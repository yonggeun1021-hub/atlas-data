#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- forward return / MFE / MAE regression
(deliverable 4).

CIO review round 4 (confirmed lookahead bug): `hypothetical_entry_at` must
always be the first REAL trading date strictly AFTER `action_eligible_at`
(== decision_date), priced at that date's OPEN -- never a prior day's
close, never the same day (pre/post-market timing can't be proven from a
daily bar). This file directly proves the fix and the hard invariant.
"""
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


class EntryTimingInvariantTests(unittest.TestCase):
    """CIO round-4 item 9: hypothetical_entry_at >= action_eligible_at is a
    hard invariant, enforced structurally, not merely asserted once."""

    def test_hypothetical_entry_at_is_always_strictly_after_action_eligible_at(self):
        series = series_from_closes("X", 1, [100.0, 101.0, 102.0, 103.0, 104.0])
        for decision_date in ("2026-08-01", "2026-08-02", "2026-08-03"):
            result = compute_forward_metrics(series, decision_date)
            if result["status"] == "OK":
                self.assertGreater(result["hypothetical_entry_at"], result["action_eligible_at"])
                self.assertEqual(result["hypothetical_entry_at"], result["entry_date"])

    def test_entry_is_never_priced_from_a_date_at_or_before_decision_date(self):
        # Regression for the confirmed round-4 bug: a signal evaluated
        # against an earlier close must never be graded as if entry
        # happened at that earlier close.
        series = series_from_closes("X", 19, [100.0, 999.0])  # 2026-08-19=100, 2026-08-20=999
        result = compute_forward_metrics(series, "2026-08-20", signal_evaluation_at="2026-08-19")
        # decision_date=action_eligible_at=08-20; the only date STRICTLY
        # after it in this tiny series doesn't exist -> NOT_GRADABLE, not
        # silently priced at 08-19's 100.0 or even 08-20's own 999.0.
        self.assertEqual(result["status"], "NOT_GRADABLE")

    def test_no_forward_date_available_is_not_gradable_not_backdated(self):
        series = series_from_closes("X", 1, [100.0])  # only one bar, nothing after it
        result = compute_forward_metrics(series, "2026-08-01")
        self.assertEqual(result["status"], "NOT_GRADABLE")
        self.assertIn("not_gradable_reason", result)
        for h in ("1", "3", "5", "10"):
            self.assertEqual(result["horizons"][h]["status"], "NOT_GRADABLE")


class ConfirmedBugRegressionTests(unittest.TestCase):
    """Direct regression for the concrete case CIO cited: a BTC-like signal
    evaluated against the PRIOR day's close (collector T-1 lag) must be
    graded from the NEXT real trading day's open, never from that prior
    close."""

    def test_signal_evaluated_on_prior_day_close_is_graded_from_next_day_open_not_prior_close(self):
        s = PriceSeries("BTC")
        s._merge_row("2026-08-19", {"close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0}, "2026-08-20")
        s._merge_row("2026-08-20", {"close": 200.0, "open": 150.0, "high": 210.0, "low": 149.0}, "2026-08-21")
        s._merge_row("2026-08-21", {"close": 220.0, "open": 205.0, "high": 225.0, "low": 200.0}, "2026-08-22")
        # decision_date = 2026-08-20 (action_eligible_at); the trigger's own
        # signal_evaluation_at is 2026-08-19 (the prior day's finalized
        # close, per collector lag) -- entry must be 08-21's open (150 was
        # ALREADY the past by the time of decision -- 08-20's own close/open
        # also already happened before the signal was known; the first
        # genuinely tradable point is 08-21).
        result = compute_forward_metrics(s, "2026-08-20", signal_evaluation_at="2026-08-19")
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["hypothetical_entry_at"], "2026-08-21")
        self.assertEqual(result["entry_price"], 205.0)  # 08-21's OPEN, never 08-19's 100 or 08-20's 150/200
        self.assertNotEqual(result["entry_price"], 100.0)
        self.assertNotEqual(result["entry_price"], 150.0)
        self.assertNotEqual(result["entry_price"], 200.0)


class HorizonComputationTests(unittest.TestCase):
    def test_all_four_horizons_present(self):
        self.assertEqual(set(HORIZONS), {1, 3, 5, 10})

    def test_horizon_1_is_entry_open_vs_entry_days_own_close(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-01", {"close": 100.0, "open": 100.0, "high": 100.0, "low": 100.0}, "2026-08-01")
        s._merge_row("2026-08-02", {"close": 110.0, "open": 105.0, "high": 111.0, "low": 104.0}, "2026-08-02")
        result = compute_forward_metrics(s, "2026-08-01")
        self.assertEqual(result["hypothetical_entry_at"], "2026-08-02")
        self.assertEqual(result["entry_price"], 105.0)
        h1 = result["horizons"]["1"]
        self.assertEqual(h1["status"], "OK")
        self.assertEqual(h1["end_date"], "2026-08-02")
        self.assertAlmostEqual(h1["forward_return_pct"], (110.0 - 105.0) / 105.0 * 100.0, places=6)

    def test_mfe_mae_use_high_low_not_just_close(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-01", {"close": 100.0, "open": 100.0, "high": 100.0, "low": 100.0}, "2026-08-01")
        s._merge_row("2026-08-02", {"close": 100.0, "open": 100.0, "high": 102.0, "low": 98.0}, "2026-08-02")
        result = compute_forward_metrics(s, "2026-08-01")
        h1 = result["horizons"]["1"]
        self.assertAlmostEqual(h1["mfe_pct"], 2.0, places=6)
        self.assertAlmostEqual(h1["mae_pct"], -2.0, places=6)

    def test_insufficient_horizon_data_reported_not_fabricated(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-01", {"close": 100.0, "open": 100.0, "high": 100.0, "low": 100.0}, "2026-08-01")
        s._merge_row("2026-08-02", {"close": 101.0, "open": 101.0, "high": 101.0, "low": 101.0}, "2026-08-02")
        result = compute_forward_metrics(s, "2026-08-01")
        self.assertEqual(result["horizons"]["1"]["status"], "OK")
        for h in ("3", "5", "10"):
            self.assertEqual(result["horizons"][h]["status"], "INSUFFICIENT_HORIZON_DATA")
            self.assertNotIn("forward_return_pct", result["horizons"][h])


if __name__ == "__main__":
    unittest.main()
