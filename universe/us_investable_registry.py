#!/usr/bin/env python3
"""Fail-closed US investable-universe evidence boundary.

This module performs no network, broker, OAuth, or order operation.  It turns
caller-supplied, point-in-time listing facts into a redacted eligibility
assessment.  Nasdaq Symbol Directory coverage alone is deliberately
insufficient: every fact that the free directory does not prove must arrive
with its own source and availability lineage.
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
CONTRACT_PATH = ROOT / "config" / "us_investable_registry_contract.json"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{1,127}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.\d+)?$")


class UsInvestableRegistryError(ValueError):
    """A contract or semantic fact is invalid and must fail closed."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsInvestableRegistryError(f"JSON_READ_FAILED:{path}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise UsInvestableRegistryError("CONTRACT_NOT_OBJECT")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "us_investable_registry/1"
        or value.get("market") != "US"
        or value.get("source_coverage_is_investability") is not False
        or value.get("otc_policy") != "ALWAYS_EXCLUDE"
    ):
        raise UsInvestableRegistryError("CONTRACT_IDENTITY_INVALID")
    if value.get("liquidity", {}).get("repository_default_policy") != "ABSENT":
        raise UsInvestableRegistryError("CONTRACT_LIQUIDITY_DEFAULT_OPEN")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("universe_observation_only") is not True:
        raise UsInvestableRegistryError("CONTRACT_AUTHORITY_INVALID")
    if any(v is not False for k, v in authority.items() if k != "universe_observation_only"):
        raise UsInvestableRegistryError("CONTRACT_AUTHORITY_OPEN")
    return copy.deepcopy(value)


def _instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise UsInvestableRegistryError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsInvestableRegistryError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UsInvestableRegistryError(code)
    return parsed


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise UsInvestableRegistryError(code)
    return value


def _token(value: object, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise UsInvestableRegistryError(code)
    return value


def _decimal(value: object, code: str) -> Decimal:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise UsInvestableRegistryError(code)
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise UsInvestableRegistryError(code) from exc


def _digest(value: dict, field: str, code: str) -> str:
    digest = _sha(value.get(field), code)
    body = copy.deepcopy(value)
    body.pop(field)
    if payload_sha256(body) != digest:
        raise UsInvestableRegistryError(f"{code}_MISMATCH")
    return digest


def _source_coverage(value: object, decision_at: dt.datetime) -> dict:
    fields = {
        "snapshot_date", "source_id", "source_ref", "source_sha256",
        "observed_at", "available_at", "coverage_scope",
        "redistribution_status",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsInvestableRegistryError("SOURCE_COVERAGE_FIELDS_INVALID")
    if value["coverage_scope"] != "CURRENT_FORWARD_ONLY":
        raise UsInvestableRegistryError("SOURCE_COVERAGE_SCOPE_INVALID")
    if value["redistribution_status"] not in {"GRANTED", "NOT_GRANTED", "UNKNOWN"}:
        raise UsInvestableRegistryError("SOURCE_REDISTRIBUTION_STATUS_INVALID")
    observed = _instant(value["observed_at"], "SOURCE_OBSERVED_AT_INVALID")
    available = _instant(value["available_at"], "SOURCE_AVAILABLE_AT_INVALID")
    if observed > available or available > decision_at:
        raise UsInvestableRegistryError("SOURCE_TIME_ORDER_INVALID")
    try:
        snapshot_date = dt.date.fromisoformat(value["snapshot_date"])
    except (TypeError, ValueError) as exc:
        raise UsInvestableRegistryError("SOURCE_SNAPSHOT_DATE_INVALID") from exc
    if snapshot_date > observed.date():
        raise UsInvestableRegistryError("SOURCE_DATE_AFTER_OBSERVATION")
    _token(value["source_id"], "SOURCE_ID_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"].strip():
        raise UsInvestableRegistryError("SOURCE_REF_INVALID")
    _sha(value["source_sha256"], "SOURCE_SHA_INVALID")
    return copy.deepcopy(value)


def _fact(value: object, decision_at: dt.datetime, name: str) -> dict:
    fields = {"status", "observed_at", "available_at", "source_ref", "source_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise UsInvestableRegistryError(f"{name}_FACT_FIELDS_INVALID")
    observed = _instant(value["observed_at"], f"{name}_OBSERVED_AT_INVALID")
    available = _instant(value["available_at"], f"{name}_AVAILABLE_AT_INVALID")
    if observed > available or available > decision_at:
        raise UsInvestableRegistryError(f"{name}_TIME_ORDER_INVALID")
    if not isinstance(value["status"], str) or not value["status"]:
        raise UsInvestableRegistryError(f"{name}_STATUS_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"].strip():
        raise UsInvestableRegistryError(f"{name}_SOURCE_REF_INVALID")
    _sha(value["source_sha256"], f"{name}_SOURCE_SHA_INVALID")
    return copy.deepcopy(value)


def _type_evidence(value: object, decision_at: dt.datetime) -> dict:
    fields = {
        "status", "source_kind", "observed_at", "available_at",
        "source_ref", "source_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsInvestableRegistryError("TYPE_EVIDENCE_FIELDS_INVALID")
    fact = _fact({k: value[k] for k in fields if k != "source_kind"}, decision_at, "TYPE")
    if value["source_kind"] not in {"NASDAQ_SYMBOL_DIRECTORY", "OFFICIAL_SECURITY_MASTER"}:
        raise UsInvestableRegistryError("TYPE_SOURCE_KIND_INVALID")
    fact["source_kind"] = value["source_kind"]
    return fact


def _liquidity_policy(value: object, decision_at: dt.datetime) -> dict:
    fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at", "effective_from", "effective_to",
        "min_median_daily_dollar_volume", "min_median_daily_trade_count",
        "max_median_spread_bps", "min_observed_session_count", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsInvestableRegistryError("LIQUIDITY_POLICY_FIELDS_INVALID")
    if value["schema_version"] != "us_liquidity_policy/1" or value["approval_status"] != "RATIFIED":
        raise UsInvestableRegistryError("LIQUIDITY_POLICY_NOT_RATIFIED")
    ratified = _instant(value["ratified_at"], "LIQUIDITY_RATIFIED_AT_INVALID")
    start = _instant(value["effective_from"], "LIQUIDITY_EFFECTIVE_FROM_INVALID")
    end = _instant(value["effective_to"], "LIQUIDITY_EFFECTIVE_TO_INVALID")
    if ratified > start or not start <= decision_at < end:
        raise UsInvestableRegistryError("LIQUIDITY_POLICY_NOT_EFFECTIVE")
    _token(value["policy_id"], "LIQUIDITY_POLICY_ID_INVALID")
    if not isinstance(value["ratified_by"], str) or not value["ratified_by"].strip():
        raise UsInvestableRegistryError("LIQUIDITY_RATIFIED_BY_INVALID")
    for field in (
        "min_median_daily_dollar_volume", "min_median_daily_trade_count",
        "max_median_spread_bps",
    ):
        _decimal(value[field], f"LIQUIDITY_POLICY_VALUE_INVALID:{field}")
    if type(value["min_observed_session_count"]) is not int or value["min_observed_session_count"] < 1:
        raise UsInvestableRegistryError("LIQUIDITY_SESSION_THRESHOLD_INVALID")
    _digest(value, "packet_sha256", "LIQUIDITY_POLICY_SHA_INVALID")
    return copy.deepcopy(value)


def _liquidity(value: object, decision_at: dt.datetime, policy: dict) -> str:
    fields = {
        "window_end", "observed_at", "available_at", "source_ref", "source_sha256",
        "median_daily_dollar_volume", "median_daily_trade_count",
        "median_spread_bps", "observed_session_count",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsInvestableRegistryError("LIQUIDITY_FIELDS_INVALID")
    observed = _instant(value["observed_at"], "LIQUIDITY_OBSERVED_AT_INVALID")
    available = _instant(value["available_at"], "LIQUIDITY_AVAILABLE_AT_INVALID")
    if observed > available or available > decision_at:
        raise UsInvestableRegistryError("LIQUIDITY_TIME_ORDER_INVALID")
    try:
        window_end = dt.date.fromisoformat(value["window_end"])
    except (TypeError, ValueError) as exc:
        raise UsInvestableRegistryError("LIQUIDITY_WINDOW_END_INVALID") from exc
    if window_end > observed.date():
        raise UsInvestableRegistryError("LIQUIDITY_WINDOW_AFTER_OBSERVATION")
    _sha(value["source_sha256"], "LIQUIDITY_SOURCE_SHA_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"].strip():
        raise UsInvestableRegistryError("LIQUIDITY_SOURCE_REF_INVALID")
    metrics = {
        field: _decimal(value[field], f"LIQUIDITY_VALUE_INVALID:{field}")
        for field in (
            "median_daily_dollar_volume", "median_daily_trade_count", "median_spread_bps"
        )
    }
    sessions = value["observed_session_count"]
    if type(sessions) is not int or sessions < 0:
        raise UsInvestableRegistryError("LIQUIDITY_OBSERVED_SESSIONS_INVALID")
    passed = (
        metrics["median_daily_dollar_volume"] >= Decimal(policy["min_median_daily_dollar_volume"])
        and metrics["median_daily_trade_count"] >= Decimal(policy["min_median_daily_trade_count"])
        and metrics["median_spread_bps"] <= Decimal(policy["max_median_spread_bps"])
        and sessions >= policy["min_observed_session_count"]
    )
    return "PASS" if passed else "LOW_LIQUIDITY"


def _evaluate_record(value: object, decision_at: dt.datetime, policy: dict, contract: dict) -> dict:
    fields = {
        "asset_id", "symbol", "listing_venue", "instrument_type", "type_evidence",
        "etf_indicator", "test_issue", "financial_status", "listing", "trading_halt",
        "scheduled_delisting", "corporate_action_state", "liquidity",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsInvestableRegistryError("RECORD_FIELDS_INVALID")
    asset_id = _token(value["asset_id"], "ASSET_ID_INVALID")
    symbol = _token(value["symbol"], "SYMBOL_INVALID")
    reasons: list[str] = []
    venue = value["listing_venue"]
    if venue == "OTC" or venue not in contract["venue_scope"]:
        reasons.append("OTC_OR_UNSUPPORTED_VENUE")
    instrument_type = value["instrument_type"]
    if instrument_type not in contract["instrument_types"]:
        reasons.append("UNSUPPORTED_INSTRUMENT_TYPE")
    type_evidence = _type_evidence(value["type_evidence"], decision_at)
    if type_evidence["status"] != "CONFIRMED":
        reasons.append("SECURITY_TYPE_UNPROVEN")
    if instrument_type == "COMMON_STOCK":
        if type_evidence["source_kind"] != "OFFICIAL_SECURITY_MASTER":
            reasons.append("COMMON_STOCK_REQUIRES_SECURITY_MASTER")
        if value["etf_indicator"] != "N":
            reasons.append("COMMON_STOCK_ETF_FLAG_CONFLICT")
    elif instrument_type == "ETF":
        if value["etf_indicator"] != "Y":
            reasons.append("ETF_FLAG_NOT_CONFIRMED")
    elif value["etf_indicator"] not in {"Y", "N", "UNKNOWN"}:
        reasons.append("ETF_FLAG_INVALID")
    if value["test_issue"] is not False:
        reasons.append("TEST_ISSUE_OR_UNKNOWN")
    if value["financial_status"] != "NORMAL":
        reasons.append("FINANCIAL_STATUS_NOT_NORMAL")
    statuses = {
        "listing": (_fact(value["listing"], decision_at, "LISTING"), "ACTIVE", "LISTING_NOT_ACTIVE"),
        "trading_halt": (_fact(value["trading_halt"], decision_at, "HALT"), "NOT_HALTED", "HALT_OR_UNKNOWN"),
        "scheduled_delisting": (_fact(value["scheduled_delisting"], decision_at, "DELISTING"), "NONE_SCHEDULED", "DELISTING_OR_UNKNOWN"),
        "corporate_action_state": (_fact(value["corporate_action_state"], decision_at, "CORPORATE_ACTION"), "CLEAR", "CORPORATE_ACTION_UNRESOLVED"),
    }
    for fact, expected, reason in statuses.values():
        if fact["status"] != expected:
            reasons.append(reason)
    liquidity_status = _liquidity(value["liquidity"], decision_at, policy)
    if liquidity_status != "PASS":
        reasons.append(liquidity_status)
    reasons = sorted(set(reasons))
    return {
        "asset_id": asset_id,
        "symbol": symbol,
        "listing_venue": venue,
        "instrument_type": instrument_type,
        "eligibility": "ELIGIBLE_FOR_PAPER_DATA_REVIEW" if not reasons else "EXCLUDED_FAIL_CLOSED",
        "eligible": not reasons,
        "liquidity_status": liquidity_status,
        "reasons": reasons,
    }


def evaluate_registry(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    fields = {
        "schema_version", "decision_at", "source_coverage", "records",
        "liquidity_policy", "authority",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsInvestableRegistryError("PACKET_FIELDS_INVALID")
    if value["schema_version"] != "us_investable_snapshot/1":
        raise UsInvestableRegistryError("PACKET_SCHEMA_INVALID")
    if value["authority"] != contract["authority"]:
        raise UsInvestableRegistryError("PACKET_AUTHORITY_INVALID")
    decision_at = _instant(value["decision_at"], "DECISION_AT_INVALID")
    source = _source_coverage(value["source_coverage"], decision_at)
    policy = _liquidity_policy(value["liquidity_policy"], decision_at)
    if not isinstance(value["records"], list) or not value["records"]:
        raise UsInvestableRegistryError("RECORDS_NOT_LIST")
    rows = [_evaluate_record(row, decision_at, policy, contract) for row in value["records"]]
    asset_ids = [row["asset_id"] for row in rows]
    symbols = [row["symbol"] for row in rows]
    if len(asset_ids) != len(set(asset_ids)) or len(symbols) != len(set(symbols)):
        raise UsInvestableRegistryError("DUPLICATE_ASSET_OR_SYMBOL")
    rows.sort(key=lambda row: (row["listing_venue"], row["symbol"], row["asset_id"]))
    eligible = [row for row in rows if row["eligible"]]
    result = {
        "schema_version": "us_investable_registry_result/1",
        "contract_version": contract["contract_version"],
        "decision_at": value["decision_at"],
        "source_snapshot_date": source["snapshot_date"],
        "source_scope": source["coverage_scope"],
        "source_coverage_is_investability": False,
        "redistribution_status": source["redistribution_status"],
        "public_raw_retention_authorized": False,
        "liquidity_policy_id": policy["policy_id"],
        "liquidity_policy_sha256": policy["packet_sha256"],
        "total_count": len(rows),
        "eligible_count": len(eligible),
        "eligible_common_stock_count": sum(row["instrument_type"] == "COMMON_STOCK" for row in eligible),
        "eligible_etf_count": sum(row["instrument_type"] == "ETF" for row in eligible),
        "records": rows,
        "authority": copy.deepcopy(contract["authority"]),
    }
    result["packet_sha256"] = payload_sha256(result)
    return result


def validate_result(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    if not isinstance(value, dict):
        raise UsInvestableRegistryError("RESULT_NOT_OBJECT")
    if value.get("contract_version") != contract["contract_version"]:
        raise UsInvestableRegistryError("RESULT_CONTRACT_INVALID")
    _digest(value, "packet_sha256", "RESULT_SHA_INVALID")
    if value.get("authority") != contract["authority"]:
        raise UsInvestableRegistryError("RESULT_AUTHORITY_INVALID")
    rows = value.get("records")
    if not isinstance(rows, list):
        raise UsInvestableRegistryError("RESULT_RECORDS_INVALID")
    if value.get("eligible_count") != sum(row.get("eligible") is True for row in rows):
        raise UsInvestableRegistryError("RESULT_COUNT_MISMATCH")
    return copy.deepcopy(value)
