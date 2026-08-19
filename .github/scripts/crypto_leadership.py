#!/usr/bin/env python3
"""Build raw Crypto relative-strength observations from CR-06 snapshots.

The approved seven-day pilot and 30-day primary windows are evaluated
independently. Missing history or an UNKNOWN CR-06 point stops only the
affected window. Missing sector/chain taxonomy stops only that group layer;
deterministic BTC/ETH/Alt buckets and asset observations remain available.

Output is offline evidence only. It never classifies a leader, ranks assets,
applies thresholds, scores a Regime, publishes a Production factor, or trades.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
BREADTH_SCRIPT = ROOT / ".github" / "scripts" / "crypto_breadth.py"
CONTRACT_PATH = ROOT / "config" / "crypto_leadership_contract.json"
LEADERSHIP_POLICY_PATH = ROOT / "config" / "crypto_leadership_policy.json"
TAXONOMY_PATH = ROOT / "config" / "crypto_asset_taxonomy.json"
UNIVERSE_POLICY_PATH = ROOT / "config" / "crypto_breadth_universe_policy.json"
IDENTITY_EXCEPTIONS_PATH = (
    ROOT / "config" / "crypto_asset_identity_exceptions.json"
)
BUCKETS = ("ALT", "BTC", "ETH")


class LeadershipError(RuntimeError):
    """Fail-closed contract, policy, taxonomy, or replay violation."""


def fail(code: str, detail: str) -> None:
    raise LeadershipError(f"{code}: {detail}")


def load_breadth_module(path: Path = BREADTH_SCRIPT):
    spec = importlib.util.spec_from_file_location("atlas_crypto_breadth", path)
    if spec is None or spec.loader is None:
        fail("SOURCE_HELPER_INVALID", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BREADTH = load_breadth_module()


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_json_constant,
        )
    except (OSError, json.JSONDecodeError, InvalidOperation) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "root must be object")
    return value


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("FILE_HASH_INVALID", str(exc))


def parse_date(value: object, code: str, label: str) -> dt.date:
    if not isinstance(value, str):
        fail(code, label)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(code, f"{label}={value}")


def optional_date(value: Optional[str], label: str) -> Optional[dt.date]:
    if value is None:
        return None
    return parse_date(value, "WINDOW_DATE_INVALID", label)


def sorted_strings(value: object, code: str, label: str) -> list:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        fail(code, label)
    return value


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_INVALID")
    pinned = {
        "schema_version": 2,
        "contract_version": "crypto_leadership_contract/v2",
        "source_helper": ".github/scripts/crypto_breadth.py",
        "source_transform_version": "crypto_breadth_observation/v2",
        "market_timezone": "UTC",
        "measurement": "raw_relative_strength_observation",
        "window_policy": "independent_exact_contiguous_calendar_days",
        "daily_return_semantics": (
            "latest_finalized_close_div_previous_finalized_close"
        ),
        "relative_strength_semantics": (
            "cumulative_gross_return_div_btc_cumulative_gross_return_minus_one"
        ),
        "cross_snapshot_close_policy": (
            "same_asset_adjacent_close_must_match"
        ),
        "current_candle_policy": "exclude_last_row_always",
        "bucket_policy": "btc_eth_else_alt",
        "taxonomy_policy": (
            "optional_effective_dated_sector_chain_no_overlap"
        ),
        "sector_chain_unknown_policy": (
            "unknown_group_layer_without_asset_or_bucket_propagation"
        ),
        "supported_group_return_methods": [
            "equal_weight_daily_rebalanced"
        ],
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
    }
    if set(contract) != set(pinned) or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def load_leadership_policy(path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    policy = read_json(path, "LEADERSHIP_POLICY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "effective_from",
        "windows",
        "group_return_method",
        "relative_strength_reference",
        "bucket_policy",
        "sector_chain_missing_policy",
        "group_coverage_policy_status",
    }
    if set(policy) != expected:
        fail("LEADERSHIP_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 2
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
        or policy.get("relative_strength_reference") != "BTC"
        or policy.get("bucket_policy") != "btc_eth_else_alt"
        or policy.get("sector_chain_missing_policy")
        != "unknown_group_layer"
        or policy.get("group_coverage_policy_status") != "UNRATIFIED"
    ):
        fail("LEADERSHIP_POLICY_INVALID", "header")
    effective = policy.get("effective_from")
    if effective is not None:
        parse_date(effective, "LEADERSHIP_POLICY_INVALID", "effective_from")
    method = policy.get("group_return_method")
    if method is not None and method != "equal_weight_daily_rebalanced":
        fail("LEADERSHIP_POLICY_INVALID", "group_return_method")
    windows = policy.get("windows")
    if not isinstance(windows, list):
        fail("LEADERSHIP_POLICY_INVALID", "windows")
    normalized = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict) or set(window) != {
            "window_id",
            "role",
            "lookback_calendar_days",
        }:
            fail("LEADERSHIP_POLICY_INVALID", f"window {index} schema")
        if (
            not isinstance(window["window_id"], str)
            or not window["window_id"]
            or window["role"] not in {"PILOT", "PRIMARY"}
            or type(window["lookback_calendar_days"]) is not int
            or window["lookback_calendar_days"] < 1
        ):
            fail("LEADERSHIP_POLICY_INVALID", f"window {index}")
        normalized.append(window)
    if len({item["window_id"] for item in normalized}) != len(normalized):
        fail("LEADERSHIP_POLICY_INVALID", "duplicate window_id")
    if policy["approval_status"] == "RATIFIED":
        if effective is None or method is None:
            fail("LEADERSHIP_POLICY_INVALID", "ratified fields incomplete")
        approved = [
            {
                "window_id": "pilot_7d",
                "role": "PILOT",
                "lookback_calendar_days": 7,
            },
            {
                "window_id": "primary_30d",
                "role": "PRIMARY",
                "lookback_calendar_days": 30,
            },
        ]
        if normalized != approved:
            fail("LEADERSHIP_POLICY_INVALID", "approved windows mismatch")
    return policy


def require_ratified_leadership_policy(policy: dict) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("LEADERSHIP_POLICY_UNRATIFIED", policy["policy_version"])


def load_taxonomy(path: Path = TAXONOMY_PATH) -> dict:
    policy = read_json(path, "TAXONOMY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "source_name",
        "effective_from",
        "records",
    }
    if set(policy) != expected:
        fail("TAXONOMY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
        or policy.get("source_name") != "kraken_spot_market_data"
        or not isinstance(policy.get("records"), list)
    ):
        fail("TAXONOMY_INVALID", "header")
    effective = policy.get("effective_from")
    if effective is not None:
        parse_date(effective, "TAXONOMY_INVALID", "effective_from")

    normalized = []
    for index, record in enumerate(policy["records"]):
        keys = {
            "canonical_asset_id",
            "effective_from",
            "effective_to",
            "bucket",
            "sectors",
            "chains",
            "reason",
        }
        if not isinstance(record, dict) or set(record) != keys:
            fail("TAXONOMY_INVALID", f"record {index} schema")
        asset_id = record["canonical_asset_id"]
        expected_bucket = asset_id if asset_id in {"BTC", "ETH"} else "ALT"
        if (
            not isinstance(asset_id, str)
            or BREADTH.ASSET_ID.fullmatch(asset_id) is None
            or record["bucket"] != expected_bucket
        ):
            fail("TAXONOMY_INVALID", f"record {index} identity")
        sectors = sorted_strings(
            record["sectors"], "TAXONOMY_INVALID", f"record {index} sectors"
        )
        chains = sorted_strings(
            record["chains"], "TAXONOMY_INVALID", f"record {index} chains"
        )
        if not sectors or not chains:
            fail("TAXONOMY_INVALID", f"record {index} groups empty")
        start = parse_date(
            record["effective_from"],
            "TAXONOMY_INVALID",
            f"record {index} effective_from",
        )
        end = None
        if record["effective_to"] is not None:
            end = parse_date(
                record["effective_to"],
                "TAXONOMY_INVALID",
                f"record {index} effective_to",
            )
            if end < start:
                fail("TAXONOMY_INVALID", f"record {index} range")
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            fail("TAXONOMY_INVALID", f"record {index} reason")
        normalized.append(record | {"_start": start, "_end": end})

    by_asset = {}
    for record in normalized:
        by_asset.setdefault(record["canonical_asset_id"], []).append(record)
    for asset_id, records in by_asset.items():
        records.sort(key=lambda item: item["_start"])
        for before, after in zip(records, records[1:]):
            before_end = before["_end"] or dt.date.max
            if after["_start"] <= before_end:
                fail("TAXONOMY_RANGE_OVERLAP", asset_id)
    if policy["approval_status"] == "RATIFIED" and (
        effective is None or not normalized
    ):
        fail("TAXONOMY_INVALID", "ratified fields incomplete")
    return policy | {"_records": normalized}


def taxonomy_for(asset_id: str, day: dt.date, policy: dict) -> Optional[dict]:
    matches = []
    for record in policy["_records"]:
        end = record["_end"] or dt.date.max
        if (
            record["canonical_asset_id"] == asset_id
            and record["_start"] <= day <= end
        ):
            matches.append(record)
    if len(matches) > 1:
        fail("TAXONOMY_RANGE_OVERLAP", f"{asset_id}@{day.isoformat()}")
    if not matches:
        return None
    return {"sectors": matches[0]["sectors"], "chains": matches[0]["chains"]}


def discover_snapshot_map(snapshot_root: Path) -> dict:
    root = Path(snapshot_root)
    if not root.is_dir():
        fail("SNAPSHOT_ROOT_INVALID", str(root))
    snapshots = {}
    try:
        children = sorted(item for item in root.iterdir() if item.is_dir())
    except OSError as exc:
        fail("SNAPSHOT_ROOT_INVALID", str(exc))
    for path in children:
        try:
            vintage = dt.date.fromisoformat(path.name)
        except ValueError:
            continue
        as_of = vintage - dt.timedelta(days=1)
        if as_of in snapshots:
            fail("SNAPSHOT_DATE_DUPLICATE", as_of.isoformat())
        snapshots[as_of] = path
    return snapshots


def decimal_text(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        fail("SOURCE_POINT_INVALID", label)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        fail("SOURCE_POINT_INVALID", label)
    if not parsed.is_finite() or parsed <= 0:
        fail("SOURCE_POINT_INVALID", label)
    return parsed


def deterministic_bucket(asset_id: str) -> str:
    return asset_id if asset_id in {"BTC", "ETH"} else "ALT"


def taxonomy_window_state(taxonomy: dict, start: dt.date) -> tuple:
    if taxonomy["approval_status"] != "RATIFIED":
        return False, "TAXONOMY_UNRATIFIED"
    effective = parse_date(
        taxonomy["effective_from"], "TAXONOMY_INVALID", "effective_from"
    )
    if effective > start:
        return False, "TAXONOMY_NOT_EFFECTIVE_FOR_FULL_WINDOW"
    return True, None


def daily_members(
    point: dict,
    day: dt.date,
    taxonomy: dict,
    taxonomy_enabled: bool,
) -> list:
    members = []
    for item in point["universe"]["members"]:
        asset_id = item["canonical_asset_id"]
        previous = decimal_text(item["previous_close"], f"{asset_id} previous")
        latest = decimal_text(item["latest_close"], f"{asset_id} latest")
        groups = taxonomy_for(asset_id, day, taxonomy) if taxonomy_enabled else None
        members.append(
            {
                "canonical_asset_id": asset_id,
                "source_asset_id": item["source_asset_id"],
                "pair_id": item["pair_id"],
                "previous_close": previous,
                "latest_close": latest,
                "daily_gross_return": latest / previous,
                "bucket": deterministic_bucket(asset_id),
                "sectors": [] if groups is None else groups["sectors"],
                "chains": [] if groups is None else groups["chains"],
                "sector_chain_taxonomy_status": (
                    "UNKNOWN" if groups is None else "OBSERVED_UNCLASSIFIED"
                ),
            }
        )
    return members


def daily_bucket_observations(members: list) -> list:
    observations = []
    for group_id in BUCKETS:
        selected = [item for item in members if item["bucket"] == group_id]
        if not selected:
            observations.append(
                {
                    "group_id": group_id,
                    "status": "UNKNOWN",
                    "unknown_reason": "BUCKET_EMPTY",
                    "member_count": 0,
                    "members": [],
                    "daily_gross_return": None,
                }
            )
            continue
        gross = sum(
            (item["daily_gross_return"] for item in selected), Decimal(0)
        ) / Decimal(len(selected))
        observations.append(
            {
                "group_id": group_id,
                "status": "OBSERVED_UNCLASSIFIED",
                "unknown_reason": None,
                "member_count": len(selected),
                "members": sorted(item["canonical_asset_id"] for item in selected),
                "daily_gross_return": gross,
            }
        )
    return observations


def check_cross_snapshot_continuity(points: list) -> None:
    for before, after in zip(points, points[1:]):
        previous = {
            item["canonical_asset_id"]: item for item in before["members"]
        }
        current = {
            item["canonical_asset_id"]: item for item in after["members"]
        }
        for asset_id in sorted(set(previous) & set(current)):
            if (
                previous[asset_id]["latest_close"]
                != current[asset_id]["previous_close"]
            ):
                fail(
                    "CROSS_SNAPSHOT_CLOSE_MISMATCH",
                    f"{asset_id}@{after['as_of_date']}",
                )


def cumulative(values: list) -> Decimal:
    result = Decimal(1)
    for value in values:
        result *= value
    return result


def render(value: Decimal, contract: dict) -> str:
    return BREADTH.render_decimal(value, contract["output_decimal_places"])


def rendered_member(item: dict, contract: dict) -> dict:
    return {
        "canonical_asset_id": item["canonical_asset_id"],
        "source_asset_id": item["source_asset_id"],
        "pair_id": item["pair_id"],
        "previous_close": render(item["previous_close"], contract),
        "latest_close": render(item["latest_close"], contract),
        "daily_gross_return": render(item["daily_gross_return"], contract),
        "bucket": item["bucket"],
        "sectors": item["sectors"],
        "chains": item["chains"],
        "sector_chain_taxonomy_status": item[
            "sector_chain_taxonomy_status"
        ],
    }


def rendered_buckets(groups: list, contract: dict) -> list:
    return [
        item
        | {
            "daily_gross_return": (
                None
                if item["daily_gross_return"] is None
                else render(item["daily_gross_return"], contract)
            )
        }
        for item in groups
    ]


def authority_boundary() -> dict:
    return {
        "leader_classification_authorized": False,
        "ranking_authorized": False,
        "threshold_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def window_descriptor(
    spec: dict,
    start: dt.date,
    end: dt.date,
    available: int,
    missing: list,
) -> dict:
    return {
        "window_id": spec["window_id"],
        "role": spec["role"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "lookback_calendar_days": spec["lookback_calendar_days"],
        "required_point_count": spec["lookback_calendar_days"],
        "available_point_count": available,
        "missing_dates": missing,
        "exact_contiguous_calendar_days": not missing,
    }


def unknown_window(
    descriptor: dict,
    reason: str,
    blockers: list,
    source_unknown_points: Optional[list] = None,
) -> dict:
    return {
        "window_id": descriptor["window_id"],
        "role": descriptor["role"],
        "status": "UNKNOWN",
        "unknown_reason": reason,
        "window": descriptor,
        "blockers": blockers,
        "source_unknown_points": source_unknown_points or [],
        "asset_relative_strength": [],
        "partial_window_assets": [],
        "group_relative_strength": {
            "bucket": [],
            "sector_chain": {
                "status": "UNKNOWN",
                "unknown_reason": "WINDOW_UNKNOWN",
                "sector": [],
                "chain": [],
            },
        },
        "daily_points": [],
        "lineage": {
            "pit_status": "window_not_observed",
            "manifest_sha256_by_date": [],
            "current_catalog_backfill_authorized": False,
        },
    }


def build_observed_window(
    spec: dict,
    descriptor: dict,
    selected: list,
    contract: dict,
    leadership: dict,
    taxonomy: dict,
    universe_policy_path: Path,
    exclusion_taxonomy_path: Path,
    identity_exceptions_path: Path,
) -> dict:
    window_start = parse_date(
        descriptor["start_date"], "WINDOW_DATE_INVALID", "start"
    )
    taxonomy_enabled, taxonomy_reason = taxonomy_window_state(
        taxonomy, window_start
    )
    raw_points = []
    source_unknown_points = []
    for day, path in selected:
        source_point = BREADTH.build_transform(
            path,
            universe_policy_path=universe_policy_path,
            exclusion_taxonomy_path=exclusion_taxonomy_path,
            identity_exceptions_path=identity_exceptions_path,
        )
        if source_point["as_of_date"] != day.isoformat():
            fail("SOURCE_POINT_DATE_MISMATCH", str(path))
        if source_point["status"] == "UNKNOWN":
            source_unknown_points.append(
                {
                    "as_of_date": day.isoformat(),
                    "unknown_reason": source_point["unknown_reason"],
                    "manifest_sha256": source_point["lineage"][
                        "manifest_sha256"
                    ],
                }
            )
            continue
        members = daily_members(
            source_point, day, taxonomy, taxonomy_enabled
        )
        raw_points.append(
            {
                "as_of_date": day.isoformat(),
                "members": members,
                "bucket_groups": daily_bucket_observations(members),
                "lineage": source_point["lineage"],
            }
        )
    if source_unknown_points:
        return unknown_window(
            descriptor,
            "SOURCE_POINT_UNKNOWN",
            [
                {
                    "code": "SOURCE_POINT_UNKNOWN",
                    "dates": [
                        item["as_of_date"] for item in source_unknown_points
                    ],
                }
            ],
            source_unknown_points,
        )

    check_cross_snapshot_continuity(raw_points)
    lookback = spec["lookback_calendar_days"]
    all_ids = sorted(
        {
            item["canonical_asset_id"]
            for point in raw_points
            for item in point["members"]
        }
    )
    counts = {
        asset_id: sum(
            any(
                item["canonical_asset_id"] == asset_id
                for item in point["members"]
            )
            for point in raw_points
        )
        for asset_id in all_ids
    }
    complete_ids = [
        asset_id for asset_id in all_ids if counts[asset_id] == lookback
    ]
    if "BTC" not in complete_ids:
        return unknown_window(
            descriptor,
            "BTC_REFERENCE_INCOMPLETE",
            [
                {
                    "code": "BTC_REFERENCE_INCOMPLETE",
                    "observed_day_count": counts.get("BTC", 0),
                    "required_day_count": lookback,
                }
            ],
        )

    asset_cumulative = {}
    for asset_id in complete_ids:
        values = []
        for point in raw_points:
            item = next(
                member
                for member in point["members"]
                if member["canonical_asset_id"] == asset_id
            )
            values.append(item["daily_gross_return"])
        asset_cumulative[asset_id] = cumulative(values)
    btc_return = asset_cumulative["BTC"]
    assets = [
        {
            "canonical_asset_id": asset_id,
            "observed_day_count": lookback,
            "cumulative_gross_return": render(
                asset_cumulative[asset_id], contract
            ),
            "relative_strength_vs_btc": render(
                asset_cumulative[asset_id] / btc_return - Decimal(1),
                contract,
            ),
            "classification": "UNDEFINED",
        }
        for asset_id in complete_ids
    ]

    bucket_results = []
    for group_id in BUCKETS:
        daily = [
            next(
                item
                for item in point["bucket_groups"]
                if item["group_id"] == group_id
            )
            for point in raw_points
        ]
        missing_dates = [
            point["as_of_date"]
            for point, group in zip(raw_points, daily)
            if group["status"] == "UNKNOWN"
        ]
        if missing_dates:
            bucket_results.append(
                {
                    "group_id": group_id,
                    "status": "UNKNOWN",
                    "unknown_reason": "BUCKET_EMPTY_ON_REQUIRED_DATE",
                    "missing_dates": missing_dates,
                    "observed_day_count": lookback - len(missing_dates),
                    "required_day_count": lookback,
                    "minimum_daily_member_count": 0,
                    "required_minimum_member_count": None,
                    "cumulative_gross_return": None,
                    "relative_strength_vs_btc": None,
                    "classification": "UNDEFINED",
                }
            )
            continue
        gross = cumulative([item["daily_gross_return"] for item in daily])
        bucket_results.append(
            {
                "group_id": group_id,
                "status": "OBSERVED_UNCLASSIFIED",
                "unknown_reason": None,
                "missing_dates": [],
                "observed_day_count": lookback,
                "required_day_count": lookback,
                "minimum_daily_member_count": min(
                    item["member_count"] for item in daily
                ),
                "required_minimum_member_count": None,
                "cumulative_gross_return": render(gross, contract),
                "relative_strength_vs_btc": render(
                    gross / btc_return - Decimal(1), contract
                ),
                "classification": "UNDEFINED",
            }
        )

    taxonomy_missing = sorted(
        {
            f"{item['canonical_asset_id']}@{point['as_of_date']}"
            for point in raw_points
            for item in point["members"]
            if item["sector_chain_taxonomy_status"] == "UNKNOWN"
        }
    )
    if taxonomy_reason is None and taxonomy_missing:
        taxonomy_reason = "TAXONOMY_COVERAGE_UNKNOWN"
    if taxonomy_reason is None:
        taxonomy_reason = "GROUP_COVERAGE_POLICY_UNRATIFIED"
    sector_chain = {
        "status": "UNKNOWN",
        "unknown_reason": taxonomy_reason,
        "missing_asset_dates": taxonomy_missing,
        "group_coverage_policy_status": leadership[
            "group_coverage_policy_status"
        ],
        "sector": [],
        "chain": [],
    }

    points = [
        {
            "as_of_date": point["as_of_date"],
            "members": [
                rendered_member(item, contract) for item in point["members"]
            ],
            "groups": {
                "bucket": rendered_buckets(
                    point["bucket_groups"], contract
                ),
                "sector": [],
                "chain": [],
            },
            "sector_chain_group_layer_status": "UNKNOWN",
            "lineage": point["lineage"],
        }
        for point in raw_points
    ]
    partial = [
        {
            "canonical_asset_id": asset_id,
            "observed_day_count": counts[asset_id],
            "required_day_count": lookback,
            "reason": "not_present_in_every_as_captured_daily_universe",
        }
        for asset_id in all_ids
        if counts[asset_id] != lookback
    ]
    return {
        "window_id": spec["window_id"],
        "role": spec["role"],
        "status": "OBSERVED_UNCLASSIFIED",
        "unknown_reason": None,
        "window": descriptor,
        "blockers": [],
        "source_unknown_points": [],
        "asset_relative_strength": assets,
        "partial_window_assets": partial,
        "group_relative_strength": {
            "bucket": bucket_results,
            "sector_chain": sector_chain,
        },
        "daily_points": points,
        "lineage": {
            "pit_status": "independent_as_captured_daily_snapshots",
            "manifest_sha256_by_date": [
                {
                    "as_of_date": point["as_of_date"],
                    "manifest_sha256": point["lineage"]["manifest_sha256"],
                }
                for point in points
            ],
            "current_catalog_backfill_authorized": False,
        },
    }


def build_transform(
    snapshot_root: Path,
    contract_path: Path = CONTRACT_PATH,
    universe_policy_path: Path = UNIVERSE_POLICY_PATH,
    exclusion_taxonomy_path: Path = BREADTH.EXCLUSION_TAXONOMY_PATH,
    leadership_policy_path: Path = LEADERSHIP_POLICY_PATH,
    taxonomy_path: Path = TAXONOMY_PATH,
    identity_exceptions_path: Path = IDENTITY_EXCEPTIONS_PATH,
    end_date: Optional[str] = None,
) -> dict:
    contract = load_contract(contract_path)
    leadership = load_leadership_policy(leadership_policy_path)
    require_ratified_leadership_policy(leadership)
    taxonomy = load_taxonomy(taxonomy_path)
    universe = BREADTH.load_universe_policy(universe_policy_path)
    if universe["approval_status"] != "RATIFIED":
        fail("SOURCE_UNIVERSE_POLICY_UNRATIFIED", universe["policy_version"])
    snapshots = discover_snapshot_map(snapshot_root)
    end = optional_date(end_date, "end_date")
    if end is None:
        if not snapshots:
            fail("END_DATE_REQUIRED_WITHOUT_SNAPSHOTS", str(snapshot_root))
        end = max(snapshots)
    leadership_effective = parse_date(
        leadership["effective_from"],
        "LEADERSHIP_POLICY_INVALID",
        "effective_from",
    )
    universe_effective = parse_date(
        universe["effective_from"],
        "SOURCE_UNIVERSE_POLICY_INVALID",
        "effective_from",
    )

    windows = []
    for spec in leadership["windows"]:
        lookback = spec["lookback_calendar_days"]
        start = end - dt.timedelta(days=lookback - 1)
        days = [start + dt.timedelta(days=index) for index in range(lookback)]
        missing = [day.isoformat() for day in days if day not in snapshots]
        descriptor = window_descriptor(
            spec, start, end, lookback - len(missing), missing
        )
        blockers = []
        if missing:
            blockers.append(
                {
                    "code": "INSUFFICIENT_CONTIGUOUS_HISTORY",
                    "missing_dates": missing,
                }
            )
        if start < leadership_effective:
            blockers.append(
                {
                    "code": "LEADERSHIP_POLICY_NOT_EFFECTIVE_FOR_FULL_WINDOW",
                    "effective_from": leadership_effective.isoformat(),
                }
            )
        if start < universe_effective:
            blockers.append(
                {
                    "code": "SOURCE_UNIVERSE_POLICY_NOT_EFFECTIVE_FOR_FULL_WINDOW",
                    "effective_from": universe_effective.isoformat(),
                }
            )
        if blockers:
            windows.append(
                unknown_window(
                    descriptor, blockers[0]["code"], blockers
                )
            )
            continue
        selected = [(day, snapshots[day]) for day in days]
        windows.append(
            build_observed_window(
                spec,
                descriptor,
                selected,
                contract,
                leadership,
                taxonomy,
                universe_policy_path,
                exclusion_taxonomy_path,
                identity_exceptions_path,
            )
        )

    observed_count = sum(
        item["status"] == "OBSERVED_UNCLASSIFIED" for item in windows
    )
    status = (
        "UNKNOWN"
        if observed_count == 0
        else "OBSERVED_UNCLASSIFIED"
        if observed_count == len(windows)
        else "PARTIAL"
    )
    manifest_by_date = {}
    for window in windows:
        for item in window["lineage"]["manifest_sha256_by_date"]:
            manifest_by_date[item["as_of_date"]] = item["manifest_sha256"]
    return {
        "schema_version": 2,
        "contract_version": contract["contract_version"],
        "market": "CRYPTO",
        "measurement": contract["measurement"],
        "status": status,
        "unknown_reason": "NO_WINDOW_OBSERVED" if status == "UNKNOWN" else None,
        "as_of_date": end.isoformat(),
        "windows": windows,
        "policies": {
            "universe": {
                "policy_version": universe["policy_version"],
                "policy_sha256": file_sha256(universe_policy_path),
                "approval_status": universe["approval_status"],
                "universe_kind": universe["universe_kind"],
            },
            "leadership": {
                "policy_version": leadership["policy_version"],
                "policy_sha256": file_sha256(leadership_policy_path),
                "approval_status": leadership["approval_status"],
                "group_return_method": leadership["group_return_method"],
                "group_coverage_policy_status": leadership[
                    "group_coverage_policy_status"
                ],
            },
            "taxonomy": {
                "policy_version": taxonomy["policy_version"],
                "policy_sha256": file_sha256(taxonomy_path),
                "approval_status": taxonomy["approval_status"],
                "effective_dated": True,
            },
        },
        "current_candle": {
            "excluded_for_every_member_and_point": True,
            "reason": "source_documents_not_yet_committed_timeframe",
        },
        "lineage": {
            "pit_status": "independent_as_captured_daily_snapshots",
            "source_transform_version": contract[
                "source_transform_version"
            ],
            "manifest_sha256_by_date": [
                {
                    "as_of_date": day,
                    "manifest_sha256": manifest_by_date[day],
                }
                for day in sorted(manifest_by_date)
            ],
            "current_catalog_backfill_authorized": False,
        },
    } | authority_boundary()


def write_output(payload: dict, target: Path) -> Path:
    return BREADTH.write_output(payload, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    transform = parser.add_subparsers(dest="command", required=True).add_parser(
        "transform"
    )
    transform.add_argument("snapshot_root", type=Path)
    transform.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    transform.add_argument(
        "--universe-policy", type=Path, default=UNIVERSE_POLICY_PATH
    )
    transform.add_argument(
        "--exclusion-taxonomy",
        type=Path,
        default=BREADTH.EXCLUSION_TAXONOMY_PATH,
    )
    transform.add_argument(
        "--leadership-policy", type=Path, default=LEADERSHIP_POLICY_PATH
    )
    transform.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    transform.add_argument(
        "--identity-exceptions", type=Path, default=IDENTITY_EXCEPTIONS_PATH
    )
    transform.add_argument("--end-date")
    transform.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_transform(
            args.snapshot_root,
            contract_path=args.contract,
            universe_policy_path=args.universe_policy,
            exclusion_taxonomy_path=args.exclusion_taxonomy,
            leadership_policy_path=args.leadership_policy,
            taxonomy_path=args.taxonomy,
            identity_exceptions_path=args.identity_exceptions,
            end_date=args.end_date,
        )
        if args.out is None:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(write_output(result, args.out))
        return 0
    except (LeadershipError, BREADTH.BreadthError) as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
