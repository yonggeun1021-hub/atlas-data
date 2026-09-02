#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location(
    "atlas_cross_asset_flow_evidence_test", ROOT / "rotation/cross_asset_flow_evidence.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def component(component_id, packet, *, as_of="2026-08-26", generated="2026-08-26T12:00:00Z", available=None):
    return {
        "component_id": component_id,
        "status": "READY",
        "reason": None,
        "as_of_date": as_of,
        "available_at": available,
        "generated_at": generated,
        "packet": packet,
        "source_packet_path": f"evidence/{component_id}",
        "source_packet_sha256": None,
        "validated": True,
        "decision_eligible": False,
        "action_eligible": False,
        "order_eligible": False,
    }


def daily_packet():
    packet = {
        "schema_version": 1,
        "contract_version": "daily_orchestrator/6",
        "output_schema_version": "daily_briefing_packet/1",
        "decision_date": "2026-08-26",
        "slot": "evening",
        "generated_at": "2026-08-26T14:45:00Z",
        "components": [
            component("STABLECOIN_NET_ISSUANCE", {
                "observation_date": "2026-08-26",
                "daily_status": "AVAILABLE",
                "weekly_status": "AVAILABLE",
                "daily_net_issuance_native_usd_peg": "100.00",
                "weekly_net_issuance_native_usd_peg": "700.00",
            }),
            component("KRX_POST_CLOSE", {
                "symbols": [{
                    "symbol": "005930",
                    "observed_row": {
                        "trading_day": "2026-08-26",
                        "observed_at_kst": "2026-08-26T18:10:00+09:00",
                        "net_value": {"외국인합계": 10, "기관합계": -5},
                        "net_volume": {"외국인합계": 2, "기관합계": -1},
                    },
                }]
            }, generated="2026-08-26T09:10:00Z"),
            component("FREE_MARKET_DATA", {
                "vixcls": {"date": "2026-08-24", "value": "15.85"},
            }, as_of="2026-08-24", available="2026-08-26T12:48:35Z"),
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
    report = {"decision_date": decision_date, "candidates": []}
    return {
        "kind": "report",
        "report_sha256": MODULE.payload_sha256(report),
        "report": report,
    }


def resign(packet):
    packet["packet_sha256"] = MODULE.payload_sha256(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    return packet


class CrossAssetFlowEvidenceTests(unittest.TestCase):
    def test_legacy_packets_without_dynamic_clock_remain_readable(self):
        for frozen_sources in (None, {}):
            with self.subTest(frozen_sources=frozen_sources):
                source = daily_packet()
                if frozen_sources is not None:
                    source["frozen_sources"] = frozen_sources
                    resign(source)
                self.assertEqual(
                    MODULE.build_packet(source)["source_daily_packet_sha256"],
                    source["packet_sha256"],
                )

    def test_exact_dynamic_clock_variants_are_accepted(self):
        for frozen_source in (
            {"kind": "unavailable"},
            {"kind": "error", "value": "DynamicClockError:sealed"},
            dynamic_clock_source(),
        ):
            with self.subTest(kind=frozen_source["kind"]):
                source = daily_packet()
                source["frozen_sources"] = {"DYNAMIC_CLOCK": frozen_source}
                packet = MODULE.build_packet(resign(source))
                self.assertFalse(packet["authority"]["action_generation_authorized"])
                self.assertFalse(packet["authority"]["order_generation_authorized"])

    def test_resigned_dynamic_clock_shape_hash_and_date_tamper_fail_closed(self):
        valid = dynamic_clock_source()
        bad_hash = copy.deepcopy(valid)
        bad_hash["report_sha256"] = "0" * 64
        cases = (
            (True, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            ({"kind": True}, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            ({"kind": "unavailable", "value": None}, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            ({"kind": "error", "value": True}, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            ({**valid, "extra": None}, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            ({**valid, "report_sha256": True}, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            ({**valid, "report_sha256": "A" * 64}, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            ({**valid, "report": []}, "SOURCE_DYNAMIC_CLOCK_INVALID"),
            (bad_hash, "SOURCE_DYNAMIC_CLOCK_SHA_MISMATCH"),
            (
                dynamic_clock_source(decision_date="2026-08-25"),
                "SOURCE_DYNAMIC_CLOCK_DATE_MISMATCH",
            ),
        )
        for frozen_source, error in cases:
            with self.subTest(frozen_source=frozen_source):
                source = daily_packet()
                source["frozen_sources"] = {"DYNAMIC_CLOCK": frozen_source}
                with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, error):
                    MODULE.build_packet(resign(source))

    def test_four_evidence_classes_are_closed(self):
        self.assertEqual(MODULE.load_contract()["evidence_classes"], [
            "DIRECT_FLOW", "MARKET_IMPLIED_FLOW", "MACRO_CONTEXT", "UNKNOWN"
        ])

    def test_real_adapter_preserves_direct_flow_and_macro_context_without_comparison(self):
        packet = MODULE.build_packet(daily_packet())
        self.assertEqual(packet["evidence_class_counts"], {
            "DIRECT_FLOW": 2, "MARKET_IMPLIED_FLOW": 1, "MACRO_CONTEXT": 1, "UNKNOWN": 0
        })
        self.assertEqual(packet["cross_market_assessment"]["status"], "UNKNOWN")
        self.assertEqual(packet["cross_market_assessment"]["reason"], "SOURCE_AS_OF_MISMATCH_NO_LAG_AUTHORITY")
        self.assertIsNone(packet["cross_market_assessment"]["flow_direction"])

    def test_same_date_still_cannot_create_cross_market_flow_claim(self):
        source = daily_packet()
        source["components"][2]["as_of_date"] = "2026-08-26"
        source["components"][2]["packet"]["vixcls"]["date"] = "2026-08-26"
        source["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in source.items() if k != "packet_sha256"})
        packet = MODULE.build_packet(source)
        self.assertEqual(packet["cross_market_assessment"]["reason"], "CROSS_MARKET_COMPARISON_POLICY_UNRATIFIED")
        self.assertIsNone(packet["cross_market_assessment"]["from_market"])
        self.assertIsNone(packet["cross_market_assessment"]["to_market"])

    def test_krx_same_day_evidence_remains_observed_unconfirmed(self):
        row = next(item for item in MODULE.build_packet(daily_packet())["evidence_rows"] if item["subject"] == "KRX:005930")
        self.assertEqual(row["status"], "OBSERVED_UNCONFIRMED")
        self.assertEqual(row["evidence_grade"], "OBSERVED_UNCONFIRMED")
        self.assertEqual(row["invalidation"]["reason"], "SAME_DAY_OBSERVATION_NOT_NEXT_DAY_CONFIRMED")

    def test_vix_is_context_not_direct_flow(self):
        row = next(item for item in MODULE.build_packet(daily_packet())["evidence_rows"] if item["subject"] == "VIXCLS")
        self.assertEqual(row["evidence_class"], "MACRO_CONTEXT")
        self.assertEqual(row["values"]["unit"], "INDEX_LEVEL_CONTEXT_ONLY")

    def test_market_implied_flow_is_explicitly_unknown(self):
        row = next(item for item in MODULE.build_packet(daily_packet())["evidence_rows"] if item["evidence_class"] == "MARKET_IMPLIED_FLOW")
        self.assertEqual(row["status"], "UNKNOWN")
        self.assertEqual(row["invalidation"]["reason"], "COMPARABLE_MULTI_DATE_MARKET_SERIES_NOT_AVAILABLE")

    def test_freshness_is_not_invented(self):
        rows = MODULE.build_packet(daily_packet())["evidence_rows"]
        for row in rows:
            if row["status"] != "UNKNOWN":
                self.assertEqual(row["freshness_status"], "NOT_COMPUTABLE_WINDOW_UNRATIFIED")

    def test_missing_source_stays_unknown(self):
        source = daily_packet()
        source["components"] = [row for row in source["components"] if row["component_id"] != "STABLECOIN_NET_ISSUANCE"]
        source["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in source.items() if k != "packet_sha256"})
        row = MODULE.build_packet(source)["evidence_rows"][0]
        self.assertEqual(row["status"], "UNKNOWN")
        self.assertEqual(row["invalidation"]["reason"], "SOURCE_COMPONENT_MISSING")

    def test_future_component_is_rejected(self):
        source = daily_packet()
        source["components"][0]["as_of_date"] = "2026-08-27"
        source["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in source.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, "SOURCE_COMPONENT_FROM_FUTURE"):
            MODULE.build_packet(source)

    def test_available_after_decision_is_rejected(self):
        source = daily_packet()
        source["components"][2]["available_at"] = "2026-08-26T15:00:00Z"
        source["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in source.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, "SOURCE_COMPONENT_AVAILABLE_AFTER_DECISION"):
            MODULE.build_packet(source)

    def test_nested_krx_observation_after_decision_is_rejected(self):
        source = daily_packet()
        source["components"][1]["packet"]["symbols"][0]["observed_row"]["observed_at_kst"] = "2026-08-27T00:01:00+09:00"
        source["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in source.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, "KRX_OBSERVED_AFTER_DECISION"):
            MODULE.build_packet(source)

    def test_runtime_contract_override_cannot_reclassify_evidence(self):
        contract = MODULE.load_contract()
        contract["source_bindings"]["FREE_MARKET_DATA"]["evidence_class"] = "DIRECT_FLOW"
        with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, "SOURCE_BINDINGS_MISMATCH"):
            MODULE.build_packet(daily_packet(), contract)

    def test_source_hash_tamper_is_rejected(self):
        source = daily_packet()
        source["decision_date"] = "2026-08-25"
        with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, "SOURCE_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(source)

    def test_source_authority_expansion_is_rejected_even_if_resigned(self):
        source = daily_packet()
        source["components"][0]["action_eligible"] = True
        source["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in source.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, "SOURCE_COMPONENT_AUTHORITY_INVALID"):
            MODULE.build_packet(source)

    def test_output_resign_tamper_is_rejected_by_rederivation(self):
        source = daily_packet()
        packet = MODULE.build_packet(source)
        packet["cross_market_assessment"]["status"] = "READY"
        packet["cross_market_assessment"]["flow_direction"] = "US_TO_CRYPTO"
        packet["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CrossAssetFlowEvidenceError, "OUTPUT_DERIVATION_MISMATCH"):
            MODULE.validate_packet(packet, source)

    def test_authority_is_observation_only(self):
        authority = MODULE.build_packet(daily_packet())["authority"]
        self.assertTrue(authority["raw_evidence_presentation_only"])
        for key, value in authority.items():
            if key != "raw_evidence_presentation_only":
                self.assertFalse(value, key)


if __name__ == "__main__":
    unittest.main()
