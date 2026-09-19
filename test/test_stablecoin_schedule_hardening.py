#!/usr/bin/env python3
"""Stablecoin three-slot scheduling and external observer contract regression.

No live GitHub or DefiLlama calls are made.  Production helpers write only to
isolated temporary roots, and the observer is read-only.
"""

import datetime as dt
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
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
REV = load_module(
    "stablecoin_revision_contract",
    ROOT / ".github" / "scripts" / "stablecoin_revision_contract.py",
)
NET = load_module(
    "stablecoin_net_issuance",
    ROOT / ".github" / "scripts" / "stablecoin_net_issuance.py",
)

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
        "ATLAS_EVENT_SCHEDULE": "50 5 * * *",
        "ATLAS_RUN_ID": "40000000001",
        "ATLAS_RUN_ATTEMPT": "1",
        "ATLAS_RUNNER_STARTED_AT_UTC": "2026-08-20T06:17:30Z",
        "ATLAS_CAPTURE_STEP_OUTCOME": "success",
        "ATLAS_CAPTURE_RESULT": "captured",
        "ATLAS_REPOSITORY": "yonggeun1021-hub/atlas-data",
        "ATLAS_SERVER_URL": "https://github.com",
    }
    base.update(overrides)
    return base


RETAINED_SNAPSHOT = ROOT / "evidence" / "stablecoin" / "raw" / "2026-09-13"
FAKE_CURL = """#!/bin/sh
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    http*) url="$1"; shift ;;
    *) shift ;;
  esac
done
case "$url" in
  */stablecoincharts/all) name=stablecoincharts_all.json ;;
  */stablecoincharts/Terra) name=stablecoincharts_Terra.json ;;
  */stablecoinchains) name=stablecoinchains.json ;;
  */stablecoins?includePrices=true) name=stablecoins_withprices.json ;;
  *) echo "unexpected url $url" >&2; exit 22 ;;
esac
cp "$FIXTURE_DIR/$name" "$out"
"""
FAKE_DATE = """#!/bin/sh
case "$*" in
  *%F*) echo "$FAKE_DAY" ;;
  *) echo "${FAKE_DAY}T05:52:00Z" ;;
esac
"""


def run_capture_step(base, *, day, event_name="schedule", schedule="50 5 * * *", broken_check=False,
                     guard_mode="", extra_env=None):
    """Execute the real capture step script offline against retained bytes."""
    base = Path(base)
    fixtures = base / "fixtures"
    if not fixtures.is_dir():
        fixtures.mkdir()
        for path in RETAINED_SNAPSHOT.glob("*.json.gz"):
            (fixtures / path.name[:-3]).write_bytes(gzip.decompress(path.read_bytes()))
    bin_dir = base / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("curl", FAKE_CURL), ("date", FAKE_DATE)):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    slug = "".join(
        ch if ch.isalnum() else "_"
        for ch in f"{day}-{event_name}-{schedule}-{guard_mode}-{int(broken_check)}"
    )
    root = base / f"root-{slug}"
    scripts = root / ".github" / "scripts"
    scripts.mkdir(parents=True)
    for path in (ROOT / ".github" / "scripts").iterdir():
        (scripts / path.name).symlink_to(path)
    if broken_check:
        (scripts / "stablecoin_net_issuance.py").unlink()
        (scripts / "stablecoin_net_issuance.py").write_text(
            "raise RuntimeError('forced current-row check failure')\n", encoding="utf-8")
    runner_temp = base / f"runner-{root.name}"
    runner_temp.mkdir()
    output = base / f"output-{root.name}"
    output.write_text("", encoding="utf-8")
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", FIXTURE_DIR=str(fixtures),
               FAKE_DAY=day, GITHUB_OUTPUT=str(output), RUNNER_TEMP=str(runner_temp),
               EVENT_NAME=event_name, EVENT_SCHEDULE=schedule,
               DISPATCH_GUARD_MODE=guard_mode)
    env.update(extra_env or {})
    command = workflow_step("Capture raw snapshot (append-only)")["run"]
    completed = subprocess.run(["bash", "-e", "-c", command], cwd=root, env=env,
                               capture_output=True, text=True, timeout=300)
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines() if "=" in line)
    return completed, outputs, root / "evidence" / "stablecoin" / "raw" / day


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

    def test_workflow_has_distinct_kst_slots_and_concurrency(self):
        triggers = WF.get("on", WF.get(True))
        schedules = {item["cron"] for item in triggers["schedule"]}

        self.assertEqual(
            schedules,
            {"50 5 * * *", "20 6 * * *", "20 7 * * *", "20 8 * * *"},
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
        self.assertEqual(telemetry.get("if"), "always()")
        self.assertIn("record_stablecoin_run.py", telemetry.get("run", ""))
        self.assertEqual(
            set(telemetry.get("env", {})),
            {
                "ATLAS_EVENT_NAME",
                "ATLAS_EVENT_SCHEDULE",
                "ATLAS_DISPATCH_GUARD_MODE",
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
            ("50 5 * * *", "2026-08-20T06:17:30Z", "primary_1450_kst", 1650),
            ("20 6 * * *", "2026-08-20T06:47:30Z", "backup_1520_kst", 1650),
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

    def test_early_slot_without_current_row_is_pending_not_failed(self):
        capture = self.require_step("Capture raw snapshot (append-only)")
        command = capture["run"]
        self.assertIn("pending_current_observation", command)
        self.assertLess(command.index("pending_current_observation"), command.index('mv "$STAGING" "$DIR"'))
        population = self.require_step("Populate P3-09 Crypto supply-demand raw features")
        self.assertIn("pending_current_observation", population["if"])
        pending = REC.build_record(environment(ATLAS_CAPTURE_RESULT="pending_current_observation"))
        self.assertEqual(pending["capture"]["result"], "pending_current_observation")
        self.assertFalse(pending["capture"]["provider_call_skipped"])

    def test_capture_step_with_current_row_publishes_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(tmp, day="2026-09-13")
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("result"), "captured")
            self.assertTrue((snapshot / "_sha256.txt").is_file())

    def test_absent_current_row_on_early_slots_leaves_path_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            for schedule in ("50 5 * * *", "20 6 * * *", "20 7 * * *"):
                with self.subTest(schedule=schedule):
                    completed, outputs, snapshot = run_capture_step(tmp, day="2026-09-14", schedule=schedule)
                    self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
                    self.assertEqual(outputs.get("result"), "pending_current_observation")
                    self.assertFalse(snapshot.exists())

    def test_final_schedule_slot_still_captures_without_current_row(self):
        """The ratified 08:20Z evidence-preservation slot is unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-14", schedule="20 8 * * *")
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("result"), "captured")
            self.assertTrue((snapshot / "_sha256.txt").is_file())
            trigger = json.loads((snapshot / "_trigger.json").read_text(encoding="utf-8"))
            self.assertEqual(trigger["trigger"], "schedule")
            self.assertEqual(
                trigger["pending_current_observation_guard"],
                "EXEMPT_SCHEDULE_FINAL_SLOT_20_8",
            )

    def test_current_row_check_error_still_captures_raw_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(tmp, day="2026-09-14", broken_check=True)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("result"), "captured")
            self.assertTrue((snapshot / "_sha256.txt").is_file())
            self.assertIn("current-row check failed; capturing the snapshot anyway", completed.stdout)

    def test_observer_keeps_legacy_primary_slot_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_ready_snapshot(root / "raw")
            for slot_id, expected in (("primary_1520_kst", "present_primary"),
                                      ("primary_1450_kst", "present_primary"),
                                      ("backup_1520_kst", "present_backup")):
                with self.subTest(slot_id=slot_id):
                    runs = root / f"runs-{slot_id}"
                    record = REC.build_record(environment())
                    record["slot"]["id"] = slot_id
                    REC.write_record(record, runs)
                    report = OBS.observe(dt.date(2026, 8, 20),
                                         dt.datetime(2026, 8, 20, 8, 25, tzinfo=dt.timezone.utc),
                                         root / "raw", runs)
                    self.assertEqual(report["classification"], expected)

    def test_crypto_runtime_cutoff_has_pre_cutoff_slots_off_the_hour(self):
        triggers = WF.get("on", WF.get(True))
        minutes = sorted(
            int(item["cron"].split()[1]) * 60 + int(item["cron"].split()[0])
            for item in triggers["schedule"]
        )
        pre_cutoff = [value for value in minutes if value < 7 * 60]
        self.assertEqual(pre_cutoff[0], 5 * 60 + 50)
        self.assertGreaterEqual(len(pre_cutoff), 2)
        for value in minutes:
            self.assertNotIn(value % 60, {0, 30})

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


class StablecoinDispatchGuardParityTest(unittest.TestCase):
    """A dispatched run must be exactly as safe as a scheduled early slot.

    The server schedule dispatcher can only be moved off alert-only if a
    dispatch cannot write a record the ``pending_current_observation`` guard
    would have refused on the scheduled path.  These are the regressions that
    hold that property: 2026-09-13 has the current UTC observation row in the
    retained bytes, 2026-09-14 does not.
    """

    # -- trigger authorisation, fail-closed ---------------------------------

    def test_dispatch_input_is_a_single_fail_closed_choice(self):
        triggers = WF.get("on", WF.get(True))
        dispatch = triggers["workflow_dispatch"]
        inputs = dispatch["inputs"]

        # Exactly one input, and nothing timestamp-shaped: the dispatcher has
        # no field through which it could hand the run an available_at.
        self.assertEqual(set(inputs), {"guard_mode"})
        guard = inputs["guard_mode"]
        self.assertEqual(guard["type"], "choice")
        self.assertTrue(guard["required"])
        # Omitting the input yields the default, and the default refuses.
        self.assertEqual(guard["default"], "refuse")
        self.assertEqual(set(guard["options"]), {"refuse", "schedule_equivalent"})

    def test_workflow_never_reads_any_input_other_than_guard_mode(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        references = set(
            re.findall(r"(?:github\.event\.)?inputs\.([A-Za-z0-9_-]+)", text)
        )
        self.assertEqual(references, {"guard_mode"})

    def test_dispatch_without_the_explicit_guard_mode_refuses_before_fetching(self):
        for guard_mode in ("", "refuse", "REFUSE", "schedule_equivalent ", "yes", "true"):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(guard_mode=guard_mode):
                completed, outputs, snapshot = run_capture_step(
                    tmp, day="2026-09-13", event_name="workflow_dispatch",
                    schedule="", guard_mode=guard_mode)
                self.assertEqual(completed.returncode, 1, completed.stdout[-2000:])
                self.assertEqual(outputs.get("result"), "dispatch_guard_mode_invalid")
                self.assertFalse(snapshot.exists())

    def test_schedule_equivalent_on_a_scheduled_event_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-13", guard_mode="schedule_equivalent")
            self.assertEqual(completed.returncode, 1, completed.stdout[-2000:])
            self.assertEqual(outputs.get("result"), "trigger_input_conflict")
            self.assertFalse(snapshot.exists())

    def test_the_refuse_default_can_never_break_the_scheduled_path(self):
        """The inputs context is empty on a schedule event, but if GitHub ever
        filled in the default, `refuse` must not brick the ratified slots."""
        for guard_mode in ("", "refuse"):
            with tempfile.TemporaryDirectory() as tmp, self.subTest(guard_mode=guard_mode):
                completed, outputs, snapshot = run_capture_step(
                    tmp, day="2026-09-13", guard_mode=guard_mode)
                self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
                self.assertEqual(outputs.get("result"), "captured")
                self.assertEqual(outputs.get("capture_trigger"), "schedule")
                trigger = json.loads((snapshot / "_trigger.json").read_text(encoding="utf-8"))
                self.assertIsNone(trigger["dispatch_guard_mode"])

    def test_unknown_trigger_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-13", event_name="push", schedule="")
            self.assertEqual(completed.returncode, 1, completed.stdout[-2000:])
            self.assertEqual(outputs.get("result"), "trigger_not_authorized")
            self.assertFalse(snapshot.exists())

    # -- guard parity -------------------------------------------------------

    def test_dispatch_without_the_current_row_refuses_like_an_early_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-14", event_name="workflow_dispatch",
                schedule="", guard_mode="schedule_equivalent")
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("result"), "pending_current_observation")
            self.assertFalse(snapshot.exists())

    def test_every_dispatch_refusal_leaves_the_repository_tree_untouched(self):
        """(c) A refused dispatch must not be able to make the day worse: no
        directory claimed, no partial bytes, nothing under evidence/ at all."""
        cases = (
            # guard_mode, day, broken_check, expected result
            ("refuse", "2026-09-13", False, "dispatch_guard_mode_invalid"),
            ("", "2026-09-13", False, "dispatch_guard_mode_invalid"),
            ("schedule_equivalent", "2026-09-14", False, "pending_current_observation"),
            ("schedule_equivalent", "2026-09-13", True, "dispatch_guard_undetermined"),
        )
        for guard_mode, day, broken, expected in cases:
            with tempfile.TemporaryDirectory() as tmp, self.subTest(expected=expected):
                completed, outputs, snapshot = run_capture_step(
                    tmp, day=day, event_name="workflow_dispatch", schedule="",
                    guard_mode=guard_mode, broken_check=broken)
                self.assertEqual(outputs.get("result"), expected, completed.stderr[-2000:])
                self.assertFalse(snapshot.exists())
                # The whole evidence/ subtree is absent, not merely the date dir,
                # so no later in-window run is blocked by a partial path.
                repo_root = snapshot.parents[3]
                self.assertFalse((repo_root / "evidence").exists())
                leftovers = [
                    path for path in repo_root.rglob("*")
                    if path.is_file() and ".github" not in path.parts
                ]
                self.assertEqual(leftovers, [])

    def test_dispatch_gets_no_final_slot_exemption(self):
        """08:20Z is a schedule-only exemption; a dispatch cannot borrow it."""
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-14", event_name="workflow_dispatch",
                schedule="20 8 * * *", guard_mode="schedule_equivalent")
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("result"), "pending_current_observation")
            self.assertFalse(snapshot.exists())

    def test_dispatch_with_an_undecidable_guard_refuses_fail_closed(self):
        """The schedule path keeps the raw bytes on a check error; a dispatch
        must not, because that is exactly the guard bypass being closed."""
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-13", event_name="workflow_dispatch",
                schedule="", guard_mode="schedule_equivalent", broken_check=True)
            self.assertEqual(completed.returncode, 1, completed.stdout[-2000:])
            self.assertEqual(outputs.get("result"), "dispatch_guard_undetermined")
            self.assertFalse(snapshot.exists())

    def test_dispatch_with_the_current_row_captures_and_is_recorded_as_dispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-13", event_name="workflow_dispatch",
                schedule="", guard_mode="schedule_equivalent")
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("result"), "captured")
            self.assertEqual(outputs.get("capture_trigger"), "workflow_dispatch")
            self.assertTrue((snapshot / "_sha256.txt").is_file())

            trigger = json.loads((snapshot / "_trigger.json").read_text(encoding="utf-8"))
            self.assertEqual(trigger["trigger"], "workflow_dispatch")
            self.assertEqual(trigger["dispatch_guard_mode"], "schedule_equivalent")
            self.assertEqual(trigger["current_row_check"], "present")
            self.assertEqual(trigger["pending_current_observation_guard"], "ENFORCED")
            self.assertFalse(trigger["available_at_supplied_by_trigger"])

    def test_scheduled_capture_is_recorded_as_scheduled(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(tmp, day="2026-09-13")
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("capture_trigger"), "schedule")
            trigger = json.loads((snapshot / "_trigger.json").read_text(encoding="utf-8"))
            self.assertEqual(trigger["trigger"], "schedule")
            self.assertEqual(trigger["event_schedule"], "50 5 * * *")
            self.assertIsNone(trigger["dispatch_guard_mode"])
            self.assertEqual(trigger["pending_current_observation_guard"], "ENFORCED")

    # -- available_at stays the provider fetch time --------------------------

    def test_dispatch_cannot_supply_or_backdate_available_at(self):
        """available_at is lineage-derived from _downloaded_at.txt, which the
        capture step writes from `date -u` immediately before the fetch.  A
        dispatch carrying timestamp-shaped environment cannot move it."""
        hostile = {
            "AVAILABLE_AT": "2026-09-13T00:00:00Z",
            "ATLAS_AVAILABLE_AT": "2026-09-13T00:00:00Z",
            "FETCHED_AT_UTC": "2026-09-13T00:00:00Z",
            "DOWNLOADED_AT": "2026-09-13T00:00:00Z",
            "SNAPSHOT_DATE": "2026-09-13",
        }
        with tempfile.TemporaryDirectory() as tmp:
            completed, outputs, snapshot = run_capture_step(
                tmp, day="2026-09-13", event_name="workflow_dispatch",
                schedule="", guard_mode="schedule_equivalent", extra_env=hostile)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertEqual(outputs.get("result"), "captured")

            downloaded = (snapshot / "_downloaded_at.txt").read_text(encoding="utf-8").strip()
            self.assertEqual(downloaded, "2026-09-13T05:52:00Z")

            manifest = json.loads((snapshot / "_manifest.json").read_text(encoding="utf-8"))
            for endpoint in manifest["endpoints"]:
                self.assertEqual(endpoint["fetched_at_utc"], downloaded)

            packet = NET.build_transform(snapshot)
            self.assertEqual(packet["lineage"]["available_at"], downloaded)
            self.assertNotIn(downloaded, hostile.values())

    def test_trigger_provenance_stays_out_of_the_revision_contract_inventory(self):
        """_trigger.json must not enter _sha256.txt or _manifest.json: the
        ratified revision contract compares both to the endpoint set exactly."""
        with tempfile.TemporaryDirectory() as tmp:
            completed, _, snapshot = run_capture_step(
                tmp, day="2026-09-13", event_name="workflow_dispatch",
                schedule="", guard_mode="schedule_equivalent")
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            self.assertNotIn("_trigger.json", (snapshot / "_sha256.txt").read_text(encoding="utf-8"))
            manifest = json.loads((snapshot / "_manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("_trigger.json", json.dumps(manifest))
            # The contract validator still accepts the published directory.
            REV.validate_snapshot(snapshot)

    # -- telemetry ----------------------------------------------------------

    def test_recorder_records_the_trigger_and_guard_state(self):
        dispatched = REC.build_record(
            environment(
                ATLAS_EVENT_NAME="workflow_dispatch",
                ATLAS_EVENT_SCHEDULE="",
                ATLAS_DISPATCH_GUARD_MODE="schedule_equivalent",
            )
        )
        self.assertEqual(dispatched["trigger"]["kind"], "workflow_dispatch")
        self.assertEqual(
            dispatched["trigger"]["dispatch_guard_mode"], "schedule_equivalent"
        )
        self.assertEqual(
            dispatched["trigger"]["pending_current_observation_guard"], "ENFORCED"
        )
        self.assertFalse(dispatched["trigger"]["final_slot_exemption_applied"])
        self.assertFalse(dispatched["trigger"]["available_at_supplied_by_trigger"])

        early = REC.build_record(environment())
        self.assertEqual(early["trigger"]["kind"], "schedule")
        self.assertIsNone(early["trigger"]["dispatch_guard_mode"])

        # A default leaking onto a schedule event is not a dispatch instruction.
        leaked = REC.build_record(environment(ATLAS_DISPATCH_GUARD_MODE="refuse"))
        self.assertEqual(leaked["trigger"]["kind"], "schedule")
        self.assertIsNone(leaked["trigger"]["dispatch_guard_mode"])
        self.assertEqual(
            early["trigger"]["pending_current_observation_guard"], "ENFORCED"
        )

        final = REC.build_record(environment(ATLAS_EVENT_SCHEDULE="20 8 * * *"))
        self.assertEqual(
            final["trigger"]["pending_current_observation_guard"],
            "EXEMPT_SCHEDULE_FINAL_SLOT_20_8",
        )
        self.assertTrue(final["trigger"]["final_slot_exemption_applied"])

    def test_recorder_keeps_guard_refusals_legible(self):
        cases = {
            "dispatch_guard_mode_invalid": (
                "dispatch_guard_mode_not_schedule_equivalent",
                True,
            ),
            "trigger_input_conflict": (
                "dispatch_input_present_on_schedule_event",
                True,
            ),
            "trigger_not_authorized": ("trigger_not_authorized", True),
            "dispatch_guard_undetermined": (
                "current_utc_observation_row_undetermined",
                False,
            ),
        }
        for result, (reason, provider_skipped) in cases.items():
            with self.subTest(result=result):
                record = REC.build_record(
                    environment(
                        ATLAS_EVENT_NAME="workflow_dispatch",
                        ATLAS_EVENT_SCHEDULE="",
                        ATLAS_DISPATCH_GUARD_MODE="schedule_equivalent",
                        ATLAS_CAPTURE_STEP_OUTCOME="failure",
                        ATLAS_CAPTURE_RESULT=result,
                    )
                )
                self.assertEqual(record["capture"]["result"], "refused_before_capture")
                self.assertEqual(record["capture"]["reason"], reason)
                self.assertEqual(
                    record["capture"]["provider_call_skipped"], provider_skipped
                )

    def test_telemetry_step_forwards_the_dispatch_guard_mode(self):
        telemetry = next(
            step for step in STEPS
            if step.get("name") == "Record Stablecoin scheduler telemetry"
        )
        self.assertEqual(
            telemetry["env"]["ATLAS_DISPATCH_GUARD_MODE"],
            "${{ inputs.guard_mode }}",
        )


if __name__ == "__main__":
    unittest.main()
