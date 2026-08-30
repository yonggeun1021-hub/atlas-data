#!/usr/bin/env python3
"""Persist one P4-07 Upbit microstructure capture workflow scheduler
observation.

The record is operations telemetry only. It allows a read-only clone to
distinguish schedule/manual execution and capture/population outcomes
without GitHub Actions API access. It never calls Upbit's private/order
endpoints and grants no decision authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "data" / "operations" / "upbit_microstructure_capture_runs"
UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SCHEDULE_SLOTS = {
    "20 1 * * *": ("daily_1020_kst", 1, 20),
}


class TelemetryError(RuntimeError):
    """P4-07 operations telemetry contract violation."""


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
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def positive_int(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TelemetryError(f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise TelemetryError(f"{field} must be a positive integer")
    return parsed


def slot_observation(event_name: str, event_schedule: str, observed: dt.datetime) -> dict:
    if event_name != "schedule":
        return {
            "id": "manual" if event_name == "workflow_dispatch" else "non_schedule",
            "timing_status": "not_applicable",
            "expected_start_utc": None, "expected_start_kst": None, "delay_seconds": None,
        }
    slot = SCHEDULE_SLOTS.get(event_schedule)
    if slot is None:
        return {
            "id": "unknown_schedule", "timing_status": "unknown_schedule",
            "expected_start_utc": None, "expected_start_kst": None, "delay_seconds": None,
        }
    slot_id, hour, minute = slot
    expected = observed.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if expected > observed:
        expected -= dt.timedelta(days=1)
    return {
        "id": slot_id, "timing_status": "measured",
        "expected_start_utc": iso_utc(expected),
        "expected_start_kst": expected.astimezone(KST).isoformat(timespec="seconds"),
        "delay_seconds": int((observed - expected).total_seconds()),
    }


def _blank_to_none(value: str) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def capture_observation(step_outcome: str, result: str) -> dict:
    outcome = step_outcome.strip().lower() if isinstance(step_outcome, str) else ""
    declared = result.strip().lower() if isinstance(result, str) else ""
    if outcome == "failure" and declared == "incomplete_existing":
        normalized, reason = "failed", "incomplete_snapshot_path_exists"
    elif outcome == "failure":
        normalized, reason = "failed", "capture_step_failed"
    elif outcome == "cancelled":
        normalized, reason = "cancelled", "capture_step_cancelled"
    elif outcome == "skipped":
        normalized, reason = "not_run", "capture_step_not_run"
    elif declared == "captured":
        normalized, reason = "captured", "new_snapshot_staged"
    elif declared == "skipped_existing":
        normalized, reason = "skipped_existing", "snapshot_already_exists"
    else:
        normalized, reason = "unknown", "capture_result_unavailable"
    return {
        "step_outcome": outcome or "unknown", "result": normalized, "reason": reason,
        "provider_calls_skipped": normalized == "skipped_existing",
        "raw_publication_eligible": normalized == "captured",
    }


def population_observation(step_outcome: str, result: str, reason: str, path: str, sha256: str) -> dict:
    outcome = step_outcome.strip().lower() if isinstance(step_outcome, str) else ""
    declared = result.strip().lower() if isinstance(result, str) else ""
    if outcome == "failure":
        normalized = "failed"
    elif outcome == "cancelled":
        normalized = "cancelled"
    elif outcome == "skipped":
        normalized = "not_run"
    elif declared in {"populated", "verified_existing", "failed"}:
        normalized = declared
    else:
        normalized = "unknown"
    return {
        "step_outcome": outcome or "unknown", "result": normalized,
        "reason": _blank_to_none(reason), "output_path": _blank_to_none(path),
        "payload_sha256": _blank_to_none(sha256),
    }


def build_record(environ: dict[str, str]) -> dict:
    observed = parse_utc(environ.get("ATLAS_RUNNER_STARTED_AT_UTC", ""))
    event_name = environ.get("ATLAS_EVENT_NAME", "").strip()
    if not event_name:
        raise TelemetryError("ATLAS_EVENT_NAME is required")
    event_schedule = environ.get("ATLAS_EVENT_SCHEDULE", "").strip()
    run_id = positive_int(environ.get("ATLAS_RUN_ID", ""), "ATLAS_RUN_ID")
    run_attempt = positive_int(environ.get("ATLAS_RUN_ATTEMPT", ""), "ATLAS_RUN_ATTEMPT")
    slot = slot_observation(event_name, event_schedule, observed)
    expected_start = slot["expected_start_utc"]
    snapshot_date = expected_start[:10] if expected_start else observed.date().isoformat()
    repository = environ.get("ATLAS_REPOSITORY", "").strip()
    server_url = environ.get("ATLAS_SERVER_URL", "https://github.com").rstrip("/")

    universe_sha = _blank_to_none(environ.get("ATLAS_UNIVERSE_RECORD_SHA256", ""))
    policy_sha = _blank_to_none(environ.get("ATLAS_P4_POLICY_SHA256", ""))
    if universe_sha is not None and SHA256_RE.fullmatch(universe_sha) is None:
        raise TelemetryError("ATLAS_UNIVERSE_RECORD_SHA256 must be lowercase SHA-256")
    if policy_sha is not None and SHA256_RE.fullmatch(policy_sha) is None:
        raise TelemetryError("ATLAS_P4_POLICY_SHA256 must be lowercase SHA-256")
    market_count_raw = _blank_to_none(environ.get("ATLAS_UNIVERSE_MARKET_COUNT", ""))
    market_count = positive_int(market_count_raw, "ATLAS_UNIVERSE_MARKET_COUNT") if market_count_raw else None

    return {
        "schema_version": 1,
        "workflow": "P4-07 Upbit Market Evidence & Microstructure Daily Capture",
        "authority": "operations_telemetry_only",
        "decision_eligible": False,
        "snapshot_date_utc": snapshot_date,
        "github": {
            "event_name": event_name, "event_schedule": event_schedule or None,
            "run_id": run_id, "run_attempt": run_attempt, "repository": repository or None,
            "run_url": f"{server_url}/{repository}/actions/runs/{run_id}" if repository else None,
        },
        "runner": {
            "observed_started_at_utc": iso_utc(observed),
            "observed_started_at_kst": observed.astimezone(KST).isoformat(timespec="seconds"),
        },
        "slot": slot,
        "capture": capture_observation(
            environ.get("ATLAS_CAPTURE_STEP_OUTCOME", ""), environ.get("ATLAS_CAPTURE_RESULT", ""),
        ),
        "p4_07_population": population_observation(
            environ.get("ATLAS_P4_07_STEP_OUTCOME", ""), environ.get("ATLAS_P4_07_RESULT", ""),
            environ.get("ATLAS_P4_07_REASON", ""), environ.get("ATLAS_P4_07_PATH", ""),
            environ.get("ATLAS_P4_07_SHA256", ""),
        ),
        "consumer_lineage": {
            "snapshot_key": _blank_to_none(environ.get("ATLAS_SNAPSHOT_KEY", "")),
            "universe_record_sha256": universe_sha,
            "universe_market_count": market_count,
            "p4_policy_id": _blank_to_none(environ.get("ATLAS_P4_POLICY_ID", "")),
            "p4_policy_sha256": policy_sha,
            "exact_hash_verified_before_provider_calls": universe_sha is not None and policy_sha is not None,
        },
        "authority_flags": {
            "policy_ratification_authorized": False,
            "evidence_derivation_only": True,
            "decision_eligible": False,
            "action_generation_authorized": False,
            "production_wiring_authorized": False,
            "trading_action_authorized": False,
            "order_action_authorized": False,
        },
    }


def record_path(record: dict, out_root: Path) -> Path:
    github = record["github"]
    filename = f"run-{github['run_id']}-attempt-{github['run_attempt']}.json"
    return out_root / record["snapshot_date_utc"] / filename


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
        "--out-root", type=Path, default=OUT_ROOT,
        help="telemetry output root; production default is tracked data/",
    )
    args = parser.parse_args(argv)
    record = build_record(dict(os.environ if environ is None else environ))
    target = write_record(record, args.out_root)
    print(
        "Upbit microstructure scheduler telemetry"
        f" event={record['github']['event_name']}"
        f" slot={record['slot']['id']}"
        f" delay_seconds={record['slot']['delay_seconds']}"
        f" capture={record['capture']['result']}"
        f" p4_07={record['p4_07_population']['result']}"
        f" path={target}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
