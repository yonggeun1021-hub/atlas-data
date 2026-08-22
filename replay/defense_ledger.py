#!/usr/bin/env python3
"""Defense Ledger (deliverable 3): downside actually avoided.

★ Explicit caveat baked into every record, not left implicit: this whole
  repo has zero ratified real-capital or Stage/Buy/Action/Order authority
  anywhere (verified structurally by `existing_ruleset_baseline.py` and by
  the P0/P5 authority-boolean invariants this PR does not touch). That means
  every "avoided drawdown" in this ledger is a structural default (capital
  was never at risk to begin with), not evidence of a deliberate defensive
  judgment.

★ CIO review round 3 fix (flaw 4, applied symmetrically to Defense --
  the same reasoning applies to a decline as to a rally): entries with
  `data_available=False` (no preserved evidence for that date/subject at
  all) are excluded from this ledger entirely and reported via
  `coverage_gap.py` instead -- "the price fell later, on an unauditable
  date" is not a defense credit any more than the symmetric case is a miss.
★ CIO review round 2 fix (flaw 5), reworked round 3 (flaw 3):
  `build_defense_records()` is the raw daily table (also excludes
  unauditable rows); `build_defense_episodes()` is the headline KPI, using
  the identical trigger-family + forward-window-overlap grouping module as
  Miss, applied symmetrically.
"""
from __future__ import annotations

from replay.opportunity_episode import group_into_episodes

MATERIALITY_DRAWDOWN_THRESHOLD_PCT = -5.0
PREFERRED_HORIZONS = ("5", "3", "1", "10")

CAVEAT = (
    "capital is 0 and no Stage/Buy/Action/Order authority is ratified anywhere in this "
    "system (verified via existing_ruleset_baseline.py) -- this drawdown was avoided by "
    "structural default, not by a defensive decision Atlas made"
)


def _best_available_horizon(entry: dict) -> tuple[str, dict] | None:
    if entry["forward_metrics"].get("status") != "OK":
        return None
    for h in PREFERRED_HORIZONS:
        data = entry["forward_metrics"]["horizons"].get(h, {})
        if data.get("status") == "OK":
            return h, data
    return None


def is_material_defense(entry: dict) -> bool:
    best = _best_available_horizon(entry)
    if best is None:
        return False
    _, data = best
    return data["forward_return_pct"] <= MATERIALITY_DRAWDOWN_THRESHOLD_PCT


def build_defense_records(entries: list[dict]) -> list[dict]:
    """Raw daily table, EXCLUDING unauditable (data_available=False) rows
    (flaw 4, applied symmetrically). NOT the headline KPI."""
    out = []
    for entry in entries:
        if not entry["data_available"]:
            continue  # ★ flaw-4 fix, symmetric: reported via coverage_gap.py instead
        if not is_material_defense(entry):
            continue
        horizon_used, data = _best_available_horizon(entry)
        out.append({
            "decision_date": entry["decision_date"],
            "subject": entry["subject"],
            "root_cause": "AVOIDED_DRAWDOWN",  # constant tag -- gives group_into_episodes a grouping key
            "entry_date": entry["forward_metrics"].get("hypothetical_entry_at"),
            "outcome_window_end": data.get("end_date"),
            "materiality_horizon_used": horizon_used,
            "avoided_forward_return_pct": data["forward_return_pct"],
            "avoided_mae_pct": data["mae_pct"],
            "existing_ruleset_action": entry["existing_ruleset"]["recommended_action"],
            "triggers_detected": [t["trigger_type"] for t in entry["triggers"]],
            "structural_zero_capital_caveat": CAVEAT,
            "evidence_sha256": entry["evidence_sha256"],
            "source": entry["source"],
        })
    return out


def build_defense_episodes(entries: list[dict]) -> list[dict]:
    """★ Headline Defense KPI (deliverable 3, post-review): deduplicated
    Opportunity Episodes over the auditable population only -- same
    grouping module and rule as build_miss_episodes()."""
    daily = build_defense_records(entries)
    episodes = group_into_episodes(daily, outcome_field="avoided_forward_return_pct")
    for ep in episodes:
        ep["structural_zero_capital_caveat"] = CAVEAT
    return episodes
