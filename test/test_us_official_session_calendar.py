#!/usr/bin/env python3
"""US official session calendar (US-SESSION-CALENDAR-SOURCE-V1-20260914).

Offline only: synthetic page fixtures, injected fake openers, fixed instants.
No test reads the wall clock or the network.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar as CAL  # noqa: E402
from market_data import us_natural_session_receipt as RECEIPT  # noqa: E402

FIXTURES = ROOT / "test" / "fixtures" / "us_session_calendar"
NYSE_HTML = (FIXTURES / "nyse_hours_calendars_synthetic.html").read_bytes()
NASDAQ_HTML = (FIXTURES / "nasdaq_holiday_schedule_synthetic.html").read_bytes()
FIXED = dt.datetime(2026, 9, 14, 21, 0, 0, tzinfo=dt.timezone.utc)


def fixed_clock():
    return FIXED


def page_opener(body: bytes, final_url: str, status: int = 200):
    def opener(url, headers):
        return status, "text/html; charset=utf-8", final_url, 0, body
    return opener


class SourceConfigTest(unittest.TestCase):
    def test_live_config_binds_ratification_and_registry(self):
        config = CAL.load_source_config()
        self.assertEqual(config["ratification"]["ratification_id"], "US-SESSION-CALENDAR-SOURCE-V1-20260914")
        self.assertTrue(config["weekday_inference_prohibited"])
        self.assertFalse(config["real_authorized"])
        self.assertEqual(config["conflict_or_missing_result"], "US_FINISHED_SESSION_UNKNOWN")

    def _temp_root(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for rel in (
            "config/us_session_calendar_source_v1.json",
            "config/regime_source_owner_registry_v2.json",
            "evidence/authority/us_session_calendar_source_user_ratification_20260914.json",
        ):
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, tmp / rel)
        return tmp

    def test_tampered_ratification_record_fails_closed(self):
        tmp = self._temp_root()
        path = tmp / "evidence/authority/us_session_calendar_source_user_ratification_20260914.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "RATIFICATION_HASH_MISMATCH"):
            CAL.load_source_config(tmp / "config/us_session_calendar_source_v1.json", root=tmp)

    def test_registry_drift_fails_closed(self):
        tmp = self._temp_root()
        path = tmp / "config/regime_source_owner_registry_v2.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "REGISTRY_HASH_MISMATCH"):
            CAL.load_source_config(tmp / "config/us_session_calendar_source_v1.json", root=tmp)

    def test_weekday_inference_cannot_be_opened(self):
        tmp = self._temp_root()
        path = tmp / "config/us_session_calendar_source_v1.json"
        config = json.loads(path.read_text())
        config["weekday_inference_prohibited"] = False
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "WEEKDAY_INFERENCE_OPEN"):
            CAL.load_source_config(path, root=tmp)

    def test_registry_bytes_are_unchanged_by_this_ratification(self):
        # The registry is hash-bound by existing runtime policies; the amendment
        # is an overlay, so the registry must still hash to the bound value.
        registry = json.loads((ROOT / "config/regime_source_owner_registry_v2.json").read_text())
        official = registry["markets"]["US"]["official_calendar"]
        self.assertTrue(official["weekday_inference_prohibited"])
        self.assertEqual(
            CAL.file_sha256(ROOT / "config/regime_source_owner_registry_v2.json"),
            "8dd2ad50f66e144aaca78ffc6a82615d814dee2b8f23f736cdbc85a56dba68bb",
        )


class PageParserTest(unittest.TestCase):
    def test_nyse_table_and_early_close_statement(self):
        parsed = CAL.parse_nyse_page(NYSE_HTML)
        self.assertEqual(parsed["published_years"], [2026, 2027, 2028])
        self.assertEqual(parsed["closures"]["2026-04-03"], "Good Friday")
        self.assertEqual(parsed["closures"]["2027-12-24"], "Christmas Day")
        self.assertNotIn("2028-01-01", parsed["closures"])  # em dash cell: no closure listed
        self.assertEqual(
            sorted(parsed["early_closes"]),
            ["2026-07-02", "2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24"],
        )
        self.assertEqual(len(parsed["closures"]), 29)

    def test_nyse_published_year_without_early_close_fails_closed(self):
        stripped = NYSE_HTML.replace(b"Friday, November 26, 2027, ", b"")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "NYSE_EARLY_CLOSE_YEAR_MISSING:2027"):
            CAL.parse_nyse_page(stripped)
        no_statement = NYSE_HTML.replace(b"1:00 p.m.", b"one o'clock")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "NYSE_EARLY_CLOSE_YEAR_MISSING:2026"):
            CAL.parse_nyse_page(no_statement)

    def test_nyse_weekday_contradicting_date_fails(self):
        bad = NYSE_HTML.replace(b"Friday, April 3", b"Thursday, April 3")
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "WEEKDAY_MISMATCH"):
            CAL.parse_nyse_page(bad)

    def test_nyse_second_year_table_is_ambiguous(self):
        start = NYSE_HTML.index(b"<table")
        end = NYSE_HTML.index(b"</table>") + len(b"</table>")
        doubled = NYSE_HTML[:end] + NYSE_HTML[start:end] + NYSE_HTML[end:]
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "NYSE_HOLIDAY_TABLE_NOT_UNIQUE"):
            CAL.parse_nyse_page(doubled)

    def test_nyse_unparseable_cell_and_incomplete_year_fail(self):
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "CELL_UNPARSEABLE"):
            CAL.parse_nyse_page(NYSE_HTML.replace(b"Monday, May 25", b"TBD"))
        rows = NYSE_HTML.split(b"<tr>")
        truncated = b"<tr>".join(rows[:5]) + b"</tbody></table></body></html>"
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "YEAR_INCOMPLETE"):
            CAL.parse_nyse_page(truncated)

    def test_nyse_page_without_table_fails(self):
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "NOT_UNIQUE"):
            CAL.parse_nyse_page(b"<html><body><div id='app'></div></body></html>")

    def test_nasdaq_rows(self):
        parsed = CAL.parse_nasdaq_page(NASDAQ_HTML)
        self.assertEqual(parsed["coverage_start"], "2026-01-01")
        self.assertIn("2026-11-26", parsed["closures"])
        self.assertIn("2026-11-27", parsed["early_closes"])

    def test_alpaca_calendar_order_is_enforced(self):
        rows = [{"date": "2018-01-03", "open": "09:30", "close": "16:00"}, {"date": "2018-01-02", "open": "09:30", "close": "16:00"}]
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "ORDER_OR_DUPLICATE"):
            CAL.parse_alpaca_calendar(json.dumps(rows).encode())


class CaptureTest(unittest.TestCase):
    def test_page_capture_binds_raw_sha(self):
        capture = CAL.capture_page(CAL.NYSE_SOURCE_ID, opener=page_opener(NYSE_HTML, CAL.NYSE_URL), clock=fixed_clock)
        checked, raw = CAL.validate_capture(capture, CAL.NYSE_SOURCE_ID)
        self.assertEqual(raw, NYSE_HTML)
        self.assertEqual(checked["response"]["raw_sha256"], CAL.digest(NYSE_HTML))
        tampered = copy.deepcopy(capture)
        tampered["response"]["raw_base64"] = base64.b64encode(NYSE_HTML + b"x").decode()
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "RAW_HASH_MISMATCH"):
            CAL.validate_capture(tampered, CAL.NYSE_SOURCE_ID)

    def test_redirect_off_source_host_fails(self):
        capture = CAL.capture_page(CAL.NYSE_SOURCE_ID, opener=page_opener(NYSE_HTML, "https://example.com/x"), clock=fixed_clock)
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "FINAL_URL_OFF_SOURCE"):
            CAL.validate_capture(capture, CAL.NYSE_SOURCE_ID)

    def test_secret_query_parameter_never_recorded(self):
        secret = "FREDSECRET0123456789"

        def opener(url, headers):
            self.assertIn(secret, url)
            return 200, "application/json", url, 0, b'{"vintage_dates":["2010-01-01"]}'

        capture = CAL.http_capture(
            "FRED_X", "https://api.stlouisfed.org/fred/series/vintagedates", {"series_id": "VIXCLS"},
            secret_params={"api_key": secret}, opener=opener, clock=fixed_clock,
        )
        self.assertNotIn(secret, json.dumps(capture))
        self.assertNotIn("api_key", json.dumps(capture))

    def test_transport_error_keeps_no_url_or_body(self):
        secret = "FREDSECRET0123456789"

        def opener(url, headers):
            raise urllib.error.HTTPError(url, 403, f"Forbidden {url}", {}, None)

        capture = CAL.http_capture(
            "FRED_X", "https://api.stlouisfed.org/x", {}, secret_params={"api_key": secret},
            opener=opener, clock=fixed_clock,
        )
        self.assertEqual(capture["transport_error"], "HTTP_ERROR:403")
        self.assertEqual(capture["response"]["raw_base64"], "")
        self.assertNotIn(secret, json.dumps(capture))
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "TRANSPORT_FAILED"):
            CAL.validate_capture(capture, "FRED_X")


def official_sources(**extra):
    sources = {
        "nyse": CAL.parse_nyse_page(NYSE_HTML),
        "nasdaq": CAL.parse_nasdaq_page(NASDAQ_HTML),
        "alpaca_calendar": None,
        "alpaca_calendar_window": None,
        "spy_bar_dates": None,
        "spy_bar_window": None,
    }
    sources.update(extra)
    return sources


def two_source(calendar_dates, bar_dates, early=()):
    rows = {day: {"open": "09:30", "close": "13:00" if day in early else "16:00"} for day in calendar_dates}
    return official_sources(
        alpaca_calendar=rows,
        alpaca_calendar_window=["2018-01-01", "2026-09-14"],
        spy_bar_dates=set(bar_dates),
        spy_bar_window=["2018-01-01", "2026-09-14"],
    )


class ClassificationTest(unittest.TestCase):
    def test_official_year_statuses(self):
        sources = official_sources()
        status = {day: CAL.classify_date(dt.date.fromisoformat(day), sources)["status"] for day in (
            "2026-09-07", "2026-11-27", "2026-09-14", "2026-09-12",
        )}
        self.assertEqual(status, {
            "2026-09-07": "CLOSED", "2026-11-27": "OPEN_EARLY_CLOSE",
            "2026-09-14": "OPEN_REGULAR", "2026-09-12": "CLOSED",
        })
        row = CAL.classify_date(dt.date(2026, 9, 14), sources)
        self.assertEqual(row["attesting_sources"], [CAL.NYSE_SOURCE_ID, CAL.NASDAQ_SOURCE_ID])

    def test_nasdaq_disagreement_is_unknown(self):
        nasdaq = CAL.parse_nasdaq_page(NASDAQ_HTML.replace(b"September 7, 2026", b"September 8, 2026"))
        sources = official_sources(nasdaq=nasdaq)
        for day in (dt.date(2026, 9, 7), dt.date(2026, 9, 8)):
            row = CAL.classify_date(day, sources)
            self.assertEqual((row["status"], row["reason"]), ("UNKNOWN", "NYSE_NASDAQ_CONFLICT"))
            self.assertEqual(row["result_code"], "US_FINISHED_SESSION_UNKNOWN")

    def test_alpaca_calendar_disagreement_in_official_year_is_unknown(self):
        sources = official_sources(
            alpaca_calendar={"2026-09-07": {"open": "09:30", "close": "16:00"}},
            alpaca_calendar_window=["2026-09-01", "2026-09-10"],
        )
        row = CAL.classify_date(dt.date(2026, 9, 7), sources)
        self.assertEqual(row["reason"], "NYSE_ALPACA_CALENDAR_CONFLICT")

    def test_historical_requires_both_sources(self):
        sources = two_source(["2019-07-03", "2019-07-05", "2019-07-08"], ["2019-07-03", "2019-07-08", "2019-07-09"], early={"2019-07-03"})
        got = {day: CAL.classify_date(dt.date.fromisoformat(day), sources) for day in (
            "2019-07-03", "2019-07-04", "2019-07-05", "2019-07-08", "2019-07-09",
        )}
        self.assertEqual(got["2019-07-03"]["status"], "OPEN_EARLY_CLOSE")
        self.assertEqual(got["2019-07-04"]["status"], "CLOSED")  # neither lists it
        self.assertEqual((got["2019-07-05"]["status"], got["2019-07-05"]["reason"]), ("UNKNOWN", "SINGLE_SOURCE_ONLY"))
        self.assertEqual(got["2019-07-08"]["status"], "OPEN_REGULAR")
        self.assertEqual((got["2019-07-09"]["status"], got["2019-07-09"]["reason"]), ("UNKNOWN", "SINGLE_SOURCE_ONLY"))

    def test_no_weekday_inference_without_sources(self):
        # A plain Wednesday with no historical capture stays UNKNOWN.
        row = CAL.classify_date(dt.date(2019, 7, 10), official_sources())
        self.assertEqual((row["status"], row["reason"]), ("UNKNOWN", "TWO_SOURCE_CAPTURE_MISSING"))
        no_nyse = official_sources(nyse=None)
        self.assertEqual(CAL.classify_date(dt.date(2026, 9, 14), no_nyse)["status"], "UNKNOWN")

    def test_outside_capture_window_and_before_2018_are_unknown(self):
        sources = two_source(["2017-12-29"], ["2017-12-29"])
        self.assertEqual(CAL.classify_date(dt.date(2017, 12, 29), sources)["reason"], "OUTSIDE_RATIFIED_SOURCE_SCOPE")
        sources["spy_bar_window"] = ["2019-01-01", "2026-09-14"]
        self.assertEqual(CAL.classify_date(dt.date(2018, 5, 1), sources)["reason"], "TWO_SOURCE_CAPTURE_MISSING")

    def test_unrecognized_alpaca_hours_are_unknown(self):
        sources = two_source(["2019-07-08"], ["2019-07-08"])
        sources["alpaca_calendar"]["2019-07-08"]["close"] = "14:00"
        self.assertEqual(CAL.classify_date(dt.date(2019, 7, 8), sources)["reason"], "ALPACA_CALENDAR_HOURS_UNRECOGNIZED")

    def test_completed_sessions_respect_dst_close(self):
        sources = official_sources()
        days = CAL.build_day_calendar(dt.date(2026, 9, 10), dt.date(2026, 9, 14), sources)
        before_close = dt.datetime(2026, 9, 14, 19, 59, tzinfo=dt.timezone.utc)
        at_close = dt.datetime(2026, 9, 14, 20, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(CAL.completed_sessions(days, before_close), ["2026-09-10", "2026-09-11"])
        self.assertEqual(CAL.completed_sessions(days, at_close), ["2026-09-10", "2026-09-11", "2026-09-14"])


class ConsensusPacketTest(unittest.TestCase):
    def _metas(self):
        nyse_capture = CAL.capture_page(CAL.NYSE_SOURCE_ID, opener=page_opener(NYSE_HTML, CAL.NYSE_URL), clock=fixed_clock)
        nasdaq_capture = CAL.capture_page(CAL.NASDAQ_SOURCE_ID, opener=page_opener(NASDAQ_HTML, CAL.NASDAQ_URL), clock=fixed_clock)
        return {
            CAL.NYSE_SOURCE_ID: CAL.capture_meta(*CAL.validate_capture(nyse_capture, CAL.NYSE_SOURCE_ID)),
            CAL.NASDAQ_SOURCE_ID: CAL.capture_meta(*CAL.validate_capture(nasdaq_capture, CAL.NASDAQ_SOURCE_ID)),
            CAL.ALPACA_CALENDAR_SOURCE_ID: {"source_url": CAL.ALPACA_CALENDAR_URLS[0], "observed_at": "2026-09-14T21:00:00Z", "captured_at": "2026-09-14T21:00:00Z", "source_sha256": "a" * 64},
            CAL.ALPACA_SPY_BAR_SOURCE_ID: {"source_url": CAL.ALPACA_BARS_URL, "observed_at": "2026-09-14T21:00:00Z", "captured_at": "2026-09-14T21:00:00Z", "source_sha256": "b" * 64},
        }

    def test_official_packet_is_receipt_admissible(self):
        contract = RECEIPT.load_contract()
        evaluated = dt.datetime(2026, 9, 14, 22, 0, tzinfo=dt.timezone.utc)
        for day, expected in (("2026-09-14", "OPEN_REGULAR"), ("2026-11-27", "OPEN_EARLY_CLOSE"), ("2026-09-07", "CLOSED")):
            row = CAL.classify_date(dt.date.fromisoformat(day), official_sources())
            packet = CAL.build_consensus_packet(row, self._metas())
            calendar = RECEIPT._calendar_from_bundle(packet, evaluated, contract)
            self.assertEqual(calendar["status"], expected)
        tampered = copy.deepcopy(packet)
        tampered["status"] = "OPEN_REGULAR"
        with self.assertRaisesRegex(RECEIPT.UsNaturalSessionError, "CALENDAR_BUNDLE_SHA_MISMATCH"):
            RECEIPT._calendar_from_bundle(tampered, evaluated, contract)

    def test_historical_packet_is_well_formed_but_not_a_natural_receipt_source(self):
        row = CAL.classify_date(dt.date(2019, 7, 8), two_source(["2019-07-08"], ["2019-07-08"]))
        packet = CAL.build_consensus_packet(row, self._metas())
        self.assertEqual(packet["schema_version"], "us_official_calendar_consensus/1")
        self.assertEqual([s["source_id"] for s in packet["sources"]], [CAL.ALPACA_CALENDAR_SOURCE_ID, CAL.ALPACA_SPY_BAR_SOURCE_ID])
        with self.assertRaises(RECEIPT.UsNaturalSessionError):
            RECEIPT._calendar_from_bundle(packet, dt.datetime(2026, 9, 14, 22, 0, tzinfo=dt.timezone.utc), RECEIPT.load_contract())

    def test_unknown_date_has_no_packet_source(self):
        row = CAL.classify_date(dt.date(2019, 7, 10), official_sources())
        with self.assertRaisesRegex(CAL.UsSessionCalendarError, "NO_ATTESTING_SOURCE"):
            CAL.build_consensus_packet(row, self._metas())


if __name__ == "__main__":
    unittest.main()
