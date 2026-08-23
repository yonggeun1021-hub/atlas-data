#!/usr/bin/env python3
"""P7-11 Baseline Audit -- `harvest_audit/gain_path.py` PIT-safety and
metric-correctness regression (B-8 items 1, 2, 5, 6, 10, 11, plus the PIT
timing invariant contract)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.price_series import PriceSeries  # noqa: E402
from replay.forward_metrics import compute_forward_metrics  # noqa: E402

from harvest_audit.gain_path import GainPathTimingError, compute_gain_path  # noqa: E402


def _series_from_rows(subject: str, rows: dict[str, dict]) -> PriceSeries:
    series = PriceSeries(subject)
    for date, row in sorted(rows.items()):
        series._merge_row(date, row, capture_date=date)  # test-only direct construction
    return series


class NoEntryPriceIsNotGradableTests(unittest.TestCase):
    """Item 5: no entry price -> NOT_GRADABLE."""

    def test_no_forward_trading_date_at_all_is_not_gradable(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 101, "low": 99, "close": 100},
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["status"], "NOT_GRADABLE")
        self.assertIn("no executable entry point", result["not_gradable_reason"])

    def test_action_eligible_at_on_the_very_last_available_date_is_not_gradable(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 101, "low": 99, "close": 100},
            "2026-08-14": {"open": 101, "high": 102, "low": 100, "close": 101},
        })
        result = compute_gain_path(series, "2026-08-14", "BTC")
        self.assertEqual(result["status"], "NOT_GRADABLE")


class PreSignalEntryRejectedTests(unittest.TestCase):
    """Items 1/2: the entry price is NEVER a same-day or prior-day price --
    only the first REAL trading date strictly after action_eligible_at,
    priced at that date's OPEN."""

    def _series(self):
        return _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 105, "low": 99, "close": 104},  # action_eligible_at itself
            "2026-08-14": {"open": 110, "high": 115, "low": 108, "close": 112},  # first forward date
            "2026-08-17": {"open": 112, "high": 118, "low": 111, "close": 116},
        })

    def test_entry_price_is_the_first_forward_dates_open_never_the_decision_dates_own_close(self):
        result = compute_gain_path(self._series(), "2026-08-13", "BTC")
        self.assertEqual(result["hypothetical_entry_at"], "2026-08-14")
        self.assertEqual(result["entry_price"], 110)  # 2026-08-14's OPEN, not 2026-08-13's close (104)

    def test_hypothetical_entry_at_is_structurally_after_action_eligible_at(self):
        result = compute_gain_path(self._series(), "2026-08-13", "BTC")
        self.assertGreater(result["hypothetical_entry_at"], result["action_eligible_at"])

    def test_timing_invariant_violation_raises_if_entry_at_or_before_action_eligible(self):
        # Directly exercise the structural guard rather than only the
        # happy path -- a hand-crafted violation must raise, not silently
        # pass through.
        from harvest_audit.gain_path import _validate_gain_path_timing
        with self.assertRaises(GainPathTimingError):
            _validate_gain_path_timing(None, "2026-08-14", "2026-08-14", "2026-08-15")

    def test_signal_evaluation_at_after_action_eligible_at_raises(self):
        from harvest_audit.gain_path import _validate_gain_path_timing
        with self.assertRaises(GainPathTimingError):
            _validate_gain_path_timing("2026-08-14", "2026-08-13", "2026-08-14", "2026-08-15")


class NoArbitraryEndpointInterpolationTests(unittest.TestCase):
    """Item 6: when fewer than `h` real trading days exist, the horizon is
    honestly INSUFFICIENT_HORIZON_DATA -- never an interpolated/estimated
    value."""

    def test_short_series_reports_insufficient_horizon_data_never_fabricates(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 101, "low": 99, "close": 100},
            "2026-08-14": {"open": 100, "high": 103, "low": 99, "close": 102},
            "2026-08-17": {"open": 102, "high": 104, "low": 101, "close": 103},
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["horizons"]["1"]["status"], "OK")
        for h in ("3", "5", "10", "20"):
            self.assertEqual(result["horizons"][h]["status"], "INSUFFICIENT_HORIZON_DATA")
            self.assertLess(result["horizons"][h]["trading_days_available"],
                             result["horizons"][h]["trading_days_required"])
        self.assertFalse(result["endpoint_coverage"]["full_horizon_reached"])

    def test_endpoint_coverage_reports_real_counts_not_the_requested_max(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 101, "low": 99, "close": 100},
            "2026-08-14": {"open": 100, "high": 103, "low": 99, "close": 102},
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["endpoint_coverage"]["trading_days_available_total"], 1)
        self.assertEqual(result["endpoint_coverage"]["trading_days_used"], 1)
        self.assertEqual(result["endpoint_coverage"]["max_horizon_requested"], 20)


class MfeAndTerminalReturnKeptSeparateTests(unittest.TestCase):
    """Item 10: MFE (best intraday high seen anywhere in the window) and
    terminal return (close-based, at the endpoint) are independently
    computed fields that can and do diverge."""

    def test_mfe_and_terminal_return_diverge_on_a_rally_that_fully_fades(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 100, "low": 100, "close": 100},
            "2026-08-14": {"open": 100, "high": 150, "low": 100, "close": 130},  # entry day, big spike
            "2026-08-17": {"open": 130, "high": 135, "low": 90, "close": 95},    # gives it all back
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["entry_price"], 100)
        self.assertAlmostEqual(result["mfe_pct"], 50.0)   # (150-100)/100
        self.assertAlmostEqual(result["terminal_return_pct"], -5.0)  # (95-100)/100
        self.assertNotEqual(round(result["mfe_pct"], 2), round(result["terminal_return_pct"], 2))


class NoGivebackBeforeMfeTests(unittest.TestCase):
    """Item 11 + CIO methodology review round 1, defect 2: a LOW that
    occurred BEFORE the MFE date -- including the MFE day's OWN low, since
    a daily bar cannot prove intraday high-vs-low ordering -- must never
    count toward `max_giveback_after_mfe_confirmed_pct`. Only real trading
    days STRICTLY AFTER the MFE day are ever used."""

    def test_a_deep_pre_mfe_dip_is_excluded_from_giveback(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 100, "low": 100, "close": 100},
            "2026-08-14": {"open": 100, "high": 101, "low": 40, "close": 100},   # deep pre-MFE dip (day 1)
            "2026-08-17": {"open": 100, "high": 200, "low": 100, "close": 190},  # MFE day (day 2)
            "2026-08-18": {"open": 190, "high": 195, "low": 180, "close": 185},  # confirmed post-MFE (day 3)
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["mfe_date"], "2026-08-17")
        # Correct: ONLY day 3's low=180 (strictly after the MFE day) vs the
        # peak high (200) -- neither day 1's low=40 NOR the MFE day's own
        # low=100 may ever appear in this computation.
        expected_giveback = (180 - 200) / 200 * 100
        self.assertAlmostEqual(result["max_giveback_after_mfe_confirmed_pct"], expected_giveback)
        self.assertEqual(result["giveback_confirmed_status"], "OK")
        self.assertGreater(result["max_giveback_after_mfe_confirmed_pct"], -60)  # nowhere near day-1's -60%

    def test_reproduces_and_fixes_the_low_before_high_same_day_bug(self):
        # ★ CIO's exact regression request: a synthetic case where the MFE
        # day's own low occurred BEFORE the high intraday (unknowable from
        # the daily bar, but constructed here so we know the "true" answer
        # by construction) -- the OLD code (`>= mfe_date`, including the
        # peak day's own low) would have wrongly reported a large giveback
        # from that same-day low, when the low actually happened BEFORE the
        # peak and there was no real post-MFE giveback at all in this
        # example. The FIXED code must never see that same-day low.
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 100, "low": 100, "close": 100},
            # MFE day: constructed as "low occurred first (morning), high
            # occurred later (afternoon)" -- open=100, dips to low=20 in the
            # morning, then rallies to the day's high=200 by the close.
            "2026-08-14": {"open": 100, "high": 200, "low": 20, "close": 195},
            # Next day holds essentially at the peak -- genuinely almost no
            # real post-MFE giveback.
            "2026-08-17": {"open": 195, "high": 198, "low": 193, "close": 196},
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["mfe_date"], "2026-08-14")
        # OLD (buggy) behavior would have computed pct(200, 20) = -90.0%
        # (treating the pre-peak morning low as if it were post-MFE
        # giveback). The FIX must never produce that number.
        old_buggy_value = (20 - 200) / 200 * 100
        self.assertNotAlmostEqual(result["max_giveback_after_mfe_confirmed_pct"], old_buggy_value)
        # Correct: only 2026-08-17 (strictly after the MFE day) counts --
        # low=193 vs peak high=200.
        expected = (193 - 200) / 200 * 100
        self.assertAlmostEqual(result["max_giveback_after_mfe_confirmed_pct"], expected)

    def test_no_trading_day_after_mfe_is_not_computable_never_fabricated(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 100, "low": 100, "close": 100},
            "2026-08-14": {"open": 100, "high": 200, "low": 90, "close": 190},  # MFE day == last day in window
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["mfe_date"], "2026-08-14")
        self.assertEqual(result["giveback_confirmed_status"], "NOT_COMPUTABLE_NO_TRADING_DAY_AFTER_MFE")
        self.assertIsNone(result["max_giveback_after_mfe_confirmed_pct"])


class BreakevenAfterGivebackTests(unittest.TestCase):
    def test_no_giveback_below_breakeven_when_price_never_drops_to_entry(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 100, "low": 100, "close": 100},
            "2026-08-14": {"open": 100, "high": 120, "low": 105, "close": 115},
            "2026-08-17": {"open": 115, "high": 118, "low": 110, "close": 112},
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["breakeven_after_positive_mfe_status"], "NO_GIVEBACK_BELOW_BREAKEVEN")
        self.assertIsNone(result["time_to_breakeven_after_positive_mfe_days"])

    def test_recovers_after_giveback_below_breakeven(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 100, "low": 100, "close": 100},
            "2026-08-14": {"open": 100, "high": 130, "low": 100, "close": 125},  # MFE day
            "2026-08-17": {"open": 125, "high": 126, "low": 90, "close": 95},    # below breakeven
            "2026-08-18": {"open": 95, "high": 108, "low": 94, "close": 105},    # recovers
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["breakeven_after_positive_mfe_status"], "RECOVERED")
        self.assertEqual(result["time_to_breakeven_after_positive_mfe_days"], 3)

    def test_not_recovered_within_window(self):
        series = _series_from_rows("X", {
            "2026-08-13": {"open": 100, "high": 100, "low": 100, "close": 100},
            "2026-08-14": {"open": 100, "high": 130, "low": 100, "close": 125},
            "2026-08-17": {"open": 125, "high": 126, "low": 90, "close": 95},
        })
        result = compute_gain_path(series, "2026-08-13", "BTC")
        self.assertEqual(result["breakeven_after_positive_mfe_status"], "NOT_RECOVERED_IN_WINDOW")
        self.assertIsNone(result["time_to_breakeven_after_positive_mfe_days"])


class CrossValidationAgainstReplayForwardMetricsTests(unittest.TestCase):
    """Proves `gain_path`'s independently-written 1/3/5/10 horizon
    computation stays byte-identical to `replay.forward_metrics.
    compute_forward_metrics`'s own, over real committed BTC evidence --
    two genuinely separate implementations of the SAME PIT-safe rule must
    agree, not merely both individually claim correctness."""

    def test_forward_return_1_3_5_10_matches_replay_forward_metrics_on_real_btc_evidence(self):
        from replay import evidence_index as ei
        from replay.price_series import build_btc_series

        snapshots = ei.find_btc_snapshots()
        series = build_btc_series(snapshots)
        decision_date = "2026-08-14"  # a real date with plenty of forward evidence

        gp = compute_gain_path(series, decision_date, "BTC")
        fm = compute_forward_metrics(series, decision_date)
        self.assertEqual(gp["hypothetical_entry_at"], fm["hypothetical_entry_at"])
        self.assertEqual(gp["entry_price"], fm["entry_price"])
        for h in ("1", "3", "5", "10"):
            gp_h, fm_h = gp["horizons"][h], fm["horizons"][h]
            if fm_h["status"] != "OK":
                self.assertEqual(gp_h["status"], fm_h["status"])
                continue
            self.assertEqual(gp_h["status"], "OK")
            self.assertAlmostEqual(gp_h["forward_return_pct"], fm_h["forward_return_pct"], places=9)
            self.assertAlmostEqual(gp_h["mfe_pct"], fm_h["mfe_pct"], places=9)
            self.assertAlmostEqual(gp_h["mae_pct"], fm_h["mae_pct"], places=9)


if __name__ == "__main__":
    unittest.main()
