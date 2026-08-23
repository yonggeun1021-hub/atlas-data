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

★ P8-10 integration (CIO's locked integration spec, 2026-08-23, post PR
  #212 merge): `price_reflection_status` is now REAL for BTC/KOREA subjects
  -- `clock/run_dynamic_clock.py` calls `clock.price_reflection_link.
  link_price_reflection()` (which reuses `decision/price_evidence.py` +
  `decision/price_reflection.py` UNCHANGED, independently re-validating
  every packet -- see that module's docstring) and passes the result into
  `build_subject_review_candidate(price_reflection_status=...)`.
  `thesis_linkage` remains the honest `NOT_LINKED_THIS_SLICE` placeholder
  -- P8-08 Forward Thesis linkage is explicitly OUT OF SCOPE for this
  integration (the locked spec connects only P8-10's `price_reflection` and
  P8-12's Dynamic Clock, not a third contract).

  **Critical invariant (integration spec item 3.4/5.3)**: a real P8-10 link
  (`price_reflection_status.status == "LINKED"`) does NOT by itself count
  as a "confirmatory" linkage for tier-elevation purposes -- `threshold_basis`
  must also be `"RATIFIED"`, never `"PROVISIONAL"` (see
  `_is_confirmatory_linkage`). `classification_thresholds_approval_status`
  in P8-10's own contract is `"PROVISIONAL"` today (not yet CIO-ratified),
  so a real, successfully-linked `price_state=OVEREXTENDED` (BTC) or
  `MODERATE`/`WEAK` (Korea names) is diagnostic information only and can
  NEVER elevate a candidate to `IMMEDIATE_REVIEW` while that remains true.
  When BOTH thesis and a CONFIRMATORY price-reflection linkage are absent
  (true for every candidate today), a candidate is capped at `WATCH_REVIEW`
  even if its confirmation-based tier would otherwise be `IMMEDIATE_REVIEW`
  -- with NO exception of any kind (round 1's AUDIT_CONFIRMED_MISS exception
  remains fully removed, not narrowed).

  **Second, independent structural lock** (defense-in-depth beyond
  `price_reflection_link.py`'s own re-validation): `build_subject_review_
  candidate` re-asserts that a `"LINKED"` `price_reflection_status` never
  carries a non-`"UNKNOWN"` `reflection_status` -- see
  `_assert_price_reflection_status_is_pit_safe`, called unconditionally
  before `compute_tier()` runs, so a directly-injected tampered dict (never
  routed through `price_reflection_link.verify_and_extract()` at all) is
  ALSO rejected, not just a re-signed packet caught upstream.

★ CIO integration review round 1, defect 3 (post-hoc data physically
  present in the operational object): the original version embedded
  `post_hoc_audit_note` and `reference_forward_metrics_*` directly on the
  `build_subject_review_candidate()` record -- not being a `compute_tier()`
  INPUT wasn't enough, since any other downstream consumer of that same
  object could still read them. This module now has NO import of
  `clock.audit_confirmed_miss` at all (moved to `clock/audit_diagnostics.py`,
  called only from `clock/run_dynamic_clock.py` to build a PHYSICALLY
  SEPARATE `audit_diagnostics` artifact) and `build_subject_review_
  candidate()` no longer accepts or emits any post-hoc/forward-return field
  whatsoever -- see `test_operational_path_never_imports_audit_module` and
  `test_operational_candidate_output_independent_of_audit_artifact` in
  `test/test_price_reflection_link.py`.

★ CIO integration review round 1, defect 4 (missing PIT timing contract):
  every candidate now separately carries `evidence_as_of`,
  `trigger_observed_at`, `decision_at`, `price_as_of`, `candidate_created_at`,
  `candidate_updated_at`, and `time_precision` (`"DATE_ONLY"` -- this repo's
  evidence is date-granularity, never a fabricated intraday timestamp
  except `price_as_of`, which P8-10 already supplies as a real UTC
  timestamp). `_validate_candidate_timing` enforces `evidence_as_of <=
  trigger_observed_at <= decision_at`, `price_as_of <= decision_at`, and
  `candidate_created_at <= candidate_updated_at <= decision_at`
  unconditionally before a candidate is ever returned.

★ Authority: every record (raw or consolidated) carries an explicit
  `authority` block with every Stage/Buy/Action/Order/Production/trading
  field hard-`False`/`None`. `validate_review_candidate` asserts this
  against `AUTHORITY_ALL_FALSE`.
"""
from __future__ import annotations

import copy
import inspect

from replay.opportunity_trigger import canonical_json, payload_sha256

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


def build_raw_trigger_record(episode: dict) -> dict:
    """One record per ACTIVE (subject, trigger_type) episode -- the full,
    unfiltered audit trail (structural trigger/episode data only). NOT a
    review candidate by itself (no `human_review_required` field); see
    `build_subject_review_candidate` for the consolidated, tiered view a
    human actually reads.

    ★ CIO integration review round 1, defect 3: this record carries NO
    post-hoc/forward-return field of any kind -- `clock/audit_diagnostics.py`
    (via `clock/run_dynamic_clock.py::run_with_diagnostics()`) is now the
    SINGLE, physically separate location for
    `reference_forward_metrics_*`/`post_hoc_audit_note`, never duplicated
    here."""
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


def _is_confirmatory_linkage(linkage: dict) -> bool:
    """A linkage channel counts toward lifting the IMMEDIATE_REVIEW cap
    ONLY if it is actually `"LINKED"` AND -- when it carries a
    `threshold_basis` (only `price_reflection_status` does today) -- that
    basis is `"RATIFIED"`, never `"PROVISIONAL"`. Integration spec item
    3.4: "A PROVISIONAL price_state alone must NEVER... elevate candidate
    tier". `classification_thresholds_approval_status` in P8-10's own
    contract is `"PROVISIONAL"` as of this integration, so a real,
    successfully-linked price_state can never satisfy this today -- not a
    gap, the honest current state."""
    if linkage.get("status") != "LINKED":
        return False
    threshold_basis = linkage.get("threshold_basis")
    if threshold_basis is not None and threshold_basis != "RATIFIED":
        return False
    return True


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
         price_reflection_status are non-CONFIRMATORY (see
         `_is_confirmatory_linkage` -- a real P8-10 link with a
         PROVISIONAL threshold_basis does NOT count), in which case it is
         capped down to WATCH_REVIEW. There is NO exception to this cap --
         a real, already-known-in-hindsight Miss (see
         `_post_hoc_audit_note`) does NOT lift it; only a genuinely
         CONFIRMATORY thesis/price linkage can.
      3. Otherwise -> WATCH_REVIEW.
    """
    if pit_eligibility_status != "PASS":
        base_tier = TIER_OBSERVATION_ONLY
    elif confirmation_count >= INDEPENDENT_CONFIRMATION_THRESHOLD:
        base_tier = TIER_IMMEDIATE_REVIEW
    else:
        base_tier = TIER_WATCH_REVIEW

    both_linkages_absent = (
        not _is_confirmatory_linkage(thesis_linkage) and not _is_confirmatory_linkage(price_reflection_status)
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
        "reason": _tier_reason(tier, confirmation_count, pit_eligibility_status, capped, price_reflection_status),
    }


# Structural PIT guard: enumerate every parameter compute_tier() is allowed
# to have. If a future edit adds a forward-return/MFE/audit/outcome-shaped
# parameter, this set must be updated in the SAME diff a reviewer sees --
# it cannot silently happen as a drive-by change.
_TIER_ALLOWED_PARAMETERS = frozenset({
    "confirmation_count", "pit_eligibility_status", "thesis_linkage", "price_reflection_status",
})


def _tier_reason(tier: str, confirmation_count: int, pit_eligibility_status: str, capped: bool,
                  price_reflection_status: dict) -> str:
    """Plain-language, template-only (never LLM-generated, never derived
    from a forward return or any post-hoc figure) explanation of WHY a
    candidate landed in this tier -- what the briefing shows instead of any
    outcome-shaped number."""
    if tier == TIER_OBSERVATION_ONLY:
        return f"pit_eligibility_status={pit_eligibility_status} (not PASS) -- identity/eligibility not confirmed"
    if tier == TIER_IMMEDIATE_REVIEW:
        return (
            f"confirmation_count={confirmation_count} independent trigger types AND a real, "
            "RATIFIED-basis thesis/price linkage is present"
        )
    if capped:
        pr_status = price_reflection_status.get("status")
        if pr_status == "LINKED":
            linkage_note = (
                f"price_reflection is linked (price_state={price_reflection_status.get('price_state')}) "
                f"but threshold_basis={price_reflection_status.get('threshold_basis')} -- PROVISIONAL "
                "diagnostics never elevate tier; no thesis linkage exists either"
            )
        else:
            linkage_note = "no thesis or price-reflection linkage exists yet"
        return (
            f"confirmation_count={confirmation_count} independent trigger types, but capped at WATCH_REVIEW: "
            f"{linkage_note}"
        )
    return f"confirmation_count={confirmation_count} -- below the independent-confirmation threshold of {INDEPENDENT_CONFIRMATION_THRESHOLD}"


def _assert_price_reflection_status_is_pit_safe(price_reflection_status: dict) -> None:
    """Second, independent structural lock (defense-in-depth beyond
    `clock.price_reflection_link.verify_and_extract()`'s own
    re-validation): a `"LINKED"` price_reflection_status may NEVER carry a
    non-`"UNKNOWN"` `reflection_status` -- catches a directly-injected
    tampered dict that bypassed the link module entirely, not just a
    re-signed packet caught upstream (integration spec item 8.2)."""
    if not isinstance(price_reflection_status, dict):
        raise ReviewCandidateError("PRICE_REFLECTION_STATUS_NOT_A_DICT")
    if price_reflection_status.get("status") == "LINKED":
        reflection_status = price_reflection_status.get("reflection_status")
        if reflection_status != "UNKNOWN":
            raise ReviewCandidateError(
                f"PRICE_REFLECTION_STATUS_NON_UNKNOWN_REJECTED:{reflection_status}"
            )


def _validate_candidate_timing(evidence_as_of: str, trigger_observed_at: str, decision_at: str,
                                price_as_of: str | None, candidate_created_at: str,
                                candidate_updated_at: str) -> None:
    """Defect 4: fails closed on any violation of the locked spec's PIT
    timing contract. Compares date-only strings lexicographically (all of
    this repo's evidence is date-granularity); `price_as_of`, when present,
    is a full UTC timestamp -- only its date portion is compared against
    `decision_at`, which is date-only."""
    if evidence_as_of > trigger_observed_at:
        raise ReviewCandidateError(
            f"TIMING_INVARIANT_VIOLATED:evidence_as_of({evidence_as_of})>trigger_observed_at({trigger_observed_at})"
        )
    if trigger_observed_at > decision_at:
        raise ReviewCandidateError(
            f"TIMING_INVARIANT_VIOLATED:trigger_observed_at({trigger_observed_at})>decision_at({decision_at})"
        )
    if price_as_of not in (None, "UNKNOWN"):
        price_date = price_as_of[:10]
        if price_date > decision_at:
            raise ReviewCandidateError(
                f"TIMING_INVARIANT_VIOLATED:price_as_of({price_as_of})>decision_at({decision_at})"
            )
    if candidate_created_at > candidate_updated_at:
        raise ReviewCandidateError(
            f"TIMING_INVARIANT_VIOLATED:candidate_created_at({candidate_created_at})"
            f">candidate_updated_at({candidate_updated_at})"
        )
    if candidate_updated_at > decision_at:
        raise ReviewCandidateError(
            f"TIMING_INVARIANT_VIOLATED:candidate_updated_at({candidate_updated_at})>decision_at({decision_at})"
        )


def build_subject_review_candidate(
    subject: str, market: str, active_episodes: list[dict], *,
    pit_eligibility_status: str,
    decision_at: str,
    price_reflection_status: dict | None = None,
) -> dict:
    """Consolidates every currently-ACTIVE episode for one subject (across
    all its trigger types) into ONE Human Review Candidate record. This is
    what a human actually reads -- `raw_trigger_ledger` remains the full,
    unconsolidated audit trail (see module docstring).

    `decision_at` is the market's operational "as of" date for this run
    (required -- defect 4's timing contract has no meaning without it).

    `price_reflection_status`, when supplied, MUST already be the output of
    `clock.price_reflection_link.to_price_reflection_status()` (or the
    equivalent `NOT_LINKED_THIS_SLICE` placeholder shape) -- re-validated
    unconditionally below regardless of provenance. Omit it (or pass
    `None`) to fall back to the honest placeholder (e.g. in tests that
    don't exercise the P8-10 link at all).

    ★ Defect 3: this record carries NO post-hoc/forward-return field of any
    kind -- not `post_hoc_audit_note`, not `reference_forward_metrics_*`.
    Those live only in the physically separate `clock/audit_diagnostics.py`
    artifact `clock/run_dynamic_clock.py` builds independently."""
    if not active_episodes:
        raise ReviewCandidateError("NO_ACTIVE_EPISODES_FOR_SUBJECT")
    if any(ep.get("status") != "ACTIVE" for ep in active_episodes):
        raise ReviewCandidateError("ALL_EPISODES_MUST_BE_ACTIVE")
    if any(ep["subject"] != subject or ep["market"] != market for ep in active_episodes):
        raise ReviewCandidateError("EPISODE_SUBJECT_MARKET_MISMATCH")

    trigger_types = sorted({ep["trigger_type"] for ep in active_episodes})
    confirmation_count = len(trigger_types)
    latest_ep = max(active_episodes, key=lambda e: e["last_detected_at"])
    earliest_ep = min(active_episodes, key=lambda e: e["opened_at"])
    detected_at = latest_ep["last_detected_at"]
    first_detected_at = earliest_ep["opened_at"]
    max_strength = max(ep["evidence_trail"][-1]["strength"] for ep in active_episodes)
    next_review_at = min(ep["next_review_at"] for ep in active_episodes)
    expiry = max(ep["expiry"] for ep in active_episodes)
    calendar_confidence = active_episodes[0].get("next_review_at_calendar_confidence")

    thesis_linkage = _thesis_linkage_placeholder()
    if price_reflection_status is None:
        price_reflection_status = _price_reflection_placeholder()
    _assert_price_reflection_status_is_pit_safe(price_reflection_status)

    # ★ PIT-safe by construction: compute_tier() is called with ONLY
    # as-of-detected_at-knowable inputs. No post-hoc value is computed
    # anywhere in this function at all any more (defect 3).
    tiering = compute_tier(confirmation_count, pit_eligibility_status, thesis_linkage, price_reflection_status)

    # ★ Defect 4: full PIT timing contract. evidence_as_of/trigger_observed_at
    # are taken from the SAME episode (latest_ep) that defines detected_at,
    # so evidence_as_of <= trigger_observed_at holds by the same construction
    # clock/dynamic_clock.py already enforces per-event (see
    # ClockEvent/_validate_ascending).
    evidence_as_of = latest_ep["evidence_trail"][-1]["evidence_available_at"]
    trigger_observed_at = detected_at
    price_as_of = (
        price_reflection_status.get("price_as_of")
        if price_reflection_status.get("status") == "LINKED" else "UNKNOWN"
    )
    candidate_created_at = first_detected_at
    candidate_updated_at = detected_at
    _validate_candidate_timing(
        evidence_as_of, trigger_observed_at, decision_at, price_as_of,
        candidate_created_at, candidate_updated_at,
    )

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
        # ★ Defect 4 timing contract fields.
        "evidence_as_of": evidence_as_of,
        "trigger_observed_at": trigger_observed_at,
        "decision_at": decision_at,
        "price_as_of": price_as_of,
        "candidate_created_at": candidate_created_at,
        "candidate_updated_at": candidate_updated_at,
        "time_precision": "DATE_ONLY",
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
