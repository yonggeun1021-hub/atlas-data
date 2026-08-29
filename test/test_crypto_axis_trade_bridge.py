#!/usr/bin/env python3
"""P5-10 five-axis to per-symbol entry/exit bridge regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "decision" / "crypto_axis_trade_bridge.py"
SPEC = importlib.util.spec_from_file_location("crypto_axis_trade_bridge", MODULE_PATH)
BRIDGE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BRIDGE)


def latest_committed_packet() -> dict:
    paths = sorted((ROOT / "evidence" / "crypto_paper_decision").glob("*/*/*/packet.json"))
    if not paths:
        raise AssertionError("NO_COMMITTED_CRYPTO_PAPER_DECISION_PACKET")
    return json.loads(paths[-1].read_text(encoding="utf-8"))


class ContractTests(unittest.TestCase):
    def test_contract_pins_existing_exit_priority_and_all_authority_false(self):
        contract = BRIDGE.load_contract()
        exit_contract = BRIDGE.EXIT_MANAGER.load_contract()
        self.assertEqual(contract["exit_policy"]["priority_categories"], exit_contract["priority_categories"])
        self.assertTrue(contract["authority"])
        self.assertTrue(all(value is False for value in contract["authority"].values()))
        self.assertEqual(contract["aggregate_policy_status"], "UNRATIFIED")
        self.assertEqual(contract["aggregate_regimes_currently_authorized"], ["UNKNOWN"])


class RealEvidenceTests(unittest.TestCase):
    def test_latest_committed_packet_builds_honest_fail_closed_bridge(self):
        packet = latest_committed_packet()
        result = BRIDGE.build_bridge(packet)
        self.assertEqual(BRIDGE.validate_output(result), result)
        self.assertEqual(result["five_axis"]["required_count"], 5)
        expected_defined = sum(
            row["status"] == "DEFINED"
            for row in packet["crypto_regime_five_axis"].values()
        )
        self.assertEqual(result["five_axis"]["defined_count"], expected_defined)
        self.assertEqual(result["five_axis"]["all_defined"], expected_defined == 5)
        self.assertEqual(result["aggregate_policy"]["status"], "UNRATIFIED")
        self.assertEqual(result["aggregate_policy"]["regime"], "UNKNOWN")
        self.assertEqual(result["summary"]["automatic_entry_count"], 0)
        self.assertEqual(result["summary"]["automatic_exit_count"], 0)
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_source_axis_tamper_cannot_be_hidden_by_rehashing(self):
        packet = latest_committed_packet()
        tampered = copy.deepcopy(packet)
        tampered["crypto_regime_five_axis"]["BREADTH"]["status"] = "DEFINED"
        tampered["payload_sha256"] = BRIDGE.DECISION.payload_sha256(tampered)
        with self.assertRaisesRegex(BRIDGE.CryptoAxisTradeBridgeError, "SOURCE_DECISION_INVALID"):
            BRIDGE.build_bridge(tampered)

    def test_populate_is_idempotent_and_byte_stable(self):
        packet = latest_committed_packet()
        with tempfile.TemporaryDirectory(prefix="axis_bridge_") as tmp:
            source = Path(tmp) / "decision.json"
            source.write_text(json.dumps(packet), encoding="utf-8")
            first = BRIDGE.populate(source, output_root=Path(tmp) / "out")
            second = BRIDGE.populate(source, output_root=Path(tmp) / "out")
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(first["packet_sha256"], second["packet_sha256"])


class SymbolRuleTests(unittest.TestCase):
    def test_candidate_rules_cap_entry_and_preserve_exit_priority(self):
        contract = BRIDGE.load_contract()
        coverage = {
            "required_count": 5,
            "defined_count": 5,
            "all_defined": True,
            "missing_axes": [],
            "axes": {},
        }
        snapshot = {
            "candidates": [
                {
                    "market": "KRW-BTC",
                    "canonical_asset_id": "BTC",
                    "state": "PAPER_BUY_ELIGIBLE",
                    "reason": "SYNTHETIC_REACHABILITY_ONLY",
                },
                {
                    "market": "KRW-ETH",
                    "canonical_asset_id": "ETH",
                    "state": "BLOCKED",
                    "reason": "UPSTREAM_BLOCKER",
                },
            ]
        }
        rows = BRIDGE._build_symbol_rules(snapshot, coverage, contract)
        self.assertEqual(rows[0]["entry"]["state"], "WAIT")
        self.assertIn("AGGREGATE_POLICY_UNRATIFIED", rows[0]["entry"]["reasons"])
        self.assertIsNone(rows[0]["entry"]["order_draft"])
        self.assertFalse(rows[0]["entry"]["automatic_entry_generated"])
        self.assertEqual(rows[1]["entry"]["state"], "BLOCKED")
        for row in rows:
            self.assertEqual(
                row["exit"]["priority_categories"],
                BRIDGE.EXIT_MANAGER.load_contract()["priority_categories"],
            )
            self.assertEqual(row["exit"]["regime_signal"], "UNKNOWN")
            self.assertEqual(row["exit"]["trend_signal"], "UNKNOWN")
            self.assertFalse(row["exit"]["automatic_exit_generated"])

    def test_missing_axes_are_named_in_each_symbol_entry_reason(self):
        contract = BRIDGE.load_contract()
        coverage = {
            "required_count": 5,
            "defined_count": 3,
            "all_defined": False,
            "missing_axes": ["BREADTH", "LEADERSHIP"],
            "axes": {},
        }
        snapshot = {
            "candidates": [{
                "market": "KRW-BTC", "canonical_asset_id": "BTC",
                "state": "WATCH", "reason": "UPSTREAM_WAITING",
            }]
        }
        row = BRIDGE._build_symbol_rules(snapshot, coverage, contract)[0]
        self.assertIn("OFFICIAL_AXES_INCOMPLETE:BREADTH,LEADERSHIP", row["entry"]["reasons"])


if __name__ == "__main__":
    unittest.main()
