#!/usr/bin/env python3
"""Runtime Regime readiness — conveys unavailability, never a Regime.

This module answers exactly one question about the *runtime* path:

    given the real ``regime_output/v1`` envelopes the daily orchestrator
    already builds from live axis evidence, what precisely still blocks a
    final runtime Regime decision?

It checks structural consistency of supplied envelopes through existing
validators. It does not authenticate the evidence URI bytes itself; the owning
consumer must rebuild those inputs. The daily orchestrator does so through its
existing source builders and full packet re-derivation.

It chains only pre-existing validators:

1. ``regime/output_contract.py``   -- envelope validity (evidence presence)
2. ``regime/minimum_coverage.py``  -- ratified 5-of-5 coverage gate
3. ``regime/decision_authority.py::evaluate_decision_authority``
                                   -- the fail-closed runtime decision gate
4. ``regime/decision_authority.py::normalize_signed_axes``
                                   -- the market-specific signed-axis boundary

It authors no policy, no threshold, no weight, no TTL, and no PIT acceptance.
It never assigns a signed axis direction, a score, a classification, or a
direction, and it never emits a common-v1 replay step.

Two invariants are structural, not stylistic:

* ``runtime_decision_available`` is pinned ``False``.  There is no success
  value in this contract, exactly as ``regime_decision_authority/v1`` has no
  success ``decision_status``.
* ``historical_replay_is_not_runtime_ready`` is pinned ``True``.  The merged
  common-v1 replay path in ``decision_authority.py`` carries the contract mode
  ``SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED``; a complete replay mechanism is
  not runtime readiness and is not P1 WBS completion.

The output's purpose is to give the P6-06 ``P1_REGIME_DECISION`` slot exact,
machine-readable unavailable blockers instead of one opaque placeholder.  That
slot stays UNAVAILABLE either way.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import decision_authority as AUTHORITY  # noqa: E402
from regime import minimum_coverage as COVERAGE  # noqa: E402
from regime import output_contract as OUTPUT  # noqa: E402


CONTRACT_VERSION = "runtime_regime_readiness/v1"
CONTRACT_MODE = "RUNTIME_READINESS_ONLY_NO_REGIME_DECISION"
STATUS = "RUNTIME_REGIME_DECISION_UNAVAILABLE"
DECISION_STATUS = "BLOCKED"
FAIL_CLOSED_REGIME = "UNKNOWN"
FAIL_CLOSED_DIRECTION = "UNKNOWN"

REASON_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,159}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

NOT_RUNTIME_WIRED_REASON = "P1_REGIME_DECISION_NOT_RUNTIME_WIRED"
REPLAY_MODE_REASON_PREFIX = "COMMON_V1_REPLAY_MODE:"
POLICY_COMPONENT_REASON_PREFIX = "REGIME_POLICY_COMPONENT_MISSING:"
DECISION_BLOCKED_REASON_PREFIX = "DECISION_AUTHORITY_BLOCKED:"
SIGNED_NORMALIZATION_REASON_PREFIX = "SIGNED_NORMALIZATION_POLICY_UNRATIFIED:"
COVERAGE_REASON_PREFIX = "MINIMUM_COVERAGE_NOT_MET:"
AXIS_REASON_PREFIX = "AXIS_UNDEFINED:"
ACCEPTANCE_REASON_PREFIX = "MARKET_ACCEPTANCE_BLOCKED:"
PIT_REPLAY_REASON_PREFIX = "PIT_REPLAY_NOT_ACCEPTED:"


class RuntimeRegimeReadinessError(ValueError):
    """Fail-closed runtime Regime readiness violation."""


def fail(code: str, detail: str) -> None:
    raise RuntimeRegimeReadinessError(f"{code}:{detail}")


def canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_READ_FAILED", f"{path}:{exc}")


def _reason(value: str) -> str:
    if REASON_RE.fullmatch(value) is None:
        fail("REASON_CODE_INVALID", value)
    return value


def authority_boundary() -> dict:
    """Every runtime-meaningful authority stays false, by construction."""
    return {
        "readiness_inventory_only": True,
        "regime_classification_authorized": False,
        "regime_direction_authorized": False,
        "confidence_authorized": False,
        "market_signed_normalization_authorized": False,
        "freshness_policy_authorized": False,
        "ttl_ratification_authorized": False,
        "pit_replay_acceptance_authorized": False,
        "runtime_binding_authorized": False,
        "regime_result_ratification_authorized": False,
        "threshold_override_authorized": False,
        "strategy_eligibility_authorized": False,
        "stage_authorized": False,
        "buy_authorized": False,
        "action_authorized": False,
        "order_authorized": False,
        "capital_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }


def _market_row(
    market: str,
    envelope: dict,
    generated_at: str,
    coverage_contract: dict,
    authority_contract: dict,
    signed_policy: dict,
) -> tuple[dict, list[str]]:
    """Re-run the three existing gates for one market and report the truth."""
    try:
        source = OUTPUT.validate_output(envelope)
    except OUTPUT.OutputContractError as exc:
        fail("REGIME_OUTPUT_INVALID", f"{market}:{exc}")
    if source["market"] != market:
        fail("REGIME_OUTPUT_MARKET_MISMATCH", f"{market}:{source['market']}")
    if source["generated_at"] != generated_at:
        fail("REGIME_OUTPUT_GENERATED_AT_MISMATCH", market)

    try:
        gate = COVERAGE.evaluate_minimum_coverage(source, coverage_contract)
    except COVERAGE.MinimumCoverageError as exc:
        fail("COVERAGE_GATE_INVALID", f"{market}:{exc}")
    try:
        decision = AUTHORITY.evaluate_decision_authority(
            source, gate, authority_contract
        )
        signed = AUTHORITY.normalize_signed_axes(source, signed_policy)
    except AUTHORITY.DecisionAuthorityError as exc:
        fail("DECISION_AUTHORITY_INVALID", f"{market}:{exc}")

    # These are invariants of the merged upstream contracts.  If any of them
    # ever stops holding, this module must fail closed rather than quietly
    # emit a readiness packet that no longer describes the real boundary.
    if decision["regime"] != FAIL_CLOSED_REGIME or decision["direction"] != FAIL_CLOSED_DIRECTION:
        fail("UPSTREAM_DECISION_NOT_FAIL_CLOSED", market)
    if decision["confidence"] is not None or signed["confidence"] is not None:
        fail("UPSTREAM_CONFIDENCE_NOT_NULL", market)
    if decision["policy_gate"]["classification_eligible"] is not False:
        fail("UPSTREAM_CLASSIFICATION_ELIGIBLE", market)
    if signed["replay_step_emitted"] is not False or signed["common_v1_replay_step"] is not None:
        fail("UPSTREAM_REPLAY_STEP_EMITTED", market)
    signed_axes = signed["axes"]
    if any(
        signed_axes[axis]["signed_direction"] is not None
        or signed_axes[axis]["normalized_value"] is not None
        for axis in signed["required_axes"]
    ):
        fail("UPSTREAM_SIGNED_DIRECTION_PRESENT", market)

    binding = signed["market_binding"]
    coverage = signed["coverage"]
    missing_axes = list(coverage["missing_axes"])

    blockers = [
        _reason(f"{SIGNED_NORMALIZATION_REASON_PREFIX}{market}"),
        _reason(
            f"{DECISION_BLOCKED_REASON_PREFIX}{market}:"
            f"{decision['decision_status']}"
        ),
        _reason(f"{ACCEPTANCE_REASON_PREFIX}{market}:{binding['acceptance_status']}"),
        _reason(f"{PIT_REPLAY_REASON_PREFIX}{market}"),
    ]
    if not coverage["minimum_coverage_met"]:
        blockers.append(_reason(f"{COVERAGE_REASON_PREFIX}{market}"))
        blockers.extend(
            _reason(f"{AXIS_REASON_PREFIX}{market}:{axis}") for axis in missing_axes
        )

    row = {
        "market": market,
        "registry_market": signed["registry_market"],
        "coverage": {
            "policy_name": coverage["policy_name"],
            "defined_axes": list(coverage["defined_axes"]),
            "missing_axes": missing_axes,
            "defined_count": coverage["defined_count"],
            "required_count": coverage["required_count"],
            "ratio": coverage["ratio"],
            "minimum_coverage_met": coverage["minimum_coverage_met"],
            "gate_result": gate["gate_result"],
        },
        "decision_gate": {
            "contract_version": decision["contract_version"],
            "contract_mode": decision["contract_mode"],
            "decision_status": decision["decision_status"],
            "missing_policy_components": list(
                decision["policy_gate"]["missing_components"]
            ),
            "classification_eligible": decision["policy_gate"]["classification_eligible"],
            "replay_eligible": decision["policy_gate"]["replay_eligible"],
        },
        "signed_axis_gate": {
            "contract_version": signed["contract_version"],
            "contract_mode": signed["contract_mode"],
            "normalization_status": signed["normalization_status"],
            "signed_normalization_policy_status": binding[
                "signed_normalization_policy_status"
            ],
            "acceptance_status": binding["acceptance_status"],
            "pit_replay_acceptance": binding["pit_replay_acceptance"],
            "forbidden_promotion": binding["forbidden_promotion"],
            "replay_step_emitted": signed["replay_step_emitted"],
            "signed_directions": {
                axis: signed_axes[axis]["signed_direction"]
                for axis in signed["required_axes"]
            },
        },
        "regime": FAIL_CLOSED_REGIME,
        "direction": FAIL_CLOSED_DIRECTION,
        "confidence": None,
        "runtime_decision_available": False,
        "lineage": {
            "regime_output_sha256": decision["source_refs"]["regime_output_sha256"],
            "minimum_coverage_sha256": decision["source_refs"][
                "minimum_coverage_sha256"
            ],
            "signed_axis_packet_sha256": payload_sha256(signed),
        },
        "blockers": sorted(set(blockers)),
    }
    return row, blockers


def _assemble(regime_outputs, generated_at: str) -> dict:
    if not isinstance(generated_at, str):
        fail("GENERATED_AT_INVALID", str(generated_at))
    try:
        OUTPUT.parse_utc(generated_at, "generated_at")
        output_contract = OUTPUT.load_contract()
        coverage_contract = COVERAGE.load_contract()
        authority_contract = AUTHORITY.load_contract()
        signed_policy = AUTHORITY.load_signed_axis_policy()
    except (
        OUTPUT.OutputContractError,
        COVERAGE.MinimumCoverageError,
        AUTHORITY.DecisionAuthorityError,
    ) as exc:
        # Re-raised as this module's own ValueError so downstream consumers
        # need exactly one exception class to fail closed on.
        fail("UPSTREAM_CONTRACT_UNAVAILABLE", str(exc))

    markets = list(output_contract["markets"])
    if not isinstance(regime_outputs, dict) or set(regime_outputs) != set(markets):
        fail("REGIME_OUTPUT_KEYS_MISMATCH", str(sorted(regime_outputs))
             if isinstance(regime_outputs, dict) else "object required")

    common = signed_policy["common_v1"]

    if common["contract_mode"] != AUTHORITY.COMMON_V1_REPLAY_MODE:
        fail("COMMON_V1_MODE_UNEXPECTED", str(common["contract_mode"]))
    if common["pit_replay_acceptance"] != AUTHORITY.COMMON_V1_PIT_REPLAY_ACCEPTANCE:
        fail("COMMON_V1_PIT_ACCEPTANCE_UNEXPECTED", str(common["pit_replay_acceptance"]))

    global_blockers = [
        _reason(NOT_RUNTIME_WIRED_REASON),
        _reason(f"{REPLAY_MODE_REASON_PREFIX}{common['contract_mode']}"),
    ]
    global_blockers.extend(
        _reason(f"{POLICY_COMPONENT_REASON_PREFIX}{component}")
        for component in authority_contract["required_policy_components"]
    )

    rows = []
    blockers = list(global_blockers)
    for market in markets:
        row, market_blockers = _market_row(
            market,
            regime_outputs[market],
            generated_at,
            coverage_contract,
            authority_contract,
            signed_policy,
        )
        rows.append(row)
        blockers.extend(market_blockers)

    covered = [row["market"] for row in rows if row["coverage"]["minimum_coverage_met"]]
    return {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "contract_mode": CONTRACT_MODE,
        "generated_at": generated_at,
        "status": STATUS,
        "decision_status": DECISION_STATUS,
        "runtime_decision_available": False,
        "historical_replay_is_not_runtime_ready": True,
        "source_validation_scope": "STRUCTURAL_ENVELOPE_ONLY",
        "source_evidence_bytes_verified": False,
        "regime": FAIL_CLOSED_REGIME,
        "direction": FAIL_CLOSED_DIRECTION,
        "confidence": None,
        "final_decision": None,
        "neutral_unknown_invariant": common["neutral_unknown_invariant"],
        "common_v1_binding": {
            "policy_status": common["policy_status"],
            "contract_version": common["contract_version"],
            "contract_mode": common["contract_mode"],
            "pit_replay_acceptance": common["pit_replay_acceptance"],
            "market_kill_stress_condition_status": common[
                "market_kill_stress_condition_status"
            ],
            "market_specific_normalization_inherited": common[
                "market_specific_normalization_inherited"
            ],
            "registry_path": common["binding"]["registry_path"],
            "registry_version": common["binding"]["registry_version"],
            "decision_identity": common["binding"]["decision_identity"],
            "decision_packet_sha256": common["binding"]["decision_packet_sha256"],
            "common_v1_alignment_sha256": common["binding"][
                "common_v1_alignment_sha256"
            ],
            "forbidden_promotion": signed_policy["forbidden_promotion"],
        },
        "markets": rows,
        "summary": {
            "market_count": len(rows),
            "coverage_met_markets": covered,
            "coverage_met_market_count": len(covered),
            "runtime_ready_market_count": 0,
            "signed_normalization_ratified_market_count": 0,
        },
        "p1_regime_decision_unavailable_reasons": sorted(set(blockers)),
        "regime_outputs": copy.deepcopy(regime_outputs),
        "authority": authority_boundary(),
    }


def build_readiness(regime_outputs, generated_at: str) -> dict:
    """Derive structural readiness; callers own source-evidence verification."""
    packet = _assemble(regime_outputs, generated_at)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_readiness(packet)


def validate_readiness(packet) -> dict:
    """Re-derive the packet from its own embedded envelopes and compare bytes."""
    fields = {
        "schema_version", "contract_version", "contract_mode", "generated_at",
        "status", "decision_status", "runtime_decision_available",
        "historical_replay_is_not_runtime_ready", "source_validation_scope",
        "source_evidence_bytes_verified", "regime", "direction",
        "confidence", "final_decision", "neutral_unknown_invariant",
        "common_v1_binding", "markets", "summary",
        "p1_regime_decision_unavailable_reasons", "regime_outputs", "authority",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        fail("OUTPUT_FIELDS_MISMATCH", "readiness packet")
    expected = _assemble(packet.get("regime_outputs"), packet.get("generated_at"))
    actual = copy.deepcopy(packet)
    digest = actual.pop("packet_sha256", None)
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        fail("OUTPUT_PACKET_SHA_INVALID", str(digest))
    if canonical_json(actual) != canonical_json(expected):
        fail("OUTPUT_DERIVATION_MISMATCH", "packet is not source-derived")
    if payload_sha256(expected) != digest:
        fail("OUTPUT_PACKET_SHA_MISMATCH", digest)
    return copy.deepcopy(packet)


def unavailable_reasons(packet) -> list[str]:
    """Exact, sorted, machine-readable blockers for a downstream slot."""
    checked = validate_readiness(packet)
    if checked["runtime_decision_available"] is not False:
        fail("RUNTIME_DECISION_AVAILABILITY_FORBIDDEN", "must be false")
    reasons = checked["p1_regime_decision_unavailable_reasons"]
    if not reasons or reasons != sorted(set(reasons)):
        fail("UNAVAILABLE_REASONS_INVALID", "non-empty sorted unique list required")
    for reason in reasons:
        _reason(reason)
    if NOT_RUNTIME_WIRED_REASON not in reasons:
        fail("UNAVAILABLE_REASONS_INVALID", NOT_RUNTIME_WIRED_REASON)
    return list(reasons)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Runtime Regime readiness")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("regime_outputs", type=Path)
    build.add_argument("--generated-at", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("packet", type=Path)
    args = parser.parse_args(argv)

    if args.command == "validate":
        packet = validate_readiness(_read_json(args.packet))
    else:
        packet = build_readiness(_read_json(args.regime_outputs), args.generated_at)
    print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        RuntimeRegimeReadinessError,
        OUTPUT.OutputContractError,
        COVERAGE.MinimumCoverageError,
        AUTHORITY.DecisionAuthorityError,
    ) as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
