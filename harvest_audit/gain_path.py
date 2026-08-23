#!/usr/bin/env python3
"""P7-11 Baseline Audit -- PIT-safe gain-path measurement for one episode.

This is a DIAGNOSTIC measurement only: it answers "if a hypothetical entry
had been taken at the first real, executable price after an episode's
action_eligible_at, how did the price path actually unfold afterward" --
never a sell-policy, never a real quantity, never an action.

★ Entry timing rule (byte-identical in spirit to `replay.forward_metrics.
  compute_forward_metrics`, intentionally re-derived here rather than
  importing that function, so this module can extend the per-horizon
  computation to 1/3/5/10/20 and expose the full day-by-day path
  `compute_forward_metrics` deliberately does not -- see
  `test_forward_return_1_3_5_10_matches_replay_forward_metrics` for the
  cross-validation regression proving the two stay numerically identical
  wherever they overlap):
    `hypothetical_entry_at` = the first REAL trading date in the committed
    series strictly after `action_eligible_at`, priced at that date's
    OPEN. If none exists, the episode is NOT_GRADABLE.

★ PIT timing contract (per horizon and per whole-path field):
    `signal_evaluation_at <= action_eligible_at < hypothetical_entry_at
    <= evaluation_horizon_end`
  enforced structurally by `_validate_gain_path_timing`, not merely
  asserted at one call site.

★ Future prices are used ONLY to MEASURE what already-committed history
  shows happened after entry -- never re-injected into what the entry/
  harvest judgment would have been AT THE TIME (there is no "judgment"
  produced by this module at all; see docs for the authority boundary).

★ No giveback is ever computed using any day BEFORE the MFE (max
  favorable excursion) date -- see `max_giveback_after_mfe_pct`.
"""
from __future__ import annotations

HORIZONS = (1, 3, 5, 10, 20)


class GainPathTimingError(ValueError):
    pass


def _pct(a: float, b: float) -> float:
    return (b - a) / a * 100.0


def market_calendar_of(market: str) -> str:
    if market == "KOREA":
        return "KRX_WEEKDAY"
    if market == "BTC":
        return "BTC_24_7"
    if market == "CRYPTO":
        return "CRYPTO_24_7"
    return "UNKNOWN"


def _validate_gain_path_timing(signal_evaluation_at: str | None, action_eligible_at: str,
                                hypothetical_entry_at: str, evaluation_horizon_end: str) -> None:
    if signal_evaluation_at is not None and signal_evaluation_at > action_eligible_at:
        raise GainPathTimingError(
            f"TIMING_INVARIANT_VIOLATED:signal_evaluation_at({signal_evaluation_at})"
            f">action_eligible_at({action_eligible_at})"
        )
    if not (hypothetical_entry_at > action_eligible_at):
        raise GainPathTimingError(
            f"TIMING_INVARIANT_VIOLATED:hypothetical_entry_at({hypothetical_entry_at})"
            f"<=action_eligible_at({action_eligible_at})"
        )
    if evaluation_horizon_end < hypothetical_entry_at:
        raise GainPathTimingError(
            f"TIMING_INVARIANT_VIOLATED:evaluation_horizon_end({evaluation_horizon_end})"
            f"<hypothetical_entry_at({hypothetical_entry_at})"
        )


def compute_gain_path(series, action_eligible_at: str, market: str, *,
                       signal_evaluation_at: str | None = None,
                       max_horizon: int = 20) -> dict:
    """`series` is a `replay.price_series.PriceSeries`. `action_eligible_at`
    is the episode's own `first_action_eligible_date` (never a backdated
    prior-day close). Returns a single dict -- either
    `{"status": "NOT_GRADABLE", ...}` or the full gain-path record."""
    forward_dates = series.trading_dates_strictly_after(action_eligible_at)
    if not forward_dates:
        return {
            "subject": series.subject,
            "market": market,
            "market_calendar": market_calendar_of(market),
            "action_eligible_at": action_eligible_at,
            "signal_evaluation_at": signal_evaluation_at,
            "status": "NOT_GRADABLE",
            "not_gradable_reason": (
                f"no real trading date exists in committed evidence strictly after "
                f"action_eligible_at={action_eligible_at} -- no executable entry point"
            ),
        }

    hypothetical_entry_at = forward_dates[0]
    window = forward_dates[:max_horizon]
    evaluation_horizon_end = window[-1]

    _validate_gain_path_timing(signal_evaluation_at, action_eligible_at,
                                hypothetical_entry_at, evaluation_horizon_end)

    entry_row = series.row_on(hypothetical_entry_at)
    entry_price = entry_row["open"]
    price_evidence_as_of = series.first_capture_date_for(hypothetical_entry_at)

    # ★ Full day-by-day path over the in-scope window only (no future date
    # beyond max_horizon trading days is ever touched by this function).
    rows = [(d, series.row_on(d)) for d in window]

    # -- MFE / MAE (whole-window peak/trough, with the date each first
    #    occurred -- ties broken to the EARLIEST date, never the latest,
    #    so time_to_mfe/time_to_mae are never overstated).
    mfe_price = max(row["high"] for _, row in rows)
    mfe_date = next(d for d, row in rows if row["high"] == mfe_price)
    mae_price = min(row["low"] for _, row in rows)
    mae_date = next(d for d, row in rows if row["low"] == mae_price)
    time_to_mfe_days = window.index(mfe_date) + 1
    time_to_mae_days = window.index(mae_date) + 1

    # -- first positive return (close-based).
    first_positive = next((d for d, row in rows if row["close"] > entry_price), None)
    time_to_first_positive_return_days = (window.index(first_positive) + 1) if first_positive else None

    # -- giveback after MFE: NEVER uses a date strictly BEFORE mfe_date
    #    (test 11). The MFE day's OWN low is deliberately INCLUDED (`>=`,
    #    not `>`) -- a daily OHLC bar cannot prove whether that day's high
    #    or low occurred first intraday, so including the peak day's own
    #    low is the conservative convention, not a lookahead or a defect.
    post_mfe_rows = [(d, row) for d, row in rows if d >= mfe_date]
    post_mfe_low = min(row["low"] for _, row in post_mfe_rows)
    max_giveback_after_mfe_pct = _pct(mfe_price, post_mfe_low)

    # -- breakeven-after-giveback: only meaningful once price has actually
    #    given back BELOW breakeven after the peak.
    giveback_below_breakeven_dates = [d for d, row in post_mfe_rows if row["close"] <= entry_price]
    if not giveback_below_breakeven_dates:
        breakeven_status = "NO_GIVEBACK_BELOW_BREAKEVEN"
        time_to_breakeven_after_positive_mfe_days = None
    else:
        first_giveback = giveback_below_breakeven_dates[0]
        recovery_candidates = [d for d, row in post_mfe_rows if d >= first_giveback and row["close"] >= entry_price]
        if recovery_candidates:
            breakeven_status = "RECOVERED"
            recovery_date = recovery_candidates[0]
            time_to_breakeven_after_positive_mfe_days = window.index(recovery_date) + 1
        else:
            breakeven_status = "NOT_RECOVERED_IN_WINDOW"
            time_to_breakeven_after_positive_mfe_days = None

    # -- duration buckets.
    positive_return_duration_days = sum(1 for _, row in rows if row["close"] > entry_price)
    underwater_duration_days = sum(1 for _, row in rows if row["close"] < entry_price)
    at_breakeven_duration_days = len(rows) - positive_return_duration_days - underwater_duration_days

    # -- terminal (endpoint-truncated) outcome.
    terminal_close = rows[-1][1]["close"]
    terminal_return_pct = _pct(entry_price, terminal_close)
    peak_to_terminal_giveback_pct = _pct(mfe_price, terminal_close)

    # -- per-horizon table, 1/3/5/10/20, each independently PIT-safe: MFE
    #    used for retention is computed ONLY over that horizon's own
    #    sub-window (never the full-window/global peak), so an early
    #    horizon's retention ratio never uses information from a later day.
    horizons: dict[str, dict] = {}
    for h in HORIZONS:
        sub = rows[:h]
        if len(sub) < h:
            horizons[str(h)] = {
                "status": "INSUFFICIENT_HORIZON_DATA",
                "trading_days_available": len(sub),
                "trading_days_required": h,
            }
            continue
        h_end_date = sub[-1][0]
        h_end_close = sub[-1][1]["close"]
        h_mfe = max(row["high"] for _, row in sub)
        h_mae = min(row["low"] for _, row in sub)
        h_forward_return_pct = _pct(entry_price, h_end_close)
        h_mfe_pct = _pct(entry_price, h_mfe)
        h_mae_pct = _pct(entry_price, h_mae)
        if h_mfe_pct > 0:
            retention_status = "OK"
            mfe_retention_ratio = h_forward_return_pct / h_mfe_pct
        else:
            retention_status = "NOT_COMPUTABLE_NO_POSITIVE_MFE"
            mfe_retention_ratio = None
        horizons[str(h)] = {
            "status": "OK",
            "end_date": h_end_date,
            "forward_return_pct": h_forward_return_pct,
            "mfe_pct": h_mfe_pct,
            "mae_pct": h_mae_pct,
            "mfe_retention_ratio_status": retention_status,
            "mfe_retention_ratio": mfe_retention_ratio,
        }

    return {
        "subject": series.subject,
        "market": market,
        "market_calendar": market_calendar_of(market),
        "status": "OK",
        "time_precision": "DATE_ONLY",
        # PIT timing contract fields (B-4).
        "signal_evaluation_at": signal_evaluation_at,
        "action_eligible_at": action_eligible_at,
        "hypothetical_entry_at": hypothetical_entry_at,
        "entry_price": entry_price,
        "price_evidence_as_of": price_evidence_as_of,
        "evaluation_horizon_end": evaluation_horizon_end,
        # continuous measurements (B-5).
        "mfe_pct": _pct(entry_price, mfe_price),
        "mfe_date": mfe_date,
        "time_to_mfe_days": time_to_mfe_days,
        "mae_pct": _pct(entry_price, mae_price),
        "mae_date": mae_date,
        "time_to_mae_days": time_to_mae_days,
        "first_positive_return_date": first_positive,
        "time_to_first_positive_return_days": time_to_first_positive_return_days,
        "breakeven_after_positive_mfe_status": breakeven_status,
        "time_to_breakeven_after_positive_mfe_days": time_to_breakeven_after_positive_mfe_days,
        "max_giveback_after_mfe_pct": max_giveback_after_mfe_pct,
        "terminal_return_pct": terminal_return_pct,
        "peak_to_terminal_giveback_pct": peak_to_terminal_giveback_pct,
        "positive_return_duration_days": positive_return_duration_days,
        "underwater_duration_days": underwater_duration_days,
        "at_breakeven_duration_days": at_breakeven_duration_days,
        "horizons": horizons,
        # endpoint coverage -- honest about data truncation, never
        # interpolated (B-6's "no arbitrary endpoint interpolation").
        "endpoint_coverage": {
            "trading_days_available_total": len(forward_dates),
            "trading_days_used": len(window),
            "max_horizon_requested": max_horizon,
            "full_horizon_reached": len(window) >= max_horizon,
        },
    }
