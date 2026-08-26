#!/usr/bin/env python3
"""P5-03 Rule ↔ Evidence Envelope lineage binding.

This layer records which explicitly named evidence envelopes are attached to each
canonical Rule and preserves the evidence period, availability time, retrieval time,
source identity, and immutable hashes.  It never discovers a source, chooses among
sources, interprets an observation, or evaluates a Rule.

Missing bindings remain unresolved.  Missing or conflicting lineage blocks the link.
Neither state is converted into a Rule result.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "rule_evidence_binding_contract.json"
RULES_PATH = ROOT / "config" / "rules.json"

PACKET_SCHEMA_VERSION = "rule_evidence_binding_packet/2"
BINDING_SCHEMA_VERSION = "rule_evidence_bindings/1"
ENVELOPE_SCHEMA_VERSION = "evidence_envelope/1"

LINK_AVAILABLE = "LINK_AVAILABLE"
LINK_BLOCKED = "LINK_BLOCKED"
LINK_UNRESOLVED = "LINK_UNRESOLVED"
LINK_STATUSES = (LINK_AVAILABLE, LINK_BLOCKED, LINK_UNRESOLVED)

EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"
EVIDENCE_BLOCKED = "EVIDENCE_BLOCKED"
EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"
EVIDENCE_STATUSES = (EVIDENCE_AVAILABLE, EVIDENCE_BLOCKED, EVIDENCE_UNRESOLVED)

EVALUATION_NOT_AUTHORIZED = "EVALUATION_NOT_AUTHORIZED"
EXPLICIT_BINDING_ABSENT = "EXPLICIT_BINDING_ABSENT"
EVIDENCE_REFERENCE_ABSENT = "EVIDENCE_REFERENCE_ABSENT"
EVIDENCE_LINEAGE_INCOMPLETE = "EVIDENCE_LINEAGE_INCOMPLETE"
EVIDENCE_STATE_INCONSISTENT = "EVIDENCE_STATE_INCONSISTENT"

RULE_ID_RE = re.compile(r"^RULE-\d{4}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RuleEvidenceBindingError(ValueError):
    """Fail-closed Rule–Evidence binding contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleEvidenceBindingError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_authority = {
        "linkage_only": True,
        "automatic_source_selection_authorized": False,
        "interpretation_authorized": False,
        "rule_evaluation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RuleEvidenceBindingError("CONTRACT_SCHEMA_MISMATCH")
    if value.get("contract_version") != "rule_evidence_binding/2":
        raise RuleEvidenceBindingError("CONTRACT_VERSION_MISMATCH")
    if value.get("canonical_rule_registry") != "config/rules.json":
        raise RuleEvidenceBindingError("CANONICAL_RULE_REGISTRY_MISMATCH")
    if value.get("canonical_rule_count") != 25:
        raise RuleEvidenceBindingError("CANONICAL_RULE_COUNT_MISMATCH")
    if value.get("selection_mode") != "ALL_REQUIRED":
        raise RuleEvidenceBindingError("SELECTION_MODE_MUST_BE_ALL_REQUIRED")
    if value.get("source_hierarchy_status") != "UNRATIFIED":
        raise RuleEvidenceBindingError("SOURCE_HIERARCHY_MUST_REMAIN_UNRATIFIED")
    if value.get("automatic_binding_authorized") is not False:
        raise RuleEvidenceBindingError("AUTOMATIC_BINDING_MUST_REMAIN_FALSE")
    if value.get("link_statuses") != list(LINK_STATUSES):
        raise RuleEvidenceBindingError("LINK_STATUS_REGISTRY_MISMATCH")
    required = value.get("required_lineage_fields")
    if required != [
        "source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"
    ]:
        raise RuleEvidenceBindingError("REQUIRED_LINEAGE_FIELDS_MISMATCH")
    if value.get("authority") != expected_authority:
        raise RuleEvidenceBindingError("AUTHORITY_BOUNDARY_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(path))


def _validate_rules(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("artifact") != "Rule SSOT (config/rules.json)":
        raise RuleEvidenceBindingError("RULE_SSOT_IDENTITY_MISMATCH")
    if value.get("authority") is not True or value.get("consumable_by_evaluator") is not False:
        raise RuleEvidenceBindingError("RULE_SSOT_AUTHORITY_BOUNDARY_MISMATCH")
    rules = value.get("rules")
    if not isinstance(rules, list) or value.get("rule_count") != len(rules):
        raise RuleEvidenceBindingError("RULE_SSOT_COUNT_MISMATCH")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise RuleEvidenceBindingError("RULE_NOT_OBJECT")
        rule_id = rule.get("rule_id")
        subject = rule.get("subject")
        if not isinstance(rule_id, str) or RULE_ID_RE.fullmatch(rule_id) is None:
            raise RuleEvidenceBindingError(f"RULE_ID_INVALID:{rule_id!r}")
        if rule_id in seen:
            raise RuleEvidenceBindingError(f"RULE_ID_DUPLICATE:{rule_id}")
        if not isinstance(subject, str) or not subject:
            raise RuleEvidenceBindingError(f"RULE_SUBJECT_INVALID:{rule_id}")
        condition_sha = rule.get("condition_text_sha256")
        if (
            not isinstance(condition_sha, str)
            or SHA256_RE.fullmatch(condition_sha) is None
        ):
            raise RuleEvidenceBindingError(f"RULE_CONDITION_SHA_INVALID:{rule_id}")
        seen.add(rule_id)
    return copy.deepcopy(value)


def load_rules(path: Path = RULES_PATH) -> dict:
    return _validate_rules(_read_json(path))


def _valid_date(value) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_utc(value) -> bool:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return False
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ) == value
    except ValueError:
        return False


def _valid_available_at(value) -> bool:
    return _valid_date(value) or _valid_utc(value)


def _key(value: dict) -> tuple[str, str, str]:
    return (
        value.get("subject"),
        value.get("measurement_identity"),
        value.get("economic_period_end"),
    )


def _validate_key(key: tuple, label: str) -> None:
    if not all(isinstance(item, str) and item for item in key):
        raise RuleEvidenceBindingError(f"{label}_KEY_INVALID:{key!r}")
    if not _valid_date(key[2]):
        raise RuleEvidenceBindingError(f"{label}_AS_OF_INVALID:{key[2]!r}")


def _index_envelopes(envelopes: list[dict]) -> dict[tuple[str, str, str], dict]:
    if not isinstance(envelopes, list):
        raise RuleEvidenceBindingError("ENVELOPES_NOT_LIST")
    indexed = {}
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            raise RuleEvidenceBindingError("ENVELOPE_NOT_OBJECT")
        if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
            raise RuleEvidenceBindingError("ENVELOPE_SCHEMA_MISMATCH")
        if envelope.get("status") not in EVIDENCE_STATUSES:
            raise RuleEvidenceBindingError("ENVELOPE_STATUS_INVALID")
        key = _key(envelope)
        _validate_key(key, "ENVELOPE")
        if key in indexed:
            raise RuleEvidenceBindingError(f"ENVELOPE_KEY_DUPLICATE:{key!r}")
        indexed[key] = copy.deepcopy(envelope)
    return indexed


def _validate_bindings(bindings: dict, rules_by_id: dict[str, dict]) -> dict[str, list[tuple]]:
    if not isinstance(bindings, dict) or bindings.get("schema_version") != (
        BINDING_SCHEMA_VERSION
    ):
        raise RuleEvidenceBindingError("BINDING_SCHEMA_MISMATCH")
    binding_set_id = bindings.get("binding_set_id")
    if not isinstance(binding_set_id, str) or not binding_set_id:
        raise RuleEvidenceBindingError("BINDING_SET_ID_INVALID")
    rows = bindings.get("bindings")
    if not isinstance(rows, list):
        raise RuleEvidenceBindingError("BINDINGS_NOT_LIST")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuleEvidenceBindingError("BINDING_NOT_OBJECT")
        rule_id = row.get("rule_id")
        if rule_id not in rules_by_id:
            raise RuleEvidenceBindingError(f"BINDING_RULE_UNKNOWN:{rule_id!r}")
        if rule_id in indexed:
            raise RuleEvidenceBindingError(f"BINDING_RULE_DUPLICATE:{rule_id}")
        if row.get("selection_mode") != "ALL_REQUIRED":
            raise RuleEvidenceBindingError(f"BINDING_SELECTION_MODE_INVALID:{rule_id}")
        refs = row.get("evidence_keys")
        if not isinstance(refs, list) or not refs:
            raise RuleEvidenceBindingError(f"BINDING_EVIDENCE_KEYS_EMPTY:{rule_id}")
        rule_subject = rules_by_id[rule_id]["subject"]
        keys = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {
                "subject", "measurement_identity", "economic_period_end"
            }:
                raise RuleEvidenceBindingError(f"BINDING_EVIDENCE_KEY_INVALID:{rule_id}")
            key = _key(ref)
            _validate_key(key, "BINDING")
            if key[0] != rule_subject:
                raise RuleEvidenceBindingError(
                    f"BINDING_SUBJECT_MISMATCH:{rule_id}:{key[0]}:{rule_subject}"
                )
            if key in keys:
                raise RuleEvidenceBindingError(f"BINDING_EVIDENCE_KEY_DUPLICATE:{rule_id}")
            keys.append(key)
        indexed[rule_id] = sorted(keys)
    return indexed


def _lineage(envelope: dict, required_fields: list[str]) -> tuple[dict, list[str]]:
    source = envelope.get("source_identity")
    source = source if isinstance(source, dict) else {}
    lineage = {
        "evidence_as_of": envelope.get("economic_period_end"),
        "available_at": source.get("available_at"),
        "retrieved_at_utc": source.get("retrieved_at_utc"),
        "source_id": source.get("source_id"),
        "source_url": source.get("source_url"),
        "source_sha256": source.get("source_sha256"),
        "envelope_sha256": payload_sha256(envelope),
    }
    missing = [field for field in required_fields if not source.get(field)]
    if source.get("source_sha256") and SHA256_RE.fullmatch(source["source_sha256"]) is None:
        missing.append("source_sha256:INVALID")
    if source.get("available_at") and not _valid_available_at(source["available_at"]):
        missing.append("available_at:INVALID")
    if source.get("retrieved_at_utc") and not _valid_utc(source["retrieved_at_utc"]):
        missing.append("retrieved_at_utc:INVALID")
    if not _valid_date(lineage["evidence_as_of"]):
        missing.append("evidence_as_of:INVALID")
    return lineage, sorted(set(missing))


def _reference_record(key: tuple, envelope: dict | None, required_fields: list[str]) -> dict:
    base = {
        "subject": key[0],
        "measurement_identity": key[1],
        "economic_period_end": key[2],
    }
    if envelope is None:
        return {
            **base,
            "evidence_status": EVIDENCE_UNRESOLVED,
            "reference_status": LINK_UNRESOLVED,
            "reasons": [EVIDENCE_REFERENCE_ABSENT],
            "blocked_by": [],
            "lineage": None,
        }

    lineage, missing = _lineage(envelope, required_fields)
    status = envelope["status"]
    reasons = list(envelope.get("reasons") or [])
    blocked_by = list(envelope.get("blocked_by") or [])
    inconsistent = (
        (status == EVIDENCE_AVAILABLE and envelope.get("consumable") is not True)
        or (status != EVIDENCE_AVAILABLE and envelope.get("consumable") is True)
    )
    if inconsistent:
        blocked_by.append(EVIDENCE_STATE_INCONSISTENT)
    if missing and status != EVIDENCE_UNRESOLVED:
        blocked_by.append(EVIDENCE_LINEAGE_INCOMPLETE)
        reasons.append(f"{EVIDENCE_LINEAGE_INCOMPLETE}:{','.join(missing)}")

    if blocked_by or status == EVIDENCE_BLOCKED:
        reference_status = LINK_BLOCKED
    elif status == EVIDENCE_UNRESOLVED:
        reference_status = LINK_UNRESOLVED
    else:
        reference_status = LINK_AVAILABLE
    return {
        **base,
        "evidence_status": status,
        "reference_status": reference_status,
        "reasons": list(dict.fromkeys(reasons)),
        "blocked_by": list(dict.fromkeys(blocked_by)),
        "lineage": lineage,
    }


def _validated_string_list(value, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != list(dict.fromkeys(value))
    ):
        raise RuleEvidenceBindingError(f"{label}_INVALID")
    return value


def _validate_reference(
    reference: dict, *, rule_subject: str, required_fields: list[str]
) -> tuple[tuple[str, str, str], list[str]]:
    fields = {
        "subject", "measurement_identity", "economic_period_end",
        "evidence_status", "reference_status", "reasons", "blocked_by",
        "lineage",
    }
    if not isinstance(reference, dict) or set(reference) != fields:
        raise RuleEvidenceBindingError("PACKET_REFERENCE_FIELDS_MISMATCH")
    key = _key(reference)
    _validate_key(key, "PACKET_REFERENCE")
    if key[0] != rule_subject:
        raise RuleEvidenceBindingError("PACKET_REFERENCE_SUBJECT_MISMATCH")
    evidence_status = reference.get("evidence_status")
    reference_status = reference.get("reference_status")
    if evidence_status not in EVIDENCE_STATUSES or reference_status not in LINK_STATUSES:
        raise RuleEvidenceBindingError("PACKET_REFERENCE_STATUS_INVALID")
    reasons = _validated_string_list(reference.get("reasons"), "PACKET_REFERENCE_REASONS")
    blocked_by = _validated_string_list(
        reference.get("blocked_by"), "PACKET_REFERENCE_BLOCKED_BY"
    )
    lineage = reference.get("lineage")
    if lineage is None:
        if (
            evidence_status != EVIDENCE_UNRESOLVED
            or reference_status != LINK_UNRESOLVED
            or reasons != [EVIDENCE_REFERENCE_ABSENT]
            or blocked_by
        ):
            raise RuleEvidenceBindingError("PACKET_REFERENCE_ABSENT_STATE_MISMATCH")
        return key, reasons

    lineage_fields = {
        "evidence_as_of", "available_at", "retrieved_at_utc", "source_id",
        "source_url", "source_sha256", "envelope_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise RuleEvidenceBindingError("PACKET_LINEAGE_FIELDS_MISMATCH")
    if (
        lineage.get("evidence_as_of") != key[2]
        or not isinstance(lineage.get("envelope_sha256"), str)
        or SHA256_RE.fullmatch(lineage["envelope_sha256"]) is None
    ):
        raise RuleEvidenceBindingError("PACKET_LINEAGE_IDENTITY_INVALID")

    missing = []
    for field in required_fields:
        value = lineage.get(field)
        if not value:
            missing.append(field)
    source_id = lineage.get("source_id")
    source_url = lineage.get("source_url")
    source_sha = lineage.get("source_sha256")
    available_at = lineage.get("available_at")
    retrieved_at = lineage.get("retrieved_at_utc")
    if source_id is not None and (
        not isinstance(source_id, str) or not source_id
    ):
        raise RuleEvidenceBindingError("PACKET_LINEAGE_SOURCE_ID_INVALID")
    if source_url is not None and (
        not isinstance(source_url, str) or not source_url
    ):
        raise RuleEvidenceBindingError("PACKET_LINEAGE_SOURCE_URL_INVALID")
    if source_sha and (
        not isinstance(source_sha, str) or SHA256_RE.fullmatch(source_sha) is None
    ):
        missing.append("source_sha256:INVALID")
    if available_at and not _valid_available_at(available_at):
        missing.append("available_at:INVALID")
    if retrieved_at and not _valid_utc(retrieved_at):
        missing.append("retrieved_at_utc:INVALID")
    missing = sorted(set(missing))
    lineage_reason = (
        f"{EVIDENCE_LINEAGE_INCOMPLETE}:{','.join(missing)}" if missing else None
    )
    if evidence_status != EVIDENCE_UNRESOLVED and missing:
        if (
            EVIDENCE_LINEAGE_INCOMPLETE not in blocked_by
            or lineage_reason not in reasons
        ):
            raise RuleEvidenceBindingError("PACKET_LINEAGE_BLOCKER_MISMATCH")
    elif EVIDENCE_LINEAGE_INCOMPLETE in blocked_by or any(
        reason.startswith(f"{EVIDENCE_LINEAGE_INCOMPLETE}:") for reason in reasons
    ):
        raise RuleEvidenceBindingError("PACKET_LINEAGE_BLOCKER_MISMATCH")

    if evidence_status == EVIDENCE_AVAILABLE:
        allowed_reasons = [] if lineage_reason is None else [lineage_reason]
        if reasons != allowed_reasons:
            raise RuleEvidenceBindingError("PACKET_AVAILABLE_REASON_MISMATCH")
    elif evidence_status == EVIDENCE_BLOCKED and not (reasons or blocked_by):
        raise RuleEvidenceBindingError("PACKET_BLOCKED_REASON_ABSENT")

    expected_status = (
        LINK_BLOCKED
        if blocked_by or evidence_status == EVIDENCE_BLOCKED
        else LINK_UNRESOLVED
        if evidence_status == EVIDENCE_UNRESOLVED
        else LINK_AVAILABLE
    )
    if reference_status != expected_status:
        raise RuleEvidenceBindingError("PACKET_REFERENCE_STATE_MISMATCH")
    return key, reasons


def validate_packet(
    packet: dict,
    rules: dict | None = None,
    contract: dict | None = None,
) -> dict:
    """Validate a persisted linkage packet without selecting or evaluating evidence."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    rules = _validate_rules(rules) if rules is not None else load_rules()
    if len(rules["rules"]) != contract["canonical_rule_count"]:
        raise RuleEvidenceBindingError("RULE_COUNT_DOES_NOT_MATCH_CONTRACT")
    fields = {
        "schema_version", "contract_version", "binding_set_id",
        "source_hierarchy_status", "automatic_binding_authorized", "authority",
        "inputs", "frozen_evidence_envelopes", "summary", "rules", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise RuleEvidenceBindingError("PACKET_FIELDS_MISMATCH")
    digest = packet.get("packet_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if (
        not isinstance(digest, str)
        or SHA256_RE.fullmatch(digest) is None
        or payload_sha256(unsigned) != digest
    ):
        raise RuleEvidenceBindingError("PACKET_SHA256_MISMATCH")
    binding_set_id = packet.get("binding_set_id")
    if (
        packet.get("schema_version") != PACKET_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
        or not isinstance(binding_set_id, str)
        or not binding_set_id
        or packet.get("source_hierarchy_status")
        != contract["source_hierarchy_status"]
        or packet.get("automatic_binding_authorized")
        is not contract["automatic_binding_authorized"]
        or packet.get("authority") != contract["authority"]
    ):
        raise RuleEvidenceBindingError("PACKET_IDENTITY_MISMATCH")
    inputs = packet.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {
        "rule_registry_sha256", "binding_set_sha256", "evidence_set_sha256"
    }:
        raise RuleEvidenceBindingError("PACKET_INPUTS_MISMATCH")
    if inputs.get("rule_registry_sha256") != payload_sha256(rules):
        raise RuleEvidenceBindingError("PACKET_RULE_REGISTRY_SHA_MISMATCH")
    if any(
        not isinstance(inputs.get(field), str)
        or SHA256_RE.fullmatch(inputs[field]) is None
        for field in ("binding_set_sha256", "evidence_set_sha256")
    ):
        raise RuleEvidenceBindingError("PACKET_INPUT_SHA_INVALID")

    frozen_envelopes = packet.get("frozen_evidence_envelopes")
    envelope_index = _index_envelopes(frozen_envelopes)
    if [_key(item) for item in frozen_envelopes] != sorted(envelope_index):
        raise RuleEvidenceBindingError("PACKET_FROZEN_ENVELOPE_ORDER_INVALID")
    if inputs["evidence_set_sha256"] != payload_sha256(frozen_envelopes):
        raise RuleEvidenceBindingError("PACKET_EVIDENCE_SET_SHA_MISMATCH")

    rows = packet.get("rules")
    registry = {rule["rule_id"]: rule for rule in rules["rules"]}
    if not isinstance(rows, list) or len(rows) != len(registry):
        raise RuleEvidenceBindingError("PACKET_RULE_COUNT_MISMATCH")
    row_fields = {
        "rule_id", "subject", "condition_text_sha256", "link_status",
        "link_reasons", "selection_mode", "evidence_references",
        "evaluation_status", "rule_result",
    }
    counts = {status: 0 for status in LINK_STATUSES}
    normalized_bindings = []
    checked_ids = []
    referenced_frozen_keys = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise RuleEvidenceBindingError("PACKET_RULE_FIELDS_MISMATCH")
        rule_id = row.get("rule_id")
        rule = registry.get(rule_id)
        refs = row.get("evidence_references")
        link_status = row.get("link_status")
        if (
            rule is None
            or row.get("subject") != rule["subject"]
            or row.get("condition_text_sha256") != rule.get("condition_text_sha256")
            or link_status not in LINK_STATUSES
            or not isinstance(refs, list)
            or row.get("evaluation_status") != EVALUATION_NOT_AUTHORIZED
            or row.get("rule_result") is not None
        ):
            raise RuleEvidenceBindingError(f"PACKET_RULE_STATIC_MISMATCH:{rule_id}")
        link_reasons = _validated_string_list(
            row.get("link_reasons"), "PACKET_LINK_REASONS"
        )
        checked_refs = [
            _validate_reference(
                ref,
                rule_subject=rule["subject"],
                required_fields=contract["required_lineage_fields"],
            )
            for ref in refs
        ]
        for ref in refs:
            key = _key(ref)
            frozen = envelope_index.get(key)
            if frozen is None:
                if ref["lineage"] is not None:
                    raise RuleEvidenceBindingError("PACKET_FROZEN_ENVELOPE_MISSING")
                continue
            referenced_frozen_keys.add(key)
            expected_ref = _reference_record(
                key, frozen, contract["required_lineage_fields"]
            )
            if ref != expected_ref:
                raise RuleEvidenceBindingError(
                    "PACKET_FROZEN_ENVELOPE_DERIVATION_MISMATCH"
                )
        ref_keys = [item[0] for item in checked_refs]
        if ref_keys != sorted(set(ref_keys)):
            raise RuleEvidenceBindingError("PACKET_REFERENCE_ORDER_INVALID")
        if not refs:
            if (
                row.get("selection_mode") is not None
                or link_status != LINK_UNRESOLVED
                or link_reasons != [EXPLICIT_BINDING_ABSENT]
            ):
                raise RuleEvidenceBindingError("PACKET_UNBOUND_RULE_MISMATCH")
        else:
            expected_statuses = {ref["reference_status"] for ref in refs}
            expected_status = (
                LINK_BLOCKED
                if LINK_BLOCKED in expected_statuses
                else LINK_UNRESOLVED
                if LINK_UNRESOLVED in expected_statuses
                else LINK_AVAILABLE
            )
            expected_reasons = list(dict.fromkeys(
                reason for _, reasons in checked_refs for reason in reasons
            ))
            if (
                row.get("selection_mode") != "ALL_REQUIRED"
                or link_status != expected_status
                or link_reasons != expected_reasons
            ):
                raise RuleEvidenceBindingError("PACKET_BOUND_RULE_DERIVATION_MISMATCH")
            normalized_bindings.append({
                "rule_id": rule_id,
                "selection_mode": "ALL_REQUIRED",
                "evidence_keys": [
                    {
                        "subject": key[0],
                        "measurement_identity": key[1],
                        "economic_period_end": key[2],
                    }
                    for key in ref_keys
                ],
            })
        counts[link_status] += 1
        checked_ids.append(rule_id)
    if checked_ids != sorted(registry):
        raise RuleEvidenceBindingError("PACKET_RULE_ORDER_INVALID")
    if referenced_frozen_keys != set(envelope_index):
        raise RuleEvidenceBindingError("PACKET_UNREFERENCED_FROZEN_ENVELOPE")
    expected_binding_doc = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "binding_set_id": binding_set_id,
        "bindings": normalized_bindings,
    }
    if inputs["binding_set_sha256"] != payload_sha256(expected_binding_doc):
        raise RuleEvidenceBindingError("PACKET_BINDING_SET_SHA_MISMATCH")
    if packet.get("summary") != {"total_rules": len(rows), **counts}:
        raise RuleEvidenceBindingError("PACKET_SUMMARY_MISMATCH")
    return copy.deepcopy(packet)


def build_packet(
    *, envelopes: list[dict], bindings: dict, rules: dict | None = None,
    contract: dict | None = None,
) -> dict:
    """Build a deterministic linkage packet from explicit bindings only."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    rules = _validate_rules(rules) if rules is not None else load_rules()
    if len(rules["rules"]) != contract["canonical_rule_count"]:
        raise RuleEvidenceBindingError("RULE_COUNT_DOES_NOT_MATCH_CONTRACT")
    rules_by_id = {rule["rule_id"]: rule for rule in rules["rules"]}
    envelope_index = _index_envelopes(envelopes)
    binding_index = _validate_bindings(bindings, rules_by_id)
    referenced_keys = {
        key for keys in binding_index.values() for key in keys if key in envelope_index
    }
    if referenced_keys != set(envelope_index):
        raise RuleEvidenceBindingError("UNREFERENCED_ENVELOPE")

    records = []
    counts = {status: 0 for status in LINK_STATUSES}
    for rule_id in sorted(rules_by_id):
        rule = rules_by_id[rule_id]
        keys = binding_index.get(rule_id)
        if keys is None:
            link_status = LINK_UNRESOLVED
            reasons = [EXPLICIT_BINDING_ABSENT]
            refs = []
            selection_mode = None
        else:
            refs = [
                _reference_record(key, envelope_index.get(key), contract["required_lineage_fields"])
                for key in keys
            ]
            ref_statuses = {ref["reference_status"] for ref in refs}
            if LINK_BLOCKED in ref_statuses:
                link_status = LINK_BLOCKED
            elif LINK_UNRESOLVED in ref_statuses:
                link_status = LINK_UNRESOLVED
            else:
                link_status = LINK_AVAILABLE
            reasons = list(dict.fromkeys(
                reason for ref in refs for reason in ref["reasons"]
            ))
            selection_mode = "ALL_REQUIRED"
        counts[link_status] += 1
        records.append({
            "rule_id": rule_id,
            "subject": rule["subject"],
            "condition_text_sha256": rule.get("condition_text_sha256"),
            "link_status": link_status,
            "link_reasons": reasons,
            "selection_mode": selection_mode,
            "evidence_references": refs,
            "evaluation_status": EVALUATION_NOT_AUTHORIZED,
            "rule_result": None,
        })

    sorted_envelopes = [envelope_index[key] for key in sorted(envelope_index)]
    normalized_bindings = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "binding_set_id": bindings["binding_set_id"],
        "bindings": [
            {
                "rule_id": rule_id,
                "selection_mode": "ALL_REQUIRED",
                "evidence_keys": [
                    {
                        "subject": key[0],
                        "measurement_identity": key[1],
                        "economic_period_end": key[2],
                    }
                    for key in binding_index[rule_id]
                ],
            }
            for rule_id in sorted(binding_index)
        ],
    }
    body = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "binding_set_id": bindings["binding_set_id"],
        "source_hierarchy_status": contract["source_hierarchy_status"],
        "automatic_binding_authorized": contract["automatic_binding_authorized"],
        "authority": copy.deepcopy(contract["authority"]),
        "inputs": {
            "rule_registry_sha256": payload_sha256(rules),
            "binding_set_sha256": payload_sha256(normalized_bindings),
            "evidence_set_sha256": payload_sha256(sorted_envelopes),
        },
        "frozen_evidence_envelopes": sorted_envelopes,
        "summary": {"total_rules": len(records), **counts},
        "rules": records,
    }
    body["packet_sha256"] = payload_sha256(body)
    return validate_packet(body, rules, contract)


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an evidence-only Rule linkage packet")
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--envelopes", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    envelope_input = _read_json(args.envelopes)
    if isinstance(envelope_input, dict):
        envelope_input = envelope_input.get("envelopes")
    packet = build_packet(
        envelopes=envelope_input,
        bindings=_read_json(args.bindings),
        rules=load_rules(args.rules),
        contract=load_contract(args.contract),
    )
    _atomic_write_json(args.out, packet)
    return 0


def main() -> int:
    try:
        return run()
    except RuleEvidenceBindingError as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
