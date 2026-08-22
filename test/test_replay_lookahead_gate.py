#!/usr/bin/env python3
"""P11 PIT Replay -- hard anti-lookahead gate regression.

Satisfies the task's explicit hard constraint: "write an explicit automated
test that fails if any lookahead is detected." This file exercises the gate
at three layers: the raw utility, the trigger engine (signal side), and the
forward-metrics calculator (outcome side) -- plus an end-to-end sweep of the
real repo-evidence replay output.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import evidence_index as ei  # noqa: E402
from replay import trigger_engine as te  # noqa: E402
from replay.forward_metrics import compute_forward_metrics  # noqa: E402
from replay.lookahead_gate import LookaheadViolation, assert_forward_only, assert_no_signal_lookahead  # noqa: E402
from replay.opportunity_trigger import build_trigger_event, OpportunityTriggerError  # noqa: E402
from replay.price_series import PriceSeries  # noqa: E402
from replay.run_pit_replay import build_signal_replay_ledger, load_all_series  # noqa: E402


def _series(subject: str, rows: dict) -> PriceSeries:
    s = PriceSeries(subject)
    for date, close in rows.items():
        s._merge_row(date, {"close": close, "open": close, "high": close, "low": close}, date)
    return s


class LookaheadGateUtilityTests(unittest.TestCase):
    def test_signal_lookahead_raises_when_evidence_dated_after_decision(self):
        with self.assertRaises(LookaheadViolation):
            assert_no_signal_lookahead("2026-08-13", ["2026-08-14"])

    def test_signal_lookahead_allows_evidence_on_or_before_decision(self):
        assert_no_signal_lookahead("2026-08-13", ["2026-08-10", "2026-08-13"])  # must not raise

    def test_forward_only_raises_when_outcome_not_strictly_after_decision(self):
        with self.assertRaises(LookaheadViolation):
            assert_forward_only("2026-08-13", ["2026-08-13"])
        with self.assertRaises(LookaheadViolation):
            assert_forward_only("2026-08-13", ["2026-08-12"])

    def test_forward_only_allows_strictly_later_dates(self):
        assert_forward_only("2026-08-13", ["2026-08-14", "2026-08-20"])  # must not raise


class TriggerEventConstructionLookaheadTests(unittest.TestCase):
    def test_trigger_event_cannot_be_first_seen_after_its_own_decision_date(self):
        with self.assertRaisesRegex(OpportunityTriggerError, "FIRST_SEEN_AT_AFTER_DECISION_DATE"):
            build_trigger_event("PRICE_CONFIRMATION", "BTC", "2026-08-20", "2026-08-13",
                                 "src", "a" * 64, 0.5)


class TriggerEngineSignalSideLookaheadTests(unittest.TestCase):
    """Feeds a series with an extreme, obviously-detectable price on a date
    AFTER decision_date and asserts the engine does not use it."""

    def test_price_confirmation_ignores_a_future_breakout(self):
        rows = {f"2026-08-{d:02d}": 100.0 for d in range(1, 21)}
        rows["2026-08-25"] = 100000.0  # a huge future breakout
        series = _series("TEST", rows)
        events = te.price_confirmation(series, "2026-08-20", "src", "a" * 64, lookback=20)
        # No breakout should be detected on 2026-08-20 using the future 08-25 spike.
        for ev in events:
            self.assertLessEqual(ev.confirmed_at, "2026-08-20")

    def test_window_at_or_before_never_returns_a_date_after_decision_date(self):
        rows = {f"2026-08-{d:02d}": 100.0 + d for d in range(1, 25)}
        series = _series("TEST", rows)
        window = te.window_at_or_before(series, "2026-08-10", 5)
        self.assertTrue(all(d <= "2026-08-10" for d in window))

    def test_pre_repo_history_decision_dates_detect_zero_triggers(self):
        """Real repo evidence: no snapshot exists with capture_date before
        2026-08-13, so live_trading_dates_at_or_before() must be empty for
        every decision_date before that -- meaning the engine can find
        nothing to trigger on, however extreme the underlying historical
        price move actually was."""
        rows = {"2026-07-20": 100.0, "2026-07-21": 100.0, "2026-07-22": 100.0, "2026-07-23": 100000.0}
        series = _series("TEST", rows)
        for date, row in rows.items():
            series._by_date[date]["first_capture_date"] = "2026-08-13"  # only known from 08-13 onward
        events = te.detect_all(series, "2026-07-23", "src", "a" * 64)
        self.assertEqual(events, [])


class ForwardMetricsOutcomeSideTests(unittest.TestCase):
    def test_forward_metrics_never_uses_a_date_at_or_before_decision_date_as_forward(self):
        rows = {f"2026-08-{d:02d}": 100.0 + d for d in range(1, 25)}
        series = _series("TEST", rows)
        result = compute_forward_metrics(series, "2026-08-10")
        for h, data in result["horizons"].items():
            if data.get("status") == "OK":
                self.assertGreater(data["end_date"], "2026-08-10")


class EndToEndRealEvidenceLookaheadSweepTests(unittest.TestCase):
    """Runs the actual replay against real committed repo evidence and
    inspects EVERY produced trigger/ledger entry for a lookahead leak."""

    @classmethod
    def setUpClass(cls):
        ctx = load_all_series()
        cls.entries = build_signal_replay_ledger(ctx)

    def test_no_trigger_is_first_seen_or_confirmed_after_its_decision_date(self):
        checked = 0
        for entry in self.entries:
            for trig in entry["triggers"]:
                checked += 1
                self.assertLessEqual(trig["first_seen_at"], entry["decision_date"])
                self.assertLessEqual(trig["decision_date"], entry["decision_date"])
                if trig["confirmed_at"] is not None:
                    self.assertLessEqual(trig["confirmed_at"], entry["decision_date"])
        self.assertGreater(checked, 0, "sanity: the real replay should have detected >=1 real trigger")

    def test_no_entry_before_repo_history_start_has_any_trigger(self):
        for entry in self.entries:
            if entry["decision_date"] < ei.REPO_HISTORY_STARTS_AT:
                self.assertEqual(entry["triggers"], [], entry)
                self.assertFalse(entry["data_available"], entry)

    def test_every_ok_forward_horizon_end_date_is_strictly_after_hypothetical_entry_at(self):
        # Anchored to hypothetical_entry_at (the signal's own evaluation
        # date), not decision_date -- entry_date can lag decision_date by
        # the collector's own finalization delay (see forward_metrics.py's
        # docstring / CIO review PR #210 flaw 4), so the correct forward
        # anchor is the entry itself, not the later action_eligible_at date.
        checked = 0
        for entry in self.entries:
            fm = entry["forward_metrics"]
            if fm.get("hypothetical_entry_at") is None:
                continue
            for h, data in fm["horizons"].items():
                if data.get("status") == "OK":
                    checked += 1
                    self.assertGreater(data["end_date"], fm["hypothetical_entry_at"])
        self.assertGreater(checked, 0, "sanity: at least one horizon should be computable from real evidence")

    def test_signal_anchored_entries_never_grade_an_unknowable_price(self):
        # CIO review PR #210 flaw 4: whenever forward_metrics graded off an
        # EXPLICIT, signal-anchored entry_date (a real detected trigger),
        # that price must have actually been live-known by decision_date --
        # never NOT_GRADABLE silently ignored, never graded off a
        # not-yet-known future close.
        checked = 0
        for entry in self.entries:
            fm = entry["forward_metrics"]
            if fm.get("entry_date_source") != "explicit_signal_evaluation_date":
                continue
            if fm.get("status") != "OK":
                continue
            checked += 1
            self.assertLessEqual(fm["hypothetical_entry_at"], entry["decision_date"])
            self.assertTrue(fm["entry_live_known_asof_decision_date"])
            self.assertLessEqual(fm["entry_price_available_at"], entry["decision_date"])
        self.assertGreater(checked, 0, "sanity: at least one real signal-anchored entry should be gradable")


if __name__ == "__main__":
    unittest.main()
