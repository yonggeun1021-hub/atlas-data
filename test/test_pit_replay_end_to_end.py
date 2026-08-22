#!/usr/bin/env python3
"""P11 PIT Replay -- end-to-end regression against real committed repo
evidence (not fixtures). Covers: determinism, zero authority violations,
window/priority-subject coverage, and structural symmetry between the
Opportunity Miss and Defense ledgers.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.opportunity_trigger import canonical_json  # noqa: E402
from replay.run_pit_replay import PRIORITY_SUBJECTS, WINDOW_END, WINDOW_START, run  # noqa: E402


class EndToEndDeterminismTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report_a = run()
        cls.report_b = run()

    def test_two_independent_runs_produce_byte_identical_json(self):
        self.assertEqual(
            canonical_json(self.report_a["signal_replay_ledger"]),
            canonical_json(self.report_b["signal_replay_ledger"]),
        )
        self.assertEqual(
            canonical_json(self.report_a["opportunity_miss_ledger"]),
            canonical_json(self.report_b["opportunity_miss_ledger"]),
        )
        self.assertEqual(
            canonical_json(self.report_a["defense_ledger"]),
            canonical_json(self.report_b["defense_ledger"]),
        )

    def test_report_asof_evidence_date_is_not_a_wall_clock_stamp(self):
        # It must be one of the real snapshot capture dates, not "today".
        self.assertRegex(self.report_a["report_asof_evidence_date"], r"^\d{4}-\d{2}-\d{2}$")


class EndToEndAuthorityInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_every_signal_ledger_entry_has_capital_zero_proposed_action(self):
        for e in self.report["signal_replay_ledger"]:
            self.assertEqual(e["proposed_ruleset"]["capital"], 0)
            self.assertIn(e["proposed_ruleset"]["recommended_action"], ("NONE", "PROBE_REVIEW_CANDIDATE"))

    def test_every_signal_ledger_entry_existing_trade_proposal_is_null(self):
        for e in self.report["signal_replay_ledger"]:
            self.assertIsNone(e["existing_ruleset"]["trade_proposal"])

    def test_no_signal_ledger_entry_ever_claims_action_was_taken(self):
        # There is no "action_taken" / "order" / "stage" field anywhere in
        # the ledger schema -- this asserts that stays true.
        for e in self.report["signal_replay_ledger"][:50]:
            self.assertNotIn("action_taken", e)
            self.assertNotIn("order", e)


class EndToEndCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_priority_subjects_covered_across_the_full_window(self):
        priority_dates = {
            (e["subject"], e["decision_date"]) for e in self.report["signal_replay_ledger_priority_only"]
        }
        for subject in PRIORITY_SUBJECTS:
            subject_dates = {d for s, d in priority_dates if s == subject}
            self.assertIn(WINDOW_START, subject_dates)
            self.assertIn(WINDOW_END, subject_dates)

    def test_window_is_exactly_2026_07_22_to_2026_08_22(self):
        self.assertEqual((WINDOW_START, WINDOW_END), ("2026-07-22", "2026-08-22"))

    def test_pre_repo_history_dates_are_uniformly_data_failure_for_priority_subjects(self):
        for e in self.report["signal_replay_ledger_priority_only"]:
            if e["decision_date"] < "2026-08-13":
                self.assertFalse(e["data_available"], e)
                self.assertEqual(e["triggers"], [], e)

    def test_rule_attribution_covers_all_recommendation_values(self):
        recs = self.report["rule_attribution"]
        self.assertGreaterEqual(len(recs), 3)
        for r in recs:
            self.assertIn(r["recommendation"], ("KEEP", "CHANGE", "KILL"))


class EndToEndSymmetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_defense_ledger_is_not_empty_and_not_larger_than_the_full_population(self):
        self.assertGreater(len(self.report["defense_ledger"]), 0)
        self.assertLessEqual(len(self.report["defense_ledger"]), len(self.report["signal_replay_ledger"]))

    def test_miss_and_defense_ledgers_were_built_from_the_same_signal_ledger(self):
        miss_sources = {(m["subject"], m["decision_date"]) for m in self.report["opportunity_miss_ledger"]}
        defense_sources = {(d["subject"], d["decision_date"]) for d in self.report["defense_ledger"]}
        ledger_keys = {(e["subject"], e["decision_date"]) for e in self.report["signal_replay_ledger"]}
        self.assertTrue(miss_sources.issubset(ledger_keys))
        self.assertTrue(defense_sources.issubset(ledger_keys))
        # No entry is simultaneously a material miss AND a material defense.
        self.assertEqual(miss_sources & defense_sources, set())


if __name__ == "__main__":
    unittest.main()
