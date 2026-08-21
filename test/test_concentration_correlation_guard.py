#!/usr/bin/env python3
"""P7-03 concentration/correlation guard regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "concentration_correlation_guard.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("concentration_correlation_guard", SOURCE)
CONTRACT = MODULE.load_contract()


def position(asset_id, market, weight, themes):
    return {
        "asset_id": asset_id,
        "market": market,
        "portfolio_weight": weight,
        "position_record_sha256": (asset_id[0].lower() if asset_id[0].isalpha() else "a") * 64,
        "asset_identity_sha256": "b" * 64,
        "bucket_id": "CORE_LONG",
        "theme_allocations": [
            {
                "theme_id": theme_id,
                "fraction": fraction,
                "membership_evidence_sha256": marker * 64,
            }
            for theme_id, fraction, marker in themes
        ],
    }


def pair(left, right, correlation):
    return {
        "asset_a": left,
        "asset_b": right,
        "correlation": correlation,
        "as_of_date": "2026-08-21",
        "available_at_utc": "2026-08-21T00:10:00Z",
        "observation_sha256": "e" * 64,
    }


def policy(**limit_overrides):
    limits = {
        "max_single_position_weight": 0.30,
        "max_theme_exposure": 0.45,
        "max_market_exposure": 0.60,
        "max_correlated_cluster_exposure": 0.50,
    }
    limits.update(limit_overrides)
    value = {
        "schema_version": "concentration_correlation_policy/1",
        "contract_version": "concentration_correlation_guard/1",
        "policy_id": "TEST-CONCENTRATION-2026",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "limits": limits,
        "correlation": {
            "method": "PEARSON",
            "return_basis": "DAILY_CLOSE_TO_CLOSE_RETURN",
            "lookback_observations": 60,
            "threshold": 0.75,
            "pair_coverage_required": 1.0,
        },
        "policy_basis_ref": "test://policy/concentration",
        "policy_basis_sha256": "f" * 64,
        "authority": copy.deepcopy(CONTRACT["policy_authority"]),
    }
    unsigned = copy.deepcopy(value)
    value["packet_sha256"] = MODULE.payload_sha256(unsigned)
    return value


def input_packet(positions=None, correlations=None):
    if positions is None:
        positions = [
            position("AAA", "US", 0.25, [("AI_STACK", 1.0, "1")]),
            position("BBB", "US", 0.20, [("AI_STACK", 0.5, "2"), ("POWER", 0.5, "3")]),
            position("CCC", "KOREA", 0.10, [("POWER", 1.0, "4")]),
        ]
    if correlations is None:
        correlations = [pair("AAA", "BBB", 0.80), pair("AAA", "CCC", 0.10), pair("BBB", "CCC", -0.20)]
    value = {
        "schema_version": "concentration_correlation_input/1",
        "contract_version": "concentration_correlation_guard/1",
        "snapshot_id": "TEST-PORTFOLIO-2026-08-21",
        "as_of_date": "2026-08-21",
        "generated_at_utc": "2026-08-21T00:20:00Z",
        "portfolio_snapshot_sha256": "5" * 64,
        "bucket_membership_packet_sha256": "6" * 64,
        "theme_taxonomy_packet_sha256": "7" * 64,
        "correlation_dataset_sha256": "8" * 64,
        "positions": positions,
        "correlations": correlations,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["positions"] = sorted(normalized["positions"], key=lambda row: row["asset_id"])
    for row in normalized["positions"]:
        row["theme_allocations"] = sorted(row["theme_allocations"], key=lambda item: item["theme_id"])
    normalized["correlations"] = sorted(
        [dict(row, asset_a=min(row["asset_a"], row["asset_b"]), asset_b=max(row["asset_a"], row["asset_b"])) for row in normalized["correlations"]],
        key=lambda row: (row["asset_a"], row["asset_b"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class ConcentrationCorrelationGuardTests(unittest.TestCase):
    def test_contract_has_no_default_policy_or_action_authority(self):
        self.assertEqual(CONTRACT["approval_mode"], "EXPLICIT_CIO_RATIFIED_ONLY")
        self.assertEqual(CONTRACT["pair_coverage"], "COMPLETE_UNORDERED_ACTIVE_ASSET_PAIRS")
        self.assertTrue(CONTRACT["authority"]["concentration_correlation_evaluation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "concentration_correlation_evaluation_only":
                self.assertFalse(value, key)

    def test_four_axes_pass_and_preserve_lineage_without_action(self):
        source = input_packet()
        ratified = policy()
        packet = MODULE.build_packet(source, ratified, "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "WITHIN_RATIFIED_LIMITS")
        self.assertEqual(packet["summary"], {
            "position_count": 3,
            "market_count": 2,
            "theme_count": 2,
            "complete_pair_count": 3,
            "correlation_edge_count": 1,
            "correlated_cluster_count": 1,
            "breach_count": 0,
        })
        self.assertEqual(packet["correlated_cluster_assessments"][0]["members"], ["AAA", "BBB"])
        self.assertEqual(packet["correlated_cluster_assessments"][0]["exposure"], 0.45)
        self.assertIsNone(packet["recommended_action"])
        self.assertIsNone(packet["target_weights"])
        self.assertIsNone(packet["position_size"])
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(packet["lineage"]["input_packet_sha256"], source["packet_sha256"])
        self.assertEqual(packet["lineage"]["policy_packet_sha256"], ratified["packet_sha256"])

    def test_position_theme_market_and_cluster_breaches_are_independent(self):
        packet = MODULE.build_packet(
            input_packet(),
            policy(
                max_single_position_weight=0.24,
                max_theme_exposure=0.34,
                max_market_exposure=0.44,
                max_correlated_cluster_exposure=0.44,
            ),
            "2026-08-21",
            CONTRACT,
        )
        self.assertEqual(packet["status"], "LIMIT_BREACH")
        self.assertEqual(packet["breaches"], [
            {"scope_type": "POSITION", "scope_id": "AAA"},
            {"scope_type": "MARKET", "scope_id": "US"},
            {"scope_type": "THEME", "scope_id": "AI_STACK"},
            {"scope_type": "CORRELATED_CLUSTER", "scope_id": "AAA+BBB"},
        ])
        self.assertEqual(packet["summary"]["breach_count"], 4)

    def test_negative_correlation_does_not_create_same_risk_cluster(self):
        value = input_packet()
        value["correlations"][0]["correlation"] = -0.90
        unsigned = copy.deepcopy(value)
        unsigned.pop("packet_sha256")
        value["packet_sha256"] = MODULE.payload_sha256(unsigned)
        packet = MODULE.build_packet(value, policy(), "2026-08-21", CONTRACT)
        self.assertEqual(packet["summary"]["correlation_edge_count"], 0)
        self.assertEqual(packet["correlated_cluster_assessments"], [])

    def test_missing_duplicate_or_unknown_correlation_pair_fails_closed(self):
        missing = input_packet(correlations=[pair("AAA", "BBB", 0.8), pair("AAA", "CCC", 0.1)])
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "CORRELATION_PAIR_COVERAGE_INCOMPLETE"):
            MODULE.build_packet(missing, policy(), "2026-08-21", CONTRACT)

        duplicate = input_packet(correlations=[pair("AAA", "BBB", 0.8), pair("BBB", "AAA", 0.8), pair("AAA", "CCC", 0.1), pair("BBB", "CCC", 0.2)])
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "CORRELATION_PAIR_DUPLICATE"):
            MODULE.build_packet(duplicate, policy(), "2026-08-21", CONTRACT)

    def test_theme_fraction_and_membership_evidence_fail_closed(self):
        positions = input_packet()["positions"]
        positions[1]["theme_allocations"][0]["fraction"] = 0.4
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "THEME_FRACTIONS_MUST_SUM_TO_ONE"):
            MODULE.build_packet(input_packet(positions=positions), policy(), "2026-08-21", CONTRACT)

        positions = input_packet()["positions"]
        positions[0]["theme_allocations"][0]["membership_evidence_sha256"] = "bad"
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "THEME_MEMBERSHIP_SHA_INVALID"):
            MODULE.build_packet(input_packet(positions=positions), policy(), "2026-08-21", CONTRACT)

    def test_unratified_policy_authority_expansion_and_tamper_fail(self):
        unratified = policy()
        unratified["status"] = "DRAFT"
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), unratified, "2026-08-21", CONTRACT)

        expanded = policy()
        expanded["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), expanded, "2026-08-21", CONTRACT)

        tampered = policy()
        tampered["limits"]["max_market_exposure"] = 0.99
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "POLICY_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(input_packet(), tampered, "2026-08-21", CONTRACT)

    def test_input_authority_and_packet_tamper_fail(self):
        expanded = input_packet()
        expanded["authority"]["position_sizing_authorized"] = True
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "INPUT_IDENTITY_INVALID"):
            MODULE.build_packet(expanded, policy(), "2026-08-21", CONTRACT)

        tampered = input_packet()
        tampered["positions"][0]["portfolio_weight"] = 0.99
        with self.assertRaisesRegex(MODULE.ConcentrationCorrelationError, "INPUT_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(tampered, policy(), "2026-08-21", CONTRACT)

    def test_output_is_deterministic_under_input_permutation(self):
        source = input_packet()
        first = MODULE.build_packet(source, policy(), "2026-08-21", CONTRACT)
        permuted = copy.deepcopy(source)
        permuted["positions"].reverse()
        permuted["correlations"].reverse()
        self.assertEqual(
            MODULE.canonical_json(first),
            MODULE.canonical_json(MODULE.build_packet(permuted, policy(), "2026-08-21", CONTRACT)),
        )
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

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
            self.assertEqual(json.loads(output.read_text())["status"], "WITHIN_RATIFIED_LIMITS")
            forbidden = ROOT / "data" / "concentration_guard_test.json"
            self.assertEqual(MODULE.run(source, ratified, "2026-08-21", forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
