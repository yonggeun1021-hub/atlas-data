#!/usr/bin/env python3
"""Macro event calendar capture/publish/reader regressions -- offline,
fixture HTML only. No network call is ever made by this file.

Fixture markup below mirrors the real structure fetched and inspected
2026-09-18 from each official source (see collectors/macro_event_calendar.py
module docstring):
  - Federal Reserve FOMC calendar page (panel/h4 + fomc-meeting__month/date
    divs + a statement <a href> once posted)
  - BLS "Schedule of Releases" <table class="release-list"> (CPI and
    Employment Situation pages share this exact shape)
  - Bank of Korea "Meeting Dates" <h3>YYYY</h3> + <table> of month <th> /
    date <td> cells
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "macro_event_calendar", ROOT / "collectors" / "macro_event_calendar.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

NOW = dt.datetime(2026, 9, 18, 12, 0, 0, tzinfo=dt.timezone.utc)


def authorities_false(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_authorized") and item is not False:
                return False
            if not authorities_false(item):
                return False
    elif isinstance(value, list):
        return all(authorities_false(item) for item in value)
    return True


# ─────────────────────────────────────────────────────────────────────────
# Fixture builders
# ─────────────────────────────────────────────────────────────────────────

def fomc_meeting_block(month: str, date_field: str, *, statement_date: str | None, shaded: bool) -> str:
    shade_class = "fomc-meeting--shaded " if shaded else ""
    statement_html = ""
    if statement_date is not None:
        statement_html = f'''
               <strong>Statement:</strong><br>
               <a href="/monetarypolicy/files/monetary{statement_date}a1.pdf">PDF</a> | <a href="/newsevents/pressreleases/monetary{statement_date}a.htm">HTML</a><br>'''
    return f'''
        <div class="{shade_class}row fomc-meeting" ">
            <div class="{shade_class}fomc-meeting__month col-xs-5 col-sm-3 col-md-2"><strong>{month}</strong></div>
            <div class="fomc-meeting__date col-xs-4 col-sm-9 col-md-10 col-lg-1">{date_field}</div>
            <div class="col-xs-12 col-md-4 col-lg-2">{statement_html}
            </div>
        </div>
'''


def fomc_html(panels: list[tuple[int, list[tuple[str, str, str | None]]]]) -> bytes:
    body = []
    for year, meetings in panels:
        panel_id = 40000 + year
        rows = "".join(
            fomc_meeting_block(month, date_field, statement_date=stmt, shaded=bool(i % 2))
            for i, (month, date_field, stmt) in enumerate(meetings)
        )
        body.append(
            f'<div class="panel panel-default"><div class="panel-heading">'
            f'<h4><a id="{panel_id}">{year} FOMC Meetings</a></h4></div>{rows}'
            f'<div class="panel-footer">* Meeting associated with a Summary of Economic Projections.  </div></div>'
        )
    return ("<html><body>" + "".join(body) + "</body></html>").encode("utf-8")


def bls_html(heading: str, rows: list[tuple[str, str, str]]) -> bytes:
    """``rows`` entries are normally 3-tuples of plain text; a row can also
    be a single pre-built ``<tr>...</tr>`` string (see
    ``test_row_with_nested_markup_in_a_cell_is_still_parsed`` /
    ``test_row_count_mismatch_fails_closed`` below) to model a source
    wrapping a cell in nested markup, or a deliberately malformed row."""
    tr_parts = []
    for row in rows:
        if isinstance(row, str):
            tr_parts.append(row)
        else:
            ref, date, time = row
            tr_parts.append(f"<tr><td>{ref}</td><td>{date}</td><td>{time}</td></tr>")
    tr = "".join(tr_parts)
    return f'''<html><body>
<h2>Schedule of Releases for the {heading}</h2>
<table class="release-list">
<thead><tr><th>Reference Month</th><th>Release Date</th><th>Release Time</th></tr></thead>
<tbody>{tr}</tbody>
</table>
</body></html>'''.encode("utf-8")


def bok_year_block(year: int, month_rows: list[list[str]], cell_rows: list[list[str]]) -> str:
    """``month_rows``/``cell_rows`` are parallel lists of same-length rows,
    matching the real page's shape of more than one <th>-row/<td>-row pair
    per year (e.g. Jan/Feb/Apr/May then Jul/Aug/Oct/Nov -- BOK's real
    8-meeting-a-year cadence)."""
    pairs = []
    for months, cells in zip(month_rows, cell_rows):
        ths = "".join(f'<th scope="col"><strong class="fc1">{m}</strong></th>' for m in months)
        tds = "".join(f'<td><p style="text-align: center;">{c}</p></td>' for c in cells)
        pairs.append(f"<tr>{ths}</tr><tr>{tds}</tr>")
    return f'''<h3>{year}</h3>
<div class="table table-view tac">
<table>
<tbody>
{"".join(pairs)}
</tbody>
</table></div>
'''


def bok_full_year_block(year: int, dates: list[str]) -> str:
    """8 meetings, BOK's standard Jan/Feb/Apr/May // Jul/Aug/Oct/Nov
    cadence, as two <th>-row/<td>-row pairs -- the real page's shape."""
    if len(dates) != 8:
        raise ValueError("bok_full_year_block needs exactly 8 dates")
    months = ["Jan.", "Feb.", "Apr.", "May.", "Jul.", "Aug.", "Oct.", "Nov."]
    return bok_year_block(
        year,
        [months[:4], months[4:]],
        [dates[:4], dates[4:]],
    )


def bok_html(blocks: list[str]) -> bytes:
    return (
        "<html><body><h1>Meeting Dates</h1>"
        "<h2>Schedule of the MPB's policy-setting meetings</h2>"
        + "".join(blocks) + "</body></html>"
    ).encode("utf-8")


FOMC_2026 = [
    ("January", "27-28", "20260128"),
    ("March", "17-18*", "20260318"),
    ("October", "27-28", None),
    ("December", "8-9*", None),
]

FOMC_RAW = fomc_html([(2026, FOMC_2026)])

CPI_RAW = bls_html("Consumer Price Index", [
    ("August 2026", "Sep. 11, 2026", "08:30 AM"),
    ("September 2026", "Oct. 14, 2026", "08:30 AM"),
])

NFP_RAW = bls_html("Employment Situation", [
    ("August 2026", "Sep. 04, 2026", "08:30 AM"),
    ("September 2026", "Oct. 02, 2026", "08:30 AM"),
])

BOK_RAW = bok_html([
    bok_full_year_block(2026, [
        "Jan.15&nbsp;(Thu)", "Feb.26 (Thu)", "Apr.10 (Fri)", "May.28 (Thu)",
        "Jul.16 (Thu)", "Aug.27 (Thu)", "Oct.22 (Thu)", "Nov.26 (Thu)",
    ]),
])


class FomcParsingTests(unittest.TestCase):
    def test_two_day_meeting_decision_is_the_second_day(self):
        events = M.parse_fomc_calendar(FOMC_RAW, year_min=2026, year_max=2026)
        january = next(e for e in events if e["detail"]["meeting_start_date"] == "2026-01-27")
        self.assertEqual(january["scheduled_date"], "2026-01-28")
        self.assertEqual(january["market"], "US")
        self.assertEqual(january["event_type"], "FOMC_DECISION")

    def test_statement_link_present_means_released_and_sep_flag_parsed(self):
        events = M.parse_fomc_calendar(FOMC_RAW, year_min=2026, year_max=2026)
        march = next(e for e in events if e["scheduled_date"] == "2026-03-18")
        self.assertEqual(march["status"], "released")
        self.assertEqual(march["status_basis"], M.BASIS_SOURCE_STATED)
        self.assertTrue(march["detail"]["summary_of_economic_projections"])

    def test_no_statement_link_means_scheduled(self):
        events = M.parse_fomc_calendar(FOMC_RAW, year_min=2026, year_max=2026)
        october = next(e for e in events if e["scheduled_date"] == "2026-10-28")
        self.assertEqual(october["status"], "scheduled")
        self.assertEqual(october["status_basis"], M.BASIS_SOURCE_STATED)

    def test_notation_vote_single_day(self):
        raw = fomc_html([(2022, [("August", "22 (notation vote)", None)])])
        events = M.parse_fomc_calendar(raw, year_min=2022, year_max=2022)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["scheduled_date"], "2022-08-22")
        self.assertTrue(events[0]["detail"]["notation_vote"])

    def test_month_crossing_meeting(self):
        raw = fomc_html([(2027, [("Apr/May", "30-1", None)])])
        events = M.parse_fomc_calendar(raw, year_min=2027, year_max=2027)
        self.assertEqual(events[0]["detail"]["meeting_start_date"], "2027-04-30")
        self.assertEqual(events[0]["scheduled_date"], "2027-05-01")

    def test_december_january_crossing_rolls_the_year_forward(self):
        raw = fomc_html([(2026, [("Dec/Jan", "31-1", None)])])
        events = M.parse_fomc_calendar(raw, year_min=2026, year_max=2027)
        self.assertEqual(events[0]["detail"]["meeting_start_date"], "2026-12-31")
        self.assertEqual(events[0]["scheduled_date"], "2027-01-01")

    def test_year_window_excludes_out_of_range_panels(self):
        raw = fomc_html([(2021, [("January", "26-27", None)]), (2026, FOMC_2026)])
        events = M.parse_fomc_calendar(raw, year_min=2025, year_max=2027)
        self.assertTrue(all(e["scheduled_date"].startswith("2026") for e in events))

    def test_statement_date_mismatch_fails_closed(self):
        raw = fomc_html([(2026, [("January", "27-28", "20260228")])])  # wrong embedded date
        with self.assertRaisesRegex(M.MacroEventCalendarError, "FOMC_STATEMENT_DATE_MISMATCH"):
            M.parse_fomc_calendar(raw, year_min=2026, year_max=2026)

    def test_unrecognized_date_format_fails_closed(self):
        raw = fomc_html([(2026, [("January", "TBD", None)])])
        with self.assertRaisesRegex(M.MacroEventCalendarError, "FOMC_DATE_FORMAT_UNRECOGNIZED"):
            M.parse_fomc_calendar(raw, year_min=2026, year_max=2026)

    def test_no_panels_found_fails_closed(self):
        with self.assertRaisesRegex(M.MacroEventCalendarError, "FOMC_NO_PANELS_FOUND"):
            M.parse_fomc_calendar(b"<html><body>nothing here</body></html>", year_min=2026, year_max=2026)


class BlsParsingTests(unittest.TestCase):
    def test_cpi_rows_parsed(self):
        events = M.parse_bls_release_table(CPI_RAW, "CPI")
        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual(first["event_type"], "CPI_RELEASE")
        self.assertEqual(first["scheduled_date"], "2026-09-11")
        self.assertEqual(first["scheduled_time"], "08:30")
        self.assertEqual(first["detail"]["reference_period"], "2026-08")
        self.assertIsNone(first["timezone"])

    def test_nfp_rows_parsed(self):
        events = M.parse_bls_release_table(NFP_RAW, "NFP")
        self.assertEqual(events[0]["event_type"], "NONFARM_PAYROLLS")
        self.assertEqual(events[0]["scheduled_date"], "2026-09-04")

    def test_row_with_nested_markup_in_a_cell_is_still_parsed(self):
        # Independent review 2026-09-18 (PR #785): BLS sometimes bolds or
        # link-wraps a cell (e.g. the next-upcoming release row). Before
        # the fix this made _BLS_ROW simply not match that <tr> at all --
        # a silently incomplete calendar, no error. Each cell here nests a
        # different kind of markup (<strong>, <a href>, <span>).
        raw = bls_html("Consumer Price Index", [
            "<tr><td><strong>August 2026</strong></td>"
            '<td><a href="/schedule/news_release/2026/09_sched.htm">Sep. 11, 2026</a></td>'
            "<td><span class=\"next-release\">08:30 AM</span></td></tr>",
            ("September 2026", "Oct. 14, 2026", "08:30 AM"),
        ])
        events = M.parse_bls_release_table(raw, "CPI")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["scheduled_date"], "2026-09-11")
        self.assertEqual(events[0]["scheduled_time"], "08:30")
        self.assertEqual(events[0]["detail"]["reference_period"], "2026-08")

    def test_row_count_mismatch_fails_closed(self):
        # A row this malformed (a 2-cell <td> row) cannot match the 3-<td>
        # row pattern at all -- findall() would otherwise just silently
        # return one fewer row than the table actually has. The <tr> count
        # cross-check must catch this and fail loudly instead.
        raw = bls_html("Consumer Price Index", [
            ("August 2026", "Sep. 11, 2026", "08:30 AM"),
            "<tr><td>September 2026</td><td>Oct. 14, 2026</td></tr>",
        ])
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BLS_ROW_COUNT_MISMATCH"):
            M.parse_bls_release_table(raw, "CPI")

    def test_pm_time_conversion(self):
        raw = bls_html("Consumer Price Index", [("August 2026", "Sep. 11, 2026", "01:15 PM")])
        events = M.parse_bls_release_table(raw, "CPI")
        self.assertEqual(events[0]["scheduled_time"], "13:15")

    def test_noon_and_midnight_edge_cases(self):
        raw = bls_html("Consumer Price Index", [
            ("August 2026", "Sep. 11, 2026", "12:00 PM"),
            ("September 2026", "Oct. 14, 2026", "12:00 AM"),
        ])
        events = M.parse_bls_release_table(raw, "CPI")
        self.assertEqual(events[0]["scheduled_time"], "12:00")
        self.assertEqual(events[1]["scheduled_time"], "00:00")

    def test_wrong_source_key_fails_closed(self):
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BLS_SOURCE_KEY_INVALID"):
            M.parse_bls_release_table(CPI_RAW, "FOMC")

    def test_page_identity_mismatch_fails_closed(self):
        raw = bls_html("Producer Price Index", [("August 2026", "Sep. 11, 2026", "08:30 AM")])
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BLS_PAGE_UNEXPECTED"):
            M.parse_bls_release_table(raw, "CPI")

    def test_missing_table_fails_closed(self):
        raw = b"<html><body><h2>Schedule of Releases for the Consumer Price Index</h2></body></html>"
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BLS_RELEASE_TABLE_NOT_FOUND"):
            M.parse_bls_release_table(raw, "CPI")

    def test_bad_header_fails_closed(self):
        raw = b'''<html><body><h2>Schedule of Releases for the Consumer Price Index</h2>
<table class="release-list"><thead><tr><th>Month</th><th>Date</th><th>Time</th></tr></thead>
<tbody><tr><td>August 2026</td><td>Sep. 11, 2026</td><td>08:30 AM</td></tr></tbody></table>
</body></html>'''
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BLS_TABLE_HEADER_UNEXPECTED"):
            M.parse_bls_release_table(raw, "CPI")

    def test_bad_time_format_fails_closed(self):
        raw = bls_html("Consumer Price Index", [("August 2026", "Sep. 11, 2026", "8:30")])
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BLS_RELEASE_TIME_INVALID"):
            M.parse_bls_release_table(raw, "CPI")


class BokParsingTests(unittest.TestCase):
    def test_full_year_all_eight_meetings_parsed_across_two_row_pairs(self):
        # The real page lays out one year as TWO <th>-row/<td>-row pairs
        # (Jan/Feb/Apr/May, then Jul/Aug/Oct/Nov) -- this stresses that
        # multi-row-pair shape, not just a single 4-column row.
        events = M.parse_bok_calendar(BOK_RAW, year_min=2026, year_max=2026)
        self.assertEqual(len(events), 8)
        jan = next(e for e in events if e["scheduled_date"] == "2026-01-15")
        self.assertEqual(jan["event_type"], "BOK_RATE_DECISION")
        self.assertEqual(jan["market"], "KR")
        self.assertEqual(jan["detail"]["weekday_label"], "Thu")
        nov = next(e for e in events if e["scheduled_date"] == "2026-11-26")
        self.assertEqual(nov["detail"]["weekday_label"], "Thu")

    def test_year_window_excludes_out_of_range_years(self):
        raw = bok_html([
            bok_year_block(2020, [["Jan."]], [["Jan.9 (Thu)"]]),
            bok_year_block(2026, [["Jan."]], [["Jan.15 (Thu)"]]),
        ])
        events = M.parse_bok_calendar(raw, year_min=2025, year_max=2027)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["scheduled_date"], "2026-01-15")

    def test_blank_cell_is_skipped_not_a_failure(self):
        raw = bok_html([bok_year_block(2026, [["Jan.", "Feb."]], [["Jan.15 (Thu)", ""]])])
        events = M.parse_bok_calendar(raw, year_min=2026, year_max=2026)
        self.assertEqual(len(events), 1)

    def test_month_mismatch_between_header_and_cell_fails_closed(self):
        raw = bok_html([bok_year_block(2026, [["Feb."]], [["Jan.15 (Thu)"]])])
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BOK_CELL_MONTH_MISMATCH"):
            M.parse_bok_calendar(raw, year_min=2026, year_max=2026)

    def test_page_identity_mismatch_fails_closed(self):
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BOK_PAGE_UNEXPECTED"):
            M.parse_bok_calendar(b"<html><body>wrong page</body></html>", year_min=2026, year_max=2026)

    def test_no_year_headers_fails_closed(self):
        raw = b"<html><body><h1>Meeting Dates</h1><h2>Schedule of the MPB's policy-setting meetings</h2></body></html>"
        with self.assertRaisesRegex(M.MacroEventCalendarError, "BOK_NO_YEAR_HEADERS_FOUND"):
            M.parse_bok_calendar(raw, year_min=2026, year_max=2026)


class ClockInferenceStatusTests(unittest.TestCase):
    def test_past_date_is_released_future_is_scheduled(self):
        events = M.parse_bls_release_table(bls_html("Consumer Price Index", [
            ("August 2026", "Sep. 11, 2026", "08:30 AM"),
            ("September 2026", "Dec. 31, 2026", "08:30 AM"),
        ]), "CPI")
        M._apply_clock_inference_status(events, NOW)  # NOW is 2026-09-18
        self.assertEqual(events[0]["status"], "released")
        self.assertEqual(events[1]["status"], "scheduled")
        self.assertTrue(all(e["status_basis"] == M.BASIS_CLOCK_INFERENCE for e in events))

    def test_fomc_status_never_overwritten_by_clock_inference(self):
        events = M.parse_fomc_calendar(FOMC_RAW, year_min=2026, year_max=2026)
        before = {e["scheduled_date"]: e["status"] for e in events}
        M._apply_clock_inference_status(events, NOW)
        after = {e["scheduled_date"]: e["status"] for e in events}
        self.assertEqual(before, after)


class BuildEventsTests(unittest.TestCase):
    def test_authority_is_all_false(self):
        batch = M.build_events(NOW, "FOMC", FOMC_RAW, year_min=2026, year_max=2026)
        self.assertTrue(authorities_false(batch["manifest"]))
        for entry in batch["observation_records"]:
            self.assertTrue(authorities_false(entry["record"]))

    def test_build_events_is_deterministic(self):
        first = M.build_events(NOW, "CPI", CPI_RAW, year_min=2026, year_max=2026)
        second = M.build_events(NOW, "CPI", CPI_RAW, year_min=2026, year_max=2026)
        self.assertEqual(first["manifest"], second["manifest"])
        self.assertEqual(
            [e["record_path"] for e in first["observation_records"]],
            [e["record_path"] for e in second["observation_records"]],
        )

    def test_capture_time_naive_fails_closed(self):
        naive = dt.datetime(2026, 9, 18, 12, 0, 0)
        with self.assertRaisesRegex(M.MacroEventCalendarError, "CAPTURE_TIME_NAIVE"):
            M.build_events(naive, "CPI", CPI_RAW, year_min=2026, year_max=2026)


class PublishTests(unittest.TestCase):
    def test_publish_writes_one_record_per_event_and_is_idempotent(self):
        batch = M.build_events(NOW, "CPI", CPI_RAW, year_min=2026, year_max=2026)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = M.publish_batch(root, batch)
            self.assertEqual(len(summary["new_observation_paths"]), 2)
            for path in summary["new_observation_paths"]:
                self.assertTrue((root / path).exists())
            summary2 = M.publish_batch(root, batch)
            self.assertEqual(summary2["new_observation_paths"], [])  # unchanged -- no-op

    def test_status_change_writes_a_new_record_not_a_collision(self):
        raw_future = bls_html("Consumer Price Index", [("August 2026", "Dec. 31, 2026", "08:30 AM")])
        scheduled_batch = M.build_events(NOW, "CPI", raw_future, year_min=2026, year_max=2026)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_batch(root, scheduled_batch)
            later = NOW + dt.timedelta(days=120)
            released_batch = M.build_events(later, "CPI", raw_future, year_min=2026, year_max=2026)
            summary = M.publish_batch(root, released_batch)
            self.assertEqual(len(summary["new_observation_paths"]), 1)
            scheduled_path = root / scheduled_batch["observation_records"][0]["record_path"]
            released_path = root / released_batch["observation_records"][0]["record_path"]
            self.assertNotEqual(scheduled_path, released_path)
            self.assertTrue(scheduled_path.exists())
            self.assertTrue(released_path.exists())
            self.assertEqual(json.loads(scheduled_path.read_text())["status"], "scheduled")
            self.assertEqual(json.loads(released_path.read_text())["status"], "released")

    def test_append_only_collision_fails_closed(self):
        batch = M.build_events(NOW, "CPI", CPI_RAW, year_min=2026, year_max=2026)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_batch(root, batch)
            path = root / batch["raw_pointer"]["manifest_path"]
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(M.MacroEventCalendarError, "APPEND_ONLY_COLLISION"):
                M.publish_batch(root, batch)

    def test_path_traversal_is_rejected(self):
        batch = M.build_events(NOW, "CPI", CPI_RAW, year_min=2026, year_max=2026)
        batch["raw_pointer"]["raw_path"] = f"{M.EVIDENCE_ROOT}/raw/../../secret"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(M.MacroEventCalendarError, "EVIDENCE_PATH_INVALID"):
                M.publish_batch(Path(tmp), batch)


class LatestPointerAndReaderTests(unittest.TestCase):
    def test_write_latest_pointer_and_read_back(self):
        fomc_batch = M.build_events(NOW, "FOMC", FOMC_RAW, year_min=2026, year_max=2026)
        cpi_batch = M.build_events(NOW, "CPI", CPI_RAW, year_min=2026, year_max=2026)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_batch(root, fomc_batch)
            M.publish_batch(root, cpi_batch)
            per_source = {
                "FOMC": [e["record"] for e in fomc_batch["observation_records"]],
                "CPI": [e["record"] for e in cpi_batch["observation_records"]],
            }
            M.write_latest_pointer(root, "2026-09-18T12:00:00Z", per_source)
            found = M.events_on_date(root, "2026-01-28")
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["event_type"], "FOMC_DECISION")
            self.assertEqual(M.events_on_date(root, "2099-01-01"), [])

    def test_events_on_date_with_no_pointer_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(M.events_on_date(Path(tmp), "2026-01-28"), [])

    def test_market_filter(self):
        bok_batch = M.build_events(NOW, "BOK", BOK_RAW, year_min=2026, year_max=2026)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_batch(root, bok_batch)
            M.write_latest_pointer(
                root, "2026-09-18T12:00:00Z",
                {"BOK": [e["record"] for e in bok_batch["observation_records"]]},
            )
            self.assertEqual(len(M.events_on_date(root, "2026-01-15", market="KR")), 1)
            self.assertEqual(M.events_on_date(root, "2026-01-15", market="US"), [])


class FetchTests(unittest.TestCase):
    def test_fetch_calendar_uses_the_registered_url(self):
        calls = []
        def fake_getter(url):
            calls.append(url)
            return CPI_RAW
        M.fetch_calendar("CPI", getter=fake_getter)
        self.assertEqual(calls[0], M.SOURCES["CPI"]["url"])

    def test_invalid_source_key_fails_closed(self):
        with self.assertRaisesRegex(M.MacroEventCalendarError, "SOURCE_KEY_INVALID"):
            M.fetch_calendar("NOT_A_SOURCE")

    def test_http_error_is_caught_and_reduced_to_a_status_code(self):
        import urllib.error
        from unittest import mock

        def raise_http_error(request, timeout=30):
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

        with mock.patch("urllib.request.urlopen", side_effect=raise_http_error):
            with self.assertRaisesRegex(M.MacroEventCalendarError, "^HTTP_ERROR_403$"):
                M.fetch_calendar("CPI")


class MainEndToEndTests(unittest.TestCase):
    def test_main_captures_all_sources_and_a_broken_one_does_not_abort_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root, original_fetch = M.ROOT, M.fetch_calendar
            M.ROOT = root

            def fake_fetch(source_key, *, getter=None):
                if source_key == "BOK":
                    M.fail("SIMULATED_FETCH_FAILURE")
                return {"FOMC": FOMC_RAW, "CPI": CPI_RAW, "NFP": NFP_RAW}[source_key]

            M.fetch_calendar = fake_fetch
            try:
                rc = M.main([])
            finally:
                M.ROOT = original_root
                M.fetch_calendar = original_fetch

            self.assertEqual(rc, 0)
            pointer = json.loads((root / "data" / "latest_macro_event_calendar.json").read_text())
            self.assertEqual(sorted(pointer["sources_captured"]), ["CPI", "FOMC", "NFP"])
            self.assertTrue((root / M.EVIDENCE_ROOT / "observations" / "FOMC_DECISION").is_dir())

    def test_main_rejects_invalid_source_argument(self):
        rc = M.main(["--sources", "NOT_A_SOURCE"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
