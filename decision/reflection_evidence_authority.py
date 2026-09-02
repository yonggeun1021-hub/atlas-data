#!/usr/bin/env python3
"""P8-10 Reflection Evidence Authority structure and fail-closed gate.

Only the authority *structure* is ratified.  The classifier remains disabled:
``effective_from`` is deliberately unresolved until canonical-main adoption,
and the numeric thresholds, natural-sample minimum, confidence thresholds,
and accountable owner remain ``UNKNOWN_PENDING_RATIFICATION``.

The registry is a locator, not a source of truth by itself.  This module pins
the exact rule identity, approval timestamp, referenced paths, and both file
digests.  Rewriting a referenced file and updating the registry digest cannot
turn the rewrite into authority; the pinned digest must change in reviewed
code as well.  Any missing, stale, future, malformed, identity-mismatched, or
tampered input resolves to an UNKNOWN/inactive assessment.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "reflection_evidence_authority_registry.json"

RULE_ID = "P8-10-REFLECTION-EVIDENCE-AUTHORITY"
RULE_VERSION = "1"
RATIFIED_AT = "2026-09-02T14:32:11Z"
EFFECTIVE_FROM = None  # Required unresolved gate; set only at canonical-main adoption.
CONTENT_REF = "config/reflection_evidence_authority_content_v1.json"
CONTENT_SHA256 = "f29a7ebb27e007f5a519c647c810f86aed87a49efb7e3cdb8d3ff1ed5a0fe064"
AUTHORITY_EVIDENCE_REF = (
    "evidence/authority/p8_10_reflection_evidence_authority_approval_20260902.json"
)
AUTHORITY_EVIDENCE_SHA256 = (
    "fc8e5a85b900029a5936debd0e4fafd907acc4f94a0bb2b28c463f9a92fe12c4"
)

REGISTRY_SCHEMA_VERSION = "reflection_evidence_authority_registry/1"
CONTENT_SCHEMA_VERSION = "reflection_evidence_authority_content/1"
EVIDENCE_SCHEMA_VERSION = "reflection_evidence_authority_approval/1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

RECORD_FIELDS = {
    "rule_id", "rule_version", "record_state", "ratified_at", "effective_from",
    "content_ref", "content_sha256", "authority_evidence_ref",
    "authority_evidence_sha256",
}
CONTENT_FIELDS = {
    "schema_version", "rule_id", "rule_version", "scope", "state_vocabulary",
    "source_identity_policy", "point_in_time_policy", "fail_closed_conditions",
    "pending_ratification", "operational_defaults", "approval_boundary",
}
EVIDENCE_FIELDS = {
    "schema_version", "source_kind", "source_thread_id", "approved_recommendation",
    "rule_id", "rule_version", "ratified_at", "approved_scope", "deferred",
    "not_approved", "implementation_evidence", "historical_backfill_authorized",
}

PENDING_FIELDS = {
    "reflection_thresholds": "UNKNOWN_PENDING_RATIFICATION",
    "minimum_natural_sample_count": "UNKNOWN_PENDING_RATIFICATION",
    "confidence_thresholds": "UNKNOWN_PENDING_RATIFICATION",
    "accountable_owner": "UNKNOWN_PENDING_RATIFICATION",
}
STATE_VOCABULARY = {
    "reflection_status": [
        "UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED", "UNKNOWN",
    ],
    "authority_state": ["ACTIVE", "INACTIVE", "UNKNOWN"],
}
SOURCE_IDENTITY_POLICY = {
    "exact_rule_identity_required": True,
    "exact_content_sha256_required": True,
    "exact_authority_evidence_sha256_required": True,
    "append_only_records_required": True,
}
POINT_IN_TIME_POLICY = {
    "ratified_at_required": True,
    "effective_from_required_for_operation": True,
    "effective_from_must_not_precede_ratified_at": True,
    "decision_at_must_not_precede_ratified_at": True,
    "decision_at_must_not_precede_effective_from": True,
    "historical_backfill_authorized": False,
}
FAIL_CLOSED_CONDITIONS = [
    "AUTHORITY_RECORD_MISSING",
    "AUTHORITY_RECORD_STALE",
    "AUTHORITY_RATIFICATION_IN_FUTURE",
    "AUTHORITY_EFFECTIVE_FROM_UNRESOLVED",
    "AUTHORITY_EFFECTIVE_FROM_IN_FUTURE",
    "AUTHORITY_HASH_INVALID",
    "AUTHORITY_CONTENT_HASH_MISMATCH",
    "AUTHORITY_EVIDENCE_HASH_MISMATCH",
    "AUTHORITY_IDENTITY_MISMATCH",
    "AUTHORITY_CONTENT_TAMPERED",
    "AUTHORITY_EVIDENCE_TAMPERED",
]
OPERATIONAL_DEFAULTS = {
    "classifier_enabled": False,
    "reflection_status": "UNKNOWN",
    "aggregate_threshold_basis": "PROVISIONAL",
    "natural_revalidation_status": "PENDING",
}
APPROVAL_BOUNDARY = {
    "p8_12_recommendation_a_approved": False,
    "p8_12_recommendation_b_approved": False,
    "p8_12_recommendation_c_approved": False,
    "candidate": "NONE",
    "p5_06_authorized": False,
    "p7_08_authorized": False,
    "p8_13_authorized": False,
    "stage_promotion_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "broker_authorized": False,
    "real_capital_authorized": False,
    "live_operation_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class ReflectionEvidenceAuthorityError(ValueError):
    """A supplied authority artifact cannot be trusted."""


def _parse_utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ReflectionEvidenceAuthorityError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise ReflectionEvidenceAuthorityError(code) from exc


def _read_json(path: Path, missing_code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReflectionEvidenceAuthorityError(missing_code) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ReflectionEvidenceAuthorityError(f"AUTHORITY_ARTIFACT_UNREADABLE:{path}") from exc
    if not isinstance(value, dict):
        raise ReflectionEvidenceAuthorityError(f"AUTHORITY_ARTIFACT_NOT_OBJECT:{path}")
    return value


def _resolve_exact_ref(root: Path, source_ref: str, expected_ref: str, code: str) -> Path:
    if source_ref != expected_ref:
        raise ReflectionEvidenceAuthorityError(code)
    candidate = (root / source_ref).resolve()
    try:
        candidate.relative_to(root.resolve())
    except (ValueError, OSError) as exc:
        raise ReflectionEvidenceAuthorityError(code) from exc
    if not candidate.is_file():
        raise ReflectionEvidenceAuthorityError("AUTHORITY_REFERENCED_ARTIFACT_MISSING")
    return candidate


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_REFERENCED_ARTIFACT_UNREADABLE") from exc


def _validate_record_shape(registry: dict) -> dict:
    if set(registry) != {"schema_version", "records"}:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_REGISTRY_FIELDS_MISMATCH")
    if registry.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_REGISTRY_IDENTITY_MISMATCH")
    records = registry.get("records")
    if not isinstance(records, list) or not records:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_RECORD_MISSING")
    matching = [row for row in records if isinstance(row, dict) and row.get("rule_id") == RULE_ID]
    if not matching:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_RECORD_MISSING")
    if len(matching) != 1:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_RECORD_AMBIGUOUS")
    record = matching[0]
    if set(record) != RECORD_FIELDS:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_RECORD_FIELDS_MISMATCH")
    if record.get("record_state") != "CURRENT":
        raise ReflectionEvidenceAuthorityError("AUTHORITY_RECORD_STALE")
    return record


def validate_authority(
    decision_at: str,
    *,
    registry_path: Path = REGISTRY_PATH,
    root: Path = ROOT,
) -> dict:
    """Validate exact authority artifacts for a point-in-time decision.

    A valid structure is not the same as an active classifier.  This returns
    the validated documents even though the canonical record's unresolved
    ``effective_from`` keeps operational use disabled.
    """
    decision_time = _parse_utc(decision_at, "AUTHORITY_DECISION_AT_INVALID")
    registry = _read_json(Path(registry_path), "AUTHORITY_RECORD_MISSING")
    record = _validate_record_shape(registry)

    if record.get("rule_id") != RULE_ID or record.get("rule_version") != RULE_VERSION:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_IDENTITY_MISMATCH")

    ratified_at = _parse_utc(
        record.get("ratified_at"), "AUTHORITY_RATIFIED_AT_INVALID"
    )
    if ratified_at > decision_time:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_RATIFICATION_IN_FUTURE")

    effective_raw = record.get("effective_from")
    if effective_raw is not None:
        effective_from = _parse_utc(
            effective_raw, "AUTHORITY_EFFECTIVE_FROM_INVALID"
        )
        if effective_from < ratified_at:
            raise ReflectionEvidenceAuthorityError(
                "AUTHORITY_EFFECTIVE_FROM_PRECEDES_RATIFICATION"
            )
        if effective_from > decision_time:
            raise ReflectionEvidenceAuthorityError("AUTHORITY_EFFECTIVE_FROM_IN_FUTURE")

    for field in ("content_sha256", "authority_evidence_sha256"):
        if not isinstance(record.get(field), str) or SHA256_RE.fullmatch(record[field]) is None:
            raise ReflectionEvidenceAuthorityError(f"AUTHORITY_HASH_INVALID:{field}")

    # These values are immutable identity, pinned independently of the mutable
    # registry.  Updating a file and its registry digest is still rejected.
    expected_record_identity = {
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "ratified_at": RATIFIED_AT,
        "effective_from": EFFECTIVE_FROM,
        "content_ref": CONTENT_REF,
        "content_sha256": CONTENT_SHA256,
        "authority_evidence_ref": AUTHORITY_EVIDENCE_REF,
        "authority_evidence_sha256": AUTHORITY_EVIDENCE_SHA256,
    }
    if any(record.get(key) != value for key, value in expected_record_identity.items()):
        raise ReflectionEvidenceAuthorityError("AUTHORITY_IMMUTABLE_IDENTITY_MISMATCH")

    root = Path(root)
    content_path = _resolve_exact_ref(
        root, record["content_ref"], CONTENT_REF, "AUTHORITY_CONTENT_IDENTITY_MISMATCH"
    )
    evidence_path = _resolve_exact_ref(
        root,
        record["authority_evidence_ref"],
        AUTHORITY_EVIDENCE_REF,
        "AUTHORITY_EVIDENCE_IDENTITY_MISMATCH",
    )
    if _sha256(content_path) != record["content_sha256"]:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_CONTENT_TAMPERED")
    if _sha256(evidence_path) != record["authority_evidence_sha256"]:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_EVIDENCE_TAMPERED")

    content = _read_json(content_path, "AUTHORITY_CONTENT_MISSING")
    if set(content) != CONTENT_FIELDS:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_CONTENT_FIELDS_MISMATCH")
    if (
        content.get("schema_version") != CONTENT_SCHEMA_VERSION
        or content.get("rule_id") != RULE_ID
        or content.get("rule_version") != RULE_VERSION
    ):
        raise ReflectionEvidenceAuthorityError("AUTHORITY_CONTENT_IDENTITY_MISMATCH")
    if content.get("state_vocabulary") != STATE_VOCABULARY:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_STATE_VOCABULARY_MISMATCH")
    if content.get("source_identity_policy") != SOURCE_IDENTITY_POLICY:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_SOURCE_IDENTITY_POLICY_MISMATCH")
    if content.get("point_in_time_policy") != POINT_IN_TIME_POLICY:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_POINT_IN_TIME_POLICY_MISMATCH")
    if content.get("fail_closed_conditions") != FAIL_CLOSED_CONDITIONS:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_FAIL_CLOSED_POLICY_MISMATCH")
    if content.get("pending_ratification") != PENDING_FIELDS:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_PENDING_RATIFICATION_MISMATCH")
    if content.get("operational_defaults") != OPERATIONAL_DEFAULTS:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_OPERATIONAL_DEFAULTS_MISMATCH")
    if content.get("approval_boundary") != APPROVAL_BOUNDARY:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_APPROVAL_BOUNDARY_MISMATCH")

    evidence = _read_json(evidence_path, "AUTHORITY_EVIDENCE_MISSING")
    if set(evidence) != EVIDENCE_FIELDS:
        raise ReflectionEvidenceAuthorityError("AUTHORITY_EVIDENCE_FIELDS_MISMATCH")
    if (
        evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION
        or evidence.get("source_kind") != "EXPLICIT_USER_APPROVAL_IN_CODEX_THREAD"
        or evidence.get("source_thread_id") != "01a0485e-777d-7462-9454-f602a86edcd9"
        or evidence.get("approved_recommendation") != "A"
        or evidence.get("rule_id") != RULE_ID
        or evidence.get("rule_version") != RULE_VERSION
        or evidence.get("ratified_at") != RATIFIED_AT
        or evidence.get("deferred") != PENDING_FIELDS
        or evidence.get("historical_backfill_authorized") is not False
    ):
        raise ReflectionEvidenceAuthorityError("AUTHORITY_EVIDENCE_IDENTITY_MISMATCH")

    return {
        "record": copy.deepcopy(record),
        "content": copy.deepcopy(content),
        "authority_evidence": copy.deepcopy(evidence),
    }


def assess_authority(
    decision_at: str,
    *,
    registry_path: Path = REGISTRY_PATH,
    root: Path = ROOT,
) -> dict:
    """Return a non-throwing, fail-closed operational authority assessment."""
    result = {
        "authority_state": "UNKNOWN",
        "classifier_enabled": False,
        "reflection_status": "UNKNOWN",
        "aggregate_threshold_basis": "PROVISIONAL",
        "reason_codes": [],
    }
    try:
        validated = validate_authority(
            decision_at, registry_path=registry_path, root=root
        )
    except ReflectionEvidenceAuthorityError as exc:
        result["reason_codes"] = [str(exc)]
        return result

    result["authority_state"] = "INACTIVE"
    if validated["record"]["effective_from"] is None:
        result["reason_codes"].append("AUTHORITY_EFFECTIVE_FROM_UNRESOLVED")
    result["reason_codes"].extend(
        [
            "AUTHORITY_NUMERIC_RATIFICATION_PENDING",
            "AUTHORITY_NATURAL_REVALIDATION_PENDING",
        ]
    )
    return result
