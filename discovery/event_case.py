#!/usr/bin/env python3
"""P3-08 evidence-linked Discovery Case packet for existing SEC D1 events.

The module turns only already-classified SEC D1 event types into deterministic case
records.  It does not decide that an event is important, interpret its direction,
promote a security's Atlas stage, evaluate a Rule, or create an action/order.

Evidence is attached only through an explicit source-record-key binding.  Missing
evidence remains unresolved and incomplete lineage is blocked.
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
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DECISION_DIR = ROOT / "decision"
sys.path.insert(0, str(DECISION_DIR))

import event_classifier as D1                                      # noqa: E402


CONTRACT_PATH = ROOT / "config" / "event_discovery_case_contract.json"
CONTRACT_SCHEMA_VERSION = 1
CASE_SCHEMA_VERSION = "discovery_case/1"
PACKET_SCHEMA_VERSION = "event_discovery_case_packet/1"
BINDING_SCHEMA_VERSION = "event_case_evidence_bindings/1"
EVIDENCE_SCHEMA_VERSION = "event_source_evidence/1"

EVIDENCE_LINKED = "EVIDENCE_LINKED"
EVIDENCE_BLOCKED = "EVIDENCE_BLOCKED"
EVIDENCE_UNRESOLVED = "EVIDENCE_UNRESOLVED"
EVIDENCE_STATUSES = (EVIDENCE_LINKED, EVIDENCE_BLOCKED, EVIDENCE_UNRESOLVED)

IMPORTANCE_UNRATIFIED = "IMPORTANCE_UNRATIFIED"
INTERPRETATION_NOT_AUTHORIZED = "INTERPRETATION_NOT_AUTHORIZED"
PROMOTION_NOT_AUTHORIZED = "PROMOTION_NOT_AUTHORIZED"

EXPLICIT_EVIDENCE_BINDING_ABSENT = "EXPLICIT_EVIDENCE_BINDING_ABSENT"
EVIDENCE_LINEAGE_INCOMPLETE = "EVIDENCE_LINEAGE_INCOMPLETE"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")


class EventCaseError(ValueError):
    """Fail-closed event-case contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventCaseError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    try:
        with path.open(encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise EventCaseError(f"JSONL_INVALID:{path}:{number}:{exc}") from exc
                if not isinstance(row, dict):
                    raise EventCaseError(f"JSONL_ROW_NOT_OBJECT:{path}:{number}")
                rows.append(row)
    except OSError as exc:
        raise EventCaseError(f"JSONL_READ_FAILED:{path}:{exc}") from exc
    return rows


def _validate_contract(value: dict) -> dict:
    expected_source = {
        "module": "decision/event_classifier.py",
        "taxonomy_version": D1.TAXONOMY_VERSION,
        "decision_version": D1.DECISION_VERSION,
        "supported_resolutions": ["resolved", "partial"],
    }
    expected_coverage = {
        "sec_edgar": "CLASSIFICATION_SUPPORTED",
        "dart_open_api": "ITEM_EXTRACTION_UNRATIFIED",
        "news": "NOT_IMPLEMENTED",
        "policy": "NOT_IMPLEMENTED",
        "crypto": "NOT_IMPLEMENTED",
    }
    expected_authority = {
        "case_recording_only": True,
        "importance_ranking_authorized": False,
        "interpretation_authorized": False,
        "stage_promotion_authorized": False,
        "rule_evaluation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if not isinstance(value, dict) or value.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise EventCaseError("CONTRACT_SCHEMA_MISMATCH")
    if value.get("contract_version") != "event_discovery_case/1":
        raise EventCaseError("CONTRACT_VERSION_MISMATCH")
    if value.get("case_schema_version") != CASE_SCHEMA_VERSION:
        raise EventCaseError("CASE_SCHEMA_VERSION_MISMATCH")
    if value.get("classification_source") != expected_source:
        raise EventCaseError("D1_CLASSIFICATION_SOURCE_MISMATCH")
    if value.get("importance_policy_status") != "UNRATIFIED":
        raise EventCaseError("IMPORTANCE_POLICY_MUST_REMAIN_UNRATIFIED")
    if value.get("automatic_promotion_authorized") is not False:
        raise EventCaseError("AUTOMATIC_PROMOTION_MUST_REMAIN_FALSE")
    if value.get("source_coverage") != expected_coverage:
        raise EventCaseError("SOURCE_COVERAGE_MISMATCH")
    if value.get("evidence_statuses") != list(EVIDENCE_STATUSES):
        raise EventCaseError("EVIDENCE_STATUS_REGISTRY_MISMATCH")
    if value.get("required_lineage_fields") != [
        "source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"
    ]:
        raise EventCaseError("REQUIRED_LINEAGE_FIELDS_MISMATCH")
    if value.get("authority") != expected_authority:
        raise EventCaseError("AUTHORITY_BOUNDARY_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(path))


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


def _record_key(record: dict) -> str:
    return D1.record_key(record)


def _validate_record(record: dict, contract: dict) -> str:
    if not isinstance(record, dict):
        raise EventCaseError("D1_RECORD_NOT_OBJECT")
    ticker = record.get("ticker")
    accession = record.get("accession")
    if not isinstance(ticker, str) or not ticker:
        raise EventCaseError("D1_TICKER_INVALID")
    if not isinstance(accession, str) or ACCESSION_RE.fullmatch(accession) is None:
        raise EventCaseError(f"D1_ACCESSION_INVALID:{accession!r}")
    if not _valid_date(record.get("filing_date")):
        raise EventCaseError(f"D1_FILING_DATE_INVALID:{record.get('filing_date')!r}")
    source = contract["classification_source"]
    if record.get("taxonomy_version") != source["taxonomy_version"]:
        raise EventCaseError(f"D1_TAXONOMY_VERSION_MISMATCH:{_record_key(record)}")
    if record.get("decision_version") != source["decision_version"]:
        raise EventCaseError(f"D1_DECISION_VERSION_MISMATCH:{_record_key(record)}")
    resolution = record.get("resolution")
    if resolution not in {"resolved", "partial", "unresolved", "not_applicable"}:
        raise EventCaseError(f"D1_RESOLUTION_INVALID:{resolution!r}")
    event_types = record.get("event_types")
    if not isinstance(event_types, list) or len(event_types) != len(set(event_types)):
        raise EventCaseError(f"D1_EVENT_TYPES_INVALID:{_record_key(record)}")
    unknown = sorted(set(event_types) - set(D1.EVENT_TYPES))
    if unknown:
        raise EventCaseError(f"D1_EVENT_TYPE_UNKNOWN:{unknown}")
    if event_types != sorted(event_types):
        raise EventCaseError(f"D1_EVENT_TYPES_NOT_CANONICAL:{_record_key(record)}")
    return _record_key(record)


def _index_records(records: list[dict], contract: dict) -> dict[str, dict]:
    if not isinstance(records, list):
        raise EventCaseError("D1_RECORDS_NOT_LIST")
    indexed = {}
    for record in records:
        key = _validate_record(record, contract)
        if key in indexed:
            raise EventCaseError(f"D1_RECORD_KEY_DUPLICATE:{key}")
        indexed[key] = copy.deepcopy(record)
    return indexed


def _validate_bindings(
    bindings: dict, records_by_key: dict[str, dict], contract: dict
) -> dict[str, dict]:
    if not isinstance(bindings, dict) or bindings.get("schema_version") != (
        BINDING_SCHEMA_VERSION
    ):
        raise EventCaseError("BINDING_SCHEMA_MISMATCH")
    binding_set_id = bindings.get("binding_set_id")
    if not isinstance(binding_set_id, str) or not binding_set_id:
        raise EventCaseError("BINDING_SET_ID_INVALID")
    rows = bindings.get("bindings")
    if not isinstance(rows, list):
        raise EventCaseError("BINDINGS_NOT_LIST")
    indexed = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EventCaseError("BINDING_NOT_OBJECT")
        key = row.get("source_record_key")
        if key not in records_by_key:
            raise EventCaseError(f"BINDING_SOURCE_RECORD_UNKNOWN:{key!r}")
        record = records_by_key[key]
        if (
            record["resolution"] not in set(
                contract["classification_source"]["supported_resolutions"]
            )
            or not record["event_types"]
        ):
            raise EventCaseError(f"BINDING_SOURCE_RECORD_NOT_CASE_ELIGIBLE:{key}")
        if key in indexed:
            raise EventCaseError(f"BINDING_SOURCE_RECORD_DUPLICATE:{key}")
        evidence = row.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("schema_version") != (
            EVIDENCE_SCHEMA_VERSION
        ):
            raise EventCaseError(f"EVIDENCE_SCHEMA_MISMATCH:{key}")
        indexed[key] = copy.deepcopy(evidence)
    return indexed


def _evidence_lineage(record: dict, evidence: dict | None, contract: dict) -> tuple:
    if evidence is None:
        return EVIDENCE_UNRESOLVED, [EXPLICIT_EVIDENCE_BINDING_ABSENT], None

    key = _record_key(record)
    if evidence.get("source_system") != "SEC_EDGAR":
        raise EventCaseError(f"EVIDENCE_SOURCE_SYSTEM_UNSUPPORTED:{key}")
    if evidence.get("subject") != record["ticker"]:
        raise EventCaseError(f"EVIDENCE_SUBJECT_MISMATCH:{key}")
    if evidence.get("event_date") != record["filing_date"]:
        raise EventCaseError(f"EVIDENCE_EVENT_DATE_MISMATCH:{key}")
    source = evidence.get("source_identity")
    if not isinstance(source, dict):
        source = {}
    if source.get("accession") != record["accession"]:
        raise EventCaseError(f"EVIDENCE_ACCESSION_MISMATCH:{key}")
    if source.get("source_url") and source.get("source_url") != record.get("url"):
        raise EventCaseError(f"EVIDENCE_SOURCE_URL_MISMATCH:{key}")

    required = contract["required_lineage_fields"]
    missing = [field for field in required if not source.get(field)]
    if source.get("source_id") and source["source_id"] != "sec_edgar":
        missing.append("source_id:INVALID")
    source_url = source.get("source_url")
    parsed_url = urlparse(source_url or "")
    if (
        source_url
        and (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "www.sec.gov"
            or parsed_url.username is not None
            or parsed_url.password is not None
        )
    ):
        missing.append("source_url:INVALID")
    if source.get("source_sha256") and SHA256_RE.fullmatch(source["source_sha256"]) is None:
        missing.append("source_sha256:INVALID")
    if source.get("available_at") and not _valid_available_at(source["available_at"]):
        missing.append("available_at:INVALID")
    if source.get("retrieved_at_utc") and not _valid_utc(source["retrieved_at_utc"]):
        missing.append("retrieved_at_utc:INVALID")
    if _valid_available_at(source.get("available_at")):
        available_date = source["available_at"][:10]
        if available_date < record["filing_date"]:
            missing.append("available_at:BEFORE_EVENT")
        if _valid_utc(source.get("retrieved_at_utc")) and (
            source["retrieved_at_utc"][:10] < available_date
        ):
            missing.append("retrieved_at_utc:BEFORE_AVAILABLE")

    lineage = {
        "event_as_of": record["filing_date"],
        "available_at": source.get("available_at"),
        "retrieved_at_utc": source.get("retrieved_at_utc"),
        "source_id": source.get("source_id"),
        "source_url": source.get("source_url"),
        "source_sha256": source.get("source_sha256"),
        "source_accession": source.get("accession"),
        "evidence_sha256": payload_sha256(evidence),
    }
    if missing:
        reason = f"{EVIDENCE_LINEAGE_INCOMPLETE}:{','.join(sorted(set(missing)))}"
        return EVIDENCE_BLOCKED, [reason], lineage
    return EVIDENCE_LINKED, [], lineage


def _case_id(record_key: str, event_type: str) -> str:
    identity = {"source_record_key": record_key, "event_type": event_type}
    return f"event-case-{payload_sha256(identity)[:24]}"


def _output_record_identity(source_record_key: str, contract: dict) -> tuple[str, str]:
    if not isinstance(source_record_key, str):
        raise EventCaseError("OUTPUT_SOURCE_RECORD_KEY_INVALID")
    parts = source_record_key.split("|")
    source = contract["classification_source"]
    if (
        len(parts) != 4
        or not parts[0]
        or ACCESSION_RE.fullmatch(parts[1]) is None
        or parts[2] != source["taxonomy_version"]
        or parts[3] != source["decision_version"]
    ):
        raise EventCaseError(f"OUTPUT_SOURCE_RECORD_KEY_INVALID:{source_record_key}")
    return parts[0], parts[1]


def _output_lineage_reasons(
    lineage: dict, event_date: str, accession: str, contract: dict
) -> list[str]:
    fields = {
        "event_as_of",
        "available_at",
        "retrieved_at_utc",
        "source_id",
        "source_url",
        "source_sha256",
        "source_accession",
        "evidence_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != fields:
        raise EventCaseError("OUTPUT_EVIDENCE_LINEAGE_FIELDS_MISMATCH")
    if lineage.get("event_as_of") != event_date:
        raise EventCaseError("OUTPUT_EVIDENCE_EVENT_DATE_MISMATCH")
    if lineage.get("source_accession") != accession:
        raise EventCaseError("OUTPUT_EVIDENCE_ACCESSION_MISMATCH")
    evidence_sha = lineage.get("evidence_sha256")
    if not isinstance(evidence_sha, str) or SHA256_RE.fullmatch(evidence_sha) is None:
        raise EventCaseError("OUTPUT_EVIDENCE_SHA256_INVALID")

    missing = []
    for field in contract["required_lineage_fields"]:
        if not lineage.get(field):
            missing.append(field)
    if lineage.get("source_id") and lineage["source_id"] != "sec_edgar":
        missing.append("source_id:INVALID")
    source_url = lineage.get("source_url")
    if source_url:
        if not isinstance(source_url, str):
            missing.append("source_url:INVALID")
        else:
            parsed_url = urlparse(source_url)
            if (
                parsed_url.scheme != "https"
                or parsed_url.hostname != "www.sec.gov"
                or parsed_url.username is not None
                or parsed_url.password is not None
            ):
                missing.append("source_url:INVALID")
    source_sha = lineage.get("source_sha256")
    if source_sha and (
        not isinstance(source_sha, str) or SHA256_RE.fullmatch(source_sha) is None
    ):
        missing.append("source_sha256:INVALID")
    available_at = lineage.get("available_at")
    retrieved_at = lineage.get("retrieved_at_utc")
    if available_at and not _valid_available_at(available_at):
        missing.append("available_at:INVALID")
    if retrieved_at and not _valid_utc(retrieved_at):
        missing.append("retrieved_at_utc:INVALID")
    if _valid_available_at(available_at):
        available_date = available_at[:10]
        if available_date < event_date:
            missing.append("available_at:BEFORE_EVENT")
        if _valid_utc(retrieved_at) and retrieved_at[:10] < available_date:
            missing.append("retrieved_at_utc:BEFORE_AVAILABLE")
    return sorted(set(missing))


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate retained event-case semantics without claiming omitted D1 proof."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet_fields = {
        "schema_version",
        "contract_version",
        "binding_set_id",
        "importance_policy_status",
        "automatic_promotion_authorized",
        "source_coverage",
        "authority",
        "inputs",
        "summary",
        "cases",
        "excluded_records",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != packet_fields:
        raise EventCaseError("OUTPUT_PACKET_FIELDS_MISMATCH")
    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise EventCaseError("OUTPUT_PACKET_SHA256_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise EventCaseError("OUTPUT_PACKET_SHA256_MISMATCH")
    if (
        packet.get("schema_version") != PACKET_SCHEMA_VERSION
        or packet.get("contract_version") != contract["contract_version"]
    ):
        raise EventCaseError("OUTPUT_PACKET_IDENTITY_MISMATCH")
    if not isinstance(packet.get("binding_set_id"), str) or not packet[
        "binding_set_id"
    ]:
        raise EventCaseError("OUTPUT_BINDING_SET_ID_INVALID")
    if (
        packet.get("importance_policy_status")
        != contract["importance_policy_status"]
        or packet.get("automatic_promotion_authorized")
        is not contract["automatic_promotion_authorized"]
        or packet.get("source_coverage") != contract["source_coverage"]
        or packet.get("authority") != contract["authority"]
    ):
        raise EventCaseError("OUTPUT_AUTHORITY_BOUNDARY_MISMATCH")
    inputs = packet.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {
        "d1_records_sha256",
        "evidence_bindings_sha256",
    }:
        raise EventCaseError("OUTPUT_INPUTS_FIELDS_MISMATCH")
    for digest_value in inputs.values():
        if not isinstance(digest_value, str) or SHA256_RE.fullmatch(digest_value) is None:
            raise EventCaseError("OUTPUT_INPUT_SHA256_INVALID")

    cases = packet.get("cases")
    if not isinstance(cases, list):
        raise EventCaseError("OUTPUT_CASES_NOT_LIST")
    case_fields = {
        "schema_version",
        "case_id",
        "market",
        "subject",
        "subject_name",
        "event_type",
        "event_date",
        "source_record_key",
        "classification",
        "evidence_status",
        "evidence_reasons",
        "evidence_lineage",
        "importance_status",
        "interpretation_status",
        "promotion_status",
        "stage_transition",
        "investment_action",
    }
    classification_fields = {
        "resolution",
        "taxonomy_version",
        "decision_version",
        "item_codes",
        "unknown_item_codes",
        "taxonomy_gap_codes",
        "undetermined",
    }
    source_case_identity = {}
    counts = {status: 0 for status in EVIDENCE_STATUSES}
    case_ids = []
    case_source_keys = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != case_fields:
            raise EventCaseError("OUTPUT_CASE_FIELDS_MISMATCH")
        source_key = case.get("source_record_key")
        subject, accession = _output_record_identity(source_key, contract)
        event_type = case.get("event_type")
        event_date = case.get("event_date")
        classification = case.get("classification")
        if (
            case.get("schema_version") != CASE_SCHEMA_VERSION
            or case.get("market") != "US"
            or case.get("subject") != subject
            or (
                case.get("subject_name") is not None
                and (
                    not isinstance(case["subject_name"], str)
                    or not case["subject_name"].strip()
                )
            )
            or event_type not in D1.EVENT_TYPES
            or not _valid_date(event_date)
            or case.get("case_id") != _case_id(source_key, event_type)
        ):
            raise EventCaseError("OUTPUT_CASE_IDENTITY_MISMATCH")
        if not isinstance(classification, dict) or set(classification) != (
            classification_fields
        ):
            raise EventCaseError("OUTPUT_CLASSIFICATION_FIELDS_MISMATCH")
        if (
            classification.get("resolution")
            not in contract["classification_source"]["supported_resolutions"]
            or classification.get("taxonomy_version")
            != contract["classification_source"]["taxonomy_version"]
            or classification.get("decision_version")
            != contract["classification_source"]["decision_version"]
            or any(
                not isinstance(classification.get(field), list)
                or any(not isinstance(value, str) for value in classification[field])
                for field in (
                    "item_codes",
                    "unknown_item_codes",
                    "taxonomy_gap_codes",
                    "undetermined",
                )
            )
        ):
            raise EventCaseError("OUTPUT_CLASSIFICATION_VALUE_MISMATCH")
        evidence_status = case.get("evidence_status")
        evidence_reasons = case.get("evidence_reasons")
        if (
            evidence_status not in EVIDENCE_STATUSES
            or not isinstance(evidence_reasons, list)
            or any(not isinstance(reason, str) or not reason for reason in evidence_reasons)
        ):
            raise EventCaseError("OUTPUT_EVIDENCE_STATUS_INVALID")
        if evidence_status == EVIDENCE_UNRESOLVED:
            if (
                evidence_reasons != [EXPLICIT_EVIDENCE_BINDING_ABSENT]
                or case.get("evidence_lineage") is not None
            ):
                raise EventCaseError("OUTPUT_UNRESOLVED_EVIDENCE_MISMATCH")
        else:
            missing = _output_lineage_reasons(
                case.get("evidence_lineage"), event_date, accession, contract
            )
            expected_reasons = (
                []
                if evidence_status == EVIDENCE_LINKED
                else [f"{EVIDENCE_LINEAGE_INCOMPLETE}:{','.join(missing)}"]
            )
            if (
                (evidence_status == EVIDENCE_LINKED and missing)
                or (evidence_status == EVIDENCE_BLOCKED and not missing)
                or evidence_reasons != expected_reasons
            ):
                raise EventCaseError("OUTPUT_EVIDENCE_DERIVATION_MISMATCH")
        if (
            case.get("importance_status") != IMPORTANCE_UNRATIFIED
            or case.get("interpretation_status") != INTERPRETATION_NOT_AUTHORIZED
            or case.get("promotion_status") != PROMOTION_NOT_AUTHORIZED
            or case.get("stage_transition") is not None
            or case.get("investment_action") is not None
        ):
            raise EventCaseError("OUTPUT_CASE_AUTHORITY_EXPANSION")
        common = {
            key: copy.deepcopy(case[key])
            for key in (
                "subject",
                "subject_name",
                "event_date",
                "classification",
                "evidence_status",
                "evidence_reasons",
                "evidence_lineage",
            )
        }
        if source_key in source_case_identity and source_case_identity[source_key] != common:
            raise EventCaseError(f"OUTPUT_SOURCE_RECORD_CASE_DRIFT:{source_key}")
        source_case_identity[source_key] = common
        case_ids.append(case["case_id"])
        case_source_keys.add(source_key)
        counts[evidence_status] += 1
    if case_ids != sorted(set(case_ids)):
        raise EventCaseError("OUTPUT_CASE_ORDER_OR_DUPLICATE_INVALID")

    excluded = packet.get("excluded_records")
    if not isinstance(excluded, list):
        raise EventCaseError("OUTPUT_EXCLUDED_RECORDS_NOT_LIST")
    allowed_reasons = {
        "RESOLUTION_UNRESOLVED",
        "RESOLUTION_NOT_APPLICABLE",
        "NO_RESOLVED_EVENT_TYPE",
    }
    excluded_keys = []
    for row in excluded:
        if not isinstance(row, dict) or set(row) != {"source_record_key", "reason"}:
            raise EventCaseError("OUTPUT_EXCLUDED_RECORD_FIELDS_MISMATCH")
        source_key = row.get("source_record_key")
        _output_record_identity(source_key, contract)
        if row.get("reason") not in allowed_reasons or source_key in case_source_keys:
            raise EventCaseError("OUTPUT_EXCLUDED_RECORD_VALUE_MISMATCH")
        excluded_keys.append(source_key)
    if excluded_keys != sorted(set(excluded_keys)):
        raise EventCaseError("OUTPUT_EXCLUDED_RECORD_ORDER_OR_DUPLICATE_INVALID")

    summary = packet.get("summary")
    expected_summary = {
        "source_records": len(case_source_keys | set(excluded_keys)),
        "cases": len(cases),
        "excluded_records": len(excluded),
        **counts,
    }
    if summary != expected_summary:
        raise EventCaseError("OUTPUT_SUMMARY_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def build_packet(
    *, records: list[dict], evidence_bindings: dict, contract: dict | None = None,
) -> dict:
    """Build deterministic cases from the ratified SEC D1 classification only."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    records_by_key = _index_records(records, contract)
    bindings_by_key = _validate_bindings(evidence_bindings, records_by_key, contract)

    cases = []
    excluded = []
    counts = {status: 0 for status in EVIDENCE_STATUSES}
    supported = set(contract["classification_source"]["supported_resolutions"])
    for key in sorted(records_by_key):
        record = records_by_key[key]
        event_types = record["event_types"]
        if record["resolution"] not in supported:
            excluded.append({
                "source_record_key": key,
                "reason": f"RESOLUTION_{record['resolution'].upper()}",
            })
            continue
        if not event_types:
            excluded.append({"source_record_key": key, "reason": "NO_RESOLVED_EVENT_TYPE"})
            continue
        evidence_status, evidence_reasons, lineage = _evidence_lineage(
            record, bindings_by_key.get(key), contract
        )
        for event_type in event_types:
            counts[evidence_status] += 1
            cases.append({
                "schema_version": CASE_SCHEMA_VERSION,
                "case_id": _case_id(key, event_type),
                "market": "US",
                "subject": record["ticker"],
                "subject_name": record.get("name"),
                "event_type": event_type,
                "event_date": record["filing_date"],
                "source_record_key": key,
                "classification": {
                    "resolution": record["resolution"],
                    "taxonomy_version": record["taxonomy_version"],
                    "decision_version": record["decision_version"],
                    "item_codes": copy.deepcopy(record.get("item_codes") or []),
                    "unknown_item_codes": copy.deepcopy(record.get("unknown_item_codes") or []),
                    "taxonomy_gap_codes": copy.deepcopy(record.get("taxonomy_gap_codes") or []),
                    "undetermined": copy.deepcopy(record.get("undetermined") or []),
                },
                "evidence_status": evidence_status,
                "evidence_reasons": copy.deepcopy(evidence_reasons),
                "evidence_lineage": copy.deepcopy(lineage),
                "importance_status": IMPORTANCE_UNRATIFIED,
                "interpretation_status": INTERPRETATION_NOT_AUTHORIZED,
                "promotion_status": PROMOTION_NOT_AUTHORIZED,
                "stage_transition": None,
                "investment_action": None,
            })

    normalized_bindings = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "binding_set_id": evidence_bindings["binding_set_id"],
        "bindings": [
            {"source_record_key": key, "evidence": bindings_by_key[key]}
            for key in sorted(bindings_by_key)
        ],
    }
    normalized_records = [records_by_key[key] for key in sorted(records_by_key)]
    body = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "binding_set_id": evidence_bindings["binding_set_id"],
        "importance_policy_status": contract["importance_policy_status"],
        "automatic_promotion_authorized": contract["automatic_promotion_authorized"],
        "source_coverage": copy.deepcopy(contract["source_coverage"]),
        "authority": copy.deepcopy(contract["authority"]),
        "inputs": {
            "d1_records_sha256": payload_sha256(normalized_records),
            "evidence_bindings_sha256": payload_sha256(normalized_bindings),
        },
        "summary": {
            "source_records": len(normalized_records),
            "cases": len(cases),
            "excluded_records": len(excluded),
            **counts,
        },
        "cases": sorted(cases, key=lambda item: item["case_id"]),
        "excluded_records": excluded,
    }
    body["packet_sha256"] = payload_sha256(body)
    return validate_packet(body, contract)


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
    parser = argparse.ArgumentParser(description="Build P3-08 event Discovery Cases")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--evidence-bindings", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    packet = build_packet(
        records=load_jsonl(args.records),
        evidence_bindings=_read_json(args.evidence_bindings),
        contract=load_contract(args.contract),
    )
    _atomic_write_json(args.out, packet)
    return 0


def main() -> int:
    try:
        return run()
    except EventCaseError as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
