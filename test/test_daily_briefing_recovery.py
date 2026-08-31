import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "daily_briefing_recovery",
    ROOT / ".github" / "scripts" / "daily_briefing_recovery.py",
)
RECOVERY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(RECOVERY)


def run(created_at, *, run_id=10, attempt=1, status="completed", conclusion="failure"):
    return {
        "id": run_id,
        "event": "schedule",
        "created_at": created_at,
        "run_attempt": attempt,
        "status": status,
        "conclusion": conclusion,
    }


def job(conclusion, status="completed"):
    return {"name": "briefing", "status": status, "conclusion": conclusion}


class DailyBriefingRecoveryTest(unittest.TestCase):
    def test_selects_only_the_original_natural_slot_for_the_kst_date(self):
        runs = [
            run("2026-08-30T22:05:00Z", run_id=11),
            run("2026-08-31T09:30:00Z", run_id=12),
            {**run("2026-08-30T22:10:00Z", run_id=13), "event": "workflow_dispatch"},
        ]
        self.assertEqual(RECOVERY.select_target_run(runs, "2026-08-31", "morning")["id"], 11)
        self.assertEqual(RECOVERY.select_target_run(runs, "2026-08-31", "evening")["id"], 12)

    def test_successful_briefing_is_healthy_even_if_the_parallel_regression_cancelled(self):
        target = run("2026-08-30T22:05:00Z", conclusion="cancelled")
        self.assertEqual(RECOVERY.classify_recovery(target, [job("success")]), "HEALTHY")

    def test_failed_briefing_gets_a_bounded_failed_job_retry(self):
        target = run("2026-08-30T22:05:00Z", attempt=1)
        self.assertEqual(RECOVERY.classify_recovery(target, [job("failure")]), "RERUN_FAILED_JOBS")
        target["run_attempt"] = RECOVERY.MAX_RUN_ATTEMPTS
        self.assertEqual(RECOVERY.classify_recovery(target, [job("failure")]), "ATTEMPTS_EXHAUSTED")

    def test_running_or_non_retryable_runs_are_never_mutated(self):
        target = run("2026-08-30T22:05:00Z", status="in_progress", conclusion=None)
        self.assertEqual(RECOVERY.classify_recovery(target, []), "WAIT_RUNNING")
        target.update(status="completed", conclusion="success")
        self.assertEqual(RECOVERY.classify_recovery(target, [job("skipped")]), "NON_RETRYABLE")

    def test_workflow_has_two_checks_per_slot_and_no_manual_or_money_surface(self):
        workflow = (ROOT / ".github" / "workflows" / "daily-briefing-recovery.yml").read_text()
        for schedule in (
            'cron: "20 22 * * *"',
            'cron: "40 22 * * *"',
            'cron: "45 9 * * 1-5"',
            'cron: "5 10 * * 1-5"',
        ):
            self.assertIn(schedule, workflow)
        self.assertIn("actions: write", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("runs-on: [self-hosted, Linux, X64, atlas-data]", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        for forbidden in ("ORDER", "TRADING", "PRODUCTION", "KIS_", "UPBIT_"):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
