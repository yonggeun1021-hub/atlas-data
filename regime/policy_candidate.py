#!/usr/bin/env python3
"""Evaluate an explainable P1-COM-05 Regime policy candidate in Shadow only.

This module does not change ``regime_output/v1`` and cannot feed the runtime
Regime decision.  It binds five externally produced, evidence-linked axis
assessments to one validated pre-score Regime envelope and evaluates a pinned,
draft consensus policy for replay and CIO comparison.

The result is always ``DRAFT_NOT_RATIFIED``.  Stage, Buy, Action, Order,
Production, and trading authority remain false.
"""

from __future__ import annotations

import argparse
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


CONTRACT_PATH = ROOT / "config" / "regime_policy_candidate_contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[a-z][a-z0-9_/-]*/v[1-9][0-9]*$")
WARNING = re.compile(r"^[A-Z][A-Z0-9_]*$")


class PolicyCandidateError(RuntimeError):
    """Fail-closed draft policy-candidate contract violation."""


def fail(code: str, detail: str) -> None:
    raise PolicyCandidateError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_json(path: Path, code: str = "JSON_INVALID") -> object:
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, f"{path}: {exc}")


def ensure_no_float(value: object, label: str = "input") -> None:
    if isinstance(value, float):
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
    pinned = {
        "schema_version": 1,
        "contract_version": "regime_policy_candidate/v1",
        "contract_mode": "SHADOW_DIAGNOSTIC_ONLY",
        "source_contract_version": "regime_output/v1",
        "source_contract_mode": "PRE_SCORE_UNKNOWN_ONLY",
        "policy_id": "EXPLAINABLE_CONSENSUS_V1_DRAFT",
        "policy_status": "DRAFT_NOT_RATIFIED",
        "required_axes": [
            "TREND",
            "BREADTH",
            "RISK_VOL",
            "LIQUIDITY",
            "LEADERSHIP",
        ],
        "orientation_vocabulary": [
            "SUPPORTIVE",
            "NEUTRAL",
            "ADVERSE",
            "STRESS",
        ],
        "change_vocabulary": [
            "IMPROVING",
            "STABLE",
            "DETERIORATING",
        ],
        "candidate_regime_vocabulary": [
            "RISK_ON",
            "NEUTRAL",
            "RISK_OFF",
            "STRESS",
        ],
        "candidate_direction_vocabulary": [
            "IMPROVING",
            "STABLE",
            "DETERIORATING",
        ],
        "confidence_band_vocabulary": ["LOW", "MEDIUM", "HIGH"],
        "classification_policy": {
            "stress_rule": "risk_vol_stress_and_at_least_one_other_adverse",
            "risk_off_rule": "at_least_three_adverse_or_stress_axes",
            "risk_on_rule": (
                "at_least_three_supportive_and_zero_adverse_or_stress_axes"
            ),
            "fallback_rule": "neutral",
        },
        "direction_policy": {
            "improving_rule": (
                "at_least_three_improving_and_zero_deteriorating_axes"
            ),
            "deteriorating_rule": (
                "at_least_three_deteriorating_and_zero_improving_axes"
            ),
            "fallback_rule": "stable",
        },
        "confidence_policy": {
            "high_consensus_count": 4,
            "medium_consensus_count": 3,
            "neutral_high_rule": "at_least_four_neutral_axes",
            "neutral_medium_rule": "at_least_three_neutral_axes",
            "fallback_band": "LOW",
        },
        "stress_axis_policy": "STRESS_ORIENTATION_ALLOWED_ONLY_FOR_RISK_VOL",
        "source_binding_policy": (
            "axis_assessment_must_bind_exact_factor_evidence_sha256"
        ),
        "minimum_coverage_policy": "ALL_REQUIRED_AXES_DEFINED_5_OF_5",
        "numeric_policy": "NO_FLOATS",
        "ratification_policy": (
            "candidate_output_cannot_self_ratify_or_feed_runtime_decision"
        ),
        "authority": {
            "diagnostic_candidate_evaluation_authorized": True,
            "policy_ratification_authorized": False,
            "runtime_classification_authorized": False,
            "hysteresis_authorized": False,
            "strategy_eligibility_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    if set(contract) != set(pinned) or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned draft semantics")
    return contract


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(load_json(path, "CONTRACT_INVALID"))


def warning_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        fail("WARNINGS_INVALID", label)
    if any(
        not isinstance(item, str) or WARNING.fullmatch(item) is None
        for item in value
    ):
        fail("WARNINGS_INVALID", label)
    if len(value) != len(set(value)):
        fail("WARNINGS_INVALID", f"{label}: duplicate")
    return sorted(value)


def validate_source(source: object, contract: dict) -> dict:
    ensure_no_float(source, "regime_output")
    try:
        validated = OUTPUT.validate_output(source)
    except OUTPUT.OutputContractError as exc:
        fail("REGIME_OUTPUT_INVALID", str(exc))
    if validated["contract_version"] != contract["source_contract_version"]:
        fail("SOURCE_CONTRACT_INVALID", str(validated["contract_version"]))
    if validated["contract_mode"] != contract["source_contract_mode"]:
        fail("SOURCE_MODE_INVALID", str(validated["contract_mode"]))
    if validated["coverage"]["required_axes"] != contract["required_axes"]:
        fail("SOURCE_AXES_INVALID", "required axes")
    if validated["coverage"]["defined_axes"] != contract["required_axes"]:
        fail("SOURCE_COVERAGE_INCOMPLETE", validated["coverage"]["ratio"])
    if validated["coverage"]["missing_axes"]:
        fail("SOURCE_COVERAGE_INCOMPLETE", "missing axes")
    for axis in contract["required_axes"]:
        factor = validated["factor_results"][axis]
        if factor["status"] != "DEFINED" or factor["evidence"] is None:
            fail("SOURCE_FACTOR_UNDEFINED", axis)
    return validated


def validate_axis_assessment(
    axis: str,
    value: object,
    source_factor: dict,
    contract: dict,
) -> dict:
    expected = {
        "axis",
        "orientation",
        "change",
        "normalization_version",
        "source_evidence_sha256",
        "warnings",
    }
    if not isinstance(value, dict) or set(value) != expected:
        fail("ASSESSMENT_INVALID", f"{axis}: schema")
    if value["axis"] != axis:
        fail("ASSESSMENT_INVALID", f"{axis}: identity")
    orientation = value["orientation"]
    if orientation not in contract["orientation_vocabulary"]:
        fail("ORIENTATION_INVALID", f"{axis}: {orientation}")
    if orientation == "STRESS" and axis != "RISK_VOL":
        fail("STRESS_AXIS_INVALID", axis)
    change = value["change"]
    if change not in contract["change_vocabulary"]:
        fail("CHANGE_INVALID", f"{axis}: {change}")
    version = value["normalization_version"]
    if not isinstance(version, str) or VERSION.fullmatch(version) is None:
        fail("NORMALIZATION_VERSION_INVALID", f"{axis}: {version}")
    source_sha = value["source_evidence_sha256"]
    if not isinstance(source_sha, str) or SHA256.fullmatch(source_sha) is None:
        fail("SOURCE_EVIDENCE_SHA_INVALID", axis)
    expected_sha = source_factor["evidence"]["sha256"]
    if source_sha != expected_sha:
        fail("SOURCE_EVIDENCE_BINDING_MISMATCH", axis)
    return {
        "axis": axis,
        "orientation": orientation,
        "change": change,
        "normalization_version": version,
        "source_evidence_sha256": source_sha,
        "warnings": warning_list(value["warnings"], f"{axis}.warnings"),
    }


def normalize_assessments(
    assessments: object,
    source: dict,
    contract: dict,
) -> dict:
    ensure_no_float(assessments, "axis_assessments")
    axes = contract["required_axes"]
    if not isinstance(assessments, dict) or set(assessments) != set(axes):
        keys = sorted(assessments) if isinstance(assessments, dict) else assessments
        fail("ASSESSMENT_SET_INVALID", str(keys))
    return {
        axis: validate_axis_assessment(
            axis,
            assessments[axis],
            source["factor_results"][axis],
            contract,
        )
        for axis in axes
    }


def count_values(assessments: dict, field: str, vocabulary: list[str]) -> dict:
    return {
        value: sum(
            assessment[field] == value for assessment in assessments.values()
        )
        for value in vocabulary
    }


def classify_candidate(assessments: dict, contract: dict) -> dict:
    orientation = count_values(
        assessments,
        "orientation",
        contract["orientation_vocabulary"],
    )
    change = count_values(
        assessments,
        "change",
        contract["change_vocabulary"],
    )
    adverse_like = orientation["ADVERSE"] + orientation["STRESS"]
    other_adverse = sum(
        assessments[axis]["orientation"] in {"ADVERSE", "STRESS"}
        for axis in contract["required_axes"]
        if axis != "RISK_VOL"
    )

    if (
        assessments["RISK_VOL"]["orientation"] == "STRESS"
        and other_adverse >= 1
    ):
        regime = "STRESS"
        classification_reason = "STRESS_OVERRIDE_RISK_VOL_PLUS_CONFIRMATION"
    elif adverse_like >= 3:
        regime = "RISK_OFF"
        classification_reason = "ADVERSE_CONSENSUS_AT_LEAST_3_OF_5"
    elif orientation["SUPPORTIVE"] >= 3 and adverse_like == 0:
        regime = "RISK_ON"
        classification_reason = "SUPPORTIVE_CONSENSUS_AT_LEAST_3_OF_5_NO_ADVERSE"
    else:
        regime = "NEUTRAL"
        classification_reason = "MIXED_OR_INSUFFICIENT_CONSENSUS"

    if change["IMPROVING"] >= 3 and change["DETERIORATING"] == 0:
        direction = "IMPROVING"
        direction_reason = "IMPROVING_CONSENSUS_AT_LEAST_3_OF_5"
    elif change["DETERIORATING"] >= 3 and change["IMPROVING"] == 0:
        direction = "DETERIORATING"
        direction_reason = "DETERIORATING_CONSENSUS_AT_LEAST_3_OF_5"
    else:
        direction = "STABLE"
        direction_reason = "MIXED_OR_INSUFFICIENT_CHANGE_CONSENSUS"

    if regime == "RISK_ON":
        consensus_count = orientation["SUPPORTIVE"]
    elif regime in {"RISK_OFF", "STRESS"}:
        consensus_count = adverse_like
    else:
        consensus_count = orientation["NEUTRAL"]
    if consensus_count >= contract["confidence_policy"]["high_consensus_count"]:
        confidence = "HIGH"
        confidence_reason = "CONSENSUS_AT_LEAST_4_OF_5"
    elif consensus_count >= contract["confidence_policy"]["medium_consensus_count"]:
        confidence = "MEDIUM"
        confidence_reason = "CONSENSUS_AT_LEAST_3_OF_5"
    else:
        confidence = contract["confidence_policy"]["fallback_band"]
        confidence_reason = "CONSENSUS_BELOW_3_OF_5"

    return {
        "orientation_counts": orientation,
        "adverse_or_stress_count": adverse_like,
        "other_axis_adverse_or_stress_count": other_adverse,
        "change_counts": change,
        "candidate": {
            "regime": regime,
            "direction": direction,
            "confidence_band": confidence,
            "classification_reason": classification_reason,
            "direction_reason": direction_reason,
            "confidence_reason": confidence_reason,
        },
    }


def expected_candidate(
    source: dict,
    assessments: dict,
    contract: dict,
) -> dict:
    classified = classify_candidate(assessments, contract)
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "contract_mode": contract["contract_mode"],
        "policy_id": contract["policy_id"],
        "policy_status": contract["policy_status"],
        "market": source["market"],
        "source_refs": {
            "regime_output_contract_version": source["contract_version"],
            "regime_output_generated_at": source["generated_at"],
            "regime_output_sha256": payload_sha256(source),
        },
        "axis_assessments": assessments,
        "summary": {
            "orientation_counts": classified["orientation_counts"],
            "adverse_or_stress_count": classified["adverse_or_stress_count"],
            "other_axis_adverse_or_stress_count": classified[
                "other_axis_adverse_or_stress_count"
            ],
            "change_counts": classified["change_counts"],
        },
        "candidate": classified["candidate"],
        "ratification": {
            "status": "NOT_RATIFIED",
            "runtime_eligible": False,
            "replay_required": True,
            "hysteresis_required": True,
            "allowed_downstream": ["POLICY_REPLAY", "CIO_COMPARISON"],
            "prohibited_downstream": [
                "RUNTIME_REGIME",
                "STRATEGY_ELIGIBILITY",
                "STAGE",
                "BUY",
                "ACTION",
                "ORDER",
                "PRODUCTION",
                "TRADING",
            ],
        },
        "authority": dict(contract["authority"]),
    }


def evaluate_policy_candidate(
    regime_output: object,
    axis_assessments: object,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    source = validate_source(regime_output, contract)
    assessments = normalize_assessments(axis_assessments, source, contract)
    candidate = expected_candidate(source, assessments, contract)
    return validate_candidate(candidate, source, assessments, contract)


def validate_candidate(
    candidate: object,
    regime_output: object,
    axis_assessments: object,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    source = validate_source(regime_output, contract)
    assessments = normalize_assessments(axis_assessments, source, contract)
    expected = expected_candidate(source, assessments, contract)
    if not isinstance(candidate, dict):
        fail("CANDIDATE_INVALID", "object required")
    if canonical_bytes(candidate) != canonical_bytes(expected):
        fail("CANDIDATE_DERIVATION_MISMATCH", "candidate != source-derived result")
    return candidate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("regime_output", type=Path)
    evaluate.add_argument("axis_assessments", type=Path)
    evaluate.add_argument("--out", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("candidate", type=Path)
    validate.add_argument("--regime-output", type=Path, required=True)
    validate.add_argument("--axis-assessments", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        source = load_json(args.regime_output, "REGIME_OUTPUT_INVALID")
        assessments = load_json(args.axis_assessments, "ASSESSMENTS_INVALID")
        if args.command == "validate":
            result = validate_candidate(
                load_json(args.candidate, "CANDIDATE_INVALID"),
                source,
                assessments,
            )
        else:
            result = evaluate_policy_candidate(source, assessments)
        if args.command == "evaluate" and args.out is not None:
            print(OUTPUT.write_output(result, args.out))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
        return 0
    except (PolicyCandidateError, OUTPUT.OutputContractError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
