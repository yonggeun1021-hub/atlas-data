#!/usr/bin/env python3
"""Forward return / MFE / MAE at 1/3/5/10 trading days (deliverable 4).

★ CIO review round 4 fix (confirmed lookahead bug in the core return
  calculation): rounds 2-3 anchored `hypothetical_entry_at` to
  `signal_evaluation_at` (the trading date a trigger's finalized-close data
  was computed FROM), which is frequently the PRIOR trading day relative to
  `action_eligible_at` (when the signal actually became knowable, given the
  collector's own T-1 finalization lag). That let this replay "buy" at a
  close that had already happened BEFORE the signal was known -- literally
  using a price from before the information existed. Concretely: a signal
  evaluated against 2026-08-19's close but only knowable on 2026-08-20 was
  being graded as if entry happened AT 2026-08-19's close. That is banned
  outright now, with no "acceptable overstatement" framing anywhere.

  The corrected, single, uniform rule (applies to EVERY entry -- Miss,
  Defense, SIGNAL_MISS, GATE_BLOCK, ACTION_CONVERSION_FAILURE alike, never
  a different rule per category):

    `hypothetical_entry_at` = the FIRST real trading date in the committed
    series STRICTLY AFTER `action_eligible_at` (== `decision_date`), priced
    at that date's OPEN (never a same-day open when only a daily bar
    exists and pre/post-market timing can't be proven, and never any prior
    day's close). If no such later trading date exists in the committed
    evidence, the entry is `NOT_GRADABLE` -- never silently graded from an
    earlier, already-known-not-executable price.

  `signal_evaluation_at` (the date the trigger's own calculation is
  anchored to) is retained ONLY as diagnostic metadata -- it plays no role
  in the price used for grading.

  Horizon N counts N real trading sessions STARTING FROM
  `hypothetical_entry_at` itself (day 1 = enter at that day's open, mark to
  that same day's close -- a real, capturable same-session paper return,
  never a lookahead since both prices belong to one already-executable
  session once entry happens at its open).

  `hypothetical_entry_at > action_eligible_at` is enforced as a hard,
  structural invariant (never merely asserted in one call site) -- see
  `test/test_replay_forward_metrics.py::EntryTimingInvariantTests` and the
  real-evidence sweep in `test_pit_replay_end_to_end.py`.

PIT discipline enforced here:
  * every date used for the N-day-forward return/MFE/MAE is required, via
    `lookahead_gate.assert_forward_only`, to be strictly AFTER
    `action_eligible_at` (== `decision_date`).
  * "trading day" means an actual row present in the committed price series
    (a real KRX trading session, or a real Kraken daily bar) -- never an
    interpolated or assumed calendar day.
  * when the committed series does not contain a real trading date after
    `action_eligible_at` at all, or not enough of them to reach a horizon,
    that is reported as `NOT_GRADABLE` / `INSUFFICIENT_HORIZON_DATA`
    respectively -- never fabricated.
"""
from __future__ import annotations

from replay.lookahead_gate import assert_forward_only
from replay.price_series import PriceSeries

HORIZONS = (1, 3, 5, 10)

EXECUTION_ASSUMPTION = (
    "hypothetical fill at hypothetical_entry_at's OPEN -- the first real trading "
    "session strictly after action_eligible_at. This is the most conservative "
    "assumption this repo's daily-OHLC-only evidence supports: never a same-day "
    "fill (pre/post-market timing relative to when the signal became knowable "
    "cannot be proven from a daily bar), never any prior day's price."
)


def _pct(a: float, b: float) -> float:
    return (b - a) / a * 100.0


def compute_forward_metrics(series: PriceSeries, decision_date: str, signal_evaluation_at: str | None = None) -> dict:
    """`decision_date` is `action_eligible_at`. `signal_evaluation_at` is
    purely diagnostic metadata (the date a trigger's own calculation was
    anchored to, which may be an earlier trading date due to collector
    lag) -- it is NEVER used to price the entry. See module docstring for
    the corrected, uniform entry-timing rule."""
    action_eligible_at = decision_date
    forward_dates = series.trading_dates_strictly_after(decision_date)
    assert_forward_only(decision_date, forward_dates, label=f"{series.subject}_forward_window")

    result = {
        "subject": series.subject,
        "decision_date": decision_date,
        "action_eligible_at": action_eligible_at,
        "signal_evaluation_at": signal_evaluation_at,
        "hypothetical_entry_at": None,
        "entry_date": None,  # kept for backward-compatible readers -- always == hypothetical_entry_at
        "entry_price": None,
        "entry_price_available_at": None,
        "execution_assumption": EXECUTION_ASSUMPTION,
        "horizons": {},
    }

    if not forward_dates:
        result["status"] = "NOT_GRADABLE"
        result["not_gradable_reason"] = (
            f"no real trading date exists in committed evidence strictly after "
            f"action_eligible_at={decision_date} -- no executable entry point can be established"
        )
        for h in HORIZONS:
            result["horizons"][str(h)] = {"status": "NOT_GRADABLE"}
        return result

    hypothetical_entry_at = forward_dates[0]
    # ★ Hard structural invariant -- fails loudly, never silently, if ever violated.
    if not (hypothetical_entry_at > action_eligible_at):
        raise AssertionError(
            f"ENTRY_TIMING_INVARIANT_VIOLATED: hypothetical_entry_at={hypothetical_entry_at} "
            f"must be strictly after action_eligible_at={action_eligible_at}"
        )

    entry_row = series.row_on(hypothetical_entry_at)
    entry_price = entry_row["open"]
    entry_price_available_at = series.first_capture_date_for(hypothetical_entry_at)

    result["hypothetical_entry_at"] = hypothetical_entry_at
    result["entry_date"] = hypothetical_entry_at
    result["entry_price"] = entry_price
    result["entry_price_available_at"] = entry_price_available_at
    result["status"] = "OK"

    for h in HORIZONS:
        window = forward_dates[:h]  # day 1 == hypothetical_entry_at itself
        if len(window) < h:
            result["horizons"][str(h)] = {
                "status": "INSUFFICIENT_HORIZON_DATA",
                "trading_days_available": len(window),
                "trading_days_required": h,
            }
            continue
        highs, lows = [], []
        for d in window:
            row = series.row_on(d)
            highs.append(row["high"])
            lows.append(row["low"])
        end_date = window[-1]
        end_close = series.close_on(end_date)
        result["horizons"][str(h)] = {
            "status": "OK",
            "end_date": end_date,
            "forward_return_pct": _pct(entry_price, end_close),
            "mfe_pct": _pct(entry_price, max(highs)),
            "mae_pct": _pct(entry_price, min(lows)),
        }
    return result
