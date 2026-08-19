#!/usr/bin/env python3
"""Persist one Atlas Daily Collect scheduler observation.

This is operational telemetry only.  It records which workflow slot reached a
runner, how late the runner first became observable, and what the Guard decided.
It does not read credentials, call data providers, or determine data readiness.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "data" / "operations" / "collect_runs"
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))

SCHEDULE_SLOTS = {
    "5 21 * * 0-4": ("primary_0605_kst", 21, 5),
    "25 21 * * 0-4": ("backup_0625_kst", 21, 25),
    "45 21 * * 0-4": ("final_0645_kst", 21, 45),
}


class TelemetryError(RuntimeError):
    pass


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


def guard_observation(result: str, skip: str) -> dict:
    normalized_result = result.strip() if isinstance(result, str) else ""
    normalized_skip = skip.strip().lower() if isinstance(skip, str) else ""

    return {
        "result": (
            normalized_result
            if normalized_result in {"fresh", "stale"}
            else "unknown"
        ),
        "skip": (
            True
            if normalized_skip == "yes"
            else False
            if normalized_skip == "no"
            else None
        ),
    }


def build_record(environ: dict[str, str]) -> dict:
    observed = parse_utc(environ.get("ATLAS_RUNNER_STARTED_AT_UTC", ""))
    event_name = environ.get("ATLAS_EVENT_NAME", "").strip()
    if not event_name:
        raise TelemetryError("ATLAS_EVENT_NAME is required")

    event_schedule = environ.get("ATLAS_EVENT_SCHEDULE", "").strip()
    run_id = positive_int(environ.get("ATLAS_RUN_ID", ""), "ATLAS_RUN_ID")
    run_attempt = positive_int(
        environ.get("ATLAS_RUN_ATTEMPT", ""),
        "ATLAS_RUN_ATTEMPT",
    )
    slot = slot_observation(event_name, event_schedule, observed)
    observed_kst = observed.astimezone(KST)

    return {
        "schema_version": 1,
        "workflow": "Atlas Daily Collect",
        "github": {
            "event_name": event_name,
            "event_schedule": event_schedule or None,
            "run_id": run_id,
            "run_attempt": run_attempt,
        },
        "runner": {
            "observed_started_at_utc": iso_utc(observed),
            "observed_started_at_kst": observed_kst.isoformat(
                timespec="seconds"
            ),
        },
        "slot": slot,
        "guard": guard_observation(
            environ.get("ATLAS_GUARD_RESULT", ""),
            environ.get("ATLAS_GUARD_SKIP", ""),
        ),
    }


def record_path(record: dict, out_root: Path) -> Path:
    slot = record["slot"]
    basis = slot["expected_start_kst"] or record["runner"][
        "observed_started_at_kst"
    ]
    kst_date = basis[:10]
    github = record["github"]
    filename = (
        f"run-{github['run_id']}-attempt-{github['run_attempt']}.json"
    )
    return out_root / kst_date / filename


def write_record(record: dict, out_root: Path = OUT_ROOT) -> Path:
    target = record_path(record, out_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    payload = json.dumps(
        record,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"

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
        "P0-02 telemetry"
        f" slot={record['slot']['id']}"
        f" delay_seconds={record['slot']['delay_seconds']}"
        f" guard={record['guard']['result']}"
        f" skip={record['guard']['skip']}"
        f" path={target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
