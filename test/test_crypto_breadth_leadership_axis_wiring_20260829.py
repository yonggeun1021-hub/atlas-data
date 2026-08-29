#!/usr/bin/env python3
"""CIO item 3 (2026-08-29): CRYPTO_BREADTH coverage-ratio diagnostics and
CRYPTO_LEADERSHIP daily component-row wiring, over real committed evidence.

This suite does not re-derive or re-prove crypto_breadth.py's own
qualified_members() gate or crypto_leadership.py's own dual-window
build_transform() -- those are already covered by test/test_crypto_breadth.py
and test/test_crypto_leadership.py (including
test_crypto_leadership.py::test_thirty_days_observe_both_windows_independently,
which already proves the underlying dual-window mechanism reaches
OBSERVED_UNCLASSIFIED given sufficient real-shaped synthetic contiguous
history) and test/test_regime_live_axis_adapter.py's existing
test_crypto_leadership_defined_when_dual_window_observed_and_pit_valid /
test_crypto_breadth_defined_with_real_evidence_on_taxonomy_complete_day
(which already prove the axis layer resolves DEFINED given a READY/OBSERVED
row). Together those pre-existing tests already prove the "pass path is
reachable" requirement independently of this PR.

What is new in this PR, and what this suite actually covers:

1. CRYPTO_BREADTH's component row now also carries real, already-computed
   taxonomy-coverage diagnostics (never a new gate) -- proven against real
   2026-08-28 (full coverage) and 2026-08-29 (BTR PIT-effective-dating gap)
   committed evidence.
2. daily_orchestrator.py's build_packet() now actually calls the
   already-existing build_crypto_leadership()/_classify_crypto_leadership()
   functions (previously wired only into regime/crypto_live_component_
   registry.py's separate rebuild path, never into the main daily rows
   dict) -- proven against real 2026-08-29 committed evidence.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "briefing" / "daily_orchestrator.py"
SPEC = importlib.util.spec_from_file_location(
    "crypto_axis_wiring_20260829_daily_orchestrator", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

LIVE_AXIS = MODULE.LIVE_AXIS_ADAPTER
CRYPTO_BREADTH = MODULE.CRYPTO_BREADTH
CRYPTO_LEADERSHIP = MODULE.CRYPTO_LEADERSHIP

FULL_COVERAGE_DATE = "2026-08-28"  # real committed evidence, OBSERVED_UNCLASSIFIED
PARTIAL_COVERAGE_DATE = "2026-08-29"  # real committed evidence, TAXONOMY_COVERAGE_UNKNOWN
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"


@unittest.skipUnless(
    (RAW_ROOT / FULL_COVERAGE_DATE).is_dir()
    and (RAW_ROOT / PARTIAL_COVERAGE_DATE).is_dir(),
    "real evidence/crypto/breadth/raw committed snapshots not present",
)
class CryptoBreadthCoverageDiagnosticsTest(unittest.TestCase):
    def test_full_coverage_real_evidence_carries_real_diagnostics_no_fabrication(self):
        row = MODULE.build_crypto_breadth(FULL_COVERAGE_DATE)
        self.assertEqual(row["status"], "READY")
        packet = row["packet"]
        self.assertEqual(packet["status"], "OBSERVED_UNCLASSIFIED")
        self.assertEqual(packet["selected_asset_count"], 100)
        self.assertEqual(packet["target_asset_count"], 100)
        # known_eligible_count_so_far is only ever set by qualified_members()
        # on its early TAXONOMY_COVERAGE_UNKNOWN return path -- a real
        # OBSERVED_UNCLASSIFIED day never reaches that branch, so this must
        # stay None (not defaulted to 0 or to selected_asset_count).
        self.assertIsNone(packet["known_eligible_count_so_far"])
        self.assertEqual(packet["resolved_cutoff_slot_count"], 100)
        self.assertEqual(packet["taxonomy_unknown_before_cutoff_count"], 0)
        self.assertEqual(packet["taxonomy_unknown_before_cutoff_assets"], [])
        # No unresolved above-cutoff asset: every target slot is resolved.
        self.assertEqual(packet["coverage_ratio_bps"], 10000)

    def test_partial_coverage_real_evidence_stays_undefined_with_real_blocker(self):
        row = MODULE.build_crypto_breadth(PARTIAL_COVERAGE_DATE)
        self.assertEqual(row["status"], "POLICY_BLOCKED")
        self.assertEqual(row["reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        packet = row["packet"]
        self.assertEqual(packet["status"], "UNKNOWN")
        self.assertEqual(packet["target_asset_count"], 100)
        self.assertIsInstance(packet["known_eligible_count_so_far"], int)
        # known_eligible_count_so_far can legitimately already equal target
        # (100) here -- the scan does not stop at the first unresolved
        # candidate, it keeps filling slots from lower-ranked candidates
        # too. That is exactly why this must still stay UNKNOWN rather than
        # being treated as "close enough": BTR (rank 91) is ranked ABOVE at
        # least one of the 100 already-filled slots, so resolving it could
        # displace that lower-ranked member -- see qualified_members()'s
        # own docstring in .github/scripts/crypto_breadth.py.
        self.assertLessEqual(packet["known_eligible_count_so_far"], 100)
        self.assertGreaterEqual(packet["taxonomy_unknown_before_cutoff_count"], 1)
        self.assertIn("BTR", packet["taxonomy_unknown_before_cutoff_assets"])
        expected_resolved = packet["target_asset_count"] - packet["taxonomy_unknown_before_cutoff_count"]
        self.assertEqual(packet["resolved_cutoff_slot_count"], expected_resolved)
        expected_ratio = (expected_resolved * 10000) // packet["target_asset_count"]
        self.assertEqual(packet["coverage_ratio_bps"], expected_ratio)
        # An unresolved above-cutoff asset can no longer be displayed as
        # misleading 100% coverage.
        self.assertGreaterEqual(packet["coverage_ratio_bps"], 0)
        self.assertLess(packet["coverage_ratio_bps"], 10000)

    def test_gate_pass_fail_semantics_are_unchanged_by_the_diagnostics(self):
        """The diagnostics are additive-only. Confirms this by independently
        rebuilding the underlying crypto_breadth.py packet and checking its
        own status/unknown_reason still solely determines READY/POLICY_
        BLOCKED -- unaffected by whatever the new diagnostics compute."""
        for decision_date, expected_status in (
            (FULL_COVERAGE_DATE, "OBSERVED_UNCLASSIFIED"),
            (PARTIAL_COVERAGE_DATE, "UNKNOWN"),
        ):
            direct = CRYPTO_BREADTH.build_transform(RAW_ROOT / decision_date)
            row = MODULE.build_crypto_breadth(decision_date)
            self.assertEqual(direct["status"], expected_status)
            self.assertEqual(row["packet"]["status"], direct["status"])
            self.assertEqual(
                row["status"], "READY" if expected_status == "OBSERVED_UNCLASSIFIED" else "POLICY_BLOCKED"
            )

    def test_coverage_diagnostics_no_fabrication_when_universe_block_absent(self):
        diagnostics = MODULE._crypto_breadth_coverage_diagnostics({"status": "DEGRADED"})
        self.assertEqual(
            diagnostics,
            {
                "selected_asset_count": None,
                "target_asset_count": None,
                "known_eligible_count_so_far": None,
                "resolved_cutoff_slot_count": None,
                "taxonomy_unknown_before_cutoff_count": None,
                "taxonomy_unknown_before_cutoff_assets": None,
                "coverage_ratio_bps": None,
            },
        )

    def test_coverage_diagnostics_deterministic(self):
        first = MODULE.build_crypto_breadth(PARTIAL_COVERAGE_DATE)
        second = MODULE.build_crypto_breadth(PARTIAL_COVERAGE_DATE)
        self.assertEqual(first, second)

    def test_axis_layer_independently_rederives_the_same_diagnostics_and_matches(self):
        row = MODULE.build_crypto_breadth(FULL_COVERAGE_DATE)
        rows = {"CRYPTO_BREADTH": row}
        generated_at = f"{FULL_COVERAGE_DATE}T23:59:59Z"
        factors = LIVE_AXIS.build_axis_factors(rows, generated_at)
        self.assertEqual(factors["CRYPTO"]["BREADTH"]["status"], "DEFINED")

    def test_axis_layer_rejects_a_tampered_coverage_diagnostic(self):
        row = MODULE.build_crypto_breadth(FULL_COVERAGE_DATE)
        row = copy.deepcopy(row)
        row["packet"]["coverage_ratio_bps"] = 9999  # real value is 10000
        rows = {"CRYPTO_BREADTH": row}
        generated_at = f"{FULL_COVERAGE_DATE}T23:59:59Z"
        factors = LIVE_AXIS.build_axis_factors(rows, generated_at)
        self.assertEqual(factors["CRYPTO"]["BREADTH"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["BREADTH"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )


@unittest.skipUnless(
    (RAW_ROOT / PARTIAL_COVERAGE_DATE).is_dir(),
    "real evidence/crypto/breadth/raw committed snapshots not present",
)
class CryptoLeadershipRowWiringTest(unittest.TestCase):
    def test_build_packet_now_produces_a_real_crypto_leadership_row(self):
        generated_at = f"{PARTIAL_COVERAGE_DATE}T23:59:00Z"
        packet = MODULE.build_packet("morning", PARTIAL_COVERAGE_DATE, generated_at)
        self.assertIn("CRYPTO_LEADERSHIP", MODULE.load_contract()["component_order"])
        by_id = {row["component_id"]: row for row in packet["components"]}
        self.assertIn("CRYPTO_LEADERSHIP", by_id)
        row = by_id["CRYPTO_LEADERSHIP"]
        # Real natural history today is genuinely insufficient (30-day
        # primary window needs history back to config/crypto_leadership_
        # policy.json's effective_from=2026-08-19, not reachable yet) --
        # this MUST stay honestly blocked, never forced to READY.
        self.assertEqual(row["status"], "POLICY_BLOCKED")
        self.assertIn(
            row["reason"],
            (
                "DUAL_WINDOW_NATURAL_HISTORY_INCOMPLETE",
                "DUAL_WINDOW_SOURCE_POINT_UNKNOWN",
                "DUAL_WINDOW_NOT_OBSERVED",
            ),
        )
        self.assertTrue(row["validated"])
        self.assertIn("CRYPTO_LEADERSHIP", MODULE.FROZEN_SOURCE_COMPONENTS)
        self.assertIn("CRYPTO_LEADERSHIP", packet["frozen_sources"])

    def test_no_fabricated_field_in_the_leadership_row_packet(self):
        row = MODULE.build_crypto_leadership(PARTIAL_COVERAGE_DATE)
        # component_row()'s packet= argument for CRYPTO_LEADERSHIP is, and
        # stays, exactly {"status": ...} -- no invented threshold, score,
        # or window detail is ever copied up into the row.
        self.assertEqual(set(row["packet"]), {"status"})

    def test_leadership_row_wiring_is_deterministic(self):
        first = MODULE.build_packet(
            "morning", PARTIAL_COVERAGE_DATE, f"{PARTIAL_COVERAGE_DATE}T23:59:00Z"
        )
        second = MODULE.build_packet(
            "morning", PARTIAL_COVERAGE_DATE, f"{PARTIAL_COVERAGE_DATE}T23:59:00Z"
        )
        first_leadership = next(
            r for r in first["components"] if r["component_id"] == "CRYPTO_LEADERSHIP"
        )
        second_leadership = next(
            r for r in second["components"] if r["component_id"] == "CRYPTO_LEADERSHIP"
        )
        self.assertEqual(first_leadership, second_leadership)
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])

    def test_built_packet_round_trips_validate_packet(self):
        packet = MODULE.build_packet(
            "morning", PARTIAL_COVERAGE_DATE, f"{PARTIAL_COVERAGE_DATE}T23:59:00Z"
        )
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

    def test_axis_now_reads_the_wired_row_and_stays_undefined_for_a_genuine_reason(self):
        row = MODULE.build_crypto_leadership(PARTIAL_COVERAGE_DATE)
        rows = {"CRYPTO_LEADERSHIP": row}
        generated_at = f"{PARTIAL_COVERAGE_DATE}T23:59:00Z"
        factors = LIVE_AXIS.build_axis_factors(rows, generated_at)
        # Now fails via COMPONENT_NOT_READY (the row exists but is not
        # READY) rather than COMPONENT_MISSING (the row does not exist at
        # all) -- same UNDEFINED end state, but for the documented, current,
        # honest reason.
        self.assertEqual(factors["CRYPTO"]["LEADERSHIP"]["status"], "UNDEFINED")
        self.assertEqual(
            factors["CRYPTO"]["LEADERSHIP"]["warnings"],
            ["LIVE_AXIS_EVIDENCE_UNAVAILABLE"],
        )

    def test_finalized_candle_pit_boundary_current_candle_always_excluded(self):
        vintage = dt.date.fromisoformat(PARTIAL_COVERAGE_DATE)
        end_date = (vintage - dt.timedelta(days=1)).isoformat()
        packet = CRYPTO_LEADERSHIP.build_transform(RAW_ROOT, end_date=end_date)
        self.assertTrue(
            packet["current_candle"]["excluded_for_every_member_and_point"]
        )
        for key in (
            "leader_classification_authorized", "ranking_authorized",
            "threshold_authorized", "regime_score_authorized",
            "production_wiring_authorized", "trading_action_authorized",
        ):
            self.assertFalse(packet[key])

    def test_dual_window_natural_history_status_matches_real_committed_archive(self):
        """Documents precisely why today's real evidence is insufficient,
        so a reviewer does not have to re-derive it by hand: the primary_30d
        window needs 30 contiguous real evidence/crypto/breadth/raw days on
        or after config/crypto_leadership_policy.json's effective_from
        (2026-08-19); as of this PR only a partial run exists."""
        policy = CRYPTO_LEADERSHIP.load_leadership_policy()
        self.assertEqual(policy["approval_status"], "RATIFIED")
        effective_from = dt.date.fromisoformat(policy["effective_from"])
        committed_days = sorted(
            p.name for p in RAW_ROOT.iterdir() if p.is_dir()
        )
        as_of_days = {
            dt.date.fromisoformat(name) - dt.timedelta(days=1)
            for name in committed_days
        }
        real_days_since_effective = {
            day for day in as_of_days if day >= effective_from
        }
        self.assertLess(
            len(real_days_since_effective),
            30,
            "This test's own premise (natural history genuinely incomplete) "
            "no longer holds -- re-check whether CRYPTO/LEADERSHIP can now "
            "resolve DEFINED with real evidence before assuming it cannot.",
        )


if __name__ == "__main__":
    unittest.main()
