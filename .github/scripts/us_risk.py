#!/usr/bin/env python3
"""Build transient-only US Risk/Vol derived observations.

The helper consumes a memory or stdin envelope and emits only
non-reconstructive derived features.  It makes no network request and never
returns or persists vendor price rows.  A separately ratified source/input
policy is mandatory, and the existing US price temporal eligibility contract
decides whether the input is forward PIT, causal research, or revised
sensitivity evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "us_risk_contract.json"
INPUT_POLICY_PATH = ROOT / "config" / "us_risk_input_policy.json"
TEMPORAL_SCRIPT = ROOT / "atlas_price_pit_contract.py"
ASSET = re.compile(r"^[A-Z0-9._-]{1,20}$")


class USRiskError(RuntimeError):
    """Fail-closed US Risk/Vol contract or input violation."""


def fail(code: str, detail: str) -> None:
    raise USRiskError(f"{code}: {detail}")


def load_temporal_module(path: Path = TEMPORAL_SCRIPT):
    spec = importlib.util.spec_from_file_location(
        "atlas_us_price_temporal", path
    )
    if spec is None or spec.loader is None:
        fail("TEMPORAL_CONTRACT_INVALID", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEMPORAL = load_temporal_module()


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_json_constant,
        )
    except (OSError, json.JSONDecodeError, InvalidOperation) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "root must be object")
    return value


def parse_payload(raw: bytes) -> dict:
    try:
        value = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, InvalidOperation) as exc:
        fail("INPUT_INVALID", str(exc))
    if not isinstance(value, dict):
        fail("INPUT_INVALID", "root must be object")
    return value


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("FILE_HASH_INVALID", str(exc))


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_INVALID")
    expected = {
        "schema_version",
        "contract_version",
        "source_temporal_contract",
        "transform_version",
        "measurement",
        "return_semantics",
        "realized_vol_estimator",
        "drawdown_semantics",
        "session_coverage_policy",
        "input_retention_policy",
        "output_retention_policy",
        "stress_calibration_status",
        "output_decimal_places",
        "rounding",
    }
    pinned = {
        "schema_version": 1,
        "contract_version": "us_risk_contract/v1",
        "source_temporal_contract": "atlas_price_pit_contract.py/v0.1",
        "transform_version": "us_risk/v1",
        "measurement": "us_risk_vol_derived_features",
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
    if set(contract) != expected or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def parse_date(value: object, code: str, label: str) -> dt.date:
    if not isinstance(value, str):
        fail(code, label)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(code, label)


def sorted_strings(value: object, code: str, label: str) -> list:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        fail(code, label)
    return value


def load_input_policy(path: Path = INPUT_POLICY_PATH) -> dict:
    policy = read_json(path, "INPUT_POLICY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "effective_from",
        "source_name",
        "asset",
        "quote_currency",
        "market_timezone",
        "price_basis",
        "allowed_run_modes",
        "session_calendar_source",
        "realized_vol_lookback_returns",
        "annualization_sessions",
        "drawdown_lookback_closes",
        "split_window_policy",
    }
    if set(policy) != expected:
        fail("INPUT_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
    ):
        fail("INPUT_POLICY_INVALID", "header")
    effective = policy.get("effective_from")
    if effective is not None:
        parse_date(effective, "INPUT_POLICY_INVALID", "effective_from")
    source = policy.get("source_name")
    if source is not None and (
        not isinstance(source, str) or not source.strip()
    ):
        fail("INPUT_POLICY_INVALID", "source_name")
    asset = policy.get("asset")
    if asset is not None and (
        not isinstance(asset, str) or ASSET.fullmatch(asset) is None
    ):
        fail("INPUT_POLICY_INVALID", "asset")
    quote = policy.get("quote_currency")
    if quote is not None and quote != "USD":
        fail("INPUT_POLICY_INVALID", "quote_currency")
    timezone = policy.get("market_timezone")
    if timezone is not None and timezone != TEMPORAL.MARKET_TZ:
        fail("INPUT_POLICY_INVALID", "market_timezone")
    basis = policy.get("price_basis")
    if basis is not None and basis not in TEMPORAL.PRICE_BASES:
        fail("INPUT_POLICY_INVALID", "price_basis")
    modes = policy.get("allowed_run_modes")
    if modes is not None:
        sorted_strings(modes, "INPUT_POLICY_INVALID", "allowed_run_modes")
        if any(mode not in TEMPORAL.RUN_MODES for mode in modes):
            fail("INPUT_POLICY_INVALID", "allowed_run_modes")
    calendar = policy.get("session_calendar_source")
    if calendar is not None and (
        not isinstance(calendar, str) or not calendar.strip()
    ):
        fail("INPUT_POLICY_INVALID", "session_calendar_source")
    for key in (
        "realized_vol_lookback_returns",
        "annualization_sessions",
        "drawdown_lookback_closes",
    ):
        value = policy.get(key)
        if value is not None and (type(value) is not int or value < 2):
            fail("INPUT_POLICY_INVALID", key)
    split = policy.get("split_window_policy")
    if split is not None and split != "no_split_events_required":
        fail("INPUT_POLICY_INVALID", "split_window_policy")
    return policy


def require_ratified_policy(
    policy: dict, first_session: Optional[dt.date] = None
) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("INPUT_POLICY_UNRATIFIED", policy["policy_version"])
    required = (
        policy["effective_from"],
        policy["source_name"],
        policy["asset"],
        policy["quote_currency"],
        policy["market_timezone"],
        policy["price_basis"],
        policy["allowed_run_modes"],
        policy["session_calendar_source"],
        policy["realized_vol_lookback_returns"],
        policy["annualization_sessions"],
        policy["drawdown_lookback_closes"],
        policy["split_window_policy"],
    )
    if any(value is None for value in required) or not policy[
        "allowed_run_modes"
    ]:
        fail("INPUT_POLICY_INVALID", "ratified fields incomplete")
    effective = parse_date(
        policy["effective_from"], "INPUT_POLICY_INVALID", "effective_from"
    )
    if first_session is not None and effective > first_session:
        fail("INPUT_POLICY_NOT_EFFECTIVE", first_session.isoformat())


def decimal_string(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        fail("INPUT_ROW_INVALID", label)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        fail("INPUT_ROW_INVALID", label)
    if not parsed.is_finite() or parsed <= 0:
        fail("INPUT_ROW_INVALID", label)
    return parsed


def ensure_no_float(value: object, label: str = "input") -> None:
    if isinstance(value, float) or isinstance(value, Decimal):
        fail("INPUT_NUMBER_MUST_BE_STRING", label)
    if isinstance(value, dict):
        for key, item in value.items():
            ensure_no_float(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_no_float(item, f"{label}[{index}]")


def normalize_input(payload: dict, policy: dict) -> dict:
    ensure_no_float(payload)
    expected = {
        "schema_version",
        "source_name",
        "asset",
        "quote_currency",
        "market_timezone",
        "run_mode",
        "price_basis",
        "observation_date",
        "fetched_at",
        "decision_at",
        "expected_session_dates",
        "rows",
    }
    if set(payload) != expected or payload.get("schema_version") != 1:
        fail("INPUT_INVALID", "schema")
    for key in (
        "source_name",
        "asset",
        "quote_currency",
        "market_timezone",
        "run_mode",
        "price_basis",
        "observation_date",
        "fetched_at",
    ):
        if not isinstance(payload.get(key), str) or not payload[key]:
            fail("INPUT_INVALID", key)
    if payload["decision_at"] is not None and not isinstance(
        payload["decision_at"], str
    ):
        fail("INPUT_INVALID", "decision_at")
    comparisons = {
        "source_name": policy["source_name"],
        "asset": policy["asset"],
        "quote_currency": policy["quote_currency"],
        "market_timezone": policy["market_timezone"],
        "price_basis": policy["price_basis"],
    }
    for key, expected_value in comparisons.items():
        if payload[key] != expected_value:
            fail("INPUT_POLICY_MISMATCH", key)
    if payload["run_mode"] not in policy["allowed_run_modes"]:
        fail("INPUT_POLICY_MISMATCH", "run_mode")

    dates_value = payload["expected_session_dates"]
    if not isinstance(dates_value, list) or not dates_value:
        fail("SESSION_CALENDAR_INVALID", "expected_session_dates")
    sessions = [
        parse_date(value, "SESSION_CALENDAR_INVALID", "session date")
        for value in dates_value
    ]
    if sessions != sorted(set(sessions)) or any(
        day.weekday() >= 5 for day in sessions
    ):
        fail("SESSION_CALENDAR_INVALID", "order, duplicate, or weekend")

    rows_value = payload["rows"]
    if not isinstance(rows_value, list) or not rows_value:
        fail("INPUT_ROWS_INVALID", "rows")
    rows = []
    for index, row in enumerate(rows_value):
        if not isinstance(row, dict) or set(row) != {
            "session_date",
            "close",
            "split_factor",
        }:
            fail("INPUT_ROW_INVALID", f"row {index} schema")
        day = parse_date(
            row["session_date"], "INPUT_ROW_INVALID", f"row {index} date"
        )
        rows.append(
            {
                "date": day,
                "close": decimal_string(row["close"], f"row {index} close"),
                "split_factor": decimal_string(
                    row["split_factor"], f"row {index} split_factor"
                ),
            }
        )
    row_dates = [item["date"] for item in rows]
    if row_dates != sessions:
        fail("SESSION_COVERAGE_MISMATCH", "rows != expected sessions")
    observation = parse_date(
        payload["observation_date"], "INPUT_INVALID", "observation_date"
    )
    if observation != sessions[-1]:
        fail("OBSERVATION_DATE_MISMATCH", "last expected session")
    if any(item["split_factor"] != Decimal(1) for item in rows):
        fail("SPLIT_EVENT_IN_WINDOW", policy["split_window_policy"])
    required = max(
        policy["realized_vol_lookback_returns"] + 1,
        policy["drawdown_lookback_closes"],
    )
    if len(rows) < required:
        fail("INSUFFICIENT_HISTORY", f"{len(rows)} < {required}")
    return {
        "rows": rows,
        "sessions": sessions,
        "observation_date": observation,
    }


def temporal_classification(payload: dict) -> dict:
    try:
        result = TEMPORAL.classify(
            payload["run_mode"],
            payload["price_basis"],
            payload["observation_date"],
            fetched_at=payload["fetched_at"],
            decision_at=payload["decision_at"],
            market_tz=payload["market_timezone"],
        )
    except TEMPORAL.Stop as exc:
        fail("TEMPORAL_INPUT_INVALID", str(exc))
    if payload["run_mode"] == "FORWARD_SHADOW" and not result[
        "forward_pit_qualified"
    ]:
        fail("TEMPORAL_INPUT_NOT_QUALIFIED", result["reason_code"])
    allowed = {
        "FORWARD_PIT_QUALIFIED",
        "CAUSAL_RESEARCH_ONLY",
        "REVISED_SENSITIVITY_ONLY",
    }
    if result["eligibility"] not in allowed:
        fail("TEMPORAL_INPUT_NOT_QUALIFIED", result["eligibility"])
    return result


def render_decimal(value: Decimal, places: int) -> str:
    if not value.is_finite():
        fail("OUTPUT_NUMBER_INVALID", "non-finite")
    quantum = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        fail("OUTPUT_NUMBER_INVALID", str(exc))
    if rounded == 0:
        rounded = Decimal(0)
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def realized_volatility(rows: list, policy: dict, contract: dict) -> dict:
    count = policy["realized_vol_lookback_returns"]
    window = rows[-(count + 1) :]
    with localcontext() as context:
        context.prec = 50
        returns = [
            after["close"] / before["close"] - Decimal(1)
            for before, after in zip(window, window[1:])
        ]
        mean_square = sum((value * value for value in returns), Decimal(0))
        mean_square /= Decimal(count)
        annualized = (
            mean_square * Decimal(policy["annualization_sessions"])
        ).sqrt()
    return {
        "lookback_returns": count,
        "window_start": window[0]["date"].isoformat(),
        "window_end": window[-1]["date"].isoformat(),
        "return_semantics": contract["return_semantics"],
        "estimator": contract["realized_vol_estimator"],
        "annualization_sessions": policy["annualization_sessions"],
        "annualized_fraction": render_decimal(
            annualized, contract["output_decimal_places"]
        ),
    }


def drawdown(rows: list, policy: dict, contract: dict) -> dict:
    count = policy["drawdown_lookback_closes"]
    window = rows[-count:]
    current_peak = max(window, key=lambda item: item["close"])
    with localcontext() as context:
        context.prec = 50
        current = window[-1]["close"] / current_peak["close"] - Decimal(1)
        running_peak = window[0]
        maximum = Decimal(0)
        maximum_peak = window[0]
        maximum_trough = window[0]
        for row in window:
            if row["close"] > running_peak["close"]:
                running_peak = row
            value = row["close"] / running_peak["close"] - Decimal(1)
            if value < maximum:
                maximum = value
                maximum_peak = running_peak
                maximum_trough = row
    places = contract["output_decimal_places"]
    return {
        "lookback_closes": count,
        "window_start": window[0]["date"].isoformat(),
        "window_end": window[-1]["date"].isoformat(),
        "semantics": contract["drawdown_semantics"],
        "current_fraction": render_decimal(current, places),
        "current_peak_day": current_peak["date"].isoformat(),
        "maximum_fraction": render_decimal(maximum, places),
        "maximum_peak_day": maximum_peak["date"].isoformat(),
        "maximum_trough_day": maximum_trough["date"].isoformat(),
    }


def canonical_payload_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def authority_boundary() -> dict:
    return {
        "stress_threshold_authorized": False,
        "stress_classification_authorized": False,
        "regime_axis_input_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def build_transform(
    payload: dict,
    contract_path: Path = CONTRACT_PATH,
    input_policy_path: Path = INPUT_POLICY_PATH,
) -> dict:
    contract = load_contract(contract_path)
    policy = load_input_policy(input_policy_path)
    if policy["approval_status"] != "RATIFIED":
        fail("INPUT_POLICY_UNRATIFIED", policy["policy_version"])
    require_ratified_policy(policy)
    normalized = normalize_input(payload, policy)
    require_ratified_policy(policy, normalized["sessions"][0])
    temporal = temporal_classification(payload)
    realized = realized_volatility(normalized["rows"], policy, contract)
    decline = drawdown(normalized["rows"], policy, contract)
    available_at = temporal.get("available_at", payload["fetched_at"])
    output_status = {
        "FORWARD_PIT_QUALIFIED": "AVAILABLE_UNCALIBRATED",
        "CAUSAL_RESEARCH_ONLY": "CAUSAL_RESEARCH_ONLY",
        "REVISED_SENSITIVITY_ONLY": "REVISED_SENSITIVITY_ONLY",
    }[temporal["eligibility"]]
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "transform_version": contract["transform_version"],
        "market": "US",
        "asset": policy["asset"],
        "quote_currency": policy["quote_currency"],
        "measurement": contract["measurement"],
        "status": output_status,
        "observation_date": normalized["observation_date"].isoformat(),
        "available_at": available_at,
        "temporal_eligibility": {
            "run_mode": temporal["run_mode"],
            "price_basis": temporal["price_basis"],
            "eligibility": temporal["eligibility"],
            "reason_code": temporal["reason_code"],
            "authoritative_historical_pit": temporal[
                "authoritative_historical_pit"
            ],
            "forward_pit_qualified": temporal["forward_pit_qualified"],
        },
        "realized_volatility": realized,
        "drawdown": decline,
        "stress_features": {
            "calibration_status": contract["stress_calibration_status"],
            "thresholds_applied": False,
            "classification": "UNDEFINED",
            "feature_vector": {
                "realized_vol_annualized_fraction": realized[
                    "annualized_fraction"
                ],
                "current_drawdown_fraction": decline["current_fraction"],
                "maximum_drawdown_fraction": decline["maximum_fraction"],
            },
        },
        "retention": {
            "input_policy": contract["input_retention_policy"],
            "output_policy": contract["output_retention_policy"],
            "vendor_rows_emitted": False,
            "vendor_prices_emitted": False,
            "reconstructive_series_emitted": False,
        },
        "lineage": {
            "input_sha256": canonical_payload_sha256(payload),
            "policy_version": policy["policy_version"],
            "policy_sha256": file_sha256(input_policy_path),
            "source_temporal_contract": contract[
                "source_temporal_contract"
            ],
            "session_calendar_source": policy["session_calendar_source"],
            "session_count": len(normalized["sessions"]),
            "session_coverage_complete": True,
            "missing_data_policy": contract["session_coverage_policy"],
        },
    } | authority_boundary()


def write_output(payload: dict, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp.{os.getpid()}"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    transform = parser.add_subparsers(dest="command", required=True).add_parser(
        "transform"
    )
    transform.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    transform.add_argument("--policy", type=Path, default=INPUT_POLICY_PATH)
    transform.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read()
        if not raw:
            fail("INPUT_INVALID", "stdin is empty")
        result = build_transform(
            parse_payload(raw),
            contract_path=args.contract,
            input_policy_path=args.policy,
        )
        if args.out is None:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(write_output(result, args.out))
        return 0
    except USRiskError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
