#!/usr/bin/env python3
"""P7-11 Baseline Audit -- `harvest_audit/population.py` real-evidence
population/survivorship-bias/coverage regression (B-8 items 3, 4, 7, 8, 9,
13, 17)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harvest_audit.population import (  # noqa: E402
    CATEGORY_DEFENSE, CATEGORY_HARVEST_OPPORTUNITY, CATEGORY_NOT_GRADABLE,
    MARKET_KPI_STATUS, build_episode_ledger, build_market_summary,
    build_signal_ledger_and_episodes,
)
from harvest_audit.scenario import MIN_SAMPLE_SIZE, build_scenario_comparisons  # noqa: E402


class RealPopulationTests(unittest.TestCase):
    """One real build, shared by every test in this module (population
    building is not cheap and every test below only reads it)."""

    @classmethod
    def setUpClass(cls):
        (cls.ctx, cls.signal_ledger, cls.miss_episodes, cls.defense_episodes,
         cls.coverage_gap) = build_signal_ledger_and_episodes()
        cls.episode_ledger = build_episode_ledger(cls.ctx, cls.signal_ledger,
                                                    cls.miss_episodes, cls.defense_episodes)
        cls.market_summary = build_market_summary(cls.episode_ledger)

    def test_population_is_non_trivial_real_evidence(self):
        self.assertGreater(len(self.episode_ledger), 0)


class FutureReturnBasedSelectionBlockedTests(RealPopulationTests):
    """Item 3: population selection never depends on a subject's future
    return -- the episode set is exactly PR #210's own Miss ∪ Defense
    episodes, nothing added or removed based on outcome size beyond PR
    #210's own pre-defined, ratified materiality threshold."""

    def test_episode_ledger_subject_set_is_exactly_miss_union_defense_subjects(self):
        expected_subjects = {e["subject"] for e in self.miss_episodes} | {e["subject"] for e in self.defense_episodes}
        actual_subjects = {r["subject"] for r in self.episode_ledger}
        self.assertEqual(actual_subjects, expected_subjects)

    def test_episode_count_matches_miss_plus_defense_episode_count_exactly(self):
        self.assertEqual(len(self.episode_ledger), len(self.miss_episodes) + len(self.defense_episodes))


class OutcomeBasedMoversExcludedFromOfficialKpiTests(RealPopulationTests):
    """Item 4: the official-KPI/diagnostic-cohort boundary per market is a
    STATIC classification (`MARKET_KPI_STATUS`), never derived from how
    large or small any individual episode's outcome was."""

    def test_market_kpi_status_is_a_static_constant_independent_of_episode_outcomes(self):
        # Re-running build_market_summary against an artificially inflated
        # (but structurally identical) episode set must not change the
        # per-market kpi_population_status/population_label at all.
        inflated = self.episode_ledger + self.episode_ledger  # duplicate outcomes, doubled magnitude of counts
        summary_real = build_market_summary(self.episode_ledger)
        summary_inflated = build_market_summary(inflated)
        for market in ("BTC", "KOREA", "CRYPTO"):
            self.assertEqual(summary_real[market]["kpi_population_status"],
                              summary_inflated[market]["kpi_population_status"])
            self.assertEqual(summary_real[market]["population_label"],
                              summary_inflated[market]["population_label"])

    def test_btc_is_the_only_market_with_an_ok_kpi_population_status(self):
        self.assertEqual(MARKET_KPI_STATUS["BTC"]["kpi_population_status"], "OK")
        self.assertNotEqual(MARKET_KPI_STATUS["KOREA"]["kpi_population_status"], "OK")
        self.assertNotEqual(MARKET_KPI_STATUS["CRYPTO"]["kpi_population_status"], "OK")


class KoreaWatchlistNeverLabeledHistoricalPitTests(unittest.TestCase):
    """Item 7: fails loudly if Korea's CURRENT watchlist is ever relabeled
    as a reconstructed historical PIT population."""

    def test_korea_population_label_says_current_watchlist_diagnostic_cohort(self):
        label = MARKET_KPI_STATUS["KOREA"]["population_label"]
        self.assertEqual(label, "CURRENT_WATCHLIST_DIAGNOSTIC_COHORT")
        self.assertNotIn("PIT_ELIGIBLE", label)
        self.assertNotIn("HISTORICAL", label)

    def test_korea_kpi_status_explicitly_says_not_computable(self):
        status = MARKET_KPI_STATUS["KOREA"]["kpi_population_status"]
        self.assertTrue(status.startswith("NOT_COMPUTABLE"), status)


class NoBackdatedCryptoEligibilityTests(RealPopulationTests):
    """Item 8: every CRYPTO episode's underlying series came from
    `ctx["breadth_series"]`, itself built ONLY from
    `asset_identity.crypto_pit_eligible_pair_ids` -- never the full raw
    Kraken catalog. This is a consistency check on top of PR #210's own
    (separately tested) `test_replay_asset_identity.py`."""

    def test_every_crypto_episode_subject_is_in_the_pit_eligible_breadth_series(self):
        crypto_rows = [r for r in self.episode_ledger if r["market"] == "CRYPTO"]
        for r in crypto_rows:
            self.assertIn(r["subject"], self.ctx["breadth_series"], r["subject"])


class DataFailureNeverBecomesHarvestOpportunityTests(RealPopulationTests):
    """Item 9: `root_cause="DATA_FAILURE"` must never appear tagged as
    HARVEST_OPPORTUNITY_DIAGNOSTIC anywhere in the ledger."""

    def test_no_data_failure_root_cause_is_ever_a_harvest_opportunity(self):
        offending = [r for r in self.episode_ledger
                     if r["diagnostic_category"] == CATEGORY_HARVEST_OPPORTUNITY
                     and r["root_cause"] == "DATA_FAILURE"]
        self.assertEqual(offending, [])

    def test_no_data_failure_root_cause_anywhere_in_the_ledger_at_all(self):
        # PR #210's own build_miss_records()/build_defense_records() already
        # exclude DATA_FAILURE rows entirely (flaw-4 fix) -- this proves
        # that exclusion survives all the way through this module's own
        # re-packaging, not merely at the source.
        offending = [r for r in self.episode_ledger if r["root_cause"] == "DATA_FAILURE"]
        self.assertEqual(offending, [])


class EpisodeDeduplicationTests(RealPopulationTests):
    """Item 13: every `episode_id` in the ledger is unique."""

    def test_episode_ids_are_unique(self):
        ids = [r["episode_id"] for r in self.episode_ledger]
        self.assertEqual(len(ids), len(set(ids)))


class InsufficientSampleNeverProducesOptimalAnswerTests(RealPopulationTests):
    """Item 17: when the gradable sample for a given early-exit horizon is
    below `MIN_SAMPLE_SIZE`, no aggregate "optimal"/average figure is ever
    produced -- only the honest `NOT_COMPUTABLE_INSUFFICIENT_SAMPLE`
    status."""

    def test_below_minimum_sample_never_carries_an_average_field(self):
        packet = build_scenario_comparisons(self.episode_ledger)
        for horizon, block in packet["by_early_exit_horizon"].items():
            s = block["aggregate_summary"]
            if s["sample_size"] < MIN_SAMPLE_SIZE:
                self.assertEqual(s["status"], "NOT_COMPUTABLE_INSUFFICIENT_SAMPLE")
                self.assertNotIn("avg_early_exit_opportunity_cost_pct", s)

    def test_artificially_small_population_never_fabricates_a_summary(self):
        harvest_rows = [r for r in self.episode_ledger
                         if r["diagnostic_category"] == CATEGORY_HARVEST_OPPORTUNITY][:1]
        packet = build_scenario_comparisons(harvest_rows)
        for horizon, block in packet["by_early_exit_horizon"].items():
            self.assertLess(block["aggregate_summary"]["sample_size"], MIN_SAMPLE_SIZE)
            self.assertEqual(block["aggregate_summary"]["status"], "NOT_COMPUTABLE_INSUFFICIENT_SAMPLE")


if __name__ == "__main__":
    unittest.main()
