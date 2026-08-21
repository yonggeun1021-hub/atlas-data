#!/usr/bin/env python3
"""P5-04 deterministic Rule boundary evaluator regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "rules" / "deterministic_rule_evaluator.py"
BINDING_TEST = ROOT / "test" / "test_rule_evidence_binding.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("deterministic_rule_evaluator", SOURCE)
BINDING_FIXTURE = load_module("rule_evidence_binding_fixture", BINDING_TEST)
CONTRACT = MODULE.load_contract()
RULES = MODULE.load_rules()


def empty_binding_packet():
    return BINDING_FIXTURE.MODULE.build_packet(
        envelopes=[],
        bindings=BINDING_FIXTURE.binding_doc(),
        rules=BINDING_FIXTURE.MODULE.load_rules(),
        contract=BINDING_FIXTURE.MODULE.load_contract(),
    )


def available_binding_packet():
    return BINDING_FIXTURE.MODULE.build_packet(
        envelopes=[BINDING_FIXTURE.available_envelope()],
        bindings=BINDING_FIXTURE.binding_doc(BINDING_FIXTURE.binding()),
        rules=BINDING_FIXTURE.MODULE.load_rules(),
        contract=BINDING_FIXTURE.MODULE.load_contract(),
    )


def blocked_binding_packet():
    envelope = BINDING_FIXTURE.available_envelope(
        status="EVIDENCE_BLOCKED",
        consumable=False,
        blocked_by=["REVISION_AUTHORITY_UNRESOLVED"],
        reasons=["REVISION_AUTHORITY_UNRESOLVED"],
        observation=None,
    )
    return BINDING_FIXTURE.MODULE.build_packet(
        envelopes=[envelope],
        bindings=BINDING_FIXTURE.binding_doc(BINDING_FIXTURE.binding()),
        rules=BINDING_FIXTURE.MODULE.load_rules(),
        contract=BINDING_FIXTURE.MODULE.load_contract(),
    )


def refresh_binding(value):
    result = copy.deepcopy(value)
    result.pop("packet_sha256", None)
    result["packet_sha256"] = MODULE.payload_sha256(result)
    return result


def by_rule(packet):
    return {row["rule_id"]: row for row in packet["rules"]}


def write_json(path, value):
    path = Path(path)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


class DeterministicRuleEvaluatorTests(unittest.TestCase):
    def test_contract_exposes_four_statuses_but_forbids_pass_fail_authority(self):
        self.assertEqual(
            CONTRACT["result_statuses"],
            ["PASS", "FAIL", "UNKNOWN", "UNDEFINED"],
        )
        self.assertEqual(CONTRACT["repository_default_evaluation_spec"], "ABSENT")
        self.assertIn("PROHIBITED", CONTRACT["pass_fail_policy"])
        self.assertTrue(CONTRACT["authority"]["boundary_classification_only"])
        self.assertFalse(CONTRACT["authority"]["pass_fail_authorized"])
        self.assertFalse(CONTRACT["authority"]["production_authorized"])
        self.assertFalse(CONTRACT["authority"]["trading_authorized"])

    def test_no_binding_classifies_defined_rules_unknown_and_undefined_rules_undefined(self):
        result = MODULE.build_packet(empty_binding_packet(), RULES, CONTRACT)
        rows = by_rule(result)

        self.assertEqual(result["status"], "BOUNDARY_CLASSIFIED_PASS_FAIL_NOT_AUTHORIZED")
        self.assertEqual(rows["RULE-0001"]["definition_status"], "UNDEFINED")
        self.assertEqual(rows["RULE-0001"]["result"], "UNDEFINED")
        self.assertEqual(rows["RULE-0001"]["reasons"], ["RULE_DEFINITION_UNDEFINED"])
        self.assertEqual(rows["RULE-0002"]["definition_status"], "DEFINED")
        self.assertEqual(rows["RULE-0002"]["result"], "UNKNOWN")
        self.assertIn("RULE_SSOT_EVALUATOR_BLOCKED", rows["RULE-0002"]["reasons"])
        self.assertEqual(rows["RULE-0003"]["rule_ssot_evaluator_status"], "READY")
        self.assertEqual(rows["RULE-0003"]["result"], "UNKNOWN")
        self.assertIn("EVIDENCE_LINK_UNRESOLVED", rows["RULE-0003"]["reasons"])

    def test_available_ready_link_is_not_promoted_to_pass_or_fail(self):
        result = MODULE.build_packet(available_binding_packet(), RULES, CONTRACT)
        row = by_rule(result)["RULE-0021"]
        self.assertEqual(row["link_status"], "LINK_AVAILABLE")
        self.assertEqual(row["rule_ssot_evaluator_status"], "READY")
        self.assertEqual(row["result"], "UNDEFINED")
        self.assertEqual(row["reasons"], [
            "EVALUATION_SPEC_ABSENT",
            "EVALUATOR_AUTHORITY_NOT_RATIFIED",
            "RULE_REGISTRY_NOT_CONSUMABLE_BY_EVALUATOR",
        ])
        self.assertIsNone(row["evaluation_spec_sha256"])
        self.assertEqual(result["summary"]["PASS"], 0)
        self.assertEqual(result["summary"]["FAIL"], 0)
        self.assertFalse(result["authority"]["pass_fail_authorized"])

    def test_blocked_evidence_is_unknown_not_fail(self):
        result = MODULE.build_packet(blocked_binding_packet(), RULES, CONTRACT)
        row = by_rule(result)["RULE-0021"]
        self.assertEqual(row["link_status"], "LINK_BLOCKED")
        self.assertEqual(row["result"], "UNKNOWN")
        self.assertIn("EVIDENCE_LINK_BLOCKED", row["reasons"])
        self.assertIn("REVISION_AUTHORITY_UNRESOLVED", row["reasons"])
        self.assertEqual(result["summary"]["FAIL"], 0)

    def test_summary_covers_every_rule_and_only_unknown_or_undefined(self):
        result = MODULE.build_packet(available_binding_packet(), RULES, CONTRACT)
        self.assertEqual(result["summary"]["total_rules"], 25)
        self.assertEqual(
            result["summary"]["UNKNOWN"] + result["summary"]["UNDEFINED"],
            25,
        )
        self.assertEqual(
            {row["result"] for row in result["rules"]},
            {"UNKNOWN", "UNDEFINED"},
        )

    def test_output_preserves_rule_link_and_input_hash_lineage(self):
        binding = available_binding_packet()
        result = MODULE.build_packet(binding, RULES, CONTRACT)
        row = by_rule(result)["RULE-0021"]
        upstream = by_rule(binding)["RULE-0021"]
        self.assertEqual(row["condition_text_sha256"], upstream["condition_text_sha256"])
        self.assertEqual(
            row["evidence_reference_set_sha256"],
            MODULE.payload_sha256(upstream["evidence_references"]),
        )
        self.assertEqual(result["lineage"]["rule_registry_sha256"], MODULE.payload_sha256(RULES))
        self.assertEqual(result["lineage"]["binding_packet_sha256"], binding["packet_sha256"])
        digest = result.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(result))

    def test_tampered_binding_packet_and_recomputed_summary_drift_fail_closed(self):
        packet = available_binding_packet()
        tampered = copy.deepcopy(packet)
        tampered["rules"][0]["link_status"] = "LINK_AVAILABLE"
        with self.assertRaisesRegex(MODULE.DeterministicRuleEvaluatorError, "BINDING_PACKET_SHA_MISMATCH"):
            MODULE.build_packet(tampered, RULES, CONTRACT)

        summary = copy.deepcopy(packet)
        summary["summary"]["LINK_AVAILABLE"] = 2
        summary = refresh_binding(summary)
        with self.assertRaisesRegex(MODULE.DeterministicRuleEvaluatorError, "BINDING_SUMMARY_MISMATCH"):
            MODULE.build_packet(summary, RULES, CONTRACT)

    def test_binding_must_match_exact_rule_registry_hash_subject_and_condition(self):
        packet = available_binding_packet()
        wrong_hash = copy.deepcopy(packet)
        wrong_hash["inputs"]["rule_registry_sha256"] = "a" * 64
        wrong_hash = refresh_binding(wrong_hash)
        with self.assertRaisesRegex(MODULE.DeterministicRuleEvaluatorError, "BINDING_RULE_REGISTRY_SHA_MISMATCH"):
            MODULE.build_packet(wrong_hash, RULES, CONTRACT)

        wrong_row = copy.deepcopy(packet)
        row = next(item for item in wrong_row["rules"] if item["rule_id"] == "RULE-0021")
        row["condition_text_sha256"] = "b" * 64
        wrong_row = refresh_binding(wrong_row)
        with self.assertRaisesRegex(MODULE.DeterministicRuleEvaluatorError, "BINDING_RULE_INVALID"):
            MODULE.build_packet(wrong_row, RULES, CONTRACT)

    def test_upstream_linkage_authority_cannot_be_expanded(self):
        packet = available_binding_packet()
        packet["authority"]["rule_evaluation_authorized"] = True
        packet = refresh_binding(packet)
        with self.assertRaisesRegex(MODULE.DeterministicRuleEvaluatorError, "BINDING_PACKET_IDENTITY_INVALID"):
            MODULE.build_packet(packet, RULES, CONTRACT)

    def test_rule_registry_global_authority_hold_is_required(self):
        rules = copy.deepcopy(RULES)
        rules["consumable_by_evaluator"] = True
        with self.assertRaisesRegex(MODULE.DeterministicRuleEvaluatorError, "RULE_REGISTRY_AUTHORITY_INVALID"):
            MODULE.build_packet(available_binding_packet(), rules, CONTRACT)

    def test_contract_tamper_fails_closed(self):
        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["pass_fail_authorized"] = True
        with self.assertRaisesRegex(MODULE.DeterministicRuleEvaluatorError, "CONTRACT_FIELD_MISMATCH"):
            MODULE.build_packet(available_binding_packet(), RULES, contract)

    def test_output_is_deterministic_and_inputs_are_not_mutated(self):
        binding = available_binding_packet()
        binding_before = MODULE.canonical_json(binding)
        rules_before = MODULE.canonical_json(RULES)
        first = MODULE.build_packet(binding, RULES, CONTRACT)
        second = MODULE.build_packet(binding, RULES, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(binding), binding_before)
        self.assertEqual(MODULE.canonical_json(RULES), rules_before)

    def test_self_rehashed_output_semantic_tamper_fails_closed(self):
        packet = MODULE.build_packet(available_binding_packet(), RULES, CONTRACT)
        packet["summary"]["UNKNOWN"] += 1
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.DeterministicRuleEvaluatorError,
            "OUTPUT_SUMMARY_MISMATCH",
        ):
            MODULE.validate_packet(packet, RULES, CONTRACT)

    def test_source_has_no_network_or_evaluator_dependency(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in (
            "requests", "urllib", "socket", "http", "subprocess", "git",
            "notion", "evaluator",
        ):
            self.assertNotIn(prohibited, imported)

    def test_cli_writes_atomically_only_outside_repository(self):
        binding = available_binding_packet()
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            binding_path = write_json(tmp / "binding.json", binding)
            output_path = tmp / "nested" / "evaluation.json"
            self.assertEqual(MODULE.run(binding_path, output_path), 0)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["summary"]["PASS"], 0)
            self.assertEqual(list(output_path.parent.glob(".evaluation.json.*")), [])

            forbidden = ROOT / "data" / "deterministic_rule_evaluator_test.json"
            self.assertEqual(MODULE.run(binding_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
