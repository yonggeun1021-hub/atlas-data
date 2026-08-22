#!/usr/bin/env python3
"""P11 PIT Replay -- proposed Opportunity Trigger Engine regression."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import trigger_engine as te  # noqa: E402
from replay.price_series import PriceSeries  # noqa: E402


def series_with_closes(subject, closes_by_date, net_value_by_date=None):
    s = PriceSeries(subject)
    for date, close in closes_by_date.items():
        row = {"close": close, "open": close, "high": close, "low": close}
        if net_value_by_date and date in net_value_by_date:
            row["net_value"] = {"외국인합계": net_value_by_date[date]}
        s._merge_row(date, row, date)  # capture_date == trading_date -> live-known immediately
    return s


class PriceConfirmationTests(unittest.TestCase):
    def test_new_20d_high_fires(self):
        dates = [f"2026-08-{d:02d}" for d in range(1, 21)]
        closes = {d: 100.0 for d in dates}
        closes[dates[-1]] = 150.0  # last day breaks out
        s = series_with_closes("X", closes)
        events = te.price_confirmation(s, dates[-1], "src", "a" * 64, lookback=20)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trigger_type, "PRICE_CONFIRMATION")

    def test_no_breakout_when_not_a_new_high(self):
        dates = [f"2026-08-{d:02d}" for d in range(1, 21)]
        closes = {d: 100.0 for d in dates}
        closes[dates[10]] = 150.0  # an earlier high, not today
        s = series_with_closes("X", closes)
        events = te.price_confirmation(s, dates[-1], "src", "a" * 64, lookback=20)
        self.assertEqual(events, [])

    def test_insufficient_lookback_returns_no_events_not_a_crash(self):
        s = series_with_closes("X", {"2026-08-01": 100.0, "2026-08-02": 101.0})
        events = te.price_confirmation(s, "2026-08-02", "src", "a" * 64, lookback=20)
        self.assertEqual(events, [])


class InvalidationTriggerTests(unittest.TestCase):
    def test_new_20d_low_fires(self):
        dates = [f"2026-08-{d:02d}" for d in range(1, 21)]
        closes = {d: 100.0 for d in dates}
        closes[dates[-1]] = 50.0
        s = series_with_closes("X", closes)
        events = te.invalidation_trigger(s, dates[-1], "src", "a" * 64, lookback=20)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trigger_type, "INVALIDATION_TRIGGER")


class FlowReversalTests(unittest.TestCase):
    def test_reversal_from_two_negative_days_to_positive_fires(self):
        dates = ["2026-08-11", "2026-08-12", "2026-08-13"]
        closes = {d: 100.0 for d in dates}
        flows = {"2026-08-11": -1.0, "2026-08-12": -1.0, "2026-08-13": 1.0}
        s = series_with_closes("X", closes, flows)
        events = te.flow_reversal(s, "2026-08-13", "src", "a" * 64, trailing_opposite_days=2)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trigger_type, "FLOW_REVERSAL")

    def test_no_reversal_when_still_negative(self):
        dates = ["2026-08-11", "2026-08-12", "2026-08-13"]
        closes = {d: 100.0 for d in dates}
        flows = {"2026-08-11": -1.0, "2026-08-12": -1.0, "2026-08-13": -1.0}
        s = series_with_closes("X", closes, flows)
        events = te.flow_reversal(s, "2026-08-13", "src", "a" * 64, trailing_opposite_days=2)
        self.assertEqual(events, [])

    def test_missing_flow_data_returns_no_events_not_a_fabricated_reversal(self):
        dates = ["2026-08-11", "2026-08-12", "2026-08-13"]
        closes = {d: 100.0 for d in dates}
        s = series_with_closes("X", closes)  # no net_value at all
        events = te.flow_reversal(s, "2026-08-13", "src", "a" * 64, trailing_opposite_days=2)
        self.assertEqual(events, [])


class RelativeStrengthReversalTests(unittest.TestCase):
    def test_outperformance_vs_peers_fires(self):
        dates = [f"2026-08-{d:02d}" for d in range(10, 15)]
        subj = series_with_closes("A", {d: 100.0 * (1.01 ** i) for i, d in enumerate(dates)})
        peer = series_with_closes("B", {d: 100.0 for d in dates})
        events = te.relative_strength_reversal(subj, {"B": peer}, dates[-1], "src", "a" * 64, lookback=5)
        self.assertEqual(len(events), 1)

    def test_no_peers_returns_not_computable_empty_list(self):
        dates = [f"2026-08-{d:02d}" for d in range(10, 15)]
        subj = series_with_closes("A", {d: 100.0 * (1.01 ** i) for i, d in enumerate(dates)})
        events = te.relative_strength_reversal(subj, {}, dates[-1], "src", "a" * 64, lookback=5)
        self.assertEqual(events, [])


class DetectAllHasNoAuthorityTests(unittest.TestCase):
    def test_detect_all_returns_only_opportunity_trigger_events(self):
        dates = [f"2026-08-{d:02d}" for d in range(1, 21)]
        closes = {d: 100.0 for d in dates}
        closes[dates[-1]] = 200.0
        s = series_with_closes("X", closes)
        events = te.detect_all(s, dates[-1], "src", "a" * 64)
        for ev in events:
            self.assertTrue(hasattr(ev, "trigger_id"))
            self.assertEqual(ev.subject, "X")


if __name__ == "__main__":
    unittest.main()
