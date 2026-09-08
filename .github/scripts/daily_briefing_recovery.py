#!/usr/bin/env python3
"""Bounded same-day recovery for the two natural Atlas briefing runs.

The watchdog may only re-run the original GitHub ``schedule`` event.  It never
manufactures a workflow_dispatch receipt, changes the decision date, or opens
any action/order/trading authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
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


def _load_handoff_report(repo_root: Path, slot: str, decision_date: str) -> dict:
    """Read the canonical handoff state instead of re-deriving delivery health."""
    script = Path(__file__).with_name("briefing_handoff_watchdog.py")
    spec = importlib.util.spec_from_file_location("briefing_handoff_watchdog", script)
    if spec is None or spec.loader is None:
        raise RecoveryError("HANDOFF_WATCHDOG_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run_check(repo_root, slot, decision_date)
    if not isinstance(report, dict) or not isinstance(report.get("status"), str):
        raise RecoveryError("HANDOFF_WATCHDOG_REPORT_INVALID")
    return report


def _load_finalization_module():
    script = Path(__file__).with_name("briefing_finalization.py")
    spec = importlib.util.spec_from_file_location("briefing_finalization", script)
    if spec is None or spec.loader is None:
        raise RecoveryError("FINALIZATION_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_delivery_receipt(
    finalization,
    directory: Path,
    draft: dict,
    validation: dict,
    receipt: dict,
) -> None:
    payload = finalization._read_bytes(
        directory / f"payload-rev-{draft['rev']:03d}.md",
        "FINALIZATION_PAYLOAD_MISSING",
    )
    payload_sha = finalization._sha256(payload)
    expected = {
        "contract_version": finalization.CONTRACT_VERSION,
        "briefing_id": draft.get("briefing_id"),
        "slot": draft.get("slot"),
        "kst_date": draft.get("kst_date"),
        "sealed_payload_sha256": payload_sha,
        "delivery_marker": draft.get("delivery_marker"),
        "source_briefing_sha256": (draft.get("source") or {}).get("briefing_sha256"),
        "source_revision": (draft.get("source") or {}).get("revision"),
        "draft_rev": draft.get("rev"),
        "validation_rev": validation.get("rev"),
        "validation_status_at_delivery": validation.get("validation_status"),
        "immutable": True,
    }
    if draft.get("delivery_payload_sha256") != payload_sha:
        raise RecoveryError("FINAL_HANDOFF_SEALED_PAYLOAD_MISMATCH")
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise RecoveryError("FINAL_HANDOFF_DELIVERY_IDENTITY_MISMATCH")

    required = receipt.get("required_channels")
    channels = receipt.get("channels")
    proofs = receipt.get("delivery_proofs")
    if (
        not isinstance(required, list)
        or not required
        or not all(isinstance(channel, str) and channel for channel in required)
        or len(required) != len(set(required))
        or not isinstance(channels, list)
        or not all(isinstance(channel, str) and channel for channel in channels)
        or len(channels) != len(set(channels))
        or not set(required).issubset(channels)
        or not isinstance(proofs, list)
        or not all(isinstance(proof, dict) for proof in proofs)
    ):
        raise RecoveryError("FINAL_HANDOFF_DELIVERY_CHANNELS_INVALID")
    proof_channels = [proof.get("channel") for proof in proofs]
    if (
        len(proof_channels) != len(set(proof_channels))
        or set(proof_channels) != set(channels)
        or any(not isinstance(proof.get("covers_full_payload"), bool) for proof in proofs)
    ):
        raise RecoveryError("FINAL_HANDOFF_DELIVERY_PROOFS_INVALID")
    full_payload = sorted(
        proof["channel"] for proof in proofs if proof["covers_full_payload"]
    )
    if receipt.get("full_payload_channels") != full_payload:
        raise RecoveryError("FINAL_HANDOFF_FULL_PAYLOAD_CHANNELS_INVALID")
    if not isinstance(receipt.get("attempts"), int) or receipt["attempts"] < 1:
        raise RecoveryError("FINAL_HANDOFF_DELIVERY_ATTEMPTS_INVALID")
    try:
        delivered_at = _parse_utc(receipt["delivered_at_utc"])
        sealed_at = _parse_utc(draft["sealed_at_utc"])
    except (KeyError, TypeError):
        raise RecoveryError("FINAL_HANDOFF_DELIVERY_TIME_INVALID") from None
    if delivered_at < sealed_at:
        raise RecoveryError("FINAL_HANDOFF_DELIVERY_TIME_INVALID")


def _validate_complete_handoff(repo_root: Path, slot: str, decision_date: str) -> None:
    """Validate the existing finalization chain without invoking any writer."""
    finalization = _load_finalization_module()
    directory = finalization.slot_dir(repo_root, decision_date, slot)
    draft_path = finalization._latest(directory, "draft")
    if draft_path is None:
        raise RecoveryError("FINAL_HANDOFF_DRAFT_MISSING")
    try:
        draft = finalization._read_json(draft_path, "FINALIZATION_DRAFT_UNREADABLE")
        validation, problem = finalization.resolve_validation(directory)
        if problem is not None or validation is None:
            raise RecoveryError("FINAL_HANDOFF_GOVERNING_VALIDATION_MISSING")
        expected_identity = {
            "contract_version": finalization.CONTRACT_VERSION,
            "briefing_id": finalization.briefing_id(decision_date, slot),
            "slot": slot,
            "kst_date": decision_date,
        }
        if any(draft.get(key) != value for key, value in expected_identity.items()):
            raise RecoveryError("FINAL_HANDOFF_DRAFT_IDENTITY_MISMATCH")
        if (
            any(validation.get(key) != value for key, value in expected_identity.items())
            or validation.get("delivery_payload_sha256")
            != draft.get("delivery_payload_sha256")
        ):
            raise RecoveryError("FINAL_HANDOFF_VALIDATION_IDENTITY_MISMATCH")
        routing = validation.get("routing") or finalization.derive_routing(
            validation, finalization.load_ratified_specs(repo_root)
        )
        if routing.get("status_deliverable") is not True:
            raise RecoveryError("FINAL_HANDOFF_VALIDATION_NOT_DELIVERABLE")
        finalization.verify_pre_delivery_portal_receipt(
            repo_root,
            decision_date,
            slot,
            draft=draft,
            validation=validation,
        )
        receipt = finalization._read_json(
            finalization.receipt_path(repo_root, decision_date, slot),
            "FINALIZATION_RECEIPT_UNREADABLE",
        )
        _validate_delivery_receipt(
            finalization, directory, draft, validation, receipt
        )
    except finalization.FinalizationError as exc:
        raise RecoveryError(f"FINAL_HANDOFF_INVALID:{exc.code}") from None


def _classify_successful_producer(handoff_report: dict | None) -> str:
    if handoff_report is None:
        return "HANDOFF_STATUS_REQUIRED"
    if handoff_report.get("status") == "COMPLETE":
        return "HEALTHY"
    semantic = (handoff_report.get("checks") or {}).get("semantic_verdict") or {}
    if semantic.get("exists") and semantic.get("status_deliverable") is False:
        return "HANDOFF_HOLD"
    if handoff_report.get("alert") is True:
        return "HANDOFF_FAILED"
    return "HANDOFF_WAIT"


def classify_recovery(
    run: dict,
    jobs: list[dict],
    handoff_report: dict | None = None,
) -> str:
    attempt = int(run.get("run_attempt", 1))
    briefing = next((job for job in jobs if job.get("name") == "briefing"), None)
    if run.get("status") != "completed":
        return "WAIT_RUNNING"
    if briefing is not None and briefing.get("status") != "completed":
        return "WAIT_RUNNING"
    if briefing is not None and briefing.get("conclusion") == "success":
        return _classify_successful_producer(handoff_report)
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


def run_watchdog(
    slot: str,
    decision_date: str,
    final_check: bool,
    api: GitHubApi,
    repo_root: Path | None = None,
) -> str:
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
    briefing = next((job for job in jobs if job.get("name") == "briefing"), None)
    handoff_report = None
    if (
        target.get("status") == "completed"
        and briefing is not None
        and briefing.get("status") == "completed"
        and briefing.get("conclusion") == "success"
    ):
        handoff_report = _load_handoff_report(
            (repo_root or Path.cwd()).resolve(), slot, decision_date
        )
        if handoff_report.get("status") == "COMPLETE":
            _validate_complete_handoff(
                (repo_root or Path.cwd()).resolve(), slot, decision_date
            )
    action = classify_recovery(target, jobs, handoff_report)
    if action == "HEALTHY":
        return (
            f"PASS: {slot} briefing handoff is COMPLETE for {decision_date} "
            f"(producer run {run_id})"
        )
    if action in {"HANDOFF_WAIT", "HANDOFF_HOLD"}:
        status = handoff_report["status"]
        reason = handoff_report.get("reason", status)
        prefix = "HOLD" if action == "HANDOFF_HOLD" else "WAIT"
        return (
            f"{prefix}: {slot} producer run {run_id} succeeded but handoff is "
            f"{status} ({reason}); no producer rerun requested"
        )
    if action == "HANDOFF_FAILED":
        status = handoff_report["status"]
        reason = handoff_report.get("reason", status)
        raise RecoveryError(
            f"BRIEFING_HANDOFF_FAILED:run={run_id}:status={status}:reason={reason}"
        )
    if action == "HANDOFF_STATUS_REQUIRED":
        raise RecoveryError(f"BRIEFING_HANDOFF_STATUS_REQUIRED:run={run_id}")
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
