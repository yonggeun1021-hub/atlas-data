#!/usr/bin/env python3
"""P5-02 validator for externally ratified TSM Rule decision results.

The module does not calculate PASS/FAIL. It validates a complete human-ratified
slice against the canonical Rule SSOT, exact condition hashes, evidence lineage,
and an explicit authority reference. This is the only P8 PASS/REJECTED ingress.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "ratified_rule_decision_contract.json"
RULES_PATH = ROOT / "config" / "rules.json"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{2,255}$")


class RatifiedRuleDecisionError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RatifiedRuleDecisionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "ratified_rule_decision/1",
        "output_schema_version": "ratified_rule_decision_packet/1",
        "slice_id": "TSM_DECISION_SLICE_V1",
        "subject": "TSM",
        "required_rule_ids": [f"RULE-{number:04d}" for number in range(3, 10)],
        "result_statuses": ["PASS", "FAIL"],
        "authority": {
            "external_ratified_result_validation_only": True,
            "threshold_invention_authorized": False,
            "rule_re_evaluation_authorized": False,
            "stage_change_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or value != expected:
        raise RatifiedRuleDecisionError("CONTRACT_IDENTITY_INVALID")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read(path))


def load_rules(path: Path = RULES_PATH) -> dict:
    value = _read(path)
    if (
        not isinstance(value, dict)
        or value.get("artifact") != "Rule SSOT (config/rules.json)"
        or value.get("authority") is not True
        or not isinstance(value.get("rules"), list)
    ):
        raise RatifiedRuleDecisionError("RULE_SSOT_INVALID")
    return value


def _sha(value, code):
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise RatifiedRuleDecisionError(code)
    return value


def _ref(value, code):
    if not isinstance(value, str) or REF_RE.fullmatch(value) is None:
        raise RatifiedRuleDecisionError(code)
    return value


def validate_packet(value: dict, rules: dict | None = None,
                    contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    rules = load_rules() if rules is None else copy.deepcopy(rules)
    fields = {
        "schema_version", "contract_version", "slice_id", "subject",
        "evaluated_at", "evaluated_by", "authority_ref", "evidence_set_sha256",
        "rule_registry_sha256", "results", "summary", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RatifiedRuleDecisionError("PACKET_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["output_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("slice_id") != contract["slice_id"]
        or value.get("subject") != contract["subject"]
        or value.get("authority") != contract["authority"]
    ):
        raise RatifiedRuleDecisionError("PACKET_IDENTITY_INVALID")
    if not isinstance(value.get("evaluated_at"), str) or UTC_RE.fullmatch(value["evaluated_at"]) is None:
        raise RatifiedRuleDecisionError("EVALUATED_AT_INVALID")
    _ref(value.get("evaluated_by"), "EVALUATED_BY_INVALID")
    _ref(value.get("authority_ref"), "AUTHORITY_REF_INVALID")
    _sha(value.get("evidence_set_sha256"), "EVIDENCE_SET_SHA_INVALID")
    registry_sha = payload_sha256(rules)
    if value.get("rule_registry_sha256") != registry_sha:
        raise RatifiedRuleDecisionError("RULE_REGISTRY_SHA_MISMATCH")
    registry = {row["rule_id"]: row for row in rules["rules"]}
    results = value.get("results")
    if not isinstance(results, list) or [row.get("rule_id") for row in results] != contract["required_rule_ids"]:
        raise RatifiedRuleDecisionError("RESULT_RULE_SET_INVALID")
    counts = {"PASS": 0, "FAIL": 0}
    for row in results:
        if not isinstance(row, dict) or set(row) != {
            "rule_id", "subject", "condition_text_sha256", "result",
            "evidence_reference_ids", "reason",
        }:
            raise RatifiedRuleDecisionError("RESULT_FIELDS_MISMATCH")
        canonical = registry.get(row["rule_id"])
        if (
            canonical is None
            or canonical.get("subject") != contract["subject"]
            or row.get("subject") != canonical.get("subject")
            or row.get("condition_text_sha256") != canonical.get("condition_text_sha256")
            or row.get("result") not in contract["result_statuses"]
            or not isinstance(row.get("reason"), str)
            or not row["reason"].strip()
            or not isinstance(row.get("evidence_reference_ids"), list)
            or not row["evidence_reference_ids"]
            or row["evidence_reference_ids"] != sorted(set(row["evidence_reference_ids"]))
            or any(REF_RE.fullmatch(ref) is None for ref in row["evidence_reference_ids"])
        ):
            raise RatifiedRuleDecisionError(f"RESULT_INVALID:{row.get('rule_id')}")
        counts[row["result"]] += 1
    if value.get("summary") != {"total": len(results), **counts}:
        raise RatifiedRuleDecisionError("SUMMARY_MISMATCH")
    digest = _sha(value.get("packet_sha256"), "PACKET_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("packet_sha256")
    if payload_sha256(payload) != digest:
        raise RatifiedRuleDecisionError("PACKET_SHA_MISMATCH")
    return copy.deepcopy(value)


def build_packet(results: list[dict], evidence_set_sha256: str, evaluated_at: str,
                 evaluated_by: str, authority_ref: str, rules: dict | None = None,
                 contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    rules = load_rules() if rules is None else copy.deepcopy(rules)
    counts = {"PASS": 0, "FAIL": 0}
    for row in results:
        if isinstance(row, dict) and row.get("result") in counts:
            counts[row["result"]] += 1
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "slice_id": contract["slice_id"],
        "subject": contract["subject"],
        "evaluated_at": evaluated_at,
        "evaluated_by": evaluated_by,
        "authority_ref": authority_ref,
        "evidence_set_sha256": evidence_set_sha256,
        "rule_registry_sha256": payload_sha256(rules),
        "results": copy.deepcopy(results),
        "summary": {"total": len(results), **counts},
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, rules, contract)
