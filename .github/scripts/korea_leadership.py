#!/usr/bin/env python3
"""P1-KR-07 transient KRX index relative-leadership transform."""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
from pathlib import Path
import sys
from typing import Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "korea_leadership_contract.json"
POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"
KST = ZoneInfo("Asia/Seoul")
ROLES = {"KOSPI_BENCHMARK", "KOSDAQ_BENCHMARK", "SECTOR", "THEME"}
RUN_MODES = {"FORWARD_SHADOW", "HISTORICAL_REPLAY"}


class KoreaLeadershipError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise KoreaLeadershipError(f"{code}: {detail}" if detail else code)


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "root must be object")
    return value


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = read_json(path, "CONTRACT_INVALID")
    if value.get("contract_version") != "korea_leadership_contract/v1" or value.get("input_retention_policy") != "transient_memory_or_stdin_only" or value.get("output_retention_policy") != "non_reconstructive_derived_observations_only" or set(value) != {
        "schema_version", "contract_version", "transform_version", "measurement",
        "market_timezone", "return_semantics", "relative_strength_semantics",
        "session_coverage_policy", "membership_policy", "taxonomy_policy",
        "input_retention_policy", "output_retention_policy",
        "output_decimal_places", "rounding",
    }:
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return value


POLICY_KEYS = {
    "schema_version", "policy_version", "approval_status", "effective_from",
    "source_name", "market", "market_timezone", "allowed_run_modes",
    "session_calendar_source", "publication_timing_source",
    "earliest_usable_time", "lookback_sessions", "records",
}
RECORD_KEYS = {"series_identity", "role", "benchmark_identity", "effective_from", "effective_to", "reason"}


def load_policy(path: Path = POLICY_PATH) -> dict:
    value = read_json(path, "LEADERSHIP_POLICY_INVALID")
    if set(value) != POLICY_KEYS or value.get("schema_version") != 1:
        fail("LEADERSHIP_POLICY_INVALID", "schema")
    if value.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}:
        fail("LEADERSHIP_POLICY_INVALID", "approval_status")
    if value.get("market") != "KOREA" or value.get("market_timezone") != "Asia/Seoul":
        fail("LEADERSHIP_POLICY_INVALID", "market identity")
    return value


def parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(code)


def parse_timestamp(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str):
        fail("TIMESTAMP_INVALID", label)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        fail("TIMESTAMP_INVALID", label)
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(hours=9):
        fail("TIMESTAMP_INVALID", label)
    return parsed


def active_record(records: list, identity: str, day: dt.date) -> Optional[dict]:
    matches = []
    for record in records:
        if record["series_identity"] != identity:
            continue
        start = parse_date(record["effective_from"], "LEADERSHIP_POLICY_INVALID")
        end = parse_date(record["effective_to"], "LEADERSHIP_POLICY_INVALID") if record["effective_to"] is not None else None
        if start <= day and (end is None or day <= end):
            matches.append(record)
    if len(matches) > 1:
        fail("TAXONOMY_OVERLAP", identity)
    return matches[0] if matches else None


def require_ratified(policy: dict) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("LEADERSHIP_POLICY_UNRATIFIED", policy["policy_version"])
    for key in POLICY_KEYS - {"schema_version", "approval_status", "records"}:
        if policy.get(key) in (None, "", []):
            fail("LEADERSHIP_POLICY_INVALID", f"missing {key}")
    if type(policy["lookback_sessions"]) is not int or policy["lookback_sessions"] < 1:
        fail("LEADERSHIP_POLICY_INVALID", "lookback_sessions")
    if policy["allowed_run_modes"] != sorted(set(policy["allowed_run_modes"])) or not set(policy["allowed_run_modes"]).issubset(RUN_MODES):
        fail("LEADERSHIP_POLICY_INVALID", "allowed_run_modes")
    if not isinstance(policy["records"], list) or not policy["records"]:
        fail("LEADERSHIP_POLICY_INVALID", "records")
    parse_date(policy["effective_from"], "LEADERSHIP_POLICY_INVALID")
    try:
        dt.time.fromisoformat(policy["earliest_usable_time"])
    except (TypeError, ValueError):
        fail("LEADERSHIP_POLICY_INVALID", "earliest_usable_time")
    for record in policy["records"]:
        if not isinstance(record, dict) or set(record) != RECORD_KEYS or record["role"] not in ROLES:
            fail("LEADERSHIP_POLICY_INVALID", "record schema")
        if not all(isinstance(record[key], str) and record[key] for key in ("series_identity", "benchmark_identity", "effective_from", "reason")):
            fail("LEADERSHIP_POLICY_INVALID", "record values")
        parse_date(record["effective_from"], "LEADERSHIP_POLICY_INVALID")
        if record["effective_to"] is not None:
            parse_date(record["effective_to"], "LEADERSHIP_POLICY_INVALID")
        if record["role"].endswith("BENCHMARK") and record["benchmark_identity"] != record["series_identity"]:
            fail("LEADERSHIP_POLICY_INVALID", "benchmark self identity")


def decimal_string(value: object, identity: str) -> Decimal:
    if not isinstance(value, str):
        fail("NUMBER_MUST_BE_DECIMAL_STRING", identity)
    try:
        number = Decimal(value)
    except InvalidOperation:
        fail("NUMBER_INVALID", identity)
    if not number.is_finite() or number <= 0:
        fail("NUMBER_INVALID", identity)
    return number


def render_decimal(value: Decimal, places: int) -> str:
    text = format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def build_transform(payload: dict, policy_path: Path = POLICY_PATH) -> dict:
    contract = load_contract()
    policy = load_policy(policy_path)
    require_ratified(policy)
    expected = {"schema_version", "source_name", "market", "market_timezone", "run_mode", "observation_date", "fetched_at", "available_at", "decision_at", "expected_session_dates", "series_rows"}
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("schema_version") != 1:
        fail("INPUT_INVALID", "schema")
    for key in ("source_name", "market", "market_timezone"):
        if payload[key] != policy[key]:
            fail("INPUT_IDENTITY_MISMATCH", key)
    if payload["run_mode"] not in policy["allowed_run_modes"]:
        fail("RUN_MODE_NOT_ALLOWED")
    dates = payload["expected_session_dates"]
    needed = policy["lookback_sessions"] + 1
    if not isinstance(dates, list) or len(dates) != needed or dates != sorted(set(dates)):
        fail("SESSION_CALENDAR_INVALID")
    parsed = [parse_date(value, "SESSION_CALENDAR_INVALID") for value in dates]
    observation = parse_date(payload["observation_date"], "SESSION_CALENDAR_INVALID")
    if observation != parsed[-1] or parsed[0] < parse_date(policy["effective_from"], "LEADERSHIP_POLICY_INVALID"):
        fail("SESSION_CALENDAR_INVALID", "observation date")

    available = parse_timestamp(payload["available_at"], "available_at")
    fetched = parse_timestamp(payload["fetched_at"], "fetched_at")
    earliest = dt.datetime.combine(observation, dt.time.fromisoformat(policy["earliest_usable_time"]), KST)
    if available < earliest or fetched < available:
        fail("TEMPORAL_INPUT_NOT_QUALIFIED")
    if payload["run_mode"] == "FORWARD_SHADOW":
        decision = parse_timestamp(payload["decision_at"], "decision_at")
        if decision < fetched:
            fail("TEMPORAL_INPUT_NOT_QUALIFIED", "DECISION_PRECEDES_FETCH")
        eligibility, status = "FORWARD_PIT_QUALIFIED", "OBSERVED_UNCLASSIFIED"
    else:
        if payload["decision_at"] is not None:
            fail("TEMPORAL_INPUT_NOT_QUALIFIED", "REPLAY_DECISION_MUST_BE_NULL")
        eligibility, status = "CAUSAL_REPLAY_ONLY", "CAUSAL_REPLAY_ONLY"

    series_rows = payload["series_rows"]
    if not isinstance(series_rows, list) or not series_rows:
        fail("INPUT_INVALID", "series_rows")
    identities = [item.get("series_identity") for item in series_rows if isinstance(item, dict)]
    if identities != sorted(set(identities)):
        fail("SERIES_IDENTITY_COLLISION_OR_ORDER")
    closes_by_identity = {}
    records = {}
    for item in series_rows:
        if set(item) != {"series_identity", "rows"}:
            fail("INPUT_INVALID", "series schema")
        identity = item["series_identity"]
        record = active_record(policy["records"], identity, observation)
        if record is None:
            fail("SERIES_NOT_IN_PIT_TAXONOMY", identity)
        rows = item["rows"]
        if not isinstance(rows, list) or [row.get("session_date") for row in rows if isinstance(row, dict)] != dates:
            fail("SESSION_COVERAGE_MISMATCH", identity)
        if any(set(row) != {"session_date", "close"} for row in rows):
            fail("INPUT_INVALID", "row schema")
        closes_by_identity[identity] = [decimal_string(row["close"], identity) for row in rows]
        records[identity] = record
    required = {record["series_identity"] for record in policy["records"] if active_record(policy["records"], record["series_identity"], observation) is record}
    if set(closes_by_identity) != required:
        fail("PIT_TAXONOMY_COVERAGE_MISMATCH")
    for record in records.values():
        if record["benchmark_identity"] not in closes_by_identity:
            fail("BENCHMARK_MISSING", record["series_identity"])

    observations = []
    places = contract["output_decimal_places"]
    for identity in sorted(closes_by_identity):
        closes = closes_by_identity[identity]
        benchmark = closes_by_identity[records[identity]["benchmark_identity"]]
        gross = closes[-1] / closes[0]
        benchmark_gross = benchmark[-1] / benchmark[0]
        observations.append({
            "series_identity": identity,
            "role": records[identity]["role"],
            "benchmark_identity": records[identity]["benchmark_identity"],
            "cumulative_gross_return": render_decimal(gross, places),
            "relative_strength_vs_benchmark": render_decimal(gross / benchmark_gross - 1, places),
        })
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "transform_version": contract["transform_version"],
        "market": "KOREA",
        "observation_date": payload["observation_date"],
        "available_at": payload["available_at"],
        "status": status,
        "temporal_eligibility": {"eligibility": eligibility, "publication_timing_source": policy["publication_timing_source"], "authoritative_historical_pit": False},
        "relative_strength_observations": observations,
        "leader_classification_authorized": False,
        "ranking_authorized": False,
        "trend_direction_authorized": False,
        "breadth_direction_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    args = parser.parse_args()
    try:
        result = build_transform(json.load(sys.stdin), args.policy)
    except (KoreaLeadershipError, json.JSONDecodeError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
