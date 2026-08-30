#!/usr/bin/env python3
"""P3-12-GOV-05: runtime exact-approval binding for the Upbit PAPER identity
registry and taxonomy.

``universe/upbit_tradeable_universe.py::_approval_effective()`` (shared by
``effective_identity_mapping()`` and the taxonomy gate inside
``build_classification()``) previously granted effect to a
``config/upbit_asset_identity_registry.json``/``config/upbit_exclusion_taxonomy.json``
document the instant its own ``approval_status`` field read ``"RATIFIED"`` --
with no binding whatsoever to the document's actual content. Anything that
could ever make that field say ``RATIFIED`` again (a bad revert, a bad
merge, a restored-from-backup file, a hand edit) would silently reactivate
whatever mapping/taxonomy content happened to be sitting in the file at
that moment -- including a previously-known-invalid one (P3-12-GOV-04
finding P1).

This module closes that gap with an ALLOWLIST, not a denylist: rather than
naming specific bad historical hashes to block, it verifies that a
document's *entire* exact-hash provenance chain -- its own embedded
``approval_evidence_ref``/``source_candidate_packet`` pointers, the
approval evidence file those pointers name, the candidate packet that
evidence approved, and this repository's own consumer file -- all
currently resolve, byte-for-byte, to the ONE approval-evidence file this
repo's own committed contract (``config/upbit_exact_release_binding_contract.json``)
explicitly allowlists. Any document that is not an exact, unmodified
projection of that one approved candidate -- whether it is the historical
55-mapping/282-record content, a single tampered field, an added market, a
forged self-consistent-but-never-approved chain, or the CURRENT approved
content with one byte changed -- fails this check. ``approval_status``
alone can never revive it.

On this branch the contract's ``allowed_approval_evidence`` list is
deliberately EMPTY: adding this validation to
``universe/upbit_tradeable_universe.py`` itself changes that file's own
bytes, so the existing v2 exact-hash approval (which pinned the file's
PRE-this-change hash as ``consumer_file_sha256``) can never satisfy this
check again regardless of the allowlist's contents. The empty allowlist
makes this explicit and structural rather than an incidental side effect:
this branch's identity/taxonomy authority is
``PENDING_EXACT_HASH_REAPPROVAL`` until a future, separate, explicit CIO
decision names a new approval-evidence file here.

Returns ``False`` (never raises) for "not currently approved" -- a
completely ordinary, expected state (including on this very branch right
now). Raises ``ExactReleaseBindingError`` only when the CONTRACT file
itself is missing, malformed, self-hash-forged, or declares any
authority `True` -- fail-closed on an integrity violation of the trust
root itself, exactly like ``load_freeze()``-style modules elsewhere in
this codebase.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "upbit_exact_release_binding_contract.json"
CONSUMER_PATH = ROOT / "universe" / "upbit_tradeable_universe.py"
FREEZE_PATH = ROOT / "config" / "upbit_identity_taxonomy_governance_freeze.json"
SCHEMA_VERSION = "upbit_exact_release_binding_contract/1"
PENDING_STATUS = "PENDING_EXACT_HASH_REAPPROVAL"

# One entry per document kind this module can bind: the live document's own
# field name for its authoritative content, the matching field on a
# candidate's ``proposed_registry``/``proposed_taxonomy`` block, and the
# field name the approval evidence's own ``candidate`` pin block uses for
# that content's payload hash.
_CONTENT_CONFIG = {
    "mappings": {
        "proposed_key": "proposed_registry",
        "proposed_payload_field": "proposed_registry_payload_sha256",
        "approval_candidate_payload_field": "registry_payload_sha256",
        "freeze_resolution_payload_field": "registry_candidate_payload_sha256",
    },
    "records": {
        "proposed_key": "proposed_taxonomy",
        "proposed_payload_field": "proposed_taxonomy_payload_sha256",
        "approval_candidate_payload_field": "taxonomy_payload_sha256",
        "freeze_resolution_payload_field": "taxonomy_candidate_payload_sha256",
    },
}


class ExactReleaseBindingError(ValueError):
    """Fail-closed P3-12-GOV-05 binding-contract integrity violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve_repo_path(relative_path, repo_root: Path) -> Path:
    if not isinstance(relative_path, str) or not relative_path or relative_path.startswith("/"):
        raise ExactReleaseBindingError(f"PATH_INVALID:{relative_path!r}")
    candidate = (Path(repo_root) / relative_path).resolve()
    try:
        candidate.relative_to(Path(repo_root).resolve())
    except ValueError as exc:
        raise ExactReleaseBindingError(f"PATH_OUTSIDE_REPOSITORY:{relative_path!r}") from exc
    return candidate


def _read_json_or_none(path: Path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_binding_contract(path: Path = CONTRACT_PATH) -> dict:
    """Load and fully self-validate the exact-release binding contract.

    Fail-closed (raise) on any structural defect -- a malformed or forged
    contract must never be silently treated as either "nothing allowlisted"
    (which would be indistinguishable from the correct PENDING state, hiding
    real tampering) or silently trusted.
    """
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExactReleaseBindingError(f"CONTRACT_READ_FAILED:{exc}") from exc
    if not isinstance(doc, dict):
        raise ExactReleaseBindingError("CONTRACT_ROOT_INVALID")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ExactReleaseBindingError("CONTRACT_SCHEMA_VERSION_MISMATCH")
    declared_hash = doc.get("payload_sha256")
    if not isinstance(declared_hash, str):
        raise ExactReleaseBindingError("CONTRACT_SELF_HASH_MISSING")
    recomputed = payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
    if recomputed != declared_hash:
        raise ExactReleaseBindingError("CONTRACT_SELF_HASH_MISMATCH")
    authority = doc.get("authority")
    if not isinstance(authority, dict) or not authority:
        raise ExactReleaseBindingError("CONTRACT_AUTHORITY_MISSING")
    for field, value in authority.items():
        if value is not False:
            raise ExactReleaseBindingError(f"CONTRACT_AUTHORITY_INVARIANT_VIOLATED:{field}")
    allowed = doc.get("allowed_approval_evidence")
    if not isinstance(allowed, list):
        raise ExactReleaseBindingError("CONTRACT_ALLOWED_LIST_INVALID")
    for row in allowed:
        if not isinstance(row, dict) or not {"path", "file_sha256"}.issubset(row):
            raise ExactReleaseBindingError("CONTRACT_ALLOWED_ENTRY_INVALID")
        if not isinstance(row["path"], str) or not isinstance(row["file_sha256"], str):
            raise ExactReleaseBindingError("CONTRACT_ALLOWED_ENTRY_INVALID")
    return doc


def is_released(contract: dict | None = None) -> bool:
    doc = contract if contract is not None else load_binding_contract()
    return doc.get("resolution_status") != PENDING_STATUS


def _validate_freeze_cross_reference(
    document: dict, content_field: str, candidate: dict, candidate_path: Path, repo_root: Path,
) -> bool:
    freeze = _read_json_or_none(_resolve_repo_path(
        str(FREEZE_PATH.relative_to(ROOT)), repo_root,
    ))
    if freeze is None:
        return False
    resolution = freeze.get("approval_resolution")
    if not isinstance(resolution, dict):
        return False
    candidate_path_str = str(candidate_path.relative_to(Path(repo_root).resolve()))
    if resolution.get("candidate_packet_path") != candidate_path_str:
        return False
    if resolution.get("candidate_packet_file_sha256") != file_sha256(candidate_path):
        return False
    if resolution.get("candidate_packet_payload_sha256") != candidate.get("payload_sha256"):
        return False
    cfg = _CONTENT_CONFIG[content_field]
    proposed = candidate.get(cfg["proposed_key"])
    if not isinstance(proposed, dict):
        return False
    if resolution.get(cfg["freeze_resolution_payload_field"]) != payload_sha256(proposed):
        return False
    if resolution.get("consumer_file_sha256") != candidate.get("consumer_file_sha256"):
        return False
    if content_field == "mappings":
        released = freeze.get("released_paper_markets")
        if not isinstance(released, list) or sorted(released) != sorted(document.get("mappings") or {}):
            return False
    return True


def validate_exact_release(
    document: dict, *, content_field: str, repo_root: Path | None = None,
) -> bool:
    """True iff ``document`` (a loaded registry or taxonomy dict) is an
    exact, unmodified projection of the ONE approval-evidence file this
    repository's own binding contract currently allowlists.

    ``content_field`` is ``"mappings"`` for the identity registry or
    ``"records"`` for the taxonomy. Never raises for an ordinary
    not-yet-approved document (including every real document on this
    branch right now) -- only ``load_binding_contract()``'s own read
    raises, and only for a genuinely malformed/forged contract file.
    """
    if repo_root is None:
        repo_root = ROOT
    if content_field not in _CONTENT_CONFIG:
        raise ExactReleaseBindingError(f"UNKNOWN_CONTENT_FIELD:{content_field}")
    cfg = _CONTENT_CONFIG[content_field]
    contract = load_binding_contract(
        _resolve_repo_path(str(CONTRACT_PATH.relative_to(ROOT)), repo_root)
    )
    if not isinstance(document, dict):
        return False

    required_provenance = {
        "approval_evidence_ref", "approval_evidence_sha256",
        "approved_candidate_payload_sha256", "source_candidate_packet",
    }
    if not required_provenance.issubset(document):
        return False
    source = document["source_candidate_packet"]
    if not isinstance(source, dict) or not {"path", "file_sha256", "payload_sha256"}.issubset(source):
        return False

    approval_ref = document["approval_evidence_ref"]
    try:
        approval_path = _resolve_repo_path(approval_ref, repo_root)
    except ExactReleaseBindingError:
        return False
    if not approval_path.is_file():
        return False
    live_approval_hash = file_sha256(approval_path)
    if live_approval_hash != document["approval_evidence_sha256"]:
        return False

    allowed = contract["allowed_approval_evidence"]
    if not any(row["path"] == approval_ref and row["file_sha256"] == live_approval_hash for row in allowed):
        return False

    approval = _read_json_or_none(approval_path)
    if approval is None:
        return False
    if approval.get("approval_status") != "RATIFIED" or approval.get("ratified_by") != "CIO_USER":
        return False
    scope = approval.get("approved_scope")
    if not isinstance(scope, dict) or not scope or any(value is not True for value in scope.values()):
        return False
    approval_authority = approval.get("authority")
    if not isinstance(approval_authority, dict) or not approval_authority or any(
        value is not False for value in approval_authority.values()
    ):
        return False

    candidate_pin = approval.get("candidate")
    if not isinstance(candidate_pin, dict) or candidate_pin.get("path") != source["path"]:
        return False
    try:
        candidate_path = _resolve_repo_path(source["path"], repo_root)
    except ExactReleaseBindingError:
        return False
    if not candidate_path.is_file():
        return False
    live_candidate_file_hash = file_sha256(candidate_path)
    if live_candidate_file_hash != source["file_sha256"] or live_candidate_file_hash != candidate_pin.get("file_sha256"):
        return False

    candidate = _read_json_or_none(candidate_path)
    if candidate is None:
        return False
    unsigned = {key: value for key, value in candidate.items() if key != "payload_sha256"}
    live_candidate_payload_hash = payload_sha256(unsigned)
    if (
        candidate.get("payload_sha256") != live_candidate_payload_hash
        or live_candidate_payload_hash != source["payload_sha256"]
        or live_candidate_payload_hash != candidate_pin.get("payload_sha256")
    ):
        return False
    candidate_authority = candidate.get("authority")
    if not isinstance(candidate_authority, dict) or not candidate_authority or any(
        value is not False for value in candidate_authority.values()
    ):
        return False

    proposed = candidate.get(cfg["proposed_key"])
    if not isinstance(proposed, dict):
        return False
    if proposed.get(content_field) != document.get(content_field):
        return False
    proposed_payload_hash = payload_sha256(proposed)
    if (
        candidate.get(cfg["proposed_payload_field"]) != proposed_payload_hash
        or document["approved_candidate_payload_sha256"] != proposed_payload_hash
        or candidate_pin.get(cfg["approval_candidate_payload_field"]) != proposed_payload_hash
    ):
        return False

    consumer_pin = candidate.get("consumer_file_sha256")
    try:
        live_consumer_hash = file_sha256(_resolve_repo_path(
            str(CONSUMER_PATH.relative_to(ROOT)), repo_root,
        ))
    except ExactReleaseBindingError:
        return False
    if consumer_pin != live_consumer_hash or candidate_pin.get("consumer_file_sha256") != consumer_pin:
        return False

    return _validate_freeze_cross_reference(document, content_field, candidate, candidate_path, repo_root)
