#!/usr/bin/env python3
"""P10-11 deterministic Crypto PAPER order simulator and hash-chain ledger.

This module is deliberately offline.  It accepts caller-supplied, hash-bound
intent and public-orderbook packets and performs arithmetic simulation only.
It contains no network, credential, exchange-order, or investment-eligibility
path.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
import hashlib
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "crypto_paper_simulator_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,20}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class CryptoPaperSimulatorError(ValueError):
    """Fail-closed P10-11 contract or replay violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperSimulatorError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_paper_simulator/1",
        "ledger_schema_version": "crypto_paper_ledger/1",
        "event_schema_version": "crypto_paper_ledger_event/1",
        "intent_schema_version": "crypto_paper_order_intent/1",
        "snapshot_schema_version": "crypto_paper_orderbook_snapshot/1",
        "account_state_schema_version": "crypto_paper_account_state/1",
        "mode": "PAPER_LAB_ONLY",
        "currency": "KRW",
        "market_prefix": "KRW-",
        "supported_sides": ["BUY", "SELL"],
        "supported_order_types": ["LIMIT", "MARKET"],
        "market_regime_statuses": ["PASS", "FAIL", "UNKNOWN", "NOT_EVALUATED"],
        "lab_scope": "MECHANISM_ONLY_NO_INVESTMENT_AUTHORITY",
        "event_types": [
            "ACCOUNT_OPENED", "ORDER_SUBMITTED", "MATCH_EVALUATED_NO_FILL",
            "FILL_APPLIED", "ORDER_CANCELLED", "ORDER_EXPIRED",
        ],
        "order_states": ["OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED"],
        "decimal_scale": 18,
        "cost_model": "CALLER_SUPPLIED_FEE_RATE_AND_QUEUE_FRACTION_NO_DEFAULTS",
        "slippage_model": "REALIZED_VWAP_VERSUS_SNAPSHOT_BEST_PRICE",
        "ledger_mode": "IMMUTABLE_HASH_CHAIN_WITH_CONTENT_ADDRESSED_SNAPSHOTS",
        "market_judgment_policy": "PRESERVE_INPUT_STATUS_NO_PROMOTION",
        "account_mark_freshness_required": "FRESH",
        "intent_authority": {
            "paper_simulation_requested": True,
            "investment_eligibility_authorized": False,
            "exchange_order_authorized": False,
            "withdrawal_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "snapshot_authority": {
            "public_market_evidence_only": True,
            "investment_eligibility_authorized": False,
            "exchange_order_authorized": False,
            "withdrawal_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "paper_simulation_only": True,
            "network_access_authorized": False,
            "credential_access_authorized": False,
            "investment_eligibility_authorized": False,
            "action_authorized": False,
            "exchange_order_authorized": False,
            "broker_submission_authorized": False,
            "withdrawal_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CryptoPaperSimulatorError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CryptoPaperSimulatorError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CryptoPaperSimulatorError(code)
    return value


def _identifier(value, code: str) -> str:
    value = _text(value, code)
    if ID_RE.fullmatch(value) is None:
        raise CryptoPaperSimulatorError(code)
    return value


def _market(value, code: str = "MARKET_INVALID") -> str:
    if not isinstance(value, str) or MARKET_RE.fullmatch(value) is None:
        raise CryptoPaperSimulatorError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CryptoPaperSimulatorError(code)
    return value


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CryptoPaperSimulatorError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoPaperSimulatorError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CryptoPaperSimulatorError(code)
    return parsed


def _format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise CryptoPaperSimulatorError("DECIMAL_NON_FINITE")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _decimal(
    value, code: str, *, positive: bool = False, maximum: Decimal | None = None,
    contract: dict | None = None,
) -> Decimal:
    if not isinstance(value, str):
        raise CryptoPaperSimulatorError(code)
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CryptoPaperSimulatorError(code) from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise CryptoPaperSimulatorError(code)
    if value != _format_decimal(parsed):
        raise CryptoPaperSimulatorError(f"{code}:NON_CANONICAL")
    scale = _expected_contract()["decimal_scale"] if contract is None else contract["decimal_scale"]
    if max(0, -parsed.as_tuple().exponent) > scale:
        raise CryptoPaperSimulatorError(f"{code}:SCALE_EXCEEDED")
    if maximum is not None and parsed > maximum:
        raise CryptoPaperSimulatorError(code)
    return parsed


def _floor(value: Decimal, scale: int) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + scale + 10)
        return value.quantize(quantum, rounding=ROUND_DOWN)


def _with_packet_sha(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["packet_sha256"] = payload_sha256(result)
    return result


def build_intent(
    *, order_id: str, idempotency_key: str, market: str, side: str,
    order_type: str, quantity: str, limit_price: str | None, fee_rate: str,
    queue_fraction: str, submitted_at: str, expires_at: str,
    market_regime_status: str, source_plan_ref: str, source_plan_sha256: str,
    source_evidence_ref: str, source_evidence_sha256: str,
    contract: dict | None = None,
) -> dict:
    """Build and independently validate one non-executable PAPER intent."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    value = {
        "schema_version": contract["intent_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "lab_scope": contract["lab_scope"],
        "order_id": order_id,
        "idempotency_key": idempotency_key,
        "market": market,
        "side": side,
        "order_type": order_type,
        "quantity": quantity,
        "limit_price": limit_price,
        "fee_rate": fee_rate,
        "queue_fraction": queue_fraction,
        "submitted_at": submitted_at,
        "expires_at": expires_at,
        "market_regime_status": market_regime_status,
        "source_plan_ref": source_plan_ref,
        "source_plan_sha256": source_plan_sha256,
        "source_evidence_ref": source_evidence_ref,
        "source_evidence_sha256": source_evidence_sha256,
        "authority": copy.deepcopy(contract["intent_authority"]),
    }
    return validate_intent(_with_packet_sha(value), contract)


def validate_intent(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "mode", "lab_scope", "order_id",
        "idempotency_key", "market", "side", "order_type", "quantity", "limit_price",
        "fee_rate", "queue_fraction", "submitted_at", "expires_at",
        "market_regime_status", "source_plan_ref", "source_plan_sha256",
        "source_evidence_ref", "source_evidence_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperSimulatorError("INTENT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["intent_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("mode") != contract["mode"]
        or value.get("lab_scope") != contract["lab_scope"]
        or value.get("authority") != contract["intent_authority"]
    ):
        raise CryptoPaperSimulatorError("INTENT_IDENTITY_INVALID")
    side = value.get("side")
    order_type = value.get("order_type")
    if side not in contract["supported_sides"]:
        raise CryptoPaperSimulatorError("INTENT_SIDE_INVALID")
    if order_type not in contract["supported_order_types"]:
        raise CryptoPaperSimulatorError("INTENT_ORDER_TYPE_INVALID")
    quantity = _decimal(value.get("quantity"), "INTENT_QUANTITY_INVALID", positive=True, contract=contract)
    fee_rate = _decimal(value.get("fee_rate"), "INTENT_FEE_RATE_INVALID", maximum=Decimal("1"), contract=contract)
    if fee_rate == Decimal("1"):
        raise CryptoPaperSimulatorError("INTENT_FEE_RATE_INVALID")
    queue_fraction = _decimal(
        value.get("queue_fraction"), "INTENT_QUEUE_FRACTION_INVALID",
        positive=True, maximum=Decimal("1"), contract=contract,
    )
    limit_price = value.get("limit_price")
    if order_type == "LIMIT":
        _decimal(limit_price, "INTENT_LIMIT_PRICE_INVALID", positive=True, contract=contract)
    elif limit_price is not None:
        raise CryptoPaperSimulatorError("MARKET_INTENT_LIMIT_PRICE_MUST_BE_NULL")
    submitted = _utc(value.get("submitted_at"), "INTENT_SUBMITTED_AT_INVALID")
    expires = _utc(value.get("expires_at"), "INTENT_EXPIRES_AT_INVALID")
    if expires <= submitted:
        raise CryptoPaperSimulatorError("INTENT_EXPIRY_NOT_AFTER_SUBMISSION")
    if value.get("market_regime_status") not in contract["market_regime_statuses"]:
        raise CryptoPaperSimulatorError("MARKET_REGIME_STATUS_INVALID")
    normalized = copy.deepcopy(value)
    normalized.update({
        "order_id": _identifier(value.get("order_id"), "ORDER_ID_INVALID"),
        "idempotency_key": _identifier(value.get("idempotency_key"), "IDEMPOTENCY_KEY_INVALID"),
        "market": _market(value.get("market")),
        "quantity": _format_decimal(quantity),
        "fee_rate": _format_decimal(fee_rate),
        "queue_fraction": _format_decimal(queue_fraction),
        "source_plan_ref": _text(value.get("source_plan_ref"), "SOURCE_PLAN_REF_INVALID"),
        "source_plan_sha256": _sha(value.get("source_plan_sha256"), "SOURCE_PLAN_SHA_INVALID"),
        "source_evidence_ref": _text(value.get("source_evidence_ref"), "SOURCE_EVIDENCE_REF_INVALID"),
        "source_evidence_sha256": _sha(value.get("source_evidence_sha256"), "SOURCE_EVIDENCE_SHA_INVALID"),
    })
    if limit_price is not None:
        normalized["limit_price"] = _format_decimal(Decimal(limit_price))
    digest = _sha(value.get("packet_sha256"), "INTENT_PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(normalized)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperSimulatorError("INTENT_PACKET_SHA_MISMATCH")
    normalized["packet_sha256"] = digest
    return normalized


def build_snapshot(
    *, snapshot_id: str, market: str, captured_at: str, freshness_status: str,
    ask_levels: list[dict], bid_levels: list[dict], source_ref: str,
    source_sha256: str, contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    value = {
        "schema_version": contract["snapshot_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "snapshot_id": snapshot_id,
        "market": market,
        "captured_at": captured_at,
        "freshness_status": freshness_status,
        "ask_levels": _validate_levels(ask_levels, "ASK", contract),
        "bid_levels": _validate_levels(bid_levels, "BID", contract),
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "authority": copy.deepcopy(contract["snapshot_authority"]),
    }
    return validate_snapshot(_with_packet_sha(value), contract)


def _validate_levels(rows, side: str, contract: dict) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise CryptoPaperSimulatorError(f"SNAPSHOT_{side}_LEVELS_EMPTY")
    normalized = []
    seen_prices = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"price", "quantity"}:
            raise CryptoPaperSimulatorError(f"SNAPSHOT_{side}_LEVEL_FIELDS_INVALID:{index}")
        price = _decimal(row.get("price"), f"SNAPSHOT_{side}_PRICE_INVALID:{index}", positive=True, contract=contract)
        quantity = _decimal(row.get("quantity"), f"SNAPSHOT_{side}_QUANTITY_INVALID:{index}", positive=True, contract=contract)
        price_text = _format_decimal(price)
        if price_text in seen_prices:
            raise CryptoPaperSimulatorError(f"SNAPSHOT_{side}_PRICE_DUPLICATE:{price_text}")
        seen_prices.add(price_text)
        normalized.append({"price": price_text, "quantity": _format_decimal(quantity)})
    normalized.sort(key=lambda row: Decimal(row["price"]), reverse=side == "BID")
    return normalized


def validate_snapshot(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "mode", "snapshot_id", "market",
        "captured_at", "freshness_status", "ask_levels", "bid_levels", "source_ref",
        "source_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperSimulatorError("SNAPSHOT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["snapshot_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("mode") != contract["mode"]
        or value.get("authority") != contract["snapshot_authority"]
    ):
        raise CryptoPaperSimulatorError("SNAPSHOT_IDENTITY_INVALID")
    freshness = value.get("freshness_status")
    if freshness not in {"FRESH", "STALE", "UNKNOWN"}:
        raise CryptoPaperSimulatorError("SNAPSHOT_FRESHNESS_STATUS_INVALID")
    normalized = copy.deepcopy(value)
    normalized.update({
        "snapshot_id": _identifier(value.get("snapshot_id"), "SNAPSHOT_ID_INVALID"),
        "market": _market(value.get("market")),
        "ask_levels": _validate_levels(value.get("ask_levels"), "ASK", contract),
        "bid_levels": _validate_levels(value.get("bid_levels"), "BID", contract),
        "source_ref": _text(value.get("source_ref"), "SNAPSHOT_SOURCE_REF_INVALID"),
        "source_sha256": _sha(value.get("source_sha256"), "SNAPSHOT_SOURCE_SHA_INVALID"),
    })
    _utc(value.get("captured_at"), "SNAPSHOT_CAPTURED_AT_INVALID")
    digest = _sha(value.get("packet_sha256"), "SNAPSHOT_PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(normalized)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperSimulatorError("SNAPSHOT_PACKET_SHA_MISMATCH")
    normalized["packet_sha256"] = digest
    return normalized


def _blank_replay_state(contract: dict) -> dict:
    return {
        "opened": False,
        "cash": Decimal("0"),
        "positions": {},
        "orders": {},
        "idempotency": {},
        "matched_snapshots": set(),
        "last_event_at": None,
        "contract": contract,
    }


def _event_identity(event_type: str, event_at: str, order_id: str | None, payload: dict) -> dict:
    return {
        "event_type": event_type,
        "event_at": event_at,
        "order_id": order_id,
        "payload": copy.deepcopy(payload),
    }


def _event_unsigned(
    *, sequence: int, event_type: str, event_at: str, idempotency_key: str,
    order_id: str | None, previous_event_sha256: str | None, payload: dict,
    contract: dict,
) -> dict:
    return {
        "schema_version": contract["event_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "sequence": sequence,
        "event_type": event_type,
        "event_at": event_at,
        "idempotency_key": idempotency_key,
        "order_id": order_id,
        "previous_event_sha256": previous_event_sha256,
        "payload": copy.deepcopy(payload),
        "authority": copy.deepcopy(contract["authority"]),
    }


def _new_event(**kwargs) -> dict:
    value = _event_unsigned(**kwargs)
    value["event_sha256"] = payload_sha256(value)
    return value


def _ledger_unsigned(ledger_id: str, events: list[dict], contract: dict) -> dict:
    return {
        "schema_version": contract["ledger_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "ledger_id": ledger_id,
        "currency": contract["currency"],
        "events": copy.deepcopy(events),
        "authority": copy.deepcopy(contract["authority"]),
    }


def _ledger_packet(ledger_id: str, events: list[dict], contract: dict) -> dict:
    return _with_packet_sha(_ledger_unsigned(ledger_id, events, contract))


def create_ledger(
    *, ledger_id: str, initial_cash: str, opened_at: str,
    idempotency_key: str, contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    ledger_id = _identifier(ledger_id, "LEDGER_ID_INVALID")
    cash = _decimal(initial_cash, "INITIAL_CASH_INVALID", positive=True, contract=contract)
    event = _new_event(
        sequence=1,
        event_type="ACCOUNT_OPENED",
        event_at=opened_at,
        idempotency_key=_identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID"),
        order_id=None,
        previous_event_sha256=None,
        payload={"initial_cash": _format_decimal(cash), "currency": contract["currency"]},
        contract=contract,
    )
    _utc(opened_at, "OPENED_AT_INVALID")
    ledger = _ledger_packet(ledger_id, [event], contract)
    validate_ledger(ledger, contract)
    return ledger


def _validate_event_shape(event: dict, index: int, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "mode", "sequence", "event_type",
        "event_at", "idempotency_key", "order_id", "previous_event_sha256",
        "payload", "authority", "event_sha256",
    }
    if not isinstance(event, dict) or set(event) != fields:
        raise CryptoPaperSimulatorError(f"EVENT_FIELDS_MISMATCH:{index}")
    if (
        event.get("schema_version") != contract["event_schema_version"]
        or event.get("contract_version") != contract["contract_version"]
        or event.get("mode") != contract["mode"]
        or event.get("authority") != contract["authority"]
    ):
        raise CryptoPaperSimulatorError(f"EVENT_IDENTITY_INVALID:{index}")
    if event.get("sequence") != index + 1:
        raise CryptoPaperSimulatorError(f"EVENT_SEQUENCE_INVALID:{index}")
    if event.get("event_type") not in contract["event_types"]:
        raise CryptoPaperSimulatorError(f"EVENT_TYPE_INVALID:{index}")
    _utc(event.get("event_at"), f"EVENT_AT_INVALID:{index}")
    _identifier(event.get("idempotency_key"), f"EVENT_IDEMPOTENCY_KEY_INVALID:{index}")
    if event.get("order_id") is not None:
        _identifier(event.get("order_id"), f"EVENT_ORDER_ID_INVALID:{index}")
    previous = event.get("previous_event_sha256")
    if index == 0:
        if previous is not None:
            raise CryptoPaperSimulatorError("GENESIS_PREVIOUS_HASH_MUST_BE_NULL")
    else:
        _sha(previous, f"EVENT_PREVIOUS_SHA_INVALID:{index}")
    if not isinstance(event.get("payload"), dict):
        raise CryptoPaperSimulatorError(f"EVENT_PAYLOAD_INVALID:{index}")
    digest = _sha(event.get("event_sha256"), f"EVENT_SHA_INVALID:{index}")
    unsigned = copy.deepcopy(event)
    unsigned.pop("event_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperSimulatorError(f"EVENT_SHA_MISMATCH:{index}")
    return copy.deepcopy(event)


def _position(state: dict, market: str) -> dict:
    return state["positions"].setdefault(
        market,
        {"quantity": Decimal("0"), "cost_basis": Decimal("0"), "realized_pnl": Decimal("0")},
    )


def _match_derivation(state: dict, order: dict, snapshot: dict) -> dict:
    contract = state["contract"]
    intent = order["intent"]
    if snapshot["freshness_status"] != "FRESH":
        raise CryptoPaperSimulatorError("SNAPSHOT_NOT_FRESH")
    if snapshot["market"] != intent["market"]:
        raise CryptoPaperSimulatorError("SNAPSHOT_MARKET_MISMATCH")
    captured = _utc(snapshot["captured_at"], "SNAPSHOT_CAPTURED_AT_INVALID")
    submitted = _utc(intent["submitted_at"], "INTENT_SUBMITTED_AT_INVALID")
    expires = _utc(intent["expires_at"], "INTENT_EXPIRES_AT_INVALID")
    if captured < submitted:
        raise CryptoPaperSimulatorError("SNAPSHOT_PRECEDES_ORDER")
    if captured >= expires:
        raise CryptoPaperSimulatorError("SNAPSHOT_AT_OR_AFTER_EXPIRY")

    remaining = Decimal(intent["quantity"]) - order["filled_quantity"]
    if remaining <= 0:
        raise CryptoPaperSimulatorError("ORDER_ALREADY_FILLED")
    side = intent["side"]
    levels = snapshot["ask_levels"] if side == "BUY" else snapshot["bid_levels"]
    best_price = Decimal(levels[0]["price"])
    limit_price = Decimal(intent["limit_price"]) if intent["limit_price"] is not None else None
    fee_rate = Decimal(intent["fee_rate"])
    queue_fraction = Decimal(intent["queue_fraction"])
    available = state["cash"] if side == "BUY" else _position(state, intent["market"])["quantity"]
    executions = []
    filled = Decimal("0")
    gross = Decimal("0")
    for level in levels:
        price = Decimal(level["price"])
        if limit_price is not None:
            if side == "BUY" and price > limit_price:
                break
            if side == "SELL" and price < limit_price:
                break
        capacity = _floor(Decimal(level["quantity"]) * queue_fraction, contract["decimal_scale"])
        take = min(remaining - filled, capacity)
        if side == "BUY":
            cash_left = available - gross - (gross * fee_rate)
            if cash_left <= 0:
                break
            affordable = _floor(cash_left / (price * (Decimal("1") + fee_rate)), contract["decimal_scale"])
            take = min(take, affordable)
        else:
            take = min(take, available - filled)
        if take <= 0:
            continue
        executions.append({"price": _format_decimal(price), "quantity": _format_decimal(take)})
        filled += take
        gross += price * take
        if filled >= remaining:
            break

    if filled <= 0:
        return {
            "snapshot": copy.deepcopy(snapshot),
            "reason": "NO_EXECUTABLE_LEVELS_OR_CAPACITY",
            "remaining_quantity": _format_decimal(remaining),
        }
    gross = _floor(gross, contract["decimal_scale"])
    fee = _floor(gross * fee_rate, contract["decimal_scale"])
    cash_delta = -(gross + fee) if side == "BUY" else gross - fee
    vwap = _floor(gross / filled, contract["decimal_scale"])
    slippage = _floor((
        (vwap - best_price) / best_price * Decimal("10000")
        if side == "BUY"
        else (best_price - vwap) / best_price * Decimal("10000")
    ), contract["decimal_scale"])
    return {
        "snapshot": copy.deepcopy(snapshot),
        "executions": executions,
        "filled_quantity": _format_decimal(filled),
        "vwap": _format_decimal(vwap),
        "gross_value": _format_decimal(gross),
        "fee_amount": _format_decimal(fee),
        "cash_delta": _format_decimal(cash_delta),
        "realized_slippage_bps": _format_decimal(slippage),
        "remaining_quantity": _format_decimal(remaining - filled),
    }


def _apply_fill(state: dict, order: dict, payload: dict) -> None:
    intent = order["intent"]
    quantity = Decimal(payload["filled_quantity"])
    gross = Decimal(payload["gross_value"])
    fee = Decimal(payload["fee_amount"])
    cash_delta = Decimal(payload["cash_delta"])
    position = _position(state, intent["market"])
    state["cash"] += cash_delta
    if state["cash"] < 0:
        raise CryptoPaperSimulatorError("CASH_NEGATIVE_AFTER_FILL")
    if intent["side"] == "BUY":
        position["quantity"] += quantity
        position["cost_basis"] += gross + fee
    else:
        if quantity > position["quantity"]:
            raise CryptoPaperSimulatorError("SELL_EXCEEDS_POSITION")
        basis_removed = _floor((
            position["cost_basis"] * quantity / position["quantity"]
            if position["quantity"] > 0 else Decimal("0")
        ), state["contract"]["decimal_scale"])
        position["quantity"] -= quantity
        position["cost_basis"] -= basis_removed
        position["realized_pnl"] += gross - fee - basis_removed
        if position["quantity"] == 0:
            position["cost_basis"] = Decimal("0")
    order["filled_quantity"] += quantity
    order["gross_value"] += gross
    order["fees"] += fee
    if order["filled_quantity"] == Decimal(intent["quantity"]):
        order["status"] = "FILLED"
    else:
        order["status"] = "PARTIALLY_FILLED"


def _replay(events: list[dict], contract: dict) -> dict:
    state = _blank_replay_state(contract)
    prior_hash = None
    prior_time = None
    for index, raw in enumerate(events):
        event = _validate_event_shape(raw, index, contract)
        if event["previous_event_sha256"] != prior_hash:
            raise CryptoPaperSimulatorError(f"EVENT_CHAIN_BROKEN:{index}")
        event_time = _utc(event["event_at"], f"EVENT_AT_INVALID:{index}")
        if prior_time is not None and event_time < prior_time:
            raise CryptoPaperSimulatorError(f"EVENT_TIME_REGRESSION:{index}")
        key = event["idempotency_key"]
        identity = _event_identity(event["event_type"], event["event_at"], event["order_id"], event["payload"])
        if key in state["idempotency"]:
            raise CryptoPaperSimulatorError(f"LEDGER_IDEMPOTENCY_KEY_DUPLICATE:{key}")
        state["idempotency"][key] = identity
        event_type = event["event_type"]
        order_id = event["order_id"]
        payload = event["payload"]

        if index == 0:
            if event_type != "ACCOUNT_OPENED" or order_id is not None or set(payload) != {"initial_cash", "currency"}:
                raise CryptoPaperSimulatorError("GENESIS_EVENT_INVALID")
            if payload.get("currency") != contract["currency"]:
                raise CryptoPaperSimulatorError("GENESIS_CURRENCY_INVALID")
            state["cash"] = _decimal(payload.get("initial_cash"), "INITIAL_CASH_INVALID", positive=True, contract=contract)
            state["opened"] = True
        elif event_type == "ACCOUNT_OPENED":
            raise CryptoPaperSimulatorError("ACCOUNT_OPENED_NOT_GENESIS")
        elif event_type == "ORDER_SUBMITTED":
            if order_id is None or set(payload) != {"intent"}:
                raise CryptoPaperSimulatorError("ORDER_SUBMITTED_PAYLOAD_INVALID")
            intent = validate_intent(payload.get("intent"), contract)
            if order_id != intent["order_id"]:
                raise CryptoPaperSimulatorError("EVENT_ORDER_INTENT_ID_MISMATCH")
            if order_id in state["orders"]:
                raise CryptoPaperSimulatorError(f"ORDER_ID_DUPLICATE:{order_id}")
            if event["event_at"] != intent["submitted_at"]:
                raise CryptoPaperSimulatorError("ORDER_EVENT_TIME_MISMATCH")
            state["orders"][order_id] = {
                "intent": intent,
                "status": "OPEN",
                "filled_quantity": Decimal("0"),
                "gross_value": Decimal("0"),
                "fees": Decimal("0"),
            }
        else:
            if order_id not in state["orders"]:
                raise CryptoPaperSimulatorError(f"ORDER_NOT_FOUND:{order_id}")
            order = state["orders"][order_id]
            if order["status"] not in {"OPEN", "PARTIALLY_FILLED"}:
                raise CryptoPaperSimulatorError(f"ORDER_TERMINAL:{order_id}:{order['status']}")
            if event_type in {"MATCH_EVALUATED_NO_FILL", "FILL_APPLIED"}:
                snapshot = validate_snapshot(payload.get("snapshot"), contract)
                snapshot_key = (order_id, snapshot["snapshot_id"])
                if snapshot_key in state["matched_snapshots"]:
                    raise CryptoPaperSimulatorError(f"SNAPSHOT_ALREADY_MATCHED:{order_id}:{snapshot['snapshot_id']}")
                if _utc(event["event_at"], "MATCH_EVENT_AT_INVALID") < _utc(snapshot["captured_at"], "SNAPSHOT_CAPTURED_AT_INVALID"):
                    raise CryptoPaperSimulatorError("MATCH_EVENT_PRECEDES_SNAPSHOT")
                expected = _match_derivation(state, order, snapshot)
                expected_type = "MATCH_EVALUATED_NO_FILL" if "reason" in expected else "FILL_APPLIED"
                if event_type != expected_type or payload != expected:
                    raise CryptoPaperSimulatorError(f"MATCH_DERIVATION_MISMATCH:{order_id}")
                state["matched_snapshots"].add(snapshot_key)
                if event_type == "FILL_APPLIED":
                    _apply_fill(state, order, payload)
            elif event_type == "ORDER_CANCELLED":
                if set(payload) != {"reason"}:
                    raise CryptoPaperSimulatorError("CANCEL_PAYLOAD_INVALID")
                _text(payload.get("reason"), "CANCEL_REASON_INVALID")
                order["status"] = "CANCELLED"
            elif event_type == "ORDER_EXPIRED":
                if payload != {"reason": "INTENT_EXPIRY_REACHED"}:
                    raise CryptoPaperSimulatorError("EXPIRY_PAYLOAD_INVALID")
                if event_time < _utc(order["intent"]["expires_at"], "INTENT_EXPIRES_AT_INVALID"):
                    raise CryptoPaperSimulatorError("ORDER_EXPIRED_TOO_EARLY")
                order["status"] = "EXPIRED"
            else:
                raise CryptoPaperSimulatorError(f"EVENT_TRANSITION_INVALID:{event_type}")
        prior_hash = event["event_sha256"]
        prior_time = event_time
        state["last_event_at"] = event["event_at"]
    if not state["opened"]:
        raise CryptoPaperSimulatorError("LEDGER_GENESIS_MISSING")
    return state


def validate_ledger(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "mode", "ledger_id", "currency",
        "events", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperSimulatorError("LEDGER_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["ledger_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("mode") != contract["mode"]
        or value.get("currency") != contract["currency"]
        or value.get("authority") != contract["authority"]
    ):
        raise CryptoPaperSimulatorError("LEDGER_IDENTITY_INVALID")
    ledger_id = _identifier(value.get("ledger_id"), "LEDGER_ID_INVALID")
    events = value.get("events")
    if not isinstance(events, list) or not events:
        raise CryptoPaperSimulatorError("LEDGER_EVENTS_EMPTY")
    _replay(events, contract)
    digest = _sha(value.get("packet_sha256"), "LEDGER_PACKET_SHA_INVALID")
    unsigned = _ledger_unsigned(ledger_id, events, contract)
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperSimulatorError("LEDGER_PACKET_SHA_MISMATCH")
    result = copy.deepcopy(unsigned)
    result["packet_sha256"] = digest
    return result


def _append_event(
    ledger: dict, *, event_type: str, event_at: str, idempotency_key: str,
    order_id: str | None, payload: dict, contract: dict,
) -> dict:
    checked = validate_ledger(ledger, contract)
    key = _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
    identity = _event_identity(event_type, event_at, order_id, payload)
    for existing in checked["events"]:
        if existing["idempotency_key"] == key:
            existing_identity = _event_identity(
                existing["event_type"], existing["event_at"], existing["order_id"], existing["payload"]
            )
            if existing_identity == identity:
                return checked
            raise CryptoPaperSimulatorError(f"IDEMPOTENCY_KEY_COLLISION:{key}")
    if _utc(event_at, "EVENT_AT_INVALID") < _utc(checked["events"][-1]["event_at"], "LAST_EVENT_AT_INVALID"):
        raise CryptoPaperSimulatorError("EVENT_TIME_REGRESSION")
    event = _new_event(
        sequence=len(checked["events"]) + 1,
        event_type=event_type,
        event_at=event_at,
        idempotency_key=key,
        order_id=order_id,
        previous_event_sha256=checked["events"][-1]["event_sha256"],
        payload=payload,
        contract=contract,
    )
    result = _ledger_packet(checked["ledger_id"], checked["events"] + [event], contract)
    validate_ledger(result, contract)
    return result


def submit_order(ledger: dict, intent: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked_intent = validate_intent(intent, contract)
    return _append_event(
        ledger,
        event_type="ORDER_SUBMITTED",
        event_at=checked_intent["submitted_at"],
        idempotency_key=checked_intent["idempotency_key"],
        order_id=checked_intent["order_id"],
        payload={"intent": checked_intent},
        contract=contract,
    )


def match_order(
    ledger: dict, *, order_id: str, snapshot: dict, event_at: str,
    idempotency_key: str, contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = validate_ledger(ledger, contract)
    order_id = _identifier(order_id, "ORDER_ID_INVALID")
    retry_key = _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
    checked_snapshot = validate_snapshot(snapshot, contract)
    for existing in checked["events"]:
        if existing["idempotency_key"] != retry_key:
            continue
        existing_snapshot = existing.get("payload", {}).get("snapshot")
        if (
            existing["event_type"] in {"MATCH_EVALUATED_NO_FILL", "FILL_APPLIED"}
            and existing["order_id"] == order_id
            and existing["event_at"] == event_at
            and isinstance(existing_snapshot, dict)
            and existing_snapshot.get("packet_sha256") == checked_snapshot["packet_sha256"]
        ):
            return checked
        raise CryptoPaperSimulatorError(f"IDEMPOTENCY_KEY_COLLISION:{retry_key}")
    state = _replay(checked["events"], contract)
    if order_id not in state["orders"]:
        raise CryptoPaperSimulatorError(f"ORDER_NOT_FOUND:{order_id}")
    order = state["orders"][order_id]
    snapshot_key = (order_id, checked_snapshot["snapshot_id"])
    if snapshot_key in state["matched_snapshots"]:
        raise CryptoPaperSimulatorError(f"SNAPSHOT_ALREADY_MATCHED:{order_id}:{checked_snapshot['snapshot_id']}")
    if order["status"] not in {"OPEN", "PARTIALLY_FILLED"}:
        raise CryptoPaperSimulatorError(f"ORDER_TERMINAL:{order_id}:{order['status']}")
    if _utc(event_at, "MATCH_EVENT_AT_INVALID") < _utc(checked_snapshot["captured_at"], "SNAPSHOT_CAPTURED_AT_INVALID"):
        raise CryptoPaperSimulatorError("MATCH_EVENT_PRECEDES_SNAPSHOT")
    payload = _match_derivation(state, order, checked_snapshot)
    event_type = "MATCH_EVALUATED_NO_FILL" if "reason" in payload else "FILL_APPLIED"
    return _append_event(
        checked, event_type=event_type, event_at=event_at,
        idempotency_key=retry_key, order_id=order_id, payload=payload, contract=contract,
    )


def cancel_order(
    ledger: dict, *, order_id: str, event_at: str, idempotency_key: str,
    reason: str, contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = validate_ledger(ledger, contract)
    order_id = _identifier(order_id, "ORDER_ID_INVALID")
    retry_key = _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
    payload = {"reason": _text(reason, "CANCEL_REASON_INVALID")}
    retry_identity = _event_identity("ORDER_CANCELLED", event_at, order_id, payload)
    for existing in checked["events"]:
        if existing["idempotency_key"] != retry_key:
            continue
        existing_identity = _event_identity(
            existing["event_type"], existing["event_at"], existing["order_id"], existing["payload"]
        )
        if existing_identity == retry_identity:
            return checked
        raise CryptoPaperSimulatorError(f"IDEMPOTENCY_KEY_COLLISION:{retry_key}")
    state = _replay(checked["events"], contract)
    if order_id not in state["orders"]:
        raise CryptoPaperSimulatorError(f"ORDER_NOT_FOUND:{order_id}")
    if state["orders"][order_id]["status"] not in {"OPEN", "PARTIALLY_FILLED"}:
        raise CryptoPaperSimulatorError(f"ORDER_TERMINAL:{order_id}:{state['orders'][order_id]['status']}")
    return _append_event(
        checked, event_type="ORDER_CANCELLED", event_at=event_at,
        idempotency_key=retry_key, order_id=order_id,
        payload=payload, contract=contract,
    )


def expire_order(
    ledger: dict, *, order_id: str, event_at: str, idempotency_key: str,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = validate_ledger(ledger, contract)
    order_id = _identifier(order_id, "ORDER_ID_INVALID")
    retry_key = _identifier(idempotency_key, "IDEMPOTENCY_KEY_INVALID")
    payload = {"reason": "INTENT_EXPIRY_REACHED"}
    retry_identity = _event_identity("ORDER_EXPIRED", event_at, order_id, payload)
    for existing in checked["events"]:
        if existing["idempotency_key"] != retry_key:
            continue
        existing_identity = _event_identity(
            existing["event_type"], existing["event_at"], existing["order_id"], existing["payload"]
        )
        if existing_identity == retry_identity:
            return checked
        raise CryptoPaperSimulatorError(f"IDEMPOTENCY_KEY_COLLISION:{retry_key}")
    state = _replay(checked["events"], contract)
    if order_id not in state["orders"]:
        raise CryptoPaperSimulatorError(f"ORDER_NOT_FOUND:{order_id}")
    order = state["orders"][order_id]
    if order["status"] not in {"OPEN", "PARTIALLY_FILLED"}:
        raise CryptoPaperSimulatorError(f"ORDER_TERMINAL:{order_id}:{order['status']}")
    if _utc(event_at, "EXPIRY_EVENT_AT_INVALID") < _utc(order["intent"]["expires_at"], "INTENT_EXPIRES_AT_INVALID"):
        raise CryptoPaperSimulatorError("ORDER_EXPIRED_TOO_EARLY")
    return _append_event(
        checked, event_type="ORDER_EXPIRED", event_at=event_at,
        idempotency_key=retry_key, order_id=order_id,
        payload=payload, contract=contract,
    )


def _assemble_account_state(
    checked: dict, *, observed_at: str, mark_prices: dict[str, str],
    mark_freshness_status: str, mark_source_ref: str, mark_source_sha256: str,
    contract: dict,
) -> dict:
    state = _replay(checked["events"], contract)
    observed = _utc(observed_at, "ACCOUNT_OBSERVED_AT_INVALID")
    if observed < _utc(state["last_event_at"], "LAST_EVENT_AT_INVALID"):
        raise CryptoPaperSimulatorError("ACCOUNT_VIEW_PRECEDES_LEDGER")
    if mark_freshness_status != contract["account_mark_freshness_required"]:
        raise CryptoPaperSimulatorError("ACCOUNT_MARK_NOT_FRESH")
    if not isinstance(mark_prices, dict):
        raise CryptoPaperSimulatorError("MARK_PRICES_INVALID")
    normalized_marks = {}
    positions = []
    position_value = Decimal("0")
    unrealized_total = Decimal("0")
    realized_total = Decimal("0")
    for market in sorted(state["positions"]):
        position = state["positions"][market]
        if position["quantity"] == 0:
            realized_total += position["realized_pnl"]
            continue
        if market not in mark_prices:
            raise CryptoPaperSimulatorError(f"MARK_PRICE_MISSING:{market}")
        mark = _decimal(mark_prices[market], f"MARK_PRICE_INVALID:{market}", positive=True, contract=contract)
        normalized_marks[market] = _format_decimal(mark)
        market_value = position["quantity"] * mark
        unrealized = market_value - position["cost_basis"]
        position_value += market_value
        unrealized_total += unrealized
        realized_total += position["realized_pnl"]
        positions.append({
            "market": market,
            "quantity": _format_decimal(position["quantity"]),
            "cost_basis": _format_decimal(position["cost_basis"]),
            "average_cost": _format_decimal(position["cost_basis"] / position["quantity"]),
            "mark_price": _format_decimal(mark),
            "market_value": _format_decimal(market_value),
            "unrealized_pnl": _format_decimal(unrealized),
            "realized_pnl": _format_decimal(position["realized_pnl"]),
        })
    orders = []
    for order_id in sorted(state["orders"]):
        order = state["orders"][order_id]
        quantity = Decimal(order["intent"]["quantity"])
        orders.append({
            "order_id": order_id,
            "market": order["intent"]["market"],
            "side": order["intent"]["side"],
            "order_type": order["intent"]["order_type"],
            "status": order["status"],
            "requested_quantity": _format_decimal(quantity),
            "filled_quantity": _format_decimal(order["filled_quantity"]),
            "remaining_quantity": _format_decimal(quantity - order["filled_quantity"]),
            "gross_value": _format_decimal(order["gross_value"]),
            "fees": _format_decimal(order["fees"]),
            "market_regime_status": order["intent"]["market_regime_status"],
        })
    packet = {
        "schema_version": contract["account_state_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "ledger_id": checked["ledger_id"],
        "observed_at": observed_at,
        "cash": _format_decimal(state["cash"]),
        "position_market_value": _format_decimal(position_value),
        "total_nav": _format_decimal(state["cash"] + position_value),
        "unrealized_pnl": _format_decimal(unrealized_total),
        "realized_pnl": _format_decimal(realized_total),
        "positions": positions,
        "orders": orders,
        "mark_prices": normalized_marks,
        "source_ledger": copy.deepcopy(checked),
        "source": {
            "ledger_sha256": checked["packet_sha256"],
            "mark_freshness_status": mark_freshness_status,
            "mark_source_ref": _text(mark_source_ref, "MARK_SOURCE_REF_INVALID"),
            "mark_source_sha256": _sha(mark_source_sha256, "MARK_SOURCE_SHA_INVALID"),
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    return _with_packet_sha(packet)


def build_account_state(
    ledger: dict, *, observed_at: str, mark_prices: dict[str, str],
    mark_freshness_status: str, mark_source_ref: str, mark_source_sha256: str,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = validate_ledger(ledger, contract)
    packet = _assemble_account_state(
        checked,
        observed_at=observed_at,
        mark_prices=mark_prices,
        mark_freshness_status=mark_freshness_status,
        mark_source_ref=mark_source_ref,
        mark_source_sha256=mark_source_sha256,
        contract=contract,
    )
    return validate_account_state(packet, contract)


def validate_account_state(value: dict, contract: dict | None = None) -> dict:
    """Rebuild a persisted account view from its exact embedded ledger."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "mode", "ledger_id", "observed_at",
        "cash", "position_market_value", "total_nav", "unrealized_pnl", "realized_pnl",
        "positions", "orders", "mark_prices", "source_ledger", "source", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperSimulatorError("ACCOUNT_STATE_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["account_state_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("mode") != contract["mode"]
        or value.get("authority") != contract["authority"]
    ):
        raise CryptoPaperSimulatorError("ACCOUNT_STATE_IDENTITY_INVALID")
    digest = _sha(value.get("packet_sha256"), "ACCOUNT_STATE_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperSimulatorError("ACCOUNT_STATE_SHA_MISMATCH")
    source = value.get("source")
    if not isinstance(source, dict) or set(source) != {
        "ledger_sha256", "mark_freshness_status", "mark_source_ref", "mark_source_sha256"
    }:
        raise CryptoPaperSimulatorError("ACCOUNT_STATE_SOURCE_FIELDS_MISMATCH")
    checked_ledger = validate_ledger(value.get("source_ledger"), contract)
    if source.get("ledger_sha256") != checked_ledger["packet_sha256"]:
        raise CryptoPaperSimulatorError("ACCOUNT_STATE_LEDGER_LINEAGE_MISMATCH")
    expected = _assemble_account_state(
        checked_ledger,
        observed_at=value.get("observed_at"),
        mark_prices=value.get("mark_prices"),
        mark_freshness_status=source.get("mark_freshness_status"),
        mark_source_ref=source.get("mark_source_ref"),
        mark_source_sha256=source.get("mark_source_sha256"),
        contract=contract,
    )
    if value != expected:
        raise CryptoPaperSimulatorError("ACCOUNT_STATE_DERIVATION_MISMATCH")
    return copy.deepcopy(value)


def publish_ledger_snapshot(
    root: Path, ledger: dict, contract: dict | None = None,
) -> tuple[Path, bool]:
    """Write an immutable content-addressed snapshot outside tracked state."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = validate_ledger(ledger, contract)
    root = Path(root)
    resolved_root = root.resolve(strict=False)
    resolved_repository = ROOT.resolve()
    if resolved_root == resolved_repository or resolved_repository in resolved_root.parents:
        raise CryptoPaperSimulatorError(f"TRACKED_LEDGER_OUTPUT_FORBIDDEN:{root}")
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise CryptoPaperSimulatorError("LEDGER_ROOT_SYMLINK_FORBIDDEN")
    ledger_dir = root / checked["ledger_id"]
    ledger_dir.mkdir(mode=0o700, exist_ok=True)
    if ledger_dir.is_symlink() or not ledger_dir.is_dir():
        raise CryptoPaperSimulatorError("LEDGER_DIRECTORY_INVALID")
    data = (canonical_json(checked) + "\n").encode("utf-8")
    target = ledger_dir / f"{len(checked['events']):08d}-{checked['packet_sha256']}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        try:
            existing = target.read_bytes()
        except OSError as exc:
            raise CryptoPaperSimulatorError(f"LEDGER_SNAPSHOT_READ_FAILED:{target}:{exc}") from exc
        if existing != data:
            raise CryptoPaperSimulatorError("LEDGER_SNAPSHOT_COLLISION")
        return target, False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            target.unlink()
        except OSError:
            pass
        raise CryptoPaperSimulatorError(f"LEDGER_SNAPSHOT_WRITE_FAILED:{target}:{exc}") from exc
    return target, True


def recover_ledger(
    root: Path, ledger_id: str, contract: dict | None = None,
) -> dict:
    """Validate every immutable snapshot and recover the single longest chain."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    ledger_id = _identifier(ledger_id, "LEDGER_ID_INVALID")
    ledger_dir = Path(root) / ledger_id
    if ledger_dir.is_symlink() or not ledger_dir.is_dir():
        raise CryptoPaperSimulatorError("LEDGER_DIRECTORY_INVALID")
    snapshots = []
    for path in sorted(ledger_dir.glob("*.json")):
        if path.is_symlink() or not path.is_file():
            raise CryptoPaperSimulatorError(f"LEDGER_SNAPSHOT_PATH_INVALID:{path.name}")
        checked = validate_ledger(_read_json(path), contract)
        if checked["ledger_id"] != ledger_id:
            raise CryptoPaperSimulatorError("LEDGER_ID_MISMATCH_DURING_RECOVERY")
        expected_name = f"{len(checked['events']):08d}-{checked['packet_sha256']}.json"
        if path.name != expected_name:
            raise CryptoPaperSimulatorError(f"LEDGER_SNAPSHOT_FILENAME_MISMATCH:{path.name}")
        snapshots.append(checked)
    if not snapshots:
        raise CryptoPaperSimulatorError("LEDGER_SNAPSHOTS_MISSING")
    snapshots.sort(key=lambda item: (len(item["events"]), item["packet_sha256"]))
    by_length = {}
    for snapshot in snapshots:
        length = len(snapshot["events"])
        if length in by_length and by_length[length]["packet_sha256"] != snapshot["packet_sha256"]:
            raise CryptoPaperSimulatorError(f"LEDGER_HISTORY_DIVERGED_AT_LENGTH:{length}")
        by_length[length] = snapshot
    ordered = [by_length[length] for length in sorted(by_length)]
    for prior, current in zip(ordered, ordered[1:]):
        if current["events"][: len(prior["events"])] != prior["events"]:
            raise CryptoPaperSimulatorError("LEDGER_HISTORY_DIVERGED")
    return copy.deepcopy(ordered[-1])
