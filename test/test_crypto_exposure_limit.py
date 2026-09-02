#!/usr/bin/env python3
"""P7-05 Crypto separate exposure limit regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "crypto_exposure_limit.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("crypto_exposure_limit", SOURCE)
CONTRACT = MODULE.load_contract()


def policy(**overrides):
    limits = copy.deepcopy(CONTRACT["ratified_paper_limits"])
    limits.update(overrides)
    value = {
        "schema_version": "crypto_exposure_policy/1",
        "contract_version": "crypto_exposure_limit/1",
        "policy_id": "P7-05-PAPER-RISK-V1",
        "status": "RATIFIED_PAPER_ONLY",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "active_limits": limits,
        "unresolved_limits": copy.deepcopy(CONTRACT["unresolved_limits"]),
        "volatility_requirement": {
            "unit": "ANNUALIZED_FRACTION",
            "transform_version": "btc_risk/v1",
            "estimator": "sqrt_mean_squared_simple_returns",
            "lookback_returns": 30,
            "annualization_days": 365,
        },
        "policy_basis_ref": f"notion-page:{CONTRACT['canonical_wbs']['page_id']}",
        "policy_basis_sha256": CONTRACT["canonical_wbs"]["row_sha256"],
        "authority": copy.deepcopy(CONTRACT["policy_authority"]),
    }
    unsigned = copy.deepcopy(value)
    value["packet_sha256"] = MODULE.payload_sha256(unsigned)
    return value


def position(position_id, asset_id, weight, planned_loss, marker):
    return {
        "position_id": position_id,
        "asset_id": asset_id,
        "portfolio_weight": weight,
        "planned_loss_nav_fraction": planned_loss,
        "position_record_sha256": marker * 64,
        "asset_identity_sha256": "b" * 64,
        "crypto_universe_membership_sha256": "c" * 64,
    }


def input_packet(positions=None, volatility=0.70):
    if positions is None:
        positions = [
            position("POS-BTC-1", "BTC", 0.02, 0.0025, "1"),
            position("POS-ETH-1", "ETH", 0.015, 0.001, "2"),
        ]
    value = {
        "schema_version": "crypto_exposure_input/1",
        "contract_version": "crypto_exposure_limit/1",
        "snapshot_id": "TEST-CRYPTO-EXPOSURE-2026-08-21",
        "as_of_date": "2026-08-21",
        "generated_at_utc": "2026-08-21T00:30:00Z",
        "portfolio_snapshot_sha256": "d" * 64,
        "crypto_universe_packet_sha256": "e" * 64,
        "market_theme_budget_packet_sha256": "f" * 64,
        "market_theme_budget_status": "WITHIN_RATIFIED_BUDGET",
        "positions": positions,
        "volatility": {
            "status": "DEFINED",
            "as_of_date": "2026-08-21",
            "available_at_utc": "2026-08-21T00:20:00Z",
            "annualized_fraction": volatility,
            "unit": "ANNUALIZED_FRACTION",
            "transform_version": "btc_risk/v1",
            "estimator": "sqrt_mean_squared_simple_returns",
            "lookback_returns": 30,
            "annualization_days": 365,
            "source_snapshot_sha256": "3" * 64,
            "observation_sha256": "4" * 64,
        },
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["positions"] = sorted(
        normalized["positions"], key=lambda row: (row["asset_id"], row["position_id"])
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class CryptoExposureLimitTests(unittest.TestCase):
    def test_contract_binds_exact_wbs_row_and_four_paper_limits(self):
        self.assertEqual(CONTRACT["output_schema_version"], "crypto_exposure_packet/2")
        self.assertEqual(CONTRACT["canonical_wbs"], {
            "page_id": "3bf9f2d7-3c84-816c-ac6a-e59938e2d99d",
            "order": 705,
            "work_item": "P7-05",
            "title": "Crypto separate exposure limit",
            "status": "🔵 검증대기",
            "snapshot_sha256": (
                "09cab6d8c7065a5952fbc480195117ac92e3331164d94a5179b6fb8c0763744f"
            ),
            "row_sha256": (
                "0881d4d107b79b9580a39e2b64f2a22b0cc1c9cb443d0a5e65eea7217d29d5bd"
            ),
        })
        self.assertEqual(CONTRACT["ratified_paper_limits"], {
            "max_per_trade_planned_loss_nav_fraction": "0.0025",
            "max_total_crypto_exposure_nav_fraction": "0.05",
            "max_single_asset_exposure_nav_fraction": "0.02",
            "max_concurrent_positions": 3,
        })
        self.assertEqual(CONTRACT["unresolved_limits"], {
            "max_total_planned_loss": {"value": None, "state": "UNKNOWN"},
            "max_annualized_realized_volatility": {
                "value": None,
                "state": "UNKNOWN",
            },
        })
        self.assertFalse(CONTRACT["authority"]["repository_default_policy_authorized"])
        for key, value in CONTRACT["authority"].items():
            if key != "crypto_exposure_limit_evaluation_only":
                self.assertFalse(value, key)

    def test_four_ratified_axes_pass_at_boundary_without_action_authority(self):
        packet = MODULE.build_packet(input_packet(), policy(), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "WITHIN_RATIFIED_LIMITS")
        self.assertEqual(packet["summary"], {
            "crypto_position_count": 2,
            "total_crypto_exposure": 0.035,
            "total_planned_loss": 0.0035,
            "upstream_market_theme_budget_status": "WITHIN_RATIFIED_BUDGET",
            "breach_count": 0,
        })
        self.assertEqual(
            [row["result"] for row in packet["assessments"][-2:]],
            ["NOT_COMPUTABLE", "NOT_COMPUTABLE"],
        )
        for row in packet["assessments"][-2:]:
            self.assertIsNone(row["observed"])
            self.assertIsNone(row["maximum"])
            self.assertEqual(row["reason"], "UNRATIFIED_LIMIT")
        self.assertIsNone(packet["recommended_action"])
        self.assertIsNone(packet["target_crypto_exposure"])
        self.assertIsNone(packet["position_sizes"])
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(packet["source_packets"]["POLICY"], policy())

    def test_each_ratified_axis_breaches_independently(self):
        rows = [
            position("POS-BTC-1", "BTC", 0.015, 0.003, "1"),
            position("POS-BTC-2", "BTC", 0.010, 0.001, "2"),
            position("POS-ETH-1", "ETH", 0.015, 0.001, "3"),
            position("POS-SOL-1", "SOL", 0.015, 0.001, "4"),
        ]
        packet = MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "LIMIT_BREACH")
        self.assertEqual(packet["summary"]["breach_count"], 4)
        self.assertEqual({row["metric"] for row in packet["breaches"]}, {
            "TOTAL_CRYPTO_EXPOSURE",
            "SINGLE_ASSET_CRYPTO_EXPOSURE",
            "PER_TRADE_PLANNED_LOSS",
            "CONCURRENT_POSITIONS",
        })

    def test_same_asset_positions_are_aggregated_and_position_ids_are_unique(self):
        rows = [
            position("POS-BTC-1", "BTC", 0.012, 0.001, "1"),
            position("POS-BTC-2", "BTC", 0.012, 0.001, "2"),
        ]
        packet = MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)
        asset_row = next(
            row for row in packet["assessments"]
            if row["metric"] == "SINGLE_ASSET_CRYPTO_EXPOSURE"
        )
        self.assertEqual(asset_row["observed"], 0.024)
        self.assertEqual(asset_row["result"], "BREACH")

        rows[1]["position_id"] = "POS-BTC-1"
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POSITION_ID_DUPLICATE"):
            MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)

    def test_empty_positions_are_explicit_zero_not_missing(self):
        packet = MODULE.build_packet(input_packet([]), policy(), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "WITHIN_RATIFIED_LIMITS")
        self.assertEqual(packet["summary"]["crypto_position_count"], 0)
        self.assertEqual(packet["summary"]["total_crypto_exposure"], 0.0)
        self.assertEqual(packet["summary"]["total_planned_loss"], 0.0)
        concurrency = next(
            row for row in packet["assessments"] if row["metric"] == "CONCURRENT_POSITIONS"
        )
        self.assertEqual(concurrency["observed"], 0)

    def test_policy_values_types_and_unresolved_injection_fail_closed(self):
        invalid_values = [
            {"max_concurrent_positions": 3.0},
            {"max_concurrent_positions": True},
            {"max_total_crypto_exposure_nav_fraction": 0.05},
            {"max_total_crypto_exposure_nav_fraction": "0.050"},
        ]
        for override in invalid_values:
            with self.subTest(override=override):
                with self.assertRaises(MODULE.CryptoExposureLimitError):
                    MODULE.build_packet(input_packet(), policy(**override), "2026-08-21", CONTRACT)

        injected = policy()
        injected["unresolved_limits"]["max_total_planned_loss"]["value"] = "0.01"
        unsigned = copy.deepcopy(injected)
        unsigned.pop("packet_sha256")
        injected["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError, "POLICY_UNRESOLVED_LIMITS_MISMATCH"
        ):
            MODULE.build_packet(input_packet(), injected, "2026-08-21", CONTRACT)

        aliased_contract = copy.deepcopy(CONTRACT)
        aliased_contract["canonical_wbs"]["order"] = True
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError, "CONTRACT_FIELD_MISMATCH:canonical_wbs"
        ):
            MODULE.build_packet(input_packet(), policy(), "2026-08-21", aliased_contract)

    def test_policy_basis_status_authority_and_hash_tamper_fail_closed(self):
        unratified = policy()
        unratified["status"] = "RATIFIED"
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), unratified, "2026-08-21", CONTRACT)

        expanded = policy()
        expanded["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), expanded, "2026-08-21", CONTRACT)

        aliased = policy()
        aliased["authority"]["order_authorized"] = 0
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), aliased, "2026-08-21", CONTRACT)

        wrong_basis = policy()
        wrong_basis["policy_basis_sha256"] = "a" * 64
        unsigned = copy.deepcopy(wrong_basis)
        unsigned.pop("packet_sha256")
        wrong_basis["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError, "POLICY_BASIS_NOT_CANONICAL_WBS_ROW"
        ):
            MODULE.build_packet(input_packet(), wrong_basis, "2026-08-21", CONTRACT)

        tampered = policy()
        tampered["active_limits"]["max_total_crypto_exposure_nav_fraction"] = "0.99"
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_LIMITS_NOT_CANONICAL"):
            MODULE.build_packet(input_packet(), tampered, "2026-08-21", CONTRACT)

    def test_upstream_market_theme_breach_is_preserved_without_action(self):
        source = input_packet()
        source["market_theme_budget_status"] = "LIMIT_BREACH"
        unsigned = copy.deepcopy(source)
        unsigned.pop("packet_sha256")
        source["packet_sha256"] = MODULE.payload_sha256(unsigned)
        packet = MODULE.build_packet(source, policy(), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "LIMIT_BREACH")
        self.assertEqual(packet["breaches"], [{
            "metric": "UPSTREAM_MARKET_THEME_BUDGET",
            "subject_id": "CRYPTO",
        }])
        self.assertIsNone(packet["recommended_action"])
        self.assertEqual(packet["order_intents"], [])

    def test_planned_loss_cannot_exceed_position_weight(self):
        rows = [position("POS-BTC-1", "BTC", 0.002, 0.003, "1")]
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError, "PLANNED_LOSS_EXCEEDS_POSITION"
        ):
            MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)

    def test_volatility_identity_staleness_and_future_availability_fail_closed(self):
        wrong = input_packet()
        wrong["volatility"]["lookback_returns"] = 20
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "VOLATILITY_IDENTITY_INVALID"):
            MODULE.build_packet(wrong, policy(), "2026-08-21", CONTRACT)

        stale = input_packet()
        stale["volatility"]["as_of_date"] = "2026-08-20"
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "VOLATILITY_IDENTITY_INVALID"):
            MODULE.build_packet(stale, policy(), "2026-08-21", CONTRACT)

        future = input_packet()
        future["volatility"]["available_at_utc"] = "2026-08-21T00:40:00Z"
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "VOLATILITY_IDENTITY_INVALID"):
            MODULE.build_packet(future, policy(), "2026-08-21", CONTRACT)

    def test_input_lineage_authority_and_hash_tamper_fail_closed(self):
        bad_lineage = input_packet()
        bad_lineage["positions"][0]["crypto_universe_membership_sha256"] = "bad"
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError, "CRYPTO_UNIVERSE_MEMBERSHIP_SHA_INVALID"
        ):
            MODULE.build_packet(bad_lineage, policy(), "2026-08-21", CONTRACT)

        expanded = input_packet()
        expanded["authority"]["position_sizing_authorized"] = True
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "INPUT_IDENTITY_INVALID"):
            MODULE.build_packet(expanded, policy(), "2026-08-21", CONTRACT)

        aliased = input_packet()
        aliased["authority"]["crypto_exposure_measurement_authorized"] = 1
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "INPUT_IDENTITY_INVALID"):
            MODULE.build_packet(aliased, policy(), "2026-08-21", CONTRACT)

        tampered = input_packet()
        tampered["positions"][0]["portfolio_weight"] = 0.99
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "INPUT_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(tampered, policy(), "2026-08-21", CONTRACT)

    def test_output_is_deterministic_under_position_permutation(self):
        source = input_packet()
        first = MODULE.build_packet(source, policy(), "2026-08-21", CONTRACT)
        source["positions"].reverse()
        self.assertEqual(
            MODULE.canonical_json(first),
            MODULE.canonical_json(MODULE.build_packet(source, policy(), "2026-08-21", CONTRACT)),
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
            MODULE.CryptoExposureLimitError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_embedded_policy_is_revalidated_at_consumption(self):
        original = MODULE.build_packet(input_packet(), policy(), "2026-08-21", CONTRACT)

        draft = copy.deepcopy(original)
        draft["source_packets"]["POLICY"]["status"] = "DRAFT"
        draft["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in draft.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_IDENTITY_INVALID"):
            MODULE.validate_packet(draft, CONTRACT)

        stale_digest = copy.deepcopy(original)
        stale_digest["source_packets"]["POLICY"]["active_limits"][
            "max_single_asset_exposure_nav_fraction"
        ] = "0.99"
        stale_digest["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in stale_digest.items() if key != "packet_sha256"
        })
        with self.assertRaises(MODULE.CryptoExposureLimitError):
            MODULE.validate_packet(stale_digest, CONTRACT)

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
            forbidden = ROOT / "data" / "crypto_exposure_limit_test.json"
            self.assertEqual(MODULE.run(source, ratified, "2026-08-21", forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
