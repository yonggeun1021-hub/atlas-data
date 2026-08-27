#!/usr/bin/env python3
"""Proposed Opportunity Trigger Engine (Control Loop doc section 3),
implemented against REAL committed market/flow data only.

Every detector below only ever reads price/flow rows dated <= decision_date
from the subject's merged PriceSeries (enforced with
`lookahead_gate.assert_no_signal_lookahead`). Three of the doc's seven
trigger types (FUNDAMENTAL_REVISION, CATALYST_APPROACH,
EXPECTATION_DISLOCATION) require parsed guidance/catalyst-calendar data that
is not committed anywhere in this repo as a dated, structured series -- they
are represented as NOT_COMPUTABLE rather than guessed at. This is itself a
finding (see the audit report), not a silent gap.

Each detector evaluates against the most recently FINALIZED, live-known
trading date at or before decision_date (`window[-1]`) -- which is allowed
to lag decision_date itself. This matches real collector behavior (e.g. the
BTC collector always excludes "today" and only finalizes through T-1, so a
same-day close is never live-known on the day itself) rather than pretending
same-day data existed. That lag is surfaced as `evaluation_date` /
`evaluation_lag_days` by `signal_replay_ledger.py`, which feeds the
DECISION_LATENCY root-cause category.

This module has NO authority: it returns `OpportunityTriggerEvent` objects
only (shadow/observational, capital=0 by construction of that type). It
never writes a Stage, trade_proposal, or order.
"""
from __future__ import annotations

from replay.lookahead_gate import assert_no_signal_lookahead
from replay.opportunity_trigger import build_trigger_event
from replay.price_series import PriceSeries

NOT_COMPUTABLE_TYPES = ("FUNDAMENTAL_REVISION", "CATALYST_APPROACH", "EXPECTATION_DISLOCATION")


def window_at_or_before(series: PriceSeries, decision_date: str, n: int) -> list[str]:
    # ★ SIGNAL side: only dates whose row was itself captured by a snapshot
    #   dated <= decision_date. This is what makes every trigger check
    #   automatically return [] for the whole pre-2026-08-13 sub-window --
    #   Atlas's own committed evidence trail starts there, so it could not
    #   have detected anything before it (see price_series.py docstring).
    dates = series.live_trading_dates_at_or_before(decision_date)
    assert_no_signal_lookahead(decision_date, dates, label=f"{series.subject}_window")
    return dates[-n:] if len(dates) >= n else dates


def price_confirmation(series: PriceSeries, decision_date: str, source: str, evidence_sha: str,
                        lookback: int = 20) -> list:
    window = window_at_or_before(series, decision_date, lookback)
    if len(window) < lookback:
        return []
    eval_date = window[-1]
    closes = [series.close_on(d) for d in window]
    if closes[-1] >= max(closes):
        strength = min(1.0, (closes[-1] - min(closes)) / (max(closes) - min(closes) + 1e-9))
        return [build_trigger_event(
            "PRICE_CONFIRMATION", series.subject, eval_date, decision_date,
            source, evidence_sha, strength,
            confirmed_at=eval_date,
        )]
    return []


def invalidation_trigger(series: PriceSeries, decision_date: str, source: str, evidence_sha: str,
                          lookback: int = 20) -> list:
    window = window_at_or_before(series, decision_date, lookback)
    if len(window) < lookback:
        return []
    eval_date = window[-1]
    closes = [series.close_on(d) for d in window]
    if closes[-1] <= min(closes):
        strength = min(1.0, (max(closes) - closes[-1]) / (max(closes) - min(closes) + 1e-9))
        return [build_trigger_event(
            "INVALIDATION_TRIGGER", series.subject, eval_date, decision_date,
            source, evidence_sha, strength,
            confirmed_at=eval_date,
        )]
    return []


def flow_reversal(series: PriceSeries, decision_date: str, source: str, evidence_sha: str,
                   flow_key: str = "외국인합계", trailing_opposite_days: int = 2) -> list:
    window = window_at_or_before(series, decision_date, trailing_opposite_days + 1)
    if len(window) < trailing_opposite_days + 1:
        return []
    eval_date = window[-1]
    signs = []
    for d in window:
        row = series.row_on(d)
        nv = (row.get("net_value") or {}).get(flow_key)
        if nv is None:
            return []  # DATA_FAILURE at the caller's level, not a fabricated reversal
        signs.append(1 if nv > 0 else (-1 if nv < 0 else 0))
    prior, today = signs[:-1], signs[-1]
    if today > 0 and all(s < 0 for s in prior):
        return [build_trigger_event(
            "FLOW_REVERSAL", series.subject, eval_date, decision_date,
            source, evidence_sha, 0.6, confirmed_at=eval_date,
        )]
    return []


def relative_strength_reversal(series: PriceSeries, peers: dict[str, PriceSeries], decision_date: str,
                                source: str, evidence_sha: str, lookback: int = 5) -> list:
    subj_window = window_at_or_before(series, decision_date, lookback)
    if len(subj_window) < lookback:
        return []
    eval_date = subj_window[-1]
    subj_ret = (series.close_on(subj_window[-1]) / series.close_on(subj_window[0])) - 1.0

    peer_rets = []
    # ``peers`` is assembled from repository evidence whose discovery path
    # may pass through sets.  Dict insertion order therefore is not a stable
    # arithmetic order across Python processes (different hash seeds).  The
    # floating-point sum below is order-sensitive at the final ULP, and that
    # tiny variation used to change Dynamic Clock candidate hashes and make a
    # freshly published daily briefing fail validation in a second process.
    # Sorting is a serialization/determinism control only; it does not change
    # the peer population or any investment threshold.
    for _, peer in sorted(peers.items(), key=lambda item: item[0]):
        if peer.subject == series.subject:
            continue
        pw = window_at_or_before(peer, decision_date, lookback)
        if len(pw) < lookback or pw[-1] != eval_date:
            continue
        peer_rets.append((peer.close_on(pw[-1]) / peer.close_on(pw[0])) - 1.0)
    if not peer_rets:
        return []  # NOT_COMPUTABLE -- no own-benchmark peer window available, not fabricated
    peer_avg = sum(peer_rets) / len(peer_rets)
    if subj_ret > peer_avg and subj_ret > 0:
        strength = min(1.0, max(0.0, subj_ret - peer_avg))
        return [build_trigger_event(
            "RELATIVE_STRENGTH_REVERSAL", series.subject, eval_date, decision_date,
            source, evidence_sha, strength, confirmed_at=eval_date,
        )]
    return []


def detect_all(series: PriceSeries, decision_date: str, source: str, evidence_sha: str,
                peers: dict[str, PriceSeries] | None = None) -> list:
    events = []
    events += price_confirmation(series, decision_date, source, evidence_sha)
    events += invalidation_trigger(series, decision_date, source, evidence_sha)
    events += flow_reversal(series, decision_date, source, evidence_sha)
    if peers:
        events += relative_strength_reversal(series, peers, decision_date, source, evidence_sha)
    return events
