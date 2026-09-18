#!/usr/bin/env python3
"""US official session calendar producer (US-DATA-1 U3).

``regime/us_paper_runtime.py::load_calendar`` refuses every decision until an
active adoption identity binds, by sha256, a committed file whose shape is
exactly:

* ``schema_version`` == ``us_official_session_calendar/1``
* ``market`` == ``US``, ``timezone`` == ``America/New_York``
* ``source`` is a JSON object (its contents are free-form to the runtime)
* ``coverage_start`` / ``coverage_end`` are ISO dates with start <= end
* ``sessions`` is a non-empty list whose every row has EXACTLY the two keys
  ``date`` and ``close_time_et``; every date lies inside the coverage window,
  is a weekday, is strictly increasing, and every close time is ``16:00`` or
  ``13:00``.

This module produces that file.  It adds no calendar logic of its own: every
date is classified by the ratified, already-reviewed
``market_data/us_official_session_calendar.py`` (rule
``US-SESSION-CALENDAR-SOURCE-V1-20260914``), and this module only

1. captures the two official pages through that module's ``capture_page``,
2. asks ``classify_date`` for every date of the coverage window,
3. refuses the whole file — never one date — if any date is ``UNKNOWN`` or was
   not decided on the ``OFFICIAL_NYSE_CAPTURE`` basis, and
4. serialises the open sessions into the runtime's exact row shape.

Only the official basis is admitted.  The ratified rule's second, historical
``ALPACA_CALENDAR_AND_IEX_SPY_BAR`` basis is deliberately NOT accepted here:
it needs broker credentials, it is a two-source reconstruction rather than an
exchange publication, and the runtime chain only ever walks sessions at or
after the accepted history's last session, which is inside the officially
published years.  Weekday inference is prohibited by
``config/us_session_calendar_source_v1.json`` (``weekday_inference_prohibited``)
and by ``config/regime_source_owner_registry_v2.json`` markets.US
official_calendar; a weekend or an unlisted weekday inside a published year is
read from that year's official closure table and early-close statement, which
the exchange publishes as the complete list for the year, and no date outside
the published years is classified at all.  ``load_source_config`` re-binds both
of those files by hash on every run, so this producer cannot run under a
weakened rule.

Determinism and the U5 binding: the file embeds retrieval instants, which
differ every run, so a naive rewrite would change the committed sha256 and
break the adoption binding the moment the workflow ran again.  ``write_calendar``
therefore rewrites only when ``derived_payload_sha256`` — the hash over the
coverage window, the published years, the session rows and the raw page hashes,
and over nothing time-varying — actually changed.  An unchanged official page
is a no-op commit.

Network access lives only in ``market_data/us_official_session_calendar.py``;
``build_runtime_calendar`` is a pure function over captured bytes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar as CAL  # noqa: E402


RUNTIME_SCHEMA = "us_official_session_calendar/1"
PRODUCER_PATH = "market_data/us_official_session_calendar_producer.py"
DEFAULT_CALENDAR_PATH = "data/us_official_session_calendar_v1.json"
EVIDENCE_DIR = "evidence/market_calendar/us_official_session_calendar"

CLOSE_REGULAR = "16:00"
CLOSE_EARLY = "13:00"
CLOSE_TIME_ET = {CAL.STATUS_REGULAR: CLOSE_REGULAR, CAL.STATUS_EARLY: CLOSE_EARLY}

WROTE = "WROTE"
UNCHANGED = "UNCHANGED"


class UsOfficialSessionCalendarProducerError(ValueError):
    """A producer invariant failed closed."""


def fail(code: str, detail: str = "") -> None:
    raise UsOfficialSessionCalendarProducerError(f"{code}:{detail}" if detail else code)


# ---------------------------------------------------------------------------
# Capture (delegated; the only network code is in the calendar module).
# ---------------------------------------------------------------------------


def capture_official_pages(*, opener=None, clock=None) -> dict:
    """Capture the NYSE page and the Nasdaq cross-check page."""
    return {
        CAL.NYSE_SOURCE_ID: CAL.capture_page(CAL.NYSE_SOURCE_ID, opener=opener, clock=clock),
        CAL.NASDAQ_SOURCE_ID: CAL.capture_page(CAL.NASDAQ_SOURCE_ID, opener=opener, clock=clock),
    }


def _source_row(capture: dict, raw: bytes, parsed: dict, role: str) -> dict:
    response = capture["response"]
    return {
        "role": role,
        "source_id": capture["source_id"],
        "request_url": capture["request"]["url"],
        "final_url": response["final_url"],
        "http_status": response["http_status"],
        "retrieved_at_utc": capture["response_received_at"],
        "capture_started_at_utc": capture["capture_started_at"],
        "raw_sha256": CAL.digest(raw),
        "raw_byte_count": len(raw),
        "published_years": list(parsed["published_years"]),
        "closure_count": len(parsed["closures"]),
        "early_close_count": len(parsed["early_closes"]),
    }


# ---------------------------------------------------------------------------
# Pure build over captured bytes.
# ---------------------------------------------------------------------------


def build_runtime_calendar(
    captures: dict,
    *,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    source_config_path: Path = CAL.SOURCE_CONFIG_PATH,
    root: Path = ROOT,
) -> dict:
    """Build ``us_official_session_calendar/1`` from the two official captures.

    Fails closed on: a bad capture, an ambiguous page (the calendar module's own
    parsers decide that), a coverage window that leaves the officially published
    years, any date the ratified rule cannot classify from the official basis,
    and a cross-check that attests nothing at all.
    """
    config = CAL.load_source_config(source_config_path, root=root)

    nyse_capture, nyse_raw = CAL.validate_capture(captures.get(CAL.NYSE_SOURCE_ID), CAL.NYSE_SOURCE_ID)
    nasdaq_capture, nasdaq_raw = CAL.validate_capture(captures.get(CAL.NASDAQ_SOURCE_ID), CAL.NASDAQ_SOURCE_ID)
    nyse = CAL.parse_nyse_page(nyse_raw)
    nasdaq = CAL.parse_nasdaq_page(nasdaq_raw)

    years = list(nyse["published_years"])
    if not years:
        fail("OFFICIAL_PAGE_PUBLISHES_NO_YEAR")
    start = CAL.parse_date(coverage_start or f"{min(years)}-01-01", "COVERAGE_START_INVALID")
    end = CAL.parse_date(coverage_end or f"{max(years)}-12-31", "COVERAGE_END_INVALID")
    if start > end:
        fail("COVERAGE_RANGE_INVALID", f"{start.isoformat()}..{end.isoformat()}")
    if start.year not in years or end.year not in years:
        # Outside a published year nothing official states the date's status, so
        # the whole file is refused rather than silently narrowed.
        fail("COVERAGE_OUTSIDE_PUBLISHED_YEARS", f"{start.isoformat()}..{end.isoformat()}")

    sources = {"nyse": nyse, "nasdaq": nasdaq}
    sessions: list[dict] = []
    cross_checked = 0
    early = 0
    closed = 0
    for day in CAL.daterange(start, end):
        row = CAL.classify_date(day, sources)
        if row["status"] == CAL.STATUS_UNKNOWN:
            fail("OFFICIAL_CALENDAR_DATE_UNKNOWN", f"{row['date']}:{row['reason']}")
        if row["basis"] != CAL.BASIS_OFFICIAL:
            fail("OFFICIAL_CALENDAR_BASIS_NOT_OFFICIAL", f"{row['date']}:{row['basis']}")
        if CAL.NYSE_SOURCE_ID not in row["attesting_sources"]:
            fail("OFFICIAL_CALENDAR_PRIMARY_SOURCE_MISSING", row["date"])
        if CAL.NASDAQ_SOURCE_ID in row["attesting_sources"]:
            cross_checked += 1
        if row["status"] == CAL.STATUS_CLOSED:
            closed += 1
            continue
        if day.weekday() >= 5:
            # Unreachable through the official basis, kept so a future rule
            # change cannot emit a row the runtime would reject.
            fail("OFFICIAL_CALENDAR_WEEKEND_SESSION", row["date"])
        if row["status"] == CAL.STATUS_EARLY:
            early += 1
        sessions.append({"date": row["date"], "close_time_et": CLOSE_TIME_ET[row["status"]]})
    if not sessions:
        fail("OFFICIAL_CALENDAR_HAS_NO_SESSION")
    if cross_checked == 0:
        # The Nasdaq page parsed but overlapped nothing in the window: the
        # cross-check silently did no work, which is not a cross-check.
        fail("OFFICIAL_CALENDAR_CROSS_CHECK_ATTESTED_NOTHING")

    ratification = config["ratification"]
    calendar = {
        "schema_version": RUNTIME_SCHEMA,
        "market": "US",
        "timezone": "America/New_York",
        "scope": "US_INTERNAL_VIRTUAL_PAPER_ONLY",
        "producer": PRODUCER_PATH,
        "basis": CAL.BASIS_OFFICIAL,
        "weekday_inference_prohibited": True,
        "conflict_or_missing_result": CAL.UNKNOWN_RESULT,
        "source_rule": {
            "ratification_id": ratification["ratification_id"],
            "ratification_path": ratification["path"],
            "ratification_sha256": ratification["sha256"],
            "source_config_path": str(Path(source_config_path).relative_to(Path(root))),
            "source_config_sha256": CAL.file_sha256(Path(source_config_path)),
            "registry_path": config["registry_binding"]["path"],
            "registry_sha256": config["registry_binding"]["sha256"],
        },
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        "published_years": years,
        "source": {
            "primary": _source_row(nyse_capture, nyse_raw, nyse, "OFFICIAL_PUBLICATION"),
            "cross_check": _source_row(nasdaq_capture, nasdaq_raw, nasdaq, "OFFICIAL_CROSS_CHECK"),
        },
        "counts": {
            "session_count": len(sessions),
            "regular_close_session_count": len(sessions) - early,
            "early_close_session_count": early,
            "closed_date_count": closed,
            "cross_checked_date_count": cross_checked,
        },
        "authority": dict(CAL.AUTHORITY),
        "sessions": sessions,
    }
    calendar["derived_payload_sha256"] = derived_payload_sha256(calendar)
    return calendar


def derived_payload_sha256(calendar: dict) -> str:
    """Hash over what the calendar *states*, excluding every retrieval instant.

    Two runs over an unchanged official page produce the same value, so the
    committed bytes — and therefore the sha256 an adoption binds — stay stable.
    """
    payload = {
        "schema_version": calendar["schema_version"],
        "market": calendar["market"],
        "timezone": calendar["timezone"],
        "basis": calendar["basis"],
        "coverage_start": calendar["coverage_start"],
        "coverage_end": calendar["coverage_end"],
        "published_years": calendar["published_years"],
        "sessions": calendar["sessions"],
        "source_raw_sha256": {
            calendar["source"][role]["source_id"]: calendar["source"][role]["raw_sha256"]
            for role in ("primary", "cross_check")
        },
        "source_rule_sha256": {
            "ratification": calendar["source_rule"]["ratification_sha256"],
            "source_config": calendar["source_rule"]["source_config_sha256"],
            "registry": calendar["source_rule"]["registry_sha256"],
        },
    }
    return CAL.payload_sha256(payload)


# ---------------------------------------------------------------------------
# Shape check (exactly the runtime's rules, re-stated independently).
# ---------------------------------------------------------------------------


def validate_runtime_calendar(calendar: object, *, as_of: dt.datetime | None = None) -> dict:
    """Re-check everything ``regime/us_paper_runtime.py::load_calendar`` checks.

    A near-miss here would only surface as another runtime UNKNOWN, so the rules
    are restated rather than imported: this producer must fail in CI, not the
    daily runtime.  ``as_of`` additionally requires a session strictly after the
    latest completed one, which is what ``session_plan`` needs to name an
    execution session and an expiry.
    """
    if not isinstance(calendar, dict):
        fail("CALENDAR_NOT_AN_OBJECT")
    if calendar.get("schema_version") != RUNTIME_SCHEMA:
        fail("CALENDAR_SCHEMA_INVALID", str(calendar.get("schema_version")))
    if calendar.get("market") != "US" or calendar.get("timezone") != "America/New_York":
        fail("CALENDAR_IDENTITY_INVALID")
    if not isinstance(calendar.get("source"), dict) or not calendar["source"]:
        fail("CALENDAR_SOURCE_INVALID")
    if calendar.get("weekday_inference_prohibited") is not True:
        fail("CALENDAR_WEEKDAY_INFERENCE_OPEN")
    if calendar.get("basis") != CAL.BASIS_OFFICIAL:
        fail("CALENDAR_BASIS_NOT_OFFICIAL", str(calendar.get("basis")))
    if calendar.get("authority") != CAL.AUTHORITY:
        fail("CALENDAR_AUTHORITY_OPEN")
    start = CAL.parse_date(calendar.get("coverage_start"), "CALENDAR_COVERAGE_INVALID")
    end = CAL.parse_date(calendar.get("coverage_end"), "CALENDAR_COVERAGE_INVALID")
    if start > end:
        fail("CALENDAR_COVERAGE_INVALID", f"{start.isoformat()}..{end.isoformat()}")
    rows = calendar.get("sessions")
    if not isinstance(rows, list) or not rows:
        fail("CALENDAR_SESSIONS_EMPTY")
    previous = None
    closes = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"date", "close_time_et"}:
            fail("CALENDAR_ROW_FIELDS_INVALID", CAL.canonical_json(row))
        day = CAL.parse_date(row["date"], "CALENDAR_ROW_DATE_INVALID")
        if not start <= day <= end:
            fail("CALENDAR_ROW_OUTSIDE_COVERAGE", row["date"])
        if day.weekday() >= 5:
            fail("CALENDAR_ROW_ON_WEEKEND", row["date"])
        if previous is not None and day <= previous:
            fail("CALENDAR_ROW_ORDER_OR_DUPLICATE", row["date"])
        if row["close_time_et"] not in (CLOSE_REGULAR, CLOSE_EARLY):
            fail("CALENDAR_ROW_CLOSE_TIME_INVALID", row["close_time_et"])
        closes.append((day, row["close_time_et"]))
        previous = day
    if calendar.get("derived_payload_sha256") != derived_payload_sha256(calendar):
        fail("CALENDAR_DERIVED_PAYLOAD_SHA256_MISMATCH")
    counts = calendar.get("counts") or {}
    if counts.get("session_count") != len(rows):
        fail("CALENDAR_SESSION_COUNT_MISMATCH")
    if counts.get("cross_checked_date_count", 0) < 1:
        fail("CALENDAR_CROSS_CHECK_ATTESTED_NOTHING")
    if as_of is not None:
        completed = [
            day for day, close in closes
            if dt.datetime.combine(day, dt.time(16, 0) if close == CLOSE_REGULAR else dt.time(13, 0),
                                   tzinfo=CAL.NY) <= as_of
        ]
        if not completed:
            fail("CALENDAR_NO_COMPLETED_SESSION_AT", CAL.utc_z(as_of))
        if completed[-1] >= closes[-1][0]:
            # The runtime needs one session strictly after the context session
            # to name an execution session and an expiry.
            fail("CALENDAR_NO_FORWARD_SESSION_AT", CAL.utc_z(as_of))
    return {
        "session_count": len(rows),
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        "first_session_date": rows[0]["date"],
        "last_session_date": rows[-1]["date"],
        "derived_payload_sha256": calendar["derived_payload_sha256"],
    }


# ---------------------------------------------------------------------------
# Committed output.
# ---------------------------------------------------------------------------


def calendar_bytes(calendar: dict) -> bytes:
    return CAL.canonical_bytes(calendar)


def write_calendar(calendar: dict, path: Path, *, evidence_dir: Path | None = None) -> dict:
    """Write the calendar only when its derived payload actually changed.

    Rewriting on an unchanged page would move the file's sha256 every run and
    silently invalidate the adoption binding that U5 pins, so an unchanged
    official publication is a no-op.
    """
    validate_runtime_calendar(calendar)
    path = Path(path)
    new_bytes = calendar_bytes(calendar)
    result = {
        "path": str(path),
        "derived_payload_sha256": calendar["derived_payload_sha256"],
        "action": WROTE,
        "file_sha256": None,
        "evidence_path": None,
    }
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict) and existing.get("derived_payload_sha256") == calendar["derived_payload_sha256"]:
            result["action"] = UNCHANGED
            result["file_sha256"] = CAL.file_sha256(path)
            return result
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(new_bytes)
    result["file_sha256"] = CAL.digest(new_bytes)
    if evidence_dir is not None:
        evidence = Path(evidence_dir) / f"{calendar['derived_payload_sha256']}.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        if not evidence.is_file():
            evidence.write_bytes(new_bytes)
        result["evidence_path"] = str(evidence)
    return result


def load_calendar_file(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("CALENDAR_FILE_UNREADABLE", str(path))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _produce(args) -> int:
    captures = capture_official_pages()
    calendar = build_runtime_calendar(
        captures, coverage_start=args.coverage_start, coverage_end=args.coverage_end,
    )
    result = write_calendar(
        calendar,
        ROOT / args.out,
        evidence_dir=None if args.no_evidence else ROOT / EVIDENCE_DIR,
    )
    summary = validate_runtime_calendar(load_calendar_file(ROOT / args.out))
    print(json.dumps({**result, **summary}, indent=2, sort_keys=True))
    return 0


def _build(args) -> int:
    captures = {
        CAL.NYSE_SOURCE_ID: json.loads(Path(args.nyse_capture).read_text(encoding="utf-8")),
        CAL.NASDAQ_SOURCE_ID: json.loads(Path(args.nasdaq_capture).read_text(encoding="utf-8")),
    }
    calendar = build_runtime_calendar(
        captures, coverage_start=args.coverage_start, coverage_end=args.coverage_end,
    )
    if args.out:
        result = write_calendar(calendar, Path(args.out))
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        sys.stdout.write(calendar_bytes(calendar).decode("utf-8"))
    return 0


def _verify(args) -> int:
    calendar = load_calendar_file(Path(args.calendar))
    as_of = None if not args.as_of else CAL.parse_instant(args.as_of, "AS_OF_INVALID")
    summary = validate_runtime_calendar(calendar, as_of=as_of)
    summary["file_sha256"] = CAL.file_sha256(Path(args.calendar))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    produce = sub.add_parser("produce", help="capture the official pages and write the calendar")
    produce.add_argument("--out", default=DEFAULT_CALENDAR_PATH)
    produce.add_argument("--coverage-start", default=None)
    produce.add_argument("--coverage-end", default=None)
    produce.add_argument("--no-evidence", action="store_true")
    produce.set_defaults(func=_produce)

    build = sub.add_parser("build", help="build the calendar from two capture files (offline)")
    build.add_argument("--nyse-capture", required=True)
    build.add_argument("--nasdaq-capture", required=True)
    build.add_argument("--coverage-start", default=None)
    build.add_argument("--coverage-end", default=None)
    build.add_argument("--out", default=None)
    build.set_defaults(func=_build)

    verify = sub.add_parser("verify", help="re-check a committed calendar against the runtime's rules")
    verify.add_argument("--calendar", default=DEFAULT_CALENDAR_PATH)
    verify.add_argument("--as-of", default=None, help="UTC instant (…Z) that must have a completed and a forward session")
    verify.set_defaults(func=_verify)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (UsOfficialSessionCalendarProducerError, CAL.UsSessionCalendarError) as exc:
        print(f"STOP {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
