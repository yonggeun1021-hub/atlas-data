#!/usr/bin/env python3
"""P3-12-GOV-02B -- Upbit governance revocations registry.

`config/upbit_governance_revocations.json` is the CIO-authored, checked-in
record of exact `(source_path, file_sha256)` lineage items that were
invalidly ratified via PR #465 (merge commit
`69e1cd27d62ea1f2c871d1d91657b05f11a6e699`) and then reverted -- see
`docs/p3_12_id_01_bounded_identity_registry_cio_decision_packet_20260830.md`
and the PR #474 decision thread for the full account.

This module is the ONLY place any read model should consult to decide
whether a specific committed artifact is revoked. It never does broad
date/prefix matching -- a lookup is a match only when BOTH the exact
`source_path` string AND the exact `revoked_file_sha256` value are present,
together, as one registry record. A file at the same path with different
(post-revert) bytes is never revoked; a file at a different path with the
same bytes is never revoked either -- the tuple is the unit of revocation,
never either half alone.

This module never deletes, rewrites, or judges the historical artifacts it
lists -- every revoked packet/run file stays byte-for-byte untouched on
disk, permanently, as historical evidence. Revocation is a read-model
concern (never select this as "current"), not a data-retention concern.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVOCATIONS_PATH = ROOT / "config" / "upbit_governance_revocations.json"
SCHEMA_VERSION = "upbit_governance_revocations/1"
APPROVAL_STATUS = "CIO_REVOKED_FAIL_CLOSED"

_REQUIRED_RECORD_FIELDS = (
    "source_path", "revoked_file_sha256", "revoked_record_payload_sha256",
    "revoked_inner_packet_sha256", "effective_from", "affected_lineage",
)


class GovernanceRevocationsError(ValueError):
    """Fail-closed P3-12-GOV-02B revocation-registry violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_revocations(path: Path = REVOCATIONS_PATH) -> dict:
    """Load and fully self-validate the revocation registry. Raises on any
    structural defect -- a malformed or tampered registry must never be
    silently treated as empty (which would fail OPEN, letting a revoked
    artifact back in) or silently trusted (which could hide a forged
    revocation). Both directions fail closed via the same mechanism: raise.
    """
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceRevocationsError(f"REVOCATIONS_READ_FAILED:{exc}") from exc
    if not isinstance(doc, dict):
        raise GovernanceRevocationsError("REVOCATIONS_ROOT_INVALID")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise GovernanceRevocationsError("REVOCATIONS_SCHEMA_VERSION_MISMATCH")
    if doc.get("approval_status") != APPROVAL_STATUS:
        raise GovernanceRevocationsError("REVOCATIONS_APPROVAL_STATUS_INVALID")
    declared_hash = doc.get("payload_sha256")
    if not isinstance(declared_hash, str):
        raise GovernanceRevocationsError("REVOCATIONS_SELF_HASH_MISSING")
    recomputed = payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
    if recomputed != declared_hash:
        raise GovernanceRevocationsError("REVOCATIONS_SELF_HASH_MISMATCH")

    authority = doc.get("authority")
    if not isinstance(authority, dict):
        raise GovernanceRevocationsError("REVOCATIONS_AUTHORITY_MISSING")
    for field, value in authority.items():
        if field == "review_only":
            continue
        if value is not False:
            raise GovernanceRevocationsError(f"REVOCATIONS_AUTHORITY_INVARIANT_VIOLATED:{field}")

    records = doc.get("records")
    if not isinstance(records, list) or not records:
        raise GovernanceRevocationsError("REVOCATIONS_RECORDS_EMPTY_OR_INVALID")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not set(_REQUIRED_RECORD_FIELDS) <= record.keys():
            raise GovernanceRevocationsError("REVOCATIONS_RECORD_FIELDS_INVALID")
        source_path = record["source_path"]
        if not isinstance(source_path, str) or not source_path:
            raise GovernanceRevocationsError("REVOCATIONS_RECORD_SOURCE_PATH_INVALID")
        file_hash = record["revoked_file_sha256"]
        if file_hash is not None and not (isinstance(file_hash, str) and len(file_hash) == 64):
            raise GovernanceRevocationsError("REVOCATIONS_RECORD_FILE_HASH_INVALID")
        key = (source_path, file_hash)
        if file_hash is not None and key in seen:
            raise GovernanceRevocationsError(f"REVOCATIONS_DUPLICATE_RECORD:{key}")
        seen.add(key)
    return doc


def is_revoked(source_path: str, file_sha256: str, *, revocations: dict | None = None) -> bool:
    """True iff the EXACT `(source_path, file_sha256)` tuple appears in the
    registry. Never a prefix, glob, or date-range match -- both the path
    string and the 64-hex-char file hash must match one record exactly.
    """
    doc = revocations if revocations is not None else load_revocations()
    for record in doc["records"]:
        if record["source_path"] == source_path and record["revoked_file_sha256"] == file_sha256:
            return True
    return False


def revocation_record_for(source_path: str, file_sha256: str, *, revocations: dict | None = None) -> dict | None:
    """The matching record (for audit/error-message purposes), or None."""
    doc = revocations if revocations is not None else load_revocations()
    for record in doc["records"]:
        if record["source_path"] == source_path and record["revoked_file_sha256"] == file_sha256:
            return record
    return None
