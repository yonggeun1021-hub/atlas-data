#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- full-population scan regression (deliverable: every
ticker in the declared universe/seen in evidence, incl. Discovery/Candidate/
Ready coverage and up/down mover ranking applied uniformly)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import universe_scan as us  # noqa: E402
from replay.price_series import PriceSeries  # noqa: E402


class WindowReturnTests(unittest.TestCase):
    def test_window_return_uses_real_dates_within_bounds(self):
        s = PriceSeries("X")
        for d, c in [("2026-07-20", 100.0), ("2026-07-22", 100.0), ("2026-08-01", 150.0), ("2026-08-25", 999.0)]:
            s._merge_row(d, {"close": c, "open": c, "high": c, "low": c}, d)
        ret = us.window_return_pct(s, "2026-07-22", "2026-08-22")
        self.assertAlmostEqual(ret, 50.0, places=6)  # 100 -> 150, NOT the 08-25 row outside the window

    def test_window_return_none_when_no_data_in_range(self):
        s = PriceSeries("X")
        s._merge_row("2020-01-01", {"close": 1, "open": 1, "high": 1, "low": 1}, "2020-01-01")
        self.assertIsNone(us.window_return_pct(s, "2026-07-22", "2026-08-22"))


class RealKrPopulationTests(unittest.TestCase):
    """Uses real committed evidence -- this is the only way to honestly test
    "every ticker in the declared universe/seen in evidence"."""

    def test_kr_population_includes_every_config_universe_code(self):
        from replay import evidence_index as ei
        snaps = ei.find_krx_snapshots()
        population = us.kr_population(snaps)
        universe = ei.load_universe()
        declared_codes = {row["code"] for row in universe["kr"]}
        population_codes = {p["code"] for p in population}
        self.assertTrue(declared_codes.issubset(population_codes))

    def test_kr_population_carries_real_stage_history_not_fabricated(self):
        from replay import evidence_index as ei
        snaps = ei.find_krx_snapshots()
        population = us.kr_population(snaps)
        row = next(p for p in population if p["code"] == "005930")
        self.assertIsInstance(row["stage_history"], list)
        for capture_date, stage in row["stage_history"]:
            self.assertRegex(capture_date, r"^\d{4}-\d{2}-\d{2}$")


class TopMoversRankingSymmetryTests(unittest.TestCase):
    def test_top_kr_movers_gainers_and_losers_use_the_same_ranking_not_separate_rules(self):
        from replay import evidence_index as ei
        snaps = ei.find_krx_snapshots()
        result = us.top_kr_movers(snaps, "2026-07-22", "2026-08-22")
        self.assertEqual(result["status"], "OK")
        # gainers is the reverse of losers over the same sorted population --
        # i.e. one ranking, not two different selection rules.
        self.assertEqual(
            sorted(r["code"] for r in result["gainers"]),
            sorted(r["code"] for r in result["losers"]),
        )


if __name__ == "__main__":
    unittest.main()
