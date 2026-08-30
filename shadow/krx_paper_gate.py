#!/usr/bin/env python3
"""KRX-only PAPER lifecycle gate evaluator.

The evaluator separates the market-independent safety boundary from the KRX
strategy/evidence boundary.  It does not inspect or depend on Crypto or US
gate completion.  A COMMON SAFETY failure always locks KRX PAPER.

This module also keeps two PAPER authorities distinct:

* ``INTERNAL_VIRTUAL_LEDGER_PAPER`` starts at ``PAPER_CANARY``;
* ``KIS_MOCK_ACCOUNT_AUTO_ORDER`` starts only at ``PAPER_ACTIVE``.

No state in this contract grants REAL, live-account, Production, or trading
authority.  The 30-natural-calendar-day requirement is a validation gate,
not a PAPER_CANARY start condition.
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
COMMON_CONTRACT_PATH = ROOT / "config/krx_paper_common_safety_gate_contract.json"
MARKET_CONTRACT_PATH = ROOT / "config/krx_paper_market_gate_contract.json"

INPUT_SCHEMA = "krx_paper_gate_evidence/1"
OUTPUT_SCHEMA = "krx_paper_gate_assessment/1"
ALLOWED_RESULTS = {"PASS", "FAIL", "UNKNOWN"}
PERMANENT_AUTHORITY = {
    "real_capital_authorized": False,
    "live_account_order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}
INPUT_AUTHORITY = {
    "paper_environment_only": True,
    **PERMANENT_AUTHORITY,
}


class KrxPaperGateError(RuntimeError):
    """Fail-closed KRX PAPER gate contract violation."""


def fail(code: str, detail: str = "") -> None:
    raise KrxPaperGateError(f"{code}:{detail}" if detail else code)


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
    if not isinstance(checks, list) or not checks:
        fail("CONTRACT_INVALID", f"{label}.checks")
    required = {
        "id",
        "input",
        "evidence",
        "failure_reason",
        "approval_authority",
    }
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
    if value["contract_version"] != "krx_paper_common_safety_gate/1":
        fail("COMMON_CONTRACT_INVALID", "contract_version")
    if value["gate_id"] != "COMMON_SAFETY":
        fail("COMMON_CONTRACT_INVALID", "gate_id")
    if value["allowed_results"] != ["PASS", "FAIL", "UNKNOWN"]:
        fail("COMMON_CONTRACT_INVALID", "allowed_results")
    checks = _validate_check_definitions(value["checks"], "common")
    invariants = value["invariants"]
    if invariants != {
        "common_failure_blocks_krx_paper": True,
        "other_market_failure_blocks_krx_paper": False,
        "real_capital_authorized": False,
        "live_order_submission_authorized": False,
        "secret_values_permitted_in_evidence": False,
    }:
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
        "gates",
        "check_definitions",
        "authority_by_state",
        "permanent_authority_boundary",
        "cross_market_isolation",
    }
    if not isinstance(value, dict) or set(value) != expected:
        fail("MARKET_CONTRACT_INVALID", "fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        fail("MARKET_CONTRACT_INVALID", "schema_version")
    if value["contract_version"] != "krx_paper_market_gate/1":
        fail("MARKET_CONTRACT_INVALID", "contract_version")
    if value["market"] != "KOREA":
        fail("MARKET_CONTRACT_INVALID", "market")
    states = [
        "LOCKED",
        "SHADOW",
        "PAPER_CANARY",
        "PAPER_ACTIVE",
        "PAPER_VALIDATED",
        "LIVE_REVIEW",
    ]
    if value["state_sequence"] != states:
        fail("MARKET_CONTRACT_INVALID", "state_sequence")
    if value["paper_substages"] != {
        "PAPER_CANARY": "INTERNAL_VIRTUAL_LEDGER_PAPER",
        "PAPER_ACTIVE": "KIS_MOCK_ACCOUNT_AUTO_ORDER",
        "PAPER_VALIDATED": "KIS_MOCK_ACCOUNT_AUTO_ORDER_VALIDATED",
        "LIVE_REVIEW": "KIS_MOCK_ACCOUNT_ONLY_LIVE_AUTHORITY_STILL_FALSE",
    }:
        fail("MARKET_CONTRACT_INVALID", "paper_substages")
    definitions = _validate_check_definitions(
        value["check_definitions"], "market"
    )
    definition_ids = {row["id"] for row in definitions}
    expected_gate_fields = {
        "id",
        "opens_state",
        "prerequisite_gate",
        "required_checks",
    }
    expected_gates = [
        ("KRX_SHADOW", "SHADOW", "COMMON_SAFETY"),
        ("KRX_PAPER_CANARY_START", "PAPER_CANARY", "KRX_SHADOW"),
        ("KRX_PAPER_ACTIVE", "PAPER_ACTIVE", "KRX_PAPER_CANARY_START"),
        ("KRX_PAPER_VALIDATED_30D", "PAPER_VALIDATED", "KRX_PAPER_ACTIVE"),
        ("KRX_LIVE_REVIEW", "LIVE_REVIEW", "KRX_PAPER_VALIDATED_30D"),
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
    authority = value["authority_by_state"]
    if not isinstance(authority, dict) or list(authority) != states:
        fail("MARKET_CONTRACT_INVALID", "authority_by_state")
    for state in states:
        row = authority[state]
        if not isinstance(row, dict) or set(row) != {
            "internal_virtual_ledger_paper_authorized",
            "kis_mock_account_auto_order_authorized",
        }:
            fail("MARKET_CONTRACT_INVALID", f"authority_by_state.{state}")
        if any(type(flag) is not bool for flag in row.values()):
            fail("MARKET_CONTRACT_INVALID", f"authority_by_state.{state}.type")
    if authority["LOCKED"] != authority["SHADOW"] or authority["LOCKED"] != {
        "internal_virtual_ledger_paper_authorized": False,
        "kis_mock_account_auto_order_authorized": False,
    }:
        fail("MARKET_CONTRACT_INVALID", "locked_shadow_authority")
    if authority["PAPER_CANARY"] != {
        "internal_virtual_ledger_paper_authorized": True,
        "kis_mock_account_auto_order_authorized": False,
    }:
        fail("MARKET_CONTRACT_INVALID", "paper_canary_authority")
    for state in ("PAPER_ACTIVE", "PAPER_VALIDATED", "LIVE_REVIEW"):
        if authority[state] != {
            "internal_virtual_ledger_paper_authorized": True,
            "kis_mock_account_auto_order_authorized": True,
        }:
            fail("MARKET_CONTRACT_INVALID", f"{state}.authority")
    if value["permanent_authority_boundary"] != PERMANENT_AUTHORITY:
        fail("MARKET_CONTRACT_INVALID", "permanent_authority_boundary")
    if value["cross_market_isolation"] != {
        "evaluated_market": "KOREA",
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
        if (
            not isinstance(refs, list)
            or any(not isinstance(ref, str) or not ref for ref in refs)
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
        "krx_checks",
        "other_market_context",
        "authority",
    }
    if not isinstance(value, dict) or set(value) != required:
        fail("EVIDENCE_INPUT_INVALID", "fields")
    if value["schema_version"] != INPUT_SCHEMA or value["market"] != "KOREA":
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
    if not isinstance(value["common_safety_checks"], dict) or set(value["common_safety_checks"]) != common_ids:
        fail("EVIDENCE_INPUT_INVALID", "common_check_coverage")
    if not isinstance(value["krx_checks"], dict) or set(value["krx_checks"]) != market_ids:
        fail("EVIDENCE_INPUT_INVALID", "krx_check_coverage")
    common_rows = {
        check_id: _validate_evidence_row(row, check_id)
        for check_id, row in value["common_safety_checks"].items()
    }
    market_rows = {
        check_id: _validate_evidence_row(row, check_id)
        for check_id, row in value["krx_checks"].items()
    }
    other = value["other_market_context"]
    if not isinstance(other, dict) or any(
        market == "KOREA"
        or not isinstance(market, str)
        or status not in ALLOWED_RESULTS
        for market, status in other.items()
    ):
        fail("EVIDENCE_INPUT_INVALID", "other_market_context")
    if value["authority"] != INPUT_AUTHORITY:
        fail("EVIDENCE_INPUT_INVALID", "authority")
    result = copy.deepcopy(value)
    result["common_safety_checks"] = common_rows
    result["krx_checks"] = market_rows
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
    market_definitions = {
        row["id"]: row for row in market["check_definitions"]
    }
    current_state = "LOCKED"
    for gate in market["gates"]:
        own, reasons = _own_gate_result(
            gate["required_checks"],
            evidence["krx_checks"],
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
        else next(
            (row for row in gate_results[1:] if row["status"] != "PASS"),
            None,
        )
    )
    paper_substage = market["paper_substages"].get(current_state, "NONE")
    state_authority = copy.deepcopy(market["authority_by_state"][current_state])
    assessment = {
        "schema_version": OUTPUT_SCHEMA,
        "contract_versions": {
            "common_safety": common["contract_version"],
            "krx_market": market["contract_version"],
        },
        "contract_sha256": {
            "common_safety": payload_sha256(common),
            "krx_market": payload_sha256(market),
        },
        "evidence_input_sha256": payload_sha256(evidence),
        "market": "KOREA",
        "as_of_utc": evidence["as_of_utc"],
        "source_revisions": copy.deepcopy(evidence["source_revisions"]),
        "current_state": current_state,
        "paper_substage": paper_substage,
        "gate_results": gate_results,
        "next_gate": first_blocked["gate_id"] if first_blocked else None,
        "blocking_reasons": first_blocked["reasons"] if first_blocked else [],
        "other_market_context": copy.deepcopy(evidence["other_market_context"]),
        "cross_market_isolation_applied": True,
        "authority": {
            **state_authority,
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
