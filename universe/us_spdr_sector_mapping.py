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
``evidence/spdr_sector_holdings/derived/<capture_date>/<ETF>.json`` -- it
does not import that collector (independent-replay / copy-preserve
convention already used across this repo's evidence readers) and makes no
network call.

"Largest weight" without a stored exact weight
------------------------------------------------
The collector deliberately never retains the exact per-symbol weight (see
its module docstring, "Licensing" section) -- only a coarse, ordered
``weight_bucket`` plus a ``weight_rank`` that is only comparable *within a
single ETF's own holdings that day*. A symbol held by more than one sector
ETF on the same day is expected to be extremely rare (SPDR Select Sector
funds partition the S&P 500 by GICS sector) -- when it happens, this reader
resolves "largest weight" as: higher ``weight_bucket`` wins; on a bucket
tie, lower ``weight_rank`` (closer to the top of its own fund) wins; on a
full tie, the alphabetically first sector ETF wins as an arbitrary, stable,
documented fallback. This ordering is a CIO interpretation of "largest
weight" given the licensing-safe derived data actually retained, not a
user-ratified exact comparison.

Not backfillable
------------------
Holdings history cannot be reconstructed after the fact (see the
collector's module docstring). If no capture exists at or before the
decision instant, this reader returns ``NO_POINT_IN_TIME_CAPTURE_AVAILABLE``
-- distinct from ``UNKNOWN_NO_T2`` (data exists; the symbol just is not
held by any sector ETF that day). Conflating the two would silently read
"we have not started capturing yet" as "no T2", which is wrong.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import re


UTC = dt.timezone.utc
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Kept identical to collectors/spdr_sector_holdings.py's SECTOR_ETFS /
# WEIGHT_BUCKETS by construction (both trace to
# config/free_market_data_contract.json ``sector_reference_symbols`` minus
# SMH); duplicated rather than imported, see module docstring.
SECTOR_ETFS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
WEIGHT_BUCKET_ORDER = {"LT_1PCT": 0, "GE_1PCT_LT_5PCT": 1, "GE_5PCT": 2}
EVIDENCE_ROOT = "evidence/spdr_sector_holdings"


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


def _load_captures_before(root: Path, decision: dt.datetime) -> list[dict]:
    derived_dir = root / EVIDENCE_ROOT / "derived"
    if not derived_dir.is_dir():
        return []
    captures = []
    for path in sorted(derived_dir.glob("*/*.json")):
        try:
            capture = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            fail("HOLDINGS_CAPTURE_UNREADABLE")
        captured_at = _parse_utc(capture.get("captured_at_utc"), "CAPTURE_TIME_INVALID")
        if captured_at <= decision:
            captures.append(capture)
    return captures


def _rank_key(row: dict) -> tuple:
    return (WEIGHT_BUCKET_ORDER[row["weight_bucket"]], -row["weight_rank"])


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

    captures = _load_captures_before(root, decision)
    if not captures:
        return {
            "status": "NO_POINT_IN_TIME_CAPTURE_AVAILABLE",
            "symbol": symbol,
            "explanation": (
                "No SPDR sector holdings capture exists with "
                "captured_at_utc <= decision_at. Holdings history is not "
                "backfillable, so this is a genuine capture gap, not "
                "evidence that the symbol is unheld."
            ),
        }

    latest_day = max(c["capture_date_utc"] for c in captures)
    same_day = [c for c in captures if c["capture_date_utc"] == latest_day]

    holders = []
    for capture in same_day:
        for row in capture.get("mapping", []):
            if row["symbol"] == symbol:
                holders.append(row)

    if not holders:
        return {
            "status": "UNKNOWN_NO_T2",
            "symbol": symbol,
            "sector_etf": None,
            "as_of_capture_date_utc": latest_day,
            "sector_etfs_captured_that_day": sorted(c["sector_etf"] for c in same_day),
        }

    holders.sort(key=_rank_key, reverse=True)
    best_bucket = holders[0]["weight_bucket"]
    best_rank = holders[0]["weight_rank"]
    tied = [h for h in holders if h["weight_bucket"] == best_bucket and h["weight_rank"] == best_rank]
    winner = min(tied, key=lambda h: h["sector_etf"])

    return {
        "status": "OK",
        "symbol": symbol,
        "sector_etf": winner["sector_etf"],
        "basis": "LARGEST_WEIGHT_ETF" if len(holders) > 1 else "SOLE_HOLDER",
        "as_of_capture_date_utc": latest_day,
        "held_by": sorted(h["sector_etf"] for h in holders),
        "tie_broken_alphabetically": len(tied) > 1,
    }
