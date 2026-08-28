#!/usr/bin/env python3
"""P4-07 candle-finalization boundary primitive.

Nothing in this repository, before this module, has an explicit "is this
sub-daily candle actually closed?" concept -- ``crypto_breadth_contract.json``
and ``upbit_market_capture_contract.json`` both hardcode "always drop the
first/last row" as a *daily*-only idiom. P4-07 generalizes that idiom into a
standalone, timeframe-aware boundary check across 15m/1h/4h/1d:

    A candle is FINALIZED iff its close time has already elapsed as of the
    evaluation instant (``as_of``), inclusive. A candle whose close time has
    not yet elapsed is IN_PROGRESS and must never be treated as usable
    decision evidence.

Every function in this module is a pure function of its arguments: no
wall-clock, no random value, no network call. Given the same raw candle rows
and the same ``as_of``, the output is byte-identical on every call --
P9-06 (real-time WebSocket layer), P5-08, P5-09, and P8-16 all depend on
this determinism.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable


UTC = dt.timezone.utc

# unit_seconds: the candle's fixed duration. Upbit's minute-candle endpoints
# are addressed by ``unit`` (an integer count of minutes); the daily endpoint
# has no ``unit`` parameter at all -- ``kind`` distinguishes the two request
# shapes for the capture layer.
TIMEFRAMES = {
    "15m": {"unit_seconds": 15 * 60, "kind": "minutes", "upbit_unit": 15},
    "1h": {"unit_seconds": 60 * 60, "kind": "minutes", "upbit_unit": 60},
    "4h": {"unit_seconds": 240 * 60, "kind": "minutes", "upbit_unit": 240},
    "1d": {"unit_seconds": 24 * 60 * 60, "kind": "days", "upbit_unit": None},
}


class CandleFinalizationError(ValueError):
    """Fail-closed P4-07 candle-finalization contract violation."""


def _require_timeframe(timeframe: str) -> dict:
    spec = TIMEFRAMES.get(timeframe)
    if spec is None:
        raise CandleFinalizationError(f"TIMEFRAME_UNKNOWN:{timeframe}")
    return spec


def _require_aware(value: dt.datetime, code: str) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise CandleFinalizationError(code)
    return value.astimezone(UTC)


def parse_candle_open_time(raw_candle: dict) -> dt.datetime:
    """Upbit's ``candle_date_time_utc`` is the candle's OPEN time (start of
    the bucket), formatted without a timezone suffix -- always UTC.
    """
    if not isinstance(raw_candle, dict):
        raise CandleFinalizationError("CANDLE_ROW_NOT_OBJECT")
    value = raw_candle.get("candle_date_time_utc")
    if not isinstance(value, str):
        raise CandleFinalizationError("CANDLE_OPEN_TIME_MISSING")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise CandleFinalizationError(f"CANDLE_OPEN_TIME_INVALID:{value}") from exc
    return parsed.replace(tzinfo=UTC)


def candle_close_time(open_time: dt.datetime, timeframe: str) -> dt.datetime:
    spec = _require_timeframe(timeframe)
    open_time = _require_aware(open_time, "NAIVE_OPEN_TIME_REJECTED")
    return open_time + dt.timedelta(seconds=spec["unit_seconds"])


def is_candle_finalized(open_time: dt.datetime, timeframe: str, as_of: dt.datetime) -> bool:
    """True iff the candle's close time has already elapsed as of ``as_of``
    (inclusive boundary: ``close_time <= as_of`` is finalized). This is the
    single boundary check every timeframe's finalization decision reduces
    to -- see ``classify_candles`` below for the batch/dedup wrapper.
    """
    as_of = _require_aware(as_of, "NAIVE_AS_OF_REJECTED")
    return candle_close_time(open_time, timeframe) <= as_of


REQUIRED_CANDLE_FIELDS = (
    "candle_date_time_utc", "opening_price", "high_price", "low_price",
    "trade_price", "candle_acc_trade_price", "candle_acc_trade_volume",
)


def _validate_candle_fields(row: dict) -> None:
    for field in REQUIRED_CANDLE_FIELDS:
        if row.get(field) is None:
            raise CandleFinalizationError(f"CANDLE_FIELD_MISSING:{field}")


def classify_candles(raw_candles: Iterable[dict], timeframe: str, as_of: dt.datetime) -> dict:
    """Deterministically parse, validate, dedupe (first-occurrence-wins,
    same discipline as ``upbit_market_capture.py::krw_markets``), sort, and
    partition raw Upbit candle rows into FINALIZED vs IN_PROGRESS.

    Fails closed (raises) on:
    - a row missing any required OHLCV field (UNKNOWN input, never guessed).
    - a candle whose OPEN time is itself later than ``as_of`` -- that is not
      merely "in progress", it is future-dated/corrupt input and must never
      be silently accepted.
    """
    _require_timeframe(timeframe)
    as_of = _require_aware(as_of, "NAIVE_AS_OF_REJECTED")
    by_open_time: dict[dt.datetime, dict] = {}
    duplicate_row_count = 0
    for row in raw_candles:
        _validate_candle_fields(row)
        open_time = parse_candle_open_time(row)
        if open_time > as_of:
            raise CandleFinalizationError(f"FUTURE_DATED_CANDLE:{open_time.isoformat()}")
        if open_time in by_open_time:
            duplicate_row_count += 1
            continue
        by_open_time[open_time] = row

    finalized = []
    in_progress = []
    for open_time in sorted(by_open_time):
        row = by_open_time[open_time]
        close_time = candle_close_time(open_time, timeframe)
        entry = {"open_time": open_time, "close_time": close_time, "raw": row}
        if close_time <= as_of:
            finalized.append(entry)
        else:
            in_progress.append(entry)
    return {
        "timeframe": timeframe,
        "as_of": as_of,
        "finalized": finalized,
        "in_progress": in_progress,
        "duplicate_row_count": duplicate_row_count,
    }


def expected_open_times(timeframe: str, window_start: dt.datetime, window_end: dt.datetime) -> list[dt.datetime]:
    """Every timeframe-aligned candle open time in ``[window_start,
    window_end)``, aligned to the Unix epoch (Upbit candle boundaries are
    UTC-epoch-aligned for every timeframe used here).
    """
    spec = _require_timeframe(timeframe)
    window_start = _require_aware(window_start, "NAIVE_WINDOW_START_REJECTED")
    window_end = _require_aware(window_end, "NAIVE_WINDOW_END_REJECTED")
    if window_end <= window_start:
        raise CandleFinalizationError("WINDOW_INVALID")
    step = dt.timedelta(seconds=spec["unit_seconds"])
    epoch = dt.datetime(1970, 1, 1, tzinfo=UTC)
    offset = (window_start - epoch) % step
    aligned_start = window_start if offset == dt.timedelta(0) else window_start + (step - offset)
    times = []
    current = aligned_start
    while current < window_end:
        times.append(current)
        current += step
    return times


def detect_gaps(
    present_open_times: Iterable[dt.datetime], timeframe: str,
    window_start: dt.datetime, window_end: dt.datetime,
) -> list[dt.datetime]:
    """Every timeframe-aligned open time in the window that is NOT present
    in the already-committed evidence -- e.g. a day the capture cron failed.
    """
    present = set(present_open_times)
    return [t for t in expected_open_times(timeframe, window_start, window_end) if t not in present]


def group_contiguous_gaps(missing_open_times: Iterable[dt.datetime], timeframe: str) -> list[dict]:
    """Group missing open times into the smallest number of contiguous
    backfill windows -- one re-query per window rather than one per missing
    candle.
    """
    spec = _require_timeframe(timeframe)
    step = dt.timedelta(seconds=spec["unit_seconds"])
    ordered = sorted(set(missing_open_times))
    windows: list[dict] = []
    for open_time in ordered:
        if windows and open_time - windows[-1]["to_open_time"] == step:
            windows[-1]["to_open_time"] = open_time
        else:
            windows.append({"from_open_time": open_time, "to_open_time": open_time})
    return windows


def merge_finalized_no_overwrite(committed: dict, new_finalized: Iterable[dict]) -> dict:
    """Merge freshly (re-)captured finalized candle entries (as produced by
    ``classify_candles``) into an already-committed ``{open_time: entry}``
    mapping.

    An already-committed open time is never silently overwritten:
    - identical raw bytes for an already-present open time is a harmless
      idempotent re-commit (no-op).
    - DIFFERENT raw bytes for an already-present open time fails closed --
      out-of-order/late-arriving evidence for a past window must never
      silently rewrite committed history.
    Only genuinely missing open times are added.
    """
    merged = dict(committed)
    added = []
    for entry in new_finalized:
        open_time = entry["open_time"]
        if open_time in merged:
            if merged[open_time]["raw"] != entry["raw"]:
                raise CandleFinalizationError(f"COMMITTED_CANDLE_MISMATCH:{open_time.isoformat()}")
            continue
        merged[open_time] = entry
        added.append(open_time)
    return {"merged": merged, "added_open_times": sorted(added)}
