#!/usr/bin/env python3
"""P6-01 Cash / Exposure Reduction independent action boundary regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "cash_exposure_action.py"
REGIME_SOURCE = ROOT / "regime" / "output_contract.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("cash_exposure_action", SOURCE)
REGIME = load_module("cash_exposure_regime_fixture", REGIME_SOURCE)
CONTRACT = MODULE.load_contract()


def upstream_output(market="KR"):
    return REGIME.build_unknown_output(market, "2026-08-21T01:00:00Z")


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class CashExposureActionTests(unittest.TestCase):
    def test_contract_keeps_cash_reduction_independent_and_unauthorized(self):
        self.assertEqual(
            CONTRACT["invariant"],
            "CASH_AND_EXPOSURE_REDUCTION_ARE_INDEPENDENT_FROM_SHORT_HEDGE_AND_ORDER",
        )
        self.assertEqual(CONTRACT["runtime_authorized_regimes"], ["UNKNOWN"])
        self.assertEqual(CONTRACT["runtime_evaluation_status"], "NOT_EVALUATED")
        self.assertTrue(CONTRACT["authority"]["independent_action_boundary_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "independent_action_boundary_only":
                self.assertFalse(value, key)

    def test_current_unknown_regime_creates_no_action_or_target(self):
        packet = MODULE.build_packet(upstream_output(), CONTRACT)
        self.assertEqual(packet["status"], "CASH_EXPOSURE_ACTION_NOT_EVALUATED")
        self.assertEqual(packet["evaluation_status"], "NOT_EVALUATED")
        self.assertEqual(
            packet["independent_action_fields"],
            ["cash_action", "exposure_reduction_action"],
        )
        for key in (
            "cash_action",
            "exposure_reduction_action",
            "target_cash_weight",
            "target_gross_exposure",
        ):
            self.assertIsNone(packet[key], key)
        for key in (
            "position_adjustments",
            "short_intents",
            "hedge_intents",
            "order_intents",
        ):
            self.assertEqual(packet[key], [], key)

    def test_no_change_is_not_fabricated_from_unknown(self):
        packet = MODULE.build_packet(upstream_output(), CONTRACT)
        self.assertIn("NO_CHANGE", CONTRACT["action_vocabulary"])
        self.assertIsNone(packet["cash_action"])
        self.assertIsNone(packet["exposure_reduction_action"])
        self.assertNotEqual(packet["evaluation_status"], "EVALUATED")

    def test_smuggled_cash_reduction_short_hedge_or_order_is_rejected(self):
        cases = [
            {"cash_action": "HOLD_CASH"},
            {"exposure_reduction_action": "REDUCE_EXPOSURE"},
            {"target_cash_weight": "0.40"},
            {"target_gross_exposure": "0.60"},
            {"position_adjustments": [{"symbol": "005930"}]},
            {"short_intents": [{"symbol": "005930"}]},
            {"hedge_intents": [{"instrument": "UNAUTHORIZED"}]},
            {"order_intents": [{"side": "SELL"}]},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(
                    MODULE.CashExposureActionError,
                    "UNAUTHORIZED_ACTION_SMUGGLING",
                ):
                    MODULE.assert_no_unauthorized_action(**kwargs)

    def test_lineage_preserves_exact_upstream_identity(self):
        source = upstream_output("US")
        packet = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(packet["market"], "US")
        self.assertEqual(packet["generated_at"], source["generated_at"])
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertEqual(packet["direction"], "UNKNOWN")
        self.assertIsNone(packet["confidence"])
        self.assertEqual(
            packet["lineage"]["upstream_regime_output_sha256"],
            MODULE.payload_sha256(source),
        )

    def test_future_regime_cannot_be_smuggled_through_current_runtime(self):
        for regime in ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"):
            with self.subTest(regime=regime):
                source = upstream_output()
                source["regime"] = regime
                with self.assertRaisesRegex(
                    MODULE.CashExposureActionError,
                    "UPSTREAM_OUTPUT_INVALID:REGIME_NOT_AUTHORIZED",
                ):
                    MODULE.build_packet(source, CONTRACT)

    def test_upstream_or_local_authority_expansion_fails_closed(self):
        source = upstream_output()
        source["authority"]["trading_action_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.CashExposureActionError,
            "UPSTREAM_OUTPUT_INVALID:AUTHORITY_BOUNDARY_INVALID",
        ):
            MODULE.build_packet(source, CONTRACT)

        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["cash_action_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.CashExposureActionError,
            "CONTRACT_FIELD_MISMATCH:authority",
        ):
            MODULE.build_packet(upstream_output(), contract)

    def test_packet_is_deterministic_and_tamper_evident(self):
        source = upstream_output("CRYPTO")
        before = MODULE.canonical_json(source)
        first = MODULE.build_packet(source, CONTRACT)
        second = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(source), before)
        self.assertEqual(MODULE.validate_packet(first, source, CONTRACT), first)

        tampered = copy.deepcopy(first)
        tampered["cash_action"] = "HOLD_CASH"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.CashExposureActionError,
            "PACKET_CONTENT_MISMATCH",
        ):
            MODULE.validate_packet(tampered, source, CONTRACT)

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
            source_path = write_json(tmp / "regime.json", upstream_output())
            output_path = tmp / "nested" / "cash-exposure.json"
            self.assertEqual(MODULE.run(source_path, output_path), 0)
            packet = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(packet["evaluation_status"], "NOT_EVALUATED")
            self.assertEqual(list(output_path.parent.glob(".cash-exposure.json.*")), [])

            forbidden = ROOT / "data" / "cash_exposure_action_test.json"
            self.assertEqual(MODULE.run(source_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
