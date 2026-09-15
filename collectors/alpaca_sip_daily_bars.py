#!/usr/bin/env python3
"""US T2 C3 liquidity input: Alpaca historical SIP daily bars (2026-09-15).

Fetches historical daily bars for the 22 symbols already approved in
``config/free_market_data_contract.json`` (``alpaca.symbols`` -- read only,
never modified or expanded here; expanding that universe needs a separate
approval per the ratified ``RULE.LIQUIDITY.US_SIP_SOURCE.V1`` decision).
``feed=sip`` is tried first per symbol; ``feed=iex`` is the fallback only
when SIP is denied/empty for that symbol.

Per-symbol requests (never the multi-symbol endpoint): the existing probe
(``collectors/alpaca_sip_access_probe.py``, run 34907066300) found the
multi-symbol endpoint returns 0 bars for some symbols in a batch without
raising an error, silently dropping them. The single-symbol endpoint
(``/v2/stocks/{symbol}/bars``) does not share that failure mode, and each
symbol's ``next_page_token`` is followed explicitly (bounded by
``MAX_PAGES_PER_REQUEST``) rather than assumed empty.

SIP embargo: only bars whose regular-session close (America/New_York
16:00, DST-aware via ``zoneinfo``) is at least ``FRESHNESS_EMBARGO_MINUTES``
(15) in the past are used -- ratified text: "정규장 마감 후 15분 이상 지난
자료". This is a freshness/embargo check on bars Alpaca already returned; it
never decides whether a date WAS a trading session (Alpaca does, by
returning or omitting a bar for it) and does not read or duplicate the
official US session calendar contract (``config/us_session_calendar_source_v1.json``),
which governs a different question (T2 gate PASS/CLOSED, not bar freshness).

Public output (derived only, per symbol): the 20-session average traded
value (``avg_traded_value_usd`` = mean of close*volume across the most
recent 20 eligible sessions -- probe evidence
(run 34907066300, XLK 10-session vwap/close notional ratio ~1.0002) showed
vwap*volume differs from close*volume by ~0.02%, immaterial at this
metric's threshold scale, so close*volume is the one formula used here),
the most recent eligible session's own close (``last_close_usd`` -- the
ratified price-floor condition's own input, the one deliberate single-price
exception to "no raw price ever appears"), the session count actually
used, which feed produced it, and the composite
RULE.LIQUIDITY.US_SIP_SOURCE.V1 verdict via
``universe/us_liquidity_sip_source.py`` (``status`` plus its three
sub-checks ``volume_status``/``price_status``/``otc_exclusion_status``,
and lineage: window_end, reasons). ``otc_exclusion_status`` is resolved via
``universe/us_listing_lookup.py`` from the already-committed, point-in-time
Nasdaq Trader Symbol Directory capture (that module's own docstring has
the exact field definitions and packet-selection rule this collector
does not duplicate here); it stays honestly ``UNKNOWN`` only when that
lookup itself cannot resolve a symbol. No per-day bar (open/high/low/
close/volume/vwap/trade_count) is ever written to disk or committed;
``assert_no_raw_bar_fields`` re-checks that mechanically before anything
is written, mirroring the existing probe's ``assert_no_forbidden_fields``
pattern.

Secrets (``ALPACA_MARKET_DATA_API_KEY``/``ALPACA_MARKET_DATA_API_SECRET`` --
the existing dedicated market-data-only credential, same as
``collectors/free_market_data.py`` and the SIP access probe) are read from
the environment and sent only as Alpaca's own auth headers; never logged,
never placed in a URL/query string, never written to the output artifact.
"""
from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, ROUND_HALF_EVEN
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universe import us_liquidity_sip_source as LIQ  # noqa: E402
from universe import us_listing_lookup as LISTING  # noqa: E402

CONTRACT_PATH = ROOT / "config" / "free_market_data_contract.json"
DATED_DIR = ROOT / "data" / "us_sip_daily_liquidity"
LATEST_PATH = ROOT / "data" / "latest_us_sip_daily_liquidity.json"

SCHEMA_VERSION = "alpaca_sip_daily_bars/1"
BARS_URL_TEMPLATE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"
TIMEFRAME = "1Day"
LOOKBACK_CALENDAR_DAYS = 45  # buffer so >=20 trading sessions land in [start, end]
FRESHNESS_EMBARGO_MINUTES = 15
MAX_PAGES_PER_REQUEST = 3
MAX_ATTEMPTS_PER_PAGE = 2  # base attempt + at most one retry
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
FEEDS = ("sip", "iex")
NY = "America/New_York"

# Exact per-day bar field names (defense in depth -- the structural checks
# below are the primary guard, see collectors/alpaca_sip_access_probe.py's
# identical rationale).
FORBIDDEN_RAW_KEYS = {
    "o", "h", "l", "c", "v", "vw", "n", "t",
    "open", "high", "low", "close", "volume", "vwap", "trade_count",
    "bars", "raw_base64", "body", "response_body", "raw", "next_page_token",
}
MAX_PLAIN_NUMERIC_LIST_LENGTH = 3
_DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AlpacaSipDailyBarsError(ValueError):
    """Fail-closed collector error."""


def fail(code: str, detail: str = "") -> None:
    raise AlpacaSipDailyBarsError(f"{code}:{detail}" if detail else code)


# ─────────────────────────────────────────────────────────────────────────
# Approved symbol universe (read only -- never expanded here)
# ─────────────────────────────────────────────────────────────────────────

def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_UNREADABLE", str(exc))


def resolve_symbols(contract: Optional[dict] = None) -> tuple[list[str], str]:
    contract = load_contract() if contract is None else contract
    approved = list(contract.get("alpaca", {}).get("symbols") or [])
    if not approved:
        fail("CONTRACT_ALPACA_SYMBOLS_EMPTY")
    return sorted(set(approved)), "config/free_market_data_contract.json alpaca.symbols"


# ─────────────────────────────────────────────────────────────────────────
# Request window and the >=15-minute-past-close freshness embargo
# ─────────────────────────────────────────────────────────────────────────

def session_window(now: dt.datetime) -> tuple[str, str]:
    if now.tzinfo is None:
        fail("CLOCK_NOT_TIMEZONE_AWARE")
    end_date = now.date()
    start_date = end_date - dt.timedelta(days=LOOKBACK_CALENDAR_DAYS)
    return start_date.isoformat(), end_date.isoformat()


def regular_close_utc(session_date: dt.date) -> dt.datetime:
    """16:00 America/New_York for ``session_date``, converted to UTC.

    DST-aware via the stdlib ``zoneinfo`` database. Used only to gate bar
    freshness (see module docstring) -- never to decide whether
    ``session_date`` was itself a trading day.
    """
    try:
        tz = ZoneInfo(NY)
    except ZoneInfoNotFoundError:
        fail("TZDATA_UNAVAILABLE")
    local_close = dt.datetime(
        session_date.year, session_date.month, session_date.day, 16, 0, tzinfo=tz
    )
    return local_close.astimezone(dt.timezone.utc)


def is_bar_fresh_enough(session_date: dt.date, now: dt.datetime) -> bool:
    embargo = dt.timedelta(minutes=FRESHNESS_EMBARGO_MINUTES)
    return now >= regular_close_utc(session_date) + embargo


# ─────────────────────────────────────────────────────────────────────────
# Bounded, retry-capped, paginated HTTP (per symbol, per feed)
# ─────────────────────────────────────────────────────────────────────────

class RequestBudget:
    def __init__(self, limit: int):
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


def _do_request(url: str, headers: dict, opener) -> dict:
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


def request_symbol_feed(
    symbol: str, feed: str, window: tuple[str, str], credentials: dict, opener, budget: RequestBudget
) -> dict:
    """Fetch every page for one (symbol, feed); bars held only in memory."""
    start, end = window
    page_token: Optional[str] = None
    attempts: list[dict] = []
    bars: list[dict] = []
    ok = True
    for _page in range(MAX_PAGES_PER_REQUEST):
        params = {
            "timeframe": TIMEFRAME,
            "start": start,
            "end": end,
            "limit": "1000",
            "adjustment": "raw",
            "sort": "asc",
            "feed": feed,
        }
        if page_token:
            params["page_token"] = page_token
        headers = {
            "APCA-API-KEY-ID": credentials.get("key", ""),
            "APCA-API-SECRET-KEY": credentials.get("secret", ""),
            "Accept": "application/json",
        }
        page_attempts: list[dict] = []
        final = None
        for attempt_index in range(MAX_ATTEMPTS_PER_PAGE):
            budget.spend()
            url = f"{BARS_URL_TEMPLATE.format(symbol=symbol)}?{urllib.parse.urlencode(params)}"
            result = _do_request(url, headers, opener)
            error_class, message = (None, None)
            if result["transport_error"] is None and result["status"] != 200:
                error_class, message = _classify_error(result["status"], result["body"])
            page_attempts.append({
                "attempt": attempt_index + 1,
                "status": result["status"],
                "transport_error": result["transport_error"],
                "error_message_class": error_class,
                "error_message": message,
            })
            final = result
            if not (result["retryable"] and attempt_index < MAX_ATTEMPTS_PER_PAGE - 1):
                break
        attempts.extend(page_attempts)
        page_ok = final is not None and final["transport_error"] is None and final["status"] == 200
        if not page_ok:
            ok = bool(bars)  # partial pages already collected still count as ok
            break
        try:
            parsed = json.loads(final["body"])
            if not isinstance(parsed, dict):
                fail("ALPACA_BARS_SHAPE_INVALID", f"{feed}:{symbol}")
            rows = parsed.get("bars") or []
            if not isinstance(rows, list):
                fail("ALPACA_BARS_SHAPE_INVALID", f"{feed}:{symbol}")
            for row in rows:
                if not (isinstance(row, dict) and "t" in row and "c" in row and "v" in row):
                    continue
                bars.append({
                    "date": str(row["t"])[:10],
                    "close": Decimal(str(row["c"])),
                    "volume": Decimal(str(row["v"])),
                })
            page_token = parsed.get("next_page_token")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            ok = bool(bars)
            break
        if not page_token:
            break
    else:
        fail("MAX_PAGES_EXCEEDED", f"{feed}:{symbol}")
    return {"feed": feed, "ok": ok, "attempts": attempts, "bars": bars}


# ─────────────────────────────────────────────────────────────────────────
# Derived 20-session observation (aggregate only -- no raw bar survives this)
# ─────────────────────────────────────────────────────────────────────────

def _format_usd(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return format(quantized, "f")


def build_observation(feed: str, bars: list[dict], now: dt.datetime, source_ref: str) -> Optional[dict]:
    eligible = sorted(
        (bar for bar in bars if is_bar_fresh_enough(dt.date.fromisoformat(bar["date"]), now)),
        key=lambda bar: bar["date"],
    )
    recent = eligible[-LIQ.REQUIRED_SESSION_WINDOW:]
    if not recent:
        return None
    values = [bar["close"] * bar["volume"] for bar in recent]
    avg = sum(values) / Decimal(len(values))
    return {
        "feed": feed,
        "avg_traded_value_usd": _format_usd(avg),
        "last_close_usd": _format_usd(recent[-1]["close"]),
        "session_count": len(recent),
        "window_end": recent[-1]["date"],
        "notional_formula": "CLOSE_TIMES_VOLUME",
        "source_ref": source_ref,
    }


# ─────────────────────────────────────────────────────────────────────────
# Per-symbol orchestration
# ─────────────────────────────────────────────────────────────────────────

def collect_symbol(
    symbol: str, window: tuple[str, str], credentials: dict, opener, budget: RequestBudget, now: dt.datetime
) -> dict:
    feed_reports: dict[str, dict] = {}
    observations: dict[str, Optional[dict]] = {}
    for feed in FEEDS:
        report = request_symbol_feed(symbol, feed, window, credentials, opener, budget)
        feed_reports[feed] = {
            "ok": report["ok"],
            "attempts": report["attempts"],
            "bar_count": len(report["bars"]),
        }
        observations[feed] = (
            build_observation(feed, report["bars"], now, f"alpaca_historical_daily_bars:{feed}")
            if report["ok"]
            else None
        )
        if feed == "sip" and observations["sip"] is not None and observations["sip"]["session_count"] >= LIQ.REQUIRED_SESSION_WINDOW:
            break  # SIP already gives a full window; IEX fallback is unneeded
    return {"feed_reports": feed_reports, "observations": observations}


def run_collection(
    credentials: dict,
    *,
    opener=None,
    clock=None,
    contract: Optional[dict] = None,
    policy: Optional[dict] = None,
    listing: Optional[dict] = None,
    listing_root: Path = LISTING.ROOT,
) -> dict:
    opener = _default_open if opener is None else opener
    clock = (lambda: dt.datetime.now(dt.timezone.utc)) if clock is None else clock
    symbols, symbol_source = resolve_symbols(contract)
    now = clock()
    window = session_window(now)
    budget = RequestBudget(limit=len(symbols) * len(FEEDS) * MAX_PAGES_PER_REQUEST * MAX_ATTEMPTS_PER_PAGE)
    policy = LIQ.load_policy() if policy is None else policy

    # 2026-09-15 CIO wiring: point-in-time exchange-listing lookup against
    # the already-committed Nasdaq Trader Symbol Directory capture (see
    # universe/us_listing_lookup.py's docstring for the exact packet
    # selection rule, its instant guard, and field definitions). ``now``
    # (an instant, not just a date) is passed through so a same-day
    # packet captured after this very run is never used. ``listing`` lets
    # a caller inject a fully synthetic result (tests); otherwise this is
    # the one real, point-in-time-correct lookup for this run.
    listing = (
        LISTING.resolve_listing(symbols, now, root=listing_root)
        if listing is None
        else listing
    )

    per_symbol: dict[str, dict] = {}
    for symbol in symbols:
        collected = collect_symbol(symbol, window, credentials, opener, budget, now)
        observations = collected["observations"]
        listing_row = listing["per_symbol"].get(symbol, {"status": None, "reasons": ["SYMBOL_ABSENT_FROM_LISTING_PACKET"]})
        result = LIQ.evaluate_symbol_liquidity(
            symbol,
            observations.get("sip"),
            observations.get("iex"),
            policy,
            exchange_listing_status=listing_row["status"],
        )
        per_symbol[symbol] = {
            "symbol": symbol,
            "source_feed": result["source_feed_used"],
            "avg_traded_value_usd": result["avg_traded_value_usd"],
            "last_close_usd": result["last_close_usd"],
            "session_count": result["session_count"],
            "window_end": result["window_end"],
            "notional_formula": "CLOSE_TIMES_VOLUME",
            "status": result["status"],
            "volume_status": result["volume_status"],
            "price_status": result["price_status"],
            "otc_exclusion_status": result["otc_exclusion_status"],
            "listing_status": listing_row["status"],
            "listing_reasons": listing_row["reasons"],
            "reasons": result["reasons"],
            "sip_feed_ok": collected["feed_reports"]["sip"]["ok"],
            "sip_bar_count": collected["feed_reports"]["sip"]["bar_count"],
            "iex_feed_ok": collected["feed_reports"].get("iex", {}).get("ok"),
            "iex_bar_count": collected["feed_reports"].get("iex", {}).get("bar_count"),
        }

    policy_diagnostic = LIQ.describe_policy()
    summary = {
        "schema_version": SCHEMA_VERSION,
        "rule_id": LIQ.RULE_ID,
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol_source": symbol_source,
        "symbol_count": len(symbols),
        "window": {
            "start": window[0],
            "end": window[1],
            "freshness_embargo_minutes": FRESHNESS_EMBARGO_MINUTES,
        },
        "min_session_window": LIQ.REQUIRED_SESSION_WINDOW,
        "policy_id": policy["policy_id"] if policy else None,
        "policy_status": policy_diagnostic["status"],
        "policy_problems": policy_diagnostic["problems"],
        "listing_packet_date": listing["packet_date"],
        "listing_packet_path": listing["packet_path"],
        "listing_packet_sha256": listing["packet_sha256"],
        "listing_packet_age_days": listing["listing_packet_age_days"],
        "request_budget": budget.limit,
        "requests_used": budget.used,
        "per_symbol": per_symbol,
    }
    assert_no_raw_bar_fields(summary)
    return summary


# ─────────────────────────────────────────────────────────────────────────
# Structural guard: never let a raw per-day bar reach the output
# ─────────────────────────────────────────────────────────────────────────

def assert_no_raw_bar_fields(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in FORBIDDEN_RAW_KEYS:
                fail("FORBIDDEN_RAW_FIELD_IN_OUTPUT", f"{path}.{key}")
            if isinstance(key, str) and _DATE_KEY_RE.match(key):
                fail("FORBIDDEN_DATE_KEYED_MAP", f"{path}.{key}")
            assert_no_raw_bar_fields(sub, f"{path}.{key}")
    elif isinstance(value, list):
        numeric = [item for item in value if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if len(numeric) == len(value) and len(value) > MAX_PLAIN_NUMERIC_LIST_LENGTH:
            fail("FORBIDDEN_NUMERIC_SERIES", f"{path} len={len(value)}")
        for index, item in enumerate(value):
            assert_no_raw_bar_fields(item, f"{path}[{index}]")


# ─────────────────────────────────────────────────────────────────────────
# Entry point (writes derived JSON inside the repo -- this IS committed)
# ─────────────────────────────────────────────────────────────────────────

def _credentials_from_env() -> dict:
    import os

    return {
        "key": os.environ.get("ALPACA_MARKET_DATA_API_KEY", "").strip(),
        "secret": os.environ.get("ALPACA_MARKET_DATA_API_SECRET", "").strip(),
    }


def write_outputs(summary: dict, *, dated_dir: Path = DATED_DIR, latest_path: Path = LATEST_PATH) -> tuple[Path, Path]:
    assert_no_raw_bar_fields(summary)
    date = summary["generated_at_utc"][:10]
    dated_path = dated_dir / f"{date}.json"
    dated_dir.mkdir(parents=True, exist_ok=True)
    dated_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        points_to = str(dated_path.relative_to(ROOT))
    except ValueError:
        points_to = str(dated_path)
    latest = {
        "schema_version": "latest_pointer/1",
        "points_to": points_to,
        "generated_at_utc": summary["generated_at_utc"],
        "policy_status": summary["policy_status"],
        "symbol_count": summary["symbol_count"],
    }
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dated_path, latest_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Alpaca SIP daily-bar liquidity collector")
    parser.add_argument("--dated-dir", type=Path, default=DATED_DIR)
    parser.add_argument("--latest-path", type=Path, default=LATEST_PATH)
    args = parser.parse_args(argv)
    credentials = _credentials_from_env()
    missing = [name for name, value in credentials.items() if not value]
    if missing:
        print(f"STOP: missing credentials: {','.join(sorted(missing))}")
        return 2
    summary = run_collection(credentials)
    dated_path, latest_path = write_outputs(summary, dated_dir=args.dated_dir, latest_path=args.latest_path)
    print(json.dumps({
        "dated_path": str(dated_path),
        "latest_path": str(latest_path),
        "symbol_count": summary["symbol_count"],
        "policy_status": summary["policy_status"],
        "requests_used": summary["requests_used"],
        "request_budget": summary["request_budget"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AlpacaSipDailyBarsError as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
