#!/usr/bin/env python3
"""Verify deterministic replay of pre-score Atlas Regime envelopes.

The harness compares two independently supplied ``regime_output/v1``
envelopes for each replay case.  It validates both sources and requires exact
canonical equality.  It does not score, classify, set coverage thresholds,
wire Production, or authorize an action.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import output_contract as OUTPUT  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "regime_replay_harness_contract.json"
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ReplayHarnessError(RuntimeError):
    """Fail-closed replay harness contract or source violation."""


def fail(code: str, detail: str) -> None:
    raise ReplayHarnessError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def parse_payload(raw: bytes) -> dict:
    try:
        value = json.loads(raw, parse_constant=reject_json_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        fail("INPUT_INVALID", str(exc))
    if not isinstance(value, dict):
        fail("INPUT_INVALID", "object required")
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


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = load_json(path, "CONTRACT_INVALID")
    pinned = {
        "schema_version": 1,
        "contract_version": "regime_replay_harness/v1",
        "source_contract_version": "regime_output/v1",
        "source_contract_mode": "PRE_SCORE_UNKNOWN_ONLY",
        "required_markets": ["US", "KR", "CRYPTO"],
        "comparison_policy": (
            "canonical_byte_identical_first_and_rerun"
        ),
        "explanation_policy": "coverage_evidence_time_and_warnings",
        "minimum_coverage_policy": "UNRATIFIED",
        "success_status": "DETERMINISM_VERIFIED_PRE_SCORE",
    }
    if set(contract) != set(pinned) or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def validate_source(source: object, contract: dict, label: str) -> dict:
    try:
        validated = OUTPUT.validate_output(source)
    except OUTPUT.OutputContractError as exc:
        fail("SOURCE_OUTPUT_INVALID", f"{label}: {exc}")
    if validated["contract_version"] != contract["source_contract_version"]:
        fail("SOURCE_CONTRACT_INVALID", label)
    if validated["contract_mode"] != contract["source_contract_mode"]:
        fail("SOURCE_MODE_INVALID", label)
    return validated


def authority_boundary() -> dict:
    return {
        "minimum_coverage_gate_ratified": False,
        "thresholds_authorized": False,
        "weights_authorized": False,
        "regime_score_authorized": False,
        "regime_classification_authorized": False,
        "hysteresis_authorized": False,
        "strategy_eligibility_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def factor_evidence_sha(source: dict) -> dict:
    return {
        axis: (
            None
            if source["factor_results"][axis]["evidence"] is None
            else source["factor_results"][axis]["evidence"]["sha256"]
        )
        for axis in source["coverage"]["required_axes"]
    }


def build_report(
    payload: dict,
    contract_path: Path = CONTRACT_PATH,
) -> dict:
    ensure_no_float(payload)
    contract = load_contract(contract_path)
    if set(payload) != {"schema_version", "cases"}:
        fail("INPUT_INVALID", "schema")
    if payload.get("schema_version") != 1:
        fail("INPUT_INVALID", "schema_version")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        fail("INPUT_INVALID", "cases")

    ids = []
    observed_markets = set()
    results = []
    for index, item in enumerate(cases):
        if not isinstance(item, dict) or set(item) != {
            "case_id",
            "first",
            "rerun",
        }:
            fail("INPUT_INVALID", f"case {index} schema")
        case_id = item["case_id"]
        if not isinstance(case_id, str) or CASE_ID.fullmatch(case_id) is None:
            fail("INPUT_INVALID", f"case {index} case_id")
        ids.append(case_id)
        first = validate_source(item["first"], contract, f"{case_id}.first")
        rerun = validate_source(item["rerun"], contract, f"{case_id}.rerun")
        if canonical_bytes(first) != canonical_bytes(rerun):
            fail("REPLAY_DRIFT", case_id)
        market = first["market"]
        observed_markets.add(market)
        results.append(
            {
                "case_id": case_id,
                "market": market,
                "status": contract["success_status"],
                "source_output_sha256": canonical_sha256(first),
                "generated_at": first["generated_at"],
                "regime": first["regime"],
                "direction": first["direction"],
                "confidence": first["confidence"],
                "coverage": first["coverage"],
                "evidence_as_of": first["evidence_as_of"],
                "available_as_of": first["available_as_of"],
                "factor_evidence_sha256": factor_evidence_sha(first),
                "warnings": first["warnings"],
            }
        )
    if ids != sorted(set(ids)):
        fail("CASE_ORDER_OR_DUPLICATE", ",".join(ids))
    missing = [
        market
        for market in contract["required_markets"]
        if market not in observed_markets
    ]
    unknown = sorted(observed_markets - set(contract["required_markets"]))
    if missing or unknown:
        fail(
            "MARKET_COVERAGE_INCOMPLETE",
            f"missing={missing} unknown={unknown}",
        )
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "source_contract_version": contract["source_contract_version"],
        "source_contract_mode": contract["source_contract_mode"],
        "status": contract["success_status"],
        "market_coverage": {
            "required": contract["required_markets"],
            "observed": [
                market
                for market in contract["required_markets"]
                if market in observed_markets
            ],
            "complete": True,
        },
        "case_count": len(results),
        "cases": results,
        "comparison_policy": contract["comparison_policy"],
        "explanation_policy": contract["explanation_policy"],
        "minimum_coverage_policy": contract["minimum_coverage_policy"],
    } | authority_boundary()


def validate_report(
    report: object,
    source: dict,
    contract_path: Path = CONTRACT_PATH,
) -> dict:
    expected = build_report(source, contract_path=contract_path)
    if not isinstance(report, dict):
        fail("REPORT_INVALID", "object required")
    if canonical_bytes(report) != canonical_bytes(expected):
        fail("REPORT_DERIVATION_MISMATCH", "report != source-derived report")
    return report


def write_output(payload: dict, target: Path) -> Path:
    return OUTPUT.write_output(payload, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    build.add_argument("--out", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("report", type=Path)
    validate.add_argument("--source", type=Path, required=True)
    validate.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            result = validate_report(
                load_json(args.report, "REPORT_INVALID"),
                load_json(args.source, "INPUT_INVALID"),
                contract_path=args.contract,
            )
        else:
            raw = sys.stdin.buffer.read()
            if not raw:
                fail("INPUT_INVALID", "stdin is empty")
            result = build_report(
                parse_payload(raw), contract_path=args.contract
            )
        if args.command == "build" and args.out is not None:
            print(write_output(result, args.out))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ReplayHarnessError, OUTPUT.OutputContractError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
