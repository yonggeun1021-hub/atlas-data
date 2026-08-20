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
OUTPUT_SCHEMA_VERSION = "supply_demand_radar_packet/1"
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
        "contract_version": "supply_demand_radar/1",
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
            "CRYPTO": "PARTIAL_EXISTING_NET_ISSUANCE_TRANSFORM",
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
        "series_results": results, "cases": cases,
        "source_coverage": copy.deepcopy(contract["source_coverage"]),
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "DEFAULT_CANDIDATE_POLICY_ABSENT", "IMPROVEMENT_DIRECTION_UNRATIFIED",
            "MINIMUM_CHANGE_UNRATIFIED", "CROSS_MARKET_COMPARABILITY_UNRATIFIED",
            "SOURCE_HIERARCHY_UNRATIFIED", "CANDIDATE_RANKING_UNRATIFIED",
            "KOREA_SOURCE_RELEASE_TIME_UNVERIFIED", "US_METRIC_SERIES_NOT_SELECTED",
            "LIVE_RADAR_POPULATION_NOT_IMPLEMENTED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


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
