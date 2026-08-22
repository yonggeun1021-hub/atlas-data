#!/usr/bin/env python3
"""P8-12 operational scan regression: NOT_COMPUTABLE labeling per market
(item 1's hard requirement), reuse-not-reimplementation checks, and PIT-
eligibility boundary preservation (item 4)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock import operational_scan as scan  # noqa: E402
from replay.opportunity_trigger import TRIGGER_TYPES  # noqa: E402


class ComputabilityMatrixTests(unittest.TestCase):
    def test_every_market_declares_a_status_for_all_seven_trigger_types(self):
        for market in ("BTC", "KOREA", "CRYPTO"):
            declared = set(scan.MARKET_TRIGGER_COMPUTABILITY[market])
            self.assertEqual(declared, set(TRIGGER_TYPES), market)

    def test_every_declared_status_is_computable_or_not_computable(self):
        for market, types in scan.MARKET_TRIGGER_COMPUTABILITY.items():
            for t, status in types.items():
                self.assertIn(status, ("COMPUTABLE", "NOT_COMPUTABLE"), (market, t))

    def test_not_computable_report_carries_a_real_reason_for_every_entry(self):
        for market in ("BTC", "KOREA", "CRYPTO"):
            report = scan.not_computable_report(market)
            declared_not_computable = {
                t for t, s in scan.MARKET_TRIGGER_COMPUTABILITY[market].items() if s == "NOT_COMPUTABLE"
            }
            reported = {r["trigger_type"] for r in report}
            self.assertEqual(reported, declared_not_computable, market)
            for r in report:
                self.assertTrue(r["reason"], f"{market}/{r['trigger_type']} has an empty reason")

    def test_fundamental_catalyst_expectation_are_not_computable_everywhere(self):
        # These three are structurally NOT_COMPUTABLE for every market -- no
        # parsed guidance/catalyst-calendar series exists anywhere in this
        # repo (replay.trigger_engine.NOT_COMPUTABLE_TYPES, reused verbatim).
        from replay.trigger_engine import NOT_COMPUTABLE_TYPES
        for market in ("BTC", "KOREA", "CRYPTO"):
            for t in NOT_COMPUTABLE_TYPES:
                self.assertEqual(scan.MARKET_TRIGGER_COMPUTABILITY[market][t], "NOT_COMPUTABLE", (market, t))

    def test_btc_flow_and_relative_strength_are_not_computable(self):
        self.assertEqual(scan.MARKET_TRIGGER_COMPUTABILITY["BTC"]["FLOW_REVERSAL"], "NOT_COMPUTABLE")
        self.assertEqual(scan.MARKET_TRIGGER_COMPUTABILITY["BTC"]["RELATIVE_STRENGTH_REVERSAL"], "NOT_COMPUTABLE")

    def test_korea_flow_reversal_is_computable(self):
        # Real repo evidence: KRX rows carry net_value.외국인합계 -- see
        # data/<date>/krx.json. Verified structurally here, not re-derived.
        self.assertEqual(scan.MARKET_TRIGGER_COMPUTABILITY["KOREA"]["FLOW_REVERSAL"], "COMPUTABLE")

    def test_crypto_flow_reversal_is_not_computable(self):
        self.assertEqual(scan.MARKET_TRIGGER_COMPUTABILITY["CRYPTO"]["FLOW_REVERSAL"], "NOT_COMPUTABLE")


class ReuseNotReimplementationTests(unittest.TestCase):
    def test_operational_scan_calls_replay_trigger_engine_functions(self):
        source = (ROOT / "clock" / "operational_scan.py").read_text(encoding="utf-8")
        self.assertIn("from replay import trigger_engine as te", source)
        for fn in ("te.price_confirmation", "te.invalidation_trigger",
                   "te.flow_reversal", "te.relative_strength_reversal"):
            self.assertIn(fn, source)

    def test_operational_scan_does_not_redefine_trigger_detection_math(self):
        source = (ROOT / "clock" / "operational_scan.py").read_text(encoding="utf-8")
        for forbidden in ("def price_confirmation", "def invalidation_trigger",
                           "def flow_reversal", "def relative_strength_reversal"):
            self.assertNotIn(forbidden, source)


class RealEvidenceScanTests(unittest.TestCase):
    """Runs the actual scanners against real committed repo evidence."""

    @classmethod
    def setUpClass(cls):
        cls.btc = scan.scan_btc()
        cls.korea = scan.scan_korea()
        cls.crypto = scan.scan_crypto()

    def test_btc_scan_finds_the_btc_subject(self):
        self.assertIn("BTC", self.btc["subjects"])

    def test_btc_population_label(self):
        self.assertEqual(self.btc["population_label"], "DEDICATED_COLLECTOR")

    def test_korea_population_label_preserves_current_watchlist_boundary(self):
        # ★ item 4: config/universe.json is NOT reconstructed historical PIT
        # evidence -- this label must never claim otherwise.
        self.assertEqual(self.korea["population_label"], "CURRENT_WATCHLIST_OPERATIONAL_COHORT")

    def test_korea_scan_covers_the_declared_universe(self):
        import json
        universe = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
        codes = {row["code"] for row in universe["kr"]}
        self.assertTrue(codes.issubset(set(self.korea["subjects"])))

    def test_crypto_population_label_preserves_ratified_taxonomy_boundary(self):
        self.assertEqual(self.crypto["population_label"], "PIT_RATIFIED_ELIGIBLE_UNIVERSE")

    def test_crypto_scan_never_includes_btc_as_a_breadth_subject(self):
        # BTC is tracked via its own dedicated collector -- see
        # replay.asset_identity's BREADTH_EXCLUDED_ASSETS. Must not be
        # double-represented in the crypto breadth population.
        self.assertNotIn("BTC/USD", self.crypto["subjects"])
        self.assertNotIn("BTC", self.crypto["subjects"])

    def test_all_produced_events_are_anti_lookahead_safe(self):
        # Every event's detected_at/evidence_available_at must already
        # satisfy replay.opportunity_trigger's own construction-time gate
        # (evidence_available_at <= detected_at) -- inherited for free since
        # these are built from real OpportunityTriggerEvent objects.
        checked = 0
        for result in (self.btc, self.korea, self.crypto):
            for by_type in result["subjects"].values():
                for events in by_type.values():
                    for ev in events:
                        checked += 1
                        self.assertLessEqual(ev.evidence_available_at, ev.detected_at)
        self.assertGreater(checked, 0, "sanity: real evidence should produce at least one event")


if __name__ == "__main__":
    unittest.main()
