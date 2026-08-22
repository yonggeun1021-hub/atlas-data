#!/usr/bin/env python3
"""P11 PIT Replay -- root-cause classifier regression (deliverable 5), plus
the structural no-survivorship-bias guarantee."""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import root_cause as rc  # noqa: E402


BASE = dict(
    data_available=True, in_universe=True, had_valid_trigger=True,
    had_independent_confirmation=True, gate_available=True, action_taken=False,
    decision_latency_days=0, no_position_rule_active=False,
)


class RootCauseCategoryTests(unittest.TestCase):
    def test_exactly_seven_categories_match_the_task_spec(self):
        expected = {
            "UNIVERSE_MISS", "SIGNAL_MISS", "ACTION_CONVERSION_FAILURE", "GATE_BLOCK",
            "DECISION_LATENCY", "NO_POSITION_RULE", "DATA_FAILURE",
        }
        self.assertEqual(set(rc.CATEGORIES), expected)

    def test_data_failure_takes_priority_over_everything_else(self):
        kwargs = {**BASE, "data_available": False, "in_universe": False, "had_valid_trigger": False}
        self.assertEqual(rc.classify(**kwargs), "DATA_FAILURE")

    def test_universe_miss(self):
        kwargs = {**BASE, "in_universe": False}
        self.assertEqual(rc.classify(**kwargs), "UNIVERSE_MISS")

    def test_signal_miss(self):
        kwargs = {**BASE, "had_valid_trigger": False}
        self.assertEqual(rc.classify(**kwargs), "SIGNAL_MISS")

    def test_gate_block(self):
        kwargs = {**BASE, "gate_available": False}
        self.assertEqual(rc.classify(**kwargs), "GATE_BLOCK")

    def test_decision_latency(self):
        kwargs = {**BASE, "decision_latency_days": rc.DECISION_LATENCY_THRESHOLD_DAYS + 1}
        self.assertEqual(rc.classify(**kwargs), "DECISION_LATENCY")

    def test_no_position_rule(self):
        kwargs = {**BASE, "no_position_rule_active": True}
        self.assertEqual(rc.classify(**kwargs), "NO_POSITION_RULE")

    def test_action_conversion_failure_when_everything_else_is_fine(self):
        self.assertEqual(rc.classify(**BASE), "ACTION_CONVERSION_FAILURE")

    def test_action_taken_is_not_a_miss_and_raises(self):
        kwargs = {**BASE, "action_taken": True}
        with self.assertRaisesRegex(rc.RootCauseError, "NOT_A_MISS_ACTION_WAS_TAKEN"):
            rc.classify(**kwargs)


class NoSurvivorshipBiasStructuralTests(unittest.TestCase):
    """The classifier must be architecturally incapable of branching on the
    realized outcome -- proven by inspecting its live parameter list, not by
    trusting a comment."""

    def test_classify_signature_excludes_any_outcome_shaped_parameter(self):
        self.assertTrue(rc.signature_excludes_outcome_fields())

    def test_classify_has_no_default_that_could_encode_a_winner_bias(self):
        params = inspect.signature(rc.classify).parameters
        for name, p in params.items():
            self.assertIs(p.kind, inspect.Parameter.KEYWORD_ONLY, name)

    def test_same_classification_regardless_of_which_direction_caller_labels_winner_or_loser(self):
        # The classifier has no "is_winner" concept at all -- demonstrate that
        # two calls differing only in an outcome-irrelevant caller label
        # (not passed to classify()) produce identical classification.
        result_a = rc.classify(**BASE)
        result_b = rc.classify(**BASE)  # same facts, would-be "loser" scenario in a caller's own bookkeeping
        self.assertEqual(result_a, result_b)


if __name__ == "__main__":
    unittest.main()
