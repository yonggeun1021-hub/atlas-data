#!/usr/bin/env python3
"""Import Kraken's official bulk OHLCVT ZIP into a replay-only daily archive.

The bulk download is historical research input, not an as-captured live market
snapshot.  This importer therefore preserves source aliases, missing days, and
the exact source archive hash while refusing to create a live universe,
turnover ranking, Leadership classification, Regime, action, or order.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "crypto_historical_ohlcvt_import_contract.json"
UTC = dt.timezone.utc


class HistoricalImportError(RuntimeError):
    """Fail-closed bulk archive validation or import error."""


def fail(code: str, detail: str) -> None:
    raise HistoricalImportError(f"{code}: {detail}")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "root must be object")
    return value


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_INVALID")
    pinned = {
        "schema_version": 1,
        "contract_version": "crypto_historical_ohlcvt_import/1",
        "source_name": "kraken_official_downloadable_ohlcvt",
        "source_archive_kind": "complete_zip",
        "archive_entry_pattern": r"^master_q4/([A-Z0-9]+)USD_1440\.csv$",
        "quote_currency": "USD",
        "interval_minutes": 1440,
        "market_timezone": "UTC",
        "csv_fields": [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "base_volume",
            "trade_count",
        ],
        "output_order": "source_pair_id_then_date",
        "missing_interval_policy": "preserve_absence_no_synthesis",
        "duplicate_timestamp_policy": (
            "dedupe_exact_exclude_pair_on_conflict"
        ),
        "turnover_policy": "no_turnover_metric_derived",
        "identity_policy": (
            "source_alias_preserved_no_historical_canonical_backfill"
        ),
        "replay_use": "research_only_not_live_evidence",
        "output_decimal_policy": (
            "plain_decimal_no_exponent_trim_trailing_zero"
        ),
        "output_files": [
            "SHA256SUMS",
            "daily_usd_1440.ndjson.gz",
            "manifest.json",
            "pair_inventory.json",
        ],
    }
    if set(contract) != set(pinned) or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    try:
        re.compile(contract["archive_entry_pattern"])
    except re.error as exc:
        fail("CONTRACT_INVALID", str(exc))
    return contract


def parse_date(value: str, label: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        fail("DATE_INVALID", f"{label}={value}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        fail("FILE_HASH_INVALID", str(exc))
    return digest.hexdigest()


def decimal_value(value: str, label: str, *, allow_zero: bool) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError):
        fail("CSV_DECIMAL_INVALID", label)
    if not parsed.is_finite() or parsed < 0 or (not allow_zero and parsed == 0):
        fail("CSV_DECIMAL_INVALID", label)
    return parsed


def decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def timestamp_day(value: str, label: str) -> tuple[int, dt.date]:
    if not isinstance(value, str) or not value.isdigit():
        fail("CSV_TIMESTAMP_INVALID", label)
    timestamp = int(value)
    try:
        moment = dt.datetime.fromtimestamp(timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        fail("CSV_TIMESTAMP_INVALID", f"{label}: {exc}")
    if (moment.hour, moment.minute, moment.second, moment.microsecond) != (
        0,
        0,
        0,
        0,
    ):
        fail("CSV_TIMESTAMP_INVALID", f"{label}: not UTC midnight")
    return timestamp, moment.date()


def validated_row(
    raw: list[str],
    *,
    source_pair_id: str,
    source_base_alias: str,
    index: int,
) -> tuple[dt.date, dict]:
    label = f"{source_pair_id} row {index}"
    if len(raw) != 7:
        fail("CSV_ROW_INVALID", f"{label}: expected 7 fields")
    timestamp, day = timestamp_day(raw[0], label)
    open_price = decimal_value(raw[1], f"{label} open", allow_zero=False)
    high = decimal_value(raw[2], f"{label} high", allow_zero=False)
    low = decimal_value(raw[3], f"{label} low", allow_zero=False)
    close = decimal_value(raw[4], f"{label} close", allow_zero=False)
    volume = decimal_value(raw[5], f"{label} base_volume", allow_zero=True)
    if not raw[6].isdigit():
        fail("CSV_TRADE_COUNT_INVALID", label)
    trades = int(raw[6])
    if high < max(open_price, close, low) or low > min(open_price, close, high):
        fail("CSV_OHLC_INVALID", label)
    return day, {
        "base_volume": decimal_text(volume),
        "close": decimal_text(close),
        "date": day.isoformat(),
        "high": decimal_text(high),
        "interval_minutes": 1440,
        "low": decimal_text(low),
        "open": decimal_text(open_price),
        "quote_currency": "USD",
        "source_base_alias": source_base_alias,
        "source_pair_id": source_pair_id,
        "timestamp": timestamp,
        "trade_count": trades,
    }


def authority_boundary() -> dict:
    return {
        "classification_authorized": False,
        "historical_live_evidence_authorized": False,
        "investability_authorized": False,
        "leader_authorized": False,
        "production_authorized": False,
        "regime_authorized": False,
        "trading_authorized": False,
    }


def write_json(path: Path, value: object) -> None:
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def import_archive(
    archive: Path,
    output_dir: Path,
    *,
    start_date: dt.date,
    end_date: dt.date,
    contract_path: Path = CONTRACT_PATH,
) -> dict:
    archive = Path(archive)
    output_dir = Path(output_dir)
    contract = load_contract(contract_path)
    if start_date > end_date:
        fail("DATE_RANGE_INVALID", f"{start_date}>{end_date}")
    if not archive.is_file():
        fail("SOURCE_ARCHIVE_MISSING", str(archive))
    if output_dir.exists():
        fail("APPEND_ONLY_VIOLATION", str(output_dir))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}-",
            dir=str(output_dir.parent),
        )
    )
    staging = temporary_root / output_dir.name
    staging.mkdir()
    pattern = re.compile(contract["archive_entry_pattern"])
    archive_sha256 = file_sha256(archive)
    archive_size = archive.stat().st_size
    all_min_day = None
    all_max_day = None
    selected_min_day = None
    selected_max_day = None
    selected_row_count = 0
    inventories = []
    excluded_pairs = []
    try:
        with zipfile.ZipFile(archive) as source:
            entries = []
            seen_pairs = set()
            for info in source.infolist():
                match = pattern.fullmatch(info.filename)
                if match is None:
                    continue
                source_base = match.group(1)
                source_pair_id = f"{source_base}USD"
                if source_pair_id in seen_pairs:
                    fail("ARCHIVE_PAIR_DUPLICATE", source_pair_id)
                seen_pairs.add(source_pair_id)
                entries.append((source_pair_id, source_base, info))
            entries.sort(key=lambda item: item[0])
            if not entries:
                fail("ARCHIVE_DAILY_USD_EMPTY", archive.name)

            output_path = staging / "daily_usd_1440.ndjson.gz"
            with output_path.open("wb") as raw_output:
                with gzip.GzipFile(
                    fileobj=raw_output,
                    mode="wb",
                    mtime=0,
                ) as compressed:
                    for source_pair_id, source_base, info in entries:
                        pair_min = None
                        pair_max = None
                        rows_by_day = {}
                        duplicate_equal_dates = []
                        duplicate_conflict_dates = []
                        with source.open(info, "r") as raw_stream:
                            text_stream = io.TextIOWrapper(
                                raw_stream,
                                encoding="utf-8",
                                newline="",
                            )
                            reader = csv.reader(text_stream)
                            for index, raw_row in enumerate(reader, start=1):
                                day, record = validated_row(
                                    raw_row,
                                    source_pair_id=source_pair_id,
                                    source_base_alias=source_base,
                                    index=index,
                                )
                                pair_min = (
                                    day if pair_min is None else min(pair_min, day)
                                )
                                pair_max = (
                                    day if pair_max is None else max(pair_max, day)
                                )
                                all_min_day = (
                                    day
                                    if all_min_day is None
                                    else min(all_min_day, day)
                                )
                                all_max_day = (
                                    day
                                    if all_max_day is None
                                    else max(all_max_day, day)
                                )
                                prior = rows_by_day.get(day)
                                if prior is not None:
                                    if prior == record:
                                        duplicate_equal_dates.append(day.isoformat())
                                    else:
                                        duplicate_conflict_dates.append(day.isoformat())
                                    continue
                                rows_by_day[day] = record
                        if pair_min is None or pair_max is None:
                            excluded_pairs.append(
                                {
                                    "archive_entry": info.filename,
                                    "conflicting_duplicate_dates": [],
                                    "exact_duplicate_dates": [],
                                    "reason": "SOURCE_EMPTY_ENTRY",
                                    "source_base_alias": source_base,
                                    "source_pair_id": source_pair_id,
                                }
                            )
                            continue
                        if duplicate_conflict_dates:
                            excluded_pairs.append(
                                {
                                    "archive_entry": info.filename,
                                    "conflicting_duplicate_dates": sorted(
                                        set(duplicate_conflict_dates)
                                    ),
                                    "exact_duplicate_dates": sorted(
                                        set(duplicate_equal_dates)
                                    ),
                                    "reason": "SOURCE_DUPLICATE_TIMESTAMP_CONFLICT",
                                    "source_base_alias": source_base,
                                    "source_pair_id": source_pair_id,
                                }
                            )
                            continue
                        selected = [
                            rows_by_day[day]
                            for day in sorted(rows_by_day)
                            if start_date <= day <= end_date
                        ]
                        for record in selected:
                            day = dt.date.fromisoformat(record["date"])
                            compressed.write(
                                (canonical_json(record) + "\n").encode(
                                    "utf-8"
                                )
                            )
                            selected_row_count += 1
                            selected_min_day = (
                                day
                                if selected_min_day is None
                                else min(selected_min_day, day)
                            )
                            selected_max_day = (
                                day
                                if selected_max_day is None
                                else max(selected_max_day, day)
                            )
                        if selected:
                            pair_selected = len(selected)
                            selected_days = [
                                dt.date.fromisoformat(item["date"])
                                for item in selected
                            ]
                            inventories.append(
                                {
                                    "archive_entry": info.filename,
                                    "archive_first_date": pair_min.isoformat(),
                                    "archive_last_date": pair_max.isoformat(),
                                    "exact_duplicate_dates": sorted(
                                        set(duplicate_equal_dates)
                                    ),
                                    "selected_first_date": min(
                                        selected_days
                                    ).isoformat(),
                                    "selected_last_date": max(
                                        selected_days
                                    ).isoformat(),
                                    "selected_row_count": pair_selected,
                                    "source_base_alias": source_base,
                                    "source_pair_id": source_pair_id,
                                }
                            )
        if selected_row_count == 0 or selected_min_day is None or selected_max_day is None:
            fail("SELECTED_RANGE_EMPTY", f"{start_date}..{end_date}")
        inventory = {
            "contract_version": contract["contract_version"],
            "excluded_pair_count": len(excluded_pairs),
            "excluded_pairs": excluded_pairs,
            "pair_count": len(inventories),
            "pairs": inventories,
            "selected_row_count": selected_row_count,
        }
        write_json(staging / "pair_inventory.json", inventory)
        data_sha256 = file_sha256(staging / "daily_usd_1440.ndjson.gz")
        inventory_sha256 = file_sha256(staging / "pair_inventory.json")
        manifest = {
            "archive": {
                "archive_first_date": all_min_day.isoformat(),
                "archive_last_date": all_max_day.isoformat(),
                "byte_length": archive_size,
                "file_name": archive.name,
                "sha256": archive_sha256,
            },
            "authority": authority_boundary(),
            "contract_version": contract["contract_version"],
            "identity_policy": contract["identity_policy"],
            "interval_minutes": contract["interval_minutes"],
            "market_timezone": contract["market_timezone"],
            "missing_interval_policy": contract["missing_interval_policy"],
            "duplicate_timestamp_policy": contract[
                "duplicate_timestamp_policy"
            ],
            "outputs": {
                "daily_usd_1440.ndjson.gz": data_sha256,
                "pair_inventory.json": inventory_sha256,
            },
            "quote_currency": contract["quote_currency"],
            "replay_use": contract["replay_use"],
            "schema_version": 1,
            "selected_range": {
                "actual_first_date": selected_min_day.isoformat(),
                "actual_last_date": selected_max_day.isoformat(),
                "excluded_pair_count": len(excluded_pairs),
                "pair_count": len(inventories),
                "requested_end_date": end_date.isoformat(),
                "requested_start_date": start_date.isoformat(),
                "row_count": selected_row_count,
            },
            "source_name": contract["source_name"],
            "turnover_policy": contract["turnover_policy"],
        }
        write_json(staging / "manifest.json", manifest)
        checksums = {
            "daily_usd_1440.ndjson.gz": data_sha256,
            "manifest.json": file_sha256(staging / "manifest.json"),
            "pair_inventory.json": inventory_sha256,
        }
        (staging / "SHA256SUMS").write_text(
            "".join(
                f"{checksums[name]}  {name}\n" for name in sorted(checksums)
            ),
            encoding="utf-8",
        )
        if sorted(path.name for path in staging.iterdir()) != sorted(
            contract["output_files"]
        ):
            fail("OUTPUT_INVENTORY_INVALID", str(staging))
        staging.replace(output_dir)
        shutil.rmtree(temporary_root)
        return manifest
    except Exception:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    args = parser.parse_args(argv)
    manifest = import_archive(
        args.archive,
        args.out,
        start_date=parse_date(args.start_date, "start_date"),
        end_date=parse_date(args.end_date, "end_date"),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (HistoricalImportError, zipfile.BadZipFile) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
