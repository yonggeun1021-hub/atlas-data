#!/usr/bin/env python3
"""P8-10 Price Reflection builder -- price/volume-only, never fundamentals.

Builds a **Price Reflection** packet: a structurally separated read on (1)
price/momentum (`price_state`) and (2) whether the market's price already
reflects a specific, real expectation or event (`reflection_status`), based
strictly on price, volume, relative-strength, and valuation-history evidence
the caller supplies.

`price_state` -- OVEREXTENDED | STRONG_MOMENTUM | MODERATE | WEAK | UNKNOWN.
A pure, price/volume-only momentum read, real and fully computed from
caller-supplied windows/relative-strength/valuation-context.

`reflection_status` -- UNDER_REFLECTED | PARTIALLY_REFLECTED |
FULLY_REFLECTED | UNKNOWN. Structurally, unconditionally `"UNKNOWN"` in
every packet this module can produce or validate.

★ SCOPE: Reflection Evidence Authority deferred (CIO PR #212, 2026-08-23).
  This module, and a companion `decision/event_evidence.py` (an Event
  Evidence Authority engine with provenance verification, direction-rule
  implementation tables, and a ratification-authority registry), went
  through 9 rounds of CIO review closing successive provenance/ratification
  defects, culminating in a final integration finding at the policy layer
  (a rule could be ratified in the future and still applied retroactively
  to a past decision; ratification "evidence" was never validated as a
  genuine authority record). The CIO declined further local patching and
  instead reduced this PR to its proven MVP boundary: `decision/event_
  evidence.py` was deleted entirely, and this module's `event_reaction`/
  `reflection_reference` citation-input parameters -- along with every
  internal function that only existed to verify or threshold-classify
  them -- were removed, not merely disconnected. A closing-fix pass then
  locked `validate_packet()` itself to unconditionally reject any packet
  claiming `reflection_status != "UNKNOWN"` (build_packet()'s own restraint
  alone was not sufficient -- a tampered/loaded packet could still claim
  one), and the companion `decision/alpha_review.py` independently
  enforces the same boundary on its own output.

  `price_state` is completely unaffected by any of this and remains fully
  real. `event_reaction`/`reflection_reference` remain present in the
  output packet SHAPE (no contract bump) as inert, all-`"UNKNOWN"`
  constants, purely for downstream schema compatibility.

  **Deferred, not abandoned:** a future, separate, dependent PR must design
  a Reflection Evidence Authority together with Atlas P5 Rule Authority --
  append-only per-rule canonical records, `ratified_at`/`effective_from`,
  exact-content provenance, explicit decision-time ordering checks, and a
  structured authority-evidence schema -- with that design approved BEFORE
  any implementation code is written, not merely before merge. Tracked on
  the existing P8-10 WBS row.

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


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ★ SCOPE REDUCTION (see module docstring): this module no longer loads
#   `decision/expectations_gap.py`, `decision/price_evidence.py`, or
#   `decision/event_evidence.py` -- none of them are used anywhere below any
#   more, since the citation-verification machinery that consumed them
#   (`_validate_event_reaction`/`_validate_reflection_reference`/
#   `_compute_verified_return`) has been removed entirely, not merely
#   disconnected. `decision/price_evidence.py` remains fully real and in
#   active use elsewhere in this repo (`decision/pilot_evidence_intake.py`
#   assembles `price_as_of`/`recent_return_windows`/`relative_strength`
#   from it before calling this module's `build_packet` -- this module
#   itself was never the right place for that assembly). `decision/event_
#   evidence.py` no longer exists in this repo at all.

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
        "schema_version": 6,
        "contract_version": "price_reflection/6",
        "output_schema_version": "price_reflection_packet/6",
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
        # ★ CIO round 5: closed vocabulary of real evidentiary categories an
        #   Event Evidence Envelope's `source_class` used to be able to
        #   declare. Kept unchanged in the contract dict itself purely to
        #   avoid a contract/schema version bump (see module docstring,
        #   scope reduction) -- no code anywhere in this module reads or
        #   validates against it any more, since `decision/event_
        #   evidence.py` and the `event_reaction` input it backed no longer
        #   exist.
        "allowed_event_source_class": [
            "SEC_FILING_EVENT", "DART_FILING_EVENT", "OFFICIAL_RELEASE_EVENT", "GUIDANCE_CHANGE_EVENT",
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


# ★ SCOPE REDUCTION (see module docstring): `_reflection_status` (the
#   function that used to compute a real, evidence-verified reference point
#   and threshold-classify a computed return into UNDER_REFLECTED/
#   PARTIALLY_REFLECTED/FULLY_REFLECTED) has been REMOVED, not merely
#   disconnected. `reflection_status` is now the literal constant
#   `"UNKNOWN"` everywhere in this module -- there is no function left
#   anywhere in this file that could compute anything else. This is
#   deliberately NOT a "the current evidence happens to be insufficient"
#   state; it is a structural fact about what code exists.
REFLECTION_STATUS_ALWAYS = "UNKNOWN"


def _classify(
    *,
    price_as_of: str | None,
    decision_date: dt.date,
    freshness_ceiling_days: int,
    windows: dict,
    strength: dict,
    valuation: dict,
    contract: dict,
) -> tuple[str, str, str, str, list[str]]:
    """Pure, deterministic classification. Rule 1 (staleness) always runs
    first and, if triggered, short-circuits everything else -- both
    `price_state` and `reflection_status` are forced UNKNOWN.

    `reflection_status` is unconditionally `REFLECTION_STATUS_ALWAYS`
    ("UNKNOWN") -- see module docstring (scope reduction). `price_state`
    (the pure, price/volume-only momentum read) is fully real and
    unaffected.

    Returns (price_state, reflection_status, confidence, data_state,
    reasons)."""
    if price_as_of is None:
        return "UNKNOWN", REFLECTION_STATUS_ALWAYS, "UNKNOWN", "PRICE_DATA_MISSING", ["PRICE_AS_OF_MISSING"]

    price_as_of_dt = _utc(price_as_of, "PRICE_AS_OF_INVALID")
    if price_as_of_dt.date() > decision_date:
        raise PriceReflectionError("PRICE_AS_OF_IN_FUTURE")
    age_days = (decision_date - price_as_of_dt.date()).days
    if age_days > freshness_ceiling_days:
        return "UNKNOWN", REFLECTION_STATUS_ALWAYS, "UNKNOWN", "PRICE_STALE", [
            f"PRICE_AS_OF_STALE:age_days={age_days}:ceiling_days={freshness_ceiling_days}"
        ]

    m1 = windows["1m"]
    rs_market = strength["vs_market"]
    pos_high = strength["position_vs_recent_high_pct"]
    volume_change = strength["volume_change_pct"]
    val_pos = valuation["position_in_range"] if valuation["position_in_range"] != "UNKNOWN" else None

    price_state, price_reasons, _scored_signals = _price_state(
        m1=m1, rs_market=rs_market, pos_high=pos_high,
        volume_change=volume_change, val_pos=val_pos, contract=contract,
    )

    # reflection_status is always UNKNOWN in this reduced scope -- price_
    # state=UNKNOWN and a non-UNKNOWN reflection_status can therefore never
    # coexist by construction (round-3's structural invariant is now
    # trivially true, not merely enforced case-by-case); still re-asserted
    # unconditionally in validate_packet() below too.
    reflection_status = REFLECTION_STATUS_ALWAYS
    reasons = [f"price_state={price_state}"] + price_reasons + [
        f"reflection_status={reflection_status}",
        "NO_REFLECTION_EVIDENCE_AUTHORITY_EXISTS_IN_THIS_REDUCED_SCOPE",
    ]

    data_state = "REFLECTION_UNCERTAIN_WITH_VALID_PRICE"
    confidence = "UNKNOWN"

    return price_state, reflection_status, confidence, data_state, reasons


# ★ SCOPE REDUCTION (see module docstring): `event_reaction`/`reflection_
#   reference` are no longer accepted parameters -- not merely unused, they
#   do not exist in this function's signature at all. There is no way for
#   any caller (real or a future edit reintroducing an old call site) to
#   pass a citation through this function; Python itself raises `TypeError`
#   on an unexpected keyword argument. The output packet's own `event_
#   reaction`/`reflection_reference` sub-objects are hardcoded, literal
#   constants (`_INERT_EVENT_REACTION`/`_INERT_REFLECTION_REFERENCE` below)
#   -- kept in the output SHAPE unchanged (no contract/schema version bump)
#   purely so every existing downstream consumer (`decision/alpha_review.py`,
#   `shadow/alpha_shadow_ledger.py`, `briefing/alpha_review_briefing.py`)
#   keeps working against the exact same packet shape it always has; their
#   values can now never be anything but "UNKNOWN".
_INERT_EVENT_REACTION = {
    "event_at": "UNKNOWN", "direction": "UNKNOWN", "reaction_magnitude_pct": "UNKNOWN",
    "source_class": "UNKNOWN", "source_ref": "UNKNOWN", "source_sha256": "UNKNOWN",
    "verified_post_event_return_pct": "UNKNOWN", "capture_kind": "UNKNOWN",
    "first_authoritative_seen_at": "UNKNOWN", "raw_source_ref": "UNKNOWN",
    "raw_source_sha256": "UNKNOWN", "published_at": "UNKNOWN", "locator": "UNKNOWN",
}
_INERT_REFLECTION_REFERENCE = {
    "reference_event_id": "UNKNOWN", "expectation_as_of": "UNKNOWN",
    "expectations_gap_status": "UNKNOWN", "expectations_gap_packet_sha256": "UNKNOWN",
    "expectations_gap_reference_date": "UNKNOWN",
    "expectations_gap_first_authoritative_seen_at": "UNKNOWN",
    "verified_post_reference_return_pct": "UNKNOWN",
}


def build_packet(
    *,
    subject: str,
    decision_date: str,
    generated_at: str,
    price_as_of: str | None = None,
    freshness_ceiling_days: int | None = None,
    relative_strength: dict | None = None,
    recent_return_windows: dict | None = None,
    valuation_context: dict | None = None,
    data_source_scope: str | None = None,
    contract: dict | None = None,
) -> dict:
    """Build a Price Reflection packet.

    Every parameter above is price, volume, relative-strength, or
    valuation-history data (or plumbing: subject/dates/contract). There is
    no thesis-quality or fundamental-strength parameter — see
    FORBIDDEN_PARAMETER_SUBSTRINGS and test_price_reflection.py for the
    signature-inspection regression that guards this. There is also no
    event-reaction or reflection-reference-point parameter any more (scope
    reduction, see module docstring) — `reflection_status` is always
    `"UNKNOWN"` in this reduced scope.
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
    valuation = _validate_valuation_context(valuation_context, contract)

    price_state, reflection_status, confidence, data_state, reasons = _classify(
        price_as_of=price_as_of,
        decision_date=decision_date_checked,
        freshness_ceiling_days=ceiling,
        windows=windows,
        strength=strength,
        valuation=valuation,
        contract=contract,
    )

    # event_reaction/reflection_reference are structurally always absent in
    # this reduced scope -- always reported as missing, matching the literal
    # truth that no caller can ever supply them.
    missing_inputs = sorted(name for name, val in (
        ("price_as_of", price_as_of),
        ("relative_strength", relative_strength),
        ("recent_return_windows", recent_return_windows),
        ("event_reaction", None),
        ("reflection_reference", None),
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
        "event_reaction": dict(_INERT_EVENT_REACTION),
        "reflection_reference": dict(_INERT_REFLECTION_REFERENCE),
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
    # ★ CIO closing-fix ruling (2026-08-23, immediately after the scope
    #   reduction): `build_packet()` being structurally incapable of
    #   producing anything but "UNKNOWN" is not the same as `validate_
    #   packet()` refusing anything else. CIO's direct repro: take a real
    #   packet, edit `reflection_status` to `"PARTIALLY_REFLECTED"` +
    #   `confidence="LOW"` + `data_state="VALID"`, recompute the hash --
    #   this function accepted it. `UNDER_REFLECTED`/`PARTIALLY_REFLECTED`/
    #   `FULLY_REFLECTED` remain legal `allowed_reflection_status` vocabulary
    #   members (no contract bump, reserved for the deferred future
    #   Reflection Evidence Authority workstream), but no packet -- however
    #   constructed, loaded, or re-signed -- may claim one of them THROUGH
    #   THIS VALIDATOR while that authority does not exist. This is the
    #   single, unconditional structural lock: it is what actually makes
    #   "UNKNOWN" the only reachable outcome, not `build_packet()`'s own
    #   restraint alone.
    if reflection_status != "UNKNOWN":
        raise PriceReflectionError("OUTPUT_REFLECTION_STATUS_MUST_BE_UNKNOWN_IN_THIS_REDUCED_SCOPE")
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

    # ★ SCOPE REDUCTION (see module docstring): `event_reaction`/
    #   `reflection_reference` can no longer legitimately carry ANY value
    #   other than the fully-inert, all-"UNKNOWN" constant -- there is no
    #   code path left anywhere in this module that could produce anything
    #   else. Rather than format-validating fields that can only ever be
    #   "UNKNOWN" (dead validation logic for a dead capability), this
    #   asserts EXACT equality to the inert constant -- a single check that
    #   is simultaneously stricter (rejects ANY deviation, not just
    #   malformed ones) and simpler than the field-by-field format checks
    #   rounds 3-9 built up. A loaded/tampered packet claiming a real
    #   citation (e.g. `capture_kind="LIVE_OFFICIAL_CAPTURE"`) is rejected
    #   outright, regardless of how well-formed the rest of it looks.
    er = pr.get("event_reaction")
    if er != _INERT_EVENT_REACTION:
        raise PriceReflectionError("OUTPUT_EVENT_REACTION_MUST_BE_INERT_IN_THIS_REDUCED_SCOPE")

    rr = pr.get("reflection_reference")
    if rr != _INERT_REFLECTION_REFERENCE:
        raise PriceReflectionError("OUTPUT_REFLECTION_REFERENCE_MUST_BE_INERT_IN_THIS_REDUCED_SCOPE")

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
