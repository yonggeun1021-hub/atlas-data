#!/usr/bin/env python3
"""Persist one Atlas Daily Collect scheduler observation.

This is operational telemetry only.  It records which workflow slot reached a
runner, how late the runner first became observable, and what the Guard decided.
It does not read credentials, call data providers, or determine data readiness.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "data" / "operations" / "collect_runs"
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))

SCHEDULE_SLOTS = {
    "5 21 * * 0-4": ("primary_0605_kst", 21, 5),
    "25 21 * * 0-4": ("backup_0625_kst", 21, 25),
    "45 21 * * 0-4": ("final_0645_kst", 21, 45),
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RUN_FILE_RE = re.compile(r"^run-(\d+)-attempt-(\d+)\.json$")
INDEX_PATH_PREFIX = "data/operations/collect_runs"
INDEX_AUTHORITY = {
    "operational_telemetry_index_only": True,
    "data_readiness_authority": False,
    "collector_authority": False,
    "schedule_authority": False,
    "recovery_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class TelemetryError(RuntimeError):
    pass


def canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def valid_kst_date(value: str) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


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

    if target.exists():
        if target.read_text(encoding="utf-8") == payload:
            return target
        raise TelemetryError(f"telemetry record conflict: {target}")

    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()

    return target


def index_entry(
    record: dict,
    raw_sha256: str,
    filename: str,
    kst_date: str,
) -> dict:
    if not isinstance(record, dict) or set(record) != {
        "schema_version", "workflow", "github", "runner", "slot", "guard"
    }:
        raise TelemetryError(f"record fields invalid: {filename}")
    github = record.get("github")
    runner = record.get("runner")
    slot = record.get("slot")
    guard = record.get("guard")
    if (
        record.get("schema_version") != 1
        or record.get("workflow") != "Atlas Daily Collect"
        or not isinstance(github, dict)
        or set(github)
        != {"event_name", "event_schedule", "run_id", "run_attempt"}
        or type(github.get("run_id")) is not int
        or github["run_id"] < 1
        or type(github.get("run_attempt")) is not int
        or github["run_attempt"] < 1
        or not isinstance(github.get("event_name"), str)
        or not github["event_name"]
        or (
            github.get("event_schedule") is not None
            and not isinstance(github["event_schedule"], str)
        )
        or not isinstance(runner, dict)
        or set(runner) != {"observed_started_at_utc", "observed_started_at_kst"}
        or not isinstance(slot, dict)
        or set(slot)
        != {
            "id",
            "timing_status",
            "expected_start_utc",
            "expected_start_kst",
            "delay_seconds",
        }
        or not isinstance(guard, dict)
        or set(guard) != {"result", "skip"}
        or guard.get("result") not in {"fresh", "stale", "unknown"}
        or not (type(guard.get("skip")) is bool or guard.get("skip") is None)
    ):
        raise TelemetryError(f"record identity invalid: {filename}")
    expected_filename = (
        f"run-{github['run_id']}-attempt-{github['run_attempt']}.json"
    )
    if filename != expected_filename or RUN_FILE_RE.fullmatch(filename) is None:
        raise TelemetryError(f"record filename mismatch: {filename}")
    observed_utc = parse_utc(runner.get("observed_started_at_utc", ""))
    try:
        observed_kst = dt.datetime.fromisoformat(
            runner.get("observed_started_at_kst", "")
        )
    except (TypeError, ValueError) as exc:
        raise TelemetryError(f"record KST time invalid: {filename}") from exc
    if (
        observed_kst.utcoffset() != dt.timedelta(hours=9)
        or observed_kst.astimezone(UTC) != observed_utc
        or runner["observed_started_at_utc"] != iso_utc(observed_utc)
        or runner["observed_started_at_kst"]
        != observed_utc.astimezone(KST).isoformat(timespec="seconds")
    ):
        raise TelemetryError(f"record runner clocks mismatch: {filename}")
    expected_slot = slot_observation(
        github["event_name"],
        github["event_schedule"] or "",
        observed_utc,
    )
    if slot != expected_slot:
        raise TelemetryError(f"record slot derivation mismatch: {filename}")
    date_basis = (
        slot["expected_start_kst"]
        or runner["observed_started_at_kst"]
    )[:10]
    if date_basis != kst_date:
        raise TelemetryError(f"record KST date mismatch: {filename}")
    if (
        not isinstance(raw_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", raw_sha256) is None
    ):
        raise TelemetryError(f"record sha invalid: {filename}")
    return {
        "path": f"{INDEX_PATH_PREFIX}/{kst_date}/{filename}",
        "record_sha256": raw_sha256,
        "run_id": github["run_id"],
        "run_attempt": github["run_attempt"],
        "event_name": github["event_name"],
        "slot_id": slot["id"],
        "timing_status": slot["timing_status"],
        "observed_started_at_utc": runner["observed_started_at_utc"],
        "guard_result": guard["result"],
        "guard_skip": guard["skip"],
    }


def validate_index(value: dict) -> dict:
    fields = {
        "schema_version",
        "contract_version",
        "kst_date",
        "record_count",
        "records",
        "summary",
        "authority",
        "index_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise TelemetryError("index fields invalid")
    kst_date = value.get("kst_date")
    records = value.get("records")
    record_fields = {
        "path",
        "record_sha256",
        "run_id",
        "run_attempt",
        "event_name",
        "slot_id",
        "timing_status",
        "observed_started_at_utc",
        "guard_result",
        "guard_skip",
    }
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "collect_run_index/1"
        or not valid_kst_date(kst_date)
        or not isinstance(records, list)
        or not records
        or value.get("record_count") != len(records)
        or value.get("authority") != INDEX_AUTHORITY
    ):
        raise TelemetryError("index identity invalid")
    paths = []
    for row in records:
        if (
            not isinstance(row, dict)
            or set(row) != record_fields
            or not isinstance(row.get("path"), str)
            or not row["path"].startswith(f"{INDEX_PATH_PREFIX}/{kst_date}/run-")
            or not isinstance(row.get("record_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", row["record_sha256"]) is None
            or type(row.get("run_id")) is not int
            or row["run_id"] < 1
            or type(row.get("run_attempt")) is not int
            or row["run_attempt"] < 1
            or row["path"] != (
                f"{INDEX_PATH_PREFIX}/{kst_date}/"
                f"run-{row['run_id']}-attempt-{row['run_attempt']}.json"
            )
            or not isinstance(row.get("event_name"), str)
            or not row["event_name"]
            or not isinstance(row.get("slot_id"), str)
            or not row["slot_id"]
            or row.get("timing_status")
            not in {"measured", "not_applicable", "unknown_schedule"}
            or not isinstance(row.get("observed_started_at_utc"), str)
            or row["observed_started_at_utc"]
            != iso_utc(parse_utc(row["observed_started_at_utc"]))
            or row.get("guard_result") not in {"fresh", "stale", "unknown"}
            or not (
                type(row.get("guard_skip")) is bool
                or row.get("guard_skip") is None
            )
        ):
            raise TelemetryError("index record invalid")
        paths.append(row["path"])
    expected_order = sorted(
        records,
        key=lambda row: (
            row["observed_started_at_utc"],
            row["run_id"],
            row["run_attempt"],
        ),
    )
    if records != expected_order:
        raise TelemetryError("index order invalid")
    if len(paths) != len(set(paths)):
        raise TelemetryError("index record path duplicate")
    expected_summary = {
        "measured_count": sum(
            row["timing_status"] == "measured" for row in records
        ),
        "guard_skip_count": sum(row["guard_skip"] is True for row in records),
        "guard_stale_count": sum(row["guard_result"] == "stale" for row in records),
    }
    if value.get("summary") != expected_summary:
        raise TelemetryError("index summary invalid")
    digest = value.get("index_sha256")
    unsigned = dict(value)
    unsigned.pop("index_sha256")
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise TelemetryError("index sha invalid")
    return json.loads(json.dumps(value))


def build_index(date_dir: Path) -> dict:
    date_dir = Path(date_dir)
    kst_date = date_dir.name
    if not valid_kst_date(kst_date):
        raise TelemetryError("index KST date invalid")
    entries = []
    for path in sorted(date_dir.glob("run-*-attempt-*.json")):
        try:
            raw = path.read_bytes()
            record = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise TelemetryError(f"record unreadable: {path.name}") from exc
        entries.append(
            index_entry(
                record,
                hashlib.sha256(raw).hexdigest(),
                path.name,
                kst_date,
            )
        )
    if not entries:
        raise TelemetryError(f"no telemetry records: {kst_date}")
    entries.sort(
        key=lambda row: (
            row["observed_started_at_utc"],
            row["run_id"],
            row["run_attempt"],
        )
    )
    index = {
        "schema_version": 1,
        "contract_version": "collect_run_index/1",
        "kst_date": kst_date,
        "record_count": len(entries),
        "records": entries,
        "summary": {
            "measured_count": sum(
                row["timing_status"] == "measured" for row in entries
            ),
            "guard_skip_count": sum(row["guard_skip"] is True for row in entries),
            "guard_stale_count": sum(row["guard_result"] == "stale" for row in entries),
        },
        "authority": dict(INDEX_AUTHORITY),
    }
    index["index_sha256"] = payload_sha256(index)
    return validate_index(index)


def write_index(date_dir: Path) -> Path:
    date_dir = Path(date_dir)
    index = build_index(date_dir)
    target = date_dir / "index.json"
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(
                index,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
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
    parser.add_argument(
        "--rebuild-index-date",
        help="rebuild one existing KST-date index without recording a run",
    )
    args = parser.parse_args(argv)
    if args.rebuild_index_date is not None:
        if not valid_kst_date(args.rebuild_index_date):
            raise TelemetryError("rebuild index date must be YYYY-MM-DD")
        target = write_index(args.out_root / args.rebuild_index_date)
        print(f"P0-02 telemetry index path={target}")
        return 0
    record = build_record(dict(os.environ if environ is None else environ))
    target = write_record(record, args.out_root)
    index_target = write_index(target.parent)
    print(
        "P0-02 telemetry"
        f" slot={record['slot']['id']}"
        f" delay_seconds={record['slot']['delay_seconds']}"
        f" guard={record['guard']['result']}"
        f" skip={record['guard']['skip']}"
        f" path={target}"
        f" index={index_target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
