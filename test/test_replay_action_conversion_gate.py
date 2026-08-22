#!/usr/bin/env python3
"""P11 PIT Replay -- proposed Action Conversion Gate regression."""
from __future__ import annotations

import dataclasses
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import action_conversion_gate as acg  # noqa: E402
from replay.opportunity_trigger import build_trigger_event  # noqa: E402


def trig(ttype, subject="BTC", date="2026-08-13"):
    return build_trigger_event(ttype, subject, date, date, "src", "a" * 64, 0.6, confirmed_at=date)


class ActionConversionGateTests(unittest.TestCase):
    def test_capital_is_always_zero_and_no_parameter_can_override_it(self):
        params = set(inspect.signature(acg.evaluate).parameters)
        self.assertNotIn("capital", params)
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.capital, 0)

    def test_zero_triggers_fails_condition_1_and_recommends_none(self):
        result = acg.evaluate("BTC", "2026-08-13", [], None, None)
        self.assertFalse(result.condition_1_hypothesis_or_catalyst)
        self.assertEqual(result.recommended_action, "NONE")

    def test_single_trigger_type_fails_independent_confirmation(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertTrue(result.condition_1_hypothesis_or_catalyst)
        self.assertFalse(result.condition_2_independent_confirmation)
        self.assertEqual(result.recommended_action, "NONE")

    def test_two_distinct_trigger_types_satisfy_independent_confirmation(self):
        triggers = [trig("PRICE_CONFIRMATION"), trig("FLOW_REVERSAL")]
        result = acg.evaluate("BTC", "2026-08-13", triggers, 100.0, 90.0)
        self.assertTrue(result.condition_2_independent_confirmation)

    def test_missing_entry_or_invalidation_price_fails_conditions_3_4_5(self):
        triggers = [trig("PRICE_CONFIRMATION"), trig("FLOW_REVERSAL")]
        result = acg.evaluate("BTC", "2026-08-13", triggers, None, None)
        self.assertFalse(result.condition_3_entry_zone)
        self.assertFalse(result.condition_4_invalidation)
        self.assertFalse(result.condition_5_position_sizing)

    def test_all_six_conditions_met_but_gate_not_ratified_still_recommends_none(self):
        triggers = [trig("PRICE_CONFIRMATION"), trig("FLOW_REVERSAL")]
        result = acg.evaluate("BTC", "2026-08-13", triggers, 100.0, 90.0)
        self.assertTrue(result.conditions_1_to_6_met)
        self.assertFalse(result.condition_7_gate_ratified)  # no ratified rule exists in this repo today
        self.assertEqual(result.recommended_action, "NONE")

    def test_gate_available_checks_real_repo_rules_json_not_assumed(self):
        # gate_available() must actually look at config/rules.json, not just return a literal.
        source = (ROOT / "replay" / "action_conversion_gate.py").read_text(encoding="utf-8")
        self.assertIn('rules.json', source)
        self.assertFalse(acg.gate_available())  # true today: no ratified Probe rule committed anywhere

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
