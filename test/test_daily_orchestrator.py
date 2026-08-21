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
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

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
# LIVE_READY sensor exercised by the orchestrator.
DECISION_DATE = "2026-08-21"
MORNING_GENERATED_AT = "2026-08-21T12:00:00Z"
EVENING_GENERATED_AT = "2026-08-21T09:30:00Z"  # 18:30 KST


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
    def test_morning_build_against_real_evidence_has_no_degraded_components(self):
        packet = MODULE.build_packet("morning", DECISION_DATE, MORNING_GENERATED_AT)
        counts = packet["component_status_counts"]
        self.assertEqual(counts["DEGRADED"], 0)
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
        for component_id in (
            "STEP0_READ_MODEL_HEALTH", "KRX_PREOPEN_COMPACT",
            "US_BREADTH_MEMBERSHIP", "BTC_TREND", "BTC_RISK",
            "STABLECOIN_NET_ISSUANCE",
        ):
            self.assertEqual(
                by_id[component_id]["status"], "READY", component_id
            )
        # Genuinely unratified/unpopulated components must say so honestly,
        # never silently disappear or read as READY.
        self.assertEqual(by_id["PORTFOLIO_BUCKET"]["status"], "POLICY_BLOCKED")
        self.assertEqual(by_id["RULE_EVALUATION"]["status"], "POLICY_BLOCKED")
        self.assertIn(
            by_id["CRYPTO_BREADTH"]["status"], ("POLICY_BLOCKED", "READY")
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
        future_relative_to_capture = MODULE.build_packet(
            "morning", never_captured_date, "2026-08-21T12:00:00Z"
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
                "STABLECOIN_NET_ISSUANCE", "CRYPTO_BREADTH",
            }),
        )
        # Built late in the day (not MORNING_GENERATED_AT) so that no real
        # sensor's own genuine capture/observation timestamp (e.g. KOFIA's
        # real captured_at_utc, which can legitimately land well into the
        # KST evening) trips the _enforce_temporal_boundary source-
        # generated_at check below and confuses this test's focus, which is
        # frozen-source re-derivability, not the temporal boundary itself
        # (covered separately).
        late_generated_at = f"{DECISION_DATE}T23:59:00Z"
        boundaries = MODULE.build_packet(
            "morning", DECISION_DATE, late_generated_at
        )["unresolved_boundaries"]
        for stale_boundary in (
            "ROLLING_POINTER_COMPONENTS_MAY_FAIL_LATER_REVALIDATION_NOT_TAMPER",
            "SAME_DAY_ROLLING_POINTER_COMPONENTS_NOT_INDEPENDENTLY_REVALIDATED",
        ):
            self.assertNotIn(stale_boundary, boundaries)

        packet = MODULE.build_packet("morning", DECISION_DATE, late_generated_at)
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)
        self.assertIn("frozen_sources", packet)
        self.assertEqual(set(packet["frozen_sources"]), MODULE.FROZEN_SOURCE_COMPONENTS)
        # All of them now honestly claim genuine re-derivability, like
        # every other component (KRX_PREOPEN_COMPACT rides on STEP0's own
        # freeze).
        by_id = {row["component_id"]: row for row in packet["components"]}
        for component_id in MODULE.FROZEN_SOURCE_COMPONENTS | {"KRX_PREOPEN_COMPACT"}:
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

    def test_step0_revisions_validate_across_a_rolling_pointer_change_without_fault_injection(
        self,
    ):
        # rev-001 published, the live rolling pointer changes (simulated),
        # rev-002 published -- then BOTH revisions must independently
        # validate with NO fault injection or monkeypatch active at
        # validation time: validate_packet() must never touch the live,
        # currently-real data/ pointer for these rows at all, only each
        # packet's own frozen_sources.
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original_evaluate = MODULE.BRIEFING_READINESS.evaluate

            first = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
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
                    "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
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
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp) / "daily_briefing"
            original_evaluate = MODULE.BRIEFING_READINESS.evaluate

            first = MODULE.publish(
                "morning", DECISION_DATE, MORNING_GENERATED_AT, evidence_root
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
                    "morning", DECISION_DATE, "2026-08-21T13:00:00Z", evidence_root
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
        # decision_date deliberately does not match generated_at's own date.
        packet = MODULE.build_packet(
            "morning", "2026-08-20", MORNING_GENERATED_AT
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
        self.assertEqual(by_id["BTC_TREND"]["status"], "READY")
        self.assertEqual(by_id["US_BREADTH_MEMBERSHIP"]["status"], "READY")
        # KRX_PREOPEN_COMPACT correctly reports DATA_BLOCKED (a collector
        # data failure -- the mismatched date -- not a read-model-only
        # DEGRADED), and ACTION_RISK_PORTFOLIO_SUMMARY cascades to DEGRADED
        # because its one required source (UNIFIED_DECISION) is unavailable.
        self.assertEqual(by_id["KRX_PREOPEN_COMPACT"]["status"], "DATA_BLOCKED")
        self.assertEqual(
            by_id["ACTION_RISK_PORTFOLIO_SUMMARY"]["status"], "DEGRADED"
        )
        self.assertEqual(packet["component_status_counts"]["DEGRADED"], 2)

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
        # while BTC_TREND is broken.
        MODULE.BTC_TREND.build_transform = _boom
        try:
            packet = MODULE.build_packet(
                "morning", DECISION_DATE, MORNING_GENERATED_AT
            )
        finally:
            MODULE.BTC_TREND.build_transform = original
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertEqual(by_id["BTC_TREND"]["status"], "DEGRADED")
        self.assertEqual(by_id["STEP0_READ_MODEL_HEALTH"]["status"], "READY")
        self.assertEqual(by_id["US_BREADTH_MEMBERSHIP"]["status"], "READY")

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
        for required in (
            "Data / Read-model health", "3-Market Regime", "Rotation / Theme",
            "Rule status", "Portfolio / Risk", "Decision & action boundary",
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
        self.assertIn(
            f"members={by_id['US_BREADTH_MEMBERSHIP']['packet']['member_count']}",
            rendered,
        )
        rule_summary = by_id["RULE_EVALUATION"]["packet"]["summary"]
        self.assertIn(
            f"PASS={rule_summary['PASS']} FAIL={rule_summary['FAIL']} "
            f"UNKNOWN={rule_summary['UNKNOWN']} UNDEFINED={rule_summary['UNDEFINED']}",
            rendered,
        )
        self.assertIn("coverage=0/5", rendered)
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
        regression = next(
            step for step in steps
            if step.get("name") == "Offline daily orchestrator regression"
        )
        self.assertIn("test_daily_orchestrator.py", regression["run"])
        publish = next(
            step for step in steps
            if step.get("name") == "Publish provider-free daily briefing packet"
        )
        command = publish["run"]
        self.assertIn("briefing/daily_orchestrator.py publish", command)
        self.assertIn("briefing/daily_orchestrator.py validate", command)
        # publish() itself decides whether a new revision is needed (same-
        # day recovery); the workflow must always call it rather than
        # skipping on bare directory presence, and must gate the commit on
        # created=true rather than "does the path already exist".
        self.assertNotIn("skipped_existing", command)
        self.assertIn("no_new_revision", command)
        self.assertIn('CREATED=$(echo "$OUTPUT" | sed -n \'s/^created=//p\')', command)
        self.assertIn('if [ "$CREATED" = "true" ]; then', command)
        self.assertIn("GITHUB_STEP_SUMMARY", command)
        self.assertLess(steps.index(regression), steps.index(publish))
        commit = next(
            step for step in steps if step.get("name") == "Commit immutable daily briefing bundle"
        )
        self.assertEqual(commit.get("if"), "steps.briefing.outputs.result == 'published'")
        # The whole decision_date directory must be staged, not just the
        # new rev-NNN/ subdirectory, so the sibling index.json (rewritten
        # on every new revision) is committed alongside it.
        self.assertIn('git add "$(dirname "$CAPTURE_PATH")"', commit["run"])

    def test_workflow_derives_slot_from_exact_cron_not_wall_clock_hour(self):
        # A large scheduler delay must not misclassify morning as evening
        # (or vice versa): the slot must come from the exact cron
        # expression GitHub reports fired (github.event.schedule), not from
        # what KST hour the runner happens to be executing at.
        schedule = WF.get("on", WF.get(True))["schedule"]
        self.assertEqual(
            {item["cron"] for item in schedule},
            {"5 22 * * 0-4", "30 9 * * 1-5"},
        )
        publish = next(
            step for step in WF["jobs"]["briefing"]["steps"]
            if step.get("name") == "Publish provider-free daily briefing packet"
        )
        command = publish["run"]
        self.assertIn("EVENT_SCHEDULE", publish.get("env", {}))
        self.assertIn('"5 22 * * 0-4") SLOT="morning"', command)
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
        self.assertIn("cron: '5 21 * * 0-4'", collect_yml)
        self.assertIn("cron: '5 7 * * 1-5'", krx_post_close_yml)


if __name__ == "__main__":
    unittest.main()
