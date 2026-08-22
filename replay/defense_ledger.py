#!/usr/bin/env python3
"""Defense Ledger (deliverable 3): downside actually avoided.

★ Explicit caveat baked into every record, not left implicit: this whole
  repo has zero ratified real-capital or Stage/Buy/Action/Order authority
  anywhere (verified structurally by `existing_ruleset_baseline.py` and by
  the P0/P5 authority-boolean invariants this PR does not touch). That means
  every "avoided drawdown" in this ledger is a structural default (capital
  was never at risk to begin with), not evidence of a deliberate defensive
  judgment. `structural_zero_capital_caveat` is present on every record so
  this cannot be silently read as a skill claim later -- matching the
  canonical audit doc's own principle 4: don't let a defense credit hide a
  missed-upside debit, and don't over-claim it either.
"""
from __future__ import annotations

MATERIALITY_DRAWDOWN_THRESHOLD_PCT = -5.0
# Same "best available horizon" policy as opportunity_miss_ledger.py, applied
# uniformly -- this is part of what makes the winner/loser rule application
# symmetric rather than picking convenient horizons per side.
PREFERRED_HORIZONS = ("5", "3", "1", "10")

CAVEAT = (
    "capital is 0 and no Stage/Buy/Action/Order authority is ratified anywhere in this "
    "system (verified via existing_ruleset_baseline.py) -- this drawdown was avoided by "
    "structural default, not by a defensive decision Atlas made"
)


def _best_available_horizon(entry: dict) -> tuple[str, dict] | None:
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
    out = []
    for entry in entries:
        if not is_material_defense(entry):
            continue
        horizon_used, data = _best_available_horizon(entry)
        out.append({
            "decision_date": entry["decision_date"],
            "subject": entry["subject"],
            "materiality_horizon_used": horizon_used,
            "avoided_forward_return_pct": data["forward_return_pct"],
            "avoided_mae_pct": data["mae_pct"],
            "existing_ruleset_action": entry["existing_ruleset"]["recommended_action"],
            "structural_zero_capital_caveat": CAVEAT,
            "evidence_sha256": entry["evidence_sha256"],
            "source": entry["source"],
        })
    return out
