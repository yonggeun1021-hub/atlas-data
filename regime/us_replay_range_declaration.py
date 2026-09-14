#!/usr/bin/env python3
"""US-DATA-1 U3: the rule-fixed US historical replay range, declared before any run.

Input: one ``us_replay_coverage_probe_summary/1`` produced by
``regime/us_replay_coverage_probe.py analyze``.
Output: one ``us_replay_range_declaration/1``.

The rule, fixed here and written into every declaration as ``rule.text``:

1. The replay symbols are exactly the 15 symbols in
   ``config/free_market_data_contract.json`` ``alpaca.trend_symbols`` (3) plus
   ``alpaca.sector_reference_symbols`` (12).
2. ``data_start`` is the later of two dates. The first is the earliest date on
   which all 15 symbols have an Alpaca IEX daily bar, i.e. the maximum
   per-symbol first-bar date. The second is the earliest date on which ALFRED
   vintages exist for VIXCLS, WRESBAL and TOTBKCR, i.e. the maximum per-series
   first vintage date. If that date falls before 2018-01-01, the first year in
   the ratified calendar scope, counting starts at 2018-01-01 instead
   (``data_start_effective``, reason ``RATIFIED_CALENDAR_SCOPE_STARTS_2018``).
3. Sessions come only from ``market_data/us_official_session_calendar.py``
   under US-SESSION-CALENDAR-SOURCE-V1-20260914. There is no weekday inference.
4. The warm-up is 61 sessions. The longest configured return window is 60
   sessions (``alpaca.return_windows_sessions``), and a 60-session return needs
   the anchor session plus 60 prior closes
   (``collectors/free_market_data.py::_session_return``). The first replay date
   is therefore the 61st session on or after ``data_start``, at index 60.
5. The last replay date is the latest US session whose official close is at
   or before the probe capture instant.
6. Every calendar date from ``data_start`` through the capture date must
   classify as a session or as closed. Any ``UNKNOWN`` date, a truncated
   capture, a missing source, or a binding symbol whose earliest bar is
   left-censored by the probe window blocks the whole declaration. No shorter
   range is ever emitted.
7. No caller argument can select a start, an end, or a sub-range.

The declaration is deterministic. ``declared_at`` is the probe capture instant,
not wall-clock time.

Committed path convention: the probe summary (dates, counts and sha256 values
only) is committed next to the declaration, as
``evidence/us_regime_replay/probe_summary_v1.json`` beside
``evidence/us_regime_replay/range_declaration_v1.json``. ``load_declaration``
(which the replay driver uses for every command) and ``verify`` both rebuild
the declaration with ``declare(summary)`` from that committed summary and
require byte equality with the committed declaration. A hand-edited declaration
is refused even when it is internally consistent and re-signed, for example a
sub-range of the declared sessions with matching counts and hashes.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar as CAL  # noqa: E402
from regime import us_replay_coverage_probe as PROBE  # noqa: E402


SCHEMA = "us_replay_range_declaration/1"
DECLARATION_FILE_NAME = "range_declaration_v1.json"
SUMMARY_FILE_NAME = "probe_summary_v1.json"
DEFAULT_DECLARATION_PATH = ROOT / "evidence" / "us_regime_replay" / DECLARATION_FILE_NAME
DEFAULT_SUMMARY_PATH = ROOT / "evidence" / "us_regime_replay" / SUMMARY_FILE_NAME
CONTRACT_REL = "config/free_market_data_contract.json"
SOURCE_CONFIG_REL = "config/us_session_calendar_source_v1.json"
STATUS_DECLARED = "DECLARED"
STATUS_BLOCKED = "BLOCKED"
MAX_LISTED_UNKNOWN_DATES = 50

RULE_TEXT = (
    "US replay range = [the 61st official US session on or after data_start,"
    " the latest official US session whose close is at or before the probe"
    " capture instant]. data_start = max(max over the 15 replay symbols"
    " (free_market_data_contract alpaca.trend_symbols + sector_reference_symbols)"
    " of the first Alpaca IEX daily bar date, max over VIXCLS/WRESBAL/TOTBKCR of"
    " the first ALFRED vintage date); a data_start before 2018-01-01 (the first"
    " year of the ratified calendar scope) is clamped to 2018-01-01. Sessions come only from"
    " US-SESSION-CALENDAR-SOURCE-V1-20260914 (official NYSE capture for"
    " published years; Alpaca calendar AND IEX SPY bar for 2018+ earlier years;"
    " conflict or missing = US_FINISHED_SESSION_UNKNOWN; no weekday inference)."
    " 61 = longest configured return window (60 sessions) + the anchor session."
    " Any UNKNOWN date from data_start through the capture date, any truncated"
    " or missing source, or a left-censored binding symbol blocks the whole"
    " declaration; no sub-range is ever selected or emitted."
)


class DeclarationError(ValueError):
    """Range declaration invariant failed closed."""


def fail(code: str, detail: str = "") -> None:
    raise DeclarationError(f"{code}:{detail}" if detail else code)


def warm_up_sessions(contract: dict) -> int:
    windows = contract["alpaca"]["return_windows_sessions"]
    if not isinstance(windows, list) or not windows or not all(type(w) is int and w > 0 for w in windows):
        fail("RETURN_WINDOWS_INVALID")
    sessions = max(windows) + 1
    if sessions != 61:
        # The ticket fixes 61 (60-session returns). A changed contract is a
        # re-decision, never silently followed.
        fail("WARM_UP_CONTRACT_CHANGED", str(sessions))
    return sessions


def range_relevant_contract_fields(contract: dict) -> dict:
    """The contract fields the range rule reads. A change to any other field
    (for example a proxy ratification scope) does not move the range."""
    return {
        "alpaca.trend_symbols": list(contract["alpaca"]["trend_symbols"]),
        "alpaca.sector_reference_symbols": list(contract["alpaca"]["sector_reference_symbols"]),
        "alpaca.return_windows_sessions": list(contract["alpaca"]["return_windows_sessions"]),
        "alpaca.feed": contract["alpaca"]["feed"],
        "fred.risk_series": list(contract["fred"]["risk_series"]),
        "fred.liquidity_series": list(contract["fred"]["liquidity_series"]),
    }


def _verify_summary(summary: dict) -> None:
    if not isinstance(summary, dict) or summary.get("schema_version") != PROBE.SUMMARY_SCHEMA:
        fail("SUMMARY_SCHEMA_INVALID")
    body = copy.deepcopy(summary)
    claimed = body.pop("summary_sha256", None)
    if CAL.payload_sha256(body) != claimed:
        fail("SUMMARY_HASH_MISMATCH")


def declare(summary: dict, *, root: Path = ROOT) -> dict:
    _verify_summary(summary)
    contract_path = root / CONTRACT_REL
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    source_config = CAL.load_source_config(root / SOURCE_CONFIG_REL, root=root)
    symbols = PROBE.replay_symbols(contract)
    warm_up = warm_up_sessions(contract)
    blocked: list[str] = []

    if summary["symbols"] != symbols:
        blocked.append("SUMMARY_SYMBOLS_DO_NOT_MATCH_CONTRACT")
    if summary["requests_used"] > PROBE.REQUEST_BUDGET:
        blocked.append("PROBE_REQUEST_BUDGET_EXCEEDED")

    capture_instant = CAL.parse_instant(summary["capture_started_at"], "CAPTURE_INSTANT_INVALID")
    capture_date = CAL.parse_date(summary["capture_date_new_york"], "CAPTURE_DATE_INVALID")
    probe_start = CAL.parse_date(summary["probe_start"], "PROBE_START_INVALID")

    # --- coverage: symbols -------------------------------------------------
    bars = summary["bars"]
    per_symbol = {}
    if bars["status"] != "COMPLETE":
        blocked.append(f"IEX_BARS_{bars['status']}:{bars.get('error')}")
    for symbol in symbols:
        row = bars["per_symbol"].get(symbol) or {}
        earliest = row.get("earliest_bar_date")
        per_symbol[symbol] = {"earliest_bar_date": earliest, "bar_count": row.get("bar_count", 0)}
        if earliest is None:
            blocked.append(f"IEX_BARS_ABSENT:{symbol}")
    symbol_start = None
    binding_symbol = None
    if all(value["earliest_bar_date"] for value in per_symbol.values()):
        binding_symbol, symbol_start = max(
            ((symbol, value["earliest_bar_date"]) for symbol, value in per_symbol.items()),
            key=lambda item: (item[1], item[0]),
        )
        censor_limit = probe_start + dt.timedelta(days=PROBE.LEFT_CENSOR_DAYS)
        if CAL.parse_date(symbol_start, "EARLIEST_BAR_INVALID") <= censor_limit:
            blocked.append(f"BINDING_SYMBOL_LEFT_CENSORED_BY_PROBE_WINDOW:{binding_symbol}")

    # --- coverage: ALFRED vintages ------------------------------------------
    fred_block = {}
    for series in PROBE.FRED_SERIES:
        row = summary["fred"].get(series) or {}
        fred_block[series] = {"earliest_vintage_date": row.get("earliest_vintage_date"), "raw_sha256": row.get("raw_sha256")}
        if row.get("status") != "PARSED" or not row.get("earliest_vintage_date"):
            blocked.append(f"ALFRED_VINTAGES_UNAVAILABLE:{series}:{row.get('error')}")
    fred_start = None
    if all(value["earliest_vintage_date"] for value in fred_block.values()):
        fred_start = max(value["earliest_vintage_date"] for value in fred_block.values())

    # --- calendar sources ---------------------------------------------------
    nyse = summary["nyse"]
    nasdaq = summary["nasdaq"]
    calendar = summary["alpaca_calendar"]
    if nyse["status"] != "PARSED":
        blocked.append(f"NYSE_OFFICIAL_CAPTURE_UNAVAILABLE:{nyse.get('error')}")
    if calendar["status"] != "PARSED":
        blocked.append(f"ALPACA_CALENDAR_UNAVAILABLE:{calendar.get('error')}")
    spy = bars["per_symbol"].get("SPY") or {}
    sources = {
        "nyse": nyse["parsed"] if nyse["status"] == "PARSED" else None,
        "nasdaq": nasdaq["parsed"] if nasdaq["status"] == "PARSED" else None,
        "alpaca_calendar": calendar["parsed"] if calendar["status"] == "PARSED" else None,
        "alpaca_calendar_window": calendar.get("window"),
        "spy_bar_dates": set(spy.get("bar_dates") or []) if bars["status"] == "COMPLETE" else None,
        "spy_bar_window": [summary["probe_start"], summary["capture_date_new_york"]],
    }

    data_start = None
    if symbol_start and fred_start:
        data_start = max(symbol_start, fred_start)

    range_block = None
    calendar_counts = None
    missing_bar_disclosure = None
    unknown_dates: list[str] = []
    if data_start and not blocked:
        start = CAL.parse_date(data_start, "DATA_START_INVALID")
        if start.year < CAL.HISTORICAL_FIRST_YEAR:
            start = dt.date(CAL.HISTORICAL_FIRST_YEAR, 1, 1)
            data_start_effective_reason = "RATIFIED_CALENDAR_SCOPE_STARTS_2018"
        else:
            data_start_effective_reason = "DATA_COVERAGE"
        day_calendar = CAL.build_day_calendar(start, capture_date, sources)
        for row in day_calendar:
            if row["status"] != CAL.STATUS_UNKNOWN:
                continue
            day = dt.date.fromisoformat(row["date"])
            if day == capture_date:
                # An unknown capture date blocks only if a session could already
                # have completed, i.e. the capture instant is after 09:30 New York.
                opening = dt.datetime.combine(day, dt.time(9, 30), tzinfo=CAL.NY)
                if capture_instant <= opening:
                    continue
            unknown_dates.append(row["date"])
        if unknown_dates:
            blocked.append(f"CALENDAR_UNKNOWN_DATES_INSIDE_RANGE:{len(unknown_dates)}")
        sessions = CAL.completed_sessions(day_calendar, capture_instant)
        if len(sessions) < warm_up:
            blocked.append(f"FEWER_SESSIONS_THAN_WARM_UP:{len(sessions)}")
        if not blocked:
            replay = sessions[warm_up - 1:]
            session_set = set(sessions)
            calendar_counts = {
                basis: sum(1 for row in day_calendar if row["basis"] == basis and row["date"] in session_set)
                for basis in (CAL.BASIS_OFFICIAL, CAL.BASIS_TWO_SOURCE)
            }
            missing_bar_disclosure = {}
            for symbol in symbols:
                have = set(bars["per_symbol"][symbol]["bar_dates"])
                first = per_symbol[symbol]["earliest_bar_date"]
                missing_bar_disclosure[symbol] = sum(1 for day in sessions if day >= first and day not in have)
            range_block = {
                "data_start": data_start,
                "data_start_effective": start.isoformat(),
                "data_start_effective_reason": data_start_effective_reason,
                "binding_symbol": binding_symbol,
                "symbol_coverage_start": symbol_start,
                "alfred_coverage_start": fred_start,
                "warm_up_sessions": warm_up,
                "warm_up_window_first_session": sessions[0],
                "warm_up_window_rule": "first_replay_date is session index 60 (the 61st) counted from warm_up_window_first_session",
                "first_replay_date": replay[0],
                "last_replay_date": replay[-1],
                "session_count": len(replay),
                "sessions_sha256": CAL.payload_sha256(replay),
                "sessions": replay,
            }

    inputs = {
        "probe_summary_sha256": summary["summary_sha256"],
        "capture_manifest_sha256": summary["capture_manifest_sha256"],
        "nyse_raw_sha256": nyse.get("raw_sha256"),
        "nasdaq_raw_sha256": nasdaq.get("raw_sha256"),
        "nasdaq_cross_check_status": nasdaq["status"],
        "alpaca_calendar_raw_sha256": calendar.get("raw_sha256"),
        "iex_bars_response_sha256": list(bars.get("response_sha256") or []),
        "alfred_vintagedates_raw_sha256": {series: value["raw_sha256"] for series, value in fred_block.items()},
        "free_market_data_contract": {
            "path": CONTRACT_REL,
            "range_relevant_fields_sha256": CAL.payload_sha256(range_relevant_contract_fields(contract)),
        },
        "session_calendar_source": {"path": SOURCE_CONFIG_REL, "sha256": CAL.file_sha256(root / SOURCE_CONFIG_REL)},
        "ratification": copy.deepcopy(source_config["ratification"]),
    }
    declaration = {
        "schema_version": SCHEMA,
        "market": "US",
        "wbs": "US-DATA-1-U3",
        "status": STATUS_BLOCKED if blocked or range_block is None else STATUS_DECLARED,
        "blocked_reasons": sorted(set(blocked)) if blocked or range_block is None else [],
        "unknown_dates_sample": unknown_dates[:MAX_LISTED_UNKNOWN_DATES],
        "declared_at": summary["capture_started_at"],
        "capture_date_new_york": summary["capture_date_new_york"],
        "rule": {
            "text": RULE_TEXT,
            "replay_symbols": symbols,
            "replay_symbol_count": len(symbols),
            "alfred_series": list(PROBE.FRED_SERIES),
            "warm_up_sessions": warm_up,
            "warm_up_derivation": "max(alpaca.return_windows_sessions)=60 + 1 anchor session",
            "sub_range_selection_allowed": False,
            "truncation_policy": "FAIL_CLOSED_WHOLE_DECLARATION",
            "calendar_ratification_id": source_config["ratification"]["ratification_id"],
        },
        "coverage": {"per_symbol": per_symbol, "alfred": fred_block},
        "calendar_session_basis_counts": calendar_counts,
        "disclosures": {
            "sessions_without_iex_bar_after_symbol_start": missing_bar_disclosure,
            "note": "Disclosed only; a date whose symbol bar is missing fails inside the replay module and is reported, never removed from the range.",
        },
        "inputs": inputs,
        "range": range_block,
        "authority": {
            "range_declaration_only": True,
            "population_run_authorized": False,
            "evidence_accepted": False,
            "runtime_authorized": False,
            "order_authorized": False,
            "trading_authorized": False,
            "real_authorized": False,
        },
    }
    if declaration["status"] == STATUS_BLOCKED and not declaration["blocked_reasons"]:
        declaration["blocked_reasons"] = ["COVERAGE_START_NOT_DETERMINED"]
    declaration["declaration_sha256"] = CAL.payload_sha256(declaration)
    return declaration


def summary_path_for(declaration_path: Path) -> Path:
    """The committed probe summary that must sit next to a declaration."""
    declaration_path = Path(declaration_path)
    if declaration_path.name != DECLARATION_FILE_NAME:
        fail("DECLARATION_PATH_CONVENTION", declaration_path.name)
    return declaration_path.with_name(SUMMARY_FILE_NAME)


def rebuild_matches(declaration_bytes: bytes, summary: dict, *, root: Path) -> dict:
    """Rebuild the declaration from ``summary`` and require byte equality."""
    rebuilt = declare(summary, root=root)
    if CAL.canonical_bytes(rebuilt) != declaration_bytes:
        fail("DECLARATION_DOES_NOT_REBUILD_FROM_COMMITTED_SUMMARY")
    return rebuilt


def load_declaration(path: Path) -> dict:
    """Load a committed declaration and prove it is ``declare(summary)``.

    Checks, in order: the declaration hash, status and rule constants, the
    session list's internal consistency, contract and calendar-source drift,
    and finally a byte-identical rebuild from the probe summary committed next
    to it (``summary_path_for``). The rebuild is what refuses a consistent
    re-signed sub-range.
    """
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("DECLARATION_UNREADABLE", str(path))
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
        fail("DECLARATION_SCHEMA_INVALID")
    body = copy.deepcopy(value)
    claimed = body.pop("declaration_sha256", None)
    if CAL.payload_sha256(body) != claimed:
        fail("DECLARATION_HASH_MISMATCH")
    if value.get("status") != STATUS_DECLARED or value.get("blocked_reasons"):
        fail("DECLARATION_NOT_DECLARED", ",".join(value.get("blocked_reasons") or []))
    rule = value["rule"]
    if (
        rule.get("text") != RULE_TEXT
        or rule.get("warm_up_sessions") != 61
        or rule.get("sub_range_selection_allowed") is not False
        or rule.get("replay_symbol_count") != 15
        or rule.get("replay_symbols") != PROBE.replay_symbols()
    ):
        fail("DECLARATION_RULE_CHANGED")
    sessions = value["range"]["sessions"]
    if (
        not sessions
        or sessions != sorted(set(sessions))
        or CAL.payload_sha256(sessions) != value["range"]["sessions_sha256"]
        or len(sessions) != value["range"]["session_count"]
        or sessions[0] != value["range"]["first_replay_date"]
        or sessions[-1] != value["range"]["last_replay_date"]
    ):
        fail("DECLARATION_SESSIONS_INCONSISTENT")
    contract = json.loads((ROOT / CONTRACT_REL).read_text(encoding="utf-8"))
    if (
        CAL.payload_sha256(range_relevant_contract_fields(contract))
        != value["inputs"]["free_market_data_contract"]["range_relevant_fields_sha256"]
    ):
        fail("DECLARATION_INPUT_DRIFT", CONTRACT_REL)
    if CAL.file_sha256(ROOT / SOURCE_CONFIG_REL) != value["inputs"]["session_calendar_source"]["sha256"]:
        fail("DECLARATION_INPUT_DRIFT", SOURCE_CONFIG_REL)
    summary_path = summary_path_for(path)
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail("DECLARATION_PROBE_SUMMARY_NOT_COMMITTED", summary_path.name)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("DECLARATION_PROBE_SUMMARY_UNREADABLE", summary_path.name)
    rebuild_matches(raw, summary, root=ROOT)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="US replay range declaration (no sub-range arguments exist)")
    sub = parser.add_subparsers(dest="command", required=True)
    dec = sub.add_parser("declare")
    dec.add_argument("--summary", type=Path, required=True)
    dec.add_argument("--out", type=Path, required=True)
    ver = sub.add_parser("verify")
    ver.add_argument("--declaration", type=Path, default=DEFAULT_DECLARATION_PATH)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify":
        # verify takes no --summary: the summary is the one committed next to
        # the declaration, so the pair cannot be mixed from different places.
        load_declaration(args.declaration)
        print(f"PASS_US_REPLAY_RANGE_DECLARATION_VERIFIED:{json.loads(args.declaration.read_text(encoding='utf-8'))['declaration_sha256']}")
        return 0
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    declaration = declare(summary)
    if args.command == "declare":
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(CAL.canonical_bytes(declaration))
        print(json.dumps({
            "status": declaration["status"],
            "blocked_reasons": declaration["blocked_reasons"],
            "session_count": (declaration["range"] or {}).get("session_count"),
            "first_replay_date": (declaration["range"] or {}).get("first_replay_date"),
            "last_replay_date": (declaration["range"] or {}).get("last_replay_date"),
            "declaration_sha256": declaration["declaration_sha256"],
        }, sort_keys=True))
        return 0 if declaration["status"] == STATUS_DECLARED else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DeclarationError, CAL.UsSessionCalendarError, PROBE.ProbeError) as exc:
        print(f"FATAL: {exc}")
        raise SystemExit(1)
