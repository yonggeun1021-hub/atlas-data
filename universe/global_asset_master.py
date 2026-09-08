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
THEME_BINDING_SCHEMA_VERSION = "global_asset_master_theme_binding_report/1"

BINDING_REFERENCE_FIELDS = (
    "asset_id",
    "gam_membership_id",
    "taxonomy_membership_id",
    "evidence_id",
)

ASSET_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9$._:/-]{0,95}$")
NAMESPACE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,47}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# An authority boundary is only immutable when the caller pins a full object
# name.  A branch, tag, or relative revision such as HEAD moves under the
# check, so it is not accepted as a trusted commit.
TRUSTED_COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
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
    if value.get("theme_membership_provenance") != {
        "contract": "theme_taxonomy/2",
        "source_role": "DISCLOSURE_EVIDENCE",
        "registry": "config/theme_taxonomy_contract.json",
    }:
        raise AssetMasterError("THEME_PROVENANCE_CONTRACT_MISMATCH")
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


def _validate_theme_source(source, market: str, as_of_date: str, context: str) -> dict:
    # This is a distinct evidence role, not an alias of an identity provider.
    # Keep one canonical market/host/time validator and preserve literal inputs.
    taxonomy = _load_theme_taxonomy()
    try:
        theme_contract = taxonomy.load_contract()
        if market not in theme_contract["market_sources"]:
            raise AssetMasterError(f"THEME_SOURCE_MARKET_UNSUPPORTED:{context}:{market}")
        cutoff = dt.datetime.strptime(as_of_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59
        )
        return taxonomy._validate_source(source, market, cutoff, theme_contract, context)
    except taxonomy.ThemeTaxonomyError as exc:
        raise AssetMasterError(f"THEME_SOURCE_INVALID:{exc}") from exc


def _validate_membership(
    row: dict, asset_id: str, contract: dict, market: str, as_of_date: str,
) -> dict:
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
        "source_identity": (
            _validate_theme_source(row.get("source_identity"), market, as_of_date, context)
            if membership_type == "THEME"
            else _validate_source(row.get("source_identity"), contract, context)
        ),
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
        _validate_membership(row, asset_id, contract, market, as_of_date) for row in memberships_raw
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


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate a persisted Global Asset Master packet from retained evidence."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet_fields = {
        "schema_version",
        "contract_version",
        "master_id",
        "as_of_date",
        "status",
        "record_count",
        "records",
        "source_coverage",
        "policy_status",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != packet_fields:
        raise AssetMasterError("OUTPUT_FIELDS_MISMATCH")

    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise AssetMasterError("OUTPUT_SHA256_INVALID")
    payload = copy.deepcopy(packet)
    payload.pop("payload_sha256")
    if payload_sha256(payload) != digest:
        raise AssetMasterError("OUTPUT_SHA256_MISMATCH")

    if packet.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise AssetMasterError("OUTPUT_SCHEMA_MISMATCH")
    if packet.get("contract_version") != contract["contract_version"]:
        raise AssetMasterError("OUTPUT_CONTRACT_VERSION_MISMATCH")
    if packet.get("status") != "IDENTITY_MASTER_VALIDATED":
        raise AssetMasterError("OUTPUT_STATUS_MISMATCH")
    master_id = packet.get("master_id")
    if not isinstance(master_id, str) or TOKEN_RE.fullmatch(master_id) is None:
        raise AssetMasterError("OUTPUT_MASTER_ID_INVALID")
    as_of_date = packet.get("as_of_date")
    if not _valid_date(as_of_date):
        raise AssetMasterError("OUTPUT_AS_OF_DATE_INVALID")

    rows = packet.get("records")
    record_count = packet.get("record_count")
    if (
        not isinstance(rows, list)
        or type(record_count) is not int
        or record_count != len(rows)
    ):
        raise AssetMasterError("OUTPUT_RECORD_COUNT_MISMATCH")
    raw_record_fields = {
        "asset_id",
        "market",
        "asset_class",
        "display_name",
        "primary_symbol",
        "exchange_id",
        "quote_currency",
        "identifiers",
        "aliases",
        "memberships",
        "source_identity",
    }
    record_fields = raw_record_fields | {
        "active_aliases",
        "active_memberships",
        "universe_approved",
        "investable_eligible",
        "stage_transition",
    }
    validated = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != record_fields:
            raise AssetMasterError("OUTPUT_RECORD_FIELDS_MISMATCH")
        raw = {key: copy.deepcopy(row[key]) for key in raw_record_fields}
        expected = _validate_record(raw, as_of_date, contract)
        if row != expected:
            raise AssetMasterError(
                f"OUTPUT_RECORD_DERIVATION_MISMATCH:{expected['asset_id']}"
            )
        validated.append(expected)

    _validate_cross_record_collisions(validated)
    if [row["asset_id"] for row in validated] != sorted(
        row["asset_id"] for row in validated
    ):
        raise AssetMasterError("OUTPUT_RECORD_ORDER_MISMATCH")

    expected_boundaries = [
        "MARKET_UNIVERSE_POLICY_UNRATIFIED",
        "THEME_TAXONOMY_UNRATIFIED",
        "LIVE_MASTER_POPULATION_NOT_IMPLEMENTED",
    ]
    if packet.get("source_coverage") != contract["source_coverage"]:
        raise AssetMasterError("OUTPUT_SOURCE_COVERAGE_MISMATCH")
    if packet.get("policy_status") != contract["policy_status"]:
        raise AssetMasterError("OUTPUT_POLICY_STATUS_MISMATCH")
    if packet.get("authority") != contract["authority"]:
        raise AssetMasterError("OUTPUT_AUTHORITY_MISMATCH")
    if packet.get("unresolved_boundaries") != expected_boundaries:
        raise AssetMasterError("OUTPUT_UNRESOLVED_BOUNDARIES_MISMATCH")
    return copy.deepcopy(packet)


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
    return validate_packet(packet, contract)


def _load_theme_taxonomy():
    """Import the merged ThemeTaxonomy/2 producer only when binding is checked.

    The identity builder and the CLI keep their existing import surface: a
    caller that never asks for a binding check never loads the rotation module.
    """
    try:
        from rotation import theme_taxonomy as TT
    except ModuleNotFoundError:  # module executed by path, repository not on sys.path
        import sys

        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from rotation import theme_taxonomy as TT
    return TT


def _validated_master(source: dict, contract: dict) -> dict:
    """Re-derive the caller's master through the production validators only."""
    if not isinstance(source, dict):
        raise AssetMasterError("MASTER_SOURCE_NOT_OBJECT")
    schema_version = source.get("schema_version")
    if schema_version == OUTPUT_SCHEMA_VERSION:
        return validate_packet(copy.deepcopy(source), contract)
    if schema_version == INPUT_SCHEMA_VERSION:
        return build_master(copy.deepcopy(source), contract)
    raise AssetMasterError(f"MASTER_SOURCE_SCHEMA_UNKNOWN:{schema_version!r}")


def _theme_binding_reference(value, index: int) -> dict:
    if not isinstance(value, dict) or set(value) != set(BINDING_REFERENCE_FIELDS):
        raise AssetMasterError(f"BINDING_REFERENCE_FIELDS_MISMATCH:{index}")
    reference = {}
    for field in BINDING_REFERENCE_FIELDS:
        if not _nonempty(value[field]):
            raise AssetMasterError(f"BINDING_REFERENCE_VALUE_INVALID:{index}:{field}")
        reference[field] = value[field]
    return reference


def _shared_source_registry(contract: dict, taxonomy_contract: dict) -> set:
    """Source IDs that both contracts admit, so equality is contract-defined."""
    taxonomy_sources = {
        source_id
        for allowed in taxonomy_contract["market_sources"].values()
        for source_id in allowed
    }
    # The GAM contract delegates only THEME provenance to this canonical
    # registry. Identity/MARKET/UNIVERSE coverage stays independent.
    gam_theme_contract = _load_theme_taxonomy().load_contract()
    gam_theme_sources = {
        source_id for allowed in gam_theme_contract["market_sources"].values()
        for source_id in allowed
    }
    return gam_theme_sources & taxonomy_sources


def validate_theme_source_binding(
    master_source: dict,
    taxonomy_source: dict,
    bindings: list,
    contract: dict | None = None,
    taxonomy_contract: dict | None = None,
    authority_registry_path=None,
    trusted_commit: str | None = None,
) -> dict:
    """Check explicit caller bindings between THEME memberships and a taxonomy.

    Read-only pre-ingestion check.  ``master_source`` is an original Global
    Asset Master input or packet, ``taxonomy_source`` is an original Theme
    graph input document, and ``bindings`` are explicit caller references
    naming an asset, a GAM THEME membership, a taxonomy membership, and one of
    that membership's evidence rows.

    Both sides are re-derived here with their own production validators: the
    master through :func:`build_master` / :func:`validate_packet`, and the graph
    through ``theme_taxonomy.build_packet`` against the committed authority
    registry at an immutable ``trusted_commit``.  That commit is a required
    caller input: this check never falls back to the working tree's current
    ``HEAD``, because the authority boundary a binding is judged against must be
    named by the caller and must not move.  No status, digest, or authority
    claim carried inside either caller document is trusted, nothing is inferred
    from symbols or names, nothing is mutated, and a verified binding never
    populates the master or opens investability.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if trusted_commit is None:
        raise AssetMasterError("BINDING_TRUSTED_COMMIT_REQUIRED")
    if (
        not isinstance(trusted_commit, str)
        or TRUSTED_COMMIT_RE.fullmatch(trusted_commit) is None
    ):
        raise AssetMasterError(f"BINDING_TRUSTED_COMMIT_INVALID:{trusted_commit!r}")
    if not isinstance(bindings, list):
        raise AssetMasterError("BINDINGS_NOT_LIST")
    if not bindings:
        raise AssetMasterError("BINDINGS_EMPTY")
    references = [
        _theme_binding_reference(value, index) for index, value in enumerate(bindings)
    ]

    master = _validated_master(master_source, contract)
    taxonomy_module = _load_theme_taxonomy()
    registry_kwargs = (
        {}
        if authority_registry_path is None
        else {"authority_registry_path": Path(authority_registry_path)}
    )
    try:
        taxonomy = taxonomy_module.build_packet(
            copy.deepcopy(taxonomy_source),
            copy.deepcopy(taxonomy_contract) if taxonomy_contract is not None else None,
            trusted_commit=trusted_commit,
            **registry_kwargs,
        )
    except taxonomy_module.ThemeTaxonomyError as exc:
        raise AssetMasterError(f"TAXONOMY_SOURCE_INVALID:{exc}") from exc
    taxonomy_contract = (
        taxonomy_module.load_contract()
        if taxonomy_contract is None
        else copy.deepcopy(taxonomy_contract)
    )

    as_of_date = master["as_of_date"]
    taxonomy_as_of = taxonomy["as_of_date"]
    records = {row["asset_id"]: row for row in master["records"]}
    taxonomy_memberships = {row["membership_id"]: row for row in taxonomy["memberships"]}
    adapter_ids = {
        row["membership_id"]
        for row in taxonomy["global_asset_master_membership_adapter"]
    }
    authority_status = taxonomy["authority_resolution"]["status"]
    authorized = taxonomy["theme_membership_authorized"] is True
    shared_sources = _shared_source_registry(contract, taxonomy_contract)
    intervals_comparable = (
        contract["effective_interval"] == taxonomy_contract["effective_interval"]
    )

    common_failures = []
    common_unresolved = []
    if not authorized:
        # Both halves are reported: an authorized registry record still cannot
        # activate a graph whose own effective slice is not a ratified claim.
        common_failures.append(
            "TAXONOMY_SOURCE_NOT_AUTHORIZED:"
            f"{taxonomy['graph_status']}:{authority_status}"
        )
    if taxonomy_as_of != as_of_date:
        common_failures.append(f"AS_OF_DATE_MISMATCH:{as_of_date}:{taxonomy_as_of}")
    if not intervals_comparable:
        common_unresolved.append(
            "EFFECTIVE_INTERVAL_SEMANTICS_UNCOMPARABLE:"
            f"{contract['effective_interval']}:{taxonomy_contract['effective_interval']}"
        )

    results = []
    for reference in references:
        failures = list(common_failures)
        unresolved = list(common_unresolved)

        record = records.get(reference["asset_id"])
        if record is None:
            failures.append(f"GAM_ASSET_NOT_FOUND:{reference['asset_id']}")
        membership = None
        if record is not None:
            candidates = [
                row
                for row in record["memberships"]
                if row["membership_type"] == "THEME"
                and row["membership_id"] == reference["gam_membership_id"]
            ]
            if not candidates:
                failures.append(
                    f"GAM_THEME_MEMBERSHIP_NOT_FOUND:{reference['gam_membership_id']}"
                )
            elif len(candidates) > 1:
                # Non-overlapping history rows may repeat one membership ID.
                # The caller reference does not name an interval, so no winner
                # is chosen here.
                failures.append(
                    f"GAM_THEME_MEMBERSHIP_AMBIGUOUS:{reference['gam_membership_id']}"
                )
            else:
                membership = candidates[0]

        theme_membership = taxonomy_memberships.get(reference["taxonomy_membership_id"])
        if theme_membership is None:
            failures.append(
                f"TAXONOMY_MEMBERSHIP_NOT_FOUND:{reference['taxonomy_membership_id']}"
            )
        evidence = None
        if theme_membership is not None:
            matches = [
                row
                for row in theme_membership["evidence"]
                if row["evidence_id"] == reference["evidence_id"]
            ]
            if not matches:
                failures.append(
                    f"TAXONOMY_EVIDENCE_NOT_FOUND:{reference['evidence_id']}"
                )
            else:
                evidence = matches[0]

        if record is not None and theme_membership is not None:
            if record["asset_id"] != theme_membership["asset_id"]:
                failures.append(
                    f"ASSET_ID_MISMATCH:{record['asset_id']}:{theme_membership['asset_id']}"
                )
            if record["market"] != theme_membership["market"]:
                failures.append(
                    f"MARKET_MISMATCH:{record['market']}:{theme_membership['market']}"
                )

        if membership is not None and theme_membership is not None:
            theme_id = theme_membership["theme_id"]
            if TOKEN_RE.fullmatch(theme_id) is None:
                # A taxonomy theme ID that this contract cannot even express as
                # a membership ID has no defined comparison, and none is
                # invented for it.
                unresolved.append(f"THEME_IDENTITY_COMPARISON_UNDEFINED:{theme_id}")
            elif membership["membership_id"] != theme_id:
                failures.append(
                    f"THEME_IDENTITY_MISMATCH:{membership['membership_id']}:{theme_id}"
                )
            if intervals_comparable and (
                membership["valid_from"] != theme_membership["valid_from"]
                or membership["valid_to"] != theme_membership["valid_to"]
            ):
                failures.append(
                    "EFFECTIVE_INTERVAL_MISMATCH:"
                    f"{membership['valid_from']}:{membership['valid_to']}:"
                    f"{theme_membership['valid_from']}:{theme_membership['valid_to']}"
                )
            if not _active(membership["valid_from"], membership["valid_to"], as_of_date):
                failures.append(f"GAM_MEMBERSHIP_NOT_ACTIVE:{as_of_date}")
            if not _active(
                theme_membership["valid_from"],
                theme_membership["valid_to"],
                taxonomy_as_of,
            ):
                failures.append(f"TAXONOMY_MEMBERSHIP_NOT_ACTIVE:{taxonomy_as_of}")
            if authorized and theme_membership["membership_id"] not in adapter_ids:
                failures.append(
                    "TAXONOMY_MEMBERSHIP_NOT_IN_AUTHORIZED_ADAPTER:"
                    f"{theme_membership['membership_id']}"
                )

        source_id_comparison = "NOT_EVALUATED"
        if membership is not None and evidence is not None:
            master_identity = membership["source_identity"]
            evidence_identity = evidence["source_identity"]
            for field in ("source_url", "source_sha256"):
                if master_identity[field] != evidence_identity[field]:
                    failures.append(f"SOURCE_EVIDENCE_MISMATCH:{field}")
            if (
                master_identity["source_id"] in shared_sources
                and evidence_identity["source_id"] in shared_sources
            ):
                source_id_comparison = "COMPARED"
                if master_identity["source_id"] != evidence_identity["source_id"]:
                    failures.append("SOURCE_EVIDENCE_MISMATCH:source_id")
            else:
                # Never convert a provider label if a supplied taxonomy contract
                # cannot define the same literal THEME provenance comparison.
                source_id_comparison = "UNCOMPARABLE_DISJOINT_SOURCE_REGISTRY"
                unresolved.append(
                    "SOURCE_ID_COMPARISON_UNDEFINED:"
                    f"{master_identity['source_id']}:{evidence_identity['source_id']}"
                )

        master_reference = None
        if membership is not None:
            master_reference = {
                "asset_id": record["asset_id"],
                "market": record["market"],
                "membership_type": membership["membership_type"],
                "membership_id": membership["membership_id"],
                "valid_from": membership["valid_from"],
                "valid_to": membership["valid_to"],
                "source_identity": copy.deepcopy(membership["source_identity"]),
            }
        taxonomy_reference = None
        if theme_membership is not None:
            taxonomy_reference = {
                "membership_id": theme_membership["membership_id"],
                "asset_id": theme_membership["asset_id"],
                "market": theme_membership["market"],
                "theme_id": theme_membership["theme_id"],
                "role_id": theme_membership["role_id"],
                "valid_from": theme_membership["valid_from"],
                "valid_to": theme_membership["valid_to"],
                "evidence_ids": [
                    row["evidence_id"] for row in theme_membership["evidence"]
                ],
                "evidence": copy.deepcopy(evidence),
            }

        failures = sorted(set(failures))
        unresolved = sorted(set(unresolved))
        # Positive verification additionally requires that the source-identity
        # comparison was actually performed against a shared registry.  An
        # undefined or skipped comparison is never treated as agreement.
        results.append(
            {
                **reference,
                "verified": (
                    not failures
                    and not unresolved
                    and source_id_comparison == "COMPARED"
                ),
                "failure_reasons": failures,
                "unresolved_reasons": unresolved,
                "source_id_comparison": source_id_comparison,
                "master_reference": master_reference,
                "taxonomy_reference": taxonomy_reference,
            }
        )

    verified_count = sum(1 for row in results if row["verified"])
    if verified_count == len(results):
        status = "THEME_SOURCE_BINDING_VERIFIED"
    elif any(row["failure_reasons"] for row in results):
        status = "THEME_SOURCE_BINDING_NOT_VERIFIED"
    else:
        status = "THEME_SOURCE_BINDING_UNRESOLVED"

    report = {
        "schema_version": THEME_BINDING_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "taxonomy_contract_version": taxonomy_contract["contract_version"],
        "master_id": master["master_id"],
        "taxonomy_id": taxonomy["taxonomy_id"],
        "as_of_date": as_of_date,
        "taxonomy_as_of_date": taxonomy_as_of,
        "status": status,
        "binding_count": len(results),
        "verified_binding_count": verified_count,
        "bindings": results,
        "master_payload_sha256": master["payload_sha256"],
        "taxonomy_payload_sha256": taxonomy["payload_sha256"],
        "taxonomy_graph_status": taxonomy["graph_status"],
        "taxonomy_authority_resolution": copy.deepcopy(
            taxonomy["authority_resolution"]
        ),
        "comparison_basis": {
            "effective_interval": contract["effective_interval"],
            "compared_fields": [
                "asset_id",
                "market",
                "theme_identity",
                "valid_from",
                "valid_to",
                "source_url",
                "source_sha256",
                "source_id",
            ],
            "preserved_uncompared_fields": {
                "audit_provenance": "GAM_MEMBERSHIP_HAS_NO_PROVENANCE_FIELD",
                "available_at": "RETRIEVAL_CLOCK_IS_NOT_DOCUMENT_IDENTITY",
                "claim_text": "GAM_MEMBERSHIP_HAS_NO_CLAIM_FIELD",
                "retrieved_at_utc": "RETRIEVAL_CLOCK_IS_NOT_DOCUMENT_IDENTITY",
                "role_id": "GAM_MEMBERSHIP_HAS_NO_ROLE_FIELD",
            },
            "shared_source_registry": sorted(shared_sources),
        },
        "master_population_authorized": False,
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "MARKET_UNIVERSE_POLICY_UNRATIFIED",
            "THEME_TAXONOMY_UNRATIFIED",
            "LIVE_MASTER_POPULATION_NOT_IMPLEMENTED",
            "THEME_MEMBERSHIP_INGESTION_NOT_IMPLEMENTED",
        ],
    }
    report["payload_sha256"] = payload_sha256(report)
    return report


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
