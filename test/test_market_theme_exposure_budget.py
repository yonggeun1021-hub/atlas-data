#!/usr/bin/env python3
"""P7-04 market/theme exposure budget regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "market_theme_exposure_budget.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("market_theme_exposure_budget", SOURCE)
CONTRACT = MODULE.load_contract()


def budget_record(scope_type, market, scope_id, maximum, marker):
    return {
        "budget_id": f"BUDGET_{scope_type}_{market}_{scope_id}",
        "scope_type": scope_type,
        "market": market,
        "scope_id": scope_id,
        "regime": "UNKNOWN",
        "max_exposure": maximum,
        "unit": "NAV_FRACTION",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "budget_basis_ref": f"test://budget/{market}/{scope_id}",
        "budget_basis_sha256": marker * 64,
    }


def policy(records=None):
    if records is None:
        records = [
            budget_record("MARKET", "US", "US", 0.60, "1"),
            budget_record("MARKET", "KOREA", "KOREA", 0.40, "2"),
            budget_record("MARKET", "CRYPTO", "CRYPTO", 0.15, "3"),
            budget_record("THEME", "US", "AI_STACK", 0.35, "4"),
            budget_record("THEME", "KOREA", "POWER", 0.25, "5"),
        ]
    value = {
        "schema_version": "market_theme_budget_policy/1",
        "contract_version": "market_theme_exposure_budget/1",
        "policy_set_id": "TEST-MARKET-THEME-BUDGET-2026",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "records": records,
        "policy_basis_ref": "test://policy/market-theme",
        "policy_basis_sha256": "a" * 64,
        "authority": copy.deepcopy(CONTRACT["policy_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["records"] = sorted(
        normalized["records"],
        key=lambda row: (
            row["scope_type"], row["market"], row["scope_id"], row["regime"],
            row["valid_from"], row["budget_id"],
        ),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def regime(market, marker):
    return {
        "market": market,
        "regime": "UNKNOWN",
        "direction": "UNKNOWN",
        "confidence": None,
        "contract_version": "regime_output/v1",
        "contract_mode": "PRE_SCORE_UNKNOWN_ONLY",
        "regime_packet_sha256": marker * 64,
    }


def exposure(scope_type, market, scope_id, value, marker):
    return {
        "scope_type": scope_type,
        "market": market,
        "scope_id": scope_id,
        "exposure": value,
        "exposure_source_sha256": marker * 64,
        "rotation_packet_sha256": None if scope_type == "MARKET" else "f" * 64,
    }


def input_packet(exposures=None):
    if exposures is None:
        exposures = [
            exposure("MARKET", "US", "US", 0.50, "1"),
            exposure("MARKET", "KOREA", "KOREA", 0.30, "2"),
            exposure("MARKET", "CRYPTO", "CRYPTO", 0.10, "3"),
            exposure("THEME", "US", "AI_STACK", 0.30, "4"),
            exposure("THEME", "KOREA", "POWER", 0.20, "5"),
        ]
    value = {
        "schema_version": "market_theme_exposure_input/1",
        "contract_version": "market_theme_exposure_budget/1",
        "snapshot_id": "TEST-EXPOSURE-2026-08-21",
        "as_of_date": "2026-08-21",
        "generated_at_utc": "2026-08-21T00:30:00Z",
        "portfolio_snapshot_sha256": "6" * 64,
        "concentration_guard_packet_sha256": "7" * 64,
        "theme_taxonomy_packet_sha256": "8" * 64,
        "regimes": [regime("US", "b"), regime("KOREA", "c"), regime("CRYPTO", "d")],
        "exposures": exposures,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["regimes"] = sorted(normalized["regimes"], key=lambda row: row["market"])
    normalized["exposures"] = sorted(
        normalized["exposures"],
        key=lambda row: (row["scope_type"], row["market"], row["scope_id"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class MarketThemeExposureBudgetTests(unittest.TestCase):
    def test_contract_is_unknown_only_and_has_no_default_or_action_authority(self):
        self.assertEqual(CONTRACT["runtime_authorized_regimes"], ["UNKNOWN"])
        self.assertEqual(CONTRACT["repository_default_status"], "BLOCKED_UNTIL_EXTERNAL_POLICY_RATIFIED")
        self.assertTrue(CONTRACT["authority"]["market_theme_budget_evaluation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "market_theme_budget_evaluation_only":
                self.assertFalse(value, key)

    def test_market_and_theme_budgets_pass_without_rebalance_or_order(self):
        source = input_packet()
        ratified = policy()
        packet = MODULE.build_packet(source, ratified, "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "WITHIN_RATIFIED_BUDGET")
        self.assertEqual(packet["summary"], {
            "assessment_count": 5,
            "market_assessment_count": 3,
            "theme_assessment_count": 2,
            "breach_count": 0,
            "unused_active_budget_ids": [],
        })
        self.assertTrue(all(row["regime"] == "UNKNOWN" for row in packet["assessments"]))
        self.assertIsNone(packet["recommended_rebalance"])
        self.assertIsNone(packet["target_exposures"])
        self.assertIsNone(packet["position_sizes"])
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(packet["lineage"]["input_packet_sha256"], source["packet_sha256"])
        self.assertEqual(packet["lineage"]["policy_packet_sha256"], ratified["packet_sha256"])

    def test_market_and_theme_breaches_are_independent(self):
        rows = input_packet()["exposures"]
        for row in rows:
            if row["scope_type"] == "MARKET" and row["market"] == "US":
                row["exposure"] = 0.61
            if row["scope_type"] == "THEME" and row["scope_id"] == "POWER":
                row["exposure"] = 0.26
        packet = MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "LIMIT_BREACH")
        self.assertEqual(packet["breaches"], [
            {"scope_type": "MARKET", "market": "US", "scope_id": "US"},
            {"scope_type": "THEME", "market": "KOREA", "scope_id": "POWER"},
        ])

    def test_missing_budget_coverage_fails_closed(self):
        records = policy()["records"][:-1]
        with self.assertRaisesRegex(
            MODULE.MarketThemeExposureBudgetError,
            "ACTIVE_BUDGET_COVERAGE_MISSING",
        ):
            MODULE.build_packet(input_packet(), policy(records), "2026-08-21", CONTRACT)

    def test_effective_history_allows_one_active_record_and_rejects_overlap(self):
        records = policy()["records"]
        current = next(row for row in records if row["budget_id"] == "BUDGET_MARKET_US_US")
        current["valid_from"] = "2026-08-21"
        prior = copy.deepcopy(current)
        prior["valid_from"] = "2026-08-20"
        prior["valid_to"] = "2026-08-21"
        prior["max_exposure"] = 0.55
        packet = MODULE.build_packet(
            input_packet(), policy(records + [prior]), "2026-08-21", CONTRACT
        )
        us = next(
            row for row in packet["assessments"]
            if row["scope_type"] == "MARKET" and row["market"] == "US"
        )
        self.assertEqual(us["max_exposure"], 0.60)

        prior["valid_to"] = "2026-08-22"
        with self.assertRaisesRegex(
            MODULE.MarketThemeExposureBudgetError,
            "BUDGET_INTERVAL_OVERLAP",
        ):
            MODULE.build_packet(
                input_packet(), policy(records + [prior]), "2026-08-21", CONTRACT
            )

    def test_scored_or_mislabeled_regime_cannot_enter_unknown_only_runtime(self):
        value = input_packet()
        value["regimes"][0]["regime"] = "RISK_ON"
        with self.assertRaisesRegex(
            MODULE.MarketThemeExposureBudgetError,
            "REGIME_RUNTIME_IDENTITY_INVALID",
        ):
            MODULE.build_packet(value, policy(), "2026-08-21", CONTRACT)

        ratified = policy()
        ratified["records"][0]["regime"] = "RISK_OFF"
        with self.assertRaisesRegex(
            MODULE.MarketThemeExposureBudgetError,
            "REGIME_NOT_RUNTIME_AUTHORIZED",
        ):
            MODULE.build_packet(input_packet(), ratified, "2026-08-21", CONTRACT)

    def test_theme_requires_rotation_lineage_and_market_forbids_it(self):
        rows = input_packet()["exposures"]
        rows[-1]["rotation_packet_sha256"] = None
        with self.assertRaisesRegex(
            MODULE.MarketThemeExposureBudgetError,
            "THEME_ROTATION_LINEAGE_REQUIRED",
        ):
            MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)

        rows = input_packet()["exposures"]
        rows[0]["rotation_packet_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            MODULE.MarketThemeExposureBudgetError,
            "MARKET_EXPOSURE_IDENTITY_INVALID",
        ):
            MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)

    def test_policy_approval_authority_and_hash_tamper_fail(self):
        unratified = policy()
        unratified["status"] = "DRAFT"
        with self.assertRaisesRegex(MODULE.MarketThemeExposureBudgetError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), unratified, "2026-08-21", CONTRACT)

        expanded = policy()
        expanded["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(MODULE.MarketThemeExposureBudgetError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), expanded, "2026-08-21", CONTRACT)

        tampered = policy()
        tampered["records"][0]["max_exposure"] = 9
        with self.assertRaisesRegex(MODULE.MarketThemeExposureBudgetError, "POLICY_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(input_packet(), tampered, "2026-08-21", CONTRACT)

    def test_input_authority_and_packet_tamper_fail(self):
        expanded = input_packet()
        expanded["authority"]["position_sizing_authorized"] = True
        with self.assertRaisesRegex(MODULE.MarketThemeExposureBudgetError, "INPUT_IDENTITY_INVALID"):
            MODULE.build_packet(expanded, policy(), "2026-08-21", CONTRACT)

        tampered = input_packet()
        tampered["exposures"][0]["exposure"] = 9
        with self.assertRaisesRegex(MODULE.MarketThemeExposureBudgetError, "INPUT_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(tampered, policy(), "2026-08-21", CONTRACT)

    def test_output_is_deterministic_under_input_and_policy_permutation(self):
        source = input_packet()
        ratified = policy()
        first = MODULE.build_packet(source, ratified, "2026-08-21", CONTRACT)
        source["regimes"].reverse()
        source["exposures"].reverse()
        ratified["records"].reverse()
        self.assertEqual(
            MODULE.canonical_json(first),
            MODULE.canonical_json(MODULE.build_packet(source, ratified, "2026-08-21", CONTRACT)),
        )
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_self_rehashed_output_semantic_tamper_fails_closed(self):
        packet = MODULE.build_packet(input_packet(), policy(), "2026-08-21", CONTRACT)
        packet["assessments"][0]["result"] = "BREACH"
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.MarketThemeExposureBudgetError,
            "OUTPUT_ASSESSMENT_RESULT_MISMATCH",
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
            ratified = write_json(tmp / "policy.json", policy())
            output = tmp / "nested" / "packet.json"
            self.assertEqual(MODULE.run(source, ratified, "2026-08-21", output), 0)
            self.assertEqual(json.loads(output.read_text())["status"], "WITHIN_RATIFIED_BUDGET")
            forbidden = ROOT / "data" / "market_theme_budget_test.json"
            self.assertEqual(MODULE.run(source, ratified, "2026-08-21", forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
