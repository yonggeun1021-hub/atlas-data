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
import time
import urllib.error
import urllib.parse
import urllib.request


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
# ★ 2026-09-19. ``/1`` receipts put the run's wall-clock times INSIDE the
# content-addressed payload, so two byte-identical receives produced two
# different receipt addresses and a re-run appended a receipt that carried no
# new evidence. Worse, whether a regression saw that depended on how fast the
# machine was: both runs inside one second hashed the same and the check passed.
# Green or red decided by execution speed is the most expensive kind of defect
# in this repository, so the identity of a receipt is now what was RECEIVED, and
# the retrieval times live in their own object next to it (``/2``).
#
# Already-committed ``/1`` receipts are read, never rewritten: ``verify``
# accepts both versions and the version number is what distinguishes them.
RECEIPT_SCHEMA = "free_market_data_history_receipt/2"
RECEIPT_TIMING_SCHEMA = "free_market_data_history_retrieval_timing/1"
RECEIPT_SCHEMA_VERSIONS_ACCEPTED = (
    "free_market_data_history_receipt/1",
    "free_market_data_history_receipt/2",
)
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

# ★ 2026-09-19, run 35432930212. mode=fetch died on the FIRST
# ``series/observations`` request with a bare ``HTTP_ERROR:400`` -- the shared
# ``free_market_data._get`` converts urllib's HTTPError into a code and DROPS the
# response body, and FRED puts the actual reason in that body. The same run's
# ``vintagedates`` requests had already succeeded with the same key, so the key
# was never the problem; the request shape or its size was, and nothing in the
# log said which. Two things follow, both implemented below.
#
# 1. This module now has its OWN getter that keeps the (masked, truncated) 400
#    body. ``free_market_data._get`` is deliberately left exactly as it is: the
#    daily collector runs on it.
# 2. ``measure`` no longer probes only ``vintagedates``. It probes the
#    ``series/observations`` endpoint itself with the vintage parameters over one
#    chunk-sized window, so the shape is proven before a full receive is started.
#    Not probing the endpoint that does the work was the design hole.
HTTP_ERROR_BODY_MAX = 400

# Key under which a request record carries its retrieval times in memory. It is
# stripped before the record reaches any content-addressed payload.
RETRIEVAL_TIMING_KEY = "retrieval_timing"

# ★ 2026-09-19, run 35435347342. The measure probe added by the previous change
# found the real cause of the fetch 400, and it was none of the three suspected
# ones. All five request shapes were rejected with the SAME provider message:
#
#   "There are 3936 vintage dates in the specified real-time period:
#    1776-07-04 to 9999-12-31. This exceeds the maximum number of vintage dates
#    allowed for this file type (2000)."
#
# The constraint is the number of VINTAGES inside the requested real-time
# period, not the request's parameter shape and not the response's size. The
# axis that has to be split is therefore the realtime axis, which used to be one
# fixed window per series. ``build_vintage_window_plan`` splits it; the
# observation axis keeps its own chunking below for paging safety.
#
# The observation window is ALWAYS received in chunks of this many days, per
# series and per realtime window. Chunks are inclusive and strictly adjacent --
# ``fred_observation_chunks`` yields [start, end] ranges with no gap and no
# overlap -- so merging them can neither lose nor duplicate a row, which
# ``_merge_vintage_rows`` enforces and the regression pins. The default is
# deliberately larger than a year: the observation axis was never the failing
# constraint (the provider named the vintage count), and multiplying it against
# the realtime windows costs requests for nothing. It is still small enough that
# a multi-year window produces more than one chunk, so the merge path runs in
# production rather than only in the regression.
FRED_CHUNK_DAYS = 1830

# Candidate request shapes for ``series/observations``, most explicit first.
# Every shape keeps ``realtime_start``/``realtime_end`` at the ALFRED extremes:
# a shape that narrowed or dropped them would silently return the CURRENT
# revision and destroy the point-in-time guarantee, so no such shape exists here
# and the regression asserts it never will. The shapes differ only in optional
# parameters whose values FRED already defaults to, so the semantics of every
# one of them are identical -- the ladder finds which one the provider ACCEPTS,
# it does not choose between different data.
#
# ``output_type: False`` omits the parameter; FRED's documented default for
# ``output_type`` is 1, the same value the contract pins, so omitting it asks for
# the same thing.
FRED_OBSERVATION_SHAPES = (
    {"id": "explicit_sorted_max_limit", "sort_order": "asc",
     "limit": FRED_PAGE_LIMIT, "output_type": True},
    {"id": "explicit_sorted_small_limit", "sort_order": "asc",
     "limit": 10000, "output_type": True},
    {"id": "explicit_unsorted_small_limit", "sort_order": None,
     "limit": 10000, "output_type": True},
    {"id": "explicit_no_limit", "sort_order": None,
     "limit": None, "output_type": True},
    {"id": "provider_defaults", "sort_order": None,
     "limit": None, "output_type": False},
)

# Vintage dates per realtime window. The provider stated its own maximum as
# 2000 on 2026-09-19, so this is a 4x margin. Two reasons for the margin rather
# than sitting just under the stated cap:
#
# * Batching by vintage COUNT (not by a fixed date window) means a series that
#   accumulates vintages gets MORE windows, never more vintages per window. The
#   plan therefore cannot rot as the series grows -- which a date-based split
#   would, and that would be a time bomb.
# * If the provider ever lowers the cap, ``build_vintage_window_plan`` compares
#   this size against the number parsed out of the provider's own message and
#   fails closed (``FRED_VINTAGE_BATCH_SIZE_EXCEEDS_PROVIDER_LIMIT``) instead of
#   discovering it mid-receive. measure reports that comparison before any
#   receive is started.
FRED_VINTAGE_BATCH_SIZE = 500

# The provider states its own limit in the 400 body; parsed, never assumed.
FRED_VINTAGE_LIMIT_PATTERN = re.compile(
    r"maximum number of vintage dates allowed[^(]*\((\d+)\)", re.IGNORECASE
)

# FRED rate-limits per key. A full receive now makes tens of observation
# requests, so they are paced rather than issued as fast as the runner can.
FRED_REQUEST_INTERVAL_SECONDS = 0.6

# measure mode request budget, counted exactly (see measure_request_budget):
#   N  ALFRED vintagedates, one per contract FRED series
#   2  Alpaca window probe (feed=iex, feed=sip)
#   1..len(FRED_OBSERVATION_SHAPES)  observations shape ladder on the first
#      series, stopping at the first shape the provider accepts
#   N-1  the accepted shape confirmed on the remaining series
# The committed contract has N = 3 series, so the worst case is
# 3 + 2 + 5 + 2 = 12 and the expected case (first shape accepted) is 8. The
# constant is the number for the committed contract; the budget actually applied
# is derived from the contract, so adding a series cannot silently overrun it.
MEASURE_REQUEST_BUDGET = 3 + 2 + len(FRED_OBSERVATION_SHAPES) + 2

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


_REGISTERED_SECRETS: set[str] = set()


def register_secrets(*values: object) -> None:
    """Remember credential values so no diagnostic can ever echo one."""
    for value in values:
        if isinstance(value, str) and len(value) >= 8:
            _REGISTERED_SECRETS.add(value)


def mask_secrets(text: str) -> str:
    """Remove anything credential-shaped from a provider diagnostic.

    A FRED error body can quote the request, and the FRED key must live in the
    query string, so the body is scrubbed three ways before it is ever allowed
    into an exception message: the ``api_key`` parameter value, every registered
    credential value, and any bare 32-character lower-case hex/alphanumeric token
    (the shape of a FRED key) are replaced.
    """
    if not isinstance(text, str):
        return ""
    text = re.sub(r"(?i)(api_key=)[^&\s\"'<>]+", r"\1<redacted>", text)
    for secret in sorted(_REGISTERED_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    text = re.sub(r"\b[0-9a-z]{32}\b", "<redacted>", text)
    return " ".join(text.split())


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    """Fetch, and on an HTTP error KEEP the provider's explanation.

    ``free_market_data._get`` raises ``HTTP_ERROR:<code>`` and discards the
    response body, which is right for the daily collector (its URLs carry the
    FRED key and it has no diagnostic to preserve) and is why run 35432930212
    said only ``HTTP_ERROR:400``. This getter is a separate function for this
    module only: the shared one is not modified. The body is masked by
    ``mask_secrets`` and truncated before it is attached, and the URL itself is
    never attached.
    """
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 200:
                raise FreeMarketDataError(f"HTTP_STATUS:{response.status}")
            return response.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()[: HTTP_ERROR_BODY_MAX * 4].decode("utf-8", "replace")
        except OSError:
            body = ""
        detail = mask_secrets(body)[:HTTP_ERROR_BODY_MAX]
        raise FreeMarketDataError(
            f"HTTP_ERROR:{exc.code}:{detail}" if detail else f"HTTP_ERROR:{exc.code}"
        ) from None
    except urllib.error.URLError:
        raise FreeMarketDataError("NETWORK_ERROR:URL_ERROR") from None
    except TimeoutError:
        raise FreeMarketDataError("NETWORK_ERROR:TIMEOUT") from None
    except OSError as exc:
        raise FreeMarketDataError(f"NETWORK_ERROR:{type(exc).__name__}") from None


class RequestBudget:
    """Fail closed rather than quietly exceed an approved request count."""

    def __init__(self, limit: int | None) -> None:
        self.limit = limit
        self.spent = 0

    def spend(self) -> None:
        if self.limit is not None and self.spent >= self.limit:
            raise FreeMarketDataError(f"REQUEST_BUDGET_EXCEEDED:{self.limit}")
        self.spent += 1


def measure_request_budget(contract: dict) -> int:
    """The exact worst-case request count measure mode is allowed."""
    series = contract["fred"]["series"]
    if not isinstance(series, list) or not series:
        raise FreeMarketDataError("FRED_SERIES_LIST_INVALID")
    return len(series) + 2 + len(FRED_OBSERVATION_SHAPES) + (len(series) - 1)


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
    getter = getter or _get
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
            "response_sha256": sha256_bytes(raw),
            "response_bytes": len(raw),
            # The two times are kept OUT of this record on purpose: it is part of
            # a content-addressed payload, and a wall-clock value in there makes
            # the address of identical evidence depend on when it was fetched.
            # They travel beside it and are published as their own object by
            # ``build_retrieval_timing``, so nothing about when the response
            # arrived is lost.
            RETRIEVAL_TIMING_KEY: {
                "requested_at_utc": _stamp(requested_at),
                "received_at_utc": _stamp(received_at),
            },
        })
    return raw


def split_request_timing(receipts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separate the timeless request facts from the retrieval times.

    Returns (content_records, timing_records). The two are joined by ``index``
    and by ``response_sha256``, so a reader can always say when a given response
    was received without the time having ever been part of the evidence address.
    """
    content: list[dict] = []
    timing: list[dict] = []
    for index, record in enumerate(receipts):
        times = record.get(RETRIEVAL_TIMING_KEY) or {}
        content.append({
            key: value for key, value in record.items()
            if key != RETRIEVAL_TIMING_KEY
        })
        timing.append({
            "index": index,
            "endpoint": record.get("endpoint"),
            "response_sha256": record.get("response_sha256"),
            "requested_at_utc": times.get("requested_at_utc"),
            "received_at_utc": times.get("received_at_utc"),
        })
    return content, timing


UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def assert_no_wall_clock(payload: object, code: str, path: str = "") -> None:
    """Refuse a wall-clock value anywhere inside a content-addressed payload.

    This is the lock on the 2026-09-19 defect, not a comment about it. Any key
    ending ``_at_utc`` and any value shaped like a UTC second stamp fails here,
    so a later edit cannot reintroduce a timestamp into the receipt and make the
    address of identical evidence depend on when it was fetched again. Calendar
    dates (``available_from``, ``observation_start``, vintage dates) are not
    timestamps and are untouched by this.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and key.endswith("_at_utc"):
                raise FreeMarketDataError(f"{code}:{path}/{key}")
            assert_no_wall_clock(value, code, f"{path}/{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            assert_no_wall_clock(value, code, f"{path}[{index}]")
    elif isinstance(payload, str) and UTC_SECOND.fullmatch(payload):
        raise FreeMarketDataError(f"{code}:{path}")


def build_retrieval_timing(
    receipt_pointer: dict, captured_at: dt.datetime, timing: list[dict]
) -> dict:
    """The run's wall clock, published beside the receipt it belongs to.

    Addressed by its own content, which includes the times, so it can never
    collide with an earlier run's record and never has to be rewritten. It is
    published only when the receipt it points at is NEW: a byte-identical
    re-receive brought no new evidence, and the times already stored with those
    bytes are the times they were received.
    """
    return build_derived_object(
        f"receipts/{receipt_pointer['payload_sha256']}/timing",
        "retrieval_timing.json",
        {
            "schema_version": RECEIPT_TIMING_SCHEMA,
            "receipt_payload_sha256": receipt_pointer["payload_sha256"],
            "receipt_path": receipt_pointer["payload_path"],
            "captured_at_utc": _stamp(captured_at),
            "request_count": len(timing),
            "request_timings": timing,
        },
    )


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
) -> tuple[bytes, dict, list[str]]:
    """Every vintage date ALFRED holds for one series, oldest first.

    The dates are the input to ``build_vintage_window_plan``: the realtime axis
    is split by vintage COUNT, so the list itself -- not a guess about how dense
    vintages are -- decides the windows. A truncated listing would silently drop
    vintages from the plan, so it fails closed rather than planning on a subset.
    """
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
    dates = sorted(dates)
    count = body.get("count")
    truncated = isinstance(count, int) and count > len(dates)
    summary = {
        "series_id": series_id,
        "vintage_date_count_reported": count if isinstance(count, int) else None,
        "vintage_date_count_returned": len(dates),
        "earliest_vintage_date": dates[0],
        "latest_vintage_date": dates[-1],
        "truncated": truncated,
    }
    return raw, summary, dates


def parse_vintage_date_limit(text: object) -> int | None:
    """The provider's own stated maximum, read out of its own message.

    Nothing here assumes 2000. The number is parsed when FRED says it, recorded,
    and compared against ``FRED_VINTAGE_BATCH_SIZE``; if the provider ever
    lowers it below twice the committed batch size, the plan fails closed.
    """
    if not isinstance(text, str):
        return None
    match = FRED_VINTAGE_LIMIT_PATTERN.search(text)
    if match is None:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def _next_day(value: str) -> str | None:
    """The calendar day after ``value``, or None at the ALFRED horizon."""
    if value == FRED_REALTIME_MAX:
        return None
    return (dt.date.fromisoformat(value) + dt.timedelta(days=1)).isoformat()


def build_vintage_window_plan(
    series_id: str,
    vintage_dates: list[str],
    *,
    batch_size: int = FRED_VINTAGE_BATCH_SIZE,
    provider_limit: int | None = None,
) -> list[dict]:
    """Split the realtime axis into windows holding at most ``batch_size`` vintages.

    The windows are calendar-contiguous and disjoint, and together they cover
    ``[earliest vintage date .. FRED_REALTIME_MAX]``. Calendar-contiguous rather
    than vintage-exact matters: ALFRED clips each row's realtime window to the
    requested one, so two adjacent requests return the two halves of one
    availability interval. If the windows had gaps on days that happen to hold no
    vintage, ``_stitch_availability`` would leave a hole on exactly those days
    and an as-of read there would find nothing. Contiguity removes that.

    The last window ends at ``FRED_REALTIME_MAX`` so a value that is still
    current keeps ``9999-12-31`` and not a clipped end.
    """
    series_id = _check_series_id(series_id)
    if not isinstance(batch_size, int) or batch_size < 1:
        raise FreeMarketDataError("FRED_VINTAGE_BATCH_SIZE_INVALID")
    if provider_limit is not None and batch_size * 2 > provider_limit:
        raise FreeMarketDataError(
            "FRED_VINTAGE_BATCH_SIZE_EXCEEDS_PROVIDER_LIMIT:"
            f"{batch_size}:{provider_limit}"
        )
    dates = sorted(
        _check_date(value, f"FRED_VINTAGE_DATE_INVALID:{series_id}")
        for value in vintage_dates
    )
    if not dates:
        raise FreeMarketDataError(f"FRED_VINTAGE_DATES_MISSING:{series_id}")
    if len(set(dates)) != len(dates):
        raise FreeMarketDataError(f"FRED_VINTAGE_DATES_DUPLICATED:{series_id}")
    windows = []
    cursor = dates[0]
    for index, position in enumerate(range(0, len(dates), batch_size)):
        batch = dates[position:position + batch_size]
        last = position + len(batch) >= len(dates)
        realtime_end = FRED_REALTIME_MAX if last else batch[-1]
        windows.append({
            "index": index,
            "realtime_start": cursor,
            "realtime_end": realtime_end,
            "vintage_date_count": len(batch),
            "first_vintage_date": batch[0],
            "last_vintage_date": batch[-1],
        })
        if not last:
            following = _next_day(realtime_end)
            if following is None:
                raise FreeMarketDataError(
                    f"FRED_VINTAGE_WINDOW_PLAN_INVALID:{series_id}"
                )
            cursor = following
    assert_vintage_window_plan_covers(series_id, dates, windows, batch_size)
    return windows


def assert_vintage_window_plan_covers(
    series_id: str, vintage_dates: list[str], windows: list[dict], batch_size: int
) -> None:
    """Prove the plan loses no vintage and double-counts none.

    Narrowing the realtime window is only safe if the union of the narrowed
    windows still covers every vintage ALFRED holds. That is asserted here, on
    every plan, at build time -- not left to a reviewer's reading.
    """
    if not windows:
        raise FreeMarketDataError(f"FRED_VINTAGE_WINDOW_PLAN_EMPTY:{series_id}")
    dates = sorted(vintage_dates)
    if windows[0]["realtime_start"] != dates[0]:
        raise FreeMarketDataError(
            f"FRED_VINTAGE_WINDOW_PLAN_START_MISMATCH:{series_id}"
        )
    if windows[-1]["realtime_end"] != FRED_REALTIME_MAX:
        raise FreeMarketDataError(
            f"FRED_VINTAGE_WINDOW_PLAN_END_MISMATCH:{series_id}"
        )
    for earlier, later in zip(windows, windows[1:]):
        if earlier["realtime_start"] > earlier["realtime_end"]:
            raise FreeMarketDataError(
                f"FRED_VINTAGE_WINDOW_ORDER_INVALID:{series_id}"
            )
        if _next_day(earlier["realtime_end"]) != later["realtime_start"]:
            raise FreeMarketDataError(
                f"FRED_VINTAGE_WINDOW_PLAN_NOT_CONTIGUOUS:{series_id}"
            )
    covered = 0
    for window in windows:
        if window["vintage_date_count"] > batch_size:
            raise FreeMarketDataError(
                f"FRED_VINTAGE_WINDOW_OVER_BATCH_SIZE:{series_id}"
            )
        inside = [
            value for value in dates
            if window["realtime_start"] <= value <= window["realtime_end"]
        ]
        if len(inside) != window["vintage_date_count"]:
            raise FreeMarketDataError(
                f"FRED_VINTAGE_WINDOW_COUNT_MISMATCH:{series_id}:{window['index']}"
            )
        covered += len(inside)
    if covered != len(dates):
        raise FreeMarketDataError(
            f"FRED_VINTAGE_WINDOW_PLAN_COVERAGE_MISMATCH:{series_id}:{covered}"
            f":{len(dates)}"
        )


def vintage_window_plan_summary(series_id: str, windows: list[dict]) -> dict:
    counts = [window["vintage_date_count"] for window in windows]
    return {
        "series_id": series_id,
        "window_count": len(windows),
        "batch_size": FRED_VINTAGE_BATCH_SIZE,
        "vintage_date_total": sum(counts),
        "max_vintage_dates_in_a_window": max(counts) if counts else 0,
        "realtime_start": windows[0]["realtime_start"] if windows else None,
        "realtime_end": windows[-1]["realtime_end"] if windows else None,
        "windows": windows,
    }


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


def fred_observation_params(
    series_id: str,
    observation_start: str,
    observation_end: str,
    output_type: int,
    shape: dict,
    offset: int = 0,
    *,
    realtime_start: str,
    realtime_end: str,
) -> dict:
    """Build one ``series/observations`` request under a candidate shape.

    The keys that carry the point-in-time meaning -- ``series_id``, the
    observation window, and the realtime window -- are NOT shape-dependent and
    are always present. Everything the shape varies is an optional parameter
    whose omitted value is FRED's own default, so no shape can change which data
    is asked for.

    The realtime window is required and is never defaulted: omitting it would
    make FRED answer for today only, i.e. the current revision, which is the one
    thing this module exists to avoid. It comes from
    ``build_vintage_window_plan``, whose windows together cover every vintage the
    series has.

    ``offset`` is sent only when it is non-zero: sending the default on the first
    page added one more parameter that could be rejected for nothing.
    """
    params = {
        "series_id": _check_series_id(series_id),
        "file_type": "json",
        "observation_start": _check_date(
            observation_start, "FRED_OBSERVATION_START_INVALID"
        ),
        "observation_end": _check_date(
            observation_end, "FRED_OBSERVATION_END_INVALID"
        ),
        "realtime_start": _check_date(
            realtime_start, "FRED_REALTIME_START_INVALID"
        ),
        "realtime_end": _check_date(realtime_end, "FRED_REALTIME_END_INVALID"),
    }
    if params["realtime_start"] > params["realtime_end"]:
        raise FreeMarketDataError("FRED_REALTIME_WINDOW_ORDER_INVALID")
    if shape.get("output_type"):
        params["output_type"] = output_type
    if shape.get("sort_order"):
        params["sort_order"] = shape["sort_order"]
    if shape.get("limit"):
        params["limit"] = shape["limit"]
    if offset:
        params["offset"] = offset
    return params


def fred_observation_chunks(
    observation_start: str, observation_end: str, chunk_days: int = FRED_CHUNK_DAYS
) -> list[tuple[str, str]]:
    """Split an inclusive window into adjacent, non-overlapping inclusive chunks.

    No gap and no overlap, so merging the chunks can neither drop nor duplicate
    an observation. The regression pins that a chunked receive equals a
    single-request receive for the same window.
    """
    start = dt.date.fromisoformat(
        _check_date(observation_start, "FRED_OBSERVATION_START_INVALID")
    )
    end = dt.date.fromisoformat(
        _check_date(observation_end, "FRED_OBSERVATION_END_INVALID")
    )
    if start > end:
        raise FreeMarketDataError("FRED_OBSERVATION_WINDOW_ORDER_INVALID")
    if not isinstance(chunk_days, int) or chunk_days < 1:
        raise FreeMarketDataError("FRED_CHUNK_DAYS_INVALID")
    chunks = []
    cursor = start
    while cursor <= end:
        stop = min(end, cursor + dt.timedelta(days=chunk_days - 1))
        chunks.append((cursor.isoformat(), stop.isoformat()))
        cursor = stop + dt.timedelta(days=1)
    return chunks


def _merge_vintage_rows(row_groups: list[list[dict]]) -> list[dict]:
    """Merge chunk/page row groups into one deterministic, duplicate-free list.

    Keyed by (observation_date, available_from), which identifies one published
    value exactly. Two groups presenting the same key with different content
    means the chunk boundaries changed the answer, and that fails closed instead
    of being silently resolved.
    """
    merged: dict[tuple[str, str], dict] = {}
    for group in row_groups:
        for row in group:
            key = (row["observation_date"], row["available_from"])
            prior = merged.get(key)
            if prior is not None and prior != row:
                raise FreeMarketDataError(
                    f"FRED_VINTAGE_CHUNK_BOUNDARY_CONFLICT:{key[0]}:{key[1]}"
                )
            merged[key] = row
    return [merged[key] for key in sorted(merged)]


def _stitch_availability(rows: list[dict]) -> list[dict]:
    """Rejoin availability intervals that a realtime window boundary split.

    ALFRED clips every row's realtime window to the one requested, so a value
    that was published inside window k and was still current in window k+1 comes
    back as two rows: ``[published, k.end]`` and ``[k+1.start, ...]``. Because
    the plan's windows are calendar-contiguous, ``k+1.start`` is exactly the day
    after ``k.end``, and only those two halves stitch.

    Two intervals are joined only when they are calendar-adjacent for the SAME
    observation date AND the same value. A value that was revised away and later
    restored has a real gap between its intervals (the intervening value
    occupied it), so it is left as two intervals -- which is correct, and is why
    the value is part of the key. An actual overlap is a contradiction and fails
    closed rather than being resolved silently.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault((row["observation_date"], row["value"]), []).append(row)
    stitched: list[dict] = []
    for (observation_date, value), group in grouped.items():
        group = sorted(group, key=lambda row: (row["available_from"], row["available_to"]))
        current = dict(group[0])
        for row in group[1:]:
            if row["available_from"] <= current["available_to"]:
                raise FreeMarketDataError(
                    f"FRED_AVAILABILITY_OVERLAP:{observation_date}:{value}"
                )
            if _next_day(current["available_to"]) == row["available_from"]:
                current["available_to"] = row["available_to"]
                continue
            stitched.append(current)
            current = dict(row)
        stitched.append(current)
    return sorted(
        stitched, key=lambda row: (row["observation_date"], row["available_from"])
    )


def fetch_fred_vintage_observations(
    api_key: str,
    series_id: str,
    observation_start: str,
    observation_end: str,
    output_type: int,
    *,
    realtime_start: str,
    realtime_end: str,
    shape: dict | None = None,
    require_rows: bool = True,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> tuple[list[bytes], list[dict]]:
    """Every (observation, revision) row ALFRED holds for ONE window.

    Each returned row carries ``realtime_start``/``realtime_end``: the dates
    between which that value was the published one. Those two fields ARE the
    availability record -- they are preserved verbatim and are what
    ``observations_available_at`` filters on.
    """
    series_id = _check_series_id(series_id)
    shape = shape or FRED_OBSERVATION_SHAPES[0]
    pages: list[bytes] = []
    rows: list[dict] = []
    offset = 0
    for _page in range(MAX_PAGES):
        params = fred_observation_params(
            series_id, observation_start, observation_end, output_type, shape, offset,
            realtime_start=realtime_start, realtime_end=realtime_end,
        )
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
    if require_rows and not rows:
        raise FreeMarketDataError(f"FRED_VINTAGE_ROWS_EMPTY:{series_id}")
    return pages, rows


def probe_fred_observation_shape(
    api_key: str,
    series_id: str,
    observation_start: str,
    observation_end: str,
    output_type: int,
    shape: dict,
    *,
    realtime_start: str,
    realtime_end: str,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> dict:
    """Try ONE shape on ONE planned realtime window and report what FRED said.

    A rejection carries the provider's own (masked, truncated) explanation, which
    is the thing run 35432930212 could not tell anyone, and which named the real
    constraint on run 35435347342. Any vintage-count limit stated in that
    explanation is parsed out and reported rather than assumed.
    """
    params = fred_observation_params(
        series_id, observation_start, observation_end, output_type, shape,
        realtime_start=realtime_start, realtime_end=realtime_end,
    )
    record = {
        "shape_id": shape["id"],
        "series_id": series_id,
        "observation_start": observation_start,
        "observation_end": observation_end,
        "realtime_start": realtime_start,
        "realtime_end": realtime_end,
        # The credential is never part of `params`, so this is safe to print
        # and safe to commit.
        "params": {key: str(value) for key, value in sorted(params.items())},
    }
    try:
        _pages, rows = fetch_fred_vintage_observations(
            api_key, series_id, observation_start, observation_end, output_type,
            realtime_start=realtime_start, realtime_end=realtime_end,
            shape=shape, require_rows=False, getter=getter, budget=budget,
            receipts=receipts,
        )
    except FreeMarketDataError as exc:
        message = mask_secrets(str(exc))
        return {
            **record,
            "status": "REJECTED",
            "provider_error": message,
            "provider_vintage_date_limit": parse_vintage_date_limit(message),
        }
    availability = sorted({row["available_from"] for row in rows})
    return {
        **record,
        "status": "ACCEPTED",
        "row_count": len(rows),
        "observation_date_count": len({row["observation_date"] for row in rows}),
        "distinct_availability_date_count": len(availability),
        "earliest_availability_date": availability[0] if availability else None,
        "latest_availability_date": availability[-1] if availability else None,
    }


def negotiate_fred_observation_shape(
    api_key: str,
    series_id: str,
    observation_start: str,
    observation_end: str,
    output_type: int,
    *,
    realtime_start: str,
    realtime_end: str,
    attempts: list | None = None,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> tuple[dict, list[dict]]:
    """Find the first committed shape the provider accepts, and record the rest.

    This is a measurement, not a guess: the ladder is a fixed committed list, the
    shapes are semantically identical (they differ only in parameters whose
    omitted value is FRED's own default), every rejection keeps the provider's
    reason, and the shape actually used is written into the run receipt. If none
    is accepted, the error carries every reason FRED gave.
    """
    # The caller may own the list, so a total failure still leaves every
    # provider reason in the caller's hands rather than only in the exception.
    attempts = attempts if attempts is not None else []
    for shape in FRED_OBSERVATION_SHAPES:
        attempt = probe_fred_observation_shape(
            api_key, series_id, observation_start, observation_end, output_type,
            shape, realtime_start=realtime_start, realtime_end=realtime_end,
            getter=getter, budget=budget, receipts=receipts,
        )
        attempts.append(attempt)
        if attempt["status"] == "ACCEPTED":
            return shape, attempts
    reasons = "; ".join(
        f"{attempt['shape_id']}={attempt.get('provider_error')}" for attempt in attempts
    )
    raise FreeMarketDataError(
        f"FRED_OBSERVATION_SHAPE_ALL_REJECTED:{series_id}:{mask_secrets(reasons)}"
    )


def fetch_fred_vintage_range(
    api_key: str,
    series_id: str,
    observation_start: str,
    observation_end: str,
    output_type: int,
    *,
    shape: dict,
    windows: list[dict],
    chunk_days: int = FRED_CHUNK_DAYS,
    pace_seconds: float = 0.0,
    getter=None,
    budget: RequestBudget | None = None,
    receipts: list | None = None,
) -> tuple[list[bytes], list[dict], list[dict]]:
    """Receive one series over every planned realtime window, then merge.

    Two axes, both split, for two different reasons:

    * **realtime** -- because the provider caps the number of vintages inside one
      requested real-time period (it said 2000 on 2026-09-19 for a series that
      has 3936). ``windows`` comes from ``build_vintage_window_plan``, which has
      already proved it covers every vintage.
    * **observation** -- for paging safety, inclusive and strictly adjacent.

    Every series takes this path, including the two that would fit in a single
    realtime window today (TOTBKCR 1553 vintages, WRESBAL 1148). Giving them a
    different code path would mean the split is never exercised for them, and the
    day they cross the cap it would break silently. One path, always.

    The rows are merged (duplicate-free, failing closed on a conflict) and then
    stitched: ALFRED clips each row's realtime window to the requested one, so a
    single availability interval crossing a window boundary arrives as two halves
    and must be rejoined or an as-of read past the boundary would find nothing.
    """
    if not windows:
        raise FreeMarketDataError(f"FRED_VINTAGE_WINDOW_PLAN_EMPTY:{series_id}")
    chunks = fred_observation_chunks(observation_start, observation_end, chunk_days)
    pages: list[bytes] = []
    row_groups: list[list[dict]] = []
    request_records: list[dict] = []
    first = True
    for window in windows:
        for chunk_start, chunk_end in chunks:
            if pace_seconds and not first:
                time.sleep(pace_seconds)
            first = False
            chunk_pages, chunk_rows = fetch_fred_vintage_observations(
                api_key, series_id, chunk_start, chunk_end, output_type,
                realtime_start=window["realtime_start"],
                realtime_end=window["realtime_end"],
                shape=shape, require_rows=False, getter=getter, budget=budget,
                receipts=receipts,
            )
            pages.extend(chunk_pages)
            row_groups.append(chunk_rows)
            request_records.append({
                "realtime_window_index": window["index"],
                "realtime_start": window["realtime_start"],
                "realtime_end": window["realtime_end"],
                "vintage_date_count": window["vintage_date_count"],
                "observation_start": chunk_start,
                "observation_end": chunk_end,
                "page_count": len(chunk_pages),
                "row_count": len(chunk_rows),
            })
    rows = _stitch_availability(_merge_vintage_rows(row_groups))
    if not rows:
        raise FreeMarketDataError(f"FRED_VINTAGE_ROWS_EMPTY:{series_id}")
    return pages, rows, request_records


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
    plan_summary: dict | None = None,
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
        # The realtime axis is received as a plan of contiguous windows whose
        # union covers every vintage the series has; the plan is recorded so the
        # coverage claim is auditable from the packet alone.
        "realtime_start": (
            plan_summary["realtime_start"] if plan_summary else FRED_REALTIME_MIN
        ),
        "realtime_end": (
            plan_summary["realtime_end"] if plan_summary else FRED_REALTIME_MAX
        ),
        "vintage_window_plan": plan_summary,
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

    ``MEASURE_REQUEST_BUDGET`` requests at most (12; 8 in the expected case --
    the arithmetic is written out at that constant):

    * ALFRED ``vintagedates`` per FRED series -- how far back vintages exist.
    * One Alpaca window request on ``feed=iex`` and one on ``feed=sip`` -- how
      many bars really come back, and whether SIP is open at all.
    * The ``series/observations`` shape ladder on the first series, then the
      accepted shape confirmed on the remaining series. **This is the check that
      was missing.** The 2026-09-19 measure run passed and the fetch run then
      died on this very endpoint, because measure probed only ``vintagedates``.
      The probe window is one ``FRED_CHUNK_DAYS`` chunk ending at ``end``, i.e.
      exactly the size and shape of a request the receive will make.
    """
    register_secrets(fred_key, alpaca_key, alpaca_secret)
    budget_limit = measure_request_budget(contract)
    budget = RequestBudget(budget_limit)
    receipts: list[dict] = []
    probe_chunks = fred_observation_chunks(start, end)
    probe_start, probe_end = probe_chunks[-1]
    result = {
        "mode": "measure",
        "writes_nothing": True,
        "measured_at_utc": _stamp(_utc_now()),
        "requested_window": {"start": start, "end": end},
        "request_budget": budget_limit,
        "fred_observation_probe": {
            "probe_window": {"start": probe_start, "end": probe_end},
            "chunk_days": FRED_CHUNK_DAYS,
            "chunk_count_for_requested_window": len(probe_chunks),
            "shape_ladder": [shape["id"] for shape in FRED_OBSERVATION_SHAPES],
            "vintage_batch_size": FRED_VINTAGE_BATCH_SIZE,
            "provider_vintage_date_limit": None,
            "batch_size_within_provider_limit": None,
            "plan": {},
            "planned_fetch_observation_requests": None,
            "attempts": [],
            "accepted_shape_id": None,
            "series": {},
        },
        "fred_vintages": {},
        "alpaca": {},
    }
    vintage_dates: dict[str, list[str]] = {}
    for series_id in contract["fred"]["series"]:
        try:
            _, summary, dates = fetch_fred_vintage_dates(
                fred_key, series_id, getter=getter, budget=budget, receipts=receipts
            )
            vintage_dates[series_id] = dates
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
    probe = result["fred_observation_probe"]
    series_ids = list(contract["fred"]["series"])
    output_type = contract["fred"]["output_type"]
    # The realtime axis is planned from the vintage date lists already received
    # above, so the probe asks for a window a receive will actually ask for --
    # the previous version probed the full 1776..9999 window and that is exactly
    # what the provider refuses.
    plans: dict[str, list[dict]] = {}
    for series_id, dates in vintage_dates.items():
        try:
            plans[series_id] = build_vintage_window_plan(series_id, dates)
            probe["plan"][series_id] = vintage_window_plan_summary(
                series_id, plans[series_id]
            )
        except FreeMarketDataError as exc:
            probe["plan"][series_id] = {
                "series_id": series_id, "error": mask_secrets(str(exc))
            }
    chunk_count = len(probe_chunks)
    probe["planned_fetch_observation_requests"] = sum(
        summary.get("window_count", 0) * chunk_count
        for summary in probe["plan"].values()
    )
    probe["observation_chunk_count_for_requested_window"] = chunk_count
    accepted_shape = None
    attempts: list[dict] = probe["attempts"]
    first_series = series_ids[0]
    first_plan = plans.get(first_series)
    if not first_plan:
        probe["error"] = f"FRED_VINTAGE_WINDOW_PLAN_EMPTY:{first_series}"
    else:
        # Probe the LAST planned window: it is the one whose realtime_end is the
        # ALFRED horizon, so it is the window an as-of read of today depends on.
        window = first_plan[-1]
        try:
            accepted_shape, _ = negotiate_fred_observation_shape(
                fred_key, first_series, probe_start, probe_end, output_type,
                realtime_start=window["realtime_start"],
                realtime_end=window["realtime_end"],
                attempts=attempts, getter=getter, budget=budget, receipts=receipts,
            )
            probe["accepted_shape_id"] = accepted_shape["id"]
            probe["series"][first_series] = attempts[-1]
        except FreeMarketDataError as exc:
            # Every shape's own rejection reason is already in `attempts`; keep
            # the aggregate too, plus any limit the provider named.
            probe["error"] = mask_secrets(str(exc))
    for attempt in attempts:
        limit = attempt.get("provider_vintage_date_limit")
        if limit is not None:
            probe["provider_vintage_date_limit"] = limit
            probe["batch_size_within_provider_limit"] = (
                FRED_VINTAGE_BATCH_SIZE * 2 <= limit
            )
    if accepted_shape is not None:
        for series_id in series_ids[1:]:
            series_plan = plans.get(series_id)
            if not series_plan:
                continue
            series_window = series_plan[-1]
            probe["series"][series_id] = probe_fred_observation_shape(
                fred_key, series_id, probe_start, probe_end, output_type,
                accepted_shape,
                realtime_start=series_window["realtime_start"],
                realtime_end=series_window["realtime_end"],
                getter=getter, budget=budget, receipts=receipts,
            )
    result["requests_made"] = budget.spent
    result["request_receipts"] = receipts
    iex = result["alpaca"].get("iex", {})
    observations_ready = accepted_shape is not None and all(
        record.get("status") == "ACCEPTED" for record in probe["series"].values()
    ) and len(probe["series"]) == len(series_ids)
    result["conclusion"] = {
        "score_symbol_count": len(score_symbols(contract)),
        "iex_meets_window_target": bool(
            iex.get("feasibility", {}).get("meets_window_target")
        ),
        "sip_available": result["alpaca"].get("sip", {}).get("status") == "OBSERVED",
        "fred_observations_ready": observations_ready,
        "fred_accepted_shape_id": probe["accepted_shape_id"],
        "fred_realtime_window_counts": {
            series_id: summary.get("window_count")
            for series_id, summary in probe["plan"].items()
        },
        "planned_fetch_observation_requests": (
            probe["planned_fetch_observation_requests"]
        ),
        "note": (
            "A full receive is only worth running when a feed reaches "
            "bars_required_for_window_target AND fred_observations_ready is true. "
            "If it is false, read fred_observation_probe.attempts: each rejected "
            "shape carries the provider's own reason, and any vintage-date limit "
            "it states is parsed into provider_vintage_date_limit. "
            "planned_fetch_observation_requests is how many observation requests "
            "the receive will make: realtime windows x observation chunks."
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
    pace_seconds: float = FRED_REQUEST_INTERVAL_SECONDS,
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
    register_secrets(fred_key, alpaca_key, alpaca_secret)
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

    fred_start = (
        dt.date.fromisoformat(start) - dt.timedelta(days=FRED_OBSERVATION_LEAD_DAYS)
    ).isoformat()

    # Vintage date lists first, for every series: they are the input to the
    # realtime window plan, and the plan has to exist before a single
    # observations request can be shaped. Run 35435347342 proved the realtime
    # axis is the constrained one (the provider caps vintages per real-time
    # period), so there is no correct request to make before this step.
    vintage_raws: dict[str, bytes] = {}
    vintage_summaries: dict[str, dict] = {}
    plans: dict[str, list[dict]] = {}
    for series_id in contract["fred"]["series"]:
        vintage_raws[series_id], vintage_summaries[series_id], dates = (
            fetch_fred_vintage_dates(
                fred_key, series_id, getter=getter, receipts=receipts
            )
        )
        if vintage_summaries[series_id]["truncated"]:
            raise FreeMarketDataError(
                f"FRED_VINTAGE_DATES_TRUNCATED:{series_id}"
            )
        plans[series_id] = build_vintage_window_plan(series_id, dates)

    # Negotiate the observations request shape ONCE, on the LAST planned realtime
    # window of the first series. The accepted shape and every rejection -- with
    # the provider's own reason and any limit it names -- go into the receipt.
    negotiation_window = fred_observation_chunks(fred_start, end)[-1]
    negotiation_realtime = plans[contract["fred"]["series"][0]][-1]
    shape_attempts: list[dict] = []
    try:
        observation_shape, _ = negotiate_fred_observation_shape(
            fred_key,
            contract["fred"]["series"][0],
            negotiation_window[0],
            negotiation_window[1],
            contract["fred"]["output_type"],
            realtime_start=negotiation_realtime["realtime_start"],
            realtime_end=negotiation_realtime["realtime_end"],
            attempts=shape_attempts,
            getter=getter,
            receipts=receipts,
        )
    except FreeMarketDataError:
        # If the provider named a vintage-date limit, say THAT rather than
        # leaving the operator to read five identical rejection bodies. The
        # actionable fact is that the committed batch size no longer fits.
        limits = [
            parse_vintage_date_limit(attempt.get("provider_error"))
            for attempt in shape_attempts
        ]
        limits = [value for value in limits if value]
        if limits and min(limits) < FRED_VINTAGE_BATCH_SIZE * 2:
            raise FreeMarketDataError(
                "FRED_VINTAGE_BATCH_SIZE_EXCEEDS_PROVIDER_LIMIT:"
                f"{FRED_VINTAGE_BATCH_SIZE}:{min(limits)}"
            ) from None
        raise

    fred_records = []
    for series_id in contract["fred"]["series"]:
        vintage_raw = vintage_raws[series_id]
        vintage_summary = vintage_summaries[series_id]
        plan = plans[series_id]
        plan_summary = vintage_window_plan_summary(series_id, plan)
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
        pages, rows, request_records = fetch_fred_vintage_range(
            fred_key,
            series_id,
            fred_start,
            end,
            contract["fred"]["output_type"],
            shape=observation_shape,
            windows=plan,
            pace_seconds=pace_seconds,
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
                    "realtime_start": plan_summary["realtime_start"],
                    "realtime_end": plan_summary["realtime_end"],
                    "page_index": index,
                },
            })
        availability = build_derived_object(
            f"fred/alfred/{series_id}/availability",
            "fred_alfred_availability.json",
            {
                **build_availability_series(
                    series_id, fred_start, end, rows, metadata, page_pointers,
                    plan_summary,
                ),
                "requested_window_start": start,
                "observation_lead_days": FRED_OBSERVATION_LEAD_DAYS,
                "request_shape_id": observation_shape["id"],
                "chunk_days": FRED_CHUNK_DAYS,
                "requests": request_records,
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
            "chunk_days": FRED_CHUNK_DAYS,
            "vintage_window_plan": plan_summary,
            "requests": request_records,
            "vintage_dates": vintage_summary,
            "vintage_dates_pointer": vintage_pointer,
            "metadata": metadata,
            "metadata_pointer": metadata_pointer,
            "row_count": len(rows),
            "observation_pointers": page_pointers,
            "availability_pointer": availability_pointer,
        })

    # The receipt's identity is WHAT was received, never when. The run's clock is
    # published beside it, and only when the receipt itself is new -- see
    # build_retrieval_timing.
    request_records, request_timings = split_request_timing(receipts)
    receipt = build_derived_object(
        "receipts",
        "manifest.json",
        {
            "schema_version": RECEIPT_SCHEMA,
            "contract_version": contract["contract_version"],
            "mode": "fetch",
            "receipt_identity": "CONTENT_ADDRESSED_TIMES_HELD_IN_TIMING_SIDECAR",
            "requested_window": {"start": start, "end": end},
            "alpaca": {
                "feed": feed,
                "symbol_count": len(symbols),
                "symbols": list(symbols),
                "series": alpaca_records,
            },
            "fred": {
                "source_scope": "ALFRED_VINTAGE_SERIES_API",
                "realtime_start": FRED_REALTIME_MIN,
                "realtime_end": FRED_REALTIME_MAX,
                "request_shape_id": observation_shape["id"],
                "request_shape_attempts": shape_attempts,
                "chunk_days": FRED_CHUNK_DAYS,
                "vintage_batch_size": FRED_VINTAGE_BATCH_SIZE,
                "realtime_axis_split_reason": (
                    "PROVIDER_CAPS_VINTAGE_DATES_PER_REALTIME_PERIOD"
                ),
                "series": fred_records,
            },
            "request_receipts": request_records,
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
    assert_no_wall_clock(
        {key: value for key, value in receipt["payload"].items()
         if key != "payload_sha256"},
        "HISTORY_RECEIPT_CARRIES_WALL_CLOCK",
    )
    # A re-receive of byte-identical evidence must be a true no-op: nothing new,
    # nothing changed. Deciding that needs no scan -- the receipt is
    # content-addressed, so its own path either already holds these exact bytes
    # or it does not.
    receipt_path = _safe_history_path(
        root, receipt["pointer"]["payload_path"], "/manifest.json"
    )
    receipt_already_present = (
        receipt_path.is_file()
        and receipt_path.read_bytes() == receipt["payload_bytes"]
    )
    receipt_pointer = publish_derived_object(root, receipt, "manifest.json")
    timing_pointer = None
    if not receipt_already_present:
        timing_pointer = publish_derived_object(
            root,
            build_retrieval_timing(receipt_pointer, captured_at, request_timings),
            "retrieval_timing.json",
        )
    return {
        "status": "PASS",
        "mode": "fetch",
        "receipt_path": receipt_pointer["payload_path"],
        "receipt_sha256": receipt_pointer["payload_sha256"],
        "receipt_status": (
            "ALREADY_PRESENT" if receipt_already_present else "PUBLISHED"
        ),
        "retrieval_timing_path": (
            timing_pointer["payload_path"] if timing_pointer else None
        ),
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
    receipts_v1_checked = 0
    receipts_v2_checked = 0
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
        if path.name == "manifest.json" and "request_receipts" in body:
            version = body.get("schema_version")
            if version not in RECEIPT_SCHEMA_VERSIONS_ACCEPTED:
                raise FreeMarketDataError(
                    f"HISTORY_RECEIPT_SCHEMA_UNKNOWN:{version}"
                )
            if version == RECEIPT_SCHEMA:
                # The identity guarantee, re-checked on the stored bytes.
                assert_no_wall_clock(
                    {key: value for key, value in body.items()
                     if key != "payload_sha256"},
                    "HISTORY_RECEIPT_CARRIES_WALL_CLOCK",
                )
                timings = sorted(
                    (path.parent / "timing").rglob("retrieval_timing.json")
                )
                if not timings:
                    raise FreeMarketDataError(
                        "HISTORY_RECEIPT_TIMING_MISSING:"
                        f"{body['payload_sha256']}"
                    )
                for timing_path in timings:
                    timing = json.loads(timing_path.read_bytes())
                    if timing.get("receipt_payload_sha256") != body["payload_sha256"]:
                        raise FreeMarketDataError(
                            "HISTORY_RECEIPT_TIMING_MISPAIRED:"
                            f"{body['payload_sha256']}"
                        )
                    if not timing.get("request_timings"):
                        raise FreeMarketDataError(
                            "HISTORY_RECEIPT_TIMING_EMPTY:"
                            f"{body['payload_sha256']}"
                        )
                receipts_v2_checked += 1
            else:
                # A ``/1`` receipt landed before the split. It is read and
                # replayed like any other object and is never rewritten.
                receipts_v1_checked += 1
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
            groups = []
            for pointer in body.get("response_pointers") or []:
                page = _read_raw_object(
                    root, pointer, "fred_alfred_observations.json.gz"
                )
                rows, _count = parse_fred_vintage_page(body["series_id"], page)
                groups.append(rows)
            # The same merge AND the same stitch the receive used, so a
            # chunked, window-split receive replays exactly and a boundary
            # conflict fails here too.
            rebuilt = _stitch_availability(_merge_vintage_rows(groups))
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
        "receipts_checked": {
            RECEIPT_SCHEMA_VERSIONS_ACCEPTED[0]: receipts_v1_checked,
            RECEIPT_SCHEMA_VERSIONS_ACCEPTED[1]: receipts_v2_checked,
        },
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
