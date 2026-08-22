#!/usr/bin/env python3
"""P11 PIT Replay -- KPI population selection must never be outcome-based.

CIO review (PR #210, flaw 1): computing full-window returns FIRST and then
selecting the top/bottom-N as the audit population is a survivorship-bias
mechanism upstream of the root-cause classifier -- even though the
classifier itself never receives the return value (see
test_replay_root_cause_classifier.py's structural proof), the SUBJECTS that
ever reach it were chosen by the very outcome being measured. This file
proves the fix: the crypto KPI population is the full committed breadth
catalog, and the outcome-selected top/bottom-N table is provably excluded
from it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import evidence_index as ei  # noqa: E402
from replay import universe_scan as us  # noqa: E402
from replay.run_pit_replay import DESCRIPTIVE_TOP_MOVER_N, WINDOW_END, WINDOW_START, load_all_series  # noqa: E402


class SourceLevelNoOutcomeSelectionTests(unittest.TestCase):
    def test_load_all_series_does_not_call_top_crypto_movers_for_the_kpi_series(self):
        import inspect
        from replay.run_pit_replay import load_all_series
        source = inspect.getsource(load_all_series)  # function body only, docstrings elsewhere don't count
        # The only call to the outcome-ranking function must feed the
        # explicitly-labeled descriptive field, never `breadth_series`.
        lines = source.splitlines()
        movers_call_lines = [i for i, l in enumerate(lines) if "top_crypto_movers(" in l]
        self.assertTrue(movers_call_lines, "expected at least one top_crypto_movers() call (descriptive table)")
        for i in movers_call_lines:
            # the assignment target on this line (or immediately preceding)
            # must be the descriptive variable, not breadth_series.
            window = "\n".join(lines[max(0, i - 2):i + 1])
            self.assertIn("movers_descriptive", window)
            self.assertNotIn("breadth_series =", window)

    def test_breadth_series_population_is_built_from_the_full_pair_catalog_not_a_ranked_subset(self):
        import inspect
        from replay.run_pit_replay import load_all_series
        source = inspect.getsource(load_all_series)
        self.assertIn("pair_ids()", source)  # full catalog enumeration
        self.assertNotIn('mover_pair_ids = [r["pair_id"] for r in movers', source)  # the removed old selection


class RealEvidencePopulationSizeTests(unittest.TestCase):
    """The real KPI population size must match the real, full breadth
    catalog size -- not 30 (2 * old TOP_MOVER_N)."""

    @classmethod
    def setUpClass(cls):
        cls.ctx = load_all_series()

    def test_breadth_kpi_population_equals_the_full_committed_catalog(self):
        breadth_snapshots = self.ctx["breadth_snapshots"]
        if not breadth_snapshots:
            self.skipTest("no committed crypto breadth evidence in this checkout")
        full_catalog_size = len(breadth_snapshots[-1].pair_ids())
        self.assertEqual(len(self.ctx["breadth_series"]), full_catalog_size)
        self.assertGreater(full_catalog_size, 2 * DESCRIPTIVE_TOP_MOVER_N)

    def test_descriptive_movers_table_is_a_strict_subset_of_the_kpi_population(self):
        movers = self.ctx["movers_descriptive"]
        if movers["status"] != "OK":
            self.skipTest("no committed crypto breadth evidence in this checkout")
        mover_ids = {r["pair_id"] for r in movers["gainers"]} | {r["pair_id"] for r in movers["losers"]}
        kpi_ids = set(self.ctx["breadth_series"])
        self.assertTrue(mover_ids.issubset(kpi_ids))
        self.assertLess(len(mover_ids), len(kpi_ids))  # strictly smaller -- it's a descriptive sample, not the KPI feed


class DescriptiveTableExcludedFromLedgerTests(unittest.TestCase):
    """The published replay_summary.json must clearly separate the
    descriptive top/bottom table from the KPI-feeding population, and the
    ledger-building function must never reference the descriptive field."""

    def test_run_function_labels_the_descriptive_table_distinctly(self):
        source = (ROOT / "replay" / "run_pit_replay.py").read_text(encoding="utf-8")
        self.assertIn("crypto_movers_descriptive_only", source)

    def test_build_signal_replay_ledger_never_references_movers_descriptive(self):
        import inspect
        from replay.run_pit_replay import build_signal_replay_ledger
        src = inspect.getsource(build_signal_replay_ledger)
        self.assertNotIn("movers_descriptive", src)
        self.assertIn("breadth_series", src)


if __name__ == "__main__":
    unittest.main()
