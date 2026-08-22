#!/usr/bin/env python3
"""Opportunity Miss Ledger (deliverable 2): which real up-moves were not
converted into even a shadow PROBE_REVIEW_CANDIDATE, and at what stage.

A miss is recorded when the 5-trading-day forward return is materially
positive (>= MATERIALITY_THRESHOLD_PCT) AND the proposed ruleset's action
was NOT "PROBE_REVIEW_CANDIDATE" for that (subject, decision_date). The
5-day horizon is used because it is the doc's own mid-range horizon
("3거래일" breakout confirmation window) and is far enough out
to separate noise from a real move while still being within reach for most
of the committed evidence window.

Materiality and horizon are both named constants, not hidden literals --
`test/test_opportunity_miss_ledger.py` pins their values.
"""
from __future__ import annotations

from replay.signal_replay_ledger import classify_gap

MATERIALITY_THRESHOLD_PCT = 5.0
# Preferred horizon order: use the longest horizon that actually has real
# committed forward data for this entry, rather than an all-or-nothing "5".
# Which horizon was actually used is recorded on every record
# (`materiality_horizon_used`) so this is never silently ambiguous.
PREFERRED_HORIZONS = ("5", "3", "1", "10")


def _best_available_horizon(entry: dict) -> tuple[str, dict] | None:
    for h in PREFERRED_HORIZONS:
        data = entry["forward_metrics"]["horizons"].get(h, {})
        if data.get("status") == "OK":
            return h, data
    return None


def is_material_miss(entry: dict) -> bool:
    best = _best_available_horizon(entry)
    if best is None:
        return False
    _, data = best
    if data["forward_return_pct"] < MATERIALITY_THRESHOLD_PCT:
        return False
    return entry["proposed_ruleset"]["recommended_action"] != "PROBE_REVIEW_CANDIDATE"


def build_miss_records(entries: list[dict], **classify_kwargs) -> list[dict]:
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
            "root_cause": root_cause,
            "evidence_sha256": entry["evidence_sha256"],
            "source": entry["source"],
        })
    return out
