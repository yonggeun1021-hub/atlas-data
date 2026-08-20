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

PACKET_SCHEMA_VERSION = "rule_evidence_binding_packet/1"
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


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
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
    if value.get("contract_version") != "rule_evidence_binding/1":
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
    return value


def load_rules(path: Path = RULES_PATH) -> dict:
    value = _read_json(path)
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
        seen.add(rule_id)
    return value


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


def build_packet(
    *, envelopes: list[dict], bindings: dict, rules: dict | None = None,
    contract: dict | None = None,
) -> dict:
    """Build a deterministic linkage packet from explicit bindings only."""
    contract = copy.deepcopy(contract or load_contract())
    rules = copy.deepcopy(rules or load_rules())
    if len(rules["rules"]) != contract["canonical_rule_count"]:
        raise RuleEvidenceBindingError("RULE_COUNT_DOES_NOT_MATCH_CONTRACT")
    rules_by_id = {rule["rule_id"]: rule for rule in rules["rules"]}
    envelope_index = _index_envelopes(envelopes)
    binding_index = _validate_bindings(bindings, rules_by_id)

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
        "summary": {"total_rules": len(records), **counts},
        "rules": records,
    }
    body["packet_sha256"] = payload_sha256(body)
    return body


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
