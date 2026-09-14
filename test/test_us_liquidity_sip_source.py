#!/usr/bin/env python3
"""Offline regression for universe/us_liquidity_sip_source.py.

Pure function -- no network, no file I/O beyond ``load_policy`` (exercised
against a temp file and against the real, currently-absent, repo path).
Covers: the real repo has no committed threshold (so production defaults
to UNKNOWN, never an invented number), the exact feed-selection/fallback
arithmetic of RULE.LIQUIDITY.US_SIP_SOURCE.V1 against a mocked ratified
policy, the insufficient-session-window path, and schema validation of a
malformed policy or observation.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "us_liquidity_sip_source", ROOT / "universe" / "us_liquidity_sip_source.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def make_policy(min_avg_traded_value_usd: str = "10000000.00") -> dict:
    body = {
        "schema_version": M.POLICY_SCHEMA_VERSION,
        "policy_id": "TEST-US-LIQUIDITY-POLICY-V1",
        "approval_status": "RATIFIED",
        "ratified_by": "TEST_HARNESS",
        "ratified_at": "2026-09-15T00:00:00Z",
        "min_avg_traded_value_usd": min_avg_traded_value_usd,
    }
    body["packet_sha256"] = M.payload_sha256(body)
    return body


def make_observation(feed: str, avg: str, sessions: int, window_end: str = "2026-09-14") -> dict:
    return {
        "feed": feed,
        "avg_traded_value_usd": avg,
        "session_count": sessions,
        "window_end": window_end,
        "notional_formula": "CLOSE_TIMES_VOLUME",
        "source_ref": f"alpaca_historical_daily_bars:{feed}",
    }


class RepoHasNoCommittedThresholdTests(unittest.TestCase):
    def test_real_repo_policy_path_is_absent_today(self):
        # This is the CIO's 2026-09-15 finding, pinned as a regression: if a
        # future PR commits config/us_liquidity_sip_source_policy.json, this
        # test starts failing and must be updated deliberately, not silently.
        self.assertFalse(M.POLICY_PATH.exists(), (
            "config/us_liquidity_sip_source_policy.json now exists -- update "
            "this test deliberately if a ratified packet was just committed"
        ))
        self.assertIsNone(M.load_policy())

    def test_missing_policy_file_returns_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist.json"
            self.assertIsNone(M.load_policy(missing))

    def test_malformed_but_present_policy_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({"not": "a policy"}), encoding="utf-8")
            with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "POLICY_FIELDS_INVALID"):
                M.load_policy(path)

    def test_valid_policy_file_round_trips(self):
        policy = make_policy()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            loaded = M.load_policy(path)
            self.assertEqual(loaded, policy)

    def test_tampered_threshold_with_stale_digest_fails_closed(self):
        policy = make_policy()
        policy["min_avg_traded_value_usd"] = "1.00"  # digest now stale
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "POLICY_SHA_MISMATCH"):
            M._validate_policy(policy)


class FeedSelectionAndThresholdArithmeticTests(unittest.TestCase):
    def test_no_policy_reports_real_sip_average_but_status_is_unknown(self):
        sip = make_observation("sip", "50000000.00", 20)
        result = M.evaluate_symbol_liquidity("SPY", sip, None, None)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["source_feed_used"], "sip")
        self.assertEqual(result["avg_traded_value_usd"], "50000000.00")
        self.assertEqual(result["session_count"], 20)
        self.assertIn("LIQUIDITY_THRESHOLD_POLICY_ABSENT_FROM_REPO", result["reasons"])

    def test_sip_at_or_above_threshold_passes(self):
        policy = make_policy("10000000.00")
        sip = make_observation("sip", "10000000.00", 20)
        result = M.evaluate_symbol_liquidity("SPY", sip, None, policy)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source_feed_used"], "sip")
        self.assertEqual(result["reasons"], [])

    def test_sip_below_threshold_fails_a_confident_negative(self):
        policy = make_policy("10000000.00")
        sip = make_observation("sip", "9999999.99", 20)
        result = M.evaluate_symbol_liquidity("SPY", sip, None, policy)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reasons"], ["SIP_BELOW_THRESHOLD"])

    def test_sip_insufficient_window_falls_back_to_full_iex_window(self):
        policy = make_policy("10000000.00")
        sip = make_observation("sip", "50000000.00", 5)  # < 20 sessions
        iex = make_observation("iex", "20000000.00", 20)
        result = M.evaluate_symbol_liquidity("SPY", sip, iex, policy)
        self.assertEqual(result["source_feed_used"], "iex")
        self.assertEqual(result["status"], "PASS")
        self.assertIn("SIP_WINDOW_INSUFFICIENT_SESSIONS", result["reasons"])

    def test_sip_absent_iex_below_threshold_is_unknown_never_fail(self):
        policy = make_policy("10000000.00")
        iex = make_observation("iex", "1000000.00", 20)
        result = M.evaluate_symbol_liquidity("SPY", None, iex, policy)
        self.assertEqual(result["source_feed_used"], "iex")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("IEX_FALLBACK_BELOW_THRESHOLD", result["reasons"])
        self.assertNotEqual(result["status"], "FAIL")

    def test_both_feeds_insufficient_window_is_unknown(self):
        policy = make_policy("10000000.00")
        sip = make_observation("sip", "50000000.00", 3)
        iex = make_observation("iex", "50000000.00", 4)
        result = M.evaluate_symbol_liquidity("SPY", sip, iex, policy)
        self.assertIsNone(result["source_feed_used"])
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("SIP_WINDOW_INSUFFICIENT_SESSIONS", result["reasons"])
        self.assertIn("IEX_WINDOW_INSUFFICIENT_SESSIONS", result["reasons"])

    def test_no_feed_data_at_all_is_unknown(self):
        policy = make_policy("10000000.00")
        result = M.evaluate_symbol_liquidity("SPY", None, None, policy)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reasons"], ["NO_FEED_DATA_AVAILABLE"])

    def test_sip_preferred_over_iex_even_when_both_have_full_windows(self):
        policy = make_policy("10000000.00")
        sip = make_observation("sip", "10000001.00", 20)
        iex = make_observation("iex", "999999999.00", 20)
        result = M.evaluate_symbol_liquidity("SPY", sip, iex, policy)
        self.assertEqual(result["source_feed_used"], "sip")
        self.assertEqual(result["avg_traded_value_usd"], "10000001.00")

    def test_result_never_mutates_caller_inputs(self):
        policy = make_policy("10000000.00")
        sip = make_observation("sip", "5000000.00", 20)
        sip_copy = copy.deepcopy(sip)
        M.evaluate_symbol_liquidity("SPY", sip, None, policy)
        self.assertEqual(sip, sip_copy)


class ObservationValidationTests(unittest.TestCase):
    def test_wrong_feed_label_fails_closed(self):
        bad = make_observation("iex", "1.00", 20)  # labeled iex, passed as sip
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_OBSERVATION_FEED_MISMATCH"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_missing_field_fails_closed(self):
        bad = make_observation("sip", "1.00", 20)
        del bad["window_end"]
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_OBSERVATION_FIELDS_INVALID"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_negative_session_count_fails_closed(self):
        bad = make_observation("sip", "1.00", 20)
        bad["session_count"] = -1
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_SESSION_COUNT_INVALID"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_non_canonical_decimal_string_fails_closed(self):
        bad = make_observation("sip", "1e7", 20)  # exponent form rejected
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_AVG_TRADED_VALUE_INVALID"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_invalid_symbol_token_fails_closed(self):
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SYMBOL_INVALID"):
            M.evaluate_symbol_liquidity("not-a-valid-symbol!", None, None, None)


class RuleConstantsTests(unittest.TestCase):
    def test_required_session_window_is_twenty(self):
        self.assertEqual(M.REQUIRED_SESSION_WINDOW, 20)

    def test_rule_id_matches_ratification(self):
        self.assertEqual(M.RULE_ID, "RULE.LIQUIDITY.US_SIP_SOURCE.V1")


if __name__ == "__main__":
    unittest.main()
