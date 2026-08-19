#!/usr/bin/env python3
"""Persist one Stablecoin capture scheduler observation.

This is operations telemetry only.  It records which scheduled slot reached a
runner, how late it arrived, and whether the capture published, skipped an
existing snapshot, or failed.  It never calls DefiLlama and has no investment
or data-readiness authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "data" / "operations" / "stablecoin_capture_runs"
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))

SCHEDULE_SLOTS = {
    "20 6 * * *": ("primary_1520_kst", 6, 20),
    "20 7 * * *": ("backup_1620_kst", 7, 20),
    "20 8 * * *": ("final_1720_kst", 8, 20),
}


class TelemetryError(RuntimeError):
    """Stablecoin operations telemetry contract violation."""


def parse_utc(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise TelemetryError("observed_started_at_utc is required")
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise TelemetryError("observed_started_at_utc must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise TelemetryError("observed_started_at_utc must include timezone")
    return parsed.astimezone(UTC)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def positive_int(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TelemetryError(f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise TelemetryError(f"{field} must be a positive integer")
    return parsed


def slot_observation(
    event_name: str,
    event_schedule: str,
    observed: dt.datetime,
) -> dict:
    if event_name != "schedule":
        return {
            "id": "manual" if event_name == "workflow_dispatch" else "non_schedule",
            "timing_status": "not_applicable",
            "expected_start_utc": None,
            "expected_start_kst": None,
            "delay_seconds": None,
        }

    slot = SCHEDULE_SLOTS.get(event_schedule)
    if slot is None:
        return {
            "id": "unknown_schedule",
            "timing_status": "unknown_schedule",
            "expected_start_utc": None,
            "expected_start_kst": None,
            "delay_seconds": None,
        }

    slot_id, hour, minute = slot
    expected = observed.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if expected > observed:
        expected -= dt.timedelta(days=1)

    return {
        "id": slot_id,
        "timing_status": "measured",
        "expected_start_utc": iso_utc(expected),
        "expected_start_kst": expected.astimezone(KST).isoformat(
            timespec="seconds"
        ),
        "delay_seconds": int((observed - expected).total_seconds()),
    }


def capture_observation(step_outcome: str, result: str) -> dict:
    outcome = step_outcome.strip().lower() if isinstance(step_outcome, str) else ""
    declared = result.strip().lower() if isinstance(result, str) else ""

    if outcome == "failure" and declared == "incomplete_existing":
        normalized = "failed"
        reason = "incomplete_snapshot_path_exists"
    elif outcome == "failure":
        normalized = "failed"
        reason = "capture_step_failed"
    elif outcome == "cancelled":
        normalized = "cancelled"
        reason = "capture_step_cancelled"
    elif declared == "captured":
        normalized = "captured"
        reason = "new_snapshot_published"
    elif declared == "skipped_existing":
        normalized = "skipped_existing"
        reason = "snapshot_already_exists"
    else:
        normalized = "unknown"
        reason = "capture_result_unavailable"

    return {
        "step_outcome": outcome or "unknown",
        "result": normalized,
        "reason": reason,
        "provider_call_skipped": normalized == "skipped_existing",
    }


def build_record(environ: dict[str, str]) -> dict:
    observed = parse_utc(environ.get("ATLAS_RUNNER_STARTED_AT_UTC", ""))
    event_name = environ.get("ATLAS_EVENT_NAME", "").strip()
    if not event_name:
        raise TelemetryError("ATLAS_EVENT_NAME is required")

    event_schedule = environ.get("ATLAS_EVENT_SCHEDULE", "").strip()
    run_id = positive_int(environ.get("ATLAS_RUN_ID", ""), "ATLAS_RUN_ID")
    run_attempt = positive_int(
        environ.get("ATLAS_RUN_ATTEMPT", ""), "ATLAS_RUN_ATTEMPT"
    )
    slot = slot_observation(event_name, event_schedule, observed)
    observed_kst = observed.astimezone(KST)
    expected_start = slot["expected_start_utc"]
    snapshot_date = (
        expected_start[:10] if expected_start else observed.date().isoformat()
    )
    repository = environ.get("ATLAS_REPOSITORY", "").strip()
    server_url = environ.get("ATLAS_SERVER_URL", "https://github.com").rstrip("/")

    return {
        "schema_version": 1,
        "workflow": "stablecoin-daily-capture",
        "authority": "operations_telemetry_only",
        "decision_eligible": False,
        "snapshot_date_utc": snapshot_date,
        "github": {
            "event_name": event_name,
            "event_schedule": event_schedule or None,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "repository": repository or None,
            "run_url": (
                f"{server_url}/{repository}/actions/runs/{run_id}"
                if repository
                else None
            ),
        },
        "runner": {
            "observed_started_at_utc": iso_utc(observed),
            "observed_started_at_kst": observed_kst.isoformat(timespec="seconds"),
        },
        "slot": slot,
        "capture": capture_observation(
            environ.get("ATLAS_CAPTURE_STEP_OUTCOME", ""),
            environ.get("ATLAS_CAPTURE_RESULT", ""),
        ),
    }


def record_path(record: dict, out_root: Path) -> Path:
    github = record["github"]
    filename = f"run-{github['run_id']}-attempt-{github['run_attempt']}.json"
    return out_root / record["snapshot_date_utc"] / filename


def write_record(record: dict, out_root: Path = OUT_ROOT) -> Path:
    target = record_path(record, out_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def run(argv=None, environ=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-root",
        type=Path,
        default=OUT_ROOT,
        help="telemetry output root; production default is tracked data/",
    )
    args = parser.parse_args(argv)
    record = build_record(dict(os.environ if environ is None else environ))
    target = write_record(record, args.out_root)
    print(
        "Stablecoin scheduler telemetry"
        f" slot={record['slot']['id']}"
        f" delay_seconds={record['slot']['delay_seconds']}"
        f" capture={record['capture']['result']}"
        f" path={target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
