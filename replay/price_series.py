#!/usr/bin/env python3
"""Builds real, merged price series for replay subjects from committed
evidence only -- never a fabricated or interpolated bar.

Two distinct facts are tracked per (subject, trading_date) close price:

  * `value`               -- the real close price, sourced from a committed
    snapshot's embedded historical window (KRX `daily` map, or Kraken OHLC
    rows).
  * `first_capture_date`  -- the EARLIEST committed snapshot capture_date
    that reports this trading_date's close. This is what makes a price
    "live-known" to Atlas's own system vs only "retrospectively
    reconstructable" from a later archive.

`live_known_asof(subject, trading_date, decision_date)` answers: could
Atlas's own committed evidence, as it stood on `decision_date`, have told
you this trading_date's close? That is False for essentially all of
2026-07-22..2026-08-12 because the repo's own git history (and therefore
every snapshot's capture_date) starts 2026-08-13 -- there is no snapshot
with capture_date <= decision_date for any decision_date before that. This
is the mechanism that turns "no committed evidence before 08-13" into an
explicit, per-entry DATA_FAILURE finding instead of a silent gap.

Forward-return / MFE / MAE grading (forward_metrics.py) is explicitly
allowed to use `value` for trading_dates after decision_date regardless of
`first_capture_date` -- realized market history is not "lookahead" against
the decision it grades; see `lookahead_gate.py`'s docstring for the two
distinct directions.
"""
from __future__ import annotations

from replay import evidence_index as ei


class PriceSeriesIntegrityError(ValueError):
    pass


def assert_no_integrity_conflicts(series: "PriceSeries") -> None:
    """Strict-mode helper: raises if `series` recorded any cross-snapshot
    close disagreement. Not called by the production replay run (a single
    revised historical bar should not void the whole audit -- see
    `PriceSeries._merge_row`), but available for callers that want to fail
    closed on any integrity conflict."""
    if series.integrity_conflicts:
        raise PriceSeriesIntegrityError(
            f"INTEGRITY_CONFLICTS_PRESENT:{series.subject}:{series.integrity_conflicts}"
        )


class PriceSeries:
    def __init__(self, subject: str):
        self.subject = subject
        # trading_date -> {"close":..., "open":..., "high":..., "low":..., "first_capture_date":...}
        self._by_date: dict[str, dict] = {}
        # real, non-fabricated finding: independently committed snapshots
        # occasionally disagree on a historical close (KRX revision, e.g.).
        # Recorded here rather than silently resolved or allowed to crash
        # the whole replay -- see `integrity_conflicts`.
        self.integrity_conflicts: list[dict] = []
        # CIO growth-driver fix (2026-09-18): `dates()` and
        # `live_trading_dates_at_or_before(decision_date)` used to re-sort
        # and re-filter the WHOLE series on every call. `window_at_or_before`
        # (replay/trigger_engine.py) calls the latter once per subject AND
        # once per peer, per trigger type, per replayed date -- for a scan
        # walking every date since REPO_HISTORY_STARTS_AT over a peer
        # universe of size P, that is O(dates × P) redundant recomputations
        # of the IDENTICAL (series, decision_date) answer, each itself
        # O(series length) -- and series length also grows one row per day.
        # That compounding is what turned a stable per-candidate cost into a
        # step that grows ~2 minutes/day (measured via cProfile: 31.8M calls
        # into `lookahead_gate._as_date` from this exact call chain).
        #
        # Both caches below are keyed on nothing but the inputs the answer
        # actually depends on (`self._by_date` content, and additionally
        # `decision_date` for the second one) and are invalidated by
        # `_merge_row` -- the ONLY method that ever mutates `_by_date` (see
        # its callers: `build_krx_series`/`build_btc_series`/
        # `crypto_breadth_series` fully build a series before anyone queries
        # it; no caller in this repo interleaves a merge after a query). A
        # key can therefore never observe a `_by_date` state other than the
        # one it was computed from.
        self._dates_cache: list[str] | None = None
        self._live_dates_cache: dict[str, list[str]] = {}
        # `window_at_or_before` (replay/trigger_engine.py) is the actual
        # call site every detector uses, and it does two things per call
        # beyond fetching the (now-cached) dates list above: re-validate the
        # whole list with `assert_no_signal_lookahead` and slice the last
        # `n`. Both are exactly as cacheable as the dates list itself for a
        # fixed (decision_date, n) -- see `window_at_or_before_cached`.
        self._window_cache: dict[tuple[str, int], list[str]] = {}

    def _invalidate_caches(self) -> None:
        self._dates_cache = None
        self._live_dates_cache.clear()
        self._window_cache.clear()

    def _merge_row(self, trading_date: str, row: dict, capture_date: str):
        existing = self._by_date.get(trading_date)
        if existing is None:
            self._by_date[trading_date] = {**row, "first_capture_date": capture_date}
            self._invalidate_caches()
            return
        # ★ Integrity check, not a silent overwrite: if two independently
        #   committed snapshots disagree on a historical close, that is
        #   recorded as a data-integrity finding. The EARLIEST-captured
        #   value is kept as canonical (closest to what Atlas would have
        #   seen live), and the run continues -- one stale/revised bar must
        #   not silently void the entire replay.
        if round(existing["close"], 6) != round(row["close"], 6):
            self.integrity_conflicts.append({
                "trading_date": trading_date,
                "kept_close": existing["close"],
                "kept_capture_date": existing["first_capture_date"],
                "conflicting_close": row["close"],
                "conflicting_capture_date": capture_date,
            })
            if capture_date < existing["first_capture_date"]:
                # the earlier-captured snapshot is more authoritative for
                # "what Atlas would have known live" -- swap to it, but
                # keep the conflict recorded either way.
                self._by_date[trading_date] = {**row, "first_capture_date": capture_date}
                self._invalidate_caches()
            return
        if capture_date < existing["first_capture_date"]:
            existing["first_capture_date"] = capture_date
            # first_capture_date moved earlier -- live_known_asof(...) can
            # now return True for decision_dates it previously returned
            # False for, so the per-decision_date caches must drop (the
            # date SET is unchanged, so `_dates_cache` itself stays valid).
            self._live_dates_cache.clear()
            self._window_cache.clear()

    def dates(self) -> list[str]:
        if self._dates_cache is None:
            self._dates_cache = sorted(self._by_date)
        return self._dates_cache

    def close_on(self, trading_date: str) -> float | None:
        row = self._by_date.get(trading_date)
        return row["close"] if row else None

    def row_on(self, trading_date: str) -> dict | None:
        return self._by_date.get(trading_date)

    def first_capture_date_for(self, trading_date: str) -> str | None:
        row = self._by_date.get(trading_date)
        return row["first_capture_date"] if row else None

    def live_known_asof(self, trading_date: str, decision_date: str) -> bool:
        """True only if some committed snapshot both (a) reports this
        trading_date's close and (b) was itself captured on/before
        decision_date. False whenever the only evidence is a later,
        retrospective archive -- e.g. every trading_date before 2026-08-13,
        since no snapshot exists with that early a capture_date at all."""
        cap = self.first_capture_date_for(trading_date)
        return cap is not None and cap <= decision_date

    def trading_dates_strictly_after(self, decision_date: str, limit: int | None = None) -> list[str]:
        out = [d for d in self.dates() if d > decision_date]
        out.sort()
        return out[:limit] if limit is not None else out

    def trading_dates_at_or_before(self, decision_date: str) -> list[str]:
        return sorted(d for d in self.dates() if d <= decision_date)

    def live_trading_dates_at_or_before(self, decision_date: str) -> list[str]:
        """Trading dates <= decision_date whose price row was ALSO captured
        by a snapshot dated <= decision_date -- i.e. dates Atlas's own
        system could plausibly have known about at decision_date. This is
        the correct window for SIGNAL-side detection (trigger_engine.py);
        `trading_dates_at_or_before` alone is only safe for OUTCOME-side
        grading (forward_metrics.py's entry price).

        Memoized per decision_date (see `__init__`/`_merge_row`): every
        caller in this repo passes a `series` that is already fully built,
        so the same (self._by_date, decision_date) pair always yields the
        same answer -- and `_merge_row` clears this cache on the one path
        that could change `self._by_date` after the fact."""
        cached = self._live_dates_cache.get(decision_date)
        if cached is not None:
            return cached
        result = sorted(
            d for d in self.dates()
            if d <= decision_date and self.live_known_asof(d, decision_date)
        )
        self._live_dates_cache[decision_date] = result
        return result

    def window_at_or_before_cached(self, decision_date: str, n: int, validate) -> list[str]:
        """Cached form of `replay/trigger_engine.py`'s `window_at_or_before`:
        fetch `live_trading_dates_at_or_before(decision_date)` (already
        memoized above), run the caller-supplied `validate(decision_date,
        dates)` lookahead check, and slice the trailing `n`. Keyed by
        (decision_date, n) and invalidated exactly where the caches above
        are (see `_merge_row`/`_invalidate_caches`).

        This exists because `window_at_or_before` is the ACTUAL hot call
        site: `relative_strength_reversal` calls it once for its own
        subject and once per peer, and it is invoked once per (subject,
        trading date) pair -- so for a peer universe of size P walked over
        D replayed dates, the same (peer, date, lookback) triple is
        recomputed up to P times. `validate` is a pure function of
        (decision_date, dates) with no side effect besides raising on a
        real violation, so calling it once per distinct key instead of once
        per caller is behaviorally identical -- a violation that would have
        fired still fires, on the first call that reaches it."""
        key = (decision_date, n)
        cached = self._window_cache.get(key)
        if cached is not None:
            return cached
        dates = self.live_trading_dates_at_or_before(decision_date)
        validate(decision_date, dates)
        result = dates[-n:] if len(dates) >= n else dates
        self._window_cache[key] = result
        return result


def build_krx_series(code: str, snapshots: list[ei.KrxSnapshot]) -> PriceSeries:
    series = PriceSeries(code)
    for snap in snapshots:
        stock = snap.stocks.get(code)
        if not stock:
            continue
        for trading_date, day in stock.get("daily", {}).items():
            row = {"close": float(day["close"]), "open": float(day["open"]),
                   "high": float(day["high"]), "low": float(day["low"]),
                   "volume": day.get("volume"), "net_value": day.get("net_value")}
            series._merge_row(trading_date, row, snap.capture_date)
    return series


def build_btc_series(snapshots: list[ei.BtcSnapshot]) -> PriceSeries:
    series = PriceSeries("BTC")
    for snap in snapshots:
        for row in snap.rows():
            series._merge_row(row["date"], {
                "close": row["close"], "open": row["open"],
                "high": row["high"], "low": row["low"], "volume": row["volume"],
            }, snap.capture_date)
    return series


def krx_name(code: str, snapshots: list[ei.KrxSnapshot]) -> str | None:
    for snap in reversed(snapshots):
        stock = snap.stocks.get(code)
        if stock and stock.get("name"):
            return stock["name"]
    return None


def krx_stage_history(code: str, snapshots: list[ei.KrxSnapshot]) -> list[tuple[str, str | None]]:
    """(capture_date, atlas_stage) pairs -- real, per-snapshot committed
    stage values only, in capture_date order."""
    out = []
    for snap in snapshots:
        stock = snap.stocks.get(code)
        if stock is not None:
            out.append((snap.capture_date, stock.get("atlas_stage")))
    return out
