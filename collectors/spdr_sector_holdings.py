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

URL pattern -- UNVERIFIED, first-dispatch check required
-----------------------------------------------------------
``HOLDINGS_URL_TEMPLATE`` below is State Street's known public daily
holdings download pattern for these funds. Fetching it from this machine to
confirm the exact shape (content-type, workbook layout, header row) was
explicitly out of scope for this PR (no live network call from here). The
first ``workflow_dispatch`` run of ``.github/workflows/spdr-sector-holdings.yml``
*is* that verification -- treat its result as unproven until then. Every
test in ``test/test_spdr_sector_holdings.py`` uses a small in-memory fixture
workbook and a fake HTTP layer; none of it proves the real endpoint's shape.
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

CAPTURE_SCHEMA_VERSION = "spdr_sector_holdings_capture/1"
MAPPING_ROW_SCHEMA_VERSION = "spdr_sector_holdings_mapping_row/1"
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

    day = captured_at.astimezone(UTC).date().isoformat()
    capture = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "sector_etf": ticker,
        "captured_at_utc": captured_at_utc,
        "capture_date_utc": day,
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
    capture_path = f"{EVIDENCE_ROOT}/derived/{day}/{ticker}.json"
    return {"capture": capture, "capture_bytes": capture_bytes, "capture_path": capture_path}


def _write_once(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
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
    created = _write_once(path, bundle["capture_bytes"])
    return {"capture_path": bundle["capture_path"], "created": created,
            "capture_id": bundle["capture"]["capture_id"]}


def write_latest_pointer(root: Path, day: str, per_ticker: dict[str, dict]) -> Path:
    """data/latest_spdr_sector_holdings.json -- small, mutable pointer (like
    this repo's other data/latest_*.json files) at the most recent capture
    day, for convenience only; the append-only evidence under
    evidence/spdr_sector_holdings/derived/ remains authoritative."""
    pointer = {
        "schema_version": "spdr_sector_holdings_latest_pointer/1",
        "capture_date_utc": day,
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
    parser.add_argument("--tickers", nargs="*", default=list(SECTOR_ETFS))
    args = parser.parse_args(argv)

    captured_at = dt.datetime.now(tz=UTC)
    per_ticker = {}
    for ticker in args.tickers:
        raw = fetch_holdings(ticker)
        bundle = build_capture(captured_at, ticker, raw)
        summary = publish_capture(ROOT, bundle)
        per_ticker[ticker] = summary
        print(json.dumps({ticker: summary}, ensure_ascii=False))

    day = captured_at.date().isoformat()
    if per_ticker:
        write_latest_pointer(ROOT, day, per_ticker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
