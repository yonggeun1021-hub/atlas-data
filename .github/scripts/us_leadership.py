#!/usr/bin/env python3
"""Build transient-only US cross-sectional leadership observations.

The helper consumes synthetic or transient stdin price panels.  It makes no
network request and never emits vendor price rows.  Calculation requires
separately ratified leadership, point-in-time universe, and effective-dated
taxonomy policies.  Outputs are unclassified observations only: no ranking,
Trend direction, Regime score, Production factor, or trading action.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "us_leadership_contract.json"
LEADERSHIP_POLICY_PATH = ROOT / "config" / "us_leadership_policy.json"
UNIVERSE_POLICY_PATH = (
    ROOT / "config" / "us_leadership_universe_policy.json"
)
TAXONOMY_PATH = ROOT / "config" / "us_asset_taxonomy.json"
TEMPORAL_SCRIPT = ROOT / "atlas_price_pit_contract.py"
ASSET = re.compile(r"^[A-Z0-9._-]{1,20}$")
GROUP = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,39}$")


class USLeadershipError(RuntimeError):
    """Fail-closed US Leadership contract or input violation."""


def fail(code: str, detail: str) -> None:
    raise USLeadershipError(f"{code}: {detail}")


def load_temporal_module(path: Path = TEMPORAL_SCRIPT):
    spec = importlib.util.spec_from_file_location(
        "atlas_us_leadership_temporal", path
    )
    if spec is None or spec.loader is None:
        fail("TEMPORAL_CONTRACT_INVALID", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEMPORAL = load_temporal_module()


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


def parse_payload(raw: bytes) -> dict:
    try:
        value = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, InvalidOperation) as exc:
        fail("INPUT_INVALID", str(exc))
    if not isinstance(value, dict):
        fail("INPUT_INVALID", "root must be object")
    return value


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("FILE_HASH_INVALID", str(exc))


def canonical_payload_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_date(value: object, code: str, label: str) -> dt.date:
    if not isinstance(value, str):
        fail(code, label)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(code, f"{label}={value}")


def sorted_strings(value: object, code: str, label: str) -> list:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        fail(code, label)
    return value


def ensure_no_float(value: object, label: str = "input") -> None:
    if isinstance(value, (float, Decimal)):
        fail("INPUT_NUMBER_MUST_BE_STRING", label)
    if isinstance(value, dict):
        for key, item in value.items():
            ensure_no_float(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_no_float(item, f"{label}[{index}]")


def decimal_string(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        fail("INPUT_ROW_INVALID", label)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        fail("INPUT_ROW_INVALID", label)
    if not parsed.is_finite() or parsed <= 0:
        fail("INPUT_ROW_INVALID", label)
    return parsed


def render_decimal(value: Decimal, places: int) -> str:
    if not value.is_finite():
        fail("OUTPUT_NUMBER_INVALID", "non-finite")
    quantum = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        fail("OUTPUT_NUMBER_INVALID", str(exc))
    if rounded == 0:
        rounded = Decimal(0)
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_INVALID")
    expected = {
        "schema_version",
        "contract_version",
        "source_temporal_contract",
        "transform_version",
        "measurement",
        "market_timezone",
        "return_semantics",
        "relative_strength_semantics",
        "participation_semantics",
        "group_return_semantics",
        "session_coverage_policy",
        "membership_policy",
        "taxonomy_policy",
        "input_retention_policy",
        "output_retention_policy",
        "output_decimal_places",
        "rounding",
    }
    pinned = {
        "schema_version": 1,
        "contract_version": "us_leadership_contract/v1",
        "source_temporal_contract": "atlas_price_pit_contract.py/v0.1",
        "transform_version": "us_leadership/v1",
        "measurement": "us_cross_sectional_leadership_observation",
        "market_timezone": "America/New_York",
        "return_semantics": "simple_close_to_close",
        "relative_strength_semantics": (
            "cumulative_gross_return_div_benchmark_"
            "cumulative_gross_return_minus_one"
        ),
        "participation_semantics": (
            "eligible_non_benchmark_assets_outperforming_"
            "benchmark_daily_return_fraction"
        ),
        "group_return_semantics": "equal_weight_daily_rebalanced",
        "session_coverage_policy": "exact_expected_session_dates_no_fill",
        "membership_policy": "explicit_effective_dated_no_overlap",
        "taxonomy_policy": "explicit_effective_dated_no_overlap",
        "input_retention_policy": "transient_memory_or_stdin_only",
        "output_retention_policy": (
            "non_reconstructive_derived_observations_only"
        ),
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
    }
    if set(contract) != expected or any(
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
        "source_name",
        "quote_currency",
        "market_timezone",
        "price_basis",
        "allowed_run_modes",
        "session_calendar_source",
        "benchmark_asset",
        "lookback_sessions",
        "minimum_assets",
        "required_groups",
        "group_minimum_members",
        "group_return_method",
        "split_window_policy",
    }
    if set(policy) != expected:
        fail("LEADERSHIP_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
    ):
        fail("LEADERSHIP_POLICY_INVALID", "header")
    if policy["effective_from"] is not None:
        parse_date(
            policy["effective_from"],
            "LEADERSHIP_POLICY_INVALID",
            "effective_from",
        )
    for key in ("source_name", "session_calendar_source"):
        value = policy[key]
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            fail("LEADERSHIP_POLICY_INVALID", key)
    if policy["quote_currency"] is not None and policy["quote_currency"] != "USD":
        fail("LEADERSHIP_POLICY_INVALID", "quote_currency")
    if (
        policy["market_timezone"] is not None
        and policy["market_timezone"] != TEMPORAL.MARKET_TZ
    ):
        fail("LEADERSHIP_POLICY_INVALID", "market_timezone")
    if (
        policy["price_basis"] is not None
        and policy["price_basis"] not in TEMPORAL.PRICE_BASES
    ):
        fail("LEADERSHIP_POLICY_INVALID", "price_basis")
    if policy["allowed_run_modes"] is not None:
        sorted_strings(
            policy["allowed_run_modes"],
            "LEADERSHIP_POLICY_INVALID",
            "allowed_run_modes",
        )
        if any(
            item not in TEMPORAL.RUN_MODES
            for item in policy["allowed_run_modes"]
        ):
            fail("LEADERSHIP_POLICY_INVALID", "allowed_run_modes")
    benchmark = policy["benchmark_asset"]
    if benchmark is not None and (
        not isinstance(benchmark, str) or ASSET.fullmatch(benchmark) is None
    ):
        fail("LEADERSHIP_POLICY_INVALID", "benchmark_asset")
    for key in ("lookback_sessions", "minimum_assets", "group_minimum_members"):
        value = policy[key]
        if value is not None and (type(value) is not int or value < 1):
            fail("LEADERSHIP_POLICY_INVALID", key)
    if policy["minimum_assets"] is not None and policy["minimum_assets"] < 2:
        fail("LEADERSHIP_POLICY_INVALID", "minimum_assets")
    if policy["required_groups"] is not None:
        groups = sorted_strings(
            policy["required_groups"],
            "LEADERSHIP_POLICY_INVALID",
            "required_groups",
        )
        if any(GROUP.fullmatch(group) is None for group in groups):
            fail("LEADERSHIP_POLICY_INVALID", "required_groups")
    if (
        policy["group_return_method"] is not None
        and policy["group_return_method"] != "equal_weight_daily_rebalanced"
    ):
        fail("LEADERSHIP_POLICY_INVALID", "group_return_method")
    if (
        policy["split_window_policy"] is not None
        and policy["split_window_policy"] != "no_split_events_required"
    ):
        fail("LEADERSHIP_POLICY_INVALID", "split_window_policy")
    return policy


def require_ratified_leadership_policy(
    policy: dict, first_session: Optional[dt.date] = None
) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("LEADERSHIP_POLICY_UNRATIFIED", policy["policy_version"])
    required = (
        policy["effective_from"],
        policy["source_name"],
        policy["quote_currency"],
        policy["market_timezone"],
        policy["price_basis"],
        policy["allowed_run_modes"],
        policy["session_calendar_source"],
        policy["benchmark_asset"],
        policy["lookback_sessions"],
        policy["minimum_assets"],
        policy["required_groups"],
        policy["group_minimum_members"],
        policy["group_return_method"],
        policy["split_window_policy"],
    )
    if any(item is None for item in required):
        fail("LEADERSHIP_POLICY_INVALID", "ratified fields incomplete")
    if not policy["allowed_run_modes"] or not policy["required_groups"]:
        fail("LEADERSHIP_POLICY_INVALID", "ratified lists empty")
    effective = parse_date(
        policy["effective_from"],
        "LEADERSHIP_POLICY_INVALID",
        "effective_from",
    )
    if first_session is not None and effective > first_session:
        fail("LEADERSHIP_POLICY_NOT_EFFECTIVE", first_session.isoformat())


def normalize_effective_records(
    records: object,
    code: str,
    with_groups: bool,
) -> list:
    if not isinstance(records, list):
        fail(code, "records")
    normalized = []
    expected = {"asset", "effective_from", "effective_to", "reason"}
    if with_groups:
        expected.add("groups")
    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != expected:
            fail(code, f"record {index} schema")
        asset = record["asset"]
        if not isinstance(asset, str) or ASSET.fullmatch(asset) is None:
            fail(code, f"record {index} asset")
        start = parse_date(
            record["effective_from"], code, f"record {index} effective_from"
        )
        end = None
        if record["effective_to"] is not None:
            end = parse_date(
                record["effective_to"],
                code,
                f"record {index} effective_to",
            )
            if end < start:
                fail(code, f"record {index} range")
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            fail(code, f"record {index} reason")
        if with_groups:
            groups = sorted_strings(record["groups"], code, f"record {index} groups")
            if not groups or any(GROUP.fullmatch(group) is None for group in groups):
                fail(code, f"record {index} groups")
        normalized.append(record | {"_start": start, "_end": end})

    by_asset = {}
    for record in normalized:
        by_asset.setdefault(record["asset"], []).append(record)
    for asset, asset_records in by_asset.items():
        asset_records.sort(key=lambda item: item["_start"])
        for before, after in zip(asset_records, asset_records[1:]):
            before_end = before["_end"] or dt.date.max
            if after["_start"] <= before_end:
                fail(f"{code.removesuffix('_INVALID')}_RANGE_OVERLAP", asset)
    return normalized


def load_universe_policy(path: Path = UNIVERSE_POLICY_PATH) -> dict:
    policy = read_json(path, "UNIVERSE_POLICY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "source_name",
        "effective_from",
        "membership_kind",
        "records",
    }
    if set(policy) != expected:
        fail("UNIVERSE_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
    ):
        fail("UNIVERSE_POLICY_INVALID", "header")
    if policy["source_name"] is not None and (
        not isinstance(policy["source_name"], str)
        or not policy["source_name"].strip()
    ):
        fail("UNIVERSE_POLICY_INVALID", "source_name")
    if policy["effective_from"] is not None:
        parse_date(
            policy["effective_from"],
            "UNIVERSE_POLICY_INVALID",
            "effective_from",
        )
    if (
        policy["membership_kind"] is not None
        and policy["membership_kind"] != "point_in_time_source_coverage"
    ):
        fail("UNIVERSE_POLICY_INVALID", "membership_kind")
    return policy | {
        "_records": normalize_effective_records(
            policy["records"], "UNIVERSE_POLICY_INVALID", False
        )
    }


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
    ):
        fail("TAXONOMY_INVALID", "header")
    if policy["source_name"] is not None and (
        not isinstance(policy["source_name"], str)
        or not policy["source_name"].strip()
    ):
        fail("TAXONOMY_INVALID", "source_name")
    if policy["effective_from"] is not None:
        parse_date(
            policy["effective_from"], "TAXONOMY_INVALID", "effective_from"
        )
    return policy | {
        "_records": normalize_effective_records(
            policy["records"], "TAXONOMY_INVALID", True
        )
    }


def require_ratified_effective_policy(
    policy: dict,
    label: str,
    code: str,
    source_name: str,
    first_session: dt.date,
) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail(f"{label}_UNRATIFIED", policy["policy_version"])
    if (
        policy["effective_from"] is None
        or policy["source_name"] != source_name
        or not policy["records"]
    ):
        fail(code, "ratified fields incomplete or source mismatch")
    effective = parse_date(policy["effective_from"], code, "effective_from")
    if effective > first_session:
        fail(f"{label}_NOT_EFFECTIVE", first_session.isoformat())


def active_records(policy: dict, asset: str, day: dt.date) -> list:
    return [
        item
        for item in policy["_records"]
        if item["asset"] == asset
        and item["_start"] <= day <= (item["_end"] or dt.date.max)
    ]


def universe_members(policy: dict, day: dt.date) -> list:
    members = sorted(
        {
            item["asset"]
            for item in policy["_records"]
            if item["_start"] <= day <= (item["_end"] or dt.date.max)
        }
    )
    if not members:
        fail("UNIVERSE_EMPTY", day.isoformat())
    return members


def groups_for(policy: dict, asset: str, day: dt.date) -> list:
    matches = active_records(policy, asset, day)
    if len(matches) != 1:
        code = "TAXONOMY_RANGE_OVERLAP" if len(matches) > 1 else "TAXONOMY_MISSING"
        fail(code, f"{asset}@{day.isoformat()}")
    return matches[0]["groups"]


def normalize_input(payload: dict, policy: dict) -> dict:
    ensure_no_float(payload)
    expected = {
        "schema_version",
        "source_name",
        "quote_currency",
        "market_timezone",
        "run_mode",
        "price_basis",
        "observation_date",
        "fetched_at",
        "decision_at",
        "expected_session_dates",
        "asset_rows",
    }
    if set(payload) != expected or payload.get("schema_version") != 1:
        fail("INPUT_INVALID", "schema")
    for key in (
        "source_name",
        "quote_currency",
        "market_timezone",
        "run_mode",
        "price_basis",
        "observation_date",
        "fetched_at",
    ):
        if not isinstance(payload.get(key), str) or not payload[key]:
            fail("INPUT_INVALID", key)
    if payload["decision_at"] is not None and not isinstance(
        payload["decision_at"], str
    ):
        fail("INPUT_INVALID", "decision_at")
    comparisons = {
        "source_name": policy["source_name"],
        "quote_currency": policy["quote_currency"],
        "market_timezone": policy["market_timezone"],
        "price_basis": policy["price_basis"],
    }
    for key, expected_value in comparisons.items():
        if payload[key] != expected_value:
            fail("INPUT_POLICY_MISMATCH", key)
    if payload["run_mode"] not in policy["allowed_run_modes"]:
        fail("INPUT_POLICY_MISMATCH", "run_mode")

    dates_value = payload["expected_session_dates"]
    if not isinstance(dates_value, list) or not dates_value:
        fail("SESSION_CALENDAR_INVALID", "expected_session_dates")
    sessions = [
        parse_date(value, "SESSION_CALENDAR_INVALID", "session date")
        for value in dates_value
    ]
    if sessions != sorted(set(sessions)) or any(
        day.weekday() >= 5 for day in sessions
    ):
        fail("SESSION_CALENDAR_INVALID", "order, duplicate, or weekend")
    if len(sessions) != policy["lookback_sessions"] + 1:
        fail(
            "SESSION_WINDOW_MISMATCH",
            f"{len(sessions)} != {policy['lookback_sessions']}+1",
        )
    observation = parse_date(
        payload["observation_date"], "INPUT_INVALID", "observation_date"
    )
    if observation != sessions[-1]:
        fail("OBSERVATION_DATE_MISMATCH", "last expected session")

    asset_rows = payload["asset_rows"]
    if not isinstance(asset_rows, list) or not asset_rows:
        fail("INPUT_ROWS_INVALID", "asset_rows")
    asset_ids = [item.get("asset") for item in asset_rows if isinstance(item, dict)]
    if (
        len(asset_ids) != len(asset_rows)
        or any(not isinstance(asset, str) or ASSET.fullmatch(asset) is None for asset in asset_ids)
        or asset_ids != sorted(set(asset_ids))
    ):
        fail("INPUT_ROWS_INVALID", "asset order or identity")

    by_asset = {}
    allowed_dates = set(sessions)
    for item in asset_rows:
        if set(item) != {"asset", "rows"} or not isinstance(item["rows"], list):
            fail("INPUT_ROWS_INVALID", item.get("asset", "unknown"))
        rows = {}
        ordered_dates = []
        for index, row in enumerate(item["rows"]):
            if not isinstance(row, dict) or set(row) != {
                "session_date",
                "close",
                "split_factor",
            }:
                fail("INPUT_ROW_INVALID", f"{item['asset']} row {index}")
            day = parse_date(
                row["session_date"],
                "INPUT_ROW_INVALID",
                f"{item['asset']} row {index} date",
            )
            if day not in allowed_dates:
                fail("INPUT_ROW_OUTSIDE_WINDOW", f"{item['asset']}@{day}")
            ordered_dates.append(day)
            rows[day] = {
                "close": decimal_string(
                    row["close"], f"{item['asset']} row {index} close"
                ),
                "split_factor": decimal_string(
                    row["split_factor"],
                    f"{item['asset']} row {index} split_factor",
                ),
            }
        if ordered_dates != sorted(set(ordered_dates)):
            fail("INPUT_ROW_DATE_INVALID", item["asset"])
        if any(row["split_factor"] != Decimal(1) for row in rows.values()):
            fail("SPLIT_EVENT_IN_WINDOW", item["asset"])
        by_asset[item["asset"]] = rows
    return {
        "sessions": sessions,
        "observation_date": observation,
        "rows": by_asset,
    }


def temporal_classification(payload: dict) -> dict:
    try:
        result = TEMPORAL.classify(
            payload["run_mode"],
            payload["price_basis"],
            payload["observation_date"],
            fetched_at=payload["fetched_at"],
            decision_at=payload["decision_at"],
            market_tz=payload["market_timezone"],
        )
    except TEMPORAL.Stop as exc:
        fail("TEMPORAL_INPUT_INVALID", str(exc))
    if payload["run_mode"] == "FORWARD_SHADOW" and not result[
        "forward_pit_qualified"
    ]:
        fail("TEMPORAL_INPUT_NOT_QUALIFIED", result["reason_code"])
    if result["eligibility"] not in {
        "FORWARD_PIT_QUALIFIED",
        "CAUSAL_RESEARCH_ONLY",
        "REVISED_SENSITIVITY_ONLY",
    }:
        fail("TEMPORAL_INPUT_NOT_QUALIFIED", result["eligibility"])
    return result


def cumulative(values: list[Decimal]) -> Decimal:
    result = Decimal(1)
    for value in values:
        result *= value
    return result


def authority_boundary() -> dict:
    return {
        "leader_classification_authorized": False,
        "ranking_authorized": False,
        "trend_direction_authorized": False,
        "breadth_direction_authorized": False,
        "threshold_authorized": False,
        "regime_axis_input_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def build_transform(
    payload: dict,
    contract_path: Path = CONTRACT_PATH,
    leadership_policy_path: Path = LEADERSHIP_POLICY_PATH,
    universe_policy_path: Path = UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = TAXONOMY_PATH,
) -> dict:
    contract = load_contract(contract_path)
    leadership = load_leadership_policy(leadership_policy_path)
    if leadership["approval_status"] != "RATIFIED":
        fail("LEADERSHIP_POLICY_UNRATIFIED", leadership["policy_version"])
    require_ratified_leadership_policy(leadership)
    normalized = normalize_input(payload, leadership)
    first_return_day = normalized["sessions"][1]
    require_ratified_leadership_policy(leadership, first_return_day)

    universe = load_universe_policy(universe_policy_path)
    taxonomy = load_taxonomy(taxonomy_path)
    require_ratified_effective_policy(
        universe,
        "UNIVERSE_POLICY",
        "UNIVERSE_POLICY_INVALID",
        leadership["source_name"],
        first_return_day,
    )
    require_ratified_effective_policy(
        taxonomy,
        "TAXONOMY",
        "TAXONOMY_INVALID",
        leadership["source_name"],
        first_return_day,
    )
    if universe["membership_kind"] != "point_in_time_source_coverage":
        fail("UNIVERSE_POLICY_INVALID", "membership_kind")

    temporal = temporal_classification(payload)
    sessions = normalized["sessions"]
    rows = normalized["rows"]
    benchmark = leadership["benchmark_asset"]
    places = contract["output_decimal_places"]
    approved_assets = {
        asset
        for day in sessions[1:]
        for asset in universe_members(universe, day)
    }
    extra_assets = sorted(set(rows) - approved_assets)
    if extra_assets:
        fail("INPUT_ASSET_OUTSIDE_UNIVERSE", ",".join(extra_assets))
    daily = []
    asset_returns = {}
    group_returns = {group: [] for group in leadership["required_groups"]}
    asset_day_counts = {}
    group_minimum_counts = {group: [] for group in leadership["required_groups"]}

    with localcontext() as context:
        context.prec = 50
        for previous_day, day in zip(sessions, sessions[1:]):
            members = universe_members(universe, day)
            if len(members) < leadership["minimum_assets"]:
                fail(
                    "UNIVERSE_COVERAGE_INCOMPLETE",
                    f"{day}={len(members)}<{leadership['minimum_assets']}",
                )
            if benchmark not in members:
                fail("BENCHMARK_NOT_IN_UNIVERSE", day.isoformat())

            observations = []
            for asset in members:
                if asset not in rows:
                    fail("INPUT_ASSET_MISSING", f"{asset}@{day}")
                if previous_day not in rows[asset] or day not in rows[asset]:
                    fail("SESSION_COVERAGE_MISMATCH", f"{asset}@{day}")
                gross = rows[asset][day]["close"] / rows[asset][previous_day]["close"]
                groups = groups_for(taxonomy, asset, day)
                observations.append(
                    {"asset": asset, "daily_gross_return": gross, "groups": groups}
                )
                asset_returns.setdefault(asset, []).append(gross)
                asset_day_counts[asset] = asset_day_counts.get(asset, 0) + 1

            benchmark_gross = next(
                item["daily_gross_return"]
                for item in observations
                if item["asset"] == benchmark
            )
            population = [item for item in observations if item["asset"] != benchmark]
            if not population:
                fail("PARTICIPATION_POPULATION_EMPTY", day.isoformat())
            outperforming = sum(
                item["daily_gross_return"] > benchmark_gross for item in population
            )

            daily_groups = []
            for group in leadership["required_groups"]:
                selected = [item for item in observations if group in item["groups"]]
                if len(selected) < leadership["group_minimum_members"]:
                    fail(
                        "GROUP_COVERAGE_INCOMPLETE",
                        f"{group}@{day}={len(selected)}<"
                        f"{leadership['group_minimum_members']}",
                    )
                group_gross = sum(
                    (item["daily_gross_return"] for item in selected), Decimal(0)
                ) / Decimal(len(selected))
                group_returns[group].append(group_gross)
                group_minimum_counts[group].append(len(selected))
                daily_groups.append(
                    {"group_id": group, "member_count": len(selected)}
                )

            daily.append(
                {
                    "session_date": day.isoformat(),
                    "eligible_non_benchmark_count": len(population),
                    "outperforming_benchmark_count": outperforming,
                    "outperformance_participation_fraction": render_decimal(
                        Decimal(outperforming) / Decimal(len(population)), places
                    ),
                    "required_group_member_counts": daily_groups,
                }
            )

    lookback = leadership["lookback_sessions"]
    if asset_day_counts.get(benchmark) != lookback:
        fail("BENCHMARK_WINDOW_INCOMPLETE", benchmark)
    benchmark_cumulative = cumulative(asset_returns[benchmark])
    complete_assets = sorted(
        asset for asset, count in asset_day_counts.items() if count == lookback
    )
    assets = [
        {
            "asset": asset,
            "observed_session_count": lookback,
            "cumulative_gross_return": render_decimal(
                cumulative(asset_returns[asset]), places
            ),
            "relative_strength_vs_benchmark": render_decimal(
                cumulative(asset_returns[asset]) / benchmark_cumulative
                - Decimal(1),
                places,
            ),
            "classification": "UNDEFINED",
        }
        for asset in complete_assets
    ]
    partial = [
        {
            "asset": asset,
            "observed_session_count": asset_day_counts[asset],
            "required_session_count": lookback,
            "reason": "not_present_in_every_point_in_time_universe",
        }
        for asset in sorted(asset_day_counts)
        if asset_day_counts[asset] != lookback
    ]
    groups = [
        {
            "group_id": group,
            "observed_session_count": lookback,
            "minimum_daily_member_count": min(group_minimum_counts[group]),
            "required_minimum_member_count": leadership[
                "group_minimum_members"
            ],
            "cumulative_gross_return": render_decimal(
                cumulative(group_returns[group]), places
            ),
            "relative_strength_vs_benchmark": render_decimal(
                cumulative(group_returns[group]) / benchmark_cumulative
                - Decimal(1),
                places,
            ),
            "classification": "UNDEFINED",
        }
        for group in leadership["required_groups"]
    ]
    status = {
        "FORWARD_PIT_QUALIFIED": "OBSERVED_UNCLASSIFIED",
        "CAUSAL_RESEARCH_ONLY": "CAUSAL_RESEARCH_ONLY",
        "REVISED_SENSITIVITY_ONLY": "REVISED_SENSITIVITY_ONLY",
    }[temporal["eligibility"]]
    available_at = temporal.get("available_at", payload["fetched_at"])
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "transform_version": contract["transform_version"],
        "market": "US",
        "measurement": contract["measurement"],
        "status": status,
        "observation_date": normalized["observation_date"].isoformat(),
        "available_at": available_at,
        "benchmark_asset": benchmark,
        "window": {
            "first_input_session": sessions[0].isoformat(),
            "first_return_session": sessions[1].isoformat(),
            "last_return_session": sessions[-1].isoformat(),
            "lookback_sessions": lookback,
            "exact_expected_sessions": True,
        },
        "temporal_eligibility": {
            "run_mode": temporal["run_mode"],
            "price_basis": temporal["price_basis"],
            "eligibility": temporal["eligibility"],
            "reason_code": temporal["reason_code"],
            "authoritative_historical_pit": temporal[
                "authoritative_historical_pit"
            ],
            "forward_pit_qualified": temporal["forward_pit_qualified"],
        },
        "asset_relative_strength": assets,
        "partial_window_assets": partial,
        "group_relative_strength": groups,
        "daily_relative_participation": daily,
        "retention": {
            "input_policy": contract["input_retention_policy"],
            "output_policy": contract["output_retention_policy"],
            "vendor_rows_emitted": False,
            "vendor_prices_emitted": False,
            "reconstructive_series_emitted": False,
        },
        "policies": {
            "leadership": {
                "policy_version": leadership["policy_version"],
                "policy_sha256": file_sha256(leadership_policy_path),
                "approval_status": leadership["approval_status"],
                "session_calendar_source": leadership[
                    "session_calendar_source"
                ],
            },
            "universe": {
                "policy_version": universe["policy_version"],
                "policy_sha256": file_sha256(universe_policy_path),
                "approval_status": universe["approval_status"],
                "membership_kind": universe["membership_kind"],
            },
            "taxonomy": {
                "policy_version": taxonomy["policy_version"],
                "policy_sha256": file_sha256(taxonomy_path),
                "approval_status": taxonomy["approval_status"],
                "effective_dated": True,
            },
        },
        "lineage": {
            "input_sha256": canonical_payload_sha256(payload),
            "source_temporal_contract": contract[
                "source_temporal_contract"
            ],
            "session_count": len(sessions),
            "return_session_count": lookback,
            "session_coverage_complete": True,
            "current_membership_backfill_authorized": False,
        },
    } | authority_boundary()


def write_output(payload: dict, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp.{os.getpid()}"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    transform = parser.add_subparsers(dest="command", required=True).add_parser(
        "transform"
    )
    transform.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    transform.add_argument(
        "--leadership-policy", type=Path, default=LEADERSHIP_POLICY_PATH
    )
    transform.add_argument(
        "--universe-policy", type=Path, default=UNIVERSE_POLICY_PATH
    )
    transform.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    transform.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.buffer.read()
        if not raw:
            fail("INPUT_INVALID", "stdin is empty")
        result = build_transform(
            parse_payload(raw),
            contract_path=args.contract,
            leadership_policy_path=args.leadership_policy,
            universe_policy_path=args.universe_policy,
            taxonomy_path=args.taxonomy,
        )
        if args.out is None:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(write_output(result, args.out))
        return 0
    except USLeadershipError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
