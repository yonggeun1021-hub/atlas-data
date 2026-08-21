#!/usr/bin/env python3
"""P9-05 externally ratified intraday risk escalation evaluator."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "intraday_risk_escalation_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


def _load_validator(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"UPSTREAM_VALIDATOR_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY_EXIT = _load_validator(
    "intraday_risk_entry_exit",
    ROOT / "execution" / "entry_exit_trigger_eligibility.py",
)
IMPORTANT_EVENT = _load_validator(
    "intraday_risk_important_event",
    ROOT / "execution" / "important_event_detector.py",
)
CONCENTRATION_GUARD = _load_validator(
    "intraday_risk_concentration_guard",
    ROOT / "portfolio" / "concentration_correlation_guard.py",
)
PLANNED_LOSS_BUDGET = _load_validator(
    "intraday_risk_planned_loss_budget",
    ROOT / "portfolio" / "planned_loss_budget.py",
)


class IntradayRiskEscalationError(ValueError):
    """Fail-closed P9-05 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntradayRiskEscalationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 3,
        "contract_version": "intraday_risk_escalation/3",
        "input_schema_version": "intraday_risk_observation_batch/3",
        "policy_schema_version": "intraday_risk_escalation_policy/3",
        "output_schema_version": "intraday_risk_escalation_packet/3",
        "entry_exit_schema_version": "entry_exit_trigger_eligibility_packet/1",
        "entry_exit_contract_version": "entry_exit_trigger_eligibility/1",
        "important_event_schema_version": "important_event_detection_packet/2",
        "important_event_contract_version": "important_event_detector/2",
        "concentration_guard_schema_version": "concentration_correlation_packet/2",
        "concentration_guard_contract_version": "concentration_correlation_guard/2",
        "planned_loss_schema_version": "planned_loss_packet/2",
        "planned_loss_contract_version": "planned_loss_budget/2",
        "validated_upstream_packets": [
            "ENTRY_EXIT_TRIGGER_ELIGIBILITY",
            "IMPORTANT_EVENT_DETECTION",
            "CONCENTRATION_GUARD",
            "PLANNED_LOSS_BUDGET",
        ],
        "lineage_only_upstreams": [],
        "markets": ["US", "KOREA", "CRYPTO"],
        "metrics": [
            "DRAWDOWN_FRACTION",
            "DOWN_GAP_FRACTION",
            "SPREAD_BPS",
            "RELATIVE_VOLUME_FRACTION",
        ],
        "risk_statuses": ["NORMAL", "ALERT"],
        "repository_default_policy": "ABSENT",
        "policy_requirement": "EXTERNAL_RATIFIED_POLICY_REQUIRED",
        "threshold_semantics": {
            "max_threshold": "GREATER_THAN_IS_ALERT",
            "min_threshold": "LESS_THAN_IS_ALERT",
        },
        "rounding_digits": 12,
        "input_authority": {
            "normalized_intraday_risk_observation_only": True,
            "risk_policy_authorized": False,
            "exposure_reduction_authorized": False,
            "stop_candidate_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "policy_authority": {
            "risk_threshold_policy_only": True,
            "exposure_reduction_authorized": False,
            "stop_candidate_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "intraday_risk_evaluation_only": True,
            "upstream_semantic_interpretation_authorized": False,
            "exposure_reduction_candidate_authorized": False,
            "stop_candidate_authorized": False,
            "action_generation_authorized": False,
            "position_sizing_authorized": False,
            "order_generation_authorized": False,
            "notification_authorized": False,
            "broker_submission_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise IntradayRiskEscalationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise IntradayRiskEscalationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise IntradayRiskEscalationError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise IntradayRiskEscalationError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise IntradayRiskEscalationError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    return value


def _decimal(value, code: str, *, positive: bool) -> Decimal:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise IntradayRiskEscalationError(code) from exc
    if (positive and parsed <= 0) or (not positive and parsed < 0):
        raise IntradayRiskEscalationError(code)
    return parsed


def _rounded(value: Decimal, digits: int) -> str:
    quantum = Decimal(1).scaleb(-digits)
    result = value.quantize(quantum, rounding=ROUND_HALF_UP)
    text = format(result, "f").rstrip("0").rstrip(".")
    return text or "0"


def _validate_policy(value: dict, observed: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_id", "status", "ratified_by",
        "ratified_at", "effective_from", "effective_to", "thresholds_by_market",
        "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskEscalationError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["policy_authority"]
    ):
        raise IntradayRiskEscalationError("POLICY_IDENTITY_INVALID")
    policy_id = _token(value.get("policy_id"), "POLICY_ID_INVALID")
    ratified = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    effective_from = _utc(value.get("effective_from"), "POLICY_EFFECTIVE_FROM_INVALID")
    effective_to = _utc(value.get("effective_to"), "POLICY_EFFECTIVE_TO_INVALID")
    if ratified > effective_from or effective_to <= effective_from:
        raise IntradayRiskEscalationError("POLICY_INTERVAL_INVALID")
    if not effective_from <= observed < effective_to:
        raise IntradayRiskEscalationError("POLICY_NOT_EFFECTIVE")
    thresholds = value.get("thresholds_by_market")
    if not isinstance(thresholds, dict) or set(thresholds) != set(contract["markets"]):
        raise IntradayRiskEscalationError("POLICY_MARKET_COVERAGE_INVALID")
    threshold_fields = {
        "max_drawdown_fraction", "max_down_gap_fraction", "max_spread_bps",
        "min_relative_volume_fraction", "policy_basis_ref", "policy_basis_sha256",
    }
    normalized_thresholds = {}
    for market in contract["markets"]:
        row = thresholds[market]
        if not isinstance(row, dict) or set(row) != threshold_fields:
            raise IntradayRiskEscalationError(f"POLICY_THRESHOLD_FIELDS_MISMATCH:{market}")
        min_relative = _decimal(
            row.get("min_relative_volume_fraction"),
            f"POLICY_MIN_RELATIVE_VOLUME_INVALID:{market}",
            positive=True,
        )
        if min_relative > 1:
            raise IntradayRiskEscalationError(f"POLICY_MIN_RELATIVE_VOLUME_INVALID:{market}")
        normalized_thresholds[market] = {
            "max_drawdown_fraction": row["max_drawdown_fraction"],
            "max_down_gap_fraction": row["max_down_gap_fraction"],
            "max_spread_bps": row["max_spread_bps"],
            "min_relative_volume_fraction": row["min_relative_volume_fraction"],
            "policy_basis_ref": _text(row.get("policy_basis_ref"), f"POLICY_BASIS_REF_INVALID:{market}"),
            "policy_basis_sha256": _sha(row.get("policy_basis_sha256"), f"POLICY_BASIS_SHA_INVALID:{market}"),
        }
        _decimal(row.get("max_drawdown_fraction"), f"POLICY_MAX_DRAWDOWN_INVALID:{market}", positive=True)
        _decimal(row.get("max_down_gap_fraction"), f"POLICY_MAX_GAP_INVALID:{market}", positive=True)
        _decimal(row.get("max_spread_bps"), f"POLICY_MAX_SPREAD_INVALID:{market}", positive=True)
    digest = _sha(value.get("packet_sha256"), "POLICY_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise IntradayRiskEscalationError("POLICY_SHA_MISMATCH")
    return {
        "policy_id": policy_id,
        "packet_sha256": digest,
        "thresholds_by_market": normalized_thresholds,
    }


def _validate_batch(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "observations", "upstream_lineage", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskEscalationError("BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise IntradayRiskEscalationError("BATCH_IDENTITY_INVALID")
    observed_at = value.get("observed_at")
    observed = _utc(observed_at, "BATCH_OBSERVED_AT_INVALID")
    upstream = value.get("upstream_lineage")
    upstream_fields = {
        "entry_exit_trigger_eligibility_packet_sha256",
        "important_event_detection_packet_sha256",
        "concentration_guard_packet_sha256",
        "planned_loss_budget_packet_sha256",
    }
    if not isinstance(upstream, dict) or set(upstream) != upstream_fields:
        raise IntradayRiskEscalationError("UPSTREAM_LINEAGE_FIELDS_MISMATCH")
    checked_upstream = {
        key: _sha(upstream.get(key), f"UPSTREAM_SHA_INVALID:{key}")
        for key in sorted(upstream)
    }
    raw = value.get("observations")
    if not isinstance(raw, list):
        raise IntradayRiskEscalationError("OBSERVATIONS_NOT_LIST")
    observation_fields = {
        "subject_id", "market", "reference_close", "open_price", "last_price",
        "bid_price", "ask_price", "cumulative_volume", "expected_volume_to_time",
        "provider_timestamp", "received_at", "source_ref", "source_sha256",
    }
    rows = []
    for index, row in enumerate(raw):
        context = f"observation:{index}"
        if not isinstance(row, dict) or set(row) != observation_fields:
            raise IntradayRiskEscalationError(f"OBSERVATION_FIELDS_MISMATCH:{context}")
        market = row.get("market")
        if market not in contract["markets"]:
            raise IntradayRiskEscalationError(f"OBSERVATION_MARKET_INVALID:{context}")
        provider = _utc(row.get("provider_timestamp"), f"PROVIDER_TIME_INVALID:{context}")
        received = _utc(row.get("received_at"), f"RECEIVED_AT_INVALID:{context}")
        if not provider <= received <= observed:
            raise IntradayRiskEscalationError(f"OBSERVATION_TIME_ORDER_INVALID:{context}")
        bid = _decimal(row.get("bid_price"), f"BID_PRICE_INVALID:{context}", positive=True)
        ask = _decimal(row.get("ask_price"), f"ASK_PRICE_INVALID:{context}", positive=True)
        if bid > ask:
            raise IntradayRiskEscalationError(f"CROSSED_QUOTE_INVALID:{context}")
        for field in ("reference_close", "open_price", "last_price", "expected_volume_to_time"):
            _decimal(row.get(field), f"{field.upper()}_INVALID:{context}", positive=True)
        _decimal(row.get("cumulative_volume"), f"CUMULATIVE_VOLUME_INVALID:{context}", positive=False)
        rows.append({
            "subject_id": _token(row.get("subject_id"), f"SUBJECT_ID_INVALID:{context}"),
            "market": market,
            "reference_close": row["reference_close"],
            "open_price": row["open_price"],
            "last_price": row["last_price"],
            "bid_price": row["bid_price"],
            "ask_price": row["ask_price"],
            "cumulative_volume": row["cumulative_volume"],
            "expected_volume_to_time": row["expected_volume_to_time"],
            "provider_timestamp": row["provider_timestamp"],
            "received_at": row["received_at"],
            "source_ref": _text(row.get("source_ref"), f"SOURCE_REF_INVALID:{context}"),
            "source_sha256": _sha(row.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"),
        })
    rows.sort(key=lambda row: (contract["markets"].index(row["market"]), row["subject_id"]))
    keys = [(row["market"], row["subject_id"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise IntradayRiskEscalationError("OBSERVATION_DUPLICATE")
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": _token(value.get("batch_id"), "BATCH_ID_INVALID"),
        "observed_at": observed_at,
        "observations": rows,
        "upstream_lineage": copy.deepcopy(value["upstream_lineage"]),
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "BATCH_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise IntradayRiskEscalationError("BATCH_SHA_MISMATCH")
    return {
        "normalized": normalized,
        "observed": observed,
        "packet_sha256": digest,
        "upstream_lineage": checked_upstream,
    }


def _metric(metric: str, observed: Decimal, threshold: Decimal, alert: bool, digits: int) -> dict:
    return {
        "metric": metric,
        "observed": _rounded(observed, digits),
        "threshold": _rounded(threshold, digits),
        "result": "ALERT" if alert else "PASS",
    }


def _validate_upstream_packets(
    entry_exit_value: dict,
    important_event_value: dict,
    concentration_value: dict,
    planned_loss_value: dict,
    batch: dict,
    contract: dict,
) -> tuple[dict, dict, dict, dict]:
    try:
        entry_exit = ENTRY_EXIT.validate_packet(entry_exit_value)
    except Exception as exc:
        raise IntradayRiskEscalationError(
            f"ENTRY_EXIT_PACKET_INVALID:{exc}"
        ) from exc
    try:
        important_event = IMPORTANT_EVENT.validate_packet(important_event_value)
    except Exception as exc:
        raise IntradayRiskEscalationError(
            f"IMPORTANT_EVENT_PACKET_INVALID:{exc}"
        ) from exc
    try:
        concentration = CONCENTRATION_GUARD.validate_packet(concentration_value)
    except Exception as exc:
        raise IntradayRiskEscalationError(
            f"CONCENTRATION_GUARD_PACKET_INVALID:{exc}"
        ) from exc
    try:
        planned_loss = PLANNED_LOSS_BUDGET.validate_packet(planned_loss_value)
    except Exception as exc:
        raise IntradayRiskEscalationError(
            f"PLANNED_LOSS_PACKET_INVALID:{exc}"
        ) from exc
    if (
        entry_exit.get("schema_version") != contract["entry_exit_schema_version"]
        or entry_exit.get("contract_version")
        != contract["entry_exit_contract_version"]
    ):
        raise IntradayRiskEscalationError("ENTRY_EXIT_PACKET_IDENTITY_INVALID")
    if (
        important_event.get("schema_version")
        != contract["important_event_schema_version"]
        or important_event.get("contract_version")
        != contract["important_event_contract_version"]
    ):
        raise IntradayRiskEscalationError("IMPORTANT_EVENT_PACKET_IDENTITY_INVALID")
    if (
        concentration.get("schema_version")
        != contract["concentration_guard_schema_version"]
        or concentration.get("contract_version")
        != contract["concentration_guard_contract_version"]
    ):
        raise IntradayRiskEscalationError("CONCENTRATION_GUARD_PACKET_IDENTITY_INVALID")
    if (
        planned_loss.get("schema_version") != contract["planned_loss_schema_version"]
        or planned_loss.get("contract_version")
        != contract["planned_loss_contract_version"]
    ):
        raise IntradayRiskEscalationError("PLANNED_LOSS_PACKET_IDENTITY_INVALID")
    upstream = batch["upstream_lineage"]
    if (
        upstream["entry_exit_trigger_eligibility_packet_sha256"]
        != entry_exit["packet_sha256"]
    ):
        raise IntradayRiskEscalationError("ENTRY_EXIT_PACKET_SHA_MISMATCH")
    if (
        upstream["important_event_detection_packet_sha256"]
        != important_event["packet_sha256"]
    ):
        raise IntradayRiskEscalationError("IMPORTANT_EVENT_PACKET_SHA_MISMATCH")
    if (
        upstream["concentration_guard_packet_sha256"]
        != concentration["packet_sha256"]
    ):
        raise IntradayRiskEscalationError("CONCENTRATION_GUARD_PACKET_SHA_MISMATCH")
    if (
        upstream["planned_loss_budget_packet_sha256"]
        != planned_loss["packet_sha256"]
    ):
        raise IntradayRiskEscalationError("PLANNED_LOSS_PACKET_SHA_MISMATCH")
    if (
        planned_loss["lineage"]["concentration_guard_packet_sha256"]
        != concentration["packet_sha256"]
    ):
        raise IntradayRiskEscalationError("PLANNED_LOSS_CONCENTRATION_LINEAGE_MISMATCH")
    observed = batch["observed"]
    entry_time = _utc(entry_exit["generated_at"], "ENTRY_EXIT_TIME_INVALID")
    event_time = _utc(important_event["detected_at"], "IMPORTANT_EVENT_TIME_INVALID")
    if entry_time > observed:
        raise IntradayRiskEscalationError("ENTRY_EXIT_PACKET_FROM_FUTURE")
    if event_time > observed:
        raise IntradayRiskEscalationError("IMPORTANT_EVENT_PACKET_FROM_FUTURE")
    concentration_time = _utc(
        concentration["source_packets"]["INPUT"]["generated_at_utc"],
        "CONCENTRATION_GUARD_TIME_INVALID",
    )
    planned_loss_time = _utc(
        planned_loss["source_packets"]["INPUT"]["generated_at_utc"],
        "PLANNED_LOSS_TIME_INVALID",
    )
    if concentration_time > observed:
        raise IntradayRiskEscalationError("CONCENTRATION_GUARD_PACKET_FROM_FUTURE")
    if planned_loss_time > observed:
        raise IntradayRiskEscalationError("PLANNED_LOSS_PACKET_FROM_FUTURE")
    if planned_loss_time < concentration_time:
        raise IntradayRiskEscalationError("PLANNED_LOSS_BEFORE_CONCENTRATION_GUARD")
    if entry_time.strftime("%Y-%m-%d") != observed.strftime("%Y-%m-%d"):
        raise IntradayRiskEscalationError("ENTRY_EXIT_BATCH_DATE_MISMATCH")
    observed_date = observed.strftime("%Y-%m-%d")
    if concentration["as_of_date"] != observed_date:
        raise IntradayRiskEscalationError("CONCENTRATION_GUARD_BATCH_DATE_MISMATCH")
    if planned_loss["as_of_date"] != observed_date:
        raise IntradayRiskEscalationError("PLANNED_LOSS_BATCH_DATE_MISMATCH")
    return entry_exit, important_event, concentration, planned_loss


def _evaluate_row(row: dict, thresholds: dict, contract: dict) -> dict:
    reference = Decimal(row["reference_close"])
    opened = Decimal(row["open_price"])
    last = Decimal(row["last_price"])
    bid = Decimal(row["bid_price"])
    ask = Decimal(row["ask_price"])
    cumulative = Decimal(row["cumulative_volume"])
    expected = Decimal(row["expected_volume_to_time"])
    drawdown = max(Decimal(0), (reference - last) / reference)
    down_gap = max(Decimal(0), (reference - opened) / reference)
    midpoint = (bid + ask) / Decimal(2)
    spread_bps = ((ask - bid) / midpoint) * Decimal(10000)
    relative_volume = cumulative / expected
    max_drawdown = Decimal(thresholds["max_drawdown_fraction"])
    max_gap = Decimal(thresholds["max_down_gap_fraction"])
    max_spread = Decimal(thresholds["max_spread_bps"])
    min_volume = Decimal(thresholds["min_relative_volume_fraction"])
    metrics = [
        _metric("DRAWDOWN_FRACTION", drawdown, max_drawdown, drawdown > max_drawdown, contract["rounding_digits"]),
        _metric("DOWN_GAP_FRACTION", down_gap, max_gap, down_gap > max_gap, contract["rounding_digits"]),
        _metric("SPREAD_BPS", spread_bps, max_spread, spread_bps > max_spread, contract["rounding_digits"]),
        _metric("RELATIVE_VOLUME_FRACTION", relative_volume, min_volume, relative_volume < min_volume, contract["rounding_digits"]),
    ]
    alert_reasons = [item["metric"] for item in metrics if item["result"] == "ALERT"]
    return {
        "subject_id": row["subject_id"],
        "market": row["market"],
        "risk_status": "ALERT" if alert_reasons else "NORMAL",
        "metrics": metrics,
        "alert_reasons": alert_reasons,
        "exposure_reduction_candidate": None,
        "stop_candidate": None,
        "action": None,
        "position_size": None,
        "order_intent": None,
        "source_observation": copy.deepcopy(row),
    }


def _assemble(
    batch: dict,
    policy: dict,
    entry_exit: dict,
    important_event: dict,
    concentration: dict,
    planned_loss: dict,
    contract: dict,
) -> dict:
    rows = [
        _evaluate_row(
            row,
            policy["thresholds_by_market"][row["market"]],
            contract,
        )
        for row in batch["normalized"]["observations"]
    ]
    metric_counts = {
        metric: sum(
            item["metric"] == metric and item["result"] == "ALERT"
            for row in rows
            for item in row["metrics"]
        )
        for metric in contract["metrics"]
    }
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "INTRADAY_RISK_EVALUATED_NO_ACTION_AUTHORITY",
        "batch_id": batch["normalized"]["batch_id"],
        "observed_at": batch["normalized"]["observed_at"],
        "policy_id": policy["policy_id"],
        "results": rows,
        "summary": {
            "subject_count": len(rows),
            "normal_count": sum(row["risk_status"] == "NORMAL" for row in rows),
            "alert_count": sum(row["risk_status"] == "ALERT" for row in rows),
            "alert_count_by_metric": metric_counts,
            "exposure_reduction_candidate_count": 0,
            "stop_candidate_count": 0,
            "action_count": 0,
            "order_count": 0,
        },
        "source_batch": copy.deepcopy(batch["normalized"]),
        "policy_packet": copy.deepcopy(policy["packet"]),
        "source_packets": {
            "ENTRY_EXIT_TRIGGER_ELIGIBILITY": copy.deepcopy(entry_exit),
            "IMPORTANT_EVENT_DETECTION": copy.deepcopy(important_event),
            "CONCENTRATION_GUARD": copy.deepcopy(concentration),
            "PLANNED_LOSS_BUDGET": copy.deepcopy(planned_loss),
        },
        "lineage": {
            "observation_batch_sha256": batch["packet_sha256"],
            "policy_sha256": policy["packet_sha256"],
            **copy.deepcopy(batch["upstream_lineage"]),
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "EXPOSURE_REDUCTION_POLICY_NOT_AUTHORIZED",
            "STOP_CANDIDATE_POLICY_NOT_AUTHORIZED",
            "NOTIFICATION_NOT_AUTHORIZED",
            "ACTION_ORDER_PRODUCTION_TRADING_NOT_AUTHORIZED",
        ],
    }


def build_packet(
    batch_value: dict,
    policy_value: dict,
    entry_exit_value: dict,
    important_event_value: dict,
    concentration_value: dict,
    planned_loss_value: dict,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    batch = _validate_batch(batch_value, contract)
    policy = _validate_policy(policy_value, batch["observed"], contract)
    policy["packet"] = copy.deepcopy(policy_value)
    entry_exit, important_event, concentration, planned_loss = _validate_upstream_packets(
        entry_exit_value,
        important_event_value,
        concentration_value,
        planned_loss_value,
        batch,
        contract,
    )
    packet = _assemble(
        batch, policy, entry_exit, important_event, concentration, planned_loss, contract
    )
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "batch_id", "observed_at",
        "policy_id", "results", "summary", "source_batch", "policy_packet",
        "source_packets", "lineage", "authority", "unresolved_boundaries",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise IntradayRiskEscalationError("OUTPUT_FIELDS_MISMATCH")
    batch_value = copy.deepcopy(packet.get("source_batch"))
    if not isinstance(batch_value, dict):
        raise IntradayRiskEscalationError("OUTPUT_SOURCE_BATCH_INVALID")
    batch_value["packet_sha256"] = packet.get("lineage", {}).get("observation_batch_sha256")
    batch = _validate_batch(batch_value, contract)
    policy_value = packet.get("policy_packet")
    policy = _validate_policy(policy_value, batch["observed"], contract)
    policy["packet"] = copy.deepcopy(policy_value)
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != set(
        contract["validated_upstream_packets"]
    ):
        raise IntradayRiskEscalationError("OUTPUT_SOURCE_PACKETS_INVALID")
    entry_exit, important_event, concentration, planned_loss = _validate_upstream_packets(
        sources["ENTRY_EXIT_TRIGGER_ELIGIBILITY"],
        sources["IMPORTANT_EVENT_DETECTION"],
        sources["CONCENTRATION_GUARD"],
        sources["PLANNED_LOSS_BUDGET"],
        batch,
        contract,
    )
    expected = _assemble(
        batch, policy, entry_exit, important_event, concentration, planned_loss, contract
    )
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_SHA_INVALID")
    if actual != expected:
        raise IntradayRiskEscalationError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise IntradayRiskEscalationError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise IntradayRiskEscalationError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(
    batch_path: Path,
    policy_path: Path,
    entry_exit_path: Path,
    important_event_path: Path,
    concentration_path: Path,
    planned_loss_path: Path,
    output_path: Path,
) -> int:
    try:
        write_json_atomic(
            output_path,
            build_packet(
                _read_json(batch_path),
                _read_json(policy_path),
                _read_json(entry_exit_path),
                _read_json(important_event_path),
                _read_json(concentration_path),
                _read_json(planned_loss_path),
            ),
        )
        return 0
    except (IntradayRiskEscalationError, OSError, TypeError, ValueError) as exc:
        print(f"Intraday risk escalation failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation_batch", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--entry-exit", type=Path, required=True)
    parser.add_argument("--important-event", type=Path, required=True)
    parser.add_argument("--concentration-guard", type=Path, required=True)
    parser.add_argument("--planned-loss", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(
        args.observation_batch,
        args.policy,
        args.entry_exit,
        args.important_event,
        args.concentration_guard,
        args.planned_loss,
        args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
