#!/usr/bin/env python3
"""Regression and fault injection for P1-COM-05 evidence population."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "policy_candidate_population.py"
SPEC = importlib.util.spec_from_file_location(
    "regime_policy_candidate_population",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MODULE.render_bytes(value))


def artifact_paths(root: Path) -> dict[str, Path]:
    contract = MODULE.load_population_contract()
    return {
        key: root / relative
        for key, relative in contract["artifact_paths"].items()
    }


def copied_source_root(root: Path) -> Path:
    for relative in (
        "config/regime_policy_candidate_contract.json",
        "config/regime_minimum_coverage_policy.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return root


class RegimePolicyCandidatePopulationTest(unittest.TestCase):
    def test_only_ratified_minimum_coverage_is_supported(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            summary = MODULE.build_population(root, ROOT)
            paths = artifact_paths(root)
            manifest = read_json(paths["manifest"])
            inventory = read_json(paths["inventory"])

            self.assertEqual(summary["candidate_status"], "CANDIDATE_BLOCKED")
            self.assertEqual(summary["supported_components"], ["MINIMUM_COVERAGE"])
            self.assertEqual(len(summary["blocked_components"]), 8)
            self.assertEqual(summary["replay_population_status"], "NOT_COMPUTABLE")

            minimum = next(
                item
                for item in manifest["parameters"]
                if item["component"] == "MINIMUM_COVERAGE"
            )
            self.assertEqual(minimum["value_type"], "STRUCTURED")
            self.assertEqual(minimum["proposed_value"]["policy_status"], "RATIFIED")
            self.assertEqual(minimum["proposed_value"]["minimum_defined_axes"], 5)
            self.assertEqual(
                minimum["proposed_value"]["required_axes"],
                ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"],
            )
            self.assertEqual(
                minimum["proposed_value"]["downstream_blocker_reason_codes"],
                [
                    "FRESHNESS_POLICY_UNRATIFIED",
                    "REGIME_CLASSIFICATION_NOT_AUTHORIZED",
                ],
            )

            for parameter in inventory["parameters"]:
                if parameter["component"] == "MINIMUM_COVERAGE":
                    self.assertEqual(parameter["status"], "SUPPORTED")
                else:
                    self.assertEqual(parameter["status"], "BLOCKED")
                    self.assertEqual(
                        parameter["blocking_reasons"],
                        ["EVIDENCE_MISSING", "VALUE_UNSPECIFIED"],
                    )
            self.assertFalse(inventory["replay"]["candidate_input_eligible"])
            self.assertFalse(inventory["ratification"]["selected"])
            self.assertFalse(inventory["ratification"]["recommended"])
            self.assertFalse(inventory["ratification"]["ratified"])
            self.assertFalse(inventory["ratification"]["runtime_eligible"])
            for key, value in summary["authority"].items():
                if key != "candidate_evidence_population_authorized":
                    self.assertFalse(value, key)

    def test_rebuild_is_byte_deterministic(self):
        with tempfile.TemporaryDirectory() as first_raw, tempfile.TemporaryDirectory() as second_raw:
            first = Path(first_raw)
            second = Path(second_raw)
            first_summary = MODULE.build_population(first, ROOT)
            second_summary = MODULE.build_population(second, ROOT)

            self.assertEqual(first_summary, second_summary)
            for key in ("evidence", "manifest", "inventory"):
                self.assertEqual(
                    artifact_paths(first)[key].read_bytes(),
                    artifact_paths(second)[key].read_bytes(),
                    key,
                )

    def test_four_of_five_and_axis_substitution_fail_source_pin(self):
        mutations = {
            "four_of_five": lambda value: value.update(minimum_defined_axes=4),
            "axis_substitution": lambda value: value["required_axes"].__setitem__(
                4, "MOMENTUM"
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                source_root = copied_source_root(Path(raw))
                path = source_root / "config/regime_minimum_coverage_policy.json"
                changed = read_json(path)
                mutate(changed)
                write_json(path, changed)
                with self.assertRaisesRegex(
                    MODULE.PolicyCandidatePopulationError,
                    "MINIMUM_COVERAGE_SOURCE_SHA_MISMATCH",
                ):
                    MODULE.build_population(source_root / "artifacts", source_root)

    def test_resigned_artifact_chain_cannot_replace_ratified_value(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            MODULE.build_population(root, ROOT)
            paths = artifact_paths(root)
            evidence = read_json(paths["evidence"])
            manifest = read_json(paths["manifest"])

            evidence["parameter_claims"][0]["supported_value"][
                "minimum_defined_axes"
            ] = 4
            write_json(paths["evidence"], evidence)
            minimum = next(
                item
                for item in manifest["parameters"]
                if item["component"] == "MINIMUM_COVERAGE"
            )
            minimum["proposed_value"]["minimum_defined_axes"] = 4
            minimum["evidence_refs"][0]["sha256"] = hashlib.sha256(
                paths["evidence"].read_bytes()
            ).hexdigest()
            write_json(paths["manifest"], manifest)
            inventory = MODULE.CANDIDATE.build_candidate_inventory(
                manifest,
                root,
                MODULE.CANDIDATE.load_contract(),
            )
            write_json(paths["inventory"], inventory)

            with self.assertRaisesRegex(
                MODULE.PolicyCandidatePopulationError,
                "EVIDENCE_ARTIFACT_MISMATCH",
            ):
                MODULE.validate_population(root, ROOT)

    def test_inventory_authority_and_ready_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            MODULE.build_population(root, ROOT)
            inventory_path = artifact_paths(root)["inventory"]
            inventory = read_json(inventory_path)

            for name, mutate in (
                (
                    "runtime_authority",
                    lambda value: value["authority"].update(
                        runtime_classification_authorized=True
                    ),
                ),
                (
                    "fake_ready",
                    lambda value: value.update(candidate_status="CANDIDATE_READY"),
                ),
            ):
                with self.subTest(name=name):
                    changed = copy.deepcopy(inventory)
                    mutate(changed)
                    write_json(inventory_path, changed)
                    with self.assertRaisesRegex(
                        MODULE.PolicyCandidatePopulationError,
                        "INVENTORY_ARTIFACT_MISMATCH",
                    ):
                        MODULE.validate_population(root, ROOT)
                    write_json(inventory_path, inventory)

    def test_population_contract_cannot_open_downstream_authority(self):
        contract = copy.deepcopy(MODULE.load_population_contract())
        contract["authority"]["policy_ratification_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.PolicyCandidatePopulationError,
            "POPULATION_CONTRACT_INVALID",
        ):
            MODULE.validate_population_contract(contract)

    def test_cli_build_and_validate(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with contextlib.redirect_stdout(io.StringIO()):
                build_exit = MODULE.main(
                    [
                        "build",
                        "--artifact-root",
                        str(root),
                        "--source-root",
                        str(ROOT),
                    ]
                )
                validate_exit = MODULE.main(
                    [
                        "validate",
                        "--artifact-root",
                        str(root),
                        "--source-root",
                        str(ROOT),
                    ]
                )
            self.assertEqual(build_exit, 0)
            self.assertEqual(validate_exit, 0)


if __name__ == "__main__":
    unittest.main()
