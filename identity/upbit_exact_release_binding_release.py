#!/usr/bin/env python3
"""P3-12-GOV-05: deterministic release builder / committed-release
validator for the code-approval chain -- v2 (release-grade: reuses the
EXACT SAME code-chain verification the runtime validator uses, never a
separate/weaker check; takes only a repo-relative approval path and
computes every hash itself; the committed-release validator re-runs the
projected registry/taxonomy through the real runtime validator end to
end, including the freeze cross-reference).

Given a genuine, RATIFIED code approval, this module computes the ONE
deterministic projection of what the identity registry / taxonomy /
governance freeze documents must contain to reflect that approval -- the
content fields (``mappings``/``records``, and every other field) stay
byte-for-byte unchanged; only the two new
``code_approval_evidence_ref``/``code_approval_evidence_sha256`` fields
(and a matching ``code_approval_resolution`` block on the freeze
document, produced by ``governance/upbit_exact_release_binding.py::verify_code_chain()``
itself -- never hand-assembled here) are added.

This removes the "a person edits the JSON by hand" release step. Before
this module ever projects anything, it runs the approval through
``verify_code_chain()`` -- the exact same function
``governance/upbit_exact_release_binding.py::validate_exact_release()``
calls at runtime -- so a malformed, unratified, wrong-authority, or
otherwise-invalid approval is refused here just as it would be refused
at runtime; there is only one code-chain-verification implementation.
``build_release_projection()`` only accepts a ``repo_root`` plus a
repo-relative approval path -- it computes the approval's file hash
itself and never trusts a caller-supplied hash or an absolute/
outside-repository path.

This module works on plain dicts/paths its caller supplies -- it never
looks at, or writes to, this repository's own real committed
registry/taxonomy/freeze files. On this branch, no genuine code approval
exists yet, so this module is only exercised by synthetic fixtures (see
test/test_upbit_exact_release_binding_release.py); it is never invoked
against real config in this PR.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

_GOVERNANCE_SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_release_governance",
    ROOT / "governance" / "upbit_exact_release_binding.py",
)
GOVERNANCE = importlib.util.module_from_spec(_GOVERNANCE_SPEC)
_GOVERNANCE_SPEC.loader.exec_module(GOVERNANCE)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReleaseProjectionError(ValueError):
    """Fail-closed deterministic-projection violation."""


def _content_source_pin(document: dict, label: str) -> dict:
    pin = GOVERNANCE._canonical_pin(document.get("source_candidate_packet"))
    if pin is None:
        raise ReleaseProjectionError(f"{label}_SOURCE_CANDIDATE_PACKET_INVALID")
    return pin


def build_release_projection(
    *,
    repo_root: Path,
    code_approval_relative_path: str,
    current_registry: dict,
    current_taxonomy: dict,
    current_freeze: dict,
    evaluation_as_of: str,
) -> dict:
    """Return ``{"registry": ..., "taxonomy": ..., "freeze": ...}`` -- the
    deterministic projection of ``current_registry``/``current_taxonomy``/
    ``current_freeze`` under the code approval at
    ``code_approval_relative_path`` (repo-relative; resolved and hashed
    HERE, never trusting a caller-supplied path or hash). Every existing
    field is preserved byte-for-byte (deep-copied, never mutated in
    place); only the code-approval pointer fields are added.

    Raises ``ReleaseProjectionError`` if the approval path is absolute,
    outside the repository, missing, or if
    ``governance/upbit_exact_release_binding.py::verify_code_chain()`` --
    the SAME full validation the runtime validator performs -- does not
    accept it. ``current_registry``/``current_taxonomy`` must each have
    already gone through the content-approval chain (their own,
    pre-existing ``source_candidate_packet`` pin identifies the base
    candidate this code approval must also pin, by the canonical 3-tuple
    -- extra registry/taxonomy metadata on that pin is preserved, never
    required of the successor's simpler pin).
    """
    try:
        approval_path = GOVERNANCE._resolve_repo_path(code_approval_relative_path, repo_root)
    except GOVERNANCE.ExactReleaseBindingError as exc:
        raise ReleaseProjectionError(f"CODE_APPROVAL_PATH_INVALID:{exc}") from exc
    if not approval_path.is_file():
        raise ReleaseProjectionError("CODE_APPROVAL_FILE_MISSING")
    code_approval_file_sha256 = file_sha256(approval_path)

    registry_source_pin = _content_source_pin(current_registry, "REGISTRY")
    taxonomy_source_pin = _content_source_pin(current_taxonomy, "TAXONOMY")
    if registry_source_pin != taxonomy_source_pin:
        raise ReleaseProjectionError("REGISTRY_AND_TAXONOMY_BASE_CANDIDATE_PIN_MISMATCH")

    contract = GOVERNANCE.load_policy_contract(
        GOVERNANCE._resolve_repo_path(
            str(GOVERNANCE.POLICY_CONTRACT_PATH.relative_to(GOVERNANCE.ROOT)), repo_root,
        )
    )
    ok, resolution = GOVERNANCE.verify_code_chain(
        code_approval_ref=code_approval_relative_path,
        code_approval_sha256=code_approval_file_sha256,
        content_source_pin=registry_source_pin,
        evaluation_as_of=evaluation_as_of,
        contract=contract,
        repo_root=repo_root,
    )
    if not ok or resolution is None:
        raise ReleaseProjectionError("CODE_APPROVAL_CHAIN_INVALID")

    registry = copy.deepcopy(current_registry)
    registry["code_approval_evidence_ref"] = code_approval_relative_path
    registry["code_approval_evidence_sha256"] = code_approval_file_sha256

    taxonomy = copy.deepcopy(current_taxonomy)
    taxonomy["code_approval_evidence_ref"] = code_approval_relative_path
    taxonomy["code_approval_evidence_sha256"] = code_approval_file_sha256

    freeze = copy.deepcopy(current_freeze)
    freeze["code_approval_resolution"] = resolution

    return {"registry": registry, "taxonomy": taxonomy, "freeze": freeze}


def validate_committed_release(
    *,
    repo_root: Path,
    code_approval_relative_path: str,
    current_registry: dict,
    current_taxonomy: dict,
    current_freeze: dict,
    committed_registry_relative_path: str,
    committed_taxonomy_relative_path: str,
    committed_freeze_relative_path: str,
    evaluation_as_of: str,
) -> None:
    """Raise unless the committed registry/taxonomy/freeze files are
    EXACTLY ``build_release_projection()``'s output for the code approval
    at ``code_approval_relative_path``, AND the committed registry AND
    taxonomy both independently pass the real runtime validator
    (``governance.validate_exact_release()``) at ``evaluation_as_of`` --
    which itself requires the committed freeze's ``code_approval_resolution``
    to match. Projection equality alone is not accepted as sufficient:
    only an end-to-end pass through the real runtime path counts as a
    valid committed release.
    """
    expected = build_release_projection(
        repo_root=repo_root,
        code_approval_relative_path=code_approval_relative_path,
        current_registry=current_registry,
        current_taxonomy=current_taxonomy,
        current_freeze=current_freeze,
        evaluation_as_of=evaluation_as_of,
    )

    try:
        committed_registry_path = GOVERNANCE._resolve_repo_path(committed_registry_relative_path, repo_root)
        committed_taxonomy_path = GOVERNANCE._resolve_repo_path(committed_taxonomy_relative_path, repo_root)
        committed_freeze_path = GOVERNANCE._resolve_repo_path(committed_freeze_relative_path, repo_root)
    except GOVERNANCE.ExactReleaseBindingError as exc:
        raise ReleaseProjectionError(f"COMMITTED_RELEASE_PATH_INVALID:{exc}") from exc
    try:
        committed_registry = json.loads(committed_registry_path.read_text(encoding="utf-8"))
        committed_taxonomy = json.loads(committed_taxonomy_path.read_text(encoding="utf-8"))
        committed_freeze = json.loads(committed_freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseProjectionError(f"COMMITTED_RELEASE_READ_FAILED:{exc}") from exc

    actual = {"registry": committed_registry, "taxonomy": committed_taxonomy, "freeze": committed_freeze}
    if actual != expected:
        raise ReleaseProjectionError("COMMITTED_RELEASE_DOCUMENT_MISMATCH")

    if not GOVERNANCE.validate_exact_release(
        committed_registry, content_field="mappings", evaluation_as_of=evaluation_as_of, repo_root=repo_root,
    ):
        raise ReleaseProjectionError("COMMITTED_REGISTRY_FAILS_RUNTIME_VALIDATION")
    if not GOVERNANCE.validate_exact_release(
        committed_taxonomy, content_field="records", evaluation_as_of=evaluation_as_of, repo_root=repo_root,
    ):
        raise ReleaseProjectionError("COMMITTED_TAXONOMY_FAILS_RUNTIME_VALIDATION")
