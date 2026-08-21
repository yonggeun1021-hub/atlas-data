#!/usr/bin/env python3
"""P6-05 RISK_OFF/STRESS != automatic inverse order regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "regime_inverse_invariant.py"
REGIME_SOURCE = ROOT / "regime" / "output_contract.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("regime_inverse_invariant", SOURCE)
REGIME = load_module("regime_inverse_fixture", REGIME_SOURCE)
CONTRACT = MODULE.load_contract()


def upstream_output():
    return REGIME.build_unknown_output("CRYPTO", "2026-08-21T01:00:00Z")


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class RegimeInverseInvariantTests(unittest.TestCase):
    def test_contract_is_exact_and_has_no_inverse_or_trading_authority(self):
        self.assertEqual(
            CONTRACT["invariant"],
            "RISK_OFF_STRESS_NEVER_IMPLIES_AUTO_INVERSE_ORDER",
        )
        self.assertEqual(CONTRACT["runtime_authorized_regimes"], ["UNKNOWN"])
        self.assertEqual(CONTRACT["derived_inverse_evaluation_status"], "NOT_EVALUATED")
        self.assertTrue(CONTRACT["authority"]["invariant_enforcement_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "invariant_enforcement_only":
                self.assertFalse(value, key)

    def test_risk_off_and_stress_directly_create_no_inverse_action(self):
        for regime in ("RISK_OFF", "STRESS"):
            with self.subTest(regime=regime):
                result = MODULE.classify_regime(regime, CONTRACT)
                self.assertIsNone(result["inverse_instrument"])
                self.assertIsNone(result["inverse_signal"])
                self.assertIsNone(result["inverse_order_intent"])
                self.assertEqual(result["inverse_evaluation_status"], "NOT_EVALUATED")
                self.assertEqual(result["invariant_status"], "ENFORCED")
                self.assertIn("REGIME_DOES_NOT_IMPLY_INVERSE_ORDER", result["reasons"])

    def test_no_regime_can_derive_any_inverse_order(self):
        for regime in CONTRACT["regime_vocabulary"]:
            with self.subTest(regime=regime):
                boundary = MODULE.classify_regime(regime, CONTRACT)
                self.assertIsNone(boundary["inverse_order_intent"])
                with self.assertRaisesRegex(
                    MODULE.RegimeInverseInvariantError,
                    "DERIVED_INVERSE_ORDER_FORBIDDEN",
                ):
                    MODULE.assert_inverse_order_not_derived(
                        regime,
                        {"instrument": "UNAUTHORIZED"},
                    )
                MODULE.assert_inverse_order_not_derived(regime, None)

    def test_current_unknown_output_is_validated_and_remains_actionless(self):
        source = upstream_output()
        result = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(result["status"], "INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED")
        self.assertEqual(result["market"], "CRYPTO")
        self.assertEqual(result["regime"], "UNKNOWN")
        self.assertIsNone(result["inverse_instrument"])
        self.assertIsNone(result["inverse_signal"])
        self.assertIsNone(result["inverse_order_intent"])
        self.assertFalse(result["authority"]["inverse_order_authorized"])
        self.assertFalse(result["authority"]["trading_authorized"])

    def test_output_preserves_upstream_identity_and_hash_lineage(self):
        source = upstream_output()
        result = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(result["generated_at"], source["generated_at"])
        self.assertEqual(result["direction"], source["direction"])
        self.assertEqual(result["confidence"], source["confidence"])
        self.assertEqual(
            result["lineage"]["upstream_regime_output_sha256"],
            MODULE.payload_sha256(source),
        )
        self.assertEqual(
            result["lineage"]["upstream_contract_version"],
            "regime_output/v1",
        )
        digest = result.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(result))

    def test_risk_off_or_stress_smuggled_into_current_runtime_is_rejected(self):
        for regime in ("RISK_OFF", "STRESS"):
            with self.subTest(regime=regime):
                source = upstream_output()
                source["regime"] = regime
                with self.assertRaisesRegex(
                    MODULE.RegimeInverseInvariantError,
                    "UPSTREAM_OUTPUT_INVALID:REGIME_NOT_AUTHORIZED",
                ):
                    MODULE.build_packet(source, CONTRACT)

    def test_upstream_authority_expansion_is_rejected(self):
        source = upstream_output()
        source["authority"]["trading_action_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.RegimeInverseInvariantError,
            "UPSTREAM_OUTPUT_INVALID:AUTHORITY_BOUNDARY_INVALID",
        ):
            MODULE.build_packet(source, CONTRACT)

    def test_upstream_derived_field_tamper_is_rejected(self):
        source = upstream_output()
        source["coverage"]["defined_count"] = 5
        with self.assertRaisesRegex(
            MODULE.RegimeInverseInvariantError,
            "UPSTREAM_OUTPUT_INVALID:DERIVED_FIELD_MISMATCH",
        ):
            MODULE.build_packet(source, CONTRACT)

    def test_contract_tamper_fails_closed(self):
        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["inverse_order_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.RegimeInverseInvariantError,
            "CONTRACT_FIELD_MISMATCH:authority",
        ):
            MODULE.build_packet(upstream_output(), contract)

    def test_output_is_deterministic_and_input_is_not_mutated(self):
        source = upstream_output()
        before = MODULE.canonical_json(source)
        first = MODULE.build_packet(source, CONTRACT)
        second = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(source), before)

    def test_self_rehashed_output_semantic_tamper_fails_closed(self):
        packet = MODULE.build_packet(upstream_output(), CONTRACT)
        packet["reasons"][0] = "TAMPERED_REASON"
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.RegimeInverseInvariantError,
            "OUTPUT_DERIVATION_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_source_is_offline_and_cli_writes_only_outside_repository(self):
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
            source_path = write_json(tmp / "regime.json", upstream_output())
            output_path = tmp / "nested" / "boundary.json"
            self.assertEqual(MODULE.run(source_path, output_path), 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIsNone(payload["inverse_order_intent"])
            self.assertEqual(list(output_path.parent.glob(".boundary.json.*")), [])

            forbidden = ROOT / "data" / "regime_inverse_invariant_test.json"
            self.assertEqual(MODULE.run(source_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
