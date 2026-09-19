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

Where a date's sources come from is a declaration, not an assumption:

* ``SOURCE_MODE_API`` (the default, and unchanged) issues live, per-date
  Alpaca/FRED requests.
* ``SOURCE_MODE_EVIDENCE`` answers those same requests from the append-only,
  content-addressed capture already committed under
  ``evidence/free_market_data/history/``, via ``regime/us_replay_evidence_source.py``.
  Only the ``getter`` differs: every fetch, bind, axis derivation and record
  field below is the same code, so an evidence-read date is not a second
  implementation of a score. That store is read and never written.
* Which mode ran is published in ``pit_source`` and re-required by
  ``validate_population``, because the two modes do not carry the same
  guarantees: the committed store holds one FRED series-metadata capture per
  series rather than one per vintage, so evidence mode withholds the units
  normalization instead of substituting a capture-time units string into an
  earlier date; it derives the units in effect on each date from the store's own
  re-publication scale instead (see ``UNITS_VINTAGE_DERIVED_STATUS``), and it
  lags the VIX vintage one calendar day to match what the live producer could
  actually obtain (see ``FRED_VINTAGE_LAG_DAYS``). An evidence-read population
  re-signed as a live-provider one would be claiming a units-vintage bind it
  never had, so that swap fails closed.

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

# CIO US-DATA-1/U1 (2026-09-14): ``breadth_axis_row``/``leadership_axis_row``
# and their fetch ``replay_breadth_leadership_source`` add BREADTH/LEADERSHIP
# arithmetic to this module, byte-identical to
# ``regime/paper_regime_reference.py::build_us``. ``REPLAYED_AXES`` and
# ``EXCLUDED_AXES`` above are kept exactly as they were before this wiring --
# they now name the ``RATIFIED_CURRENT_REFERENCE_ONLY`` *default* rather than
# runtime truth. ``PREPARED_NOT_WIRED_AXES`` is kept for the module's own
# history/tests; it no longer describes the code path (see
# ``authorized_axes`` below), which now actually attempts BREADTH/LEADERSHIP
# once ``HISTORICAL_PIT_REPLAY_IDENTITY_PATH`` says so.
PREPARED_NOT_WIRED_AXES = ["BREADTH", "LEADERSHIP"]

# CIO plan U2 (2026-09-13/14): the ratified identity that widens the source
# scope from current-reference-only to PAPER PIT replay. Landing this
# identity is a CIO technical decision
# (``CIO-REGIME-PATH-AND-REDESIGN-START-20260914``, ``US`` -> ``U2_identity``)
# this module never makes on its own -- it only recognizes the identity once
# ``config/us_historical_pit_replay_identity_v1.json`` (a dedicated file, not
# a field inside ``config/free_market_data_contract.json`` -- see
# ``_load_historical_pit_replay_identity``'s docstring for why) carries it,
# hash-bound to that decision record so a re-signed file cannot claim the
# identity by merely restating the status string. ``authority.
# us_breadth_authorized`` is deliberately NOT the gate: it stays ``false``
# permanently because other contract consumers depend on it staying false, so
# authorization here is keyed entirely off that dedicated identity file
# instead.
RATIFIED_HISTORICAL_PIT_REPLAY_STATUS = "US_ETF_PROXY_HISTORICAL_PIT_SCOPE_V1"
RATIFIED_HISTORICAL_PIT_REPLAY_DECISION_ID = (
    "CIO-REGIME-PATH-AND-REDESIGN-START-20260914"
)
# sha256 of the external CIO decision record that adopted the identity above
# (outside this repository checkout, so it is a compile-time anchor here
# rather than a runtime file read -- this module's own tests, and CI, never
# depend on that external file existing).
RATIFIED_HISTORICAL_PIT_REPLAY_DECISION_SHA256 = (
    "47819e1078f3ec8450e6c4aadfa31233ee5784ebe102904034f2c22fc1cdbaa6"
)

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
# The one additional key a record carries once BREADTH/LEADERSHIP are
# authorized: both axes are measured from the same combined Alpaca fetch (see
# ``replay_breadth_leadership_source``), so they share one response hash
# rather than each carrying its own. Absent under the current 3-axis
# authorization, so ``SOURCE_HASH_KEYS``-shaped records stay byte-identical.
BREADTH_LEADERSHIP_RESPONSE_HASH_KEY = "breadth_leadership_response_sha256"
LIQUIDITY_RESPONSE_HASH_KEYS = (
    "metadata_response_sha256", "observations_response_sha256",
)
AXIS_RESPONSE_HASH_KEY = {
    "TREND": "trend_response_sha256",
    "RISK_VOL": "risk_vol_response_sha256",
    "LIQUIDITY": "liquidity_response_hashes",
    "BREADTH": BREADTH_LEADERSHIP_RESPONSE_HASH_KEY,
    "LEADERSHIP": BREADTH_LEADERSHIP_RESPONSE_HASH_KEY,
}


def _source_hash_keys(replayed: list[str]) -> tuple[str, ...]:
    """The exact source-hash keys a record carries for this replay's scope."""
    keys = list(SOURCE_HASH_KEYS)
    if "BREADTH" in replayed:
        keys.append(BREADTH_LEADERSHIP_RESPONSE_HASH_KEY)
    return tuple(keys)

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

# The candidate rule can only classify a full 5/5 axis set. Whenever coverage
# is partial -- BREADTH/LEADERSHIP excluded by ratification scope, or simply
# not observed on a given date even when authorized -- the honest
# normalization outcome is "not computable", never a NEUTRAL stand-in.
CLASSIFICATION_STATUS = "NOT_COMPUTABLE_PARTIAL_AXIS_COVERAGE"
# The live rule's own status (``PRR.market_packet``) for a genuine 5/5
# classification -- reused verbatim rather than invented, exactly like every
# other threshold/label this module mirrors from ``paper_regime_reference.py``.
CLASSIFICATION_STATUS_CLASSIFIED = "PAPER_REFERENCE_CLASSIFIED"

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

# ---------------------------------------------------------------------------
# Where a replayed date's sources are read from.
# ---------------------------------------------------------------------------
#
# ``LIVE_PROVIDER_API`` is the original and still the default: every axis is
# rebuilt from live, per-date Alpaca/FRED requests. It is unchanged by the
# evidence mode below -- same requests, same binds, same record shape, same
# warnings -- so every existing caller keeps behaving exactly as before.
#
# ``COMMITTED_EVIDENCE_HISTORY_STORE`` rebuilds the same dates from the
# append-only, content-addressed capture already committed under
# ``evidence/free_market_data/history/``. That store is read, never written, by
# ``regime/us_replay_evidence_source.py``, which answers the *same* request URLs
# this module already issues -- so nothing about the derivation, the axis
# arithmetic, the lookahead binds, or the record shape is re-implemented for it.
#
# Which mode produced a population is recorded in its ``pit_source`` block and
# re-required by ``validate_population``: an evidence-read population can never
# be mistaken for a live-provider one, in either direction.
SOURCE_MODE_API = "LIVE_PROVIDER_API"
SOURCE_MODE_EVIDENCE = "COMMITTED_EVIDENCE_HISTORY_STORE"
SOURCE_MODES = (SOURCE_MODE_API, SOURCE_MODE_EVIDENCE)
PIT_SOURCE_KEYS = ("mode", "store", "statement", "units_vintage_available")

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
# The evidence-read statement is the API one plus the two facts that only apply
# when the sources came off disk. The API statement is left byte-identical so an
# existing live-provider population still validates unchanged.
PIT_REPLAY_STATEMENT_EVIDENCE = PIT_REPLAY_STATEMENT + (
    " Sources were read from the committed, content-addressed"
    " evidence/free_market_data/history/ store rather than requested live; FRED"
    " observations were resolved through observations_available_at, so only rows"
    " already published on the replayed date were visible. The store holds one"
    " FRED series-metadata capture per series rather than one per vintage, so no"
    " units normalization is read from it: each liquidity row's factor is"
    " derived from the store's own re-publication scale and carries a"
    " units_vintage disclosure instead of a metadata vintage window. The VIX"
    " vintage is resolved one calendar day before the replayed date, matching"
    " what the live producer could actually obtain, because ALFRED backdates a"
    " VIXCLS row's availability to its observation date while the live"
    " observations endpoint had not yet published it."
)


def pit_replay_statement(source_mode: str) -> str:
    if source_mode == SOURCE_MODE_EVIDENCE:
        return PIT_REPLAY_STATEMENT_EVIDENCE
    return PIT_REPLAY_STATEMENT

RAW_RETENTION = "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED"
RECORD_WARNINGS = [
    "FREE_IEX_REPRESENTATIVE_ETF_REFERENCE",
    "NOT_FULL_US_SECURITY_LEVEL_BREADTH",
    "RAW_UNADJUSTED_CLOSES_MIRROR_PRODUCTION_COLLECTOR_CONVENTION",
    "SHADOW_HISTORICAL_BACKFILL_NOT_NATURAL_OBSERVATION",
    "REGIME_INTERPRETATION_UNAUTHORIZED",
]

# The one point-in-time fact the committed store cannot supply, stated as a
# limitation rather than papered over.
#
# FRED series *metadata* fixes the units string and hence the normalization
# factor, and it is itself vintaged: WRESBAL was rescaled billions -> millions
# on 2025-11-13. The committed history store holds exactly one metadata capture
# per series, taken at capture time, so for any replayed date before that
# capture the units in effect *on that date* are simply not in the evidence.
#
# Substituting the capture-time units for a historical date would be using a
# later revision as if it had been knowable -- the exact thing this module
# exists to prevent -- so evidence mode does not do it. It applies no
# normalization at all (factor 1), records the observation values on the
# vintage's own native scale, publishes no units claim it cannot support, and
# says so in the row and in the record warnings.
#
# The consequence is bounded and stated: ``liquidity_axis_row`` reads only the
# *sign* of each series' change, both sides of that difference come from the
# same vintage and therefore the same scale, and every unit factor in
# ``FMD.FRED_LIQUIDITY_UNITS`` is strictly positive -- so the axis direction is
# invariant under the missing factor, while the recorded magnitudes are native
# rather than normalized. ``test_us_historical_replay_population.py`` pins that
# invariance instead of asserting it here.
# The per-axis FRED vintage lag, and why it is asymmetric.
#
# CIO decision 2026-09-20. The operational US producer does not read both FRED
# axes at the same vintage, and the replay must not either. Two independent
# reconstructions agree on the asymmetry: the sealed pre-registration executor
# reverse-engineered the live producer as "VIX at the previous calendar day's
# vintage, liquidity at the same day's vintage" (matching that asymmetry moved
# the published-record agreement from 1/13 to 13/13), and this module's own
# evidence replay independently showed its VIX running exactly one observation
# step fresher than every published packet.
#
# The cause is ALFRED, not the collector: ALFRED backdates a VIXCLS row's
# ``realtime_start`` to the observation date, while the live FRED observations
# endpoint had not yet published that day's value when the packet was built. So
# resolving VIX at the replayed date's own vintage hands the replay a value that
# could not be obtained at decision time. That is future information -- quiet,
# but future information -- and it is removed here rather than disclosed.
#
# The liquidity series keep the same-day vintage, because that is what the live
# producer does: WRESBAL and TOTBKCR are weekly and publish with their own
# release lag already inside the row, so a further calendar-day lag would be a
# second, invented delay. The asymmetry is the operational behaviour; flattening
# it in either direction is a change to the rule, and ``validate_population``
# refuses a record that declares it flat.
#
# API mode is untouched: it asks the provider for the replayed date's vintage and
# the provider answers with whatever it has actually published, which is the
# live path's own business.
FRED_VINTAGE_LAG_DAYS = {
    SOURCE_MODE_API: {"RISK_VOL": 0, "LIQUIDITY": 0},
    SOURCE_MODE_EVIDENCE: {"RISK_VOL": 1, "LIQUIDITY": 0},
}


def fred_vintage_lag_days(source_mode: str, axis: str) -> int:
    return FRED_VINTAGE_LAG_DAYS[_check_source_mode(source_mode)][axis]


# How the liquidity units vintage is settled when the sources come off disk.
#
# A FRED series' units string fixes the normalization factor and is itself
# vintaged: WRESBAL was rescaled billions -> millions on 2025-11-13. The
# committed store holds exactly one metadata capture per series, at capture time,
# so the capture-time units string is *not* the units of an earlier replayed date
# and applying it anyway would be the later-revision substitution this module
# exists to prevent.
#
# It is not, however, unknowable. A units rescale leaves a signature no ordinary
# revision does -- ALFRED re-publishes the whole history and every re-published
# observation moves by the *same exact power of ten* --
# so ``regime/us_replay_evidence_source.py`` derives the rescale timeline from the
# committed rows and resolves the factor that was in effect on each replayed
# date. Only the unit *scale* is recovered that way; no observation value ever
# crosses a vintage boundary.
#
# What lands in a record is therefore a normalized value on the same
# "Millions of U.S. Dollars" scale the production capture publishes, plus the
# factor that was applied and where it came from. The rescale boundaries
# themselves stay out of the record: an ``effective_from`` is later than most
# replayed dates, and a date inside a measurement is bound as a consumed source
# date. They live in the population's ``pit_source.store`` instead, and
# ``validate_population`` re-derives each row's factor from that declared
# timeline rather than reading the factor the row wrote.
#
# The disclosed limitation: a rescale published in the same revision as a data
# change would not be an exact power of ten and would not be detected. If that
# ever happened the factor would be the neighbouring vintage's, which
# ``liquidity_axis_row`` cannot notice -- it reads only the sign of each change,
# both sides of that difference share one vintage and therefore one scale, and
# every factor in ``FMD.FRED_LIQUIDITY_UNITS`` is positive, so an axis direction
# is invariant under any such error while a magnitude is not.
# ``test_us_historical_replay_population.py`` pins that invariance.
UNITS_VINTAGE_DERIVED_STATUS = "DERIVED_FROM_THE_COMMITTED_REPUBLICATION_SCALE"
UNITS_VINTAGE_DERIVED_STATEMENT = (
    "The committed evidence store holds one FRED series-metadata capture per"
    " series rather than one per vintage, so the units in effect on this"
    " replayed date are not read from that capture -- doing so would be a later"
    " revision leaking backwards. They are derived instead from the store's own"
    " re-publication scale: a units rescale moves every re-published observation"
    " by the same exact power of ten, which an ordinary data revision does not,"
    " so the rescale timeline and the factor in effect on this date follow from"
    " the committed rows. The rescale boundaries are published in the"
    " population's pit_source.store and this factor is re-derived from them at"
    " validation time. Only the unit scale is recovered this way; no observation"
    " value crosses a vintage boundary."
)
UNITS_VINTAGE_DERIVATION = "REPUBLISHED_SAME_OBSERVATION_DIFFERS_BY_AN_EXACT_POWER_OF_TEN"
UNITS_VINTAGE_KEYS = (
    "derivation", "normalization_factor", "rescale_events_undone", "statement",
    "status",
)
EVIDENCE_RECORD_WARNINGS = RECORD_WARNINGS + [
    "SOURCES_READ_FROM_COMMITTED_EVIDENCE_STORE_NOT_LIVE_PROVIDER",
    "FRED_UNITS_VINTAGE_DERIVED_FROM_THE_COMMITTED_REPUBLICATION_SCALE",
    "RISK_VOL_VINTAGE_LAGGED_ONE_CALENDAR_DAY_TO_MATCH_THE_LIVE_PRODUCER",
]


def _derived_units_scale_at(
    series_scale: dict, as_of_date: str, label: str,
) -> tuple[Decimal, int]:
    """The normalization factor in effect on ``as_of_date``, from the timeline.

    Undoes every rescale the committed store shows taking effect *after* that
    date, so the factor is the one the series actually carried then. The one
    implementation of this arithmetic: ``replay_liquidity_source`` applies it and
    ``_validate_fred_vintage_binding`` re-derives it from the population's own
    declared timeline, so a row cannot carry a factor the timeline does not
    yield.

    Fails closed rather than applying a scale the production normalization could
    not have produced: the result must be a factor
    ``FMD.FRED_LIQUIDITY_UNITS`` actually contains.
    """
    events = series_scale.get("rescale_events")
    factor_text = series_scale.get("capture_normalization_factor")
    if not isinstance(events, list) or not isinstance(factor_text, str):
        fail("UNITS_SCALE_INVALID", label)
    try:
        factor = Decimal(factor_text)
    except ArithmeticError:
        fail("UNITS_SCALE_INVALID", f"{label}.capture_normalization_factor")
    undone = 0
    for event in events:
        effective_from = event.get("effective_from") if isinstance(event, dict) else None
        exponent = event.get("power_of_ten") if isinstance(event, dict) else None
        if _calendar_date(effective_from) is None or not isinstance(exponent, int):
            fail("UNITS_SCALE_INVALID", f"{label}.rescale_events")
        if str(effective_from) > str(as_of_date):
            factor *= Decimal(10) ** exponent
            undone += 1
    if factor not in {value[1] for value in FMD.FRED_LIQUIDITY_UNITS.values()}:
        fail("UNITS_SCALE_FACTOR_UNSUPPORTED", f"{label}:{factor}")
    return factor, undone


def units_vintage_block(factor: object, rescale_events_undone: int) -> dict:
    """The units disclosure one evidence-read liquidity row carries.

    Deliberately a factor, a code and a *count* -- never the rescale boundaries
    themselves. A boundary's ``effective_from`` is later than the dates it
    applies to, and ``_validate_measurement_source_dates`` binds every date
    inside a measurement as a consumed source date, so carrying one here would
    read as a lookahead. The boundaries live in the population's
    ``pit_source.store`` and the validator re-derives both the factor and this
    count from them.
    """
    return {
        "status": UNITS_VINTAGE_DERIVED_STATUS,
        "normalization_factor": str(factor),
        "derivation": UNITS_VINTAGE_DERIVATION,
        "rescale_events_undone": int(rescale_events_undone),
        "statement": UNITS_VINTAGE_DERIVED_STATEMENT,
    }


PIT_SOURCE_STATEMENT = {
    SOURCE_MODE_API: (
        "Every axis observation was rebuilt from live, per-date Alpaca/FRED"
        " requests issued at replay time."
    ),
    SOURCE_MODE_EVIDENCE: (
        "Every axis observation was rebuilt from the append-only,"
        " content-addressed capture committed under"
        " evidence/free_market_data/history/, read and never written. FRED"
        " observations are resolved through"
        " collectors/free_market_data_history.py::observations_available_at, so"
        " only rows already published on the replayed date are visible and a"
        " later revision cannot leak backwards. The VIX vintage is lagged one"
        " calendar day and the liquidity vintage is not, matching the live"
        " producer; see FRED_VINTAGE_LAG_DAYS. The per-vintage FRED units string"
        " is not in the store and is derived from its own re-publication scale;"
        " see each liquidity row's units_vintage block."
    ),
}


def record_warnings(source_mode: str) -> list[str]:
    """The disclosed limitations a record carries under this source mode.

    Mode-derived rather than a single global: reading the committed store adds
    two limitations a live-provider replay does not have, and a reader must not
    have to infer either of them from the population's mode field.
    """
    if source_mode == SOURCE_MODE_EVIDENCE:
        return list(EVIDENCE_RECORD_WARNINGS)
    return list(RECORD_WARNINGS)


def _check_source_mode(source_mode: object) -> str:
    if source_mode not in SOURCE_MODES:
        fail("SOURCE_MODE_INVALID", str(source_mode))
    return str(source_mode)


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


def breadth_axis_row(advance_fraction: object) -> dict:
    """Line-for-line mirror of ``build_us``'s BREADTH row (14-ETF advance
    fraction). Prepared, not yet wired -- see ``PREPARED_NOT_WIRED_AXES``.
    The threshold literals are ``build_us``'s current policy-derived values,
    hardcoded exactly like ``trend_axis_row``'s; a future policy change is
    caught by the parity test, not silently re-derived here.
    """
    value = PRR.decimal(advance_fraction, "US_BREADTH_INVALID")
    direction = PRR.ratio_direction(value, Decimal("0.55"), Decimal("0.45"))
    return PRR.axis(
        "BREADTH",
        direction,
        {"advance_fraction": str(value)},
        f"대표 ETF 중 상승 비중은 {value * 100:.1f}%입니다.",
    )


def leadership_axis_row(ordered_groups: list[dict]) -> dict:
    """Line-for-line mirror of ``build_us``'s LEADERSHIP row (12-group
    20-session positive fraction). Prepared, not yet wired -- see
    ``PREPARED_NOT_WIRED_AXES``.
    """
    if not isinstance(ordered_groups, list) or len(ordered_groups) != 12:
        fail("US_LEADERSHIP_COVERAGE_INCOMPLETE")
    positive_groups = sum(
        PRR.decimal(row.get("return_pct"), "US_LEADERSHIP_INVALID") > 0
        for row in ordered_groups
    )
    fraction = Decimal(positive_groups) / Decimal(len(ordered_groups))
    direction = PRR.ratio_direction(fraction, Decimal("0.666667"), Decimal("0.333333"))
    return PRR.axis(
        "LEADERSHIP",
        direction,
        {"positive_groups": positive_groups, "total": 12},
        f"대표 업종 12개 중 {positive_groups}개가 20거래일 기준 상승입니다.",
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


def _grouped_sessions(
    normalized: list[dict], anchor: dt.date, *, invalid_code: str, lookahead_label: str,
) -> dict[str, list[dict]]:
    """Group normalized Alpaca bars by symbol, failing closed on any lookahead.

    Shared by ``replay_trend_source`` and ``replay_breadth_leadership_source``
    so the exact same session-date parsing and no-lookahead check governs
    every bar this module ever consumes, whichever fetch it came from.
    """
    grouped: dict[str, list[dict]] = {}
    for row in normalized:
        # Parsed, not shape-matched: a bar timestamped ``2026-02-31`` is not a
        # session, and comparing that string against the anchor would admit it
        # as an ordinary earlier one.
        session = _calendar_date(str(row.get("opened_at", ""))[:10])
        if session is None:
            fail(invalid_code)
        # A provider that answers with a later bar than requested must fail
        # this date closed rather than have the bar silently trimmed.
        if session > anchor:
            fail("US_REPLAY_LOOKAHEAD_VIOLATION", lookahead_label)
        grouped.setdefault(row["symbol"], []).append(
            {**row, "session_date": session.isoformat()}
        )
    return grouped


def _derive_trend_etfs(
    grouped: dict[str, list[dict]], symbols: list[str], windows: list[int],
) -> list[dict]:
    """The per-symbol trend-ETF rows ``trend_axis_row`` consumes.

    Shared by ``replay_trend_source`` (its own narrower 3-symbol fetch) and
    ``replay_breadth_leadership_source`` (the wider union fetch, restricted to
    the trend symbols) so both ever compute this the same way from whichever
    bars they were given.
    """
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
    return trend_etfs


def replay_trend_source(
    alpaca_key: str, alpaca_secret: str, anchor: dt.date, *, getter, contract: dict,
) -> dict:
    """Rebuild the trend-ETF observations available as of ``anchor``.

    Its own narrow 3-symbol fetch: used only when BREADTH/LEADERSHIP are not
    authorized for this replay. Once they are, TREND is derived from
    ``replay_breadth_leadership_source``'s wider union fetch instead (see
    ``replay_one_requested_date``), so a leadership/breadth session ending on
    a different day than the trend ETFs' is structurally impossible rather
    than merely checked after the fact.
    """
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
    grouped = _grouped_sessions(
        normalized, anchor, invalid_code="US_TREND_SESSION_DATE_INVALID",
        lookahead_label="ALPACA_BAR",
    )
    trend_etfs = _derive_trend_etfs(grouped, symbols, windows)

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


PROXY_MIXED_SESSION_GENERATION = "US_PROXY_MIXED_SESSION_GENERATION"


def _assert_proxy_session_alignment(measurement: object, label: str) -> None:
    """Every session date a combined TREND/BREADTH/LEADERSHIP measurement names
    must be the one effective session it claims.

    The union fetch makes the *trend* session single by construction, but not
    the proxy groups: ``FMD.derive_us_market_reference`` stamps LEADERSHIP's
    top-level ``as_of_session_date`` from the trend ETFs alone, while each
    ``ordered_groups`` row carries its own symbol's last bar. A sector ETF that
    is in LEADERSHIP but not in BREADTH (SMH) whose latest bar is missing
    therefore still produced an ``OBSERVED`` LEADERSHIP axis mixing that
    symbol's previous session into the effective one. Mirrors
    ``regime/us_paper_runtime.py::_session_axis``'s ``_MIXED_SESSION_GENERATION``
    check: the builder applies it right after the combined fetch and the
    record validator re-applies it to the stored measurement, so neither a
    replay nor a re-signed record can carry a mixed session generation.
    """
    if not isinstance(measurement, dict):
        fail(PROXY_MIXED_SESSION_GENERATION, label)
    effective = measurement.get("as_of_session_date")
    if _calendar_date(effective) is None:
        fail(PROXY_MIXED_SESSION_GENERATION, label)
    breadth = measurement.get("breadth_measurement")
    leadership = measurement.get("leadership_measurement")
    trend_rows = measurement.get("trend_etfs")
    if not isinstance(breadth, dict) or not isinstance(leadership, dict):
        fail(PROXY_MIXED_SESSION_GENERATION, label)
    breadth_rows = breadth.get("observations")
    leadership_rows = leadership.get("ordered_groups")
    for rows in (trend_rows, breadth_rows, leadership_rows):
        if not isinstance(rows, list) or not rows or not all(
            isinstance(row, dict) for row in rows
        ):
            fail(PROXY_MIXED_SESSION_GENERATION, label)
    dates = {
        measurement.get("reference_as_of_session_date"),
        breadth.get("as_of_session_date"),
        leadership.get("as_of_session_date"),
    }
    for rows in (trend_rows, breadth_rows, leadership_rows):
        dates |= {row.get("as_of_session_date") for row in rows}
    if dates != {effective}:
        fail(PROXY_MIXED_SESSION_GENERATION, label)


def replay_breadth_leadership_source(
    alpaca_key: str, alpaca_secret: str, anchor: dt.date, *, getter, contract: dict,
) -> dict:
    """Rebuild the TREND/BREADTH/LEADERSHIP observations available as of
    ``anchor`` from one combined union fetch.

    Reuses ``FMD.derive_us_market_reference`` unmodified for BREADTH/
    LEADERSHIP: no arithmetic is reimplemented here. That function needs bars
    for the trend symbols *and* the sector-reference symbols to compute both
    proxy axes (SPY is the LEADERSHIP benchmark, and BREADTH's 14 symbols
    overlap both sets), so this fetches their union in one
    ``fetch_alpaca_daily_bars`` call. Once BREADTH/LEADERSHIP are authorized,
    ``replay_one_requested_date`` derives TREND's own row from this same
    fetch too (via ``_derive_trend_etfs`` on the trend-symbol subset of the
    same bars), rather than issuing ``replay_trend_source``'s own narrower
    request -- so a leadership/breadth session ending on a different day than
    the trend ETFs' is structurally impossible, not merely checked.

    Anchored and lookahead-checked exactly like ``replay_trend_source``:
    ``end`` is pinned to the requested date's last instant, and any returned
    bar dated after it fails this date closed rather than being used.
    """
    if not alpaca_key and not alpaca_secret:
        fail("BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL")
    if not alpaca_key or not alpaca_secret:
        fail("BLOCKED_BY_INCOMPLETE_DEDICATED_MARKET_DATA_CREDENTIAL")
    trend_symbols = list(contract["alpaca"]["trend_symbols"])
    windows = list(contract["alpaca"]["return_windows_sessions"])
    symbols = sorted(
        set(trend_symbols) | set(contract["alpaca"]["sector_reference_symbols"])
    )
    anchor_end = dt.datetime.combine(anchor, dt.time(23, 59, 59), tzinfo=UTC)
    raw, normalized = FMD.fetch_alpaca_daily_bars(
        alpaca_key, alpaca_secret, symbols, anchor_end, getter=getter,
    )
    grouped = _grouped_sessions(
        normalized, anchor, invalid_code="US_PROXY_SESSION_DATE_INVALID",
        lookahead_label="ALPACA_BAR",
    )
    trend_etfs = _derive_trend_etfs(grouped, trend_symbols, windows)
    session_dates = {row["as_of_session_date"] for row in trend_etfs}
    if len(session_dates) != 1:
        fail("US_TREND_SESSION_DATE_MISMATCH")
    effective = session_dates.pop()

    reference = FMD.derive_us_market_reference(normalized, contract)
    breadth = reference.get("proxy_axes", {}).get("BREADTH", {})
    leadership = reference.get("proxy_axes", {}).get("LEADERSHIP", {})
    if breadth.get("status") != "OBSERVED":
        fail("US_BREADTH_NOT_OBSERVED")
    if leadership.get("status") != "OBSERVED":
        fail("US_LEADERSHIP_NOT_OBSERVED")
    measurement = {
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
        "reference_as_of_session_date": reference.get("as_of_session_date"),
        "symbols": symbols,
        "breadth_measurement": breadth["measurement"],
        "leadership_measurement": leadership["measurement"],
        "raw_retention": RAW_RETENTION,
        "response_sha256": FMD.sha256_bytes(raw),
    }
    # Fail closed before any axis row is built: the breadth session, the
    # reference session, and every leadership group's own session must all be
    # the effective session, not merely the trend ETFs'.
    _assert_proxy_session_alignment(measurement, "BREADTH_LEADERSHIP")
    return measurement


def replay_risk_vol_source(
    fred_key: str, anchor: dt.date, *, getter, contract: dict,
    source_mode: str = SOURCE_MODE_API,
) -> dict:
    """Rebuild the VIXCLS observation obtainable as of ``anchor``.

    *Obtainable*, not merely dated at or before it. Under
    ``SOURCE_MODE_EVIDENCE`` the vintage is resolved at ``anchor`` minus
    ``FRED_VINTAGE_LAG_DAYS``, one calendar day, because ALFRED backdates a
    VIXCLS row's availability to its observation date while the live FRED
    observations endpoint had not yet published that day when the decision was
    made. Reading the replayed date's own ALFRED vintage therefore hands the
    replay a value it could not have had -- see ``FRED_VINTAGE_LAG_DAYS`` for the
    two independent reconstructions of the live producer that agree on this, and
    for why the liquidity axis deliberately keeps the same-day vintage.

    The lag and the date it resolved to are both recorded, and
    ``_validate_fred_vintage_binding`` re-requires them: a record whose VIX
    observation is dated on the replayed date itself fails closed rather than
    passing as an ordinary backward-looking observation.
    """
    source_mode = _check_source_mode(source_mode)
    if not fred_key:
        fail("BLOCKED_BY_FRED_CREDENTIAL")
    series_id = contract["fred"]["risk_series"][0]
    lag_days = fred_vintage_lag_days(source_mode, "RISK_VOL")
    vintage_anchor = anchor - dt.timedelta(days=lag_days)
    raw = getter(
        "https://api.stlouisfed.org/fred/series/observations?"
        + _fred_query(series_id, fred_key, vintage_anchor, 60)
    )
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReplayPopulationError("US_VIX_JSON_INVALID") from exc
    latest = _valid_observations(body, "US_VIX_OBSERVATIONS_MISSING")[-1]
    # Bounded by the *lagged* vintage anchor, which is the stricter bound: an
    # observation later than it could not have been obtained on the replayed
    # date even though it is dated at or before that date.
    observation_date = _assert_not_after(
        vintage_anchor, latest.get("date"), "US_VIX_OBSERVATION_DATE_INVALID"
    )
    # Bound, not copied: the observation date alone says nothing about which
    # vintage of that observation was served. Containment is checked against the
    # vintage this request actually pinned.
    realtime_start, realtime_end = _assert_vintage_covers(
        vintage_anchor.isoformat(), latest, series_id,
    )
    return {
        "series_id": series_id,
        "source_scope": contract["fred"]["source_scope"],
        "observation_date": observation_date,
        "value": latest.get("value"),
        "realtime_start": realtime_start,
        "realtime_end": realtime_end,
        "vintage_date": anchor.isoformat(),
        "vintage_as_of_date": vintage_anchor.isoformat(),
        "vintage_lag_days": lag_days,
        "raw_retention": RAW_RETENTION,
        "response_sha256": FMD.sha256_bytes(raw),
    }


def replay_liquidity_source(
    fred_key: str, anchor: dt.date, *, getter, contract: dict,
    source_mode: str = SOURCE_MODE_API, units_scale: dict | None = None,
) -> dict:
    """Rebuild the WRESBAL/TOTBKCR change known as of ``anchor``.

    Unit handling reuses ``collectors/free_market_data.FRED_LIQUIDITY_UNITS``
    and ``_decimal``/``_decimal_text`` unmodified, so a replayed change is on
    the same normalized scale as the production capture.

    Under ``SOURCE_MODE_EVIDENCE`` the normalized scale is the same but the units
    it rests on are *derived* rather than read: the committed store holds one
    metadata capture per series, not one per vintage, so the capture-time factor
    is not this date's factor and applying it would be the later-revision leak
    this module exists to prevent. ``units_scale`` -- the timeline the population
    itself declares in ``pit_source.store`` -- resolves the factor that was in
    effect on this date from the store's own re-publication scale. See
    ``UNITS_VINTAGE_DERIVED_STATUS``.

    The vintage this axis reads is the replayed date's own, in both modes, and
    deliberately so: the weekly liquidity series carry their release lag inside
    the row already, so the one-calendar-day lag ``replay_risk_vol_source``
    applies would be a second, invented delay here. That asymmetry is the live
    producer's behaviour -- see ``FRED_VINTAGE_LAG_DAYS`` -- and is recorded
    rather than left implicit.
    """
    source_mode = _check_source_mode(source_mode)
    units_vintage_available = source_mode != SOURCE_MODE_EVIDENCE
    lag_days = fred_vintage_lag_days(source_mode, "LIQUIDITY")
    if lag_days:
        # Defensive: nothing in the ratified rule lags this axis, and a lag
        # introduced here would silently move every replayed liquidity change.
        fail("LIQUIDITY_VINTAGE_MUST_NOT_BE_LAGGED", str(lag_days))
    if not units_vintage_available and not isinstance(units_scale, dict):
        fail("UNITS_SCALE_REQUIRED", source_mode)
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
        units_block = None
        if units_vintage_available:
            metadata_vintage = _assert_vintage_covers(
                anchor.isoformat(), metadata_rows[0], f"{series_id}.metadata",
            )
            units = metadata_rows[0].get("units")
            unit_base = units.split(",", 1)[0].strip() if isinstance(units, str) else None
            if unit_base not in FMD.FRED_LIQUIDITY_UNITS:
                fail("US_LIQUIDITY_UNITS_INVALID", series_id)
            normalized_unit, factor = FMD.FRED_LIQUIDITY_UNITS[unit_base]
        else:
            # The metadata vintage window is not copied into the row: the
            # captured window lies after most replayed dates, so recording it
            # here would either read as a lookahead or have to be exempted from
            # the backward-looking measurement walk. The population-level
            # ``pit_source.store`` block carries the capture provenance and the
            # derived rescale timeline instead.
            metadata_vintage = None
            series_scale = units_scale.get(series_id)
            if not isinstance(series_scale, dict):
                fail("UNITS_SCALE_MISSING_SERIES", series_id)
            units = series_scale.get("capture_unit")
            normalized_unit = series_scale.get("normalized_unit")
            factor, undone = _derived_units_scale_at(
                series_scale, anchor.isoformat(), series_id,
            )
            units_block = units_vintage_block(factor, undone)
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
        row = {
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
        }
        if metadata_vintage is not None:
            row["metadata_realtime_start"] = metadata_vintage[0]
            row["metadata_realtime_end"] = metadata_vintage[1]
        else:
            # ``source_unit`` would be a claim about the replayed vintage's own
            # units string, which is what the derivation recovers as a factor
            # rather than as a string, so the row names the factor and where it
            # came from instead of a unit it did not read.
            row["source_unit"] = None
            row["units_vintage"] = units_block
        series_rows.append(row)
    return {
        "source_scope": contract["fred"]["source_scope"],
        "derivation_version": "fred_liquidity_current/v1",
        "vintage_date": anchor.isoformat(),
        "vintage_as_of_date": anchor.isoformat(),
        "vintage_lag_days": lag_days,
        "series": series_rows,
        "response_hashes": response_hashes,
        "raw_retention": RAW_RETENTION,
    }


# ---------------------------------------------------------------------------
# Per-date replay.
# ---------------------------------------------------------------------------


HISTORICAL_PIT_REPLAY_IDENTITY_PATH = (
    ROOT / "config" / "us_historical_pit_replay_identity_v1.json"
)


def _load_historical_pit_replay_identity() -> dict | None:
    """The dedicated identity file, or ``None`` if it is absent/unreadable.

    Deliberately its own file rather than a field inside
    ``config/free_market_data_contract.json``: that contract's exact bytes
    are pinned elsewhere (``config/regime_source_owner_registry_v2.json``,
    read by ``regime/decision_authority.py``) as an unrelated governance
    anchor, so editing the contract at all -- even an additive key -- would
    change its sha256 and break that pin. A missing or unreadable file is
    treated exactly like an absent identity always was: the pre-U1 narrow
    default, never an error that could abort an otherwise-unrelated replay.

    Only a genuinely *absent* file is that default. A file that is present but
    unreadable, not UTF-8, not JSON, or not a JSON object is a corrupted claim,
    not an absence, and fails closed exactly like a present-but-mis-hashed one:
    treating it as absent would let a truncated or garbled identity silently
    re-narrow scope instead of surfacing.
    """
    path = HISTORICAL_PIT_REPLAY_IDENTITY_PATH
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReplayPopulationError(
            f"HISTORICAL_PIT_REPLAY_IDENTITY_INVALID:{path}:UNREADABLE"
        ) from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayPopulationError(
            f"HISTORICAL_PIT_REPLAY_IDENTITY_INVALID:{path}:MALFORMED_JSON"
        ) from exc
    if not isinstance(value, dict):
        fail("HISTORICAL_PIT_REPLAY_IDENTITY_INVALID", f"{path}:NOT_AN_OBJECT")
    return value


def authorized_axes(contract: dict) -> list[str]:
    """Which of ``PRR.AXES`` this contract/identity currently authorizes.

    Fails closed on any ``approval_status`` or ``us_breadth_authorized`` this
    module does not explicitly recognize -- widening scope is a ratification
    decision (CIO plan U2), never inferred from an unrecognized string.
    ``us_breadth_authorized`` is deliberately never the gate that *widens*
    scope (it stays ``false`` permanently; other contract consumers depend on
    that), only a sanity check that it has not drifted from the value every
    other state here assumes. Widening is instead keyed entirely off
    ``HISTORICAL_PIT_REPLAY_IDENTITY_PATH``: absent, it is simply the pre-U1
    default (3-axis, unauthorized); present but not shaped or hash-bound
    exactly as the ratified decision record fails closed (a forged or
    malformed claim); present, correctly hash-bound, and explicitly activated
    is the only state that authorizes all five axes.
    """
    proxy = contract["alpaca"]["current_proxy_axes"]
    approval_status = proxy.get("approval_status")
    breadth_authorized = contract["authority"].get("us_breadth_authorized")
    if approval_status != "RATIFIED_CURRENT_REFERENCE_ONLY":
        fail("EXCLUSION_BASIS_CHANGED", "alpaca.current_proxy_axes.approval_status")
    if breadth_authorized is not False:
        fail("EXCLUSION_BASIS_CHANGED", "authority.us_breadth_authorized")
    if not _historical_pit_replay_activated():
        return list(REPLAYED_AXES)
    return list(PRR.AXES)


def _historical_pit_replay_activated() -> bool:
    """Whether the dedicated identity file actually widens scope.

    Three outcomes, not two: absent entirely (the pre-U1 default, quietly
    narrow -- every historical population built before this file existed is
    exactly this case); present but malformed, wrongly shaped, or
    hash-mismatched against the ratified decision record (a forged or
    corrupted claim, failed closed rather than silently treated as absent);
    present, correctly hash-bound, and its own
    ``replay_population_wiring_activated`` flag explicitly ``True`` or
    ``False`` (a deliberately staged, reviewable widening switch -- recording
    the CIO's ratified identity does not by itself flip every historical
    population from 3-axis to 5-axis replay).
    """
    identity = _load_historical_pit_replay_identity()
    if identity is None:
        return False
    decision = identity.get("decision_record")
    if (
        identity.get("status") != RATIFIED_HISTORICAL_PIT_REPLAY_STATUS
        or not isinstance(decision, dict)
        or decision.get("decision_id") != RATIFIED_HISTORICAL_PIT_REPLAY_DECISION_ID
        or decision.get("sha256") != RATIFIED_HISTORICAL_PIT_REPLAY_DECISION_SHA256
    ):
        fail(
            "HISTORICAL_PIT_REPLAY_IDENTITY_INVALID",
            str(HISTORICAL_PIT_REPLAY_IDENTITY_PATH),
        )
    activated = identity.get("replay_population_wiring_activated")
    if activated is not True and activated is not False:
        fail(
            "HISTORICAL_PIT_REPLAY_IDENTITY_INVALID",
            "replay_population_wiring_activated",
        )
    return activated


def exclusion_basis(contract: dict) -> dict:
    """Why any of ``PRR.AXES`` stay UNKNOWN — read from the contract, not asserted.

    Derived from ``authorized_axes``: every axis it does not authorize gets
    the same shape this function has always produced for BREADTH/LEADERSHIP.
    If the ratification scope of the ETF proxies ever changes, this module
    must be re-decided by a human rather than keep quietly excluding (or
    quietly start including) axes, so a changed basis fails closed via
    ``authorized_axes`` before this function is even reached. Once every axis
    is authorized this returns ``{}`` -- nothing is excluded.
    """
    replayed = authorized_axes(contract)
    excluded_names = [name for name in PRR.AXES if name not in replayed]
    if not excluded_names:
        return {}
    proxy = contract["alpaca"]["current_proxy_axes"]
    approval_status = proxy.get("approval_status")
    breadth_authorized = contract["authority"].get("us_breadth_authorized")
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
        for name in excluded_names
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
    observed: list[str], not_computable: list[str], attempted: int, replayed: list[str],
) -> dict:
    """The record's own free-axis coverage, derived from the axes themselves."""
    return {
        "attempted_count": attempted,
        "observed_count": len(observed),
        "ratio": f"{len(observed)}/{len(replayed)}",
        "observed_axes": list(observed),
        "not_computable_axes": list(not_computable),
    }


def _five_axis_block(
    observed: list[str], not_computable: list[str], axes: dict, excluded_names: list[str],
) -> dict:
    """The five-axis packet, whose status and coverage are derived, not asserted.

    The status names what this packet actually holds: never "5/5 observed", and
    never "observed" at all when nothing survived. The coverage counts the same
    axes the packet carries, and any axis this replay does not authorize is
    always missing because this module never populates it.
    """
    return {
        "status": FIVE_AXIS_STATUS_OBSERVED if observed else FIVE_AXIS_STATUS_NONE,
        "coverage": {
            "defined_count": len(observed),
            "required_count": len(PRR.AXES),
            "ratio": f"{len(observed)}/{len(PRR.AXES)}",
            "defined_axes": list(observed),
            "missing_axes": sorted(list(not_computable) + list(excluded_names)),
        },
        "axes": axes,
    }


def _breadth_leadership_session_date_range(measurement: dict | None) -> list[str]:
    """The earliest/latest date named anywhere inside a combined measurement.

    Generic over ``_measurement_dates`` rather than reaching for specific keys,
    so it stays accurate however the combined BREADTH/LEADERSHIP measurement's
    shape grows: every date it names -- trend/breadth/leadership session dates
    included -- is covered by the same walk ``_validate_measurement_source_dates``
    already applies to it.
    """
    if not measurement:
        return []
    dates = _measurement_dates(measurement, "BREADTH_LEADERSHIP")
    if not dates:
        return []
    return [min(dates).isoformat(), max(dates).isoformat()]


def _no_lookahead_attestation(
    requested_date: str,
    trend: dict | None,
    risk: dict | None,
    liquidity: dict | None,
    liquidity_dates: list[str],
    *,
    attempted: bool,
    breadth_leadership: dict | None = None,
    breadth_leadership_authorized: bool = False,
) -> dict:
    """The dates this record actually consulted, taken from its own measurements.

    Derived rather than declared, and re-derived the same way in
    ``_validate_attestation_is_derived_from_its_evidence``: a summary of consulted
    dates that nothing rebuilds can be re-signed into naming fewer sources than
    the record used, which would leave ``_validate_no_lookahead``'s walk with
    nothing to reject.

    ``breadth_leadership_session_date_range`` is added only when this replay
    authorizes BREADTH/LEADERSHIP at all, so a record built under today's
    3-axis contract keeps exactly its current shape -- adding an always-empty
    key for an axis pair this replay never attempts would itself be an
    unauthorized claim about what was consulted.
    """
    attestation = {
        "anchor_requested_date": requested_date,
        # A date that produced no axis packet at all issued no vintage-pinned
        # request whose vintage could be attested.
        "fred_realtime_vintage_date": requested_date if attempted else None,
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
    if breadth_leadership_authorized:
        attestation["breadth_leadership_session_date_range"] = (
            _breadth_leadership_session_date_range(breadth_leadership)
        )
    return attestation


def _blocked_date_record(
    requested_date: str, failure_reason: str, *, replayed: list[str], attempted: int = 0,
    source_mode: str = SOURCE_MODE_API,
) -> dict:
    return {
        "requested_date": requested_date,
        "status": STATUS_BLOCKED,
        "evidence_class": EVIDENCE_CLASS,
        "effective_session_date": None,
        "free_axis_coverage": _free_axis_coverage([], list(replayed), attempted, replayed),
        "five_axis": None,
        "candidate_normalized_result": None,
        "source_hashes": None,
        "failure_reason": failure_reason,
        "warnings": record_warnings(source_mode),
        "no_lookahead_attestation": _no_lookahead_attestation(
            requested_date, None, None, None, [], attempted=False,
            breadth_leadership_authorized="BREADTH" in replayed,
        ),
    }


def _candidate_normalized_result(
    rows: list[dict], as_of_date: str | None, policy: dict, excluded: dict,
) -> dict:
    """Apply the existing candidate rule's own classifier to this axis set.

    ``PRR.classify`` is called unmodified in every case, so the outcome always
    follows the live rule rather than a re-derived copy of it. With fewer than
    five axes it returns UNKNOWN by its own contract -- the honest outcome
    whenever coverage is partial, authorized or not. Once this replay is
    authorized for all five axes (``excluded`` is empty) AND all five were
    genuinely observed, the same unmodified call is allowed to publish
    whatever real regime it computes, instead of being forced to UNKNOWN.

    A full five-row result while any axis is still excluded must never occur
    -- if it somehow did, this fails closed rather than silently upgrading a
    partial-scope replay into a genuine regime (defense in depth: this is the
    one guarantee that must never regress in any authorization state).
    """
    regime, score, explanation = PRR.classify(rows, policy)
    five_axis_authorized = not excluded
    full_coverage = len(rows) == len(PRR.AXES)
    if not five_axis_authorized and (full_coverage or regime != "UNKNOWN"):
        fail("PARTIAL_COVERAGE_MUST_NOT_CLASSIFY")
    classifies = five_axis_authorized and full_coverage
    defined = [row["axis"] for row in rows]
    missing = [name for name in PRR.AXES if name not in defined]
    confidence = PRR.confidence(regime, rows) if classifies else None
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
            # from a partial set: a numeric score over fewer than 5 axes would
            # read as comparable to a full-coverage score and is not. Once
            # coverage is complete and authorized, they are the live rule's
            # own genuine values -- not invented here.
            "score": score if classifies else None,
            "confidence": None if confidence is None else str(confidence),
            "explanation_ko": explanation,
        },
        "classification_status": (
            CLASSIFICATION_STATUS_CLASSIFIED if classifies else CLASSIFICATION_STATUS
        ),
        "runtime_regime": regime if classifies else "UNKNOWN",
        "axes": rows,
        "candidate_rule_source": (
            "regime/paper_regime_reference.py::build_us"
            if classifies
            else (
                "regime/paper_regime_reference.py::build_us"
                " (TREND/RISK_VOL/LIQUIDITY axes only)"
            )
        ),
    }


def _unified_trend_breadth_leadership_attempts(
    alpaca_key: str, alpaca_secret: str, anchor: dt.date, *, getter, contract: dict,
    secrets: list[str],
) -> tuple[dict, dict, dict]:
    """One shared Alpaca fetch backs TREND, BREADTH, and LEADERSHIP together.

    Used only once BREADTH/LEADERSHIP are authorized: TREND's own narrower
    fetch (``replay_trend_source``) is bypassed entirely so all three axes are
    derived from the exact same union fetch, making a leadership/breadth
    session ending on a different day than the trend ETFs' structurally
    impossible rather than merely checked after the fact. A fetch failure is
    therefore attributed to all three axes' NOT_COMPUTABLE reason, exactly
    like the shared measurement that feeds them on success -- never silently
    failing one while the others report nothing about why they, too, went
    unobserved.
    """
    try:
        measurement = replay_breadth_leadership_source(
            alpaca_key, alpaca_secret, anchor, getter=getter, contract=contract,
        )
    except (
        ReplayPopulationError, PRR.PaperRegimeReferenceError, FMD.FreeMarketDataError,
    ) as exc:
        reason = redact(str(exc), secrets)
        failed = {"measurement": None, "row": None, "reason": reason}
        return dict(failed), dict(failed), dict(failed)
    except Exception as exc:  # noqa: BLE001 — same containment as _axis_attempt.
        reason = f"UNSUPPORTED_REPLAY_SHAPE_{type(exc).__name__}"
        failed = {"measurement": None, "row": None, "reason": reason}
        return dict(failed), dict(failed), dict(failed)
    trend_attempt = _axis_attempt(
        lambda: measurement, lambda m: trend_axis_row(m["trend_etfs"]), secrets,
    )
    breadth_attempt = _axis_attempt(
        lambda: measurement,
        lambda m: breadth_axis_row(m["breadth_measurement"]["advance_fraction"]),
        secrets,
    )
    leadership_attempt = _axis_attempt(
        lambda: measurement,
        lambda m: leadership_axis_row(m["leadership_measurement"]["ordered_groups"]),
        secrets,
    )
    return trend_attempt, breadth_attempt, leadership_attempt


def replay_one_requested_date(
    credentials: dict, requested_date: str, *, getter, contract: dict, policy: dict,
    excluded: dict, replayed: list[str], source_mode: str = SOURCE_MODE_API,
    units_scale: dict | None = None,
) -> dict:
    """Resolve and replay exactly one caller-supplied historical date.

    Takes only this one requested date plus the on-disk contract/candidate
    policy. Every source request is anchored to this date and bounded backward,
    so the call is structurally incapable of consuming a session, observation,
    revision, or outcome belonging to any other requested date.

    ``replayed``/``excluded`` are derived once, from the pinned contract, by
    the caller (``build_population``) and threaded down explicitly rather than
    read from the bare ``REPLAYED_AXES``/``EXCLUDED_AXES`` module globals, so
    this call's authorized axis set can never silently diverge from the
    contract it was actually built against.
    """
    source_mode = _check_source_mode(source_mode)
    secrets = [value for value in credentials.values() if value]
    try:
        anchor = _parse_requested_date(requested_date)
    except ReplayPopulationError as exc:
        return _blocked_date_record(
            requested_date, redact(str(exc), secrets), replayed=replayed,
            source_mode=source_mode,
        )

    breadth_leadership_authorized = "BREADTH" in replayed
    if breadth_leadership_authorized:
        trend_attempt, breadth_attempt, leadership_attempt = (
            _unified_trend_breadth_leadership_attempts(
                credentials.get("alpaca_key", ""), credentials.get("alpaca_secret", ""),
                anchor, getter=getter, contract=contract, secrets=secrets,
            )
        )
        attempts = {
            "TREND": trend_attempt,
            "RISK_VOL": _axis_attempt(
                lambda: replay_risk_vol_source(
                    credentials.get("fred_key", ""), anchor, getter=getter,
                    contract=contract, source_mode=source_mode,
                ),
                lambda measurement: risk_vol_axis_row(measurement["value"]),
                secrets,
            ),
            "LIQUIDITY": _axis_attempt(
                lambda: replay_liquidity_source(
                    credentials.get("fred_key", ""), anchor, getter=getter,
                    contract=contract, source_mode=source_mode,
                    units_scale=units_scale,
                ),
                lambda measurement: liquidity_axis_row(measurement["series"]),
                secrets,
            ),
            "BREADTH": breadth_attempt,
            "LEADERSHIP": leadership_attempt,
        }
    else:
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
                    credentials.get("fred_key", ""), anchor, getter=getter,
                    contract=contract, source_mode=source_mode,
                ),
                lambda measurement: risk_vol_axis_row(measurement["value"]),
                secrets,
            ),
            "LIQUIDITY": _axis_attempt(
                lambda: replay_liquidity_source(
                    credentials.get("fred_key", ""), anchor, getter=getter,
                    contract=contract, source_mode=source_mode,
                    units_scale=units_scale,
                ),
                lambda measurement: liquidity_axis_row(measurement["series"]),
                secrets,
            ),
        }

    observed_axes = [name for name in replayed if attempts[name]["row"] is not None]
    not_computable = [name for name in replayed if attempts[name]["row"] is None]

    trend = attempts["TREND"]["measurement"]
    risk = attempts["RISK_VOL"]["measurement"]
    liquidity = attempts["LIQUIDITY"]["measurement"]
    effective_session_date = trend["as_of_session_date"] if trend else None
    # Under the unified fetch, TREND/BREADTH/LEADERSHIP all carry the exact
    # same combined measurement -- fall back across them rather than reading
    # only "TREND" so a record where TREND's own derivation independently
    # failed (while BREADTH/LEADERSHIP's succeeded from the identical fetch)
    # still uses the shared measurement's dates and hash correctly.
    breadth_leadership_measurement = (
        (
            trend
            or attempts["BREADTH"]["measurement"]
            or attempts["LEADERSHIP"]["measurement"]
        )
        if breadth_leadership_authorized
        else None
    )

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
    breadth_leadership_dates: list[str] = []
    if breadth_leadership_measurement:
        breadth_leadership_dates = [
            date.isoformat()
            for date in _measurement_dates(
                breadth_leadership_measurement, "BREADTH_LEADERSHIP",
            )
        ]
        source_dates.extend(breadth_leadership_dates)
    # Computed, not asserted: if any consumed source date is later than the
    # requested date the whole date fails closed rather than publishing a
    # "no lookahead" claim it cannot support.
    if any(date > requested_date for date in source_dates):
        return _blocked_date_record(
            requested_date, LOOKAHEAD_BLOCKED_REASON, replayed=replayed,
            attempted=len(replayed), source_mode=source_mode,
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
        for name in replayed
    }
    for name in excluded:
        axes[name] = {
            "status": "UNKNOWN",
            "reason": excluded[name]["reason_code"],
            "measurement": None,
        }

    rows = [attempts[name]["row"] for name in replayed if attempts[name]["row"] is not None]
    candidate = (
        _candidate_normalized_result(rows, effective_session_date, policy, excluded)
        if rows
        else None
    )

    if len(observed_axes) == len(replayed):
        status, failure_reason = STATUS_OBSERVED, None
    elif observed_axes:
        status, failure_reason = STATUS_PARTIAL, None
    else:
        # Per-axis reasons carry the attribution; the record-level reason only
        # states that nothing publishable survived for this date.
        status, failure_reason = STATUS_BLOCKED, ALL_AXES_NOT_COMPUTABLE_REASON

    source_hashes = {
        "trend_response_sha256": trend["response_sha256"] if trend else None,
        "risk_vol_response_sha256": risk["response_sha256"] if risk else None,
        "liquidity_response_hashes": (
            copy.deepcopy(liquidity["response_hashes"]) if liquidity else None
        ),
    }
    if breadth_leadership_authorized:
        source_hashes[BREADTH_LEADERSHIP_RESPONSE_HASH_KEY] = (
            breadth_leadership_measurement["response_sha256"]
            if breadth_leadership_measurement
            else None
        )

    return {
        "requested_date": requested_date,
        "status": status,
        "evidence_class": EVIDENCE_CLASS,
        "effective_session_date": effective_session_date,
        "free_axis_coverage": _free_axis_coverage(
            observed_axes, not_computable, len(replayed), replayed,
        ),
        "five_axis": _five_axis_block(observed_axes, not_computable, axes, list(excluded)),
        "candidate_normalized_result": candidate,
        "source_hashes": source_hashes,
        "failure_reason": failure_reason,
        "warnings": record_warnings(source_mode),
        "no_lookahead_attestation": _no_lookahead_attestation(
            requested_date, trend, risk, liquidity, liquidity_dates, attempted=True,
            breadth_leadership=breadth_leadership_measurement,
            breadth_leadership_authorized=breadth_leadership_authorized,
        ),
    }


# ---------------------------------------------------------------------------
# Population.
# ---------------------------------------------------------------------------


def _declared_units_scale(store: object) -> dict:
    """The derived FRED units timeline the population's own store declares.

    One place both the builder and the validator read it from, so a row's factor
    and the timeline a reader would check it against can never be two different
    things.
    """
    fred = store.get("fred") if isinstance(store, dict) else None
    scale = fred.get("units_scale") if isinstance(fred, dict) else None
    if not isinstance(scale, dict) or not scale:
        fail("SOURCE_STORE_UNITS_SCALE_MISSING")
    return scale


def _pit_source_block(source_mode: str, store: object) -> dict:
    """Which sources this population was actually rebuilt from.

    Published and re-required key for key by ``_validate_pit_source``, for the
    same reason ``pit_replay`` is: a re-hashed payload is a valid signature over
    whatever it contains, so an unchecked mode field would let an evidence-read
    population be re-signed as a live-provider one -- and with it the stronger
    units-vintage guarantee it never had.
    """
    return {
        "mode": _check_source_mode(source_mode),
        "store": store,
        "units_vintage_available": source_mode != SOURCE_MODE_EVIDENCE,
        "statement": PIT_SOURCE_STATEMENT[source_mode],
    }


def _pit_replay_block(source_mode: str = SOURCE_MODE_API) -> dict:
    """The population's point-in-time declaration, built from the shared shape.

    Emitted here and re-required by ``_validate_pit_replay`` from the same
    constants, so a field can never be published without being checked or
    checked without being published.
    """
    return {
        **{key: True for key in PIT_REPLAY_TRUE_KEYS},
        **{key: False for key in PIT_REPLAY_FALSE_KEYS},
        "close_adjustment": PIT_REPLAY_CLOSE_ADJUSTMENT,
        "statement": pit_replay_statement(source_mode),
    }


def build_population(
    credentials: dict, requested_dates: list[str], *, getter=None,
    source_mode: str = SOURCE_MODE_API, source_store: object = None,
) -> dict:
    """Replay every requested date and publish one population.

    ``source_mode`` records — and is re-checked against — where the per-date
    sources came from. It changes nothing about the derivation: the same
    requests are issued against the same code, only answered by a different
    ``getter``. It is a declaration, not a switch over arithmetic, and
    ``SOURCE_MODE_EVIDENCE`` additionally requires ``source_store`` so the
    population names the store it read.
    """
    source_mode = _check_source_mode(source_mode)
    if source_mode == SOURCE_MODE_EVIDENCE and not isinstance(source_store, dict):
        fail("SOURCE_STORE_REQUIRED", source_mode)
    if source_mode == SOURCE_MODE_API and source_store is not None:
        fail("SOURCE_STORE_FORBIDDEN", source_mode)
    # Read out of the declared store rather than taken as its own argument, so
    # the timeline a record's factor was derived from is by construction the
    # timeline the population publishes and the validator re-derives against.
    units_scale = None
    if source_mode == SOURCE_MODE_EVIDENCE:
        units_scale = _declared_units_scale(source_store)
    getter = FMD._get if getter is None else getter
    contract = FMD.load_contract(FMD.CONTRACT_PATH)
    policy = _load_candidate_policy()
    # Derived once from the pinned contract and threaded explicitly through
    # every nested call below, rather than read from the bare
    # ``REPLAYED_AXES``/``EXCLUDED_AXES`` module globals -- so this build's
    # authorized axis set can never silently diverge from the contract it was
    # actually built against.
    replayed = authorized_axes(contract)
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
            excluded=excluded, replayed=replayed, source_mode=source_mode,
            units_scale=units_scale,
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
        "replayed_axes": list(replayed),
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
        # Where the per-date sources came from -- live providers or the
        # committed evidence store -- and what that choice does and does not
        # guarantee. Re-required by ``_validate_pit_source``.
        "pit_source": _pit_source_block(source_mode, source_store),
        # Structural facts about *how* this population was produced. Each is
        # enforced by test/test_us_historical_replay_population.py rather than
        # merely asserted here, and each is re-required key for key by
        # ``_validate_pit_replay``.
        "pit_replay": _pit_replay_block(source_mode),
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
    contract = _revalidation_contract(value)
    # Re-derived from the re-read, sha256-pinned contract rather than compared
    # against the bare ``REPLAYED_AXES`` module global: a genuinely-ratified
    # population must not fail validation merely because the module constant
    # still names the pre-ratification default.
    replayed = authorized_axes(contract)
    if value.get("market") != "US" or value.get("replayed_axes") != replayed:
        fail("POPULATION_SCOPE_INVALID")
    excluded = _validate_excluded_axes(value, contract)
    requested = value.get("requested_dates")
    if (
        not isinstance(requested, list)
        or not requested
        or any(not isinstance(date, str) for date in requested)
        or requested != sorted(set(requested))
    ):
        fail("POPULATION_DATE_ORDER_INVALID")
    # Read before the records are walked: which units-vintage rule applies to an
    # evidence-read liquidity row is keyed off this declaration, never off what
    # the row itself claims.
    source_mode = _validate_pit_source(value)
    units_scale = (
        _declared_units_scale((value.get("pit_source") or {}).get("store"))
        if source_mode == SOURCE_MODE_EVIDENCE
        else None
    )
    _validate_records(
        value, requested, _revalidation_policy(value), excluded, replayed,
        source_mode, units_scale,
    )
    _validate_pit_replay(value, source_mode)
    _validate_authority(value)
    return copy.deepcopy(value)


def _validate_pit_source(value: dict) -> str:
    """The population's own source declaration, required key for key.

    Returns the declared mode, which every record check below is keyed off.

    A population built before this block existed carries no ``pit_source`` at
    all; that is the live-provider default and is accepted as such, exactly like
    an absent historical-PIT identity file is the pre-U1 narrow default. What is
    not accepted is a *present* block that is malformed, that names an unknown
    mode, or whose statement or ``units_vintage_available`` flag disagrees with
    the mode it declares -- re-signing an evidence-read population as a
    live-provider one would silently claim the stronger units-vintage guarantee
    that mode never had.
    """
    pit_source = value.get("pit_source")
    if pit_source is None:
        return SOURCE_MODE_API
    if not isinstance(pit_source, dict) or sorted(pit_source) != sorted(PIT_SOURCE_KEYS):
        fail("PIT_SOURCE_SCHEMA_INVALID")
    mode = _check_source_mode(pit_source.get("mode"))
    if pit_source != _pit_source_block(mode, pit_source.get("store")):
        fail("PIT_SOURCE_DECLARATION_INVALID", mode)
    if mode == SOURCE_MODE_EVIDENCE and not isinstance(pit_source.get("store"), dict):
        fail("PIT_SOURCE_STORE_REQUIRED", mode)
    if mode == SOURCE_MODE_API and pit_source.get("store") is not None:
        fail("PIT_SOURCE_STORE_FORBIDDEN", mode)
    return mode


def _validate_pit_replay(value: dict, source_mode: str = SOURCE_MODE_API) -> None:
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
    if pit["statement"] != pit_replay_statement(source_mode):
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
    if not isinstance(declared, dict):
        fail("POPULATION_SCOPE_INVALID", "excluded_axes")
    expected = exclusion_basis(contract)
    if sorted(declared) != sorted(expected):
        fail("POPULATION_SCOPE_INVALID", "excluded_axes")
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
    value: dict, requested: list[str], policy: dict, excluded: dict, replayed: list[str],
    source_mode: str = SOURCE_MODE_API, units_scale: dict | None = None,
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
        _validate_record(
            record, requested_date, policy, excluded, replayed, source_mode,
            units_scale,
        )


def _validate_record(
    record: dict, requested_date: str, policy: dict, excluded: dict, replayed: list[str],
    source_mode: str = SOURCE_MODE_API, units_scale: dict | None = None,
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
    if record.get("warnings") != record_warnings(source_mode):
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
    not_computable = list(replayed)
    if five_axis is not None:
        if not isinstance(five_axis, dict) or sorted(five_axis) != sorted(FIVE_AXIS_KEYS):
            fail("RECORD_FIVE_AXIS_INVALID", requested_date)
        axes = five_axis.get("axes")
        if not isinstance(axes, dict) or sorted(axes) != sorted(PRR.AXES):
            fail("RECORD_AXIS_SET_INVALID", requested_date)
        # The one substantive guarantee of this slice: an axis this replay does
        # not authorize must never carry a value, whatever else a record
        # carries. The per-record reason must also be the population's own
        # contract-derived exclusion code, so a record cannot keep the UNKNOWN
        # status while attributing it to some other, unratified cause.
        for name in excluded:
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
        for name in replayed:
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
            name for name in replayed if axes[name].get("status") == "OBSERVED"
        ]
        not_computable = [name for name in replayed if name not in observed]

    # Coverage and status are recomputed from the axes themselves, so a payload
    # cannot report a coverage ratio or a record status the axes do not support.
    coverage = record.get("free_axis_coverage")
    if not isinstance(coverage, dict):
        fail("RECORD_COVERAGE_MISSING", requested_date)
    # ``attempted_count`` is checked against the whole block, not on its own:
    # it is the denominator a reader divides ``observed_count`` by, so an
    # attempted count of 0 beside observed axes reports a replay that never
    # ran and still produced evidence. A record that carries an axis packet
    # attempted every replayed axis by construction; one that carries none either
    # never got past its own requested date (0) or attempted them all and then
    # failed the date closed for lookahead.
    expected_attempted = (
        len(replayed)
        if five_axis is not None
        or record.get("failure_reason") == LOOKAHEAD_BLOCKED_REASON
        else 0
    )
    if coverage != _free_axis_coverage(observed, not_computable, expected_attempted, replayed):
        fail("RECORD_COVERAGE_INCONSISTENT", requested_date)
    if status != _status_for(observed, replayed):
        fail("RECORD_STATUS_INCONSISTENT_WITH_COVERAGE", requested_date)
    # The five-axis packet publishes its own status and coverage over the same
    # axes, and both are derived. Checking only the axis *set* above left them
    # free text: a re-signed ``defined_count`` of 999, a ``missing_axes`` list
    # that omits the excluded axes, or an "observed" status on a packet that
    # observed nothing all travelled intact.
    if five_axis is not None and five_axis != _five_axis_block(
        observed, not_computable, axes, list(excluded),
    ):
        fail("RECORD_FIVE_AXIS_NOT_DERIVED_FROM_ITS_AXES", requested_date)
    _validate_failure_reason(record, five_axis, observed, requested_date)
    if five_axis is None and record.get("effective_session_date") is not None:
        # With no axis packet there is no TREND measurement to bind it to, so
        # ``_validate_candidate_is_derived_from_its_evidence`` never reaches it.
        fail("EFFECTIVE_SESSION_DATE_NOT_DERIVED_FROM_ITS_EVIDENCE", requested_date)

    # This is the one guarantee that must never regress in any authorization
    # state: while coverage is partial (fewer than all of ``PRR.AXES``
    # observed), the candidate must stay UNKNOWN, unclassified, and honestly
    # labelled not-computable. Once coverage is complete -- which, by
    # construction, ``observed`` can only reach when this replay is authorized
    # for all five axes -- a genuine classification is permitted and required
    # to be exactly what re-derivation below produces.
    full_coverage = len(observed) == len(PRR.AXES)
    if candidate is not None:
        if not full_coverage:
            if candidate.get("paper_reference", {}).get("candidate_regime") != "UNKNOWN":
                fail("PARTIAL_COVERAGE_MUST_NOT_CLASSIFY")
            if candidate.get("runtime_regime") != "UNKNOWN":
                fail("RUNTIME_REGIME_MUST_STAY_UNKNOWN")
            if candidate.get("classification_status") != CLASSIFICATION_STATUS:
                fail("CLASSIFICATION_STATUS_INVALID")
        else:
            if candidate.get("classification_status") != CLASSIFICATION_STATUS_CLASSIFIED:
                fail("CLASSIFICATION_STATUS_INVALID")
            if candidate.get("runtime_regime") != candidate.get(
                "paper_reference", {},
            ).get("candidate_regime"):
                fail("RUNTIME_REGIME_NOT_DERIVED_FROM_ITS_EVIDENCE")
    _validate_candidate_is_derived_from_its_evidence(
        record, five_axis, candidate, policy, requested_date, replayed, excluded,
    )
    _validate_source_hash_consistency(record, axes, observed, replayed, requested_date)
    _validate_fred_vintage_binding(
        axes, observed, requested_date, source_mode, units_scale,
    )
    _validate_measurement_source_dates(axes, observed, requested_date)
    _validate_no_lookahead(record, requested_date)
    _validate_attestation_is_derived_from_its_evidence(
        record, five_axis, axes, observed, replayed, requested_date,
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
    record: dict, five_axis: object, axes: dict, observed: list[str],
    replayed: list[str], requested_date: str,
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
    breadth_leadership_authorized = "BREADTH" in replayed
    # Under the unified fetch, TREND/BREADTH/LEADERSHIP all carry the exact
    # same combined measurement -- fall back across them rather than reading
    # only "TREND" so a record where TREND's own derivation independently
    # failed (while BREADTH/LEADERSHIP's succeeded from the identical fetch)
    # still attests the shared measurement's dates correctly.
    breadth_leadership_measurement = (
        measurement("TREND") or measurement("BREADTH") or measurement("LEADERSHIP")
        if breadth_leadership_authorized
        else None
    )
    expected = _no_lookahead_attestation(
        requested_date,
        measurement("TREND"),
        measurement("RISK_VOL"),
        liquidity,
        liquidity_dates,
        attempted=five_axis is not None,
        breadth_leadership=breadth_leadership_measurement,
        breadth_leadership_authorized=breadth_leadership_authorized,
    )
    if record.get("no_lookahead_attestation") != expected:
        fail("RECORD_ATTESTATION_NOT_DERIVED_FROM_ITS_EVIDENCE", requested_date)


def _validate_vintage_lag(
    measurement: dict, axis: str, requested_date: str, source_mode: str,
) -> str:
    """The vintage a FRED axis resolved at must be the one the rule allows.

    Returns the as-of date every observation in that measurement is bound to.

    This is the lock on the asymmetry in ``FRED_VINTAGE_LAG_DAYS``, and it is a
    lock in both directions. Under ``SOURCE_MODE_EVIDENCE`` the VIX axis must
    declare a one-calendar-day lag and resolve to exactly ``requested_date``
    minus that day, so a record whose VIX observation is dated on the replayed
    date itself -- which is what reading ALFRED's backdated availability gives,
    and what the live producer could not obtain -- fails closed instead of
    passing as an ordinary backward-looking observation. The liquidity axis must
    declare no lag at all, so the asymmetry cannot be flattened by lagging
    liquidity either.

    Keyed off the population's declared source mode, never off the number the
    measurement wrote: a record cannot license its own lag.
    """
    expected_lag = fred_vintage_lag_days(source_mode, axis)
    label = f"{requested_date}:{axis}"
    if measurement.get("vintage_lag_days") != expected_lag:
        fail("FRED_VINTAGE_LAG_NOT_THE_DECLARED_RULE", f"{label}:{expected_lag}")
    anchor = _calendar_date(requested_date)
    if anchor is None:
        fail("REQUESTED_DATE_CALENDAR_INVALID", label)
    expected_as_of = (anchor - dt.timedelta(days=expected_lag)).isoformat()
    if measurement.get("vintage_as_of_date") != expected_as_of:
        fail("FRED_VINTAGE_AS_OF_NOT_DERIVED_FROM_THE_REQUESTED_DATE", label)
    return expected_as_of


def _validate_units_vintage_derived(
    row: object, label: str, units_scale: dict, as_of_date: str,
) -> None:
    """An evidence-read liquidity row's units factor must be the derived one.

    The factor is not read, it is rebuilt from the timeline the population itself
    declares in ``pit_source.store`` -- so a row cannot carry a normalization the
    committed store does not support, and cannot quietly adopt the capture-time
    factor for a date the store shows was on a different scale.

    Two shapes are refused outright. A row carrying a metadata vintage window
    claims a units vintage the store does not hold, which is the substitution
    this mode exists to avoid. A row carrying a ``source_unit`` claims to have
    read the replayed vintage's own units string, which the derivation recovers
    as a factor rather than as a string.
    """
    if not isinstance(row, dict):
        fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", label)
    if any(key in row for key in FRED_METADATA_VINTAGE_KEYS):
        fail("UNITS_VINTAGE_MUST_NOT_BE_CLAIMED_FROM_THE_EVIDENCE_STORE", label)
    if row.get("source_unit") is not None:
        fail("UNITS_VINTAGE_DISCLOSURE_INVALID", f"{label}.source_unit")
    series_scale = units_scale.get(row.get("series_id"))
    if not isinstance(series_scale, dict):
        fail("UNITS_SCALE_MISSING_SERIES", label)
    expected_factor, undone = _derived_units_scale_at(series_scale, as_of_date, label)
    if row.get("normalization_factor") != FMD._decimal_text(expected_factor):
        fail("UNITS_VINTAGE_FACTOR_NOT_DERIVED_FROM_THE_DECLARED_SCALE", label)
    if row.get("normalized_unit") != series_scale.get("normalized_unit"):
        fail("UNITS_VINTAGE_DISCLOSURE_INVALID", f"{label}.normalized_unit")
    block = row.get("units_vintage")
    if (
        not isinstance(block, dict)
        or sorted(block) != sorted(UNITS_VINTAGE_KEYS)
        or block != units_vintage_block(expected_factor, undone)
    ):
        fail("UNITS_VINTAGE_DISCLOSURE_INVALID", label)


def _validate_fred_vintage_binding(
    axes: dict, observed: list[str], requested_date: str,
    source_mode: str = SOURCE_MODE_API, units_scale: dict | None = None,
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

    Containment alone is not enough for VIX, and that is the point of
    ``_validate_vintage_lag``. "At or before the replayed date" admits the
    replayed date itself, which ALFRED's backdated availability offers and the
    live producer could not obtain. The per-axis lag rule is therefore re-required
    here and every observation is bound to the lagged as-of rather than to the
    requested date.
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
        as_of = _validate_vintage_lag(measurement, name, requested_date, source_mode)
        if name == "RISK_VOL":
            _assert_vintage_covers(as_of, measurement, f"{requested_date}:RISK_VOL")
            # Bound to the lagged as-of, not merely to the requested date: an
            # observation dated on the replayed date is exactly the value the
            # live producer did not have. Whether that date is a real calendar
            # day at all is left to ``_validate_measurement_source_dates``, which
            # reports it with its own attributable code -- pre-empting that here
            # would relabel a malformed date as a lag violation.
            observed_on = _calendar_date(measurement.get("observation_date"))
            if (
                as_of != requested_date
                and observed_on is not None
                and observed_on.isoformat() > as_of
            ):
                fail(
                    "RISK_VOL_OBSERVATION_LATER_THAN_ITS_LAGGED_VINTAGE",
                    f"{requested_date}:{as_of}",
                )
            continue
        series = measurement.get("series")
        if not isinstance(series, list) or not series:
            fail("OBSERVED_AXIS_MUST_CARRY_ITS_MEASUREMENT", f"{requested_date}:LIQUIDITY")
        for row in series:
            series_id = row.get("series_id") if isinstance(row, dict) else None
            label = f"{requested_date}:LIQUIDITY.{series_id}"
            _assert_vintage_covers(as_of, row, label)
            _assert_vintage_covers(
                as_of, row, f"{label}.previous", FRED_PREVIOUS_VINTAGE_KEYS,
            )
            # The observation vintages above are bound identically in both
            # modes. Only the units vintage differs, and which of the two rules
            # applies is keyed off the population's declared source mode rather
            # than off anything the row itself says -- a row cannot escape the
            # strict bind by declaring its units derived.
            if source_mode == SOURCE_MODE_EVIDENCE:
                if not isinstance(units_scale, dict):
                    fail("SOURCE_STORE_UNITS_SCALE_MISSING", label)
                _validate_units_vintage_derived(row, label, units_scale, as_of)
            else:
                _assert_vintage_covers(
                    requested_date, row, f"{label}.metadata",
                    FRED_METADATA_VINTAGE_KEYS,
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
        if name in ("BREADTH", "LEADERSHIP"):
            # Re-applied, not trusted: the same session-generation check the
            # builder runs right after the combined fetch.
            _assert_proxy_session_alignment(measurement, label)
            trend = axes.get("TREND")
            trend_measurement = trend.get("measurement") if isinstance(trend, dict) else None
            if "TREND" in observed and (
                not isinstance(trend_measurement, dict)
                or trend_measurement.get("as_of_session_date")
                != measurement.get("as_of_session_date")
            ):
                fail(PROXY_MIXED_SESSION_GENERATION, label)


# Each observed axis's row is rebuilt by the *same* helper that produced it, so
# re-derivation cannot drift from production even if a threshold in one of those
# helpers is later changed upstream. BREADTH/LEADERSHIP read their sub-fields
# out of the shared combined measurement rather than a dedicated one.
AXIS_ROW_FROM_MEASUREMENT = {
    "TREND": lambda measurement: trend_axis_row(measurement.get("trend_etfs")),
    "RISK_VOL": lambda measurement: risk_vol_axis_row(measurement.get("value")),
    "LIQUIDITY": lambda measurement: liquidity_axis_row(measurement.get("series")),
    "BREADTH": lambda measurement: breadth_axis_row(
        (measurement.get("breadth_measurement") or {}).get("advance_fraction")
    ),
    "LEADERSHIP": lambda measurement: leadership_axis_row(
        (measurement.get("leadership_measurement") or {}).get("ordered_groups")
    ),
}


def _rederive_axis_rows(axes: dict, replayed: list[str]) -> list[dict]:
    """Rebuild the candidate rows the record's own observed measurements yield.

    Only axes the record itself calls ``OBSERVED`` contribute, in ``replayed``
    order, which is exactly how ``replay_one_requested_date`` assembles them --
    and, once authorized, exactly ``PRR.AXES`` order, which is what lets
    ``PRR.classify`` actually classify a genuine 5/5 result.
    """
    rows = []
    for name in replayed:
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
    replayed: list[str], excluded: dict,
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
        rows = _rederive_axis_rows(axes, replayed)
        expected = (
            _candidate_normalized_result(rows, expected_effective, policy, excluded)
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
    record: dict, axes: dict, observed: list[str], replayed: list[str], requested_date: str,
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
    expected_keys = _source_hash_keys(replayed)
    expected = {key: None for key in expected_keys}
    for name in replayed:
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
        # BREADTH and LEADERSHIP share one combined measurement/response, so
        # both map to ``BREADTH_LEADERSHIP_RESPONSE_HASH_KEY`` and set it to
        # the same value -- idempotent, not a conflict.
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
    if not isinstance(hashes, dict) or sorted(hashes) != sorted(expected_keys):
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


def _status_for(observed: list[str], replayed: list[str]) -> str:
    if len(observed) == len(replayed):
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
    parser.add_argument(
        "--source-mode", choices=list(SOURCE_MODES), default=SOURCE_MODE_API,
        dest="source_mode",
        help=(
            "Where each date's sources are read from."
            f" {SOURCE_MODE_API} (default) issues live Alpaca/FRED requests."
            f" {SOURCE_MODE_EVIDENCE} reads the committed, content-addressed"
            " evidence/free_market_data/history/ store via"
            " regime/us_replay_evidence_source.py and makes no network request."
        ),
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

    if args.source_mode == SOURCE_MODE_EVIDENCE:
        # Imported here, not at module import: the live-provider path must not
        # acquire a dependency on the evidence reader.
        from regime import us_replay_evidence_source as EVIDENCE

        store = EVIDENCE.open_store(ROOT)
        population = build_population(
            dict(EVIDENCE.EVIDENCE_CREDENTIALS), args.dates,
            getter=EVIDENCE.EvidenceGetter(store),
            source_mode=SOURCE_MODE_EVIDENCE, source_store=store.descriptor(),
        )
    else:
        population = build_population(_credentials_from_env(), args.dates)
    out_path = args.out if args.out is not None else _default_temp_out()
    write_population(population, out_path)
    counts = {status: 0 for status in RECORD_STATUSES}
    for record in population["records"]:
        counts[record["status"]] += 1
    print(json.dumps(
        {
            "out": str(out_path),
            "source_mode": args.source_mode,
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
