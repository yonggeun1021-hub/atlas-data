#!/usr/bin/env python3
"""P5-05 P5-03→P5-04 negative/mutation integration regression."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "config" / "rule_evaluator_mutation_matrix.json"
BINDING_TEST = ROOT / "test" / "test_rule_evidence_binding.py"
EVALUATOR_PATH = ROOT / "rules" / "deterministic_rule_evaluator.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BINDING = load_module("mutation_binding_fixture", BINDING_TEST)
EVALUATOR = load_module("mutation_evaluator", EVALUATOR_PATH)
RULES = EVALUATOR.load_rules()
CONTRACT = EVALUATOR.load_contract()


def link_packet(envelopes=None, binding_rows=None):
    return BINDING.MODULE.build_packet(
        envelopes=[] if envelopes is None else envelopes,
        bindings=BINDING.binding_doc(*(binding_rows or [])),
        rules=BINDING.MODULE.load_rules(),
        contract=BINDING.MODULE.load_contract(),
    )


def available_packet(envelope=None):
    return link_packet(
        [BINDING.available_envelope() if envelope is None else envelope],
        [BINDING.binding()],
    )


def refresh_packet(packet):
    result = copy.deepcopy(packet)
    result.pop("packet_sha256", None)
    result["packet_sha256"] = EVALUATOR.payload_sha256(result)
    return result


def evaluated_row(packet, rule_id="RULE-0021"):
    result = EVALUATOR.build_packet(packet, RULES, CONTRACT)
    row = next(item for item in result["rules"] if item["rule_id"] == rule_id)
    return result, row


def assert_no_pass_fail(case, result):
    case.assertEqual(result["summary"]["PASS"], 0)
    case.assertEqual(result["summary"]["FAIL"], 0)
    case.assertFalse(result["authority"]["pass_fail_authorized"])


class RuleEvaluatorMutationTests(unittest.TestCase):
    def test_matrix_registry_is_exact_test_only_and_closes_authority(self):
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        self.assertEqual(matrix["schema_version"], 1)
        self.assertEqual(
            matrix["contract_version"], "rule_evaluator_mutation_matrix/1"
        )
        self.assertEqual(matrix["targets"], [
            "rule_evidence_binding/2",
            "deterministic_rule_evaluator/2",
        ])
        self.assertTrue(matrix["pass_fail_must_remain_zero"])
        self.assertEqual(len(matrix["scenarios"]), 13)
        codes = [item["code"] for item in matrix["scenarios"]]
        self.assertEqual(codes, list(dict.fromkeys(codes)))
        self.assertEqual(set(codes), {
            "EVIDENCE_ABSENT", "EVIDENCE_BLOCKED",
            "EVIDENCE_LINEAGE_MISSING", "EVIDENCE_CONSUMABLE_CONFLICT",
            "OBSERVATION_VALUE_MUTATION", "DUPLICATE_ENVELOPE_KEY",
            "BINDING_SUBJECT_MISMATCH", "HIDDEN_SELECTION_MODE",
            "BINDING_PACKET_DIGEST_TAMPER", "CONDITION_HASH_DRIFT",
            "RULE_REGISTRY_HASH_DRIFT", "LINKAGE_AUTHORITY_EXPANSION",
            "REGISTRY_EVALUATOR_AUTHORITY_EXPANSION",
        })
        self.assertTrue(matrix["authority"]["test_only"])
        self.assertTrue(
            all(
                value is False
                for key, value in matrix["authority"].items()
                if key != "test_only"
            )
        )

    def test_absent_evidence_stays_unknown_and_never_becomes_fail(self):
        result, row = evaluated_row(
            link_packet([], [BINDING.binding()])
        )
        self.assertEqual(row["link_status"], "LINK_UNRESOLVED")
        self.assertEqual(row["result"], "UNKNOWN")
        self.assertIn("EVIDENCE_REFERENCE_ABSENT", row["reasons"])
        assert_no_pass_fail(self, result)

    def test_blocked_evidence_stays_unknown_and_preserves_reason(self):
        envelope = BINDING.available_envelope(
            status="EVIDENCE_BLOCKED",
            consumable=False,
            blocked_by=["REVISION_AUTHORITY_UNRESOLVED"],
            reasons=["REVISION_AUTHORITY_UNRESOLVED"],
            observation=None,
        )
        result, row = evaluated_row(available_packet(envelope))
        self.assertEqual(row["link_status"], "LINK_BLOCKED")
        self.assertEqual(row["result"], "UNKNOWN")
        self.assertIn("REVISION_AUTHORITY_UNRESOLVED", row["reasons"])
        assert_no_pass_fail(self, result)

    def test_missing_lineage_blocks_link_and_stays_unknown(self):
        envelope = BINDING.available_envelope()
        envelope["source_identity"]["available_at"] = None
        result, row = evaluated_row(available_packet(envelope))
        self.assertEqual(row["link_status"], "LINK_BLOCKED")
        self.assertEqual(row["result"], "UNKNOWN")
        self.assertTrue(
            any("EVIDENCE_LINEAGE_INCOMPLETE" in item for item in row["reasons"])
        )
        assert_no_pass_fail(self, result)

    def test_consumable_state_conflict_blocks_link_and_stays_unknown(self):
        envelope = BINDING.available_envelope(consumable=False)
        result, row = evaluated_row(available_packet(envelope))
        self.assertEqual(row["link_status"], "LINK_BLOCKED")
        self.assertEqual(row["result"], "UNKNOWN")
        assert_no_pass_fail(self, result)

    def test_observation_value_mutation_changes_evidence_hash_but_not_authority(self):
        first = BINDING.available_envelope()
        second = copy.deepcopy(first)
        second["observation"]["numeric_value"] = "999999"
        packet_a = available_packet(first)
        packet_b = available_packet(second)
        result_a, row_a = evaluated_row(packet_a)
        result_b, row_b = evaluated_row(packet_b)
        self.assertNotEqual(
            result_a["lineage"]["evidence_set_sha256"],
            result_b["lineage"]["evidence_set_sha256"],
        )
        self.assertEqual(row_a["result"], "UNDEFINED")
        self.assertEqual(row_b["result"], "UNDEFINED")
        self.assertEqual(row_a["reasons"], row_b["reasons"])
        assert_no_pass_fail(self, result_a)
        assert_no_pass_fail(self, result_b)

    def test_duplicate_envelope_subject_mismatch_and_hidden_selection_are_rejected(self):
        duplicate = BINDING.available_envelope()
        wrong_subject = BINDING.binding(subject="TSM")
        hidden = BINDING.binding()
        hidden["selection_mode"] = "FIRST_AVAILABLE"
        cases = [
            (
                lambda: link_packet([duplicate, copy.deepcopy(duplicate)], [BINDING.binding()]),
                "ENVELOPE_KEY_DUPLICATE",
            ),
            (
                lambda: link_packet([BINDING.available_envelope()], [wrong_subject]),
                "BINDING_SUBJECT_MISMATCH",
            ),
            (
                lambda: link_packet([BINDING.available_envelope()], [hidden]),
                "BINDING_SELECTION_MODE_INVALID",
            ),
        ]
        for operation, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                BINDING.MODULE.RuleEvidenceBindingError, error
            ):
                operation()

    def test_binding_packet_digest_tamper_is_rejected(self):
        packet = available_packet()
        packet["rules"][0]["link_status"] = "LINK_AVAILABLE"
        with self.assertRaisesRegex(
            EVALUATOR.DeterministicRuleEvaluatorError,
            "BINDING_PACKET_SHA_MISMATCH",
        ):
            EVALUATOR.build_packet(packet, RULES, CONTRACT)

    def test_condition_and_registry_hash_drift_are_rejected_after_rehash(self):
        condition = available_packet()
        condition["rules"][0]["condition_text_sha256"] = "a" * 64
        condition = refresh_packet(condition)
        with self.assertRaisesRegex(
            EVALUATOR.DeterministicRuleEvaluatorError,
            "BINDING_RULE_INVALID",
        ):
            EVALUATOR.build_packet(condition, RULES, CONTRACT)

        registry = available_packet()
        registry["inputs"]["rule_registry_sha256"] = "b" * 64
        registry = refresh_packet(registry)
        with self.assertRaisesRegex(
            EVALUATOR.DeterministicRuleEvaluatorError,
            "BINDING_RULE_REGISTRY_SHA_MISMATCH",
        ):
            EVALUATOR.build_packet(registry, RULES, CONTRACT)

    def test_authority_expansion_at_linkage_or_registry_is_rejected(self):
        linkage = available_packet()
        linkage["authority"]["rule_evaluation_authorized"] = True
        linkage = refresh_packet(linkage)
        with self.assertRaisesRegex(
            EVALUATOR.DeterministicRuleEvaluatorError,
            "BINDING_PACKET_IDENTITY_INVALID",
        ):
            EVALUATOR.build_packet(linkage, RULES, CONTRACT)

        registry = copy.deepcopy(RULES)
        registry["consumable_by_evaluator"] = True
        with self.assertRaisesRegex(
            EVALUATOR.DeterministicRuleEvaluatorError,
            "RULE_REGISTRY_AUTHORITY_INVALID",
        ):
            EVALUATOR.build_packet(available_packet(), registry, CONTRACT)

    def test_negative_pipeline_is_byte_deterministic_and_input_immutable(self):
        packet = available_packet()
        before = EVALUATOR.canonical_json(packet)
        first = EVALUATOR.build_packet(packet, RULES, CONTRACT)
        second = EVALUATOR.build_packet(packet, RULES, CONTRACT)
        self.assertEqual(
            EVALUATOR.canonical_json(first), EVALUATOR.canonical_json(second)
        )
        self.assertEqual(EVALUATOR.canonical_json(packet), before)
        assert_no_pass_fail(self, first)


if __name__ == "__main__":
    unittest.main()
