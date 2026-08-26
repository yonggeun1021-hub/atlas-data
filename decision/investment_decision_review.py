#!/usr/bin/env python3
"""P8-07 Evidence -> Thesis -> Buy Review fail-closed vertical slice.

This module consumes the production P5 deterministic Rule packet.  It never
re-evaluates a Rule or invents a threshold.  The current P5 contract explicitly
withholds PASS/FAIL authority, so v1 truthfully terminates at BLOCKED and emits
no Trade Proposal.  That is an executable boundary, not a recommendation.
"""
from __future__ import annotations

import copy
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import os
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "investment_decision_review_contract.json"
RULE_VALIDATOR_PATH = ROOT / "rules" / "deterministic_rule_evaluator.py"
RATIFIED_RULE_VALIDATOR_PATH = ROOT / "rules" / "ratified_rule_decision.py"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")


def _load_rule_validator():
    spec = importlib.util.spec_from_file_location("p8_07_rule_validator", RULE_VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("RULE_VALIDATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RULE = _load_rule_validator()


def _load_ratified_rule_validator():
    spec = importlib.util.spec_from_file_location(
        "p8_07_ratified_rule_validator", RATIFIED_RULE_VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("RATIFIED_RULE_VALIDATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RATIFIED_RULE = _load_ratified_rule_validator()


class InvestmentDecisionReviewError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvestmentDecisionReviewError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_keys = {
        "schema_version", "contract_version", "output_schema_version",
        "supported_subjects", "required_rule_ids", "outcomes", "authority",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise InvestmentDecisionReviewError("CONTRACT_FIELDS_MISMATCH")
    if (
        value["schema_version"] != 2
        or value["contract_version"] != "investment_decision_review/2"
        or value["output_schema_version"] != "investment_decision_review_packet/2"
        or value["supported_subjects"] != ["TSM"]
        or value["required_rule_ids"] != {
            "TSM": [
                "RULE-0003", "RULE-0004", "RULE-0005", "RULE-0006",
                "RULE-0007", "RULE-0008", "RULE-0009",
            ]
        }
        or value["outcomes"] != ["PASS", "REJECTED", "BLOCKED"]
        or value["authority"] != {
            "thesis_assembly_only": True,
            "rule_result_consumption_only": True,
            "trade_proposal_draft_authorized": True,
            "stage_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "trade_proposal_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
    ):
        raise InvestmentDecisionReviewError("CONTRACT_IDENTITY_INVALID")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(path))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise InvestmentDecisionReviewError(code)
    return value


def _id(value, code: str) -> str:
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise InvestmentDecisionReviewError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise InvestmentDecisionReviewError(code)
    return value


def _texts(value, code: str, required: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (required and not value)
        or value != list(dict.fromkeys(value))
        or any(not isinstance(item, str) or not item.strip() or item != item.strip() for item in value)
    ):
        raise InvestmentDecisionReviewError(code)
    return list(value)


def validate_thesis(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "thesis_id", "subject", "as_of", "stage_observed", "statement",
        "supporting_evidence_ids", "counter_evidence_ids", "earnings_conversion",
        "invalidation_conditions", "evidence_set_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise InvestmentDecisionReviewError("THESIS_FIELDS_MISMATCH")
    subject = _text(value.get("subject"), "THESIS_SUBJECT_INVALID")
    if subject not in contract["supported_subjects"]:
        raise InvestmentDecisionReviewError("THESIS_SUBJECT_UNSUPPORTED")
    if not isinstance(value.get("as_of"), str) or UTC_RE.fullmatch(value["as_of"]) is None:
        raise InvestmentDecisionReviewError("THESIS_AS_OF_INVALID")
    earnings = value.get("earnings_conversion")
    if not isinstance(earnings, dict) or set(earnings) != {
        "status", "spend_owner", "revenue_recipient", "conversion_window",
        "margin_visibility", "evidence_ids",
    }:
        raise InvestmentDecisionReviewError("EARNINGS_CONVERSION_FIELDS_MISMATCH")
    if earnings.get("status") not in {"SUPPORTED", "PARTIAL", "UNSUPPORTED", "UNKNOWN"}:
        raise InvestmentDecisionReviewError("EARNINGS_CONVERSION_STATUS_INVALID")
    if earnings.get("margin_visibility") not in {"KNOWN", "PARTIAL", "UNKNOWN"}:
        raise InvestmentDecisionReviewError("MARGIN_VISIBILITY_INVALID")
    for key in ("spend_owner", "revenue_recipient", "conversion_window"):
        _text(earnings.get(key), f"EARNINGS_CONVERSION_{key.upper()}_INVALID")
    supporting = _texts(value.get("supporting_evidence_ids"), "SUPPORTING_EVIDENCE_INVALID")
    counter = _texts(value.get("counter_evidence_ids"), "COUNTER_EVIDENCE_INVALID")
    earnings_ids = _texts(earnings.get("evidence_ids"), "EARNINGS_EVIDENCE_INVALID")
    known = set(supporting + counter)
    if not set(earnings_ids).issubset(known):
        raise InvestmentDecisionReviewError("EARNINGS_EVIDENCE_NOT_IN_THESIS")
    _id(value.get("thesis_id"), "THESIS_ID_INVALID")
    _text(value.get("stage_observed"), "STAGE_OBSERVED_INVALID")
    _text(value.get("statement"), "THESIS_STATEMENT_INVALID")
    _texts(value.get("invalidation_conditions"), "INVALIDATION_CONDITIONS_INVALID")
    _sha(value.get("evidence_set_sha256"), "EVIDENCE_SET_SHA_INVALID")
    return copy.deepcopy(value)


def build_packet(thesis: dict, rule_packet: dict, generated_at: str,
                 contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    checked_thesis = validate_thesis(thesis, contract)
    if not isinstance(generated_at, str) or UTC_RE.fullmatch(generated_at) is None:
        raise InvestmentDecisionReviewError("GENERATED_AT_INVALID")
    if rule_packet.get("schema_version") == "ratified_rule_decision_packet/1":
        raise InvestmentDecisionReviewError("RATIFIED_RULE_PACKET_V1_RETIRED_NO_PROVENANCE")
    ratified = rule_packet.get("schema_version") == "ratified_rule_decision_packet/2"
    checked_rules = (
        RATIFIED_RULE.validate_packet(rule_packet)
        if ratified else RULE.validate_packet(rule_packet)
    )
    rule_evidence_sha = (
        checked_rules["evidence_set_sha256"]
        if ratified else checked_rules["lineage"]["evidence_set_sha256"]
    )
    if rule_evidence_sha != checked_thesis["evidence_set_sha256"]:
        raise InvestmentDecisionReviewError("EVIDENCE_SET_SHA_MISMATCH")
    if ratified and checked_rules["authority_evidence"]["usable_from"] > generated_at:
        raise InvestmentDecisionReviewError("RATIFIED_RULE_AUTHORITY_NOT_YET_USABLE")

    blockers = []
    if checked_thesis["earnings_conversion"]["status"] in {"UNKNOWN", "PARTIAL"}:
        blockers.append("EARNINGS_CONVERSION_INCOMPLETE")
    elif checked_thesis["earnings_conversion"]["status"] == "UNSUPPORTED":
        blockers.append("THESIS_EARNINGS_CONVERSION_UNSUPPORTED")
    if ratified:
        rows = {row["rule_id"]: row for row in checked_rules["results"]}
    else:
        if checked_rules["authority"]["pass_fail_authorized"] is not True:
            blockers.append("P5_PASS_FAIL_NOT_AUTHORIZED")
        if checked_rules["authority"]["downstream_action_authorized"] is not True:
            blockers.append("P5_DOWNSTREAM_ACTION_NOT_AUTHORIZED")
        rows = {row["rule_id"]: row for row in checked_rules["rules"]}
    required = contract["required_rule_ids"][checked_thesis["subject"]]
    selected = []
    for rule_id in required:
        row = rows.get(rule_id)
        if row is None:
            blockers.append(f"RULE_RESULT_MISSING:{rule_id}")
            continue
        result = row["result"]
        selected.append({
            "rule_id": rule_id,
            "result": result,
            "reasons": (
                [row["reason"]] if ratified else copy.deepcopy(row["reasons"])
            ),
        })
        if result in {"UNKNOWN", "UNDEFINED"}:
            blockers.append(f"RULE_{result}:{rule_id}")
        if row["subject"] != checked_thesis["subject"]:
            raise InvestmentDecisionReviewError(
                f"RULE_SUBJECT_MISMATCH:{rule_id}"
            )

    blockers = sorted(set(blockers))
    any_fail = any(row["result"] == "FAIL" for row in selected)
    outcome = "BLOCKED" if blockers else ("REJECTED" if any_fail else "PASS")
    proposal = None
    if outcome == "PASS":
        proposal = {
            "schema_version": "trade_proposal_draft/1",
            "subject": checked_thesis["subject"],
            "action": "BUY",
            "mode": "REVIEW_ONLY_ZERO_CAPITAL",
            "entry_rule_ids": [
                row["rule_id"] for row in selected
                if row["rule_id"] in {"RULE-0006", "RULE-0008", "RULE-0009"}
            ],
            "position_size": None,
            "risk_budget": None,
            "invalidation_conditions": copy.deepcopy(
                checked_thesis["invalidation_conditions"]
            ),
            "approval_status": "PENDING_HUMAN",
            "approval_required": True,
            "broker_submission": False,
            "capital_authorized": False,
            "order_intent": None,
        }
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "generated_at": generated_at,
        "subject": checked_thesis["subject"],
        "thesis": checked_thesis,
        "buy_review": {
            "outcome": outcome,
            "required_rule_results": selected,
            "blockers": blockers,
        },
        "trade_proposal": proposal,
        "frozen_rule_packet": checked_rules,
        "lineage": {
            "thesis_sha256": payload_sha256(checked_thesis),
            "evidence_set_sha256": checked_thesis["evidence_set_sha256"],
            "rule_packet_sha256": checked_rules["packet_sha256"],
            "rule_packet_schema_version": checked_rules["schema_version"],
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "generated_at", "subject", "thesis",
        "buy_review", "trade_proposal", "frozen_rule_packet", "lineage", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise InvestmentDecisionReviewError("OUTPUT_FIELDS_MISMATCH")
    review = value.get("buy_review")
    if (
        value.get("schema_version") != contract["output_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["authority"]
        or not isinstance(review, dict)
        or set(review) != {"outcome", "required_rule_results", "blockers"}
        or review.get("outcome") not in contract["outcomes"]
        or (review.get("outcome") == "BLOCKED" and not review.get("blockers"))
        or (review.get("outcome") != "BLOCKED" and review.get("blockers") != [])
        or review.get("blockers") != sorted(set(review["blockers"]))
        or not isinstance(review.get("required_rule_results"), list)
    ):
        raise InvestmentDecisionReviewError("OUTPUT_BOUNDARY_INVALID")
    if not isinstance(value.get("generated_at"), str) or UTC_RE.fullmatch(value["generated_at"]) is None:
        raise InvestmentDecisionReviewError("OUTPUT_GENERATED_AT_INVALID")
    proposal = value.get("trade_proposal")
    if review["outcome"] == "PASS":
        if (
            not isinstance(proposal, dict)
            or set(proposal) != {
                "schema_version", "subject", "action", "mode", "entry_rule_ids",
                "position_size", "risk_budget", "invalidation_conditions",
                "approval_status", "approval_required", "broker_submission",
                "capital_authorized", "order_intent",
            }
            or proposal.get("schema_version") != "trade_proposal_draft/1"
            or proposal.get("subject") != value.get("subject")
            or proposal.get("action") != "BUY"
            or proposal.get("mode") != "REVIEW_ONLY_ZERO_CAPITAL"
            or proposal.get("position_size") is not None
            or proposal.get("risk_budget") is not None
            or proposal.get("approval_status") != "PENDING_HUMAN"
            or proposal.get("approval_required") is not True
            or proposal.get("broker_submission") is not False
            or proposal.get("capital_authorized") is not False
            or proposal.get("order_intent") is not None
            or proposal.get("entry_rule_ids") != ["RULE-0006", "RULE-0008", "RULE-0009"]
        ):
            raise InvestmentDecisionReviewError("TRADE_PROPOSAL_BOUNDARY_INVALID")
    elif proposal is not None:
        raise InvestmentDecisionReviewError("TRADE_PROPOSAL_WITHOUT_PASS")
    checked_thesis = validate_thesis(value.get("thesis"), contract)
    if value.get("subject") != checked_thesis["subject"]:
        raise InvestmentDecisionReviewError("OUTPUT_SUBJECT_MISMATCH")
    frozen = value.get("frozen_rule_packet")
    frozen_schema = frozen.get("schema_version") if isinstance(frozen, dict) else None
    if frozen_schema == "ratified_rule_decision_packet/1":
        raise InvestmentDecisionReviewError("RATIFIED_RULE_PACKET_V1_RETIRED_NO_PROVENANCE")
    try:
        checked_rules = (
            RATIFIED_RULE.validate_packet(frozen)
            if frozen_schema == "ratified_rule_decision_packet/2"
            else RULE.validate_packet(frozen)
        )
    except Exception as exc:
        raise InvestmentDecisionReviewError(f"FROZEN_RULE_PACKET_INVALID:{exc}") from exc
    if frozen_schema == "ratified_rule_decision_packet/2" and checked_rules["authority_evidence"]["usable_from"] > value["generated_at"]:
        raise InvestmentDecisionReviewError("RATIFIED_RULE_AUTHORITY_NOT_YET_USABLE")
    rule_evidence_sha = (
        checked_rules["evidence_set_sha256"]
        if frozen_schema == "ratified_rule_decision_packet/2"
        else checked_rules["lineage"]["evidence_set_sha256"]
    )
    if rule_evidence_sha != checked_thesis["evidence_set_sha256"]:
        raise InvestmentDecisionReviewError("FROZEN_RULE_EVIDENCE_SET_SHA_MISMATCH")
    required_results = review["required_rule_results"]
    if (
        [row.get("rule_id") for row in required_results]
        != contract["required_rule_ids"][checked_thesis["subject"]]
    ):
        raise InvestmentDecisionReviewError("OUTPUT_RULE_SET_INVALID")
    for row in required_results:
        if (
            not isinstance(row, dict)
            or set(row) != {"rule_id", "result", "reasons"}
            or row.get("result") not in {"PASS", "FAIL", "UNKNOWN", "UNDEFINED"}
            or not isinstance(row.get("reasons"), list)
            or not row["reasons"]
        ):
            raise InvestmentDecisionReviewError("OUTPUT_RULE_RESULT_INVALID")
    source_rows = {
        row["rule_id"]: row
        for row in (checked_rules["results"] if frozen_schema == "ratified_rule_decision_packet/2" else checked_rules["rules"])
    }
    expected_results = [{
        "rule_id": rule_id,
        "result": source_rows[rule_id]["result"],
        "reasons": ([source_rows[rule_id]["reason"]]
                    if frozen_schema == "ratified_rule_decision_packet/2"
                    else copy.deepcopy(source_rows[rule_id]["reasons"])),
    } for rule_id in contract["required_rule_ids"][checked_thesis["subject"]]]
    if required_results != expected_results:
        raise InvestmentDecisionReviewError("OUTPUT_RULE_RESULTS_NOT_DERIVED_FROM_FROZEN_PACKET")
    results = [row["result"] for row in required_results]
    if (
        (review["outcome"] == "PASS" and any(result != "PASS" for result in results))
        or (review["outcome"] == "REJECTED" and "FAIL" not in results)
        or (
            any(result in {"UNKNOWN", "UNDEFINED"} for result in results)
            and review["outcome"] != "BLOCKED"
        )
    ):
        raise InvestmentDecisionReviewError("OUTPUT_OUTCOME_DERIVATION_INVALID")
    if proposal is not None and proposal.get("invalidation_conditions") != checked_thesis[
        "invalidation_conditions"
    ]:
        raise InvestmentDecisionReviewError("TRADE_PROPOSAL_INVALIDATION_MISMATCH")
    lineage = value.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {
        "thesis_sha256", "evidence_set_sha256", "rule_packet_sha256",
        "rule_packet_schema_version",
    }:
        raise InvestmentDecisionReviewError("OUTPUT_LINEAGE_INVALID")
    for key in ("thesis_sha256", "evidence_set_sha256", "rule_packet_sha256"):
        _sha(lineage[key], f"OUTPUT_LINEAGE_SHA_INVALID:{key}")
    if lineage["rule_packet_schema_version"] not in {
        "deterministic_rule_evaluation_packet/2", "ratified_rule_decision_packet/2"
    }:
        raise InvestmentDecisionReviewError("OUTPUT_RULE_PACKET_SCHEMA_INVALID")
    if lineage["thesis_sha256"] != payload_sha256(value["thesis"]):
        raise InvestmentDecisionReviewError("OUTPUT_THESIS_SHA_MISMATCH")
    if lineage["evidence_set_sha256"] != checked_thesis["evidence_set_sha256"]:
        raise InvestmentDecisionReviewError("OUTPUT_EVIDENCE_SHA_MISMATCH")
    if lineage["rule_packet_sha256"] != checked_rules["packet_sha256"]:
        raise InvestmentDecisionReviewError("OUTPUT_RULE_PACKET_SHA_MISMATCH")
    if lineage["rule_packet_schema_version"] != checked_rules["schema_version"]:
        raise InvestmentDecisionReviewError("OUTPUT_RULE_PACKET_SCHEMA_MISMATCH")
    digest = _sha(value.get("packet_sha256"), "OUTPUT_PACKET_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("packet_sha256")
    if payload_sha256(payload) != digest:
        raise InvestmentDecisionReviewError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(value)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise InvestmentDecisionReviewError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, output_path: Path) -> int:
    try:
        envelope = _read_json(input_path)
        if not isinstance(envelope, dict) or set(envelope) != {
            "thesis", "rule_packet", "generated_at"
        }:
            raise InvestmentDecisionReviewError("INPUT_ENVELOPE_FIELDS_MISMATCH")
        packet = build_packet(
            envelope["thesis"], envelope["rule_packet"], envelope["generated_at"]
        )
        write_json_atomic(output_path, packet)
        return 0
    except (InvestmentDecisionReviewError, OSError, TypeError, ValueError) as exc:
        print(f"Investment Decision Review failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
