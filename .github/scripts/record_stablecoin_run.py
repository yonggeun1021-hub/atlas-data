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
    "50 5 * * *": ("primary_1450_kst", 5, 50),
    "20 6 * * *": ("backup_1520_kst", 6, 20),
    "20 7 * * *": ("backup_1620_kst", 7, 20),
    "20 8 * * *": ("final_1720_kst", 8, 20),
}

# The schedule-only 08:20Z slot is the one ratified exemption from the
# pending_current_observation guard.  A dispatched run never gets it.
FINAL_SLOT_CRON = "20 8 * * *"

# Refusals the capture step raises *before* it can publish anything.  They are
# recorded verbatim so a dispatched run that the guard turned away is legible in
# the telemetry rather than collapsing into a generic step failure.
# value = (result, reason, provider_call_skipped)
TRIGGER_REFUSALS = {
    "dispatch_guard_mode_invalid": (
        "refused_before_capture",
        "dispatch_guard_mode_not_schedule_equivalent",
        True,
    ),
    "trigger_input_conflict": (
        "refused_before_capture",
        "dispatch_input_present_on_schedule_event",
        True,
    ),
    "trigger_not_authorized": (
        "refused_before_capture",
        "trigger_not_authorized",
        True,
    ),
    # This one fires after the fetch: the guard could not be decided, so the
    # snapshot is discarded instead of published.
    "dispatch_guard_undetermined": (
        "refused_before_capture",
        "current_utc_observation_row_undetermined",
        False,
    ),
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


def trigger_observation(
    event_name: str,
    event_schedule: str,
    guard_mode: str,
) -> dict:
    """Record which trigger ran and whether the guard applied to it.

    ``available_at`` is never part of this: it stays the provider fetch time in
    the snapshot's own ``_downloaded_at.txt``, and no trigger can supply it.
    """
    exempt = event_name == "schedule" and event_schedule == FINAL_SLOT_CRON
    # Only a dispatch has a guard mode.  A default that leaks onto another
    # trigger is not a dispatch instruction and is not recorded as one.
    declared = guard_mode.strip() if isinstance(guard_mode, str) else ""
    return {
        "kind": event_name,
        "dispatch_guard_mode": (
            declared or None if event_name == "workflow_dispatch" else None
        ),
        "pending_current_observation_guard": (
            "EXEMPT_SCHEDULE_FINAL_SLOT_20_8" if exempt else "ENFORCED"
        ),
        "final_slot_exemption_applied": exempt,
        "available_at_supplied_by_trigger": False,
    }


def capture_observation(step_outcome: str, result: str) -> dict:
    outcome = step_outcome.strip().lower() if isinstance(step_outcome, str) else ""
    declared = result.strip().lower() if isinstance(result, str) else ""

    if declared in TRIGGER_REFUSALS:
        normalized, reason, provider_skipped = TRIGGER_REFUSALS[declared]
        return {
            "step_outcome": outcome or "unknown",
            "result": normalized,
            "reason": reason,
            "provider_call_skipped": provider_skipped,
        }

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
    elif declared == "pending_current_observation":
        normalized = "pending_current_observation"
        reason = "current_utc_observation_row_absent"
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
        "trigger": trigger_observation(
            event_name,
            event_schedule,
            environ.get("ATLAS_DISPATCH_GUARD_MODE", ""),
        ),
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
        f" trigger={record['trigger']['kind']}"
        f" guard={record['trigger']['pending_current_observation_guard']}"
        f" slot={record['slot']['id']}"
        f" delay_seconds={record['slot']['delay_seconds']}"
        f" capture={record['capture']['result']}"
        f" path={target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
