#!/usr/bin/env python3
"""US PAPER runtime producer — U4 design draft, structurally UNKNOWN-only today.

CIO 지시(2026-09-14, U4 항목, "U3 결과 전에는 판정이 UNKNOWN으로만 나오게")를 따라,
KR 브리지(``regime/kr_paper_runtime.py`` · ``regime/kr_information_system_runtime_bridge.py``,
PR #696 패턴)를 미러링한 US 러ntime 생산자의 **설계 초안**이다.  완전히 배선된 운영
생산자가 아니다.

This module never imports or depends on ``regime.us_historical_replay_population``
(a sibling module still being extended by an unmerged PR at the time this draft was
written).  It is structurally parallel to it — it reuses the same ALFRED
vintage-containment discipline — but reimplements that one check locally rather than
importing it, exactly as instructed.

Two independent, real (not hardcoded-comment) gates must both be true before this
module will even attempt to compute a PAPER regime, and a third, unconditional
structural fact makes ``runtime_regime`` impossible to promote regardless:

1. ``US_PIT_ACCEPTED_BUNDLE_AVAILABLE`` — computed by calling the existing,
   unmodified ``regime.market_scoped_pit_acceptance.evaluate_market_pit_acceptance
   ("US", bundle)`` every time this module runs.  This is **U3**: a real US
   PIT-accepted evidence bundle.  As of this draft, ``US`` can never reach
   ``PIT_ACCEPTED`` through this repository's own code: the only real population
   module, ``regime.us_historical_replay_population``, computes just three of five
   required axes (``TREND``, ``RISK_VOL``, ``LIQUIDITY`` — see its own module
   docstring); ``BREADTH`` and ``LEADERSHIP`` are never populated because doing so
   would require a separate ratification (**U2**) that has not happened.  Condition
   2 of the acceptance rule (5-of-5 axes per date) can therefore never be satisfied
   by real US evidence today, so ``evaluate_market_pit_acceptance("US", ...)``
   structurally cannot return anything but ``NOT_ACCEPTED`` — this is not asserted
   here, it is a fact about the current state of ``us_historical_replay_population``
   and ``market_scoped_pit_acceptance`` this module merely calls into, unmodified.

2. ``US_PAPER_RUNTIME_ADOPTION_RATIFIED`` — computed by reading
   ``config/us_paper_runtime_adoption_v1.json`` (the **U5** identity, the US
   equivalent of KR's PR #696 adoption).  That file does not exist in this
   repository yet.  ``_us_runtime_adoption_ratified()`` fails closed to ``False``
   on any read error, exactly like
   ``market_scoped_pit_acceptance.normalization_and_freshness_ratified``.  U5 is a
   separate CIO-technical ratification; this draft does not perform it, and this
   module never authors that file.

3. Even in the hypothetical future where both gates above are real and true, this
   module still never writes anything other than the literal string ``"UNKNOWN"``
   into ``runtime_regime``.  Search this file: there is exactly one assignment of
   ``runtime_regime`` in the returned packet, made once at packet-skeleton
   construction, and it is never reassigned anywhere below.  When both gates pass,
   the computed common-v1 regime is published only as ``paper_regime`` (a PAPER
   simulation value, exactly the KR bridge's own ``SYNTHETIC_OFFLINE_FIXTURE`` /
   ``HISTORICAL_REPLAY`` treatment for non-``LIVE_NATURAL`` evidence) — promoting
   that value to ``runtime_regime`` is the one additional step U5's real adoption
   identity would authorize, and this draft intentionally does not wire it.

Authority: every authority flag below is unconditionally ``False`` except the one
"this is a PAPER calculation only" marker.  No strategy, Stage, Buy, Action, Order,
capital, Production, trading, or REAL authority is ever granted by this module.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import decision_authority as COMMON
from regime import paper_regime_reference as REFERENCE
from regime import market_scoped_pit_acceptance as PIT_ACCEPTANCE

SCHEMA = "us_paper_runtime_decision/1"
REFERENCE_POLICY_PATH = ROOT / "config" / "paper_regime_reference_policy_v1.json"
FRESHNESS_POLICY_PATH = ROOT / "config" / "regime_semantic_freshness_policy_v1.json"
NORMALIZATION_POLICY_PATH = ROOT / "config" / "paper_runtime_normalization_v1.json"
# U5. Does not exist yet in this repository -- see module docstring gate 2.
US_RUNTIME_ADOPTION_IDENTITY_PATH = ROOT / "config" / "us_paper_runtime_adoption_v1.json"
DATE10 = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CODE_REVISION = re.compile(r"^[0-9a-f]{40}$")


class USRuntimeError(ValueError):
    pass


def require(condition, code: str) -> None:
    if not condition:
        raise USRuntimeError(code)


def _read_json(path: Path) -> object:
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _time(value: object) -> dt.datetime:
    require(isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is not None,
        "UTC_TIMESTAMP_REQUIRED")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise USRuntimeError("UTC_TIMESTAMP_INVALID") from exc


def _calendar_date(value: object) -> dt.date | None:
    if not isinstance(value, str) or DATE10.fullmatch(value) is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _fred_vintage_covers(anchor: dt.date, row: object, label: str) -> None:
    """Independent reimplementation of the ALFRED vintage-containment bind.

    Same discipline as ``us_historical_replay_population._assert_vintage_covers``
    (containment of ``anchor`` in ``[realtime_start, realtime_end]``, never
    equality), reimplemented locally rather than imported -- this producer is
    structurally parallel to, but does not depend on, that historical-replay
    module (module docstring).  A window that begins after ``anchor`` is a
    lookahead; a window that already ended before ``anchor`` was superseded --
    two distinct facts, kept distinct in the failure code.
    """
    # Errors never expose raw source/provider detail past the first colon
    # (see the except block in evaluate_us_paper_runtime, same convention as
    # the KR bridge), so the axis/series label is joined with "_", not ":",
    # to survive that truncation intact.
    require(isinstance(row, dict), "US_FRED_VINTAGE_MISSING_" + label)
    start = _calendar_date(row.get("realtime_start"))
    end = _calendar_date(row.get("realtime_end"))
    require(start is not None and end is not None, "US_FRED_VINTAGE_MISSING_" + label)
    require(start <= anchor, "US_RUNTIME_LOOKAHEAD_VIOLATION_FRED_VINTAGE_" + label)
    require(anchor <= end, "US_FRED_VINTAGE_SUPERSEDED_BEFORE_REQUESTED_DATE_" + label)


def _us_runtime_adoption_ratified() -> bool:
    """U5 gate. True only once a real, ratified adoption identity is committed.

    Reads ``config/us_paper_runtime_adoption_v1.json`` fresh on every call
    (never a cached module constant), exactly like
    ``market_scoped_pit_acceptance.normalization_and_freshness_ratified``, and
    fails closed to ``False`` on any missing file, read error, or structural
    mismatch.  As of this draft that file does not exist, so this always
    returns ``False`` today -- but the check is wired to real file state, not
    a Python literal, so a future PR that lands U5 flips this without touching
    this function.
    """
    identity = _read_json(US_RUNTIME_ADOPTION_IDENTITY_PATH)
    if not isinstance(identity, dict):
        return False
    decision = identity.get("decision")
    return bool(
        identity.get("policy_status") == "RATIFIED"
        and identity.get("market") == "US"
        and isinstance(decision, dict)
        and str(decision.get("identity", "")).startswith("US_PAPER_RUNTIME_ADOPTION_V1")
    )


def _freshness_policy_ratified_for_us() -> dict:
    freshness = _read_json(FRESHNESS_POLICY_PATH)
    require(
        isinstance(freshness, dict)
        and freshness.get("policy_status") == "RATIFIED"
        and "US" in freshness.get("markets", {}),
        "US_FRESHNESS_POLICY_UNRATIFIED",
    )
    return freshness["markets"]["US"]


def _live_axis_directions(
    packet: dict, policy: dict, evaluation_date: dt.date,
) -> tuple[dict[str, str], str]:
    """Derive today's signed US axis directions, freshness-gated first.

    ``paper_regime_reference.build_us`` has no freshness notion of its own --
    it trusts whatever packet it is handed -- so every session-exact-match and
    FRED vintage bind here is additive validation performed *before* the
    unmodified ``build_us`` arithmetic runs, never a re-implementation of it.
    Field names (``us_market_reference``, ``trend_etfs``, ``proxy_axes``,
    ``fred``, ``fred_liquidity.series``) are exactly ``build_us``'s own input
    shape; nothing here is invented.
    """
    reference = packet.get("us_market_reference")
    require(isinstance(reference, dict) and reference.get("status") == "READY",
            "US_REFERENCE_NOT_READY")
    session_date = reference.get("as_of_session_date")
    require(isinstance(session_date, str) and DATE10.fullmatch(session_date) is not None,
            "US_SESSION_DATE_INVALID")

    # TREND / BREADTH / LEADERSHIP: SESSION_EXACT_MATCH per
    # config/regime_semantic_freshness_policy_v1.json markets.US.session_based_axes.
    # All three share one us_market_reference.as_of_session_date in this packet
    # shape, so one check covers all three; carry/substitution stays FORBIDDEN --
    # this function never reuses an older session's directions for a newer date.

    fred = packet.get("fred")
    require(isinstance(fred, dict), "US_VIX_MISSING")
    # RISK_VOL: RELEASE_CYCLE_LATEST_FETCH -- session_date_coercion is FORBIDDEN,
    # so this is bound against the evaluation date, never the ETF session date.
    _fred_vintage_covers(evaluation_date, fred, "VIXCLS")

    liquidity_rows = packet.get("fred_liquidity", {}).get("series")
    require(isinstance(liquidity_rows, list) and liquidity_rows, "US_LIQUIDITY_INVALID")
    for row in liquidity_rows:
        label = row.get("series_id") if isinstance(row, dict) else None
        require(isinstance(label, str) and label, "US_LIQUIDITY_INVALID")
        # LIQUIDITY: RELEASE_CYCLE_LATEST_FETCH, same evaluation-date anchor.
        _fred_vintage_covers(evaluation_date, row, label)

    built = REFERENCE.build_us(packet, policy)
    directions = {row["axis"]: row["direction"] for row in built["axes"]}
    require(set(directions) == set(COMMON.load_common_v1_policy()["required_axes"]),
            "US_AXES_INCOMPLETE")
    return directions, session_date


def _skeleton(evaluation_at: str, code_revision: str) -> dict:
    return {
        "schema_version": SCHEMA,
        "market": "US",
        "evaluation_at": evaluation_at,
        "code_revision": code_revision,
        "evidence_class": None,
        "decision_status": "BLOCKED",
        "paper_regime": "UNKNOWN",
        # Structural gate 3 (module docstring): the only assignment of this key
        # anywhere in this module. It is never reassigned below.
        "runtime_regime": "UNKNOWN",
        "direction": "UNKNOWN",
        "confidence": None,
        "runtime_decision_available": False,
        "us_pit_accepted_bundle_available": False,
        "us_paper_runtime_adoption_ratified": False,
        "pit_acceptance": None,
        "latest_completed_session_date": None,
        "current_observation": None,
        "aggregation": None,
        "reasons": [],
        "authority": {
            "us_paper_experiment_calculation_only": True,
            "paper_runtime_display_authorized": False,
            "operational_policy_ratified": False,
            "strategy_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "capital_authorized": False,
            "order_authorized": False,
            "real_order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_authorized": False,
        },
    }


def evaluate_us_paper_runtime(
    *,
    evaluation_at: str,
    code_revision: str,
    pit_acceptance_bundle: object = None,
    current_source_packet: dict | None = None,
    reference_policy: dict | None = None,
    latest_completed_session_date: str | None = None,
) -> dict:
    """Pure calculation. No IO writes, registry ratification, or order authority.

    ``pit_acceptance_bundle`` must be the real, unmodified output of
    ``regime.us_historical_replay_population.build_population`` -- this function
    never trusts a caller's claim about it; it hands the object, byte-for-byte,
    to the existing ``market_scoped_pit_acceptance.evaluate_market_pit_acceptance``
    gate, unmodified. ``current_source_packet`` is the live-session packet shaped
    exactly like ``data/latest_free_market_data.json``
    (``regime.paper_regime_reference.build_us``'s own input). None of these
    arguments is defaulted to a fixture; a missing one fails closed.

    ``latest_completed_session_date`` is the caller-attested latest officially
    completed NYSE session date.  Unlike KR, this repository has no NYSE session
    calendar module yet (KR's independently verified
    ``market_data/krx_session_calendar_v2.py`` has no US equivalent) -- this is a
    known, documented limitation of this draft, not something this function
    hides: see the accompanying design doc.
    """
    now = _time(evaluation_at)
    require(CODE_REVISION.fullmatch(code_revision) is not None, "CODE_REVISION_REQUIRED")
    packet = _skeleton(evaluation_at, code_revision)
    try:
        # --- Gate 1 (U3): real US PIT acceptance, reused unmodified. ---
        acceptance = PIT_ACCEPTANCE.evaluate_market_pit_acceptance("US", pit_acceptance_bundle)
        packet["pit_acceptance"] = acceptance
        pit_ok = acceptance["status"] == PIT_ACCEPTANCE.STATUS_PIT_ACCEPTED
        packet["us_pit_accepted_bundle_available"] = pit_ok

        # --- Gate 2 (U5): real adoption-identity ratification, file-backed. ---
        adoption_ok = _us_runtime_adoption_ratified()
        packet["us_paper_runtime_adoption_ratified"] = adoption_ok

        require(pit_ok, "US_PIT_NOT_ACCEPTED")
        require(adoption_ok, "US_PAPER_RUNTIME_ADOPTION_NOT_RATIFIED")

        # Everything below is unreachable today: gate 1 cannot pass while
        # regime.us_historical_replay_population never populates BREADTH/
        # LEADERSHIP (see module docstring), independent of gate 2.
        require(reference_policy is not None, "US_REFERENCE_POLICY_MISSING")
        require(current_source_packet is not None, "US_CURRENT_SOURCE_PACKET_MISSING")
        require(
            isinstance(latest_completed_session_date, str)
            and DATE10.fullmatch(latest_completed_session_date) is not None,
            "US_LATEST_COMPLETED_SESSION_DATE_MISSING",
        )
        packet["latest_completed_session_date"] = latest_completed_session_date
        _freshness_policy_ratified_for_us()

        directions, live_session_date = _live_axis_directions(
            current_source_packet, reference_policy, now.date(),
        )
        # Session-exact match, no carry/substitution -- a stale-but-present
        # session falls back to UNKNOWN via this require(), never silently
        # reused (config/regime_semantic_freshness_policy_v1.json
        # not_advanced_rule).
        require(live_session_date == latest_completed_session_date,
                "SOURCE_NOT_ADVANCED_EXPECTED_SESSION")

        records = PIT_ACCEPTANCE._real_evidence_bundle("US", pit_acceptance_bundle)
        historical_sequence = PIT_ACCEPTANCE._build_sequence("US", records)
        require(historical_sequence is not None, "US_HISTORICAL_SEQUENCE_EMPTY")

        sequence = copy.deepcopy(historical_sequence)
        sequence["case_id"] = "us-paper-runtime"
        sequence["steps"].append({
            "packet_id": f"us-live-{live_session_date}",
            "as_of_date": live_session_date,
            "axes": {axis: {"status": "DEFINED", "direction": direction}
                     for axis, direction in directions.items()},
        })
        replay = COMMON.replay_common_v1(sequence)
        packet["aggregation"] = replay
        packet["evidence_class"] = "LIVE_NATURAL"
        current = replay["steps"][-1]
        packet["current_observation"] = {
            "as_of_date": live_session_date,
            "candidate_regime": current["raw_classification"],
            "score": current["score"],
            "confirmed_regime": current["confirmed_regime"],
            "hysteresis": current["hysteresis"],
        }
        packet["paper_regime"] = replay["final_regime"]
        packet["direction"] = replay["final_direction"]
        packet["confidence"] = replay["final_confidence"]
        if replay["final_regime"] == "UNKNOWN":
            packet["reasons"] = ["COMMON_CONFIRMATION_PENDING"]
        else:
            # PAPER simulation only. runtime_regime (set once above, in
            # _skeleton) is never touched here -- see module docstring gate 3.
            packet["decision_status"] = "PAPER_SIMULATION_CLASSIFIED"
    except (
        USRuntimeError,
        PIT_ACCEPTANCE.MarketScopedPitAcceptanceError,
        REFERENCE.PaperRegimeReferenceError,
        COMMON.DecisionAuthorityError,
        OSError, KeyError, TypeError, ValueError,
    ) as exc:
        code = str(exc).split(":", 1)[0] if isinstance(exc, (
            USRuntimeError, PIT_ACCEPTANCE.MarketScopedPitAcceptanceError,
            REFERENCE.PaperRegimeReferenceError, COMMON.DecisionAuthorityError,
        )) else "INPUT_SHAPE_INVALID"
        packet["reasons"] = [code]
    packet["decision_id"] = "us-paper-regime:" + COMMON.payload_sha256(packet)
    return packet


def validate_us_paper_runtime(packet: dict, **inputs) -> dict:
    """Rebuild the full result from separately supplied inputs and compare.

    Mirrors ``kr_paper_runtime.validate_kr_paper_runtime`` exactly: a rehashed
    tamper of any output field, including a boolean/numeric alias, fails this
    rederivation closed.
    """
    expected = evaluate_us_paper_runtime(**inputs)
    require(COMMON.canonical_bytes(packet) == COMMON.canonical_bytes(expected),
            "RUNTIME_REDERIVATION_MISMATCH")
    return copy.deepcopy(expected)
