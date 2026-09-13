#!/usr/bin/env python3
"""Extraction of the per-symbol row builders from the KR/US symbol reviews.

The bounded reviews must stay byte-identical, and the extracted builders
must report missing SMA20 / investor flows / prices / stage tags explicitly
instead of raising (KR) or estimating (both).
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


KR = _load("row_extraction_kr", "decision/korea_symbol_market_review.py")
US = _load("row_extraction_us", "decision/us_symbol_market_review.py")
MISSING_KR = {"missing_price_state": "BLOCKED", "missing_input_state": "BLOCKED"}


def _read(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class BoundedOutputsUnchangedTests(unittest.TestCase):
    def test_kr_bounded_review_is_byte_identical_to_committed_packet(self):
        stages = _read("data/stage_history.json")
        rebuilt = KR.build_review(_read("data/latest_korea_market_signals.json"), stages)
        committed = _read("data/latest_korea_symbol_market_review.json")
        self.assertEqual(rebuilt, committed)
        self.assertEqual(rebuilt["packet_sha256"], committed["packet_sha256"])

    def test_us_bounded_review_is_byte_identical_to_committed_packet(self):
        stages = _read("data/stage_history.json")
        rebuilt = US.build_review(_read("data/latest_free_market_data.json"), stages)
        committed = _read("data/latest_us_symbol_market_review.json")
        self.assertEqual(rebuilt, committed)
        self.assertEqual(rebuilt["packet_sha256"], committed["packet_sha256"])

    def test_kr_symbol_row_with_missing_policy_equals_bounded_row_when_inputs_complete(self):
        review = _read("data/latest_korea_symbol_market_review.json")
        contract = KR.load_contract()
        for row in review["symbols"]:
            observed = review["source"]["stage_snapshot"]["subjects"][row["symbol"]]
            rebuilt = KR._symbol_row(row["symbol"], observed, review["source"]["stage_snapshot"]["as_of"], contract, missing_policy=MISSING_KR)
            self.assertEqual(rebuilt, row)


class KoreaMissingInputTests(unittest.TestCase):
    def setUp(self):
        review = _read("data/latest_korea_symbol_market_review.json")
        self.contract = KR.load_contract()
        self.stage_as_of = review["source"]["stage_snapshot"]["as_of"]
        self.observed = copy.deepcopy(review["source"]["stage_snapshot"]["subjects"]["012450"])

    def test_default_path_still_fails_closed_on_missing_sma20(self):
        observed = copy.deepcopy(self.observed)
        observed["confirmed_metrics"]["sma20"] = None
        with self.assertRaises(KR.KoreaSymbolMarketReviewError):
            KR._symbol_row("012450", observed, self.stage_as_of, self.contract)

    def test_missing_sma20_is_reported_not_estimated(self):
        observed = copy.deepcopy(self.observed)
        observed["confirmed_metrics"] = {"sma20": None, "status": "NOT_COMPUTABLE", "reason": "RETAINED_SESSIONS=2"}
        row = KR._symbol_row("012450", observed, self.stage_as_of, self.contract, missing_policy=MISSING_KR)
        self.assertEqual(row["price_context"]["status"], "OBSERVED_CONFIRMED_NO_SMA20")
        self.assertIsNone(row["price_context"]["sma20_krw"])
        self.assertIsNone(row["price_context"]["distance_from_sma20_pct"])
        self.assertEqual(row["price_context"]["close_krw"], self.observed["latest_confirmed_row"]["close"])
        self.assertNotIn("PRICE_ABOVE_20_DAY_AVERAGE", row["observed_facts"])
        self.assertNotIn("PRICE_BELOW_20_DAY_AVERAGE", row["observed_facts"])
        self.assertEqual(row["entry_review"]["state"], "BLOCKED")
        self.assertIn("SMA20_NOT_COMPUTABLE:RETAINED_SESSIONS=2", row["entry_review"]["reasons"])
        self.assertIn("FINAL_KOREA_REGIME_POLICY_PENDING", row["entry_review"]["reasons"])
        self.assertFalse(row["entry_review"]["automatic_entry_generated"])
        self.assertIsNone(row["entry_review"]["order_draft"])
        # flows were present, so the flow fact and context survive
        self.assertIsNotNone(row["flow_context"])

    def test_missing_flows_and_stage_are_reported(self):
        observed = copy.deepcopy(self.observed)
        observed["latest_confirmed_row"]["net_value"] = {}
        observed["atlas_stage"] = None
        row = KR._symbol_row("012450", observed, self.stage_as_of, self.contract, missing_policy=MISSING_KR)
        self.assertIsNone(row["flow_context"])
        self.assertIsNone(row["pipeline_stage"])
        self.assertEqual(row["entry_review"]["state"], "BLOCKED")
        self.assertIn("INVESTOR_FLOW_NOT_AVAILABLE", row["entry_review"]["reasons"])
        self.assertIn("PIPELINE_STAGE_NOT_ASSIGNED", row["entry_review"]["reasons"])
        self.assertNotIn("PIPELINE_STAGE_IS_NOT_BUY_AUTHORITY", row["entry_review"]["reasons"])
        self.assertTrue(all("NET_BUY" not in fact and "NET_SELL" not in fact for fact in row["observed_facts"]))

    def test_missing_price_row(self):
        observed = copy.deepcopy(self.observed)
        observed["latest_confirmed_row"]["close"] = None
        row = KR._symbol_row("012450", observed, self.stage_as_of, self.contract, missing_policy=MISSING_KR)
        self.assertEqual(row["price_context"]["status"], "UNAVAILABLE")
        self.assertIsNone(row["price_context"]["close_krw"])
        self.assertEqual(row["entry_review"]["state"], "BLOCKED")
        self.assertIn("PIPELINE_SYMBOL_PRICE_UNAVAILABLE", row["entry_review"]["reasons"])


class UsRowTests(unittest.TestCase):
    def setUp(self):
        self.market = _read("data/latest_free_market_data.json")
        self.stages = _read("data/stage_history.json")
        self.contract = US.load_contract()
        self.source = US._compact_source(self.market, self.stages, self.contract)
        self.coverage = US._axes(self.source, self.contract)
        self.leadership = US._leadership_by_symbol(self.coverage)
        self.breadth = self.coverage["axes"]["BREADTH"].get("facts")

    def _row(self, symbol, identity, prices):
        return US._symbol_row(
            symbol, identity, prices, self.coverage, self.contract, self.source["stage_snapshot"]["as_of"],
            leadership_by_symbol=self.leadership, breadth=self.breadth, source_scope=self.source["market_capture"]["alpaca_scope"],
        )

    def test_symbol_without_declared_proxies_or_stage_builds_a_row(self):
        bars = sorted(
            (copy.deepcopy(bar) for bar in self.market["alpaca"]["daily_bars"] if bar.get("symbol") == "MSFT"),
            key=lambda bar: bar.get("opened_at", ""),
        )
        self.assertTrue(bars)
        row = self._row("MSFT", {"name": "Microsoft", "stage": None}, bars)
        self.assertEqual(row["price_context"]["status"], "OBSERVED")
        self.assertEqual(row["entry_review"]["state"], "WAIT")
        self.assertIn("PIPELINE_STAGE_NOT_ASSIGNED", row["entry_review"]["reasons"])
        self.assertNotIn("PIPELINE_STAGE_IS_NOT_BUY_AUTHORITY", row["entry_review"]["reasons"])
        self.assertEqual(row["market_context"]["leadership_proxies"], [])
        self.assertIsNone(row["pipeline_stage"])

    def test_missing_prices_stay_blocked_without_estimation(self):
        row = self._row("ZZZZ", {"name": "No Bars Inc", "stage": None}, [])
        self.assertEqual(row["price_context"]["status"], "UNAVAILABLE")
        self.assertIsNone(row["price_context"]["close_usd"])
        self.assertEqual(row["entry_review"]["state"], "BLOCKED")
        self.assertIn("PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE", row["entry_review"]["reasons"])
        self.assertIn("PIPELINE_STAGE_NOT_ASSIGNED", row["entry_review"]["reasons"])


if __name__ == "__main__":
    unittest.main()
