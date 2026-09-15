#!/usr/bin/env python3
"""Offline regression for universe/us_listing_lookup.py.

No network. All directory-scan/packet-read tests use small synthetic
fixture packets under a temp directory shaped like
``data/observations/us_global_universe/<date>/packet.json`` -- never the
real ~74MB committed packets (those are exercised separately, directly
against the real files, in the "real committed source" test class below).

Covers: point-in-time packet selection (never a later packet than the
as-of instant, correctly picks the nearest earlier one across a gap, no
packet before the archive's first capture); the INSTANT guard -- a
same-day packet whose own ``as_of_utc`` is after the decision instant is
never used, even though its directory date qualifies on a date-only
basis, and the search correctly falls back to the nearest earlier
qualifying packet; a symbol absent from the selected packet; a confirmed
Nasdaq "Test Issue" symbol (the source's own analogue of a confirmed
exclusion -- see module docstring for why this source structurally cannot
produce a positive "OTC" signal directly); an unrecognized source_name
being ignored rather than trusted; and that the real committed producer
(universe/us_global_universe.py and its workflow) is untouched.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "us_listing_lookup", ROOT / "universe" / "us_listing_lookup.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

UTC = dt.timezone.utc


def _instant(date: str, time: str = "23:59:59") -> dt.datetime:
    """A tz-aware instant on ``date`` (default: the last second of that
    UTC day) -- convenience for tests that don't care about the exact
    time, only that it is safely on/after a given calendar date."""
    return dt.datetime.fromisoformat(f"{date}T{time}+00:00")


def _row(symbol: str, source_name: str = "other_listed", test_issue: str = "N", exchange: str = "P") -> dict:
    return {
        "asset_id": f"US:TEST:{symbol}",
        "primary_symbol": symbol,
        "source_name": source_name,
        "fields": {
            "ACT Symbol": symbol,
            "Exchange": exchange,
            "ETF": "Y",
            "Test Issue": test_issue,
            "Security Name": f"{symbol} Test Fixture",
        },
    }


def _write_packet(observations_dir: Path, date: str, rows: list[dict], as_of_utc: str | None = None) -> Path:
    packet_dir = observations_dir / date
    packet_dir.mkdir(parents=True)
    path = packet_dir / "packet.json"
    path.write_text(
        json.dumps({
            "packet": {
                "as_of_date": date,
                "as_of_utc": as_of_utc or f"{date}T23:59:59Z",
                "source_attribute_rows": rows,
            }
        }),
        encoding="utf-8",
    )
    return path


class PointInTimeSelectionTests(unittest.TestCase):
    def test_picks_the_exact_date_when_a_packet_exists_for_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY")])
            _write_packet(obs, "2026-09-11", [_row("SPY")])
            picked = M.find_latest_packet_date(dt.date(2026, 9, 11), root=root)
            self.assertEqual(picked, dt.date(2026, 9, 11))

    def test_never_picks_a_later_packet_than_the_as_of_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY")])
            _write_packet(obs, "2026-09-20", [_row("SPY")])  # future relative to as-of
            picked = M.find_latest_packet_date(dt.date(2026, 9, 11), root=root)
            self.assertEqual(picked, dt.date(2026, 9, 4))  # NOT 09-20

    def test_picks_the_nearest_earlier_packet_across_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY")])
            _write_packet(obs, "2026-09-08", [_row("SPY")])
            picked = M.find_latest_packet_date(dt.date(2026, 9, 6), root=root)  # weekend gap
            self.assertEqual(picked, dt.date(2026, 9, 4))

    def test_no_packet_before_the_archives_first_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY")])
            picked = M.find_latest_packet_date(dt.date(2026, 8, 1), root=root)
            self.assertIsNone(picked)

    def test_missing_observations_directory_is_no_packet_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)  # no data/observations/us_global_universe at all
            picked = M.find_latest_packet_date(dt.date(2026, 9, 11), root=root)
            self.assertIsNone(picked)

    def test_non_date_directories_and_missing_packet_json_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY")])
            (obs / "not-a-date").mkdir(parents=True)
            (obs / "2026-09-09").mkdir(parents=True)  # no packet.json inside
            picked = M.find_latest_packet_date(dt.date(2026, 9, 11), root=root)
            self.assertEqual(picked, dt.date(2026, 9, 4))

    def test_resolve_listing_end_to_end_pit_correct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY", test_issue="N")])
            _write_packet(obs, "2026-09-11", [_row("SPY", test_issue="Y")])  # different content on purpose
            result = M.resolve_listing(["SPY"], _instant("2026-09-06"), root=root)  # between the two dates
            self.assertEqual(result["packet_date"], "2026-09-04")
            self.assertEqual(result["per_symbol"]["SPY"]["status"], M.EXCHANGE_LISTED)  # the 09-04 packet's content
            self.assertEqual(result["listing_packet_age_days"], 2)

    def test_naive_instant_fails_closed(self):
        with self.assertRaisesRegex(M.UsListingLookupError, "AS_OF_INSTANT_NOT_TIMEZONE_AWARE"):
            M.resolve_listing(["SPY"], dt.datetime(2026, 9, 11))  # no tzinfo


class InstantGuardTests(unittest.TestCase):
    """Directory-date granularity alone is not enough: a same-day packet
    captured LATER that day than the decision instant must never be used.
    """

    def test_same_day_packet_captured_after_the_decision_instant_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            # Mirrors the real producer: as_of_date 09-11, but the capture
            # instant itself lands after 09-11 evening.
            _write_packet(obs, "2026-09-11", [_row("SPY", test_issue="Y")], as_of_utc="2026-09-12T01:30:00Z")
            _write_packet(obs, "2026-09-04", [_row("SPY", test_issue="N")], as_of_utc="2026-09-05T01:30:00Z")
            decision_instant = _instant("2026-09-11", "20:00:00")  # before the 09-11 packet's own capture
            result = M.resolve_listing(["SPY"], decision_instant, root=root)
            self.assertEqual(result["packet_date"], "2026-09-04")  # NOT 09-11
            self.assertEqual(result["per_symbol"]["SPY"]["status"], M.EXCHANGE_LISTED)  # the 09-04 content

    def test_same_day_packet_captured_before_the_decision_instant_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-11", [_row("SPY", test_issue="Y")], as_of_utc="2026-09-11T14:00:00Z")
            decision_instant = _instant("2026-09-11", "20:00:00")  # after the packet's own capture instant
            result = M.resolve_listing(["SPY"], decision_instant, root=root)
            self.assertEqual(result["packet_date"], "2026-09-11")
            self.assertEqual(result["per_symbol"]["SPY"]["status"], M.TEST_ISSUE)

    def test_captured_exactly_at_the_decision_instant_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-11", [_row("SPY")], as_of_utc="2026-09-11T20:00:00Z")
            result = M.resolve_listing(["SPY"], _instant("2026-09-11", "20:00:00"), root=root)
            self.assertEqual(result["packet_date"], "2026-09-11")

    def test_falls_back_across_multiple_disqualified_same_or_later_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY", test_issue="N")], as_of_utc="2026-09-05T01:00:00Z")
            _write_packet(obs, "2026-09-08", [_row("SPY", test_issue="Y")], as_of_utc="2026-09-09T01:00:00Z")
            _write_packet(obs, "2026-09-09", [_row("SPY", test_issue="Y")], as_of_utc="2026-09-10T01:00:00Z")
            # decision instant is early on 09-09 itself, before both the
            # 09-08 packet's own capture (09-09T01:00Z) and the 09-09
            # packet's own capture (09-10T01:00Z).
            decision_instant = _instant("2026-09-09", "00:30:00")
            result = M.resolve_listing(["SPY"], decision_instant, root=root)
            self.assertEqual(result["packet_date"], "2026-09-04")
            self.assertEqual(result["per_symbol"]["SPY"]["status"], M.EXCHANGE_LISTED)

    def test_all_candidates_disqualified_by_instant_is_no_packet_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-04", [_row("SPY")], as_of_utc="2026-09-04T23:00:00Z")
            decision_instant = _instant("2026-09-04", "01:00:00")  # before that packet's own capture
            result = M.resolve_listing(["SPY"], decision_instant, root=root)
            self.assertIsNone(result["packet_date"])
            self.assertEqual(result["per_symbol"]["SPY"]["reasons"], ["NO_LISTING_PACKET_AVAILABLE"])

    def test_missing_as_of_utc_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_dir = root / "data" / "observations" / "us_global_universe" / "2026-09-11"
            packet_dir.mkdir(parents=True)
            (packet_dir / "packet.json").write_text(
                json.dumps({"packet": {"as_of_date": "2026-09-11", "source_attribute_rows": [_row("SPY")]}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(M.UsListingLookupError, "LISTING_PACKET_AS_OF_UTC_MISSING"):
                M.resolve_listing(["SPY"], _instant("2026-09-11"), root=root)

    def test_malformed_as_of_utc_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet_dir = root / "data" / "observations" / "us_global_universe" / "2026-09-11"
            packet_dir.mkdir(parents=True)
            (packet_dir / "packet.json").write_text(
                json.dumps({"packet": {"as_of_utc": "not-a-timestamp", "source_attribute_rows": [_row("SPY")]}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(M.UsListingLookupError, "LISTING_PACKET_AS_OF_UTC_INVALID"):
                M.resolve_listing(["SPY"], _instant("2026-09-11"), root=root)


class ClassificationTests(unittest.TestCase):
    def test_symbol_absent_from_packet_is_unresolved_not_otc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-11", [_row("SPY")])
            result = M.resolve_listing(["SPY", "NOTPRESENT"], _instant("2026-09-11"), root=root)
            row = result["per_symbol"]["NOTPRESENT"]
            self.assertIsNone(row["status"])
            self.assertNotEqual(row["status"], M.OTC)  # absence is UNKNOWN, never assumed OTC
            self.assertEqual(row["reasons"], ["SYMBOL_ABSENT_FROM_LISTING_PACKET"])

    def test_confirmed_test_issue_symbol_is_the_sources_own_confirmed_exclusion(self):
        # Both nasdaqlisted.txt and otherlisted.txt are exchange-listed-only
        # directories -- this source structurally cannot assert "OTC"
        # directly (see module docstring). A confirmed Test Issue=Y row is
        # the source's own equivalent confident, evidence-backed exclusion.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-11", [_row("ZAZZT", source_name="nasdaq_listed", test_issue="Y")])
            result = M.resolve_listing(["ZAZZT"], _instant("2026-09-11"), root=root)
            row = result["per_symbol"]["ZAZZT"]
            self.assertEqual(row["status"], M.TEST_ISSUE)
            self.assertEqual(row["reasons"], ["CONFIRMED_TEST_ISSUE"])

    def test_exchange_listed_symbol_from_either_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-11", [
                _row("SPY", source_name="other_listed"),
                _row("MSFT", source_name="nasdaq_listed"),
            ])
            result = M.resolve_listing(["SPY", "MSFT"], _instant("2026-09-11"), root=root)
            self.assertEqual(result["per_symbol"]["SPY"]["status"], M.EXCHANGE_LISTED)
            self.assertEqual(result["per_symbol"]["MSFT"]["status"], M.EXCHANGE_LISTED)

    def test_unrecognized_source_name_is_ignored_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            _write_packet(obs, "2026-09-11", [_row("FAKE", source_name="some_other_file")])
            result = M.resolve_listing(["FAKE"], _instant("2026-09-11"), root=root)
            row = result["per_symbol"]["FAKE"]
            self.assertIsNone(row["status"])  # not EXCHANGE_LISTED just because a row exists
            self.assertEqual(row["reasons"], ["SYMBOL_ABSENT_FROM_LISTING_PACKET"])

    def test_no_packet_available_is_unknown_for_every_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = M.resolve_listing(["SPY", "MSFT"], _instant("2026-09-11"), root=root)
            self.assertIsNone(result["packet_date"])
            self.assertIsNone(result["packet_sha256"])
            self.assertIsNone(result["listing_packet_age_days"])
            for symbol in ("SPY", "MSFT"):
                self.assertIsNone(result["per_symbol"][symbol]["status"])
                self.assertEqual(result["per_symbol"][symbol]["reasons"], ["NO_LISTING_PACKET_AVAILABLE"])

    def test_packet_sha256_matches_the_bytes_actually_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe"
            path = _write_packet(obs, "2026-09-11", [_row("SPY")])
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            result = M.resolve_listing(["SPY"], _instant("2026-09-11"), root=root)
            self.assertEqual(result["packet_sha256"], expected)

    def test_malformed_packet_shape_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obs = root / "data" / "observations" / "us_global_universe" / "2026-09-11"
            obs.mkdir(parents=True)
            (obs / "packet.json").write_text(json.dumps({"packet": {"no_rows_here": True}}), encoding="utf-8")
            with self.assertRaisesRegex(M.UsListingLookupError, "LISTING_PACKET_SHAPE_INVALID"):
                M.resolve_listing(["SPY"], _instant("2026-09-11"), root=root)


class RealCommittedSourceTests(unittest.TestCase):
    """Against the real, already-committed producer output -- not a mock."""

    OBS_DIR = ROOT / "data" / "observations" / "us_global_universe"

    def test_real_capture_directory_exists_with_at_least_one_dated_packet(self):
        if not self.OBS_DIR.is_dir():
            self.skipTest("data/observations/us_global_universe not materialized in this checkout")
        dated = sorted(p.name for p in self.OBS_DIR.iterdir() if p.is_dir())
        self.assertTrue(dated)

    def test_real_latest_packet_resolves_all_22_approved_symbols(self):
        if not self.OBS_DIR.is_dir():
            self.skipTest("data/observations/us_global_universe not materialized in this checkout")
        dated = sorted((p.name for p in self.OBS_DIR.iterdir() if p.is_dir() and (p / "packet.json").is_file()))
        if not dated:
            self.skipTest("no dated packet.json present in this checkout")
        latest = dt.date.fromisoformat(dated[-1])
        contract_path = ROOT / "config" / "free_market_data_contract.json"
        symbols = json.loads(contract_path.read_text(encoding="utf-8"))["alpaca"]["symbols"]
        # The real producer's own as_of_utc for a given as_of_date routinely
        # falls on the NEXT calendar day (see module docstring) -- read it
        # directly and use a decision instant safely after it, rather than
        # assuming end-of-day on the directory date would qualify.
        real_as_of_utc = M.load_listing_rows(M.packet_path_for_date(latest, root=ROOT), symbols)["as_of_utc"]
        decision_instant = real_as_of_utc + dt.timedelta(minutes=1)
        result = M.resolve_listing(symbols, decision_instant, root=ROOT)
        self.assertEqual(result["packet_date"], latest.isoformat())
        for symbol in symbols:
            row = result["per_symbol"][symbol]
            self.assertEqual(row["status"], M.EXCHANGE_LISTED, msg=f"{symbol}: {row}")

    def test_real_packets_own_as_of_utc_is_readable_and_timezone_aware(self):
        if not self.OBS_DIR.is_dir():
            self.skipTest("data/observations/us_global_universe not materialized in this checkout")
        dated = sorted((p.name for p in self.OBS_DIR.iterdir() if p.is_dir() and (p / "packet.json").is_file()))
        if not dated:
            self.skipTest("no dated packet.json present in this checkout")
        latest = dt.date.fromisoformat(dated[-1])
        as_of_utc = M.load_listing_rows(M.packet_path_for_date(latest, root=ROOT), ["SPY"])["as_of_utc"]
        self.assertIsNotNone(as_of_utc.tzinfo)


class ProducerUntouchedTests(unittest.TestCase):
    """This module reads the packet; it must never edit the producer."""

    def test_producer_module_contract_and_workflow_are_not_imported_for_writing(self):
        text = (ROOT / "universe" / "us_listing_lookup.py").read_text(encoding="utf-8")
        self.assertNotIn("import universe.us_global_universe", text)
        self.assertNotIn("from universe import us_global_universe", text)
        self.assertNotIn("write_text", text)
        self.assertNotIn("open(", text)


if __name__ == "__main__":
    unittest.main()
