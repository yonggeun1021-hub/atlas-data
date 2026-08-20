#!/usr/bin/env python3
"""P3-03 exact-date KOSPI/KOSDAQ source-coverage master adapter.

The adapter verifies exact KRX response bytes for both stock-market endpoints,
then represents every returned ISU_CD as a one-trading-date membership in the
P3-01 Global Asset Master.  It does not approve investability or apply listing,
liquidity, tradability, theme, ranking, or stage-promotion policy.
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
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universe import global_asset_master as GAM  # noqa: E402


_BREADTH_SPEC = importlib.util.spec_from_file_location(
    "korea_breadth_for_global_universe",
    ROOT / ".github" / "scripts" / "korea_breadth.py",
)
KOREA_BREADTH = importlib.util.module_from_spec(_BREADTH_SPEC)
assert _BREADTH_SPEC.loader is not None
_BREADTH_SPEC.loader.exec_module(KOREA_BREADTH)


CONTRACT_PATH = ROOT / "config" / "krx_global_universe_contract.json"
CONTRACT_SCHEMA_VERSION = 1
INPUT_SCHEMA_VERSION = "krx_global_universe_input/1"
OUTPUT_SCHEMA_VERSION = "krx_global_universe_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class KrxUniverseError(ValueError):
    """Fail-closed KRX universe adapter violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KrxUniverseError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_policy = {
        "source_coverage_membership": "IMPLEMENTED",
        "investable_universe_policy": "UNRATIFIED",
        "liquidity_policy": "UNRATIFIED",
        "tradability_policy": "UNRATIFIED",
        "listing_delisting_policy": "UNRATIFIED",
        "theme_taxonomy": "UNRATIFIED",
    }
    expected_authority = {
        "source_coverage_universe_only": True,
        "investable_universe_authorized": False,
        "liquidity_filter_authorized": False,
        "tradability_filter_authorized": False,
        "theme_inference_authorized": False,
        "stage_promotion_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    exact = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "contract_version": "krx_global_universe_adapter/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "asset_master_contract_version": "global_asset_master/1",
        "source_contract": {
            "path": "config/korea_breadth_contract.json",
            "schema_version": 1,
            "source_name": "KRX_OPEN_API_STOCK_DAILY",
            "universe_semantics": (
                "exact_date_official_response_rows_source_coverage_not_investable"
            ),
            "identity_semantics": "KRX_ISU_CD_exact_no_name_or_ticker_inference",
        },
        "required_markets": ["KOSDAQ", "KOSPI"],
        "source_id": "krx_open_api_stock_daily",
        "exchange_id": "XKRX",
        "quote_currency": "KRW",
        "asset_class": "EQUITY",
        "identity_field": "ISU_CD",
        "display_name_field": "ISU_NM",
        "market_field": "MKT_NM",
        "raw_input_encoding": "base64_exact_response_bytes",
        "membership_semantics": "exact_trading_date_source_coverage_not_investable",
        "effective_interval": "[trading_date, next_calendar_date)",
        "policy_status": expected_policy,
        "authority": expected_authority,
    }
    if not isinstance(value, dict):
        raise KrxUniverseError("CONTRACT_NOT_OBJECT")
    for key, expected in exact.items():
        if value.get(key) != expected:
            raise KrxUniverseError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(exact):
        raise KrxUniverseError("CONTRACT_FIELDS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _load_source_contract(contract: dict) -> dict:
    dependency = contract["source_contract"]
    source = KOREA_BREADTH.load_contract(ROOT / dependency["path"])
    for key in (
        "schema_version",
        "source_name",
        "universe_semantics",
        "identity_semantics",
    ):
        if source.get(key) != dependency[key]:
            raise KrxUniverseError(f"SOURCE_CONTRACT_MISMATCH:{key}")
    if source.get("identity_field") != contract["identity_field"]:
        raise KrxUniverseError("SOURCE_CONTRACT_MISMATCH:identity_field")
    return source


def _decode_body(snapshot: dict, market: str) -> bytes:
    encoded = snapshot.get("response_body_base64")
    if not isinstance(encoded, str) or not encoded:
        raise KrxUniverseError(f"RESPONSE_BODY_BASE64_INVALID:{market}")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KrxUniverseError(f"RESPONSE_BODY_BASE64_INVALID:{market}") from exc


def _validate_source_identity(
    source: dict,
    body: bytes,
    market: str,
    expected_date: str,
    contract: dict,
    source_contract: dict,
) -> dict:
    if not isinstance(source, dict):
        raise KrxUniverseError(f"SOURCE_IDENTITY_NOT_OBJECT:{market}")
    if source.get("source_id") != contract["source_id"]:
        raise KrxUniverseError(f"SOURCE_ID_MISMATCH:{market}")
    digest = hashlib.sha256(body).hexdigest()
    if not isinstance(source.get("source_sha256"), str) or SHA256_RE.fullmatch(
        source["source_sha256"]
    ) is None:
        raise KrxUniverseError(f"SOURCE_SHA256_INVALID:{market}")
    if source["source_sha256"] != digest:
        raise KrxUniverseError(f"SOURCE_SHA256_MISMATCH:{market}")
    parsed = urlparse(str(source.get("source_url") or ""))
    expected_url = urlparse(source_contract["market_endpoints"][market.lower()])
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme != "https"
        or parsed.netloc != expected_url.netloc
        or parsed.path != expected_url.path
        or query != {"basDd": [expected_date]}
    ):
        raise KrxUniverseError(f"SOURCE_URL_MISMATCH:{market}")
    try:
        return GAM._validate_source(source, GAM.load_contract(), f"KRX:{market}")
    except GAM.AssetMasterError as exc:
        raise KrxUniverseError(f"SOURCE_LINEAGE_INVALID:{market}:{exc}") from exc


def _decode_json_body(body: bytes, market: str) -> dict:
    try:
        text = body.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KrxUniverseError(f"KRX_RESPONSE_INVALID_JSON:{market}") from exc
    if not isinstance(value, dict):
        raise KrxUniverseError(f"KRX_RESPONSE_ROOT_NOT_OBJECT:{market}")
    return value


def _next_date(value: str) -> str:
    return (dt.date.fromisoformat(value) + dt.timedelta(days=1)).isoformat()


def _to_master_record(
    row: dict,
    market: str,
    trading_date: str,
    valid_to: str,
    source_identity: dict,
    contract: dict,
) -> dict:
    identity = str(row[contract["identity_field"]]).strip()
    name = str(row[contract["display_name_field"]]).strip()
    asset_id = f"KR:XKRX:{identity}"
    interval = {
        "valid_from": trading_date,
        "valid_to": valid_to,
        "source_identity": copy.deepcopy(source_identity),
    }
    return {
        "asset_id": asset_id,
        "market": "KOREA",
        "asset_class": contract["asset_class"],
        "display_name": name,
        "primary_symbol": identity,
        "exchange_id": contract["exchange_id"],
        "quote_currency": contract["quote_currency"],
        "identifiers": [
            {"namespace": "KRX_ISU_CD", "value": identity},
        ],
        "aliases": [
            {
                "alias_type": "SYMBOL",
                "value": identity,
                "exchange_id": contract["exchange_id"],
                **copy.deepcopy(interval),
            }
        ],
        "memberships": [
            {
                "membership_type": "MARKET",
                "membership_id": "KOREA",
                **copy.deepcopy(interval),
            },
            {
                "membership_type": "UNIVERSE",
                "membership_id": market,
                **copy.deepcopy(interval),
            },
        ],
        "source_identity": copy.deepcopy(source_identity),
    }


def build_packet(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    source_contract = _load_source_contract(contract)
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise KrxUniverseError("INPUT_SCHEMA_MISMATCH")
    master_id = value.get("master_id")
    as_of_date = value.get("as_of_date")
    if not isinstance(master_id, str) or not master_id:
        raise KrxUniverseError("MASTER_ID_INVALID")
    if not GAM._valid_date(as_of_date):
        raise KrxUniverseError("AS_OF_DATE_INVALID")
    snapshots = value.get("snapshots")
    if not isinstance(snapshots, list):
        raise KrxUniverseError("SNAPSHOTS_NOT_LIST")

    expected_date = as_of_date.replace("-", "")
    by_market = {}
    records = []
    source_snapshots = []
    valid_to = _next_date(as_of_date)
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise KrxUniverseError("SNAPSHOT_NOT_OBJECT")
        market = snapshot.get("market")
        if market not in contract["required_markets"]:
            raise KrxUniverseError(f"SNAPSHOT_MARKET_INVALID:{market}")
        if market in by_market:
            raise KrxUniverseError(f"SNAPSHOT_MARKET_DUPLICATE:{market}")
        body = _decode_body(snapshot, market)
        source_identity = _validate_source_identity(
            snapshot.get("source_identity"),
            body,
            market,
            expected_date,
            contract,
            source_contract,
        )
        payload = _decode_json_body(body, market)
        try:
            validated = KOREA_BREADTH.validate_snapshot(
                payload, expected_date, market.lower(), contract=source_contract
            )
        except KOREA_BREADTH.BreadthError as exc:
            raise KrxUniverseError(f"KRX_SNAPSHOT_INVALID:{market}:{exc}") from exc
        rows = payload[source_contract["response_block"]]
        for row in rows:
            if str(row[contract["market_field"]]).strip().upper() != market:
                raise KrxUniverseError(
                    f"ROW_MARKET_MISMATCH:{market}:{row[contract['market_field']]}"
                )
            records.append(
                _to_master_record(
                    row,
                    market,
                    as_of_date,
                    valid_to,
                    source_identity,
                    contract,
                )
            )
        by_market[market] = validated["universe_count"]
        source_snapshots.append(
            {
                "market": market,
                "source_sha256": source_identity["source_sha256"],
                "available_at": source_identity["available_at"],
                "retrieved_at_utc": source_identity["retrieved_at_utc"],
                "universe_count": validated["universe_count"],
            }
        )

    missing = sorted(set(contract["required_markets"]) - set(by_market))
    if missing:
        raise KrxUniverseError(f"REQUIRED_MARKET_MISSING:{','.join(missing)}")

    try:
        master = GAM.build_master(
            {
                "schema_version": GAM.INPUT_SCHEMA_VERSION,
                "master_id": master_id,
                "as_of_date": as_of_date,
                "records": records,
            }
        )
    except GAM.AssetMasterError as exc:
        raise KrxUniverseError(f"GLOBAL_ASSET_MASTER_INVALID:{exc}") from exc

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "as_of_date": as_of_date,
        "status": "SOURCE_COVERAGE_UNIVERSE_VALIDATED",
        "membership_semantics": contract["membership_semantics"],
        "effective_interval": {
            "valid_from": as_of_date,
            "valid_to": valid_to,
        },
        "market_counts": {market: by_market[market] for market in sorted(by_market)},
        "total_count": sum(by_market.values()),
        "source_snapshots": sorted(source_snapshots, key=lambda item: item["market"]),
        "asset_master": master,
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "INVESTABLE_UNIVERSE_POLICY_UNRATIFIED",
            "LIQUIDITY_POLICY_UNRATIFIED",
            "TRADABILITY_POLICY_UNRATIFIED",
            "LISTING_DELISTING_POLICY_UNRATIFIED",
            "THEME_TAXONOMY_UNRATIFIED",
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
    except KrxUniverseError as exc:
        print(f"KRX global universe failed: {exc}")
        return 1
    print(
        f"KRX global universe: {packet['market_counts']} total={packet['total_count']} "
        f"as_of={packet['as_of_date']} sha256={packet['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
