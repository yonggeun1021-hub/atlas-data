"""US-U1 investable universe display generator (US-DATA-1 item 1) regression.

Covers the filter pipeline in isolation (no network, no real packet
required) plus a reconciliation check and the authority/caveat contract.
`run_all.py` executes this file directly (`python3 <script>`), not via
pytest -- it must self-run through `unittest.main()`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "us_investable_universe_v1.py"
SPEC = importlib.util.spec_from_file_location("us_investable_universe_v1", MODULE_PATH)
GEN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GEN)


def _row(symbol, name, source_name="nasdaq_listed", etf="N", test_issue="N", financial_status="N"):
    fields = {
        "Symbol": symbol,
        "Security Name": name,
        "ETF": etf,
        "Test Issue": test_issue,
    }
    if financial_status is not None:
        fields["Financial Status"] = financial_status
    return {
        "asset_id": f"US:TEST:{symbol}",
        "primary_symbol": symbol,
        "source_name": source_name,
        "fields": fields,
    }


CIK_SNAPSHOT = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [320193, "Apple Inc.", "AAPL", "Nasdaq"],
        [1067983, "Berkshire Hathaway", "BRK-B", "NYSE"],
        [1, "Foo Preferred Co", "FOO$A", "NYSE"],
    ],
}


class ClassifySecurityNameTests(unittest.TestCase):
    def test_ads(self):
        self.assertEqual(GEN.classify_security_name("BRBI BR Partners S.A. - ADSs"), GEN.SECURITY_TYPE_ADS)
        self.assertEqual(
            GEN.classify_security_name("KANZHUN LIMITED - American Depository Shares"),
            GEN.SECURITY_TYPE_ADS,
        )

    def test_ordinary(self):
        self.assertEqual(
            GEN.classify_security_name("Bit Digital, Inc. - Ordinary Share"), GEN.SECURITY_TYPE_ORDINARY
        )

    def test_common(self):
        self.assertEqual(
            GEN.classify_security_name("Agilent Technologies, Inc. Common Stock"), GEN.SECURITY_TYPE_COMMON
        )
        self.assertEqual(
            GEN.classify_security_name("Capital Clean Energy Carriers Corp. - Common Share"),
            GEN.SECURITY_TYPE_COMMON,
        )

    def test_common_bare_for_plain_name(self):
        self.assertEqual(GEN.classify_security_name("AMETEK, Inc."), GEN.SECURITY_TYPE_COMMON_BARE)

    def test_excludes_non_common(self):
        names = [
            "Armada Acquisition Corp. III - Units",
            "Armada Acquisition Corp. III - Warrant",
            "Apogee Acquisition Corp - Rights",
            "Bank of America Corporation Non Cumulative Perpetual Conv Pfd Ser L",
            "Adams Diversified Equity Fund Inc.",
            "BlackRock Health Sciences Trust",
            "ETRACS Alerian MLP Index ETN Series B due July 18, 2042",
        ]
        kept = {GEN.SECURITY_TYPE_ADS, GEN.SECURITY_TYPE_ORDINARY, GEN.SECURITY_TYPE_COMMON, GEN.SECURITY_TYPE_COMMON_BARE}
        for name in names:
            self.assertNotIn(GEN.classify_security_name(name), kept, name)


class CikLookupTests(unittest.TestCase):
    def test_exact_and_normalization(self):
        by_ticker = GEN.cik_by_ticker_from_snapshot(CIK_SNAPSHOT)
        self.assertEqual(GEN.lookup_cik("AAPL", by_ticker), ("320193", "exact"))
        self.assertEqual(GEN.lookup_cik("BRK.B", by_ticker), ("1067983", "dot_to_dash"))
        self.assertEqual(GEN.lookup_cik("FOO$A", by_ticker), ("1", "exact"))
        self.assertEqual(GEN.lookup_cik("NOPE", by_ticker), (None, None))

    def test_snapshot_field_order_enforced(self):
        bad = {"fields": ["ticker", "cik"], "data": []}
        with self.assertRaises(ValueError):
            GEN.cik_by_ticker_from_snapshot(bad)


class PipelineTests(unittest.TestCase):
    @staticmethod
    def _build(rows):
        packet = {"source_attribute_rows": rows}
        return GEN.build_investable_universe(
            packet, CIK_SNAPSHOT,
            generated_at_utc="2026-09-13T00:00:00Z",
            source_packet_ref={"date": "2026-09-13", "path": "x", "payload_sha256": "y"},
            cik_snapshot_ref={"path": "z", "sha256": "w", "row_count": 3},
        )

    def test_excludes_etf(self):
        result = self._build([_row("AAPL", "Apple Inc. Common Stock", etf="Y")])
        self.assertEqual(result["exclusion_counts"][GEN.EXCLUSION_ETF], 1)
        self.assertEqual(result["kept_count"], 0)

    def test_excludes_test_issue(self):
        result = self._build([_row("AAPL", "Apple Inc. Common Stock", test_issue="Y")])
        self.assertEqual(result["exclusion_counts"][GEN.EXCLUSION_TEST_ISSUE], 1)

    def test_excludes_abnormal_financial_status(self):
        result = self._build([_row("AAPL", "Apple Inc. Common Stock", financial_status="D")])
        self.assertEqual(result["exclusion_counts"][GEN.EXCLUSION_FINANCIAL_STATUS], 1)

    def test_missing_financial_status_is_not_excluded_by_that_reason(self):
        # otherlisted.txt rows have no Financial Status field at all.
        row = _row("AAPL", "Apple Inc. Common Stock", source_name="other_listed", financial_status=None)
        result = self._build([row])
        self.assertEqual(result["exclusion_counts"][GEN.EXCLUSION_FINANCIAL_STATUS], 0)
        self.assertEqual(result["kept_count"], 1)
        self.assertFalse(result["kept_rows"][0]["financial_status_available"])

    def test_excludes_non_common_security_type(self):
        result = self._build([_row("AAPL", "Apple Inc. Preferred Stock Series A")])
        self.assertEqual(result["exclusion_counts"][GEN.EXCLUSION_SECURITY_TYPE], 1)

    def test_excludes_missing_cik(self):
        result = self._build([_row("ZZZZNOPE", "Zzzz Nope Inc. Common Stock")])
        self.assertEqual(result["exclusion_counts"][GEN.EXCLUSION_CIK_NOT_FOUND], 1)

    def test_keeps_and_reconciles(self):
        rows = [
            _row("AAPL", "Apple Inc. Common Stock"),
            _row("BRK.B", "Berkshire Hathaway Inc. Common Stock"),
            _row("AAPL", "Apple Inc.", etf="Y"),  # excluded
        ]
        result = self._build(rows)
        self.assertEqual(result["kept_count"], 2)
        self.assertEqual(sum(result["exclusion_counts"].values()), 1)
        self.assertEqual(result["total_source_rows"], 3)
        self.assertEqual(
            result["kept_count"] + sum(result["exclusion_counts"].values()),
            result["total_source_rows"],
        )

    def test_rejects_missing_rows(self):
        with self.assertRaises(GEN.USInvestableUniverseError):
            GEN.build_investable_universe(
                {}, CIK_SNAPSHOT,
                generated_at_utc="2026-09-13T00:00:00Z",
                source_packet_ref={}, cik_snapshot_ref={},
            )

    def test_authority_all_display_only(self):
        result = self._build([_row("AAPL", "Apple Inc. Common Stock")])
        self.assertEqual(result["authority"], GEN.AUTHORITY)
        self.assertTrue(result["authority"]["t1_display_only"])
        self.assertFalse(result["authority"]["ratified"])
        self.assertFalse(result["authority"]["trading_or_order_authority"])

    def test_determinism_same_input_same_output(self):
        rows = [_row("AAPL", "Apple Inc. Common Stock")]
        r1 = self._build(rows)
        r2 = self._build(rows)
        r1.pop("generated_at_utc")
        r2.pop("generated_at_utc")
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
