#!/usr/bin/env python3
"""P3-02 forward-only Nasdaq directory → Global Asset Master adapter.

The adapter verifies exact bytes for both official Nasdaq Trader Symbol
Directory files and represents every exact source row as one source-date
coverage membership. It does not merge source identities, infer an exchange
MIC, filter security types, approve investability, reconstruct history, or
acquire paid data.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universe import global_asset_master as GAM  # noqa: E402


_SOURCE_SPEC = importlib.util.spec_from_file_location(
    "us_breadth_for_global_universe",
    ROOT / ".github" / "scripts" / "us_breadth_forward.py",
)
US_BREADTH = importlib.util.module_from_spec(_SOURCE_SPEC)
assert _SOURCE_SPEC.loader is not None
_SOURCE_SPEC.loader.exec_module(US_BREADTH)


CONTRACT_PATH = ROOT / "config" / "us_global_universe_contract.json"
CONTRACT_SCHEMA_VERSION = 1
INPUT_SCHEMA_VERSION = "us_global_universe_input/1"
OUTPUT_SCHEMA_VERSION = "us_global_universe_packet/1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class UsUniverseError(ValueError):
    """Fail-closed US forward-universe adapter violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsUniverseError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_policy = {
        "source_coverage_membership": "IMPLEMENTED",
        "listing_policy": "UNRATIFIED",
        "delisting_policy": "UNRATIFIED",
        "security_type_policy": "UNRATIFIED",
        "liquidity_policy": "UNRATIFIED",
        "tradability_policy": "UNRATIFIED",
        "investable_universe_policy": "UNRATIFIED",
        "source_hierarchy": "UNRATIFIED",
    }
    expected_authority = {
        "source_coverage_universe_only": True,
        "cross_source_identity_merge_authorized": False,
        "exchange_MIC_inference_authorized": False,
        "security_type_filter_authorized": False,
        "liquidity_filter_authorized": False,
        "tradability_filter_authorized": False,
        "investable_universe_authorized": False,
        "historical_reconstruction_authorized": False,
        "stage_promotion_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
        "paid_data_acquisition_authorized": False,
    }
    expected = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contract_version": "us_global_universe_adapter/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "asset_master_contract_version": "global_asset_master/1",
        "source_contract": {
            "path": "config/us_breadth_forward_contract.json",
            "schema_version": 1,
            "approval_status": "FORWARD_ONLY_RATIFIED",
            "universe_semantics": (
                "source_directory_membership_not_investable_universe"
            ),
        },
        "required_sources": ["nasdaq_listed", "other_listed"],
        "source_id": "nasdaq_trader_symbol_directory",
        "market": "US",
        "quote_currency": "USD",
        "asset_class": "EQUITY",
        "raw_input_encoding": "base64_exact_response_bytes",
        "identity_semantics": (
            "source_name_plus_exact_primary_symbol_no_cross_source_merge"
        ),
        "asset_id_semantics": (
            "US:NASDAQDIR:plus_first_24_upper_sha256_hex_of_source_name_NUL_symbol"
        ),
        "exchange_id_semantics": (
            "NASDAQ_TRADER_plus_exact_source_exchange_code_no_MIC_inference"
        ),
        "membership_semantics": (
            "exact_source_date_forward_coverage_not_investable"
        ),
        "effective_interval": "[source_date, next_calendar_date)",
        "source_attribute_policy": (
            "preserve_exact_directory_fields_without_eligibility_interpretation"
        ),
        "policy_status": expected_policy,
        "authority": expected_authority,
        "paid_data_boundary": (
            "INHERIT_SOURCE_CONTRACT_USER_RECONFIRMATION_REQUIRED"
        ),
    }
    if not isinstance(value, dict):
        raise UsUniverseError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise UsUniverseError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise UsUniverseError("CONTRACT_FIELDS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _load_source_contract(contract: dict) -> dict:
    path = ROOT / contract["source_contract"]["path"]
    try:
        source_contract = US_BREADTH.load_contract(path)
    except US_BREADTH.ContractError as exc:
        raise UsUniverseError(f"SOURCE_CONTRACT_INVALID:{exc}") from exc
    expected = contract["source_contract"]
    if source_contract.get("schema_version") != expected["schema_version"]:
        raise UsUniverseError("SOURCE_CONTRACT_SCHEMA_MISMATCH")
    if source_contract.get("approval_status") != expected["approval_status"]:
        raise UsUniverseError("SOURCE_CONTRACT_APPROVAL_MISMATCH")
    if source_contract.get("universe_semantics") != expected["universe_semantics"]:
        raise UsUniverseError("SOURCE_CONTRACT_SEMANTICS_MISMATCH")
    if source_contract.get("paid_data_checkpoint", {}).get("status") != (
        "USER_RECONFIRMATION_REQUIRED"
    ) or source_contract["paid_data_checkpoint"].get("approved") is not False:
        raise UsUniverseError("PAID_DATA_BOUNDARY_MISSING")
    return source_contract


def _utc(value: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise UsUniverseError("UTC_INVALID")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise UsUniverseError("UTC_INVALID") from exc


def _next_date(value: str) -> str:
    return (dt.date.fromisoformat(value) + dt.timedelta(days=1)).isoformat()


def _source_definitions(source_contract: dict) -> dict:
    return {source["name"]: source for source in source_contract["sources"]}


def _decode_body(snapshot: dict, source_name: str) -> bytes:
    encoded = snapshot.get("response_body_base64")
    if not isinstance(encoded, str) or not encoded:
        raise UsUniverseError(f"RESPONSE_BODY_BASE64_INVALID:{source_name}")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UsUniverseError(f"RESPONSE_BODY_BASE64_INVALID:{source_name}") from exc


def _validate_source_identity(
    value: dict,
    body: bytes,
    source: dict,
    source_date: str,
    as_of_utc: dt.datetime,
    contract: dict,
) -> dict:
    source_name = source["name"]
    if not isinstance(value, dict) or set(value) != {
        "source_id",
        "source_url",
        "source_sha256",
        "available_at",
        "retrieved_at_utc",
    }:
        raise UsUniverseError(f"SOURCE_IDENTITY_FIELDS_MISMATCH:{source_name}")
    if value.get("source_id") != contract["source_id"]:
        raise UsUniverseError(f"SOURCE_ID_MISMATCH:{source_name}")
    parsed = urlparse(str(value.get("source_url") or ""))
    expected = urlparse(source["endpoint"])
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected.hostname
        or parsed.netloc != expected.netloc
        or parsed.path != expected.path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise UsUniverseError(f"SOURCE_URL_MISMATCH:{source_name}")
    digest = value.get("source_sha256")
    if (
        not isinstance(digest, str)
        or SHA256_RE.fullmatch(digest) is None
        or digest != hashlib.sha256(body).hexdigest()
    ):
        raise UsUniverseError(f"SOURCE_SHA256_MISMATCH:{source_name}")
    if value.get("available_at") != source_date:
        raise UsUniverseError(f"SOURCE_AVAILABLE_AT_MISMATCH:{source_name}")
    retrieved = _utc(value.get("retrieved_at_utc"))
    if retrieved > as_of_utc or retrieved.date() < dt.date.fromisoformat(source_date):
        raise UsUniverseError(f"SOURCE_TEMPORAL_ORDER_INVALID:{source_name}")
    return copy.deepcopy(value)


def _asset_id(source_name: str, symbol: str) -> str:
    digest = hashlib.sha256(f"{source_name}\0{symbol}".encode("utf-8")).hexdigest()
    return "US:NASDAQDIR:" + digest[:24].upper()


def _exchange_id(source_name: str, row: dict, source: dict) -> str:
    code = "NASDAQ" if source["exchange_field"] is None else row[source["exchange_field"]]
    if not code or not re.fullmatch(r"[A-Z0-9._-]+", code):
        raise UsUniverseError(f"SOURCE_EXCHANGE_CODE_INVALID:{source_name}:{code!r}")
    return f"NASDAQ_TRADER:{code}"


def _identifiers(source_name: str, symbol: str, row: dict) -> list[dict]:
    prefix = source_name.upper()
    identifiers = [
        {
            "namespace": f"NASDAQ_TRADER_{prefix}_ROW",
            "value": f"{source_name}:{symbol}",
        },
        {
            "namespace": f"NASDAQ_TRADER_{prefix}_SYMBOL",
            "value": symbol,
        },
    ]
    if source_name == "other_listed":
        for namespace, field in (
            ("NASDAQ_TRADER_OTHER_CQS_SYMBOL", "CQS Symbol"),
            ("NASDAQ_TRADER_OTHER_NASDAQ_SYMBOL", "NASDAQ Symbol"),
        ):
            value = row[field]
            if value:
                identifiers.append({"namespace": namespace, "value": value})
    return identifiers


def _to_master_record(
    source_name: str,
    source: dict,
    row: dict,
    source_date: str,
    valid_to: str,
    source_identity: dict,
    contract: dict,
) -> tuple[dict, dict]:
    symbol = row[source["identity_field"]]
    exchange_id = _exchange_id(source_name, row, source)
    asset_id = _asset_id(source_name, symbol)
    security_name = row["Security Name"]
    if not security_name:
        raise UsUniverseError(f"SECURITY_NAME_EMPTY:{source_name}:{symbol}")
    interval = {
        "valid_from": source_date,
        "valid_to": valid_to,
        "source_identity": copy.deepcopy(source_identity),
    }
    record = {
        "asset_id": asset_id,
        "market": contract["market"],
        "asset_class": contract["asset_class"],
        "display_name": security_name,
        "primary_symbol": symbol,
        "exchange_id": exchange_id,
        "quote_currency": contract["quote_currency"],
        "identifiers": _identifiers(source_name, symbol, row),
        "aliases": [
            {
                "alias_type": "SYMBOL",
                "value": symbol,
                "exchange_id": exchange_id,
                **copy.deepcopy(interval),
            }
        ],
        "memberships": [
            {
                "membership_type": "MARKET",
                "membership_id": contract["market"],
                **copy.deepcopy(interval),
            },
            {
                "membership_type": "UNIVERSE",
                "membership_id": f"NASDAQ_TRADER_{source_name.upper()}",
                **copy.deepcopy(interval),
            },
        ],
        "source_identity": copy.deepcopy(source_identity),
    }
    attributes = {
        "asset_id": asset_id,
        "source_name": source_name,
        "primary_symbol": symbol,
        "exchange_code_raw": (
            None if source["exchange_field"] is None else row[source["exchange_field"]]
        ),
        "fields": {key: row[key] for key in source["required_fields"]},
        "eligibility_interpretation": None,
        "liquidity_observation": None,
        "tradability_decision": None,
        "investable_eligible": False,
    }
    return record, attributes


def build_packet(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    source_contract = _load_source_contract(contract)
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise UsUniverseError("INPUT_SCHEMA_MISMATCH")
    if set(value) != {
        "schema_version",
        "master_id",
        "as_of_date",
        "as_of_utc",
        "snapshots",
    }:
        raise UsUniverseError("INPUT_FIELDS_MISMATCH")
    master_id = value.get("master_id")
    source_date = value.get("as_of_date")
    if not isinstance(master_id, str) or not master_id:
        raise UsUniverseError("MASTER_ID_INVALID")
    if not GAM._valid_date(source_date):
        raise UsUniverseError("AS_OF_DATE_INVALID")
    as_of_utc = _utc(value.get("as_of_utc"))
    if as_of_utc.date() < dt.date.fromisoformat(source_date):
        raise UsUniverseError("AS_OF_UTC_BEFORE_SOURCE_DATE")
    snapshots = value.get("snapshots")
    if not isinstance(snapshots, list):
        raise UsUniverseError("SNAPSHOTS_NOT_LIST")

    definitions = _source_definitions(source_contract)
    required_sources = contract["required_sources"]
    by_source = {}
    records = []
    attributes = []
    source_snapshots = []
    valid_to = _next_date(source_date)
    symbols = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "source_name",
            "response_body_base64",
            "source_identity",
        }:
            raise UsUniverseError("SNAPSHOT_FIELDS_MISMATCH")
        source_name = snapshot.get("source_name")
        if source_name not in definitions or source_name not in required_sources:
            raise UsUniverseError(f"SNAPSHOT_SOURCE_INVALID:{source_name}")
        if source_name in by_source:
            raise UsUniverseError(f"SNAPSHOT_SOURCE_DUPLICATE:{source_name}")
        source = definitions[source_name]
        body = _decode_body(snapshot, source_name)
        try:
            parsed = US_BREADTH.parse_source(body, source)
        except US_BREADTH.ContractError as exc:
            raise UsUniverseError(f"SOURCE_BODY_INVALID:{source_name}:{exc}") from exc
        if parsed["source_date"].isoformat() != source_date:
            raise UsUniverseError(f"SOURCE_DATE_MISMATCH:{source_name}")
        source_identity = _validate_source_identity(
            snapshot.get("source_identity"),
            body,
            source,
            source_date,
            as_of_utc,
            contract,
        )
        for row in parsed["rows"]:
            symbol = row[source["identity_field"]]
            owner = symbols.get(symbol)
            if owner is not None and owner != source_name:
                raise UsUniverseError(
                    f"CROSS_SOURCE_SYMBOL_COLLISION:{symbol}:{owner}:{source_name}"
                )
            symbols[symbol] = source_name
            record, attribute_row = _to_master_record(
                source_name,
                source,
                row,
                source_date,
                valid_to,
                source_identity,
                contract,
            )
            records.append(record)
            attributes.append(attribute_row)
        by_source[source_name] = len(parsed["rows"])
        source_snapshots.append(
            {
                "source_name": source_name,
                "source_sha256": source_identity["source_sha256"],
                "available_at": source_identity["available_at"],
                "retrieved_at_utc": source_identity["retrieved_at_utc"],
                "source_file_creation_time_raw": parsed["creation_time_raw"],
                "record_count": len(parsed["rows"]),
            }
        )

    missing = sorted(set(required_sources) - set(by_source))
    if missing:
        raise UsUniverseError(f"REQUIRED_SOURCE_MISSING:{','.join(missing)}")
    try:
        master = GAM.build_master(
            {
                "schema_version": GAM.INPUT_SCHEMA_VERSION,
                "master_id": master_id,
                "as_of_date": source_date,
                "records": records,
            }
        )
    except GAM.AssetMasterError as exc:
        raise UsUniverseError(f"GLOBAL_ASSET_MASTER_INVALID:{exc}") from exc

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "as_of_date": source_date,
        "as_of_utc": value["as_of_utc"],
        "status": "FORWARD_SOURCE_COVERAGE_UNIVERSE_VALIDATED",
        "membership_semantics": contract["membership_semantics"],
        "identity_semantics": contract["identity_semantics"],
        "exchange_id_semantics": contract["exchange_id_semantics"],
        "effective_interval": {"valid_from": source_date, "valid_to": valid_to},
        "source_counts": {key: by_source[key] for key in sorted(by_source)},
        "total_count": sum(by_source.values()),
        "source_snapshots": sorted(
            source_snapshots, key=lambda item: item["source_name"]
        ),
        "source_attribute_rows": sorted(
            attributes, key=lambda item: (item["source_name"], item["primary_symbol"])
        ),
        "asset_master": master,
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "paid_data_checkpoint": copy.deepcopy(
            source_contract["paid_data_checkpoint"]
        ),
        "unresolved_boundaries": [
            "LISTING_POLICY_UNRATIFIED",
            "DELISTING_POLICY_UNRATIFIED",
            "SECURITY_TYPE_POLICY_UNRATIFIED",
            "LIQUIDITY_POLICY_UNRATIFIED",
            "TRADABILITY_POLICY_UNRATIFIED",
            "INVESTABLE_UNIVERSE_POLICY_UNRATIFIED",
            "SOURCE_HIERARCHY_UNRATIFIED",
            "HISTORICAL_RECONSTRUCTION_REQUIRES_USER_RECONFIRMATION",
            "DELISTED_OHLCV_REQUIRES_USER_RECONFIRMATION",
            "SCHEDULED_MASTER_POPULATION_NOT_IMPLEMENTED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def run(input_path: Path, output_path: Path, contract_path: Path = CONTRACT_PATH) -> dict:
    packet = build_packet(_read_json(Path(input_path)), load_contract(Path(contract_path)))
    GAM.write_json_atomic(Path(output_path), packet)
    return packet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)
    try:
        packet = run(args.input, args.out, args.contract)
    except UsUniverseError as exc:
        print(f"US global universe failed: {exc}")
        return 1
    print(
        f"US global universe: {packet['source_counts']} total={packet['total_count']} "
        f"as_of={packet['as_of_date']} sha256={packet['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
