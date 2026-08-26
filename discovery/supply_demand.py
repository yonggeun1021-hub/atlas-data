#!/usr/bin/env python3
"""P3-09 policy-gated Supply-Demand / Scarcity radar.

The capability preserves three exact evidence points and calculates only raw
changes.  It does not decide whether higher or lower is better.  A radar case
exists only when an external, effective, explicitly RATIFIED policy defines the
measurement, direction and minimum changes for the exact series.
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
CONTRACT_PATH = ROOT / "config" / "supply_demand_radar_contract.json"
INPUT_SCHEMA_VERSION = "supply_demand_radar_input/1"
OUTPUT_SCHEMA_VERSION = "supply_demand_radar_packet/3"
POLICY_SCHEMA_VERSION = "supply_demand_candidate_policy/1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class SupplyDemandError(ValueError):
    """Fail-closed supply-demand radar violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupplyDemandError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected = {
        "schema_version": 1,
        "contract_version": "supply_demand_radar/2",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "candidate_policy_schema_version": POLICY_SCHEMA_VERSION,
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "allowed_metric_types": {
            "CRYPTO": ["AGGREGATE_TOKEN_SUPPLY"],
            "KOREA": ["INVESTOR_NET_DEMAND_VALUE", "INVESTOR_NET_DEMAND_VOLUME"],
            "US": ["SECURITY_SUPPLY"],
        },
        "market_sources": {
            "CRYPTO": ["defillama_stablecoins_api"],
            "KOREA": ["krx_information_data_system_pykrx"],
            "US": ["sec_edgar"],
        },
        "source_hosts": {
            "defillama_stablecoins_api": ["stablecoins.llama.fi"],
            "krx_information_data_system_pykrx": ["data.krx.co.kr"],
            "sec_edgar": ["data.sec.gov", "www.sec.gov"],
        },
        "required_point_count": 3,
        "allowed_frequencies": ["DAILY", "MONTHLY", "QUARTERLY", "IRREGULAR"],
        "feature_contract": {
            "prior_change": "value[1] minus value[0]",
            "latest_change": "value[2] minus value[1]",
            "acceleration_change": "latest_change minus prior_change",
        },
        "candidate_logic": (
            "direction_adjusted_latest_change_gte_minimum_AND_"
            "direction_adjusted_acceleration_change_gte_minimum"
        ),
        "output_decimal_places": 12,
        "source_coverage": {
            "CRYPTO": "OPERATIONAL_PIT_POPULATION_WIRED",
            "KOREA": "PARTIAL_KRX_ONLY_RELEASE_TIME_UNVERIFIED",
            "US": "PARTIAL_SEC_SOURCE_NO_SELECTED_METRIC_SERIES",
        },
        "policy_status": {
            "default_candidate_policy": "ABSENT",
            "improvement_direction": "UNRATIFIED",
            "minimum_change": "UNRATIFIED",
            "cross_market_comparability": "UNRATIFIED",
            "source_hierarchy": "UNRATIFIED",
            "candidate_ranking": "UNRATIFIED",
        },
        "authority": {
            "raw_feature_observation_only_without_ratified_policy": True,
            "radar_case_recording_only_with_ratified_policy": True,
            "source_ranking_authorized": False,
            "cross_market_scoring_authorized": False,
            "importance_ranking_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "rule_evaluation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    if not isinstance(value, dict):
        raise SupplyDemandError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise SupplyDemandError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise SupplyDemandError("CONTRACT_FIELDS_MISMATCH")
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
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ) == value
    except ValueError:
        return False


def _utc(value: str) -> dt.datetime:
    if not _valid_utc(value):
        raise SupplyDemandError(f"UTC_INVALID:{value!r}")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _decimal(value, context: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise SupplyDemandError(f"DECIMAL_NOT_STRING:{context}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise SupplyDemandError(f"DECIMAL_INVALID:{context}") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise SupplyDemandError(f"DECIMAL_INVALID:{context}")
    return result


def _render(value: Decimal, contract: dict) -> str:
    with localcontext() as context:
        context.prec = 50
        quantum = Decimal(1).scaleb(-contract["output_decimal_places"])
        return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def _validate_source(source: dict, market: str, as_of: dt.datetime, contract: dict, context: str) -> dict:
    required = {"source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"}
    if not isinstance(source, dict) or set(source) != required:
        raise SupplyDemandError(f"SOURCE_IDENTITY_FIELDS_MISMATCH:{context}")
    source_id = source.get("source_id")
    if source_id not in contract["market_sources"][market]:
        raise SupplyDemandError(f"SOURCE_ID_MISMATCH:{context}:{source_id}")
    parsed = urlparse(str(source.get("source_url") or ""))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in contract["source_hosts"][source_id]
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise SupplyDemandError(f"SOURCE_URL_INVALID:{context}")
    if not isinstance(source.get("source_sha256"), str) or SHA256_RE.fullmatch(source["source_sha256"]) is None:
        raise SupplyDemandError(f"SOURCE_SHA256_INVALID:{context}")
    available = source.get("available_at")
    retrieved = source.get("retrieved_at_utc")
    if not (_valid_date(available) or _valid_utc(available)) or not _valid_utc(retrieved):
        raise SupplyDemandError(f"SOURCE_TIME_INVALID:{context}")
    retrieved_time = _utc(retrieved)
    if _valid_date(available):
        invalid_order = dt.date.fromisoformat(available) > retrieved_time.date()
        after_as_of = dt.date.fromisoformat(available) > as_of.date()
    else:
        available_time = _utc(available)
        invalid_order = available_time > retrieved_time
        after_as_of = available_time > as_of
    if invalid_order or after_as_of or retrieved_time > as_of:
        raise SupplyDemandError(f"SOURCE_TEMPORAL_ORDER_INVALID:{context}")
    return copy.deepcopy(source)


def _validate_point(point: dict, series: dict, as_of: dt.datetime, contract: dict) -> tuple[dict, Decimal | None]:
    required = {"period_end", "status", "numeric_value", "missing_reasons", "source_identity"}
    if not isinstance(point, dict) or set(point) != required:
        raise SupplyDemandError(f"EVIDENCE_FIELDS_MISMATCH:{series['series_id']}")
    period = point.get("period_end")
    if not _valid_date(period):
        raise SupplyDemandError(f"EVIDENCE_PERIOD_INVALID:{period}")
    status = point.get("status")
    if status not in {"EVIDENCE_AVAILABLE", "EVIDENCE_BLOCKED", "EVIDENCE_UNRESOLVED"}:
        raise SupplyDemandError(f"EVIDENCE_STATUS_INVALID:{period}:{status}")
    reasons = point.get("missing_reasons")
    if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
        raise SupplyDemandError(f"MISSING_REASONS_INVALID:{period}")
    if status != "EVIDENCE_AVAILABLE":
        if point.get("numeric_value") is not None or point.get("source_identity") is not None or not reasons:
            raise SupplyDemandError(f"UNAVAILABLE_EVIDENCE_INCONSISTENT:{period}")
        return copy.deepcopy(point), None
    if reasons:
        raise SupplyDemandError(f"AVAILABLE_EVIDENCE_INCONSISTENT:{period}")
    source = _validate_source(point.get("source_identity"), series["market"], as_of, contract, period)
    value = _decimal(point.get("numeric_value"), period)
    checked = copy.deepcopy(point)
    checked["source_identity"] = source
    return checked, value


def _validate_policy(value: dict | None, contract: dict) -> dict | None:
    if value is None:
        return None
    required = {
        "schema_version", "policy_id", "approval_status", "effective_from",
        "effective_to", "ratified_by", "ratified_at_utc", "rules",
    }
    if not isinstance(value, dict) or value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise SupplyDemandError("POLICY_SCHEMA_MISMATCH")
    if set(value) != required:
        raise SupplyDemandError("POLICY_FIELDS_MISMATCH")
    if not isinstance(value.get("policy_id"), str) or TOKEN_RE.fullmatch(value["policy_id"]) is None:
        raise SupplyDemandError("POLICY_ID_INVALID")
    status = value.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise SupplyDemandError("POLICY_APPROVAL_STATUS_INVALID")
    if not _valid_date(value.get("effective_from")):
        raise SupplyDemandError("POLICY_EFFECTIVE_FROM_INVALID")
    end = value.get("effective_to")
    if end is not None and (not _valid_date(end) or end <= value["effective_from"]):
        raise SupplyDemandError("POLICY_EFFECTIVE_TO_INVALID")
    if status == "RATIFIED" and (
        not isinstance(value.get("ratified_by"), str) or not value["ratified_by"].strip()
        or not _valid_utc(value.get("ratified_at_utc"))
    ):
        raise SupplyDemandError("POLICY_RATIFICATION_PROOF_INVALID")
    if status == "UNRATIFIED" and (value.get("ratified_by") is not None or value.get("ratified_at_utc") is not None):
        raise SupplyDemandError("UNRATIFIED_POLICY_PROOF_FORBIDDEN")
    if not isinstance(value.get("rules"), list) or (status == "RATIFIED" and not value["rules"]):
        raise SupplyDemandError("POLICY_RULES_INVALID")
    rule_fields = {
        "market", "series_id", "measurement_identity", "metric_type", "unit",
        "frequency", "comparison_basis", "improvement_direction",
        "minimum_latest_change", "minimum_acceleration_change",
    }
    seen = set()
    checked = []
    for rule in value["rules"]:
        if not isinstance(rule, dict) or set(rule) != rule_fields:
            raise SupplyDemandError("POLICY_RULE_FIELDS_MISMATCH")
        market = rule.get("market")
        series_id = rule.get("series_id")
        if market not in contract["allowed_markets"]:
            raise SupplyDemandError(f"POLICY_MARKET_INVALID:{market}")
        if not isinstance(series_id, str) or TOKEN_RE.fullmatch(series_id) is None:
            raise SupplyDemandError("POLICY_SERIES_ID_INVALID")
        if rule.get("metric_type") not in contract["allowed_metric_types"][market]:
            raise SupplyDemandError("POLICY_METRIC_TYPE_INVALID")
        if rule.get("frequency") not in contract["allowed_frequencies"]:
            raise SupplyDemandError("POLICY_FREQUENCY_INVALID")
        if rule.get("improvement_direction") not in {"HIGHER_IS_IMPROVEMENT", "LOWER_IS_IMPROVEMENT"}:
            raise SupplyDemandError("POLICY_DIRECTION_INVALID")
        for field in ("measurement_identity", "unit", "comparison_basis"):
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                raise SupplyDemandError(f"POLICY_IDENTITY_INVALID:{field}")
        _decimal(rule.get("minimum_latest_change"), "policy:latest", nonnegative=True)
        _decimal(rule.get("minimum_acceleration_change"), "policy:acceleration", nonnegative=True)
        key = (market, series_id)
        if key in seen:
            raise SupplyDemandError(f"POLICY_RULE_DUPLICATE:{market}:{series_id}")
        seen.add(key)
        checked.append(copy.deepcopy(rule))
    result = copy.deepcopy(value)
    result["rules"] = sorted(checked, key=lambda item: (item["market"], item["series_id"]))
    return result


def _matching_rule(policy: dict | None, series: dict, observation_date: str) -> dict | None:
    if policy is None or policy["approval_status"] != "RATIFIED":
        return None
    if observation_date < policy["effective_from"] or (
        policy["effective_to"] is not None and observation_date >= policy["effective_to"]
    ):
        return None
    return next((rule for rule in policy["rules"] if rule["market"] == series["market"] and rule["series_id"] == series["series_id"]), None)


def _series_result(series: dict, as_of: dt.datetime, policy: dict | None, contract: dict) -> tuple[dict, dict | None]:
    required = {
        "series_id", "market", "asset_id", "measurement_identity", "metric_type",
        "unit", "frequency", "comparison_basis", "expected_periods", "evidence_points",
    }
    if not isinstance(series, dict) or set(series) != required:
        raise SupplyDemandError("SERIES_FIELDS_MISMATCH")
    for field in ("series_id", "asset_id"):
        if not isinstance(series.get(field), str) or TOKEN_RE.fullmatch(series[field]) is None:
            raise SupplyDemandError(f"{field.upper()}_INVALID")
    market = series.get("market")
    if market not in contract["allowed_markets"]:
        raise SupplyDemandError(f"MARKET_INVALID:{market}")
    if series.get("metric_type") not in contract["allowed_metric_types"][market]:
        raise SupplyDemandError(f"METRIC_TYPE_INVALID:{series['series_id']}")
    if series.get("frequency") not in contract["allowed_frequencies"]:
        raise SupplyDemandError(f"FREQUENCY_INVALID:{series['series_id']}")
    for field in ("measurement_identity", "unit", "comparison_basis"):
        if not isinstance(series.get(field), str) or not series[field].strip():
            raise SupplyDemandError(f"SERIES_IDENTITY_INVALID:{field}")
    periods = series.get("expected_periods")
    if (
        not isinstance(periods, list)
        or len(periods) != contract["required_point_count"]
        or periods != sorted(set(periods))
        or not all(_valid_date(item) for item in periods)
    ):
        raise SupplyDemandError(f"EXPECTED_PERIODS_INVALID:{series['series_id']}")
    points = series.get("evidence_points")
    if not isinstance(points, list) or len(points) != len(periods):
        raise SupplyDemandError(f"EVIDENCE_POINT_COUNT_INVALID:{series['series_id']}")
    checked = [_validate_point(point, series, as_of, contract) for point in points]
    checked.sort(key=lambda item: item[0]["period_end"])
    if [item[0]["period_end"] for item in checked] != periods:
        raise SupplyDemandError(f"EVIDENCE_PERIOD_COVERAGE_MISMATCH:{series['series_id']}")
    unavailable = [
        {"period_end": item[0]["period_end"], "status": item[0]["status"], "missing_reasons": copy.deepcopy(item[0]["missing_reasons"])}
        for item in checked if item[1] is None
    ]
    base = {
        "series_id": series["series_id"], "market": market, "asset_id": series["asset_id"],
        "measurement_identity": series["measurement_identity"], "metric_type": series["metric_type"],
        "unit": series["unit"], "frequency": series["frequency"],
        "comparison_basis": series["comparison_basis"], "expected_periods": periods,
        "evidence_lineage": [
            {
                "period_end": item[0]["period_end"],
                "status": item[0]["status"],
                "source_identity": copy.deepcopy(item[0]["source_identity"]),
            }
            for item in checked
        ],
        "candidate_policy_match": None, "radar_case_created": False,
        "importance": "UNRATIFIED", "candidate_rank": None,
        "investable_eligible": False, "stage_transition": None, "action": None,
    }
    if unavailable:
        return {
            **base, "feature_status": "UNKNOWN_EVIDENCE", "values": None,
            "prior_change": None, "latest_change": None, "acceleration_change": None,
            "unavailable_evidence": unavailable,
            "candidate_policy_status": "NOT_EVALUATED_UNKNOWN_EVIDENCE",
        }, None
    values = [item[1] for item in checked]
    prior = values[1] - values[0]
    latest = values[2] - values[1]
    acceleration = latest - prior
    result = {
        **base, "feature_status": "OBSERVED", "values": [_render(item, contract) for item in values],
        "prior_change": _render(prior, contract), "latest_change": _render(latest, contract),
        "acceleration_change": _render(acceleration, contract), "unavailable_evidence": [],
    }
    rule = _matching_rule(policy, series, periods[-1])
    if rule is None:
        result["candidate_policy_status"] = (
            "ABSENT_OR_UNRATIFIED" if policy is None or policy["approval_status"] != "RATIFIED"
            else "NO_EFFECTIVE_EXACT_RULE"
        )
        return result, None
    identity_fields = ("measurement_identity", "metric_type", "unit", "frequency", "comparison_basis")
    if any(rule[field] != series[field] for field in identity_fields):
        result["candidate_policy_status"] = "EXACT_RULE_IDENTITY_MISMATCH"
        result["candidate_policy_match"] = False
        return result, None
    sign = Decimal(1) if rule["improvement_direction"] == "HIGHER_IS_IMPROVEMENT" else Decimal(-1)
    adjusted_latest = sign * latest
    adjusted_acceleration = sign * acceleration
    matched = (
        adjusted_latest >= _decimal(rule["minimum_latest_change"], "policy:latest", nonnegative=True)
        and adjusted_acceleration >= _decimal(rule["minimum_acceleration_change"], "policy:acceleration", nonnegative=True)
    )
    result["candidate_policy_status"] = "RATIFIED_EXACT_RULE_APPLIED"
    result["candidate_policy_match"] = matched
    if not matched:
        return result, None
    result["radar_case_created"] = True
    policy_sha = payload_sha256(policy)
    seed = {"policy_id": policy["policy_id"], "market": market, "series_id": series["series_id"], "last_period": periods[-1]}
    case = {
        "schema_version": "supply_demand_case/1",
        "case_id": "RADAR-SD-" + payload_sha256(seed)[:16].upper(),
        "market": market, "asset_id": series["asset_id"], "observation_date": periods[-1],
        "why_found": {
            "measurement_identity": series["measurement_identity"], "metric_type": series["metric_type"],
            "unit": series["unit"], "frequency": series["frequency"],
            "comparison_basis": series["comparison_basis"], "values": result["values"],
            "prior_change": result["prior_change"], "latest_change": result["latest_change"],
            "acceleration_change": result["acceleration_change"],
            "improvement_direction": rule["improvement_direction"],
            "minimum_latest_change": rule["minimum_latest_change"],
            "minimum_acceleration_change": rule["minimum_acceleration_change"],
            "candidate_logic": contract["candidate_logic"],
        },
        "confirmed_evidence": [
            {"period_end": item[0]["period_end"], "numeric_value": item[0]["numeric_value"], "source_identity": copy.deepcopy(item[0]["source_identity"])}
            for item in checked
        ],
        "candidate_policy": {"policy_id": policy["policy_id"], "policy_sha256": policy_sha, "ratified_by": policy["ratified_by"], "ratified_at_utc": policy["ratified_at_utc"]},
        "importance": "UNRATIFIED", "candidate_rank": None, "investable_eligible": False,
        "stage_transition": None, "action": None,
    }
    return result, case


def _output_decimal(value, context: str, contract: dict) -> Decimal:
    parsed = _decimal(value, context)
    if value != _render(parsed, contract):
        raise SupplyDemandError(f"OUTPUT_DECIMAL_NOT_CANONICAL:{context}")
    return parsed


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate persisted raw features, lineage, cases, and authority boundaries."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet_fields = {
        "schema_version",
        "contract_version",
        "as_of_utc",
        "status",
        "series_count",
        "case_count",
        "candidate_policy",
        "source_policy",
        "series_results",
        "cases",
        "source_coverage",
        "policy_status",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != packet_fields:
        raise SupplyDemandError("OUTPUT_FIELDS_MISMATCH")
    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise SupplyDemandError("OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != digest:
        raise SupplyDemandError("OUTPUT_SHA256_MISMATCH")
    as_of_utc = packet.get("as_of_utc")
    if (
        packet.get("schema_version") != OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "SUPPLY_DEMAND_FEATURES_OBSERVED"
        or not _valid_utc(as_of_utc)
    ):
        raise SupplyDemandError("OUTPUT_IDENTITY_MISMATCH")
    as_of = _utc(as_of_utc)

    source_policy = _validate_policy(packet.get("source_policy"), contract)
    if (
        source_policy is not None
        and source_policy["approval_status"] == "RATIFIED"
        and _utc(source_policy["ratified_at_utc"]) > as_of
    ):
        raise SupplyDemandError("OUTPUT_POLICY_RATIFIED_AFTER_AS_OF")
    policy = packet.get("candidate_policy")
    expected_policy = (
        None
        if source_policy is None
        else {
            "policy_id": source_policy["policy_id"],
            "approval_status": source_policy["approval_status"],
            "policy_sha256": payload_sha256(source_policy),
        }
    )
    if policy != expected_policy:
        raise SupplyDemandError("OUTPUT_POLICY_SOURCE_MISMATCH")

    results = packet.get("series_results")
    if not isinstance(results, list) or not results:
        raise SupplyDemandError("OUTPUT_SERIES_EMPTY")
    result_fields = {
        "series_id",
        "market",
        "asset_id",
        "measurement_identity",
        "metric_type",
        "unit",
        "frequency",
        "comparison_basis",
        "expected_periods",
        "evidence_lineage",
        "candidate_policy_match",
        "radar_case_created",
        "importance",
        "candidate_rank",
        "investable_eligible",
        "stage_transition",
        "action",
        "feature_status",
        "values",
        "prior_change",
        "latest_change",
        "acceleration_change",
        "unavailable_evidence",
        "candidate_policy_status",
    }
    result_by_case_id = {}
    result_keys = []
    for result in results:
        if not isinstance(result, dict) or set(result) != result_fields:
            raise SupplyDemandError("OUTPUT_SERIES_RESULT_FIELDS_MISMATCH")
        series_id = result.get("series_id")
        market = result.get("market")
        asset_id = result.get("asset_id")
        frequency = result.get("frequency")
        if (
            not isinstance(series_id, str)
            or TOKEN_RE.fullmatch(series_id) is None
            or market not in contract["allowed_markets"]
            or not isinstance(asset_id, str)
            or TOKEN_RE.fullmatch(asset_id) is None
            or result.get("metric_type") not in contract["allowed_metric_types"][market]
            or frequency not in contract["allowed_frequencies"]
            or any(
                not isinstance(result.get(field), str) or not result[field].strip()
                for field in ("measurement_identity", "unit", "comparison_basis")
            )
        ):
            raise SupplyDemandError("OUTPUT_SERIES_IDENTITY_MISMATCH")
        periods = result.get("expected_periods")
        if (
            not isinstance(periods, list)
            or len(periods) != contract["required_point_count"]
            or periods != sorted(set(periods))
            or any(not _valid_date(period) for period in periods)
            or periods[-1] > as_of_utc[:10]
        ):
            raise SupplyDemandError("OUTPUT_SERIES_PERIODS_MISMATCH")
        if (
            result.get("importance") != "UNRATIFIED"
            or result.get("candidate_rank") is not None
            or result.get("investable_eligible") is not False
            or result.get("stage_transition") is not None
            or result.get("action") is not None
        ):
            raise SupplyDemandError("OUTPUT_SERIES_AUTHORITY_EXPANSION")

        lineage = result.get("evidence_lineage")
        if not isinstance(lineage, list) or len(lineage) != len(periods):
            raise SupplyDemandError("OUTPUT_EVIDENCE_LINEAGE_COUNT_MISMATCH")
        lineage_by_period = {}
        unavailable_statuses = set()
        for index, row in enumerate(lineage):
            if not isinstance(row, dict) or set(row) != {
                "period_end",
                "status",
                "source_identity",
            }:
                raise SupplyDemandError("OUTPUT_EVIDENCE_LINEAGE_FIELDS_MISMATCH")
            period = row.get("period_end")
            status = row.get("status")
            if period != periods[index] or status not in {
                "EVIDENCE_AVAILABLE",
                "EVIDENCE_BLOCKED",
                "EVIDENCE_UNRESOLVED",
            }:
                raise SupplyDemandError("OUTPUT_EVIDENCE_LINEAGE_IDENTITY_MISMATCH")
            if status == "EVIDENCE_AVAILABLE":
                source = _validate_source(
                    row.get("source_identity"), market, as_of, contract, period
                )
            else:
                if row.get("source_identity") is not None:
                    raise SupplyDemandError("OUTPUT_UNAVAILABLE_SOURCE_PRESENT")
                source = None
                unavailable_statuses.add(period)
            lineage_by_period[period] = {"status": status, "source_identity": source}

        unavailable = result.get("unavailable_evidence")
        if not isinstance(unavailable, list):
            raise SupplyDemandError("OUTPUT_UNAVAILABLE_EVIDENCE_NOT_LIST")
        unavailable_periods = []
        for row in unavailable:
            if not isinstance(row, dict) or set(row) != {
                "period_end",
                "status",
                "missing_reasons",
            }:
                raise SupplyDemandError("OUTPUT_UNAVAILABLE_EVIDENCE_FIELDS_MISMATCH")
            period = row.get("period_end")
            if (
                period not in unavailable_statuses
                or row.get("status") != lineage_by_period[period]["status"]
                or not isinstance(row.get("missing_reasons"), list)
                or not row["missing_reasons"]
                or any(
                    not isinstance(reason, str) or not reason
                    for reason in row["missing_reasons"]
                )
            ):
                raise SupplyDemandError("OUTPUT_UNAVAILABLE_EVIDENCE_MISMATCH")
            unavailable_periods.append(period)
        if unavailable_periods != sorted(unavailable_statuses):
            raise SupplyDemandError("OUTPUT_UNAVAILABLE_EVIDENCE_COVERAGE_MISMATCH")

        feature_status = result.get("feature_status")
        policy_status = result.get("candidate_policy_status")
        match = result.get("candidate_policy_match")
        created = result.get("radar_case_created")
        if unavailable_statuses:
            if (
                feature_status != "UNKNOWN_EVIDENCE"
                or result.get("values") is not None
                or result.get("prior_change") is not None
                or result.get("latest_change") is not None
                or result.get("acceleration_change") is not None
                or policy_status != "NOT_EVALUATED_UNKNOWN_EVIDENCE"
                or match is not None
                or created is not False
            ):
                raise SupplyDemandError("OUTPUT_UNKNOWN_FEATURE_MISMATCH")
        else:
            values_text = result.get("values")
            if (
                feature_status != "OBSERVED"
                or not isinstance(values_text, list)
                or len(values_text) != contract["required_point_count"]
                or unavailable
            ):
                raise SupplyDemandError("OUTPUT_OBSERVED_FEATURE_MISMATCH")
            values = [
                _output_decimal(value, f"{series_id}:value", contract)
                for value in values_text
            ]
            prior = values[1] - values[0]
            latest = values[2] - values[1]
            acceleration = latest - prior
            if (
                result.get("prior_change") != _render(prior, contract)
                or result.get("latest_change") != _render(latest, contract)
                or result.get("acceleration_change") != _render(acceleration, contract)
            ):
                raise SupplyDemandError("OUTPUT_FEATURE_ARITHMETIC_MISMATCH")
            rule = _matching_rule(source_policy, result, periods[-1])
            if source_policy is None or source_policy["approval_status"] != "RATIFIED":
                expected_status = "ABSENT_OR_UNRATIFIED"
                expected_match = None
            elif rule is None:
                expected_status = "NO_EFFECTIVE_EXACT_RULE"
                expected_match = None
            elif any(
                rule[field] != result[field]
                for field in (
                    "measurement_identity",
                    "metric_type",
                    "unit",
                    "frequency",
                    "comparison_basis",
                )
            ):
                expected_status = "EXACT_RULE_IDENTITY_MISMATCH"
                expected_match = False
            else:
                expected_status = "RATIFIED_EXACT_RULE_APPLIED"
                sign = (
                    Decimal(1)
                    if rule["improvement_direction"] == "HIGHER_IS_IMPROVEMENT"
                    else Decimal(-1)
                )
                expected_match = (
                    sign * latest >= Decimal(rule["minimum_latest_change"])
                    and sign * acceleration
                    >= Decimal(rule["minimum_acceleration_change"])
                )
            allowed_policy_statuses = {
                "ABSENT_OR_UNRATIFIED",
                "NO_EFFECTIVE_EXACT_RULE",
                "EXACT_RULE_IDENTITY_MISMATCH",
                "RATIFIED_EXACT_RULE_APPLIED",
            }
            if policy_status not in allowed_policy_statuses:
                raise SupplyDemandError("OUTPUT_POLICY_STATUS_INVALID")
            if policy_status != expected_status:
                raise SupplyDemandError("OUTPUT_POLICY_STATUS_MISMATCH")
            if match is not expected_match or created is not (expected_match is True):
                raise SupplyDemandError("OUTPUT_POLICY_RESULT_MISMATCH")
            if created:
                seed = {
                    "policy_id": policy["policy_id"],
                    "market": market,
                    "series_id": series_id,
                    "last_period": periods[-1],
                }
                case_id = "RADAR-SD-" + payload_sha256(seed)[:16].upper()
                result_by_case_id[case_id] = {
                    "result": result,
                    "lineage": lineage_by_period,
                    "rule": rule,
                }
        result_keys.append((market, series_id))
    if result_keys != sorted(set(result_keys)):
        raise SupplyDemandError("OUTPUT_SERIES_ORDER_OR_DUPLICATE_INVALID")

    cases = packet.get("cases")
    if not isinstance(cases, list):
        raise SupplyDemandError("OUTPUT_CASES_NOT_LIST")
    case_fields = {
        "schema_version",
        "case_id",
        "market",
        "asset_id",
        "observation_date",
        "why_found",
        "confirmed_evidence",
        "candidate_policy",
        "importance",
        "candidate_rank",
        "investable_eligible",
        "stage_transition",
        "action",
    }
    case_ids = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != case_fields:
            raise SupplyDemandError("OUTPUT_CASE_FIELDS_MISMATCH")
        case_id = case.get("case_id")
        linked = result_by_case_id.get(case_id)
        if linked is None:
            raise SupplyDemandError("OUTPUT_CASE_IDENTITY_MISMATCH")
        result = linked["result"]
        rule = linked["rule"]
        why = case.get("why_found")
        why_fields = {
            "measurement_identity",
            "metric_type",
            "unit",
            "frequency",
            "comparison_basis",
            "values",
            "prior_change",
            "latest_change",
            "acceleration_change",
            "improvement_direction",
            "minimum_latest_change",
            "minimum_acceleration_change",
            "candidate_logic",
        }
        if not isinstance(why, dict) or set(why) != why_fields:
            raise SupplyDemandError("OUTPUT_CASE_REASON_FIELDS_MISMATCH")
        sign = (
            Decimal(1)
            if why.get("improvement_direction") == "HIGHER_IS_IMPROVEMENT"
            else Decimal(-1)
            if why.get("improvement_direction") == "LOWER_IS_IMPROVEMENT"
            else None
        )
        minimum_latest = _decimal(
            why.get("minimum_latest_change"), case_id, nonnegative=True
        )
        minimum_acceleration = _decimal(
            why.get("minimum_acceleration_change"), case_id, nonnegative=True
        )
        if (
            case.get("schema_version") != "supply_demand_case/1"
            or case.get("market") != result["market"]
            or case.get("asset_id") != result["asset_id"]
            or case.get("observation_date") != result["expected_periods"][-1]
            or any(
                why.get(field) != result[field]
                for field in (
                    "measurement_identity",
                    "metric_type",
                    "unit",
                    "frequency",
                    "comparison_basis",
                    "values",
                    "prior_change",
                    "latest_change",
                    "acceleration_change",
                )
            )
            or sign is None
            or why.get("improvement_direction") != rule["improvement_direction"]
            or why.get("minimum_latest_change") != rule["minimum_latest_change"]
            or why.get("minimum_acceleration_change")
            != rule["minimum_acceleration_change"]
            or why.get("candidate_logic") != contract["candidate_logic"]
            or sign * Decimal(result["latest_change"]) < minimum_latest
            or sign * Decimal(result["acceleration_change"]) < minimum_acceleration
        ):
            raise SupplyDemandError("OUTPUT_CASE_REASON_DERIVATION_MISMATCH")
        evidence = case.get("confirmed_evidence")
        if not isinstance(evidence, list) or len(evidence) != 3:
            raise SupplyDemandError("OUTPUT_CASE_EVIDENCE_COUNT_MISMATCH")
        for index, row in enumerate(evidence):
            period = result["expected_periods"][index]
            if (
                not isinstance(row, dict)
                or set(row) != {"period_end", "numeric_value", "source_identity"}
                or row.get("period_end") != period
                or _render(_decimal(row.get("numeric_value"), case_id), contract)
                != result["values"][index]
                or row.get("source_identity")
                != linked["lineage"][period]["source_identity"]
            ):
                raise SupplyDemandError("OUTPUT_CASE_EVIDENCE_MISMATCH")
        case_policy = case.get("candidate_policy")
        if (
            policy is None
            or policy["approval_status"] != "RATIFIED"
            or not isinstance(case_policy, dict)
            or set(case_policy) != {
                "policy_id",
                "policy_sha256",
                "ratified_by",
                "ratified_at_utc",
            }
            or case_policy.get("policy_id") != policy["policy_id"]
            or case_policy.get("policy_sha256") != policy["policy_sha256"]
            or case_policy.get("ratified_by") != source_policy["ratified_by"]
            or case_policy.get("ratified_at_utc") != source_policy["ratified_at_utc"]
        ):
            raise SupplyDemandError("OUTPUT_CASE_POLICY_LINEAGE_MISMATCH")
        if (
            case.get("importance") != "UNRATIFIED"
            or case.get("candidate_rank") is not None
            or case.get("investable_eligible") is not False
            or case.get("stage_transition") is not None
            or case.get("action") is not None
        ):
            raise SupplyDemandError("OUTPUT_CASE_AUTHORITY_EXPANSION")
        case_ids.append(case_id)
    if case_ids != sorted(set(case_ids)) or set(case_ids) != set(result_by_case_id):
        raise SupplyDemandError("OUTPUT_CASE_SET_OR_ORDER_MISMATCH")

    expected_boundaries = [
        "DEFAULT_CANDIDATE_POLICY_ABSENT",
        "IMPROVEMENT_DIRECTION_UNRATIFIED",
        "MINIMUM_CHANGE_UNRATIFIED",
        "CROSS_MARKET_COMPARABILITY_UNRATIFIED",
        "SOURCE_HIERARCHY_UNRATIFIED",
        "CANDIDATE_RANKING_UNRATIFIED",
        "KOREA_SOURCE_RELEASE_TIME_UNVERIFIED",
        "US_METRIC_SERIES_NOT_SELECTED",
        "LIVE_RADAR_POPULATION_PARTIAL_CRYPTO_ONLY",
    ]
    if (
        type(packet.get("series_count")) is not int
        or packet["series_count"] != len(results)
        or type(packet.get("case_count")) is not int
        or packet["case_count"] != len(cases)
        or packet.get("source_coverage") != contract["source_coverage"]
        or packet.get("policy_status") != contract["policy_status"]
        or packet.get("authority") != contract["authority"]
        or packet.get("unresolved_boundaries") != expected_boundaries
    ):
        raise SupplyDemandError("OUTPUT_SUMMARY_OR_BOUNDARY_MISMATCH")
    return copy.deepcopy(packet)


def build_packet(value: dict, candidate_policy: dict | None = None, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    policy = _validate_policy(candidate_policy, contract)
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise SupplyDemandError("INPUT_SCHEMA_MISMATCH")
    if set(value) != {"schema_version", "as_of_utc", "series"}:
        raise SupplyDemandError("INPUT_FIELDS_MISMATCH")
    as_of_utc = value.get("as_of_utc")
    if not _valid_utc(as_of_utc):
        raise SupplyDemandError("AS_OF_UTC_INVALID")
    if policy is not None and policy["approval_status"] == "RATIFIED" and _utc(policy["ratified_at_utc"]) > _utc(as_of_utc):
        raise SupplyDemandError("POLICY_RATIFIED_AFTER_AS_OF")
    raw_series = value.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise SupplyDemandError("SERIES_EMPTY")
    results = []
    cases = []
    seen = set()
    for series in raw_series:
        result, case = _series_result(series, _utc(as_of_utc), policy, contract)
        key = (result["market"], result["series_id"])
        if key in seen:
            raise SupplyDemandError(f"SERIES_DUPLICATE:{key[0]}:{key[1]}")
        seen.add(key)
        results.append(result)
        if case is not None:
            cases.append(case)
    results.sort(key=lambda item: (item["market"], item["series_id"]))
    cases.sort(key=lambda item: item["case_id"])
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"], "as_of_utc": as_of_utc,
        "status": "SUPPLY_DEMAND_FEATURES_OBSERVED", "series_count": len(results),
        "case_count": len(cases),
        "candidate_policy": None if policy is None else {
            "policy_id": policy["policy_id"], "approval_status": policy["approval_status"],
            "policy_sha256": payload_sha256(policy),
        },
        "source_policy": copy.deepcopy(policy),
        "series_results": results, "cases": cases,
        "source_coverage": copy.deepcopy(contract["source_coverage"]),
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "DEFAULT_CANDIDATE_POLICY_ABSENT", "IMPROVEMENT_DIRECTION_UNRATIFIED",
            "MINIMUM_CHANGE_UNRATIFIED", "CROSS_MARKET_COMPARABILITY_UNRATIFIED",
            "SOURCE_HIERARCHY_UNRATIFIED", "CANDIDATE_RANKING_UNRATIFIED",
            "KOREA_SOURCE_RELEASE_TIME_UNVERIFIED", "US_METRIC_SERIES_NOT_SELECTED",
            "LIVE_RADAR_POPULATION_PARTIAL_CRYPTO_ONLY",
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
        raise SupplyDemandError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
        packet = build_packet(
            _read_json(input_path),
            _read_json(policy_path) if policy_path is not None else None,
        )
        write_json_atomic(output_path, packet)
        return 0
    except (SupplyDemandError, OSError, TypeError, ValueError) as exc:
        print(f"supply-demand radar failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a policy-gated supply-demand radar packet")
    parser.add_argument("input", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.out, args.policy)


if __name__ == "__main__":
    raise SystemExit(main())
