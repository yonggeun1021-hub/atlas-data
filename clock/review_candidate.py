#!/usr/bin/env python3
"""P8-12 output contract: raw trigger audit records + the consolidated,
tiered Human Review Candidate.

★ CIO review round 1 on PR #211 ("candidate flood"): the original version
  emitted one `human_review_required=True` record per ACTIVE
  (subject, trigger_type) episode -- 99 for Crypto alone. Fixed by splitting:

    - `build_raw_trigger_record` / `build_expired_record` -- one record per
      (subject, trigger_type) episode, kept in FULL for audit (nothing is
      ever dropped) but with NO `human_review_required` field --
      reviewability is a SUBJECT-level concept, decided by
      `build_subject_review_candidate`.
    - `build_subject_review_candidate` -- ONE record per subject,
      consolidating every currently-ACTIVE episode across all its trigger
      types into `trigger_types` + `confirmation_count`, then assigning a
      priority TIER (`IMMEDIATE_REVIEW` / `WATCH_REVIEW` /
      `OBSERVATION_ONLY`).

★ CIO review round 2 on PR #211 (PIT LOOKAHEAD VIOLATION, fixed here):
  round 1's tier logic let a real PR #210 audit finding
  (`AUDIT_CONFIRMED_MISS`, itself computed from REAL RETURNS AFTER the
  decision date) elevate a candidate to `IMMEDIATE_REVIEW` even when no
  real thesis/price linkage existed. That is a lookahead violation: it used
  information that was NOT available as of `detected_at` to set an
  OPERATIONAL priority as of `detected_at`. `compute_tier()` below no
  longer accepts any post-hoc/outcome-shaped argument AT ALL -- not "accepts
  it but ignores it" (which a future edit could silently re-wire), but
  structurally cannot see it: its signature only takes
  `confirmation_count` / `pit_eligibility_status` / `thesis_linkage` /
  `price_reflection_status`, all of which are the same quantities knowable
  strictly as of `detected_at`. See `test_dynamic_clock_pit_tier_invariant.py`
  for a signature-inspection test enforcing this structurally, and a
  tamper test proving forward-return/MFE/audit-tag values have zero effect
  on `tier`.

  PR #210's Miss-episode registry (`clock.audit_confirmed_miss`) is STILL
  read and STILL attached to the record -- but now only as
  `post_hoc_audit_note`, explicitly labeled `authoritative_for_tier: False`,
  for regression-explanation/evaluation purposes only (e.g. "why does this
  specific subject look interesting in hindsight"), never as an input to
  `tier`.

  Real consequence: with no real thesis/price linkage wired yet (item 8 of
  round 1 / item 4 of round 2, deferred until the separate P8-10 PR
  merges), `IMMEDIATE_REVIEW` is currently 0 everywhere, including for BTC
  2026-08-20 -- which is the CORRECT, honest operational answer: as of
  2026-08-20 itself, Atlas had a single tactical PRICE_CONFIRMATION trigger
  and no thesis/price confirmation, which is a `WATCH_REVIEW`, not an
  "obviously should have bought this" signal. Only PR #210's later,
  retrospective audit (using real subsequent returns) could tell you it was
  a Miss -- and using that fact to backdate today's priority is exactly the
  outcome-based reasoning this whole workstream exists to eliminate (see
  the CIO's own framing: "the price went up, so it must be reflected" /
  "we later confirmed it went up, so it should have been flagged at the
  time" are the same category of bug).

★ Deliberate non-coupling (item 4 of round 2, still deferred): `thesis_linkage`
  and `price_reflection_status` are NOT produced by importing
  `decision/forward_thesis.py` (P8-08) or `decision/price_reflection.py`
  (P8-10) -- both owned by a separate PR (P8-10's methodology fixes land
  first; see docs/dynamic_clock_contract.md). Honest placeholders
  (`NOT_LINKED_THIS_SLICE`), never fabricated. When BOTH are absent, a
  candidate is capped at `WATCH_REVIEW` even if its confirmation-based tier
  would otherwise be `IMMEDIATE_REVIEW` -- with NO exception of any kind
  now (round 1's AUDIT_CONFIRMED_MISS exception is fully removed, not
  narrowed).

★ Authority: every record (raw or consolidated) carries an explicit
  `authority` block with every Stage/Buy/Action/Order/Production/trading
  field hard-`False`/`None`. `validate_review_candidate` asserts this
  against `AUTHORITY_ALL_FALSE`.
"""
from __future__ import annotations

import copy
import inspect

from replay.opportunity_trigger import canonical_json, payload_sha256

from clock.audit_confirmed_miss import confirmed_miss_for

NOT_LINKED = "NOT_LINKED_THIS_SLICE"

TIER_IMMEDIATE_REVIEW = "IMMEDIATE_REVIEW"
TIER_WATCH_REVIEW = "WATCH_REVIEW"
TIER_OBSERVATION_ONLY = "OBSERVATION_ONLY"
TIERS = (TIER_IMMEDIATE_REVIEW, TIER_WATCH_REVIEW, TIER_OBSERVATION_ONLY)

# Reused verbatim from replay.action_conversion_gate._condition_2 -- "PASS"
# there is exactly "2 or more distinct trigger types present". This module
# does not invent a different confirmation-count threshold.
INDEPENDENT_CONFIRMATION_THRESHOLD = 2

AUTHORITY_ALL_FALSE = {
    "trade_proposal": None,
    "stage_promotion_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
    "capital": 0,
}


class ReviewCandidateError(ValueError):
    pass


def _authority_block() -> dict:
    return copy.deepcopy(AUTHORITY_ALL_FALSE)


def _linkage_placeholder(owner_note: str) -> dict:
    return {"status": NOT_LINKED, "reason": owner_note}


def _thesis_linkage_placeholder() -> dict:
    return _linkage_placeholder(
        "decoupled from decision/forward_thesis.py (P8-08, separate Forward Alpha WBS row) by design"
    )


def _price_reflection_placeholder() -> dict:
    return _linkage_placeholder(
        "decoupled from decision/price_reflection.py (P8-10, separate Price/PIT WBS row/PR) by design"
    )


def build_raw_trigger_record(episode: dict, *, reference_forward_metrics: dict | None = None,
                              reference_forward_metrics_first_detection: dict | None = None) -> dict:
    """One record per ACTIVE (subject, trigger_type) episode -- the full,
    unfiltered audit trail. NOT a review candidate by itself (no
    `human_review_required` field); see `build_subject_review_candidate`
    for the consolidated, tiered view a human actually reads.

    `reference_forward_metrics_*` are post-hoc diagnostic fields ONLY (see
    module docstring) -- kept here for audit/regression purposes, never
    read by `compute_tier()`."""
    if episode.get("status") != "ACTIVE":
        raise ReviewCandidateError(f"EPISODE_NOT_ACTIVE:{episode.get('status')}")

    latest_evidence = episode["evidence_trail"][-1]
    first_evidence = episode["evidence_trail"][0]

    record = {
        "candidate_id": episode["episode_id"],
        "series_id": episode["series_id"],
        "subject": episode["subject"],
        "market": episode["market"],
        "trigger_type": episode["trigger_type"],
        "detected_at": latest_evidence["detected_at"],
        "evidence_available_at": latest_evidence["evidence_available_at"],
        "first_detected_at": first_evidence["detected_at"],
        "source": latest_evidence["source"],
        "evidence_hash": latest_evidence["evidence_hash"],
        "strength": latest_evidence["strength"],
        "urgency": episode["urgency"],
        "expiry": episode["expiry"],
        "expiry_calendar_confidence": episode.get("expiry_calendar_confidence"),
        "next_review_at": episode["next_review_at"],
        "next_review_at_calendar_confidence": episode.get("next_review_at_calendar_confidence"),
        "renewal_count": episode["renewal_count"],
        "reactivated_from_episode_id": episode["reactivated_from_episode_id"],
        "opened_at": episode["opened_at"],
        "evidence_trail_length": len(episode["evidence_trail"]),
        "authority": _authority_block(),
        "reference_forward_metrics_first_detection": reference_forward_metrics_first_detection,
        "reference_forward_metrics_latest_detection": reference_forward_metrics,
    }
    record["record_hash"] = payload_sha256({k: v for k, v in record.items() if k != "record_hash"})
    return record


def build_expired_record(episode: dict) -> dict:
    """The audit-visible counterpart for a stale, un-renewed episode -- what
    the briefing's 'expired triggers' section reads (see item 6)."""
    if episode.get("status") != "EXPIRED":
        raise ReviewCandidateError(f"EPISODE_NOT_EXPIRED:{episode.get('status')}")
    latest_evidence = episode["evidence_trail"][-1]
    return {
        "candidate_id": episode["episode_id"],
        "series_id": episode["series_id"],
        "subject": episode["subject"],
        "market": episode["market"],
        "trigger_type": episode["trigger_type"],
        "opened_at": episode["opened_at"],
        "last_detected_at": episode["last_detected_at"],
        "expiry": episode["expiry"],
        "renewal_count": episode["renewal_count"],
        "evidence_hash": latest_evidence["evidence_hash"],
        "status": "EXPIRED",
        "authority": _authority_block(),
    }


def _post_hoc_audit_note(subject: str, active_episodes: list[dict]) -> dict | None:
    """PR #210's real, committed Miss-episode registry, read for
    REGRESSION-EXPLANATION/EVALUATION PURPOSES ONLY. `authoritative_for_tier`
    is always `False` and is not a suggestion -- `compute_tier()` cannot
    even accept this value (see its signature)."""
    for ep in active_episodes:
        for ev in ep["evidence_trail"]:
            match = confirmed_miss_for(subject, ev["detected_at"])
            if match is not None:
                return {
                    "authoritative_for_tier": False,
                    "purpose": "post_hoc_regression_explanation_only",
                    "matched_trigger_type": ep["trigger_type"],
                    "matched_detected_at": ev["detected_at"],
                    "pr210_episode_id": match.get("episode_id"),
                    "pr210_root_cause": match.get("root_cause"),
                    "pr210_representative_forward_return_pct": match.get("representative_forward_return_pct"),
                    "pr210_episode_window": [match.get("episode_start_date"), match.get("episode_end_date")],
                }
    return None


def compute_tier(confirmation_count: int, pit_eligibility_status: str,
                  thesis_linkage: dict, price_reflection_status: dict) -> dict:
    """Pure tiering function -- no new arbitrary thresholds, only quantities
    already meaningful elsewhere in this system, and (CIO review round 2)
    structurally ONLY quantities knowable as of the candidate's own
    `detected_at`. This function's signature intentionally has NO parameter
    for forward return, MFE, or any post-hoc/audit-derived value --
    `test_dynamic_clock_pit_tier_invariant.py::TierSignatureIsPITSafeTests`
    asserts this via `inspect.signature`, so a future edit cannot silently
    re-wire a lookahead input back in without that test failing loudly.

    Priority order:
      1. PIT/asset-identity ineligible -> always OBSERVATION_ONLY.
      2. >= INDEPENDENT_CONFIRMATION_THRESHOLD distinct confirming trigger
         types -> IMMEDIATE_REVIEW, UNLESS both thesis_linkage and
         price_reflection_status are absent, in which case it is capped
         down to WATCH_REVIEW. There is NO exception to this cap -- a real,
         already-known-in-hindsight Miss (see `_post_hoc_audit_note`) does
         NOT lift it; only real, as-of-`detected_at` thesis/price linkage
         can (item 4, deferred until the P8-10 PR merges).
      3. Otherwise -> WATCH_REVIEW.
    """
    if pit_eligibility_status != "PASS":
        base_tier = TIER_OBSERVATION_ONLY
    elif confirmation_count >= INDEPENDENT_CONFIRMATION_THRESHOLD:
        base_tier = TIER_IMMEDIATE_REVIEW
    else:
        base_tier = TIER_WATCH_REVIEW

    both_linkages_absent = (
        thesis_linkage.get("status") == NOT_LINKED and price_reflection_status.get("status") == NOT_LINKED
    )
    capped = False
    tier = base_tier
    if both_linkages_absent and base_tier == TIER_IMMEDIATE_REVIEW:
        tier = TIER_WATCH_REVIEW
        capped = True

    return {
        "tier": tier,
        "base_tier": base_tier,
        "capped_for_missing_linkage": capped,
        "human_review_required": tier == TIER_IMMEDIATE_REVIEW,
        "reason": _tier_reason(tier, confirmation_count, pit_eligibility_status, capped),
    }


# Structural PIT guard: enumerate every parameter compute_tier() is allowed
# to have. If a future edit adds a forward-return/MFE/audit/outcome-shaped
# parameter, this set must be updated in the SAME diff a reviewer sees --
# it cannot silently happen as a drive-by change.
_TIER_ALLOWED_PARAMETERS = frozenset({
    "confirmation_count", "pit_eligibility_status", "thesis_linkage", "price_reflection_status",
})


def _tier_reason(tier: str, confirmation_count: int, pit_eligibility_status: str, capped: bool) -> str:
    """Plain-language, template-only (never LLM-generated, never derived
    from a forward return) explanation of WHY a candidate landed in this
    tier -- what item 8 of CIO review round 2 asks the briefing to show
    instead of any post-hoc figure."""
    if tier == TIER_OBSERVATION_ONLY:
        return f"pit_eligibility_status={pit_eligibility_status} (not PASS) -- identity/eligibility not confirmed"
    if tier == TIER_IMMEDIATE_REVIEW:
        return f"confirmation_count={confirmation_count} independent trigger types AND real thesis/price linkage present"
    if capped:
        return (
            f"confirmation_count={confirmation_count} independent trigger types, but capped at WATCH_REVIEW: "
            "no thesis or price-reflection linkage exists yet (P8-10 not connected)"
        )
    return f"confirmation_count={confirmation_count} -- below the independent-confirmation threshold of {INDEPENDENT_CONFIRMATION_THRESHOLD}"


def build_subject_review_candidate(
    subject: str, market: str, active_episodes: list[dict], *,
    pit_eligibility_status: str,
    reference_forward_metrics_first_detection: dict | None = None,
    reference_forward_metrics_latest_detection: dict | None = None,
) -> dict:
    """Consolidates every currently-ACTIVE episode for one subject (across
    all its trigger types) into ONE Human Review Candidate record. This is
    what a human actually reads -- `raw_trigger_ledger` remains the full,
    unconsolidated audit trail (see module docstring).

    `reference_forward_metrics_*` and `post_hoc_audit_note` are attached
    here for audit/diagnostic purposes only -- both are computed AFTER
    `tier` (see `compute_tier()` call below, which never receives them)."""
    if not active_episodes:
        raise ReviewCandidateError("NO_ACTIVE_EPISODES_FOR_SUBJECT")
    if any(ep.get("status") != "ACTIVE" for ep in active_episodes):
        raise ReviewCandidateError("ALL_EPISODES_MUST_BE_ACTIVE")
    if any(ep["subject"] != subject or ep["market"] != market for ep in active_episodes):
        raise ReviewCandidateError("EPISODE_SUBJECT_MARKET_MISMATCH")

    trigger_types = sorted({ep["trigger_type"] for ep in active_episodes})
    confirmation_count = len(trigger_types)
    detected_at = max(ep["last_detected_at"] for ep in active_episodes)
    first_detected_at = min(ep["opened_at"] for ep in active_episodes)
    max_strength = max(ep["evidence_trail"][-1]["strength"] for ep in active_episodes)
    next_review_at = min(ep["next_review_at"] for ep in active_episodes)
    expiry = max(ep["expiry"] for ep in active_episodes)
    calendar_confidence = active_episodes[0].get("next_review_at_calendar_confidence")

    thesis_linkage = _thesis_linkage_placeholder()
    price_reflection_status = _price_reflection_placeholder()

    # ★ PIT-safe by construction: compute_tier() is called with ONLY
    # as-of-detected_at-knowable inputs. The post-hoc audit note is
    # computed SEPARATELY, below, and never passed in.
    tiering = compute_tier(confirmation_count, pit_eligibility_status, thesis_linkage, price_reflection_status)
    post_hoc_note = _post_hoc_audit_note(subject, active_episodes)

    record = {
        "candidate_id": payload_sha256({"subject": subject, "market": market,
                                         "trigger_types": trigger_types, "detected_at": detected_at}),
        "subject": subject,
        "market": market,
        "trigger_types": trigger_types,
        "confirmation_count": confirmation_count,
        "episode_ids": sorted(ep["episode_id"] for ep in active_episodes),
        "detected_at": detected_at,
        "first_detected_at": first_detected_at,
        "max_strength": max_strength,
        "pit_eligibility_status": pit_eligibility_status,
        "urgency": max((ep["urgency"] for ep in active_episodes), key=_URGENCY_RANK.get),
        "expiry": expiry,
        "next_review_at": next_review_at,
        "next_review_at_calendar_confidence": calendar_confidence,
        "thesis_linkage": thesis_linkage,
        "price_reflection_status": price_reflection_status,
        "tier": tiering["tier"],
        "base_tier": tiering["base_tier"],
        "capped_for_missing_linkage": tiering["capped_for_missing_linkage"],
        "reason": tiering["reason"],
        "human_review_required": tiering["human_review_required"],
        "authority": _authority_block(),
        "post_hoc_audit_note": post_hoc_note,
        "reference_forward_metrics_first_detection": reference_forward_metrics_first_detection,
        "reference_forward_metrics_latest_detection": reference_forward_metrics_latest_detection,
        "reference_forward_metrics_note": (
            "post-hoc diagnostic only, reused verbatim from replay.forward_metrics.compute_forward_metrics "
            "(PR #210's anti-backdated-entry invariant) -- NEVER an input to `tier` or an entry authorization; "
            "see compute_tier()'s signature, which cannot accept it"
        ),
    }
    record["record_hash"] = payload_sha256({k: v for k, v in record.items() if k != "record_hash"})
    return record


_URGENCY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def validate_review_candidate(record: dict) -> dict:
    """Re-validates a round-tripped SUBJECT-level candidate: authority block
    must be exactly all-False/None/0, `human_review_required` must equal
    `tier == IMMEDIATE_REVIEW`, and the record_hash must match."""
    if record.get("authority") != AUTHORITY_ALL_FALSE:
        raise ReviewCandidateError("AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE")
    expected_human_review = record.get("tier") == TIER_IMMEDIATE_REVIEW
    if record.get("human_review_required") != expected_human_review:
        raise ReviewCandidateError("HUMAN_REVIEW_REQUIRED_TIER_MISMATCH")
    expected_hash = payload_sha256({k: v for k, v in record.items() if k != "record_hash"})
    if record.get("record_hash") != expected_hash:
        raise ReviewCandidateError("RECORD_HASH_MISMATCH")
    return copy.deepcopy(record)


def canonical(record: dict) -> str:
    return canonical_json(record)


def _assert_tier_signature_is_pit_safe() -> None:
    params = set(inspect.signature(compute_tier).parameters)
    extra = params - _TIER_ALLOWED_PARAMETERS
    if extra:
        raise ReviewCandidateError(f"TIER_SIGNATURE_LOOKAHEAD_RISK:unexpected parameters {extra}")


_assert_tier_signature_is_pit_safe()  # fails at import time, not just in a test
