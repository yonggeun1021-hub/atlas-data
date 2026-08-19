#!/usr/bin/env python3
"""P1-COM-02 fail-closed minimum-coverage Gate regression.

All source envelopes, Gate artifacts, and CLI outputs use memory or temporary
files. No market data, network, tracked output, score, or action is produced.
"""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "coverage_gate.py"

SPEC = importlib.util.spec_from_file_location("regime_coverage_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()
OUTPUT = MODULE.OUTPUT


def defined(axis, sha_character):
    return {
        "status": "DEFINED",
        "observation_date": "2026-08-19",
        "available_at": "2026-08-20T00:20:00Z",
        "transform_version": f"regime_{axis.lower()}/v1",
        "evidence": {
            "uri": f"evidence/{axis.lower()}/2026-08-19.json",
            "sha256": sha_character * 64,
        },
        "warnings": [],
    }


def source(factors=None):
    return OUTPUT.build_unknown_output(
        "CRYPTO",
        "2026-08-20T01:00:00Z",
        factors,
    )


class RegimeCoverageGateTest(unittest.TestCase):
    def test_contract_pins_unratified_blocked_semantics_without_a_number(self):
        self.assertEqual(
            CONTRACT["contract_version"],
            "regime_coverage_gate/v1",
        )
        self.assertEqual(CONTRACT["policy_status"], "UNRATIFIED")
        self.assertEqual(CONTRACT["runtime_authorized_results"], ["BLOCKED"])
        self.assertIsNone(CONTRACT["minimum_defined_axes"])
        self.assertEqual(CONTRACT["source_contract_version"], "regime_output/v1")

    def test_zero_coverage_is_explicitly_blocked_unknown_not_neutral(self):
        gate = MODULE.evaluate_coverage_gate(source())

        self.assertEqual(gate["gate_result"], "BLOCKED")
        self.assertFalse(gate["classification_eligible"])
        self.assertEqual(gate["regime"], "UNKNOWN")
        self.assertEqual(gate["direction"], "UNKNOWN")
        self.assertIsNone(gate["confidence"])
        self.assertEqual(gate["coverage"]["ratio"], "0/5")
        self.assertEqual(
            gate["reasons"],
            CONTRACT["base_reason_codes"]
            + list(CONTRACT["axis_missing_reason_codes"].values()),
        )
        self.assertNotIn("NEUTRAL", json.dumps(gate))

    def test_partial_coverage_reports_each_missing_axis_and_binds_source(self):
        input_source = source(
            {
                "TREND": defined("TREND", "a"),
                "RISK_VOL": defined("RISK_VOL", "b"),
            }
        )
        gate = MODULE.evaluate_coverage_gate(input_source)

        self.assertEqual(gate["coverage"]["defined_axes"], ["TREND", "RISK_VOL"])
        self.assertEqual(
            gate["coverage"]["missing_axes"],
            ["BREADTH", "LIQUIDITY", "LEADERSHIP"],
        )
        self.assertIn("BREADTH_UNDEFINED", gate["reasons"])
        self.assertEqual(
            gate["source_output_sha256"],
            MODULE.source_sha256(input_source),
        )
        self.assertEqual(
            MODULE.validate_gate(gate, input_source),
            gate,
        )

    def test_full_five_axis_coverage_still_cannot_ratify_or_classify(self):
        factors = {
            axis: defined(axis, character)
            for axis, character in zip(
                CONTRACT["required_axes"],
                "abcde",
            )
        }
        gate = MODULE.evaluate_coverage_gate(source(factors))

        self.assertEqual(gate["coverage"]["ratio"], "5/5")
        self.assertEqual(gate["coverage"]["missing_axes"], [])
        self.assertEqual(gate["reasons"], CONTRACT["base_reason_codes"])
        self.assertEqual(gate["policy_status"], "UNRATIFIED")
        self.assertEqual(gate["gate_result"], "BLOCKED")
        self.assertFalse(gate["classification_eligible"])
        self.assertEqual(gate["regime"], "UNKNOWN")

    def test_neutral_direction_and_confidence_tamper_fail_at_source_boundary(self):
        baseline = source()
        neutral = copy.deepcopy(baseline)
        neutral["regime"] = "NEUTRAL"
        improving = copy.deepcopy(baseline)
        improving["direction"] = "IMPROVING"
        confident = copy.deepcopy(baseline)
        confident["confidence"] = "0.9"

        for payload in (neutral, improving, confident):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                MODULE.CoverageGateError,
                "SOURCE_OUTPUT_INVALID",
            ):
                MODULE.evaluate_coverage_gate(payload)

    def test_source_schema_and_coverage_boolean_integer_confusion_is_rejected(self):
        baseline = source({"TREND": defined("TREND", "a")})
        bad_schema = copy.deepcopy(baseline)
        bad_schema["schema_version"] = True
        bad_count = copy.deepcopy(baseline)
        bad_count["coverage"]["defined_count"] = True

        for payload in (bad_schema, bad_count):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                MODULE.CoverageGateError,
                "SOURCE_OUTPUT_INVALID",
            ):
                MODULE.evaluate_coverage_gate(payload)

    def test_gate_eligibility_result_reasons_hash_and_authority_are_derived(self):
        input_source = source({"TREND": defined("TREND", "a")})
        baseline = MODULE.evaluate_coverage_gate(input_source)
        mutations = []

        eligible = copy.deepcopy(baseline)
        eligible["classification_eligible"] = True
        mutations.append(eligible)
        passed = copy.deepcopy(baseline)
        passed["gate_result"] = "PASS"
        mutations.append(passed)
        neutral = copy.deepcopy(baseline)
        neutral["regime"] = "NEUTRAL"
        mutations.append(neutral)
        minimum = copy.deepcopy(baseline)
        minimum["minimum_defined_axes"] = 3
        mutations.append(minimum)
        reasons = copy.deepcopy(baseline)
        reasons["reasons"] = []
        mutations.append(reasons)
        digest = copy.deepcopy(baseline)
        digest["source_output_sha256"] = "0" * 64
        mutations.append(digest)
        authority = copy.deepcopy(baseline)
        authority["authority"]["regime_score_authorized"] = True
        mutations.append(authority)

        for gate in mutations:
            with self.subTest(gate=gate), self.assertRaisesRegex(
                MODULE.CoverageGateError,
                "GATE_DERIVATION_MISMATCH",
            ):
                MODULE.validate_gate(gate, input_source)

    def test_source_object_key_order_does_not_change_hash_or_gate(self):
        input_source = source(
            {
                "TREND": defined("TREND", "a"),
                "LIQUIDITY": defined("LIQUIDITY", "b"),
            }
        )
        reordered = copy.deepcopy(input_source)
        reordered["factor_results"] = dict(
            reversed(list(reordered["factor_results"].items()))
        )

        first = MODULE.evaluate_coverage_gate(input_source)
        second = MODULE.evaluate_coverage_gate(reordered)
        self.assertEqual(first, second)
        self.assertEqual(
            MODULE.source_sha256(input_source),
            MODULE.source_sha256(reordered),
        )

    def test_contract_edit_cannot_ratify_minimum_or_enable_result(self):
        ratified = copy.deepcopy(CONTRACT)
        ratified["policy_status"] = "RATIFIED"
        numbered = copy.deepcopy(CONTRACT)
        numbered["minimum_defined_axes"] = 3
        enabled = copy.deepcopy(CONTRACT)
        enabled["runtime_authorized_results"] = ["BLOCKED", "PASS"]

        for contract in (ratified, numbered, enabled):
            with self.subTest(contract=contract), self.assertRaisesRegex(
                MODULE.CoverageGateError,
                "CONTRACT_INVALID",
            ):
                MODULE.validate_contract(contract)

    def test_cli_evaluate_and_validate_only_write_requested_temp_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.json"
            gate_path = root / "output" / "gate.json"
            source_path.write_text(
                json.dumps(source({"TREND": defined("TREND", "a")})),
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                evaluate_exit = MODULE.main(
                    [
                        "evaluate",
                        str(source_path),
                        "--out",
                        str(gate_path),
                    ]
                )
                validate_exit = MODULE.main(
                    [
                        "validate",
                        str(gate_path),
                        "--source",
                        str(source_path),
                    ]
                )

            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            self.assertEqual(evaluate_exit, 0)
            self.assertEqual(validate_exit, 0)
            self.assertEqual(gate["gate_result"], "BLOCKED")
            self.assertEqual(gate["regime"], "UNKNOWN")
            self.assertFalse(list(gate_path.parent.glob(".*.tmp.*")))


if __name__ == "__main__":
    unittest.main()
