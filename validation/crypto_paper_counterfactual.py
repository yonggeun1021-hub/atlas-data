#!/usr/bin/env python3
"""P10-12 Crypto PAPER counterfactual validation scaffolding.

This module is an offline diagnostic consumer.  It reuses P10-11's exact
ledger/account validators and computes review metrics from caller-supplied,
hash-bound artifacts.  It cannot start D0 by itself: only a separate explicit
OPEN gate artifact plus NATURAL_AUTOMATED evidence is countable.  Manual,
replay, and synthetic evidence always remain diagnostic.

No network, credential, exchange, candidate, entry, exit, sizing, or threshold
generation path exists here.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "crypto_paper_counterfactual_validation_contract.json"
SIMULATOR_PATH = ROOT / "shadow" / "crypto_paper_simulator.py"
SIM_SPEC = importlib.util.spec_from_file_location("p1012_crypto_paper_simulator", SIMULATOR_PATH)
SIMULATOR = importlib.util.module_from_spec(SIM_SPEC)
assert SIM_SPEC.loader is not None
SIM_SPEC.loader.exec_module(SIMULATOR)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CryptoPaperValidationError(ValueError):
    """Fail-closed P10-12 input, lineage, or derivation violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperValidationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_paper_counterfactual_validation/1",
        "input_schema_version": "crypto_paper_validation_daily_input/1",
        "daily_report_schema_version": "crypto_paper_validation_daily_report/1",
        "cio_review_schema_version": "crypto_paper_validation_cio_review/1",
        "sample_origins": [
            "NATURAL_AUTOMATED", "MANUAL_OBSERVATION", "PIT_REPLAY", "SYNTHETIC_FIXTURE",
        ],
        "artifact_roles": [
            "D0_GATE", "LEDGER", "ACCOUNT_STATE", "NAV_SERIES", "MARK_SERIES",
            "PLANNED_LOSS", "ERROR_ASSESSMENT",
        ],
        "error_metric_types": [
            "FALSE_POSITIVE", "MISS", "STALE", "DUPLICATE", "SILENT_ERROR",
        ],
        "metric_statuses": ["PRESENT", "ABSENT", "UNVERIFIED"],
        "official_gate_prerequisites": [
            "exact_public_private_pins_verified",
            "natural_eligible_later_book_fill_verified",
            "natural_virtual_sell_reconciliation_verified",
            "restart_recovery_verified",
            "silent_error_zero_24h_verified",
            "cio_d0_ratification_recorded",
        ],
        "official_count_policy": (
            "EXPLICIT_OPEN_GATE_PLUS_NATURAL_AUTOMATED_ONLY_NO_RETROACTIVE_BACKFILL"
        ),
        "lookahead_policy": "OBSERVED_AT_LE_AVAILABLE_AT_LE_REPORT_GENERATED_AT",
        "counterfactual_policy": (
            "NO_TRADE_PNL_IS_ZERO_AND_NEVER_PROMOTES_A_HISTORICAL_CANDIDATE"
        ),
        "daily_chain_policy": "CONTENT_ADDRESSED_APPEND_ONLY_EXACT_PREDECESSOR",
        "review_policy": (
            "THIRTY_CONSECUTIVE_COUNTABLE_DAYS_ENABLE_CIO_REVIEW_NOT_LIVE_AUTHORITY"
        ),
        "authority": {
            "diagnostic_only": True,
            "official_d0_automatic_start_authorized": False,
            "candidate_promotion_authorized": False,
            "strategy_change_authorized": False,
            "risk_threshold_change_authorized": False,
            "paper_order_authorized": False,
            "paper_exit_authorized": False,
            "exchange_order_authorized": False,
            "withdrawal_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CryptoPaperValidationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CryptoPaperValidationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CryptoPaperValidationError(code)
    return value


def _token(value, code: str) -> str:
    value = _text(value, code)
    if TOKEN_RE.fullmatch(value) is None:
        raise CryptoPaperValidationError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CryptoPaperValidationError(code)
    return value


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CryptoPaperValidationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoPaperValidationError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CryptoPaperValidationError(code)
    return parsed


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise CryptoPaperValidationError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CryptoPaperValidationError(code) from exc
    if parsed.isoformat() != value:
        raise CryptoPaperValidationError(code)
    return parsed


def _decimal(value, code: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise CryptoPaperValidationError(code)
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CryptoPaperValidationError(code) from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise CryptoPaperValidationError(code)
    if value != _format_decimal(parsed):
        raise CryptoPaperValidationError(f"{code}:NON_CANONICAL")
    return parsed


def _format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise CryptoPaperValidationError("DECIMAL_NON_FINITE")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _pct(value: Decimal) -> str:
    return _format_decimal(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN))


def _with_sha(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["packet_sha256"] = payload_sha256(result)
    return result


def _artifact_map(batch: dict, generated_at: dt.datetime, contract: dict) -> dict[str, dict]:
    rows = batch.get("source_artifacts")
    if not isinstance(rows, list) or len(rows) != len(contract["artifact_roles"]):
        raise CryptoPaperValidationError("SOURCE_ARTIFACT_SET_INCOMPLETE")
    by_role = {}
    for index, row in enumerate(rows):
        fields = {"role", "origin", "source_ref", "available_at", "payload", "payload_sha256"}
        if not isinstance(row, dict) or set(row) != fields:
            raise CryptoPaperValidationError(f"SOURCE_ARTIFACT_FIELDS_MISMATCH:{index}")
        role = row.get("role")
        if role not in contract["artifact_roles"] or role in by_role:
            raise CryptoPaperValidationError(f"SOURCE_ARTIFACT_ROLE_INVALID:{role}")
        origin = row.get("origin")
        if role == "D0_GATE":
            if origin != "CONTROL":
                raise CryptoPaperValidationError("D0_GATE_ORIGIN_INVALID")
        elif origin != batch.get("sample_origin"):
            raise CryptoPaperValidationError(f"SOURCE_ORIGIN_MISMATCH:{role}")
        available = _utc(row.get("available_at"), f"SOURCE_AVAILABLE_AT_INVALID:{role}")
        if available > generated_at:
            raise CryptoPaperValidationError(f"SOURCE_FROM_FUTURE:{role}")
        _text(row.get("source_ref"), f"SOURCE_REF_INVALID:{role}")
        digest = _sha(row.get("payload_sha256"), f"SOURCE_SHA_INVALID:{role}")
        if payload_sha256(row.get("payload")) != digest:
            raise CryptoPaperValidationError(f"SOURCE_SHA_MISMATCH:{role}")
        by_role[role] = copy.deepcopy(row)
    if set(by_role) != set(contract["artifact_roles"]):
        raise CryptoPaperValidationError("SOURCE_ARTIFACT_ROLES_MISMATCH")
    return by_role


def _official_status(gate_artifact: dict, *, origin: str, report_date: dt.date,
                     generated_at: dt.datetime, contract: dict) -> dict:
    gate = gate_artifact["payload"]
    fields = {"gate_id", "status", "d0_date", "opened_at", "prerequisites", "authority"}
    if not isinstance(gate, dict) or set(gate) != fields:
        raise CryptoPaperValidationError("D0_GATE_FIELDS_MISMATCH")
    _token(gate.get("gate_id"), "D0_GATE_ID_INVALID")
    status = gate.get("status")
    expected_authority = {
        "official_count_authorized": status == "OPEN",
        "live_review_authorized": False,
        "exchange_order_authorized": False,
        "real_capital_authorized": False,
    }
    if gate.get("authority") != expected_authority:
        raise CryptoPaperValidationError("D0_GATE_AUTHORITY_INVALID")
    prerequisites = gate.get("prerequisites")
    if not isinstance(prerequisites, dict) or set(prerequisites) != set(
        contract["official_gate_prerequisites"]
    ) or any(type(value) is not bool for value in prerequisites.values()):
        raise CryptoPaperValidationError("D0_GATE_PREREQUISITES_INVALID")
    if status == "CLOSED":
        if gate.get("d0_date") is not None or gate.get("opened_at") is not None:
            raise CryptoPaperValidationError("CLOSED_D0_GATE_HAS_START")
        if any(prerequisites.values()):
            raise CryptoPaperValidationError("CLOSED_D0_GATE_HAS_SATISFIED_PREREQUISITE")
        return {
            "status": "NOT_STARTED", "d0_started": False, "countable": False,
            "day_number": None, "d0_date": None,
            "reason": "EXPLICIT_D0_GATE_CLOSED",
            "gate_source_ref": gate_artifact["source_ref"],
            "gate_source_sha256": gate_artifact["payload_sha256"],
        }
    if status != "OPEN":
        raise CryptoPaperValidationError("D0_GATE_STATUS_INVALID")
    if not all(prerequisites.values()):
        raise CryptoPaperValidationError("OPEN_D0_GATE_PREREQUISITE_FALSE")
    d0_date = _date(gate.get("d0_date"), "D0_DATE_INVALID")
    opened_at = _utc(gate.get("opened_at"), "D0_OPENED_AT_INVALID")
    if (
        opened_at > generated_at
        or opened_at > _utc(gate_artifact["available_at"], "D0_GATE_AVAILABLE_AT_INVALID")
        or d0_date > report_date
    ):
        raise CryptoPaperValidationError("D0_GATE_FROM_FUTURE")
    if origin != "NATURAL_AUTOMATED":
        return {
            "status": "DIAGNOSTIC_NOT_COUNTABLE", "d0_started": True, "countable": False,
            "day_number": None, "d0_date": d0_date.isoformat(),
            "reason": f"ORIGIN_NOT_NATURAL_AUTOMATED:{origin}",
            "gate_source_ref": gate_artifact["source_ref"],
            "gate_source_sha256": gate_artifact["payload_sha256"],
        }
    return {
        "status": "COUNTABLE", "d0_started": True, "countable": True,
        "day_number": (report_date - d0_date).days + 1,
        "d0_date": d0_date.isoformat(), "reason": None,
        "gate_source_ref": gate_artifact["source_ref"],
        "gate_source_sha256": gate_artifact["payload_sha256"],
    }


def _validate_time_series(rows, *, role: str, generated_at: dt.datetime,
                          value_field: str) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise CryptoPaperValidationError(f"{role}_EMPTY")
    normalized = []
    prior = None
    for index, row in enumerate(rows):
        fields = {"observed_at", "available_at", value_field}
        if value_field == "price":
            fields.add("market")
        if not isinstance(row, dict) or set(row) != fields:
            raise CryptoPaperValidationError(f"{role}_FIELDS_MISMATCH:{index}")
        observed = _utc(row.get("observed_at"), f"{role}_OBSERVED_AT_INVALID:{index}")
        available = _utc(row.get("available_at"), f"{role}_AVAILABLE_AT_INVALID:{index}")
        if observed > available or available > generated_at:
            raise CryptoPaperValidationError(f"{role}_LOOKAHEAD:{index}")
        order_key = (observed, row.get("market")) if value_field == "price" else (observed,)
        if prior is not None and order_key <= prior:
            raise CryptoPaperValidationError(f"{role}_TIME_NOT_STRICTLY_INCREASING:{index}")
        prior = order_key
        _decimal(row.get(value_field), f"{role}_VALUE_INVALID:{index}", positive=True)
        if value_field == "price":
            _text(row.get("market"), f"{role}_MARKET_INVALID:{index}")
        normalized.append(copy.deepcopy(row))
    return normalized


def _ledger_index(ledger: dict) -> tuple[dict, list[dict]]:
    intents = {}
    fills = []
    for event in ledger["events"]:
        if event["event_type"] == "ORDER_SUBMITTED":
            intents[event["order_id"]] = event["payload"]["intent"]
        elif event["event_type"] == "FILL_APPLIED":
            row = copy.deepcopy(event["payload"])
            row["order_id"] = event["order_id"]
            row["event_at"] = event["event_at"]
            row["side"] = intents[event["order_id"]]["side"]
            row["market"] = intents[event["order_id"]]["market"]
            fills.append(row)
    return intents, fills


def _pnl_metrics(ledger: dict, account: dict) -> dict:
    initial_cash = Decimal(ledger["events"][0]["payload"]["initial_cash"])
    final_nav = Decimal(account["total_nav"])
    _, fills = _ledger_index(ledger)
    fees = sum((Decimal(row["fee_amount"]) for row in fills), Decimal("0"))
    slippage = sum((
        abs(Decimal(row["realized_slippage_bps"])) * Decimal(row["gross_value"])
        / Decimal("10000") for row in fills
    ), Decimal("0"))
    partial = sum(1 for row in fills if Decimal(row["remaining_quantity"]) > 0)
    net = final_nav - initial_cash
    return {
        "currency": ledger["currency"],
        "initial_cash": _format_decimal(initial_cash),
        "final_nav": _format_decimal(final_nav),
        "net_pnl_after_fee_slippage_partial_fill": _format_decimal(net),
        "total_fee": _format_decimal(fees),
        "estimated_slippage_cost_from_vwap_vs_best": _format_decimal(slippage),
        "fill_event_count": len(fills),
        "partial_fill_event_count": partial,
        "no_trade_benchmark_pnl": "0",
        "pnl_delta_vs_no_trade": _format_decimal(net),
        "interpretation": "POST_HOC_DIAGNOSTIC_ONLY_NO_CANDIDATE_PROMOTION",
    }


def _drawdown_metrics(rows: list[dict]) -> dict:
    peak = None
    max_amount = Decimal("0")
    max_pct = Decimal("0")
    trough_at = None
    peak_at = None
    active_peak_at = None
    for row in rows:
        nav = Decimal(row["total_nav"])
        if peak is None or nav > peak:
            peak = nav
            active_peak_at = row["observed_at"]
        amount = nav - peak
        pct = amount / peak * Decimal("100")
        if amount < max_amount:
            max_amount = amount
            max_pct = pct
            peak_at = active_peak_at
            trough_at = row["observed_at"]
    return {
        "max_drawdown_amount": _format_decimal(max_amount),
        "max_drawdown_pct": _pct(max_pct),
        "peak_at": peak_at,
        "trough_at": trough_at,
        "observation_count": len(rows),
    }


def _planned_loss_metrics(rows, *, intents: dict, fills: list[dict], marks: list[dict],
                          generated_at: dt.datetime) -> dict:
    if not isinstance(rows, list):
        raise CryptoPaperValidationError("PLANNED_LOSS_NOT_LIST")
    fill_by_order = {}
    for fill in fills:
        fill_by_order.setdefault(fill["order_id"], []).append(fill)
    results = []
    used_trade_ids = set()
    used_entry_ids = set()
    used_exit_ids = set()
    for index, row in enumerate(rows):
        fields = {
            "trade_id", "entry_order_id", "exit_order_ids", "planned_at",
            "planned_loss", "source_plan_ref", "source_plan_sha256",
        }
        if not isinstance(row, dict) or set(row) != fields:
            raise CryptoPaperValidationError(f"PLANNED_LOSS_FIELDS_MISMATCH:{index}")
        trade_id = _token(row.get("trade_id"), f"TRADE_ID_INVALID:{index}")
        entry_id = _token(row.get("entry_order_id"), f"ENTRY_ORDER_ID_INVALID:{index}")
        if trade_id in used_trade_ids or entry_id in used_entry_ids:
            raise CryptoPaperValidationError(f"PLANNED_TRADE_IDENTITY_DUPLICATE:{trade_id}")
        used_trade_ids.add(trade_id)
        used_entry_ids.add(entry_id)
        intent = intents.get(entry_id)
        if intent is None or intent["side"] != "BUY":
            raise CryptoPaperValidationError(f"PLANNED_ENTRY_ORDER_INVALID:{entry_id}")
        planned_at = _utc(row.get("planned_at"), f"PLANNED_AT_INVALID:{trade_id}")
        if planned_at > _utc(intent["submitted_at"], f"ENTRY_SUBMITTED_AT_INVALID:{trade_id}"):
            raise CryptoPaperValidationError(f"PLANNED_LOSS_AFTER_ENTRY:{trade_id}")
        if (
            row.get("source_plan_ref") != intent["source_plan_ref"]
            or row.get("source_plan_sha256") != intent["source_plan_sha256"]
        ):
            raise CryptoPaperValidationError(f"PLANNED_LOSS_LINEAGE_MISMATCH:{trade_id}")
        planned_loss = _decimal(row.get("planned_loss"), f"PLANNED_LOSS_INVALID:{trade_id}")
        if planned_loss < 0:
            raise CryptoPaperValidationError(f"PLANNED_LOSS_NEGATIVE:{trade_id}")
        entry_fills = fill_by_order.get(entry_id, [])
        if not entry_fills:
            results.append({
                "trade_id": trade_id, "status": "NOT_FILLED", "planned_loss": row["planned_loss"],
                "realized_loss": None, "realized_minus_planned_loss": None,
                "mfe_pct": None, "mae_pct": None,
            })
            continue
        entry_qty = sum((Decimal(item["filled_quantity"]) for item in entry_fills), Decimal("0"))
        entry_gross = sum((Decimal(item["gross_value"]) for item in entry_fills), Decimal("0"))
        entry_fee = sum((Decimal(item["fee_amount"]) for item in entry_fills), Decimal("0"))
        entry_price = entry_gross / entry_qty
        entry_at = min(item["event_at"] for item in entry_fills)
        exit_ids = row.get("exit_order_ids")
        if not isinstance(exit_ids, list) or len(exit_ids) != len(set(exit_ids)):
            raise CryptoPaperValidationError(f"EXIT_ORDER_IDS_INVALID:{trade_id}")
        exit_fills = []
        for exit_id in exit_ids:
            if exit_id in used_exit_ids:
                raise CryptoPaperValidationError(f"EXIT_ORDER_REUSED:{exit_id}")
            used_exit_ids.add(exit_id)
            exit_intent = intents.get(exit_id)
            if exit_intent is None or exit_intent["side"] != "SELL" or exit_intent["market"] != intent["market"]:
                raise CryptoPaperValidationError(f"EXIT_ORDER_INVALID:{trade_id}:{exit_id}")
            exit_fills.extend(fill_by_order.get(exit_id, []))
        sold_qty = sum((Decimal(item["filled_quantity"]) for item in exit_fills), Decimal("0"))
        if sold_qty > entry_qty:
            raise CryptoPaperValidationError(f"EXIT_EXCEEDS_ENTRY:{trade_id}")
        exit_net = sum((
            Decimal(item["gross_value"]) - Decimal(item["fee_amount"]) for item in exit_fills
        ), Decimal("0"))
        basis = (entry_gross + entry_fee) * sold_qty / entry_qty if sold_qty else Decimal("0")
        realized_pnl = exit_net - basis
        realized_loss = max(-realized_pnl, Decimal("0"))
        exit_at = max((item["event_at"] for item in exit_fills), default=None)
        window_end = _utc(exit_at, "EXIT_AT_INVALID") if exit_at else generated_at
        prices = [Decimal(point["price"]) for point in marks if (
            point["market"] == intent["market"]
            and _utc(point["observed_at"], "MARK_OBSERVED_AT_INVALID") >= _utc(entry_at, "ENTRY_AT_INVALID")
            and _utc(point["available_at"], "MARK_AVAILABLE_AT_INVALID") <= window_end
        )]
        excursions = [Decimal("0")] + [
            (price - entry_price) / entry_price * Decimal("100") for price in prices
        ]
        status = "CLOSED" if sold_qty == entry_qty else "OPEN_OR_PARTIAL"
        results.append({
            "trade_id": trade_id,
            "status": status,
            "entry_order_id": entry_id,
            "entry_price": _format_decimal(entry_price),
            "entry_quantity": _format_decimal(entry_qty),
            "sold_quantity": _format_decimal(sold_qty),
            "planned_loss": _format_decimal(planned_loss),
            "realized_loss": _format_decimal(realized_loss) if sold_qty else None,
            "realized_minus_planned_loss": (
                _format_decimal(realized_loss - planned_loss) if sold_qty else None
            ),
            "mfe_pct": _pct(max(excursions)),
            "mae_pct": _pct(min(excursions)),
            "mark_observation_count": len(prices),
        })
    planned_total = sum((
        Decimal(row["planned_loss"]) for row in results if row["status"] != "NOT_FILLED"
    ), Decimal("0"))
    realized_rows = [Decimal(row["realized_loss"]) for row in results if row["realized_loss"] is not None]
    realized_total = sum(realized_rows, Decimal("0"))
    return {
        "trades": results,
        "planned_loss_total": _format_decimal(planned_total),
        "realized_loss_total": _format_decimal(realized_total) if realized_rows else None,
        "realized_minus_planned_total": (
            _format_decimal(realized_total - planned_total) if realized_rows else None
        ),
    }


def _error_metrics(rows, *, generated_at: dt.datetime, contract: dict) -> dict:
    if not isinstance(rows, list):
        raise CryptoPaperValidationError("ERROR_ASSESSMENTS_NOT_LIST")
    counts = {
        metric: {"PRESENT": 0, "ABSENT": 0, "UNVERIFIED": 0}
        for metric in contract["error_metric_types"]
    }
    seen = set()
    for index, row in enumerate(rows):
        fields = {
            "assessment_id", "metric_type", "status", "assessed_at",
            "evidence_ref", "evidence_sha256",
        }
        if not isinstance(row, dict) or set(row) != fields:
            raise CryptoPaperValidationError(f"ERROR_ASSESSMENT_FIELDS_MISMATCH:{index}")
        assessment_id = _token(row.get("assessment_id"), f"ASSESSMENT_ID_INVALID:{index}")
        if assessment_id in seen:
            raise CryptoPaperValidationError(f"ASSESSMENT_ID_DUPLICATE:{assessment_id}")
        seen.add(assessment_id)
        metric = row.get("metric_type")
        status = row.get("status")
        if metric not in counts or status not in contract["metric_statuses"]:
            raise CryptoPaperValidationError(f"ASSESSMENT_CLASS_INVALID:{index}")
        if _utc(row.get("assessed_at"), f"ASSESSMENT_TIME_INVALID:{index}") > generated_at:
            raise CryptoPaperValidationError(f"ASSESSMENT_FROM_FUTURE:{index}")
        if status == "UNVERIFIED":
            if row.get("evidence_ref") is not None or row.get("evidence_sha256") is not None:
                raise CryptoPaperValidationError(f"UNVERIFIED_ASSESSMENT_HAS_EVIDENCE:{index}")
        else:
            _text(row.get("evidence_ref"), f"ASSESSMENT_EVIDENCE_REF_INVALID:{index}")
            _sha(row.get("evidence_sha256"), f"ASSESSMENT_EVIDENCE_SHA_INVALID:{index}")
        counts[metric][status] += 1
    result = {}
    for metric, metric_counts in counts.items():
        denominator = metric_counts["PRESENT"] + metric_counts["ABSENT"]
        rate = None if denominator == 0 else _pct(
            Decimal(metric_counts["PRESENT"]) / Decimal(denominator)
        )
        result[metric.lower()] = {**metric_counts, "verified_rate": rate}
    return result


def _input_unsigned(batch: dict) -> dict:
    value = copy.deepcopy(batch)
    value.pop("packet_sha256", None)
    return value


def build_daily_report(batch: dict, contract: dict | None = None) -> dict:
    """Build one deterministic daily report without changing any authority."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "report_id", "report_date", "generated_at",
        "sample_origin", "previous_report_sha256", "source_artifacts", "authority",
        "packet_sha256",
    }
    if not isinstance(batch, dict) or set(batch) != fields:
        raise CryptoPaperValidationError("DAILY_INPUT_FIELDS_MISMATCH")
    if (
        batch.get("schema_version") != contract["input_schema_version"]
        or batch.get("contract_version") != contract["contract_version"]
        or batch.get("sample_origin") not in contract["sample_origins"]
        or batch.get("authority") != contract["authority"]
    ):
        raise CryptoPaperValidationError("DAILY_INPUT_IDENTITY_INVALID")
    _token(batch.get("report_id"), "REPORT_ID_INVALID")
    report_date = _date(batch.get("report_date"), "REPORT_DATE_INVALID")
    generated_at = _utc(batch.get("generated_at"), "GENERATED_AT_INVALID")
    prior = batch.get("previous_report_sha256")
    if prior is not None:
        _sha(prior, "PREVIOUS_REPORT_SHA_INVALID")
    digest = _sha(batch.get("packet_sha256"), "DAILY_INPUT_SHA_INVALID")
    if payload_sha256(_input_unsigned(batch)) != digest:
        raise CryptoPaperValidationError("DAILY_INPUT_SHA_MISMATCH")
    artifacts = _artifact_map(batch, generated_at, contract)
    ledger = SIMULATOR.validate_ledger(artifacts["LEDGER"]["payload"])
    account = SIMULATOR.validate_account_state(artifacts["ACCOUNT_STATE"]["payload"])
    if account["source"]["ledger_sha256"] != ledger["packet_sha256"]:
        raise CryptoPaperValidationError("ACCOUNT_LEDGER_EXACT_LINEAGE_MISMATCH")
    if _utc(account["observed_at"], "ACCOUNT_OBSERVED_AT_INVALID") > generated_at:
        raise CryptoPaperValidationError("ACCOUNT_STATE_FROM_FUTURE")
    nav = _validate_time_series(
        artifacts["NAV_SERIES"]["payload"], role="NAV_SERIES",
        generated_at=generated_at, value_field="total_nav",
    )
    marks = _validate_time_series(
        artifacts["MARK_SERIES"]["payload"], role="MARK_SERIES",
        generated_at=generated_at, value_field="price",
    )
    if (
        nav[-1]["observed_at"] != account["observed_at"]
        or nav[-1]["total_nav"] != account["total_nav"]
    ):
        raise CryptoPaperValidationError("NAV_FINAL_ACCOUNT_STATE_MISMATCH")
    intents, fills = _ledger_index(ledger)
    planned = _planned_loss_metrics(
        artifacts["PLANNED_LOSS"]["payload"], intents=intents, fills=fills,
        marks=marks, generated_at=generated_at,
    )
    errors = _error_metrics(
        artifacts["ERROR_ASSESSMENT"]["payload"], generated_at=generated_at,
        contract=contract,
    )
    official = _official_status(
        artifacts["D0_GATE"], origin=batch["sample_origin"], report_date=report_date,
        generated_at=generated_at, contract=contract,
    )
    report = {
        "schema_version": contract["daily_report_schema_version"],
        "contract_version": contract["contract_version"],
        "report_id": batch["report_id"],
        "report_date": batch["report_date"],
        "generated_at": batch["generated_at"],
        "sample_origin": batch["sample_origin"],
        "previous_report_sha256": prior,
        "source_input_sha256": digest,
        "source_lineage": [
            {
                "role": role,
                "origin": artifacts[role]["origin"],
                "source_ref": artifacts[role]["source_ref"],
                "available_at": artifacts[role]["available_at"],
                "payload_sha256": artifacts[role]["payload_sha256"],
            }
            for role in contract["artifact_roles"]
        ],
        "source_input": copy.deepcopy(batch),
        "official_validation": official,
        "metrics": {
            "pnl_and_no_trade": _pnl_metrics(ledger, account),
            "max_drawdown": _drawdown_metrics(nav),
            "planned_vs_realized_and_excursions": planned,
            "errors": errors,
            "ledger_integrity": {
                "ledger_id": ledger["ledger_id"],
                "ledger_sha256": ledger["packet_sha256"],
                "event_count": len(ledger["events"]),
                "accepted_duplicate_event_count": 0,
                "exact_account_lineage_verified": True,
            },
        },
        "decision": {
            "status": "DIAGNOSTIC_ONLY",
            "wbs_p10_12_promoted": False,
            "live_review_opened": False,
            "live_authorized": False,
            "reason": "DAILY_METRICS_CANNOT_PROMOTE_POLICY_OR_TRADING_AUTHORITY",
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    return _with_sha(report)


def validate_daily_report(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "report_id", "report_date", "generated_at",
        "sample_origin", "previous_report_sha256", "source_input_sha256", "source_lineage",
        "source_input",
        "official_validation", "metrics", "decision", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperValidationError("DAILY_REPORT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["daily_report_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("sample_origin") not in contract["sample_origins"]
        or value.get("authority") != contract["authority"]
    ):
        raise CryptoPaperValidationError("DAILY_REPORT_IDENTITY_INVALID")
    _token(value.get("report_id"), "REPORT_ID_INVALID")
    _date(value.get("report_date"), "REPORT_DATE_INVALID")
    _utc(value.get("generated_at"), "GENERATED_AT_INVALID")
    _sha(value.get("source_input_sha256"), "SOURCE_INPUT_SHA_INVALID")
    if value.get("previous_report_sha256") is not None:
        _sha(value["previous_report_sha256"], "PREVIOUS_REPORT_SHA_INVALID")
    if value.get("decision") != {
        "status": "DIAGNOSTIC_ONLY",
        "wbs_p10_12_promoted": False,
        "live_review_opened": False,
        "live_authorized": False,
        "reason": "DAILY_METRICS_CANNOT_PROMOTE_POLICY_OR_TRADING_AUTHORITY",
    }:
        raise CryptoPaperValidationError("DAILY_REPORT_DECISION_INVALID")
    official = value.get("official_validation")
    if not isinstance(official, dict) or official.get("countable") not in (True, False):
        raise CryptoPaperValidationError("DAILY_REPORT_OFFICIAL_STATUS_INVALID")
    if value["sample_origin"] != "NATURAL_AUTOMATED" and official["countable"]:
        raise CryptoPaperValidationError("NON_NATURAL_REPORT_COUNTABLE")
    digest = _sha(value.get("packet_sha256"), "DAILY_REPORT_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperValidationError("DAILY_REPORT_SHA_MISMATCH")
    source_input = value.get("source_input")
    if not isinstance(source_input, dict):
        raise CryptoPaperValidationError("DAILY_REPORT_SOURCE_INPUT_INVALID")
    if source_input.get("packet_sha256") != value["source_input_sha256"]:
        raise CryptoPaperValidationError("DAILY_REPORT_SOURCE_INPUT_SHA_MISMATCH")
    expected = build_daily_report(source_input, contract)
    if value != expected:
        raise CryptoPaperValidationError("DAILY_REPORT_DERIVATION_MISMATCH")
    return copy.deepcopy(value)


def build_cio_review(reports: list[dict], *, review_id: str, generated_at: str,
                     contract: dict | None = None) -> dict:
    """Aggregate reports; only an exact 30-day natural chain is CIO-review ready."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    _token(review_id, "REVIEW_ID_INVALID")
    generated = _utc(generated_at, "REVIEW_GENERATED_AT_INVALID")
    if not isinstance(reports, list) or not reports:
        raise CryptoPaperValidationError("REVIEW_REPORTS_EMPTY")
    checked = [validate_daily_report(row, contract) for row in reports]
    checked.sort(key=lambda row: row["report_date"])
    if len({row["report_date"] for row in checked}) != len(checked):
        raise CryptoPaperValidationError("REVIEW_DATE_DUPLICATE")
    for index, row in enumerate(checked):
        if _utc(row["generated_at"], "REPORT_GENERATED_AT_INVALID") > generated:
            raise CryptoPaperValidationError("REVIEW_CONTAINS_FUTURE_REPORT")
        if index and row["previous_report_sha256"] != checked[index - 1]["packet_sha256"]:
            raise CryptoPaperValidationError("REVIEW_REPORT_CHAIN_BROKEN")
        if index:
            previous_ledger = next(
                artifact["payload"] for artifact in checked[index - 1]["source_input"]["source_artifacts"]
                if artifact["role"] == "LEDGER"
            )
            current_ledger = next(
                artifact["payload"] for artifact in row["source_input"]["source_artifacts"]
                if artifact["role"] == "LEDGER"
            )
            if (
                current_ledger["ledger_id"] != previous_ledger["ledger_id"]
                or current_ledger["events"][:len(previous_ledger["events"])]
                != previous_ledger["events"]
            ):
                raise CryptoPaperValidationError("REVIEW_LEDGER_CHAIN_NOT_APPEND_ONLY")
    countable = [row for row in checked if row["official_validation"]["countable"]]
    consecutive = True
    for left, right in zip(countable, countable[1:]):
        if (_date(right["report_date"], "REPORT_DATE_INVALID") - _date(
            left["report_date"], "REPORT_DATE_INVALID"
        )).days != 1:
            consecutive = False
    same_gate = len({
        row["official_validation"]["gate_source_sha256"] for row in countable
    }) <= 1
    exactly_30 = len(countable) == 30 and len(checked) == 30
    day_numbers = [row["official_validation"]["day_number"] for row in countable]
    ready = exactly_30 and consecutive and same_gate and day_numbers == list(range(1, 31)) and all(
        row["sample_origin"] == "NATURAL_AUTOMATED" for row in countable
    )
    first_initial = Decimal(checked[0]["metrics"]["pnl_and_no_trade"]["initial_cash"])
    last_nav = Decimal(checked[-1]["metrics"]["pnl_and_no_trade"]["final_nav"])
    period_net = last_nav - first_initial
    review = {
        "schema_version": contract["cio_review_schema_version"],
        "contract_version": contract["contract_version"],
        "review_id": review_id,
        "generated_at": generated_at,
        "report_count": len(checked),
        "official_countable_day_count": len(countable),
        "official_chain_exactly_30_consecutive_days": ready,
        "status": (
            "READY_FOR_CIO_REVIEW_NOT_LIVE_AUTHORIZED" if ready
            else "PREVIEW_OR_INCOMPLETE_NOT_OFFICIAL"
        ),
        "report_sha256s": [row["packet_sha256"] for row in checked],
        "source_reports": copy.deepcopy(checked),
        "aggregate": {
            "period_net_pnl_vs_no_trade": _format_decimal(period_net),
            "max_daily_report_drawdown_pct": min(
                Decimal(row["metrics"]["max_drawdown"]["max_drawdown_pct"])
                for row in checked
            ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN).to_eng_string(),
            "error_present_counts": {
                metric.lower(): sum(
                    row["metrics"]["errors"][metric.lower()]["PRESENT"] for row in checked
                ) for metric in contract["error_metric_types"]
            },
        },
        "decision": {
            "cio_review_required": ready,
            "live_authorized": False,
            "automatic_transition_forbidden": True,
            "wbs_p10_12_completion_authorized": False,
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    return _with_sha(review)


def validate_cio_review(value: dict, contract: dict | None = None) -> dict:
    """Independently rederive a CIO packet from its exact embedded reports."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "review_id", "generated_at",
        "report_count", "official_countable_day_count",
        "official_chain_exactly_30_consecutive_days", "status", "report_sha256s",
        "source_reports", "aggregate", "decision", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperValidationError("CIO_REVIEW_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["cio_review_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["authority"]
    ):
        raise CryptoPaperValidationError("CIO_REVIEW_IDENTITY_INVALID")
    digest = _sha(value.get("packet_sha256"), "CIO_REVIEW_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperValidationError("CIO_REVIEW_SHA_MISMATCH")
    expected = build_cio_review(
        value.get("source_reports"), review_id=value.get("review_id"),
        generated_at=value.get("generated_at"), contract=contract,
    )
    if value != expected:
        raise CryptoPaperValidationError("CIO_REVIEW_DERIVATION_MISMATCH")
    return copy.deepcopy(value)


def persist_daily_report(root: Path, report: dict, contract: dict | None = None) -> tuple[Path, bool]:
    """Persist one immutable report outside Git, enforcing exact predecessor."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked = validate_daily_report(report, contract)
    root = Path(root)
    resolved = root.resolve(strict=False)
    repo = ROOT.resolve()
    if resolved == repo or repo in resolved.parents:
        raise CryptoPaperValidationError(f"TRACKED_REPORT_OUTPUT_FORBIDDEN:{root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise CryptoPaperValidationError("REPORT_ROOT_SYMLINK_FORBIDDEN")
    os.chmod(root, 0o700)
    origin_root = root / checked["sample_origin"]
    origin_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(origin_root, 0o700)
    existing = sorted(origin_root.glob("daily/*/*.json"))
    prior_reports = [validate_daily_report(_read_json(path), contract) for path in existing]
    if prior_reports:
        latest = max(prior_reports, key=lambda row: (row["report_date"], row["generated_at"]))
        if checked["packet_sha256"] == latest["packet_sha256"]:
            return next(path for path in existing if path.stem == checked["packet_sha256"]), False
        if checked["previous_report_sha256"] != latest["packet_sha256"]:
            raise CryptoPaperValidationError("APPEND_PREDECESSOR_MISMATCH")
        if checked["report_date"] <= latest["report_date"]:
            raise CryptoPaperValidationError("APPEND_DATE_NOT_INCREASING")
    elif checked["previous_report_sha256"] is not None:
        raise CryptoPaperValidationError("GENESIS_REPORT_HAS_PREDECESSOR")
    directory = origin_root / "daily" / checked["report_date"]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    target = directory / f"{checked['packet_sha256']}.json"
    data = (canonical_json(checked) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o600)
    except FileExistsError:
        if target.read_bytes() != data:
            raise CryptoPaperValidationError("REPORT_PATH_CONTENT_MISMATCH")
        return target, False
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return target, True


def _write_json(path: Path, value: dict) -> None:
    path = Path(path)
    resolved = path.resolve(strict=False)
    repo = ROOT.resolve()
    if resolved == repo or repo in resolved.parents:
        raise CryptoPaperValidationError(f"TRACKED_REPORT_OUTPUT_FORBIDDEN:{path}")
    if path.is_symlink():
        raise CryptoPaperValidationError(f"REPORT_OUTPUT_SYMLINK_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise CryptoPaperValidationError(f"REPORT_OUTPUT_PARENT_SYMLINK_FORBIDDEN:{path.parent}")
    os.chmod(path.parent, 0o700)
    data = (canonical_json(value) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise CryptoPaperValidationError(f"REPORT_OUTPUT_WRITE_FAILED:{path}:{exc}") from exc
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    daily = sub.add_parser("daily")
    daily.add_argument("--input", type=Path, required=True)
    daily.add_argument("--output", type=Path, required=True)
    append = sub.add_parser("append-daily")
    append.add_argument("--input", type=Path, required=True)
    append.add_argument("--state-root", type=Path, required=True)
    review = sub.add_parser("review")
    review.add_argument("--reports", type=Path, nargs="+", required=True)
    review.add_argument("--review-id", required=True)
    review.add_argument("--generated-at", required=True)
    review.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "daily":
        _write_json(args.output, build_daily_report(_read_json(args.input)))
    elif args.command == "append-daily":
        path, created = persist_daily_report(args.state_root, _read_json(args.input))
        print(canonical_json({"path": str(path), "created": created}))
    else:
        reports = [_read_json(path) for path in args.reports]
        _write_json(args.output, build_cio_review(
            reports, review_id=args.review_id, generated_at=args.generated_at,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
