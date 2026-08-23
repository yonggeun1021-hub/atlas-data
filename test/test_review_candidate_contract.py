#!/usr/bin/env python3
"""P8-12 output contract regression: raw trigger records (audit trail, no
human_review_required), consolidated subject candidates (tiered, item 3),
the linkage cap + AUDIT_CONFIRMED_MISS exception (item 4), and the
authority hard-false invariant (item 5)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.dynamic_clock import ClockEvent, build_episode_history  # noqa: E402
from clock.review_candidate import (  # noqa: E402
    AUTHORITY_ALL_FALSE, NOT_LINKED, TIER_IMMEDIATE_REVIEW, TIER_OBSERVATION_ONLY, TIER_WATCH_REVIEW,
    ReviewCandidateError, build_expired_record, build_raw_trigger_record, build_subject_review_candidate,
    compute_tier, validate_review_candidate,
)

RAW_REQUIRED_FIELDS = {
    "subject", "market", "trigger_type", "detected_at", "evidence_available_at",
    "source", "evidence_hash", "urgency", "expiry", "next_review_at",
}
SUBJECT_REQUIRED_FIELDS = {
    "subject", "market", "trigger_types", "detected_at", "first_detected_at",
    "thesis_linkage", "price_reflection_status", "urgency", "expiry", "next_review_at", "human_review_required",
}


def _episode(status="ACTIVE", detected_at="2026-08-20", evidence_hash="a" * 64,
             trigger_type="PRICE_CONFIRMATION", subject="BTC", market="BTC"):
    ev = ClockEvent(detected_at=detected_at, evidence_available_at=detected_at,
                     evidence_hash=evidence_hash, source=f"test/fixture#{subject}", strength=1.0)
    episodes = build_episode_history(subject, market, trigger_type, [ev])
    ep = episodes[0]
    if status == "EXPIRED":
        ep = {**ep, "status": "EXPIRED"}
    return ep


class RawRecordFieldTests(unittest.TestCase):
    def test_raw_record_has_required_fields_and_no_human_review_required(self):
        record = build_raw_trigger_record(_episode())
        missing = RAW_REQUIRED_FIELDS - set(record)
        self.assertEqual(missing, set())
        self.assertNotIn("human_review_required", record,
                          "raw per-trigger records must not carry human_review_required -- that is subject-level")

    def test_raw_record_rejects_expired_episode(self):
        with self.assertRaisesRegex(ReviewCandidateError, "EPISODE_NOT_ACTIVE"):
            build_raw_trigger_record(_episode(status="EXPIRED"))

    def test_expired_record_shape(self):
        record = build_expired_record(_episode(status="EXPIRED"))
        self.assertEqual(record["status"], "EXPIRED")
        self.assertEqual(record["authority"], AUTHORITY_ALL_FALSE)


class SubjectCandidateFieldTests(unittest.TestCase):
    def test_subject_candidate_has_required_fields(self):
        candidate = build_subject_review_candidate("BTC", "BTC", [_episode()], pit_eligibility_status="PASS")
        missing = SUBJECT_REQUIRED_FIELDS - set(candidate)
        self.assertEqual(missing, set())


class TierComputationTests(unittest.TestCase):
    """See test_dynamic_clock_pit_tier_invariant.py for the CIO review
    round 2 PIT-safety regression (compute_tier() no longer accepts any
    post-hoc/outcome-shaped argument at all -- not even ignored)."""
    # A CONFIRMATORY linkage (counts toward lifting the cap): status
    # "LINKED" with no threshold_basis at all, or threshold_basis
    # "RATIFIED" -- see _is_confirmatory_linkage.
    LINKED = {"status": "LINKED"}
    UNLINKED = {"status": NOT_LINKED}
    # A real P8-10 link that is NOT confirmatory -- PROVISIONAL basis, the
    # honest state of every real price_reflection link today.
    LINKED_PROVISIONAL = {"status": "LINKED", "price_state": "OVEREXTENDED", "threshold_basis": "PROVISIONAL"}

    def test_pit_ineligible_is_always_observation_only(self):
        result = compute_tier(5, "FAIL", self.LINKED, self.LINKED)
        self.assertEqual(result["tier"], TIER_OBSERVATION_ONLY)
        self.assertFalse(result["human_review_required"])

    def test_two_confirmations_with_real_linkage_is_immediate(self):
        result = compute_tier(2, "PASS", self.LINKED, self.LINKED)
        self.assertEqual(result["tier"], TIER_IMMEDIATE_REVIEW)
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["capped_for_missing_linkage"])

    def test_provisional_overextended_price_reflection_cannot_elevate_tier(self):
        # Integration spec section 8, item 3: a PROVISIONAL OVEREXTENDED
        # price_state must NEVER elevate tier, even though the link itself
        # succeeded and confirmation_count would otherwise qualify.
        result = compute_tier(2, "PASS", self.UNLINKED, self.LINKED_PROVISIONAL)
        self.assertEqual(result["tier"], TIER_WATCH_REVIEW)
        self.assertTrue(result["capped_for_missing_linkage"])
        self.assertFalse(result["human_review_required"])

    def test_provisional_strong_momentum_price_reflection_cannot_elevate_tier(self):
        # Integration spec section 8, item 4: same invariant, different
        # price_state value -- PROVISIONAL is what disqualifies it, not
        # the specific momentum label.
        strong_momentum = {"status": "LINKED", "price_state": "STRONG_MOMENTUM", "threshold_basis": "PROVISIONAL"}
        result = compute_tier(2, "PASS", self.UNLINKED, strong_momentum)
        self.assertEqual(result["tier"], TIER_WATCH_REVIEW)
        self.assertTrue(result["capped_for_missing_linkage"])
        self.assertFalse(result["human_review_required"])

    def test_ratified_price_reflection_would_count_as_confirmatory(self):
        # Forward-looking: IF threshold_basis ever becomes RATIFIED (not
        # true today), the cap correctly lifts.
        ratified = {"status": "LINKED", "price_state": "OVEREXTENDED", "threshold_basis": "RATIFIED"}
        result = compute_tier(2, "PASS", self.UNLINKED, ratified)
        self.assertEqual(result["tier"], TIER_IMMEDIATE_REVIEW)

    def test_two_confirmations_with_no_linkage_is_capped_to_watch(self):
        # Both linkages absent -> never IMMEDIATE_REVIEW, no exception of
        # any kind (CIO review round 2 removed the AUDIT_CONFIRMED_MISS
        # exception entirely -- see test_dynamic_clock_pit_tier_invariant.py).
        result = compute_tier(2, "PASS", self.UNLINKED, self.UNLINKED)
        self.assertEqual(result["tier"], TIER_WATCH_REVIEW)
        self.assertTrue(result["capped_for_missing_linkage"])
        self.assertFalse(result["human_review_required"])

    def test_single_confirmation_with_no_linkage_is_watch_not_capped_flag(self):
        # Base tier is already WATCH_REVIEW here (not IMMEDIATE_REVIEW), so
        # the "capped" flag should be False -- nothing was downgraded.
        result = compute_tier(1, "PASS", self.UNLINKED, self.UNLINKED)
        self.assertEqual(result["tier"], TIER_WATCH_REVIEW)
        self.assertFalse(result["capped_for_missing_linkage"])

    def test_reason_field_never_mentions_a_percent_or_return(self):
        # Item 8: the reason string must be template-only, never contain a
        # forward-return-shaped figure.
        for args in ((2, "PASS", self.LINKED, self.LINKED),
                     (2, "PASS", self.UNLINKED, self.UNLINKED),
                     (1, "PASS", self.UNLINKED, self.UNLINKED),
                     (1, "FAIL", self.UNLINKED, self.UNLINKED)):
            result = compute_tier(*args)
            self.assertNotIn("%", result["reason"])


class SubjectCandidateConsolidationTests(unittest.TestCase):
    def test_multiple_trigger_types_consolidate_into_one_candidate(self):
        ep1 = _episode(trigger_type="PRICE_CONFIRMATION", subject="005930", market="KOREA")
        ep2 = _episode(trigger_type="FLOW_REVERSAL", subject="005930", market="KOREA", evidence_hash="b" * 64)
        candidate = build_subject_review_candidate(
            "005930", "KOREA", [ep1, ep2], pit_eligibility_status="PASS",
        )
        self.assertEqual(candidate["confirmation_count"], 2)
        self.assertEqual(candidate["trigger_types"], ["FLOW_REVERSAL", "PRICE_CONFIRMATION"])
        self.assertEqual(len(candidate["episode_ids"]), 2)

    def test_rejects_mixed_subject_episodes(self):
        ep1 = _episode(subject="005930", market="KOREA")
        ep2 = _episode(subject="000660", market="KOREA", evidence_hash="c" * 64)
        with self.assertRaisesRegex(ReviewCandidateError, "EPISODE_SUBJECT_MARKET_MISMATCH"):
            build_subject_review_candidate("005930", "KOREA", [ep1, ep2], pit_eligibility_status="PASS")

    def test_rejects_expired_episode_in_the_group(self):
        ep1 = _episode()
        ep2 = _episode(status="EXPIRED", evidence_hash="d" * 64)
        with self.assertRaisesRegex(ReviewCandidateError, "ALL_EPISODES_MUST_BE_ACTIVE"):
            build_subject_review_candidate("BTC", "BTC", [ep1, ep2], pit_eligibility_status="PASS")

    def test_empty_episode_list_rejected(self):
        with self.assertRaisesRegex(ReviewCandidateError, "NO_ACTIVE_EPISODES_FOR_SUBJECT"):
            build_subject_review_candidate("BTC", "BTC", [], pit_eligibility_status="PASS")


class NonCouplingTests(unittest.TestCase):
    def test_review_candidate_module_never_imports_decision_or_shadow(self):
        source = (ROOT / "clock" / "review_candidate.py").read_text(encoding="utf-8")
        import_lines = [ln.strip() for ln in source.splitlines()
                         if ln.strip().startswith(("import ", "from "))]
        for forbidden in ("import decision", "from decision", "import shadow", "from shadow"):
            self.assertFalse(any(ln.startswith(forbidden) for ln in import_lines), forbidden)

    def test_no_clock_module_actually_imports_briefing_daily_orchestrator(self):
        for path in (ROOT / "clock").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            import_lines = [ln.strip() for ln in source.splitlines()
                             if ln.strip().startswith(("import ", "from "))]
            for ln in import_lines:
                self.assertNotIn("daily_orchestrator", ln, path)


class AuthorityHardFalseTests(unittest.TestCase):
    def test_raw_record_authority_block_is_all_false(self):
        record = build_raw_trigger_record(_episode())
        self.assertEqual(record["authority"], AUTHORITY_ALL_FALSE)

    def test_subject_candidate_authority_block_is_all_false(self):
        candidate = build_subject_review_candidate("BTC", "BTC", [_episode()], pit_eligibility_status="PASS")
        self.assertEqual(candidate["authority"], AUTHORITY_ALL_FALSE)


class ValidationRoundTripTests(unittest.TestCase):
    def _candidate(self):
        return build_subject_review_candidate("BTC", "BTC", [_episode()], pit_eligibility_status="PASS")

    def test_valid_candidate_round_trips(self):
        candidate = self._candidate()
        self.assertEqual(validate_review_candidate(candidate), candidate)

    def test_tampered_authority_block_rejected(self):
        candidate = self._candidate()
        tampered = {**candidate, "authority": {**candidate["authority"], "trading_authority": True}}
        with self.assertRaisesRegex(ReviewCandidateError, "AUTHORITY_BLOCK_TAMPERED"):
            validate_review_candidate(tampered)

    def test_human_review_required_tier_mismatch_rejected(self):
        candidate = self._candidate()
        tampered = {**candidate, "human_review_required": not candidate["human_review_required"]}
        with self.assertRaisesRegex(ReviewCandidateError, "HUMAN_REVIEW_REQUIRED_TIER_MISMATCH"):
            validate_review_candidate(tampered)

    def test_tampered_field_without_rehash_rejected(self):
        candidate = self._candidate()
        tampered = {**candidate, "urgency": "LOW"}
        with self.assertRaisesRegex(ReviewCandidateError, "RECORD_HASH_MISMATCH"):
            validate_review_candidate(tampered)


class DeterminismTests(unittest.TestCase):
    def test_candidate_hash_is_deterministic(self):
        c1 = build_subject_review_candidate("BTC", "BTC", [_episode()], pit_eligibility_status="PASS")
        c2 = build_subject_review_candidate("BTC", "BTC", [_episode()], pit_eligibility_status="PASS")
        self.assertEqual(c1["record_hash"], c2["record_hash"])
        self.assertEqual(c1["candidate_id"], c2["candidate_id"])


if __name__ == "__main__":
    unittest.main()
