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
relabelled.  That basis is a derivation, not a label: ``validate_population``
re-reads the pinned contract, binds its sha256, and rebuilds the basis, so a
re-signed payload can neither restate the ratification scope nor carry a
record-level exclusion reason the contract does not support.  Because coverage
is therefore 3/5, the candidate normalization
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
* Pinning the request is only half of that.  What enters the population is what
  the provider *answered*, so every returned FRED vintage window — the latest
  observation, the previous observation the change is measured against, and the
  series metadata that fixes the units — is required to contain the requested
  date, at build time and again in ``validate_population``.  A response whose
  vintage opens after the replayed date is a lookahead and fails that axis
  closed; one whose vintage had already ended was superseded before the date and
  is refused separately, because it is a different fact.  The bind is
  containment, not equality: a still-current FRED value legitimately reports
  ``realtime_end`` as ``9999-12-31``.
* Every provider- and payload-supplied date is *parsed* as a calendar date
  before it is compared.  Shape is not a calendar: ``2026-02-31`` is
  ``YYYY-MM-DD``-shaped, is a day no calendar has, and — because ISO dates
  compare lexicographically — sorts before ``2026-03-01``, so a shape check
  followed by a string comparison cleared it as ordinary backward-looking
  evidence wherever it appeared.
* Every date a measurement carries — the observation the value came from, the
  previous observation the change is measured against, and the Alpaca sessions
  the closes came from — is bound to the requested date in
  ``validate_population`` as well, because the attestation walk never reaches
  inside a measurement.  The still-current ``9999-12-31`` vintage sentinel is
  the single exemption and is bound separately as a containment window.
* Each requested date is resolved independently from its own anchor, so no
  other requested date's outcome can influence this one.
* The population's own ``pit_replay`` declaration is validated key for key
  against the shape ``build_population`` publishes.  A re-hashed payload is a
  valid signature over whatever it contains, so an unchecked declaration could
  be re-signed with ``future_dates_used_in_any_date_evaluation`` set ``true``,
  or deleted outright, while every record-level check still passed.

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

Every *derived* field of a record is re-derived rather than read.  A record's
coverage blocks, five-axis status, per-axis entry shapes, record status, failure
reason, disclosed warnings, and attested source dates are all computed from the
axes that date actually produced, by the same helpers ``build_population`` uses,
and ``validate_population`` rebuilds each of them.  Without that, any one of them
is free text under a valid signature: an integration probe re-signed
``attempted_count`` to 0 beside three observed axes, a five-axis
``coverage.defined_count`` to 999, and a fabricated ``failure_reason`` onto a
fully observed record, and each was accepted with every measurement, axis row,
response hash, and payload digest left genuine.

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
SOURCE_CONTRACT_PATH = "config/free_market_data_contract.json"
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

# The exact derived shape of one record, declared once so the builders below and
# ``_validate_record`` cannot drift apart, and re-required key for key and value
# for value at validation time.
#
# Every field named here is *derived* from the axes a date actually produced. A
# re-hashed payload is a valid signature over whatever it contains, so any
# derived field that nothing re-derives can be rewritten to contradict the axes
# it came from while the measurements, the axis rows, the source hashes, and the
# payload digest all stay internally consistent — an ``attempted_count`` of 0
# beside three observed axes, a five-axis ``defined_count`` of 999, or an
# ``OBSERVED`` record carrying a fabricated ``failure_reason`` a reader would
# treat as the cause of a failure that never happened.
FIVE_AXIS_KEYS = ("axes", "coverage", "status")
FIVE_AXIS_STATUS_OBSERVED = "OBSERVED_UNCLASSIFIED_FREE_AXES_ONLY"
FIVE_AXIS_STATUS_NONE = "NOT_COMPUTABLE_NO_FREE_AXIS_OBSERVED"
AXIS_ENTRY_KEYS = ("measurement", "reason", "status")
# The record-level reason a date carries when every free axis was attempted and
# none survived. Per-axis reasons carry the attribution; this only states that
# nothing publishable survived, so it is a fixed code rather than free text.
ALL_AXES_NOT_COMPUTABLE_REASON = "ALL_FREE_AXES_NOT_COMPUTABLE"
# The one record-level reason a date carries after every replayed axis was
# attempted and the date was then failed closed for consuming a later source
# date. It is the only blocked-without-a-packet case with a non-zero
# ``attempted_count``, which is how ``_validate_record`` re-derives that count.
LOOKAHEAD_BLOCKED_REASON = "US_REPLAY_LOOKAHEAD_VIOLATION"

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

# The ALFRED vintage bounds every consumed FRED row must carry.
# ``realtime_start`` is when a value became the current one; ``realtime_end`` is
# when it stopped being current — FRED serves the open-ended sentinel
# ``9999-12-31`` while it still is. Pinning the *request* to the replayed date
# only states what was asked for; what travels into this population is what the
# provider answered, so each returned window is bound to the requested date at
# both build and validation time. The bind is containment of the requested date
# in the window, never equality with it: a genuine current value legitimately
# reports a ``realtime_end`` far in the future, while a value whose vintage
# *begins* after the requested date could not have been known on it.
FRED_VINTAGE_KEYS = ("realtime_start", "realtime_end")
FRED_PREVIOUS_VINTAGE_KEYS = ("previous_realtime_start", "previous_realtime_end")
FRED_METADATA_VINTAGE_KEYS = ("metadata_realtime_start", "metadata_realtime_end")
# The only measurement dates that may legitimately fall after the requested
# date, and therefore the only ones exempt from the backward-looking walk in
# ``_validate_measurement_source_dates``. They are bound as containment windows
# by ``_assert_vintage_covers`` instead.
VINTAGE_END_KEYS = frozenset(
    key for _, key in (
        FRED_VINTAGE_KEYS, FRED_PREVIOUS_VINTAGE_KEYS, FRED_METADATA_VINTAGE_KEYS,
    )
)

# The exact point-in-time block this population publishes, declared once so
# ``build_population`` and ``validate_population`` cannot drift apart, and
# required key for key by ``_validate_pit_replay``.
#
# This block is the population's own declaration that no date's evaluation saw a
# later session or a later vintage. A re-hashed payload is a valid signature over
# whatever it contains, so a validator that never inspected it would accept a
# population that flipped ``future_dates_used_in_any_date_evaluation`` to
# ``true`` — a payload simultaneously claiming to honour and to breach the
# non-negotiable PIT boundary — or that simply deleted the declaration and left
# nothing to check.
PIT_REPLAY_TRUE_KEYS = (
    "each_date_replayed_independently",
    "alpaca_request_end_pinned_to_requested_date",
    "fred_observation_end_pinned_to_requested_date",
    "fred_realtime_vintage_pinned_to_requested_date",
    "fred_returned_vintage_bound_to_requested_date",
)
PIT_REPLAY_FALSE_KEYS = (
    "future_dates_used_in_any_date_evaluation",
    "retained_sources_mutated_by_this_module",
    "candidate_rule_modified_by_this_module",
)
PIT_REPLAY_CLOSE_ADJUSTMENT = "raw"
PIT_REPLAY_STATEMENT = (
    "Every axis observation is rebuilt from one requested date's own"
    " backward-bounded source requests plus the on-disk contract and"
    " candidate policy. FRED requests are pinned to the ALFRED vintage of the"
    " requested date and every returned vintage window is required to contain"
    " that date, so neither a later revision of a revised series nor a"
    " future-vintage response can enter an earlier replayed date. No episode is"
    " selected, no threshold is tuned, and no outcome label enters any date's"
    " evaluation."
)
PIT_REPLAY_KEYS = PIT_REPLAY_TRUE_KEYS + PIT_REPLAY_FALSE_KEYS + (
    "close_adjustment", "statement",
)

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


def _calendar_date(value: object) -> dt.date | None:
    """The real calendar date a string denotes, or ``None`` if it denotes none.

    Date *shape* is not a date. ``DATE10`` accepts ``2026-02-31`` and
    ``2026-13-01``, and ISO strings compare lexicographically, so a shape-only
    check followed by a string comparison silently clears a day that never
    existed: ``2026-02-31`` sorts before ``2026-03-01`` and therefore reads as
    backward-looking against every later anchor. Every provider-supplied and
    payload-supplied date in this module is parsed here before it is compared, so
    a calendar-impossible date fails closed instead of satisfying a
    point-in-time bound it could never have satisfied.

    The ``DATE10`` shape gate is kept ahead of the parse rather than replaced by
    it: on current Python ``dt.date.fromisoformat`` also accepts ``20260228`` and
    ISO week/ordinal forms, none of which is the ``YYYY-MM-DD`` this population
    publishes, so the gate keeps the accepted set fixed across interpreters.
    """
    if not isinstance(value, str) or DATE10.fullmatch(value) is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _parse_requested_date(value: str) -> dt.date:
    if not isinstance(value, str) or DATE10.fullmatch(value) is None:
        fail("REQUESTED_DATE_FORMAT_INVALID")
    parsed = _calendar_date(value)
    if parsed is None:
        fail("REQUESTED_DATE_CALENDAR_INVALID")
    return parsed


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
    """A provider observation date must be a real day at or before ``anchor``.

    Parsed, not shape-matched: a provider answering ``2026-02-31`` names no day
    at all, and comparing that string against the anchor would pass it through
    as an ordinary earlier observation.
    """
    parsed = _calendar_date(observation_date)
    if parsed is None:
        fail(code)
    if parsed > anchor:
        fail("US_REPLAY_LOOKAHEAD_VIOLATION", code)
    return parsed.isoformat()


def _assert_vintage_covers(
    requested_date: str,
    row: object,
    label: str,
    keys: tuple[str, str] = FRED_VINTAGE_KEYS,
) -> tuple[str, str]:
    """The FRED row actually returned must be the one current on the replayed date.

    ``_fred_query``/``_fred_metadata_query`` pin the ALFRED vintage on the
    *request*, which states only what was asked for. The value that travels into
    this population is what the provider answered, so the returned vintage window
    is bound to the requested date here rather than assumed to match the query.
    Without this bind a response carrying ``realtime_start``/``realtime_end``
    after the requested date — a revision published later — is consumed as if it
    had been knowable on that date, and every downstream check passes because the
    measurement, the re-derived axis row, and every hash are internally
    consistent.

    Two distinct failures, kept distinct because they are different facts:

    * a window that *begins* after the requested date is a lookahead — that value
      did not exist yet;
    * a window that *ended* before the requested date was already superseded, so
      it is not what the date could have been evaluated with either. This is not
      a lookahead and is not reported as one.

    ``realtime_end`` later than the requested date is normal and required: FRED
    serves ``9999-12-31`` while a value is still current. The bind is therefore
    containment of the requested date in the window, never equality with it.

    Both window bounds and the requested date are parsed as calendar dates
    first. Comparing the ISO strings alone let a window such as
    ``2026-02-31``/``2026-02-31`` — a day no calendar has — satisfy containment
    against an anchor later in the year, so a re-signed measurement could carry a
    vintage that cannot be checked against any real ALFRED window while every
    downstream hash and re-derivation stayed consistent.
    """
    anchor = _calendar_date(requested_date)
    if anchor is None:
        fail("REQUESTED_DATE_CALENDAR_INVALID", label)
    if not isinstance(row, dict):
        fail("US_FRED_VINTAGE_MISSING", label)
    bounds = []
    for key in keys:
        parsed = _calendar_date(row.get(key))
        if parsed is None:
            fail("US_FRED_VINTAGE_MISSING", f"{label}.{key}")
        bounds.append(parsed)
    start, end = bounds
    if start > anchor:
        fail("US_REPLAY_LOOKAHEAD_VIOLATION", f"FRED_VINTAGE:{label}")
    if end < anchor:
        fail("US_FRED_VINTAGE_SUPERSEDED_BEFORE_REQUESTED_DATE", label)
    return start.isoformat(), end.isoformat()


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
        # Parsed, not shape-matched: a bar timestamped ``2026-02-31`` is not a
        # session, and comparing that string against the anchor would admit it
        # as an ordinary earlier one.
        session = _calendar_date(str(row.get("opened_at", ""))[:10])
        if session is None:
            fail("US_TREND_SESSION_DATE_INVALID")
        # A provider that answers with a later bar than requested must fail
        # this date closed rather than have the bar silently trimmed.
        if session > anchor:
            fail("US_REPLAY_LOOKAHEAD_VIOLATION", "ALPACA_BAR")
        grouped.setdefault(row["symbol"], []).append(
            {**row, "session_date": session.isoformat()}
        )

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
    # Bound, not copied: the observation date alone says nothing about which
    # vintage of that observation was served.
    realtime_start, realtime_end = _assert_vintage_covers(
        anchor.isoformat(), latest, series_id,
    )
    return {
        "series_id": series_id,
        "source_scope": contract["fred"]["source_scope"],
        "observation_date": observation_date,
        "value": latest.get("value"),
        "realtime_start": realtime_start,
        "realtime_end": realtime_end,
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
        # The units definition is itself vintaged: a later metadata vintage can
        # carry a units string — and therefore a normalization factor — that was
        # not in effect on the replayed date.
        metadata_realtime_start, metadata_realtime_end = _assert_vintage_covers(
            anchor.isoformat(), metadata_rows[0], f"{series_id}.metadata",
        )
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
        # Both consumed rows are bound: the change is a difference, so a
        # future-vintage *previous* value corrupts it exactly as a future-vintage
        # latest value does.
        realtime_start, realtime_end = _assert_vintage_covers(
            anchor.isoformat(), latest, series_id,
        )
        previous_realtime_start, previous_realtime_end = _assert_vintage_covers(
            anchor.isoformat(), previous, f"{series_id}.previous",
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
            "realtime_start": realtime_start,
            "realtime_end": realtime_end,
            "previous_realtime_start": previous_realtime_start,
            "previous_realtime_end": previous_realtime_end,
            "metadata_realtime_start": metadata_realtime_start,
            "metadata_realtime_end": metadata_realtime_end,
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
                f"{SOURCE_CONTRACT_PATH}"
                "#alpaca.current_proxy_axes.approval_status": approval_status,
                f"{SOURCE_CONTRACT_PATH}"
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


def _free_axis_coverage(
    observed: list[str], not_computable: list[str], attempted: int,
) -> dict:
    """The record's own free-axis coverage, derived from the axes themselves."""
    return {
        "attempted_count": attempted,
        "observed_count": len(observed),
        "ratio": f"{len(observed)}/{len(REPLAYED_AXES)}",
        "observed_axes": list(observed),
        "not_computable_axes": list(not_computable),
    }


def _five_axis_block(observed: list[str], not_computable: list[str], axes: dict) -> dict:
    """The five-axis packet, whose status and coverage are derived, not asserted.

    The status names what this packet actually holds: never "5/5 observed", and
    never "observed" at all when nothing survived. The coverage counts the same
    axes the packet carries, and the excluded pair is always missing because this
    module never populates it.
    """
    return {
        "status": FIVE_AXIS_STATUS_OBSERVED if observed else FIVE_AXIS_STATUS_NONE,
        "coverage": {
            "defined_count": len(observed),
            "required_count": len(PRR.AXES),
            "ratio": f"{len(observed)}/{len(PRR.AXES)}",
            "defined_axes": list(observed),
            "missing_axes": sorted(list(not_computable) + EXCLUDED_AXES),
        },
        "axes": axes,
    }


def _no_lookahead_attestation(
    requested_date: str,
    trend: dict | None,
    risk: dict | None,
    liquidity: dict | None,
    liquidity_dates: list[str],
    *,
    replayed: bool,
) -> dict:
    """The dates this record actually consulted, taken from its own measurements.

    Derived rather than declared, and re-derived the same way in
    ``_validate_attestation_is_derived_from_its_evidence``: a summary of consulted
    dates that nothing rebuilds can be re-signed into naming fewer sources than
    the record used, which would leave ``_validate_no_lookahead``'s walk with
    nothing to reject.
    """
    return {
        "anchor_requested_date": requested_date,
        # A date that produced no axis packet at all issued no vintage-pinned
        # request whose vintage could be attested.
        "fred_realtime_vintage_date": requested_date if replayed else None,
        # ``get`` rather than ``[]``: the same helper re-derives this block from a
        # payload-supplied measurement at validation time, where a missing key
        # must produce a mismatch that fails the record closed rather than an
        # unhandled ``KeyError``.
        "trend_session_date_range": (
            [trend.get("earliest_session_date"), trend.get("as_of_session_date")]
            if trend
            else []
        ),
        "vix_observation_date": risk.get("observation_date") if risk else None,
        "liquidity_observation_dates": list(liquidity_dates) if liquidity else [],
        "any_source_date_after_requested_date": False,
        "other_requested_dates_consulted": False,
    }


def _blocked_date_record(
    requested_date: str, failure_reason: str, *, attempted: int = 0,
) -> dict:
    return {
        "requested_date": requested_date,
        "status": STATUS_BLOCKED,
        "evidence_class": EVIDENCE_CLASS,
        "effective_session_date": None,
        "free_axis_coverage": _free_axis_coverage([], list(REPLAYED_AXES), attempted),
        "five_axis": None,
        "candidate_normalized_result": None,
        "source_hashes": None,
        "failure_reason": failure_reason,
        "warnings": list(RECORD_WARNINGS),
        "no_lookahead_attestation": _no_lookahead_attestation(
            requested_date, None, None, None, [], replayed=False,
        ),
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
            requested_date, LOOKAHEAD_BLOCKED_REASON, attempted=len(REPLAYED_AXES),
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
        status, failure_reason = STATUS_BLOCKED, ALL_AXES_NOT_COMPUTABLE_REASON

    return {
        "requested_date": requested_date,
        "status": status,
        "evidence_class": EVIDENCE_CLASS,
        "effective_session_date": effective_session_date,
        "free_axis_coverage": _free_axis_coverage(
            observed_axes, not_computable, len(REPLAYED_AXES),
        ),
        "five_axis": _five_axis_block(observed_axes, not_computable, axes),
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
        "no_lookahead_attestation": _no_lookahead_attestation(
            requested_date, trend, risk, liquidity, liquidity_dates, replayed=True,
        ),
    }


# ---------------------------------------------------------------------------
# Population.
# ---------------------------------------------------------------------------


def _pit_replay_block() -> dict:
    """The population's point-in-time declaration, built from the shared shape.

    Emitted here and re-required by ``_validate_pit_replay`` from the same
    constants, so a field can never be published without being checked or
    checked without being published.
    """
    return {
        **{key: True for key in PIT_REPLAY_TRUE_KEYS},
        **{key: False for key in PIT_REPLAY_FALSE_KEYS},
        "close_adjustment": PIT_REPLAY_CLOSE_ADJUSTMENT,
        "statement": PIT_REPLAY_STATEMENT,
    }


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
            "path": SOURCE_CONTRACT_PATH,
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
        # Structural facts about *how* this population was produced. Each is
        # enforced by test/test_us_historical_replay_population.py rather than
        # merely asserted here, and each is re-required key for key by
        # ``_validate_pit_replay``.
        "pit_replay": _pit_replay_block(),
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

    Exactness extends to every *derived* field of a record, not only to the ones
    a first pass happened to compare. An integration probe re-signed
    ``attempted_count`` to 0 beside three observed axes, the five-axis
    ``coverage.defined_count`` to 999, and a fabricated ``failure_reason`` onto a
    fully observed record, and each was accepted because nothing rebuilt those
    fields. ``_validate_record`` now recomputes both coverage blocks, the
    five-axis status, every per-axis entry shape, the record status, the failure
    reason, the disclosed warnings, and the attested source dates from the axes
    the record actually carries, using the same helpers that produced them.

    Re-derived axis rows are still only half of an observation. They prove the
    stored direction follows from the stored measurement, but say nothing about
    *which provider response that measurement came from* — so each observed
    axis's ``source_hashes`` entry is separately required and checked against the
    provenance inside that axis's own measurement. Both copies are mutable
    fields of this payload, so that check establishes consistency, not external
    attribution; ``_validate_source_hash_consistency`` states the limit.

    The excluded-axis scope gets the same treatment as the axis rows: the reason
    BREADTH and LEADERSHIP stay UNKNOWN is a *derivation* from
    ``config/free_market_data_contract.json``, so it is re-derived from that
    pinned, re-read contract rather than accepted as written. Checking only the
    excluded-axis key set would let a re-hashed payload keep the two names while
    rewriting the ratification basis they rest on, and leave the pinned contract
    digest itself unbound.

    Two point-in-time facts are re-checked here rather than trusted, because
    neither is reachable from the record dates ``_validate_no_lookahead`` walks.
    ``_validate_fred_vintage_binding`` requires every observed FRED measurement's
    stored ALFRED vintage window to contain the requested date — a
    future-vintage response is otherwise consumed as if it had been knowable,
    with the measurement, the re-derived row, the source hashes, and the payload
    signature all internally consistent. ``_validate_pit_replay`` requires the
    population's own PIT declaration key for key and value for value, so that
    declaration cannot be re-signed into claiming the opposite of what this
    module does, or deleted so there is nothing left to check.
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
    excluded = _validate_excluded_axes(value, _revalidation_contract(value))
    requested = value.get("requested_dates")
    if (
        not isinstance(requested, list)
        or not requested
        or any(not isinstance(date, str) for date in requested)
        or requested != sorted(set(requested))
    ):
        fail("POPULATION_DATE_ORDER_INVALID")
    _validate_records(value, requested, _revalidation_policy(value), excluded)
    _validate_pit_replay(value)
    _validate_authority(value)
    return copy.deepcopy(value)


def _validate_pit_replay(value: dict) -> None:
    """The point-in-time declaration must be complete and must say what it says.

    Point-in-time integrity is non-negotiable, so the block asserting it is
    checked exactly rather than carried unread. Carrying it unread was a real
    hole, not a theoretical one: every other check here re-derives *records*, so
    a payload that re-signed itself with
    ``future_dates_used_in_any_date_evaluation`` set ``true``, or that deleted
    the declaration outright, validated successfully while publishing — under a
    valid signature — a population that simultaneously claims and denies the
    boundary.

    Three things are required and none is redundant:

    * the **exact key set**, because a payload that deletes a flag has not
      stopped claiming PIT integrity, it has stopped being checkable;
    * the **declared value** of every flag and of ``close_adjustment``, because
      the adjustment convention is a disclosed limitation a reader relies on;
    * the **statement**, because rewriting the prose while leaving the booleans
      alone misdescribes the same payload just as effectively to a human reader.

    This is a check on what the population *declares*. What it actually did is
    enforced separately and structurally — by the per-record lookahead re-check
    in ``_validate_no_lookahead``, the returned-vintage bind in
    ``_validate_fred_vintage_binding``, the per-measurement source-date bind in
    ``_validate_measurement_source_dates``, and full axis re-derivation — none of
    which depends on this block being honest. All of them parse each date with
    ``_calendar_date`` before comparing it, so a date-shaped string that names no
    day cannot satisfy a bound by string ordering alone.
    """
    pit = value.get("pit_replay")
    if not isinstance(pit, dict) or sorted(pit) != sorted(PIT_REPLAY_KEYS):
        fail("PIT_REPLAY_SCHEMA_INVALID")
    for key in PIT_REPLAY_TRUE_KEYS:
        if pit[key] is not True:
            fail("PIT_REPLAY_DECLARATION_INVALID", key)
    for key in PIT_REPLAY_FALSE_KEYS:
        if pit[key] is not False:
            fail("PIT_REPLAY_DECLARATION_INVALID", key)
    if pit["close_adjustment"] != PIT_REPLAY_CLOSE_ADJUSTMENT:
        fail("PIT_REPLAY_DECLARATION_INVALID", "close_adjustment")
    if pit["statement"] != PIT_REPLAY_STATEMENT:
        fail("PIT_REPLAY_STATEMENT_INVALID")


def _revalidation_contract(value: dict) -> dict:
    """The free-source contract this population pinned, re-read for re-binding.

    The exclusion of BREADTH/LEADERSHIP is derived from this contract's
    ratification scope and authority flags, so re-deriving it is only meaningful
    against the *same* contract the population was built with. The pinned sha256
    is therefore compared with the on-disk file rather than assumed: a payload
    that re-signed itself over an arbitrary digest, or a checkout carrying a
    different contract, fails closed here with an attributable code instead of
    letting an unpinned "source_contract" block travel as if it had been checked.
    """
    pinned = value.get("source_contract")
    if not isinstance(pinned, dict) or pinned.get("path") != SOURCE_CONTRACT_PATH:
        fail("POPULATION_SOURCE_CONTRACT_INVALID", "path")
    if pinned.get("sha256") != file_sha256(FMD.CONTRACT_PATH):
        fail("SOURCE_CONTRACT_SHA_MISMATCH", SOURCE_CONTRACT_PATH)
    try:
        contract = FMD.load_contract(FMD.CONTRACT_PATH)
    except (FMD.FreeMarketDataError, OSError, json.JSONDecodeError) as exc:
        raise ReplayPopulationError(f"SOURCE_CONTRACT_UNREADABLE:{exc}") from exc
    if pinned.get("contract_version") != contract.get("contract_version"):
        fail("POPULATION_SOURCE_CONTRACT_INVALID", "contract_version")
    return contract


def _validate_excluded_axes(value: dict, contract: dict) -> dict:
    """The exclusion basis must be exactly what the pinned contract yields.

    ``excluded_axes`` is the one place this population states *why* it never
    populated US BREADTH or LEADERSHIP, and that statement is a derivation from
    the contract, not a free-text label. Comparing only the two axis names would
    accept a re-hashed payload that kept the names while rewriting the approval
    status, the authority flag it cites, the reason code, or the statement —
    i.e. one that reported a ratification scope the contract does not have.
    Rebuilding the basis with ``exclusion_basis`` and requiring exact equality
    closes that, and inherits its fail-closed behaviour: a contract that has
    since widened the proxies' scope or authorized US BREADTH stops validation
    here rather than letting an old population's exclusion claim stand.
    """
    declared = value.get("excluded_axes")
    if not isinstance(declared, dict) or sorted(declared) != sorted(EXCLUDED_AXES):
        fail("POPULATION_SCOPE_INVALID", "excluded_axes")
    expected = exclusion_basis(contract)
    if declared != expected:
        fail("EXCLUDED_AXIS_BASIS_NOT_DERIVED_FROM_THE_PINNED_CONTRACT")
    return expected


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


def _validate_records(
    value: dict, requested: list[str], policy: dict, excluded: dict,
) -> None:
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
        _validate_record(record, requested_date, policy, excluded)


def _validate_record(
    record: dict, requested_date: str, policy: dict, excluded: dict,
) -> None:
    """One record, re-derived from its own axes rather than read as written.

    Every field a reader would treat as a fact about the replay — the record
    status, both coverage blocks, the five-axis status, the per-axis shapes, the
    failure reason, the disclosed warnings, and the attested source dates — is
    recomputed here from the axes the record actually carries. A re-hashed
    payload is a valid signature over whatever it contains, so a derived field
    that nothing re-derives is free text: an integration probe re-signed
    ``attempted_count`` to 0 beside three observed axes, a five-axis
    ``defined_count`` to 999, and a fabricated ``failure_reason`` onto a fully
    observed record, and each was accepted with every measurement, axis row,
    source hash, and digest left genuine.
    """
    if not isinstance(record, dict) or record.get("evidence_class") != EVIDENCE_CLASS:
        fail("RECORD_EVIDENCE_CLASS_INVALID")
    status = record.get("status")
    if status not in RECORD_STATUSES:
        fail("RECORD_STATUS_INVALID")
    # The unadjusted-close convention and the "shadow, not NATURAL" scope are
    # disclosed limitations a reader relies on, exactly like ``close_adjustment``
    # in the PIT block, so they are required rather than carried unread.
    if record.get("warnings") != RECORD_WARNINGS:
        fail("RECORD_WARNINGS_INVALID", requested_date)
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
        if not isinstance(five_axis, dict) or sorted(five_axis) != sorted(FIVE_AXIS_KEYS):
            fail("RECORD_FIVE_AXIS_INVALID", requested_date)
        axes = five_axis.get("axes")
        if not isinstance(axes, dict) or sorted(axes) != sorted(PRR.AXES):
            fail("RECORD_AXIS_SET_INVALID", requested_date)
        # The one substantive guarantee of this slice: a US BREADTH or
        # LEADERSHIP value must never appear in this population, whatever else
        # a record carries. The per-record reason must also be the population's
        # own contract-derived exclusion code, so a record cannot keep the
        # UNKNOWN status while attributing it to some other, unratified cause.
        for name in EXCLUDED_AXES:
            entry = axes.get(name)
            if (
                not isinstance(entry, dict)
                or sorted(entry) != sorted(AXIS_ENTRY_KEYS)
                or entry.get("status") != "UNKNOWN"
                or entry.get("measurement") is not None
            ):
                fail("EXCLUDED_AXIS_MUST_STAY_UNKNOWN", name)
            if entry.get("reason") != excluded[name]["reason_code"]:
                fail("EXCLUDED_AXIS_REASON_NOT_DERIVED_FROM_THE_PINNED_CONTRACT", name)
        for name in REPLAYED_AXES:
            entry = axes.get(name)
            if (
                not isinstance(entry, dict)
                or sorted(entry) != sorted(AXIS_ENTRY_KEYS)
                or entry.get("status") not in ("OBSERVED", "NOT_COMPUTABLE")
            ):
                fail("REPLAYED_AXIS_STATUS_INVALID", name)
            # An axis entry must be shaped like the outcome it claims. An
            # ``OBSERVED`` axis that also carried a reason would read as an
            # observation *and* a failure at once, and a ``NOT_COMPUTABLE`` one
            # that retained a measurement would be silently skipped by every
            # derived field built from "the observed axes" while
            # ``_validate_candidate_is_derived_from_its_evidence`` still read its
            # session date. Whether an ``OBSERVED`` axis carries a usable
            # measurement is settled by re-deriving its row below, which reports
            # the richer failure.
            if entry["status"] == "OBSERVED":
                if entry.get("reason") is not None:
                    fail("OBSERVED_AXIS_MUST_NOT_CARRY_A_REASON", f"{requested_date}:{name}")
            elif entry.get("measurement") is not None:
                fail(
                    "NOT_COMPUTABLE_AXIS_MUST_NOT_CARRY_A_MEASUREMENT",
                    f"{requested_date}:{name}",
                )
            elif not (isinstance(entry.get("reason"), str) and entry["reason"]):
                fail("NOT_COMPUTABLE_AXIS_MUST_BE_ATTRIBUTED", f"{requested_date}:{name}")
        observed = [
            name for name in REPLAYED_AXES if axes[name].get("status") == "OBSERVED"
        ]
        not_computable = [name for name in REPLAYED_AXES if name not in observed]

    # Coverage and status are recomputed from the axes themselves, so a payload
    # cannot report a coverage ratio or a record status the axes do not support.
    coverage = record.get("free_axis_coverage")
    if not isinstance(coverage, dict):
        fail("RECORD_COVERAGE_MISSING", requested_date)
    # ``attempted_count`` is checked against the whole block, not on its own:
    # it is the denominator a reader divides ``observed_count`` by, so an
    # attempted count of 0 beside three observed axes reports a replay that never
    # ran and still produced evidence. A record that carries an axis packet
    # attempted every replayed axis by construction; one that carries none either
    # never got past its own requested date (0) or attempted them all and then
    # failed the date closed for lookahead.
    expected_attempted = (
        len(REPLAYED_AXES)
        if five_axis is not None
        or record.get("failure_reason") == LOOKAHEAD_BLOCKED_REASON
        else 0
    )
    if coverage != _free_axis_coverage(observed, not_computable, expected_attempted):
        fail("RECORD_COVERAGE_INCONSISTENT", requested_date)
    if status != _status_for(observed):
        fail("RECORD_STATUS_INCONSISTENT_WITH_COVERAGE", requested_date)
    # The five-axis packet publishes its own status and coverage over the same
    # axes, and both are derived. Checking only the axis *set* above left them
    # free text: a re-signed ``defined_count`` of 999, a ``missing_axes`` list
    # that omits the excluded pair, or an "observed" status on a packet that
    # observed nothing all travelled intact.
    if five_axis is not None and five_axis != _five_axis_block(
        observed, not_computable, axes,
    ):
        fail("RECORD_FIVE_AXIS_NOT_DERIVED_FROM_ITS_AXES", requested_date)
    _validate_failure_reason(record, five_axis, observed, requested_date)
    if five_axis is None and record.get("effective_session_date") is not None:
        # With no axis packet there is no TREND measurement to bind it to, so
        # ``_validate_candidate_is_derived_from_its_evidence`` never reaches it.
        fail("EFFECTIVE_SESSION_DATE_NOT_DERIVED_FROM_ITS_EVIDENCE", requested_date)

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
    _validate_fred_vintage_binding(axes, observed, requested_date)
    _validate_measurement_source_dates(axes, observed, requested_date)
    _validate_no_lookahead(record, requested_date)
    _validate_attestation_is_derived_from_its_evidence(
        record, five_axis, axes, observed, requested_date,
    )


def _validate_failure_reason(
    record: dict, five_axis: object, observed: list[str], requested_date: str,
) -> None:
    """A record's failure reason must match what actually happened to its axes.

    Left unchecked, this field is free text under a valid signature in both
    directions. A fully observed record could carry a fabricated reason a reader
    would take as the cause of a failure that never occurred, and a date that
    observed nothing could drop its reason and become an unattributed blank —
    which is exactly the "BLOCKED with no recorded cause" shape downstream
    evidence can only summarize as an unexplained UNKNOWN.

    The three cases are the three the builders produce: an axis survived, so
    there is no failure; every attempted axis failed, so the reason is the fixed
    record-level code (the attribution lives in the per-axis reasons); or the
    date produced no axis packet at all, so it carries its own attributable
    reason string.
    """
    failure_reason = record.get("failure_reason")
    if observed:
        if failure_reason is not None:
            fail("REPLAYED_RECORD_MUST_NOT_CARRY_A_FAILURE", requested_date)
    elif five_axis is not None:
        if failure_reason != ALL_AXES_NOT_COMPUTABLE_REASON:
            fail("BLOCKED_RECORD_MUST_BE_ATTRIBUTED", requested_date)
    elif not isinstance(failure_reason, str) or not failure_reason:
        fail("BLOCKED_RECORD_MUST_BE_ATTRIBUTED", requested_date)


def _validate_attestation_is_derived_from_its_evidence(
    record: dict, five_axis: object, axes: dict, observed: list[str], requested_date: str,
) -> None:
    """The attested source dates must be the ones this record's axes carry.

    ``_validate_no_lookahead`` re-checks that every date the attestation *names*
    is at or before the requested date, which is a bound on the listed dates and
    not on the list. A re-signed payload could therefore shorten the list —
    dropping the liquidity observation dates, blanking the VIX observation date,
    or emptying the trend session range — and the walk would simply have less to
    reject while the record still published a "no lookahead" claim over sources it
    no longer named. Rebuilding the block from the observed measurements
    themselves, with the same helper that produced it, closes that in both
    directions: a date the record did not consult cannot be added either.
    """
    def measurement(name: str) -> dict | None:
        entry = axes.get(name) if isinstance(axes, dict) else None
        if name not in observed or not isinstance(entry, dict):
            return None
        value = entry.get("measurement")
        return value if isinstance(value, dict) else None

    liquidity = measurement("LIQUIDITY")
    series = liquidity.get("series") if isinstance(liquidity, dict) else None
    try:
        liquidity_dates = sorted({
            date
            for row in (series if isinstance(series, list) else [])
            for date in (row["previous_observation_date"], row["observation_date"])
        })
    except (KeyError, TypeError) as exc:
        raise ReplayPopulationError(
            f"OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT:{requested_date}:LIQUIDITY"
        ) from exc
    expected = _no_lookahead_attestation(
        requested_date,
        measurement("TREND"),
        measurement("RISK_VOL"),
        liquidity,
        liquidity_dates,
        replayed=five_axis is not None,
    )
    if record.get("no_lookahead_attestation") != expected:
        fail("RECORD_ATTESTATION_NOT_DERIVED_FROM_ITS_EVIDENCE", requested_date)


def _validate_fred_vintage_binding(
    axes: dict, observed: list[str], requested_date: str,
) -> None:
    """Every observed FRED measurement must carry a vintage that covers its date.

    ``_validate_no_lookahead`` walks the record's *attestation* and effective
    session date, and never reaches inside a measurement. A vintage window could
    not be checked by that rule anyway: a still-current FRED value legitimately
    reports ``realtime_end`` as ``9999-12-31``, which is why
    ``_validate_measurement_source_dates`` — the walk that does reach inside a
    measurement — exempts the vintage *end* keys and leaves them to this
    containment bind. The vintage a measurement was actually served at therefore
    needs its own bind, and without one a WRESBAL, TOTBKCR,
    or VIXCLS row whose ALFRED window opens *after* the replayed date is accepted
    as if it had been knowable then — with the axis row, the source hashes, the
    coverage, and the payload signature all internally consistent.

    The same containment rule the fetchers apply is re-applied here rather than
    trusted, over every vintage that entered the measurement: the latest and
    previous liquidity observations (the change is a difference of the two) and
    the series metadata (which fixes the units and hence the normalization
    factor). ``vintage_date`` is bound to the requested date separately, because
    it is otherwise a free-standing claim about which vintage was requested.
    """
    for name in ("RISK_VOL", "LIQUIDITY"):
        if name not in observed:
            continue
        entry = axes.get(name)
        measurement = entry.get("measurement") if isinstance(entry, dict) else None
        if not isinstance(measurement, dict):
            fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", f"{requested_date}:{name}")
        if measurement.get("vintage_date") != requested_date:
            fail("FRED_VINTAGE_NOT_BOUND_TO_THE_REQUESTED_DATE", f"{requested_date}:{name}")
        if name == "RISK_VOL":
            _assert_vintage_covers(requested_date, measurement, f"{requested_date}:RISK_VOL")
            continue
        series = measurement.get("series")
        if not isinstance(series, list) or not series:
            fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", f"{requested_date}:LIQUIDITY")
        for row in series:
            series_id = row.get("series_id") if isinstance(row, dict) else None
            label = f"{requested_date}:LIQUIDITY.{series_id}"
            _assert_vintage_covers(requested_date, row, label)
            _assert_vintage_covers(
                requested_date, row, f"{label}.previous", FRED_PREVIOUS_VINTAGE_KEYS,
            )
            _assert_vintage_covers(
                requested_date, row, f"{label}.metadata", FRED_METADATA_VINTAGE_KEYS,
            )


def _measurement_dates(value: object, label: str) -> list[dt.date]:
    """Every date-shaped string inside a measurement, as a real calendar date.

    Generic, so a measurement field added later is bound automatically instead
    of escaping the walk. The FRED vintage *end* keys are the one exemption:
    a still-current value legitimately reports ``9999-12-31``, and those bounds
    are checked as containment windows by ``_assert_vintage_covers`` rather than
    as backward-looking source dates.
    """
    if isinstance(value, dict):
        return [
            item
            for key, entry in value.items()
            if key not in VINTAGE_END_KEYS
            for item in _measurement_dates(entry, label)
        ]
    if isinstance(value, list):
        return [item for entry in value for item in _measurement_dates(entry, label)]
    return _dates_in(value, label)


def _validate_measurement_source_dates(
    axes: dict, observed: list[str], requested_date: str,
) -> None:
    """Every date inside an observed measurement is a real day, at or before it.

    ``_validate_no_lookahead`` walks the record's *attestation*, which carries
    only a summary of the dates consumed, and ``_validate_fred_vintage_binding``
    reaches the ALFRED windows but nothing else. The measurements themselves
    carry the observation, previous-observation, and Alpaca session dates each
    axis row was actually built from, and those were bound by neither. A
    re-signed record could therefore carry a RISK_VOL ``observation_date`` of
    ``2026-02-31`` — a day no calendar has — or a trend session date after the
    replayed date, while the attestation stayed clean and every hash, row
    re-derivation, and signature stayed internally consistent.
    """
    if not observed:
        return
    anchor = _calendar_date(requested_date)
    if anchor is None:
        # An observed measurement filed under a date that is not a real day
        # cannot be bound to anything, so it fails closed rather than skipping.
        fail("REQUESTED_DATE_CALENDAR_INVALID", requested_date)
    for name in observed:
        entry = axes.get(name)
        measurement = entry.get("measurement") if isinstance(entry, dict) else None
        if not isinstance(measurement, dict):
            fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", f"{requested_date}:{name}")
        label = f"{requested_date}:{name}"
        if any(date > anchor for date in _measurement_dates(measurement, label)):
            fail("US_REPLAY_LOOKAHEAD_VIOLATION", label)


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
    anchor = _calendar_date(requested_date)
    if anchor is None:
        # A malformed or calendar-impossible requested date is itself a
        # legitimate BLOCKED record — ``_parse_requested_date`` produces exactly
        # that — so there is no calendar anchor to compare against here.
        return
    consulted = _dates_in(attestation, requested_date) + _dates_in(
        record.get("effective_session_date"), requested_date,
    )
    if any(date > anchor for date in consulted):
        fail("RECORD_LOOKAHEAD_VIOLATION", requested_date)


def _dates_in(value: object, label: str) -> list[dt.date]:
    """Every ISO-date-shaped string reachable inside ``value``, as a real date.

    A date-shaped string that no calendar can produce fails the record closed
    rather than being compared. String comparison would have cleared it: an
    attested source date of ``2026-02-31`` sorts before ``2026-03-01`` and so
    reads as backward-looking, which is how a re-signed payload could attest to
    a source date that never existed and still satisfy this walk.
    """
    if isinstance(value, str):
        if DATE10.fullmatch(value) is None:
            return []
        parsed = _calendar_date(value)
        if parsed is None:
            fail("RECORD_SOURCE_DATE_CALENDAR_INVALID", f"{label}:{value}")
        return [parsed]
    if isinstance(value, dict):
        return [item for entry in value.values() for item in _dates_in(entry, label)]
    if isinstance(value, list):
        return [item for entry in value for item in _dates_in(entry, label)]
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
