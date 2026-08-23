#!/usr/bin/env python3
"""P8-12 operational trigger scan -- BTC / Korea / Crypto.

★ Reuse, not reimplementation: every actual trigger DETECTION here calls
  straight into PR #210's `replay.trigger_engine` functions (unmodified) --
  the same `price_confirmation` / `invalidation_trigger` / `flow_reversal` /
  `relative_strength_reversal` code the retrospective audit used, which
  itself enforces anti-lookahead via `replay.lookahead_gate` at every call.
  This module's only job is (a) deciding, per market, WHICH of the seven
  trigger types are actually computable from this repo's real committed
  evidence today (never silently omitting the rest -- see
  `MARKET_TRIGGER_COMPUTABILITY`), and (b) turning the resulting
  `OpportunityTriggerEvent` objects into `clock.dynamic_clock.ClockEvent`s
  for the state machine in `dynamic_clock.py`.

★ PIT-eligibility boundaries preserved from PR #210, not re-litigated here:
    - Korea: `config/universe.json`'s current watchlist is used as the
      OPERATIONAL scan population (this is a forward-looking, real-time
      clock, so scanning "what should we watch starting today" is exactly
      the right use of the current watchlist) -- but it is explicitly
      labeled `CURRENT_WATCHLIST_OPERATIONAL_COHORT`, never presented as a
      reconstructed historical PIT-eligible universe (that upgrade is
      exactly what PR #210 refused to do, for the same file, and this
      module does not silently reverse that call).
    - Crypto: only pairs `replay.asset_identity.crypto_pit_eligible_pair_ids`
      says are RATIFIED-eligible as of each scanned date are ever scanned --
      the same real, mostly-empty-before-2026-08-19 boundary PR #210 used.
    - BTC: its own dedicated collector, identity always PASS (see
      `replay.asset_identity`'s module docstring).

Every trigger-detection date scanned is real committed evidence's own
capture_date range -- `replay.evidence_index.REPO_HISTORY_STARTS_AT` through
the latest capture_date this repo actually has for that market (or through
`decision_date` when one is supplied -- see below). No `datetime.now()`
anywhere in this module.

★ CIO integration review round 1, defect 1 (PIT lookahead via post-hoc
  `max()` correction): every `scan_*` function now accepts an optional
  `decision_date`. When given, snapshots dated AFTER `decision_date` are
  filtered out BEFORE any series is built or any trigger is detected --
  filtering happens at THIS scanning boundary, not by patching results
  afterward. This is what makes real historical replay
  (`clock/run_dynamic_clock.py`'s `mode="HISTORICAL_REPLAY"`) genuinely
  safe: a request for `decision_date=2026-08-20` can structurally never see
  a 2026-08-21 or 2026-08-22 snapshot, because that snapshot is removed
  from the candidate list before `build_btc_series`/`build_krx_series`/
  `crypto_breadth_series` ever touches it.
"""
from __future__ import annotations

import datetime as dt

from replay import asset_identity as ai
from replay import evidence_index as ei
from replay import trigger_engine as te
from replay.price_series import PriceSeries, build_btc_series, build_krx_series
from replay import universe_scan as us

from clock.dynamic_clock import ClockEvent

ALL_TRIGGER_TYPES = (
    "PRICE_CONFIRMATION",
    "INVALIDATION_TRIGGER",
    "FLOW_REVERSAL",
    "RELATIVE_STRENGTH_REVERSAL",
    "FUNDAMENTAL_REVISION",
    "CATALYST_APPROACH",
    "EXPECTATION_DISLOCATION",
)
assert set(ALL_TRIGGER_TYPES) == set(te.NOT_COMPUTABLE_TYPES) | {
    "PRICE_CONFIRMATION", "INVALIDATION_TRIGGER", "FLOW_REVERSAL", "RELATIVE_STRENGTH_REVERSAL",
}

_STRUCTURAL_NOT_COMPUTABLE = {t: "no parsed guidance/catalyst-calendar series is committed anywhere "
                                  "in this repo as a dated structured series (see replay/trigger_engine.py)"
                              for t in te.NOT_COMPUTABLE_TYPES}

MARKET_TRIGGER_COMPUTABILITY: dict[str, dict[str, str]] = {
    "BTC": {
        "PRICE_CONFIRMATION": "COMPUTABLE",
        "INVALIDATION_TRIGGER": "COMPUTABLE",
        "FLOW_REVERSAL": "NOT_COMPUTABLE",
        "RELATIVE_STRENGTH_REVERSAL": "NOT_COMPUTABLE",
        **{t: "NOT_COMPUTABLE" for t in te.NOT_COMPUTABLE_TYPES},
    },
    "KOREA": {
        "PRICE_CONFIRMATION": "COMPUTABLE",
        "INVALIDATION_TRIGGER": "COMPUTABLE",
        "FLOW_REVERSAL": "COMPUTABLE",
        "RELATIVE_STRENGTH_REVERSAL": "COMPUTABLE",
        **{t: "NOT_COMPUTABLE" for t in te.NOT_COMPUTABLE_TYPES},
    },
    "CRYPTO": {
        "PRICE_CONFIRMATION": "COMPUTABLE",
        "INVALIDATION_TRIGGER": "COMPUTABLE",
        "FLOW_REVERSAL": "NOT_COMPUTABLE",
        "RELATIVE_STRENGTH_REVERSAL": "COMPUTABLE",
        **{t: "NOT_COMPUTABLE" for t in te.NOT_COMPUTABLE_TYPES},
    },
}

NOT_COMPUTABLE_REASONS: dict[tuple[str, str], str] = {
    ("BTC", "FLOW_REVERSAL"): "BTC's dedicated Kraken OHLC collector carries no investor/exchange net-flow field",
    ("BTC", "RELATIVE_STRENGTH_REVERSAL"): "no peer asset series exists in this repo for BTC's single-asset dedicated collector",
    ("CRYPTO", "FLOW_REVERSAL"): "the crypto breadth collector carries OHLC only, no on/off-chain or exchange net-flow field",
}
for _market, _types in MARKET_TRIGGER_COMPUTABILITY.items():
    for _t, _status in _types.items():
        if _status == "NOT_COMPUTABLE" and (_market, _t) not in NOT_COMPUTABLE_REASONS:
            NOT_COMPUTABLE_REASONS[(_market, _t)] = _STRUCTURAL_NOT_COMPUTABLE[_t]


def not_computable_report(market: str) -> list[dict]:
    return [
        {"trigger_type": t, "status": "NOT_COMPUTABLE", "reason": NOT_COMPUTABLE_REASONS[(market, t)]}
        for t, status in sorted(MARKET_TRIGGER_COMPUTABILITY[market].items())
        if status == "NOT_COMPUTABLE"
    ]


def _date_range(start: str, end: str) -> list[str]:
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    out, d = [], d0
    while d <= d1:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def _to_clock_event(trig) -> ClockEvent:
    """Maps a `replay.opportunity_trigger.OpportunityTriggerEvent` onto the
    Dynamic Clock's `ClockEvent` shape (see `dynamic_clock.py` docstring for
    the `detected_at` vs `evidence_available_at` distinction)."""
    evidence_available_at = trig.confirmed_at or trig.first_seen_at
    return ClockEvent(
        detected_at=trig.decision_date,
        evidence_available_at=evidence_available_at,
        evidence_hash=trig.evidence_sha256,
        source=trig.source,
        strength=trig.strength,
    )


def _events_by_type(raw_events: dict[str, list]) -> dict[str, list[ClockEvent]]:
    return {t: [_to_clock_event(e) for e in events] for t, events in raw_events.items()}


def _filter_snapshots(snapshots: list, decision_date: str | None) -> list:
    """Scanner-level PIT filter (defect 1): drops any snapshot captured
    AFTER `decision_date`, BEFORE it is ever handed to a series builder or
    trigger detector. A no-op when `decision_date` is None (the bare
    artifact-reproduction / "use whatever is latest" mode)."""
    if decision_date is None:
        return snapshots
    return [s for s in snapshots if s.capture_date <= decision_date]


def all_evidence_dates(market: str) -> list[str]:
    """Cheap peek at every REAL committed capture_date for `market`,
    completely unfiltered -- no series building, no trigger detection.
    Used by `clock/run_dynamic_clock.py`'s OPERATIONAL-mode fail-closed
    check (`DECISION_DATE_PRECEDES_EVIDENCE_AS_OF`) to learn what evidence
    genuinely exists before deciding whether a caller-supplied
    `decision_date` is anomalously behind it -- kept separate from the
    (expensive) full scan so that check never needs the expensive path."""
    if market == "BTC":
        return [s.capture_date for s in ei.find_btc_snapshots()]
    if market == "KOREA":
        return [s.capture_date for s in ei.find_krx_snapshots()]
    if market == "CRYPTO":
        return [s.capture_date for s in ei.find_breadth_snapshots()]
    raise ValueError(f"UNKNOWN_MARKET:{market}")


def scan_btc(decision_date: str | None = None) -> dict:
    """Returns {"BTC": {trigger_type: [ClockEvent, ...]}} for every
    COMPUTABLE trigger type, using only real committed BTC evidence dated
    at or before `decision_date` (all evidence, when `decision_date` is
    None)."""
    snapshots = _filter_snapshots(ei.find_btc_snapshots(), decision_date)
    if not snapshots:
        return {"subjects": {}, "population_label": "DEDICATED_COLLECTOR", "evidence_dates": []}
    series = build_btc_series(snapshots)
    dates = _date_range(ei.REPO_HISTORY_STARTS_AT, snapshots[-1].capture_date)
    raw: dict[str, list] = {"PRICE_CONFIRMATION": [], "INVALIDATION_TRIGGER": []}
    for d in dates:
        snap = ei.snapshot_at_or_before(snapshots, d)
        if snap is None:
            continue
        source, sha = snap.citation(), snap.sha256
        raw["PRICE_CONFIRMATION"] += te.price_confirmation(series, d, source, sha)
        raw["INVALIDATION_TRIGGER"] += te.invalidation_trigger(series, d, source, sha)
    return {
        "subjects": {"BTC": _events_by_type(raw)},
        "series": {"BTC": series},
        "population_label": "DEDICATED_COLLECTOR",
        "evidence_dates": [s.capture_date for s in snapshots],
    }


def scan_korea(decision_date: str | None = None) -> dict:
    """Korea market scan over the CURRENT watchlist -- see module docstring
    for why this is a valid operational (not historical-PIT) population.
    Evidence dated after `decision_date` is filtered out before any series
    is built (defect 1)."""
    krx_snapshots = _filter_snapshots(ei.find_krx_snapshots(), decision_date)
    if not krx_snapshots:
        return {"subjects": {}, "population_label": "CURRENT_WATCHLIST_OPERATIONAL_COHORT", "evidence_dates": []}
    universe_codes = {row["code"] for row in ei.load_universe().get("kr", [])}
    seen_codes = set(ei.all_krx_codes(krx_snapshots))
    codes = sorted(universe_codes | seen_codes)
    kr_series: dict[str, PriceSeries] = {code: build_krx_series(code, krx_snapshots) for code in codes}
    dates = _date_range(ei.REPO_HISTORY_STARTS_AT, krx_snapshots[-1].capture_date)

    subjects: dict[str, dict[str, list[ClockEvent]]] = {}
    for code, series in kr_series.items():
        raw: dict[str, list] = {
            "PRICE_CONFIRMATION": [], "INVALIDATION_TRIGGER": [],
            "FLOW_REVERSAL": [], "RELATIVE_STRENGTH_REVERSAL": [],
        }
        peers = kr_series
        for d in dates:
            snap = ei.snapshot_at_or_before(krx_snapshots, d)
            if snap is None:
                continue
            source, sha = snap.citation(code), snap.sha256
            raw["PRICE_CONFIRMATION"] += te.price_confirmation(series, d, source, sha)
            raw["INVALIDATION_TRIGGER"] += te.invalidation_trigger(series, d, source, sha)
            raw["FLOW_REVERSAL"] += te.flow_reversal(series, d, source, sha)
            raw["RELATIVE_STRENGTH_REVERSAL"] += te.relative_strength_reversal(series, peers, d, source, sha)
        subjects[code] = _events_by_type(raw)

    return {
        "subjects": subjects,
        "series": kr_series,
        "population_label": "CURRENT_WATCHLIST_OPERATIONAL_COHORT",
        "evidence_dates": [s.capture_date for s in krx_snapshots],
        "kr_universe_codes": universe_codes | seen_codes,
    }


def scan_crypto(decision_date: str | None = None) -> dict:
    """Crypto market scan over the RATIFIED PIT-eligible taxonomy only --
    real evidence: this is an empty population for essentially the whole
    window before 2026-08-19 (see `replay.asset_identity` docstring), which
    this scan reproduces honestly rather than substituting the full
    source catalog. Evidence dated after `decision_date` is filtered out
    before any series is built (defect 1)."""
    breadth_snapshots = _filter_snapshots(ei.find_breadth_snapshots(), decision_date)
    if not breadth_snapshots:
        return {"subjects": {}, "population_label": "PIT_RATIFIED_ELIGIBLE_UNIVERSE", "evidence_dates": []}
    latest_date = breadth_snapshots[-1].capture_date
    known_pair_ids = set(breadth_snapshots[-1].pair_ids())
    ever_eligible = ai.crypto_pit_eligible_pair_ids(latest_date, known_pair_ids)
    breadth_series = {pid: us.crypto_breadth_series(breadth_snapshots, pid) for pid in ever_eligible}
    dates = _date_range(ei.REPO_HISTORY_STARTS_AT, latest_date)

    subjects: dict[str, dict[str, list[ClockEvent]]] = {}
    for date in dates:
        eligible_today = ai.crypto_pit_eligible_pair_ids(date, known_pair_ids)
        snap = ei.snapshot_at_or_before(breadth_snapshots, date)
        if snap is None:
            continue
        source_sha = snap.sha256
        for pid in sorted(eligible_today):
            series = breadth_series[pid]
            source = snap.citation(pid)
            bucket = subjects.setdefault(pid, {
                "PRICE_CONFIRMATION": [], "INVALIDATION_TRIGGER": [], "RELATIVE_STRENGTH_REVERSAL": [],
            })
            bucket["PRICE_CONFIRMATION"] += te.price_confirmation(series, date, source, source_sha)
            bucket["INVALIDATION_TRIGGER"] += te.invalidation_trigger(series, date, source, source_sha)
            bucket["RELATIVE_STRENGTH_REVERSAL"] += te.relative_strength_reversal(
                series, breadth_series, date, source, source_sha,
            )

    return {
        "subjects": {pid: _events_by_type(raw) for pid, raw in subjects.items()},
        "series": breadth_series,
        "population_label": "PIT_RATIFIED_ELIGIBLE_UNIVERSE",
        "evidence_dates": [s.capture_date for s in breadth_snapshots],
        "pit_eligible_population_size_at_latest_evidence_date": len(ever_eligible),
    }


MARKET_SCANNERS = {"BTC": scan_btc, "KOREA": scan_korea, "CRYPTO": scan_crypto}
