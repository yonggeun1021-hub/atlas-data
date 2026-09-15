#!/usr/bin/env python3
"""Kraken bulk OHLCVT BTC-only replay diagnostic (crypto acceptance condition 6).

User ratification CRYPTO-PAPER-RUNTIME-V1-20260914 replaces G8 condition 6 for
Crypto only with a diagnostic replay of the BTC-only axes (TREND, RISK_VOL)
from 2019-01-01 to the latest finalized UTC day.  History comes from Kraken's
official bulk OHLCVT archive, extended past the archive's last day with exactly
one retained, qualified Kraken OHLC API capture
(``evidence/crypto/btc/raw/<date>``) only after every close on the dates shared
by both sources matches exactly.  The diagnostic passes only when the ratified RISK_VOL rule
produces STRESS, NEGATIVE and POSITIVE at least once.  No sub-range is chosen:
the input must be an unmodified output directory of
``.github/scripts/crypto_historical_ohlcvt_import.py`` requested from
2019-01-01 without truncating the BTC pair's archived history, the join must be
exact and gap-free, and every computable day is counted.

The archive is research input kept outside Git.  This module downloads
nothing, writes only an explicitly requested receipt, and never opens a
Regime, strategy, capital, order, production, trading or REAL authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = "crypto_kraken_bulk_btc_replay_diagnostic/1"
REPLAY_START_DATE = "2019-01-01"
BTC_SOURCE_PAIR_ID = "XBTUSD"
REQUIRED_RISK_VOL_RESULTS = ["STRESS", "NEGATIVE", "POSITIVE"]
RISK_VOL_RESULTS = ["STRESS", "NEGATIVE", "NEUTRAL", "POSITIVE"]
TREND_RESULTS = ["ABOVE_200DMA", "AT_200DMA", "BELOW_200DMA"]
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class KrakenReplayDiagnosticError(ValueError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise KrakenReplayDiagnosticError(code)


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


IMPORTER = _load("atlas_crypto_kraken_replay_importer", ".github/scripts/crypto_historical_ohlcvt_import.py")
BTC_RISK = _load("atlas_crypto_kraken_replay_btc_risk", ".github/scripts/btc_risk.py")
BTC_TREND = BTC_RISK.btc_trend


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KrakenReplayDiagnosticError(code) from exc
    require(isinstance(value, dict), code)
    return value


def validate_archive(archive_dir: Path) -> dict:
    """Validate an importer output directory without trusting its own claims."""
    archive_dir = Path(archive_dir)
    require(archive_dir.is_dir(), "ARCHIVE_DIR_MISSING")
    contract = IMPORTER.load_contract()
    require(sorted(p.name for p in archive_dir.iterdir()) == sorted(contract["output_files"]),
            "ARCHIVE_OUTPUT_INVENTORY_INVALID")
    hashes = {name: file_sha256(archive_dir / name)
              for name in contract["output_files"] if name != "SHA256SUMS"}
    expected_sums = "".join(f"{hashes[name]}  {name}\n" for name in sorted(hashes))
    require((archive_dir / "SHA256SUMS").read_text(encoding="utf-8") == expected_sums,
            "ARCHIVE_SHA256SUMS_MISMATCH")
    manifest = _json(archive_dir / "manifest.json", "ARCHIVE_MANIFEST_INVALID")
    require(manifest.get("contract_version") == contract["contract_version"]
            and manifest.get("source_name") == contract["source_name"]
            and manifest.get("replay_use") == contract["replay_use"]
            and manifest.get("interval_minutes") == 1440
            and manifest.get("quote_currency") == "USD"
            and manifest.get("market_timezone") == "UTC", "ARCHIVE_MANIFEST_SCOPE_INVALID")
    require(isinstance(manifest.get("authority"), dict)
            and all(value is False for value in manifest["authority"].values()),
            "ARCHIVE_AUTHORITY_INVALID")
    require(manifest.get("outputs") == {
        "daily_usd_1440.ndjson.gz": hashes["daily_usd_1440.ndjson.gz"],
        "pair_inventory.json": hashes["pair_inventory.json"],
    }, "ARCHIVE_OUTPUT_HASH_MISMATCH")
    archive = manifest.get("archive")
    require(isinstance(archive, dict) and SHA256.fullmatch(str(archive.get("sha256"))) is not None,
            "ARCHIVE_SOURCE_BINDING_INVALID")
    selected = manifest.get("selected_range")
    require(isinstance(selected, dict), "ARCHIVE_RANGE_INVALID")
    # No range selection: the import must start exactly at the ratified start and
    # must not truncate the archive's latest day.
    require(selected.get("requested_start_date") == REPLAY_START_DATE, "RANGE_SELECTION_START_INVALID")
    inventory = _json(archive_dir / "pair_inventory.json", "ARCHIVE_INVENTORY_INVALID")
    pairs = [row for row in inventory.get("pairs", []) if row.get("source_pair_id") == BTC_SOURCE_PAIR_ID]
    excluded = [row for row in inventory.get("excluded_pairs", [])
                if row.get("source_pair_id") == BTC_SOURCE_PAIR_ID]
    require(not excluded, "BTC_PAIR_EXCLUDED")
    require(len(pairs) == 1, "BTC_PAIR_MISSING")
    pair = pairs[0]
    require(pair.get("selected_last_date") == pair.get("archive_last_date"), "RANGE_SELECTION_END_TRUNCATED")
    require(max(REPLAY_START_DATE, str(pair.get("archive_first_date"))) == pair.get("selected_first_date"),
            "RANGE_SELECTION_START_INVALID")
    return {"manifest": manifest, "pair": pair, "hashes": hashes,
            "sha256sums_sha256": file_sha256(archive_dir / "SHA256SUMS")}


def btc_candles(archive_dir: Path, pair: dict) -> list[dict]:
    candles = []
    with gzip.open(Path(archive_dir) / "daily_usd_1440.ndjson.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("source_pair_id") != BTC_SOURCE_PAIR_ID:
                continue
            try:
                close = Decimal(row["close"])
                date = dt.date.fromisoformat(row["date"])
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                raise KrakenReplayDiagnosticError("BTC_ROW_INVALID") from exc
            require(close.is_finite() and close > 0, "BTC_ROW_INVALID")
            candles.append({"date": date, "close": close})
    require(len(candles) == pair.get("selected_row_count"), "BTC_ROW_COUNT_MISMATCH")
    require(bool(candles) and candles[0]["date"].isoformat() == pair["selected_first_date"]
            and candles[-1]["date"].isoformat() == pair["selected_last_date"], "BTC_RANGE_MISMATCH")
    for before, after in zip(candles, candles[1:]):
        require(after["date"] > before["date"], "BTC_ROW_ORDER_INVALID")
    return candles


def missing_dates(candles: list[dict]) -> list[str]:
    """Calendar days absent from the joined history (preserved, never filled)."""
    missing = []
    for before, after in zip(candles, candles[1:]):
        cursor = before["date"] + dt.timedelta(days=1)
        while cursor < after["date"]:
            missing.append(cursor.isoformat())
            cursor += dt.timedelta(days=1)
    return missing


def contiguous_window(candles: list[dict], index: int, size: int) -> bool:
    """True when the ``size`` closes ending at ``index`` are consecutive UTC days."""
    start = index - size + 1
    return start >= 0 and (candles[index]["date"] - candles[start]["date"]).days == size - 1


def latest_api_snapshot(root: Path = ROOT) -> Path:
    raw = Path(root) / "evidence" / "crypto" / "btc" / "raw"
    dates = sorted(p.name for p in raw.iterdir() if p.is_dir()) if raw.is_dir() else []
    require(bool(dates), "API_SNAPSHOT_MISSING")
    return raw / dates[-1]


def api_candles(snapshot_dir: Path) -> tuple[list[dict], dict]:
    """Finalized closes of one qualified retained Kraken OHLC API capture."""
    try:
        contract = BTC_TREND.load_contract(BTC_RISK.PRICE_CONTRACT_PATH)
        core = BTC_TREND.snapshot_core(snapshot_dir, contract)
        manifest = BTC_TREND.validate_manifest(snapshot_dir, core, contract)
    except BTC_TREND.TrendError as exc:
        raise KrakenReplayDiagnosticError("API_SNAPSHOT_INVALID") from exc
    candles = [{"date": row["date"], "close": row["close"]} for row in core["series"]["finalized"]]
    manifest_sha = file_sha256(Path(snapshot_dir) / "_manifest.json")
    return candles, {
        "snapshot_path": str(Path(snapshot_dir).resolve().relative_to(ROOT.resolve()))
        if Path(snapshot_dir).resolve().is_relative_to(ROOT.resolve()) else Path(snapshot_dir).name,
        "snapshot_date": core["snapshot_date"],
        "fetched_at_utc": core["fetched_at_utc"],
        "response_sha256": core["response_sha256"],
        "manifest_sha256": manifest_sha,
        "capture_version": manifest["capture_version"],
        "first_finalized_date": candles[0]["date"].isoformat(),
        "last_finalized_date": candles[-1]["date"].isoformat(),
    }


def join_history(bulk: list[dict], api: list[dict]) -> tuple[list[dict], dict]:
    """Extend bulk closes with API closes after an exact overlap check."""
    bulk_by_date = {row["date"]: row["close"] for row in bulk}
    shared = [row for row in api if row["date"] in bulk_by_date]
    mismatches = [row["date"].isoformat() for row in shared if row["close"] != bulk_by_date[row["date"]]]
    overlap = {
        "shared_date_count": len(shared),
        "first_shared_date": shared[0]["date"].isoformat() if shared else None,
        "last_shared_date": shared[-1]["date"].isoformat() if shared else None,
        "close_mismatch_count": len(mismatches),
        "close_mismatch_dates": mismatches,
        "comparison": "EXACT_DECIMAL_CLOSE_EQUALITY_EVERY_SHARED_DATE",
    }
    require(bool(shared), "API_BULK_OVERLAP_EMPTY")
    require(not mismatches, "API_BULK_OVERLAP_CLOSE_MISMATCH")
    last_bulk = bulk[-1]["date"]
    require(all(row["date"] in bulk_by_date for row in api if row["date"] <= last_bulk),
            "API_BULK_OVERLAP_DATE_MISSING")
    extension = [row for row in api if row["date"] > last_bulk]
    overlap["extension_row_count"] = len(extension)
    return bulk + extension, overlap


def build_receipt(archive_dir: Path, api_snapshot_dir: Path | None = None) -> dict:
    from regime import crypto_paper_runtime as RUNTIME

    validated = validate_archive(archive_dir)
    bulk = btc_candles(archive_dir, validated["pair"])
    api_snapshot_dir = latest_api_snapshot() if api_snapshot_dir is None else Path(api_snapshot_dir)
    api, api_source = api_candles(api_snapshot_dir)
    candles, overlap = join_history(bulk, api)
    absent = missing_dates(candles)
    risk_contract = BTC_RISK.load_contract()
    required = max(risk_contract["realized_vol_lookback_returns"] + 1,
                   risk_contract["drawdown_lookback_closes"])
    trend_window = 200
    risk_counts = {name: 0 for name in RISK_VOL_RESULTS}
    trend_counts = {name: 0 for name in TREND_RESULTS}
    first_seen = {name: None for name in RISK_VOL_RESULTS}
    point_count = 0
    undefined_risk_dates = []
    undefined_trend_count = 0
    # Every finalized close from the first full lookback is evaluated.  A day
    # whose lookback window spans a preserved missing interval is UNDEFINED
    # (counted and disclosed), never filled, skipped silently or re-ranged.
    for index in range(required - 1, len(candles)):
        prefix = candles[: index + 1]
        if not contiguous_window(candles, index, required):
            undefined_risk_dates.append(candles[index]["date"].isoformat())
            continue
        point = BTC_RISK.risk_point(prefix, risk_contract)
        vol = Decimal(point["realized_volatility"]["annualized_fraction"])
        dd = Decimal(point["drawdown"]["current_fraction"])
        result = RUNTIME.risk_vol_direction(vol, dd)
        risk_counts[result] += 1
        first_seen[result] = first_seen[result] or point["as_of_date"]
        point_count += 1
        if not contiguous_window(candles, index, trend_window):
            undefined_trend_count += 1
        else:
            window = prefix[-trend_window:]
            dma = sum((item["close"] for item in window), Decimal(0)) / Decimal(trend_window)
            latest = window[-1]["close"]
            trend_counts["ABOVE_200DMA" if latest > dma else "BELOW_200DMA" if latest < dma else "AT_200DMA"] += 1
    require(point_count > 0, "BTC_HISTORY_INSUFFICIENT")
    missing = [name for name in REQUIRED_RISK_VOL_RESULTS if risk_counts[name] == 0]
    manifest = validated["manifest"]
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "market": "CRYPTO",
        "mode": "DIAGNOSTIC_REPLAY_NOT_NATURAL_PIT",
        "source": {
            "source_name": manifest["source_name"],
            "archive_file_name": manifest["archive"].get("file_name"),
            "archive_sha256": manifest["archive"]["sha256"],
            "archive_byte_length": manifest["archive"].get("byte_length"),
            "import_manifest_sha256": validated["hashes"]["manifest.json"],
            "import_sha256sums_sha256": validated["sha256sums_sha256"],
            "daily_rows_sha256": validated["hashes"]["daily_usd_1440.ndjson.gz"],
            "source_pair_id": BTC_SOURCE_PAIR_ID,
            "bulk_first_date": bulk[0]["date"].isoformat(),
            "bulk_last_date": bulk[-1]["date"].isoformat(),
        },
        "api_extension": api_source,
        "overlap_check": overlap,
        "range": {
            "requested_start_date": REPLAY_START_DATE,
            "first_close_date": candles[0]["date"].isoformat(),
            "last_close_date": candles[-1]["date"].isoformat(),
            "first_risk_point_date": candles[required - 1]["date"].isoformat(),
            "range_selection": "NONE_ALL_COMPUTABLE_DAYS",
            "missing_calendar_dates": absent,
            "missing_interval_policy": "PRESERVED_ABSENCE_WINDOW_UNDEFINED_NO_FILL",
        },
        "undefined_risk_point_count": len(undefined_risk_dates),
        "undefined_risk_point_first_date": undefined_risk_dates[0] if undefined_risk_dates else None,
        "undefined_risk_point_last_date": undefined_risk_dates[-1] if undefined_risk_dates else None,
        "undefined_trend_point_count": undefined_trend_count,
        "axes": ["TREND", "RISK_VOL"],
        "risk_vol_rule": "CRYPTO_PAPER_RUNTIME_V1_ABSOLUTE",
        "risk_point_count": point_count,
        "risk_vol_counts": risk_counts,
        "risk_vol_first_seen": first_seen,
        "trend_counts": trend_counts,
        "required_risk_vol_results": list(REQUIRED_RISK_VOL_RESULTS),
        "missing_required_results": missing,
        "status": "PASS" if not missing else "FAIL",
        "authority": {
            "diagnostic_only": True, "natural_pit_evidence": False, "threshold_revision_authorized": False,
            "runtime_authorized": False, "capital_authorized": False, "order_authorized": False,
            "trading_authorized": False, "real_authorized": False,
        },
    }
    receipt["payload_sha256"] = payload_sha256(receipt)
    return receipt


def validate_receipt(receipt: dict, archive_dir: Path | None = None) -> dict:
    require(isinstance(receipt, dict) and receipt.get("schema_version") == SCHEMA_VERSION, "RECEIPT_SCHEMA_INVALID")
    unsigned = dict(receipt)
    claimed = unsigned.pop("payload_sha256", None)
    require(claimed == payload_sha256(unsigned), "RECEIPT_PAYLOAD_HASH_MISMATCH")
    counts = receipt.get("risk_vol_counts")
    require(isinstance(counts, dict) and set(counts) == set(RISK_VOL_RESULTS)
            and all(type(v) is int and v >= 0 for v in counts.values())
            and sum(counts.values()) == receipt.get("risk_point_count"), "RECEIPT_COUNTS_INVALID")
    missing = [name for name in REQUIRED_RISK_VOL_RESULTS if counts[name] == 0]
    require(receipt.get("missing_required_results") == missing
            and receipt.get("status") == ("PASS" if not missing else "FAIL"), "RECEIPT_STATUS_INVALID")
    require(receipt.get("range", {}).get("requested_start_date") == REPLAY_START_DATE
            and receipt["range"].get("range_selection") == "NONE_ALL_COMPUTABLE_DAYS", "RECEIPT_RANGE_INVALID")
    authority = receipt.get("authority")
    require(isinstance(authority, dict) and authority.get("diagnostic_only") is True
            and all(v is False for k, v in authority.items() if k != "diagnostic_only"), "RECEIPT_AUTHORITY_ESCALATION")
    overlap = receipt.get("overlap_check")
    require(isinstance(overlap, dict) and overlap.get("close_mismatch_count") == 0
            and overlap.get("shared_date_count", 0) > 0, "RECEIPT_OVERLAP_INVALID")
    if archive_dir is not None:
        rebuilt = build_receipt(archive_dir, ROOT / receipt["api_extension"]["snapshot_path"])
        require(canonical_json(rebuilt) == canonical_json(receipt), "RECEIPT_REDERIVATION_MISMATCH")
    return dict(receipt)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive-dir", type=Path, required=True,
                        help="output directory of crypto_historical_ohlcvt_import.py (outside Git)")
    parser.add_argument("--api-snapshot-dir", type=Path,
                        help="retained evidence/crypto/btc/raw/<date> capture (default: latest retained)")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    receipt = build_receipt(args.archive_dir, args.api_snapshot_dir)
    text = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        require(not args.out.exists(), "RECEIPT_APPEND_ONLY_VIOLATION")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(json.dumps({"out": str(args.out), "sha256": hashlib.sha256(text.encode()).hexdigest(),
                          "status": receipt["status"]}, sort_keys=True))
    else:
        print(text, end="")
    return 0 if receipt["status"] == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KrakenReplayDiagnosticError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1)
