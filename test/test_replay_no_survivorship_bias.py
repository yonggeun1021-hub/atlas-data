#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- KPI population selection must never be
outcome-based, AND (CIO review round 3, flaw 1) must never be a raw source
catalog either.

Round 2 fixed: computing full-window returns FIRST and selecting top/
bottom-N as the audit population (survivorship bias).
Round 3 fixes a DIFFERENT problem the round-2 fix introduced: dumping the
full 632-pair Kraken source catalog into the KPI population contaminates it
with non-investable/unclassified/never-actually-tracked assets. The real
KPI population must be the genuinely ratified, PIT-eligible universe
(`config/crypto_breadth_exclusion_taxonomy.json`, `approval_status:
RATIFIED`, category `eligible_crypto`, evaluated per-date against each
record's own real `effective_from`) -- ~87 assets today, essentially ZERO
before 2026-08-19. The 632-pair catalog survives only as an explicitly
separate `source_coverage_population` (data-coverage metric, never a KPI
population).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import asset_identity as ai  # noqa: E402
from replay import evidence_index as ei  # noqa: E402
from replay import universe_scan as us  # noqa: E402
from replay.run_pit_replay import DESCRIPTIVE_TOP_MOVER_N, WINDOW_END, WINDOW_START, load_all_series  # noqa: E402


class SourceLevelNoOutcomeSelectionTests(unittest.TestCase):
    def test_load_all_series_does_not_call_top_crypto_movers_for_the_kpi_series(self):
        import inspect
        from replay.run_pit_replay import load_all_series as _las
        source = inspect.getsource(_las)
        lines = source.splitlines()
        movers_call_lines = [i for i, l in enumerate(lines) if "top_crypto_movers(" in l]
        self.assertTrue(movers_call_lines, "expected at least one top_crypto_movers() call (descriptive table)")
        for i in movers_call_lines:
            window = "\n".join(lines[max(0, i - 2):i + 1])
            self.assertIn("movers_descriptive", window)
            self.assertNotIn("breadth_series =", window)

    def test_kpi_population_is_built_from_a_real_ratified_taxonomy_not_the_full_catalog(self):
        import inspect
        from replay.run_pit_replay import load_all_series as _las
        source = inspect.getsource(_las)
        self.assertIn("crypto_pit_eligible_pair_ids", source)
        # The round-2-era "take everything the latest snapshot lists" pattern
        # for the KPI series must not reappear.
        self.assertNotIn('all_pair_ids = breadth_snapshots[-1].pair_ids()\n    breadth_series', source)


class RealEvidencePopulationSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = load_all_series()

    def test_kpi_population_is_much_smaller_than_the_full_source_catalog(self):
        if not self.ctx["breadth_snapshots"]:
            self.skipTest("no committed crypto breadth evidence in this checkout")
        source_size = self.ctx["source_coverage"]["pair_count"]
        kpi_size = len(self.ctx["breadth_series"])
        self.assertGreater(source_size, 600)  # the raw Kraken catalog
        self.assertLess(kpi_size, 100)        # the real ratified eligible universe
        self.assertLess(kpi_size, source_size)

    def test_kpi_population_excludes_btc_to_avoid_double_counting_the_dedicated_subject(self):
        self.assertNotIn("BTC/USD", self.ctx["breadth_series"])

    def test_pit_eligible_universe_is_empty_before_the_real_ratification_date(self):
        known = set(self.ctx["breadth_series"])
        self.assertEqual(ai.crypto_pit_eligible_pair_ids("2026-07-25", known), set())
        self.assertEqual(ai.crypto_pit_eligible_pair_ids("2026-08-01", known), set())

    def test_pit_eligible_universe_grows_only_at_its_own_real_effective_from_dates(self):
        known = set(self.ctx["breadth_series"])
        before = ai.crypto_pit_eligible_pair_ids("2026-08-18", known)
        on_ratification = ai.crypto_pit_eligible_pair_ids("2026-08-19", known)
        later = ai.crypto_pit_eligible_pair_ids("2026-08-22", known)
        self.assertEqual(before, set())
        self.assertGreater(len(on_ratification), 0)
        self.assertGreaterEqual(len(later), len(on_ratification))  # monotonic, never shrinks


class DescriptiveTableClearlyLabeledAndExcludedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = load_all_series()

    def test_run_function_labels_the_descriptive_table_distinctly(self):
        source = (ROOT / "replay" / "run_pit_replay.py").read_text(encoding="utf-8")
        self.assertIn("crypto_movers_descriptive_only", source)
        self.assertIn("crypto_source_coverage_population", source)

    def test_build_signal_replay_ledger_never_references_movers_descriptive(self):
        import inspect
        from replay.run_pit_replay import build_signal_replay_ledger
        src = inspect.getsource(build_signal_replay_ledger)
        self.assertNotIn("movers_descriptive", src)
        self.assertIn("breadth_series", src)

    def test_descriptive_table_is_drawn_from_the_full_source_catalog_not_the_kpi_set(self):
        movers = self.ctx["movers_descriptive"]
        if movers["status"] != "OK":
            self.skipTest("no committed crypto breadth evidence in this checkout")
        self.assertGreater(movers["population_size"], 600)  # full catalog, not the ~87-asset KPI set


if __name__ == "__main__":
    unittest.main()
