#!/usr/bin/env python3
"""Read-only Stablecoin capture deadline observer.

The scheduler for this check must live outside ``stablecoin-capture.yml``.
Otherwise a dropped GitHub schedule could also drop its own alarm.  This helper
only inspects a cloned repository and prints a machine-readable observation; it
never triggers a workflow, sends an alert, or changes repository files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "stablecoin" / "raw"
TELEMETRY_ROOT = ROOT / "data" / "operations" / "stablecoin_capture_runs"
UTC = dt.timezone.utc
DEADLINE_UTC = dt.time(hour=8, minute=25, tzinfo=UTC)  # 17:25 KST

REQUIRED_FILES = {
    "_downloaded_at.txt",
    "_sha256.txt",
    "_manifest.json",
    "stablecoincharts_all.json.gz",
    "stablecoincharts_Terra.json.gz",
    "stablecoinchains.json.gz",
    "stablecoins_withprices.json.gz",
}


class ObservationError(RuntimeError):
    """Invalid observer input or malformed operations telemetry."""


def parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ObservationError("date must be YYYY-MM-DD") from exc


def parse_utc(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ObservationError("now must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ObservationError("now must include timezone")
    return parsed.astimezone(UTC)


def load_telemetry(root: Path, snapshot_date: dt.date) -> list[dict]:
    directory = root / snapshot_date.isoformat()
    if not directory.exists():
        return []

    records = []
    for path in sorted(directory.glob("run-*-attempt-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ObservationError(f"telemetry invalid: {path}: {exc}") from exc
        if payload.get("snapshot_date_utc") != snapshot_date.isoformat():
            raise ObservationError(f"telemetry date mismatch: {path}")
        records.append(payload)
    return records


def captured_lineage(records: list[dict]) -> dict | None:
    captured = [
        item for item in records if item.get("capture", {}).get("result") == "captured"
    ]
    if len(captured) != 1:
        return None
    item = captured[0]
    return {
        "slot_id": item.get("slot", {}).get("id"),
        "run_id": item.get("github", {}).get("run_id"),
        "run_attempt": item.get("github", {}).get("run_attempt"),
        "run_url": item.get("github", {}).get("run_url"),
        "delay_seconds": item.get("slot", {}).get("delay_seconds"),
    }


def failed_capture(records: list[dict]) -> dict | None:
    failed = [
        item
        for item in records
        if item.get("capture", {}).get("result") in {"failed", "cancelled"}
    ]
    if not failed:
        return None
    item = failed[-1]
    return {
        "slot_id": item.get("slot", {}).get("id"),
        "run_id": item.get("github", {}).get("run_id"),
        "run_attempt": item.get("github", {}).get("run_attempt"),
        "run_url": item.get("github", {}).get("run_url"),
        "result": item.get("capture", {}).get("result"),
    }


def observe(
    snapshot_date: dt.date,
    now: dt.datetime,
    raw_root: Path = RAW_ROOT,
    telemetry_root: Path = TELEMETRY_ROOT,
) -> dict:
    if now.tzinfo is None:
        raise ObservationError("now must include timezone")
    now = now.astimezone(UTC)
    deadline = dt.datetime.combine(snapshot_date, DEADLINE_UTC)
    directory = raw_root / snapshot_date.isoformat()
    present = (
        {
            name
            for name in REQUIRED_FILES
            if (directory / name).is_file()
        }
        if directory.is_dir()
        else set()
    )
    missing_files = sorted(REQUIRED_FILES - present)
    records = load_telemetry(telemetry_root, snapshot_date)
    lineage = captured_lineage(records)
    failure = failed_capture(records)
    deadline_passed = now >= deadline

    if not missing_files:
        status = "PRESENT"
        if lineage is None:
            classification = "present_unknown_lineage"
        elif lineage["slot_id"] == "primary_1520_kst":
            classification = "present_primary"
        elif lineage["slot_id"] in {"backup_1620_kst", "final_1720_kst"}:
            classification = "present_backup"
        elif lineage["slot_id"] == "manual":
            classification = "present_manual"
        else:
            classification = "present_unknown_lineage"
    elif not deadline_passed:
        status = "PENDING"
        classification = "pending_before_deadline"
    elif failure is not None:
        status = "FAILED"
        classification = "capture_failed_after_deadline"
    elif directory.is_dir():
        status = "INCOMPLETE"
        classification = "incomplete_after_deadline"
    else:
        status = "MISSING"
        classification = "snapshot_missing_after_deadline"

    return {
        "schema_version": 1,
        "observer": "stablecoin-capture-deadline/v1",
        "authority": "operations_observation_only",
        "decision_eligible": False,
        "snapshot_date_utc": snapshot_date.isoformat(),
        "observed_at_utc": now.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "deadline_utc": deadline.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "deadline_kst": deadline.astimezone(
            dt.timezone(dt.timedelta(hours=9))
        ).isoformat(timespec="seconds"),
        "status": status,
        "classification": classification,
        "alert_required": status in {"MISSING", "INCOMPLETE", "FAILED"},
        "manual_dispatch_authorized": False,
        "missing_files": missing_files,
        "telemetry_record_count": len(records),
        "captured_lineage": lineage,
        "failed_capture": failure,
    }


def run(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="UTC snapshot date; default is current UTC date")
    parser.add_argument("--now", help="observer time; default is current UTC time")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--telemetry-root", type=Path, default=TELEMETRY_ROOT)
    args = parser.parse_args(argv)

    now = parse_utc(args.now) if args.now else dt.datetime.now(UTC)
    snapshot_date = parse_date(args.date) if args.date else now.date()
    report = observe(snapshot_date, now, args.raw_root, args.telemetry_root)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "PRESENT":
        return 0
    if report["status"] == "PENDING":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(run())
