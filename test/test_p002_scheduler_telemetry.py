#!/usr/bin/env python3
"""P0-02 Daily Collect scheduler telemetry regression.

No live GitHub/KRX calls are made. Production helpers are exercised with an
isolated temporary output root so tracked data/ is never modified.
"""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "record_collect_run.py"
WORKFLOW = ROOT / ".github" / "workflows" / "collect.yml"
SPEC = importlib.util.spec_from_file_location("record_collect_run", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

with WORKFLOW.open(encoding="utf-8") as fh:
    WF = yaml.safe_load(fh)

STEPS = WF["jobs"]["collect"]["steps"]


def workflow_step(name):
    for item in STEPS:
        if item.get("name") == name:
            return item
    return None


def environment(**overrides):
    base = {
        "ATLAS_EVENT_NAME": "schedule",
        "ATLAS_EVENT_SCHEDULE": "5 21 * * 0-4",
        "ATLAS_RUN_ID": "32189764427",
        "ATLAS_RUN_ATTEMPT": "1",
        "ATLAS_RUNNER_STARTED_AT_UTC": "2026-08-18T21:50:31Z",
        "ATLAS_GUARD_RESULT": "stale",
        "ATLAS_GUARD_SKIP": "no",
    }
    base.update(overrides)
    return base


class P002SchedulerTelemetryTest(unittest.TestCase):
    def require_workflow_step(self, name):
        found = workflow_step(name)
        self.assertIsNotNone(found, f"missing workflow step: {name}")
        return found

    def test_primary_slot_has_measured_runner_delay(self):
        record = MODULE.build_record(environment())

        self.assertEqual(record["slot"]["id"], "primary_0605_kst")
        self.assertEqual(record["slot"]["timing_status"], "measured")
        self.assertEqual(
            record["slot"]["expected_start_utc"],
            "2026-08-18T21:05:00Z",
        )
        self.assertEqual(
            record["slot"]["expected_start_kst"],
            "2026-08-19T06:05:00+09:00",
        )
        self.assertEqual(record["slot"]["delay_seconds"], 2731)

    def test_backup_and_final_slots_keep_distinct_identity(self):
        cases = (
            (
                "25 21 * * 0-4",
                "2026-08-18T21:31:00Z",
                "backup_0625_kst",
                360,
            ),
            (
                "45 21 * * 0-4",
                "2026-08-18T21:58:20Z",
                "final_0645_kst",
                800,
            ),
        )

        for cron, observed, expected_id, expected_delay in cases:
            with self.subTest(cron=cron):
                record = MODULE.build_record(
                    environment(
                        ATLAS_EVENT_SCHEDULE=cron,
                        ATLAS_RUNNER_STARTED_AT_UTC=observed,
                    )
                )
                self.assertEqual(record["slot"]["id"], expected_id)
                self.assertEqual(
                    record["slot"]["delay_seconds"],
                    expected_delay,
                )

    def test_delay_stays_nonnegative_across_utc_midnight(self):
        record = MODULE.build_record(
            environment(ATLAS_RUNNER_STARTED_AT_UTC="2026-08-19T00:05:00Z")
        )

        self.assertEqual(
            record["slot"]["expected_start_utc"],
            "2026-08-18T21:05:00Z",
        )
        self.assertEqual(record["slot"]["delay_seconds"], 10800)

    def test_manual_run_does_not_invent_a_scheduled_delay(self):
        record = MODULE.build_record(
            environment(
                ATLAS_EVENT_NAME="workflow_dispatch",
                ATLAS_EVENT_SCHEDULE="",
            )
        )

        self.assertEqual(record["slot"]["id"], "manual")
        self.assertEqual(record["slot"]["timing_status"], "not_applicable")
        self.assertIsNone(record["slot"]["expected_start_utc"])
        self.assertIsNone(record["slot"]["delay_seconds"])

    def test_unknown_schedule_is_explicit_and_unmeasured(self):
        record = MODULE.build_record(
            environment(ATLAS_EVENT_SCHEDULE="15 22 * * 0-4")
        )

        self.assertEqual(record["slot"]["id"], "unknown_schedule")
        self.assertEqual(record["slot"]["timing_status"], "unknown_schedule")
        self.assertIsNone(record["slot"]["delay_seconds"])

    def test_guard_result_and_skip_are_machine_readable(self):
        fresh = MODULE.build_record(
            environment(
                ATLAS_GUARD_RESULT="fresh",
                ATLAS_GUARD_SKIP="yes",
            )
        )
        unknown = MODULE.build_record(
            environment(
                ATLAS_GUARD_RESULT="",
                ATLAS_GUARD_SKIP="",
            )
        )

        self.assertEqual(fresh["guard"], {"result": "fresh", "skip": True})
        self.assertEqual(unknown["guard"], {"result": "unknown", "skip": None})

    def test_run_writes_only_to_isolated_output_root(self):
        tracked_root = ROOT / "data" / "operations" / "collect_runs"
        tracked_before = tracked_root.exists()

        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "collect_runs"
            self.assertEqual(
                MODULE.run(
                    ["--out-root", str(out_root)],
                    environ=environment(),
                ),
                0,
            )
            paths = list(out_root.rglob("*.json"))
            self.assertEqual(len(paths), 1)
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["github"]["run_id"], 32189764427)
            self.assertEqual(payload["slot"]["delay_seconds"], 2731)

        self.assertEqual(tracked_root.exists(), tracked_before)

    def test_invalid_identity_or_timestamp_fails_closed(self):
        cases = (
            environment(ATLAS_RUN_ID="not-a-number"),
            environment(ATLAS_RUN_ATTEMPT="0"),
            environment(ATLAS_RUNNER_STARTED_AT_UTC="2026-08-18 21:50:31"),
        )

        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(MODULE.TelemetryError):
                    MODULE.build_record(case)

    def test_workflow_captures_runner_start_before_checkout(self):
        capture_index = next(
            i
            for i, item in enumerate(STEPS)
            if item.get("name") == "Capture runner start time (P0-02)"
        )
        checkout_index = next(
            i
            for i, item in enumerate(STEPS)
            if item.get("uses") == "actions/checkout@v4"
        )
        capture = STEPS[capture_index]

        self.assertLess(capture_index, checkout_index)
        self.assertEqual(capture.get("id"), "runner_start")
        self.assertIn("date -u", capture.get("run", ""))
        self.assertIn("GITHUB_OUTPUT", capture.get("run", ""))

    def test_workflow_records_slot_and_guard_on_every_path(self):
        telemetry = self.require_workflow_step(
            "Record scheduler telemetry (P0-02)"
        )
        guard = self.require_workflow_step(
            "Guard — 오늘자 수집 여부 확인"
        )

        self.assertEqual(telemetry.get("if"), "always()")
        self.assertIn(
            "record_collect_run.py",
            telemetry.get("run", ""),
        )
        self.assertEqual(
            set(telemetry.get("env", {})),
            {
                "ATLAS_EVENT_NAME",
                "ATLAS_EVENT_SCHEDULE",
                "ATLAS_RUN_ID",
                "ATLAS_RUN_ATTEMPT",
                "ATLAS_RUNNER_STARTED_AT_UTC",
                "ATLAS_GUARD_RESULT",
                "ATLAS_GUARD_SKIP",
            },
        )
        self.assertIn("result=$RESULT", guard.get("run", ""))


if __name__ == "__main__":
    unittest.main()
