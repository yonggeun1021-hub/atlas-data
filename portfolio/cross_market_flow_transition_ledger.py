#!/usr/bin/env python3
"""P2-COM-03 append-only cross-market flow transition ledger.

The ledger consumes only the validated P2-COM-02 output packet.  It records
structural state history and exact lineage without creating confirmation,
allocation, capital, action, order, Production, or Trading authority.

Contract /2 separates the two clocks that /1 mixed into one order key.  Ledger
order, revision drift, and staleness are decided only by
``source_generated_date_kst`` -- the producer ``generated_at`` converted to a
fixed +09:00 date.  ``comparison_as_of_date`` stays a market fact inside
``current_state`` and never orders anything.  A /2 ledger always continues the
frozen /1 chain through a verified predecessor projection, so ``first_seen``,
persistence, observation counts, and revision height survive the migration.
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
LEDGER_SCHEMA_VERSION = "cross_market_flow_transition_ledger_packet/2"
CONTRACT_VERSION = "cross_market_flow_transition_ledger/2"
PREDECESSOR_CONTRACT_VERSION = "cross_market_flow_transition_ledger/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KST = dt.timezone(dt.timedelta(hours=9))
OBSERVATION_COUNT_SCOPE = "CUMULATIVE_INCLUDING_PREDECESSOR"
UNRESOLVED_BOUNDARIES = [
    "CONFIRMATION_POLICY_UNRATIFIED",
    "NUMERIC_THRESHOLD_ABSENT",
    "MARKET_ALLOCATION_UNAUTHORIZED",
    "CAPITAL_ACTION_ORDER_PRODUCTION_TRADING_UNAUTHORIZED",
]

# The canonical /1 chain this repository must continue.  Pinned in code as well
# as in the contract file so a contract edit alone cannot silently repoint the
# chain.  This is an in-repository anchor only: a writer that can change both
# code and evidence is still outside what /2 defends against (F2).
PRODUCTION_PREDECESSOR = {
    "contract_version": PREDECESSOR_CONTRACT_VERSION,
    "evidence_path": (
        "evidence/portfolio/cross_market_flow_transition_ledger/2026-09-02/"
        "58f34d06c92d66d96d64a0deb0261462aaae06a4ac99da7c43d4d2cfc35161cf/packet.json"
    ),
    "evidence_file_sha256": (
        "5f6c0e26d8ac57dbc4b145924013f63887ceb513a2fcb5a3845bb47249eb2df5"
    ),
    "payload_sha256": (
        "58f34d06c92d66d96d64a0deb0261462aaae06a4ac99da7c43d4d2cfc35161cf"
    ),
    "tail_entry_sha256": (
        "0bee822d5974d91323ec273f1c506d9c4caccf68576d9b8d46b2f7b94aa1f294"
    ),
    "height": 1,
}


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
    """Every /2 contract field except the predecessor identity, which varies by
    chain and is validated separately."""
    return {
        "schema_version": 2,
        "contract_version": CONTRACT_VERSION,
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
        "observation_order_key": "source_generated_date_kst",
        "observation_order_key_policy": (
            "PRODUCER_GENERATED_AT_CONVERTED_TO_FIXED_PLUS_0900_DATE"
        ),
        "market_comparison_date_policy": (
            "COMPARISON_AS_OF_DATE_IS_STATE_FACT_ONLY_NEVER_AN_ORDER_KEY"
        ),
        "same_source_generated_date_policy": (
            "IDENTICAL_SOURCE_NOOP_OTHERWISE_REVISION_DRIFT_FAIL_CLOSED"
        ),
        "confirmation_policy": "UNRATIFIED_CONFIRMED_AT_NULL",
        "stale_policy": (
            "NON_FORWARD_SOURCE_GENERATED_DATE_KST_FAIL_CLOSED"
            "_NO_WALL_CLOCK_THRESHOLD"
        ),
        "observation_count_scope": OBSERVATION_COUNT_SCOPE,
        "production_fresh_chain_allowed": False,
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


def _validate_predecessor_identity(value: object) -> dict:
    required = {
        "contract_version",
        "evidence_path",
        "evidence_file_sha256",
        "payload_sha256",
        "tail_entry_sha256",
        "height",
    }
    if not isinstance(value, dict) or set(value) != required:
        fail("CONTRACT_PREDECESSOR_FIELDS_MISMATCH")
    if value["contract_version"] != PREDECESSOR_CONTRACT_VERSION:
        fail("CONTRACT_PREDECESSOR_VERSION_INVALID")
    path = value["evidence_path"]
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or ".." in Path(path).parts
    ):
        fail("CONTRACT_PREDECESSOR_PATH_INVALID")
    for key in ("evidence_file_sha256", "payload_sha256", "tail_entry_sha256"):
        _sha(value[key], "CONTRACT_PREDECESSOR_SHA_INVALID")
    height = value["height"]
    if not isinstance(height, int) or isinstance(height, bool) or height < 1:
        fail("CONTRACT_PREDECESSOR_HEIGHT_INVALID")
    return copy.deepcopy(value)


def validate_contract(value: object, *, production: bool = False) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected) | {"predecessor"}:
        fail("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            fail("CONTRACT_FIELD_MISMATCH", key)
    predecessor = _validate_predecessor_identity(value.get("predecessor"))
    if production and predecessor != PRODUCTION_PREDECESSOR:
        fail("CONTRACT_PRODUCTION_PREDECESSOR_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    path = Path(path)
    production = path.resolve() == CONTRACT_PATH.resolve()
    return validate_contract(
        read_json(path, "CONTRACT_READ_FAILED"), production=production
    )


def source_generated_date_kst(packet: dict) -> str:
    """The ledger order key: the producer generated_at as a fixed +09:00 date.

    Asia/Seoul has no daylight saving, so a fixed offset is exact and needs no
    time-zone database at runtime.  The key names what it proves -- the date the
    source packet was generated -- not a ledger runtime observation date.
    """
    generated_at = _timestamp(
        packet.get("generated_at"), "SOURCE_GENERATED_AT_INVALID"
    )
    return generated_at.astimezone(KST).date().isoformat()


def verify_predecessor_ledger(value: object) -> dict:
    """Schema-agnostic hash-chain verification of the frozen /1 ledger.

    The /1 semantics are deliberately not re-implemented here: only the hashes
    that /1 itself committed to are recomputed, so this stays correct without
    carrying a second frozen validator.
    """
    if not isinstance(value, dict):
        fail("PREDECESSOR_LEDGER_INVALID")
    if value.get("contract_version") != PREDECESSOR_CONTRACT_VERSION:
        fail("PREDECESSOR_CONTRACT_VERSION_INVALID")
    unsigned = copy.deepcopy(value)
    claimed = _sha(unsigned.pop("payload_sha256", None), "PREDECESSOR_SHA_INVALID")
    if payload_sha256(unsigned) != claimed:
        fail("PREDECESSOR_SHA_MISMATCH")
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        fail("PREDECESSOR_ENTRIES_INVALID")
    previous_sha = None
    for entry in entries:
        if not isinstance(entry, dict):
            fail("PREDECESSOR_ENTRY_INVALID")
        unsigned_entry = copy.deepcopy(entry)
        entry_sha = _sha(
            unsigned_entry.pop("entry_sha256", None), "PREDECESSOR_ENTRY_SHA_INVALID"
        )
        if payload_sha256(unsigned_entry) != entry_sha:
            fail("PREDECESSOR_ENTRY_SHA_MISMATCH")
        if entry.get("previous_entry_sha256") != previous_sha:
            fail("PREDECESSOR_ENTRY_CHAIN_INVALID")
        previous_sha = entry_sha
    return copy.deepcopy(value)


def derive_predecessor(ledger: dict, identity: dict) -> dict:
    """Project the frozen /1 chain into exactly what /2 needs to continue it."""
    entries = ledger["entries"]
    modes = {"NATURAL": 0, "MANUAL": 0, "RECOVERY": 0, "REPLAY": 0}
    tally: dict = {}
    source_payload_sha256: list[str] = []
    for entry in entries:
        semantic_sha = _sha(
            entry.get("current_semantic_state_sha256"),
            "PREDECESSOR_ENTRY_SEMANTIC_SHA_INVALID",
        )
        observed_at = entry.get("observed_at")
        _timestamp(observed_at, "PREDECESSOR_ENTRY_TIME_INVALID")
        mode = entry.get("observation_mode")
        if mode not in modes:
            fail("PREDECESSOR_ENTRY_MODE_INVALID")
        modes[mode] += 1
        item = tally.setdefault(
            semantic_sha,
            {
                "first_seen": observed_at,
                "observation_count_total": 0,
                "natural_count_total": 0,
            },
        )
        item["observation_count_total"] += 1
        if mode == "NATURAL":
            item["natural_count_total"] += 1
        lineage = entry.get("lineage")
        if not isinstance(lineage, dict):
            fail("PREDECESSOR_ENTRY_LINEAGE_INVALID")
        source_payload_sha256.append(
            _sha(
                lineage.get("input_payload_sha256"),
                "PREDECESSOR_ENTRY_SOURCE_SHA_INVALID",
            )
        )
    tail = entries[-1]
    lineage = tail["lineage"]
    packet = lineage.get("input_packet")
    if not isinstance(packet, dict):
        fail("PREDECESSOR_TAIL_SOURCE_PACKET_INVALID")
    persistence = tail.get("persistence")
    if not isinstance(persistence, dict):
        fail("PREDECESSOR_TAIL_PERSISTENCE_INVALID")
    for key in ("current_streak_observation_count", "current_streak_natural_count"):
        if not isinstance(persistence.get(key), int) or isinstance(
            persistence.get(key), bool
        ):
            fail("PREDECESSOR_TAIL_PERSISTENCE_INVALID", key)
    return {
        "contract_version": identity["contract_version"],
        "evidence_path": identity["evidence_path"],
        "evidence_file_sha256": identity["evidence_file_sha256"],
        "payload_sha256": identity["payload_sha256"],
        "tail_entry_sha256": identity["tail_entry_sha256"],
        "height": len(entries),
        "observation_mode_counts": modes,
        "counted_natural_observations": modes["NATURAL"],
        "state_tally": tally,
        "source_payload_sha256": source_payload_sha256,
        "tail": {
            "entry_sha256": tail["entry_sha256"],
            "observed_at": tail["observed_at"],
            # re-derived from the frozen source packet, never copied from the
            # /1 order key, which meant something else
            "source_generated_date_kst": source_generated_date_kst(packet),
            "current_semantic_state_sha256": tail["current_semantic_state_sha256"],
            "current_state": copy.deepcopy(tail["current_state"]),
            "persistence": copy.deepcopy(persistence),
        },
    }


def load_predecessor(contract: dict, root: Path = ROOT) -> dict:
    identity = _validate_predecessor_identity(contract.get("predecessor"))
    path = Path(root) / identity["evidence_path"]
    if not path.is_file():
        fail("PREDECESSOR_REQUIRED_MISSING", identity["evidence_path"])
    if file_sha256(path) != identity["evidence_file_sha256"]:
        fail("PREDECESSOR_EVIDENCE_FILE_SHA_MISMATCH")
    ledger = verify_predecessor_ledger(
        read_json(path, "PREDECESSOR_READ_FAILED")
    )
    if ledger["payload_sha256"] != identity["payload_sha256"]:
        fail("PREDECESSOR_PAYLOAD_SHA_MISMATCH")
    if len(ledger["entries"]) != identity["height"]:
        fail("PREDECESSOR_HEIGHT_MISMATCH")
    if ledger["entries"][-1].get("entry_sha256") != identity["tail_entry_sha256"]:
        fail("PREDECESSOR_TAIL_SHA_MISMATCH")
    return derive_predecessor(ledger, identity)


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
    _timestamp(packet.get("generated_at"), "SOURCE_GENERATED_AT_INVALID")
    state = _current_state(packet)
    # comparison_as_of_date stays a market fact inside current_state; it never
    # orders the ledger.  Order comes only from the producer generated_at.
    order_key = source_generated_date_kst(packet)
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
        "source_generated_date_kst": order_key,
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
        "contract_version": CONTRACT_VERSION,
    }


def empty_ledger(
    contract: Optional[dict] = None,
    predecessor: Optional[dict] = None,
    *,
    contract_path: Path = CONTRACT_PATH,
    root: Path = ROOT,
) -> dict:
    """A /2 ledger with no observation of its own yet.

    It always carries a predecessor: this repository has a ratified /1 history,
    so a production fresh chain is forbidden.  This shape is never written --
    write_outputs refuses an entry-less ledger -- it only seeds the first append.
    """
    contract = (
        load_contract(contract_path)
        if contract is None
        else validate_contract(contract)
    )
    if predecessor is None:
        predecessor = load_predecessor(contract, root)
    packet = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "status": "EMPTY",
        "ledger_id": "CROSS_MARKET_FLOW",
        "ledger_revision": predecessor["height"],
        "contract": _contract_lineage(contract_path, root),
        "predecessor": copy.deepcopy(predecessor),
        "entries": [],
        "current_state": None,
        "latest_transition": None,
        "observation_mode_counts": copy.deepcopy(
            predecessor["observation_mode_counts"]
        ),
        "counted_natural_observations": predecessor["counted_natural_observations"],
        "observation_count_scope": contract["observation_count_scope"],
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": list(UNRESOLVED_BOUNDARIES),
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


def _persistence(
    entries: list[dict],
    semantic_sha: str,
    mode: str,
    predecessor: dict,
) -> tuple[str, dict]:
    """Continue the predecessor's tallies instead of restarting them.

    ``first_seen`` and the totals come from the predecessor projection when the
    same semantic state was already observed on the /1 chain; the streak
    continues from the /1 tail when it carried the same state.
    """
    prior = predecessor["state_tally"].get(semantic_sha)
    matching = [
        item for item in entries if item["current_semantic_state_sha256"] == semantic_sha
    ]
    if prior is not None:
        first_seen = prior["first_seen"]
    elif matching:
        first_seen = matching[0]["observed_at"]
    else:
        first_seen = None
    if entries:
        previous_persistence = entries[-1]["persistence"]
        previous_same = entries[-1]["current_semantic_state_sha256"] == semantic_sha
    else:
        previous_persistence = predecessor["tail"]["persistence"]
        previous_same = (
            predecessor["tail"]["current_semantic_state_sha256"] == semantic_sha
        )
    if previous_same:
        streak_all = previous_persistence["current_streak_observation_count"] + 1
        streak_natural = previous_persistence["current_streak_natural_count"] + (
            1 if mode == "NATURAL" else 0
        )
    else:
        streak_all = 1
        streak_natural = 1 if mode == "NATURAL" else 0
    prior_total = 0 if prior is None else prior["observation_count_total"]
    prior_natural = 0 if prior is None else prior["natural_count_total"]
    return first_seen, {
        "state_observation_count_total": prior_total + len(matching) + 1,
        "state_natural_observation_count_total": (
            prior_natural
            + sum(item["observation_mode"] == "NATURAL" for item in matching)
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
    predecessor: dict,
) -> dict:
    if mode not in contract["observation_modes"]:
        fail("OBSERVATION_MODE_INVALID")
    previous = entries[-1] if entries else None
    if previous is None:
        previous_state = copy.deepcopy(predecessor["tail"]["current_state"])
        previous_entry_sha256 = predecessor["tail"]["entry_sha256"]
    else:
        previous_state = previous["current_state"]
        previous_entry_sha256 = previous["entry_sha256"]
    transition_type = _transition_type(previous_state, validated["state"])
    semantic_sha = validated["semantic_state_sha256"]
    first_seen, persistence = _persistence(entries, semantic_sha, mode, predecessor)
    if first_seen is None:
        first_seen = validated["generated_at"]
    previous_flow = None if previous_state is None else previous_state["cross_market_flow"]
    current_flow = validated["state"]["cross_market_flow"]
    entry = {
        "ledger_revision": predecessor["height"] + len(entries) + 1,
        "source_generated_date_kst": validated["source_generated_date_kst"],
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
        "previous_entry_sha256": previous_entry_sha256,
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
    _timestamp(packet.get("generated_at"), "LEDGER_SOURCE_GENERATED_AT_INVALID")
    source_state = _current_state(packet)
    order_key = source_generated_date_kst(packet)
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
        or entry.get("source_generated_date_kst") != order_key
    ):
        fail("LEDGER_SOURCE_LINEAGE_MISMATCH")
    _sha(lineage["input_file_sha256"], "LEDGER_SOURCE_FILE_SHA_INVALID")
    if entry.get("current_state") != source_state:
        fail("LEDGER_STATE_SOURCE_MISMATCH")


def validate_ledger(
    value: object,
    contract: Optional[dict] = None,
    *,
    root: Path = ROOT,
    contract_path: Path = CONTRACT_PATH,
) -> dict:
    contract = (
        load_contract(contract_path)
        if contract is None
        else validate_contract(contract)
    )
    required = {
        "schema_version",
        "contract_version",
        "status",
        "ledger_id",
        "ledger_revision",
        "contract",
        "predecessor",
        "entries",
        "current_state",
        "latest_transition",
        "observation_mode_counts",
        "counted_natural_observations",
        "observation_count_scope",
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
        or value["observation_count_scope"] != contract["observation_count_scope"]
        or value["authority"] != contract["authority"]
        or value["unresolved_boundaries"] != UNRESOLVED_BOUNDARIES
    ):
        fail("LEDGER_CONTRACT_MISMATCH")
    contract_lineage = value.get("contract")
    if (
        not isinstance(contract_lineage, dict)
        or set(contract_lineage) != {"path", "sha256", "contract_version"}
        or contract_lineage["path"]
        != "config/cross_market_flow_transition_ledger_contract.json"
        or contract_lineage["sha256"] != file_sha256(Path(contract_path))
        or contract_lineage["contract_version"] != contract["contract_version"]
    ):
        fail("LEDGER_CONTRACT_LINEAGE_INVALID")
    _sha(contract_lineage["sha256"], "LEDGER_CONTRACT_SHA_INVALID")
    # The declared predecessor is never trusted: it must equal the projection
    # re-derived from the pinned, hash-verified /1 evidence file.
    predecessor = load_predecessor(contract, root)
    if value["predecessor"] != predecessor:
        fail("LEDGER_PREDECESSOR_MISMATCH")
    entries = value.get("entries")
    if not isinstance(entries, list):
        fail("LEDGER_ENTRIES_INVALID")
    if value.get("ledger_revision") != predecessor["height"] + len(entries):
        fail("LEDGER_REVISION_INVALID")
    expected_modes = copy.deepcopy(predecessor["observation_mode_counts"])
    prior_entry = None
    prior_date = _date(
        predecessor["tail"]["source_generated_date_kst"],
        "PREDECESSOR_TAIL_DATE_INVALID",
    )
    prior_entries: list[dict] = []
    for index, entry in enumerate(entries, 1):
        entry_required = {
            "ledger_revision",
            "source_generated_date_kst",
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
        if entry["ledger_revision"] != predecessor["height"] + index:
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
            entry["source_generated_date_kst"], "LEDGER_ENTRY_DATE_INVALID"
        )
        _timestamp(entry["observed_at"], "LEDGER_ENTRY_TIME_INVALID")
        if day <= prior_date:
            fail("LEDGER_NON_FORWARD_SOURCE_GENERATED_DATE")
        prior_date = day
        if prior_entry is None:
            expected_previous_state = predecessor["tail"]["current_state"]
            expected_previous_sha = predecessor["tail"]["entry_sha256"]
        else:
            expected_previous_state = prior_entry["current_state"]
            expected_previous_sha = prior_entry["entry_sha256"]
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
        if expected_type == "INITIAL":
            # every /2 entry continues the predecessor chain
            fail("LEDGER_ENTRY_INITIAL_TRANSITION_FORBIDDEN")
        expected_first_seen, expected_persistence = _persistence(
            prior_entries, semantic_sha, mode, predecessor
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


def apply_observation(
    source_path: Path,
    observation_mode: str,
    previous_ledger: Optional[dict] = None,
    *,
    root: Path = ROOT,
    contract_path: Optional[Path] = None,
) -> dict:
    """Decide what one P2-COM-02 packet does to the canonical ledger pointer.

    Returns an envelope rather than a bare ledger because "nothing changes" is a
    distinct outcome from "a new entry was appended": an identical /1 source must
    leave the /1 pointer untouched instead of being rewritten into /2 shape.

    action is one of:
      V1_NOOP_KEEP  identical source already on the /1 chain -- write nothing
      V2_NOOP       identical source already on the /2 chain -- write nothing
      V2_BOOTSTRAP  first forward source after /1 -- the pointer becomes /2
      V2_APPEND     ordinary forward append on an existing /2 chain
    """
    root = Path(root)
    contract_path = (
        root / "config" / "cross_market_flow_transition_ledger_contract.json"
        if contract_path is None
        else Path(contract_path)
    )
    contract = load_contract(contract_path)
    predecessor = load_predecessor(contract, root)
    source_path = Path(source_path)
    packet = read_json(source_path, "SOURCE_READ_FAILED")
    validated = _validate_source_packet(packet, source_path, root, contract)

    if previous_ledger is None:
        # This repository has a ratified /1 history.  A missing pointer is a
        # recovery problem, never a licence to start a new chain.
        fail("PREDECESSOR_REQUIRED_MISSING", "canonical ledger pointer absent")
    if not isinstance(previous_ledger, dict):
        fail("LEDGER_READ_FAILED")
    version = previous_ledger.get("contract_version")

    if version == PREDECESSOR_CONTRACT_VERSION:
        frozen = verify_predecessor_ledger(previous_ledger)
        if frozen["payload_sha256"] != predecessor["payload_sha256"]:
            fail("PREDECESSOR_LEDGER_IDENTITY_MISMATCH")
        if validated["packet_sha256"] in predecessor["source_payload_sha256"]:
            return {"action": "V1_NOOP_KEEP", "ledger": copy.deepcopy(previous_ledger)}
        _assert_forward(validated, predecessor["tail"]["source_generated_date_kst"])
        ledger = empty_ledger(
            contract, predecessor, contract_path=contract_path, root=root
        )
        action = "V2_BOOTSTRAP"
    elif version == contract["contract_version"]:
        ledger = validate_ledger(
            previous_ledger, contract, root=root, contract_path=contract_path
        )
        consumed = set(predecessor["source_payload_sha256"]) | {
            entry["lineage"]["input_payload_sha256"] for entry in ledger["entries"]
        }
        if validated["packet_sha256"] in consumed:
            return {"action": "V2_NOOP", "ledger": ledger}
        last = (
            ledger["entries"][-1]["source_generated_date_kst"]
            if ledger["entries"]
            else predecessor["tail"]["source_generated_date_kst"]
        )
        _assert_forward(validated, last)
        action = "V2_APPEND"
    else:
        fail("LEDGER_CONTRACT_VERSION_UNSUPPORTED", str(version))

    result = copy.deepcopy(ledger)
    entry = _build_entry(
        validated, observation_mode, result["entries"], contract, predecessor
    )
    result["entries"].append(entry)
    result["status"] = "HISTORY_OBSERVED"
    result["ledger_revision"] = predecessor["height"] + len(result["entries"])
    result["current_state"] = copy.deepcopy(entry["current_state"])
    result["latest_transition"] = copy.deepcopy(entry["transition"])
    result["observation_mode_counts"][observation_mode] += 1
    if observation_mode == "NATURAL":
        result["counted_natural_observations"] += 1
    result.pop("payload_sha256")
    result["payload_sha256"] = payload_sha256(result)
    return {
        "action": action,
        "ledger": validate_ledger(
            result, contract, root=root, contract_path=contract_path
        ),
    }


def _assert_forward(validated: dict, last_key: str) -> None:
    current_day = _date(
        validated["source_generated_date_kst"], "SOURCE_GENERATED_DATE_INVALID"
    )
    prior_day = _date(last_key, "LEDGER_ENTRY_DATE_INVALID")
    if current_day == prior_day:
        fail("SOURCE_REVISION_DRIFT_SAME_SOURCE_GENERATED_DATE")
    if current_day < prior_day:
        fail("SOURCE_STALE_NON_FORWARD_SOURCE_GENERATED_DATE")


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
    contract_path = Path(root) / "config" / (
        "cross_market_flow_transition_ledger_contract.json"
    )
    packet = validate_ledger(packet, root=root, contract_path=contract_path)
    if not packet["entries"]:
        fail("EMPTY_LEDGER_WRITE_FORBIDDEN")
    last = packet["entries"][-1]
    evidence = (
        Path(root)
        / "evidence"
        / "portfolio"
        / "cross_market_flow_transition_ledger"
        / last["source_generated_date_kst"]
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
            candidate = read_json(args.verify, "LEDGER_READ_FAILED")
            version = candidate.get("contract_version")
            # the canonical pointer is still /1 until the first forward source
            if version == PREDECESSOR_CONTRACT_VERSION:
                verify_predecessor_ledger(candidate)
                contract = load_contract()
                if candidate["payload_sha256"] != contract["predecessor"][
                    "payload_sha256"
                ]:
                    fail("PREDECESSOR_LEDGER_IDENTITY_MISMATCH")
                load_predecessor(contract)
                print("PASS_CROSS_MARKET_FLOW_TRANSITION_LEDGER_VERIFIED_PREDECESSOR")
                return 0
            validate_ledger(candidate)
            print("PASS_CROSS_MARKET_FLOW_TRANSITION_LEDGER_VERIFIED")
            return 0
        if args.mode is None:
            fail("OBSERVATION_MODE_REQUIRED")
        previous = (
            read_json(args.ledger, "LEDGER_READ_FAILED")
            if args.ledger.exists()
            else None
        )
        result = apply_observation(args.source, args.mode, previous)
        if result["action"] in {"V1_NOOP_KEEP", "V2_NOOP"}:
            # identical source: nothing is written, nothing is rewritten
            print(
                json.dumps(
                    {
                        "action": result["action"],
                        "ledger_contract_version": result["ledger"][
                            "contract_version"
                        ],
                        "status": "NO_OP_IDENTICAL_SOURCE_PACKET",
                        "written": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        ledger = result["ledger"]
        if args.write:
            evidence, latest = write_outputs(ledger)
            print(
                json.dumps(
                    {
                        "action": result["action"],
                        "status": ledger["status"],
                        "ledger_revision": ledger["ledger_revision"],
                        "evidence": str(evidence.relative_to(ROOT)),
                        "latest": str(latest.relative_to(ROOT)),
                        "payload_sha256": ledger["payload_sha256"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            print(json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (CrossMarketFlowTransitionLedgerError, OSError, TypeError, ValueError) as exc:
        print(f"Cross-market flow transition ledger failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
