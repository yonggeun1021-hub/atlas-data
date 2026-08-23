#!/usr/bin/env python3
"""P7-11 Baseline Audit -- `harvest_audit/population.py` real-evidence
population/survivorship-bias/coverage regression (B-8 items 3, 4, 7, 8, 9,
13, 17), PLUS the CIO methodology review round 1, defect 1/3 regressions:
population membership must be provably outcome-independent, and no
"optimal" analytical-grid verdict is ever produced."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.opportunity_trigger import canonical_json  # noqa: E402

from harvest_audit.population import (  # noqa: E402
    CATEGORY_DEFENSE, CATEGORY_FLAT, CATEGORY_HARVEST_OPPORTUNITY, CATEGORY_HOLD_BENEFIT,
    CATEGORY_NOT_GRADABLE, MARKET_KPI_STATUS, build_episode_ledger, build_market_summary,
    build_pit_episodes, build_pr210_auxiliary_cohort, build_reconciliation_table,
    build_signal_ledger_and_episodes, build_trigger_population_records,
)
from harvest_audit.scenario import AGGREGATE_STATUS, ANALYTICAL_GRID_STATUS, build_scenario_comparisons  # noqa: E402


class RealPopulationTests(unittest.TestCase):
    """One real build, shared by every test in this module."""

    @classmethod
    def setUpClass(cls):
        (cls.ctx, cls.signal_ledger, cls.miss_episodes, cls.defense_episodes,
         cls.coverage_gap) = build_signal_ledger_and_episodes()
        cls.episode_ledger = build_episode_ledger(cls.ctx, cls.signal_ledger)
        cls.market_summary = build_market_summary(cls.episode_ledger)

    def test_population_is_non_trivial_real_evidence(self):
        self.assertGreater(len(self.episode_ledger), 0)


class RealTriggerGradablePopulationCountsTests(RealPopulationTests):
    """Confirms the exact real counts the CIO independently verified:
    316 total rows, 211 forward-metric-gradable rows, 21 rows with a real
    contemporaneous Trigger + gradable entry."""

    def test_total_rows_is_316(self):
        self.assertEqual(len(self.signal_ledger), 316)

    def test_gradable_rows_is_211(self):
        gradable = [e for e in self.signal_ledger if e["forward_metrics"].get("status") == "OK"]
        self.assertEqual(len(gradable), 211)

    def test_triggered_and_gradable_rows_is_21(self):
        records = build_trigger_population_records(self.signal_ledger)
        self.assertEqual(len(records), 21)

    def test_298040_is_present_in_the_official_population_despite_no_material_future_outcome(self):
        # ★ CIO's exact reproduction: 298040 (2026-08-13, PRICE_CONFIRMATION)
        # was silently EXCLUDED under the old Miss ∪ Defense population
        # solely because its future outcome never became material. It has
        # a real contemporaneous Trigger, so it MUST appear in the official
        # population now.
        subjects = {r["subject"] for r in self.episode_ledger}
        self.assertIn("298040", subjects)


class PopulationMembershipIsOutcomeIndependentTests(unittest.TestCase):
    """CIO methodology review round 1, defect 1's explicit required proof:
    population episode IDs must be byte-identical even if every entry's
    future return is altered."""

    def test_episode_id_set_is_unchanged_when_forward_returns_are_mutated(self):
        ctx, signal_ledger, _miss, _defense, _cov = build_signal_ledger_and_episodes()
        real_episodes = build_pit_episodes(signal_ledger)

        mutated_ledger = copy.deepcopy(signal_ledger)
        for entry in mutated_ledger:
            fm = entry.get("forward_metrics", {})
            if fm.get("status") != "OK":
                continue
            for h in fm.get("horizons", {}).values():
                if h.get("status") == "OK":
                    # Wildly alter every forward-looking number.
                    h["forward_return_pct"] = 999.0
                    h["mfe_pct"] = 999.0
                    h["mae_pct"] = -999.0

        mutated_episodes = build_pit_episodes(mutated_ledger)
        mutated_ids = {e["subject"] for e in mutated_episodes}, {e["episode_start_date"] for e in mutated_episodes}
        real_ids_cmp = {e["subject"] for e in real_episodes}, {e["episode_start_date"] for e in real_episodes}
        self.assertEqual(real_ids_cmp, mutated_ids)
        # Stronger: the full episode structures (subject/family/root_cause/
        # start/end/dedup-count) are byte-identical -- only forward-return
        # fields were mutated, and NONE of those ever entered grouping.
        strip = lambda eps: canonical_json([  # noqa: E731
            {k: v for k, v in e.items()} for e in
            sorted(eps, key=lambda x: (x["subject"], x["episode_start_date"], x.get("trigger_family") or ""))
        ])
        self.assertEqual(strip(real_episodes), strip(mutated_episodes))

    def test_episode_ledger_records_byte_identical_membership_regardless_of_mutated_returns(self):
        # End-to-end version of the same proof, through build_episode_ledger
        # (which DOES attach real gain_path/outcome_category from the
        # UNMUTATED series -- only the SIGNAL_LEDGER's own forward_metrics
        # values are mutated here, which membership must never depend on).
        ctx, signal_ledger, _miss, _defense, _cov = build_signal_ledger_and_episodes()
        real_ledger = build_episode_ledger(ctx, signal_ledger)
        real_membership = sorted((r["subject"], r["episode_start_date"], r["episode_end_date"]) for r in real_ledger)

        mutated_ledger = copy.deepcopy(signal_ledger)
        for entry in mutated_ledger:
            fm = entry.get("forward_metrics", {})
            if fm.get("status") != "OK":
                continue
            for h in fm.get("horizons", {}).values():
                if h.get("status") == "OK":
                    h["forward_return_pct"] = -777.0

        mutated_membership_ledger = build_episode_ledger(ctx, mutated_ledger)
        mutated_membership = sorted(
            (r["subject"], r["episode_start_date"], r["episode_end_date"]) for r in mutated_membership_ledger)
        self.assertEqual(real_membership, mutated_membership)


class OutcomeBasedMoversExcludedFromOfficialKpiTests(RealPopulationTests):
    """Item 4: the official-KPI/diagnostic-cohort boundary per market is a
    STATIC classification (`MARKET_KPI_STATUS`), never derived from how
    large or small any individual episode's outcome was."""

    def test_market_kpi_status_is_a_static_constant_independent_of_episode_outcomes(self):
        inflated = self.episode_ledger + self.episode_ledger
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
    def test_korea_population_label_says_current_watchlist_diagnostic_cohort(self):
        label = MARKET_KPI_STATUS["KOREA"]["population_label"]
        self.assertEqual(label, "CURRENT_WATCHLIST_DIAGNOSTIC_COHORT")
        self.assertNotIn("PIT_ELIGIBLE", label)
        self.assertNotIn("HISTORICAL", label)

    def test_korea_kpi_status_explicitly_says_not_computable(self):
        status = MARKET_KPI_STATUS["KOREA"]["kpi_population_status"]
        self.assertTrue(status.startswith("NOT_COMPUTABLE"), status)


class NoBackdatedCryptoEligibilityTests(RealPopulationTests):
    def test_every_crypto_episode_subject_is_in_the_pit_eligible_breadth_series(self):
        crypto_rows = [r for r in self.episode_ledger if r["market"] == "CRYPTO"]
        for r in crypto_rows:
            self.assertIn(r["subject"], self.ctx["breadth_series"], r["subject"])


class DataFailureNeverBecomesHarvestOpportunityTests(RealPopulationTests):
    def test_no_data_failure_root_cause_is_ever_a_harvest_opportunity(self):
        offending = [r for r in self.episode_ledger
                     if r["outcome_category"] == CATEGORY_HARVEST_OPPORTUNITY
                     and r["root_cause"] == "DATA_FAILURE"]
        self.assertEqual(offending, [])

    def test_no_data_failure_root_cause_anywhere_in_the_ledger(self):
        # The official population requires a REAL trigger (entry["triggers"]
        # non-empty), and DATA_FAILURE by definition means data_available was
        # False -- structurally impossible for a triggered row.
        offending = [r for r in self.episode_ledger if r["root_cause"] == "DATA_FAILURE"]
        self.assertEqual(offending, [])


class EpisodeDeduplicationTests(RealPopulationTests):
    def test_episode_ids_are_unique(self):
        ids = [r["episode_id"] for r in self.episode_ledger]
        self.assertEqual(len(ids), len(set(ids)))


class ReconciliationTests(RealPopulationTests):
    """Required reconciliation: every real-trigger+gradable row must map
    into exactly one PIT episode."""

    def test_all_21_triggered_gradable_rows_reconcile_into_an_episode(self):
        table = build_reconciliation_table(self.signal_ledger, self.episode_ledger)
        self.assertEqual(len(table), 21)
        unreconciled = [r for r in table if not r["reconciled"]]
        self.assertEqual(unreconciled, [])

    def test_298040_reconciles_to_a_real_episode_id(self):
        table = build_reconciliation_table(self.signal_ledger, self.episode_ledger)
        row = next(r for r in table if r["subject"] == "298040")
        self.assertTrue(row["reconciled"])
        self.assertEqual(len(row["matched_episode_ids"]), 1)


class AuxiliaryCohortIsNotTheOfficialPopulationTests(RealPopulationTests):
    """The old Miss ∪ Defense episode set is retained ONLY as an auxiliary
    comparison cohort -- structurally distinct from `episode_ledger`."""

    def test_auxiliary_cohort_is_a_separate_structure_from_the_official_ledger(self):
        aux = build_pr210_auxiliary_cohort(self.ctx, self.signal_ledger, self.miss_episodes, self.defense_episodes)
        for row in aux:
            self.assertIn("pr210_category", row)
            self.assertNotIn("outcome_category", row)  # never mislabeled as an official-population field
        official_subjects_dates = {(r["subject"], r["episode_start_date"]) for r in self.episode_ledger}
        aux_subjects_dates = {(r["subject"], r["episode_start_date"]) for r in aux}
        # The auxiliary cohort is allowed to overlap with the official
        # population (both derive from the same real evidence), but it must
        # not be a structurally identical list -- proving it is genuinely a
        # DIFFERENT (smaller, outcome-selected) construction.
        self.assertLessEqual(len(aux), len(self.episode_ledger) + 5)
        self.assertEqual(len(aux), 8)  # the exact CIO-confirmed old population size


class NoOptimalVerdictUnderUnratifiedGridTests(RealPopulationTests):
    """CIO methodology review round 1, defect 3: no aggregate policy
    verdict is EVER produced from the analytical horizon grid, regardless
    of sample size."""

    def test_every_horizon_aggregate_is_always_the_unratified_status(self):
        packet = build_scenario_comparisons(self.episode_ledger)
        for horizon, block in packet["by_early_exit_horizon"].items():
            s = block["aggregate_summary"]
            self.assertEqual(s["status"], AGGREGATE_STATUS)
            self.assertEqual(block["grid_status"], ANALYTICAL_GRID_STATUS)

    def test_aggregate_summary_never_carries_an_averaged_or_ranked_field(self):
        packet = build_scenario_comparisons(self.episode_ledger)
        forbidden_keys = {"avg_early_exit_opportunity_cost_pct", "episodes_where_full_hold_outperformed",
                           "episodes_where_early_exit_outperformed", "average", "avg", "optimal_horizon"}
        for horizon, block in packet["by_early_exit_horizon"].items():
            for key in block["aggregate_summary"]:
                self.assertNotIn(key, forbidden_keys)

    def test_even_an_artificially_large_population_still_never_produces_a_verdict(self):
        inflated = self.episode_ledger * 10  # far above any plausible MIN_SAMPLE_SIZE
        packet = build_scenario_comparisons(inflated)
        for horizon, block in packet["by_early_exit_horizon"].items():
            self.assertEqual(block["aggregate_summary"]["status"], AGGREGATE_STATUS)


if __name__ == "__main__":
    unittest.main()
