#!/usr/bin/env python3
"""Macro event calendar -- evidence capture only, no trading judgement.

CIO decision 2026-09-16: record scheduled macro events (US FOMC decision
dates, US CPI releases, US nonfarm payrolls, and Bank of Korea rate
decisions) so that (a) briefings and decision records can later state
"this day was an FOMC day" and (b) a future, separately RATIFIED rule can
pause new buys before a scheduled release. This module derives event dates
from each source's own OFFICIAL calendar page -- it never predicts a
release's direction or magnitude, never flags a "risk day", and is never
imported by any briefing, decision, rule, or execution path in this PR.
Every ``authority`` field below is False, as in every other evidence
collector in this repo.

Sources (official, free, fetched and PARSED -- never a hand-written date
table)
--------------------------------------------------------------------------
  FOMC  Federal Reserve Board's own FOMC meeting calendar
        https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
        One HTML page with one "panel" per year (``<h4><a id="...">YYYY
        FOMC Meetings</a></h4>``), each panel listing every meeting as a
        month + a date-range div (``fomc-meeting__month`` /
        ``fomc-meeting__date``). The decision date is the *last* day of a
        two-day meeting (or the single day of a one-day/notation-vote
        entry) -- see ``parse_fomc_month_date``. When the Fed has already
        posted that meeting's statement (a
        ``/newsevents/pressreleases/monetary<YYYYMMDD>a.htm`` link appears
        in that meeting's own block), status is ``released`` and the link's
        own embedded date is cross-checked against the parsed decision
        date (``FOMC_STATEMENT_DATE_MISMATCH`` if they disagree) -- this is
        a SOURCE-STATED fact, not a clock guess. Otherwise status is
        ``scheduled``. This page does not state a release time/timezone
        anywhere (confirmed by scanning the live page 2026-09-18 for
        "2:00 p.m." / "Eastern" -- neither appears near the calendar), so
        ``scheduled_time``/``timezone`` are recorded as ``null`` rather than
        the commonly-known-but-unstated "2:00 p.m. ET" convention -- this
        module never fills in a fact the source itself did not state.

  CPI   BLS "Schedule of Releases for the Consumer Price Index"
        https://www.bls.gov/schedule/news_release/cpi.htm
  NFP   BLS "Schedule of Releases for the Employment Situation" (the
        report containing the nonfarm payrolls figure)
        https://www.bls.gov/schedule/news_release/empsit.htm
        Both are the same ``<table class="release-list">`` shape: one row
        per (Reference Month, Release Date, Release Time), e.g. ("November
        2025", "Dec. 18, 2025", "08:30 AM"). Confirmed against the live
        pages 2026-09-18 (fetched via a third-party reader because BLS's
        own Akamai bot manager blocks this development sandbox's outbound
        IP wholesale -- even ``bls.gov/robots.txt`` and the bare homepage
        return HTTP 403 from here; the *parser* below was built against the
        real HTML those pages actually served that day). This collector's
        own network call always goes straight to the official bls.gov URL,
        never through any intermediary -- if a GitHub Actions runner's IP
        is *also* bot-blocked, ``fetch_calendar`` fails closed with
        ``{SOURCE}_HTTP_ERROR_403`` rather than silently falling back to a
        non-official mirror. Exactly like ``spdr_sector_holdings.py``'s
        URL template and ``fred_dexkous_fx.py``'s ``cosd`` parameter, real
        reachability from the actual runner is UNVERIFIED until this
        workflow's first live ``workflow_dispatch`` run.
        Independent review 2026-09-18 (pre-merge, PR #785): each ``<td>``
        cell is read tolerantly of nested markup (``_cell_text``, shared
        with the BOK parser below) rather than assuming a bare text node --
        BLS is known to sometimes bold or link-wrap the next-upcoming
        release's row, which would otherwise make that one row's regex
        silently fail to match. ``parse_bls_release_table`` also cross-
        checks the number of parsed rows against a raw ``<tr>`` count
        inside the table's ``<tbody>`` and fails closed
        (``BLS_ROW_COUNT_MISMATCH``) on any mismatch, so a row that still
        cannot be parsed for some other reason is a loud failure, never a
        quietly incomplete calendar.
        Neither BLS table states a timezone (BLS releases are Eastern Time
        by well-known convention, but that word never appears on either
        page) -- ``timezone`` is recorded as ``null`` for the same
        never-fill-in-an-unstated-fact reason as FOMC above. Neither table
        marks which rows are already-released vs still-scheduled either,
        so status here is a coarse CIO clock inference (``scheduled_date``
        <= this capture's own UTC date => ``released``), explicitly tagged
        ``status_basis: CIO_CLOCK_INFERENCE_NOT_SOURCE_STATED`` -- never
        conflated with FOMC's source-stated status above.

  BOK   Bank of Korea "Meeting Dates" (Monetary Policy Board schedule)
        https://www.bok.or.kr/eng/main/contents.do?menuNo=400020
        One ``<h3>YYYY</h3>`` per year followed by a table of ``<th>``
        month labels (fixed 8-meeting cadence: Jan/Feb/Apr/May/Jul/Aug/
        Oct/Nov -- confirmed against the live page 2026-09-18, years
        2023-2026) with a same-column ``<td>`` cell like "Jan.15 (Thu)".
        Like the BLS tables, this page carries no release-vs-scheduled
        marker and no time/timezone, so status is the same coarse
        ``CIO_CLOCK_INFERENCE_NOT_SOURCE_STATED`` comparison and
        ``scheduled_time``/``timezone`` stay ``null``.

Bounded capture, not full-history (same rationale as
fred_dexkous_fx.py's RECENT_WINDOW_DAYS)
--------------------------------------------------------------------------
The FOMC and BOK pages each carry a decade-plus of past panels/years. A
normal run only PARSES panels/years within
``[captured_year - YEARS_BACK, captured_year + YEARS_FORWARD]``
(defaults 1/1) -- deliberately small, since this collector exists to
answer "is an event coming up soon", not to backfill history. ``--all-
years`` parses every panel/year the page has, for a one-time deliberate
wide capture; it is never used by the normal (non-``--all-years``)
CLI path.

Append-only, content-addressed, no rewrite-every-run
--------------------------------------------------------------------------
Every derived event observation is written under
``evidence/macro_event_calendar/observations/<event_type>/<market>/
<scheduled_date>/<state_hash>.json``, where ``state_hash`` is a hash of
only the fields that can actually change over time (status,
scheduled_time, timezone, detail) -- see ``build_events``. Re-observing an
unchanged event on a later run resolves to the SAME path (a harmless
no-op, exactly like ``fred_dexkous_fx.py``'s ``_write_observation_once``);
only a genuine change (typically ``scheduled`` -> ``released``) produces a
new file. This is what keeps a daily schedule from rewriting ~100
unchanged events every single day. The raw page fetched from each source
is archived once per source per day the same way ``fred_dexkous_fx.py``
archives FRED's raw response (gzip, content-addressed, never overwritten).

No trading authority
--------------------------------------------------------------------------
Every record's ``authority`` block has every flag False. This module is
not imported by any briefing, decision, rule, or execution path in this
PR -- it only produces evidence. A later, separately ratified rule may
read this evidence to pause new buys before a scheduled release; this PR
does not implement or wire that rule.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

EVIDENCE_ROOT = "evidence/macro_event_calendar"
RAW_RETENTION = "APPEND_ONLY_CONTENT_ADDRESSED"

BATCH_SCHEMA_VERSION = "macro_event_calendar_raw_batch/1"
OBSERVATION_SCHEMA_VERSION = "macro_event_calendar_observation/1"

YEARS_BACK_DEFAULT = 1
YEARS_FORWARD_DEFAULT = 1

STATUS_SCHEDULED = "scheduled"
STATUS_RELEASED = "released"

BASIS_SOURCE_STATED = "SOURCE_STATED_STATEMENT_LINK"
BASIS_CLOCK_INFERENCE = "CIO_CLOCK_INFERENCE_NOT_SOURCE_STATED"

SOURCES = {
    "FOMC": {
        "event_type": "FOMC_DECISION",
        "market": "US",
        "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    },
    "CPI": {
        "event_type": "CPI_RELEASE",
        "market": "US",
        "url": "https://www.bls.gov/schedule/news_release/cpi.htm",
    },
    "NFP": {
        "event_type": "NONFARM_PAYROLLS",
        "market": "US",
        "url": "https://www.bls.gov/schedule/news_release/empsit.htm",
    },
    "BOK": {
        "event_type": "BOK_RATE_DECISION",
        "market": "KR",
        "url": "https://www.bok.or.kr/eng/main/contents.do?menuNo=400020",
    },
}

AUTHORITY = {
    "evidence_capture_only": True,
    "event_prediction_authorized": False,
    "direction_authorized": False,
    "risk_day_classification_authorized": False,
    "buy_pause_authorized": False,
    "candidate_validity_authorized": False,
    "action_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

MONTH_NUMBERS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


class MacroEventCalendarError(ValueError):
    """The capture cannot prove the declared macro event(s)."""


def fail(code: str):
    raise MacroEventCalendarError(code)


def _month_number(text: str) -> int:
    key = text.strip().rstrip(".").lower()
    if key not in MONTH_NUMBERS:
        fail("MONTH_NAME_UNRECOGNIZED")
    return MONTH_NUMBERS[key]


# ─────────────────────────────────────────────────────────────────────────
# Small self-contained primitives -- deliberately duplicated rather than
# imported from collectors/fred_dexkous_fx.py (CIO copy-preserve
# convention per run_all.py: each evidence module stays independently
# replayable without a cross-module edit surface).
# ─────────────────────────────────────────────────────────────────────────

def canonical_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def deterministic_gzip(value: bytes) -> bytes:
    """Gzip container whose header is stable across Python/OS (OS=255)."""
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0
    ) as stream:
        stream.write(value)
    return output.getvalue()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        fail(code)


_INNER_TAG = re.compile(r"<[^>]+>")


def _cell_text(raw_cell: str) -> str:
    """Text content of one table cell, tolerant of nested markup (e.g. a
    source bolding/link-wrapping one particular row) -- strip every inner
    tag, unescape entities, then collapse whitespace. Used by both the BLS
    and BOK table parsers below so neither silently drops a cell whose
    <td> is not a single bare text node."""
    text = _INNER_TAG.sub(" ", raw_cell)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ─────────────────────────────────────────────────────────────────────────
# FOMC -- https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# ─────────────────────────────────────────────────────────────────────────

_FOMC_PANEL = re.compile(r'<h4><a id="\d+">(\d{4}) FOMC Meetings</a></h4>')
_FOMC_MONTH = re.compile(r'fomc-meeting__month[^>]*><strong>([^<]+)</strong>')
_FOMC_DATE = re.compile(r'fomc-meeting__date[^>]*>([^<]+)</div>')
_FOMC_STATEMENT_LINK = re.compile(
    r'href="/newsevents/pressreleases/monetary(\d{8})a\.htm"'
)


def parse_fomc_month_date(year: int, month_field: str, date_field: str) -> dict:
    """Decision date (last day of the meeting) from the panel year plus the
    page's own month/date-range text. Handles the shapes actually observed
    on the live page: "27-28", "17-18*" (SEP flag), "22 (notation vote)",
    and a month-crossing range like "Apr/May" "30-1"."""
    sep_meeting = date_field.endswith("*")
    cleaned = date_field[:-1] if sep_meeting else date_field
    notation_vote = False

    m_notation = re.fullmatch(r"(\d{1,2})\s*\(notation vote\)", cleaned)
    m_range = re.fullmatch(r"(\d{1,2})-(\d{1,2})", cleaned)
    m_single = re.fullmatch(r"(\d{1,2})", cleaned)
    if m_notation:
        notation_vote = True
        day1 = day2 = int(m_notation.group(1))
    elif m_range:
        day1, day2 = int(m_range.group(1)), int(m_range.group(2))
    elif m_single:
        day1 = day2 = int(m_single.group(1))
    else:
        fail("FOMC_DATE_FORMAT_UNRECOGNIZED")

    month_parts = month_field.split("/")
    if len(month_parts) == 1:
        mon1 = mon2 = _month_number(month_parts[0])
    elif len(month_parts) == 2:
        mon1 = _month_number(month_parts[0])
        mon2 = _month_number(month_parts[1])
    else:
        fail("FOMC_MONTH_FORMAT_UNRECOGNIZED")

    year1 = year
    year2 = year if mon2 >= mon1 else year + 1
    try:
        meeting_start = dt.date(year1, mon1, day1)
        decision = dt.date(year2, mon2, day2)
    except ValueError:
        fail("FOMC_DATE_INVALID_CALENDAR_DAY")
    if decision < meeting_start:
        fail("FOMC_DECISION_BEFORE_MEETING_START")

    return {
        "decision_date": decision.isoformat(),
        "meeting_start_date": meeting_start.isoformat(),
        "summary_of_economic_projections": sep_meeting,
        "notation_vote": notation_vote,
    }


def parse_fomc_calendar(raw: bytes, *, year_min: int, year_max: int) -> list[dict]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("FOMC_RAW_DECODE_INVALID")

    panels = list(_FOMC_PANEL.finditer(text))
    if not panels:
        fail("FOMC_NO_PANELS_FOUND")

    months = list(_FOMC_MONTH.finditer(text))
    dates = list(_FOMC_DATE.finditer(text))
    if len(months) != len(dates) or not months:
        fail("FOMC_MONTH_DATE_COUNT_MISMATCH")

    events = []
    for i, (month_match, date_match) in enumerate(zip(months, dates)):
        if month_match.start() > date_match.start():
            fail("FOMC_MONTH_DATE_ORDER_INVALID")

        panel = None
        for candidate in panels:
            if candidate.start() < month_match.start():
                panel = candidate
            else:
                break
        if panel is None:
            fail("FOMC_MEETING_OUTSIDE_PANEL")
        year = int(panel.group(1))
        if year < year_min or year > year_max:
            continue

        body_start = date_match.end()
        body_end = months[i + 1].start() if i + 1 < len(months) else len(text)
        body = text[body_start:body_end]
        statement_match = _FOMC_STATEMENT_LINK.search(body)

        parsed = parse_fomc_month_date(
            year, month_match.group(1).strip(), date_match.group(1).strip()
        )
        if statement_match is not None:
            expected = parsed["decision_date"].replace("-", "")
            if statement_match.group(1) != expected:
                fail("FOMC_STATEMENT_DATE_MISMATCH")
            status, basis = STATUS_RELEASED, BASIS_SOURCE_STATED
        else:
            status, basis = STATUS_SCHEDULED, BASIS_SOURCE_STATED

        events.append({
            "event_type": "FOMC_DECISION",
            "market": "US",
            "scheduled_date": parsed["decision_date"],
            "scheduled_time": None,
            "timezone": None,
            "status": status,
            "status_basis": basis,
            "detail": {
                "meeting_start_date": parsed["meeting_start_date"],
                "summary_of_economic_projections": parsed["summary_of_economic_projections"],
                "notation_vote": parsed["notation_vote"],
            },
        })
    return events


# ─────────────────────────────────────────────────────────────────────────
# BLS release-schedule tables (CPI, Employment Situation / nonfarm payrolls)
# ─────────────────────────────────────────────────────────────────────────

_BLS_TABLE = re.compile(r'<table class="release-list">.*?</table>', re.S)
_BLS_THEAD = re.compile(r'<thead>.*?</thead>', re.S)
_BLS_TH = re.compile(r'<th>([^<]*)</th>')
_BLS_TBODY = re.compile(r'<tbody>(.*?)</tbody>', re.S)
_BLS_TR = re.compile(r'<tr\b')
# ``(.*?)`` (not ``[^<]*``) tolerates a source wrapping one cell's content
# in nested markup (e.g. bolding/linking the next-upcoming release row) --
# see _cell_text, which strips whatever tags land inside each captured
# group. A row this pattern still cannot match at all (e.g. a missing
# <td>) is caught by the row-count cross-check in parse_bls_release_table,
# not silently dropped.
_BLS_ROW = re.compile(
    r'<tr[^>]*>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>', re.S
)
_BLS_DATE = re.compile(r'^([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})$')
_BLS_TIME = re.compile(r'^(\d{1,2}):(\d{2})\s*(AM|PM)$', re.IGNORECASE)
_BLS_REFERENCE_MONTH = re.compile(r'^([A-Za-z]+)\s+(\d{4})$')

_BLS_HEADING_BY_SOURCE = {
    "CPI": "Consumer Price Index",
    "NFP": "Employment Situation",
}


def _parse_bls_reference_month(text: str, code: str) -> str:
    m = _BLS_REFERENCE_MONTH.fullmatch(text)
    if not m:
        fail(code)
    month = _month_number(m.group(1))
    return f"{int(m.group(2)):04d}-{month:02d}"


def _parse_bls_date(text: str, code: str) -> str:
    m = _BLS_DATE.fullmatch(text)
    if not m:
        fail(code)
    month = _month_number(m.group(1))
    try:
        return dt.date(int(m.group(3)), month, int(m.group(2))).isoformat()
    except ValueError:
        fail(code)


def _parse_bls_time(text: str, code: str) -> str:
    m = _BLS_TIME.fullmatch(text)
    if not m:
        fail(code)
    hour, minute, meridiem = int(m.group(1)), int(m.group(2)), m.group(3).upper()
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        fail(code)
    if meridiem == "AM":
        hour24 = 0 if hour == 12 else hour
    else:
        hour24 = 12 if hour == 12 else hour + 12
    return f"{hour24:02d}:{minute:02d}"


def parse_bls_release_table(raw: bytes, source_key: str) -> list[dict]:
    if source_key not in _BLS_HEADING_BY_SOURCE:
        fail("BLS_SOURCE_KEY_INVALID")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("BLS_RAW_DECODE_INVALID")
    if _BLS_HEADING_BY_SOURCE[source_key] not in text:
        fail("BLS_PAGE_UNEXPECTED")

    table_match = _BLS_TABLE.search(text)
    if not table_match:
        fail("BLS_RELEASE_TABLE_NOT_FOUND")
    table = table_match.group(0)

    thead_match = _BLS_THEAD.search(table)
    headers = [h.strip() for h in _BLS_TH.findall(thead_match.group(0))] if thead_match else []
    if headers != ["Reference Month", "Release Date", "Release Time"]:
        fail("BLS_TABLE_HEADER_UNEXPECTED")

    tbody_match = _BLS_TBODY.search(table)
    if not tbody_match:
        fail("BLS_TBODY_NOT_FOUND")
    tbody = tbody_match.group(1)

    rows = _BLS_ROW.findall(tbody)
    if not rows:
        fail("BLS_TABLE_NO_ROWS")

    # Row-count cross-check: an incomplete parse must be loud, not silent.
    # If any <tr> inside tbody failed to match the 3-<td> row pattern above
    # (e.g. a malformed row, a missing <td>), findall() would otherwise
    # just return fewer rows and the run would report success with a
    # quietly incomplete calendar.
    tr_count = len(_BLS_TR.findall(tbody))
    if len(rows) != tr_count:
        fail("BLS_ROW_COUNT_MISMATCH")

    event_type = SOURCES[source_key]["event_type"]
    events = []
    for reference_text, date_text, time_text in rows:
        reference_period = _parse_bls_reference_month(
            _cell_text(reference_text), "BLS_REFERENCE_MONTH_INVALID"
        )
        scheduled_date = _parse_bls_date(_cell_text(date_text), "BLS_RELEASE_DATE_INVALID")
        scheduled_time = _parse_bls_time(_cell_text(time_text), "BLS_RELEASE_TIME_INVALID")
        events.append({
            "event_type": event_type,
            "market": "US",
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "timezone": None,
            "detail": {"reference_period": reference_period},
        })
    return events


# ─────────────────────────────────────────────────────────────────────────
# Bank of Korea -- Meeting Dates (Monetary Policy Board schedule)
# ─────────────────────────────────────────────────────────────────────────

_BOK_YEAR = re.compile(r'<h3>(\d{4})</h3>')
_BOK_TABLE = re.compile(r'<table>.*?</table>', re.S)
_BOK_TH = re.compile(r'<th[^>]*><strong[^>]*>([^<]+)</strong></th>')
_BOK_TD = re.compile(r'<td>(.*?)</td>', re.S)
_BOK_CELL = re.compile(r'^([A-Za-z]+)\.?\s*(\d{1,2})\s*\(([A-Za-z]+)\)$')


def parse_bok_calendar(raw: bytes, *, year_min: int, year_max: int) -> list[dict]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("BOK_RAW_DECODE_INVALID")
    if "Meeting Dates" not in text:
        fail("BOK_PAGE_UNEXPECTED")

    years = list(_BOK_YEAR.finditer(text))
    if not years:
        fail("BOK_NO_YEAR_HEADERS_FOUND")

    events = []
    for i, year_match in enumerate(years):
        year = int(year_match.group(1))
        block_start = year_match.end()
        block_end = years[i + 1].start() if i + 1 < len(years) else len(text)
        block = text[block_start:block_end]
        if year < year_min or year > year_max:
            continue

        table_match = _BOK_TABLE.search(block)
        if not table_match:
            fail("BOK_YEAR_TABLE_MISSING")
        table = table_match.group(0)

        months = [_month_number(m) for m in _BOK_TH.findall(table)]
        cells = [_cell_text(c) for c in _BOK_TD.findall(table)]
        if not months or len(cells) != len(months):
            fail("BOK_TABLE_SHAPE_INVALID")

        for month, cell_text in zip(months, cells):
            if not cell_text:
                continue  # no meeting published yet for this slot
            m = _BOK_CELL.fullmatch(cell_text)
            if not m:
                fail("BOK_CELL_FORMAT_UNRECOGNIZED")
            cell_month = _month_number(m.group(1))
            if cell_month != month:
                fail("BOK_CELL_MONTH_MISMATCH")
            try:
                scheduled_date = dt.date(year, month, int(m.group(2))).isoformat()
            except ValueError:
                fail("BOK_DATE_INVALID_CALENDAR_DAY")
            events.append({
                "event_type": "BOK_RATE_DECISION",
                "market": "KR",
                "scheduled_date": scheduled_date,
                "scheduled_time": None,
                "timezone": None,
                "detail": {"weekday_label": m.group(3)},
            })
    return events


# ─────────────────────────────────────────────────────────────────────────
# Fetch -- injectable getter so tests never touch the network.
# ─────────────────────────────────────────────────────────────────────────

def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Atlas-Macro-Calendar/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        fail(f"HTTP_ERROR_{exc.code}")
    except urllib.error.URLError:
        fail("HTTP_UNREACHABLE")


def fetch_calendar(source_key: str, *, getter=_get) -> bytes:
    if source_key not in SOURCES:
        fail("SOURCE_KEY_INVALID")
    return getter(SOURCES[source_key]["url"])


# ─────────────────────────────────────────────────────────────────────────
# Derive -- parse one source's raw bytes into event dicts (no dates/times
# stamped on yet; that happens in build_events, which also assigns
# CIO-clock-inference status for the sources that need it).
# ─────────────────────────────────────────────────────────────────────────

def parse_source(source_key: str, raw: bytes, *, year_min: int, year_max: int) -> list[dict]:
    if source_key == "FOMC":
        return parse_fomc_calendar(raw, year_min=year_min, year_max=year_max)
    if source_key in ("CPI", "NFP"):
        return parse_bls_release_table(raw, source_key)
    if source_key == "BOK":
        return parse_bok_calendar(raw, year_min=year_min, year_max=year_max)
    fail("SOURCE_KEY_INVALID")


def _apply_clock_inference_status(events: list[dict], captured_at: dt.datetime) -> None:
    """CPI/NFP/BOK carry no released-vs-scheduled marker on their own
    pages, so status there is a coarse UTC-date comparison against this
    capture's own wall clock -- explicitly tagged so it is never confused
    with FOMC's source-stated status (see module docstring)."""
    today = captured_at.astimezone(UTC).date()
    for event in events:
        if "status" in event:
            continue  # FOMC already assigned a source-stated status
        scheduled = dt.date.fromisoformat(event["scheduled_date"])
        event["status"] = STATUS_RELEASED if scheduled <= today else STATUS_SCHEDULED
        event["status_basis"] = BASIS_CLOCK_INFERENCE


def _state_hash(event: dict) -> str:
    comparable = {
        "status": event["status"],
        "scheduled_time": event["scheduled_time"],
        "timezone": event["timezone"],
        "detail": event["detail"],
    }
    return sha256_bytes(canonical_bytes(comparable))[:16]


def build_events(
    captured_at: dt.datetime, source_key: str, raw: bytes, *, year_min: int, year_max: int
) -> dict:
    """Pure/deterministic: parse ``raw`` and stamp capture metadata. Returns
    the raw-archive bundle plus every derived observation record for this
    source, none of it written to disk yet (see ``publish_batch``)."""
    if captured_at.tzinfo is None:
        fail("CAPTURE_TIME_NAIVE")
    if source_key not in SOURCES:
        fail("SOURCE_KEY_INVALID")
    captured_at_utc = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _parse_utc(captured_at_utc, "CAPTURE_TIME_INVALID")

    events = parse_source(source_key, raw, year_min=year_min, year_max=year_max)
    _apply_clock_inference_status(events, captured_at)

    raw_sha256 = sha256_bytes(raw)
    day = captured_at.astimezone(UTC).date().isoformat()
    manifest_basis = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "source_key": source_key,
        "source_url": SOURCES[source_key]["url"],
        "captured_at_utc": captured_at_utc,
        "raw_sha256": raw_sha256,
        "event_count": len(events),
        "year_min": year_min,
        "year_max": year_max,
    }
    revision_id = sha256_bytes(canonical_bytes(manifest_basis))
    base = f"{EVIDENCE_ROOT}/raw/{source_key}/{day}/{revision_id}"
    manifest = {**manifest_basis, "revision_id": revision_id,
                "raw_retention": RAW_RETENTION, "authority": AUTHORITY}
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    raw_gzip_bytes = deterministic_gzip(raw)
    raw_pointer = {
        "revision_id": revision_id,
        "manifest_path": f"{base}/manifest.json",
        "manifest_file_sha256": sha256_bytes(manifest_bytes),
        "raw_path": f"{base}/raw.html.gz",
        "raw_file_sha256": sha256_bytes(raw_gzip_bytes),
        "raw_response_sha256": raw_sha256,
    }

    observation_records = []
    for event in events:
        record = {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "event_type": event["event_type"],
            "market": event["market"],
            "scheduled_date": event["scheduled_date"],
            "scheduled_time": event["scheduled_time"],
            "timezone": event["timezone"],
            "status": event["status"],
            "status_basis": event["status_basis"],
            "detail": event["detail"],
            "source_key": source_key,
            "source_url": SOURCES[source_key]["url"],
            "source_sha256": raw_sha256,
            "retrieved_at_utc": captured_at_utc,
            "batch_revision_id": revision_id,
            "authority": AUTHORITY,
        }
        state_hash = _state_hash(event)
        record_bytes = json.dumps(
            record, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8") + b"\n"
        record_path = (
            f"{EVIDENCE_ROOT}/observations/{event['event_type']}/{event['market']}/"
            f"{event['scheduled_date']}/{state_hash}.json"
        )
        observation_records.append({
            "record": record,
            "record_bytes": record_bytes,
            "record_path": record_path,
            "record_sha256": sha256_bytes(record_bytes),
        })

    return {
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "raw_gzip_bytes": raw_gzip_bytes,
        "raw_pointer": raw_pointer,
        "observation_records": observation_records,
    }


# ─────────────────────────────────────────────────────────────────────────
# Publish -- write-once, append-only, path is the content address.
# ─────────────────────────────────────────────────────────────────────────

def _write_once(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            fail("APPEND_ONLY_COLLISION")
        return False
    path.write_bytes(data)
    return True


_OBSERVATION_IDENTITY_FIELDS = (
    "event_type", "market", "scheduled_date", "status", "scheduled_time",
    "timezone", "detail", "source_key",
)


def _write_observation_once(path: Path, data: bytes) -> bool:
    """Write an observation record only if this exact (event_type, market,
    scheduled_date, state_hash) path has never been written before.

    The path already encodes a hash of every field that can legitimately
    change (see ``_state_hash``), so any existing file at this exact path
    must agree on all of ``_OBSERVATION_IDENTITY_FIELDS`` -- only
    ``retrieved_at_utc``/``batch_revision_id``/``source_sha256`` are
    allowed to differ (a benign re-observation of the same state, e.g. two
    daily runs in a row that saw no change). Any other mismatch is treated
    as tampering, exactly like ``fred_dexkous_fx.py``'s
    ``_write_observation_once``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file():
            fail("APPEND_ONLY_COLLISION")
        try:
            existing = json.loads(path.read_bytes())
            incoming = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            fail("APPEND_ONLY_COLLISION")
        if any(existing.get(f) != incoming.get(f) for f in _OBSERVATION_IDENTITY_FIELDS):
            fail("APPEND_ONLY_COLLISION")
        return False
    path.write_bytes(data)
    return True


def _safe_evidence_path(root: Path, value: object, prefix: str) -> Path:
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        fail("EVIDENCE_PATH_INVALID")
    if not value.startswith(prefix):
        fail("EVIDENCE_PATH_INVALID")
    resolved_root = root.resolve()
    resolved = (root / value).resolve()
    if resolved_root not in resolved.parents:
        fail("EVIDENCE_PATH_INVALID")
    return resolved


def publish_batch(root: Path, batch: dict) -> dict:
    manifest_path = _safe_evidence_path(
        root, batch["raw_pointer"]["manifest_path"], f"{EVIDENCE_ROOT}/raw/"
    )
    raw_path = _safe_evidence_path(
        root, batch["raw_pointer"]["raw_path"], f"{EVIDENCE_ROOT}/raw/"
    )
    _write_once(raw_path, batch["raw_gzip_bytes"])
    _write_once(manifest_path, batch["manifest_bytes"])

    new_paths = []
    for entry in batch["observation_records"]:
        record_path = _safe_evidence_path(
            root, entry["record_path"], f"{EVIDENCE_ROOT}/observations/"
        )
        if _write_observation_once(record_path, entry["record_bytes"]):
            new_paths.append(entry["record_path"])

    return {
        "raw_pointer": batch["raw_pointer"],
        "observation_count": len(batch["observation_records"]),
        "new_observation_paths": new_paths,
    }


def write_latest_pointer(root: Path, captured_at_utc: str, per_source_events: dict[str, list[dict]]) -> Path:
    """data/latest_macro_event_calendar.json -- small, mutable pointer (like
    this repo's other data/latest_*.json files) listing this run's own
    view of every event it derived; the append-only evidence under
    evidence/macro_event_calendar/observations/ remains authoritative."""
    events = []
    for source_key, source_events in per_source_events.items():
        for event in source_events:
            events.append({
                "event_type": event["event_type"],
                "market": event["market"],
                "scheduled_date": event["scheduled_date"],
                "scheduled_time": event["scheduled_time"],
                "timezone": event["timezone"],
                "status": event["status"],
                "status_basis": event["status_basis"],
                "detail": event["detail"],
                "source_key": source_key,
            })
    events.sort(key=lambda e: (e["scheduled_date"], e["event_type"], e["market"]))
    pointer = {
        "schema_version": "macro_event_calendar_latest_pointer/1",
        "captured_at_utc": captured_at_utc,
        "sources_captured": sorted(per_source_events),
        "event_count": len(events),
        "events": events,
        "authority": AUTHORITY,
    }
    path = root / "data" / "latest_macro_event_calendar.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


# ─────────────────────────────────────────────────────────────────────────
# Reader -- minimal, standalone. Not imported by any briefing, decision,
# rule, or execution path in this PR (evidence collection only).
# ─────────────────────────────────────────────────────────────────────────

def events_on_date(root: Path, date_iso: str, *, market: str | None = None) -> list[dict]:
    """Events from data/latest_macro_event_calendar.json whose
    scheduled_date equals date_iso. Returns [] if the pointer is absent or
    no event matches -- never raises for "nothing scheduled that day"."""
    pointer_path = root / "data" / "latest_macro_event_calendar.json"
    if not pointer_path.is_file():
        return []
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        fail("LATEST_POINTER_UNREADABLE")
    return [
        e for e in pointer.get("events", [])
        if e.get("scheduled_date") == date_iso and (market is None or e.get("market") == market)
    ]


# ─────────────────────────────────────────────────────────────────────────
# CLI entrypoint -- workflow_dispatch only (see
# .github/workflows/macro-event-calendar.yml). No cron is enabled by this
# PR; the proposed schedule is written in that workflow's comments only,
# pending explicit user approval.
# ─────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources", default=None,
        help="Space-separated subset of " + " ".join(SOURCES) + " (default: all).",
    )
    parser.add_argument("--years-back", type=int, default=YEARS_BACK_DEFAULT)
    parser.add_argument("--years-forward", type=int, default=YEARS_FORWARD_DEFAULT)
    parser.add_argument(
        "--all-years", action="store_true",
        help="Parse every panel/year the FOMC and BOK pages carry (one-time "
             "wide capture) instead of the bounded [captured_year-years_back, "
             "captured_year+years_forward] window. BLS tables are unaffected "
             "(they never carry more than roughly a year of rows).",
    )
    args = parser.parse_args(argv)

    requested = args.sources.split() if args.sources else list(SOURCES)
    invalid = sorted(set(requested) - set(SOURCES))
    if invalid:
        print(f"SOURCE_KEY_INVALID: {invalid} not in {list(SOURCES)}", file=sys.stderr)
        return 2

    captured_at = dt.datetime.now(tz=UTC)
    year = captured_at.year
    year_min = 1 if args.all_years else year - args.years_back
    year_max = 9999 if args.all_years else year + args.years_forward

    per_source_events: dict[str, list[dict]] = {}
    summary = {}
    for source_key in requested:
        try:
            raw = fetch_calendar(source_key)
            batch = build_events(captured_at, source_key, raw, year_min=year_min, year_max=year_max)
        except MacroEventCalendarError as exc:
            print(f"WARN: fetch/parse failed for {source_key}: {exc}", file=sys.stderr)
            continue
        result = publish_batch(ROOT, batch)
        summary[source_key] = result
        per_source_events[source_key] = [entry["record"] for entry in batch["observation_records"]]

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if per_source_events:
        captured_at_utc = captured_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        write_latest_pointer(ROOT, captured_at_utc, per_source_events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
