#!/usr/bin/env python3
"""Crypto Breadth/Leadership scheduler telemetry offline regression."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "record_crypto_breadth_run.py"
WORKFLOW = ROOT / ".github" / "workflows" / "crypto-breadth-capture.yml"

SPEC = importlib.util.spec_from_file_location("record_crypto_breadth_run", SCRIPT)
REC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REC)

with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)

STEPS = WF["jobs"]["capture"]["steps"]


def workflow_step(name):
    for step in STEPS:
        if step.get("name") == name:
            return step
    return None


def environment(**overrides):
    base = {
        "ATLAS_EVENT_NAME": "schedule",
        "ATLAS_EVENT_SCHEDULE": "40 0 * * *",
        "ATLAS_RUN_ID": "32340000001",
        "ATLAS_RUN_ATTEMPT": "1",
        "ATLAS_RUNNER_STARTED_AT_UTC": "2026-08-21T00:45:00Z",
        "ATLAS_CAPTURE_STEP_OUTCOME": "success",
        "ATLAS_CAPTURE_RESULT": "captured",
        "ATLAS_BREADTH_VALIDATION_OUTCOME": "success",
        "ATLAS_LEADERSHIP_VALIDATION_OUTCOME": "success",
        "ATLAS_P2_04_STEP_OUTCOME": "success",
        "ATLAS_P2_04_RESULT": "populated",
        "ATLAS_P2_04_REASON": "ROTATION_POLICY_ABSENT_SOURCE_PAIR_ONLY",
        "ATLAS_P2_04_PATH": "data/observations/crypto_rotation_source_pair/2026-08-21/pair-aaaaaaaaaaaaaaaa.json",
        "ATLAS_P2_04_SHA256": "b" * 64,
        "ATLAS_P3_04_STEP_OUTCOME": "success",
        "ATLAS_P3_04_RESULT": "populated",
        "ATLAS_P3_04_REASON": "",
        "ATLAS_P3_04_PATH": "data/observations/crypto_global_universe/2026-08-21/packet.json",
        "ATLAS_P3_04_SHA256": "a" * 64,
        "ATLAS_REPOSITORY": "yonggeun1021-hub/atlas-data",
        "ATLAS_SERVER_URL": "https://github.com",
    }
    base.update(overrides)
    return base


class CryptoSchedulerTelemetryTest(unittest.TestCase):
    def require_step(self, name):
        step = workflow_step(name)
        self.assertIsNotNone(step, f"missing workflow step: {name}")
        return step

    def test_daily_slot_and_runner_delay_are_measured(self):
        record = REC.build_record(environment())

        self.assertEqual(record["slot"]["id"], "daily_0940_kst")
        self.assertEqual(record["slot"]["timing_status"], "measured")
        self.assertEqual(
            record["slot"]["expected_start_utc"], "2026-08-21T00:40:00Z"
        )
        self.assertEqual(
            record["slot"]["expected_start_kst"],
            "2026-08-21T09:40:00+09:00",
        )
        self.assertEqual(record["slot"]["delay_seconds"], 300)
        self.assertEqual(record["snapshot_date_utc"], "2026-08-21")

    def test_manual_and_unknown_schedule_do_not_invent_delay(self):
        manual = REC.build_record(
            environment(
                ATLAS_EVENT_NAME="workflow_dispatch",
                ATLAS_EVENT_SCHEDULE="",
            )
        )
        unknown = REC.build_record(
            environment(ATLAS_EVENT_SCHEDULE="45 0 * * *")
        )

        self.assertEqual(manual["slot"]["id"], "manual")
        self.assertIsNone(manual["slot"]["delay_seconds"])
        self.assertEqual(unknown["slot"]["id"], "unknown_schedule")
        self.assertEqual(unknown["slot"]["timing_status"], "unknown_schedule")
        self.assertIsNone(unknown["slot"]["delay_seconds"])

    def test_capture_and_both_validation_outcomes_are_separate(self):
        captured = REC.build_record(environment())
        skipped = REC.build_record(
            environment(ATLAS_CAPTURE_RESULT="skipped_existing")
        )
        incomplete = REC.build_record(
            environment(
                ATLAS_CAPTURE_STEP_OUTCOME="failure",
                ATLAS_CAPTURE_RESULT="incomplete_existing",
                ATLAS_BREADTH_VALIDATION_OUTCOME="skipped",
                ATLAS_LEADERSHIP_VALIDATION_OUTCOME="skipped",
            )
        )
        leadership_failed = REC.build_record(
            environment(ATLAS_LEADERSHIP_VALIDATION_OUTCOME="failure")
        )

        self.assertEqual(captured["capture"]["result"], "captured")
        self.assertTrue(captured["capture"]["raw_publication_eligible"])
        self.assertEqual(captured["p1_cr_06_validation"]["result"], "passed")
        self.assertEqual(captured["p1_cr_07_validation"]["result"], "passed")
        self.assertTrue(skipped["capture"]["provider_calls_skipped"])
        self.assertEqual(
            incomplete["capture"]["reason"],
            "incomplete_snapshot_path_exists",
        )
        self.assertEqual(
            incomplete["p1_cr_06_validation"]["result"], "not_run"
        )
        self.assertEqual(
            leadership_failed["p1_cr_07_validation"]["result"], "failed"
        )

    def test_p3_04_population_outcome_reason_path_and_sha_are_recorded(self):
        populated = REC.build_record(environment())
        blocked = REC.build_record(
            environment(
                ATLAS_P3_04_RESULT="blocked",
                ATLAS_P3_04_REASON="BREADTH_SELECTION_UNKNOWN:TAXONOMY_COVERAGE_UNKNOWN",
                ATLAS_P3_04_PATH="",
                ATLAS_P3_04_SHA256="",
            )
        )
        not_run = REC.build_record(
            environment(
                ATLAS_P3_04_STEP_OUTCOME="skipped",
                ATLAS_P3_04_RESULT="", ATLAS_P3_04_REASON="",
                ATLAS_P3_04_PATH="", ATLAS_P3_04_SHA256="",
            )
        )

        self.assertEqual(populated["p3_04_population"]["result"], "populated")
        self.assertIsNone(populated["p3_04_population"]["reason"])
        self.assertEqual(
            populated["p3_04_population"]["output_path"],
            "data/observations/crypto_global_universe/2026-08-21/packet.json",
        )
        self.assertEqual(populated["p3_04_population"]["payload_sha256"], "a" * 64)

        self.assertEqual(blocked["p3_04_population"]["result"], "blocked")
        self.assertEqual(
            blocked["p3_04_population"]["reason"],
            "BREADTH_SELECTION_UNKNOWN:TAXONOMY_COVERAGE_UNKNOWN",
        )
        self.assertIsNone(blocked["p3_04_population"]["output_path"])
        self.assertIsNone(blocked["p3_04_population"]["payload_sha256"])

        self.assertEqual(not_run["p3_04_population"]["result"], "not_run")

    def test_p2_04_source_pair_outcome_is_separate_and_policy_boundary_is_explicit(self):
        populated = REC.build_record(environment())
        blocked = REC.build_record(environment(
            ATLAS_P2_04_RESULT="blocked",
            ATLAS_P2_04_REASON="CURRENT_INSUFFICIENT_CONTIGUOUS_HISTORY",
            ATLAS_P2_04_PATH="",
            ATLAS_P2_04_SHA256="",
        ))
        self.assertEqual(
            populated["p2_04_source_pair_population"]["result"], "populated"
        )
        self.assertEqual(
            populated["p2_04_source_pair_population"]["reason"],
            "ROTATION_POLICY_ABSENT_SOURCE_PAIR_ONLY",
        )
        self.assertEqual(
            populated["p2_04_source_pair_population"]["payload_sha256"],
            "b" * 64,
        )
        self.assertEqual(
            blocked["p2_04_source_pair_population"]["result"], "blocked"
        )
        self.assertIsNone(blocked["p2_04_source_pair_population"]["output_path"])

    def test_authority_is_operations_only(self):
        record = REC.build_record(environment())

        self.assertEqual(record["authority"], "operations_telemetry_only")
        self.assertFalse(record["decision_eligible"])
        self.assertTrue(record["authority_flags"])
        self.assertFalse(any(record["authority_flags"].values()))

    def test_atomic_writer_uses_only_isolated_temp_root(self):
        tracked = ROOT / "data" / "operations" / "crypto_breadth_capture_runs"
        tracked_before = tracked.exists()
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "runs"
            self.assertEqual(
                REC.run(["--out-root", str(out_root)], environ=environment()),
                0,
            )
            paths = list(out_root.rglob("*.json"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(
                paths[0].relative_to(out_root).as_posix(),
                "2026-08-21/run-32340000001-attempt-1.json",
            )
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["github"]["event_name"], "schedule")
            self.assertFalse(list(paths[0].parent.glob(".*.tmp.*")))
        self.assertEqual(tracked.exists(), tracked_before)

    def test_invalid_identity_and_timestamp_fail_closed(self):
        cases = (
            environment(ATLAS_RUN_ID="bad"),
            environment(ATLAS_RUN_ATTEMPT="0"),
            environment(ATLAS_RUNNER_STARTED_AT_UTC="2026-08-21 00:45:00"),
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(REC.TelemetryError):
                    REC.build_record(case)

    def test_workflow_records_start_and_all_outcomes(self):
        runner = self.require_step("Capture Crypto Breadth runner start time")
        capture = self.require_step("Capture complete append-only Kraken USD universe")
        breadth = self.require_step("P1-CR-06 immutable snapshot validation")
        leadership = self.require_step("P1-CR-07 transient live replay")
        source_pair = self.require_step("Populate P2-04 Crypto Rotation source pair")
        population = self.require_step("Populate P3-04 Crypto source-coverage packet")
        telemetry = self.require_step("Record Crypto Breadth scheduler telemetry")
        checkout_index = next(
            index
            for index, step in enumerate(STEPS)
            if step.get("uses")
            == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        )

        self.assertLess(STEPS.index(runner), checkout_index)
        self.assertEqual(runner.get("id"), "runner_start")
        self.assertEqual(capture.get("id"), "capture")
        self.assertEqual(breadth.get("id"), "breadth_validation")
        self.assertEqual(leadership.get("id"), "leadership_validation")
        self.assertEqual(source_pair.get("id"), "p2_04_source_pair")
        self.assertEqual(population.get("id"), "p3_04_population")
        self.assertEqual(telemetry.get("if"), "always()")
        self.assertGreater(STEPS.index(population), STEPS.index(leadership))
        self.assertGreater(STEPS.index(source_pair), STEPS.index(leadership))
        self.assertGreater(STEPS.index(telemetry), STEPS.index(population))
        self.assertIn("record_crypto_breadth_run.py", telemetry.get("run", ""))
        self.assertEqual(
            set(telemetry.get("env", {})),
            {
                "ATLAS_EVENT_NAME",
                "ATLAS_EVENT_SCHEDULE",
                "ATLAS_RUN_ID",
                "ATLAS_RUN_ATTEMPT",
                "ATLAS_RUNNER_STARTED_AT_UTC",
                "ATLAS_CAPTURE_STEP_OUTCOME",
                "ATLAS_CAPTURE_RESULT",
                "ATLAS_BREADTH_VALIDATION_OUTCOME",
                "ATLAS_LEADERSHIP_VALIDATION_OUTCOME",
                "ATLAS_P2_04_STEP_OUTCOME",
                "ATLAS_P2_04_RESULT",
                "ATLAS_P2_04_REASON",
                "ATLAS_P2_04_PATH",
                "ATLAS_P2_04_SHA256",
                "ATLAS_P3_04_STEP_OUTCOME",
                "ATLAS_P3_04_RESULT",
                "ATLAS_P3_04_REASON",
                "ATLAS_P3_04_PATH",
                "ATLAS_P3_04_SHA256",
                "ATLAS_REPOSITORY",
                "ATLAS_SERVER_URL",
            },
        )

    def test_population_step_never_fails_job_on_blocked_and_commits_separately(self):
        population = self.require_step("Populate P3-04 Crypto source-coverage packet")
        commit_raw = self.require_step("Commit immutable raw snapshot and run telemetry")
        commit_population = self.require_step("Commit P3-04 source-coverage population")

        self.assertIn("crypto_forward_universe_populate.py", population.get("run", ""))
        self.assertEqual(commit_population.get("if"), "always()")
        self.assertGreater(STEPS.index(commit_population), STEPS.index(commit_raw))
        command = commit_population.get("run", "")
        self.assertIn("data/observations/crypto_global_universe", command)
        self.assertIn('git pull --rebase origin "$DEFAULT_BRANCH"', command)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', command)

    def test_capture_guard_precedes_network_and_marks_incomplete_path(self):
        capture = self.require_step("Capture complete append-only Kraken USD universe")
        command = capture.get("run", "")

        self.assertLess(command.index("_manifest.json"), command.index("crypto_breadth_capture.py"))
        self.assertLess(command.index("skipped_existing"), command.index("crypto_breadth_capture.py"))
        self.assertIn("result=incomplete_existing", command)

    def test_commit_keeps_telemetry_and_raw_scope_separate(self):
        commit = self.require_step(
            "Commit immutable raw snapshot and run telemetry"
        )
        command = commit.get("run", "")

        self.assertEqual(commit.get("if"), "always()")
        self.assertIn("data/operations/crypto_breadth_capture_runs", command)
        self.assertIn('[ "$CAPTURE_RESULT" = "captured" ]', command)
        self.assertIn(
            'git add "evidence/crypto/breadth/raw/$SNAPSHOT_DATE"', command
        )
        self.assertNotIn("git add evidence/crypto/breadth/raw\n", command)
        self.assertNotIn("data/factors", command)
        self.assertIn('git pull --rebase origin "$DEFAULT_BRANCH"', command)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', command)


if __name__ == "__main__":
    unittest.main()
