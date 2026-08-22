#!/usr/bin/env python3
"""P2-03 policy-gated Korea Theme capital-rotation transform."""
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


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "korea_capital_rotation_contract.json"
INPUT_SCHEMA_VERSION = "korea_capital_rotation_input/1"
POLICY_SCHEMA_VERSION = "korea_capital_rotation_policy/1"
OUTPUT_SCHEMA_VERSION = "korea_capital_rotation_packet/3"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class KoreaCapitalRotationError(ValueError):
    """Fail-closed P2-03 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KoreaCapitalRotationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "korea_capital_rotation/3",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "upstream_contract_version": "korea_leadership_contract/v1",
        "upstream_transform_version": "korea_leadership/v1",
        "taxonomy_contract_version": "theme_taxonomy/1",
        "measurement": "korea_theme_relative_rotation_observation",
        "ranking_metric": "RELATIVE_STRENGTH_VS_OWN_BENCHMARK",
        "ranking_order": "DESCENDING_WITHIN_BENCHMARK_SCOPE",
        "tie_break": "SERIES_IDENTITY_ASC",
        "eligible_roles": ["SECTOR", "THEME"],
        "bucket_vocabulary": ["TOP", "MIDDLE", "BOTTOM"],
        "transition_semantics": "PRIOR_BUCKET_TO_CURRENT_BUCKET_WITHIN_BENCHMARK_SCOPE",
        "breadth_context_policy": "OBSERVATION_ONLY_NOT_RANKING_INPUT",
        "breadth_status_vocabulary": ["AVAILABLE", "BLOCKED", "STALE", "UNKNOWN"],
        "breadth_required_markets": ["KOSDAQ", "KOSPI"],
        "investor_flow_context_policy": "KRX_ONLY_UNVERIFIED_AVAILABLE_AT_NOT_RANKING_INPUT",
        "effective_interval": "[effective_from, effective_to)",
        "input_retention_policy": "UPSTREAM_DERIVED_PACKETS_ONLY_NO_SOURCE_CLOSE_ROWS",
        "output_retention_policy": "NON_RECONSTRUCTIVE_DERIVED_OBSERVATIONS_ONLY",
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
        "repository_default_policy": "ABSENT",
        "authority": {
            "external_ratified_rotation_policy_only": True,
            "cross_benchmark_ranking_authorized": False,
            "breadth_as_ranking_input_authorized": False,
            "investor_flow_as_ranking_input_authorized": False,
            "default_theme_selection_authorized": False,
            "p2_state_vocabulary_authorized": False,
            "state_ledger_authorized": False,
            "regime_input_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise KoreaCapitalRotationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise KoreaCapitalRotationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str):
        raise KoreaCapitalRotationError(code)
    try:
        result = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KoreaCapitalRotationError(code) from exc
    if result.isoformat() != value:
        raise KoreaCapitalRotationError(code)
    return result


def _timestamp(value, code: str, *, require_kst: bool = False) -> dt.datetime:
    if not isinstance(value, str):
        raise KoreaCapitalRotationError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KoreaCapitalRotationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KoreaCapitalRotationError(code)
    if require_kst and parsed.utcoffset() != dt.timedelta(hours=9):
        raise KoreaCapitalRotationError(code)
    return parsed.astimezone(dt.timezone.utc)


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise KoreaCapitalRotationError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise KoreaCapitalRotationError(code)
    return value


def _identity(value, code: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > 200 or any(ord(character) < 32 for character in value)
    ):
        raise KoreaCapitalRotationError(code)
    return value


def _positive_int(value, code: str) -> int:
    if type(value) is not int or value < 1:
        raise KoreaCapitalRotationError(code)
    return value


def _decimal(value, code: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise KoreaCapitalRotationError(code)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise KoreaCapitalRotationError(code) from exc
    if not result.is_finite() or (positive and result <= 0):
        raise KoreaCapitalRotationError(code)
    return result


def _render(value: Decimal, places: int) -> str:
    try:
        with localcontext() as context:
            context.prec = 50
            result = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise KoreaCapitalRotationError("OUTPUT_NUMBER_INVALID") from exc
    if result == 0:
        result = Decimal(0)
    text = format(result, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _validate_binding(value: dict, contract: dict) -> dict:
    fields = {
        "taxonomy_contract_version", "taxonomy_id", "taxonomy_decision_id",
        "taxonomy_decision_sha256", "taxonomy_packet_sha256",
        "upstream_leadership_policy_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise KoreaCapitalRotationError("TAXONOMY_BINDING_FIELDS_MISMATCH")
    if value.get("taxonomy_contract_version") != contract["taxonomy_contract_version"]:
        raise KoreaCapitalRotationError("TAXONOMY_CONTRACT_VERSION_MISMATCH")
    return {
        "taxonomy_contract_version": value["taxonomy_contract_version"],
        "taxonomy_id": _token(value.get("taxonomy_id"), "TAXONOMY_ID_INVALID"),
        "taxonomy_decision_id": _token(
            value.get("taxonomy_decision_id"), "TAXONOMY_DECISION_ID_INVALID"
        ),
        "taxonomy_decision_sha256": _sha(
            value.get("taxonomy_decision_sha256"), "TAXONOMY_DECISION_SHA_INVALID"
        ),
        "taxonomy_packet_sha256": _sha(
            value.get("taxonomy_packet_sha256"), "TAXONOMY_PACKET_SHA_INVALID"
        ),
        "upstream_leadership_policy_sha256": _sha(
            value.get("upstream_leadership_policy_sha256"),
            "UPSTREAM_LEADERSHIP_POLICY_SHA_INVALID",
        ),
    }


_BREADTH_STATUS_SEVERITY = {"UNKNOWN": 0, "BLOCKED": 1, "STALE": 2, "AVAILABLE": 3}


def _validate_breadth_market(value: dict, market: str) -> dict:
    """Parse one market's minimum-sufficient breadth lineage facts.

    All three fields null together means no observation was supplied for
    this market at all (-> UNKNOWN downstream). Any other combination
    requires at least lineage_sha256 and as_of_date -- a market cannot
    have a partial identity. available_at alone may still be null: that
    is exactly what every P1-KR-05 Breadth observation packet emits today
    (decision_eligible=false at the source), and must map to BLOCKED, not
    be treated as a missing observation.
    """
    if not isinstance(value, dict) or set(value) != {
        "lineage_sha256", "as_of_date", "available_at",
    }:
        raise KoreaCapitalRotationError(f"BREADTH_MARKET_FIELDS_MISMATCH:{market}")
    lineage_sha256 = value.get("lineage_sha256")
    as_of_date = value.get("as_of_date")
    available_at = value.get("available_at")
    if lineage_sha256 is None and as_of_date is None and available_at is None:
        return {"lineage_sha256": None, "as_of_date": None, "available_at": None}
    if lineage_sha256 is None or as_of_date is None:
        raise KoreaCapitalRotationError(f"BREADTH_MARKET_PARTIAL_IDENTITY:{market}")
    parsed = {
        "lineage_sha256": _sha(lineage_sha256, f"BREADTH_MARKET_SHA_INVALID:{market}"),
        "as_of_date": _date(as_of_date, f"BREADTH_MARKET_DATE_INVALID:{market}"),
        "available_at": (
            None if available_at is None
            else _timestamp(available_at, f"BREADTH_MARKET_AVAILABLE_AT_INVALID:{market}")
        ),
    }
    return parsed


def _derive_breadth_market_status(
    parsed: dict, as_of_date: dt.date, freshness_limit_days: int, market: str
) -> str:
    if parsed["lineage_sha256"] is None:
        return "UNKNOWN"
    if parsed["available_at"] is None:
        return "BLOCKED"
    age_days = (as_of_date - parsed["available_at"].date()).days
    if age_days < 0:
        raise KoreaCapitalRotationError(f"BREADTH_MARKET_AVAILABLE_AT_AFTER_AS_OF:{market}")
    if age_days > freshness_limit_days:
        return "STALE"
    return "AVAILABLE"


def _validate_context(value: dict, as_of_date: dt.date) -> dict:
    fields = {"breadth", "investor_flow"}
    if not isinstance(value, dict) or set(value) != fields:
        raise KoreaCapitalRotationError("COVERAGE_CONTEXT_FIELDS_MISMATCH")
    breadth = value.get("breadth")
    if not isinstance(breadth, dict) or set(breadth) != {
        "status", "markets", "freshness_limit_days",
        "ranking_input_authorized", "decision_eligible",
    }:
        raise KoreaCapitalRotationError("BREADTH_CONTEXT_FIELDS_MISMATCH")
    freshness_limit_days = _positive_int(
        breadth.get("freshness_limit_days"), "BREADTH_FRESHNESS_LIMIT_INVALID"
    )
    markets = breadth.get("markets")
    if not isinstance(markets, dict) or set(markets) != {"KOSDAQ", "KOSPI"}:
        raise KoreaCapitalRotationError("BREADTH_MARKETS_FIELDS_MISMATCH")
    parsed_markets = {
        market: _validate_breadth_market(markets[market], market)
        for market in ("KOSDAQ", "KOSPI")
    }
    # Independently re-derived from the raw per-market facts, not trusted
    # from the caller's own declared status -- exactly like every other
    # derived field in this contract (rotation_policy_effective, bucket
    # transitions, ...). The worst per-market status wins: one market
    # blocked or unknown makes the whole Breadth context that severity,
    # never averaged or masked by the other market being fresher.
    per_market_status = {
        market: _derive_breadth_market_status(
            parsed, as_of_date, freshness_limit_days, market
        )
        for market, parsed in parsed_markets.items()
    }
    derived_status = min(
        per_market_status.values(), key=lambda status: _BREADTH_STATUS_SEVERITY[status]
    )
    derived_decision_eligible = derived_status == "AVAILABLE"
    if (
        breadth.get("status") != derived_status
        or breadth.get("decision_eligible") is not derived_decision_eligible
        or breadth.get("ranking_input_authorized") is not False
    ):
        raise KoreaCapitalRotationError("BREADTH_CONTEXT_AUTHORITY_INVALID")
    flow = value.get("investor_flow")
    if not isinstance(flow, dict) or set(flow) != {
        "status", "market_venue_scope", "nxt_included",
        "whole_korea_market_claim_authorized", "source_release_time_status",
        "available_at", "decision_eligible", "ranking_input_authorized",
    }:
        raise KoreaCapitalRotationError("INVESTOR_FLOW_CONTEXT_FIELDS_MISMATCH")
    if (
        flow.get("status") != "KRX_ONLY_PARTIAL_MARKET_COVERAGE"
        or flow.get("market_venue_scope") != "KRX_ONLY"
        or flow.get("nxt_included") is not False
        or flow.get("whole_korea_market_claim_authorized") is not False
        or flow.get("source_release_time_status") != "unverified"
        or flow.get("available_at") is not None
        or flow.get("decision_eligible") is not False
        or flow.get("ranking_input_authorized") is not False
    ):
        raise KoreaCapitalRotationError("INVESTOR_FLOW_CONTEXT_AUTHORITY_INVALID")
    return copy.deepcopy(value)


AUTHORITY_FIELDS = {
    "leader_classification_authorized", "ranking_authorized",
    "trend_direction_authorized", "breadth_direction_authorized",
    "regime_score_authorized", "production_wiring_authorized",
    "trading_action_authorized",
}


def _validate_upstream(value: dict, label: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "transform_version", "measurement",
        "market", "observation_date", "available_at", "status", "window",
        "temporal_eligibility", "relative_strength_observations", "retention",
        "policy", "lineage", "payload_sha256",
    } | AUTHORITY_FIELDS
    if not isinstance(value, dict) or set(value) != fields:
        raise KoreaCapitalRotationError(f"UPSTREAM_FIELDS_MISMATCH:{label}")
    digest = _sha(value.get("payload_sha256"), f"UPSTREAM_PAYLOAD_SHA_INVALID:{label}")
    digest_payload = copy.deepcopy(value)
    digest_payload.pop("payload_sha256")
    if payload_sha256(digest_payload) != digest:
        raise KoreaCapitalRotationError(f"UPSTREAM_PAYLOAD_SHA_MISMATCH:{label}")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != contract["upstream_contract_version"]
        or value.get("transform_version") != contract["upstream_transform_version"]
        or value.get("measurement") != "korea_index_relative_leadership_observation"
        or value.get("market") != "KOREA"
        or value.get("status") != "OBSERVED_UNCLASSIFIED"
    ):
        raise KoreaCapitalRotationError(f"UPSTREAM_IDENTITY_INVALID:{label}")
    if any(value[field] is not False for field in AUTHORITY_FIELDS):
        raise KoreaCapitalRotationError(f"UPSTREAM_AUTHORITY_EXPANDED:{label}")
    observation_date = _date(value.get("observation_date"), f"UPSTREAM_DATE_INVALID:{label}")
    available_at = _timestamp(
        value.get("available_at"), f"UPSTREAM_AVAILABLE_AT_INVALID:{label}", require_kst=True
    )
    if available_at.date() < observation_date:
        raise KoreaCapitalRotationError(f"UPSTREAM_AVAILABLE_BEFORE_OBSERVATION:{label}")
    window = value.get("window")
    if not isinstance(window, dict) or set(window) != {
        "first_input_session", "first_return_session", "last_return_session",
        "lookback_sessions", "exact_expected_sessions",
    }:
        raise KoreaCapitalRotationError(f"UPSTREAM_WINDOW_INVALID:{label}")
    first_input = _date(window.get("first_input_session"), f"UPSTREAM_WINDOW_INVALID:{label}")
    first_return = _date(window.get("first_return_session"), f"UPSTREAM_WINDOW_INVALID:{label}")
    last_return = _date(window.get("last_return_session"), f"UPSTREAM_WINDOW_INVALID:{label}")
    lookback = _positive_int(window.get("lookback_sessions"), f"UPSTREAM_WINDOW_INVALID:{label}")
    if not (first_input < first_return <= last_return == observation_date) or window.get("exact_expected_sessions") is not True:
        raise KoreaCapitalRotationError(f"UPSTREAM_WINDOW_INVALID:{label}")
    temporal = value.get("temporal_eligibility")
    if (
        not isinstance(temporal, dict)
        or set(temporal) != {"eligibility", "publication_timing_source", "authoritative_historical_pit"}
        or temporal.get("eligibility") != "FORWARD_PIT_QUALIFIED"
        or not isinstance(temporal.get("publication_timing_source"), str)
        or not temporal["publication_timing_source"]
        or temporal.get("authoritative_historical_pit") is not False
    ):
        raise KoreaCapitalRotationError(f"UPSTREAM_NOT_FORWARD_PIT:{label}")
    retention = value.get("retention")
    if (
        not isinstance(retention, dict)
        or set(retention) != {
            "input_policy", "output_policy", "source_rows_emitted",
            "source_closes_emitted", "reconstructive_series_emitted",
        }
        or retention.get("input_policy") != "transient_memory_or_stdin_only"
        or retention.get("output_policy") != "non_reconstructive_derived_observations_only"
        or any(retention.get(field) is not False for field in (
            "source_rows_emitted", "source_closes_emitted", "reconstructive_series_emitted"
        ))
    ):
        raise KoreaCapitalRotationError(f"UPSTREAM_RETENTION_INVALID:{label}")
    upstream_policy = value.get("policy")
    if (
        not isinstance(upstream_policy, dict)
        or set(upstream_policy) != {
            "policy_version", "policy_sha256", "approval_status", "source_name",
            "session_calendar_source", "publication_timing_source",
            "effective_dated_taxonomy",
        }
        or upstream_policy.get("approval_status") != "RATIFIED"
        or upstream_policy.get("effective_dated_taxonomy") is not True
        or not isinstance(upstream_policy.get("policy_version"), str)
        or not upstream_policy["policy_version"].strip()
    ):
        raise KoreaCapitalRotationError(f"UPSTREAM_POLICY_INVALID:{label}")
    policy_sha = _sha(
        upstream_policy.get("policy_sha256"), f"UPSTREAM_POLICY_SHA_INVALID:{label}"
    )
    lineage = value.get("lineage")
    if (
        not isinstance(lineage, dict)
        or set(lineage) != {
            "input_sha256", "session_count", "return_session_count",
            "session_coverage_complete", "current_membership_backfill_authorized",
        }
        or lineage.get("session_count") != lookback + 1
        or lineage.get("return_session_count") != lookback
        or lineage.get("session_coverage_complete") is not True
        or lineage.get("current_membership_backfill_authorized") is not False
    ):
        raise KoreaCapitalRotationError(f"UPSTREAM_LINEAGE_INVALID:{label}")
    _sha(lineage.get("input_sha256"), f"UPSTREAM_INPUT_SHA_INVALID:{label}")
    rows = value.get("relative_strength_observations")
    if not isinstance(rows, list) or len(rows) < 4:
        raise KoreaCapitalRotationError(f"UPSTREAM_OBSERVATIONS_INSUFFICIENT:{label}")
    normalized = {}
    order = []
    row_fields = {
        "series_identity", "role", "benchmark_identity",
        "cumulative_gross_return", "relative_strength_vs_benchmark",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise KoreaCapitalRotationError(f"UPSTREAM_OBSERVATION_FIELDS_MISMATCH:{label}")
        identity = _identity(row.get("series_identity"), f"UPSTREAM_SERIES_ID_INVALID:{label}")
        benchmark = _identity(row.get("benchmark_identity"), f"UPSTREAM_BENCHMARK_ID_INVALID:{label}")
        role = row.get("role")
        if role not in {"KOSPI_BENCHMARK", "KOSDAQ_BENCHMARK", "SECTOR", "THEME"}:
            raise KoreaCapitalRotationError(f"UPSTREAM_ROLE_INVALID:{label}:{identity}")
        if identity in normalized:
            raise KoreaCapitalRotationError(f"UPSTREAM_SERIES_DUPLICATE:{label}:{identity}")
        order.append(identity)
        gross = _decimal(
            row.get("cumulative_gross_return"), f"UPSTREAM_RETURN_INVALID:{label}", positive=True
        )
        relative = _decimal(
            row.get("relative_strength_vs_benchmark"), f"UPSTREAM_RS_INVALID:{label}"
        )
        if relative <= Decimal(-1):
            raise KoreaCapitalRotationError(f"UPSTREAM_RS_INVALID:{label}:{identity}")
        if role.endswith("BENCHMARK") and (identity != benchmark or relative != 0):
            raise KoreaCapitalRotationError(f"UPSTREAM_BENCHMARK_SEMANTICS_INVALID:{label}:{identity}")
        normalized[identity] = {
            "series_identity": identity,
            "role": role,
            "benchmark_identity": benchmark,
            "cumulative_gross_return": gross,
            "relative_strength_vs_benchmark": relative,
        }
    if order != sorted(order):
        raise KoreaCapitalRotationError(f"UPSTREAM_OBSERVATION_ORDER_INVALID:{label}")
    for row in normalized.values():
        benchmark = normalized.get(row["benchmark_identity"])
        if benchmark is None or not benchmark["role"].endswith("BENCHMARK"):
            raise KoreaCapitalRotationError(f"UPSTREAM_BENCHMARK_MISSING:{label}:{row['series_identity']}")
    return {
        "observation_date": observation_date,
        "available_at": available_at,
        "lookback_sessions": lookback,
        "policy_sha256": policy_sha,
        "rows": normalized,
        "packet_sha256": digest,
    }


def _validate_policy(
    value: dict,
    binding: dict,
    eligible: dict[str, dict],
    prior_date: dt.date,
    current_date: dt.date,
    prior_available_at: dt.datetime,
) -> tuple[dict, bool, dict[str, dict]]:
    fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to",
        "taxonomy_decision_sha256", "taxonomy_packet_sha256",
        "upstream_leadership_policy_sha256", "ranking_metric", "ranking_order",
        "tie_break", "maximum_calendar_gap_days", "benchmark_scopes",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise KoreaCapitalRotationError("POLICY_FIELDS_MISMATCH")
    if value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise KoreaCapitalRotationError("POLICY_SCHEMA_MISMATCH")
    _token(value.get("policy_id"), "POLICY_ID_INVALID")
    status = value.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise KoreaCapitalRotationError("POLICY_APPROVAL_STATUS_INVALID")
    effective_from = _date(value.get("effective_from"), "POLICY_EFFECTIVE_FROM_INVALID")
    effective_to = None
    if value.get("effective_to") is not None:
        effective_to = _date(value["effective_to"], "POLICY_EFFECTIVE_TO_INVALID")
        if effective_to <= effective_from:
            raise KoreaCapitalRotationError("POLICY_EFFECTIVE_TO_INVALID")
    if value.get("taxonomy_decision_sha256") != binding["taxonomy_decision_sha256"]:
        raise KoreaCapitalRotationError("POLICY_TAXONOMY_DECISION_MISMATCH")
    if value.get("taxonomy_packet_sha256") != binding["taxonomy_packet_sha256"]:
        raise KoreaCapitalRotationError("POLICY_TAXONOMY_PACKET_MISMATCH")
    if value.get("upstream_leadership_policy_sha256") != binding["upstream_leadership_policy_sha256"]:
        raise KoreaCapitalRotationError("POLICY_UPSTREAM_POLICY_MISMATCH")
    if value.get("ranking_metric") != "RELATIVE_STRENGTH_VS_OWN_BENCHMARK":
        raise KoreaCapitalRotationError("POLICY_RANKING_METRIC_INVALID")
    if (
        value.get("ranking_order") != "DESCENDING_WITHIN_BENCHMARK_SCOPE"
        or value.get("tie_break") != "SERIES_IDENTITY_ASC"
    ):
        raise KoreaCapitalRotationError("POLICY_RANKING_ORDER_INVALID")
    maximum_gap = _positive_int(
        value.get("maximum_calendar_gap_days"), "POLICY_MAXIMUM_GAP_INVALID"
    )
    raw_scopes = value.get("benchmark_scopes")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise KoreaCapitalRotationError("POLICY_SCOPES_EMPTY")
    scopes = {}
    order = []
    all_themes = set()
    for raw in raw_scopes:
        if not isinstance(raw, dict) or set(raw) != {
            "benchmark_identity", "members", "top_count", "bottom_count"
        }:
            raise KoreaCapitalRotationError("POLICY_SCOPE_FIELDS_MISMATCH")
        benchmark = _identity(raw.get("benchmark_identity"), "POLICY_SCOPE_BENCHMARK_INVALID")
        order.append(benchmark)
        members = raw.get("members")
        if not isinstance(members, list) or len(members) < 3:
            raise KoreaCapitalRotationError(f"POLICY_SCOPE_THEME_SET_INVALID:{benchmark}")
        mapping = {}
        member_order = []
        for member in members:
            if not isinstance(member, dict) or set(member) != {"series_identity", "theme_id"}:
                raise KoreaCapitalRotationError(f"POLICY_SCOPE_MEMBER_FIELDS_INVALID:{benchmark}")
            series_identity = _identity(
                member.get("series_identity"), "POLICY_SCOPE_SERIES_ID_INVALID"
            )
            theme_id = _token(member.get("theme_id"), "POLICY_SCOPE_THEME_ID_INVALID")
            if series_identity in mapping:
                raise KoreaCapitalRotationError(f"POLICY_SCOPE_SERIES_DUPLICATE:{benchmark}")
            mapping[series_identity] = theme_id
            member_order.append(series_identity)
        if member_order != sorted(member_order):
            raise KoreaCapitalRotationError(f"POLICY_SCOPE_MEMBER_ORDER_INVALID:{benchmark}")
        top_count = _positive_int(raw.get("top_count"), "POLICY_SCOPE_TOP_COUNT_INVALID")
        bottom_count = _positive_int(raw.get("bottom_count"), "POLICY_SCOPE_BOTTOM_COUNT_INVALID")
        if top_count + bottom_count > len(mapping):
            raise KoreaCapitalRotationError(f"POLICY_SCOPE_BUCKETS_OVERLAP:{benchmark}")
        if set(mapping) & all_themes:
            raise KoreaCapitalRotationError("POLICY_THEME_IN_MULTIPLE_SCOPES")
        all_themes.update(mapping)
        scopes[benchmark] = {
            "benchmark_identity": benchmark,
            "series_to_theme": mapping,
            "top_count": top_count,
            "bottom_count": bottom_count,
        }
    if order != sorted(set(order)):
        raise KoreaCapitalRotationError("POLICY_SCOPE_ORDER_INVALID")
    if all_themes != set(eligible):
        raise KoreaCapitalRotationError("POLICY_THEME_COVERAGE_MISMATCH")
    for theme_id, row in eligible.items():
        scope = scopes.get(row["benchmark_identity"])
        if scope is None or theme_id not in scope["series_to_theme"]:
            raise KoreaCapitalRotationError(f"POLICY_BENCHMARK_SCOPE_MISMATCH:{theme_id}")
    mapped_theme_ids = [
        theme_id
        for scope in scopes.values()
        for theme_id in scope["series_to_theme"].values()
    ]
    if len(mapped_theme_ids) != len(set(mapped_theme_ids)):
        raise KoreaCapitalRotationError("POLICY_THEME_ID_MULTIPLE_PROXY_UNDEFINED")
    covers_both = effective_from <= prior_date and (
        effective_to is None or current_date < effective_to
    )
    if status == "UNRATIFIED":
        if value.get("ratified_by") is not None or value.get("ratified_at_utc") is not None:
            raise KoreaCapitalRotationError("UNRATIFIED_POLICY_PROOF_FORBIDDEN")
    else:
        if (
            not isinstance(value.get("ratified_by"), str) or not value["ratified_by"].strip()
            or not isinstance(value.get("ratified_at_utc"), str)
            or not value["ratified_at_utc"].endswith("Z")
        ):
            raise KoreaCapitalRotationError("POLICY_RATIFICATION_PROOF_INVALID")
        ratified_at = _timestamp(value["ratified_at_utc"], "POLICY_RATIFICATION_PROOF_INVALID")
        if covers_both and ratified_at > prior_available_at:
            raise KoreaCapitalRotationError("POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION")
    effective = status == "RATIFIED" and covers_both
    if effective and (current_date - prior_date).days > maximum_gap:
        raise KoreaCapitalRotationError("OBSERVATION_GAP_EXCEEDS_POLICY")
    return copy.deepcopy(value), effective, scopes


def _rank(rows: dict[str, dict], theme_ids: list[str]) -> list[str]:
    return sorted(
        theme_ids,
        key=lambda identity: (-rows[identity]["relative_strength_vs_benchmark"], identity),
    )


def _buckets(ranked: list[str], top_count: int, bottom_count: int) -> dict[str, str]:
    top = set(ranked[:top_count])
    bottom = set(ranked[-bottom_count:])
    return {
        identity: "TOP" if identity in top else "BOTTOM" if identity in bottom else "MIDDLE"
        for identity in ranked
    }


def build_packet(value: dict, policy: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "as_of_date", "taxonomy_binding", "coverage_context",
        "prior_observation", "current_observation",
    }
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise KoreaCapitalRotationError("INPUT_SCHEMA_MISMATCH")
    if set(value) != fields:
        raise KoreaCapitalRotationError("INPUT_FIELDS_MISMATCH")
    as_of_date = _date(value.get("as_of_date"), "AS_OF_DATE_INVALID")
    binding = _validate_binding(value.get("taxonomy_binding"), contract)
    context = _validate_context(value.get("coverage_context"), as_of_date)
    prior = _validate_upstream(value.get("prior_observation"), "prior", contract)
    current = _validate_upstream(value.get("current_observation"), "current", contract)
    if not prior["observation_date"] < current["observation_date"] == as_of_date:
        raise KoreaCapitalRotationError("OBSERVATION_DATE_ORDER_INVALID")
    if prior["available_at"] >= current["available_at"]:
        raise KoreaCapitalRotationError("OBSERVATION_AVAILABLE_AT_ORDER_INVALID")
    if prior["lookback_sessions"] != current["lookback_sessions"]:
        raise KoreaCapitalRotationError("UPSTREAM_LOOKBACK_DRIFT")
    if (
        prior["policy_sha256"] != binding["upstream_leadership_policy_sha256"]
        or current["policy_sha256"] != binding["upstream_leadership_policy_sha256"]
    ):
        raise KoreaCapitalRotationError("UPSTREAM_POLICY_BINDING_MISMATCH")
    prior_keys = sorted(prior["rows"])
    if prior_keys != sorted(current["rows"]):
        raise KoreaCapitalRotationError("UPSTREAM_TAXONOMY_DRIFT")
    for identity in prior_keys:
        before = prior["rows"][identity]
        after = current["rows"][identity]
        if before["role"] != after["role"] or before["benchmark_identity"] != after["benchmark_identity"]:
            raise KoreaCapitalRotationError(f"UPSTREAM_ROLE_OR_BENCHMARK_DRIFT:{identity}")
    eligible = {
        identity: row for identity, row in current["rows"].items()
        if row["role"] in contract["eligible_roles"]
    }
    if len(eligible) < 3:
        raise KoreaCapitalRotationError("ELIGIBLE_THEME_COVERAGE_INSUFFICIENT")
    checked_policy, effective, scopes = _validate_policy(
        policy, binding, eligible, prior["observation_date"],
        current["observation_date"], prior["available_at"],
    )
    scope_outputs = []
    places = contract["output_decimal_places"]
    for benchmark in sorted(scopes):
        scope = scopes[benchmark]
        observations = []
        if effective:
            series_ids = list(scope["series_to_theme"])
            prior_ranked = _rank(prior["rows"], series_ids)
            current_ranked = _rank(current["rows"], series_ids)
            prior_ranks = {identity: index + 1 for index, identity in enumerate(prior_ranked)}
            current_ranks = {identity: index + 1 for index, identity in enumerate(current_ranked)}
            prior_buckets = _buckets(prior_ranked, scope["top_count"], scope["bottom_count"])
            current_buckets = _buckets(current_ranked, scope["top_count"], scope["bottom_count"])
            top_themes = [
                scope["series_to_theme"][identity]
                for identity in current_ranked[: scope["top_count"]]
            ]
            bottom_themes = [
                scope["series_to_theme"][identity]
                for identity in reversed(current_ranked[-scope["bottom_count"] :])
            ]
        else:
            prior_ranks = current_ranks = prior_buckets = current_buckets = {}
            top_themes = bottom_themes = []
        for identity in scope["series_to_theme"]:
            before = prior["rows"][identity]["relative_strength_vs_benchmark"]
            after = current["rows"][identity]["relative_strength_vs_benchmark"]
            prior_bucket = prior_buckets.get(identity)
            current_bucket = current_buckets.get(identity)
            observations.append({
                "series_identity": identity,
                "theme_id": scope["series_to_theme"][identity],
                "role": current["rows"][identity]["role"],
                "prior_relative_strength_vs_benchmark": _render(before, places),
                "current_relative_strength_vs_benchmark": _render(after, places),
                "relative_strength_change": _render(after - before, places),
                "prior_rank_within_benchmark": prior_ranks.get(identity),
                "current_rank_within_benchmark": current_ranks.get(identity),
                "rank_change_within_benchmark": (
                    prior_ranks[identity] - current_ranks[identity] if effective else None
                ),
                "prior_bucket": prior_bucket,
                "current_bucket": current_bucket,
                "bucket_transition": (
                    f"{prior_bucket}_TO_{current_bucket}" if effective else None
                ),
                "p2_state": "UNDEFINED_PENDING_P2_05",
            })
        scope_outputs.append({
            "benchmark_identity": benchmark,
            "top_themes": top_themes,
            "bottom_themes": bottom_themes,
            "theme_observations": observations,
        })
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "measurement": contract["measurement"],
        "market": "KOREA",
        "as_of_date": as_of_date.isoformat(),
        "status": "ROTATION_BUCKETS_OBSERVED" if effective else "POLICY_NOT_EFFECTIVE",
        "observation_pair": {
            "prior_date": prior["observation_date"].isoformat(),
            "current_date": current["observation_date"].isoformat(),
            "calendar_gap_days": (current["observation_date"] - prior["observation_date"]).days,
            "lookback_sessions": current["lookback_sessions"],
            # Both upstream observations' own available_at, persisted so a
            # standalone validate_packet() call can independently re-prove
            # temporal order and ratified-before-prior without either
            # re-reading the two full upstream Leadership packets (which
            # output_retention_policy forbids retaining) or trusting the
            # persisted rotation_policy_effective flag blindly.
            "prior_available_at": prior["available_at"].isoformat(),
            "current_available_at": current["available_at"].isoformat(),
        },
        "taxonomy_binding": binding,
        "coverage_context": context,
        "rotation_policy": checked_policy,
        "rotation_policy_effective": effective,
        "ranking_method": {
            "metric": contract["ranking_metric"],
            "order": contract["ranking_order"],
            "tie_break": contract["tie_break"],
            "cross_benchmark_ranking": False,
        } if effective else None,
        "benchmark_scopes": scope_outputs,
        "retention": {
            "input_policy": contract["input_retention_policy"],
            "output_policy": contract["output_retention_policy"],
            "source_close_rows_received": False,
            "source_closes_emitted": False,
            "reconstructive_series_emitted": False,
        },
        "lineage": {
            "prior_upstream_packet_sha256": prior["packet_sha256"],
            "current_upstream_packet_sha256": current["packet_sha256"],
            "rotation_policy_sha256": payload_sha256(checked_policy),
            "taxonomy_packet_sha256": binding["taxonomy_packet_sha256"],
            "upstream_leadership_policy_sha256": binding["upstream_leadership_policy_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]) | {
            "theme_ranking_within_benchmark_authorized": effective,
            "top_bottom_bucket_authorized": effective,
            "bucket_transition_authorized": effective,
        },
        "unresolved_boundaries": [
            "KOREA_BREADTH_DURABLE_AVAILABLE_AT_LINEAGE_NOT_IMPLEMENTED",
            "INVESTOR_FLOW_SOURCE_RELEASE_TIME_UNVERIFIED",
            "INVESTOR_FLOW_NXT_NOT_INCLUDED",
            "THEME_TAXONOMY_OPERATIONAL_POPULATION_NOT_IMPLEMENTED",
            "P2_STATE_VOCABULARY_PENDING_P2_05",
            "ROTATION_LEDGER_LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
            "BRIEFING_INTEGRATION_LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate the complete v1 output without inventing omitted source rows."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "measurement", "market",
        "as_of_date", "status", "observation_pair", "taxonomy_binding",
        "coverage_context", "rotation_policy", "rotation_policy_effective",
        "ranking_method", "benchmark_scopes", "retention", "lineage",
        "authority", "unresolved_boundaries", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise KoreaCapitalRotationError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("measurement") != contract["measurement"]
        or packet.get("market") != "KOREA"
    ):
        raise KoreaCapitalRotationError("OUTPUT_IDENTITY_INVALID")
    as_of = _date(packet.get("as_of_date"), "OUTPUT_AS_OF_DATE_INVALID")
    pair = packet.get("observation_pair")
    if not isinstance(pair, dict) or set(pair) != {
        "prior_date", "current_date", "calendar_gap_days", "lookback_sessions",
        "prior_available_at", "current_available_at",
    }:
        raise KoreaCapitalRotationError("OUTPUT_OBSERVATION_PAIR_INVALID")
    prior_date = _date(pair.get("prior_date"), "OUTPUT_PRIOR_DATE_INVALID")
    current_date = _date(pair.get("current_date"), "OUTPUT_CURRENT_DATE_INVALID")
    _positive_int(pair.get("lookback_sessions"), "OUTPUT_LOOKBACK_INVALID")
    if (
        not prior_date < current_date == as_of
        or pair.get("calendar_gap_days") != (current_date - prior_date).days
    ):
        raise KoreaCapitalRotationError("OUTPUT_OBSERVATION_PAIR_MISMATCH")
    # Both upstream available_at timestamps, independently re-parsed and
    # re-ordered here -- not trusted from the persisted rotation_policy_
    # effective flag. This is what makes prior-vs-current temporal order
    # standalone-provable without either upstream Leadership packet.
    prior_available_at = _timestamp(
        pair.get("prior_available_at"), "OUTPUT_PRIOR_AVAILABLE_AT_INVALID"
    )
    current_available_at = _timestamp(
        pair.get("current_available_at"), "OUTPUT_CURRENT_AVAILABLE_AT_INVALID"
    )
    if prior_available_at >= current_available_at:
        raise KoreaCapitalRotationError("OUTPUT_AVAILABLE_AT_ORDER_INVALID")
    binding = _validate_binding(packet.get("taxonomy_binding"), contract)
    _validate_context(packet.get("coverage_context"), as_of)

    policy = packet.get("rotation_policy")
    policy_fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to",
        "taxonomy_decision_sha256", "taxonomy_packet_sha256",
        "upstream_leadership_policy_sha256", "ranking_metric", "ranking_order",
        "tie_break", "maximum_calendar_gap_days", "benchmark_scopes",
    }
    if not isinstance(policy, dict) or set(policy) != policy_fields:
        raise KoreaCapitalRotationError("OUTPUT_POLICY_FIELDS_MISMATCH")
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise KoreaCapitalRotationError("OUTPUT_POLICY_SCHEMA_MISMATCH")
    _token(policy.get("policy_id"), "OUTPUT_POLICY_ID_INVALID")
    approval = policy.get("approval_status")
    if approval not in {"RATIFIED", "UNRATIFIED"}:
        raise KoreaCapitalRotationError("OUTPUT_POLICY_STATUS_INVALID")
    effective_from = _date(
        policy.get("effective_from"), "OUTPUT_POLICY_EFFECTIVE_FROM_INVALID"
    )
    effective_to = (
        None
        if policy.get("effective_to") is None
        else _date(policy["effective_to"], "OUTPUT_POLICY_EFFECTIVE_TO_INVALID")
    )
    if effective_to is not None and effective_to <= effective_from:
        raise KoreaCapitalRotationError("OUTPUT_POLICY_EFFECTIVE_TO_INVALID")
    if (
        policy.get("taxonomy_decision_sha256") != binding["taxonomy_decision_sha256"]
        or policy.get("taxonomy_packet_sha256") != binding["taxonomy_packet_sha256"]
        or policy.get("upstream_leadership_policy_sha256")
        != binding["upstream_leadership_policy_sha256"]
        or policy.get("ranking_metric") != contract["ranking_metric"]
        or policy.get("ranking_order") != contract["ranking_order"]
        or policy.get("tie_break") != contract["tie_break"]
    ):
        raise KoreaCapitalRotationError("OUTPUT_POLICY_BINDING_INVALID")
    maximum_gap = _positive_int(
        policy.get("maximum_calendar_gap_days"), "OUTPUT_POLICY_MAXIMUM_GAP_INVALID"
    )
    ratified_at = None
    if approval == "UNRATIFIED":
        if policy.get("ratified_by") is not None or policy.get("ratified_at_utc") is not None:
            raise KoreaCapitalRotationError("OUTPUT_UNRATIFIED_PROOF_FORBIDDEN")
    else:
        if (
            not isinstance(policy.get("ratified_by"), str)
            or not policy["ratified_by"].strip()
            or not isinstance(policy.get("ratified_at_utc"), str)
            or not policy["ratified_at_utc"].endswith("Z")
        ):
            raise KoreaCapitalRotationError("OUTPUT_RATIFICATION_PROOF_INVALID")
        ratified_at = _timestamp(
            policy["ratified_at_utc"], "OUTPUT_RATIFICATION_PROOF_INVALID"
        )
    covers_both = effective_from <= prior_date and (
        effective_to is None or current_date < effective_to
    )
    # Standalone re-proof of ratified-before-prior, mirroring build-time
    # _validate_policy() -- a tampered packet claiming covers_both without
    # this having actually held must still fail here.
    if approval == "RATIFIED" and covers_both and ratified_at > prior_available_at:
        raise KoreaCapitalRotationError("OUTPUT_POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION")
    effective = approval == "RATIFIED" and covers_both
    if effective and (current_date - prior_date).days > maximum_gap:
        raise KoreaCapitalRotationError("OUTPUT_OBSERVATION_GAP_EXCEEDS_POLICY")
    if packet.get("rotation_policy_effective") is not effective:
        raise KoreaCapitalRotationError("OUTPUT_POLICY_EFFECTIVE_MISMATCH")

    raw_policy_scopes = policy.get("benchmark_scopes")
    if not isinstance(raw_policy_scopes, list) or not raw_policy_scopes:
        raise KoreaCapitalRotationError("OUTPUT_POLICY_SCOPES_INVALID")
    policy_scopes = {}
    policy_scope_order = []
    all_series = set()
    all_theme_ids = set()
    for scope in raw_policy_scopes:
        if not isinstance(scope, dict) or set(scope) != {
            "benchmark_identity", "members", "top_count", "bottom_count",
        }:
            raise KoreaCapitalRotationError("OUTPUT_POLICY_SCOPE_FIELDS_MISMATCH")
        benchmark = _identity(
            scope.get("benchmark_identity"), "OUTPUT_POLICY_BENCHMARK_INVALID"
        )
        members = scope.get("members")
        if not isinstance(members, list) or len(members) < 3:
            raise KoreaCapitalRotationError(
                f"OUTPUT_POLICY_SCOPE_MEMBERS_INVALID:{benchmark}"
            )
        mapping = {}
        member_order = []
        for member in members:
            if not isinstance(member, dict) or set(member) != {
                "series_identity", "theme_id",
            }:
                raise KoreaCapitalRotationError(
                    f"OUTPUT_POLICY_MEMBER_FIELDS_MISMATCH:{benchmark}"
                )
            series = _identity(
                member.get("series_identity"), "OUTPUT_POLICY_SERIES_INVALID"
            )
            theme_id = _token(member.get("theme_id"), "OUTPUT_POLICY_THEME_ID_INVALID")
            if series in mapping or series in all_series or theme_id in all_theme_ids:
                raise KoreaCapitalRotationError("OUTPUT_POLICY_MEMBER_DUPLICATE")
            mapping[series] = theme_id
            member_order.append(series)
            all_series.add(series)
            all_theme_ids.add(theme_id)
        if member_order != sorted(member_order):
            raise KoreaCapitalRotationError(
                f"OUTPUT_POLICY_MEMBER_ORDER_INVALID:{benchmark}"
            )
        top_count = _positive_int(
            scope.get("top_count"), "OUTPUT_POLICY_TOP_COUNT_INVALID"
        )
        bottom_count = _positive_int(
            scope.get("bottom_count"), "OUTPUT_POLICY_BOTTOM_COUNT_INVALID"
        )
        if top_count + bottom_count > len(mapping):
            raise KoreaCapitalRotationError(
                f"OUTPUT_POLICY_BUCKETS_OVERLAP:{benchmark}"
            )
        if benchmark in policy_scopes:
            raise KoreaCapitalRotationError("OUTPUT_POLICY_BENCHMARK_DUPLICATE")
        policy_scope_order.append(benchmark)
        policy_scopes[benchmark] = {
            "mapping": mapping,
            "member_order": member_order,
            "top_count": top_count,
            "bottom_count": bottom_count,
        }
    if policy_scope_order != sorted(policy_scope_order):
        raise KoreaCapitalRotationError("OUTPUT_POLICY_SCOPE_ORDER_INVALID")

    raw_outputs = packet.get("benchmark_scopes")
    if not isinstance(raw_outputs, list) or len(raw_outputs) != len(policy_scopes):
        raise KoreaCapitalRotationError("OUTPUT_BENCHMARK_SCOPES_INVALID")
    output_order = []
    places = contract["output_decimal_places"]
    for raw_scope in raw_outputs:
        if not isinstance(raw_scope, dict) or set(raw_scope) != {
            "benchmark_identity", "top_themes", "bottom_themes",
            "theme_observations",
        }:
            raise KoreaCapitalRotationError("OUTPUT_SCOPE_FIELDS_MISMATCH")
        benchmark = _identity(
            raw_scope.get("benchmark_identity"), "OUTPUT_BENCHMARK_ID_INVALID"
        )
        if benchmark not in policy_scopes:
            raise KoreaCapitalRotationError("OUTPUT_BENCHMARK_NOT_IN_POLICY")
        output_order.append(benchmark)
        checked_scope = policy_scopes[benchmark]
        raw_rows = raw_scope.get("theme_observations")
        if not isinstance(raw_rows, list) or len(raw_rows) != len(checked_scope["mapping"]):
            raise KoreaCapitalRotationError(
                f"OUTPUT_THEME_OBSERVATIONS_INVALID:{benchmark}"
            )
        rows = []
        row_fields = {
            "series_identity", "theme_id", "role",
            "prior_relative_strength_vs_benchmark",
            "current_relative_strength_vs_benchmark", "relative_strength_change",
            "prior_rank_within_benchmark", "current_rank_within_benchmark",
            "rank_change_within_benchmark", "prior_bucket", "current_bucket",
            "bucket_transition", "p2_state",
        }
        for row in raw_rows:
            if not isinstance(row, dict) or set(row) != row_fields:
                raise KoreaCapitalRotationError(
                    f"OUTPUT_THEME_FIELDS_MISMATCH:{benchmark}"
                )
            series = _identity(row.get("series_identity"), "OUTPUT_SERIES_ID_INVALID")
            theme_id = _token(row.get("theme_id"), "OUTPUT_THEME_ID_INVALID")
            if (
                checked_scope["mapping"].get(series) != theme_id
                or row.get("role") not in contract["eligible_roles"]
                or row.get("p2_state") != "UNDEFINED_PENDING_P2_05"
            ):
                raise KoreaCapitalRotationError(
                    f"OUTPUT_THEME_BINDING_INVALID:{benchmark}:{series}"
                )
            prior = _decimal(
                row.get("prior_relative_strength_vs_benchmark"),
                f"OUTPUT_PRIOR_RELATIVE_STRENGTH_INVALID:{series}",
            )
            current = _decimal(
                row.get("current_relative_strength_vs_benchmark"),
                f"OUTPUT_CURRENT_RELATIVE_STRENGTH_INVALID:{series}",
            )
            if (
                prior <= Decimal(-1)
                or current <= Decimal(-1)
                or row["prior_relative_strength_vs_benchmark"] != _render(prior, places)
                or row["current_relative_strength_vs_benchmark"] != _render(current, places)
                or row.get("relative_strength_change") != _render(current - prior, places)
            ):
                raise KoreaCapitalRotationError(
                    f"OUTPUT_THEME_DERIVATION_MISMATCH:{series}"
                )
            rows.append({"series": series, "theme_id": theme_id, "prior": prior,
                         "current": current, "row": row})
        if [item["series"] for item in rows] != checked_scope["member_order"]:
            raise KoreaCapitalRotationError(
                f"OUTPUT_THEME_ORDER_MISMATCH:{benchmark}"
            )
        prior_ranked = [
            item["series"]
            for item in sorted(rows, key=lambda item: (-item["prior"], item["series"]))
        ]
        current_ranked = [
            item["series"]
            for item in sorted(rows, key=lambda item: (-item["current"], item["series"]))
        ]
        if effective:
            prior_ranks = {
                series: index + 1 for index, series in enumerate(prior_ranked)
            }
            current_ranks = {
                series: index + 1 for index, series in enumerate(current_ranked)
            }
            prior_buckets = _buckets(
                prior_ranked, checked_scope["top_count"], checked_scope["bottom_count"]
            )
            current_buckets = _buckets(
                current_ranked, checked_scope["top_count"], checked_scope["bottom_count"]
            )
            for item in rows:
                row = item["row"]
                series = item["series"]
                expected = {
                    "prior_rank_within_benchmark": prior_ranks[series],
                    "current_rank_within_benchmark": current_ranks[series],
                    "rank_change_within_benchmark": prior_ranks[series] - current_ranks[series],
                    "prior_bucket": prior_buckets[series],
                    "current_bucket": current_buckets[series],
                    "bucket_transition": (
                        f"{prior_buckets[series]}_TO_{current_buckets[series]}"
                    ),
                }
                if any(row.get(key) != value for key, value in expected.items()):
                    raise KoreaCapitalRotationError(
                        f"OUTPUT_RANK_BUCKET_MISMATCH:{benchmark}:{series}"
                    )
            expected_top = [
                checked_scope["mapping"][series]
                for series in current_ranked[: checked_scope["top_count"]]
            ]
            expected_bottom = [
                checked_scope["mapping"][series]
                for series in reversed(current_ranked[-checked_scope["bottom_count"] :])
            ]
            if (
                raw_scope.get("top_themes") != expected_top
                or raw_scope.get("bottom_themes") != expected_bottom
            ):
                raise KoreaCapitalRotationError(
                    f"OUTPUT_RANKING_SUMMARY_MISMATCH:{benchmark}"
                )
        else:
            for item in rows:
                if any(
                    item["row"].get(key) is not None
                    for key in (
                        "prior_rank_within_benchmark", "current_rank_within_benchmark",
                        "rank_change_within_benchmark", "prior_bucket", "current_bucket",
                        "bucket_transition",
                    )
                ):
                    raise KoreaCapitalRotationError("OUTPUT_UNAUTHORIZED_RANKING")
            if raw_scope.get("top_themes") != [] or raw_scope.get("bottom_themes") != []:
                raise KoreaCapitalRotationError(
                    f"OUTPUT_INEFFECTIVE_POLICY_SCOPE_MISMATCH:{benchmark}"
                )
    if output_order != policy_scope_order:
        raise KoreaCapitalRotationError("OUTPUT_SCOPE_ORDER_MISMATCH")
    expected_ranking_method = (
        {
            "metric": contract["ranking_metric"],
            "order": contract["ranking_order"],
            "tie_break": contract["tie_break"],
            "cross_benchmark_ranking": False,
        }
        if effective else None
    )
    if (
        packet.get("status")
        != ("ROTATION_BUCKETS_OBSERVED" if effective else "POLICY_NOT_EFFECTIVE")
        or packet.get("ranking_method") != expected_ranking_method
    ):
        raise KoreaCapitalRotationError("OUTPUT_POLICY_BOUNDARY_MISMATCH")
    if packet.get("retention") != {
        "input_policy": contract["input_retention_policy"],
        "output_policy": contract["output_retention_policy"],
        "source_close_rows_received": False,
        "source_closes_emitted": False,
        "reconstructive_series_emitted": False,
    }:
        raise KoreaCapitalRotationError("OUTPUT_RETENTION_MISMATCH")
    lineage = packet.get("lineage")
    lineage_fields = {
        "prior_upstream_packet_sha256", "current_upstream_packet_sha256",
        "rotation_policy_sha256", "taxonomy_packet_sha256",
        "upstream_leadership_policy_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise KoreaCapitalRotationError("OUTPUT_LINEAGE_FIELDS_MISMATCH")
    for key in lineage_fields:
        _sha(lineage.get(key), f"OUTPUT_LINEAGE_SHA_INVALID:{key}")
    if (
        lineage["rotation_policy_sha256"] != payload_sha256(policy)
        or lineage["taxonomy_packet_sha256"] != binding["taxonomy_packet_sha256"]
        or lineage["upstream_leadership_policy_sha256"]
        != binding["upstream_leadership_policy_sha256"]
    ):
        raise KoreaCapitalRotationError("OUTPUT_LINEAGE_BINDING_MISMATCH")
    expected_authority = copy.deepcopy(contract["authority"]) | {
        "theme_ranking_within_benchmark_authorized": effective,
        "top_bottom_bucket_authorized": effective,
        "bucket_transition_authorized": effective,
    }
    if packet.get("authority") != expected_authority:
        raise KoreaCapitalRotationError("OUTPUT_AUTHORITY_MISMATCH")
    if packet.get("unresolved_boundaries") != [
        "KOREA_BREADTH_DURABLE_AVAILABLE_AT_LINEAGE_NOT_IMPLEMENTED",
        "INVESTOR_FLOW_SOURCE_RELEASE_TIME_UNVERIFIED",
        "INVESTOR_FLOW_NXT_NOT_INCLUDED",
        "THEME_TAXONOMY_OPERATIONAL_POPULATION_NOT_IMPLEMENTED",
        "P2_STATE_VOCABULARY_PENDING_P2_05",
        "ROTATION_LEDGER_LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
        "BRIEFING_INTEGRATION_LIVE_OPERATIONAL_REPLAY_NOT_OBSERVED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]:
        raise KoreaCapitalRotationError("OUTPUT_BOUNDARIES_MISMATCH")
    digest = _sha(packet.get("payload_sha256"), "OUTPUT_PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("payload_sha256")
    if payload_sha256(normalized) != digest:
        raise KoreaCapitalRotationError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise KoreaCapitalRotationError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(input_path: Path, policy_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(
            output_path,
            build_packet(_read_json(input_path), _read_json(policy_path)),
        )
        return 0
    except (KoreaCapitalRotationError, OSError, TypeError, ValueError) as exc:
        print(f"Korea capital rotation failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build policy-gated Korea Theme rotation")
    parser.add_argument("input", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.policy, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
