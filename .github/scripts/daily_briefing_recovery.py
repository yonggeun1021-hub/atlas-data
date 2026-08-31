#!/usr/bin/env python3
"""Bounded same-day recovery for the two natural Atlas briefing runs.

The watchdog may only re-run the original GitHub ``schedule`` event.  It never
manufactures a workflow_dispatch receipt, changes the decision date, or opens
any action/order/trading authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
MAX_RUN_ATTEMPTS = 3
RETRYABLE_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "stale",
    "startup_failure",
    "timed_out",
}


class RecoveryError(RuntimeError):
    pass


def _parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RecoveryError("RUN_CREATED_AT_TIMEZONE_MISSING")
    return parsed.astimezone(dt.timezone.utc)


def _slot_for_created_at(value: str) -> str | None:
    local = _parse_utc(value).astimezone(KST)
    if 5 <= local.hour < 14:
        return "morning"
    if 16 <= local.hour <= 23:
        return "evening"
    return None


def select_target_run(runs: list[dict], decision_date: str, slot: str) -> dict | None:
    expected_date = dt.date.fromisoformat(decision_date)
    candidates = []
    for run in runs:
        created_at = run.get("created_at")
        if run.get("event") != "schedule" or not isinstance(created_at, str):
            continue
        local = _parse_utc(created_at).astimezone(KST)
        if local.date() == expected_date and _slot_for_created_at(created_at) == slot:
            candidates.append(run)
    if not candidates:
        return None
    return max(candidates, key=lambda row: (_parse_utc(row["created_at"]), int(row.get("run_attempt", 0))))


def classify_recovery(run: dict, jobs: list[dict]) -> str:
    attempt = int(run.get("run_attempt", 1))
    briefing = next((job for job in jobs if job.get("name") == "briefing"), None)
    if run.get("status") != "completed":
        return "WAIT_RUNNING"
    if briefing is not None and briefing.get("status") != "completed":
        return "WAIT_RUNNING"
    if briefing is not None and briefing.get("conclusion") == "success":
        return "HEALTHY"
    if attempt >= MAX_RUN_ATTEMPTS:
        return "ATTEMPTS_EXHAUSTED"
    if briefing is not None and briefing.get("conclusion") in RETRYABLE_CONCLUSIONS:
        return "RERUN_FAILED_JOBS"
    if briefing is None and run.get("conclusion") in RETRYABLE_CONCLUSIONS:
        return "RERUN_ALL"
    return "NON_RETRYABLE"


class GitHubApi:
    def __init__(self, repository: str, token: str):
        if not repository or "/" not in repository:
            raise RecoveryError("GITHUB_REPOSITORY_INVALID")
        if not token:
            raise RecoveryError("GITHUB_TOKEN_MISSING")
        self.base = f"https://api.github.com/repos/{repository}"
        self.headers = {
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {token}",
            "user-agent": "atlas-daily-briefing-recovery/1.0",
            "x-github-api-version": "2022-11-28",
        }

    def request(self, method: str, path: str) -> dict:
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=b"{}" if method == "POST" else None,
            headers=self.headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise RecoveryError(f"GITHUB_API_{method}_{exc.code}") from exc
        except OSError as exc:
            raise RecoveryError(f"GITHUB_API_{method}_UNAVAILABLE") from exc
        if not body:
            return {}
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RecoveryError("GITHUB_API_INVALID_JSON") from exc
        if not isinstance(value, dict):
            raise RecoveryError("GITHUB_API_RESPONSE_INVALID")
        return value


def run_watchdog(slot: str, decision_date: str, final_check: bool, api: GitHubApi) -> str:
    payload = api.request(
        "GET", "/actions/workflows/daily-briefing.yml/runs?event=schedule&per_page=50"
    )
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise RecoveryError("WORKFLOW_RUNS_INVALID")
    target = select_target_run(runs, decision_date, slot)
    if target is None:
        if final_check:
            raise RecoveryError(f"SCHEDULED_{slot.upper()}_RUN_MISSING_FINAL_CHECK")
        return f"WAIT: scheduled {slot} run for {decision_date} has not appeared yet"

    run_id = target.get("id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise RecoveryError("WORKFLOW_RUN_ID_INVALID")
    jobs_payload = api.request("GET", f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list):
        raise RecoveryError("WORKFLOW_JOBS_INVALID")
    action = classify_recovery(target, jobs)
    if action == "HEALTHY":
        return f"PASS: {slot} briefing job succeeded for {decision_date} (run {run_id})"
    if action == "WAIT_RUNNING":
        return f"WAIT: {slot} briefing run {run_id} is still running"
    if action == "RERUN_FAILED_JOBS":
        api.request("POST", f"/actions/runs/{run_id}/rerun-failed-jobs")
        return f"RECOVERY_REQUESTED: failed jobs in {slot} run {run_id}"
    if action == "RERUN_ALL":
        api.request("POST", f"/actions/runs/{run_id}/rerun")
        return f"RECOVERY_REQUESTED: complete {slot} run {run_id}"
    raise RecoveryError(f"BRIEFING_RECOVERY_{action}:run={run_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", required=True, choices=("morning", "evening"))
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--final-check", action="store_true")
    args = parser.parse_args()
    try:
        dt.date.fromisoformat(args.decision_date)
        api = GitHubApi(
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_TOKEN", ""),
        )
        print(run_watchdog(args.slot, args.decision_date, args.final_check, api))
        return 0
    except (RecoveryError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
