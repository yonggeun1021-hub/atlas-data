#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "briefing/flow_first_briefing.py"
SPEC = importlib.util.spec_from_file_location("atlas_flow_first_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def row(component_id, status="READY", *, as_of="2026-08-26", reason=None, packet=None,
        generated_at="2026-08-26T10:00:00Z"):
    value = {
        "component_id": component_id,
        "status": status,
        "reason": reason,
        "as_of_date": as_of,
        "available_at": "2026-08-26T10:00:00Z",
        "generated_at": generated_at,
        "source_packet_path": f"evidence/{component_id}.json",
        "source_packet_sha256": "a" * 64,
        "validated": status == "READY",
        "decision_eligible": False,
        "action_eligible": False,
        "order_eligible": False,
    }
    if packet is not None:
        value["packet"] = packet
    return value


def daily_packet():
    packet = {
        "schema_version": 1,
        "contract_version": "daily_orchestrator/6",
        "output_schema_version": "daily_briefing_packet/1",
        "decision_date": "2026-08-26",
        "slot": "evening",
        "generated_at": "2026-08-26T10:30:00Z",
        "components": [
            row("THREE_MARKET_REGIME_HEADER"),
            row("ROTATION_DISCOVERY"),
            row("KOREA_ROTATION"),
            row("DEFENSIVE_ACTION_DECISION", "PENDING", reason="P6_POLICY_UNRATIFIED"),
            row("STRATEGIC_CAPITAL_POSTURE", "PENDING", reason="P7_POLICY_UNRATIFIED"),
            row("ACTION_RISK_PORTFOLIO_SUMMARY"),
            row("SHADOW_ENTRY_REVIEW"),
            row("POSITION_SIZING", "POLICY_BLOCKED", reason="POSITION_SIZING_POLICY_UNRATIFIED"),
            row("PLANNED_LOSS_BUDGET", "POLICY_BLOCKED", reason="LOSS_BUDGET_UNRATIFIED"),
            row("STABLECOIN_NET_ISSUANCE", packet={
                "observation_date": "2026-08-26",
                "daily_status": "AVAILABLE",
                "weekly_status": "AVAILABLE",
                "daily_net_issuance_native_usd_peg": "100.00",
                "weekly_net_issuance_native_usd_peg": "700.00",
            }),
            row("KRX_POST_CLOSE", packet={
                "symbols": [{
                    "symbol": "005930",
                    "observed_row": {
                        "trading_day": "2026-08-26",
                        "observed_at_kst": "2026-08-26T18:10:00+09:00",
                        "net_value": {"foreign": 10, "institution": -5},
                        "net_volume": {"foreign": 2, "institution": -1},
                    },
                }],
            }),
            row("FREE_MARKET_DATA", as_of="2026-08-24", packet={
                "vixcls": {"date": "2026-08-24", "value": "15.85"},
            }),
        ],
        "authority": {
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["packet_sha256"] = MODULE.payload_sha256(packet)
    return packet


def dynamic_clock_source(*, decision_date="2026-08-26"):
    report = {"decision_date": decision_date, "review_queue": []}
    return {
        "kind": "report",
        "report": report,
        "report_sha256": MODULE.payload_sha256(report),
    }


def resign_source(packet):
    packet["packet_sha256"] = MODULE.payload_sha256(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    return packet


def contract_without_cross_market_flow():
    contract = copy.deepcopy(MODULE.load_contract())
    contract["section_order"].remove("CROSS_MARKET_FLOW")
    del contract["sections"]["CROSS_MARKET_FLOW"]
    return contract


class FlowFirstBriefingTests(unittest.TestCase):
    def test_root_order_is_fixed(self):
        packet = MODULE.build_packet(daily_packet())
        self.assertEqual(packet["section_order"], [
            "REGIME", "CROSS_MARKET_FLOW", "THEME_ROTATION",
            "CAPITAL_ACTION", "ASSETS", "ENTRY_EXIT_SIZE",
        ])
        self.assertEqual([item["section_id"] for item in packet["sections"]], packet["section_order"])

    def test_each_section_exposes_required_evidence_boundaries(self):
        packet = MODULE.build_packet(daily_packet())
        for section in packet["sections"]:
            self.assertIn("as_of_date", section)
            self.assertEqual(section["evidence_grade"], "UNKNOWN")
            self.assertIn("unknown_reason", section)
            self.assertEqual(section["invalidation"]["status"], "UNKNOWN")
            self.assertFalse(section["decision_eligible"])
            self.assertFalse(section["action_eligible"])
            self.assertFalse(section["order_eligible"])

    def test_cross_market_flow_is_visible_and_not_inferred(self):
        packet = MODULE.build_packet(daily_packet())
        section = packet["sections"][1]
        self.assertEqual(section["status"], "UNKNOWN")
        self.assertEqual(section["unknown_reason"], "SOURCE_AS_OF_MISMATCH_NO_LAG_AUTHORITY")
        self.assertEqual(section["cross_asset_flow_evidence"]["evidence_class_counts"], {
            "DIRECT_FLOW": 2,
            "MARKET_IMPLIED_FLOW": 1,
            "MACRO_CONTEXT": 1,
            "UNKNOWN": 0,
        })
        self.assertIsNone(section["cross_asset_flow_evidence"]["flow_direction"])
        self.assertEqual(
            {row["component_id"] for row in section["source_components"]},
            {"STABLECOIN_NET_ISSUANCE", "KRX_POST_CLOSE", "FREE_MARKET_DATA"},
        )

    def test_policy_blocked_entry_section_cannot_look_ready(self):
        packet = MODULE.build_packet(daily_packet())
        section = packet["sections"][-1]
        self.assertEqual(section["status"], "POLICY_BLOCKED")
        self.assertEqual(section["unknown_reason"], "SOURCE_COMPONENT_NOT_READY")

    def test_capital_action_exposes_defensive_and_strategic_readiness(self):
        packet = MODULE.build_packet(daily_packet())
        section = next(
            row for row in packet["sections"] if row["section_id"] == "CAPITAL_ACTION"
        )
        self.assertEqual(section["status"], "PENDING")
        self.assertEqual(
            [row["component_id"] for row in section["source_components"]],
            [
                "DEFENSIVE_ACTION_DECISION",
                "STRATEGIC_CAPITAL_POSTURE",
                "ACTION_RISK_PORTFOLIO_SUMMARY",
            ],
        )
        self.assertFalse(section["action_eligible"])
        self.assertFalse(section["order_eligible"])

    def test_different_source_dates_fail_closed(self):
        source = daily_packet()
        source["components"][2]["as_of_date"] = "2026-08-25"
        source["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in source.items() if k != "packet_sha256"})
        section = MODULE.build_packet(source)["sections"][2]
        self.assertEqual(section["status"], "DATA_BLOCKED")
        self.assertEqual(section["unknown_reason"], "SOURCE_AS_OF_DATE_MISMATCH")
        self.assertIsNone(section["as_of_date"])

    def test_source_packet_resign_tamper_is_not_silently_accepted_by_output_validator(self):
        source = daily_packet()
        packet = MODULE.build_packet(source)
        tampered_source = copy.deepcopy(source)
        tampered_source["components"][0]["status"] = "UNKNOWN"
        tampered_source["components"][0]["reason"] = "TAMPERED"
        tampered_source["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered_source.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.FlowFirstBriefingError, "OUTPUT_DERIVATION_MISMATCH"):
            MODULE.validate_packet(packet, tampered_source)

    def test_output_tamper_with_new_hash_is_rejected_by_independent_derivation(self):
        source = daily_packet()
        packet = MODULE.build_packet(source)
        packet["sections"][0]["status"] = "READY"
        packet["sections"][0]["evidence_grade"] = "HIGH"
        packet["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in packet.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.FlowFirstBriefingError, "OUTPUT_DERIVATION_MISMATCH"):
            MODULE.validate_packet(packet, source)

    def test_source_packet_hash_tamper_is_rejected(self):
        source = daily_packet()
        source["decision_date"] = "2030-01-01"
        with self.assertRaisesRegex(MODULE.FlowFirstBriefingError, "SOURCE_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(source)

    def test_legacy_absence_and_valid_dynamic_clock_remain_readable_without_flow_section(self):
        contract = contract_without_cross_market_flow()
        legacy = MODULE.build_packet(daily_packet(), contract)
        self.assertEqual(
            legacy["section_order"],
            ["REGIME", "THEME_ROTATION", "CAPITAL_ACTION", "ASSETS", "ENTRY_EXIT_SIZE"],
        )

        source = daily_packet()
        source["frozen_sources"] = {"DYNAMIC_CLOCK": dynamic_clock_source()}
        valid = MODULE.build_packet(resign_source(source), contract)
        self.assertEqual(valid["source_daily_packet_sha256"], source["packet_sha256"])

    def test_direct_reader_rejects_resigned_dynamic_clock_identity_tamper(self):
        valid = dynamic_clock_source()
        cases = [
            ([], "SOURCE_DYNAMIC_CLOCK_INVALID:frozen_sources_type", True),
            ([], "SOURCE_DYNAMIC_CLOCK_INVALID:source_type", False),
            ({"kind": True}, "SOURCE_DYNAMIC_CLOCK_INVALID:kind_type", False),
            (
                {"kind": "unavailable", "value": "extra"},
                "SOURCE_DYNAMIC_CLOCK_INVALID:unavailable_shape",
                False,
            ),
            (
                {"kind": "error", "error": "failure"},
                "SOURCE_DYNAMIC_CLOCK_INVALID:error_shape",
                False,
            ),
            (
                {"kind": "report", "report": valid["report"]},
                "SOURCE_DYNAMIC_CLOCK_INVALID:report_shape",
                False,
            ),
            (
                {**valid, "report_sha256": True},
                "SOURCE_DYNAMIC_CLOCK_INVALID:report_hash_type",
                False,
            ),
            (
                {**valid, "report_sha256": "not-a-sha"},
                "SOURCE_DYNAMIC_CLOCK_INVALID:report_sha256",
                False,
            ),
            (
                {**valid, "report_sha256": "0" * 64},
                "SOURCE_DYNAMIC_CLOCK_SHA_MISMATCH",
                False,
            ),
            (
                dynamic_clock_source(decision_date="2026-08-25"),
                "SOURCE_DYNAMIC_CLOCK_DATE_MISMATCH",
                False,
            ),
        ]
        contract = contract_without_cross_market_flow()
        for frozen_value, error, replaces_frozen_sources in cases:
            with self.subTest(error=error):
                source = daily_packet()
                source["frozen_sources"] = (
                    frozen_value
                    if replaces_frozen_sources
                    else {"DYNAMIC_CLOCK": frozen_value}
                )
                with self.assertRaisesRegex(MODULE.FlowFirstBriefingError, error):
                    MODULE.build_packet(resign_source(source), contract)

    def test_all_investment_and_trading_authority_remains_false(self):
        contract = MODULE.load_contract()
        self.assertTrue(contract["authority"]["presentation_order_authorized"])
        for key, value in contract["authority"].items():
            if key != "presentation_order_authorized":
                self.assertFalse(value, key)

    def test_source_component_authority_expansion_is_rejected_even_if_resigned(self):
        source = daily_packet()
        source["components"][0]["action_eligible"] = True
        source["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in source.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.FlowFirstBriefingError, "SOURCE_COMPONENT_AUTHORITY_INVALID"):
            MODULE.build_packet(source)

    def test_future_component_is_allowed_only_as_explicitly_data_blocked_metadata(self):
        source = daily_packet()
        source["components"][0]["as_of_date"] = "2026-08-27"
        source["components"][0]["status"] = "DATA_BLOCKED"
        source["components"][0]["reason"] = "AS_OF_DATE_AFTER_DECISION_DATE"
        source["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in source.items() if k != "packet_sha256"}
        )
        packet = MODULE.build_packet(source)
        self.assertEqual(packet["sections"][0]["status"], "DATA_BLOCKED")

        source["components"][0]["status"] = "READY"
        source["components"][0]["reason"] = None
        source["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in source.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.FlowFirstBriefingError, "SOURCE_COMPONENT_FROM_FUTURE"):
            MODULE.build_packet(source)


if __name__ == "__main__":
    unittest.main()
