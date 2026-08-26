"""P8-10 operational Price State linkage evidence inventory.

Uses only independently revalidatable Candidate Validity observations and
their exact retained Dynamic Clock reports. It measures live linkage; it does
not create Reflection Status authority, a validity window, or an action.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock.candidate_validity_evidence_inventory import (  # noqa: E402
    DEFAULT_DYNAMIC_ROOT,
    DEFAULT_OBSERVATION_ROOT,
    build_inventory as build_validity_inventory,
)
from clock.candidate_validity_observation import (  # noqa: E402
    AUTHORITY_ALL_FALSE,
    _evaluation_invariant_report_sha256,
    load_and_validate_observation,
)
from replay.opportunity_trigger import canonical_json, payload_sha256  # noqa: E402


CONTRACT_VERSION = "price_state_operational_evidence_inventory/1"
DEFAULT_OUTPUT = DEFAULT_DYNAMIC_ROOT / "price_state_operational_evidence_inventory.json"


class PriceStateOperationalEvidenceInventoryError(ValueError):
    pass


def _inc(bucket: dict[str, int], value: object) -> None:
    key = str(value) if value is not None else "NOT_AVAILABLE"
    bucket[key] = bucket.get(key, 0) + 1


def _sample(artifact: dict, dynamic_root: Path) -> dict:
    observation_path = Path(artifact["path"])
    observation = load_and_validate_observation(
        observation_path,
        dynamic_root,
        trigger_kind=artifact["trigger_kind"],
    )
    retained = observation["source_dynamic_clock"]["retained_report"]["path"]
    report_path = dynamic_root / retained
    report = json.loads(report_path.read_text(encoding="utf-8"))
    invariant = _evaluation_invariant_report_sha256(report)
    if invariant != artifact["evaluation_invariant_report_sha256"]:
        raise PriceStateOperationalEvidenceInventoryError("SOURCE_INVARIANT_MISMATCH")

    by_market = {}
    total_linked = 0
    total_candidates = 0
    for market in ("BTC", "CRYPTO", "KOREA"):
        counts = {
            "linkage_status_counts": {},
            "price_state_counts": {},
            "reflection_status_counts": {},
            "threshold_basis_counts": {},
        }
        candidates = report["by_market"][market]["review_queue"]
        for candidate in candidates:
            if candidate["authority"] != AUTHORITY_ALL_FALSE:
                raise PriceStateOperationalEvidenceInventoryError("CANDIDATE_AUTHORITY_OPENED")
            price = candidate["price_reflection_status"]
            _inc(counts["linkage_status_counts"], price.get("status"))
            _inc(counts["price_state_counts"], price.get("price_state"))
            _inc(counts["reflection_status_counts"], price.get("reflection_status"))
            _inc(counts["threshold_basis_counts"], price.get("threshold_basis"))
            if price.get("reflection_status", "UNKNOWN") != "UNKNOWN":
                raise PriceStateOperationalEvidenceInventoryError("REFLECTION_STATUS_AUTHORITY_LEAK")
            total_linked += int(price.get("status") == "LINKED")
            total_candidates += 1
        by_market[market] = {"candidate_count": len(candidates), **counts}
    return {
        "sample_id": invariant,
        "sample_qualification": artifact["sample_qualification"],
        "observation_date": artifact["observation_date"],
        "observation_sha256": artifact["observation_sha256"],
        "source_report_sha256": artifact["source_report_sha256"],
        "candidate_count": total_candidates,
        "price_state_linked_candidate_count": total_linked,
        "by_market": by_market,
    }


def build_inventory(
    observation_root: Path = DEFAULT_OBSERVATION_ROOT,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> dict:
    validity = build_validity_inventory(observation_root, dynamic_root)
    samples_by_key: dict[tuple[str, str], dict] = {}
    for artifact in validity["artifacts"]:
        if artifact["evidence_status"] != "INDEPENDENTLY_REVALIDATABLE":
            continue
        sample = _sample(artifact, dynamic_root)
        key = (sample["sample_qualification"], sample["sample_id"])
        existing = samples_by_key.get(key)
        if existing is not None and existing != sample:
            raise PriceStateOperationalEvidenceInventoryError("DUPLICATE_SAMPLE_SEMANTIC_DRIFT")
        samples_by_key[key] = sample
    samples = sorted(samples_by_key.values(), key=lambda row: (row["observation_date"], row["sample_qualification"], row["sample_id"]))
    natural = [row for row in samples if row["sample_qualification"] == "NATURAL_OPERATIONAL_SAMPLE"]
    manual = [row for row in samples if row["sample_qualification"] == "MANUAL_OPERATIONAL_SAMPLE"]
    inventory = {
        "contract_version": CONTRACT_VERSION,
        "wbs_item": "P8-10 Price State Operational Evidence",
        "as_of_observation_date": validity["as_of_observation_date"],
        "source_candidate_validity_inventory_sha256": validity["inventory_sha256"],
        "distinct_sample_count": len(samples),
        "natural_distinct_sample_count": len(natural),
        "manual_distinct_sample_count": len(manual),
        "natural_price_state_linked_candidate_observed": any(row["price_state_linked_candidate_count"] > 0 for row in natural),
        "samples": samples,
        "operational_boundary": {
            "price_state_is_diagnostic_only": True,
            "reflection_status_authority": "ABSENT_STRUCTURALLY_UNKNOWN_ONLY",
            "classification_thresholds_approval_status": "PROVISIONAL",
            "candidate_validity_evaluated": False,
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
        raise PriceStateOperationalEvidenceInventoryError("INVENTORY_SEMANTIC_TAMPER_OR_DRIFT")
    return inventory


def write_inventory(output: Path = DEFAULT_OUTPUT) -> Path:
    inventory = build_inventory()
    payload = canonical_json(inventory) + "\n"
    if not output.exists() or output.read_text(encoding="utf-8") != payload:
        output.write_text(payload, encoding="utf-8")
    return output


if __name__ == "__main__":
    print(write_inventory())
