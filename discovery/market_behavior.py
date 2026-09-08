#!/usr/bin/env python3
"""P3-07 policy-gated cross-market price/volume behavior radar.

Raw features are always policy-neutral: cumulative relative strength versus an
explicit benchmark and latest volume versus both prior-window mean and median.
A radar case may be recorded only when the caller supplies a structurally valid,
explicitly RATIFIED candidate policy.  No default thresholds live in the repo.
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
CONTRACT_PATH = ROOT / "config" / "market_behavior_radar_contract.json"
INPUT_SCHEMA_VERSION = "market_behavior_radar_input/1"
OUTPUT_SCHEMA_VERSION = "market_behavior_radar_packet/2"
POLICY_SCHEMA_VERSION = "market_behavior_candidate_policy/1"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class MarketBehaviorError(ValueError):
    """Fail-closed Market Behavior radar violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketBehaviorError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_policy = {
        "default_candidate_policy": "ABSENT",
        "anomaly_threshold": "UNRATIFIED",
        "cross_market_cadence": "UNRATIFIED",
        "source_hierarchy": "UNRATIFIED",
        "candidate_ranking": "UNRATIFIED",
    }
    expected_authority = {
        "raw_feature_observation_only_without_ratified_policy": True,
        "radar_case_recording_only_with_ratified_policy": True,
        "source_ranking_authorized": False,
        "importance_ranking_authorized": False,
        "candidate_ranking_authorized": False,
        "stage_promotion_authorized": False,
        "rule_evaluation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    expected = {
        "schema_version": 1,
        "contract_version": "market_behavior_radar/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "candidate_policy_schema_version": POLICY_SCHEMA_VERSION,
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "market_sources": {
            "CRYPTO": "kraken_public_api",
            "KOREA": "krx_open_api_stock_daily",
            "US": "tiingo_us_daily_price",
        },
        "source_hosts": {
            "kraken_public_api": ["api.kraken.com"],
            "krx_open_api_stock_daily": ["data-dbg.krx.co.kr"],
            "tiingo_us_daily_price": ["api.tiingo.com"],
        },
        "minimum_session_count": 3,
        "return_semantics": (
            "asset_cumulative_gross_return_div_benchmark_cumulative_gross_return_minus_one"
        ),
        "volume_features": {
            "LATEST_VS_PRIOR_MEAN": (
                "latest volume divided by arithmetic mean of all prior window volumes"
            ),
            "LATEST_VS_PRIOR_MEDIAN": (
                "latest volume divided by median of all prior window volumes"
            ),
        },
        "candidate_logic": (
            "relative_strength_gte_threshold_AND_selected_volume_ratio_gte_threshold"
        ),
        "output_decimal_places": 12,
        "policy_status": expected_policy,
        "authority": expected_authority,
    }
    if not isinstance(value, dict):
        raise MarketBehaviorError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise MarketBehaviorError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise MarketBehaviorError("CONTRACT_FIELDS_MISMATCH")
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
        raise MarketBehaviorError(f"UTC_INVALID:{value!r}")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _decimal(value, context: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise MarketBehaviorError(f"DECIMAL_NOT_STRING:{context}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise MarketBehaviorError(f"DECIMAL_INVALID:{context}") from exc
    if not result.is_finite() or (positive and result <= 0) or (
        nonnegative and result < 0
    ):
        raise MarketBehaviorError(f"DECIMAL_INVALID:{context}")
    return result


def _render(value: Decimal, contract: dict) -> str:
    with localcontext() as context:
        context.prec = 50
        quantum = Decimal(1).scaleb(-contract["output_decimal_places"])
        return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def _validate_source(
    source: dict, market: str, as_of: dt.datetime, contract: dict, context: str
) -> dict:
    if not isinstance(source, dict):
        raise MarketBehaviorError(f"SOURCE_IDENTITY_NOT_OBJECT:{context}")
    if set(source) != {
        "source_id",
        "source_url",
        "source_sha256",
        "available_at",
        "retrieved_at_utc",
    }:
        raise MarketBehaviorError(f"SOURCE_IDENTITY_FIELDS_MISMATCH:{context}")
    expected_id = contract["market_sources"][market]
    if source.get("source_id") != expected_id:
        raise MarketBehaviorError(f"SOURCE_ID_MISMATCH:{context}")
    parsed = urlparse(str(source.get("source_url") or ""))
    if (
        parsed.scheme != "https"
        or parsed.hostname not in contract["source_hosts"][expected_id]
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise MarketBehaviorError(f"SOURCE_URL_INVALID:{context}")
    sha = source.get("source_sha256")
    if not isinstance(sha, str) or SHA256_RE.fullmatch(sha) is None:
        raise MarketBehaviorError(f"SOURCE_SHA256_INVALID:{context}")
    available = source.get("available_at")
    retrieved = source.get("retrieved_at_utc")
    if not (_valid_date(available) or _valid_utc(available)) or not _valid_utc(retrieved):
        raise MarketBehaviorError(f"SOURCE_TIME_INVALID:{context}")
    retrieved_time = _utc(retrieved)
    if _valid_date(available):
        invalid_order = dt.date.fromisoformat(available) > retrieved_time.date()
        after_as_of = dt.date.fromisoformat(available) > as_of.date()
    else:
        available_time = _utc(available)
        invalid_order = available_time > retrieved_time
        after_as_of = available_time > as_of
    if invalid_order or after_as_of or retrieved_time > as_of:
        raise MarketBehaviorError(f"SOURCE_TEMPORAL_ORDER_INVALID:{context}")
    return copy.deepcopy(source)


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext() as context:
        context.prec = 50
        return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def volume_baseline_features(prior_volumes: list, latest_volume: Decimal) -> dict:
    """Source-independent latest-vs-prior-window volume arithmetic.

    This is the single implementation of the two published volume features
    (``LATEST_VS_PRIOR_MEAN`` / ``LATEST_VS_PRIOR_MEDIAN``): latest volume
    divided by the arithmetic mean, and by the median, of every prior window
    value, under this module's existing 50-digit Decimal context.

    It deliberately carries no market, source, policy, threshold, window
    selection or rendering choice, so a second caller reuses this exact
    arithmetic instead of restating it.  A zero mean or median denominator
    yields ``None`` (never ``0`` and never infinity) with a
    ``ZERO_BASELINE_UNKNOWN`` baseline status -- the observation is unknown,
    not neutral.  Returned values are unrendered ``Decimal``; each caller
    applies its own already-existing serialization.
    """
    if not isinstance(prior_volumes, list) or not prior_volumes:
        raise MarketBehaviorError("VOLUME_PRIOR_WINDOW_EMPTY")
    for value in [*prior_volumes, latest_volume]:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise MarketBehaviorError("VOLUME_VALUE_NOT_FINITE_DECIMAL")
    with localcontext() as context:
        context.prec = 50
        mean = sum(prior_volumes, Decimal(0)) / Decimal(len(prior_volumes))
        median = _median(prior_volumes)
        mean_ratio = latest_volume / mean if mean > 0 else None
        median_ratio = latest_volume / median if median > 0 else None
    return {
        "prior_count": len(prior_volumes),
        "latest": latest_volume,
        "prior_mean": mean,
        "prior_median": median,
        "latest_vs_prior_mean": mean_ratio,
        "latest_vs_prior_median": median_ratio,
        "baseline_status": (
            "OBSERVED" if mean > 0 and median > 0 else "ZERO_BASELINE_UNKNOWN"
        ),
    }


def _validate_policy(value: dict | None, contract: dict) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise MarketBehaviorError("POLICY_SCHEMA_MISMATCH")
    expected_keys = {
        "schema_version",
        "policy_id",
        "approval_status",
        "effective_from",
        "effective_to",
        "ratified_by",
        "ratified_at_utc",
        "rules",
    }
    if set(value) != expected_keys:
        raise MarketBehaviorError("POLICY_FIELDS_MISMATCH")
    policy_id = value.get("policy_id")
    if not isinstance(policy_id, str) or TOKEN_RE.fullmatch(policy_id) is None:
        raise MarketBehaviorError("POLICY_ID_INVALID")
    status = value.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise MarketBehaviorError("POLICY_APPROVAL_STATUS_INVALID")
    if not _valid_date(value.get("effective_from")):
        raise MarketBehaviorError("POLICY_EFFECTIVE_FROM_INVALID")
    end = value.get("effective_to")
    if end is not None and (not _valid_date(end) or end <= value["effective_from"]):
        raise MarketBehaviorError("POLICY_EFFECTIVE_TO_INVALID")
    if status == "RATIFIED" and (
        not isinstance(value.get("ratified_by"), str)
        or not value["ratified_by"].strip()
        or not _valid_utc(value.get("ratified_at_utc"))
    ):
        raise MarketBehaviorError("POLICY_RATIFICATION_PROOF_INVALID")
    if status == "UNRATIFIED" and (
        value.get("ratified_by") is not None
        or value.get("ratified_at_utc") is not None
    ):
        raise MarketBehaviorError("UNRATIFIED_POLICY_PROOF_FORBIDDEN")
    rules = value.get("rules")
    if not isinstance(rules, list):
        raise MarketBehaviorError("POLICY_RULES_NOT_LIST")
    if status == "RATIFIED" and not rules:
        raise MarketBehaviorError("RATIFIED_POLICY_RULES_EMPTY")
    seen = set()
    checked = []
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {
            "market",
            "window_id",
            "relative_strength_min",
            "volume_ratio_feature",
            "volume_ratio_min",
        }:
            raise MarketBehaviorError("POLICY_RULE_FIELDS_MISMATCH")
        market = rule.get("market")
        window_id = rule.get("window_id")
        if market not in contract["allowed_markets"]:
            raise MarketBehaviorError(f"POLICY_MARKET_INVALID:{market}")
        if not isinstance(window_id, str) or TOKEN_RE.fullmatch(window_id) is None:
            raise MarketBehaviorError("POLICY_WINDOW_ID_INVALID")
        if rule.get("volume_ratio_feature") not in contract["volume_features"]:
            raise MarketBehaviorError("POLICY_VOLUME_FEATURE_INVALID")
        _decimal(rule.get("relative_strength_min"), f"policy:{market}:relative")
        _decimal(rule.get("volume_ratio_min"), f"policy:{market}:volume", nonnegative=True)
        key = (market, window_id)
        if key in seen:
            raise MarketBehaviorError(f"POLICY_RULE_DUPLICATE:{market}:{window_id}")
        seen.add(key)
        checked.append(copy.deepcopy(rule))
    checked.sort(key=lambda rule: (rule["market"], rule["window_id"]))
    result = copy.deepcopy(value)
    result["rules"] = checked
    return result


def _policy_rule(policy: dict | None, market: str, window_id: str, observation: str):
    if policy is None or policy["approval_status"] != "RATIFIED":
        return None
    if observation < policy["effective_from"] or (
        policy["effective_to"] is not None and observation >= policy["effective_to"]
    ):
        return None
    return next(
        (
            rule
            for rule in policy["rules"]
            if rule["market"] == market and rule["window_id"] == window_id
        ),
        None,
    )


def _window_features(window: dict, as_of: dt.datetime, contract: dict) -> dict:
    if not isinstance(window, dict):
        raise MarketBehaviorError("WINDOW_NOT_OBJECT")
    if set(window) != {
        "window_id",
        "market",
        "benchmark_asset_id",
        "price_basis",
        "expected_sessions",
        "series",
    }:
        raise MarketBehaviorError("WINDOW_FIELDS_MISMATCH")
    window_id = window.get("window_id")
    market = window.get("market")
    benchmark = window.get("benchmark_asset_id")
    price_basis = window.get("price_basis")
    if not isinstance(window_id, str) or TOKEN_RE.fullmatch(window_id) is None:
        raise MarketBehaviorError("WINDOW_ID_INVALID")
    if market not in contract["allowed_markets"]:
        raise MarketBehaviorError(f"WINDOW_MARKET_INVALID:{window_id}:{market}")
    if not isinstance(benchmark, str) or TOKEN_RE.fullmatch(benchmark) is None:
        raise MarketBehaviorError(f"BENCHMARK_ID_INVALID:{window_id}")
    if not isinstance(price_basis, str) or not price_basis.strip():
        raise MarketBehaviorError(f"PRICE_BASIS_INVALID:{window_id}")
    sessions = window.get("expected_sessions")
    if (
        not isinstance(sessions, list)
        or len(sessions) < contract["minimum_session_count"]
        or sessions != sorted(set(sessions))
        or not all(_valid_date(day) for day in sessions)
    ):
        raise MarketBehaviorError(f"EXPECTED_SESSIONS_INVALID:{window_id}")
    series = window.get("series")
    if not isinstance(series, list) or not series:
        raise MarketBehaviorError(f"SERIES_EMPTY:{window_id}")
    by_asset = {}
    for item in series:
        if not isinstance(item, dict) or set(item) != {
            "asset_id",
            "price_basis",
            "source_identity",
            "rows",
        }:
            raise MarketBehaviorError(f"SERIES_FIELDS_MISMATCH:{window_id}")
        asset_id = item.get("asset_id")
        if not isinstance(asset_id, str) or TOKEN_RE.fullmatch(asset_id) is None:
            raise MarketBehaviorError(f"ASSET_ID_INVALID:{window_id}")
        if asset_id in by_asset:
            raise MarketBehaviorError(f"ASSET_ID_DUPLICATE:{window_id}:{asset_id}")
        if item.get("price_basis") != price_basis:
            raise MarketBehaviorError(f"PRICE_BASIS_MISMATCH:{window_id}:{asset_id}")
        source = _validate_source(
            item.get("source_identity"), market, as_of, contract, f"{window_id}:{asset_id}"
        )
        rows = item.get("rows")
        if not isinstance(rows, list) or [row.get("session_date") for row in rows if isinstance(row, dict)] != sessions:
            raise MarketBehaviorError(f"SESSION_COVERAGE_MISMATCH:{window_id}:{asset_id}")
        closes = []
        volumes = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"session_date", "close", "volume"}:
                raise MarketBehaviorError(f"ROW_FIELDS_MISMATCH:{window_id}:{asset_id}")
            closes.append(_decimal(row["close"], f"{asset_id}:close", positive=True))
            volumes.append(_decimal(row["volume"], f"{asset_id}:volume", nonnegative=True))
        by_asset[asset_id] = {"closes": closes, "volumes": volumes, "source": source}
    if benchmark not in by_asset:
        raise MarketBehaviorError(f"BENCHMARK_MISSING:{window_id}:{benchmark}")
    benchmark_values = by_asset[benchmark]
    with localcontext() as context:
        context.prec = 50
        benchmark_gross = (
            benchmark_values["closes"][-1] / benchmark_values["closes"][0]
        )
    features = []
    for asset_id in sorted(by_asset):
        values = by_asset[asset_id]
        prior = values["volumes"][:-1]
        latest = values["volumes"][-1]
        with localcontext() as context:
            context.prec = 50
            gross = values["closes"][-1] / values["closes"][0]
            relative_strength = gross / benchmark_gross - Decimal(1)
        volume = volume_baseline_features(prior, latest)
        mean_ratio = volume["latest_vs_prior_mean"]
        median_ratio = volume["latest_vs_prior_median"]
        features.append(
            {
                "asset_id": asset_id,
                "is_benchmark": asset_id == benchmark,
                "observed_session_count": len(sessions),
                "relative_strength_vs_benchmark": _render(relative_strength, contract),
                "latest_volume_vs_prior_mean": (
                    _render(mean_ratio, contract) if mean_ratio is not None else None
                ),
                "latest_volume_vs_prior_median": (
                    _render(median_ratio, contract)
                    if median_ratio is not None
                    else None
                ),
                "volume_baseline_status": volume["baseline_status"],
                "source_identity": copy.deepcopy(values["source"]),
                "benchmark_source_identity": copy.deepcopy(
                    benchmark_values["source"]
                ),
                "candidate_policy_match": None,
                "radar_case_created": False,
                "candidate_rank": None,
                "stage_transition": None,
                "action": None,
            }
        )
    return {
        "window_id": window_id,
        "market": market,
        "observation_date": sessions[-1],
        "benchmark_asset_id": benchmark,
        "price_basis": price_basis,
        "window": {
            "first_session": sessions[0],
            "last_session": sessions[-1],
            "session_count": len(sessions),
            "exact_expected_sessions": True,
        },
        "features": features,
        "raw_rows_emitted": False,
        "reconstructive_price_volume_series_emitted": False,
    }


def _apply_policy(window: dict, policy: dict | None, contract: dict) -> list[dict]:
    rule = _policy_rule(
        policy, window["market"], window["window_id"], window["observation_date"]
    )
    if rule is None:
        window["candidate_policy_status"] = (
            "ABSENT_OR_UNRATIFIED" if policy is None or policy["approval_status"] != "RATIFIED"
            else "NO_EFFECTIVE_MATCHING_RULE"
        )
        return []
    window["candidate_policy_status"] = "RATIFIED_RULE_APPLIED"
    rs_min = _decimal(rule["relative_strength_min"], "policy:relative")
    volume_min = _decimal(rule["volume_ratio_min"], "policy:volume", nonnegative=True)
    field = {
        "LATEST_VS_PRIOR_MEAN": "latest_volume_vs_prior_mean",
        "LATEST_VS_PRIOR_MEDIAN": "latest_volume_vs_prior_median",
    }[rule["volume_ratio_feature"]]
    policy_sha = payload_sha256(policy)
    cases = []
    for feature in window["features"]:
        if feature["is_benchmark"]:
            feature["candidate_policy_match"] = False
            continue
        volume_value = feature[field]
        matched = volume_value is not None and (
            Decimal(feature["relative_strength_vs_benchmark"]) >= rs_min
            and Decimal(volume_value) >= volume_min
        )
        feature["candidate_policy_match"] = matched
        if not matched:
            continue
        feature["radar_case_created"] = True
        seed = {
            "policy_id": policy["policy_id"],
            "window_id": window["window_id"],
            "asset_id": feature["asset_id"],
            "observation_date": window["observation_date"],
        }
        cases.append(
            {
                "schema_version": "market_behavior_case/1",
                "case_id": "RADAR-MB-" + payload_sha256(seed)[:16].upper(),
                "asset_id": feature["asset_id"],
                "market": window["market"],
                "observation_date": window["observation_date"],
                "why_found": {
                    "benchmark_asset_id": window["benchmark_asset_id"],
                    "window": copy.deepcopy(window["window"]),
                    "relative_strength_vs_benchmark": feature[
                        "relative_strength_vs_benchmark"
                    ],
                    "relative_strength_min": rule["relative_strength_min"],
                    "volume_ratio_feature": rule["volume_ratio_feature"],
                    "volume_ratio": volume_value,
                    "volume_ratio_min": rule["volume_ratio_min"],
                    "candidate_logic": contract["candidate_logic"],
                },
                "source_identity": {
                    "asset": copy.deepcopy(feature["source_identity"]),
                    "benchmark": copy.deepcopy(
                        feature["benchmark_source_identity"]
                    ),
                },
                "candidate_policy": {
                    "policy_id": policy["policy_id"],
                    "policy_sha256": policy_sha,
                    "ratified_by": policy["ratified_by"],
                    "ratified_at_utc": policy["ratified_at_utc"],
                },
                "importance": "UNRATIFIED",
                "candidate_rank": None,
                "investable_eligible": False,
                "stage_transition": None,
                "action": None,
            }
        )
    return cases


def _output_decimal(value, context: str, contract: dict, *, nonnegative=False) -> str:
    parsed = _decimal(value, context, nonnegative=nonnegative)
    if value != _render(parsed, contract):
        raise MarketBehaviorError(f"OUTPUT_DECIMAL_NOT_CANONICAL:{context}")
    return value


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate retained output semantics and the complete source policy."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet_fields = {
        "schema_version",
        "contract_version",
        "as_of_utc",
        "status",
        "window_count",
        "case_count",
        "candidate_policy",
        "source_policy",
        "market_windows",
        "cases",
        "policy_status",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != packet_fields:
        raise MarketBehaviorError("OUTPUT_FIELDS_MISMATCH")
    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise MarketBehaviorError("OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != digest:
        raise MarketBehaviorError("OUTPUT_SHA256_MISMATCH")
    as_of_utc = packet.get("as_of_utc")
    if (
        packet.get("schema_version") != OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "MARKET_BEHAVIOR_FEATURES_OBSERVED"
        or not _valid_utc(as_of_utc)
    ):
        raise MarketBehaviorError("OUTPUT_IDENTITY_MISMATCH")
    as_of = _utc(as_of_utc)

    source_policy = _validate_policy(packet.get("source_policy"), contract)
    if (
        source_policy is not None
        and source_policy["approval_status"] == "RATIFIED"
        and _utc(source_policy["ratified_at_utc"]) > as_of
    ):
        raise MarketBehaviorError("OUTPUT_POLICY_RATIFIED_AFTER_AS_OF")
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
        raise MarketBehaviorError("OUTPUT_POLICY_SOURCE_MISMATCH")

    windows = packet.get("market_windows")
    if not isinstance(windows, list) or not windows:
        raise MarketBehaviorError("OUTPUT_WINDOWS_EMPTY")
    window_fields = {
        "window_id",
        "market",
        "observation_date",
        "benchmark_asset_id",
        "price_basis",
        "window",
        "features",
        "raw_rows_emitted",
        "reconstructive_price_volume_series_emitted",
        "candidate_policy_status",
    }
    feature_fields = {
        "asset_id",
        "is_benchmark",
        "observed_session_count",
        "relative_strength_vs_benchmark",
        "latest_volume_vs_prior_mean",
        "latest_volume_vs_prior_median",
        "volume_baseline_status",
        "source_identity",
        "benchmark_source_identity",
        "candidate_policy_match",
        "radar_case_created",
        "candidate_rank",
        "stage_transition",
        "action",
    }
    features_by_case_id = {}
    window_keys = []
    for window in windows:
        if not isinstance(window, dict) or set(window) != window_fields:
            raise MarketBehaviorError("OUTPUT_WINDOW_FIELDS_MISMATCH")
        market = window.get("market")
        window_id = window.get("window_id")
        benchmark_id = window.get("benchmark_asset_id")
        observation_date = window.get("observation_date")
        if (
            market not in contract["allowed_markets"]
            or not isinstance(window_id, str)
            or TOKEN_RE.fullmatch(window_id) is None
            or not isinstance(benchmark_id, str)
            or TOKEN_RE.fullmatch(benchmark_id) is None
            or not _valid_date(observation_date)
            or observation_date > as_of_utc[:10]
            or not isinstance(window.get("price_basis"), str)
            or not window["price_basis"].strip()
            or window.get("raw_rows_emitted") is not False
            or window.get("reconstructive_price_volume_series_emitted") is not False
        ):
            raise MarketBehaviorError("OUTPUT_WINDOW_IDENTITY_MISMATCH")
        boundary = window.get("window")
        if not isinstance(boundary, dict) or set(boundary) != {
            "first_session",
            "last_session",
            "session_count",
            "exact_expected_sessions",
        }:
            raise MarketBehaviorError("OUTPUT_WINDOW_BOUNDARY_FIELDS_MISMATCH")
        if (
            not _valid_date(boundary.get("first_session"))
            or boundary.get("last_session") != observation_date
            or boundary["first_session"] > boundary["last_session"]
            or type(boundary.get("session_count")) is not int
            or boundary["session_count"] < contract["minimum_session_count"]
            or boundary.get("exact_expected_sessions") is not True
        ):
            raise MarketBehaviorError("OUTPUT_WINDOW_BOUNDARY_MISMATCH")
        policy_status = window.get("candidate_policy_status")
        rule = _policy_rule(source_policy, market, window_id, observation_date)
        expected_policy_status = (
            "ABSENT_OR_UNRATIFIED"
            if source_policy is None
            or source_policy["approval_status"] != "RATIFIED"
            else "RATIFIED_RULE_APPLIED"
            if rule is not None
            else "NO_EFFECTIVE_MATCHING_RULE"
        )
        if policy_status not in {
            "ABSENT_OR_UNRATIFIED",
            "NO_EFFECTIVE_MATCHING_RULE",
            "RATIFIED_RULE_APPLIED",
        }:
            raise MarketBehaviorError("OUTPUT_WINDOW_POLICY_STATUS_INVALID")
        if policy_status != expected_policy_status:
            raise MarketBehaviorError("OUTPUT_WINDOW_POLICY_STATUS_MISMATCH")

        features = window.get("features")
        if not isinstance(features, list) or not features:
            raise MarketBehaviorError("OUTPUT_FEATURES_EMPTY")
        benchmark_source = None
        feature_ids = []
        benchmark_count = 0
        for feature in features:
            if not isinstance(feature, dict) or set(feature) != feature_fields:
                raise MarketBehaviorError("OUTPUT_FEATURE_FIELDS_MISMATCH")
            asset_id = feature.get("asset_id")
            is_benchmark = asset_id == benchmark_id
            if (
                not isinstance(asset_id, str)
                or TOKEN_RE.fullmatch(asset_id) is None
                or feature.get("is_benchmark") is not is_benchmark
                or type(feature.get("observed_session_count")) is not int
                or feature["observed_session_count"] != boundary["session_count"]
                or feature.get("candidate_rank") is not None
                or feature.get("stage_transition") is not None
                or feature.get("action") is not None
            ):
                raise MarketBehaviorError("OUTPUT_FEATURE_IDENTITY_OR_AUTHORITY_MISMATCH")
            relative = _output_decimal(
                feature.get("relative_strength_vs_benchmark"), asset_id, contract
            )
            if is_benchmark and relative != _render(Decimal(0), contract):
                raise MarketBehaviorError("OUTPUT_BENCHMARK_RELATIVE_STRENGTH_MISMATCH")
            ratios = []
            for field in (
                "latest_volume_vs_prior_mean",
                "latest_volume_vs_prior_median",
            ):
                value = feature.get(field)
                ratios.append(
                    None
                    if value is None
                    else _output_decimal(value, f"{asset_id}:{field}", contract, nonnegative=True)
                )
            baseline = feature.get("volume_baseline_status")
            if (
                baseline not in {"OBSERVED", "ZERO_BASELINE_UNKNOWN"}
                or (baseline == "OBSERVED" and any(value is None for value in ratios))
                or (baseline == "ZERO_BASELINE_UNKNOWN" and all(value is not None for value in ratios))
            ):
                raise MarketBehaviorError("OUTPUT_VOLUME_BASELINE_MISMATCH")
            source = _validate_source(
                feature.get("source_identity"), market, as_of, contract, asset_id
            )
            linked_benchmark_source = _validate_source(
                feature.get("benchmark_source_identity"),
                market,
                as_of,
                contract,
                f"{asset_id}:benchmark",
            )
            if is_benchmark:
                benchmark_count += 1
                benchmark_source = source
            match = feature.get("candidate_policy_match")
            created = feature.get("radar_case_created")
            if rule is not None:
                volume_field = {
                    "LATEST_VS_PRIOR_MEAN": "latest_volume_vs_prior_mean",
                    "LATEST_VS_PRIOR_MEDIAN": "latest_volume_vs_prior_median",
                }[rule["volume_ratio_feature"]]
                volume_value = feature[volume_field]
                expected_match = False if is_benchmark else (
                    volume_value is not None
                    and Decimal(relative) >= Decimal(rule["relative_strength_min"])
                    and Decimal(volume_value) >= Decimal(rule["volume_ratio_min"])
                )
                if match is not expected_match or created is not expected_match:
                    raise MarketBehaviorError("OUTPUT_FEATURE_POLICY_RESULT_MISMATCH")
                if is_benchmark and match is not False:
                    raise MarketBehaviorError("OUTPUT_BENCHMARK_POLICY_MATCH_INVALID")
            elif match is not None or created is not False:
                raise MarketBehaviorError("OUTPUT_FEATURE_POLICY_RESULT_MISMATCH")
            if created:
                seed = {
                    "policy_id": policy["policy_id"],
                    "window_id": window_id,
                    "asset_id": asset_id,
                    "observation_date": observation_date,
                }
                case_id = "RADAR-MB-" + payload_sha256(seed)[:16].upper()
                features_by_case_id[case_id] = {
                    "feature": feature,
                    "window": window,
                    "source": source,
                    "benchmark_source": linked_benchmark_source,
                    "rule": rule,
                }
            feature_ids.append(asset_id)
        if feature_ids != sorted(set(feature_ids)) or benchmark_count != 1:
            raise MarketBehaviorError("OUTPUT_FEATURE_ORDER_OR_BENCHMARK_MISMATCH")
        if any(
            feature["benchmark_source_identity"] != benchmark_source
            for feature in features
        ):
            raise MarketBehaviorError("OUTPUT_BENCHMARK_SOURCE_DRIFT")
        window_keys.append((market, window_id))
    if window_keys != sorted(set(window_keys)):
        raise MarketBehaviorError("OUTPUT_WINDOW_ORDER_OR_DUPLICATE_INVALID")

    cases = packet.get("cases")
    if not isinstance(cases, list):
        raise MarketBehaviorError("OUTPUT_CASES_NOT_LIST")
    case_fields = {
        "schema_version",
        "case_id",
        "asset_id",
        "market",
        "observation_date",
        "why_found",
        "source_identity",
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
            raise MarketBehaviorError("OUTPUT_CASE_FIELDS_MISMATCH")
        case_id = case.get("case_id")
        match = features_by_case_id.get(case_id)
        if match is None:
            raise MarketBehaviorError("OUTPUT_CASE_IDENTITY_MISMATCH")
        feature = match["feature"]
        window = match["window"]
        rule = match["rule"]
        why = case.get("why_found")
        why_fields = {
            "benchmark_asset_id",
            "window",
            "relative_strength_vs_benchmark",
            "relative_strength_min",
            "volume_ratio_feature",
            "volume_ratio",
            "volume_ratio_min",
            "candidate_logic",
        }
        if not isinstance(why, dict) or set(why) != why_fields:
            raise MarketBehaviorError("OUTPUT_CASE_REASON_FIELDS_MISMATCH")
        ratio_field = {
            "LATEST_VS_PRIOR_MEAN": "latest_volume_vs_prior_mean",
            "LATEST_VS_PRIOR_MEDIAN": "latest_volume_vs_prior_median",
        }.get(why.get("volume_ratio_feature"))
        relative_min = _decimal(why.get("relative_strength_min"), case_id)
        volume_min = _decimal(why.get("volume_ratio_min"), case_id, nonnegative=True)
        if (
            case.get("schema_version") != "market_behavior_case/1"
            or case.get("asset_id") != feature["asset_id"]
            or case.get("market") != window["market"]
            or case.get("observation_date") != window["observation_date"]
            or why.get("benchmark_asset_id") != window["benchmark_asset_id"]
            or why.get("window") != window["window"]
            or why.get("relative_strength_vs_benchmark")
            != feature["relative_strength_vs_benchmark"]
            or ratio_field is None
            or why.get("volume_ratio") != feature[ratio_field]
            or why.get("relative_strength_min") != rule["relative_strength_min"]
            or why.get("volume_ratio_feature") != rule["volume_ratio_feature"]
            or why.get("volume_ratio_min") != rule["volume_ratio_min"]
            or why.get("candidate_logic") != contract["candidate_logic"]
            or Decimal(feature["relative_strength_vs_benchmark"]) < relative_min
            or feature[ratio_field] is None
            or Decimal(feature[ratio_field]) < volume_min
        ):
            raise MarketBehaviorError("OUTPUT_CASE_REASON_DERIVATION_MISMATCH")
        source_identity = case.get("source_identity")
        if source_identity != {
            "asset": match["source"],
            "benchmark": match["benchmark_source"],
        }:
            raise MarketBehaviorError("OUTPUT_CASE_SOURCE_LINEAGE_MISMATCH")
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
            raise MarketBehaviorError("OUTPUT_CASE_POLICY_LINEAGE_MISMATCH")
        if (
            case.get("importance") != "UNRATIFIED"
            or case.get("candidate_rank") is not None
            or case.get("investable_eligible") is not False
            or case.get("stage_transition") is not None
            or case.get("action") is not None
        ):
            raise MarketBehaviorError("OUTPUT_CASE_AUTHORITY_EXPANSION")
        case_ids.append(case_id)
    if case_ids != sorted(set(case_ids)) or set(case_ids) != set(features_by_case_id):
        raise MarketBehaviorError("OUTPUT_CASE_SET_OR_ORDER_MISMATCH")

    expected_boundaries = [
        "DEFAULT_CANDIDATE_POLICY_ABSENT",
        "ANOMALY_THRESHOLD_UNRATIFIED",
        "CROSS_MARKET_CADENCE_UNRATIFIED",
        "SOURCE_HIERARCHY_UNRATIFIED",
        "CANDIDATE_RANKING_UNRATIFIED",
        "LIVE_RADAR_POPULATION_NOT_IMPLEMENTED",
    ]
    if (
        type(packet.get("window_count")) is not int
        or packet["window_count"] != len(windows)
        or type(packet.get("case_count")) is not int
        or packet["case_count"] != len(cases)
        or packet.get("policy_status") != contract["policy_status"]
        or packet.get("authority") != contract["authority"]
        or packet.get("unresolved_boundaries") != expected_boundaries
    ):
        raise MarketBehaviorError("OUTPUT_SUMMARY_OR_BOUNDARY_MISMATCH")
    return copy.deepcopy(packet)


def build_packet(
    value: dict, candidate_policy: dict | None = None, contract: dict | None = None
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    policy = _validate_policy(candidate_policy, contract)
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise MarketBehaviorError("INPUT_SCHEMA_MISMATCH")
    if set(value) != {"schema_version", "as_of_utc", "market_windows"}:
        raise MarketBehaviorError("INPUT_FIELDS_MISMATCH")
    as_of_utc = value.get("as_of_utc")
    if not _valid_utc(as_of_utc):
        raise MarketBehaviorError("AS_OF_UTC_INVALID")
    if (
        policy is not None
        and policy["approval_status"] == "RATIFIED"
        and _utc(policy["ratified_at_utc"]) > _utc(as_of_utc)
    ):
        raise MarketBehaviorError("POLICY_RATIFIED_AFTER_AS_OF")
    windows_raw = value.get("market_windows")
    if not isinstance(windows_raw, list):
        raise MarketBehaviorError("MARKET_WINDOWS_NOT_LIST")
    if not windows_raw:
        raise MarketBehaviorError("MARKET_WINDOWS_EMPTY")
    windows = []
    cases = []
    seen = set()
    for raw in windows_raw:
        window = _window_features(raw, _utc(as_of_utc), contract)
        key = (window["market"], window["window_id"])
        if key in seen:
            raise MarketBehaviorError(f"WINDOW_DUPLICATE:{key[0]}:{key[1]}")
        seen.add(key)
        cases.extend(_apply_policy(window, policy, contract))
        windows.append(window)
    windows.sort(key=lambda item: (item["market"], item["window_id"]))
    cases.sort(key=lambda item: item["case_id"])
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "as_of_utc": as_of_utc,
        "status": "MARKET_BEHAVIOR_FEATURES_OBSERVED",
        "window_count": len(windows),
        "case_count": len(cases),
        "candidate_policy": (
            None
            if policy is None
            else {
                "policy_id": policy["policy_id"],
                "approval_status": policy["approval_status"],
                "policy_sha256": payload_sha256(policy),
            }
        ),
        "source_policy": copy.deepcopy(policy),
        "market_windows": windows,
        "cases": cases,
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "DEFAULT_CANDIDATE_POLICY_ABSENT",
            "ANOMALY_THRESHOLD_UNRATIFIED",
            "CROSS_MARKET_CADENCE_UNRATIFIED",
            "SOURCE_HIERARCHY_UNRATIFIED",
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


def run(
    input_path: Path,
    output_path: Path,
    policy_path: Path | None = None,
    contract_path: Path = CONTRACT_PATH,
) -> dict:
    policy = _read_json(policy_path) if policy_path is not None else None
    packet = build_packet(
        _read_json(input_path), policy, load_contract(contract_path)
    )
    write_json_atomic(output_path, packet)
    return packet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)
    try:
        packet = run(args.input, args.out, args.policy, args.contract)
    except MarketBehaviorError as exc:
        print(f"market behavior radar failed: {exc}")
        return 1
    print(
        f"market behavior radar: windows={packet['window_count']} "
        f"cases={packet['case_count']} sha256={packet['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
