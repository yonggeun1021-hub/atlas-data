#!/usr/bin/env python3
"""P5-04 deterministic UNKNOWN/UNDEFINED Rule boundary evaluator."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bridge import rule_evidence_binding as RULE_EVIDENCE_BINDING  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "deterministic_rule_evaluator_contract.json"
RULES_PATH = ROOT / "config" / "rules.json"
OUTPUT_SCHEMA_VERSION = "deterministic_rule_evaluation_packet/1"
RULE_ID_RE = re.compile(r"^RULE-\d{4}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LINK_STATUSES = ("LINK_AVAILABLE", "LINK_BLOCKED", "LINK_UNRESOLVED")
RESULT_STATUSES = ("PASS", "FAIL", "UNKNOWN", "UNDEFINED")


class DeterministicRuleEvaluatorError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeterministicRuleEvaluatorError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "deterministic_rule_evaluator/1",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "upstream_binding_contract_version": "rule_evidence_binding/1",
        "upstream_binding_schema_version": "rule_evidence_binding_packet/1",
        "canonical_rule_registry": "config/rules.json",
        "canonical_rule_count": 25,
        "result_statuses": list(RESULT_STATUSES),
        "classification_precedence": [
            "RULE_DEFINITION_UNDEFINED_TO_UNDEFINED",
            "EVIDENCE_OR_RULE_EXECUTION_GAP_TO_UNKNOWN",
            "EVALUATION_SPEC_OR_AUTHORITY_GAP_TO_UNDEFINED",
        ],
        "repository_default_evaluation_spec": "ABSENT",
        "pass_fail_policy": (
            "PROHIBITED_UNTIL_P5_02_AND_EXPLICIT_EVALUATOR_AUTHORIZATION"
        ),
        "authority": {
            "boundary_classification_only": True,
            "evidence_interpretation_authorized": False,
            "threshold_application_authorized": False,
            "pass_fail_authorized": False,
            "downstream_action_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise DeterministicRuleEvaluatorError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise DeterministicRuleEvaluatorError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DeterministicRuleEvaluatorError(code)
    return value


def _validate_rules(value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or value.get("artifact") != "Rule SSOT (config/rules.json)"
        or value.get("authority") is not True
        or value.get("consumable_by_evaluator") is not False
        or value.get("evaluator_wiring") != "미연결"
        or value.get("rule_count") != 25
        or not isinstance(value.get("rules"), list)
        or len(value["rules"]) != 25
    ):
        raise DeterministicRuleEvaluatorError("RULE_REGISTRY_IDENTITY_INVALID")
    seen = set()
    for rule in value["rules"]:
        if not isinstance(rule, dict):
            raise DeterministicRuleEvaluatorError("RULE_REGISTRY_ROW_INVALID")
        rule_id = rule.get("rule_id")
        if (
            not isinstance(rule_id, str)
            or RULE_ID_RE.fullmatch(rule_id) is None
            or rule_id in seen
            or not isinstance(rule.get("subject"), str)
            or not rule["subject"]
            or rule.get("definition_status") not in {"DEFINED", "UNDEFINED"}
            or rule.get("evaluator_status") not in {"READY", "BLOCKED"}
            or not isinstance(rule.get("blocked_by"), list)
        ):
            raise DeterministicRuleEvaluatorError(f"RULE_REGISTRY_ROW_INVALID:{rule_id}")
        _sha(rule.get("condition_text_sha256"), f"RULE_CONDITION_SHA_INVALID:{rule_id}")
        seen.add(rule_id)
    return copy.deepcopy(value)


def load_rules(path: Path = RULES_PATH) -> dict:
    return _validate_rules(_read_json(Path(path)))


def _validate_binding_packet(value: dict, rules: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "binding_set_id",
        "source_hierarchy_status", "automatic_binding_authorized", "authority",
        "inputs", "summary", "rules", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise DeterministicRuleEvaluatorError("BINDING_PACKET_FIELDS_MISMATCH")
    digest = _sha(value.get("packet_sha256"), "BINDING_PACKET_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("packet_sha256")
    if payload_sha256(payload) != digest:
        raise DeterministicRuleEvaluatorError("BINDING_PACKET_SHA_MISMATCH")
    if (
        value.get("schema_version") != contract["upstream_binding_schema_version"]
        or value.get("contract_version") != contract[
            "upstream_binding_contract_version"
        ]
        or value.get("source_hierarchy_status") != "UNRATIFIED"
        or value.get("automatic_binding_authorized") is not False
        or value.get("authority") != {
            "linkage_only": True,
            "automatic_source_selection_authorized": False,
            "interpretation_authorized": False,
            "rule_evaluation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
    ):
        raise DeterministicRuleEvaluatorError("BINDING_PACKET_IDENTITY_INVALID")
    inputs = value.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {
        "rule_registry_sha256", "binding_set_sha256", "evidence_set_sha256"
    }:
        raise DeterministicRuleEvaluatorError("BINDING_PACKET_INPUTS_INVALID")
    if inputs.get("rule_registry_sha256") != payload_sha256(rules):
        raise DeterministicRuleEvaluatorError("BINDING_RULE_REGISTRY_SHA_MISMATCH")
    _sha(inputs.get("binding_set_sha256"), "BINDING_INPUT_SHA_INVALID")
    _sha(inputs.get("evidence_set_sha256"), "BINDING_INPUT_SHA_INVALID")
    registry = {rule["rule_id"]: rule for rule in rules["rules"]}
    rows = value.get("rules")
    if not isinstance(rows, list) or len(rows) != contract["canonical_rule_count"]:
        raise DeterministicRuleEvaluatorError("BINDING_RULE_COUNT_INVALID")
    checked = []
    counts = {status: 0 for status in LINK_STATUSES}
    row_fields = {
        "rule_id", "subject", "condition_text_sha256", "link_status",
        "link_reasons", "selection_mode", "evidence_references",
        "evaluation_status", "rule_result",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise DeterministicRuleEvaluatorError("BINDING_RULE_FIELDS_MISMATCH")
        rule_id = row.get("rule_id")
        rule = registry.get(rule_id)
        link_status = row.get("link_status")
        if (
            rule is None
            or row.get("subject") != rule["subject"]
            or row.get("condition_text_sha256") != rule["condition_text_sha256"]
            or link_status not in LINK_STATUSES
            or not isinstance(row.get("link_reasons"), list)
            or not isinstance(row.get("evidence_references"), list)
            or row.get("evaluation_status") != "EVALUATION_NOT_AUTHORIZED"
            or row.get("rule_result") is not None
            or (
                row.get("selection_mode")
                not in ({"ALL_REQUIRED"} if row["evidence_references"] else {None})
            )
        ):
            raise DeterministicRuleEvaluatorError(f"BINDING_RULE_INVALID:{rule_id}")
        counts[link_status] += 1
        checked.append(copy.deepcopy(row))
    if [row["rule_id"] for row in checked] != sorted(registry):
        raise DeterministicRuleEvaluatorError("BINDING_RULE_ORDER_INVALID")
    expected_summary = {
        "total_rules": contract["canonical_rule_count"],
        **counts,
    }
    if value.get("summary") != expected_summary:
        raise DeterministicRuleEvaluatorError("BINDING_SUMMARY_MISMATCH")
    try:
        RULE_EVIDENCE_BINDING.validate_packet(
            value,
            rules=rules,
            contract=RULE_EVIDENCE_BINDING.load_contract(),
        )
    except RULE_EVIDENCE_BINDING.RuleEvidenceBindingError as exc:
        raise DeterministicRuleEvaluatorError(
            f"BINDING_PACKET_SEMANTIC_INVALID:{exc}"
        ) from exc
    return {
        "packet_sha256": digest,
        "inputs": copy.deepcopy(inputs),
        "binding_set_id": value["binding_set_id"],
        "rules": checked,
    }


def _classify(rule: dict, link: dict) -> tuple[str, list[str]]:
    if rule["definition_status"] == "UNDEFINED":
        return "UNDEFINED", ["RULE_DEFINITION_UNDEFINED"]
    unknown_reasons = []
    if link["link_status"] != "LINK_AVAILABLE":
        unknown_reasons.append(f"EVIDENCE_{link['link_status']}")
        unknown_reasons.extend(str(item) for item in link["link_reasons"])
    if rule["evaluator_status"] != "READY":
        unknown_reasons.append("RULE_SSOT_EVALUATOR_BLOCKED")
        unknown_reasons.extend(str(item) for item in rule["blocked_by"])
    if unknown_reasons:
        return "UNKNOWN", sorted(set(unknown_reasons))
    return "UNDEFINED", [
        "EVALUATION_SPEC_ABSENT",
        "EVALUATOR_AUTHORITY_NOT_RATIFIED",
        "RULE_REGISTRY_NOT_CONSUMABLE_BY_EVALUATOR",
    ]


def build_packet(
    binding_packet: dict,
    rules: dict | None = None,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    try:
        rules = _validate_rules(rules) if rules is not None else load_rules()
    except DeterministicRuleEvaluatorError as exc:
        raise DeterministicRuleEvaluatorError(
            f"RULE_REGISTRY_AUTHORITY_INVALID:{exc}"
        ) from exc
    if len(rules["rules"]) != contract["canonical_rule_count"]:
        raise DeterministicRuleEvaluatorError("RULE_REGISTRY_COUNT_MISMATCH")
    checked = _validate_binding_packet(binding_packet, rules, contract)
    registry = {rule["rule_id"]: rule for rule in rules["rules"]}
    output = []
    counts = {status: 0 for status in RESULT_STATUSES}
    for link in checked["rules"]:
        rule = registry[link["rule_id"]]
        result, reasons = _classify(rule, link)
        counts[result] += 1
        output.append({
            "rule_id": rule["rule_id"],
            "subject": rule["subject"],
            "rule_kind": rule["rule_kind"],
            "downstream_effect": rule["downstream_effect"],
            "condition_text_sha256": rule["condition_text_sha256"],
            "definition_status": rule["definition_status"],
            "data_status": rule["data_status"],
            "source_qualification": rule["source_qualification"],
            "rule_ssot_evaluator_status": rule["evaluator_status"],
            "rule_ssot_blocked_by": copy.deepcopy(rule["blocked_by"]),
            "link_status": link["link_status"],
            "link_reasons": copy.deepcopy(link["link_reasons"]),
            "evidence_reference_set_sha256": payload_sha256(
                link["evidence_references"]
            ),
            "result": result,
            "reasons": reasons,
            "evaluation_spec_sha256": None,
        })
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "BOUNDARY_CLASSIFIED_PASS_FAIL_NOT_AUTHORIZED",
        "binding_set_id": checked["binding_set_id"],
        "summary": {"total_rules": len(output), **counts},
        "rules": output,
        "lineage": {
            "rule_registry_sha256": payload_sha256(rules),
            "binding_packet_sha256": checked["packet_sha256"],
            "binding_set_sha256": checked["inputs"]["binding_set_sha256"],
            "evidence_set_sha256": checked["inputs"]["evidence_set_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "P5_02_RULE_SOURCE_FRESHNESS_THRESHOLD_POLICY_BLOCKED",
            "RULE_REGISTRY_CONSUMABLE_BY_EVALUATOR_FALSE",
            "EVALUATION_SPEC_NOT_IMPLEMENTED",
            "PASS_FAIL_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, rules, contract)


def validate_packet(
    packet: dict,
    rules: dict | None = None,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    try:
        rules = _validate_rules(rules) if rules is not None else load_rules()
    except DeterministicRuleEvaluatorError as exc:
        raise DeterministicRuleEvaluatorError(
            f"RULE_REGISTRY_AUTHORITY_INVALID:{exc}"
        ) from exc
    fields = {
        "schema_version", "contract_version", "status", "binding_set_id",
        "summary", "rules", "lineage", "authority", "unresolved_boundaries",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise DeterministicRuleEvaluatorError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "BOUNDARY_CLASSIFIED_PASS_FAIL_NOT_AUTHORIZED"
        or packet.get("authority") != contract["authority"]
    ):
        raise DeterministicRuleEvaluatorError("OUTPUT_IDENTITY_INVALID")
    rows = packet.get("rules")
    if not isinstance(rows, list) or len(rows) != contract["canonical_rule_count"]:
        raise DeterministicRuleEvaluatorError("OUTPUT_RULE_COUNT_INVALID")
    registry = {rule["rule_id"]: rule for rule in rules["rules"]}
    row_fields = {
        "rule_id", "subject", "rule_kind", "downstream_effect",
        "condition_text_sha256", "definition_status", "data_status",
        "source_qualification", "rule_ssot_evaluator_status",
        "rule_ssot_blocked_by", "link_status", "link_reasons",
        "evidence_reference_set_sha256", "result", "reasons",
        "evaluation_spec_sha256",
    }
    counts = {status: 0 for status in RESULT_STATUSES}
    checked_ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise DeterministicRuleEvaluatorError("OUTPUT_RULE_FIELDS_MISMATCH")
        rule_id = row.get("rule_id")
        rule = registry.get(rule_id)
        if rule is None:
            raise DeterministicRuleEvaluatorError(f"OUTPUT_RULE_UNKNOWN:{rule_id}")
        static = {
            "subject": rule["subject"],
            "rule_kind": rule["rule_kind"],
            "downstream_effect": rule["downstream_effect"],
            "condition_text_sha256": rule["condition_text_sha256"],
            "definition_status": rule["definition_status"],
            "data_status": rule["data_status"],
            "source_qualification": rule["source_qualification"],
            "rule_ssot_evaluator_status": rule["evaluator_status"],
            "rule_ssot_blocked_by": rule["blocked_by"],
        }
        if any(row.get(key) != value for key, value in static.items()):
            raise DeterministicRuleEvaluatorError(
                f"OUTPUT_RULE_REGISTRY_MISMATCH:{rule_id}"
            )
        if (
            row.get("link_status") not in LINK_STATUSES
            or not isinstance(row.get("link_reasons"), list)
            or row.get("evaluation_spec_sha256") is not None
        ):
            raise DeterministicRuleEvaluatorError(f"OUTPUT_RULE_VALUE_INVALID:{rule_id}")
        _sha(
            row.get("evidence_reference_set_sha256"),
            f"OUTPUT_EVIDENCE_SET_SHA_INVALID:{rule_id}",
        )
        expected_result, expected_reasons = _classify(rule, row)
        if row.get("result") != expected_result or row.get("reasons") != expected_reasons:
            raise DeterministicRuleEvaluatorError(
                f"OUTPUT_RULE_DERIVATION_MISMATCH:{rule_id}"
            )
        counts[expected_result] += 1
        checked_ids.append(rule_id)
    if checked_ids != sorted(registry):
        raise DeterministicRuleEvaluatorError("OUTPUT_RULE_ORDER_INVALID")
    if packet.get("summary") != {"total_rules": len(rows), **counts}:
        raise DeterministicRuleEvaluatorError("OUTPUT_SUMMARY_MISMATCH")
    lineage = packet.get("lineage")
    lineage_fields = {
        "rule_registry_sha256", "binding_packet_sha256", "binding_set_sha256",
        "evidence_set_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise DeterministicRuleEvaluatorError("OUTPUT_LINEAGE_FIELDS_MISMATCH")
    if lineage.get("rule_registry_sha256") != payload_sha256(rules):
        raise DeterministicRuleEvaluatorError("OUTPUT_RULE_REGISTRY_SHA_MISMATCH")
    for key in lineage_fields - {"rule_registry_sha256"}:
        _sha(lineage.get(key), f"OUTPUT_LINEAGE_SHA_INVALID:{key}")
    if packet.get("unresolved_boundaries") != [
        "P5_02_RULE_SOURCE_FRESHNESS_THRESHOLD_POLICY_BLOCKED",
        "RULE_REGISTRY_CONSUMABLE_BY_EVALUATOR_FALSE",
        "EVALUATION_SPEC_NOT_IMPLEMENTED",
        "PASS_FAIL_NOT_AUTHORIZED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]:
        raise DeterministicRuleEvaluatorError("OUTPUT_BOUNDARIES_MISMATCH")
    digest = _sha(packet.get("packet_sha256"), "OUTPUT_PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise DeterministicRuleEvaluatorError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise DeterministicRuleEvaluatorError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(binding_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(binding_path)))
        return 0
    except (DeterministicRuleEvaluatorError, OSError, TypeError, ValueError) as exc:
        print(f"Deterministic Rule evaluator failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify Rule UNKNOWN/UNDEFINED boundaries"
    )
    parser.add_argument("binding_packet", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.binding_packet, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
