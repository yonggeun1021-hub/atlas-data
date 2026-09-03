#!/usr/bin/env python3
"""P7-10 fail-closed Capital Reallocation readiness boundary.

Capital Reallocation requires an exact portfolio scope, canonical incumbent
positions, eligible challenger proposals, settled harvest proceeds, a risk
budget and allocation-ranking authority.  None of those may be inferred from
the diagnostic packets currently available in the public repository.

This module independently validates the P7-11 operational readiness packet
and emits the precise missing inputs.  It never creates an amount, target,
proposal, action, order or capital allocation.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio import profit_harvest_readiness as harvest_readiness
from replay.opportunity_trigger import payload_sha256


CONTRACT_SCHEMA_VERSION = "capital_reallocation_readiness_contract/1"
PACKET_SCHEMA_VERSION = "capital_reallocation_readiness/1"
HISTORY_SCHEMA_VERSION = "capital_reallocation_readiness_history/1"

DEFAULT_REPORT = harvest_readiness.DEFAULT_REPORT
DEFAULT_IDENTITY = harvest_readiness.DEFAULT_IDENTITY
DEFAULT_SHADOW_CONTRACT = harvest_readiness.DEFAULT_SHADOW_CONTRACT
DEFAULT_SHADOW_PACKET = harvest_readiness.DEFAULT_SHADOW_PACKET
DEFAULT_ENTRY_READINESS_CONTRACT = harvest_readiness.DEFAULT_ENTRY_READINESS_CONTRACT
DEFAULT_ENTRY_READINESS_PACKET = harvest_readiness.DEFAULT_ENTRY_READINESS_PACKET
DEFAULT_ENTRY_BOUNDARY_CONTRACT = harvest_readiness.DEFAULT_ENTRY_BOUNDARY_CONTRACT
DEFAULT_ENTRY_BOUNDARY_PACKET = harvest_readiness.DEFAULT_ENTRY_BOUNDARY_PACKET
DEFAULT_HARVEST_CONTRACT = harvest_readiness.DEFAULT_HARVEST_CONTRACT
DEFAULT_HARVEST_PACKET = harvest_readiness.DEFAULT_OUTPUT
DEFAULT_AUDIT_ROOT = harvest_readiness.DEFAULT_AUDIT_ROOT
DEFAULT_CONTRACT = ROOT / "config/capital_reallocation_readiness_contract.json"
DEFAULT_OUTPUT = ROOT / "evidence/operational/dynamic_clock/capital_reallocation_readiness.json"
DEFAULT_HISTORY_ROOT = ROOT / "evidence/operational/dynamic_clock/capital_reallocation_readiness_history"

EXPECTED_INPUT_AXES = {
    "portfolio_scope": "NOT_COMPUTABLE_PRIVATE_PORTFOLIO_SCOPE_UNAVAILABLE",
    "incumbent_position": "NOT_COMPUTABLE_NO_LIVE_CANONICAL_POSITION_INPUT",
    "challenger_entry": "LOCKED_P8_13_POLICY_UNRATIFIED",
    "settled_harvest_proceeds": "NOT_AVAILABLE_NO_AUTHORIZED_HARVEST",
    "risk_budget": "NOT_COMPUTABLE_AUTHORITY_UNRATIFIED",
    "allocation_ranking": "LOCKED_AUTHORITY_UNRATIFIED",
}

EXPECTED_PROPOSAL_BOUNDARY = {
    "status": "LOCKED_POLICY_UNRATIFIED",
    "recommended_action": "NONE",
    "maintain_amount": None,
    "reduce_amount": None,
    "add_amount": None,
    "probe_amount": None,
    "expected_proceeds": None,
    "settled_proceeds_available": False,
    "reallocation_proposal": None,
    "trade_proposal": None,
    "order_intent": None,
    "capital": 0,
}

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "risk_budget_authorized": False,
    "allocation_ranking_authorized": False,
    "reallocation_authorized": False,
    "stage_promotion_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class CapitalReallocationReadinessError(ValueError):
    pass


def _exact_json_equal(actual: object, expected: object) -> bool:
    """Compare JSON values without Python's bool/number aliases."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _exact_json_equal(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact_json_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise CapitalReallocationReadinessError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def validate_contract(contract: dict) -> dict:
    required = {
        "schema_version", "document_status", "approval_status", "purpose",
        "input_axes", "proposal_boundary", "authority",
    }
    if not isinstance(contract, dict) or set(contract) != required:
        raise CapitalReallocationReadinessError("CONTRACT_FIELDS_INVALID")
    if contract["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise CapitalReallocationReadinessError("CONTRACT_SCHEMA_UNSUPPORTED")
    if (
        contract["document_status"] != "DESIGN_DRAFT"
        or contract["approval_status"] != "PROPOSED_UNRATIFIED"
    ):
        raise CapitalReallocationReadinessError("CONTRACT_AUTHORITY_NOT_LOCKED")
    if not _exact_json_equal(contract["input_axes"], EXPECTED_INPUT_AXES):
        raise CapitalReallocationReadinessError("INPUT_AXES_DRIFT")
    if not _exact_json_equal(
        contract["proposal_boundary"], EXPECTED_PROPOSAL_BOUNDARY
    ):
        raise CapitalReallocationReadinessError("PROPOSAL_BOUNDARY_DRIFT")
    if not _exact_json_equal(contract["authority"], AUTHORITY_ALL_FALSE):
        raise CapitalReallocationReadinessError("CONTRACT_AUTHORITY_ESCALATION")
    return copy.deepcopy(contract)


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict:
    return validate_contract(_load_json(path))


def build_packet(contract: dict, harvest_packet: dict, **harvest_validation_inputs) -> dict:
    locked_contract = validate_contract(contract)
    validated_harvest = harvest_readiness.validate_operational_packet(
        harvest_packet, **harvest_validation_inputs
    )
    if not _exact_json_equal(
        validated_harvest["summary"],
        {
            "baseline_episode_count": 11,
            "entry_proposal_count": 0,
            "live_position_eligible_count": 0,
            "harvest_review_item_count": 0,
            "harvest_proposal_count": 0,
            "order_intent_count": 0,
        },
    ):
        raise CapitalReallocationReadinessError("UPSTREAM_HARVEST_READINESS_CHANGED")
    if not _exact_json_equal(
        validated_harvest["authority"], harvest_readiness.AUTHORITY_ALL_FALSE
    ):
        raise CapitalReallocationReadinessError("UPSTREAM_AUTHORITY_ESCALATION")

    packet = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "as_of": validated_harvest["as_of"],
        "source": {
            "profit_harvest_readiness_sha256": validated_harvest["packet_sha256"],
            "entry_proposal_boundary_sha256": validated_harvest["source"][
                "entry_proposal_boundary_sha256"
            ],
            "public_code_commit_sha": validated_harvest["source"][
                "public_code_commit_sha"
            ],
            "contract_sha256": payload_sha256(locked_contract),
        },
        "input_axes": copy.deepcopy(locked_contract["input_axes"]),
        "summary": {
            "portfolio_scope_ready_count": 0,
            "incumbent_position_ready_count": 0,
            "challenger_entry_ready_count": 0,
            "settled_proceeds_ready_count": 0,
            "risk_budget_ready_count": 0,
            "allocation_ranking_ready_count": 0,
            "reallocation_proposal_count": 0,
            "order_intent_count": 0,
        },
        "decision": copy.deepcopy(locked_contract["proposal_boundary"]),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, contract: dict, harvest_packet: dict, **inputs) -> dict:
    expected = build_packet(contract, harvest_packet, **inputs)
    if not _exact_json_equal(packet, expected):
        raise CapitalReallocationReadinessError(
            "CAPITAL_REALLOCATION_READINESS_SEMANTIC_TAMPER_OR_DRIFT"
        )
    return copy.deepcopy(packet)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_outputs(packet: dict, *, output: Path, history_root: Path) -> Path:
    encoded = (
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if not output.exists() or output.read_bytes() != encoded:
        _write_bytes_atomic(output, encoded)
    record = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "as_of": packet["as_of"],
        "capital_reallocation_readiness": copy.deepcopy(packet),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    record["record_sha256"] = payload_sha256(record)
    target = history_root / f"readiness-{record['record_sha256']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    history_bytes = (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if target.exists() and target.read_bytes() != history_bytes:
        raise CapitalReallocationReadinessError("CONTENT_ADDRESSED_HISTORY_COLLISION")
    if not target.exists():
        _write_bytes_atomic(target, history_bytes)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--shadow-contract", type=Path, default=DEFAULT_SHADOW_CONTRACT)
    parser.add_argument("--shadow-packet", type=Path, default=DEFAULT_SHADOW_PACKET)
    parser.add_argument("--entry-readiness-contract", type=Path, default=DEFAULT_ENTRY_READINESS_CONTRACT)
    parser.add_argument("--entry-readiness-packet", type=Path, default=DEFAULT_ENTRY_READINESS_PACKET)
    parser.add_argument("--entry-boundary-contract", type=Path, default=DEFAULT_ENTRY_BOUNDARY_CONTRACT)
    parser.add_argument("--entry-boundary-packet", type=Path, default=DEFAULT_ENTRY_BOUNDARY_PACKET)
    parser.add_argument("--harvest-contract", type=Path, default=DEFAULT_HARVEST_CONTRACT)
    parser.add_argument("--harvest-packet", type=Path, default=DEFAULT_HARVEST_PACKET)
    parser.add_argument("--audit-root", type=Path, default=DEFAULT_AUDIT_ROOT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    parser.add_argument("--source-commit", default=None)
    parser.add_argument(
        "--trigger-kind",
        choices=harvest_readiness.entry_boundary.readiness.shadow.VALID_TRIGGER_KINDS,
        default=None,
    )
    args = parser.parse_args()

    report = _load_json(args.report)
    identity_packet = _load_json(args.identity)
    shadow_contract = _load_json(args.shadow_contract)
    shadow_packet = _load_json(args.shadow_packet)
    entry_readiness_contract = _load_json(args.entry_readiness_contract)
    entry_readiness_packet = _load_json(args.entry_readiness_packet)
    entry_contract = _load_json(args.entry_boundary_contract)
    entry_packet = _load_json(args.entry_boundary_packet)
    harvest_contract = _load_json(args.harvest_contract)
    harvest_packet = _load_json(args.harvest_packet)
    contract = load_contract(args.contract)
    trigger_kind = args.trigger_kind or entry_packet["source"]["trigger_kind"]
    source_commit = args.source_commit or harvest_readiness.current_source_commit()
    validation_inputs = {
        "entry_packet": entry_packet,
        "entry_contract": entry_contract,
        "readiness_packet": entry_readiness_packet,
        "readiness_contract": entry_readiness_contract,
        "shadow_packet": shadow_packet,
        "report": report,
        "identity_packet": identity_packet,
        "shadow_contract": shadow_contract,
        "harvest_contract": harvest_contract,
        "source_commit": source_commit,
        "audit_root": args.audit_root,
        "trigger_kind": trigger_kind,
    }
    packet = build_packet(contract, harvest_packet, **validation_inputs)
    validate_packet(packet, contract, harvest_packet, **validation_inputs)
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
