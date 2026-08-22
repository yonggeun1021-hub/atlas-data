#!/usr/bin/env python3
"""Opportunity Miss Ledger (deliverable 2): which real up-moves were not
converted into even a shadow PROBE_REVIEW_CANDIDATE, and at what stage.

A miss is recorded when the best-available-horizon forward return is
materially positive (>= MATERIALITY_THRESHOLD_PCT) AND the proposed
ruleset's action was NOT "PROBE_REVIEW_CANDIDATE" for that
(subject, decision_date).

★ CIO review fix (flaw 5, PR #210): `build_miss_records()` still returns the
  raw, one-row-per-day table (kept for transparency/debugging), but it is
  NO LONGER the headline KPI. `build_miss_episodes()` deduplicates
  consecutive same-subject/same-root_cause daily rows into one
  `Opportunity Episode` per real event (see `opportunity_episode.py`) --
  THAT is the number that must be reported as "the Miss count".
★ CIO review fix (flaw 4, PR #210): an entry whose forward_metrics status is
  `NOT_GRADABLE` is excluded from materiality entirely (we cannot know
  whether it was a miss or not without a valid grade) and reported
  separately via `build_ungradable_records()` for transparency, rather than
  silently defaulting to "not material".
"""
from __future__ import annotations

from replay.opportunity_episode import group_into_episodes
from replay.signal_replay_ledger import classify_gap

MATERIALITY_THRESHOLD_PCT = 5.0
# Preferred horizon order: use the longest horizon that actually has real
# committed forward data for this entry, rather than an all-or-nothing "5".
# Which horizon was actually used is recorded on every record
# (`materiality_horizon_used`) so this is never silently ambiguous.
PREFERRED_HORIZONS = ("5", "3", "1", "10")


def _best_available_horizon(entry: dict) -> tuple[str, dict] | None:
    if entry["forward_metrics"].get("status") != "OK":
        return None  # NOT_GRADABLE / NO_ENTRY_PRICE_DATA -- no horizon can be trusted
    for h in PREFERRED_HORIZONS:
        data = entry["forward_metrics"]["horizons"].get(h, {})
        if data.get("status") == "OK":
            return h, data
    return None


def is_ungradable(entry: dict) -> bool:
    return entry["forward_metrics"].get("status") == "NOT_GRADABLE"


def is_material_miss(entry: dict) -> bool:
    best = _best_available_horizon(entry)
    if best is None:
        return False
    _, data = best
    if data["forward_return_pct"] < MATERIALITY_THRESHOLD_PCT:
        return False
    return entry["proposed_ruleset"]["recommended_action"] != "PROBE_REVIEW_CANDIDATE"


def build_ungradable_records(entries: list[dict]) -> list[dict]:
    """Entries excluded from grading entirely (flaw 4) -- reported
    separately, never silently folded into "not material"."""
    out = []
    for entry in entries:
        if not is_ungradable(entry):
            continue
        out.append({
            "decision_date": entry["decision_date"],
            "subject": entry["subject"],
            "not_gradable_reason": entry["forward_metrics"].get("not_gradable_reason"),
            "signal_evaluation_at": entry["forward_metrics"].get("signal_evaluation_at"),
            "action_eligible_at": entry["forward_metrics"].get("action_eligible_at"),
        })
    return out


def build_miss_records(entries: list[dict], **classify_kwargs) -> list[dict]:
    """Raw, one-row-per-calendar-day table. NOT the headline KPI -- see
    `build_miss_episodes()`."""
    out = []
    for entry in entries:
        if not is_material_miss(entry):
            continue
        horizon_used, data = _best_available_horizon(entry)
        root_cause = classify_gap(entry, **classify_kwargs)
        out.append({
            "decision_date": entry["decision_date"],
            "subject": entry["subject"],
            "materiality_horizon_used": horizon_used,
            "forward_return_pct": data["forward_return_pct"],
            "mfe_pct": data["mfe_pct"],
            "proposed_ruleset_action": entry["proposed_ruleset"]["recommended_action"],
            "existing_ruleset_action": entry["existing_ruleset"]["recommended_action"],
            "triggers_detected": [t["trigger_type"] for t in entry["triggers"]],
            "conditions_1_to_6_all_pass": entry["proposed_ruleset"]["conditions_1_to_6_all_pass"],
            "root_cause": root_cause,
            "evidence_sha256": entry["evidence_sha256"],
            "source": entry["source"],
        })
    return out


def build_miss_episodes(entries: list[dict], **classify_kwargs) -> list[dict]:
    """★ Headline Miss KPI (deliverable 2, post-review): deduplicated
    Opportunity Episodes, not raw daily rows."""
    daily = build_miss_records(entries, **classify_kwargs)
    return group_into_episodes(daily, outcome_field="forward_return_pct")
