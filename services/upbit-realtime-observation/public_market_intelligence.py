#!/usr/bin/env python3
"""Pure helpers for staged Upbit KRW observation and finalized-candle facts.

This module contains no credentials, order endpoints, sockets, or repository
writes.  ``service.py`` supplies public REST responses and this module turns
them into reference-only price intelligence.  The results are explicitly not
candidate, PAPER, or order authority.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics


UTC = dt.timezone.utc
SUPPORTED_MINUTE_UNITS = (15, 60, 240)
REFERENCE_TIMEFRAMES = ("15m", "1h", "4h", "1d")
KRW_MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{1,20}$")


class PublicMarketIntelligenceError(ValueError):
    pass


def _aware(value: dt.datetime, code: str) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise PublicMarketIntelligenceError(code)
    return value.astimezone(UTC)


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise PublicMarketIntelligenceError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PublicMarketIntelligenceError(code) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_krw_market_catalog(payload: object) -> tuple[list[str], dict[str, dict]]:
    """Validate Upbit's public market list and retain warning metadata."""
    if not isinstance(payload, list):
        raise PublicMarketIntelligenceError("UPBIT_MARKET_CATALOG_NOT_LIST")
    markets: list[str] = []
    metadata: dict[str, dict] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        market = row.get("market")
        if not isinstance(market, str) or not KRW_MARKET_RE.fullmatch(market):
            continue
        base = market[4:]
        if not base or market in metadata:
            continue
        markets.append(market)
        metadata[market] = {
            "market_warning": row.get("market_warning") if isinstance(row.get("market_warning"), str) else "UNKNOWN",
            "korean_name": row.get("korean_name") if isinstance(row.get("korean_name"), str) else None,
            "english_name": row.get("english_name") if isinstance(row.get("english_name"), str) else None,
        }
    markets.sort()
    if not markets:
        raise PublicMarketIntelligenceError("UPBIT_KRW_MARKET_CATALOG_EMPTY")
    return markets, metadata


def _finalized_rows(rows: object, *, now: dt.datetime, duration: dt.timedelta) -> list[dict]:
    now = _aware(now, "CANDLE_NOW_NAIVE")
    if not isinstance(rows, list):
        return []
    accepted: list[tuple[dt.datetime, dict]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            opened_at = _parse_utc(row.get("candle_date_time_utc"), "CANDLE_OPEN_TIME_INVALID")
        except PublicMarketIntelligenceError:
            continue
        close = row.get("trade_price")
        if not isinstance(close, (int, float)) or isinstance(close, bool) or close <= 0:
            continue
        # Upbit REST returns the currently forming candle first.  Never use
        # it for trend/RS; only intervals whose close boundary has passed.
        if opened_at + duration > now:
            continue
        accepted.append((opened_at, row))
    accepted.sort(key=lambda item: item[0])
    return [row for _, row in accepted]


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _timeframe_fact(rows: list[dict], *, duration: dt.timedelta, now: dt.datetime) -> dict:
    if not rows:
        return {"status": "MISSING", "as_of_utc": None, "close": None, "age_seconds": None}
    row = rows[-1]
    opened_at = _parse_utc(row["candle_date_time_utc"], "CANDLE_OPEN_TIME_INVALID")
    closed_at = opened_at + duration
    age = max(0.0, (now - closed_at).total_seconds())
    # A quiet market can legitimately omit an interval.  The generous 3x
    # window distinguishes that from an indefinitely old reference.
    return {
        "status": "FRESH" if age <= duration.total_seconds() * 3 else "STALE",
        "as_of_utc": _iso(closed_at),
        "close": float(row["trade_price"]),
        "age_seconds": round(age, 3),
    }


def analyze_finalized_candles(
    market: str,
    *,
    day_rows: object,
    h4_rows: object,
    h1_rows: object,
    m15_rows: object,
    now: dt.datetime,
) -> dict:
    """Return reference-only multi-timeframe trend and 20-day return facts."""
    now = _aware(now, "CANDLE_ANALYSIS_NOW_NAIVE")
    day = _finalized_rows(day_rows, now=now, duration=dt.timedelta(days=1))
    h4 = _finalized_rows(h4_rows, now=now, duration=dt.timedelta(minutes=240))
    h1 = _finalized_rows(h1_rows, now=now, duration=dt.timedelta(minutes=60))
    m15 = _finalized_rows(m15_rows, now=now, duration=dt.timedelta(minutes=15))
    day_closes = [float(row["trade_price"]) for row in day]
    h4_closes = [float(row["trade_price"]) for row in h4]
    daily_ema20 = _ema(day_closes, 20)
    h4_ema20 = _ema(h4_closes, 20)
    h4_prior_ema20 = _ema(h4_closes[:-1], 20) if len(h4_closes) > 20 else None

    if daily_ema20 is None or h4_ema20 is None or h4_prior_ema20 is None or not day_closes or not h4_closes:
        trend = {
            "status": "UNKNOWN",
            "reason": "FINALIZED_CANDLE_HISTORY_INSUFFICIENT",
            "daily_close_gt_ema20": None,
            "h4_ema20_rising": None,
        }
    else:
        daily_up = day_closes[-1] > daily_ema20
        h4_up = h4_ema20 > h4_prior_ema20
        if daily_up and h4_up:
            status, reason = "POSITIVE", "DAILY_ABOVE_EMA20_AND_H4_EMA20_RISING"
        elif not daily_up and not h4_up:
            status, reason = "NEGATIVE", "DAILY_BELOW_EMA20_AND_H4_EMA20_FALLING"
        else:
            status, reason = "NEUTRAL", "DAILY_AND_H4_DIRECTION_MIXED"
        trend = {
            "status": status,
            "reason": reason,
            "daily_close_gt_ema20": daily_up,
            "h4_ema20_rising": h4_up,
            "daily_ema20": round(daily_ema20, 12),
            "h4_ema20": round(h4_ema20, 12),
        }

    return_20d_pct = None
    if len(day_closes) >= 21 and day_closes[-21] > 0:
        return_20d_pct = (day_closes[-1] / day_closes[-21] - 1.0) * 100.0

    return {
        "market": market,
        "status": "AVAILABLE" if trend["status"] != "UNKNOWN" else "PARTIAL",
        "calculated_at_utc": _iso(now),
        "reference_only": True,
        "finalized_candles": {
            "15m": _timeframe_fact(m15, duration=dt.timedelta(minutes=15), now=now),
            "1h": _timeframe_fact(h1, duration=dt.timedelta(minutes=60), now=now),
            "4h": _timeframe_fact(h4, duration=dt.timedelta(minutes=240), now=now),
            "1d": _timeframe_fact(day, duration=dt.timedelta(days=1), now=now),
        },
        "trend": trend,
        "relative_strength": {
            "status": "PENDING_CROSS_SECTION" if return_20d_pct is not None else "UNKNOWN",
            "return_20d_pct": None if return_20d_pct is None else round(return_20d_pct, 6),
            "vs_btc_20d_pct": None,
            "vs_peer_median_20d_pct": None,
            "reason": None if return_20d_pct is not None else "FINALIZED_DAILY_HISTORY_INSUFFICIENT",
        },
    }


def complete_cross_section_relative_strength(rows: dict[str, dict]) -> dict[str, dict]:
    """Add BTC and peer-median 20-day RS without changing authority."""
    returns = {
        market: row.get("relative_strength", {}).get("return_20d_pct")
        for market, row in rows.items()
        if isinstance(row, dict)
    }
    valid = [float(value) for value in returns.values() if isinstance(value, (int, float))]
    btc = returns.get("KRW-BTC")
    peer_median = statistics.median(valid) if valid else None
    result: dict[str, dict] = {}
    for market, row in rows.items():
        copied = dict(row)
        rs = dict(copied.get("relative_strength") or {})
        value = returns.get(market)
        if isinstance(value, (int, float)) and isinstance(btc, (int, float)) and peer_median is not None:
            rs.update({
                "status": "AVAILABLE",
                "vs_btc_20d_pct": round(float(value) - float(btc), 6),
                "vs_peer_median_20d_pct": round(float(value) - float(peer_median), 6),
                "reason": "FINALIZED_20D_RETURN_CROSS_SECTION",
            })
        elif rs.get("status") == "PENDING_CROSS_SECTION":
            rs.update({"status": "UNKNOWN", "reason": "BTC_OR_PEER_REFERENCE_UNAVAILABLE"})
        copied["relative_strength"] = rs
        result[market] = copied
    return result
