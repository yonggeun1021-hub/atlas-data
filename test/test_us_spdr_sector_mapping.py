#!/usr/bin/env python3
"""RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1 reader regressions --
built directly on collectors/spdr_sector_holdings.py captures (no network,
no live SSGA fetch)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

import openpyxl

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load_module("spdr_sector_holdings_for_mapping_test", ROOT / "collectors" / "spdr_sector_holdings.py")
R = load_module("us_spdr_sector_mapping_under_test", ROOT / "universe" / "us_spdr_sector_mapping.py")

NOW = dt.datetime(2026, 9, 15, 6, 0, 0, tzinfo=dt.timezone.utc)


def fixture_workbook(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ticker", "Name", "Weight (%)"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def publish(root, ticker, rows, captured_at=NOW):
    bundle = C.build_capture(captured_at, ticker, fixture_workbook(rows))
    C.publish_capture(root, bundle)


class SectorMembershipTests(unittest.TestCase):
    def test_sector_etf_maps_to_its_own_sector(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = R.sector_for_symbol(root, "XLK", "2026-09-15T12:00:00Z")
            self.assertEqual(result, {
                "status": "OK", "symbol": "XLK", "sector_etf": "XLK", "basis": "OWN_SECTOR",
            })

    def test_sole_holder_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish(root, "XLK", [("NVDA", "NVIDIA", 8.5)])
            result = R.sector_for_symbol(root, "nvda", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["sector_etf"], "XLK")
            self.assertEqual(result["basis"], "SOLE_HOLDER")

    def test_multi_holder_picks_largest_weight_bucket_then_rank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish(root, "XLK", [("AAPL", "APPLE", 4.2), ("NVDA", "NVIDIA", 8.5)])
            publish(root, "XLC", [("META", "META", 9.0), ("NVDA", "NVIDIA", 2.0)])
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            self.assertEqual(result["sector_etf"], "XLK")  # GE_5PCT beats GE_1PCT_LT_5PCT
            self.assertEqual(result["basis"], "LARGEST_WEIGHT_ETF")
            self.assertEqual(result["held_by"], ["XLC", "XLK"])
            self.assertFalse(result["tie_broken_alphabetically"])

    def test_full_tie_breaks_alphabetically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Same bucket (GE_5PCT) and same rank (1st, sole holding) in both
            # funds -- a genuine full tie on the only signals this reader has.
            publish(root, "XLK", [("DUP", "DUP CO", 8.5)])
            publish(root, "XLC", [("DUP", "DUP CO", 8.5)])
            result = R.sector_for_symbol(root, "DUP", "2026-09-15T12:00:00Z")
            self.assertEqual(result["sector_etf"], "XLC")  # alphabetically first at rank1/GE_5PCT
            self.assertTrue(result["tie_broken_alphabetically"])

    def test_unheld_symbol_is_unknown_no_t2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish(root, "XLK", [("NVDA", "NVIDIA", 8.5)])
            result = R.sector_for_symbol(root, "MSFT", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "UNKNOWN_NO_T2")
            self.assertIsNone(result["sector_etf"])

    def test_no_capture_before_decision_is_distinct_from_unheld(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish(root, "XLK", [("NVDA", "NVIDIA", 8.5)], captured_at=NOW)
            result = R.sector_for_symbol(root, "NVDA", "2026-09-14T00:00:00Z")
            self.assertEqual(result["status"], "NO_POINT_IN_TIME_CAPTURE_AVAILABLE")

    def test_only_the_latest_capture_day_before_decision_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = dt.datetime(2026, 9, 10, 6, 0, 0, tzinfo=dt.timezone.utc)
            publish(root, "XLK", [("NVDA", "NVIDIA", 8.5)], captured_at=older)
            publish(root, "XLK", [("AAPL", "APPLE", 4.2)], captured_at=NOW)
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            # NVDA was only held on the older day; the latest capture day
            # (2026-09-15) does not hold it -> unknown, not stale-but-held.
            self.assertEqual(result["status"], "UNKNOWN_NO_T2")
            self.assertEqual(result["as_of_capture_date_utc"], "2026-09-15")

    def test_symbol_is_case_and_whitespace_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish(root, "XLK", [("NVDA", "NVIDIA", 8.5)])
            result = R.sector_for_symbol(root, "  nvda  ", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "OK")


if __name__ == "__main__":
    unittest.main()
