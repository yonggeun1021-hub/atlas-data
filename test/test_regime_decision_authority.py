#!/usr/bin/env python3
"""P1-COM-05 Regime decision-authority and draft-policy regressions."""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "decision_authority.py"
SPEC = importlib.util.spec_from_file_location("regime_decision_authority", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()
OUTPUT = MODULE.OUTPUT
COVERAGE = MODULE.COVERAGE

POLICY_SCRIPT = ROOT / "regime" / "policy_candidate.py"
POLICY_SPEC = importlib.util.spec_from_file_location(
    "regime_policy_candidate", POLICY_SCRIPT
)
POLICY = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(POLICY)
POLICY_CONTRACT = POLICY.load_contract()


def defined(axis, character):
    return {
        "status": "DEFINED",
        "observation_date": "2026-08-26",
        "available_at": "2026-08-27T00:20:00Z",
        "transform_version": f"regime_{axis.lower()}/v1",
        "evidence": {
            "uri": f"evidence/{axis.lower()}/2026-08-26.json",
            "sha256": character * 64,
        },
        "warnings": [],
    }


def source(factors=None):
    return OUTPUT.build_unknown_output(
        "CRYPTO",
        "2026-08-27T01:00:00Z",
        factors,
    )


def full_source():
    return source(
        {
            axis: defined(axis, character)
            for axis, character in zip(CONTRACT["required_axes"], "abcde")
        }
    )


def gate(value):
    return COVERAGE.evaluate_minimum_coverage(value)


def policy_assessments(
    value,
    orientations=None,
    changes=None,
):
    orientations = {} if orientations is None else orientations
    changes = {} if changes is None else changes
    return {
        axis: {
            "axis": axis,
            "orientation": orientations.get(axis, "NEUTRAL"),
            "change": changes.get(axis, "STABLE"),
            "normalization_version": f"regime_{axis.lower()}_orientation/v1",
            "source_evidence_sha256": value["factor_results"][axis]["evidence"][
                "sha256"
            ],
            "warnings": [],
        }
        for axis in POLICY_CONTRACT["required_axes"]
    }


class RegimeDecisionAuthorityTest(unittest.TestCase):
    def test_contract_pins_unratified_policy_boundary(self):
        self.assertEqual(
            CONTRACT["contract_version"], "regime_decision_authority/v1"
        )
        self.assertEqual(CONTRACT["repository_policy_registry_status"], "ABSENT")
        self.assertEqual(len(CONTRACT["required_policy_components"]), 9)
        self.assertEqual(
            CONTRACT["allowed_decision_statuses"],
            ["BLOCKED_COVERAGE", "BLOCKED_POLICY_UNRATIFIED"],
        )
        self.assertTrue(
            CONTRACT["authority"]["decision_boundary_validation_authorized"]
        )
        for key, value in CONTRACT["authority"].items():
            if key != "decision_boundary_validation_authorized":
                self.assertFalse(value, key)

    def test_missing_axis_is_distinct_from_missing_policy(self):
        partial = source({"TREND": defined("TREND", "a")})
        blocked_coverage = MODULE.evaluate_decision_authority(partial, gate(partial))
        complete = full_source()
        blocked_policy = MODULE.evaluate_decision_authority(complete, gate(complete))

        self.assertEqual(blocked_coverage["decision_status"], "BLOCKED_COVERAGE")
        self.assertEqual(blocked_coverage["coverage"]["ratio"], "1/5")
        self.assertIn("MINIMUM_COVERAGE_NOT_MET", blocked_coverage["reasons"])
        self.assertEqual(
            blocked_policy["decision_status"], "BLOCKED_POLICY_UNRATIFIED"
        )
        self.assertEqual(blocked_policy["coverage"]["ratio"], "5/5")
        self.assertEqual(
            blocked_policy["reasons"],
            [
                CONTRACT["policy_reason_codes"][component]
                for component in CONTRACT["required_policy_components"]
            ],
        )

    def test_five_of_five_never_opens_classification(self):
        complete = full_source()
        decision = MODULE.evaluate_decision_authority(complete, gate(complete))

        self.assertFalse(decision["policy_gate"]["classification_eligible"])
        self.assertFalse(decision["policy_gate"]["replay_eligible"])
        self.assertEqual(decision["regime"], "UNKNOWN")
        self.assertEqual(decision["direction"], "UNKNOWN")
        self.assertIsNone(decision["confidence"])
        self.assertNotIn("NEUTRAL", json.dumps(decision))
        self.assertFalse(decision["authority"]["classification_authorized"])
        self.assertFalse(decision["authority"]["production_authorized"])
        self.assertFalse(decision["authority"]["trading_authorized"])

    def test_sources_are_hash_bound_and_reordered_keys_are_deterministic(self):
        original = full_source()
        reordered = copy.deepcopy(original)
        reordered["factor_results"] = dict(
            reversed(list(reordered["factor_results"].items()))
        )
        first_gate = gate(original)
        second_gate = gate(reordered)
        first = MODULE.evaluate_decision_authority(original, first_gate)
        second = MODULE.evaluate_decision_authority(reordered, second_gate)

        self.assertEqual(first, second)
        self.assertEqual(
            first["source_refs"]["regime_output_sha256"],
            MODULE.payload_sha256(original),
        )
        self.assertEqual(
            first["source_refs"]["minimum_coverage_sha256"],
            MODULE.payload_sha256(first_gate),
        )

    def test_source_gate_and_decision_tampering_fail_closed(self):
        original = full_source()
        original_gate = gate(original)
        decision = MODULE.evaluate_decision_authority(original, original_gate)

        bad_gate = copy.deepcopy(original_gate)
        bad_gate["classification_eligible"] = True
        with self.assertRaisesRegex(MODULE.DecisionAuthorityError, "COVERAGE_GATE_INVALID"):
            MODULE.evaluate_decision_authority(original, bad_gate)

        bad_source = copy.deepcopy(original)
        bad_source["regime"] = "NEUTRAL"
        with self.assertRaisesRegex(MODULE.DecisionAuthorityError, "REGIME_OUTPUT_INVALID"):
            MODULE.evaluate_decision_authority(bad_source, original_gate)

        mutations = []
        for key, value in [
            ("decision_status", "PASS"),
            ("regime", "RISK_ON"),
            ("direction", "IMPROVING"),
            ("confidence", 1),
        ]:
            changed = copy.deepcopy(decision)
            changed[key] = value
            mutations.append(changed)
        authority = copy.deepcopy(decision)
        authority["authority"]["classification_authorized"] = True
        mutations.append(authority)
        policy = copy.deepcopy(decision)
        policy["policy_gate"]["component_status"]["AGGREGATION_WEIGHTS"] = (
            "RATIFIED"
        )
        mutations.append(policy)

        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaisesRegex(
                MODULE.DecisionAuthorityError,
                "DECISION_DERIVATION_MISMATCH",
            ):
                MODULE.validate_decision(changed, original, original_gate)

    def test_contract_edit_cannot_self_ratify_policy_or_open_authority(self):
        registry = copy.deepcopy(CONTRACT)
        registry["repository_policy_registry_status"] = "PRESENT"
        status = copy.deepcopy(CONTRACT)
        status["policy_component_status"]["FRESHNESS"] = "RATIFIED"
        authority = copy.deepcopy(CONTRACT)
        authority["authority"]["classification_authorized"] = True
        result = copy.deepcopy(CONTRACT)
        result["allowed_decision_statuses"].append("CLASSIFIED")

        for changed in (registry, status, authority, result):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                MODULE.DecisionAuthorityError,
                "CONTRACT_INVALID",
            ):
                MODULE.validate_contract(changed)

    def test_cli_evaluate_and_validate_use_explicit_outputs(self):
        original = full_source()
        original_gate = gate(original)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_path = root / "regime.json"
            gate_path = root / "coverage.json"
            decision_path = root / "decision.json"
            source_path.write_text(json.dumps(original), encoding="utf-8")
            gate_path.write_text(json.dumps(original_gate), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                evaluate_exit = MODULE.main(
                    [
                        "evaluate",
                        str(source_path),
                        str(gate_path),
                        "--out",
                        str(decision_path),
                    ]
                )
                validate_exit = MODULE.main(
                    [
                        "validate",
                        str(decision_path),
                        "--regime-output",
                        str(source_path),
                        "--coverage-gate",
                        str(gate_path),
                    ]
                )
            self.assertEqual(evaluate_exit, 0)
            self.assertEqual(validate_exit, 0)
            self.assertEqual(
                json.loads(decision_path.read_text())["decision_status"],
                "BLOCKED_POLICY_UNRATIFIED",
            )


class RegimePolicyCandidateTest(unittest.TestCase):
    def test_candidate_contract_is_draft_and_shadow_only(self):
        self.assertEqual(
            POLICY_CONTRACT["contract_version"], "regime_policy_candidate/v1"
        )
        self.assertEqual(POLICY_CONTRACT["policy_status"], "DRAFT_NOT_RATIFIED")
        self.assertEqual(POLICY_CONTRACT["contract_mode"], "SHADOW_DIAGNOSTIC_ONLY")
        self.assertTrue(
            POLICY_CONTRACT["authority"][
                "diagnostic_candidate_evaluation_authorized"
            ]
        )
        for key, value in POLICY_CONTRACT["authority"].items():
            if key != "diagnostic_candidate_evaluation_authorized":
                self.assertFalse(value, key)

    def test_explainable_candidate_rules_are_deterministic(self):
        original = full_source()
        cases = [
            (
                {
                    "TREND": "SUPPORTIVE",
                    "BREADTH": "SUPPORTIVE",
                    "LIQUIDITY": "SUPPORTIVE",
                },
                {
                    "TREND": "IMPROVING",
                    "BREADTH": "IMPROVING",
                    "LIQUIDITY": "IMPROVING",
                },
                ("RISK_ON", "IMPROVING", "MEDIUM"),
            ),
            (
                {
                    "TREND": "ADVERSE",
                    "BREADTH": "ADVERSE",
                    "RISK_VOL": "ADVERSE",
                },
                {
                    "TREND": "DETERIORATING",
                    "BREADTH": "DETERIORATING",
                    "RISK_VOL": "DETERIORATING",
                },
                ("RISK_OFF", "DETERIORATING", "MEDIUM"),
            ),
            (
                {"TREND": "ADVERSE", "RISK_VOL": "STRESS"},
                {},
                ("STRESS", "STABLE", "LOW"),
            ),
            (
                {
                    "TREND": "SUPPORTIVE",
                    "BREADTH": "SUPPORTIVE",
                    "LIQUIDITY": "ADVERSE",
                },
                {},
                ("NEUTRAL", "STABLE", "LOW"),
            ),
        ]
        for orientations, changes, expected in cases:
            with self.subTest(expected=expected):
                assessments = policy_assessments(
                    original,
                    orientations=orientations,
                    changes=changes,
                )
                candidate = POLICY.evaluate_policy_candidate(original, assessments)
                observed = candidate["candidate"]
                self.assertEqual(
                    (
                        observed["regime"],
                        observed["direction"],
                        observed["confidence_band"],
                    ),
                    expected,
                )
                reordered = dict(reversed(list(assessments.items())))
                self.assertEqual(
                    candidate,
                    POLICY.evaluate_policy_candidate(original, reordered),
                )

    def test_candidate_is_hash_bound_and_cannot_self_ratify(self):
        original = full_source()
        assessments = policy_assessments(
            original,
            orientations={
                "TREND": "SUPPORTIVE",
                "BREADTH": "SUPPORTIVE",
                "LIQUIDITY": "SUPPORTIVE",
            },
        )
        candidate = POLICY.evaluate_policy_candidate(original, assessments)
        self.assertEqual(
            candidate["source_refs"]["regime_output_sha256"],
            POLICY.payload_sha256(original),
        )
        self.assertFalse(candidate["ratification"]["runtime_eligible"])
        self.assertEqual(
            candidate["ratification"]["allowed_downstream"],
            ["POLICY_REPLAY", "CIO_COMPARISON"],
        )

        mutations = []
        ratified = copy.deepcopy(candidate)
        ratified["policy_status"] = "RATIFIED"
        mutations.append(ratified)
        runtime = copy.deepcopy(candidate)
        runtime["ratification"]["runtime_eligible"] = True
        mutations.append(runtime)
        authority = copy.deepcopy(candidate)
        authority["authority"]["runtime_classification_authorized"] = True
        mutations.append(authority)
        changed_regime = copy.deepcopy(candidate)
        changed_regime["candidate"]["regime"] = "STRESS"
        mutations.append(changed_regime)

        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaisesRegex(
                POLICY.PolicyCandidateError,
                "CANDIDATE_DERIVATION_MISMATCH",
            ):
                POLICY.validate_candidate(
                    changed,
                    original,
                    assessments,
                )

    def test_incomplete_or_unbound_assessments_fail_closed(self):
        partial = source({"TREND": defined("TREND", "a")})
        partial_assessments = {
            "TREND": {
                "axis": "TREND",
                "orientation": "SUPPORTIVE",
                "change": "IMPROVING",
                "normalization_version": "regime_trend_orientation/v1",
                "source_evidence_sha256": "a" * 64,
                "warnings": [],
            }
        }
        with self.assertRaisesRegex(
            POLICY.PolicyCandidateError,
            "SOURCE_COVERAGE_INCOMPLETE",
        ):
            POLICY.evaluate_policy_candidate(partial, partial_assessments)

        original = full_source()
        assessments = policy_assessments(original)
        assessments["TREND"]["source_evidence_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            POLICY.PolicyCandidateError,
            "SOURCE_EVIDENCE_BINDING_MISMATCH",
        ):
            POLICY.evaluate_policy_candidate(original, assessments)

        wrong_stress = policy_assessments(original)
        wrong_stress["TREND"]["orientation"] = "STRESS"
        with self.assertRaisesRegex(
            POLICY.PolicyCandidateError,
            "STRESS_AXIS_INVALID",
        ):
            POLICY.evaluate_policy_candidate(original, wrong_stress)

    def test_candidate_contract_cannot_be_edited_into_authority(self):
        ratified = copy.deepcopy(POLICY_CONTRACT)
        ratified["policy_status"] = "RATIFIED"
        runtime = copy.deepcopy(POLICY_CONTRACT)
        runtime["authority"]["runtime_classification_authorized"] = True
        weighted = copy.deepcopy(POLICY_CONTRACT)
        weighted["classification_policy"]["risk_on_rule"] = "weighted_score"

        for changed in (ratified, runtime, weighted):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                POLICY.PolicyCandidateError,
                "CONTRACT_INVALID",
            ):
                POLICY.validate_contract(changed)

    def test_candidate_cli_evaluate_and_validate(self):
        original = full_source()
        assessments = policy_assessments(
            original,
            orientations={
                "TREND": "SUPPORTIVE",
                "BREADTH": "SUPPORTIVE",
                "LIQUIDITY": "SUPPORTIVE",
            },
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_path = root / "regime.json"
            assessment_path = root / "assessments.json"
            candidate_path = root / "candidate.json"
            source_path.write_text(json.dumps(original), encoding="utf-8")
            assessment_path.write_text(json.dumps(assessments), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                evaluate_exit = POLICY.main(
                    [
                        "evaluate",
                        str(source_path),
                        str(assessment_path),
                        "--out",
                        str(candidate_path),
                    ]
                )
                validate_exit = POLICY.main(
                    [
                        "validate",
                        str(candidate_path),
                        "--regime-output",
                        str(source_path),
                        "--axis-assessments",
                        str(assessment_path),
                    ]
                )
            self.assertEqual(evaluate_exit, 0)
            self.assertEqual(validate_exit, 0)
            persisted = json.loads(candidate_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["candidate"]["regime"], "RISK_ON")
            self.assertEqual(persisted["policy_status"], "DRAFT_NOT_RATIFIED")


if __name__ == "__main__":
    unittest.main()
