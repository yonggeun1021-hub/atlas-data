#!/usr/bin/env python3
"""Validate Kraken BTC/USD PIT captures and build a 200DMA trend factor.

The source contract states that the final OHLC row is the current,
not-yet-committed bucket.  This transform always removes that row and uses
exactly 200 contiguous finalized UTC daily closes.  It never calls Kraken,
writes a tracked output by default, or authorizes a Regime/trading decision.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "btc_price_contract.json"
UTC = dt.timezone.utc
SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$")
CAPTURE_VERSION = re.compile(r"^btc-price-capture/v[1-9][0-9]*$")


class TrendError(RuntimeError):
    """Fail-closed BTC source or transform contract violation."""


def fail(code: str, detail: str) -> None:
    raise TrendError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", str(exc))

    expected = {
        "schema_version",
        "source_name",
        "endpoint",
        "request_pair",
        "response_pair",
        "asset_version",
        "interval_minutes",
        "market_timezone",
        "quote_currency",
        "capture_mode",
        "raw_file",
        "minimum_finalized_candles",
        "maximum_documented_finalized_candles",
        "current_candle_policy",
        "close_semantics",
        "gap_policy",
        "candle_fields",
        "transform_version",
    }
    if set(contract) != expected or contract.get("schema_version") != 1:
        fail("CONTRACT_INVALID", "schema or fields")
    if contract.get("source_name") != "kraken_spot_ohlc":
        fail("CONTRACT_INVALID", "source_name")
    if contract.get("endpoint") != (
        "https://api.kraken.com/0/public/OHLC?"
        "pair=XBTUSD&interval=1440&assetVersion=1"
    ):
        fail("CONTRACT_INVALID", "endpoint")
    if (
        contract.get("request_pair") != "XBTUSD"
        or contract.get("response_pair") != "BTC/USD"
        or contract.get("asset_version") != 1
        or contract.get("interval_minutes") != 1440
        or contract.get("market_timezone") != "UTC"
        or contract.get("quote_currency") != "USD"
    ):
        fail("CONTRACT_INVALID", "pair or UTC daily semantics")
    if contract.get("capture_mode") != "direct_fetch_append_only":
        fail("CONTRACT_INVALID", "capture_mode")
    if contract.get("raw_file") != "kraken_ohlc_xbtusd.json.gz":
        fail("CONTRACT_INVALID", "raw_file")
    if contract.get("minimum_finalized_candles") != 200:
        fail("CONTRACT_INVALID", "minimum_finalized_candles")
    if contract.get("maximum_documented_finalized_candles") != 720:
        fail("CONTRACT_INVALID", "maximum_documented_finalized_candles")
    if contract.get("current_candle_policy") != "exclude_last_row_always":
        fail("CONTRACT_INVALID", "current_candle_policy")
    if contract.get("close_semantics") != (
        "last_trade_in_finalized_utc_daily_bucket"
    ):
        fail("CONTRACT_INVALID", "close_semantics")
    if contract.get("gap_policy") != "exact_contiguous_utc_calendar_days":
        fail("CONTRACT_INVALID", "gap_policy")
    if contract.get("candle_fields") != [
        "time",
        "open",
        "high",
        "low",
        "close",
        "vwap",
        "volume",
        "trade_count",
    ]:
        fail("CONTRACT_INVALID", "candle_fields")
    if contract.get("transform_version") != "btc_trend/v1":
        fail("CONTRACT_INVALID", "transform_version")
    return contract


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


def checksum(snapshot_dir: Path, expected_name: str) -> str:
    try:
        lines = (Path(snapshot_dir) / "_sha256.txt").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as exc:
        fail("CHECKSUM_FILE_INVALID", str(exc))
    if len(lines) != 1:
        fail("CHECKSUM_FILE_INVALID", "exactly one line required")
    match = SHA_LINE.fullmatch(lines[0])
    if match is None or match.group(2) != expected_name:
        fail("CHECKSUM_FILE_INVALID", lines[0] if lines else "empty")
    return match.group(1)


def read_raw(snapshot_dir: Path, raw_file: str) -> tuple[bytes, object]:
    try:
        with gzip.open(Path(snapshot_dir) / raw_file, "rb") as stream:
            raw = stream.read()
    except (OSError, EOFError) as exc:
        fail("RAW_RESPONSE_INVALID", str(exc))
    try:
        payload = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, InvalidOperation) as exc:
        fail("RAW_RESPONSE_INVALID", str(exc))
    return raw, payload


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


def normalize_candle(row: object, index: int) -> dict:
    if not isinstance(row, list) or len(row) != 8:
        fail("CANDLE_SHAPE_INVALID", f"row {index}")
    date_value = candle_date(row[0], f"row {index}")
    open_price = decimal_field(row[1], f"row {index} open", False)
    high = decimal_field(row[2], f"row {index} high", False)
    low = decimal_field(row[3], f"row {index} low", False)
    close = decimal_field(row[4], f"row {index} close", False)
    vwap = decimal_field(row[5], f"row {index} vwap", False)
    volume = decimal_field(row[6], f"row {index} volume", True)
    trades = row[7]
    if type(trades) is not int or trades < 0:
        fail("CANDLE_VALUE_INVALID", f"row {index} trade_count")
    if (
        high < low
        or high < open_price
        or high < close
        or low > open_price
        or low > close
        or vwap < low
        or vwap > high
    ):
        fail("CANDLE_OHLC_INVALID", f"row {index}")
    return {
        "date": date_value,
        "timestamp": row[0],
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "vwap": vwap,
        "volume": volume,
        "trade_count": trades,
    }


def normalized_series(payload: object, vintage: dt.date, contract: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("error") != []:
        error = payload.get("error") if isinstance(payload, dict) else None
        fail("SOURCE_ERROR", str(error))
    result = payload.get("result")
    if not isinstance(result, dict):
        fail("PAYLOAD_SHAPE_INVALID", "result")
    pair = contract["response_pair"]
    if set(result) != {pair, "last"} or type(result.get("last")) is not int:
        fail("PAYLOAD_SHAPE_INVALID", "result keys or cursor")
    rows = result[pair]
    if not isinstance(rows, list):
        fail("PAYLOAD_SHAPE_INVALID", pair)
    if len(rows) < contract["minimum_finalized_candles"] + 1:
        fail("INSUFFICIENT_FINALIZED_HISTORY", str(max(len(rows) - 1, 0)))

    candles = [normalize_candle(row, i) for i, row in enumerate(rows)]
    dates = [item["date"] for item in candles]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        fail("CANDLE_ORDER_OR_DUPLICATE", "dates")

    current = candles[-1]
    if current["date"] != vintage:
        fail(
            "CURRENT_CANDLE_DATE_MISMATCH",
            f"{current['date'].isoformat()} != {vintage.isoformat()}",
        )
    finalized = candles[:-1]
    if len(finalized) > contract["maximum_documented_finalized_candles"]:
        fail("FINALIZED_HISTORY_EXCEEDS_SOURCE_BOUND", str(len(finalized)))
    if any(item["date"] >= vintage for item in finalized):
        fail("FINALIZED_BOUNDARY_INVALID", vintage.isoformat())
    expected_latest = vintage - dt.timedelta(days=1)
    if finalized[-1]["date"] != expected_latest:
        fail(
            "MISSING_LATEST_FINALIZED_DAY",
            f"expected {expected_latest} got {finalized[-1]['date']}",
        )

    window_size = contract["minimum_finalized_candles"]
    window = finalized[-window_size:]
    for before, after in zip(window, window[1:]):
        if after["date"] - before["date"] != dt.timedelta(days=1):
            fail(
                "MISSING_EXACT_DAILY_CANDLE",
                f"{before['date']} -> {after['date']}",
            )
    return {
        "all": candles,
        "finalized": finalized,
        "current_excluded": current,
        "window": window,
    }


def snapshot_core(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    snapshot_dir = Path(snapshot_dir)
    vintage = snapshot_date(snapshot_dir)
    fetched = downloaded_at(snapshot_dir, vintage)
    raw_name = Path(contract["raw_file"]).name.removesuffix(".gz")
    expected_sha = checksum(snapshot_dir, raw_name)
    raw, payload = read_raw(snapshot_dir, contract["raw_file"])
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != expected_sha:
        fail("CHECKSUM_MISMATCH", raw_name)
    series = normalized_series(payload, vintage, contract)
    return {
        "snapshot_date": vintage.isoformat(),
        "fetched_at_utc": fetched,
        "response_sha256": actual_sha,
        "byte_length": len(raw),
        "raw_file": contract["raw_file"],
        "response_rows": len(series["all"]),
        "finalized_rows": len(series["finalized"]),
        "latest_finalized_day": series["finalized"][-1]["date"].isoformat(),
        "current_excluded_day": series["current_excluded"]["date"].isoformat(),
        "series": series,
    }


def manifest_payload(core: dict, contract: dict, capture_version: str) -> dict:
    if not CAPTURE_VERSION.fullmatch(capture_version):
        fail("CAPTURE_VERSION_INVALID", capture_version)
    return {
        "schema_version": 1,
        "capture_version": capture_version,
        "snapshot_date": core["snapshot_date"],
        "fetched_at_utc": core["fetched_at_utc"],
        "source": {
            "name": contract["source_name"],
            "endpoint": contract["endpoint"],
            "request_pair": contract["request_pair"],
            "response_pair": contract["response_pair"],
            "interval_minutes": contract["interval_minutes"],
            "market_timezone": contract["market_timezone"],
            "close_semantics": contract["close_semantics"],
            "current_candle_policy": contract["current_candle_policy"],
        },
        "raw": {
            "file": core["raw_file"],
            "response_sha256": core["response_sha256"],
            "byte_length": core["byte_length"],
            "response_rows": core["response_rows"],
            "finalized_rows": core["finalized_rows"],
            "latest_finalized_day": core["latest_finalized_day"],
            "current_excluded_day": core["current_excluded_day"],
        },
    }


def build_manifest(
    snapshot_dir: Path,
    capture_version: str,
    contract: Optional[dict] = None,
) -> Path:
    contract = load_contract() if contract is None else contract
    snapshot_dir = Path(snapshot_dir)
    target = snapshot_dir / "_manifest.json"
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    core = snapshot_core(snapshot_dir, contract)
    payload = manifest_payload(core, contract, capture_version)
    return write_output(payload, target)


def validate_manifest(
    snapshot_dir: Path,
    core: dict,
    contract: dict,
) -> dict:
    path = Path(snapshot_dir) / "_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("MANIFEST_INVALID", str(exc))
    expected = manifest_payload(core, contract, manifest.get("capture_version", ""))
    if manifest != expected:
        fail("MANIFEST_MISMATCH", str(path))
    return manifest


def validate_snapshot(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    core = snapshot_core(snapshot_dir, contract)
    manifest = validate_manifest(snapshot_dir, core, contract)
    return {
        key: value
        for key, value in core.items()
        if key != "series"
    } | {"capture_version": manifest["capture_version"]}


def decimal_text(value: Decimal) -> str:
    if value == 0:
        value = Decimal(0)
    return format(value, "f")


def build_transform(
    snapshot_dir: Path,
    contract: Optional[dict] = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    core = snapshot_core(snapshot_dir, contract)
    manifest = validate_manifest(snapshot_dir, core, contract)
    window = core["series"]["window"]
    latest = window[-1]
    dma = sum((item["close"] for item in window), Decimal(0)) / Decimal(
        len(window)
    )
    if latest["close"] > dma:
        direction = "ABOVE_200DMA"
    elif latest["close"] < dma:
        direction = "BELOW_200DMA"
    else:
        direction = "AT_200DMA"

    return {
        "schema_version": 1,
        "transform_version": contract["transform_version"],
        "market": "CRYPTO",
        "asset": "BTC",
        "quote_currency": contract["quote_currency"],
        "market_timezone": contract["market_timezone"],
        "measurement": "btc_close_vs_200dma",
        "status": "AVAILABLE",
        "direction": direction,
        "latest_finalized_day": latest["date"].isoformat(),
        "latest_finalized_close": decimal_text(latest["close"]),
        "dma_200": decimal_text(dma),
        "window": {
            "count": len(window),
            "start": window[0]["date"].isoformat(),
            "end": window[-1]["date"].isoformat(),
            "history_basis": "finalized_utc_daily_close_only",
            "gap_policy": contract["gap_policy"],
            "missing_data": "UNKNOWN_FAIL_CLOSED_NO_FILL",
        },
        "current_candle": {
            "date": core["series"]["current_excluded"]["date"].isoformat(),
            "excluded": True,
            "reason": "source_documents_not_yet_committed_timeframe",
        },
        "lineage": {
            "pit_status": "qualified_direct_capture",
            "vintage_date": core["snapshot_date"],
            "available_at": core["fetched_at_utc"],
            "source_name": contract["source_name"],
            "source_endpoint": contract["endpoint"],
            "source_pair": contract["response_pair"],
            "source_sha256": core["response_sha256"],
            "capture_version": manifest["capture_version"],
            "close_semantics": contract["close_semantics"],
            "current_candle_policy": contract["current_candle_policy"],
            "evidence_grade": "A_DIRECT_FETCH",
            "source_type": "primary_exchange_market_data",
        },
        "regime_score_authorized": False,
        "threshold_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def write_output(payload: dict, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest")
    manifest.add_argument("--snapshot-dir", type=Path, required=True)
    manifest.add_argument("--capture-version", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("snapshot_dir", type=Path)

    transform = sub.add_parser("transform")
    transform.add_argument("snapshot_dir", type=Path)
    transform.add_argument("--out", type=Path)

    args = parser.parse_args(argv)
    if args.command == "manifest":
        path = build_manifest(args.snapshot_dir, args.capture_version)
        print(path)
        return 0
    if args.command == "validate":
        print(json.dumps(validate_snapshot(args.snapshot_dir), sort_keys=True))
        return 0

    payload = build_transform(args.snapshot_dir)
    if args.out:
        print(write_output(payload, args.out))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TrendError as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
