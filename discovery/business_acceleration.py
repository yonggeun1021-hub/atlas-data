#!/usr/bin/env python3
"""P3-05 comparable published-growth Business Acceleration radar.

The helper consumes exactly three ordered evidence_envelope/1 observations for
one metric and records a radar case only when both consecutive published growth
rates increased.  It does not infer metrics from text, rank sources or companies,
set an importance threshold, promote an Atlas stage, or create an action/order.
"""
from __future__ import annotations

import argparse
import calendar
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
CONTRACT_PATH = ROOT / "config" / "business_acceleration_radar_contract.json"

INPUT_SCHEMA_VERSION = "business_acceleration_radar_input/1"
OUTPUT_SCHEMA_VERSION = "business_acceleration_radar_packet/2"
EVIDENCE_SCHEMA_VERSION = "evidence_envelope/1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class BusinessAccelerationError(ValueError):
    """Fail-closed Business Acceleration contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BusinessAccelerationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_authority = {
        "radar_case_recording_only": True,
        "source_ranking_authorized": False,
        "importance_ranking_authorized": False,
        "candidate_ranking_authorized": False,
        "stage_promotion_authorized": False,
        "rule_evaluation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    expected_policy = {
        "source_hierarchy": "UNRATIFIED",
        "cross_company_comparability": "UNRATIFIED",
        "importance_threshold": "UNRATIFIED",
        "candidate_ranking": "UNRATIFIED",
        "automatic_stage_promotion": "PROHIBITED",
    }
    expected_patterns = {
        "TWO_STEP_ACCELERATION_OBSERVED": (
            "both consecutive published growth-rate changes are greater than zero"
        ),
        "LATEST_STEP_UP_ONLY": (
            "latest change is greater than zero but prior change is not"
        ),
        "LATEST_STEP_NOT_UP": "latest change is zero or less",
        "UNKNOWN_EVIDENCE": "one or more evidence points are unavailable",
    }
    expected_sources = {
        "dart_open_api": "PARTIAL",
        "microsoft_sec_issuer_disclosure": "PARTIAL",
        "msft_official_earnings_release": "PARTIAL",
        "sec_edgar": "PARTIAL",
        "tsmc_investor_relations": "PARTIAL",
        "tsmc_ir_monthly_revenue": "PARTIAL",
    }
    expected_hosts = {
        "dart_open_api": ["opendart.fss.or.kr"],
        "microsoft_sec_issuer_disclosure": ["www.sec.gov"],
        "msft_official_earnings_release": ["www.sec.gov"],
        "sec_edgar": ["www.sec.gov"],
        "tsmc_investor_relations": ["investor.tsmc.com"],
        "tsmc_ir_monthly_revenue": ["investor.tsmc.com"],
    }
    expected = {
        "schema_version": 1,
        "contract_version": "business_acceleration_radar/2",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "allowed_metric_types": ["GUIDANCE_GROWTH", "ORDER_GROWTH", "REVENUE_GROWTH"],
        "allowed_frequencies": ["MONTHLY", "QUARTERLY"],
        "required_point_count": 3,
        "required_unit": "pct",
        "output_decimal_places": 12,
        "pattern_contract": expected_patterns,
        "source_coverage": expected_sources,
        "source_hosts": expected_hosts,
        "policy_status": expected_policy,
        "authority": expected_authority,
    }
    if not isinstance(value, dict):
        raise BusinessAccelerationError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise BusinessAccelerationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise BusinessAccelerationError("CONTRACT_FIELDS_MISMATCH")
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


def _as_datetime(value: str, end_of_date: bool = False) -> dt.datetime:
    if _valid_utc(value):
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    if _valid_date(value):
        parsed = dt.datetime.combine(dt.date.fromisoformat(value), dt.time())
        return parsed + (dt.timedelta(days=1) - dt.timedelta(microseconds=1) if end_of_date else dt.timedelta())
    raise BusinessAccelerationError(f"TEMPORAL_VALUE_INVALID:{value!r}")


def _decimal(value, context: str) -> Decimal:
    if not isinstance(value, str):
        raise BusinessAccelerationError(f"NUMERIC_VALUE_NOT_STRING:{context}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise BusinessAccelerationError(f"NUMERIC_VALUE_INVALID:{context}") from exc
    if not result.is_finite():
        raise BusinessAccelerationError(f"NUMERIC_VALUE_INVALID:{context}")
    return result


def _render(value: Decimal, places: int) -> str:
    with localcontext() as context:
        context.prec = 50
        quantum = Decimal(1).scaleb(-places)
        return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def _validate_source(source: dict, as_of: dt.datetime, contract: dict, context: str) -> dict:
    if not isinstance(source, dict):
        raise BusinessAccelerationError(f"SOURCE_IDENTITY_NOT_OBJECT:{context}")
    source_id = source.get("source_id")
    if source_id not in contract["source_coverage"]:
        raise BusinessAccelerationError(f"SOURCE_ID_NOT_REGISTERED:{context}:{source_id}")
    parsed = urlparse(str(source.get("source_url") or ""))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in contract["source_hosts"][source_id]
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise BusinessAccelerationError(f"SOURCE_URL_INVALID:{context}")
    if not isinstance(source.get("source_sha256"), str) or SHA256_RE.fullmatch(
        source["source_sha256"]
    ) is None:
        raise BusinessAccelerationError(f"SOURCE_SHA256_INVALID:{context}")
    available_at = source.get("available_at")
    retrieved_at = source.get("retrieved_at_utc")
    if not (_valid_date(available_at) or _valid_utc(available_at)):
        raise BusinessAccelerationError(f"AVAILABLE_AT_INVALID:{context}")
    if not _valid_utc(retrieved_at):
        raise BusinessAccelerationError(f"RETRIEVED_AT_INVALID:{context}")
    retrieved = _as_datetime(retrieved_at)
    if _valid_date(available_at):
        available_after_retrieval = dt.date.fromisoformat(available_at) > retrieved.date()
        available_after_as_of = dt.date.fromisoformat(available_at) > as_of.date()
    else:
        available = _as_datetime(available_at)
        available_after_retrieval = available > retrieved
        available_after_as_of = available > as_of
    if available_after_retrieval or available_after_as_of or retrieved > as_of:
        raise BusinessAccelerationError(f"TEMPORAL_ORDER_INVALID:{context}")
    return copy.deepcopy(source)


def _next_period(previous: str, current: str, frequency: str) -> bool:
    left = dt.date.fromisoformat(previous)
    right = dt.date.fromisoformat(current)
    if left.day != calendar.monthrange(left.year, left.month)[1]:
        return False
    if right.day != calendar.monthrange(right.year, right.month)[1]:
        return False
    month_step = 1 if frequency == "MONTHLY" else 3
    expected_month = left.month - 1 + month_step
    expected_year = left.year + expected_month // 12
    expected_month = expected_month % 12 + 1
    expected_day = calendar.monthrange(expected_year, expected_month)[1]
    return right == dt.date(expected_year, expected_month, expected_day)


def _validate_envelope(
    envelope: dict,
    subject: str,
    measurement: str,
    as_of: dt.datetime,
    contract: dict,
) -> tuple[dict, Decimal | None]:
    if not isinstance(envelope, dict) or envelope.get("schema_version") != (
        EVIDENCE_SCHEMA_VERSION
    ):
        raise BusinessAccelerationError("EVIDENCE_SCHEMA_MISMATCH")
    period = envelope.get("economic_period_end")
    if envelope.get("subject") != subject:
        raise BusinessAccelerationError(f"EVIDENCE_SUBJECT_MISMATCH:{period}")
    if envelope.get("measurement_identity") != measurement:
        raise BusinessAccelerationError(f"EVIDENCE_MEASUREMENT_MISMATCH:{period}")
    if not _valid_date(period):
        raise BusinessAccelerationError(f"EVIDENCE_PERIOD_INVALID:{period}")
    status = envelope.get("status")
    if status not in {"EVIDENCE_AVAILABLE", "EVIDENCE_BLOCKED", "EVIDENCE_UNRESOLVED"}:
        raise BusinessAccelerationError(f"EVIDENCE_STATUS_INVALID:{period}:{status}")
    if status != "EVIDENCE_AVAILABLE":
        if envelope.get("consumable") is not False or envelope.get("observation") is not None:
            raise BusinessAccelerationError(f"EVIDENCE_UNAVAILABLE_INCONSISTENT:{period}")
        if not (envelope.get("reasons") or envelope.get("blocked_by")):
            raise BusinessAccelerationError(f"EVIDENCE_UNAVAILABLE_REASON_ABSENT:{period}")
        return copy.deepcopy(envelope), None
    if envelope.get("consumable") is not True or envelope.get("blocked_by"):
        raise BusinessAccelerationError(f"EVIDENCE_AVAILABLE_INCONSISTENT:{period}")
    if envelope.get("acquisition_provenance_present") is not True:
        raise BusinessAccelerationError(f"EVIDENCE_PROVENANCE_ABSENT:{period}")
    if not isinstance(envelope.get("audit_provenance"), dict) or not envelope[
        "audit_provenance"
    ]:
        raise BusinessAccelerationError(f"EVIDENCE_AUDIT_PROVENANCE_INVALID:{period}")
    source = _validate_source(envelope.get("source_identity"), as_of, contract, period)
    observation = envelope.get("observation")
    if not isinstance(observation, dict):
        raise BusinessAccelerationError(f"EVIDENCE_OBSERVATION_ABSENT:{period}")
    if observation.get("unit") != contract["required_unit"]:
        raise BusinessAccelerationError(f"EVIDENCE_UNIT_MISMATCH:{period}")
    value = _decimal(observation.get("numeric_value"), period)
    validated = copy.deepcopy(envelope)
    validated["source_identity"] = source
    return validated, value


def _series_result(series: dict, as_of: dt.datetime, contract: dict) -> tuple[dict, dict | None]:
    if not isinstance(series, dict):
        raise BusinessAccelerationError("SERIES_NOT_OBJECT")
    series_id = series.get("series_id")
    asset_id = series.get("asset_id")
    subject = series.get("subject")
    measurement = series.get("measurement_identity")
    metric_type = series.get("metric_type")
    frequency = series.get("frequency")
    comparison_basis = series.get("comparison_basis")
    if not isinstance(series_id, str) or TOKEN_RE.fullmatch(series_id) is None:
        raise BusinessAccelerationError("SERIES_ID_INVALID")
    if not isinstance(asset_id, str) or TOKEN_RE.fullmatch(asset_id) is None:
        raise BusinessAccelerationError(f"ASSET_ID_INVALID:{series_id}")
    if not all(isinstance(item, str) and item.strip() == item and item for item in (subject, measurement, comparison_basis)):
        raise BusinessAccelerationError(f"SERIES_IDENTITY_INVALID:{series_id}")
    if metric_type not in contract["allowed_metric_types"]:
        raise BusinessAccelerationError(f"METRIC_TYPE_INVALID:{series_id}")
    if frequency not in contract["allowed_frequencies"]:
        raise BusinessAccelerationError(f"FREQUENCY_INVALID:{series_id}")
    points = series.get("evidence_points")
    if not isinstance(points, list) or len(points) != contract["required_point_count"]:
        raise BusinessAccelerationError(f"EVIDENCE_POINT_COUNT_INVALID:{series_id}")
    checked = [
        _validate_envelope(point, subject, measurement, as_of, contract) for point in points
    ]
    checked.sort(key=lambda item: item[0]["economic_period_end"])
    periods = [item[0]["economic_period_end"] for item in checked]
    if len(set(periods)) != len(periods):
        raise BusinessAccelerationError(f"EVIDENCE_PERIOD_DUPLICATE:{series_id}")
    if not all(_next_period(periods[index], periods[index + 1], frequency) for index in range(2)):
        raise BusinessAccelerationError(f"EVIDENCE_PERIOD_NOT_CONSECUTIVE:{series_id}")
    unavailable = [
        {
            "economic_period_end": item[0]["economic_period_end"],
            "status": item[0]["status"],
            "reasons": copy.deepcopy(item[0].get("reasons") or item[0].get("blocked_by") or []),
        }
        for item in checked
        if item[1] is None
    ]
    base = {
        "series_id": series_id,
        "asset_id": asset_id,
        "subject": subject,
        "metric_type": metric_type,
        "measurement_identity": measurement,
        "frequency": frequency,
        "comparison_basis": comparison_basis,
        "economic_periods": periods,
        "importance": "UNRATIFIED",
        "candidate_rank": None,
        "candidate_eligible": False,
        "stage_transition": None,
    }
    if unavailable:
        return {
            **base,
            "pattern": "UNKNOWN_EVIDENCE",
            "values_pct": None,
            "prior_change_pp": None,
            "latest_change_pp": None,
            "acceleration_change_pp": None,
            "evidence_source": None,
            "unavailable_evidence": unavailable,
            "radar_case_created": False,
        }, None
    values = [item[1] for item in checked]
    prior_change = values[1] - values[0]
    latest_change = values[2] - values[1]
    if prior_change > 0 and latest_change > 0:
        pattern = "TWO_STEP_ACCELERATION_OBSERVED"
    elif latest_change > 0:
        pattern = "LATEST_STEP_UP_ONLY"
    else:
        pattern = "LATEST_STEP_NOT_UP"
    places = contract["output_decimal_places"]
    # Persisted regardless of whether a case is created -- a non-case
    # series_result must remain standalone-reprovable too, so the minimum
    # sufficient frozen source snapshot (raw numeric_value + source_identity
    # per period) is kept here, not only in a created case's
    # confirmed_evidence.
    evidence_source = [
        {
            "economic_period_end": item[0]["economic_period_end"],
            "numeric_value": item[0]["observation"]["numeric_value"],
            "unit": item[0]["observation"]["unit"],
            "source_identity": copy.deepcopy(item[0]["source_identity"]),
        }
        for item in checked
    ]
    result = {
        **base,
        "pattern": pattern,
        "values_pct": [_render(value, places) for value in values],
        "prior_change_pp": _render(prior_change, places),
        "latest_change_pp": _render(latest_change, places),
        "acceleration_change_pp": _render(latest_change - prior_change, places),
        "evidence_source": copy.deepcopy(evidence_source),
        "unavailable_evidence": [],
        "radar_case_created": pattern == "TWO_STEP_ACCELERATION_OBSERVED",
    }
    if not result["radar_case_created"]:
        return result, None
    case_seed = {
        "series_id": series_id,
        "asset_id": asset_id,
        "last_period": periods[-1],
        "pattern": pattern,
    }
    case_id = "RADAR-BA-" + payload_sha256(case_seed)[:16].upper()
    case = {
        "schema_version": "business_acceleration_case/1",
        "case_id": case_id,
        "asset_id": asset_id,
        "subject": subject,
        "why_found": {
            "pattern": pattern,
            "published_growth_values_pct": result["values_pct"],
            "prior_change_pp": result["prior_change_pp"],
            "latest_change_pp": result["latest_change_pp"],
            "comparison_basis": comparison_basis,
        },
        "confirmed_evidence": copy.deepcopy(evidence_source),
        "unconfirmed_items": [
            "IMPORTANCE_THRESHOLD_UNRATIFIED",
            "CROSS_COMPANY_COMPARABILITY_UNRATIFIED",
            "CANDIDATE_RANKING_UNRATIFIED",
        ],
        "importance": "UNRATIFIED",
        "candidate_rank": None,
        "candidate_eligible": False,
        "stage_transition": None,
        "action": None,
    }
    return result, case


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate persisted radar arithmetic and retained case evidence semantics."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet_fields = {
        "schema_version",
        "contract_version",
        "as_of_utc",
        "status",
        "series_count",
        "case_count",
        "pattern_counts",
        "series_results",
        "cases",
        "source_coverage",
        "policy_status",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != packet_fields:
        raise BusinessAccelerationError("OUTPUT_FIELDS_MISMATCH")
    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise BusinessAccelerationError("OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != digest:
        raise BusinessAccelerationError("OUTPUT_SHA256_MISMATCH")
    as_of_utc = packet.get("as_of_utc")
    if (
        packet.get("schema_version") != OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "RADAR_CAPABILITY_RESULT"
        or not _valid_utc(as_of_utc)
    ):
        raise BusinessAccelerationError("OUTPUT_IDENTITY_MISMATCH")
    as_of = _as_datetime(as_of_utc)

    results = packet.get("series_results")
    if not isinstance(results, list):
        raise BusinessAccelerationError("OUTPUT_SERIES_RESULTS_NOT_LIST")
    result_fields = {
        "series_id",
        "asset_id",
        "subject",
        "metric_type",
        "measurement_identity",
        "frequency",
        "comparison_basis",
        "economic_periods",
        "importance",
        "candidate_rank",
        "candidate_eligible",
        "stage_transition",
        "pattern",
        "values_pct",
        "prior_change_pp",
        "latest_change_pp",
        "acceleration_change_pp",
        "evidence_source",
        "unavailable_evidence",
        "radar_case_created",
    }
    pattern_counts = {key: 0 for key in contract["pattern_contract"]}
    result_by_case_id = {}
    series_ids = []
    places = contract["output_decimal_places"]
    for result in results:
        if not isinstance(result, dict) or set(result) != result_fields:
            raise BusinessAccelerationError("OUTPUT_SERIES_RESULT_FIELDS_MISMATCH")
        series_id = result.get("series_id")
        asset_id = result.get("asset_id")
        subject = result.get("subject")
        measurement = result.get("measurement_identity")
        comparison_basis = result.get("comparison_basis")
        frequency = result.get("frequency")
        pattern = result.get("pattern")
        if (
            not isinstance(series_id, str)
            or TOKEN_RE.fullmatch(series_id) is None
            or not isinstance(asset_id, str)
            or TOKEN_RE.fullmatch(asset_id) is None
            or not all(
                isinstance(value, str) and value and value.strip() == value
                for value in (subject, measurement, comparison_basis)
            )
            or result.get("metric_type") not in contract["allowed_metric_types"]
            or frequency not in contract["allowed_frequencies"]
            or pattern not in contract["pattern_contract"]
        ):
            raise BusinessAccelerationError("OUTPUT_SERIES_IDENTITY_MISMATCH")
        periods = result.get("economic_periods")
        if (
            not isinstance(periods, list)
            or len(periods) != contract["required_point_count"]
            or any(not _valid_date(period) for period in periods)
            or periods != sorted(set(periods))
            or not all(
                _next_period(periods[index], periods[index + 1], frequency)
                for index in range(2)
            )
        ):
            raise BusinessAccelerationError("OUTPUT_SERIES_PERIODS_MISMATCH")
        if (
            result.get("importance") != "UNRATIFIED"
            or result.get("candidate_rank") is not None
            or result.get("candidate_eligible") is not False
            or result.get("stage_transition") is not None
        ):
            raise BusinessAccelerationError("OUTPUT_SERIES_AUTHORITY_EXPANSION")

        unavailable = result.get("unavailable_evidence")
        if pattern == "UNKNOWN_EVIDENCE":
            if (
                result.get("values_pct") is not None
                or result.get("prior_change_pp") is not None
                or result.get("latest_change_pp") is not None
                or result.get("acceleration_change_pp") is not None
                or result.get("evidence_source") is not None
                or result.get("radar_case_created") is not False
                or not isinstance(unavailable, list)
                or not unavailable
            ):
                raise BusinessAccelerationError("OUTPUT_UNKNOWN_PATTERN_MISMATCH")
            unavailable_fields = {"economic_period_end", "status", "reasons"}
            unavailable_periods = []
            for row in unavailable:
                if (
                    not isinstance(row, dict)
                    or set(row) != unavailable_fields
                    or row.get("economic_period_end") not in periods
                    or row.get("status")
                    not in {"EVIDENCE_BLOCKED", "EVIDENCE_UNRESOLVED"}
                    or not isinstance(row.get("reasons"), list)
                    or not row["reasons"]
                    or any(not isinstance(reason, str) or not reason for reason in row["reasons"])
                ):
                    raise BusinessAccelerationError("OUTPUT_UNAVAILABLE_EVIDENCE_MISMATCH")
                unavailable_periods.append(row["economic_period_end"])
            if unavailable_periods != sorted(set(unavailable_periods)):
                raise BusinessAccelerationError("OUTPUT_UNAVAILABLE_EVIDENCE_ORDER_MISMATCH")
        else:
            values_text = result.get("values_pct")
            if (
                not isinstance(values_text, list)
                or len(values_text) != contract["required_point_count"]
                or unavailable != []
            ):
                raise BusinessAccelerationError("OUTPUT_VALUES_MISMATCH")
            values = [_decimal(value, series_id) for value in values_text]
            if values_text != [_render(value, places) for value in values]:
                raise BusinessAccelerationError("OUTPUT_VALUE_CANONICALIZATION_MISMATCH")
            # Independently re-prove that values_pct is backed by genuine,
            # correctly-sourced, temporally-ordered raw evidence -- not just
            # internally self-consistent numbers. This runs for every
            # non-UNKNOWN_EVIDENCE series, not only ones that created a case,
            # closing the standalone-source-completeness gap for non-case
            # packets.
            evidence_source = result.get("evidence_source")
            evidence_fields = {
                "economic_period_end", "numeric_value", "unit", "source_identity",
            }
            if (
                not isinstance(evidence_source, list)
                or len(evidence_source) != contract["required_point_count"]
            ):
                raise BusinessAccelerationError(f"OUTPUT_EVIDENCE_SOURCE_MISMATCH:{series_id}")
            for index, row in enumerate(evidence_source):
                if (
                    not isinstance(row, dict)
                    or set(row) != evidence_fields
                    or row.get("economic_period_end") != periods[index]
                    or row.get("unit") != contract["required_unit"]
                    or _render(_decimal(row.get("numeric_value"), series_id), places)
                    != values_text[index]
                ):
                    raise BusinessAccelerationError(
                        f"OUTPUT_EVIDENCE_SOURCE_VALUE_MISMATCH:{series_id}"
                    )
                _validate_source(row.get("source_identity"), as_of, contract, series_id)
            prior = values[1] - values[0]
            latest = values[2] - values[1]
            expected_pattern = (
                "TWO_STEP_ACCELERATION_OBSERVED"
                if prior > 0 and latest > 0
                else "LATEST_STEP_UP_ONLY"
                if latest > 0
                else "LATEST_STEP_NOT_UP"
            )
            if (
                pattern != expected_pattern
                or result.get("prior_change_pp") != _render(prior, places)
                or result.get("latest_change_pp") != _render(latest, places)
                or result.get("acceleration_change_pp")
                != _render(latest - prior, places)
                or result.get("radar_case_created")
                is not (pattern == "TWO_STEP_ACCELERATION_OBSERVED")
            ):
                raise BusinessAccelerationError("OUTPUT_PATTERN_DERIVATION_MISMATCH")
        if result["radar_case_created"]:
            seed = {
                "series_id": series_id,
                "asset_id": asset_id,
                "last_period": periods[-1],
                "pattern": pattern,
            }
            case_id = "RADAR-BA-" + payload_sha256(seed)[:16].upper()
            result_by_case_id[case_id] = result
        series_ids.append(series_id)
        pattern_counts[pattern] += 1
    if series_ids != sorted(set(series_ids)):
        raise BusinessAccelerationError("OUTPUT_SERIES_ORDER_OR_DUPLICATE_INVALID")

    cases = packet.get("cases")
    if not isinstance(cases, list):
        raise BusinessAccelerationError("OUTPUT_CASES_NOT_LIST")
    case_fields = {
        "schema_version",
        "case_id",
        "asset_id",
        "subject",
        "why_found",
        "confirmed_evidence",
        "unconfirmed_items",
        "importance",
        "candidate_rank",
        "candidate_eligible",
        "stage_transition",
        "action",
    }
    case_ids = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != case_fields:
            raise BusinessAccelerationError("OUTPUT_CASE_FIELDS_MISMATCH")
        case_id = case.get("case_id")
        result = result_by_case_id.get(case_id)
        if (
            result is None
            or case.get("schema_version") != "business_acceleration_case/1"
            or case.get("asset_id") != result["asset_id"]
            or case.get("subject") != result["subject"]
        ):
            raise BusinessAccelerationError("OUTPUT_CASE_IDENTITY_MISMATCH")
        expected_why = {
            "pattern": result["pattern"],
            "published_growth_values_pct": result["values_pct"],
            "prior_change_pp": result["prior_change_pp"],
            "latest_change_pp": result["latest_change_pp"],
            "comparison_basis": result["comparison_basis"],
        }
        if case.get("why_found") != expected_why:
            raise BusinessAccelerationError("OUTPUT_CASE_REASON_DERIVATION_MISMATCH")
        evidence_rows = case.get("confirmed_evidence")
        evidence_fields = {
            "economic_period_end",
            "numeric_value",
            "unit",
            "source_identity",
        }
        if not isinstance(evidence_rows, list) or len(evidence_rows) != 3:
            raise BusinessAccelerationError("OUTPUT_CASE_EVIDENCE_COUNT_MISMATCH")
        for index, row in enumerate(evidence_rows):
            if (
                not isinstance(row, dict)
                or set(row) != evidence_fields
                or row.get("economic_period_end") != result["economic_periods"][index]
                or row.get("unit") != contract["required_unit"]
                or _render(_decimal(row.get("numeric_value"), case_id), places)
                != result["values_pct"][index]
            ):
                raise BusinessAccelerationError("OUTPUT_CASE_EVIDENCE_VALUE_MISMATCH")
            _validate_source(row.get("source_identity"), as_of, contract, case_id)
        if (
            case.get("unconfirmed_items")
            != [
                "IMPORTANCE_THRESHOLD_UNRATIFIED",
                "CROSS_COMPANY_COMPARABILITY_UNRATIFIED",
                "CANDIDATE_RANKING_UNRATIFIED",
            ]
            or case.get("importance") != "UNRATIFIED"
            or case.get("candidate_rank") is not None
            or case.get("candidate_eligible") is not False
            or case.get("stage_transition") is not None
            or case.get("action") is not None
        ):
            raise BusinessAccelerationError("OUTPUT_CASE_AUTHORITY_EXPANSION")
        case_ids.append(case_id)
    if case_ids != sorted(set(case_ids)) or set(case_ids) != set(result_by_case_id):
        raise BusinessAccelerationError("OUTPUT_CASE_SET_OR_ORDER_MISMATCH")

    expected_boundaries = [
        "COMPLETE_CROSS_COMPANY_EVIDENCE_NETWORK_UNAVAILABLE",
        "SOURCE_HIERARCHY_UNRATIFIED",
        "IMPORTANCE_THRESHOLD_UNRATIFIED",
        "CROSS_COMPANY_COMPARABILITY_UNRATIFIED",
        "CANDIDATE_RANKING_UNRATIFIED",
        "LIVE_RADAR_POPULATION_NOT_IMPLEMENTED",
    ]
    if (
        type(packet.get("series_count")) is not int
        or packet["series_count"] != len(results)
        or type(packet.get("case_count")) is not int
        or packet["case_count"] != len(cases)
        or packet.get("pattern_counts") != pattern_counts
        or packet.get("source_coverage") != contract["source_coverage"]
        or packet.get("policy_status") != contract["policy_status"]
        or packet.get("authority") != contract["authority"]
        or packet.get("unresolved_boundaries") != expected_boundaries
    ):
        raise BusinessAccelerationError("OUTPUT_SUMMARY_OR_BOUNDARY_MISMATCH")
    return copy.deepcopy(packet)


def build_packet(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise BusinessAccelerationError("INPUT_SCHEMA_MISMATCH")
    as_of_utc = value.get("as_of_utc")
    if not _valid_utc(as_of_utc):
        raise BusinessAccelerationError("AS_OF_UTC_INVALID")
    as_of = _as_datetime(as_of_utc)
    series = value.get("series")
    if not isinstance(series, list):
        raise BusinessAccelerationError("SERIES_NOT_LIST")
    results = []
    cases = []
    seen = set()
    for item in series:
        result, case = _series_result(item, as_of, contract)
        if result["series_id"] in seen:
            raise BusinessAccelerationError(f"SERIES_ID_DUPLICATE:{result['series_id']}")
        seen.add(result["series_id"])
        results.append(result)
        if case is not None:
            cases.append(case)
    results.sort(key=lambda item: item["series_id"])
    cases.sort(key=lambda item: item["case_id"])
    patterns = {key: 0 for key in contract["pattern_contract"]}
    for result in results:
        patterns[result["pattern"]] += 1
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "as_of_utc": as_of_utc,
        "status": "RADAR_CAPABILITY_RESULT",
        "series_count": len(results),
        "case_count": len(cases),
        "pattern_counts": patterns,
        "series_results": results,
        "cases": cases,
        "source_coverage": copy.deepcopy(contract["source_coverage"]),
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "COMPLETE_CROSS_COMPANY_EVIDENCE_NETWORK_UNAVAILABLE",
            "SOURCE_HIERARCHY_UNRATIFIED",
            "IMPORTANCE_THRESHOLD_UNRATIFIED",
            "CROSS_COMPANY_COMPARABILITY_UNRATIFIED",
            "CANDIDATE_RANKING_UNRATIFIED",
            "LIVE_RADAR_POPULATION_NOT_IMPLEMENTED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
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


def run(input_path: Path, output_path: Path, contract_path: Path = CONTRACT_PATH) -> dict:
    packet = build_packet(_read_json(Path(input_path)), load_contract(Path(contract_path)))
    write_json_atomic(Path(output_path), packet)
    return packet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)
    try:
        packet = run(args.input, args.out, args.contract)
    except BusinessAccelerationError as exc:
        print(f"business acceleration radar failed: {exc}")
        return 1
    print(
        f"business acceleration radar: series={packet['series_count']} "
        f"cases={packet['case_count']} sha256={packet['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
