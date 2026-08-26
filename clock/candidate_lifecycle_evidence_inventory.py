#!/usr/bin/env python3
"""P8-12 empirical Candidate Lifecycle evidence inventory.

This module aggregates only the forward, append-only lifecycle observations
that Atlas can independently rebuild from retained Dynamic Clock evidence.
It deliberately reports observation endpoints and transition counts, not a
candidate-validity duration.  An unchanged subject at two observations does
not prove continuous presence between them, and no sample count or time
window in this file is policy authority.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock.candidate_lifecycle_observation import (
    BASELINE_PREEXISTING,
    CHAIN_NATURAL,
    CONTRACT_VERSION as LIFECYCLE_CONTRACT_VERSION,
    NATURAL_SAMPLE,
    load_and_validate_lifecycle_observation,
)
from clock.candidate_validity_observation import load_and_validate_observation
from clock.review_candidate import AUTHORITY_ALL_FALSE
from replay.opportunity_trigger import canonical_json, payload_sha256


CONTRACT_VERSION = "candidate_lifecycle_evidence_inventory/1"
DEFAULT_DYNAMIC_ROOT = Path("evidence/operational/dynamic_clock")
DEFAULT_LIFECYCLE_ROOT = DEFAULT_DYNAMIC_ROOT / "candidate_lifecycle_observations"
DEFAULT_OUTPUT = DEFAULT_DYNAMIC_ROOT / "candidate_lifecycle_evidence_inventory.json"

EVENTS = (
    "BASELINE_PREEXISTING_FIRST_SEEN_NOT_COMPUTABLE",
    "FIRST_SEEN_EXACT_FORWARD_ONLY",
    "CONTINUING_UNCHANGED",
    "CONTINUING_CHANGED",
    "FIRST_ABSENCE_OBSERVED_FORWARD_ONLY",
    "STILL_ABSENT",
    "REAPPEARED_OBSERVED_FORWARD_ONLY",
)


class CandidateLifecycleEvidenceInventoryError(ValueError):
    pass


def _utc(value: object, *, field: str) -> dt.datetime:
    if not isinstance(value, str):
        raise CandidateLifecycleEvidenceInventoryError(f"{field}_MUST_BE_TIMESTAMP")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CandidateLifecycleEvidenceInventoryError(f"{field}_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CandidateLifecycleEvidenceInventoryError(f"{field}_TIMEZONE_REQUIRED")
    parsed = parsed.astimezone(dt.timezone.utc)
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CandidateLifecycleEvidenceInventoryError(f"{field}_MUST_BE_CANONICAL_UTC_SECONDS")
    return parsed


def _relative_under(path: Path, root: Path, *, field: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise CandidateLifecycleEvidenceInventoryError(f"{field}_OUTSIDE_DYNAMIC_ROOT") from exc


def _source_candidate_map(document: dict, dynamic_root: Path) -> dict[str, dict]:
    source = document.get("source_candidate_validity")
    if not isinstance(source, dict):
        raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_METADATA_INVALID")
    relative = source.get("path")
    if not isinstance(relative, str):
        raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_PATH_INVALID")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_PATH_UNSAFE")
    source_path = (dynamic_root / relative).resolve()
    _relative_under(source_path, dynamic_root, field="SOURCE_VALIDITY")
    if not source_path.is_file():
        raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_MISSING")
    try:
        probe = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_JSON_INVALID") from exc
    trigger_kind = probe.get("source_run", {}).get("trigger_kind")
    try:
        observation = load_and_validate_observation(
            source_path, dynamic_root, trigger_kind=trigger_kind
        )
    except Exception as exc:
        raise CandidateLifecycleEvidenceInventoryError(
            f"SOURCE_VALIDITY_REVALIDATION_FAILED:{type(exc).__name__}"
        ) from exc
    if observation.get("observation_sha256") != source.get("observation_sha256"):
        raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_HASH_MISMATCH")

    result: dict[str, dict] = {}
    for market, market_block in sorted(observation.get("by_market", {}).items()):
        candidates = market_block.get("candidates") if isinstance(market_block, dict) else None
        if not isinstance(candidates, list):
            raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_CANDIDATES_INVALID")
        for candidate in candidates:
            subject = candidate.get("subject") if isinstance(candidate, dict) else None
            if not isinstance(subject, str) or candidate.get("market") != market:
                raise CandidateLifecycleEvidenceInventoryError("SOURCE_VALIDITY_CANDIDATE_INVALID")
            triggers = candidate.get("trigger_types")
            if (
                not isinstance(triggers, list)
                or not triggers
                or any(not isinstance(item, str) or not item for item in triggers)
                or len(triggers) != len(set(triggers))
            ):
                raise CandidateLifecycleEvidenceInventoryError(
                    "SOURCE_VALIDITY_TRIGGER_TYPES_INVALID"
                )
            stable_key = payload_sha256({"market": market, "subject": subject})
            if stable_key in result:
                raise CandidateLifecycleEvidenceInventoryError(
                    "SOURCE_VALIDITY_STABLE_KEY_DUPLICATE"
                )
            result[stable_key] = {
                "market": market,
                "subject": subject,
                "trigger_types": sorted(triggers),
            }
    return result


def _validated_artifacts(
    lifecycle_root: Path, dynamic_root: Path
) -> list[tuple[Path, dict, dict[str, dict]]]:
    paths = sorted(lifecycle_root.glob("*/lifecycle-*.json"))
    if not paths:
        raise CandidateLifecycleEvidenceInventoryError("NO_LIFECYCLE_ARTIFACTS")
    validated = []
    for path in paths:
        try:
            document = load_and_validate_lifecycle_observation(
                path, dynamic_clock_root=dynamic_root
            )
        except Exception as exc:
            raise CandidateLifecycleEvidenceInventoryError(
                f"LIFECYCLE_REVALIDATION_FAILED:{path.name}:{type(exc).__name__}"
            ) from exc
        if document.get("contract_version") != LIFECYCLE_CONTRACT_VERSION:
            raise CandidateLifecycleEvidenceInventoryError("LIFECYCLE_CONTRACT_UNEXPECTED")
        validated.append((path, document, _source_candidate_map(document, dynamic_root)))
    return validated


def _natural_chain(
    artifacts: list[tuple[Path, dict, dict[str, dict]]], dynamic_root: Path
) -> list[tuple[Path, dict, dict[str, dict]]]:
    natural = [
        item for item in artifacts
        if item[1].get("sample_qualification") == NATURAL_SAMPLE
        and item[1].get("chain_qualification") == CHAIN_NATURAL
        and item[1].get("chain_advancing") is True
    ]
    if not natural:
        return []
    by_sha = {item[1].get("lifecycle_observation_sha256"): item for item in natural}
    if len(by_sha) != len(natural) or None in by_sha:
        raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_SHA_DUPLICATE_OR_MISSING")

    baselines = [item for item in natural if item[1].get("prior_lifecycle") is None]
    if len(baselines) != 1:
        raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_BASELINE_COUNT_INVALID")
    children: dict[str, list[str]] = defaultdict(list)
    for path, document, _ in natural:
        prior = document.get("prior_lifecycle")
        if prior is None:
            continue
        if not isinstance(prior, dict):
            raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_PRIOR_INVALID")
        parent_sha = prior.get("lifecycle_observation_sha256")
        parent = by_sha.get(parent_sha)
        if parent is None:
            raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_PARENT_MISSING")
        expected_path = _relative_under(parent[0], dynamic_root, field="NATURAL_CHAIN_PARENT")
        if prior.get("path") != expected_path:
            raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_PARENT_PATH_MISMATCH")
        children[parent_sha].append(document["lifecycle_observation_sha256"])
    if any(len(values) != 1 for values in children.values()):
        raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_FORK")

    ordered = []
    current = baselines[0]
    seen: set[str] = set()
    while True:
        current_sha = current[1]["lifecycle_observation_sha256"]
        if current_sha in seen:
            raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_CYCLE")
        seen.add(current_sha)
        ordered.append(current)
        descendants = children.get(current_sha, [])
        if not descendants:
            break
        current = by_sha[descendants[0]]
    if len(ordered) != len(natural):
        raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_DISCONNECTED")
    instants = [
        _utc(item[1].get("operational_evaluated_at_utc"), field="NATURAL_EVALUATED_AT")
        for item in ordered
    ]
    if instants != sorted(instants) or len(instants) != len(set(instants)):
        raise CandidateLifecycleEvidenceInventoryError("NATURAL_CHAIN_TIME_ORDER_INVALID")
    return ordered


def _zero_event_counts() -> dict[str, int]:
    return {event: 0 for event in EVENTS}


def _build_from_validated(
    artifacts: list[tuple[Path, dict, dict[str, dict]]], dynamic_root: Path
) -> dict:
    chain = _natural_chain(artifacts, dynamic_root)
    non_natural = [item for item in artifacts if item not in chain]
    qualification_counts = Counter(
        item[1].get("sample_qualification") for item in artifacts
    )

    market_rows: dict[str, dict] = defaultdict(lambda: {
        "baseline_active_candidate_count": 0,
        "transition_record_count": 0,
        "distinct_evidence_transition_record_count": 0,
        "event_counts": _zero_event_counts(),
        "distinct_evidence_event_counts": _zero_event_counts(),
    })
    trigger_rows: dict[str, dict] = defaultdict(lambda: {
        "baseline_active_candidate_count": 0,
        "transition_record_count": 0,
        "distinct_evidence_transition_record_count": 0,
        "event_counts": _zero_event_counts(),
        "distinct_evidence_event_counts": _zero_event_counts(),
    })
    all_event_counts = _zero_event_counts()
    distinct_event_counts = _zero_event_counts()
    transitions = []
    last_known_triggers: dict[str, list[str]] = {}

    if chain:
        baseline_path, baseline, baseline_sources = chain[0]
        baseline_records = baseline.get("state_records", [])
        for record in baseline_records:
            key = record.get("stable_candidate_key")
            source = baseline_sources.get(key)
            if record.get("lifecycle_event") != BASELINE_PREEXISTING or source is None:
                raise CandidateLifecycleEvidenceInventoryError("NATURAL_BASELINE_RECORD_INVALID")
            market_rows[source["market"]]["baseline_active_candidate_count"] += 1
            for trigger in source["trigger_types"]:
                trigger_rows[trigger]["baseline_active_candidate_count"] += 1
            last_known_triggers[key] = source["trigger_types"]

        previous = baseline
        for path, document, current_sources in chain[1:]:
            prior_at = _utc(
                previous.get("operational_evaluated_at_utc"), field="PRIOR_EVALUATED_AT"
            )
            current_at = _utc(
                document.get("operational_evaluated_at_utc"), field="CURRENT_EVALUATED_AT"
            )
            distinct = document.get("distinct_evidence_basis_from_previous")
            if distinct not in (True, False):
                raise CandidateLifecycleEvidenceInventoryError(
                    "NATURAL_TRANSITION_DISTINCTNESS_INVALID"
                )
            transition_events = _zero_event_counts()
            for record in document.get("state_records", []):
                key = record.get("stable_candidate_key")
                event = record.get("lifecycle_event")
                market = record.get("market")
                if event not in EVENTS or not isinstance(market, str):
                    raise CandidateLifecycleEvidenceInventoryError(
                        "NATURAL_TRANSITION_RECORD_INVALID"
                    )
                source = current_sources.get(key)
                triggers = source["trigger_types"] if source is not None else last_known_triggers.get(key)
                if not triggers:
                    raise CandidateLifecycleEvidenceInventoryError(
                        "NATURAL_TRANSITION_TRIGGER_LINEAGE_MISSING"
                    )
                transition_events[event] += 1
                all_event_counts[event] += 1
                market_rows[market]["transition_record_count"] += 1
                market_rows[market]["event_counts"][event] += 1
                for trigger in triggers:
                    trigger_rows[trigger]["transition_record_count"] += 1
                    trigger_rows[trigger]["event_counts"][event] += 1
                if distinct:
                    distinct_event_counts[event] += 1
                    market_rows[market]["distinct_evidence_transition_record_count"] += 1
                    market_rows[market]["distinct_evidence_event_counts"][event] += 1
                    for trigger in triggers:
                        trigger_rows[trigger]["distinct_evidence_transition_record_count"] += 1
                        trigger_rows[trigger]["distinct_evidence_event_counts"][event] += 1
                if source is not None:
                    last_known_triggers[key] = triggers
            transitions.append({
                "path": _relative_under(path, dynamic_root, field="NATURAL_TRANSITION"),
                "lifecycle_observation_sha256": document["lifecycle_observation_sha256"],
                "prior_operational_evaluated_at_utc": previous["operational_evaluated_at_utc"],
                "operational_evaluated_at_utc": document["operational_evaluated_at_utc"],
                "observation_gap_seconds": int((current_at - prior_at).total_seconds()),
                "evidence_basis_status": document.get("evidence_basis_status"),
                "distinct_evidence_basis_from_previous": distinct,
                "state_counts": copy.deepcopy(document.get("state_counts")),
                "event_counts": transition_events,
            })
            previous = document

    baseline_meta = None
    tip_meta = None
    observation_span_seconds = None
    if chain:
        baseline_path, baseline, _ = chain[0]
        tip_path, tip, _ = chain[-1]
        baseline_meta = {
            "path": _relative_under(baseline_path, dynamic_root, field="NATURAL_BASELINE"),
            "lifecycle_observation_sha256": baseline["lifecycle_observation_sha256"],
            "operational_evaluated_at_utc": baseline["operational_evaluated_at_utc"],
            "active_candidate_count": baseline["state_counts"]["active"],
        }
        tip_meta = {
            "path": _relative_under(tip_path, dynamic_root, field="NATURAL_TIP"),
            "lifecycle_observation_sha256": tip["lifecycle_observation_sha256"],
            "operational_evaluated_at_utc": tip["operational_evaluated_at_utc"],
            "state_counts": copy.deepcopy(tip["state_counts"]),
        }
        observation_span_seconds = int((
            _utc(tip["operational_evaluated_at_utc"], field="TIP_EVALUATED_AT")
            - _utc(baseline["operational_evaluated_at_utc"], field="BASELINE_EVALUATED_AT")
        ).total_seconds())

    if not chain:
        chain_status = "NO_NATURAL_FORWARD_CHAIN"
    elif len(chain) == 1:
        chain_status = "NATURAL_BASELINE_ONLY_NO_TRANSITION"
    elif any(item["distinct_evidence_basis_from_previous"] for item in transitions):
        chain_status = "NATURAL_FORWARD_CHAIN_WITH_DISTINCT_EVIDENCE_TRANSITION"
    else:
        chain_status = "NATURAL_FORWARD_CHAIN_EVALUATION_ONLY_DUPLICATES"

    document = {
        "contract_version": CONTRACT_VERSION,
        "wbs_item": "P8-12 Candidate Lifecycle Evidence Inventory",
        "evidence_status": chain_status,
        "artifact_count": len(artifacts),
        "artifact_qualification_counts": {
            "NATURAL_OPERATIONAL_SAMPLE": qualification_counts.get(
                "NATURAL_OPERATIONAL_SAMPLE", 0
            ),
            "MANUAL_OPERATIONAL_SAMPLE": qualification_counts.get(
                "MANUAL_OPERATIONAL_SAMPLE", 0
            ),
            "LOCAL_REPRODUCTION_NOT_OPERATIONAL_SAMPLE": qualification_counts.get(
                "LOCAL_REPRODUCTION_NOT_OPERATIONAL_SAMPLE", 0
            ),
        },
        "natural_forward_chain": {
            "artifact_count": len(chain),
            "transition_count": max(0, len(chain) - 1),
            "distinct_evidence_transition_count": sum(
                item["distinct_evidence_basis_from_previous"] is True
                for item in transitions
            ),
            "evaluation_only_duplicate_transition_count": sum(
                item["distinct_evidence_basis_from_previous"] is False
                for item in transitions
            ),
            "baseline": baseline_meta,
            "tip": tip_meta,
            "observation_span_seconds": observation_span_seconds,
            "observation_span_interpretation": (
                "ENDPOINT_OBSERVATION_SPAN_NOT_CONTINUOUS_CANDIDATE_LIFETIME"
            ),
            "transition_event_counts": all_event_counts,
            "distinct_evidence_transition_event_counts": distinct_event_counts,
            "transitions": transitions,
        },
        "by_market": [
            {"market": market, **row}
            for market, row in sorted(market_rows.items())
        ],
        "by_trigger_type": [
            {"trigger_type": trigger, **row}
            for trigger, row in sorted(trigger_rows.items())
        ],
        "diagnostic_artifacts_excluded_from_natural_chain": [
            {
                "path": _relative_under(path, dynamic_root, field="DIAGNOSTIC_ARTIFACT"),
                "lifecycle_observation_sha256": document[
                    "lifecycle_observation_sha256"
                ],
                "sample_qualification": document.get("sample_qualification"),
                "operational_evaluated_at_utc": document.get(
                    "operational_evaluated_at_utc"
                ),
            }
            for path, document, _ in sorted(non_natural, key=lambda item: item[0].as_posix())
        ],
        "policy_boundary": {
            "candidate_lifetime_inferred": False,
            "continuous_presence_assumed_between_observations": False,
            "minimum_sample_threshold": None,
            "minimum_sample_authority_status": "UNRATIFIED_NOT_DEFINED",
            "validity_window_days": None,
            "validity_window_selected": False,
            "validity_window_recommendation_status": (
                "NOT_COMPUTABLE_NO_RATIFIED_EVALUATION_RULE"
            ),
            "candidate_freshness_evaluated": False,
            "risk_capacity_opened": False,
            "p8_13_entry_proposal_opened": False,
            "money_action": "NONE",
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    document["inventory_sha256"] = payload_sha256(document)
    return document


def build_inventory(
    lifecycle_root: Path = DEFAULT_LIFECYCLE_ROOT,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> dict:
    dynamic_root = dynamic_root.resolve()
    lifecycle_root = lifecycle_root.resolve()
    expected = (dynamic_root / "candidate_lifecycle_observations").resolve()
    if lifecycle_root != expected:
        raise CandidateLifecycleEvidenceInventoryError("LIFECYCLE_ROOT_MISROUTED")
    artifacts = _validated_artifacts(lifecycle_root, dynamic_root)
    return _build_from_validated(artifacts, dynamic_root)


def validate_inventory(
    inventory: dict,
    lifecycle_root: Path = DEFAULT_LIFECYCLE_ROOT,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> dict:
    if inventory != build_inventory(lifecycle_root, dynamic_root):
        raise CandidateLifecycleEvidenceInventoryError(
            "LIFECYCLE_INVENTORY_SEMANTIC_TAMPER_OR_DRIFT"
        )
    return copy.deepcopy(inventory)


def write_inventory(
    output: Path = DEFAULT_OUTPUT,
    lifecycle_root: Path = DEFAULT_LIFECYCLE_ROOT,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> Path:
    inventory = build_inventory(lifecycle_root, dynamic_root)
    payload = (canonical_json(inventory) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() == payload:
        return output
    output.write_bytes(payload)
    return output


if __name__ == "__main__":
    print(write_inventory())
