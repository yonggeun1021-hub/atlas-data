#!/usr/bin/env python3
"""P5-06 -> P7-08 -> P8-13 zero-capital human-review bridge.

The executable investment policies needed for an entry proposal are not
ratified.  That must not make Atlas blind: independently validated Dynamic
Clock candidates can still be sorted into an explicit human-review queue.
This module therefore creates review *dispositions*, never trade proposals.

The bridge intentionally keeps two truths separate:

* the candidate is PIT-safe enough to display for this exact operational run;
* the candidate is valid for capital, sizing or trading (not decided here).

Every output retains ``trade_proposal=null``, ``capital=0`` and all money
authorities false.  The packet is rebuilt by the validator from the exact
Dynamic Clock report, identity observation and contract, so re-signing a
tampered output cannot make it valid.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock import candidate_validity_observation as validity
from clock.review_candidate import (
    INDEPENDENT_CONFIRMATION_THRESHOLD,
    validate_review_candidate,
)
from identity import canonical_identity as ci
from identity import candidate_identity_observation as identity_observation
from replay.opportunity_trigger import canonical_json, payload_sha256


SCHEMA_VERSION = "shadow_entry_review_packet/1"
HISTORY_SCHEMA_VERSION = "shadow_entry_review_history/1"
DEFAULT_REPORT = ROOT / "evidence/operational/dynamic_clock/dynamic_clock_report.json"
DEFAULT_IDENTITY = ROOT / "evidence/operational/dynamic_clock/candidate_identity_observation.json"
DEFAULT_CONTRACT = ROOT / "config/shadow_entry_review_contract.json"
DEFAULT_OUTPUT = ROOT / "evidence/operational/dynamic_clock/shadow_entry_review.json"
DEFAULT_HISTORY_ROOT = ROOT / "evidence/operational/dynamic_clock/shadow_entry_reviews"

TRIGGER_UPSTREAM_WORKFLOW_RUN = "UPSTREAM_WORKFLOW_RUN"
TRIGGER_MANUAL_WORKFLOW_DISPATCH = "MANUAL_WORKFLOW_DISPATCH"
TRIGGER_LOCAL_REPRODUCTION = "LOCAL_REPRODUCTION"
VALID_TRIGGER_KINDS = (
    TRIGGER_UPSTREAM_WORKFLOW_RUN,
    TRIGGER_MANUAL_WORKFLOW_DISPATCH,
    TRIGGER_LOCAL_REPRODUCTION,
)

REVIEW_MOMENTUM = "MOMENTUM_PROBE_REVIEW"
REVIEW_REVERSAL = "REVERSAL_PROBE_REVIEW"
REVIEW_PULLBACK = "WAIT_FOR_PULLBACK_REVIEW"
REVIEW_WATCH = "WATCH_REVIEW"
REVIEW_BLOCKED = "NOT_REVIEWABLE"

PRICE_STRONG = "STRONG_MOMENTUM"
PRICE_MODERATE = "MODERATE"
PRICE_WEAK = "WEAK"
PRICE_OVEREXTENDED = "OVEREXTENDED"
PRICE_UNKNOWN = "UNKNOWN"

AUTHORITY_ZERO_CAPITAL = {
    "trade_proposal": None,
    "capital": 0,
    "stage_promotion_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class ShadowEntryReviewError(ValueError):
    pass


def _canonical_bytes(value: object) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _date(value: object, *, field: str) -> dt.date:
    if not isinstance(value, str):
        raise ShadowEntryReviewError(f"{field}_MUST_BE_DATE")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ShadowEntryReviewError(f"{field}_INVALID") from exc
    if parsed.isoformat() != value:
        raise ShadowEntryReviewError(f"{field}_NOT_CANONICAL")
    return parsed


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ShadowEntryReviewError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _validate_contract(contract: dict) -> None:
    required = {
        "schema_version",
        "approval_status",
        "purpose",
        "supported_trigger_types",
        "unsupported_without_live_sample",
        "review_states",
        "boundary",
    }
    if set(contract) != required:
        raise ShadowEntryReviewError("CONTRACT_SCHEMA_MISMATCH")
    if contract["schema_version"] != "shadow_entry_review_contract/1":
        raise ShadowEntryReviewError("CONTRACT_VERSION_UNSUPPORTED")
    if contract["approval_status"] != "PROVISIONAL_CIO_ZERO_CAPITAL_REVIEW_ONLY":
        raise ShadowEntryReviewError("CONTRACT_MUST_REMAIN_REVIEW_ONLY")
    if contract["supported_trigger_types"] != sorted(contract["supported_trigger_types"]):
        raise ShadowEntryReviewError("SUPPORTED_TRIGGER_TYPES_NOT_CANONICAL")
    if set(contract["review_states"]) != {
        REVIEW_MOMENTUM, REVIEW_REVERSAL, REVIEW_PULLBACK, REVIEW_WATCH, REVIEW_BLOCKED,
    }:
        raise ShadowEntryReviewError("REVIEW_STATE_VOCABULARY_MISMATCH")
    boundary = contract["boundary"]
    if boundary.get("human_review_surface") is not True:
        raise ShadowEntryReviewError("HUMAN_REVIEW_SURFACE_MUST_BE_TRUE")
    expected = {
        "candidate_validity_policy": "UNRATIFIED",
        "entry_policy": "UNRATIFIED",
        "position_management_policy": "UNRATIFIED",
        "position_size_policy": "UNRATIFIED",
        "human_review_surface": True,
        **AUTHORITY_ZERO_CAPITAL,
    }
    if boundary != expected:
        raise ShadowEntryReviewError("CONTRACT_MONEY_BOUNDARY_TAMPERED")


def _candidate_index(report: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for market in sorted(report["by_market"]):
        for candidate in report["by_market"][market]["review_queue"]:
            validate_review_candidate(candidate)
            candidate_id = candidate["candidate_id"]
            if candidate_id in rows:
                raise ShadowEntryReviewError("CANDIDATE_ID_DUPLICATE")
            if candidate["market"] != market:
                raise ShadowEntryReviewError("CANDIDATE_MARKET_MISMATCH")
            rows[candidate_id] = candidate
    return rows


def _identity_index(identity_packet: dict) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for observation in identity_packet["observations"]:
        candidate_id = observation["candidate_id"]
        if candidate_id in rows:
            raise ShadowEntryReviewError("IDENTITY_CANDIDATE_ID_DUPLICATE")
        rows[candidate_id] = observation
    return rows


def _blocked(reason: str) -> tuple[str, str, str]:
    return REVIEW_BLOCKED, "RADAR", reason


def _classify(candidate: dict, identity: dict, contract: dict) -> tuple[str, str, str]:
    """Return review_state, P7 shadow state and one deterministic reason."""
    if candidate["pit_eligibility_status"] != "PASS":
        return _blocked("PIT_ELIGIBILITY_NOT_PASS")
    if _date(candidate["expiry"], field="EXPIRY") < _date(candidate["decision_at"], field="DECISION_AT"):
        return _blocked("CANDIDATE_EXPIRED_FOR_THIS_OPERATIONAL_RUN")
    if identity["identity"]["status"] != ci.RESOLVED:
        return _blocked(identity["identity"]["status"])
    if identity["operational_evaluated_at"] != candidate["operational_evaluation"]["evaluated_at_utc"]:
        raise ShadowEntryReviewError("IDENTITY_OPERATIONAL_TIME_MISMATCH")

    unsupported = set(candidate["trigger_types"]) & set(contract["unsupported_without_live_sample"])
    if unsupported:
        return _blocked("TRIGGER_FAMILY_UNVALIDATED_NO_LIVE_SAMPLE")
    if not set(candidate["trigger_types"]) <= set(contract["supported_trigger_types"]):
        return _blocked("TRIGGER_FAMILY_NOT_SUPPORTED_BY_REVIEW_CONTRACT")

    price = candidate["price_reflection_status"]
    if price.get("status") != "LINKED":
        return _blocked("PRICE_STATE_NOT_LINKED")
    if price.get("reflection_status") != "UNKNOWN":
        raise ShadowEntryReviewError("REFLECTION_AUTHORITY_MUST_REMAIN_UNKNOWN")
    if price.get("threshold_basis") != "PROVISIONAL":
        raise ShadowEntryReviewError("PRICE_THRESHOLD_BASIS_CONTRACT_CHANGED")

    price_state = price.get("price_state")
    if price_state == PRICE_OVEREXTENDED:
        return REVIEW_PULLBACK, "RADAR", "OVEREXTENDED_PRICE_STATE_REQUIRES_PULLBACK_REVIEW"
    if price_state in (PRICE_STRONG, PRICE_MODERATE):
        return REVIEW_MOMENTUM, "PROBE_REVIEW", "PIT_TRIGGER_WITH_LINKED_MOMENTUM_PRICE_STATE"
    if (
        price_state == PRICE_WEAK
        and candidate["confirmation_count"] >= INDEPENDENT_CONFIRMATION_THRESHOLD
    ):
        return REVIEW_REVERSAL, "PROBE_REVIEW", "WEAK_PRICE_STATE_WITH_TWO_INDEPENDENT_TRIGGER_TYPES"
    if price_state in (PRICE_WEAK, PRICE_UNKNOWN):
        return REVIEW_WATCH, "RADAR", f"{price_state}_PRICE_STATE_REQUIRES_MORE_CONFIRMATION"
    raise ShadowEntryReviewError("PRICE_STATE_VOCABULARY_CHANGED")


def _money_boundary() -> dict:
    return {
        "candidate_validity_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        "entry_eligibility_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        "risk_capacity_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        "position_management_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        "position_size_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        "entry_zone": None,
        "invalidation": None,
        "max_loss": None,
        "quantity": None,
        **AUTHORITY_ZERO_CAPITAL,
    }


def build_packet(
    report: dict,
    identity_packet: dict,
    contract: dict,
    *,
    trigger_kind: str = TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    _validate_contract(contract)
    if trigger_kind not in VALID_TRIGGER_KINDS:
        raise ShadowEntryReviewError("TRIGGER_KIND_INVALID")

    # Independent upstream validation.  The shadow observation remains
    # UNRATIFIED and is used here only to verify the report's full PIT shape.
    validity.build_observation(report, trigger_kind=trigger_kind)
    authority = ci.load_authority()
    scope_authority = ci.load_scope_authority()
    identity_observation.validate_observation(
        identity_packet, report, authority, scope_authority,
    )

    candidates = _candidate_index(report)
    identities = _identity_index(identity_packet)
    if set(candidates) != set(identities):
        raise ShadowEntryReviewError("CANDIDATE_IDENTITY_SET_MISMATCH")

    rows = []
    for candidate_id in sorted(candidates):
        candidate = candidates[candidate_id]
        identity = identities[candidate_id]
        review_state, participation_state, reason = _classify(candidate, identity, contract)
        row = {
            "candidate_id": candidate_id,
            "market": candidate["market"],
            "subject": candidate["subject"],
            "canonical_instrument_id": identity["identity"]["canonical_instrument_id"],
            "identity_status": identity["identity"]["status"],
            "trigger_types": copy.deepcopy(candidate["trigger_types"]),
            "confirmation_count": candidate["confirmation_count"],
            "decision_at": candidate["decision_at"],
            "operational_evaluated_at": candidate["operational_evaluation"]["evaluated_at_utc"],
            "expiry": candidate["expiry"],
            "next_review_at": candidate["next_review_at"],
            "price_state": candidate["price_reflection_status"].get("price_state"),
            "reflection_status": candidate["price_reflection_status"].get("reflection_status"),
            "review_state": review_state,
            "participation_state": participation_state,
            "review_reason": reason,
            "p5_policy_diagnosis": "PROPOSED_E2_SHADOW_REVIEW_ONLY",
            "p7_participation_diagnosis": participation_state,
            "p8_13_review_surface": (
                "ZERO_CAPITAL_HUMAN_REVIEW_ITEM"
                if review_state != REVIEW_BLOCKED
                else "NOT_REVIEWABLE"
            ),
            "money_boundary": _money_boundary(),
        }
        row["row_sha256"] = payload_sha256(row)
        rows.append(row)

    counts = {state: 0 for state in contract["review_states"]}
    for row in rows:
        counts[row["review_state"]] += 1
    packet = {
        "schema_version": SCHEMA_VERSION,
        "decision_date": report["decision_date"],
        "operational_evaluation": copy.deepcopy(report["operational_evaluation"]),
        "source": {
            "dynamic_clock_report_sha256": payload_sha256(report),
            "candidate_identity_packet_sha256": identity_packet["packet_sha256"],
            "contract_sha256": payload_sha256(contract),
            "trigger_kind": trigger_kind,
        },
        "policy_status": {
            "human_review_surface": "PROVISIONAL_CIO_ZERO_CAPITAL_REVIEW_ONLY",
            "candidate_validity": "UNRATIFIED",
            "entry": "UNRATIFIED",
            "position_management": "UNRATIFIED",
            "position_size": "UNRATIFIED",
        },
        "review_items": rows,
        "summary": {
            "candidate_count": len(rows),
            "review_state_counts": counts,
            "zero_capital_review_item_count": sum(
                row["p8_13_review_surface"] == "ZERO_CAPITAL_HUMAN_REVIEW_ITEM"
                for row in rows
            ),
            "probe_review_count": sum(row["participation_state"] == "PROBE_REVIEW" for row in rows),
        },
        "authority": copy.deepcopy(AUTHORITY_ZERO_CAPITAL),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(
    packet: dict,
    report: dict,
    identity_packet: dict,
    contract: dict,
    *,
    trigger_kind: str = TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    expected = build_packet(
        report, identity_packet, contract, trigger_kind=trigger_kind,
    )
    if packet != expected:
        raise ShadowEntryReviewError("SHADOW_ENTRY_REVIEW_SEMANTIC_TAMPER_OR_DRIFT")
    if packet["authority"] != AUTHORITY_ZERO_CAPITAL:
        raise ShadowEntryReviewError("PACKET_AUTHORITY_TAMPERED")
    for row in packet["review_items"]:
        if row["money_boundary"] != _money_boundary():
            raise ShadowEntryReviewError("ROW_MONEY_BOUNDARY_TAMPERED")
    return copy.deepcopy(packet)


def _history_record(packet: dict) -> dict:
    record = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "decision_date": packet["decision_date"],
        "operational_evaluated_at": packet["operational_evaluation"]["evaluated_at_utc"],
        "trigger_kind": packet["source"]["trigger_kind"],
        "shadow_entry_review": copy.deepcopy(packet),
        "authority": copy.deepcopy(AUTHORITY_ZERO_CAPITAL),
    }
    record["record_sha256"] = payload_sha256(record)
    return record


def write_outputs(packet: dict, *, output: Path, history_root: Path) -> Path:
    encoded = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists() or output.read_text() != encoded:
        output.write_text(encoded)

    record = _history_record(packet)
    target = (
        history_root
        / record["decision_date"]
        / record["trigger_kind"].lower()
        / f"review-{record['record_sha256']}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    history_bytes = _canonical_bytes(record)
    if target.exists() and target.read_bytes() != history_bytes:
        raise ShadowEntryReviewError("CONTENT_ADDRESSED_HISTORY_COLLISION")
    if not target.exists():
        target.write_bytes(history_bytes)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    parser.add_argument("--trigger-kind", choices=VALID_TRIGGER_KINDS, default=TRIGGER_LOCAL_REPRODUCTION)
    args = parser.parse_args()

    report = _load_json(args.report)
    identity_packet = _load_json(args.identity)
    contract = _load_json(args.contract)
    packet = build_packet(report, identity_packet, contract, trigger_kind=args.trigger_kind)
    validate_packet(packet, report, identity_packet, contract, trigger_kind=args.trigger_kind)
    history_path = write_outputs(packet, output=args.output, history_root=args.history_root)
    result = dict(packet["summary"])
    try:
        result["history_path"] = history_path.relative_to(ROOT).as_posix()
    except ValueError:
        # Tests and read-only local reproductions may deliberately route
        # output outside the repository.  This value is console metadata,
        # never part of the signed packet.
        result["history_path"] = history_path.as_posix()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
