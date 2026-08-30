#!/usr/bin/env python3
"""Deterministic KRX briefing-to-strategy SHADOW evaluator.

This module owns no symbol-selection, policy-ratification, account, or order
authority.  It consumes an explicit, hash-bound interface and calculates what
an externally supplied strategy plan would decide.  Contract v1 has no trusted
upstream policy adapters, so every final action fails closed to ``NO_TRADE``.
The counterfactual calculation is retained only as ``diagnostic_action``.

The output can never contain an order draft or submission authority.  Private
account inputs and generated runtime packets belong in private storage; this
public module and its committed fixtures contain synthetic values only.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import re
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_shadow_strategy_contract.json"
INPUT_SCHEMA_VERSION = "krx_shadow_strategy_input/1"
OUTPUT_SCHEMA_VERSION = "krx_shadow_strategy_packet/1"
CONTRACT_VERSION = "krx_shadow_strategy/1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SYMBOL_RE = re.compile(r"^\d{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d$")

ACTION_ENTER = "ENTER"
ACTION_HOLD = "HOLD"
ACTION_EXIT = "EXIT"
ACTION_NO_TRADE = "NO_TRADE"
ACTIONS = (ACTION_ENTER, ACTION_HOLD, ACTION_EXIT, ACTION_NO_TRADE)
ACTIVE_POLICY_STATUSES = {
    "RATIFIED_SHADOW_ONLY",
    "DIAGNOSTIC_DRAFT_NOT_AUTHORITY",
    "TEST_FIXTURE_NOT_AUTHORITY",
}
AUTHORITY_STATUSES = ACTIVE_POLICY_STATUSES | {"UNRATIFIED"}
REQUIRED_INTERVALS = ("15m", "1h", "1d")

AUTHORITY_BOUNDARY = {
    "shadow_evaluation_only": True,
    "symbol_selection_authority": False,
    "portfolio_action_authority": False,
    "order_draft_authority": False,
    "order_submission_authority": False,
    "paper_order_write": False,
    "real_trading": False,
    "production_trading": False,
}
ORDER_BOUNDARY = {
    "executable": False,
    "order_draft": None,
    "submission_authority": None,
}


class KrxShadowStrategyError(ValueError):
    """Fail-closed KRX SHADOW contract violation."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KrxShadowStrategyError(f"JSON_READ_FAILED:{path}") from exc
    if not isinstance(value, dict):
        raise KrxShadowStrategyError("JSON_OBJECT_REQUIRED")
    return value


def _expected_contract() -> dict:
    return {
        "schema_version": "krx_shadow_strategy_contract/1",
        "contract_version": CONTRACT_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "market": "KOREA",
        "required_completed_bar_intervals": list(REQUIRED_INTERVALS),
        "four_hour_bar_required": False,
        "actions": list(ACTIONS),
        "policy_statuses": sorted(AUTHORITY_STATUSES),
        "ratified_shadow_status": "RATIFIED_SHADOW_ONLY",
        "repository_default_strategy_policy": None,
        "repository_default_universe_eligibility": None,
        "ratified_shadow_policy_bindings": [],
        "paper_canary": {"symbol": "005930", "max_open_positions": 1},
        "authority": copy.deepcopy(AUTHORITY_BOUNDARY),
        "order_boundary": copy.deepcopy(ORDER_BOUNDARY),
    }


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    expected = _expected_contract()
    if value != expected:
        raise KrxShadowStrategyError("CONTRACT_DRIFT_OR_AUTHORITY_ESCALATION")
    return copy.deepcopy(value)


def _exact(value: object, fields: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise KrxShadowStrategyError(code)
    return value


def _utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise KrxShadowStrategyError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise KrxShadowStrategyError(code) from exc
    return parsed


def _date(value: object, code: str) -> str:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise KrxShadowStrategyError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KrxShadowStrategyError(code) from exc
    if parsed.isoformat() != value:
        raise KrxShadowStrategyError(code)
    return value


def _token(value: object, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise KrxShadowStrategyError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise KrxShadowStrategyError(code)
    return value


def _positive_int(value: object, code: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KrxShadowStrategyError(code)
    if value < 0 or (value == 0 and not allow_zero):
        raise KrxShadowStrategyError(code)
    return value


def _bps(value: object, code: str) -> int:
    parsed = _positive_int(value, code, allow_zero=True)
    if parsed > 10_000:
        raise KrxShadowStrategyError(code)
    return parsed


def _window(value: dict, evaluated_at: dt.datetime, prefix: str) -> list[str]:
    available = _utc(value.get("available_at"), f"{prefix}_AVAILABLE_AT_INVALID")
    valid_until = _utc(value.get("valid_until"), f"{prefix}_VALID_UNTIL_INVALID")
    if valid_until < available:
        raise KrxShadowStrategyError(f"{prefix}_WINDOW_INVALID")
    if available > evaluated_at:
        return [f"{prefix}_FUTURE"]
    if valid_until < evaluated_at:
        return [f"{prefix}_STALE"]
    return []


def _validate_authority_status(value: object, code: str) -> str:
    if value not in AUTHORITY_STATUSES:
        raise KrxShadowStrategyError(code)
    return value


def _validate_bar(
    interval: str, value: object, evaluated_at: dt.datetime
) -> tuple[dict, list[str]]:
    row = _exact(
        value,
        {
            "interval", "completed", "opened_at", "closed_at", "available_at",
            "valid_until", "open", "high", "low", "close", "source_sha256",
        },
        f"BAR_FIELDS_INVALID:{interval}",
    )
    if row["interval"] != interval:
        raise KrxShadowStrategyError(f"BAR_INTERVAL_MISMATCH:{interval}")
    opened = _utc(row["opened_at"], f"BAR_OPENED_AT_INVALID:{interval}")
    closed = _utc(row["closed_at"], f"BAR_CLOSED_AT_INVALID:{interval}")
    available = _utc(row["available_at"], f"BAR_AVAILABLE_AT_INVALID:{interval}")
    _sha(row["source_sha256"], f"BAR_SOURCE_SHA_INVALID:{interval}")
    if not isinstance(row["completed"], bool):
        raise KrxShadowStrategyError(f"BAR_COMPLETION_INVALID:{interval}")
    prices = {
        key: _positive_int(row[key], f"BAR_PRICE_INVALID:{interval}:{key}")
        for key in ("open", "high", "low", "close")
    }
    if not prices["low"] <= min(prices["open"], prices["close"]):
        raise KrxShadowStrategyError(f"BAR_OHLC_INVALID:{interval}")
    if not prices["high"] >= max(prices["open"], prices["close"]):
        raise KrxShadowStrategyError(f"BAR_OHLC_INVALID:{interval}")
    if opened >= closed or available < closed:
        raise KrxShadowStrategyError(f"BAR_TIME_ORDER_INVALID:{interval}")
    duration = int((closed - opened).total_seconds())
    expected_duration = {"15m": 15 * 60, "1h": 60 * 60, "1d": 6 * 60 * 60 + 30 * 60}[interval]
    if duration != expected_duration:
        raise KrxShadowStrategyError(f"BAR_DURATION_INVALID:{interval}")
    opened_kst = opened.astimezone(ZoneInfo("Asia/Seoul"))
    closed_kst = closed.astimezone(ZoneInfo("Asia/Seoul"))
    if opened_kst.date() != closed_kst.date():
        raise KrxShadowStrategyError(f"BAR_SESSION_DATE_INVALID:{interval}")
    opened_hms = opened_kst.strftime("%H:%M:%S")
    closed_hms = closed_kst.strftime("%H:%M:%S")
    if interval == "15m" and (
        opened_hms < "09:00:00" or closed_hms > "15:30:00"
        or opened_kst.second != 0 or opened_kst.minute % 15 != 0
    ):
        raise KrxShadowStrategyError("BAR_SESSION_BOUNDARY_INVALID:15m")
    if interval == "1h" and (
        opened_hms < "09:00:00" or closed_hms > "15:30:00"
        or opened_kst.minute != 0 or opened_kst.second != 0
    ):
        raise KrxShadowStrategyError("BAR_SESSION_BOUNDARY_INVALID:1h")
    if interval == "1d" and (opened_hms != "09:00:00" or closed_hms != "15:30:00"):
        raise KrxShadowStrategyError("BAR_SESSION_BOUNDARY_INVALID:1d")
    blockers = _window(row, evaluated_at, f"BAR_{interval.upper()}")
    if row["completed"] is not True or closed > evaluated_at:
        blockers.append(f"BAR_{interval.upper()}_INCOMPLETE")
    return copy.deepcopy(row), blockers


def _validate_input(value: dict, contract: dict) -> dict:
    _exact(
        value,
        {
            "schema_version", "contract_version", "decision_batch_id", "evaluated_at",
            "business_date", "mode", "prior_decision_keys", "candidates", "authority",
            "packet_sha256",
        },
        "INPUT_FIELDS_INVALID",
    )
    if (
        value["schema_version"] != INPUT_SCHEMA_VERSION
        or value["contract_version"] != CONTRACT_VERSION
        or value["authority"] != AUTHORITY_BOUNDARY
    ):
        raise KrxShadowStrategyError("INPUT_IDENTITY_OR_AUTHORITY_INVALID")
    _token(value["decision_batch_id"], "DECISION_BATCH_ID_INVALID")
    evaluated_at = _utc(value["evaluated_at"], "EVALUATED_AT_INVALID")
    business_date = _date(value["business_date"], "BUSINESS_DATE_INVALID")
    if evaluated_at.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat() != business_date:
        raise KrxShadowStrategyError("BUSINESS_DATE_EVALUATION_MISMATCH")
    if value["mode"] not in {"PAPER_CANARY", "DYNAMIC_BRIEFING"}:
        raise KrxShadowStrategyError("MODE_INVALID")
    if (
        not isinstance(value["prior_decision_keys"], list)
        or len(set(value["prior_decision_keys"])) != len(value["prior_decision_keys"])
        or any(not isinstance(item, str) or SHA256_RE.fullmatch(item) is None for item in value["prior_decision_keys"])
    ):
        raise KrxShadowStrategyError("PRIOR_DECISION_KEYS_INVALID")
    if not isinstance(value["candidates"], list) or not value["candidates"]:
        raise KrxShadowStrategyError("CANDIDATES_REQUIRED")
    claimed = _sha(value["packet_sha256"], "INPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise KrxShadowStrategyError("INPUT_SHA256_MISMATCH")
    return copy.deepcopy(value)


def _validate_candidate(candidate: object, evaluated_at: dt.datetime) -> tuple[dict, list[str]]:
    row = _exact(
        candidate,
        {
            "candidate_id", "symbol", "briefing_rank", "identity", "eligibility",
            "market_context", "relative_strength", "liquidity", "bars", "quote",
            "position", "trade_plan", "risk_budget", "source_sha256",
        },
        "CANDIDATE_FIELDS_INVALID",
    )
    _token(row["candidate_id"], "CANDIDATE_ID_INVALID")
    if not isinstance(row["symbol"], str) or SYMBOL_RE.fullmatch(row["symbol"]) is None:
        raise KrxShadowStrategyError("CANDIDATE_SYMBOL_INVALID")
    _positive_int(row["briefing_rank"], "BRIEFING_RANK_INVALID")
    _sha(row["source_sha256"], "CANDIDATE_SOURCE_SHA_INVALID")
    blockers: list[str] = []

    identity = _exact(
        row["identity"],
        {"status", "symbol", "canonical_instrument_id", "available_at", "valid_until", "source_sha256"},
        "IDENTITY_FIELDS_INVALID",
    )
    if identity["symbol"] != row["symbol"]:
        raise KrxShadowStrategyError("IDENTITY_SYMBOL_MISMATCH")
    _sha(identity["source_sha256"], "IDENTITY_SOURCE_SHA_INVALID")
    blockers.extend(_window(identity, evaluated_at, "IDENTITY"))
    expected_canonical_id = f"KRX:{row['symbol']}:COMMON"
    if identity["status"] != "RESOLVED" or identity["canonical_instrument_id"] != expected_canonical_id:
        blockers.append("IDENTITY_NOT_RESOLVED")

    eligibility = _exact(
        row["eligibility"],
        {"status", "authority_status", "available_at", "valid_until", "source_sha256"},
        "ELIGIBILITY_FIELDS_INVALID",
    )
    _validate_authority_status(eligibility["authority_status"], "ELIGIBILITY_AUTHORITY_INVALID")
    _sha(eligibility["source_sha256"], "ELIGIBILITY_SOURCE_SHA_INVALID")
    blockers.extend(_window(eligibility, evaluated_at, "ELIGIBILITY"))
    if eligibility["status"] != "ELIGIBLE":
        blockers.append("SYMBOL_NOT_ELIGIBLE")

    market = _exact(
        row["market_context"],
        {"status", "authority_status", "entry_allowed", "hold_allowed", "available_at", "valid_until", "source_sha256"},
        "MARKET_CONTEXT_FIELDS_INVALID",
    )
    _validate_authority_status(market["authority_status"], "MARKET_AUTHORITY_INVALID")
    _sha(market["source_sha256"], "MARKET_SOURCE_SHA_INVALID")
    blockers.extend(_window(market, evaluated_at, "MARKET_CONTEXT"))
    if market["status"] != "AVAILABLE" or not isinstance(market["entry_allowed"], bool) or not isinstance(market["hold_allowed"], bool):
        blockers.append("MARKET_CONTEXT_NOT_ACTIONABLE")

    relative = _exact(
        row["relative_strength"],
        {"status", "authority_status", "entry_confirmed", "hold_confirmed", "available_at", "valid_until", "source_sha256"},
        "RELATIVE_STRENGTH_FIELDS_INVALID",
    )
    _validate_authority_status(relative["authority_status"], "RELATIVE_STRENGTH_AUTHORITY_INVALID")
    _sha(relative["source_sha256"], "RELATIVE_STRENGTH_SOURCE_SHA_INVALID")
    blockers.extend(_window(relative, evaluated_at, "RELATIVE_STRENGTH"))
    if relative["status"] != "AVAILABLE" or not isinstance(relative["entry_confirmed"], bool) or not isinstance(relative["hold_confirmed"], bool):
        blockers.append("RELATIVE_STRENGTH_NOT_ACTIONABLE")

    liquidity = _exact(
        row["liquidity"],
        {"status", "authority_status", "eligible", "max_shadow_quantity", "available_at", "valid_until", "source_sha256"},
        "LIQUIDITY_FIELDS_INVALID",
    )
    _validate_authority_status(liquidity["authority_status"], "LIQUIDITY_AUTHORITY_INVALID")
    _sha(liquidity["source_sha256"], "LIQUIDITY_SOURCE_SHA_INVALID")
    blockers.extend(_window(liquidity, evaluated_at, "LIQUIDITY"))
    if liquidity["status"] != "AVAILABLE" or not isinstance(liquidity["eligible"], bool):
        blockers.append("LIQUIDITY_NOT_ACTIONABLE")
    if liquidity["max_shadow_quantity"] is not None:
        _positive_int(liquidity["max_shadow_quantity"], "LIQUIDITY_QUANTITY_CAP_INVALID")

    bars = _exact(row["bars"], set(REQUIRED_INTERVALS), "BAR_INTERVAL_SET_INVALID")
    for interval in REQUIRED_INTERVALS:
        _, bar_blockers = _validate_bar(interval, bars[interval], evaluated_at)
        blockers.extend(bar_blockers)
    evaluated_kst = evaluated_at.astimezone(ZoneInfo("Asia/Seoul"))
    session_open = evaluated_kst.replace(hour=9, minute=0, second=0, microsecond=0)
    session_close = evaluated_kst.replace(hour=15, minute=30, second=0, microsecond=0)
    reference_time = min(evaluated_kst, session_close)
    if reference_time < session_open + dt.timedelta(minutes=15):
        blockers.append("NO_COMPLETED_15M_IN_CURRENT_SESSION")
    else:
        elapsed_seconds = int((reference_time - session_open).total_seconds())
        completed_slots = elapsed_seconds // (15 * 60)
        expected_close = session_open + dt.timedelta(minutes=15 * completed_slots)
        actual_close = _utc(bars["15m"]["closed_at"], "BAR_CLOSED_AT_INVALID:15m")
        if actual_close != expected_close.astimezone(dt.timezone.utc):
            blockers.append("BAR_15M_NOT_LATEST_COMPLETED_SESSION_SLOT")

    quote = _exact(
        row["quote"],
        {"status", "observed_at", "available_at", "valid_until", "last", "bid", "ask", "source_sha256"},
        "QUOTE_FIELDS_INVALID",
    )
    observed_at = _utc(quote["observed_at"], "QUOTE_OBSERVED_AT_INVALID")
    available_at = _utc(quote["available_at"], "QUOTE_AVAILABLE_AT_INVALID")
    if observed_at > available_at:
        raise KrxShadowStrategyError("QUOTE_OBSERVED_AFTER_AVAILABLE")
    _sha(quote["source_sha256"], "QUOTE_SOURCE_SHA_INVALID")
    blockers.extend(_window(quote, evaluated_at, "QUOTE"))
    if quote["status"] != "AVAILABLE":
        blockers.append("QUOTE_NOT_AVAILABLE")
    for key in ("last", "bid", "ask"):
        _positive_int(quote[key], f"QUOTE_PRICE_INVALID:{key}")
    if not quote["bid"] <= quote["last"] <= quote["ask"]:
        blockers.append("QUOTE_BOOK_LAST_INCONSISTENT")

    position = _exact(
        row["position"],
        {
            "status", "entry_price", "quantity", "opened_at", "take_profit_1_done",
            "available_at", "valid_until", "source_sha256",
        },
        "POSITION_FIELDS_INVALID",
    )
    _sha(position["source_sha256"], "POSITION_SOURCE_SHA_INVALID")
    blockers.extend(_window(position, evaluated_at, "POSITION"))
    if position["status"] == "FLAT":
        if any(position[key] is not None for key in ("entry_price", "quantity", "opened_at", "take_profit_1_done")):
            raise KrxShadowStrategyError("FLAT_POSITION_FIELDS_MUST_BE_NULL")
    elif position["status"] == "OPEN":
        _positive_int(position["entry_price"], "POSITION_ENTRY_PRICE_INVALID")
        _positive_int(position["quantity"], "POSITION_QUANTITY_INVALID")
        opened_at = _utc(position["opened_at"], "POSITION_OPENED_AT_INVALID")
        if opened_at > evaluated_at:
            blockers.append("POSITION_OPENED_AT_FUTURE")
        if opened_at > _utc(position["available_at"], "POSITION_AVAILABLE_AT_INVALID"):
            blockers.append("POSITION_OPENED_AFTER_SNAPSHOT_AVAILABLE")
        if not isinstance(position["take_profit_1_done"], bool):
            raise KrxShadowStrategyError("POSITION_TAKE_PROFIT_STATE_INVALID")
    else:
        raise KrxShadowStrategyError("POSITION_STATUS_INVALID")

    plan = _exact(
        row["trade_plan"],
        {
            "policy_id", "status", "entry_reference_price", "max_entry_price",
            "stop_price", "take_profit_1_price", "final_take_profit_price",
            "take_profit_1_fraction_bps", "expires_at", "invalidation_triggered",
            "exit_on_regime_block", "exit_on_relative_strength_break", "tick_size",
            "entry_fee_bps", "exit_fee_bps", "stop_slippage_bps", "entry_after_kst",
            "session_ends_kst", "quote_max_age_seconds", "max_spread_bps",
            "available_at", "valid_until", "source_sha256",
        },
        "TRADE_PLAN_FIELDS_INVALID",
    )
    _token(plan["policy_id"], "POLICY_ID_INVALID")
    status = _validate_authority_status(plan["status"], "POLICY_STATUS_INVALID")
    _sha(plan["source_sha256"], "POLICY_SOURCE_SHA_INVALID")
    blockers.extend(_window(plan, evaluated_at, "TRADE_PLAN"))
    if status in ACTIVE_POLICY_STATUSES:
        for key in (
            "entry_reference_price", "max_entry_price", "stop_price",
            "take_profit_1_price", "final_take_profit_price", "tick_size",
        ):
            _positive_int(plan[key], f"POLICY_PRICE_INVALID:{key}")
        fraction = _bps(plan["take_profit_1_fraction_bps"], "TAKE_PROFIT_1_FRACTION_INVALID")
        if fraction <= 0 or fraction >= 10_000:
            raise KrxShadowStrategyError("TAKE_PROFIT_1_FRACTION_MUST_BE_PARTIAL")
        for key in ("entry_fee_bps", "exit_fee_bps", "stop_slippage_bps"):
            _bps(plan[key], f"POLICY_BPS_INVALID:{key}")
        _bps(plan["max_spread_bps"], "POLICY_BPS_INVALID:max_spread_bps")
        _positive_int(plan["quote_max_age_seconds"], "QUOTE_MAX_AGE_SECONDS_INVALID")
        for key in ("entry_after_kst", "session_ends_kst"):
            if not isinstance(plan[key], str) or TIME_RE.fullmatch(plan[key]) is None:
                raise KrxShadowStrategyError(f"POLICY_SESSION_TIME_INVALID:{key}")
            try:
                dt.time.fromisoformat(plan[key])
            except ValueError as exc:
                raise KrxShadowStrategyError(f"POLICY_SESSION_TIME_INVALID:{key}") from exc
        if plan["entry_after_kst"] >= plan["session_ends_kst"]:
            raise KrxShadowStrategyError("POLICY_SESSION_WINDOW_INVALID")
        _utc(plan["expires_at"], "POLICY_EXPIRY_INVALID")
        for key in ("invalidation_triggered", "exit_on_regime_block", "exit_on_relative_strength_break"):
            if not isinstance(plan[key], bool):
                raise KrxShadowStrategyError(f"POLICY_BOOLEAN_INVALID:{key}")
        if not (
            plan["stop_price"] < plan["entry_reference_price"] <= plan["max_entry_price"]
            < plan["take_profit_1_price"] < plan["final_take_profit_price"]
        ):
            raise KrxShadowStrategyError("POLICY_PRICE_ORDER_INVALID")
        if plan["entry_reference_price"] != bars["15m"]["high"]:
            blockers.append("ENTRY_REFERENCE_NOT_COMPLETED_15M_HIGH")
        for price_key in (
            "entry_reference_price", "max_entry_price", "stop_price",
            "take_profit_1_price", "final_take_profit_price",
        ):
            if plan[price_key] % plan["tick_size"] != 0:
                blockers.append(f"TICK_SIZE_MISMATCH:{price_key}")
        for price_key in ("last", "bid", "ask"):
            if quote[price_key] % plan["tick_size"] != 0:
                blockers.append(f"TICK_SIZE_MISMATCH:quote_{price_key}")
    else:
        numeric = (
            "entry_reference_price", "max_entry_price", "stop_price",
            "take_profit_1_price", "final_take_profit_price",
            "take_profit_1_fraction_bps", "expires_at", "invalidation_triggered",
            "exit_on_regime_block", "exit_on_relative_strength_break", "tick_size",
            "entry_fee_bps", "exit_fee_bps", "stop_slippage_bps", "entry_after_kst",
            "session_ends_kst", "quote_max_age_seconds", "max_spread_bps",
        )
        if any(plan[key] is not None for key in numeric):
            raise KrxShadowStrategyError("UNRATIFIED_POLICY_VALUES_MUST_BE_NULL")
        blockers.append("STRATEGY_POLICY_UNRATIFIED")

    risk = _exact(
        row["risk_budget"],
        {
            "status", "allocation_id", "allocation_scope", "account_risk_budget_id",
            "account_risk_budget_total_krw", "account_committed_risk_krw",
            "risk_budget_krw",
            "account_capacity_quantity", "current_open_positions", "available_at",
            "valid_until", "source_sha256",
        },
        "RISK_BUDGET_FIELDS_INVALID",
    )
    risk_status = _validate_authority_status(risk["status"], "RISK_STATUS_INVALID")
    _token(risk["allocation_id"], "RISK_ALLOCATION_ID_INVALID")
    _token(risk["account_risk_budget_id"], "ACCOUNT_RISK_BUDGET_ID_INVALID")
    if risk["allocation_scope"] != "PER_CANDIDATE_PREALLOCATED_FROM_ACCOUNT_RISK_BUDGET":
        raise KrxShadowStrategyError("RISK_ALLOCATION_SCOPE_INVALID")
    _sha(risk["source_sha256"], "RISK_SOURCE_SHA_INVALID")
    blockers.extend(_window(risk, evaluated_at, "RISK_BUDGET"))
    _positive_int(risk["current_open_positions"], "OPEN_POSITION_COUNT_INVALID", allow_zero=True)
    if risk_status in ACTIVE_POLICY_STATUSES:
        _positive_int(risk["account_risk_budget_total_krw"], "ACCOUNT_RISK_BUDGET_TOTAL_INVALID")
        _positive_int(
            risk["account_committed_risk_krw"],
            "ACCOUNT_COMMITTED_RISK_INVALID",
            allow_zero=True,
        )
        _positive_int(risk["risk_budget_krw"], "RISK_BUDGET_INVALID")
        _positive_int(risk["account_capacity_quantity"], "ACCOUNT_CAPACITY_INVALID")
        if risk["risk_budget_krw"] > risk["account_risk_budget_total_krw"]:
            raise KrxShadowStrategyError("RISK_ALLOCATION_EXCEEDS_ACCOUNT_BUDGET")
    elif any(risk[key] is not None for key in (
        "account_risk_budget_total_krw", "account_committed_risk_krw",
        "risk_budget_krw", "account_capacity_quantity"
    )):
        raise KrxShadowStrategyError("UNRATIFIED_RISK_VALUES_MUST_BE_NULL")
    else:
        blockers.append("RISK_BUDGET_UNRATIFIED")

    return copy.deepcopy(row), sorted(set(blockers))


def _round_up_bps(price: int, bps: int) -> int:
    value = (Decimal(price) * Decimal(bps) / Decimal(10_000)).quantize(
        Decimal("1"), rounding=ROUND_CEILING
    )
    return int(value)


def _loss_per_share(entry: int, stop: int, plan: dict) -> tuple[int, dict]:
    entry_fee = _round_up_bps(entry, plan["entry_fee_bps"])
    exit_fee = _round_up_bps(stop, plan["exit_fee_bps"])
    stop_slippage = _round_up_bps(stop, plan["stop_slippage_bps"])
    price_risk = entry - stop
    total = price_risk + entry_fee + exit_fee + stop_slippage
    if total <= 0:
        raise KrxShadowStrategyError("PLANNED_LOSS_PER_SHARE_NOT_POSITIVE")
    return total, {
        "price_risk_krw": price_risk,
        "entry_fee_buffer_krw": entry_fee,
        "exit_fee_buffer_krw": exit_fee,
        "stop_slippage_buffer_krw": stop_slippage,
    }


def _spread_bps(bid: int, ask: int) -> Decimal:
    midpoint = (Decimal(bid) + Decimal(ask)) / Decimal(2)
    return (Decimal(ask - bid) * Decimal(10_000)) / midpoint


def _decision_key(candidate: dict, evaluated_at: str) -> str:
    minute = evaluated_at[:16] + ":00Z"
    return payload_sha256({
        "market": "KOREA",
        "symbol": candidate["symbol"],
        "policy_id": candidate["trade_plan"]["policy_id"],
        "policy_source_sha256": candidate["trade_plan"]["source_sha256"],
        "observation_minute": minute,
    })


def _calculate(candidate: dict, blockers: list[str], evaluated_at: dt.datetime) -> dict:
    position = candidate["position"]
    plan = candidate["trade_plan"]
    quote = candidate["quote"]
    market = candidate["market_context"]
    relative = candidate["relative_strength"]
    liquidity = candidate["liquidity"]
    risk = candidate["risk_budget"]
    if blockers or plan["status"] not in ACTIVE_POLICY_STATUSES:
        return {
            "diagnostic_action": ACTION_NO_TRADE,
            "action_stage": None,
            "planned_entry_price": None,
            "quantity": None,
            "planned_loss_per_share_krw": None,
            "planned_loss_krw": None,
            "cost_breakdown": None,
            "reason_codes": sorted(set(blockers)),
        }

    reasons: list[str] = []
    action = ACTION_NO_TRADE
    stage = None
    planned_entry = None
    quantity = None
    per_share = None
    planned_loss = None
    cost_breakdown = None

    if position["status"] == "OPEN":
        planned_entry = position["entry_price"]
        quantity = position["quantity"]
        per_share, cost_breakdown = _loss_per_share(planned_entry, plan["stop_price"], plan)
        planned_loss = per_share * quantity
        if plan["invalidation_triggered"]:
            action, stage, reasons = ACTION_EXIT, "INVALIDATION", ["PLAN_INVALIDATION_TRIGGERED"]
        elif evaluated_at >= _utc(plan["expires_at"], "POLICY_EXPIRY_INVALID"):
            action, stage, reasons = ACTION_EXIT, "TIME_EXPIRY", ["POSITION_TIME_EXPIRED"]
        elif quote["bid"] <= plan["stop_price"]:
            action, stage, reasons = ACTION_EXIT, "STOP", ["STOP_PRICE_REACHED"]
        elif plan["exit_on_regime_block"] and not market["hold_allowed"]:
            action, stage, reasons = ACTION_EXIT, "REGIME_INVALIDATION", ["MARKET_HOLD_PERMISSION_WITHDRAWN"]
        elif plan["exit_on_relative_strength_break"] and not relative["hold_confirmed"]:
            action, stage, reasons = ACTION_EXIT, "RELATIVE_STRENGTH_INVALIDATION", ["RELATIVE_STRENGTH_HOLD_CONFIRMATION_LOST"]
        elif quote["bid"] >= plan["final_take_profit_price"]:
            action, stage, reasons = ACTION_EXIT, "FINAL_TAKE_PROFIT", ["FINAL_TAKE_PROFIT_REACHED"]
        elif quote["bid"] >= plan["take_profit_1_price"] and not position["take_profit_1_done"]:
            action, stage, reasons = ACTION_EXIT, "TAKE_PROFIT_1", ["TAKE_PROFIT_1_REACHED"]
            quantity = max(1, (position["quantity"] * plan["take_profit_1_fraction_bps"]) // 10_000)
            planned_loss = per_share * quantity
        else:
            action, stage, reasons = ACTION_HOLD, "WITHIN_PLAN", ["POSITION_WITHIN_RATIFIED_PLAN"]
    else:
        quote_age_seconds = int(
            (evaluated_at - _utc(quote["observed_at"], "QUOTE_OBSERVED_AT_INVALID")).total_seconds()
        )
        trade_time_kst = evaluated_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%H:%M:%S")
        if quote_age_seconds < 0 or quote_age_seconds > plan["quote_max_age_seconds"]:
            reasons.append("QUOTE_EXCEEDS_POLICY_MAX_AGE")
        if _spread_bps(quote["bid"], quote["ask"]) > Decimal(plan["max_spread_bps"]):
            reasons.append("SPREAD_EXCEEDS_POLICY_MAX")
        if trade_time_kst < plan["entry_after_kst"] or trade_time_kst > plan["session_ends_kst"]:
            reasons.append("OUTSIDE_KRX_POLICY_SESSION")
        if plan["invalidation_triggered"]:
            reasons.append("PLAN_INVALIDATION_TRIGGERED")
        if evaluated_at >= _utc(plan["expires_at"], "POLICY_EXPIRY_INVALID"):
            reasons.append("ENTRY_PLAN_EXPIRED")
        if not market["entry_allowed"]:
            reasons.append("MARKET_ENTRY_NOT_ALLOWED")
        if not relative["entry_confirmed"]:
            reasons.append("RELATIVE_STRENGTH_ENTRY_NOT_CONFIRMED")
        if not liquidity["eligible"]:
            reasons.append("LIQUIDITY_NOT_ELIGIBLE")
        if quote["last"] <= plan["entry_reference_price"]:
            reasons.append("ENTRY_REFERENCE_NOT_BROKEN")
        if quote["ask"] <= plan["entry_reference_price"]:
            reasons.append("ENTRY_ASK_NOT_ABOVE_REFERENCE")
        if quote["ask"] > plan["max_entry_price"]:
            reasons.append("ENTRY_GAP_ABOVE_MAX_PRICE")
        if not reasons:
            planned_entry = quote["ask"]
            per_share, cost_breakdown = _loss_per_share(planned_entry, plan["stop_price"], plan)
            raw_quantity = risk["risk_budget_krw"] // per_share
            caps = [raw_quantity, risk["account_capacity_quantity"]]
            if liquidity["max_shadow_quantity"] is not None:
                caps.append(liquidity["max_shadow_quantity"])
            quantity = min(caps)
            if quantity < 1:
                reasons.append("RISK_BUDGET_BELOW_ONE_SHARE_PLANNED_LOSS")
                quantity = None
                planned_entry = None
                per_share = None
                cost_breakdown = None
            else:
                action, stage = ACTION_ENTER, "ENTRY_TRIGGER"
                reasons.append("COMPLETED_15M_BREAKOUT_WITH_REQUIRED_GATES")
                planned_loss = per_share * quantity

    return {
        "diagnostic_action": action,
        "action_stage": stage,
        "planned_entry_price": planned_entry,
        "quantity": quantity,
        "planned_loss_per_share_krw": per_share,
        "planned_loss_krw": planned_loss,
        "cost_breakdown": cost_breakdown,
        "reason_codes": sorted(set(reasons)),
    }


def _authoritative_shadow_ready(candidate: dict, contract: dict) -> bool:
    # v1 has no trusted adapters that independently load and validate the
    # claimed policy/eligibility/market/risk sources.  Caller-supplied status
    # strings and hashes are not authority.  A future positive action requires
    # a separately versioned contract with exact upstream validators.
    if contract["ratified_shadow_policy_bindings"] != []:
        raise KrxShadowStrategyError("V1_RATIFIED_POLICY_BINDINGS_MUST_REMAIN_EMPTY")
    return False


def _briefing_sentence(row: dict) -> str:
    symbol = row["symbol"]
    diagnostic = row["diagnostic_action"]
    action = row["action"]
    if action == ACTION_NO_TRADE and diagnostic != ACTION_NO_TRADE:
        return (
            f"{symbol}: 계산상 SHADOW {diagnostic}이지만 정책 또는 eligibility 권한이 "
            "미비준이어서 NO_TRADE입니다. 주문 초안과 제출 권한은 없습니다."
        )
    if action == ACTION_ENTER:
        return (
            f"{symbol}: 완료된 15분봉 기준과 시장·상대강도·유동성·위험 관문이 "
            "충족되어 SHADOW ENTER입니다. 주문 초안과 제출 권한은 없습니다."
        )
    if action == ACTION_HOLD:
        return (
            f"{symbol}: 손절·익절·시간만료·무효화 조건에 닿지 않아 SHADOW HOLD입니다. "
            "주문 초안과 제출 권한은 없습니다."
        )
    if action == ACTION_EXIT:
        return (
            f"{symbol}: {row['action_stage']} 조건으로 SHADOW EXIT입니다. "
            "주문 초안과 제출 권한은 없습니다."
        )
    reasons = ", ".join(row["reason_codes"]) or "ENTRY_CONDITION_NOT_MET"
    return f"{symbol}: {reasons} 때문에 NO_TRADE입니다. 주문 초안과 제출 권한은 없습니다."


def _build_from_source(source: dict) -> dict:
    _exact(source, {"contract", "input"}, "SOURCE_FIELDS_INVALID")
    contract = source.get("contract")
    if contract != load_contract():
        raise KrxShadowStrategyError("EMBEDDED_CONTRACT_MISMATCH")
    input_packet = _validate_input(source.get("input"), contract)
    evaluated_at = _utc(input_packet["evaluated_at"], "EVALUATED_AT_INVALID")
    prior = set(input_packet["prior_decision_keys"])
    candidates: list[tuple[dict, list[str]]] = []
    ids: set[str] = set()
    symbols: set[str] = set()
    ranks: set[int] = set()
    allocation_ids: set[str] = set()
    account_budget_keys: set[tuple[object, object, object]] = set()
    open_position_counts: set[int] = set()
    new_allocation_total = 0
    open_candidate_count = 0
    for raw in input_packet["candidates"]:
        candidate, blockers = _validate_candidate(raw, evaluated_at)
        if candidate["candidate_id"] in ids or candidate["symbol"] in symbols or candidate["briefing_rank"] in ranks:
            raise KrxShadowStrategyError("CANDIDATE_ID_SYMBOL_OR_RANK_DUPLICATE")
        ids.add(candidate["candidate_id"])
        symbols.add(candidate["symbol"])
        ranks.add(candidate["briefing_rank"])
        risk = candidate["risk_budget"]
        if risk["allocation_id"] in allocation_ids:
            raise KrxShadowStrategyError("RISK_ALLOCATION_ID_DUPLICATE")
        allocation_ids.add(risk["allocation_id"])
        account_budget_keys.add((
            risk["account_risk_budget_id"], risk["account_risk_budget_total_krw"],
            risk["account_committed_risk_krw"],
        ))
        open_position_counts.add(risk["current_open_positions"])
        if candidate["position"]["status"] == "OPEN":
            open_candidate_count += 1
        elif risk["risk_budget_krw"] is not None:
            new_allocation_total += risk["risk_budget_krw"]
        candidates.append((candidate, blockers))
    if len(account_budget_keys) != 1 or len(open_position_counts) != 1:
        raise KrxShadowStrategyError("ACCOUNT_RISK_BUDGET_BATCH_MISMATCH")
    account_budget_key = next(iter(account_budget_keys))
    account_budget_total = account_budget_key[1]
    committed_risk = account_budget_key[2]
    reported_open_positions = next(iter(open_position_counts))
    if reported_open_positions < open_candidate_count:
        raise KrxShadowStrategyError("OPEN_POSITION_COUNT_BELOW_BATCH_OPEN_CANDIDATES")
    if reported_open_positions > 0 and committed_risk == 0:
        raise KrxShadowStrategyError("OPEN_POSITION_COUNT_WITHOUT_COMMITTED_RISK")
    if reported_open_positions == 0 and committed_risk not in (None, 0):
        raise KrxShadowStrategyError("COMMITTED_RISK_WITHOUT_OPEN_POSITION")
    if (
        account_budget_total is not None
        and committed_risk is not None
        and committed_risk + new_allocation_total > account_budget_total
    ):
        raise KrxShadowStrategyError("AGGREGATE_RISK_ALLOCATION_EXCEEDS_ACCOUNT_BUDGET")
    if (
        input_packet["mode"] == "PAPER_CANARY"
        and open_candidate_count > 0
        and reported_open_positions != 1
    ):
        raise KrxShadowStrategyError("PAPER_CANARY_OPEN_POSITION_COUNT_INVALID")

    decisions = []
    for candidate, blockers in sorted(candidates, key=lambda pair: (pair[0]["briefing_rank"], pair[0]["symbol"])):
        key = _decision_key(candidate, input_packet["evaluated_at"])
        if key in prior:
            blockers.append("DUPLICATE_DECISION")
        if input_packet["mode"] == "PAPER_CANARY":
            if candidate["symbol"] != contract["paper_canary"]["symbol"]:
                blockers.append("PAPER_CANARY_SYMBOL_NOT_ALLOWED")
            open_positions = candidate["risk_budget"]["current_open_positions"]
            if open_positions > contract["paper_canary"]["max_open_positions"]:
                blockers.append("PAPER_CANARY_POSITION_LIMIT_EXCEEDED")
            if candidate["position"]["status"] == "FLAT" and open_positions >= contract["paper_canary"]["max_open_positions"]:
                blockers.append("PAPER_CANARY_POSITION_LIMIT_REACHED")

        calculated = _calculate(candidate, sorted(set(blockers)), evaluated_at)
        authority_ready = _authoritative_shadow_ready(candidate, contract)
        final_action = calculated["diagnostic_action"] if authority_ready else ACTION_NO_TRADE
        reasons = list(calculated["reason_codes"])
        if calculated["diagnostic_action"] != ACTION_NO_TRADE and not authority_ready:
            reasons.append("SHADOW_POLICY_OR_ELIGIBILITY_AUTHORITY_UNRATIFIED")
        row = {
            "decision_key": key,
            "candidate_id": candidate["candidate_id"],
            "symbol": candidate["symbol"],
            "briefing_rank": candidate["briefing_rank"],
            "action": final_action,
            "diagnostic_action": calculated["diagnostic_action"],
            "action_stage": calculated["action_stage"] if final_action != ACTION_NO_TRADE else None,
            "diagnostic": {
                "action": calculated["diagnostic_action"],
                "action_stage": calculated["action_stage"],
                "planned_entry_price_krw": calculated["planned_entry_price"],
                "planned_loss_per_share_krw": calculated["planned_loss_per_share_krw"],
                "planned_loss_krw": calculated["planned_loss_krw"],
                "quantity": calculated["quantity"],
                "quantity_basis": "FLOOR_PREALLOCATED_RISK_BUDGET_BY_FEE_SLIPPAGE_ADJUSTED_STOP_LOSS_THEN_CAP_BY_ACCOUNT_AND_LIQUIDITY",
                "cost_breakdown": calculated["cost_breakdown"],
            },
            "reason_codes": sorted(set(reasons)),
            "entry": {
                "condition": "CURRENT_LAST_ABOVE_PREVIOUS_COMPLETED_15M_HIGH_AND_ASK_AT_OR_BELOW_MAX_ENTRY",
                "price_basis": "CURRENT_ASK_FOR_SHADOW_PLANNED_LOSS_ONLY",
                "reference_price_krw": candidate["trade_plan"]["entry_reference_price"],
                "max_entry_price_krw": candidate["trade_plan"]["max_entry_price"],
                "planned_entry_price_krw": calculated["planned_entry_price"] if authority_ready else None,
            },
            "risk_plan": {
                "stop_price_krw": candidate["trade_plan"]["stop_price"],
                "take_profit_1_price_krw": candidate["trade_plan"]["take_profit_1_price"],
                "final_take_profit_price_krw": candidate["trade_plan"]["final_take_profit_price"],
                "expires_at": candidate["trade_plan"]["expires_at"],
                "invalidation_triggered": candidate["trade_plan"]["invalidation_triggered"],
                "planned_loss_per_share_krw": calculated["planned_loss_per_share_krw"] if authority_ready else None,
                "planned_loss_krw": calculated["planned_loss_krw"] if authority_ready else None,
                "quantity": calculated["quantity"] if authority_ready else None,
                "quantity_basis": "FLOOR_PREALLOCATED_RISK_BUDGET_BY_FEE_SLIPPAGE_ADJUSTED_STOP_LOSS_THEN_CAP_BY_ACCOUNT_AND_LIQUIDITY",
                "cost_breakdown": calculated["cost_breakdown"] if authority_ready else None,
            },
            "authority": copy.deepcopy(AUTHORITY_BOUNDARY),
            "order": copy.deepcopy(ORDER_BOUNDARY),
        }
        row["briefing_sentence"] = _briefing_sentence(row)
        decisions.append(row)

    summary = {f"{action.lower()}_count": sum(row["action"] == action for row in decisions) for action in ACTIONS}
    summary.update({
        "candidate_count": len(decisions),
        "diagnostic_non_no_trade_count": sum(row["diagnostic_action"] != ACTION_NO_TRADE for row in decisions),
        "order_draft_count": 0,
        "submission_authority_count": 0,
    })
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "decision_batch_id": input_packet["decision_batch_id"],
        "evaluated_at": input_packet["evaluated_at"],
        "business_date": input_packet["business_date"],
        "mode": input_packet["mode"],
        "decisions": decisions,
        "summary": summary,
        "authority": copy.deepcopy(AUTHORITY_BOUNDARY),
        "order": copy.deepcopy(ORDER_BOUNDARY),
        "source": copy.deepcopy(source),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_packet(input_packet: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    if contract != _expected_contract():
        raise KrxShadowStrategyError("CONTRACT_DRIFT_OR_AUTHORITY_ESCALATION")
    _validate_input(input_packet, contract)
    return _build_from_source({"contract": copy.deepcopy(contract), "input": copy.deepcopy(input_packet)})


def validate_packet(packet: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "decision_batch_id", "evaluated_at",
        "business_date", "mode", "decisions", "summary", "authority", "order",
        "source", "packet_sha256",
    }
    _exact(packet, fields, "OUTPUT_FIELDS_INVALID")
    if packet["schema_version"] != OUTPUT_SCHEMA_VERSION or packet["contract_version"] != CONTRACT_VERSION:
        raise KrxShadowStrategyError("OUTPUT_IDENTITY_INVALID")
    if packet["authority"] != AUTHORITY_BOUNDARY or packet["order"] != ORDER_BOUNDARY:
        raise KrxShadowStrategyError("OUTPUT_AUTHORITY_ESCALATION")
    claimed = _sha(packet["packet_sha256"], "OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise KrxShadowStrategyError("OUTPUT_SHA256_MISMATCH")
    rebuilt = _build_from_source(packet["source"])
    if rebuilt != packet:
        raise KrxShadowStrategyError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)
