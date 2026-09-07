import importlib.util
import json
import tempfile
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


def write_json(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


class FakeApi:
    def __init__(self, target, jobs):
        self.target = target
        self.jobs = jobs
        self.posts = []

    def request(self, method, path):
        if method == "POST":
            self.posts.append(path)
            return {}
        if path.startswith("/actions/workflows/"):
            return {"workflow_runs": [self.target]}
        if path.endswith("/jobs?filter=latest&per_page=100"):
            return {"jobs": self.jobs}
        raise AssertionError((method, path))


class DailyBriefingRecoveryTest(unittest.TestCase):
    def test_selects_only_the_original_natural_slot_for_the_kst_date(self):
        runs = [
            run("2026-08-30T22:05:00Z", run_id=11),
            run("2026-08-31T09:30:00Z", run_id=12),
            {**run("2026-08-30T22:10:00Z", run_id=13), "event": "workflow_dispatch"},
        ]
        self.assertEqual(RECOVERY.select_target_run(runs, "2026-08-31", "morning")["id"], 11)
        self.assertEqual(RECOVERY.select_target_run(runs, "2026-08-31", "evening")["id"], 12)

    def test_successful_briefing_requires_complete_handoff(self):
        target = run("2026-08-30T22:05:00Z", conclusion="cancelled")
        complete = {"status": "COMPLETE", "checks": {}, "alert": False}
        self.assertEqual(
            RECOVERY.classify_recovery(target, [job("success")], complete),
            "HEALTHY",
        )
        self.assertEqual(
            RECOVERY.classify_recovery(target, [job("success")]),
            "HANDOFF_STATUS_REQUIRED",
        )

    def test_successful_producer_never_reruns_when_final_delivery_is_missing(self):
        target = run("2026-08-30T22:05:00Z", conclusion="cancelled")
        report = {
            "status": "FINAL_DRAIN_MISSING",
            "reason": "FINAL_DRAIN_MISSING",
            "checks": {},
            "alert": True,
        }
        self.assertEqual(
            RECOVERY.classify_recovery(target, [job("success")], report),
            "HANDOFF_FAILED",
        )

    def test_successful_producer_preserves_sealed_hold(self):
        target = run("2026-08-30T22:05:00Z", conclusion="cancelled")
        report = {
            "status": "WAITING_VALIDATION",
            "reason": "SOURCE_REVIEW_REQUIRED",
            "checks": {
                "semantic_verdict": {
                    "exists": True,
                    "status_deliverable": False,
                }
            },
            "alert": True,
        }
        self.assertEqual(
            RECOVERY.classify_recovery(target, [job("success")], report),
            "HANDOFF_HOLD",
        )

    def test_run_watchdog_accepts_only_the_canonical_complete_receipt_chain(self):
        date = "2026-08-31"
        slot = "morning"
        target = run("2026-08-30T22:05:00Z", conclusion="cancelled")
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            write_json(repo, f"evidence/daily_briefing/{slot}/{date}/index.json", {})
            write_json(
                repo,
                f"evidence/validated_briefing_portal/{slot}/{date}/index.json",
                {"latest_revision": 1, "revisions": []},
            )
            write_json(
                repo,
                f"data/briefing/finalization/{date}/{slot}/portal-final-receipt-rev-001.json",
                {},
            )
            delivery = (
                repo
                / f"data/briefing/finalization/{date}/{slot}/delivery_receipt.json"
            )
            delivery.parent.mkdir(parents=True, exist_ok=True)
            delivery.write_text("{}", encoding="utf-8")
            api = FakeApi(target, [job("success")])
            result = RECOVERY.run_watchdog(slot, date, False, api, repo)
            self.assertIn("handoff is COMPLETE", result)
            self.assertEqual(api.posts, [])

            delivery.unlink()
            with self.assertRaisesRegex(
                RECOVERY.RecoveryError,
                "BRIEFING_HANDOFF_FAILED.*FINAL_DRAIN_MISSING",
            ):
                RECOVERY.run_watchdog(slot, date, False, api, repo)
            self.assertEqual(api.posts, [])

    def test_run_watchdog_reports_hold_without_rerun(self):
        date = "2026-08-31"
        slot = "morning"
        target = run("2026-08-30T22:05:00Z", conclusion="cancelled")
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            write_json(repo, f"evidence/daily_briefing/{slot}/{date}/index.json", {})
            write_json(repo, f"evidence/briefing_events/{date}/{slot}/index.json", {})
            write_json(
                repo,
                f"data/briefing/finalization/{date}/{slot}/validation-rev-001.json",
                {
                    "validation_status": "HOLD",
                    "routing": {"status_deliverable": False},
                    "hold_reasons": ["SOURCE_REVIEW_REQUIRED"],
                },
            )
            write_json(
                repo,
                f"data/briefing/finalization/{date}/{slot}/draft-rev-001.json",
                {"sealed_at_utc": "2026-08-30T22:10:00Z"},
            )
            api = FakeApi(target, [job("success")])
            result = RECOVERY.run_watchdog(slot, date, False, api, repo)
            self.assertIn("HOLD:", result)
            self.assertIn("SOURCE_REVIEW_REQUIRED", result)
            self.assertEqual(api.posts, [])

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
