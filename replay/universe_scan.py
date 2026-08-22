#!/usr/bin/env python3
"""Full-population scan (deliverable: "every ticker that appeared in any
briefing", Discovery/Candidate/Ready coverage, and major up/down movers) --
built only from subjects that genuinely have committed repo evidence.

★ Honesty boundary: this repo does not persist historical briefing TEXT
  anywhere (no committed markdown/JSON briefing output exists for any date --
  `briefing/*.py` are generator modules, not archives). "Every ticker that
  appeared in any briefing" is therefore operationalized as "every ticker
  that appears in this repo's committed collector universe" -- the KR
  equity watchlist (`config/universe.json`, cross-checked against every
  code any KRX snapshot ever reported) plus BTC plus the crypto breadth
  collector's tracked pairs. Anything outside that set cannot be audited
  from repo evidence and is reported as a coverage gap, not silently
  omitted.
"""
from __future__ import annotations

from replay import evidence_index as ei
from replay.price_series import PriceSeries, build_krx_series, krx_name, krx_stage_history


def kr_population(krx_snapshots: list[ei.KrxSnapshot]) -> list[dict]:
    universe = ei.load_universe()
    universe_codes = {row["code"] for row in universe.get("kr", [])}
    seen_codes = set(ei.all_krx_codes(krx_snapshots))
    out = []
    for code in sorted(universe_codes | seen_codes):
        out.append({
            "code": code,
            "name": krx_name(code, krx_snapshots),
            "in_declared_universe": code in universe_codes,
            "seen_in_any_snapshot": code in seen_codes,
            "stage_history": krx_stage_history(code, krx_snapshots),
        })
    return out


def crypto_breadth_series(breadth_snapshots: list[ei.BreadthSnapshot], pair_id: str) -> PriceSeries:
    series = PriceSeries(pair_id)
    for snap in breadth_snapshots:
        for row in snap.rows_for_pair(pair_id):
            series._merge_row(row["date"], {
                "close": row["close"], "open": row["open"],
                "high": row["high"], "low": row["low"], "volume": row["volume"],
            }, snap.capture_date)
    return series


def window_return_pct(series: PriceSeries, start_date: str, end_date: str) -> float | None:
    dates = series.dates()
    if not dates:
        return None
    start_candidates = [d for d in dates if d >= start_date]
    end_candidates = [d for d in dates if d <= end_date]
    if not start_candidates or not end_candidates:
        return None
    d0, d1 = min(start_candidates), max(end_candidates)
    if d0 >= d1:
        return None
    c0, c1 = series.close_on(d0), series.close_on(d1)
    return (c1 - c0) / c0 * 100.0


def top_crypto_movers(breadth_snapshots: list[ei.BreadthSnapshot], window_start: str, window_end: str,
                       top_n: int = 15) -> dict:
    """Real total-window return per pair, ranked. Uses the LATEST breadth
    snapshot's pair catalog (most complete coverage) and its own embedded
    historical rows -- no fabricated pairs, no invented prices."""
    if not breadth_snapshots:
        return {"gainers": [], "losers": [], "status": "NO_BREADTH_EVIDENCE"}
    latest = breadth_snapshots[-1]
    results = []
    for pair_id in latest.pair_ids():
        series = crypto_breadth_series(breadth_snapshots, pair_id)
        ret = window_return_pct(series, window_start, window_end)
        if ret is not None:
            results.append({"pair_id": pair_id, "window_return_pct": ret})
    results.sort(key=lambda r: r["window_return_pct"])
    return {
        "status": "OK",
        "population_size": len(results),
        "gainers": list(reversed(results[-top_n:])),
        "losers": results[:top_n],
    }


def crypto_source_coverage(breadth_snapshots: list[ei.BreadthSnapshot]) -> dict:
    """★ CIO review round 3 (flaw 1): a pure DATA-COVERAGE metric --
    "how many pairs does this repo's committed Kraken breadth catalog have
    ANY real price data for, over what date range" -- NEVER an opportunity
    or investable-universe claim. See `asset_identity.py` /
    `crypto_pit_eligible_pair_ids()` for the actual PIT-eligible Opportunity
    KPI population, which is a real, ratified, much smaller subset."""
    if not breadth_snapshots:
        return {"status": "NO_BREADTH_EVIDENCE", "pair_count": 0}
    latest = breadth_snapshots[-1]
    pair_ids = latest.pair_ids()
    return {
        "status": "OK",
        "pair_count": len(pair_ids),
        "note": "this is a source-catalog coverage count, not a confirmed investable/eligible universe",
    }


def top_kr_movers(krx_snapshots: list[ei.KrxSnapshot], window_start: str, window_end: str) -> dict:
    codes = ei.all_krx_codes(krx_snapshots)
    results = []
    for code in codes:
        series = build_krx_series(code, krx_snapshots)
        ret = window_return_pct(series, window_start, window_end)
        if ret is not None:
            results.append({"code": code, "name": krx_name(code, krx_snapshots), "window_return_pct": ret})
    results.sort(key=lambda r: r["window_return_pct"])
    return {
        "status": "OK" if results else "NO_KRX_EVIDENCE",
        "population_size": len(results),
        "gainers": list(reversed(results)),
        "losers": results,
    }
