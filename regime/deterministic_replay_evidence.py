#!/usr/bin/env python3
"""P1-COM-05 deterministic replay evidence — facts only, no policy conclusion.

CIO mandate (2026-09-04) closing slice of the P1-COM-05 replay set: take the
combined KR+US SHADOW historical replay population produced by
``regime/combined_shadow_historical_replay.py`` and summarize, deterministically,
exactly five families of *fact*:

1. **Coverage** — how many caller-supplied dates each market and each of the ten
   market/axis slots actually produced an observation for.
2. **UNKNOWN** — how many did not, split by *why*, keeping the three meanings
   apart: ``UNKNOWN`` (deliberately outside ratified scope, e.g. US
   BREADTH/LEADERSHIP), ``NOT_COMPUTABLE`` (attempted, source could not support
   it), and ``NOT_ATTEMPTED_DATE_BLOCKED`` (the whole date failed closed in that
   market).  None of the three is ever collapsed into another, and none is ever
   reported as ``NEUTRAL``.
3. **Transitions** — where the unmodified candidate rule's own direction and
   candidate-regime values changed between adjacent observations, split into a
   real state change and a mere evidence-availability change (any pair touching
   ``UNKNOWN``), because those two are not the same event.
4. **Stress detection** — how often the existing rule already emitted a
   ``STRESS`` axis direction or a ``STRESS`` candidate regime, and on which
   dates and axes.
5. **Hysteresis facts** — the raw run lengths, single-observation runs, and
   immediate A→B→A reversals present in that sequence.

This module invents nothing:

* It issues no KRX/Alpaca/FRED request, derives no axis, and re-runs no
  normalization.  Every direction, candidate regime, coverage ratio, and reason
  code it counts was already produced by
  ``regime/kr_historical_replay_population.py`` /
  ``regime/us_historical_replay_population.py`` and joined by
  ``regime/combined_shadow_historical_replay.py``.  The input population is
  re-checked by *that* module's own validator before a single fact is derived,
  so this report can never summarize a population its owning contract rejects.
* It selects no episode.  Dates come only from the caller, exactly as the
  combined slice defines them; an episode label is carried verbatim and never
  reads on any count.  ``test/test_deterministic_replay_evidence.py`` pins that
  by proving a labelled and an unlabelled run produce identical facts.
* **It reaches no policy or threshold conclusion.**  This is the load-bearing
  guarantee of the slice.  Stress counts are counts, not evidence that a stress
  threshold is right; transition and run-length counts are counts, not evidence
  that hysteresis is needed, nor a proposed dwell time.  ``hysteresis_applied``
  is ``false`` — no buffer, confirmation count, or dwell rule is applied to the
  sequence, because ``evidence/regime/policy_candidates/candidate_inventory.json``
  still records ``HYSTERESIS`` and ``STRESS_OVERRIDE`` as ``BLOCKED`` under a
  ``DRAFT_NOT_RATIFIED`` policy.  That basis is read from disk rather than
  asserted, and a component that ever becomes ``SUPPORTED`` fails this module
  closed so a human re-decides the framing instead of this file quietly
  continuing to describe a ratified rule as absent.  ``validate_evidence``
  rejects any report carrying a conclusion, verdict, recommendation, or proposed
  parameter — including one added under a new key name.

Point-in-time integrity and historical audit are kept apart, as
``docs/ATLAS_SESSION_BOOTSTRAP.md`` requires:

* No date's observation is created, altered, promoted, or graded here.  Every
  per-date fact is read from that one date's own already-replayed record.
* Transition, run-length, and reversal facts are unavoidably cross-date — that
  is what a transition *is* — so they are labelled as descriptions of an
  already-fixed replay set that never feed back into any date's evaluation.
* Adjacency is *requested-date order*, not calendar adjacency.  A caller may
  request three dates months apart; each sequence therefore carries its own
  calendar-gap facts so a transition count between distant observations can
  never be misread as a next-session flip.

Output is refused anywhere inside this repository checkout — external ``--out``
or a private system-temp file only — because it summarizes SHADOW
historical-backfill evidence and must never be mistaken for a NATURAL
observation.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import combined_shadow_historical_replay as CSR  # noqa: E402
from regime import paper_regime_reference as PRR  # noqa: E402


SCHEMA_VERSION = "regime_deterministic_replay_evidence/v1"
MODE = "SHADOW_REPLAY_EVIDENCE_ONLY_NOT_POLICY"
EVIDENCE_CLASS = CSR.EVIDENCE_CLASS
MARKETS = CSR.MARKETS
AXES = tuple(PRR.AXES)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE10 = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# How one axis behaved on one date, in this report's own vocabulary. Total and
# explicit: the three "no observation" meanings stay distinct, and a source
# status this module does not recognize becomes its own bucket rather than
# being silently counted as observed.
AXIS_OBSERVED = "OBSERVED"
AXIS_NOT_COMPUTABLE = "NOT_COMPUTABLE"
AXIS_UNKNOWN_BY_SCOPE = "UNKNOWN_EXCLUDED_BY_RATIFICATION_SCOPE"
AXIS_DATE_BLOCKED = "NOT_ATTEMPTED_DATE_BLOCKED"
AXIS_UNRECOGNIZED = "UNRECOGNIZED_SOURCE_AXIS_STATUS"
AXIS_STATUSES = (
    AXIS_OBSERVED,
    AXIS_NOT_COMPUTABLE,
    AXIS_UNKNOWN_BY_SCOPE,
    AXIS_DATE_BLOCKED,
    AXIS_UNRECOGNIZED,
)
SOURCE_AXIS_STATUS = {
    "OBSERVED": AXIS_OBSERVED,
    "NOT_COMPUTABLE": AXIS_NOT_COMPUTABLE,
}

# A state that means "we do not know", never "we observed a middle value".
UNKNOWN_STATE = "UNKNOWN"
STRESS_STATE = "STRESS"

TRANSITION_STATE_CHANGE = "STATE_CHANGE"
TRANSITION_EVIDENCE_CHANGE = "EVIDENCE_AVAILABILITY_CHANGE"

ADJACENCY_BASIS = "REQUESTED_DATE_ORDER_NOT_CALENDAR_ADJACENCY"

POLICY_INVENTORY_PATH = "evidence/regime/policy_candidates/candidate_inventory.json"
POLICY_CONTRACT_VERSION = "regime_policy_candidate_evidence/v1"
POLICY_STATUS = "DRAFT_NOT_RATIFIED"
# The two components whose *absence* this report's framing depends on. If either
# is ever ratified, "no hysteresis applied" and "no stress threshold proposed"
# stop being safe descriptions and a human must re-decide.
REQUIRED_UNRATIFIED_COMPONENTS = ("HYSTERESIS", "STRESS_OVERRIDE")
BLOCKED_COMPONENT_STATUS = "BLOCKED"

CONCLUSION_STATUS = "WITHHELD_NO_POLICY_OR_THRESHOLD_AUTHORITY"
# Key names that would turn this fact sheet into a recommendation. Checked
# recursively over the whole report so a conclusion cannot be smuggled in under
# a new field name.
FORBIDDEN_KEY_PREFIXES = (
    "recommended_", "proposed_", "suggested_", "optimal_", "tuned_", "ratified_",
)
FORBIDDEN_KEY_NAMES = frozenset({
    "verdict", "conclusion_value", "policy_decision", "threshold_value",
    "acceptance_decision", "recommendation",
})


class ReplayEvidenceError(ValueError):
    """Deterministic replay evidence cannot be safely derived or published."""


def fail(code: str, detail: str = "") -> None:
    raise ReplayEvidenceError(f"{code}:{detail}" if detail else code)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReplayEvidenceError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ReplayEvidenceError(f"SOURCE_MISSING:{path}") from exc


def reason_code(text: object) -> str | None:
    """The attributable leading code of a recorded reason, never its detail.

    Mirrors ``regime/normalization_replay_readiness.py::_error_code``. Counting
    codes rather than whole messages keeps a histogram bounded and deterministic
    and drops per-date detail that has no place in a summary.
    """
    if not isinstance(text, str) or not text:
        return None
    return text.split(":", 1)[0]


def _calendar_gap_days(earlier: object, later: object) -> int | None:
    """Whole days between two ISO dates, or ``None`` if either is not one.

    A caller may request dates that are not calendar-adjacent (or not even
    parseable, since a malformed date is a legitimate blocked record), so a gap
    is reported as unknown rather than guessed.
    """
    if (
        not isinstance(earlier, str) or DATE10.fullmatch(earlier) is None
        or not isinstance(later, str) or DATE10.fullmatch(later) is None
    ):
        return None
    try:
        return (dt.date.fromisoformat(later) - dt.date.fromisoformat(earlier)).days
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Unratified-policy basis, read from disk rather than asserted.
# ---------------------------------------------------------------------------


def load_policy_basis(root: Path = ROOT) -> dict:
    """Record that HYSTERESIS and STRESS_OVERRIDE are still unratified.

    This module describes a replay in which *no* hysteresis was applied and *no*
    stress threshold was proposed. That description is only honest while those
    components remain unsupported, so the claim is read from the repository's own
    candidate inventory and fails closed the moment either is ratified.
    """
    path = Path(root) / POLICY_INVENTORY_PATH
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayEvidenceError(f"POLICY_BASIS_UNREADABLE:{POLICY_INVENTORY_PATH}") from exc
    if not isinstance(inventory, dict) or inventory.get("contract_version") != POLICY_CONTRACT_VERSION:
        fail("POLICY_BASIS_INVALID", "contract_version")
    if inventory.get("policy_status") != POLICY_STATUS:
        fail(
            "POLICY_STATUS_CHANGED",
            "the candidate policy is no longer DRAFT_NOT_RATIFIED; this module"
            " must not keep describing hysteresis and stress override as absent",
        )
    parameters = inventory.get("parameters")
    if not isinstance(parameters, list):
        fail("POLICY_BASIS_INVALID", "parameters")
    by_component = {
        row.get("component"): row for row in parameters if isinstance(row, dict)
    }
    component_status = {}
    for component in REQUIRED_UNRATIFIED_COMPONENTS:
        row = by_component.get(component)
        if not isinstance(row, dict):
            fail("POLICY_BASIS_INVALID", component)
        status = row.get("status")
        if status != BLOCKED_COMPONENT_STATUS:
            fail(
                "POLICY_COMPONENT_STATUS_CHANGED",
                f"{component}={status}; a ratified component must be applied or"
                " explicitly excluded by a human, not silently ignored here",
            )
        component_status[component] = status
    return {
        "path": POLICY_INVENTORY_PATH,
        "sha256": file_sha256(path),
        "candidate_id": inventory.get("candidate_id"),
        "candidate_status": inventory.get("candidate_status"),
        "policy_status": inventory.get("policy_status"),
        "component_status": component_status,
        "statement": (
            "HYSTERESIS and STRESS_OVERRIDE are both BLOCKED in the repository's"
            " own candidate inventory under a DRAFT_NOT_RATIFIED policy. This"
            " report therefore applies no hysteresis and proposes no stress"
            " threshold; it only counts what the existing unmodified candidate"
            " rule already emitted."
        ),
    }


# ---------------------------------------------------------------------------
# Per-date, per-market observation projection.
# ---------------------------------------------------------------------------


def _axis_directions(candidate: object) -> dict:
    """Axis -> direction from a market's own candidate normalization rows.

    The rows are ``regime/paper_regime_reference.py::axis`` output carried
    verbatim through both replay populations; nothing is recomputed here.
    """
    rows = candidate.get("axes") if isinstance(candidate, dict) else None
    if not isinstance(rows, list):
        return {}
    return {
        row["axis"]: row.get("direction")
        for row in rows
        if isinstance(row, dict) and row.get("axis") in AXES
    }


def market_observation(view: dict, record: object, excluded: frozenset) -> dict:
    """Project one market's join view (+ its own record) into per-axis facts.

    The join's ``outcome`` is authoritative: a market the join demoted to
    ``BLOCKED`` — including one contained for a lookahead violation — is treated
    as having produced nothing on that date, and its underlying axes are never
    read. That is what keeps a contained date from leaking into any count here.

    ``excluded`` is the market population's *own* declared out-of-scope axis set.
    A source ``UNKNOWN`` is only reported as a ratification-scope exclusion when
    the population actually declares that axis excluded; any other ``UNKNOWN``
    is left unrecognized rather than granted an exclusion basis it never claimed.
    """
    blocked = view.get("outcome") == CSR.OUTCOME_BLOCKED
    if blocked or not isinstance(record, dict):
        return {
            "outcome": CSR.OUTCOME_BLOCKED,
            "blocked_reason_code": reason_code(view.get("failure_reason")),
            "lookahead_contained": bool(view.get("lookahead_violation")),
            "candidate_regime": None,
            "candidate_classification_status": None,
            "axis_status": {name: AXIS_DATE_BLOCKED for name in AXES},
            "axis_direction": {name: None for name in AXES},
            "axis_reason_code": {name: None for name in AXES},
        }

    five_axis = record.get("five_axis")
    axes = five_axis.get("axes") if isinstance(five_axis, dict) else None
    directions = _axis_directions(record.get("candidate_normalized_result"))

    axis_status = {}
    axis_reason = {}
    for name in AXES:
        entry = axes.get(name) if isinstance(axes, dict) else None
        if not isinstance(entry, dict):
            axis_status[name] = AXIS_UNRECOGNIZED
            axis_reason[name] = None
            continue
        source_status = entry.get("status")
        if source_status == "UNKNOWN" and name in excluded:
            status = AXIS_UNKNOWN_BY_SCOPE
        else:
            status = SOURCE_AXIS_STATUS.get(source_status, AXIS_UNRECOGNIZED)
        # An axis its own market calls observed but for which no direction row
        # survived is not counted as observed: the direction is what every
        # transition, stress, and run fact below is built from.
        if status == AXIS_OBSERVED and directions.get(name) is None:
            status = AXIS_UNRECOGNIZED
        axis_status[name] = status
        axis_reason[name] = reason_code(entry.get("reason"))

    return {
        "outcome": view.get("outcome"),
        "blocked_reason_code": None,
        "lookahead_contained": False,
        "candidate_regime": view.get("candidate_regime"),
        "candidate_classification_status": view.get("candidate_classification_status"),
        "axis_status": axis_status,
        "axis_direction": {
            name: (directions.get(name) if axis_status[name] == AXIS_OBSERVED else None)
            for name in AXES
        },
        "axis_reason_code": axis_reason,
    }


def declared_excluded_axes(embedded: object) -> frozenset:
    """The axes a market population itself declares out of ratified scope."""
    axes = embedded.get("excluded_axes") if isinstance(embedded, dict) else None
    if not isinstance(axes, dict):
        return frozenset()
    return frozenset(name for name in axes if name in AXES)


def observation_table(population: dict) -> dict:
    """``{market: {requested_date: observation}}`` for every requested date.

    Each cell is derived from that one date's own combined record plus that
    market's own replay record; no cell can see another date.
    """
    indexed = {}
    excluded = {}
    for market in MARKETS:
        embedded = population["market_populations"][market]
        indexed[market] = (
            {record["requested_date"]: record for record in embedded["records"]}
            if embedded is not None
            else {}
        )
        excluded[market] = declared_excluded_axes(embedded)
    table = {market: {} for market in MARKETS}
    for combined in population["records"]:
        date = combined["requested_date"]
        for market in MARKETS:
            table[market][date] = market_observation(
                combined["markets"][market], indexed[market].get(date), excluded[market],
            )
    return table


# ---------------------------------------------------------------------------
# Sequence facts: transitions, runs, reversals.
#
# One shared derivation for candidate regimes and axis directions alike, so the
# two can never drift apart in how they count a change.
# ---------------------------------------------------------------------------


def sequence_facts(points: list[tuple[str, str]]) -> dict:
    """Describe an ordered ``[(date, state)]`` sequence — count only, never judge.

    ``points`` covers **every** requested date, carrying ``UNKNOWN`` where that
    date produced no state. A date without evidence is deliberately not dropped:
    dropping it would let a run silently bridge a blocked date and report a
    continuity the replay never observed.

    A pair touching ``UNKNOWN`` is reported as an evidence-availability change,
    not a state change: "we stopped being able to observe" and "the market
    changed" are different events and collapsing them would misdescribe both.

    Runs and reversals are cross-date descriptions of an already-fixed replay
    set. They never alter a date's observation and are not, and must not be read
    as, an argument for any hysteresis parameter.
    """
    states = [state for _date, state in points]
    dates = [date for date, _state in points]

    transitions = []
    for index in range(len(points) - 1):
        before, after = states[index], states[index + 1]
        if before == after:
            continue
        touches_unknown = UNKNOWN_STATE in (before, after)
        transitions.append({
            "from_date": dates[index],
            "to_date": dates[index + 1],
            "from_state": before,
            "to_state": after,
            "kind": (
                TRANSITION_EVIDENCE_CHANGE if touches_unknown else TRANSITION_STATE_CHANGE
            ),
            "calendar_gap_days": _calendar_gap_days(dates[index], dates[index + 1]),
        })

    runs = []
    for index, (date, state) in enumerate(points):
        if runs and runs[-1]["state"] == state:
            runs[-1]["length"] += 1
            runs[-1]["end_date"] = date
        else:
            runs.append({
                "state": state, "length": 1, "start_date": date, "end_date": date,
            })

    reversals = [
        {
            "state": runs[index - 1]["state"],
            "interrupting_state": runs[index]["state"],
            "interrupting_date": runs[index]["start_date"],
            "before_date": runs[index - 1]["end_date"],
            "after_date": runs[index + 1]["start_date"],
        }
        for index in range(1, len(runs) - 1)
        if runs[index]["length"] == 1
        and runs[index - 1]["state"] == runs[index + 1]["state"]
        and UNKNOWN_STATE not in (
            runs[index - 1]["state"], runs[index]["state"], runs[index + 1]["state"],
        )
    ]

    gaps = [
        _calendar_gap_days(dates[index], dates[index + 1])
        for index in range(len(dates) - 1)
    ]
    known_gaps = [gap for gap in gaps if gap is not None]
    return {
        "sequence_length": len(points),
        "sequence_dates": dates,
        "observed_state_count": sum(1 for state in states if state != UNKNOWN_STATE),
        "unknown_state_count": sum(1 for state in states if state == UNKNOWN_STATE),
        "state_sequence": states,
        "distinct_states": sorted(set(states)),
        "transition_count": len(transitions),
        "state_change_count": sum(
            1 for row in transitions if row["kind"] == TRANSITION_STATE_CHANGE
        ),
        "evidence_availability_change_count": sum(
            1 for row in transitions if row["kind"] == TRANSITION_EVIDENCE_CHANGE
        ),
        "transitions": transitions,
        "runs": runs,
        "run_count": len(runs),
        "single_observation_run_count": sum(1 for run in runs if run["length"] == 1),
        "longest_run_length": max((run["length"] for run in runs), default=0),
        "immediate_reversal_count": len(reversals),
        "immediate_reversals": reversals,
        "adjacency": {
            "basis": ADJACENCY_BASIS,
            "adjacent_pair_count": max(len(points) - 1, 0),
            "calendar_consecutive_pair_count": sum(1 for gap in known_gaps if gap == 1),
            "unknown_calendar_gap_pair_count": sum(1 for gap in gaps if gap is None),
            "max_calendar_gap_days": max(known_gaps, default=None),
        },
    }


# ---------------------------------------------------------------------------
# Fact families.
# ---------------------------------------------------------------------------


def coverage_facts(population: dict, table: dict, dates: list[str]) -> dict:
    """Family 1 — how much of the requested replay actually produced evidence."""
    combined_counts = {status: 0 for status in CSR.COMBINED_STATUSES}
    for record in population["records"]:
        combined_counts[record["combined_status"]] += 1
    # Recomputed from the records rather than trusted: a summary that disagrees
    # with the records it claims to summarize must fail closed, not be copied.
    if combined_counts != population["combined_summary"]["combined_status_counts"]:
        fail("SOURCE_SUMMARY_INCONSISTENT", "combined_status_counts")

    markets = {}
    for market in MARKETS:
        rows = [table[market][date] for date in dates]
        classified = [
            row for row in rows
            if row["candidate_regime"] not in (None, UNKNOWN_STATE)
        ]
        markets[market] = {
            "requested_date_count": len(dates),
            "outcome_counts": {
                outcome: sum(1 for row in rows if row["outcome"] == outcome)
                for outcome in CSR.OUTCOMES
            },
            "dates_with_candidate_regime": sum(
                1 for row in rows if row["candidate_regime"] is not None
            ),
            "dates_with_classified_candidate_regime": len(classified),
            "population_available": population["market_population_status"][market]["available"],
            "axis_status_counts": {
                name: {
                    status: sum(1 for row in rows if row["axis_status"][name] == status)
                    for status in AXIS_STATUSES
                }
                for name in AXES
            },
            "axis_observed_ratio": {
                name: (
                    f"{sum(1 for row in rows if row['axis_status'][name] == AXIS_OBSERVED)}"
                    f"/{len(dates)}"
                )
                for name in AXES
            },
        }
    return {
        "requested_date_count": len(dates),
        "requested_dates": list(dates),
        "combined_status_counts": combined_counts,
        "markets": markets,
        "statement": (
            "Counts describe how many caller-supplied dates produced an"
            " observation. They are not a sufficiency finding: no minimum"
            " coverage, history length, or acceptance level is asserted here."
        ),
    }


def unknown_facts(table: dict, dates: list[str]) -> dict:
    """Family 2 — why evidence is absent, with the three meanings kept apart."""
    markets = {}
    for market in MARKETS:
        rows = [table[market][date] for date in dates]
        blocked_codes: dict[str, int] = {}
        for row in rows:
            code = row["blocked_reason_code"]
            if code:
                blocked_codes[code] = blocked_codes.get(code, 0) + 1
        axes = {}
        for name in AXES:
            not_computable_codes: dict[str, int] = {}
            for row in rows:
                if row["axis_status"][name] != AXIS_NOT_COMPUTABLE:
                    continue
                code = row["axis_reason_code"][name] or "UNATTRIBUTED"
                not_computable_codes[code] = not_computable_codes.get(code, 0) + 1
            axes[name] = {
                "unknown_excluded_by_ratification_scope": sum(
                    1 for row in rows if row["axis_status"][name] == AXIS_UNKNOWN_BY_SCOPE
                ),
                "not_computable": sum(
                    1 for row in rows if row["axis_status"][name] == AXIS_NOT_COMPUTABLE
                ),
                "not_attempted_date_blocked": sum(
                    1 for row in rows if row["axis_status"][name] == AXIS_DATE_BLOCKED
                ),
                "unrecognized_source_status": sum(
                    1 for row in rows if row["axis_status"][name] == AXIS_UNRECOGNIZED
                ),
                "not_computable_reason_codes": dict(sorted(not_computable_codes.items())),
                "excluded_axis_reason_codes": sorted({
                    row["axis_reason_code"][name]
                    for row in rows
                    if row["axis_status"][name] == AXIS_UNKNOWN_BY_SCOPE
                    and row["axis_reason_code"][name]
                }),
            }
        markets[market] = {
            "blocked_date_count": sum(
                1 for row in rows if row["outcome"] == CSR.OUTCOME_BLOCKED
            ),
            "blocked_reason_codes": dict(sorted(blocked_codes.items())),
            "lookahead_contained_date_count": sum(
                1 for row in rows if row["lookahead_contained"]
            ),
            "unknown_candidate_regime_dates": [
                date for date in dates
                if table[market][date]["candidate_regime"] == UNKNOWN_STATE
            ],
            "candidate_classification_status_counts": _counted(
                row["candidate_classification_status"] for row in rows
            ),
            "axes": axes,
        }
    return {
        "markets": markets,
        "semantics": {
            # Repeating the canonical invariant where the counts live, so a
            # reader of this section alone cannot conflate the three.
            "unknown_is_insufficient_evidence_not_a_neutral_observation": True,
            "unknown_excluded_by_ratification_scope_is_not_a_source_failure": True,
            "not_computable_is_attempted_and_unsupported_not_out_of_scope": True,
            "not_attempted_date_blocked_is_neither": True,
            "statement": (
                "UNKNOWN_EXCLUDED_BY_RATIFICATION_SCOPE means the axis was"
                " deliberately not populated (US BREADTH/LEADERSHIP)."
                " NOT_COMPUTABLE means it was attempted and the source could not"
                " support it. NOT_ATTEMPTED_DATE_BLOCKED means the whole date"
                " failed closed in that market. None of the three is a NEUTRAL"
                " observation and none is interchangeable with another."
            ),
        },
    }


def _counted(values) -> dict:
    counts: dict[str, int] = {}
    for value in values:
        key = "NONE" if value is None else str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def transition_facts(table: dict, dates: list[str]) -> dict:
    """Family 3 — where the existing rule's own output changed between dates."""
    markets = {}
    for market in MARKETS:
        regime_points = [
            (date, table[market][date]["candidate_regime"] or UNKNOWN_STATE)
            for date in dates
        ]
        axes = {}
        for name in AXES:
            points = [
                (
                    date,
                    table[market][date]["axis_direction"][name]
                    if table[market][date]["axis_status"][name] == AXIS_OBSERVED
                    else UNKNOWN_STATE,
                )
                for date in dates
            ]
            axes[name] = sequence_facts(points)
        markets[market] = {
            "candidate_regime": sequence_facts(regime_points),
            # Both kinds of UNKNOWN are genuinely "the regime is not known", but
            # they arise differently, so the split is kept rather than left for a
            # reader to guess from the state sequence alone.
            "candidate_regime_unknown_basis_counts": {
                "DATE_BLOCKED": sum(
                    1 for date in dates
                    if table[market][date]["candidate_regime"] is None
                ),
                "OBSERVED_BUT_UNCLASSIFIED": sum(
                    1 for date in dates
                    if table[market][date]["candidate_regime"] == UNKNOWN_STATE
                ),
            },
            "axis_direction": axes,
        }
    return {
        "markets": markets,
        "cross_market_transitions_computed": False,
        "statement": (
            "A transition is a difference between two adjacent states of the"
            " unmodified candidate rule's own output. Every requested date is in"
            " the sequence, carrying UNKNOWN where it produced no state, so a run"
            " can never bridge a date the replay could not observe. Adjacency is"
            " requested-date order, not calendar adjacency, so each sequence"
            " carries its own calendar-gap facts. A pair touching UNKNOWN is"
            " counted as an evidence-availability change, never as a market state"
            " change. No cross-market transition is computed: no ratified rule"
            " relates a KR state change to a US one."
        ),
    }


def stress_facts(table: dict, dates: list[str], policy_basis: dict) -> dict:
    """Family 4 — how often the existing rule already emitted STRESS."""
    markets = {}
    for market in MARKETS:
        per_date = []
        for date in dates:
            row = table[market][date]
            stress_axes = [
                name for name in AXES
                if row["axis_status"][name] == AXIS_OBSERVED
                and row["axis_direction"][name] == STRESS_STATE
            ]
            regime_stress = row["candidate_regime"] == STRESS_STATE
            if stress_axes or regime_stress:
                per_date.append({
                    "requested_date": date,
                    "stress_axes": stress_axes,
                    "candidate_regime_is_stress": regime_stress,
                })
        markets[market] = {
            "dates_with_any_stress_axis": sum(
                1 for row in per_date if row["stress_axes"]
            ),
            "dates_with_stress_candidate_regime": sum(
                1 for row in per_date if row["candidate_regime_is_stress"]
            ),
            "stress_axis_counts": {
                name: sum(
                    1 for date in dates
                    if table[market][date]["axis_status"][name] == AXIS_OBSERVED
                    and table[market][date]["axis_direction"][name] == STRESS_STATE
                )
                for name in AXES
            },
            "stress_observations": per_date,
        }
    return {
        "markets": markets,
        "detection_source": (
            "regime/paper_regime_reference.py::axis,classify"
            " (applied by the KR and US replay populations, not re-applied here)"
        ),
        "stress_threshold_introduced_by_this_module": False,
        "stress_override_component_status": policy_basis["component_status"]["STRESS_OVERRIDE"],
        "statement": (
            "Every STRESS value counted here was already emitted by the existing"
            " unmodified candidate rule for that date. This module adds no stress"
            " threshold, evaluates none, and draws no conclusion about whether the"
            " existing one is correct, early, or late. STRESS_OVERRIDE remains an"
            " unratified policy component."
        ),
    }


def hysteresis_facts(transitions: dict, policy_basis: dict) -> dict:
    """Family 5 — the raw sequence shape a hysteresis rule would have to govern.

    Deliberately derived from the *same* sequence facts family 3 already
    published, so the two can never disagree, and deliberately stopping at the
    counts: no dwell time, confirmation count, buffer width, or need-for-
    hysteresis finding is produced.
    """
    markets = {}
    for market in MARKETS:
        regime = transitions["markets"][market]["candidate_regime"]
        markets[market] = {
            "candidate_regime": _hysteresis_view(regime),
            "axis_direction": {
                name: _hysteresis_view(transitions["markets"][market]["axis_direction"][name])
                for name in AXES
            },
        }
    return {
        "hysteresis_applied": False,
        "hysteresis_component_status": policy_basis["component_status"]["HYSTERESIS"],
        "hysteresis_parameter_source": None,
        "markets": markets,
        "statement": (
            "No hysteresis, dwell time, confirmation count, or buffer was applied"
            " to any sequence — the HYSTERESIS policy component is unratified, so"
            " there is no parameter to apply. The run, single-observation, and"
            " immediate-reversal counts below describe the raw output of the"
            " existing rule over caller-supplied, not necessarily calendar-"
            " consecutive dates. They are observations, not evidence that"
            " hysteresis is needed and not a proposed value for one."
        ),
    }


def _hysteresis_view(sequence: dict) -> dict:
    return {
        "sequence_length": sequence["sequence_length"],
        "observed_state_count": sequence["observed_state_count"],
        "unknown_state_count": sequence["unknown_state_count"],
        "run_count": sequence["run_count"],
        "runs": copy.deepcopy(sequence["runs"]),
        "single_observation_run_count": sequence["single_observation_run_count"],
        "longest_run_length": sequence["longest_run_length"],
        "raw_state_change_count": sequence["state_change_count"],
        "immediate_reversal_count": sequence["immediate_reversal_count"],
        "immediate_reversals": copy.deepcopy(sequence["immediate_reversals"]),
        "adjacency": copy.deepcopy(sequence["adjacency"]),
    }


# ---------------------------------------------------------------------------
# Report.
# ---------------------------------------------------------------------------


def build_evidence(population: dict, *, root: Path = ROOT) -> dict:
    """Derive the deterministic replay evidence report from one population.

    Pure: the same population always yields a byte-identical report, and the
    caller's population is never mutated — it is re-validated by its own owning
    module, whose validator returns the deep copy this function works from.
    """
    try:
        source = CSR.validate_population(population)
    except CSR.CombinedReplayError as exc:
        raise ReplayEvidenceError(f"SOURCE_POPULATION_INVALID:{exc}") from exc

    policy_basis = load_policy_basis(root)
    dates = list(source["requested_dates"])
    table = observation_table(source)

    coverage = coverage_facts(source, table, dates)
    unknown = unknown_facts(table, dates)
    transitions = transition_facts(table, dates)
    stress = stress_facts(table, dates, policy_basis)
    hysteresis = hysteresis_facts(transitions, policy_basis)

    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "wbs": "P1-COM-05",
        "evidence_class": EVIDENCE_CLASS,
        "markets": list(MARKETS),
        "axes": list(AXES),
        "source_population": {
            "schema_version": source["schema_version"],
            "mode": source["mode"],
            "evidence_class": source["evidence_class"],
            "payload_sha256": source["payload_sha256"],
            "requested_dates": list(dates),
            "market_population_available": {
                market: source["market_population_status"][market]["available"]
                for market in MARKETS
            },
            "source_module": "regime/combined_shadow_historical_replay.py",
            "revalidated_by_its_own_validator": True,
        },
        # Carried verbatim from the source, never re-derived: an episode is a
        # caller label here exactly as it is there.
        "episodes": copy.deepcopy(source["episodes"]),
        "episode_selection": {
            "selection_source": source["episode_selection"]["selection_source"],
            "episode_selected_by_this_module": False,
            "label_influences_any_fact": False,
            "statement": (
                "Episode labels are carried from the source population unchanged."
                " No fact in this report is computed from, filtered by, or"
                " grouped by a label, and no episode is selected here."
            ),
        },
        "policy_basis": policy_basis,
        "per_date_observations": {
            market: {date: copy.deepcopy(table[market][date]) for date in dates}
            for market in MARKETS
        },
        "coverage_facts": coverage,
        "unknown_facts": unknown,
        "transition_facts": transitions,
        "stress_detection_facts": stress,
        "hysteresis_facts": hysteresis,
        "policy_conclusion": {
            # The load-bearing guarantee of this slice, enforced field by field
            # in validate_evidence and by a recursive forbidden-key scan.
            "conclusion": None,
            "conclusion_status": CONCLUSION_STATUS,
            "threshold_proposed": False,
            "threshold_tuned": False,
            "stress_threshold_proposed": False,
            "hysteresis_parameter_proposed": False,
            "minimum_coverage_proposed": False,
            "replay_acceptance_asserted": False,
            "candidate_policy_ratified": False,
            "market_regime_asserted": False,
            "statement": (
                "This report states what the replay showed and nothing else. It"
                " does not judge whether coverage is sufficient, whether a"
                " threshold is correct, whether stress fired early or late,"
                " whether hysteresis is required, or whether the replay is"
                " acceptable. Every one of those is a separate CIO ratification."
            ),
        },
        "pit_and_audit_separation": {
            # Structural facts about *how* this report was produced. Each is
            # enforced by test/test_deterministic_replay_evidence.py rather than
            # merely asserted here.
            "each_date_summarized_from_its_own_record": True,
            "no_date_observation_altered_by_another_date": True,
            "future_dates_used_in_any_date_evaluation": False,
            "source_population_mutated_by_this_module": False,
            "market_observations_recomputed_by_this_module": False,
            "candidate_rule_modified_by_this_module": False,
            "threshold_introduced_by_this_module": False,
            "hysteresis_applied_by_this_module": False,
            "episode_selected_by_this_module": False,
            "sequence_adjacency_basis": ADJACENCY_BASIS,
            "statement": (
                "Every per-date fact is read from that one date's own already"
                " replayed record; no observation is created, altered, promoted,"
                " or graded here. Transition, run, and reversal facts are"
                " cross-date descriptions of an already-fixed replay set — they"
                " are historical audit output and never feed back into any date's"
                " evaluation or into a live operational decision."
            ),
        },
        "authority": {
            "replay_evidence_summary_authorized": True,
            "policy_conclusion_authorized": False,
            "threshold_ratification_authorized": False,
            "hysteresis_authorized": False,
            "stress_override_ratification_authorized": False,
            "episode_selection_authorized": False,
            "cross_market_regime_authorized": False,
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
        },
    }
    report["payload_sha256"] = payload_sha256(report)
    return report


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------


def _forbidden_keys(value: object) -> list[str]:
    """Every key anywhere in ``value`` that would read as a recommendation."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            if name in FORBIDDEN_KEY_NAMES or name.startswith(FORBIDDEN_KEY_PREFIXES):
                found.append(name)
            found.extend(_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_forbidden_keys(item))
    return found


def _validate_no_conclusion(value: dict) -> None:
    conclusion = value.get("policy_conclusion")
    if not isinstance(conclusion, dict):
        fail("POLICY_CONCLUSION_MISSING")
    if conclusion.get("conclusion") is not None:
        fail("POLICY_CONCLUSION_MUST_STAY_NULL")
    if conclusion.get("conclusion_status") != CONCLUSION_STATUS:
        fail("POLICY_CONCLUSION_STATUS_INVALID")
    for key, claimed in conclusion.items():
        if key in ("conclusion", "conclusion_status", "statement"):
            continue
        if claimed is not False:
            fail("POLICY_CONCLUSION_MUST_STAY_NULL", key)
    if value.get("hysteresis_facts", {}).get("hysteresis_applied") is not False:
        fail("HYSTERESIS_MUST_NOT_BE_APPLIED")
    if value.get("hysteresis_facts", {}).get("hysteresis_parameter_source") is not None:
        fail("HYSTERESIS_MUST_NOT_BE_APPLIED", "parameter_source")
    if value.get("stress_detection_facts", {}).get(
        "stress_threshold_introduced_by_this_module"
    ) is not False:
        fail("STRESS_THRESHOLD_MUST_NOT_BE_INTRODUCED")
    smuggled = sorted(set(_forbidden_keys(value)))
    if smuggled:
        fail("POLICY_CONCLUSION_SMUGGLED", ",".join(smuggled))


def _validate_counts(value: dict) -> None:
    """Per-axis status buckets must be total over the requested dates.

    A bucket set that does not add up would mean a date was counted twice or
    dropped — either of which would silently misstate coverage or UNKNOWN.
    """
    coverage = value.get("coverage_facts", {})
    total = coverage.get("requested_date_count")
    if not isinstance(total, int):
        fail("COVERAGE_FACTS_INVALID", "requested_date_count")
    for market in MARKETS:
        market_facts = coverage.get("markets", {}).get(market)
        if not isinstance(market_facts, dict):
            fail("COVERAGE_FACTS_INVALID", market)
        if sum(market_facts.get("outcome_counts", {}).values()) != total:
            fail("COVERAGE_COUNTS_NOT_TOTAL", f"{market}.outcome_counts")
        for name in AXES:
            buckets = market_facts.get("axis_status_counts", {}).get(name, {})
            if sorted(buckets) != sorted(AXIS_STATUSES):
                fail("AXIS_STATUS_VOCABULARY_INVALID", f"{market}.{name}")
            if sum(buckets.values()) != total:
                fail("COVERAGE_COUNTS_NOT_TOTAL", f"{market}.{name}")


def _validate_sequences(value: dict) -> None:
    markets = value.get("transition_facts", {}).get("markets", {})
    if sorted(markets) != sorted(MARKETS):
        fail("TRANSITION_FACTS_INVALID", "markets")
    total = value.get("coverage_facts", {}).get("requested_date_count")
    for market in MARKETS:
        sequences = [markets[market]["candidate_regime"]] + [
            markets[market]["axis_direction"][name] for name in AXES
        ]
        for sequence in sequences:
            # Totality: a sequence shorter than the requested dates would mean a
            # date without evidence was dropped, letting a run bridge it.
            if sequence["sequence_length"] != total:
                fail("SEQUENCE_NOT_TOTAL_OVER_REQUESTED_DATES", market)
            if (
                sequence["observed_state_count"] + sequence["unknown_state_count"]
                != sequence["sequence_length"]
            ):
                fail("SEQUENCE_STATE_COUNTS_INCONSISTENT", market)
            counted = (
                sequence["state_change_count"]
                + sequence["evidence_availability_change_count"]
            )
            if counted != sequence["transition_count"]:
                fail("TRANSITION_COUNTS_INCONSISTENT", market)
            # A pair touching UNKNOWN must never be counted as a market state
            # change — that is the UNKNOWN/observed-state boundary itself.
            for row in sequence["transitions"]:
                touches_unknown = UNKNOWN_STATE in (row["from_state"], row["to_state"])
                expected = (
                    TRANSITION_EVIDENCE_CHANGE if touches_unknown
                    else TRANSITION_STATE_CHANGE
                )
                if row["kind"] != expected:
                    fail("UNKNOWN_MUST_NOT_COUNT_AS_STATE_CHANGE", market)
            if sequence["adjacency"]["basis"] != ADJACENCY_BASIS:
                fail("ADJACENCY_BASIS_INVALID", market)


def validate_evidence(
    value: dict, *, population: dict | None = None, root: Path = ROOT,
) -> dict:
    """Integrity/shape check, plus full re-derivation when the source is supplied.

    Re-derivation is offered rather than required because the source population
    is external SHADOW output that a verifier may not still hold. When it *is*
    supplied, the check is exact: this report is a pure function of that
    population, so any drift is a defect.
    """
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        fail("EVIDENCE_SCHEMA_INVALID")
    unsigned = copy.deepcopy(value)
    claimed = unsigned.pop("payload_sha256", None)
    if (
        not isinstance(claimed, str)
        or SHA256.fullmatch(claimed) is None
        or payload_sha256(unsigned) != claimed
    ):
        fail("EVIDENCE_SHA_INVALID")
    if value.get("mode") != MODE or value.get("evidence_class") != EVIDENCE_CLASS:
        fail("EVIDENCE_MODE_INVALID")
    if value.get("markets") != list(MARKETS) or value.get("axes") != list(AXES):
        fail("EVIDENCE_SCOPE_INVALID")
    if value.get("episode_selection", {}).get("episode_selected_by_this_module") is not False:
        fail("EPISODE_SELECTION_INVALID")
    if value.get("episode_selection", {}).get("label_influences_any_fact") is not False:
        fail("EPISODE_SELECTION_INVALID", "label_influences_any_fact")
    _validate_no_conclusion(value)
    _validate_counts(value)
    _validate_sequences(value)
    for key, allowed in value.get("authority", {}).items():
        if key == "replay_evidence_summary_authorized":
            if allowed is not True:
                fail("EVIDENCE_AUTHORITY_INVALID", key)
        elif allowed is not False:
            fail("EVIDENCE_AUTHORITY_INVALID", key)
    if population is not None and value != build_evidence(population, root=root):
        fail("EVIDENCE_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# Output boundary.
# ---------------------------------------------------------------------------


def _forbid_tracked_output(root: Path, path: Path) -> None:
    """Fail closed if ``path`` resolves inside this repository checkout.

    This report summarizes SHADOW historical-backfill evidence, so it must never
    land in any tracked location — the NATURAL ``data/observations/`` and
    ``evidence/`` paths included. The guard is a blanket "not inside the checkout
    at all", not a denylist a new tracked directory could slip past.
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


def write_evidence(report: dict, out_path: Path, *, root: Path = ROOT) -> Path:
    _forbid_tracked_output(root, out_path)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(Path(out_path), text)
    return Path(out_path)


def _default_temp_out() -> Path:
    fd, name = tempfile.mkstemp(prefix="deterministic_replay_evidence.", suffix=".json")
    os.close(fd)
    return Path(name)


def load_population_file(path: Path) -> dict:
    """Read an already-written combined replay population the caller names."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayEvidenceError(f"SOURCE_POPULATION_UNREADABLE:{path}") from exc
    if not isinstance(value, dict):
        fail("SOURCE_POPULATION_UNREADABLE", str(path))
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from", dest="source", type=Path, default=None,
        help="Path to an already-written combined replay population JSON."
             " Mutually exclusive with --date/--episode/--episode-file.",
    )
    parser.add_argument(
        "--date", action="append", default=[], dest="dates",
        help="Historical date, YYYY-MM-DD, replayed in both markets. Repeatable."
             " No date is ever selected automatically.",
    )
    parser.add_argument(
        "--episode", action="append", default=[], dest="episodes",
        help="Caller-labelled episode as NAME=YYYY-MM-DD[,YYYY-MM-DD...]. Repeatable."
             " The label is descriptive only and never selects a date or a fact.",
    )
    parser.add_argument(
        "--episode-file", type=Path, default=None,
        help='Caller-supplied JSON: {"episodes": [{"name": ..., "dates": [...]}]}.',
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="External output path (must be outside this checkout)."
             " Defaults to a private system-temp file.",
    )
    parser.add_argument(
        "--verify", type=Path,
        help="Verify an evidence report's hash, shape, and no-conclusion guarantee.",
    )
    parser.add_argument(
        "--verify-against", type=Path, default=None,
        help="With --verify: also re-derive the report from this source population.",
    )
    args = parser.parse_args()

    if args.verify:
        value = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        population = (
            load_population_file(args.verify_against)
            if args.verify_against is not None
            else None
        )
        validate_evidence(value, population=population)
        print(f"PASS_DETERMINISTIC_REPLAY_EVIDENCE_VERIFIED:{value['payload_sha256']}")
        return 0

    if args.source is not None:
        if args.dates or args.episodes or args.episode_file is not None:
            fail("SOURCE_AND_DATES_ARE_MUTUALLY_EXCLUSIVE")
        population = load_population_file(args.source)
    else:
        episodes = [CSR.parse_episode_argument(text) for text in args.episodes]
        episode_sources = []
        if args.episode_file is not None:
            from_file, source = CSR.load_episode_file(args.episode_file)
            episodes.extend(from_file)
            episode_sources.append(source)
        if not args.dates and not episodes:
            fail("NO_DATES_REQUESTED")
        population = CSR.build_population(
            CSR._credentials_from_env(),
            dates=args.dates,
            episodes=episodes,
            episode_sources=episode_sources,
        )

    report = build_evidence(population)
    out_path = args.out if args.out is not None else _default_temp_out()
    write_evidence(report, out_path)
    print(json.dumps(
        {
            "out": str(out_path),
            "payload_sha256": report["payload_sha256"],
            "source_population_sha256": report["source_population"]["payload_sha256"],
            "requested_dates": report["coverage_facts"]["requested_date_count"],
            "combined_status_counts": report["coverage_facts"]["combined_status_counts"],
            "policy_conclusion_status": report["policy_conclusion"]["conclusion_status"],
            "hysteresis_applied": report["hysteresis_facts"]["hysteresis_applied"],
        },
        ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
