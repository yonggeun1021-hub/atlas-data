#!/usr/bin/env python3
"""Validate as-captured Kraken universes and build raw alt participation.

The helper makes no network call and publishes no tracked factor.  A ratified,
effective-dated universe policy is mandatory.  Every point is rebuilt from the
Assets, AssetPairs, and per-pair OHLC responses captured on that vintage date;
the current Kraken listing must never be used to backfill historical membership.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "crypto_breadth_contract.json"
UNIVERSE_POLICY_PATH = (
    ROOT / "config" / "crypto_breadth_universe_policy.json"
)
IDENTITY_EXCEPTIONS_PATH = (
    ROOT / "config" / "crypto_asset_identity_exceptions.json"
)
UTC = dt.timezone.utc
CAPTURE_VERSION = re.compile(r"^crypto-breadth-capture/v[1-9][0-9]*$")
IDENTITY_POLICY_VERSION = re.compile(
    r"^crypto_asset_identity_exceptions/v[1-9][0-9]*$"
)
ASSET_ID = re.compile(r"^[A-Z0-9._-]{1,32}$")
OHLC_STEM = re.compile(r"^[0-9a-f]{64}$")
SHA_LINE = re.compile(
    r"^([0-9a-f]{64})  "
    r"(kraken_assets\.json|kraken_asset_pairs\.json|"
    r"ohlc/[0-9a-f]{64}\.json)$"
)


class BreadthError(RuntimeError):
    """Fail-closed source, identity, universe, or transform violation."""


def fail(code: str, detail: str) -> None:
    raise BreadthError(f"{code}: {detail}")


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


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_INVALID")
    expected = {
        "schema_version",
        "source_name",
        "assets_endpoint",
        "asset_pairs_endpoint",
        "ohlc_endpoint_template",
        "asset_version",
        "asset_class",
        "interval_minutes",
        "market_timezone",
        "quote_currency",
        "capture_mode",
        "assets_raw_file",
        "asset_pairs_raw_file",
        "ohlc_directory",
        "minimum_finalized_candles",
        "maximum_response_rows",
        "current_candle_policy",
        "close_semantics",
        "gap_policy",
        "candidate_pair_rule",
        "historical_universe_policy",
        "identity_exception_policy",
        "transform_version",
        "replay_version",
        "output_decimal_places",
        "rounding",
    }
    pinned = {
        "schema_version": 1,
        "source_name": "kraken_spot_market_data",
        "assets_endpoint": (
            "https://api.kraken.com/0/public/Assets?assetVersion=1"
        ),
        "asset_pairs_endpoint": (
            "https://api.kraken.com/0/public/AssetPairs?"
            "assetVersion=1&aclass_base=currency"
        ),
        "ohlc_endpoint_template": (
            "https://api.kraken.com/0/public/OHLC?"
            "pair={PAIR}&interval=1440&assetVersion=1"
        ),
        "asset_version": 1,
        "asset_class": "currency",
        "interval_minutes": 1440,
        "market_timezone": "UTC",
        "quote_currency": "USD",
        "capture_mode": "direct_fetch_append_only",
        "assets_raw_file": "kraken_assets.json.gz",
        "asset_pairs_raw_file": "kraken_asset_pairs.json.gz",
        "ohlc_directory": "ohlc",
        "minimum_finalized_candles": 2,
        "maximum_response_rows": 720,
        "current_candle_policy": "exclude_last_row_always",
        "close_semantics": "last_trade_in_finalized_utc_daily_bucket",
        "gap_policy": "exact_t_and_t_minus_1_required",
        "candidate_pair_rule": (
            "ratified_policy_over_exact_asset_pairs_snapshot"
        ),
        "historical_universe_policy": (
            "as_captured_append_only_no_current_state_backfill"
        ),
        "identity_exception_policy": (
            "explicit_effective_dated_fail_closed"
        ),
        "transform_version": "crypto_breadth_observation/v1",
        "replay_version": "crypto_breadth_replay/v1",
        "output_decimal_places": 12,
        "rounding": "ROUND_HALF_EVEN",
    }
    if set(contract) != expected or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def parse_date(value: object, code: str, label: str) -> dt.date:
    if not isinstance(value, str):
        fail(code, label)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail(code, f"{label}={value}")


def load_identity_exceptions(
    path: Path = IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    policy = read_json(path, "IDENTITY_POLICY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "source_name",
        "asset_version",
        "records",
    }
    if set(policy) != expected:
        fail("IDENTITY_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or policy.get("source_name") != "kraken_spot_market_data"
        or policy.get("asset_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or IDENTITY_POLICY_VERSION.fullmatch(policy["policy_version"])
        is None
        or not isinstance(policy.get("records"), list)
    ):
        fail("IDENTITY_POLICY_INVALID", "header")

    normalized = []
    for index, record in enumerate(policy["records"]):
        keys = {
            "source_asset_id",
            "canonical_asset_id",
            "aliases",
            "effective_from",
            "effective_to",
            "reason",
        }
        if not isinstance(record, dict) or set(record) != keys:
            fail("IDENTITY_POLICY_INVALID", f"record {index} schema")
        source_id = record["source_asset_id"]
        canonical_id = record["canonical_asset_id"]
        if (
            not isinstance(source_id, str)
            or ASSET_ID.fullmatch(source_id) is None
            or not isinstance(canonical_id, str)
            or ASSET_ID.fullmatch(canonical_id) is None
        ):
            fail("IDENTITY_POLICY_INVALID", f"record {index} identity")
        aliases = record["aliases"]
        if (
            not isinstance(aliases, list)
            or any(
                not isinstance(alias, str)
                or ASSET_ID.fullmatch(alias) is None
                for alias in aliases
            )
            or aliases != sorted(set(aliases))
        ):
            fail("IDENTITY_POLICY_INVALID", f"record {index} aliases")
        start = parse_date(
            record["effective_from"],
            "IDENTITY_POLICY_INVALID",
            f"record {index} effective_from",
        )
        end_value = record["effective_to"]
        end = None
        if end_value is not None:
            end = parse_date(
                end_value,
                "IDENTITY_POLICY_INVALID",
                f"record {index} effective_to",
            )
            if end < start:
                fail("IDENTITY_POLICY_INVALID", f"record {index} range")
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            fail("IDENTITY_POLICY_INVALID", f"record {index} reason")
        normalized.append(record | {"_start": start, "_end": end})

    by_source = {}
    for record in normalized:
        by_source.setdefault(record["source_asset_id"], []).append(record)
    for source_id, records in by_source.items():
        records.sort(key=lambda item: item["_start"])
        for before, after in zip(records, records[1:]):
            before_end = before["_end"] or dt.date.max
            if after["_start"] <= before_end:
                fail("IDENTITY_RANGE_OVERLAP", source_id)
    return policy | {"_records": normalized}


def canonical_identity(source_id: str, day: dt.date, policy: dict) -> str:
    matches = []
    for record in policy["_records"]:
        end = record["_end"] or dt.date.max
        if (
            record["source_asset_id"] == source_id
            and record["_start"] <= day <= end
        ):
            matches.append(record)
    if len(matches) > 1:
        fail("IDENTITY_RANGE_OVERLAP", source_id)
    if matches:
        return matches[0]["canonical_asset_id"]
    return source_id


def load_universe_policy(path: Path = UNIVERSE_POLICY_PATH) -> dict:
    policy = read_json(path, "UNIVERSE_POLICY_INVALID")
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "source_name",
        "universe_kind",
        "effective_from",
        "quote_currency",
        "allowed_asset_statuses",
        "allowed_pair_statuses",
        "excluded_canonical_assets",
        "minimum_asset_count",
        "selection_rule",
    }
    if set(policy) != expected:
        fail("UNIVERSE_POLICY_INVALID", "schema")
    if (
        policy.get("schema_version") != 1
        or not isinstance(policy.get("policy_version"), str)
        or not policy["policy_version"].strip()
        or policy.get("approval_status") not in {"UNRATIFIED", "RATIFIED"}
        or policy.get("source_name") != "kraken_spot_market_data"
        or policy.get("universe_kind")
        != "breadth_source_coverage_not_investable"
        or policy.get("quote_currency") != "USD"
        or policy.get("selection_rule")
        != "all_matching_pairs_minus_explicit_canonical_exclusions"
    ):
        fail("UNIVERSE_POLICY_INVALID", "header or semantics")
    for key in ("allowed_asset_statuses", "allowed_pair_statuses"):
        values = policy.get(key)
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item for item in values)
            or values != sorted(set(values))
        ):
            fail("UNIVERSE_POLICY_INVALID", key)
    excluded = policy.get("excluded_canonical_assets")
    if (
        not isinstance(excluded, list)
        or any(
            not isinstance(item, str) or ASSET_ID.fullmatch(item) is None
            for item in excluded
        )
        or excluded != sorted(set(excluded))
    ):
        fail("UNIVERSE_POLICY_INVALID", "excluded_canonical_assets")
    minimum = policy.get("minimum_asset_count")
    if minimum is not None and (type(minimum) is not int or minimum < 2):
        fail("UNIVERSE_POLICY_INVALID", "minimum_asset_count")
    effective = policy.get("effective_from")
    if effective is not None:
        parse_date(effective, "UNIVERSE_POLICY_INVALID", "effective_from")
    return policy


def require_ratified_policy(policy: dict, as_of: dt.date) -> None:
    if policy["approval_status"] != "RATIFIED":
        fail("UNIVERSE_POLICY_UNRATIFIED", policy["policy_version"])
    if (
        policy["effective_from"] is None
        or not policy["allowed_asset_statuses"]
        or not policy["allowed_pair_statuses"]
        or policy["minimum_asset_count"] is None
    ):
        fail("UNIVERSE_POLICY_INVALID", "ratified fields incomplete")
    effective = parse_date(
        policy["effective_from"],
        "UNIVERSE_POLICY_INVALID",
        "effective_from",
    )
    if effective > as_of:
        fail("UNIVERSE_POLICY_NOT_EFFECTIVE", as_of.isoformat())


def snapshot_date(snapshot_dir: Path) -> dt.date:
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.is_dir():
        fail("NO_VINTAGE_MECHANISM", str(snapshot_dir))
    try:
        return dt.date.fromisoformat(snapshot_dir.name)
    except ValueError:
        fail("SNAPSHOT_DATE_INVALID", snapshot_dir.name)


def downloaded_at(snapshot_dir: Path, vintage: dt.date) -> str:
    try:
        value = (Path(snapshot_dir) / "_downloaded_at.txt").read_text(
            encoding="utf-8"
        ).strip()
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OSError, ValueError) as exc:
        fail("FETCHED_AT_INVALID", str(exc))
    if not value.endswith("Z") or parsed.tzinfo is None:
        fail("FETCHED_AT_INVALID", value)
    normalized = parsed.astimezone(UTC)
    if normalized.date() != vintage:
        fail("FETCHED_AT_DATE_MISMATCH", value)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def ohlc_file_name(pair_id: str) -> str:
    digest = hashlib.sha256(pair_id.encode("utf-8")).hexdigest()
    return f"ohlc/{digest}.json.gz"


def raw_checksum_name(relative_gz: str) -> str:
    if not relative_gz.endswith(".gz"):
        fail("RAW_FILE_INVALID", relative_gz)
    return relative_gz[:-3]


def checksum_index(snapshot_dir: Path, raw_names: set) -> dict:
    try:
        lines = (Path(snapshot_dir) / "_sha256.txt").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as exc:
        fail("CHECKSUM_FILE_INVALID", str(exc))
    parsed = {}
    names = []
    for line in lines:
        match = SHA_LINE.fullmatch(line)
        if match is None or match.group(2) in parsed:
            fail("CHECKSUM_FILE_INVALID", line or "empty")
        parsed[match.group(2)] = match.group(1)
        names.append(match.group(2))
    if names != sorted(names) or set(parsed) != raw_names:
        fail("CHECKSUM_FILE_INVALID", "raw file inventory mismatch")
    return parsed


def read_raw(snapshot_dir: Path, relative_gz: str, expected_sha: str) -> dict:
    path = Path(snapshot_dir) / relative_gz
    try:
        with gzip.open(path, "rb") as stream:
            raw = stream.read()
    except (OSError, EOFError) as exc:
        fail("RAW_RESPONSE_INVALID", f"{relative_gz}: {exc}")
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != expected_sha:
        fail("CHECKSUM_MISMATCH", raw_checksum_name(relative_gz))
    try:
        payload = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, InvalidOperation) as exc:
        fail("RAW_RESPONSE_INVALID", f"{relative_gz}: {exc}")
    return {
        "file": relative_gz,
        "response_sha256": actual_sha,
        "byte_length": len(raw),
        "payload": payload,
    }


def source_result(payload: object, label: str) -> dict:
    if not isinstance(payload, dict) or payload.get("error") != []:
        error = payload.get("error") if isinstance(payload, dict) else None
        fail("SOURCE_ERROR", f"{label}: {error}")
    if set(payload) != {"error", "result"} or not isinstance(
        payload.get("result"), dict
    ):
        fail("PAYLOAD_SHAPE_INVALID", label)
    return payload["result"]


def normalize_assets(payload: object) -> dict:
    result = source_result(payload, "Assets")
    if not result:
        fail("ASSET_CATALOG_EMPTY", "Assets")
    assets = {}
    for asset_id, info in result.items():
        if (
            not isinstance(asset_id, str)
            or ASSET_ID.fullmatch(asset_id) is None
            or not isinstance(info, dict)
        ):
            fail("ASSET_INVALID", str(asset_id))
        if (
            info.get("aclass") != "currency"
            or not isinstance(info.get("altname"), str)
            or not info["altname"]
            or not isinstance(info.get("status"), str)
            or not info["status"]
        ):
            fail("ASSET_INVALID", asset_id)
        assets[asset_id] = {
            "source_asset_id": asset_id,
            "altname": info["altname"],
            "status": info["status"],
        }
    return assets


def normalize_pairs(payload: object, assets: dict) -> dict:
    result = source_result(payload, "AssetPairs")
    if not result:
        fail("PAIR_CATALOG_EMPTY", "AssetPairs")
    pairs = {}
    for pair_id, info in result.items():
        if not isinstance(pair_id, str) or not isinstance(info, dict):
            fail("PAIR_INVALID", str(pair_id))
        base = info.get("base")
        quote = info.get("quote")
        if (
            not isinstance(base, str)
            or not isinstance(quote, str)
            or pair_id != f"{base}/{quote}"
            or info.get("aclass_base") != "currency"
            or info.get("aclass_quote") != "currency"
            or not isinstance(info.get("status"), str)
            or not info["status"]
            or not isinstance(info.get("altname"), str)
            or not isinstance(info.get("wsname"), str)
            or base not in assets
            or quote not in assets
        ):
            fail("PAIR_INVALID", pair_id)
        pairs[pair_id] = {
            "pair_id": pair_id,
            "base": base,
            "quote": quote,
            "status": info["status"],
            "altname": info["altname"],
            "wsname": info["wsname"],
        }
    return pairs


def decimal_field(value: object, label: str, allow_zero: bool) -> Decimal:
    if not isinstance(value, str):
        fail("CANDLE_VALUE_INVALID", label)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        fail("CANDLE_VALUE_INVALID", label)
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        fail("CANDLE_VALUE_INVALID", label)
    return parsed


def candle_date(timestamp: object, label: str) -> dt.date:
    if type(timestamp) is not int:
        fail("CANDLE_TIME_INVALID", label)
    try:
        moment = dt.datetime.fromtimestamp(timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        fail("CANDLE_TIME_INVALID", f"{label}: {exc}")
    if (moment.hour, moment.minute, moment.second, moment.microsecond) != (
        0,
        0,
        0,
        0,
    ):
        fail("CANDLE_TIME_INVALID", f"{label}: not UTC midnight")
    return moment.date()


def normalize_candle(row: object, index: int, pair_id: str) -> dict:
    label = f"{pair_id} row {index}"
    if not isinstance(row, list) or len(row) != 8:
        fail("CANDLE_SHAPE_INVALID", label)
    day = candle_date(row[0], label)
    open_price = decimal_field(row[1], f"{label} open", False)
    high = decimal_field(row[2], f"{label} high", False)
    low = decimal_field(row[3], f"{label} low", False)
    close = decimal_field(row[4], f"{label} close", False)
    vwap = decimal_field(row[5], f"{label} vwap", False)
    volume = decimal_field(row[6], f"{label} volume", True)
    trades = row[7]
    if type(trades) is not int or trades < 0:
        fail("CANDLE_VALUE_INVALID", f"{label} trade_count")
    if (
        high < low
        or high < open_price
        or high < close
        or low > open_price
        or low > close
        or vwap < low
        or vwap > high
    ):
        fail("CANDLE_OHLC_INVALID", label)
    return {
        "date": day,
        "close": close,
        "volume": volume,
        "trade_count": trades,
    }


def normalize_ohlc(
    payload: object,
    vintage: dt.date,
    contract: dict,
    relative_file: str,
) -> dict:
    result = source_result(payload, relative_file)
    pair_keys = [key for key in result if key != "last"]
    if (
        len(pair_keys) != 1
        or set(result) != {pair_keys[0], "last"}
        or type(result.get("last")) is not int
    ):
        fail("PAYLOAD_SHAPE_INVALID", relative_file)
    pair_id = pair_keys[0]
    rows = result[pair_id]
    if (
        not isinstance(pair_id, str)
        or not isinstance(rows, list)
        or len(rows) < contract["minimum_finalized_candles"] + 1
        or len(rows) > contract["maximum_response_rows"]
    ):
        fail("OHLC_HISTORY_INVALID", pair_id)
    candles = [
        normalize_candle(row, index, pair_id)
        for index, row in enumerate(rows)
    ]
    dates = [item["date"] for item in candles]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        fail("CANDLE_ORDER_OR_DUPLICATE", pair_id)
    current = candles[-1]
    finalized = candles[:-1]
    if current["date"] != vintage:
        fail("CURRENT_CANDLE_DATE_MISMATCH", pair_id)
    if any(item["date"] >= vintage for item in finalized):
        fail("FINALIZED_BOUNDARY_INVALID", pair_id)
    expected_t = vintage - dt.timedelta(days=1)
    expected_t_minus_1 = vintage - dt.timedelta(days=2)
    if (
        finalized[-1]["date"] != expected_t
        or finalized[-2]["date"] != expected_t_minus_1
    ):
        fail("MISSING_EXACT_T_OR_T_MINUS_1", pair_id)
    if relative_file != ohlc_file_name(pair_id):
        fail("OHLC_FILENAME_MISMATCH", pair_id)
    return {
        "pair_id": pair_id,
        "response_rows": len(candles),
        "finalized_rows": len(finalized),
        "latest_finalized_day": expected_t.isoformat(),
        "previous_finalized_day": expected_t_minus_1.isoformat(),
        "current_excluded_day": current["date"].isoformat(),
        "previous_close": finalized[-2]["close"],
        "latest_close": finalized[-1]["close"],
    }


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("FILE_HASH_INVALID", str(exc))


def source_core(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
    identity_exceptions_path: Path = IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    contract = load_contract() if contract is None else contract
    identity = load_identity_exceptions(identity_exceptions_path)
    snapshot_dir = Path(snapshot_dir)
    vintage = snapshot_date(snapshot_dir)
    fetched = downloaded_at(snapshot_dir, vintage)
    ohlc_dir = snapshot_dir / contract["ohlc_directory"]
    try:
        ohlc_files = sorted(
            path.relative_to(snapshot_dir).as_posix()
            for path in ohlc_dir.glob("*.json.gz")
            if path.is_file()
        )
    except OSError as exc:
        fail("RAW_FILE_INVALID", str(exc))
    if not ohlc_files or any(
        OHLC_STEM.fullmatch(Path(name).name.removesuffix(".json.gz")) is None
        for name in ohlc_files
    ):
        fail("OHLC_FILE_INVENTORY_INVALID", str(ohlc_dir))
    relative_files = [
        contract["assets_raw_file"],
        contract["asset_pairs_raw_file"],
    ] + ohlc_files
    discovered_gzip = sorted(
        path.relative_to(snapshot_dir).as_posix()
        for path in snapshot_dir.rglob("*.json.gz")
        if path.is_file()
    )
    if discovered_gzip != sorted(relative_files):
        fail("RAW_FILE_INVENTORY_INVALID", str(snapshot_dir))
    raw_names = {raw_checksum_name(name) for name in relative_files}
    checksums = checksum_index(snapshot_dir, raw_names)
    raw_items = {
        name: read_raw(
            snapshot_dir,
            name,
            checksums[raw_checksum_name(name)],
        )
        for name in relative_files
    }
    assets = normalize_assets(
        raw_items[contract["assets_raw_file"]]["payload"]
    )
    pairs = normalize_pairs(
        raw_items[contract["asset_pairs_raw_file"]]["payload"],
        assets,
    )
    ohlc = {}
    for name in ohlc_files:
        series = normalize_ohlc(
            raw_items[name]["payload"], vintage, contract, name
        )
        pair_id = series["pair_id"]
        if pair_id not in pairs:
            fail("OHLC_PAIR_NOT_IN_CAPTURED_CATALOG", pair_id)
        if pair_id in ohlc:
            fail("OHLC_PAIR_DUPLICATE", pair_id)
        ohlc[pair_id] = series | {
            "file": name,
            "response_sha256": raw_items[name]["response_sha256"],
            "byte_length": raw_items[name]["byte_length"],
        }
    return {
        "snapshot_date": vintage.isoformat(),
        "vintage": vintage,
        "fetched_at_utc": fetched,
        "contract": contract,
        "identity": identity,
        "identity_policy_sha256": file_sha256(identity_exceptions_path),
        "assets": assets,
        "pairs": pairs,
        "ohlc": ohlc,
        "assets_raw": raw_items[contract["assets_raw_file"]],
        "pairs_raw": raw_items[contract["asset_pairs_raw_file"]],
    }


def raw_manifest_entry(item: dict) -> dict:
    return {
        "file": item["file"],
        "response_sha256": item["response_sha256"],
        "byte_length": item["byte_length"],
    }


def manifest_payload(core: dict, capture_version: str) -> dict:
    if not CAPTURE_VERSION.fullmatch(capture_version):
        fail("CAPTURE_VERSION_INVALID", capture_version)
    contract = core["contract"]
    series = []
    for pair_id in sorted(core["ohlc"]):
        item = core["ohlc"][pair_id]
        series.append(
            {
                "pair_id": pair_id,
                "file": item["file"],
                "response_sha256": item["response_sha256"],
                "byte_length": item["byte_length"],
                "response_rows": item["response_rows"],
                "finalized_rows": item["finalized_rows"],
                "latest_finalized_day": item["latest_finalized_day"],
                "previous_finalized_day": item["previous_finalized_day"],
                "current_excluded_day": item["current_excluded_day"],
            }
        )
    return {
        "schema_version": 1,
        "capture_version": capture_version,
        "snapshot_date": core["snapshot_date"],
        "fetched_at_utc": core["fetched_at_utc"],
        "source": {
            "name": contract["source_name"],
            "assets_endpoint": contract["assets_endpoint"],
            "asset_pairs_endpoint": contract["asset_pairs_endpoint"],
            "ohlc_endpoint_template": contract["ohlc_endpoint_template"],
            "asset_version": contract["asset_version"],
            "interval_minutes": contract["interval_minutes"],
            "market_timezone": contract["market_timezone"],
            "current_candle_policy": contract["current_candle_policy"],
        },
        "identity": {
            "policy_version": core["identity"]["policy_version"],
            "policy_sha256": core["identity_policy_sha256"],
        },
        "raw": {
            "assets": raw_manifest_entry(core["assets_raw"]),
            "asset_pairs": raw_manifest_entry(core["pairs_raw"]),
            "ohlc": series,
        },
        "catalog_counts": {
            "assets": len(core["assets"]),
            "pairs": len(core["pairs"]),
            "ohlc_pairs": len(core["ohlc"]),
        },
        "historical_universe_policy": contract[
            "historical_universe_policy"
        ],
    }


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


def build_manifest(
    snapshot_dir: Path,
    capture_version: str,
    contract: Optional[dict] = None,
    identity_exceptions_path: Path = IDENTITY_EXCEPTIONS_PATH,
) -> Path:
    snapshot_dir = Path(snapshot_dir)
    target = snapshot_dir / "_manifest.json"
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    core = source_core(
        snapshot_dir,
        contract=contract,
        identity_exceptions_path=identity_exceptions_path,
    )
    return write_output(manifest_payload(core, capture_version), target)


def validate_manifest(core: dict, snapshot_dir: Path) -> dict:
    path = Path(snapshot_dir) / "_manifest.json"
    manifest = read_json(path, "MANIFEST_INVALID")
    expected = manifest_payload(core, manifest.get("capture_version", ""))
    if manifest != expected:
        fail("MANIFEST_MISMATCH", str(path))
    return manifest


def validate_snapshot(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
    identity_exceptions_path: Path = IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    core = source_core(
        snapshot_dir,
        contract=contract,
        identity_exceptions_path=identity_exceptions_path,
    )
    manifest = validate_manifest(core, snapshot_dir)
    return {
        "snapshot_date": core["snapshot_date"],
        "fetched_at_utc": core["fetched_at_utc"],
        "capture_version": manifest["capture_version"],
        "asset_count": len(core["assets"]),
        "pair_count": len(core["pairs"]),
        "ohlc_pair_count": len(core["ohlc"]),
        "identity_policy_version": core["identity"]["policy_version"],
    }


def render_decimal(value: Decimal, places: int) -> str:
    if not value.is_finite():
        fail("NUMBER_INVALID", str(value))
    quantum = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        fail("NUMBER_INVALID", str(exc))
    if rounded == 0:
        rounded = Decimal(0)
    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def authority_boundary() -> dict:
    return {
        "breadth_classification_authorized": False,
        "threshold_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def direction(previous: Decimal, latest: Decimal) -> str:
    if latest > previous:
        return "ADVANCE"
    if latest < previous:
        return "DECLINE"
    return "UNCHANGED"


def qualified_members(
    core: dict,
    universe_policy: dict,
) -> list:
    as_of = core["vintage"] - dt.timedelta(days=1)
    require_ratified_policy(universe_policy, as_of)
    allowed_assets = set(universe_policy["allowed_asset_statuses"])
    allowed_pairs = set(universe_policy["allowed_pair_statuses"])
    excluded = set(universe_policy["excluded_canonical_assets"])
    members = []
    canonical_seen = {}
    for pair_id in sorted(core["pairs"]):
        pair = core["pairs"][pair_id]
        if (
            pair["quote"] != universe_policy["quote_currency"]
            or pair["status"] not in allowed_pairs
            or core["assets"][pair["base"]]["status"] not in allowed_assets
            or core["assets"][pair["quote"]]["status"] not in allowed_assets
        ):
            continue
        canonical = canonical_identity(
            pair["base"], as_of, core["identity"]
        )
        if canonical in excluded:
            continue
        if canonical in canonical_seen:
            fail(
                "CANONICAL_ASSET_DUPLICATE",
                f"{canonical_seen[canonical]} and {pair_id}",
            )
        canonical_seen[canonical] = pair_id
        series = core["ohlc"].get(pair_id)
        if series is None:
            members.append(
                {
                    "pair_id": pair_id,
                    "source_asset_id": pair["base"],
                    "canonical_asset_id": canonical,
                    "series": None,
                }
            )
            continue
        members.append(
            {
                "pair_id": pair_id,
                "source_asset_id": pair["base"],
                "canonical_asset_id": canonical,
                "series": series,
            }
        )
    missing = [item["pair_id"] for item in members if item["series"] is None]
    if missing:
        fail("COVERAGE_INCOMPLETE", ",".join(missing))
    minimum = universe_policy["minimum_asset_count"]
    if len(members) < minimum:
        fail("UNIVERSE_BELOW_RATIFIED_MINIMUM", str(len(members)))
    if not any(item["canonical_asset_id"] == "BTC" for item in members):
        fail("BTC_REFERENCE_MISSING", as_of.isoformat())
    if not any(item["canonical_asset_id"] != "BTC" for item in members):
        fail("ALT_UNIVERSE_EMPTY", as_of.isoformat())
    return members


def member_payload(item: dict) -> dict:
    series = item["series"]
    return {
        "canonical_asset_id": item["canonical_asset_id"],
        "source_asset_id": item["source_asset_id"],
        "pair_id": item["pair_id"],
        "previous_finalized_day": series["previous_finalized_day"],
        "latest_finalized_day": series["latest_finalized_day"],
        "previous_close": render_decimal(series["previous_close"], 12),
        "latest_close": render_decimal(series["latest_close"], 12),
        "direction": direction(
            series["previous_close"], series["latest_close"]
        ),
    }


def participation(members: list, places: int) -> dict:
    counts = {"ADVANCE": 0, "DECLINE": 0, "UNCHANGED": 0}
    for item in members:
        counts[item["direction"]] += 1
    total = len(members)
    denominator = Decimal(total)
    return {
        "asset_count": total,
        "advancing_count": counts["ADVANCE"],
        "declining_count": counts["DECLINE"],
        "unchanged_count": counts["UNCHANGED"],
        "advance_fraction": render_decimal(
            Decimal(counts["ADVANCE"]) / denominator, places
        ),
        "decline_fraction": render_decimal(
            Decimal(counts["DECLINE"]) / denominator, places
        ),
        "unchanged_fraction": render_decimal(
            Decimal(counts["UNCHANGED"]) / denominator, places
        ),
        "classification": "UNDEFINED",
        "thresholds_applied": False,
    }


def build_transform(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
    universe_policy_path: Path = UNIVERSE_POLICY_PATH,
    identity_exceptions_path: Path = IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    contract = load_contract() if contract is None else contract
    core = source_core(
        snapshot_dir,
        contract=contract,
        identity_exceptions_path=identity_exceptions_path,
    )
    manifest = validate_manifest(core, snapshot_dir)
    universe_policy = load_universe_policy(universe_policy_path)
    members = qualified_members(core, universe_policy)
    rendered = [member_payload(item) for item in members]
    rendered.sort(key=lambda item: item["canonical_asset_id"])
    btc = [item for item in rendered if item["canonical_asset_id"] == "BTC"]
    alts = [item for item in rendered if item["canonical_asset_id"] != "BTC"]
    manifest_sha = file_sha256(Path(snapshot_dir) / "_manifest.json")
    as_of = (core["vintage"] - dt.timedelta(days=1)).isoformat()
    return {
        "schema_version": 1,
        "transform_version": contract["transform_version"],
        "market": "CRYPTO",
        "measurement": "raw_alt_participation",
        "status": "OBSERVED_UNCLASSIFIED",
        "as_of_date": as_of,
        "previous_date": (
            core["vintage"] - dt.timedelta(days=2)
        ).isoformat(),
        "universe": {
            "kind": universe_policy["universe_kind"],
            "policy_version": universe_policy["policy_version"],
            "policy_sha256": file_sha256(universe_policy_path),
            "approval_status": universe_policy["approval_status"],
            "effective_from": universe_policy["effective_from"],
            "quote_currency": universe_policy["quote_currency"],
            "asset_count": len(rendered),
            "coverage_complete": True,
            "members": rendered,
        },
        "btc_reference": btc[0],
        "alt_participation": participation(
            alts, contract["output_decimal_places"]
        ),
        "current_candle": {
            "date": core["snapshot_date"],
            "excluded_for_every_member": True,
            "reason": "source_documents_not_yet_committed_timeframe",
        },
        "lineage": {
            "pit_status": "qualified_as_captured_universe",
            "vintage_date": core["snapshot_date"],
            "available_at": core["fetched_at_utc"],
            "capture_version": manifest["capture_version"],
            "manifest_sha256": manifest_sha,
            "identity_policy_version": core["identity"]["policy_version"],
            "identity_policy_sha256": core["identity_policy_sha256"],
            "historical_universe_policy": contract[
                "historical_universe_policy"
            ],
        },
    } | authority_boundary()


def optional_date(value: Optional[str], label: str) -> Optional[dt.date]:
    if value is None:
        return None
    return parse_date(value, "REPLAY_DATE_INVALID", label)


def build_replay(
    snapshot_root: Path,
    contract: Optional[dict] = None,
    universe_policy_path: Path = UNIVERSE_POLICY_PATH,
    identity_exceptions_path: Path = IDENTITY_EXCEPTIONS_PATH,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    start = optional_date(start_date, "start_date")
    end = optional_date(end_date, "end_date")
    if start is not None and end is not None and start > end:
        fail("REPLAY_DATE_INVALID", "start after end")
    root = Path(snapshot_root)
    if not root.is_dir():
        fail("REPLAY_ROOT_INVALID", str(root))
    points = []
    for path in sorted(item for item in root.iterdir() if item.is_dir()):
        try:
            vintage = dt.date.fromisoformat(path.name)
        except ValueError:
            continue
        as_of = vintage - dt.timedelta(days=1)
        if start is not None and as_of < start:
            continue
        if end is not None and as_of > end:
            continue
        points.append(
            build_transform(
                path,
                contract=contract,
                universe_policy_path=universe_policy_path,
                identity_exceptions_path=identity_exceptions_path,
            )
        )
    if not points:
        fail("REPLAY_RANGE_EMPTY", f"{start_date}..{end_date}")
    return {
        "schema_version": 1,
        "replay_version": contract["replay_version"],
        "transform_version": contract["transform_version"],
        "mode": "independent_as_captured_daily_snapshots",
        "point_count": len(points),
        "first_as_of_date": points[0]["as_of_date"],
        "last_as_of_date": points[-1]["as_of_date"],
        "current_catalog_backfill_authorized": False,
        "points": points,
    } | authority_boundary()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest")
    manifest.add_argument("--snapshot-dir", type=Path, required=True)
    manifest.add_argument("--capture-version", required=True)
    manifest.add_argument(
        "--identity-exceptions", type=Path, default=IDENTITY_EXCEPTIONS_PATH
    )

    validate = sub.add_parser("validate")
    validate.add_argument("snapshot_dir", type=Path)
    validate.add_argument(
        "--identity-exceptions", type=Path, default=IDENTITY_EXCEPTIONS_PATH
    )

    transform = sub.add_parser("transform")
    transform.add_argument("snapshot_dir", type=Path)
    transform.add_argument(
        "--universe-policy", type=Path, default=UNIVERSE_POLICY_PATH
    )
    transform.add_argument(
        "--identity-exceptions", type=Path, default=IDENTITY_EXCEPTIONS_PATH
    )
    transform.add_argument("--out", type=Path)

    replay = sub.add_parser("replay")
    replay.add_argument("snapshot_root", type=Path)
    replay.add_argument(
        "--universe-policy", type=Path, default=UNIVERSE_POLICY_PATH
    )
    replay.add_argument(
        "--identity-exceptions", type=Path, default=IDENTITY_EXCEPTIONS_PATH
    )
    replay.add_argument("--start-date")
    replay.add_argument("--end-date")
    replay.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    if args.command == "manifest":
        print(
            build_manifest(
                args.snapshot_dir,
                args.capture_version,
                identity_exceptions_path=args.identity_exceptions,
            )
        )
        return 0
    if args.command == "validate":
        payload = validate_snapshot(
            args.snapshot_dir,
            identity_exceptions_path=args.identity_exceptions,
        )
    elif args.command == "transform":
        payload = build_transform(
            args.snapshot_dir,
            universe_policy_path=args.universe_policy,
            identity_exceptions_path=args.identity_exceptions,
        )
    else:
        payload = build_replay(
            args.snapshot_root,
            universe_policy_path=args.universe_policy,
            identity_exceptions_path=args.identity_exceptions,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    if getattr(args, "out", None):
        print(write_output(payload, args.out))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BreadthError as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
