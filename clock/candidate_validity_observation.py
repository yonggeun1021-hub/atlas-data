"""P8-12 candidate-validity SHADOW observation.

This module measures the timing shape of real Dynamic Clock candidates.  It
does *not* decide whether a candidate is fresh enough for Risk Capacity,
P8-13, entry, sizing, or trading.  No validity-window authority is ratified
today, so every candidate remains fail-closed as
``NOT_COMPUTABLE_CANDIDATE_FRESHNESS_UNRATIFIED``.

The observation is deliberately derived only from an already-built Dynamic
Clock report.  It makes no provider call, reads no future price outcome, and
uses no wall clock.  The report's own decision date is the observation date.
Each persisted observation and its exact source report are content-addressed,
so an identical complete run is a byte-identical no-op and distinct same-day
evaluations cannot overwrite one another.  Because an exact operational
evaluation timestamp intentionally makes each real evaluation distinct, the
v3 observation also carries an evaluation-invariant source hash.  Sample
counting must use that invariant hash, not the exact report hash, so repeatedly
evaluating unchanged evidence cannot inflate the evidence population.
Retaining the canonical source bytes is part of the contract: the rolling
``dynamic_clock_report.json`` may be replaced by a later run, but an older
observation must remain independently rebuildable without searching git
history.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

from replay.opportunity_trigger import canonical_json, payload_sha256

from clock import dynamic_clock as dc
from clock.review_candidate import (
    AUTHORITY_ALL_FALSE,
    _operational_evaluation_context,
    validate_review_candidate,
)


CONTRACT_VERSION = "candidate_validity_shadow_observation/4"
LEGACY_CONTRACT_VERSION = "candidate_validity_shadow_observation/2"
OBSERVATION_MODE = "PROVISIONAL_SHADOW_OBSERVATION_ONLY"
VALIDITY_POLICY_STATUS = "UNRATIFIED_NO_CANDIDATE_VALIDITY_WINDOW_AUTHORITY"
FRESHNESS_STATUS = "NOT_COMPUTABLE_CANDIDATE_FRESHNESS_UNRATIFIED"
PIT_DATE_ORDER_VALID = "PIT_DATE_ORDER_VALID"
TIME_PRECISION_NOT_COMPUTABLE = "NOT_COMPUTABLE_TIME_PRECISION"
EVALUATION_TIMESTAMP_EXACT_CURRENT_RUN_ONLY = "EXACT_CURRENT_RUN_EVALUATION_ONLY"
EVALUATION_TIMESTAMP_NOT_AVAILABLE = "NOT_AVAILABLE_ARTIFACT_REPRODUCTION"
SCHEDULE_WITHIN = "WITHIN_PROVISIONAL_DYNAMIC_CLOCK_REVIEW_SCHEDULE"
SCHEDULE_OUTSIDE = "OUTSIDE_PROVISIONAL_DYNAMIC_CLOCK_REVIEW_SCHEDULE"

EXPECTED_MARKETS = ("BTC", "CRYPTO", "KOREA")
EXPECTED_DYNAMIC_POLICY_STATUS = "PROVISIONAL_CIO_MVP"
TRIGGER_UPSTREAM_WORKFLOW_RUN = "UPSTREAM_WORKFLOW_RUN"
TRIGGER_MANUAL_WORKFLOW_DISPATCH = "MANUAL_WORKFLOW_DISPATCH"
TRIGGER_LOCAL_REPRODUCTION = "LOCAL_REPRODUCTION"
VALID_TRIGGER_KINDS = (
    TRIGGER_UPSTREAM_WORKFLOW_RUN,
    TRIGGER_MANUAL_WORKFLOW_DISPATCH,
    TRIGGER_LOCAL_REPRODUCTION,
)
SOURCE_REPORT_DIRECTORY = "candidate_validity_source_reports"
SOURCE_REPORT_FORMAT = "CANONICAL_JSON_UTF8_LF"

# The v2 CIO design review found no retained live sample for these trigger
# families.  Seeing one later is useful new evidence, but does not silently
# ratify its validity window.
NO_LIVE_SAMPLE_TRIGGER_TYPES = frozenset({
    "CATALYST_APPROACH",
    "EXPECTATION_DISLOCATION",
    "FLOW_REVERSAL",
    "FUNDAMENTAL_REVISION",
})


class CandidateValidityObservationError(ValueError):
    pass


def _date(value: object, *, field: str) -> dt.date:
    if not isinstance(value, str):
        raise CandidateValidityObservationError(f"{field}_MUST_BE_DATE")
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CandidateValidityObservationError(f"{field}_INVALID:{value!r}") from exc
    if parsed.isoformat() != value:
        raise CandidateValidityObservationError(f"{field}_MUST_BE_CANONICAL_DATE")
    return parsed


def _authority() -> dict:
    return copy.deepcopy(AUTHORITY_ALL_FALSE)


def _source_report_relative_path(source_report_sha256: str) -> str:
    if (
        not isinstance(source_report_sha256, str)
        or len(source_report_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in source_report_sha256)
    ):
        raise CandidateValidityObservationError("SOURCE_REPORT_SHA256_INVALID")
    return f"{SOURCE_REPORT_DIRECTORY}/report-{source_report_sha256}.json"


def _canonical_payload_bytes(document: dict) -> bytes:
    return (canonical_json(document) + "\n").encode("utf-8")


def _evaluation_invariant_report_sha256(report: dict) -> str:
    """Hash the source semantics without the current-run evaluation instant.

    Exact source retention intentionally distinguishes two real evaluations.
    Evidence-population accounting must not.  Candidate record hashes and the
    new timing-precision member are evaluation-derived, so normalize those as
    well before hashing.  No historical trigger/decision/evidence field is
    removed.
    """
    def normalize(value: object) -> object:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value

        has_evaluation = "operational_evaluation" in value
        normalized = {}
        for key, child in value.items():
            if key == "operational_evaluation":
                continue
            # A candidate record hash is derived from the exact evaluation
            # context. Exclude it only on objects that actually carried
            # that context; unrelated record hashes remain in the basis.
            if key == "record_hash" and has_evaluation:
                continue
            normalized_child = normalize(child)
            if key == "timing_precision" and isinstance(normalized_child, dict):
                normalized_child.pop("operational_evaluated_at", None)
            normalized[key] = normalized_child
        return normalized

    return payload_sha256(normalize(report))


def _validate_source_report(report: dict) -> None:
    if not isinstance(report, dict):
        raise CandidateValidityObservationError("SOURCE_REPORT_MUST_BE_OBJECT")
    if report.get("policy_approval_status") != EXPECTED_DYNAMIC_POLICY_STATUS:
        raise CandidateValidityObservationError("SOURCE_DYNAMIC_POLICY_STATUS_UNEXPECTED")
    if set(report.get("by_market", {})) != set(EXPECTED_MARKETS):
        raise CandidateValidityObservationError("SOURCE_MARKET_SET_MISMATCH")

    report_date = _date(report.get("decision_date"), field="REPORT_DECISION_DATE")
    report_evaluation = report.get("operational_evaluation")
    if report_evaluation is not None:
        if not isinstance(report_evaluation, dict):
            raise CandidateValidityObservationError(
                "SOURCE_OPERATIONAL_EVALUATION_SCHEMA_INVALID"
            )
        try:
            expected_evaluation = _operational_evaluation_context(
                report["decision_date"], report_evaluation.get("evaluated_at_utc")
            )
        except ValueError as exc:
            raise CandidateValidityObservationError(str(exc)) from exc
        if report_evaluation != expected_evaluation:
            raise CandidateValidityObservationError(
                "SOURCE_OPERATIONAL_EVALUATION_MISMATCH"
            )
    seen_ids: set[str] = set()
    for market in EXPECTED_MARKETS:
        market_report = report["by_market"][market]
        if market_report.get("market") != market:
            raise CandidateValidityObservationError("SOURCE_MARKET_LABEL_MISMATCH")
        if _date(market_report.get("decision_date"), field="MARKET_DECISION_DATE") != report_date:
            raise CandidateValidityObservationError("SOURCE_MARKET_DECISION_DATE_MISMATCH")
        if report_evaluation is not None and market_report.get(
            "operational_evaluation"
        ) != report_evaluation:
            raise CandidateValidityObservationError(
                "SOURCE_MARKET_OPERATIONAL_EVALUATION_MISMATCH"
            )
        queue = market_report.get("review_queue")
        if not isinstance(queue, list):
            raise CandidateValidityObservationError("SOURCE_REVIEW_QUEUE_MUST_BE_LIST")
        if market_report.get("review_queue_subject_count") != len(queue):
            raise CandidateValidityObservationError("SOURCE_REVIEW_QUEUE_COUNT_MISMATCH")

        tier_counts = {"IMMEDIATE_REVIEW": 0, "WATCH_REVIEW": 0, "OBSERVATION_ONLY": 0}
        for candidate in queue:
            validate_review_candidate(candidate)
            candidate_id = candidate.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                raise CandidateValidityObservationError("SOURCE_CANDIDATE_ID_MISSING")
            if candidate_id in seen_ids:
                raise CandidateValidityObservationError("SOURCE_CANDIDATE_ID_DUPLICATE")
            seen_ids.add(candidate_id)
            if candidate.get("market") != market:
                raise CandidateValidityObservationError("SOURCE_CANDIDATE_MARKET_MISMATCH")
            if _date(candidate.get("decision_at"), field="CANDIDATE_DECISION_AT") != report_date:
                raise CandidateValidityObservationError("SOURCE_CANDIDATE_DECISION_DATE_MISMATCH")
            if report_evaluation is not None and candidate.get(
                "operational_evaluation"
            ) != report_evaluation:
                raise CandidateValidityObservationError(
                    "SOURCE_CANDIDATE_OPERATIONAL_EVALUATION_MISMATCH"
                )
            tier = candidate.get("tier")
            if tier not in tier_counts:
                raise CandidateValidityObservationError("SOURCE_CANDIDATE_TIER_UNKNOWN")
            tier_counts[tier] += 1
        if market_report.get("tier_counts") != tier_counts:
            raise CandidateValidityObservationError("SOURCE_TIER_COUNTS_MISMATCH")


def _candidate_observation(
    candidate: dict,
    observation_date: dt.date,
    *,
    include_operational_evaluation: bool,
) -> dict:
    evidence_as_of = _date(candidate["evidence_as_of"], field="EVIDENCE_AS_OF")
    trigger_observed_at = _date(candidate["trigger_observed_at"], field="TRIGGER_OBSERVED_AT")
    created_at = _date(candidate["candidate_created_at"], field="CANDIDATE_CREATED_AT")
    updated_at = _date(candidate["candidate_updated_at"], field="CANDIDATE_UPDATED_AT")
    decision_at = _date(candidate["decision_at"], field="DECISION_AT")
    expiry = _date(candidate["expiry"], field="EXPIRY")

    if not (
        evidence_as_of <= trigger_observed_at <= updated_at <= decision_at <= observation_date
        and created_at <= updated_at
    ):
        raise CandidateValidityObservationError("CANDIDATE_PIT_DATE_ORDER_INVALID")

    aggregate_precision = candidate.get("time_precision")
    if aggregate_precision != "DATE_ONLY":
        # The upstream contract currently permits only DATE_ONLY.  Keep an
        # explicit guard here so a later upstream expansion requires a
        # deliberate contract revision rather than silently opening a gate.
        raise CandidateValidityObservationError("CANDIDATE_TIME_PRECISION_CONTRACT_CHANGED")

    trigger_types = sorted(candidate["trigger_types"])
    result = {
        "candidate_id": candidate["candidate_id"],
        "candidate_record_hash": candidate["record_hash"],
        "subject": candidate["subject"],
        "market": candidate["market"],
        "tier_observed": candidate["tier"],
        "trigger_types": trigger_types,
        "evidence_as_of": candidate["evidence_as_of"],
        "trigger_observed_at": candidate["trigger_observed_at"],
        "candidate_created_at": candidate["candidate_created_at"],
        "candidate_updated_at": candidate["candidate_updated_at"],
        "decision_at": candidate["decision_at"],
        "expiry": candidate["expiry"],
        "time_precision": aggregate_precision,
        "timing_precision": copy.deepcopy(candidate["timing_precision"]),
        "candidate_age_calendar_days": (observation_date - created_at).days,
        "days_since_candidate_update": (observation_date - updated_at).days,
        "days_to_provisional_dynamic_clock_expiry": (expiry - observation_date).days,
        "pit_date_order_status": PIT_DATE_ORDER_VALID,
        "timestamp_order_status": TIME_PRECISION_NOT_COMPUTABLE,
        "dynamic_clock_schedule_observation": (
            SCHEDULE_WITHIN if observation_date <= expiry else SCHEDULE_OUTSIDE
        ),
        "dynamic_clock_schedule_authority": "DIAGNOSTIC_ONLY_PROVISIONAL_POLICY",
        "candidate_freshness_status": FRESHNESS_STATUS,
        "risk_capacity_status": FRESHNESS_STATUS,
        "p8_13_entry_proposal_status": "LOCKED_NOT_STARTED",
        "authority": _authority(),
    }
    if include_operational_evaluation:
        evaluation = copy.deepcopy(candidate["operational_evaluation"])
        result["operational_evaluation"] = evaluation
        result["operational_evaluation_timestamp_status"] = (
            EVALUATION_TIMESTAMP_EXACT_CURRENT_RUN_ONLY
            if evaluation["evaluated_at_utc"] is not None
            else EVALUATION_TIMESTAMP_NOT_AVAILABLE
        )
    result["record_hash"] = payload_sha256(result)
    return result


def _trigger_type_observations(report: dict, policy: dict) -> list[dict]:
    counts = {name: 0 for name in policy["trigger_types"]}
    for market in EXPECTED_MARKETS:
        for candidate in report["by_market"][market]["review_queue"]:
            for trigger_type in candidate["trigger_types"]:
                if trigger_type not in counts:
                    raise CandidateValidityObservationError("SOURCE_TRIGGER_TYPE_NOT_IN_POLICY")
                counts[trigger_type] += 1

    observations = []
    for trigger_type in sorted(counts):
        count = counts[trigger_type]
        if trigger_type in NO_LIVE_SAMPLE_TRIGGER_TYPES and count == 0:
            evidence_status = "UNVALIDATED_NO_LIVE_SAMPLE"
        elif count == 0:
            evidence_status = "NO_SAMPLE_IN_THIS_OBSERVATION"
        else:
            evidence_status = "PROVISIONAL_SHADOW_SAMPLE_ONLY"
        observations.append({
            "trigger_type": trigger_type,
            "candidate_observation_count": count,
            "validity_evidence_status": evidence_status,
        })
    return observations


def build_observation(
    report: dict,
    policy: dict | None = None,
    *,
    trigger_kind: str = TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    """Build a deterministic, non-authoritative timing observation."""
    _validate_source_report(report)
    if trigger_kind not in VALID_TRIGGER_KINDS:
        raise CandidateValidityObservationError("OBSERVATION_TRIGGER_KIND_INVALID")
    if policy is None:
        policy = dc.load_policy()
    if policy.get("approval_status") != report.get("policy_approval_status"):
        raise CandidateValidityObservationError("SOURCE_POLICY_DOCUMENT_STATUS_MISMATCH")
    if policy.get("policy_version") != report.get("policy_version"):
        raise CandidateValidityObservationError("SOURCE_POLICY_DOCUMENT_VERSION_MISMATCH")
    if not isinstance(policy.get("trigger_types"), dict) or not policy["trigger_types"]:
        raise CandidateValidityObservationError("SOURCE_POLICY_TRIGGER_TYPES_MISSING")

    observation_date = _date(report["decision_date"], field="OBSERVATION_DATE")
    include_operational_evaluation = "operational_evaluation" in report
    source_report_sha256 = payload_sha256(report)
    by_market: dict[str, dict] = {}
    total_candidates = 0
    for market in EXPECTED_MARKETS:
        candidates = [
            _candidate_observation(
                candidate,
                observation_date,
                include_operational_evaluation=include_operational_evaluation,
            )
            for candidate in sorted(
                report["by_market"][market]["review_queue"],
                key=lambda item: (item["subject"], item["candidate_id"]),
            )
        ]
        total_candidates += len(candidates)
        by_market[market] = {
            "market": market,
            "candidate_count": len(candidates),
            "candidate_freshness_status_counts": {FRESHNESS_STATUS: len(candidates)},
            "pit_date_order_status_counts": {PIT_DATE_ORDER_VALID: len(candidates)},
            "timestamp_order_status_counts": {TIME_PRECISION_NOT_COMPUTABLE: len(candidates)},
            "candidates": candidates,
        }

    observation = {
        "contract_version": (
            CONTRACT_VERSION if include_operational_evaluation
            else LEGACY_CONTRACT_VERSION
        ),
        "wbs_item": "P8-12 Candidate Validity Shadow Observation",
        "observation_mode": OBSERVATION_MODE,
        "validity_policy_status": VALIDITY_POLICY_STATUS,
        "observation_date": report["decision_date"],
        "source_run": {
            "trigger_kind": trigger_kind,
            "sample_qualification": {
                TRIGGER_UPSTREAM_WORKFLOW_RUN: "NATURAL_OPERATIONAL_SAMPLE",
                TRIGGER_MANUAL_WORKFLOW_DISPATCH: "MANUAL_OPERATIONAL_SAMPLE",
                TRIGGER_LOCAL_REPRODUCTION: "LOCAL_REPRODUCTION_NOT_OPERATIONAL_SAMPLE",
            }[trigger_kind],
        },
        "source_dynamic_clock": {
            "report_sha256": source_report_sha256,
            "retained_report": {
                "path": _source_report_relative_path(source_report_sha256),
                "format": SOURCE_REPORT_FORMAT,
                "append_only": True,
            },
            "policy_version": report["policy_version"],
            "policy_approval_status": report["policy_approval_status"],
            "mode": report["mode"],
            "report_asof_evidence_date": report["report_asof_evidence_date"],
        },
        "candidate_count": total_candidates,
        "trigger_type_observations": _trigger_type_observations(report, policy),
        "by_market": by_market,
        "downstream_locks": {
            "risk_capacity": FRESHNESS_STATUS,
            "p8_13_entry_proposal": "LOCKED_NOT_STARTED",
            "stage": False,
            "buy": False,
            "action": False,
            "order": False,
            "production": False,
            "trading": False,
        },
        "authority": _authority(),
    }
    if include_operational_evaluation:
        observation["operational_evaluation"] = copy.deepcopy(
            report["operational_evaluation"]
        )
        observation["source_dynamic_clock"][
            "evaluation_invariant_report_sha256"
        ] = _evaluation_invariant_report_sha256(report)
    observation["observation_sha256"] = payload_sha256(observation)
    return observation


def validate_observation(
    observation: dict,
    source_report: dict,
    policy: dict | None = None,
    *,
    trigger_kind: str = TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    """Independently rebuild the observation from its Dynamic Clock source."""
    expected = build_observation(source_report, policy, trigger_kind=trigger_kind)
    if observation != expected:
        raise CandidateValidityObservationError("OBSERVATION_SEMANTIC_TAMPER_OR_DRIFT")
    return observation


def write_observation(
    report: dict,
    *,
    output_root: Path,
    source_output_root: Path | None = None,
    policy: dict | None = None,
    trigger_kind: str = TRIGGER_LOCAL_REPRODUCTION,
) -> Path:
    """Persist an exact source snapshot and its derived observation.

    The source snapshot is written first.  This prevents an observation from
    being committed without the immutable input required to rebuild it.
    """
    observation = build_observation(report, policy, trigger_kind=trigger_kind)
    source_sha = observation["source_dynamic_clock"]["report_sha256"]
    expected_source_relative = _source_report_relative_path(source_sha)
    retained = observation["source_dynamic_clock"]["retained_report"]
    if retained != {
        "path": expected_source_relative,
        "format": SOURCE_REPORT_FORMAT,
        "append_only": True,
    }:
        raise CandidateValidityObservationError("SOURCE_RETENTION_METADATA_INVALID")

    if source_output_root is None:
        source_output_root = output_root.parent / SOURCE_REPORT_DIRECTORY
    expected_source_root = output_root.parent / SOURCE_REPORT_DIRECTORY
    if source_output_root.resolve() != expected_source_root.resolve():
        raise CandidateValidityObservationError("SOURCE_OUTPUT_ROOT_MISROUTED")
    source_target = source_output_root / f"report-{source_sha}.json"
    source_payload = _canonical_payload_bytes(report)
    source_output_root.mkdir(parents=True, exist_ok=True)
    if source_target.exists():
        if source_target.read_bytes() != source_payload:
            raise CandidateValidityObservationError(
                "APPEND_ONLY_EXISTING_SOURCE_REPORT_TAMPERED"
            )
    else:
        source_target.write_bytes(source_payload)

    observation_sha = observation["observation_sha256"]
    target_dir = output_root / observation["observation_date"]
    target = target_dir / f"observation-{observation_sha}.json"
    payload = _canonical_payload_bytes(observation)
    target_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise CandidateValidityObservationError("APPEND_ONLY_EXISTING_OBSERVATION_TAMPERED")
        return target
    target.write_bytes(payload)
    return target


def load_and_validate_observation(
    observation_path: Path,
    dynamic_clock_root: Path | None = None,
    policy: dict | None = None,
    *,
    trigger_kind: str = TRIGGER_LOCAL_REPRODUCTION,
) -> dict:
    """Validate a persisted observation using only its retained source.

    The rolling ``dynamic_clock_report.json`` is deliberately not accepted.
    Exact canonical bytes, content-addressed filename, metadata path and both
    hashes must all agree before the semantic rebuild is attempted.
    """
    observation_bytes = observation_path.read_bytes()
    observation = json.loads(observation_bytes.decode("utf-8"))
    if observation_bytes != _canonical_payload_bytes(observation):
        raise CandidateValidityObservationError("OBSERVATION_BYTES_NOT_CANONICAL")
    observation_sha = observation.get("observation_sha256")
    expected_observation_name = f"observation-{observation_sha}.json"
    if observation_path.name != expected_observation_name:
        raise CandidateValidityObservationError("OBSERVATION_FILENAME_HASH_MISMATCH")
    if observation_path.parent.name != observation.get("observation_date"):
        raise CandidateValidityObservationError("OBSERVATION_DATE_DIRECTORY_MISMATCH")

    source_meta = observation.get("source_dynamic_clock")
    if not isinstance(source_meta, dict):
        raise CandidateValidityObservationError("SOURCE_RETENTION_METADATA_INVALID")
    source_sha = source_meta.get("report_sha256")
    expected_relative = _source_report_relative_path(source_sha)
    retained = source_meta.get("retained_report")
    if retained != {
        "path": expected_relative,
        "format": SOURCE_REPORT_FORMAT,
        "append_only": True,
    }:
        raise CandidateValidityObservationError("SOURCE_RETENTION_METADATA_INVALID")

    if dynamic_clock_root is None:
        try:
            dynamic_clock_root = observation_path.parents[2]
        except IndexError as exc:
            raise CandidateValidityObservationError(
                "DYNAMIC_CLOCK_ROOT_NOT_RESOLVABLE"
            ) from exc
    source_report_path = dynamic_clock_root / expected_relative
    if not source_report_path.is_file():
        raise CandidateValidityObservationError("RETAINED_SOURCE_REPORT_MISSING")
    source_bytes = source_report_path.read_bytes()
    source_report = json.loads(source_bytes.decode("utf-8"))
    if source_bytes != _canonical_payload_bytes(source_report):
        raise CandidateValidityObservationError("RETAINED_SOURCE_BYTES_NOT_CANONICAL")
    if payload_sha256(source_report) != source_sha:
        raise CandidateValidityObservationError("RETAINED_SOURCE_HASH_MISMATCH")
    return validate_observation(
        observation,
        source_report,
        policy,
        trigger_kind=trigger_kind,
    )
