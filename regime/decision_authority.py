#!/usr/bin/env python3
"""Validate the P1-COM-05 Regime decision-authority boundary.

The repository has a ratified five-of-five coverage gate, but it does not have
an approved normalization, freshness, aggregation, classification, direction,
confidence, stress, invalidation, or hysteresis policy.  This module binds the
two existing source packets and deterministically keeps classification closed.
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

from regime import minimum_coverage as COVERAGE  # noqa: E402
from regime import output_contract as OUTPUT  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "regime_decision_authority_contract.json"


class DecisionAuthorityError(RuntimeError):
    """Fail-closed Regime decision-authority contract violation."""


def fail(code: str, detail: str) -> None:
    raise DecisionAuthorityError(f"{code}: {detail}")


def load_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_INVALID", f"{path}: {exc}")


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


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate_contract(contract: object) -> dict:
    if not isinstance(contract, dict):
        fail("CONTRACT_INVALID", "object required")
    expected = {
        "schema_version",
        "contract_version",
        "contract_mode",
        "source_contract_versions",
        "required_axes",
        "coverage_policy_name",
        "repository_policy_registry_status",
        "required_policy_components",
        "policy_component_status",
        "policy_reason_codes",
        "allowed_decision_statuses",
        "fail_closed_regime",
        "fail_closed_direction",
        "confidence_policy",
        "neutral_unknown_invariant",
        "authority",
    }
    if set(contract) != expected or type(contract.get("schema_version")) is not int:
        fail("CONTRACT_INVALID", "schema or fields")
    pinned = {
        "schema_version": 1,
        "contract_version": "regime_decision_authority/v1",
        "contract_mode": "UNRATIFIED_CLASSIFICATION_GATE",
        "source_contract_versions": {
            "regime_output": "regime_output/v1",
            "minimum_coverage": "regime_minimum_coverage/v1",
        },
        "required_axes": [
            "TREND",
            "BREADTH",
            "RISK_VOL",
            "LIQUIDITY",
            "LEADERSHIP",
        ],
        "coverage_policy_name": "ALL_REQUIRED_AXES_5_OF_5",
        "repository_policy_registry_status": "ABSENT",
        "required_policy_components": [
            "FACTOR_NORMALIZATION",
            "FRESHNESS",
            "AGGREGATION_WEIGHTS",
            "CLASSIFICATION_THRESHOLDS",
            "DIRECTION",
            "CONFIDENCE",
            "STRESS_OVERRIDE",
            "INVALIDATION",
            "HYSTERESIS",
        ],
        "policy_component_status": {
            "FACTOR_NORMALIZATION": "UNRATIFIED",
            "FRESHNESS": "UNRATIFIED",
            "AGGREGATION_WEIGHTS": "ABSENT",
            "CLASSIFICATION_THRESHOLDS": "ABSENT",
            "DIRECTION": "UNRATIFIED",
            "CONFIDENCE": "UNRATIFIED",
            "STRESS_OVERRIDE": "UNRATIFIED",
            "INVALIDATION": "UNRATIFIED",
            "HYSTERESIS": "UNRATIFIED",
        },
        "policy_reason_codes": {
            "FACTOR_NORMALIZATION": "FACTOR_NORMALIZATION_POLICY_UNRATIFIED",
            "FRESHNESS": "FRESHNESS_POLICY_UNRATIFIED",
            "AGGREGATION_WEIGHTS": "AGGREGATION_WEIGHTS_ABSENT",
            "CLASSIFICATION_THRESHOLDS": "CLASSIFICATION_THRESHOLDS_ABSENT",
            "DIRECTION": "DIRECTION_POLICY_UNRATIFIED",
            "CONFIDENCE": "CONFIDENCE_POLICY_UNRATIFIED",
            "STRESS_OVERRIDE": "STRESS_OVERRIDE_POLICY_UNRATIFIED",
            "INVALIDATION": "INVALIDATION_POLICY_UNRATIFIED",
            "HYSTERESIS": "HYSTERESIS_POLICY_UNRATIFIED",
        },
        "allowed_decision_statuses": [
            "BLOCKED_COVERAGE",
            "BLOCKED_POLICY_UNRATIFIED",
        ],
        "fail_closed_regime": "UNKNOWN",
        "fail_closed_direction": "UNKNOWN",
        "confidence_policy": "null_until_policy_and_replay_ratified",
        "neutral_unknown_invariant": (
            "NEUTRAL_IS_OBSERVED_STATE_UNKNOWN_IS_INSUFFICIENT_OR_UNAUTHORIZED_EVIDENCE"
        ),
        "authority": {
            "decision_boundary_validation_authorized": True,
            "policy_ratification_authorized": False,
            "factor_normalization_authorized": False,
            "classification_authorized": False,
            "direction_authorized": False,
            "confidence_authorized": False,
            "stress_override_authorized": False,
            "hysteresis_authorized": False,
            "strategy_eligibility_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    if any(contract.get(key) != value for key, value in pinned.items()):
        fail("CONTRACT_INVALID", "pinned fail-closed semantics")
    if set(contract["policy_component_status"]) != set(
        contract["required_policy_components"]
    ) or set(contract["policy_reason_codes"]) != set(
        contract["required_policy_components"]
    ):
        fail("CONTRACT_INVALID", "policy component mismatch")
    return contract


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(load_json(path))


def validate_sources(
    regime_output: object,
    coverage_gate: object,
    contract: dict,
) -> tuple[dict, dict]:
    try:
        source = OUTPUT.validate_output(regime_output)
    except OUTPUT.OutputContractError as exc:
        fail("REGIME_OUTPUT_INVALID", str(exc))
    try:
        gate = COVERAGE.validate_gate(coverage_gate, source)
    except COVERAGE.MinimumCoverageError as exc:
        fail("COVERAGE_GATE_INVALID", str(exc))
    if source["contract_version"] != contract["source_contract_versions"]["regime_output"]:
        fail("SOURCE_CONTRACT_INVALID", "regime_output")
    if gate["contract_version"] != contract["source_contract_versions"]["minimum_coverage"]:
        fail("SOURCE_CONTRACT_INVALID", "minimum_coverage")
    if source["coverage"]["required_axes"] != contract["required_axes"]:
        fail("SOURCE_AXES_INVALID", "regime_output")
    if gate["policy_name"] != contract["coverage_policy_name"]:
        fail("COVERAGE_POLICY_INVALID", str(gate["policy_name"]))
    return source, gate


def expected_decision(source: dict, gate: dict, contract: dict) -> dict:
    coverage_met = gate["gate_result"] == "COVERAGE_MET"
    missing_components = list(contract["required_policy_components"])
    if coverage_met:
        status = "BLOCKED_POLICY_UNRATIFIED"
        reasons = [contract["policy_reason_codes"][key] for key in missing_components]
    else:
        status = "BLOCKED_COVERAGE"
        reasons = [
            reason
            for reason in gate["reasons"]
            if reason == "MINIMUM_COVERAGE_NOT_MET" or reason.endswith("_UNDEFINED")
        ]
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "contract_mode": contract["contract_mode"],
        "market": source["market"],
        "source_refs": {
            "regime_output_contract_version": source["contract_version"],
            "regime_output_generated_at": source["generated_at"],
            "regime_output_sha256": payload_sha256(source),
            "minimum_coverage_contract_version": gate["contract_version"],
            "minimum_coverage_sha256": payload_sha256(gate),
        },
        "coverage": {
            "policy_name": gate["policy_name"],
            "gate_result": gate["gate_result"],
            "minimum_coverage_met": gate["minimum_coverage_met"],
            "defined_axes": list(gate["coverage"]["defined_axes"]),
            "missing_axes": list(gate["coverage"]["missing_axes"]),
            "ratio": gate["coverage"]["ratio"],
        },
        "policy_gate": {
            "repository_policy_registry_status": contract[
                "repository_policy_registry_status"
            ],
            "required_components": list(contract["required_policy_components"]),
            "component_status": dict(contract["policy_component_status"]),
            "missing_components": missing_components,
            "classification_eligible": False,
            "replay_eligible": False,
        },
        "decision_status": status,
        "reasons": reasons,
        "regime": contract["fail_closed_regime"],
        "direction": contract["fail_closed_direction"],
        "confidence": None,
        "authority": dict(contract["authority"]),
    }


def evaluate_decision_authority(
    regime_output: object,
    coverage_gate: object,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    source, gate = validate_sources(regime_output, coverage_gate, contract)
    decision = expected_decision(source, gate, contract)
    return validate_decision(decision, source, gate, contract)


def validate_decision(
    decision: object,
    regime_output: object,
    coverage_gate: object,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    source, gate = validate_sources(regime_output, coverage_gate, contract)
    expected = expected_decision(source, gate, contract)
    if not isinstance(decision, dict):
        fail("DECISION_INVALID", "object required")
    if canonical_bytes(decision) != canonical_bytes(expected):
        fail("DECISION_DERIVATION_MISMATCH", "decision is not source-derived")
    return decision


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("regime_output", type=Path)
    evaluate.add_argument("coverage_gate", type=Path)
    evaluate.add_argument("--out", type=Path)
    validate = sub.add_parser("validate")
    validate.add_argument("decision", type=Path)
    validate.add_argument("--regime-output", type=Path, required=True)
    validate.add_argument("--coverage-gate", type=Path, required=True)
    args = parser.parse_args(argv)

    source = load_json(args.regime_output)
    gate = load_json(args.coverage_gate)
    if args.command == "validate":
        decision = validate_decision(load_json(args.decision), source, gate)
        print(json.dumps(decision, ensure_ascii=False, sort_keys=False))
        return 0

    decision = evaluate_decision_authority(source, gate)
    if args.out:
        print(OUTPUT.write_output(decision, args.out))
    else:
        print(json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        DecisionAuthorityError,
        OUTPUT.OutputContractError,
        COVERAGE.MinimumCoverageError,
    ) as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
