#!/usr/bin/env python3
"""P8-10 real evidence assembly (decision/price_evidence.py) regression.

Exercises the module against REAL committed repo evidence (KRX daily
snapshots, korea_leadership_context KOSPI/KOSDAQ composite packets,
free_market_data Alpaca IEX snapshots) -- no synthetic price is fabricated
anywhere in this file except where a test explicitly builds an isolated
temp-directory fixture to probe an edge case (e.g. a code with zero
evidence), which is always clearly labeled as such.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import price_evidence as pe  # noqa: E402
from decision import price_reflection as pr  # noqa: E402

DECISION_DATE = "2026-08-22"  # matches PILOT_DECISION_DATE / current latest committed evidence


class KoreaBenchmarkSeriesTests(unittest.TestCase):
    def test_loads_real_committed_kospi_and_kosdaq_sessions(self):
        kospi = pe.KoreaBenchmarkSeries.load("KOSPI")
        kosdaq = pe.KoreaBenchmarkSeries.load("KOSDAQ")
        # Real committed korea_leadership_context dates as of this build:
        # 2026-08-10..08-14 and 08-18..08-21 (weekend/holiday gaps excluded).
        self.assertIn("2026-08-21", kospi.dates())
        self.assertIn("2026-08-21", kosdaq.dates())
        self.assertGreaterEqual(len(kospi.dates()), 8)

    def test_unsupported_market_rejected(self):
        with self.assertRaises(ValueError):
            pe.KoreaBenchmarkSeries("KRX")

    def test_index_levels_are_chain_linked_not_absolute(self):
        series = pe.KoreaBenchmarkSeries.load("KOSPI")
        levels = series.index_levels(DECISION_DATE)
        dates = sorted(levels)
        self.assertGreaterEqual(len(dates), 2)
        # Chain-link identity: level(d) == level(d-1) * gross_return(d).
        first, second = dates[0], dates[1]
        gross = series._by_date[second]["gross_return"]
        self.assertEqual(levels[second], levels[first] * gross)

    def test_missing_or_malformed_packet_dir_yields_empty_series_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = pe.KoreaBenchmarkSeries.load("KOSPI", base_dir=Path(tmp) / "does_not_exist")
            self.assertEqual(series.dates(), [])

    def test_directory_name_mismatch_with_own_observation_date_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bad_dir = base / "2026-08-10"
            bad_dir.mkdir()
            (bad_dir / "packet.json").write_text(json.dumps({
                "generated_at": "2026-08-22T00:00:00Z",
                "leadership_packet": {
                    "observation_date": "2026-08-11",  # deliberately mismatched
                    "relative_strength_observations": [{
                        "role": "KOSPI_BENCHMARK", "series_identity": "KOSPI::코스피",
                        "cumulative_gross_return": "1.01",
                    }],
                },
            }), encoding="utf-8")
            series = pe.KoreaBenchmarkSeries.load("KOSPI", base_dir=base)
            self.assertEqual(series.dates(), [])


class KrxStockEvidenceTests(unittest.TestCase):
    def test_hyosung_298040_produces_real_differentiated_evidence(self):
        ev = pe.assemble_krx_stock_evidence("298040", DECISION_DATE)
        self.assertIsNotNone(ev["price_as_of"])
        self.assertEqual(ev["data_source_scope"], "KRX_OFFICIAL")
        self.assertIsNotNone(ev["recent_return_windows"])
        self.assertIsNotNone(ev["recent_return_windows"]["1m"])
        self.assertIsNotNone(ev["relative_strength"]["vs_market"])
        self.assertIsNotNone(ev["relative_strength"]["position_vs_recent_high_pct"])

    def test_hd_hyundai_electric_267260_produces_real_differentiated_evidence(self):
        ev = pe.assemble_krx_stock_evidence("267260", DECISION_DATE)
        self.assertIsNotNone(ev["price_as_of"])
        self.assertIsNotNone(ev["recent_return_windows"]["1m"])
        self.assertIsNotNone(ev["relative_strength"]["vs_market"])

    def test_doosan_034020_has_zero_evidence_and_returns_all_none(self):
        ev = pe.assemble_krx_stock_evidence("034020", DECISION_DATE)
        self.assertEqual(ev, {
            "price_as_of": None,
            "data_source_scope": "KRX_OFFICIAL",
            "recent_return_windows": None,
            "relative_strength": None,
        })

    def test_code_without_declared_market_membership_gets_no_vs_market(self):
        # 012450 (한화에어로스페이스) has real KRX price evidence but is NOT
        # in KOREA_STOCK_MARKET_MEMBERSHIP -- vs_market must fail closed to
        # None rather than guess a benchmark.
        self.assertNotIn("012450", pe.KOREA_STOCK_MARKET_MEMBERSHIP)
        ev = pe.assemble_krx_stock_evidence("012450", DECISION_DATE)
        self.assertIsNotNone(ev["price_as_of"])  # real price evidence exists
        if ev["relative_strength"] is not None:
            self.assertIsNone(ev["relative_strength"]["vs_market"])

    def test_result_is_deterministic(self):
        first = pe.assemble_krx_stock_evidence("298040", DECISION_DATE)
        second = pe.assemble_krx_stock_evidence("298040", DECISION_DATE)
        self.assertEqual(first, second)

    def test_earlier_decision_date_never_sees_kospi_benchmark_backfilled_same_day(self):
        # Every real committed korea_leadership_context packet has
        # generated_at dated 2026-08-22 (all backfilled the same real day),
        # regardless of its own observation_date -- see price_evidence.py's
        # module docstring. So for any decision_date strictly before that,
        # zero benchmark sessions are PIT-eligible and vs_market must be
        # None even though the stock's own KRX price history goes back to
        # 2026-07-06.
        ev = pe.assemble_krx_stock_evidence("298040", "2026-08-20")
        self.assertIsNotNone(ev["price_as_of"])
        if ev["relative_strength"] is not None:
            self.assertIsNone(ev["relative_strength"]["vs_market"])


class CryptoEvidenceTests(unittest.TestCase):
    def test_btc_produces_real_multi_window_evidence(self):
        ev = pe.assemble_crypto_evidence("BTC", DECISION_DATE)
        self.assertIsNotNone(ev["price_as_of"])
        self.assertEqual(ev["data_source_scope"], "KRAKEN_OHLC")
        self.assertIsNotNone(ev["recent_return_windows"])
        # Unlike KRX (~32 real days) and TSM (1 point), BTC's ~720-day
        # embedded Kraken history genuinely supports all three windows.
        for label in ("1m", "3m", "6m"):
            self.assertIsNotNone(ev["recent_return_windows"][label])
        self.assertIsNotNone(ev["relative_strength"]["position_vs_recent_high_pct"])
        # No separate crypto market-index series exists in this repo --
        # vs_market must never be fabricated as tautologically BTC-vs-BTC.
        self.assertIsNone(ev["relative_strength"]["vs_market"])

    def test_non_btc_crypto_symbol_returns_all_none(self):
        ev = pe.assemble_crypto_evidence("ETH", DECISION_DATE)
        self.assertEqual(ev, {
            "price_as_of": None,
            "data_source_scope": "KRAKEN_OHLC",
            "recent_return_windows": None,
            "relative_strength": None,
        })

    def test_btc_aliases_all_dispatch_to_the_same_real_series(self):
        canonical = pe.assemble_price_evidence("BTC", DECISION_DATE)
        for alias in ("BTC-USD", "XBTUSD", "BTCUSD"):
            self.assertEqual(pe.assemble_price_evidence(alias, DECISION_DATE), canonical)

    def test_result_is_deterministic(self):
        first = pe.assemble_crypto_evidence("BTC", DECISION_DATE)
        second = pe.assemble_crypto_evidence("BTC", DECISION_DATE)
        self.assertEqual(first, second)


class UsEquityEvidenceTests(unittest.TestCase):
    def test_tsm_single_point_snapshot_is_fresh_but_has_no_windows(self):
        ev = pe.assemble_us_equity_evidence("TSM", DECISION_DATE)
        self.assertIsNotNone(ev["price_as_of"])
        self.assertEqual(ev["data_source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")
        self.assertIsNone(ev["recent_return_windows"])
        self.assertIsNone(ev["relative_strength"])

    def test_unknown_symbol_returns_all_none(self):
        ev = pe.assemble_us_equity_evidence("ZZZZ_NOT_REAL", DECISION_DATE)
        self.assertEqual(ev, {
            "price_as_of": None,
            "data_source_scope": "IEX_ONLY_PARTIAL_US_MARKET",
            "recent_return_windows": None,
            "relative_strength": None,
        })

    def test_return_window_label_only_assigned_within_real_calendar_bands(self):
        self.assertIsNone(pe._us_return_window_label(1))
        self.assertEqual(pe._us_return_window_label(25), "1m")
        self.assertEqual(pe._us_return_window_label(90), "3m")
        self.assertEqual(pe._us_return_window_label(180), "6m")
        self.assertIsNone(pe._us_return_window_label(45))


class DispatchTests(unittest.TestCase):
    def test_ks_suffixed_code_dispatches_to_krx(self):
        self.assertEqual(pe._krx_code_from_subject("298040.KS"), "298040")

    def test_bare_six_digit_code_dispatches_to_krx(self):
        self.assertEqual(pe._krx_code_from_subject("298040"), "298040")

    def test_us_ticker_does_not_dispatch_to_krx(self):
        self.assertIsNone(pe._krx_code_from_subject("TSM"))

    def test_assemble_price_evidence_dispatches_correctly(self):
        krx = pe.assemble_price_evidence("298040.KS", DECISION_DATE)
        us = pe.assemble_price_evidence("TSM", DECISION_DATE)
        self.assertEqual(krx["data_source_scope"], "KRX_OFFICIAL")
        self.assertEqual(us["data_source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")


class EndToEndPriceReflectionWiringTests(unittest.TestCase):
    """Feeds real assembled evidence straight into
    decision.price_reflection.build_packet() -- proves the wiring genuinely
    stops returning blanket UNKNOWN for the KRX-covered pilots."""

    def _build(self, subject: str, decision_date: str = DECISION_DATE) -> dict:
        evidence = pe.assemble_price_evidence(subject, decision_date)
        return pr.build_packet(
            subject=subject, decision_date=decision_date,
            generated_at=f"{decision_date}T01:00:00Z", **evidence,
        )

    def test_hyosung_gets_a_confident_non_unknown_status(self):
        packet = self._build("298040.KS")
        rp = packet["price_reflection"]
        self.assertNotEqual(rp["status"], "UNKNOWN")
        self.assertEqual(pr.data_state_of(rp), "VALID")

    def test_hd_hyundai_electric_gets_a_confident_non_unknown_status(self):
        packet = self._build("267260.KS")
        rp = packet["price_reflection"]
        self.assertNotEqual(rp["status"], "UNKNOWN")
        self.assertEqual(pr.data_state_of(rp), "VALID")

    def test_doosan_is_honestly_price_data_missing(self):
        packet = self._build("034020.KS")
        rp = packet["price_reflection"]
        self.assertEqual(rp["status"], "UNKNOWN")
        self.assertEqual(pr.data_state_of(rp), "PRICE_DATA_MISSING")

    def test_samsung_electronics_005930_gets_a_confident_non_unknown_status(self):
        packet = self._build("005930.KS")
        rp = packet["price_reflection"]
        self.assertNotEqual(rp["status"], "UNKNOWN")
        self.assertEqual(pr.data_state_of(rp), "VALID")

    def test_sk_hynix_000660_gets_a_confident_non_unknown_status(self):
        packet = self._build("000660.KS")
        rp = packet["price_reflection"]
        self.assertNotEqual(rp["status"], "UNKNOWN")
        self.assertEqual(pr.data_state_of(rp), "VALID")

    def test_btc_gets_a_confident_non_unknown_status(self):
        packet = self._build("BTC")
        rp = packet["price_reflection"]
        self.assertNotEqual(rp["status"], "UNKNOWN")
        self.assertEqual(pr.data_state_of(rp), "VALID")

    def test_tsm_is_reflection_uncertain_with_valid_price(self):
        packet = self._build("TSM")
        rp = packet["price_reflection"]
        self.assertEqual(rp["status"], "UNKNOWN")
        self.assertEqual(pr.data_state_of(rp), "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")
        self.assertNotEqual(rp["price_as_of"], "UNKNOWN")  # a real, fresh price WAS found

    def test_every_built_packet_still_validates(self):
        for subject in ("298040.KS", "267260.KS", "034020.KS", "TSM",
                         "005930.KS", "000660.KS", "BTC"):
            packet = self._build(subject)
            pr.validate_packet(packet, pr.load_contract())


if __name__ == "__main__":
    unittest.main()
