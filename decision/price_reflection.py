#!/usr/bin/env python3
"""P8-10 Price Reflection builder — price/volume-only, never fundamentals.

★ CIO review round 2 (`price_reflection/2`) fixed a real defect in round 1:
  a price rally is PRICE MOMENTUM, not evidence that the market has
  "reflected" anything. Reflection is a claim about a specific expectation
  or event — you cannot judge whether price has caught up to something
  without knowing what that something is. Round 1 conflated the two into one
  `status` field and let momentum alone (>=8% => "FULLY_REFLECTED") stand in
  for a reflection judgment with no event/expectation reference at all. This
  module now keeps the two claims structurally separate:

  * `price_state`      — OVEREXTENDED | STRONG_MOMENTUM | MODERATE | WEAK |
    UNKNOWN. A pure, price/volume-only read on momentum and positioning.
    Momentum alone can never produce a reflection verdict — that's the whole
    point of this field existing.
  * `reflection_status` — UNDER_REFLECTED | PARTIALLY_REFLECTED |
    FULLY_REFLECTED | UNKNOWN. Only ever leaves UNKNOWN when a real
    REFERENCE POINT is present (see `_has_reference_point` below: an
    `event_reaction.event_date`, a `reflection_reference.reference_event_id`,
    a `reflection_reference.expectation_as_of`, or a real, caller-supplied
    P8-09 Expectations Gap status via `reflection_reference.
    expectations_gap_status`) AND a comparable direction + momentum exist.
    Abundant, fresh, valid price data with NO reference point still forces
    `reflection_status=UNKNOWN` / `data_state=
    REFLECTION_UNCERTAIN_WITH_VALID_PRICE` — momentum is never a substitute
    for a reference.
  * `data_state`        — PRICE_DATA_MISSING | PRICE_STALE |
    REFLECTION_UNCERTAIN_WITH_VALID_PRICE | VALID. Tracks the REFLECTION
    judgment specifically (mirrors `reflection_status`): `VALID` iff
    `reflection_status != "UNKNOWN"`. This is now a real, structured
    top-level field (not string-parsed out of `reasons` — round 1's
    `reasons[0]=="DATA_STATE:..."` encoding was an accepted stopgap to avoid
    touching `decision/alpha_review.py`'s own strict field-set check; round 2
    updates that module directly instead, see its own docstring).

★ CIO review round 3 (`price_reflection/3`) fixed four further defects
  round 2 left open:

  1. A bare `reflection_reference.expectations_gap_status` STRING (or a bare
     `event_reaction.direction` with no citation) is not a real reference --
     a caller can type `"POSITIVE"` without any actual P8-09 evidence behind
     it. `reflection_reference.expectations_gap_status` is retired; callers
     now pass `reflection_reference.expectations_gap_packet` (the FULL,
     already-built P8-09 packet), which this module independently
     re-validates via `decision/expectations_gap.py`'s own `validate_packet`
     (hash/tamper/vocab) and cross-checks `subject`/`decision_date` against
     this packet's own -- see `_validate_reflection_reference`. Likewise
     `event_reaction.direction` now requires `event_reaction.source_ref` +
     `event_reaction.source_sha256` (real evidence-lineage citation) before
     it counts as usable for a reflection verdict.
  2. `reflection_status` used to compare a generic, "now"-anchored 1-month
     return against an event that could be dated anywhere up to
     `decision_date` -- almost entirely PRE-event movement, yet still fed
     into a reflection judgment. It now requires a real,
     event/reference-anchored `event_reaction.post_event_return_pct` /
     `reflection_reference.post_reference_return_pct` (a return the CALLER
     computed specifically from the reference date forward) -- never the
     generic `recent_return_windows`/`relative_strength` figures. Without
     one, `reflection_status` stays `UNKNOWN`.
  3. `price_state=UNKNOWN` and a non-`UNKNOWN` `reflection_status` can now
     never coexist -- enforced as a hard structural invariant in both
     `_classify` (forces `reflection_status` back to `UNKNOWN` if
     `price_state` came out `UNKNOWN`) and `validate_packet` (raises
     `OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_CONTRADICTION` on any
     packet, however constructed, that violates it).
  4. `classification_thresholds_approval_status="PROVISIONAL"` now actually
     gates `decision/alpha_review.py`'s operational output, not just this
     module's own diagnostic `threshold_basis` field -- see that module's
     own docstring for the `alpha_review/4` change.

★ CIO review round 4 (`price_reflection/4`) found round 3's "evidence
  verification" was still only a FORMAT check -- `source_ref`/`source_sha256`
  were regex-validated but never cross-checked against a real committed
  file, and `post_event_return_pct`/`post_reference_return_pct` were still
  trusted caller-supplied numbers with no real price lookup or PIT check
  behind them. Confirmed reproducible: `source_ref="MADE-UP"`,
  `source_sha256="a"*64`, `post_event_return_pct="99"` (all fabricated, no
  real evidence anywhere) produced a confident `FULLY_REFLECTED`. Closed:

  1. `_verify_evidence_citation` resolves `event_reaction.source_ref` to a
     real file under this repo and independently recomputes its sha256 --
     `source_ref="MADE-UP"` (or any non-existent path, or a real path with a
     wrong hash) now fails verification and the event path cannot unlock a
     reflection verdict (soft-downgrades to `UNKNOWN`, same fail-closed
     posture as a missing citation).
  2. `post_event_return_pct`/`post_reference_return_pct` are RETIRED as
     accepted input. There is no code path anywhere in this module that
     accepts a return percentage from a caller and uses it. The return is
     always computed internally by `_compute_verified_return`, from two
     real, independently looked-up close prices (`decision/price_evidence.
     py`'s `real_close_on_date`/`latest_real_close_at_or_before`, themselves
     built on `replay/price_series.py`/`replay/evidence_index.py`, PR #210 --
     reused, not reimplemented).
  3. Both endpoint prices must be PIT-live-known as of `decision_date`
     (`PriceSeries.live_known_asof`/`live_trading_dates_at_or_before`,
     unchanged PR #210 discipline) -- an evidence row captured after
     `decision_date` can never be used, matching PR #210's/#211's own
     anti-lookahead gate.
  4. The return's START price is always anchored to a real reference
     timestamp -- `event_reaction.event_date` for the event path, or the
     validated P8-09 packet's own `decision_date` for the expectations_gap
     path (echoed as `reflection_reference.expectations_gap_reference_date`)
     -- never an independently caller-chosen window. The END price is
     always the latest real, PIT-live close at or before this packet's own
     `decision_date`.
  5. Any failure at any step (file doesn't exist, hash mismatch, no real
     price evidence for the subject, price row not yet PIT-eligible, no
     genuine forward date gap between start and end) makes the return
     `None` -- there is no fallback to a caller-supplied number, ever;
     `reflection_status` simply stays `UNKNOWN`.

Staleness is still the loudest rule: if `price_as_of` is missing or older
than the freshness ceiling relative to `decision_date`, BOTH `price_state`
and `reflection_status` are forced to `UNKNOWN` regardless of every other
input. This check runs first and short-circuits everything else.

`classification_thresholds` (15%/8%/3%/2%-style cutoffs) have never been
CIO-ratified — `classification_thresholds_approval_status` says so explicitly
in the contract (`"PROVISIONAL"`) and every output packet echoes it verbatim
as `price_reflection.threshold_basis`. A `PROVISIONAL` basis is not a defect
in this module (it is the honest, currently-true state of these numbers) but
IS a signal to every downstream consumer: no `PARTIALLY_REFLECTED`/
`FULLY_REFLECTED`/`OVEREXTENDED`/`STRONG_MOMENTUM` verdict this module ever
emits is a CIO-ratified final call — see `authority` below, which already
sets `rule_authority_substitution_authorized: false` and every trading-path
boolean `false`; `threshold_basis` makes that same "review signal, not a
final determination" property visible on the verdict itself, not just in
the authority block.

It is deliberately blind to thesis quality, conviction, or any fundamental
narrative — the public builder below (`build_packet`) accepts **only**
price/volume/valuation-history/reference-point parameters. There is no
"thesis" or "fundamental strength" parameter anywhere in its signature: it
is structurally impossible to feed this module optimism as an input. Good
fundamentals alone can never produce `UNDER_REFLECTED`, because this module
has no channel through which fundamentals could even arrive.

This module does not fetch evidence itself. It assembles whatever price data
the caller already has into a closed-vocabulary, deterministic,
tamper-evident packet.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "price_reflection_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# `event_reaction.source_ref` is a real repo-relative FILE PATH (round 4),
# not an opaque token -- needs "/" in addition to TOKEN_RE's charset.
SOURCE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,255}$")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ★ CIO round 3, required item 1: a `reflection_reference.
#   expectations_gap_packet` must be independently re-validated (hash,
#   vocab, tamper) against P8-09's OWN builder before its status can ever
#   serve as a reflection reference -- reused unchanged, same cross-module
#   pattern `decision/alpha_review.py` already uses for all three of its
#   upstream packets.
EXPECTATIONS_GAP = _load_module(
    "p8_10_expectations_gap", ROOT / "decision" / "expectations_gap.py"
)
EG_CONTRACT = EXPECTATIONS_GAP.load_contract()

# ★ CIO round 4, required items 2/3/4: the ONLY source of a reflection
#   return figure -- real, PIT-verified close-price lookups against actually
#   committed repo evidence. Reused unchanged, not reimplemented.
PRICE_EVIDENCE = _load_module(
    "p8_10_price_evidence", ROOT / "decision" / "price_evidence.py"
)

# Parameter-name substrings this module's public builder must never contain.
# Enforced both by construction (see build_packet's signature) and by a
# regression test that inspects the live signature at test time, so a future
# edit cannot silently reintroduce fundamental/thesis-shaped scope creep.
FORBIDDEN_PARAMETER_SUBSTRINGS = (
    "thesis", "fundamental", "quality", "conviction", "narrative", "story",
)


class PriceReflectionError(ValueError):
    """Fail-closed P8-10 Price Reflection contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PriceReflectionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 4,
        "contract_version": "price_reflection/4",
        "output_schema_version": "price_reflection_packet/4",
        "allowed_price_state": [
            "OVEREXTENDED", "STRONG_MOMENTUM", "MODERATE", "WEAK", "UNKNOWN",
        ],
        "allowed_reflection_status": [
            "UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED", "UNKNOWN",
        ],
        "allowed_data_state": [
            "PRICE_DATA_MISSING", "PRICE_STALE",
            "REFLECTION_UNCERTAIN_WITH_VALID_PRICE", "VALID",
        ],
        "allowed_confidence": ["LOW", "MEDIUM", "HIGH", "UNKNOWN"],
        "allowed_direction": ["POSITIVE", "NEGATIVE", "NEUTRAL", "UNKNOWN"],
        "allowed_valuation_position": ["LOW", "MID", "HIGH", "UNKNOWN"],
        "allowed_data_source_scope": [
            "IEX_ONLY_PARTIAL_US_MARKET", "KRX_OFFICIAL", "KRAKEN_OHLC", "UNKNOWN",
        ],
        "allowed_threshold_basis": ["PROVISIONAL", "RATIFIED"],
        "korea_data_source_scope": "KRX_OFFICIAL",
        "default_freshness_ceiling_days": 5,
        "classification_thresholds": {
            "rally_min_1m_return_pct": "15", "near_high_max_distance_pct": "3",
            "strong_momentum_min_pct": "8", "mild_momentum_min_pct": "2",
        },
        # ★ CIO round 2, required item 7: these specific cutoff numbers have
        #   never been CIO-ratified (round 1's docs already said so: "the
        #   spec did not name an exact number"). Declared PROVISIONAL here,
        #   verifiable in this contract, and echoed on every output packet
        #   as `price_reflection.threshold_basis` -- never silently upgraded
        #   to RATIFIED by this module itself.
        "classification_thresholds_approval_status": "PROVISIONAL",
        "confidence_thresholds": {
            "high_min_scored_signal_count": 4, "medium_min_scored_signal_count": 2,
        },
        "authority": {
            "price_reflection_assembly_only": True,
            "rule_authority_substitution_authorized": False,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PriceReflectionError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise PriceReflectionError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if expected["classification_thresholds_approval_status"] not in expected["allowed_threshold_basis"]:
        raise PriceReflectionError("CONTRACT_THRESHOLD_APPROVAL_STATUS_INVALID")
    for bad in ("REJECTED",):
        if bad in expected["allowed_price_state"] or bad in expected["allowed_reflection_status"]:
            raise PriceReflectionError("CONTRACT_VOCABULARY_CONTAINS_REJECTED")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise PriceReflectionError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise PriceReflectionError(code) from exc


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise PriceReflectionError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PriceReflectionError(code) from exc
    if parsed.isoformat() != value:
        raise PriceReflectionError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise PriceReflectionError(code)
    return value


def _pct(value, code: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PriceReflectionError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PriceReflectionError(code) from exc
    if not parsed.is_finite():
        raise PriceReflectionError(code)
    return parsed


def _validate_recent_return_windows(value, contract: dict) -> dict:
    fields = {"1m", "3m", "6m"}
    if value is None:
        return {"1m": None, "3m": None, "6m": None}
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("RECENT_RETURN_WINDOWS_FIELDS_INVALID")
    return {key: _pct(value.get(key), f"RECENT_RETURN_WINDOWS_{key}_INVALID") for key in fields}


def _validate_relative_strength(value, contract: dict) -> dict:
    fields = {"vs_market", "vs_sector", "volume_change_pct", "position_vs_recent_high_pct"}
    if value is None:
        return {key: None for key in fields}
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("RELATIVE_STRENGTH_FIELDS_INVALID")
    return {key: _pct(value.get(key), f"RELATIVE_STRENGTH_{key}_INVALID") for key in fields}


def _validate_event_reaction(value, decision_date: dt.date, contract: dict) -> dict:
    """CIO round 3, required item 1: `direction` alone (a bare string) is
    not a real reference -- if the caller declares a direction, it MUST also
    cite real evidence (`source_ref` + `source_sha256`) backing that claim.
    CIO round 4: `post_event_return_pct` is RETIRED as an accepted input --
    a caller-supplied return figure is never trusted (see module docstring
    and `_compute_verified_return`). This validator only checks FORMAT
    (real-file/hash cross-checking happens later, in `_resolve_reflection_
    basis`, where the decision_date/subject needed to look up real evidence
    are both in scope)."""
    fields = {"event_date", "direction", "reaction_magnitude_pct", "source_ref", "source_sha256"}
    if value is None:
        return {
            "event_date": None, "direction": None, "reaction_magnitude_pct": None,
            "source_ref": None, "source_sha256": None,
        }
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("EVENT_REACTION_FIELDS_MISMATCH")
    event_date = None
    if value.get("event_date") is not None:
        event_date = _date(value["event_date"], "EVENT_REACTION_EVENT_DATE_INVALID")
        if event_date > decision_date:
            raise PriceReflectionError("EVENT_REACTION_EVENT_DATE_IN_FUTURE")
    direction = value.get("direction")
    if direction is not None and direction not in contract["allowed_direction"]:
        raise PriceReflectionError("EVENT_REACTION_DIRECTION_INVALID")
    magnitude = _pct(value.get("reaction_magnitude_pct"), "EVENT_REACTION_MAGNITUDE_INVALID")

    source_ref = value.get("source_ref")
    if source_ref is not None and (
        not isinstance(source_ref, str) or SOURCE_REF_RE.fullmatch(source_ref) is None
    ):
        raise PriceReflectionError("EVENT_REACTION_SOURCE_REF_INVALID")
    source_sha256 = value.get("source_sha256")
    if source_sha256 is not None and SHA256_RE.fullmatch(source_sha256) is None:
        raise PriceReflectionError("EVENT_REACTION_SOURCE_SHA256_INVALID")
    # ★ A bare `direction` string is accepted here (structurally valid
    #   input) but is DELIBERATELY not enough on its own to unlock a
    #   reflection verdict -- see `_resolve_reflection_basis`, which
    #   additionally requires `source_ref`/`source_sha256` to resolve to a
    #   REAL committed file with a REAL matching hash (round 4), and a
    #   REAL, internally-computed, PIT-verified return (round 4). Soft
    #   downgrade (still UNKNOWN reflection), not a hard build failure --
    #   the caller may legitimately want to record a direction it observed
    #   without (yet) having a citation.

    return {
        "event_date": event_date.isoformat() if event_date else None,
        "direction": direction,
        "reaction_magnitude_pct": str(magnitude) if magnitude is not None else None,
        "source_ref": source_ref,
        "source_sha256": source_sha256,
    }


def _validate_reflection_reference(value, subject: str, decision_date: dt.date, contract: dict) -> dict:
    """The REFERENCE POINT this module requires before it will ever emit a
    confident `reflection_status` -- see module docstring.

    CIO round 3, required item 1: `expectations_gap_packet` (the FULL,
    already-built P8-09 packet) replaces round 2's bare
    `expectations_gap_status` string. It is independently re-validated via
    `decision/expectations_gap.py`'s own `validate_packet` (hash/tamper/
    vocab) and its `subject` is cross-checked against THIS packet's own --
    a mismatched or unvalidatable packet is rejected outright, never
    silently downgraded to "no reference".

    CIO round 4, required item 4: the EG packet's own `decision_date` is the
    real reference TIMESTAMP the return calculation anchors to (`_compute_
    verified_return`'s `start_date`) -- so it must be at-or-before THIS
    packet's `decision_date` (a genuine past reference point, not the exact
    same instant, which round 3 wrongly required and would leave zero real
    time gap to measure a return over), never in the future. Echoed as
    `expectations_gap_reference_date` for auditability. Required item 2:
    `post_reference_return_pct` is RETIRED as an accepted input -- see
    `_compute_verified_return`."""
    fields = {"reference_event_id", "expectation_as_of", "expectations_gap_packet"}
    if value is None:
        return {
            "reference_event_id": None, "expectation_as_of": None,
            "expectations_gap_status": None, "expectations_gap_packet_sha256": None,
            "expectations_gap_reference_date": None,
        }
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("REFLECTION_REFERENCE_FIELDS_MISMATCH")
    reference_event_id = value.get("reference_event_id")
    if reference_event_id is not None:
        _token(reference_event_id, "REFLECTION_REFERENCE_EVENT_ID_INVALID")
    expectation_as_of = None
    if value.get("expectation_as_of") is not None:
        expectation_as_of = _date(value["expectation_as_of"], "REFLECTION_REFERENCE_EXPECTATION_AS_OF_INVALID")
        if expectation_as_of > decision_date:
            raise PriceReflectionError("REFLECTION_REFERENCE_EXPECTATION_AS_OF_IN_FUTURE")

    gap_status = None
    gap_sha256 = None
    gap_reference_date = None
    eg_packet = value.get("expectations_gap_packet")
    if eg_packet is not None:
        if not isinstance(eg_packet, dict):
            raise PriceReflectionError("REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_INVALID")
        try:
            validated = EXPECTATIONS_GAP.validate_packet(eg_packet, EG_CONTRACT)
        except EXPECTATIONS_GAP.ExpectationsGapError as exc:
            raise PriceReflectionError(f"REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_INVALID:{exc}") from exc
        if validated["subject"] != subject:
            raise PriceReflectionError("REFLECTION_REFERENCE_EXPECTATIONS_GAP_SUBJECT_MISMATCH")
        if validated["decision_date"] > decision_date.isoformat():
            raise PriceReflectionError("REFLECTION_REFERENCE_EXPECTATIONS_GAP_DECISION_DATE_IN_FUTURE")
        gap_status = validated["expectations_gap"]["status"]
        gap_sha256 = validated["packet_sha256"]
        gap_reference_date = validated["decision_date"]

    return {
        "reference_event_id": reference_event_id,
        "expectation_as_of": expectation_as_of.isoformat() if expectation_as_of else None,
        "expectations_gap_status": gap_status,
        "expectations_gap_packet_sha256": gap_sha256,
        "expectations_gap_reference_date": gap_reference_date,
    }


def _validate_valuation_context(value, contract: dict) -> dict:
    fields = {"metric_type", "position_in_range"}
    if value is None:
        return {"metric_type": None, "position_in_range": None}
    if not isinstance(value, dict) or not set(value).issubset(fields):
        raise PriceReflectionError("VALUATION_CONTEXT_FIELDS_MISMATCH")
    metric_type = value.get("metric_type")
    if metric_type is not None:
        _token(metric_type, "VALUATION_CONTEXT_METRIC_TYPE_INVALID")
    position = value.get("position_in_range")
    if position is not None and position not in contract["allowed_valuation_position"]:
        raise PriceReflectionError("VALUATION_CONTEXT_POSITION_INVALID")
    return {"metric_type": metric_type, "position_in_range": position}


def _render_or_unknown(value: Decimal | None) -> str:
    return "UNKNOWN" if value is None else str(value)


def _has_reference_point(event: dict, reference: dict) -> bool:
    """CIO round 2, required item 2: a reflection judgment requires a
    reference point for WHAT is supposed to be reflected. At least one of
    four real signals must be present -- an event date, an explicit
    reference-event id, an expectation-capture date, or a real,
    independently-re-validated P8-09 Expectations Gap status (not UNKNOWN,
    since an unknown gap has nothing to compare price against either).
    This is a NECESSARY but not SUFFICIENT condition -- see
    `_resolve_reflection_basis` for the full, lineage-and-momentum-verified
    bar a reflection VERDICT additionally requires (round 3)."""
    return (
        event["event_date"] is not None
        or reference["reference_event_id"] is not None
        or reference["expectation_as_of"] is not None
        or reference["expectations_gap_status"] not in (None, "UNKNOWN")
    )


def _verify_evidence_citation(source_ref: str, source_sha256: str) -> bool:
    """CIO round 4, required item 1: `source_ref` must resolve to a REAL
    file actually committed in this repo, and `source_sha256` must equal
    that file's REAL, independently recomputed sha256 -- format validity
    (checked earlier, at `_validate_event_reaction` time) proves nothing on
    its own, as the CIO's exact repro demonstrated (`source_ref="MADE-UP"`,
    an arbitrary 64-hex-char `source_sha256`). Fails closed (`False`) on any
    non-existent path, any path that escapes the repo root, or any hash
    mismatch -- never raises, since an unverifiable citation is meant to
    softly leave `reflection_status=UNKNOWN`, not crash the build."""
    try:
        candidate = (ROOT / source_ref).resolve()
        candidate.relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return False
    if not candidate.is_file():
        return False
    try:
        real_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return False
    return real_hash == source_sha256


def _compute_verified_return(subject: str, start_date: str, decision_date: str) -> Decimal | None:
    """CIO round 4, required items 2/3/4: the ONLY way a return figure can
    ever enter this module -- computed HERE, from two real, independently
    looked-up, PIT-verified close prices. Never a caller-supplied scalar.

    `start_date` must already be a real reference timestamp (the caller of
    this function anchors it to `event_reaction.event_date` or the
    validated P8-09 packet's own `decision_date` -- see
    `_resolve_reflection_basis`), not an independently chosen window.
    `end_date` is always the latest real, PIT-live close at or before
    `decision_date`. Both lookups go through `decision/price_evidence.py`'s
    `real_close_on_date`/`latest_real_close_at_or_before`, which only ever
    return a value when the underlying evidence row is genuinely committed
    AND was captured on/before `decision_date` (PR #210's PIT discipline,
    reused unchanged). Returns `None` -- never a fallback number -- if
    either endpoint isn't reconstructable, or if there is no genuine
    forward date gap between them."""
    start_close = PRICE_EVIDENCE.real_close_on_date(subject, start_date, decision_date)
    if start_close is None or start_close == 0:
        return None
    end_date, end_close = PRICE_EVIDENCE.latest_real_close_at_or_before(subject, decision_date)
    if end_date is None or end_close is None:
        return None
    if end_date <= start_date:
        return None
    return (end_close / start_close - 1) * 100


def _resolve_reflection_basis(
    *, subject: str, decision_date: str, event: dict, reference: dict,
) -> tuple[str | None, Decimal | None, str | None]:
    """CIO round 3+4, required items 1-4: returns `(direction, computed_
    return, source)` -- and ONLY a non-`None` triple -- when a fully
    real-evidence-verified, event/reference-ANCHORED basis exists:

    * event_reaction path: `direction` is POSITIVE/NEGATIVE AND `event_date`
      is present AND `source_ref`/`source_sha256` resolve to a REAL,
      hash-matching committed file (`_verify_evidence_citation`) AND a real,
      PIT-verified return is computable from `event_date` forward
      (`_compute_verified_return`).
    * expectations_gap path: `expectations_gap_status` is POSITIVE/NEGATIVE
      (only ever set after `_validate_reflection_reference` has already
      independently re-validated the full upstream P8-09 packet's hash and
      cross-checked its subject -- never a bare caller string) AND a real,
      PIT-verified return is computable from the validated packet's own
      `decision_date` (`expectations_gap_reference_date`) forward.

    event_reaction is preferred when both are independently satisfiable.
    Returns `(None, None, None)` otherwise -- including when a reference
    point technically exists (`_has_reference_point` is True) but isn't
    fully reconstructable from real evidence, which still correctly yields
    `reflection_status=UNKNOWN` downstream (never a fabricated fallback)."""
    if (
        event["direction"] in ("POSITIVE", "NEGATIVE")
        and event["event_date"] is not None
        and event["source_ref"] is not None
        and event["source_sha256"] is not None
        and _verify_evidence_citation(event["source_ref"], event["source_sha256"])
    ):
        computed = _compute_verified_return(subject, event["event_date"], decision_date)
        if computed is not None:
            return event["direction"], computed, "EVENT_REACTION"
    if (
        reference["expectations_gap_status"] in ("POSITIVE", "NEGATIVE")
        and reference["expectations_gap_reference_date"] is not None
    ):
        computed = _compute_verified_return(
            subject, reference["expectations_gap_reference_date"], decision_date
        )
        if computed is not None:
            return reference["expectations_gap_status"], computed, "EXPECTATIONS_GAP"
    return None, None, None


def _price_state(
    *, m1: Decimal | None, rs_market: Decimal | None, pos_high: Decimal | None,
    volume_change: Decimal | None, val_pos: str | None, contract: dict,
) -> tuple[str, list[str], int]:
    """Pure, price/volume-only momentum read. NEVER produces a reflection
    verdict -- see module docstring for why this is now structurally
    separate from `_reflection_status`."""
    reasons: list[str] = []
    for label, val in (
        ("1m_return", m1), ("vs_market", rs_market),
        ("position_vs_recent_high_pct", pos_high), ("volume_change_pct", volume_change),
    ):
        if val is not None:
            reasons.append(f"{label}:{val}")
    if val_pos is not None:
        reasons.append(f"valuation_position_in_range:{val_pos}")

    scored_signals = sum(
        signal is not None for signal in (m1, rs_market, pos_high, volume_change, val_pos)
    )

    thresholds = contract["classification_thresholds"]
    rally_threshold = Decimal(thresholds["rally_min_1m_return_pct"])
    near_high_threshold = Decimal(thresholds["near_high_max_distance_pct"])
    strong_threshold = Decimal(thresholds["strong_momentum_min_pct"])
    mild_threshold = Decimal(thresholds["mild_momentum_min_pct"])

    rally = m1 is not None and m1 >= rally_threshold
    near_high = pos_high is not None and pos_high <= near_high_threshold
    expensive = val_pos == "HIGH"
    if rally and (near_high or expensive):
        reasons.append("RALLY_AND_STRETCHED_POSITIONING")
        return "OVEREXTENDED", reasons, scored_signals

    momentum_values = [v for v in (m1, rs_market) if v is not None]
    momentum = sum(momentum_values) / len(momentum_values) if momentum_values else None
    if momentum is None or scored_signals < 2:
        reasons.append(f"INSUFFICIENT_PRICE_SIGNALS:scored_count={scored_signals}")
        return "UNKNOWN", reasons, scored_signals

    reasons.append(f"momentum_avg:{momentum}")
    if momentum >= strong_threshold:
        return "STRONG_MOMENTUM", reasons, scored_signals
    if momentum >= mild_threshold:
        return "MODERATE", reasons, scored_signals
    return "WEAK", reasons, scored_signals


def _reflection_status(
    *, subject: str, decision_date: str, event: dict, reference: dict, contract: dict,
) -> tuple[str, list[str], Decimal | None, str | None]:
    """Only ever leaves `UNKNOWN` when a real, evidence-verified reference
    point AND a comparable direction AND a real, internally-COMPUTED,
    PIT-verified return are all present -- see module docstring and
    `_resolve_reflection_basis`. Deliberately takes NO generic momentum
    input (no `m1`/`rs_market`) and NO caller-supplied return figure -- CIO
    round 3/4: a generic, "now"-anchored trailing return can be almost
    entirely pre-event movement, and a caller-supplied number can be
    entirely fabricated; only a return this module computed itself, from
    real evidence, anchored to the real reference date, may ever grade
    reflection. Returns `(status, reasons, computed_return, source)` --
    the last two are `None` unless a confident status was reached, so
    `build_packet` can render exactly what was verified."""
    reasons: list[str] = []
    if not _has_reference_point(event, reference):
        reasons.append("NO_REFLECTION_REFERENCE_POINT")
        return "UNKNOWN", reasons, None, None

    direction, computed_return, source = _resolve_reflection_basis(
        subject=subject, decision_date=decision_date, event=event, reference=reference,
    )
    if direction is None or computed_return is None:
        reasons.append(
            "REFERENCE_POINT_PRESENT_BUT_NOT_RECONSTRUCTABLE_FROM_REAL_EVIDENCE"
        )
        return "UNKNOWN", reasons, None, None

    reasons.append(f"reflection_basis_source:{source}")
    reasons.append(f"reflection_effective_direction:{direction}")
    reasons.append(f"verified_return_pct:{computed_return}")

    thresholds = contract["classification_thresholds"]
    strong_threshold = Decimal(thresholds["strong_momentum_min_pct"])
    mild_threshold = Decimal(thresholds["mild_momentum_min_pct"])

    agrees = (direction == "POSITIVE" and computed_return > 0) or (
        direction == "NEGATIVE" and computed_return < 0
    )
    if not agrees or abs(computed_return) < mild_threshold:
        reasons.append("VERIFIED_RETURN_HAS_NOT_CAUGHT_UP_TO_REFERENCE")
        return "UNDER_REFLECTED", reasons, computed_return, source
    if abs(computed_return) >= strong_threshold:
        reasons.append("VERIFIED_RETURN_STRONGLY_AGREES_WITH_REFERENCE")
        return "FULLY_REFLECTED", reasons, computed_return, source
    reasons.append("VERIFIED_RETURN_PARTIALLY_AGREES_WITH_REFERENCE")
    return "PARTIALLY_REFLECTED", reasons, computed_return, source


def _classify(
    *,
    subject: str,
    price_as_of: str | None,
    decision_date: dt.date,
    freshness_ceiling_days: int,
    windows: dict,
    strength: dict,
    event: dict,
    reference: dict,
    valuation: dict,
    contract: dict,
) -> tuple[str, str, str, str, list[str], Decimal | None, str | None]:
    """Pure, deterministic classification. Rule 1 (staleness) always runs
    first and, if triggered, short-circuits everything else -- both
    `price_state` and `reflection_status` are forced UNKNOWN.

    Returns (price_state, reflection_status, confidence, data_state,
    reasons, verified_return, verified_return_source)."""
    if price_as_of is None:
        return "UNKNOWN", "UNKNOWN", "UNKNOWN", "PRICE_DATA_MISSING", ["PRICE_AS_OF_MISSING"], None, None

    price_as_of_dt = _utc(price_as_of, "PRICE_AS_OF_INVALID")
    if price_as_of_dt.date() > decision_date:
        raise PriceReflectionError("PRICE_AS_OF_IN_FUTURE")
    age_days = (decision_date - price_as_of_dt.date()).days
    if age_days > freshness_ceiling_days:
        return "UNKNOWN", "UNKNOWN", "UNKNOWN", "PRICE_STALE", [
            f"PRICE_AS_OF_STALE:age_days={age_days}:ceiling_days={freshness_ceiling_days}"
        ], None, None

    m1 = windows["1m"]
    rs_market = strength["vs_market"]
    pos_high = strength["position_vs_recent_high_pct"]
    volume_change = strength["volume_change_pct"]
    val_pos = valuation["position_in_range"] if valuation["position_in_range"] != "UNKNOWN" else None

    price_state, price_reasons, scored_signals = _price_state(
        m1=m1, rs_market=rs_market, pos_high=pos_high,
        volume_change=volume_change, val_pos=val_pos, contract=contract,
    )
    reflection_status, reflection_reasons, verified_return, verified_return_source = _reflection_status(
        subject=subject, decision_date=decision_date.isoformat(),
        event=event, reference=reference, contract=contract,
    )

    # ★ CIO round 3, required item 3: price_state=UNKNOWN and a non-UNKNOWN
    #   reflection_status must never coexist -- a hard structural invariant,
    #   not a case-by-case fix. If the momentum/price basis itself couldn't
    #   be classified, reflection cannot be confidently anything either,
    #   REGARDLESS of what _reflection_status independently computed (e.g.
    #   an event-anchored verified return with zero generic price signal
    #   otherwise). Re-asserted unconditionally in validate_packet() below
    #   too, so this can never be reintroduced by a future edit or bypassed
    #   by tampering.
    if price_state == "UNKNOWN" and reflection_status != "UNKNOWN":
        reflection_status = "UNKNOWN"
        reflection_reasons = ["PRICE_STATE_UNKNOWN_BLOCKS_REFLECTION_VERDICT"] + reflection_reasons
        verified_return, verified_return_source = None, None

    reasons = [f"price_state={price_state}"] + price_reasons + \
        [f"reflection_status={reflection_status}"] + reflection_reasons

    data_state = "VALID" if reflection_status != "UNKNOWN" else "REFLECTION_UNCERTAIN_WITH_VALID_PRICE"

    if reflection_status == "UNKNOWN":
        confidence = "UNKNOWN"
    else:
        conf_t = contract["confidence_thresholds"]
        if scored_signals >= conf_t["high_min_scored_signal_count"]:
            confidence = "HIGH"
        elif scored_signals >= conf_t["medium_min_scored_signal_count"]:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

    return price_state, reflection_status, confidence, data_state, reasons, verified_return, verified_return_source


def build_packet(
    *,
    subject: str,
    decision_date: str,
    generated_at: str,
    price_as_of: str | None = None,
    freshness_ceiling_days: int | None = None,
    relative_strength: dict | None = None,
    recent_return_windows: dict | None = None,
    event_reaction: dict | None = None,
    reflection_reference: dict | None = None,
    valuation_context: dict | None = None,
    data_source_scope: str | None = None,
    contract: dict | None = None,
) -> dict:
    """Build a Price Reflection packet.

    Every parameter above is price, volume, relative-strength, event-
    reaction, reflection-reference-point, or valuation-history data (or
    plumbing: subject/dates/contract). There is no thesis-quality or
    fundamental-strength parameter — see FORBIDDEN_PARAMETER_SUBSTRINGS and
    test_price_reflection.py for the signature-inspection regression that
    guards this.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    subject_checked = _token(subject, "SUBJECT_INVALID")
    decision_date_checked = _date(decision_date, "DECISION_DATE_INVALID")
    _utc(generated_at, "GENERATED_AT_INVALID")

    scope = data_source_scope if data_source_scope is not None else "UNKNOWN"
    if scope not in contract["allowed_data_source_scope"]:
        raise PriceReflectionError("DATA_SOURCE_SCOPE_INVALID")

    ceiling = (
        contract["default_freshness_ceiling_days"]
        if freshness_ceiling_days is None else freshness_ceiling_days
    )
    if type(ceiling) is not int or ceiling < 0:
        raise PriceReflectionError("FRESHNESS_CEILING_DAYS_INVALID")

    if price_as_of is not None:
        _utc(price_as_of, "PRICE_AS_OF_INVALID")

    windows = _validate_recent_return_windows(recent_return_windows, contract)
    strength = _validate_relative_strength(relative_strength, contract)
    event = _validate_event_reaction(event_reaction, decision_date_checked, contract)
    reference = _validate_reflection_reference(
        reflection_reference, subject_checked, decision_date_checked, contract
    )
    valuation = _validate_valuation_context(valuation_context, contract)

    price_state, reflection_status, confidence, data_state, reasons, verified_return, verified_return_source = _classify(
        subject=subject_checked,
        price_as_of=price_as_of,
        decision_date=decision_date_checked,
        freshness_ceiling_days=ceiling,
        windows=windows,
        strength=strength,
        event=event,
        reference=reference,
        valuation=valuation,
        contract=contract,
    )

    missing_inputs = sorted(name for name, val in (
        ("price_as_of", price_as_of),
        ("relative_strength", relative_strength),
        ("recent_return_windows", recent_return_windows),
        ("event_reaction", event_reaction),
        ("reflection_reference", reflection_reference),
        ("valuation_context", valuation_context),
    ) if val is None)

    price_reflection = {
        "price_state": price_state,
        "reflection_status": reflection_status,
        "confidence": confidence,
        "data_state": data_state,
        "threshold_basis": contract["classification_thresholds_approval_status"],
        "price_as_of": price_as_of if price_as_of is not None else "UNKNOWN",
        "relative_strength": {
            "vs_market": _render_or_unknown(strength["vs_market"]),
            "vs_sector": _render_or_unknown(strength["vs_sector"]),
            "volume_change_pct": _render_or_unknown(strength["volume_change_pct"]),
            "position_vs_recent_high_pct": _render_or_unknown(strength["position_vs_recent_high_pct"]),
        },
        "recent_return_windows": {
            "1m": _render_or_unknown(windows["1m"]),
            "3m": _render_or_unknown(windows["3m"]),
            "6m": _render_or_unknown(windows["6m"]),
        },
        "event_reaction": {
            "event_date": event["event_date"] or "UNKNOWN",
            "direction": event["direction"] or "UNKNOWN",
            "reaction_magnitude_pct": event["reaction_magnitude_pct"] or "UNKNOWN",
            "source_ref": event["source_ref"] or "UNKNOWN",
            "source_sha256": event["source_sha256"] or "UNKNOWN",
            "verified_post_event_return_pct": (
                str(verified_return) if verified_return_source == "EVENT_REACTION" else "UNKNOWN"
            ),
        },
        "reflection_reference": {
            "reference_event_id": reference["reference_event_id"] or "UNKNOWN",
            "expectation_as_of": reference["expectation_as_of"] or "UNKNOWN",
            "expectations_gap_status": reference["expectations_gap_status"] or "UNKNOWN",
            "expectations_gap_packet_sha256": reference["expectations_gap_packet_sha256"] or "UNKNOWN",
            "expectations_gap_reference_date": reference["expectations_gap_reference_date"] or "UNKNOWN",
            "verified_post_reference_return_pct": (
                str(verified_return) if verified_return_source == "EXPECTATIONS_GAP" else "UNKNOWN"
            ),
        },
        "valuation_context": {
            "metric_type": valuation["metric_type"] or "UNKNOWN",
            "position_in_range": valuation["position_in_range"] or "UNKNOWN",
        },
        "reasons": reasons,
        "missing_inputs": missing_inputs,
        "data_source_scope": scope,
    }

    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": subject_checked,
        "decision_date": decision_date,
        "price_reflection": price_reflection,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "generated_at", "subject",
        "decision_date", "price_reflection", "authority", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise PriceReflectionError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("authority") != contract["authority"]
    ):
        raise PriceReflectionError("OUTPUT_IDENTITY_INVALID")
    _utc(packet.get("generated_at"), "OUTPUT_GENERATED_AT_INVALID")
    _token(packet.get("subject"), "OUTPUT_SUBJECT_INVALID")
    _date(packet.get("decision_date"), "OUTPUT_DECISION_DATE_INVALID")

    pr = packet.get("price_reflection")
    pr_fields = {
        "price_state", "reflection_status", "confidence", "data_state", "threshold_basis",
        "price_as_of", "relative_strength", "recent_return_windows", "event_reaction",
        "reflection_reference", "valuation_context", "reasons", "missing_inputs",
        "data_source_scope",
    }
    if not isinstance(pr, dict) or set(pr) != pr_fields:
        raise PriceReflectionError("OUTPUT_PRICE_REFLECTION_FIELDS_MISMATCH")

    price_state = pr.get("price_state")
    reflection_status = pr.get("reflection_status")
    confidence = pr.get("confidence")
    data_state = pr.get("data_state")
    threshold_basis = pr.get("threshold_basis")

    if price_state not in contract["allowed_price_state"]:
        raise PriceReflectionError("OUTPUT_PRICE_STATE_INVALID")
    if reflection_status not in contract["allowed_reflection_status"]:
        raise PriceReflectionError("OUTPUT_REFLECTION_STATUS_INVALID")
    if confidence not in contract["allowed_confidence"]:
        raise PriceReflectionError("OUTPUT_CONFIDENCE_INVALID")
    if reflection_status == "UNKNOWN" and confidence != "UNKNOWN":
        raise PriceReflectionError("OUTPUT_UNKNOWN_REFLECTION_STATUS_REQUIRES_UNKNOWN_CONFIDENCE")
    if "REJECTED" in contract["allowed_price_state"] or "REJECTED" in contract["allowed_reflection_status"]:
        raise PriceReflectionError("OUTPUT_VOCABULARY_CONTAINS_REJECTED")  # defensive

    if data_state not in contract["allowed_data_state"]:
        raise PriceReflectionError("OUTPUT_DATA_STATE_INVALID")
    if (reflection_status == "UNKNOWN") != (data_state != "VALID"):
        raise PriceReflectionError("OUTPUT_DATA_STATE_REFLECTION_STATUS_MISMATCH")

    # ★ CIO round 3, required item 3: structural invariant, re-asserted here
    #   independent of _classify's own enforcement -- price_state=UNKNOWN
    #   and a non-UNKNOWN reflection_status can never coexist in ANY packet
    #   this function accepts, however it was constructed or tampered with.
    if price_state == "UNKNOWN" and reflection_status != "UNKNOWN":
        raise PriceReflectionError("OUTPUT_PRICE_STATE_UNKNOWN_REFLECTION_STATUS_CONTRADICTION")

    if threshold_basis != contract["classification_thresholds_approval_status"]:
        raise PriceReflectionError("OUTPUT_THRESHOLD_BASIS_MISMATCH")

    data_source_scope = pr.get("data_source_scope")
    if data_source_scope not in contract["allowed_data_source_scope"]:
        raise PriceReflectionError("OUTPUT_DATA_SOURCE_SCOPE_INVALID")

    reasons = pr.get("reasons")
    if not isinstance(reasons, list) or not reasons or any(
        not isinstance(item, str) or not item.strip() for item in reasons
    ):
        raise PriceReflectionError("OUTPUT_REASONS_INVALID")

    missing = pr.get("missing_inputs")
    allowed_missing = {
        "price_as_of", "relative_strength", "recent_return_windows",
        "event_reaction", "reflection_reference", "valuation_context",
    }
    if (
        not isinstance(missing, list)
        or missing != sorted(set(missing))
        or any(item not in allowed_missing for item in missing)
    ):
        raise PriceReflectionError("OUTPUT_MISSING_INPUTS_INVALID")
    if ("price_as_of" in missing) != (pr.get("price_as_of") == "UNKNOWN"):
        raise PriceReflectionError("OUTPUT_MISSING_INPUTS_PRICE_AS_OF_MISMATCH")
    if "price_as_of" in missing and data_state != "PRICE_DATA_MISSING":
        raise PriceReflectionError("OUTPUT_MISSING_PRICE_AS_OF_MUST_BE_PRICE_DATA_MISSING")

    rs = pr.get("relative_strength")
    if not isinstance(rs, dict) or set(rs) != {
        "vs_market", "vs_sector", "volume_change_pct", "position_vs_recent_high_pct"
    }:
        raise PriceReflectionError("OUTPUT_RELATIVE_STRENGTH_FIELDS_MISMATCH")
    for value in rs.values():
        if value != "UNKNOWN":
            _pct(value, "OUTPUT_RELATIVE_STRENGTH_VALUE_INVALID")

    rw = pr.get("recent_return_windows")
    if not isinstance(rw, dict) or set(rw) != {"1m", "3m", "6m"}:
        raise PriceReflectionError("OUTPUT_RECENT_RETURN_WINDOWS_FIELDS_MISMATCH")
    for value in rw.values():
        if value != "UNKNOWN":
            _pct(value, "OUTPUT_RECENT_RETURN_WINDOWS_VALUE_INVALID")

    er = pr.get("event_reaction")
    if not isinstance(er, dict) or set(er) != {
        "event_date", "direction", "reaction_magnitude_pct",
        "source_ref", "source_sha256", "verified_post_event_return_pct",
    }:
        raise PriceReflectionError("OUTPUT_EVENT_REACTION_FIELDS_MISMATCH")
    if er["event_date"] != "UNKNOWN":
        _date(er["event_date"], "OUTPUT_EVENT_REACTION_EVENT_DATE_INVALID")
        if _date(er["event_date"], "OUTPUT_EVENT_REACTION_EVENT_DATE_INVALID") > _date(
            packet["decision_date"], "OUTPUT_DECISION_DATE_INVALID"
        ):
            raise PriceReflectionError("OUTPUT_EVENT_REACTION_EVENT_DATE_IN_FUTURE")
    if er["direction"] not in contract["allowed_direction"] + ["UNKNOWN"]:
        raise PriceReflectionError("OUTPUT_EVENT_REACTION_DIRECTION_INVALID")
    if er["reaction_magnitude_pct"] != "UNKNOWN":
        _pct(er["reaction_magnitude_pct"], "OUTPUT_EVENT_REACTION_MAGNITUDE_INVALID")
    if er["source_sha256"] != "UNKNOWN" and SHA256_RE.fullmatch(er["source_sha256"]) is None:
        raise PriceReflectionError("OUTPUT_EVENT_REACTION_SOURCE_SHA256_INVALID")
    if er["verified_post_event_return_pct"] != "UNKNOWN":
        _pct(er["verified_post_event_return_pct"], "OUTPUT_EVENT_REACTION_VERIFIED_RETURN_INVALID")
        # ★ CIO round 4: a verified event-anchored return can only ever be
        #   present when reflection_status is confident AND the source was
        #   genuinely the event_reaction path -- structural consistency,
        #   never independently settable by a caller (this whole sub-field
        #   is compute-only, never accepted as build_packet input).
        if reflection_status == "UNKNOWN":
            raise PriceReflectionError("OUTPUT_VERIFIED_RETURN_REQUIRES_NON_UNKNOWN_REFLECTION_STATUS")

    rr = pr.get("reflection_reference")
    if not isinstance(rr, dict) or set(rr) != {
        "reference_event_id", "expectation_as_of", "expectations_gap_status",
        "expectations_gap_packet_sha256", "expectations_gap_reference_date",
        "verified_post_reference_return_pct",
    }:
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_FIELDS_MISMATCH")
    if rr["expectation_as_of"] != "UNKNOWN":
        _date(rr["expectation_as_of"], "OUTPUT_REFLECTION_REFERENCE_EXPECTATION_AS_OF_INVALID")
    if rr["expectations_gap_status"] not in contract["allowed_direction"] + ["UNKNOWN"]:
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_EXPECTATIONS_GAP_STATUS_INVALID")
    if rr["expectations_gap_packet_sha256"] != "UNKNOWN" and SHA256_RE.fullmatch(
        rr["expectations_gap_packet_sha256"]
    ) is None:
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_EXPECTATIONS_GAP_PACKET_SHA256_INVALID")
    if rr["expectations_gap_reference_date"] != "UNKNOWN":
        _date(rr["expectations_gap_reference_date"], "OUTPUT_REFLECTION_REFERENCE_REFERENCE_DATE_INVALID")
    if rr["verified_post_reference_return_pct"] != "UNKNOWN":
        _pct(rr["verified_post_reference_return_pct"], "OUTPUT_REFLECTION_REFERENCE_VERIFIED_RETURN_INVALID")
        if reflection_status == "UNKNOWN":
            raise PriceReflectionError("OUTPUT_VERIFIED_RETURN_REQUIRES_NON_UNKNOWN_REFLECTION_STATUS")
    if rr["expectations_gap_status"] != "UNKNOWN" and rr["expectations_gap_packet_sha256"] == "UNKNOWN":
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_EXPECTATIONS_GAP_STATUS_REQUIRES_VERIFIED_PACKET")

    vc = pr.get("valuation_context")
    if not isinstance(vc, dict) or set(vc) != {"metric_type", "position_in_range"}:
        raise PriceReflectionError("OUTPUT_VALUATION_CONTEXT_FIELDS_MISMATCH")
    if vc["position_in_range"] not in contract["allowed_valuation_position"] + ["UNKNOWN"]:
        raise PriceReflectionError("OUTPUT_VALUATION_CONTEXT_POSITION_INVALID")

    if pr.get("price_as_of") != "UNKNOWN":
        _utc(pr["price_as_of"], "OUTPUT_PRICE_AS_OF_INVALID")

    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise PriceReflectionError("OUTPUT_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise PriceReflectionError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def assert_no_fundamental_parameters() -> None:
    """Structural guard: the public builder must never accept a thesis/
    fundamental-strength parameter. Used by the CIO-facing regression test."""
    params = list(inspect.signature(build_packet).parameters)
    offending = [
        name for name in params
        if any(bad in name.lower() for bad in FORBIDDEN_PARAMETER_SUBSTRINGS)
    ]
    if offending:
        raise PriceReflectionError(f"FORBIDDEN_PARAMETER_PRESENT:{offending}")


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise PriceReflectionError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, output_path: Path) -> int:
    try:
        envelope = _read_json(input_path)
        if not isinstance(envelope, dict):
            raise PriceReflectionError("INPUT_ENVELOPE_NOT_OBJECT")
        packet = build_packet(**envelope)
        write_json_atomic(output_path, packet)
        return 0
    except (PriceReflectionError, OSError, TypeError, ValueError) as exc:
        print(f"Price Reflection build failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
