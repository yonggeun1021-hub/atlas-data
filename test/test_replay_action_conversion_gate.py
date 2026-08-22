#!/usr/bin/env python3
"""P11 PIT Replay -- proposed Action Conversion Gate regression.

CIO review (PR #210, flaws 2/3): conditions use a real
PASS/FAIL/NOT_EVALUATED/NOT_COMPUTABLE vocabulary and are actually computed
from real inputs -- no fabricated shortcuts. This file directly re-proves
each of the specific defects the review called out.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import action_conversion_gate as acg  # noqa: E402
from replay.opportunity_trigger import build_trigger_event  # noqa: E402
from replay.price_series import PriceSeries  # noqa: E402


def trig(ttype, subject="BTC", date="2026-08-13", source="src", evidence_sha=None):
    return build_trigger_event(ttype, subject, date, date, source, evidence_sha or "a" * 64, 0.6, confirmed_at=date)


class ConditionVocabularyTests(unittest.TestCase):
    def test_only_four_statuses_exist_and_true_false_are_never_returned(self):
        result = acg.evaluate("BTC", "2026-08-13", [], None, None)
        for field in (
            result.condition_1_hypothesis_or_catalyst, result.condition_2_independent_confirmation,
            result.condition_3_entry_zone, result.condition_4_invalidation,
            result.condition_5_position_sizing, result.condition_6_pit_data_integrity,
            result.condition_7_gate_ratified,
        ):
            self.assertIn(field, acg.CONDITION_STATUSES)
            self.assertNotIsInstance(field, bool)


class Condition1Tests(unittest.TestCase):
    """CIO flaw 3: condition 1 must verify a REAL, grounded evidence
    citation, not just count(triggers) >= 1."""

    def test_zero_triggers_fails(self):
        result = acg.evaluate("BTC", "2026-08-13", [], None, None)
        self.assertEqual(result.condition_1_hypothesis_or_catalyst, "FAIL")
        self.assertEqual(result.recommended_action, "NONE")

    def test_real_grounded_trigger_passes(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.condition_1_hypothesis_or_catalyst, "PASS")

    def test_trigger_with_sentinel_missing_evidence_source_fails(self):
        t = trig("PRICE_CONFIRMATION", source="NO_BTC_SNAPSHOT_AVAILABLE_AT_OR_BEFORE_DECISION_DATE")
        result = acg.evaluate("BTC", "2026-08-13", [t], 100.0, 90.0)
        self.assertEqual(result.condition_1_hypothesis_or_catalyst, "FAIL")


class Condition2Tests(unittest.TestCase):
    def test_zero_triggers_is_not_evaluated(self):
        result = acg.evaluate("BTC", "2026-08-13", [], None, None)
        self.assertEqual(result.condition_2_independent_confirmation, "NOT_EVALUATED")

    def test_single_trigger_type_fails(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.condition_2_independent_confirmation, "FAIL")

    def test_two_distinct_trigger_types_pass(self):
        triggers = [trig("PRICE_CONFIRMATION"), trig("FLOW_REVERSAL")]
        result = acg.evaluate("BTC", "2026-08-13", triggers, 100.0, 90.0)
        self.assertEqual(result.condition_2_independent_confirmation, "PASS")


class Condition3And4Tests(unittest.TestCase):
    def test_missing_entry_price_not_computable(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], None, 90.0)
        self.assertEqual(result.condition_3_entry_zone, "NOT_COMPUTABLE")

    def test_missing_invalidation_not_computable(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, None)
        self.assertEqual(result.condition_4_invalidation, "NOT_COMPUTABLE")

    def test_invalidation_at_or_above_entry_fails_as_nonsensical(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 105.0)
        self.assertEqual(result.condition_4_invalidation, "FAIL")

    def test_valid_below_entry_invalidation_passes(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.condition_4_invalidation, "PASS")


class Condition5RealArithmeticTests(unittest.TestCase):
    """CIO flaw 3: condition 5 must be a real max-loss computation, not
    `cond3 and cond4`."""

    def test_max_loss_pct_is_real_arithmetic(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.condition_5_position_sizing, "PASS")
        self.assertAlmostEqual(result.max_loss_pct, 10.0, places=6)

    def test_no_computation_shortcut_exists_in_source(self):
        source = (ROOT / "replay" / "action_conversion_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("cond5 = cond3 and cond4", source)

    def test_out_of_sane_bounds_max_loss_fails(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 10.0)  # 90% loss
        self.assertEqual(result.condition_5_position_sizing, "FAIL")
        self.assertGreater(result.max_loss_pct, acg.MAX_SANE_LOSS_PCT)

    def test_missing_inputs_yields_not_computable_and_none_max_loss(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], None, None)
        self.assertEqual(result.condition_5_position_sizing, "NOT_COMPUTABLE")
        self.assertIsNone(result.max_loss_pct)


class Condition6RealIntegrityCheckTests(unittest.TestCase):
    """CIO flaw 3: condition 6 must be a real check against the series' own
    recorded integrity_conflicts, not hard-coded True."""

    def test_no_series_supplied_is_not_computable(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.condition_6_pit_data_integrity, "NOT_COMPUTABLE")

    def test_clean_series_passes(self):
        s = PriceSeries("BTC")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0,
                               series=s, evaluation_date="2026-08-13", lookback_dates=["2026-08-13"])
        self.assertEqual(result.condition_6_pit_data_integrity, "PASS")

    def test_real_integrity_conflict_in_evaluation_window_fails(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        s._merge_row("2026-08-13", {"close": 105.0, "open": 100, "high": 101, "low": 99}, "2026-08-14")
        self.assertEqual(len(s.integrity_conflicts), 1)
        result = acg.evaluate("X", "2026-08-13", [trig("PRICE_CONFIRMATION", subject="X")], 100.0, 90.0,
                               series=s, evaluation_date="2026-08-13", lookback_dates=["2026-08-13"])
        self.assertEqual(result.condition_6_pit_data_integrity, "FAIL")

    def test_no_hardcoded_true_shortcut_exists_in_source(self):
        source = (ROOT / "replay" / "action_conversion_gate.py").read_text(encoding="utf-8")
        self.assertNotIn('cond6 = True', source)


class Condition7RealPolicyFileCheckTests(unittest.TestCase):
    """CIO flaw 3: condition 7 must check a real ratified policy file per
    this repo's OWN established convention, not an arbitrary invented
    sentinel string."""

    def test_no_arbitrary_invented_sentinel_string_in_source(self):
        source = (ROOT / "replay" / "action_conversion_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("PROBE_RULE_RATIFIED", source)

    def test_checks_real_config_policy_convention(self):
        source = (ROOT / "replay" / "action_conversion_gate.py").read_text(encoding="utf-8")
        self.assertIn("approval_status", source)
        self.assertIn("RATIFIED", source)

    def test_convention_is_grounded_against_a_real_committed_policy_file(self):
        real_policy = ROOT / "config" / "korea_leadership_policy.json"
        self.assertTrue(real_policy.is_file())
        doc = json.loads(real_policy.read_text(encoding="utf-8"))
        self.assertEqual(doc.get("approval_status"), "RATIFIED")  # the real convention this module checks for

    def test_no_probe_policy_exists_today_so_condition_7_fails_for_a_real_grounded_reason(self):
        status, detail = acg._gate_ratified_status()
        self.assertEqual(status, "FAIL")
        self.assertIn("config/", detail)
        self.assertFalse(acg.gate_available())


class GateBlockEligibilityTests(unittest.TestCase):
    """CIO flaw 2: conditions_1_to_6_all_pass must be True only when EVERY
    ONE of 1-6 is a real PASS."""

    def test_all_six_pass_when_fully_qualified(self):
        s = PriceSeries("BTC")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        triggers = [trig("PRICE_CONFIRMATION"), trig("FLOW_REVERSAL")]
        result = acg.evaluate("BTC", "2026-08-13", triggers, 100.0, 90.0,
                               series=s, evaluation_date="2026-08-13", lookback_dates=["2026-08-13"])
        self.assertTrue(result.conditions_1_to_6_all_pass)
        self.assertEqual(result.condition_7_gate_ratified, "FAIL")  # no ratified rule exists in this repo today
        self.assertEqual(result.recommended_action, "NONE")

    def test_single_trigger_type_means_conditions_not_all_pass(self):
        s = PriceSeries("BTC")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0,
                               series=s, evaluation_date="2026-08-13", lookback_dates=["2026-08-13"])
        self.assertFalse(result.conditions_1_to_6_all_pass)


class CapitalHardCodedTests(unittest.TestCase):
    def test_capital_is_always_zero_and_no_parameter_can_override_it(self):
        params = set(inspect.signature(acg.evaluate).parameters)
        self.assertNotIn("capital", params)
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.capital, 0)

    def test_result_is_frozen(self):
        result = acg.evaluate("BTC", "2026-08-13", [], None, None)
        with self.assertRaises(Exception):
            result.capital = 1  # type: ignore[misc]

    def test_result_serializes_with_dataclasses_asdict(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        d = dataclasses.asdict(result)
        self.assertEqual(d["capital"], 0)
        self.assertIn("recommended_action", d)


if __name__ == "__main__":
    unittest.main()
