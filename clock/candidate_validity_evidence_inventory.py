"""P8-12 retained Candidate Validity evidence inventory.

Counts independently revalidatable natural and manual observations without
inventing a minimum sample threshold or a validity window.  Legacy and
rejected contract artifacts remain visible but cannot qualify as evidence.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock.candidate_validity_observation import (
    AUTHORITY_ALL_FALSE,
    TRIGGER_MANUAL_WORKFLOW_DISPATCH,
    TRIGGER_UPSTREAM_WORKFLOW_RUN,
    _canonical_payload_bytes,
    _evaluation_invariant_report_sha256,
    load_and_validate_observation,
)
from replay.opportunity_trigger import canonical_json, payload_sha256


CONTRACT_VERSION = "candidate_validity_evidence_inventory/1"
REVALIDATABLE_CONTRACTS = frozenset({
    "candidate_validity_shadow_observation/2",
    "candidate_validity_shadow_observation/4",
})
LEGACY_CONTRACT = "candidate_validity_shadow_observation/1"
REJECTED_CONTRACT = "candidate_validity_shadow_observation/3"
DEFAULT_DYNAMIC_ROOT = Path("evidence/operational/dynamic_clock")
DEFAULT_OBSERVATION_ROOT = DEFAULT_DYNAMIC_ROOT / "candidate_validity_observations"
DEFAULT_OUTPUT = DEFAULT_DYNAMIC_ROOT / "candidate_validity_evidence_inventory.json"


class CandidateValidityEvidenceInventoryError(ValueError):
    pass


def _verify_container(path: Path) -> dict:
    raw = path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateValidityEvidenceInventoryError("OBSERVATION_JSON_INVALID") from exc
    if raw != _canonical_payload_bytes(document):
        raise CandidateValidityEvidenceInventoryError("OBSERVATION_BYTES_NOT_CANONICAL")
    observation_sha = document.get("observation_sha256")
    if not isinstance(observation_sha, str) or path.name != f"observation-{observation_sha}.json":
        raise CandidateValidityEvidenceInventoryError("OBSERVATION_FILENAME_HASH_MISMATCH")
    unsigned = {key: value for key, value in document.items() if key != "observation_sha256"}
    if payload_sha256(unsigned) != observation_sha:
        raise CandidateValidityEvidenceInventoryError("OBSERVATION_EMBEDDED_HASH_MISMATCH")
    if path.parent.name != document.get("observation_date"):
        raise CandidateValidityEvidenceInventoryError("OBSERVATION_DATE_DIRECTORY_MISMATCH")
    return document


def _retained_source(dynamic_root: Path, observation: dict) -> dict:
    retained = observation.get("source_dynamic_clock", {}).get("retained_report", {})
    relative = retained.get("path")
    if not isinstance(relative, str):
        raise CandidateValidityEvidenceInventoryError("RETAINED_SOURCE_PATH_MISSING")
    path = dynamic_root / relative
    if not path.is_file():
        raise CandidateValidityEvidenceInventoryError("RETAINED_SOURCE_REPORT_MISSING")
    raw = path.read_bytes()
    report = json.loads(raw.decode("utf-8"))
    if raw != _canonical_payload_bytes(report):
        raise CandidateValidityEvidenceInventoryError("RETAINED_SOURCE_BYTES_NOT_CANONICAL")
    return report


def build_inventory(
    observation_root: Path = DEFAULT_OBSERVATION_ROOT,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> dict:
    paths = sorted(observation_root.glob("*/observation-*.json"))
    if not paths:
        raise CandidateValidityEvidenceInventoryError("NO_OBSERVATION_ARTIFACTS")

    artifacts = []
    qualification_counts = {
        "NATURAL_OPERATIONAL_SAMPLE": 0,
        "MANUAL_OPERATIONAL_SAMPLE": 0,
        "LOCAL_REPRODUCTION_NOT_OPERATIONAL_SAMPLE": 0,
    }
    natural_dates: set[str] = set()
    manual_dates: set[str] = set()
    invariant_hashes: set[str] = set()
    natural_invariant_hashes: set[str] = set()
    manual_invariant_hashes: set[str] = set()
    trigger_coverage: dict[str, dict[str, int]] = {}
    counts = {"REVALIDATABLE": 0, "LEGACY_NON_REVALIDATABLE": 0, "REJECTED_NOT_A_SAMPLE": 0}

    for path in paths:
        observation = _verify_container(path)
        contract = observation.get("contract_version")
        qualification = observation.get("source_run", {}).get("sample_qualification")
        trigger_kind = observation.get("source_run", {}).get("trigger_kind")
        row = {
            "path": path.as_posix(),
            "observation_sha256": observation["observation_sha256"],
            "observation_date": observation["observation_date"],
            "contract_version": contract,
            "sample_qualification": qualification,
            "trigger_kind": trigger_kind,
            "candidate_count": observation.get("candidate_count"),
        }
        if contract in REVALIDATABLE_CONTRACTS:
            try:
                load_and_validate_observation(path, dynamic_root, trigger_kind=trigger_kind)
            except Exception as exc:
                raise CandidateValidityEvidenceInventoryError(
                    f"REVALIDATABLE_OBSERVATION_FAILED:{path.name}:{type(exc).__name__}"
                ) from exc
            report = _retained_source(dynamic_root, observation)
            invariant = _evaluation_invariant_report_sha256(report)
            declared = observation["source_dynamic_clock"].get("evaluation_invariant_report_sha256")
            if declared is not None and declared != invariant:
                raise CandidateValidityEvidenceInventoryError("EVALUATION_INVARIANT_HASH_MISMATCH")
            row.update({
                "evidence_status": "INDEPENDENTLY_REVALIDATABLE",
                "source_report_sha256": observation["source_dynamic_clock"]["report_sha256"],
                "evaluation_invariant_report_sha256": invariant,
            })
            counts["REVALIDATABLE"] += 1
            if qualification not in qualification_counts:
                raise CandidateValidityEvidenceInventoryError("SAMPLE_QUALIFICATION_UNKNOWN")
            qualification_counts[qualification] += 1
            invariant_hashes.add(invariant)
            if qualification == "NATURAL_OPERATIONAL_SAMPLE":
                natural_dates.add(observation["observation_date"])
                natural_invariant_hashes.add(invariant)
            elif qualification == "MANUAL_OPERATIONAL_SAMPLE":
                manual_dates.add(observation["observation_date"])
                manual_invariant_hashes.add(invariant)
            for item in observation.get("trigger_type_observations", []):
                trigger_type = item["trigger_type"]
                entry = trigger_coverage.setdefault(trigger_type, {
                    "natural_samples_with_candidate": 0,
                    "manual_samples_with_candidate": 0,
                })
                if item["candidate_observation_count"] > 0:
                    if qualification == "NATURAL_OPERATIONAL_SAMPLE":
                        entry["natural_samples_with_candidate"] += 1
                    elif qualification == "MANUAL_OPERATIONAL_SAMPLE":
                        entry["manual_samples_with_candidate"] += 1
        elif contract == LEGACY_CONTRACT:
            row.update({"evidence_status": "LEGACY_NON_REVALIDATABLE", "reason": "NO_RETAINED_SOURCE_CONTRACT"})
            counts["LEGACY_NON_REVALIDATABLE"] += 1
        elif contract == REJECTED_CONTRACT:
            row.update({"evidence_status": "REJECTED_NOT_A_SAMPLE", "reason": "CONTRACT_REJECTED_BY_CURRENT_VALIDATOR"})
            counts["REJECTED_NOT_A_SAMPLE"] += 1
        else:
            raise CandidateValidityEvidenceInventoryError("OBSERVATION_CONTRACT_UNKNOWN")
        artifacts.append(row)

    inventory = {
        "contract_version": CONTRACT_VERSION,
        "wbs_item": "P8-12 Candidate Validity Evidence Inventory",
        "as_of_observation_date": max(row["observation_date"] for row in artifacts),
        "artifact_count": len(artifacts),
        "evidence_status_counts": counts,
        "revalidatable_artifact_qualification_counts": qualification_counts,
        "distinct_revalidatable_evaluation_invariant_count": len(invariant_hashes),
        "natural_operational_sample": {
            "artifact_count": qualification_counts["NATURAL_OPERATIONAL_SAMPLE"],
            "distinct_evidence_sample_count": len(natural_invariant_hashes),
            "distinct_observation_date_count": len(natural_dates),
            "distinct_evaluation_invariant_count": len(natural_invariant_hashes),
        },
        "manual_operational_sample": {
            "artifact_count": qualification_counts["MANUAL_OPERATIONAL_SAMPLE"],
            "distinct_evidence_sample_count": len(manual_invariant_hashes),
            "distinct_observation_date_count": len(manual_dates),
        },
        "trigger_family_coverage": [
            {"trigger_type": trigger_type, **trigger_coverage[trigger_type]}
            for trigger_type in sorted(trigger_coverage)
        ],
        "artifacts": artifacts,
        "policy_boundary": {
            "minimum_required_natural_samples": None,
            "minimum_sample_authority_status": "UNRATIFIED_NOT_DEFINED",
            "validity_window_selected": False,
            "candidate_freshness_evaluated": False,
            "risk_capacity_opened": False,
            "p8_13_entry_proposal_opened": False,
            "money_action": "NONE",
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    inventory["inventory_sha256"] = payload_sha256(inventory)
    return inventory


def validate_inventory(
    inventory: dict,
    observation_root: Path = DEFAULT_OBSERVATION_ROOT,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> dict:
    if inventory != build_inventory(observation_root, dynamic_root):
        raise CandidateValidityEvidenceInventoryError("INVENTORY_SEMANTIC_TAMPER_OR_DRIFT")
    return inventory


def write_inventory(
    output: Path = DEFAULT_OUTPUT,
    observation_root: Path = DEFAULT_OBSERVATION_ROOT,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> Path:
    inventory = build_inventory(observation_root, dynamic_root)
    payload = (canonical_json(inventory) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() == payload:
        return output
    output.write_bytes(payload)
    return output


if __name__ == "__main__":
    target = write_inventory()
    print(target)
