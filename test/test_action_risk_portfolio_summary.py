#!/usr/bin/env python3
"""P8-06 Action / Bear-Hedge / Portfolio summary regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "briefing" / "action_risk_portfolio_summary.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("action_risk_portfolio_summary", SOURCE)
CONTRACT = MODULE.load_contract()
POSITION_FIXTURE = load_module(
    "p806_position_sizing_fixture", ROOT / "test" / "test_position_sizing.py"
)
CONCENTRATION_FIXTURE = load_module(
    "p806_concentration_fixture",
    ROOT / "test" / "test_concentration_correlation_guard.py",
)
PLANNED_LOSS_FIXTURE = load_module(
    "p806_planned_loss_fixture", ROOT / "test" / "test_planned_loss_budget.py"
)


def unified_packet():
    contract = MODULE.UNIFIED.load_contract()
    components = {name: None for name in contract["component_order"]}
    reasons = {name: ["TEST_UNAVAILABLE"] for name in contract["component_order"]}
    return MODULE.UNIFIED.build_packet(
        components=components,
        unavailable_reasons=reasons,
        decision_date="2026-08-21",
        slot="morning",
        generated_at="2026-08-21T00:30:00Z",
    )


def source_packet(name, status=None, breaches=None):
    if name == "POSITION_SIZING":
        packet = POSITION_FIXTURE.build()
        if status is not None and status != packet["status"]:
            packet["status"] = status
            packet["packet_sha256"] = MODULE.payload_sha256({
                key: value for key, value in packet.items()
                if key != "packet_sha256"
            })
        return packet
    if name == "CONCENTRATION_GUARD":
        ratified = (
            CONCENTRATION_FIXTURE.policy(max_market_exposure=0.44)
            if status == "LIMIT_BREACH"
            else CONCENTRATION_FIXTURE.policy()
        )
        packet = CONCENTRATION_FIXTURE.MODULE.build_packet(
            CONCENTRATION_FIXTURE.input_packet(),
            ratified,
            "2026-08-21",
            CONCENTRATION_FIXTURE.CONTRACT,
        )
        if status is not None:
            assert packet["status"] == status
        if breaches is not None:
            assert packet["breaches"] == breaches
        return packet
    if name == "PLANNED_LOSS_BUDGET":
        packet = PLANNED_LOSS_FIXTURE.MODULE.build_packet(
            PLANNED_LOSS_FIXTURE.input_packet(),
            PLANNED_LOSS_FIXTURE.constitution(),
            "2026-08-21",
            PLANNED_LOSS_FIXTURE.CONTRACT,
        )
        if status is not None:
            assert packet["status"] == status
        if breaches is not None:
            assert packet["breaches"] == breaches
        return packet
    spec = CONTRACT["source_specs"][name]
    packet = {
        "schema_version": spec["schema_version"],
        "contract_version": spec["contract_version"],
        "status": spec["statuses"][0] if status is None else status,
        "authority": MODULE._source_authority(spec),
        "evidence": {"source": name},
    }
    if name in CONTRACT["risk_sources"]:
        packet["breaches"] = [] if breaches is None else breaches
    packet["packet_sha256"] = MODULE.payload_sha256(packet)
    return packet


def bundle(all_available=True):
    packets = {}
    reasons = {}
    for name in CONTRACT["source_order"]:
        if name == "UNIFIED_DECISION":
            packets[name] = unified_packet()
            reasons[name] = []
        elif all_available:
            packets[name] = source_packet(name)
            reasons[name] = []
        else:
            packets[name] = None
            reasons[name] = ["TEST_SOURCE_NOT_CONNECTED"]
    return packets, reasons


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class ActionRiskPortfolioSummaryTests(unittest.TestCase):
    def test_contract_has_six_categories_and_no_action_authority(self):
        self.assertEqual(CONTRACT["action_categories"], [
            "BUY", "WATCH", "REDUCE", "HEDGE", "EXIT", "NOTHING"
        ])
        self.assertEqual(CONTRACT["required_sources"], ["UNIFIED_DECISION"])
        self.assertTrue(CONTRACT["authority"]["briefing_read_model_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "briefing_read_model_only":
                self.assertFalse(value, key)
        tampered = copy.deepcopy(CONTRACT)
        tampered["source_specs"]["PLANNED_LOSS_BUDGET"]["statuses"].append("FAKE_PASS")
        with self.assertRaisesRegex(
            MODULE.ActionRiskPortfolioSummaryError,
            "CONTRACT_MISMATCH",
        ):
            MODULE._validate_contract(tampered)

    def test_all_sources_present_but_every_action_stays_not_evaluated(self):
        packets, reasons = bundle()
        packet = MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)
        self.assertEqual(packet["status"], "ACTION_RISK_PORTFOLIO_PRESENTED_NO_ACTION_AUTHORITY")
        self.assertEqual([row["category"] for row in packet["actions"]], CONTRACT["action_categories"])
        self.assertTrue(all(row["evaluation_status"] == "NOT_EVALUATED" for row in packet["actions"]))
        self.assertTrue(all(row["action"] is None for row in packet["actions"]))
        self.assertEqual(packet["summary"], {
            "source_count": 15,
            "available_source_count": 15,
            "unavailable_source_count": 0,
            "risk_breach_source_count": 0,
            "action_category_count": 6,
            "evaluated_action_count": 0,
            "nothing_action": None,
        })
        nothing = next(row for row in packet["actions"] if row["category"] == "NOTHING")
        self.assertIn("ABSENCE_OF_ACTION_IS_NOT_NOTHING_ACTION", nothing["reasons"])

    def test_optional_sources_remain_explicitly_unavailable(self):
        packets, reasons = bundle(all_available=False)
        packet = MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)
        self.assertEqual(packet["summary"]["available_source_count"], 1)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 14)
        reduce_row = next(row for row in packet["actions"] if row["category"] == "REDUCE")
        self.assertIn("SOURCE_UNAVAILABLE:CASH_EXPOSURE_US", reduce_row["reasons"])
        self.assertEqual(reduce_row["evidence_packet_sha256"], [])

    def test_portfolio_breaches_are_shown_but_not_translated_to_action(self):
        packets, reasons = bundle()
        breach = [{"scope_type": "MARKET", "scope_id": "US"}]
        packets["CONCENTRATION_GUARD"] = source_packet(
            "CONCENTRATION_GUARD", status="LIMIT_BREACH", breaches=breach
        )
        packet = MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)
        finding = next(row for row in packet["risk_findings"] if row["source"] == "CONCENTRATION_GUARD")
        self.assertEqual(finding["breaches"], breach)
        self.assertEqual(packet["summary"]["risk_breach_source_count"], 1)
        self.assertTrue(all(row["action"] is None for row in packet["actions"]))

    def test_position_sizing_is_fully_validated_and_never_becomes_buy(self):
        packets, reasons = bundle()
        packet = MODULE.build_summary(
            packets, reasons, "2026-08-21T00:35:00Z", CONTRACT
        )
        finding = next(
            row for row in packet["risk_findings"]
            if row["source"] == "POSITION_SIZING"
        )
        self.assertEqual(
            finding["sizing"]["maximum_position_weight_nav_fraction"], "0.02"
        )
        self.assertEqual(
            finding["sizing"]["target_position_weight_nav_fraction"], "0.01"
        )
        buy = next(row for row in packet["actions"] if row["category"] == "BUY")
        self.assertIn(
            packets["POSITION_SIZING"]["packet_sha256"],
            buy["evidence_packet_sha256"],
        )
        self.assertIsNone(buy["action"])

        tampered = packets["POSITION_SIZING"]
        tampered["target_position_weight_nav_fraction"] = "0.5"
        tampered["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in tampered.items()
            if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.ActionRiskPortfolioSummaryError, "POSITION_SIZING_INVALID"
        ):
            MODULE.build_summary(
                packets, reasons, "2026-08-21T00:35:00Z", CONTRACT
            )

    def test_p7_risk_packets_require_full_production_validation(self):
        packets, reasons = bundle()
        concentration = packets["CONCENTRATION_GUARD"]
        concentration["summary"]["breach_count"] = 99
        concentration["packet_sha256"] = MODULE.payload_sha256(
            {
                key: value
                for key, value in concentration.items()
                if key != "packet_sha256"
            }
        )
        with self.assertRaisesRegex(
            MODULE.ActionRiskPortfolioSummaryError, "CONCENTRATION_GUARD_INVALID"
        ):
            MODULE.build_summary(
                packets, reasons, "2026-08-21T00:35:00Z", CONTRACT
            )

        packets, reasons = bundle()
        planned = packets["PLANNED_LOSS_BUDGET"]
        planned["summary"]["breach_count"] = 99
        planned["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in planned.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ActionRiskPortfolioSummaryError, "PLANNED_LOSS_BUDGET_INVALID"
        ):
            MODULE.build_summary(
                packets, reasons, "2026-08-21T00:35:00Z", CONTRACT
            )

    def test_required_unified_source_and_same_day_time_are_enforced(self):
        packets, reasons = bundle()
        packets["UNIFIED_DECISION"] = None
        reasons["UNIFIED_DECISION"] = ["TEST_MISSING"]
        with self.assertRaisesRegex(
            MODULE.ActionRiskPortfolioSummaryError,
            "REQUIRED_SOURCE_UNAVAILABLE:UNIFIED_DECISION",
        ):
            MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)

        packets, reasons = bundle()
        with self.assertRaisesRegex(MODULE.ActionRiskPortfolioSummaryError, "SUMMARY_DATE_MISMATCH"):
            MODULE.build_summary(packets, reasons, "2026-08-22T00:35:00Z", CONTRACT)

    def test_source_hash_authority_and_action_smuggling_fail_closed(self):
        packets, reasons = bundle()
        packets["CASH_EXPOSURE_US"]["evidence"]["source"] = "TAMPER"
        with self.assertRaisesRegex(MODULE.ActionRiskPortfolioSummaryError, "SOURCE_PACKET_SHA_MISMATCH"):
            MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)

        packets, reasons = bundle()
        packets["CASH_EXPOSURE_US"]["authority"]["order_authorized"] = True
        unsigned = copy.deepcopy(packets["CASH_EXPOSURE_US"])
        unsigned.pop("packet_sha256")
        packets["CASH_EXPOSURE_US"]["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.ActionRiskPortfolioSummaryError, "SOURCE_IDENTITY_INVALID"):
            MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)

        packets, reasons = bundle()
        packets["CASH_EXPOSURE_US"]["order_intents"] = [{"symbol": "MSFT"}]
        unsigned = copy.deepcopy(packets["CASH_EXPOSURE_US"])
        unsigned.pop("packet_sha256")
        packets["CASH_EXPOSURE_US"]["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.ActionRiskPortfolioSummaryError, "SOURCE_ACTION_SMUGGLING"):
            MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)

    def test_unified_decision_requires_full_source_validation(self):
        packets, reasons = bundle()
        unified = packets["UNIFIED_DECISION"]
        unified["summary"]["component_count"] = 999
        unsigned = copy.deepcopy(unified)
        unsigned.pop("packet_sha256")
        unified["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.ActionRiskPortfolioSummaryError, "UNIFIED_DECISION_INVALID"):
            MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)

    def test_output_is_deterministic_and_lineage_complete(self):
        packets, reasons = bundle()
        first = MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)
        second = MODULE.build_summary(packets, reasons, "2026-08-21T00:35:00Z", CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(
            first["lineage"]["unified_decision_packet_sha256"],
            packets["UNIFIED_DECISION"]["packet_sha256"],
        )
        self.assertEqual(set(first["lineage"]["source_packet_sha256"]), set(CONTRACT["source_order"]))
        self.assertEqual(first["source_packets"], packets)
        self.assertEqual(first["unavailable_reasons"], reasons)
        self.assertEqual(MODULE.validate_packet(first, CONTRACT), first)
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_self_rehashed_summary_tamper_fails_closed(self):
        packets, reasons = bundle()
        packet = MODULE.build_summary(
            packets, reasons, "2026-08-21T00:35:00Z", CONTRACT
        )
        packet["summary"]["risk_breach_source_count"] = 99
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ActionRiskPortfolioSummaryError, "OUTPUT_DERIVATION_MISMATCH"
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
            packets, reasons = bundle()
            source = write_json(tmp / "bundle.json", {
                "source_packets": packets,
                "unavailable_reasons": reasons,
            })
            output = tmp / "nested" / "packet.json"
            self.assertEqual(MODULE.run(source, "2026-08-21T00:35:00Z", output), 0)
            serialized = json.loads(output.read_text())
            self.assertEqual(serialized["summary"]["action_category_count"], 6)
            self.assertEqual(MODULE.validate_packet(serialized, CONTRACT), serialized)
            forbidden = ROOT / "data" / "action_risk_summary_test.json"
            self.assertEqual(MODULE.run(source, "2026-08-21T00:35:00Z", forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
