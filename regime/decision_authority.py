#!/usr/bin/env python3
"""Validate the P1-COM-05 Regime decision-authority boundary.

The repository has a ratified five-of-five coverage gate, but it does not have
an approved normalization, freshness, aggregation, classification, direction,
confidence, stress, invalidation, or hysteresis policy.  This module binds the
two existing source packets and deterministically keeps classification closed.

It additionally implements the already-merged ratified PAPER baseline v1 common
aggregation values (``common_v1_alignment`` in
``config/regime_source_owner_registry_v2.json``) as a hash-bound, replay-only
policy.  That path consumes already-signed axis directions, never market-
specific normalization inputs, and produces PIT replay packets for
bull/bear/sideways/stress sequences.  It does not change the fail-closed
runtime decision above, does not accept PIT replay, and opens no authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
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


# ---------------------------------------------------------------------------
# Ratified PAPER baseline v1 common aggregation — hash-bound, replay only.
# ---------------------------------------------------------------------------

REGISTRY_PATH = ROOT / "config" / "regime_source_owner_registry_v2.json"
PAPER_BASELINE_POLICY_PATH = (
    ROOT / "config" / "paper_regime_reference_policy_v1.json"
)

REGISTRY_VERSION = "regime_source_owner_registry/v2"
COMMON_V1_DECISION_IDENTITY = "CIO-GATE2-3MARKET-REGIME-SOURCE-FIRST-B-2026-09-01"
COMMON_V1_DECISION_PACKET_SHA256 = (
    "bdeb9b9970c71d38a9650f2374b9078e1f76ef4eeddf5acb34c6a890e9b7591c"
)
COMMON_V1_REPLAY_CONTRACT_VERSION = "regime_common_aggregation_replay/v1"
COMMON_V1_REPLAY_MODE = "SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED"
COMMON_V1_DETERMINISM_STATUS = "DETERMINISM_VERIFIED_COMMON_AGGREGATION_V1"
COMMON_V1_PIT_REPLAY_ACCEPTANCE = "NOT_ACCEPTED"
COMMON_V1_MARKET_KILL_STRESS_STATUS = "UNRATIFIED_NOT_IMPLEMENTED"

COMMON_V1_AXIS_DIRECTIONS = ("POSITIVE", "NEUTRAL", "NEGATIVE")
COMMON_V1_STRESS_AXIS = "RISK_VOL"
COMMON_V1_STRESS_DIRECTION = "STRESS"
COMMON_V1_STRESS_NORMALIZED_AS = "NEGATIVE"
COMMON_V1_COUNT_WORDS = {2: "TWO"}

PACKET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Verbatim transcription of config/regime_source_owner_registry_v2.json
# ``common_v1_alignment``.  Nothing here is authored by this module; drift
# between the registry and this block fails closed.
RATIFIED_COMMON_V1 = {
    "policy_status": "RATIFIED_PAPER_BASELINE_V1",
    "repository_v2_alignment_status": "ALIGNED_ARCHITECTURE_ONLY_RUNTIME_NOT_WIRED",
    "legacy_runtime_contract": {
        "path": "config/regime_decision_authority_contract.json",
        "contract_version": "regime_decision_authority/v1",
        "sha256": "de5e9d6bd36766af914c4491f12938fce7c89aa7882f3c9f1dc39b7029ae29f3",
        "status": "UNCHANGED_FAIL_CLOSED",
    },
    "required_axes": [
        "TREND",
        "BREADTH",
        "RISK_VOL",
        "LIQUIDITY",
        "LEADERSHIP",
    ],
    "normalized_axis_values": {
        "POSITIVE": 1,
        "NEUTRAL": 0,
        "NEGATIVE": -1,
    },
    "weights": {
        "TREND": 1,
        "BREADTH": 1,
        "RISK_VOL": 1,
        "LIQUIDITY": 1,
        "LEADERSHIP": 1,
    },
    "classification": {
        "RISK_ON": "S>=3",
        "NEUTRAL": "-2<=S<=2",
        "RISK_OFF": "S<=-3",
        "STRESS": "RISK_VOL_STRESS_OR_RATIFIED_MARKET_KILL_STRESS_CONDITION",
        "UNKNOWN": (
            "COVERAGE_OR_FRESHNESS_OR_POLICY_OR_LINEAGE_OR_INVALIDATION_FAILURE"
        ),
    },
    "direction": {
        "IMPROVING": "DELTA_S>=2",
        "STABLE": "-1<=DELTA_S<=1",
        "DETERIORATING": "DELTA_S<=-2",
        "UNKNOWN": "NO_COMPARABLE_PRIOR_FINALIZED_PACKET",
    },
    "confidence": {
        "RISK_ON": "POSITIVE_AXIS_COUNT_DIV_5",
        "RISK_OFF": "NEGATIVE_AXIS_COUNT_DIV_5",
        "NEUTRAL": "NEUTRAL_AXIS_COUNT_DIV_5",
        "STRESS": "EXPLICIT_STRESS_OVERRIDE_1_DIV_1",
        "UNKNOWN": None,
    },
    "hysteresis": {
        "ordinary_transition_finalized_packets": 2,
        "stress_entry": "IMMEDIATE",
        "unknown_on_coverage_freshness_invalidation_failure": "IMMEDIATE",
        "stress_exit": "TWO_CONSECUTIVE_NON_STRESS_AND_S_GREATER_THAN_NEGATIVE_3",
    },
    "market_specific_normalization_freshness_and_replay_inherited": False,
}

# Numeric form of the ratified expressions above.  Every value is re-checked
# against the ratified expression strings in ``derive_common_v1_thresholds``.
COMMON_V1_THRESHOLDS = {
    "risk_on_min_score": 3,
    "neutral_min_score": -2,
    "neutral_max_score": 2,
    "risk_off_max_score": -3,
    "improving_min_delta": 2,
    "stable_min_delta": -1,
    "stable_max_delta": 1,
    "deteriorating_max_delta": -2,
    "stress_exit_min_exclusive_score": -3,
    "ordinary_transition_finalized_packets": 2,
    "confidence_denominator": 5,
}


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("SOURCE_FILE_MISSING", f"{path}: {exc}")


def reject_float(value: object, label: str = "sequence") -> None:
    if isinstance(value, (float, Decimal)):
        fail("FLOAT_NOT_ALLOWED", label)
    if isinstance(value, dict):
        for key, item in value.items():
            reject_float(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_float(item, f"{label}[{index}]")


def derive_common_v1_thresholds(alignment: dict) -> dict:
    """Re-derive numeric bands from the ratified expression strings."""
    thresholds = dict(COMMON_V1_THRESHOLDS)
    classification = alignment["classification"]
    direction = alignment["direction"]
    hysteresis = alignment["hysteresis"]
    required = thresholds["ordinary_transition_finalized_packets"]
    expected = [
        (classification["RISK_ON"], f"S>={thresholds['risk_on_min_score']}"),
        (
            classification["NEUTRAL"],
            f"{thresholds['neutral_min_score']}<=S<={thresholds['neutral_max_score']}",
        ),
        (classification["RISK_OFF"], f"S<={thresholds['risk_off_max_score']}"),
        (direction["IMPROVING"], f"DELTA_S>={thresholds['improving_min_delta']}"),
        (
            direction["STABLE"],
            f"{thresholds['stable_min_delta']}<=DELTA_S"
            f"<={thresholds['stable_max_delta']}",
        ),
        (
            direction["DETERIORATING"],
            f"DELTA_S<={thresholds['deteriorating_max_delta']}",
        ),
        (
            hysteresis["stress_exit"],
            f"{COMMON_V1_COUNT_WORDS.get(required)}_CONSECUTIVE_NON_STRESS_AND_S"
            f"_GREATER_THAN_NEGATIVE"
            f"_{abs(thresholds['stress_exit_min_exclusive_score'])}",
        ),
    ]
    for ratified, derived in expected:
        if ratified != derived:
            fail("COMMON_V1_THRESHOLD_DERIVATION_MISMATCH", str(ratified))
    if hysteresis["ordinary_transition_finalized_packets"] != required:
        fail("COMMON_V1_THRESHOLD_DERIVATION_MISMATCH", "confirmation count")
    denominator = thresholds["confidence_denominator"]
    if denominator != len(alignment["required_axes"]):
        fail("COMMON_V1_THRESHOLD_DERIVATION_MISMATCH", "confidence denominator")
    for regime in ("RISK_ON", "RISK_OFF", "NEUTRAL"):
        if not alignment["confidence"][regime].endswith(f"_DIV_{denominator}"):
            fail("COMMON_V1_THRESHOLD_DERIVATION_MISMATCH", f"confidence {regime}")
    if thresholds["risk_on_min_score"] != thresholds["neutral_max_score"] + 1:
        fail("COMMON_V1_THRESHOLD_DERIVATION_MISMATCH", "risk_on band")
    if thresholds["risk_off_max_score"] != thresholds["neutral_min_score"] - 1:
        fail("COMMON_V1_THRESHOLD_DERIVATION_MISMATCH", "risk_off band")
    return thresholds


def cross_check_paper_baseline(path: Path, thresholds: dict) -> dict:
    """Confirm the merged PAPER baseline packet carries the same v1 numbers."""
    paper = load_json(path)
    if (
        not isinstance(paper, dict)
        or paper.get("contract_version") != "paper_regime_reference_policy/v1"
    ):
        fail("PAPER_BASELINE_INVALID", str(path))
    aggregation = paper.get("aggregation")
    axis_values = paper.get("axis_values")
    if (
        not isinstance(aggregation, dict)
        or not isinstance(axis_values, dict)
        or not isinstance(paper.get("status"), str)
    ):
        fail("PAPER_BASELINE_INVALID", "aggregation, axis_values, or status")
    expected = {
        "RISK_ON_MIN_SCORE": thresholds["risk_on_min_score"],
        "RISK_OFF_MAX_SCORE": thresholds["risk_off_max_score"],
        "NEUTRAL_MIN_SCORE": thresholds["neutral_min_score"],
        "NEUTRAL_MAX_SCORE": thresholds["neutral_max_score"],
        "stress_overrides_score": True,
    }
    if any(aggregation.get(key) != value for key, value in expected.items()):
        fail("PAPER_BASELINE_THRESHOLD_MISMATCH", "aggregation")
    if axis_values != RATIFIED_COMMON_V1["normalized_axis_values"]:
        fail("PAPER_BASELINE_THRESHOLD_MISMATCH", "axis_values")
    if paper.get("required_axes") != RATIFIED_COMMON_V1["required_axes"]:
        fail("PAPER_BASELINE_THRESHOLD_MISMATCH", "required_axes")
    return paper


def load_common_v1_policy(
    registry_path: Path = REGISTRY_PATH,
    paper_policy_path: Path = PAPER_BASELINE_POLICY_PATH,
    contract_path: Path = CONTRACT_PATH,
) -> dict:
    registry = load_json(registry_path)
    if not isinstance(registry, dict):
        fail("REGISTRY_INVALID", "object required")
    if registry.get("registry_version") != REGISTRY_VERSION:
        fail("REGISTRY_INVALID", str(registry.get("registry_version")))
    decision = registry.get("decision")
    if not isinstance(decision, dict):
        fail("REGISTRY_INVALID", "decision")
    if (
        decision.get("identity") != COMMON_V1_DECISION_IDENTITY
        or decision.get("packet_sha256") != COMMON_V1_DECISION_PACKET_SHA256
    ):
        fail("RATIFIED_DECISION_BINDING_INVALID", str(decision.get("identity")))
    alignment = registry.get("common_v1_alignment")
    if canonical_bytes(alignment) != canonical_bytes(RATIFIED_COMMON_V1):
        fail("COMMON_V1_ALIGNMENT_MISMATCH", "registry is not the ratified v1 set")
    legacy = alignment["legacy_runtime_contract"]
    if legacy["sha256"] != file_sha256(contract_path):
        fail("LEGACY_CONTRACT_HASH_MISMATCH", legacy["path"])
    contract = load_contract(contract_path)
    thresholds = derive_common_v1_thresholds(alignment)
    paper = cross_check_paper_baseline(paper_policy_path, thresholds)
    return {
        "policy_status": alignment["policy_status"],
        "contract_version": COMMON_V1_REPLAY_CONTRACT_VERSION,
        "contract_mode": COMMON_V1_REPLAY_MODE,
        "markets": list(OUTPUT.load_contract()["markets"]),
        "required_axes": list(alignment["required_axes"]),
        "normalized_axis_values": dict(alignment["normalized_axis_values"]),
        "weights": dict(alignment["weights"]),
        "thresholds": thresholds,
        "hysteresis": dict(alignment["hysteresis"]),
        "market_specific_normalization_inherited": alignment[
            "market_specific_normalization_freshness_and_replay_inherited"
        ],
        "market_kill_stress_condition_status": COMMON_V1_MARKET_KILL_STRESS_STATUS,
        "coverage_policy_name": contract["coverage_policy_name"],
        "coverage_failure_reason_code": "MINIMUM_COVERAGE_NOT_MET",
        "fail_closed_regime": contract["fail_closed_regime"],
        "fail_closed_direction": contract["fail_closed_direction"],
        "neutral_unknown_invariant": contract["neutral_unknown_invariant"],
        "pit_replay_acceptance": COMMON_V1_PIT_REPLAY_ACCEPTANCE,
        "binding": {
            "registry_path": "config/regime_source_owner_registry_v2.json",
            "registry_version": REGISTRY_VERSION,
            "decision_identity": COMMON_V1_DECISION_IDENTITY,
            "decision_packet_sha256": COMMON_V1_DECISION_PACKET_SHA256,
            "common_v1_alignment_sha256": payload_sha256(alignment),
            "legacy_runtime_contract_path": legacy["path"],
            "legacy_runtime_contract_sha256": legacy["sha256"],
            "paper_baseline_policy_path": (
                "config/paper_regime_reference_policy_v1.json"
            ),
            "paper_baseline_policy_sha256": file_sha256(paper_policy_path),
            "paper_baseline_policy_status": paper["status"],
        },
    }


def common_v1_authority() -> dict:
    return {
        "common_aggregation_replay_authorized": True,
        "market_signed_normalization_authorized": False,
        "freshness_policy_authorized": False,
        "pit_replay_acceptance_authorized": False,
        "runtime_classification_authorized": False,
        "runtime_binding_authorized": False,
        "regime_result_ratification_authorized": False,
        "threshold_override_authorized": False,
        "hysteresis_runtime_authorized": False,
        "strategy_eligibility_authorized": False,
        "stage_authorized": False,
        "buy_authorized": False,
        "action_authorized": False,
        "order_authorized": False,
        "capital_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }


def validate_common_v1_sequence(sequence: object, policy: dict) -> dict:
    if not isinstance(sequence, dict):
        fail("SEQUENCE_INVALID", "object required")
    reject_float(sequence)
    if set(sequence) != {"schema_version", "market", "case_id", "steps"}:
        fail("SEQUENCE_INVALID", "schema")
    if type(sequence["schema_version"]) is not int or sequence["schema_version"] != 1:
        fail("SEQUENCE_INVALID", "schema_version")
    market = sequence["market"]
    if market not in policy["markets"]:
        fail("MARKET_INVALID", str(market))
    case_id = sequence["case_id"]
    if not isinstance(case_id, str) or PACKET_ID.fullmatch(case_id) is None:
        fail("SEQUENCE_INVALID", "case_id")
    steps = sequence["steps"]
    if not isinstance(steps, list) or not steps:
        fail("SEQUENCE_INVALID", "steps")

    axes = policy["required_axes"]
    packet_ids = []
    previous_date = None
    normalized = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {
            "packet_id",
            "as_of_date",
            "axes",
        }:
            fail("STEP_INVALID", f"step {index} schema")
        packet_id = step["packet_id"]
        if not isinstance(packet_id, str) or PACKET_ID.fullmatch(packet_id) is None:
            fail("STEP_INVALID", f"step {index} packet_id")
        as_of_date = step["as_of_date"]
        if not isinstance(as_of_date, str) or ISO_DATE.fullmatch(as_of_date) is None:
            fail("STEP_INVALID", f"step {index} as_of_date")
        try:
            parsed = dt.date.fromisoformat(as_of_date)
        except ValueError:
            fail("STEP_INVALID", f"step {index} as_of_date")
        if previous_date is not None and parsed <= previous_date:
            fail("PIT_ORDER_INVALID", f"{as_of_date} <= {previous_date.isoformat()}")
        previous_date = parsed
        packet_ids.append(packet_id)

        step_axes = step["axes"]
        if not isinstance(step_axes, dict) or set(step_axes) != set(axes):
            fail("STEP_INVALID", f"step {index} axes")
        rows = {}
        for axis in axes:
            row = step_axes[axis]
            if not isinstance(row, dict) or set(row) != {"status", "direction"}:
                fail("STEP_INVALID", f"step {index} {axis} fields")
            status = row["status"]
            direction = row["direction"]
            if status == "UNDEFINED":
                if direction is not None:
                    fail("STEP_INVALID", f"step {index} {axis} undefined direction")
            elif status == "DEFINED":
                allowed = set(COMMON_V1_AXIS_DIRECTIONS)
                if axis == COMMON_V1_STRESS_AXIS:
                    allowed.add(COMMON_V1_STRESS_DIRECTION)
                if direction not in allowed:
                    fail("STEP_INVALID", f"step {index} {axis} direction")
            else:
                fail("STEP_INVALID", f"step {index} {axis} status")
            rows[axis] = {"axis": axis, "status": status, "direction": direction}
        normalized.append(
            {
                "packet_id": packet_id,
                "as_of_date": as_of_date,
                "axes": rows,
            }
        )
    if len(set(packet_ids)) != len(packet_ids):
        fail("SEQUENCE_INVALID", "duplicate packet_id")
    return {
        "schema_version": 1,
        "market": market,
        "case_id": case_id,
        "steps": normalized,
    }


def common_v1_signed_value(policy: dict, direction: str) -> int:
    if direction == COMMON_V1_STRESS_DIRECTION:
        direction = COMMON_V1_STRESS_NORMALIZED_AS
    return policy["normalized_axis_values"][direction]


def common_v1_band(score: int, policy: dict) -> str:
    thresholds = policy["thresholds"]
    if score >= thresholds["risk_on_min_score"]:
        return "RISK_ON"
    if score <= thresholds["risk_off_max_score"]:
        return "RISK_OFF"
    if thresholds["neutral_min_score"] <= score <= thresholds["neutral_max_score"]:
        return "NEUTRAL"
    fail("CLASSIFICATION_BAND_GAP", str(score))


def common_v1_direction(delta: int, policy: dict) -> str:
    thresholds = policy["thresholds"]
    if delta >= thresholds["improving_min_delta"]:
        return "IMPROVING"
    if delta <= thresholds["deteriorating_max_delta"]:
        return "DETERIORATING"
    if thresholds["stable_min_delta"] <= delta <= thresholds["stable_max_delta"]:
        return "STABLE"
    fail("DIRECTION_BAND_GAP", str(delta))


def common_v1_confidence(regime: str, counts: dict, policy: dict) -> Optional[str]:
    if regime == "STRESS":
        return "1"
    axis_direction = {
        "RISK_ON": "POSITIVE",
        "RISK_OFF": "NEGATIVE",
        "NEUTRAL": "NEUTRAL",
    }.get(regime)
    if axis_direction is None:
        return None
    denominator = policy["thresholds"]["confidence_denominator"]
    return str(Decimal(counts[axis_direction]) / Decimal(denominator))


def replay_common_v1(
    sequence: object,
    policy: Optional[dict] = None,
) -> dict:
    policy = load_common_v1_policy() if policy is None else policy
    validated = validate_common_v1_sequence(sequence, policy)
    axes = policy["required_axes"]
    weights = policy["weights"]
    thresholds = policy["thresholds"]
    required = thresholds["ordinary_transition_finalized_packets"]

    confirmed = policy["fail_closed_regime"]
    pending_regime = None
    pending_count = 0
    stress_exit_streak = 0
    previous_score = None
    rows = []
    for step in validated["steps"]:
        defined = [
            axis for axis in axes if step["axes"][axis]["status"] == "DEFINED"
        ]
        missing = [axis for axis in axes if axis not in defined]
        counts = {direction: 0 for direction in COMMON_V1_AXIS_DIRECTIONS}
        score = None
        stress = False
        reasons = []
        if missing:
            raw = policy["fail_closed_regime"]
            reasons.append(policy["coverage_failure_reason_code"])
            reasons.extend(f"{axis}_UNDEFINED" for axis in missing)
        else:
            score = 0
            for axis in axes:
                direction = step["axes"][axis]["direction"]
                if direction == COMMON_V1_STRESS_DIRECTION:
                    stress = True
                    counts[COMMON_V1_STRESS_NORMALIZED_AS] += 1
                else:
                    counts[direction] += 1
                score += weights[axis] * common_v1_signed_value(policy, direction)
            raw = "STRESS" if stress else common_v1_band(score, policy)

        previous_confirmed = confirmed
        if raw == policy["fail_closed_regime"]:
            confirmed = policy["fail_closed_regime"]
            pending_regime = None
            pending_count = 0
            stress_exit_streak = 0
            hysteresis_rule = "UNKNOWN_IMMEDIATE"
        elif raw == "STRESS":
            confirmed = "STRESS"
            pending_regime = "STRESS"
            pending_count = 1
            stress_exit_streak = 0
            hysteresis_rule = "STRESS_ENTRY_IMMEDIATE"
        else:
            if pending_regime == raw:
                pending_count += 1
            else:
                pending_regime = raw
                pending_count = 1
            if score > thresholds["stress_exit_min_exclusive_score"]:
                stress_exit_streak += 1
            else:
                stress_exit_streak = 0
            stress_exit_ok = (
                previous_confirmed != "STRESS" or stress_exit_streak >= required
            )
            if pending_count >= required and stress_exit_ok:
                confirmed = raw
                hysteresis_rule = "ORDINARY_CONFIRMATION_MET"
            elif previous_confirmed == "STRESS":
                hysteresis_rule = "STRESS_EXIT_PENDING"
            else:
                hysteresis_rule = "ORDINARY_CONFIRMATION_PENDING"

        if score is None or previous_score is None:
            delta = None
            direction_value = policy["fail_closed_direction"]
        else:
            delta = score - previous_score
            direction_value = common_v1_direction(delta, policy)
        previous_score = score

        rows.append(
            {
                "packet_id": step["packet_id"],
                "as_of_date": step["as_of_date"],
                "coverage": {
                    "required_axes": list(axes),
                    "defined_axes": defined,
                    "missing_axes": missing,
                    "defined_count": len(defined),
                    "required_count": len(axes),
                    "ratio": f"{len(defined)}/{len(axes)}",
                    "policy_name": policy["coverage_policy_name"],
                    "minimum_coverage_met": not missing,
                },
                "axis_directions": {
                    axis: step["axes"][axis]["direction"] for axis in axes
                },
                "signed_axis_counts": counts,
                "score": score,
                "stress_override": stress,
                "raw_classification": raw,
                "previous_confirmed_regime": previous_confirmed,
                "confirmed_regime": confirmed,
                "regime_changed": confirmed != previous_confirmed,
                "hysteresis": {
                    "rule": hysteresis_rule,
                    "confirmation_required": required,
                    "confirmation_count": pending_count,
                    "pending_regime": pending_regime,
                    "stress_exit_qualified_streak": stress_exit_streak,
                },
                "delta_score": delta,
                "direction": direction_value,
                "confidence": common_v1_confidence(confirmed, counts, policy),
                "reasons": reasons,
            }
        )

    return {
        "schema_version": 1,
        "contract_version": policy["contract_version"],
        "contract_mode": policy["contract_mode"],
        "policy_status": policy["policy_status"],
        "market": validated["market"],
        "case_id": validated["case_id"],
        "policy_binding": dict(policy["binding"]),
        "source_sequence_sha256": payload_sha256(validated),
        "required_axes": list(axes),
        "weights": dict(weights),
        "thresholds": dict(thresholds),
        "hysteresis_policy": dict(policy["hysteresis"]),
        "market_specific_signed_normalization_inherited": policy[
            "market_specific_normalization_inherited"
        ],
        "market_kill_stress_condition_status": policy[
            "market_kill_stress_condition_status"
        ],
        "neutral_unknown_invariant": policy["neutral_unknown_invariant"],
        "step_count": len(rows),
        "steps": rows,
        "final_regime": rows[-1]["confirmed_regime"],
        "final_direction": rows[-1]["direction"],
        "final_confidence": rows[-1]["confidence"],
        "determinism_status": COMMON_V1_DETERMINISM_STATUS,
        "pit_replay_acceptance": policy["pit_replay_acceptance"],
        "authority": common_v1_authority(),
    }


def validate_common_v1_replay(
    report: object,
    sequence: object,
    policy: Optional[dict] = None,
) -> dict:
    expected = replay_common_v1(sequence, policy)
    if not isinstance(report, dict):
        fail("COMMON_V1_REPLAY_INVALID", "object required")
    if canonical_bytes(report) != canonical_bytes(expected):
        fail(
            "COMMON_V1_REPLAY_DERIVATION_MISMATCH",
            "report is not sequence-derived",
        )
    return report


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
    replay = sub.add_parser("replay-common-v1")
    replay.add_argument("sequence", type=Path)
    replay.add_argument("--out", type=Path)
    validate_replay = sub.add_parser("validate-common-v1-replay")
    validate_replay.add_argument("report", type=Path)
    validate_replay.add_argument("--sequence", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "replay-common-v1":
        report = replay_common_v1(load_json(args.sequence))
        if args.out:
            print(OUTPUT.write_output(report, args.out))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False))
        return 0

    if args.command == "validate-common-v1-replay":
        report = validate_common_v1_replay(
            load_json(args.report),
            load_json(args.sequence),
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=False))
        return 0

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
