#!/usr/bin/env python3
"""P3-01 policy-neutral Global Security / Asset Master.

The module validates caller-supplied cross-market identities and effective-dated
memberships.  It never discovers assets, merges identities, infers themes,
approves a universe, or decides investability.  Structural ambiguity fails
closed instead of choosing a winner.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "global_asset_master_contract.json"

CONTRACT_SCHEMA_VERSION = 1
INPUT_SCHEMA_VERSION = "global_asset_master_input/1"
OUTPUT_SCHEMA_VERSION = "global_asset_master_packet/1"

ASSET_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9$._:/-]{0,95}$")
NAMESPACE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,47}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AssetMasterError(ValueError):
    """Fail-closed Global Asset Master contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetMasterError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_authority = {
        "identity_recording_only": True,
        "automatic_identity_merge_authorized": False,
        "theme_inference_authorized": False,
        "universe_approval_authorized": False,
        "investability_authorized": False,
        "stage_promotion_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    expected_policy = {
        "cross_market_identity_schema": "IMPLEMENTED",
        "market_universe_policy": "UNRATIFIED",
        "theme_taxonomy": "UNRATIFIED",
        "automatic_identity_merge": "PROHIBITED",
        "membership_selection": "EXPLICIT_ONLY",
        "identity_collision": "FAIL_CLOSED",
    }
    if not isinstance(value, dict) or value.get("schema_version") != (
        CONTRACT_SCHEMA_VERSION
    ):
        raise AssetMasterError("CONTRACT_SCHEMA_MISMATCH")
    if value.get("contract_version") != "global_asset_master/1":
        raise AssetMasterError("CONTRACT_VERSION_MISMATCH")
    if value.get("input_schema_version") != INPUT_SCHEMA_VERSION:
        raise AssetMasterError("INPUT_SCHEMA_VERSION_MISMATCH")
    if value.get("output_schema_version") != OUTPUT_SCHEMA_VERSION:
        raise AssetMasterError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    if value.get("allowed_markets") != ["CRYPTO", "KOREA", "US"]:
        raise AssetMasterError("MARKET_REGISTRY_MISMATCH")
    if value.get("allowed_asset_classes") != ["CRYPTO_ASSET", "EQUITY"]:
        raise AssetMasterError("ASSET_CLASS_REGISTRY_MISMATCH")
    if value.get("allowed_membership_types") != ["MARKET", "THEME", "UNIVERSE"]:
        raise AssetMasterError("MEMBERSHIP_TYPE_REGISTRY_MISMATCH")
    if value.get("required_lineage_fields") != [
        "source_id",
        "source_url",
        "source_sha256",
        "available_at",
        "retrieved_at_utc",
    ]:
        raise AssetMasterError("LINEAGE_FIELDS_MISMATCH")
    if value.get("effective_interval") != "[valid_from, valid_to)":
        raise AssetMasterError("EFFECTIVE_INTERVAL_MISMATCH")
    if value.get("policy_status") != expected_policy:
        raise AssetMasterError("POLICY_BOUNDARY_MISMATCH")
    if value.get("authority") != expected_authority:
        raise AssetMasterError("AUTHORITY_BOUNDARY_MISMATCH")
    expected_coverage = {
        "kraken_public_api": "SOURCE_CAPABILITY_EXISTS",
        "krx_open_api_stock_daily": "SOURCE_CAPABILITY_EXISTS",
        "nasdaq_trader_symbol_directory": "SOURCE_CAPABILITY_EXISTS",
    }
    if value.get("source_coverage") != expected_coverage:
        raise AssetMasterError("SOURCE_COVERAGE_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(path))


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


def _valid_available_at(value) -> bool:
    return _valid_date(value) or _valid_utc(value)


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _validate_source(source: dict, contract: dict, context: str) -> dict:
    if not isinstance(source, dict):
        raise AssetMasterError(f"SOURCE_IDENTITY_NOT_OBJECT:{context}")
    required = contract["required_lineage_fields"]
    missing = [field for field in required if not source.get(field)]
    if missing:
        raise AssetMasterError(f"SOURCE_LINEAGE_INCOMPLETE:{context}:{','.join(missing)}")
    if source["source_id"] not in contract["source_coverage"]:
        raise AssetMasterError(f"SOURCE_ID_UNKNOWN:{context}:{source['source_id']}")
    parsed = urlparse(source["source_url"])
    if parsed.scheme != "https" or not parsed.netloc:
        raise AssetMasterError(f"SOURCE_URL_INVALID:{context}")
    if not isinstance(source["source_sha256"], str) or SHA256_RE.fullmatch(
        source["source_sha256"]
    ) is None:
        raise AssetMasterError(f"SOURCE_SHA256_INVALID:{context}")
    if not _valid_available_at(source["available_at"]):
        raise AssetMasterError(f"SOURCE_AVAILABLE_AT_INVALID:{context}")
    if not _valid_utc(source["retrieved_at_utc"]):
        raise AssetMasterError(f"SOURCE_RETRIEVED_AT_INVALID:{context}")
    if _valid_utc(source["available_at"]):
        available = dt.datetime.strptime(source["available_at"], "%Y-%m-%dT%H:%M:%SZ")
        retrieved = dt.datetime.strptime(
            source["retrieved_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        )
        if available > retrieved:
            raise AssetMasterError(f"SOURCE_TEMPORAL_ORDER_INVALID:{context}")
    else:
        retrieved = dt.datetime.strptime(
            source["retrieved_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        )
        if dt.date.fromisoformat(source["available_at"]) > retrieved.date():
            raise AssetMasterError(f"SOURCE_TEMPORAL_ORDER_INVALID:{context}")
    return copy.deepcopy(source)


def _validate_interval(row: dict, context: str) -> tuple[str, str | None]:
    start = row.get("valid_from")
    end = row.get("valid_to")
    if not _valid_date(start):
        raise AssetMasterError(f"VALID_FROM_INVALID:{context}")
    if end is not None and not _valid_date(end):
        raise AssetMasterError(f"VALID_TO_INVALID:{context}")
    if end is not None and end <= start:
        raise AssetMasterError(f"EFFECTIVE_INTERVAL_EMPTY:{context}")
    return start, end


def _active(start: str, end: str | None, as_of_date: str) -> bool:
    return start <= as_of_date and (end is None or as_of_date < end)


def _overlap(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def _validate_identifier(row: dict, asset_id: str) -> dict:
    if not isinstance(row, dict):
        raise AssetMasterError(f"IDENTIFIER_NOT_OBJECT:{asset_id}")
    namespace = row.get("namespace")
    value = row.get("value")
    if not isinstance(namespace, str) or NAMESPACE_RE.fullmatch(namespace) is None:
        raise AssetMasterError(f"IDENTIFIER_NAMESPACE_INVALID:{asset_id}")
    if not _nonempty(value) or len(value) > 160:
        raise AssetMasterError(f"IDENTIFIER_VALUE_INVALID:{asset_id}:{namespace}")
    return {"namespace": namespace, "value": value}


def _validate_alias(row: dict, asset_id: str, contract: dict) -> dict:
    if not isinstance(row, dict):
        raise AssetMasterError(f"ALIAS_NOT_OBJECT:{asset_id}")
    context = f"{asset_id}:alias"
    if row.get("alias_type") != "SYMBOL":
        raise AssetMasterError(f"ALIAS_TYPE_UNSUPPORTED:{asset_id}")
    value = row.get("value")
    exchange_id = row.get("exchange_id")
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise AssetMasterError(f"ALIAS_VALUE_INVALID:{asset_id}")
    if not isinstance(exchange_id, str) or TOKEN_RE.fullmatch(exchange_id) is None:
        raise AssetMasterError(f"ALIAS_EXCHANGE_INVALID:{asset_id}")
    start, end = _validate_interval(row, context)
    return {
        "alias_type": "SYMBOL",
        "value": value,
        "exchange_id": exchange_id,
        "valid_from": start,
        "valid_to": end,
        "source_identity": _validate_source(row.get("source_identity"), contract, context),
    }


def _validate_membership(row: dict, asset_id: str, contract: dict) -> dict:
    if not isinstance(row, dict):
        raise AssetMasterError(f"MEMBERSHIP_NOT_OBJECT:{asset_id}")
    membership_type = row.get("membership_type")
    if membership_type not in contract["allowed_membership_types"]:
        raise AssetMasterError(f"MEMBERSHIP_TYPE_INVALID:{asset_id}:{membership_type}")
    membership_id = row.get("membership_id")
    if not isinstance(membership_id, str) or TOKEN_RE.fullmatch(membership_id) is None:
        raise AssetMasterError(f"MEMBERSHIP_ID_INVALID:{asset_id}:{membership_type}")
    context = f"{asset_id}:membership:{membership_type}:{membership_id}"
    start, end = _validate_interval(row, context)
    return {
        "membership_type": membership_type,
        "membership_id": membership_id,
        "valid_from": start,
        "valid_to": end,
        "source_identity": _validate_source(row.get("source_identity"), contract, context),
    }


def _assert_no_overlaps(rows: list[dict], key_fields: tuple[str, ...], code: str) -> None:
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        groups.setdefault(key, []).append(row)
    for key, grouped in groups.items():
        ordered = sorted(grouped, key=lambda item: (item["valid_from"], item["valid_to"] or ""))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if _overlap(
                    left["valid_from"], left["valid_to"], right["valid_from"], right["valid_to"]
                ):
                    raise AssetMasterError(f"{code}:{':'.join(key)}")


def _validate_record(record: dict, as_of_date: str, contract: dict) -> dict:
    if not isinstance(record, dict):
        raise AssetMasterError("ASSET_RECORD_NOT_OBJECT")
    asset_id = record.get("asset_id")
    if not isinstance(asset_id, str) or ASSET_ID_RE.fullmatch(asset_id) is None:
        raise AssetMasterError(f"ASSET_ID_INVALID:{asset_id!r}")
    market = record.get("market")
    asset_class = record.get("asset_class")
    if market not in contract["allowed_markets"]:
        raise AssetMasterError(f"MARKET_INVALID:{asset_id}:{market}")
    if asset_class not in contract["allowed_asset_classes"]:
        raise AssetMasterError(f"ASSET_CLASS_INVALID:{asset_id}:{asset_class}")
    if (market == "CRYPTO") != (asset_class == "CRYPTO_ASSET"):
        raise AssetMasterError(f"MARKET_ASSET_CLASS_MISMATCH:{asset_id}")
    display_name = record.get("display_name")
    if not _nonempty(display_name):
        raise AssetMasterError(f"DISPLAY_NAME_INVALID:{asset_id}")
    primary_symbol = record.get("primary_symbol")
    exchange_id = record.get("exchange_id")
    quote_currency = record.get("quote_currency")
    for value, label in (
        (primary_symbol, "PRIMARY_SYMBOL"),
        (exchange_id, "EXCHANGE_ID"),
        (quote_currency, "QUOTE_CURRENCY"),
    ):
        if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
            raise AssetMasterError(f"{label}_INVALID:{asset_id}")

    identifiers_raw = record.get("identifiers")
    aliases_raw = record.get("aliases")
    memberships_raw = record.get("memberships")
    if not isinstance(identifiers_raw, list) or not identifiers_raw:
        raise AssetMasterError(f"IDENTIFIERS_EMPTY:{asset_id}")
    if not isinstance(aliases_raw, list) or not aliases_raw:
        raise AssetMasterError(f"ALIASES_EMPTY:{asset_id}")
    if not isinstance(memberships_raw, list) or not memberships_raw:
        raise AssetMasterError(f"MEMBERSHIPS_EMPTY:{asset_id}")

    identifiers = [_validate_identifier(row, asset_id) for row in identifiers_raw]
    if len({(row["namespace"], row["value"]) for row in identifiers}) != len(identifiers):
        raise AssetMasterError(f"IDENTIFIER_DUPLICATE:{asset_id}")
    aliases = [_validate_alias(row, asset_id, contract) for row in aliases_raw]
    memberships = [
        _validate_membership(row, asset_id, contract) for row in memberships_raw
    ]
    _assert_no_overlaps(
        aliases, ("alias_type", "value", "exchange_id"), "ALIAS_INTERVAL_OVERLAP"
    )
    _assert_no_overlaps(
        memberships,
        ("membership_type", "membership_id"),
        "MEMBERSHIP_INTERVAL_OVERLAP",
    )
    active_aliases = [
        row for row in aliases if _active(row["valid_from"], row["valid_to"], as_of_date)
    ]
    if not any(
        row["value"] == primary_symbol and row["exchange_id"] == exchange_id
        for row in active_aliases
    ):
        raise AssetMasterError(f"PRIMARY_ALIAS_NOT_ACTIVE:{asset_id}")
    active_memberships = [
        row
        for row in memberships
        if _active(row["valid_from"], row["valid_to"], as_of_date)
    ]
    if not any(
        row["membership_type"] == "MARKET" and row["membership_id"] == market
        for row in active_memberships
    ):
        raise AssetMasterError(f"MARKET_MEMBERSHIP_NOT_ACTIVE:{asset_id}")

    return {
        "asset_id": asset_id,
        "market": market,
        "asset_class": asset_class,
        "display_name": display_name,
        "primary_symbol": primary_symbol,
        "exchange_id": exchange_id,
        "quote_currency": quote_currency,
        "identifiers": sorted(identifiers, key=lambda row: (row["namespace"], row["value"])),
        "aliases": sorted(
            aliases,
            key=lambda row: (
                row["alias_type"],
                row["value"],
                row["exchange_id"],
                row["valid_from"],
            ),
        ),
        "memberships": sorted(
            memberships,
            key=lambda row: (
                row["membership_type"],
                row["membership_id"],
                row["valid_from"],
            ),
        ),
        "active_aliases": sorted(
            active_aliases, key=lambda row: (row["value"], row["exchange_id"])
        ),
        "active_memberships": sorted(
            active_memberships,
            key=lambda row: (row["membership_type"], row["membership_id"]),
        ),
        "source_identity": _validate_source(
            record.get("source_identity"), contract, f"{asset_id}:record"
        ),
        "universe_approved": False,
        "investable_eligible": False,
        "stage_transition": None,
    }


def _validate_cross_record_collisions(records: list[dict]) -> None:
    asset_ids: dict[str, str] = {}
    primary_keys: dict[tuple[str, str], str] = {}
    identifiers: dict[tuple[str, str], str] = {}
    aliases: list[dict] = []
    for record in records:
        asset_id = record["asset_id"]
        if asset_id in asset_ids:
            raise AssetMasterError(f"ASSET_ID_DUPLICATE:{asset_id}")
        asset_ids[asset_id] = asset_id
        primary_key = (record["exchange_id"], record["primary_symbol"])
        if primary_key in primary_keys:
            raise AssetMasterError(
                f"PRIMARY_IDENTITY_COLLISION:{primary_key[0]}:{primary_key[1]}"
            )
        primary_keys[primary_key] = asset_id
        for identifier in record["identifiers"]:
            key = (identifier["namespace"], identifier["value"])
            owner = identifiers.get(key)
            if owner is not None and owner != asset_id:
                raise AssetMasterError(
                    f"IDENTIFIER_COLLISION:{key[0]}:{key[1]}:{owner}:{asset_id}"
                )
            identifiers[key] = asset_id
        for alias in record["aliases"]:
            aliases.append({**alias, "asset_id": asset_id})

    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for alias in aliases:
        key = (alias["alias_type"], alias["value"], alias["exchange_id"])
        grouped.setdefault(key, []).append(alias)
    for key, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: (row["valid_from"], row["asset_id"]))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if left["asset_id"] == right["asset_id"]:
                    continue
                if _overlap(
                    left["valid_from"], left["valid_to"], right["valid_from"], right["valid_to"]
                ):
                    raise AssetMasterError(
                        "ALIAS_IDENTITY_COLLISION:"
                        + ":".join(key)
                        + f":{left['asset_id']}:{right['asset_id']}"
                    )


def build_master(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise AssetMasterError("INPUT_SCHEMA_MISMATCH")
    master_id = value.get("master_id")
    if not isinstance(master_id, str) or TOKEN_RE.fullmatch(master_id) is None:
        raise AssetMasterError("MASTER_ID_INVALID")
    as_of_date = value.get("as_of_date")
    if not _valid_date(as_of_date):
        raise AssetMasterError("AS_OF_DATE_INVALID")
    rows = value.get("records")
    if not isinstance(rows, list):
        raise AssetMasterError("ASSET_RECORDS_NOT_LIST")
    records = [_validate_record(row, as_of_date, contract) for row in rows]
    _validate_cross_record_collisions(records)
    records.sort(key=lambda row: row["asset_id"])

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "master_id": master_id,
        "as_of_date": as_of_date,
        "status": "IDENTITY_MASTER_VALIDATED",
        "record_count": len(records),
        "records": records,
        "source_coverage": copy.deepcopy(contract["source_coverage"]),
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "MARKET_UNIVERSE_POLICY_UNRATIFIED",
            "THEME_TAXONOMY_UNRATIFIED",
            "LIVE_MASTER_POPULATION_NOT_IMPLEMENTED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


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
    packet = build_master(_read_json(Path(input_path)), load_contract(Path(contract_path)))
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
    except AssetMasterError as exc:
        print(f"global asset master failed: {exc}")
        return 1
    print(
        f"global asset master: {packet['record_count']} records "
        f"as_of={packet['as_of_date']} sha256={packet['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
