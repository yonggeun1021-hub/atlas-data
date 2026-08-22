#!/usr/bin/env python3
"""Forward return / MFE / MAE at 1/3/5/10 trading days (deliverable 4).

★ CIO review fix (flaw 4, PR #210): the entry price used for grading MUST be
  anchored to the exact same trading date the signal itself was evaluated
  against (`entry_date`, typically `trigger_engine`'s `evaluation_date`) --
  never independently re-derived as "latest trading date <= decision_date"
  from the full merged series. The old behavior could silently pick a LATER
  trading date than the signal's own evaluation date whenever that later
  date's row existed in the merged series but was not yet live-known as of
  decision_date (e.g. BTC's collector T-1 finalization lag). That is exactly
  the kind of contamination `lookahead_gate.py` exists to catch, just at the
  entry-price step instead of the trigger-detection step.

  Every result now carries an explicit timestamp trail so this alignment
  cannot silently regress:
    - `signal_evaluation_at`   -- the trading date the signal itself was
      evaluated against (caller-supplied `entry_date`).
    - `action_eligible_at`     -- the date the signal became knowable/
      actionable given collector lag (caller-supplied `decision_date`).
    - `hypothetical_entry_at`  -- always equal to `signal_evaluation_at`
      here (the honest, simplest execution assumption given this repo's
      daily-OHLC-only granularity -- see `execution_assumption`).
    - `entry_price_available_at` -- the earliest snapshot capture_date that
      actually reported `hypothetical_entry_at`'s close.
    - `execution_assumption`  -- a human-readable note making the gap
      between `signal_evaluation_at` and `action_eligible_at` explicit
      rather than hidden.

  If `hypothetical_entry_at`'s price was not actually live-known as of
  `action_eligible_at` (a defensive check -- should not trip given the
  caller discipline in `signal_replay_ledger.py`, but enforced here rather
  than assumed), the whole entry is marked `status = "NOT_GRADABLE"` instead
  of silently computing a number from an unknowable price.

PIT discipline enforced here:
  * every date used for the N-day-forward return/MFE/MAE is required, via
    `lookahead_gate.assert_forward_only`, to be strictly AFTER
    `hypothetical_entry_at` (not decision_date -- see above).
  * "trading day" means an actual row present in the committed price series
    (a real KRX trading session, or a real Kraken daily bar) -- never an
    interpolated or assumed calendar day.
  * when the committed series does not contain enough forward rows to reach
    a horizon, that horizon is reported as INSUFFICIENT_HORIZON_DATA rather
    than computed from a partial or synthetic window.
"""
from __future__ import annotations

from replay.lookahead_gate import assert_forward_only
from replay.price_series import PriceSeries

HORIZONS = (1, 3, 5, 10)

EXECUTION_ASSUMPTION = (
    "hypothetical fill at signal_evaluation_at's finalized close (the same "
    "close the trigger itself fired on). This is the most conservative "
    "assumption this repo's daily-OHLC-only evidence supports -- there is "
    "no intraday execution price available. It likely OVERSTATES capturable "
    "return by the collector's own finalization lag: the signal only became "
    "knowable at action_eligible_at, which can be 1+ calendar days after "
    "signal_evaluation_at (see the two fields' values on this entry)."
)


def _pct(a: float, b: float) -> float:
    return (b - a) / a * 100.0


def compute_forward_metrics(series: PriceSeries, decision_date: str, entry_date: str | None = None) -> dict:
    """`decision_date` is `action_eligible_at` (when the signal became
    knowable). `entry_date`, when supplied, MUST be a real trading date
    present in `series` and is used verbatim as `signal_evaluation_at` /
    `hypothetical_entry_at` -- callers grading a real detected signal (see
    `signal_replay_ledger.py`) MUST always pass the exact same date the
    signal itself was evaluated against. When omitted, falls back to the
    latest trading date <= decision_date (only appropriate for callers with
    no signal-side context at all, e.g. ad hoc price-only queries)."""
    if entry_date is None:
        candidates = series.trading_dates_at_or_before(decision_date)
        entry_date = candidates[-1] if candidates else None
        entry_date_source = "auto_latest_at_or_before_decision_date"
    else:
        entry_date_source = "explicit_signal_evaluation_date"

    result = {
        "subject": series.subject,
        "decision_date": decision_date,
        "action_eligible_at": decision_date,
        "signal_evaluation_at": entry_date,
        "hypothetical_entry_at": entry_date,
        "entry_date": entry_date,           # kept for backward-compatible readers
        "entry_date_source": entry_date_source,
        "entry_close": None,
        "entry_price_available_at": None,
        "entry_live_known_asof_decision_date": None,
        "execution_assumption": EXECUTION_ASSUMPTION,
        "horizons": {},
    }
    if entry_date is None or series.close_on(entry_date) is None:
        result["status"] = "NO_ENTRY_PRICE_DATA"
        for h in HORIZONS:
            result["horizons"][str(h)] = {"status": "NO_ENTRY_PRICE_DATA"}
        return result

    entry_close = series.close_on(entry_date)
    entry_available_at = series.first_capture_date_for(entry_date)
    live_known = series.live_known_asof(entry_date, decision_date)
    result["entry_close"] = entry_close
    result["entry_price_available_at"] = entry_available_at
    result["entry_live_known_asof_decision_date"] = live_known

    # ★ The strict NOT_GRADABLE gate (flaw 4) applies ONLY when a real,
    #   signal-anchored entry_date was explicitly supplied -- i.e. this
    #   entry is claiming to grade a hypothetical trade off a real detected
    #   trigger, and that claim must not rest on an unknowable price.
    #
    #   When entry_date was auto-selected (no live signal existed at all --
    #   typically the pre-2026-08-13 DATA_FAILURE sub-window), this function
    #   is instead answering a DIFFERENT, legitimate question: "what did the
    #   market actually do around this date?" (an opportunity-cost / defense
    #   measurement using real historical prices, never claimed as an
    #   executable hypothetical trade). That is explicitly allowed even when
    #   `entry_live_known_asof_decision_date` is False -- the flag is
    #   reported for transparency, not used to block grading here, exactly
    #   as originally designed (see evidence_index.py's module docstring).
    if entry_date_source == "explicit_signal_evaluation_date" and (entry_date > decision_date or not live_known):
        result["status"] = "NOT_GRADABLE"
        result["not_gradable_reason"] = (
            f"hypothetical_entry_at={entry_date} was not live-known as of "
            f"action_eligible_at={decision_date} "
            f"(first genuinely available {entry_available_at})"
        )
        for h in HORIZONS:
            result["horizons"][str(h)] = {"status": "NOT_GRADABLE"}
        return result

    result["status"] = "OK"

    all_forward = series.trading_dates_strictly_after(entry_date)
    assert_forward_only(entry_date, all_forward, label=f"{series.subject}_forward_window")

    for h in HORIZONS:
        window = all_forward[:h]
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
            "forward_return_pct": _pct(entry_close, end_close),
            "mfe_pct": _pct(entry_close, max(highs)),
            "mae_pct": _pct(entry_close, min(lows)),
        }
    return result
