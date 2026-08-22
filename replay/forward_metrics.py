#!/usr/bin/env python3
"""Forward return / MFE / MAE at 1/3/5/10 trading days (deliverable 4).

PIT discipline enforced here:
  * the ENTRY reference is the close on the latest real trading date <=
    decision_date (never a date after it).
  * every date used for the N-day-forward return/MFE/MAE is required, via
    `lookahead_gate.assert_forward_only`, to be strictly AFTER decision_date.
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


def _pct(a: float, b: float) -> float:
    return (b - a) / a * 100.0


def compute_forward_metrics(series: PriceSeries, decision_date: str) -> dict:
    entry_dates = series.trading_dates_at_or_before(decision_date)
    result = {
        "subject": series.subject,
        "decision_date": decision_date,
        "entry_date": None,
        "entry_close": None,
        "entry_live_known_asof_decision_date": None,
        "horizons": {},
    }
    if not entry_dates:
        result["status"] = "NO_ENTRY_PRICE_DATA"
        for h in HORIZONS:
            result["horizons"][str(h)] = {"status": "NO_ENTRY_PRICE_DATA"}
        return result

    entry_date = entry_dates[-1]
    entry_close = series.close_on(entry_date)
    result["entry_date"] = entry_date
    result["entry_close"] = entry_close
    result["entry_live_known_asof_decision_date"] = series.live_known_asof(entry_date, decision_date)
    result["status"] = "OK"

    all_forward = series.trading_dates_strictly_after(decision_date)
    assert_forward_only(decision_date, all_forward, label=f"{series.subject}_forward_window")

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
