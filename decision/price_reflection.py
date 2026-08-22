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
        "schema_version": 2,
        "contract_version": "price_reflection/2",
        "output_schema_version": "price_reflection_packet/2",
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
    fields = {"event_date", "direction", "reaction_magnitude_pct"}
    if value is None:
        return {"event_date": None, "direction": None, "reaction_magnitude_pct": None}
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
    return {
        "event_date": event_date.isoformat() if event_date else None,
        "direction": direction,
        "reaction_magnitude_pct": str(magnitude) if magnitude is not None else None,
    }


def _validate_reflection_reference(value, decision_date: dt.date, contract: dict) -> dict:
    """The REFERENCE POINT this module requires before it will ever emit a
    confident `reflection_status` -- see module docstring. All three fields
    are optional individually; `_has_reference_point` below only needs one
    of them (or `event_reaction.event_date`) to be present."""
    fields = {"reference_event_id", "expectation_as_of", "expectations_gap_status"}
    if value is None:
        return {"reference_event_id": None, "expectation_as_of": None, "expectations_gap_status": None}
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
    gap_status = value.get("expectations_gap_status")
    if gap_status is not None and gap_status not in contract["allowed_direction"]:
        # Reuses the SAME closed vocabulary decision/expectations_gap.py's
        # own `status` field uses (POSITIVE/NEGATIVE/NEUTRAL/UNKNOWN) --
        # this is a real pass-through of an already-validated P8-09 packet's
        # status, not a new fabricated vocabulary.
        raise PriceReflectionError("REFLECTION_REFERENCE_EXPECTATIONS_GAP_STATUS_INVALID")
    return {
        "reference_event_id": reference_event_id,
        "expectation_as_of": expectation_as_of.isoformat() if expectation_as_of else None,
        "expectations_gap_status": gap_status,
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
    reference-event id, an expectation-capture date, or a real P8-09
    Expectations Gap status (not UNKNOWN, since an unknown gap has nothing
    to compare price against either)."""
    return (
        event["event_date"] is not None
        or reference["reference_event_id"] is not None
        or reference["expectation_as_of"] is not None
        or reference["expectations_gap_status"] not in (None, "UNKNOWN")
    )


def _effective_reference_direction(event: dict, reference: dict) -> str | None:
    """The directional claim the reference point makes -- what was actually
    expected, so momentum can be compared against it. Prefers a direct event
    reaction; falls back to a real Expectations Gap status. Returns None if
    neither resolves to a comparable POSITIVE/NEGATIVE claim (e.g. only a
    bare `reference_event_id`/`expectation_as_of` was supplied with no
    directional content) -- see module docstring: a reference point alone,
    without a direction to compare against, still cannot support a confident
    reflection verdict."""
    if event["direction"] in ("POSITIVE", "NEGATIVE"):
        return event["direction"]
    if reference["expectations_gap_status"] in ("POSITIVE", "NEGATIVE"):
        return reference["expectations_gap_status"]
    return None


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
    *, event: dict, reference: dict, m1: Decimal | None, rs_market: Decimal | None, contract: dict,
) -> tuple[str, list[str]]:
    """Only ever leaves `UNKNOWN` when a real reference point AND a
    comparable direction AND real momentum are all present -- see module
    docstring. Momentum magnitude alone (no matter how large) is never
    sufficient on its own."""
    reasons: list[str] = []
    if not _has_reference_point(event, reference):
        reasons.append("NO_REFLECTION_REFERENCE_POINT")
        return "UNKNOWN", reasons

    effective_direction = _effective_reference_direction(event, reference)
    momentum_values = [v for v in (m1, rs_market) if v is not None]
    momentum = sum(momentum_values) / len(momentum_values) if momentum_values else None
    if effective_direction is None or momentum is None:
        reasons.append("REFERENCE_POINT_PRESENT_BUT_NO_COMPARABLE_DIRECTION_OR_MOMENTUM")
        return "UNKNOWN", reasons

    reasons.append(f"reference_effective_direction:{effective_direction}")
    reasons.append(f"momentum_avg:{momentum}")

    thresholds = contract["classification_thresholds"]
    strong_threshold = Decimal(thresholds["strong_momentum_min_pct"])
    mild_threshold = Decimal(thresholds["mild_momentum_min_pct"])

    agrees = (effective_direction == "POSITIVE" and momentum > 0) or (
        effective_direction == "NEGATIVE" and momentum < 0
    )
    if not agrees or abs(momentum) < mild_threshold:
        reasons.append("MOMENTUM_HAS_NOT_CAUGHT_UP_TO_REFERENCE")
        return "UNDER_REFLECTED", reasons
    if abs(momentum) >= strong_threshold:
        reasons.append("MOMENTUM_STRONGLY_AGREES_WITH_REFERENCE")
        return "FULLY_REFLECTED", reasons
    reasons.append("MOMENTUM_PARTIALLY_AGREES_WITH_REFERENCE")
    return "PARTIALLY_REFLECTED", reasons


def _classify(
    *,
    price_as_of: str | None,
    decision_date: dt.date,
    freshness_ceiling_days: int,
    windows: dict,
    strength: dict,
    event: dict,
    reference: dict,
    valuation: dict,
    contract: dict,
) -> tuple[str, str, str, str, list[str]]:
    """Pure, deterministic classification. Rule 1 (staleness) always runs
    first and, if triggered, short-circuits everything else -- both
    `price_state` and `reflection_status` are forced UNKNOWN.

    Returns (price_state, reflection_status, confidence, data_state,
    reasons)."""
    if price_as_of is None:
        return "UNKNOWN", "UNKNOWN", "UNKNOWN", "PRICE_DATA_MISSING", ["PRICE_AS_OF_MISSING"]

    price_as_of_dt = _utc(price_as_of, "PRICE_AS_OF_INVALID")
    if price_as_of_dt.date() > decision_date:
        raise PriceReflectionError("PRICE_AS_OF_IN_FUTURE")
    age_days = (decision_date - price_as_of_dt.date()).days
    if age_days > freshness_ceiling_days:
        return "UNKNOWN", "UNKNOWN", "UNKNOWN", "PRICE_STALE", [
            f"PRICE_AS_OF_STALE:age_days={age_days}:ceiling_days={freshness_ceiling_days}"
        ]

    m1 = windows["1m"]
    rs_market = strength["vs_market"]
    pos_high = strength["position_vs_recent_high_pct"]
    volume_change = strength["volume_change_pct"]
    val_pos = valuation["position_in_range"] if valuation["position_in_range"] != "UNKNOWN" else None

    price_state, price_reasons, scored_signals = _price_state(
        m1=m1, rs_market=rs_market, pos_high=pos_high,
        volume_change=volume_change, val_pos=val_pos, contract=contract,
    )
    reflection_status, reflection_reasons = _reflection_status(
        event=event, reference=reference, m1=m1, rs_market=rs_market, contract=contract,
    )

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

    return price_state, reflection_status, confidence, data_state, reasons


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
    reference = _validate_reflection_reference(reflection_reference, decision_date_checked, contract)
    valuation = _validate_valuation_context(valuation_context, contract)

    price_state, reflection_status, confidence, data_state, reasons = _classify(
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
        },
        "reflection_reference": {
            "reference_event_id": reference["reference_event_id"] or "UNKNOWN",
            "expectation_as_of": reference["expectation_as_of"] or "UNKNOWN",
            "expectations_gap_status": reference["expectations_gap_status"] or "UNKNOWN",
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
    if not isinstance(er, dict) or set(er) != {"event_date", "direction", "reaction_magnitude_pct"}:
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

    rr = pr.get("reflection_reference")
    if not isinstance(rr, dict) or set(rr) != {
        "reference_event_id", "expectation_as_of", "expectations_gap_status",
    }:
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_FIELDS_MISMATCH")
    if rr["expectation_as_of"] != "UNKNOWN":
        _date(rr["expectation_as_of"], "OUTPUT_REFLECTION_REFERENCE_EXPECTATION_AS_OF_INVALID")
    if rr["expectations_gap_status"] not in contract["allowed_direction"] + ["UNKNOWN"]:
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_EXPECTATIONS_GAP_STATUS_INVALID")

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
