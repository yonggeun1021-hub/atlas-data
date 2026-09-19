#!/usr/bin/env python3
"""SPDR Select Sector ETF official holdings -- daily, derived-only capture.

Implements the *data* side of ``RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1``
(config/rule_registry_v1.json, source record
evidence/authority/USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json,
sha256 2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd):
US individual stock sector membership = the latest official SPDR sector ETF
holdings published before the decision date; if a symbol is held by more
than one sector ETF, the one with the largest weight; if held by none,
UNKNOWN (no T2); a sector ETF maps to its own sector.

Universe: the 11 SPDR Select Sector ETFs already used elsewhere in this
repo as the sector reference set (config/free_market_data_contract.json
``sector_reference_symbols``, collectors/free_market_data.py) --
XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY. (``SMH`` appears alongside
them in that other, unrelated breadth/leadership reference list -- it is
not a SPDR Select Sector ETF and is deliberately excluded here.)

Physically time-gated, not backfillable
----------------------------------------
Unlike a price or FX series, a fund's *holdings on a given day* were never
published again after that day passed -- there is no historical endpoint to
backfill from. Each daily capture is the only evidence that will ever exist
for that day. This module has no ``--backfill`` mode; enabling the daily
schedule promptly (subject to separate user/CIO schedule approval -- this
PR proposes it, it does not enable it) is the only way future point-in-time
answers gain history.

Licensing -- derived-only storage
-----------------------------------
State Street/SSGA's fund holdings pages are published for information, not
for wholesale redistribution, and this is a public repository. To stay on
the safe side without a clear redistribution license, this collector never
commits the downloaded workbook. It retains only:
  * a derived mapping row per (symbol, sector ETF): the symbol, the ETF,
    a 1-based ``weight_rank`` *within that ETF's own holdings that day*,
    and a coarse ``weight_bucket`` (not the exact weight) -- see
    ``WEIGHT_BUCKETS``.
  * capture metadata: source URL, fetch time, raw byte length and
    ``raw_sha256`` (a hash, not the content) so a later re-fetch could in
    principle be checked against what was actually seen, without this repo
    ever holding a redistributable copy itself.
If SSGA's terms are later confirmed to clearly permit redistribution, that
is a separate, deliberate decision -- this module does not assume it.

URL pattern -- CONFIRMED by the first live run (2026-09-15)
-----------------------------------------------------------
``HOLDINGS_URL_TEMPLATE`` below was originally an unverified guess at
State Street's public daily holdings download pattern (no live network
call was made building it). The first ``workflow_dispatch`` run of
``.github/workflows/spdr-sector-holdings.yml`` (run 34926977666) confirmed
it works: a complete 11-ETF batch, 515 symbols resolved. Every test in
``test/test_spdr_sector_holdings.py`` still uses a small in-memory fixture
workbook and a fake HTTP layer -- none of it proves the real endpoint's
shape on its own; that first live run is what did.

Holdings "As of" date (CIO 2026-09-15, PR #765 follow-up)
-------------------------------------------------------------
SSGA's holdings workbooks carry their own "as of" date in a header row
above the real column-header row (e.g. "Holdings are as of MM/DD/YYYY").
That date is the fund's own claim about which trading day's holdings the
file describes, and can differ from ``capture_date_utc`` (this collector's
own capture wall-clock date) -- SSGA may not have refreshed a fund's file
yet when this collector's daily run executes, in which case the file still
legitimately describes the *prior* trading day. ``parse_holdings_as_of_date``
scans for it tolerantly (the exact real-world header wording/date format is
still UNVERIFIED -- no committed metadata from the first live run preserved
the raw header text to confirm it against, since this collector deliberately
never retains the raw workbook; the derived-only design in "Licensing" above
predates this feature. The collector's *next* scheduled run is what will
verify the real wording). When the date cannot be found or parsed, the
field is recorded as the literal string ``"UNKNOWN"`` -- never a guess, and
never blocks the capture (a missing "as of" date does not mean the holdings
themselves are unusable, only that this one piece of provenance is absent).
See ``build_capture``'s ``holdings_as_of_date`` and ``build_batch``'s
``holdings_as_of_dates``/``holdings_as_of_date``.

Cross-ETF "largest weight" resolution (CIO review 2026-09-15, PR #761)
--------------------------------------------------------------------------
The rule's "if held by more than one, the ETF with the largest weight" is
a comparison *across* funds, and a coarse per-fund weight bucket/rank
cannot answer it correctly (a rank-1 holding in one fund is not
necessarily heavier than a rank-2 holding in another). This module
resolves that comparison from the *exact* weights it already holds in
memory, but only when one capture run covers all 11 sector ETFs together
(a "complete batch") -- see ``resolve_cross_etf_winners`` and
``build_batch``. Only the outcome is committed for each symbol:
``primary_sector_etf``, ``holder_etf_count``, and a ``tie`` flag; the exact
weight itself is discarded, never written to disk, exactly as before. If a
batch is incomplete (one or more ETF fetches failed that run),
cross-ETF resolution is not attempted for that day at all -- the reader
(``universe/us_spdr_sector_mapping.py``) falls back to the most recent
earlier *complete* batch instead of trusting a partial one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# CIO 2026-09-15 note: verified against config/free_market_data_contract.json
# ``sector_reference_symbols`` / collectors/free_market_data.py, minus SMH
# (not a SPDR Select Sector ETF).
SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")

# UNVERIFIED -- see module docstring "URL pattern" section.
HOLDINGS_URL_TEMPLATE = (
    "https://www.ssga.com/us/en/intermediary/etfs/library-content/products/"
    "fund-data/etfs/us/holdings-daily-us-en-{ticker_lower}.xlsx"
)

CAPTURE_SCHEMA_VERSION = "spdr_sector_holdings_capture/3"  # /3: + evidence_day (as-of keyed path)
MAPPING_ROW_SCHEMA_VERSION = "spdr_sector_holdings_mapping_row/1"
RESOLVED_BATCH_SCHEMA_VERSION = "spdr_sector_holdings_resolved_batch/3"  # /3: + evidence_day
RESOLVED_SYMBOL_SCHEMA_VERSION = "spdr_sector_holdings_resolved_symbol/1"

HOLDINGS_AS_OF_UNKNOWN = "UNKNOWN"
EVIDENCE_ROOT = "evidence/spdr_sector_holdings"
DERIVED_RETENTION = "APPEND_ONLY_CONTENT_ADDRESSED_DERIVED_ONLY_NO_RAW_WORKBOOK"

# Coarse, ordered (ascending) weight buckets. Exact weight is intentionally
# never stored (see "Licensing" above); ordering is enough to resolve the
# rule's "largest weight ETF" tie-break for the rare symbol held by more
# than one sector fund.
WEIGHT_BUCKETS = ("LT_1PCT", "GE_1PCT_LT_5PCT", "GE_5PCT")

AUTHORITY = {
    "evidence_capture_only": True,
    "sector_membership_authorized": False,
    "candidate_validity_authorized": False,
    "entry_eligibility_authorized": False,
    "action_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class SpdrSectorHoldingsError(ValueError):
    """The capture cannot prove the declared holdings-derived mapping."""


def fail(code: str):
    raise SpdrSectorHoldingsError(code)


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        fail(code)


def _parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code)
    if parsed.isoformat() != value:
        fail(code)
    return parsed


def weight_bucket(weight_pct: float) -> str:
    if weight_pct < 1.0:
        return "LT_1PCT"
    if weight_pct < 5.0:
        return "GE_1PCT_LT_5PCT"
    return "GE_5PCT"


# ─────────────────────────────────────────────────────────────────────────
# Parsing -- tolerant header detection over the workbook's first sheet.
# SSGA's own column names have varied by fund/date in the past ("Ticker" vs
# "Identifier", "Weight" vs "Weight (%)"); this looks for the first
# plausible symbol + weight columns rather than a single hardcoded layout.
# ─────────────────────────────────────────────────────────────────────────

SYMBOL_HEADER_NAMES = {"ticker", "identifier", "symbol"}
WEIGHT_HEADER_NAMES = {"weight", "weight (%)", "weight(%)", "index weight", "% of net assets"}
NAME_HEADER_NAMES = {"name", "security description", "description"}

# Rows that are not a real holding (cash, disclaimers, totals) -- symbol
# cells matching any of these (case-insensitive) are skipped.
NOT_A_HOLDING_SYMBOLS = {"", "cash", "cash_usd", "n/a", "-", "net cash", "total"}


_AS_OF_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%B %d %Y", "%b %d %Y")
# CIO review 2026-09-15 (PR #767): the date must be POSITIONALLY anchored
# right after the "as of" phrase, in the same cell -- an unrelated earlier
# date in the same cell (e.g. "Fund inception 01/01/2001. Holdings as of
# 09/12/2026") or an earlier disclaimer row's own "as of <date>" must never
# be picked up as a decoy. This is deliberately tolerant of spelling/format
# (the real header wording is still UNVERIFIED -- see module docstring)
# but never tolerant of *position*.
_AS_OF_ANCHORED_DATE = re.compile(
    r"as[\s-]*of\s*:?\s*("
    r"\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}-[A-Za-z]{3}-\d{4}"
    r"|[A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}"
    r")",
    re.IGNORECASE,
)


def _anchored_dates_in_text(text: str) -> list[dt.date]:
    dates = []
    for match in _AS_OF_ANCHORED_DATE.finditer(text):
        token = match.group(1).replace(",", "").replace(".", "")
        for fmt in _AS_OF_DATE_FORMATS:
            try:
                dates.append(dt.datetime.strptime(token, fmt).date())
                break
            except ValueError:
                continue
    return dates


def parse_holdings_as_of_date(raw: bytes) -> str | None:
    """The fund's own "as of" date from a header row above the real column
    header (e.g. "Holdings are as of 09/12/2026") -- see module docstring.
    Only a date immediately following the "as of" phrase, in the same
    cell, is a candidate (never an unrelated earlier date in that cell or
    a different row's own "as of" claim). Returns an ISO date string only
    when exactly one distinct candidate date is found across the whole
    scanned region; returns ``None`` (no candidate, or two+ CONFLICTING
    candidates -- this never guesses which one "wins") otherwise. Never
    raises -- the caller records ``HOLDINGS_AS_OF_UNKNOWN``, it never fails
    the capture.
    """
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        return None
    try:
        sheet = workbook.worksheets[0]
        found: set[dt.date] = set()
        for row in sheet.iter_rows(values_only=True, max_row=20):
            for cell in row:
                if isinstance(cell, str):
                    found.update(_anchored_dates_in_text(cell))
        if len(found) == 1:
            return found.pop().isoformat()
        return None
    except Exception:
        return None
    finally:
        workbook.close()


def parse_holdings_workbook(raw: bytes) -> list[dict]:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        fail("HOLDINGS_WORKBOOK_UNREADABLE")
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header_row = None
        header_index = {}
        for row in rows:
            candidate = {
                str(cell).strip().lower(): i
                for i, cell in enumerate(row)
                if isinstance(cell, str) and cell.strip()
            }
            symbol_col = next((candidate[n] for n in SYMBOL_HEADER_NAMES if n in candidate), None)
            weight_col = next((candidate[n] for n in WEIGHT_HEADER_NAMES if n in candidate), None)
            if symbol_col is not None and weight_col is not None:
                header_row, header_index = row, candidate
                name_col = next((header_index[n] for n in NAME_HEADER_NAMES if n in header_index), None)
                break
        else:
            fail("HOLDINGS_HEADER_NOT_FOUND")

        out = []
        for row in rows:
            if row is None or symbol_col >= len(row) or weight_col >= len(row):
                continue
            symbol_cell = row[symbol_col]
            if symbol_cell is None:
                continue
            symbol = str(symbol_cell).strip().upper()
            if symbol.lower() in NOT_A_HOLDING_SYMBOLS:
                continue
            weight_cell = row[weight_col]
            if weight_cell is None:
                continue
            try:
                weight_pct = float(str(weight_cell).replace("%", "").strip())
            except ValueError:
                continue
            name = None
            if name_col is not None and name_col < len(row) and row[name_col] is not None:
                name = str(row[name_col]).strip()
            out.append({"symbol": symbol, "weight_pct": weight_pct, "name": name})
        if not out:
            fail("HOLDINGS_NO_ROWS_PARSED")
        return out
    finally:
        workbook.close()


# ─────────────────────────────────────────────────────────────────────────
# Fetch -- injectable getter so tests never touch the network.
# ─────────────────────────────────────────────────────────────────────────

def holdings_url(ticker: str) -> str:
    return HOLDINGS_URL_TEMPLATE.format(ticker_lower=ticker.lower())


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        fail(f"HOLDINGS_HTTP_ERROR_{exc.code}")
    except urllib.error.URLError:
        fail("HOLDINGS_HTTP_UNREACHABLE")


def fetch_holdings(ticker: str, *, getter=_get) -> bytes:
    if ticker not in SECTOR_ETFS:
        fail("HOLDINGS_TICKER_NOT_IN_UNIVERSE")
    return getter(holdings_url(ticker))


# ─────────────────────────────────────────────────────────────────────────
# Derive -- rank + bucket within a single ETF's own holdings that day.
# ─────────────────────────────────────────────────────────────────────────

def derive_mapping_rows(ticker: str, holdings: list[dict]) -> list[dict]:
    ranked = sorted(holdings, key=lambda h: h["weight_pct"], reverse=True)
    rows = []
    for rank, holding in enumerate(ranked, start=1):
        rows.append({
            "schema_version": MAPPING_ROW_SCHEMA_VERSION,
            "symbol": holding["symbol"],
            "sector_etf": ticker,
            "weight_rank": rank,
            "weight_bucket": weight_bucket(holding["weight_pct"]),
        })
    return rows


def build_capture(
    captured_at: dt.datetime, ticker: str, raw: bytes, *, source_url: str | None = None
) -> dict:
    if captured_at.tzinfo is None:
        fail("CAPTURE_TIME_NAIVE")
    if ticker not in SECTOR_ETFS:
        fail("HOLDINGS_TICKER_NOT_IN_UNIVERSE")
    captured_at_utc = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _parse_utc(captured_at_utc, "CAPTURE_TIME_INVALID")

    holdings = parse_holdings_workbook(raw)
    mapping_rows = derive_mapping_rows(ticker, holdings)
    raw_sha256 = sha256_bytes(raw)
    holdings_as_of_date = parse_holdings_as_of_date(raw) or HOLDINGS_AS_OF_UNKNOWN

    day = captured_at.astimezone(UTC).date().isoformat()
    # Evidence is keyed by what the workbook describes, not by when this
    # process fetched it. GitHub's schedule for this collector runs hours
    # late, so two fetches can land on one UTC date (2026-09-17: 00:03Z with
    # as-of 09-15, then 22:10Z with a newer as-of) and collide on the same
    # path with different bytes -- APPEND_ONLY_COLLISION, a red run every
    # day. The as-of date is the capture's real identity. When the workbook
    # carries no parseable as-of, the capture day stays the key, exactly as
    # before.
    evidence_day = day if holdings_as_of_date == HOLDINGS_AS_OF_UNKNOWN else holdings_as_of_date
    capture = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "sector_etf": ticker,
        "captured_at_utc": captured_at_utc,
        "capture_date_utc": day,
        "holdings_as_of_date": holdings_as_of_date,
        "evidence_day": evidence_day,
        "source_url": source_url or holdings_url(ticker),
        "raw_sha256": raw_sha256,
        "raw_byte_length": len(raw),
        "holding_count": len(holdings),
        "mapping": mapping_rows,
        "retention": DERIVED_RETENTION,
        "authority": AUTHORITY,
    }
    capture_id = sha256_bytes(canonical_bytes({k: v for k, v in capture.items() if k != "authority"}))
    capture["capture_id"] = capture_id
    capture_bytes = json.dumps(
        capture, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    capture_path = f"{EVIDENCE_ROOT}/derived/{evidence_day}/{ticker}.json"
    return {"capture": capture, "capture_bytes": capture_bytes, "capture_path": capture_path}


def _write_once(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            fail("APPEND_ONLY_COLLISION")
        return False
    path.write_bytes(data)
    return True


# The identity of a capture is the ETF and the exact source workbook it came
# from -- not when this process happened to fetch it.  ``captured_at_utc``
# (and therefore ``capture_id``, and any schema field added later) differ on
# every run, so a re-run inside the same UTC capture day -- a retry after a
# partial failure, or the server dispatcher catching up a missed slot -- used
# to hit APPEND_ONLY_COLLISION on the tickers the earlier run had already
# stored and fail the whole job (run 35031279570).  Re-observing the same
# workbook is expected and harmless: keep the existing file (its earlier,
# tighter ``captured_at_utc``) and never rewrite it.  A different workbook
# under the same path is still a genuine append-only violation.
_CAPTURE_IDENTITY_FIELDS = ("sector_etf", "raw_sha256", "raw_byte_length", "mapping")
# The batch manifest's identity is which ETFs were captured, what they describe
# and what was resolved from them -- not when this run fetched them. Two runs
# that share an evidence_day (the same as-of re-fetched, which is exactly what
# 2026-09-16 and 2026-09-17 did) must not collide on captured_at_utc alone.
_BATCH_IDENTITY_FIELDS = (
    "tickers_captured", "batch_complete", "resolved_symbol_count",
    "holdings_as_of_dates", "holdings_as_of_date", "evidence_day",
)


def _write_identity_once(path: Path, data: bytes, identity_fields: tuple[str, ...]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file():
            fail("APPEND_ONLY_COLLISION")
        existing_bytes = path.read_bytes()
        if existing_bytes == data:
            return False
        try:
            existing = json.loads(existing_bytes)
            incoming = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            fail("APPEND_ONLY_COLLISION")
        if any(existing.get(f) != incoming.get(f) for f in identity_fields):
            fail("APPEND_ONLY_COLLISION")
        return False
    path.write_bytes(data)
    return True


def _safe_evidence_path(root: Path, value: str, prefix: str) -> Path:
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        fail("EVIDENCE_PATH_INVALID")
    if not value.startswith(prefix):
        fail("EVIDENCE_PATH_INVALID")
    resolved_root = root.resolve()
    resolved = (root / value).resolve()
    if resolved_root not in resolved.parents:
        fail("EVIDENCE_PATH_INVALID")
    return resolved


def publish_capture(root: Path, bundle: dict) -> dict:
    path = _safe_evidence_path(root, bundle["capture_path"], f"{EVIDENCE_ROOT}/derived/")
    created = _write_identity_once(path, bundle["capture_bytes"], _CAPTURE_IDENTITY_FIELDS)
    return {"capture_path": bundle["capture_path"], "created": created,
            "capture_id": bundle["capture"]["capture_id"]}


# ─────────────────────────────────────────────────────────────────────────
# Cross-ETF resolution -- exact weights, in memory only, never committed.
# See module docstring, "Cross-ETF 'largest weight' resolution".
# ─────────────────────────────────────────────────────────────────────────

def resolve_cross_etf_winners(per_ticker_holdings: dict[str, list[dict]]) -> dict[str, dict]:
    """Per-symbol cross-ETF "largest weight" winner, from exact weights.

    ``per_ticker_holdings`` is ``{ticker: parse_holdings_workbook(...)}``
    for every ticker in a *complete* batch. Only called when the batch is
    complete -- callers must not call this with a partial set of tickers,
    since a missing ETF could have been the true winner for some symbol.
    Returns ``{symbol: {"primary_sector_etf", "holder_etf_count", "tie"}}``;
    the exact weight values used to decide this are not part of the return
    value and are never persisted by any caller in this module.
    """
    by_symbol: dict[str, list[tuple[str, float]]] = {}
    for ticker, holdings in per_ticker_holdings.items():
        for holding in holdings:
            by_symbol.setdefault(holding["symbol"], []).append((ticker, holding["weight_pct"]))

    resolved = {}
    for symbol, entries in by_symbol.items():
        max_weight = max(weight for _, weight in entries)
        winners = sorted(ticker for ticker, weight in entries if weight == max_weight)
        resolved[symbol] = {
            "schema_version": RESOLVED_SYMBOL_SCHEMA_VERSION,
            "symbol": symbol,
            "primary_sector_etf": winners[0],
            "holder_etf_count": len(entries),
            "tie": len(winners) > 1,
        }
    return resolved


def build_batch(captured_at: dt.datetime, per_ticker_raw: dict[str, bytes]) -> dict:
    """Build one day's full capture batch: a per-ETF capture bundle for
    every ticker actually fetched (``per_ticker_raw``), plus -- only when
    every one of the 11 sector ETFs was fetched this run (a "complete
    batch") -- the cross-ETF resolved symbol mapping. An incomplete batch
    still publishes each ETF's own per-ETF capture (unaffected, unchanged
    shape) but carries no resolved symbols file at all; see module
    docstring.
    """
    if captured_at.tzinfo is None:
        fail("CAPTURE_TIME_NAIVE")
    invalid = sorted(set(per_ticker_raw) - set(SECTOR_ETFS))
    if invalid:
        fail("HOLDINGS_TICKER_NOT_IN_UNIVERSE")

    captured_at_utc = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _parse_utc(captured_at_utc, "CAPTURE_TIME_INVALID")
    day = captured_at.astimezone(UTC).date().isoformat()

    per_ticker_capture = {}
    per_ticker_holdings = {}
    for ticker, raw in per_ticker_raw.items():
        per_ticker_capture[ticker] = build_capture(captured_at, ticker, raw)
        per_ticker_holdings[ticker] = parse_holdings_workbook(raw)

    tickers_captured = sorted(per_ticker_raw)
    complete = tickers_captured == sorted(SECTOR_ETFS)

    resolved_symbols = resolve_cross_etf_winners(per_ticker_holdings) if complete else {}
    symbols_list = [resolved_symbols[s] for s in sorted(resolved_symbols)]

    # Per-ETF "as of" date (see module docstring, "Holdings 'As of' date")
    # plus a conservative batch-level aggregate: only a single date if
    # every captured ETF agrees on one KNOWN date, HOLDINGS_AS_OF_UNKNOWN
    # otherwise (a disagreement or any missing date is never averaged or
    # guessed away).
    holdings_as_of_dates = {
        ticker: per_ticker_capture[ticker]["capture"]["holdings_as_of_date"]
        for ticker in tickers_captured
    }
    distinct_dates = set(holdings_as_of_dates.values())
    if len(distinct_dates) == 1 and HOLDINGS_AS_OF_UNKNOWN not in distinct_dates:
        holdings_as_of_date = distinct_dates.pop()
    else:
        holdings_as_of_date = HOLDINGS_AS_OF_UNKNOWN

    evidence_day = day if holdings_as_of_date == HOLDINGS_AS_OF_UNKNOWN else holdings_as_of_date

    manifest = {
        "schema_version": RESOLVED_BATCH_SCHEMA_VERSION,
        "captured_at_utc": captured_at_utc,
        "capture_date_utc": day,
        "evidence_day": evidence_day,
        "tickers_captured": tickers_captured,
        "batch_complete": complete,
        "resolved_symbol_count": len(symbols_list),
        "holdings_as_of_dates": holdings_as_of_dates,
        "holdings_as_of_date": holdings_as_of_date,
        "authority": AUTHORITY,
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    manifest_path = f"{EVIDENCE_ROOT}/resolved/{evidence_day}/manifest.json"

    symbols_bytes = None
    symbols_path = None
    if complete:
        symbols_bytes = json.dumps(
            symbols_list, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8") + b"\n"
        symbols_path = f"{EVIDENCE_ROOT}/resolved/{evidence_day}/symbols.json"

    return {
        "per_ticker_capture": per_ticker_capture,
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "manifest_path": manifest_path,
        "symbols_bytes": symbols_bytes,
        "symbols_path": symbols_path,
        "batch_complete": complete,
    }


def publish_batch(root: Path, batch: dict) -> dict:
    per_ticker_summary = {}
    for ticker, bundle in batch["per_ticker_capture"].items():
        per_ticker_summary[ticker] = publish_capture(root, bundle)

    manifest_path = _safe_evidence_path(root, batch["manifest_path"], f"{EVIDENCE_ROOT}/resolved/")
    _write_identity_once(manifest_path, batch["manifest_bytes"], _BATCH_IDENTITY_FIELDS)

    if batch["symbols_bytes"] is not None:
        # symbols.json carries no timestamp: byte equality is the identity.
        symbols_path = _safe_evidence_path(root, batch["symbols_path"], f"{EVIDENCE_ROOT}/resolved/")
        _write_once(symbols_path, batch["symbols_bytes"])

    return {
        "per_ticker": per_ticker_summary,
        "manifest_path": batch["manifest_path"],
        "batch_complete": batch["batch_complete"],
        "resolved_symbol_count": batch["manifest"]["resolved_symbol_count"],
        "symbols_path": batch["symbols_path"],
    }


def write_latest_pointer(root: Path, day: str, per_ticker: dict[str, dict]) -> Path:
    """data/latest_spdr_sector_holdings.json -- small, mutable pointer (like
    this repo's other data/latest_*.json files) at the most recent evidence
    day (the holdings as-of date when the workbook carries one), for
    convenience only; the append-only evidence under
    evidence/spdr_sector_holdings/derived/ remains authoritative."""
    pointer = {
        "schema_version": "spdr_sector_holdings_latest_pointer/2",
        "evidence_day": day,
        "sector_etfs_captured": sorted(per_ticker),
        "complete": sorted(per_ticker) == sorted(SECTOR_ETFS),
        "capture_ids": {ticker: per_ticker[ticker]["capture_id"] for ticker in sorted(per_ticker)},
    }
    path = root / "data" / "latest_spdr_sector_holdings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


# ─────────────────────────────────────────────────────────────────────────
# CLI entrypoint -- workflow_dispatch only (see
# .github/workflows/spdr-sector-holdings.yml). No cron is enabled by this
# PR; task recommends the daily schedule because this data is physically
# time-gated (see module docstring), but enabling it is a separate,
# explicit user/CIO approval.
# ─────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers", default=None,
        help=(
            "Single space-separated string of tickers to capture (default: "
            "all 11 SPDR Select Sector ETFs). Every ticker must be one of: "
            + " ".join(SECTOR_ETFS) + ". Passed as one shell-quoted argument "
            "(see .github/workflows/spdr-sector-holdings.yml) and split/"
            "validated here, not by the shell."
        ),
    )
    args = parser.parse_args(argv)

    tickers = args.tickers.split() if args.tickers else list(SECTOR_ETFS)
    invalid = sorted(set(tickers) - set(SECTOR_ETFS))
    if invalid:
        print(
            f"HOLDINGS_TICKER_NOT_IN_UNIVERSE: {invalid} not in {list(SECTOR_ETFS)}",
            file=sys.stderr,
        )
        return 2

    captured_at = dt.datetime.now(tz=UTC)
    per_ticker_raw: dict[str, bytes] = {}
    for ticker in tickers:
        try:
            per_ticker_raw[ticker] = fetch_holdings(ticker)
        except SpdrSectorHoldingsError as exc:
            # A single ETF's fetch failure does not abort the whole run --
            # it makes the batch incomplete (see build_batch), which the
            # reader then treats as "no cross-ETF resolution today", not as
            # a crash.
            print(f"WARN: fetch failed for {ticker}: {exc}", file=sys.stderr)

    batch = build_batch(captured_at, per_ticker_raw)
    summary = publish_batch(ROOT, batch)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if summary["per_ticker"]:
        # Point at the directory the evidence actually went to (as-of keyed).
        write_latest_pointer(ROOT, batch["manifest"]["evidence_day"], summary["per_ticker"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
