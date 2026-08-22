#!/usr/bin/env python3
"""P8-12 output contract: raw trigger audit records + the consolidated,
tiered Human Review Candidate.

★ CIO review round 1 on PR #211 ("candidate flood"): the original version
  of this module emitted one `human_review_required=True` record per ACTIVE
  (subject, trigger_type) episode -- 99 for Crypto alone, 95 newly created
  in one run, all `human_review_required=True`. That is unreviewable and
  was rightly rejected. This version keeps a granularity split:

    - `build_raw_trigger_record` / `build_expired_record` -- one record per
      (subject, trigger_type) episode, exactly as before, kept in FULL for
      audit (nothing is ever dropped) but with NO `human_review_required`
      field -- reviewability is a SUBJECT-level concept now, decided by
      `build_subject_review_candidate`.
    - `build_subject_review_candidate` -- ONE record per subject,
      consolidating every currently-ACTIVE episode across all its trigger
      types into `trigger_types` + `confirmation_count`, then assigning a
      priority TIER (`IMMEDIATE_REVIEW` / `WATCH_REVIEW` /
      `OBSERVATION_ONLY`) derived ONLY from quantities that already exist
      elsewhere in this system -- never a newly-invented investment
      threshold:
        * `confirmation_count >= 2` (independent confirmation) reuses the
          EXACT threshold `replay.action_conversion_gate._condition_2`
          already uses for "PASS" (>= 2 distinct trigger types).
        * PIT/asset-identity eligibility reuses
          `replay.asset_identity.asset_identity_status` verbatim.
      `human_review_required=True` is set ONLY on `IMMEDIATE_REVIEW`
      candidates (see item 3's explicit "only IMMEDIATE_REVIEW should carry
      that").

★ Deliberate non-coupling (still true, item 8 not yet started): `thesis_linkage`
  and `price_reflection_status` are NOT produced by importing
  `decision/forward_thesis.py` (P8-08) or `decision/price_reflection.py`
  (P8-10) -- both owned by separate WBS rows/sessions. Honest placeholders
  (`NOT_LINKED_THIS_SLICE`), never fabricated. Per item 4: when BOTH are
  absent, a candidate is capped at `WATCH_REVIEW` even if its
  confirmation-based tier would otherwise be `IMMEDIATE_REVIEW` -- UNLESS
  `clock.audit_confirmed_miss` finds a real PR #210-confirmed Miss episode
  covering one of this candidate's detection dates, in which case the cap
  is lifted but the record carries an explicit `audit_confirmed_miss` tag
  (still never a buy signal -- see the `authority` block, unconditionally
  all-`False`/`None`/`0`).

★ Authority: every record (raw or consolidated) carries an explicit
  `authority` block with every Stage/Buy/Action/Order/Production/trading
  field hard-`False`/`None`. `validate_review_candidate` asserts this
  against `AUTHORITY_ALL_FALSE`.
"""
from __future__ import annotations

import copy

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
    for the consolidated, tiered view a human actually reads."""
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


def _audit_confirmed_miss_tag(subject: str, active_episodes: list[dict]) -> dict | None:
    for ep in active_episodes:
        for ev in ep["evidence_trail"]:
            match = confirmed_miss_for(subject, ev["detected_at"])
            if match is not None:
                return {
                    "matched_trigger_type": ep["trigger_type"],
                    "matched_detected_at": ev["detected_at"],
                    "pr210_episode_id": match.get("episode_id"),
                    "pr210_root_cause": match.get("root_cause"),
                    "pr210_representative_forward_return_pct": match.get("representative_forward_return_pct"),
                    "pr210_episode_window": [match.get("episode_start_date"), match.get("episode_end_date")],
                }
    return None


def compute_tier(confirmation_count: int, pit_eligibility_status: str,
                  thesis_linkage: dict, price_reflection_status: dict,
                  audit_confirmed_miss: dict | None) -> dict:
    """Pure tiering function -- no new arbitrary thresholds, only quantities
    already meaningful elsewhere in this system (see module docstring).

    Priority order:
      1. PIT/asset-identity ineligible -> always OBSERVATION_ONLY. This is a
         safety floor even the AUDIT_CONFIRMED_MISS exception cannot lift.
      2. A real PR #210-confirmed Miss covering this candidate (item 4's
         named exception) -> IMMEDIATE_REVIEW regardless of
         confirmation_count (BTC's real 2026-08-20 signal is a single
         PRICE_CONFIRMATION trigger -- RELATIVE_STRENGTH_REVERSAL is
         structurally NOT_COMPUTABLE for BTC, so confirmation_count can
         never reach 2 for it; without this elevation the one subject this
         whole exception exists for could never actually use it).
      3. Otherwise: >= INDEPENDENT_CONFIRMATION_THRESHOLD distinct
         confirming trigger types -> IMMEDIATE_REVIEW, UNLESS both
         thesis_linkage and price_reflection_status are absent, in which
         case it is capped down to WATCH_REVIEW (item 4's default rule).
      4. Otherwise -> WATCH_REVIEW.
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
    exception_applied = False
    tier = base_tier

    if pit_eligibility_status == "PASS" and audit_confirmed_miss is not None:
        tier = TIER_IMMEDIATE_REVIEW
        exception_applied = True
    elif both_linkages_absent and base_tier == TIER_IMMEDIATE_REVIEW:
        tier = TIER_WATCH_REVIEW
        capped = True

    return {
        "tier": tier,
        "base_tier": base_tier,
        "capped_for_missing_linkage": capped,
        "audit_confirmed_miss_exception_applied": exception_applied,
        "human_review_required": tier == TIER_IMMEDIATE_REVIEW,
    }


def build_subject_review_candidate(
    subject: str, market: str, active_episodes: list[dict], *,
    pit_eligibility_status: str,
    reference_forward_metrics_first_detection: dict | None = None,
    reference_forward_metrics_latest_detection: dict | None = None,
) -> dict:
    """Consolidates every currently-ACTIVE episode for one subject (across
    all its trigger types) into ONE Human Review Candidate record. This is
    what a human actually reads -- `raw_trigger_ledger` remains the full,
    unconsolidated audit trail (see module docstring)."""
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
    audit_tag = _audit_confirmed_miss_tag(subject, active_episodes)

    tiering = compute_tier(confirmation_count, pit_eligibility_status, thesis_linkage,
                            price_reflection_status, audit_tag)

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
        "audit_confirmed_miss": audit_tag,
        "tier": tiering["tier"],
        "base_tier": tiering["base_tier"],
        "capped_for_missing_linkage": tiering["capped_for_missing_linkage"],
        "audit_confirmed_miss_exception_applied": tiering["audit_confirmed_miss_exception_applied"],
        "human_review_required": tiering["human_review_required"],
        "authority": _authority_block(),
        "reference_forward_metrics_first_detection": reference_forward_metrics_first_detection,
        "reference_forward_metrics_latest_detection": reference_forward_metrics_latest_detection,
        "reference_forward_metrics_note": (
            "diagnostic only, reused verbatim from replay.forward_metrics.compute_forward_metrics "
            "(PR #210's anti-backdated-entry invariant) -- never an entry authorization"
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
