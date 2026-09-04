#!/usr/bin/env python3
"""P1-COM-05 US free-axis historical replay population — SHADOW only, NOT NATURAL.

CIO mandate (2026-09-04) continuation of the KR slice: for a caller-supplied
list of historical US dates, reconstruct the free/existing-source US axis
observations — and only those — as they could have been computed on that date,
plus the candidate normalization the current, unmodified rule produces for
them.

Scope is deliberately three of five axes:

* ``TREND``     — Alpaca IEX daily bars for the contract's ``trend_symbols``.
* ``RISK_VOL``  — FRED ``VIXCLS``.
* ``LIQUIDITY`` — FRED ``WRESBAL`` + ``TOTBKCR``.

``BREADTH`` and ``LEADERSHIP`` are **not** populated here.  Both are derived
today from ``alpaca.current_proxy_axes`` in
``config/free_market_data_contract.json``, whose ``approval_status`` is
``RATIFIED_CURRENT_REFERENCE_ONLY`` and whose companion authority flag
``authority.us_breadth_authorized`` is ``false``.  Replaying a
current-reference-only proxy across history would be a new ratification, which
this module has no authority to make, so both axes stay ``UNKNOWN`` with an
attributable exclusion basis and are never silently interpolated, defaulted, or
relabelled.  Because coverage is therefore 3/5, the candidate normalization
result is honestly ``NOT_COMPUTABLE`` and the candidate regime stays
``UNKNOWN`` — this module never manufactures a US regime out of a partial axis
set.

This module invents nothing new:

* Bar retrieval, OHLC validation, decimal parsing, session-return math, and
  FRED liquidity unit normalization reuse ``collectors/free_market_data.py``
  unmodified (``fetch_alpaca_daily_bars``, ``_session_return``, ``_decimal``,
  ``_decimal_text``, ``FRED_LIQUIDITY_UNITS``, ``load_contract``).
* The three axis directions/thresholds/summaries mirror
  ``regime/paper_regime_reference.py::build_us`` exactly and are pinned to it
  by ``test/test_us_historical_replay_population.py``, which drives the live
  ``build_us`` over the same inputs and asserts row-for-row equality.  No
  threshold is added, tuned, or re-ratified here.

Historical replay evidence != NATURAL evidence:

* Input dates are exactly and only what the caller supplies via ``--date``.
  This module never selects bull/bear/sideways/stress episodes on its own —
  regime-episode selection is a separate CIO policy gate.
* Every record is tagged ``evidence_class =
  "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"`` and every authority flag stays
  ``false`` except the one read-only "this is shadow historical-replay
  evidence" marker.
* This module refuses to write its output anywhere inside this repository
  checkout — not the NATURAL ``evidence/free_market_data/`` or ``data/`` paths,
  not any other tracked path.  The only accepted destinations are an external
  ``--out`` path outside the checkout, or (when ``--out`` is omitted) a private
  system-temp file whose path is printed and never committed.

Point-in-time integrity is structural, not merely asserted:

* Alpaca bars are requested with ``end`` pinned to the requested date, and any
  returned bar dated after it fails that date closed instead of being used.
* Both FRED calls pin ``observation_end`` **and** the ALFRED vintage
  ``realtime_start``/``realtime_end`` to the requested date, so a later
  revision of a revisable series (``WRESBAL``/``TOTBKCR`` are revised) can
  never leak backwards into an earlier replayed date.
* Each requested date is resolved independently from its own anchor, so no
  other requested date's outcome can influence this one.

Provenance is part of the observation, not decoration — and it is described as
what it is.  Every observed axis carries the sha256 of the Alpaca/FRED response
it was measured from, and ``validate_population`` requires that hash to be
present exactly when the axis is ``OBSERVED``, absent exactly when it is not,
and *consistent with* the provenance inside that axis's own measurement.  An
axis whose record-level hash was deleted, blanked, or swapped for another axis's
is therefore rejected even when the measurement, the re-derived axis row, and
every payload hash are otherwise intact.

This is a consistency check between two copies of the same hash, not an
external anchor, and the module does not claim more.  Both copies live in the
same mutable payload, so replacing *both* with the same arbitrary valid SHA-256
and recomputing the population digest is self-consistent and **is accepted**;
the raw provider responses are not retained and neither Alpaca nor FRED signs
them, so nothing in this evidence can distinguish that case.  The check is
named and coded accordingly
(``RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS``), and
``test_us_historical_replay_population.py`` pins both the caught and the
uncaught side so the guarantee cannot drift into an over-claim.

Known, disclosed limitation: closes are unadjusted (``adjustment=raw``)
because that is the convention the production collector already uses; a
corporate action *inside* a replayed return window is therefore reflected the
same way production reflects it, and this module does not introduce a new
adjustment policy to "fix" it.  The fact is carried in every record's
``warnings``.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import paper_regime_reference as PRR  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# `collectors/` is not an importable Python package — this dynamic load is the
# same technique regime/live_axis_adapter.py already uses to reuse the free
# market data collector unmodified.
FMD = _load_module(
    "atlas_us_historical_replay_free_market_data",
    ROOT / "collectors" / "free_market_data.py",
)


SCHEMA_VERSION = "regime_us_historical_replay_population/v1"
MODE = "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL"
EVIDENCE_CLASS = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"
CANDIDATE_POLICY_PATH = "config/paper_regime_reference_policy_v1.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE10 = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC = dt.timezone.utc

REPLAYED_AXES = ["TREND", "RISK_VOL", "LIQUIDITY"]
EXCLUDED_AXES = ["BREADTH", "LEADERSHIP"]

# The exact provenance a record must carry, one entry per replayed axis, and the
# exact per-series shape the FRED liquidity capture emits. Required key for key
# by ``validate_population``: a re-hashed payload that *deletes* a response hash
# must fail rather than pass by having nothing left to check. Presence and
# agreement is all this can establish — see
# ``_validate_source_hash_consistency`` for the boundary.
SOURCE_HASH_KEYS = (
    "liquidity_response_hashes",
    "risk_vol_response_sha256",
    "trend_response_sha256",
)
LIQUIDITY_RESPONSE_HASH_KEYS = (
    "metadata_response_sha256", "observations_response_sha256",
)
AXIS_RESPONSE_HASH_KEY = {
    "TREND": "trend_response_sha256",
    "RISK_VOL": "risk_vol_response_sha256",
    "LIQUIDITY": "liquidity_response_hashes",
}

STATUS_OBSERVED = "FREE_AXES_OBSERVED"
STATUS_PARTIAL = "FREE_AXES_PARTIAL"
STATUS_BLOCKED = "BLOCKED"
RECORD_STATUSES = (STATUS_OBSERVED, STATUS_PARTIAL, STATUS_BLOCKED)

# The candidate rule can only classify a full 5/5 axis set; with BREADTH and
# LEADERSHIP excluded by ratification scope, the honest normalization outcome
# is "not computable", never a NEUTRAL stand-in.
CLASSIFICATION_STATUS = "NOT_COMPUTABLE_PARTIAL_AXIS_COVERAGE"

# The exact authority boundary of this population, declared once and required
# key-for-key by ``validate_population``. A payload that drops a flag must not
# pass merely because the flag it dropped is no longer there to be checked.
AUTHORITY_GRANTED_KEY = "historical_replay_evidence_authorized"
AUTHORITY = {
    "historical_replay_evidence_authorized": True,
    "natural_promotion_authorized": False,
    "us_breadth_authorized": False,
    "us_leadership_authorized": False,
    "sensor_normalization_ratification_authorized": False,
    "registry_promotion_authorized": False,
    "ttl_ratification_authorized": False,
    "pit_replay_acceptance_authorized": False,
    "runtime_regime_wiring_authorized": False,
    "strategy_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authorized": False,
}

RAW_RETENTION = "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED"
RECORD_WARNINGS = [
    "FREE_IEX_REPRESENTATIVE_ETF_REFERENCE",
    "NOT_FULL_US_SECURITY_LEVEL_BREADTH",
    "RAW_UNADJUSTED_CLOSES_MIRROR_PRODUCTION_COLLECTOR_CONVENTION",
    "SHADOW_HISTORICAL_BACKFILL_NOT_NATURAL_OBSERVATION",
    "REGIME_INTERPRETATION_UNAUTHORIZED",
]


class ReplayPopulationError(ValueError):
    """A requested historical US replay population cannot be safely built."""


def fail(code: str, detail: str = "") -> None:
    raise ReplayPopulationError(f"{code}:{detail}" if detail else code)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReplayPopulationError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ReplayPopulationError(f"SOURCE_MISSING:{path}") from exc


def redact(text: str, secrets: list[str]) -> str:
    """Never let a credential reach a recorded failure reason.

    ``collectors/free_market_data.py`` already refuses to let urllib's
    URL-bearing exception text escape, so this is defence in depth rather than
    the only guard: any secret that still appeared in a code/detail string
    would otherwise be written to the caller's output file.
    """
    cleaned = str(text)
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def _load_candidate_policy() -> dict:
    policy = PRR.read_json(PRR.POLICY_PATH, "POLICY_INVALID")
    if policy.get("contract_version") != "paper_regime_reference_policy/v1":
        fail("POLICY_INVALID", "contract_version")
    return policy


def _parse_requested_date(value: str) -> dt.date:
    if not isinstance(value, str) or DATE10.fullmatch(value) is None:
        fail("REQUESTED_DATE_FORMAT_INVALID")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ReplayPopulationError("REQUESTED_DATE_CALENDAR_INVALID") from exc


# ---------------------------------------------------------------------------
# Candidate normalization rows.
#
# Each function below is a line-for-line mirror of the corresponding block in
# regime/paper_regime_reference.py::build_us — same comparison values, same
# observed_value shape, same Korean summary text — restricted to the three
# free-source axes this slice replays.  ``build_us`` itself cannot be called
# because it requires a full 5/5 packet including the current-reference-only
# BREADTH/LEADERSHIP proxies that this module must not backfill.  Divergence is
# not left to review discipline: test/test_us_historical_replay_population.py
# drives the live ``build_us`` over the same inputs across each threshold
# boundary and asserts these rows are byte-equal to its rows.
# ---------------------------------------------------------------------------


def trend_axis_row(trend_etfs: list[dict]) -> dict:
    if not isinstance(trend_etfs, list) or len(trend_etfs) != 3:
        fail("US_TREND_COVERAGE_INCOMPLETE")
    trend_returns = [
        PRR.decimal(row.get("returns", {}).get("20_session_pct"), "US_TREND_INVALID")
        for row in trend_etfs
    ]
    positive = sum(value > 0 for value in trend_returns)
    fraction = Decimal(positive) / Decimal(len(trend_returns))
    direction = PRR.ratio_direction(fraction, Decimal("0.666667"), Decimal("0.333333"))
    return PRR.axis(
        "TREND",
        direction,
        {"positive": positive, "total": 3},
        f"대표지수 3개 중 {positive}개가 20거래일 기준 상승입니다.",
    )


def risk_vol_axis_row(vix_value: object) -> dict:
    vix = PRR.decimal(vix_value, "US_VIX_INVALID")
    if vix < Decimal("15"):
        direction = "POSITIVE"
    elif vix < Decimal("25"):
        direction = "NEUTRAL"
    elif vix < Decimal("30"):
        direction = "NEGATIVE"
    else:
        direction = "STRESS"
    band = "낮은" if direction == "POSITIVE" else "보통" if direction == "NEUTRAL" else "높은"
    return PRR.axis(
        "RISK_VOL",
        direction,
        {"vix": str(vix)},
        f"VIX는 {vix}로 {band} 구간입니다.",
    )


def liquidity_axis_row(liquidity_rows: list[dict]) -> dict:
    if (
        not isinstance(liquidity_rows, list)
        or {row.get("series_id") for row in liquidity_rows} != {"WRESBAL", "TOTBKCR"}
    ):
        fail("US_LIQUIDITY_INVALID")
    changes = [PRR.decimal(row.get("change"), "US_LIQUIDITY_INVALID") for row in liquidity_rows]
    direction = PRR.sign_pair(changes)
    summary = (
        "연준 준비금과 은행 신용 변화 방향이 서로 엇갈립니다."
        if direction == "NEUTRAL"
        else "유동성 지표 두 개가 같은 방향입니다."
    )
    return PRR.axis(
        "LIQUIDITY",
        direction,
        {row["series_id"]: row["change"] for row in liquidity_rows},
        summary,
    )


# ---------------------------------------------------------------------------
# Point-in-time source retrieval.
#
# Every request below is anchored to one requested date and can only look
# backward from it.  Nothing is persisted: only response hashes travel into the
# population, matching the collector's own
# TRANSIENT_NOT_PERSISTED_HASH_ATTESTED liquidity convention.
# ---------------------------------------------------------------------------


def _fred_query(series_id: str, api_key: str, anchor: dt.date, lookback_days: int) -> str:
    """Observation query pinned to the requested date in *both* time axes.

    ``observation_end`` bounds *what happened*; ``realtime_start``/
    ``realtime_end`` bound *what was known* — the ALFRED vintage.  WRESBAL and
    TOTBKCR are revised series, so without the vintage pin a replay would
    silently consume a revision published after the replayed date.
    """
    return urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": (anchor - dt.timedelta(days=lookback_days)).isoformat(),
        "observation_end": anchor.isoformat(),
        "realtime_start": anchor.isoformat(),
        "realtime_end": anchor.isoformat(),
    })


def _fred_metadata_query(series_id: str, api_key: str, anchor: dt.date) -> str:
    return urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "realtime_start": anchor.isoformat(),
        "realtime_end": anchor.isoformat(),
    })


def _valid_observations(body: object, code: str) -> list[dict]:
    rows = body.get("observations") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        fail(code)
    valid = [
        row for row in rows
        if isinstance(row, dict) and row.get("value") not in (None, ".")
    ]
    if not valid:
        fail(code)
    return valid


def _assert_not_after(anchor: dt.date, observation_date: object, code: str) -> str:
    if not isinstance(observation_date, str) or DATE10.fullmatch(observation_date) is None:
        fail(code)
    if observation_date > anchor.isoformat():
        fail("US_REPLAY_LOOKAHEAD_VIOLATION", code)
    return observation_date


def replay_trend_source(
    alpaca_key: str, alpaca_secret: str, anchor: dt.date, *, getter, contract: dict,
) -> dict:
    """Rebuild the trend-ETF observations available as of ``anchor``."""
    if not alpaca_key and not alpaca_secret:
        fail("BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL")
    if not alpaca_key or not alpaca_secret:
        fail("BLOCKED_BY_INCOMPLETE_DEDICATED_MARKET_DATA_CREDENTIAL")
    symbols = list(contract["alpaca"]["trend_symbols"])
    windows = list(contract["alpaca"]["return_windows_sessions"])
    # `end` is the requested date's last instant, so the provider is never
    # asked for a session after it.
    anchor_end = dt.datetime.combine(anchor, dt.time(23, 59, 59), tzinfo=UTC)
    raw, normalized = FMD.fetch_alpaca_daily_bars(
        alpaca_key, alpaca_secret, symbols, anchor_end, getter=getter,
    )
    grouped: dict[str, list[dict]] = {}
    for row in normalized:
        session_date = str(row.get("opened_at", ""))[:10]
        if DATE10.fullmatch(session_date) is None:
            fail("US_TREND_SESSION_DATE_INVALID")
        # A provider that answers with a later bar than requested must fail
        # this date closed rather than have the bar silently trimmed.
        if session_date > anchor.isoformat():
            fail("US_REPLAY_LOOKAHEAD_VIOLATION", "ALPACA_BAR")
        grouped.setdefault(row["symbol"], []).append({**row, "session_date": session_date})

    trend_etfs = []
    for symbol in symbols:
        bars = sorted(grouped.get(symbol, []), key=lambda row: row["session_date"])
        if len(bars) < 2:
            fail("US_TREND_HISTORY_INSUFFICIENT", symbol)
        try:
            closes = [FMD._decimal(bar["close"], "US_TREND_CLOSE_INVALID") for bar in bars]
            returns = {
                f"{window}_session_pct": FMD._session_return(closes, window)
                for window in windows
            }
        except FMD.FreeMarketDataError as exc:
            raise ReplayPopulationError(
                f"US_TREND_HISTORY_INSUFFICIENT:{symbol}"
            ) from exc
        trend_etfs.append({
            "symbol": symbol,
            "as_of_session_date": bars[-1]["session_date"],
            "previous_session_date": bars[-2]["session_date"],
            "earliest_session_date": bars[0]["session_date"],
            "close": FMD._decimal_text(closes[-1]),
            "available_session_count": len(bars),
            "returns": returns,
        })

    session_dates = {row["as_of_session_date"] for row in trend_etfs}
    if len(session_dates) != 1:
        fail("US_TREND_SESSION_DATE_MISMATCH")
    effective = session_dates.pop()
    return {
        "source_scope": contract["alpaca"]["source_scope"],
        "feed": contract["alpaca"]["feed"],
        "timeframe": "1Day",
        "adjustment": "raw",
        "requested_end_date": anchor.isoformat(),
        "as_of_session_date": effective,
        "earliest_session_date": min(row["earliest_session_date"] for row in trend_etfs),
        "axis_window_sessions": 20,
        "return_windows_sessions": windows,
        "trend_etfs": trend_etfs,
        "raw_retention": RAW_RETENTION,
        "response_sha256": FMD.sha256_bytes(raw),
    }


def replay_risk_vol_source(
    fred_key: str, anchor: dt.date, *, getter, contract: dict,
) -> dict:
    """Rebuild the VIXCLS observation known as of ``anchor``."""
    if not fred_key:
        fail("BLOCKED_BY_FRED_CREDENTIAL")
    series_id = contract["fred"]["risk_series"][0]
    raw = getter(
        "https://api.stlouisfed.org/fred/series/observations?"
        + _fred_query(series_id, fred_key, anchor, 60)
    )
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReplayPopulationError("US_VIX_JSON_INVALID") from exc
    latest = _valid_observations(body, "US_VIX_OBSERVATIONS_MISSING")[-1]
    observation_date = _assert_not_after(
        anchor, latest.get("date"), "US_VIX_OBSERVATION_DATE_INVALID"
    )
    return {
        "series_id": series_id,
        "source_scope": contract["fred"]["source_scope"],
        "observation_date": observation_date,
        "value": latest.get("value"),
        "realtime_start": latest.get("realtime_start"),
        "realtime_end": latest.get("realtime_end"),
        "vintage_date": anchor.isoformat(),
        "raw_retention": RAW_RETENTION,
        "response_sha256": FMD.sha256_bytes(raw),
    }


def replay_liquidity_source(
    fred_key: str, anchor: dt.date, *, getter, contract: dict,
) -> dict:
    """Rebuild the WRESBAL/TOTBKCR change known as of ``anchor``.

    Unit handling reuses ``collectors/free_market_data.FRED_LIQUIDITY_UNITS``
    and ``_decimal``/``_decimal_text`` unmodified, so a replayed change is on
    the same normalized scale as the production capture.
    """
    if not fred_key:
        fail("BLOCKED_BY_FRED_CREDENTIAL")
    series_rows = []
    response_hashes = {}
    for series_id in contract["fred"]["liquidity_series"]:
        metadata_raw = getter(
            "https://api.stlouisfed.org/fred/series?"
            + _fred_metadata_query(series_id, fred_key, anchor)
        )
        observations_raw = getter(
            "https://api.stlouisfed.org/fred/series/observations?"
            + _fred_query(series_id, fred_key, anchor, 180)
        )
        try:
            metadata_body = json.loads(metadata_raw)
            observations_body = json.loads(observations_raw)
        except json.JSONDecodeError as exc:
            raise ReplayPopulationError(
                f"US_LIQUIDITY_JSON_INVALID:{series_id}"
            ) from exc
        metadata_rows = metadata_body.get("seriess") if isinstance(metadata_body, dict) else None
        if not isinstance(metadata_rows, list) or len(metadata_rows) != 1:
            fail("US_LIQUIDITY_METADATA_INVALID", series_id)
        units = metadata_rows[0].get("units")
        unit_base = units.split(",", 1)[0].strip() if isinstance(units, str) else None
        if unit_base not in FMD.FRED_LIQUIDITY_UNITS:
            fail("US_LIQUIDITY_UNITS_INVALID", series_id)
        normalized_unit, factor = FMD.FRED_LIQUIDITY_UNITS[unit_base]
        valid = _valid_observations(
            observations_body, f"US_LIQUIDITY_OBSERVATIONS_MISSING:{series_id}"
        )
        if len(valid) < 2:
            fail("US_LIQUIDITY_HISTORY_INSUFFICIENT", series_id)
        previous, latest = valid[-2], valid[-1]
        observation_date = _assert_not_after(
            anchor, latest.get("date"), f"US_LIQUIDITY_OBSERVATION_DATE_INVALID:{series_id}"
        )
        previous_date = _assert_not_after(
            anchor, previous.get("date"), f"US_LIQUIDITY_OBSERVATION_DATE_INVALID:{series_id}"
        )
        try:
            previous_value = FMD._decimal(previous["value"], "US_LIQUIDITY_VALUE_INVALID") * factor
            latest_value = FMD._decimal(latest["value"], "US_LIQUIDITY_VALUE_INVALID") * factor
        except FMD.FreeMarketDataError as exc:
            raise ReplayPopulationError(
                f"US_LIQUIDITY_VALUE_INVALID:{series_id}"
            ) from exc
        metadata_sha = FMD.sha256_bytes(metadata_raw)
        observations_sha = FMD.sha256_bytes(observations_raw)
        response_hashes[series_id] = {
            "metadata_response_sha256": metadata_sha,
            "observations_response_sha256": observations_sha,
        }
        series_rows.append({
            "series_id": series_id,
            "title": metadata_rows[0].get("title"),
            "frequency": metadata_rows[0].get("frequency"),
            "source_unit": units,
            "normalized_unit": normalized_unit,
            "normalization_factor": FMD._decimal_text(factor),
            "observation_date": observation_date,
            "value": FMD._decimal_text(latest_value),
            "previous_observation_date": previous_date,
            "previous_value": FMD._decimal_text(previous_value),
            "change": FMD._decimal_text(latest_value - previous_value),
            "realtime_start": latest.get("realtime_start"),
            "realtime_end": latest.get("realtime_end"),
        })
    return {
        "source_scope": contract["fred"]["source_scope"],
        "derivation_version": "fred_liquidity_current/v1",
        "vintage_date": anchor.isoformat(),
        "series": series_rows,
        "response_hashes": response_hashes,
        "raw_retention": RAW_RETENTION,
    }


# ---------------------------------------------------------------------------
# Per-date replay.
# ---------------------------------------------------------------------------


def exclusion_basis(contract: dict) -> dict:
    """Why BREADTH/LEADERSHIP stay UNKNOWN — read from the contract, not asserted.

    If the ratification scope of the ETF proxies ever changes, this module must
    be re-decided by a human rather than keep quietly excluding (or quietly
    start including) the two axes, so a changed basis fails closed.
    """
    proxy = contract["alpaca"]["current_proxy_axes"]
    approval_status = proxy.get("approval_status")
    breadth_authorized = contract["authority"].get("us_breadth_authorized")
    if approval_status != "RATIFIED_CURRENT_REFERENCE_ONLY":
        fail("EXCLUSION_BASIS_CHANGED", "alpaca.current_proxy_axes.approval_status")
    if breadth_authorized is not False:
        fail("EXCLUSION_BASIS_CHANGED", "authority.us_breadth_authorized")
    statement = (
        "US BREADTH and LEADERSHIP are derived today only from"
        " alpaca.current_proxy_axes, which is ratified for current reference"
        " only. Replaying that proxy across history would be a new"
        " ratification this module has no authority to make, so both axes stay"
        " UNKNOWN and are never estimated, defaulted, or interpolated."
    )
    return {
        name: {
            "status": "UNKNOWN",
            "reason_code": "EXCLUDED_PROXY_RATIFIED_CURRENT_REFERENCE_ONLY",
            "basis": {
                "config/free_market_data_contract.json"
                "#alpaca.current_proxy_axes.approval_status": approval_status,
                "config/free_market_data_contract.json"
                "#authority.us_breadth_authorized": breadth_authorized,
            },
            "statement": statement,
        }
        for name in EXCLUDED_AXES
    }


def _axis_attempt(fetch, derive, secrets: list[str]) -> dict:
    """Run one axis end to end; contain its failure to that one axis.

    A source that is missing, credential-blocked, unrevised at the requested
    vintage, or shaped in a way the reused collector code cannot consume yields
    an attributable NOT_COMPUTABLE reason *code* — never a leaked raw message,
    never a fabricated value, and never a failure for the other two axes.
    """
    try:
        measurement = fetch()
        row = derive(measurement)
    except (
        ReplayPopulationError, PRR.PaperRegimeReferenceError, FMD.FreeMarketDataError,
    ) as exc:
        return {"measurement": None, "row": None, "reason": redact(str(exc), secrets)}
    except Exception as exc:  # noqa: BLE001 — deliberate per-axis containment,
        # mirrors regime/kr_historical_replay_population.py: an unrecognized
        # response shape degrades to "this axis is not replayable on this date"
        # instead of aborting the population. Only the exception *type* is
        # recorded, never its message.
        return {
            "measurement": None,
            "row": None,
            "reason": f"UNSUPPORTED_REPLAY_SHAPE_{type(exc).__name__}",
        }
    return {"measurement": measurement, "row": row, "reason": None}


def _blocked_date_record(
    requested_date: str, failure_reason: str, *, attempted: int = 0,
) -> dict:
    return {
        "requested_date": requested_date,
        "status": STATUS_BLOCKED,
        "evidence_class": EVIDENCE_CLASS,
        "effective_session_date": None,
        "free_axis_coverage": {
            "attempted_count": attempted,
            "observed_count": 0,
            "ratio": f"0/{len(REPLAYED_AXES)}",
            "observed_axes": [],
            "not_computable_axes": list(REPLAYED_AXES),
        },
        "five_axis": None,
        "candidate_normalized_result": None,
        "source_hashes": None,
        "failure_reason": failure_reason,
        "warnings": list(RECORD_WARNINGS),
        "no_lookahead_attestation": {
            "anchor_requested_date": requested_date,
            "fred_realtime_vintage_date": None,
            "trend_session_date_range": [],
            "vix_observation_date": None,
            "liquidity_observation_dates": [],
            "any_source_date_after_requested_date": False,
            "other_requested_dates_consulted": False,
        },
    }


def _candidate_normalized_result(
    rows: list[dict], as_of_date: str | None, policy: dict, excluded: dict,
) -> dict:
    """Apply the existing candidate rule's own classifier to a partial axis set.

    ``PRR.classify`` is called unmodified. With fewer than five axes it returns
    UNKNOWN by its own contract, which is exactly the honest outcome here; the
    call is kept (rather than hardcoding UNKNOWN) so the result stays tied to
    the live rule, and a future change that would let a partial set classify
    fails closed instead of silently publishing a US regime.
    """
    regime, _score, explanation = PRR.classify(rows, policy)
    if len(rows) >= len(PRR.AXES) or regime != "UNKNOWN":
        fail("PARTIAL_COVERAGE_MUST_NOT_CLASSIFY")
    defined = [row["axis"] for row in rows]
    missing = [name for name in PRR.AXES if name not in defined]
    return {
        "market": "US",
        "as_of_date": as_of_date,
        "coverage": {
            "defined_count": len(defined),
            "required_count": len(PRR.AXES),
            "ratio": f"{len(defined)}/{len(PRR.AXES)}",
            "defined_axes": defined,
            "missing_axes": missing,
            "excluded_axes": sorted(excluded),
        },
        "paper_reference": {
            "candidate_regime": regime,
            # Score and confidence are withheld rather than reported as 0/None
            # from a partial set: a numeric score over 3 of 5 axes would read
            # as comparable to a full-coverage score and is not.
            "score": None,
            "confidence": None,
            "explanation_ko": explanation,
        },
        "classification_status": CLASSIFICATION_STATUS,
        "runtime_regime": "UNKNOWN",
        "axes": rows,
        "candidate_rule_source": (
            "regime/paper_regime_reference.py::build_us"
            " (TREND/RISK_VOL/LIQUIDITY axes only)"
        ),
    }


def replay_one_requested_date(
    credentials: dict, requested_date: str, *, getter, contract: dict, policy: dict,
    excluded: dict,
) -> dict:
    """Resolve and replay exactly one caller-supplied historical date.

    Takes only this one requested date plus the on-disk contract/candidate
    policy. Every source request is anchored to this date and bounded backward,
    so the call is structurally incapable of consuming a session, observation,
    revision, or outcome belonging to any other requested date.
    """
    secrets = [value for value in credentials.values() if value]
    try:
        anchor = _parse_requested_date(requested_date)
    except ReplayPopulationError as exc:
        return _blocked_date_record(requested_date, redact(str(exc), secrets))

    attempts = {
        "TREND": _axis_attempt(
            lambda: replay_trend_source(
                credentials.get("alpaca_key", ""), credentials.get("alpaca_secret", ""),
                anchor, getter=getter, contract=contract,
            ),
            lambda measurement: trend_axis_row(measurement["trend_etfs"]),
            secrets,
        ),
        "RISK_VOL": _axis_attempt(
            lambda: replay_risk_vol_source(
                credentials.get("fred_key", ""), anchor, getter=getter, contract=contract,
            ),
            lambda measurement: risk_vol_axis_row(measurement["value"]),
            secrets,
        ),
        "LIQUIDITY": _axis_attempt(
            lambda: replay_liquidity_source(
                credentials.get("fred_key", ""), anchor, getter=getter, contract=contract,
            ),
            lambda measurement: liquidity_axis_row(measurement["series"]),
            secrets,
        ),
    }

    observed_axes = [name for name in REPLAYED_AXES if attempts[name]["row"] is not None]
    not_computable = [name for name in REPLAYED_AXES if attempts[name]["row"] is None]

    trend = attempts["TREND"]["measurement"]
    risk = attempts["RISK_VOL"]["measurement"]
    liquidity = attempts["LIQUIDITY"]["measurement"]
    effective_session_date = trend["as_of_session_date"] if trend else None

    source_dates = []
    trend_range: list[str] = []
    if trend:
        trend_range = [trend["earliest_session_date"], trend["as_of_session_date"]]
        source_dates.extend(trend_range)
    vix_observation_date = risk["observation_date"] if risk else None
    if vix_observation_date:
        source_dates.append(vix_observation_date)
    liquidity_dates = sorted({
        date
        for row in (liquidity["series"] if liquidity else [])
        for date in (row["previous_observation_date"], row["observation_date"])
    })
    source_dates.extend(liquidity_dates)
    # Computed, not asserted: if any consumed source date is later than the
    # requested date the whole date fails closed rather than publishing a
    # "no lookahead" claim it cannot support.
    if any(date > requested_date for date in source_dates):
        return _blocked_date_record(
            requested_date, "US_REPLAY_LOOKAHEAD_VIOLATION",
            attempted=len(REPLAYED_AXES),
        )

    axes = {
        name: (
            {
                "status": "OBSERVED",
                "reason": None,
                "measurement": copy.deepcopy(attempts[name]["measurement"]),
            }
            if attempts[name]["row"] is not None
            else {
                "status": "NOT_COMPUTABLE",
                "reason": attempts[name]["reason"],
                "measurement": None,
            }
        )
        for name in REPLAYED_AXES
    }
    for name in EXCLUDED_AXES:
        axes[name] = {
            "status": "UNKNOWN",
            "reason": excluded[name]["reason_code"],
            "measurement": None,
        }

    rows = [attempts[name]["row"] for name in REPLAYED_AXES if attempts[name]["row"] is not None]
    candidate = (
        _candidate_normalized_result(rows, effective_session_date, policy, excluded)
        if rows
        else None
    )

    if len(observed_axes) == len(REPLAYED_AXES):
        status, failure_reason = STATUS_OBSERVED, None
    elif observed_axes:
        status, failure_reason = STATUS_PARTIAL, None
    else:
        # Per-axis reasons carry the attribution; the record-level reason only
        # states that nothing publishable survived for this date.
        status, failure_reason = STATUS_BLOCKED, "ALL_FREE_AXES_NOT_COMPUTABLE"

    return {
        "requested_date": requested_date,
        "status": status,
        "evidence_class": EVIDENCE_CLASS,
        "effective_session_date": effective_session_date,
        "free_axis_coverage": {
            "attempted_count": len(REPLAYED_AXES),
            "observed_count": len(observed_axes),
            "ratio": f"{len(observed_axes)}/{len(REPLAYED_AXES)}",
            "observed_axes": observed_axes,
            "not_computable_axes": not_computable,
        },
        "five_axis": {
            # The status names what this packet actually holds: never "5/5
            # observed", and never "observed" at all when nothing survived.
            "status": (
                "OBSERVED_UNCLASSIFIED_FREE_AXES_ONLY"
                if observed_axes
                else "NOT_COMPUTABLE_NO_FREE_AXIS_OBSERVED"
            ),
            "coverage": {
                "defined_count": len(observed_axes),
                "required_count": len(PRR.AXES),
                "ratio": f"{len(observed_axes)}/{len(PRR.AXES)}",
                "defined_axes": observed_axes,
                "missing_axes": sorted(not_computable + EXCLUDED_AXES),
            },
            "axes": axes,
        },
        "candidate_normalized_result": candidate,
        "source_hashes": {
            "trend_response_sha256": trend["response_sha256"] if trend else None,
            "risk_vol_response_sha256": risk["response_sha256"] if risk else None,
            "liquidity_response_hashes": (
                copy.deepcopy(liquidity["response_hashes"]) if liquidity else None
            ),
        },
        "failure_reason": failure_reason,
        "warnings": list(RECORD_WARNINGS),
        "no_lookahead_attestation": {
            "anchor_requested_date": requested_date,
            "fred_realtime_vintage_date": requested_date,
            "trend_session_date_range": trend_range,
            "vix_observation_date": vix_observation_date,
            "liquidity_observation_dates": liquidity_dates,
            "any_source_date_after_requested_date": False,
            "other_requested_dates_consulted": False,
        },
    }


# ---------------------------------------------------------------------------
# Population.
# ---------------------------------------------------------------------------


def build_population(
    credentials: dict, requested_dates: list[str], *, getter=None,
) -> dict:
    getter = FMD._get if getter is None else getter
    contract = FMD.load_contract(FMD.CONTRACT_PATH)
    policy = _load_candidate_policy()
    excluded = exclusion_basis(contract)
    # Deterministic regardless of caller ordering/duplication: sort the
    # distinct requested strings so a shuffled --date list reproduces the
    # exact same record order every time.
    unique_dates = sorted({str(value) for value in requested_dates})
    if not unique_dates:
        fail("NO_DATES_REQUESTED")
    records = [
        replay_one_requested_date(
            credentials, date, getter=getter, contract=contract, policy=policy,
            excluded=excluded,
        )
        for date in unique_dates
    ]
    population = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "wbs": "P1-COM-05",
        "market": "US",
        "evidence_class": EVIDENCE_CLASS,
        "requested_dates": unique_dates,
        "replayed_axes": list(REPLAYED_AXES),
        "excluded_axes": excluded,
        "source_contract": {
            "path": "config/free_market_data_contract.json",
            "sha256": file_sha256(FMD.CONTRACT_PATH),
            "contract_version": contract["contract_version"],
        },
        "candidate_policy": {
            "path": CANDIDATE_POLICY_PATH,
            "sha256": file_sha256(PRR.POLICY_PATH),
            "status": policy.get("status"),
        },
        "candidate_rule_source": (
            "regime/paper_regime_reference.py::build_us"
            " (TREND/RISK_VOL/LIQUIDITY axes only)"
        ),
        "source_reuse": [
            "collectors/free_market_data.py::FRED_LIQUIDITY_UNITS",
            "collectors/free_market_data.py::_session_return",
            "collectors/free_market_data.py::fetch_alpaca_daily_bars",
            "collectors/free_market_data.py::load_contract",
        ],
        "records": records,
        "pit_replay": {
            # Structural facts about *how* this population was produced. Each
            # is enforced by test/test_us_historical_replay_population.py
            # rather than merely asserted here.
            "each_date_replayed_independently": True,
            "future_dates_used_in_any_date_evaluation": False,
            "alpaca_request_end_pinned_to_requested_date": True,
            "fred_observation_end_pinned_to_requested_date": True,
            "fred_realtime_vintage_pinned_to_requested_date": True,
            "close_adjustment": "raw",
            "retained_sources_mutated_by_this_module": False,
            "candidate_rule_modified_by_this_module": False,
            "statement": (
                "Every axis observation is rebuilt from one requested date's own"
                " backward-bounded source requests plus the on-disk contract and"
                " candidate policy. FRED responses are pinned to the ALFRED"
                " vintage of the requested date so a later revision of a revised"
                " series cannot enter an earlier replayed date. No episode is"
                " selected, no threshold is tuned, and no outcome label enters"
                " any date's evaluation."
            ),
        },
        "authority": dict(AUTHORITY),
    }
    population["payload_sha256"] = payload_sha256(population)
    return population


def validate_population(value: dict) -> dict:
    """Integrity check — never re-fetches providers, always re-derives axes.

    Re-fetching would require re-issuing live Alpaca/FRED requests for every
    replayed date. Per the CIO mandate an actual provider probe must stay
    separate from implementation verification and must never become a CI
    prerequisite, so ``--verify`` never touches the network. What it *can* do
    offline, and now does, is re-derive each observed TREND/RISK_VOL/LIQUIDITY
    row from the measurement the record already stores: enforcing only that the
    candidate regime and runtime regime stay UNKNOWN would still accept a
    re-hashed payload whose axis *directions* were forged beside intact
    measurements.

    "Shape" is deliberately exact rather than "whatever happens to be present":
    a re-hashed payload is a valid signature over whatever it contains, so a
    check that only inspects the keys it finds would accept a population that
    silently dropped its records or its explicit authority boundary — and a
    record that dropped its axis packet would satisfy the never-BREADTH
    guarantee vacuously. Every requested date must therefore map to exactly one
    record, in order; a record's status must agree with the axis coverage it
    carries; and the authority block must match ``AUTHORITY`` key for key.

    Re-derived axis rows are still only half of an observation. They prove the
    stored direction follows from the stored measurement, but say nothing about
    *which provider response that measurement came from* — so each observed
    axis's ``source_hashes`` entry is separately required and checked against the
    provenance inside that axis's own measurement. Both copies are mutable
    fields of this payload, so that check establishes consistency, not external
    attribution; ``_validate_source_hash_consistency`` states the limit.
    """
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        fail("POPULATION_SCHEMA_INVALID")
    unsigned = copy.deepcopy(value)
    claimed = unsigned.pop("payload_sha256", None)
    if (
        not isinstance(claimed, str)
        or SHA256.fullmatch(claimed) is None
        or payload_sha256(unsigned) != claimed
    ):
        fail("POPULATION_SHA_INVALID")
    if value.get("mode") != MODE or value.get("evidence_class") != EVIDENCE_CLASS:
        fail("POPULATION_MODE_INVALID")
    if value.get("market") != "US" or value.get("replayed_axes") != REPLAYED_AXES:
        fail("POPULATION_SCOPE_INVALID")
    if sorted(value.get("excluded_axes", {})) != sorted(EXCLUDED_AXES):
        fail("POPULATION_SCOPE_INVALID", "excluded_axes")
    requested = value.get("requested_dates")
    if (
        not isinstance(requested, list)
        or not requested
        or any(not isinstance(date, str) for date in requested)
        or requested != sorted(set(requested))
    ):
        fail("POPULATION_DATE_ORDER_INVALID")
    _validate_records(value, requested, _revalidation_policy(value))
    _validate_authority(value)
    return copy.deepcopy(value)


def _revalidation_policy(value: dict) -> dict:
    """The candidate policy this population pinned, re-read for re-derivation.

    Recomputing a record's normalization is only meaningful against the *same*
    policy the population was built with, so the pinned sha256 is compared with
    the on-disk file rather than assumed. A checkout carrying a different
    candidate policy fails closed here with an attributable code instead of
    reporting a normalization mismatch the payload did not cause.
    """
    pinned = value.get("candidate_policy")
    if not isinstance(pinned, dict) or pinned.get("path") != CANDIDATE_POLICY_PATH:
        fail("POPULATION_CANDIDATE_POLICY_INVALID", "path")
    if pinned.get("sha256") != file_sha256(PRR.POLICY_PATH):
        fail("CANDIDATE_POLICY_SHA_MISMATCH", CANDIDATE_POLICY_PATH)
    policy = _load_candidate_policy()
    if pinned.get("status") != policy.get("status"):
        fail("POPULATION_CANDIDATE_POLICY_INVALID", "status")
    return policy


def _validate_records(value: dict, requested: list[str], policy: dict) -> None:
    """Exactly one record per requested date, in the same order — no omissions.

    ``build_population`` emits one record for each sorted, de-duplicated
    requested date, so list equality is the whole bijection: a dropped,
    duplicated, reordered, or invented record all fail here rather than
    producing a population whose coverage silently disagrees with the replay it
    claims to describe.
    """
    records = value.get("records")
    if not isinstance(records, list) or len(records) != len(requested):
        fail("POPULATION_RECORDS_NOT_BIJECTIVE", "count")
    dates = [
        record.get("requested_date") if isinstance(record, dict) else None
        for record in records
    ]
    if dates != requested:
        fail("POPULATION_RECORDS_NOT_BIJECTIVE", "requested_date")
    for record, requested_date in zip(records, requested):
        _validate_record(record, requested_date, policy)


def _validate_record(record: dict, requested_date: str, policy: dict) -> None:
    if not isinstance(record, dict) or record.get("evidence_class") != EVIDENCE_CLASS:
        fail("RECORD_EVIDENCE_CLASS_INVALID")
    status = record.get("status")
    if status not in RECORD_STATUSES:
        fail("RECORD_STATUS_INVALID")
    five_axis = record.get("five_axis")
    candidate = record.get("candidate_normalized_result")
    # A record may not claim an observed or partial replay while omitting the
    # axis packet and candidate result those claims are made of: nulling both
    # would otherwise satisfy every axis guarantee below by having no axes.
    if status in (STATUS_OBSERVED, STATUS_PARTIAL):
        if not isinstance(five_axis, dict) or not isinstance(candidate, dict):
            fail("REPLAYED_RECORD_MUST_CARRY_ITS_EVIDENCE", requested_date)
    elif candidate is not None:
        fail("BLOCKED_RECORD_MUST_NOT_CLASSIFY", requested_date)

    axes: dict = {}
    observed: list[str] = []
    not_computable = list(REPLAYED_AXES)
    if five_axis is not None:
        if not isinstance(five_axis, dict):
            fail("RECORD_FIVE_AXIS_INVALID", requested_date)
        axes = five_axis.get("axes")
        if not isinstance(axes, dict) or sorted(axes) != sorted(PRR.AXES):
            fail("RECORD_AXIS_SET_INVALID", requested_date)
        # The one substantive guarantee of this slice: a US BREADTH or
        # LEADERSHIP value must never appear in this population, whatever else
        # a record carries.
        for name in EXCLUDED_AXES:
            entry = axes.get(name)
            if (
                not isinstance(entry, dict)
                or entry.get("status") != "UNKNOWN"
                or entry.get("measurement") is not None
            ):
                fail("EXCLUDED_AXIS_MUST_STAY_UNKNOWN", name)
        for name in REPLAYED_AXES:
            entry = axes.get(name)
            if (
                not isinstance(entry, dict)
                or entry.get("status") not in ("OBSERVED", "NOT_COMPUTABLE")
            ):
                fail("REPLAYED_AXIS_STATUS_INVALID", name)
        observed = [
            name for name in REPLAYED_AXES if axes[name].get("status") == "OBSERVED"
        ]
        not_computable = [name for name in REPLAYED_AXES if name not in observed]

    # Coverage and status are recomputed from the axes themselves, so a payload
    # cannot report a coverage ratio or a record status the axes do not support.
    coverage = record.get("free_axis_coverage")
    if not isinstance(coverage, dict):
        fail("RECORD_COVERAGE_MISSING", requested_date)
    if (
        coverage.get("observed_axes") != observed
        or coverage.get("not_computable_axes") != not_computable
        or coverage.get("observed_count") != len(observed)
        or coverage.get("ratio") != f"{len(observed)}/{len(REPLAYED_AXES)}"
    ):
        fail("RECORD_COVERAGE_INCONSISTENT", requested_date)
    if status != _status_for(observed):
        fail("RECORD_STATUS_INCONSISTENT_WITH_COVERAGE", requested_date)

    if candidate is not None:
        if candidate.get("paper_reference", {}).get("candidate_regime") != "UNKNOWN":
            fail("PARTIAL_COVERAGE_MUST_NOT_CLASSIFY")
        if candidate.get("runtime_regime") != "UNKNOWN":
            fail("RUNTIME_REGIME_MUST_STAY_UNKNOWN")
        if candidate.get("classification_status") != CLASSIFICATION_STATUS:
            fail("CLASSIFICATION_STATUS_INVALID")
    _validate_candidate_is_derived_from_its_evidence(
        record, five_axis, candidate, policy, requested_date,
    )
    _validate_source_hash_consistency(record, axes, observed, requested_date)
    _validate_no_lookahead(record, requested_date)


# Each observed axis's row is rebuilt by the *same* helper that produced it, so
# re-derivation cannot drift from production even if a threshold in one of those
# helpers is later changed upstream.
AXIS_ROW_FROM_MEASUREMENT = {
    "TREND": lambda measurement: trend_axis_row(measurement.get("trend_etfs")),
    "RISK_VOL": lambda measurement: risk_vol_axis_row(measurement.get("value")),
    "LIQUIDITY": lambda measurement: liquidity_axis_row(measurement.get("series")),
}


def _rederive_axis_rows(axes: dict) -> list[dict]:
    """Rebuild the candidate rows the record's own observed measurements yield.

    Only axes the record itself calls ``OBSERVED`` contribute, in
    ``REPLAYED_AXES`` order, which is exactly how ``replay_one_requested_date``
    assembles them.
    """
    rows = []
    for name in REPLAYED_AXES:
        entry = axes.get(name)
        if not isinstance(entry, dict) or entry.get("status") != "OBSERVED":
            continue
        measurement = entry.get("measurement")
        if not isinstance(measurement, dict):
            fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", name)
        rows.append(AXIS_ROW_FROM_MEASUREMENT[name](measurement))
    return rows


def _validate_candidate_is_derived_from_its_evidence(
    record: dict, five_axis: object, candidate: object, policy: dict, requested_date: str,
) -> None:
    """A stored axis direction must be what the stored measurement yields.

    The UNKNOWN candidate/runtime checks above constrain the *classification*
    but say nothing about the rows underneath it, so a re-hashed payload could
    keep genuine Alpaca/FRED measurements and publish any direction beside them
    — and every downstream transition, stress, and run fact is built from those
    directions. Re-deriving each observed row with the same helper that produced
    it, and requiring exact equality of the whole normalization result, closes
    that. The effective session date is bound to the TREND measurement for the
    same reason: it is otherwise a free-standing claim.
    """
    if not isinstance(five_axis, dict):
        # No axis packet means no normalization to re-derive; the status and
        # coverage rules above already govern that case.
        if candidate is not None:
            fail("BLOCKED_RECORD_MUST_NOT_CLASSIFY", requested_date)
        return
    axes = five_axis.get("axes")
    axes = axes if isinstance(axes, dict) else {}
    trend = axes.get("TREND")
    trend_measurement = trend.get("measurement") if isinstance(trend, dict) else None
    expected_effective = (
        trend_measurement.get("as_of_session_date")
        if isinstance(trend_measurement, dict)
        else None
    )
    if record.get("effective_session_date") != expected_effective:
        fail("EFFECTIVE_SESSION_DATE_NOT_DERIVED_FROM_ITS_EVIDENCE", requested_date)
    try:
        rows = _rederive_axis_rows(axes)
        expected = (
            _candidate_normalized_result(
                rows, expected_effective, policy, {name: {} for name in EXCLUDED_AXES},
            )
            if rows
            else None
        )
    except (ReplayPopulationError, PRR.PaperRegimeReferenceError) as exc:
        raise ReplayPopulationError(
            f"OBSERVED_AXIS_EVIDENCE_NOT_NORMALIZABLE:{requested_date}:{exc}"
        ) from exc
    if candidate != expected:
        fail("RECORD_CANDIDATE_NOT_DERIVED_FROM_ITS_EVIDENCE", requested_date)


def _sha256_text(value: object, code: str, detail: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        fail(code, detail)
    return value


def _expected_liquidity_hashes(measurement: dict, requested_date: str) -> dict:
    """The FRED response hashes the LIQUIDITY measurement itself carries.

    One entry per series the measurement actually used, each with both the
    metadata and the observations response — a series whose provenance was
    dropped would otherwise leave its change value unattributed.
    """
    hashes = measurement.get("response_hashes")
    series = measurement.get("series")
    if not isinstance(series, list):
        fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", f"{requested_date}:LIQUIDITY")
    series_ids = sorted({
        row.get("series_id") for row in series if isinstance(row, dict)
    })
    if not isinstance(hashes, dict) or sorted(hashes) != series_ids:
        fail("OBSERVED_AXIS_MUST_CARRY_ITS_SOURCE_HASHES", f"{requested_date}:LIQUIDITY")
    for series_id in series_ids:
        row = hashes[series_id]
        label = f"{requested_date}:LIQUIDITY.{series_id}"
        if not isinstance(row, dict) or sorted(row) != sorted(LIQUIDITY_RESPONSE_HASH_KEYS):
            fail("OBSERVED_AXIS_SOURCE_HASH_SHAPE_INVALID", label)
        for key in LIQUIDITY_RESPONSE_HASH_KEYS:
            _sha256_text(
                row[key], "OBSERVED_AXIS_SOURCE_HASH_SYNTAX_INVALID", f"{label}.{key}",
            )
    return hashes


def _validate_source_hash_consistency(
    record: dict, axes: dict, observed: list[str], requested_date: str,
) -> None:
    """The record's source hashes must agree with its own measurements.

    Re-deriving each axis row proves the stored *direction* follows from the
    stored measurement, but says nothing about where that measurement came from:
    a re-hashed payload could delete ``source_hashes`` outright, or point it at a
    different response, and every other check above would still pass. Each
    per-axis hash is therefore required to be present exactly when that axis is
    ``OBSERVED``, absent exactly when it is not, syntactically a SHA-256, and
    equal to the provenance carried inside that axis's own measurement — so a
    record-level hash can neither be removed nor swapped for another axis's
    while the measurement it claims to attribute stays put.

    The name is deliberately ``consistency`` rather than ``provenance``: both
    compared values are mutable fields of the same payload, so this cannot
    establish that either one is the digest a provider actually served. An
    adversary who edits *both* copies to the same arbitrary valid SHA-256 and
    recomputes the population digest passes, and there is no retained raw
    response or provider signature to catch it — obtaining such an anchor is a
    data decision, not something this validator can synthesize. Both sides of
    that boundary are pinned in
    ``test_us_historical_replay_population.py`` so the claim stays accurate.

    A record with no observed axis has no provenance to carry, which is why the
    whole block may be ``null`` only in that case.
    """
    expected = {key: None for key in SOURCE_HASH_KEYS}
    for name in REPLAYED_AXES:
        if name not in observed:
            continue
        entry = axes.get(name)
        measurement = entry.get("measurement") if isinstance(entry, dict) else None
        if not isinstance(measurement, dict):
            fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", f"{requested_date}:{name}")
        if name == "LIQUIDITY":
            expected[AXIS_RESPONSE_HASH_KEY[name]] = _expected_liquidity_hashes(
                measurement, requested_date,
            )
            continue
        expected[AXIS_RESPONSE_HASH_KEY[name]] = _sha256_text(
            measurement.get("response_sha256"),
            "OBSERVED_AXIS_MUST_CARRY_ITS_SOURCE_HASHES",
            f"{requested_date}:{name}",
        )

    hashes = record.get("source_hashes")
    if hashes is None:
        if observed:
            fail("OBSERVED_RECORD_MUST_CARRY_ITS_SOURCE_HASHES", requested_date)
        return
    if not isinstance(hashes, dict) or sorted(hashes) != sorted(SOURCE_HASH_KEYS):
        fail("RECORD_SOURCE_HASH_SCHEMA_INVALID", requested_date)
    if hashes != expected:
        fail("RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS", requested_date)


def _validate_no_lookahead(record: dict, requested_date: str) -> None:
    """Re-check, never trust, that this record only ever looked backward.

    ``no_lookahead_attestation`` is a *claim*; the source dates it names are the
    evidence. Both are compared against the requested date here, so a payload
    cannot assert "no lookahead" over a session or vintage it could not have
    seen. The attestation is walked generically, so a date field added to it
    later is covered automatically rather than escaping this check.
    """
    attestation = record.get("no_lookahead_attestation")
    if not isinstance(attestation, dict):
        fail("RECORD_ATTESTATION_MISSING", requested_date)
    if attestation.get("anchor_requested_date") != requested_date:
        fail("RECORD_ATTESTATION_ANCHOR_INVALID", requested_date)
    if (
        attestation.get("any_source_date_after_requested_date") is not False
        or attestation.get("other_requested_dates_consulted") is not False
    ):
        fail("RECORD_ATTESTATION_CLAIM_INVALID", requested_date)
    if DATE10.fullmatch(requested_date) is None:
        # A malformed requested date is itself a legitimate BLOCKED record;
        # there is no calendar anchor to compare against.
        return
    consulted = _dates_in(attestation) + _dates_in(record.get("effective_session_date"))
    if any(date > requested_date for date in consulted):
        fail("RECORD_LOOKAHEAD_VIOLATION", requested_date)


def _dates_in(value: object) -> list[str]:
    """Every ISO-date-shaped string reachable inside ``value``."""
    if isinstance(value, str):
        return [value] if DATE10.fullmatch(value) is not None else []
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _dates_in(entry)]
    if isinstance(value, list):
        return [item for entry in value for item in _dates_in(entry)]
    return []


def _status_for(observed: list[str]) -> str:
    if len(observed) == len(REPLAYED_AXES):
        return STATUS_OBSERVED
    return STATUS_PARTIAL if observed else STATUS_BLOCKED


def _validate_authority(value: dict) -> None:
    """The authority block must be present, complete, and exactly as declared."""
    authority = value.get("authority")
    if not isinstance(authority, dict) or sorted(authority) != sorted(AUTHORITY):
        fail("POPULATION_AUTHORITY_SCHEMA_INVALID")
    for key, allowed in AUTHORITY.items():
        if authority[key] is not allowed:
            fail("POPULATION_AUTHORITY_INVALID", key)


def _forbid_tracked_output(root: Path, path: Path) -> None:
    """Fail closed if ``path`` resolves inside this repository checkout.

    Historical replay evidence must never land in any tracked location —
    NATURAL ``evidence/free_market_data/`` and ``data/`` included — so the
    guard is a blanket "not inside the checkout at all", not a NATURAL-path
    denylist that a new tracked directory could slip past.
    """
    root_resolved = Path(root).resolve()
    path_resolved = Path(path).resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError:
        return
    fail("TRACKED_OUTPUT_FORBIDDEN", str(path_resolved))


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_population(population: dict, out_path: Path, *, root: Path = ROOT) -> Path:
    _forbid_tracked_output(root, out_path)
    text = json.dumps(population, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(Path(out_path), text)
    return Path(out_path)


def _default_temp_out() -> Path:
    fd, name = tempfile.mkstemp(prefix="us_historical_replay_population.", suffix=".json")
    os.close(fd)
    return Path(name)


def _credentials_from_env() -> dict:
    # ★ The account/trading Alpaca credential (`ALPACA_API_KEY` /
    # `ALPACA_API_SECRET`) lives only in the private evidence repo. This
    # module, like collectors/free_market_data.py, is a market-data-only
    # consumer and has no code path that reads those names.
    return {
        "fred_key": os.environ.get("FRED_API_KEY", "").strip(),
        "alpaca_key": os.environ.get("ALPACA_MARKET_DATA_API_KEY", "").strip(),
        "alpaca_secret": os.environ.get("ALPACA_MARKET_DATA_API_SECRET", "").strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", action="append", default=[], dest="dates",
        help="Historical US date, YYYY-MM-DD. Repeatable. No date is ever selected automatically.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="External output path (must be outside this checkout). Defaults to a private system-temp file.",
    )
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()

    if args.verify:
        value = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        validate_population(value)
        print(f"PASS_US_HISTORICAL_REPLAY_POPULATION_VERIFIED:{value['payload_sha256']}")
        return 0

    if not args.dates:
        fail("NO_DATES_REQUESTED")

    population = build_population(_credentials_from_env(), args.dates)
    out_path = args.out if args.out is not None else _default_temp_out()
    write_population(population, out_path)
    counts = {status: 0 for status in RECORD_STATUSES}
    for record in population["records"]:
        counts[record["status"]] += 1
    print(json.dumps(
        {
            "out": str(out_path),
            "payload_sha256": population["payload_sha256"],
            "records": len(population["records"]),
            "free_axes_observed": counts[STATUS_OBSERVED],
            "free_axes_partial": counts[STATUS_PARTIAL],
            "blocked": counts[STATUS_BLOCKED],
        },
        ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
