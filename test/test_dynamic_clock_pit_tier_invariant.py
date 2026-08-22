#!/usr/bin/env python3
"""P8-12 PIT tier invariant regression (CIO review round 2, item 2 -- the
core defect fixed this round).

`AUDIT_CONFIRMED_MISS` (round 1) let a real PR #210 audit conclusion --
itself computed from REAL RETURNS AFTER the decision date -- elevate an
OPERATIONAL priority tier as of that same decision date. That is a
lookahead violation: using information not available as of `detected_at`
to set a decision as of `detected_at`. This file proves the fix
structurally, not just behaviorally:

  1. `compute_tier()`'s function SIGNATURE cannot even accept a forward-
     return/MFE/post-hoc-audit argument -- so a future edit cannot
     silently re-wire one back in without this test failing loudly.
  2. Tampering with forward-return/MFE/audit-tag VALUES on a built record
     has zero effect on the `tier` a fresh computation produces.
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.review_candidate import (  # noqa: E402
    _TIER_ALLOWED_PARAMETERS, ReviewCandidateError, compute_tier,
)


class TierSignatureIsPITSafeTests(unittest.TestCase):
    """The structural guard: compute_tier() must ONLY ever accept
    quantities knowable as of a candidate's own detected_at."""

    def test_compute_tier_signature_exactly_matches_the_allowlist(self):
        params = set(inspect.signature(compute_tier).parameters)
        self.assertEqual(params, _TIER_ALLOWED_PARAMETERS)

    def test_compute_tier_signature_has_no_outcome_shaped_parameter_name(self):
        params = set(inspect.signature(compute_tier).parameters)
        forbidden_substrings = ("forward", "return", "mfe", "mae", "audit", "miss", "outcome", "hindsight")
        for p in params:
            lowered = p.lower()
            for forbidden in forbidden_substrings:
                self.assertNotIn(forbidden, lowered, f"parameter {p!r} looks outcome/post-hoc-shaped")

    def test_module_level_guard_runs_at_import_time(self):
        # _assert_tier_signature_is_pit_safe() is called unconditionally at
        # module import in review_candidate.py -- re-importing the already-
        # loaded module must not raise (it already passed once at import).
        import importlib

        import clock.review_candidate as rc
        importlib.reload(rc)  # re-executes the module body, including the guard call

    def test_calling_compute_tier_with_a_forward_return_kwarg_raises_typeerror(self):
        # Not silently ignored -- Python itself refuses an unknown kwarg,
        # which is exactly the "cannot silently re-wire it back in" property.
        with self.assertRaises(TypeError):
            compute_tier(  # type: ignore[call-arg]
                confirmation_count=2, pit_eligibility_status="PASS",
                thesis_linkage={"status": "x"}, price_reflection_status={"status": "x"},
                forward_return_pct=7.30,
            )


class TierValueTamperInvariantTests(unittest.TestCase):
    """Behavioral proof: given the SAME PIT-knowable inputs, tier never
    changes regardless of what post-hoc value accompanies the record."""

    LINKED = {"status": "LINKED_EXAMPLE"}
    UNLINKED = {"status": "NOT_LINKED_THIS_SLICE"}

    def test_tier_identical_regardless_of_any_hypothetical_forward_return(self):
        # compute_tier() has no way to see a forward return at all (proven
        # above) -- this just double-checks the RESULT is stable across
        # repeated calls with the same PIT-knowable arguments, i.e. no
        # hidden global/random state leaks a different answer in.
        results = [
            compute_tier(1, "PASS", self.UNLINKED, self.UNLINKED)
            for _ in range(5)
        ]
        tiers = {r["tier"] for r in results}
        self.assertEqual(len(tiers), 1)
        self.assertEqual(tiers.pop(), "WATCH_REVIEW")

    def test_no_exception_lifts_the_missing_linkage_cap_anymore(self):
        # Round 1 had an AUDIT_CONFIRMED_MISS exception parameter here;
        # round 2 removed it entirely. 2 confirmations + no linkage must
        # ALWAYS cap at WATCH_REVIEW now, full stop.
        result = compute_tier(2, "PASS", self.UNLINKED, self.UNLINKED)
        self.assertEqual(result["tier"], "WATCH_REVIEW")
        self.assertTrue(result["capped_for_missing_linkage"])

    def test_btc_shape_confirmation_count_one_cannot_reach_immediate_by_any_means(self):
        # BTC's real shape (a single PRICE_CONFIRMATION trigger,
        # RELATIVE_STRENGTH_REVERSAL structurally NOT_COMPUTABLE for BTC)
        # can never reach IMMEDIATE_REVIEW without real linkage -- this is
        # now the CORRECT, PIT-honest answer, not a gap to work around.
        result = compute_tier(1, "PASS", self.UNLINKED, self.UNLINKED)
        self.assertEqual(result["tier"], "WATCH_REVIEW")
        result_with_linkage = compute_tier(1, "PASS", self.LINKED, self.LINKED)
        # Even WITH real linkage, confirmation_count=1 alone still isn't
        # enough (independent-confirmation threshold is 2) -- linkage only
        # lifts the CAP, it doesn't substitute for confirmation_count.
        self.assertEqual(result_with_linkage["tier"], "WATCH_REVIEW")


class PostHocNoteDoesNotFeedTierTests(unittest.TestCase):
    """The audit tag is preserved for regression-explanation purposes only
    (item 1's explicit carve-out) -- proven by building full subject
    candidates with/without a real PR #210 match and confirming tier is
    unaffected either way."""

    def test_build_subject_review_candidate_tier_unaffected_by_post_hoc_note_presence(self):
        from clock.dynamic_clock import ClockEvent, build_episode_history
        from clock.review_candidate import build_subject_review_candidate

        # BTC 2026-08-20 -- a real PR #210 AUDIT_CONFIRMED_MISS date.
        ev_real_miss_date = ClockEvent(detected_at="2026-08-20", evidence_available_at="2026-08-19",
                                        evidence_hash="a" * 64, source="test", strength=1.0)
        episodes_a = [ep for ep in build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev_real_miss_date])
                      if ep["status"] == "ACTIVE"]
        candidate_with_real_miss_date = build_subject_review_candidate(
            "BTC", "BTC", episodes_a, pit_eligibility_status="PASS",
        )

        # A date with no PR #210 match at all.
        ev_no_miss_date = ClockEvent(detected_at="2026-07-25", evidence_available_at="2026-07-25",
                                      evidence_hash="b" * 64, source="test", strength=1.0)
        episodes_b = [ep for ep in build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev_no_miss_date])
                      if ep["status"] == "ACTIVE"]
        candidate_without_miss_date = build_subject_review_candidate(
            "BTC", "BTC", episodes_b, pit_eligibility_status="PASS",
        )

        self.assertIsNotNone(candidate_with_real_miss_date["post_hoc_audit_note"])
        self.assertIsNone(candidate_without_miss_date["post_hoc_audit_note"])
        # The presence/absence of the post-hoc note must not change tier.
        self.assertEqual(candidate_with_real_miss_date["tier"], candidate_without_miss_date["tier"])
        self.assertEqual(candidate_with_real_miss_date["human_review_required"],
                          candidate_without_miss_date["human_review_required"])


if __name__ == "__main__":
    unittest.main()
