#!/usr/bin/env python3
"""SPDR sector holdings capture/publish regressions -- fixture .xlsx
workbook + fake HTTP layer only. No network call is ever made by this
file, and the real SSGA endpoint is never contacted."""
from __future__ import annotations

import datetime as dt
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "spdr_sector_holdings", ROOT / "collectors" / "spdr_sector_holdings.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

NOW = dt.datetime(2026, 9, 15, 6, 0, 0, tzinfo=dt.timezone.utc)


def fixture_workbook(rows: list[tuple], header=("Ticker", "Name", "Weight (%)")) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class SectorUniverseTests(unittest.TestCase):
    def test_universe_matches_repo_config(self):
        import json
        contract = json.loads((ROOT / "config" / "free_market_data_contract.json").read_text())
        reference = set(contract["alpaca"]["sector_reference_symbols"]) - {"SMH"}
        self.assertEqual(set(M.SECTOR_ETFS), reference)
        self.assertEqual(len(M.SECTOR_ETFS), 11)


class ParsingTests(unittest.TestCase):
    def test_parses_symbol_and_weight_and_skips_cash(self):
        raw = fixture_workbook([
            ("NVDA", "NVIDIA CORP", 8.5),
            ("AAPL", "APPLE INC", 4.2),
            ("CASH", "CASH", 0.1),
        ])
        holdings = M.parse_holdings_workbook(raw)
        self.assertEqual({h["symbol"] for h in holdings}, {"NVDA", "AAPL"})

    def test_tolerates_alternate_header_names(self):
        raw = fixture_workbook(
            [("MSFT", "MICROSOFT", "6.10%")],
            header=("Identifier", "Security Description", "Weight"),
        )
        holdings = M.parse_holdings_workbook(raw)
        self.assertEqual(holdings[0]["symbol"], "MSFT")
        self.assertAlmostEqual(holdings[0]["weight_pct"], 6.10)

    def test_leading_junk_rows_before_header_are_skipped(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["SPDR Select Sector Fund - XLK", None, None])
        ws.append(["As of 2026-09-14", None, None])
        ws.append(["Ticker", "Name", "Weight (%)"])
        ws.append(["NVDA", "NVIDIA", 8.5])
        buf = io.BytesIO()
        wb.save(buf)
        holdings = M.parse_holdings_workbook(buf.getvalue())
        self.assertEqual(holdings[0]["symbol"], "NVDA")

    def test_unreadable_bytes_fail_closed(self):
        with self.assertRaisesRegex(M.SpdrSectorHoldingsError, "HOLDINGS_WORKBOOK_UNREADABLE"):
            M.parse_holdings_workbook(b"not an xlsx file")

    def test_no_header_found_fails_closed(self):
        raw = fixture_workbook([], header=("Foo", "Bar", "Baz"))
        with self.assertRaisesRegex(M.SpdrSectorHoldingsError, "HOLDINGS_HEADER_NOT_FOUND"):
            M.parse_holdings_workbook(raw)


class WeightBucketTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(M.weight_bucket(0.5), "LT_1PCT")
        self.assertEqual(M.weight_bucket(1.0), "GE_1PCT_LT_5PCT")
        self.assertEqual(M.weight_bucket(4.99), "GE_1PCT_LT_5PCT")
        self.assertEqual(M.weight_bucket(5.0), "GE_5PCT")


class FetchTests(unittest.TestCase):
    def test_fetch_holdings_uses_the_documented_url_template(self):
        calls = []
        def fake_getter(url, headers=None):
            calls.append(url)
            return fixture_workbook([("NVDA", "NVIDIA", 8.5)])
        M.fetch_holdings("XLK", getter=fake_getter)
        self.assertEqual(calls[0], M.holdings_url("XLK"))
        self.assertIn("holdings-daily-us-en-xlk.xlsx", calls[0])

    def test_fetch_rejects_ticker_outside_universe(self):
        with self.assertRaisesRegex(M.SpdrSectorHoldingsError, "HOLDINGS_TICKER_NOT_IN_UNIVERSE"):
            M.fetch_holdings("SMH", getter=lambda url, headers=None: b"")


class CaptureAndPublishTests(unittest.TestCase):
    def test_authority_is_all_false(self):
        raw = fixture_workbook([("NVDA", "NVIDIA", 8.5)])
        bundle = M.build_capture(NOW, "XLK", raw)
        for key, value in bundle["capture"]["authority"].items():
            if key.endswith("_authorized"):
                self.assertFalse(value, key)

    def test_derived_mapping_ranked_by_weight_descending(self):
        raw = fixture_workbook([
            ("AAPL", "APPLE", 4.2),
            ("NVDA", "NVIDIA", 8.5),
            ("AVGO", "BROADCOM", 4.2),
        ])
        bundle = M.build_capture(NOW, "XLK", raw)
        mapping = bundle["capture"]["mapping"]
        self.assertEqual(mapping[0]["symbol"], "NVDA")
        self.assertEqual(mapping[0]["weight_rank"], 1)
        self.assertEqual(mapping[0]["weight_bucket"], "GE_5PCT")

    def test_raw_workbook_bytes_are_never_stored_in_the_capture(self):
        raw = fixture_workbook([("NVDA", "NVIDIA", 8.5)])
        bundle = M.build_capture(NOW, "XLK", raw)
        serialized = bundle["capture_bytes"]
        self.assertNotIn(raw, serialized)
        self.assertIn(M.sha256_bytes(raw), bundle["capture"]["raw_sha256"])
        self.assertNotIn("openpyxl", str(bundle["capture"]))  # sanity: no binary leakage

    def test_publish_is_idempotent_and_content_addressed(self):
        raw = fixture_workbook([("NVDA", "NVIDIA", 8.5)])
        bundle = M.build_capture(NOW, "XLK", raw)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = M.publish_capture(root, bundle)
            self.assertTrue(first["created"])
            second = M.publish_capture(root, bundle)
            self.assertFalse(second["created"])
            self.assertEqual(first["capture_id"], second["capture_id"])

    def test_append_only_collision_fails_closed(self):
        raw = fixture_workbook([("NVDA", "NVIDIA", 8.5)])
        bundle = M.build_capture(NOW, "XLK", raw)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_capture(root, bundle)
            (root / bundle["capture_path"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(M.SpdrSectorHoldingsError, "APPEND_ONLY_COLLISION"):
                M.publish_capture(root, bundle)

    def test_path_traversal_is_rejected(self):
        raw = fixture_workbook([("NVDA", "NVIDIA", 8.5)])
        bundle = M.build_capture(NOW, "XLK", raw)
        bundle["capture_path"] = f"{M.EVIDENCE_ROOT}/derived/../../secret.json"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(M.SpdrSectorHoldingsError, "EVIDENCE_PATH_INVALID"):
                M.publish_capture(Path(tmp), bundle)

    def test_ticker_outside_universe_rejected_at_build_time(self):
        raw = fixture_workbook([("NVDA", "NVIDIA", 8.5)])
        with self.assertRaisesRegex(M.SpdrSectorHoldingsError, "HOLDINGS_TICKER_NOT_IN_UNIVERSE"):
            M.build_capture(NOW, "SMH", raw)


class LatestPointerTests(unittest.TestCase):
    def test_pointer_reports_completeness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            per_ticker = {"XLK": {"capture_id": "abc"}}
            path = M.write_latest_pointer(root, "2026-09-15", per_ticker)
            import json
            pointer = json.loads(path.read_text())
            self.assertFalse(pointer["complete"])
            per_ticker_all = {t: {"capture_id": "x"} for t in M.SECTOR_ETFS}
            M.write_latest_pointer(root, "2026-09-15", per_ticker_all)
            pointer_all = json.loads(path.read_text())
            self.assertTrue(pointer_all["complete"])


if __name__ == "__main__":
    unittest.main()
