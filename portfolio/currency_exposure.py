#!/usr/bin/env python3
"""P7-07 raw quote-currency exposure aggregation without FX policy."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "currency_exposure_contract.json"
INPUT_SCHEMA_VERSION = "currency_position_snapshot/1"
OUTPUT_SCHEMA_VERSION = "currency_exposure_packet/1"
ASSET_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,95}$")
CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,15}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class CurrencyExposureError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CurrencyExposureError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "currency_exposure/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "upstream_asset_master_schema_version": "global_asset_master_packet/1",
        "upstream_asset_master_contract_version": "global_asset_master/1",
        "upstream_asset_master_status": "IDENTITY_MASTER_VALIDATED",
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "position_side_policy": "LONG_ONLY_CURRENT_BOUNDARY",
        "aggregation_policy": "QUOTE_CURRENCY_ONLY_NO_CROSS_CURRENCY_TOTAL",
        "fx_conversion_policy": "NOT_AUTHORIZED",
        "reporting_currency_policy": "UNRATIFIED",
        "exposure_limit_policy": "UNRATIFIED",
        "decimal_policy": "CANONICAL_NON_EXPONENT_STRING",
        "upstream_asset_master_authority": {
            "identity_recording_only": True,
            "automatic_identity_merge_authorized": False,
            "theme_inference_authorized": False,
            "universe_approval_authorized": False,
            "investability_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "input_authority": {
            "position_observation_only": True,
            "short_position_authorized": False,
            "fx_conversion_authorized": False,
            "limit_evaluation_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "raw_currency_exposure_aggregation_only": True,
            "fx_conversion_authorized": False,
            "cross_currency_total_authorized": False,
            "exposure_limit_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CurrencyExposureError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CurrencyExposureError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise CurrencyExposureError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CurrencyExposureError(code) from exc
    if parsed.isoformat() != value:
        raise CurrencyExposureError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CurrencyExposureError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CurrencyExposureError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CurrencyExposureError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CurrencyExposureError(code)
    return value


def _token(value, pattern, code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CurrencyExposureError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CurrencyExposureError(code)
    return value


def _decimal(value, code: str, positive: bool = False) -> tuple[str, Decimal]:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise CurrencyExposureError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CurrencyExposureError(code) from exc
    canonical = format(parsed, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    if canonical != value or (positive and parsed <= 0):
        raise CurrencyExposureError(code)
    return canonical, parsed


def _decimal_text(value: Decimal) -> str:
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def _validate_asset_master(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "master_id", "as_of_date",
        "status", "record_count", "records", "source_coverage", "policy_status",
        "authority", "unresolved_boundaries", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CurrencyExposureError("ASSET_MASTER_FIELDS_MISMATCH")
    digest = _sha(value.get("payload_sha256"), "ASSET_MASTER_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("payload_sha256")
    if payload_sha256(payload) != digest:
        raise CurrencyExposureError("ASSET_MASTER_SHA_MISMATCH")
    if (
        value.get("schema_version") != contract["upstream_asset_master_schema_version"]
        or value.get("contract_version")
        != contract["upstream_asset_master_contract_version"]
        or value.get("status") != contract["upstream_asset_master_status"]
        or value.get("authority") != contract["upstream_asset_master_authority"]
    ):
        raise CurrencyExposureError("ASSET_MASTER_IDENTITY_INVALID")
    _text(value.get("master_id"), "ASSET_MASTER_ID_INVALID")
    as_of_date = _date(value.get("as_of_date"), "ASSET_MASTER_AS_OF_INVALID")
    rows = value.get("records")
    if (
        not isinstance(rows, list)
        or type(value.get("record_count")) is not int
        or len(rows) != value["record_count"]
    ):
        raise CurrencyExposureError("ASSET_MASTER_RECORD_COUNT_INVALID")
    required_record_fields = {
        "asset_id", "market", "asset_class", "display_name", "primary_symbol",
        "exchange_id", "quote_currency", "identifiers", "aliases", "memberships",
        "active_aliases", "active_memberships", "source_identity",
        "universe_approved", "investable_eligible", "stage_transition",
    }
    records = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != required_record_fields:
            raise CurrencyExposureError("ASSET_MASTER_RECORD_FIELDS_MISMATCH")
        asset_id = _token(row.get("asset_id"), ASSET_ID_RE, "ASSET_ID_INVALID")
        market = row.get("market")
        currency = row.get("quote_currency")
        if market not in contract["allowed_markets"]:
            raise CurrencyExposureError(f"ASSET_MARKET_INVALID:{asset_id}:{market}")
        _token(currency, CURRENCY_RE, f"QUOTE_CURRENCY_INVALID:{asset_id}")
        if (
            row.get("universe_approved") is not False
            or row.get("investable_eligible") is not False
            or row.get("stage_transition") is not None
        ):
            raise CurrencyExposureError(f"ASSET_AUTHORITY_EXPANSION:{asset_id}")
        if asset_id in records:
            raise CurrencyExposureError(f"ASSET_ID_DUPLICATE:{asset_id}")
        records[asset_id] = {
            "asset_id": asset_id,
            "market": market,
            "quote_currency": currency,
            "display_name": _text(
                row.get("display_name"), f"ASSET_DISPLAY_NAME_INVALID:{asset_id}"
            ),
        }
    if list(records) != sorted(records):
        raise CurrencyExposureError("ASSET_MASTER_RECORD_ORDER_INVALID")
    return {
        "payload_sha256": digest,
        "master_id": value["master_id"],
        "as_of_date": as_of_date,
        "records": records,
    }


def _validate_position(row: dict, assets: dict, snapshot_available: str) -> dict:
    fields = {
        "account_id", "position_id", "asset_id", "quantity", "price",
        "price_quote_currency", "price_as_of", "price_source_ref",
        "price_source_sha256", "position_record_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise CurrencyExposureError("POSITION_FIELDS_MISMATCH")
    account_id = _token(row.get("account_id"), TOKEN_RE, "ACCOUNT_ID_INVALID")
    position_id = _token(row.get("position_id"), TOKEN_RE, "POSITION_ID_INVALID")
    asset_id = _token(row.get("asset_id"), ASSET_ID_RE, "POSITION_ASSET_ID_INVALID")
    asset = assets.get(asset_id)
    if asset is None:
        raise CurrencyExposureError(f"POSITION_ASSET_UNKNOWN:{asset_id}")
    currency = _token(
        row.get("price_quote_currency"),
        CURRENCY_RE,
        f"POSITION_CURRENCY_INVALID:{asset_id}",
    )
    if currency != asset["quote_currency"]:
        raise CurrencyExposureError(f"POSITION_QUOTE_CURRENCY_MISMATCH:{asset_id}")
    quantity_text, quantity = _decimal(
        row.get("quantity"), f"POSITION_QUANTITY_INVALID:{asset_id}", positive=True
    )
    price_text, price = _decimal(
        row.get("price"), f"POSITION_PRICE_INVALID:{asset_id}", positive=True
    )
    price_as_of = _utc(row.get("price_as_of"), f"POSITION_PRICE_AS_OF_INVALID:{asset_id}")
    if price_as_of > snapshot_available:
        raise CurrencyExposureError(f"POSITION_PRICE_FROM_FUTURE:{asset_id}")
    return {
        "account_id": account_id,
        "position_id": position_id,
        "asset_id": asset_id,
        "market": asset["market"],
        "display_name": asset["display_name"],
        "quantity": quantity_text,
        "price": price_text,
        "price_quote_currency": currency,
        "raw_notional": _decimal_text(quantity * price),
        "price_as_of": price_as_of,
        "price_source_ref": _text(
            row.get("price_source_ref"), f"POSITION_PRICE_SOURCE_REF_INVALID:{asset_id}"
        ),
        "price_source_sha256": _sha(
            row.get("price_source_sha256"), f"POSITION_PRICE_SOURCE_SHA_INVALID:{asset_id}"
        ),
        "position_record_sha256": _sha(
            row.get("position_record_sha256"), f"POSITION_RECORD_SHA_INVALID:{asset_id}"
        ),
    }


def _validate_snapshot(value: dict, master: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "snapshot_id", "as_of_date",
        "available_at", "positions", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CurrencyExposureError("POSITION_SNAPSHOT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise CurrencyExposureError("POSITION_SNAPSHOT_IDENTITY_INVALID")
    snapshot_id = _text(value.get("snapshot_id"), "POSITION_SNAPSHOT_ID_INVALID")
    as_of_date = _date(value.get("as_of_date"), "POSITION_SNAPSHOT_AS_OF_INVALID")
    if as_of_date != master["as_of_date"]:
        raise CurrencyExposureError("POSITION_ASSET_MASTER_DATE_MISMATCH")
    available_at = _utc(value.get("available_at"), "POSITION_SNAPSHOT_AVAILABLE_INVALID")
    if available_at[:10] < as_of_date:
        raise CurrencyExposureError("POSITION_SNAPSHOT_AVAILABLE_BEFORE_AS_OF")
    raw_rows = value.get("positions")
    if not isinstance(raw_rows, list):
        raise CurrencyExposureError("POSITIONS_NOT_LIST")
    rows = sorted(
        (
            _validate_position(row, master["records"], available_at)
            for row in raw_rows
        ),
        key=lambda row: (row["account_id"], row["position_id"]),
    )
    identities = [(row["account_id"], row["position_id"]) for row in rows]
    if len(identities) != len(set(identities)):
        raise CurrencyExposureError("POSITION_ID_DUPLICATE")
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "snapshot_id": snapshot_id,
        "as_of_date": as_of_date,
        "available_at": available_at,
        "positions": [
            {
                key: row[key]
                for key in (
                    "account_id", "position_id", "asset_id", "quantity", "price",
                    "price_quote_currency", "price_as_of", "price_source_ref",
                    "price_source_sha256", "position_record_sha256",
                )
            }
            for row in rows
        ],
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "POSITION_SNAPSHOT_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise CurrencyExposureError("POSITION_SNAPSHOT_SHA_MISMATCH")
    return {
        "snapshot_id": snapshot_id,
        "as_of_date": as_of_date,
        "available_at": available_at,
        "packet_sha256": digest,
        "positions": rows,
    }


def build_packet(
    asset_master: dict,
    position_snapshot: dict,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    master = _validate_asset_master(asset_master, contract)
    snapshot = _validate_snapshot(position_snapshot, master, contract)
    currency_totals: dict[str, Decimal] = {}
    currency_markets: dict[str, set[str]] = {}
    currency_positions: dict[str, int] = {}
    for row in snapshot["positions"]:
        currency = row["price_quote_currency"]
        currency_totals[currency] = currency_totals.get(currency, Decimal(0)) + Decimal(
            row["raw_notional"]
        )
        currency_markets.setdefault(currency, set()).add(row["market"])
        currency_positions[currency] = currency_positions.get(currency, 0) + 1
    exposures = [
        {
            "quote_currency": currency,
            "raw_gross_notional": _decimal_text(currency_totals[currency]),
            "position_count": currency_positions[currency],
            "markets": sorted(currency_markets[currency]),
            "fx_conversion_status": "NOT_AUTHORIZED",
            "limit_status": "UNRATIFIED",
            "limit_value": None,
            "breach": None,
        }
        for currency in sorted(currency_totals)
    ]
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "RAW_QUOTE_CURRENCY_EXPOSURE_ONLY",
        "as_of_date": snapshot["as_of_date"],
        "available_at": snapshot["available_at"],
        "snapshot_id": snapshot["snapshot_id"],
        "summary": {
            "position_count": len(snapshot["positions"]),
            "quote_currency_count": len(exposures),
            "cross_currency_total": None,
            "reporting_currency": None,
            "reporting_currency_status": "UNRATIFIED",
        },
        "positions": copy.deepcopy(snapshot["positions"]),
        "quote_currency_exposures": exposures,
        "lineage": {
            "asset_master_id": master["master_id"],
            "asset_master_payload_sha256": master["payload_sha256"],
            "position_snapshot_sha256": snapshot["packet_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REPORTING_CURRENCY_UNRATIFIED",
            "FX_RATE_SOURCE_UNRATIFIED",
            "FX_CONVERSION_NOT_AUTHORIZED",
            "CURRENCY_EXPOSURE_LIMITS_UNRATIFIED",
            "POSITION_SIZING_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise CurrencyExposureError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(asset_master_path: Path, snapshot_path: Path, output_path: Path) -> int:
    try:
        packet = build_packet(_read_json(asset_master_path), _read_json(snapshot_path))
        write_json_atomic(output_path, packet)
        return 0
    except (CurrencyExposureError, OSError, TypeError, ValueError) as exc:
        print(f"Currency exposure failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate raw quote-currency exposure without FX conversion"
    )
    parser.add_argument("asset_master", type=Path)
    parser.add_argument("position_snapshot", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.asset_master, args.position_snapshot, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
