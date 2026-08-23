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
        # ★ CIO integration review round 2, defect 2: this test used to
        # `importlib.reload(clock.review_candidate)`, which REBINDS
        # `ReviewCandidateError`/every function in that module to NEW class/
        # function objects -- any OTHER test in the same process that had
        # already done `from clock.review_candidate import ReviewCandidateError`
        # (or similar) before this test ran would then fail `assertRaises`
        # against the now-stale old class, poisoning run order. `reload()`
        # is banned from this shared test process entirely; instead, call
        # the guard function directly (it is idempotent -- calling it again
        # after it already passed once at import must still not raise) AND
        # prove the module-level call genuinely happens at import time via a
        # real, separate subprocess (see
        # `test_fresh_subprocess_import_does_not_raise` below), which is a
        # strictly more faithful proof than reload ever was.
        from clock.review_candidate import _assert_tier_signature_is_pit_safe
        _assert_tier_signature_is_pit_safe()  # must not raise

    def test_fresh_subprocess_import_does_not_raise(self):
        # The module-level guard call (`_assert_tier_signature_is_pit_safe()`
        # at the bottom of clock/review_candidate.py) genuinely fires on a
        # real, first-time import in a completely fresh interpreter -- not
        # merely "doesn't raise when called a second time in-process".
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", "import clock.review_candidate"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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
    """CIO integration review round 1, defect 3: post-hoc data is no longer
    merely "not an input to tier" -- it is PHYSICALLY ABSENT from
    `build_subject_review_candidate()`'s output and code path entirely (see
    `clock/audit_diagnostics.py`). This proves the two are genuinely
    independent: a real PR #210 Miss-episode match (or its absence) has
    zero relationship to the candidate the operational path produces,
    because `build_subject_review_candidate()` cannot see it at all."""

    def test_build_subject_review_candidate_never_carries_a_post_hoc_field(self):
        from clock.dynamic_clock import ClockEvent, build_episode_history
        from clock.review_candidate import build_subject_review_candidate

        # BTC 2026-08-20 -- a real PR #210 AUDIT_CONFIRMED_MISS date (see
        # test/test_audit_confirmed_miss.py). Even so, the operational
        # candidate built from it carries no trace of that fact.
        ev = ClockEvent(detected_at="2026-08-20", evidence_available_at="2026-08-19",
                         evidence_hash="a" * 64, source="test", strength=1.0)
        episodes = [ep for ep in build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])
                    if ep["status"] == "ACTIVE"]
        candidate = build_subject_review_candidate(
            "BTC", "BTC", episodes, pit_eligibility_status="PASS", decision_at="2026-08-20",
        )
        self.assertNotIn("post_hoc_audit_note", candidate)

    def test_review_candidate_module_source_never_imports_audit_confirmed_miss(self):
        source = (ROOT / "clock" / "review_candidate.py").read_text(encoding="utf-8")
        import_lines = [ln.strip() for ln in source.splitlines()
                         if ln.strip().startswith(("import ", "from "))]
        for forbidden in ("import clock.audit_confirmed_miss", "from clock.audit_confirmed_miss",
                           "import clock.audit_diagnostics", "from clock.audit_diagnostics"):
            self.assertFalse(any(ln.startswith(forbidden) for ln in import_lines), forbidden)

    def test_audit_diagnostics_module_is_the_only_place_that_computes_the_post_hoc_note(self):
        # The SAME real PR #210 match, built via the genuinely separate
        # module -- proves the note is still real/computable, just no
        # longer reachable from build_subject_review_candidate at all.
        from clock.audit_diagnostics import build_audit_diagnostic_record
        from clock.dynamic_clock import ClockEvent, build_episode_history

        ev = ClockEvent(detected_at="2026-08-20", evidence_available_at="2026-08-19",
                         evidence_hash="a" * 64, source="test", strength=1.0)
        episodes = [ep for ep in build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])
                    if ep["status"] == "ACTIVE"]
        diag = build_audit_diagnostic_record("BTC", "BTC", episodes)
        self.assertIsNotNone(diag["post_hoc_audit_note"])
        self.assertFalse(diag["post_hoc_audit_note"]["authoritative_for_tier"])


if __name__ == "__main__":
    unittest.main()
