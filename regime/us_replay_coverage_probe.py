#!/usr/bin/env python3
"""US-DATA-1 U3 coverage probe: a bounded capture plus offline analysis.

``capture`` makes at most ``REQUEST_BUDGET`` (15) HTTP requests. It is run
only by ``.github/workflows/us-regime-replay-range-probe.yml`` after the user
approves that run. The requests are made in this order:

1. The NYSE hours-calendars page.
2. The Nasdaq holiday schedule, used as a cross-check where available.
3. One Alpaca ``/v2/calendar`` request from ``PROBE_START`` to the capture date.
4. Up to ``MAX_BAR_PAGES`` (9) pages of one multi-symbol Alpaca IEX daily-bar
   request for the 15 replay symbols, from ``PROBE_START`` to the capture
   instant.
5. Three FRED ``series/vintagedates`` requests (VIXCLS, WRESBAL, TOTBKCR).

The replay symbols are exactly ``trend_symbols`` (3) plus
``sector_reference_symbols`` (12) from ``config/free_market_data_contract.json``:
15 symbols, the set ``regime/us_historical_replay_population.py`` fetches.

``analyze`` is offline. Given the captured responses in a directory, it
determines each symbol's earliest IEX daily bar, the SPY bar dates, the Alpaca
calendar rows, the parsed NYSE/Nasdaq pages and the earliest ALFRED vintage per
series. It writes a public-safe summary made of dates, counts and sha256 values
only. Bar prices are never copied into the summary.

Secrets are read from the environment, sent only as headers or query
parameters, and never recorded or printed. Capture files record only
non-secret request parameters.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar as CAL  # noqa: E402


SUMMARY_SCHEMA = "us_replay_coverage_probe_summary/1"
CONTRACT_PATH = ROOT / "config" / "free_market_data_contract.json"
REQUEST_BUDGET = 15
MAX_BAR_PAGES = 9
PROBE_START = "2015-01-01"
BAR_PAGE_LIMIT = 10000
FRED_SERIES = ("VIXCLS", "WRESBAL", "TOTBKCR")
FRED_VINTAGEDATES_URL = "https://api.stlouisfed.org/fred/series/vintagedates"
# A binding symbol whose first bar is this close to PROBE_START may have
# history before the probe window, so its true earliest date is unknown.
LEFT_CENSOR_DAYS = 10

PUBLIC_DIR = "public"
PRIVATE_DIR = "private"


class ProbeError(ValueError):
    """Coverage probe invariant failed closed."""


def fail(code: str, detail: str = "") -> None:
    raise ProbeError(f"{code}:{detail}" if detail else code)


def replay_symbols(contract: dict | None = None) -> list[str]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8")) if contract is None else contract
    alpaca = contract["alpaca"]
    trend = list(alpaca["trend_symbols"])
    sector = list(alpaca["sector_reference_symbols"])
    if len(trend) != 3 or len(sector) != 12 or set(trend) & set(sector):
        fail("REPLAY_SYMBOL_CONTRACT_CHANGED")
    symbols = sorted(set(trend) | set(sector))
    if len(symbols) != 15:
        fail("REPLAY_SYMBOL_COUNT_INVALID", str(len(symbols)))
    return symbols


class RequestBudget:
    def __init__(self, limit: int = REQUEST_BUDGET):
        self.limit = limit
        self.used = 0

    def spend(self) -> None:
        if self.used >= self.limit:
            fail("REQUEST_BUDGET_EXHAUSTED", str(self.limit))
        self.used += 1


def _write(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = CAL.canonical_bytes(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def capture(
    out_dir: Path,
    credentials: dict,
    *,
    calendar_url: str = CAL.ALPACA_CALENDAR_URLS[0],
    opener=None,
    clock=None,
    budget: RequestBudget | None = None,
) -> dict:
    """Bounded capture. Returns a manifest with no secrets and no bodies."""
    if calendar_url not in CAL.ALPACA_CALENDAR_URLS:
        fail("ALPACA_CALENDAR_URL_NOT_ALLOWED")
    budget = RequestBudget() if budget is None else budget
    clock = (lambda: dt.datetime.now(CAL.UTC)) if clock is None else clock
    out_dir = Path(out_dir)
    started = clock()
    capture_date = started.astimezone(CAL.NY).date().isoformat()
    end_instant = CAL.utc_z(started)
    symbols = replay_symbols()
    alpaca_headers = {
        "APCA-API-KEY-ID": credentials.get("alpaca_key", ""),
        "APCA-API-SECRET-KEY": credentials.get("alpaca_secret", ""),
    }
    files: dict[str, str] = {}

    def record(kind: str, name: str, value: dict) -> None:
        files[f"{kind}/{name}"] = _write(out_dir / kind / name, value)

    for source_id, name in ((CAL.NYSE_SOURCE_ID, "nyse_page.json"), (CAL.NASDAQ_SOURCE_ID, "nasdaq_page.json")):
        budget.spend()
        record(PUBLIC_DIR, name, CAL.capture_page(source_id, opener=opener, clock=clock))

    budget.spend()
    record(PUBLIC_DIR, "alpaca_calendar.json", CAL.http_capture(
        CAL.ALPACA_CALENDAR_SOURCE_ID, calendar_url,
        {"start": PROBE_START, "end": capture_date},
        headers=alpaca_headers, opener=opener, clock=clock,
    ))

    page_token = None
    pages = 0
    bars_complete = False
    while pages < MAX_BAR_PAGES:
        params = {
            "symbols": ",".join(symbols), "timeframe": "1Day", "start": PROBE_START,
            "end": end_instant, "limit": str(BAR_PAGE_LIMIT), "adjustment": "raw",
            "feed": "iex", "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        budget.spend()
        page = CAL.http_capture(
            CAL.ALPACA_SPY_BAR_SOURCE_ID, CAL.ALPACA_BARS_URL, params,
            headers=alpaca_headers, opener=opener, clock=clock,
        )
        pages += 1
        record(PRIVATE_DIR, f"alpaca_bars_page_{pages:03d}.json", page)
        if page["transport_error"] is not None:
            break
        try:
            body = json.loads(base64.b64decode(page["response"]["raw_base64"]))
        except (ValueError, json.JSONDecodeError):
            break
        page_token = body.get("next_page_token") if isinstance(body, dict) else None
        if not page_token:
            bars_complete = True
            break

    for series_id in FRED_SERIES:
        budget.spend()
        record(PUBLIC_DIR, f"fred_vintagedates_{series_id}.json", CAL.http_capture(
            f"FRED_ALFRED_VINTAGEDATES_{series_id}", FRED_VINTAGEDATES_URL,
            {"series_id": series_id, "file_type": "json", "sort_order": "asc", "limit": "10000"},
            secret_params={"api_key": credentials.get("fred_key", "")},
            opener=opener, clock=clock,
        ))

    manifest = {
        "schema_version": "us_replay_coverage_probe_capture/1",
        "capture_started_at": CAL.utc_z(started),
        "capture_date_new_york": capture_date,
        "probe_start": PROBE_START,
        "bars_end": end_instant,
        "symbols": symbols,
        "request_budget": budget.limit,
        "requests_used": budget.used,
        "bar_pages": pages,
        "bar_pagination_complete": bars_complete,
        "calendar_url": calendar_url,
        "files": dict(sorted(files.items())),
    }
    _write(out_dir / PUBLIC_DIR / "capture_manifest.json", manifest)
    return manifest


def _load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("CAPTURE_FILE_UNREADABLE", Path(path).name)


def _source_result(parse, capture_path: Path, source_id: str) -> dict:
    """Parse one captured page, recording failure instead of raising."""
    if not capture_path.is_file():
        return {"status": "ABSENT", "error": "CAPTURE_FILE_ABSENT", "raw_sha256": None, "parsed": None, "meta": None}
    try:
        captured, raw = CAL.validate_capture(_load(capture_path), source_id)
        parsed = parse(raw)
    except (CAL.UsSessionCalendarError, ProbeError) as exc:
        return {"status": "UNAVAILABLE", "error": str(exc)[:200], "raw_sha256": None, "parsed": None, "meta": None}
    return {
        "status": "PARSED",
        "error": None,
        "raw_sha256": CAL.digest(raw),
        "parsed": parsed,
        "meta": CAL.capture_meta(captured, raw),
    }


def analyze(raw_dir: Path) -> dict:
    raw_dir = Path(raw_dir)
    manifest = _load(raw_dir / PUBLIC_DIR / "capture_manifest.json")
    symbols = replay_symbols()
    if manifest.get("symbols") != symbols:
        fail("MANIFEST_SYMBOLS_MISMATCH")
    if not isinstance(manifest.get("requests_used"), int) or manifest["requests_used"] > REQUEST_BUDGET:
        fail("MANIFEST_BUDGET_EXCEEDED")
    for relative, expected in manifest["files"].items():
        path = raw_dir / relative
        if not path.is_file() or CAL.file_sha256(path) != expected:
            fail("CAPTURE_FILE_HASH_MISMATCH", relative)
    capture_started = CAL.parse_instant(manifest["capture_started_at"], "MANIFEST_TIME_INVALID")

    nyse = _source_result(CAL.parse_nyse_page, raw_dir / PUBLIC_DIR / "nyse_page.json", CAL.NYSE_SOURCE_ID)
    nasdaq = _source_result(CAL.parse_nasdaq_page, raw_dir / PUBLIC_DIR / "nasdaq_page.json", CAL.NASDAQ_SOURCE_ID)
    calendar = _source_result(
        CAL.parse_alpaca_calendar, raw_dir / PUBLIC_DIR / "alpaca_calendar.json", CAL.ALPACA_CALENDAR_SOURCE_ID,
    )
    if calendar["status"] == "PARSED":
        calendar["meta"]["source_url"] = manifest["calendar_url"]
        calendar["window"] = [manifest["probe_start"], manifest["capture_date_new_york"]]

    bars = _analyze_bars(raw_dir, manifest, symbols)
    fred = {series: _analyze_fred(raw_dir / PUBLIC_DIR / f"fred_vintagedates_{series}.json", series) for series in FRED_SERIES}

    summary = {
        "schema_version": SUMMARY_SCHEMA,
        "capture_started_at": manifest["capture_started_at"],
        "capture_date_new_york": manifest["capture_date_new_york"],
        "probe_start": manifest["probe_start"],
        "symbols": symbols,
        "symbol_source": "config/free_market_data_contract.json alpaca.trend_symbols (3) + alpaca.sector_reference_symbols (12)",
        "requests_used": manifest["requests_used"],
        "request_budget": REQUEST_BUDGET,
        "capture_manifest_sha256": CAL.file_sha256(raw_dir / PUBLIC_DIR / "capture_manifest.json"),
        "nyse": nyse,
        "nasdaq": nasdaq,
        "alpaca_calendar": calendar,
        "bars": bars,
        "fred": fred,
        "left_censor_days": LEFT_CENSOR_DAYS,
    }
    if capture_started.astimezone(CAL.NY).date().isoformat() != manifest["capture_date_new_york"]:
        fail("MANIFEST_CAPTURE_DATE_INCONSISTENT")
    summary["summary_sha256"] = CAL.payload_sha256(summary)
    return summary


def _analyze_bars(raw_dir: Path, manifest: dict, symbols: list[str]) -> dict:
    per_symbol: dict[str, set] = {symbol: set() for symbol in symbols}
    response_hashes = []
    error = None
    for index in range(1, manifest["bar_pages"] + 1):
        path = raw_dir / PRIVATE_DIR / f"alpaca_bars_page_{index:03d}.json"
        try:
            _, raw = CAL.validate_capture(_load(path), CAL.ALPACA_SPY_BAR_SOURCE_ID)
            body = json.loads(raw)
            groups = body.get("bars") if isinstance(body, dict) else None
            if not isinstance(groups, dict):
                fail("ALPACA_BARS_SHAPE_INVALID")
            for symbol, rows in groups.items():
                if symbol not in per_symbol or not isinstance(rows, list):
                    fail("ALPACA_BARS_SYMBOL_UNEXPECTED", str(symbol))
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("t"), str):
                        fail("ALPACA_BAR_ROW_INVALID", symbol)
                    session = CAL.parse_date(row["t"][:10], "ALPACA_BAR_DATE_INVALID")
                    if session.isoformat() in per_symbol[symbol]:
                        fail("ALPACA_BAR_DUPLICATE", f"{symbol}:{session}")
                    per_symbol[symbol].add(session.isoformat())
        except (CAL.UsSessionCalendarError, ProbeError, json.JSONDecodeError) as exc:
            error = str(exc)[:200]
            break
        response_hashes.append(CAL.digest(raw))
    complete = error is None and manifest.get("bar_pagination_complete") is True
    symbols_block = {}
    for symbol in symbols:
        dates = sorted(per_symbol[symbol])
        symbols_block[symbol] = {
            "earliest_bar_date": dates[0] if dates else None,
            "latest_bar_date": dates[-1] if dates else None,
            "bar_count": len(dates),
            "bar_dates": dates,
        }
    return {
        "status": "COMPLETE" if complete else "INCOMPLETE",
        "error": error if error else (None if complete else "BAR_PAGINATION_TRUNCATED_OR_FAILED"),
        "pages": manifest["bar_pages"],
        "response_sha256": response_hashes,
        "window": [manifest["probe_start"], manifest["bars_end"]],
        "per_symbol": symbols_block,
        "price_fields_retained": False,
    }


def _analyze_fred(path: Path, series_id: str) -> dict:
    if not path.is_file():
        return {"status": "ABSENT", "earliest_vintage_date": None, "vintage_count": 0, "raw_sha256": None, "error": "CAPTURE_FILE_ABSENT"}
    try:
        _, raw = CAL.validate_capture(_load(path), f"FRED_ALFRED_VINTAGEDATES_{series_id}")
        body = json.loads(raw)
        dates = body.get("vintage_dates") if isinstance(body, dict) else None
        if not isinstance(dates, list) or not dates:
            fail("FRED_VINTAGEDATES_EMPTY", series_id)
        parsed = [CAL.parse_date(value, "FRED_VINTAGE_DATE_INVALID") for value in dates]
        if parsed != sorted(parsed):
            fail("FRED_VINTAGEDATES_NOT_ASCENDING", series_id)
    except (CAL.UsSessionCalendarError, ProbeError, json.JSONDecodeError) as exc:
        return {"status": "UNAVAILABLE", "earliest_vintage_date": None, "vintage_count": 0, "raw_sha256": None, "error": str(exc)[:200]}
    return {
        "status": "PARSED",
        "earliest_vintage_date": parsed[0].isoformat(),
        "vintage_count": len(parsed),
        "raw_sha256": CAL.digest(raw),
        "error": None,
    }


def _credentials_from_env() -> dict:
    return {
        "fred_key": os.environ.get("FRED_API_KEY", "").strip(),
        "alpaca_key": os.environ.get("ALPACA_MARKET_DATA_API_KEY", "").strip(),
        "alpaca_secret": os.environ.get("ALPACA_MARKET_DATA_API_SECRET", "").strip(),
    }


def _forbid_inside_checkout(path: Path) -> None:
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    fail("OUTPUT_INSIDE_CHECKOUT_FORBIDDEN")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="US replay coverage probe")
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--out-dir", type=Path, required=True)
    cap.add_argument("--calendar-url", default=CAL.ALPACA_CALENDAR_URLS[0])
    ana = sub.add_parser("analyze")
    ana.add_argument("--raw-dir", type=Path, required=True)
    ana.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "capture":
        _forbid_inside_checkout(args.out_dir)
        credentials = _credentials_from_env()
        missing = [name for name, value in credentials.items() if not value]
        if missing:
            print(f"STOP: missing credentials: {','.join(sorted(missing))}")
            return 2
        manifest = capture(args.out_dir, credentials, calendar_url=args.calendar_url)
        print(json.dumps({
            "requests_used": manifest["requests_used"],
            "bar_pages": manifest["bar_pages"],
            "bar_pagination_complete": manifest["bar_pagination_complete"],
        }, sort_keys=True))
        return 0
    _forbid_inside_checkout(args.out)
    summary = analyze(args.raw_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(CAL.canonical_bytes(summary))
    print(json.dumps({
        "summary_sha256": summary["summary_sha256"],
        "nyse": summary["nyse"]["status"],
        "nasdaq": summary["nasdaq"]["status"],
        "alpaca_calendar": summary["alpaca_calendar"]["status"],
        "bars": summary["bars"]["status"],
        "fred": {key: value["status"] for key, value in summary["fred"].items()},
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ProbeError, CAL.UsSessionCalendarError) as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
