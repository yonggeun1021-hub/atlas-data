#!/usr/bin/env python3
"""BTC scheduler telemetry regression with no live GitHub or Kraken calls."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "record_btc_run.py"
WORKFLOW = ROOT / ".github" / "workflows" / "btc-price-capture.yml"

SPEC = importlib.util.spec_from_file_location("record_btc_run", SCRIPT)
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
        "ATLAS_EVENT_SCHEDULE": "20 0 * * *",
        "ATLAS_RUN_ID": "32330000001",
        "ATLAS_RUN_ATTEMPT": "1",
        "ATLAS_RUNNER_STARTED_AT_UTC": "2026-08-21T00:24:00Z",
        "ATLAS_CAPTURE_STEP_OUTCOME": "success",
        "ATLAS_CAPTURE_RESULT": "captured",
        "ATLAS_VALIDATION_STEP_OUTCOME": "success",
        "ATLAS_REPOSITORY": "yonggeun1021-hub/atlas-data",
        "ATLAS_SERVER_URL": "https://github.com",
    }
    base.update(overrides)
    return base


class BtcSchedulerTelemetryTest(unittest.TestCase):
    def require_step(self, name):
        step = workflow_step(name)
        self.assertIsNotNone(step, f"missing workflow step: {name}")
        return step

    def test_schedule_slot_and_delay_are_machine_readable(self):
        record = REC.build_record(environment())

        self.assertEqual(record["slot"]["id"], "daily_0920_kst")
        self.assertEqual(record["slot"]["timing_status"], "measured")
        self.assertEqual(
            record["slot"]["expected_start_utc"], "2026-08-21T00:20:00Z"
        )
        self.assertEqual(
            record["slot"]["expected_start_kst"],
            "2026-08-21T09:20:00+09:00",
        )
        self.assertEqual(record["slot"]["delay_seconds"], 240)
        self.assertEqual(record["snapshot_date_utc"], "2026-08-21")
        self.assertEqual(
            record["github"]["run_url"],
            "https://github.com/yonggeun1021-hub/atlas-data/actions/runs/32330000001",
        )

    def test_manual_dispatch_does_not_claim_schedule_or_delay(self):
        record = REC.build_record(
            environment(
                ATLAS_EVENT_NAME="workflow_dispatch",
                ATLAS_EVENT_SCHEDULE="",
                ATLAS_RUNNER_STARTED_AT_UTC="2026-08-20T00:16:42Z",
            )
        )

        self.assertEqual(record["github"]["event_name"], "workflow_dispatch")
        self.assertIsNone(record["github"]["event_schedule"])
        self.assertEqual(record["slot"]["id"], "manual")
        self.assertEqual(record["slot"]["timing_status"], "not_applicable")
        self.assertIsNone(record["slot"]["delay_seconds"])
        self.assertEqual(record["snapshot_date_utc"], "2026-08-20")

    def test_unknown_schedule_is_explicit_and_unmeasured(self):
        record = REC.build_record(
            environment(ATLAS_EVENT_SCHEDULE="25 0 * * *")
        )

        self.assertEqual(record["slot"]["id"], "unknown_schedule")
        self.assertEqual(record["slot"]["timing_status"], "unknown_schedule")
        self.assertIsNone(record["slot"]["expected_start_utc"])
        self.assertIsNone(record["slot"]["delay_seconds"])

    def test_capture_and_validation_outcomes_stay_separate(self):
        captured = REC.build_record(environment())
        skipped = REC.build_record(
            environment(ATLAS_CAPTURE_RESULT="skipped_existing")
        )
        failed = REC.build_record(
            environment(
                ATLAS_CAPTURE_STEP_OUTCOME="failure",
                ATLAS_CAPTURE_RESULT="",
                ATLAS_VALIDATION_STEP_OUTCOME="skipped",
            )
        )
        invalid = REC.build_record(
            environment(ATLAS_VALIDATION_STEP_OUTCOME="failure")
        )

        self.assertEqual(captured["capture"]["result"], "captured")
        self.assertEqual(captured["validation"]["result"], "passed")
        self.assertTrue(captured["validation"]["raw_publication_eligible"])
        self.assertEqual(skipped["capture"]["result"], "skipped_existing")
        self.assertTrue(skipped["capture"]["provider_call_skipped"])
        self.assertEqual(failed["capture"]["result"], "failed")
        self.assertEqual(failed["validation"]["result"], "not_run")
        self.assertEqual(invalid["validation"]["result"], "failed")
        self.assertFalse(invalid["validation"]["raw_publication_eligible"])

    def test_authority_is_operations_only(self):
        record = REC.build_record(environment())

        self.assertEqual(record["authority"], "operations_telemetry_only")
        self.assertFalse(record["decision_eligible"])
        self.assertTrue(record["authority_flags"])
        self.assertFalse(any(record["authority_flags"].values()))

    def test_writer_is_atomic_and_temp_root_isolated(self):
        tracked = ROOT / "data" / "operations" / "btc_capture_runs"
        tracked_before = tracked.exists()
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "runs"
            self.assertEqual(
                REC.run(
                    ["--out-root", str(out_root)],
                    environ=environment(),
                ),
                0,
            )
            paths = list(out_root.rglob("*.json"))
            self.assertEqual(len(paths), 1)
            self.assertEqual(
                paths[0].relative_to(out_root).as_posix(),
                "2026-08-21/run-32330000001-attempt-1.json",
            )
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["github"]["event_name"], "schedule")
            self.assertFalse(list(paths[0].parent.glob(".*.tmp.*")))
        self.assertEqual(tracked.exists(), tracked_before)

    def test_invalid_identity_and_timestamp_fail_closed(self):
        cases = (
            environment(ATLAS_RUN_ID="not-a-number"),
            environment(ATLAS_RUN_ATTEMPT="0"),
            environment(ATLAS_RUNNER_STARTED_AT_UTC="2026-08-21 00:24:00"),
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(REC.TelemetryError):
                    REC.build_record(case)

    def test_workflow_records_start_before_checkout_and_every_outcome(self):
        runner = self.require_step("Capture BTC runner start time")
        capture = self.require_step("Capture immutable Kraken BTC/USD daily OHLC")
        validation = self.require_step(
            "Validate BTC Trend and Risk from immutable snapshot"
        )
        telemetry = self.require_step("Record BTC scheduler telemetry")
        checkout_index = next(
            index
            for index, step in enumerate(STEPS)
            if step.get("uses")
            == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
        )

        self.assertLess(STEPS.index(runner), checkout_index)
        self.assertEqual(runner.get("id"), "runner_start")
        self.assertEqual(capture.get("id"), "capture")
        self.assertEqual(validation.get("id"), "validation")
        capture_command = capture.get("run", "")
        self.assertLess(
            capture_command.index("_manifest.json"),
            capture_command.index("api.kraken.com/0/public/OHLC"),
        )
        self.assertLess(
            capture_command.index("skipped_existing"),
            capture_command.index("api.kraken.com/0/public/OHLC"),
        )
        self.assertEqual(telemetry.get("if"), "always()")
        self.assertIn("record_btc_run.py", telemetry.get("run", ""))
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
                "ATLAS_VALIDATION_STEP_OUTCOME",
                "ATLAS_REPOSITORY",
                "ATLAS_SERVER_URL",
            },
        )

    def test_commit_publishes_telemetry_but_only_valid_new_raw(self):
        commit = self.require_step("Commit BTC price evidence")
        command = commit.get("run", "")

        self.assertEqual(commit.get("if"), "always()")
        self.assertIn("git add data/operations/btc_capture_runs", command)
        self.assertIn('[ "$CAPTURE_RESULT" = "captured" ]', command)
        self.assertIn('[ "$VALIDATION_OUTCOME" = "success" ]', command)
        self.assertIn(
            'git add "evidence/crypto/btc/raw/$SNAPSHOT_DATE"', command
        )
        self.assertNotIn("git add evidence/crypto/btc/raw\n", command)
        self.assertIn('git pull --rebase origin "$DEFAULT_BRANCH"', command)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', command)


if __name__ == "__main__":
    unittest.main()
