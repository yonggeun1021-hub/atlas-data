#!/usr/bin/env python3
"""Alpaca historical SIP daily-bar access probe (user approval 2026-09-15).

Question this answers, once, on request: can the existing dedicated
``ALPACA_MARKET_DATA_API_KEY``/``ALPACA_MARKET_DATA_API_SECRET`` credential
read HISTORICAL SIP daily bars (``feed=sip``) when the request window ends
well outside Alpaca's real-time SIP embargo (>=15 minutes; this probe uses
a window ending >=2 days before the run), and how does SIP daily volume
compare with IEX daily volume over the same window?

This module makes AT MOST ``REQUEST_BUDGET`` (6) HTTP GET requests to
``https://data.alpaca.markets/v2/stocks/bars`` (multi-symbol): one request
with ``feed=sip`` and one with ``feed=iex`` over the SAME fixed 10-session
window, each with at most one retry (spent only on a transport-level or
5xx/429 transient failure -- a definitive 401/403 is never retried, since
retrying a subscription denial cannot change the answer and would only
burn budget). It is run only by
``.github/workflows/alpaca-sip-access-probe.yml`` after the user approves
that dispatch.

Output is an AGGREGATE-ONLY JSON artifact:
  * per request: HTTP status, transport-error class, and a normalized
    error-message class (plus a short truncated copy of the API's own
    ``message`` text -- never the raw response body).
  * whether SIP returned bars at all, and a bar count per symbol.
  * the SIP/IEX daily-volume ratio per symbol, median across the shared
    session window.
  * the (vwap*volume)/(close*volume) notional-definition ratio per symbol
    per feed, median across the session window -- a ratio only, never the
    volumes or prices it was built from.

Per-day price/close/volume/vwap values are held only transiently in this
process's memory while the aggregates above are computed, and are never
written to the output artifact. ``assert_no_forbidden_fields`` re-checks
that invariant mechanically before anything is written to disk, and the
workflow re-checks the written file the same way before upload.

Secrets are read from the environment and sent only as Alpaca's
``APCA-API-KEY-ID``/``APCA-API-SECRET-KEY`` headers. They are never placed
in a URL, a query string, a log line, or the output artifact.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "free_market_data_contract.json"

SCHEMA_VERSION = "alpaca_sip_access_probe/1"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
TIMEFRAME = "1Day"
SESSION_COUNT = 10
MIN_DAYS_AGO = 2
CALENDAR_LOOKBACK_DAYS = 20  # buffer so >=10 trading sessions fall in [start, end]
REQUEST_BUDGET = 6
FEEDS = ("sip", "iex")
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}

PREFERRED_SYMBOLS = ("SPY", "XLK", "AAPL")
FALLBACK_SYMBOLS = ("SPY", "XLK", "MSFT")

# Exact key names a per-day bar row (or this script's own raw capture) would
# carry. None of these may appear anywhere in the aggregate artifact. This is
# a defense-in-depth blocklist, NOT the primary guard -- a field simply
# renamed around it (e.g. "sip_daily_volume_series") would slip past a
# key-name check alone. The two STRUCTURAL rules below (a bare list of more
# than a few numbers; a dict keyed by dates) are the primary guard, because
# they reject the *shape* a per-day series necessarily has, independent of
# what its key is called.
FORBIDDEN_KEYS = {
    "o", "h", "l", "c", "v", "vw", "n", "t",
    "open", "high", "low", "close", "volume", "vwap", "trade_count",
    "bars", "raw_base64", "body", "response_body", "raw",
}

# A per-day series (SESSION_COUNT=10 sessions) is never this short; the
# aggregate artifact's own longest legitimate lists (symbols, feeds, attempts)
# are short lists of strings/dicts, not bare numbers. Any bare list of more
# than this many plain numbers is therefore treated as a smuggled per-day
# series regardless of what field name it is stored under.
MAX_PLAIN_NUMERIC_LIST_LENGTH = 3

# A per-day observation keyed by its own date (e.g. {"2026-09-08": ...}) is
# rejected regardless of key name, independent of FORBIDDEN_KEYS.
_DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ProbeError(ValueError):
    """The probe failed closed."""


def fail(code: str, detail: str = "") -> None:
    raise ProbeError(f"{code}:{detail}" if detail else code)


# ─────────────────────────────────────────────────────────────────────────
# Symbol selection (contract-verified, deterministic, no free-text input)
# ─────────────────────────────────────────────────────────────────────────

def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_UNREADABLE", str(exc))


def resolve_symbols(contract: Optional[dict] = None) -> tuple[list[str], str]:
    """Return (symbols, symbol_source) -- exactly 3 approved alpaca.symbols.

    Prefers SPY/XLK/AAPL (the task's nominal set) when all three are
    approved; otherwise SPY/XLK/MSFT (AAPL is not currently in
    ``alpaca.symbols``); otherwise the first 3 approved symbols in
    contract order, so the probe still runs against something the
    contract actually admits rather than failing closed on a naming
    mismatch.
    """
    contract = load_contract() if contract is None else contract
    approved = list(contract.get("alpaca", {}).get("symbols") or [])
    if not approved:
        fail("CONTRACT_ALPACA_SYMBOLS_EMPTY")
    approved_set = set(approved)
    if all(symbol in approved_set for symbol in PREFERRED_SYMBOLS):
        return list(PREFERRED_SYMBOLS), "config/free_market_data_contract.json alpaca.symbols (SPY,XLK,AAPL)"
    if all(symbol in approved_set for symbol in FALLBACK_SYMBOLS):
        return list(FALLBACK_SYMBOLS), "config/free_market_data_contract.json alpaca.symbols (SPY,XLK,AAPL not all present; fallback SPY,XLK,MSFT)"
    chosen = approved[:3]
    if len(chosen) < 3:
        fail("CONTRACT_ALPACA_SYMBOLS_INSUFFICIENT", str(len(chosen)))
    return chosen, "config/free_market_data_contract.json alpaca.symbols (first 3 approved; SPY,XLK,AAPL/MSFT not all present)"


# ─────────────────────────────────────────────────────────────────────────
# Fixed session window
# ─────────────────────────────────────────────────────────────────────────

def session_window(now: dt.datetime) -> tuple[str, str]:
    """A fixed (start, end) date pair, same for the sip and iex request.

    ``end`` is >=2 calendar days before ``now`` (comfortably outside
    Alpaca's real-time SIP embargo, which only restricts the trailing 15
    minutes). ``start`` is far enough back that the 10 most recent trading
    sessions on or before ``end`` are within [start, end] even accounting
    for weekends/holidays; the request still asks for ``limit=10`` so
    Alpaca -- not this script -- decides which 10 sessions those are.
    """
    if now.tzinfo is None:
        fail("CLOCK_NOT_TIMEZONE_AWARE")
    end_date = (now - dt.timedelta(days=MIN_DAYS_AGO)).date()
    start_date = end_date - dt.timedelta(days=CALENDAR_LOOKBACK_DAYS)
    return start_date.isoformat(), end_date.isoformat()


# ─────────────────────────────────────────────────────────────────────────
# Bounded, retry-capped HTTP
# ─────────────────────────────────────────────────────────────────────────

class RequestBudget:
    def __init__(self, limit: int = REQUEST_BUDGET):
        self.limit = limit
        self.used = 0

    def spend(self) -> None:
        if self.used >= self.limit:
            fail("REQUEST_BUDGET_EXHAUSTED", str(self.limit))
        self.used += 1


def _default_open(url: str, headers: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - fixed https host
        return response.status, response.read()


def _do_request(params: dict, headers: dict, opener) -> dict:
    """One GET. Returns a public-safe record; the response body, if any,
    is returned separately (and only in memory) via the second tuple slot.
    """
    query = urllib.parse.urlencode(params)
    url = f"{BARS_URL}?{query}"
    status = None
    body = b""
    transport_error = None
    try:
        status, body = opener(url, headers)
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            body = exc.read()
        except Exception:  # pragma: no cover - defensive only
            body = b""
    except urllib.error.URLError:
        transport_error = "NETWORK_ERROR:URL_ERROR"
    except TimeoutError:
        transport_error = "NETWORK_ERROR:TIMEOUT"
    except OSError as exc:
        transport_error = f"NETWORK_ERROR:{type(exc).__name__}"
    retryable = transport_error is not None or status in RETRYABLE_HTTP_STATUSES
    return {"status": status, "body": body, "transport_error": transport_error, "retryable": retryable}


def _classify_error(status: Optional[int], body: bytes) -> tuple[Optional[str], Optional[str]]:
    """Return (error_message_class, truncated_message) for a non-200 result."""
    message = None
    try:
        parsed = json.loads(body) if body else None
        if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
            message = parsed["message"][:300]
    except (ValueError, json.JSONDecodeError):
        message = None
    if status == 403:
        if message and "subscription does not permit" in message.lower():
            return "SIP_SUBSCRIPTION_DENIED", message
        return "FORBIDDEN_OTHER", message
    if status == 401:
        return "AUTH_INVALID", message
    if status == 429:
        return "RATE_LIMITED", message
    if status is not None and 500 <= status < 600:
        return "SERVER_ERROR", message
    if status is not None and status >= 400:
        return "HTTP_ERROR_OTHER", message
    return None, message


def request_feed(feed: str, symbols: list[str], window: tuple[str, str], credentials: dict, opener, budget: RequestBudget) -> dict:
    """Request one feed once, with at most one retry on a transient failure.

    Returns a public-safe result plus (only in this return value, held by
    the caller only long enough to compute aggregates) the parsed bars.
    """
    start, end = window
    params = {
        "symbols": ",".join(symbols),
        "timeframe": TIMEFRAME,
        "start": start,
        "end": end,
        "limit": str(SESSION_COUNT),
        "adjustment": "raw",
        "sort": "desc",
        "feed": feed,
    }
    headers = {
        "APCA-API-KEY-ID": credentials.get("key", ""),
        "APCA-API-SECRET-KEY": credentials.get("secret", ""),
        "Accept": "application/json",
    }
    attempts = []
    bars_by_symbol: dict = {}
    final = None
    for attempt_index in range(2):  # base attempt + at most one retry
        budget.spend()
        result = _do_request(params, headers, opener)
        error_class, message = (None, None)
        if result["transport_error"] is None and result["status"] != 200:
            error_class, message = _classify_error(result["status"], result["body"])
        attempts.append({
            "attempt": attempt_index + 1,
            "status": result["status"],
            "transport_error": result["transport_error"],
            "error_message_class": error_class,
            "error_message": message,
        })
        final = result
        if not (result["retryable"] and attempt_index == 0):
            break
    ok = final is not None and final["transport_error"] is None and final["status"] == 200
    if ok:
        try:
            parsed = json.loads(final["body"])
            groups = parsed.get("bars") if isinstance(parsed, dict) else None
            if not isinstance(groups, dict):
                fail("ALPACA_BARS_SHAPE_INVALID", feed)
            for symbol in symbols:
                rows = groups.get(symbol) or []
                if not isinstance(rows, list):
                    fail("ALPACA_BARS_SYMBOL_SHAPE_INVALID", f"{feed}:{symbol}")
                bars_by_symbol[symbol] = sorted(
                    (
                        {
                            "date": str(row["t"])[:10],
                            "close": float(row["c"]),
                            "volume": float(row["v"]),
                            "vwap": float(row["vw"]) if row.get("vw") is not None else None,
                        }
                        for row in rows
                        if isinstance(row, dict) and "t" in row and "c" in row and "v" in row
                    ),
                    key=lambda r: r["date"],
                )
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            ok = False
            bars_by_symbol = {}
    return {
        "feed": feed,
        "attempts": attempts,
        "attempts_used": len(attempts),
        "returned_bars": ok,
        "bars_by_symbol": bars_by_symbol,  # stripped out before anything is written
    }


# ─────────────────────────────────────────────────────────────────────────
# Aggregation (ratios only -- no price/volume value ever survives this step)
# ─────────────────────────────────────────────────────────────────────────

def _median(values: list[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _notional_ratio_median(rows: list[dict]) -> Optional[float]:
    """median across days of (vwap*volume) / (close*volume) for one symbol/feed."""
    ratios = []
    for row in rows:
        if row["vwap"] is None or row["close"] in (0, None) or row["volume"] in (0, None):
            continue
        close_notional = row["close"] * row["volume"]
        if close_notional == 0:
            continue
        ratios.append((row["vwap"] * row["volume"]) / close_notional)
    return _median(ratios)


def build_summary(
    symbols: list[str],
    symbol_source: str,
    window: tuple[str, str],
    feed_results: dict,
    budget: RequestBudget,
    generated_at: dt.datetime,
) -> dict:
    per_symbol = {}
    for symbol in symbols:
        sip_rows = feed_results["sip"]["bars_by_symbol"].get(symbol, [])
        iex_rows = feed_results["iex"]["bars_by_symbol"].get(symbol, [])
        sip_by_date = {row["date"]: row for row in sip_rows}
        iex_by_date = {row["date"]: row for row in iex_rows}
        shared_dates = sorted(set(sip_by_date) & set(iex_by_date))
        volume_ratios = []
        for date in shared_dates:
            iex_volume = iex_by_date[date]["volume"]
            if not iex_volume:
                continue
            volume_ratios.append(sip_by_date[date]["volume"] / iex_volume)
        per_symbol[symbol] = {
            "sip_bar_count": len(sip_rows),
            "iex_bar_count": len(iex_rows),
            "shared_session_count": len(shared_dates),
            "sip_volume_over_iex_volume_ratio_median": _median(volume_ratios),
            "vwap_notional_over_close_notional_ratio_median_sip": _notional_ratio_median(sip_rows),
            "vwap_notional_over_close_notional_ratio_median_iex": _notional_ratio_median(iex_rows),
        }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbols": symbols,
        "symbol_source": symbol_source,
        "timeframe": TIMEFRAME,
        "requested_session_count": SESSION_COUNT,
        "window": {"start": window[0], "end": window[1], "min_days_before_run": MIN_DAYS_AGO},
        "request_budget": budget.limit,
        "requests_used": budget.used,
        "feeds": {
            feed: {
                "feed": feed,
                "attempts": feed_results[feed]["attempts"],
                "attempts_used": feed_results[feed]["attempts_used"],
                "returned_bars": feed_results[feed]["returned_bars"],
            }
            for feed in FEEDS
        },
        "per_symbol": per_symbol,
    }
    return summary


def assert_no_forbidden_fields(value: object, path: str = "$") -> None:
    """Fail closed if any per-day price/volume/close/vwap/bar shape survived.

    Three independent checks, so a field simply renamed around the
    key-name blocklist still fails closed:
      1. FORBIDDEN_KEYS -- exact per-day bar field names (defense in depth).
      2. Any dict keyed by a ``YYYY-MM-DD`` date, whatever it is called.
      3. Any bare list of more than MAX_PLAIN_NUMERIC_LIST_LENGTH plain
         numbers, whatever it is called -- the structural shape a per-day
         price/volume/vwap series necessarily has.
    """
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in FORBIDDEN_KEYS:
                fail("FORBIDDEN_FIELD_IN_ARTIFACT", f"{path}.{key}")
            if isinstance(key, str) and _DATE_KEY_RE.match(key):
                fail("FORBIDDEN_DATE_KEYED_MAP", f"{path}.{key}")
            assert_no_forbidden_fields(sub, f"{path}.{key}")
    elif isinstance(value, list):
        numeric = [item for item in value if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if len(numeric) == len(value) and len(value) > MAX_PLAIN_NUMERIC_LIST_LENGTH:
            fail("FORBIDDEN_NUMERIC_SERIES", f"{path} len={len(value)}")
        for index, item in enumerate(value):
            assert_no_forbidden_fields(item, f"{path}[{index}]")


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────

def run_probe(credentials: dict, *, opener=None, clock=None, contract: Optional[dict] = None) -> dict:
    opener = _default_open if opener is None else opener
    clock = (lambda: dt.datetime.now(dt.timezone.utc)) if clock is None else clock
    symbols, symbol_source = resolve_symbols(contract)
    now = clock()
    window = session_window(now)
    budget = RequestBudget()
    feed_results = {feed: request_feed(feed, symbols, window, credentials, opener, budget) for feed in FEEDS}
    summary = build_summary(symbols, symbol_source, window, feed_results, budget, now)
    assert_no_forbidden_fields(summary)
    return summary


def _credentials_from_env() -> dict:
    import os

    return {
        "key": os.environ.get("ALPACA_MARKET_DATA_API_KEY", "").strip(),
        "secret": os.environ.get("ALPACA_MARKET_DATA_API_SECRET", "").strip(),
    }


def _forbid_inside_checkout(path: Path) -> None:
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    fail("OUTPUT_INSIDE_CHECKOUT_FORBIDDEN")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Alpaca historical SIP access probe")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    _forbid_inside_checkout(args.out)
    credentials = _credentials_from_env()
    missing = [name for name, value in credentials.items() if not value]
    if missing:
        print(f"STOP: missing credentials: {','.join(sorted(missing))}")
        return 2
    summary = run_probe(credentials)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "requests_used": summary["requests_used"],
        "request_budget": summary["request_budget"],
        "window": summary["window"],
        "sip_returned_bars": summary["feeds"]["sip"]["returned_bars"],
        "iex_returned_bars": summary["feeds"]["iex"]["returned_bars"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeError as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
