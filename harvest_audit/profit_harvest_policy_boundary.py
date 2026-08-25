#!/usr/bin/env python3
"""P7-11 policy-design boundary; intentionally cannot emit a harvest action."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

CONTRACT_SCHEMA_VERSION = "profit_harvest_policy_contract/1"
BOUNDARY_SCHEMA_VERSION = "profit_harvest_policy_boundary/1"
AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "harvest_review_authorized": False,
    "reduce_authorized": False,
    "exit_authorized": False,
    "quantity_authorized": False,
    "reallocation_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}
EXPECTED_AXES = {
    "trigger_eligibility", "action_family", "quantity_authority",
    "reallocation_handoff",
}
EXPECTED_OPTIONS = {
    "H1_THESIS_PRESERVING": "PROPOSED_UNRATIFIED",
    "H2_BALANCED_HARVEST_REVIEW": "PROVISIONAL_DESIGN_PREFERENCE_ONLY",
    "H3_RISK_RECOVERY_PRIORITY": "PROPOSED_UNRATIFIED",
}
EXPECTED_POPULATION = {
    "official_kpi_eligible_markets": ["BTC"],
    "diagnostic_only_markets": ["KOREA"],
    "not_computable_markets": ["CRYPTO"],
    "outcome_labels_are_operational_inputs": False,
}
EXPECTED_UPSTREAM_AUTHORITY = {
    "review_only": True,
    "harvest_review_authorized": False,
    "reduce_authorized": False,
    "exit_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class ProfitHarvestPolicyBoundaryError(ValueError):
    pass


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def load_contract(path: Path) -> dict:
    document = json.loads(path.read_text())
    return validate_contract(document)


def validate_contract(document: dict) -> dict:
    if not isinstance(document, dict) or set(document) != {
        "schema_version", "document_status", "approval_status",
        "population_authority", "policy_axes", "design_options", "authority",
    }:
        raise ProfitHarvestPolicyBoundaryError("CONTRACT_FIELDS_INVALID")
    if document["schema_version"] != CONTRACT_SCHEMA_VERSION:
        raise ProfitHarvestPolicyBoundaryError("CONTRACT_SCHEMA_UNSUPPORTED")
    if document["document_status"] != "DESIGN_DRAFT" or document["approval_status"] != "PROPOSED_UNRATIFIED":
        raise ProfitHarvestPolicyBoundaryError("CONTRACT_AUTHORITY_NOT_LOCKED")
    if document["population_authority"] != EXPECTED_POPULATION:
        raise ProfitHarvestPolicyBoundaryError("POPULATION_AUTHORITY_DRIFT")
    if document["design_options"] != EXPECTED_OPTIONS:
        raise ProfitHarvestPolicyBoundaryError("DESIGN_OPTION_AUTHORITY_DRIFT")
    if document["authority"] != AUTHORITY_ALL_FALSE:
        raise ProfitHarvestPolicyBoundaryError("CONTRACT_AUTHORITY_PROMOTION")
    axes = document["policy_axes"]
    if not isinstance(axes, dict) or set(axes) != EXPECTED_AXES:
        raise ProfitHarvestPolicyBoundaryError("POLICY_AXES_INVALID")
    if axes["trigger_eligibility"] != {
        "status": "NOT_COMPUTABLE_AUTHORITY_UNRATIFIED",
        "required_authorities": [
            "HARVEST_TRIGGER_AUTHORITY", "THESIS_STATE_AUTHORITY", "GIVEBACK_AUTHORITY",
        ],
    }:
        raise ProfitHarvestPolicyBoundaryError("TRIGGER_ELIGIBILITY_AUTHORITY_DRIFT")
    if axes["action_family"] != {
        "status": "LOCKED_AUTHORITY_UNRATIFIED",
        "diagnostic_vocabulary": [
            "HOLD", "HARVEST_PARTIAL", "RECOVER_RISK", "TRAIL", "REDUCE", "EXIT",
        ],
    }:
        raise ProfitHarvestPolicyBoundaryError("ACTION_FAMILY_AUTHORITY_DRIFT")
    if axes["quantity_authority"] != {
        "status": "NOT_COMPUTABLE_AUTHORITY_UNRATIFIED",
        "partial_exit_quantity": None,
        "remaining_core_quantity": None,
        "risk_recovery_quantity": None,
    }:
        raise ProfitHarvestPolicyBoundaryError("QUANTITY_AUTHORITY_DRIFT")
    if axes["reallocation_handoff"] != {
        "status": "LOCKED_AUTHORITY_UNRATIFIED",
        "expected_proceeds_are_available_cash": False,
        "settled_cash_required": True,
    }:
        raise ProfitHarvestPolicyBoundaryError("REALLOCATION_HANDOFF_AUTHORITY_DRIFT")
    _reject_policy_numbers(document)
    return copy.deepcopy(document)


def _reject_policy_numbers(value: object, path: str = "root") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, (int, float)):
        raise ProfitHarvestPolicyBoundaryError(f"NUMERIC_POLICY_PARAMETER_FORBIDDEN:{path}")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_policy_numbers(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_policy_numbers(child, f"{path}.{key}")
        return
    raise ProfitHarvestPolicyBoundaryError(f"CONTRACT_VALUE_TYPE_INVALID:{path}")


def validate_locked_readiness(readiness: dict) -> dict:
    if not isinstance(readiness, dict):
        raise ProfitHarvestPolicyBoundaryError("UPSTREAM_READINESS_INVALID")
    required = {"schema_version", "as_of", "source", "baseline", "policy", "harvest", "authority", "readiness_sha256"}
    if set(readiness) != required:
        raise ProfitHarvestPolicyBoundaryError("UPSTREAM_READINESS_FIELDS_INVALID")
    unsigned = {key: value for key, value in readiness.items() if key != "readiness_sha256"}
    if readiness["readiness_sha256"] != payload_sha256(unsigned):
        raise ProfitHarvestPolicyBoundaryError("UPSTREAM_READINESS_HASH_INVALID")
    if readiness["schema_version"] != "profit_harvest_readiness/1":
        raise ProfitHarvestPolicyBoundaryError("UPSTREAM_READINESS_SCHEMA_UNSUPPORTED")
    if readiness["policy"] != {
        "status": "NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED",
        "grid_status": "ANALYTICAL_GRID_UNRATIFIED",
    }:
        raise ProfitHarvestPolicyBoundaryError("UPSTREAM_POLICY_BOUNDARY_OPEN")
    harvest = readiness["harvest"]
    if harvest.get("status") != "LOCKED_POLICY_UNRATIFIED" or harvest.get("recommended_action") != "NONE":
        raise ProfitHarvestPolicyBoundaryError("UPSTREAM_HARVEST_BOUNDARY_OPEN")
    for key in ("harvest_review_items", "reduce_proposal", "exit_proposal", "trade_proposal"):
        expected = [] if key == "harvest_review_items" else None
        if harvest.get(key) != expected:
            raise ProfitHarvestPolicyBoundaryError("UPSTREAM_PROPOSAL_PRESENT")
    if readiness["authority"] != EXPECTED_UPSTREAM_AUTHORITY:
        raise ProfitHarvestPolicyBoundaryError("UPSTREAM_AUTHORITY_PROMOTION")
    return copy.deepcopy(readiness)


def build_policy_boundary(contract: dict, readiness: dict) -> dict:
    locked_contract = validate_contract(contract)
    locked_readiness = validate_locked_readiness(readiness)
    packet = {
        "schema_version": BOUNDARY_SCHEMA_VERSION,
        "as_of": locked_readiness["as_of"],
        "source": {
            "contract_sha256": payload_sha256(locked_contract),
            "profit_harvest_readiness_sha256": locked_readiness["readiness_sha256"],
        },
        "population_authority": copy.deepcopy(locked_contract["population_authority"]),
        "policy_axes": copy.deepcopy(locked_contract["policy_axes"]),
        "design_options": copy.deepcopy(locked_contract["design_options"]),
        "decision": {
            "status": "LOCKED_POLICY_UNRATIFIED",
            "recommended_action": "NONE",
            "review_items": [],
            "harvest_proposal": None,
            "quantity_proposal": None,
            "reallocation_handoff": None,
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["boundary_sha256"] = payload_sha256(packet)
    return packet


def validate_policy_boundary(packet: dict, contract: dict, readiness: dict) -> dict:
    expected = build_policy_boundary(contract, readiness)
    if packet != expected:
        raise ProfitHarvestPolicyBoundaryError("POLICY_BOUNDARY_SEMANTIC_TAMPER_OR_DRIFT")
    return copy.deepcopy(packet)
