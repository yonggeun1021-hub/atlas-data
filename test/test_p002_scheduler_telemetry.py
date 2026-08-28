#!/usr/bin/env python3
"""P0-02 Daily Collect scheduler telemetry regression.

No live GitHub/KRX calls are made. Production helpers are exercised with an
isolated temporary output root so tracked data/ is never modified.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
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
CURRENT_CRONS = (
    ("55 20 * * 0-4", "primary_0555_kst"),
    ("15 21 * * 0-4", "backup_0615_kst"),
    ("35 21 * * 0-4", "final_0635_kst"),
)


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

    def test_workflow_uses_only_current_pre_gate_slots(self):
        raw = WORKFLOW.read_text(encoding="utf-8")
        scheduled = tuple(
            re.findall(r"^\s*-\s*cron:\s*'([^']+)'", raw, re.MULTILINE)
        )
        self.assertEqual(scheduled, tuple(cron for cron, _ in CURRENT_CRONS))

    def test_current_pre_gate_slots_have_measured_telemetry_identity(self):
        cases = (
            ("55 20 * * 0-4", "2026-08-18T21:00:00Z", "primary_0555_kst", 300),
            ("15 21 * * 0-4", "2026-08-18T21:21:00Z", "backup_0615_kst", 360),
            ("35 21 * * 0-4", "2026-08-18T21:48:20Z", "final_0635_kst", 800),
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
                self.assertEqual(record["slot"]["timing_status"], "measured")
                self.assertEqual(record["slot"]["delay_seconds"], expected_delay)

    def test_nominal_buffers_before_0655_are_60_40_20_minutes(self):
        kst_minutes = []
        for cron, _ in CURRENT_CRONS:
            minute, hour, *_ = cron.split()
            total = (int(hour) * 60 + int(minute) + 9 * 60) % (24 * 60)
            kst_minutes.append(total)

        checkpoint = 6 * 60 + 55
        self.assertEqual([checkpoint - minute for minute in kst_minutes], [60, 40, 20])

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
            self.assertEqual(len(paths), 2)
            run_path = next(
                path for path in paths if path.name.startswith("run-")
            )
            index_path = next(
                path for path in paths if path.name == "index.json"
            )
            payload = json.loads(run_path.read_text(encoding="utf-8"))
            index = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["github"]["run_id"], 32189764427)
            self.assertEqual(payload["slot"]["delay_seconds"], 2731)
            self.assertEqual(index["record_count"], 1)
            self.assertEqual(
                index["records"][0]["record_sha256"],
                hashlib.sha256(run_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                index["records"][0]["path"],
                "data/operations/collect_runs/2026-08-19/"
                "run-32189764427-attempt-1.json",
            )
            self.assertEqual(
                index["records"][0]["slot_id"],
                "primary_0605_kst",
            )
            self.assertEqual(index["records"][0]["timing_status"], "measured")
            self.assertEqual(index["summary"]["guard_stale_count"], 1)
            self.assertFalse(index["authority"]["data_readiness_authority"])

        self.assertEqual(tracked_root.exists(), tracked_before)

    def test_daily_index_is_sorted_and_rebuilt_from_exact_record_bytes(self):
        cases = (
            environment(
                ATLAS_EVENT_SCHEDULE="45 21 * * 0-4",
                ATLAS_RUN_ID="300",
                ATLAS_RUNNER_STARTED_AT_UTC="2026-08-18T22:00:00Z",
                ATLAS_GUARD_RESULT="fresh",
                ATLAS_GUARD_SKIP="yes",
            ),
            environment(
                ATLAS_EVENT_SCHEDULE="5 21 * * 0-4",
                ATLAS_RUN_ID="100",
                ATLAS_RUNNER_STARTED_AT_UTC="2026-08-18T21:50:31Z",
            ),
            environment(
                ATLAS_EVENT_SCHEDULE="25 21 * * 0-4",
                ATLAS_RUN_ID="200",
                ATLAS_RUNNER_STARTED_AT_UTC="2026-08-18T21:55:00Z",
                ATLAS_GUARD_RESULT="fresh",
                ATLAS_GUARD_SKIP="yes",
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "collect_runs"
            run_paths = []
            for item in cases:
                run_paths.append(
                    MODULE.write_record(MODULE.build_record(item), out_root)
                )
            target = MODULE.write_index(run_paths[0].parent)
            index = json.loads(target.read_text(encoding="utf-8"))

            self.assertEqual(index, MODULE.validate_index(index))
            self.assertEqual(index["record_count"], 3)
            self.assertEqual(
                [row["run_id"] for row in index["records"]],
                [100, 200, 300],
            )
            self.assertEqual(
                index["summary"],
                {
                    "measured_count": 3,
                    "guard_skip_count": 2,
                    "guard_stale_count": 1,
                },
            )
            for path, row in zip(
                sorted(
                    run_paths,
                    key=lambda item: json.loads(item.read_text())["runner"][
                        "observed_started_at_utc"
                    ],
                ),
                index["records"],
            ):
                self.assertEqual(
                    row["record_sha256"],
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )

    def test_run_record_is_idempotent_but_conflicting_overwrite_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "collect_runs"
            original = MODULE.build_record(environment())
            target = MODULE.write_record(original, out_root)

            self.assertEqual(MODULE.write_record(original, out_root), target)

            conflict = MODULE.build_record(
                environment(ATLAS_GUARD_RESULT="fresh", ATLAS_GUARD_SKIP="yes")
            )
            with self.assertRaisesRegex(
                MODULE.TelemetryError,
                "telemetry record conflict",
            ):
                MODULE.write_record(conflict, out_root)

    def test_malformed_record_or_tampered_index_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "collect_runs"
            path = MODULE.write_record(
                MODULE.build_record(environment()),
                out_root,
            )
            malformed = json.loads(path.read_text(encoding="utf-8"))
            malformed["slot"]["delay_seconds"] += 1
            path.write_text(json.dumps(malformed), encoding="utf-8")

            with self.assertRaisesRegex(
                MODULE.TelemetryError,
                "slot derivation mismatch",
            ):
                MODULE.build_index(path.parent)

            path.write_text(
                json.dumps(MODULE.build_record(environment())),
                encoding="utf-8",
            )
            index = MODULE.build_index(path.parent)
            index["summary"]["guard_stale_count"] = 0
            unsigned = dict(index)
            unsigned.pop("index_sha256")
            index["index_sha256"] = MODULE.payload_sha256(unsigned)

            with self.assertRaisesRegex(
                MODULE.TelemetryError,
                "index summary invalid",
            ):
                MODULE.validate_index(index)

    def test_rebuild_index_date_needs_no_run_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "collect_runs"
            path = MODULE.write_record(
                MODULE.build_record(environment()),
                out_root,
            )

            self.assertEqual(
                MODULE.run(
                    [
                        "--out-root",
                        str(out_root),
                        "--rebuild-index-date",
                        "2026-08-19",
                    ],
                    environ={},
                ),
                0,
            )
            self.assertTrue((path.parent / "index.json").exists())

            with self.assertRaises(MODULE.TelemetryError):
                MODULE.run(
                    [
                        "--out-root",
                        str(out_root),
                        "--rebuild-index-date",
                        "2026-02-30",
                    ],
                    environ={},
                )

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
            if item.get("uses")
            == "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
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
