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
    limits = {
        "max_total_crypto_exposure": 0.15,
        "max_single_crypto_exposure": 0.10,
        "max_total_planned_loss": 0.03,
        "max_single_planned_loss": 0.02,
        "max_annualized_realized_volatility": 0.90,
    }
    limits.update(overrides)
    value = {
        "schema_version": "crypto_exposure_policy/1",
        "contract_version": "crypto_exposure_limit/1",
        "policy_id": "TEST-CRYPTO-LIMIT-2026",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "limits": limits,
        "volatility_requirement": {
            "unit": "ANNUALIZED_FRACTION",
            "transform_version": "btc_risk/v1",
            "estimator": "sqrt_mean_squared_simple_returns",
            "lookback_returns": 30,
            "annualization_days": 365,
        },
        "policy_basis_ref": "test://policy/crypto-limit",
        "policy_basis_sha256": "a" * 64,
        "authority": copy.deepcopy(CONTRACT["policy_authority"]),
    }
    unsigned = copy.deepcopy(value)
    value["packet_sha256"] = MODULE.payload_sha256(unsigned)
    return value


def position(asset_id, weight, planned_loss, marker):
    return {
        "asset_id": asset_id,
        "portfolio_weight": weight,
        "planned_loss_nav_fraction": planned_loss,
        "position_record_sha256": marker * 64,
        "asset_identity_sha256": "b" * 64,
        "crypto_universe_membership_sha256": "c" * 64,
    }


def input_packet(positions=None, volatility=0.70):
    if positions is None:
        positions = [position("BTC", 0.08, 0.01, "1"), position("ETH", 0.05, 0.01, "2")]
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
    normalized["positions"] = sorted(normalized["positions"], key=lambda row: row["asset_id"])
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class CryptoExposureLimitTests(unittest.TestCase):
    def test_contract_has_no_default_limit_or_action_authority(self):
        self.assertEqual(CONTRACT["output_schema_version"], "crypto_exposure_packet/2")
        self.assertEqual(CONTRACT["volatility_transform_version"], "btc_risk/v1")
        self.assertEqual(CONTRACT["volatility_lookback_returns"], 30)
        self.assertTrue(CONTRACT["authority"]["crypto_exposure_limit_evaluation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "crypto_exposure_limit_evaluation_only":
                self.assertFalse(value, key)

    def test_all_five_axes_pass_without_action_sizing_or_order(self):
        source = input_packet()
        ratified = policy()
        packet = MODULE.build_packet(source, ratified, "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "WITHIN_RATIFIED_LIMITS")
        self.assertEqual(packet["summary"], {
            "crypto_position_count": 2,
            "total_crypto_exposure": 0.13,
            "total_planned_loss": 0.02,
            "upstream_market_theme_budget_status": "WITHIN_RATIFIED_BUDGET",
            "breach_count": 0,
        })
        self.assertEqual(len(packet["assessments"]), 7)
        self.assertIsNone(packet["recommended_action"])
        self.assertIsNone(packet["target_crypto_exposure"])
        self.assertIsNone(packet["position_sizes"])
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(packet["lineage"]["input_packet_sha256"], source["packet_sha256"])
        self.assertEqual(packet["lineage"]["policy_packet_sha256"], ratified["packet_sha256"])
        self.assertEqual(
            packet["source_packets"]["INPUT"]["packet_sha256"], source["packet_sha256"]
        )
        self.assertEqual(packet["source_packets"]["POLICY"], ratified)

    def test_each_limit_breaches_independently(self):
        packet = MODULE.build_packet(
            input_packet(volatility=0.91),
            policy(
                max_total_crypto_exposure=0.12,
                max_single_crypto_exposure=0.04,
                max_total_planned_loss=0.019,
                max_single_planned_loss=0.009,
            ),
            "2026-08-21",
            CONTRACT,
        )
        self.assertEqual(packet["status"], "LIMIT_BREACH")
        self.assertEqual(packet["summary"]["breach_count"], 7)
        self.assertEqual({row["metric"] for row in packet["breaches"]}, {
            "TOTAL_CRYPTO_EXPOSURE", "SINGLE_CRYPTO_EXPOSURE",
            "TOTAL_PLANNED_LOSS", "SINGLE_PLANNED_LOSS",
            "ANNUALIZED_REALIZED_VOLATILITY",
        })

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
        rows = [position("BTC", 0.05, 0.06, "1")]
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError,
            "PLANNED_LOSS_EXCEEDS_POSITION",
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

    def test_universe_lineage_and_duplicate_assets_fail_closed(self):
        rows = input_packet()["positions"]
        rows[0]["crypto_universe_membership_sha256"] = "bad"
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError,
            "CRYPTO_UNIVERSE_MEMBERSHIP_SHA_INVALID",
        ):
            MODULE.build_packet(input_packet(rows), policy(), "2026-08-21", CONTRACT)

        duplicate = [position("BTC", 0.05, 0.01, "1"), position("BTC", 0.04, 0.01, "2")]
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POSITION_ASSET_DUPLICATE"):
            MODULE.build_packet(input_packet(duplicate), policy(), "2026-08-21", CONTRACT)

    def test_policy_approval_authority_and_hash_tamper_fail(self):
        unratified = policy()
        unratified["ratified_by"] = "SYSTEM"
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), unratified, "2026-08-21", CONTRACT)

        expanded = policy()
        expanded["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_IDENTITY_INVALID"):
            MODULE.build_packet(input_packet(), expanded, "2026-08-21", CONTRACT)

        tampered = policy()
        tampered["limits"]["max_total_crypto_exposure"] = 9
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "POLICY_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(input_packet(), tampered, "2026-08-21", CONTRACT)

    def test_input_authority_and_hash_tamper_fail(self):
        expanded = input_packet()
        expanded["authority"]["position_sizing_authorized"] = True
        with self.assertRaisesRegex(MODULE.CryptoExposureLimitError, "INPUT_IDENTITY_INVALID"):
            MODULE.build_packet(expanded, policy(), "2026-08-21", CONTRACT)

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
            MODULE.CryptoExposureLimitError,
            "OUTPUT_DERIVATION_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_self_rehashed_exposure_and_limit_forgery_fails_closed(self):
        packet = MODULE.build_packet(input_packet(), policy(), "2026-08-21", CONTRACT)
        total_loss_index = next(
            index
            for index, row in enumerate(packet["assessments"])
            if row["metric"] == "TOTAL_PLANNED_LOSS"
        )
        exposure_rows = packet["assessments"][1:total_loss_index]
        self.assertEqual(exposure_rows[0]["result"], "PASS")
        exposure_rows[0]["observed"] = 0.30
        for row in [packet["assessments"][0], *exposure_rows]:
            row["maximum"] = 999
            row["result"] = "PASS"
        packet["assessments"][0]["observed"] = MODULE._rounded_sum(
            row["observed"] for row in exposure_rows
        )
        packet["summary"]["total_crypto_exposure"] = packet["assessments"][0]["observed"]
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError,
            "OUTPUT_DERIVATION_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_embedded_policy_is_revalidated_at_consumption(self):
        original = MODULE.build_packet(input_packet(), policy(), "2026-08-21", CONTRACT)

        draft = copy.deepcopy(original)
        draft["source_packets"]["POLICY"]["status"] = "DRAFT"
        draft["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in draft.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError,
            "POLICY_IDENTITY_INVALID",
        ):
            MODULE.validate_packet(draft, CONTRACT)

        stale_digest = copy.deepcopy(original)
        stale_digest["source_packets"]["POLICY"]["limits"][
            "max_single_crypto_exposure"
        ] = 999
        stale_digest["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in stale_digest.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.CryptoExposureLimitError,
            "POLICY_PACKET_SHA_MISMATCH",
        ):
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
