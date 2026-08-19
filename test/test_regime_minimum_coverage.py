#!/usr/bin/env python3
"""Ratified five-of-five Regime minimum-coverage regression."""

import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "minimum_coverage.py"

SPEC = importlib.util.spec_from_file_location("regime_minimum_coverage", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()
OUTPUT = MODULE.OUTPUT


def defined(axis, sha_character, available_at="2026-08-20T00:20:00Z"):
    return {
        "status": "DEFINED",
        "observation_date": "2026-08-19",
        "available_at": available_at,
        "transform_version": f"regime_{axis.lower()}/v1",
        "evidence": {
            "uri": f"evidence/{axis.lower()}/2026-08-19.json",
            "sha256": sha_character * 64,
        },
        "warnings": [],
    }


def source(factors=None, generated_at="2026-08-20T01:00:00Z"):
    return OUTPUT.build_unknown_output(
        "CRYPTO",
        generated_at,
        factors,
    )


def full_source():
    return source(
        {
            axis: defined(axis, character)
            for axis, character in zip(CONTRACT["required_axes"], "abcde")
        }
    )


class RegimeMinimumCoverageTest(unittest.TestCase):
    def test_contract_pins_ratified_five_of_five_policy(self):
        self.assertEqual(CONTRACT["contract_version"], "regime_minimum_coverage/v1")
        self.assertEqual(CONTRACT["policy_status"], "RATIFIED")
        self.assertEqual(CONTRACT["policy_name"], "ALL_REQUIRED_AXES_5_OF_5")
        self.assertEqual(CONTRACT["minimum_defined_axes"], 5)
        self.assertEqual(len(CONTRACT["required_axes"]), 5)
        self.assertEqual(
            CONTRACT["runtime_authorized_results"],
            ["BLOCKED", "COVERAGE_MET"],
        )

    def test_zero_and_partial_coverage_are_blocked_with_exact_missing_axes(self):
        cases = [
            (source(), CONTRACT["required_axes"]),
            (
                source(
                    {
                        "TREND": defined("TREND", "a"),
                        "RISK_VOL": defined("RISK_VOL", "b"),
                    }
                ),
                ["BREADTH", "LIQUIDITY", "LEADERSHIP"],
            ),
        ]
        for input_source, expected_missing in cases:
            with self.subTest(expected_missing=expected_missing):
                gate = MODULE.evaluate_minimum_coverage(input_source)
                self.assertEqual(gate["gate_result"], "BLOCKED")
                self.assertFalse(gate["minimum_coverage_met"])
                self.assertFalse(gate["classification_eligible"])
                self.assertEqual(gate["coverage"]["missing_axes"], expected_missing)
                self.assertEqual(gate["regime"], "UNKNOWN")
                self.assertNotIn("NEUTRAL", json.dumps(gate))
                self.assertIn("MINIMUM_COVERAGE_NOT_MET", gate["reasons"])
                for axis in expected_missing:
                    self.assertIn(
                        CONTRACT["axis_missing_reason_codes"][axis],
                        gate["reasons"],
                    )

    def test_all_five_axes_meet_coverage_but_do_not_authorize_classification(self):
        gate = MODULE.evaluate_minimum_coverage(full_source())

        self.assertEqual(gate["gate_result"], "COVERAGE_MET")
        self.assertTrue(gate["minimum_coverage_met"])
        self.assertEqual(gate["coverage"]["ratio"], "5/5")
        self.assertEqual(gate["coverage"]["missing_axes"], [])
        self.assertEqual(gate["reasons"], CONTRACT["downstream_blocker_reason_codes"])
        self.assertFalse(gate["classification_eligible"])
        self.assertEqual(gate["regime"], "UNKNOWN")
        self.assertEqual(gate["direction"], "UNKNOWN")
        self.assertIsNone(gate["confidence"])
        self.assertTrue(gate["authority"]["minimum_coverage_gate_ratified"])
        self.assertFalse(gate["authority"]["freshness_policy_ratified"])
        self.assertFalse(gate["authority"]["classification_authorized"])

    def test_old_but_defined_evidence_cannot_become_classification_eligible(self):
        old = source(
            {
                axis: defined(axis, character, "2026-01-01T00:00:00Z")
                for axis, character in zip(CONTRACT["required_axes"], "abcde")
            }
        )
        gate = MODULE.evaluate_minimum_coverage(old)

        self.assertEqual(gate["gate_result"], "COVERAGE_MET")
        self.assertIn("FRESHNESS_POLICY_UNRATIFIED", gate["reasons"])
        self.assertFalse(gate["classification_eligible"])
        self.assertEqual(gate["regime"], "UNKNOWN")

    def test_source_is_canonically_bound_and_object_key_order_is_irrelevant(self):
        input_source = full_source()
        reordered = copy.deepcopy(input_source)
        reordered["factor_results"] = dict(
            reversed(list(reordered["factor_results"].items()))
        )

        first = MODULE.evaluate_minimum_coverage(input_source)
        second = MODULE.evaluate_minimum_coverage(reordered)
        self.assertEqual(first, second)
        self.assertEqual(
            MODULE.source_sha256(input_source),
            MODULE.source_sha256(reordered),
        )
        self.assertEqual(MODULE.validate_gate(first, input_source), first)

    def test_source_and_derived_tampering_fail_closed(self):
        input_source = full_source()
        baseline = MODULE.evaluate_minimum_coverage(input_source)

        source_tamper = copy.deepcopy(input_source)
        source_tamper["regime"] = "NEUTRAL"
        with self.assertRaisesRegex(MODULE.MinimumCoverageError, "SOURCE_OUTPUT_INVALID"):
            MODULE.evaluate_minimum_coverage(source_tamper)

        mutations = []
        for path, value in [
            (("gate_result",), "BLOCKED"),
            (("minimum_coverage_met",), False),
            (("classification_eligible",), True),
            (("source_output_sha256",), "0" * 64),
            (("regime",), "NEUTRAL"),
        ]:
            changed = copy.deepcopy(baseline)
            changed[path[0]] = value
            mutations.append(changed)
        authority = copy.deepcopy(baseline)
        authority["authority"]["classification_authorized"] = True
        mutations.append(authority)

        for gate in mutations:
            with self.subTest(gate=gate), self.assertRaisesRegex(
                MODULE.MinimumCoverageError,
                "GATE_DERIVATION_MISMATCH",
            ):
                MODULE.validate_gate(gate, input_source)

    def test_contract_edit_cannot_lower_minimum_or_open_authority(self):
        lowered = copy.deepcopy(CONTRACT)
        lowered["minimum_defined_axes"] = 4
        result = copy.deepcopy(CONTRACT)
        result["runtime_authorized_results"].append("PASS")
        freshness = copy.deepcopy(CONTRACT)
        freshness["downstream_blocker_reason_codes"] = [
            "REGIME_CLASSIFICATION_NOT_AUTHORIZED"
        ]

        for contract in (lowered, result, freshness):
            with self.subTest(contract=contract), self.assertRaisesRegex(
                MODULE.MinimumCoverageError,
                "CONTRACT_INVALID",
            ):
                MODULE.validate_contract(contract)

    def test_cli_only_writes_requested_temporary_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.json"
            gate_path = root / "output" / "coverage.json"
            source_path.write_text(json.dumps(full_source()), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                evaluate_exit = MODULE.main(
                    ["evaluate", str(source_path), "--out", str(gate_path)]
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
            self.assertEqual(gate["gate_result"], "COVERAGE_MET")
            self.assertFalse(gate["classification_eligible"])
            self.assertFalse(list(gate_path.parent.glob(".*.tmp.*")))


if __name__ == "__main__":
    unittest.main()
