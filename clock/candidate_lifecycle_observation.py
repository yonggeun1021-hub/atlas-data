#!/usr/bin/env python3
"""Forward-only P8-12 candidate lifecycle shadow observations.

This module records when Atlas itself first observes, changes, loses, or
re-observes a Dynamic Clock subject *after* this contract is deployed.  It
never fabricates an intraday timestamp for a candidate that already existed
at the baseline, and it never treats the operational evaluation timestamp as
the historical source-event time.

Only a ``NATURAL_OPERATIONAL_SAMPLE`` advances the forward lifecycle chain.
Manual and local runs may exercise the mechanism, but remain standalone
diagnostics.  Every output is append-only, content-addressed, independently
rebuildable from the retained Candidate Validity v4 observation, and locked
away from validity, Risk Capacity, P8-13, orders, and trading.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path, PurePosixPath

from clock.candidate_validity_observation import (
    CONTRACT_VERSION as VALIDITY_CONTRACT_VERSION,
    FRESHNESS_STATUS,
    load_and_validate_observation,
)
from clock.review_candidate import AUTHORITY_ALL_FALSE
from replay.opportunity_trigger import canonical_json, payload_sha256


CONTRACT_VERSION = "candidate_lifecycle_shadow_observation/1"
OUTPUT_DIRECTORY = "candidate_lifecycle_observations"
SOURCE_DIRECTORY = "candidate_validity_observations"
SOURCE_FORMAT = "CANONICAL_JSON_UTF8_LF"

NATURAL_SAMPLE = "NATURAL_OPERATIONAL_SAMPLE"
MANUAL_SAMPLE = "MANUAL_OPERATIONAL_SAMPLE"
LOCAL_SAMPLE = "LOCAL_REPRODUCTION_NOT_OPERATIONAL_SAMPLE"
VALID_SAMPLE_QUALIFICATIONS = (NATURAL_SAMPLE, MANUAL_SAMPLE, LOCAL_SAMPLE)

CHAIN_NATURAL = "NATURAL_FORWARD_ONLY_CHAIN_ADVANCING"
CHAIN_MANUAL = "MANUAL_DIAGNOSTIC_NOT_CHAIN_ADVANCING"
CHAIN_LOCAL = "LOCAL_DIAGNOSTIC_NOT_CHAIN_ADVANCING"

BASELINE_PREEXISTING = "BASELINE_PREEXISTING_FIRST_SEEN_NOT_COMPUTABLE"
FIRST_SEEN_EXACT = "FIRST_SEEN_EXACT_FORWARD_ONLY"
CONTINUING_UNCHANGED = "CONTINUING_UNCHANGED"
CONTINUING_CHANGED = "CONTINUING_CHANGED"
FIRST_ABSENCE = "FIRST_ABSENCE_OBSERVED_FORWARD_ONLY"
STILL_ABSENT = "STILL_ABSENT"
REAPPEARED = "REAPPEARED_OBSERVED_FORWARD_ONLY"

FIRST_SEEN_UNKNOWN = "PRE_BASELINE_FIRST_SEEN_NOT_COMPUTABLE"
FIRST_SEEN_FORWARD = "EXACT_ATLAS_FORWARD_OBSERVATION"
HISTORICAL_TIME_UNKNOWN = "HISTORICAL_TRIGGER_TIME_NOT_RECONSTRUCTED"
FORWARD_TIME_ONLY = "ATLAS_FORWARD_OBSERVATION_NOT_SOURCE_EVENT_TIME"

LOCKS = {
    "candidate_validity": FRESHNESS_STATUS,
    "risk_capacity": FRESHNESS_STATUS,
    "p8_13_entry_proposal": "LOCKED_NOT_STARTED",
    "stage": False,
    "buy": False,
    "action": False,
    "order": False,
    "production": False,
    "trading": False,
}

SOURCE_LOCKS = {
    key: value for key, value in LOCKS.items() if key != "candidate_validity"
}


class CandidateLifecycleObservationError(ValueError):
    pass


def _canonical_bytes(document: dict) -> bytes:
    return (canonical_json(document) + "\n").encode("utf-8")


def _utc(value: object, *, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise CandidateLifecycleObservationError(f"{field}_MUST_BE_UTC_TIMESTAMP")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CandidateLifecycleObservationError(f"{field}_INVALID:{value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateLifecycleObservationError(f"{field}_TIMEZONE_REQUIRED")
    parsed = parsed.astimezone(dt.timezone.utc)
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CandidateLifecycleObservationError(f"{field}_MUST_BE_CANONICAL_UTC_SECONDS")
    return parsed


def _safe_relative_path(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateLifecycleObservationError(f"{field}_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix:
        raise CandidateLifecycleObservationError(f"{field}_MISROUTED")
    return path.as_posix()


def _source_sample_qualification(observation: dict) -> str:
    source_run = observation.get("source_run")
    if not isinstance(source_run, dict):
        raise CandidateLifecycleObservationError("SOURCE_RUN_SCHEMA_INVALID")
    qualification = source_run.get("sample_qualification")
    if qualification not in VALID_SAMPLE_QUALIFICATIONS:
        raise CandidateLifecycleObservationError("SOURCE_SAMPLE_QUALIFICATION_INVALID")
    return qualification


def _evaluation_at(observation: dict) -> str:
    context = observation.get("operational_evaluation")
    if not isinstance(context, dict) or context.get("status") != (
        "EXACT_CALLER_SUPPLIED_OPERATIONAL_RUN_TIMESTAMP"
    ) or context.get("time_precision") != "TIMESTAMP":
        raise CandidateLifecycleObservationError("SOURCE_EXACT_OPERATIONAL_EVALUATION_REQUIRED")
    value = context.get("evaluated_at_utc")
    _utc(value, field="SOURCE_OPERATIONAL_EVALUATED_AT")
    return value


def _candidate_semantic_sha256(candidate: dict) -> str:
    """Hash only lifecycle-relevant candidate semantics.

    Observation-age counters and the current evaluation context change on
    every run and are deliberately excluded.  Historical date-only fields
    are retained exactly; no timestamp is synthesized from them.
    """
    timing_precision = copy.deepcopy(candidate.get("timing_precision"))
    if not isinstance(timing_precision, dict):
        raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_TIMING_PRECISION_INVALID")
    timing_precision.pop("operational_evaluated_at", None)
    payload = {
        "subject": candidate.get("subject"),
        "market": candidate.get("market"),
        "tier_observed": candidate.get("tier_observed"),
        "trigger_types": candidate.get("trigger_types"),
        "evidence_as_of": candidate.get("evidence_as_of"),
        "trigger_observed_at": candidate.get("trigger_observed_at"),
        "candidate_created_at": candidate.get("candidate_created_at"),
        "candidate_updated_at": candidate.get("candidate_updated_at"),
        "expiry": candidate.get("expiry"),
        "time_precision": candidate.get("time_precision"),
        "timing_precision": timing_precision,
    }
    if not isinstance(payload["subject"], str) or not payload["subject"]:
        raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_SUBJECT_INVALID")
    if not isinstance(payload["market"], str) or not payload["market"]:
        raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_MARKET_INVALID")
    if not isinstance(payload["trigger_types"], list):
        raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_TRIGGER_TYPES_INVALID")
    return payload_sha256(payload)


def _candidate_rows(observation: dict) -> dict[str, dict]:
    if observation.get("contract_version") != VALIDITY_CONTRACT_VERSION:
        raise CandidateLifecycleObservationError("SOURCE_VALIDITY_CONTRACT_MUST_BE_V4")
    expected_observation_sha = payload_sha256({
        key: value for key, value in observation.items()
        if key != "observation_sha256"
    })
    if observation.get("observation_sha256") != expected_observation_sha:
        raise CandidateLifecycleObservationError("SOURCE_VALIDITY_OBSERVATION_HASH_MISMATCH")
    if observation.get("candidate_freshness_status", FRESHNESS_STATUS) != FRESHNESS_STATUS:
        raise CandidateLifecycleObservationError("SOURCE_FRESHNESS_BOUNDARY_CHANGED")
    if observation.get("downstream_locks") != SOURCE_LOCKS:
        raise CandidateLifecycleObservationError("SOURCE_DOWNSTREAM_LOCKS_CHANGED")
    if observation.get("authority") != AUTHORITY_ALL_FALSE:
        raise CandidateLifecycleObservationError("SOURCE_AUTHORITY_NOT_ALL_FALSE")

    by_market = observation.get("by_market")
    if not isinstance(by_market, dict):
        raise CandidateLifecycleObservationError("SOURCE_BY_MARKET_INVALID")
    result: dict[str, dict] = {}
    seen = 0
    for market, market_block in sorted(by_market.items()):
        if not isinstance(market_block, dict) or market_block.get("market") != market:
            raise CandidateLifecycleObservationError("SOURCE_MARKET_BLOCK_INVALID")
        candidates = market_block.get("candidates")
        if not isinstance(candidates, list) or market_block.get("candidate_count") != len(candidates):
            raise CandidateLifecycleObservationError("SOURCE_MARKET_CANDIDATE_COUNT_MISMATCH")
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("market") != market:
                raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_MARKET_MISMATCH")
            if candidate.get("candidate_freshness_status") != FRESHNESS_STATUS:
                raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_FRESHNESS_CHANGED")
            if candidate.get("risk_capacity_status") != FRESHNESS_STATUS:
                raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_RISK_LOCK_CHANGED")
            if candidate.get("p8_13_entry_proposal_status") != "LOCKED_NOT_STARTED":
                raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_P8_13_LOCK_CHANGED")
            if candidate.get("authority") != AUTHORITY_ALL_FALSE:
                raise CandidateLifecycleObservationError("SOURCE_CANDIDATE_AUTHORITY_NOT_ALL_FALSE")
            subject = candidate.get("subject")
            stable_key = payload_sha256({"market": market, "subject": subject})
            if stable_key in result:
                raise CandidateLifecycleObservationError("SOURCE_STABLE_CANDIDATE_KEY_DUPLICATE")
            result[stable_key] = {
                "stable_candidate_key": stable_key,
                "subject": subject,
                "market": market,
                "source_candidate_id": candidate.get("candidate_id"),
                "source_candidate_record_hash": candidate.get("candidate_record_hash"),
                "candidate_semantic_sha256": _candidate_semantic_sha256(candidate),
            }
            seen += 1
    if observation.get("candidate_count") != seen:
        raise CandidateLifecycleObservationError("SOURCE_TOTAL_CANDIDATE_COUNT_MISMATCH")
    return result


def _chain_qualification(sample_qualification: str) -> str:
    return {
        NATURAL_SAMPLE: CHAIN_NATURAL,
        MANUAL_SAMPLE: CHAIN_MANUAL,
        LOCAL_SAMPLE: CHAIN_LOCAL,
    }[sample_qualification]


def _validate_prior(prior: dict | None, current_evaluated_at: str) -> dict[str, dict]:
    if prior is None:
        return {}
    if prior.get("contract_version") != CONTRACT_VERSION:
        raise CandidateLifecycleObservationError("PRIOR_CONTRACT_VERSION_INVALID")
    expected_sha = payload_sha256({
        key: value for key, value in prior.items()
        if key != "lifecycle_observation_sha256"
    })
    if prior.get("lifecycle_observation_sha256") != expected_sha:
        raise CandidateLifecycleObservationError("PRIOR_LIFECYCLE_HASH_MISMATCH")
    if prior.get("chain_qualification") != CHAIN_NATURAL or not prior.get("chain_advancing"):
        raise CandidateLifecycleObservationError("PRIOR_MUST_BE_NATURAL_CHAIN_SAMPLE")
    if prior.get("sample_qualification") != NATURAL_SAMPLE:
        raise CandidateLifecycleObservationError("PRIOR_SAMPLE_QUALIFICATION_INVALID")
    if prior.get("downstream_locks") != LOCKS:
        raise CandidateLifecycleObservationError("PRIOR_DOWNSTREAM_LOCKS_CHANGED")
    if prior.get("authority") != AUTHORITY_ALL_FALSE:
        raise CandidateLifecycleObservationError("PRIOR_AUTHORITY_NOT_ALL_FALSE")
    prior_eval = prior.get("operational_evaluated_at_utc")
    if _utc(prior_eval, field="PRIOR_OPERATIONAL_EVALUATED_AT") >= _utc(
        current_evaluated_at, field="CURRENT_OPERATIONAL_EVALUATED_AT"
    ):
        raise CandidateLifecycleObservationError("LIFECYCLE_EVALUATION_NOT_STRICTLY_INCREASING")
    records = prior.get("state_records")
    if not isinstance(records, list):
        raise CandidateLifecycleObservationError("PRIOR_STATE_RECORDS_INVALID")
    counts = prior.get("state_counts")
    if counts != {
        "total": len(records),
        "active": sum(row.get("state") == "ACTIVE" for row in records if isinstance(row, dict)),
        "absent_observed": sum(
            row.get("state") == "ABSENT_OBSERVED" for row in records if isinstance(row, dict)
        ),
    }:
        raise CandidateLifecycleObservationError("PRIOR_STATE_COUNTS_INVALID")
    mapped = {}
    for row in records:
        if not isinstance(row, dict) or row.get("authority") != AUTHORITY_ALL_FALSE:
            raise CandidateLifecycleObservationError("PRIOR_STATE_RECORD_INVALID")
        key = row.get("stable_candidate_key")
        if not isinstance(key, str) or key in mapped:
            raise CandidateLifecycleObservationError("PRIOR_STATE_KEY_INVALID_OR_DUPLICATE")
        mapped[key] = row
    return mapped


def _active_row(current: dict, prior: dict | None, evaluated_at: str, *, baseline: bool) -> dict:
    if prior is None:
        event = BASELINE_PREEXISTING if baseline else FIRST_SEEN_EXACT
        first_seen = None if baseline else evaluated_at
        first_seen_status = FIRST_SEEN_UNKNOWN if baseline else FIRST_SEEN_FORWARD
        last_changed = None if baseline else evaluated_at
        first_absent = None
        last_reappeared = None
    elif prior.get("state") == "ABSENT_OBSERVED":
        event = REAPPEARED
        first_seen = prior.get("atlas_first_operational_observed_at_utc")
        first_seen_status = prior.get("first_seen_status")
        last_changed = evaluated_at
        first_absent = prior.get("first_absent_observed_at_utc")
        last_reappeared = evaluated_at
    else:
        changed = prior.get("candidate_semantic_sha256") != current["candidate_semantic_sha256"]
        event = CONTINUING_CHANGED if changed else CONTINUING_UNCHANGED
        first_seen = prior.get("atlas_first_operational_observed_at_utc")
        first_seen_status = prior.get("first_seen_status")
        last_changed = evaluated_at if changed else prior.get("last_changed_observed_at_utc")
        first_absent = prior.get("first_absent_observed_at_utc")
        last_reappeared = prior.get("last_reappeared_observed_at_utc")

    return {
        **current,
        "state": "ACTIVE",
        "lifecycle_event": event,
        "atlas_first_operational_observed_at_utc": first_seen,
        "first_seen_status": first_seen_status,
        "last_operational_observed_at_utc": evaluated_at,
        "last_changed_observed_at_utc": last_changed,
        "first_absent_observed_at_utc": first_absent,
        "last_reappeared_observed_at_utc": last_reappeared,
        "historical_trigger_time_status": (
            HISTORICAL_TIME_UNKNOWN if first_seen is None else FORWARD_TIME_ONLY
        ),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }


def _absent_row(prior: dict, evaluated_at: str) -> dict:
    first_absence = prior.get("first_absent_observed_at_utc")
    first = prior.get("state") == "ACTIVE"
    if first:
        first_absence = evaluated_at
    return {
        **copy.deepcopy(prior),
        "state": "ABSENT_OBSERVED",
        "lifecycle_event": FIRST_ABSENCE if first else STILL_ABSENT,
        "source_candidate_id": None,
        "source_candidate_record_hash": None,
        "last_changed_observed_at_utc": (
            evaluated_at if first else prior.get("last_changed_observed_at_utc")
        ),
        "first_absent_observed_at_utc": first_absence,
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }


def build_lifecycle_observation(
    source_observation: dict,
    *,
    source_observation_path: str,
    prior_lifecycle: dict | None = None,
    prior_lifecycle_path: str | None = None,
) -> dict:
    """Build one deterministic lifecycle observation.

    Direct callers receive the same fail-closed structural checks as the
    persisted path.  The writer/loader additionally rebuilds the Candidate
    Validity source from its retained Dynamic Clock report.
    """
    source_path = _safe_relative_path(
        source_observation_path, prefix=SOURCE_DIRECTORY,
        field="SOURCE_VALIDITY_OBSERVATION_PATH",
    )
    sample_qualification = _source_sample_qualification(source_observation)
    evaluated_at = _evaluation_at(source_observation)
    current = _candidate_rows(source_observation)

    chain_advancing = sample_qualification == NATURAL_SAMPLE
    if not chain_advancing and prior_lifecycle is not None:
        raise CandidateLifecycleObservationError("DIAGNOSTIC_SAMPLE_CANNOT_CONSUME_CHAIN_STATE")
    prior_rows = _validate_prior(prior_lifecycle, evaluated_at) if chain_advancing else {}
    baseline = chain_advancing and prior_lifecycle is None

    if prior_lifecycle is None:
        if prior_lifecycle_path is not None:
            raise CandidateLifecycleObservationError("PRIOR_PATH_WITHOUT_PRIOR_DOCUMENT")
        prior_reference = None
    else:
        prior_path = _safe_relative_path(
            prior_lifecycle_path, prefix=OUTPUT_DIRECTORY,
            field="PRIOR_LIFECYCLE_PATH",
        )
        prior_reference = {
            "path": prior_path,
            "lifecycle_observation_sha256": prior_lifecycle.get(
                "lifecycle_observation_sha256"
            ),
            "operational_evaluated_at_utc": prior_lifecycle.get(
                "operational_evaluated_at_utc"
            ),
        }

    state_records = []
    for key in sorted(set(current) | set(prior_rows)):
        if key in current:
            state_records.append(
                _active_row(current[key], prior_rows.get(key), evaluated_at, baseline=baseline)
            )
        else:
            state_records.append(_absent_row(prior_rows[key], evaluated_at))

    current_invariant = source_observation["source_dynamic_clock"].get(
        "evaluation_invariant_report_sha256"
    )
    if not isinstance(current_invariant, str) or len(current_invariant) != 64:
        raise CandidateLifecycleObservationError("SOURCE_EVALUATION_INVARIANT_HASH_INVALID")
    prior_invariant = None if prior_lifecycle is None else prior_lifecycle[
        "source_candidate_validity"
    ]["evaluation_invariant_report_sha256"]
    if prior_invariant is None:
        evidence_basis_status = "BASELINE_EVIDENCE_BASIS"
        distinct_from_previous = None
    elif prior_invariant == current_invariant:
        evidence_basis_status = "DUPLICATE_EVIDENCE_BASIS_EVALUATION_ONLY"
        distinct_from_previous = False
    else:
        evidence_basis_status = "DISTINCT_EVIDENCE_BASIS"
        distinct_from_previous = True

    document = {
        "contract_version": CONTRACT_VERSION,
        "wbs_item": "P8-12 Forward-only Candidate Lifecycle Shadow Observation",
        "observation_date": source_observation.get("observation_date"),
        "operational_evaluated_at_utc": evaluated_at,
        "sample_qualification": sample_qualification,
        "chain_qualification": _chain_qualification(sample_qualification),
        "chain_advancing": chain_advancing,
        "baseline_status": (
            "BASELINE_ESTABLISHED_NO_HISTORICAL_BACKFILL"
            if baseline else "NOT_A_BASELINE"
        ),
        "source_candidate_validity": {
            "path": source_path,
            "observation_sha256": source_observation.get("observation_sha256"),
            "evaluation_invariant_report_sha256": current_invariant,
            "format": SOURCE_FORMAT,
        },
        "prior_lifecycle": prior_reference,
        "evidence_basis_status": evidence_basis_status,
        "distinct_evidence_basis_from_previous": distinct_from_previous,
        "state_counts": {
            "total": len(state_records),
            "active": sum(r["state"] == "ACTIVE" for r in state_records),
            "absent_observed": sum(r["state"] == "ABSENT_OBSERVED" for r in state_records),
        },
        "state_records": state_records,
        "permitted_use": [
            "FORWARD_ATLAS_FIRST_OBSERVATION_DIAGNOSTIC",
            "FORWARD_CHANGE_ABSENCE_REAPPEARANCE_DIAGNOSTIC",
        ],
        "prohibited_use": [
            "HISTORICAL_TRIGGER_TIMESTAMP_BACKFILL",
            "CANDIDATE_FRESHNESS_CLASSIFICATION",
            "RISK_CAPACITY_UNLOCK",
            "P8_13_ENTRY_PROPOSAL",
            "POSITION_SIZE_SELECTION_ORDER_OR_TRADING_AUTHORITY",
        ],
        "downstream_locks": copy.deepcopy(LOCKS),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    document["lifecycle_observation_sha256"] = payload_sha256(document)
    return document


def validate_lifecycle_observation(
    document: dict,
    source_observation: dict,
    *,
    source_observation_path: str,
    prior_lifecycle: dict | None = None,
    prior_lifecycle_path: str | None = None,
) -> dict:
    expected = build_lifecycle_observation(
        source_observation,
        source_observation_path=source_observation_path,
        prior_lifecycle=prior_lifecycle,
        prior_lifecycle_path=prior_lifecycle_path,
    )
    if document != expected:
        raise CandidateLifecycleObservationError("LIFECYCLE_SEMANTIC_TAMPER_OR_DRIFT")
    return copy.deepcopy(document)


def _resolved_under(root: Path, relative: str, *, field: str) -> Path:
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise CandidateLifecycleObservationError(f"{field}_ESCAPES_ROOT") from exc
    return target


def load_and_validate_lifecycle_observation(
    lifecycle_path: Path,
    *,
    dynamic_clock_root: Path | None = None,
    _seen: set[Path] | None = None,
) -> dict:
    """Recursively rebuild a persisted lifecycle record from retained inputs."""
    lifecycle_path = lifecycle_path.resolve()
    if _seen is None:
        _seen = set()
    if lifecycle_path in _seen:
        raise CandidateLifecycleObservationError("LIFECYCLE_CHAIN_CYCLE")
    _seen.add(lifecycle_path)
    try:
        raw = lifecycle_path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
        if raw != _canonical_bytes(document):
            raise CandidateLifecycleObservationError("LIFECYCLE_BYTES_NOT_CANONICAL")
        digest = document.get("lifecycle_observation_sha256")
        if lifecycle_path.name != f"lifecycle-{digest}.json":
            raise CandidateLifecycleObservationError("LIFECYCLE_FILENAME_HASH_MISMATCH")
        if lifecycle_path.parent.name != document.get("observation_date"):
            raise CandidateLifecycleObservationError("LIFECYCLE_DATE_DIRECTORY_MISMATCH")

        if dynamic_clock_root is None:
            dynamic_clock_root = lifecycle_path.parents[2]
        source_meta = document.get("source_candidate_validity")
        if not isinstance(source_meta, dict):
            raise CandidateLifecycleObservationError("SOURCE_VALIDITY_METADATA_INVALID")
        source_rel = _safe_relative_path(
            source_meta.get("path"), prefix=SOURCE_DIRECTORY,
            field="SOURCE_VALIDITY_OBSERVATION_PATH",
        )
        source_path = _resolved_under(
            dynamic_clock_root, source_rel, field="SOURCE_VALIDITY_OBSERVATION_PATH"
        )
        source_probe = json.loads(source_path.read_text(encoding="utf-8"))
        trigger_kind = source_probe.get("source_run", {}).get("trigger_kind")
        source_observation = load_and_validate_observation(
            source_path, dynamic_clock_root, trigger_kind=trigger_kind
        )

        prior_meta = document.get("prior_lifecycle")
        prior = None
        prior_rel = None
        if prior_meta is not None:
            if not isinstance(prior_meta, dict):
                raise CandidateLifecycleObservationError("PRIOR_LIFECYCLE_METADATA_INVALID")
            prior_rel = _safe_relative_path(
                prior_meta.get("path"), prefix=OUTPUT_DIRECTORY,
                field="PRIOR_LIFECYCLE_PATH",
            )
            prior_path = _resolved_under(
                dynamic_clock_root, prior_rel, field="PRIOR_LIFECYCLE_PATH"
            )
            prior = load_and_validate_lifecycle_observation(
                prior_path, dynamic_clock_root=dynamic_clock_root, _seen=_seen
            )
            if prior_meta != {
                "path": prior_rel,
                "lifecycle_observation_sha256": prior["lifecycle_observation_sha256"],
                "operational_evaluated_at_utc": prior["operational_evaluated_at_utc"],
            }:
                raise CandidateLifecycleObservationError("PRIOR_LIFECYCLE_METADATA_MISMATCH")

        return validate_lifecycle_observation(
            document,
            source_observation,
            source_observation_path=source_rel,
            prior_lifecycle=prior,
            prior_lifecycle_path=prior_rel,
        )
    finally:
        _seen.remove(lifecycle_path)


def _existing_valid_lifecycle_paths(output_root: Path, dynamic_clock_root: Path) -> list[tuple[Path, dict]]:
    validated = []
    if not output_root.exists():
        return validated
    for path in sorted(output_root.glob("*/lifecycle-*.json")):
        validated.append((path, load_and_validate_lifecycle_observation(
            path, dynamic_clock_root=dynamic_clock_root
        )))
    return validated


def write_lifecycle_observation(
    source_observation_path: Path,
    *,
    dynamic_clock_root: Path,
    output_root: Path,
    trigger_kind: str,
) -> Path:
    """Validate the v4 source, select the prior natural chain tip, persist."""
    dynamic_clock_root = dynamic_clock_root.resolve()
    if output_root.resolve() != (dynamic_clock_root / OUTPUT_DIRECTORY).resolve():
        raise CandidateLifecycleObservationError("LIFECYCLE_OUTPUT_ROOT_MISROUTED")
    source_observation_path = source_observation_path.resolve()
    source_rel = source_observation_path.relative_to(dynamic_clock_root).as_posix()
    _safe_relative_path(
        source_rel, prefix=SOURCE_DIRECTORY, field="SOURCE_VALIDITY_OBSERVATION_PATH"
    )
    source_observation = load_and_validate_observation(
        source_observation_path, dynamic_clock_root, trigger_kind=trigger_kind
    )
    if source_observation.get("contract_version") != VALIDITY_CONTRACT_VERSION:
        raise CandidateLifecycleObservationError("SOURCE_VALIDITY_CONTRACT_MUST_BE_V4")

    existing = _existing_valid_lifecycle_paths(output_root, dynamic_clock_root)
    source_sha = source_observation["observation_sha256"]
    for path, document in existing:
        if document["source_candidate_validity"]["observation_sha256"] == source_sha:
            return path

    qualification = _source_sample_qualification(source_observation)
    prior_path = None
    prior = None
    if qualification == NATURAL_SAMPLE:
        natural = [
            (path, doc) for path, doc in existing
            if doc.get("chain_qualification") == CHAIN_NATURAL and doc.get("chain_advancing")
        ]
        if natural:
            prior_path, prior = max(
                natural, key=lambda item: _utc(
                    item[1]["operational_evaluated_at_utc"], field="PRIOR_OPERATIONAL_EVALUATED_AT"
                )
            )

    prior_rel = None if prior_path is None else prior_path.resolve().relative_to(
        dynamic_clock_root
    ).as_posix()
    document = build_lifecycle_observation(
        source_observation,
        source_observation_path=source_rel,
        prior_lifecycle=prior,
        prior_lifecycle_path=prior_rel,
    )
    target_dir = output_root / document["observation_date"]
    target = target_dir / f"lifecycle-{document['lifecycle_observation_sha256']}.json"
    payload = _canonical_bytes(document)
    target_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != payload:
            raise CandidateLifecycleObservationError("APPEND_ONLY_LIFECYCLE_TAMPERED")
        return target
    target.write_bytes(payload)
    return target
