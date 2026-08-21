#!/usr/bin/env python3
"""P7-06 planned-stop / Portfolio loss-budget regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "planned_loss_budget.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("planned_loss_budget", SOURCE)
CONTRACT = MODULE.load_contract()


def constitution():
    return {
        "_comment": "Synthetic ratified Constitution for contract regression only.",
        "status": "ratified",
        "ratified_at": "2026-08-20T00:00:00Z",
        "constitution_version": "TEST-CONSTITUTION-1",
        "B1_bucket_definition": {"CORE_LONG": "test-only"},
        "B2_cash_floor_pct": 50,
        "B3_bucket_max_pct": 30,
        "B4_position_max_pct": 20,
        "B5_stop_loss_pct": 10,
        "B6_portfolio_max_loss_pct": 5,
        "B7_evidence_state_max_pct": {
            "backtest_only": 2,
            "forward_early": 5,
            "forward_established": 10,
            "operating": 20,
        },
        "amendment_log": [],
    }


def position(asset_id, market, weight, entry, stop, currency, marker):
    loss = round(weight * ((entry - stop) / entry), 12)
    return {
        "asset_id": asset_id,
        "market": market,
        "currency": currency,
        "position_weight_nav_fraction": weight,
        "entry_price": entry,
        "planned_stop_price": stop,
        "planned_loss_nav_fraction": loss,
        "position_record_sha256": marker * 64,
        "asset_identity_sha256": "a" * 64,
        "bucket_membership_packet_sha256": "b" * 64,
        "position_sizing_packet_sha256": "c" * 64,
    }


def input_packet(positions=None, crypto_sha="d" * 64):
    if positions is None:
        positions = [
            position("MSFT", "US", 0.10, 100, 95, "USD", "1"),
            position("005930", "KOREA", 0.08, 100, 92, "KRW", "2"),
            position("BTC", "CRYPTO", 0.05, 100, 90, "USD", "3"),
        ]
    value = {
        "schema_version": CONTRACT["input_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "snapshot_id": "TEST-PLANNED-LOSS-2026-08-21",
        "as_of_date": "2026-08-21",
        "generated_at_utc": "2026-08-21T00:30:00Z",
        "portfolio_snapshot_sha256": "e" * 64,
        "concentration_guard_packet_sha256": "f" * 64,
        "market_theme_budget_packet_sha256": "4" * 64,
        "crypto_exposure_limit_packet_sha256": crypto_sha,
        "positions": positions,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["positions"] = sorted(normalized["positions"], key=lambda row: row["asset_id"])
    for row in normalized["positions"]:
        row["stop_distance_fraction"] = round(
            (row["entry_price"] - row["planned_stop_price"]) / row["entry_price"], 12
        )
        row["planned_loss_nav_fraction"] = round(
            row["position_weight_nav_fraction"] * row["stop_distance_fraction"], 12
        )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class PlannedLossBudgetTests(unittest.TestCase):
    def test_contract_uses_canonical_constitution_without_action_authority(self):
        self.assertEqual(CONTRACT["canonical_constitution"], "config/constitution.json")
        self.assertEqual(CONTRACT["repository_default_status"], "BLOCKED_UNTIL_CONSTITUTION_B2_B7_RATIFIED")
        self.assertTrue(CONTRACT["authority"]["planned_loss_budget_evaluation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "planned_loss_budget_evaluation_only":
                self.assertFalse(value, key)

    def test_position_stops_connect_to_total_budget_without_exit_or_order(self):
        source = input_packet()
        ratified = constitution()
        packet = MODULE.build_packet(source, ratified, "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "WITHIN_RATIFIED_LOSS_BUDGET")
        self.assertEqual(packet["summary"], {
            "position_count": 3,
            "total_planned_loss_nav_fraction": 0.0164,
            "portfolio_loss_budget_nav_fraction": 0.05,
            "planned_loss_by_market": {"CRYPTO": 0.005, "KOREA": 0.0064, "US": 0.005},
            "breach_count": 0,
        })
        self.assertIsNone(packet["recommended_exit"])
        self.assertEqual(packet["stop_order_intents"], [])
        self.assertIsNone(packet["position_sizes"])
        self.assertEqual(packet["lineage"]["input_packet_sha256"], source["packet_sha256"])
        self.assertEqual(packet["lineage"]["constitution_sha256"], MODULE.payload_sha256(ratified))
        canonical_source = copy.deepcopy(source)
        canonical_source["positions"] = sorted(
            canonical_source["positions"], key=lambda row: row["asset_id"]
        )
        self.assertEqual(packet["source_packets"]["INPUT"], canonical_source)
        self.assertEqual(packet["source_packets"]["CONSTITUTION"], ratified)
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)

    def test_position_weight_stop_and_total_loss_breaches_are_independent(self):
        rows = [
            position("AAA", "US", 0.21, 100, 89, "USD", "1"),
            position("BBB", "US", 0.20, 100, 70, "USD", "2"),
        ]
        source = input_packet(rows, crypto_sha=None)
        packet = MODULE.build_packet(source, constitution(), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "LIMIT_BREACH")
        self.assertEqual({row["metric"] for row in packet["breaches"]}, {
            "POSITION_WEIGHT", "STOP_DISTANCE", "POSITION_PLANNED_LOSS",
            "PORTFOLIO_TOTAL_PLANNED_LOSS",
        })
        self.assertIsNone(packet["recommended_exit"])
        self.assertEqual(packet["stop_order_intents"], [])

    def test_stated_planned_loss_formula_and_stop_direction_fail_closed(self):
        rows = [position("AAA", "US", 0.10, 100, 90, "USD", "1")]
        rows[0]["planned_loss_nav_fraction"] = 0.5
        with self.assertRaisesRegex(MODULE.PlannedLossBudgetError, "PLANNED_LOSS_FORMULA_MISMATCH"):
            MODULE.build_packet(input_packet(rows, None), constitution(), "2026-08-21", CONTRACT)

        rows = [position("AAA", "US", 0.10, 100, 90, "USD", "1")]
        rows[0]["planned_stop_price"] = 100
        with self.assertRaisesRegex(MODULE.PlannedLossBudgetError, "PLANNED_STOP_NOT_BELOW_ENTRY"):
            MODULE.build_packet(input_packet(rows, None), constitution(), "2026-08-21", CONTRACT)

    def test_crypto_lineage_is_required_if_and_only_if_crypto_position_exists(self):
        crypto = [position("BTC", "CRYPTO", 0.05, 100, 90, "USD", "1")]
        with self.assertRaisesRegex(
            MODULE.PlannedLossBudgetError,
            "CRYPTO_EXPOSURE_LINEAGE_PRESENCE_MISMATCH",
        ):
            MODULE.build_packet(input_packet(crypto, None), constitution(), "2026-08-21", CONTRACT)

        us = [position("AAA", "US", 0.05, 100, 90, "USD", "1")]
        with self.assertRaisesRegex(
            MODULE.PlannedLossBudgetError,
            "CRYPTO_EXPOSURE_LINEAGE_PRESENCE_MISMATCH",
        ):
            MODULE.build_packet(input_packet(us, "d" * 64), constitution(), "2026-08-21", CONTRACT)

    def test_unratified_or_contradictory_constitution_fails_closed(self):
        unratified = constitution()
        unratified["status"] = "not_ratified"
        with self.assertRaisesRegex(MODULE.PlannedLossBudgetError, "CONSTITUTION_NOT_RATIFIED"):
            MODULE.build_packet(input_packet(), unratified, "2026-08-21", CONTRACT)

        contradictory = constitution()
        contradictory["B2_cash_floor_pct"] = 0
        contradictory["B5_stop_loss_pct"] = 20
        contradictory["B6_portfolio_max_loss_pct"] = 5
        with self.assertRaisesRegex(MODULE.PlannedLossBudgetError, "CONSTITUTION_CONTRADICTORY"):
            MODULE.build_packet(input_packet(), contradictory, "2026-08-21", CONTRACT)

        future = constitution()
        future["ratified_at"] = "2026-08-22T00:00:00Z"
        with self.assertRaisesRegex(
            MODULE.PlannedLossBudgetError,
            "CONSTITUTION_RATIFIED_AFTER_AS_OF",
        ):
            MODULE.build_packet(input_packet(), future, "2026-08-21", CONTRACT)

    def test_position_lineage_input_authority_and_packet_tamper_fail(self):
        bad = input_packet()["positions"]
        bad[0]["position_sizing_packet_sha256"] = "bad"
        with self.assertRaisesRegex(MODULE.PlannedLossBudgetError, "POSITION_SIZING_PACKET_SHA_INVALID"):
            MODULE.build_packet(input_packet(bad), constitution(), "2026-08-21", CONTRACT)

        expanded = input_packet()
        expanded["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(MODULE.PlannedLossBudgetError, "INPUT_IDENTITY_INVALID"):
            MODULE.build_packet(expanded, constitution(), "2026-08-21", CONTRACT)

        tampered = input_packet()
        tampered["portfolio_snapshot_sha256"] = "9" * 64
        with self.assertRaisesRegex(MODULE.PlannedLossBudgetError, "INPUT_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(tampered, constitution(), "2026-08-21", CONTRACT)

    def test_output_is_deterministic_under_position_permutation(self):
        source = input_packet()
        first = MODULE.build_packet(source, constitution(), "2026-08-21", CONTRACT)
        source["positions"].reverse()
        self.assertEqual(
            MODULE.canonical_json(first),
            MODULE.canonical_json(MODULE.build_packet(source, constitution(), "2026-08-21", CONTRACT)),
        )
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_self_rehashed_output_and_embedded_constitution_tamper_fail_closed(self):
        packet = MODULE.build_packet(
            input_packet(), constitution(), "2026-08-21", CONTRACT
        )
        packet["summary"]["breach_count"] = 99
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.PlannedLossBudgetError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

        packet = MODULE.build_packet(
            input_packet(), constitution(), "2026-08-21", CONTRACT
        )
        packet["source_packets"]["CONSTITUTION"]["status"] = "not_ratified"
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.PlannedLossBudgetError, "CONSTITUTION_NOT_RATIFIED"
        ):
            MODULE.validate_packet(packet, CONTRACT)

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
            tmp = Path(tmp)
            source = write_json(tmp / "input.json", input_packet())
            ratified = write_json(tmp / "constitution.json", constitution())
            output = tmp / "nested" / "packet.json"
            self.assertEqual(MODULE.run(source, ratified, "2026-08-21", output), 0)
            serialized = json.loads(output.read_text())
            self.assertEqual(serialized["status"], "WITHIN_RATIFIED_LOSS_BUDGET")
            self.assertEqual(MODULE.validate_packet(serialized, CONTRACT), serialized)
            forbidden = ROOT / "data" / "planned_loss_budget_test.json"
            self.assertEqual(MODULE.run(source, ratified, "2026-08-21", forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
