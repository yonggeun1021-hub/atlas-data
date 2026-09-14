#!/usr/bin/env python3
"""Ratified wiring of rotation confirmation states (RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1).

* T1 ``rotation_selection``: only STRONG_CONFIRMED / STRONG_HELD entities;
  EMERGING_WATCH is returned as a display-only list, released entities are
  excluded.
* T2 C5 (rotation membership): PASS only when the asset's sector/bucket is
  STRONG_CONFIRMED / STRONG_HELD. The KR variant calls the unchanged
  ``universe/security_sector_membership.c5_rotation_membership`` with the
  confirmed/held theme ids as the selected membership ids.
* New buys by market state (allocation v2 new-buy table reused, no new
  numbers): RISK_ON and NEUTRAL permit new buys only in confirmed/held
  sectors; RISK_OFF / STRESS / UNKNOWN deny.
* Release (RULE.ROTATION.RELEASE_HANDLING.V1): new-buy stop flag only;
  ``forced_exit`` is always False and held positions follow existing
  stop-loss/take-profit rules.

Every function is fail-closed: a confirmation packet dated after the
evaluation date is refused (lookahead), and a stale/UNKNOWN/not-yet-effective
packet yields an empty selection with an ``UNKNOWN:<reason>`` status.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MARKET_STATES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", "UNKNOWN")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RC = _load("atlas_rotation_confirmation_for_wiring", HERE / "rotation_confirmation.py")
RotationConfirmationError = RC.RotationConfirmationError
_SSM = None


def _ssm():
    global _SSM
    if _SSM is None:
        _SSM = _load("atlas_security_sector_membership_for_wiring", ROOT / "universe" / "security_sector_membership.py")
    return _SSM


def _date(value: str, code: str) -> dt.date:
    return RC._date(value, code)


def _refs(policy: dict, market: str, roles: dict) -> list:
    ids = {
        "MARKET": policy["markets"][market]["rule_id"],
        "COMMON": policy["common"]["rule_id"],
        "RELEASE": policy["release_handling"]["rule_id"],
    }
    refs = [RC.rule_ref(policy, ids[key], role) for key, role in roles.items()]
    return sorted(refs, key=lambda item: (item["rule_id"], item["role"]))


def packet_blocker(packet: dict, market: str, evaluation_date: str, policy: dict) -> Optional[str]:
    RC.validate_packet(packet)
    if packet["market"] != market:
        RC._fail("CONFIRMATION_PACKET_MARKET_MISMATCH", f"{packet['market']}!={market}")
    if packet["policy"] != RC.policy_identity(policy):
        RC._fail("CONFIRMATION_PACKET_POLICY_MISMATCH")
    evaluation = _date(evaluation_date, "EVALUATION_DATE_INVALID")
    as_of = _date(packet["as_of_date"], "CONFIRMATION_AS_OF_INVALID")
    if as_of > evaluation:
        RC._fail("CONFIRMATION_PACKET_AFTER_EVALUATION_DATE", packet["as_of_date"])
    if evaluation_date < policy["decisions_effective_from"]:
        return "RULE_NOT_EFFECTIVE_FOR_EVALUATION_DATE"
    if packet["observation"]["status"] != "OBSERVED":
        return f"CONFIRMATION_OBSERVATION_UNKNOWN_{packet['observation']['unknown_reason']}"
    if (evaluation - as_of).days > packet["maximum_observation_gap_days"]:
        return "CONFIRMATION_STALE"
    return None


def _rows(packet: dict, states: tuple) -> list:
    rows = []
    for scope in packet["scopes"]:
        for entity in scope["entities"]:
            if entity["state"] in states:
                rows.append({
                    "scope_id": scope["scope_id"],
                    "entity_id": entity["entity_id"],
                    "label": entity["source_identity"],
                    "state": entity["state"],
                    "state_since_date": entity["state_since_date"],
                    "observations_in_state": entity["observations_in_state"],
                })
    return rows


def t1_rotation_selection(packet: dict, market: str, evaluation_date: str, policy: Optional[dict] = None) -> dict:
    policy = RC.load_policy() if policy is None else policy
    blocker = packet_blocker(packet, market, evaluation_date, policy)
    result = {
        "market": market,
        "evaluation_date": evaluation_date,
        "confirmation_as_of_date": packet["as_of_date"],
        "confirmation_payload_sha256": packet["payload_sha256"],
        "rule_refs": _refs(policy, market, {"MARKET": "APPLIED", "COMMON": "APPLIED"}),
    }
    if blocker is not None:
        return result | {
            "rotation_selection_status": f"UNKNOWN:{blocker}",
            "rotation_selection": [], "emerging_watch_display_only": [], "excluded_released": [],
        }
    selection = _rows(packet, tuple(policy["common"]["t1_selection_states"]))
    return result | {
        "rotation_selection_status": "SELECTED" if selection else "EMPTY:NO_STRONG_CONFIRMED_OR_HELD",
        "rotation_selection": selection,
        "emerging_watch_display_only": _rows(packet, ("EMERGING_WATCH",)),
        "excluded_released": _rows(packet, ("STRONG_RELEASED",)),
    }


def _entity(packet: dict, scope_id: Optional[str], entity_id: str) -> Optional[dict]:
    matches = [
        entity for scope in packet["scopes"] for entity in scope["entities"]
        if entity["entity_id"] == entity_id and (scope_id is None or scope["scope_id"] == scope_id)
    ]
    if len(matches) > 1:
        RC._fail("CONFIRMATION_ENTITY_AMBIGUOUS", entity_id)
    return matches[0] if matches else None


def c5_from_entity(packet: dict, market: str, entity_id: str, evaluation_date: str,
                   scope_id: Optional[str] = None, policy: Optional[dict] = None) -> dict:
    """Generic C5 for a caller-resolved sector/bucket entity (CRYPTO bucket, US SPDR, KR theme)."""
    policy = RC.load_policy() if policy is None else policy
    blocker = packet_blocker(packet, market, evaluation_date, policy)
    base = {
        "condition": "C5_ROTATION_MEMBERSHIP", "market": market, "entity_id": entity_id,
        "evaluation_date": evaluation_date, "confirmation_as_of_date": packet["as_of_date"],
        "confirmation_payload_sha256": packet["payload_sha256"], "sector_state": None,
    }
    if blocker is not None:
        return base | {"result": "FAIL", "reason": f"ROTATION_CONFIRMATION_UNKNOWN:{blocker}",
                       "rule_refs": _refs(policy, market, {"MARKET": "BLOCKED_BY", "COMMON": "BLOCKED_BY"})}
    entity = _entity(packet, scope_id, entity_id)
    if entity is None:
        return base | {"result": "FAIL", "reason": "ENTITY_NOT_IN_CONFIRMATION_PACKET",
                       "rule_refs": _refs(policy, market, {"MARKET": "BLOCKED_BY", "COMMON": "BLOCKED_BY"})}
    state = entity["state"]
    if state in policy["common"]["t2_c5_pass_states"]:
        return base | {"sector_state": state, "result": "PASS", "reason": f"SECTOR_{state}",
                       "rule_refs": _refs(policy, market, {"MARKET": "APPLIED", "COMMON": "APPLIED"})}
    return base | {"sector_state": state, "result": "FAIL", "reason": f"SECTOR_NOT_STRONG_CONFIRMED_OR_HELD:{state}",
                   "rule_refs": _refs(policy, market, {"MARKET": "APPLIED", "COMMON": "BLOCKED_BY"})}


def c5_rotation_membership_kr(document: dict, asset_id: str, kr_packet: dict, t: str,
                              evaluation_date: str, policy: Optional[dict] = None) -> dict:
    """KR C5: unchanged membership evaluation with confirmed/held theme ids as selection."""
    policy = RC.load_policy() if policy is None else policy
    selection = t1_rotation_selection(kr_packet, "KR", evaluation_date, policy)
    selected_ids = sorted(row["entity_id"] for row in selection["rotation_selection"])
    result = _ssm().c5_rotation_membership(document, asset_id, selected_ids, t)
    states = {
        entity["entity_id"]: entity["state"]
        for scope in kr_packet["scopes"] for entity in scope["entities"]
    }
    return dict(result) | {
        "rotation_confirmation": {
            "selection_status": selection["rotation_selection_status"],
            "confirmation_as_of_date": kr_packet["as_of_date"],
            "confirmation_payload_sha256": kr_packet["payload_sha256"],
            "membership_sector_state": states.get(result.get("membership_id")),
            "selected_membership_ids": selected_ids,
        },
        "rule_refs": _refs(
            policy, "KR",
            {"MARKET": "APPLIED", "COMMON": "APPLIED" if result["result"] == "PASS" else "BLOCKED_BY"},
        ),
    }


def new_buy_permission(packet: dict, market: str, entity_id: str, market_state: str, evaluation_date: str,
                       scope_id: Optional[str] = None, policy: Optional[dict] = None) -> dict:
    policy = RC.load_policy() if policy is None else policy
    if market_state not in MARKET_STATES:
        RC._fail("MARKET_STATE_INVALID", str(market_state))
    blocker = packet_blocker(packet, market, evaluation_date, policy)
    entity = None if blocker is not None else _entity(packet, scope_id, entity_id)
    sector_state = None if entity is None else entity["state"]
    rule = policy["common"]["new_buy_by_market_state"][market_state]
    release = policy["release_handling"]
    released = sector_state == "STRONG_RELEASED"
    if rule == "DENY":
        permission, reason = "DENY", f"MARKET_STATE_{market_state}_DENIES_NEW_BUYS"
        roles = {"MARKET": "APPLIED", "COMMON": "BLOCKED_BY"}
    elif blocker is not None:
        permission, reason = "DENY", f"ROTATION_CONFIRMATION_UNKNOWN:{blocker}"
        roles = {"MARKET": "BLOCKED_BY", "COMMON": "BLOCKED_BY"}
    elif entity is None:
        permission, reason = "DENY", "ENTITY_NOT_IN_CONFIRMATION_PACKET"
        roles = {"MARKET": "BLOCKED_BY", "COMMON": "BLOCKED_BY"}
    elif sector_state in policy["decision_eligible_states"]:
        permission = "PERMIT_SELECTIVE" if market_state == "NEUTRAL" else "PERMIT"
        reason = f"SECTOR_{sector_state}"
        roles = {"MARKET": "APPLIED", "COMMON": "APPLIED"}
    elif released:
        permission, reason = "DENY", "STRENGTH_RELEASED_NEW_BUY_STOP"
        roles = {"MARKET": "APPLIED", "COMMON": "APPLIED", "RELEASE": "BLOCKED_BY"}
    else:
        permission, reason = "DENY", f"SECTOR_NOT_STRONG_CONFIRMED_OR_HELD:{sector_state}"
        roles = {"MARKET": "APPLIED", "COMMON": "BLOCKED_BY"}
    if released and "RELEASE" not in roles:
        roles["RELEASE"] = "APPLIED"
    return {
        "market": market,
        "entity_id": entity_id,
        "evaluation_date": evaluation_date,
        "market_state": market_state,
        "sector_state": sector_state,
        "new_buy_permission": permission,
        "reason": reason,
        "release_new_buy_stop": bool(released and release["new_buy_stop"]),
        "forced_exit": release["forced_exit"],
        "held_position_action": release["held_position_action"],
        "exit_review_display": released,
        "allocation_v2_numbers_changed": policy["common"]["allocation_v2_numbers_changed"],
        "confirmation_as_of_date": packet["as_of_date"],
        "confirmation_payload_sha256": packet["payload_sha256"],
        "rule_refs": _refs(policy, market, roles),
    }


def load_latest_packet(market: str, root: Path = ROOT) -> dict:
    return RC.validate_packet(copy.deepcopy(RC._read_json(RC.latest_path(root, market))))
