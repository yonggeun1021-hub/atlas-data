#!/usr/bin/env python3
"""P1-COM-04 canonical replay-population readiness regression."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "replay_population_readiness.py"
SPEC = importlib.util.spec_from_file_location(
    "regime_replay_population_readiness_tested",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def copied_root(destination: Path) -> Path:
    relative_paths = (
        "config/regime_replay_population_readiness_contract.json",
        "config/regime_replay_harness_contract.json",
        "config/regime_policy_candidate_population_contract.json",
        "config/regime_policy_candidate_contract.json",
        "config/regime_minimum_coverage_policy.json",
        "evidence/regime/policy_candidates/minimum_coverage_evidence.json",
        "evidence/regime/policy_candidates/market_normalization_unratified_evidence.json",
        "evidence/regime/policy_candidates/candidate_manifest.json",
        "evidence/regime/policy_candidates/candidate_inventory.json",
    )
    for relative in relative_paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return destination


class RegimeReplayPopulationReadinessTest(unittest.TestCase):
    def test_actual_population_is_bound_but_not_eligible(self):
        result = MODULE.build_readiness()

        self.assertEqual(
            result["status"], "NOT_COMPUTABLE_POLICY_CANDIDATE_BLOCKED"
        )
        self.assertEqual(result["candidate"]["candidate_status"], "CANDIDATE_BLOCKED")
        self.assertEqual(
            result["candidate"]["supported_components"], ["MINIMUM_COVERAGE"]
        )
        self.assertEqual(
            result["candidate"]["explicit_negative_components"],
            ["MARKET_NORMALIZATION"],
        )
        self.assertEqual(len(result["candidate"]["missing_evidence_components"]), 7)
        self.assertEqual(len(result["candidate"]["blocked_components"]), 8)
        self.assertTrue(result["replay_capability"]["capability_available"])
        self.assertTrue(result["replay_capability"]["candidate_inventory_bound"])
        self.assertEqual(result["population"]["eligible_market_count"], 0)
        self.assertEqual(result["population"]["eligible_case_count"], 0)
        self.assertFalse(result["population"]["historical_outcome_evaluated"])
        self.assertEqual(
            result["population"]["case_population_status"],
            "NOT_COMPUTABLE_CANDIDATE_INPUT_NOT_ELIGIBLE",
        )
        self.assertEqual(len(result["blockers"]), 9)

    def test_authority_is_inventory_only_and_all_downstream_false(self):
        authority = MODULE.build_readiness()["authority"]
        self.assertTrue(authority["readiness_inventory_only"])
        for key, value in authority.items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_output_is_deterministic_and_independently_rederived(self):
        first = MODULE.build_readiness()
        second = MODULE.build_readiness()
        self.assertEqual(first, second)
        unsigned = copy.deepcopy(first)
        claimed = unsigned.pop("payload_sha256")
        self.assertEqual(claimed, MODULE.payload_sha256(unsigned))
        self.assertEqual(MODULE.validate_readiness(first), first)

        tampered = copy.deepcopy(first)
        tampered["population"]["eligible_case_count"] = 1
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.ReplayPopulationReadinessError,
            "READINESS_REDERIVATION_MISMATCH",
        ):
            MODULE.validate_readiness(tampered)

    def test_bound_contract_bytes_are_exactly_pinned(self):
        with tempfile.TemporaryDirectory() as raw:
            root = copied_root(Path(raw))
            path = root / "config/regime_replay_harness_contract.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["success_status"] = "FABRICATED"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.ReplayPopulationReadinessError,
                "BOUND_CONTRACT_SHA_MISMATCH:replay_harness_contract",
            ):
                MODULE.build_readiness(root)

    def test_candidate_artifact_tamper_fails_through_source_validator(self):
        with tempfile.TemporaryDirectory() as raw:
            root = copied_root(Path(raw))
            path = root / "evidence/regime/policy_candidates/candidate_inventory.json"
            inventory = json.loads(path.read_text(encoding="utf-8"))
            inventory["candidate_status"] = "CANDIDATE_READY"
            path.write_text(json.dumps(inventory), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.ReplayPopulationReadinessError,
                "BOUND_SOURCE_VALIDATION_FAILED",
            ):
                MODULE.build_readiness(root)

    def test_cli_writes_only_outside_repository(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "readiness.json"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--out", str(target)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(MODULE.validate_readiness(value), value)

        forbidden = ROOT / "replay-readiness-should-not-exist.json"
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--out", str(forbidden)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("TRACKED_OUTPUT_FORBIDDEN", completed.stderr)
        self.assertFalse(forbidden.exists())

    def test_module_adds_no_provider_workflow_or_policy_defaults(self):
        source = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
        )
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("replay_population_readiness.py", workflows)
        self.assertNotIn("eligible_case_count\": 1", source)
        self.assertNotIn("replay_population_authorized\": True", source)


if __name__ == "__main__":
    unittest.main()
