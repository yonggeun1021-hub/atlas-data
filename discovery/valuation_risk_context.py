#!/usr/bin/env python3
"""P3-10 policy-gated valuation and risk context attachment.

The module attaches exact two-point raw context to immutable Discovery Case
references.  Missing valuation or risk stays visible.  It labels deterioration
only when an external, effective, explicitly RATIFIED policy binds the exact
context identity and direction.  It never mutates, ranks or promotes a case.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "valuation_risk_context_contract.json"
INPUT_SCHEMA_VERSION = "valuation_risk_context_input/1"
OUTPUT_SCHEMA_VERSION = "valuation_risk_context_packet/3"
POLICY_SCHEMA_VERSION = "valuation_risk_interpretation_policy/1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
CASE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class ValuationRiskContextError(ValueError):
    """Fail-closed P3-10 context violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValuationRiskContextError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    metrics = {
        "CRYPTO": {
            "RISK": ["CURRENT_DRAWDOWN", "MAXIMUM_DRAWDOWN", "REALIZED_VOLATILITY"],
            "VALUATION": [],
        },
        "KOREA": {
            "RISK": ["CURRENT_DRAWDOWN", "MAXIMUM_DRAWDOWN", "REALIZED_VOLATILITY"],
            "VALUATION": ["BOOK_MULTIPLE", "EARNINGS_MULTIPLE", "FREE_CASH_FLOW_YIELD", "SALES_MULTIPLE"],
        },
        "US": {
            "RISK": ["CURRENT_DRAWDOWN", "MAXIMUM_DRAWDOWN", "REALIZED_VOLATILITY"],
            "VALUATION": ["BOOK_MULTIPLE", "EARNINGS_MULTIPLE", "FREE_CASH_FLOW_YIELD", "SALES_MULTIPLE"],
        },
    }
    requirements = {
        "CRYPTO": {"RISK": [["kraken_public_api"]], "VALUATION": []},
        "KOREA": {
            "RISK": [["krx_open_api_stock_daily"]],
            "VALUATION": [["dart_open_api"], ["krx_open_api_stock_daily"]],
        },
        "US": {
            "RISK": [["tiingo_us_daily_price"]],
            "VALUATION": [["sec_edgar"], ["tiingo_us_daily_price"]],
        },
    }
    return {
        "schema_version": 1,
        "contract_version": "valuation_risk_context/2",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "interpretation_policy_schema_version": POLICY_SCHEMA_VERSION,
        "allowed_case_schema_versions": [
            "business_acceleration_case/1", "discovery_case/1",
            "market_behavior_case/1", "supply_demand_case/1",
        ],
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "allowed_dimensions": ["RISK", "VALUATION"],
        "allowed_metric_types": metrics,
        "source_requirements": requirements,
        "source_hosts": {
            "dart_open_api": ["opendart.fss.or.kr"],
            "kraken_public_api": ["api.kraken.com"],
            "krx_open_api_stock_daily": ["data-dbg.krx.co.kr"],
            "sec_edgar": ["data.sec.gov", "www.sec.gov"],
            "tiingo_us_daily_price": ["api.tiingo.com"],
        },
        "required_point_count": 2,
        "feature_contract": {
            "change": "latest value minus prior value",
            "deterioration": "external policy direction times change is at least the external minimum",
        },
        "output_decimal_places": 12,
        "source_coverage": {
            "CRYPTO": {"RISK": "OPERATIONAL_PIT_SOURCE_POPULATION_WIRED", "VALUATION": "UNDEFINED_NO_RATIFIED_METRIC"},
            "KOREA": {"RISK": "PARTIAL_INPUT_POLICY_AND_RELEASE_TIMING_UNRATIFIED", "VALUATION": "PARTIAL_DART_AND_KRX_SOURCE_FRAGMENTS"},
            "US": {"RISK": "PARTIAL_INPUT_POLICY_UNRATIFIED", "VALUATION": "PARTIAL_SEC_AND_TRANSIENT_PRICE_SOURCE_FRAGMENTS"},
        },
        "policy_status": {
            "default_interpretation_policy": "ABSENT",
            "deterioration_direction": "UNRATIFIED",
            "minimum_change": "UNRATIFIED",
            "metric_selection": "UNRATIFIED",
            "source_hierarchy": "UNRATIFIED",
        },
        "authority": {
            "raw_context_attachment_without_policy": True,
            "deterioration_label_only_with_ratified_policy": True,
            "candidate_mutation_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "rule_evaluation_authorized": False,
            "portfolio_action_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict):
        raise ValuationRiskContextError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ValuationRiskContextError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise ValuationRiskContextError("CONTRACT_FIELDS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _valid_date(value) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_utc(value) -> bool:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return False
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") == value
    except ValueError:
        return False


def _utc(value: str) -> dt.datetime:
    if not _valid_utc(value):
        raise ValuationRiskContextError(f"UTC_INVALID:{value!r}")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _decimal(value, context: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ValuationRiskContextError(f"DECIMAL_NOT_STRING:{context}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValuationRiskContextError(f"DECIMAL_INVALID:{context}") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValuationRiskContextError(f"DECIMAL_INVALID:{context}")
    return result


def _render(value: Decimal, contract: dict) -> str:
    with localcontext() as context:
        context.prec = 50
        quantum = Decimal(1).scaleb(-contract["output_decimal_places"])
        return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def _validate_source(
    source: dict,
    market: str,
    dimension: str,
    period_end: str,
    as_of: dt.datetime,
    contract: dict,
    context: str,
) -> dict:
    fields = {"source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"}
    if not isinstance(source, dict) or set(source) != fields:
        raise ValuationRiskContextError(f"SOURCE_IDENTITY_FIELDS_MISMATCH:{context}")
    source_id = source.get("source_id")
    allowed = {item for group in contract["source_requirements"][market][dimension] for item in group}
    if source_id not in allowed:
        raise ValuationRiskContextError(f"SOURCE_ID_NOT_ALLOWED:{context}:{source_id}")
    parsed = urlparse(str(source.get("source_url") or ""))
    if (
        parsed.scheme != "https" or parsed.hostname not in contract["source_hosts"][source_id]
        or parsed.username is not None or parsed.password is not None
    ):
        raise ValuationRiskContextError(f"SOURCE_URL_INVALID:{context}")
    if not isinstance(source.get("source_sha256"), str) or SHA256_RE.fullmatch(source["source_sha256"]) is None:
        raise ValuationRiskContextError(f"SOURCE_SHA256_INVALID:{context}")
    available = source.get("available_at")
    retrieved = source.get("retrieved_at_utc")
    if not (_valid_date(available) or _valid_utc(available)) or not _valid_utc(retrieved):
        raise ValuationRiskContextError(f"SOURCE_TIME_INVALID:{context}")
    retrieved_time = _utc(retrieved)
    if _valid_date(available):
        bad_order = dt.date.fromisoformat(available) > retrieved_time.date()
        after_as_of = dt.date.fromisoformat(available) > as_of.date()
        before_period = dt.date.fromisoformat(available) < dt.date.fromisoformat(period_end)
    else:
        available_time = _utc(available)
        bad_order = available_time > retrieved_time
        after_as_of = available_time > as_of
        before_period = available_time.date() < dt.date.fromisoformat(period_end)
    if bad_order or after_as_of or before_period or retrieved_time > as_of:
        raise ValuationRiskContextError(f"SOURCE_TEMPORAL_ORDER_INVALID:{context}")
    return copy.deepcopy(source)


def _validate_candidate(value: dict, as_of: dt.datetime, contract: dict) -> dict:
    fields = {"case_id", "case_schema_version", "market", "asset_id", "observation_date", "case_payload_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValuationRiskContextError("CANDIDATE_REF_FIELDS_MISMATCH")
    if not isinstance(value.get("case_id"), str) or CASE_ID_RE.fullmatch(value["case_id"]) is None:
        raise ValuationRiskContextError("CASE_ID_INVALID")
    if value.get("case_schema_version") not in contract["allowed_case_schema_versions"]:
        raise ValuationRiskContextError(f"CASE_SCHEMA_VERSION_INVALID:{value.get('case_schema_version')}")
    if value.get("market") not in contract["allowed_markets"]:
        raise ValuationRiskContextError(f"CANDIDATE_MARKET_INVALID:{value.get('market')}")
    if not isinstance(value.get("asset_id"), str) or TOKEN_RE.fullmatch(value["asset_id"]) is None:
        raise ValuationRiskContextError("CANDIDATE_ASSET_ID_INVALID")
    if not _valid_date(value.get("observation_date")) or dt.date.fromisoformat(value["observation_date"]) > as_of.date():
        raise ValuationRiskContextError("CANDIDATE_OBSERVATION_DATE_INVALID")
    if not isinstance(value.get("case_payload_sha256"), str) or SHA256_RE.fullmatch(value["case_payload_sha256"]) is None:
        raise ValuationRiskContextError("CASE_PAYLOAD_SHA256_INVALID")
    return copy.deepcopy(value)


def _validate_point(point: dict, context: dict, as_of: dt.datetime, contract: dict) -> tuple[dict, Decimal | None]:
    fields = {"period_end", "status", "numeric_value", "missing_reasons", "source_identities"}
    if not isinstance(point, dict) or set(point) != fields:
        raise ValuationRiskContextError(f"EVIDENCE_FIELDS_MISMATCH:{context['context_id']}")
    period = point.get("period_end")
    if not _valid_date(period) or dt.date.fromisoformat(period) > as_of.date():
        raise ValuationRiskContextError(f"EVIDENCE_PERIOD_INVALID:{period}")
    status = point.get("status")
    if status not in {"EVIDENCE_AVAILABLE", "EVIDENCE_BLOCKED", "EVIDENCE_UNRESOLVED"}:
        raise ValuationRiskContextError(f"EVIDENCE_STATUS_INVALID:{period}:{status}")
    reasons = point.get("missing_reasons")
    sources = point.get("source_identities")
    if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
        raise ValuationRiskContextError(f"MISSING_REASONS_INVALID:{period}")
    if status != "EVIDENCE_AVAILABLE":
        if point.get("numeric_value") is not None or sources != [] or not reasons:
            raise ValuationRiskContextError(f"UNAVAILABLE_EVIDENCE_INCONSISTENT:{period}")
        return copy.deepcopy(point), None
    if reasons or not isinstance(sources, list) or not sources:
        raise ValuationRiskContextError(f"AVAILABLE_EVIDENCE_INCONSISTENT:{period}")
    checked_sources = [
        _validate_source(
            source,
            context["market"],
            context["dimension"],
            period,
            as_of,
            contract,
            f"{context['context_id']}:{period}",
        )
        for source in sources
    ]
    checked_sources.sort(key=lambda item: item["source_id"])
    source_ids = [item["source_id"] for item in checked_sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValuationRiskContextError(f"SOURCE_ID_DUPLICATE:{context['context_id']}:{period}")
    for group in contract["source_requirements"][context["market"]][context["dimension"]]:
        if not any(source_id in group for source_id in source_ids):
            raise ValuationRiskContextError(f"REQUIRED_SOURCE_GROUP_MISSING:{context['context_id']}:{period}:{group}")
    value = _decimal(point.get("numeric_value"), f"{context['context_id']}:{period}")
    if context["dimension"] == "RISK" and value < 0:
        raise ValuationRiskContextError(f"RISK_VALUE_NEGATIVE:{context['context_id']}:{period}")
    checked = copy.deepcopy(point)
    checked["source_identities"] = checked_sources
    return checked, value


def _validate_policy(value: dict | None, contract: dict) -> dict | None:
    if value is None:
        return None
    fields = {"schema_version", "policy_id", "approval_status", "effective_from", "effective_to", "ratified_by", "ratified_at_utc", "rules"}
    if not isinstance(value, dict) or value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise ValuationRiskContextError("POLICY_SCHEMA_MISMATCH")
    if set(value) != fields:
        raise ValuationRiskContextError("POLICY_FIELDS_MISMATCH")
    if not isinstance(value.get("policy_id"), str) or TOKEN_RE.fullmatch(value["policy_id"]) is None:
        raise ValuationRiskContextError("POLICY_ID_INVALID")
    status = value.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise ValuationRiskContextError("POLICY_APPROVAL_STATUS_INVALID")
    if not _valid_date(value.get("effective_from")):
        raise ValuationRiskContextError("POLICY_EFFECTIVE_FROM_INVALID")
    end = value.get("effective_to")
    if end is not None and (not _valid_date(end) or end <= value["effective_from"]):
        raise ValuationRiskContextError("POLICY_EFFECTIVE_TO_INVALID")
    if status == "RATIFIED" and (
        not isinstance(value.get("ratified_by"), str) or not value["ratified_by"].strip()
        or not _valid_utc(value.get("ratified_at_utc"))
    ):
        raise ValuationRiskContextError("POLICY_RATIFICATION_PROOF_INVALID")
    if status == "UNRATIFIED" and (value.get("ratified_by") is not None or value.get("ratified_at_utc") is not None):
        raise ValuationRiskContextError("UNRATIFIED_POLICY_PROOF_FORBIDDEN")
    if not isinstance(value.get("rules"), list) or (status == "RATIFIED" and not value["rules"]):
        raise ValuationRiskContextError("POLICY_RULES_INVALID")
    rule_fields = {
        "market", "context_id", "dimension", "measurement_identity", "metric_type",
        "unit", "comparison_basis", "deterioration_direction", "minimum_change",
    }
    seen = set()
    checked = []
    for rule in value["rules"]:
        if not isinstance(rule, dict) or set(rule) != rule_fields:
            raise ValuationRiskContextError("POLICY_RULE_FIELDS_MISMATCH")
        market = rule.get("market")
        dimension = rule.get("dimension")
        context_id = rule.get("context_id")
        if market not in contract["allowed_markets"] or dimension not in contract["allowed_dimensions"]:
            raise ValuationRiskContextError("POLICY_SCOPE_INVALID")
        if not isinstance(context_id, str) or TOKEN_RE.fullmatch(context_id) is None:
            raise ValuationRiskContextError("POLICY_CONTEXT_ID_INVALID")
        if rule.get("metric_type") not in contract["allowed_metric_types"][market][dimension]:
            raise ValuationRiskContextError("POLICY_METRIC_TYPE_INVALID")
        for field in ("measurement_identity", "unit", "comparison_basis"):
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                raise ValuationRiskContextError(f"POLICY_IDENTITY_INVALID:{field}")
        if rule.get("deterioration_direction") not in {"HIGHER_IS_DETERIORATION", "LOWER_IS_DETERIORATION"}:
            raise ValuationRiskContextError("POLICY_DIRECTION_INVALID")
        _decimal(rule.get("minimum_change"), "policy:minimum_change", nonnegative=True)
        key = (market, context_id)
        if key in seen:
            raise ValuationRiskContextError(f"POLICY_RULE_DUPLICATE:{market}:{context_id}")
        seen.add(key)
        checked.append(copy.deepcopy(rule))
    result = copy.deepcopy(value)
    result["rules"] = sorted(checked, key=lambda item: (item["market"], item["context_id"]))
    return result


def _matching_rule(policy: dict | None, context: dict, last_period: str) -> dict | None:
    if policy is None or policy["approval_status"] != "RATIFIED":
        return None
    if last_period < policy["effective_from"] or (policy["effective_to"] is not None and last_period >= policy["effective_to"]):
        return None
    return next((rule for rule in policy["rules"] if rule["market"] == context["market"] and rule["context_id"] == context["context_id"]), None)


def _context_result(context: dict, candidates: dict, as_of: dt.datetime, policy: dict | None, contract: dict) -> dict:
    fields = {
        "context_id", "case_id", "market", "asset_id", "dimension", "measurement_identity",
        "metric_type", "unit", "comparison_basis", "expected_periods", "evidence_points",
    }
    if not isinstance(context, dict) or set(context) != fields:
        raise ValuationRiskContextError("CONTEXT_FIELDS_MISMATCH")
    context_id = context.get("context_id")
    if not isinstance(context_id, str) or TOKEN_RE.fullmatch(context_id) is None:
        raise ValuationRiskContextError("CONTEXT_ID_INVALID")
    candidate = candidates.get(context.get("case_id"))
    if candidate is None:
        raise ValuationRiskContextError(f"CONTEXT_CASE_REF_UNKNOWN:{context.get('case_id')}")
    if context.get("market") != candidate["market"] or context.get("asset_id") != candidate["asset_id"]:
        raise ValuationRiskContextError(f"CONTEXT_CANDIDATE_IDENTITY_MISMATCH:{context_id}")
    market = context["market"]
    dimension = context.get("dimension")
    metric_type = context.get("metric_type")
    if dimension not in contract["allowed_dimensions"]:
        raise ValuationRiskContextError(f"CONTEXT_DIMENSION_INVALID:{context_id}")
    if metric_type not in contract["allowed_metric_types"][market][dimension]:
        raise ValuationRiskContextError(f"CONTEXT_METRIC_TYPE_INVALID:{context_id}")
    for field in ("measurement_identity", "unit", "comparison_basis"):
        if not isinstance(context.get(field), str) or not context[field].strip():
            raise ValuationRiskContextError(f"CONTEXT_IDENTITY_INVALID:{context_id}:{field}")
    periods = context.get("expected_periods")
    if (
        not isinstance(periods, list) or len(periods) != contract["required_point_count"]
        or periods != sorted(set(periods)) or not all(_valid_date(item) for item in periods)
    ):
        raise ValuationRiskContextError(f"EXPECTED_PERIODS_INVALID:{context_id}")
    points = context.get("evidence_points")
    if not isinstance(points, list) or len(points) != len(periods):
        raise ValuationRiskContextError(f"EVIDENCE_POINT_COUNT_INVALID:{context_id}")
    checked = [_validate_point(point, context, as_of, contract) for point in points]
    checked.sort(key=lambda item: item[0]["period_end"])
    if [item[0]["period_end"] for item in checked] != periods:
        raise ValuationRiskContextError(f"EVIDENCE_PERIOD_COVERAGE_MISMATCH:{context_id}")
    unavailable = [
        {"period_end": item[0]["period_end"], "status": item[0]["status"], "missing_reasons": copy.deepcopy(item[0]["missing_reasons"])}
        for item in checked if item[1] is None
    ]
    base = {
        "context_id": context_id, "case_id": candidate["case_id"], "market": market,
        "asset_id": context["asset_id"], "dimension": dimension,
        "measurement_identity": context["measurement_identity"], "metric_type": metric_type,
        "unit": context["unit"], "comparison_basis": context["comparison_basis"],
        "expected_periods": periods,
        "evidence_lineage": [
            {"period_end": item[0]["period_end"], "status": item[0]["status"], "source_identities": copy.deepcopy(item[0]["source_identities"])}
            for item in checked
        ],
        "deterioration_policy_match": None,
    }
    if unavailable:
        return {
            **base, "feature_status": "UNKNOWN_EVIDENCE", "values": None,
            "change": None, "interpretation_status": "NOT_EVALUATED_UNKNOWN_EVIDENCE",
            "unavailable_evidence": unavailable,
        }
    values = [item[1] for item in checked]
    change = values[1] - values[0]
    result = {
        **base, "feature_status": "OBSERVED", "values": [_render(item, contract) for item in values],
        "change": _render(change, contract), "unavailable_evidence": [],
    }
    rule = _matching_rule(policy, context, periods[-1])
    if rule is None:
        result["interpretation_status"] = (
            "ABSENT_OR_UNRATIFIED_POLICY" if policy is None or policy["approval_status"] != "RATIFIED"
            else "NO_EFFECTIVE_EXACT_RULE"
        )
        return result
    identity_fields = ("dimension", "measurement_identity", "metric_type", "unit", "comparison_basis")
    if any(rule[field] != context[field] for field in identity_fields):
        result["interpretation_status"] = "EXACT_RULE_IDENTITY_MISMATCH"
        result["deterioration_policy_match"] = False
        return result
    sign = Decimal(1) if rule["deterioration_direction"] == "HIGHER_IS_DETERIORATION" else Decimal(-1)
    matched = sign * change >= _decimal(rule["minimum_change"], "policy:minimum_change", nonnegative=True)
    result["interpretation_status"] = "RATIFIED_EXACT_RULE_APPLIED"
    result["deterioration_policy_match"] = matched
    result["interpretation_policy"] = {
        "policy_id": policy["policy_id"], "policy_sha256": payload_sha256(policy),
        "ratified_by": policy["ratified_by"], "ratified_at_utc": policy["ratified_at_utc"],
        "deterioration_direction": rule["deterioration_direction"],
        "minimum_change": rule["minimum_change"],
    }
    return result


def _dimension_packet(items: list[dict]) -> dict:
    if not items:
        status = "EVIDENCE_ABSENT"
    elif any(item["feature_status"] == "OBSERVED" for item in items):
        status = "OBSERVED_RAW_CONTEXT"
    else:
        status = "UNKNOWN_EVIDENCE"
    return {
        "status": status,
        "context_count": len(items),
        "deterioration_match_count": sum(item.get("deterioration_policy_match") is True for item in items),
        "contexts": copy.deepcopy(items),
    }


def _output_decimal(value, context: str, contract: dict) -> Decimal:
    parsed = _decimal(value, context)
    if value != _render(parsed, contract):
        raise ValuationRiskContextError(f"OUTPUT_DECIMAL_NOT_CANONICAL:{context}")
    return parsed


def _validate_output_context(
    context: dict,
    candidate: dict,
    as_of: dt.datetime,
    policy: dict | None,
    contract: dict,
) -> dict:
    base_fields = {
        "context_id",
        "case_id",
        "market",
        "asset_id",
        "dimension",
        "measurement_identity",
        "metric_type",
        "unit",
        "comparison_basis",
        "expected_periods",
        "evidence_lineage",
        "deterioration_policy_match",
        "feature_status",
        "values",
        "change",
        "interpretation_status",
        "unavailable_evidence",
    }
    if not isinstance(context, dict) or (
        set(context) != base_fields
        and set(context) != base_fields | {"interpretation_policy"}
    ):
        raise ValuationRiskContextError("OUTPUT_CONTEXT_FIELDS_MISMATCH")
    context_id = context.get("context_id")
    market = context.get("market")
    dimension = context.get("dimension")
    if (
        not isinstance(context_id, str)
        or TOKEN_RE.fullmatch(context_id) is None
        or context.get("case_id") != candidate["case_id"]
        or market != candidate["market"]
        or context.get("asset_id") != candidate["asset_id"]
        or dimension not in contract["allowed_dimensions"]
        or context.get("metric_type")
        not in contract["allowed_metric_types"][market][dimension]
        or any(
            not isinstance(context.get(field), str) or not context[field].strip()
            for field in ("measurement_identity", "unit", "comparison_basis")
        )
    ):
        raise ValuationRiskContextError("OUTPUT_CONTEXT_IDENTITY_MISMATCH")
    periods = context.get("expected_periods")
    if (
        not isinstance(periods, list)
        or len(periods) != contract["required_point_count"]
        or periods != sorted(set(periods))
        or any(not _valid_date(period) for period in periods)
        or periods[-1] > as_of.strftime("%Y-%m-%d")
    ):
        raise ValuationRiskContextError("OUTPUT_CONTEXT_PERIODS_MISMATCH")

    lineage = context.get("evidence_lineage")
    if not isinstance(lineage, list) or len(lineage) != len(periods):
        raise ValuationRiskContextError("OUTPUT_CONTEXT_LINEAGE_COUNT_MISMATCH")
    lineage_by_period = {}
    unavailable_statuses = set()
    for index, row in enumerate(lineage):
        if not isinstance(row, dict) or set(row) != {
            "period_end",
            "status",
            "source_identities",
        }:
            raise ValuationRiskContextError("OUTPUT_CONTEXT_LINEAGE_FIELDS_MISMATCH")
        period = row.get("period_end")
        status = row.get("status")
        sources = row.get("source_identities")
        if period != periods[index] or status not in {
            "EVIDENCE_AVAILABLE",
            "EVIDENCE_BLOCKED",
            "EVIDENCE_UNRESOLVED",
        }:
            raise ValuationRiskContextError("OUTPUT_CONTEXT_LINEAGE_IDENTITY_MISMATCH")
        if status == "EVIDENCE_AVAILABLE":
            if not isinstance(sources, list) or not sources:
                raise ValuationRiskContextError("OUTPUT_CONTEXT_SOURCES_EMPTY")
            checked_sources = [
                _validate_source(
                    source,
                    market,
                    dimension,
                    period,
                    as_of,
                    contract,
                    f"{context_id}:{period}",
                )
                for source in sources
            ]
            checked_sources.sort(key=lambda item: item["source_id"])
            source_ids = [source["source_id"] for source in checked_sources]
            if source_ids != sorted(set(source_ids)):
                raise ValuationRiskContextError("OUTPUT_CONTEXT_SOURCE_ORDER_INVALID")
            for group in contract["source_requirements"][market][dimension]:
                if not any(source_id in group for source_id in source_ids):
                    raise ValuationRiskContextError("OUTPUT_CONTEXT_SOURCE_GROUP_MISSING")
        else:
            if sources != []:
                raise ValuationRiskContextError("OUTPUT_UNAVAILABLE_CONTEXT_SOURCE_PRESENT")
            checked_sources = []
            unavailable_statuses.add(period)
        lineage_by_period[period] = {
            "status": status,
            "source_identities": checked_sources,
        }

    unavailable = context.get("unavailable_evidence")
    if not isinstance(unavailable, list):
        raise ValuationRiskContextError("OUTPUT_CONTEXT_UNAVAILABLE_NOT_LIST")
    unavailable_periods = []
    for row in unavailable:
        if not isinstance(row, dict) or set(row) != {
            "period_end",
            "status",
            "missing_reasons",
        }:
            raise ValuationRiskContextError("OUTPUT_CONTEXT_UNAVAILABLE_FIELDS_MISMATCH")
        period = row.get("period_end")
        if (
            period not in unavailable_statuses
            or row.get("status") != lineage_by_period[period]["status"]
            or not isinstance(row.get("missing_reasons"), list)
            or not row["missing_reasons"]
            or any(not isinstance(reason, str) or not reason for reason in row["missing_reasons"])
        ):
            raise ValuationRiskContextError("OUTPUT_CONTEXT_UNAVAILABLE_MISMATCH")
        unavailable_periods.append(period)
    if unavailable_periods != sorted(unavailable_statuses):
        raise ValuationRiskContextError("OUTPUT_CONTEXT_UNAVAILABLE_COVERAGE_MISMATCH")

    feature_status = context.get("feature_status")
    interpretation_status = context.get("interpretation_status")
    matched = context.get("deterioration_policy_match")
    if unavailable_statuses:
        if (
            feature_status != "UNKNOWN_EVIDENCE"
            or context.get("values") is not None
            or context.get("change") is not None
            or interpretation_status != "NOT_EVALUATED_UNKNOWN_EVIDENCE"
            or matched is not None
            or "interpretation_policy" in context
        ):
            raise ValuationRiskContextError("OUTPUT_UNKNOWN_CONTEXT_MISMATCH")
    else:
        values_text = context.get("values")
        if (
            feature_status != "OBSERVED"
            or not isinstance(values_text, list)
            or len(values_text) != contract["required_point_count"]
            or unavailable
        ):
            raise ValuationRiskContextError("OUTPUT_OBSERVED_CONTEXT_MISMATCH")
        values = [
            _output_decimal(value, f"{context_id}:value", contract)
            for value in values_text
        ]
        if dimension == "RISK" and any(value < 0 for value in values):
            raise ValuationRiskContextError("OUTPUT_RISK_VALUE_NEGATIVE")
        change = values[1] - values[0]
        if context.get("change") != _render(change, contract):
            raise ValuationRiskContextError("OUTPUT_CONTEXT_CHANGE_MISMATCH")
        rule = _matching_rule(policy, context, periods[-1])
        if policy is None or policy["approval_status"] != "RATIFIED":
            expected_status = "ABSENT_OR_UNRATIFIED_POLICY"
            expected_match = None
            expected_proof = None
        elif rule is None:
            expected_status = "NO_EFFECTIVE_EXACT_RULE"
            expected_match = None
            expected_proof = None
        elif any(
            rule[field] != context[field]
            for field in (
                "dimension",
                "measurement_identity",
                "metric_type",
                "unit",
                "comparison_basis",
            )
        ):
            expected_status = "EXACT_RULE_IDENTITY_MISMATCH"
            expected_match = False
            expected_proof = None
        else:
            expected_status = "RATIFIED_EXACT_RULE_APPLIED"
            sign = (
                Decimal(1)
                if rule["deterioration_direction"] == "HIGHER_IS_DETERIORATION"
                else Decimal(-1)
            )
            expected_match = sign * change >= Decimal(rule["minimum_change"])
            expected_proof = {
                "policy_id": policy["policy_id"],
                "policy_sha256": policy["policy_sha256"],
                "ratified_by": policy["ratified_by"],
                "ratified_at_utc": policy["ratified_at_utc"],
                "deterioration_direction": rule["deterioration_direction"],
                "minimum_change": rule["minimum_change"],
            }
        if (
            interpretation_status != expected_status
            or matched is not expected_match
            or context.get("interpretation_policy") != expected_proof
            or (("interpretation_policy" in context) is not (expected_proof is not None))
        ):
            raise ValuationRiskContextError("OUTPUT_INTERPRETATION_DERIVATION_MISMATCH")
    return copy.deepcopy(context)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate persisted candidate contexts from retained two-point evidence."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet_fields = {
        "schema_version",
        "contract_version",
        "as_of_utc",
        "status",
        "candidate_count",
        "context_observation_count",
        "context_complete_candidate_count",
        "interpretation_policy",
        "source_policy",
        "candidate_contexts",
        "source_coverage",
        "policy_status",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != packet_fields:
        raise ValuationRiskContextError("OUTPUT_FIELDS_MISMATCH")
    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise ValuationRiskContextError("OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != digest:
        raise ValuationRiskContextError("OUTPUT_SHA256_MISMATCH")
    as_of_utc = packet.get("as_of_utc")
    if (
        packet.get("schema_version") != OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "VALUATION_RISK_CONTEXT_ATTACHED"
        or not _valid_utc(as_of_utc)
    ):
        raise ValuationRiskContextError("OUTPUT_IDENTITY_MISMATCH")
    as_of = _utc(as_of_utc)
    source_policy = _validate_policy(packet.get("source_policy"), contract)
    if (
        source_policy is not None
        and source_policy["approval_status"] == "RATIFIED"
        and _utc(source_policy["ratified_at_utc"]) > as_of
    ):
        raise ValuationRiskContextError("OUTPUT_POLICY_RATIFIED_AFTER_AS_OF")
    policy_summary = packet.get("interpretation_policy")
    expected_policy_summary = (
        None
        if source_policy is None
        else {
            "policy_id": source_policy["policy_id"],
            "approval_status": source_policy["approval_status"],
            "policy_sha256": payload_sha256(source_policy),
        }
    )
    if policy_summary != expected_policy_summary:
        raise ValuationRiskContextError("OUTPUT_POLICY_SOURCE_MISMATCH")
    policy = (
        None
        if source_policy is None
        else {**source_policy, "policy_sha256": payload_sha256(source_policy)}
    )

    candidates = packet.get("candidate_contexts")
    if not isinstance(candidates, list) or not candidates:
        raise ValuationRiskContextError("OUTPUT_CANDIDATES_EMPTY")
    candidate_fields = {
        "candidate_ref",
        "valuation",
        "risk",
        "context_complete",
        "total_deterioration_match_count",
        "candidate_rank",
        "stage_transition",
        "rule_evaluation",
        "portfolio_action",
        "trading_action",
    }
    dimension_fields = {
        "status",
        "context_count",
        "deterioration_match_count",
        "contexts",
    }
    candidate_ids = []
    all_context_ids = []
    complete_count = 0
    for candidate_packet in candidates:
        if not isinstance(candidate_packet, dict) or set(candidate_packet) != candidate_fields:
            raise ValuationRiskContextError("OUTPUT_CANDIDATE_FIELDS_MISMATCH")
        candidate = _validate_candidate(
            candidate_packet.get("candidate_ref"), as_of, contract
        )
        if (
            candidate_packet.get("candidate_rank") is not None
            or candidate_packet.get("stage_transition") is not None
            or candidate_packet.get("rule_evaluation") is not None
            or candidate_packet.get("portfolio_action") is not None
            or candidate_packet.get("trading_action") is not None
        ):
            raise ValuationRiskContextError("OUTPUT_CANDIDATE_AUTHORITY_EXPANSION")
        dimension_packets = {}
        for dimension, key in (("VALUATION", "valuation"), ("RISK", "risk")):
            value = candidate_packet.get(key)
            if not isinstance(value, dict) or set(value) != dimension_fields:
                raise ValuationRiskContextError("OUTPUT_DIMENSION_FIELDS_MISMATCH")
            contexts = value.get("contexts")
            if not isinstance(contexts, list):
                raise ValuationRiskContextError("OUTPUT_DIMENSION_CONTEXTS_NOT_LIST")
            checked = [
                _validate_output_context(context, candidate, as_of, policy, contract)
                for context in contexts
            ]
            if any(context["dimension"] != dimension for context in checked):
                raise ValuationRiskContextError("OUTPUT_DIMENSION_CONTEXT_MISMATCH")
            ids = [context["context_id"] for context in checked]
            if ids != sorted(set(ids)):
                raise ValuationRiskContextError("OUTPUT_DIMENSION_CONTEXT_ORDER_INVALID")
            expected_status = (
                "EVIDENCE_ABSENT"
                if not checked
                else "OBSERVED_RAW_CONTEXT"
                if any(context["feature_status"] == "OBSERVED" for context in checked)
                else "UNKNOWN_EVIDENCE"
            )
            match_count = sum(
                context.get("deterioration_policy_match") is True
                for context in checked
            )
            if (
                value.get("status") != expected_status
                or type(value.get("context_count")) is not int
                or value["context_count"] != len(checked)
                or type(value.get("deterioration_match_count")) is not int
                or value["deterioration_match_count"] != match_count
            ):
                raise ValuationRiskContextError("OUTPUT_DIMENSION_SUMMARY_MISMATCH")
            dimension_packets[dimension] = value
            all_context_ids.extend(ids)
        expected_complete = (
            dimension_packets["VALUATION"]["status"] == "OBSERVED_RAW_CONTEXT"
            and dimension_packets["RISK"]["status"] == "OBSERVED_RAW_CONTEXT"
        )
        expected_matches = sum(
            dimension_packets[dimension]["deterioration_match_count"]
            for dimension in ("VALUATION", "RISK")
        )
        if (
            candidate_packet.get("context_complete") is not expected_complete
            or type(candidate_packet.get("total_deterioration_match_count")) is not int
            or candidate_packet["total_deterioration_match_count"] != expected_matches
        ):
            raise ValuationRiskContextError("OUTPUT_CANDIDATE_SUMMARY_MISMATCH")
        candidate_ids.append(candidate["case_id"])
        complete_count += int(expected_complete)
    if candidate_ids != sorted(set(candidate_ids)):
        raise ValuationRiskContextError("OUTPUT_CANDIDATE_ORDER_OR_DUPLICATE_INVALID")
    if len(all_context_ids) != len(set(all_context_ids)):
        raise ValuationRiskContextError("OUTPUT_CONTEXT_ID_DUPLICATE")

    expected_boundaries = [
        "DEFAULT_INTERPRETATION_POLICY_ABSENT",
        "DETERIORATION_DIRECTION_UNRATIFIED",
        "MINIMUM_CHANGE_UNRATIFIED",
        "METRIC_SELECTION_UNRATIFIED",
        "SOURCE_HIERARCHY_UNRATIFIED",
        "CRYPTO_VALUATION_UNDEFINED",
        "US_RISK_INPUT_POLICY_UNRATIFIED",
        "KOREA_RISK_INPUT_AND_RELEASE_TIMING_UNRATIFIED",
        "LIVE_CONTEXT_POPULATION_PARTIAL_CRYPTO_RISK_SOURCE_ONLY",
    ]
    if (
        type(packet.get("candidate_count")) is not int
        or packet["candidate_count"] != len(candidates)
        or type(packet.get("context_observation_count")) is not int
        or packet["context_observation_count"] != len(all_context_ids)
        or type(packet.get("context_complete_candidate_count")) is not int
        or packet["context_complete_candidate_count"] != complete_count
        or packet.get("source_coverage") != contract["source_coverage"]
        or packet.get("policy_status") != contract["policy_status"]
        or packet.get("authority") != contract["authority"]
        or packet.get("unresolved_boundaries") != expected_boundaries
    ):
        raise ValuationRiskContextError("OUTPUT_SUMMARY_OR_BOUNDARY_MISMATCH")
    return copy.deepcopy(packet)


def build_packet(value: dict, interpretation_policy: dict | None = None, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    policy = _validate_policy(interpretation_policy, contract)
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ValuationRiskContextError("INPUT_SCHEMA_MISMATCH")
    if set(value) != {"schema_version", "as_of_utc", "candidates", "context_observations"}:
        raise ValuationRiskContextError("INPUT_FIELDS_MISMATCH")
    as_of_utc = value.get("as_of_utc")
    if not _valid_utc(as_of_utc):
        raise ValuationRiskContextError("AS_OF_UTC_INVALID")
    as_of = _utc(as_of_utc)
    if policy is not None and policy["approval_status"] == "RATIFIED" and _utc(policy["ratified_at_utc"]) > as_of:
        raise ValuationRiskContextError("POLICY_RATIFIED_AFTER_AS_OF")
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValuationRiskContextError("CANDIDATES_EMPTY")
    candidates = {}
    for raw in raw_candidates:
        candidate = _validate_candidate(raw, as_of, contract)
        if candidate["case_id"] in candidates:
            raise ValuationRiskContextError(f"CANDIDATE_DUPLICATE:{candidate['case_id']}")
        candidates[candidate["case_id"]] = candidate
    raw_contexts = value.get("context_observations")
    if not isinstance(raw_contexts, list):
        raise ValuationRiskContextError("CONTEXT_OBSERVATIONS_NOT_LIST")
    contexts_by_case = {case_id: [] for case_id in candidates}
    context_ids = set()
    for raw in raw_contexts:
        result = _context_result(raw, candidates, as_of, policy, contract)
        if result["context_id"] in context_ids:
            raise ValuationRiskContextError(f"CONTEXT_ID_DUPLICATE:{result['context_id']}")
        context_ids.add(result["context_id"])
        contexts_by_case[result["case_id"]].append(result)
    candidate_packets = []
    for case_id in sorted(candidates):
        candidate = candidates[case_id]
        items = sorted(contexts_by_case[case_id], key=lambda item: (item["dimension"], item["context_id"]))
        valuation = _dimension_packet([item for item in items if item["dimension"] == "VALUATION"])
        risk = _dimension_packet([item for item in items if item["dimension"] == "RISK"])
        candidate_packets.append({
            "candidate_ref": copy.deepcopy(candidate),
            "valuation": valuation,
            "risk": risk,
            "context_complete": valuation["status"] == "OBSERVED_RAW_CONTEXT" and risk["status"] == "OBSERVED_RAW_CONTEXT",
            "total_deterioration_match_count": valuation["deterioration_match_count"] + risk["deterioration_match_count"],
            "candidate_rank": None, "stage_transition": None, "rule_evaluation": None,
            "portfolio_action": None, "trading_action": None,
        })
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"], "as_of_utc": as_of_utc,
        "status": "VALUATION_RISK_CONTEXT_ATTACHED", "candidate_count": len(candidate_packets),
        "context_observation_count": len(context_ids),
        "context_complete_candidate_count": sum(item["context_complete"] for item in candidate_packets),
        "interpretation_policy": None if policy is None else {
            "policy_id": policy["policy_id"], "approval_status": policy["approval_status"],
            "policy_sha256": payload_sha256(policy),
        },
        "source_policy": copy.deepcopy(policy),
        "candidate_contexts": candidate_packets,
        "source_coverage": copy.deepcopy(contract["source_coverage"]),
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "DEFAULT_INTERPRETATION_POLICY_ABSENT", "DETERIORATION_DIRECTION_UNRATIFIED",
            "MINIMUM_CHANGE_UNRATIFIED", "METRIC_SELECTION_UNRATIFIED",
            "SOURCE_HIERARCHY_UNRATIFIED", "CRYPTO_VALUATION_UNDEFINED",
            "US_RISK_INPUT_POLICY_UNRATIFIED", "KOREA_RISK_INPUT_AND_RELEASE_TIMING_UNRATIFIED",
            "LIVE_CONTEXT_POPULATION_PARTIAL_CRYPTO_RISK_SOURCE_ONLY",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ValuationRiskContextError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, output_path: Path, policy_path: Path | None = None) -> int:
    try:
        packet = build_packet(_read_json(input_path), _read_json(policy_path) if policy_path is not None else None)
        write_json_atomic(output_path, packet)
        return 0
    except (ValuationRiskContextError, OSError, TypeError, ValueError) as exc:
        print(f"valuation-risk context failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Attach policy-gated valuation/risk context to Discovery Cases")
    parser.add_argument("input", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.out, args.policy)


if __name__ == "__main__":
    raise SystemExit(main())
