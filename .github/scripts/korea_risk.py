#!/usr/bin/env python3
"""P1-KR-06 transient Korea Risk/Vol transform.

Consumes an already-qualified, exact-session KRX index-close envelope from
stdin or memory. It never fetches or persists source prices and grants no
stress, Regime, Production, or trading authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "korea_risk_contract.json"
INPUT_POLICY_PATH = ROOT / "config" / "korea_risk_input_policy.json"
KST = ZoneInfo("Asia/Seoul")
RUN_MODES = {"FORWARD_SHADOW", "HISTORICAL_REPLAY"}


class KoreaRiskError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise KoreaRiskError(f"{code}: {detail}" if detail else code)


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
    pinned = {
        "schema_version": 1,
        "contract_version": "korea_risk_contract/v1",
        "transform_version": "korea_risk/v1",
        "measurement": "korea_risk_vol_derived_features",
        "return_semantics": "simple_close_to_close",
        "realized_vol_estimator": "sqrt_mean_squared_simple_returns",
        "drawdown_semantics": "close_peak_to_trough",
        "session_coverage_policy": "exact_expected_session_dates_no_fill",
        "input_retention_policy": "transient_memory_or_stdin_only",
        "output_retention_policy": "non_reconstructive_derived_features_only",
        "stress_calibration_status": "UNRATIFIED",
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
    }
    if value != pinned:
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return value


POLICY_KEYS = {
    "schema_version", "policy_version", "approval_status", "effective_from",
    "source_name", "market", "index_identity", "market_timezone",
    "allowed_run_modes", "session_calendar_source",
    "publication_timing_source", "earliest_usable_time",
    "realized_vol_lookback_returns", "annualization_sessions",
    "drawdown_lookback_closes",
}


def load_input_policy(path: Path = INPUT_POLICY_PATH) -> dict:
    value = read_json(path, "INPUT_POLICY_INVALID")
    if set(value) != POLICY_KEYS or value.get("schema_version") != 1:
        fail("INPUT_POLICY_INVALID", "schema")
    if value.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}:
        fail("INPUT_POLICY_INVALID", "approval_status")
    if value.get("market") != "KOREA" or value.get("market_timezone") != "Asia/Seoul":
        fail("INPUT_POLICY_INVALID", "market identity")
    return value


def require_ratified(policy: dict) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("INPUT_POLICY_UNRATIFIED", policy["policy_version"])
    required = POLICY_KEYS - {"schema_version", "approval_status"}
    if any(policy.get(key) in (None, "", []) for key in required):
        fail("INPUT_POLICY_INVALID", "ratified fields incomplete")
    if policy["allowed_run_modes"] != sorted(set(policy["allowed_run_modes"])):
        fail("INPUT_POLICY_INVALID", "allowed_run_modes")
    if not set(policy["allowed_run_modes"]).issubset(RUN_MODES):
        fail("INPUT_POLICY_INVALID", "allowed_run_modes")
    for key in ("realized_vol_lookback_returns", "annualization_sessions", "drawdown_lookback_closes"):
        if type(policy[key]) is not int or policy[key] < 2:
            fail("INPUT_POLICY_INVALID", key)
    try:
        dt.date.fromisoformat(policy["effective_from"])
        dt.time.fromisoformat(policy["earliest_usable_time"])
    except (TypeError, ValueError):
        fail("INPUT_POLICY_INVALID", "temporal policy")


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


def parse_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        fail("NUMBER_MUST_BE_DECIMAL_STRING", label)
    try:
        number = Decimal(value)
    except InvalidOperation:
        fail("NUMBER_INVALID", label)
    if not number.is_finite() or number <= 0:
        fail("NUMBER_INVALID", label)
    return number


def render(number: Decimal, places: int) -> str:
    quantum = Decimal(1).scaleb(-places)
    return format(number.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def build_transform(payload: dict, input_policy_path: Path = INPUT_POLICY_PATH) -> dict:
    contract = load_contract()
    policy = load_input_policy(input_policy_path)
    require_ratified(policy)
    expected_keys = {
        "schema_version", "source_name", "market", "index_identity",
        "market_timezone", "run_mode", "observation_date", "fetched_at",
        "available_at", "decision_at", "expected_session_dates", "rows",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys or payload.get("schema_version") != 1:
        fail("INPUT_INVALID", "schema")
    for key in ("source_name", "market", "index_identity", "market_timezone"):
        if payload[key] != policy[key]:
            fail("INPUT_IDENTITY_MISMATCH", key)
    mode = payload["run_mode"]
    if mode not in policy["allowed_run_modes"]:
        fail("RUN_MODE_NOT_ALLOWED", str(mode))

    dates = payload["expected_session_dates"]
    if not isinstance(dates, list) or len(dates) < 2 or dates != sorted(set(dates)):
        fail("SESSION_CALENDAR_INVALID")
    try:
        parsed_dates = [dt.date.fromisoformat(item) for item in dates]
        observation_date = dt.date.fromisoformat(payload["observation_date"])
        effective = dt.date.fromisoformat(policy["effective_from"])
    except (TypeError, ValueError):
        fail("SESSION_CALENDAR_INVALID")
    if observation_date != parsed_dates[-1] or parsed_dates[0] < effective:
        fail("SESSION_CALENDAR_INVALID", "observation/effective date")

    rows = payload["rows"]
    if not isinstance(rows, list) or [row.get("session_date") for row in rows if isinstance(row, dict)] != dates:
        fail("SESSION_COVERAGE_MISMATCH")
    if any(set(row) != {"session_date", "close"} for row in rows):
        fail("INPUT_INVALID", "row schema")
    closes = [parse_decimal(row["close"], row["session_date"]) for row in rows]
    returns_needed = policy["realized_vol_lookback_returns"]
    drawdown_needed = policy["drawdown_lookback_closes"]
    if len(closes) < max(returns_needed + 1, drawdown_needed):
        fail("INSUFFICIENT_HISTORY")

    available = parse_timestamp(payload["available_at"], "available_at")
    fetched = parse_timestamp(payload["fetched_at"], "fetched_at")
    earliest = dt.datetime.combine(observation_date, dt.time.fromisoformat(policy["earliest_usable_time"]), KST)
    if available < earliest:
        fail("TEMPORAL_INPUT_NOT_QUALIFIED", "AVAILABLE_BEFORE_POLICY_CUTOFF")
    if fetched < available:
        fail("TEMPORAL_INPUT_NOT_QUALIFIED", "FETCH_PRECEDES_AVAILABLE")
    if mode == "FORWARD_SHADOW":
        decision = parse_timestamp(payload["decision_at"], "decision_at")
        if decision < fetched:
            fail("TEMPORAL_INPUT_NOT_QUALIFIED", "DECISION_PRECEDES_FETCH")
        eligibility = "FORWARD_PIT_QUALIFIED"
        status = "AVAILABLE_UNCALIBRATED"
    else:
        if payload["decision_at"] is not None:
            fail("TEMPORAL_INPUT_NOT_QUALIFIED", "REPLAY_DECISION_MUST_BE_NULL")
        eligibility = "CAUSAL_REPLAY_ONLY"
        status = "CAUSAL_REPLAY_ONLY"

    rv_closes = closes[-(returns_needed + 1):]
    returns = [rv_closes[i] / rv_closes[i - 1] - 1 for i in range(1, len(rv_closes))]
    variance = sum((item * item for item in returns), Decimal(0)) / Decimal(len(returns))
    annualized_vol = (variance * Decimal(policy["annualization_sessions"])).sqrt()
    dd_closes = closes[-drawdown_needed:]
    peak = dd_closes[0]
    maximum = Decimal(0)
    for close in dd_closes:
        peak = max(peak, close)
        maximum = min(maximum, close / peak - 1)
    current = dd_closes[-1] / max(dd_closes) - 1
    places = contract["output_decimal_places"]
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "transform_version": contract["transform_version"],
        "market": "KOREA",
        "index_identity": policy["index_identity"],
        "observation_date": payload["observation_date"],
        "available_at": payload["available_at"],
        "status": status,
        "temporal_eligibility": {
            "eligibility": eligibility,
            "publication_timing_source": policy["publication_timing_source"],
            "authoritative_historical_pit": False,
        },
        "realized_volatility": {
            "lookback_returns": returns_needed,
            "annualization_sessions": policy["annualization_sessions"],
            "annualized_fraction": render(annualized_vol, places),
        },
        "drawdown": {
            "lookback_closes": drawdown_needed,
            "current_fraction": render(current, places),
            "maximum_fraction": render(maximum, places),
        },
        "stress_features": {"classification": "UNDEFINED"},
        "stress_threshold_authorized": False,
        "stress_classification_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-policy", type=Path, default=INPUT_POLICY_PATH)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        result = build_transform(payload, args.input_policy)
    except (KoreaRiskError, json.JSONDecodeError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
