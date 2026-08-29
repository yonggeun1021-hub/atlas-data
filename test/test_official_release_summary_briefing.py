#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
from unittest import mock
import unittest


ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_PATH = ROOT / "briefing" / "daily_orchestrator.py"
RUN_ALL_PATH = ROOT / "run_all.py"
CONSUMER_PATH = ROOT / ".github/scripts/consume_scheduled_briefing_authority.py"
DECISION_AT = "2026-08-28T00:00:00Z"


def load_orchestrator(name: str):
    spec = importlib.util.spec_from_file_location(name, ORCHESTRATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _pinned_delivery_packet(contract_version: str) -> dict:
    packet = {
        "schema_version": 1,
        "contract_version": contract_version,
        "output_schema_version": "daily_briefing_packet/1",
        "slot": "morning",
        "decision_date": "2026-08-28",
        "generated_at": "2026-08-28T00:00:00Z",
        "capture_mode": "provider_free_aggregation_of_persisted_evidence_only",
        "component_status_counts": {
            "READY": 0,
            "PENDING": 1,
            "DATA_BLOCKED": 0,
            "POLICY_BLOCKED": 0,
            "DEGRADED": 0,
            "UNAVAILABLE": 0,
            "UNKNOWN": 0,
        },
        "components": [{
            "component_id": "OFFICIAL_RELEASE_SUMMARY",
            "status": "PENDING",
            "decision_eligible": False,
            "action_eligible": False,
            "order_eligible": False,
        }],
        "authority": {
            "aggregation_only": True,
            "component_build_authorized": True,
            "source_interpretation_authorized": False,
            "regime_score_authorized": False,
            "rotation_ranking_authorized": False,
            "discovery_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "portfolio_sizing_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "frozen_sources": {},
        "unresolved_boundaries": ["INTERPRETATION_AND_RANKING_UNRATIFIED"],
    }
    packet["packet_sha256"] = hashlib.sha256(
        json.dumps(
            packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return packet


class OfficialReleaseSummaryBriefingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_orchestrator("official_release_summary_briefing_test")
        cls.consumer = load_module("official_release_summary_consumer_test", CONSUMER_PATH)

    def test_component_is_exact_facts_only_and_non_executable(self):
        row = self.module.build_official_release_summary_status(DECISION_AT)
        self.assertEqual(row["component_id"], "OFFICIAL_RELEASE_SUMMARY")
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(
            row["reason"],
            "OFFICIAL_FACTS_OBSERVED_INTERPRETATION_AND_RANKING_UNRATIFIED",
        )
        self.assertEqual(row["packet"]["counts"]["observed_summary_items"], 5)
        self.assertEqual(row["packet"]["observations"][0]["subject"], "SNDK")
        self.assertEqual(
            row["packet"]["observations"][0]["interpretation_status"],
            "UNDETERMINED",
        )
        self.assertFalse(row["decision_eligible"])
        self.assertFalse(row["action_eligible"])
        self.assertFalse(row["order_eligible"])
        self.assertFalse(row["authority"]["trading_authorized"])

    def test_component_uses_real_evidence_availability_not_wall_clock(self):
        row = self.module.build_official_release_summary_status(DECISION_AT)
        self.assertEqual(row["as_of_date"], "2026-08-20")
        self.assertEqual(row["generated_at"], "2026-08-20T21:59:19Z")
        self.assertEqual(row["available_at"], "2026-08-20T21:59:19Z")
        self.assertEqual(
            row["source_packet_sha256"], row["packet"]["packet_sha256"]
        )

    def test_production_packet_validator_is_called(self):
        original = self.module.OFFICIAL_RELEASE_SUMMARY.validate_packet
        with mock.patch.object(
            self.module.OFFICIAL_RELEASE_SUMMARY,
            "validate_packet",
            wraps=original,
        ) as validator:
            row = self.module.build_official_release_summary_status(DECISION_AT)
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(validator.call_count, 1)
        self.assertEqual(validator.call_args.kwargs["data_root"], ROOT / "data")

    def test_source_failure_is_isolated_as_degraded(self):
        with mock.patch.object(
            self.module.OFFICIAL_RELEASE_SUMMARY,
            "build_packet",
            side_effect=RuntimeError("synthetic-failure"),
        ):
            row = self.module.build_official_release_summary_status(DECISION_AT)
        self.assertEqual(row["status"], "DEGRADED")
        self.assertEqual(row["reason"], "RuntimeError:synthetic-failure")
        self.assertIsNone(row["packet"])

    def test_renderer_preserves_all_five_ordered_official_items(self):
        row = self.module.build_official_release_summary_status(DECISION_AT)
        rendered = "\n".join(self.module._format_component_detail(row))
        self.assertIn(
            "Sandisk Reports Fiscal Fourth Quarter 2026 Financial Results",
            rendered,
        )
        for ordinal in range(1, 6):
            self.assertEqual(rendered.count(f"official_summary_{ordinal}:"), 1)
        for item in row["packet"]["observations"][0]["summary_items"]:
            self.assertIn(item["text"], rendered)
        self.assertIn(
            "interpretation=UNDETERMINED ranking=UNRATIFIED", rendered
        )

    def test_contract_adds_component_and_bumps_exact_downstream_sources(self):
        daily = json.loads(
            (ROOT / "config/daily_orchestrator_contract.json").read_text()
        )
        flow = json.loads(
            (ROOT / "config/flow_first_briefing_contract.json").read_text()
        )
        cross = json.loads(
            (ROOT / "config/cross_asset_flow_evidence_contract.json").read_text()
        )
        self.assertEqual(daily["contract_version"], "daily_orchestrator/6")
        self.assertEqual(
            daily["component_order"].count("OFFICIAL_RELEASE_SUMMARY"), 1
        )
        self.assertEqual(flow["source_contract_version"], "daily_orchestrator/6")
        self.assertEqual(cross["source_contract_version"], "daily_orchestrator/6")

    def test_retrieval_consumer_accepts_previous_and_current_during_rollout(self):
        for version in (
            "daily_orchestrator/3",
            "daily_orchestrator/4",
            "daily_orchestrator/5",
            "daily_orchestrator/6",
        ):
            with self.subTest(version=version):
                self.consumer._validate_pinned_delivery_packet(
                    _pinned_delivery_packet(version), "2026-08-28", "morning"
                )

    def test_retrieval_consumer_rejects_unapproved_future_contract(self):
        with self.assertRaisesRegex(
            self.consumer.ScheduledConsumerError,
            "DELIVERY_PACKET_SCHEMA_UNSUPPORTED",
        ):
            self.consumer._validate_pinned_delivery_packet(
                _pinned_delivery_packet("daily_orchestrator/7"),
                "2026-08-28",
                "morning",
            )

    def test_authoritative_registry_contains_test_once(self):
        source = RUN_ALL_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            source.count('"test/test_official_release_summary_briefing.py"'), 1
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
