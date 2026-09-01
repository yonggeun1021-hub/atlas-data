#!/usr/bin/env python3
"""P8 Atlas Daily Briefing Integration v1 regression.

Builds and publishes only against real, already-committed evidence and
already-existing production builders; introduces no synthetic sensor data.
Focuses on: honest component status classification, failure isolation,
determinism, tamper/mismatch fail-closed behaviour, atomic append-only
publication, and the false authority boundary.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "briefing" / "daily_orchestrator.py"
WORKFLOW = ROOT / ".github" / "workflows" / "daily-briefing.yml"
SPEC = importlib.util.spec_from_file_location("daily_orchestrator", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)

# A recent date this repo has real committed evidence for across every
# LIVE_READY sensor exercised by the orchestrator, EXCEPT US_BREADTH_
# MEMBERSHIP -- see _us_breadth_ready_decision_date_and_generated_at()
# below for why that one is deliberately resolved separately rather than
# folded into these two fixed literals.
DECISION_DATE = "2026-08-21"
MORNING_GENERATED_AT = "2026-08-21T12:00:00Z"
EVENING_GENERATED_AT = "2026-08-21T09:30:00Z"  # 18:30 KST


def _us_breadth_ready_decision_date_and_generated_at():
    """(decision_date, generated_at) that resolve the US_BREADTH archive's
    real latest snapshot to READY right now, computed from disk rather
    than hardcoded.

    Every snapshot in this archive (verified across 2026-08-20 and
    2026-08-21, not a one-off) was downloaded on the calendar day AFTER
    its own snapshot date -- P1-US-04's capture runs ~01:20 UTC and
    commits ~03:00-04:00 UTC the following morning, a steady-state
    property of that source, not a delay. That means no generated_at
    dated the same calendar day as DECISION_DATE can ever be safely after
    US_BREADTH_MEMBERSHIP's own real downloaded_at, so this sensor cannot
    be folded into the fixed DECISION_DATE/MORNING_GENERATED_AT pair used
    for the other LIVE_READY sensors above (STEP0_READ_MODEL_HEALTH /
    KRX_PREOPEN_COMPACT read a same-day rolling pointer that requires an
    exact decision_date match, which is what pins DECISION_DATE to
    2026-08-21 in the first place). Resolved independently here: read the
    archive's actual latest snapshot date and its actual downloaded_at
    from disk, and build a generated_at on the day after that snapshot's
    own date (still >= its decision_date, since as_of_date must not be
    after decision_date) -- so this keeps resolving to READY as later
    snapshots are added, with no future re-fix needed for this sensor
    specifically.
    """
    raw_root = MODULE.US_BREADTH.RAW_ROOT
    latest_snapshot_date = max(
        path.name
        for path in raw_root.iterdir()
        if path.is_dir() and len(path.name) == len("2026-08-20")
    )
    downloaded_at = (
        raw_root / latest_snapshot_date / "_downloaded_at.txt"
    ).read_text(encoding="utf-8").strip()
    # End of the download day itself -- always after downloaded_at's own
    # time-of-day, and still >= latest_snapshot_date (its as_of_date), so
    # the AS_OF_DATE_AFTER_DECISION_DATE boundary never trips either.
    generated_at = f"{downloaded_at[:10]}T23:59:59Z"
    return latest_snapshot_date, generated_at


def _us_breadth_and_btc_ready_decision_date_and_generated_at():
    """Latest immutable date that is present in both source archives.

    The latest US-breadth date is not guaranteed to have a same-date BTC
    capture (2026-08-27 is a real example).  A component-isolation test must
    not relabel that honest absence as a transform failure.  Select the
    latest intersection instead, then place packet generation after both
    sources' own retained download timestamps so the injected BTC transform
    exception is the only reason BTC_TREND becomes DEGRADED.
    """
    breadth_root = MODULE.US_BREADTH.RAW_ROOT
    btc_root = MODULE.ROOT / "evidence" / "crypto" / "btc" / "raw"
    breadth_dates = {
        path.name
        for path in breadth_root.iterdir()
        if path.is_dir() and (path / "_downloaded_at.txt").is_file()
    }
    btc_dates = {
        path.name
        for path in btc_root.iterdir()
        if path.is_dir() and (path / "_downloaded_at.txt").is_file()
    }
    common_dates = sorted(breadth_dates & btc_dates)
    if not common_dates:
        raise AssertionError("no common immutable US-breadth/BTC capture date")
    decision_date = common_dates[-1]
    downloaded_dates = [
        (root / decision_date / "_downloaded_at.txt")
        .read_text(encoding="utf-8").strip()[:10]
        for root in (breadth_root, btc_root)
    ]
    generated_at = f"{max(downloaded_dates)}T23:59:59Z"
    return decision_date, generated_at


def _step0_ready_decision_date_and_generated_at():
    """(decision_date, generated_at) that resolve STEP0_READ_MODEL_HEALTH's
    (and KRX_PREOPEN_COMPACT's, and -- since they read the exact same
    mutable pointer family -- DART_FILING_CONTENT's/SEC_FILING_CONTENT's)
    real current rolling-pointer state to READY right now, computed from
    disk rather than hardcoded.

    Independent from _us_breadth_ready_decision_date_and_generated_at()
    above -- shares no literal, module-level state, or logic with it -- and
    for a completely different reason. US_BREADTH_MEMBERSHIP reads a
    genuinely immutable, append-only, per-date evidence archive; its
    problem is a steady-state one-day capture-to-commit delay. STEP0_READ_
    MODEL_HEALTH/KRX_PREOPEN_COMPACT instead read data/latest_krx.json,
    data/latest_dart.json, and data/latest_sec.json -- a single MUTABLE
    "latest" pointer that the real scheduled collector overwrites every
    day, with no per-date archive behind it at all. There is no historical
    literal decision_date/generated_at pair that could stay resolvable for
    this sensor the way DECISION_DATE does for the archived sensors: once
    the real collector advances the pointer past whatever date is hard-
    coded here, _enforce_temporal_boundary's SOURCE_GENERATED_AT_AFTER_
    PACKET_GENERATED_AT check trips (the pointer's real collected_at_utc is
    now after the frozen packet's own generated_at) and collapses this
    sensor to a generic DATA_BLOCKED no matter how healthy the real data
    actually is. So, like the US_BREADTH helper, this one re-derives its
    answer from live disk state on every call -- but from the *pointer's
    own* real collected_for_kst_date/collected_at_utc, not from an
    append-only archive's latest directory.

    decision_date is read directly off data/latest_krx.json's own real
    collected_for_kst_date -- it must be an EXACT match, since
    BRIEFING_READINESS.evaluate(expected_date, ...) fails closed on any
    expected_kst_date mismatch across data/briefing_status.json, data/
    briefing/step0_status.json, and all three latest_{krx,dart,sec}.json
    files.

    generated_at is derived from the same real, qualified (parseable,
    timezone-aware, exactly-UTC) krx/dart/sec collected_at_utc triple that
    _classify_step0()/build_krx_preopen_compact() themselves qualify, via
    MODULE._read_source_collected_at_utc() and MODULE._qualify_collected_
    at_utc() -- both pure, already-correct functions, reused here rather
    than reimplemented. Rather than mirroring US_BREADTH_MEMBERSHIP's "end
    of the UTC calendar day the raw timestamp falls on" bound (which would
    not reliably land on decision_date's own KST day here -- krx/dart/sec
    are routinely collected in the evening UTC hours of the day BEFORE
    decision_date's own KST calendar date, per collected_for_kst_date),
    this instead takes the end of decision_date's own KST calendar day,
    converted to UTC: always safely after the real qualified
    collected_at_utc (which, by construction of collected_for_kst_date,
    already falls within decision_date's KST day), and never later than
    decision_date's own last KST instant, so _enforce_temporal_boundary's
    SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT check never trips and
    BRIEFING_READINESS.evaluate's expected_kst_date match keeps resolving
    READY no matter how many more days pass before this suite runs again.
    """
    raw_collected_at_utc = {
        name: MODULE._read_source_collected_at_utc(
            MODULE.BRIEFING_READINESS.DATA, name
        )
        for name in ("krx", "dart", "sec")
    }
    qualification = MODULE._qualify_collected_at_utc(raw_collected_at_utc)
    if not qualification["ok"]:
        raise AssertionError(
            "real data/latest_{krx,dart,sec}.json pointer does not "
            f"currently qualify: {qualification['reason']}"
        )
    krx_payload = json.loads(
        (MODULE.BRIEFING_READINESS.DATA / "latest_krx.json").read_text(
            encoding="utf-8"
        )
    )
    decision_date = krx_payload["collected_for_kst_date"]
    end_of_decision_date_kst = MODULE.dt.datetime.fromisoformat(
        f"{decision_date}T23:59:59"
    ).replace(tzinfo=MODULE.KST)
    generated_at = (
        end_of_decision_date_kst.astimezone(MODULE.UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return decision_date, generated_at


def _walk_authorized_keys(value, path=""):
    """Yield (path, value) for every key ending in _authorized anywhere."""
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if key.endswith("_authorized"):
                yield child, item
            yield from _walk_authorized_keys(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_authorized_keys(item, f"{path}[{index}]")


class DailyOrchestratorTest(unittest.TestCase):
    def test_filing_content_rows_bind_the_content_status_file_bytes(self):
        for component_id, relative_path in (
            ("DART_FILING_CONTENT", "data/latest_dart_content.json"),
            ("SEC_FILING_CONTENT", "data/latest_sec_content.json"),
        ):
            with self.subTest(component_id=component_id):
                path = ROOT / relative_path
                snapshot = MODULE._fetch_filing_snapshot(relative_path)
                expected = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(snapshot["kind"], "payload")
                self.assertEqual(snapshot["content_sha256"], expected)
                self.assertNotEqual(
                    expected,
                    snapshot["value"].get("source_sha256"),
                    "upstream metadata hash must not be relabelled as the content file hash",
                )
                row = MODULE._classify_filing_content(
                    component_id,
                    relative_path,
                    snapshot["value"]["collected_for_kst_date"],
                    snapshot,
                )
                self.assertEqual(row["source_packet_path"], relative_path)
                self.assertEqual(row["source_packet_sha256"], expected)

    def test_historical_filing_snapshot_without_content_digest_still_replays(self):
        snapshot = MODULE._fetch_filing_snapshot("data/latest_dart_content.json")
        legacy_snapshot = copy.deepcopy(snapshot)
        del legacy_snapshot["content_sha256"]
        row = MODULE._classify_filing_content(
            "DART_FILING_CONTENT",
            "data/latest_dart_content.json",
            legacy_snapshot["value"]["collected_for_kst_date"],
            legacy_snapshot,
        )
        self.assertEqual(
            row["source_packet_sha256"],
            legacy_snapshot["value"].get("source_sha256"),
        )

    def test_morning_build_against_real_evidence_has_no_degraded_components(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        counts = packet["component_status_counts"]
        # DYNAMIC_CLOCK is the one component allowed to be DEGRADED here --
        # same reason US_BREADTH_MEMBERSHIP is checked separately below,
        # just via a different mechanism: P8-12's own real evidence has
        # since advanced past the frozen DECISION_DATE literal above, and
        # CIO integration review round 1 (defect 1) requires DYNAMIC_CLOCK
        # to fail closed (DECISION_DATE_PRECEDES_EVIDENCE_AS_OF) rather than
        # silently use that newer evidence -- so DEGRADED here is the
        # CORRECT, intended behavior for a stale test literal, not a
        # regression. Its real READY happy path is verified separately in
        # DynamicClockRenderCapTest below, against a dynamically-computed
        # decision_date that is never behind real evidence.
        if counts["DEGRADED"] == 1:
            degraded_ids = [row["component_id"] for row in packet["components"] if row["status"] == "DEGRADED"]
            self.assertEqual(degraded_ids, ["DYNAMIC_CLOCK"], degraded_ids)
        else:
            self.assertEqual(counts["DEGRADED"], 0)
        # KRX_PREOPEN_COMPACT is the one component allowed to be UNKNOWN
        # here. It is derived purely from STEP0_READ_MODEL_HEALTH's own
        # packet (build_krx_preopen_compact short-circuits to UNKNOWN/
        # STEP0_READ_MODEL_HEALTH_UNAVAILABLE when that packet is None), and
        # STEP0_READ_MODEL_HEALTH/KRX_PREOPEN_COMPACT are both checked
        # against a separate, dynamically-resolved decision_date/
        # generated_at pair below (see
        # _step0_ready_decision_date_and_generated_at), not against
        # DECISION_DATE/MORNING_GENERATED_AT -- so this legitimately
        # surfaces here as UNKNOWN, not a fabricated/silent status, exactly
        # like DYNAMIC_CLOCK's DEGRADED above for the same underlying
        # reason (the frozen DECISION_DATE literal is now behind the real
        # rolling pointer).
        if counts["UNKNOWN"] == 1:
            unknown_ids = [row["component_id"] for row in packet["components"] if row["status"] == "UNKNOWN"]
            self.assertEqual(unknown_ids, ["KRX_PREOPEN_COMPACT"], unknown_ids)
        else:
            self.assertEqual(counts["UNKNOWN"], 0)
        self.assertGreater(counts["READY"], 0)
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(
            by_id["KRX_POST_CLOSE"]["status"], "PENDING"
        )
        self.assertEqual(
            by_id["KRX_POST_CLOSE"]["reason"],
            "MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY",
        )
        # A handful of components this repo genuinely has live evidence for.
        # STEP0_READ_MODEL_HEALTH/KRX_PREOPEN_COMPACT and
        # US_BREADTH_MEMBERSHIP are checked separately below, not here -- see
        # _step0_ready_decision_date_and_generated_at() and
        # _us_breadth_ready_decision_date_and_generated_at() for why neither
        # can share DECISION_DATE/MORNING_GENERATED_AT with these (two
        # independent reasons, one per helper).
        for component_id in (
            "BTC_TREND", "BTC_RISK", "STABLECOIN_NET_ISSUANCE",
        ):
            self.assertEqual(
                by_id[component_id]["status"], "READY", component_id
            )
        # STEP0_READ_MODEL_HEALTH/KRX_PREOPEN_COMPACT read a MUTABLE single
        # "latest" rolling pointer (data/latest_{krx,dart,sec}.json) that
        # the real scheduled collector overwrites every day, with no
        # per-date archive behind it -- unlike BTC_TREND/BTC_RISK/
        # STABLECOIN_NET_ISSUANCE above, there is no historical
        # decision_date this repo could pin a literal to and expect it to
        # remain resolvable forever (see
        # _step0_ready_decision_date_and_generated_at() for the full
        # reasoning, and for why this is independent of the
        # US_BREADTH_MEMBERSHIP helper below).
        step0_date, step0_generated_at = (
            _step0_ready_decision_date_and_generated_at()
        )
        step0_packet = MODULE.build_packet(
            "morning", step0_date, step0_generated_at
        )
        step0_by_id = {
            row["component_id"]: row for row in step0_packet["components"]
        }
        self.assertEqual(step0_by_id["STEP0_READ_MODEL_HEALTH"]["status"], "READY")
        self.assertTrue(step0_by_id["STEP0_READ_MODEL_HEALTH"]["validated"])
        self.assertEqual(step0_by_id["KRX_PREOPEN_COMPACT"]["status"], "READY")
        self.assertTrue(step0_by_id["KRX_PREOPEN_COMPACT"]["validated"])
        # US_BREADTH_MEMBERSHIP's real evidence genuinely has no
        # generated_at on DECISION_DATE's own calendar day that could ever
        # be after it (see helper docstring), so its READY happy path is
        # exercised here against the archive's real latest snapshot and
        # its own real generated_at instead -- still a genuine full-packet
        # build through the same temporal boundary, just not sharing
        # DECISION_DATE/MORNING_GENERATED_AT with the sensors above.
        us_breadth_date, us_breadth_generated_at = (
            _us_breadth_ready_decision_date_and_generated_at()
        )
        us_breadth_packet = MODULE.build_packet(
            "morning", us_breadth_date, us_breadth_generated_at
        )
        us_breadth_by_id = {
            row["component_id"]: row for row in us_breadth_packet["components"]
        }
        us_breadth_row = us_breadth_by_id["US_BREADTH_MEMBERSHIP"]
        self.assertEqual(us_breadth_row["status"], "READY")
        self.assertEqual(us_breadth_row["as_of_date"], us_breadth_date)
        self.assertIsNotNone(us_breadth_row["packet"])
        self.assertGreater(us_breadth_row["packet"]["member_count"], 0)
        # Genuinely unratified/unpopulated components must say so honestly,
        # never silently disappear or read as READY.
        self.assertEqual(by_id["PORTFOLIO_BUCKET"]["status"], "POLICY_BLOCKED")
        self.assertEqual(by_id["RULE_EVALUATION"]["status"], "POLICY_BLOCKED")
        self.assertIn(
            by_id["CRYPTO_BREADTH"]["status"], ("POLICY_BLOCKED", "READY")
        )

    def test_defensive_and_strategic_readiness_are_wired_fail_closed(self):
        packet = MODULE.build_packet(
            "morning", DECISION_DATE, MORNING_GENERATED_AT
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        defensive = by_id["DEFENSIVE_ACTION_DECISION"]
        strategic = by_id["STRATEGIC_CAPITAL_POSTURE"]
        summary = by_id["ACTION_RISK_PORTFOLIO_SUMMARY"]

        self.assertEqual(defensive["status"], "PENDING")
        self.assertEqual(
            defensive["packet"]["status"],
            "DEFENSIVE_ACTION_READINESS_BLOCKED",
        )
        self.assertTrue(
            all(row["eligible"] is None for row in defensive["packet"]["decisions"])
        )
        self.assertIsNone(defensive["packet"]["selected_action"])
        self.assertEqual(defensive["packet"]["order_intents"], [])

        self.assertEqual(strategic["status"], "PENDING")
        self.assertEqual(
            strategic["packet"]["status"],
            "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED",
        )
        self.assertTrue(
            all(value is None for value in strategic["packet"]["market_budget"].values())
        )
        self.assertIsNone(strategic["packet"]["allocation_proposal"])
        self.assertEqual(strategic["packet"]["order_intents"], [])

        embedded = summary["packet"]["source_packets"]
        self.assertEqual(
            embedded["DEFENSIVE_ACTION_DECISION"]["packet_sha256"],
            defensive["packet"]["packet_sha256"],
        )
        self.assertEqual(
            embedded["STRATEGIC_CAPITAL_POSTURE"]["packet_sha256"],
            strategic["packet"]["packet_sha256"],
        )
        self.assertTrue(
            all(row["action"] is None for row in summary["packet"]["actions"])
        )

    def test_replaying_a_past_decision_date_never_reads_newer_evidence(self):
        # This repo has real committed evidence for both 2026-08-20 and
        # 2026-08-21 for BTC/stablecoin/crypto-breadth. Building a briefing
        # for the *older* date must resolve to exactly that date's
        # directory -- never silently substitute the newer one that
        # happens to also be committed by now.
        past_date = "2026-08-20"
        packet = MODULE.build_packet("morning", past_date, "2026-08-20T12:00:00Z")
        by_id = {row["component_id"]: row for row in packet["components"]}
        for component_id, expected_path in (
            ("BTC_TREND", "evidence/crypto/btc/raw/2026-08-20"),
            ("BTC_RISK", "evidence/crypto/btc/raw/2026-08-20"),
            ("STABLECOIN_NET_ISSUANCE", "evidence/stablecoin/raw/2026-08-20"),
        ):
            row = by_id[component_id]
            self.assertEqual(row["as_of_date"], past_date, component_id)
            self.assertEqual(row["source_packet_path"], expected_path, component_id)
            self.assertNotIn("2026-08-21", row["source_packet_path"], component_id)

        # KOFIA_FIRST_SEEN and US_BREADTH_MEMBERSHIP are both fed by live
        # daily-scheduled captures (p1-kr03-kofia-first-seen.yml,
        # p1-us04-forward-breadth.yml) that keep appending real evidence for
        # "today" to this very repo while this suite runs. A decision_date
        # equal to the literal calendar date this test happens to run on is
        # therefore NOT a safe stand-in for "a date with no capture" -- the
        # live cron may capture it later the same day, on either side of a
        # given test run, which is exactly the flakiness that broke this
        # assertion once already. Use a date far enough in the future that
        # no scheduled capture (which only ever writes *today's* date) can
        # ever produce it, so the "no exact capture" / "never read newer
        # evidence" property holds no matter when this test executes.
        never_captured_date = "2099-01-01"
        # generated_at must be at least as far in the future as
        # never_captured_date itself, not a fixed real-ish literal --
        # otherwise the same live-cron accretion this test guards against
        # can eventually push US_BREADTH_MEMBERSHIP's real latest
        # snapshot's downloaded_at past a hardcoded generated_at (exactly
        # what broke this assertion the second time: the archive's real
        # latest snapshot is READ dynamically below, but was still being
        # compared against a fixed "2026-08-21T12:00:00Z" generated_at).
        future_relative_to_capture = MODULE.build_packet(
            "morning", never_captured_date, f"{never_captured_date}T12:00:00Z"
        )
        components_by_id = {
            row["component_id"]: row
            for row in future_relative_to_capture["components"]
        }

        # KOFIA has no capture for a date this far out. Asking for a
        # decision_date with no exact capture must be DATA_BLOCKED, never
        # silently fall back to the nearest available capture.
        kofia_row = components_by_id["KOFIA_FIRST_SEEN"]
        self.assertEqual(kofia_row["status"], "DATA_BLOCKED")
        self.assertEqual(kofia_row["reason"], "NO_CAPTURE_FOR_DECISION_DATE")

        # US Breadth membership is a genuine as-of (forward-fill) series by
        # contract, so requesting a date after every real capture must
        # resolve to the latest capture ON OR BEFORE that date -- never a
        # later one, and (since no capture can ever exist for
        # never_captured_date) never that date itself either. Compare
        # against whatever the archive's real latest snapshot is at the
        # moment this test runs, rather than a hardcoded literal that would
        # go stale as soon as tomorrow's capture lands.
        real_latest_snapshot = max(
            path.name
            for path in MODULE.US_BREADTH.RAW_ROOT.iterdir()
            if path.is_dir() and len(path.name) == len("2026-08-20")
        )
        breadth_row = components_by_id["US_BREADTH_MEMBERSHIP"]
        self.assertEqual(breadth_row["status"], "READY")
        self.assertEqual(breadth_row["as_of_date"], real_latest_snapshot)
        self.assertNotEqual(breadth_row["as_of_date"], never_captured_date)

    def test_evening_slot_includes_observed_unconfirmed_krx_post_close(self):
        packet = MODULE.build_packet("evening", DECISION_DATE, EVENING_GENERATED_AT)
        by_id = {row["component_id"]: row for row in packet["components"]}
        row = by_id["KRX_POST_CLOSE"]
        self.assertEqual(row["status"], "READY")
        self.assertIsNotNone(row["packet"])
        self.assertEqual(row["packet"]["status"], "READY_OBSERVED_UNCONFIRMED")
        # The orchestrator itself never promotes an unconfirmed evening
        # observation to a decision input, regardless of the embedded
        # packet's own fields.
        self.assertFalse(row["decision_eligible"])
        self.assertFalse(row["action_eligible"])
        self.assertFalse(row["order_eligible"])

    def test_morning_never_builds_krx_post_close_even_if_bundle_exists(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertIsNone(by_id["KRX_POST_CLOSE"]["packet"])

    def test_rebuild_is_byte_identical_deterministic(self):
        first = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        second = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

    def test_validate_packet_round_trips_and_rejects_tamper(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

        sha_tamper = copy.deepcopy(packet)
        sha_tamper["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "OUTPUT_SHA_MISMATCH"
        ):
            MODULE.validate_packet(sha_tamper)

        # Self-rehashed semantic tamper: change a real value and recompute
        # the digest over the tampered payload. The rebuild-and-compare
        # step must still catch it because the rebuilt packet won't match.
        semantic_tamper = copy.deepcopy(packet)
        for row in semantic_tamper["components"]:
            if row["component_id"] == "BTC_TREND":
                row["packet"]["direction"] = "BELOW_200DMA"
        unsigned = copy.deepcopy(semantic_tamper)
        del unsigned["packet_sha256"]
        semantic_tamper["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"
        ):
            MODULE.validate_packet(semantic_tamper)

        # Source path/sha substitution: point a component at a path that
        # does not match what a real rebuild would produce.
        path_tamper = copy.deepcopy(packet)
        for row in path_tamper["components"]:
            if row["component_id"] == "US_BREADTH_MEMBERSHIP":
                row["source_packet_path"] = "evidence/us_breadth/raw/1999-01-01"
        unsigned = copy.deepcopy(path_tamper)
        del unsigned["packet_sha256"]
        path_tamper["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"
        ):
            MODULE.validate_packet(path_tamper)

    def test_validate_packet_of_a_past_decision_date_is_independent_of_newer_evidence(
        self,
    ):
        # This repo already has evidence for both 2026-08-20 and 2026-08-21
        # (i.e. "newer evidence has since been added" is already true right
        # now). A packet built for the older date must still validate
        # cleanly -- every revalidatable component re-resolves to the same
        # frozen 2026-08-20 evidence, not whatever is newest today.
        past_date = "2026-08-20"
        packet = MODULE.build_packet("morning", past_date, "2026-08-20T12:00:00Z")
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

    def test_frozen_source_components_are_genuinely_independently_revalidatable(self):
        # STEP0_READ_MODEL_HEALTH / DART_FILING_CONTENT / SEC_FILING_CONTENT
        # (and, transitively through it, KRX_PREOPEN_COMPACT) read a mutable
        # rolling pointer with no per-date archive. KOFIA_FIRST_SEEN /
        # US_BREADTH_MEMBERSHIP / BTC_TREND / BTC_RISK /
        # STABLECOIN_NET_ISSUANCE / CRYPTO_BREADTH read a genuinely
        # immutable, append-only, per-date archive whose *presence* (not
        # content) can still change between build time and a later
        # revalidation, if the same-dated capture lands afterward. All nine
        # are frozen the same way: packet["frozen_sources"] carries the
        # exact input snapshot each was built from, and validate_packet()
        # re-derives them purely from that -- genuinely independent, no
        # live data/ access, no "cannot be revalidated" boundary any more.
        self.assertEqual(
            MODULE.FROZEN_SOURCE_COMPONENTS,
            frozenset({
                "STEP0_READ_MODEL_HEALTH", "DART_FILING_CONTENT", "SEC_FILING_CONTENT",
                "KOFIA_FIRST_SEEN", "US_BREADTH_MEMBERSHIP", "BTC_TREND", "BTC_RISK",
                "STABLECOIN_NET_ISSUANCE", "CRYPTO_BREADTH", "CRYPTO_LEADERSHIP",
                "KRX_POST_CLOSE", "FREE_MARKET_DATA", "KOREA_ROTATION",
                "KOREA_MARKET_SIGNALS",
            }),
        )
        # Built late in the KST day, on the evening slot (so KRX_POST_CLOSE
        # is actually fetched too) -- not MORNING_GENERATED_AT or a bare
        # end-of-UTC-day value, so that no real sensor's own genuine
        # capture/observation timestamp (KOFIA's real captured_at_utc,
        # KRX_POST_CLOSE's real observed_at_kst, both of which can
        # legitimately land well into the KST evening) trips the
        # _enforce_temporal_boundary source-generated_at check below and
        # confuses this test's focus, which is frozen-source
        # re-derivability, not the temporal boundary itself (covered
        # separately). 14:59:00Z = 23:59:00 KST on DECISION_DATE itself --
        # after the 18:00 KST evening floor, still the same KST calendar
        # date, and after every real timestamp this suite's evidence has.
        late_generated_at = f"{DECISION_DATE}T14:59:00Z"
        boundaries = MODULE.build_packet(
            "evening", DECISION_DATE, late_generated_at
        )["unresolved_boundaries"]
        for stale_boundary in (
            "ROLLING_POINTER_COMPONENTS_MAY_FAIL_LATER_REVALIDATION_NOT_TAMPER",
            "SAME_DAY_ROLLING_POINTER_COMPONENTS_NOT_INDEPENDENTLY_REVALIDATED",
        ):
            self.assertNotIn(stale_boundary, boundaries)

        packet = MODULE.build_packet("evening", DECISION_DATE, late_generated_at)
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)
        self.assertIn("frozen_sources", packet)
        self.assertEqual(set(packet["frozen_sources"]), MODULE.FROZEN_SOURCE_COMPONENTS)
        # STEP0_READ_MODEL_HEALTH / KRX_PREOPEN_COMPACT / DART_FILING_
        # CONTENT / SEC_FILING_CONTENT all read the SAME family of mutable
        # single "latest" rolling pointers (data/latest_krx.json, data/
        # latest_dart.json, data/latest_sec.json, data/latest_dart_content.
        # json, data/latest_sec_content.json), whose real current dates have
        # since advanced past DECISION_DATE (2026-08-21) -- real time has
        # simply moved on since that literal was pinned, exactly the same
        # underlying condition as FREE_MARKET_DATA/US_BREADTH_MEMBERSHIP
        # below, just tripping different specific temporal-boundary checks
        # (SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT for STEP0/KRX_
        # PREOPEN_COMPACT, AS_OF_DATE_AFTER_DECISION_DATE for DART/SEC
        # content, since their own real collected_for_kst_date is now after
        # DECISION_DATE). Their genuine independent re-derivability is
        # proven below against a separate, dynamically-resolved decision_
        # date/generated_at pair instead (see
        # _step0_ready_decision_date_and_generated_at) -- not by relaxing
        # this main DECISION_DATE-pinned assertion loop.
        step0_date, step0_generated_at = (
            _step0_ready_decision_date_and_generated_at()
        )
        step0_packet = MODULE.build_packet(
            "evening", step0_date, step0_generated_at
        )
        step0_by_id = {
            row["component_id"]: row for row in step0_packet["components"]
        }
        # All of them now honestly claim genuine re-derivability, like
        # every other component (KRX_PREOPEN_COMPACT rides on STEP0's own
        # freeze).
        by_id = {row["component_id"]: row for row in packet["components"]}
        for component_id in MODULE.FROZEN_SOURCE_COMPONENTS | {"KRX_PREOPEN_COMPACT"}:
            if component_id in (
                "STEP0_READ_MODEL_HEALTH", "KRX_PREOPEN_COMPACT",
                "DART_FILING_CONTENT", "SEC_FILING_CONTENT",
            ):
                self.assertTrue(step0_by_id[component_id]["validated"], component_id)
                continue
            if component_id == "FREE_MARKET_DATA":
                # The committed live capture was collected on the following
                # UTC day, so this historical packet correctly blocks it at
                # the common temporal boundary.
                self.assertEqual(by_id[component_id]["status"], "DATA_BLOCKED")
                continue
            if component_id == "US_BREADTH_MEMBERSHIP":
                # Same root cause as FREE_MARKET_DATA above, not a
                # frozen-source defect: its real downloaded_at lands the
                # calendar day after DECISION_DATE (see
                # _us_breadth_ready_decision_date_and_generated_at), so
                # the common temporal boundary correctly blocks it here
                # too. The tamper-detection loop below still exercises its
                # frozen_sources re-derivability regardless of status.
                self.assertEqual(by_id[component_id]["status"], "DATA_BLOCKED")
                continue
            if component_id == "KOREA_MARKET_SIGNALS":
                # Same root cause as FREE_MARKET_DATA/US_BREADTH_MEMBERSHIP
                # above: _classify_korea_market_signals reads the live
                # rolling pointer data/latest_korea_market_signals.json
                # (not point-in-time frozen evidence), and that pointer's
                # as_of_date has since advanced past DECISION_DATE
                # (2026-08-21). The row's own internal FROM_FUTURE check
                # would have set validated=True, but the generic, common
                # _enforce_temporal_boundary wrapper (applied to every row
                # regardless of builder) independently re-checks
                # as_of_date > decision_date and unconditionally rebuilds
                # the row as DATA_BLOCKED/AS_OF_DATE_AFTER_DECISION_DATE
                # with validated defaulting to False -- this outer,
                # defense-in-depth downgrade is what actually reaches this
                # packet, per _enforce_temporal_boundary's own docstring.
                self.assertEqual(by_id[component_id]["status"], "DATA_BLOCKED")
                self.assertFalse(by_id[component_id]["validated"])
                continue
            if component_id == "KOREA_ROTATION":
                # Different root cause from the DATA_BLOCKED cases above:
                # P2-03's rotation_policy was ratified 2026-08-22 and its
                # real end-to-end proof required a genuinely POST-
                # ratification observation pair (anti-lookahead, see
                # korea_capital_rotation_ledger_proof.py) -- the only real
                # pair available for that is dated 2026-08-18/2026-08-20,
                # so the committed pointer's as_of_date is now 2026-08-20,
                # not DECISION_DATE (2026-08-21). _classify_korea_rotation
                # correctly returns PENDING (no observation for this exact
                # decision date), not DATA_BLOCKED (an observation exists
                # but is temporally rejected) -- still validated=True and
                # never fabricated.
                self.assertEqual(by_id[component_id]["status"], "PENDING")
                self.assertTrue(by_id[component_id]["validated"])
                continue
            self.assertTrue(by_id[component_id]["validated"], component_id)

        # Tampering the row itself (leaving frozen_sources untouched) is
        # still caught -- the rebuild re-derives from frozen_sources, which
        # the tamper never touched, so it disagrees with the tampered row.
        for component_id in MODULE.FROZEN_SOURCE_COMPONENTS | {"KRX_PREOPEN_COMPACT"}:
            tampered = copy.deepcopy(packet)
            for row in tampered["components"]:
                if row["component_id"] == component_id:
                    row["reason"] = "TAMPERED_REASON_NEVER_REALLY_HAPPENED"
            unsigned = copy.deepcopy(tampered)
            del unsigned["packet_sha256"]
            tampered["packet_sha256"] = MODULE.payload_sha256(unsigned)
            with self.assertRaisesRegex(
                MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH", msg=component_id
            ):
                MODULE.validate_packet(copy.deepcopy(tampered))

    def test_evidence_that_arrives_after_build_time_never_flips_an_old_data_blocked_revision(
        self,
    ):
        # The literal scenario from review: rev-001 published while
        # BTC_TREND's evidence directory for decision_date does not exist
        # yet (DATA_BLOCKED), the directory is created afterward (same-day
        # capture landing), rev-002 published (now READY) -- and BOTH
        # revisions must still validate with NO fault injection active,
        # because presence/absence was frozen at each revision's own build
        # time, not re-derived from current disk state.
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original_fetch = MODULE._fetch_dated_evidence_snapshot

            def _btc_absent(root, decision_date):
                if root == ROOT / "evidence" / "crypto" / "btc" / "raw":
                    return {"kind": "absent"}
                return original_fetch(root, decision_date)

            MODULE._fetch_dated_evidence_snapshot = _btc_absent
            try:
                first = MODULE.publish(
                    "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
                )
            finally:
                MODULE._fetch_dated_evidence_snapshot = original_fetch
            first_persisted = json.loads((first["path"] / "packet.json").read_text())
            first_by_id = {
                row["component_id"]: row for row in first_persisted["components"]
            }
            self.assertEqual(first_by_id["BTC_TREND"]["status"], "DATA_BLOCKED")
            self.assertEqual(
                first_persisted["frozen_sources"]["BTC_TREND"]["kind"], "absent"
            )

            # The real evidence directory for DECISION_DATE genuinely
            # exists in this repo (used throughout this suite) -- so a
            # plain, un-monkeypatched republish now sees it "arrive".
            second = MODULE.publish(
                "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
            )
            second_persisted = json.loads((second["path"] / "packet.json").read_text())
            second_by_id = {
                row["component_id"]: row for row in second_persisted["components"]
            }
            self.assertTrue(second["created"])
            self.assertEqual(second_by_id["BTC_TREND"]["status"], "READY")
            self.assertEqual(
                second_persisted["frozen_sources"]["BTC_TREND"]["kind"], "present"
            )

            # No fault injection active here -- rev-001 must still say
            # DATA_BLOCKED on independent re-validation, never flipped to
            # READY just because the directory exists on disk right now.
            revalidated_first = MODULE.validate_packet(copy.deepcopy(first_persisted))
            self.assertEqual(revalidated_first, first_persisted)
            revalidated_first_by_id = {
                row["component_id"]: row for row in revalidated_first["components"]
            }
            self.assertEqual(revalidated_first_by_id["BTC_TREND"]["status"], "DATA_BLOCKED")
            self.assertEqual(
                MODULE.validate_packet(copy.deepcopy(second_persisted)), second_persisted
            )

    def test_source_retrieval_time_after_generated_at_is_not_promoted_to_ready(self):
        # Real, un-monkeypatched repro from review: this repo's real BTC/
        # stablecoin captures for DECISION_DATE were genuinely fetched
        # (per their own _downloaded_at.txt) several hours after
        # midnight UTC. A packet claiming to have been generated at
        # midnight UTC that same day must not read evidence retrieved
        # after that moment as READY -- it did not exist yet when the
        # packet claims to have been assembled.
        packet = MODULE.build_packet(
            "morning", DECISION_DATE, f"{DECISION_DATE}T00:00:00Z"
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        for component_id in ("BTC_TREND", "BTC_RISK", "STABLECOIN_NET_ISSUANCE"):
            self.assertEqual(by_id[component_id]["status"], "DATA_BLOCKED", component_id)
            self.assertEqual(
                by_id[component_id]["reason"],
                "SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT",
                component_id,
            )
            self.assertFalse(by_id[component_id]["validated"], component_id)

    def test_krx_post_close_bundle_that_arrives_after_build_time_never_flips_an_old_unknown_revision(
        self,
    ):
        # The same literal scenario as BTC_TREND above, for KRX_POST_CLOSE
        # specifically: rev-001 published while the bundle for
        # decision_date does not exist yet (UNKNOWN), the bundle is
        # created afterward (same-evening arrival), rev-002 published (now
        # READY) -- and BOTH revisions must still validate with NO fault
        # injection active, because presence/absence was frozen at each
        # revision's own build time.
        evening_generated_at = f"{DECISION_DATE}T14:59:00Z"  # 23:59 KST
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original_fetch = MODULE._fetch_krx_post_close_snapshot

            def _absent(decision_date):
                return {"kind": "absent"}

            MODULE._fetch_krx_post_close_snapshot = _absent
            try:
                first = MODULE.publish(
                    "evening", DECISION_DATE, evening_generated_at, evidence_root
                )
            finally:
                MODULE._fetch_krx_post_close_snapshot = original_fetch
            first_persisted = json.loads((first["path"] / "packet.json").read_text())
            first_by_id = {
                row["component_id"]: row for row in first_persisted["components"]
            }
            self.assertEqual(first_by_id["KRX_POST_CLOSE"]["status"], "UNKNOWN")
            self.assertEqual(
                first_persisted["frozen_sources"]["KRX_POST_CLOSE"]["kind"], "absent"
            )

            # The real bundle for DECISION_DATE genuinely exists in this
            # repo -- so a plain, un-monkeypatched republish now sees it.
            second = MODULE.publish(
                "evening", DECISION_DATE, f"{DECISION_DATE}T14:59:30Z", evidence_root
            )
            second_persisted = json.loads((second["path"] / "packet.json").read_text())
            second_by_id = {
                row["component_id"]: row for row in second_persisted["components"]
            }
            self.assertTrue(second["created"])
            self.assertEqual(second_by_id["KRX_POST_CLOSE"]["status"], "READY")
            self.assertEqual(
                second_persisted["frozen_sources"]["KRX_POST_CLOSE"]["kind"], "present"
            )

            # No fault injection active here -- rev-001 must still say
            # UNKNOWN on independent re-validation, never flipped to READY
            # just because the bundle exists on disk right now.
            revalidated_first = MODULE.validate_packet(copy.deepcopy(first_persisted))
            self.assertEqual(revalidated_first, first_persisted)
            revalidated_first_by_id = {
                row["component_id"]: row for row in revalidated_first["components"]
            }
            self.assertEqual(revalidated_first_by_id["KRX_POST_CLOSE"]["status"], "UNKNOWN")
            self.assertEqual(
                MODULE.validate_packet(copy.deepcopy(second_persisted)), second_persisted
            )

    def test_krx_post_close_real_observed_at_after_generated_at_is_not_promoted_to_ready(
        self,
    ):
        # Real, un-monkeypatched repro from review: this repo's real
        # KRX_POST_CLOSE bundle for DECISION_DATE was genuinely observed
        # (per its own source.json collected_at_utc / symbols/*.json
        # observed_at_kst) at 18:11 KST. A packet claiming to have been
        # generated at exactly the 18:00 KST evening floor that same day
        # must not read those observations as READY -- they did not exist
        # yet at that moment.
        packet = MODULE.build_packet(
            "evening", DECISION_DATE, f"{DECISION_DATE}T09:00:00Z"  # 18:00:00 KST
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(by_id["KRX_POST_CLOSE"]["status"], "DATA_BLOCKED")
        self.assertEqual(
            by_id["KRX_POST_CLOSE"]["reason"],
            "SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT",
        )
        self.assertFalse(by_id["KRX_POST_CLOSE"]["validated"])

    def test_step0_and_krx_preopen_real_collected_at_after_generated_at_is_not_promoted_to_ready(
        self,
    ):
        # Real, un-monkeypatched repro found by audit: data/latest_{krx,
        # dart,sec}.json each carry a real collected_at_utc, but
        # check_briefing_readiness.py's own evaluate() result never
        # surfaces it, so STEP0_READ_MODEL_HEALTH/KRX_PREOPEN_COMPACT had
        # no real retrieval-time boundary at all -- the same class of gap
        # already fixed for every other evidence-reading component. A
        # packet claiming to have been generated before krx/dart/sec were
        # actually collected must not read them as READY.
        collected_at_values = []
        for name in ("krx", "dart", "sec"):
            payload = json.loads((ROOT / "data" / f"latest_{name}.json").read_text())
            collected_at_values.append(payload["collected_at_utc"])
        earliest_real_collection = min(
            MODULE.dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            for value in collected_at_values
        )
        before_any_collection = (
            earliest_real_collection - MODULE.dt.timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        packet = MODULE.build_packet("morning", DECISION_DATE, before_any_collection)
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(by_id["STEP0_READ_MODEL_HEALTH"]["status"], "DATA_BLOCKED")
        self.assertEqual(
            by_id["STEP0_READ_MODEL_HEALTH"]["reason"],
            "SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT",
        )
        self.assertFalse(by_id["STEP0_READ_MODEL_HEALTH"]["validated"])
        # KRX_PREOPEN_COMPACT cascades: with STEP0 unavailable, it has no
        # source to derive from.
        self.assertEqual(by_id["KRX_PREOPEN_COMPACT"]["status"], "UNKNOWN")
        self.assertEqual(
            by_id["KRX_PREOPEN_COMPACT"]["reason"], "STEP0_READ_MODEL_HEALTH_UNAVAILABLE"
        )

    def test_qualify_collected_at_utc_requires_all_three_valid_utc_timestamps(self):
        # _qualify_collected_at_utc is a pure function -- exercised
        # directly here for precise coverage of every disqualifying case,
        # independent of build_packet()'s live data/ state.
        valid = {
            "krx": "2026-08-21T06:58:30+00:00",
            "dart": "2026-08-21T06:58:50+00:00",
            "sec": "2026-08-21T06:58:58+00:00",  # latest of the three
        }

        # 1/2/3: missing timestamp for each source in turn.
        for missing_name in ("krx", "dart", "sec"):
            broken = dict(valid)
            broken[missing_name] = None
            result = MODULE._qualify_collected_at_utc(broken)
            self.assertFalse(result["ok"], missing_name)
            self.assertEqual(
                result["reason"], f"{missing_name.upper()}_COLLECTED_AT_UTC_MISSING"
            )

        # 4: unparseable ISO timestamp.
        broken = dict(valid, krx="not-a-timestamp")
        result = MODULE._qualify_collected_at_utc(broken)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "KRX_COLLECTED_AT_UTC_UNPARSEABLE")

        # 5: naive timestamp (no timezone at all).
        broken = dict(valid, dart="2026-08-21T06:58:50")
        result = MODULE._qualify_collected_at_utc(broken)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "DART_COLLECTED_AT_UTC_NAIVE")

        # 6: timezone-aware but not UTC (KST +09:00).
        broken = dict(valid, sec="2026-08-21T15:58:58+09:00")
        result = MODULE._qualify_collected_at_utc(broken)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "SEC_COLLECTED_AT_UTC_NOT_UTC")

        # 7: all three genuinely valid -> qualifies, latest (sec) selected.
        result = MODULE._qualify_collected_at_utc(valid)
        self.assertTrue(result["ok"])
        self.assertEqual(result["generated_at"], "2026-08-21T06:58:58+00:00")

        # A literal "Z" suffix (as this repo's real files use) is also
        # accepted as UTC, not just an explicit +00:00 offset.
        result = MODULE._qualify_collected_at_utc({
            "krx": "2026-08-21T06:58:30Z",
            "dart": "2026-08-21T06:58:50Z",
            "sec": "2026-08-21T06:58:58Z",
        })
        self.assertTrue(result["ok"])

    def test_step0_and_krx_preopen_refuse_ready_on_missing_or_invalid_timestamp(self):
        # The exact bug from review: a snapshot with a missing/invalid
        # collected_at_utc must never let STEP0_READ_MODEL_HEALTH reach
        # READY with generated_at=None and validated=True -- and
        # KRX_PREOPEN_COMPACT, sharing the same disqualified triple, must
        # never be READY either while its sibling failed qualification.
        ready_payload = {
            "classification": "data_ready_read_model_ready",
            "data_ready": True,
            "read_model_ready": True,
            "reasons": [],
            "sources": {
                "krx": {
                    "collected_for_kst_date": DECISION_DATE,
                    "path": "data/latest_krx.json",
                    "source_sha256": "f" * 64,
                },
                "dart": {"collected_for_kst_date": DECISION_DATE},
                "sec": {"collected_for_kst_date": DECISION_DATE},
            },
        }
        for broken_raw, case in (
            ({"krx": None, "dart": None, "sec": None}, "all_missing"),
            (
                {
                    "krx": "2026-08-21T06:58:30+00:00",
                    "dart": "not-a-timestamp",
                    "sec": "2026-08-21T06:58:58+00:00",
                },
                "one_unparseable",
            ),
            (
                {
                    "krx": "2026-08-21T06:58:30+00:00",
                    "dart": "2026-08-21T06:58:50",  # naive
                    "sec": "2026-08-21T06:58:58+00:00",
                },
                "one_naive",
            ),
        ):
            snapshot = {
                "kind": "payload",
                "value": ready_payload,
                "collected_at_utc_raw": broken_raw,
            }
            step0_row = MODULE._classify_step0(DECISION_DATE, snapshot)
            self.assertNotEqual(step0_row["status"], "READY", case)
            self.assertIsNone(step0_row["generated_at"], case)
            self.assertFalse(step0_row["validated"], case)
            self.assertIn("TEMPORAL_QUALIFICATION_FAILED", step0_row["reason"], case)

            krx_preopen_row = MODULE.build_krx_preopen_compact(
                DECISION_DATE, ready_payload, broken_raw
            )
            self.assertNotEqual(krx_preopen_row["status"], "READY", case)
            self.assertIsNone(krx_preopen_row["generated_at"], case)
            self.assertFalse(krx_preopen_row["validated"], case)
            self.assertIn(
                "TEMPORAL_QUALIFICATION_FAILED", krx_preopen_row["reason"], case
            )

        # End to end through build_packet(), with a monkeypatched fetch,
        # to prove the wiring (not just the pure functions in isolation)
        # never lets either component reach READY.
        original_fetch = MODULE._fetch_step0_snapshot

        def _missing_timestamps(decision_date):
            snapshot = original_fetch(decision_date)
            if snapshot["kind"] == "payload":
                snapshot = dict(snapshot)
                snapshot["collected_at_utc_raw"] = {"krx": None, "dart": None, "sec": None}
            return snapshot

        MODULE._fetch_step0_snapshot = _missing_timestamps
        try:
            packet = MODULE.build_packet(
                "morning", DECISION_DATE, f"{DECISION_DATE}T23:59:00Z"
            )
        finally:
            MODULE._fetch_step0_snapshot = original_fetch
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertNotEqual(by_id["STEP0_READ_MODEL_HEALTH"]["status"], "READY")
        self.assertNotEqual(by_id["KRX_PREOPEN_COMPACT"]["status"], "READY")

    def test_frozen_source_tamper_leaving_the_row_untouched_is_caught_for_every_component(
        self,
    ):
        # Complements the row-tamper tests above: tampering
        # packet["frozen_sources"][X] directly -- the *input* -- while
        # leaving packet["components"] (the *output* row) untouched must
        # also be caught. validate_packet() always re-derives every
        # FROZEN_SOURCE_COMPONENTS row from frozen_sources, so an input
        # that no longer matches the persisted output necessarily produces
        # a different rebuilt row, which disagrees with the untouched
        # persisted one. This was already true by construction but had no
        # explicit regression covering it.
        late_generated_at = f"{DECISION_DATE}T14:59:00Z"  # 23:59 KST
        packet = MODULE.build_packet("evening", DECISION_DATE, late_generated_at)

        # Each component's snapshot has its own "kind" vocabulary
        # (_fetch_step0_snapshot's error/payload differs from
        # _fetch_dated_evidence_snapshot's absent/present, etc.) -- these
        # are the "no real evidence" shape each one's own _classify_*
        # function actually recognizes, so the tamper is realistic rather
        # than an arbitrary malformed shape that would raise a raw
        # KeyError instead of exercising the real comparison.
        no_evidence_shape = {
            "STEP0_READ_MODEL_HEALTH": {"kind": "error", "value": "TAMPERED"},
            "DART_FILING_CONTENT": {"kind": "missing", "value": None},
            "SEC_FILING_CONTENT": {"kind": "missing", "value": None},
            "KOFIA_FIRST_SEEN": {"kind": "absent"},
            "US_BREADTH_MEMBERSHIP": {"kind": "unresolved", "value": "TAMPERED"},
            "BTC_TREND": {"kind": "absent"},
            "BTC_RISK": {"kind": "absent"},
            "STABLECOIN_NET_ISSUANCE": {"kind": "absent"},
            "CRYPTO_BREADTH": {"kind": "absent"},
            "CRYPTO_LEADERSHIP": {"kind": "absent"},
            "KRX_POST_CLOSE": {"kind": "absent"},
            "FREE_MARKET_DATA": {"kind": "missing"},
            "KOREA_ROTATION": {"kind": "missing", "value": None},
            "KOREA_MARKET_SIGNALS": {"kind": "error", "value": "TAMPERED"},
        }
        self.assertEqual(set(no_evidence_shape), MODULE.FROZEN_SOURCE_COMPONENTS)

        for component_id in MODULE.FROZEN_SOURCE_COMPONENTS:
            tampered = copy.deepcopy(packet)
            tampered["frozen_sources"][component_id] = no_evidence_shape[component_id]
            unsigned = copy.deepcopy(tampered)
            del unsigned["packet_sha256"]
            tampered["packet_sha256"] = MODULE.payload_sha256(unsigned)
            with self.assertRaisesRegex(
                MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH", msg=component_id
            ):
                MODULE.validate_packet(copy.deepcopy(tampered))

    def test_step0_revisions_validate_across_a_rolling_pointer_change_without_fault_injection(
        self,
    ):
        # rev-001 published, the live rolling pointer changes (simulated),
        # rev-002 published -- then BOTH revisions must independently
        # validate with NO fault injection or monkeypatch active at
        # validation time: validate_packet() must never touch the live,
        # currently-real data/ pointer for these rows at all, only each
        # packet's own frozen_sources.
        #
        # Uses the dynamic STEP0-ready decision_date/generated_at pair (see
        # _step0_ready_decision_date_and_generated_at), not DECISION_DATE/
        # MORNING_GENERATED_AT: this test's whole point is that a genuine
        # STEP0 status CHANGE between two builds (the injected rolling-
        # pointer drift below) triggers a new revision. With the frozen
        # DECISION_DATE literal, BOTH the "first" and "second" real
        # STEP0_READ_MODEL_HEALTH evaluations are already collapsed
        # identically to DATA_BLOCKED by _enforce_temporal_boundary before
        # the drift can ever have any effect -- first is already blocked, so
        # "first vs. drifted-second" is DATA_BLOCKED-vs-DATA_BLOCKED, not a
        # real change, and no second revision is ever published. The
        # dynamic pair makes "first" genuinely READY (unblocked) so the
        # drifted "second" genuinely differs from it. The same generated_at
        # is reused for both builds -- publish()'s no-op-republish check
        # (_component_semantic_fingerprint) compares status/reason/values,
        # not generated_at, so the drift itself (not a generated_at bump) is
        # the real, sufficient, and only differentiator between first and
        # second here.
        step0_date, step0_generated_at = (
            _step0_ready_decision_date_and_generated_at()
        )
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original_evaluate = MODULE.BRIEFING_READINESS.evaluate

            first = MODULE.publish(
                "morning", step0_date, step0_generated_at, evidence_root
            )
            first_persisted = json.loads((first["path"] / "packet.json").read_text())

            def _drifted_pointer(decision_date, data_root):
                real = original_evaluate(decision_date, data_root)
                drifted = copy.deepcopy(real)
                drifted["classification"] = "data_not_ready"
                drifted["data_ready"] = False
                drifted["reasons"] = ["SIMULATED_ROLLING_POINTER_DRIFT"]
                return drifted

            MODULE.BRIEFING_READINESS.evaluate = _drifted_pointer
            try:
                second = MODULE.publish(
                    "morning", step0_date, step0_generated_at, evidence_root
                )
            finally:
                MODULE.BRIEFING_READINESS.evaluate = original_evaluate
            second_persisted = json.loads((second["path"] / "packet.json").read_text())
            # The drift is a real status change -> a new revision.
            self.assertTrue(second["created"])
            self.assertEqual(second["revision"], 2)
            second_by_id = {
                row["component_id"]: row for row in second_persisted["components"]
            }
            self.assertEqual(second_by_id["STEP0_READ_MODEL_HEALTH"]["status"], "DATA_BLOCKED")

            # No fault injection, no monkeypatch active here -- plain
            # validate_packet() calls, exactly what an external reporter or
            # a later audit would do.
            self.assertEqual(
                MODULE.validate_packet(copy.deepcopy(first_persisted)), first_persisted
            )
            self.assertEqual(
                MODULE.validate_packet(copy.deepcopy(second_persisted)), second_persisted
            )

    def test_semantic_fingerprint_includes_real_nested_source_sha_not_just_status(self):
        # A blanket "drop every key ending in sha256" fingerprint rule
        # would ALSO drop STEP0_READ_MODEL_HEALTH's real, per-source hash
        # (sources.krx.source_sha256) -- meaning a same-day re-collection
        # that changes only the underlying file's bytes, with status/counts
        # unchanged, would wrongly look like a no-op. Prove it is not: only
        # the nested source_sha256 changes, status/reason/value are
        # identical, and a new revision must still be published.
        #
        # Uses the dynamic STEP0-ready decision_date/generated_at pair (see
        # _step0_ready_decision_date_and_generated_at), not DECISION_DATE/
        # MORNING_GENERATED_AT: with the frozen DECISION_DATE literal,
        # STEP0_READ_MODEL_HEALTH is already collapsed to a generic
        # SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT DATA_BLOCKED by
        # _enforce_temporal_boundary before the mutated source_sha256 below
        # can ever reach the persisted packet (the boundary clears
        # packet["packet"], so frozen_sources still holds the raw fetched
        # snapshot but the row itself never reflects it either way) -- both
        # "first" and "second" end up with the exact same boundary-derived
        # fingerprint regardless of the injected sha mutation, so no second
        # revision is published and the assertions below about the real
        # mutated hash never hold. The dynamic pair makes STEP0_READ_MODEL_
        # HEALTH genuinely READY so the nested source_sha256 mutation is a
        # real, detectable semantic difference.
        step0_date, step0_generated_at = (
            _step0_ready_decision_date_and_generated_at()
        )
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original_evaluate = MODULE.BRIEFING_READINESS.evaluate

            first = MODULE.publish(
                "morning", step0_date, step0_generated_at, evidence_root
            )
            first_persisted = json.loads((first["path"] / "packet.json").read_text())
            first_krx_sha = first_persisted["frozen_sources"][
                "STEP0_READ_MODEL_HEALTH"
            ]["value"]["sources"]["krx"]["source_sha256"]

            def _same_status_different_source_sha(decision_date, data_root):
                real = original_evaluate(decision_date, data_root)
                mutated = copy.deepcopy(real)
                mutated["sources"]["krx"]["source_sha256"] = "f" * 64
                return mutated

            MODULE.BRIEFING_READINESS.evaluate = _same_status_different_source_sha
            try:
                second = MODULE.publish(
                    "morning", step0_date, step0_generated_at, evidence_root
                )
            finally:
                MODULE.BRIEFING_READINESS.evaluate = original_evaluate
            second_persisted = json.loads((second["path"] / "packet.json").read_text())

            first_by_id = {
                row["component_id"]: row for row in first_persisted["components"]
            }
            second_by_id = {
                row["component_id"]: row for row in second_persisted["components"]
            }
            # Status/reason genuinely unchanged...
            self.assertEqual(
                first_by_id["STEP0_READ_MODEL_HEALTH"]["status"],
                second_by_id["STEP0_READ_MODEL_HEALTH"]["status"],
            )
            self.assertEqual(
                first_by_id["STEP0_READ_MODEL_HEALTH"]["reason"],
                second_by_id["STEP0_READ_MODEL_HEALTH"]["reason"],
            )
            # ...only the nested source hash differs...
            self.assertNotEqual(first_krx_sha, "f" * 64)
            self.assertEqual(
                second_persisted["frozen_sources"]["STEP0_READ_MODEL_HEALTH"]["value"][
                    "sources"
                ]["krx"]["source_sha256"],
                "f" * 64,
            )
            # ...yet a new revision was still published, because the
            # fingerprint includes real nested source hashes.
            self.assertTrue(second["created"])
            self.assertEqual(second["revision"], 2)

    def test_temporal_boundary_applies_before_unified_decision_and_action_risk_summary(
        self,
    ):
        # A future/not-yet-available upstream row must be downgraded
        # BEFORE any aggregator consumes it -- not merely at the end of
        # build_packet() after UNIFIED_DECISION/ACTION_RISK_PORTFOLIO_
        # SUMMARY have already read it. Inject a violating available_at
        # into THREE_MARKET_REGIME_HEADER (which UNIFIED_DECISION directly
        # consumes, and which ACTION_RISK_PORTFOLIO_SUMMARY consumes
        # transitively through UNIFIED_DECISION) and prove neither
        # aggregator ever sees the smuggled value.
        original = MODULE.build_three_market_header

        def _future_header(regime_outputs, slot, generated_at):
            row = copy.deepcopy(original(regime_outputs, slot, generated_at))
            row["available_at"] = "2026-08-21T23:59:00Z"
            return row

        MODULE.build_three_market_header = _future_header
        try:
            packet = MODULE.build_packet(
                "morning", DECISION_DATE, "2026-08-21T12:00:00Z"
            )
        finally:
            MODULE.build_three_market_header = original
        by_id = {row["component_id"]: row for row in packet["components"]}

        # The upstream row itself is downgraded...
        self.assertEqual(by_id["THREE_MARKET_REGIME_HEADER"]["status"], "DATA_BLOCKED")
        self.assertEqual(
            by_id["THREE_MARKET_REGIME_HEADER"]["reason"], "AVAILABLE_AT_AFTER_GENERATED_AT"
        )
        self.assertIsNone(by_id["THREE_MARKET_REGIME_HEADER"]["packet"])

        # ...and UNIFIED_DECISION never received the smuggled REGIME
        # packet: its own REGIME source is unavailable, with a reason,
        # exactly as if THREE_MARKET_REGIME_HEADER had been unavailable
        # from the start.
        unified_packet = by_id["UNIFIED_DECISION"]["packet"]
        self.assertIsNotNone(unified_packet)
        regime_component = next(
            component
            for component in unified_packet["components"]
            if component["component"] == "REGIME"
        )
        self.assertEqual(regime_component["availability"], "UNAVAILABLE")
        self.assertNotEqual(regime_component["unavailable_reasons"], [])

        # ...and it therefore never reaches ACTION_RISK_PORTFOLIO_SUMMARY's
        # embedded UNIFIED_DECISION source either -- the smuggled
        # available_at value does not appear anywhere in its packet.
        summary_packet = by_id["ACTION_RISK_PORTFOLIO_SUMMARY"]["packet"]
        self.assertIsNotNone(summary_packet)
        self.assertNotIn(
            '"available_at": "2026-08-21T23:59:00Z"', json.dumps(summary_packet)
        )

    def test_generated_date_mismatch_isolates_unified_decision_not_whole_run(self):
        # decision_date deliberately does not match generated_at's KST
        # operating date. A UTC previous-calendar-date morning timestamp is
        # valid when it resolves to this decision_date in Asia/Seoul; this
        # case is one full operating day earlier and must still fail closed.
        # Built as "the day before the dynamic STEP0-ready decision_date"
        # with the dynamic STEP0-ready generated_at (see
        # _step0_ready_decision_date_and_generated_at), rather than the
        # fixed "2026-08-20"/MORNING_GENERATED_AT literals: with those
        # literals, STEP0_READ_MODEL_HEALTH's real qualified generated_at
        # (the mutable data/latest_{krx,dart,sec}.json pointer's real,
        # current collected_at_utc) is now well after MORNING_GENERATED_AT,
        # so _enforce_temporal_boundary collapses it to a generic
        # SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT DATA_BLOCKED with no
        # packet -- which in turn makes KRX_PREOPEN_COMPACT UNKNOWN
        # (STEP0_READ_MODEL_HEALTH_UNAVAILABLE), not the data-driven
        # DATA_BLOCKED this test intends to isolate. Anchoring generated_at
        # to the real qualified value (via the dynamic pair) while still
        # deliberately dating decision_date one day earlier reproduces the
        # same "generated_at's KST operating date does not match decision_date"
        # mismatch the test needs, without also tripping that unrelated
        # boundary check.
        step0_date, step0_generated_at = (
            _step0_ready_decision_date_and_generated_at()
        )
        mismatched_decision_date = (
            MODULE.dt.date.fromisoformat(step0_date) - MODULE.dt.timedelta(days=1)
        ).isoformat()
        packet = MODULE.build_packet(
            "morning", mismatched_decision_date, step0_generated_at
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(by_id["UNIFIED_DECISION"]["status"], "DEGRADED")
        self.assertIn(
            "GENERATED_DATE_MISMATCH", by_id["UNIFIED_DECISION"]["reason"]
        )
        # Every other, unrelated component still built normally: one
        # component's failure must not crash or blank out the rest. (STEP0
        # legitimately reports DATA_BLOCKED here too, because the mismatched
        # decision_date genuinely does not match the real committed
        # collector evidence's own collected_for_kst_date -- that is
        # correct, honest behaviour, not a cascading crash.)
        # These independent components must have exactly the status their
        # own production builders derive for this real evidence date.  Do
        # not freeze that status to READY: append-only archives can have an
        # honest date gap (for example BTC capture 2026-08-27 is absent),
        # and DATA_BLOCKED/NO_CAPTURE_FOR_DECISION_DATE is then the correct
        # non-cascading result.  The invariant under test is that the
        # UNIFIED_DECISION date failure does not rewrite either component.
        for component_id, builder in (
            ("BTC_TREND", MODULE.build_btc_trend),
            ("US_BREADTH_MEMBERSHIP", MODULE.build_us_breadth_membership),
        ):
            independent = builder(mismatched_decision_date)
            self.assertEqual(
                by_id[component_id]["status"], independent["status"], component_id
            )
            self.assertEqual(
                by_id[component_id]["reason"], independent["reason"], component_id
            )
        # KRX_PREOPEN_COMPACT correctly reports DATA_BLOCKED (a collector
        # data failure -- the mismatched date -- not a read-model-only
        # DEGRADED).  Builders that independently enforce the generated-date
        # boundary still fail closed, while STRATEGIC_CAPITAL_POSTURE can
        # honestly assemble a PENDING packet from unavailable machine-coded
        # sources instead of cascading a human-readable diagnostic failure.
        self.assertEqual(by_id["KRX_PREOPEN_COMPACT"]["status"], "DATA_BLOCKED")
        self.assertEqual(
            by_id["ACTION_RISK_PORTFOLIO_SUMMARY"]["status"], "DEGRADED"
        )
        self.assertEqual(by_id["STRATEGIC_CAPITAL_POSTURE"]["status"], "PENDING")
        self.assertTrue(by_id["STRATEGIC_CAPITAL_POSTURE"]["validated"])
        # The three builders with their own date/input failure remain
        # DEGRADED. Plus DYNAMIC_CLOCK whenever this decision_date (one day
        # before the dynamic STEP0-ready date) is behind P8-12's real
        # advancing evidence -- see the identical DYNAMIC_CLOCK note in
        # test_morning_build_against_real_evidence_has_no_degraded_components.
        expected_degraded = {
            "UNIFIED_DECISION",
            "DEFENSIVE_ACTION_DECISION",
            "ACTION_RISK_PORTFOLIO_SUMMARY",
        }
        if by_id["DYNAMIC_CLOCK"]["status"] == "DEGRADED":
            expected_degraded.add("DYNAMIC_CLOCK")
        actual_degraded = {row["component_id"] for row in packet["components"] if row["status"] == "DEGRADED"}
        self.assertEqual(actual_degraded, expected_degraded)
        self.assertEqual(packet["component_status_counts"]["DEGRADED"], len(expected_degraded))

    def test_single_component_failure_is_isolated(self):
        original = MODULE.BTC_TREND.build_transform

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated sensor failure")

        MODULE.BTC_TREND.build_transform = _boom
        try:
            row = MODULE.build_btc_trend(DECISION_DATE)
        finally:
            MODULE.BTC_TREND.build_transform = original
        self.assertEqual(row["status"], "DEGRADED")
        self.assertIn("simulated sensor failure", row["reason"])

        # And the full pipeline still assembles every other component even
        # while BTC_TREND is broken. This needs TWO separate packet builds,
        # not one: BTC_TREND's fault only actually reaches
        # BTC_TREND.build_transform (and so surfaces as DEGRADED rather than
        # a directory-absent DATA_BLOCKED) at a decision_date its own
        # per-date archive genuinely has a directory for, which the
        # US_BREADTH-ready pair's date satisfies (like DECISION_DATE would)
        # -- but STEP0_READ_MODEL_HEALTH's own healthy baseline can only be
        # proven READY at the STEP0-ready dynamic pair (see
        # _step0_ready_decision_date_and_generated_at), since STEP0 reads an
        # entirely independent mutable rolling pointer that is NOT READY at
        # the US_BREADTH pair's (necessarily different, see that helper's
        # own docstring) date. One packet build cannot satisfy both real
        # constraints at once, so BTC_TREND's DEGRADED-from-failure
        # isolation is proven against the US_BREADTH-ready pair, and
        # STEP0_READ_MODEL_HEALTH's healthy-despite-a-different-component's-
        # fault isolation is proven separately against the STEP0-ready
        # pair, with the same fault still injected in both builds.
        us_breadth_date, us_breadth_generated_at = (
            _us_breadth_and_btc_ready_decision_date_and_generated_at()
        )
        # Prove the fixture reaches both healthy production builders before
        # injecting the fault.  Otherwise a future archive skew could turn
        # this back into an assertion about missing evidence instead of
        # component failure isolation.
        self.assertEqual(
            MODULE.build_us_breadth_membership(us_breadth_date)["status"],
            "READY",
        )
        self.assertEqual(MODULE.build_btc_trend(us_breadth_date)["status"], "READY")
        MODULE.BTC_TREND.build_transform = _boom
        try:
            us_breadth_packet = MODULE.build_packet(
                "morning", us_breadth_date, us_breadth_generated_at
            )
        finally:
            MODULE.BTC_TREND.build_transform = original
        us_breadth_by_id = {
            row["component_id"]: row for row in us_breadth_packet["components"]
        }
        self.assertEqual(us_breadth_by_id["BTC_TREND"]["status"], "DEGRADED")
        self.assertEqual(us_breadth_by_id["US_BREADTH_MEMBERSHIP"]["status"], "READY")

        step0_date, step0_generated_at = (
            _step0_ready_decision_date_and_generated_at()
        )
        MODULE.BTC_TREND.build_transform = _boom
        try:
            step0_packet = MODULE.build_packet(
                "morning", step0_date, step0_generated_at
            )
        finally:
            MODULE.BTC_TREND.build_transform = original
        step0_by_id = {
            row["component_id"]: row for row in step0_packet["components"]
        }
        self.assertEqual(step0_by_id["STEP0_READ_MODEL_HEALTH"]["status"], "READY")

    def test_unavailable_exception_reason_does_not_degrade_unified_decision(self):
        source_ids = (
            "THREE_MARKET_REGIME_HEADER",
            "ROTATION_DISCOVERY",
            "RULE_EVALUATION",
            "PORTFOLIO_BUCKET",
            "PORTFOLIO_CURRENCY",
            "ACTION_BOUNDARY",
        )
        rows = {
            component_id: MODULE.component_row(
                component_id, "PENDING", "SOURCE_PACKET_NOT_PROVIDED"
            )
            for component_id in source_ids
        }
        diagnostic = (
            "RotationDiscoveryBriefingError:"
            "DYNAMIC_SIGNAL_INPUT_INVALID:REPORT_DECISION_AFTER_BOUNDARY_AS_OF"
        )
        rows["ROTATION_DISCOVERY"] = MODULE.component_row(
            "ROTATION_DISCOVERY", "DEGRADED", diagnostic
        )

        result = MODULE.build_unified_decision(
            rows, DECISION_DATE, "morning", MORNING_GENERATED_AT
        )

        self.assertEqual(result["status"], "PENDING")
        self.assertTrue(result["validated"])
        by_name = {
            row["component"]: row for row in result["packet"]["components"]
        }
        self.assertEqual(
            by_name["ROTATION_DISCOVERY"]["unavailable_reasons"],
            ["ROTATION_DISCOVERY_DEGRADED"],
        )
        self.assertEqual(rows["ROTATION_DISCOVERY"]["reason"], diagnostic)

    def test_no_action_order_production_or_trading_authority_is_ever_true(self):
        for slot, generated_at in (
            ("morning", MORNING_GENERATED_AT),
            ("evening", EVENING_GENERATED_AT),
        ):
            packet = MODULE.build_packet(slot, DECISION_DATE, generated_at)
            structural_true_allowed = {
                "aggregation_only", "component_build_authorized",
                "daily_decision_assembly_only", "briefing_read_model_only",
                "evidence_only",
            }
            for path, value in _walk_authorized_keys(packet):
                key = path.rsplit(".", 1)[-1]
                if key in structural_true_allowed:
                    continue
                self.assertFalse(
                    value, f"{slot}: {path} must remain false, got {value}"
                )
            for row in packet["components"]:
                self.assertFalse(row["decision_eligible"], row["component_id"])
                self.assertFalse(row["action_eligible"], row["component_id"])
                self.assertFalse(row["order_eligible"], row["component_id"])
            self.assertEqual(packet["decision"] if "decision" in packet else None, None)

    def test_render_markdown_covers_required_sections_and_hides_nothing(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        rendered = MODULE.render_markdown(packet)
        flow_titles = [
            "## 1. Regime", "## 2. Cross-Market Flow",
            "## 3. Theme Rotation", "## 4. Capital Action",
            "## 5. Assets", "## 6. Entry / Exit / Size",
        ]
        positions = [rendered.index(title) for title in flow_titles]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("CROSS_MARKET_COMPARISON_POLICY_UNRATIFIED", rendered)
        self.assertIn("evidence_class_counts", rendered)
        self.assertIn("flow_direction: UNKNOWN", rendered)
        self.assertIn("SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED", rendered)
        self.assertIn("UPSTREAM_INVALIDATION_NOT_STANDARDIZED", rendered)
        for required in (
            "Data / Read-model health", "3-Market Regime", "Rotation / Theme",
            "Rule status", "Portfolio / Risk", "Decision Review",
            "Decision & action boundary", "Shadow learning record",
            "PENDING / UNKNOWN / DEGRADED / BLOCKED components",
            "Unresolved boundaries",
        ):
            self.assertIn(required, rendered)
        # Every single component must appear somewhere in the render, so a
        # blocked/pending section is shown with its reason, never hidden.
        for row in packet["components"]:
            self.assertIn(row["component_id"], rendered)
            if row["reason"]:
                self.assertIn(row["reason"], rendered)
        self.assertIn(
            "No action, order, Production, or trading authority", rendered
        )

    def test_market_freshness_is_independent_and_stale_equity_values_are_withheld(self):
        packet = {"decision_date": "2026-09-01"}
        by_id = {
            "KOREA_MARKET_SIGNALS": {"status": "READY", "as_of_date": "2026-08-31"},
            "FREE_MARKET_DATA": {"status": "READY", "as_of_date": "2026-08-28"},
            "BTC_TREND": {"as_of_date": "2026-09-01"},
            "BTC_RISK": {"as_of_date": "2026-09-01"},
            "STABLECOIN_NET_ISSUANCE": {"as_of_date": "2026-09-01"},
        }
        context = "\n".join(MODULE._market_session_freshness_lines(packet, by_id))
        self.assertIn("## 3-market session board", context)
        self.assertIn("### KRX · 한국", context)
        self.assertIn("session: FRESH_CLOSE_PENDING", context)
        self.assertIn("evidence_date=2026-08-31", context)
        self.assertIn("### US · 미국", context)
        self.assertIn("session: INDEPENDENT_SESSION_PENDING", context)
        self.assertIn("evidence_date=2026-08-28", context)
        self.assertIn("### Crypto · 코인", context)
        self.assertIn("session: CONTINUOUS_CURRENT_EVIDENCE", context)
        self.assertIn("continuous_observation_date: 2026-09-01", context)
        self.assertIn("pending a same-date validated close", context)
        self.assertIn("pending independently dated validated US session evidence", context)

        korea = {
            "component_id": "KOREA_MARKET_SIGNALS",
            "as_of_date": "2026-08-31",
            "packet": {
                "as_of_date": "2026-08-31",
                "axes": {"TREND": {"measurement": {"benchmarks": {
                    "KOSPI": {"one_session_return_pct": "9.99"},
                    "KOSDAQ": {"one_session_return_pct": "8.88"},
                }}}},
            },
        }
        korea_detail = "\n".join(MODULE._format_component_detail(korea, "2026-09-01"))
        self.assertIn("한국 종가 수치 보류", korea_detail)
        self.assertNotIn("코스피=9.99%", korea_detail)

        us = {
            "component_id": "FREE_MARKET_DATA",
            "as_of_date": "2026-08-28",
            "packet": {
                "vixcls": {"value": "14.43", "date": "2026-08-28"},
                "alpaca_iex_bars": [{"symbol": "SPY", "close": "766.87"}],
                "scope_warning": "PARTIAL",
            },
        }
        us_detail = "\n".join(MODULE._format_component_detail(us, "2026-09-01"))
        self.assertIn("US close values withheld", us_detail)
        self.assertNotIn("VIXCLS=14.43", us_detail)

    def test_weekend_morning_discloses_closed_session_without_date_relabelling(self):
        packet = MODULE.build_packet(
            "morning", "2026-08-29", "2026-08-28T22:09:34Z"
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(
            by_id["KRX_POST_CLOSE"]["reason"],
            "WEEKEND_MORNING_MARKET_CLOSED_NO_NEW_SESSION_LATEST_CONFIRMED_EVIDENCE",
        )
        rendered = MODULE.render_markdown(packet)
        self.assertIn("- market_session: MARKET_CLOSED", rendered)
        self.assertIn("- new_session: NONE", rendered)
        latest_line = next(
            line for line in rendered.splitlines()
            if line.startswith("- latest_confirmed_evidence_date: ")
        )
        latest_confirmed = latest_line.partition(": ")[2]
        # Rolling committed inputs may be newer than this historical
        # generated_at.  In that case the temporal boundary must disclose
        # UNKNOWN, never relabel future/current evidence as the weekend date.
        if latest_confirmed != "UNKNOWN":
            self.assertLess(latest_confirmed, "2026-08-29")
        self.assertIn(
            "- latest_confirmed_evidence_relabelled_as_today: false", rendered
        )

    def test_render_markdown_shows_real_values_not_just_status_and_path(self):
        # The render must be an actual briefing, not a raw JSON dump and not
        # a bare status/path list -- real per-component values that exist
        # today (BTC direction, drawdown, stablecoin issuance, US breadth
        # membership, rule PASS/FAIL/UNKNOWN counts, regime coverage ratio)
        # must be legible in the text.
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        rendered = MODULE.render_markdown(packet)
        by_id = {row["component_id"]: row for row in packet["components"]}

        self.assertIn(
            f"direction={by_id['BTC_TREND']['packet']['direction']}", rendered
        )
        drawdown = by_id["BTC_RISK"]["packet"]["risk_point"]["drawdown"]
        self.assertIn(f"current_drawdown={drawdown['current_fraction']}", rendered)
        self.assertIn(
            f"daily_net_issuance="
            f"{by_id['STABLECOIN_NET_ISSUANCE']['packet']['daily_net_issuance_native_usd_peg']}",
            rendered,
        )
        # US_BREADTH_MEMBERSHIP is DATA_BLOCKED (packet=None) under
        # DECISION_DATE/MORNING_GENERATED_AT -- see
        # _us_breadth_ready_decision_date_and_generated_at() -- so its real
        # member_count is checked in a render of its own READY packet
        # instead, not this one.
        us_breadth_date, us_breadth_generated_at = (
            _us_breadth_ready_decision_date_and_generated_at()
        )
        us_breadth_packet = MODULE.build_packet(
            "morning", us_breadth_date, us_breadth_generated_at
        )
        us_breadth_rendered = MODULE.render_markdown(us_breadth_packet)
        us_breadth_row = {
            row["component_id"]: row for row in us_breadth_packet["components"]
        }["US_BREADTH_MEMBERSHIP"]
        self.assertEqual(us_breadth_row["status"], "READY")
        self.assertIn(
            f"members={us_breadth_row['packet']['member_count']}",
            us_breadth_rendered,
        )
        rule_summary = by_id["RULE_EVALUATION"]["packet"]["summary"]
        self.assertIn(
            f"PASS={rule_summary['PASS']} FAIL={rule_summary['FAIL']} "
            f"UNKNOWN={rule_summary['UNKNOWN']} UNDEFINED={rule_summary['UNDEFINED']}",
            rendered,
        )
        self.assertIn("coverage=0/5", rendered)
        review = by_id["INVESTMENT_DECISION_REVIEW"]
        self.assertEqual(review["status"], "POLICY_BLOCKED")
        self.assertEqual(review["packet"]["review_outcome"], "BLOCKED")
        self.assertIsNone(review["packet"]["trade_proposal"])
        self.assertEqual(review["packet"]["money_action"], "NONE")
        self.assertIn("review=BLOCKED", rendered)
        self.assertIn("trade_proposal=None", rendered)
        self.assertIn("money_action=NONE", rendered)
        shadow = by_id["INVESTMENT_REVIEW_SHADOW"]
        self.assertFalse(shadow["packet"]["ledger_record_created"])
        self.assertEqual(shadow["packet"]["capital"], {"authorized": False, "amount": 0})
        self.assertIsNone(shadow["packet"]["order"])
        # No raw JSON dump: braces-and-quotes packet serialization must not
        # appear verbatim (the top-level status-counts summary dict repr is
        # the one intentional exception).
        self.assertNotIn('"schema_version"', rendered)
        self.assertNotIn("'schema_version'", rendered)

    def test_publish_is_atomic_and_a_no_op_republish_reuses_the_existing_revision(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            first = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
            )
            self.assertTrue(first["created"])
            self.assertEqual(first["revision"], 1)
            target = first["path"]
            self.assertEqual(target.name, "rev-001")
            self.assertTrue((target / "packet.json").exists())
            self.assertTrue((target / "briefing.md").exists())
            original_bytes = (target / "packet.json").read_bytes()

            # Republishing with nothing new to say (same decision_date,
            # same real evidence, only generated_at ticks forward) must be
            # a genuine no-op: it reuses rev-001 rather than manufacturing
            # an identical-in-substance rev-002.
            second = MODULE.publish(
                "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
            )
            self.assertFalse(second["created"])
            self.assertEqual(second["revision"], 1)
            self.assertEqual(second["path"], target)
            self.assertEqual((target / "packet.json").read_bytes(), original_bytes)
            self.assertFalse(
                any(p.name.startswith(".") for p in target.parent.iterdir()),
                "no leftover temp directory after a no-op republish",
            )
            index = MODULE._read_index(target.parent)
            self.assertEqual(index["latest_revision"], 1)
            self.assertEqual(len(index["revisions"]), 1)

    def test_publish_leaves_no_partial_bundle_on_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original = MODULE.validate_packet

            def _always_fail(*args, **kwargs):
                raise MODULE.DailyOrchestratorError("SIMULATED_VALIDATION_FAILURE")

            MODULE.validate_packet = _always_fail
            try:
                with self.assertRaisesRegex(
                    MODULE.DailyOrchestratorError, "SIMULATED_VALIDATION_FAILURE"
                ):
                    MODULE.publish(
                        "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
                    )
            finally:
                MODULE.validate_packet = original
            target = evidence_root / "morning" / DECISION_DATE
            self.assertFalse(target.exists())
            self.assertFalse(evidence_root.exists())

    def test_idempotent_rerun_produces_identical_published_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            first = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
            )
            first_bytes = (first["path"] / "packet.json").read_bytes()
            evidence_root_2 = Path(tmp) / "daily_briefing_2"
            second = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root_2
            )
            second_bytes = (second["path"] / "packet.json").read_bytes()
            self.assertEqual(first_bytes, second_bytes)

    def test_same_day_recovery_publishes_a_new_revision_when_status_changes(self):
        # A first publish that catches a component mid-DATA_BLOCKED (e.g.
        # its capture had not landed yet) is not a dead end: republishing
        # once that component recovers must add a new revision, preserving
        # the first one, rather than being rejected as append-only or
        # silently treated as a no-op.
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original = MODULE.BTC_TREND.build_transform

            def _boom(*args, **kwargs):
                raise RuntimeError("capture not landed yet")

            MODULE.BTC_TREND.build_transform = _boom
            try:
                first = MODULE.publish(
                    "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
                )
            finally:
                MODULE.BTC_TREND.build_transform = original
            self.assertTrue(first["created"])
            self.assertEqual(first["revision"], 1)
            first_packet = json.loads((first["path"] / "packet.json").read_text())
            first_by_id = {
                row["component_id"]: row for row in first_packet["components"]
            }
            self.assertEqual(first_by_id["BTC_TREND"]["status"], "DEGRADED")

            # Now the real sensor is used (no monkeypatch) -- BTC_TREND
            # recovers to READY, so a republish must create rev-002.
            second = MODULE.publish(
                "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
            )
            self.assertTrue(second["created"])
            self.assertEqual(second["revision"], 2)
            self.assertEqual(second["path"].name, "rev-002")
            second_packet = json.loads((second["path"] / "packet.json").read_text())
            second_by_id = {
                row["component_id"]: row for row in second_packet["components"]
            }
            self.assertEqual(second_by_id["BTC_TREND"]["status"], "READY")

            # rev-001 must still exist, byte-identical to what it was.
            self.assertEqual(
                json.loads((first["path"] / "packet.json").read_text()), first_packet
            )
            index = MODULE._read_index(evidence_root / "morning" / DECISION_DATE)
            self.assertEqual(index["latest_revision"], 2)
            self.assertEqual([r["revision"] for r in index["revisions"]], [1, 2])

    def test_same_day_recovery_publishes_a_new_revision_on_value_change_even_if_status_unchanged(
        self,
    ):
        # A component that stays READY -> READY across two builds, but
        # whose actual retained value silently changed underneath (e.g. a
        # same-day corrected re-collection), must still trigger a new
        # revision. A status-only comparison would wrongly treat this as
        # "nothing changed" and reuse the stale revision forever.
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original = MODULE.BTC_TREND.build_transform

            def _direction_a(*args, **kwargs):
                packet = copy.deepcopy(original(*args, **kwargs))
                packet["direction"] = "ABOVE_200DMA"
                return packet

            MODULE.BTC_TREND.build_transform = _direction_a
            try:
                first = MODULE.publish(
                    "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
                )
            finally:
                MODULE.BTC_TREND.build_transform = original
            self.assertTrue(first["created"])
            first_packet = json.loads((first["path"] / "packet.json").read_text())
            first_by_id = {
                row["component_id"]: row for row in first_packet["components"]
            }
            self.assertEqual(first_by_id["BTC_TREND"]["status"], "READY")
            self.assertEqual(
                first_by_id["BTC_TREND"]["packet"]["direction"], "ABOVE_200DMA"
            )

            def _direction_b(*args, **kwargs):
                packet = copy.deepcopy(original(*args, **kwargs))
                packet["direction"] = "BELOW_200DMA"
                return packet

            MODULE.BTC_TREND.build_transform = _direction_b
            try:
                second = MODULE.publish(
                    "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
                )
            finally:
                MODULE.BTC_TREND.build_transform = original
            # Status is READY both times -- a status-only fingerprint would
            # call this a no-op. The direction value actually changed, so a
            # new revision must be created.
            self.assertTrue(second["created"])
            self.assertEqual(second["revision"], 2)
            second_packet = json.loads((second["path"] / "packet.json").read_text())
            second_by_id = {
                row["component_id"]: row for row in second_packet["components"]
            }
            self.assertEqual(second_by_id["BTC_TREND"]["status"], "READY")
            self.assertEqual(
                second_by_id["BTC_TREND"]["packet"]["direction"], "BELOW_200DMA"
            )

    def test_same_day_recovery_every_revision_independently_validates_and_rejects_tamper(
        self,
    ):
        # After a same-day recovery produces rev-001 (BTC_TREND artificially
        # DEGRADED) and rev-002 (recovered to READY), BOTH persisted
        # revisions -- not just the latest one -- must independently
        # re-validate under their own real build-time conditions, and each
        # must independently reject a semantic tamper + rehash of its own
        # bytes. publish()'s existing-revision check is a cheap self-hash
        # only (see the comment in publish()); this test proves the
        # stronger guarantee -- full validate_packet() -- separately holds
        # for every revision that was ever written, not just the one
        # publish() happens to compare against.
        #
        # rev-001 must be revalidated while the same fault injection that
        # produced it is still active: a DEGRADED-from-exception status is,
        # by nature, a snapshot of a transient build-time failure, not
        # re-derivable evidence -- removing the injection before
        # revalidating it would make an honest, untampered rev-001 fail to
        # match, which is a fault-injection artifact of this test, not the
        # property being proven here.
        def _tamper_and_rehash(persisted, component_id, field_path):
            tampered = copy.deepcopy(persisted)
            for row in tampered["components"]:
                if row["component_id"] == component_id:
                    target = row
                    for key in field_path[:-1]:
                        target = target[key]
                    target[field_path[-1]] = "TAMPERED"
            unsigned = copy.deepcopy(tampered)
            del unsigned["packet_sha256"]
            tampered["packet_sha256"] = MODULE.payload_sha256(unsigned)
            return tampered

        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original = MODULE.BTC_TREND.build_transform

            def _boom(*args, **kwargs):
                raise RuntimeError("capture not landed yet")

            MODULE.BTC_TREND.build_transform = _boom
            try:
                first = MODULE.publish(
                    "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
                )
                first_persisted = json.loads(
                    (first["path"] / "packet.json").read_text()
                )
                self.assertEqual(
                    MODULE.validate_packet(copy.deepcopy(first_persisted)),
                    first_persisted,
                )
                with self.assertRaisesRegex(
                    MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"
                ):
                    MODULE.validate_packet(
                        _tamper_and_rehash(
                            first_persisted,
                            "STABLECOIN_NET_ISSUANCE",
                            ["packet", "daily_status"],
                        )
                    )
            finally:
                MODULE.BTC_TREND.build_transform = original

            second = MODULE.publish(
                "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
            )
            second_persisted = json.loads(
                (second["path"] / "packet.json").read_text()
            )
            self.assertEqual(first["revision"], 1)
            self.assertEqual(second["revision"], 2)
            self.assertNotEqual(first["path"], second["path"])
            self.assertEqual(
                MODULE.validate_packet(copy.deepcopy(second_persisted)),
                second_persisted,
            )
            with self.assertRaisesRegex(
                MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"
            ):
                MODULE.validate_packet(
                    _tamper_and_rehash(
                        second_persisted,
                        "STABLECOIN_NET_ISSUANCE",
                        ["packet", "daily_status"],
                    )
                )
            # rev-001's own persisted bytes on disk are untouched by any of
            # the above (all tampering happened on in-memory copies).
            self.assertEqual(
                json.loads((first["path"] / "packet.json").read_text()),
                first_persisted,
            )

    def test_temporal_boundary_rejects_as_of_date_after_decision_date(self):
        row = MODULE.component_row(
            "BTC_TREND", "READY", None, as_of_date="2099-01-02"
        )
        result = MODULE._enforce_temporal_boundary(
            row, "2099-01-01", MODULE.dt.datetime.fromisoformat("2099-01-01T12:00:00+00:00")
        )
        self.assertEqual(result["status"], "DATA_BLOCKED")
        self.assertEqual(result["reason"], "AS_OF_DATE_AFTER_DECISION_DATE")
        self.assertIsNone(result["packet"])

    def test_temporal_boundary_rejects_available_at_after_generated_at(self):
        # A common, generic check -- proven here directly against the
        # helper, and again below through build_packet() with a
        # monkeypatched sensor -- so no future sensor can silently smuggle
        # not-yet-available evidence past this by simply not implementing
        # its own guard.
        row = MODULE.component_row(
            "KOFIA_FIRST_SEEN", "READY", None,
            as_of_date="2026-08-21", available_at="2026-08-21T15:00:00Z",
        )
        generated_at_dt = MODULE.dt.datetime.fromisoformat("2026-08-21T12:00:00+00:00")
        result = MODULE._enforce_temporal_boundary(row, "2026-08-21", generated_at_dt)
        self.assertEqual(result["status"], "DATA_BLOCKED")
        self.assertEqual(result["reason"], "AVAILABLE_AT_AFTER_GENERATED_AT")

        # And the same afternoon-evidence-not-visible-to-a-morning-packet
        # scenario end to end through build_packet(), not just the unit
        # helper: a morning packet generated early in the day must not read
        # a component whose evidence only became available later that day.
        original = MODULE._classify_btc_trend

        def _afternoon_evidence(snapshot):
            row = copy.deepcopy(original(snapshot))
            row["available_at"] = "2026-08-21T15:00:00Z"
            return row

        MODULE._classify_btc_trend = _afternoon_evidence
        try:
            packet = MODULE.build_packet(
                "morning", DECISION_DATE, "2026-08-21T12:00:00Z"
            )
        finally:
            MODULE._classify_btc_trend = original
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(by_id["BTC_TREND"]["status"], "DATA_BLOCKED")
        self.assertEqual(by_id["BTC_TREND"]["reason"], "AVAILABLE_AT_AFTER_GENERATED_AT")

    def test_existing_corrupted_revision_is_surfaced_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            first = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
            )
            packet_path = first["path"] / "packet.json"
            corrupted = json.loads(packet_path.read_text())
            corrupted["component_status_counts"]["READY"] += 999
            packet_path.write_text(json.dumps(corrupted), encoding="utf-8")

            with self.assertRaisesRegex(
                MODULE.DailyOrchestratorError, "EXISTING_REVISION_INVALID"
            ):
                MODULE.publish(
                    "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
                )

    def test_slot_and_generated_at_are_validated(self):
        with self.assertRaisesRegex(MODULE.DailyOrchestratorError, "SLOT_INVALID"):
            MODULE.build_packet("afternoon", DECISION_DATE, MORNING_GENERATED_AT)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "GENERATED_AT_INVALID"
        ):
            MODULE.build_packet("morning", DECISION_DATE, "not-a-timestamp")
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "GENERATED_AT_INVALID"
        ):
            MODULE.build_packet(
                "morning", DECISION_DATE, "2026-08-21T12:00:00"
            )  # missing tz offset

    def test_contract_authority_and_status_vocabulary_are_pinned(self):
        contract = MODULE.load_contract()
        self.assertEqual(
            set(contract["component_status_values"]), MODULE.STATUS_VALUES
        )
        for key, value in contract["authority"].items():
            if key in ("aggregation_only", "component_build_authorized"):
                self.assertTrue(value, key)
            else:
                self.assertFalse(value, key)

    def test_no_workflow_calls_a_live_provider_from_this_module(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("import requests", script)
        self.assertNotIn("urlopen", script)
        self.assertNotIn("urllib.request", script)

    def test_workflow_calls_the_real_orchestrator_before_committing(self):
        triggers = WF.get("on", WF.get(True))
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertEqual(WF["permissions"], {"contents": "write"})
        self.assertEqual(WF["concurrency"]["cancel-in-progress"], False)
        steps = WF["jobs"]["briefing"]["steps"]
        regression_steps = WF["jobs"]["offline-regression"]["steps"]
        regression = next(
            step for step in regression_steps
            if step.get("name") == "Offline daily orchestrator regression"
        )
        self.assertIn("test_daily_orchestrator.py", regression["run"])
        self.assertNotIn(
            "Offline daily orchestrator regression",
            [step.get("name") for step in steps],
        )
        publish = next(
            step for step in steps
            if step.get("name") == "Publish provider-free daily briefing packet"
        )
        command = publish["run"]
        self.assertIn("briefing/daily_orchestrator.py publish", command)
        self.assertIn("briefing/daily_orchestrator.py validate", command)
        self.assertIn("daily_briefing_delivery.py publish-locator", command)
        self.assertIn("daily_briefing_delivery.py consume", command)
        self.assertIn("publish_scheduled_briefing_authority.py publish", command)
        self.assertIn("publish_scheduled_briefing_authority.py validate", command)
        self.assertIn("decision/decision_change_lineage_operational.py", command)
        self.assertIn("shadow/three_market_shadow_operational_readiness.py", command)
        self.assertLess(
            command.index("publish_scheduled_briefing_authority.py publish"),
            command.index("decision/decision_change_lineage_operational.py"),
        )
        self.assertLess(
            command.index("decision/decision_change_lineage_operational.py"),
            command.index("shadow/three_market_shadow_operational_readiness.py"),
        )
        self.assertIn("LINEAGE_STATUS=BLOCKED_FAIL_CLOSED", command)
        self.assertIn("SHADOW_READINESS_STATUS=BLOCKED_FAIL_CLOSED", command)
        self.assertNotIn('SOURCE_COMMIT=$(git rev-parse HEAD)', command)
        self.assertIn('CONSUMER_READY_COMMIT=$(git rev-parse HEAD)', command)
        self.assertIn('--source-commit "$CONSUMER_READY_COMMIT"', command)
        self.assertLess(
            command.index('git add data/briefing/daily_briefing_sources.json'),
            command.index('--source-commit "$CONSUMER_READY_COMMIT"'),
        )
        # publish() itself decides whether a new revision is needed (same-
        # day recovery); the workflow must always call it rather than
        # skipping on bare directory presence, and must gate the commit on
        # created=true rather than "does the path already exist".
        self.assertNotIn("skipped_existing", command)
        self.assertIn("no_new_revision", command)
        self.assertIn('CREATED=$(echo "$OUTPUT" | sed -n \'s/^created=//p\')', command)
        self.assertIn('for ATTEMPT in 1 2; do', command)
        self.assertIn('git reset --hard "origin/$DEFAULT_BRANCH"', command)
        self.assertIn('if [ "$CREATED" != "true" ]', command)
        # The accepted finalization gate, rather than the producer, now owns
        # the single human-reaching write.  The producer may only prepare the
        # immutable consume payload for that later gate.
        self.assertNotIn("GITHUB_STEP_SUMMARY", command)
        self.assertFalse(WF["jobs"]["briefing"].get("needs"))
        # Commit lives in the publish step so a rejected push can discard
        # the entire attempt and regenerate the immutable pointer from a
        # fresh main, never rebasing a pointer built from stale bytes.
        self.assertNotIn(
            "Commit immutable daily briefing bundle",
            [step.get("name") for step in steps],
        )
        # The whole decision_date directory must be staged, not just the
        # new rev-NNN/ subdirectory, so the sibling index.json (rewritten
        # on every new revision) is committed alongside it.
        self.assertIn('git add "$(dirname "$CAPTURE_PATH")"', command)
        self.assertIn(
            "git add data/briefing/daily_briefing_sources.json", command
        )
        self.assertIn('git add "$AUTHORITY_PATH"', command)
        self.assertIn('git add "$LINEAGE_PATH"', command)
        self.assertIn("record_created=//p", command)
        self.assertNotIn("git pull --rebase", command)
        self.assertIn('if git push origin "HEAD:$DEFAULT_BRANCH"; then', command)
        self.assertIn("main advanced twice; no briefing locator was published", command)
        self.assertIn("main advanced twice; no authority bootstrap was published", command)
        self.assertIn("main advanced twice while confirming unchanged delivery", command)
        self.assertIn('if [ "$LOCAL_COMMIT" != "$REMOTE_COMMIT" ]; then', command)

        resync = next(
            step for step in steps
            if step.get("name") == "Re-sync to the latest main before binding retrieval authority"
        )
        self.assertIn('git reset --hard "origin/$DEFAULT_BRANCH"', resync["run"])
        self.assertLess(steps.index(resync), steps.index(publish))

    def test_workflow_derives_slot_from_exact_cron_not_wall_clock_hour(self):
        # A large scheduler delay must not misclassify morning as evening
        # (or vice versa): the slot must come from the exact cron
        # expression GitHub reports fired (github.event.schedule), not from
        # what KST hour the runner happens to be executing at.
        schedule = WF.get("on", WF.get(True))["schedule"]
        self.assertEqual(
            {item["cron"] for item in schedule},
            {"5 22 * * *", "30 9 * * 1-5"},
        )
        publish = next(
            step for step in WF["jobs"]["briefing"]["steps"]
            if step.get("name") == "Publish provider-free daily briefing packet"
        )
        command = publish["run"]
        self.assertIn("EVENT_SCHEDULE", publish.get("env", {}))
        self.assertIn('"5 22 * * *") SLOT="morning"', command)
        self.assertIn('"30 9 * * 1-5") SLOT="evening"', command)
        self.assertNotIn("date +%H", command)
        self.assertNotIn("KST_HOUR", command)

    def test_same_day_recovery_has_no_automatic_trigger_and_says_so(self):
        # publish() can correctly add a same-day recovery revision when
        # called again (proven above), but nothing currently re-invokes it
        # automatically during the day -- only the two approved scheduled
        # entry points plus manual workflow_dispatch exist. Rather than
        # fabricating a new, unapproved cron to manufacture that
        # capability, this is left as an honest, disclosed WBS blocker
        # baked into every packet, not just documentation prose.
        schedule = WF.get("on", WF.get(True))["schedule"]
        self.assertEqual(len(schedule), 2, "no unapproved third schedule entry")
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        self.assertIn(
            "SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED",
            packet["unresolved_boundaries"],
        )

    def test_korea_rotation_missing_pointer_is_pending_not_degraded(self):
        row = MODULE.build_korea_rotation(
            DECISION_DATE, snapshot={"kind": "missing", "value": None}
        )
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["reason"], "NO_ROTATION_POINTER_PUBLISHED")
        self.assertFalse(row["validated"])
        self.assertIsNone(row["packet"])

    def test_korea_rotation_read_error_is_degraded(self):
        row = MODULE.build_korea_rotation(
            DECISION_DATE, snapshot={"kind": "error", "value": "JSON_READ_FAILED"}
        )
        self.assertEqual(row["status"], "DEGRADED")
        self.assertEqual(row["reason"], "JSON_READ_FAILED")

    def test_korea_rotation_wrong_date_pointer_is_pending(self):
        payload = {
            "as_of_date": "2026-08-19", "run_status": "OK",
            "rotation": {"status": "ROTATION_BUCKETS_OBSERVED", "rotation_policy_effective": True},
            "breadth": {"status": "AVAILABLE", "decision_eligible": True},
        }
        row = MODULE.build_korea_rotation(
            DECISION_DATE, snapshot={"kind": "payload", "value": payload}
        )
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["reason"], "NO_ROTATION_OBSERVATION_FOR_DECISION_DATE")
        self.assertEqual(row["as_of_date"], "2026-08-19")

    def test_korea_rotation_real_blocked_breadth_surfaces_as_policy_blocked(self):
        # Exact shape build_briefing_pointer() produces for the real
        # 2026-08-21 P1-KR-05/P3-03 BLOCKED lineage proof.
        payload = {
            "schema_version": "korea_rotation_briefing_pointer/1",
            "contract_version": "korea_capital_rotation/3",
            "as_of_date": "2026-08-21",
            "generated_at": "2026-08-22T03:35:36Z",
            "run_status": "OK",
            "rotation": {
                "status": "ROTATION_BUCKETS_OBSERVED",
                "rotation_policy_effective": True,
                "packet_sha256": "a" * 64,
            },
            "breadth": {
                "status": "BLOCKED",
                "reason": "KOSDAQ_AVAILABLE_AT_NULL,KOSPI_AVAILABLE_AT_NULL",
                "decision_eligible": False,
                "ranking_input_authorized": False,
                "markets": {
                    "KOSPI": {"lineage_sha256": "b" * 64, "as_of_date": "2026-08-21", "available_at": None},
                    "KOSDAQ": {"lineage_sha256": "c" * 64, "as_of_date": "2026-08-21", "available_at": None},
                },
                "source_context_path": "data/observations/korea_breadth_context/2026-08-21/packet.json",
                "source_context_sha256": "d" * 64,
            },
            "authority": {
                "ranking_input_authorized": False, "candidate_ranking_authorized": False,
                "stage_promotion_authorized": False, "production_authorized": False,
                "trading_authorized": False,
            },
        }
        row = MODULE.build_korea_rotation(
            "2026-08-21", snapshot={"kind": "payload", "value": payload}
        )
        self.assertEqual(row["status"], "POLICY_BLOCKED")
        self.assertIn("KOREA_BREADTH_BLOCKED", row["reason"])
        self.assertTrue(row["validated"])
        self.assertEqual(row["packet"]["breadth_status"], "BLOCKED")
        self.assertFalse(row["packet"]["breadth_decision_eligible"])
        self.assertEqual(row["packet"]["breadth_markets"]["KOSPI"]["lineage_sha256"], "b" * 64)
        # Never relabeled NEUTRAL/AVAILABLE/PASS.
        self.assertNotEqual(row["packet"]["breadth_status"], "AVAILABLE")
        for value in row["authority"].values():
            self.assertFalse(value)

    def test_korea_rotation_available_breadth_is_ready(self):
        payload = {
            "as_of_date": DECISION_DATE.replace("2026-08-21", "2026-08-21"),
            "generated_at": f"{DECISION_DATE}T10:00:00Z",
            "run_status": "OK",
            "rotation": {"status": "ROTATION_BUCKETS_OBSERVED", "rotation_policy_effective": True, "packet_sha256": "e" * 64},
            "breadth": {
                "status": "AVAILABLE", "reason": "KOSDAQ_AVAILABLE,KOSPI_AVAILABLE",
                "decision_eligible": True, "ranking_input_authorized": False,
                "markets": {
                    "KOSPI": {"lineage_sha256": "f" * 64, "as_of_date": DECISION_DATE, "available_at": f"{DECISION_DATE}T09:00:00Z"},
                    "KOSDAQ": {"lineage_sha256": "0" * 64, "as_of_date": DECISION_DATE, "available_at": f"{DECISION_DATE}T09:00:00Z"},
                },
                "source_context_path": "data/observations/korea_breadth_context/2026-08-21/packet.json",
                "source_context_sha256": "1" * 64,
            },
            "authority": {
                "ranking_input_authorized": False, "candidate_ranking_authorized": False,
                "stage_promotion_authorized": False, "production_authorized": False,
                "trading_authorized": False,
            },
        }
        row = MODULE.build_korea_rotation(
            DECISION_DATE, snapshot={"kind": "payload", "value": payload}
        )
        self.assertEqual(row["status"], "READY")
        self.assertIsNone(row["reason"])

    def test_korea_rotation_run_status_failed_is_degraded(self):
        payload = {
            "as_of_date": DECISION_DATE, "generated_at": f"{DECISION_DATE}T10:00:00Z",
            "run_status": "FAILED",
            "rotation": {}, "breadth": {"status": "UNKNOWN"},
            "authority": {},
        }
        row = MODULE.build_korea_rotation(
            DECISION_DATE, snapshot={"kind": "payload", "value": payload}
        )
        self.assertEqual(row["status"], "DEGRADED")
        self.assertEqual(row["reason"], "run_status=FAILED")

    def test_free_market_v3_keeps_fred_visible_when_alpaca_is_blocked(self):
        authority = {
            "evidence_capture_only": True,
            "us_breadth_authorized": False,
            "market_wide_price_authorized": False,
            "entry_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "broker_submission_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
        payload = {
            "schema_version": "free_market_data_capture/3",
            "contract_version": "free_market_data/1",
            "observed_at_utc": "2026-08-26T00:35:00Z",
            "packet_sha256": "a" * 64,
            "fred": {
                "status": "READY", "series_id": "VIXCLS",
                "observation_date": "2026-08-25", "value": "15.5",
                "response_sha256": "b" * 64,
                "raw_retention": "TRANSIENT_NOT_PERSISTED",
            },
            "alpaca": {
                "status": "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL",
                "feed": "iex", "source_scope": "IEX_ONLY_PARTIAL_US_MARKET",
                "bars": [], "daily_bars": [], "raw_sha256": None,
                "daily_raw_sha256": None,
            },
            "authority": authority,
        }
        row = MODULE.build_free_market_data({"kind": "ready", "value": payload})
        self.assertEqual(row["status"], "DEGRADED")
        self.assertEqual(row["reason"], "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL")
        self.assertTrue(row["validated"])
        self.assertEqual(row["packet"]["vixcls"], {"date": "2026-08-25", "value": "15.5"})
        self.assertEqual(row["packet"]["alpaca_iex_bars"], [])
        self.assertFalse(row["authority"]["trading_authorized"])

    def test_free_market_v3_rejects_fred_raw_retention_drift(self):
        current = json.loads((ROOT / "data" / "latest_free_market_data.json").read_text())
        current["schema_version"] = "free_market_data_capture/3"
        current["fred"]["status"] = "READY"
        current["fred"]["response_sha256"] = "b" * 64
        current["fred"]["raw_retention"] = "PERSISTED"
        current["alpaca"]["status"] = "READY"
        current["alpaca"]["daily_bars"] = [{"symbol": "MSFT"}]
        row = MODULE.build_free_market_data({"kind": "ready", "value": current})
        self.assertEqual(row["status"], "DEGRADED")
        self.assertEqual(row["reason"], "FRED_DERIVED_CONTRACT_INVALID")

    def test_free_market_v3_requires_same_kst_capture_date(self):
        # This is a v3 freshness-contract test, so it must not inherit the
        # repository's moving latest pointer (now v4 with append-only FRED
        # provenance).  Otherwise an unrelated current-evidence problem can
        # make the supposedly "fresh" v3 control case DEGRADED before the
        # date boundary is what gets tested.
        current = {
            "schema_version": "free_market_data_capture/3",
            "contract_version": "free_market_data/1",
            "observed_at_utc": "2026-08-26T11:32:34Z",
            "packet_sha256": "a" * 64,
            "fred": {
                "status": "READY",
                "series_id": "VIXCLS",
                "observation_date": "2026-08-25",
                "value": "15.5",
                "response_sha256": "b" * 64,
                "raw_retention": "TRANSIENT_NOT_PERSISTED",
            },
            "alpaca": {
                "status": "READY",
                "feed": "iex",
                "source_scope": "IEX_ONLY_PARTIAL_US_MARKET",
                "bars": [{"symbol": "MSFT"}],
                "daily_bars": [{"symbol": "MSFT"}],
                "raw_sha256": "c" * 64,
                "daily_raw_sha256": "d" * 64,
            },
            "authority": {
                "evidence_capture_only": True,
                "us_breadth_authorized": False,
                "market_wide_price_authorized": False,
                "entry_authorized": False,
                "action_authorized": False,
                "order_authorized": False,
                "broker_submission_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
            },
        }
        fresh = MODULE.build_free_market_data(
            {"kind": "ready", "value": current}, decision_date="2026-08-26"
        )
        stale = MODULE.build_free_market_data(
            {"kind": "ready", "value": current}, decision_date="2026-08-27"
        )
        future = MODULE.build_free_market_data(
            {"kind": "ready", "value": current}, decision_date="2026-08-25"
        )
        self.assertEqual(fresh["status"], "READY")
        self.assertEqual(stale["status"], "DATA_BLOCKED")
        self.assertEqual(stale["reason"], "CAPTURE_STALE_FOR_DECISION_DATE")
        self.assertEqual(future["status"], "DATA_BLOCKED")
        self.assertEqual(future["reason"], "CAPTURE_FUTURE_FOR_DECISION_DATE")

    def test_free_market_v3_invalid_capture_time_is_degraded(self):
        current = json.loads((ROOT / "data" / "latest_free_market_data.json").read_text())
        current["observed_at_utc"] = "not-a-timestamp"
        row = MODULE.build_free_market_data(
            {"kind": "ready", "value": current}, decision_date="2026-08-26"
        )
        self.assertEqual(row["status"], "DEGRADED")
        self.assertEqual(row["reason"], "CAPTURE_TIME_INVALID")

    def test_free_market_legacy_packet_rebuild_keeps_pre_v3_semantics(self):
        legacy = json.loads(
            (ROOT / "evidence/free_market_data/raw/2026-08-22/manifest.json").read_text()
        )
        row = MODULE.build_free_market_data(
            {"kind": "ready", "value": legacy}, decision_date="2026-08-26"
        )
        self.assertEqual(row["status"], "READY")
        self.assertIsNone(row["reason"])

    def test_workflow_does_not_duplicate_or_alter_existing_collector_schedules(self):
        # The daily briefing workflow must never re-fetch anything the
        # collectors already fetched -- it is a separate, later, read-only
        # aggregation step over what they already committed.
        command = "\n".join(
            step.get("run", "") for step in WF["jobs"]["briefing"]["steps"]
        )
        for forbidden in ("curl ", "collectors/", ".github/scripts/build_briefing_inputs.py"):
            self.assertNotIn(forbidden, command)
        collect_yml = (ROOT / ".github" / "workflows" / "collect.yml").read_text(
            encoding="utf-8"
        )
        krx_post_close_yml = (
            ROOT / ".github" / "workflows" / "krx-post-close.yml"
        ).read_text(encoding="utf-8")
        # This test file's own existence and content must not have altered
        # either sibling collector workflow's schedule.
        self.assertIn("cron: '55 20 * * 0-4'", collect_yml)
        self.assertIn("cron: '5 7 * * 1-5'", krx_post_close_yml)


class DynamicClockRenderCapTest(unittest.TestCase):
    """P8-10 <-> P8-12 integration (2026-08-23 locked spec): the rendered
    markdown must never re-flood the briefing even though
    build_briefing_section()'s underlying JSON keeps every WATCH_REVIEW
    candidate in full -- only this ONE presentation layer caps what gets
    printed inline."""

    def test_dynamic_clock_section_is_rendered_and_capped(self):
        # Real current evidence -- CRYPTO alone has dozens of WATCH_REVIEW
        # candidates; the rendered markdown must never enumerate all of
        # them inline. Uses the Dynamic Clock module's own real
        # report_asof_evidence_date as decision_date so DYNAMIC_CLOCK
        # itself resolves READY rather than DATA_BLOCKED (its own
        # as_of_date must not be after the packet's decision_date).
        dc_report = MODULE.DYNAMIC_CLOCK.run()
        decision_date = dc_report["report_asof_evidence_date"]
        packet = MODULE.build_packet("morning", decision_date, f"{decision_date}T12:00:00Z")
        md = MODULE.render_markdown(packet)
        self.assertIn("Dynamic Clock", md)
        lines = md.splitlines()
        start = next(i for i, ln in enumerate(lines) if "Dynamic Clock" in ln)
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines)
        )
        section = lines[start:end]
        crypto_candidate_lines = [
            ln for ln in section if ln.strip().startswith(("- IMMEDIATE_REVIEW", "- WATCH_REVIEW"))
        ]
        # 15-per-tier-per-market cap -- BTC(<=15) + KOREA(<=15) +
        # CRYPTO(<=15+15) is a safe generous ceiling regardless of exactly
        # how many real candidates each market has today.
        self.assertLessEqual(len(crypto_candidate_lines), 60)


class ShadowEntryReviewBriefingTests(unittest.TestCase):
    def setUp(self):
        MODULE._SHADOW_REVIEW_VALIDATION_CACHE.clear()

    def tearDown(self):
        MODULE._SHADOW_REVIEW_VALIDATION_CACHE.clear()

    def _source_decision_date(self):
        return MODULE._read_json(
            MODULE.ROOT / MODULE._SHADOW_REVIEW_PACKET_PATH
        )["decision_date"]

    def _row(self, decision_date=None):
        decision_date = decision_date or self._source_decision_date()
        return MODULE.build_shadow_entry_review_status(
            decision_date, "evening", f"{decision_date}T23:45:00+09:00"
        )

    def test_sample_label_matches_exact_review_trigger_and_exposes_zero_capital_items(self):
        row = self._row()
        self.assertEqual(row["status"], "READY")
        packet = row["packet"]
        source = MODULE._read_json(MODULE.ROOT / MODULE._SHADOW_REVIEW_PACKET_PATH)
        expected_sample_status = {
            "UPSTREAM_WORKFLOW_RUN": "NATURAL_OPERATIONAL_SAMPLE",
            "MANUAL_WORKFLOW_DISPATCH": "MANUAL_DIAGNOSTIC_SAMPLE",
            "LOCAL_REPRODUCTION": "LOCAL_REPRODUCTION_ONLY",
        }[source["source"]["trigger_kind"]]
        self.assertEqual(packet["sample_status"], expected_sample_status)
        if source["source"]["trigger_kind"] != "UPSTREAM_WORKFLOW_RUN":
            self.assertNotEqual(packet["sample_status"], "NATURAL_OPERATIONAL_SAMPLE")
        self.assertEqual(packet["summary"]["candidate_count"], len(source["review_items"]))
        retained_source = [
            item for item in source["review_items"]
            if item["p8_13_review_surface"] == "ZERO_CAPITAL_HUMAN_REVIEW_ITEM"
        ]
        retained = [{
            "subject": item["subject"],
            "market": item["market"],
            "canonical_instrument_id": item["canonical_instrument_id"],
            "identity_status": item["identity_status"],
            "trigger_types": item["trigger_types"],
            "confirmation_count": item["confirmation_count"],
            "decision_at": item["decision_at"],
            "next_review_at": item["next_review_at"],
            "review_due_status": MODULE._review_due_status(
                item["next_review_at"], source["decision_date"]
            ),
            "price_state": item["price_state"],
            "reflection_status": item["reflection_status"],
            "review_state": item["review_state"],
            "participation_state": item["participation_state"],
            "review_reason": item["review_reason"],
            "p8_13_review_surface": item["p8_13_review_surface"],
            "money_boundary": item["money_boundary"],
        } for item in retained_source]
        self.assertEqual(packet["review_items"], retained)
        self.assertEqual(
            packet["summary"]["zero_capital_review_item_count"], len(retained)
        )
        # This is a rolling natural sample.  Zero qualifying review surfaces
        # is a valid, truthful state; the contract under test is exact
        # retention and count agreement, not forced candidate generation.
        if not retained_source:
            self.assertEqual(packet["review_items"], [])

    def test_each_review_trigger_has_an_independently_exercised_exact_label(self):
        validated = MODULE._validated_shadow_review_source()["packet"]
        cases = (
            ("UPSTREAM_WORKFLOW_RUN", "NATURAL_OPERATIONAL_SAMPLE"),
            ("MANUAL_WORKFLOW_DISPATCH", "MANUAL_DIAGNOSTIC_SAMPLE"),
            ("LOCAL_REPRODUCTION", "LOCAL_REPRODUCTION_ONLY"),
        )
        for trigger_kind, expected_sample_status in cases:
            with self.subTest(trigger_kind=trigger_kind), mock.patch.object(
                MODULE,
                "_validated_shadow_review_source",
                return_value={
                    "packet": copy.deepcopy(validated),
                    "trigger_kind": trigger_kind,
                },
            ):
                packet = self._row(validated["decision_date"])["packet"]
                self.assertEqual(packet["sample_status"], expected_sample_status)
                if trigger_kind != "UPSTREAM_WORKFLOW_RUN":
                    self.assertNotEqual(
                        packet["sample_status"], "NATURAL_OPERATIONAL_SAMPLE"
                    )

    def test_unknown_review_trigger_fails_closed(self):
        validated = MODULE._validated_shadow_review_source()["packet"]
        with mock.patch.object(
            MODULE,
            "_validated_shadow_review_source",
            return_value={
                "packet": copy.deepcopy(validated),
                "trigger_kind": "UNKNOWN_TRIGGER",
            },
        ), self.assertRaisesRegex(
            MODULE.DailyOrchestratorError,
            "SHADOW_ENTRY_REVIEW_TRIGGER_KIND_INVALID",
        ):
            self._row(validated["decision_date"])

    def test_every_retained_item_and_component_keep_money_authority_at_zero(self):
        packet = self._row()["packet"]
        self.assertEqual(packet["authority"]["capital"], 0)
        self.assertIsNone(packet["authority"]["trade_proposal"])
        for key in (
            "stage_promotion_authority", "buy_authority", "action_authority",
            "order_authority", "production_authority", "trading_authority",
        ):
            self.assertFalse(packet["authority"][key])
        for item in packet["review_items"]:
            money = item["money_boundary"]
            self.assertEqual(money["capital"], 0)
            self.assertIsNone(money["trade_proposal"])
            self.assertIsNone(money["quantity"])
            self.assertIsNone(money["entry_zone"])
            self.assertIsNone(money["invalidation"])
        self.assertFalse(MODULE._contains_shadow_review_post_hoc_key(packet))

    def test_other_decision_date_is_fail_closed_not_reused_as_current(self):
        source_date = dt.date.fromisoformat(self._source_decision_date())
        row = self._row((source_date - dt.timedelta(days=1)).isoformat())
        self.assertEqual(row["status"], "DATA_BLOCKED")
        self.assertEqual(row["reason"], "SHADOW_ENTRY_REVIEW_DECISION_DATE_MISMATCH")
        self.assertIsNone(row["packet"])
        self.assertFalse(row["decision_eligible"])

    def test_resigned_review_state_tamper_is_rejected_by_production_validator(self):
        original_read = MODULE._read_json
        shadow_path = MODULE.ROOT / MODULE._SHADOW_REVIEW_PACKET_PATH
        tampered = copy.deepcopy(original_read(shadow_path))
        self.assertTrue(
            tampered["review_items"],
            "committed shadow-review fixture must contain a row for tamper testing",
        )
        target = min(
            tampered["review_items"],
            key=lambda item: (
                item["market"],
                item["subject"],
                item["candidate_id"],
            ),
        )
        target["review_state"] = (
            "WATCH_REVIEW"
            if target["review_state"] == "MOMENTUM_PROBE_REVIEW"
            else "MOMENTUM_PROBE_REVIEW"
        )
        target["row_sha256"] = MODULE.SHADOW_ENTRY_REVIEW.payload_sha256(
            {key: value for key, value in target.items() if key != "row_sha256"}
        )
        tampered["packet_sha256"] = MODULE.SHADOW_ENTRY_REVIEW.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )

        def read_with_tamper(path):
            if Path(path) == shadow_path:
                return copy.deepcopy(tampered)
            return original_read(path)

        with mock.patch.object(MODULE, "_read_json", side_effect=read_with_tamper):
            row = self._row()
        self.assertEqual(row["status"], "DEGRADED")
        self.assertIn("SHADOW_ENTRY_REVIEW_SEMANTIC_TAMPER_OR_DRIFT", row["reason"])

    def test_markdown_says_why_now_and_why_not_without_buy_language(self):
        row = self._row()
        decision_date = self._source_decision_date()
        packet = MODULE.build_packet(
            "evening", decision_date, f"{decision_date}T23:59:59Z"
        )
        packet["components"] = [
            row if item["component_id"] == "SHADOW_ENTRY_REVIEW" else item
            for item in packet["components"]
        ]
        packet["component_status_counts"] = {
            status: sum(item["status"] == status for item in packet["components"])
            for status in MODULE.STATUS_VALUES
        }
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        rendered = MODULE.render_markdown(packet)
        self.assertIn("Zero-capital human review", rendered)
        for item in row["packet"]["review_items"]:
            self.assertIn(
                f"{item['subject']} ({item['market']}): "
                f"review_state={item['review_state']}",
                rendered,
            )
        if row["packet"]["review_items"]:
            self.assertIn("capital=0 trade_proposal=null", rendered)
        else:
            self.assertIn("zero_capital_review_items=0", rendered)
        self.assertIn("ENTRY_POLICY_UNRATIFIED", rendered)
        self.assertNotIn("forward_return", rendered.lower())
        self.assertNotIn("mfe", rendered.lower())
        self.assertNotIn("mae", rendered.lower())


class DartObservationBriefingIntegrationTests(unittest.TestCase):
    def test_real_dart_observations_render_as_evidence_not_a_recommendation(self):
        paths = sorted(
            (ROOT / "data/observations/dart_event_observations").glob("*/*.json")
        )
        self.assertTrue(paths)
        source = max(
            (json.loads(path.read_text(encoding="utf-8")) for path in paths),
            key=lambda packet: dt.datetime.fromisoformat(
                packet["decision_at"].replace("Z", "+00:00")
            ),
        )
        generated_at = (
            dt.datetime.fromisoformat(source["decision_at"].replace("Z", "+00:00"))
            + dt.timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        row = MODULE.build_rotation_discovery("evening", generated_at)
        self.assertEqual(row["status"], "PENDING")
        dart = row["packet"]["dart_observations"]
        expected_summary = source["summary"]
        self.assertEqual(
            dart["observation_count"], expected_summary["relevant_filing_count"]
        )
        self.assertEqual(
            dart["source_failed_count"], expected_summary.get("source_failed_count", 0)
        )
        self.assertEqual(
            dart["content_failure_count"], expected_summary.get("content_failure_count", 0)
        )
        self.assertEqual(
            row["reason"],
            "DART_OBSERVATIONS_PRESENT_WITH_PARTIAL_FAILURES_ESCALATION_BLOCKED"
            if dart["source_failed_count"] or dart["content_failure_count"]
            else "DART_OBSERVATIONS_PRESENT_ESCALATION_BLOCKED",
        )
        self.assertEqual(row["authority"]["stage_promotion_authorized"], False)
        self.assertEqual(row["authority"]["action_generation_authorized"], False)
        self.assertEqual(row["authority"]["trading_authorized"], False)

        # Keep this integration assertion bound to the already-validated DART
        # row above.  Other rolling Dynamic Clock evidence can legitimately
        # advance between captures and make a fresh rotation rebuild fail
        # closed for unrelated temporal reasons.
        with mock.patch.object(
            MODULE, "build_rotation_discovery", return_value=copy.deepcopy(row)
        ):
            packet = MODULE.build_packet(
                "evening", source["source_date"], generated_at
            )
        rendered = MODULE.render_markdown(packet)
        if dart["observation_count"] or dart["source_failed_count"] or dart["content_failure_count"]:
            self.assertIn(f"DART observations={dart['observation_count']}", rendered)
            self.assertIn(
                f"source_failed={dart['source_failed_count']} "
                f"content_failed={dart['content_failure_count']}",
                rendered,
            )
            self.assertIn("event_type=UNRATIFIED importance=UNRATIFIED", rendered)
        for observation in dart["observations"][:10]:
            self.assertIn(observation["subject_name"], rendered)
            self.assertIn("action=null", rendered)

    def test_zero_observation_partial_failure_is_not_hidden(self):
        packet = {
            "summary": {
                "rotation_change_count": 0,
                "discovery_case_count": 0,
                "new_candidate_count": 0,
                "existing_candidate_change_count": 0,
                "signal_observation_count": 0,
                "dart_observation_count": 0,
                "ready_count": 0,
                "entry_trigger_count": 0,
            },
            "dart_observations": {
                "observation_count": 0,
                "raw_bytes_verified_count": 0,
                "metadata_only_count": 0,
                "source_failed_count": 1,
                "content_failure_count": 0,
                "observations": [],
            },
            "signal_observations": {},
            "wildcard_observations": {},
        }
        lines = MODULE._format_component_detail({
            "component_id": "ROTATION_DISCOVERY", "packet": packet,
        })
        rendered = "\n".join(lines)
        self.assertIn("DART observations=0", rendered)
        self.assertIn("source_failed=1 content_failed=0", rendered)


if __name__ == "__main__":
    unittest.main()
