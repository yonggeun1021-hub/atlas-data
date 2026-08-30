#!/usr/bin/env python3
"""P3-12-GOV-05: runtime exact-approval binding for the Upbit PAPER identity
registry and taxonomy -- v2 design (no mutable allowlist).

The v1 design (superseded within this same PR) pinned a mutable
``allowed_approval_evidence`` list a future re-approval would need to
append to. That is circular: a candidate whose own consumer-file pin
covers this repository's runtime code cannot ALSO pre-declare the hash of
a list that must be edited, after the candidate is approved, to actually
register that approval -- editing the list after the fact changes its
hash out from under any pin that named it in advance.

This design removes that list entirely. Trust has exactly two ONE-WAY
chains, both rooted in fields the identity registry / taxonomy documents
carry on themselves -- exactly like the pre-existing (unmodified)
``approval_evidence_ref``/``approval_evidence_sha256`` content-approval
pointer these documents already had before GOV-05:

**Content chain** (unchanged, pre-existing): ``document.approval_evidence_ref``
names a RATIFIED content-approval file, which pins the exact candidate
that proposed ``document``'s own ``mappings``/``records`` content.

**Code chain** (new, this module's actual job): ``document.code_approval_evidence_ref``
names a RATIFIED code-approval file, which pins a successor candidate
that in turn pins the exact consumer file
(``universe/upbit_tradeable_universe.py``), this validator's own file,
and the immutable policy contract below -- and pins the ORIGINAL content
candidate too, so both chains are proven to agree on the same underlying
content.

On this branch ``code_approval_evidence_ref`` is absent from every real
committed document -- there is no future approval to point at yet, and
this module never fabricates one. Absence fails closed by construction,
the same way a missing required field always has in this codebase; no
external list needs to be pre-populated or later mutated for that to be
true. A real future CIO approval populates this field ONCE, by hand,
exactly the way ``approval_evidence_ref`` was originally populated for
content -- never by this module.

``config/upbit_exact_release_binding_policy_contract.json`` is genuinely
immutable: it declares schema/field-shape invariants only (expected
schema-version strings, required field names, the PAPER scope/authority
key vocabularies) and never names a specific approved hash. It never
changes as part of an approval, so a successor candidate's pin on its
hash is never invalidated by the approval process itself.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_CONTRACT_PATH = ROOT / "config" / "upbit_exact_release_binding_policy_contract.json"
CONSUMER_PATH = ROOT / "universe" / "upbit_tradeable_universe.py"
VALIDATOR_PATH = Path(__file__).resolve()
FREEZE_PATH = ROOT / "config" / "upbit_identity_taxonomy_governance_freeze.json"
SCHEMA_VERSION = "upbit_exact_release_binding_policy_contract/1"

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
    """Fail-closed P3-12-GOV-05 policy-contract integrity violation."""


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


def load_policy_contract(path: Path = POLICY_CONTRACT_PATH) -> dict:
    """Load and fully self-validate the IMMUTABLE policy/schema contract.

    Fail-closed (raise) on any structural defect. This file never names a
    specific approved hash -- there is nothing here for a real approval
    event to mutate, and nothing here should ever need to change as part
    of approving a release.
    """
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExactReleaseBindingError(f"POLICY_CONTRACT_READ_FAILED:{exc}") from exc
    if not isinstance(doc, dict):
        raise ExactReleaseBindingError("POLICY_CONTRACT_ROOT_INVALID")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ExactReleaseBindingError("POLICY_CONTRACT_SCHEMA_VERSION_MISMATCH")
    declared_hash = doc.get("payload_sha256")
    if not isinstance(declared_hash, str):
        raise ExactReleaseBindingError("POLICY_CONTRACT_SELF_HASH_MISSING")
    recomputed = payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
    if recomputed != declared_hash:
        raise ExactReleaseBindingError("POLICY_CONTRACT_SELF_HASH_MISMATCH")
    authority = doc.get("authority")
    if not isinstance(authority, dict) or not authority:
        raise ExactReleaseBindingError("POLICY_CONTRACT_AUTHORITY_MISSING")
    for field, value in authority.items():
        if value is not False:
            raise ExactReleaseBindingError(f"POLICY_CONTRACT_AUTHORITY_INVARIANT_VIOLATED:{field}")
    return doc


def _verify_content_chain(
    document: dict, *, content_field: str, contract: dict, repo_root: Path,
) -> tuple[bool, dict | None]:
    """Content chain only: does ``document``'s own pre-existing
    ``approval_evidence_ref``/``source_candidate_packet`` pointers resolve
    to a RATIFIED content approval whose candidate proposed EXACTLY
    ``document[content_field]``? Returns ``(ok, candidate_or_none)`` so the
    code chain below can cross-check it names the SAME candidate.
    """
    required = {"approval_evidence_ref", "approval_evidence_sha256", "approved_candidate_payload_sha256", "source_candidate_packet"}
    if not required.issubset(document):
        return False, None
    source = document["source_candidate_packet"]
    if not isinstance(source, dict) or not {"path", "file_sha256", "payload_sha256"}.issubset(source):
        return False, None

    try:
        approval_path = _resolve_repo_path(document["approval_evidence_ref"], repo_root)
    except ExactReleaseBindingError:
        return False, None
    if not approval_path.is_file() or file_sha256(approval_path) != document["approval_evidence_sha256"]:
        return False, None
    approval = _read_json_or_none(approval_path)
    if approval is None or approval.get("schema_version") != contract["content_approval_schema_version"]:
        return False, None
    if approval.get("approval_status") != "RATIFIED" or approval.get("ratified_by") != "CIO_USER":
        return False, None
    scope = approval.get("approved_scope")
    if not isinstance(scope, dict) or set(scope) != set(contract["paper_scope_keys"]) or any(v is not True for v in scope.values()):
        return False, None
    approval_authority = approval.get("authority")
    if not isinstance(approval_authority, dict) or set(approval_authority) != set(contract["forbidden_authority_keys"]) or any(
        v is not False for v in approval_authority.values()
    ):
        return False, None

    candidate_pin = approval.get("candidate")
    if not isinstance(candidate_pin, dict) or candidate_pin.get("path") != source["path"]:
        return False, None
    try:
        candidate_path = _resolve_repo_path(source["path"], repo_root)
    except ExactReleaseBindingError:
        return False, None
    if not candidate_path.is_file():
        return False, None
    live_candidate_hash = file_sha256(candidate_path)
    if live_candidate_hash != source["file_sha256"] or live_candidate_hash != candidate_pin.get("file_sha256"):
        return False, None

    candidate = _read_json_or_none(candidate_path)
    if candidate is None:
        return False, None
    unsigned = {k: v for k, v in candidate.items() if k != "payload_sha256"}
    live_payload_hash = payload_sha256(unsigned)
    if (
        candidate.get("payload_sha256") != live_payload_hash
        or live_payload_hash != source["payload_sha256"]
        or live_payload_hash != candidate_pin.get("payload_sha256")
    ):
        return False, None
    candidate_authority = candidate.get("authority")
    if not isinstance(candidate_authority, dict) or not candidate_authority or any(
        v is not False for v in candidate_authority.values()
    ):
        return False, None

    cfg = _CONTENT_CONFIG[content_field]
    proposed = candidate.get(cfg["proposed_key"])
    if not isinstance(proposed, dict) or proposed.get(content_field) != document.get(content_field):
        return False, None
    proposed_payload_hash = payload_sha256(proposed)
    if (
        candidate.get(cfg["proposed_payload_field"]) != proposed_payload_hash
        or document["approved_candidate_payload_sha256"] != proposed_payload_hash
        or candidate_pin.get(cfg["approval_candidate_payload_field"]) != proposed_payload_hash
    ):
        return False, None
    return True, candidate


def _verify_code_chain(
    document: dict, *, content_candidate: dict, contract: dict, repo_root: Path,
) -> bool:
    """Code chain: does ``document.code_approval_evidence_ref`` resolve to a
    RATIFIED code approval whose pinned successor candidate (a) pins the
    CURRENT, live consumer/validator/policy-contract files exactly, and
    (b) pins the SAME base content candidate the content chain above just
    verified? Absent/missing fields fail closed -- there is nothing here
    for a real approval to have mutated in advance; a genuine future
    approval populates this field once, by hand.
    """
    ref = document.get("code_approval_evidence_ref")
    sha = document.get("code_approval_evidence_sha256")
    if not isinstance(ref, str) or not isinstance(sha, str):
        return False
    try:
        approval_path = _resolve_repo_path(ref, repo_root)
    except ExactReleaseBindingError:
        return False
    if not approval_path.is_file() or file_sha256(approval_path) != sha:
        return False
    approval = _read_json_or_none(approval_path)
    if approval is None or approval.get("schema_version") != contract["code_approval_schema_version"]:
        return False
    if approval.get("approval_status") != "RATIFIED" or approval.get("ratified_by") != "CIO_USER":
        return False
    approval_authority = approval.get("authority")
    if not isinstance(approval_authority, dict) or not approval_authority or any(
        v is not False for v in approval_authority.values()
    ):
        return False

    successor_pin = approval.get("successor_candidate")
    if not isinstance(successor_pin, dict):
        return False
    try:
        successor_path = _resolve_repo_path(successor_pin.get("path"), repo_root)
    except ExactReleaseBindingError:
        return False
    if not successor_path.is_file():
        return False
    live_successor_hash = file_sha256(successor_path)
    if live_successor_hash != successor_pin.get("file_sha256"):
        return False
    successor = _read_json_or_none(successor_path)
    if successor is None or successor.get("schema_version") != contract["successor_candidate_schema_version"]:
        return False
    unsigned = {k: v for k, v in successor.items() if k != "payload_sha256"}
    live_payload_hash = payload_sha256(unsigned)
    if successor.get("payload_sha256") != live_payload_hash or live_payload_hash != successor_pin.get("payload_sha256"):
        return False
    successor_authority = successor.get("authority")
    if not isinstance(successor_authority, dict) or not successor_authority or any(
        v is not False for v in successor_authority.values()
    ):
        return False
    # The successor candidate is an immutable proposal artifact, the same
    # way identity/upbit_paper_identity_hardening_candidate.py's own
    # candidate packets are -- it keeps declaring itself unapproved
    # forever. Approval is signaled ENTIRELY by the existence of a
    # separate, hash-verified code-approval file that references it (the
    # check this function is already doing above), never by flipping a
    # field inside the candidate itself.
    if successor.get("release_ready") is not False or successor.get("exact_hash_cio_approval_present") is not False:
        return False

    code_binding = successor.get("code_binding")
    if not isinstance(code_binding, dict):
        return False
    for label, path_const in (
        ("consumer_file", CONSUMER_PATH), ("validator_file", VALIDATOR_PATH), ("policy_contract", POLICY_CONTRACT_PATH),
    ):
        pin = code_binding.get(label)
        if not isinstance(pin, dict):
            return False
        try:
            live_path = _resolve_repo_path(str(path_const.relative_to(ROOT)), repo_root)
        except ExactReleaseBindingError:
            return False
        if not live_path.is_file() or file_sha256(live_path) != pin.get("sha256"):
            return False

    base_pin = successor.get("base_candidate")
    if not isinstance(base_pin, dict):
        return False
    try:
        base_path = _resolve_repo_path(base_pin.get("path"), repo_root)
    except ExactReleaseBindingError:
        return False
    if not base_path.is_file() or file_sha256(base_path) != base_pin.get("file_sha256"):
        return False
    base_candidate = _read_json_or_none(base_path)
    if base_candidate is None:
        return False
    unsigned_base = {k: v for k, v in base_candidate.items() if k != "payload_sha256"}
    if base_candidate.get("payload_sha256") != payload_sha256(unsigned_base) or base_candidate.get("payload_sha256") != base_pin.get("payload_sha256"):
        return False

    # The successor's own declared base candidate must be the SAME
    # candidate the content chain independently resolved -- both chains
    # must agree on one underlying content approval.
    return base_candidate == content_candidate


def _verify_freeze_cross_reference(content_field: str, content_candidate: dict, repo_root: Path) -> bool:
    freeze = _read_json_or_none(_resolve_repo_path(str(FREEZE_PATH.relative_to(ROOT)), repo_root))
    if freeze is None:
        return False
    resolution = freeze.get("approval_resolution")
    if not isinstance(resolution, dict):
        return False
    cfg = _CONTENT_CONFIG[content_field]
    proposed = content_candidate.get(cfg["proposed_key"])
    if not isinstance(proposed, dict):
        return False
    if resolution.get(cfg["freeze_resolution_payload_field"]) != payload_sha256(proposed):
        return False
    if content_field == "mappings":
        released = freeze.get("released_paper_markets")
        if not isinstance(released, list) or sorted(released) != sorted(proposed.get("mappings") or {}):
            return False
    return True


def validate_exact_release(document: dict, *, content_field: str, repo_root: Path | None = None) -> bool:
    """True iff ``document`` (a loaded registry or taxonomy dict) is bound,
    by two independent one-way chains rooted in its own fields, to a
    RATIFIED content approval AND a RATIFIED code approval that together
    agree on the exact same underlying content and on the exact current
    runtime code. Never raises for an ordinary not-yet-approved document
    (including every real document on this branch) -- only
    ``load_policy_contract()``'s own read raises, and only for a
    genuinely malformed/forged contract file.
    """
    if repo_root is None:
        repo_root = ROOT
    if content_field not in _CONTENT_CONFIG:
        raise ExactReleaseBindingError(f"UNKNOWN_CONTENT_FIELD:{content_field}")
    contract = load_policy_contract(_resolve_repo_path(str(POLICY_CONTRACT_PATH.relative_to(ROOT)), repo_root))
    if not isinstance(document, dict):
        return False

    content_ok, content_candidate = _verify_content_chain(
        document, content_field=content_field, contract=contract, repo_root=repo_root,
    )
    if not content_ok or content_candidate is None:
        return False
    if not _verify_code_chain(document, content_candidate=content_candidate, contract=contract, repo_root=repo_root):
        return False
    return _verify_freeze_cross_reference(content_field, content_candidate, repo_root)
