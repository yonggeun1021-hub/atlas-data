#!/usr/bin/env python3
"""Build and validate the pre-score Atlas Regime UNKNOWN envelope.

The approved vocabulary contains future candidate states, but the current
runtime contract authorizes only UNKNOWN/UNKNOWN with confidence=null.  This
module never computes thresholds, weights, scores, strategies, or actions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "regime_output_contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class OutputContractError(RuntimeError):
    """Fail-closed common Regime output contract violation."""


def fail(code: str, detail: str) -> None:
    raise OutputContractError(f"{code}: {detail}")


def load_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_INVALID", f"{path}: {exc}")


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = load_json(path)
    if not isinstance(contract, dict):
        fail("CONTRACT_INVALID", "object required")
    expected = {
        "schema_version",
        "contract_version",
        "contract_mode",
        "markets",
        "market_timezones",
        "regime_vocabulary",
        "runtime_authorized_regimes",
        "direction_vocabulary",
        "runtime_authorized_directions",
        "required_axes",
        "factor_statuses",
        "confidence_policy",
        "minimum_coverage_policy",
        "neutral_unknown_invariant",
        "timestamp_policy",
        "evidence_as_of_policy",
        "available_as_of_policy",
        "warning_code_pattern",
        "transform_version_pattern",
    }
    if (
        set(contract) != expected
        or type(contract.get("schema_version")) is not int
        or contract.get("schema_version") != 1
    ):
        fail("CONTRACT_INVALID", "schema or fields")
    pinned = {
        "contract_version": "regime_output/v1",
        "contract_mode": "PRE_SCORE_UNKNOWN_ONLY",
        "markets": ["US", "KR", "CRYPTO"],
        "market_timezones": {
            "US": "America/New_York",
            "KR": "Asia/Seoul",
            "CRYPTO": "UTC",
        },
        "regime_vocabulary": [
            "RISK_ON",
            "NEUTRAL",
            "RISK_OFF",
            "STRESS",
            "UNKNOWN",
        ],
        "runtime_authorized_regimes": ["UNKNOWN"],
        "direction_vocabulary": [
            "IMPROVING",
            "STABLE",
            "DETERIORATING",
            "UNKNOWN",
        ],
        "runtime_authorized_directions": ["UNKNOWN"],
        "required_axes": [
            "TREND",
            "BREADTH",
            "RISK_VOL",
            "LIQUIDITY",
            "LEADERSHIP",
        ],
        "factor_statuses": ["DEFINED", "UNDEFINED"],
        "confidence_policy": "null_until_replay_defined",
        "minimum_coverage_policy": "UNRATIFIED",
        "neutral_unknown_invariant": (
            "NEUTRAL_IS_OBSERVED_STATE_UNKNOWN_IS_INSUFFICIENT_EVIDENCE"
        ),
        "timestamp_policy": "per_axis_observation_and_utc_availability",
        "evidence_as_of_policy": "oldest_defined_axis_observation",
        "available_as_of_policy": "latest_defined_axis_availability",
        "warning_code_pattern": "^[A-Z][A-Z0-9_]*$",
        "transform_version_pattern": "^[a-z][a-z0-9_/-]*/v[1-9][0-9]*$",
    }
    if any(contract.get(key) != value for key, value in pinned.items()):
        fail("CONTRACT_INVALID", "pinned semantics")
    return contract


def parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail("TIMESTAMP_INVALID", f"{label}={value}")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        fail("TIMESTAMP_INVALID", f"{label}={value}")


def parse_date(value: object, label: str) -> dt.date:
    if not isinstance(value, str):
        fail("DATE_INVALID", f"{label}={value}")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail("DATE_INVALID", f"{label}={value}")
    if parsed.isoformat() != value:
        fail("DATE_INVALID", f"{label}={value}")
    return parsed


def warning_list(value: object, contract: dict, label: str) -> list:
    if not isinstance(value, list):
        fail("WARNINGS_INVALID", label)
    pattern = re.compile(contract["warning_code_pattern"])
    if any(
        not isinstance(item, str) or pattern.fullmatch(item) is None
        for item in value
    ):
        fail("WARNINGS_INVALID", label)
    if len(value) != len(set(value)):
        fail("WARNINGS_INVALID", label)
    return sorted(value)


def empty_factor(axis: str) -> dict:
    return {
        "axis": axis,
        "status": "UNDEFINED",
        "observation_date": None,
        "available_at": None,
        "age_seconds": None,
        "transform_version": None,
        "evidence": None,
        "warnings": ["DATA_CONTRACT_INCOMPLETE"],
    }


def defined_factor(
    axis: str,
    spec: dict,
    generated: dt.datetime,
    market_date: dt.date,
    contract: dict,
) -> dict:
    expected = {
        "status",
        "observation_date",
        "available_at",
        "transform_version",
        "evidence",
        "warnings",
    }
    if set(spec) != expected or spec.get("status") != "DEFINED":
        fail("FACTOR_INVALID", f"{axis}: fields or status")
    observation = parse_date(spec["observation_date"], f"{axis}.observation_date")
    if observation > market_date:
        fail("OBSERVATION_FROM_FUTURE", f"{axis}: {observation}")
    available = parse_utc(spec["available_at"], f"{axis}.available_at")
    if available > generated:
        fail("AVAILABILITY_FROM_FUTURE", f"{axis}: {spec['available_at']}")
    transform = spec["transform_version"]
    if (
        not isinstance(transform, str)
        or re.fullmatch(contract["transform_version_pattern"], transform) is None
    ):
        fail("TRANSFORM_VERSION_INVALID", f"{axis}: {transform}")
    evidence = spec["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"uri", "sha256"}:
        fail("EVIDENCE_INVALID", axis)
    if not isinstance(evidence["uri"], str) or not evidence["uri"].strip():
        fail("EVIDENCE_INVALID", f"{axis}: uri")
    if not isinstance(evidence["sha256"], str) or SHA256.fullmatch(
        evidence["sha256"]
    ) is None:
        fail("EVIDENCE_INVALID", f"{axis}: sha256")
    warnings = warning_list(spec["warnings"], contract, f"{axis}.warnings")
    return {
        "axis": axis,
        "status": "DEFINED",
        "observation_date": observation.isoformat(),
        "available_at": spec["available_at"],
        "age_seconds": int((generated - available).total_seconds()),
        "transform_version": transform,
        "evidence": {
            "uri": evidence["uri"],
            "sha256": evidence["sha256"],
        },
        "warnings": warnings,
    }


def normalize_factors(
    factors: Optional[dict],
    generated: dt.datetime,
    market: str,
    contract: dict,
) -> dict:
    factors = {} if factors is None else factors
    if not isinstance(factors, dict):
        fail("FACTORS_INVALID", "object required")
    axes = contract["required_axes"]
    if any(not isinstance(axis, str) for axis in factors):
        fail("AXIS_UNKNOWN", "non-string key")
    unknown = sorted(set(factors) - set(axes))
    if unknown:
        fail("AXIS_UNKNOWN", ",".join(unknown))
    try:
        market_date = generated.astimezone(
            ZoneInfo(contract["market_timezones"][market])
        ).date()
    except ZoneInfoNotFoundError as exc:
        fail("TIMEZONE_INVALID", str(exc))

    normalized = {}
    for axis in axes:
        spec = factors.get(axis)
        if spec is None:
            normalized[axis] = empty_factor(axis)
        elif isinstance(spec, dict) and spec.get("status") == "UNDEFINED":
            if set(spec) != {"status", "warnings"}:
                fail("FACTOR_INVALID", f"{axis}: undefined fields")
            warnings = warning_list(spec["warnings"], contract, f"{axis}.warnings")
            if not warnings:
                fail("UNDEFINED_FACTOR_WARNING_REQUIRED", axis)
            normalized[axis] = empty_factor(axis)
            normalized[axis]["warnings"] = warnings
        elif isinstance(spec, dict):
            normalized[axis] = defined_factor(
                axis,
                spec,
                generated,
                market_date,
                contract,
            )
        else:
            fail("FACTOR_INVALID", axis)
    return normalized


def derived_sections(factors: dict, contract: dict) -> dict:
    axes = contract["required_axes"]
    defined = [axis for axis in axes if factors[axis]["status"] == "DEFINED"]
    missing = [axis for axis in axes if axis not in defined]
    observations = {
        axis: factors[axis]["observation_date"]
        for axis in axes
    }
    availability = [
        factors[axis]["available_at"]
        for axis in defined
    ]
    warnings = {
        "MINIMUM_COVERAGE_GATE_UNRATIFIED",
        "REGIME_SCORE_NOT_AUTHORIZED",
    }
    if missing:
        warnings.add("AXIS_COVERAGE_INCOMPLETE")
    for axis in axes:
        warnings.update(factors[axis]["warnings"])
    return {
        "coverage": {
            "required_axes": axes,
            "defined_axes": defined,
            "missing_axes": missing,
            "defined_count": len(defined),
            "required_count": len(axes),
            "ratio": f"{len(defined)}/{len(axes)}",
            "minimum_gate_status": contract["minimum_coverage_policy"],
        },
        "evidence_as_of": {
            "oldest_observation_date": min(
                (observations[axis] for axis in defined),
                default=None,
            ),
            "per_axis": observations,
        },
        "available_as_of": max(availability, default=None),
        "warnings": sorted(warnings),
    }


def authority_boundary() -> dict:
    return {
        "minimum_coverage_gate_ratified": False,
        "thresholds_authorized": False,
        "weights_authorized": False,
        "regime_score_authorized": False,
        "strategy_eligibility_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def build_unknown_output(
    market: str,
    generated_at: str,
    factors: Optional[dict] = None,
    contract: Optional[dict] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    if market not in contract["markets"]:
        fail("MARKET_INVALID", market)
    generated = parse_utc(generated_at, "generated_at")
    normalized = normalize_factors(factors, generated, market, contract)
    derived = derived_sections(normalized, contract)
    payload = {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "contract_mode": contract["contract_mode"],
        "market": market,
        "market_timezone": contract["market_timezones"][market],
        "generated_at": generated_at,
        "regime": "UNKNOWN",
        "direction": "UNKNOWN",
        "confidence": None,
        "factor_results": normalized,
        "coverage": derived["coverage"],
        "evidence_as_of": derived["evidence_as_of"],
        "available_as_of": derived["available_as_of"],
        "warnings": derived["warnings"],
        "authority": authority_boundary(),
    }
    validate_output(payload, contract)
    return payload


def validate_normalized_factor(
    axis: str,
    factor: object,
    generated: dt.datetime,
    market_date: dt.date,
    contract: dict,
) -> None:
    expected = {
        "axis",
        "status",
        "observation_date",
        "available_at",
        "age_seconds",
        "transform_version",
        "evidence",
        "warnings",
    }
    if not isinstance(factor, dict) or set(factor) != expected:
        fail("FACTOR_OUTPUT_INVALID", axis)
    if factor["axis"] != axis or factor["status"] not in contract["factor_statuses"]:
        fail("FACTOR_OUTPUT_INVALID", f"{axis}: identity/status")
    warnings = warning_list(factor["warnings"], contract, f"{axis}.warnings")
    if factor["warnings"] != warnings:
        fail("WARNINGS_INVALID", f"{axis}: deterministic order")
    if factor["status"] == "UNDEFINED":
        if not warnings:
            fail("UNDEFINED_FACTOR_WARNING_REQUIRED", axis)
        nullable = (
            "observation_date",
            "available_at",
            "age_seconds",
            "transform_version",
            "evidence",
        )
        if any(factor[key] is not None for key in nullable):
            fail("UNDEFINED_FACTOR_HAS_EVIDENCE", axis)
        return

    observation = parse_date(factor["observation_date"], f"{axis}.observation_date")
    if observation > market_date:
        fail("OBSERVATION_FROM_FUTURE", axis)
    available = parse_utc(factor["available_at"], f"{axis}.available_at")
    if available > generated:
        fail("AVAILABILITY_FROM_FUTURE", axis)
    expected_age = int((generated - available).total_seconds())
    if type(factor["age_seconds"]) is not int or factor["age_seconds"] != expected_age:
        fail("FACTOR_AGE_INVALID", axis)
    transform = factor["transform_version"]
    if (
        not isinstance(transform, str)
        or re.fullmatch(contract["transform_version_pattern"], transform) is None
    ):
        fail("TRANSFORM_VERSION_INVALID", axis)
    evidence = factor["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"uri", "sha256"}:
        fail("EVIDENCE_INVALID", axis)
    if not isinstance(evidence["uri"], str) or not evidence["uri"].strip():
        fail("EVIDENCE_INVALID", f"{axis}: uri")
    if not isinstance(evidence["sha256"], str) or SHA256.fullmatch(
        evidence["sha256"]
    ) is None:
        fail("EVIDENCE_INVALID", f"{axis}: sha256")


def validate_output(payload: object, contract: Optional[dict] = None) -> dict:
    contract = load_contract() if contract is None else contract
    expected = {
        "schema_version",
        "contract_version",
        "contract_mode",
        "market",
        "market_timezone",
        "generated_at",
        "regime",
        "direction",
        "confidence",
        "factor_results",
        "coverage",
        "evidence_as_of",
        "available_as_of",
        "warnings",
        "authority",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        fail("OUTPUT_INVALID", "schema or fields")
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["contract_version"] != contract["contract_version"]
        or payload["contract_mode"] != contract["contract_mode"]
    ):
        fail("OUTPUT_INVALID", "version or mode")
    market = payload["market"]
    if market not in contract["markets"]:
        fail("MARKET_INVALID", str(market))
    if payload["market_timezone"] != contract["market_timezones"][market]:
        fail("TIMEZONE_INVALID", str(payload["market_timezone"]))
    if payload["regime"] not in contract["runtime_authorized_regimes"]:
        fail("REGIME_NOT_AUTHORIZED", str(payload["regime"]))
    if payload["direction"] not in contract["runtime_authorized_directions"]:
        fail("DIRECTION_NOT_AUTHORIZED", str(payload["direction"]))
    if payload["confidence"] is not None:
        fail("CONFIDENCE_NOT_AUTHORIZED", str(payload["confidence"]))

    generated = parse_utc(payload["generated_at"], "generated_at")
    try:
        market_date = generated.astimezone(
            ZoneInfo(contract["market_timezones"][market])
        ).date()
    except ZoneInfoNotFoundError as exc:
        fail("TIMEZONE_INVALID", str(exc))
    factors = payload["factor_results"]
    axes = contract["required_axes"]
    if not isinstance(factors, dict) or set(factors) != set(axes):
        factor_keys = list(factors) if isinstance(factors, dict) else factors
        fail("FACTOR_SET_INVALID", str(factor_keys))
    for axis in axes:
        validate_normalized_factor(
            axis,
            factors[axis],
            generated,
            market_date,
            contract,
        )
    derived = derived_sections(factors, contract)
    coverage = payload["coverage"]
    if not isinstance(coverage, dict):
        fail("COVERAGE_INVALID", "object required")
    if (
        type(coverage.get("defined_count")) is not int
        or type(coverage.get("required_count")) is not int
    ):
        fail("COVERAGE_INVALID", "integer counts required")
    for key in ("coverage", "evidence_as_of", "available_as_of", "warnings"):
        if payload[key] != derived[key]:
            fail("DERIVED_FIELD_MISMATCH", key)
    if payload["authority"] != authority_boundary():
        fail("AUTHORITY_BOUNDARY_INVALID", "authority")
    return payload


def write_output(payload: dict, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build-unknown")
    build.add_argument("--market", required=True)
    build.add_argument("--generated-at", required=True)
    build.add_argument("--factors", type=Path)
    build.add_argument("--out", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("path", type=Path)

    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = validate_output(load_json(args.path))
        print(json.dumps(payload, ensure_ascii=False, sort_keys=False))
        return 0

    factors = None if args.factors is None else load_json(args.factors)
    payload = build_unknown_output(args.market, args.generated_at, factors)
    if args.out:
        print(write_output(payload, args.out))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OutputContractError as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
