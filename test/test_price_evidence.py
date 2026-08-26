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


class KoreaMarketMembershipLoaderTests(unittest.TestCase):
    """`config/korea_market_membership.json` loader -- proves the RATIFIED
    pathway actually works (not just that everything is currently
    UNRATIFIED), and that UNRATIFIED/malformed entries are correctly
    excluded."""

    def test_real_committed_file_has_zero_ratified_entries(self):
        self.assertEqual(pe.load_ratified_korea_market_membership(), {})

    def test_ratified_entry_is_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "korea_market_membership.json"
            path.write_text(json.dumps({
                "members": [
                    {"code": "298040", "market_claim": "KOSPI", "approval_status": "RATIFIED"},
                    {"code": "267260", "market_claim": "KOSPI", "approval_status": "UNRATIFIED"},
                ],
            }), encoding="utf-8")
            self.assertEqual(pe.load_ratified_korea_market_membership(path), {"298040": "KOSPI"})

    def test_missing_file_returns_empty_dict_not_a_crash(self):
        self.assertEqual(
            pe.load_ratified_korea_market_membership(Path("/nonexistent/path.json")), {},
        )

    def test_malformed_entries_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "korea_market_membership.json"
            path.write_text(json.dumps({
                "members": [
                    {"code": "005930", "market_claim": "NYSE", "approval_status": "RATIFIED"},  # bad market
                    {"code": "000660", "approval_status": "RATIFIED"},  # missing market_claim
                    "not_a_dict",
                ],
            }), encoding="utf-8")
            self.assertEqual(pe.load_ratified_korea_market_membership(path), {})


class KrxStockEvidenceTests(unittest.TestCase):
    def test_hyosung_298040_produces_real_differentiated_evidence(self):
        ev = pe.assemble_krx_stock_evidence("298040", DECISION_DATE)
        self.assertIsNotNone(ev["price_as_of"])
        self.assertEqual(ev["data_source_scope"], "KRX_OFFICIAL")
        self.assertIsNotNone(ev["recent_return_windows"])
        self.assertIsNotNone(ev["recent_return_windows"]["1m"])
        self.assertIsNotNone(ev["relative_strength"]["position_vs_recent_high_pct"])

    def test_hd_hyundai_electric_267260_produces_real_differentiated_evidence(self):
        ev = pe.assemble_krx_stock_evidence("267260", DECISION_DATE)
        self.assertIsNotNone(ev["price_as_of"])
        self.assertIsNotNone(ev["recent_return_windows"]["1m"])
        self.assertIsNotNone(ev["relative_strength"]["position_vs_recent_high_pct"])

    def test_doosan_034020_has_no_pit_evidence_at_frozen_date_and_returns_all_none(self):
        ev = pe.assemble_krx_stock_evidence("034020", DECISION_DATE)
        self.assertEqual(ev, {
            "price_as_of": None,
            "data_source_scope": "KRX_OFFICIAL",
            "recent_return_windows": None,
            "relative_strength": None,
        })

    def test_later_doosan_capture_is_not_backfilled_into_frozen_date(self):
        frozen = pe.assemble_krx_stock_evidence("034020", DECISION_DATE)
        later = pe.assemble_krx_stock_evidence("034020", "2026-08-24")
        self.assertIsNone(frozen["price_as_of"])
        self.assertIsNotNone(later["price_as_of"])

    def test_no_code_currently_gets_a_ratified_vs_market_benchmark(self):
        # CIO review round 2 on PR #212: the old hardcoded
        # KOREA_STOCK_MARKET_MEMBERSHIP dict was retracted ("a code comment
        # is not real evidence"). config/korea_market_membership.json's
        # entries are all still UNRATIFIED as of this build, so vs_market
        # must be None for every Korea code, with real price evidence still
        # otherwise present -- see test_price_evidence_market_membership.py.
        self.assertEqual(pe.load_ratified_korea_market_membership(), {})
        for code in ("298040", "267260", "005930", "000660", "012450"):
            ev = pe.assemble_krx_stock_evidence(code, DECISION_DATE)
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
        # 2026-07-06. (Moot as of round 2 since vs_market is currently
        # always None regardless of decision_date -- kept as a regression in
        # case a future RATIFIED membership entry lands.)
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
    stops returning a blanket UNKNOWN `price_state` for the KRX/BTC-covered
    subjects, while `reflection_status` honestly stays UNKNOWN for all of
    them (CIO review round 2: no subject's real evidence bundle currently
    carries an event/expectation reference point -- see
    decision/pilot_evidence_intake.py's price_reflection input builders,
    none of which pass `event_reaction`/`reflection_reference`)."""

    def _build(self, subject: str, decision_date: str = DECISION_DATE) -> dict:
        evidence = pe.assemble_price_evidence(subject, decision_date)
        return pr.build_packet(
            subject=subject, decision_date=decision_date,
            generated_at=f"{decision_date}T01:00:00Z", **evidence,
        )

    def test_hyosung_gets_a_confident_price_state_but_unknown_reflection(self):
        rp = self._build("298040.KS")["price_reflection"]
        self.assertNotEqual(rp["price_state"], "UNKNOWN")
        self.assertEqual(rp["reflection_status"], "UNKNOWN")
        self.assertEqual(rp["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")

    def test_hd_hyundai_electric_gets_a_confident_price_state_but_unknown_reflection(self):
        rp = self._build("267260.KS")["price_reflection"]
        self.assertNotEqual(rp["price_state"], "UNKNOWN")
        self.assertEqual(rp["reflection_status"], "UNKNOWN")

    def test_doosan_is_honestly_price_data_missing(self):
        rp = self._build("034020.KS")["price_reflection"]
        self.assertEqual(rp["price_state"], "UNKNOWN")
        self.assertEqual(rp["reflection_status"], "UNKNOWN")
        self.assertEqual(rp["data_state"], "PRICE_DATA_MISSING")

    def test_samsung_electronics_005930_gets_a_confident_price_state_but_unknown_reflection(self):
        rp = self._build("005930.KS")["price_reflection"]
        self.assertNotEqual(rp["price_state"], "UNKNOWN")
        self.assertEqual(rp["reflection_status"], "UNKNOWN")

    def test_sk_hynix_000660_gets_a_confident_price_state_but_unknown_reflection(self):
        rp = self._build("000660.KS")["price_reflection"]
        self.assertNotEqual(rp["price_state"], "UNKNOWN")
        self.assertEqual(rp["reflection_status"], "UNKNOWN")

    def test_btc_is_overextended_price_state_but_unknown_reflection(self):
        # The exact CIO round-2 core example: a real, extreme BTC rally is
        # real price_state=OVEREXTENDED evidence, but with no expectation/
        # catalyst reference point in this repo's evidence, that is NOT
        # "future expectations are fully reflected" -- reflection_status
        # must stay UNKNOWN.
        rp = self._build("BTC")["price_reflection"]
        self.assertEqual(rp["price_state"], "OVEREXTENDED")
        self.assertEqual(rp["reflection_status"], "UNKNOWN")
        self.assertEqual(rp["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")

    def test_tsm_is_reflection_uncertain_with_valid_price(self):
        packet = self._build("TSM")
        rp = packet["price_reflection"]
        self.assertEqual(rp["price_state"], "UNKNOWN")  # single point, no momentum computable either
        self.assertEqual(rp["reflection_status"], "UNKNOWN")
        self.assertEqual(rp["data_state"], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE")
        self.assertNotEqual(rp["price_as_of"], "UNKNOWN")  # a real, fresh price WAS found

    def test_every_built_packet_still_validates(self):
        for subject in ("298040.KS", "267260.KS", "034020.KS", "TSM",
                         "005930.KS", "000660.KS", "BTC"):
            packet = self._build(subject)
            pr.validate_packet(packet, pr.load_contract())


if __name__ == "__main__":
    unittest.main()
