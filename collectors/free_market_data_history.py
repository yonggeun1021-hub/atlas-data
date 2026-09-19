#!/usr/bin/env python3
"""Receive a historical RANGE of the US regime score inputs (Alpaca + ALFRED).

Why this is a separate module and a separate entry point
-------------------------------------------------------
``collectors/free_market_data.py`` captures ONE current observation per run and
publishes it to ``evidence/free_market_data/{derived,raw,fred/raw}`` plus
``data/latest_free_market_data.json``. That path runs on a schedule and the
five-day clock depends on it, so it is not modified and not reused as an entry
point here: this module has its own ``main()``, writes ONLY under
``evidence/free_market_data/history/`` (mechanically enforced by
``_safe_history_path``), and never touches ``data/latest_free_market_data.json``
or the daily ``derived``/``raw``/``fred/raw`` trees.

What it adds that the daily path cannot do
------------------------------------------
1. **Alpaca ``start``/``end`` with paging.** The daily path asks for a fixed
   trailing window (``observed_at - 180 days``, ``limit`` 240), which is why the
   committed evidence is a ~124-session rolling window. Here the window is an
   explicit argument and every ``next_page_token`` is followed, so an arbitrary
   historical span can be received.
2. **ALFRED vintages instead of the latest revision.** The daily path asks FRED
   for current values only. A score for a past date may not be built from
   today's revision: on 2026-09-19 the committed ``TOTBKCR`` weekly change of
   4,553 could not be reproduced from any current-revision week (that week now
   reads 4,129.5), while ``WRESBAL`` reproduced exactly. So every observation is
   received with its full realtime (availability) window --
   ``realtime_start``..``realtime_end`` -- and ``observations_available_at()``
   selects, for an as-of date, only rows that had already been PUBLISHED by that
   date. A value whose ``available_from`` is later than the as-of date is
   future information and is excluded.

Conventions kept, deliberately unchanged
----------------------------------------
* ``deterministic_gzip`` from ``collectors/fred_vix_provenance`` (reached via
  ``free_market_data.FRED_PROVENANCE``) encodes every retained response, so the
  bytes reproduce across Python releases and host OSes.
* Every retained object is content-addressed by the sha256 of the exact
  response bytes and published with ``_write_once``: an identical capture is a
  no-op, conflicting bytes at one address fail closed. Nothing is ever
  overwritten -- the retention marker is ``APPEND_ONLY_CONTENT_ADDRESSED``, the
  same marker the daily Alpaca store carries.
* Per-request receipts record the response sha256 and the retrieval time.
  Credentials are never logged: the FRED key is appended to the URL only at
  request time and the recorded ``params`` never contain it, while the Alpaca
  key and secret travel only in Alpaca's own auth headers.

Authority
---------
Receiving only. No axis definition, threshold, symbol list, score or regime
label is computed, proposed or written here; the contract and the policy files
are read, never edited. ``config/free_market_data_contract.json`` remains the
single source of the symbol lists and of the FRED series.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def _load_daily_collector():
    """Import the daily collector as a library, without running its main()."""
    path = ROOT / "collectors" / "free_market_data.py"
    spec = importlib.util.spec_from_file_location("atlas_free_market_data", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("FREE_MARKET_DATA_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DAILY = _load_daily_collector()
FreeMarketDataError = DAILY.FreeMarketDataError
canonical_bytes = DAILY.canonical_bytes
sha256_bytes = DAILY.sha256_bytes
deterministic_gzip = DAILY.FRED_PROVENANCE.deterministic_gzip

HISTORY_STORE = "evidence/free_market_data/history"
HISTORY_RAW_RETENTION = "APPEND_ONLY_CONTENT_ADDRESSED"
RECEIPT_SCHEMA = "free_market_data_history_receipt/1"
ALPACA_PAGE_SCHEMA = "alpaca_daily_bars_range_page/1"
ALPACA_SERIES_SCHEMA = "alpaca_daily_bars_range_series/1"
ALFRED_OBSERVATIONS_SCHEMA = "fred_alfred_vintage_observations_page/1"
ALFRED_VINTAGE_DATES_SCHEMA = "fred_alfred_vintage_dates/1"
ALFRED_AVAILABILITY_SCHEMA = "fred_alfred_availability_series/1"

FRED_API = "https://api.stlouisfed.org/fred"
ALPACA_API = "https://data.alpaca.markets/v2"
# The widest realtime window ALFRED accepts. Asking for it returns one row per
# (observation date, revision), each carrying the dates between which that
# value was the published one -- which is exactly the availability record a
# point-in-time replay needs.
FRED_REALTIME_MIN = "1776-07-04"
FRED_REALTIME_MAX = "9999-12-31"
FRED_PAGE_LIMIT = 100000
FRED_VINTAGE_DATES_LIMIT = 10000
ALPACA_PAGE_LIMIT = 10000
MAX_PAGES = 64
MEASURE_REQUEST_BUDGET = 5

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SERIES_ID = re.compile(r"^[A-Z0-9]{1,32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

# A score day needs a 20-session return, so 21 bars must precede the first
# scored session; ten non-overlapping 20-session windows are 200 scored
# sessions. Both numbers are reported by measure mode, never enforced here.
LEAD_BARS_REQUIRED = 21
WINDOW_SESSIONS = 20
WINDOW_TARGET = 10

# The two weekly liquidity series are read as a change between consecutive
# observations, so the first scored day in the window needs the weekly
# observation BEFORE the window start to already be on hand. The FRED
# observation window is therefore extended backwards by this many calendar days
# -- the same kind of lookback margin the daily path uses (180 days) and well
# more than the two weeks a single weekly step needs. The requested window and
# the applied window are both recorded, so the extension is never implicit.
FRED_OBSERVATION_LEAD_DAYS = 60


class RequestBudget:
    """Fail closed rather than quietly exceed an approved request count."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.spent = 0

    def spend(self) -> None:
        if self.limit is not None and self.spent >= self.limit:
            raise FreeMarketDataError(f"REQUEST_BUDGET_EXCEEDED:{self.limit}")
        self.spent += 1


def _utc_now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def _stamp(moment: dt.datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_date(value: object, code: str) -> str:
    if not isinstance(value, str) or DATE.fullmatch(value) is None:
        raise FreeMarketDataError(code)
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise FreeMarketDataError(code) from exc
    return value


def _check_series_id(value: object) -> str:
    if not isinstance(value, str) or SERIES_ID.fullmatch(value) is None:
        raise FreeMarketDataError("FRED_SERIES_ID_INVALID")
    return value


def score_symbols(contract: dict) -> list[str]:
    """Symbols the five US axes actually consume, read from the contract only.

    TREND + BREADTH + LEADERSHIP + the leadership benchmark. The contract's
    wider ``alpaca.symbols`` list (22, including seven single stocks) is the
    approved universe and is not expanded; this is a subset of it, checked.
    """
    alpaca = contract["alpaca"]
    proxy = alpaca["current_proxy_axes"]
    wanted = set(alpaca["trend_symbols"])
    wanted |= set(proxy["breadth_symbols"])
    wanted |= set(proxy["leadership_symbols"])
    wanted.add(proxy["leadership_benchmark"])
    if not wanted <= set(alpaca["symbols"]):
        raise FreeMarketDataError("HISTORY_SYMBOLS_OUTSIDE_CONTRACT")
    return sorted(wanted)


# ---------------------------------------------------------------------------
# Content-addressed, append-only publication (same convention as the daily
# Alpaca raw store; a different subtree so the daily store's object shape stays
# homogeneous and the daily workflow's `git add` paths are untouched).
# ---------------------------------------------------------------------------

def build_raw_object(
    relative_dir: str,
    filename: str,
    schema_version: str,
    raw: bytes,
) -> dict:
    """Address exact response bytes by content; never by capture time.

    The manifest describes the BYTES only -- schema, retention, response hash --
    exactly as the daily Alpaca raw store's manifest does. Nothing about the
    request (symbol, feed, window, page index) belongs here: two different
    requests can legitimately return byte-identical responses (an empty window,
    for one), and putting request fields in a content-addressed manifest would
    make that legitimate case collide and fail closed. The request that produced
    each object is recorded in the run receipt instead, where it belongs.
    """
    if not isinstance(raw, bytes):
        raise FreeMarketDataError("HISTORY_RAW_BYTES_INVALID")
    raw_sha256 = sha256_bytes(raw)
    raw_gzip_bytes = deterministic_gzip(raw)
    base = f"{HISTORY_STORE}/{relative_dir}/{raw_sha256}"
    manifest = {
        "schema_version": schema_version,
        "raw_retention": HISTORY_RAW_RETENTION,
        "raw_response_sha256": raw_sha256,
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    return {
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "raw_gzip_bytes": raw_gzip_bytes,
        "pointer": {
            "raw_retention": HISTORY_RAW_RETENTION,
            "raw_response_sha256": raw_sha256,
            "raw_path": f"{base}/{filename}",
            "raw_file_sha256": sha256_bytes(raw_gzip_bytes),
            "manifest_path": f"{base}/manifest.json",
            "manifest_file_sha256": sha256_bytes(manifest_bytes),
        },
    }


def build_derived_object(relative_dir: str, filename: str, payload: dict) -> dict:
    """Content-address a derived packet by its own canonical payload hash."""
    body = {**payload}
    body["payload_sha256"] = sha256_bytes(canonical_bytes(payload))
    body_bytes = json.dumps(
        body, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    base = f"{HISTORY_STORE}/{relative_dir}/{body['payload_sha256']}"
    return {
        "payload": body,
        "payload_bytes": body_bytes,
        "pointer": {
            "payload_sha256": body["payload_sha256"],
            "payload_path": f"{base}/{filename}",
            "payload_file_sha256": sha256_bytes(body_bytes),
        },
    }


def _safe_history_path(root: Path, value: object, suffix: str) -> Path:
    """Refuse any path that is not inside this module's own history subtree."""
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        raise FreeMarketDataError("HISTORY_PATH_INVALID")
    if not value.startswith(f"{HISTORY_STORE}/") or not value.endswith(suffix):
        raise FreeMarketDataError("HISTORY_PATH_INVALID")
    resolved_root = Path(root).resolve()
    resolved = (Path(root) / value).resolve()
    if resolved_root not in resolved.parents:
        raise FreeMarketDataError("HISTORY_PATH_INVALID")
    return resolved


def _write_once(path: Path, data: bytes, code: str) -> None:
    """Publish an immutable object without ever overwriting one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise FreeMarketDataError(code)
        return
    path.write_bytes(data)


def publish_raw_object(root: Path, obj: dict, filename: str) -> dict:
    pointer = obj["pointer"]
    raw_path = _safe_history_path(root, pointer["raw_path"], f"/{filename}")
    manifest_path = _safe_history_path(root, pointer["manifest_path"], "/manifest.json")
    _write_once(raw_path, obj["raw_gzip_bytes"], "HISTORY_RAW_OBJECT_CONFLICT")
    _write_once(manifest_path, obj["manifest_bytes"], "HISTORY_RAW_OBJECT_CONFLICT")
    return pointer


def publish_derived_object(root: Path, obj: dict, filename: str) -> dict:
    pointer = obj["pointer"]
    path = _safe_history_path(root, pointer["payload_path"], f"/{filename}")
    _write_once(path, obj["payload_bytes"], "HISTORY_DERIVED_OBJECT_CONFLICT")
    return pointer


# ---------------------------------------------------------------------------
# HTTP, with a receipt per request and never a credential in a receipt
# ---------------------------------------------------------------------------

def _request(
    url_base: str,
    params: dict,
    *,
    secret_params: dict | None = None,
    headers: dict | None = None,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> bytes:
    getter = getter or DAILY._get
    if budget is not None:
        budget.spend()
    query = dict(params)
    if secret_params:
        query.update(secret_params)
    requested_at = _utc_now()
    raw = getter(url_base + "?" + urllib.parse.urlencode(query), headers or {})
    received_at = _utc_now()
    if receipts is not None:
        receipts.append({
            "endpoint": url_base,
            # `params` deliberately excludes every credential-bearing key, so a
            # committed receipt can never leak the FRED key that the URL needs.
            "params": {key: str(value) for key, value in sorted(params.items())},
            "requested_at_utc": _stamp(requested_at),
            "received_at_utc": _stamp(received_at),
            "response_sha256": sha256_bytes(raw),
            "response_bytes": len(raw),
        })
    return raw


def _json(raw: bytes, code: str) -> dict:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FreeMarketDataError(code) from exc
    if not isinstance(body, dict):
        raise FreeMarketDataError(code)
    return body


# ---------------------------------------------------------------------------
# ALFRED: vintage dates, vintage observations, availability selection
# ---------------------------------------------------------------------------

def fetch_fred_vintage_dates(
    api_key: str,
    series_id: str,
    *,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> tuple[bytes, dict]:
    """How far back ALFRED actually holds vintages for one series."""
    series_id = _check_series_id(series_id)
    params = {
        "series_id": series_id,
        "file_type": "json",
        "sort_order": "asc",
        "limit": FRED_VINTAGE_DATES_LIMIT,
    }
    raw = _request(
        f"{FRED_API}/series/vintagedates",
        params,
        secret_params={"api_key": api_key},
        getter=getter,
        budget=budget,
        receipts=receipts,
    )
    body = _json(raw, f"FRED_VINTAGE_DATES_JSON_INVALID:{series_id}")
    dates = body.get("vintage_dates")
    if not isinstance(dates, list) or not dates:
        raise FreeMarketDataError(f"FRED_VINTAGE_DATES_MISSING:{series_id}")
    for value in dates:
        _check_date(value, f"FRED_VINTAGE_DATE_INVALID:{series_id}")
    count = body.get("count")
    summary = {
        "series_id": series_id,
        "vintage_date_count_reported": count if isinstance(count, int) else None,
        "vintage_date_count_returned": len(dates),
        "earliest_vintage_date": dates[0],
        "latest_vintage_date": dates[-1],
        "truncated": isinstance(count, int) and count > len(dates),
    }
    return raw, summary


def parse_fred_vintage_page(series_id: str, raw: bytes) -> tuple[list[dict], object]:
    """Normalize one ALFRED observations page, preserving availability verbatim.

    Used both when receiving and when replaying retained bytes, so a stored
    page always re-derives the rows it was recorded as producing.
    """
    series_id = _check_series_id(series_id)
    body = _json(raw, f"FRED_VINTAGE_OBSERVATIONS_JSON_INVALID:{series_id}")
    observations = body.get("observations")
    if not isinstance(observations, list):
        raise FreeMarketDataError(f"FRED_VINTAGE_OBSERVATIONS_MISSING:{series_id}")
    rows = []
    for row in observations:
        if not isinstance(row, dict):
            raise FreeMarketDataError(f"FRED_VINTAGE_ROW_INVALID:{series_id}")
        rows.append({
            "observation_date": _check_date(
                row.get("date"), f"FRED_VINTAGE_ROW_DATE_INVALID:{series_id}"
            ),
            "value": str(row.get("value")),
            # Availability, preserved exactly as ALFRED reported it.
            "available_from": _check_date(
                row.get("realtime_start"),
                f"FRED_VINTAGE_ROW_REALTIME_START_INVALID:{series_id}",
            ),
            "available_to": _check_date(
                row.get("realtime_end"),
                f"FRED_VINTAGE_ROW_REALTIME_END_INVALID:{series_id}",
            ),
        })
    return rows, body.get("count")


def fetch_fred_vintage_observations(
    api_key: str,
    series_id: str,
    observation_start: str,
    observation_end: str,
    output_type: int,
    *,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> tuple[list[bytes], list[dict]]:
    """Every (observation, revision) row ALFRED holds for a window.

    Each returned row carries ``realtime_start``/``realtime_end``: the dates
    between which that value was the published one. Those two fields ARE the
    availability record -- they are preserved verbatim and are what
    ``observations_available_at`` filters on.
    """
    series_id = _check_series_id(series_id)
    observation_start = _check_date(observation_start, "FRED_OBSERVATION_START_INVALID")
    observation_end = _check_date(observation_end, "FRED_OBSERVATION_END_INVALID")
    pages: list[bytes] = []
    rows: list[dict] = []
    offset = 0
    for _page in range(MAX_PAGES):
        params = {
            "series_id": series_id,
            "file_type": "json",
            "observation_start": observation_start,
            "observation_end": observation_end,
            "realtime_start": FRED_REALTIME_MIN,
            "realtime_end": FRED_REALTIME_MAX,
            "output_type": output_type,
            "sort_order": "asc",
            "limit": FRED_PAGE_LIMIT,
            "offset": offset,
        }
        raw = _request(
            f"{FRED_API}/series/observations",
            params,
            secret_params={"api_key": api_key},
            getter=getter,
            budget=budget,
            receipts=receipts,
        )
        pages.append(raw)
        page_rows, count = parse_fred_vintage_page(series_id, raw)
        rows.extend(page_rows)
        if not isinstance(count, int) or offset + len(page_rows) >= count:
            break
        if not page_rows:
            break
        offset += len(page_rows)
    else:
        raise FreeMarketDataError(f"FRED_VINTAGE_MAX_PAGES_EXCEEDED:{series_id}")
    if not rows:
        raise FreeMarketDataError(f"FRED_VINTAGE_ROWS_EMPTY:{series_id}")
    return pages, rows


def fetch_fred_series_metadata(
    api_key: str,
    series_id: str,
    *,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> tuple[bytes, dict]:
    """Units/frequency, normalized with the daily path's own unit table."""
    series_id = _check_series_id(series_id)
    raw = _request(
        f"{FRED_API}/series",
        {"series_id": series_id, "file_type": "json"},
        secret_params={"api_key": api_key},
        getter=getter,
        budget=budget,
        receipts=receipts,
    )
    body = _json(raw, f"FRED_METADATA_JSON_INVALID:{series_id}")
    rows = body.get("seriess")
    if not isinstance(rows, list) or len(rows) != 1:
        raise FreeMarketDataError(f"FRED_METADATA_INVALID:{series_id}")
    metadata = rows[0]
    units = metadata.get("units")
    unit_base = units.split(",", 1)[0].strip() if isinstance(units, str) else None
    normalized_unit = normalization_factor = None
    if unit_base in DAILY.FRED_LIQUIDITY_UNITS:
        normalized_unit, factor = DAILY.FRED_LIQUIDITY_UNITS[unit_base]
        normalization_factor = DAILY._decimal_text(factor)
    return raw, {
        "series_id": series_id,
        "title": metadata.get("title"),
        "frequency": metadata.get("frequency"),
        "source_unit": units,
        "normalized_unit": normalized_unit,
        "normalization_factor": normalization_factor,
    }


def observations_available_at(rows: list[dict], as_of_date: str) -> list[dict]:
    """Rows that had already been PUBLISHED on ``as_of_date``.

    A row is visible when ``available_from <= as_of_date <= available_to``.
    Anything whose ``available_from`` is later is future information and is
    dropped -- that is the whole point of receiving vintages rather than the
    current revision. Missing values (FRED's ``.``) are dropped as well, so the
    caller sees only usable observations. Result is ordered oldest first.
    """
    as_of = _check_date(as_of_date, "AS_OF_DATE_INVALID")
    visible: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FreeMarketDataError("FRED_AVAILABILITY_ROW_INVALID")
        available_from = _check_date(
            row.get("available_from"), "FRED_AVAILABILITY_ROW_INVALID"
        )
        available_to = _check_date(
            row.get("available_to"), "FRED_AVAILABILITY_ROW_INVALID"
        )
        if not (available_from <= as_of <= available_to):
            continue
        value = row.get("value")
        if value in (None, ".", ""):
            continue
        observation_date = _check_date(
            row.get("observation_date"), "FRED_AVAILABILITY_ROW_INVALID"
        )
        if observation_date > as_of:
            # Defensive: ALFRED should never publish a future observation date
            # with an earlier availability date, but a score day must not read
            # one if it ever does.
            continue
        prior = visible.get(observation_date)
        if prior is not None and prior != {
            "observation_date": observation_date,
            "value": str(value),
            "available_from": available_from,
            "available_to": available_to,
        }:
            raise FreeMarketDataError(
                f"FRED_AVAILABILITY_AMBIGUOUS:{observation_date}"
            )
        visible[observation_date] = {
            "observation_date": observation_date,
            "value": str(value),
            "available_from": available_from,
            "available_to": available_to,
        }
    return [visible[key] for key in sorted(visible)]


def build_availability_series(
    series_id: str,
    observation_start: str,
    observation_end: str,
    rows: list[dict],
    metadata: dict,
    page_pointers: list[dict],
) -> dict:
    vintages = sorted({row["available_from"] for row in rows})
    observed = sorted({row["observation_date"] for row in rows})
    return {
        "schema_version": ALFRED_AVAILABILITY_SCHEMA,
        "series_id": _check_series_id(series_id),
        "source_scope": "ALFRED_VINTAGE_SERIES_API",
        "vintage_semantics": (
            "EVERY_ROW_CARRIES_ITS_OWN_PUBLICATION_WINDOW_NOT_LATEST_REVISION"
        ),
        "observation_start": observation_start,
        "observation_end": observation_end,
        "realtime_start": FRED_REALTIME_MIN,
        "realtime_end": FRED_REALTIME_MAX,
        "metadata": metadata,
        "row_count": len(rows),
        "observation_date_count": len(observed),
        "distinct_availability_date_count": len(vintages),
        "earliest_availability_date": vintages[0] if vintages else None,
        "latest_availability_date": vintages[-1] if vintages else None,
        "earliest_observation_date": observed[0] if observed else None,
        "latest_observation_date": observed[-1] if observed else None,
        "rows": rows,
        "response_pointers": page_pointers,
        "warnings": [
            "VINTAGE_ROWS_ARE_NOT_THE_CURRENT_REVISION",
            "AVAILABILITY_MUST_GATE_ANY_AS_OF_READ",
            "RECEIVED_EVIDENCE_ONLY_NO_SCORE_OR_REGIME_DERIVED",
        ],
    }


# ---------------------------------------------------------------------------
# Alpaca: an explicit start/end window, every page followed
# ---------------------------------------------------------------------------

def normalize_daily_bar(symbol: str, bar: object) -> dict:
    """Validate and normalize one daily bar exactly as the daily path does.

    The rule is deliberately identical to
    ``free_market_data.fetch_alpaca_daily_bars``: every OHLCV field present,
    numeric, non-negative, positive OHLC, high >= max(open, close),
    low <= min(open, close), and a non-empty timestamp string. The regression
    cross-checks this function against that one on the same bytes rather than
    trusting the two descriptions to agree.
    """
    if not isinstance(bar, dict) or not all(
        field in bar for field in ("o", "h", "l", "c", "v", "t")
    ):
        raise FreeMarketDataError(f"ALPACA_DAILY_BAR_FIELDS_MISSING:{symbol}")
    values = [bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]]
    try:
        numeric = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise FreeMarketDataError(
            f"ALPACA_DAILY_BAR_VALUES_INVALID:{symbol}"
        ) from exc
    if (
        not all(value >= 0 for value in numeric)
        or min(numeric[:4]) <= 0
        or numeric[1] < max(numeric[0], numeric[3])
        or numeric[2] > min(numeric[0], numeric[3])
    ):
        raise FreeMarketDataError(f"ALPACA_DAILY_OHLC_INVALID:{symbol}")
    if not isinstance(bar["t"], str) or not bar["t"]:
        raise FreeMarketDataError(f"ALPACA_DAILY_TIME_INVALID:{symbol}")
    return {
        "symbol": symbol,
        "opened_at": bar["t"],
        "open": str(bar["o"]),
        "high": str(bar["h"]),
        "low": str(bar["l"]),
        "close": str(bar["c"]),
        "volume": str(bar["v"]),
    }


def fetch_alpaca_daily_bars_range(
    key: str,
    secret: str,
    symbol: str,
    start: str,
    end: str,
    *,
    feed: str,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
    page_limit: int = ALPACA_PAGE_LIMIT,
) -> tuple[list[bytes], list[dict], dict]:
    """Daily bars for one symbol over an EXPLICIT [start, end] window.

    Unlike the daily path (fixed trailing 180 days, ``limit`` 240) the window is
    an argument, and ``next_page_token`` is followed rather than assumed absent,
    so a truncated window can no longer masquerade as the provider's limit.
    """
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z.\-]{0,15}", symbol):
        raise FreeMarketDataError("ALPACA_SYMBOL_INVALID")
    start = _check_date(start, "ALPACA_RANGE_START_INVALID")
    end = _check_date(end, "ALPACA_RANGE_END_INVALID")
    if start > end:
        raise FreeMarketDataError("ALPACA_RANGE_ORDER_INVALID")
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
    }
    pages: list[bytes] = []
    bars: list[dict] = []
    page_token = None
    for _page in range(MAX_PAGES):
        params = {
            "timeframe": "1Day",
            "start": start,
            "end": end,
            "limit": page_limit,
            "adjustment": "raw",
            "feed": feed,
            "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        raw = _request(
            f"{ALPACA_API}/stocks/{symbol}/bars",
            params,
            headers=headers,
            getter=getter,
            budget=budget,
            receipts=receipts,
        )
        pages.append(raw)
        body = _json(raw, f"ALPACA_DAILY_JSON_INVALID:{symbol}")
        rows = body.get("bars")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise FreeMarketDataError(f"ALPACA_DAILY_BARS_MISSING:{symbol}")
        for bar in rows:
            bars.append(normalize_daily_bar(symbol, bar))
        page_token = body.get("next_page_token")
        if not page_token:
            break
    else:
        raise FreeMarketDataError(f"ALPACA_DAILY_MAX_PAGES_EXCEEDED:{symbol}")
    bars.sort(key=lambda row: row["opened_at"])
    sessions = [row["opened_at"][:10] for row in bars]
    summary = {
        "symbol": symbol,
        "feed": feed,
        "requested_start": start,
        "requested_end": end,
        "page_count": len(pages),
        "bar_count": len(bars),
        "first_session_date": sessions[0] if sessions else None,
        "last_session_date": sessions[-1] if sessions else None,
        "distinct_session_count": len(set(sessions)),
    }
    return pages, bars, summary


def build_session_series(summary: dict, bars: list[dict]) -> dict:
    return {
        "schema_version": ALPACA_SERIES_SCHEMA,
        "source_scope": (
            "ALPACA_IEX_DAILY_BARS_PARTIAL_EXCHANGE_REFERENCE"
            if summary["feed"] == "iex"
            else f"ALPACA_{summary['feed'].upper()}_DAILY_BARS"
        ),
        "timeframe": "1Day",
        "adjustment": "raw",
        **summary,
        "bars": bars,
        "warnings": [
            "RECEIVED_EVIDENCE_ONLY_NO_SCORE_OR_REGIME_DERIVED",
            "NOT_FULL_US_SECURITY_LEVEL_BREADTH",
        ],
    }


def window_feasibility(bar_count: int) -> dict:
    """How many scored sessions and 20-session windows a bar count supports."""
    scored = max(0, bar_count - LEAD_BARS_REQUIRED)
    windows = scored // WINDOW_SESSIONS
    return {
        "bar_count": bar_count,
        "lead_bars_required": LEAD_BARS_REQUIRED,
        "scored_sessions_possible": scored,
        "window_sessions": WINDOW_SESSIONS,
        "non_overlapping_windows_possible": windows,
        "window_target": WINDOW_TARGET,
        "meets_window_target": windows >= WINDOW_TARGET,
        "bars_required_for_window_target": (
            LEAD_BARS_REQUIRED + WINDOW_TARGET * WINDOW_SESSIONS
        ),
    }


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def measure(
    fred_key: str,
    alpaca_key: str,
    alpaca_secret: str,
    contract: dict,
    start: str,
    end: str,
    *,
    probe_symbol: str = "SPY",
    getter=None,
) -> dict:
    """Log-only feasibility measurement. Writes nothing, commits nothing.

    Five requests at most: ALFRED ``vintagedates`` for the three FRED series
    (how far back vintages actually exist), one Alpaca ``start``/``end`` request
    on ``feed=iex`` (how many bars really come back), and one on ``feed=sip``
    (whether SIP is available on this credential at all).
    """
    budget = RequestBudget(MEASURE_REQUEST_BUDGET)
    receipts: list[dict] = []
    result = {
        "mode": "measure",
        "writes_nothing": True,
        "measured_at_utc": _stamp(_utc_now()),
        "requested_window": {"start": start, "end": end},
        "request_budget": MEASURE_REQUEST_BUDGET,
        "fred_vintages": {},
        "alpaca": {},
    }
    for series_id in contract["fred"]["series"]:
        try:
            _, summary = fetch_fred_vintage_dates(
                fred_key, series_id, getter=getter, budget=budget, receipts=receipts
            )
            result["fred_vintages"][series_id] = {"status": "OBSERVED", **summary}
        except FreeMarketDataError as exc:
            result["fred_vintages"][series_id] = {
                "status": "UNAVAILABLE",
                "series_id": series_id,
                "error": str(exc),
            }
    for feed in ("iex", "sip"):
        try:
            _, bars, summary = fetch_alpaca_daily_bars_range(
                alpaca_key,
                alpaca_secret,
                probe_symbol,
                start,
                end,
                feed=feed,
                getter=getter,
                budget=budget,
                receipts=receipts,
            )
            result["alpaca"][feed] = {
                "status": "OBSERVED",
                **summary,
                "feasibility": window_feasibility(len(bars)),
            }
        except FreeMarketDataError as exc:
            result["alpaca"][feed] = {
                "status": "UNAVAILABLE",
                "symbol": probe_symbol,
                "feed": feed,
                "error": str(exc),
            }
    result["requests_made"] = budget.spent
    result["request_receipts"] = receipts
    iex = result["alpaca"].get("iex", {})
    result["conclusion"] = {
        "score_symbol_count": len(score_symbols(contract)),
        "iex_meets_window_target": bool(
            iex.get("feasibility", {}).get("meets_window_target")
        ),
        "sip_available": result["alpaca"].get("sip", {}).get("status") == "OBSERVED",
        "note": (
            "A full receive is only worth running if a feed here reaches the "
            "bars_required_for_window_target count."
        ),
    }
    return result


def fetch(
    root: Path,
    fred_key: str,
    alpaca_key: str,
    alpaca_secret: str,
    contract: dict,
    start: str,
    end: str,
    *,
    feed: str = "iex",
    symbols: list[str] | None = None,
    getter=None,
) -> dict:
    """Receive the range and publish it append-only under the history store."""
    if feed != contract["alpaca"]["feed"]:
        # The contract pins iex; receiving under another feed would make the
        # committed evidence disagree with the contract that describes it.
        raise FreeMarketDataError("ALPACA_FEED_NOT_CONTRACTED")
    start = _check_date(start, "RANGE_START_INVALID")
    end = _check_date(end, "RANGE_END_INVALID")
    if start > end:
        raise FreeMarketDataError("RANGE_ORDER_INVALID")
    symbols = symbols if symbols is not None else score_symbols(contract)
    receipts: list[dict] = []
    captured_at = _utc_now()

    alpaca_records = []
    for symbol in symbols:
        pages, bars, summary = fetch_alpaca_daily_bars_range(
            alpaca_key,
            alpaca_secret,
            symbol,
            start,
            end,
            feed=feed,
            getter=getter,
            receipts=receipts,
        )
        page_pointers = []
        for index, raw in enumerate(pages):
            obj = build_raw_object(
                "alpaca/daily_bars",
                "alpaca_daily_bars_page.json.gz",
                ALPACA_PAGE_SCHEMA,
                raw,
            )
            pointer = publish_raw_object(root, obj, "alpaca_daily_bars_page.json.gz")
            page_pointers.append({
                **pointer,
                "request": {
                    "symbol": symbol,
                    "feed": feed,
                    "timeframe": "1Day",
                    "adjustment": "raw",
                    "requested_start": start,
                    "requested_end": end,
                    "page_index": index,
                },
            })
        series = build_derived_object(
            f"alpaca/series/{symbol}",
            "alpaca_daily_bars_series.json",
            {
                **build_session_series(summary, bars),
                "response_pointers": page_pointers,
            },
        )
        series_pointer = publish_derived_object(
            root, series, "alpaca_daily_bars_series.json"
        )
        alpaca_records.append({
            **summary,
            "feasibility": window_feasibility(len(bars)),
            "response_pointers": page_pointers,
            "series_pointer": series_pointer,
        })

    fred_records = []
    for series_id in contract["fred"]["series"]:
        vintage_raw, vintage_summary = fetch_fred_vintage_dates(
            fred_key, series_id, getter=getter, receipts=receipts
        )
        vintage_pointer = publish_raw_object(
            root,
            build_raw_object(
                f"fred/alfred/{series_id}/vintage_dates",
                "fred_alfred_vintage_dates.json.gz",
                ALFRED_VINTAGE_DATES_SCHEMA,
                vintage_raw,
            ),
            "fred_alfred_vintage_dates.json.gz",
        )
        metadata_raw, metadata = fetch_fred_series_metadata(
            fred_key, series_id, getter=getter, receipts=receipts
        )
        metadata_pointer = publish_raw_object(
            root,
            build_raw_object(
                f"fred/alfred/{series_id}/metadata",
                "fred_series_metadata.json.gz",
                "fred_series_metadata/1",
                metadata_raw,
            ),
            "fred_series_metadata.json.gz",
        )
        fred_start = (
            dt.date.fromisoformat(start)
            - dt.timedelta(days=FRED_OBSERVATION_LEAD_DAYS)
        ).isoformat()
        pages, rows = fetch_fred_vintage_observations(
            fred_key,
            series_id,
            fred_start,
            end,
            contract["fred"]["output_type"],
            getter=getter,
            receipts=receipts,
        )
        page_pointers = []
        for index, raw in enumerate(pages):
            pointer = publish_raw_object(
                root,
                build_raw_object(
                    f"fred/alfred/{series_id}/observations",
                    "fred_alfred_observations.json.gz",
                    ALFRED_OBSERVATIONS_SCHEMA,
                    raw,
                ),
                "fred_alfred_observations.json.gz",
            )
            page_pointers.append({
                **pointer,
                "request": {
                    "series_id": series_id,
                    "observation_start": fred_start,
                    "observation_end": end,
                    "realtime_start": FRED_REALTIME_MIN,
                    "realtime_end": FRED_REALTIME_MAX,
                    "page_index": index,
                },
            })
        availability = build_derived_object(
            f"fred/alfred/{series_id}/availability",
            "fred_alfred_availability.json",
            {
                **build_availability_series(
                    series_id, fred_start, end, rows, metadata, page_pointers
                ),
                "requested_window_start": start,
                "observation_lead_days": FRED_OBSERVATION_LEAD_DAYS,
            },
        )
        availability_pointer = publish_derived_object(
            root, availability, "fred_alfred_availability.json"
        )
        fred_records.append({
            "series_id": series_id,
            "observation_start_applied": fred_start,
            "observation_end_applied": end,
            "observation_lead_days": FRED_OBSERVATION_LEAD_DAYS,
            "vintage_dates": vintage_summary,
            "vintage_dates_pointer": vintage_pointer,
            "metadata": metadata,
            "metadata_pointer": metadata_pointer,
            "row_count": len(rows),
            "observation_pointers": page_pointers,
            "availability_pointer": availability_pointer,
        })

    receipt = build_derived_object(
        "receipts",
        "manifest.json",
        {
            "schema_version": RECEIPT_SCHEMA,
            "contract_version": contract["contract_version"],
            "mode": "fetch",
            "captured_at_utc": _stamp(captured_at),
            "requested_window": {"start": start, "end": end},
            "alpaca": {
                "feed": feed,
                "symbol_count": len(symbols),
                "symbols": list(symbols),
                "series": alpaca_records,
            },
            "fred": {
                "source_scope": "ALFRED_VINTAGE_SERIES_API",
                "series": fred_records,
            },
            "request_receipts": receipts,
            "store": HISTORY_STORE,
            "raw_retention": HISTORY_RAW_RETENTION,
            "daily_paths_written": [],
            "authority": contract["authority"],
            "warnings": [
                "RECEIVED_EVIDENCE_ONLY_NO_SCORE_OR_REGIME_DERIVED",
                "VINTAGE_ROWS_ARE_NOT_THE_CURRENT_REVISION",
            ],
        },
    )
    receipt_pointer = publish_derived_object(root, receipt, "manifest.json")
    return {
        "status": "PASS",
        "mode": "fetch",
        "receipt_path": receipt_pointer["payload_path"],
        "receipt_sha256": receipt_pointer["payload_sha256"],
        "requests_made": len(receipts),
        "alpaca_bar_counts": {
            record["symbol"]: record["bar_count"] for record in alpaca_records
        },
        "fred_row_counts": {
            record["series_id"]: record["row_count"] for record in fred_records
        },
    }


def verify(root: Path) -> dict:
    """Replay every retained history object before anything is committed.

    The same discipline the daily path applies in
    ``free_market_data.validate_alpaca_daily_evidence``: the stored bytes are
    decompressed and re-derived, and a derived packet must reproduce exactly
    from the responses it points at. A packet that cannot be replayed is a
    failure, not a warning.
    """
    root = Path(root)
    store = root / HISTORY_STORE
    if not store.is_dir():
        raise FreeMarketDataError("HISTORY_STORE_MISSING")
    raw_checked = 0
    for path in sorted(store.rglob("*.json.gz")):
        stored = path.read_bytes()
        try:
            raw = gzip.decompress(stored)
        except (OSError, ValueError, EOFError) as exc:
            raise FreeMarketDataError(
                f"HISTORY_RAW_UNREADABLE:{path.name}"
            ) from exc
        raw_sha256 = sha256_bytes(raw)
        if path.parent.name != raw_sha256:
            raise FreeMarketDataError("HISTORY_RAW_ADDRESS_MISMATCH")
        if stored != deterministic_gzip(raw):
            raise FreeMarketDataError("HISTORY_RAW_GZIP_NOT_DETERMINISTIC")
        manifest_path = path.parent / "manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        if manifest.get("raw_response_sha256") != raw_sha256:
            raise FreeMarketDataError("HISTORY_RAW_MANIFEST_MISMATCH")
        relative_dir = path.parent.parent.relative_to(store).as_posix()
        expected = build_raw_object(
            relative_dir, path.name, manifest["schema_version"], raw
        )
        if expected["manifest_bytes"] != manifest_bytes:
            raise FreeMarketDataError("HISTORY_RAW_REDERIVATION_MISMATCH")
        raw_checked += 1

    derived_checked = 0
    replayed_series = 0
    replayed_availability = 0
    for path in sorted(store.rglob("*.json")):
        body = json.loads(path.read_bytes())
        if not isinstance(body, dict):
            raise FreeMarketDataError("HISTORY_OBJECT_UNCLASSIFIED")
        if "raw_response_sha256" in body and "payload_sha256" not in body:
            continue  # a raw-object manifest, already replayed above
        if "payload_sha256" not in body:
            raise FreeMarketDataError("HISTORY_OBJECT_UNCLASSIFIED")
        payload = {key: value for key, value in body.items() if key != "payload_sha256"}
        if body["payload_sha256"] != sha256_bytes(canonical_bytes(payload)):
            raise FreeMarketDataError("HISTORY_DERIVED_PAYLOAD_SHA_MISMATCH")
        if path.parent.name != body["payload_sha256"]:
            raise FreeMarketDataError("HISTORY_DERIVED_ADDRESS_MISMATCH")
        if path.read_bytes() != json.dumps(
            body, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8") + b"\n":
            raise FreeMarketDataError("HISTORY_DERIVED_BYTES_NOT_CANONICAL")
        derived_checked += 1
        if body.get("schema_version") == ALPACA_SERIES_SCHEMA:
            rebuilt = []
            for pointer in body.get("response_pointers") or []:
                page = _read_raw_object(root, pointer, "alpaca_daily_bars_page.json.gz")
                page_body = _json(page, "HISTORY_ALPACA_PAGE_JSON_INVALID")
                for bar in page_body.get("bars") or []:
                    rebuilt.append(normalize_daily_bar(body["symbol"], bar))
            rebuilt.sort(key=lambda row: row["opened_at"])
            if rebuilt != body.get("bars"):
                raise FreeMarketDataError("HISTORY_ALPACA_SERIES_REPLAY_MISMATCH")
            replayed_series += 1
        if body.get("schema_version") == ALFRED_AVAILABILITY_SCHEMA:
            rebuilt = []
            for pointer in body.get("response_pointers") or []:
                page = _read_raw_object(
                    root, pointer, "fred_alfred_observations.json.gz"
                )
                rows, _count = parse_fred_vintage_page(body["series_id"], page)
                rebuilt.extend(rows)
            if rebuilt != body.get("rows"):
                raise FreeMarketDataError("HISTORY_ALFRED_AVAILABILITY_REPLAY_MISMATCH")
            latest = body.get("latest_availability_date")
            if latest and not observations_available_at(body["rows"], latest):
                raise FreeMarketDataError("HISTORY_ALFRED_AVAILABILITY_EMPTY_AT_LATEST")
            replayed_availability += 1
    return {
        "status": "PASS",
        "mode": "verify",
        "raw_objects_replayed": raw_checked,
        "derived_objects_replayed": derived_checked,
        "alpaca_series_replayed": replayed_series,
        "alfred_availability_series_replayed": replayed_availability,
    }


def _read_raw_object(root: Path, pointer: object, filename: str) -> bytes:
    """Resolve a pinned pointer to the exact bytes it addresses."""
    if not isinstance(pointer, dict):
        raise FreeMarketDataError("HISTORY_POINTER_INVALID")
    for key in ("raw_response_sha256", "raw_file_sha256", "manifest_file_sha256"):
        value = pointer.get(key)
        if not isinstance(value, str) or HEX64.fullmatch(value) is None:
            raise FreeMarketDataError("HISTORY_POINTER_INVALID")
    path = _safe_history_path(root, pointer.get("raw_path"), f"/{filename}")
    stored = path.read_bytes()
    if sha256_bytes(stored) != pointer["raw_file_sha256"]:
        raise FreeMarketDataError("HISTORY_POINTER_FILE_BYTES_MISMATCH")
    try:
        raw = gzip.decompress(stored)
    except (OSError, ValueError, EOFError) as exc:
        raise FreeMarketDataError("HISTORY_POINTER_RAW_UNREADABLE") from exc
    if sha256_bytes(raw) != pointer["raw_response_sha256"]:
        raise FreeMarketDataError("HISTORY_POINTER_RAW_HASH_MISMATCH")
    return raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Receive a historical range of the US regime score inputs. "
            "Separate from the daily capture in collectors/free_market_data.py."
        )
    )
    parser.add_argument(
        "--mode", choices=("measure", "fetch", "verify"), required=True
    )
    parser.add_argument("--start", help="window start, YYYY-MM-DD")
    parser.add_argument("--end", help="window end, YYYY-MM-DD")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--feed", default="iex")
    parser.add_argument("--probe-symbol", default="SPY")
    args = parser.parse_args(argv)

    if args.mode == "verify":
        # Replay only: reads the committed store, needs no credential and makes
        # no request.
        print(json.dumps(verify(args.root), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not args.start or not args.end:
        raise SystemExit("HISTORY_WINDOW_REQUIRED")
    fred_key = os.getenv("FRED_API_KEY", "").strip()
    # ★ 2026-08-23 cutover: the account/trading Alpaca credential
    # (ALPACA_API_KEY/ALPACA_API_SECRET) lives ONLY in the private
    # atlas-private-evidence repo. This module reads the dedicated
    # market-data-only names and has no code path that reads the other pair.
    alpaca_key = os.getenv("ALPACA_MARKET_DATA_API_KEY", "").strip()
    alpaca_secret = os.getenv("ALPACA_MARKET_DATA_API_SECRET", "").strip()
    if not fred_key or not alpaca_key or not alpaca_secret:
        raise SystemExit("FREE_MARKET_DATA_HISTORY_CREDENTIALS_MISSING")
    contract = DAILY.load_contract(
        args.root / "config" / "free_market_data_contract.json"
    )
    start = _check_date(args.start, "RANGE_START_INVALID")
    end = _check_date(args.end, "RANGE_END_INVALID")
    if args.mode == "measure":
        report = measure(
            fred_key,
            alpaca_key,
            alpaca_secret,
            contract,
            start,
            end,
            probe_symbol=args.probe_symbol,
        )
    else:
        report = fetch(
            args.root,
            fred_key,
            alpaca_key,
            alpaca_secret,
            contract,
            start,
            end,
            feed=args.feed,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
