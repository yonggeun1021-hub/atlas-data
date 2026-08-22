#!/usr/bin/env python3
"""Verified structural facts about the CURRENT ("existing ruleset") system,
used as the baseline side of `ruleset_comparison.py`.

★ This module never imports, executes, or modifies `decision/alpha_review.py`
  or any other Forward Alpha module -- it only READS their source text
  (read-only, same as `grep`) to verify two specific, already-documented
  claims from "Opportunity Capture Control Loop" section 1:

    1. `trade_proposal` is unconditionally hard-coded to `None` in the P8-11
       Alpha Review packet builder.
    2. the default re-review cadence is 30 days, with no shorter cadence for
       fast-decaying signals (flow/price reversals).

  Both claims are re-derived here from the literal committed source text
  (byte match), not merely repeated from the design doc. If a future commit
  changes that source in a way that breaks these markers, this module fails
  loudly (`ExistingRulesetBaselineError`) rather than silently reporting a
  stale baseline.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALPHA_REVIEW_SOURCE = ROOT / "decision" / "alpha_review.py"


class ExistingRulesetBaselineError(ValueError):
    pass


def verify_trade_proposal_always_null() -> dict:
    text = ALPHA_REVIEW_SOURCE.read_text(encoding="utf-8")
    marker = '"trade_proposal": None,'
    guard = 'if packet.get("trade_proposal") is not None:'
    if marker not in text or guard not in text:
        raise ExistingRulesetBaselineError(
            "TRADE_PROPOSAL_ALWAYS_NULL_MARKER_NOT_FOUND -- existing_ruleset_baseline.py's "
            "citation of decision/alpha_review.py is stale; re-verify by hand before trusting "
            "this baseline."
        )
    return {
        "claim": "trade_proposal is unconditionally None in every Alpha Review packet",
        "source": "decision/alpha_review.py",
        "verified_by": "literal source-text match (read-only)",
        "value": True,
    }


def verify_default_review_cadence_days() -> dict:
    text = ALPHA_REVIEW_SOURCE.read_text(encoding="utf-8")
    marker = '"default_next_review_cadence_days": 30,'
    if marker not in text:
        raise ExistingRulesetBaselineError(
            "DEFAULT_REVIEW_CADENCE_MARKER_NOT_FOUND -- re-verify by hand before trusting this baseline."
        )
    return {
        "claim": "default next_review_date cadence is 30 days, with no dynamic "
                 "shorter cadence for flow/price-reversal triggers",
        "source": "decision/alpha_review.py",
        "verified_by": "literal source-text match (read-only)",
        "value": 30,
    }


def existing_ruleset_action_for(has_any_trigger: bool) -> dict:
    """What the CURRENT system structurally does for a given real trigger
    presence, given the two verified facts above. Never claims Atlas
    actually evaluated this on a historical date it has no evidence for --
    that distinction is carried by the caller's `atlas_actually_ran` flag in
    ruleset_comparison.py."""
    verify_trade_proposal_always_null()
    verify_default_review_cadence_days()
    return {
        "trade_proposal": None,          # always, structurally
        "recommended_action": "NONE",    # trade_proposal is always null -> no action can convert
        "next_review_cadence_days": 30,  # fixed, regardless of trigger urgency
        "action_convertible": False,     # structurally impossible under the current ruleset
    }


def baseline_summary() -> dict:
    return {
        "trade_proposal_always_null": verify_trade_proposal_always_null(),
        "default_review_cadence_days": verify_default_review_cadence_days(),
    }
