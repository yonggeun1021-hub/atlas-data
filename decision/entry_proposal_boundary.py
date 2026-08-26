#!/usr/bin/env python3
"""P8-13 fail-closed human-review proposal boundary.

The upstream P5-06/P7-08 readiness packet can identify observations worth a
human review, but none of the policies required to construct an entry
proposal are ratified.  This module makes that last boundary explicit:

* diagnostic review material may be carried forward;
* no entry zone, invalidation, risk budget, size, quantity or action is made;
* a proposal, order intent and capital allocation are always absent;
* all Stage/Buy/Action/Order/Production/trading authority remains false.

Validation is semantic.  The packet is rebuilt from the exact readiness and
upstream inputs, so changing a result and recalculating its hash cannot make
the result valid.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import entry_policy_readiness as readiness
from replay.opportunity_trigger import payload_sha256


CONTRACT_SCHEMA_VERSION = "entry_proposal_boundary_contract/1"
PACKET_SCHEMA_VERSION = "entry_proposal_boundary/1"
HISTORY_SCHEMA_VERSION = "entry_proposal_boundary_history/1"

DEFAULT_REPORT = readiness.DEFAULT_REPORT
DEFAULT_IDENTITY = readiness.DEFAULT_IDENTITY
DEFAULT_SHADOW_CONTRACT = readiness.DEFAULT_SHADOW_CONTRACT
DEFAULT_SHADOW_PACKET = readiness.DEFAULT_SHADOW_PACKET
DEFAULT_READINESS_CONTRACT = readiness.DEFAULT_CONTRACT
DEFAULT_READINESS_PACKET = readiness.DEFAULT_OUTPUT
DEFAULT_CONTRACT = ROOT / "config/entry_proposal_boundary_contract.json"
DEFAULT_OUTPUT = ROOT / "evidence/operational/dynamic_clock/entry_proposal_boundary.json"
DEFAULT_HISTORY_ROOT = ROOT / "evidence/operational/dynamic_clock/entry_proposal_boundaries"

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "proposal_draft_authorized": False,
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

EXPECTED_REVIEW_MATERIAL = {
    "status": "DIAGNOSTIC_ONLY",
    "source_filter": "DIAGNOSTIC_REVIEWABLE_TRUE",
    "authorized_actions": [],
}

EXPECTED_PROPOSAL_BOUNDARY = {
    "status": "LOCKED_POLICY_UNRATIFIED",
    "p8_13_boundary": "IMPLEMENTED_FAIL_CLOSED",
    "proposed_action": "NONE",
    "entry_zone": None,
    "invalidation": None,
    "risk_budget_pct": None,
    "max_loss": None,
    "position_size": None,
    "quantity": None,
    "trade_proposal": None,
    "order_intent": None,
    "capital": 0,
}


class EntryProposalBoundaryError(ValueError):
    pass


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise EntryProposalBoundaryError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def validate_contract(contract: dict) -> dict:
    required = {
        "schema_version",
        "document_status",
        "approval_status",
        "purpose",
        "required_upstream_status",
        "human_review_material",
        "proposal_boundary",
        "authority",
    }
    if not isinstance(contract, dict) or set(contract) != required:
        raise EntryProposalBoundaryError("CONTRACT_FIELDS_INVALID")
    if contract["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise EntryProposalBoundaryError("CONTRACT_SCHEMA_UNSUPPORTED")
    if (
        contract["document_status"] != "DESIGN_DRAFT"
        or contract["approval_status"] != "PROPOSED_UNRATIFIED"
    ):
        raise EntryProposalBoundaryError("CONTRACT_AUTHORITY_NOT_LOCKED")
    if contract["required_upstream_status"] != "LOCKED_POLICY_UNRATIFIED":
        raise EntryProposalBoundaryError("UPSTREAM_LOCK_REQUIREMENT_DRIFT")
    if contract["human_review_material"] != EXPECTED_REVIEW_MATERIAL:
        raise EntryProposalBoundaryError("REVIEW_MATERIAL_CONTRACT_DRIFT")
    if contract["proposal_boundary"] != EXPECTED_PROPOSAL_BOUNDARY:
        raise EntryProposalBoundaryError("PROPOSAL_BOUNDARY_DRIFT")
    if contract["authority"] != AUTHORITY_ALL_FALSE:
        raise EntryProposalBoundaryError("CONTRACT_AUTHORITY_ESCALATION")
    return copy.deepcopy(contract)


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict:
    return validate_contract(_load_json(path))


def _review_material(row: dict) -> dict:
    if not row["diagnostic_reviewable"]:
        raise EntryProposalBoundaryError("NON_REVIEWABLE_ROW_SELECTED")
    if row["execution_status"] != "LOCKED_POLICY_UNRATIFIED":
        raise EntryProposalBoundaryError("UPSTREAM_EXECUTION_STATUS_CHANGED")
    if (
        row["trade_proposal"] is not None
        or row["capital"] != 0
        or row["quantity"] is not None
        or row["action"] != "NONE"
    ):
        raise EntryProposalBoundaryError("UPSTREAM_MONEY_BOUNDARY_OPEN")
    if row["authority"] != readiness.AUTHORITY_ALL_FALSE:
        raise EntryProposalBoundaryError("UPSTREAM_AUTHORITY_ESCALATION")

    material = {
        "candidate_id": row["candidate_id"],
        "market": row["market"],
        "subject": row["subject"],
        "canonical_instrument_id": row["canonical_instrument_id"],
        "identity_status": row["identity_status"],
        "review_state": row["review_state"],
        "diagnostic_participation_state": row["diagnostic_participation_state"],
        "diagnostic_reason": row["diagnostic_reason"],
        "readiness_row_sha256": row["row_sha256"],
        "material_status": "DIAGNOSTIC_REVIEW_MATERIAL_ONLY",
        "proposal_status": "LOCKED_POLICY_UNRATIFIED",
        **copy.deepcopy(EXPECTED_PROPOSAL_BOUNDARY),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    material["row_sha256"] = payload_sha256(material)
    return material


def build_packet(
    contract: dict,
    readiness_packet: dict,
    readiness_contract: dict,
    shadow_packet: dict,
    report: dict,
    identity_packet: dict,
    shadow_contract: dict,
    *,
    trigger_kind: str = readiness.shadow.TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    locked_contract = validate_contract(contract)
    validated_readiness = readiness.validate_packet(
        readiness_packet,
        readiness_contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    if validated_readiness["decision"]["status"] != "LOCKED_POLICY_UNRATIFIED":
        raise EntryProposalBoundaryError("UPSTREAM_POLICY_LOCK_NOT_PRESENT")
    if validated_readiness["authority"] != readiness.AUTHORITY_ALL_FALSE:
        raise EntryProposalBoundaryError("UPSTREAM_PACKET_AUTHORITY_ESCALATION")
    if any(
        validated_readiness["summary"].get(key) != 0
        for key in ("execution_eligible_count", "entry_proposal_count", "order_intent_count")
    ):
        raise EntryProposalBoundaryError("UPSTREAM_EXECUTABLE_OUTPUT_PRESENT")

    materials = [
        _review_material(row)
        for row in validated_readiness["candidates"]
        if row["diagnostic_reviewable"]
    ]
    packet = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "decision_date": validated_readiness["decision_date"],
        "operational_evaluation": copy.deepcopy(
            validated_readiness["operational_evaluation"]
        ),
        "source": {
            "entry_proposal_boundary_contract_sha256": payload_sha256(locked_contract),
            "entry_policy_readiness_packet_sha256": validated_readiness["packet_sha256"],
            "shadow_entry_review_packet_sha256": validated_readiness["source"][
                "shadow_entry_review_packet_sha256"
            ],
            "dynamic_clock_report_sha256": validated_readiness["source"][
                "dynamic_clock_report_sha256"
            ],
            "candidate_identity_packet_sha256": validated_readiness["source"][
                "candidate_identity_packet_sha256"
            ],
            "trigger_kind": trigger_kind,
        },
        "human_review_material": materials,
        "summary": {
            "observed_candidate_count": validated_readiness["summary"]["candidate_count"],
            "human_review_material_count": len(materials),
            "observation_only_count": (
                validated_readiness["summary"]["candidate_count"] - len(materials)
            ),
            "actionable_proposal_count": 0,
            "entry_proposal_count": 0,
            "order_intent_count": 0,
        },
        "decision": copy.deepcopy(locked_contract["proposal_boundary"]),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(
    packet: dict,
    contract: dict,
    readiness_packet: dict,
    readiness_contract: dict,
    shadow_packet: dict,
    report: dict,
    identity_packet: dict,
    shadow_contract: dict,
    *,
    trigger_kind: str = readiness.shadow.TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    expected = build_packet(
        contract,
        readiness_packet,
        readiness_contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    if packet != expected:
        raise EntryProposalBoundaryError(
            "ENTRY_PROPOSAL_BOUNDARY_SEMANTIC_TAMPER_OR_DRIFT"
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
        "entry_proposal_boundary": copy.deepcopy(packet),
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
        / f"boundary-{record['record_sha256']}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    history_bytes = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if target.exists() and target.read_bytes() != history_bytes:
        raise EntryProposalBoundaryError("CONTENT_ADDRESSED_HISTORY_COLLISION")
    if not target.exists():
        target.write_bytes(history_bytes)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--shadow-contract", type=Path, default=DEFAULT_SHADOW_CONTRACT)
    parser.add_argument("--shadow-packet", type=Path, default=DEFAULT_SHADOW_PACKET)
    parser.add_argument("--readiness-contract", type=Path, default=DEFAULT_READINESS_CONTRACT)
    parser.add_argument("--readiness-packet", type=Path, default=DEFAULT_READINESS_PACKET)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    parser.add_argument(
        "--trigger-kind",
        choices=readiness.shadow.VALID_TRIGGER_KINDS,
        default=None,
    )
    args = parser.parse_args()

    report = _load_json(args.report)
    identity_packet = _load_json(args.identity)
    shadow_contract = _load_json(args.shadow_contract)
    shadow_packet = _load_json(args.shadow_packet)
    readiness_contract = _load_json(args.readiness_contract)
    readiness_packet = _load_json(args.readiness_packet)
    contract = load_contract(args.contract)
    trigger_kind = args.trigger_kind or readiness_packet["source"]["trigger_kind"]
    packet = build_packet(
        contract,
        readiness_packet,
        readiness_contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    validate_packet(
        packet,
        contract,
        readiness_packet,
        readiness_contract,
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
