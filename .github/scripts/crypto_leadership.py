#!/usr/bin/env python3
"""Build raw Crypto relative-strength observations from PIT snapshots.

The helper is offline and fail-closed.  It reuses the validated P1-CR-06
daily snapshots and will not calculate anything until the universe,
leadership calculation, and effective-dated taxonomy policies are all
explicitly RATIFIED.  Its output is evidence only: it does not classify a
leader, rank assets, score a Regime, publish a Production factor, or trade.
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
LEADERSHIP_POLICY_PATH = (
    ROOT / "config" / "crypto_leadership_policy.json"
)
TAXONOMY_PATH = ROOT / "config" / "crypto_asset_taxonomy.json"
UNIVERSE_POLICY_PATH = (
    ROOT / "config" / "crypto_breadth_universe_policy.json"
)
IDENTITY_EXCEPTIONS_PATH = (
    ROOT / "config" / "crypto_asset_identity_exceptions.json"
)
BUCKETS = ("ALT", "BTC", "ETH")
GROUP_TYPES = ("bucket", "sector", "chain")


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
    expected = {
        "schema_version",
        "contract_version",
        "source_helper",
        "source_transform_version",
        "market_timezone",
        "measurement",
        "window_policy",
        "daily_return_semantics",
        "relative_strength_semantics",
        "cross_snapshot_close_policy",
        "current_candle_policy",
        "taxonomy_policy",
        "supported_group_return_methods",
        "output_decimal_places",
        "rounding",
    }
    pinned = {
        "schema_version": 1,
        "contract_version": "crypto_leadership_contract/v1",
        "source_helper": ".github/scripts/crypto_breadth.py",
        "source_transform_version": "crypto_breadth_observation/v1",
        "market_timezone": "UTC",
        "measurement": "raw_relative_strength_observation",
        "window_policy": "exact_contiguous_calendar_days",
        "daily_return_semantics": "latest_finalized_close_div_previous_finalized_close",
        "relative_strength_semantics": "cumulative_gross_return_div_btc_cumulative_gross_return_minus_one",
        "cross_snapshot_close_policy": "same_asset_adjacent_close_must_match",
        "current_candle_policy": "exclude_last_row_always",
        "taxonomy_policy": "explicit_effective_dated_no_overlap",
        "supported_group_return_methods": [
            "equal_weight_daily_rebalanced"
        ],
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
    }
    if set(contract) != expected or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def validate_minimum_map(value: object, code: str) -> Optional[dict]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(BUCKETS):
        fail(code, "required_bucket_minimum_members")
    if any(type(item) is not int or item < 1 for item in value.values()):
        fail(code, "required_bucket_minimum_members")
    return value


def load_leadership_policy(path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    policy = read_json(path, "LEADERSHIP_POLICY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "effective_from",
        "lookback_calendar_days",
        "group_return_method",
        "relative_strength_reference",
        "required_bucket_minimum_members",
        "required_sectors",
        "required_chains",
        "taxonomy_group_minimum_members",
    }
    if set(policy) != expected:
        fail("LEADERSHIP_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
        or policy.get("relative_strength_reference") != "BTC"
    ):
        fail("LEADERSHIP_POLICY_INVALID", "header")
    effective = policy.get("effective_from")
    if effective is not None:
        parse_date(
            effective, "LEADERSHIP_POLICY_INVALID", "effective_from"
        )
    lookback = policy.get("lookback_calendar_days")
    if lookback is not None and (type(lookback) is not int or lookback < 1):
        fail("LEADERSHIP_POLICY_INVALID", "lookback_calendar_days")
    method = policy.get("group_return_method")
    if method is not None and method != "equal_weight_daily_rebalanced":
        fail("LEADERSHIP_POLICY_INVALID", "group_return_method")
    validate_minimum_map(
        policy.get("required_bucket_minimum_members"),
        "LEADERSHIP_POLICY_INVALID",
    )
    for key in ("required_sectors", "required_chains"):
        value = policy.get(key)
        if value is not None:
            sorted_strings(value, "LEADERSHIP_POLICY_INVALID", key)
    minimum = policy.get("taxonomy_group_minimum_members")
    if minimum is not None and (type(minimum) is not int or minimum < 1):
        fail(
            "LEADERSHIP_POLICY_INVALID",
            "taxonomy_group_minimum_members",
        )
    return policy


def require_ratified_leadership_policy(
    policy: dict, start: dt.date, end: dt.date
) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("LEADERSHIP_POLICY_UNRATIFIED", policy["policy_version"])
    required = (
        policy["effective_from"],
        policy["lookback_calendar_days"],
        policy["group_return_method"],
        policy["required_bucket_minimum_members"],
        policy["required_sectors"],
        policy["required_chains"],
        policy["taxonomy_group_minimum_members"],
    )
    if any(value is None for value in required):
        fail("LEADERSHIP_POLICY_INVALID", "ratified fields incomplete")
    if not policy["required_sectors"] or not policy["required_chains"]:
        fail("LEADERSHIP_POLICY_INVALID", "required groups empty")
    effective = parse_date(
        policy["effective_from"],
        "LEADERSHIP_POLICY_INVALID",
        "effective_from",
    )
    if effective > start:
        fail("LEADERSHIP_POLICY_NOT_EFFECTIVE", start.isoformat())
    expected_start = end - dt.timedelta(
        days=policy["lookback_calendar_days"] - 1
    )
    if expected_start != start:
        fail("WINDOW_POLICY_MISMATCH", f"{start}..{end}")


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
        if (
            not isinstance(asset_id, str)
            or BREADTH.ASSET_ID.fullmatch(asset_id) is None
            or record["bucket"] not in BUCKETS
        ):
            fail("TAXONOMY_INVALID", f"record {index} identity")
        if asset_id == "BTC" and record["bucket"] != "BTC":
            fail("TAXONOMY_INVALID", "BTC bucket")
        if asset_id == "ETH" and record["bucket"] != "ETH":
            fail("TAXONOMY_INVALID", "ETH bucket")
        if asset_id not in {"BTC", "ETH"} and record["bucket"] != "ALT":
            fail("TAXONOMY_INVALID", f"{asset_id} bucket")
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
    return policy | {"_records": normalized}


def require_ratified_taxonomy(
    policy: dict, start: dt.date
) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("TAXONOMY_UNRATIFIED", policy["policy_version"])
    if policy["effective_from"] is None or not policy["records"]:
        fail("TAXONOMY_INVALID", "ratified fields incomplete")
    effective = parse_date(
        policy["effective_from"], "TAXONOMY_INVALID", "effective_from"
    )
    if effective > start:
        fail("TAXONOMY_NOT_EFFECTIVE", start.isoformat())


def taxonomy_for(asset_id: str, day: dt.date, policy: dict) -> dict:
    matches = []
    for record in policy["_records"]:
        end = record["_end"] or dt.date.max
        if (
            record["canonical_asset_id"] == asset_id
            and record["_start"] <= day <= end
        ):
            matches.append(record)
    if len(matches) != 1:
        code = "TAXONOMY_RANGE_OVERLAP" if len(matches) > 1 else "TAXONOMY_MISSING"
        fail(code, f"{asset_id}@{day.isoformat()}")
    record = matches[0]
    return {
        "bucket": record["bucket"],
        "sectors": record["sectors"],
        "chains": record["chains"],
    }


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
    if not snapshots:
        fail("SNAPSHOT_RANGE_EMPTY", str(root))
    return snapshots


def selected_window(
    snapshot_root: Path, lookback: int, end_date: Optional[str]
) -> tuple:
    snapshots = discover_snapshot_map(snapshot_root)
    end = optional_date(end_date, "end_date") or max(snapshots)
    start = end - dt.timedelta(days=lookback - 1)
    days = [start + dt.timedelta(days=index) for index in range(lookback)]
    missing = [day.isoformat() for day in days if day not in snapshots]
    if missing:
        fail("WINDOW_NOT_CONTIGUOUS", ",".join(missing))
    return start, end, [(day, snapshots[day]) for day in days]


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


def daily_members(point: dict, day: dt.date, taxonomy: dict) -> list:
    members = []
    for item in point["universe"]["members"]:
        asset_id = item["canonical_asset_id"]
        previous = decimal_text(item["previous_close"], f"{asset_id} previous")
        latest = decimal_text(item["latest_close"], f"{asset_id} latest")
        groups = taxonomy_for(asset_id, day, taxonomy)
        members.append(
            {
                "canonical_asset_id": asset_id,
                "source_asset_id": item["source_asset_id"],
                "pair_id": item["pair_id"],
                "previous_close": previous,
                "latest_close": latest,
                "daily_gross_return": latest / previous,
                "bucket": groups["bucket"],
                "sectors": groups["sectors"],
                "chains": groups["chains"],
            }
        )
    return members


def member_ids_for_group(members: list, group_type: str, group_id: str) -> list:
    if group_type == "bucket":
        return [item for item in members if item["bucket"] == group_id]
    key = "sectors" if group_type == "sector" else "chains"
    return [item for item in members if group_id in item[key]]


def group_specs(policy: dict) -> list:
    specs = []
    for group_id in BUCKETS:
        specs.append(
            (
                "bucket",
                group_id,
                policy["required_bucket_minimum_members"][group_id],
            )
        )
    minimum = policy["taxonomy_group_minimum_members"]
    specs.extend(
        ("sector", group_id, minimum)
        for group_id in policy["required_sectors"]
    )
    specs.extend(
        ("chain", group_id, minimum)
        for group_id in policy["required_chains"]
    )
    return specs


def daily_group_observations(members: list, policy: dict) -> dict:
    observations = {key: [] for key in GROUP_TYPES}
    for group_type, group_id, minimum in group_specs(policy):
        selected = member_ids_for_group(members, group_type, group_id)
        if len(selected) < minimum:
            fail(
                "GROUP_COVERAGE_INCOMPLETE",
                f"{group_type}:{group_id}={len(selected)}<{minimum}",
            )
        gross = sum(
            (item["daily_gross_return"] for item in selected), Decimal(0)
        ) / Decimal(len(selected))
        observations[group_type].append(
            {
                "group_id": group_id,
                "member_count": len(selected),
                "members": sorted(
                    item["canonical_asset_id"] for item in selected
                ),
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
            if previous[asset_id]["latest_close"] != current[asset_id]["previous_close"]:
                fail(
                    "CROSS_SNAPSHOT_CLOSE_MISMATCH",
                    f"{asset_id}@{after['as_of_date']}",
                )


def cumulative(value_lists: list) -> Decimal:
    result = Decimal(1)
    for value in value_lists:
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
    }


def rendered_groups(groups: dict, contract: dict) -> dict:
    return {
        group_type: [
            {
                "group_id": item["group_id"],
                "member_count": item["member_count"],
                "members": item["members"],
                "daily_gross_return": render(
                    item["daily_gross_return"], contract
                ),
            }
            for item in groups[group_type]
        ]
        for group_type in GROUP_TYPES
    }


def authority_boundary() -> dict:
    return {
        "leader_classification_authorized": False,
        "ranking_authorized": False,
        "threshold_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def build_transform(
    snapshot_root: Path,
    contract_path: Path = CONTRACT_PATH,
    universe_policy_path: Path = UNIVERSE_POLICY_PATH,
    leadership_policy_path: Path = LEADERSHIP_POLICY_PATH,
    taxonomy_path: Path = TAXONOMY_PATH,
    identity_exceptions_path: Path = IDENTITY_EXCEPTIONS_PATH,
    end_date: Optional[str] = None,
) -> dict:
    contract = load_contract(contract_path)
    leadership = load_leadership_policy(leadership_policy_path)
    if leadership["approval_status"] != "RATIFIED":
        fail("LEADERSHIP_POLICY_UNRATIFIED", leadership["policy_version"])
    taxonomy = load_taxonomy(taxonomy_path)
    if taxonomy["approval_status"] != "RATIFIED":
        fail("TAXONOMY_UNRATIFIED", taxonomy["policy_version"])
    lookback = leadership["lookback_calendar_days"]
    if lookback is None:
        fail("LEADERSHIP_POLICY_INVALID", "lookback_calendar_days")
    start, end, selected = selected_window(snapshot_root, lookback, end_date)
    require_ratified_leadership_policy(leadership, start, end)
    require_ratified_taxonomy(taxonomy, start)

    universe = BREADTH.load_universe_policy(universe_policy_path)
    raw_points = []
    for day, path in selected:
        BREADTH.require_ratified_policy(universe, day)
        source_point = BREADTH.build_transform(
            path,
            universe_policy_path=universe_policy_path,
            identity_exceptions_path=identity_exceptions_path,
        )
        if source_point["as_of_date"] != day.isoformat():
            fail("SOURCE_POINT_DATE_MISMATCH", str(path))
        members = daily_members(source_point, day, taxonomy)
        raw_points.append(
            {
                "as_of_date": day.isoformat(),
                "members": members,
                "groups": daily_group_observations(members, leadership),
                "lineage": source_point["lineage"],
            }
        )
    check_cross_snapshot_continuity(raw_points)

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
    complete_ids = [asset_id for asset_id in all_ids if counts[asset_id] == lookback]
    if "BTC" not in complete_ids:
        fail("BTC_REFERENCE_INCOMPLETE", f"{counts.get('BTC', 0)}/{lookback}")

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

    group_results = {key: [] for key in GROUP_TYPES}
    for group_type, group_id, minimum in group_specs(leadership):
        values = []
        member_counts = []
        for point in raw_points:
            match = next(
                item
                for item in point["groups"][group_type]
                if item["group_id"] == group_id
            )
            values.append(match["daily_gross_return"])
            member_counts.append(match["member_count"])
        gross = cumulative(values)
        group_results[group_type].append(
            {
                "group_id": group_id,
                "observed_day_count": lookback,
                "minimum_daily_member_count": min(member_counts),
                "required_minimum_member_count": minimum,
                "cumulative_gross_return": render(gross, contract),
                "relative_strength_vs_btc": render(
                    gross / btc_return - Decimal(1), contract
                ),
                "classification": "UNDEFINED",
            }
        )

    points = [
        {
            "as_of_date": point["as_of_date"],
            "members": [
                rendered_member(item, contract) for item in point["members"]
            ],
            "groups": rendered_groups(point["groups"], contract),
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
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "market": "CRYPTO",
        "measurement": contract["measurement"],
        "status": "OBSERVED_UNCLASSIFIED",
        "window": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "lookback_calendar_days": lookback,
            "point_count": len(points),
            "exact_contiguous_calendar_days": True,
        },
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
            },
            "taxonomy": {
                "policy_version": taxonomy["policy_version"],
                "policy_sha256": file_sha256(taxonomy_path),
                "approval_status": taxonomy["approval_status"],
                "effective_dated": True,
            },
        },
        "asset_relative_strength": assets,
        "partial_window_assets": partial,
        "group_relative_strength": group_results,
        "daily_points": points,
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
                    "as_of_date": point["as_of_date"],
                    "manifest_sha256": point["lineage"]["manifest_sha256"],
                }
                for point in points
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
