#!/usr/bin/env python3
"""Evaluate the ratified Atlas five-of-five minimum-coverage policy.

This is a coverage-only eligibility audit over ``regime_output/v1``.  Meeting
the coverage minimum does not authorize freshness, scoring, classification,
strategy eligibility, Production wiring, or trading actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import output_contract as OUTPUT  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "regime_minimum_coverage_policy.json"


class MinimumCoverageError(RuntimeError):
    """Fail-closed minimum-coverage contract violation."""


def fail(code: str, detail: str) -> None:
    raise MinimumCoverageError(f"{code}: {detail}")


def load_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_INVALID", f"{path}: {exc}")


def validate_contract(contract: object) -> dict:
    if not isinstance(contract, dict):
        fail("CONTRACT_INVALID", "object required")
    expected = {
        "schema_version",
        "contract_version",
        "source_contract_version",
        "policy_status",
        "policy_name",
        "runtime_authorized_results",
        "minimum_defined_axes",
        "required_axes",
        "axis_missing_reason_codes",
        "minimum_not_met_reason_code",
        "downstream_blocker_reason_codes",
        "fail_closed_regime",
        "fail_closed_direction",
        "confidence_policy",
        "neutral_unknown_invariant",
    }
    if set(contract) != expected:
        fail("CONTRACT_INVALID", "fields")
    pinned = {
        "schema_version": 1,
        "contract_version": "regime_minimum_coverage/v1",
        "source_contract_version": "regime_output/v1",
        "policy_status": "RATIFIED",
        "policy_name": "ALL_REQUIRED_AXES_5_OF_5",
        "runtime_authorized_results": ["BLOCKED", "COVERAGE_MET"],
        "minimum_defined_axes": 5,
        "required_axes": [
            "TREND",
            "BREADTH",
            "RISK_VOL",
            "LIQUIDITY",
            "LEADERSHIP",
        ],
        "axis_missing_reason_codes": {
            "TREND": "TREND_UNDEFINED",
            "BREADTH": "BREADTH_UNDEFINED",
            "RISK_VOL": "RISK_VOL_UNDEFINED",
            "LIQUIDITY": "LIQUIDITY_UNDEFINED",
            "LEADERSHIP": "LEADERSHIP_UNDEFINED",
        },
        "minimum_not_met_reason_code": "MINIMUM_COVERAGE_NOT_MET",
        "downstream_blocker_reason_codes": [
            "FRESHNESS_POLICY_UNRATIFIED",
            "REGIME_CLASSIFICATION_NOT_AUTHORIZED",
        ],
        "fail_closed_regime": "UNKNOWN",
        "fail_closed_direction": "UNKNOWN",
        "confidence_policy": "null_until_replay_defined",
        "neutral_unknown_invariant": (
            "NEUTRAL_IS_OBSERVED_STATE_UNKNOWN_IS_INSUFFICIENT_EVIDENCE"
        ),
    }
    if type(contract.get("schema_version")) is not int:
        fail("CONTRACT_INVALID", "integer schema_version required")
    if any(contract.get(key) != value for key, value in pinned.items()):
        fail("CONTRACT_INVALID", "pinned ratified semantics")
    if contract["minimum_defined_axes"] != len(contract["required_axes"]):
        fail("CONTRACT_INVALID", "minimum must equal required axis count")
    return contract


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(load_json(path))


def canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))
    return encoded.encode("utf-8")


def source_sha256(source: dict) -> str:
    return hashlib.sha256(canonical_bytes(source)).hexdigest()


def validate_source(source: object, contract: dict) -> dict:
    try:
        validated = OUTPUT.validate_output(source)
    except OUTPUT.OutputContractError as exc:
        fail("SOURCE_OUTPUT_INVALID", str(exc))
    if validated["contract_version"] != contract["source_contract_version"]:
        fail("SOURCE_CONTRACT_INVALID", str(validated["contract_version"]))
    if validated["coverage"]["required_axes"] != contract["required_axes"]:
        fail("SOURCE_AXES_INVALID", "required_axes")
    return validated


def authority_boundary() -> dict:
    return {
        "minimum_coverage_gate_ratified": True,
        "freshness_policy_ratified": False,
        "classification_authorized": False,
        "neutral_fallback_authorized": False,
        "thresholds_authorized": False,
        "weights_authorized": False,
        "regime_score_authorized": False,
        "strategy_eligibility_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def expected_gate(source: dict, contract: dict) -> dict:
    coverage = source["coverage"]
    defined = list(coverage["defined_axes"])
    missing = list(coverage["missing_axes"])
    minimum_met = len(defined) == contract["minimum_defined_axes"] and not missing
    reasons = []
    if not minimum_met:
        reasons.append(contract["minimum_not_met_reason_code"])
        reasons.extend(
            contract["axis_missing_reason_codes"][axis]
            for axis in contract["required_axes"]
            if axis in missing
        )
    reasons.extend(contract["downstream_blocker_reason_codes"])
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "source_contract_version": contract["source_contract_version"],
        "market": source["market"],
        "source_generated_at": source["generated_at"],
        "source_output_sha256": source_sha256(source),
        "policy_status": contract["policy_status"],
        "policy_name": contract["policy_name"],
        "gate_result": "COVERAGE_MET" if minimum_met else "BLOCKED",
        "minimum_coverage_met": minimum_met,
        "classification_eligible": False,
        "minimum_defined_axes": contract["minimum_defined_axes"],
        "coverage": {
            "required_axes": list(contract["required_axes"]),
            "defined_axes": defined,
            "missing_axes": missing,
            "defined_count": len(defined),
            "required_count": len(contract["required_axes"]),
            "ratio": f"{len(defined)}/{len(contract['required_axes'])}",
        },
        "reasons": reasons,
        "regime": contract["fail_closed_regime"],
        "direction": contract["fail_closed_direction"],
        "confidence": None,
        "authority": authority_boundary(),
    }


def evaluate_minimum_coverage(
    source: object,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    validated = validate_source(source, contract)
    gate = expected_gate(validated, contract)
    validate_gate(gate, validated, contract)
    return gate


def validate_gate(
    gate: object,
    source: object,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    validated_source = validate_source(source, contract)
    expected = expected_gate(validated_source, contract)
    if not isinstance(gate, dict):
        fail("GATE_INVALID", "object required")
    if type(gate.get("schema_version")) is not int:
        fail("GATE_INVALID", "integer schema_version required")
    if canonical_bytes(gate) != canonical_bytes(expected):
        fail("GATE_DERIVATION_MISMATCH", "gate is not source-derived")
    return gate


def write_gate(gate: dict, target: Path) -> Path:
    return OUTPUT.write_output(gate, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("source", type=Path)
    evaluate.add_argument("--out", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("gate", type=Path)
    validate.add_argument("--source", type=Path, required=True)

    args = parser.parse_args(argv)
    source = load_json(args.source)
    if args.command == "validate":
        gate = validate_gate(load_json(args.gate), source)
        print(json.dumps(gate, ensure_ascii=False, sort_keys=False))
        return 0

    gate = evaluate_minimum_coverage(source)
    if args.out:
        print(write_gate(gate, args.out))
    else:
        print(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (MinimumCoverageError, OUTPUT.OutputContractError) as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
