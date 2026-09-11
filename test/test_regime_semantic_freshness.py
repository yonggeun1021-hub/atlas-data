#!/usr/bin/env python3
"""P1-COM-05 G4 ratified source-frequency semantic freshness regressions."""

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "regime_semantic_freshness.py"
SPEC = importlib.util.spec_from_file_location("regime_semantic_freshness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

POLICY_PATH = ROOT / "config" / "regime_semantic_freshness_policy_v1.json"
KOREA_LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"


def defined(observation_date="2026-09-10"):
    return {
        "status": "DEFINED",
        "observation_date": observation_date,
        "available_at": "2026-09-11T00:20:00Z",
        "transform_version": "regime_x/v1",
    }


def undefined():
    return {
        "status": "UNDEFINED",
        "observation_date": None,
        "available_at": None,
        "transform_version": None,
    }


class PolicyContractTest(unittest.TestCase):
    def test_policy_is_ratified_and_pinned(self):
        policy = MODULE.load_policy()
        self.assertEqual(policy["policy_status"], "RATIFIED")
        self.assertEqual(
            policy["contract_version"], "regime_semantic_freshness_policy/v1"
        )
        self.assertIsNone(
            policy["markets"]["US"]["release_based_axes"]["RISK_VOL"][
                "numeric_ttl_seconds"
            ]
        )
        self.assertIsNone(
            policy["markets"]["US"]["session_based_axes"]["TREND"][
                "numeric_ttl_seconds"
            ]
        )
        for market_block in policy["markets"].values():
            for axis_block in market_block.get("session_based_axes", {}).values():
                self.assertIsNone(axis_block["numeric_ttl_seconds"])
            for axis_block in market_block.get("release_based_axes", {}).values():
                self.assertIsNone(axis_block["numeric_ttl_seconds"])

    def test_kr_usability_gate_reuses_live_korea_leadership_policy(self):
        policy = MODULE.load_policy()
        gate = policy["markets"]["KR"]["same_session_usability_gate"]
        live = json.loads(KOREA_LEADERSHIP_POLICY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(gate["earliest_usable_time_value"], live["earliest_usable_time"])
        self.assertEqual(gate["earliest_usable_time_value"], "18:00:00")

    def test_us_session_based_axes_are_exactly_trend_breadth_leadership(self):
        policy = MODULE.load_policy()
        self.assertEqual(
            set(policy["markets"]["US"]["session_based_axes"]),
            {"TREND", "BREADTH", "LEADERSHIP"},
        )
        self.assertEqual(
            set(policy["markets"]["US"]["release_based_axes"]),
            {"RISK_VOL", "LIQUIDITY"},
        )

    def test_kr_all_five_axes_are_session_based(self):
        policy = MODULE.load_policy()
        self.assertEqual(
            set(policy["markets"]["KR"]["session_based_axes"]),
            set(MODULE.REQUIRED_AXES),
        )
        self.assertEqual(policy["markets"]["KR"]["release_based_axes"], {})


class SessionExactMatchTest(unittest.TestCase):
    def test_exact_session_match_is_fresh(self):
        result = MODULE.evaluate_axis_freshness(
            "US", "TREND", defined("2026-09-10"),
            expected_completed_session_date="2026-09-10",
        )
        self.assertEqual(result["freshness_status"], MODULE.FRESH)
        self.assertIsNone(result["reason"])

    def test_older_session_source_is_rejected_as_not_advanced(self):
        """A prior-session observation, while a newer completed session
        exists, must be rejected as UNKNOWN -- never silently carried
        forward or substituted."""
        result = MODULE.evaluate_axis_freshness(
            "US", "TREND", defined("2026-09-09"),
            expected_completed_session_date="2026-09-10",
        )
        self.assertEqual(result["freshness_status"], MODULE.UNKNOWN)
        self.assertEqual(result["reason"], "SOURCE_NOT_ADVANCED_EXPECTED_SESSION")

    def test_calendar_unknown_is_immediate_unknown(self):
        result = MODULE.evaluate_axis_freshness("US", "BREADTH", defined())
        self.assertEqual(result["freshness_status"], MODULE.UNKNOWN)
        self.assertEqual(
            result["reason"], "EXPECTED_COMPLETED_SESSION_CALENDAR_UNKNOWN"
        )

    def test_undefined_axis_is_immediate_unknown(self):
        result = MODULE.evaluate_axis_freshness(
            "KR", "LEADERSHIP", undefined(),
            expected_completed_session_date="2026-09-10",
        )
        self.assertEqual(result["freshness_status"], MODULE.UNKNOWN)
        self.assertEqual(result["reason"], "AXIS_EVIDENCE_UNDEFINED")

    def test_kr_before_earliest_usable_time_is_unknown(self):
        # 08:00 UTC == 17:00 KST, before the ratified 18:00 KST gate.
        result = MODULE.evaluate_axis_freshness(
            "KR", "BREADTH", defined("2026-09-10"),
            expected_completed_session_date="2026-09-10",
            decision_at="2026-09-10T08:00:00Z",
        )
        self.assertEqual(result["freshness_status"], MODULE.UNKNOWN)
        self.assertEqual(result["reason"], "KR_SESSION_BEFORE_EARLIEST_USABLE_TIME")

    def test_kr_after_earliest_usable_time_is_fresh(self):
        # 09:30 UTC == 18:30 KST, after the ratified 18:00 KST gate.
        result = MODULE.evaluate_axis_freshness(
            "KR", "BREADTH", defined("2026-09-10"),
            expected_completed_session_date="2026-09-10",
            decision_at="2026-09-10T09:30:00Z",
        )
        self.assertEqual(result["freshness_status"], MODULE.FRESH)


class ReleaseCycleLatestFetchTest(unittest.TestCase):
    def test_defined_release_axis_is_fresh_with_no_session_date_required(self):
        """VIXCLS (daily) needs no expected_completed_session_date at all --
        release-based freshness never depends on the ETF session calendar."""
        result = MODULE.evaluate_axis_freshness("US", "RISK_VOL", defined())
        self.assertEqual(result["freshness_status"], MODULE.FRESH)
        self.assertIsNone(result["reason"])

    def test_weekly_wresbal_unchanged_value_is_still_permitted_fresh(self):
        """An unchanged weekly FRED value (WRESBAL/TOTBKCR) is a normal, fresh
        outcome for that publication frequency -- this module never compares
        values across dates, so 'unchanged' can never itself trigger
        staleness."""
        same_value_twice = defined("2026-09-03")  # same weekly-ending date
        result_a = MODULE.evaluate_axis_freshness("US", "LIQUIDITY", same_value_twice)
        result_b = MODULE.evaluate_axis_freshness("US", "LIQUIDITY", same_value_twice)
        self.assertEqual(result_a, result_b)
        self.assertEqual(result_a["freshness_status"], MODULE.FRESH)

    def test_release_axis_ignores_a_stale_looking_session_date(self):
        """Even an observation_date far from any 'current' ETF session must
        not be coerced onto the session calendar or rejected on that basis --
        release-based freshness is evaluated purely on evidence presence."""
        result = MODULE.evaluate_axis_freshness("US", "RISK_VOL", defined("2026-08-01"))
        self.assertEqual(result["freshness_status"], MODULE.FRESH)


class MarketFreshnessTest(unittest.TestCase):
    def test_market_freshness_all_fresh_true_only_when_every_axis_fresh(self):
        factors = {
            "TREND": defined("2026-09-10"),
            "BREADTH": defined("2026-09-10"),
            "RISK_VOL": defined("2026-09-10"),
            "LIQUIDITY": defined("2026-09-03"),
            "LEADERSHIP": defined("2026-09-10"),
        }
        result = MODULE.evaluate_market_freshness(
            "US", factors, expected_completed_session_date="2026-09-10"
        )
        self.assertTrue(result["all_fresh"])

        factors["TREND"] = defined("2026-09-09")  # stale
        result = MODULE.evaluate_market_freshness(
            "US", factors, expected_completed_session_date="2026-09-10"
        )
        self.assertFalse(result["all_fresh"])
        self.assertEqual(
            result["axes"]["TREND"]["reason"], "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
        )


if __name__ == "__main__":
    unittest.main()
