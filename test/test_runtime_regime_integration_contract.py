#!/usr/bin/env python3
"""Independent P1 runtime-Regime -> P6-06 integration safety contract.

This baseline suite deliberately exercises only already-merged public contracts.
It must pass before a runtime-readiness producer exists, and it pins the negative
transitions that the later technical-wiring head must continue to satisfy.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION = load_module(
    "runtime_regime_integration_decision_authority",
    "regime/decision_authority.py",
)
DEFENSIVE = load_module(
    "runtime_regime_integration_defensive_action",
    "portfolio/defensive_action_decision.py",
)
ORCHESTRATOR = load_module(
    "runtime_regime_integration_daily_orchestrator",
    "briefing/daily_orchestrator.py",
)

AXES = tuple(DECISION.load_contract()["required_axes"])
GENERATED_AT = "2026-09-05T02:00:00Z"
AS_OF_DATE = "2026-09-05"


def defined_factor(axis: str, marker: str) -> dict:
    return {
        "status": "DEFINED",
        "observation_date": "2026-09-04",
        "available_at": "2026-09-05T00:20:00Z",
        "transform_version": f"regime_{axis.lower()}/v1",
        "evidence": {
            "uri": f"evidence/{axis.lower()}/2026-09-04.json",
            "sha256": marker * 64,
        },
        "warnings": [],
    }


def five_of_five_source() -> dict:
    factors = {
        axis: defined_factor(axis, marker)
        for axis, marker in zip(AXES, "abcde")
    }
    return DECISION.OUTPUT.build_unknown_output(
        "US", GENERATED_AT, factors
    )


def complete_replay_sequence() -> dict:
    positive = {
        axis: {"status": "DEFINED", "direction": "POSITIVE"}
        for axis in AXES
    }
    return {
        "schema_version": 1,
        "market": "US",
        "case_id": "integration-complete-replay",
        "steps": [
            {
                "packet_id": "integration-complete-1",
                "as_of_date": "2026-09-03",
                "axes": copy.deepcopy(positive),
            },
            {
                "packet_id": "integration-complete-2",
                "as_of_date": "2026-09-04",
                "axes": copy.deepcopy(positive),
            },
        ],
    }


def unavailable_bundle() -> tuple[dict, dict]:
    contract = DEFENSIVE.load_contract()
    packets = {name: None for name in contract["source_order"]}
    reasons = {
        name: [f"{name}_INTEGRATION_BASELINE_UNAVAILABLE"]
        for name in contract["source_order"]
    }
    return packets, reasons


def assert_runtime_authority_closed(case: unittest.TestCase, authority: dict) -> None:
    forbidden = {
        "runtime_classification_authorized",
        "runtime_binding_authorized",
        "strategy_eligibility_authorized",
        "stage_authorized",
        "buy_authorized",
        "action_authorized",
        "defensive_action_authorized",
        "action_proposal_authorized",
        "order_authorized",
        "capital_authorized",
        "production_authorized",
        "trading_authorized",
    }
    for key in forbidden & set(authority):
        case.assertIs(authority[key], False, key)


class RuntimeRegimeIntegrationContractTest(unittest.TestCase):
    def test_five_of_five_coverage_stays_unknown_and_policy_blocked(self):
        source = five_of_five_source()
        coverage = DECISION.COVERAGE.evaluate_minimum_coverage(source)
        packet = DECISION.evaluate_decision_authority(source, coverage)

        self.assertEqual(packet["coverage"]["ratio"], "5/5")
        self.assertTrue(packet["coverage"]["minimum_coverage_met"])
        self.assertEqual(packet["decision_status"], "BLOCKED_POLICY_UNRATIFIED")
        self.assertFalse(packet["policy_gate"]["classification_eligible"])
        self.assertFalse(packet["policy_gate"]["replay_eligible"])
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertEqual(packet["direction"], "UNKNOWN")
        self.assertIsNone(packet["confidence"])
        assert_runtime_authority_closed(self, packet["authority"])

    def test_complete_replay_is_not_runtime_availability(self):
        report = DECISION.replay_common_v1(complete_replay_sequence())

        # The replay mechanism can deterministically classify its own SHADOW
        # sequence.  Those facts are deliberately not a runtime source packet.
        self.assertEqual(report["final_regime"], "RISK_ON")
        self.assertEqual(
            report["contract_mode"],
            "SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED",
        )
        self.assertEqual(report["pit_replay_acceptance"], "NOT_ACCEPTED")
        self.assertFalse(report["market_specific_signed_normalization_inherited"])
        assert_runtime_authority_closed(self, report["authority"])

    def test_complete_coverage_cannot_emit_signed_normalization_or_replay_step(self):
        packet = DECISION.normalize_signed_axes(five_of_five_source())

        self.assertEqual(packet["coverage"]["ratio"], "5/5")
        self.assertEqual(
            packet["normalization_status"],
            "BLOCKED_SIGNED_NORMALIZATION_UNRATIFIED",
        )
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertEqual(packet["direction"], "UNKNOWN")
        self.assertIsNone(packet["confidence"])
        self.assertFalse(packet["replay_step_emitted"])
        self.assertIsNone(packet["common_v1_replay_step"])
        self.assertTrue(
            all(row["signed_direction"] is None for row in packet["axes"].values())
        )
        assert_runtime_authority_closed(self, packet["authority"])

    def test_fake_normalization_policy_state_cannot_promote_output(self):
        forged = DECISION.load_signed_axis_policy()
        forged["markets"]["US"].update(
            {
                "signed_normalization_policy_status": "RATIFIED",
                "acceptance_status": "ACCEPTED",
                "pit_replay_acceptance": "ACCEPTED",
            }
        )
        forged["common_v1"]["market_specific_normalization_inherited"] = True
        forged["common_v1"]["pit_replay_acceptance"] = "ACCEPTED"

        try:
            packet = DECISION.normalize_signed_axes(five_of_five_source(), forged)
        except DECISION.DecisionAuthorityError:
            # Rejecting an injected policy object is also a valid fail-closed
            # outcome for a later hardened implementation.
            return
        self.assertEqual(
            packet["normalization_status"],
            "BLOCKED_SIGNED_NORMALIZATION_UNRATIFIED",
        )
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertFalse(packet["replay_step_emitted"])
        self.assertTrue(
            all(row["normalized_value"] is None for row in packet["axes"].values())
        )
        assert_runtime_authority_closed(self, packet["authority"])

    def test_registry_edit_cannot_self_ratify_normalization_or_pit_acceptance(self):
        registry = DECISION.load_json(DECISION.REGISTRY_PATH)
        registry["markets"]["US"]["signed_normalization_policy"] = {
            "status": "RATIFIED"
        }
        registry["markets"]["US"]["pit_replay_acceptance"] = "ACCEPTED"
        registry["markets"]["US"]["acceptance_status"] = "ACCEPTED"

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "forged-registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(
                DECISION.DecisionAuthorityError,
                "SIGNED_AXIS_POLICY_UNIMPLEMENTED|SIGNED_AXIS_BINDING_INVALID",
            ):
                DECISION.load_signed_axis_policy(registry_path=path)

    def test_p6_baseline_is_blocked_and_does_not_infer_no_action(self):
        packets, reasons = unavailable_bundle()
        packet = DEFENSIVE.build_packet(
            packets,
            reasons,
            AS_OF_DATE,
            GENERATED_AT,
        )

        self.assertEqual(packet["status"], "DEFENSIVE_ACTION_READINESS_BLOCKED")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(packet["summary"]["available_source_count"], 0)
        self.assertIsNone(packet["summary"]["no_action"])
        self.assertTrue(all(row["eligible"] is None for row in packet["decisions"]))
        self.assertIsNone(packet["selected_action"])
        self.assertIsNone(packet["action_proposal"])
        self.assertEqual(packet["order_intents"], [])
        assert_runtime_authority_closed(self, packet["authority"])

    def test_replay_coverage_and_normalization_packets_cannot_fill_p1_runtime_slot(self):
        source = five_of_five_source()
        coverage = DECISION.COVERAGE.evaluate_minimum_coverage(source)
        candidates = (
            DECISION.evaluate_decision_authority(source, coverage),
            DECISION.normalize_signed_axes(source),
            DECISION.replay_common_v1(complete_replay_sequence()),
        )

        for candidate in candidates:
            with self.subTest(contract=candidate["contract_version"]):
                packets, reasons = unavailable_bundle()
                packets["P1_REGIME_DECISION"] = candidate
                reasons["P1_REGIME_DECISION"] = []
                with self.assertRaisesRegex(
                    DEFENSIVE.DefensiveActionDecisionError,
                    (
                        "SOURCE_PACKET_NOT_YET_SUPPORTED:P1_REGIME_DECISION|"
                        "SOURCE_IDENTITY_INVALID:P1_REGIME_DECISION|"
                        "SOURCE_SEMANTIC_INVALID:P1_REGIME_DECISION"
                    ),
                ):
                    DEFENSIVE.build_packet(
                        packets,
                        reasons,
                        AS_OF_DATE,
                        GENERATED_AT,
                    )

    def test_daily_consumer_preserves_p1_unavailable_and_blocked_decision(self):
        contract = DEFENSIVE.load_contract()
        rows = {
            name: ORCHESTRATOR.component_row(
                name,
                "UNAVAILABLE",
                f"{name}_INTEGRATION_BASELINE_UNAVAILABLE",
            )
            for name in contract["source_order"]
            if name not in contract["unavailable_only_source_slots"]
        }
        row = ORCHESTRATOR.build_defensive_action_decision(
            rows, AS_OF_DATE, GENERATED_AT
        )
        packet = row["packet"]
        sources = {source["name"]: source for source in packet["sources"]}

        self.assertEqual(row["status"], "PENDING")
        self.assertTrue(row["validated"])
        self.assertEqual(sources["P1_REGIME_DECISION"]["availability"], "UNAVAILABLE")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertIsNone(packet["selected_action"])
        self.assertEqual(packet["order_intents"], [])
        assert_runtime_authority_closed(self, packet["authority"])


if __name__ == "__main__":
    unittest.main()
