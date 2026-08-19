#!/usr/bin/env python3
"""Stablecoin three-slot scheduling and external observer contract regression.

No live GitHub or DefiLlama calls are made.  Production helpers write only to
isolated temporary roots, and the observer is read-only.
"""

import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "stablecoin-capture.yml"
RECORDER = ROOT / ".github" / "scripts" / "record_stablecoin_run.py"
OBSERVER = ROOT / ".github" / "scripts" / "check_stablecoin_capture.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REC = load_module("record_stablecoin_run", RECORDER)
OBS = load_module("check_stablecoin_capture", OBSERVER)

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
        "ATLAS_EVENT_SCHEDULE": "20 6 * * *",
        "ATLAS_RUN_ID": "40000000001",
        "ATLAS_RUN_ATTEMPT": "1",
        "ATLAS_RUNNER_STARTED_AT_UTC": "2026-08-20T06:47:30Z",
        "ATLAS_CAPTURE_STEP_OUTCOME": "success",
        "ATLAS_CAPTURE_RESULT": "captured",
        "ATLAS_REPOSITORY": "yonggeun1021-hub/atlas-data",
        "ATLAS_SERVER_URL": "https://github.com",
    }
    base.update(overrides)
    return base


def create_ready_snapshot(raw_root, snapshot_date="2026-08-20"):
    target = Path(raw_root) / snapshot_date
    target.mkdir(parents=True)
    for name in OBS.REQUIRED_FILES:
        (target / name).write_text("fixture\n", encoding="utf-8")
    return target


class StablecoinScheduleHardeningTest(unittest.TestCase):
    def require_step(self, name):
        step = workflow_step(name)
        self.assertIsNotNone(step, f"missing workflow step: {name}")
        return step

    def test_workflow_has_three_distinct_kst_slots_and_concurrency(self):
        triggers = WF.get("on", WF.get(True))
        schedules = {item["cron"] for item in triggers["schedule"]}

        self.assertEqual(
            schedules,
            {"20 6 * * *", "20 7 * * *", "20 8 * * *"},
        )
        self.assertEqual(
            WF["concurrency"],
            {
                "group": "atlas-stablecoin-daily-capture",
                "cancel-in-progress": False,
            },
        )

    def test_guard_precedes_provider_calls_and_staging_is_atomic(self):
        capture = self.require_step("Capture raw snapshot (append-only)")
        command = capture["run"]

        self.assertEqual(capture.get("id"), "capture")
        self.assertLess(command.index("_sha256.txt"), command.index("curl"))
        self.assertLess(command.index("skipped_existing"), command.index("curl"))
        self.assertLess(command.index('if [ -e "$DIR" ]'), command.index("curl"))
        self.assertIn("mktemp -d \"$RUNNER_TEMP/stablecoin.", command)
        self.assertIn('mv "$STAGING" "$DIR"', command)
        self.assertLess(
            command.index("stablecoin_revision_contract.py\" validate"),
            command.index('mv "$STAGING" "$DIR"'),
        )

    def test_workflow_records_runner_slot_and_capture_on_every_path(self):
        runner = self.require_step("Capture runner start time")
        telemetry = self.require_step("Record Stablecoin scheduler telemetry")
        checkout_index = next(
            index
            for index, step in enumerate(STEPS)
            if step.get("uses") == "actions/checkout@v4"
        )

        self.assertLess(STEPS.index(runner), checkout_index)
        self.assertEqual(runner.get("id"), "runner_start")
        checkout = STEPS[checkout_index]
        self.assertEqual(
            checkout.get("with", {}).get("ref"),
            "${{ github.event.repository.default_branch }}",
        )
        self.assertEqual(telemetry.get("if"), "always()")
        self.assertIn("record_stablecoin_run.py", telemetry.get("run", ""))
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
                "ATLAS_REPOSITORY",
                "ATLAS_SERVER_URL",
            },
        )

    def test_commit_keeps_failed_partial_capture_out_of_staging(self):
        commit = self.require_step("Commit")
        command = commit.get("run", "")

        self.assertEqual(commit.get("if"), "always()")
        self.assertIn("data/operations/stablecoin_capture_runs", command)
        self.assertIn('if [ "$CAPTURE_RESULT" = "captured" ]', command)
        self.assertIn('evidence/stablecoin/raw/$SNAPSHOT_DATE', command)
        self.assertNotIn("git add evidence/stablecoin/raw\n", command)
        self.assertIn('git pull --rebase origin "$DEFAULT_BRANCH"', command)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', command)

    def test_recorder_measures_all_slots_and_run_url(self):
        cases = (
            ("20 6 * * *", "2026-08-20T06:47:30Z", "primary_1520_kst", 1650),
            ("20 7 * * *", "2026-08-20T07:31:00Z", "backup_1620_kst", 660),
            ("20 8 * * *", "2026-08-20T08:25:30Z", "final_1720_kst", 330),
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
                self.assertEqual(record["slot"]["delay_seconds"], delay)
                self.assertEqual(record["snapshot_date_utc"], "2026-08-20")
                self.assertEqual(
                    record["github"]["run_url"],
                    "https://github.com/yonggeun1021-hub/atlas-data/actions/runs/40000000001",
                )

    def test_recorder_distinguishes_skip_failure_and_manual(self):
        skipped = REC.build_record(
            environment(ATLAS_CAPTURE_RESULT="skipped_existing")
        )
        failed = REC.build_record(
            environment(
                ATLAS_CAPTURE_STEP_OUTCOME="failure",
                ATLAS_CAPTURE_RESULT="",
            )
        )
        incomplete = REC.build_record(
            environment(
                ATLAS_CAPTURE_STEP_OUTCOME="failure",
                ATLAS_CAPTURE_RESULT="incomplete_existing",
            )
        )
        manual = REC.build_record(
            environment(
                ATLAS_EVENT_NAME="workflow_dispatch",
                ATLAS_EVENT_SCHEDULE="",
            )
        )

        self.assertTrue(skipped["capture"]["provider_call_skipped"])
        self.assertEqual(failed["capture"]["result"], "failed")
        self.assertEqual(
            incomplete["capture"]["reason"],
            "incomplete_snapshot_path_exists",
        )
        self.assertEqual(manual["slot"]["id"], "manual")
        self.assertIsNone(manual["slot"]["delay_seconds"])

    def test_recorder_writes_only_to_isolated_root(self):
        tracked = ROOT / "data" / "operations" / "stablecoin_capture_runs"
        tracked_before = tracked.exists()
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "runs"
            self.assertEqual(
                REC.run(
                    ["--out-root", str(out_root)], environ=environment()
                ),
                0,
            )
            paths = list(out_root.rglob("*.json"))
            self.assertEqual(len(paths), 1)
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["capture"]["result"], "captured")
        self.assertEqual(tracked.exists(), tracked_before)

    def test_observer_is_pending_before_deadline_and_missing_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            date = dt.date(2026, 8, 20)
            pending = OBS.observe(
                date,
                dt.datetime(2026, 8, 20, 8, 24, tzinfo=dt.timezone.utc),
                root / "raw",
                root / "runs",
            )
            missing = OBS.observe(
                date,
                dt.datetime(2026, 8, 20, 8, 25, tzinfo=dt.timezone.utc),
                root / "raw",
                root / "runs",
            )

        self.assertEqual(pending["status"], "PENDING")
        self.assertFalse(pending["alert_required"])
        self.assertEqual(missing["status"], "MISSING")
        self.assertEqual(
            missing["classification"], "snapshot_missing_after_deadline"
        )
        self.assertTrue(missing["alert_required"])
        self.assertFalse(missing["manual_dispatch_authorized"])

    def test_observer_uses_captured_telemetry_for_slot_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            runs = root / "runs"
            create_ready_snapshot(raw)
            record = REC.build_record(
                environment(
                    ATLAS_EVENT_SCHEDULE="20 7 * * *",
                    ATLAS_RUNNER_STARTED_AT_UTC="2026-08-20T07:31:00Z",
                )
            )
            REC.write_record(record, runs)
            report = OBS.observe(
                dt.date(2026, 8, 20),
                dt.datetime(2026, 8, 20, 8, 25, tzinfo=dt.timezone.utc),
                raw,
                runs,
            )

        self.assertEqual(report["status"], "PRESENT")
        self.assertEqual(report["classification"], "present_backup")
        self.assertEqual(report["captured_lineage"]["run_id"], 40000000001)
        self.assertFalse(report["alert_required"])

    def test_observer_distinguishes_failed_run_from_missing_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = REC.build_record(
                environment(
                    ATLAS_CAPTURE_STEP_OUTCOME="failure",
                    ATLAS_CAPTURE_RESULT="",
                )
            )
            REC.write_record(record, root / "runs")
            report = OBS.observe(
                dt.date(2026, 8, 20),
                dt.datetime(2026, 8, 20, 8, 25, tzinfo=dt.timezone.utc),
                root / "raw",
                root / "runs",
            )

        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(
            report["classification"], "capture_failed_after_deadline"
        )
        self.assertEqual(report["failed_capture"]["run_id"], 40000000001)


if __name__ == "__main__":
    unittest.main()
