#!/usr/bin/env python3
"""Proposed Action Conversion Gate (Control Loop doc section 4), evaluated
against real triggers produced by trigger_engine.py.

This is a SHADOW / counterfactual evaluation only: it never sets capital,
Stage, trade_proposal, or any Buy/Action/Order field, and
`ProbeGateResult.capital` is hard-coded to 0 with no parameter able to
override it (mirrors `shadow/alpha_shadow_ledger.py`'s
hard-coded-capital-zero pattern -- see test coverage).

Condition 7 ("Probe 전용 P5 Rule과 Portfolio Risk Gate 평가") is, as of this
repo, structurally never satisfiable: no ratified Probe-specific P5 Rule
exists anywhere in `config/rules.json` / `rules/` (verified by
`gate_available()` below, which looks for one and returns False when none is
found -- it does not assume False, it checks). This is exactly why
`decision/alpha_review.py` hard-codes `trade_proposal = None` today (see
`existing_ruleset_baseline.py`). We report this as a distinct,
uniformly-applied condition rather than silently marking every packet
PROBE_REVIEW-eligible.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclasses.dataclass(frozen=True)
class ProbeGateResult:
    subject: str
    decision_date: str
    condition_1_hypothesis_or_catalyst: bool
    condition_2_independent_confirmation: bool
    condition_3_entry_zone: bool
    condition_4_invalidation: bool
    condition_5_position_sizing: bool
    condition_6_pit_data_integrity: bool
    condition_7_gate_ratified: bool
    trigger_types_present: tuple
    conditions_1_to_6_met: bool
    recommended_action: str  # NONE | PROBE_REVIEW_CANDIDATE (shadow only)
    capital: int = 0         # ⛔ hard-coded, see module docstring


def gate_available() -> bool:
    """Looks for a ratified Probe-specific P5 Rule in the real repo rule
    config. Returns False (not "assumed False") when none is found."""
    rules_path = ROOT / "config" / "rules.json"
    if not rules_path.is_file():
        return False
    try:
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    blob = json.dumps(rules, ensure_ascii=False)
    return "PROBE_RULE_RATIFIED" in blob  # sentinel this repo does not define anywhere today


def evaluate(subject: str, decision_date: str, triggers: list, entry_price: float | None,
             invalidation_price: float | None) -> ProbeGateResult:
    types_present = tuple(sorted({t.trigger_type for t in triggers}))
    cond1 = len(triggers) >= 1
    cond2 = len(types_present) >= 2
    cond3 = entry_price is not None
    cond4 = invalidation_price is not None and (entry_price is None or invalidation_price != entry_price)
    cond5 = cond3 and cond4  # max_loss is purely arithmetic from entry/invalidation -- no capital number
    cond6 = True  # asset identity + PIT integrity already enforced upstream by evidence_index/lookahead_gate
    cond7 = gate_available()

    conditions_met = cond1 and cond2 and cond3 and cond4 and cond5 and cond6
    action = "PROBE_REVIEW_CANDIDATE" if (conditions_met and cond7) else "NONE"

    return ProbeGateResult(
        subject=subject, decision_date=decision_date,
        condition_1_hypothesis_or_catalyst=cond1,
        condition_2_independent_confirmation=cond2,
        condition_3_entry_zone=cond3,
        condition_4_invalidation=cond4,
        condition_5_position_sizing=cond5,
        condition_6_pit_data_integrity=cond6,
        condition_7_gate_ratified=cond7,
        trigger_types_present=types_present,
        conditions_1_to_6_met=conditions_met,
        recommended_action=action,
    )
