#!/usr/bin/env python3
"""RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1 -- point-in-time reader.

US individual stock sector membership = the latest official SPDR sector ETF
holdings published before the decision date; if held by more than one
sector ETF, the ETF with the largest weight; if held by none, UNKNOWN (no
T2); a sector ETF maps to its own sector
(config/rule_registry_v1.json ``RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1``,
source record
evidence/authority/USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json,
sha256 2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd).

This module only *reads* the derived evidence written by
``collectors/spdr_sector_holdings.py`` under
``evidence/spdr_sector_holdings/resolved/<capture_date>/`` -- it does not
import that collector (independent-replay / copy-preserve convention
already used across this repo's evidence readers) and makes no network
call.

"Largest weight" -- exact at capture time, never committed
--------------------------------------------------------------
CIO review 2026-09-15 (PR #761): a symbol's cross-ETF "largest weight"
winner is resolved from the *exact* weights the collector holds in memory
for one instant, at capture time, whenever a single capture run covers all
11 sector ETFs (a "complete batch") -- see
``collectors.spdr_sector_holdings.resolve_cross_etf_winners``. Only the
outcome is committed: ``primary_sector_etf``, ``holder_etf_count`` and a
``tie`` flag (set when two or more ETFs hold the symbol at the exact same
weight -- the winner is then the alphabetically first tied ETF, an
arbitrary, stable, documented fallback). The exact weight itself is never
written to disk (see the collector's module docstring, "Licensing"
section) -- this reader does not need it, because the comparison already
happened, correctly, before the raw weights were discarded.

An earlier revision of this reader approximated the tie-break from a
coarse weight bucket plus a rank that was only comparable *within* one
ETF's own holdings -- which could pick the wrong ETF across funds. That
approximation is gone; ``sector_for_symbol`` now reads the batch's own
resolved answer directly.

Incomplete batches -- fail closed, not silently wrong
--------------------------------------------------------
If a capture run does not cover all 11 ETFs (one or more fetches failed),
cross-ETF resolution for that day is impossible -- an ETF that failed to
fetch might have been the true largest holder of some symbol. Such a batch
is marked ``batch_complete: false`` and carries no ``symbols.json`` at all.
This reader never falls back to a same-day incomplete batch: it walks
backward to the most recent **complete** batch captured at or before the
decision instant. If none exists, the answer is
``NO_POINT_IN_TIME_CAPTURE_AVAILABLE`` (distinct from ``UNKNOWN_NO_T2`` --
data exists and the symbol is genuinely unheld) -- conflating the two would
silently read "we have no usable capture yet" as "no T2", which is wrong.

Not backfillable
------------------
Holdings history cannot be reconstructed after the fact (see the
collector's module docstring); there is no way to manufacture a missing
complete batch for a past day.

Holdings "As of" date, exposed (PR #765 follow-up)
-------------------------------------------------------
``capture_date_utc`` is this collector's own capture wall-clock date, not
necessarily the trading day the holdings file actually describes -- a
22:00 UTC capture may still legitimately reflect the *prior* trading
day's holdings if SSGA has not refreshed a fund's file yet by then; this
reader never assumes same-day freshness. Every ``OK`` result therefore
also carries ``holdings_as_of_date``: for a resolved symbol, the *specific*
winning ETF's own "as of" date (from
``collectors.spdr_sector_holdings.parse_holdings_as_of_date``); for
``UNKNOWN_NO_T2`` (no winning ETF to point at), the whole batch's
conservative aggregate instead. Either can be the literal string
``"UNKNOWN"`` (never a guess) -- including for a manifest committed before
this field existed, which this reader tolerates via ``.get(..., "UNKNOWN")``
rather than crashing on an old record's missing key.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re


UTC = dt.timezone.utc
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Kept identical to collectors/spdr_sector_holdings.py's SECTOR_ETFS by
# construction (both trace to config/free_market_data_contract.json
# ``sector_reference_symbols`` minus SMH); duplicated rather than imported,
# see module docstring.
SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
EVIDENCE_ROOT = "evidence/spdr_sector_holdings"
HOLDINGS_AS_OF_UNKNOWN = "UNKNOWN"


class UsSpdrSectorMappingError(ValueError):
    """The point-in-time sector answer cannot be derived as requested."""


def fail(code: str):
    raise UsSpdrSectorMappingError(code)


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        fail(code)


def _load_batches_before(root: Path, decision: dt.datetime) -> list[dict]:
    resolved_dir = root / EVIDENCE_ROOT / "resolved"
    if not resolved_dir.is_dir():
        return []
    batches = []
    for manifest_path in sorted(resolved_dir.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            fail("HOLDINGS_MANIFEST_UNREADABLE")
        captured_at = _parse_utc(manifest.get("captured_at_utc"), "CAPTURE_TIME_INVALID")
        if captured_at <= decision:
            # The directory name is the batch's own key: the holdings as-of
            # date when the workbook carried one, else the capture day
            # (collectors/spdr_sector_holdings.py). Older batches predate the
            # field, so fall back to the directory they were found in.
            manifest.setdefault("evidence_day", manifest_path.parent.name)
            batches.append(manifest)
    return batches


def _load_symbols(root: Path, evidence_day: str) -> list[dict]:
    path = root / EVIDENCE_ROOT / "resolved" / evidence_day / "symbols.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("HOLDINGS_SYMBOLS_FILE_UNREADABLE")


def sector_for_symbol(root: Path, symbol: str, decision_at: str) -> dict:
    symbol = symbol.strip().upper()
    decision = _parse_utc(decision_at, "DECISION_TIME_INVALID")

    if symbol in SECTOR_ETFS:
        return {
            "status": "OK",
            "symbol": symbol,
            "sector_etf": symbol,
            "basis": "OWN_SECTOR",
        }

    batches = _load_batches_before(root, decision)
    complete_batches = [b for b in batches if b.get("batch_complete")]

    if not complete_batches:
        return {
            "status": "NO_POINT_IN_TIME_CAPTURE_AVAILABLE",
            "symbol": symbol,
            "incomplete_batches_present": bool(batches),
            "explanation": (
                "Only incomplete SPDR sector holdings batches (one or more "
                "ETF fetches failed that day) exist with captured_at_utc <= "
                "decision_at, and no earlier complete batch exists either. "
                "Cross-ETF 'largest weight' cannot be resolved from an "
                "incomplete batch, and holdings history is not backfillable."
                if batches else
                "No SPDR sector holdings batch exists with captured_at_utc "
                "<= decision_at. Holdings history is not backfillable, so "
                "this is a genuine capture gap, not evidence that the "
                "symbol is unheld."
            ),
        }

    # Latest by captured_at_utc: two batches can now share an evidence day
    # (a re-fetch of the same as-of), and the later fetch is the one whose
    # files are on disk under that key.
    latest = max(complete_batches, key=lambda b: (b["captured_at_utc"], b["evidence_day"]))
    symbols = _load_symbols(root, latest["evidence_day"])
    row = next((s for s in symbols if s["symbol"] == symbol), None)

    if row is None:
        return {
            "status": "UNKNOWN_NO_T2",
            "symbol": symbol,
            "sector_etf": None,
            "as_of_capture_date_utc": latest["capture_date_utc"],
            "holdings_as_of_date": latest.get("holdings_as_of_date", HOLDINGS_AS_OF_UNKNOWN),
        }

    winner_as_of_date = latest.get("holdings_as_of_dates", {}).get(
        row["primary_sector_etf"], HOLDINGS_AS_OF_UNKNOWN
    )
    return {
        "status": "OK",
        "symbol": symbol,
        "sector_etf": row["primary_sector_etf"],
        "basis": "LARGEST_WEIGHT_ETF" if row["holder_etf_count"] > 1 else "SOLE_HOLDER",
        "as_of_capture_date_utc": latest["capture_date_utc"],
        "holdings_as_of_date": winner_as_of_date,
        "holder_etf_count": row["holder_etf_count"],
        "tie": row["tie"],
    }
