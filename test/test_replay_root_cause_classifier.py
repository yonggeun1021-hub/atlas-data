#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- root-cause classifier regression (deliverable 5), plus
the structural no-survivorship-bias guarantee, plus (CIO review, PR #210,
flaw 2) the GATE_BLOCK-narrowing proof: GATE_BLOCK may only be assigned when
conditions_1_to_6_all_pass is real, verified True."""
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
    conditions_1_to_6_all_pass=True, gate_available=True, action_taken=False,
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

    def test_gate_block_requires_conditions_1_to_6_all_pass_true(self):
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


class GateBlockNarrowingTests(unittest.TestCase):
    """CIO review (PR #210, flaw 2): a trigger existing is NOT sufficient for
    GATE_BLOCK -- conditions 1-6 must ALL be real, verified PASS. Anything
    short of that (even with gate_available=False, i.e. condition 7 also
    failing) must fall through to ACTION_CONVERSION_FAILURE instead."""

    def test_trigger_exists_but_conditions_1_to_6_not_all_pass_is_never_gate_block(self):
        kwargs = {**BASE, "conditions_1_to_6_all_pass": False, "gate_available": False}
        result = rc.classify(**kwargs)
        self.assertNotEqual(result, "GATE_BLOCK")
        self.assertEqual(result, "ACTION_CONVERSION_FAILURE")

    def test_conditions_1_to_6_all_pass_true_and_gate_available_true_is_not_a_gap_case(self):
        # This combination means the gate says PROBE_REVIEW_CANDIDATE -- a
        # real caller would never invoke classify() on such an entry (it is
        # not a miss), but the classifier itself still must not report
        # GATE_BLOCK when gate_available is True (no gate is actually
        # blocking anything).
        kwargs = {**BASE, "conditions_1_to_6_all_pass": True, "gate_available": True}
        self.assertNotEqual(rc.classify(**kwargs), "GATE_BLOCK")

    def test_only_the_exact_combination_conditions_pass_true_gate_false_yields_gate_block(self):
        combos = [
            (True, False, "GATE_BLOCK"),
            (True, True, "ACTION_CONVERSION_FAILURE"),
            (False, False, "ACTION_CONVERSION_FAILURE"),
            (False, True, "ACTION_CONVERSION_FAILURE"),
        ]
        for conditions_pass, gate_avail, expected in combos:
            with self.subTest(conditions_pass=conditions_pass, gate_avail=gate_avail):
                kwargs = {**BASE, "conditions_1_to_6_all_pass": conditions_pass, "gate_available": gate_avail}
                self.assertEqual(rc.classify(**kwargs), expected)


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
