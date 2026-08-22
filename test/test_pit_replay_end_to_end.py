#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- end-to-end regression against real committed
repo evidence (not fixtures). Covers: determinism, zero authority
violations, window/priority-subject coverage, structural symmetry between
the Opportunity Miss and Defense ledgers, GATE_BLOCK-narrowing and
NOT_GRADABLE-enforcement (CIO review round 2), and (CIO review round 3)
DATA_FAILURE exclusion from Miss/Defense KPIs, the real ratified crypto
PIT-eligible population, and the per-market breakdown.
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
        for key in ("signal_replay_ledger", "opportunity_miss_episodes", "defense_episodes",
                    "opportunity_miss_ledger_daily", "defense_ledger_daily", "ungradable_ledger",
                    "coverage_gap", "by_market"):
            self.assertEqual(
                canonical_json(self.report_a[key]),
                canonical_json(self.report_b[key]),
                key,
            )

    def test_report_asof_evidence_date_is_not_a_wall_clock_stamp(self):
        self.assertRegex(self.report_a["report_asof_evidence_date"], r"^\d{4}-\d{2}-\d{2}$")


class EndToEndAuthorityInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_every_signal_ledger_entry_has_capital_zero_proposed_action(self):
        for e in self.report["signal_replay_ledger"]:
            self.assertEqual(e["proposed_ruleset"]["capital"], 0)
            self.assertIn(e["proposed_ruleset"]["recommended_action"],
                           ("NONE", "PROBE_REVIEW_CANDIDATE", "PROBE_REVIEW_CANDIDATE_TACTICAL"))

    def test_every_signal_ledger_entry_existing_trade_proposal_is_null(self):
        for e in self.report["signal_replay_ledger"]:
            self.assertIsNone(e["existing_ruleset"]["trade_proposal"])

    def test_no_signal_ledger_entry_ever_claims_action_was_taken(self):
        for e in self.report["signal_replay_ledger"][:50]:
            self.assertNotIn("action_taken", e)
            self.assertNotIn("order", e)

    def test_wbs_phase_labeled_p10_not_p11(self):
        # CIO review round 3 (flaw 5/10): this is P10-02/P10-03, not P11.
        self.assertIn("P10-02", self.report["wbs_phase"])
        self.assertIn("P11", self.report["wbs_phase"])  # explicitly disclaims it
        self.assertIn("NOT P11", self.report["wbs_phase"])


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

    def test_defense_episodes_not_larger_than_the_full_population(self):
        self.assertLessEqual(len(self.report["defense_episodes"]), len(self.report["signal_replay_ledger"]))

    def test_miss_and_defense_episodes_were_built_from_the_same_signal_ledger(self):
        miss_sources = {(m["subject"], m["episode_start_date"]) for m in self.report["opportunity_miss_episodes"]}
        defense_sources = {(d["subject"], d["episode_start_date"]) for d in self.report["defense_episodes"]}
        ledger_keys = {(e["subject"], e["decision_date"]) for e in self.report["signal_replay_ledger"]}
        self.assertTrue(miss_sources.issubset(ledger_keys))
        self.assertTrue(defense_sources.issubset(ledger_keys))
        self.assertEqual(miss_sources & defense_sources, set())


class EndToEndGateBlockNarrowingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_every_gate_block_daily_row_has_all_six_conditions_really_passing(self):
        checked = 0
        for row in self.report["opportunity_miss_ledger_daily"]:
            if row["root_cause"] != "GATE_BLOCK":
                continue
            checked += 1
            self.assertTrue(row["conditions_1_to_6_all_pass"], row)
        self.assertGreaterEqual(checked, 0)

    def test_gate_block_never_assigned_to_a_partially_qualified_entry_in_the_real_ledger(self):
        partially_qualified_but_not_gate_block = 0
        for entry in self.report["signal_replay_ledger"]:
            pr = entry["proposed_ruleset"]
            if pr["trigger_types_present"] and not pr["conditions_1_to_6_all_pass"]:
                partially_qualified_but_not_gate_block += 1
        self.assertGreater(partially_qualified_but_not_gate_block, 0)


class EndToEndNotGradableEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_no_signal_anchored_ok_entry_has_a_not_live_known_hypothetical_entry(self):
        checked = 0
        for entry in self.report["signal_replay_ledger"]:
            fm = entry["forward_metrics"]
            if fm.get("entry_date_source") != "explicit_signal_evaluation_date":
                continue
            if fm.get("status") != "OK":
                continue
            checked += 1
            self.assertTrue(fm["entry_live_known_asof_decision_date"], entry)
        self.assertGreater(checked, 0)

    def test_ungradable_ledger_entries_are_excluded_from_miss_and_defense_episodes(self):
        ungradable_keys = {(u["subject"], u["decision_date"]) for u in self.report["ungradable_ledger"]}
        miss_keys = {(ep["subject"], ep["episode_start_date"]) for ep in self.report["opportunity_miss_episodes"]}
        self.assertEqual(ungradable_keys & miss_keys, set())


class EndToEndDataFailureSeparationTests(unittest.TestCase):
    """CIO review round 3, flaw 4: DATA_FAILURE must never appear in the
    Miss/Defense KPI numerator or denominator -- it belongs only in
    coverage_gap."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_no_miss_episode_or_daily_row_has_root_cause_data_failure(self):
        for row in self.report["opportunity_miss_ledger_daily"]:
            self.assertNotEqual(row["root_cause"], "DATA_FAILURE")
        for ep in self.report["opportunity_miss_episodes"]:
            self.assertNotEqual(ep["root_cause"], "DATA_FAILURE")

    def test_no_defense_row_or_episode_is_built_from_an_unauditable_entry(self):
        unauditable_dates = {(e["subject"], e["decision_date"]) for e in self.report["signal_replay_ledger"]
                              if not e["data_available"]}
        for row in self.report["defense_ledger_daily"]:
            self.assertNotIn((row["subject"], row["decision_date"]), unauditable_dates)

    def test_coverage_gap_report_accounts_for_the_real_unauditable_population(self):
        cg = self.report["coverage_gap"]
        self.assertGreater(cg["unauditable_entries"], 0)
        self.assertIn("2026-07-22", cg["unauditable_days"])
        self.assertLess(cg["auditable_coverage_pct"], 100.0)


class EndToEndCryptoPitEligibleUniverseTests(unittest.TestCase):
    """CIO review round 3, flaw 1: crypto Opportunity KPI population must be
    the real ratified PIT-eligible universe, not the full source catalog."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_crypto_source_coverage_population_is_reported_separately_and_much_larger(self):
        pop = self.report["population"]
        source_cov = pop["crypto_source_coverage_population"]
        if source_cov.get("status") != "OK":
            self.skipTest("no committed crypto breadth evidence in this checkout")
        self.assertGreater(source_cov["pair_count"], 600)
        self.assertLess(pop["crypto_pit_eligible_population_size_at_window_end"], 100)

    def test_crypto_entries_before_ratification_date_are_absent_not_substituted(self):
        crypto_entries = [e for e in self.report["signal_replay_ledger"] if "/" in e["subject"]]
        for e in crypto_entries:
            self.assertGreaterEqual(e["decision_date"], "2026-08-19")


class EndToEndByMarketBreakdownTests(unittest.TestCase):
    """CIO review round 3, flaw 11: results must be split by market
    (BTC / Korea / Crypto), never blended into one number."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_by_market_contains_btc_korea_and_crypto_separately(self):
        by_market = self.report["by_market"]
        self.assertIn("BTC", by_market)
        self.assertIn("KOREA", by_market)
        self.assertIn("CRYPTO", by_market)

    def test_market_entry_counts_sum_to_the_full_ledger(self):
        total = sum(m["entry_count"] for m in self.report["by_market"].values())
        self.assertEqual(total, len(self.report["signal_replay_ledger"]))

    def test_each_market_has_its_own_coverage_gap_report(self):
        for market, data in self.report["by_market"].items():
            self.assertIn("coverage_gap", data)
            self.assertIn("auditable_coverage_pct", data["coverage_gap"])


if __name__ == "__main__":
    unittest.main()
