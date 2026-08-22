#!/usr/bin/env python3
"""P11 PIT Replay -- Signal Replay / Opportunity Miss / Defense ledger
regression (deliverables 1-3), plus determinism and winner/loser symmetry."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import defense_ledger as dl  # noqa: E402
from replay import opportunity_miss_ledger as oml  # noqa: E402
from replay import ruleset_comparison as rsc  # noqa: E402
from replay.price_series import PriceSeries  # noqa: E402
from replay.signal_replay_ledger import build_entry, classify_gap  # noqa: E402


def series_with_closes(subject, closes_by_date):
    s = PriceSeries(subject)
    for date, close in closes_by_date.items():
        s._merge_row(date, {"close": close, "open": close, "high": close, "low": close}, date)
    return s


def flat_then_move(subject, up: bool):
    dates = [f"2026-08-{d:02d}" for d in range(1, 12)]
    closes = {d: 100.0 for d in dates}
    move = [102, 105, 110, 115, 118] if up else [98, 95, 90, 85, 82]
    for i, d in enumerate(["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15"]):
        closes[d] = float(move[i])
    return series_with_closes(subject, closes)


class SignalReplayLedgerTests(unittest.TestCase):
    def test_build_entry_is_deterministic(self):
        s = flat_then_move("X", up=True)
        e1 = build_entry(s, "2026-08-10", "src", "a" * 64)
        e2 = build_entry(s, "2026-08-10", "src", "a" * 64)
        self.assertEqual(e1, e2)
        self.assertEqual(e1["entry_hash"], e2["entry_hash"])

    def test_entry_trade_proposal_is_always_null_and_no_stage_or_order_field_exists(self):
        s = flat_then_move("X", up=True)
        e = build_entry(s, "2026-08-10", "src", "a" * 64)
        # `existing_ruleset.trade_proposal` is intentionally always None -- it
        # documents decision/alpha_review.py's own real structural constant,
        # never a real proposal this module could generate.
        self.assertIsNone(e["existing_ruleset"]["trade_proposal"])
        blob = str(e)
        for forbidden in ("stage_change", "\"order\"", "production_authorized\": true"):
            self.assertNotIn(forbidden, blob)
        self.assertEqual(e["proposed_ruleset"]["capital"], 0)


class OpportunityMissAndDefenseLedgerSymmetryTests(unittest.TestCase):
    """Same construction path, same materiality/horizon policy, applied to a
    winner series and a loser series -- proves no special-casing by
    direction."""

    def test_miss_and_defense_ledgers_use_the_same_horizon_preference_order(self):
        self.assertEqual(oml.PREFERRED_HORIZONS, dl.PREFERRED_HORIZONS)

    def test_material_up_move_is_a_candidate_miss_and_material_down_move_is_a_candidate_defense(self):
        up = flat_then_move("UP", up=True)
        down = flat_then_move("DOWN", up=False)
        entry_up = build_entry(up, "2026-08-10", "src", "a" * 64)
        entry_down = build_entry(down, "2026-08-10", "src", "a" * 64)

        self.assertTrue(oml.is_material_miss(entry_up))
        self.assertFalse(oml.is_material_miss(entry_down))
        self.assertTrue(dl.is_material_defense(entry_down))
        self.assertFalse(dl.is_material_defense(entry_up))

    def test_defense_records_always_carry_the_structural_zero_capital_caveat(self):
        down = flat_then_move("DOWN", up=False)
        entry_down = build_entry(down, "2026-08-10", "src", "a" * 64)
        records = dl.build_defense_records([entry_down])
        self.assertEqual(len(records), 1)
        self.assertIn("structural default", records[0]["structural_zero_capital_caveat"])

    def test_miss_records_get_a_root_cause_from_the_same_seven_category_set(self):
        from replay import root_cause as rc
        up = flat_then_move("UP", up=True)
        entry_up = build_entry(up, "2026-08-10", "src", "a" * 64)
        records = oml.build_miss_records([entry_up])
        self.assertEqual(len(records), 1)
        self.assertIn(records[0]["root_cause"], rc.CATEGORIES)


class RulesetComparisonTests(unittest.TestCase):
    def test_existing_ruleset_action_conversion_is_always_zero(self):
        up = flat_then_move("UP", up=True)
        entry = build_entry(up, "2026-08-10", "src", "a" * 64)
        comparison = rsc.compare([entry])
        self.assertEqual(comparison["existing_ruleset"]["convertible_count"], 0)

    def test_comparison_runs_over_the_identical_entry_list_for_both_rulesets(self):
        up = flat_then_move("UP", up=True)
        entry = build_entry(up, "2026-08-10", "src", "a" * 64)
        comparison = rsc.compare([entry])
        self.assertEqual(
            comparison["population"]["total_entries"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
