#!/usr/bin/env python3
"""Point-in-time US exchange-listing lookup for the OTC-exclusion
sub-check in ``universe/us_liquidity_sip_source.py``
(``RULE.LIQUIDITY.US_SIP_SOURCE.V1``).

2026-09-15 CIO wiring note: without a listing input, every US name's
``otc_exclusion_status`` was ``UNKNOWN``, and the base ratification
record's own fail_closed clause turns that into an overall ``UNKNOWN`` for
every name -- the reader could never PASS. This module closes that gap by
reading the listing source that was already committed to this repo for a
different purpose, rather than inventing a new one.

★ Source (untouched by this change): ``universe/us_global_universe.py``
  (contract ``config/us_global_universe_contract.json``,
  ``us_global_universe_adapter/1``) forward-captures the two official
  Nasdaq Trader Symbol Directory files
  (``config/us_breadth_forward_contract.json.sources`` --
  ``nasdaq_listed``/``nasdaqlisted.txt`` and
  ``other_listed``/``otherlisted.txt``, fetched by
  ``.github/scripts/us_breadth_forward.py``, ALSO untouched here) into
  ``data/observations/us_global_universe/<date>/packet.json``. This
  module reads that committed packet only -- no network call, no write
  to it, no change to its producer, contract, or workflow.

★ Point-in-time packet selection (2026-09-15 CIO instruction): the packet
  used for an evaluation as-of date is the LATEST
  ``data/observations/us_global_universe/<date>/packet.json`` whose
  directory date is <= the as-of date, found by a plain directory scan
  (this producer has no ``latest`` pointer file). A later packet is never
  used for an earlier as-of date, and a date before this producer's first
  capture -- or a missing ``data/observations/us_global_universe``
  directory entirely -- resolves to no packet at all, never a silent
  fallback to whatever happens to be newest.

★ Classification -- reads the packet's OWN field definitions, cited
  exactly where they already live in this repo, and interprets nothing
  the producer didn't already surface unmodified:
    - ``packet['packet']['source_attribute_rows']`` is one row per Nasdaq
      Trader Symbol Directory source row: ``source_name`` (``"nasdaq_listed"``
      or ``"other_listed"`` -- exactly
      ``config/us_breadth_forward_contract.json.sources[].name``),
      ``primary_symbol``, and the RAW ``fields`` dict. The adapter that
      builds this deliberately does not interpret these fields itself
      (``universe/us_global_universe.py``'s own docstring: "does not ...
      filter security types" -- interpretation is left to a downstream
      reader, which is what this module is).
    - Both source files are official EXCHANGE-listed directories; neither
      ever carries an OTC/pink-sheet security (those live in Nasdaq
      Trader's separate OTC files, which this repo's forward capture does
      not fetch -- see the contract's exactly-two-sources check). A
      symbol's row present in EITHER file is therefore positive evidence
      of exchange listing (``EXCHANGE_LISTED``), independent of which
      single exchange code it carries.
    - ``fields["Test Issue"]`` (``"Y"``/``"N"``, a required field for both
      source files per the contract above) is Nasdaq's own definition
      (https://nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs, already
      cited at ``docs/us_breadth_forward_contract.md``): ``"Y"`` flags a
      test security, not a real tradable instrument -> ``TEST_ISSUE``, a
      confident exclusion distinct from OTC.
    - A symbol absent from every row of the selected packet is ``None``
      (unresolved) -- never assumed listed and never assumed OTC.
    - Financial Status, the ETF flag, and every other column are present
      in ``fields`` but UNUSED here: the base ratification record names
      only "OTC / non-exchange-listed" as an exclusion for this
      condition, and this module does not invent additional exclusion
      grounds beyond what was asked.

★ ``listing_packet_age_days`` (calendar days between the as-of date and
  the selected packet's own directory date) is a RECORDED field only.
  This module does not gate, warn, or degrade anything based on its
  value -- no staleness threshold is invented here.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Exactly config/us_breadth_forward_contract.json's two sources[].name
# values -- a row under any other source_name is not one of the two
# official exchange-listed directories and is ignored, not trusted.
KNOWN_SOURCE_NAMES = ("nasdaq_listed", "other_listed")

EXCHANGE_LISTED = "EXCHANGE_LISTED"
OTC = "OTC"
TEST_ISSUE = "TEST_ISSUE"


class UsListingLookupError(ValueError):
    """A packet or contract shape is invalid -- fail closed (a real bug,
    not an ordinary data gap; a data gap resolves to ``None``/UNKNOWN
    instead of raising -- see ``resolve_listing``)."""


def _fail(code: str, detail: str = "") -> None:
    raise UsListingLookupError(f"{code}:{detail}" if detail else code)


def _observations_dir(root: Path) -> Path:
    return root / "data" / "observations" / "us_global_universe"


def find_latest_packet_date(
    as_of_date: dt.date, root: Path = ROOT
) -> Optional[dt.date]:
    """Latest available packet directory date <= ``as_of_date``, or ``None``.

    Plain directory scan (no ``latest`` pointer file exists for this
    producer). Never returns a date after ``as_of_date``. A missing
    observations directory is treated as "nothing captured yet", not an
    error.
    """
    observations_dir = _observations_dir(root)
    if not observations_dir.is_dir():
        return None
    candidates: list[dt.date] = []
    for child in observations_dir.iterdir():
        if not child.is_dir() or _DATE_DIR_RE.fullmatch(child.name) is None:
            continue
        try:
            candidate_date = dt.date.fromisoformat(child.name)
        except ValueError:
            continue
        if candidate_date <= as_of_date and (child / "packet.json").is_file():
            candidates.append(candidate_date)
    return max(candidates) if candidates else None


def packet_path_for_date(packet_date: dt.date, root: Path = ROOT) -> Path:
    return _observations_dir(root) / packet_date.isoformat() / "packet.json"


def load_listing_rows(packet_path: Path, symbols: Iterable[str]) -> dict:
    """Read one ``packet.json`` once; return only what the requested
    ``symbols`` need. The full ~13,000-row population is never retained
    beyond this filtering pass.

    Returns ``{"sha256": <file sha256>, "rows_by_symbol": {symbol: [row, ...]}}``.
    """
    try:
        raw_bytes = Path(packet_path).read_bytes()
    except OSError as exc:
        _fail("LISTING_PACKET_UNREADABLE", str(exc))
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("LISTING_PACKET_INVALID_JSON", str(exc))
    try:
        rows = parsed["packet"]["source_attribute_rows"]
    except (KeyError, TypeError):
        _fail("LISTING_PACKET_SHAPE_INVALID")
    if not isinstance(rows, list):
        _fail("LISTING_PACKET_SHAPE_INVALID")
    wanted = set(symbols)
    rows_by_symbol: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get("primary_symbol")
        if symbol not in wanted:
            continue
        rows_by_symbol.setdefault(symbol, []).append(row)
    return {"sha256": sha256, "rows_by_symbol": rows_by_symbol}


def classify_symbol_listing(symbol: str, rows_by_symbol: dict) -> tuple[Optional[str], list[str]]:
    """``(exchange_listing_status, reasons)`` for one symbol from
    already-loaded rows (see module docstring for the exact field
    definitions this reads). ``exchange_listing_status`` is
    ``EXCHANGE_LISTED`` / ``OTC`` / ``TEST_ISSUE`` / ``None`` (absent from
    the packet -- the caller must treat that exactly like no packet being
    available at all).
    """
    rows = rows_by_symbol.get(symbol)
    if not rows:
        return None, ["SYMBOL_ABSENT_FROM_LISTING_PACKET"]
    listed = False
    test_issue = False
    for row in rows:
        if row.get("source_name") not in KNOWN_SOURCE_NAMES:
            continue
        listed = True
        fields = row.get("fields") or {}
        if fields.get("Test Issue") == "Y":
            test_issue = True
    if not listed:
        return None, ["SYMBOL_ABSENT_FROM_LISTING_PACKET"]
    if test_issue:
        return TEST_ISSUE, ["CONFIRMED_TEST_ISSUE"]
    return EXCHANGE_LISTED, []


def resolve_listing(
    symbols: Iterable[str],
    as_of_date: dt.date,
    root: Path = ROOT,
) -> dict:
    """Full point-in-time listing lookup for ``symbols`` as of ``as_of_date``.

    ``{
      "packet_date": "YYYY-MM-DD" | None,
      "packet_path": "data/observations/.../packet.json" | None,
      "packet_sha256": str | None,
      "listing_packet_age_days": int | None,   # recorded only, never gates
      "per_symbol": {symbol: {"status": ..., "reasons": [...]}, ...},
    }``
    """
    symbols = list(symbols)
    packet_date = find_latest_packet_date(as_of_date, root=root)
    if packet_date is None:
        return {
            "packet_date": None,
            "packet_path": None,
            "packet_sha256": None,
            "listing_packet_age_days": None,
            "per_symbol": {
                symbol: {"status": None, "reasons": ["NO_LISTING_PACKET_AVAILABLE"]}
                for symbol in symbols
            },
        }
    packet_path = packet_path_for_date(packet_date, root=root)
    loaded = load_listing_rows(packet_path, symbols)
    per_symbol = {
        symbol: dict(zip(("status", "reasons"), classify_symbol_listing(symbol, loaded["rows_by_symbol"])))
        for symbol in symbols
    }
    try:
        packet_path_out = str(packet_path.relative_to(root))
    except ValueError:
        packet_path_out = str(packet_path)
    return {
        "packet_date": packet_date.isoformat(),
        "packet_path": packet_path_out,
        "packet_sha256": loaded["sha256"],
        "listing_packet_age_days": (as_of_date - packet_date).days,
        "per_symbol": per_symbol,
    }
