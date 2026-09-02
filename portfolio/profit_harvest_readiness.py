#!/usr/bin/env python3
"""P7-11 operational readiness bridge, locked before policy and positions.

This module joins two independently validated facts:

* P8-13 currently exposes diagnostic human-review material but no proposal;
* the P7-11 baseline audit is reproducible, outcome-independent research.

It does not convert either fact into a Harvest action.  Live canonical
position eligibility is not connected in this public repository and all
Profit Harvest policy axes remain unratified.  The resulting readiness and
policy-boundary packets therefore contain no review item, quantity, action,
reallocation handoff, trade proposal or order.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import entry_proposal_boundary as entry_boundary
from harvest_audit import profit_harvest_policy_boundary as policy_boundary
from replay.opportunity_trigger import payload_sha256


READINESS_SCHEMA_VERSION = "profit_harvest_readiness/1"
OUTPUT_SCHEMA_VERSION = "profit_harvest_operational_readiness/1"
HISTORY_SCHEMA_VERSION = "profit_harvest_operational_readiness_history/1"

DEFAULT_REPORT = entry_boundary.DEFAULT_REPORT
DEFAULT_IDENTITY = entry_boundary.DEFAULT_IDENTITY
DEFAULT_SHADOW_CONTRACT = entry_boundary.DEFAULT_SHADOW_CONTRACT
DEFAULT_SHADOW_PACKET = entry_boundary.DEFAULT_SHADOW_PACKET
DEFAULT_ENTRY_READINESS_CONTRACT = entry_boundary.DEFAULT_READINESS_CONTRACT
DEFAULT_ENTRY_READINESS_PACKET = entry_boundary.DEFAULT_READINESS_PACKET
DEFAULT_ENTRY_BOUNDARY_CONTRACT = entry_boundary.DEFAULT_CONTRACT
DEFAULT_ENTRY_BOUNDARY_PACKET = entry_boundary.DEFAULT_OUTPUT
DEFAULT_HARVEST_CONTRACT = ROOT / "config/profit_harvest_policy_contract.json"
DEFAULT_AUDIT_ROOT = ROOT / "evidence/audit/profit_harvest_baseline"
DEFAULT_OUTPUT = ROOT / "evidence/operational/dynamic_clock/profit_harvest_readiness.json"
DEFAULT_BOUNDARY_OUTPUT = ROOT / "evidence/operational/dynamic_clock/profit_harvest_policy_boundary.json"
DEFAULT_HISTORY_ROOT = ROOT / "evidence/operational/dynamic_clock/profit_harvest_readiness_history"

AUDIT_ARTIFACTS = {
    "episode_ledger.json": "episode_ledger",
    "reconciliation.json": "reconciliation_table",
    "market_summary.json": "market_summary",
    "coverage_gap.json": "coverage_gap",
    "gain_path_distribution.json": "gain_path_distribution",
    "giveback_distribution.json": "giveback_distribution",
    "policy_input_packet.json": "policy_input_packet",
}
BASELINE_AUDIT_COMMIT = "70ea697aad2a3d63022d5163ce2294428344d838"

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "harvest_review_authorized": False,
    "reduce_authorized": False,
    "exit_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

EXPECTED_HARVEST = {
    "status": "LOCKED_POLICY_UNRATIFIED",
    "recommended_action": "NONE",
    "harvest_review_items": [],
    "reduce_proposal": None,
    "exit_proposal": None,
    "trade_proposal": None,
    "blocking_reasons": [
        "P8_13_ENTRY_PROPOSAL_LOCKED",
        "LIVE_CANONICAL_POSITION_ELIGIBILITY_NOT_CONNECTED",
        "PROFIT_HARVEST_POLICY_UNRATIFIED",
        "HARVEST_QUANTITY_AUTHORITY_UNRATIFIED",
        "REALLOCATION_AUTHORITY_UNRATIFIED",
    ],
}

EXPECTED_BASELINE = {
    "status": "VALIDATED_BASELINE_AUDIT_ONLY",
    "episode_count": 11,
}

EXPECTED_POLICY = {
    "status": "NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED",
    "grid_status": "ANALYTICAL_GRID_UNRATIFIED",
}


class ProfitHarvestReadinessError(ValueError):
    pass


def _exact_equal(actual, expected) -> bool:
    """Compare nested JSON values without Python scalar aliases."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _exact_equal(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ProfitHarvestReadinessError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def current_source_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def validate_source_commit(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
        raise ProfitHarvestReadinessError("SOURCE_COMMIT_NOT_IMMUTABLE_FULL_SHA")
    try:
        resolved = subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{value}^{{commit}}"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise ProfitHarvestReadinessError("SOURCE_COMMIT_UNVERIFIABLE") from exc
    if resolved != value:
        raise ProfitHarvestReadinessError("SOURCE_COMMIT_RESOLUTION_MISMATCH")
    return value


def validate_baseline(audit_root: Path = DEFAULT_AUDIT_ROOT) -> dict:
    artifact_hashes = {}
    artifacts = {}
    for filename in AUDIT_ARTIFACTS:
        path = audit_root / filename
        if not path.is_file():
            raise ProfitHarvestReadinessError(f"BASELINE_ARTIFACT_MISSING:{filename}")
        actual = path.read_bytes()
        try:
            committed = subprocess.check_output(
                [
                    "git",
                    "show",
                    f"{BASELINE_AUDIT_COMMIT}:evidence/audit/profit_harvest_baseline/{filename}",
                ],
                cwd=ROOT,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            raise ProfitHarvestReadinessError(
                f"BASELINE_COMMIT_PROVENANCE_UNAVAILABLE:{filename}"
            ) from exc
        if actual != committed:
            raise ProfitHarvestReadinessError(f"BASELINE_ARTIFACT_DRIFT:{filename}")
        artifact_hashes[filename] = _sha256_bytes(actual)
        artifacts[filename] = json.loads(actual)

    episode_count = len(artifacts["episode_ledger.json"])
    if episode_count != 11:
        raise ProfitHarvestReadinessError("BASELINE_EPISODE_POPULATION_CHANGED")
    if any(not row["reconciled"] for row in artifacts["reconciliation.json"]):
        raise ProfitHarvestReadinessError("BASELINE_RECONCILIATION_GAP")
    policy_input = artifacts["policy_input_packet.json"]
    if (
        policy_input.get("approval_status") != "UNRATIFIED"
        or policy_input.get("grid_status") != "ANALYTICAL_GRID_UNRATIFIED"
        or policy_input.get("action_authorized") is not False
        or policy_input.get("order_authorized") is not False
    ):
        raise ProfitHarvestReadinessError("BASELINE_POLICY_AUTHORITY_ESCALATION")
    return {
        "status": "VALIDATED_BASELINE_AUDIT_ONLY",
        "episode_count": episode_count,
        "baseline_audit_commit_sha": BASELINE_AUDIT_COMMIT,
        "artifact_sha256": artifact_hashes,
    }


def build_readiness(
    entry_packet: dict,
    entry_contract: dict,
    readiness_packet: dict,
    readiness_contract: dict,
    shadow_packet: dict,
    report: dict,
    identity_packet: dict,
    shadow_contract: dict,
    *,
    source_commit: str,
    audit_root: Path = DEFAULT_AUDIT_ROOT,
    trigger_kind: str = entry_boundary.readiness.shadow.TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    immutable_commit = validate_source_commit(source_commit)
    validated_entry = entry_boundary.validate_packet(
        entry_packet,
        entry_contract,
        readiness_packet,
        readiness_contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        trigger_kind=trigger_kind,
    )
    if not _exact_equal(
        validated_entry["decision"], entry_boundary.EXPECTED_PROPOSAL_BOUNDARY
    ):
        raise ProfitHarvestReadinessError("P8_13_BOUNDARY_NOT_LOCKED")
    if not _exact_equal(
        validated_entry["authority"], entry_boundary.AUTHORITY_ALL_FALSE
    ):
        raise ProfitHarvestReadinessError("P8_13_AUTHORITY_ESCALATION")
    entry_proposal_count = validated_entry["summary"]["entry_proposal_count"]
    if type(entry_proposal_count) is not int or entry_proposal_count != 0:
        raise ProfitHarvestReadinessError("P8_13_ENTRY_PROPOSAL_PRESENT")

    baseline = validate_baseline(audit_root)
    packet = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "as_of": validated_entry["operational_evaluation"]["evaluated_at_utc"],
        "source": {
            "entry_proposal_boundary_sha256": validated_entry["packet_sha256"],
            "public_code_commit_sha": immutable_commit,
            "baseline_audit_commit_sha": baseline["baseline_audit_commit_sha"],
            "audit_files_sha256": baseline["artifact_sha256"],
        },
        "baseline": {
            "status": baseline["status"],
            "episode_count": baseline["episode_count"],
        },
        "policy": {
            "status": "NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED",
            "grid_status": "ANALYTICAL_GRID_UNRATIFIED",
        },
        "harvest": copy.deepcopy(EXPECTED_HARVEST),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["readiness_sha256"] = policy_boundary.payload_sha256(packet)
    policy_boundary.validate_locked_readiness(packet)
    return packet


def build_operational_packet(readiness: dict, harvest_contract: dict) -> dict:
    locked_readiness = policy_boundary.validate_locked_readiness(readiness)
    if not _exact_equal(locked_readiness.get("baseline"), EXPECTED_BASELINE):
        raise ProfitHarvestReadinessError("UPSTREAM_READINESS_BASELINE_DRIFT")
    if not _exact_equal(locked_readiness.get("policy"), EXPECTED_POLICY):
        raise ProfitHarvestReadinessError("UPSTREAM_READINESS_POLICY_DRIFT")
    if not _exact_equal(locked_readiness.get("harvest"), EXPECTED_HARVEST):
        raise ProfitHarvestReadinessError("UPSTREAM_READINESS_HARVEST_DRIFT")
    if not _exact_equal(locked_readiness.get("authority"), AUTHORITY_ALL_FALSE):
        raise ProfitHarvestReadinessError("UPSTREAM_READINESS_AUTHORITY_ESCALATION")
    boundary = policy_boundary.build_policy_boundary(harvest_contract, locked_readiness)
    policy_boundary.validate_policy_boundary(boundary, harvest_contract, locked_readiness)
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "as_of": locked_readiness["as_of"],
        "source": copy.deepcopy(locked_readiness["source"]),
        "readiness": copy.deepcopy(locked_readiness),
        "policy_boundary": boundary,
        "summary": {
            "baseline_episode_count": locked_readiness["baseline"]["episode_count"],
            "entry_proposal_count": 0,
            "live_position_eligible_count": 0,
            "harvest_review_item_count": 0,
            "harvest_proposal_count": 0,
            "order_intent_count": 0,
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_operational_packet(
    packet: dict,
    entry_packet: dict,
    entry_contract: dict,
    readiness_packet: dict,
    readiness_contract: dict,
    shadow_packet: dict,
    report: dict,
    identity_packet: dict,
    shadow_contract: dict,
    harvest_contract: dict,
    *,
    source_commit: str,
    audit_root: Path = DEFAULT_AUDIT_ROOT,
    trigger_kind: str = entry_boundary.readiness.shadow.TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    expected_readiness = build_readiness(
        entry_packet,
        entry_contract,
        readiness_packet,
        readiness_contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        source_commit=source_commit,
        audit_root=audit_root,
        trigger_kind=trigger_kind,
    )
    expected = build_operational_packet(expected_readiness, harvest_contract)
    if not _exact_equal(packet, expected):
        raise ProfitHarvestReadinessError(
            "PROFIT_HARVEST_OPERATIONAL_SEMANTIC_TAMPER_OR_DRIFT"
        )
    return copy.deepcopy(packet)


def write_outputs(
    packet: dict,
    *,
    output: Path,
    boundary_output: Path,
    history_root: Path,
) -> Path:
    for path, value in (
        (output, packet),
        (boundary_output, packet["policy_boundary"]),
    ):
        encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != encoded:
            path.write_text(encoded)

    record = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "as_of": packet["as_of"],
        "profit_harvest_operational_readiness": copy.deepcopy(packet),
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
        raise ProfitHarvestReadinessError("CONTENT_ADDRESSED_HISTORY_COLLISION")
    if not target.exists():
        target.write_bytes(history_bytes)
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
    parser.add_argument("--audit-root", type=Path, default=DEFAULT_AUDIT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--boundary-output", type=Path, default=DEFAULT_BOUNDARY_OUTPUT)
    parser.add_argument("--history-root", type=Path, default=DEFAULT_HISTORY_ROOT)
    parser.add_argument("--source-commit", default=None)
    parser.add_argument(
        "--trigger-kind",
        choices=entry_boundary.readiness.shadow.VALID_TRIGGER_KINDS,
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
    trigger_kind = args.trigger_kind or entry_packet["source"]["trigger_kind"]
    source_commit = args.source_commit or current_source_commit()
    readiness_packet = build_readiness(
        entry_packet,
        entry_contract,
        entry_readiness_packet,
        entry_readiness_contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        source_commit=source_commit,
        audit_root=args.audit_root,
        trigger_kind=trigger_kind,
    )
    packet = build_operational_packet(readiness_packet, harvest_contract)
    validate_operational_packet(
        packet,
        entry_packet,
        entry_contract,
        entry_readiness_packet,
        entry_readiness_contract,
        shadow_packet,
        report,
        identity_packet,
        shadow_contract,
        harvest_contract,
        source_commit=source_commit,
        audit_root=args.audit_root,
        trigger_kind=trigger_kind,
    )
    history = write_outputs(
        packet,
        output=args.output,
        boundary_output=args.boundary_output,
        history_root=args.history_root,
    )
    result = copy.deepcopy(packet["summary"])
    try:
        result["history_path"] = history.relative_to(ROOT).as_posix()
    except ValueError:
        result["history_path"] = history.as_posix()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
