#!/usr/bin/env python3
"""Cross-market comparability at one common decision timestamp.

Why this module exists
----------------------
``regime/paper_regime_runtime_adoption.compare_market_rows`` declares three
markets comparable only when their unchanged decision dates are *identical*
(``len(groups) == 1``).  The US market observes US sessions, KR observes KRX
sessions and Crypto never closes, so an exact three-way date identity is a
coincidence rather than a reachable steady state: over the twenty committed
PAPER reference days 2026-08-28..2026-09-19 the three ``as_of_date`` values
coincided on exactly one day, and on that day Crypto was ``UNKNOWN``.  A
COMPLETE label conditioned on date identity is therefore unreachable in
practice, while forcing the dates to agree is explicitly unauthorized
(``config/paper_regime_runtime_adoption_v1.json``
``comparability_and_caveats.cross_market_date_coercion_authorized = false``).

What this module does instead
-----------------------------
Comparison is anchored to a **common decision timestamp** ``evaluation_at``
(``T``), never to a shared calendar date:

1. ``T`` is supplied by the caller and is never inferred.  The adopted
   ``evaluation_clock`` convention is *"Explicit caller-supplied
   evaluation_at, retained and hash-bound.  Never infer from source
   generated_at or local wall clock."*  A missing or malformed ``T`` is a
   contract error, not a default.
2. Each market contributes **its own latest session that was finalized and
   available at or before T**.  Sessions are never shifted, relabelled or
   substituted, and a session whose ``available_at`` is after ``T`` is
   rejected as not-yet-available rather than back-dated.
3. Differing dates are *recorded*, not repaired.  Every market keeps its own
   ``as_of_date``, its own ``available_at`` and its own lag from ``T``
   (``lag_seconds`` / ``lag_hours``), and the result carries the distinct
   dates and their spread so the disagreement stays visible downstream.
4. COMPLETE no longer means "the three dates agree".  It means "every market's
   own freshness rule is satisfied at T".  That per-market rule is the one
   that already exists and is reused verbatim: a market is inside its budget
   when its ``market_eligibility`` is one of the adopted
   ``CURRENT``/``CURRENT_AS_FETCHED_NOT_PIT`` states, which the adopted record
   derives per market from that market's own
   ``source_frequency_rules`` entry.  No new numeric budget is invented here;
   see ``FRESHNESS_BUDGET`` for what the adopted record does and does not
   define.
5. One unusable market removes **that market only**.  Following the ratified
   per-market freshness mode already used by
   ``shadow/crypto_paper_runtime_bridge`` (``PER_MARKET_RATIFIED``: "one
   market's realtime evidence is not usable ... under per-market freshness
   this is that market's blocker only"), an excluded market is recorded with
   explicit reason codes and the remaining markets stay comparable among
   themselves.  A two-market comparison is *labelled* as a two-market
   comparison and never reported as a three-market one.

Authority
---------
This module classifies nothing, reads no file, opens no socket, reads no
clock and grants no authority.  It is a pure function over caller-supplied
rows.  It never sets or consults any trading, capital, order or sizing
authority flag, and it never writes ``price_date`` into a decision date.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = "cross_market_comparability/1"

#: Per-market freshness mode name, taken from the ratified crypto PAPER
#: runtime bridge (``shadow/crypto_paper_runtime_bridge.PER_MARKET_FRESHNESS_MODE``)
#: so the two subsystems name the same behaviour the same way.
COMPARABILITY_MODE = "PER_MARKET_RATIFIED"

COMPARISON_BASIS = "COMMON_DECISION_TIMESTAMP_PER_MARKET_LATEST_COMPLETED_SESSION"

MARKETS = ("US", "KR", "CRYPTO")

#: Reused verbatim from the adopted record; not a new threshold.
WITHIN_BUDGET_ELIGIBILITY_STATES = ("CURRENT", "CURRENT_AS_FETCHED_NOT_PIT")

CLASSIFIED_REGIMES = ("RISK_ON", "RISK_OFF", "NEUTRAL", "STRESS")

#: Exactly where the per-market freshness rule comes from, and what the
#: adopted record does *not* define.  ``numeric_lag_budget`` is ``None``
#: because no hour/day lag allowance exists anywhere in the adopted record:
#: the adopted rule is state membership, evaluated per market from that
#: market's own source-frequency rule, plus the already-enforced ordering
#: constraint that a session cannot be available after the decision
#: timestamp.  Callers that need a numeric allowance must ratify one; this
#: module reports its absence instead of inventing it.
FRESHNESS_BUDGET = {
    "mode": COMPARABILITY_MODE,
    "within_budget_states": list(WITHIN_BUDGET_ELIGIBILITY_STATES),
    "state_rule_source": (
        "config/paper_regime_runtime_adoption_v1.json"
        "::eligibility_states + comparability_and_caveats.consumer_complete_gate"
    ),
    "per_market_derivation_source": (
        "config/paper_regime_runtime_adoption_v1.json::source_frequency_rules"
    ),
    "ordering_rule_source": (
        "regime/paper_regime_runtime_adoption.py::FUTURE_SOURCE_AVAILABLE_AT"
    ),
    "per_market_mode_source": (
        "shadow/crypto_paper_runtime_bridge.py::PER_MARKET_FRESHNESS_MODE"
    ),
    "numeric_lag_budget": None,
    "numeric_lag_budget_status": "NOT_DEFINED:PER_MARKET_NUMERIC_LAG_BUDGET",
}

# Reason codes.  A market is excluded from the comparison for one or more of
# these, and the codes stay in the result so the exclusion is auditable.
REASON_MARKET_ROW_ABSENT = "MARKET_ROW_ABSENT"
REASON_MARKET_DUPLICATED = "MARKET_ROW_DUPLICATED"
REASON_MARKET_UNKNOWN_NAME = "MARKET_NOT_IN_COMPARISON_SET"
REASON_DECISION_DATE_INVALID = "DECISION_DATE_ABSENT_OR_MALFORMED"
REASON_AVAILABLE_AT_INVALID = "AVAILABLE_AT_ABSENT_OR_MALFORMED"
REASON_AVAILABLE_AFTER_T = "SESSION_NOT_AVAILABLE_AT_DECISION_TIMESTAMP"
REASON_DECISION_DATE_AFTER_T = "DECISION_DATE_AFTER_DECISION_TIMESTAMP"
REASON_OUTSIDE_BUDGET = "MARKET_ELIGIBILITY_OUTSIDE_FRESHNESS_BUDGET"
REASON_REGIME_UNCLASSIFIED = "CANDIDATE_REGIME_NOT_CLASSIFIED"
REASON_ASSESSMENTS_INCOMPLETE = "REQUIRED_ASSESSMENTS_INCOMPLETE"

ISO_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


class CrossMarketComparabilityError(ValueError):
    """Raised only when the API contract itself is unusable."""


def _timestamp(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not ISO_TIMESTAMP.match(value):
        raise CrossMarketComparabilityError(f"{label}_TIMESTAMP_INVALID")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise CrossMarketComparabilityError(f"{label}_TIMESTAMP_NOT_OFFSET_AWARE")
    return parsed.astimezone(dt.timezone.utc)


def _optional_timestamp(value: Any) -> dt.datetime | None:
    try:
        return _timestamp(value, "OPTIONAL")
    except CrossMarketComparabilityError:
        return None


def _date(value: Any) -> dt.date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _candidate_regime(row: Mapping[str, Any]) -> Any:
    judgement = row.get("judgement")
    if isinstance(judgement, Mapping) and "candidate_regime" in judgement:
        return judgement.get("candidate_regime")
    reference = row.get("paper_reference")
    if isinstance(reference, Mapping) and "candidate_regime" in reference:
        return reference.get("candidate_regime")
    return row.get("candidate_regime")


def require_evaluation_at(evaluation: Any) -> dt.datetime:
    """Return the caller-supplied common decision timestamp ``T``.

    Never falls back to a clock, to ``generated_at``, or to any date found in
    the rows.  Absence is an error by design: the adopted ``evaluation_clock``
    convention requires an explicit, retained, hash-bindable ``T``.
    """
    if evaluation is None:
        raise CrossMarketComparabilityError("EVALUATION_AT_REQUIRED_NEVER_INFERRED")
    return _timestamp(evaluation, "EVALUATION_AT")


def select_market_session(
    candidates: Sequence[Mapping[str, Any]],
    *,
    evaluation_at: Any,
) -> dict[str, Any]:
    """Pick one market's latest session finalized and available at or before T.

    ``candidates`` are that market's own retained sessions.  Selection orders
    by ``available_at`` and breaks ties by ``as_of_date``; nothing is shifted,
    and a session that only became available after ``T`` is reported in
    ``rejected_not_yet_available`` rather than pulled backwards.
    """
    at = require_evaluation_at(evaluation_at)
    usable: list[tuple[dt.datetime, dt.date, Mapping[str, Any]]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        as_of = _date(candidate.get("as_of_date") or candidate.get("decision_date"))
        available = _optional_timestamp(candidate.get("available_at"))
        if as_of is None:
            rejected.append({
                "as_of_date": candidate.get("as_of_date")
                or candidate.get("decision_date"),
                "available_at": candidate.get("available_at"),
                "reason_codes": [REASON_DECISION_DATE_INVALID],
            })
            continue
        if available is None:
            rejected.append({
                "as_of_date": as_of.isoformat(),
                "available_at": candidate.get("available_at"),
                "reason_codes": [REASON_AVAILABLE_AT_INVALID],
            })
            continue
        if available > at:
            rejected.append({
                "as_of_date": as_of.isoformat(),
                "available_at": candidate.get("available_at"),
                "reason_codes": [REASON_AVAILABLE_AFTER_T],
            })
            continue
        usable.append((available, as_of, candidate))
    usable.sort(key=lambda item: (item[0], item[1]))
    selected = dict(usable[-1][2]) if usable else None
    return {
        "evaluation_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "selected": selected,
        "considered_count": len(candidates),
        "usable_count": len(usable),
        "rejected_not_yet_available": rejected,
    }


def _evaluate_row(
    row: Mapping[str, Any],
    *,
    evaluation_at: dt.datetime,
    within_budget_states: Sequence[str],
) -> dict[str, Any]:
    reasons: list[str] = []
    as_of = _date(row.get("decision_date") or row.get("as_of_date"))
    if as_of is None:
        reasons.append(REASON_DECISION_DATE_INVALID)
    elif as_of > evaluation_at.date():
        reasons.append(REASON_DECISION_DATE_AFTER_T)

    available_raw = row.get("available_at")
    available = _optional_timestamp(available_raw)
    if available is None:
        reasons.append(REASON_AVAILABLE_AT_INVALID)
    elif available > evaluation_at:
        reasons.append(REASON_AVAILABLE_AFTER_T)

    eligibility = row.get("market_eligibility")
    if eligibility not in tuple(within_budget_states):
        reasons.append(f"{REASON_OUTSIDE_BUDGET}:{eligibility}")

    candidate = _candidate_regime(row)
    if candidate not in CLASSIFIED_REGIMES:
        reasons.append(f"{REASON_REGIME_UNCLASSIFIED}:{candidate}")

    if row.get("required_assessments_complete", False) is not True:
        reasons.append(REASON_ASSESSMENTS_INCOMPLETE)

    lag_seconds = None
    if available is not None:
        lag_seconds = int((evaluation_at - available).total_seconds())
    lag_calendar_days = None
    if as_of is not None:
        lag_calendar_days = (evaluation_at.date() - as_of).days

    return {
        "market": row.get("market"),
        # The market's own unchanged decision date.  Never coerced, never
        # replaced by another market's date and never by a price date.
        "as_of_date": row.get("decision_date") or row.get("as_of_date"),
        "price_date": row.get("price_date"),
        "available_at": available_raw,
        "lag_seconds": lag_seconds,
        "lag_hours": None if lag_seconds is None else round(lag_seconds / 3600.0, 3),
        "as_of_lag_calendar_days": lag_calendar_days,
        "market_eligibility": eligibility,
        "candidate_regime": candidate,
        "freshness_within_budget": not any(
            reason.startswith(REASON_OUTSIDE_BUDGET)
            or reason in {REASON_AVAILABLE_AFTER_T, REASON_AVAILABLE_AT_INVALID}
            for reason in reasons
        ),
        "comparable": not reasons,
        "exclusion_reason_codes": reasons,
    }


def compare_markets_at(
    rows: Sequence[Mapping[str, Any]],
    *,
    evaluation_at: Any,
    within_budget_states: Sequence[str] = WITHIN_BUDGET_ELIGIBILITY_STATES,
) -> dict[str, Any]:
    """Compare markets at one common decision timestamp without coercing dates.

    Each market keeps its own ``as_of_date`` and ``available_at``; the result
    records the lag of each market from ``T`` and the reason codes for any
    market left out.  Scope is labelled explicitly so a two-market comparison
    can never be read as a three-market one.
    """
    at = require_evaluation_at(evaluation_at)
    budget_states = tuple(within_budget_states)

    seen: dict[str, int] = {}
    per_market: dict[str, dict[str, Any]] = {}
    foreign: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise CrossMarketComparabilityError("COMPARISON_ROW_NOT_MAPPING")
        market = row.get("market")
        if market not in MARKETS:
            foreign.append({
                "market": market,
                "exclusion_reason_codes": [REASON_MARKET_UNKNOWN_NAME],
            })
            continue
        seen[market] = seen.get(market, 0) + 1
        evaluated = _evaluate_row(
            row, evaluation_at=at, within_budget_states=budget_states
        )
        if seen[market] > 1:
            first = per_market[market]
            first["comparable"] = False
            if REASON_MARKET_DUPLICATED not in first["exclusion_reason_codes"]:
                first["exclusion_reason_codes"].append(REASON_MARKET_DUPLICATED)
            evaluated["comparable"] = False
            evaluated["exclusion_reason_codes"].append(REASON_MARKET_DUPLICATED)
            continue
        per_market[market] = evaluated

    for market in MARKETS:
        if market not in per_market:
            per_market[market] = {
                "market": market,
                "as_of_date": None,
                "price_date": None,
                "available_at": None,
                "lag_seconds": None,
                "lag_hours": None,
                "as_of_lag_calendar_days": None,
                "market_eligibility": None,
                "candidate_regime": None,
                "freshness_within_budget": False,
                "comparable": False,
                "exclusion_reason_codes": [REASON_MARKET_ROW_ABSENT],
            }

    ordered = [per_market[market] for market in MARKETS]
    comparable = [entry["market"] for entry in ordered if entry["comparable"]]
    excluded = {
        entry["market"]: list(entry["exclusion_reason_codes"])
        for entry in ordered
        if not entry["comparable"]
    }

    as_of_by_market = {entry["market"]: entry["as_of_date"] for entry in ordered}
    comparable_dates = sorted({
        entry["as_of_date"]
        for entry in ordered
        if entry["comparable"] and entry["as_of_date"]
    })
    parsed_dates = [d for d in (_date(value) for value in comparable_dates) if d]
    spread_days = (
        (max(parsed_dates) - min(parsed_dates)).days if len(parsed_dates) > 1 else 0
    )

    if len(comparable) == 3:
        scope = "THREE_MARKET"
        status = "COMPLETE_THREE_MARKET"
    elif len(comparable) == 2:
        scope = "TWO_MARKET"
        status = "COMPLETE_TWO_MARKET"
    elif len(comparable) == 1:
        scope = "ONE_MARKET"
        status = "NOT_COMPARABLE_SINGLE_MARKET"
    else:
        scope = "NONE"
        status = "NOT_COMPARABLE_NO_MARKET"

    lags = {
        entry["market"]: entry["lag_hours"]
        for entry in ordered
        if entry["lag_hours"] is not None
    }

    result = {
        "schema_version": SCHEMA_VERSION,
        "comparison_basis": COMPARISON_BASIS,
        "comparability_mode": COMPARABILITY_MODE,
        "evaluation_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "comparison_status": status,
        "comparison_scope": scope,
        # Retained for existing consumers: still only COMPLETE when all three
        # markets are comparable.  It is now reachable because the gate is the
        # per-market freshness rule instead of calendar-date identity.
        "three_market_comparison_status": (
            "COMPLETE" if scope == "THREE_MARKET" else "PARTIAL_OR_NON_COMPARABLE"
        ),
        "complete": scope == "THREE_MARKET",
        "comparable_markets": comparable,
        "comparable_market_count": len(comparable),
        "excluded_markets": excluded,
        "markets": ordered,
        "as_of_date_by_market": as_of_by_market,
        "distinct_as_of_dates": comparable_dates,
        "same_decision_date": len(comparable_dates) == 1 and len(comparable) > 1,
        "as_of_date_spread_days": spread_days,
        "lag_hours_by_market": lags,
        "freshness_budget": dict(FRESHNESS_BUDGET, within_budget_states=list(budget_states)),
        "date_coercion_used": False,
        "cross_market_date_coercion_authorized": False,
        "price_date_substitution_used": False,
        "foreign_rows": foreign,
    }
    _assert_no_date_coercion(result, rows)
    return result


def _assert_no_date_coercion(
    result: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> None:
    """Fail closed if any market's decision date changed on the way out."""
    supplied: dict[str, list[Any]] = {}
    for row in rows:
        market = row.get("market")
        if market in MARKETS:
            supplied.setdefault(market, []).append(
                row.get("decision_date") or row.get("as_of_date")
            )
    for entry in result["markets"]:
        market = entry["market"]
        if market in supplied and entry["as_of_date"] not in supplied[market]:
            raise CrossMarketComparabilityError(
                f"DATE_COERCION_DETECTED:{market}"
            )


__all__ = [
    "CLASSIFIED_REGIMES",
    "COMPARABILITY_MODE",
    "COMPARISON_BASIS",
    "CrossMarketComparabilityError",
    "FRESHNESS_BUDGET",
    "MARKETS",
    "SCHEMA_VERSION",
    "WITHIN_BUDGET_ELIGIBILITY_STATES",
    "compare_markets_at",
    "require_evaluation_at",
    "select_market_session",
]
