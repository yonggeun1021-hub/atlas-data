#!/usr/bin/env python3
"""Publish a fail-closed KRX post-close observation bundle.

This path is intentionally separate from the morning KRX authority.  It never
writes ``data/latest_krx.json`` or ``data/YYYY-MM-DD/krx.json``.  A successful
run publishes one immutable, exact-date bundle for the 18:00 briefing; a
partial or malformed response is preserved only as an incident.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
KST = ZoneInfo("Asia/Seoul")

BASIC_INVESTORS = ("기관합계", "외국인합계", "개인", "기타법인")
OBSERVATION_STATUS = "observed_unconfirmed"
CONFIRM_REASON = "deferred_to_next_day"
SCHEMA_VERSION = 1
KRX_CODE = re.compile(r"^\d{6}$")


class PostCloseError(RuntimeError):
    pass


def canonical_bytes(obj):
    return (
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_bytes(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def write_json(path, obj):
    write_bytes(path, canonical_bytes(obj))


def parse_date(value):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise PostCloseError(f"INVALID_EXPECTED_DATE:{value}") from exc


def parse_observed_at(value, expected_date):
    if not isinstance(value, str):
        raise PostCloseError("OBSERVED_AT_MISSING")
    try:
        observed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise PostCloseError(f"OBSERVED_AT_INVALID:{value}") from exc
    if observed.tzinfo is None:
        raise PostCloseError("OBSERVED_AT_TIMEZONE_MISSING")
    if observed.astimezone(KST).date().isoformat() != expected_date:
        raise PostCloseError(
            f"OBSERVED_AT_DATE_MISMATCH:{value}:{expected_date}"
        )
    return value


def require_numeric(row, field):
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PostCloseError(f"OBSERVED_FIELD_INVALID:{field}")
    return value


def require_investor_map(row, field):
    value = row.get(field)
    if not isinstance(value, dict):
        raise PostCloseError(f"INVESTOR_FIELD_MISSING:{field}")
    missing = [name for name in BASIC_INVESTORS if name not in value]
    if missing:
        raise PostCloseError(
            f"INVESTOR_COLUMNS_MISSING:{field}:{','.join(missing)}"
        )
    if any(
        isinstance(value[name], bool) or not isinstance(value[name], int)
        for name in BASIC_INVESTORS
    ):
        raise PostCloseError(f"INVESTOR_VALUE_INVALID:{field}")
    return {name: value[name] for name in BASIC_INVESTORS}


def build_symbol_view(code, stock, expected_date, source_sha):
    if not isinstance(code, str) or not KRX_CODE.fullmatch(code):
        raise PostCloseError(f"INVALID_KRX_CODE:{code}")
    if not isinstance(stock, dict) or stock.get("status") != "ok":
        raise PostCloseError(f"STOCK_NOT_OK:{code}")

    daily = stock.get("daily")
    row = daily.get(expected_date) if isinstance(daily, dict) else None
    if not isinstance(row, dict):
        raise PostCloseError(f"EXACT_DAY_ROW_MISSING:{code}:{expected_date}")

    if stock.get("latest_observed_day") != expected_date:
        raise PostCloseError(f"LATEST_OBSERVED_DAY_MISMATCH:{code}")
    if row.get("confirmed") is not False:
        raise PostCloseError(f"SAME_DAY_MUST_BE_UNCONFIRMED:{code}")
    if row.get("confirm_reason") != CONFIRM_REASON:
        raise PostCloseError(f"CONFIRM_REASON_INVALID:{code}")

    latest_confirmed = stock.get("latest_trading_day")
    if not isinstance(latest_confirmed, str):
        raise PostCloseError(f"CONFIRMED_BOUNDARY_INVALID:{code}")
    parse_date(latest_confirmed)
    if latest_confirmed >= expected_date:
        raise PostCloseError(f"CONFIRMED_BOUNDARY_INVALID:{code}")
    if stock.get("sma20_through") != latest_confirmed:
        raise PostCloseError(f"SMA20_BOUNDARY_INVALID:{code}")

    absent = row.get("investor_rows_absent")
    if not isinstance(absent, list):
        raise PostCloseError(f"INVESTOR_ABSENCE_CONTRACT_MISSING:{code}")
    if any(name in absent for name in ("net_value", "net_volume")):
        raise PostCloseError(f"INVESTOR_ROW_MISSING:{code}:{expected_date}")

    observed_row = {
        "observation_status": OBSERVATION_STATUS,
        "decision_eligible": False,
        "confirmed": False,
        "confirm_reason": CONFIRM_REASON,
        "trading_day": expected_date,
        "observed_at_kst": parse_observed_at(
            row.get("observed_at_kst"), expected_date
        ),
        "source_snapshot_sha256": source_sha,
        "close": require_numeric(row, "close"),
        "open": require_numeric(row, "open"),
        "high": require_numeric(row, "high"),
        "low": require_numeric(row, "low"),
        "volume": require_numeric(row, "volume"),
        "change_pct": require_numeric(row, "change_pct"),
        "net_value": require_investor_map(row, "net_value"),
        "net_volume": require_investor_map(row, "net_volume"),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "market": "KRX",
        "symbol": code,
        "name": stock.get("name"),
        "atlas_stage": stock.get("atlas_stage"),
        "latest_observed_day": expected_date,
        "latest_trading_day": latest_confirmed,
        "observed_row": observed_row,
        "decision_boundary": {
            "same_day_confirmation": "next_day",
            "history_basis": "confirmed_only",
            "sma20": stock.get("sma20"),
            "sma20_basis": stock.get("sma20_basis"),
            "sma20_through": stock.get("sma20_through"),
            "sma20_status": stock.get("sma20_status"),
        },
        "source": {
            "source_file": (
                "data/observations/krx_post_close/"
                f"{expected_date}/source.json"
            ),
            "source_snapshot_sha256": source_sha,
        },
    }


def build_bundle(source, expected_date):
    parse_date(expected_date)
    if not isinstance(source, dict):
        raise PostCloseError("SOURCE_NOT_OBJECT")
    if source.get("collected_for_kst_date") != expected_date:
        raise PostCloseError("SOURCE_DATE_MISMATCH")
    if source.get("same_day_confirmation") != "next_day":
        raise PostCloseError("SOURCE_CONFIRMATION_CONTRACT_INVALID")

    stocks = source.get("stocks")
    summary = source.get("summary")
    if not isinstance(stocks, dict) or not stocks:
        raise PostCloseError("STOCKS_MISSING")
    if not isinstance(summary, dict):
        raise PostCloseError("SUMMARY_MISSING")
    if (
        type(summary.get("ok")) is not int
        or type(summary.get("failed")) is not int
        or summary.get("failed") != 0
        or summary.get("ok") != len(stocks)
    ):
        raise PostCloseError("PARTIAL_SOURCE_RESPONSE")

    source_raw = canonical_bytes(source)
    source_sha = hashlib.sha256(source_raw).hexdigest()
    views = {
        code: build_symbol_view(code, stock, expected_date, source_sha)
        for code, stock in sorted(stocks.items())
    }
    confirmed_days = {view["latest_trading_day"] for view in views.values()}

    index = {
        "schema_version": SCHEMA_VERSION,
        "purpose": "KRX post-close briefing observation",
        "expected_kst_date": expected_date,
        "status": "ready_observed_unconfirmed",
        "observation_status": OBSERVATION_STATUS,
        "decision_eligible": False,
        "latest_observed_day": expected_date,
        "latest_trading_day": (
            next(iter(confirmed_days)) if len(confirmed_days) == 1 else None
        ),
        "summary": {"ok": len(views), "failed": 0},
        "symbols": sorted(views),
        "source": {
            "source_file": (
                "data/observations/krx_post_close/"
                f"{expected_date}/source.json"
            ),
            "source_snapshot_sha256": source_sha,
            "collected_at_utc": source.get("collected_at_utc"),
            "collected_at_kst": source.get("collected_at_kst"),
        },
        "warnings": (
            []
            if len(confirmed_days) == 1
            else ["confirmed_boundary_differs_by_symbol"]
        ),
    }
    return source_raw, index, views


def bundle_root(data_root):
    return Path(data_root) / "observations" / "krx_post_close"


def publish_bundle(source, expected_date, data_root=DATA_ROOT):
    source_raw, index, views = build_bundle(source, expected_date)
    root = bundle_root(data_root)
    target = root / expected_date
    if target.exists():
        raise PostCloseError(f"APPEND_ONLY_VIOLATION:{target}")

    staging = root / f".{expected_date}.tmp.{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        write_bytes(staging / "source.json", source_raw)
        write_json(staging / "index.json", index)
        for code, view in views.items():
            write_json(staging / "symbols" / f"{code}.json", view)
        staging.rename(target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return target


def check_bundle(expected_date, data_root=DATA_ROOT):
    try:
        target = bundle_root(data_root) / expected_date
        source_path = target / "source.json"
        source_file_raw = source_path.read_bytes()
        source = json.loads(source_file_raw)
        source_raw, expected_index, expected_views = build_bundle(
            source, expected_date
        )
        if source_file_raw != source_raw:
            return False
        index = json.loads((target / "index.json").read_text(encoding="utf-8"))
        if index != expected_index:
            return False
        symbol_paths = {
            path.name for path in (target / "symbols").glob("*.json")
        }
        if symbol_paths != {f"{code}.json" for code in expected_views}:
            return False
        for code, expected_view in expected_views.items():
            view = json.loads(
                (target / "symbols" / f"{code}.json").read_text(
                    encoding="utf-8"
                )
            )
            if view != expected_view:
                return False
        return True
    except (OSError, ValueError, TypeError, PostCloseError):
        return False


def incident_path(expected_date, data_root, environ):
    run_id = str(environ.get("GITHUB_RUN_ID") or "local")
    run_attempt = str(environ.get("GITHUB_RUN_ATTEMPT") or "1")
    safe_id = "".join(ch for ch in run_id if ch.isdigit()) or "local"
    safe_attempt = "".join(ch for ch in run_attempt if ch.isdigit()) or "1"
    return (
        Path(data_root)
        / "incident"
        / "krx_post_close"
        / expected_date
        / f"run-{safe_id}-attempt-{safe_attempt}.json"
    )


def write_incident(expected_date, error, source, data_root, environ):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "expected_kst_date": expected_date,
        "status": "unknown",
        "decision_eligible": False,
        "error": f"{type(error).__name__}:{error}",
        "source_payload": source,
    }
    path = incident_path(expected_date, data_root, environ)
    write_json(path, payload)
    return path


def collect_source(expected):
    # Lazy import keeps the guard/check path independent of pykrx and secrets.
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        raise PostCloseError("KRX_CREDENTIALS_MISSING")
    import common
    import krx

    payload = krx.collect_payload(today=expected, record_stage=False)
    payload["universe_meta"] = dict(common.universe_meta)
    return payload


def run_collection(expected_date, data_root=DATA_ROOT, environ=None):
    environ = os.environ if environ is None else environ
    expected = parse_date(expected_date)
    source = None
    try:
        source = collect_source(expected)
        return publish_bundle(source, expected_date, data_root=data_root)
    except Exception as exc:
        write_incident(
            expected_date,
            exc,
            source,
            data_root=data_root,
            environ=environ,
        )
        raise


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=dt.datetime.now(KST).date().isoformat())
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        print("fresh" if check_bundle(args.date, args.data_root) else "stale")
        return 0

    try:
        target = run_collection(args.date, args.data_root)
    except Exception as exc:
        print(f"FATAL: KRX post-close observation failed — {exc}")
        return 1

    print(f"PASS: KRX post-close observation published — {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
