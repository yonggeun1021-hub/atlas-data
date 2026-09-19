#!/usr/bin/env python3
"""US official session calendar under US-SESSION-CALENDAR-SOURCE-V1-20260914.

User ratification ``evidence/authority/us_session_calendar_source_user_ratification_20260914.json``
(bound by ``config/us_session_calendar_source_v1.json``) fixes the only two ways
a US date may be classified for INTERNAL_VIRTUAL_PAPER use. REAL is out of scope.

1. **Official capture.** This covers the years the captured NYSE hours-calendars
   page publishes. The raw page bytes are sha256-bound and parsed offline. The
   date rule mirrors ``market_data/krx_official_holiday_calendar.py``:
   - a listed closure is ``CLOSED``;
   - a date in the official early-close statement is ``OPEN_EARLY_CLOSE``;
   - Saturday and Sunday are ``CLOSED``;
   - any other date of a published year is ``OPEN_REGULAR``.
   The last two lines are the ratified ``official_capture.date_rule`` in
   ``config/us_session_calendar_source_v1.json`` (the KRX mirror), and they
   apply only inside a year the captured official page publishes. There, the
   page's closure table and early-close statement are the complete official
   list for that year, so a weekend or an unlisted weekday is read from that
   official publication rather than inferred. No year outside the page's
   published years is ever classified this way.
   The Nasdaq holiday schedule is a cross-check where it is available. Any
   disagreement, including with a supplied Alpaca market-calendar row, is
   ``UNKNOWN``.
2. **Historical two-source.** This covers 2018 up to the first officially
   published year. A date is a session only when BOTH the Alpaca market
   calendar lists it AND SPY has an Alpaca IEX daily bar for it. If neither
   source lists it, it is ``CLOSED``. If only one does, or the date falls
   outside either capture window, it is ``UNKNOWN``. Early close is read from
   the Alpaca calendar close time and never inferred.

``UNKNOWN`` is the registry's ``US_FINISHED_SESSION_UNKNOWN``. Outside the
ratified official-year date rule above, nothing here derives a session from a
weekday or a holiday rule: in the two-source era, a weekday that no source
attests stays ``UNKNOWN`` and a weekend is ``CLOSED`` only when neither source
lists it.

Network access exists only in ``http_capture``/``capture_page``. Tests inject a
fake opener. Parsers and builders are pure functions over captured bytes.

The consensus packet (``us_official_calendar_consensus/1``) has exactly the
field set ``market_data/us_natural_session_receipt.py::_calendar_from_bundle``
requires. The receipt contract names NYSE and Nasdaq as its sources, so only an
official-year packet with an agreeing Nasdaq cross-check is receipt-admissible.
A historical two-source packet is well-formed but is refused there by source
identity. That refusal is intentional: the receipt is a NATURAL-session gate,
and historical replay does not pass through it.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONFIG_PATH = ROOT / "config" / "us_session_calendar_source_v1.json"
NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

CAPTURE_SCHEMA = "us_session_calendar_http_capture/1"
CONSENSUS_SCHEMA = "us_official_calendar_consensus/1"
DAY_CALENDAR_SCHEMA = "us_session_day_calendar/1"

NYSE_SOURCE_ID = "NYSE_HOLIDAYS_AND_TRADING_HOURS"
NYSE_URL = "https://www.nyse.com/trade/hours-calendars"
NASDAQ_SOURCE_ID = "NASDAQ_TRADING_SCHEDULE"
NASDAQ_URL = "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule"
ALPACA_CALENDAR_SOURCE_ID = "ALPACA_MARKET_CALENDAR"
ALPACA_CALENDAR_URLS = (
    "https://paper-api.alpaca.markets/v2/calendar",
    "https://api.alpaca.markets/v2/calendar",
)
ALPACA_SPY_BAR_SOURCE_ID = "ALPACA_IEX_SPY_DAILY_BAR"
ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
# The receipt contract's official source ids and urls.
RECEIPT_SOURCE_URLS = {NYSE_SOURCE_ID: NYSE_URL, NASDAQ_SOURCE_ID: NASDAQ_URL}
PAGE_HOSTS = {NYSE_SOURCE_ID: "www.nyse.com", NASDAQ_SOURCE_ID: "www.nasdaq.com"}
RECEIPT_CONTRACT_PATH = ROOT / "config" / "us_natural_session_receipt_contract.json"

STATUS_REGULAR = "OPEN_REGULAR"
STATUS_EARLY = "OPEN_EARLY_CLOSE"
STATUS_CLOSED = "CLOSED"
STATUS_UNKNOWN = "UNKNOWN"
OPEN_STATUSES = (STATUS_REGULAR, STATUS_EARLY)
UNKNOWN_RESULT = "US_FINISHED_SESSION_UNKNOWN"

BASIS_OFFICIAL = "OFFICIAL_NYSE_CAPTURE"
BASIS_TWO_SOURCE = "HISTORICAL_ALPACA_CALENDAR_AND_IEX_SPY_BAR"
BASIS_NONE = "OUTSIDE_RATIFIED_SOURCE_SCOPE"

HISTORICAL_FIRST_YEAR = 2018
MAX_REDIRECTS = 3
MIN_OFFICIAL_CLOSURES_PER_YEAR = 8

AUTHORITY = {
    "market_calendar_observation_only": True,
    "candidate_authorized": False,
    "entry_authorized": False,
    "order_authorized": False,
    "trading_authorized": False,
    "real_capital_authorized": False,
}

MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December",
        ),
        start=1,
    )
}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_RE = "|".join(MONTHS)
_WEEKDAY_RE = "|".join(WEEKDAYS)
# "Thursday, January 1" (NYSE table cell; year comes from the column header).
TABLE_CELL_DATE = re.compile(rf"^({_WEEKDAY_RE}),\s+({_MONTH_RE})\s+(\d{{1,2}})\b")
# "Friday, November 27, 2026" or "November 27, 2026" (statements, Nasdaq rows).
FULL_DATE = re.compile(rf"(?:({_WEEKDAY_RE}),\s+)?({_MONTH_RE})\s+(\d{{1,2}}),\s+(\d{{4}})")
YEAR_CELL = re.compile(r"^(\d{4})$")
EMPTY_CELL = {"", "-", "—", "–", "n/a", "N/A"}


class UsSessionCalendarError(ValueError):
    """A US session calendar invariant failed closed."""


def fail(code: str, detail: str = "") -> None:
    raise UsSessionCalendarError(f"{code}:{detail}" if detail else code)


# ---------------------------------------------------------------------------
# Hashing / time helpers.
# ---------------------------------------------------------------------------


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    return digest(Path(path).read_bytes())


def utc_z(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail(code)
    try:
        return dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        fail(code)


def parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code, value)
    if parsed.isoformat() != value:
        fail(code, value)
    return parsed


def daterange(start: dt.date, end: dt.date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += dt.timedelta(days=1)


def session_hours(day: dt.date, status: str) -> tuple[str | None, str | None]:
    if status not in OPEN_STATUSES:
        return None, None
    close = dt.time(16, 0) if status == STATUS_REGULAR else dt.time(13, 0)
    opened = dt.datetime.combine(day, dt.time(9, 30), tzinfo=NY)
    closed = dt.datetime.combine(day, close, tzinfo=NY)
    return opened.isoformat(), closed.isoformat()


# ---------------------------------------------------------------------------
# Ratified source configuration.
# ---------------------------------------------------------------------------


def load_source_config(path: Path = SOURCE_CONFIG_PATH, *, root: Path = ROOT) -> dict:
    """Load the PAPER-scope source overlay and re-bind every hash it names."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("SOURCE_CONFIG_READ_FAILED")
    if (
        not isinstance(config, dict)
        or config.get("schema_version") != 1
        or config.get("contract_version") != "us_session_calendar_source/1"
        or config.get("market") != "US"
        or config.get("timezone") != "America/New_York"
    ):
        fail("SOURCE_CONFIG_IDENTITY_INVALID")
    if config.get("weekday_inference_prohibited") is not True:
        fail("SOURCE_CONFIG_WEEKDAY_INFERENCE_OPEN")
    if config.get("real_authorized") is not False or config.get("authority") != AUTHORITY:
        fail("SOURCE_CONFIG_AUTHORITY_OPEN")
    if config.get("conflict_or_missing_result") != UNKNOWN_RESULT:
        fail("SOURCE_CONFIG_UNKNOWN_RESULT_INVALID")
    ratification = config.get("ratification") or {}
    ratification_path = root / str(ratification.get("path", ""))
    if not ratification_path.is_file() or file_sha256(ratification_path) != ratification.get("sha256"):
        fail("SOURCE_CONFIG_RATIFICATION_HASH_MISMATCH")
    record = json.loads(ratification_path.read_text(encoding="utf-8"))
    if (
        record.get("ratification_id") != ratification.get("ratification_id")
        or "no weekday/holiday-rule inference" not in record.get("prohibitions", [])
        or "no single-source historical session" not in record.get("prohibitions", [])
    ):
        fail("SOURCE_CONFIG_RATIFICATION_CONTENT_INVALID")
    binding = config.get("registry_binding") or {}
    registry_path = root / str(binding.get("path", ""))
    if not registry_path.is_file() or file_sha256(registry_path) != binding.get("sha256"):
        fail("SOURCE_CONFIG_REGISTRY_HASH_MISMATCH")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    official = registry["markets"]["US"]["official_calendar"]
    if (
        official.get("weekday_inference_prohibited") is not True
        or official.get("conflict_or_missing_result") != UNKNOWN_RESULT
        or [row["source_id"] for row in official.get("sources", [])]
        != [NYSE_SOURCE_ID, NASDAQ_SOURCE_ID]
    ):
        fail("SOURCE_CONFIG_REGISTRY_CALENDAR_CHANGED")
    capture = config.get("official_capture") or {}
    if capture.get("source_id") != NYSE_SOURCE_ID or capture.get("url") != NYSE_URL:
        fail("SOURCE_CONFIG_OFFICIAL_SOURCE_INVALID")
    cross = capture.get("cross_check") or {}
    if cross.get("source_id") != NASDAQ_SOURCE_ID or cross.get("url") != NASDAQ_URL:
        fail("SOURCE_CONFIG_CROSS_CHECK_INVALID")
    historical = config.get("historical_two_source") or {}
    if historical.get("first_year") != HISTORICAL_FIRST_YEAR or [
        row.get("source_id") for row in historical.get("sources", [])
    ] != [ALPACA_CALENDAR_SOURCE_ID, ALPACA_SPY_BAR_SOURCE_ID]:
        fail("SOURCE_CONFIG_HISTORICAL_SOURCES_INVALID")
    return copy.deepcopy(config)


# ---------------------------------------------------------------------------
# HTTP capture (the only network code in this module).
# ---------------------------------------------------------------------------


class _CountingRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise urllib.error.HTTPError(req.full_url, code, "TOO_MANY_REDIRECTS", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_open(url: str, headers: dict) -> tuple[int, str, str, int, bytes]:
    redirects = _CountingRedirects()
    opener = urllib.request.build_opener(redirects)
    request = urllib.request.Request(url, headers=headers, method="GET")
    with opener.open(request, timeout=30) as response:
        return (
            response.status,
            response.headers.get("Content-Type", ""),
            response.geturl(),
            redirects.count,
            response.read(),
        )


def http_capture(
    source_id: str,
    url: str,
    params: dict,
    *,
    headers: dict | None = None,
    secret_params: dict | None = None,
    opener=None,
    clock=None,
) -> dict:
    """GET one URL and return a capture record.

    ``params`` are recorded verbatim. ``secret_params`` (for example a FRED
    ``api_key``) and ``headers`` (Alpaca key headers) are sent but never
    recorded. Transport errors become a capture with ``transport_error`` set
    and no body. The exception text is never kept, because urllib error
    strings can contain the full URL and therefore a query-string key.
    """
    opener = _default_open if opener is None else opener
    clock = (lambda: dt.datetime.now(UTC)) if clock is None else clock
    query = urllib.parse.urlencode({**params, **(secret_params or {})})
    full_url = f"{url}?{query}" if query else url
    started = clock()
    status = None
    content_type = ""
    final_url = None
    redirect_count = 0
    raw = b""
    transport_error = None
    try:
        status, content_type, final_url, redirect_count, raw = opener(full_url, dict(headers or {}))
    except urllib.error.HTTPError as exc:
        status = exc.code
        transport_error = f"HTTP_ERROR:{exc.code}"
    except urllib.error.URLError:
        transport_error = "NETWORK_ERROR:URL_ERROR"
    except TimeoutError:
        transport_error = "NETWORK_ERROR:TIMEOUT"
    except OSError as exc:
        transport_error = f"NETWORK_ERROR:{type(exc).__name__}"
    received = clock()
    if transport_error is None and status != 200:
        transport_error = f"HTTP_STATUS:{status}"
    if final_url is not None and secret_params:
        # Strip every secret parameter from the recorded final url.
        parsed = urllib.parse.urlsplit(final_url)
        kept = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if key not in secret_params
        ]
        final_url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(kept)))
    if transport_error is not None:
        raw = b""
    return {
        "schema_version": CAPTURE_SCHEMA,
        "source_id": source_id,
        "request": {"url": url, "params": dict(sorted(params.items()))},
        "capture_started_at": utc_z(started),
        "response_received_at": utc_z(received),
        "response": {
            "http_status": status,
            "content_type": content_type,
            "final_url": final_url,
            "redirect_count": redirect_count,
            "raw_base64": base64.b64encode(raw).decode("ascii"),
            "raw_sha256": digest(raw) if transport_error is None else None,
        },
        "transport_error": transport_error,
        "authority": dict(AUTHORITY),
    }


def capture_page(source_id: str, *, opener=None, clock=None) -> dict:
    if source_id not in RECEIPT_SOURCE_URLS:
        fail("PAGE_SOURCE_INVALID", source_id)
    return http_capture(
        source_id,
        RECEIPT_SOURCE_URLS[source_id],
        {},
        headers={"User-Agent": "Atlas-US-Session-Calendar/1", "Accept": "text/html"},
        opener=opener,
        clock=clock,
    )


def validate_capture(capture: object, source_id: str) -> tuple[dict, bytes]:
    """Return ``(capture, raw)`` for a successful, hash-consistent capture."""
    fields = {
        "schema_version", "source_id", "request", "capture_started_at",
        "response_received_at", "response", "transport_error", "authority",
    }
    if not isinstance(capture, dict) or set(capture) != fields:
        fail("CAPTURE_FIELDS_INVALID", source_id)
    if capture["schema_version"] != CAPTURE_SCHEMA or capture["source_id"] != source_id:
        fail("CAPTURE_IDENTITY_INVALID", source_id)
    if capture["authority"] != AUTHORITY:
        fail("CAPTURE_AUTHORITY_INVALID", source_id)
    if capture["transport_error"] is not None:
        fail("CAPTURE_TRANSPORT_FAILED", f"{source_id}:{capture['transport_error']}")
    started = parse_instant(capture["capture_started_at"], "CAPTURE_TIME_INVALID")
    received = parse_instant(capture["response_received_at"], "CAPTURE_TIME_INVALID")
    if started > received:
        fail("CAPTURE_TIME_ORDER_INVALID", source_id)
    response = capture["response"]
    if not isinstance(response, dict) or response.get("http_status") != 200:
        fail("CAPTURE_HTTP_STATUS_INVALID", source_id)
    if source_id in PAGE_HOSTS:
        final = urllib.parse.urlsplit(str(response.get("final_url") or ""))
        if final.scheme != "https" or final.hostname != PAGE_HOSTS[source_id]:
            fail("CAPTURE_FINAL_URL_OFF_SOURCE", source_id)
        if capture["request"].get("url") != RECEIPT_SOURCE_URLS[source_id]:
            fail("CAPTURE_REQUEST_URL_INVALID", source_id)
    try:
        raw = base64.b64decode(response.get("raw_base64", ""), validate=True)
    except (ValueError, TypeError):
        fail("CAPTURE_BASE64_INVALID", source_id)
    if not raw or digest(raw) != response.get("raw_sha256"):
        fail("CAPTURE_RAW_HASH_MISMATCH", source_id)
    return capture, raw


# ---------------------------------------------------------------------------
# Official page parsers (offline).
# ---------------------------------------------------------------------------


_BLOCK_TAGS = {"p", "li", "div", "td", "th", "tr", "section", "article", "br", "h1", "h2", "h3", "h4", "h5", "h6", "table"}


class _PageText(HTMLParser):
    """Collect tables (rows of cell text) and block-level text segments."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.blocks: list[str] = []
        self._table_stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._block: list[str] = []
        self._skip = 0

    def _flush_block(self):
        text = " ".join("".join(self._block).split())
        if text:
            self.blocks.append(text)
        self._block = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush_block()
        if tag == "table":
            self._table_stack.append([])
        elif tag == "tr" and self._table_stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table_stack:
            if self._row:
                self._table_stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._table_stack:
            self.tables.append(self._table_stack.pop())
        if tag in _BLOCK_TAGS:
            self._flush_block()

    def handle_data(self, data):
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        self._block.append(data)

    def close(self):
        super().close()
        self._flush_block()


def _page_text(raw: bytes) -> _PageText:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail("PAGE_NOT_UTF8")
    parser = _PageText()
    parser.feed(text)
    parser.close()
    return parser


def _strip_markers(cell: str) -> str:
    return re.sub(r"[*†‡]+", "", cell).strip()


def _checked_date(year: int, month_name: str, day_text: str, weekday_name: str | None, code: str) -> dt.date:
    try:
        day = dt.date(year, MONTHS[month_name], int(day_text))
    except ValueError:
        fail(code, f"{year}-{month_name}-{day_text}")
    if weekday_name is not None and WEEKDAYS[day.weekday()] != weekday_name:
        fail(code + "_WEEKDAY_MISMATCH", day.isoformat())
    return day


def parse_nyse_page(raw: bytes) -> dict:
    """Parse the NYSE holidays table and the early-close statement.

    Fails closed when the page is ambiguous. That covers zero or several
    year-header tables, an unparseable cell, a weekday that contradicts its
    date, a weekend closure, too few closures per year, an early close that
    collides with a closure, and a published year with zero early-close dates.
    """
    page = _page_text(raw)
    candidates = []
    for table in page.tables:
        if not table:
            continue
        header = table[0]
        years = [YEAR_CELL.match(_strip_markers(cell)) for cell in header[1:]]
        if len(header) >= 2 and all(years):
            candidates.append((table, [int(match.group(1)) for match in years]))
    if len(candidates) != 1:
        fail("NYSE_HOLIDAY_TABLE_NOT_UNIQUE", str(len(candidates)))
    table, years = candidates[0]
    if len(set(years)) != len(years) or years != sorted(years):
        fail("NYSE_HOLIDAY_YEARS_INVALID")
    closures: dict[str, str] = {}
    for row in table[1:]:
        if len(row) != len(years) + 1:
            fail("NYSE_HOLIDAY_ROW_WIDTH_INVALID", row[0] if row else "")
        name = _strip_markers(row[0])
        if not name:
            fail("NYSE_HOLIDAY_NAME_MISSING")
        for year, cell in zip(years, row[1:]):
            text = _strip_markers(cell)
            if text in EMPTY_CELL:
                continue
            match = TABLE_CELL_DATE.match(text)
            if match is None:
                fail("NYSE_HOLIDAY_CELL_UNPARSEABLE", f"{name}:{year}")
            day = _checked_date(year, match.group(2), match.group(3), match.group(1), "NYSE_HOLIDAY_DATE_INVALID")
            if day.weekday() >= 5:
                fail("NYSE_HOLIDAY_ON_WEEKEND", day.isoformat())
            if day.isoformat() in closures:
                fail("NYSE_HOLIDAY_DUPLICATE", day.isoformat())
            closures[day.isoformat()] = name
    for year in years:
        count = sum(1 for key in closures if key.startswith(f"{year}-"))
        if count < MIN_OFFICIAL_CLOSURES_PER_YEAR:
            fail("NYSE_HOLIDAY_YEAR_INCOMPLETE", f"{year}:{count}")
    early: dict[str, str] = {}
    for block in page.blocks:
        lowered = block.lower()
        if "close early" not in lowered and "early close" not in lowered:
            continue
        if "1:00 p.m." not in lowered:
            continue
        for match in FULL_DATE.finditer(block):
            year = int(match.group(4))
            if year not in years:
                continue
            day = _checked_date(year, match.group(2), match.group(3), match.group(1), "NYSE_EARLY_CLOSE_DATE_INVALID")
            if day.weekday() >= 5 or day.isoformat() in closures:
                fail("NYSE_EARLY_CLOSE_COLLIDES", day.isoformat())
            early[day.isoformat()] = "NYSE_EARLY_CLOSE_1PM_STATEMENT"
    for year in years:
        # Every NYSE year has at least one 1:00 p.m. early close (the day after
        # Thanksgiving). A published year with none means the statement was not
        # parsed, and a silent {} would turn those dates into OPEN_REGULAR.
        if not any(key.startswith(f"{year}-") for key in early):
            fail("NYSE_EARLY_CLOSE_YEAR_MISSING", str(year))
    return {
        "source_id": NYSE_SOURCE_ID,
        "published_years": years,
        "closures": dict(sorted(closures.items())),
        "early_closes": dict(sorted(early.items())),
    }


def parse_nasdaq_page(raw: bytes) -> dict:
    """Parse Nasdaq holiday-schedule rows: a full date plus Closed/Early Close."""
    page = _page_text(raw)
    closures: dict[str, str] = {}
    early: dict[str, str] = {}
    for table in page.tables:
        for row in table:
            joined = " | ".join(row)
            lowered = joined.lower()
            is_early = "early close" in lowered or "close early" in lowered
            is_closed = "closed" in lowered and not is_early
            if not (is_early or is_closed):
                continue
            dates = list(FULL_DATE.finditer(joined))
            if len(dates) != 1:
                fail("NASDAQ_ROW_DATE_NOT_UNIQUE", joined[:80])
            match = dates[0]
            day = _checked_date(int(match.group(4)), match.group(2), match.group(3), match.group(1), "NASDAQ_DATE_INVALID")
            key = day.isoformat()
            if key in closures or key in early:
                fail("NASDAQ_DATE_DUPLICATE", key)
            (early if is_early else closures)[key] = _strip_markers(row[0])
    if not closures:
        fail("NASDAQ_SCHEDULE_UNPARSEABLE")
    years = sorted({int(key[:4]) for key in closures})
    return {
        "source_id": NASDAQ_SOURCE_ID,
        # The schedule may list only the remaining dates of its first year, so
        # it cross-checks from its earliest listed date onward, never before.
        "coverage_start": min([*closures, *early]),
        "published_years": years,
        "closures": dict(sorted(closures.items())),
        "early_closes": dict(sorted(early.items())),
    }


def parse_alpaca_calendar(raw: bytes) -> dict:
    """Alpaca ``/v2/calendar`` rows as ``{date: {"open", "close"}}``."""
    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail("ALPACA_CALENDAR_JSON_INVALID")
    if not isinstance(rows, list) or not rows:
        fail("ALPACA_CALENDAR_EMPTY")
    result: dict[str, dict] = {}
    previous = None
    for row in rows:
        if not isinstance(row, dict) or not {"date", "open", "close"} <= set(row):
            fail("ALPACA_CALENDAR_ROW_INVALID")
        day = parse_date(row["date"], "ALPACA_CALENDAR_DATE_INVALID")
        if previous is not None and day <= previous:
            fail("ALPACA_CALENDAR_ORDER_OR_DUPLICATE", day.isoformat())
        if not isinstance(row["open"], str) or not isinstance(row["close"], str):
            fail("ALPACA_CALENDAR_HOURS_INVALID", day.isoformat())
        result[day.isoformat()] = {"open": row["open"], "close": row["close"]}
        previous = day
    return result


def official_page_status(parsed: dict, day: dt.date) -> str | None:
    """Status the parsed official page states for ``day``, or ``None`` if the
    page does not publish that year."""
    if day.year not in parsed["published_years"]:
        return None
    key = day.isoformat()
    if key in parsed["closures"]:
        return STATUS_CLOSED
    if key in parsed["early_closes"]:
        return STATUS_EARLY
    if day.weekday() >= 5:
        return STATUS_CLOSED
    return STATUS_REGULAR


def alpaca_calendar_status(row: dict | None) -> str:
    """Status an Alpaca calendar row attests. A missing row attests CLOSED
    only inside the capture window, which the caller checks."""
    if row is None:
        return STATUS_CLOSED
    if row.get("open") != "09:30":
        return STATUS_UNKNOWN
    if row.get("close") == "16:00":
        return STATUS_REGULAR
    if row.get("close") == "13:00":
        return STATUS_EARLY
    return STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------


def _in_window(day: dt.date, window: tuple[str, str] | None) -> bool:
    if window is None:
        return False
    return parse_date(window[0], "WINDOW_INVALID") <= day <= parse_date(window[1], "WINDOW_INVALID")


def classify_date(day: dt.date, sources: dict) -> dict:
    """Classify one date from captured sources.

    ``sources`` keys (any may be ``None``):
    ``nyse`` (parsed page), ``nasdaq`` (parsed page), ``alpaca_calendar``
    (``{date: row}``), ``alpaca_calendar_window`` (``[start, end]``),
    ``spy_bar_dates`` (set of ISO dates), ``spy_bar_window`` (``[start, end]``).
    """
    key = day.isoformat()
    nyse = sources.get("nyse")
    nasdaq = sources.get("nasdaq")
    calendar = sources.get("alpaca_calendar")
    calendar_window = sources.get("alpaca_calendar_window")
    calendar_known = calendar is not None and _in_window(day, calendar_window)
    first_official_year = min(nyse["published_years"]) if nyse else None

    if nyse is not None and day.year in nyse["published_years"]:
        status = official_page_status(nyse, day)
        attesting = [NYSE_SOURCE_ID]
        reason = "NYSE_OFFICIAL_PAGE"
        if (
            nasdaq is not None
            and day.year <= max(nasdaq["published_years"])
            and key >= nasdaq["coverage_start"]
        ):
            nasdaq_status = official_page_status(nasdaq, day)
            if nasdaq_status != status:
                return _classified(key, STATUS_UNKNOWN, BASIS_OFFICIAL, "NYSE_NASDAQ_CONFLICT", [NYSE_SOURCE_ID, NASDAQ_SOURCE_ID])
            attesting.append(NASDAQ_SOURCE_ID)
        if calendar_known and alpaca_calendar_status(calendar.get(key)) != status:
            return _classified(key, STATUS_UNKNOWN, BASIS_OFFICIAL, "NYSE_ALPACA_CALENDAR_CONFLICT", attesting + [ALPACA_CALENDAR_SOURCE_ID])
        return _classified(key, status, BASIS_OFFICIAL, reason, attesting)

    in_historical_scope = day.year >= HISTORICAL_FIRST_YEAR and (
        first_official_year is None or day.year < first_official_year
    )
    if nyse is None or not in_historical_scope:
        return _classified(key, STATUS_UNKNOWN, BASIS_NONE, "OUTSIDE_RATIFIED_SOURCE_SCOPE", [])
    spy_dates = sources.get("spy_bar_dates")
    if not calendar_known or spy_dates is None or not _in_window(day, sources.get("spy_bar_window")):
        return _classified(key, STATUS_UNKNOWN, BASIS_TWO_SOURCE, "TWO_SOURCE_CAPTURE_MISSING", [])
    listed = calendar.get(key)
    has_bar = key in spy_dates
    both = [ALPACA_CALENDAR_SOURCE_ID, ALPACA_SPY_BAR_SOURCE_ID]
    if listed is None and not has_bar:
        return _classified(key, STATUS_CLOSED, BASIS_TWO_SOURCE, "NEITHER_SOURCE_LISTS_DATE", both)
    if listed is None or not has_bar:
        return _classified(key, STATUS_UNKNOWN, BASIS_TWO_SOURCE, "SINGLE_SOURCE_ONLY", both)
    status = alpaca_calendar_status(listed)
    if status == STATUS_UNKNOWN:
        return _classified(key, STATUS_UNKNOWN, BASIS_TWO_SOURCE, "ALPACA_CALENDAR_HOURS_UNRECOGNIZED", both)
    return _classified(key, status, BASIS_TWO_SOURCE, "ALPACA_CALENDAR_AND_SPY_BAR_AGREE", both)


def _classified(key: str, status: str, basis: str, reason: str, attesting: list[str]) -> dict:
    return {
        "date": key,
        "status": status,
        "basis": basis,
        "reason": reason,
        "attesting_sources": list(attesting),
        "result_code": UNKNOWN_RESULT if status == STATUS_UNKNOWN else None,
    }


def build_day_calendar(start: dt.date, end: dt.date, sources: dict) -> list[dict]:
    if start > end:
        fail("CALENDAR_RANGE_INVALID")
    return [classify_date(day, sources) for day in daterange(start, end)]


# ---------------------------------------------------------------------------
# Consensus packet.
# ---------------------------------------------------------------------------


def build_consensus_packet(classified: dict, source_captures: dict) -> dict:
    """``us_official_calendar_consensus/1`` for one classified date.

    ``source_captures`` maps source id to ``{"source_url", "observed_at",
    "captured_at", "source_sha256"}`` for every attesting source.
    """
    day = parse_date(classified["date"], "PACKET_DATE_INVALID")
    status = classified["status"]
    open_at, close_at = session_hours(day, status)
    receipt_ids = receipt_source_ids()
    rows = []
    for source_id in classified["attesting_sources"]:
        meta = source_captures.get(source_id)
        if meta is None:
            fail("PACKET_SOURCE_CAPTURE_MISSING", source_id)
        rows.append({
            # Official pages carry the natural receipt contract's own source id
            # (matched by url); the registry id stays in source_record_id.
            "source_id": receipt_ids.get(source_id, source_id),
            "source_url": meta["source_url"],
            "source_record_id": f"{source_id}:{day.isoformat()}",
            "session_date": day.isoformat(),
            "status": status,
            "open_at": open_at,
            "close_at": close_at,
            "observed_at": meta["observed_at"],
            "captured_at": meta["captured_at"],
            "source_sha256": meta["source_sha256"],
        })
    if not rows:
        fail("PACKET_HAS_NO_ATTESTING_SOURCE", day.isoformat())
    packet = {
        "schema_version": CONSENSUS_SCHEMA,
        "session_date": day.isoformat(),
        "timezone": "America/New_York",
        "status": status,
        "open_at": open_at,
        "close_at": close_at,
        "captured_at": max(row["captured_at"] for row in rows),
        "sources": rows,
    }
    packet["bundle_sha256"] = payload_sha256(packet)
    return packet


def receipt_source_ids(path: Path = RECEIPT_CONTRACT_PATH) -> dict:
    """Registry source id -> natural receipt contract source id, matched by url."""
    contract = json.loads(Path(path).read_text(encoding="utf-8"))
    by_url = {row["source_url"]: row["source_id"] for row in contract["official_calendar_sources"]}
    result = {}
    for registry_id, url in RECEIPT_SOURCE_URLS.items():
        if url not in by_url:
            fail("RECEIPT_CONTRACT_SOURCE_URL_MISSING", registry_id)
        result[registry_id] = by_url[url]
    return result


def capture_meta(capture: dict, raw: bytes) -> dict:
    return {
        "source_url": capture["request"]["url"],
        "observed_at": capture["response_received_at"],
        "captured_at": capture["response_received_at"],
        "source_sha256": digest(raw),
    }


def completed_sessions(day_calendar: list[dict], as_of: dt.datetime) -> list[str]:
    """Session dates whose official close is at or before ``as_of``."""
    result = []
    for row in day_calendar:
        if row["status"] not in OPEN_STATUSES:
            continue
        _, close_at = session_hours(dt.date.fromisoformat(row["date"]), row["status"])
        if dt.datetime.fromisoformat(close_at) <= as_of:
            result.append(row["date"])
    return result
