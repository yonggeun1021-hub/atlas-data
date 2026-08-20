#!/usr/bin/env python3
"""P0-04 KRX post-close scheduler telemetry offline regression."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "record_krx_post_close_run.py"
WORKFLOW = ROOT / ".github" / "workflows" / "krx-post-close.yml"

SPEC = importlib.util.spec_from_file_location("record_krx_post_close_run", SCRIPT)
REC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REC)

with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)

STEPS = WF["jobs"]["observe"]["steps"]


def workflow_step(name):
    for step in STEPS:
        if step.get("name") == name:
            return step
    return None


def environment(**overrides):
    base = {
        "ATLAS_EVENT_NAME": "schedule",
        "ATLAS_EVENT_SCHEDULE": "5 7 * * 1-5",
        "ATLAS_RUN_ID": "32350000001",
        "ATLAS_RUN_ATTEMPT": "1",
        "ATLAS_RUNNER_STARTED_AT_UTC": "2026-08-20T07:12:30Z",
        "ATLAS_GUARD_STEP_OUTCOME": "success",
        "ATLAS_GUARD_RESULT": "stale",
        "ATLAS_GUARD_SKIP": "no",
        "ATLAS_POST_CLOSE_STEP_OUTCOME": "success",
        "ATLAS_REPOSITORY": "yonggeun1021-hub/atlas-data",
        "ATLAS_SERVER_URL": "https://github.com",
    }
    base.update(overrides)
    return base


class P004SchedulerTelemetryTest(unittest.TestCase):
    def require_step(self, name):
        step = workflow_step(name)
        self.assertIsNotNone(step, f"missing workflow step: {name}")
        return step

    def test_three_slots_measure_runner_delay_and_kst_date(self):
        cases = (
            ("5 7 * * 1-5", "2026-08-20T07:12:30Z", "primary_1605_kst", 450),
            ("25 7 * * 1-5", "2026-08-20T07:31:00Z", "backup_1625_kst", 360),
            ("45 7 * * 1-5", "2026-08-20T07:48:10Z", "final_1645_kst", 190),
        )

        for cron, observed, slot_id, delay in cases:
            with self.subTest(cron=cron):
                record = REC.build_record(
                    environment(
                        ATLAS_EVENT_SCHEDULE=cron,
                        ATLAS_RUNNER_STARTED_AT_UTC=observed,
                    )
                )
                self.assertEqual(record["slot"]["id"], slot_id)
                self.assertEqual(record["slot"]["timing_status"], "measured")
                self.assertEqual(record["slot"]["delay_seconds"], delay)
                self.assertEqual(record["observation_date_kst"], "2026-08-20")

    def test_manual_and_unknown_schedule_do_not_invent_delay(self):
        manual = REC.build_record(
            environment(
                ATLAS_EVENT_NAME="workflow_dispatch",
                ATLAS_EVENT_SCHEDULE="",
            )
        )
        unknown = REC.build_record(
            environment(ATLAS_EVENT_SCHEDULE="15 7 * * 1-5")
        )

        self.assertEqual(manual["slot"]["id"], "manual")
        self.assertIsNone(manual["slot"]["delay_seconds"])
        self.assertEqual(unknown["slot"]["id"], "unknown_schedule")
        self.assertEqual(unknown["slot"]["timing_status"], "unknown_schedule")
        self.assertIsNone(unknown["slot"]["delay_seconds"])

    def test_guard_and_collection_outcomes_are_distinct(self):
        captured = REC.build_record(environment())
        skipped = REC.build_record(
            environment(
                ATLAS_GUARD_RESULT="fresh",
                ATLAS_GUARD_SKIP="yes",
                ATLAS_POST_CLOSE_STEP_OUTCOME="skipped",
            )
        )
        failed = REC.build_record(
            environment(ATLAS_POST_CLOSE_STEP_OUTCOME="failure")
        )
        guard_failed = REC.build_record(
            environment(
                ATLAS_GUARD_STEP_OUTCOME="failure",
                ATLAS_GUARD_RESULT="",
                ATLAS_POST_CLOSE_STEP_OUTCOME="skipped",
            )
        )

        self.assertEqual(captured["collection"]["result"], "captured")
        self.assertTrue(captured["collection"]["observation_publication_eligible"])
        self.assertEqual(skipped["collection"]["result"], "skipped_existing")
        self.assertTrue(skipped["collection"]["provider_calls_skipped"])
        self.assertEqual(failed["collection"]["result"], "failed")
        self.assertEqual(guard_failed["collection"]["result"], "not_run")
        self.assertEqual(
            guard_failed["collection"]["reason"], "guard_failed_or_cancelled"
        )

    def test_authority_stays_operations_only_and_unconfirmed(self):
        record = REC.build_record(environment())

        self.assertEqual(record["authority"], "operations_telemetry_only")
        self.assertFalse(record["decision_eligible"])
        self.assertTrue(record["authority_flags"])
        self.assertFalse(any(record["authority_flags"].values()))

    def test_atomic_writer_uses_only_isolated_temp_root(self):
        tracked = ROOT / "data" / "operations" / "krx_post_close_runs"
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
                "2026-08-20/run-32350000001-attempt-1.json",
            )
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["github"]["event_name"], "schedule")
            self.assertFalse(list(paths[0].parent.glob(".*.tmp.*")))
        self.assertEqual(tracked.exists(), tracked_before)

    def test_invalid_identity_and_timestamp_fail_closed(self):
        cases = (
            environment(ATLAS_RUN_ID="bad"),
            environment(ATLAS_RUN_ATTEMPT="0"),
            environment(ATLAS_RUNNER_STARTED_AT_UTC="2026-08-20 07:12:30"),
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(REC.TelemetryError):
                    REC.build_record(case)

    def test_workflow_records_runner_guard_and_collection_on_every_path(self):
        runner = self.require_step("Capture KRX post-close runner start time")
        guard = self.require_step("Guard — 오늘자 post-close bundle 확인")
        collect = self.require_step("Collect and publish KRX post-close observation")
        telemetry = self.require_step("Record KRX post-close scheduler telemetry")
        checkout_index = next(
            index
            for index, step in enumerate(STEPS)
            if step.get("uses")
            == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        )

        self.assertLess(STEPS.index(runner), checkout_index)
        self.assertEqual(runner.get("id"), "runner_start")
        checkout = STEPS[checkout_index]
        self.assertEqual(
            checkout.get("with", {}).get("ref"),
            "${{ github.event.repository.default_branch }}",
        )
        self.assertEqual(checkout.get("with", {}).get("fetch-depth"), 0)
        self.assertEqual(guard.get("id"), "guard")
        self.assertEqual(collect.get("id"), "post_close")
        self.assertEqual(telemetry.get("if"), "always()")
        self.assertGreater(STEPS.index(telemetry), STEPS.index(collect))
        self.assertIn("record_krx_post_close_run.py", telemetry.get("run", ""))
        self.assertEqual(
            set(telemetry.get("env", {})),
            {
                "ATLAS_EVENT_NAME",
                "ATLAS_EVENT_SCHEDULE",
                "ATLAS_RUN_ID",
                "ATLAS_RUN_ATTEMPT",
                "ATLAS_RUNNER_STARTED_AT_UTC",
                "ATLAS_GUARD_STEP_OUTCOME",
                "ATLAS_GUARD_RESULT",
                "ATLAS_GUARD_SKIP",
                "ATLAS_POST_CLOSE_STEP_OUTCOME",
                "ATLAS_REPOSITORY",
                "ATLAS_SERVER_URL",
            },
        )

    def test_guard_failure_prevents_provider_collection(self):
        condition = "steps.guard.outcome == 'success' && steps.guard.outputs.skip != 'yes'"
        setup = next(step for step in STEPS if str(step.get("uses", "")).startswith("actions/setup-python@"))
        install = self.require_step("Install deps")
        collect = self.require_step("Collect and publish KRX post-close observation")

        self.assertEqual(setup.get("if"), condition)
        self.assertEqual(install.get("if"), condition)
        self.assertEqual(collect.get("if"), condition)

    def test_commit_preserves_telemetry_and_observation_boundaries(self):
        commit = self.require_step("Commit post-close observation evidence")
        command = commit.get("run", "")

        self.assertEqual(commit.get("if"), "always()")
        self.assertIn("data/operations/krx_post_close_runs", command)
        self.assertIn("data/observations/krx_post_close", command)
        self.assertIn("data/incident/krx_post_close", command)
        self.assertIn("successful collection produced no staged", command)
        self.assertNotIn("latest_krx.json", command)
        self.assertNotIn("data/briefing", command)
        self.assertIn('git pull --rebase origin "$DEFAULT_BRANCH"', command)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', command)


if __name__ == "__main__":
    unittest.main()
