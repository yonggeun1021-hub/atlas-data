#!/usr/bin/env python3
"""P8-02 Unified Decision Contract regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "unified_decision_contract.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("unified_decision_contract", SOURCE)
REGIME_FIXTURE = load_module(
    "unified_regime_fixture", ROOT / "test" / "test_three_market_regime_header.py"
)
ROTATION_FIXTURE = load_module(
    "unified_rotation_fixture", ROOT / "test" / "test_rotation_discovery_briefing.py"
)
RULE_FIXTURE = load_module(
    "unified_rule_fixture", ROOT / "test" / "test_deterministic_rule_evaluator.py"
)
BUCKET_FIXTURE = load_module(
    "unified_bucket_fixture", ROOT / "test" / "test_bucket_membership.py"
)
CURRENCY_FIXTURE = load_module(
    "unified_currency_fixture", ROOT / "test" / "test_currency_exposure.py"
)
ACTION_FIXTURE = load_module(
    "unified_action_fixture", ROOT / "test" / "test_ready_signal_order_boundary.py"
)
CONTRACT = MODULE.load_contract()


def components():
    return {
        "REGIME": REGIME_FIXTURE.MODULE.build_header(
            REGIME_FIXTURE.sources(), "morning", "2026-08-21T01:10:00Z",
            REGIME_FIXTURE.CONTRACT,
        ),
        "ROTATION_DISCOVERY": ROTATION_FIXTURE.MODULE.build_briefing(
            ROTATION_FIXTURE.empty_ledger(), ROTATION_FIXTURE.records(),
            ROTATION_FIXTURE.bindings(), "morning", "2026-08-21T02:00:00Z",
            ROTATION_FIXTURE.CONTRACT,
        ),
        "RULE": RULE_FIXTURE.MODULE.build_packet(
            RULE_FIXTURE.empty_binding_packet(),
            RULE_FIXTURE.RULES,
            RULE_FIXTURE.CONTRACT,
        ),
        "PORTFOLIO_BUCKET": BUCKET_FIXTURE.MODULE.build_packet(
            BUCKET_FIXTURE.assignment_set(),
            BUCKET_FIXTURE.ratified_constitution(),
            "2026-08-21",
            BUCKET_FIXTURE.CONTRACT,
        ),
        "PORTFOLIO_CURRENCY": CURRENCY_FIXTURE.MODULE.build_packet(
            CURRENCY_FIXTURE.asset_master(),
            CURRENCY_FIXTURE.snapshot(),
            CURRENCY_FIXTURE.CONTRACT,
        ),
        "ACTION_BOUNDARY": ACTION_FIXTURE.MODULE.build_packet(
            ACTION_FIXTURE.input_packet(), ACTION_FIXTURE.CONTRACT
        ),
    }


def reasons():
    return {name: [] for name in CONTRACT["component_order"]}


def envelope(source=None, unavailable=None):
    return {
        "decision_date": "2026-08-21",
        "slot": "morning",
        "generated_at": "2026-08-21T02:10:00Z",
        "components": components() if source is None else source,
        "unavailable_reasons": reasons() if unavailable is None else unavailable,
    }


class UnifiedDecisionContractTests(unittest.TestCase):
    def test_contract_pins_full_pipeline_and_closes_action_authority(self):
        self.assertEqual(
            CONTRACT["pipeline_order"],
            ["REGIME", "ROTATION_DISCOVERY", "RULE", "PORTFOLIO"],
        )
        self.assertEqual(CONTRACT["component_order"], [
            "REGIME", "ROTATION_DISCOVERY", "RULE", "PORTFOLIO_BUCKET",
            "PORTFOLIO_CURRENCY", "ACTION_BOUNDARY",
        ])
        self.assertTrue(CONTRACT["authority"]["daily_decision_assembly_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "daily_decision_assembly_only":
                self.assertFalse(value, key)

    def test_all_upstream_results_are_one_hash_linked_daily_object(self):
        source = components()
        packet = MODULE.build_packet(
            source, reasons(), "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", CONTRACT,
        )
        self.assertEqual(packet["decision_id"], "atlas-2026-08-21-morning")
        self.assertTrue(packet["summary"]["pipeline_complete"])
        self.assertEqual(packet["summary"]["available_component_count"], 6)
        self.assertEqual(
            [row["stage"] for row in packet["pipeline"]],
            ["REGIME", "ROTATION_DISCOVERY", "RULE", "PORTFOLIO"],
        )
        by_name = {row["component"]: row for row in packet["components"]}
        for name, value in source.items():
            self.assertEqual(by_name[name]["source_packet"], value)
            self.assertEqual(by_name[name]["source_packet_sha256"], value["packet_sha256"])
            self.assertEqual(
                packet["lineage"]["component_packet_sha256"][name],
                value["packet_sha256"],
            )

    def test_complete_inputs_still_create_no_action_size_or_order(self):
        packet = MODULE.build_packet(
            components(), reasons(), "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", CONTRACT,
        )
        self.assertEqual(packet["decision"]["state"], "NO_ACTION_AUTHORIZED")
        self.assertIsNone(packet["decision"]["action"])
        self.assertIsNone(packet["decision"]["entry_trigger"])
        self.assertIsNone(packet["decision"]["position_size"])
        self.assertIsNone(packet["decision"]["order_intent"])
        self.assertIn(
            "RULE_PASS_FAIL_NOT_AUTHORIZED",
            packet["decision"]["blocking_reasons"],
        )

    def test_missing_component_is_explicit_and_blocks_downstream_gate(self):
        source = components()
        source["ROTATION_DISCOVERY"] = None
        unavailable = reasons()
        unavailable["ROTATION_DISCOVERY"] = ["SOURCE_PACKET_NOT_PROVIDED"]
        packet = MODULE.build_packet(
            source, unavailable, "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", CONTRACT,
        )
        row = packet["components"][1]
        self.assertEqual(row["availability"], "UNAVAILABLE")
        self.assertIsNone(row["source_packet"])
        self.assertEqual(row["unavailable_reasons"], ["SOURCE_PACKET_NOT_PROVIDED"])
        self.assertFalse(packet["summary"]["pipeline_complete"])
        by_stage = {stage["stage"]: stage for stage in packet["pipeline"]}
        self.assertFalse(by_stage["RULE"]["upstream_gate_satisfied"])
        self.assertFalse(by_stage["PORTFOLIO"]["upstream_gate_satisfied"])
        self.assertIn(
            "MISSING_COMPONENT:ROTATION_DISCOVERY",
            packet["decision"]["blocking_reasons"],
        )

    def test_partial_portfolio_is_distinct_from_complete_portfolio(self):
        source = components()
        source["PORTFOLIO_BUCKET"] = None
        unavailable = reasons()
        unavailable["PORTFOLIO_BUCKET"] = ["CONSTITUTION_B1_NOT_RATIFIED"]
        packet = MODULE.build_packet(
            source, unavailable, "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", CONTRACT,
        )
        portfolio = packet["pipeline"][-1]
        self.assertEqual(portfolio["state"], "PARTIAL")
        self.assertEqual(portfolio["missing_components"], ["PORTFOLIO_BUCKET"])
        self.assertFalse(portfolio["upstream_gate_satisfied"])

    def test_self_rehashed_identity_field_and_source_time_tamper_fail_closed(self):
        identity = components()
        identity["RULE"]["status"] = "PASS"
        identity["RULE"]["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in identity["RULE"].items()
            if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.UnifiedDecisionContractError, "COMPONENT_IDENTITY_INVALID:RULE"
        ):
            MODULE.build_packet(
                identity, reasons(), "2026-08-21", "morning",
                "2026-08-21T02:10:00Z", CONTRACT,
            )

        authority = components()
        authority["RULE"]["authority"]["pass_fail_authorized"] = True
        authority["RULE"]["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in authority["RULE"].items()
            if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.UnifiedDecisionContractError, "COMPONENT_IDENTITY_INVALID:RULE"
        ):
            MODULE.build_packet(
                authority, reasons(), "2026-08-21", "morning",
                "2026-08-21T02:10:00Z", CONTRACT,
            )

        future = components()
        future["PORTFOLIO_CURRENCY"]["available_at"] = "2026-08-21T02:11:00Z"
        future["PORTFOLIO_CURRENCY"]["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in future["PORTFOLIO_CURRENCY"].items()
            if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.UnifiedDecisionContractError,
            "COMPONENT_FROM_FUTURE:PORTFOLIO_CURRENCY",
        ):
            MODULE.build_packet(
                future, reasons(), "2026-08-21", "morning",
                "2026-08-21T02:10:00Z", CONTRACT,
            )

    def test_all_self_rehashed_component_semantic_tamper_fails_closed(self):
        summary_fields = {
            "REGIME": "market_count",
            "ROTATION_DISCOVERY": "rotation_change_count",
            "RULE": "UNKNOWN",
            "PORTFOLIO_BUCKET": "subject_count",
            "PORTFOLIO_CURRENCY": "position_count",
            "ACTION_BOUNDARY": "ready_count",
        }
        for name, field in summary_fields.items():
            with self.subTest(component=name):
                source = components()
                source[name]["summary"][field] += 1
                source[name]["packet_sha256"] = MODULE.payload_sha256({
                    key: value
                    for key, value in source[name].items()
                    if key != "packet_sha256"
                })
                with self.assertRaisesRegex(
                    MODULE.UnifiedDecisionContractError,
                    f"COMPONENT_SEMANTIC_INVALID:{name}",
                ):
                    MODULE.build_packet(
                        source, reasons(), "2026-08-21", "morning",
                        "2026-08-21T02:10:00Z", CONTRACT,
                    )

    def test_regime_projection_drift_is_rejected_before_unified_assembly(self):
        source = components()
        forged = copy.deepcopy(source["REGIME"])
        forged["markets"][0]["coverage"]["defined_count"] = 1
        forged["markets"][0]["coverage"]["ratio"] = "1/5"
        forged["markets"][0]["source_sha256"] = "b" * 64
        forged.pop("packet_sha256")
        forged["packet_sha256"] = MODULE.payload_sha256(forged)
        source["REGIME"] = forged
        with self.assertRaisesRegex(
            MODULE.UnifiedDecisionContractError,
            "COMPONENT_SEMANTIC_INVALID:REGIME:.*HEADER_DERIVATION_MISMATCH",
        ):
            MODULE.build_packet(
                source, reasons(), "2026-08-21", "morning",
                "2026-08-21T02:10:00Z", CONTRACT,
            )

    def test_forged_bucket_membership_component_is_rejected_before_assembly(self):
        # Mirrors the bucket_membership.py fix: a fabricated membership row for
        # an asset the CIO never actually ratified into the assignment set,
        # injected directly into PORTFOLIO_BUCKET's active_memberships, must
        # never reach the assembled Unified Decision packet.
        source = components()
        forged = copy.deepcopy(source["PORTFOLIO_BUCKET"])
        fake_row = copy.deepcopy(forged["active_memberships"][0])
        fake_row["asset_id"] = "US:XNAS:NEVERRATIFIED"
        forged["active_memberships"].append(fake_row)
        forged["summary"]["subject_count"] = len(forged["active_memberships"])
        forged["summary"]["active_membership_count"] = len(forged["active_memberships"])
        forged.pop("packet_sha256")
        forged["packet_sha256"] = MODULE.payload_sha256(forged)
        source["PORTFOLIO_BUCKET"] = forged
        with self.assertRaisesRegex(
            MODULE.UnifiedDecisionContractError,
            "COMPONENT_SEMANTIC_INVALID:PORTFOLIO_BUCKET:.*OUTPUT_DERIVATION_MISMATCH",
        ):
            MODULE.build_packet(
                source, reasons(), "2026-08-21", "morning",
                "2026-08-21T02:10:00Z", CONTRACT,
            )

    def test_component_slot_and_missing_reason_contracts_fail_closed(self):
        wrong_slot = components()
        wrong_slot["REGIME"] = REGIME_FIXTURE.MODULE.build_header(
            REGIME_FIXTURE.sources(), "evening", "2026-08-21T01:10:00Z",
            REGIME_FIXTURE.CONTRACT,
        )
        with self.assertRaisesRegex(
            MODULE.UnifiedDecisionContractError, "COMPONENT_SLOT_MISMATCH:REGIME"
        ):
            MODULE.build_packet(
                wrong_slot, reasons(), "2026-08-21", "morning",
                "2026-08-21T02:10:00Z", CONTRACT,
            )
        missing = components()
        missing["RULE"] = None
        with self.assertRaisesRegex(
            MODULE.UnifiedDecisionContractError, "UNAVAILABLE_REASONS_INVALID:RULE"
        ):
            MODULE.build_packet(
                missing, reasons(), "2026-08-21", "morning",
                "2026-08-21T02:10:00Z", CONTRACT,
            )

    def test_build_is_deterministic_and_inputs_are_immutable(self):
        source = components()
        unavailable = reasons()
        before = MODULE.canonical_json([source, unavailable])
        first = MODULE.build_packet(
            source, unavailable, "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", CONTRACT,
        )
        second = MODULE.build_packet(
            source, unavailable, "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", CONTRACT,
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json([source, unavailable]), before)

    def test_output_summary_lineage_decision_and_digest_tamper_fail_closed(self):
        original = MODULE.build_packet(
            components(), reasons(), "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", CONTRACT,
        )
        variants = []
        summary = copy.deepcopy(original)
        summary["summary"]["available_component_count"] = 5
        variants.append((summary, "SUMMARY_MISMATCH"))
        lineage = copy.deepcopy(original)
        lineage["lineage"]["component_packet_sha256"]["RULE"] = "0" * 64
        variants.append((lineage, "LINEAGE_MISMATCH"))
        decision = copy.deepcopy(original)
        decision["decision"]["action"] = "BUY"
        variants.append((decision, "DECISION_MISMATCH"))
        digest = copy.deepcopy(original)
        digest["packet_sha256"] = "0" * 64
        variants.append((digest, "PACKET_SHA_MISMATCH"))
        for value, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.UnifiedDecisionContractError, error
            ):
                MODULE.validate_packet(value, CONTRACT)

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
            temp = Path(tmp)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps(envelope()), encoding="utf-8")
            output = temp / "out" / "decision.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertEqual(
                json.loads(output.read_text())["decision"]["state"],
                "NO_ACTION_AUTHORIZED",
            )
            forbidden = ROOT / "data" / "unified_decision_contract_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
