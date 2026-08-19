#!/usr/bin/env python3
"""Build a deterministic, uncalibrated US stress replay evidence packet.

The helper consumes only validated ``regime_output/v1`` envelopes.  It makes
no network request, retains no vendor prices, chooses no replay dates, and
computes no score, threshold, weight, market classification, or action.  An
explicitly ratified case policy must enumerate every expected market date.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import output_contract as REGIME  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "us_stress_replay_packet_contract.json"
CASE_POLICY_PATH = ROOT / "config" / "us_stress_replay_case_policy.json"
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
VERSION = re.compile(r"^[a-z][a-z0-9_/-]*/v[1-9][0-9]*$")


class StressReplayError(RuntimeError):
    """Fail-closed replay packet contract, policy, or source violation."""


def fail(code: str, detail: str) -> None:
    raise StressReplayError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "root must be object")
    return value


def parse_payload(raw: bytes) -> dict:
    try:
        value = json.loads(raw, parse_constant=reject_json_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        fail("INPUT_INVALID", str(exc))
    if not isinstance(value, dict):
        fail("INPUT_INVALID", "root must be object")
    return value


def ensure_no_float(value: object, label: str = "input") -> None:
    if isinstance(value, (float, Decimal)):
        fail("FLOAT_NOT_ALLOWED", label)
    if isinstance(value, dict):
        for key, item in value.items():
            ensure_no_float(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_no_float(item, f"{label}[{index}]")


def canonical_bytes(value: object) -> bytes:
    ensure_no_float(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))
    return encoded.encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("FILE_HASH_INVALID", str(exc))


def parse_date(value: object, code: str, label: str) -> dt.date:
    if not isinstance(value, str):
        fail(code, label)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code, f"{label}={value}")
    if parsed.isoformat() != value:
        fail(code, f"{label}={value}")
    return parsed


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_INVALID")
    pinned = {
        "schema_version": 1,
        "contract_version": "us_stress_replay_packet/v1",
        "source_contract_version": "regime_output/v1",
        "market": "US",
        "market_timezone": "America/New_York",
        "required_contexts": [
            "STRESS_2008",
            "RECENT_BULL",
            "RECENT_BEAR",
            "RECENT_SIDEWAYS",
        ],
        "allowed_temporal_uses": [
            "CAUSAL_RESEARCH_ONLY",
            "REVISED_SENSITIVITY_ONLY",
        ],
        "authoritative_historical_pit_status": "UNAVAILABLE",
        "coverage_policy": "exact_explicit_market_dates_no_fill",
        "source_sequence_policy": (
            "one_validated_output_per_expected_market_date"
        ),
        "digest_policy": "canonical_json_sha256",
        "evaluation_status": "UNDEFINED_UNCALIBRATED",
        "output_retention_policy": (
            "non_reconstructive_regime_evidence_only"
        ),
    }
    if set(contract) != set(pinned) or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def normalize_case(record: object, index: int, contract: dict) -> dict:
    expected = {
        "case_id",
        "context",
        "start_date",
        "end_date",
        "expected_market_dates",
        "temporal_use",
        "reason",
    }
    if not isinstance(record, dict) or set(record) != expected:
        fail("CASE_POLICY_INVALID", f"case {index} schema")
    case_id = record["case_id"]
    if not isinstance(case_id, str) or CASE_ID.fullmatch(case_id) is None:
        fail("CASE_POLICY_INVALID", f"case {index} case_id")
    context = record["context"]
    if context not in contract["required_contexts"]:
        fail("CASE_POLICY_INVALID", f"case {index} context")
    if record["temporal_use"] not in contract["allowed_temporal_uses"]:
        fail("CASE_POLICY_INVALID", f"case {index} temporal_use")
    if not isinstance(record["reason"], str) or not record["reason"].strip():
        fail("CASE_POLICY_INVALID", f"case {index} reason")
    start = parse_date(
        record["start_date"], "CASE_POLICY_INVALID", f"case {index} start"
    )
    end = parse_date(
        record["end_date"], "CASE_POLICY_INVALID", f"case {index} end"
    )
    dates_value = record["expected_market_dates"]
    if not isinstance(dates_value, list) or not dates_value:
        fail("CASE_POLICY_INVALID", f"case {index} expected_market_dates")
    dates = [
        parse_date(
            item,
            "CASE_POLICY_INVALID",
            f"case {index} expected_market_dates",
        )
        for item in dates_value
    ]
    if dates != sorted(set(dates)):
        fail("CASE_POLICY_INVALID", f"case {index} date order/duplicate")
    if start != dates[0] or end != dates[-1]:
        fail("CASE_POLICY_INVALID", f"case {index} range bounds")
    if context == "STRESS_2008" and any(day.year != 2008 for day in dates):
        fail("CASE_POLICY_INVALID", "STRESS_2008 dates must be in 2008")
    return record | {"_dates": dates}


def load_case_policy(
    path: Path = CASE_POLICY_PATH,
    contract: Optional[dict] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    policy = read_json(path, "CASE_POLICY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "effective_from",
        "source_policy_version",
        "cases",
    }
    if set(policy) != expected:
        fail("CASE_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
        or not isinstance(policy.get("cases"), list)
    ):
        fail("CASE_POLICY_INVALID", "header")
    effective = policy.get("effective_from")
    if effective is not None:
        parse_date(effective, "CASE_POLICY_INVALID", "effective_from")
    source_policy = policy.get("source_policy_version")
    if source_policy is not None and (
        not isinstance(source_policy, str)
        or VERSION.fullmatch(source_policy) is None
    ):
        fail("CASE_POLICY_INVALID", "source_policy_version")
    cases = [
        normalize_case(record, index, contract)
        for index, record in enumerate(policy["cases"])
    ]
    ids = [item["case_id"] for item in cases]
    contexts = [item["context"] for item in cases]
    if len(ids) != len(set(ids)):
        fail("CASE_POLICY_INVALID", "duplicate case_id")
    if len(contexts) != len(set(contexts)):
        fail("CASE_POLICY_INVALID", "duplicate context")
    if policy["approval_status"] == "RATIFIED":
        if effective is None or source_policy is None:
            fail("CASE_POLICY_INVALID", "ratified fields incomplete")
        if contexts != contract["required_contexts"]:
            fail("CASE_POLICY_INVALID", "required contexts incomplete/order")
    return policy | {"_cases": cases}


def require_ratified_policy(policy: dict) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("CASE_POLICY_UNRATIFIED", policy["policy_version"])


def market_date(source: dict, contract: dict) -> dt.date:
    generated = REGIME.parse_utc(source["generated_at"], "generated_at")
    return generated.astimezone(
        ZoneInfo(contract["market_timezone"])
    ).date()


def normalize_source(
    source: object,
    expected_date: dt.date,
    contract: dict,
) -> dict:
    try:
        validated = REGIME.validate_output(source)
    except REGIME.OutputContractError as exc:
        fail("SOURCE_OUTPUT_INVALID", str(exc))
    if validated["contract_version"] != contract["source_contract_version"]:
        fail("SOURCE_CONTRACT_INVALID", validated["contract_version"])
    if validated["market"] != contract["market"]:
        fail("SOURCE_MARKET_INVALID", validated["market"])
    if validated["market_timezone"] != contract["market_timezone"]:
        fail("SOURCE_TIMEZONE_INVALID", validated["market_timezone"])
    actual_date = market_date(validated, contract)
    if actual_date != expected_date:
        fail(
            "SOURCE_DATE_MISMATCH",
            f"expected={expected_date.isoformat()} actual={actual_date.isoformat()}",
        )
    return validated


def authority_boundary() -> dict:
    return {
        "authoritative_historical_pit": False,
        "behavior_assessment_authorized": False,
        "thresholds_authorized": False,
        "weights_authorized": False,
        "regime_classification_authorized": False,
        "strategy_eligibility_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def build_packet(
    payload: dict,
    contract_path: Path = CONTRACT_PATH,
    case_policy_path: Path = CASE_POLICY_PATH,
) -> dict:
    ensure_no_float(payload)
    contract = load_contract(contract_path)
    policy = load_case_policy(case_policy_path, contract)
    require_ratified_policy(policy)
    if set(payload) != {"schema_version", "policy_version", "cases"}:
        fail("INPUT_INVALID", "schema")
    if payload.get("schema_version") != 1:
        fail("INPUT_INVALID", "schema_version")
    if payload.get("policy_version") != policy["policy_version"]:
        fail("INPUT_POLICY_MISMATCH", "policy_version")
    input_cases = payload.get("cases")
    if not isinstance(input_cases, list):
        fail("INPUT_INVALID", "cases")
    if len(input_cases) != len(policy["_cases"]):
        fail("CASE_SET_MISMATCH", "case count")

    case_packets = []
    for index, (case_policy, case_input) in enumerate(
        zip(policy["_cases"], input_cases)
    ):
        if not isinstance(case_input, dict) or set(case_input) != {
            "case_id",
            "outputs",
        }:
            fail("INPUT_INVALID", f"case {index} schema")
        if case_input["case_id"] != case_policy["case_id"]:
            fail("CASE_SET_MISMATCH", f"case {index} identity/order")
        outputs = case_input["outputs"]
        expected_dates = case_policy["_dates"]
        if not isinstance(outputs, list) or len(outputs) != len(expected_dates):
            fail("CASE_DATE_COVERAGE_MISMATCH", case_policy["case_id"])
        points = []
        case_previous = None
        for expected_date, source in zip(expected_dates, outputs):
            validated = normalize_source(source, expected_date, contract)
            generated = REGIME.parse_utc(
                validated["generated_at"], "generated_at"
            )
            if case_previous is not None and generated <= case_previous:
                fail("SOURCE_SEQUENCE_INVALID", case_policy["case_id"])
            case_previous = generated
            points.append(
                {
                    "market_date": expected_date.isoformat(),
                    "generated_at": validated["generated_at"],
                    "source_output_sha256": canonical_sha256(validated),
                    "regime": validated["regime"],
                    "direction": validated["direction"],
                    "confidence": validated["confidence"],
                    "coverage": validated["coverage"],
                    "evidence_as_of": validated["evidence_as_of"],
                    "available_as_of": validated["available_as_of"],
                    "warnings": validated["warnings"],
                }
            )
        case_packets.append(
            {
                "case_id": case_policy["case_id"],
                "context": case_policy["context"],
                "temporal_use": case_policy["temporal_use"],
                "start_date": case_policy["start_date"],
                "end_date": case_policy["end_date"],
                "status": "RESEARCH_EVIDENCE_AVAILABLE_UNCALIBRATED",
                "behavior_assessment": contract["evaluation_status"],
                "point_count": len(points),
                "source_sequence_sha256": canonical_sha256(points),
                "points": points,
            }
        )

    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "source_contract_version": contract["source_contract_version"],
        "market": contract["market"],
        "market_timezone": contract["market_timezone"],
        "status": "RESEARCH_PACKET_AVAILABLE_UNCALIBRATED",
        "behavior_assessment": contract["evaluation_status"],
        "context_coverage": {
            "required": contract["required_contexts"],
            "observed": [item["context"] for item in case_packets],
            "complete": True,
        },
        "policy": {
            "policy_version": policy["policy_version"],
            "policy_sha256": file_sha256(case_policy_path),
            "approval_status": policy["approval_status"],
            "source_policy_version": policy["source_policy_version"],
        },
        "case_count": len(case_packets),
        "cases": case_packets,
        "retention": {
            "policy": contract["output_retention_policy"],
            "vendor_rows_emitted": False,
            "vendor_prices_emitted": False,
            "reconstructive_price_series_emitted": False,
        },
    } | authority_boundary()


def validate_packet(
    packet: object,
    source: dict,
    contract_path: Path = CONTRACT_PATH,
    case_policy_path: Path = CASE_POLICY_PATH,
) -> dict:
    expected = build_packet(
        source,
        contract_path=contract_path,
        case_policy_path=case_policy_path,
    )
    if not isinstance(packet, dict):
        fail("PACKET_INVALID", "object required")
    if canonical_bytes(packet) != canonical_bytes(expected):
        fail("PACKET_DERIVATION_MISMATCH", "packet != source-derived packet")
    return packet


def write_output(payload: dict, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
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
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    build.add_argument("--policy", type=Path, default=CASE_POLICY_PATH)
    build.add_argument("--out", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("packet", type=Path)
    validate.add_argument("--source", type=Path, required=True)
    validate.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    validate.add_argument("--policy", type=Path, default=CASE_POLICY_PATH)
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            result = validate_packet(
                read_json(args.packet, "PACKET_INVALID"),
                read_json(args.source, "INPUT_INVALID"),
                contract_path=args.contract,
                case_policy_path=args.policy,
            )
        else:
            raw = sys.stdin.buffer.read()
            if not raw:
                fail("INPUT_INVALID", "stdin is empty")
            result = build_packet(
                parse_payload(raw),
                contract_path=args.contract,
                case_policy_path=args.policy,
            )
        if args.command == "build" and args.out is not None:
            print(write_output(result, args.out))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (StressReplayError, REGIME.OutputContractError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
