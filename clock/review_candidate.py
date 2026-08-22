#!/usr/bin/env python3
"""P8-12 output contract: the Human Review Candidate record.

Design note (why this is a NEW, minimal contract rather than reusing
`replay.opportunity_trigger.OpportunityTriggerEvent` directly): that type
models one raw, atomic detection at a fixed historical `decision_date` --
exactly right for retrospective replay, but it has no notion of an ongoing
episode, a review cadence, or a thesis/price-reflection linkage. This module
wraps a `clock.dynamic_clock` episode (itself built from a stream of those
same `OpportunityTriggerEvent`s -- see `clock/operational_scan.py`) into the
record the task's item 3 asks for.

Every field the task requires is present: `subject`, `market`,
`trigger_type`, `detected_at`, `evidence_available_at`, `source` +
`evidence_hash`, thesis linkage, price/reflection status, `urgency`,
`expiry`, `next_review_at`, `human_review_required`.

★ Deliberate non-coupling: `thesis_linkage` and `price_reflection_status`
  are NOT produced by importing `decision/forward_thesis.py` (P8-08) or
  `decision/price_reflection.py` (P8-10) -- both are owned by the separate
  "Forward Alpha" / "Price-PIT" WBS slices (see the Notion Master WBS
  Tracker: P8-10's own Evidence/Note explicitly says "기존 P8-10 canonical
  row 유지. 신규 row 금지" -- a different session's row, not this one's).
  Importing either module here would create exactly the kind of
  cross-session coupling this workstream's isolation was designed to avoid.
  The fields are still present and honest (`NOT_LINKED_THIS_SLICE`, never a
  fabricated status) -- a future integration can populate them once those
  slices produce real linkable output.

★ Authority: every record carries an explicit `authority` block with every
  Stage/Buy/Action/Order/Production/trading field hard-`False`/`None`. This
  block is not merely documentation -- `validate_review_candidate` asserts
  it against `AUTHORITY_ALL_FALSE` (see `test_dynamic_clock_authority.py`).
"""
from __future__ import annotations

import copy

from replay.opportunity_trigger import canonical_json, payload_sha256

NOT_LINKED = "NOT_LINKED_THIS_SLICE"

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


def build_review_candidate(episode: dict, *, reference_forward_metrics: dict | None = None,
                            reference_forward_metrics_first_detection: dict | None = None) -> dict:
    """Builds one Human Review Candidate record from an ACTIVE
    `clock.dynamic_clock` episode. Raises if the episode is not ACTIVE --
    an EXPIRED episode is reported separately (see
    `clock/run_dynamic_clock.py`'s `expired_triggers` section), never as a
    live review candidate."""
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
        # `detected_at` -- when this episode's most recent detection became
        # knowable/actionable to Atlas (== the trigger's decision_date).
        "detected_at": latest_evidence["detected_at"],
        # `evidence_available_at` -- the real-world date the underlying
        # evidence is dated to (may lag `detected_at`; see
        # `clock/dynamic_clock.py::ClockEvent` docstring).
        "evidence_available_at": latest_evidence["evidence_available_at"],
        "first_detected_at": first_evidence["detected_at"],
        "source": latest_evidence["source"],
        "evidence_hash": latest_evidence["evidence_hash"],
        "thesis_linkage": _linkage_placeholder(
            "decoupled from decision/forward_thesis.py (P8-08, separate Forward Alpha WBS row) by design"
        ),
        "price_reflection_status": _linkage_placeholder(
            "decoupled from decision/price_reflection.py (P8-10, separate Price/PIT WBS row) by design"
        ),
        "urgency": episode["urgency"],
        "expiry": episode["expiry"],
        "next_review_at": episode["next_review_at"],
        "human_review_required": True,
        "renewal_count": episode["renewal_count"],
        "reactivated_from_episode_id": episode["reactivated_from_episode_id"],
        "opened_at": episode["opened_at"],
        "evidence_trail_length": len(episode["evidence_trail"]),
        "authority": _authority_block(),
        # Both reused verbatim from replay.forward_metrics.compute_forward_metrics
        # (PR #210's anti-backdated-entry invariant) -- diagnostic only, never an
        # entry authorization. "first_detection" grades from this episode's
        # ORIGINAL opened_at (what PR #210's own audit would have graded);
        # "latest_detection" grades from the most recent renewal (what grading
        # from today's freshest confirmation would look like -- often
        # NOT_GRADABLE near the evidence horizon, which is itself correct,
        # fail-closed behavior, not a bug).
        "reference_forward_metrics_first_detection": reference_forward_metrics_first_detection,
        "reference_forward_metrics_latest_detection": reference_forward_metrics,
        "reference_forward_metrics_note": (
            "diagnostic only, reused verbatim from replay.forward_metrics.compute_forward_metrics "
            "(PR #210's anti-backdated-entry invariant) -- never an entry authorization"
        ),
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
        "human_review_required": False,
        "status": "EXPIRED",
        "authority": _authority_block(),
    }


def validate_review_candidate(record: dict) -> dict:
    """Re-validates a round-tripped record: authority block must be exactly
    all-False/None/0, human_review_required must be True, and the
    record_hash must match. Returns a deep copy on success, fails closed
    otherwise."""
    if record.get("authority") != AUTHORITY_ALL_FALSE:
        raise ReviewCandidateError("AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE")
    if record.get("human_review_required") is not True:
        raise ReviewCandidateError("HUMAN_REVIEW_REQUIRED_MUST_BE_TRUE")
    expected_hash = payload_sha256({k: v for k, v in record.items() if k != "record_hash"})
    if record.get("record_hash") != expected_hash:
        raise ReviewCandidateError("RECORD_HASH_MISMATCH")
    return copy.deepcopy(record)


def canonical(record: dict) -> str:
    return canonical_json(record)
