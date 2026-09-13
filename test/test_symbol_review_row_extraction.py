#!/usr/bin/env python3
"""Extraction of the per-symbol row builders from the KR/US symbol reviews.

The bounded reviews must stay byte-identical, and the extracted builders
must report missing SMA20 / investor flows / prices / stage tags explicitly
instead of raising (KR) or estimating (both).

Byte identity is proven on a frozen consistent input snapshot
(test/rolling_pointer_snapshot.py): the live stage_history.json and
data/briefing/krx are rewritten by the daily collect hours before the
committed reviews are, so rebuilding from the live pointers tests data
timing, not code.  The live committed reviews are still re-derived from
their own embedded source.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "test") not in sys.path:
    sys.path.insert(0, str(ROOT / "test"))

import rolling_pointer_snapshot as SNAPSHOT  # noqa: E402


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


KR = _load("row_extraction_kr", "decision/korea_symbol_market_review.py")
US = _load("row_extraction_us", "decision/us_symbol_market_review.py")
MISSING_KR = {"missing_price_state": "BLOCKED", "missing_input_state": "BLOCKED"}


def _snapshot(relative: str):
    return json.loads(SNAPSHOT.fixture_bytes(relative))


def _live(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class BoundedOutputsUnchangedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.snapshot = SNAPSHOT.materialize(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_kr_bounded_review_is_byte_identical_to_committed_packet(self):
        stages = _snapshot("data/stage_history.json")
        rebuilt = KR.build_review(
            _snapshot("data/latest_korea_market_signals.json"), stages, briefing_root=self.snapshot / "data" / "briefing" / "krx"
        )
        committed = _snapshot("data/latest_korea_symbol_market_review.json")
        self.assertEqual(rebuilt, committed)
        self.assertEqual(rebuilt["packet_sha256"], committed["packet_sha256"])
        self.assertEqual(rebuilt["source_stage_history_sha256"], KR.payload_sha256(stages))

    def test_us_bounded_review_is_byte_identical_to_committed_packet(self):
        stages = _snapshot("data/stage_history.json")
        rebuilt = US.build_review(_snapshot("data/latest_free_market_data.json"), stages)
        committed = _snapshot("data/latest_us_symbol_market_review.json")
        self.assertEqual(rebuilt, committed)
        self.assertEqual(rebuilt["packet_sha256"], committed["packet_sha256"])

    def test_snapshot_drift_is_detected_not_absorbed(self):
        # A stage_history refresh alone (same tags, one newer date) must change
        # the rebuilt review: the byte-identity check is not vacuous.
        stages = _snapshot("data/stage_history.json")
        latest = sorted(stages)[-1]
        stages["9999-12-31"] = copy.deepcopy(stages[latest])
        rebuilt = KR.build_review(
            _snapshot("data/latest_korea_market_signals.json"), stages, briefing_root=self.snapshot / "data" / "briefing" / "krx"
        )
        self.assertNotEqual(rebuilt, _snapshot("data/latest_korea_symbol_market_review.json"))
        rebuilt_us = US.build_review(_snapshot("data/latest_free_market_data.json"), stages)
        self.assertNotEqual(rebuilt_us, _snapshot("data/latest_us_symbol_market_review.json"))

    def test_tampered_snapshot_bytes_fail_closed(self):
        entry = SNAPSHOT.MANIFEST["files"][0]
        original = SNAPSHOT.MANIFEST["files"][0]["sha256"]
        try:
            entry["sha256"] = "0" * 64
            with self.assertRaises(SNAPSHOT.SnapshotIntegrityError):
                SNAPSHOT.fixture_bytes(entry["repo_path"])
        finally:
            entry["sha256"] = original

    def test_live_committed_reviews_rederive_from_their_embedded_source(self):
        for module, relative in (
            (KR, "data/latest_korea_symbol_market_review.json"),
            (US, "data/latest_us_symbol_market_review.json"),
        ):
            with self.subTest(relative=relative):
                committed = _live(relative)
                self.assertEqual(module.validate_output(committed), committed)

    def test_kr_symbol_row_with_missing_policy_equals_bounded_row_when_inputs_complete(self):
        review = _snapshot("data/latest_korea_symbol_market_review.json")
        contract = KR.load_contract()
        for row in review["symbols"]:
            observed = review["source"]["stage_snapshot"]["subjects"][row["symbol"]]
            rebuilt = KR._symbol_row(row["symbol"], observed, review["source"]["stage_snapshot"]["as_of"], contract, missing_policy=MISSING_KR)
            self.assertEqual(rebuilt, row)


class KoreaMissingInputTests(unittest.TestCase):
    def setUp(self):
        review = _snapshot("data/latest_korea_symbol_market_review.json")
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
        self.market = _snapshot("data/latest_free_market_data.json")
        self.stages = _snapshot("data/stage_history.json")
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
