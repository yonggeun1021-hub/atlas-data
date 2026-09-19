#!/usr/bin/env python3
"""Offline regression for universe/us_liquidity_sip_source.py.

Pure function -- no network. File I/O is exercised only against temp
files (for the sha-mismatch/tamper paths) and, separately, against the
real committed policy + evidence files (to pin that the ratified
threshold is actually bound in this repo today, not left UNKNOWN).
Covers: the real repo now HAS a committed, evidence-sha-verified
threshold; a mismatched or missing cited evidence file fails closed to
UNKNOWN (never a stale/invented number); the exact status vocabulary
(NOT_EVALUATED for an insufficient session window vs UNKNOWN for a real
but inconclusive/missing input vs FAIL for a confirmed negative);
SIP/IEX feed-selection and volume/price/OTC-exclusion arithmetic; and
schema validation of a malformed policy or observation.
"""
from __future__ import annotations

import copy
import hashlib
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

BASE_RATIFICATION_PATH = ROOT / "evidence" / "authority" / "paper_liquidity_kr_us_user_ratification_20260914.json"
SIP_RATIFICATION_PATH = ROOT / "evidence" / "authority" / "us_liquidity_sip_source_user_ratification_20260915.json"


def make_policy(
    min_avg_traded_value_usd: str = "10000000",
    last_close_usd_min: str = "5",
    evidence_refs=None,
) -> dict:
    body = {
        "schema_version": M.POLICY_SCHEMA_VERSION,
        "policy_id": "TEST-US-LIQUIDITY-POLICY-V1",
        "approval_status": "RATIFIED",
        "ratified_by": "TEST_HARNESS",
        "ratified_at": "2026-09-15T00:00:00Z",
        "min_avg_traded_value_usd": min_avg_traded_value_usd,
        "last_close_usd_min": last_close_usd_min,
        "exclude_conditions": ["OTC_OR_NON_EXCHANGE_LISTED"],
        "insufficient_sessions_status": "NOT_EVALUATED",
        "missing_or_stale_data_status": "UNKNOWN",
        "sip_source_rule": {
            "rule_id": M.RULE_ID,
            "primary_source": "test primary source",
            "metric": "test metric",
            "fallback": "test fallback",
        },
        "evidence_refs": evidence_refs
        if evidence_refs is not None
        else [
            {
                "ratification_id": "TEST-BASE-RECORD",
                "description": "test base record",
                "path": "evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json",
                "sha256": hashlib.sha256(BASE_RATIFICATION_PATH.read_bytes()).hexdigest(),
            },
            {
                "ratification_id": "TEST-SIP-SOURCE-RECORD",
                "description": "test SIP-source record",
                "path": "evidence/authority/us_liquidity_sip_source_user_ratification_20260915.json",
                "sha256": hashlib.sha256(SIP_RATIFICATION_PATH.read_bytes()).hexdigest(),
            },
        ],
    }
    body["packet_sha256"] = M.payload_sha256(body)
    return body


def make_observation(feed: str, avg: str, last_close: str = "450.00", sessions: int = 20, window_end: str = "2026-09-14") -> dict:
    return {
        "feed": feed,
        "avg_traded_value_usd": avg,
        "last_close_usd": last_close,
        "session_count": sessions,
        "window_end": window_end,
        "notional_formula": "CLOSE_TIMES_VOLUME",
        "source_ref": f"alpaca_historical_daily_bars:{feed}",
    }


class RealCommittedThresholdTests(unittest.TestCase):
    """The 2026-09-15 correction: the threshold is now bound, not UNKNOWN."""

    def test_real_policy_file_exists_and_loads(self):
        self.assertTrue(M.POLICY_PATH.exists())
        policy = M.load_policy()
        self.assertIsNotNone(policy)
        self.assertEqual(policy["min_avg_traded_value_usd"], "10000000")
        self.assertEqual(policy["last_close_usd_min"], "5")

    def test_describe_policy_reports_ratified_for_the_real_committed_files(self):
        self.assertEqual(M.describe_policy(), {"status": "RATIFIED", "problems": []})

    def test_real_evidence_files_exist_at_the_exact_committed_paths(self):
        self.assertTrue(BASE_RATIFICATION_PATH.exists())
        self.assertTrue(SIP_RATIFICATION_PATH.exists())
        policy = M.load_policy()
        cited = {ref["path"]: ref["sha256"] for ref in policy["evidence_refs"]}
        self.assertEqual(
            cited["evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json"],
            hashlib.sha256(BASE_RATIFICATION_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            cited["evidence/authority/us_liquidity_sip_source_user_ratification_20260915.json"],
            hashlib.sha256(SIP_RATIFICATION_PATH.read_bytes()).hexdigest(),
        )

    def test_end_to_end_real_policy_binds_real_volume_and_price_status(self):
        # A synthetic observation well above both real ratified numbers,
        # evaluated against the REAL loaded policy (not a mock) --
        # pins that binding actually changes behavior, not just schema.
        policy = M.load_policy()
        sip = make_observation("sip", "100000000.00", "450.00")
        result = M.evaluate_symbol_liquidity("SPY", sip, None, policy)
        self.assertEqual(result["volume_status"], "PASS")
        self.assertEqual(result["price_status"], "PASS")
        self.assertEqual(result["threshold_usd"], "10000000")
        self.assertEqual(result["last_close_usd_min"], "5")
        # Honest, not assumed: no listing source is wired into the
        # collector today, so this specific sub-check -- and therefore the
        # overall status -- is UNKNOWN, not a false PASS.
        self.assertEqual(result["otc_exclusion_status"], "UNKNOWN")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("EXCHANGE_LISTING_STATUS_UNAVAILABLE", result["reasons"])

    def test_end_to_end_real_policy_with_listing_status_supplied_passes(self):
        policy = M.load_policy()
        sip = make_observation("sip", "100000000.00", "450.00")
        result = M.evaluate_symbol_liquidity("SPY", sip, None, policy, exchange_listing_status="EXCHANGE_LISTED")
        self.assertEqual(result["status"], "PASS")


class EvidenceMismatchFailsClosedTests(unittest.TestCase):
    """A cited evidence file missing or tampered -> UNKNOWN, never a stale number."""

    def test_missing_cited_evidence_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = make_policy(evidence_refs=[
                {"ratification_id": "X", "description": "d", "path": "evidence/authority/does_not_exist.json", "sha256": "0" * 64},
                {"ratification_id": "Y", "description": "d", "path": "evidence/authority/also_missing.json", "sha256": "1" * 64},
            ])
            problems = M.verify_evidence_refs(policy, root=root)
            self.assertEqual(len(problems), 2)
            self.assertTrue(all(p.startswith("EVIDENCE_FILE_MISSING:") for p in problems))

    def test_tampered_committed_copy_hash_mismatch_fails_closed_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence_dir = root / "evidence" / "authority"
            evidence_dir.mkdir(parents=True)
            real_path = evidence_dir / "paper_liquidity_kr_us_user_ratification_20260914.json"
            real_path.write_bytes(b'{"tampered": true}')
            actual_sha = hashlib.sha256(real_path.read_bytes()).hexdigest()
            stale_sha = "f" * 64  # what the policy THINKS the file's hash is
            policy = make_policy(evidence_refs=[
                {"ratification_id": "X", "description": "d", "path": "evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json", "sha256": stale_sha},
                {"ratification_id": "Y", "description": "d", "path": "evidence/authority/us_liquidity_sip_source_user_ratification_20260915.json", "sha256": "2" * 64},
            ])
            problems = M.verify_evidence_refs(policy, root=root)
            self.assertIn(
                "EVIDENCE_HASH_MISMATCH:evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json",
                problems,
            )
            self.assertNotEqual(actual_sha, stale_sha)

    def test_load_policy_returns_none_on_evidence_mismatch_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy_path = root / "policy.json"
            evidence_dir = root / "evidence" / "authority"
            evidence_dir.mkdir(parents=True)
            (evidence_dir / "paper_liquidity_kr_us_user_ratification_20260914.json").write_bytes(b"{}")
            (evidence_dir / "us_liquidity_sip_source_user_ratification_20260915.json").write_bytes(b"{}")
            policy = make_policy(evidence_refs=[
                {"ratification_id": "X", "description": "d", "path": "evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json", "sha256": "0" * 64},
                {"ratification_id": "Y", "description": "d", "path": "evidence/authority/us_liquidity_sip_source_user_ratification_20260915.json", "sha256": "1" * 64},
            ])
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            self.assertIsNone(M.load_policy(policy_path, root=root))
            self.assertEqual(M.describe_policy(policy_path, root=root)["status"], "EVIDENCE_HASH_MISMATCH")

    def test_load_policy_binds_when_evidence_matches_in_an_isolated_temp_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy_path = root / "policy.json"
            evidence_dir = root / "evidence" / "authority"
            evidence_dir.mkdir(parents=True)
            base_bytes = b'{"ratification": "base"}'
            sip_bytes = b'{"ratification": "sip"}'
            (evidence_dir / "paper_liquidity_kr_us_user_ratification_20260914.json").write_bytes(base_bytes)
            (evidence_dir / "us_liquidity_sip_source_user_ratification_20260915.json").write_bytes(sip_bytes)
            policy = make_policy(evidence_refs=[
                {"ratification_id": "X", "description": "d", "path": "evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json", "sha256": hashlib.sha256(base_bytes).hexdigest()},
                {"ratification_id": "Y", "description": "d", "path": "evidence/authority/us_liquidity_sip_source_user_ratification_20260915.json", "sha256": hashlib.sha256(sip_bytes).hexdigest()},
            ])
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            loaded = M.load_policy(policy_path, root=root)
            self.assertIsNotNone(loaded)
            self.assertEqual(M.describe_policy(policy_path, root=root), {"status": "RATIFIED", "problems": []})


class PolicySchemaValidationTests(unittest.TestCase):
    def test_missing_policy_file_returns_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist.json"
            self.assertIsNone(M.load_policy(missing))
            self.assertEqual(M.describe_policy(missing)["status"], "ABSENT_FROM_REPO")

    def test_malformed_but_present_policy_file_fails_closed_on_load_but_not_on_describe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({"not": "a policy"}), encoding="utf-8")
            with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "POLICY_FIELDS_INVALID"):
                M.load_policy(path)
            self.assertEqual(M.describe_policy(path)["status"], "MALFORMED")

    def test_tampered_threshold_with_stale_self_digest_fails_closed(self):
        policy = make_policy()
        policy["min_avg_traded_value_usd"] = "1"  # packet_sha256 now stale
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "POLICY_SHA_MISMATCH"):
            M._validate_policy(policy)

    def test_wrong_insufficient_sessions_vocabulary_fails_closed(self):
        policy = make_policy()
        policy.pop("packet_sha256")
        policy["insufficient_sessions_status"] = "UNKNOWN"  # must be NOT_EVALUATED
        policy["packet_sha256"] = M.payload_sha256(policy)
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "POLICY_INSUFFICIENT_SESSIONS_STATUS_INVALID"):
            M._validate_policy(policy)

    def test_wrong_exclude_conditions_fails_closed(self):
        policy = make_policy()
        policy.pop("packet_sha256")
        policy["exclude_conditions"] = ["SOMETHING_ELSE"]
        policy["packet_sha256"] = M.payload_sha256(policy)
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "POLICY_EXCLUDE_CONDITIONS_INVALID"):
            M._validate_policy(policy)

    def test_wrong_evidence_ref_count_fails_closed(self):
        policy = make_policy()
        policy.pop("packet_sha256")
        policy["evidence_refs"] = policy["evidence_refs"][:1]
        policy["packet_sha256"] = M.payload_sha256(policy)
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "POLICY_EVIDENCE_REFS_COUNT_INVALID"):
            M._validate_policy(policy)


class StatusVocabularyTests(unittest.TestCase):
    """NOT_EVALUATED (insufficient window) vs UNKNOWN (inconclusive/missing
    input) vs FAIL (confirmed negative) vs PASS -- the record's own words."""

    def test_fewer_than_20_sessions_on_every_feed_is_not_evaluated_not_unknown(self):
        policy = make_policy()
        sip = make_observation("sip", "50000000.00", sessions=5)
        iex = make_observation("iex", "50000000.00", sessions=4)
        result = M.evaluate_symbol_liquidity("SPY", sip, iex, policy)
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertIsNone(result["volume_status"])
        self.assertIsNone(result["price_status"])
        self.assertIsNone(result["otc_exclusion_status"])

    def test_no_feed_data_at_all_is_not_evaluated(self):
        policy = make_policy()
        result = M.evaluate_symbol_liquidity("SPY", None, None, policy)
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertEqual(result["reasons"], ["NO_FEED_DATA_AVAILABLE"])

    def test_no_policy_reports_real_observation_but_every_subcheck_is_unknown(self):
        sip = make_observation("sip", "50000000.00", "450.00")
        result = M.evaluate_symbol_liquidity("SPY", sip, None, None)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["volume_status"], "UNKNOWN")
        self.assertEqual(result["price_status"], "UNKNOWN")
        self.assertEqual(result["otc_exclusion_status"], "UNKNOWN")
        self.assertEqual(result["source_feed_used"], "sip")
        self.assertEqual(result["avg_traded_value_usd"], "50000000.00")  # still reported

    def test_sip_confirmed_below_volume_threshold_is_fail(self):
        policy = make_policy(min_avg_traded_value_usd="10000000")
        sip = make_observation("sip", "9999999.99", "450.00")
        result = M.evaluate_symbol_liquidity("SPY", sip, None, policy, exchange_listing_status="EXCHANGE_LISTED")
        self.assertEqual(result["volume_status"], "FAIL")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("SIP_BELOW_THRESHOLD", result["reasons"])

    def test_iex_fallback_below_threshold_is_unknown_never_fail(self):
        policy = make_policy(min_avg_traded_value_usd="10000000")
        iex = make_observation("iex", "1.00", "450.00")
        result = M.evaluate_symbol_liquidity("SPY", None, iex, policy, exchange_listing_status="EXCHANGE_LISTED")
        self.assertEqual(result["volume_status"], "UNKNOWN")
        self.assertNotEqual(result["volume_status"], "FAIL")
        self.assertEqual(result["status"], "UNKNOWN")

    def test_close_below_price_floor_is_fail(self):
        policy = make_policy(last_close_usd_min="5")
        sip = make_observation("sip", "50000000.00", last_close="2.00")
        result = M.evaluate_symbol_liquidity("PENNY", sip, None, policy, exchange_listing_status="EXCHANGE_LISTED")
        self.assertEqual(result["price_status"], "FAIL")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("LAST_CLOSE_BELOW_PRICE_FLOOR", result["reasons"])

    def test_close_at_exactly_the_price_floor_passes(self):
        policy = make_policy(last_close_usd_min="5")
        sip = make_observation("sip", "50000000.00", last_close="5.00")
        result = M.evaluate_symbol_liquidity("EDGE", sip, None, policy, exchange_listing_status="EXCHANGE_LISTED")
        self.assertEqual(result["price_status"], "PASS")

    def test_otc_confirmed_is_fail(self):
        policy = make_policy()
        sip = make_observation("sip", "50000000.00", "450.00")
        result = M.evaluate_symbol_liquidity("OTCX", sip, None, policy, exchange_listing_status="OTC")
        self.assertEqual(result["otc_exclusion_status"], "FAIL")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("OTC_EXCLUDED", result["reasons"])

    def test_confirmed_test_issue_is_fail_with_its_own_reason(self):
        policy = make_policy()
        sip = make_observation("sip", "50000000.00", "450.00")
        result = M.evaluate_symbol_liquidity("TESTX", sip, None, policy, exchange_listing_status="TEST_ISSUE")
        self.assertEqual(result["otc_exclusion_status"], "FAIL")
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("TEST_ISSUE_EXCLUDED", result["reasons"])
        self.assertNotIn("OTC_EXCLUDED", result["reasons"])

    def test_invalid_exchange_listing_status_token_fails_closed(self):
        policy = make_policy()
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "EXCHANGE_LISTING_STATUS_INVALID"):
            M.evaluate_symbol_liquidity("SPY", None, None, policy, exchange_listing_status="MAYBE")

    def test_full_pass_requires_all_three_subchecks(self):
        policy = make_policy()
        sip = make_observation("sip", "10000000", "5")
        result = M.evaluate_symbol_liquidity("SPY", sip, None, policy, exchange_listing_status="EXCHANGE_LISTED")
        self.assertEqual(result["volume_status"], "PASS")
        self.assertEqual(result["price_status"], "PASS")
        self.assertEqual(result["otc_exclusion_status"], "PASS")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["reasons"], [])

    def test_sip_preferred_over_iex_even_when_both_have_full_windows(self):
        policy = make_policy()
        sip = make_observation("sip", "10000001.00", "450.00")
        iex = make_observation("iex", "999999999.00", "450.00")
        result = M.evaluate_symbol_liquidity("SPY", sip, iex, policy)
        self.assertEqual(result["source_feed_used"], "sip")
        self.assertEqual(result["avg_traded_value_usd"], "10000001.00")

    def test_result_never_mutates_caller_inputs(self):
        policy = make_policy()
        sip = make_observation("sip", "5000000.00", "450.00")
        sip_copy = copy.deepcopy(sip)
        M.evaluate_symbol_liquidity("SPY", sip, None, policy)
        self.assertEqual(sip, sip_copy)


class ObservationValidationTests(unittest.TestCase):
    def test_wrong_feed_label_fails_closed(self):
        bad = make_observation("iex", "1.00")  # labeled iex, passed as sip
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_OBSERVATION_FEED_MISMATCH"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_missing_field_fails_closed(self):
        bad = make_observation("sip", "1.00")
        del bad["window_end"]
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_OBSERVATION_FIELDS_INVALID"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_missing_last_close_field_fails_closed(self):
        bad = make_observation("sip", "1.00")
        del bad["last_close_usd"]
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_OBSERVATION_FIELDS_INVALID"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_negative_session_count_fails_closed(self):
        bad = make_observation("sip", "1.00")
        bad["session_count"] = -1
        with self.assertRaisesRegex(M.UsLiquiditySipSourceError, "SIP_SESSION_COUNT_INVALID"):
            M.evaluate_symbol_liquidity("SPY", bad, None, make_policy())

    def test_non_canonical_decimal_string_fails_closed(self):
        bad = make_observation("sip", "1e7")  # exponent form rejected
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

    def test_allowed_statuses_include_not_evaluated(self):
        self.assertEqual(set(M.ALLOWED_STATUSES), {"PASS", "FAIL", "UNKNOWN", "NOT_EVALUATED"})


if __name__ == "__main__":
    unittest.main()
