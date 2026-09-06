#!/usr/bin/env python3
"""P8 Atlas Daily Briefing Integration v1 regression.

Builds and publishes only against real, already-committed evidence and
already-existing production builders; introduces no synthetic sensor data.
Focuses on: honest component status classification, failure isolation,
determinism, tamper/mismatch fail-closed behaviour, atomic append-only
publication, and the false authority boundary.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import lzma
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


def _natural_morning_generated_at(decision_date: str) -> str:
    """The generated_at a real *scheduled* morning run produces for
    ``decision_date``, derived from the workflow's own morning cron rather
    than hardcoded.

    MORNING_GENERATED_AT above, and every fixture pair the Capital Action
    tests share, place generated_at on the same UTC calendar day as
    decision_date. A real scheduled morning run does not: the morning cron
    fires at 22:05Z, which is 07:05 the NEXT KST day, so the packet's own
    UTC day is one day BEFORE the KST business date it is deciding for.
    A component row labelled with generated_at[:10] instead of the KST
    decision date therefore disagrees with its siblings under that geometry
    while agreeing with them under a same-UTC-day one -- which is why this
    family of date-basis defects survives a fixture that only ever decides
    for its own UTC day. (test_weekend_morning_discloses_closed_session_
    without_date_relabelling does build a previous-UTC-day morning packet,
    but asserts weekend session disclosure, not component date agreement.)

    The morning cron is identified structurally (the scheduled instant whose
    KST date is the next day), not by matching a literal, so this stays
    honest if the schedule is retimed; test_workflow_derives_slot_from_
    exact_cron_not_wall_clock_hour separately pins the exact expressions.
    """
    schedule = WF.get("on", WF.get(True))["schedule"]
    next_kst_day_crons = []
    for item in schedule:
        minute, hour = item["cron"].split()[:2]
        fired = dt.datetime(
            2026, 1, 1, int(hour), int(minute), tzinfo=dt.timezone.utc
        )
        if fired.astimezone(MODULE.KST).date() > fired.date():
            next_kst_day_crons.append((int(hour), int(minute)))
    if len(next_kst_day_crons) != 1:
        raise AssertionError(
            f"expected exactly one next-KST-day cron, got {next_kst_day_crons}"
        )
    hour, minute = next_kst_day_crons[0]
    fires_on = dt.date.fromisoformat(decision_date) - dt.timedelta(days=1)
    return f"{fires_on.isoformat()}T{hour:02d}:{minute:02d}:00Z"


NATURAL_MORNING_GENERATED_AT = _natural_morning_generated_at(DECISION_DATE)
CAPITAL_ACTION_COMPONENTS = (
    "DEFENSIVE_ACTION_DECISION",
    "STRATEGIC_CAPITAL_POSTURE",
    "ACTION_RISK_PORTFOLIO_SUMMARY",
)


def _default_frozen_source_keys() -> set:
    """Every frozen_sources key a DEFAULT build carries.

    The per-component input snapshots, plus the version-bound inputs the
    current default derivation binds (see VERSIONED_FROZEN_INPUTS). Optional
    caller-supplied inputs are absent unless a caller passes them.
    """
    return set(MODULE.FROZEN_SOURCE_COMPONENTS) | set(MODULE.VERSIONED_FROZEN_INPUTS)


def _capital_action_section(packet: dict) -> dict:
    """The Flow-First CAPITAL_ACTION section built from a real daily packet.

    Goes through the production presentation path (the same
    FLOW_FIRST_BRIEFING.build_packet render_markdown() calls), so the
    section aggregator's fail-closed date behaviour is exercised for real
    rather than re-implemented here.
    """
    flow_first = MODULE.FLOW_FIRST_BRIEFING.build_packet(packet)
    sections = {row["section_id"]: row for row in flow_first["sections"]}
    return sections["CAPITAL_ACTION"]


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

    def test_free_market_row_binds_exact_pointer_file_bytes(self):
        relative_path = "data/latest_free_market_data.json"
        snapshot = MODULE._fetch_free_market_data_snapshot()
        expected = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        observed_at = snapshot["value"]["observed_at_utc"]
        normalized = observed_at[:-1] + "+00:00" if observed_at.endswith("Z") else observed_at
        decision_date = (
            dt.datetime.fromisoformat(normalized).astimezone(MODULE.KST).date().isoformat()
        )
        row = MODULE._classify_free_market_data(snapshot, decision_date)
        self.assertEqual(snapshot["content_sha256"], expected)
        self.assertEqual(row["source_packet_path"], relative_path)
        self.assertEqual(row["source_packet_sha256"], expected)
        self.assertNotEqual(expected, snapshot["value"].get("packet_sha256"))

    def test_korea_market_signals_row_binds_exact_dated_packet_bytes(self):
        snapshot = MODULE._fetch_korea_market_signals_snapshot()
        as_of_date = snapshot["value"]["as_of_date"]
        relative_path = (
            f"data/observations/korea_market_signals/{as_of_date}/packet.json"
        )
        expected = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        row = MODULE._classify_korea_market_signals("9999-12-31", snapshot)
        self.assertEqual(snapshot["content_sha256"], expected)
        self.assertEqual(row["source_packet_path"], relative_path)
        self.assertEqual(row["source_packet_sha256"], expected)
        self.assertNotEqual(expected, snapshot["value"].get("payload_sha256"))

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

    def test_natural_morning_capital_action_rows_share_the_kst_business_date(self):
        # A genuine 22:05Z scheduled morning run, whose UTC calendar day is
        # the day BEFORE the KST decision date. Pinned first, so this test
        # cannot silently decay into another same-UTC-day build if the
        # schedule or the fixture date ever moves.
        self.assertEqual(
            NATURAL_MORNING_GENERATED_AT[:10],
            (dt.date.fromisoformat(DECISION_DATE) - dt.timedelta(days=1)).isoformat(),
        )
        self.assertNotEqual(NATURAL_MORNING_GENERATED_AT[:10], DECISION_DATE)

        packet = MODULE.build_packet(
            "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT
        )
        by_id = {row["component_id"]: row for row in packet["components"]}

        # All three Capital Action components must build under this geometry
        # -- if any of them degraded, the date agreement below would be
        # vacuously true (a DEGRADED row carries as_of_date None).
        for component_id in CAPITAL_ACTION_COMPONENTS:
            row = by_id[component_id]
            self.assertEqual(row["status"], "PENDING", component_id)
            self.assertTrue(row["validated"], component_id)
            self.assertEqual(row["as_of_date"], DECISION_DATE, component_id)

        # The summary row's label is the KST business date its own already
        # validated packet reports for, not the packet's UTC invocation day.
        summary = by_id["ACTION_RISK_PORTFOLIO_SUMMARY"]
        self.assertEqual(summary["packet"]["decision_date"], DECISION_DATE)
        self.assertNotEqual(
            summary["as_of_date"], summary["generated_at"][:10]
        )

        section = _capital_action_section(packet)
        self.assertEqual(
            [row["component_id"] for row in section["source_components"]],
            list(CAPITAL_ACTION_COMPONENTS),
        )
        # One agreed date means the aggregator has nothing to fail closed
        # on, so the section reaches its honest not-yet-ready state rather
        # than the DATA_BLOCKED / SOURCE_AS_OF_DATE_MISMATCH escalation a
        # divergent row would force (proved separately below).
        self.assertEqual(section["status"], "PENDING")
        self.assertEqual(section["as_of_date"], DECISION_DATE)
        self.assertEqual(section["unknown_reason"], "SOURCE_COMPONENT_NOT_READY")

        # Agreeing on a date grants nothing. PENDING here is still an
        # honest "not ready", never a promotion.
        self.assertFalse(section["decision_eligible"])
        self.assertFalse(section["action_eligible"])
        self.assertFalse(section["order_eligible"])
        self.assertEqual(
            summary["packet"]["status"],
            "ACTION_RISK_PORTFOLIO_PRESENTED_NO_ACTION_AUTHORITY",
        )
        self.assertTrue(
            all(row["action"] is None for row in summary["packet"]["actions"])
        )
        self.assertEqual(
            by_id["DEFENSIVE_ACTION_DECISION"]["packet"]["status"],
            "DEFENSIVE_ACTION_READINESS_BLOCKED",
        )
        self.assertEqual(
            by_id["STRATEGIC_CAPITAL_POSTURE"]["packet"]["status"],
            "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED",
        )

    def test_evening_summary_row_date_is_unchanged_by_the_kst_basis(self):
        # The evening cron fires at 09:30Z = 18:30 the SAME KST day, so the
        # UTC calendar day and the KST business date coincide and the two
        # candidate bases are indistinguishable. Pinning that here is what
        # makes this a preservation proof rather than a restatement of the
        # morning test: for this input the row keeps exactly the value the
        # generated_at[:10] basis produced.
        self.assertEqual(EVENING_GENERATED_AT[:10], DECISION_DATE)

        packet = MODULE.build_packet(
            "evening", DECISION_DATE, EVENING_GENERATED_AT
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        summary = by_id["ACTION_RISK_PORTFOLIO_SUMMARY"]
        self.assertEqual(summary["status"], "PENDING")
        self.assertEqual(summary["as_of_date"], EVENING_GENERATED_AT[:10])
        self.assertEqual(summary["as_of_date"], DECISION_DATE)

        section = _capital_action_section(packet)
        self.assertEqual(section["status"], "PENDING")
        self.assertEqual(section["as_of_date"], DECISION_DATE)
        self.assertEqual(section["unknown_reason"], "SOURCE_COMPONENT_NOT_READY")

    def test_a_genuinely_mismatched_capital_action_date_is_still_blocked(self):
        # Labelling the summary row consistently must not be mistaken for
        # relaxing the aggregator. Re-introduce exactly the divergence the
        # generated_at[:10] basis produced -- one component reporting for a
        # different business date than its siblings -- and the unchanged
        # fail-closed guard must still refuse to present the section.
        packet = MODULE.build_packet(
            "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT
        )
        tampered = copy.deepcopy(packet)
        for row in tampered["components"]:
            if row["component_id"] == "ACTION_RISK_PORTFOLIO_SUMMARY":
                row["as_of_date"] = NATURAL_MORNING_GENERATED_AT[:10]
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("packet_sha256")
        tampered["packet_sha256"] = MODULE.payload_sha256(unsigned)

        section = _capital_action_section(tampered)
        self.assertEqual(section["status"], "DATA_BLOCKED")
        self.assertEqual(section["unknown_reason"], "SOURCE_AS_OF_DATE_MISMATCH")
        self.assertIsNone(section["as_of_date"])
        self.assertFalse(section["decision_eligible"])
        self.assertFalse(section["action_eligible"])
        self.assertFalse(section["order_eligible"])

    def test_p1_regime_slot_carries_exact_runtime_blockers_not_a_placeholder(self):
        packet = MODULE.build_packet(
            "morning", DECISION_DATE, MORNING_GENERATED_AT
        )
        by_id = {row["component_id"]: row for row in packet["components"]}
        defensive = by_id["DEFENSIVE_ACTION_DECISION"]["packet"]
        reasons = defensive["unavailable_reasons"]["P1_REGIME_DECISION"]

        # The runtime readiness boundary actually ran over this build's own
        # regime_output/v1 envelopes: the opaque placeholder is gone and the
        # exact upstream gaps are named.
        self.assertNotIn(
            "P1_REGIME_DECISION_PRODUCTION_CONTRACT_UNAVAILABLE", reasons
        )
        self.assertIn("P1_REGIME_DECISION_NOT_RUNTIME_WIRED", reasons)
        self.assertIn(
            "COMMON_V1_REPLAY_MODE:SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED",
            reasons,
        )
        for market in ("US", "KR", "CRYPTO"):
            self.assertIn(
                f"SIGNED_NORMALIZATION_POLICY_UNRATIFIED:{market}", reasons
            )
            self.assertIn(f"PIT_REPLAY_NOT_ACCEPTED:{market}", reasons)
        authority_contract = MODULE.RUNTIME_REGIME_READINESS.AUTHORITY.load_contract()
        for component in authority_contract["required_policy_components"]:
            self.assertIn(f"REGIME_POLICY_COMPONENT_MISSING:{component}", reasons)
        # No invocation-time-tainted hash may leak into this list: the
        # component's semantic fingerprint is computed over it, so a
        # generated_at-derived value here would force a spurious new
        # revision on every same-day rebuild.
        self.assertFalse(any("SHA256" in reason for reason in reasons), reasons)

        # Naming the blockers grants nothing.
        rows = {row["name"]: row for row in defensive["sources"]}
        self.assertEqual(rows["P1_REGIME_DECISION"]["availability"], "UNAVAILABLE")
        self.assertEqual(defensive["decision_status"], "BLOCKED")
        self.assertIsNone(defensive["selected_action"])
        self.assertEqual(defensive["order_intents"], [])
        self.assertIn(
            "P1_REGIME_DECISION_UNAVAILABLE", defensive["unresolved_boundaries"]
        )

    def test_pre_wiring_packet_rebuild_and_version_tamper(self):
        legacy = MODULE.build_packet(
            "morning", DECISION_DATE, MORNING_GENERATED_AT,
            runtime_regime_readiness_version=None,
        )
        self.assertNotIn("runtime_regime_readiness_version", legacy)
        self.assertEqual(MODULE.validate_packet(legacy), legacy)
        upgraded = copy.deepcopy(legacy)
        upgraded["runtime_regime_readiness_version"] = 1
        unsigned = copy.deepcopy(upgraded)
        unsigned.pop("packet_sha256")
        upgraded["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaises(MODULE.DailyOrchestratorError):
            MODULE.validate_packet(upgraded)
        with self.assertRaises(MODULE.DailyOrchestratorError):
            MODULE.build_packet(
                "morning", DECISION_DATE, MORNING_GENERATED_AT,
                runtime_regime_readiness_version=True,
            )

    def test_p1_regime_blocker_derivation_falls_back_instead_of_failing(self):
        # A failure keeps the slot unavailable and preserves the exact code.
        self.assertIsNone(
            MODULE.build_p1_regime_unavailable_reasons(None, MORNING_GENERATED_AT)
        )
        self.assertEqual(
            MODULE.build_p1_regime_unavailable_reasons(
                {"US": {}, "KR": {}, "CRYPTO": {}}, MORNING_GENERATED_AT
            ),
            ["P1_REGIME_DECISION_PRODUCTION_CONTRACT_UNAVAILABLE",
             "P1_REGIME_READINESS_INVALID:REGIME_OUTPUT_INVALID"],
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

    def test_later_same_decision_date_btc_capture_cannot_change_persisted_validation(
        self,
    ):
        latest = MODULE.DYNAMIC_CLOCK.run()
        decision_date = latest["report_asof_evidence_date"]
        publication_report = MODULE.DYNAMIC_CLOCK.run(decision_date=decision_date)
        later_btc_report = copy.deepcopy(publication_report)
        later_btc_report["by_market"]["BTC"]["raw_trigger_count"] += 1
        self.assertEqual(later_btc_report["decision_date"], decision_date)
        self.assertNotEqual(
            MODULE.payload_sha256(publication_report),
            MODULE.payload_sha256(later_btc_report),
        )

        # The first call is the publication-time input set.  The second
        # side-effect represents a later BTC capture on the same decision
        # date. Validation must not invoke run() again or observe it.
        with mock.patch.object(
            MODULE.DYNAMIC_CLOCK,
            "run",
            side_effect=[publication_report, later_btc_report],
        ) as dynamic_run:
            packet = MODULE.build_packet(
                "morning", decision_date, f"{decision_date}T14:59:00Z"
            )
            self.assertEqual(dynamic_run.call_count, 1)
            persisted = copy.deepcopy(packet)
            self.assertEqual(MODULE.validate_packet(persisted), packet)
            self.assertEqual(dynamic_run.call_count, 1)

        source = packet["frozen_sources"]["DYNAMIC_CLOCK"]
        self.assertEqual(source["kind"], "report")
        self.assertEqual(source["report"], publication_report)
        self.assertEqual(
            source["report_sha256"], MODULE.payload_sha256(publication_report)
        )
        dynamic_row = next(
            row for row in packet["components"]
            if row["component_id"] == "DYNAMIC_CLOCK"
        )
        for authority_key in (
            "stage_promotion_authorized",
            "candidate_ready_buy_promotion_authorized",
            "portfolio_decision_authorized",
            "trade_proposal_authorized",
            "capital_authorized",
            "action_authorized",
            "order_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertIs(dynamic_row["authority"][authority_key], False)

        # Rehashing the outer packet cannot hide a mismatch between the
        # frozen source bytes and their publication-time identity.
        tampered = copy.deepcopy(packet)
        tampered["frozen_sources"]["DYNAMIC_CLOCK"]["report"]["by_market"][
            "BTC"
        ]["raw_trigger_count"] += 1
        unsigned = copy.deepcopy(tampered)
        del unsigned["packet_sha256"]
        tampered["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError,
            "DYNAMIC_CLOCK_SOURCE_SHA256_MISMATCH",
        ):
            MODULE.validate_packet(tampered)

        # Older schema-1 packets have no recoverable publication-time
        # Dynamic Clock input identity. They fail deterministically instead
        # of consulting whichever BTC evidence exists at validation time.
        legacy = copy.deepcopy(packet)
        del legacy["frozen_sources"]["DYNAMIC_CLOCK"]
        unsigned = copy.deepcopy(legacy)
        del unsigned["packet_sha256"]
        legacy["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError,
            "DYNAMIC_CLOCK_SOURCE_NOT_FROZEN",
        ):
            MODULE.validate_packet(legacy)

    def test_frozen_source_components_are_genuinely_independently_revalidatable(self):
        # STEP0_READ_MODEL_HEALTH / DART_FILING_CONTENT / SEC_FILING_CONTENT
        # (and, transitively through it, KRX_PREOPEN_COMPACT) read a mutable
        # rolling pointer with no per-date archive. KOFIA_FIRST_SEEN /
        # US_BREADTH_MEMBERSHIP / BTC_TREND / BTC_RISK /
        # STABLECOIN_NET_ISSUANCE / CRYPTO_BREADTH read a genuinely
        # immutable, append-only, per-date archive whose *presence* (not
        # content) can still change between build time and a later
        # revalidation, if the same-dated capture lands afterward. All nine
        # are frozen the same way. DYNAMIC_CLOCK freezes its exact shared
        # report and digest because later same-date captures can grow its
        # input set. packet["frozen_sources"] carries the
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
                "KOREA_MARKET_SIGNALS", "DYNAMIC_CLOCK",
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
        self.assertEqual(set(packet["frozen_sources"]), _default_frozen_source_keys())
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
            if component_id == "DYNAMIC_CLOCK":
                # This stale literal may correctly freeze either a report or
                # its fail-closed DECISION_DATE_PRECEDES_EVIDENCE verdict.
                self.assertIn(
                    packet["frozen_sources"][component_id]["kind"],
                    {"report", "error", "unavailable"},
                )
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
            "DYNAMIC_CLOCK": {"kind": "error", "value": "TAMPERED"},
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


class USRotationLedgerWiringTests(unittest.TestCase):
    """P2-05 -> P8-05: the daily component's real US rotation ledger input.

    Before this wiring build_rotation_discovery() always called
    LEDGER.empty_ledger(), so the daily Rotation section could not show a
    real state history even when one already existed. These tests exercise
    the orchestrator's own component (not the P8-05 builder in isolation),
    and every rotation fact asserted here is re-derived independently in the
    test from the unchanged rotation_state_ledger, never copied out of the
    row being checked.

    Fixture rotation packets/policies are synthetic P2-05 regression
    fixtures. They prove the wiring, the validation boundary and the
    fail-closed behaviour; they are not, and must not be read as, a natural
    operational rotation sample.
    """

    # An instant this repository has real committed Discovery/DART/wildcard
    # evidence for -- the same one test_event_discovery_population.py pins
    # for its real-population assertion on this exact component -- and after
    # ROTATION_AS_OF, so the rotation observation is never future-dated
    # relative to the briefing.
    GENERATED_AT = "2026-08-25T01:00:00Z"
    ROTATION_AS_OF = "2026-08-20"
    # The pair the frozen-source re-derivability test above already proves
    # build_packet()/validate_packet() resolve consistently.
    FROZEN_DECISION_DATE = DECISION_DATE
    FROZEN_GENERATED_AT = f"{DECISION_DATE}T14:59:00Z"

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "daily_orchestrator_rotation_state_fixture",
            ROOT / "test" / "test_rotation_state_ledger.py",
        )
        cls.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)

    def _source(self, *, packet=None, policy=None, previous_ledger=None):
        packet = self.fixture.us_packet(self.ROTATION_AS_OF) if packet is None else packet
        policy = self.fixture.policy_for(packet) if policy is None else policy
        return {
            "rotation_packet": packet,
            "state_policy": policy,
            "previous_ledger": previous_ledger,
        }

    def _row(self, source=MODULE.US_ROTATION_LEDGER_OMITTED, generated_at=None):
        # Default is the omitted-input sentinel, NOT None: None is a real
        # supplied value at this boundary (see the explicit-null cases below).
        return MODULE.build_rotation_discovery(
            "morning", generated_at or self.GENERATED_AT, None, source
        )

    @staticmethod
    def _rows_by_id(packet):
        return {row["component_id"]: row for row in packet["components"]}

    def test_valid_us_packet_reaches_the_real_daily_ledger_section(self):
        source = self._source()
        # Independently derived here, through the unchanged ledger module,
        # so the row is compared against a real apply_rotation() result
        # rather than against itself.
        expected = MODULE.LEDGER.apply_rotation(
            copy.deepcopy(source["rotation_packet"]),
            copy.deepcopy(source["state_policy"]),
        )
        mapping = source["state_policy"]["state_by_bucket_transition"]

        row = self._row(source)

        self.assertEqual(row["status"], "PENDING")
        rotation = row["packet"]["rotation"]
        self.assertEqual(rotation["ledger_status"], "STATE_HISTORY_OBSERVED")
        self.assertEqual(rotation["ledger_revision"], expected["ledger_revision"])
        self.assertEqual(rotation["ledger_revision"], 1)
        self.assertEqual(rotation["source_ledger_sha256"], expected["payload_sha256"])
        self.assertEqual(rotation["latest_change_count"], len(expected["records"]))
        self.assertGreater(rotation["latest_change_count"], 0)
        expected_by_entity = {
            (record["scope_id"], record["entity_id"]): record
            for record in expected["records"]
        }
        for change in rotation["latest_changes"]:
            record = expected_by_entity[(change["scope_id"], change["entity_id"])]
            self.assertEqual(change["market"], "US")
            self.assertEqual(change["record_sha256"], record["record_sha256"])
            self.assertEqual(
                change["source_packet_sha256"], record["input_packet_sha256"]
            )
            self.assertEqual(change["as_of_date"], self.ROTATION_AS_OF)
            # The state comes from the caller's ratified external policy,
            # not from anything this orchestrator decided.
            self.assertEqual(
                change["current_state"],
                mapping[change["structural_bucket_transition"]],
            )
            self.assertIsNone(change["prior_state"])
            self.assertEqual(
                change["state_transition"],
                f"UNINITIALIZED_TO_{change['current_state']}",
            )
        self.assertEqual(
            row["packet"]["summary"]["rotation_change_count"],
            rotation["latest_change_count"],
        )
        # A real rotation history still grants no new authority.
        self.assertEqual(row["packet"]["summary"]["new_candidate_count"], 0)
        self.assertEqual(row["packet"]["summary"]["ready_count"], 0)
        self.assertEqual(row["packet"]["summary"]["entry_trigger_count"], 0)
        self.assertIsNone(row["packet"]["summary"]["ranked_candidate"])
        self.assertIsNone(row["packet"]["summary"]["action"])
        self.assertFalse(row["authority"]["stage_promotion_authorized"])
        self.assertFalse(row["authority"]["candidate_ranking_authorized"])
        self.assertFalse(row["authority"]["action_generation_authorized"])
        self.assertFalse(row["authority"]["production_authorized"])
        self.assertFalse(row["authority"]["trading_authorized"])
        self.assertFalse(row["decision_eligible"])
        self.assertFalse(row["action_eligible"])
        self.assertFalse(row["order_eligible"])

    def test_absent_optional_input_preserves_legacy_empty_ledger_output(self):
        # The first call is literally the pre-wiring call signature -- the
        # rotation source argument is not passed at all -- and the second is
        # the explicit omitted sentinel. Absence means absence, and only
        # absence keeps the legacy bytes.
        legacy = MODULE.build_rotation_discovery("morning", self.GENERATED_AT)
        omitted = self._row()

        self.assertEqual(
            MODULE.payload_sha256(legacy), MODULE.payload_sha256(omitted)
        )
        rotation = legacy["packet"]["rotation"]
        self.assertEqual(rotation["ledger_status"], "EMPTY")
        self.assertEqual(rotation["ledger_revision"], 0)
        self.assertEqual(rotation["latest_changes"], [])
        self.assertEqual(rotation["latest_change_count"], 0)
        self.assertEqual(
            rotation["source_ledger_sha256"],
            MODULE.LEDGER.empty_ledger()["payload_sha256"],
        )
        # The optional input is not a component and is never fetched, so an
        # unsupplied build keeps the daily packet's existing frozen-source
        # key set (asserted exhaustively in
        # test_frozen_source_components_are_genuinely_independently_revalidatable).
        self.assertNotIn(
            MODULE.US_ROTATION_LEDGER_SOURCE, MODULE.FROZEN_SOURCE_COMPONENTS
        )

    def test_invalid_supplied_input_is_never_silently_an_empty_ledger(self):
        packet = self.fixture.us_packet(self.ROTATION_AS_OF)
        policy = self.fixture.policy_for(packet)
        cases = {
            # A caller who passes null passed something. "Null was supplied"
            # is not "nothing was supplied", so it must not resolve to the
            # legacy empty ledger.
            "explicitly_supplied_null": (
                None, "US_ROTATION_LEDGER_SOURCE_INVALID"
            ),
            "not_an_object": (["rotation_packet"], "SOURCE_FIELDS_INVALID"),
            "missing_previous_ledger_key": (
                {"rotation_packet": packet, "state_policy": policy},
                "SOURCE_FIELDS_INVALID",
            ),
            "unexpected_key": (
                {
                    "rotation_packet": packet,
                    "state_policy": policy,
                    "previous_ledger": None,
                    "ledger": "already-derived",
                },
                "SOURCE_FIELDS_INVALID",
            ),
            "packet_not_an_object": (
                {
                    "rotation_packet": "us_capital_rotation_packet/2",
                    "state_policy": policy,
                    "previous_ledger": None,
                },
                "SOURCE_INVALID",
            ),
            "previous_ledger_wrong_type": (
                {
                    "rotation_packet": packet,
                    "state_policy": policy,
                    "previous_ledger": [],
                },
                "SOURCE_INVALID",
            ),
            "empty_packet": (
                {
                    "rotation_packet": {},
                    "state_policy": policy,
                    "previous_ledger": None,
                },
                "US_ROTATION_LEDGER_MARKET_INVALID",
            ),
        }
        legacy_ledger_sha = MODULE.LEDGER.empty_ledger()["payload_sha256"]
        for name, (source, expected_code) in cases.items():
            with self.subTest(case=name):
                row = self._row(source)
                self.assertEqual(row["status"], "DEGRADED")
                self.assertIn(expected_code, row["reason"])
                self.assertFalse(row["validated"])
                # The whole point: a rejected input must not render as a
                # healthy component holding an empty rotation ledger.
                self.assertIsNone(row["packet"])
                self.assertNotIn(legacy_ledger_sha, MODULE.canonical_json(row))

    def test_future_wrong_market_unratified_mismatch_and_forgery_are_rejected(self):
        packet = self.fixture.us_packet(self.ROTATION_AS_OF)
        korea = self.fixture.korea_packet(self.ROTATION_AS_OF)

        # Point-in-time: an observation dated after the briefing instant is
        # rejected by the existing P8-05 check, not silently presented.
        future = self.fixture.us_packet("2026-08-26")
        cases = {
            "from_future": (
                self._source(packet=future, policy=self.fixture.policy_for(future)),
                "ROTATION_FROM_FUTURE",
            ),
            "wrong_market": (
                self._source(packet=korea),
                "US_ROTATION_LEDGER_MARKET_INVALID",
            ),
            "unratified_policy": (
                self._source(
                    packet=packet,
                    policy=self.fixture.policy_for(
                        packet, approval_status="UNRATIFIED"
                    ),
                ),
                "STATE_POLICY_NOT_RATIFIED",
            ),
            "policy_ratified_after_observation": (
                self._source(
                    packet=packet,
                    policy=self.fixture.policy_for(
                        packet, ratified_at_utc=f"{self.ROTATION_AS_OF}T00:00:01Z"
                    ),
                ),
                "STATE_POLICY_RATIFIED_TOO_LATE",
            ),
            "policy_input_binding_mismatch": (
                self._source(
                    packet=packet,
                    policy=self.fixture.policy_for(
                        packet, input_rotation_policy_sha256="0" * 64
                    ),
                ),
                "STATE_POLICY_INPUT_BINDING_MISMATCH",
            ),
            "rehashed_semantic_forgery": (
                self._source(packet=self._forged_us_packet(packet)),
                "ROTATION_PACKET_SEMANTIC_INVALID:US",
            ),
        }
        for name, (source, expected_code) in cases.items():
            with self.subTest(case=name):
                row = self._row(source)
                self.assertEqual(row["status"], "DEGRADED")
                self.assertIn(expected_code, row["reason"])
                self.assertIsNone(row["packet"])

    def _forged_us_packet(self, packet):
        """A packet whose bucket verdict was edited and then re-signed.

        Recomputing payload_sha256 makes the packet internally self-
        consistent, so only the producer's own re-derivation of ranks and
        buckets can catch it -- exactly why this wiring hands the ORIGINAL
        packet to apply_rotation() instead of trusting any digest.
        """
        forged = copy.deepcopy(packet)
        row = forged["theme_observations"][0]
        row["current_bucket"] = (
            "MIDDLE" if row["current_bucket"] != "MIDDLE" else "TOP"
        )
        row["bucket_transition"] = (
            f"{row['prior_bucket']}_TO_{row['current_bucket']}"
        )
        forged.pop("payload_sha256")
        forged["payload_sha256"] = MODULE.payload_sha256(forged)
        self.assertNotEqual(forged["payload_sha256"], packet["payload_sha256"])
        return forged

    def test_previous_non_us_history_is_preserved_and_reapply_is_idempotent(self):
        korea_packet = self.fixture.korea_packet(self.ROTATION_AS_OF)
        previous = MODULE.LEDGER.apply_rotation(
            copy.deepcopy(korea_packet),
            self.fixture.policy_for(korea_packet),
        )
        korea_records = {
            (record["scope_id"], record["entity_id"]): record
            for record in previous["records"]
            if record["market"] == "KOREA"
        }
        self.assertTrue(korea_records)

        us = self.fixture.us_packet(self.ROTATION_AS_OF)
        source = self._source(packet=us, previous_ledger=previous)
        row = self._row(source)

        self.assertEqual(row["status"], "PENDING")
        rotation = row["packet"]["rotation"]
        self.assertEqual(rotation["ledger_revision"], 2)
        markets = {change["market"] for change in rotation["latest_changes"]}
        self.assertEqual(markets, {"US", "KOREA"})
        for change in rotation["latest_changes"]:
            if change["market"] != "KOREA":
                continue
            # Pre-existing non-US history is carried through untouched, not
            # replaced by the US application.
            self.assertEqual(
                change["record_sha256"],
                korea_records[
                    (change["scope_id"], change["entity_id"])
                ]["record_sha256"],
            )

        # Re-applying the exact same source packet on top of the resulting
        # ledger is idempotent through the ledger's own append-only rules.
        applied = MODULE.LEDGER.apply_rotation(
            copy.deepcopy(us),
            copy.deepcopy(source["state_policy"]),
            copy.deepcopy(previous),
        )
        duplicate = self._row(
            self._source(packet=us, previous_ledger=applied)
        )
        self.assertEqual(duplicate["packet"]["rotation"], rotation)
        self.assertEqual(
            duplicate["packet"]["rotation"]["source_ledger_sha256"],
            applied["payload_sha256"],
        )

    def test_supplied_input_objects_are_not_mutated(self):
        us = self.fixture.us_packet(self.ROTATION_AS_OF)
        policy = self.fixture.policy_for(us)
        # A same-packet re-application on an existing ledger: exercises the
        # duplicate path while passing every supplied object through the
        # component.
        source = self._source(
            packet=us,
            policy=policy,
            previous_ledger=MODULE.LEDGER.apply_rotation(
                copy.deepcopy(us), copy.deepcopy(policy)
            ),
        )
        before = MODULE.canonical_json(source)
        row = self._row(source)
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(MODULE.canonical_json(source), before)
        self.assertEqual(row["packet"]["rotation"]["ledger_revision"], 1)

    def test_frozen_replay_survives_external_input_change_and_catches_tamper(self):
        source = self._source()
        frozen_before = copy.deepcopy(source)
        packet = MODULE.build_packet(
            "evening",
            self.FROZEN_DECISION_DATE,
            self.FROZEN_GENERATED_AT,
            frozen_sources={MODULE.US_ROTATION_LEDGER_SOURCE: source},
        )
        self.assertEqual(
            set(packet["frozen_sources"]),
            _default_frozen_source_keys() | {MODULE.US_ROTATION_LEDGER_SOURCE},
        )
        frozen = packet["frozen_sources"][MODULE.US_ROTATION_LEDGER_SOURCE]
        # The RAW inputs are frozen -- not a derived, self-rehashed ledger --
        # so revalidation re-runs the real apply_rotation() over them.
        self.assertEqual(frozen, frozen_before)
        self.assertEqual(set(frozen), {"rotation_packet", "state_policy", "previous_ledger"})

        # Mutating the caller's own object afterwards must not retroactively
        # change what this packet was built from, nor break its digest.
        source["rotation_packet"]["theme_observations"][0]["current_bucket"] = "TOP"
        source["state_policy"]["policy_id"] = "US.STATE.SWAPPED.V1"
        self.assertEqual(
            packet["frozen_sources"][MODULE.US_ROTATION_LEDGER_SOURCE], frozen_before
        )
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

        # Tampering the frozen input itself is caught, because validation
        # genuinely re-derives the row from it instead of trusting the row.
        tampered = copy.deepcopy(packet)
        tampered["frozen_sources"][MODULE.US_ROTATION_LEDGER_SOURCE][
            "rotation_packet"
        ] = self._forged_us_packet(frozen_before["rotation_packet"])
        unsigned = copy.deepcopy(tampered)
        del unsigned["packet_sha256"]
        tampered["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"):
            MODULE.validate_packet(tampered)

    def test_key_present_null_frozen_source_is_supplied_input_not_absence(self):
        """The production boundary: build_packet() must not read a supplied
        null as an omitted source.

        Only key ABSENCE is absence. A frozen_sources entry that exists and
        holds null (or any other unusable value) is an input the caller
        actually handed this build, so it is frozen exactly as supplied and
        fails the ROTATION_DISCOVERY row closed. Reading it as absence would
        render an explicitly broken build identically to a healthy one that
        never had a rotation source -- the two must stay distinguishable in
        the packet's own bytes.
        """
        key = MODULE.US_ROTATION_LEDGER_SOURCE
        omitted = MODULE.build_packet(
            "evening", self.FROZEN_DECISION_DATE, self.FROZEN_GENERATED_AT
        )
        explicit_null = MODULE.build_packet(
            "evening",
            self.FROZEN_DECISION_DATE,
            self.FROZEN_GENERATED_AT,
            frozen_sources={key: None},
        )

        # Same slot, same date, same instant -- yet not the same packet,
        # because they were built from different inputs.
        self.assertNotEqual(omitted["packet_sha256"], explicit_null["packet_sha256"])

        # Omitted: legacy key set, legacy bytes, no rotation-source failure,
        # and the packet still replays to itself byte-for-byte.
        self.assertNotIn(key, omitted["frozen_sources"])
        self.assertEqual(
            set(omitted["frozen_sources"]), _default_frozen_source_keys()
        )
        omitted_row = self._rows_by_id(omitted)["ROTATION_DISCOVERY"]
        self.assertNotEqual(omitted_row["status"], "DEGRADED")
        self.assertNotIn(key, omitted_row["reason"])
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(omitted)), omitted)

        # Explicit null: the raw supplied value is preserved, so the
        # fail-closed verdict replays deterministically from the same
        # rejected input rather than from a key that was quietly dropped.
        self.assertIn(key, explicit_null["frozen_sources"])
        self.assertIsNone(explicit_null["frozen_sources"][key])
        null_row = self._rows_by_id(explicit_null)["ROTATION_DISCOVERY"]
        self.assertEqual(null_row["status"], "DEGRADED")
        self.assertIn("US_ROTATION_LEDGER_SOURCE_INVALID", null_row["reason"])
        self.assertIsNone(null_row["packet"])
        self.assertFalse(null_row["validated"])
        # No silent legacy fallback: the empty ledger this component used to
        # hand out unconditionally appears nowhere in the failed row.
        self.assertNotIn(
            MODULE.LEDGER.empty_ledger()["payload_sha256"],
            MODULE.canonical_json(null_row),
        )
        self.assertNotEqual(
            MODULE.payload_sha256(null_row), MODULE.payload_sha256(omitted_row)
        )
        self.assertEqual(
            MODULE.validate_packet(copy.deepcopy(explicit_null)), explicit_null
        )

    def test_malformed_key_present_frozen_source_cannot_render_as_healthy(self):
        """Same boundary, non-null unusable values: still never the empty
        ledger, still frozen exactly as supplied for replay."""
        key = MODULE.US_ROTATION_LEDGER_SOURCE
        legacy_ledger_sha = MODULE.LEDGER.empty_ledger()["payload_sha256"]
        for name, supplied in {
            "empty_object": {},
            "wrong_keys": {"ledger": "already-derived"},
            "derived_ledger_instead_of_raw_inputs": MODULE.LEDGER.empty_ledger(),
            "not_an_object": "US_ROTATION_LEDGER",
        }.items():
            with self.subTest(case=name):
                packet = MODULE.build_packet(
                    "evening",
                    self.FROZEN_DECISION_DATE,
                    self.FROZEN_GENERATED_AT,
                    frozen_sources={key: supplied},
                )
                self.assertEqual(packet["frozen_sources"][key], supplied)
                row = self._rows_by_id(packet)["ROTATION_DISCOVERY"]
                self.assertEqual(row["status"], "DEGRADED")
                self.assertIn("US_ROTATION_LEDGER_SOURCE", row["reason"])
                self.assertIsNone(row["packet"])
                self.assertNotIn(
                    legacy_ledger_sha, MODULE.canonical_json(row)
                )

    def test_unknown_frozen_source_key_still_fails_closed(self):
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "FROZEN_SOURCES_INVALID"
        ):
            MODULE.build_packet(
                "morning",
                self.FROZEN_DECISION_DATE,
                self.FROZEN_GENERATED_AT,
                frozen_sources={"KOREA_ROTATION_LEDGER": {}},
            )


class RuntimeRegimeReadinessDerivationVersionTests(unittest.TestCase):
    """`runtime_regime_readiness_version` binds a DERIVATION, not a policy.

    Version 2 wires the exact, independently re-derived P1 runtime blockers
    into BOTH P6-06 and P7-12, and labels the ACTION_RISK_PORTFOLIO_SUMMARY
    component row with the KST business date its own validated summary packet
    reports for. Version 3 -- the default for every new packet -- adds the
    exact P2_ROTATION_STATE prerequisites, re-derived from an immutable
    Git-authenticated snapshot of P2-05's three committed inputs, and changes
    nothing else.

    The marker-absent and explicit-1 forms are ambiguous about exactly one
    field -- that summary row's `as_of_date` -- because packets of both kinds
    were genuinely issued under the earlier `generated_at`-UTC-day basis and
    under the current KST basis, with nothing recorded to tell them apart.
    Both must keep replaying byte-identically, and nothing else may become
    acceptable in the process.

    Every packet here is built once and cached: these are full, real-evidence
    orchestrator builds, and the point of the suite is which bytes come out,
    not how many times they are recomputed.
    """

    _CACHE: dict[tuple, dict] = {}
    _UTC_DAY = MODULE.SUMMARY_ROW_DATE_BASIS_GENERATED_AT_UTC_DAY
    _KST_DAY = MODULE.SUMMARY_ROW_DATE_BASIS_PACKET_DECISION_DATE

    @classmethod
    def packet(
        cls,
        *,
        version,
        basis=None,
        slot="morning",
        generated_at=NATURAL_MORNING_GENERATED_AT,
    ):
        basis = cls._KST_DAY if basis is None else basis
        key = (slot, DECISION_DATE, generated_at, version, basis)
        if key not in cls._CACHE:
            cls._CACHE[key] = MODULE.build_packet(
                slot,
                DECISION_DATE,
                generated_at,
                runtime_regime_readiness_version=version,
                summary_row_date_basis=basis,
            )
        return copy.deepcopy(cls._CACHE[key])

    @staticmethod
    def _rows(packet):
        return {row["component_id"]: row for row in packet["components"]}

    @staticmethod
    def _resign(packet):
        unsigned = copy.deepcopy(packet)
        unsigned.pop("packet_sha256", None)
        packet["packet_sha256"] = MODULE.payload_sha256(unsigned)
        return packet

    @staticmethod
    def _relabel_summary_row(packet, as_of_date):
        for row in packet["components"]:
            if row["component_id"] == "ACTION_RISK_PORTFOLIO_SUMMARY":
                row["as_of_date"] = as_of_date
        return packet

    def _p1_reasons(self, packet, component_id):
        return self._rows(packet)[component_id]["packet"]["unavailable_reasons"][
            "P1_REGIME_DECISION"
        ]

    def test_new_packets_default_to_derivation_version_three(self):
        self.assertEqual(MODULE.RUNTIME_REGIME_READINESS_VERSION, 3)
        default = MODULE.build_packet(
            "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT
        )
        marker = default["runtime_regime_readiness_version"]
        self.assertEqual(marker, 3)
        self.assertIs(type(marker), int)
        # The marker is hash-bound like every other field, and an explicit 3
        # is exactly the default -- there is no second "new" derivation.
        self.assertEqual(default, self.packet(version=3))
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(default)), default)

    def test_version_two_binds_exact_p7_blockers_and_grants_nothing(self):
        # Deliberately the same-UTC-day geometry the already-merged P6-06
        # assertion below uses (test_p1_regime_slot_carries_exact_runtime_
        # blockers_not_a_placeholder), so this pins the version-2 P7-12
        # binding against a run whose exact blocker derivation is already
        # regression-covered rather than assumed.
        packet = self.packet(version=2, generated_at=MORNING_GENERATED_AT)
        posture = self._rows(packet)["STRATEGIC_CAPITAL_POSTURE"]["packet"]
        reasons = posture["unavailable_reasons"]["P1_REGIME_DECISION"]

        # The opaque placeholder is gone from P7-12 too, and the real gaps
        # are named.
        self.assertNotIn(
            "P1_REGIME_DECISION_PRODUCTION_CONTRACT_UNAVAILABLE", reasons
        )
        self.assertIn("P1_REGIME_DECISION_NOT_RUNTIME_WIRED", reasons)
        for market in ("US", "KR", "CRYPTO"):
            self.assertIn(
                f"SIGNED_NORMALIZATION_POLICY_UNRATIFIED:{market}", reasons
            )
            self.assertIn(f"PIT_REPLAY_NOT_ACCEPTED:{market}", reasons)
        self.assertEqual(reasons, sorted(set(reasons)))
        # Both consumers re-derive the list from this run's own envelopes, so
        # they agree -- P7-12 does not read P6-06's packet to obtain it.
        self.assertEqual(reasons, self._p1_reasons(packet, "DEFENSIVE_ACTION_DECISION"))
        # No invocation-derived identity may leak into semantic content.
        self.assertFalse(any("SHA256" in reason for reason in reasons), reasons)

        # Naming the blockers promotes nothing.
        source_rows = {row["name"]: row for row in posture["sources"]}
        self.assertEqual(
            source_rows["P1_REGIME_DECISION"]["availability"], "UNAVAILABLE"
        )
        self.assertIsNone(source_rows["P1_REGIME_DECISION"]["source_packet_sha256"])
        self.assertEqual(
            posture["status"], "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED"
        )
        self.assertEqual(posture["decision_status"], "BLOCKED")
        self.assertEqual(
            posture["market_budget"], {"CRYPTO": None, "KOREA": None, "US": None}
        )
        for key in (
            "risk_posture", "cash_reserve", "hedge_budget", "max_gross_risk",
            "max_net_risk", "theme_headroom", "allocation_proposal",
            "target_exposures", "position_sizes", "policy_packet",
        ):
            self.assertIsNone(posture[key], key)
        self.assertEqual(posture["order_intents"], [])
        self.assertIn(
            "SOURCE_UNAVAILABLE:P1_REGIME_DECISION", posture["binding_reasons"]
        )
        self.assertIn(
            "P1_REGIME_DECISION_UNAVAILABLE", posture["unresolved_boundaries"]
        )
        self.assertTrue(posture["authority"]["readiness_inventory_only"])
        for key, value in posture["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_legacy_versions_keep_their_original_blocker_fidelity(self):
        generic = ["P1_REGIME_DECISION_PRODUCTION_CONTRACT_UNAVAILABLE"]

        absent = self.packet(version=None)
        self.assertNotIn("runtime_regime_readiness_version", absent)
        for component_id in ("DEFENSIVE_ACTION_DECISION", "STRATEGIC_CAPITAL_POSTURE"):
            self.assertEqual(
                self._p1_reasons(absent, component_id), generic, component_id
            )

        # Version 1 wired the runtime derivation into P6-06 only. P7-12 keeps
        # the generic blocker, exactly as the already-issued v1 packets carry
        # it, while P6-06's list is no longer the marker-absent one.
        v1 = self.packet(version=1)
        self.assertEqual(v1["runtime_regime_readiness_version"], 1)
        self.assertEqual(self._p1_reasons(v1, "STRATEGIC_CAPITAL_POSTURE"), generic)
        self.assertNotEqual(
            self._p1_reasons(v1, "DEFENSIVE_ACTION_DECISION"), generic
        )

    def test_both_enumerated_legacy_forms_replay_byte_identically(self):
        # Natural morning geometry: the UTC calendar day is the day BEFORE
        # the KST business date, so the two historical bases are genuinely
        # distinguishable here rather than coincidentally equal.
        self.assertNotEqual(NATURAL_MORNING_GENERATED_AT[:10], DECISION_DATE)

        for version in (None, 1):
            with self.subTest(version=version):
                kst_form = self.packet(version=version, basis=self._KST_DAY)
                utc_form = self.packet(version=version, basis=self._UTC_DAY)
                kst_row = self._rows(kst_form)["ACTION_RISK_PORTFOLIO_SUMMARY"]
                utc_row = self._rows(utc_form)["ACTION_RISK_PORTFOLIO_SUMMARY"]

                self.assertEqual(kst_row["as_of_date"], DECISION_DATE)
                self.assertEqual(
                    utc_row["as_of_date"], NATURAL_MORNING_GENERATED_AT[:10]
                )
                # Exactly one field separates the two historical forms.
                self.assertEqual(
                    {k: v for k, v in kst_row.items() if k != "as_of_date"},
                    {k: v for k, v in utc_row.items() if k != "as_of_date"},
                )
                self.assertNotEqual(
                    kst_form["packet_sha256"], utc_form["packet_sha256"]
                )
                # Both are accepted, each only against a complete rebuild of
                # the whole packet -- never by copying the stored row across.
                self.assertEqual(
                    MODULE.validate_packet(copy.deepcopy(kst_form)), kst_form
                )
                self.assertEqual(
                    MODULE.validate_packet(copy.deepcopy(utc_form)), utc_form
                )

    def test_same_kst_day_legacy_reconstructions_coalesce(self):
        # The 18:30 KST evening run lands on the same UTC and KST day, so the
        # two bases are indistinguishable and the dual reconstruction
        # collapses to one historical result. This is the preservation case:
        # the row keeps exactly the value the old basis produced.
        self.assertEqual(EVENING_GENERATED_AT[:10], DECISION_DATE)
        kst_form = self.packet(
            version=None, slot="evening", generated_at=EVENING_GENERATED_AT,
            basis=self._KST_DAY,
        )
        utc_form = self.packet(
            version=None, slot="evening", generated_at=EVENING_GENERATED_AT,
            basis=self._UTC_DAY,
        )
        self.assertEqual(kst_form, utc_form)
        self.assertEqual(
            self._rows(kst_form)["ACTION_RISK_PORTFOLIO_SUMMARY"]["as_of_date"],
            DECISION_DATE,
        )
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(kst_form)), kst_form)

    def test_legacy_replay_accepts_only_the_two_enumerated_dates(self):
        # Accepting two historical forms is not accepting an arbitrary date:
        # the rebuild derives both candidates from decision_date and
        # generated_at, never from the persisted row.
        two_days_before = (
            dt.date.fromisoformat(DECISION_DATE) - dt.timedelta(days=2)
        ).isoformat()
        self.assertNotIn(
            two_days_before, (DECISION_DATE, NATURAL_MORNING_GENERATED_AT[:10])
        )
        forged = self._resign(
            self._relabel_summary_row(self.packet(version=None), two_days_before)
        )
        with self.assertRaisesRegex(MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"):
            MODULE.validate_packet(forged)

    def test_version_two_never_falls_back_to_the_legacy_date_basis(self):
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "SUMMARY_ROW_DATE_BASIS_NOT_LEGACY"
        ):
            MODULE.build_packet(
                "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT,
                summary_row_date_basis=self._UTC_DAY,
            )
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError, "SUMMARY_ROW_DATE_BASIS_INVALID"
        ):
            MODULE.build_packet(
                "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT,
                runtime_regime_readiness_version=None,
                summary_row_date_basis="KST",
            )
        # A version-2 packet re-signed onto the archival row label has no
        # legacy derivation to fall into.
        tampered = self._resign(
            self._relabel_summary_row(
                self.packet(version=2), NATURAL_MORNING_GENERATED_AT[:10]
            )
        )
        with self.assertRaisesRegex(MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"):
            MODULE.validate_packet(tampered)

    def test_version_marker_is_strict_and_null_is_not_absence(self):
        for value in (True, False, "1", "2", "3", 0, -1, 4, 1.0, [1]):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    MODULE.DailyOrchestratorError,
                    "RUNTIME_REGIME_READINESS_VERSION_INVALID",
                ):
                    MODULE.build_packet(
                        "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT,
                        runtime_regime_readiness_version=value,
                    )

        legacy = self.packet(version=None)
        # A persisted null is a value, not absence: no build ever emits it,
        # so it must fail instead of resolving to the legacy default.
        explicit_null = copy.deepcopy(legacy)
        explicit_null["runtime_regime_readiness_version"] = None
        self._resign(explicit_null)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError,
            "RUNTIME_REGIME_READINESS_VERSION_INVALID",
        ):
            MODULE.validate_packet(explicit_null)

        unknown = copy.deepcopy(legacy)
        unknown["runtime_regime_readiness_version"] = 4
        self._resign(unknown)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError,
            "RUNTIME_REGIME_READINESS_VERSION_INVALID",
        ):
            MODULE.validate_packet(unknown)

        # Re-signing a higher marker onto a legacy packet cannot promote it:
        # the marker selects the derivation, and that derivation has to
        # reproduce the persisted bytes.
        for forged_version in (1, 2):
            with self.subTest(forged_version=forged_version):
                promoted = copy.deepcopy(legacy)
                promoted["runtime_regime_readiness_version"] = forged_version
                self._resign(promoted)
                with self.assertRaisesRegex(
                    MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"
                ):
                    MODULE.validate_packet(promoted)

    def test_version_two_blockers_reach_the_flow_first_capital_action_section(self):
        packet = self.packet(version=2)
        rows = self._rows(packet)
        posture = rows["STRATEGIC_CAPITAL_POSTURE"]["packet"]
        summary_packet = rows["ACTION_RISK_PORTFOLIO_SUMMARY"]["packet"]
        embedded = summary_packet["source_packets"]["STRATEGIC_CAPITAL_POSTURE"]

        # The exact blockers travel through the summary's own revalidated
        # source lineage, hash and all.
        self.assertEqual(embedded["packet_sha256"], posture["packet_sha256"])
        self.assertEqual(
            embedded["unavailable_reasons"]["P1_REGIME_DECISION"],
            posture["unavailable_reasons"]["P1_REGIME_DECISION"],
        )
        self.assertEqual(
            summary_packet["lineage"]["source_packet_sha256"][
                "STRATEGIC_CAPITAL_POSTURE"
            ],
            posture["packet_sha256"],
        )
        self.assertEqual(
            summary_packet["status"],
            "ACTION_RISK_PORTFOLIO_PRESENTED_NO_ACTION_AUTHORITY",
        )
        self.assertTrue(
            all(row["action"] is None for row in summary_packet["actions"])
        )

        # All three CAPITAL_ACTION rows agree on the KST business date, so the
        # section reaches its honest not-ready state -- and that grants
        # nothing either.
        for component_id in CAPITAL_ACTION_COMPONENTS:
            self.assertEqual(rows[component_id]["as_of_date"], DECISION_DATE)
        section = _capital_action_section(packet)
        self.assertEqual(section["status"], "PENDING")
        self.assertEqual(section["as_of_date"], DECISION_DATE)
        self.assertEqual(section["unknown_reason"], "SOURCE_COMPONENT_NOT_READY")
        self.assertFalse(section["decision_eligible"])
        self.assertFalse(section["action_eligible"])
        self.assertFalse(section["order_eligible"])

    def test_legacy_utc_row_still_blocks_flow_first_exactly_as_it_did(self):
        # Archival fidelity, not new-generation behaviour: the historical
        # UTC-day row genuinely disagreed with its two siblings, and the
        # unchanged aggregator must still refuse to present the section.
        utc_form = self.packet(version=None, basis=self._UTC_DAY)
        section = _capital_action_section(utc_form)
        self.assertEqual(section["status"], "DATA_BLOCKED")
        self.assertEqual(section["unknown_reason"], "SOURCE_AS_OF_DATE_MISMATCH")
        self.assertIsNone(section["as_of_date"])
        self.assertFalse(section["decision_eligible"])
        self.assertFalse(section["action_eligible"])
        self.assertFalse(section["order_eligible"])

    def test_version_two_row_label_carries_no_invocation_day_noise(self):
        # Same decision_date, two runs on different UTC calendar days. The
        # row label is the KST business date in both, so a same-day rebuild
        # cannot move this component's semantic fingerprint merely because
        # the invocation moved.
        morning = self.packet(version=2)
        evening = self.packet(
            version=2, slot="evening", generated_at=EVENING_GENERATED_AT
        )
        self.assertNotEqual(
            NATURAL_MORNING_GENERATED_AT[:10], EVENING_GENERATED_AT[:10]
        )
        for built in (morning, evening):
            rows = self._rows(built)
            self.assertEqual(
                rows["ACTION_RISK_PORTFOLIO_SUMMARY"]["as_of_date"], DECISION_DATE
            )
            reasons = self._p1_reasons(built, "STRATEGIC_CAPITAL_POSTURE")
            self.assertEqual(reasons, sorted(set(reasons)))
            self.assertEqual(
                reasons, self._p1_reasons(built, "DEFENSIVE_ACTION_DECISION")
            )
            self.assertFalse(any("SHA256" in reason for reason in reasons), reasons)


# The exact, finite P2_ROTATION_STATE reason list derivation version 3 binds:
# the preserved generic production-contract blocker plus one code per validated
# market and prerequisite. Written out in full rather than generated, so a
# change to the mapping has to be made deliberately here too.
P2_ROTATION_STATE_EXACT_REASONS = [
    "P2_ROTATION_STATE:CRYPTO:APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
    "P2_ROTATION_STATE:CRYPTO:EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
    "P2_ROTATION_STATE:CRYPTO:FULL_PRODUCTION_ROTATION_PACKET_MISSING",
    "P2_ROTATION_STATE:KOREA:APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
    "P2_ROTATION_STATE:KOREA:EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
    "P2_ROTATION_STATE:KOREA:FULL_PRODUCTION_ROTATION_PACKET_MISSING",
    "P2_ROTATION_STATE:US:APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
    "P2_ROTATION_STATE:US:EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
    "P2_ROTATION_STATE:US:FULL_PRODUCTION_ROTATION_PACKET_MISSING",
    "P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE",
]
P2_ROTATION_STATE_GENERIC_REASONS = [
    "P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE"
]


class P2RotationStateDiagnosticBindingTests(unittest.TestCase):
    """Derivation version 3 names the real P2_ROTATION_STATE prerequisites.

    The slot stays unavailable-only with a null source identity, so this is a
    diagnostic conveyance and nothing else: no production rotation/state
    packet is wired, no state vocabulary, freshness policy, availability or
    ranking is authorized, budgets stay null and every action/order/Production/
    trading flag stays false.

    Packets are built through the sibling suite's cache -- these are full,
    real-evidence orchestrator builds and the point is which bytes come out.
    """

    KEY = MODULE.P2_ROTATION_STATE_READINESS_INPUTS
    PROVENANCE_ERROR = (
        MODULE.ROTATION_STATE_READINESS.RotationStateLedgerReadinessProvenanceError
    )
    _VERSIONS = RuntimeRegimeReadinessDerivationVersionTests

    @classmethod
    def packet(cls, **kwargs):
        return cls._VERSIONS.packet(**kwargs)

    @staticmethod
    def _rows(packet):
        return {row["component_id"]: row for row in packet["components"]}

    @staticmethod
    def _resign(packet):
        return RuntimeRegimeReadinessDerivationVersionTests._resign(packet)

    def _posture(self, packet):
        return self._rows(packet)["STRATEGIC_CAPITAL_POSTURE"]["packet"]

    def _p2_reasons(self, packet, component_id="STRATEGIC_CAPITAL_POSTURE"):
        return self._rows(packet)[component_id]["packet"]["unavailable_reasons"][
            "P2_ROTATION_STATE"
        ]

    @contextlib.contextmanager
    def _p2_source_unreadable(self):
        """Make every route to P2-05's inputs raise, live and frozen alike.

        If a derivation touches the P2 source at all -- capture, frozen
        evaluation, live producer run, live committed-HEAD check or file read
        -- it fails loudly here instead of quietly succeeding.
        """
        boom = AssertionError("P2 readiness source must not be touched here")
        producer = MODULE.ROTATION_STATE_READINESS
        # Capture and rederivation share one loaded producer, so patching it
        # once really does close every route.
        self.assertIs(producer, MODULE.STRATEGIC_CAPITAL_POSTURE.ROTATION_STATE_READINESS)
        names = (
            "capture_readiness_inputs",
            "evaluate_frozen_readiness_inputs",
            "verify_readiness_inputs",
            "build_readiness",
            "_verify_head_blob",
            "_read_json",
        )
        with contextlib.ExitStack() as stack:
            for name in names:
                stack.enter_context(
                    mock.patch.object(producer, name, side_effect=boom)
                )
            yield

    # -- the binding itself -------------------------------------------------

    def test_version_three_binds_the_exact_finite_blockers_and_grants_nothing(self):
        packet = self.packet(version=3)
        posture = self._posture(packet)
        reasons = posture["unavailable_reasons"]["P2_ROTATION_STATE"]

        self.assertEqual(reasons, P2_ROTATION_STATE_EXACT_REASONS)
        self.assertEqual(reasons, sorted(set(reasons)))
        # The generic production-contract blocker is preserved, not replaced.
        self.assertIn("P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE", reasons)
        # Nothing invocation- or object-identity-derived travels with them.
        for reason in reasons:
            self.assertNotIn("SHA256", reason)
            self.assertNotIn("/", reason)

        row = {row["name"]: row for row in posture["sources"]}["P2_ROTATION_STATE"]
        self.assertEqual(row["availability"], "UNAVAILABLE")
        self.assertIsNone(row["source_status"])
        self.assertIsNone(row["evidence_date"])
        self.assertIsNone(row["source_packet_sha256"])
        self.assertIsNone(posture["source_packets"]["P2_ROTATION_STATE"])
        self.assertIsNone(
            posture["lineage"]["source_packet_sha256"]["P2_ROTATION_STATE"]
        )
        self.assertIn(
            "SOURCE_UNAVAILABLE:P2_ROTATION_STATE", posture["binding_reasons"]
        )
        self.assertIn(
            "P2_ROTATION_STATE_UNAVAILABLE", posture["unresolved_boundaries"]
        )

        # Naming the gaps promotes nothing.
        self.assertEqual(
            posture["status"], "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED"
        )
        self.assertEqual(posture["decision_status"], "BLOCKED")
        self.assertEqual(
            posture["market_budget"], {"CRYPTO": None, "KOREA": None, "US": None}
        )
        for key in (
            "risk_posture", "cash_reserve", "hedge_budget", "max_gross_risk",
            "max_net_risk", "theme_headroom", "allocation_proposal",
            "target_exposures", "position_sizes", "policy_packet",
        ):
            self.assertIsNone(posture[key], key)
        self.assertEqual(posture["order_intents"], [])
        self.assertTrue(posture["authority"]["readiness_inventory_only"])
        for key, value in posture["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

    def test_only_the_p2_reason_list_moves_between_version_two_and_three(self):
        v2 = self.packet(version=2)
        v3 = self.packet(version=3)
        self.assertEqual(
            self._p2_reasons(v2), P2_ROTATION_STATE_GENERIC_REASONS
        )
        self.assertEqual(self._p2_reasons(v3), P2_ROTATION_STATE_EXACT_REASONS)
        # P6-06 has no P2_ROTATION_STATE slot at all, and its P1 derivation is
        # identical under both versions.
        self.assertNotIn(
            "P2_ROTATION_STATE",
            self._rows(v3)["DEFENSIVE_ACTION_DECISION"]["packet"][
                "unavailable_reasons"
            ],
        )
        self.assertEqual(
            self._rows(v2)["DEFENSIVE_ACTION_DECISION"]["packet"],
            self._rows(v3)["DEFENSIVE_ACTION_DECISION"]["packet"],
        )

        v2_posture = self._posture(v2)
        v3_posture = self._posture(v3)
        self.assertNotEqual(v2_posture["packet_sha256"], v3_posture["packet_sha256"])
        ignored = {"unavailable_reasons", "sources", "packet_sha256"}
        self.assertEqual(
            {k: v for k, v in v2_posture.items() if k not in ignored},
            {k: v for k, v in v3_posture.items() if k not in ignored},
        )
        v2_rows = {row["name"]: row for row in v2_posture["sources"]}
        v3_rows = {row["name"]: row for row in v3_posture["sources"]}
        for name in v2_rows:
            if name == "P2_ROTATION_STATE":
                continue
            self.assertEqual(v2_rows[name], v3_rows[name], name)

    def test_the_frozen_input_is_the_exact_committed_tuple_and_nothing_else(self):
        packet = self.packet(version=3)
        envelope = packet["frozen_sources"][self.KEY]
        producer = MODULE.ROTATION_STATE_READINESS

        self.assertEqual(set(envelope), {"schema_version", "source_commit", "files"})
        self.assertEqual(
            envelope["schema_version"], "p2_rotation_readiness_inputs/1"
        )
        self.assertRegex(envelope["source_commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(
            set(envelope["files"]),
            {
                "config/rotation_state_ledger_operational_readiness_contract.json",
                "config/rotation_state_ledger_contract.json",
                "data/latest_korea_rotation.json",
            },
        )
        for relative, entry in envelope["files"].items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    set(entry), {"state", "blob_oid", "content_base64"}
                )
                self.assertIn(entry["state"], ("PRESENT", "ABSENT"))
        # No stored reason, validity flag or error code: the packet carries
        # source bytes and object identity only. (Both markers contain ':' and
        # '_', which are outside the base64 alphabet, so they can only appear
        # here if something actually stored them.)
        body = MODULE.canonical_json(envelope)
        for forbidden in ("VALIDATION_FAILED", "P2_ROTATION_STATE:"):
            self.assertNotIn(forbidden, body)
        # A capture is deterministic at one HEAD.
        self.assertEqual(envelope, producer.capture_readiness_inputs(MODULE.ROOT))

    # -- replay stability ---------------------------------------------------

    def test_v3_replay_never_recaptures_or_rereads_the_live_pointer(self):
        packet = self.packet(version=3)
        producer = MODULE.ROTATION_STATE_READINESS
        boom = AssertionError("replay must not recapture or read live state")
        # Replay may prove the frozen envelope against Git objects, and must do
        # nothing else: no fresh capture, no live producer run, no
        # committed-HEAD check against today's working tree, no file read.
        with contextlib.ExitStack() as stack:
            for name in (
                "capture_readiness_inputs",
                "build_readiness",
                "_verify_head_blob",
                "_read_json",
            ):
                stack.enter_context(
                    mock.patch.object(producer, name, side_effect=boom)
                )
            self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

    def test_a_v3_packet_missing_the_frozen_key_is_a_hard_error(self):
        stripped = copy.deepcopy(self.packet(version=3))
        del stripped["frozen_sources"][self.KEY]
        self._resign(stripped)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError,
            "P2_ROTATION_STATE_READINESS_INPUTS_NOT_FROZEN",
        ):
            MODULE.validate_packet(stripped)

    def test_a_null_or_malformed_frozen_envelope_is_a_hard_error_not_a_diagnostic(
        self,
    ):
        # None of these may resolve to a fresh capture, to the generic legacy
        # blocker, or to the semantic-invalid diagnostic: an envelope that
        # cannot be authenticated is not an envelope that was authenticated
        # and found wanting.
        valid = copy.deepcopy(
            self.packet(version=3)["frozen_sources"][self.KEY]
        )
        missing_object = dict(valid, source_commit="b" * 40)
        extra_key = dict(valid, unexpected="x")
        cases = {
            "null": None,
            "empty": {},
            "not_an_object": "P2_ROTATION_STATE_READINESS_INPUTS",
            "extra_key": extra_key,
            "missing_object": missing_object,
        }
        for name, supplied in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(self.PROVENANCE_ERROR):
                    MODULE.build_packet(
                        "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT,
                        frozen_sources={self.KEY: supplied},
                    )

    # -- legacy non-interference -------------------------------------------

    def test_absent_v1_and_v2_never_touch_the_new_p2_source(self):
        # Four historical reconstructions plus the real version-2 form. None
        # of them may capture, read or validate the new input -- so all five
        # still build byte-identically while every route to it raises.
        forms = [
            {"version": None, "basis": self._VERSIONS._KST_DAY},
            {"version": None, "basis": self._VERSIONS._UTC_DAY},
            {"version": 1, "basis": self._VERSIONS._KST_DAY},
            {"version": 1, "basis": self._VERSIONS._UTC_DAY},
            {"version": 2},
        ]
        expected = [self.packet(**form) for form in forms]
        with self._p2_source_unreadable():
            for form, before in zip(forms, expected):
                with self.subTest(**form):
                    basis = form.get("basis", self._VERSIONS._KST_DAY)
                    rebuilt = MODULE.build_packet(
                        "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT,
                        runtime_regime_readiness_version=form["version"],
                        summary_row_date_basis=basis,
                    )
                    self.assertEqual(rebuilt, before)
                    self.assertNotIn(self.KEY, rebuilt["frozen_sources"])
                    self.assertEqual(
                        self._p2_reasons(rebuilt),
                        P2_ROTATION_STATE_GENERIC_REASONS,
                    )
            # Their persisted bytes replay the same way, with the P2 source
            # still unreachable.
            for before in expected:
                self.assertEqual(
                    MODULE.validate_packet(copy.deepcopy(before)), before
                )

    def test_old_versions_reject_an_injected_v3_input_instead_of_promoting_it(self):
        envelope = copy.deepcopy(
            self.packet(version=3)["frozen_sources"][self.KEY]
        )
        for version in (None, 1, 2):
            with self.subTest(version=version):
                with self.assertRaisesRegex(
                    MODULE.DailyOrchestratorError,
                    "P2_ROTATION_STATE_READINESS_INPUTS_NOT_SUPPORTED",
                ):
                    MODULE.build_packet(
                        "morning", DECISION_DATE, NATURAL_MORNING_GENERATED_AT,
                        runtime_regime_readiness_version=version,
                        frozen_sources={self.KEY: envelope},
                    )

        # Injecting the key into an already-issued version-2 packet, and
        # re-signing it, is an incompatible version/input combination -- not a
        # promotion to the newer derivation.
        issued_v2 = copy.deepcopy(self.packet(version=2))
        injected = copy.deepcopy(issued_v2)
        injected["frozen_sources"][self.KEY] = envelope
        self._resign(injected)
        with self.assertRaisesRegex(
            MODULE.DailyOrchestratorError,
            "P2_ROTATION_STATE_READINESS_INPUTS_NOT_SUPPORTED",
        ):
            MODULE.validate_packet(injected)
        # The genuine version-2 packet is untouched and still replays exactly.
        self.assertEqual(
            MODULE.validate_packet(copy.deepcopy(issued_v2)), issued_v2
        )
        self.assertEqual(issued_v2, self.packet(version=2))

    def test_a_forged_version_three_marker_cannot_promote_a_legacy_packet(self):
        for version in (None, 1, 2):
            with self.subTest(version=version):
                promoted = copy.deepcopy(self.packet(version=version))
                promoted["runtime_regime_readiness_version"] = 3
                self._resign(promoted)
                with self.assertRaisesRegex(
                    MODULE.DailyOrchestratorError,
                    "P2_ROTATION_STATE_READINESS_INPUTS_NOT_FROZEN",
                ):
                    MODULE.validate_packet(promoted)

    # -- consumer lineage ---------------------------------------------------

    def test_version_three_blockers_reach_summary_and_flow_first(self):
        packet = self.packet(version=3)
        rows = self._rows(packet)
        posture = rows["STRATEGIC_CAPITAL_POSTURE"]["packet"]
        summary_packet = rows["ACTION_RISK_PORTFOLIO_SUMMARY"]["packet"]
        embedded = summary_packet["source_packets"]["STRATEGIC_CAPITAL_POSTURE"]

        self.assertEqual(embedded["packet_sha256"], posture["packet_sha256"])
        self.assertEqual(
            embedded["unavailable_reasons"]["P2_ROTATION_STATE"],
            P2_ROTATION_STATE_EXACT_REASONS,
        )
        self.assertEqual(
            summary_packet["lineage"]["source_packet_sha256"][
                "STRATEGIC_CAPITAL_POSTURE"
            ],
            posture["packet_sha256"],
        )
        self.assertEqual(
            summary_packet["status"],
            "ACTION_RISK_PORTFOLIO_PRESENTED_NO_ACTION_AUTHORITY",
        )
        for row in summary_packet["actions"]:
            self.assertEqual(row["evaluation_status"], "NOT_EVALUATED")
            self.assertIsNone(row["action"])

        for component_id in CAPITAL_ACTION_COMPONENTS:
            self.assertEqual(rows[component_id]["as_of_date"], DECISION_DATE)
            self.assertFalse(rows[component_id]["decision_eligible"])
            self.assertFalse(rows[component_id]["action_eligible"])
            self.assertFalse(rows[component_id]["order_eligible"])
        section = _capital_action_section(packet)
        self.assertEqual(section["status"], "PENDING")
        self.assertEqual(section["as_of_date"], DECISION_DATE)
        self.assertEqual(section["unknown_reason"], "SOURCE_COMPONENT_NOT_READY")
        self.assertFalse(section["decision_eligible"])
        self.assertFalse(section["action_eligible"])
        self.assertFalse(section["order_eligible"])

    def test_a_genuine_date_mismatch_still_blocks_the_section_under_version_three(
        self,
    ):
        # The diagnostic blockers do not soften the aggregator: relabel the
        # summary row onto a genuinely different date and the section must
        # refuse to present, while the packet itself stops revalidating.
        self.assertNotEqual(NATURAL_MORNING_GENERATED_AT[:10], DECISION_DATE)
        mismatched = RuntimeRegimeReadinessDerivationVersionTests._relabel_summary_row(
            copy.deepcopy(self.packet(version=3)), NATURAL_MORNING_GENERATED_AT[:10]
        )
        self._resign(mismatched)
        section = _capital_action_section(mismatched)
        self.assertEqual(section["status"], "DATA_BLOCKED")
        self.assertEqual(section["unknown_reason"], "SOURCE_AS_OF_DATE_MISMATCH")
        self.assertIsNone(section["as_of_date"])
        self.assertFalse(section["action_eligible"])
        with self.assertRaisesRegex(MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"):
            MODULE.validate_packet(mismatched)

    def test_the_semantic_fingerprint_sees_blockers_only(self):
        # The P7-12 packet carries the reasons; the commit, blob oids and
        # base64 bytes stay in top-level frozen_sources, so a consumer that
        # fingerprints the posture packet cannot pick up source identity.
        packet = self.packet(version=3)
        envelope = packet["frozen_sources"][self.KEY]
        posture_body = MODULE.canonical_json(self._posture(packet))
        self.assertNotIn(envelope["source_commit"], posture_body)
        for entry in envelope["files"].values():
            if entry["state"] != "PRESENT":
                continue
            self.assertNotIn(entry["blob_oid"], posture_body)
            self.assertNotIn(entry["content_base64"][:64], posture_body)

        # Same repository semantics on a different slot and invocation day:
        # the blockers, and therefore the fingerprint input, do not move.
        evening = self.packet(
            version=3, slot="evening", generated_at=EVENING_GENERATED_AT
        )
        self.assertNotEqual(
            NATURAL_MORNING_GENERATED_AT[:10], EVENING_GENERATED_AT[:10]
        )
        self.assertEqual(
            self._p2_reasons(evening), self._p2_reasons(packet)
        )



# Independently frozen pre-P2 full packet bytes; never regenerate with patched code.
_PRE_P2_REPLAY_FIXTURES_LZMA_BASE64 = (
    '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'
    '2eoILKQT5XGNiAJQlqXZo67EpM6zYU87wkmskSuzWSZB3mN4quTOmiNFH54k/Fc/eUkdR6+L/RuKLmiYeM4scURmr+Qr9BgbQ3/q'
    'IMdrGYFYmO9bCZj5yUbWBmdPHVQCbIZI2tPwB7HE7hmD8ZKeyl4A9seFmAHbsMpzG7al63ckRv30ZwJ9T8oVdW3ymRo0Xqm0v69u'
    'PqZn6k6pKvmZaskPS6BzrYSOd41pLs+ab0cqnBsYTIW7C2jJ6z63+7Do1UjPa0vkKXfv50UoNq0seVbaJ+gtCmPAqIlCzluW/Uwl'
    'qQWDX0CyE32MAuWeaSKqEvfxfb7GLSBTEUPxkYhFQ8N1yeQMk34CVDOy8zm9+H3wiClCLXyo/xEmQMZEXySAH6eZ5ayQEw47/IUH'
    '3xZSSahmKgpfekiPFfPk0Jvq6M7UfPuu8wwqm1I/l+NrZki4yONgjcak0qxIVovURG5EEIFNsraFwy/Hl02+jxsf9d9H1635trHY'
    'oNc5EszleI8KqiCuDUmjAFB4PrmerakWGe8UBnQ16BqZ6kwLczGe2TS4Sshi80RdySuBqha93BvqrHziPLQuF3BB9eN2ZJdWat/I'
    '5Df6bboEmwGAKOATpxjKVSeJaj7mvUQijut70sUzM+HAGXGCUNe4PbXQkahBQM4lobzRc+I6QNODFk7QjJ1CS9cbZZpyhHBCgDtZ'
    'lvn98hCjLhKuabva+YjfIw+XMaQaJpu/vFxOJyqKvWSQAnBK41HNppCk0uKbikNUfaxVFHK2QBT2HpuO8uO6YfiaZhyIsxfaD2Nq'
    '/sCn+MtxorW0rpdyAEyiDigO1+lbd7P4bfpzIQ7l62AsXmAsn33RNz3OP+rdI+CAVaw/vVUyEgYKMNVJJXfg931rGoruNFw+mjDe'
    'FEq8JavNV5hnV+Yje+JHxS8VbdyiPPvV66/2WTCXKsOqdvmZXXbiCGZ5u5Fsc35HOJd86wk7fdZ3AjTJEJUWfcWEDZY8pF70+6GV'
    'Slsl9RC7T8prayl4NyzwPXp00LGgfp92M778bts2uP7nlmvwY9dtUKXAAFb06bEpTGEEqC68RKerui4nV0mf5CjzeWZ6sFauai2G'
    '/30oDcJEK4yX1VxCVxGVUGNz4896iQ9UC/+tIv9qhtAuPgXT6rQsLEnWu1kzj/RSqLyxH6rpdebHJpVq6g3N6XXaQhzz7OqqplFG'
    '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'
    'CbFNzpyfLHtj84qSTZEzwzQMsoTQoldD9DQO5yzgQyzSIqasG2DJ/iiS76bjEJd7ks9zm2p5wsCq+56PP2ZJPZyDgKF8rGtXYXPl'
    'TR1rPn1xPn1R8L3tUaWiG2G0STVwnNekBoqBa6uetct1mvPC89hZRr8xjp9MJ8UTGLtruB0cs9pWYCGH61EwXc53Ijzh0ERKIf66'
    'tbDgBE9/Ibk/8pS5JypTk8d0yc5f+ESgwvqiEcys6bagd2qVLQmdAsauOVClldP9si45dnylqDF7TyEwVDfQiCUrd9qGEdEIZ5r4'
    'UgH7oBfWmFh7A0eCvH4gszVk0q71MKbFfOwikuN7EcXS3vh5ORipy7WnYt/qd5NVxwCjYEoKbtssKbd16fPa+WLFLWMVCHtbtqd3'
    'K5NI3MLJCTzOXz5MEaSqtrdgq7d53sMji4/5QIwlZaTWRbBBqB2IOUlT0FY0L1gHHxRHNGxNn3F++rw1qJwofaSSmpKvu9hvv6p8'
    'UJrGD7TnOz8T53oEegSln0E0sqRLkgse9Zje4l/Gosw1qXfTEXZbr7yt24f6kioHB+DYkxHEdcqsxQ/c2VD1dD9hxU+JmjVCAHZQ'
    'Zi+vpbqKdmP+6CuF+EfgSOWWxneWNr0rUSLlgQPuy4yYKmNK86wvVdCbuVXQYepEMX3jwVHXMsCMekAn75nOzTBjZWJKzTVd9SGB'
    'N9MEo59RzWC7j//GL6F7MW0yr7WPBPgG0/+MgiAa0U5cjECER0uyByEv8MQbopvlpbQ9931Rw9xIFU8A6QtXe8FgA6jUrx4XUeOE'
    'F2vSXbigk6rjFi/0d1cz92a5ee5rHItgqQy3P9v5nP14c/Nsn3eqOj4h0evZF5Nqbydzfjya17y7nlUk1puTCEuqWxWMp626ard6'
    'uVVNo19YcE6XZK3nX2ZZ4a8tNpeat+kakK8YEu/SSJ+M3SPu4YcdM8tdDgPHNUyNc3A94nTYz8rgTj+/eAdBGS0g/lDdGS+JDIWv'
    'Te9D5XS2/wFgZRS6eiYX0+nKdta9z5ZGh7zj6EKB6hbMfQVSvBQuGCKsP1HP7BZshwVVBwWthYR31bfqU6km6w6lB8y2tE8+tUiu'
    '1Rd/KobkS2MeoEYj4VTKvP4IFiQ5Q2U3jz9NzeNmPp/xf/5Pszrr8wxghc5R2FBtzFR3EbIeES8VVxO+055bcWyTe8zVAY4fBwXm'
    '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'
)


# ---------------------------------------------------------------------------
# Durable replay of packets frozen BEFORE the P2_ROTATION_STATE binding existed
# ---------------------------------------------------------------------------
#
# The suites above prove the legacy forms still build, but they do it by
# REBUILDING both sides with today's code: one shared regression could move the
# expectation and the result together and stay green. The capsules below close
# that hole. They hold the ORIGINAL UTF-8 file text of packets produced
# independently of this change, replayed here as bytes -- decoded, checked
# against digests pinned as immutable literals, then handed to the real
# validator for a complete-packet comparison. No expected value here is derived
# from the patched builder, and the opaque data literal above is used as data
# only: never regenerated, reformatted or rebuilt from live code.
#
# Provenance, stated exactly, because two of these are different kinds of
# object:
#   * the four absent/version-1 entries are preserved historical full packets;
#   * `pre-p2-v2-actual-am-inputs` is an independent PRE-P2 BASELINE, not an
#     issued packet: the unmodified PR598 producer (head 9df8a7e1) rebuilt from
#     the actual AM run's frozen inputs, reproducing that run's prior 5276bd6a
#     receipt. The AM packet actually archived in public is a version-1 packet.
#     No version-2 packet was ever issued there, and nothing below claims one
#     was.
# ---------------------------------------------------------------------------

_PRE_P2_REPLAY_CAPSULES = (
    {
        "name": "pre597-absent",
        "kind": "PRESERVED_HISTORICAL_PACKET",
        "version": None,
        "file_sha256":
            "7b42d531cc78597c5094c0dc4fd11c3ae5f878b6afe5df8fec101aa1ecee86ef",
        "packet_sha256":
            "68112b60e3db560ec0a72801c966e50daecaef6fa112e05ad530a98112cc9b9d",
    },
    {
        "name": "pre597-v1",
        "kind": "PRESERVED_HISTORICAL_PACKET",
        "version": 1,
        "file_sha256":
            "c8aa7d9e086e43d9fcde4ad10c079b3dc7b17c3b6f69d63a41468abbf7394718",
        "packet_sha256":
            "c737b8d2eb5fc37bbfc835bcfc2b139f29422c5153127a5d1c039a56915cd9bf",
    },
    {
        "name": "post597-absent",
        "kind": "PRESERVED_HISTORICAL_PACKET",
        "version": None,
        "file_sha256":
            "21b07b65ba32090198f724d59bbb3549eddab9b79693d24a0cc96c3832bd6b3a",
        "packet_sha256":
            "5746015f60f94ce2b90325e926f95ef268bbdde4b2d3565a4790f15599d3e5d9",
    },
    {
        "name": "post597-v1",
        "kind": "PRESERVED_HISTORICAL_PACKET",
        "version": 1,
        "file_sha256":
            "67b5bf3ccdfadeee2e14ec380c3c82be9657978e326e7fd7d598974f1987b04b",
        "packet_sha256":
            "6ffbce8763a3e5fabf82749450cce9ec1c5eb14e7e2e83134cd484c91605197b",
    },
    {
        "name": "pre-p2-v2-actual-am-inputs",
        "kind": "INDEPENDENT_PRE_P2_BASELINE_NOT_ISSUED",
        "version": 2,
        "file_sha256":
            "860bf9a012b515012592ac6aa5dda5d765dd3522ae80eb8ecca2a30f98133c1f",
        "packet_sha256":
            "5276bd6aea4786da77a3c43a06f1990e8ecae2c63c2550b005b15dcd9264e03b",
    },
)


def _decode_pre_p2_replay_fixtures() -> dict:
    """Fixture name -> ORIGINAL UTF-8 file text, from the embedded capsule.

    Pure data handling: base64, then lzma, then JSON. It executes nothing from
    the capsule and consults no live builder.
    """
    raw = lzma.decompress(
        base64.b64decode(_PRE_P2_REPLAY_FIXTURES_LZMA_BASE64, validate=True)
    )
    fixtures = json.loads(raw.decode("utf-8"))
    if not isinstance(fixtures, dict):
        raise AssertionError("pre-P2 replay capsule must decode to a JSON object")
    return fixtures


def _independent_canonical_sha256(value) -> str:
    """The packet self-hash rule, recomputed WITHOUT calling the module.

    Deliberately a second implementation of the same canonical form. If the
    production hasher or its canonical JSON ever drifts, the pinned digests stop
    matching instead of quietly following it.
    """
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class PreP2FrozenPacketReplayTests(unittest.TestCase):
    """Independently frozen pre-P2 packet BYTES still replay exactly.

    Every expectation is a pinned digest or the frozen bytes themselves, so a
    regression that changed both the builder and its rebuilt expectation would
    still be caught here.
    """

    KEY = MODULE.P2_ROTATION_STATE_READINESS_INPUTS
    LEGACY = tuple(
        capsule for capsule in _PRE_P2_REPLAY_CAPSULES if capsule["version"] != 2
    )
    V2_BASELINE = next(
        capsule for capsule in _PRE_P2_REPLAY_CAPSULES if capsule["version"] == 2
    )
    # The sibling suite's guard, reused verbatim rather than re-stated: inside
    # it every route to the P2 readiness source -- capture, frozen evaluation,
    # live producer run, committed-HEAD check and file read -- raises.
    _p2_source_unreadable = P2RotationStateDiagnosticBindingTests._p2_source_unreadable

    @classmethod
    def setUpClass(cls):
        cls.fixtures = _decode_pre_p2_replay_fixtures()

    # -- capsule access ------------------------------------------------------

    def _capsule_text(self, name):
        """The original file text for one capsule, by fixture identity.

        The preserved artifact's basename is accepted as an alias so the map is
        addressed by fixture name rather than by a filename convention; a name
        that resolves to neither is a hard failure.
        """
        for key in (name, f"{name}.json"):
            if key in self.fixtures:
                return self.fixtures[key]
        raise AssertionError(f"frozen capsule {name!r} missing from the constant")

    @staticmethod
    def _archived_packet(document, packet_sha256):
        """The packet inside a preserved file, selected by its pinned digest.

        Selection is by immutable hash, never by shape guessing, and a document
        that carries no object with that digest fails loudly -- so this cannot
        quietly promote some other value into the replay below.
        """
        candidates = [document]
        candidates.extend(
            value for value in document.values() if isinstance(value, dict)
        )
        for candidate in candidates:
            if candidate.get("packet_sha256") == packet_sha256:
                return candidate
        raise AssertionError(
            f"no packet with pinned sha256 {packet_sha256} in the frozen file"
        )

    def capsule_packet(self, capsule):
        """Decode, hash-check and return one capsule's frozen packet."""
        name = capsule["name"]
        text = self._capsule_text(name)
        self.assertIsInstance(text, str, name)
        self.assertEqual(
            hashlib.sha256(text.encode("utf-8")).hexdigest(),
            capsule["file_sha256"],
            f"{name}: frozen file bytes do not match their pinned digest",
        )
        document = json.loads(text)
        self.assertIsInstance(document, dict, name)
        packet = self._archived_packet(document, capsule["packet_sha256"])

        unsigned = copy.deepcopy(packet)
        self.assertEqual(
            unsigned.pop("packet_sha256", None), capsule["packet_sha256"], name
        )
        # The packet's own identity, recomputed twice: once here, once with the
        # production hasher. Both must equal the pinned literal.
        self.assertEqual(
            _independent_canonical_sha256(unsigned), capsule["packet_sha256"], name
        )
        self.assertEqual(MODULE.payload_sha256(unsigned), capsule["packet_sha256"], name)
        return packet

    @staticmethod
    def _posture_packet(packet):
        rows = {row["component_id"]: row for row in packet["components"]}
        return rows["STRATEGIC_CAPITAL_POSTURE"]["packet"]

    # -- the frozen bytes ----------------------------------------------------

    def test_the_capsule_holds_exactly_the_five_pinned_original_files(self):
        self.assertEqual(len(self.fixtures), len(_PRE_P2_REPLAY_CAPSULES))
        digests = set()
        for capsule in _PRE_P2_REPLAY_CAPSULES:
            with self.subTest(capsule=capsule["name"]):
                text = self._capsule_text(capsule["name"])
                self.assertIsInstance(text, str)
                self.assertEqual(
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    capsule["file_sha256"],
                )
                digests.add(capsule["file_sha256"])
        # Five distinct preserved files, not one file counted five times.
        self.assertEqual(len(digests), len(_PRE_P2_REPLAY_CAPSULES))

    def test_every_capsule_is_a_full_pre_p2_packet_with_its_own_version_marker(self):
        for capsule in _PRE_P2_REPLAY_CAPSULES:
            with self.subTest(capsule=capsule["name"]):
                packet = self.capsule_packet(capsule)
                self.assertLessEqual(
                    {
                        "slot", "decision_date", "generated_at", "components",
                        "frozen_sources", "packet_sha256",
                    },
                    set(packet),
                )
                # A full orchestrator packet, not a fragment: the frozen input
                # every validated packet must carry is present.
                self.assertIn("DYNAMIC_CLOCK", packet["frozen_sources"])
                # ...and the P2 input is not, because all five predate it.
                self.assertNotIn(self.KEY, packet["frozen_sources"])

                if capsule["version"] is None:
                    self.assertNotIn("runtime_regime_readiness_version", packet)
                else:
                    marker = packet["runtime_regime_readiness_version"]
                    self.assertEqual(marker, capsule["version"])
                    self.assertIs(type(marker), int)
                self.assertEqual(
                    self._posture_packet(packet)["unavailable_reasons"][
                        "P2_ROTATION_STATE"
                    ],
                    P2_ROTATION_STATE_GENERIC_REASONS,
                )

    # -- replay --------------------------------------------------------------

    def test_the_four_historical_packets_replay_byte_identically(self):
        """The absent/version-1 forms are re-derived, not merely re-read.

        `validate_packet` rebuilds each one completely and accepts only total
        equality, so this compares today's derivation against bytes frozen
        before the change -- while every route to the new P2 source raises.
        """
        self.assertEqual(len(self.LEGACY), 4)
        packets = [self.capsule_packet(capsule) for capsule in self.LEGACY]
        with self._p2_source_unreadable():
            for capsule, frozen in zip(self.LEGACY, packets):
                with self.subTest(capsule=capsule["name"]):
                    replayed = MODULE.validate_packet(copy.deepcopy(frozen))
                    self.assertEqual(replayed, frozen)
                    self.assertEqual(
                        _independent_canonical_sha256(
                            {
                                key: value for key, value in replayed.items()
                                if key != "packet_sha256"
                            }
                        ),
                        capsule["packet_sha256"],
                    )

    def test_the_pre_p2_version_two_baseline_replays_byte_identically(self):
        """An independent pre-P2 version-2 baseline, not an issued packet.

        This is the unmodified PR598 producer rebuilt from the actual AM run's
        frozen inputs, reproducing that run's prior 5276bd6a receipt. The AM
        packet actually archived in public is a version-1 packet; this fixture
        is evidence about the version-2 derivation, not evidence of issuance.
        """
        capsule = self.V2_BASELINE
        self.assertEqual(capsule["kind"], "INDEPENDENT_PRE_P2_BASELINE_NOT_ISSUED")
        frozen = self.capsule_packet(capsule)
        self.assertEqual(frozen["runtime_regime_readiness_version"], 2)
        with self._p2_source_unreadable():
            self.assertEqual(MODULE.validate_packet(copy.deepcopy(frozen)), frozen)

    def test_a_frozen_packet_cannot_be_promoted_by_rewriting_its_p2_reasons(self):
        # Proves the replay above is a real byte comparison rather than a
        # vacuous acceptance: give a frozen legacy packet the new exact
        # blockers, re-sign it so its self-hash is consistent, and the rebuild
        # still refuses it.
        capsule = self.LEGACY[0]
        frozen = self.capsule_packet(capsule)
        promoted = copy.deepcopy(frozen)
        self._posture_packet(promoted)["unavailable_reasons"]["P2_ROTATION_STATE"] = list(
            P2_ROTATION_STATE_EXACT_REASONS
        )
        RuntimeRegimeReadinessDerivationVersionTests._resign(promoted)
        self.assertNotEqual(promoted["packet_sha256"], capsule["packet_sha256"])
        with self.assertRaisesRegex(MODULE.DailyOrchestratorError, "OUTPUT_MISMATCH"):
            MODULE.validate_packet(promoted)
        # The untouched original is unaffected and still replays.
        with self._p2_source_unreadable():
            self.assertEqual(MODULE.validate_packet(copy.deepcopy(frozen)), frozen)


if __name__ == "__main__":
    unittest.main()
