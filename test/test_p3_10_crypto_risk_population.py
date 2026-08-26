#!/usr/bin/env python3
"""P3-10 detached BTC risk-source population and binding regression."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "p3_10_crypto_risk_population.py"
SNAPSHOT = ROOT / "evidence" / "crypto" / "btc" / "raw" / "2026-08-26"
WORKFLOW = ROOT / ".github" / "workflows" / "btc-price-capture.yml"
SPEC = importlib.util.spec_from_file_location("p3_10_crypto_risk_population", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def candidate_ref(**updates):
    value = {
        "case_id": "BTC-DISCOVERY-20260825",
        "case_schema_version": "discovery_case/1",
        "market": "CRYPTO",
        "asset_id": "CRYPTO:BTC",
        "observation_date": "2026-08-25",
        "case_payload_sha256": "a" * 64,
    }
    value.update(updates)
    return value


class P310CryptoRiskPopulationTest(unittest.TestCase):
    def test_actual_pit_snapshot_publishes_three_detached_two_point_contexts(self):
        packet = MODULE.build_source_packet(SNAPSHOT)

        self.assertEqual(packet["schema_version"], "valuation_risk_source_observation/1")
        self.assertEqual(packet["asset_id"], "CRYPTO:BTC")
        self.assertEqual(packet["expected_periods"], ["2026-08-24", "2026-08-25"])
        self.assertEqual(packet["candidate_binding"]["status"], "BLOCKED_NO_ALLOWED_CASE")
        self.assertIsNone(packet["candidate_binding"]["candidate_ref"])
        self.assertEqual(
            [item["metric_type"] for item in packet["risk_context_observations"]],
            ["CURRENT_DRAWDOWN", "MAXIMUM_DRAWDOWN", "REALIZED_VOLATILITY"],
        )
        self.assertEqual(
            [[point["numeric_value"] for point in item["evidence_points"]]
             for item in packet["risk_context_observations"]],
            [
                ["0.000000000000", "0.005783494436"],
                ["0.212615235192", "0.206386933808"],
                ["0.457486526668", "0.454552809507"],
            ],
        )
        self.assertEqual(
            packet["source_transform"]["source_sha256"],
            "37b6b5a9efd024ae3d25f2da14ac76b3db6d6a39f6e7fd83f6d268be537e7da1",
        )
        for context in packet["risk_context_observations"]:
            for point in context["evidence_points"]:
                source = point["source_identities"][0]
                self.assertEqual(source["available_at"], "2026-08-26T03:39:32Z")
                self.assertEqual(source["retrieved_at_utc"], source["available_at"])

    def test_real_canonical_btc_identity_is_hash_bound(self):
        packet = MODULE.build_source_packet(SNAPSHOT)
        identity = packet["identity_ref"]
        self.assertEqual(identity["policy_version"], "canonical_security_identity/v1")
        self.assertEqual(identity["canonical_instrument_id"], "CRYPTO:BTC")
        self.assertEqual(identity["listing_id"], "KRAKEN:BTC-USD:SPOT")
        self.assertEqual(
            identity["authority_sha256"],
            MODULE.hashlib.sha256(MODULE.IDENTITY_PATH.read_bytes()).hexdigest(),
        )

    def test_allowed_case_can_bind_without_interpretation_or_valuation(self):
        packet = MODULE.build_candidate_packet(SNAPSHOT, candidate_ref())
        candidate = packet["candidate_contexts"][0]
        contexts = candidate["risk"]["contexts"]

        self.assertEqual(packet["schema_version"], "valuation_risk_context_packet/3")
        self.assertEqual(packet["contract_version"], "valuation_risk_context/2")
        self.assertEqual(candidate["valuation"]["status"], "EVIDENCE_ABSENT")
        self.assertEqual(candidate["risk"]["status"], "OBSERVED_RAW_CONTEXT")
        self.assertFalse(candidate["context_complete"])
        self.assertEqual([item["change"] for item in contexts], [
            "0.005783494436", "-0.006228301384", "-0.002933717161",
        ])
        self.assertTrue(all(item["deterioration_policy_match"] is None for item in contexts))
        self.assertTrue(all(item["interpretation_status"] == "ABSENT_OR_UNRATIFIED_POLICY" for item in contexts))
        self.assertIsNone(packet["source_policy"])

    def test_missing_or_wrong_candidate_identity_fails_closed(self):
        with self.assertRaisesRegex(MODULE.PopulationError, "CANDIDATE_SOURCE_IDENTITY_MISMATCH"):
            MODULE.build_candidate_packet(SNAPSHOT, candidate_ref(asset_id="CRYPTO:ETH"))
        with self.assertRaisesRegex(
            MODULE.valuation_risk.ValuationRiskContextError,
            "CASE_SCHEMA_VERSION_INVALID",
        ):
            MODULE.build_candidate_packet(
                SNAPSHOT,
                candidate_ref(case_schema_version="candidate_identity_proposal/1"),
            )

    def test_raw_replay_validation_rejects_self_rehashed_value_drift(self):
        packet = MODULE.build_source_packet(SNAPSHOT)
        tampered = copy.deepcopy(packet)
        tampered["risk_context_observations"][0]["evidence_points"][1]["numeric_value"] = "0.999000000000"
        tampered["payload_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(MODULE.PopulationError, "SOURCE_PACKET_RAW_REPLAY_MISMATCH"):
            MODULE.validate_source_packet(tampered, SNAPSHOT)

    def test_publish_is_append_only_and_byte_identical(self):
        packet = MODULE.build_source_packet(SNAPSHOT)
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            target, first = MODULE.publish_packet(packet, "2026-08-26", output)
            same, second = MODULE.publish_packet(packet, "2026-08-26", output)
            self.assertEqual((first, second), ("published", "existing_identical"))
            self.assertEqual(target, same)
            tampered = copy.deepcopy(packet)
            tampered["candidate_binding"]["status"] = "ATTACHED"
            with self.assertRaisesRegex(MODULE.PopulationError, "APPEND_ONLY_PACKET_MISMATCH"):
                MODULE.publish_packet(tampered, "2026-08-26", output)
            self.assertEqual(target.read_bytes(), MODULE.packet_bytes(packet))

    def test_workflow_populates_after_validation_and_commits_detached_source(self):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["capture"]["steps"]
        population = next(
            item for item in steps
            if item.get("name") == "Populate P3-10 Crypto risk source context"
        )
        commit = next(item for item in steps if item.get("name") == "Commit BTC price evidence")
        self.assertEqual(population["if"], "${{ steps.validation.outcome == 'success' }}")
        self.assertIn("p3_10_crypto_risk_population.py", population["run"])
        self.assertIn("steps.capture.outputs.snapshot_date", population["env"]["SNAPSHOT_DATE"])
        self.assertIn("evidence/valuation_risk_sources/crypto/$SNAPSHOT_DATE", commit["run"])
        self.assertNotIn("--policy", population["run"])

    def test_all_decision_and_trading_authority_remains_closed(self):
        packet = MODULE.build_source_packet(SNAPSHOT)
        authority = packet["authority"]
        self.assertTrue(authority["raw_source_context_authorized"])
        self.assertTrue(authority["candidate_attachment_requires_allowed_case"])
        for key in (
            "deterioration_interpretation_authorized",
            "candidate_creation_authorized",
            "candidate_mutation_authorized",
            "candidate_ranking_authorized",
            "stage_promotion_authorized",
            "rule_evaluation_authorized",
            "portfolio_action_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertIs(authority[key], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
