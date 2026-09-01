#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "us_natural_session_receipt", ROOT / "market_data" / "us_natural_session_receipt.py"
)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
NY = ZoneInfo("America/New_York")
SHA = "3" * 64


def z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def calendar_bundle(day: str, status: str = "OPEN_REGULAR") -> dict:
    date = dt.date.fromisoformat(day)
    open_local = dt.datetime.combine(date, dt.time(9, 30), NY)
    close_time = dt.time(13) if status == "OPEN_EARLY_CLOSE" else dt.time(16)
    close_local = dt.datetime.combine(date, close_time, NY)
    opened = open_local.isoformat(timespec="seconds") if status.startswith("OPEN_") else None
    closed = close_local.isoformat(timespec="seconds") if status.startswith("OPEN_") else None
    captured = z(dt.datetime.combine(date, dt.time(8), NY))
    urls = {
        "NYSE_TRADING_HOURS_CALENDAR": "https://www.nyse.com/trade/hours-calendars",
        "NASDAQ_STOCK_MARKET_HOLIDAY_SCHEDULE": "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule",
    }
    value = {
        "schema_version": "us_official_calendar_consensus/1",
        "session_date": day,
        "timezone": "America/New_York",
        "status": status,
        "open_at": opened,
        "close_at": closed,
        "captured_at": captured,
        "sources": [
            {
                "source_id": source_id,
                "source_url": source_url,
                "source_record_id": f"synthetic-test-only:{day}:{source_id}",
                "session_date": day,
                "status": status,
                "open_at": opened,
                "close_at": closed,
                "observed_at": captured,
                "captured_at": captured,
                "source_sha256": SHA,
            }
            for source_id, source_url in urls.items()
        ],
    }
    value["bundle_sha256"] = MOD.payload_sha256(value)
    return value


def minute_capture(bundle: dict) -> dict:
    opened = dt.datetime.fromisoformat(bundle["open_at"])
    closed = dt.datetime.fromisoformat(bundle["close_at"])
    rows = []
    cursor = opened
    index = 0
    while cursor < closed:
        end = cursor + dt.timedelta(minutes=1)
        price = str(100 + index)
        rows.append(
            {
                "open_at": cursor.isoformat(timespec="seconds"),
                "close_at": end.isoformat(timespec="seconds"),
                "open": price,
                "high": str(101 + index),
                "low": price,
                "close": str(101 + index),
                "volume": "1000",
            }
        )
        cursor = end
        index += 1
    value = {
        "schema_version": "us_original_minute_capture/1",
        "evidence_class": "NATURAL_ORIGINAL",
        "capture_mode": "EXTERNAL_RESULT_INJECTED_READ_ONLY",
        "fixture": False,
        "session_date": bundle["session_date"],
        "asset_id": "US.NASDAQ.SPY",
        "symbol": "SPY",
        "provider_id": "SYNTHETIC_TEST_ONLY",
        "feed_scope": "IEX_ONLY",
        "observed_at": z(closed),
        "available_at": z(closed + dt.timedelta(minutes=1)),
        "source_ref": "memory://synthetic-test-only",
        "source_sha256": SHA,
        "redistribution_status": "NOT_GRANTED",
        "bars": rows,
    }
    value["capture_sha256"] = MOD.payload_sha256(value)
    return value


class ReceiptTests(unittest.TestCase):
    def test_absent_actual_inputs_are_wait_unknown_hold_and_mutation_zero(self):
        receipt = MOD.build_receipt(
            session_date="2026-09-01",
            evaluated_at_utc="2026-09-01T14:00:00Z",
            next_natural_observation_at_utc="2026-09-01T20:05:00Z",
            calendar_bundle=None,
            minute_capture=None,
        )
        self.assertEqual(receipt["gate1"]["status"], "UNKNOWN")
        self.assertEqual(receipt["gate2"]["status"], "HOLD")
        self.assertEqual(receipt["recommendation"], "WAIT")
        self.assertIsNone(receipt["gate2"]["numeric_ttl_seconds"])
        self.assertTrue(all(value == 0 for value in receipt["side_effects"].values()))
        self.assertFalse(receipt["authority"]["trading_authorized"])
        MOD.verify_receipt(receipt)

    def test_regular_finished_session_proves_gate1_only(self):
        bundle = calendar_bundle("2026-09-01")
        capture = minute_capture(bundle)
        receipt = MOD.build_receipt(
            session_date="2026-09-01",
            evaluated_at_utc="2026-09-01T20:05:00Z",
            next_natural_observation_at_utc="2026-09-02T20:05:00Z",
            calendar_bundle=bundle,
            minute_capture=capture,
        )
        self.assertEqual(receipt["gate1"]["status"], "PASS")
        self.assertEqual(receipt["gate2"]["status"], "HOLD")
        self.assertEqual(
            [(row["timeframe"], row["completed_interval_count"]) for row in receipt["completed_timeframes"]],
            [("15m", 26), ("1h", 6)],
        )
        self.assertNotIn("bars", MOD.canonical_json(receipt))

    def test_early_close_and_dst_are_iana_date_specific(self):
        bundle = calendar_bundle("2026-11-27", "OPEN_EARLY_CLOSE")
        capture = minute_capture(bundle)
        receipt = MOD.build_receipt(
            session_date="2026-11-27",
            evaluated_at_utc="2026-11-27T18:05:00Z",
            next_natural_observation_at_utc="2026-11-30T21:05:00Z",
            calendar_bundle=bundle,
            minute_capture=capture,
        )
        self.assertEqual(receipt["gate1"]["status"], "PASS")
        self.assertEqual(
            [row["completed_interval_count"] for row in receipt["completed_timeframes"]],
            [14, 3],
        )
        bad = calendar_bundle("2026-03-09")
        bad["open_at"] = "2026-03-09T09:30:00-05:00"
        for row in bad["sources"]:
            row["open_at"] = bad["open_at"]
        bad["bundle_sha256"] = MOD.payload_sha256(MOD._without_hash(bad, "bundle_sha256"))
        bad_receipt = MOD.build_receipt(
            session_date="2026-03-09",
            evaluated_at_utc="2026-03-09T20:05:00Z",
            next_natural_observation_at_utc="2026-03-10T20:05:00Z",
            calendar_bundle=bad,
            minute_capture=None,
        )
        self.assertEqual(bad_receipt["calendar"]["status"], "ABSENT")
        self.assertTrue(
            any("NOT_NEW_YORK_OFFSET" in reason for reason in bad_receipt["blockers"])
        )

    def test_partial_minute_capture_never_promotes_completed_series(self):
        bundle = calendar_bundle("2026-09-01")
        capture = minute_capture(bundle)
        capture["bars"].pop(10)
        capture["capture_sha256"] = MOD.payload_sha256(
            MOD._without_hash(capture, "capture_sha256")
        )
        receipt = MOD.build_receipt(
            session_date="2026-09-01",
            evaluated_at_utc="2026-09-01T20:05:00Z",
            next_natural_observation_at_utc="2026-09-02T20:05:00Z",
            calendar_bundle=bundle,
            minute_capture=capture,
        )
        self.assertEqual(receipt["gate1"]["status"], "UNKNOWN")
        self.assertTrue(any("SERIES_INCOMPLETE" in reason for reason in receipt["blockers"]))

    def test_out_of_session_minute_is_rejected_not_silently_ignored(self):
        bundle = calendar_bundle("2026-09-01")
        capture = minute_capture(bundle)
        extra = copy.deepcopy(capture["bars"][0])
        extra["open_at"] = "2026-09-01T09:29:00-04:00"
        extra["close_at"] = "2026-09-01T09:30:00-04:00"
        capture["bars"].append(extra)
        capture["capture_sha256"] = MOD.payload_sha256(
            MOD._without_hash(capture, "capture_sha256")
        )
        receipt = MOD.build_receipt(
            session_date="2026-09-01",
            evaluated_at_utc="2026-09-01T20:05:00Z",
            next_natural_observation_at_utc="2026-09-02T20:05:00Z",
            calendar_bundle=bundle,
            minute_capture=capture,
        )
        self.assertEqual(receipt["gate1"]["status"], "UNKNOWN")
        self.assertEqual(receipt["evidence_class"], "NATURAL_INPUT_PROVIDED_NOT_ADMITTED")
        self.assertTrue(
            any("OUTSIDE_REGULAR_SESSION" in reason for reason in receipt["blockers"])
        )

    def test_calendar_source_disagreement_and_holiday_fail_closed(self):
        bundle = calendar_bundle("2026-09-07", "CLOSED")
        receipt = MOD.build_receipt(
            session_date="2026-09-07",
            evaluated_at_utc="2026-09-07T20:05:00Z",
            next_natural_observation_at_utc="2026-09-08T20:05:00Z",
            calendar_bundle=bundle,
            minute_capture=None,
        )
        self.assertEqual(receipt["gate1"]["status"], "UNKNOWN")
        self.assertIn("OFFICIAL_CLOSED_SESSION_WAIT", receipt["blockers"])
        disputed = calendar_bundle("2026-09-01")
        disputed["sources"][0]["status"] = "CLOSED"
        disputed["bundle_sha256"] = MOD.payload_sha256(
            MOD._without_hash(disputed, "bundle_sha256")
        )
        receipt = MOD.build_receipt(
            session_date="2026-09-01",
            evaluated_at_utc="2026-09-01T20:05:00Z",
            next_natural_observation_at_utc="2026-09-02T20:05:00Z",
            calendar_bundle=disputed,
            minute_capture=None,
        )
        self.assertEqual(receipt["calendar"]["status"], "ABSENT")
        self.assertTrue(any("CONSENSUS_MISMATCH" in reason for reason in receipt["blockers"]))

    def test_cli_output_is_deterministic_and_verifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            receipt = MOD.build_receipt(
                session_date="2026-09-01",
                evaluated_at_utc="2026-09-01T14:00:00Z",
                next_natural_observation_at_utc="2026-09-01T20:05:00Z",
                calendar_bundle=None,
                minute_capture=None,
            )
            MOD._write(first, receipt)
            MOD._write(second, receipt)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            tampered = copy.deepcopy(receipt)
            tampered["recommendation"] = "PASS"
            with self.assertRaisesRegex(MOD.UsNaturalSessionError, "SHA_MISMATCH"):
                MOD.verify_receipt(tampered)


if __name__ == "__main__":
    unittest.main()
