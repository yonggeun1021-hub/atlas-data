#!/usr/bin/env python3
"""P8-12 end-to-end regression against real committed repo evidence: the
BTC 2026-08-20 regression case (item 4/9), the candidate-flood fix (CIO
review round 1, item 3/9), determinism, and a full anti-lookahead sweep of
everything the orchestrator produces."""
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
    Item 4/9 require this to remain present in the Review Queue after
    triage -- checked directly against the real report."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def _btc_raw_price_confirmation(self):
        btc = self.report["by_market"]["BTC"]
        return [r for r in btc["raw_trigger_ledger"] if r["trigger_type"] == "PRICE_CONFIRMATION"]

    def _btc_subject_candidate(self):
        btc = self.report["by_market"]["BTC"]
        matches = [r for r in btc["review_queue"] if r["subject"] == "BTC"]
        self.assertEqual(len(matches), 1, "BTC must have exactly one consolidated subject candidate")
        return matches[0]

    def test_raw_ledger_still_has_the_2026_08_20_episode(self):
        # Item 3: raw triggers are NEVER dropped, only consolidated/tiered.
        raw = self._btc_raw_price_confirmation()
        opened_dates = {r["opened_at"] for r in raw}
        self.assertIn("2026-08-20", opened_dates)

    def test_the_2026_08_20_episode_reference_metrics_match_pr210s_audited_figure(self):
        target = next(r for r in self._btc_raw_price_confirmation() if r["opened_at"] == "2026-08-20")
        fm = target["reference_forward_metrics_first_detection"]
        self.assertEqual(fm["status"], "OK")
        self.assertEqual(fm["decision_date"], "2026-08-20")
        self.assertEqual(fm["signal_evaluation_at"], "2026-08-19")
        self.assertEqual(fm["hypothetical_entry_at"], "2026-08-21")
        self.assertAlmostEqual(fm["horizons"]["1"]["forward_return_pct"], 7.2957704805, places=3)

    def test_btc_remains_present_in_the_review_queue_after_triage(self):
        # Item 9's explicit regression requirement.
        candidate = self._btc_subject_candidate()
        self.assertEqual(candidate["subject"], "BTC")

    def test_btc_carries_the_audit_confirmed_miss_exception_and_is_elevated(self):
        # BTC's real trigger is a single PRICE_CONFIRMATION -- confirmation_count
        # can never reach 2 for BTC (RELATIVE_STRENGTH_REVERSAL is structurally
        # NOT_COMPUTABLE there), so without the item-4 exception it would be
        # capped at WATCH_REVIEW forever despite being a real, audited Miss.
        candidate = self._btc_subject_candidate()
        self.assertIsNotNone(candidate["audit_confirmed_miss"])
        self.assertTrue(candidate["audit_confirmed_miss_exception_applied"])
        self.assertEqual(candidate["tier"], "IMMEDIATE_REVIEW")
        self.assertTrue(candidate["human_review_required"])

    def test_the_candidate_carries_no_authority(self):
        candidate = self._btc_subject_candidate()
        self.assertIsNone(candidate["authority"]["trade_proposal"])
        self.assertFalse(candidate["authority"]["trading_authority"])
        self.assertFalse(candidate["authority"]["buy_authority"])


class CandidateFloodRegressionTests(unittest.TestCase):
    """CIO review round 1 on PR #211: Crypto alone previously produced 99
    active review candidates (one per raw trigger), all human_review_required
    =True. Item 9 requires an explicit test that fails if this flood
    recurs -- asserting BOTH the raw count (must stay high/complete) AND the
    post-triage IMMEDIATE_REVIEW count (must stay small) together, so triage
    logic can't silently regress back to flooding without either number
    moving in a way this test would catch."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()
        cls.crypto = cls.report["by_market"]["CRYPTO"]

    def test_raw_trigger_ledger_is_not_truncated(self):
        # The full raw audit trail must be preserved -- sanity floor, not an
        # exact literal (real evidence can drift as new snapshots land).
        self.assertGreater(self.crypto["raw_trigger_count"], 50,
                            "raw trigger ledger looks truncated relative to the real evidence population")

    def test_immediate_review_does_not_flood(self):
        # The actual hard requirement: post-triage IMMEDIATE_REVIEW (the
        # only tier with human_review_required=True) must be a small
        # fraction of the raw trigger count, never all of it.
        immediate_count = len(self.crypto["immediate_review"])
        raw_count = self.crypto["raw_trigger_count"]
        self.assertLess(
            immediate_count, raw_count,
            "IMMEDIATE_REVIEW must not equal the raw trigger count -- this is the flood CIO review rejected",
        )
        self.assertLessEqual(
            immediate_count, 10,
            f"IMMEDIATE_REVIEW candidate count ({immediate_count}) is too high for a human to actually review",
        )

    def test_every_raw_trigger_is_accounted_for_in_either_review_queue_or_expired(self):
        # Nothing silently vanishes between raw detection and the tiered
        # output -- every ACTIVE raw trigger's subject appears in exactly
        # one review_queue entry.
        raw_subjects = {r["subject"] for r in self.crypto["raw_trigger_ledger"]}
        queue_subjects = {r["subject"] for r in self.crypto["review_queue"]}
        self.assertEqual(raw_subjects, queue_subjects)

    def test_only_immediate_review_candidates_require_human_review(self):
        for r in self.crypto["watch_review"] + self.crypto["observation_only"]:
            self.assertFalse(r["human_review_required"], r["subject"])
        for r in self.crypto["immediate_review"]:
            self.assertTrue(r["human_review_required"], r["subject"])

    def test_candidates_without_thesis_or_price_linkage_are_capped_unless_audit_exception(self):
        for r in self.crypto["review_queue"]:
            both_absent = (
                r["thesis_linkage"]["status"] == "NOT_LINKED_THIS_SLICE"
                and r["price_reflection_status"]["status"] == "NOT_LINKED_THIS_SLICE"
            )
            if both_absent and r["tier"] == "IMMEDIATE_REVIEW":
                self.assertTrue(
                    r["audit_confirmed_miss_exception_applied"],
                    f"{r['subject']} reached IMMEDIATE_REVIEW with no linkage and no audit exception -- flood risk",
                )


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
            all_records = (
                market_result["raw_trigger_ledger"] + market_result["expired_triggers"]
                + market_result["review_queue"]
            )
            for record in all_records:
                checked += 1
                auth = record["authority"]
                self.assertIsNone(auth["trade_proposal"])
                for key in ("stage_promotion_authority", "buy_authority", "action_authority",
                            "order_authority", "production_authority", "trading_authority"):
                    self.assertFalse(auth[key], (record["subject"], key))
                self.assertEqual(auth["capital"], 0)
        self.assertGreater(checked, 0, "sanity: real evidence should produce at least one record")

    def test_p5_not_pass_never_promoted_anywhere_in_this_module(self):
        # This module has no P5 concept of its own (it never evaluates a
        # Rule), so the invariant is structural: nothing it produces can
        # ever be an Action Proposal/Shadow Entry/Order regardless of P5 --
        # verified by the authority block being unconditionally all-False.
        for market_result in self.report["by_market"].values():
            for record in market_result["review_queue"]:
                self.assertEqual(record["authority"]["action_authority"], False)
                self.assertEqual(record["authority"]["order_authority"], False)
                self.assertIsNone(record["authority"]["trade_proposal"])


class LookaheadSweepTests(unittest.TestCase):
    """Anti-lookahead regression at the orchestrator/report level -- extends
    PR #210's `test_replay_lookahead_gate.py` pattern to the OPERATIONAL
    (episode/renewal/consolidation) shape."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_raw_records_evidence_available_at_is_never_after_detected_at(self):
        checked = 0
        for market_result in self.report["by_market"].values():
            for record in market_result["raw_trigger_ledger"]:
                checked += 1
                self.assertLessEqual(record["evidence_available_at"], record["detected_at"], record)
                self.assertLessEqual(record["first_detected_at"], record["detected_at"], record)
        self.assertGreater(checked, 0)

    def test_subject_candidates_first_detected_at_never_after_detected_at(self):
        checked = 0
        for market_result in self.report["by_market"].values():
            for record in market_result["review_queue"]:
                checked += 1
                self.assertLessEqual(record["first_detected_at"], record["detected_at"], record)
        self.assertGreater(checked, 0)

    def test_no_record_detected_at_is_after_its_markets_as_of_evidence_date(self):
        checked = 0
        for market, market_result in self.report["by_market"].items():
            as_of = market_result["as_of_evidence_date"]
            if as_of is None:
                continue
            all_records = market_result["review_queue"] + market_result["expired_triggers"]
            for record in all_records:
                checked += 1
                detected = record.get("detected_at") or record.get("last_detected_at")
                self.assertLessEqual(detected, as_of, (market, record["subject"]))
        self.assertGreater(checked, 0)

    def test_reference_forward_metrics_never_price_an_entry_at_or_before_its_decision_date(self):
        checked = 0
        for market_result in self.report["by_market"].values():
            for record in market_result["review_queue"]:
                for key in ("reference_forward_metrics_first_detection", "reference_forward_metrics_latest_detection"):
                    fm = record.get(key)
                    if fm and fm.get("status") == "OK":
                        checked += 1
                        self.assertGreater(fm["hypothetical_entry_at"], fm["decision_date"], record)
        self.assertGreater(checked, 0, "sanity: at least one reference metric should be OK-graded")


class BriefingSectionShapeTests(unittest.TestCase):
    """Item 6's standalone artifact -- consumed by
    `briefing/daily_orchestrator.py`'s `DYNAMIC_CLOCK` component."""

    def test_briefing_section_has_all_required_keys_per_market(self):
        report = run()
        section = build_briefing_section(report)
        for market in ("BTC", "KOREA", "CRYPTO"):
            m = section["markets"][market]
            for key in ("new_triggers", "immediate_review", "watch_review",
                        "observation_only_count", "expired_triggers", "not_computable_trigger_types"):
                self.assertIn(key, m, (market, key))

    def test_briefing_section_immediate_review_does_not_flood(self):
        report = run()
        section = build_briefing_section(report)
        for market, m in section["markets"].items():
            self.assertLessEqual(len(m["immediate_review"]), 10, market)


if __name__ == "__main__":
    unittest.main()
