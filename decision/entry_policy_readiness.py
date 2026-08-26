#!/usr/bin/env python3
"""P5-06/P7-08 policy-readiness boundary for the P8-13 handoff.

Atlas already has a validated, zero-capital human-review surface.  That
surface deliberately diagnoses review states without ratifying candidate
validity, entry, position-management, or sizing policy.  This module turns
that distinction into one deterministic downstream packet:

* every upstream candidate remains visible as a diagnostic observation;
* no diagnostic state is treated as executable participation;
* no numerical policy parameter is accepted;
* P8-13, capital, action, order, Production, and trading stay locked.

The validator rebuilds the packet from the exact Dynamic Clock, identity,
shadow-review, and contract inputs.  Re-signing a modified result cannot make
it valid.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import shadow_entry_review as shadow
from replay.opportunity_trigger import payload_sha256


CONTRACT_SCHEMA_VERSION = "entry_policy_readiness_contract/1"
PACKET_SCHEMA_VERSION = "entry_policy_readiness/1"
HISTORY_SCHEMA_VERSION = "entry_policy_readiness_history/1"

DEFAULT_REPORT = shadow.DEFAULT_REPORT
DEFAULT_IDENTITY = shadow.DEFAULT_IDENTITY
DEFAULT_SHADOW_CONTRACT = shadow.DEFAULT_CONTRACT
DEFAULT_SHADOW_PACKET = shadow.DEFAULT_OUTPUT
DEFAULT_CONTRACT = ROOT / "config/entry_policy_readiness_contract.json"
DEFAULT_OUTPUT = ROOT / "evidence/operational/dynamic_clock/entry_policy_readiness.json"
DEFAULT_HISTORY_ROOT = ROOT / "evidence/operational/dynamic_clock/entry_policy_readiness_history"

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "candidate_validity_authorized": False,
    "entry_authorized": False,
    "position_management_authorized": False,
    "position_size_authorized": False,
    "stage_promotion_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}

EXPECTED_AXES = {
    "candidate_validity": {
        "status": "NOT_COMPUTABLE_AUTHORITY_UNRATIFIED",
        "required_authorities": [
            "DYNAMIC_CLOCK_CANDIDATE_VALIDITY_WINDOW_AUTHORITY",
            "CANONICAL_SECURITY_IDENTITY_AUTHORITY",
        ],
    },
    "entry_eligibility": {
        "status": "LOCKED_AUTHORITY_UNRATIFIED",
        "allowed_surface": "ZERO_CAPITAL_HUMAN_REVIEW_ONLY",
    },
    "position_management": {
        "status": "LOCKED_AUTHORITY_UNRATIFIED",
        "authorized_actions": [],
    },
    "position_size": {
        "status": "NOT_COMPUTABLE_AUTHORITY_UNRATIFIED",
        "risk_budget_pct": None,
        "stop_distance_pct": None,
        "max_loss": None,
        "quantity": None,
    },
}

EXPECTED_DIAGNOSTIC_PARTICIPATION = {
    "allowed_states": ["RADAR", "PROBE_REVIEW"],
    "executable_states": [],
}

EXPECTED_DOWNSTREAM_BOUNDARY = {
    "p8_13_entry_proposal": "LOCKED_NOT_STARTED",
    "trade_proposal": None,
    "capital": 0,
    "action": "NONE",
}


class EntryPolicyReadinessError(ValueError):
    pass


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise EntryPolicyReadinessError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _reject_policy_numbers(value: object, path: str = "policy_axes") -> None:
    """Reject policy numbers while permitting nulls and structural strings.

    The explicit zero-capital boundary is validated separately and is not a
    sizing parameter.  Any number inside the policy axes would be an invented
    threshold, risk budget, stop, or quantity.
    """
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        raise EntryPolicyReadinessError(
            f"NUMERIC_POLICY_PARAMETER_FORBIDDEN:{path}"
        )
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_policy_numbers(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_policy_numbers(child, f"{path}.{key}")
        return
    raise EntryPolicyReadinessError(f"POLICY_VALUE_TYPE_INVALID:{path}")


def validate_contract(contract: dict) -> dict:
    required = {
        "schema_version",
        "document_status",
        "approval_status",
        "purpose",
        "policy_axes",
        "diagnostic_participation",
        "downstream_boundary",
        "authority",
    }
    if not isinstance(contract, dict) or set(contract) != required:
        raise EntryPolicyReadinessError("CONTRACT_FIELDS_INVALID")
    if contract["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise EntryPolicyReadinessError("CONTRACT_SCHEMA_UNSUPPORTED")
    if (
        contract["document_status"] != "DESIGN_DRAFT"
        or contract["approval_status"] != "PROPOSED_UNRATIFIED"
    ):
        raise EntryPolicyReadinessError("CONTRACT_AUTHORITY_NOT_LOCKED")
    if contract["policy_axes"] != EXPECTED_AXES:
        raise EntryPolicyReadinessError("POLICY_AXES_DRIFT")
    if contract["diagnostic_participation"] != EXPECTED_DIAGNOSTIC_PARTICIPATION:
        raise EntryPolicyReadinessError("DIAGNOSTIC_PARTICIPATION_DRIFT")
    if contract["downstream_boundary"] != EXPECTED_DOWNSTREAM_BOUNDARY:
        raise EntryPolicyReadinessError("DOWNSTREAM_BOUNDARY_DRIFT")
    if contract["authority"] != AUTHORITY_ALL_FALSE:
        raise EntryPolicyReadinessError("CONTRACT_AUTHORITY_ESCALATION")
    _reject_policy_numbers(contract["policy_axes"])
    return copy.deepcopy(contract)


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict:
    return validate_contract(_load_json(path))


def _blocker_reasons() -> list[str]:
    return [
        "CANDIDATE_VALIDITY_WINDOW_AUTHORITY_UNRATIFIED",
        "ENTRY_ELIGIBILITY_AUTHORITY_UNRATIFIED",
        "POSITION_MANAGEMENT_AUTHORITY_UNRATIFIED",
        "POSITION_SIZE_AUTHORITY_UNRATIFIED",
    ]


def _candidate_row(row: dict) -> dict:
    money = row["money_boundary"]
    if money.get("capital") != 0 or money.get("trade_proposal") is not None:
        raise EntryPolicyReadinessError("UPSTREAM_MONEY_BOUNDARY_OPEN")
    if money.get("quantity") is not None:
        raise EntryPolicyReadinessError("UPSTREAM_QUANTITY_PRESENT")
    if any(
        money.get(key) is not False
        for key in (
            "stage_promotion_authority",
            "buy_authority",
            "action_authority",
            "order_authority",
            "production_authority",
            "trading_authority",
        )
    ):
        raise EntryPolicyReadinessError("UPSTREAM_AUTHORITY_ESCALATION")

    diagnostic_reviewable = (
        row["p8_13_review_surface"] == "ZERO_CAPITAL_HUMAN_REVIEW_ITEM"
    )
    result = {
        "candidate_id": row["candidate_id"],
        "market": row["market"],
        "subject": row["subject"],
        "canonical_instrument_id": row["canonical_instrument_id"],
        "identity_status": row["identity_status"],
        "review_state": row["review_state"],
        "diagnostic_participation_state": row["participation_state"],
        "diagnostic_reviewable": diagnostic_reviewable,
        "diagnostic_reason": row["review_reason"],
        "execution_status": "LOCKED_POLICY_UNRATIFIED",
        "execution_blockers": _blocker_reasons(),
        "p8_13_entry_proposal": "LOCKED_NOT_STARTED",
        "trade_proposal": None,
        "capital": 0,
        "quantity": None,
        "action": "NONE",
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    result["row_sha256"] = payload_sha256(result)
    return result


def build_packet(
    contract: dict,
    shadow_packet: dict,
    report: dict,
    identity_packet: dict,
    shadow_contract: dict,
    *,
    trigger_kind: str = shadow.TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    locked_contract = validate_contract(contract)
    validated_shadow = shadow.validate_packet(
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    if validated_shadow["authority"] != shadow.AUTHORITY_ZERO_CAPITAL:
        raise EntryPolicyReadinessError("UPSTREAM_PACKET_AUTHORITY_ESCALATION")
    if validated_shadow["policy_status"] != {
        "human_review_surface": "PROVISIONAL_CIO_ZERO_CAPITAL_REVIEW_ONLY",
        "candidate_validity": "UNRATIFIED",
        "entry": "UNRATIFIED",
        "position_management": "UNRATIFIED",
        "position_size": "UNRATIFIED",
    }:
        raise EntryPolicyReadinessError("UPSTREAM_POLICY_STATUS_CHANGED")

    candidates = [_candidate_row(row) for row in validated_shadow["review_items"]]
    reviewable_count = sum(row["diagnostic_reviewable"] for row in candidates)
    probe_count = sum(
        row["diagnostic_participation_state"] == "PROBE_REVIEW"
        for row in candidates
    )
    packet = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "decision_date": validated_shadow["decision_date"],
        "operational_evaluation": copy.deepcopy(
            validated_shadow["operational_evaluation"]
        ),
        "source": {
            "entry_policy_contract_sha256": payload_sha256(locked_contract),
            "shadow_entry_review_packet_sha256": validated_shadow["packet_sha256"],
            "dynamic_clock_report_sha256": validated_shadow["source"][
                "dynamic_clock_report_sha256"
            ],
            "candidate_identity_packet_sha256": validated_shadow["source"][
                "candidate_identity_packet_sha256"
            ],
            "trigger_kind": trigger_kind,
        },
        "policy_axes": copy.deepcopy(locked_contract["policy_axes"]),
        "diagnostic_participation": copy.deepcopy(
            locked_contract["diagnostic_participation"]
        ),
        "candidates": candidates,
        "summary": {
            "candidate_count": len(candidates),
            "diagnostic_reviewable_count": reviewable_count,
            "probe_review_diagnostic_count": probe_count,
            "execution_eligible_count": 0,
            "entry_proposal_count": 0,
            "order_intent_count": 0,
        },
        "decision": {
            "status": "LOCKED_POLICY_UNRATIFIED",
            "blocker_reasons": _blocker_reasons(),
            **copy.deepcopy(locked_contract["downstream_boundary"]),
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(
    packet: dict,
    contract: dict,
    shadow_packet: dict,
    report: dict,
    identity_packet: dict,
    shadow_contract: dict,
    *,
    trigger_kind: str = shadow.TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    expected = build_packet(
        contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    if packet != expected:
        raise EntryPolicyReadinessError(
            "ENTRY_POLICY_READINESS_SEMANTIC_TAMPER_OR_DRIFT"
        )
    return copy.deepcopy(packet)


def _history_record(packet: dict) -> dict:
    record = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "decision_date": packet["decision_date"],
        "operational_evaluated_at": packet["operational_evaluation"][
            "evaluated_at_utc"
        ],
        "trigger_kind": packet["source"]["trigger_kind"],
        "entry_policy_readiness": copy.deepcopy(packet),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
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
        / f"readiness-{record['record_sha256']}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    history_bytes = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if target.exists() and target.read_bytes() != history_bytes:
        raise EntryPolicyReadinessError("CONTENT_ADDRESSED_HISTORY_COLLISION")
    if not target.exists():
        target.write_bytes(history_bytes)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--shadow-contract", type=Path, default=DEFAULT_SHADOW_CONTRACT)
    parser.add_argument("--shadow-packet", type=Path, default=DEFAULT_SHADOW_PACKET)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    parser.add_argument(
        "--trigger-kind",
        choices=shadow.VALID_TRIGGER_KINDS,
        default=None,
    )
    args = parser.parse_args()

    report = _load_json(args.report)
    identity_packet = _load_json(args.identity)
    shadow_contract = _load_json(args.shadow_contract)
    shadow_packet = _load_json(args.shadow_packet)
    contract = load_contract(args.contract)
    trigger_kind = args.trigger_kind or shadow_packet["source"]["trigger_kind"]
    packet = build_packet(
        contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    validate_packet(
        packet,
        contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    history = write_outputs(packet, output=args.output, history_root=args.history_root)
    result = copy.deepcopy(packet["summary"])
    try:
        result["history_path"] = history.relative_to(ROOT).as_posix()
    except ValueError:
        result["history_path"] = history.as_posix()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
