#!/usr/bin/env python3
"""Root-cause classifier for every miss / action-gap (deliverable 5).

Exactly the seven categories required by the task, matching the
"사후검증 원칙" / Missed Opportunity Sentinel section of the two canonical
docs:
  UNIVERSE_MISS, SIGNAL_MISS, ACTION_CONVERSION_FAILURE, GATE_BLOCK,
  DECISION_LATENCY, NO_POSITION_RULE, DATA_FAILURE.

★ No-survivorship-bias guarantee, made structural rather than asserted:
  `classify()`'s signature does not accept a realized return, MFE, MAE, or
  any other outcome value -- only signal-existence facts that are knowable
  at decision_date. It is architecturally impossible for this function to
  branch on whether the ticker went on to win or lose, because it never
  receives that number. `test/test_root_cause_classifier.py` asserts this
  by inspecting the function's live parameter list.
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
    had_independent_confirmation: bool,
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
    if not gate_available:
        return "GATE_BLOCK"
    if decision_latency_days is not None and decision_latency_days > DECISION_LATENCY_THRESHOLD_DAYS:
        return "DECISION_LATENCY"
    if no_position_rule_active:
        return "NO_POSITION_RULE"
    if had_valid_trigger and had_independent_confirmation:
        return "ACTION_CONVERSION_FAILURE"
    return "ACTION_CONVERSION_FAILURE"


def signature_excludes_outcome_fields() -> bool:
    """Structural proof helper for the no-survivorship-bias test: confirms
    `classify`'s parameters never included a realized-return-shaped name."""
    forbidden = ("return", "mfe", "mae", "pnl", "profit", "outcome", "won", "lost", "gain", "loss")
    params = set(inspect.signature(classify).parameters)
    return not any(any(bad in p.lower() for bad in forbidden) for p in params)
