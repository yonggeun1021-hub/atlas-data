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
  that hysteresis is needed, nor a proposed dwell time.  ``validate_evidence``
  rejects any report carrying a conclusion, verdict, recommendation, or proposed
  parameter — including one added under a new key name, and including one
  produced by *deleting* an explicit refusal flag.

Two different policies bear on that guarantee, and ``policy_basis`` keeps them
apart so neither is ever reported as the other's absence:

1. The repository already holds a **ratified, replay-only common aggregation
   policy** — ``common_v1_alignment`` in
   ``config/regime_source_owner_registry_v2.json``, ``policy_status =
   RATIFIED_PAPER_BASELINE_V1``, implemented by
   ``regime/decision_authority.py`` — and it *does* define explicit hysteresis
   and stress entry/exit behavior.  This report quotes it verbatim through that
   owning module (which binds it hash-for-hash to the registry, the legacy
   fail-closed contract, and the merged PAPER baseline packet) precisely so this
   file can never be read as claiming no such policy exists.  It is not applied
   here, and this module neither ratifies nor changes it: it consumes
   already-signed axis directions, and the registry itself records
   market-specific normalization, freshness, and replay as **not inherited**
   from it.  If that scope field ever flips, this module fails closed.
2. The **market-specific candidate policy** this SHADOW replay actually
   exercised is still ``DRAFT_NOT_RATIFIED``, with ``HYSTERESIS`` and
   ``STRESS_OVERRIDE`` recorded ``BLOCKED`` in
   ``evidence/regime/policy_candidates/candidate_inventory.json``.  That is why
   ``hysteresis_applied`` is ``false``: no buffer, confirmation count, or dwell
   rule exists at this layer to apply.  The basis is read from disk rather than
   asserted, and a component that ever becomes ``SUPPORTED`` fails this module
   closed so a human re-decides the framing.

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

``validate_evidence`` is exact rather than best-effort.  A re-hashed report is a
valid signature over whatever it contains, so checking only the fields that
happen to be present would accept one that dropped its observations, a refusal
flag, an authority boundary, its own source pin, or its point-in-time
declaration.  Instead: every requested date must carry an observation, every
fact family must *re-derive* from those observations, and the
``source_population``, ``policy_conclusion``, ``authority``, ``policy_basis``,
and ``pit_and_audit_separation`` blocks must match their declared shapes key for
key — the source pin and the PIT declaration included on the standalone
``--verify`` path, where no population is at hand to re-derive against.  A
report that re-signed itself claiming future dates *were* used in a date's
evaluation is refused there, as is one whose policy pin is not a SHA-256.

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
from regime import decision_authority as DA  # noqa: E402
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

# --- The two distinct policies that bear on this report -------------------
#
# They must never be conflated, and neither may be described as the other's
# absence. (1) The *market-specific* candidate policy this SHADOW replay
# actually exercised is still a draft, with HYSTERESIS and STRESS_OVERRIDE
# BLOCKED. (2) The repository separately holds an already-ratified, replay-only
# *common* aggregation policy (``common_v1_alignment`` in the v2 source-owner
# registry, implemented by ``regime/decision_authority.py``) that does define
# explicit hysteresis and stress behavior. Both are read from disk and quoted.
POLICY_INVENTORY_PATH = "evidence/regime/policy_candidates/candidate_inventory.json"
POLICY_CONTRACT_VERSION = "regime_policy_candidate_evidence/v1"
POLICY_STATUS = "DRAFT_NOT_RATIFIED"
# The two components whose *unratified market-specific* status this report's
# framing depends on. If either is ever ratified for market-specific use,
# "no hysteresis applied" and "no stress threshold proposed" stop being safe
# descriptions and a human must re-decide.
REQUIRED_UNRATIFIED_COMPONENTS = ("HYSTERESIS", "STRESS_OVERRIDE")
BLOCKED_COMPONENT_STATUS = "BLOCKED"
CANDIDATE_POLICY_SCOPE = "MARKET_SPECIFIC_NORMALIZATION_FRESHNESS_AND_REPLAY"

REGISTRY_PATH = "config/regime_source_owner_registry_v2.json"
COMMON_V1_POLICY_STATUS = "RATIFIED_PAPER_BASELINE_V1"
COMMON_V1_SCOPE = "COMMON_SIGNED_AXIS_AGGREGATION_REPLAY_ONLY_RUNTIME_NOT_WIRED"
COMMON_V1_IMPLEMENTED_BY = "regime/decision_authority.py::load_common_v1_policy"
# Why an existing, ratified hysteresis rule is nonetheless not applied to this
# replay. Taken from the registry's own scope field, not invented here: the
# common policy consumes already-signed axis directions, and the registry
# records that market-specific normalization, freshness, and replay are *not*
# inherited from it.
COMMON_V1_NOT_APPLIED_REASON = (
    "COMMON_V1_CONSUMES_ALREADY_SIGNED_AXIS_DIRECTIONS_AND_THE_REGISTRY_RECORDS"
    "_MARKET_SPECIFIC_NORMALIZATION_FRESHNESS_AND_REPLAY_AS_NOT_INHERITED"
)

# The exact published shape of each policy layer. Required key for key by
# ``validate_evidence``: deleting ``common_v1_replay_policy``, or a field inside
# it, is precisely how this report would go back to describing an existing
# ratified policy as absent.
CANDIDATE_POLICY_KEYS = (
    "path", "sha256", "scope", "candidate_id", "candidate_status", "policy_status",
    "component_status", "statement",
)
COMMON_V1_POLICY_KEYS = (
    "path", "sha256", "scope", "policy_status", "implemented_by",
    "present_in_repository", "hysteresis", "stress_classification",
    "market_kill_stress_condition_status",
    "market_specific_normalization_freshness_and_replay_inherited",
    "pit_replay_acceptance", "binding", "applied_to_this_replay",
    "not_applied_reason", "statement",
)
# The quoted registry fields this report actually reads. A superset is allowed —
# the registry owns that block — but these may not go missing.
COMMON_V1_HYSTERESIS_KEYS = (
    "ordinary_transition_finalized_packets", "stress_entry", "stress_exit",
)

# The exact shape of the pinned source-population block. Required key for key by
# ``validate_evidence`` even when no population is supplied: this block is the
# report's whole claim about *what it summarized*, so a standalone verifier that
# only checked it when it already held the source would accept a report whose
# source digest, mode, or evidence class had been deleted and re-signed.
SOURCE_MODULE = "regime/combined_shadow_historical_replay.py"
SOURCE_POPULATION_KEYS = (
    "schema_version", "mode", "evidence_class", "payload_sha256",
    "requested_dates", "market_population_available", "source_module",
    "revalidated_by_its_own_validator",
)

CONCLUSION_STATUS = "WITHHELD_NO_POLICY_OR_THRESHOLD_AUTHORITY"
# Every claim this report explicitly refuses to make. Declared once and required
# key for key by ``validate_evidence``: a payload that *removes* one of these
# flags must not pass merely because the flag it removed is no longer there to
# be checked.
CONCLUSION_FALSE_FLAGS = (
    "threshold_proposed",
    "threshold_tuned",
    "stress_threshold_proposed",
    "hysteresis_parameter_proposed",
    "minimum_coverage_proposed",
    "replay_acceptance_asserted",
    "candidate_policy_ratified",
    "common_v1_replay_policy_ratified_or_changed",
    "market_regime_asserted",
)
CONCLUSION_KEYS = ("conclusion", "conclusion_status", "statement") + CONCLUSION_FALSE_FLAGS

# The exact point-in-time / historical-audit separation block, declared once so
# ``build_evidence`` and ``validate_evidence`` cannot drift apart, and required
# key for key by ``_validate_pit_and_audit_separation``.
#
# This is the report's own declaration that no date's fact was produced from a
# later date. A re-signed report is a valid signature over whatever it contains,
# so a verifier that never read this block would accept one whose
# ``future_dates_used_in_any_date_evaluation`` had been flipped to ``true`` —
# a report claiming, under its own valid signature, to have breached the
# non-negotiable PIT boundary of ``docs/ATLAS_SESSION_BOOTSTRAP.md`` — or one
# that had simply deleted the declaration and kept every other guarantee.
PIT_SEPARATION_TRUE_KEYS = (
    "each_date_summarized_from_its_own_record",
    "no_date_observation_altered_by_another_date",
)
PIT_SEPARATION_FALSE_KEYS = (
    "future_dates_used_in_any_date_evaluation",
    "source_population_mutated_by_this_module",
    "market_observations_recomputed_by_this_module",
    "candidate_rule_modified_by_this_module",
    "threshold_introduced_by_this_module",
    "hysteresis_applied_by_this_module",
    "episode_selected_by_this_module",
)
PIT_SEPARATION_STATEMENT = (
    "Every per-date fact is read from that one date's own already"
    " replayed record; no observation is created, altered, promoted,"
    " or graded here. Transition, run, and reversal facts are"
    " cross-date descriptions of an already-fixed replay set — they"
    " are historical audit output and never feed back into any date's"
    " evaluation or into a live operational decision."
)
PIT_SEPARATION_KEYS = (
    PIT_SEPARATION_TRUE_KEYS
    + PIT_SEPARATION_FALSE_KEYS
    + ("sequence_adjacency_basis", "statement")
)

# The exact authority boundary of this report, for the same reason.
AUTHORITY_GRANTED_KEY = "replay_evidence_summary_authorized"
AUTHORITY = {
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
}

# The exact shape of one per-date, per-market observation cell, so a report's
# published observations can be re-derived from — and checked against — the
# fact families that claim to summarize them.
OBSERVATION_KEYS = (
    "outcome",
    "blocked_reason_code",
    "lookahead_contained",
    "candidate_regime",
    "candidate_classification_status",
    "axis_status",
    "axis_direction",
    "axis_reason_code",
)

# The exact shape ``sequence_facts`` publishes, for the same reason: a sequence
# missing a field would let a totality or transition check pass by having
# nothing to compare.
SEQUENCE_KEYS = (
    "sequence_length",
    "sequence_dates",
    "observed_state_count",
    "unknown_state_count",
    "state_sequence",
    "distinct_states",
    "transition_count",
    "state_change_count",
    "evidence_availability_change_count",
    "transitions",
    "runs",
    "run_count",
    "single_observation_run_count",
    "longest_run_length",
    "immediate_reversal_count",
    "immediate_reversals",
    "adjacency",
)

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
# Policy basis, read from disk rather than asserted.
#
# Two different policies bear on this report and are reported side by side so
# neither can be mistaken for the other's absence.
# ---------------------------------------------------------------------------


def load_policy_basis(root: Path = ROOT) -> dict:
    """Read both policies this report's framing depends on, and fail closed.

    This module describes a replay in which *no* hysteresis was applied and *no*
    stress threshold was proposed. Two separate facts have to hold for that to
    be an honest description, and both are read from disk:

    * the market-specific candidate policy the replay actually exercised is
      still ``DRAFT_NOT_RATIFIED`` with HYSTERESIS and STRESS_OVERRIDE
      ``BLOCKED`` — if either becomes supported, this module fails closed so a
      human re-decides the framing;
    * the repository's already-ratified *common* replay-only aggregation policy
      is quoted verbatim rather than described as absent, together with the
      reason it is not applied to this market-specific replay.
    """
    candidate = _load_candidate_policy_basis(root)
    common = _load_common_v1_basis(root)
    return {
        "market_specific_candidate_policy": candidate,
        "common_v1_replay_policy": common,
        "statement": (
            "Two different policies bear on this report and are not"
            " interchangeable. The repository does hold a ratified"
            " replay-only common aggregation policy with explicit hysteresis and"
            " stress behavior; it is quoted here from"
            f" {REGISTRY_PATH} and is not absent. It is also not applied to this"
            " replay, because it consumes already-signed axis directions and the"
            " registry itself records market-specific normalization, freshness,"
            " and replay as not inherited from it. The market-specific candidate"
            " policy this SHADOW replay did exercise is still DRAFT_NOT_RATIFIED"
            " with HYSTERESIS and STRESS_OVERRIDE BLOCKED. This report therefore"
            " applies no hysteresis and proposes no stress threshold; it only"
            " counts what the existing unmodified candidate rule already emitted."
        ),
    }


def _load_candidate_policy_basis(root: Path) -> dict:
    """The unratified market-specific candidate policy the replay exercised."""
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
            "the market-specific candidate policy is no longer"
            " DRAFT_NOT_RATIFIED; this module must not keep describing its"
            " hysteresis and stress override components as unratified",
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
        "scope": CANDIDATE_POLICY_SCOPE,
        "candidate_id": inventory.get("candidate_id"),
        "candidate_status": inventory.get("candidate_status"),
        "policy_status": inventory.get("policy_status"),
        "component_status": component_status,
        "statement": (
            "This is the policy layer the replayed market-specific"
            " normalization would need. HYSTERESIS and STRESS_OVERRIDE are both"
            " BLOCKED here under a DRAFT_NOT_RATIFIED policy, so no hysteresis"
            " parameter and no stress threshold exists for this module to apply."
        ),
    }


def _load_common_v1_basis(root: Path) -> dict:
    """Quote the already-ratified, replay-only common aggregation policy.

    Loaded through ``regime/decision_authority.py``, which owns this policy and
    already binds it hash-for-hash to the v2 source-owner registry, the legacy
    fail-closed contract, and the merged PAPER baseline packet. Nothing is
    re-authored here: proving the registry equals that module's pinned
    ``RATIFIED_COMMON_V1`` block is what makes quoting the block a quote of the
    registry.
    """
    registry_path = Path(root) / REGISTRY_PATH
    try:
        policy = DA.load_common_v1_policy(
            registry_path=registry_path,
            paper_policy_path=Path(root) / "config" / "paper_regime_reference_policy_v1.json",
            contract_path=Path(root) / "config" / "regime_decision_authority_contract.json",
        )
    except DA.DecisionAuthorityError as exc:
        raise ReplayEvidenceError(f"COMMON_V1_POLICY_UNREADABLE:{REGISTRY_PATH}:{exc}") from exc
    if policy["policy_status"] != COMMON_V1_POLICY_STATUS:
        fail("COMMON_V1_POLICY_STATUS_CHANGED", str(policy["policy_status"]))
    # The reason this ratified rule is not applied below is the registry's own
    # scope field. If that ever flips, "not applied here" stops being a scope
    # fact and a human must re-decide instead of this file continuing.
    if policy["market_specific_normalization_inherited"] is not False:
        fail(
            "COMMON_V1_SCOPE_CHANGED",
            "market-specific normalization, freshness, and replay are now"
            " inherited from the ratified common policy; whether it applies to"
            " this replay is a human decision, not a default",
        )
    alignment = DA.RATIFIED_COMMON_V1
    return {
        "path": REGISTRY_PATH,
        "sha256": file_sha256(registry_path),
        "scope": COMMON_V1_SCOPE,
        "policy_status": policy["policy_status"],
        "implemented_by": COMMON_V1_IMPLEMENTED_BY,
        "present_in_repository": True,
        "hysteresis": copy.deepcopy(alignment["hysteresis"]),
        "stress_classification": alignment["classification"]["STRESS"],
        "market_kill_stress_condition_status": policy["market_kill_stress_condition_status"],
        "market_specific_normalization_freshness_and_replay_inherited": False,
        "pit_replay_acceptance": policy["pit_replay_acceptance"],
        "binding": copy.deepcopy(policy["binding"]),
        "applied_to_this_replay": False,
        "not_applied_reason": COMMON_V1_NOT_APPLIED_REASON,
        "statement": (
            "The repository already holds this ratified, replay-only common"
            " aggregation policy, and it does define explicit hysteresis and"
            " stress behavior. It is quoted verbatim so this report cannot be"
            " read as claiming no such policy exists. It is not applied to this"
            " replay and this module neither ratifies, changes, nor proposes a"
            " change to it: it consumes already-signed axis directions, and the"
            " registry records market-specific normalization, freshness, and"
            " replay as not inherited from it. Its own PIT replay acceptance is"
            " carried above exactly as its owning module reports it."
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


def source_combined_counts(population: dict) -> dict:
    """The source population's own combined statuses, recounted from its records.

    Recomputed rather than trusted: a summary that disagrees with the records it
    claims to summarize must fail closed here, not be copied into this report.
    """
    counts = {status: 0 for status in CSR.COMBINED_STATUSES}
    for record in population["records"]:
        counts[record["combined_status"]] += 1
    if counts != population["combined_summary"]["combined_status_counts"]:
        fail("SOURCE_SUMMARY_INCONSISTENT", "combined_status_counts")
    return counts


def coverage_facts(
    table: dict, dates: list[str], combined_counts: dict, population_available: dict,
) -> dict:
    """Family 1 — how much of the requested replay actually produced evidence.

    A pure function of the per-date observation table plus the two source-level
    facts it cannot derive from that table, so ``validate_evidence`` can
    reproduce it exactly from the report's own published observations.
    """
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
            "population_available": population_available[market],
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
        "combined_status_counts": dict(combined_counts),
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
    candidate = policy_basis["market_specific_candidate_policy"]
    common = policy_basis["common_v1_replay_policy"]
    return {
        "markets": markets,
        "detection_source": (
            "regime/paper_regime_reference.py::axis,classify"
            " (applied by the KR and US replay populations, not re-applied here)"
        ),
        "stress_threshold_introduced_by_this_module": False,
        # The two policy layers are reported apart so an unratified
        # market-specific component is never read as "no stress policy exists".
        "stress_policy_status": {
            "market_specific_candidate_component": candidate["component_status"]["STRESS_OVERRIDE"],
            "common_v1_replay_policy": common["policy_status"],
        },
        "common_v1_replay_stress_behavior": {
            "classification": common["stress_classification"],
            "stress_entry": common["hysteresis"]["stress_entry"],
            "stress_exit": common["hysteresis"]["stress_exit"],
            "market_kill_stress_condition_status": common[
                "market_kill_stress_condition_status"
            ],
            "applied_to_this_replay": False,
        },
        "statement": (
            "Every STRESS value counted here was already emitted by the existing"
            " unmodified candidate rule for that date. This module adds no stress"
            " threshold, evaluates none, and draws no conclusion about whether the"
            " existing one is correct, early, or late. The repository's ratified"
            " replay-only common policy does define stress entry and exit"
            " behavior; it is quoted above rather than described as absent, and"
            " it was not applied to this market-specific replay. The"
            " market-specific STRESS_OVERRIDE component remains unratified."
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
    candidate = policy_basis["market_specific_candidate_policy"]
    common = policy_basis["common_v1_replay_policy"]
    return {
        "hysteresis_applied": False,
        "hysteresis_parameter_source": None,
        # The two policy layers are reported apart so an unratified
        # market-specific component is never read as "no hysteresis rule exists".
        "hysteresis_policy_status": {
            "market_specific_candidate_component": candidate["component_status"]["HYSTERESIS"],
            "common_v1_replay_policy": common["policy_status"],
        },
        "common_v1_replay_hysteresis": {
            **copy.deepcopy(common["hysteresis"]),
            "applied_to_this_replay": False,
            "not_applied_reason": common["not_applied_reason"],
        },
        "markets": markets,
        "statement": (
            "No hysteresis, dwell time, confirmation count, or buffer was applied"
            " to any sequence below. The repository's ratified replay-only common"
            " aggregation policy does carry a hysteresis rule; it is quoted above"
            " rather than described as absent, and it is not applied here because"
            " it consumes already-signed axis directions and market-specific"
            " normalization, freshness, and replay are not inherited from it. The"
            " market-specific HYSTERESIS component this replay would need is"
            " unratified, so there is no parameter for this module to apply. The"
            " run, single-observation, and immediate-reversal counts below"
            " describe the raw output of the existing rule over caller-supplied,"
            " not necessarily calendar-consecutive dates. They are observations,"
            " not evidence that hysteresis is needed and not a proposed value for"
            " one."
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


def _pit_separation_block() -> dict:
    """The report's point-in-time / historical-audit declaration.

    Emitted here and re-required by ``_validate_pit_and_audit_separation`` from
    the same constants, so a field can never be published without being checked
    or checked without being published.
    """
    return {
        **{key: True for key in PIT_SEPARATION_TRUE_KEYS},
        **{key: False for key in PIT_SEPARATION_FALSE_KEYS},
        "sequence_adjacency_basis": ADJACENCY_BASIS,
        "statement": PIT_SEPARATION_STATEMENT,
    }


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
    available = {
        market: source["market_population_status"][market]["available"]
        for market in MARKETS
    }

    coverage = coverage_facts(table, dates, source_combined_counts(source), available)
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
            "market_population_available": dict(available),
            "source_module": SOURCE_MODULE,
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
        # The load-bearing guarantee of this slice, enforced key for key in
        # validate_evidence and by a recursive forbidden-key scan.
        "policy_conclusion": {
            "conclusion": None,
            "conclusion_status": CONCLUSION_STATUS,
            **{flag: False for flag in CONCLUSION_FALSE_FLAGS},
            "statement": (
                "This report states what the replay showed and nothing else. It"
                " does not judge whether coverage is sufficient, whether a"
                " threshold is correct, whether stress fired early or late,"
                " whether hysteresis is required, or whether the replay is"
                " acceptable. It neither ratifies nor proposes a change to the"
                " ratified common replay policy it quotes. Every one of those is"
                " a separate CIO ratification."
            ),
        },
        # Structural facts about *how* this report was produced. Each is enforced
        # by test/test_deterministic_replay_evidence.py rather than merely
        # asserted here, and re-required key for key by
        # ``_validate_pit_and_audit_separation``.
        "pit_and_audit_separation": _pit_separation_block(),
        "authority": dict(AUTHORITY),
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


def _validate_policy_pin(digest: object, layer: str) -> None:
    """A policy layer's pinned digest must at least be a SHA-256.

    A digest that is not one identifies no file that could ever exist, so a
    report carrying it pins nothing while appearing to.
    """
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        fail("POLICY_BASIS_SHA_INVALID", layer)


def _validate_policy_basis(value: dict) -> dict:
    """Both policy layers must be stated, and neither described as the other.

    Checked without re-reading disk, because a verifier may hold this report
    without the checkout that produced it. The exact key set matters as much as
    the values: dropping ``common_v1_replay_policy`` is precisely how this
    report would go back to describing a ratified policy as absent.

    Both layers pin the file they quote by ``sha256``, and both pins are checked
    for SHA-256 syntax. Requiring only that the key be *present* left a pin that
    could be re-signed to any text at all — ``"not-a-sha256"`` included — so a
    report could name a policy digest that could never identify a file. Syntax is
    the whole of what a detached verifier can prove: that the digest is the
    quoted file's is re-established by ``load_policy_basis`` re-reading disk on
    the deriving side, and remains a separate, checkout-bound guarantee here.
    """
    basis = value.get("policy_basis")
    if not isinstance(basis, dict) or sorted(basis) != [
        "common_v1_replay_policy", "market_specific_candidate_policy", "statement",
    ]:
        fail("POLICY_BASIS_SCHEMA_INVALID")

    candidate = basis["market_specific_candidate_policy"]
    if not isinstance(candidate, dict) or sorted(candidate) != sorted(CANDIDATE_POLICY_KEYS):
        fail("POLICY_BASIS_SCHEMA_INVALID", "market_specific_candidate_policy")
    if candidate["path"] != POLICY_INVENTORY_PATH:
        fail("POLICY_BASIS_SCHEMA_INVALID", "candidate path")
    _validate_policy_pin(candidate["sha256"], "market_specific_candidate_policy")
    if candidate["policy_status"] != POLICY_STATUS:
        fail("POLICY_STATUS_CHANGED", str(candidate["policy_status"]))
    if candidate["scope"] != CANDIDATE_POLICY_SCOPE:
        fail("POLICY_BASIS_SCHEMA_INVALID", "candidate scope")
    if candidate["component_status"] != {
        component: BLOCKED_COMPONENT_STATUS
        for component in REQUIRED_UNRATIFIED_COMPONENTS
    }:
        fail("POLICY_COMPONENT_STATUS_CHANGED", "market_specific_candidate_policy")

    common = basis["common_v1_replay_policy"]
    if not isinstance(common, dict) or sorted(common) != sorted(COMMON_V1_POLICY_KEYS):
        fail("POLICY_BASIS_SCHEMA_INVALID", "common_v1_replay_policy")
    if common["path"] != REGISTRY_PATH:
        fail("POLICY_BASIS_SCHEMA_INVALID", "common path")
    _validate_policy_pin(common["sha256"], "common_v1_replay_policy")
    # The correction this section exists for: the ratified common replay policy
    # must be reported as present, with its own hysteresis and stress behavior,
    # and explicitly not applied — never as missing.
    hysteresis = common["hysteresis"]
    if (
        common["policy_status"] != COMMON_V1_POLICY_STATUS
        or common["present_in_repository"] is not True
        or not isinstance(hysteresis, dict)
        or not all(hysteresis.get(key) for key in COMMON_V1_HYSTERESIS_KEYS)
        or not common["stress_classification"]
    ):
        fail("COMMON_V1_POLICY_MUST_NOT_BE_REPORTED_AS_ABSENT")
    if common["applied_to_this_replay"] is not False:
        fail("COMMON_V1_POLICY_MUST_NOT_BE_APPLIED")
    if common["market_specific_normalization_freshness_and_replay_inherited"] is not False:
        fail("COMMON_V1_SCOPE_CHANGED", "inherited")
    if not common["not_applied_reason"] or not common["market_kill_stress_condition_status"]:
        fail("POLICY_BASIS_SCHEMA_INVALID", "common attribution")
    return basis


def _validate_no_conclusion(value: dict, basis: dict) -> None:
    # Scanned first, over the whole report, so a recommendation added under a new
    # key name is always reported as smuggling rather than as whichever section's
    # schema happened to notice the extra key.
    smuggled = sorted(set(_forbidden_keys(value)))
    if smuggled:
        fail("POLICY_CONCLUSION_SMUGGLED", ",".join(smuggled))

    conclusion = value.get("policy_conclusion")
    # Exact key set, not "every key that happens to be here": removing a refusal
    # flag must fail rather than pass by having nothing left to check.
    if not isinstance(conclusion, dict) or sorted(conclusion) != sorted(CONCLUSION_KEYS):
        fail("POLICY_CONCLUSION_SCHEMA_INVALID")
    if conclusion["conclusion"] is not None:
        fail("POLICY_CONCLUSION_MUST_STAY_NULL")
    if conclusion["conclusion_status"] != CONCLUSION_STATUS:
        fail("POLICY_CONCLUSION_STATUS_INVALID")
    if not isinstance(conclusion["statement"], str) or not conclusion["statement"]:
        fail("POLICY_CONCLUSION_SCHEMA_INVALID", "statement")
    for flag in CONCLUSION_FALSE_FLAGS:
        if conclusion[flag] is not False:
            fail("POLICY_CONCLUSION_MUST_STAY_NULL", flag)

    candidate_status = basis["market_specific_candidate_policy"]["component_status"]
    common_status = basis["common_v1_replay_policy"]["policy_status"]

    hysteresis = value.get("hysteresis_facts")
    if not isinstance(hysteresis, dict) or sorted(hysteresis) != [
        "common_v1_replay_hysteresis", "hysteresis_applied",
        "hysteresis_parameter_source", "hysteresis_policy_status", "markets",
        "statement",
    ]:
        fail("HYSTERESIS_FACTS_SCHEMA_INVALID")
    if hysteresis["hysteresis_applied"] is not False:
        fail("HYSTERESIS_MUST_NOT_BE_APPLIED")
    if hysteresis["hysteresis_parameter_source"] is not None:
        fail("HYSTERESIS_MUST_NOT_BE_APPLIED", "parameter_source")
    quoted = hysteresis["common_v1_replay_hysteresis"]
    if not isinstance(quoted, dict) or quoted.get("applied_to_this_replay") is not False:
        fail("COMMON_V1_POLICY_MUST_NOT_BE_APPLIED", "hysteresis")
    if hysteresis["hysteresis_policy_status"] != {
        "market_specific_candidate_component": candidate_status["HYSTERESIS"],
        "common_v1_replay_policy": common_status,
    }:
        fail("HYSTERESIS_POLICY_STATUS_INCONSISTENT")

    stress = value.get("stress_detection_facts")
    if not isinstance(stress, dict) or sorted(stress) != [
        "common_v1_replay_stress_behavior", "detection_source", "markets",
        "statement", "stress_policy_status",
        "stress_threshold_introduced_by_this_module",
    ]:
        fail("STRESS_FACTS_SCHEMA_INVALID")
    if stress["stress_threshold_introduced_by_this_module"] is not False:
        fail("STRESS_THRESHOLD_MUST_NOT_BE_INTRODUCED")
    behavior = stress["common_v1_replay_stress_behavior"]
    if not isinstance(behavior, dict) or behavior.get("applied_to_this_replay") is not False:
        fail("COMMON_V1_POLICY_MUST_NOT_BE_APPLIED", "stress")
    if stress["stress_policy_status"] != {
        "market_specific_candidate_component": candidate_status["STRESS_OVERRIDE"],
        "common_v1_replay_policy": common_status,
    }:
        fail("STRESS_POLICY_STATUS_INCONSISTENT")


def _validate_counts(value: dict) -> None:
    """Per-axis status buckets must be total over the requested dates.

    A bucket set that does not add up would mean a date was counted twice or
    dropped — either of which would silently misstate coverage or UNKNOWN.
    """
    coverage = value.get("coverage_facts")
    if not isinstance(coverage, dict):
        fail("COVERAGE_FACTS_INVALID", "coverage_facts")
    dates = coverage.get("requested_dates")
    total = coverage.get("requested_date_count")
    if (
        not isinstance(dates, list)
        or not dates
        or any(not isinstance(date, str) for date in dates)
        or dates != sorted(set(dates))
    ):
        fail("COVERAGE_FACTS_INVALID", "requested_dates")
    if not isinstance(total, int) or isinstance(total, bool) or total != len(dates):
        fail("COVERAGE_FACTS_INVALID", "requested_date_count")
    markets = coverage.get("markets")
    if not isinstance(markets, dict) or sorted(markets) != sorted(MARKETS):
        fail("COVERAGE_FACTS_INVALID", "markets")
    for market in MARKETS:
        market_facts = markets[market]
        if not isinstance(market_facts, dict):
            fail("COVERAGE_FACTS_INVALID", market)
        outcomes = market_facts.get("outcome_counts")
        if not isinstance(outcomes, dict) or sorted(outcomes) != sorted(CSR.OUTCOMES):
            fail("COVERAGE_FACTS_INVALID", f"{market}.outcome_counts")
        if _total(outcomes) != total:
            fail("COVERAGE_COUNTS_NOT_TOTAL", f"{market}.outcome_counts")
        per_axis = market_facts.get("axis_status_counts")
        if not isinstance(per_axis, dict) or sorted(per_axis) != sorted(AXES):
            fail("COVERAGE_FACTS_INVALID", f"{market}.axis_status_counts")
        for name in AXES:
            buckets = per_axis[name]
            if not isinstance(buckets, dict) or sorted(buckets) != sorted(AXIS_STATUSES):
                fail("AXIS_STATUS_VOCABULARY_INVALID", f"{market}.{name}")
            if _total(buckets) != total:
                fail("COVERAGE_COUNTS_NOT_TOTAL", f"{market}.{name}")


def _total(counts: dict) -> int | None:
    """Sum a count bucket, or ``None`` if any value is not a plain count."""
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()
    ):
        return None
    return sum(counts.values())


def _validate_sequences(value: dict) -> None:
    facts = value.get("transition_facts")
    markets = facts.get("markets") if isinstance(facts, dict) else None
    if not isinstance(markets, dict) or sorted(markets) != sorted(MARKETS):
        fail("TRANSITION_FACTS_INVALID", "markets")
    total = value.get("coverage_facts", {}).get("requested_date_count")
    for market in MARKETS:
        per_market = markets[market]
        directions = per_market.get("axis_direction") if isinstance(per_market, dict) else None
        if not isinstance(directions, dict) or sorted(directions) != sorted(AXES):
            fail("TRANSITION_FACTS_INVALID", f"{market}.axis_direction")
        sequences = [per_market.get("candidate_regime")] + [
            directions[name] for name in AXES
        ]
        for sequence in sequences:
            if not isinstance(sequence, dict) or sorted(sequence) != sorted(SEQUENCE_KEYS):
                fail("TRANSITION_FACTS_INVALID", market)
            if not isinstance(sequence["transitions"], list):
                fail("TRANSITION_FACTS_INVALID", f"{market}.transitions")
            for key in (
                "sequence_length", "observed_state_count", "unknown_state_count",
                "transition_count", "state_change_count",
                "evidence_availability_change_count",
            ):
                if not isinstance(sequence[key], int) or isinstance(sequence[key], bool):
                    fail("TRANSITION_FACTS_INVALID", f"{market}.{key}")
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
                if not isinstance(row, dict):
                    fail("TRANSITION_FACTS_INVALID", f"{market}.transition")
                touches_unknown = UNKNOWN_STATE in (
                    row.get("from_state"), row.get("to_state")
                )
                expected = (
                    TRANSITION_EVIDENCE_CHANGE if touches_unknown
                    else TRANSITION_STATE_CHANGE
                )
                if row.get("kind") != expected:
                    fail("UNKNOWN_MUST_NOT_COUNT_AS_STATE_CHANGE", market)
            adjacency = sequence["adjacency"]
            if not isinstance(adjacency, dict) or adjacency.get("basis") != ADJACENCY_BASIS:
                fail("ADJACENCY_BASIS_INVALID", market)


def _validate_observations(value: dict, basis: dict) -> None:
    """Every requested date must have an observation, and every fact must follow.

    This is the counterpart to the source population's own record bijection. A
    re-hashed report is a valid signature over whatever it contains, so it is
    not enough to check the observations that happen to be present: the
    published observation table must cover exactly the requested dates, and
    every fact family must then *reproduce* from it. A report whose counts,
    sequences, or stress observations do not follow from its own published
    observations fails closed rather than being trusted.

    Re-derivation runs against the report's own ``policy_basis`` rather than
    disk, so a verifier holding only the report can still run it.
    """
    coverage = value["coverage_facts"]
    dates = list(coverage["requested_dates"])
    observations = value.get("per_date_observations")
    if not isinstance(observations, dict) or sorted(observations) != sorted(MARKETS):
        fail("PER_DATE_OBSERVATIONS_INVALID", "markets")
    for market in MARKETS:
        cells = observations[market]
        if not isinstance(cells, dict) or sorted(cells) != dates:
            fail("OBSERVATIONS_NOT_BIJECTIVE_OVER_REQUESTED_DATES", market)
        for date in dates:
            _validate_observation(cells[date], f"{market}.{date}")

    table = {
        market: {date: observations[market][date] for date in dates}
        for market in MARKETS
    }
    combined_counts = coverage.get("combined_status_counts")
    if (
        not isinstance(combined_counts, dict)
        or sorted(combined_counts) != sorted(CSR.COMBINED_STATUSES)
        or _total(combined_counts) != len(dates)
    ):
        fail("COVERAGE_FACTS_INVALID", "combined_status_counts")
    available = {}
    for market in MARKETS:
        flag = coverage["markets"][market].get("population_available")
        if not isinstance(flag, bool):
            fail("COVERAGE_FACTS_INVALID", f"{market}.population_available")
        available[market] = flag

    if coverage != coverage_facts(table, dates, combined_counts, available):
        fail("COVERAGE_FACTS_INCONSISTENT")
    if value.get("unknown_facts") != unknown_facts(table, dates):
        fail("UNKNOWN_FACTS_INCONSISTENT")
    transitions = transition_facts(table, dates)
    if value.get("transition_facts") != transitions:
        fail("TRANSITION_FACTS_INCONSISTENT")
    if value.get("stress_detection_facts") != stress_facts(table, dates, basis):
        fail("STRESS_FACTS_INCONSISTENT")
    if value.get("hysteresis_facts") != hysteresis_facts(transitions, basis):
        fail("HYSTERESIS_FACTS_INCONSISTENT")


def _validate_source_population(value: dict) -> None:
    """The pinned source block must be complete, well-formed, and self-agreeing.

    Checked on every verification, not only when the caller still holds the
    population. This block is the report's entire claim about *what* it
    summarized: a verifier that skipped it without the source would accept a
    re-signed report whose source digest, SHADOW mode, or evidence class had
    simply been deleted, while the report still said its source was pinned.

    Standalone checking cannot prove the digest names this population — only
    ``--verify-against`` re-derivation does that, and it is retained above. What
    it can prove is that the pin exists, is a syntactically valid SHA-256, names
    the module and contract that may produce it, is SHADOW historical-backfill
    rather than NATURAL, and covers exactly the requested dates and market
    availability this report's own facts were derived from.
    """
    source = value.get("source_population")
    if not isinstance(source, dict) or sorted(source) != sorted(SOURCE_POPULATION_KEYS):
        fail("SOURCE_POPULATION_SCHEMA_INVALID")
    if source["schema_version"] != CSR.SCHEMA_VERSION:
        fail("SOURCE_POPULATION_SCHEMA_INVALID", "schema_version")
    # A summary of SHADOW historical backfill must never claim a NATURAL source.
    if source["mode"] != CSR.MODE or source["evidence_class"] != EVIDENCE_CLASS:
        fail("SOURCE_POPULATION_MODE_INVALID")
    digest = source["payload_sha256"]
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        fail("SOURCE_POPULATION_SHA_INVALID")
    if source["source_module"] != SOURCE_MODULE:
        fail("SOURCE_POPULATION_SCHEMA_INVALID", "source_module")
    if source["revalidated_by_its_own_validator"] is not True:
        fail("SOURCE_POPULATION_NOT_REVALIDATED_BY_ITS_OWNER")
    # The pinned scope must be the scope the facts below were derived over,
    # otherwise the report would summarize one replay while pinning another.
    coverage = value["coverage_facts"]
    if source["requested_dates"] != list(coverage["requested_dates"]):
        fail("SOURCE_POPULATION_SCOPE_INCONSISTENT", "requested_dates")
    available = source["market_population_available"]
    if (
        not isinstance(available, dict)
        or sorted(available) != sorted(MARKETS)
        # Explicitly bool, because ``1 == True`` would otherwise let a
        # non-boolean availability claim compare equal to the coverage facts.
        or any(not isinstance(flag, bool) for flag in available.values())
    ):
        fail("SOURCE_POPULATION_SCHEMA_INVALID", "market_population_available")
    if available != {
        market: coverage["markets"][market]["population_available"]
        for market in MARKETS
    }:
        fail("SOURCE_POPULATION_SCOPE_INCONSISTENT", "market_population_available")


def _validate_observation(cell: object, label: str) -> None:
    """One observation cell, complete and in this report's own vocabulary."""
    if not isinstance(cell, dict) or sorted(cell) != sorted(OBSERVATION_KEYS):
        fail("OBSERVATION_SCHEMA_INVALID", label)
    if cell["outcome"] not in CSR.OUTCOMES:
        fail("OBSERVATION_OUTCOME_INVALID", label)
    if not isinstance(cell["lookahead_contained"], bool):
        fail("OBSERVATION_SCHEMA_INVALID", f"{label}.lookahead_contained")
    for key in ("blocked_reason_code", "candidate_regime", "candidate_classification_status"):
        if cell[key] is not None and not isinstance(cell[key], str):
            fail("OBSERVATION_SCHEMA_INVALID", f"{label}.{key}")
    for key in ("axis_status", "axis_direction", "axis_reason_code"):
        per_axis = cell[key]
        if not isinstance(per_axis, dict) or sorted(per_axis) != sorted(AXES):
            fail("OBSERVATION_AXIS_SET_INVALID", f"{label}.{key}")
    for name in AXES:
        if cell["axis_status"][name] not in AXIS_STATUSES:
            fail("OBSERVATION_AXIS_STATUS_INVALID", f"{label}.{name}")
        for key in ("axis_direction", "axis_reason_code"):
            if cell[key][name] is not None and not isinstance(cell[key][name], str):
                fail("OBSERVATION_SCHEMA_INVALID", f"{label}.{key}.{name}")
        # An axis without an OBSERVED status carries no direction: that is the
        # UNKNOWN/observed boundary, and a direction here would smuggle a state
        # into a date the replay could not observe.
        if cell["axis_status"][name] != AXIS_OBSERVED and cell["axis_direction"][name] is not None:
            fail("UNOBSERVED_AXIS_MUST_NOT_CARRY_A_DIRECTION", f"{label}.{name}")


def _validate_pit_and_audit_separation(value: dict) -> None:
    """The PIT / historical-audit declaration must be complete and unchanged.

    Point-in-time integrity is the one boundary this repository treats as
    non-negotiable, so the block asserting it is verified exactly rather than
    carried. Three things are required and none is redundant:

    * the **exact key set**, because a re-signed report that deletes
      ``future_dates_used_in_any_date_evaluation`` has not stopped claiming PIT
      integrity — it has stopped being checkable, and every other guarantee in
      this file would still pass;
    * the **declared value** of every flag, because a report that sets that flag
      ``true`` and re-signs itself would otherwise publish, under a valid
      signature, a summary that both claims and denies the boundary;
    * the **statement and adjacency basis**, because rewriting the prose — or
      quietly relabelling requested-date adjacency as calendar adjacency — while
      leaving the booleans alone misleads a human reader just as effectively.

    This checks what the report *declares*. What it actually did is enforced
    separately and structurally: every fact family is re-derived from the
    report's own per-date observations in ``_validate_observations``, the source
    population is re-checked by its own validator in ``build_evidence``, and the
    per-date table is built from one date's record at a time. None of those
    depends on this block being honest.
    """
    separation = value.get("pit_and_audit_separation")
    if not isinstance(separation, dict) or sorted(separation) != sorted(PIT_SEPARATION_KEYS):
        fail("PIT_SEPARATION_SCHEMA_INVALID")
    for key in PIT_SEPARATION_TRUE_KEYS:
        if separation[key] is not True:
            fail("PIT_SEPARATION_DECLARATION_INVALID", key)
    for key in PIT_SEPARATION_FALSE_KEYS:
        if separation[key] is not False:
            fail("PIT_SEPARATION_DECLARATION_INVALID", key)
    if separation["sequence_adjacency_basis"] != ADJACENCY_BASIS:
        fail("ADJACENCY_BASIS_INVALID", "pit_and_audit_separation")
    if separation["statement"] != PIT_SEPARATION_STATEMENT:
        fail("PIT_SEPARATION_STATEMENT_INVALID")


def validate_evidence(
    value: dict, *, population: dict | None = None, root: Path = ROOT,
) -> dict:
    """Integrity/shape check, plus full re-derivation when the source is supplied.

    Re-derivation is offered rather than required because the source population
    is external SHADOW output that a verifier may not still hold. When it *is*
    supplied, the check is exact: this report is a pure function of that
    population, so any drift is a defect.

    The optional argument governs re-derivation *only*. The report's own pinned
    ``source_population`` block is checked either way — a standalone verifier
    that inspected it only when it already held the population would accept a
    re-signed report that had simply deleted its source digest while still
    claiming its source was pinned.
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
    basis = _validate_policy_basis(value)
    _validate_no_conclusion(value, basis)
    _validate_counts(value)
    _validate_sequences(value)
    _validate_observations(value, basis)
    # After the coverage facts and observations it must agree with, so the pinned
    # scope is compared against an already-verified one rather than a claim.
    _validate_source_population(value)
    _validate_pit_and_audit_separation(value)
    authority = value.get("authority")
    # Exact key set, not "every key that happens to be here": a payload that
    # deletes an explicit false boundary must fail, not pass silently.
    if not isinstance(authority, dict) or sorted(authority) != sorted(AUTHORITY):
        fail("EVIDENCE_AUTHORITY_SCHEMA_INVALID")
    for key, allowed in AUTHORITY.items():
        if authority[key] is not allowed:
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
