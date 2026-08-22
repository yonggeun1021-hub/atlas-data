#!/usr/bin/env python3
"""P11 PIT Replay -- forward return / MFE / MAE regression (deliverable 4)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.forward_metrics import HORIZONS, compute_forward_metrics  # noqa: E402
from replay.price_series import PriceSeries  # noqa: E402


def series_from_closes(subject: str, start_day: int, closes: list[float]) -> PriceSeries:
    s = PriceSeries(subject)
    for i, c in enumerate(closes):
        d = f"2026-08-{start_day + i:02d}"
        s._merge_row(d, {"close": c, "open": c, "high": c * 1.02, "low": c * 0.98}, d)
    return s


class ForwardMetricsTests(unittest.TestCase):
    def test_all_four_horizons_present(self):
        self.assertEqual(set(HORIZONS), {1, 3, 5, 10})

    def test_ok_case_all_horizons_computed_correctly(self):
        closes = [100.0] + [100.0 + i for i in range(1, 11)]  # entry=100, then 101..110
        series = series_from_closes("X", 1, closes)
        result = compute_forward_metrics(series, "2026-08-01")
        self.assertEqual(result["entry_close"], 100.0)
        h1 = result["horizons"]["1"]
        self.assertEqual(h1["status"], "OK")
        self.assertAlmostEqual(h1["forward_return_pct"], 1.0, places=6)
        h10 = result["horizons"]["10"]
        self.assertEqual(h10["status"], "OK")
        self.assertAlmostEqual(h10["forward_return_pct"], 10.0, places=6)

    def test_mfe_mae_use_high_low_not_just_close(self):
        closes = [100.0, 100.0]
        series = series_from_closes("X", 1, closes)
        result = compute_forward_metrics(series, "2026-08-01")
        h1 = result["horizons"]["1"]
        self.assertAlmostEqual(h1["mfe_pct"], 2.0, places=6)   # 100*1.02
        self.assertAlmostEqual(h1["mae_pct"], -2.0, places=6)  # 100*0.98

    def test_insufficient_horizon_data_reported_not_fabricated(self):
        closes = [100.0, 101.0]  # only 1 forward day exists
        series = series_from_closes("X", 1, closes)
        result = compute_forward_metrics(series, "2026-08-01")
        self.assertEqual(result["horizons"]["1"]["status"], "OK")
        for h in ("3", "5", "10"):
            self.assertEqual(result["horizons"][h]["status"], "INSUFFICIENT_HORIZON_DATA")
            self.assertNotIn("forward_return_pct", result["horizons"][h])

    def test_no_entry_price_data_reported_for_empty_series(self):
        series = PriceSeries("X")
        result = compute_forward_metrics(series, "2026-08-01")
        self.assertEqual(result["status"], "NO_ENTRY_PRICE_DATA")
        for h in ("1", "3", "5", "10"):
            self.assertEqual(result["horizons"][h]["status"], "NO_ENTRY_PRICE_DATA")

    def test_entry_uses_latest_date_at_or_before_decision_date(self):
        series = series_from_closes("X", 1, [100.0, 105.0, 110.0])
        result = compute_forward_metrics(series, "2026-08-10")  # decision date is a non-trading gap day
        self.assertEqual(result["entry_date"], "2026-08-03")
        self.assertEqual(result["entry_close"], 110.0)


class SignalAnchoredEntryAlignmentTests(unittest.TestCase):
    """CIO review (PR #210, flaw 4): when a real detected trigger supplies an
    explicit entry_date, forward metrics must be anchored to EXACTLY that
    date, and must refuse to grade if that price was not actually
    live-known as of the assumed execution time."""

    def test_explicit_entry_date_is_used_verbatim_not_re_derived(self):
        s = PriceSeries("X")
        # A later trading date (08-20) exists in the series and WOULD have
        # been picked by the old naive "latest <= decision_date" logic, but
        # the signal itself evaluated against 08-19.
        s._merge_row("2026-08-19", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-19")
        s._merge_row("2026-08-20", {"close": 200.0, "open": 200, "high": 201, "low": 199}, "2026-08-21")
        result = compute_forward_metrics(s, "2026-08-20", entry_date="2026-08-19")
        self.assertEqual(result["hypothetical_entry_at"], "2026-08-19")
        self.assertEqual(result["signal_evaluation_at"], "2026-08-19")
        self.assertEqual(result["action_eligible_at"], "2026-08-20")
        self.assertEqual(result["entry_close"], 100.0)  # NOT 200.0 -- the old bug this fixes

    def test_signal_anchored_entry_not_live_known_is_not_gradable(self):
        s = PriceSeries("X")
        # entry_date's row exists in the series but was only captured AFTER
        # decision_date -- i.e. it was not actually knowable at signal time.
        s._merge_row("2026-08-20", {"close": 200.0, "open": 200, "high": 201, "low": 199}, "2026-08-21")
        result = compute_forward_metrics(s, "2026-08-20", entry_date="2026-08-20")
        self.assertEqual(result["status"], "NOT_GRADABLE")
        self.assertIn("not_gradable_reason", result)
        for h in ("1", "3", "5", "10"):
            self.assertEqual(result["horizons"][h]["status"], "NOT_GRADABLE")

    def test_auto_fallback_entry_date_is_never_gated_as_not_gradable(self):
        # No live signal existed at all (DATA_FAILURE-style date) -- the
        # auto-fallback opportunity-cost measurement remains OK even though
        # the price was only knowable retrospectively; that is a distinct,
        # legitimate use case from a signal-anchored grade (see
        # forward_metrics.py's docstring).
        s = PriceSeries("X")
        s._merge_row("2026-07-22", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        s._merge_row("2026-07-23", {"close": 110.0, "open": 110, "high": 111, "low": 109}, "2026-08-13")
        result = compute_forward_metrics(s, "2026-07-22")  # entry_date omitted -> auto fallback
        self.assertEqual(result["status"], "OK")
        self.assertFalse(result["entry_live_known_asof_decision_date"])
        self.assertEqual(result["entry_date_source"], "auto_latest_at_or_before_decision_date")


if __name__ == "__main__":
    unittest.main()
