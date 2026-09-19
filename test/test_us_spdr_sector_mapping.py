#!/usr/bin/env python3
"""RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1 reader regressions --
built directly on collectors/spdr_sector_holdings.py capture batches (no
network, no live SSGA fetch)."""
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


def fixture_workbook_with_as_of(as_of_text: str, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([as_of_text])
    ws.append(["Ticker", "Name", "Weight (%)"])
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_and_publish(root, per_ticker_rows, *, captured_at=NOW, tickers=None):
    """Build and publish one capture batch.

    ``per_ticker_rows``: {ticker: [(symbol, name, weight_pct), ...]}.
    ``tickers``: the full set of tickers this batch attempts to cover
    (default: all 11 -- a complete batch). Pass a smaller set to build a
    deliberately incomplete batch. Any ticker in ``tickers`` without an
    entry in ``per_ticker_rows`` gets a harmless placeholder holding so
    callers only need to spell out the tickers they actually care about.
    """
    tickers = list(C.SECTOR_ETFS) if tickers is None else tickers
    raw = {
        ticker: fixture_workbook(per_ticker_rows.get(ticker, [("PLACEHOLDER", "PLACEHOLDER CO", 0.01)]))
        for ticker in tickers
    }
    batch = C.build_batch(captured_at, raw)
    C.publish_batch(root, batch)
    return batch


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
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]})
            result = R.sector_for_symbol(root, "nvda", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["sector_etf"], "XLK")
            self.assertEqual(result["basis"], "SOLE_HOLDER")
            self.assertEqual(result["holder_etf_count"], 1)
            self.assertFalse(result["tie"])

    def test_multi_holder_uses_exact_weight_not_within_fund_rank(self):
        # The bug PR #761 fixed: a rank-1-in-its-own-fund holding is NOT
        # necessarily heavier than a rank-2 holding in a different fund.
        # XLK's NVDA is rank 1 in XLK (6.0, sole holding there); XLC's NVDA
        # is rank 2 in XLC (8.5, behind META's 9.0). The true larger weight
        # is XLC's 8.5 -- a bucket+rank approximation would have picked
        # XLK instead (rank 1 beats rank 2 on a same-bucket tie).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {
                "XLK": [("NVDA", "NVIDIA", 6.0)],
                "XLC": [("META", "META", 9.0), ("NVDA", "NVIDIA", 8.5)],
            })
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            self.assertEqual(result["sector_etf"], "XLC")
            self.assertEqual(result["basis"], "LARGEST_WEIGHT_ETF")
            self.assertEqual(result["holder_etf_count"], 2)
            self.assertFalse(result["tie"])

    def test_exact_tie_sets_tie_flag_and_breaks_alphabetically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {
                "XLK": [("DUP", "DUP CO", 5.0)],
                "XLC": [("DUP", "DUP CO", 5.0)],
            })
            result = R.sector_for_symbol(root, "DUP", "2026-09-15T12:00:00Z")
            self.assertEqual(result["sector_etf"], "XLC")  # alphabetically first on an exact tie
            self.assertTrue(result["tie"])
            self.assertEqual(result["holder_etf_count"], 2)

    def test_unheld_symbol_is_unknown_no_t2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]})
            result = R.sector_for_symbol(root, "MSFT", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "UNKNOWN_NO_T2")
            self.assertIsNone(result["sector_etf"])

    def test_no_capture_before_decision_is_distinct_from_unheld(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]}, captured_at=NOW)
            result = R.sector_for_symbol(root, "NVDA", "2026-09-14T00:00:00Z")
            self.assertEqual(result["status"], "NO_POINT_IN_TIME_CAPTURE_AVAILABLE")
            self.assertFalse(result["incomplete_batches_present"])

    def test_incomplete_batch_is_never_used_falls_back_to_previous_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = dt.datetime(2026, 9, 10, 6, 0, 0, tzinfo=dt.timezone.utc)
            # Day 1: complete batch, NVDA held by XLK.
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]}, captured_at=older)
            # Day 2: only XLC fetched successfully -- an incomplete batch
            # that, if trusted, would wrongly answer "not held" for NVDA.
            build_and_publish(
                root, {"XLC": [("META", "META", 9.0)]}, captured_at=NOW, tickers=["XLC"],
            )
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["sector_etf"], "XLK")
            self.assertEqual(result["as_of_capture_date_utc"], "2026-09-10")

    def test_only_incomplete_batches_reports_incomplete_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {"XLC": [("META", "META", 9.0)]}, tickers=["XLC"])
            result = R.sector_for_symbol(root, "META", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "NO_POINT_IN_TIME_CAPTURE_AVAILABLE")
            self.assertTrue(result["incomplete_batches_present"])

    def test_only_the_latest_complete_capture_day_before_decision_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = dt.datetime(2026, 9, 10, 6, 0, 0, tzinfo=dt.timezone.utc)
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]}, captured_at=older)
            build_and_publish(root, {"XLK": [("AAPL", "APPLE", 4.2)]}, captured_at=NOW)
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            # NVDA was only held on the older day; the latest complete
            # capture day (2026-09-15) does not hold it -> unknown, not
            # stale-but-held.
            self.assertEqual(result["status"], "UNKNOWN_NO_T2")
            self.assertEqual(result["as_of_capture_date_utc"], "2026-09-15")

    def test_symbol_is_case_and_whitespace_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]})
            result = R.sector_for_symbol(root, "  nvda  ", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "OK")


class HoldingsAsOfDateExposureTests(unittest.TestCase):
    """PR #765 follow-up: a 22:00 UTC capture may still reflect the prior
    trading day's file -- this reader must expose that, never assume
    same-day freshness."""

    def _publish_with_as_of(self, root, per_ticker_as_of, *, captured_at=NOW):
        raw = {
            ticker: fixture_workbook_with_as_of(as_of, [("NVDA" if ticker == "XLK" else "META", "X", 8.5)])
            for ticker, as_of in per_ticker_as_of.items()
        }
        for ticker in C.SECTOR_ETFS:
            raw.setdefault(ticker, fixture_workbook([("PLACEHOLDER", "PLACEHOLDER CO", 0.01)]))
        batch = C.build_batch(captured_at, raw)
        C.publish_batch(root, batch)

    def test_ok_result_exposes_the_winning_etfs_own_as_of_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._publish_with_as_of(root, {"XLK": "Holdings are as of 09/12/2026"})
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["sector_etf"], "XLK")
            self.assertEqual(result["holdings_as_of_date"], "2026-09-12")

    def test_ok_result_is_unknown_when_the_winning_etfs_file_had_no_as_of_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]})  # no as-of preamble
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            self.assertEqual(result["holdings_as_of_date"], R.HOLDINGS_AS_OF_UNKNOWN)

    def test_unheld_result_exposes_the_batch_level_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]})
            result = R.sector_for_symbol(root, "MSFT", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "UNKNOWN_NO_T2")
            self.assertIn("holdings_as_of_date", result)

    def test_manifest_from_before_this_field_existed_is_tolerated_not_crashed(self):
        # Simulate a manifest committed by the pre-#765-follow-up schema
        # (no holdings_as_of_date/holdings_as_of_dates keys at all).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_and_publish(root, {"XLK": [("NVDA", "NVIDIA", 8.5)]})
            import json
            manifest_path = root / C.EVIDENCE_ROOT / "resolved" / "2026-09-15" / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            del manifest["holdings_as_of_date"]
            del manifest["holdings_as_of_dates"]
            manifest_path.write_text(json.dumps(manifest))
            result = R.sector_for_symbol(root, "NVDA", "2026-09-15T12:00:00Z")
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["holdings_as_of_date"], R.HOLDINGS_AS_OF_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
