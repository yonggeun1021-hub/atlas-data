#!/usr/bin/env python3
"""P11 PIT Replay -- price series merge / integrity / PIT-flag regression."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.price_series import PriceSeries, PriceSeriesIntegrityError, assert_no_integrity_conflicts  # noqa: E402


class PriceSeriesMergeTests(unittest.TestCase):
    def test_merging_identical_rows_from_two_snapshots_keeps_earliest_capture_date(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 99, "high": 101, "low": 98}, "2026-08-14")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 99, "high": 101, "low": 98}, "2026-08-13")
        self.assertEqual(s.first_capture_date_for("2026-08-13"), "2026-08-13")
        self.assertEqual(s.integrity_conflicts, [])

    def test_conflicting_close_is_recorded_not_raised_and_earliest_kept(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-14", {"close": 1638000.0, "open": 0, "high": 0, "low": 0}, "2026-08-14")
        s._merge_row("2026-08-14", {"close": 1645000.0, "open": 0, "high": 0, "low": 0}, "2026-08-15")
        self.assertEqual(len(s.integrity_conflicts), 1)
        self.assertEqual(s.close_on("2026-08-14"), 1638000.0)  # earlier capture wins
        with self.assertRaises(PriceSeriesIntegrityError):
            assert_no_integrity_conflicts(s)

    def test_live_known_asof_false_before_any_capture_and_true_after(self):
        s = PriceSeries("X")
        s._merge_row("2026-07-22", {"close": 100.0, "open": 0, "high": 0, "low": 0}, "2026-08-13")
        self.assertFalse(s.live_known_asof("2026-07-22", "2026-07-25"))  # not yet captured
        self.assertFalse(s.live_known_asof("2026-07-22", "2026-08-12"))  # still not captured
        self.assertTrue(s.live_known_asof("2026-07-22", "2026-08-13"))  # captured today
        self.assertTrue(s.live_known_asof("2026-07-22", "2026-08-20"))  # captured earlier, still true

    def test_live_trading_dates_at_or_before_excludes_dates_only_known_retrospectively(self):
        s = PriceSeries("X")
        s._merge_row("2026-07-22", {"close": 100.0, "open": 0, "high": 0, "low": 0}, "2026-08-13")
        s._merge_row("2026-08-13", {"close": 110.0, "open": 0, "high": 0, "low": 0}, "2026-08-13")
        self.assertEqual(s.live_trading_dates_at_or_before("2026-07-25"), [])
        self.assertEqual(s.live_trading_dates_at_or_before("2026-08-13"), ["2026-07-22", "2026-08-13"])

    def test_trading_dates_strictly_after_excludes_the_decision_date_itself(self):
        s = PriceSeries("X")
        for d, c in [("2026-08-10", 1), ("2026-08-11", 2), ("2026-08-12", 3)]:
            s._merge_row(d, {"close": c, "open": c, "high": c, "low": c}, d)
        self.assertEqual(s.trading_dates_strictly_after("2026-08-11"), ["2026-08-12"])
        self.assertEqual(s.trading_dates_strictly_after("2026-08-12"), [])


if __name__ == "__main__":
    unittest.main()
