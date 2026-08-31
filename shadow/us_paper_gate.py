#!/usr/bin/env python3
"""US-only PAPER lifecycle Gate evaluator.

``COMMON_SAFETY`` and ``US_PAPER`` are separate contracts.  KRX and Crypto
status is diagnostic only.  This module has no broker client, network call, or
credential input.  Every state is PAPER-only, keeps broker POST at zero, and
grants no REAL, live-account, real-capital, Production, or Trading authority.

CI verifies this mechanism and repository regression only.  CI is never an
operational approval and cannot advance the evidence-derived state.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
COMMON_CONTRACT_PATH = ROOT / "config/us_paper_common_safety_gate_contract.json"
MARKET_CONTRACT_PATH = ROOT / "config/us_paper_market_gate_contract.json"

INPUT_SCHEMA = "us_paper_gate_evidence/1"
OUTPUT_SCHEMA = "us_paper_gate_assessment/1"
ALLOWED_RESULTS = {"PASS", "FAIL", "UNKNOWN"}
STATES = [
    "LOCKED",
    "SHADOW",
    "PAPER_CANARY",
    "PAPER_ACTIVE",
    "PAPER_VALIDATED",
    "LIVE_REVIEW",
]
PERMANENT_AUTHORITY = {
    "paper_only": True,
    "broker_post_count": 0,
    "real_capital_authorized": False,
    "live_account_order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}
INPUT_AUTHORITY = {
    "paper_environment_only": True,
    "broker_post_authorized": False,
    **PERMANENT_AUTHORITY,
}
CI_SEMANTICS = {
    "focused_us_gate_ci_is_mechanism_verification_only": True,
    "full_repository_ci_is_regression_only_not_operational_approval": True,
    "ci_may_advance_operational_state": False,
}
INTERNAL_VIRTUAL_US_PAPER_POLICY = {
    "humanApprovalRequired": False,
    "userReceiptRequired": False,
    "hardGateNullPolicy": "FAIL_CLOSED",
    "automaticTransitionRequiresEveryHardGatePass": True,
    "brokerPostCount": 0,
    "realCapitalAuthorized": False,
    "liveAccountAuthorized": False,
}


class UsPaperGateError(RuntimeError):
    """Fail-closed US PAPER Gate contract violation."""


def fail(code: str, detail: str = "") -> None:
    raise UsPaperGateError(f"{code}:{detail}" if detail else code)


def load_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_INVALID", f"{path}:{exc}")


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _validate_check_definitions(checks: object, label: str) -> list[dict]:
    required = {
        "id",
        "input",
        "evidence",
        "failure_reason",
        "approval_authority",
    }
    if not isinstance(checks, list) or not checks:
        fail("CONTRACT_INVALID", f"{label}.checks")
    seen: set[str] = set()
    validated: list[dict] = []
    for index, row in enumerate(checks):
        if not isinstance(row, dict) or set(row) != required:
            fail("CONTRACT_INVALID", f"{label}.checks[{index}].fields")
        if any(not isinstance(row[key], str) or not row[key] for key in required):
            fail("CONTRACT_INVALID", f"{label}.checks[{index}].values")
        if row["id"] in seen:
            fail("CONTRACT_INVALID", f"{label}.duplicate:{row['id']}")
        seen.add(row["id"])
        validated.append(copy.deepcopy(row))
    return validated


def validate_common_contract(value: object) -> dict:
    expected = {
        "schema_version",
        "contract_version",
        "gate_id",
        "scope",
        "approval_status",
        "allowed_results",
        "checks",
        "invariants",
    }
    if not isinstance(value, dict) or set(value) != expected:
        fail("COMMON_CONTRACT_INVALID", "fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        fail("COMMON_CONTRACT_INVALID", "schema_version")
    if value["contract_version"] != "us_paper_common_safety_gate/1":
        fail("COMMON_CONTRACT_INVALID", "contract_version")
    if value["gate_id"] != "COMMON_SAFETY":
        fail("COMMON_CONTRACT_INVALID", "gate_id")
    if value["scope"] != "COMMON_SAFETY_APPLIED_TO_US_PAPER_ONLY":
        fail("COMMON_CONTRACT_INVALID", "scope")
    if value["allowed_results"] != ["PASS", "FAIL", "UNKNOWN"]:
        fail("COMMON_CONTRACT_INVALID", "allowed_results")
    checks = _validate_check_definitions(value["checks"], "common")
    required_invariants = {
        "common_failure_blocks_us_paper": True,
        "other_market_failure_blocks_us_paper": False,
        "paper_only": True,
        "broker_post_authorized": False,
        "broker_post_count": 0,
        "real_capital_authorized": False,
        "live_account_order_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
        "full_repository_ci_is_regression_only_not_operational_approval": True,
        "secret_values_permitted_in_evidence": False,
    }
    if value["invariants"] != required_invariants:
        fail("COMMON_CONTRACT_INVALID", "invariants")
    result = copy.deepcopy(value)
    result["checks"] = checks
    return result


def validate_market_contract(value: object) -> dict:
    expected = {
        "schema_version",
        "contract_version",
        "market",
        "approval_status",
        "state_sequence",
        "paper_substages",
        "internal_virtual_us_paper_policy",
        "gates",
        "check_definitions",
        "authority_by_state",
        "permanent_authority_boundary",
        "ci_semantics",
        "cross_market_isolation",
    }
    if not isinstance(value, dict) or set(value) != expected:
        fail("MARKET_CONTRACT_INVALID", "fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        fail("MARKET_CONTRACT_INVALID", "schema_version")
    if value["contract_version"] != "us_paper_market_gate/1":
        fail("MARKET_CONTRACT_INVALID", "contract_version")
    if value["market"] != "US" or value["state_sequence"] != STATES:
        fail("MARKET_CONTRACT_INVALID", "market_or_state_sequence")
    if value["paper_substages"] != {
        "PAPER_CANARY": "BOUNDED_INTERNAL_US_PAPER_LEDGER",
        "PAPER_ACTIVE": "SCHEDULED_INTERNAL_US_PAPER_LEDGER",
        "PAPER_VALIDATED": "VALIDATED_INTERNAL_US_PAPER_LEDGER",
        "LIVE_REVIEW": "REVIEW_ONLY_REAL_AUTHORITY_STILL_FALSE",
    }:
        fail("MARKET_CONTRACT_INVALID", "paper_substages")
    if value["internal_virtual_us_paper_policy"] != INTERNAL_VIRTUAL_US_PAPER_POLICY:
        fail("MARKET_CONTRACT_INVALID", "internal_virtual_us_paper_policy")

    definitions = _validate_check_definitions(value["check_definitions"], "market")
    definition_ids = {row["id"] for row in definitions}
    expected_gate_fields = {
        "id",
        "opens_state",
        "prerequisite_gate",
        "required_checks",
    }
    expected_gates = [
        ("US_SHADOW", "SHADOW", "COMMON_SAFETY"),
        ("US_PAPER_CANARY_START", "PAPER_CANARY", "US_SHADOW"),
        ("US_PAPER_ACTIVE", "PAPER_ACTIVE", "US_PAPER_CANARY_START"),
        ("US_PAPER_VALIDATED_30D", "PAPER_VALIDATED", "US_PAPER_ACTIVE"),
        ("US_LIVE_REVIEW", "LIVE_REVIEW", "US_PAPER_VALIDATED_30D"),
    ]
    if not isinstance(value["gates"], list) or len(value["gates"]) != len(expected_gates):
        fail("MARKET_CONTRACT_INVALID", "gates")
    consumed: list[str] = []
    for index, (gate, expected_gate) in enumerate(zip(value["gates"], expected_gates)):
        if not isinstance(gate, dict) or set(gate) != expected_gate_fields:
            fail("MARKET_CONTRACT_INVALID", f"gates[{index}].fields")
        if (gate["id"], gate["opens_state"], gate["prerequisite_gate"]) != expected_gate:
            fail("MARKET_CONTRACT_INVALID", f"gates[{index}].sequence")
        required = gate["required_checks"]
        if (
            not isinstance(required, list)
            or not required
            or len(set(required)) != len(required)
            or any(check not in definition_ids for check in required)
        ):
            fail("MARKET_CONTRACT_INVALID", f"gates[{index}].checks")
        consumed.extend(required)
    if len(consumed) != len(set(consumed)) or set(consumed) != definition_ids:
        fail("MARKET_CONTRACT_INVALID", "check_definition_coverage")

    state_fields = {
        "internal_paper_ledger_authorized",
        "scheduled_internal_paper_authorized",
        "broker_post_authorized",
    }
    authority = value["authority_by_state"]
    if not isinstance(authority, dict) or list(authority) != STATES:
        fail("MARKET_CONTRACT_INVALID", "authority_by_state")
    for state in STATES:
        row = authority[state]
        if not isinstance(row, dict) or set(row) != state_fields:
            fail("MARKET_CONTRACT_INVALID", f"authority_by_state.{state}")
        if any(type(flag) is not bool for flag in row.values()):
            fail("MARKET_CONTRACT_INVALID", f"authority_by_state.{state}.type")
        if row["broker_post_authorized"]:
            fail("MARKET_CONTRACT_INVALID", f"{state}.broker_post")
    if authority["LOCKED"] != authority["SHADOW"] or authority["LOCKED"] != {
        "internal_paper_ledger_authorized": False,
        "scheduled_internal_paper_authorized": False,
        "broker_post_authorized": False,
    }:
        fail("MARKET_CONTRACT_INVALID", "locked_shadow_authority")
    if authority["PAPER_CANARY"] != {
        "internal_paper_ledger_authorized": True,
        "scheduled_internal_paper_authorized": False,
        "broker_post_authorized": False,
    }:
        fail("MARKET_CONTRACT_INVALID", "paper_canary_authority")
    for state in ("PAPER_ACTIVE", "PAPER_VALIDATED", "LIVE_REVIEW"):
        if authority[state] != {
            "internal_paper_ledger_authorized": True,
            "scheduled_internal_paper_authorized": True,
            "broker_post_authorized": False,
        }:
            fail("MARKET_CONTRACT_INVALID", f"{state}.authority")
    if value["permanent_authority_boundary"] != PERMANENT_AUTHORITY:
        fail("MARKET_CONTRACT_INVALID", "permanent_authority_boundary")
    if value["ci_semantics"] != CI_SEMANTICS:
        fail("MARKET_CONTRACT_INVALID", "ci_semantics")
    if value["cross_market_isolation"] != {
        "evaluated_market": "US",
        "other_market_gate_results_are_diagnostic_only": True,
        "common_safety_failure_is_blocking": True,
    }:
        fail("MARKET_CONTRACT_INVALID", "cross_market_isolation")
    result = copy.deepcopy(value)
    result["check_definitions"] = definitions
    return result


def load_contracts(
    common_path: Path = COMMON_CONTRACT_PATH,
    market_path: Path = MARKET_CONTRACT_PATH,
) -> tuple[dict, dict]:
    return (
        validate_common_contract(load_json(common_path)),
        validate_market_contract(load_json(market_path)),
    )


def _validate_evidence_row(value: object, check_id: str) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "status",
        "evidence_refs",
        "approval_refs",
        "note",
    }:
        fail("EVIDENCE_INPUT_INVALID", f"{check_id}.fields")
    if value["status"] not in ALLOWED_RESULTS:
        fail("EVIDENCE_INPUT_INVALID", f"{check_id}.status")
    for key in ("evidence_refs", "approval_refs"):
        refs = value[key]
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or not ref for ref in refs
        ):
            fail("EVIDENCE_INPUT_INVALID", f"{check_id}.{key}")
    if value["status"] != "UNKNOWN" and not value["evidence_refs"]:
        fail("EVIDENCE_INPUT_INVALID", f"{check_id}.evidence_required")
    if not isinstance(value["note"], str):
        fail("EVIDENCE_INPUT_INVALID", f"{check_id}.note")
    return copy.deepcopy(value)


def validate_evidence_input(
    value: object,
    common_contract: dict,
    market_contract: dict,
) -> dict:
    required = {
        "schema_version",
        "market",
        "as_of_utc",
        "source_revisions",
        "common_safety_checks",
        "us_checks",
        "other_market_context",
        "authority",
    }
    if not isinstance(value, dict) or set(value) != required:
        fail("EVIDENCE_INPUT_INVALID", "fields")
    if value["schema_version"] != INPUT_SCHEMA or value["market"] != "US":
        fail("EVIDENCE_INPUT_INVALID", "schema_or_market")
    if not isinstance(value["as_of_utc"], str) or not value["as_of_utc"].endswith("Z"):
        fail("EVIDENCE_INPUT_INVALID", "as_of_utc")
    revisions = value["source_revisions"]
    if not isinstance(revisions, dict) or not revisions:
        fail("EVIDENCE_INPUT_INVALID", "source_revisions")
    if any(
        not isinstance(name, str)
        or not name
        or not isinstance(sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", sha) is None
        for name, sha in revisions.items()
    ):
        fail("EVIDENCE_INPUT_INVALID", "source_revisions.values")
    common_ids = {row["id"] for row in common_contract["checks"]}
    market_ids = {row["id"] for row in market_contract["check_definitions"]}
    if not isinstance(value["common_safety_checks"], dict) or set(
        value["common_safety_checks"]
    ) != common_ids:
        fail("EVIDENCE_INPUT_INVALID", "common_check_coverage")
    if not isinstance(value["us_checks"], dict) or set(value["us_checks"]) != market_ids:
        fail("EVIDENCE_INPUT_INVALID", "us_check_coverage")
    common_rows = {
        check_id: _validate_evidence_row(row, check_id)
        for check_id, row in value["common_safety_checks"].items()
    }
    market_rows = {
        check_id: _validate_evidence_row(row, check_id)
        for check_id, row in value["us_checks"].items()
    }
    other = value["other_market_context"]
    if not isinstance(other, dict) or any(
        market == "US"
        or market not in {"KOREA", "CRYPTO"}
        or status not in ALLOWED_RESULTS
        for market, status in other.items()
    ):
        fail("EVIDENCE_INPUT_INVALID", "other_market_context")
    if value["authority"] != INPUT_AUTHORITY:
        fail("EVIDENCE_INPUT_INVALID", "authority")
    result = copy.deepcopy(value)
    result["common_safety_checks"] = common_rows
    result["us_checks"] = market_rows
    return result


def _own_gate_result(
    check_ids: list[str],
    evidence: dict,
    definitions: dict[str, dict],
) -> tuple[str, list[str]]:
    statuses = [evidence[check_id]["status"] for check_id in check_ids]
    reasons = [
        definitions[check_id]["failure_reason"]
        if evidence[check_id]["status"] == "FAIL"
        else f"{check_id}_EVIDENCE_UNKNOWN"
        for check_id in check_ids
        if evidence[check_id]["status"] != "PASS"
    ]
    if "FAIL" in statuses:
        return "FAIL", reasons
    if "UNKNOWN" in statuses:
        return "UNKNOWN", reasons
    return "PASS", []


def _combine_with_prerequisite(own: str, prerequisite: str) -> str:
    if own == "FAIL" or prerequisite == "FAIL":
        return "FAIL"
    if own == "PASS" and prerequisite == "PASS":
        return "PASS"
    return "UNKNOWN"


def evaluate(
    evidence_input: object,
    common_contract: Optional[dict] = None,
    market_contract: Optional[dict] = None,
) -> dict:
    if common_contract is None or market_contract is None:
        loaded_common, loaded_market = load_contracts()
        common_contract = loaded_common if common_contract is None else common_contract
        market_contract = loaded_market if market_contract is None else market_contract
    common = validate_common_contract(common_contract)
    market = validate_market_contract(market_contract)
    evidence = validate_evidence_input(evidence_input, common, market)

    common_definitions = {row["id"]: row for row in common["checks"]}
    common_ids = [row["id"] for row in common["checks"]]
    common_status, common_reasons = _own_gate_result(
        common_ids,
        evidence["common_safety_checks"],
        common_definitions,
    )
    gate_results: list[dict] = [
        {
            "gate_id": "COMMON_SAFETY",
            "opens_state": "LOCKED_BOUNDARY_ONLY",
            "prerequisite_gate": None,
            "prerequisite_status": None,
            "own_status": common_status,
            "status": common_status,
            "required_checks": common_ids,
            "reasons": common_reasons,
        }
    ]
    status_by_gate = {"COMMON_SAFETY": common_status}
    market_definitions = {row["id"]: row for row in market["check_definitions"]}
    current_state = "LOCKED"
    for gate in market["gates"]:
        own, reasons = _own_gate_result(
            gate["required_checks"],
            evidence["us_checks"],
            market_definitions,
        )
        prerequisite_status = status_by_gate[gate["prerequisite_gate"]]
        status = _combine_with_prerequisite(own, prerequisite_status)
        if prerequisite_status != "PASS":
            reasons = [
                f"{gate['prerequisite_gate']}_NOT_PASS:{prerequisite_status}"
            ] + reasons
        gate_results.append(
            {
                "gate_id": gate["id"],
                "opens_state": gate["opens_state"],
                "prerequisite_gate": gate["prerequisite_gate"],
                "prerequisite_status": prerequisite_status,
                "own_status": own,
                "status": status,
                "required_checks": list(gate["required_checks"]),
                "reasons": reasons,
            }
        )
        status_by_gate[gate["id"]] = status
        if status == "PASS":
            current_state = gate["opens_state"]

    first_blocked = (
        gate_results[0]
        if common_status != "PASS"
        else next((row for row in gate_results[1:] if row["status"] != "PASS"), None)
    )
    assessment = {
        "schema_version": OUTPUT_SCHEMA,
        "contract_versions": {
            "common_safety": common["contract_version"],
            "us_market": market["contract_version"],
        },
        "contract_sha256": {
            "common_safety": payload_sha256(common),
            "us_market": payload_sha256(market),
        },
        "evidence_input_sha256": payload_sha256(evidence),
        "market": "US",
        "as_of_utc": evidence["as_of_utc"],
        "source_revisions": copy.deepcopy(evidence["source_revisions"]),
        "current_state": current_state,
        "paper_substage": market["paper_substages"].get(current_state, "NONE"),
        "gate_results": gate_results,
        "next_gate": first_blocked["gate_id"] if first_blocked else None,
        "blocking_reasons": first_blocked["reasons"] if first_blocked else [],
        "other_market_context": copy.deepcopy(evidence["other_market_context"]),
        "cross_market_isolation_applied": True,
        "ci_semantics": copy.deepcopy(market["ci_semantics"]),
        "internal_virtual_us_paper_policy": copy.deepcopy(
            market["internal_virtual_us_paper_policy"]
        ),
        "authority": {
            **copy.deepcopy(market["authority_by_state"][current_state]),
            **PERMANENT_AUTHORITY,
        },
    }
    assessment["assessment_sha256"] = payload_sha256(assessment)
    return assessment


def validate_assessment(
    assessment: object,
    evidence_input: object,
    common_contract: Optional[dict] = None,
    market_contract: Optional[dict] = None,
) -> dict:
    expected = evaluate(evidence_input, common_contract, market_contract)
    if not isinstance(assessment, dict) or canonical_bytes(assessment) != canonical_bytes(expected):
        fail("ASSESSMENT_DERIVATION_MISMATCH")
    return copy.deepcopy(assessment)


def write_json(value: dict, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("evidence_input", type=Path)
    evaluate_parser.add_argument("--out", type=Path)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("assessment", type=Path)
    validate_parser.add_argument("--evidence-input", type=Path, required=True)
    args = parser.parse_args(argv)

    common, market = load_contracts()
    evidence_input = load_json(args.evidence_input)
    if args.command == "evaluate":
        assessment = evaluate(evidence_input, common, market)
        if args.out:
            write_json(assessment, args.out)
        print(json.dumps(assessment, ensure_ascii=False, sort_keys=True))
        return 0
    assessment = validate_assessment(
        load_json(args.assessment),
        evidence_input,
        common,
        market,
    )
    print(json.dumps(assessment, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
