#!/usr/bin/env python3
"""Deterministic, non-authorizing KRX PAPER_CANARY eligibility selector.

The committed policy packet is explicitly unratified.  Consequently this v1
module always returns the authoritative pair ``symbol=NONE`` and
``status=LOCKED``.  Synthetic fixtures may exercise the exact candidate
ordering and rejection rules through ``diagnostic_selected_symbol`` only.

There is no network, broker, ledger, or order client in this module.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_paper_canary_eligibility_contract.json"
PROPOSAL_PATH = ROOT / "config" / "krx_paper_canary_policy_ratification_proposal.json"
DEFAULT_INPUT_PATH = ROOT / "test" / "fixtures" / "krx_paper_canary_eligibility_input.json"

CONTRACT_VERSION = "krx_paper_canary_eligibility/1"
INPUT_SCHEMA_VERSION = "krx_paper_canary_eligibility_input/1"
OUTPUT_SCHEMA_VERSION = "krx_paper_canary_eligibility_packet/1"
PROPOSAL_SCHEMA_VERSION = "krx_paper_canary_policy_ratification_proposal/1"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SYMBOL_RE = re.compile(r"^[0-9]{6}$")
SECURITY_ID_RE = re.compile(r"^KR:XKRX:[A-Z0-9]{12}$")
REQUIRED_INTERVALS = ("15m", "1h", "1d")
INTERVAL_SECONDS = {"15m": 900, "1h": 3600, "1d": 23_400}
SOURCE_KINDS = {"NATURAL", "SYNTHETIC_TEST_FIXTURE"}
THRESHOLD_FIELDS = {
    "minimum_turnover_krw",
    "minimum_bid_depth_krw",
    "minimum_ask_depth_krw",
    "maximum_spread_bps",
    "slippage_order_notional_krw",
    "maximum_worst_side_slippage_bps",
}

AUTHORITY_ALL_FALSE = {
    "eligibility_authority": False,
    "candidate_selection_authority": False,
    "strategy_authority": False,
    "internal_virtual_ledger_authorized": False,
    "paper_order_write": False,
    "kis_post_authorized": False,
    "order_authorized": False,
    "real_account_authorized": False,
    "real_capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

ORDER_BOUNDARY = {
    "order_draft": None,
    "broker_submission": None,
    "network_or_broker_client_imported": False,
}


class KrxPaperCanaryEligibilityError(ValueError):
    """Raised for structural, lineage, or tamper errors."""


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
        raise KrxPaperCanaryEligibilityError(f"JSON_INVALID:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise KrxPaperCanaryEligibilityError(f"JSON_ROOT_INVALID:{path}")
    return value


def _exact(value: object, fields: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise KrxPaperCanaryEligibilityError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise KrxPaperCanaryEligibilityError(code)
    return value


def _time(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise KrxPaperCanaryEligibilityError(code)
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise KrxPaperCanaryEligibilityError(code) from exc
    if parsed.tzinfo != dt.timezone.utc:
        raise KrxPaperCanaryEligibilityError(code)
    return parsed


def _nonnegative_int(value: object, code: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < 0 or (positive and value == 0):
        raise KrxPaperCanaryEligibilityError(code)
    return value


def _decimal(value: object, code: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise KrxPaperCanaryEligibilityError(code)
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise KrxPaperCanaryEligibilityError(code) from exc
    if not result.is_finite() or result < 0:
        raise KrxPaperCanaryEligibilityError(code)
    return result


def _append(target: list[str], code: str) -> None:
    if code not in target:
        target.append(code)


def validate_contract(value: object) -> dict:
    fields = {
        "schema_version", "contract_version", "input_schema_version",
        "output_schema_version", "market", "proposal_path",
        "required_completed_bar_intervals", "required_natural_measurements",
        "maximum_symbols", "maximum_open_positions", "ranking_order",
        "authoritative_result_while_proposal_unratified", "synthetic_boundary",
        "order_boundary", "authority",
    }
    row = _exact(value, fields, "CONTRACT_FIELDS_INVALID")
    if (
        row["schema_version"] != "krx_paper_canary_eligibility_contract/1"
        or row["contract_version"] != CONTRACT_VERSION
        or row["input_schema_version"] != INPUT_SCHEMA_VERSION
        or row["output_schema_version"] != OUTPUT_SCHEMA_VERSION
        or row["market"] != "KOREA"
        or row["proposal_path"] != "config/krx_paper_canary_policy_ratification_proposal.json"
    ):
        raise KrxPaperCanaryEligibilityError("CONTRACT_IDENTITY_INVALID")
    if row["required_completed_bar_intervals"] != list(REQUIRED_INTERVALS):
        raise KrxPaperCanaryEligibilityError("CONTRACT_BAR_INTERVAL_DRIFT")
    if row["required_natural_measurements"] != [
        "turnover", "depth", "spread", "slippage", "tick"
    ]:
        raise KrxPaperCanaryEligibilityError("CONTRACT_MEASUREMENT_DRIFT")
    if row["maximum_symbols"] != 1 or row["maximum_open_positions"] != 1:
        raise KrxPaperCanaryEligibilityError("CONTRACT_CANARY_SCOPE_DRIFT")
    if row["ranking_order"] != [
        "TURNOVER_DESC", "MIN_BID_ASK_DEPTH_DESC", "SPREAD_BPS_ASC",
        "WORST_SIDE_SLIPPAGE_BPS_ASC", "SYMBOL_ASC", "SECURITY_ID_ASC",
    ]:
        raise KrxPaperCanaryEligibilityError("CONTRACT_RANKING_DRIFT")
    if row["authoritative_result_while_proposal_unratified"] != {
        "status": "LOCKED", "symbol": "NONE"
    }:
        raise KrxPaperCanaryEligibilityError("CONTRACT_LOCKED_RESULT_DRIFT")
    if row["synthetic_boundary"] != {
        "allowed_mode": "SYNTHETIC_TEST_FIXTURE",
        "diagnostic_selection_only": True,
        "may_set_authoritative_symbol": False,
        "may_ratify_policy": False,
    }:
        raise KrxPaperCanaryEligibilityError("CONTRACT_SYNTHETIC_BOUNDARY_DRIFT")
    if row["order_boundary"] != ORDER_BOUNDARY or row["authority"] != AUTHORITY_ALL_FALSE:
        raise KrxPaperCanaryEligibilityError("CONTRACT_AUTHORITY_ESCALATION")
    return copy.deepcopy(row)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(path))


def validate_proposal(value: object) -> dict:
    row = value if isinstance(value, dict) else {}
    required = {
        "schema_version", "packet_id", "market", "proposal_status",
        "decision_status", "ratified", "effective_at_utc", "ratified_at_utc",
        "ratified_by", "audit_as_of_utc", "source_lineage",
        "existing_packet_boundary", "measured_current_gaps",
        "recommended_from_existing_authoritative_contracts",
        "unratified_policy_axes", "eligibility_selector_proposal",
        "cio_decision_required", "authority",
    }
    _exact(row, required, "PROPOSAL_FIELDS_INVALID")
    if (
        row["schema_version"] != PROPOSAL_SCHEMA_VERSION
        or row["market"] != "KOREA"
        or row["proposal_status"] != "UNRATIFIED_PROPOSAL_ONLY"
        or row["decision_status"] != "LOCKED_EVIDENCE_AND_POLICY_INCOMPLETE"
        or row["ratified"] is not False
        or row["effective_at_utc"] is not None
        or row["ratified_at_utc"] is not None
        or row["ratified_by"] is not None
    ):
        raise KrxPaperCanaryEligibilityError("PROPOSAL_STATUS_OR_EFFECTIVE_DRIFT")
    if row["existing_packet_boundary"] != {
        "path": "config/krx_paper_policy_ratification_packet.json",
        "schema_version": "krx_paper_policy_ratification_packet/1",
        "decision_status": "UNRATIFIED_EVIDENCE_INCOMPLETE",
        "ratifies_strategy_policy": False,
        "mutation_by_this_proposal": False,
    }:
        raise KrxPaperCanaryEligibilityError("PROPOSAL_EXISTING_PACKET_BOUNDARY_DRIFT")
    if row["source_lineage"] != {
        "public_main_audited": "0421b473957e318a7642524b41820306455e5f41",
        "universe_interface_merge": "acb33d8b1cd866d478ec6c1e49422571fe71a48d",
        "completed_market_data_merge": "37e07659aa934e9a6e09b27b786dc49203060af1",
        "execution_measurement_merge": "7446cc2ba8261ab09cf40cd4daf2d3fe1a1bb17e",
        "shadow_merge": "7353be0dc26af8d6cacf2115c07d68358b5d607f",
        "p8_13_bridge_merge": "f70249c306c3d069d7c3a549ac7c87f4f2bcf37f",
        "p8_13_merge_main_full_ci": {
            "run_id": 33317349022,
            "status": "SUCCESS",
            "completed_at_utc": "2026-08-30T15:11:27Z",
        },
        "private_read_only_reconciliation_merge": "196dfd3b1380f60673d84cd3df80a586f691ea85",
        "private_p8_13_lineage_merge": "b8f0538877ed149f2ad5e4ad59a3232722ce21ef",
    }:
        raise KrxPaperCanaryEligibilityError("PROPOSAL_SOURCE_LINEAGE_DRIFT")
    axes = row["unratified_policy_axes"]
    required_nulls = {
        "virtual_nav_scope": {"virtual_nav_krw"},
        "planned_loss_per_trade": {
            "maximum_planned_loss_krw", "maximum_planned_loss_bps_of_virtual_nav"
        },
        "single_symbol_and_gross_exposure": {
            "maximum_single_symbol_notional_krw",
            "maximum_single_symbol_bps_of_virtual_nav",
            "maximum_gross_exposure_krw",
            "maximum_gross_exposure_bps_of_virtual_nav",
        },
        "entry": {"rule", "parameters"},
        "stop": {"rule", "parameters"},
        "profit_taking": {"first_take_profit", "final_take_profit", "partial_fraction"},
        "time_expiry": {"value"},
        "fee_tax_slippage_tick": {
            "entry_fee_bps", "exit_fee_bps", "sell_tax_bps",
            "maximum_slippage_bps", "slippage_order_notional_krw", "tick_size",
        },
        "daily_loss_stop": {
            "maximum_daily_loss_krw", "maximum_daily_loss_bps_of_virtual_nav"
        },
    }
    if not isinstance(axes, dict) or set(axes) != set(required_nulls):
        raise KrxPaperCanaryEligibilityError("PROPOSAL_AXIS_COVERAGE_DRIFT")
    for axis, null_fields in required_nulls.items():
        axis_row = axes[axis]
        if not isinstance(axis_row, dict) or axis_row.get("status") != "UNRATIFIED":
            raise KrxPaperCanaryEligibilityError(f"PROPOSAL_AXIS_RATIFICATION_DRIFT:{axis}")
        if any(axis_row.get(field) is not None for field in null_fields):
            raise KrxPaperCanaryEligibilityError(f"PROPOSAL_AXIS_NUMBER_INVENTED:{axis}")
        choices = axis_row.get("choices")
        if (
            not isinstance(choices, list) or len(choices) not in {2, 3}
            or len(choices) != len(set(choices))
            or any(not isinstance(item, str) or not item for item in choices)
        ):
            raise KrxPaperCanaryEligibilityError(f"PROPOSAL_AXIS_CHOICES_INVALID:{axis}")
    if not isinstance(row["authority"], dict) or not row["authority"] or any(
        value is not False for value in row["authority"].values()
    ):
        raise KrxPaperCanaryEligibilityError("PROPOSAL_AUTHORITY_ESCALATION")
    return copy.deepcopy(row)


def load_proposal(path: Path = PROPOSAL_PATH) -> dict:
    return validate_proposal(_read_json(path))


def _window(row: dict, evaluated_at: dt.datetime, prefix: str, blockers: list[str]) -> None:
    available = _time(row["available_at_utc"], f"{prefix}_AVAILABLE_AT_INVALID")
    valid_until = _time(row["valid_until_utc"], f"{prefix}_VALID_UNTIL_INVALID")
    if valid_until <= available:
        raise KrxPaperCanaryEligibilityError(f"{prefix}_WINDOW_INVALID")
    if available > evaluated_at:
        raise KrxPaperCanaryEligibilityError(f"{prefix}_LOOKAHEAD")
    if evaluated_at > valid_until:
        _append(blockers, f"{prefix}_STALE")


def _validate_evidence_row(
    value: object,
    fields: set[str],
    evaluated_at: dt.datetime,
    expected_kind: str,
    prefix: str,
    blockers: list[str],
) -> dict:
    row = _exact(value, fields, f"{prefix}_FIELDS_INVALID")
    if row["source_kind"] not in SOURCE_KINDS:
        raise KrxPaperCanaryEligibilityError(f"{prefix}_SOURCE_KIND_INVALID")
    if row["source_kind"] != expected_kind:
        _append(blockers, f"{prefix}_SOURCE_KIND_MISMATCH")
    if row["status"] != "AVAILABLE":
        _append(blockers, f"{prefix}_NOT_AVAILABLE")
    _sha(row["source_sha256"], f"{prefix}_SOURCE_SHA_INVALID")
    _window(row, evaluated_at, prefix, blockers)
    return row


def validate_input(value: object, contract: dict) -> dict:
    fields = {
        "schema_version", "evaluated_at_utc", "evidence_mode", "selection_window_id",
        "prior_selection_keys", "open_position_count", "gate_assessment", "policy",
        "candidates", "authority", "packet_sha256",
    }
    row = _exact(value, fields, "INPUT_FIELDS_INVALID")
    if row["schema_version"] != INPUT_SCHEMA_VERSION:
        raise KrxPaperCanaryEligibilityError("INPUT_SCHEMA_INVALID")
    if row["evidence_mode"] not in SOURCE_KINDS:
        raise KrxPaperCanaryEligibilityError("EVIDENCE_MODE_INVALID")
    if row["authority"] != AUTHORITY_ALL_FALSE:
        raise KrxPaperCanaryEligibilityError("INPUT_AUTHORITY_ESCALATION")
    claimed = _sha(row["packet_sha256"], "INPUT_PACKET_SHA_INVALID")
    unsigned = {key: copy.deepcopy(item) for key, item in row.items() if key != "packet_sha256"}
    if payload_sha256(unsigned) != claimed:
        raise KrxPaperCanaryEligibilityError("INPUT_PACKET_HASH_MISMATCH")
    _time(row["evaluated_at_utc"], "EVALUATED_AT_INVALID")
    if not isinstance(row["selection_window_id"], str) or not row["selection_window_id"]:
        raise KrxPaperCanaryEligibilityError("SELECTION_WINDOW_ID_INVALID")
    prior = row["prior_selection_keys"]
    if (
        not isinstance(prior, list) or len(prior) != len(set(prior))
        or any(not isinstance(item, str) or SHA_RE.fullmatch(item) is None for item in prior)
    ):
        raise KrxPaperCanaryEligibilityError("PRIOR_SELECTION_KEYS_INVALID")
    _nonnegative_int(row["open_position_count"], "OPEN_POSITION_COUNT_INVALID")
    gate = _exact(
        row["gate_assessment"],
        {"common_safety", "effective_krx_shadow", "current_state", "source_sha256"},
        "GATE_FIELDS_INVALID",
    )
    if gate["common_safety"] not in {"PASS", "FAIL", "UNKNOWN"}:
        raise KrxPaperCanaryEligibilityError("COMMON_SAFETY_INVALID")
    if gate["effective_krx_shadow"] not in {"PASS", "FAIL", "UNKNOWN"}:
        raise KrxPaperCanaryEligibilityError("KRX_SHADOW_INVALID")
    if gate["current_state"] not in {"LOCKED", "SHADOW"}:
        raise KrxPaperCanaryEligibilityError("CURRENT_STATE_INVALID")
    _sha(gate["source_sha256"], "GATE_SOURCE_SHA_INVALID")
    policy = _exact(
        row["policy"],
        {"status", "effective_at_utc", "source_sha256", "thresholds"},
        "POLICY_FIELDS_INVALID",
    )
    if policy["status"] not in {"UNRATIFIED", "RATIFIED", "TEST_FIXTURE_NOT_AUTHORITY"}:
        raise KrxPaperCanaryEligibilityError("POLICY_STATUS_INVALID")
    if policy["effective_at_utc"] is not None:
        _time(policy["effective_at_utc"], "POLICY_EFFECTIVE_AT_INVALID")
    _sha(policy["source_sha256"], "POLICY_SOURCE_SHA_INVALID")
    thresholds = _exact(policy["thresholds"], THRESHOLD_FIELDS, "POLICY_THRESHOLD_FIELDS_INVALID")
    for name, item in thresholds.items():
        if item is not None:
            _nonnegative_int(item, f"POLICY_THRESHOLD_INVALID:{name}", positive=True)
    candidates = row["candidates"]
    if not isinstance(candidates, list):
        raise KrxPaperCanaryEligibilityError("CANDIDATES_INVALID")
    symbols: set[str] = set()
    security_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise KrxPaperCanaryEligibilityError("CANDIDATE_INVALID")
        symbol = candidate.get("symbol")
        security_id = candidate.get("security_id")
        if symbol in symbols or security_id in security_ids:
            raise KrxPaperCanaryEligibilityError("CANDIDATE_SYMBOL_OR_SECURITY_ID_DUPLICATE")
        symbols.add(symbol)
        security_ids.add(security_id)
    return copy.deepcopy(row)


def _candidate(
    value: object,
    evaluated_at: dt.datetime,
    expected_kind: str,
    thresholds: dict,
    prior_keys: set[str],
    policy_sha256: str,
) -> dict:
    fields = {
        "symbol", "security_id", "screening_state", "decision_eligibility",
        "source_kind", "available_at_utc", "valid_until_utc", "source_sha256",
        "bars", "turnover", "depth", "spread", "slippage", "tick",
    }
    row = _exact(value, fields, "CANDIDATE_FIELDS_INVALID")
    blockers: list[str] = []
    if not isinstance(row["symbol"], str) or SYMBOL_RE.fullmatch(row["symbol"]) is None:
        raise KrxPaperCanaryEligibilityError("CANDIDATE_SYMBOL_INVALID")
    if not isinstance(row["security_id"], str) or SECURITY_ID_RE.fullmatch(row["security_id"]) is None:
        raise KrxPaperCanaryEligibilityError("CANDIDATE_SECURITY_ID_INVALID")
    if row["screening_state"] != "CATEGORICAL_CANDIDATE":
        _append(blockers, "CATEGORICAL_SCREEN_NOT_PASS")
    if row["decision_eligibility"] != "ELIGIBLE":
        _append(blockers, "UPSTREAM_DECISION_ELIGIBILITY_NOT_ELIGIBLE")
    if row["source_kind"] not in SOURCE_KINDS:
        raise KrxPaperCanaryEligibilityError("CANDIDATE_SOURCE_KIND_INVALID")
    if row["source_kind"] != expected_kind:
        _append(blockers, "CANDIDATE_SOURCE_KIND_MISMATCH")
    _sha(row["source_sha256"], "CANDIDATE_SOURCE_SHA_INVALID")
    _window(row, evaluated_at, "CANDIDATE", blockers)

    bars = row["bars"]
    if not isinstance(bars, dict) or list(bars) != list(REQUIRED_INTERVALS):
        raise KrxPaperCanaryEligibilityError("CANDIDATE_BAR_INTERVAL_COVERAGE_INVALID")
    for interval, raw in bars.items():
        bar = _exact(
            raw,
            {
                "interval", "completed", "latest_completed_slot_exact", "source_kind",
                "opened_at_utc", "closed_at_utc", "available_at_utc", "valid_until_utc",
                "source_sha256",
            },
            f"BAR_FIELDS_INVALID:{interval}",
        )
        if bar["interval"] != interval:
            raise KrxPaperCanaryEligibilityError(f"BAR_INTERVAL_IDENTITY_INVALID:{interval}")
        opened = _time(bar["opened_at_utc"], f"BAR_OPEN_INVALID:{interval}")
        closed = _time(bar["closed_at_utc"], f"BAR_CLOSE_INVALID:{interval}")
        if int((closed - opened).total_seconds()) != INTERVAL_SECONDS[interval]:
            raise KrxPaperCanaryEligibilityError(f"BAR_DURATION_INVALID:{interval}")
        if bar["completed"] is not True:
            _append(blockers, f"BAR_NOT_COMPLETED:{interval}")
        if bar["latest_completed_slot_exact"] is not True:
            _append(blockers, f"BAR_NOT_LATEST_COMPLETED_SLOT:{interval}")
        if bar["source_kind"] != expected_kind:
            _append(blockers, f"BAR_SOURCE_KIND_MISMATCH:{interval}")
        _sha(bar["source_sha256"], f"BAR_SOURCE_SHA_INVALID:{interval}")
        available = _time(bar["available_at_utc"], f"BAR_AVAILABLE_INVALID:{interval}")
        if not opened < closed <= available <= evaluated_at:
            raise KrxPaperCanaryEligibilityError(f"BAR_TIME_ORDER_INVALID:{interval}")
        _window(bar, evaluated_at, f"BAR_{interval}", blockers)

    turnover = _validate_evidence_row(
        row["turnover"],
        {"status", "source_kind", "value_krw", "available_at_utc", "valid_until_utc", "source_sha256"},
        evaluated_at, expected_kind, "TURNOVER", blockers,
    )
    depth = _validate_evidence_row(
        row["depth"],
        {
            "status", "source_kind", "bid_depth_krw", "ask_depth_krw",
            "available_at_utc", "valid_until_utc", "source_sha256",
        },
        evaluated_at, expected_kind, "DEPTH", blockers,
    )
    spread = _validate_evidence_row(
        row["spread"],
        {"status", "source_kind", "value_bps", "available_at_utc", "valid_until_utc", "source_sha256"},
        evaluated_at, expected_kind, "SPREAD", blockers,
    )
    slippage = _validate_evidence_row(
        row["slippage"],
        {
            "status", "source_kind", "order_notional_krw", "buy_impact_bps",
            "sell_impact_bps", "available_at_utc", "valid_until_utc", "source_sha256",
        },
        evaluated_at, expected_kind, "SLIPPAGE", blockers,
    )
    tick = _validate_evidence_row(
        row["tick"],
        {"status", "source_kind", "tick_size_krw", "available_at_utc", "valid_until_utc", "source_sha256"},
        evaluated_at, expected_kind, "TICK", blockers,
    )
    turnover_value = _nonnegative_int(turnover["value_krw"], "TURNOVER_VALUE_INVALID")
    bid_depth = _nonnegative_int(depth["bid_depth_krw"], "BID_DEPTH_INVALID")
    ask_depth = _nonnegative_int(depth["ask_depth_krw"], "ASK_DEPTH_INVALID")
    spread_bps = _decimal(spread["value_bps"], "SPREAD_VALUE_INVALID")
    slippage_notional = _nonnegative_int(
        slippage["order_notional_krw"], "SLIPPAGE_NOTIONAL_INVALID", positive=True
    )
    buy_impact = _decimal(slippage["buy_impact_bps"], "BUY_IMPACT_INVALID")
    sell_impact = _decimal(slippage["sell_impact_bps"], "SELL_IMPACT_INVALID")
    _nonnegative_int(tick["tick_size_krw"], "TICK_SIZE_INVALID", positive=True)

    if any(value is None for value in thresholds.values()):
        _append(blockers, "ELIGIBILITY_THRESHOLDS_UNRATIFIED")
    else:
        if turnover_value < thresholds["minimum_turnover_krw"]:
            _append(blockers, "TURNOVER_BELOW_MINIMUM")
        if bid_depth < thresholds["minimum_bid_depth_krw"]:
            _append(blockers, "BID_DEPTH_BELOW_MINIMUM")
        if ask_depth < thresholds["minimum_ask_depth_krw"]:
            _append(blockers, "ASK_DEPTH_BELOW_MINIMUM")
        if spread_bps > Decimal(thresholds["maximum_spread_bps"]):
            _append(blockers, "SPREAD_ABOVE_MAXIMUM")
        if slippage_notional != thresholds["slippage_order_notional_krw"]:
            _append(blockers, "SLIPPAGE_NOTIONAL_POLICY_MISMATCH")
        if max(buy_impact, sell_impact) > Decimal(
            thresholds["maximum_worst_side_slippage_bps"]
        ):
            _append(blockers, "SLIPPAGE_ABOVE_MAXIMUM")

    selection_key = payload_sha256({
        "market": "KOREA",
        "symbol": row["symbol"],
        "security_id": row["security_id"],
        "candidate_source_sha256": row["source_sha256"],
        "policy_source_sha256": policy_sha256,
        "evaluated_minute_utc": evaluated_at.strftime("%Y-%m-%dT%H:%M:00Z"),
    })
    if selection_key in prior_keys:
        _append(blockers, "DUPLICATE_SELECTION_KEY")

    rank = (
        -turnover_value,
        -min(bid_depth, ask_depth),
        spread_bps,
        max(buy_impact, sell_impact),
        row["symbol"],
        row["security_id"],
    )
    return {
        "symbol": row["symbol"],
        "security_id": row["security_id"],
        "selection_key": selection_key,
        "diagnostic_eligible": not blockers,
        "reason_codes": sorted(blockers),
        "rank_key": rank,
        "measurements": {
            "turnover_krw": turnover_value,
            "minimum_side_depth_krw": min(bid_depth, ask_depth),
            "spread_bps": str(spread_bps),
            "worst_side_slippage_bps": str(max(buy_impact, sell_impact)),
        },
    }


def _build_from_source(source: dict) -> dict:
    _exact(source, {"contract", "proposal", "input"}, "SOURCE_FIELDS_INVALID")
    contract = validate_contract(source["contract"])
    proposal = validate_proposal(source["proposal"])
    input_packet = validate_input(source["input"], contract)
    evaluated_at = _time(input_packet["evaluated_at_utc"], "EVALUATED_AT_INVALID")
    policy = input_packet["policy"]
    thresholds = policy["thresholds"]
    expected_kind = input_packet["evidence_mode"]

    global_blockers: list[str] = []
    if proposal["proposal_status"] != "RATIFIED" or proposal["ratified"] is not True:
        _append(global_blockers, "POLICY_PROPOSAL_UNRATIFIED")
    if proposal["effective_at_utc"] is None:
        _append(global_blockers, "POLICY_PROPOSAL_NOT_EFFECTIVE")
    if input_packet["gate_assessment"]["common_safety"] != "PASS":
        _append(global_blockers, "COMMON_SAFETY_NOT_PASS")
    if input_packet["gate_assessment"]["effective_krx_shadow"] != "PASS":
        _append(global_blockers, "EFFECTIVE_KRX_SHADOW_NOT_PASS")
    if input_packet["gate_assessment"]["current_state"] != "SHADOW":
        _append(global_blockers, "CURRENT_STATE_NOT_SHADOW")
    if input_packet["open_position_count"] >= contract["maximum_open_positions"]:
        _append(global_blockers, "PAPER_CANARY_POSITION_LIMIT_REACHED")
    if expected_kind == "NATURAL" and policy["status"] != "RATIFIED":
        _append(global_blockers, "NATURAL_POLICY_BINDING_NOT_RATIFIED")
    if expected_kind == "SYNTHETIC_TEST_FIXTURE" and policy["status"] != "TEST_FIXTURE_NOT_AUTHORITY":
        _append(global_blockers, "SYNTHETIC_POLICY_BINDING_INVALID")

    rows = [
        _candidate(
            raw, evaluated_at, expected_kind, thresholds,
            set(input_packet["prior_selection_keys"]), policy["source_sha256"],
        )
        for raw in input_packet["candidates"]
    ]
    eligible = sorted(
        (row for row in rows if row["diagnostic_eligible"]),
        key=lambda row: row["rank_key"],
    )
    selected = eligible[0] if eligible else None
    if selected is None:
        _append(global_blockers, "NO_CANDIDATE_WITH_COMPLETE_ELIGIBLE_EVIDENCE")
    if expected_kind == "NATURAL" and not input_packet["candidates"]:
        _append(global_blockers, "NATURAL_CANDIDATE_SET_EMPTY")

    candidate_audit = []
    for row in sorted(rows, key=lambda item: (item["symbol"], item["security_id"])):
        candidate_audit.append({
            "symbol": row["symbol"],
            "security_id": row["security_id"],
            "selection_key": row["selection_key"],
            "diagnostic_eligible": row["diagnostic_eligible"],
            "reason_codes": row["reason_codes"],
            "measurements": row["measurements"],
        })

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "market": "KOREA",
        "evaluated_at_utc": input_packet["evaluated_at_utc"],
        "selection_window_id": input_packet["selection_window_id"],
        "evidence_mode": expected_kind,
        "status": "LOCKED",
        "symbol": "NONE",
        "diagnostic_selected_symbol": selected["symbol"] if selected else None,
        "diagnostic_selection_key": selected["selection_key"] if selected else None,
        "candidate_audit": candidate_audit,
        "reason_codes": sorted(global_blockers),
        "summary": {
            "candidate_count": len(rows),
            "diagnostic_eligible_count": len(eligible),
            "authoritative_selected_count": 0,
            "order_draft_count": 0,
            "broker_submission_count": 0,
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
        "order_boundary": copy.deepcopy(ORDER_BOUNDARY),
        "source": copy.deepcopy(source),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_packet(
    input_packet: dict,
    contract: dict | None = None,
    proposal: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else validate_contract(contract)
    proposal = load_proposal() if proposal is None else validate_proposal(proposal)
    validate_input(input_packet, contract)
    return _build_from_source({
        "contract": copy.deepcopy(contract),
        "proposal": copy.deepcopy(proposal),
        "input": copy.deepcopy(input_packet),
    })


def validate_packet(packet: object) -> dict:
    fields = {
        "schema_version", "contract_version", "market", "evaluated_at_utc",
        "selection_window_id", "evidence_mode", "status", "symbol",
        "diagnostic_selected_symbol", "diagnostic_selection_key",
        "candidate_audit", "reason_codes", "summary", "authority",
        "order_boundary", "source", "packet_sha256",
    }
    row = _exact(packet, fields, "OUTPUT_FIELDS_INVALID")
    if (
        row["schema_version"] != OUTPUT_SCHEMA_VERSION
        or row["contract_version"] != CONTRACT_VERSION
        or row["market"] != "KOREA"
        or row["status"] != "LOCKED"
        or row["symbol"] != "NONE"
    ):
        raise KrxPaperCanaryEligibilityError("OUTPUT_LOCKED_IDENTITY_INVALID")
    if row["authority"] != AUTHORITY_ALL_FALSE or row["order_boundary"] != ORDER_BOUNDARY:
        raise KrxPaperCanaryEligibilityError("OUTPUT_AUTHORITY_ESCALATION")
    claimed = _sha(row["packet_sha256"], "OUTPUT_PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(row)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise KrxPaperCanaryEligibilityError("OUTPUT_PACKET_HASH_MISMATCH")
    rebuilt = _build_from_source(row["source"])
    if rebuilt != row:
        raise KrxPaperCanaryEligibilityError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(row)


def main() -> int:
    packet = build_packet(_read_json(DEFAULT_INPUT_PATH))
    print(json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
