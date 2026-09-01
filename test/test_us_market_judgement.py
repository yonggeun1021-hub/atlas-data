#!/usr/bin/env python3
"""PAPER 12-6 deterministic US market-judgement regression."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "regime" / "us_market_judgement.py"


def load_module():
    spec = importlib.util.spec_from_file_location("us_market_judgement_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module()
CONTRACT = MODULE.load_contract()
EVALUATION_AT = "2026-08-31T22:15:00Z"
SESSION_DATE = "2026-08-31"


def baseline() -> dict:
    return MODULE.build_no_input_baseline(EVALUATION_AT, SESSION_DATE)


def available_source(source_id: str, path: Path, policy_path: Path) -> dict:
    return {
        "sourceId": source_id,
        "status": "AVAILABLE",
        "observationDate": SESSION_DATE,
        "sourceTime": "2026-08-31T21:15:00Z",
        "ttlSeconds": 7200,
        "pin": {"ref": str(path), "sha256": MODULE.file_sha256(path)},
        "policy": {
            "version": "synthetic_policy/v1",
            "approvalStatus": "RATIFIED",
            "pin": {"ref": str(policy_path), "sha256": MODULE.file_sha256(policy_path)},
        },
        "coverage": {"status": "COVERAGE_MET", "observedCount": 5, "requiredCount": 5},
        "sessionStatus": "FINISHED" if source_id == "US_FINISHED_SESSION" else None,
    }


class USMarketJudgementTests(unittest.TestCase):
    def test_contract_preserves_current_unratified_and_zero_authority_facts(self):
        self.assertEqual(CONTRACT["current_policy_facts"]["leadership_policy_status"], "UNRATIFIED")
        self.assertEqual(CONTRACT["current_policy_facts"]["leadership_policy_version"], "us_leadership/draft-v1")
        self.assertEqual(CONTRACT["required_axis_count"], 5)
        self.assertFalse(CONTRACT["current_policy_facts"]["price_breadth_authorized"])
        self.assertTrue(CONTRACT["authority"]["paper_observation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "paper_observation_only":
                self.assertFalse(value, key)

    def test_schema_requires_all_exact_source_envelope_fields(self):
        schema = MODULE.load_schema()
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], MODULE.INPUT_SCHEMA_VERSION)
        self.assertEqual(schema["properties"]["sources"]["minItems"], 8)
        required = set(schema["$defs"]["source"]["required"])
        self.assertEqual(
            required,
            {"sourceId", "status", "observationDate", "sourceTime", "ttlSeconds", "pin", "policy", "coverage", "sessionStatus"},
        )

    def test_machine_report_baseline_hashes_are_rederived(self):
        report = json.loads(
            (ROOT / "reports" / "paper_12_6_us_market_judgement_report.json").read_text(
                encoding="utf-8"
            )
        )
        baseline_report = report["no_input_baseline_receipt"]
        value = MODULE.build_no_input_baseline(
            baseline_report["evaluation_at"], baseline_report["session_date"]
        )
        receipt = MODULE.build_receipt(value)
        self.assertEqual(baseline_report["input_sha256"], receipt["inputSha256"])
        self.assertEqual(baseline_report["receipt_sha256"], receipt["receiptSha256"])
        self.assertEqual(
            baseline_report["paper_12_4_regime_output_sha256"],
            receipt["consumerPins"]["paper_12_4"]["sha256"],
        )
        self.assertEqual(
            baseline_report["paper_12_1_bridge_projection_sha256"],
            receipt["consumerPins"]["paper_12_1"]["sha256"],
        )
        self.assertEqual(
            baseline_report["runtime_sha256"],
            receipt["implementationPins"]["runtime"]["sha256"],
        )

    def test_no_input_baseline_is_exactly_unknown_hold_zero_of_five(self):
        receipt = MODULE.build_receipt(baseline())
        self.assertEqual(receipt["status"], "HOLD")
        self.assertEqual(receipt["judgement"], "UNKNOWN")
        self.assertEqual(receipt["recommendation"], "WAIT")
        self.assertIsNone(receipt["action"])
        self.assertEqual(receipt["regimeOutput"]["coverage"]["ratio"], "0/5")
        self.assertEqual(receipt["coverageGate"]["gate_result"], "BLOCKED")
        self.assertEqual(
            [row["axis"] for row in receipt["axisChecks"]],
            ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"],
        )
        self.assertTrue(all(row["status"] == "UNDEFINED" for row in receipt["axisChecks"]))
        for blocker in (
            "US_LEADERSHIP_POLICY_UNRATIFIED",
            "US_UNIVERSE_POLICY_UNRATIFIED",
            "US_PRICE_BREADTH_NOT_AUTHORIZED",
            "REGIME_CLASSIFICATION_NOT_AUTHORIZED",
            "MINIMUM_COVERAGE_NOT_MET",
            "TREND_UNDEFINED",
            "BREADTH_UNDEFINED",
            "RISK_VOL_UNDEFINED",
            "LIQUIDITY_UNDEFINED",
            "LEADERSHIP_UNDEFINED",
        ):
            self.assertIn(blocker, receipt["blockers"])

    def test_all_synthetic_sources_cannot_override_repository_policy_blockers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            policy = root / "policy.json"
            source.write_text('{"synthetic":true}\n', encoding="utf-8")
            policy.write_text(
                '{"approval_status":"RATIFIED","policy_version":"synthetic_policy/v1","synthetic":true}\n',
                encoding="utf-8",
            )
            value = baseline()
            value["evidenceClass"] = "SYNTHETIC_CONTRACT_TEST"
            value["sources"] = [
                available_source(source_id, source, policy)
                for source_id in CONTRACT["source_order"]
            ]
            receipt = MODULE.build_receipt(value)
        self.assertEqual(receipt["regimeOutput"]["coverage"]["ratio"], "0/5")
        self.assertEqual(receipt["status"], "HOLD")
        self.assertIsNone(receipt["action"])
        universe = next(row for row in receipt["sourceChecks"] if row["sourceId"] == "US_UNIVERSE")
        leadership = next(row for row in receipt["sourceChecks"] if row["sourceId"] == "US_SECTOR_LEADERSHIP")
        self.assertIn("US_UNIVERSE_POLICY_UNRATIFIED", universe["reasons"])
        self.assertIn("US_LEADERSHIP_POLICY_UNRATIFIED", leadership["reasons"])

    def test_hash_mismatch_stale_ttl_and_unfinished_session_are_exact_blockers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            policy = root / "policy.json"
            source.write_text("{}\n", encoding="utf-8")
            policy.write_text(
                '{"approval_status":"RATIFIED","policy_version":"synthetic_policy/v1"}\n',
                encoding="utf-8",
            )
            value = baseline()
            rows = []
            for source_id in CONTRACT["source_order"]:
                row = available_source(source_id, source, policy)
                rows.append(row)
            rows[0]["pin"]["sha256"] = "0" * 64
            rows[1]["sourceTime"] = "2026-08-31T19:00:00Z"
            rows[-1]["sessionStatus"] = "IN_PROGRESS"
            value["sources"] = rows
            receipt = MODULE.build_receipt(value)
        checks = {row["sourceId"]: row for row in receipt["sourceChecks"]}
        self.assertIn("US_UNIVERSE_PIN_HASH_MISMATCH", checks["US_UNIVERSE"]["reasons"])
        self.assertIn("US_BREADTH_TTL_EXPIRED", checks["US_BREADTH"]["reasons"])
        self.assertIn("US_FINISHED_SESSION_IN_PROGRESS", checks["US_FINISHED_SESSION"]["reasons"])
        self.assertEqual(receipt["status"], "HOLD")

    def test_policy_pin_content_must_match_claimed_version_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            policy = root / "policy.json"
            source.write_text("{}\n", encoding="utf-8")
            policy.write_text(
                '{"approval_status":"UNRATIFIED","policy_version":"other/v1"}\n',
                encoding="utf-8",
            )
            value = baseline()
            value["sources"] = [
                available_source(source_id, source, policy)
                for source_id in CONTRACT["source_order"]
            ]
            receipt = MODULE.build_receipt(value)
        for row in receipt["sourceChecks"]:
            self.assertIn(f"{row['sourceId']}_POLICY_CONTENT_MISMATCH", row["reasons"])

    def test_consumer_pins_bind_exact_aggregate_and_bridge_subtrees(self):
        receipt = MODULE.build_receipt(baseline())
        aggregate = receipt["consumerPins"]["paper_12_4"]
        bridge = receipt["consumerPins"]["paper_12_1"]
        self.assertEqual(aggregate["json_pointer"], "/regimeOutput")
        self.assertEqual(aggregate["sha256"], MODULE.payload_sha256(receipt["regimeOutput"]))
        self.assertEqual(bridge["json_pointer"], "/paperDecisionBridgeProjection")
        self.assertEqual(bridge["sha256"], MODULE.payload_sha256(receipt["paperDecisionBridgeProjection"]))
        self.assertEqual(
            receipt["implementationPins"]["contract"]["sha256"],
            MODULE.file_sha256(ROOT / "config" / "us_market_judgement_contract.json"),
        )
        self.assertEqual(
            receipt["implementationPins"]["runtime"]["sha256"],
            MODULE.file_sha256(SOURCE),
        )
        projection = receipt["paperDecisionBridgeProjection"]
        self.assertEqual(projection["marketJudgement"]["status"], "HOLD")
        self.assertEqual(projection["marketJudgement"]["coverage"]["ratio"], "0/5")
        self.assertEqual(projection["leadership"]["approvalStatus"], "UNRATIFIED")
        self.assertIsNone(projection["action"])

    def test_receipt_tamper_and_same_identity_conflict_fail_closed(self):
        value = baseline()
        receipt = MODULE.build_receipt(value)
        changed = copy.deepcopy(receipt)
        changed["status"] = "PASS"
        with self.assertRaisesRegex(MODULE.USMarketJudgementError, "RECEIPT_SHA_MISMATCH"):
            MODULE.validate_receipt(changed, value)
        forged = copy.deepcopy(receipt)
        forged["blockers"] = ["FORGED_BLOCKER_SET"]
        forged.pop("receiptSha256")
        forged["receiptSha256"] = MODULE.payload_sha256(forged)
        with self.assertRaisesRegex(MODULE.USMarketJudgementError, "RECEIPT_DERIVATION_MISMATCH"):
            MODULE.validate_receipt(forged, value)
        with tempfile.TemporaryDirectory() as directory:
            path, first = MODULE.persist_immutable_receipt(receipt, Path(directory))
            second_path, second = MODULE.persist_immutable_receipt(receipt, Path(directory))
            self.assertEqual((first, second), ("CREATED", "NO_CHANGE"))
            self.assertEqual(path, second_path)

    def test_runtime_summary_and_receipt_are_replay_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            first_out = root / "first.json"
            second_out = root / "second.json"
            receipts = root / "receipts"
            input_path.write_text(json.dumps(baseline()) + "\n", encoding="utf-8")
            self.assertEqual(MODULE.run(input_path, first_out, receipts), 0)
            self.assertEqual(MODULE.run(input_path, second_out, receipts), 0)
            first = json.loads(first_out.read_text(encoding="utf-8"))
            second = json.loads(second_out.read_text(encoding="utf-8"))
        self.assertEqual(first["disposition"], "CREATED")
        self.assertEqual(second["disposition"], "NO_CHANGE")
        self.assertEqual(first["receiptSha256"], second["receiptSha256"])
        self.assertEqual(first["coverage"], "0/5")
        self.assertEqual(first["status"], "HOLD")
        self.assertIsNone(first["action"])

    def test_source_order_and_unavailable_source_claims_fail_closed(self):
        value = baseline()
        value["sources"] = list(reversed(value["sources"]))
        with self.assertRaisesRegex(MODULE.USMarketJudgementError, "source_order"):
            MODULE.build_receipt(value)
        value = baseline()
        value["sources"][0]["sourceTime"] = "2026-08-31T21:00:00Z"
        with self.assertRaisesRegex(MODULE.USMarketJudgementError, "unavailable_has_time"):
            MODULE.build_receipt(value)

    def test_static_zero_transport_zero_broker_zero_order_boundary(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(imports.isdisjoint({"requests", "httpx", "urllib", "socket", "subprocess", "ccxt"}))
        receipt = MODULE.build_receipt(baseline())
        self.assertFalse(receipt["authority"]["network"])
        self.assertFalse(receipt["authority"]["broker"])
        self.assertFalse(receipt["authority"]["post"])
        self.assertFalse(receipt["authority"]["order"])


if __name__ == "__main__":
    unittest.main()
