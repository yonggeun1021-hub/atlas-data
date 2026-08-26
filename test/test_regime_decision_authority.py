#!/usr/bin/env python3
"""P1-COM-05 Regime decision-authority boundary regression."""

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


if __name__ == "__main__":
    unittest.main()
