#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- price series merge / integrity / PIT-flag regression."""
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


class PriceSeriesCacheCorrectnessTests(unittest.TestCase):
    """CIO growth-driver fix (2026-09-18): `dates()`,
    `live_trading_dates_at_or_before()`, and `window_at_or_before_cached()`
    now memoize on the instance instead of re-sorting/re-filtering the
    whole series on every call (replay/trigger_engine.py's
    `relative_strength_reversal` was calling the unmemoized form once per
    peer per candidate per replayed date -- see clock/operational_scan.py).
    These tests pin the CORRECTNESS of that cache, not just its presence:
    a cache that returns the right answer once and a stale one thereafter
    would still pass every pre-existing test in this file, since none of
    them call the same query twice around a mutation."""

    def test_repeated_calls_return_the_identical_correct_list(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-10", {"close": 1, "open": 1, "high": 1, "low": 1}, "2026-08-10")
        s._merge_row("2026-08-11", {"close": 2, "open": 2, "high": 2, "low": 2}, "2026-08-11")
        first = s.live_trading_dates_at_or_before("2026-08-11")
        second = s.live_trading_dates_at_or_before("2026-08-11")
        self.assertEqual(first, ["2026-08-10", "2026-08-11"])
        self.assertEqual(first, second)

    def test_cache_does_not_survive_a_merge_that_changes_first_capture_date(self):
        """A later `_merge_row` call for an EARLIER trading_date with an
        earlier capture_date changes `live_known_asof` for decision_dates
        already cached. If invalidation were missing, the second call
        below would wrongly keep returning the pre-merge (stale) answer."""
        s = PriceSeries("X")
        s._merge_row("2026-08-13", {"close": 1, "open": 1, "high": 1, "low": 1}, "2026-08-20")
        # Not yet live-known on 2026-08-15 -- the only capture is dated 08-20.
        self.assertEqual(s.live_trading_dates_at_or_before("2026-08-15"), [])
        # A second, earlier-captured snapshot for the SAME trading_date lands.
        s._merge_row("2026-08-13", {"close": 1, "open": 1, "high": 1, "low": 1}, "2026-08-14")
        self.assertEqual(s.first_capture_date_for("2026-08-13"), "2026-08-14")
        # Now live-known as of 2026-08-15 -- must NOT be served from the
        # (now-stale) empty answer cached above.
        self.assertEqual(s.live_trading_dates_at_or_before("2026-08-15"), ["2026-08-13"])

    def test_cache_does_not_survive_a_new_row_added_after_first_query(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-10", {"close": 1, "open": 1, "high": 1, "low": 1}, "2026-08-10")
        self.assertEqual(s.dates(), ["2026-08-10"])
        self.assertEqual(s.live_trading_dates_at_or_before("2026-08-12"), ["2026-08-10"])
        s._merge_row("2026-08-11", {"close": 2, "open": 2, "high": 2, "low": 2}, "2026-08-11")
        self.assertEqual(s.dates(), ["2026-08-10", "2026-08-11"])
        self.assertEqual(
            s.live_trading_dates_at_or_before("2026-08-12"),
            ["2026-08-10", "2026-08-11"],
        )

    def test_window_at_or_before_cached_matches_uncached_semantics_and_reuses_result(self):
        from replay.trigger_engine import window_at_or_before

        s = PriceSeries("X")
        for d, c in [("2026-08-10", 1), ("2026-08-11", 2), ("2026-08-12", 3)]:
            s._merge_row(d, {"close": c, "open": c, "high": c, "low": c}, d)
        first = window_at_or_before(s, "2026-08-12", 2)
        second = window_at_or_before(s, "2026-08-12", 2)
        self.assertEqual(first, ["2026-08-11", "2026-08-12"])
        self.assertEqual(first, second)
        # Different n against the same (series, decision_date) is a
        # different cache key, not a collision with the n=2 entry above.
        self.assertEqual(window_at_or_before(s, "2026-08-12", 1), ["2026-08-12"])

    def test_window_at_or_before_cached_validates_once_per_key_not_per_call(self):
        """`window_at_or_before_cached`'s `validate` callback must still run
        -- a genuine violation must still raise -- but only ONCE per
        distinct (decision_date, n), not once per caller. This is the
        exact redundancy the fix removes: `relative_strength_reversal`
        called the uncached form once per peer per candidate per date."""
        s = PriceSeries("X")
        for d, c in [("2026-08-10", 1), ("2026-08-11", 2), ("2026-08-12", 3)]:
            s._merge_row(d, {"close": c, "open": c, "high": c, "low": c}, d)

        calls = []

        def spy(decision_date, dates):
            calls.append((decision_date, tuple(dates)))

        s.window_at_or_before_cached("2026-08-12", 2, spy)
        s.window_at_or_before_cached("2026-08-12", 2, spy)
        s.window_at_or_before_cached("2026-08-12", 2, spy)
        self.assertEqual(len(calls), 1)  # validated on the first call only

        s.window_at_or_before_cached("2026-08-12", 1, spy)
        self.assertEqual(len(calls), 2)  # a different n is a different key

        def boom(decision_date, dates):
            raise ValueError("LOOKAHEAD")

        with self.assertRaises(ValueError):
            s.window_at_or_before_cached("2026-08-11", 1, boom)
        # A raise must not poison the cache with a partial/absent result.
        self.assertNotIn(("2026-08-11", 1), s._window_cache)


if __name__ == "__main__":
    unittest.main()
