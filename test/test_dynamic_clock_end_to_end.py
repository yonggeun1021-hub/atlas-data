#!/usr/bin/env python3
"""P8-12 end-to-end regression against real committed repo evidence: the
BTC 2026-08-20 regression case (item 4), determinism (item 7), and a full
anti-lookahead sweep of everything the orchestrator produces."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.run_dynamic_clock import build_briefing_section, run  # noqa: E402


class BtcRegressionCaseTests(unittest.TestCase):
    """PR #210's audit found BTC's real Miss Episode: decision_date
    2026-08-20, PRICE_CONFIRMATION, corrected forward return +7.30%
    (signal_evaluation_at=2026-08-19, hypothetical_entry_at=2026-08-21).
    Item 4 requires this to actually surface as a Dynamic Clock re-review
    candidate under the new operational logic -- checked directly against
    the real report, not a synthetic fixture."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def _btc_price_confirmation_candidates(self):
        btc = self.report["by_market"]["BTC"]
        return [r for r in btc["active_review_candidates"] if r["trigger_type"] == "PRICE_CONFIRMATION"]

    def test_btc_has_an_active_price_confirmation_candidate_opened_2026_08_20(self):
        candidates = self._btc_price_confirmation_candidates()
        self.assertTrue(candidates, "expected at least one active BTC PRICE_CONFIRMATION candidate")
        opened_dates = {c["opened_at"] for c in candidates}
        self.assertIn("2026-08-20", opened_dates,
                       f"BTC 2026-08-20 signal did not surface as a Dynamic Clock episode; got {opened_dates}")

    def test_the_2026_08_20_episode_reference_metrics_match_pr210s_audited_figure(self):
        candidates = self._btc_price_confirmation_candidates()
        target = next(c for c in candidates if c["opened_at"] == "2026-08-20")
        fm = target["reference_forward_metrics_first_detection"]
        self.assertEqual(fm["status"], "OK")
        self.assertEqual(fm["decision_date"], "2026-08-20")
        self.assertEqual(fm["signal_evaluation_at"], "2026-08-19")
        self.assertEqual(fm["hypothetical_entry_at"], "2026-08-21")
        # PR #210's audited figure: +7.30% (was mis-reported +5.36% before
        # the round-4 anti-backdating fix). Allow a tight float tolerance.
        self.assertAlmostEqual(fm["horizons"]["1"]["forward_return_pct"], 7.2957704805, places=3)

    def test_the_candidate_carries_human_review_required_and_no_authority(self):
        candidates = self._btc_price_confirmation_candidates()
        target = next(c for c in candidates if c["opened_at"] == "2026-08-20")
        self.assertTrue(target["human_review_required"])
        self.assertIsNone(target["authority"]["trade_proposal"])
        self.assertFalse(target["authority"]["trading_authority"])
        self.assertFalse(target["authority"]["buy_authority"])


class DeterminismTests(unittest.TestCase):
    def test_two_full_runs_are_byte_identical(self):
        from replay.opportunity_trigger import canonical_json
        r1 = run()
        r2 = run()
        self.assertEqual(canonical_json(r1), canonical_json(r2))


class AuthorityInvariantAcrossReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_no_record_anywhere_in_the_report_grants_any_authority(self):
        checked = 0
        for market_result in self.report["by_market"].values():
            for record in market_result["active_review_candidates"] + market_result["expired_triggers"]:
                checked += 1
                auth = record["authority"]
                self.assertIsNone(auth["trade_proposal"])
                for key in ("stage_promotion_authority", "buy_authority", "action_authority",
                            "order_authority", "production_authority", "trading_authority"):
                    self.assertFalse(auth[key], (record["subject"], key))
                self.assertEqual(auth["capital"], 0)
        self.assertGreater(checked, 0, "sanity: real evidence should produce at least one record")


class LookaheadSweepTests(unittest.TestCase):
    """Anti-lookahead regression at the orchestrator/report level -- extends
    PR #210's `test_replay_lookahead_gate.py` pattern to the OPERATIONAL
    (episode/renewal) shape rather than a fixed historical decision_date."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_no_active_candidates_evidence_available_at_is_after_detected_at(self):
        checked = 0
        for market_result in self.report["by_market"].values():
            for record in market_result["active_review_candidates"]:
                checked += 1
                self.assertLessEqual(record["evidence_available_at"], record["detected_at"], record)
                self.assertLessEqual(record["first_detected_at"], record["detected_at"], record)
        self.assertGreater(checked, 0)

    def test_no_candidate_detected_at_is_after_its_markets_as_of_evidence_date(self):
        checked = 0
        for market, market_result in self.report["by_market"].items():
            as_of = market_result["as_of_evidence_date"]
            if as_of is None:
                continue
            for record in market_result["active_review_candidates"] + market_result["expired_triggers"]:
                checked += 1
                detected = record.get("detected_at") or record.get("last_detected_at")
                self.assertLessEqual(detected, as_of, (market, record["subject"]))
        self.assertGreater(checked, 0)

    def test_reference_forward_metrics_never_price_an_entry_at_or_before_its_decision_date(self):
        # Reuses PR #210's own invariant (compute_forward_metrics); this
        # just confirms the reused function's guarantee survives being
        # called from the new operational orchestrator.
        checked = 0
        for market_result in self.report["by_market"].values():
            for record in market_result["active_review_candidates"]:
                for key in ("reference_forward_metrics_first_detection", "reference_forward_metrics_latest_detection"):
                    fm = record.get(key)
                    if fm and fm.get("status") == "OK":
                        checked += 1
                        self.assertGreater(fm["hypothetical_entry_at"], fm["decision_date"], record)
        self.assertGreater(checked, 0, "sanity: at least one reference metric should be OK-graded")


class BriefingSectionShapeTests(unittest.TestCase):
    """Item 6's standalone artifact -- a briefing-readable section, produced
    without touching briefing/daily_orchestrator.py (see the module
    docstring in run_dynamic_clock.py and the PR conflict-check note)."""

    def test_briefing_section_has_new_expired_and_needs_re_review_per_market(self):
        report = run()
        section = build_briefing_section(report)
        for market in ("BTC", "KOREA", "CRYPTO"):
            m = section["markets"][market]
            for key in ("new_triggers", "needs_re_review", "expired_triggers", "not_computable_trigger_types"):
                self.assertIn(key, m, (market, key))


if __name__ == "__main__":
    unittest.main()
