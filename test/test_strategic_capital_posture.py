#!/usr/bin/env python3
"""P7-12 fail-closed Strategic Capital Posture readiness regression."""

import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "strategic_capital_posture.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("strategic_capital_posture", SOURCE)
CONTRACT = MODULE.load_contract()
P606_TEST = load_module(
    "p712_p606_fixture", ROOT / "test" / "test_defensive_action_decision.py"
)
CONCENTRATION_TEST = load_module(
    "p712_concentration_fixture",
    ROOT / "test" / "test_concentration_correlation_guard.py",
)
MARKET_THEME_TEST = load_module(
    "p712_market_theme_fixture",
    ROOT / "test" / "test_market_theme_exposure_budget.py",
)
CRYPTO_LIMIT_TEST = load_module(
    "p712_crypto_limit_fixture", ROOT / "test" / "test_crypto_exposure_limit.py"
)
PLANNED_LOSS_TEST = load_module(
    "p712_planned_loss_fixture", ROOT / "test" / "test_planned_loss_budget.py"
)
CURRENCY_TEST = load_module(
    "p712_currency_fixture", ROOT / "test" / "test_currency_exposure.py"
)


# P7-12's own baseline shares the P6-06 fixture's (dynamic) day and lands an
# hour after its fixed 02:00 generated_at, exactly like the original pinned
# "2026-08-21" convention -- see test_defensive_action_decision.py for why
# that day is derived from real evidence rather than hardcoded.
AS_OF_DATE = P606_TEST.AS_OF_DATE
GENERATED_AT = AS_OF_DATE + "T03:00:00Z"
FUTURE_AS_OF_DATE = (
    dt.date.fromisoformat(AS_OF_DATE) + dt.timedelta(days=1)
).isoformat()
FUTURE_GENERATED_AT = FUTURE_AS_OF_DATE + "T02:00:00Z"


def defensive_packet(as_of=P606_TEST.AS_OF_DATE, generated_at=P606_TEST.GENERATED_AT):
    packets, reasons = P606_TEST.bundle()
    return P606_TEST.MODULE.build_packet(
        packets,
        reasons,
        as_of,
        generated_at,
        contract=P606_TEST.CONTRACT,
    )


def p7_source_packet(name):
    if name == "P7_CONCENTRATION_GUARD":
        return CONCENTRATION_TEST.MODULE.build_packet(
            CONCENTRATION_TEST.input_packet(),
            CONCENTRATION_TEST.policy(),
            "2026-08-21",
            CONCENTRATION_TEST.CONTRACT,
        )
    if name == "P7_MARKET_THEME_BUDGET":
        return MARKET_THEME_TEST.MODULE.build_packet(
            MARKET_THEME_TEST.input_packet(),
            MARKET_THEME_TEST.policy(),
            "2026-08-21",
            MARKET_THEME_TEST.CONTRACT,
        )
    if name == "P7_CRYPTO_EXPOSURE_LIMIT":
        return CRYPTO_LIMIT_TEST.MODULE.build_packet(
            CRYPTO_LIMIT_TEST.input_packet(),
            CRYPTO_LIMIT_TEST.policy(),
            "2026-08-21",
            CRYPTO_LIMIT_TEST.CONTRACT,
        )
    if name == "P7_PLANNED_LOSS_BUDGET":
        return PLANNED_LOSS_TEST.MODULE.build_packet(
            PLANNED_LOSS_TEST.input_packet(),
            PLANNED_LOSS_TEST.constitution(),
            "2026-08-21",
            PLANNED_LOSS_TEST.CONTRACT,
        )
    if name == "P7_CURRENCY_EXPOSURE":
        return CURRENCY_TEST.MODULE.build_packet(
            CURRENCY_TEST.asset_master(),
            CURRENCY_TEST.snapshot(),
            CURRENCY_TEST.CONTRACT,
        )
    raise AssertionError(name)


def bundle(*, defensive_available=True):
    packets = {}
    reasons = {}
    for name in CONTRACT["source_order"]:
        if name == "P6_DEFENSIVE_ACTION" and defensive_available:
            packets[name] = defensive_packet()
            reasons[name] = []
        else:
            packets[name] = None
            reasons[name] = [f"{name}_NOT_CONNECTED_OR_UNRATIFIED"]
    return packets, reasons


def bundle_with_all_supported_sources():
    packets, reasons = bundle()
    for name in CONTRACT["source_specs"]:
        if name == "P6_DEFENSIVE_ACTION":
            continue
        packets[name] = p7_source_packet(name)
        reasons[name] = []
    return packets, reasons


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class StrategicCapitalPostureTests(unittest.TestCase):
    def build(self, *, defensive_available=True):
        packets, reasons = bundle(defensive_available=defensive_available)
        return MODULE.build_packet(
            packets,
            reasons,
            AS_OF_DATE,
            GENERATED_AT,
            contract=CONTRACT,
        )

    def test_contract_is_zero_capital_and_execution_authority_is_closed(self):
        self.assertEqual(CONTRACT["scope"], "ZERO_CAPITAL_CROSS_MARKET_BUDGET_READINESS")
        self.assertEqual(CONTRACT["runtime_status"], "BLOCKED")
        self.assertTrue(CONTRACT["authority"]["readiness_inventory_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_current_dependencies_are_blocked_and_missing_is_not_zero(self):
        packet = self.build()
        self.assertEqual(packet["status"], "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(packet["summary"]["available_source_count"], 1)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 8)
        self.assertEqual(
            packet["market_budget"], {"CRYPTO": None, "KOREA": None, "US": None}
        )
        for key in (
            "cash_reserve", "hedge_budget", "max_gross_risk", "max_net_risk",
            "theme_headroom", "risk_posture", "allocation_proposal",
            "target_exposures", "position_sizes",
        ):
            self.assertIsNone(packet[key], key)
        self.assertEqual(packet["order_intents"], [])
        self.assertNotIn(0, packet["market_budget"].values())

    def test_sum_overlap_and_currency_are_visible_but_not_evaluated(self):
        packet = self.build()
        checks = {row["check"]: row for row in packet["constraint_checks"]}
        self.assertEqual(set(checks), {"ALLOCATION_SUM", "CURRENCY_BOUNDARY", "OVERLAP_EXPOSURE"})
        for row in checks.values():
            self.assertEqual(row["evaluation_status"], "NOT_EVALUATED")
            self.assertIsNone(row["result"])
            self.assertIsNone(row["observed"])
        self.assertEqual(packet["summary"]["evaluated_constraint_count"], 0)
        self.assertEqual(packet["summary"]["numeric_budget_field_count"], 0)

    def test_all_supported_p6_p7_packets_are_revalidated_but_do_not_create_budget(self):
        packets, reasons = bundle_with_all_supported_sources()
        packet = MODULE.build_packet(
            packets,
            reasons,
            AS_OF_DATE,
            GENERATED_AT,
            contract=CONTRACT,
        )
        self.assertEqual(packet["summary"]["available_source_count"], 6)
        self.assertEqual(
            packet["summary"]["unavailable_sources"],
            ["P1_REGIME_DECISION", "P2_CROSS_MARKET_FLOW", "P2_ROTATION_STATE"],
        )
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertTrue(all(value is None for value in packet["market_budget"].values()))
        self.assertIsNone(packet["allocation_proposal"])

    def test_self_rehashed_p7_source_semantic_tamper_fails_closed(self):
        packets, reasons = bundle_with_all_supported_sources()
        source = packets["P7_CURRENCY_EXPOSURE"]
        source["summary"]["position_count"] += 1
        source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_SEMANTIC_INVALID:P7_CURRENCY_EXPOSURE",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_p6_packet_is_semantically_revalidated_at_consumption_boundary(self):
        packets, reasons = bundle()
        source = packets["P6_DEFENSIVE_ACTION"]
        source["summary"]["available_source_count"] += 1
        source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_SEMANTIC_INVALID:P6_DEFENSIVE_ACTION",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_source_slot_substitution_fails_closed(self):
        packets, reasons = bundle()
        packets["P7_CONCENTRATION_GUARD"] = copy.deepcopy(
            packets["P6_DEFENSIVE_ACTION"]
        )
        reasons["P7_CONCENTRATION_GUARD"] = []
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_IDENTITY_INVALID:P7_CONCENTRATION_GUARD",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_p1_or_p2_packet_cannot_bypass_missing_production_contract(self):
        packets, reasons = bundle()
        packets["P1_REGIME_DECISION"] = {"packet_sha256": "0" * 64}
        reasons["P1_REGIME_DECISION"] = []
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_PACKET_NOT_YET_SUPPORTED:P1_REGIME_DECISION",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_future_source_fails_closed(self):
        packets, reasons = bundle()
        packets["P6_DEFENSIVE_ACTION"] = defensive_packet(
            FUTURE_AS_OF_DATE, FUTURE_GENERATED_AT
        )
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_FROM_FUTURE:P6_DEFENSIVE_ACTION",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_future_effective_availability_in_each_p7_source_fails_closed(self):
        for name in (
            "P7_CONCENTRATION_GUARD",
            "P7_MARKET_THEME_BUDGET",
            "P7_CRYPTO_EXPOSURE_LIMIT",
            "P7_PLANNED_LOSS_BUDGET",
            "P7_CURRENCY_EXPOSURE",
        ):
            with self.subTest(name=name):
                packets, reasons = bundle(defensive_available=False)
                packets[name] = p7_source_packet(name)
                reasons[name] = []
                with self.assertRaisesRegex(
                    MODULE.StrategicCapitalPostureError,
                    f"SOURCE_FROM_FUTURE:{name}",
                ):
                    MODULE.build_packet(
                        packets,
                        reasons,
                        "2026-08-21",
                        "2026-08-21T00:00:00Z",
                        contract=CONTRACT,
                    )

    def test_unratified_policy_packet_is_rejected(self):
        packets, reasons = bundle()
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "UNRATIFIED_POLICY_PACKET_FORBIDDEN",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                policy_packet={"status": "RATIFIED"},
                contract=CONTRACT,
            )

    def test_self_rehashed_output_budget_or_order_tamper_fails_closed(self):
        packet = self.build()
        packet["market_budget"]["US"] = 0.5
        packet["order_intents"] = [{"side": "BUY"}]
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_output_is_deterministic_and_preserves_exact_lineage(self):
        first = self.build()
        second = self.build()
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.validate_packet(first, CONTRACT), first)
        self.assertEqual(
            first["lineage"]["source_packet_sha256"],
            {row["name"]: row["source_packet_sha256"] for row in first["sources"]},
        )

    def test_all_sources_unavailable_remains_blocked_without_fabrication(self):
        packet = self.build(defensive_available=False)
        self.assertEqual(packet["summary"]["available_source_count"], 0)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 9)
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertIsNone(packet["risk_posture"])
        self.assertEqual(packet["order_intents"], [])

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

        with tempfile.TemporaryDirectory() as name:
            temp = Path(name)
            packets, reasons = bundle()
            bundle_path = write_json(temp / "bundle.json", {
                "source_packets": packets,
                "unavailable_reasons": reasons,
                "policy_packet": None,
            })
            output_path = temp / "nested" / "readiness.json"
            self.assertEqual(
                MODULE.run(
                    bundle_path,
                    AS_OF_DATE,
                    GENERATED_AT,
                    output_path,
                ),
                0,
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["decision_status"], "BLOCKED")
            self.assertEqual(list(output_path.parent.glob(".readiness.json.*")), [])

            forbidden = ROOT / "data" / "strategic_capital_posture_test.json"
            self.assertEqual(
                MODULE.run(
                    bundle_path,
                    AS_OF_DATE,
                    GENERATED_AT,
                    forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
