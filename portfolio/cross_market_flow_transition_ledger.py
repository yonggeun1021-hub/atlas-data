#!/usr/bin/env python3
"""P2-COM-03 append-only cross-market flow transition ledger.

The ledger consumes only the validated P2-COM-02 output packet.  It records
structural state history and exact lineage without creating confirmation,
allocation, capital, action, order, Production, or Trading authority.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "cross_market_flow_transition_ledger_contract.json"
SOURCE_PATH = ROOT / "data" / "latest_capital_flow_posture_reference.json"
LATEST_PATH = ROOT / "data" / "latest_cross_market_flow_transition_ledger.json"
EVIDENCE_ROOT = ROOT / "evidence" / "portfolio" / "cross_market_flow_transition_ledger"
LEDGER_SCHEMA_VERSION = "cross_market_flow_transition_ledger_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CrossMarketFlowTransitionLedgerError(ValueError):
    """Fail-closed P2-COM-03 contract violation."""


def fail(code: str, detail: str = "") -> None:
    raise CrossMarketFlowTransitionLedgerError(
        f"{code}:{detail}" if detail else code
    )


def _load_producer():
    path = ROOT / "portfolio" / "capital_flow_posture_reference.py"
    spec = importlib.util.spec_from_file_location(
        "atlas_p2_com_02_for_transition_ledger", path
    )
    if spec is None or spec.loader is None:
        fail("PRODUCER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRODUCER = _load_producer()


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CrossMarketFlowTransitionLedgerError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CrossMarketFlowTransitionLedgerError(
            f"SOURCE_MISSING:{path}"
        ) from exc


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CrossMarketFlowTransitionLedgerError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        fail(code)
    return value


def _date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code)
    try:
        result = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CrossMarketFlowTransitionLedgerError(code) from exc
    if result.isoformat() != value:
        fail(code)
    return result


def _timestamp(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        fail(code)
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CrossMarketFlowTransitionLedgerError(code) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        fail(code)
    return result.astimezone(dt.timezone.utc)


def _relative_path(path: Path, root: Path, code: str) -> str:
    try:
        relative = Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise CrossMarketFlowTransitionLedgerError(code) from exc
    return relative.as_posix()


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "cross_market_flow_transition_ledger/1",
        "input_schema_version": "capital_flow_posture_reference/v1",
        "input_contract_version": "capital_flow_posture_reference_policy/v1",
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "observation_modes": ["NATURAL", "MANUAL", "RECOVERY", "REPLAY"],
        "persistence_count_policy": {
            "NATURAL": True,
            "MANUAL": False,
            "RECOVERY": False,
            "REPLAY": False,
        },
        "transition_types": [
            "INITIAL",
            "UNCHANGED",
            "CHANGED",
            "REVERSAL",
            "INVALIDATION",
            "RECOVERY",
        ],
        "same_observation_date_policy": (
            "IDENTICAL_SOURCE_NOOP_OTHERWISE_REVISION_DRIFT_FAIL_CLOSED"
        ),
        "confirmation_policy": "UNRATIFIED_CONFIRMED_AT_NULL",
        "stale_policy": (
            "NON_FORWARD_OBSERVATION_DATE_FAIL_CLOSED_NO_WALL_CLOCK_THRESHOLD"
        ),
        "authority": {
            "read_only_audit_display_authorized": True,
            "persistence_observation_authorized": True,
            "confirmation_authorized": False,
            "numeric_threshold_authorized": False,
            "market_allocation_authorized": False,
            "capital_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def validate_contract(value: object) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        fail("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            fail("CONTRACT_FIELD_MISMATCH", key)
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(read_json(Path(path), "CONTRACT_READ_FAILED"))


def _packet_digest(packet: dict) -> str:
    claimed = _sha(packet.get("payload_sha256"), "SOURCE_PAYLOAD_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        fail("SOURCE_PAYLOAD_SHA_MISMATCH")
    return claimed


def _current_state(packet: dict) -> dict:
    flow = packet.get("cross_market_flow")
    if not isinstance(flow, dict):
        fail("SOURCE_CROSS_MARKET_FLOW_INVALID")
    required = {
        "actual_money_flow",
        "actual_money_flow_reason",
        "comparison_status",
        "comparison_as_of_date",
        "comparable_market_count",
        "required_market_count",
        "relative_strength_leader",
        "relative_strength_laggard",
        "explanation_ko",
    }
    if set(flow) != required:
        fail("SOURCE_CROSS_MARKET_FLOW_FIELDS_MISMATCH")
    if flow["comparison_as_of_date"] is not None:
        _date(flow["comparison_as_of_date"], "SOURCE_COMPARISON_DATE_INVALID")
    if flow["comparison_status"] not in {
        "UNKNOWN",
        "PARTIAL_RELATIVE_STRENGTH_REFERENCE",
        "THREE_MARKET_RELATIVE_STRENGTH_REFERENCE",
    }:
        fail("SOURCE_COMPARISON_STATUS_INVALID")
    for key in ("relative_strength_leader", "relative_strength_laggard"):
        if flow[key] is not None and flow[key] not in {"US", "KR", "CRYPTO"}:
            fail("SOURCE_RELATIVE_MARKET_INVALID", key)
    if (
        flow["relative_strength_leader"] is not None
        and flow["relative_strength_leader"] == flow["relative_strength_laggard"]
    ):
        fail("SOURCE_RELATIVE_MARKETS_COLLIDE")
    return {
        "source_status": copy.deepcopy(packet.get("status")),
        "cross_market_flow": copy.deepcopy(flow),
    }


def _semantic_state(state: dict) -> dict:
    flow = state["cross_market_flow"]
    return {
        "actual_money_flow": copy.deepcopy(flow["actual_money_flow"]),
        "actual_money_flow_reason": copy.deepcopy(flow["actual_money_flow_reason"]),
        "comparison_status": copy.deepcopy(flow["comparison_status"]),
        "relative_strength_leader": copy.deepcopy(flow["relative_strength_leader"]),
        "relative_strength_laggard": copy.deepcopy(flow["relative_strength_laggard"]),
    }


def _validate_source_packet(
    packet: dict,
    source_path: Path,
    root: Path,
    contract: dict,
) -> dict:
    if not isinstance(packet, dict):
        fail("SOURCE_PACKET_INVALID")
    try:
        PRODUCER.validate_reference(packet, root)
    except Exception as exc:
        raise CrossMarketFlowTransitionLedgerError(
            f"SOURCE_SEMANTIC_REVALIDATION_FAILED:{exc}"
        ) from exc
    if (
        packet.get("schema_version") != contract["input_schema_version"]
        or packet.get("contract_version") != contract["input_contract_version"]
    ):
        fail("SOURCE_CONTRACT_IDENTITY_INVALID")
    source_digest = _packet_digest(packet)
    generated_at = _timestamp(packet.get("generated_at"), "SOURCE_GENERATED_AT_INVALID")
    state = _current_state(packet)
    comparison_date = state["cross_market_flow"]["comparison_as_of_date"]
    effective_date = (
        _date(comparison_date, "SOURCE_COMPARISON_DATE_INVALID")
        if comparison_date is not None
        else generated_at.date()
    )
    authority = packet.get("authority")
    if not isinstance(authority, dict):
        fail("SOURCE_AUTHORITY_INVALID")
    for key in (
        "actual_flow_claim_authorized",
        "gross_exposure_authorized",
        "cash_target_authorized",
        "cross_market_allocation_authorized",
        "position_size_authorized",
        "stage_authorized",
        "buy_authorized",
        "action_authorized",
        "order_authorized",
        "production_authorized",
        "trading_authorized",
    ):
        if authority.get(key) is not False:
            fail("SOURCE_AUTHORITY_EXPANDED", key)
    relative_path = _relative_path(source_path, root, "SOURCE_PATH_OUTSIDE_ROOT")
    policy = packet.get("policy")
    sources = packet.get("sources")
    if not isinstance(policy, dict) or not isinstance(sources, list):
        fail("SOURCE_LINEAGE_INVALID")
    return {
        "packet": copy.deepcopy(packet),
        "packet_sha256": source_digest,
        "generation_id": _sha(
            packet.get("generation_id"), "SOURCE_GENERATION_ID_INVALID"
        ),
        "generated_at": packet["generated_at"],
        "effective_observation_date": effective_date.isoformat(),
        "state": state,
        "semantic_state_sha256": payload_sha256(_semantic_state(state)),
        "lineage": {
            "input_path": relative_path,
            "input_file_sha256": file_sha256(source_path),
            "input_payload_sha256": source_digest,
            "input_generation_id": packet["generation_id"],
            "input_schema_version": packet["schema_version"],
            "input_contract_version": packet["contract_version"],
            "producer_policy": copy.deepcopy(policy),
            "producer_sources": copy.deepcopy(sources),
            "input_packet": copy.deepcopy(packet),
        },
    }


def _contract_lineage(contract_path: Path, root: Path) -> dict:
    return {
        "path": _relative_path(contract_path, root, "CONTRACT_PATH_OUTSIDE_ROOT"),
        "sha256": file_sha256(contract_path),
        "contract_version": "cross_market_flow_transition_ledger/1",
    }


def empty_ledger(
    contract: Optional[dict] = None,
    *,
    contract_path: Path = CONTRACT_PATH,
    root: Path = ROOT,
) -> dict:
    contract = load_contract(contract_path) if contract is None else validate_contract(contract)
    packet = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "status": "EMPTY",
        "ledger_id": "CROSS_MARKET_FLOW",
        "ledger_revision": 0,
        "contract": _contract_lineage(contract_path, root),
        "entries": [],
        "current_state": None,
        "latest_transition": None,
        "observation_mode_counts": {
            mode: 0 for mode in contract["observation_modes"]
        },
        "counted_natural_observations": 0,
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "CONFIRMATION_POLICY_UNRATIFIED",
            "NUMERIC_THRESHOLD_ABSENT",
            "MARKET_ALLOCATION_UNAUTHORIZED",
            "CAPITAL_ACTION_ORDER_PRODUCTION_TRADING_UNAUTHORIZED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def _transition_type(previous_state: Optional[dict], current_state: dict) -> str:
    if previous_state is None:
        return "INITIAL"
    previous_flow = previous_state["cross_market_flow"]
    current_flow = current_state["cross_market_flow"]
    previous_unknown = previous_flow["comparison_status"] == "UNKNOWN"
    current_unknown = current_flow["comparison_status"] == "UNKNOWN"
    if not previous_unknown and current_unknown:
        return "INVALIDATION"
    if previous_unknown and not current_unknown:
        return "RECOVERY"
    if (
        previous_flow["relative_strength_leader"] is not None
        and previous_flow["relative_strength_laggard"] is not None
        and current_flow["relative_strength_leader"]
        == previous_flow["relative_strength_laggard"]
        and current_flow["relative_strength_laggard"]
        == previous_flow["relative_strength_leader"]
    ):
        return "REVERSAL"
    if payload_sha256(_semantic_state(previous_state)) == payload_sha256(
        _semantic_state(current_state)
    ):
        return "UNCHANGED"
    return "CHANGED"


def _persistence(entries: list[dict], semantic_sha: str, mode: str) -> tuple[str, dict]:
    matching = [
        item for item in entries if item["current_semantic_state_sha256"] == semantic_sha
    ]
    first_seen = matching[0]["observed_at"] if matching else None
    previous_same = bool(
        entries and entries[-1]["current_semantic_state_sha256"] == semantic_sha
    )
    if previous_same:
        previous = entries[-1]["persistence"]
        streak_all = previous["current_streak_observation_count"] + 1
        streak_natural = previous["current_streak_natural_count"] + (
            1 if mode == "NATURAL" else 0
        )
    else:
        streak_all = 1
        streak_natural = 1 if mode == "NATURAL" else 0
    return first_seen, {
        "state_observation_count_total": len(matching) + 1,
        "state_natural_observation_count_total": (
            sum(item["observation_mode"] == "NATURAL" for item in matching)
            + (1 if mode == "NATURAL" else 0)
        ),
        "current_streak_observation_count": streak_all,
        "current_streak_natural_count": streak_natural,
        "confirmation_threshold": None,
        "confirmation_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
    }


def _build_entry(
    validated: dict,
    mode: str,
    entries: list[dict],
    contract: dict,
) -> dict:
    if mode not in contract["observation_modes"]:
        fail("OBSERVATION_MODE_INVALID")
    previous = entries[-1] if entries else None
    previous_state = None if previous is None else previous["current_state"]
    transition_type = _transition_type(previous_state, validated["state"])
    semantic_sha = validated["semantic_state_sha256"]
    first_seen, persistence = _persistence(entries, semantic_sha, mode)
    if first_seen is None:
        first_seen = validated["generated_at"]
    previous_flow = None if previous_state is None else previous_state["cross_market_flow"]
    current_flow = validated["state"]["cross_market_flow"]
    entry = {
        "ledger_revision": len(entries) + 1,
        "effective_observation_date": validated["effective_observation_date"],
        "observed_at": validated["generated_at"],
        "observation_mode": mode,
        "counts_toward_persistence": contract["persistence_count_policy"][mode],
        "previous_state": copy.deepcopy(previous_state),
        "current_state": copy.deepcopy(validated["state"]),
        "current_semantic_state_sha256": semantic_sha,
        "first_seen": first_seen,
        "confirmed_at": None,
        "persistence": persistence,
        "transition": {
            "type": transition_type,
            "reversal": {
                "detected": transition_type == "REVERSAL",
                "previous_leader": (
                    None if previous_flow is None else previous_flow["relative_strength_leader"]
                ),
                "previous_laggard": (
                    None if previous_flow is None else previous_flow["relative_strength_laggard"]
                ),
                "current_leader": current_flow["relative_strength_leader"],
                "current_laggard": current_flow["relative_strength_laggard"],
            },
            "invalidation": {
                "detected": transition_type == "INVALIDATION",
                "reason": (
                    current_flow["actual_money_flow_reason"]
                    if transition_type == "INVALIDATION"
                    else None
                ),
            },
        },
        "lineage": copy.deepcopy(validated["lineage"]),
        "previous_entry_sha256": (
            None if previous is None else previous["entry_sha256"]
        ),
    }
    entry["entry_sha256"] = payload_sha256(entry)
    return entry


def _validate_embedded_source(entry: dict, contract: dict) -> None:
    lineage = entry.get("lineage")
    required = {
        "input_path",
        "input_file_sha256",
        "input_payload_sha256",
        "input_generation_id",
        "input_schema_version",
        "input_contract_version",
        "producer_policy",
        "producer_sources",
        "input_packet",
    }
    if not isinstance(lineage, dict) or set(lineage) != required:
        fail("LEDGER_LINEAGE_FIELDS_MISMATCH")
    packet = lineage["input_packet"]
    if not isinstance(packet, dict):
        fail("LEDGER_SOURCE_PACKET_INVALID")
    digest = _packet_digest(packet)
    if (
        packet.get("schema_version") != contract["input_schema_version"]
        or packet.get("contract_version") != contract["input_contract_version"]
    ):
        fail("LEDGER_SOURCE_CONTRACT_IDENTITY_INVALID")
    generated_at = _timestamp(
        packet.get("generated_at"), "LEDGER_SOURCE_GENERATED_AT_INVALID"
    )
    source_state = _current_state(packet)
    comparison_date = source_state["cross_market_flow"]["comparison_as_of_date"]
    effective_date = (
        comparison_date if comparison_date is not None else generated_at.date().isoformat()
    )
    authority = packet.get("authority")
    if not isinstance(authority, dict):
        fail("LEDGER_SOURCE_AUTHORITY_INVALID")
    for key in (
        "actual_flow_claim_authorized",
        "gross_exposure_authorized",
        "cash_target_authorized",
        "cross_market_allocation_authorized",
        "position_size_authorized",
        "stage_authorized",
        "buy_authorized",
        "action_authorized",
        "order_authorized",
        "production_authorized",
        "trading_authorized",
    ):
        if authority.get(key) is not False:
            fail("LEDGER_SOURCE_AUTHORITY_EXPANDED", key)
    if (
        lineage["input_payload_sha256"] != digest
        or lineage["input_generation_id"] != packet.get("generation_id")
        or lineage["input_schema_version"] != contract["input_schema_version"]
        or lineage["input_schema_version"] != packet.get("schema_version")
        or lineage["input_contract_version"] != contract["input_contract_version"]
        or lineage["input_contract_version"] != packet.get("contract_version")
        or lineage["producer_policy"] != packet.get("policy")
        or lineage["producer_sources"] != packet.get("sources")
        or entry.get("observed_at") != packet.get("generated_at")
        or entry.get("effective_observation_date") != effective_date
    ):
        fail("LEDGER_SOURCE_LINEAGE_MISMATCH")
    _sha(lineage["input_file_sha256"], "LEDGER_SOURCE_FILE_SHA_INVALID")
    if entry.get("current_state") != source_state:
        fail("LEDGER_STATE_SOURCE_MISMATCH")


def validate_ledger(value: object, contract: Optional[dict] = None) -> dict:
    contract = load_contract() if contract is None else validate_contract(contract)
    required = {
        "schema_version",
        "contract_version",
        "status",
        "ledger_id",
        "ledger_revision",
        "contract",
        "entries",
        "current_state",
        "latest_transition",
        "observation_mode_counts",
        "counted_natural_observations",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        fail("LEDGER_FIELDS_MISMATCH")
    unsigned = copy.deepcopy(value)
    claimed = _sha(unsigned.pop("payload_sha256", None), "LEDGER_SHA_INVALID")
    if payload_sha256(unsigned) != claimed:
        fail("LEDGER_SHA_MISMATCH")
    if (
        value["schema_version"] != LEDGER_SCHEMA_VERSION
        or value["contract_version"] != contract["contract_version"]
        or value["ledger_id"] != "CROSS_MARKET_FLOW"
        or value["authority"] != contract["authority"]
        or value["unresolved_boundaries"]
        != [
            "CONFIRMATION_POLICY_UNRATIFIED",
            "NUMERIC_THRESHOLD_ABSENT",
            "MARKET_ALLOCATION_UNAUTHORIZED",
            "CAPITAL_ACTION_ORDER_PRODUCTION_TRADING_UNAUTHORIZED",
        ]
    ):
        fail("LEDGER_CONTRACT_MISMATCH")
    contract_lineage = value.get("contract")
    if (
        not isinstance(contract_lineage, dict)
        or set(contract_lineage) != {"path", "sha256", "contract_version"}
        or contract_lineage["path"]
        != "config/cross_market_flow_transition_ledger_contract.json"
        or contract_lineage["sha256"] != file_sha256(CONTRACT_PATH)
        or contract_lineage["contract_version"] != contract["contract_version"]
    ):
        fail("LEDGER_CONTRACT_LINEAGE_INVALID")
    _sha(contract_lineage["sha256"], "LEDGER_CONTRACT_SHA_INVALID")
    entries = value.get("entries")
    if not isinstance(entries, list):
        fail("LEDGER_ENTRIES_INVALID")
    if value.get("ledger_revision") != len(entries):
        fail("LEDGER_REVISION_INVALID")
    expected_modes = {mode: 0 for mode in contract["observation_modes"]}
    prior_entry = None
    prior_date = None
    prior_entries: list[dict] = []
    for index, entry in enumerate(entries, 1):
        entry_required = {
            "ledger_revision",
            "effective_observation_date",
            "observed_at",
            "observation_mode",
            "counts_toward_persistence",
            "previous_state",
            "current_state",
            "current_semantic_state_sha256",
            "first_seen",
            "confirmed_at",
            "persistence",
            "transition",
            "lineage",
            "previous_entry_sha256",
            "entry_sha256",
        }
        if not isinstance(entry, dict) or set(entry) != entry_required:
            fail("LEDGER_ENTRY_FIELDS_MISMATCH")
        if entry["ledger_revision"] != index:
            fail("LEDGER_ENTRY_REVISION_INVALID")
        mode = entry["observation_mode"]
        if mode not in contract["observation_modes"]:
            fail("LEDGER_ENTRY_MODE_INVALID")
        expected_modes[mode] += 1
        if entry["counts_toward_persistence"] is not contract[
            "persistence_count_policy"
        ][mode]:
            fail("LEDGER_ENTRY_COUNT_LABEL_INVALID")
        day = _date(
            entry["effective_observation_date"], "LEDGER_ENTRY_DATE_INVALID"
        )
        _timestamp(entry["observed_at"], "LEDGER_ENTRY_TIME_INVALID")
        if prior_date is not None and day <= prior_date:
            fail("LEDGER_NON_FORWARD_OBSERVATION")
        prior_date = day
        expected_previous_state = (
            None if prior_entry is None else prior_entry["current_state"]
        )
        expected_previous_sha = (
            None if prior_entry is None else prior_entry["entry_sha256"]
        )
        if (
            entry["previous_state"] != expected_previous_state
            or entry["previous_entry_sha256"] != expected_previous_sha
            or entry["confirmed_at"] is not None
        ):
            fail("LEDGER_ENTRY_CHAIN_INVALID")
        _validate_embedded_source(entry, contract)
        semantic_sha = payload_sha256(_semantic_state(entry["current_state"]))
        if entry["current_semantic_state_sha256"] != semantic_sha:
            fail("LEDGER_ENTRY_SEMANTIC_SHA_MISMATCH")
        expected_type = _transition_type(
            expected_previous_state, entry["current_state"]
        )
        expected_first_seen, expected_persistence = _persistence(
            prior_entries, semantic_sha, mode
        )
        if expected_first_seen is None:
            expected_first_seen = entry["observed_at"]
        current_flow = entry["current_state"]["cross_market_flow"]
        previous_flow = (
            None
            if expected_previous_state is None
            else expected_previous_state["cross_market_flow"]
        )
        expected_transition = {
            "type": expected_type,
            "reversal": {
                "detected": expected_type == "REVERSAL",
                "previous_leader": (
                    None if previous_flow is None else previous_flow["relative_strength_leader"]
                ),
                "previous_laggard": (
                    None if previous_flow is None else previous_flow["relative_strength_laggard"]
                ),
                "current_leader": current_flow["relative_strength_leader"],
                "current_laggard": current_flow["relative_strength_laggard"],
            },
            "invalidation": {
                "detected": expected_type == "INVALIDATION",
                "reason": (
                    current_flow["actual_money_flow_reason"]
                    if expected_type == "INVALIDATION"
                    else None
                ),
            },
        }
        if (
            entry["first_seen"] != expected_first_seen
            or entry["persistence"] != expected_persistence
            or entry["transition"] != expected_transition
        ):
            fail("LEDGER_ENTRY_DERIVATION_MISMATCH")
        entry_unsigned = copy.deepcopy(entry)
        entry_sha = _sha(
            entry_unsigned.pop("entry_sha256", None), "LEDGER_ENTRY_SHA_INVALID"
        )
        if payload_sha256(entry_unsigned) != entry_sha:
            fail("LEDGER_ENTRY_SHA_MISMATCH")
        prior_entry = entry
        prior_entries.append(entry)
    expected_status = "EMPTY" if not entries else "HISTORY_OBSERVED"
    if (
        value["status"] != expected_status
        or value["current_state"]
        != (None if not entries else entries[-1]["current_state"])
        or value["latest_transition"]
        != (None if not entries else entries[-1]["transition"])
        or value["observation_mode_counts"] != expected_modes
        or value["counted_natural_observations"] != expected_modes["NATURAL"]
    ):
        fail("LEDGER_SUMMARY_DERIVATION_MISMATCH")
    return copy.deepcopy(value)


def apply_source(
    source_path: Path,
    observation_mode: str,
    previous_ledger: Optional[dict] = None,
    *,
    root: Path = ROOT,
    contract_path: Optional[Path] = None,
) -> dict:
    root = Path(root)
    contract_path = (
        root / "config" / "cross_market_flow_transition_ledger_contract.json"
        if contract_path is None
        else Path(contract_path)
    )
    contract = load_contract(contract_path)
    source_path = Path(source_path)
    packet = read_json(source_path, "SOURCE_READ_FAILED")
    validated = _validate_source_packet(packet, source_path, root, contract)
    ledger = validate_ledger(
        empty_ledger(contract, contract_path=contract_path, root=root)
        if previous_ledger is None
        else previous_ledger,
        contract,
    )
    duplicate = next(
        (
            entry
            for entry in ledger["entries"]
            if entry["lineage"]["input_payload_sha256"]
            == validated["packet_sha256"]
        ),
        None,
    )
    if duplicate is not None:
        return ledger
    current_day = _date(
        validated["effective_observation_date"], "SOURCE_EFFECTIVE_DATE_INVALID"
    )
    if ledger["entries"]:
        prior_day = _date(
            ledger["entries"][-1]["effective_observation_date"],
            "LEDGER_ENTRY_DATE_INVALID",
        )
        if current_day == prior_day:
            fail("SOURCE_REVISION_DRIFT_SAME_OBSERVATION_DATE")
        if current_day < prior_day:
            fail("SOURCE_STALE_NON_FORWARD_OBSERVATION")
    result = copy.deepcopy(ledger)
    entry = _build_entry(
        validated, observation_mode, result["entries"], contract
    )
    result["entries"].append(entry)
    result["status"] = "HISTORY_OBSERVED"
    result["ledger_revision"] = len(result["entries"])
    result["current_state"] = copy.deepcopy(entry["current_state"])
    result["latest_transition"] = copy.deepcopy(entry["transition"])
    result["observation_mode_counts"][observation_mode] += 1
    if observation_mode == "NATURAL":
        result["counted_natural_observations"] += 1
    result.pop("payload_sha256")
    result["payload_sha256"] = payload_sha256(result)
    return validate_ledger(result, contract)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_outputs(packet: dict, root: Path = ROOT) -> tuple[Path, Path]:
    packet = validate_ledger(packet)
    if not packet["entries"]:
        fail("EMPTY_LEDGER_WRITE_FORBIDDEN")
    last = packet["entries"][-1]
    evidence = (
        Path(root)
        / "evidence"
        / "portfolio"
        / "cross_market_flow_transition_ledger"
        / last["effective_observation_date"]
        / packet["payload_sha256"]
        / "packet.json"
    )
    latest = Path(root) / "data" / "latest_cross_market_flow_transition_ledger.json"
    rendered = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if evidence.exists() and evidence.read_text(encoding="utf-8") != rendered:
        fail("APPEND_ONLY_EVIDENCE_CONFLICT")
    write_json_atomic(evidence, packet)
    write_json_atomic(latest, packet)
    return evidence, latest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append one exact P2-COM-02 packet to the transition ledger"
    )
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--ledger", type=Path, default=LATEST_PATH)
    parser.add_argument(
        "--mode", choices=["NATURAL", "MANUAL", "RECOVERY", "REPLAY"]
    )
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    try:
        if args.verify is not None:
            validate_ledger(read_json(args.verify, "LEDGER_READ_FAILED"))
            print("PASS_CROSS_MARKET_FLOW_TRANSITION_LEDGER_VERIFIED")
            return 0
        if args.mode is None:
            fail("OBSERVATION_MODE_REQUIRED")
        previous = (
            read_json(args.ledger, "LEDGER_READ_FAILED")
            if args.ledger.exists()
            else None
        )
        result = apply_source(args.source, args.mode, previous)
        if args.write:
            evidence, latest = write_outputs(result)
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "ledger_revision": result["ledger_revision"],
                        "evidence": str(evidence.relative_to(ROOT)),
                        "latest": str(latest.relative_to(ROOT)),
                        "payload_sha256": result["payload_sha256"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (CrossMarketFlowTransitionLedgerError, OSError, TypeError, ValueError) as exc:
        print(f"Cross-market flow transition ledger failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
