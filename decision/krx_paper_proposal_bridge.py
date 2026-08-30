#!/usr/bin/env python3
"""Hash-bound KRX briefing/Shadow to P8-13 PAPER proposal bridge.

The bridge packages a human-readable briefing and a machine-readable proposal
from the same evidence basis.  It grants no policy, portfolio, ledger, broker,
or trading authority.  Until the exact Wave1 sources are merged, a strategy
policy is explicitly ratified, and COMMON_SAFETY plus KRX_SHADOW pass, the
authoritative proposal is always ``NONE``.

No network or broker client is imported by this module.  A future non-NONE
result may contain only an internal virtual-ledger draft whose KIS submission,
exchange, order, PAPER-write, REAL, Production, and trading authorities remain
false.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_paper_proposal_bridge_contract.json"
POLICY_PACKET_PATH = ROOT / "config" / "krx_paper_policy_ratification_packet.json"
DEFAULT_INPUT_PATH = ROOT / "test" / "fixtures" / "krx_paper_proposal_bridge_input.json"

CONTRACT_VERSION = "krx_paper_proposal_bridge/1"
INPUT_SCHEMA_VERSION = "krx_paper_proposal_bridge_input/1"
OUTPUT_SCHEMA_VERSION = "krx_paper_proposal_bridge_packet/1"
POLICY_PACKET_SCHEMA_VERSION = "krx_paper_policy_ratification_packet/1"
PROPOSAL_SCHEMA_VERSION = "krx_paper_proposal/1"
LEDGER_DRAFT_SCHEMA_VERSION = "krx_internal_virtual_ledger_draft/1"

SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SYMBOL_RE = re.compile(r"[0-9]{6}")
ALLOWED_GATE_RESULTS = {"PASS", "FAIL", "UNKNOWN"}
ALLOWED_STATES = [
    "LOCKED",
    "SHADOW",
    "PAPER_CANARY",
    "PAPER_ACTIVE",
    "PAPER_VALIDATED",
    "LIVE_REVIEW",
]
ALLOWED_ACTIONS = {"ENTER", "HOLD", "EXIT"}

EXPECTED_SOURCE_PINS = {
    "public_krx_gate": {
        "merge_commit": "016a2889c503066a3a07180e8d12b9da81869e7b",
        "assessment_schema_version": "krx_paper_gate_assessment/1",
        "assessment_sha256": "780690500832dceffed4ede9059da4c9cb4e8d565042878ee1c503fc3e724e07",
        "common_contract_sha256": "83ceb7bfcb05d5b8c492b6d98c8a2f0d73274c87a228a9369291db95adef8411",
        "market_contract_sha256": "e8275ac083c5624946718da0dc7db7f01e9700a82aa330e6d306ab734e73cd3f",
    },
    "private_kis_safety": {
        "merge_commit": "273d07e73eb9577c4e5a4edcd241eab2037f3c8f",
        "exact_approved_head": "792aa93273e71813cab3ddebe529be69849cfbaa",
        "prerequisite_only": True,
        "grants_proposal_or_order_authority": False,
    },
    "universe": {
        "repository": "yonggeun1021-hub/atlas-data",
        "interface_contract_version": "krx_investable_registry/1",
        "exact_head": "e7b7a209d785d63627dc596f4a58581b681b61ad",
        "merge_commit": "acb33d8b1cd866d478ec6c1e49422571fe71a48d",
        "required_repository_state": "MERGED_TO_PUBLIC_MAIN",
    },
    "shadow": {
        "repository": "yonggeun1021-hub/atlas-data",
        "interface_contract_version": "krx_shadow_strategy/1",
        "exact_head": "858ee61d149e91cbb3b7e45aeb8ff9b2d0fd05a6",
        "required_repository_state": "MERGED_TO_PUBLIC_MAIN",
    },
}

AUTHORITY_ALL_FALSE = {
    "briefing_candidate_selection_authority": False,
    "strategy_policy_ratification_authority": False,
    "portfolio_action_authority": False,
    "internal_virtual_ledger_consumption_authority": False,
    "paper_order_write": False,
    "kis_submission_compatible": False,
    "exchange_authority": False,
    "order_authority": False,
    "real_capital_authority": False,
    "live_account_authority": False,
    "production_authority": False,
    "trading_authority": False,
}

GATE_AUTHORITY_FIELDS = {
    "internal_virtual_ledger_paper_authorized",
    "kis_mock_account_auto_order_authorized",
    "live_account_order_authorized",
    "production_authorized",
    "real_capital_authorized",
    "trading_authorized",
}


class KrxPaperProposalBridgeError(ValueError):
    """Raised when an input violates the immutable interface contract."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KrxPaperProposalBridgeError(f"JSON_INVALID:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise KrxPaperProposalBridgeError(f"JSON_ROOT_INVALID:{path}")
    return value


def _exact(value: object, fields: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise KrxPaperProposalBridgeError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise KrxPaperProposalBridgeError(code)
    return value


def _commit(value: object, code: str) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise KrxPaperProposalBridgeError(code)
    return value


def _symbol(value: object, code: str) -> str:
    if not isinstance(value, str) or SYMBOL_RE.fullmatch(value) is None:
        raise KrxPaperProposalBridgeError(code)
    return value


def _token(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise KrxPaperProposalBridgeError(code)
    return value


def _time(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise KrxPaperProposalBridgeError(code)
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise KrxPaperProposalBridgeError(code) from exc
    if parsed.tzinfo != dt.timezone.utc:
        raise KrxPaperProposalBridgeError(code)
    return parsed


def _nonnegative_int(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise KrxPaperProposalBridgeError(code)
    return value


def _positive_int(value: object, code: str) -> int:
    result = _nonnegative_int(value, code)
    if result == 0:
        raise KrxPaperProposalBridgeError(code)
    return result


def _append_once(target: list[str], code: str) -> None:
    if code not in target:
        target.append(code)


def validate_contract(value: object) -> dict:
    fields = {
        "schema_version",
        "contract_version",
        "input_schema_version",
        "output_schema_version",
        "market",
        "source_requirements",
        "required_completed_bar_intervals",
        "proposal_actions",
        "policy_boundary",
        "gate_requirements",
        "canary",
        "order_boundary",
        "authority",
    }
    row = _exact(value, fields, "CONTRACT_FIELDS_INVALID")
    if (
        row["schema_version"] != "krx_paper_proposal_bridge_contract/1"
        or row["contract_version"] != CONTRACT_VERSION
        or row["input_schema_version"] != INPUT_SCHEMA_VERSION
        or row["output_schema_version"] != OUTPUT_SCHEMA_VERSION
        or row["market"] != "KOREA"
    ):
        raise KrxPaperProposalBridgeError("CONTRACT_IDENTITY_INVALID")
    if row["source_requirements"] != EXPECTED_SOURCE_PINS:
        raise KrxPaperProposalBridgeError("CONTRACT_SOURCE_PIN_DRIFT")
    if row["required_completed_bar_intervals"] != ["15m", "1h", "1d"]:
        raise KrxPaperProposalBridgeError("CONTRACT_BAR_INTERVAL_DRIFT")
    if row["proposal_actions"] != ["ENTER", "HOLD", "EXIT"]:
        raise KrxPaperProposalBridgeError("CONTRACT_ACTION_DRIFT")
    if row["policy_boundary"] != {
        "required_status": "RATIFIED",
        "ratified_policy_bindings": [],
        "candidate_policy_is_authority": False,
        "merge_or_ci_ratifies_policy": False,
    }:
        raise KrxPaperProposalBridgeError("CONTRACT_POLICY_AUTHORITY_DRIFT")
    if row["gate_requirements"] != {
        "common_safety": "PASS",
        "krx_shadow": "PASS",
        "minimum_state_for_proposal": "SHADOW",
        "paper_canary_gate_is_downstream": True,
    }:
        raise KrxPaperProposalBridgeError("CONTRACT_GATE_DRIFT")
    if row["canary"] != {
        "maximum_symbols_per_packet": 1,
        "maximum_open_positions": 1,
        "proposal_expiry_required": True,
        "duplicate_decision_key_blocking": True,
        "stale_input_blocking": True,
        "identity_mismatch_blocking": True,
        "exit_without_position_blocking": True,
    }:
        raise KrxPaperProposalBridgeError("CONTRACT_CANARY_DRIFT")
    if row["order_boundary"] != {
        "draft_kind": "INTERNAL_VIRTUAL_LEDGER_DRAFT_ONLY",
        "submission_compatible": False,
        "exchange_authority": False,
        "order_authority": False,
        "kis_mock_submission_authority": False,
        "new_broker_post_permitted": False,
    }:
        raise KrxPaperProposalBridgeError("CONTRACT_ORDER_BOUNDARY_DRIFT")
    if row["authority"] != AUTHORITY_ALL_FALSE:
        raise KrxPaperProposalBridgeError("CONTRACT_AUTHORITY_ESCALATION")
    return copy.deepcopy(row)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(path))


def validate_policy_packet(value: object) -> dict:
    fields = {
        "schema_version",
        "market",
        "decision_status",
        "ratifies_strategy_policy",
        "candidate_policy_ids",
        "source_lineage",
        "required_evidence",
        "decision_metrics",
        "ratification_requirements",
        "authority",
    }
    row = _exact(value, fields, "POLICY_PACKET_FIELDS_INVALID")
    if (
        row["schema_version"] != POLICY_PACKET_SCHEMA_VERSION
        or row["market"] != "KOREA"
        or row["decision_status"] != "UNRATIFIED_EVIDENCE_INCOMPLETE"
        or row["ratifies_strategy_policy"] is not False
    ):
        raise KrxPaperProposalBridgeError("POLICY_PACKET_STATUS_INVALID")
    ids = row["candidate_policy_ids"]
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) != len(set(ids))
        or any(not isinstance(item, str) or not item for item in ids)
    ):
        raise KrxPaperProposalBridgeError("POLICY_PACKET_CANDIDATES_INVALID")
    if row["source_lineage"] != {
        "shadow_policy_candidate_head": EXPECTED_SOURCE_PINS["shadow"]["exact_head"],
        "shadow_policy_candidate_status": "PROPOSED_UNRATIFIED_REPLAY_ONLY",
        "universe_candidate_head": EXPECTED_SOURCE_PINS["universe"]["exact_head"],
        "universe_candidate_status": "MERGED_NON_AUTHORITY_INTERFACE",
    }:
        raise KrxPaperProposalBridgeError("POLICY_PACKET_LINEAGE_INVALID")
    evidence = row["required_evidence"]
    required_evidence = {
        "point_in_time_chronological_replay",
        "walk_forward_out_of_sample",
        "cost_fee_slippage_tick_gap_sensitivity",
        "up_regime",
        "down_regime",
        "sideways_regime",
        "missed_upside_and_avoided_downside",
        "expiry_and_lifecycle_outcomes",
    }
    if not isinstance(evidence, dict) or set(evidence) != required_evidence:
        raise KrxPaperProposalBridgeError("POLICY_PACKET_EVIDENCE_COVERAGE_INVALID")
    for key, evidence_row in evidence.items():
        if evidence_row != {"status": "NOT_AVAILABLE", "result": None}:
            raise KrxPaperProposalBridgeError(f"POLICY_PACKET_EVIDENCE_DRIFT:{key}")
    metrics = row["decision_metrics"]
    if not isinstance(metrics, dict) or not metrics or any(
        value is not None for value in metrics.values()
    ):
        raise KrxPaperProposalBridgeError("POLICY_PACKET_THRESHOLD_INVENTED")
    requirements = row["ratification_requirements"]
    if (
        not isinstance(requirements, list)
        or not requirements
        or len(requirements) != len(set(requirements))
        or any(not isinstance(item, str) or not item for item in requirements)
    ):
        raise KrxPaperProposalBridgeError("POLICY_PACKET_REQUIREMENTS_INVALID")
    authority = row["authority"]
    if not isinstance(authority, dict) or not authority or any(
        value is not False for value in authority.values()
    ):
        raise KrxPaperProposalBridgeError("POLICY_PACKET_AUTHORITY_ESCALATION")
    return copy.deepcopy(row)


def load_policy_packet(path: Path = POLICY_PACKET_PATH) -> dict:
    return validate_policy_packet(_read_json(path))


def _validate_window(row: dict, evaluated_at: dt.datetime, prefix: str) -> None:
    available = _time(row["available_at_utc"], f"{prefix}_AVAILABLE_AT_INVALID")
    valid_until = _time(row["valid_until_utc"], f"{prefix}_VALID_UNTIL_INVALID")
    if valid_until <= available:
        raise KrxPaperProposalBridgeError(f"{prefix}_WINDOW_INVALID")
    # Availability after evaluation is a semantic look-ahead error, not a
    # recoverable stale blocker.
    if available > evaluated_at:
        raise KrxPaperProposalBridgeError(f"{prefix}_LOOKAHEAD")


def validate_input(value: object, contract: dict) -> dict:
    fields = {
        "schema_version",
        "evaluated_at_utc",
        "proposal_expires_at_utc",
        "prior_proposal_keys",
        "briefing",
        "universe",
        "shadow",
        "bars",
        "position",
        "policy",
        "gate_assessment",
        "authority",
        "packet_sha256",
    }
    row = _exact(value, fields, "INPUT_FIELDS_INVALID")
    if row["schema_version"] != INPUT_SCHEMA_VERSION:
        raise KrxPaperProposalBridgeError("INPUT_SCHEMA_INVALID")
    if row["authority"] != contract["authority"]:
        raise KrxPaperProposalBridgeError("INPUT_AUTHORITY_ESCALATION")
    packet_sha = _sha(row["packet_sha256"], "INPUT_PACKET_SHA_INVALID")
    unsigned = {key: copy.deepcopy(item) for key, item in row.items() if key != "packet_sha256"}
    if packet_sha != payload_sha256(unsigned):
        raise KrxPaperProposalBridgeError("INPUT_PACKET_HASH_MISMATCH")

    evaluated_at = _time(row["evaluated_at_utc"], "EVALUATED_AT_INVALID")
    expires_at = _time(row["proposal_expires_at_utc"], "PROPOSAL_EXPIRY_INVALID")
    if expires_at <= evaluated_at:
        raise KrxPaperProposalBridgeError("PROPOSAL_ALREADY_EXPIRED")
    prior = row["prior_proposal_keys"]
    if (
        not isinstance(prior, list)
        or len(prior) != len(set(prior))
        or any(not isinstance(item, str) or SHA256_RE.fullmatch(item) is None for item in prior)
    ):
        raise KrxPaperProposalBridgeError("PRIOR_PROPOSAL_KEYS_INVALID")

    briefing = _exact(
        row["briefing"],
        {"symbol", "rank", "summary", "source_sha256"},
        "BRIEFING_FIELDS_INVALID",
    )
    _symbol(briefing["symbol"], "BRIEFING_SYMBOL_INVALID")
    _positive_int(briefing["rank"], "BRIEFING_RANK_INVALID")
    _token(briefing["summary"], "BRIEFING_SUMMARY_INVALID")
    _sha(briefing["source_sha256"], "BRIEFING_SOURCE_SHA_INVALID")

    universe = _exact(
        row["universe"],
        {
            "repository", "source_commit", "repository_state", "contract_version",
            "symbol", "security_id", "decision_eligibility", "available_at_utc",
            "valid_until_utc", "source_sha256", "packet_sha256",
        },
        "UNIVERSE_FIELDS_INVALID",
    )
    _token(universe["repository"], "UNIVERSE_REPOSITORY_INVALID")
    _commit(universe["source_commit"], "UNIVERSE_COMMIT_INVALID")
    _token(universe["repository_state"], "UNIVERSE_STATE_INVALID")
    _token(universe["contract_version"], "UNIVERSE_CONTRACT_INVALID")
    _symbol(universe["symbol"], "UNIVERSE_SYMBOL_INVALID")
    _token(universe["security_id"], "UNIVERSE_SECURITY_ID_INVALID")
    _token(universe["decision_eligibility"], "UNIVERSE_ELIGIBILITY_INVALID")
    _sha(universe["source_sha256"], "UNIVERSE_SOURCE_SHA_INVALID")
    _sha(universe["packet_sha256"], "UNIVERSE_PACKET_SHA_INVALID")
    _validate_window(universe, evaluated_at, "UNIVERSE")

    shadow = _exact(
        row["shadow"],
        {
            "repository", "source_commit", "repository_state", "contract_version",
            "decision_key", "symbol", "action", "diagnostic_action",
            "source_sha256", "packet_sha256",
        },
        "SHADOW_FIELDS_INVALID",
    )
    _token(shadow["repository"], "SHADOW_REPOSITORY_INVALID")
    _commit(shadow["source_commit"], "SHADOW_COMMIT_INVALID")
    _token(shadow["repository_state"], "SHADOW_STATE_INVALID")
    _token(shadow["contract_version"], "SHADOW_CONTRACT_INVALID")
    _sha(shadow["decision_key"], "SHADOW_DECISION_KEY_INVALID")
    _symbol(shadow["symbol"], "SHADOW_SYMBOL_INVALID")
    if shadow["action"] not in ALLOWED_ACTIONS | {"NO_TRADE"}:
        raise KrxPaperProposalBridgeError("SHADOW_ACTION_INVALID")
    if shadow["diagnostic_action"] not in ALLOWED_ACTIONS:
        raise KrxPaperProposalBridgeError("SHADOW_DIAGNOSTIC_ACTION_INVALID")
    _sha(shadow["source_sha256"], "SHADOW_SOURCE_SHA_INVALID")
    _sha(shadow["packet_sha256"], "SHADOW_PACKET_SHA_INVALID")

    bars = row["bars"]
    if not isinstance(bars, dict) or list(bars) != contract["required_completed_bar_intervals"]:
        raise KrxPaperProposalBridgeError("BAR_INTERVAL_COVERAGE_INVALID")
    bar_fields = {
        "completed", "opened_at_utc", "closed_at_utc", "available_at_utc",
        "valid_until_utc", "source_sha256",
    }
    for interval, raw_bar in bars.items():
        bar = _exact(raw_bar, bar_fields, f"BAR_FIELDS_INVALID:{interval}")
        if type(bar["completed"]) is not bool:
            raise KrxPaperProposalBridgeError(f"BAR_COMPLETION_TYPE_INVALID:{interval}")
        opened = _time(bar["opened_at_utc"], f"BAR_OPEN_INVALID:{interval}")
        closed = _time(bar["closed_at_utc"], f"BAR_CLOSE_INVALID:{interval}")
        available = _time(bar["available_at_utc"], f"BAR_AVAILABLE_INVALID:{interval}")
        _time(bar["valid_until_utc"], f"BAR_VALID_UNTIL_INVALID:{interval}")
        if not opened < closed <= available <= evaluated_at:
            raise KrxPaperProposalBridgeError(f"BAR_TIME_ORDER_INVALID:{interval}")
        _sha(bar["source_sha256"], f"BAR_SOURCE_SHA_INVALID:{interval}")
        _validate_window(bar, evaluated_at, f"BAR_{interval}")

    position = _exact(
        row["position"],
        {
            "symbol", "status", "current_open_positions", "available_at_utc",
            "valid_until_utc", "source_sha256",
        },
        "POSITION_FIELDS_INVALID",
    )
    _symbol(position["symbol"], "POSITION_SYMBOL_INVALID")
    if position["status"] not in {"FLAT", "OPEN"}:
        raise KrxPaperProposalBridgeError("POSITION_STATUS_INVALID")
    _nonnegative_int(position["current_open_positions"], "OPEN_POSITION_COUNT_INVALID")
    _sha(position["source_sha256"], "POSITION_SOURCE_SHA_INVALID")
    _validate_window(position, evaluated_at, "POSITION")

    policy = _exact(
        row["policy"],
        {
            "policy_id", "status", "symbol", "entry_zone", "stop_price_units",
            "first_take_profit_price_units", "final_take_profit_price_units",
            "expires_at_utc", "planned_loss_units", "account_risk_budget_units",
            "account_committed_risk_units", "available_at_utc", "valid_until_utc",
            "source_sha256",
        },
        "POLICY_FIELDS_INVALID",
    )
    _token(policy["policy_id"], "POLICY_ID_INVALID")
    _token(policy["status"], "POLICY_STATUS_INVALID")
    _symbol(policy["symbol"], "POLICY_SYMBOL_INVALID")
    entry_zone = _exact(
        policy["entry_zone"],
        {"minimum_price_units", "maximum_price_units"},
        "ENTRY_ZONE_FIELDS_INVALID",
    )
    entry_min = _positive_int(entry_zone["minimum_price_units"], "ENTRY_MIN_INVALID")
    entry_max = _positive_int(entry_zone["maximum_price_units"], "ENTRY_MAX_INVALID")
    stop = _positive_int(policy["stop_price_units"], "STOP_PRICE_INVALID")
    first_take = _positive_int(
        policy["first_take_profit_price_units"], "FIRST_TAKE_PROFIT_INVALID"
    )
    final_take = _positive_int(
        policy["final_take_profit_price_units"], "FINAL_TAKE_PROFIT_INVALID"
    )
    if not stop < entry_min <= entry_max < first_take <= final_take:
        raise KrxPaperProposalBridgeError("PRICE_PLAN_ORDER_INVALID")
    policy_expiry = _time(policy["expires_at_utc"], "POLICY_EXPIRY_INVALID")
    if policy_expiry != expires_at:
        raise KrxPaperProposalBridgeError("POLICY_PROPOSAL_EXPIRY_MISMATCH")
    planned_loss = _positive_int(policy["planned_loss_units"], "PLANNED_LOSS_INVALID")
    account_budget = _positive_int(
        policy["account_risk_budget_units"], "ACCOUNT_RISK_BUDGET_INVALID"
    )
    committed = _nonnegative_int(
        policy["account_committed_risk_units"], "ACCOUNT_COMMITTED_RISK_INVALID"
    )
    if committed > account_budget or planned_loss > account_budget - committed:
        raise KrxPaperProposalBridgeError("PLANNED_LOSS_EXCEEDS_REMAINING_RISK_BUDGET")
    _sha(policy["source_sha256"], "POLICY_SOURCE_SHA_INVALID")
    _validate_window(policy, evaluated_at, "POLICY")

    gate = _exact(
        row["gate_assessment"],
        {
            "schema_version", "assessment_sha256", "current_state", "common_safety",
            "krx_shadow", "krx_paper_canary_start", "authority",
        },
        "GATE_FIELDS_INVALID",
    )
    _token(gate["schema_version"], "GATE_SCHEMA_INVALID")
    _sha(gate["assessment_sha256"], "GATE_ASSESSMENT_SHA_INVALID")
    if gate["current_state"] not in ALLOWED_STATES:
        raise KrxPaperProposalBridgeError("GATE_STATE_INVALID")
    for key in ("common_safety", "krx_shadow", "krx_paper_canary_start"):
        if gate[key] not in ALLOWED_GATE_RESULTS:
            raise KrxPaperProposalBridgeError(f"GATE_RESULT_INVALID:{key}")
    gate_authority = gate["authority"]
    if (
        not isinstance(gate_authority, dict)
        or set(gate_authority) != GATE_AUTHORITY_FIELDS
        or any(type(flag) is not bool for flag in gate_authority.values())
        or any(
            gate_authority[key] is not False
            for key in (
                "live_account_order_authorized", "production_authorized",
                "real_capital_authorized", "trading_authorized",
            )
        )
    ):
        raise KrxPaperProposalBridgeError("GATE_AUTHORITY_INVALID")
    return copy.deepcopy(row)


def _is_stale(row: dict, evaluated_at: dt.datetime) -> bool:
    return _time(row["valid_until_utc"], "VALID_UNTIL_INVALID") <= evaluated_at


def _evidence_basis(packet: dict, policy_packet: dict) -> dict:
    return {
        "market": "KOREA",
        "symbol": packet["briefing"]["symbol"],
        "security_id": packet["universe"]["security_id"],
        "evaluated_at_utc": packet["evaluated_at_utc"],
        "proposal_expires_at_utc": packet["proposal_expires_at_utc"],
        "briefing_source_sha256": packet["briefing"]["source_sha256"],
        "universe": {
            "source_commit": packet["universe"]["source_commit"],
            "source_sha256": packet["universe"]["source_sha256"],
            "packet_sha256": packet["universe"]["packet_sha256"],
            "decision_eligibility": packet["universe"]["decision_eligibility"],
        },
        "shadow": {
            "source_commit": packet["shadow"]["source_commit"],
            "source_sha256": packet["shadow"]["source_sha256"],
            "packet_sha256": packet["shadow"]["packet_sha256"],
            "decision_key": packet["shadow"]["decision_key"],
            "action": packet["shadow"]["action"],
            "diagnostic_action": packet["shadow"]["diagnostic_action"],
        },
        "completed_bar_sha256": {
            interval: packet["bars"][interval]["source_sha256"]
            for interval in ("15m", "1h", "1d")
        },
        "position_source_sha256": packet["position"]["source_sha256"],
        "policy_source_sha256": packet["policy"]["source_sha256"],
        "policy_decision_packet_sha256": payload_sha256(policy_packet),
        "gate_assessment_sha256": packet["gate_assessment"]["assessment_sha256"],
    }


def _proposal_key(packet: dict, basis_sha256: str) -> str:
    return payload_sha256(
        {
            "contract_version": CONTRACT_VERSION,
            "basis_sha256": basis_sha256,
            "shadow_decision_key": packet["shadow"]["decision_key"],
            "policy_id": packet["policy"]["policy_id"],
            "proposal_expires_at_utc": packet["proposal_expires_at_utc"],
        }
    )


def _blockers(packet: dict, contract: dict, proposal_key: str) -> list[str]:
    blockers: list[str] = []
    universe_req = contract["source_requirements"]["universe"]
    shadow_req = contract["source_requirements"]["shadow"]
    universe = packet["universe"]
    shadow = packet["shadow"]
    evaluated_at = _time(packet["evaluated_at_utc"], "EVALUATED_AT_INVALID")

    if universe["repository"] != universe_req["repository"]:
        _append_once(blockers, "UNIVERSE_REPOSITORY_MISMATCH")
    if universe["source_commit"] != universe_req["exact_head"]:
        _append_once(blockers, "UNIVERSE_EXACT_HEAD_MISMATCH")
    if universe["contract_version"] != universe_req["interface_contract_version"]:
        _append_once(blockers, "UNIVERSE_CONTRACT_MISMATCH")
    if universe["repository_state"] != universe_req["required_repository_state"]:
        _append_once(blockers, "UNIVERSE_SOURCE_UNMERGED")
    if universe["decision_eligibility"] != "ELIGIBLE":
        _append_once(blockers, "UNIVERSE_DECISION_ELIGIBILITY_NOT_ELIGIBLE")

    if shadow["repository"] != shadow_req["repository"]:
        _append_once(blockers, "SHADOW_REPOSITORY_MISMATCH")
    if shadow["source_commit"] != shadow_req["exact_head"]:
        _append_once(blockers, "SHADOW_EXACT_HEAD_MISMATCH")
    if shadow["contract_version"] != shadow_req["interface_contract_version"]:
        _append_once(blockers, "SHADOW_CONTRACT_MISMATCH")
    if shadow["repository_state"] != shadow_req["required_repository_state"]:
        _append_once(blockers, "SHADOW_SOURCE_UNMERGED")
    if shadow["action"] == "NO_TRADE":
        _append_once(blockers, "SHADOW_ACTION_NOT_AUTHORIZED")

    symbols = {
        packet["briefing"]["symbol"],
        universe["symbol"],
        shadow["symbol"],
        packet["position"]["symbol"],
        packet["policy"]["symbol"],
    }
    if len(symbols) != 1:
        _append_once(blockers, "IDENTITY_SYMBOL_MISMATCH")

    for label, source in (
        ("UNIVERSE", universe),
        ("POSITION", packet["position"]),
        ("POLICY", packet["policy"]),
    ):
        if _is_stale(source, evaluated_at):
            _append_once(blockers, f"{label}_STALE")
    for interval, bar in packet["bars"].items():
        if not bar["completed"]:
            _append_once(blockers, f"BAR_NOT_COMPLETED:{interval}")
        if _is_stale(bar, evaluated_at):
            _append_once(blockers, f"BAR_STALE:{interval}")

    policy = packet["policy"]
    if policy["status"] != contract["policy_boundary"]["required_status"]:
        _append_once(blockers, "STRATEGY_POLICY_UNRATIFIED")
    bindings = contract["policy_boundary"]["ratified_policy_bindings"]
    if not any(
        isinstance(binding, dict)
        and binding.get("policy_id") == policy["policy_id"]
        and binding.get("source_sha256") == policy["source_sha256"]
        for binding in bindings
    ):
        _append_once(blockers, "RATIFIED_POLICY_BINDING_ABSENT")

    gate = packet["gate_assessment"]
    gate_req = contract["source_requirements"]["public_krx_gate"]
    if gate["schema_version"] != gate_req["assessment_schema_version"]:
        _append_once(blockers, "KRX_GATE_SCHEMA_MISMATCH")
    if gate["assessment_sha256"] != gate_req["assessment_sha256"]:
        _append_once(blockers, "KRX_GATE_ASSESSMENT_HASH_MISMATCH")
    if gate["common_safety"] != contract["gate_requirements"]["common_safety"]:
        _append_once(blockers, "COMMON_SAFETY_NOT_PASS")
    if gate["krx_shadow"] != contract["gate_requirements"]["krx_shadow"]:
        _append_once(blockers, "KRX_SHADOW_NOT_PASS")
    minimum_state = contract["gate_requirements"]["minimum_state_for_proposal"]
    if ALLOWED_STATES.index(gate["current_state"]) < ALLOWED_STATES.index(minimum_state):
        _append_once(blockers, "KRX_GATE_STATE_BELOW_SHADOW")

    action = shadow["diagnostic_action"] if shadow["action"] == "NO_TRADE" else shadow["action"]
    position = packet["position"]
    if position["current_open_positions"] > contract["canary"]["maximum_open_positions"]:
        _append_once(blockers, "CANARY_OPEN_POSITION_LIMIT_EXCEEDED")
    if action == "ENTER" and position["status"] != "FLAT":
        _append_once(blockers, "ENTER_REQUIRES_FLAT_POSITION")
    if action == "ENTER" and position["current_open_positions"] != 0:
        _append_once(blockers, "ENTER_REQUIRES_ZERO_OPEN_POSITIONS")
    if action == "HOLD" and position["status"] != "OPEN":
        _append_once(blockers, "NO_POSITION_HOLD")
    if action == "EXIT" and position["status"] != "OPEN":
        _append_once(blockers, "NO_POSITION_EXIT")

    if shadow["decision_key"] in packet["prior_proposal_keys"]:
        _append_once(blockers, "DUPLICATE_SHADOW_DECISION_KEY")
    if proposal_key in packet["prior_proposal_keys"]:
        _append_once(blockers, "DUPLICATE_PROPOSAL_KEY")
    return blockers


def _ledger_draft(packet: dict, proposal_key: str, evidence_basis_sha256: str) -> dict:
    policy = packet["policy"]
    material = {
        "schema_version": LEDGER_DRAFT_SCHEMA_VERSION,
        "draft_kind": "INTERNAL_VIRTUAL_LEDGER_DRAFT_ONLY",
        "consumer": "ATLAS_INTERNAL_VIRTUAL_LEDGER",
        "consumption_authorized": False,
        "proposal_key": proposal_key,
        "evidence_basis_sha256": evidence_basis_sha256,
        "symbol": packet["briefing"]["symbol"],
        "action": packet["shadow"]["action"],
        "planned_loss_units": policy["planned_loss_units"],
        "account_risk_budget_units": policy["account_risk_budget_units"],
        "expires_at_utc": packet["proposal_expires_at_utc"],
        "submissionCompatible": False,
        "exchange_authority": False,
        "order_authority": False,
        "kis_mock_submission_authority": False,
        "paper_order_write": False,
        "real_capital_authority": False,
        "production_authority": False,
        "trading_authority": False,
    }
    material["draft_sha256"] = payload_sha256(material)
    return material


def build_packet(
    input_packet: dict,
    contract: dict | None = None,
    policy_packet: dict | None = None,
) -> dict:
    locked_contract = validate_contract(contract if contract is not None else load_contract())
    policy_decision = validate_policy_packet(
        policy_packet if policy_packet is not None else load_policy_packet()
    )
    source = validate_input(input_packet, locked_contract)
    basis = _evidence_basis(source, policy_decision)
    basis_sha = payload_sha256(basis)
    proposal_key = _proposal_key(source, basis_sha)
    blockers = _blockers(source, locked_contract, proposal_key)
    diagnostic_action = source["shadow"]["diagnostic_action"]
    authoritative_action = source["shadow"]["action"]
    non_none = not blockers and authoritative_action in ALLOWED_ACTIONS
    if not non_none:
        authoritative_action = "NONE"

    policy = source["policy"]
    proposal = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "proposal_key": proposal_key,
        "status": "NON_NONE" if non_none else "NONE",
        "market": "KOREA",
        "symbol": source["briefing"]["symbol"],
        "security_id": source["universe"]["security_id"],
        "action": authoritative_action,
        "diagnostic_action": diagnostic_action,
        "evaluated_at_utc": source["evaluated_at_utc"],
        "expires_at_utc": source["proposal_expires_at_utc"],
        "eligibility": {
            "status": source["universe"]["decision_eligibility"],
            "source_commit": source["universe"]["source_commit"],
            "source_sha256": source["universe"]["source_sha256"],
        },
        "completed_bars": {
            interval: {
                "completed": source["bars"][interval]["completed"],
                "closed_at_utc": source["bars"][interval]["closed_at_utc"],
                "source_sha256": source["bars"][interval]["source_sha256"],
            }
            for interval in locked_contract["required_completed_bar_intervals"]
        },
        "strategy_policy": {
            "policy_id": policy["policy_id"],
            "status": policy["status"],
            "source_sha256": policy["source_sha256"],
            "ratification_packet_sha256": payload_sha256(policy_decision),
        },
        "candidate_plan_not_authority": {
            "entry_zone": copy.deepcopy(policy["entry_zone"]),
            "stop_price_units": policy["stop_price_units"],
            "first_take_profit_price_units": policy["first_take_profit_price_units"],
            "final_take_profit_price_units": policy["final_take_profit_price_units"],
            "planned_loss_units": policy["planned_loss_units"],
            "account_risk_budget_units": policy["account_risk_budget_units"],
            "account_committed_risk_units": policy["account_committed_risk_units"],
        },
        "evidence_basis_sha256": basis_sha,
        "blockers": blockers,
        "internal_virtual_ledger_draft": None,
        "broker_order_draft": None,
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    if non_none:
        proposal["internal_virtual_ledger_draft"] = _ledger_draft(
            source, proposal_key, basis_sha
        )
    proposal["proposal_sha256"] = payload_sha256(proposal)

    blocker_text = ", ".join(blockers) if blockers else "none"
    human = {
        "schema_version": "krx_paper_proposal_human_briefing/1",
        "proposal_key": proposal_key,
        "evidence_basis_sha256": basis_sha,
        "symbol": source["briefing"]["symbol"],
        "proposal_status": proposal["status"],
        "action": proposal["action"],
        "diagnostic_action": diagnostic_action,
        "summary": (
            f"{source['briefing']['symbol']} KRX PAPER proposal={proposal['status']} "
            f"action={proposal['action']} diagnostic={diagnostic_action}; "
            f"blockers={blocker_text}."
        ),
    }
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "market": "KOREA",
        "source": {
            "input_packet_sha256": source["packet_sha256"],
            "contract_sha256": payload_sha256(locked_contract),
            "policy_ratification_packet_sha256": payload_sha256(policy_decision),
            "public_krx_gate_merge_commit": locked_contract["source_requirements"][
                "public_krx_gate"
            ]["merge_commit"],
            "private_kis_safety_merge_commit": locked_contract["source_requirements"][
                "private_kis_safety"
            ]["merge_commit"],
            "universe_exact_head": source["universe"]["source_commit"],
            "shadow_exact_head": source["shadow"]["source_commit"],
        },
        "evidence_basis": basis,
        "evidence_basis_sha256": basis_sha,
        "human_briefing": human,
        "machine_proposal": proposal,
        "summary": {
            "proposal_status": proposal["status"],
            "action": proposal["action"],
            "diagnostic_action": diagnostic_action,
            "blocker_count": len(blockers),
            "policy_status": policy_decision["decision_status"],
            "internal_virtual_ledger_draft_present": (
                proposal["internal_virtual_ledger_draft"] is not None
            ),
            "broker_order_draft_present": False,
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(
    packet: dict,
    input_packet: dict,
    contract: dict | None = None,
    policy_packet: dict | None = None,
) -> dict:
    expected = build_packet(input_packet, contract, policy_packet)
    if packet != expected:
        raise KrxPaperProposalBridgeError("PROPOSAL_PACKET_SEMANTIC_TAMPER_OR_DRIFT")
    if (
        packet["human_briefing"]["evidence_basis_sha256"]
        != packet["machine_proposal"]["evidence_basis_sha256"]
        or packet["evidence_basis_sha256"]
        != packet["machine_proposal"]["evidence_basis_sha256"]
    ):
        raise KrxPaperProposalBridgeError("HUMAN_MACHINE_EVIDENCE_BASIS_MISMATCH")
    return copy.deepcopy(packet)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--policy-packet", type=Path, default=POLICY_PACKET_PATH)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    contract = load_contract(args.contract)
    policy = load_policy_packet(args.policy_packet)
    input_packet = _read_json(args.input)
    packet = build_packet(input_packet, contract, policy)
    validate_packet(packet, input_packet, contract, policy)
    encoded = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
