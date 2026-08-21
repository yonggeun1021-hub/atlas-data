#!/usr/bin/env python3
"""P9-01 intraday price/volume freshness regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "execution" / "intraday_freshness.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("intraday_freshness", SOURCE)
CONTRACT = MODULE.load_contract()


def quote(
    asset_id="US.XNAS.MSFT",
    market="US",
    provider_timestamp="2026-08-21T01:09:30Z",
    received_at="2026-08-21T01:09:35Z",
):
    return {
        "asset_id": asset_id,
        "market": market,
        "price": "500.25",
        "volume": "1000",
        "quote_currency": "USD",
        "provider_id": "TEST.FEED",
        "provider_timestamp": provider_timestamp,
        "received_at": received_at,
        "source_ref": f"test://quote/{asset_id}",
        "source_sha256": "a" * 64,
    }


def batch(rows=None, observed_at="2026-08-21T01:10:00Z"):
    value = {
        "schema_version": "intraday_quote_batch/1",
        "contract_version": "intraday_freshness_guard/1",
        "batch_id": "INTRADAY.TEST.20260821",
        "observed_at": observed_at,
        "quotes": [quote()] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def policy(**changes):
    value = {
        "schema_version": "intraday_freshness_policy/1",
        "policy_id": "INTRADAY.FRESHNESS.TEST.V1",
        "approval_status": "RATIFIED",
        "ratified_by": "CIO test fixture",
        "ratified_at_utc": "2026-08-20T00:00:00Z",
        "effective_from_utc": "2026-08-21T00:00:00Z",
        "effective_to_utc": "2026-08-22T00:00:00Z",
        "input_contract_version": "intraday_freshness_guard/1",
        "max_provider_age_seconds_by_market": {
            "US": 60, "KOREA": 45, "CRYPTO": 30,
        },
        "max_transport_delay_seconds_by_market": {
            "US": 10, "KOREA": 8, "CRYPTO": 5,
        },
    }
    value.update(changes)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class IntradayFreshnessTests(unittest.TestCase):
    def test_contract_has_no_default_threshold_and_no_execution_authority(self):
        self.assertEqual(CONTRACT["repository_default_policy"], "ABSENT")
        self.assertEqual(CONTRACT["policy_requirement"], "EXTERNAL_RATIFIED_POLICY_REQUIRED")
        self.assertTrue(CONTRACT["authority"]["freshness_guard_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "freshness_guard_only":
                self.assertFalse(value, key)

    def test_fresh_quote_preserves_price_volume_time_and_source(self):
        result = MODULE.evaluate_freshness(batch(), policy(), CONTRACT)
        row = result["results"][0]
        self.assertEqual(row["price"], "500.25")
        self.assertEqual(row["volume"], "1000")
        self.assertEqual(row["provider_age_seconds"], 30)
        self.assertEqual(row["transport_delay_seconds"], 5)
        self.assertEqual(row["freshness_status"], "FRESH")
        self.assertTrue(row["fresh_for_intraday_consumption"])
        self.assertIsNone(row["entry_eligibility"])
        self.assertIsNone(row["action"])
        self.assertIsNone(row["order"])

    def test_equal_threshold_is_fresh_but_age_or_transport_excess_is_stale(self):
        at_boundary = quote(
            provider_timestamp="2026-08-21T01:09:00Z",
            received_at="2026-08-21T01:09:10Z",
        )
        old = quote(
            asset_id="US.XNAS.NVDA",
            provider_timestamp="2026-08-21T01:08:59Z",
            received_at="2026-08-21T01:09:00Z",
        )
        slow = quote(
            asset_id="US.XNAS.TSM",
            provider_timestamp="2026-08-21T01:09:40Z",
            received_at="2026-08-21T01:09:51Z",
        )
        result = MODULE.evaluate_freshness(batch([slow, old, at_boundary]), policy(), CONTRACT)
        by_id = {row["asset_id"]: row for row in result["results"]}
        self.assertEqual(by_id["US.XNAS.MSFT"]["freshness_status"], "FRESH")
        self.assertEqual(
            by_id["US.XNAS.NVDA"]["stale_reasons"], ["PROVIDER_AGE_EXCEEDED"]
        )
        self.assertEqual(
            by_id["US.XNAS.TSM"]["stale_reasons"], ["TRANSPORT_DELAY_EXCEEDED"]
        )

    def test_market_specific_policy_is_applied_without_cross_market_threshold(self):
        rows = [
            quote("CRYPTO.KRAKEN.BTC", "CRYPTO", "2026-08-21T01:09:30Z", "2026-08-21T01:09:35Z"),
            quote("KR.XKRX.005930", "KOREA", "2026-08-21T01:09:15Z", "2026-08-21T01:09:23Z"),
            quote(),
        ]
        result = MODULE.evaluate_freshness(batch(rows), policy(), CONTRACT)
        self.assertEqual([row["market"] for row in result["results"]], ["US", "KOREA", "CRYPTO"])
        self.assertEqual([row["freshness_status"] for row in result["results"]], ["FRESH"] * 3)

    def test_unratified_late_or_ineffective_policy_fails_closed(self):
        variants = [
            (policy(approval_status="DRAFT"), "POLICY_IDENTITY_INVALID"),
            (policy(ratified_at_utc="2026-08-21T00:00:01Z"), "POLICY_RATIFIED_AFTER_EFFECTIVE_START"),
            (policy(effective_to_utc="2026-08-21T01:00:00Z"), "POLICY_NOT_EFFECTIVE"),
        ]
        for value, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.IntradayFreshnessError, error
            ):
                MODULE.evaluate_freshness(batch(), value, CONTRACT)

    def test_future_and_reversed_clocks_fail_closed(self):
        cases = [
            quote(provider_timestamp="2026-08-21T01:10:01Z", received_at="2026-08-21T01:10:01Z"),
            quote(provider_timestamp="2026-08-21T01:09:40Z", received_at="2026-08-21T01:09:39Z"),
        ]
        for row in cases:
            with self.subTest(row=row), self.assertRaisesRegex(
                MODULE.IntradayFreshnessError, "QUOTE_TIME_ORDER_INVALID"
            ):
                MODULE.evaluate_freshness(batch([row]), policy(), CONTRACT)

    def test_duplicate_asset_digest_and_authority_drift_fail_closed(self):
        duplicate = batch([quote(), copy.deepcopy(quote())])
        with self.assertRaisesRegex(MODULE.IntradayFreshnessError, "QUOTE_ASSET_DUPLICATE"):
            MODULE.evaluate_freshness(duplicate, policy(), CONTRACT)
        digest = batch()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.IntradayFreshnessError, "BATCH_SHA_INVALID_MISMATCH"):
            MODULE.evaluate_freshness(digest, policy(), CONTRACT)
        authority = batch()
        authority["authority"]["entry_eligibility_authorized"] = True
        authority["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in authority.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.IntradayFreshnessError, "BATCH_IDENTITY_INVALID"):
            MODULE.evaluate_freshness(authority, policy(), CONTRACT)

    def test_deterministic_permutation_safe_and_inputs_immutable(self):
        rows = [
            quote(),
            quote("CRYPTO.KRAKEN.BTC", "CRYPTO", "2026-08-21T01:09:30Z", "2026-08-21T01:09:35Z"),
        ]
        first_batch = batch(rows)
        first_policy = policy()
        before = MODULE.canonical_json([first_batch, first_policy])
        first = MODULE.evaluate_freshness(first_batch, first_policy, CONTRACT)
        second = MODULE.evaluate_freshness(batch(list(reversed(rows))), policy(), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json([first_batch, first_policy]), before)

    def test_output_embeds_full_ratified_policy_with_matching_lineage(self):
        result = MODULE.evaluate_freshness(batch(), policy(), CONTRACT)
        self.assertEqual(result["schema_version"], "intraday_freshness_result/2")
        self.assertEqual(result["policy_id"], "INTRADAY.FRESHNESS.TEST.V1")
        self.assertEqual(result["policy_packet"]["approval_status"], "RATIFIED")
        self.assertEqual(result["policy_packet"]["policy_id"], result["policy_id"])
        self.assertEqual(
            result["lineage"]["policy_sha256"],
            result["policy_packet"]["packet_sha256"],
        )

    def test_consumer_revalidates_embedded_policy_and_rejects_forgery(self):
        stale_quote = quote(
            provider_timestamp="2026-08-21T01:08:00Z",
            received_at="2026-08-21T01:08:05Z",
        )
        original = MODULE.evaluate_freshness(
            batch([stale_quote]), policy(), CONTRACT
        )
        self.assertEqual(original["results"][0]["freshness_status"], "STALE")

        # Mirrors the original exploit: forge policy_id/threshold in the
        # embedded policy, leave its own packet_sha256 stale, and only
        # recompute the outer envelope's packet_sha256.
        forged = copy.deepcopy(original)
        forged["policy_packet"]["max_provider_age_seconds_by_market"]["US"] = 86400
        forged["policy_packet"]["policy_id"] = "NEVER.RATIFIED.POLICY"
        forged["policy_packet"]["ratified_by"] = "attacker"
        row = forged["results"][0]
        row["max_provider_age_seconds"] = 86400
        row["freshness_status"] = "FRESH"
        row["stale_reasons"] = []
        row["fresh_for_intraday_consumption"] = True
        forged["summary"]["fresh_count"] = 1
        forged["summary"]["stale_count"] = 0
        forged.pop("packet_sha256")
        forged["packet_sha256"] = MODULE.payload_sha256(forged)
        with self.assertRaisesRegex(
            MODULE.IntradayFreshnessError, "POLICY_SHA_INVALID"
        ):
            MODULE.validate_output(forged, CONTRACT)

        # Consumption-side re-validation also rejects a DRAFT / unratified
        # policy even when the embedded packet is internally self-consistent.
        draft = copy.deepcopy(original)
        draft["policy_packet"]["approval_status"] = "DRAFT"
        draft["policy_packet"].pop("packet_sha256")
        draft["policy_packet"]["packet_sha256"] = MODULE.payload_sha256(
            draft["policy_packet"]
        )
        draft["lineage"]["policy_sha256"] = draft["policy_packet"]["packet_sha256"]
        draft.pop("packet_sha256")
        draft["packet_sha256"] = MODULE.payload_sha256(draft)
        with self.assertRaisesRegex(
            MODULE.IntradayFreshnessError, "POLICY_IDENTITY_INVALID"
        ):
            MODULE.validate_output(draft, CONTRACT)

        # The consumer must independently enforce the policy's input-contract
        # binding even when both the embedded and outer digests are recomputed.
        wrong_contract = copy.deepcopy(original)
        wrong_contract["policy_packet"]["input_contract_version"] = (
            "intraday_freshness_guard/999"
        )
        wrong_contract["policy_packet"].pop("packet_sha256")
        wrong_contract["policy_packet"]["packet_sha256"] = MODULE.payload_sha256(
            wrong_contract["policy_packet"]
        )
        wrong_contract["lineage"]["policy_sha256"] = wrong_contract[
            "policy_packet"
        ]["packet_sha256"]
        wrong_contract.pop("packet_sha256")
        wrong_contract["packet_sha256"] = MODULE.payload_sha256(wrong_contract)
        with self.assertRaisesRegex(
            MODULE.IntradayFreshnessError, "POLICY_IDENTITY_INVALID"
        ):
            MODULE.validate_output(wrong_contract, CONTRACT)

        # The effective window is also rechecked at consumption time. The end
        # is exclusive, so equality with observed_at is already ineffective.
        ineffective = copy.deepcopy(original)
        ineffective["policy_packet"]["effective_to_utc"] = original["observed_at"]
        ineffective["policy_packet"].pop("packet_sha256")
        ineffective["policy_packet"]["packet_sha256"] = MODULE.payload_sha256(
            ineffective["policy_packet"]
        )
        ineffective["lineage"]["policy_sha256"] = ineffective["policy_packet"][
            "packet_sha256"
        ]
        ineffective.pop("packet_sha256")
        ineffective["packet_sha256"] = MODULE.payload_sha256(ineffective)
        with self.assertRaisesRegex(
            MODULE.IntradayFreshnessError, "POLICY_NOT_EFFECTIVE"
        ):
            MODULE.validate_output(ineffective, CONTRACT)

        # policy_id at the top level must match the embedded policy_packet.
        id_mismatch = copy.deepcopy(original)
        id_mismatch["policy_id"] = "SOME.OTHER.POLICY"
        id_mismatch.pop("packet_sha256")
        id_mismatch["packet_sha256"] = MODULE.payload_sha256(id_mismatch)
        with self.assertRaisesRegex(
            MODULE.IntradayFreshnessError, "OUTPUT_POLICY_ID_MISMATCH"
        ):
            MODULE.validate_output(id_mismatch, CONTRACT)

        # lineage.policy_sha256 must match the embedded policy's own digest,
        # even when policy_packet itself is untouched.
        lineage_mismatch = copy.deepcopy(original)
        lineage_mismatch["lineage"]["policy_sha256"] = "0" * 64
        lineage_mismatch.pop("packet_sha256")
        lineage_mismatch["packet_sha256"] = MODULE.payload_sha256(lineage_mismatch)
        with self.assertRaisesRegex(
            MODULE.IntradayFreshnessError, "OUTPUT_LINEAGE_POLICY_SHA_MISMATCH"
        ):
            MODULE.validate_output(lineage_mismatch, CONTRACT)

        # A genuinely valid packet still round-trips through validate_output.
        self.assertEqual(MODULE.validate_output(original, CONTRACT), original)

    def test_output_derivation_summary_and_authority_tamper_fail_closed(self):
        original = MODULE.evaluate_freshness(batch(), policy(), CONTRACT)
        variants = []
        status = copy.deepcopy(original)
        status["results"][0]["freshness_status"] = "STALE"
        variants.append((status, "OUTPUT_RESULT_DERIVATION_INVALID"))
        provider = copy.deepcopy(original)
        provider["results"][0]["provider_id"] = "bad provider"
        variants.append((provider, "OUTPUT_PROVIDER_INVALID"))
        summary = copy.deepcopy(original)
        summary["summary"]["entry_eligible_count"] = 1
        variants.append((summary, "OUTPUT_SUMMARY_INVALID"))
        authority = copy.deepcopy(original)
        authority["authority"]["entry_eligibility_authorized"] = True
        variants.append((authority, "OUTPUT_IDENTITY_INVALID"))
        digest = copy.deepcopy(original)
        digest["packet_sha256"] = "0" * 64
        variants.append((digest, "OUTPUT_SHA_MISMATCH"))
        for packet, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.IntradayFreshnessError, error
            ):
                MODULE.validate_output(packet, CONTRACT)

    def test_serialized_output_remains_valid_when_json_key_order_changes(self):
        packet = MODULE.evaluate_freshness(batch(), policy(), CONTRACT)
        serialized = json.loads(json.dumps(packet, sort_keys=True))
        self.assertEqual(MODULE.validate_output(serialized, CONTRACT), serialized)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            batch_path = temp / "batch.json"
            policy_path = temp / "policy.json"
            batch_path.write_text(json.dumps(batch()), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            output = temp / "out" / "freshness.json"
            self.assertEqual(MODULE.run(batch_path, policy_path, output), 0)
            forbidden = ROOT / "data" / "intraday_freshness_test.json"
            self.assertEqual(MODULE.run(batch_path, policy_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
