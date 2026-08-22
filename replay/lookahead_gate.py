#!/usr/bin/env python3
"""Hard anti-lookahead gate shared by every P11 PIT replay module.

Two distinct directions matter and this module keeps them explicit:

1. SIGNAL-SIDE (`assert_no_signal_lookahead`): the facts used to decide
   "was there a trigger / was action taken / what gate applied" at a given
   `decision_date` must never be dated after `decision_date`. This is the
   hard constraint from the task: "Zero use of future data relative to each
   replayed decision point."

2. OUTCOME-SIDE (`assert_forward_only`): forward return / MFE / MAE grading
   is *defined* as using price observations strictly after `decision_date`.
   This direction is required, not forbidden -- but it must never reach
   backward past decision_date (that would silently smuggle a "forward"
   number that is actually measuring something already priced in), and the
   two directions must never be confused inside one call site.
"""
from __future__ import annotations

import datetime as dt
import re

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class LookaheadViolation(ValueError):
    pass


def _as_date(value) -> dt.date:
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise LookaheadViolation(f"DATE_INVALID:{value!r}")
    return dt.date.fromisoformat(value)


def assert_no_signal_lookahead(decision_date, evidence_dates, label: str = "evidence") -> None:
    """Raise LookaheadViolation if any evidence_dates entry is strictly after
    decision_date. Used by trigger/gate/ledger builders on the SIGNAL side."""
    dd = _as_date(decision_date)
    for raw in evidence_dates:
        ed = _as_date(raw)
        if ed > dd:
            raise LookaheadViolation(
                f"SIGNAL_LOOKAHEAD:{label}:{ed.isoformat()} > decision_date {dd.isoformat()}"
            )


def assert_forward_only(decision_date, outcome_dates, label: str = "forward_metric") -> None:
    """Raise LookaheadViolation if any outcome_dates entry is NOT strictly
    after decision_date. Used by forward_metrics.py so a "forward" return can
    never be silently computed from decision_date itself or earlier."""
    dd = _as_date(decision_date)
    for raw in outcome_dates:
        od = _as_date(raw)
        if od <= dd:
            raise LookaheadViolation(
                f"FORWARD_WINDOW_NOT_STRICTLY_AFTER_DECISION_DATE:{label}:"
                f"{od.isoformat()} <= decision_date {dd.isoformat()}"
            )
