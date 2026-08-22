#!/usr/bin/env python3
"""Root-cause classifier for every miss / action-gap (deliverable 5).

Exactly the seven categories required by the task, matching the
"사후검증 원칙" / Missed Opportunity Sentinel section of the two canonical
docs:
  UNIVERSE_MISS, SIGNAL_MISS, ACTION_CONVERSION_FAILURE, GATE_BLOCK,
  DECISION_LATENCY, NO_POSITION_RULE, DATA_FAILURE.

★ CIO review fix (flaw 2, PR #210): `GATE_BLOCK` may ONLY be assigned when
  `conditions_1_to_6_all_pass` is `True` (i.e. every one of the Action
  Conversion Gate's first six conditions is a real, verified `PASS` from
  `action_conversion_gate.py` -- not merely "a trigger existed"). A real
  trigger that fails ANY of conditions 1-6 (weak/unconfirmed hypothesis, no
  entry zone, bad invalidation, unsizeable, PIT-integrity conflict) now
  falls through to `ACTION_CONVERSION_FAILURE` instead of being
  misclassified as `GATE_BLOCK`. This directly fixes the pre-review defect
  where entries with `conditions_1_to_6_all_pass=False` (e.g. only a single,
  unconfirmed trigger type) were still labeled `GATE_BLOCK`.

★ No-survivorship-bias guarantee, made structural rather than asserted:
  `classify()`'s signature does not accept a realized return, MFE, MAE, or
  any other outcome value -- only signal-existence facts that are knowable
  at decision_date. It is architecturally impossible for this function to
  branch on whether the ticker went on to win or lose, because it never
  receives that number. `test/test_replay_root_cause_classifier.py` asserts
  this by inspecting the function's live parameter list.

`no_position_rule_active` represents a genuinely-observed, explicit
portfolio-level "do not open a position here" constraint (e.g. a
correlation/concentration/risk-budget limit). This replay has no committed
evidence source for such a constraint for any of its subjects, so every
real caller in this codebase passes `False` -- this category is defined and
tested, but is not expected to fire from this replay's actual evidence (see
`docs/audit/...md` limitations section). It is intentionally NOT a stand-in
for the generic "capital is always 0" structural fact, which is what
`GATE_BLOCK` (P5 Probe Rule unratified) already, correctly, represents.
"""
from __future__ import annotations

import inspect

CATEGORIES = (
    "UNIVERSE_MISS",
    "SIGNAL_MISS",
    "ACTION_CONVERSION_FAILURE",
    "GATE_BLOCK",
    "DECISION_LATENCY",
    "NO_POSITION_RULE",
    "DATA_FAILURE",
)

DECISION_LATENCY_THRESHOLD_DAYS = 3  # doc section 6: price/flow triggers should re-review "다음 거래일 또는 24시간"


class RootCauseError(ValueError):
    pass


def classify(
    *,
    data_available: bool,
    in_universe: bool,
    had_valid_trigger: bool,
    conditions_1_to_6_all_pass: bool,
    gate_available: bool,
    action_taken: bool,
    decision_latency_days: int | None,
    no_position_rule_active: bool,
) -> str:
    if not data_available:
        return "DATA_FAILURE"
    if not in_universe:
        return "UNIVERSE_MISS"
    if not had_valid_trigger:
        return "SIGNAL_MISS"
    if action_taken:
        # Real signal, real action -- not a miss at all; callers should not
        # be invoking this classifier for entries with action_taken=True,
        # but if they do, there is no failure category to assign.
        raise RootCauseError("NOT_A_MISS_ACTION_WAS_TAKEN")
    if no_position_rule_active:
        return "NO_POSITION_RULE"
    if decision_latency_days is not None and decision_latency_days > DECISION_LATENCY_THRESHOLD_DAYS:
        return "DECISION_LATENCY"
    if conditions_1_to_6_all_pass and not gate_available:
        # ★ The ONLY path to GATE_BLOCK: a fully-qualified candidate
        #   (real hypothesis, real independent confirmation, real entry
        #   zone, real invalidation, real sizing, real PIT integrity --
        #   ALL verified PASS) blocked on nothing but the unratified P5
        #   Probe Rule.
        return "GATE_BLOCK"
    return "ACTION_CONVERSION_FAILURE"


def signature_excludes_outcome_fields() -> bool:
    """Structural proof helper for the no-survivorship-bias test: confirms
    `classify`'s parameters never included a realized-return-shaped name."""
    forbidden = ("return", "mfe", "mae", "pnl", "profit", "outcome", "won", "lost", "gain", "loss")
    params = set(inspect.signature(classify).parameters)
    return not any(any(bad in p.lower() for bad in forbidden) for p in params)
