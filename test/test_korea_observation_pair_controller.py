#!/usr/bin/env python3
"""Offline controller, dedupe, and final-handoff regressions for P2-03."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_observation_pair_controller.py"
SPEC = importlib.util.spec_from_file_location("korea_observation_pair_controller", SCRIPT)
CTRL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CTRL)


class RequestReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _touch(self, family: str) -> None:
        path = (
            self.root / "data" / "observations" / family
            / "2026-09-15" / "packet.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    def test_archived_pre_effective_pair_stays_waiting(self):
        result = CTRL.request_readiness("20260813", "20260814", self.root)
        self.assertFalse(result["call_ready"])
        self.assertEqual(result["status"], "WAIT_POLICY_NOT_EFFECTIVE_FOR_PAIR")

    def test_post_effective_missing_inputs_calls_only_ordered_combined_path(self):
        result = CTRL.request_readiness("20260914", "20260915", self.root)
        self.assertTrue(result["call_ready"])
        self.assertEqual(result["status"], "CALL_ORDERED_CAPTURE")

    def test_existing_leadership_without_prior_breadth_waits_for_next_pair(self):
        self._touch("korea_leadership_context")
        result = CTRL.request_readiness("20260914", "20260915", self.root)
        self.assertFalse(result["call_ready"])
        self.assertEqual(
            result["status"],
            "WAIT_EXISTING_LEADERSHIP_PRECEDES_MISSING_BREADTH",
        )

    def test_existing_pair_must_pass_exact_current_producer(self):
        self._touch("korea_breadth_context")
        self._touch("korea_leadership_context")
        packet = {
            "status": "ROTATION_BUCKETS_OBSERVED",
            "rotation_policy_effective": True,
            "lineage": {
                "rotation_policy_sha256": "a" * 64,
                "upstream_leadership_policy_sha256": "b" * 64,
            },
        }
        with mock.patch.object(
            CTRL.LEDGER, "build_current_ratified_packet", return_value=packet
        ):
            result = CTRL.request_readiness("20260914", "20260915", self.root)
        self.assertTrue(result["call_ready"])
        self.assertEqual(result["status"], "CALL_EXISTING_ELIGIBLE_PAIR")

    def test_existing_pair_producer_refusal_is_wait_not_success(self):
        self._touch("korea_breadth_context")
        self._touch("korea_leadership_context")
        with mock.patch.object(
            CTRL.LEDGER,
            "build_current_ratified_packet",
            side_effect=RuntimeError("BREADTH_AFTER_LEADERSHIP"),
        ):
            result = CTRL.request_readiness("20260914", "20260915", self.root)
        self.assertFalse(result["call_ready"])
        self.assertEqual(result["status"], "WAIT_EXISTING_PAIR_NOT_PRODUCER_ELIGIBLE")


class RunClassificationTests(unittest.TestCase):
    def test_active_exact_request_blocks_duplicate_call(self):
        title = "P2-03 Korea Observation Pair 20260914-20260915"
        payload = {
            "workflow_runs": [
                {"id": 10, "status": "in_progress", "display_title": title},
                {"id": 11, "status": "in_progress", "display_title": title + "x"},
            ]
        }
        result = CTRL.classify_runs([payload], title, 99)
        self.assertEqual(
            result["active_same_request_runs"],
            [{"run_id": 10, "run_attempt": 1}],
        )

    def test_green_run_is_only_artifact_candidate(self):
        payload = {
            "workflow_runs": [
                {
                    "id": 20,
                    "run_attempt": 2,
                    "status": "completed",
                    "conclusion": "success",
                    "display_title": "controller",
                    "created_at": "2026-09-15T09:25:00Z",
                },
                {
                    "id": 21,
                    "run_attempt": 1,
                    "status": "completed",
                    "conclusion": "failure",
                    "display_title": "controller",
                    "created_at": "2026-09-15T09:10:00Z",
                },
            ]
        }
        result = CTRL.classify_runs([payload], "expected", 99)
        self.assertEqual(result["active_same_request_runs"], [])
        self.assertEqual(
            result["successful_artifact_candidates"],
            [{"run_id": 20, "run_attempt": 2, "created_at": "2026-09-15T09:25:00Z"}],
        )


class FinalArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.artifact = Path(self.temp.name)
        self.packet = {
            "status": "ROTATION_BUCKETS_OBSERVED",
            "rotation_policy_effective": True,
            "observation_pair": {
                "prior_date": "2026-09-14",
                "current_date": "2026-09-15",
            },
            "payload_sha256": "c" * 64,
            "lineage": {
                "rotation_policy_sha256": "a" * 64,
                "upstream_leadership_policy_sha256": "b" * 64,
            },
        }
        (self.artifact / "packet.json").write_text(
            json.dumps(self.packet), encoding="utf-8"
        )
        (self.artifact / "public-main-commit.txt").write_text(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_current_packet_is_the_only_dedupe_proof(self):
        with (
            mock.patch.object(CTRL.KCR, "validate_packet", return_value=self.packet),
            mock.patch.object(
                CTRL.LEDGER, "build_current_ratified_packet", return_value=self.packet
            ),
        ):
            result = CTRL.verify_handoff(
                self.artifact, "20260914", "20260915"
            )
        self.assertTrue(result["validated"])
        self.assertEqual(result["status"], "FINAL_ROTATION_HANDOFF_VALIDATED")

    def test_policy_or_source_refresh_makes_old_green_artifact_retryable(self):
        refreshed = dict(self.packet)
        refreshed["payload_sha256"] = "e" * 64
        with (
            mock.patch.object(CTRL.KCR, "validate_packet", return_value=self.packet),
            mock.patch.object(
                CTRL.LEDGER, "build_current_ratified_packet", return_value=refreshed
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "FINAL_HANDOFF_CURRENT_SOURCE_OR_POLICY_MISMATCH"
            ):
                CTRL.verify_handoff(self.artifact, "20260914", "20260915")

    def test_not_evaluated_or_missing_artifact_cannot_suppress_retry(self):
        (self.artifact / "packet.json").unlink()
        with self.assertRaisesRegex(
            RuntimeError, "FINAL_HANDOFF_FILES_MISSING_OR_AMBIGUOUS"
        ):
            CTRL.verify_handoff(self.artifact, "20260914", "20260915")


if __name__ == "__main__":
    unittest.main()
