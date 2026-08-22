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

    def _merge_row(self, trading_date: str, row: dict, capture_date: str):
        existing = self._by_date.get(trading_date)
        if existing is None:
            self._by_date[trading_date] = {**row, "first_capture_date": capture_date}
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
            return
        if capture_date < existing["first_capture_date"]:
            existing["first_capture_date"] = capture_date

    def dates(self) -> list[str]:
        return sorted(self._by_date)

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
        grading (forward_metrics.py's entry price)."""
        return sorted(
            d for d in self.dates()
            if d <= decision_date and self.live_known_asof(d, decision_date)
        )


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
