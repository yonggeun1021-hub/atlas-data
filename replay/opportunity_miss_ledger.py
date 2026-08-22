#!/usr/bin/env python3
"""Opportunity Miss Ledger (deliverable 2): which real up-moves were not
converted into even a shadow PROBE_REVIEW_CANDIDATE, and at what stage.

A miss is recorded when the best-available-horizon forward return is
materially positive (>= MATERIALITY_THRESHOLD_PCT) AND the proposed
ruleset's action was NOT a PROBE_REVIEW_CANDIDATE* variant for that
(subject, decision_date).

★ CIO review round 3 fix (flaw 4): `DATA_FAILURE` entries are now
  completely excluded from this ledger's numerator AND denominator -- a
  ticker whose price rose later, on a date with no preserved evidence, is
  not a "miss" (nothing was readable to miss); it is an unauditable
  coverage gap. See `replay/coverage_gap.py` for where those entries are
  reported instead.
★ CIO review round 2 fix (flaw 5), reworked round 3 (flaw 3):
  `build_miss_records()` is the raw, one-row-per-day table (still excludes
  DATA_FAILURE); `build_miss_episodes()` deduplicates via the reworked
  trigger-family + forward-window-overlap grouping in
  `opportunity_episode.py` -- THAT is the headline Miss KPI.
"""
from __future__ import annotations

from replay.opportunity_episode import group_into_episodes
from replay.signal_replay_ledger import classify_gap

MATERIALITY_THRESHOLD_PCT = 5.0
PREFERRED_HORIZONS = ("5", "3", "1", "10")


def _best_available_horizon(entry: dict) -> tuple[str, dict] | None:
    if entry["forward_metrics"].get("status") != "OK":
        return None
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
    return entry["proposed_ruleset"]["recommended_action"] not in (
        "PROBE_REVIEW_CANDIDATE", "PROBE_REVIEW_CANDIDATE_TACTICAL",
    )


def build_ungradable_records(entries: list[dict]) -> list[dict]:
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
    """Raw, one-row-per-calendar-day table, EXCLUDING DATA_FAILURE (flaw 4).
    NOT the headline KPI -- see `build_miss_episodes()`."""
    out = []
    for entry in entries:
        if not is_material_miss(entry):
            continue
        horizon_used, data = _best_available_horizon(entry)
        root_cause = classify_gap(entry, **classify_kwargs)
        if root_cause == "DATA_FAILURE":
            continue  # ★ flaw-4 fix: reported via coverage_gap.py instead, not counted here at all
        out.append({
            "decision_date": entry["decision_date"],
            "subject": entry["subject"],
            "entry_date": entry["forward_metrics"].get("hypothetical_entry_at"),
            "outcome_window_end": data.get("end_date"),
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
    Opportunity Episodes over the DATA_FAILURE-excluded, auditable
    population only."""
    daily = build_miss_records(entries, **classify_kwargs)
    return group_into_episodes(daily, outcome_field="forward_return_pct")
