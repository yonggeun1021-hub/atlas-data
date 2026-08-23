#!/usr/bin/env python3
"""P8-12 end-to-end regression against real committed repo evidence: the
BTC 2026-08-20 regression case (item 4/9), the candidate-flood fix (CIO
review round 1), the PIT-safe tiering fix (CIO review round 2), determinism,
and a full anti-lookahead sweep of everything the orchestrator produces."""
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
    Item 9 requires this to remain present in the Review Queue after
    triage -- checked directly against the real report.

    ★ CIO review round 2: BTC must land at WATCH_REVIEW, NOT
      IMMEDIATE_REVIEW, as of 2026-08-20 itself -- round 1's
      AUDIT_CONFIRMED_MISS exception used PR #210's own retrospective audit
      (computed from REAL RETURNS AFTER the decision date) to elevate
      operational priority, which is a PIT lookahead violation. As of
      2026-08-20, Atlas had exactly one tactical trigger
      (confirmation_count=1) and no real thesis/price linkage -- that is
      honestly a WATCH_REVIEW, not a "should have bought this" signal.
      Only PR #210's later audit could tell you it was a Miss."""

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
        # Item 3 (round 1): raw triggers are NEVER dropped, only
        # consolidated/tiered.
        raw = self._btc_raw_price_confirmation()
        opened_dates = {r["opened_at"] for r in raw}
        self.assertIn("2026-08-20", opened_dates)

    def test_the_2026_08_20_episode_reference_metrics_match_pr210s_audited_figure(self):
        # Still computed and preserved for post-hoc/audit purposes -- just
        # never fed into tier (see PitTierInvariantTests below).
        target = next(r for r in self._btc_raw_price_confirmation() if r["opened_at"] == "2026-08-20")
        fm = target["reference_forward_metrics_first_detection"]
        self.assertEqual(fm["status"], "OK")
        self.assertEqual(fm["decision_date"], "2026-08-20")
        self.assertEqual(fm["signal_evaluation_at"], "2026-08-19")
        self.assertEqual(fm["hypothetical_entry_at"], "2026-08-21")
        self.assertAlmostEqual(fm["horizons"]["1"]["forward_return_pct"], 7.2957704805, places=3)

    def test_btc_remains_present_in_the_review_queue_after_triage(self):
        candidate = self._btc_subject_candidate()
        self.assertEqual(candidate["subject"], "BTC")

    def test_btc_is_watch_review_not_immediate_pit_correct(self):
        # The corrected, honest answer (CIO review round 2, item 3).
        candidate = self._btc_subject_candidate()
        self.assertEqual(candidate["confirmation_count"], 1)
        self.assertEqual(candidate["tier"], "WATCH_REVIEW")
        self.assertFalse(candidate["human_review_required"])

    def test_btc_still_carries_a_post_hoc_audit_note_for_regression_explanation(self):
        # The real PR #210 finding is still visible -- just clearly
        # labeled as non-authoritative for tier.
        candidate = self._btc_subject_candidate()
        self.assertIsNotNone(candidate["post_hoc_audit_note"])
        self.assertFalse(candidate["post_hoc_audit_note"]["authoritative_for_tier"])

    def test_the_candidate_carries_no_authority(self):
        candidate = self._btc_subject_candidate()
        self.assertIsNone(candidate["authority"]["trade_proposal"])
        self.assertFalse(candidate["authority"]["trading_authority"])
        self.assertFalse(candidate["authority"]["buy_authority"])


class CorrectedTierCountsTests(unittest.TestCase):
    """CIO review round 2's explicit required check, still true after the
    real P8-10 integration: with the AUDIT_CONFIRMED_MISS exception removed
    and no CONFIRMATORY (RATIFIED-basis) thesis/price linkage today (P8-10's
    real links all carry threshold_basis=PROVISIONAL, which never counts --
    see clock/review_candidate.py's _is_confirmatory_linkage),
    IMMEDIATE_REVIEW must be 0 everywhere."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_immediate_review_is_zero_in_every_market(self):
        for market, m in self.report["by_market"].items():
            self.assertEqual(
                len(m["immediate_review"]), 0,
                f"{market} has {len(m['immediate_review'])} IMMEDIATE_REVIEW candidates -- expected 0 "
                "while price_reflection's threshold_basis stays PROVISIONAL and no thesis linkage exists",
            )

    def test_no_candidate_reaches_immediate_review_without_real_linkage(self):
        for market_result in self.report["by_market"].values():
            for r in market_result["review_queue"]:
                if r["tier"] == "IMMEDIATE_REVIEW":
                    self.assertNotEqual(r["thesis_linkage"]["status"], "NOT_LINKED_THIS_SLICE")
                    self.assertNotEqual(r["price_reflection_status"]["status"], "NOT_LINKED_THIS_SLICE")


class CandidateFloodRegressionTests(unittest.TestCase):
    """CIO review round 1 on PR #211: Crypto alone previously produced 99
    active review candidates (one per raw trigger), all
    human_review_required=True. Asserts BOTH the raw count (must stay high/
    complete) AND the post-triage IMMEDIATE_REVIEW count (must stay small,
    now provably 0) together, so triage logic can't silently regress back
    to flooding without either number moving in a way this test would
    catch."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()
        cls.crypto = cls.report["by_market"]["CRYPTO"]

    def test_raw_trigger_ledger_is_not_truncated(self):
        self.assertGreater(self.crypto["raw_trigger_count"], 50,
                            "raw trigger ledger looks truncated relative to the real evidence population")

    def test_immediate_review_does_not_flood(self):
        immediate_count = len(self.crypto["immediate_review"])
        raw_count = self.crypto["raw_trigger_count"]
        self.assertLess(immediate_count, raw_count)
        self.assertLessEqual(immediate_count, 10)

    def test_every_raw_trigger_is_accounted_for_in_either_review_queue_or_expired(self):
        raw_subjects = {r["subject"] for r in self.crypto["raw_trigger_ledger"]}
        queue_subjects = {r["subject"] for r in self.crypto["review_queue"]}
        self.assertEqual(raw_subjects, queue_subjects)

    def test_only_immediate_review_candidates_require_human_review(self):
        for r in self.crypto["watch_review"] + self.crypto["observation_only"]:
            self.assertFalse(r["human_review_required"], r["subject"])
        for r in self.crypto["immediate_review"]:
            self.assertTrue(r["human_review_required"], r["subject"])

    def test_candidates_without_thesis_or_price_linkage_are_always_capped_no_exception(self):
        # CIO review round 2: there is NO exception left that can lift this
        # cap -- not even a real PR #210-confirmed Miss.
        for r in self.crypto["review_queue"]:
            both_absent = (
                r["thesis_linkage"]["status"] == "NOT_LINKED_THIS_SLICE"
                and r["price_reflection_status"]["status"] == "NOT_LINKED_THIS_SLICE"
            )
            if both_absent:
                self.assertNotEqual(r["tier"], "IMMEDIATE_REVIEW", r["subject"])


class RealP810IntegrationTests(unittest.TestCase):
    """P8-10 <-> P8-12 integration (post PR #212 merge, 2026-08-23 locked
    spec) verified against REAL current evidence across all three markets
    -- item 3.1's structural invariant re-checked end-to-end, and the
    confirmatory-linkage cap re-verified against every real candidate this
    integration actually produces (not just the CRYPTO-only slice
    CandidateFloodRegressionTests covers, since BTC/KOREA now get real
    LINKED-but-PROVISIONAL price_reflection_status)."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_reflection_status_is_unknown_for_every_candidate_in_every_market(self):
        # Item 3.1 + section 9's explicit requirement: count where
        # reflection_status != UNKNOWN MUST be 0.
        checked = 0
        violations = 0
        for market_result in self.report["by_market"].values():
            for r in market_result["review_queue"]:
                pr = r["price_reflection_status"]
                if pr.get("status") == "LINKED":
                    checked += 1
                    if pr.get("reflection_status") != "UNKNOWN":
                        violations += 1
        self.assertEqual(violations, 0)
        self.assertGreater(checked, 0, "sanity: at least one real subject should have a LINKED price_reflection")

    def test_no_candidate_reaches_immediate_review_without_a_confirmatory_linkage_anywhere(self):
        from clock.review_candidate import _is_confirmatory_linkage

        checked = 0
        for market_result in self.report["by_market"].values():
            for r in market_result["review_queue"]:
                checked += 1
                confirmatory = (
                    _is_confirmatory_linkage(r["thesis_linkage"])
                    or _is_confirmatory_linkage(r["price_reflection_status"])
                )
                if r["tier"] == "IMMEDIATE_REVIEW":
                    self.assertTrue(confirmatory, r["subject"])
                if not confirmatory:
                    self.assertNotEqual(r["tier"], "IMMEDIATE_REVIEW", r["subject"])
        self.assertGreater(checked, 0)

    def test_btc_real_overextended_provisional_link_does_not_elevate_it(self):
        btc = next(r for r in self.report["by_market"]["BTC"]["review_queue"] if r["subject"] == "BTC")
        pr = btc["price_reflection_status"]
        self.assertEqual(pr["status"], "LINKED")
        self.assertEqual(pr["threshold_basis"], "PROVISIONAL")
        self.assertNotEqual(btc["tier"], "IMMEDIATE_REVIEW")

    def test_korea_real_linked_subjects_stay_capped(self):
        korea = self.report["by_market"]["KOREA"]["review_queue"]
        linked = [r for r in korea if r["price_reflection_status"].get("status") == "LINKED"]
        self.assertGreater(len(linked), 0, "sanity: at least one real Korea subject should link")
        for r in linked:
            self.assertEqual(r["price_reflection_status"]["threshold_basis"], "PROVISIONAL")
            self.assertNotEqual(r["tier"], "IMMEDIATE_REVIEW", r["subject"])

    def test_crypto_altcoins_are_honestly_not_supported_not_fabricated(self):
        crypto = self.report["by_market"]["CRYPTO"]["review_queue"]
        self.assertGreater(len(crypto), 0)
        for r in crypto:
            self.assertEqual(r["price_reflection_status"]["status"], "NOT_LINKED_THIS_SLICE")


class PitTierInvariantTests(unittest.TestCase):
    """CIO review round 2, item 2: tampering with forward-return/MFE/
    post-hoc-audit fields must have ZERO effect on tier -- proven here at
    the full-report level (see test_dynamic_clock_pit_tier_invariant.py for
    the unit-level, signature-based structural guarantee)."""

    @classmethod
    def setUpClass(cls):
        cls.report = run()

    def test_tier_is_independent_of_reference_forward_metrics_value(self):
        from clock.dynamic_clock import ClockEvent, build_episode_history
        from clock.review_candidate import build_subject_review_candidate

        ev = ClockEvent(detected_at="2026-08-20", evidence_available_at="2026-08-19",
                         evidence_hash="a" * 64, source="test", strength=1.0)
        episodes = [ep for ep in build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])
                    if ep["status"] == "ACTIVE"]

        # Rebuild the SAME episode with wildly different (fabricated)
        # forward-metrics values attached and confirm tier/
        # human_review_required are byte-identical either way.
        baseline = build_subject_review_candidate(
            "BTC", "BTC", episodes, pit_eligibility_status="PASS",
            reference_forward_metrics_first_detection=None,
        )
        tampered = build_subject_review_candidate(
            "BTC", "BTC", episodes, pit_eligibility_status="PASS",
            reference_forward_metrics_first_detection={
                "status": "OK", "horizons": {"1": {"forward_return_pct": 999999.0}},
            },
        )
        self.assertEqual(baseline["tier"], tampered["tier"])
        self.assertEqual(baseline["human_review_required"], tampered["human_review_required"])


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

    def test_no_record_detected_at_is_after_its_markets_evidence_as_of(self):
        checked = 0
        for market, market_result in self.report["by_market"].items():
            as_of = market_result["evidence_as_of"]
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
    """Item 6/8's standalone artifact -- consumed by
    `briefing/daily_orchestrator.py`'s `DYNAMIC_CLOCK` component. Must show
    ONLY the subject-level queue (never the raw ledger) with per-tier
    counts and reasons, and no forward-return/post-hoc figure anywhere."""

    def test_briefing_section_has_all_required_keys_per_market(self):
        report = run()
        section = build_briefing_section(report)
        self.assertIn("policy_approval_status", section)
        for market in ("BTC", "KOREA", "CRYPTO"):
            m = section["markets"][market]
            for key in ("new_triggers", "immediate_review", "watch_review",
                        "observation_only_count", "expired_triggers",
                        "not_computable_trigger_types", "tier_counts", "calendar_confidence"):
                self.assertIn(key, m, (market, key))

    def test_briefing_section_immediate_review_does_not_flood(self):
        report = run()
        section = build_briefing_section(report)
        for market, m in section["markets"].items():
            self.assertLessEqual(len(m["immediate_review"]), 10, market)

    def test_briefing_section_never_carries_a_forward_return_figure(self):
        # Item 8: post-hoc/forward returns must never appear as the stated
        # reason for an operational recommendation -- checked structurally
        # by scanning the whole section for the diagnostic field names.
        import json
        report = run()
        section = build_briefing_section(report)
        blob = json.dumps(section)
        for forbidden in ("forward_return_pct", "reference_forward_metrics", "post_hoc_audit_note", "mfe_pct", "mae_pct"):
            self.assertNotIn(forbidden, blob)

    def test_immediate_review_candidates_carry_a_template_reason_not_a_figure(self):
        report = run()
        section = build_briefing_section(report)
        for m in section["markets"].values():
            for c in m["immediate_review"] + m["watch_review"]:
                self.assertIn("reason", c)
                self.assertNotIn("%", c["reason"])

    def test_policy_approval_status_is_provisional(self):
        report = run()
        section = build_briefing_section(report)
        self.assertEqual(section["policy_approval_status"], "PROVISIONAL_CIO_MVP")

    def test_briefing_candidates_carry_the_exact_section_7_field_allowlist(self):
        # Integration spec section 7: subject, tier, trigger_types+
        # confirmation_count, price_state, reflection_status, data_state,
        # threshold_basis, a data-as-of timestamp, reason,
        # authority=REVIEW_ONLY, money_action=NONE.
        required = {
            "subject", "tier", "trigger_types", "confirmation_count", "price_state",
            "reflection_status", "data_state", "threshold_basis", "price_as_of",
            "next_review_at", "reason", "authority", "money_action",
        }
        report = run()
        section = build_briefing_section(report)
        checked = 0
        for m in section["markets"].values():
            for c in m["immediate_review"] + m["watch_review"]:
                checked += 1
                self.assertEqual(set(c), required, c["subject"])
        self.assertGreater(checked, 0)

    def test_briefing_candidates_authority_is_always_review_only_money_action_none(self):
        report = run()
        section = build_briefing_section(report)
        checked = 0
        for m in section["markets"].values():
            for c in m["immediate_review"] + m["watch_review"]:
                checked += 1
                self.assertEqual(c["authority"], "REVIEW_ONLY", c["subject"])
                self.assertEqual(c["money_action"], "NONE", c["subject"])
        self.assertGreater(checked, 0)

    def test_briefing_candidates_reflection_status_is_always_unknown(self):
        report = run()
        section = build_briefing_section(report)
        checked = 0
        for m in section["markets"].values():
            for c in m["immediate_review"] + m["watch_review"]:
                checked += 1
                self.assertEqual(c["reflection_status"], "UNKNOWN", c["subject"])
        self.assertGreater(checked, 0)

    def test_briefing_watch_review_candidates_are_shown_not_only_immediate(self):
        # Since IMMEDIATE_REVIEW is 0 everywhere today, WATCH_REVIEW must
        # actually be rendered -- otherwise already-moving subjects like
        # BTC/삼성전자/SK하이닉스 fall through the briefing's cracks
        # (section 2's explicit purpose).
        report = run()
        section = build_briefing_section(report)
        total_watch = sum(len(m["watch_review"]) for m in section["markets"].values())
        self.assertGreater(total_watch, 0)
        btc_subjects = {c["subject"] for c in section["markets"]["BTC"]["watch_review"]}
        self.assertIn("BTC", btc_subjects)

    def test_briefing_section_never_contains_a_buy_sell_entry_order_value(self):
        # Section 8: Buy/Entry/Order-style language must never appear.
        # Checked as exact FIELD VALUES (not a blind prose-substring ban --
        # this module's own defensive notes legitimately say "never a Buy
        # signal", which must not itself be flagged).
        forbidden_values = {"BUY", "SELL", "ENTRY", "ORDER", "PLACE_ORDER", "BUY_NOW", "ENTRY_APPROVED"}
        report = run()
        section = build_briefing_section(report)

        def _walk(value):
            if isinstance(value, dict):
                for v in value.values():
                    yield from _walk(v)
            elif isinstance(value, list):
                for v in value:
                    yield from _walk(v)
            elif isinstance(value, str):
                yield value

        checked = 0
        for v in _walk(section):
            checked += 1
            self.assertNotIn(v, forbidden_values, v)
        self.assertGreater(checked, 0)

    def test_korea_calendar_confidence_is_surfaced_and_unverified(self):
        report = run()
        section = build_briefing_section(report)
        self.assertEqual(section["markets"]["KOREA"]["calendar_confidence"], "UNVERIFIED_NO_HOLIDAY_CALENDAR")


if __name__ == "__main__":
    unittest.main()
