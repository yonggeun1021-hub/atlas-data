#!/usr/bin/env python3
"""P10-01 three-market zero-capital Shadow ledger regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "three_market_shadow_ledger.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("three_market_shadow_ledger", SOURCE)
UNIFIED_FIXTURE = load_module(
    "shadow_unified_fixture", ROOT / "test" / "test_unified_decision_contract.py"
)
CONTRACT = MODULE.load_contract()


def decision(day="2026-08-21", generated="2026-08-21T02:10:00Z"):
    return UNIFIED_FIXTURE.MODULE.build_packet(
        UNIFIED_FIXTURE.components(), UNIFIED_FIXTURE.reasons(), day, "morning",
        generated, UNIFIED_FIXTURE.CONTRACT,
    )


def unavailable_rule_decision():
    source = UNIFIED_FIXTURE.components()
    source["RULE"] = None
    reasons = UNIFIED_FIXTURE.reasons()
    reasons["RULE"] = ["RULE_PACKET_NOT_PROVIDED"]
    return UNIFIED_FIXTURE.MODULE.build_packet(
        source, reasons, "2026-08-21", "morning",
        "2026-08-21T02:10:00Z", UNIFIED_FIXTURE.CONTRACT,
    )


class ThreeMarketShadowLedgerTests(unittest.TestCase):
    def test_contract_is_zero_capital_recording_only(self):
        self.assertEqual(CONTRACT["markets"], ["US", "KOREA", "CRYPTO"])
        self.assertEqual(CONTRACT["capital_mode"], "ZERO_CAPITAL_SHADOW_ONLY")
        self.assertTrue(CONTRACT["authority"]["shadow_observation_recording_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "shadow_observation_recording_only":
                self.assertFalse(value, key)

    def test_empty_ledger_has_no_capital_orders_or_decision(self):
        ledger = MODULE.empty_ledger(CONTRACT)
        self.assertEqual(ledger["status"], "EMPTY")
        self.assertEqual(ledger["ledger_revision"], 0)
        self.assertEqual(ledger["records"], [])
        self.assertEqual(ledger["summary"]["real_capital_deployed"], "0")
        self.assertEqual(ledger["summary"]["real_order_count"], 0)

    def test_append_records_exact_unified_decision_and_three_markets(self):
        source = decision()
        ledger = MODULE.append_decision(
            source, "2026-08-21T02:15:00Z", None, CONTRACT
        )
        self.assertEqual(ledger["status"], "SHADOW_HISTORY_RECORDED")
        self.assertEqual(ledger["ledger_revision"], 1)
        row = ledger["records"][0]
        self.assertEqual(row["unified_decision"], source)
        self.assertEqual(row["unified_decision_sha256"], source["packet_sha256"])
        self.assertEqual(
            [item["market"] for item in row["market_snapshots"]],
            ["US", "KOREA", "CRYPTO"],
        )
        self.assertEqual(row["rotation_change_count"], 0)
        self.assertEqual(row["discovery_case_count"], 1)

    def test_shadow_record_never_deploys_capital_or_creates_order(self):
        ledger = MODULE.append_decision(
            decision(), "2026-08-21T02:15:00Z", None, CONTRACT
        )
        row = ledger["records"][0]
        self.assertEqual(row["capital_mode"], "ZERO_CAPITAL_SHADOW_ONLY")
        self.assertEqual(row["real_capital_deployed"], "0")
        self.assertEqual(row["real_order_count"], 0)
        self.assertIsNone(row["action"])
        self.assertIsNone(row["entry_trigger"])
        self.assertIsNone(row["position_size"])
        self.assertIsNone(row["order_intent"])

    def test_forward_append_builds_record_hash_chain(self):
        first = MODULE.append_decision(
            decision(), "2026-08-21T02:15:00Z", None, CONTRACT
        )
        second_decision = decision("2026-08-22", "2026-08-22T02:10:00Z")
        second = MODULE.append_decision(
            second_decision, "2026-08-22T02:15:00Z", first, CONTRACT
        )
        self.assertEqual(second["ledger_revision"], 2)
        self.assertEqual(second["summary"]["decision_date_count"], 2)
        self.assertEqual(
            second["records"][1]["prior_record_sha256"],
            second["records"][0]["record_sha256"],
        )

    def test_same_decision_retry_is_idempotent_but_payload_conflict_fails(self):
        first = MODULE.append_decision(
            decision(), "2026-08-21T02:15:00Z", None, CONTRACT
        )
        retry = MODULE.append_decision(
            decision(), "2026-08-21T02:20:00Z", first, CONTRACT
        )
        self.assertEqual(retry, first)
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "DECISION_ID_PAYLOAD_CONFLICT"
        ):
            MODULE.append_decision(
                unavailable_rule_decision(), "2026-08-21T02:20:00Z", first, CONTRACT
            )

    def test_required_regime_rotation_and_record_time_fail_closed(self):
        source = UNIFIED_FIXTURE.components()
        source["REGIME"] = None
        reasons = UNIFIED_FIXTURE.reasons()
        reasons["REGIME"] = ["REGIME_PACKET_NOT_PROVIDED"]
        missing = UNIFIED_FIXTURE.MODULE.build_packet(
            source, reasons, "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", UNIFIED_FIXTURE.CONTRACT,
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError,
            "REQUIRED_COMPONENT_UNAVAILABLE:REGIME",
        ):
            MODULE.append_decision(
                missing, "2026-08-21T02:15:00Z", None, CONTRACT
            )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "RECORDED_BEFORE_DECISION"
        ):
            MODULE.append_decision(
                decision(), "2026-08-21T02:09:59Z", None, CONTRACT
            )

    def test_non_forward_decision_and_tampered_chain_fail_closed(self):
        later = MODULE.append_decision(
            decision("2026-08-22", "2026-08-22T02:10:00Z"),
            "2026-08-22T02:15:00Z", None, CONTRACT,
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "LEDGER_NON_FORWARD_DECISION"
        ):
            MODULE.append_decision(
                decision(), "2026-08-22T02:20:00Z", later, CONTRACT
            )

        first = MODULE.append_decision(
            decision(), "2026-08-21T02:15:00Z", None, CONTRACT
        )
        tampered = copy.deepcopy(first)
        tampered["records"][0]["real_capital_deployed"] = "1"
        tampered["records"][0]["record_sha256"] = MODULE.payload_sha256({
            key: value for key, value in tampered["records"][0].items()
            if key != "record_sha256"
        })
        tampered["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in tampered.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "RECORD_MISMATCH"
        ):
            MODULE.validate_ledger(tampered, CONTRACT)

    def test_output_is_deterministic_and_inputs_are_immutable(self):
        source = decision()
        before = MODULE.canonical_json(source)
        first = MODULE.append_decision(
            source, "2026-08-21T02:15:00Z", None, CONTRACT
        )
        second = MODULE.append_decision(
            source, "2026-08-21T02:15:00Z", None, CONTRACT
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(source), before)

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
            source_path = temp / "decision.json"
            source_path.write_text(json.dumps(decision()), encoding="utf-8")
            output = temp / "out" / "ledger.json"
            self.assertEqual(
                MODULE.run(
                    source_path, "2026-08-21T02:15:00Z", output, None
                ),
                0,
            )
            self.assertEqual(json.loads(output.read_text())["ledger_revision"], 1)
            forbidden = ROOT / "data" / "three_market_shadow_ledger_test.json"
            self.assertEqual(
                MODULE.run(
                    source_path, "2026-08-21T02:15:00Z", forbidden, None
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
