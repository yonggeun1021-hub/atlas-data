#!/usr/bin/env python3
"""Proposed Action Conversion Gate (Control Loop doc section 4), evaluated
against real triggers produced by trigger_engine.py.

★ CIO review round 3 fix (flaw 2): condition semantics corrected further --
    - Condition 1 no longer conflates "a price-breakout trigger has a real
      evidence source" with "an investment thesis/catalyst exists". A real,
      grounded price/flow-structure trigger (PRICE_CONFIRMATION,
      INVALIDATION_TRIGGER, FLOW_REVERSAL, RELATIVE_STRENGTH_REVERSAL) now
      yields the distinct value `PASS_TACTICAL` -- an explicit "Tactical
      Probe" hypothesis class, never conflated with a real fundamental
      thesis. Only a FUNDAMENTAL_REVISION/CATALYST_APPROACH/
      EXPECTATION_DISLOCATION-class trigger (none implemented against real
      data today -- see trigger_engine.py) would yield `PASS_FUNDAMENTAL`.
    - Condition 5 no longer masquerades stop distance as position sizing.
      `stop_distance_pct` (renamed from `max_loss_pct`) is the real
      entry-to-invalidation arithmetic; `condition_5_position_sizing` is a
      SEPARATE, honestly-`NOT_EVALUATED`-always field, because real sizing
      requires Portfolio NAV / per-trade loss allowance / a ratified Probe
      loss budget / a target weight or quantity / portfolio headroom --
      NONE of which exist anywhere in this repo's committed evidence.
    - Condition 6 is now three independently-evaluated real sub-checks
      (`condition_6a_price_integrity`, `condition_6b_asset_identity_status`,
      `condition_6c_pit_availability`), not one aggregate that a bare price
      series existing could satisfy. `asset_identity_status` is answered by
      `asset_identity.py`'s real, ratified-taxonomy-backed check -- not "a
      price series exists therefore identity is resolved".

  `conditions_1_to_6_all_pass` requires condition 1 in
  {PASS_TACTICAL, PASS_FUNDAMENTAL} AND conditions 2-4 == PASS AND
  condition_5_position_sizing == PASS AND condition_6 == PASS. Because
  condition 5 is now always NOT_EVALUATED, this is structurally always
  False today -- an honest, not a papered-over, result (see the narrative
  report: this repo has no portfolio-sizing data source at all yet).

This is a SHADOW / counterfactual evaluation only: it never sets capital,
Stage, trade_proposal, or any Buy/Action/Order field, and
`ProbeGateResult.capital` is hard-coded to 0 with no parameter able to
override it.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from replay import asset_identity as ai

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"

CONDITION_1_STATUSES = ("PASS_TACTICAL", "PASS_FUNDAMENTAL", "FAIL", "NOT_COMPUTABLE")
CONDITION_STATUSES = ("PASS", "FAIL", "NOT_EVALUATED", "NOT_COMPUTABLE")
SENTINEL_SOURCE_PREFIXES = ("NO_", "0" * 64)

TACTICAL_TRIGGER_TYPES = frozenset({
    "PRICE_CONFIRMATION", "INVALIDATION_TRIGGER", "FLOW_REVERSAL", "RELATIVE_STRENGTH_REVERSAL",
})
FUNDAMENTAL_TRIGGER_TYPES = frozenset({
    "FUNDAMENTAL_REVISION", "CATALYST_APPROACH", "EXPECTATION_DISLOCATION",
})


@dataclasses.dataclass(frozen=True)
class ProbeGateResult:
    subject: str
    decision_date: str
    condition_1_hypothesis_or_catalyst: str   # PASS_TACTICAL/PASS_FUNDAMENTAL/FAIL/NOT_COMPUTABLE
    condition_2_independent_confirmation: str
    condition_3_entry_zone: str
    condition_4_invalidation: str
    condition_5_position_sizing: str          # always NOT_EVALUATED today -- see module docstring
    condition_6_pit_data_integrity: str       # aggregate of 6a/6b/6c
    condition_6a_price_integrity: str
    condition_6b_asset_identity_status: str
    condition_6c_pit_availability: str
    condition_7_gate_ratified: str
    condition_7_detail: str
    trigger_types_present: tuple
    stop_distance_pct: float | None           # renamed from max_loss_pct -- see module docstring
    conditions_1_to_6_all_pass: bool
    recommended_action: str  # NONE | PROBE_REVIEW_CANDIDATE | PROBE_REVIEW_CANDIDATE_TACTICAL (shadow only)
    capital: int = 0         # ⛔ hard-coded, see module docstring


def _gate_ratified_status() -> tuple[str, str]:
    if not CONFIG_DIR.is_dir():
        return "NOT_COMPUTABLE", "config/ directory not found"
    candidates = sorted(CONFIG_DIR.glob("*probe*polic*.json")) + sorted(CONFIG_DIR.glob("*p5*polic*.json"))
    if not candidates:
        return "FAIL", (
            "no config/*probe*_policy.json or config/*p5*_policy.json file exists in this repo "
            "(checked against the real config/*_policy.json + approval_status=='RATIFIED' "
            "convention already used by e.g. config/korea_leadership_policy.json)"
        )
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and doc.get("approval_status") == "RATIFIED":
            return "PASS", f"{path.relative_to(ROOT)} has approval_status == 'RATIFIED'"
    names = [str(p.relative_to(ROOT)) for p in candidates]
    return "FAIL", f"found {names} but none has approval_status == 'RATIFIED'"


def gate_available() -> bool:
    status, _ = _gate_ratified_status()
    return status == "PASS"


def _trigger_type(t) -> str:
    return t.trigger_type if hasattr(t, "trigger_type") else t["trigger_type"]


def _trigger_source(t):
    return t.source if hasattr(t, "source") else t.get("source")


def _trigger_evidence_sha(t):
    return t.evidence_sha256 if hasattr(t, "evidence_sha256") else t.get("evidence_sha256")


def _condition_1(triggers) -> str:
    if not triggers:
        return "FAIL"
    for t in triggers:
        source = _trigger_source(t)
        evidence_sha = _trigger_evidence_sha(t)
        if not source or source.startswith(SENTINEL_SOURCE_PREFIXES) or evidence_sha in (None, "0" * 64):
            return "FAIL"  # a trigger exists but its evidence citation is a placeholder, not real
    types_present = {_trigger_type(t) for t in triggers}
    if types_present & FUNDAMENTAL_TRIGGER_TYPES:
        return "PASS_FUNDAMENTAL"
    if types_present & TACTICAL_TRIGGER_TYPES:
        return "PASS_TACTICAL"
    return "NOT_COMPUTABLE"  # a trigger type this module doesn't yet classify either way


def _condition_2(triggers) -> str:
    if not triggers:
        return "NOT_EVALUATED"
    types_present = {_trigger_type(t) for t in triggers}
    return "PASS" if len(types_present) >= 2 else "FAIL"


def _condition_3(entry_price) -> str:
    return "PASS" if entry_price is not None else "NOT_COMPUTABLE"


def _condition_4(entry_price, invalidation_price) -> str:
    if entry_price is None or invalidation_price is None:
        return "NOT_COMPUTABLE"
    if invalidation_price >= entry_price:
        return "FAIL"
    return "PASS"


MAX_SANE_STOP_DISTANCE_PCT = 50.0  # a bound to catch data errors, not a real capital limit


def _stop_distance(entry_price, invalidation_price):
    """Real entry-to-invalidation arithmetic. This is a STOP DISTANCE, not
    position sizing -- see module docstring. Returns (status, pct|None)."""
    if entry_price is None or invalidation_price is None:
        return "NOT_COMPUTABLE", None
    if invalidation_price >= entry_price:
        return "NOT_COMPUTABLE", None
    pct = (entry_price - invalidation_price) / entry_price * 100.0
    if not (0.0 < pct < MAX_SANE_STOP_DISTANCE_PCT):
        return "FAIL", pct
    return "PASS", pct


def _condition_5_position_sizing() -> str:
    """Real position sizing requires Portfolio NAV, a per-trade loss
    allowance, a ratified Probe loss budget, a target weight/quantity, and
    portfolio headroom -- none of which exist anywhere in this repo's
    committed evidence. Always NOT_EVALUATED; never derived from stop
    distance alone (that would be exactly the flaw this fixes)."""
    return "NOT_EVALUATED"


def _condition_6a_price_integrity(series, evaluation_date: str | None, lookback_dates: list) -> str:
    if series is None or evaluation_date is None:
        return "NOT_COMPUTABLE"
    conflicted_dates = {c["trading_date"] for c in getattr(series, "integrity_conflicts", [])}
    checked_dates = set(lookback_dates) | {evaluation_date}
    return "FAIL" if (conflicted_dates & checked_dates) else "PASS"


def _condition_6b_asset_identity(subject: str, decision_date: str, kr_universe_codes) -> str:
    return ai.asset_identity_status(subject, decision_date, kr_universe_codes)


def _condition_6c_pit_availability(evaluation_date: str | None) -> str:
    return "PASS" if evaluation_date is not None else "FAIL"


def _aggregate_condition_6(a: str, b: str, c: str) -> str:
    if "FAIL" in (a, b, c):
        return "FAIL"
    if "NOT_COMPUTABLE" in (a, b, c):
        return "NOT_COMPUTABLE"
    if a == b == c == "PASS":
        return "PASS"
    return "NOT_COMPUTABLE"


def evaluate(subject: str, decision_date: str, triggers: list, entry_price: float | None,
             invalidation_price: float | None, series=None, evaluation_date: str | None = None,
             lookback_dates: list | None = None, kr_universe_codes=None) -> ProbeGateResult:
    types_present = tuple(sorted({_trigger_type(t) for t in triggers}))
    cond1 = _condition_1(triggers)
    cond2 = _condition_2(triggers)
    cond3 = _condition_3(entry_price)
    cond4 = _condition_4(entry_price, invalidation_price)
    stop_status, stop_distance_pct = _stop_distance(entry_price, invalidation_price)
    cond5 = _condition_5_position_sizing()
    cond6a = _condition_6a_price_integrity(series, evaluation_date, lookback_dates or [])
    cond6b = _condition_6b_asset_identity(subject, decision_date, kr_universe_codes)
    cond6c = _condition_6c_pit_availability(evaluation_date)
    cond6 = _aggregate_condition_6(cond6a, cond6b, cond6c)
    cond7, cond7_detail = _gate_ratified_status()

    hypothesis_ok = cond1 in ("PASS_TACTICAL", "PASS_FUNDAMENTAL")
    conditions_all_pass = (
        hypothesis_ok and cond2 == "PASS" and cond3 == "PASS" and cond4 == "PASS"
        and cond5 == "PASS" and cond6 == "PASS"
    )
    if conditions_all_pass and cond7 == "PASS":
        action = "PROBE_REVIEW_CANDIDATE_TACTICAL" if cond1 == "PASS_TACTICAL" else "PROBE_REVIEW_CANDIDATE"
    else:
        action = "NONE"

    return ProbeGateResult(
        subject=subject, decision_date=decision_date,
        condition_1_hypothesis_or_catalyst=cond1,
        condition_2_independent_confirmation=cond2,
        condition_3_entry_zone=cond3,
        condition_4_invalidation=cond4,
        condition_5_position_sizing=cond5,
        condition_6_pit_data_integrity=cond6,
        condition_6a_price_integrity=cond6a,
        condition_6b_asset_identity_status=cond6b,
        condition_6c_pit_availability=cond6c,
        condition_7_gate_ratified=cond7,
        condition_7_detail=cond7_detail,
        trigger_types_present=types_present,
        stop_distance_pct=stop_distance_pct,
        conditions_1_to_6_all_pass=conditions_all_pass,
        recommended_action=action,
    )
