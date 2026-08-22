#!/usr/bin/env python3
"""P8-10 Price Reflection builder — price/volume-only, never fundamentals.

This module answers one question: *given price, volume, relative-strength,
event-reaction and valuation-history evidence the caller already has, has the
market's price already reflected what is known?*

It is deliberately blind to thesis quality, conviction, or any fundamental
narrative — the public builder below (`build_packet`) accepts **only**
price/volume/valuation-history parameters. There is no "thesis" or
"fundamental strength" parameter anywhere in its signature: it is
structurally impossible to feed this module optimism as an input. Good
fundamentals alone can never produce `UNDER_REFLECTED`, because this module
has no channel through which fundamentals could even arrive.

Staleness is the loudest rule here: if `price_as_of` is missing or older than
the freshness ceiling relative to `decision_date`, `status` is forced to
`UNKNOWN` regardless of what every other input suggests. See
`docs/price_reflection_contract.md` for the chosen default ceiling and the
full classification method.

Whenever `status` is `UNKNOWN`, `reasons[0]` always carries a second, more
granular `"DATA_STATE:<value>"` marker recording WHY, from a closed,
real-evidence-only vocabulary (`contract["allowed_data_state"]`):
`PRICE_DATA_MISSING` (no price at all), `PRICE_STALE` (a price exists but is
older than the freshness ceiling), or `REFLECTION_UNCERTAIN_WITH_VALID_PRICE`
(price is fresh and valid, but there isn't enough real relative-strength/
momentum signal to render a reflection judgment). `reasons[0]` is
`"DATA_STATE:VALID"` whenever `status` is one of the confident values. See
`data_state_of()` below for a small parsing helper.

This is deliberately encoded inside the existing `reasons` field rather than
as a new top-level key: `decision/alpha_review.py` hard-validates the
`price_reflection` sub-object's field set with a strict `set(pr) != pr_fields`
check on its own embedded copy, and this PR is explicitly scoped to leave
that module (and `shadow/alpha_shadow_ledger.py`) untouched -- adding a new
key there would break `alpha_review.py`'s own validation. `reasons` was
already an unconstrained list of strings, so this is fully backward
compatible with every existing consumer.

`OVEREXTENDED` is a legitimate, distinct state — entry-timing risk is
elevated, not "the company is bad" and not a Rule/Portfolio rejection. There
is no `REJECTED` value anywhere in this module's vocabulary; Rule/Portfolio
rejection is a different system's job. This module never emits a P5 Rule
PASS/FAIL-shaped result.

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


def data_state_of(price_reflection: dict) -> str:
    """Extracts the `"DATA_STATE:<value>"` marker `build_packet` always
    places at `reasons[0]` -- see module docstring for why this lives inside
    `reasons` rather than as its own top-level key. Callers should pass an
    already-`validate_packet`-checked packet's inner `price_reflection`
    sub-object; this raises the same `OUTPUT_DATA_STATE_MARKER_MISSING`-shaped
    error `validate_packet` would if that invariant is somehow violated."""
    reasons = price_reflection.get("reasons")
    if not isinstance(reasons, list) or not reasons or not isinstance(reasons[0], str) \
            or not reasons[0].startswith("DATA_STATE:"):
        raise PriceReflectionError("OUTPUT_DATA_STATE_MARKER_MISSING")
    return reasons[0][len("DATA_STATE:"):]


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
        "schema_version": 1,
        "contract_version": "price_reflection/1",
        "output_schema_version": "price_reflection_packet/1",
        "allowed_status": [
            "UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED",
            "OVEREXTENDED", "UNKNOWN",
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
        "korea_data_source_scope": "KRX_OFFICIAL",
        "default_freshness_ceiling_days": 5,
        "classification_thresholds": {
            "rally_min_1m_return_pct": "15", "near_high_max_distance_pct": "3",
            "strong_momentum_min_pct": "8", "mild_momentum_min_pct": "2",
        },
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


def _classify(
    *,
    price_as_of: str | None,
    decision_date: dt.date,
    freshness_ceiling_days: int,
    data_source_scope: str,
    windows: dict,
    strength: dict,
    event: dict,
    valuation: dict,
    contract: dict,
) -> tuple[str, str, list[str], str]:
    """Pure, deterministic classification. Rule 1 (staleness) always runs first
    and, if triggered, short-circuits every other signal unconditionally.

    Returns (status, confidence, reasons, data_state). `data_state` is only
    ever non-`VALID` when `status == "UNKNOWN"` -- it records WHICH of the
    three real, distinct reasons produced that UNKNOWN (see module
    docstring): no price at all (`PRICE_DATA_MISSING`), a price too old to
    trust (`PRICE_STALE`), or a fresh/valid price with too little real
    signal to render a reflection judgment
    (`REFLECTION_UNCERTAIN_WITH_VALID_PRICE`)."""
    reasons: list[str] = []

    if price_as_of is None:
        return "UNKNOWN", "UNKNOWN", ["PRICE_AS_OF_MISSING"], "PRICE_DATA_MISSING"

    price_as_of_dt = _utc(price_as_of, "PRICE_AS_OF_INVALID")
    if price_as_of_dt.date() > decision_date:
        raise PriceReflectionError("PRICE_AS_OF_IN_FUTURE")
    age_days = (decision_date - price_as_of_dt.date()).days
    if age_days > freshness_ceiling_days:
        return "UNKNOWN", "UNKNOWN", [
            f"PRICE_AS_OF_STALE:age_days={age_days}:ceiling_days={freshness_ceiling_days}"
        ], "PRICE_STALE"

    m1 = windows["1m"]
    rs_market = strength["vs_market"]
    pos_high = strength["position_vs_recent_high_pct"]
    val_pos = valuation["position_in_range"] if valuation["position_in_range"] != "UNKNOWN" else None
    event_dir = event["direction"] if event["direction"] not in (None, "UNKNOWN") else None

    korea_scope = contract["korea_data_source_scope"]
    if data_source_scope == korea_scope and (m1 is None or rs_market is None or pos_high is None):
        return "UNKNOWN", "UNKNOWN", [
            "KOREA_PRICE_DATA_INSUFFICIENT:requires_1m_return_and_vs_market_and_position_vs_recent_high"
        ], "REFLECTION_UNCERTAIN_WITH_VALID_PRICE"

    scored_signals = sum(
        signal is not None for signal in (m1, rs_market, pos_high, val_pos, event_dir)
    )
    if scored_signals < 2:
        return "UNKNOWN", "UNKNOWN", [f"INSUFFICIENT_PRICE_SIGNALS:scored_count={scored_signals}"], \
            "REFLECTION_UNCERTAIN_WITH_VALID_PRICE"

    thresholds = contract["classification_thresholds"]
    rally_threshold = Decimal(thresholds["rally_min_1m_return_pct"])
    near_high_threshold = Decimal(thresholds["near_high_max_distance_pct"])
    strong_threshold = Decimal(thresholds["strong_momentum_min_pct"])
    mild_threshold = Decimal(thresholds["mild_momentum_min_pct"])

    for label, val in (("1m_return", m1), ("vs_market", rs_market), ("position_vs_recent_high_pct", pos_high)):
        if val is not None:
            reasons.append(f"{label}:{val}")
    if val_pos is not None:
        reasons.append(f"valuation_position_in_range:{val_pos}")
    if event_dir is not None:
        reasons.append(f"event_reaction_direction:{event_dir}")

    rally = m1 is not None and m1 >= rally_threshold
    near_high = pos_high is not None and pos_high <= near_high_threshold
    expensive = val_pos == "HIGH"

    if rally and (near_high or expensive):
        reasons.append("RALLY_AND_STRETCHED_POSITIONING")
        status = "OVEREXTENDED"
    else:
        momentum_values = [v for v in (m1, rs_market) if v is not None]
        momentum = sum(momentum_values) / len(momentum_values) if momentum_values else None
        if momentum is not None:
            reasons.append(f"momentum_avg:{momentum}")
        if event_dir in ("POSITIVE", "NEGATIVE") and momentum is not None:
            agrees = (event_dir == "POSITIVE" and momentum > 0) or (event_dir == "NEGATIVE" and momentum < 0)
            if not agrees or abs(momentum) < mild_threshold:
                reasons.append("MOMENTUM_HAS_NOT_CAUGHT_UP_TO_KNOWN_EVENT_REACTION")
                status = "UNDER_REFLECTED"
            elif abs(momentum) >= strong_threshold:
                reasons.append("MOMENTUM_STRONGLY_AGREES_WITH_KNOWN_EVENT_REACTION")
                status = "FULLY_REFLECTED"
            else:
                reasons.append("MOMENTUM_PARTIALLY_AGREES_WITH_KNOWN_EVENT_REACTION")
                status = "PARTIALLY_REFLECTED"
        elif momentum is None:
            status = "UNKNOWN"
            reasons.append("INSUFFICIENT_PRICE_SIGNALS:no_momentum_signal")
        elif momentum >= strong_threshold:
            reasons.append("STRONG_MOMENTUM_NO_CONFIRMED_EVENT_SIGNAL")
            status = "FULLY_REFLECTED"
        else:
            # Flat/negative momentum with no confirmed catalyst never claims
            # UNDER_REFLECTED — there is no channel here to distinguish "price
            # hasn't caught up yet" from "there is nothing to catch up to".
            reasons.append("MODERATE_OR_FLAT_MOMENTUM_NO_CONFIRMED_EVENT_SIGNAL")
            status = "PARTIALLY_REFLECTED"

    if status == "UNKNOWN":
        confidence = "UNKNOWN"
        data_state = "REFLECTION_UNCERTAIN_WITH_VALID_PRICE"
    else:
        conf_t = contract["confidence_thresholds"]
        if scored_signals >= conf_t["high_min_scored_signal_count"]:
            confidence = "HIGH"
        elif scored_signals >= conf_t["medium_min_scored_signal_count"]:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        data_state = "VALID"

    return status, confidence, reasons, data_state


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
    valuation_context: dict | None = None,
    data_source_scope: str | None = None,
    contract: dict | None = None,
) -> dict:
    """Build a Price Reflection packet.

    Every parameter above is price, volume, relative-strength, event-reaction,
    or valuation-history data (or plumbing: subject/dates/contract). There is
    no thesis-quality or fundamental-strength parameter — see
    FORBIDDEN_PARAMETER_SUBSTRINGS and test_price_reflection.py for the
    signature-inspection regression that guards this.
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
    valuation = _validate_valuation_context(valuation_context, contract)

    status, confidence, reasons, data_state = _classify(
        price_as_of=price_as_of,
        decision_date=decision_date_checked,
        freshness_ceiling_days=ceiling,
        data_source_scope=scope,
        windows=windows,
        strength=strength,
        event=event,
        valuation=valuation,
        contract=contract,
    )

    missing_inputs = sorted(name for name, val in (
        ("price_as_of", price_as_of),
        ("relative_strength", relative_strength),
        ("recent_return_windows", recent_return_windows),
        ("event_reaction", event_reaction),
        ("valuation_context", valuation_context),
    ) if val is None)

    reasons = [f"DATA_STATE:{data_state}"] + reasons

    price_reflection = {
        "status": status,
        "confidence": confidence,
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
        "status", "confidence", "price_as_of", "relative_strength",
        "recent_return_windows", "event_reaction", "valuation_context",
        "reasons", "missing_inputs", "data_source_scope",
    }
    if not isinstance(pr, dict) or set(pr) != pr_fields:
        raise PriceReflectionError("OUTPUT_PRICE_REFLECTION_FIELDS_MISMATCH")

    status = pr.get("status")
    confidence = pr.get("confidence")
    if status not in contract["allowed_status"]:
        raise PriceReflectionError("OUTPUT_STATUS_INVALID")
    if confidence not in contract["allowed_confidence"]:
        raise PriceReflectionError("OUTPUT_CONFIDENCE_INVALID")
    if status == "UNKNOWN" and confidence != "UNKNOWN":
        raise PriceReflectionError("OUTPUT_UNKNOWN_STATUS_REQUIRES_UNKNOWN_CONFIDENCE")
    if "REJECTED" in contract["allowed_status"]:  # defensive: vocabulary must never gain this value
        raise PriceReflectionError("OUTPUT_VOCABULARY_CONTAINS_REJECTED")

    reasons_check = pr.get("reasons")
    if not isinstance(reasons_check, list) or not reasons_check or not isinstance(reasons_check[0], str) \
            or not reasons_check[0].startswith("DATA_STATE:"):
        raise PriceReflectionError("OUTPUT_DATA_STATE_MARKER_MISSING")
    data_state = reasons_check[0][len("DATA_STATE:"):]
    if data_state not in contract["allowed_data_state"]:
        raise PriceReflectionError("OUTPUT_DATA_STATE_INVALID")
    if (status == "UNKNOWN") != (data_state != "VALID"):
        raise PriceReflectionError("OUTPUT_DATA_STATE_STATUS_MISMATCH")

    data_source_scope = pr.get("data_source_scope")
    if data_source_scope not in contract["allowed_data_source_scope"]:
        raise PriceReflectionError("OUTPUT_DATA_SOURCE_SCOPE_INVALID")
    if data_source_scope == contract["korea_data_source_scope"] and status not in {"UNKNOWN"}:
        rs = pr.get("relative_strength", {})
        rw = pr.get("recent_return_windows", {})
        if "UNKNOWN" in (rw.get("1m"), rs.get("vs_market"), rs.get("position_vs_recent_high_pct")):
            raise PriceReflectionError("OUTPUT_KOREA_INSUFFICIENT_DATA_MUST_BE_UNKNOWN")

    reasons = pr.get("reasons")
    if not isinstance(reasons, list) or not reasons or any(
        not isinstance(item, str) or not item.strip() for item in reasons
    ):
        raise PriceReflectionError("OUTPUT_REASONS_INVALID")

    missing = pr.get("missing_inputs")
    allowed_missing = {
        "price_as_of", "relative_strength", "recent_return_windows",
        "event_reaction", "valuation_context",
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
