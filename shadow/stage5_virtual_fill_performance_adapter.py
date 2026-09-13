#!/usr/bin/env python3
"""Re-derive a private Stage5 connector result for P7-19 intake.

The public repository may re-derive private facts in memory, but it must not
persist or disclose private balances, quantities, prices, costs, FX rates, or
positions.  This module therefore consumes a Stage5 envelope and lifecycle
receipt together with the exact ledger bytes, independently re-derives their
bindings and ledger transitions, and emits only a redacted readiness packet.

All trust pins currently enter through the same call boundary.  They are
therefore useful for deterministic consistency checking but are not an
independent trust anchor.  The output is explicitly untrusted and cannot claim
private-source authentication until a separately controlled handoff supplies
the exact envelope/receipt hashes and producer revision.

The P7-18 connector is transaction-state evidence, not a future outcome.  In
the absence of a separately authenticated P7-19 outcome/NAV history, every
performance value remains ``None`` and the sample contribution remains zero.
Synthetic fixtures exercise this exact path but can never become natural
performance evidence.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


SCHEMA_VERSION = "stage5_virtual_fill_performance_input/2"
LEDGER_SCHEMA = "p7_18_virtual_sleeve_fill_ledger_file/1"
ENTRY_SCHEMA = "p7_18_virtual_sleeve_fill_ledger_entry/1"
FILL_SCHEMA = "p7_18_virtual_fill_cost/1"
CONNECTOR_CONTRACT_VERSION = "stage5_internal_virtual_paper_connector/1"
CONNECTOR_ENVELOPE_SCHEMA = "stage5_internal_virtual_paper_envelope/1"
CONNECTOR_RECEIPT_SCHEMA = "stage5_internal_virtual_paper_lifecycle_receipt/1"
CONNECTOR_MODE = "INTERNAL_VIRTUAL_PAPER_NON_FIXTURE"
PRODUCER_REPOSITORY = "yonggeun1021-hub/atlas-private-evidence"
CONNECTOR_PATH = "private_evidence/stage5_internal_virtual_paper_connector.py"

MARKETS = ("KOREA_VIRTUAL", "US_VIRTUAL", "CRYPTO_VIRTUAL")
NATIVE_CURRENCY = {
    "KOREA_VIRTUAL": "KRW",
    "US_VIRTUAL": "USD",
    "CRYPTO_VIRTUAL": "KRW",
}
SIDES = ("BUY", "SELL")

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

SOURCE_PIN_FIELDS = ("repository", "path", "commit", "sha256")
REQUEST_FIELDS = (
    "evaluation_id", "evaluated_at_utc", "market", "side", "notional_native",
    "eligible_price", "cost_policy", "fx_rate",
)
ELIGIBLE_PRICE_FIELDS = (
    "schema_version", "market", "price_native", "price_currency",
    "decision_time", "candle_closed_at", "available_at", "fill_time",
    "source_pin", "payload_sha256",
)
FX_RATE_FIELDS = (
    "schema_version", "pair", "rate", "as_of", "staleness_status",
    "source_pin", "payload_sha256",
)
COST_POLICY_FIELDS_KOREA = (
    "schema_version", "market", "policy_id", "effective_from",
    "buy_fee_bps", "sell_fee_bps", "sell_tax_bps", "slippage_bps",
    "source_pin", "payload_sha256",
)
COST_POLICY_FIELDS_OTHER = (
    "schema_version", "market", "policy_id", "effective_from",
    "buy_fee_bps", "sell_fee_bps", "slippage_bps", "source_pin",
    "payload_sha256",
)
FILL_FIELDS = (
    "schema_version", "evaluation_id", "evaluated_at", "market", "side",
    "gross_notional_native", "fee_native", "tax_native", "slippage_native",
    "net_cash_native", "net_cash_krw", "native_currency", "fx_applied",
    "eligible_price", "cost_policy", "fx_rate", "authority", "payload_sha256",
)
POSITION_FIELDS = (
    "quantity_native", "native_currency", "cost_basis_krw", "realized_pnl_krw",
)
ENTRY_FIELDS = (
    "schema_version", "sequence", "identity_sha256", "previous_entry_sha256",
    "recorded_at", "market", "side", "fill_cost_payload_sha256",
    "fill_cost_result", "fill_cost_request", "max_price_age_seconds",
    "price_age_seconds", "cash_before_krw", "cash_after_krw",
    "position_before", "position_after", "realized_pnl_delta_krw",
    "authority", "entry_sha256",
)
CONNECTOR_ENVELOPE_FIELDS = (
    "schema_version", "contract_version", "mode", "envelope_id",
    "evidence_class", "decision", "allocation_decision",
    "fill_cost_request", "fill_cost_result", "ledger_request",
    "dependency_pins", "authority", "packet_sha256",
)
CONNECTOR_RECEIPT_FIELDS = (
    "schema_version", "contract_version", "mode", "envelope_id",
    "evidence_class", "attempted_at", "status", "execution_performed",
    "request_sha256", "fill_cost_payload_sha256", "result_code",
    "error_code", "before", "after", "deduplication", "recovery",
    "authority", "packet_sha256",
)
CONNECTOR_STATE_PIN_FIELDS = (
    "ledger_ref", "ledger_file_sha256", "snapshot_sha256",
    "head_entry_sha256", "entry_count",
)
EXPECTED_ENTRY_PIN_FIELDS = (
    "evaluation_id", "entry_sha256", "fill_cost_payload_sha256",
    "eligible_price_source_pin", "cost_policy_source_pin", "fx_source_pin",
)
EXPECTED_PIN_FIELDS = (
    "producer_repository", "producer_commit", "connector_path",
    "connector_module_sha256", "connector_contract_payload_sha256",
    "connector_envelope_sha256", "connector_lifecycle_receipt_sha256",
    "expected_decision_packet_sha256", "expected_decision_source_sha256",
    "ledger_raw_bytes_sha256", "ledger_content_payload_sha256",
    "genesis_snapshot_payload_sha256", "initial_cash_krw", "entries",
)

FILL_AUTHORITY = {
    "internal_virtual_fill_cost_computation": True,
    "ledger_write": False,
    "position_creation": False,
    "rebalance_instruction_authorized": False,
    "order_creation_or_submission": False,
    "real_account": False,
    "live_trading": False,
    "capital_transfer": False,
    "provider_or_network": False,
    "broker_or_exchange": False,
    "hedge_allocation_or_execution": False,
}
ENTRY_AUTHORITY = {
    "internal_virtual_fill_ledger_write": True,
    "position_mutation": True,
    "cash_mutation": True,
    "order_creation_or_submission": False,
    "broker_or_exchange": False,
    "real_account": False,
    "live_trading": False,
    "capital_transfer": False,
    "provider_or_network": False,
    "credential": False,
    "hedge_allocation_or_execution": False,
}
ALLOCATION_AUTHORITY = {
    "internal_decision_ledger_write": True,
    "non_null_target_recording": True,
    "rebalance_instruction_authorized": False,
    "execution_required_determination_authorized": False,
    "order_creation_or_submission": False,
    "position_creation": False,
    "real_account": False,
    "live_trading": False,
    "capital_transfer": False,
    "provider_or_network": False,
    "broker_or_exchange": False,
    "hedge_allocation_or_execution": False,
}
AUTHORITY = {
    "caller_pinned_rederivation": True,
    "private_source_authentication": False,
    "connector_lifecycle_authentication": False,
    "p7_19_input_readiness_projection": True,
    "performance_evaluation": False,
    "performance_claim": False,
    "sample_counting": False,
    "strategy_change": False,
    "paper_or_real_activation": False,
    "order": False,
    "capital": False,
    "production": False,
    "trading": False,
}


class Stage5VirtualFillPerformanceError(ValueError):
    """Fail-closed connector, lineage, arithmetic, or privacy violation."""


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise Stage5VirtualFillPerformanceError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _self_sha(value: dict, field: str) -> str:
    unsigned = copy.deepcopy(value)
    unsigned.pop(field, None)
    return payload_sha256(unsigned)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Stage5VirtualFillPerformanceError(f"JSON_DUPLICATE_KEY:{key}")
        value[key] = item
    return value


def _parse_json_bytes(raw: object) -> dict:
    if not isinstance(raw, bytes):
        raise Stage5VirtualFillPerformanceError("LEDGER_BYTES_REQUIRED")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                Stage5VirtualFillPerformanceError(f"JSON_CONSTANT_INVALID:{item}")
            ),
        )
    except Stage5VirtualFillPerformanceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage5VirtualFillPerformanceError("LEDGER_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise Stage5VirtualFillPerformanceError("LEDGER_JSON_INVALID")
    return value


def _exact_fields(value: object, fields: tuple[str, ...], code: str) -> dict:
    if not isinstance(value, dict) or tuple(value) != fields:
        raise Stage5VirtualFillPerformanceError(code)
    return value


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Stage5VirtualFillPerformanceError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise Stage5VirtualFillPerformanceError(code)
    return value


def _git_sha(value: object, code: str) -> str:
    if not isinstance(value, str) or GIT_SHA_RE.fullmatch(value) is None:
        raise Stage5VirtualFillPerformanceError(code)
    return value


def _utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise Stage5VirtualFillPerformanceError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise Stage5VirtualFillPerformanceError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise Stage5VirtualFillPerformanceError(code)
    return parsed


def _decimal(value: object, code: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str) or not value:
        raise Stage5VirtualFillPerformanceError(code)
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise Stage5VirtualFillPerformanceError(code) from exc
    if not number.is_finite() or (positive and number <= 0):
        raise Stage5VirtualFillPerformanceError(code)
    return number


def _krw_text(value: Decimal) -> str:
    if value != value.to_integral_value():
        raise Stage5VirtualFillPerformanceError("KRW_AMOUNT_NOT_INTEGRAL")
    return format(value.to_integral_value(), "f")


def _source_pin(value: object, code: str) -> dict:
    _exact_fields(value, SOURCE_PIN_FIELDS, f"{code}_FIELDS_INVALID")
    _text(value["repository"], f"{code}_REPOSITORY_INVALID")
    _text(value["path"], f"{code}_PATH_INVALID")
    _git_sha(value["commit"], f"{code}_COMMIT_INVALID")
    _sha(value["sha256"], f"{code}_SHA_INVALID")
    return copy.deepcopy(value)


def _validate_self_hash(value: dict, field: str, code: str) -> None:
    digest = _sha(value.get(field), f"{code}_INVALID")
    if _self_sha(value, field) != digest:
        raise Stage5VirtualFillPerformanceError(f"{code}_MISMATCH")


def _expected_pins(value: object) -> dict:
    _exact_fields(value, EXPECTED_PIN_FIELDS, "EXPECTED_PINS_FIELDS_INVALID")
    if value["producer_repository"] != PRODUCER_REPOSITORY:
        raise Stage5VirtualFillPerformanceError("PRODUCER_REPOSITORY_INVALID")
    _git_sha(value["producer_commit"], "PRODUCER_COMMIT_INVALID")
    if value["connector_path"] != CONNECTOR_PATH:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_PATH_INVALID")
    for key in (
        "connector_module_sha256", "connector_contract_payload_sha256",
        "connector_envelope_sha256", "connector_lifecycle_receipt_sha256",
        "expected_decision_packet_sha256", "expected_decision_source_sha256",
        "ledger_raw_bytes_sha256", "ledger_content_payload_sha256",
        "genesis_snapshot_payload_sha256",
    ):
        _sha(value[key], f"EXPECTED_{key.upper()}_INVALID")
    _decimal(value["initial_cash_krw"], "EXPECTED_INITIAL_CASH_INVALID", positive=True)
    entries = value["entries"]
    if not isinstance(entries, list):
        raise Stage5VirtualFillPerformanceError("EXPECTED_ENTRY_PINS_INVALID")
    seen = set()
    for index, row in enumerate(entries):
        _exact_fields(row, EXPECTED_ENTRY_PIN_FIELDS, f"EXPECTED_ENTRY_PIN_FIELDS_INVALID:{index}")
        evaluation_id = _text(row["evaluation_id"], f"EXPECTED_EVALUATION_ID_INVALID:{index}")
        if evaluation_id in seen:
            raise Stage5VirtualFillPerformanceError("EXPECTED_EVALUATION_ID_DUPLICATE")
        seen.add(evaluation_id)
        for key in ("entry_sha256", "fill_cost_payload_sha256"):
            _sha(row[key], f"EXPECTED_{key.upper()}_INVALID:{index}")
        _source_pin(row["eligible_price_source_pin"], f"EXPECTED_PRICE_SOURCE:{index}")
        _source_pin(row["cost_policy_source_pin"], f"EXPECTED_COST_SOURCE:{index}")
        if row["fx_source_pin"] is not None:
            _source_pin(row["fx_source_pin"], f"EXPECTED_FX_SOURCE:{index}")
    return copy.deepcopy(value)


def _validate_price(value: object, *, market: str, expected_pin: dict) -> dict:
    _exact_fields(value, ELIGIBLE_PRICE_FIELDS, "ELIGIBLE_PRICE_FIELDS_INVALID")
    if value["schema_version"] != "p7_18_eligible_price_attestation/1" or value["market"] != market:
        raise Stage5VirtualFillPerformanceError("ELIGIBLE_PRICE_IDENTITY_INVALID")
    _decimal(value["price_native"], "ELIGIBLE_PRICE_INVALID", positive=True)
    if value["price_currency"] != NATIVE_CURRENCY[market]:
        raise Stage5VirtualFillPerformanceError("ELIGIBLE_PRICE_CURRENCY_INVALID")
    decision = _utc(value["decision_time"], "PRICE_DECISION_TIME_INVALID")
    closed = _utc(value["candle_closed_at"], "PRICE_CANDLE_TIME_INVALID")
    available = _utc(value["available_at"], "PRICE_AVAILABLE_TIME_INVALID")
    fill = _utc(value["fill_time"], "PRICE_FILL_TIME_INVALID")
    if not decision <= closed < available <= fill:
        raise Stage5VirtualFillPerformanceError("ELIGIBLE_PRICE_PIT_ORDER_INVALID")
    pin = _source_pin(value["source_pin"], "ELIGIBLE_PRICE_SOURCE")
    if pin != expected_pin:
        raise Stage5VirtualFillPerformanceError("ELIGIBLE_PRICE_SOURCE_EXPECTED_PIN_MISMATCH")
    _validate_self_hash(value, "payload_sha256", "ELIGIBLE_PRICE_SHA")
    return copy.deepcopy(value)


def _validate_cost(value: object, *, market: str, expected_pin: dict) -> dict:
    fields = COST_POLICY_FIELDS_KOREA if market == "KOREA_VIRTUAL" else COST_POLICY_FIELDS_OTHER
    _exact_fields(value, fields, "COST_POLICY_FIELDS_INVALID")
    schema = "p7_18_market_cost_policy_korea/1" if market == "KOREA_VIRTUAL" else "p7_18_market_cost_policy/1"
    if value["schema_version"] != schema or value["market"] != market:
        raise Stage5VirtualFillPerformanceError("COST_POLICY_IDENTITY_INVALID")
    _text(value["policy_id"], "COST_POLICY_ID_INVALID")
    _utc(value["effective_from"], "COST_POLICY_EFFECTIVE_FROM_INVALID")
    for key in ("buy_fee_bps", "sell_fee_bps", "slippage_bps"):
        number = _decimal(value[key], f"COST_POLICY_{key.upper()}_INVALID")
        if number < 0 or number > 10000:
            raise Stage5VirtualFillPerformanceError(f"COST_POLICY_{key.upper()}_INVALID")
    if market == "KOREA_VIRTUAL":
        tax = _decimal(value["sell_tax_bps"], "COST_POLICY_SELL_TAX_INVALID")
        if tax < 0 or tax > 10000:
            raise Stage5VirtualFillPerformanceError("COST_POLICY_SELL_TAX_INVALID")
    pin = _source_pin(value["source_pin"], "COST_POLICY_SOURCE")
    if pin != expected_pin:
        raise Stage5VirtualFillPerformanceError("COST_POLICY_SOURCE_EXPECTED_PIN_MISMATCH")
    _validate_self_hash(value, "payload_sha256", "COST_POLICY_SHA")
    return copy.deepcopy(value)


def _validate_fx(value: object, *, expected_pin: dict, fill_time: str) -> dict:
    _exact_fields(value, FX_RATE_FIELDS, "FX_RATE_FIELDS_INVALID")
    if value["schema_version"] != "p7_18_fx_rate_attestation/1" or value["pair"] != "USD/KRW":
        raise Stage5VirtualFillPerformanceError("FX_RATE_IDENTITY_INVALID")
    _decimal(value["rate"], "FX_RATE_INVALID", positive=True)
    if value["staleness_status"] != "FRESH":
        raise Stage5VirtualFillPerformanceError("FX_RATE_NOT_FRESH_AT_FILL")
    if _utc(value["as_of"], "FX_AS_OF_INVALID") > _utc(fill_time, "PRICE_FILL_TIME_INVALID"):
        raise Stage5VirtualFillPerformanceError("FX_FROM_FUTURE")
    pin = _source_pin(value["source_pin"], "FX_SOURCE")
    if pin != expected_pin:
        raise Stage5VirtualFillPerformanceError("FX_SOURCE_EXPECTED_PIN_MISMATCH")
    _validate_self_hash(value, "payload_sha256", "FX_RATE_SHA")
    return copy.deepcopy(value)


def _derive_fill_result(request: object, expected_entry: dict) -> dict:
    _exact_fields(request, REQUEST_FIELDS, "FILL_REQUEST_FIELDS_INVALID")
    evaluation_id = _text(request["evaluation_id"], "FILL_EVALUATION_ID_INVALID")
    if evaluation_id != expected_entry["evaluation_id"]:
        raise Stage5VirtualFillPerformanceError("FILL_EVALUATION_ID_MISMATCH")
    evaluated_at = _utc(request["evaluated_at_utc"], "FILL_EVALUATED_AT_INVALID")
    market, side = request["market"], request["side"]
    if market not in MARKETS or side not in SIDES:
        raise Stage5VirtualFillPerformanceError("FILL_MARKET_OR_SIDE_INVALID")
    notional = _decimal(request["notional_native"], "FILL_NOTIONAL_INVALID", positive=True)
    price = _validate_price(
        request["eligible_price"], market=market,
        expected_pin=expected_entry["eligible_price_source_pin"],
    )
    if _utc(price["fill_time"], "PRICE_FILL_TIME_INVALID") > evaluated_at:
        raise Stage5VirtualFillPerformanceError("FILL_AFTER_EVALUATION")
    cost = _validate_cost(
        request["cost_policy"], market=market,
        expected_pin=expected_entry["cost_policy_source_pin"],
    )
    if _utc(cost["effective_from"], "COST_POLICY_EFFECTIVE_FROM_INVALID") > _utc(
        price["fill_time"], "PRICE_FILL_TIME_INVALID"
    ):
        raise Stage5VirtualFillPerformanceError("COST_POLICY_NOT_EFFECTIVE_AT_FILL_TIME")

    fx = request["fx_rate"]
    if market == "US_VIRTUAL":
        if fx is None or expected_entry["fx_source_pin"] is None:
            raise Stage5VirtualFillPerformanceError("FX_RATE_REQUIRED")
        fx = _validate_fx(
            fx, expected_pin=expected_entry["fx_source_pin"],
            fill_time=price["fill_time"],
        )
    elif fx is not None or expected_entry["fx_source_pin"] is not None:
        raise Stage5VirtualFillPerformanceError("FX_RATE_NOT_APPLICABLE")

    bps = Decimal(10000)
    fee_rate = _decimal(cost["buy_fee_bps"] if side == "BUY" else cost["sell_fee_bps"], "FEE_BPS_INVALID")
    slip_rate = _decimal(cost["slippage_bps"], "SLIPPAGE_BPS_INVALID")
    tax_rate = (
        _decimal(cost["sell_tax_bps"], "SELL_TAX_BPS_INVALID")
        if market == "KOREA_VIRTUAL" and side == "SELL" else Decimal(0)
    )
    fee = notional * fee_rate / bps
    tax = notional * tax_rate / bps
    slippage = notional * slip_rate / bps
    net_native = (
        -(notional + fee + slippage)
        if side == "BUY" else notional - fee - tax - slippage
    )
    net_krw = net_native if market != "US_VIRTUAL" else net_native * _decimal(fx["rate"], "FX_RATE_INVALID", positive=True)
    result = {
        "schema_version": FILL_SCHEMA,
        "evaluation_id": evaluation_id,
        "evaluated_at": request["evaluated_at_utc"],
        "market": market,
        "side": side,
        "gross_notional_native": format(notional, "f"),
        "fee_native": format(fee, "f"),
        "tax_native": format(tax, "f"),
        "slippage_native": format(slippage, "f"),
        "net_cash_native": format(net_native, "f"),
        "net_cash_krw": format(net_krw, "f"),
        "native_currency": NATIVE_CURRENCY[market],
        "fx_applied": market == "US_VIRTUAL",
        "eligible_price": price,
        "cost_policy": cost,
        "fx_rate": fx,
        "authority": copy.deepcopy(FILL_AUTHORITY),
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


def _zero_position(market: str) -> dict:
    return {
        "quantity_native": "0",
        "native_currency": NATIVE_CURRENCY[market],
        "cost_basis_krw": "0",
        "realized_pnl_krw": "0",
    }


def _derive_after_state(fill: dict, position_before: dict, cash_before: Decimal):
    quantity = _decimal(fill["gross_notional_native"], "FILL_QUANTITY_INVALID", positive=True)
    net_cash = _decimal(fill["net_cash_krw"], "FILL_NET_CASH_KRW_INVALID")
    if fill["side"] == "BUY":
        cash_after = cash_before + net_cash
        if cash_after < 0:
            raise Stage5VirtualFillPerformanceError("INSUFFICIENT_VIRTUAL_CASH")
        position_after = {
            "quantity_native": (
                _krw_text(_decimal(position_before["quantity_native"], "POSITION_QUANTITY_INVALID") + quantity)
                if fill["native_currency"] == "KRW"
                else format(_decimal(position_before["quantity_native"], "POSITION_QUANTITY_INVALID") + quantity, "f")
            ),
            "native_currency": fill["native_currency"],
            "cost_basis_krw": _krw_text(_decimal(position_before["cost_basis_krw"], "POSITION_BASIS_INVALID") - net_cash),
            "realized_pnl_krw": position_before["realized_pnl_krw"],
        }
        realized_delta = Decimal(0)
    else:
        current_quantity = _decimal(position_before["quantity_native"], "POSITION_QUANTITY_INVALID")
        if quantity > current_quantity or current_quantity <= 0:
            raise Stage5VirtualFillPerformanceError("SELL_EXCEEDS_VIRTUAL_POSITION")
        basis = _decimal(position_before["cost_basis_krw"], "POSITION_BASIS_INVALID")
        basis_removed = basis * quantity / current_quantity
        remaining = current_quantity - quantity
        remaining_basis = basis - basis_removed
        if remaining == 0:
            remaining_basis = Decimal(0)
        realized_delta = net_cash - basis_removed
        cash_after = cash_before + net_cash
        position_after = {
            "quantity_native": _krw_text(remaining) if fill["native_currency"] == "KRW" else format(remaining, "f"),
            "native_currency": fill["native_currency"],
            "cost_basis_krw": _krw_text(remaining_basis),
            "realized_pnl_krw": _krw_text(_decimal(position_before["realized_pnl_krw"], "POSITION_REALIZED_INVALID") + realized_delta),
        }
    return cash_after, position_after, realized_delta


def _all_false_authority(value: object, code: str) -> dict:
    if not isinstance(value, dict) or not value or any(item is not False for item in value.values()):
        raise Stage5VirtualFillPerformanceError(code)
    return copy.deepcopy(value)


def _validate_connector_envelope(value: object, pins: dict) -> dict:
    _exact_fields(value, CONNECTOR_ENVELOPE_FIELDS, "CONNECTOR_ENVELOPE_FIELDS_INVALID")
    if (
        value["schema_version"] != CONNECTOR_ENVELOPE_SCHEMA
        or value["contract_version"] != CONNECTOR_CONTRACT_VERSION
        or value["mode"] != CONNECTOR_MODE
        or value["evidence_class"] not in ("SYNTHETIC_INTERNAL_VALIDATION", "NATURAL_VERIFIED")
    ):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ENVELOPE_IDENTITY_INVALID")
    _text(value["envelope_id"], "CONNECTOR_ENVELOPE_ID_INVALID")
    if value["envelope_id"].startswith("STAGE5.FIXTURE."):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_FIXTURE_NAMESPACE_FORBIDDEN")
    _all_false_authority(value["authority"], "CONNECTOR_ENVELOPE_AUTHORITY_INVALID")
    _validate_self_hash(value, "packet_sha256", "CONNECTOR_ENVELOPE_SHA")
    if value["packet_sha256"] != pins["connector_envelope_sha256"]:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ENVELOPE_EXPECTED_PIN_MISMATCH")

    decision = value["decision"]
    if not isinstance(decision, dict):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_DECISION_INVALID")
    _validate_self_hash(decision, "packet_sha256", "CONNECTOR_DECISION_SHA")
    if decision["packet_sha256"] != pins["expected_decision_packet_sha256"]:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_DECISION_PACKET_PIN_MISMATCH")
    if _sha(decision.get("source_sha256"), "CONNECTOR_DECISION_SOURCE_SHA_INVALID") != pins[
        "expected_decision_source_sha256"
    ]:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_DECISION_SOURCE_PIN_MISMATCH")
    _all_false_authority(
        decision.get("authority"), "CONNECTOR_DECISION_AUTHORITY_INVALID"
    )

    allocation = value["allocation_decision"]
    if (
        not isinstance(allocation, dict)
        or allocation.get("schema_version") != "p7_17_allocation_decision_ledger_entry/1"
        or allocation.get("target_availability_status") != "AVAILABLE"
        or allocation.get("new_target_krw") is None
    ):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ALLOCATION_INVALID")
    _validate_self_hash(allocation, "entry_sha256", "CONNECTOR_ALLOCATION_SHA")
    _utc(allocation.get("decision_time"), "CONNECTOR_ALLOCATION_TIME_INVALID")
    if allocation.get("authority") != ALLOCATION_AUTHORITY:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ALLOCATION_AUTHORITY_INVALID")

    request = value["fill_cost_request"]
    result = value["fill_cost_result"]
    _exact_fields(request, REQUEST_FIELDS, "CONNECTOR_FILL_REQUEST_FIELDS_INVALID")
    _exact_fields(result, FILL_FIELDS, "CONNECTOR_FILL_RESULT_FIELDS_INVALID")
    ledger_request = value["ledger_request"]
    _exact_fields(
        ledger_request, ("recorded_at", "max_price_age_seconds"),
        "CONNECTOR_LEDGER_REQUEST_FIELDS_INVALID",
    )
    _utc(ledger_request["recorded_at"], "CONNECTOR_LEDGER_RECORDED_AT_INVALID")
    if type(ledger_request["max_price_age_seconds"]) is not int or ledger_request[
        "max_price_age_seconds"
    ] < 0:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_LEDGER_MAX_AGE_INVALID")

    dependencies = value["dependency_pins"]
    if not isinstance(dependencies, dict) or tuple(dependencies) != (
        "stage4_result_store", "p7_17_allocation_ledger",
        "p7_18_cost_model", "p7_18_fill_ledger",
    ):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_DEPENDENCY_PINS_INVALID")
    for name, pin in dependencies.items():
        _source_pin(pin, f"CONNECTOR_DEPENDENCY_{name.upper()}")
    return copy.deepcopy(value)


def _validate_connector_receipt(value: object, envelope: dict, pins: dict) -> dict:
    _exact_fields(value, CONNECTOR_RECEIPT_FIELDS, "CONNECTOR_RECEIPT_FIELDS_INVALID")
    if (
        value["schema_version"] != CONNECTOR_RECEIPT_SCHEMA
        or value["contract_version"] != CONNECTOR_CONTRACT_VERSION
        or value["mode"] != CONNECTOR_MODE
        or value["envelope_id"] != envelope["envelope_id"]
        or value["evidence_class"] != envelope["evidence_class"]
        or value["authority"] != envelope["authority"]
    ):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_IDENTITY_INVALID")
    _validate_self_hash(value, "packet_sha256", "CONNECTOR_RECEIPT_SHA")
    if value["packet_sha256"] != pins["connector_lifecycle_receipt_sha256"]:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_EXPECTED_PIN_MISMATCH")
    if value["attempted_at"] != envelope["ledger_request"]["recorded_at"]:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_ATTEMPT_TIME_MISMATCH")
    if value["request_sha256"] != payload_sha256(envelope["fill_cost_request"]):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_REQUEST_SHA_MISMATCH")
    if value["fill_cost_payload_sha256"] != envelope["fill_cost_result"]["payload_sha256"]:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_FILL_SHA_MISMATCH")
    if value["status"] not in (
        "APPLIED_TO_EXISTING_INTERNAL_VIRTUAL_LEDGER", "DUPLICATE_NO_CHANGE",
    ):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_NOT_CONSUMABLE")
    for state in (value["before"], value["after"]):
        _exact_fields(state, CONNECTOR_STATE_PIN_FIELDS, "CONNECTOR_STATE_PIN_FIELDS_INVALID")
        _text(state["ledger_ref"], "CONNECTOR_LEDGER_REF_INVALID")
        _sha(state["ledger_file_sha256"], "CONNECTOR_LEDGER_CONTENT_SHA_INVALID")
        _sha(state["snapshot_sha256"], "CONNECTOR_SNAPSHOT_SHA_INVALID")
        if state["head_entry_sha256"] is not None:
            _sha(state["head_entry_sha256"], "CONNECTOR_HEAD_ENTRY_SHA_INVALID")
        if type(state["entry_count"]) is not int or state["entry_count"] < 0:
            raise Stage5VirtualFillPerformanceError("CONNECTOR_ENTRY_COUNT_INVALID")
    if value["status"] == "APPLIED_TO_EXISTING_INTERNAL_VIRTUAL_LEDGER":
        valid = (
            value["execution_performed"] is True
            and value["result_code"] == "APPLIED"
            and value["error_code"] is None
            and value["after"]["entry_count"] == value["before"]["entry_count"] + 1
            and value["deduplication"] == {
                "checked": True, "result": "NO_CHANGE", "state_unchanged": True,
            }
            and value["recovery"] == {
                "status": "NOT_REQUIRED", "source_ref": value["after"]["ledger_ref"],
            }
        )
    else:
        valid = (
            value["execution_performed"] is False
            and value["result_code"] == "NO_CHANGE"
            and value["error_code"] is None
            and value["before"] == value["after"]
            and value["deduplication"] == {
                "checked": True, "result": "NO_CHANGE", "state_unchanged": True,
            }
            and value["recovery"] == {
                "status": "NOT_REQUIRED", "source_ref": value["before"]["ledger_ref"],
            }
        )
    if not valid:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_STATUS_SHAPE_INVALID")
    return copy.deepcopy(value)


def resolve_connector_result(
    ledger_bytes: bytes,
    *,
    connector_envelope: dict,
    lifecycle_receipt: dict,
    expected_pins: dict,
    evaluated_at: str,
) -> dict:
    """Return a redacted, untrusted, zero-sample P7-19 readiness projection."""
    moment = _utc(evaluated_at, "EVALUATED_AT_INVALID")
    pins = _expected_pins(expected_pins)
    envelope = _validate_connector_envelope(connector_envelope, pins)
    receipt = _validate_connector_receipt(lifecycle_receipt, envelope, pins)
    raw_sha = hashlib.sha256(ledger_bytes).hexdigest() if isinstance(ledger_bytes, bytes) else None
    if raw_sha != pins["ledger_raw_bytes_sha256"]:
        raise Stage5VirtualFillPerformanceError("LEDGER_RAW_BYTES_EXPECTED_PIN_MISMATCH")
    ledger = _parse_json_bytes(ledger_bytes)
    if tuple(ledger) != (
        "schema_version", "genesis_snapshot_payload_sha256", "entries", "file_sha256",
    ) or ledger.get("schema_version") != LEDGER_SCHEMA:
        raise Stage5VirtualFillPerformanceError("LEDGER_SCHEMA_INVALID")
    _validate_self_hash(ledger, "file_sha256", "LEDGER_PAYLOAD_SHA")
    if ledger["file_sha256"] != pins["ledger_content_payload_sha256"]:
        raise Stage5VirtualFillPerformanceError("LEDGER_CONTENT_PAYLOAD_EXPECTED_PIN_MISMATCH")
    if ledger["genesis_snapshot_payload_sha256"] != pins["genesis_snapshot_payload_sha256"]:
        raise Stage5VirtualFillPerformanceError("LEDGER_GENESIS_EXPECTED_PIN_MISMATCH")
    entries = ledger["entries"]
    if not isinstance(entries, list) or len(entries) != len(pins["entries"]):
        raise Stage5VirtualFillPerformanceError("LEDGER_ENTRY_SET_INVALID")
    cash = _decimal(pins["initial_cash_krw"], "EXPECTED_INITIAL_CASH_INVALID", positive=True)
    positions = {market: _zero_position(market) for market in MARKETS}
    derived_states = []
    previous = None
    previous_recorded = None
    seen_fill = set()
    seen_evaluation = set()
    for index, (entry, expected) in enumerate(zip(entries, pins["entries"]), 1):
        _exact_fields(entry, ENTRY_FIELDS, f"ENTRY_FIELDS_INVALID:{index}")
        if entry["schema_version"] != ENTRY_SCHEMA or entry["sequence"] != index:
            raise Stage5VirtualFillPerformanceError(f"ENTRY_IDENTITY_INVALID:{index}")
        _validate_self_hash(entry, "entry_sha256", f"ENTRY_SHA:{index}")
        if entry["entry_sha256"] != expected["entry_sha256"]:
            raise Stage5VirtualFillPerformanceError("ENTRY_EXPECTED_PIN_MISMATCH")
        if entry["previous_entry_sha256"] != previous:
            raise Stage5VirtualFillPerformanceError("ENTRY_CHAIN_BROKEN")
        recorded = _utc(entry["recorded_at"], f"ENTRY_RECORDED_AT_INVALID:{index}")
        if previous_recorded is not None and recorded < previous_recorded:
            raise Stage5VirtualFillPerformanceError("ENTRY_RECORDED_AT_BACKWARDS")
        if recorded > moment:
            raise Stage5VirtualFillPerformanceError("ENTRY_FROM_FUTURE")
        if entry["authority"] != ENTRY_AUTHORITY:
            raise Stage5VirtualFillPerformanceError("ENTRY_AUTHORITY_INVALID")
        if type(entry["max_price_age_seconds"]) is not int or entry["max_price_age_seconds"] < 0:
            raise Stage5VirtualFillPerformanceError("ENTRY_MAX_PRICE_AGE_INVALID")
        if type(entry["price_age_seconds"]) is not int or entry["price_age_seconds"] < 0:
            raise Stage5VirtualFillPerformanceError("ENTRY_PRICE_AGE_INVALID")

        request = entry["fill_cost_request"]
        evaluation_id = request.get("evaluation_id") if isinstance(request, dict) else None
        if evaluation_id in seen_evaluation:
            raise Stage5VirtualFillPerformanceError("FILL_EVALUATION_ID_DUPLICATE")
        expected_fill = _derive_fill_result(request, expected)
        _exact_fields(entry["fill_cost_result"], FILL_FIELDS, "FILL_RESULT_FIELDS_INVALID")
        if entry["fill_cost_result"] != expected_fill:
            raise Stage5VirtualFillPerformanceError("FILL_RESULT_DERIVATION_MISMATCH")
        fill_sha = expected_fill["payload_sha256"]
        if (
            fill_sha != expected["fill_cost_payload_sha256"]
            or entry["identity_sha256"] != fill_sha
            or entry["fill_cost_payload_sha256"] != fill_sha
            or entry["market"] != expected_fill["market"]
            or entry["side"] != expected_fill["side"]
        ):
            raise Stage5VirtualFillPerformanceError("ENTRY_FILL_BINDING_MISMATCH")
        if fill_sha in seen_fill:
            raise Stage5VirtualFillPerformanceError("FILL_IDENTITY_DUPLICATE")
        seen_fill.add(fill_sha)
        seen_evaluation.add(evaluation_id)

        fill_time = _utc(expected_fill["eligible_price"]["fill_time"], "FILL_TIME_INVALID")
        age = int((recorded - fill_time).total_seconds())
        if age < 0 or age != entry["price_age_seconds"] or age > entry["max_price_age_seconds"]:
            raise Stage5VirtualFillPerformanceError("ENTRY_PRICE_AGE_MISMATCH")
        market = expected_fill["market"]
        if tuple(entry["position_before"]) != POSITION_FIELDS or tuple(entry["position_after"]) != POSITION_FIELDS:
            raise Stage5VirtualFillPerformanceError("ENTRY_POSITION_FIELDS_INVALID")
        if entry["cash_before_krw"] != _krw_text(cash) or entry["position_before"] != positions[market]:
            raise Stage5VirtualFillPerformanceError("ENTRY_BEFORE_STATE_MISMATCH")
        cash_after, position_after, realized_delta = _derive_after_state(
            expected_fill, positions[market], cash
        )
        if (
            entry["cash_after_krw"] != _krw_text(cash_after)
            or entry["position_after"] != position_after
            or entry["realized_pnl_delta_krw"] != _krw_text(realized_delta)
        ):
            raise Stage5VirtualFillPerformanceError("ENTRY_AFTER_STATE_MISMATCH")
        cash, positions[market] = cash_after, position_after
        previous = entry["entry_sha256"]
        previous_recorded = recorded
        derived_states.append((cash, copy.deepcopy(positions), previous))

    if entries and previous != pins["entries"][-1]["entry_sha256"]:
        raise Stage5VirtualFillPerformanceError("LEDGER_HEAD_EXPECTED_PIN_MISMATCH")

    matching = [
        entry for entry in entries
        if entry["fill_cost_payload_sha256"] == receipt["fill_cost_payload_sha256"]
    ]
    if len(matching) != 1:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_LEDGER_FILL_MISMATCH")
    target = matching[0]
    if (
        envelope["fill_cost_request"] != target["fill_cost_request"]
        or envelope["fill_cost_result"] != target["fill_cost_result"]
        or envelope["ledger_request"] != {
            "recorded_at": target["recorded_at"],
            "max_price_age_seconds": target["max_price_age_seconds"],
        }
    ):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ENVELOPE_LEDGER_BINDING_MISMATCH")
    allocation = envelope["allocation_decision"]
    market, side = target["market"], target["side"]
    if allocation["decision_time"] != target["fill_cost_request"]["eligible_price"]["decision_time"]:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ALLOCATION_PRICE_TIME_MISMATCH")
    try:
        drift = _decimal(
            allocation["drift_by_sleeve_krw"][market],
            "CONNECTOR_ALLOCATION_DRIFT_INVALID",
        )
    except (KeyError, TypeError) as exc:
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ALLOCATION_DRIFT_INVALID") from exc
    if drift == 0 or (side == "BUY" and drift < 0) or (side == "SELL" and drift > 0):
        raise Stage5VirtualFillPerformanceError("CONNECTOR_ALLOCATION_FILL_DIRECTION_MISMATCH")

    def state_pin(entry_count: int) -> dict:
        state_cash = _decimal(pins["initial_cash_krw"], "EXPECTED_INITIAL_CASH_INVALID", positive=True)
        state_positions = {market_name: _zero_position(market_name) for market_name in MARKETS}
        state_head = None
        if entry_count:
            state_cash, state_positions, state_head = derived_states[entry_count - 1]
        snapshot = {
            "cash_krw": _krw_text(state_cash),
            "positions": copy.deepcopy(state_positions),
            "book_value_krw": _krw_text(
                state_cash + sum(
                    (_decimal(state_positions[name]["cost_basis_krw"], "POSITION_BASIS_INVALID") for name in MARKETS),
                    Decimal(0),
                )
            ),
            "cumulative_realized_pnl_krw": _krw_text(
                sum(
                    (_decimal(state_positions[name]["realized_pnl_krw"], "POSITION_REALIZED_INVALID") for name in MARKETS),
                    Decimal(0),
                )
            ),
            "entry_count": entry_count,
            "head_entry_sha256": state_head,
            "reconciliation_note": (
                "book_value_krw is cost-basis book value, not a mark-to-market NAV -- "
                "there is no live price feed in scope here. book_value_krw must always "
                "equal ratified NAV plus cumulative_realized_pnl_krw; see reconcile()."
            ),
        }
        partial = {
            "schema_version": LEDGER_SCHEMA,
            "genesis_snapshot_payload_sha256": ledger["genesis_snapshot_payload_sha256"],
            "entries": copy.deepcopy(entries[:entry_count]),
        }
        return {
            "ledger_file_sha256": payload_sha256(partial),
            "snapshot_sha256": payload_sha256(snapshot),
            "head_entry_sha256": state_head,
            "entry_count": entry_count,
        }

    current_pin = state_pin(len(entries))
    for key, value in current_pin.items():
        if receipt["after"][key] != value:
            raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_AFTER_STATE_MISMATCH")
    if receipt["status"] == "APPLIED_TO_EXISTING_INTERNAL_VIRTUAL_LEDGER":
        if target is not entries[-1]:
            raise Stage5VirtualFillPerformanceError("CONNECTOR_APPLIED_ENTRY_NOT_LEDGER_HEAD")
        before_pin = state_pin(len(entries) - 1)
        for key, value in before_pin.items():
            if receipt["before"][key] != value:
                raise Stage5VirtualFillPerformanceError("CONNECTOR_RECEIPT_BEFORE_STATE_MISMATCH")

    limitations = [
        "CALLER_SUPPLIED_PINS_ARE_NOT_AN_INDEPENDENT_TRUST_ANCHOR",
        "P7_18_TRANSACTION_STATE_IS_NOT_P7_19_FUTURE_OUTCOME",
        "P7_19_DAILY_NAV_HISTORY_NOT_SUPPLIED",
        "P7_19_STATIC_ALLOCATION_BENCHMARK_NOT_SUPPLIED",
        "P7_19_CASH_BENCHMARK_NOT_SUPPLIED",
        "SYNTHETIC_INTEGRATION_NEVER_COUNTS_AS_NATURAL_PERFORMANCE",
        "INDEPENDENT_PRIVATE_HANDOFF_RECEIPT_PIN_NOT_YET_INTEGRATED",
        "PRODUCER_COMMIT_IS_CALLER_PINNED_NOT_DERIVED_FROM_LEDGER_BYTES",
    ]
    packet = {
        "schema_version": SCHEMA_VERSION,
        "status": "UNTRUSTED_CALLER_PINNED_REDERIVATION_ONLY",
        "evaluated_at": evaluated_at,
        "caller_pinned_source_identity": {
            "producer_repository": pins["producer_repository"],
            "producer_commit": pins["producer_commit"],
            "connector_path": pins["connector_path"],
            "connector_module_sha256": pins["connector_module_sha256"],
            "connector_contract_payload_sha256": pins["connector_contract_payload_sha256"],
            "connector_envelope_sha256": pins["connector_envelope_sha256"],
            "connector_lifecycle_receipt_sha256": pins["connector_lifecycle_receipt_sha256"],
            "ledger_raw_bytes_sha256": pins["ledger_raw_bytes_sha256"],
            "ledger_content_payload_sha256": pins["ledger_content_payload_sha256"],
            "genesis_snapshot_payload_sha256": pins["genesis_snapshot_payload_sha256"],
            "entry_count": len(entries),
            "head_entry_sha256": previous,
        },
        "verification": {
            "caller_supplied_pins_consistent": True,
            "connector_envelope_rederived": True,
            "connector_lifecycle_receipt_rederived": True,
            "decision_hashes_consistent": True,
            "ledger_hash_chain": True,
            "fill_rederivation": True,
            "price_time_order": True,
            "cost_rederivation": True,
            "fx_binding": True,
            "cash_position_reconciliation": True,
        },
        "performance_input": {
            "status": "OUTCOME_OBSERVATION_REQUIRED",
            "sample_contribution": 0,
            "daily_nav_krw": None,
            "pnl_krw": None,
            "return_pct": None,
            "max_drawdown_pct": None,
            "volatility": None,
            "recovery_time": None,
            "turnover": None,
            "cash_drag": None,
            "fx_contribution_krw": None,
            "rebalance_cost_krw": None,
            "atlas_dynamic_comparison": None,
            "static_allocation_comparison": None,
            "cash_comparison": None,
        },
        "limitations": limitations,
        "authority": copy.deepcopy(AUTHORITY),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_projection(
    value: object,
    ledger_bytes: bytes,
    *,
    connector_envelope: dict,
    lifecycle_receipt: dict,
    expected_pins: dict,
) -> dict:
    """Rebuild a persisted projection; its own digest is never sufficient."""
    fields = (
        "schema_version", "status", "evaluated_at", "caller_pinned_source_identity",
        "verification", "performance_input", "limitations", "authority",
        "packet_sha256",
    )
    _exact_fields(value, fields, "PROJECTION_FIELDS_INVALID")
    if value["schema_version"] != SCHEMA_VERSION:
        raise Stage5VirtualFillPerformanceError("PROJECTION_SCHEMA_INVALID")
    _validate_self_hash(value, "packet_sha256", "PROJECTION_SHA")
    expected = resolve_connector_result(
        ledger_bytes,
        connector_envelope=connector_envelope,
        lifecycle_receipt=lifecycle_receipt,
        expected_pins=expected_pins,
        evaluated_at=value["evaluated_at"],
    )
    if value != expected:
        raise Stage5VirtualFillPerformanceError("PROJECTION_DERIVATION_MISMATCH")
    return copy.deepcopy(expected)


__all__ = [
    "ALLOCATION_AUTHORITY", "AUTHORITY", "CONNECTOR_CONTRACT_VERSION", "CONNECTOR_ENVELOPE_FIELDS",
    "CONNECTOR_ENVELOPE_SCHEMA", "CONNECTOR_MODE", "CONNECTOR_RECEIPT_FIELDS",
    "CONNECTOR_RECEIPT_SCHEMA", "ENTRY_AUTHORITY", "ENTRY_FIELDS", "ENTRY_SCHEMA", "EXPECTED_ENTRY_PIN_FIELDS",
    "EXPECTED_PIN_FIELDS", "FILL_AUTHORITY", "FILL_FIELDS", "FILL_SCHEMA",
    "CONNECTOR_PATH", "LEDGER_SCHEMA", "PRODUCER_REPOSITORY", "SCHEMA_VERSION",
    "SOURCE_PIN_FIELDS",
    "Stage5VirtualFillPerformanceError", "canonical_json", "payload_sha256",
    "resolve_connector_result", "validate_projection",
]
