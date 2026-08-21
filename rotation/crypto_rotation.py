#!/usr/bin/env python3
"""P2-04 external-policy-gated BTC/ETH/ALT rotation transform."""
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
CONTRACT_PATH = ROOT / "config" / "crypto_rotation_contract.json"
INPUT_SCHEMA_VERSION = "crypto_rotation_input/1"
POLICY_SCHEMA_VERSION = "crypto_rotation_policy/1"
OUTPUT_SCHEMA_VERSION = "crypto_rotation_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
AUTHORITY_FIELDS = {
    "leader_classification_authorized", "ranking_authorized",
    "threshold_authorized", "regime_score_authorized",
    "production_wiring_authorized", "trading_action_authorized",
}


class CryptoRotationError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoRotationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_rotation/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "upstream_contract_version": "crypto_leadership_contract/v2",
        "measurement": "crypto_bucket_relative_rotation_observation",
        "allowed_window_ids": ["pilot_7d", "primary_30d"],
        "bucket_ids": ["ALT", "BTC", "ETH"],
        "ranking_metric": "BUCKET_RELATIVE_STRENGTH_VS_BTC",
        "ranking_order": "DESCENDING",
        "tie_break": "BUCKET_ID_ASC",
        "sector_chain_policy": "UNKNOWN_NOT_RANKING_INPUT",
        "transition_semantics": "PRIOR_BUCKET_TO_CURRENT_BUCKET",
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
        "repository_default_policy": "ABSENT",
        "authority": {
            "external_ratified_rotation_policy_only": True,
            "sector_chain_ranking_authorized": False,
            "asset_ranking_authorized": False,
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
        raise CryptoRotationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CryptoRotationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str):
        raise CryptoRotationError(code)
    try:
        result = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CryptoRotationError(code) from exc
    if result.isoformat() != value:
        raise CryptoRotationError(code)
    return result


def _timestamp(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise CryptoRotationError(code)
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CryptoRotationError(code) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise CryptoRotationError(code)
    return result.astimezone(dt.timezone.utc)


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CryptoRotationError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise CryptoRotationError(code)
    return value


def _positive_int(value, code: str) -> int:
    if type(value) is not int or value < 1:
        raise CryptoRotationError(code)
    return value


def _decimal(value, code: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise CryptoRotationError(code)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CryptoRotationError(code) from exc
    if not result.is_finite() or (positive and result <= 0):
        raise CryptoRotationError(code)
    return result


def _render(value: Decimal, places: int) -> str:
    try:
        with localcontext() as context:
            context.prec = 50
            result = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise CryptoRotationError("OUTPUT_NUMBER_INVALID") from exc
    if result == 0:
        result = Decimal(0)
    text = format(result, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _validate_policies(value: dict, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"universe", "leadership", "taxonomy"}:
        raise CryptoRotationError(f"UPSTREAM_POLICIES_INVALID:{label}")
    required = {
        "universe": {"policy_version", "policy_sha256", "approval_status", "universe_kind"},
        "leadership": {
            "policy_version", "policy_sha256", "approval_status",
            "group_return_method", "group_coverage_policy_status",
        },
        "taxonomy": {"policy_version", "policy_sha256", "approval_status", "effective_dated"},
    }
    checked = {}
    for name in ("universe", "leadership", "taxonomy"):
        item = value.get(name)
        if not isinstance(item, dict) or set(item) != required[name]:
            raise CryptoRotationError(f"UPSTREAM_POLICY_FIELDS_INVALID:{label}:{name}")
        if name != "taxonomy" and item.get("approval_status") != "RATIFIED":
            raise CryptoRotationError(f"UPSTREAM_POLICY_UNRATIFIED:{label}:{name}")
        checked[name] = _sha(item.get("policy_sha256"), f"UPSTREAM_POLICY_SHA_INVALID:{label}:{name}")
    if (
        value["leadership"].get("group_coverage_policy_status") != "UNRATIFIED"
        or value["leadership"].get("group_return_method") != "equal_weight_daily_rebalanced"
        or value["taxonomy"].get("effective_dated") is not True
    ):
        raise CryptoRotationError(f"UPSTREAM_POLICY_SEMANTICS_INVALID:{label}")
    return checked


def _validate_upstream(value: dict, label: str, window_id: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "market", "measurement", "status",
        "unknown_reason", "as_of_date", "windows", "policies", "current_candle",
        "lineage",
    } | AUTHORITY_FIELDS
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoRotationError(f"UPSTREAM_FIELDS_MISMATCH:{label}")
    if (
        value.get("schema_version") != 2
        or value.get("contract_version") != contract["upstream_contract_version"]
        or value.get("market") != "CRYPTO"
        or value.get("measurement") != "raw_relative_strength_observation"
        or value.get("status") not in {"OBSERVED_UNCLASSIFIED", "PARTIAL"}
    ):
        raise CryptoRotationError(f"UPSTREAM_IDENTITY_INVALID:{label}")
    if any(value[field] is not False for field in AUTHORITY_FIELDS):
        raise CryptoRotationError(f"UPSTREAM_AUTHORITY_EXPANDED:{label}")
    as_of_date = _date(value.get("as_of_date"), f"UPSTREAM_DATE_INVALID:{label}")
    current = value.get("current_candle")
    if (
        not isinstance(current, dict)
        or current.get("excluded_for_every_member_and_point") is not True
        or current.get("reason") != "source_documents_not_yet_committed_timeframe"
    ):
        raise CryptoRotationError(f"UPSTREAM_CURRENT_CANDLE_INVALID:{label}")
    policies = _validate_policies(value.get("policies"), label)
    windows = value.get("windows")
    if not isinstance(windows, list) or [item.get("window_id") for item in windows if isinstance(item, dict)] != contract["allowed_window_ids"]:
        raise CryptoRotationError(f"UPSTREAM_WINDOWS_INVALID:{label}")
    selected = next(item for item in windows if item["window_id"] == window_id)
    expected_window_fields = {
        "window_id", "role", "status", "unknown_reason", "window", "blockers",
        "source_unknown_points", "asset_relative_strength", "partial_window_assets",
        "group_relative_strength", "daily_points", "lineage",
    }
    if not isinstance(selected, dict) or set(selected) != expected_window_fields or selected.get("status") != "OBSERVED_UNCLASSIFIED":
        raise CryptoRotationError(f"UPSTREAM_SELECTED_WINDOW_NOT_OBSERVED:{label}")
    descriptor = selected.get("window")
    if not isinstance(descriptor, dict) or set(descriptor) != {
        "window_id", "role", "start_date", "end_date", "lookback_calendar_days",
        "required_point_count", "available_point_count", "missing_dates",
        "exact_contiguous_calendar_days",
    }:
        raise CryptoRotationError(f"UPSTREAM_WINDOW_DESCRIPTOR_INVALID:{label}")
    lookback = _positive_int(descriptor.get("lookback_calendar_days"), f"UPSTREAM_LOOKBACK_INVALID:{label}")
    start_date = _date(
        descriptor.get("start_date"),
        f"UPSTREAM_WINDOW_DATE_INVALID:{label}",
    )
    if (
        descriptor.get("window_id") != window_id
        or _date(descriptor.get("end_date"), f"UPSTREAM_WINDOW_DATE_INVALID:{label}") != as_of_date
        or start_date != as_of_date - dt.timedelta(days=lookback - 1)
        or descriptor.get("required_point_count") != lookback
        or descriptor.get("available_point_count") != lookback
        or descriptor.get("missing_dates") != []
        or descriptor.get("exact_contiguous_calendar_days") is not True
    ):
        raise CryptoRotationError(f"UPSTREAM_WINDOW_COVERAGE_INVALID:{label}")
    groups = selected.get("group_relative_strength")
    if not isinstance(groups, dict) or set(groups) != {"bucket", "sector_chain"}:
        raise CryptoRotationError(f"UPSTREAM_GROUP_LAYER_INVALID:{label}")
    sector = groups.get("sector_chain")
    if (
        not isinstance(sector, dict) or sector.get("status") != "UNKNOWN"
        or sector.get("sector") != [] or sector.get("chain") != []
        or sector.get("group_coverage_policy_status") != "UNRATIFIED"
    ):
        raise CryptoRotationError(f"UPSTREAM_SECTOR_CHAIN_AUTHORITY_INVALID:{label}")
    buckets = {}
    bucket_order = []
    bucket_fields = {
        "group_id", "status", "unknown_reason", "missing_dates", "observed_day_count",
        "required_day_count", "minimum_daily_member_count",
        "required_minimum_member_count", "cumulative_gross_return",
        "relative_strength_vs_btc", "classification",
    }
    for item in groups.get("bucket", []):
        if not isinstance(item, dict) or set(item) != bucket_fields:
            raise CryptoRotationError(f"UPSTREAM_BUCKET_FIELDS_INVALID:{label}")
        bucket_id = item.get("group_id")
        bucket_order.append(bucket_id)
        if (
            bucket_id not in contract["bucket_ids"]
            or item.get("status") != "OBSERVED_UNCLASSIFIED"
            or item.get("unknown_reason") is not None or item.get("missing_dates") != []
            or item.get("observed_day_count") != lookback
            or item.get("required_day_count") != lookback
            or _positive_int(item.get("minimum_daily_member_count"), f"UPSTREAM_BUCKET_COUNT_INVALID:{label}") < 1
            or item.get("required_minimum_member_count") is not None
            or item.get("classification") != "UNDEFINED"
        ):
            raise CryptoRotationError(f"UPSTREAM_BUCKET_SEMANTICS_INVALID:{label}:{bucket_id}")
        buckets[bucket_id] = {
            "cumulative_gross_return": _decimal(
                item.get("cumulative_gross_return"), f"UPSTREAM_BUCKET_RETURN_INVALID:{label}", positive=True
            ),
            "relative_strength_vs_btc": _decimal(
                item.get("relative_strength_vs_btc"), f"UPSTREAM_BUCKET_RS_INVALID:{label}"
            ),
        }
    if bucket_order != contract["bucket_ids"] or set(buckets) != set(contract["bucket_ids"]):
        raise CryptoRotationError(f"UPSTREAM_BUCKET_SET_INVALID:{label}")
    if buckets["BTC"]["relative_strength_vs_btc"] != 0:
        raise CryptoRotationError(f"UPSTREAM_BTC_REFERENCE_INVALID:{label}")
    btc_return = buckets["BTC"]["cumulative_gross_return"]
    for bucket_id, item in buckets.items():
        if item["relative_strength_vs_btc"] != (
            item["cumulative_gross_return"] / btc_return - Decimal(1)
        ):
            raise CryptoRotationError(
                f"UPSTREAM_BUCKET_RS_INCONSISTENT:{label}:{bucket_id}"
            )
    daily = selected.get("daily_points")
    if not isinstance(daily, list) or len(daily) != lookback:
        raise CryptoRotationError(f"UPSTREAM_DAILY_POINTS_INVALID:{label}")
    selected_manifests = []
    previous_available_at = None
    available_at = None
    for index, point in enumerate(daily):
        expected_date = start_date + dt.timedelta(days=index)
        if not isinstance(point, dict) or point.get("as_of_date") != expected_date.isoformat():
            raise CryptoRotationError(f"UPSTREAM_DAILY_POINT_DATE_INVALID:{label}")
        point_lineage = point.get("lineage")
        if not isinstance(point_lineage, dict):
            raise CryptoRotationError(f"UPSTREAM_DAILY_LINEAGE_INVALID:{label}")
        available_at = _timestamp(
            point_lineage.get("available_at"),
            f"UPSTREAM_AVAILABLE_AT_INVALID:{label}",
        )
        manifest_sha = _sha(
            point_lineage.get("manifest_sha256"),
            f"UPSTREAM_MANIFEST_SHA_INVALID:{label}",
        )
        if previous_available_at is not None and available_at <= previous_available_at:
            raise CryptoRotationError(f"UPSTREAM_DAILY_AVAILABILITY_ORDER_INVALID:{label}")
        previous_available_at = available_at
        selected_manifests.append(
            {"as_of_date": expected_date.isoformat(), "manifest_sha256": manifest_sha}
        )
    window_lineage = selected.get("lineage")
    if (
        not isinstance(window_lineage, dict)
        or window_lineage.get("pit_status") != "independent_as_captured_daily_snapshots"
        or window_lineage.get("current_catalog_backfill_authorized") is not False
        or window_lineage.get("manifest_sha256_by_date") != selected_manifests
    ):
        raise CryptoRotationError(f"UPSTREAM_WINDOW_LINEAGE_INVALID:{label}")
    top_lineage = value.get("lineage")
    if (
        not isinstance(top_lineage, dict)
        or top_lineage.get("pit_status") != "independent_as_captured_daily_snapshots"
        or top_lineage.get("current_catalog_backfill_authorized") is not False
        or not isinstance(top_lineage.get("manifest_sha256_by_date"), list)
    ):
        raise CryptoRotationError(f"UPSTREAM_LINEAGE_INVALID:{label}")
    top_manifests = top_lineage["manifest_sha256_by_date"]
    if any(
        not isinstance(item, dict)
        or set(item) != {"as_of_date", "manifest_sha256"}
        for item in top_manifests
    ):
        raise CryptoRotationError(f"UPSTREAM_LINEAGE_INVALID:{label}")
    top_manifest_map = {}
    for item in top_manifests:
        day = _date(item["as_of_date"], f"UPSTREAM_LINEAGE_INVALID:{label}")
        manifest_sha = _sha(
            item["manifest_sha256"], f"UPSTREAM_LINEAGE_INVALID:{label}"
        )
        if day.isoformat() in top_manifest_map:
            raise CryptoRotationError(f"UPSTREAM_LINEAGE_INVALID:{label}")
        top_manifest_map[day.isoformat()] = manifest_sha
    if any(
        top_manifest_map.get(item["as_of_date"]) != item["manifest_sha256"]
        for item in selected_manifests
    ):
        raise CryptoRotationError(f"UPSTREAM_LINEAGE_MISMATCH:{label}")
    return {
        "as_of_date": as_of_date, "available_at": available_at, "lookback": lookback,
        "buckets": buckets, "policies": policies, "packet_sha256": payload_sha256(value),
    }


def _validate_policy(value: dict, prior: dict, current: dict, contract: dict) -> tuple[dict, bool]:
    fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to", "window_id",
        "bucket_ids", "universe_policy_sha256", "leadership_policy_sha256",
        "taxonomy_policy_sha256", "ranking_metric", "ranking_order", "tie_break",
        "top_count", "bottom_count", "maximum_calendar_gap_days",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoRotationError("POLICY_FIELDS_MISMATCH")
    if value.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise CryptoRotationError("POLICY_SCHEMA_MISMATCH")
    _token(value.get("policy_id"), "POLICY_ID_INVALID")
    status = value.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise CryptoRotationError("POLICY_APPROVAL_STATUS_INVALID")
    start = _date(value.get("effective_from"), "POLICY_EFFECTIVE_FROM_INVALID")
    end = None if value.get("effective_to") is None else _date(value["effective_to"], "POLICY_EFFECTIVE_TO_INVALID")
    if end is not None and end <= start:
        raise CryptoRotationError("POLICY_EFFECTIVE_TO_INVALID")
    if value.get("window_id") not in contract["allowed_window_ids"]:
        raise CryptoRotationError("POLICY_WINDOW_INVALID")
    if value.get("bucket_ids") != contract["bucket_ids"]:
        raise CryptoRotationError("POLICY_BUCKET_SET_INVALID")
    for field, name in (
        ("universe_policy_sha256", "universe"),
        ("leadership_policy_sha256", "leadership"),
        ("taxonomy_policy_sha256", "taxonomy"),
    ):
        if value.get(field) != prior["policies"][name] or value.get(field) != current["policies"][name]:
            raise CryptoRotationError(f"POLICY_UPSTREAM_HASH_MISMATCH:{name}")
    if (
        value.get("ranking_metric") != contract["ranking_metric"]
        or value.get("ranking_order") != contract["ranking_order"]
        or value.get("tie_break") != contract["tie_break"]
    ):
        raise CryptoRotationError("POLICY_RANKING_METHOD_INVALID")
    top = _positive_int(value.get("top_count"), "POLICY_TOP_COUNT_INVALID")
    bottom = _positive_int(value.get("bottom_count"), "POLICY_BOTTOM_COUNT_INVALID")
    gap = _positive_int(value.get("maximum_calendar_gap_days"), "POLICY_GAP_INVALID")
    if top + bottom > len(contract["bucket_ids"]):
        raise CryptoRotationError("POLICY_BUCKETS_OVERLAP")
    covers = start <= prior["as_of_date"] and (end is None or current["as_of_date"] < end)
    if status == "UNRATIFIED":
        if value.get("ratified_by") is not None or value.get("ratified_at_utc") is not None:
            raise CryptoRotationError("UNRATIFIED_POLICY_PROOF_FORBIDDEN")
    else:
        if (
            not isinstance(value.get("ratified_by"), str) or not value["ratified_by"].strip()
            or not isinstance(value.get("ratified_at_utc"), str) or not value["ratified_at_utc"].endswith("Z")
        ):
            raise CryptoRotationError("POLICY_RATIFICATION_PROOF_INVALID")
        if covers and _timestamp(value["ratified_at_utc"], "POLICY_RATIFICATION_PROOF_INVALID") > prior["available_at"]:
            raise CryptoRotationError("POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION")
    effective = status == "RATIFIED" and covers
    if effective and (current["as_of_date"] - prior["as_of_date"]).days > gap:
        raise CryptoRotationError("OBSERVATION_GAP_EXCEEDS_POLICY")
    return copy.deepcopy(value), effective


def _rank(buckets: dict) -> list[str]:
    return sorted(buckets, key=lambda item: (-buckets[item]["relative_strength_vs_btc"], item))


def _buckets(ranked: list[str], top: int, bottom: int) -> dict[str, str]:
    top_ids, bottom_ids = set(ranked[:top]), set(ranked[-bottom:])
    return {item: "TOP" if item in top_ids else "BOTTOM" if item in bottom_ids else "MIDDLE" for item in ranked}


def build_packet(value: dict, policy: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise CryptoRotationError("INPUT_SCHEMA_MISMATCH")
    if set(value) != {"schema_version", "as_of_date", "prior_observation", "current_observation"}:
        raise CryptoRotationError("INPUT_FIELDS_MISMATCH")
    window_id = policy.get("window_id") if isinstance(policy, dict) else None
    if window_id not in contract["allowed_window_ids"]:
        raise CryptoRotationError("POLICY_WINDOW_INVALID")
    prior = _validate_upstream(value.get("prior_observation"), "prior", window_id, contract)
    current = _validate_upstream(value.get("current_observation"), "current", window_id, contract)
    as_of = _date(value.get("as_of_date"), "AS_OF_DATE_INVALID")
    if not prior["as_of_date"] < current["as_of_date"] == as_of:
        raise CryptoRotationError("OBSERVATION_DATE_ORDER_INVALID")
    if prior["available_at"] >= current["available_at"] or prior["lookback"] != current["lookback"]:
        raise CryptoRotationError("UPSTREAM_TIME_OR_WINDOW_DRIFT")
    checked, effective = _validate_policy(policy, prior, current, contract)
    if effective:
        prior_ranked, current_ranked = _rank(prior["buckets"]), _rank(current["buckets"])
        prior_rank = {item: index + 1 for index, item in enumerate(prior_ranked)}
        current_rank = {item: index + 1 for index, item in enumerate(current_ranked)}
        prior_bucket = _buckets(prior_ranked, checked["top_count"], checked["bottom_count"])
        current_bucket = _buckets(current_ranked, checked["top_count"], checked["bottom_count"])
        top_groups = current_ranked[: checked["top_count"]]
        bottom_groups = list(reversed(current_ranked[-checked["bottom_count"] :]))
    else:
        prior_rank = current_rank = prior_bucket = current_bucket = {}
        top_groups = bottom_groups = []
    observations = []
    for bucket_id in contract["bucket_ids"]:
        before = prior["buckets"][bucket_id]["relative_strength_vs_btc"]
        after = current["buckets"][bucket_id]["relative_strength_vs_btc"]
        observations.append({
            "bucket_id": bucket_id,
            "prior_relative_strength_vs_btc": _render(before, contract["output_decimal_places"]),
            "current_relative_strength_vs_btc": _render(after, contract["output_decimal_places"]),
            "relative_strength_change": _render(after - before, contract["output_decimal_places"]),
            "prior_rank": prior_rank.get(bucket_id), "current_rank": current_rank.get(bucket_id),
            "rank_change": prior_rank[bucket_id] - current_rank[bucket_id] if effective else None,
            "prior_bucket": prior_bucket.get(bucket_id), "current_bucket": current_bucket.get(bucket_id),
            "bucket_transition": f"{prior_bucket[bucket_id]}_TO_{current_bucket[bucket_id]}" if effective else None,
            "p2_state": "UNDEFINED_PENDING_P2_05",
        })
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION, "contract_version": contract["contract_version"],
        "measurement": contract["measurement"], "market": "CRYPTO", "as_of_date": as_of.isoformat(),
        "status": "ROTATION_BUCKETS_OBSERVED" if effective else "POLICY_NOT_EFFECTIVE",
        "window_id": window_id, "lookback_calendar_days": current["lookback"],
        "rotation_policy": checked, "rotation_policy_effective": effective,
        "ranking_method": {
            "metric": contract["ranking_metric"], "order": contract["ranking_order"],
            "tie_break": contract["tie_break"],
        } if effective else None,
        "top_groups": top_groups, "bottom_groups": bottom_groups,
        "bucket_observations": observations,
        "sector_chain_layer": {
            "status": "UNKNOWN", "ranking_input_authorized": False,
            "reason": "GROUP_COVERAGE_POLICY_UNRATIFIED",
        },
        "lineage": {
            "prior_upstream_packet_sha256": prior["packet_sha256"],
            "current_upstream_packet_sha256": current["packet_sha256"],
            "rotation_policy_sha256": payload_sha256(checked),
            "universe_policy_sha256": current["policies"]["universe"],
            "leadership_policy_sha256": current["policies"]["leadership"],
            "taxonomy_policy_sha256": current["policies"]["taxonomy"],
        },
        "authority": copy.deepcopy(contract["authority"]) | {
            "bucket_ranking_authorized": effective,
            "top_bottom_bucket_authorized": effective,
            "bucket_transition_authorized": effective,
        },
        "unresolved_boundaries": [
            "SECTOR_CHAIN_GROUP_COVERAGE_POLICY_UNRATIFIED",
            "SECTOR_CHAIN_ROTATION_NOT_IMPLEMENTED", "P2_STATE_VOCABULARY_PENDING_P2_05",
            "ROTATION_LEDGER_NOT_IMPLEMENTED", "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate complete v1 output semantics without inventing omitted dates."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "measurement", "market",
        "as_of_date", "status", "window_id", "lookback_calendar_days",
        "rotation_policy", "rotation_policy_effective", "ranking_method",
        "top_groups", "bottom_groups", "bucket_observations",
        "sector_chain_layer", "lineage", "authority", "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise CryptoRotationError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != OUTPUT_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("measurement") != contract["measurement"]
        or packet.get("market") != "CRYPTO"
    ):
        raise CryptoRotationError("OUTPUT_IDENTITY_INVALID")
    as_of = _date(packet.get("as_of_date"), "OUTPUT_AS_OF_DATE_INVALID")
    window_id = packet.get("window_id")
    if window_id not in contract["allowed_window_ids"]:
        raise CryptoRotationError("OUTPUT_WINDOW_INVALID")
    expected_lookback = {"pilot_7d": 7, "primary_30d": 30}[window_id]
    if packet.get("lookback_calendar_days") != expected_lookback:
        raise CryptoRotationError("OUTPUT_LOOKBACK_MISMATCH")

    policy = packet.get("rotation_policy")
    policy_fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to", "window_id",
        "bucket_ids", "universe_policy_sha256", "leadership_policy_sha256",
        "taxonomy_policy_sha256", "ranking_metric", "ranking_order", "tie_break",
        "top_count", "bottom_count", "maximum_calendar_gap_days",
    }
    if not isinstance(policy, dict) or set(policy) != policy_fields:
        raise CryptoRotationError("OUTPUT_POLICY_FIELDS_MISMATCH")
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise CryptoRotationError("OUTPUT_POLICY_SCHEMA_MISMATCH")
    _token(policy.get("policy_id"), "OUTPUT_POLICY_ID_INVALID")
    approval = policy.get("approval_status")
    if approval not in {"RATIFIED", "UNRATIFIED"}:
        raise CryptoRotationError("OUTPUT_POLICY_STATUS_INVALID")
    effective_from = _date(
        policy.get("effective_from"), "OUTPUT_POLICY_EFFECTIVE_FROM_INVALID"
    )
    effective_to = (
        None
        if policy.get("effective_to") is None
        else _date(policy["effective_to"], "OUTPUT_POLICY_EFFECTIVE_TO_INVALID")
    )
    if effective_to is not None and effective_to <= effective_from:
        raise CryptoRotationError("OUTPUT_POLICY_EFFECTIVE_TO_INVALID")
    if (
        policy.get("window_id") != window_id
        or policy.get("bucket_ids") != contract["bucket_ids"]
        or policy.get("ranking_metric") != contract["ranking_metric"]
        or policy.get("ranking_order") != contract["ranking_order"]
        or policy.get("tie_break") != contract["tie_break"]
    ):
        raise CryptoRotationError("OUTPUT_POLICY_BINDING_INVALID")
    for field in (
        "universe_policy_sha256", "leadership_policy_sha256",
        "taxonomy_policy_sha256",
    ):
        _sha(policy.get(field), f"OUTPUT_POLICY_SHA_INVALID:{field}")
    top_count = _positive_int(policy.get("top_count"), "OUTPUT_TOP_COUNT_INVALID")
    bottom_count = _positive_int(
        policy.get("bottom_count"), "OUTPUT_BOTTOM_COUNT_INVALID"
    )
    _positive_int(
        policy.get("maximum_calendar_gap_days"), "OUTPUT_MAXIMUM_GAP_INVALID"
    )
    if top_count + bottom_count > len(contract["bucket_ids"]):
        raise CryptoRotationError("OUTPUT_POLICY_BUCKETS_OVERLAP")
    if approval == "UNRATIFIED":
        if policy.get("ratified_by") is not None or policy.get("ratified_at_utc") is not None:
            raise CryptoRotationError("OUTPUT_UNRATIFIED_PROOF_FORBIDDEN")
    else:
        if (
            not isinstance(policy.get("ratified_by"), str)
            or not policy["ratified_by"].strip()
            or not isinstance(policy.get("ratified_at_utc"), str)
            or not policy["ratified_at_utc"].endswith("Z")
        ):
            raise CryptoRotationError("OUTPUT_RATIFICATION_PROOF_INVALID")
        _timestamp(policy["ratified_at_utc"], "OUTPUT_RATIFICATION_PROOF_INVALID")
    effective = packet.get("rotation_policy_effective")
    if type(effective) is not bool:
        raise CryptoRotationError("OUTPUT_POLICY_EFFECTIVE_INVALID")
    current_date_eligible = (
        effective_from <= as_of and (effective_to is None or as_of < effective_to)
    )
    if effective and (approval != "RATIFIED" or not current_date_eligible):
        raise CryptoRotationError("OUTPUT_POLICY_EFFECTIVE_MISMATCH")

    raw_rows = packet.get("bucket_observations")
    row_fields = {
        "bucket_id", "prior_relative_strength_vs_btc",
        "current_relative_strength_vs_btc", "relative_strength_change",
        "prior_rank", "current_rank", "rank_change", "prior_bucket",
        "current_bucket", "bucket_transition", "p2_state",
    }
    if not isinstance(raw_rows, list) or len(raw_rows) != len(contract["bucket_ids"]):
        raise CryptoRotationError("OUTPUT_BUCKET_OBSERVATIONS_INVALID")
    rows = []
    places = contract["output_decimal_places"]
    for row in raw_rows:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise CryptoRotationError("OUTPUT_BUCKET_FIELDS_MISMATCH")
        bucket_id = row.get("bucket_id")
        if bucket_id not in contract["bucket_ids"]:
            raise CryptoRotationError("OUTPUT_BUCKET_ID_INVALID")
        prior = _decimal(
            row.get("prior_relative_strength_vs_btc"),
            f"OUTPUT_PRIOR_RELATIVE_STRENGTH_INVALID:{bucket_id}",
        )
        current = _decimal(
            row.get("current_relative_strength_vs_btc"),
            f"OUTPUT_CURRENT_RELATIVE_STRENGTH_INVALID:{bucket_id}",
        )
        if (
            prior <= Decimal(-1)
            or current <= Decimal(-1)
            or row["prior_relative_strength_vs_btc"] != _render(prior, places)
            or row["current_relative_strength_vs_btc"] != _render(current, places)
            or row.get("relative_strength_change") != _render(current - prior, places)
            or row.get("p2_state") != "UNDEFINED_PENDING_P2_05"
            or (bucket_id == "BTC" and (prior != 0 or current != 0))
        ):
            raise CryptoRotationError(
                f"OUTPUT_BUCKET_DERIVATION_MISMATCH:{bucket_id}"
            )
        rows.append({"bucket_id": bucket_id, "prior": prior, "current": current,
                     "row": row})
    if [item["bucket_id"] for item in rows] != contract["bucket_ids"]:
        raise CryptoRotationError("OUTPUT_BUCKET_ORDER_MISMATCH")
    prior_ranked = [
        item["bucket_id"]
        for item in sorted(rows, key=lambda item: (-item["prior"], item["bucket_id"]))
    ]
    current_ranked = [
        item["bucket_id"]
        for item in sorted(rows, key=lambda item: (-item["current"], item["bucket_id"]))
    ]
    if effective:
        prior_ranks = {
            bucket_id: index + 1 for index, bucket_id in enumerate(prior_ranked)
        }
        current_ranks = {
            bucket_id: index + 1 for index, bucket_id in enumerate(current_ranked)
        }
        prior_buckets = _buckets(prior_ranked, top_count, bottom_count)
        current_buckets = _buckets(current_ranked, top_count, bottom_count)
        for item in rows:
            row = item["row"]
            bucket_id = item["bucket_id"]
            expected = {
                "prior_rank": prior_ranks[bucket_id],
                "current_rank": current_ranks[bucket_id],
                "rank_change": prior_ranks[bucket_id] - current_ranks[bucket_id],
                "prior_bucket": prior_buckets[bucket_id],
                "current_bucket": current_buckets[bucket_id],
                "bucket_transition": (
                    f"{prior_buckets[bucket_id]}_TO_{current_buckets[bucket_id]}"
                ),
            }
            if any(row.get(key) != value for key, value in expected.items()):
                raise CryptoRotationError(
                    f"OUTPUT_RANK_BUCKET_MISMATCH:{bucket_id}"
                )
        expected_ranking = {
            "metric": contract["ranking_metric"],
            "order": contract["ranking_order"],
            "tie_break": contract["tie_break"],
        }
        if (
            packet.get("status") != "ROTATION_BUCKETS_OBSERVED"
            or packet.get("ranking_method") != expected_ranking
            or packet.get("top_groups") != current_ranked[:top_count]
            or packet.get("bottom_groups")
            != list(reversed(current_ranked[-bottom_count:]))
        ):
            raise CryptoRotationError("OUTPUT_RANKING_SUMMARY_MISMATCH")
    else:
        for item in rows:
            if any(
                item["row"].get(key) is not None
                for key in (
                    "prior_rank", "current_rank", "rank_change", "prior_bucket",
                    "current_bucket", "bucket_transition",
                )
            ):
                raise CryptoRotationError("OUTPUT_UNAUTHORIZED_RANKING")
        if (
            packet.get("status") != "POLICY_NOT_EFFECTIVE"
            or packet.get("ranking_method") is not None
            or packet.get("top_groups") != []
            or packet.get("bottom_groups") != []
        ):
            raise CryptoRotationError("OUTPUT_INEFFECTIVE_POLICY_BOUNDARY_MISMATCH")
    if packet.get("sector_chain_layer") != {
        "status": "UNKNOWN",
        "ranking_input_authorized": False,
        "reason": "GROUP_COVERAGE_POLICY_UNRATIFIED",
    }:
        raise CryptoRotationError("OUTPUT_SECTOR_CHAIN_BOUNDARY_MISMATCH")
    lineage = packet.get("lineage")
    lineage_fields = {
        "prior_upstream_packet_sha256", "current_upstream_packet_sha256",
        "rotation_policy_sha256", "universe_policy_sha256",
        "leadership_policy_sha256", "taxonomy_policy_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise CryptoRotationError("OUTPUT_LINEAGE_FIELDS_MISMATCH")
    for key in lineage_fields:
        _sha(lineage.get(key), f"OUTPUT_LINEAGE_SHA_INVALID:{key}")
    if (
        lineage["rotation_policy_sha256"] != payload_sha256(policy)
        or lineage["universe_policy_sha256"] != policy["universe_policy_sha256"]
        or lineage["leadership_policy_sha256"] != policy["leadership_policy_sha256"]
        or lineage["taxonomy_policy_sha256"] != policy["taxonomy_policy_sha256"]
    ):
        raise CryptoRotationError("OUTPUT_LINEAGE_BINDING_MISMATCH")
    expected_authority = copy.deepcopy(contract["authority"]) | {
        "bucket_ranking_authorized": effective,
        "top_bottom_bucket_authorized": effective,
        "bucket_transition_authorized": effective,
    }
    if packet.get("authority") != expected_authority:
        raise CryptoRotationError("OUTPUT_AUTHORITY_MISMATCH")
    if packet.get("unresolved_boundaries") != [
        "SECTOR_CHAIN_GROUP_COVERAGE_POLICY_UNRATIFIED",
        "SECTOR_CHAIN_ROTATION_NOT_IMPLEMENTED",
        "P2_STATE_VOCABULARY_PENDING_P2_05",
        "ROTATION_LEDGER_NOT_IMPLEMENTED",
        "BRIEFING_INTEGRATION_NOT_IMPLEMENTED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]:
        raise CryptoRotationError("OUTPUT_BOUNDARIES_MISMATCH")
    digest = _sha(packet.get("payload_sha256"), "OUTPUT_PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("payload_sha256")
    if payload_sha256(normalized) != digest:
        raise CryptoRotationError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise CryptoRotationError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def run(input_path: Path, policy_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(input_path), _read_json(policy_path)))
        return 0
    except (CryptoRotationError, OSError, TypeError, ValueError) as exc:
        print(f"Crypto rotation failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build policy-gated Crypto bucket rotation")
    parser.add_argument("input", type=Path); parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True); args = parser.parse_args()
    return run(args.input, args.policy, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
