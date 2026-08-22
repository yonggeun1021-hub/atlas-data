#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- proposed Action Conversion Gate regression.

CIO review round 2 (flaws 2/3) and round 3 (flaw 2 rework): conditions use
a real PASS/FAIL/NOT_EVALUATED/NOT_COMPUTABLE vocabulary, are actually
computed from real inputs, and (round 3) condition 1 distinguishes tactical
price-structure hypotheses from fundamental-catalyst ones, condition 5 is
honestly NOT_EVALUATED (no portfolio-sizing data exists anywhere), and
condition 6 is three independently-evaluated real sub-checks.
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


class Condition1HypothesisClassTests(unittest.TestCase):
    """CIO round-3 flaw 2: condition 1 must not conflate a price-only
    tactical trigger with a real fundamental thesis/catalyst."""

    def test_zero_triggers_fails(self):
        result = acg.evaluate("BTC", "2026-08-13", [], None, None)
        self.assertEqual(result.condition_1_hypothesis_or_catalyst, "FAIL")
        self.assertEqual(result.recommended_action, "NONE")

    def test_tactical_price_trigger_yields_pass_tactical_not_plain_pass(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.condition_1_hypothesis_or_catalyst, "PASS_TACTICAL")

    def test_trigger_with_sentinel_missing_evidence_source_fails(self):
        t = trig("PRICE_CONFIRMATION", source="NO_BTC_SNAPSHOT_AVAILABLE_AT_OR_BEFORE_DECISION_DATE")
        result = acg.evaluate("BTC", "2026-08-13", [t], 100.0, 90.0)
        self.assertEqual(result.condition_1_hypothesis_or_catalyst, "FAIL")

    def test_all_tactical_types_are_classified_tactical_not_fundamental(self):
        for ttype in acg.TACTICAL_TRIGGER_TYPES:
            with self.subTest(ttype=ttype):
                result = acg.evaluate("BTC", "2026-08-13", [trig(ttype)], 100.0, 90.0)
                self.assertEqual(result.condition_1_hypothesis_or_catalyst, "PASS_TACTICAL")

    def test_fundamental_trigger_types_are_a_disjoint_set_from_tactical(self):
        self.assertEqual(acg.TACTICAL_TRIGGER_TYPES & acg.FUNDAMENTAL_TRIGGER_TYPES, frozenset())


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


class Condition5NoLongerMasqueradesAsSizingTests(unittest.TestCase):
    """CIO round-3 flaw 2: stop distance != position sizing. Real sizing
    requires Portfolio NAV / per-trade loss allowance / a ratified Probe
    loss budget / a target weight / portfolio headroom -- none of which
    exist anywhere in this repo, so condition_5_position_sizing must always
    be NOT_EVALUATED, and the renamed stop_distance_pct must never be
    reported as if it were a sizing decision."""

    def test_condition_5_position_sizing_is_always_not_evaluated(self):
        cases = [
            acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0),
            acg.evaluate("BTC", "2026-08-13", [], None, None),
            acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 10.0),
        ]
        for result in cases:
            self.assertEqual(result.condition_5_position_sizing, "NOT_EVALUATED")

    def test_stop_distance_pct_field_name_replaces_max_loss_pct(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertTrue(hasattr(result, "stop_distance_pct"))
        self.assertFalse(hasattr(result, "max_loss_pct"))
        self.assertAlmostEqual(result.stop_distance_pct, 10.0, places=6)

    def test_no_position_sizing_computation_derived_from_stop_distance_alone(self):
        source = (ROOT / "replay" / "action_conversion_gate.py").read_text(encoding="utf-8")
        self.assertNotIn('cond5 = cond3 and cond4', source)

    def test_out_of_sane_bounds_stop_distance_is_flagged_but_still_not_evaluated_for_sizing(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 10.0)  # 90% stop
        self.assertGreater(result.stop_distance_pct, acg.MAX_SANE_STOP_DISTANCE_PCT)
        self.assertEqual(result.condition_5_position_sizing, "NOT_EVALUATED")


class Condition6ThreeSubChecksTests(unittest.TestCase):
    """CIO round-3 flaw 2: condition 6 must be split into
    price_integrity / asset_identity_status / PIT_availability."""

    def test_all_three_sub_fields_exist(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertTrue(hasattr(result, "condition_6a_price_integrity"))
        self.assertTrue(hasattr(result, "condition_6b_asset_identity_status"))
        self.assertTrue(hasattr(result, "condition_6c_pit_availability"))

    def test_btc_identity_is_always_resolved_dedicated_collector(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)
        self.assertEqual(result.condition_6b_asset_identity_status, "PASS")

    def test_pit_availability_fails_without_a_real_evaluation_date(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0)  # no evaluation_date
        self.assertEqual(result.condition_6c_pit_availability, "FAIL")

    def test_pit_availability_passes_with_a_real_evaluation_date(self):
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0,
                               evaluation_date="2026-08-13")
        self.assertEqual(result.condition_6c_pit_availability, "PASS")

    def test_clean_series_passes_price_integrity_and_aggregate(self):
        s = PriceSeries("BTC")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        result = acg.evaluate("BTC", "2026-08-13", [trig("PRICE_CONFIRMATION")], 100.0, 90.0,
                               series=s, evaluation_date="2026-08-13", lookback_dates=["2026-08-13"])
        self.assertEqual(result.condition_6a_price_integrity, "PASS")
        self.assertEqual(result.condition_6_pit_data_integrity, "PASS")

    def test_real_integrity_conflict_fails_price_integrity_and_aggregate(self):
        s = PriceSeries("X")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        s._merge_row("2026-08-13", {"close": 105.0, "open": 100, "high": 101, "low": 99}, "2026-08-14")
        self.assertEqual(len(s.integrity_conflicts), 1)
        result = acg.evaluate("X", "2026-08-13", [trig("PRICE_CONFIRMATION", subject="X")], 100.0, 90.0,
                               series=s, evaluation_date="2026-08-13", lookback_dates=["2026-08-13"])
        self.assertEqual(result.condition_6a_price_integrity, "FAIL")
        self.assertEqual(result.condition_6_pit_data_integrity, "FAIL")

    def test_kr_identity_checked_against_real_declared_universe(self):
        result_known = acg.evaluate("005930", "2026-08-13", [trig("PRICE_CONFIRMATION", subject="005930")],
                                     100.0, 90.0, evaluation_date="2026-08-13",
                                     kr_universe_codes={"005930", "000660"})
        result_unknown = acg.evaluate("999999", "2026-08-13", [trig("PRICE_CONFIRMATION", subject="999999")],
                                       100.0, 90.0, evaluation_date="2026-08-13",
                                       kr_universe_codes={"005930", "000660"})
        self.assertEqual(result_known.condition_6b_asset_identity_status, "PASS")
        self.assertEqual(result_unknown.condition_6b_asset_identity_status, "FAIL")

    def test_no_hardcoded_true_shortcut_exists_in_source(self):
        source = (ROOT / "replay" / "action_conversion_gate.py").read_text(encoding="utf-8")
        self.assertNotIn('cond6 = True', source)


class Condition7RealPolicyFileCheckTests(unittest.TestCase):
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
        self.assertEqual(doc.get("approval_status"), "RATIFIED")

    def test_no_probe_policy_exists_today_so_condition_7_fails_for_a_real_grounded_reason(self):
        status, detail = acg._gate_ratified_status()
        self.assertEqual(status, "FAIL")
        self.assertIn("config/", detail)
        self.assertFalse(acg.gate_available())


class GateBlockEligibilityStructurallyUnreachableTodayTests(unittest.TestCase):
    """CIO round-3 flaw 2: since condition 5 is always NOT_EVALUATED,
    conditions_1_to_6_all_pass must always be False today -- an honest,
    not a papered-over, structural result."""

    def test_conditions_1_to_6_all_pass_is_always_false_today(self):
        s = PriceSeries("BTC")
        s._merge_row("2026-08-13", {"close": 100.0, "open": 100, "high": 101, "low": 99}, "2026-08-13")
        triggers = [trig("PRICE_CONFIRMATION"), trig("FLOW_REVERSAL")]
        result = acg.evaluate("BTC", "2026-08-13", triggers, 100.0, 90.0,
                               series=s, evaluation_date="2026-08-13", lookback_dates=["2026-08-13"])
        self.assertFalse(result.conditions_1_to_6_all_pass)
        self.assertEqual(result.recommended_action, "NONE")


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
