#!/usr/bin/env python3
"""Persist one KRX post-close scheduler observation.

This operations-only record lets a read-only clone distinguish scheduled,
manual, skipped, and failed runs without using the GitHub Actions API.  It does
not call KRX, confirm same-day market data, or grant briefing/decision authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "data" / "operations" / "krx_post_close_runs"
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))

SCHEDULE_SLOTS = {
    "5 7 * * 1-5": ("primary_1605_kst", 7, 5),
    "25 7 * * 1-5": ("backup_1625_kst", 7, 25),
    "45 7 * * 1-5": ("final_1645_kst", 7, 45),
}


class TelemetryError(RuntimeError):
    """KRX post-close operations telemetry contract violation."""


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


def guard_observation(step_outcome: str, result: str, skip: str) -> dict:
    outcome = step_outcome.strip().lower() if isinstance(step_outcome, str) else ""
    declared = result.strip().lower() if isinstance(result, str) else ""
    skip_value = skip.strip().lower() if isinstance(skip, str) else ""
    return {
        "step_outcome": outcome or "unknown",
        "result": declared or "unknown",
        "skip": skip_value == "yes",
        "trusted": outcome == "success" and declared in {"fresh", "stale"},
    }


def collection_observation(
    step_outcome: str,
    guard: dict,
) -> dict:
    outcome = step_outcome.strip().lower() if isinstance(step_outcome, str) else ""
    if guard["step_outcome"] != "success":
        normalized, reason = "not_run", "guard_failed_or_cancelled"
    elif guard["skip"] and guard["result"] == "fresh":
        normalized, reason = "skipped_existing", "bundle_already_exists"
    elif outcome == "success":
        normalized, reason = "captured", "new_observation_staged"
    elif outcome == "failure":
        normalized, reason = "failed", "collector_step_failed"
    elif outcome == "cancelled":
        normalized, reason = "cancelled", "collector_step_cancelled"
    elif outcome == "skipped":
        normalized, reason = "not_run", "collector_step_not_run"
    else:
        normalized, reason = "unknown", "collector_result_unavailable"
    return {
        "step_outcome": outcome or "unknown",
        "result": normalized,
        "reason": reason,
        "provider_calls_skipped": normalized in {"skipped_existing", "not_run"},
        "observation_publication_eligible": normalized == "captured",
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
    expected_start = slot["expected_start_utc"]
    observation_date = (
        parse_utc(expected_start).astimezone(KST).date().isoformat()
        if expected_start
        else observed.astimezone(KST).date().isoformat()
    )
    repository = environ.get("ATLAS_REPOSITORY", "").strip()
    server_url = environ.get("ATLAS_SERVER_URL", "https://github.com").rstrip("/")
    guard = guard_observation(
        environ.get("ATLAS_GUARD_STEP_OUTCOME", ""),
        environ.get("ATLAS_GUARD_RESULT", ""),
        environ.get("ATLAS_GUARD_SKIP", ""),
    )

    return {
        "schema_version": 1,
        "workflow": "Atlas KRX Post-Close Observation",
        "authority": "operations_telemetry_only",
        "decision_eligible": False,
        "observation_date_kst": observation_date,
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
            "observed_started_at_kst": observed.astimezone(KST).isoformat(
                timespec="seconds"
            ),
        },
        "slot": slot,
        "guard": guard,
        "collection": collection_observation(
            environ.get("ATLAS_POST_CLOSE_STEP_OUTCOME", ""), guard
        ),
        "authority_flags": {
            "same_day_confirmation_authorized": False,
            "briefing_decision_input_authorized": False,
            "regime_score_authorized": False,
            "production_wiring_authorized": False,
            "trading_action_authorized": False,
        },
    }


def record_path(record: dict, out_root: Path) -> Path:
    github = record["github"]
    filename = f"run-{github['run_id']}-attempt-{github['run_attempt']}.json"
    return out_root / record["observation_date_kst"] / filename


def write_record(record: dict, out_root: Path = OUT_ROOT) -> Path:
    target = record_path(record, out_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
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
        "KRX post-close scheduler telemetry"
        f" event={record['github']['event_name']}"
        f" slot={record['slot']['id']}"
        f" delay_seconds={record['slot']['delay_seconds']}"
        f" guard={record['guard']['result']}"
        f" collection={record['collection']['result']}"
        f" path={target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
