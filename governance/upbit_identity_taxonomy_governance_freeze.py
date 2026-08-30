#!/usr/bin/env python3
"""P3-12-GOV-03A -- structured, self-hash-bound identity/taxonomy freeze registry.

``config/upbit_identity_taxonomy_governance_freeze.json`` records exact
``(source_path, content-identity)`` tuples for every lineage item frozen
because P3-12's identity registry and taxonomy were merged without an
explicit post-HOLD CIO approval (see the file's own ``reason`` field and
``docs/p3_12_gov_03_identity_authority_audit_and_freeze_20260830.md``).

**The one rule every consumer in this codebase must follow**: a frozen
tuple blocks regardless of whether the CURRENTLY OBSERVED bytes/hash happen
to match, differ, or have been restored back to the frozen value. Checking
"only when there is a mismatch" is exactly the defect this module closes --
see ``is_frozen()``'s docstring. Never a broad date/prefix/glob pattern,
and never "the path alone, regardless of hash" either.

Matching is exact ``(source_path, revoked_file_sha256)`` for a document's
own raw file bytes -- an unrelated file elsewhere that happens to share
those exact bytes is never swept in. ``revoked_record_payload_sha256`` and
``revoked_inner_packet_sha256`` match GLOBALLY (independent of
``source_path``): these are a JSON document's OWN embedded,
canonical-serialization content-identity fields, which by design must keep
identifying the same frozen content no matter where that document is
currently stored -- including a content-addressed retained copy this
codebase's own append-only evidence convention makes at a path that could
never be enumerated in the registry in advance (see
``decision/crypto_paper_decision_snapshot.py::retain_source()``).

This module never deletes, rewrites, or judges the historical artifacts it
lists -- every frozen packet/config stays byte-for-byte exactly what it is
on disk. Freezing is a read-model/authority concern (never treat this
content as currently effective), never a data-retention concern.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE_PATH = ROOT / "config" / "upbit_identity_taxonomy_governance_freeze.json"
SCHEMA_VERSION = "upbit_identity_taxonomy_governance_freeze/1"
PENDING_RESOLUTION_STATUS = "PENDING_GOVERNANCE_RESOLUTION"

_REQUIRED_RECORD_FIELDS = (
    "source_path", "revoked_file_sha256", "revoked_record_payload_sha256",
    "revoked_inner_packet_sha256", "reason", "effective_from",
)
_HASH_FIELDS = ("revoked_file_sha256", "revoked_record_payload_sha256", "revoked_inner_packet_sha256")


class GovernanceFreezeError(ValueError):
    """Fail-closed P3-12-GOV-03A freeze-registry violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _valid_hash_or_none(value) -> bool:
    return value is None or (isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value))


def load_freeze(path: Path = FREEZE_PATH) -> dict:
    """Load and fully self-validate the freeze registry. Raises on any
    structural defect -- a malformed or tampered registry must never be
    silently treated as empty (fail OPEN, letting frozen authority back in)
    or silently trusted (hiding a forged freeze). Both directions fail
    closed via the same mechanism: raise, never proceed.
    """
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceFreezeError(f"FREEZE_READ_FAILED:{exc}") from exc
    if not isinstance(doc, dict):
        raise GovernanceFreezeError("FREEZE_ROOT_INVALID")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise GovernanceFreezeError("FREEZE_SCHEMA_VERSION_MISMATCH")
    declared_hash = doc.get("payload_sha256")
    if not isinstance(declared_hash, str):
        raise GovernanceFreezeError("FREEZE_SELF_HASH_MISSING")
    recomputed = payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
    if recomputed != declared_hash:
        raise GovernanceFreezeError("FREEZE_SELF_HASH_MISMATCH")

    authority = doc.get("authority")
    if not isinstance(authority, dict) or not authority:
        raise GovernanceFreezeError("FREEZE_AUTHORITY_MISSING")
    for field, value in authority.items():
        if value is not False:
            raise GovernanceFreezeError(f"FREEZE_AUTHORITY_INVARIANT_VIOLATED:{field}")

    records = doc.get("records")
    if not isinstance(records, list) or not records:
        raise GovernanceFreezeError("FREEZE_RECORDS_EMPTY_OR_INVALID")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not set(_REQUIRED_RECORD_FIELDS) <= record.keys():
            raise GovernanceFreezeError("FREEZE_RECORD_FIELDS_INVALID")
        source_path = record["source_path"]
        if not isinstance(source_path, str) or not source_path:
            raise GovernanceFreezeError("FREEZE_RECORD_SOURCE_PATH_INVALID")
        for field in _HASH_FIELDS:
            if not _valid_hash_or_none(record.get(field)):
                raise GovernanceFreezeError(f"FREEZE_RECORD_HASH_FIELD_INVALID:{field}")
        if all(record.get(field) is None for field in _HASH_FIELDS):
            raise GovernanceFreezeError("FREEZE_RECORD_NO_HASH_IDENTITY")
        key = (source_path, tuple(record.get(field) for field in _HASH_FIELDS))
        if key in seen:
            raise GovernanceFreezeError(f"FREEZE_DUPLICATE_RECORD:{key}")
        seen.add(key)
    return doc


def is_frozen(
    source_path: str,
    *,
    file_sha256: str | None = None,
    record_payload_sha256: str | None = None,
    inner_packet_sha256: str | None = None,
    freeze: dict | None = None,
) -> bool:
    """True iff this observation matches a frozen record.

    Deliberately does NOT require the caller to have already detected a
    byte mismatch -- pass whatever hash(es) you can observe (a consumer's
    own pinned/declared reference, the file's current raw bytes, a
    document's own embedded content-identity field) and this checks them
    all. This is what makes the check correct when a document's ratified
    metadata gets edited (so its raw file hash no longer matches what was
    originally frozen) or when bytes are deliberately restored back to the
    exact frozen value (so the raw file hash matches again) -- in both
    cases, whichever hash argument still identifies the frozen content
    triggers the block.

    ``file_sha256`` (the physical file's own raw bytes) is matched WITH
    ``source_path`` -- an unrelated file elsewhere that happens to share
    raw bytes with a frozen file is never swept in just because the hash
    matches (test G.5).

    ``record_payload_sha256``/``inner_packet_sha256`` (a JSON document's
    OWN embedded, canonical-serialization content-identity fields) are
    matched GLOBALLY, independent of ``source_path`` -- these fields exist
    specifically to identify a document's logical content regardless of
    which physical location it currently lives at, and this codebase's own
    append-only evidence-retention convention
    (``decision/crypto_paper_decision_snapshot.py::retain_source()``)
    legitimately copies a frozen record to a content-addressed path
    (``evidence/.../_sources/sha256/<digest>/...``) that is never, and can
    never be, enumerated in advance as its own ``source_path`` entry. A
    frozen record's content-identity must still be recognized there. Never
    a path-only match either direction (a record must also match at least
    one recorded hash).
    """
    doc = freeze if freeze is not None else load_freeze()
    for record in doc["records"]:
        if record["source_path"] == source_path and file_sha256 is not None and record.get("revoked_file_sha256") == file_sha256:
            return True
        if record_payload_sha256 is not None and record.get("revoked_record_payload_sha256") == record_payload_sha256:
            return True
        if inner_packet_sha256 is not None and record.get("revoked_inner_packet_sha256") == inner_packet_sha256:
            return True
    return False


def frozen_record_for(
    source_path: str,
    *,
    file_sha256: str | None = None,
    record_payload_sha256: str | None = None,
    inner_packet_sha256: str | None = None,
    freeze: dict | None = None,
) -> dict | None:
    """The matching frozen record (for audit/error-message purposes), or
    None. Same matching rule as ``is_frozen()``: ``file_sha256`` requires
    ``source_path`` to also match; ``record_payload_sha256``/
    ``inner_packet_sha256`` match globally.
    """
    doc = freeze if freeze is not None else load_freeze()
    for record in doc["records"]:
        if record["source_path"] == source_path and file_sha256 is not None and record.get("revoked_file_sha256") == file_sha256:
            return record
        if record_payload_sha256 is not None and record.get("revoked_record_payload_sha256") == record_payload_sha256:
            return record
        if inner_packet_sha256 is not None and record.get("revoked_inner_packet_sha256") == inner_packet_sha256:
            return record
    return None


def is_released(freeze: dict | None = None) -> bool:
    """True only when the registry's own ``resolution_status`` has moved
    off ``PENDING_GOVERNANCE_RESOLUTION``. A synthetic RELEASED fixture
    used in tests should be constructed and injected explicitly (never by
    editing the real committed file) -- see
    ``test_upbit_identity_taxonomy_governance_freeze.py``'s
    ``released_freeze()`` helper.
    """
    doc = freeze if freeze is not None else load_freeze()
    return doc.get("resolution_status") != PENDING_RESOLUTION_STATUS
