#!/usr/bin/env python3
"""P3-12-GOV-05: deterministic release builder / committed-release
validator for the code-approval chain.

Given a genuine, RATIFIED code approval (already independently verifiable
by ``governance/upbit_exact_release_binding.py``'s code chain), this
module computes the ONE deterministic projection of what the identity
registry / taxonomy / governance freeze documents must contain to reflect
that approval -- the content fields (``mappings``/``records``, and every
other field) stay byte-for-byte unchanged; only the two new
``code_approval_evidence_ref``/``code_approval_evidence_sha256`` fields
(and a matching ``code_approval_resolution`` block on the freeze
document) are added, both pointing at the SAME code approval file.

This removes the "a person edits the JSON by hand" release step: given a
code approval, ``build_release_projection()`` is the only way those three
fields are supposed to be populated, and ``validate_committed_release()``
proves a committed set of files is byte-for-byte exactly that projection
and nothing else. Neither function ever calls the network, mutates a
file, or grants any authority.

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
import json
from pathlib import Path


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReleaseProjectionError(ValueError):
    """Fail-closed deterministic-projection violation."""


def build_release_projection(
    *,
    code_approval: dict,
    code_approval_path: str,
    code_approval_file_sha256: str,
    current_registry: dict,
    current_taxonomy: dict,
    current_freeze: dict,
) -> dict:
    """Return ``{"registry": ..., "taxonomy": ..., "freeze": ...}`` -- the
    deterministic projection of ``current_registry``/``current_taxonomy``/
    ``current_freeze`` under ``code_approval``. Every existing field is
    preserved byte-for-byte (deep-copied, never mutated in place); only
    the code-approval pointer fields are added/overwritten.

    Raises ``ReleaseProjectionError`` if ``code_approval`` itself is
    missing the fields this projection needs to be well-defined -- this
    function does not re-verify the FULL code chain (that is
    ``governance/upbit_exact_release_binding.py``'s job, and callers
    should have already required it to pass); it only refuses to project
    from an approval that is not even minimally shaped correctly.
    """
    if code_approval.get("approval_status") != "RATIFIED":
        raise ReleaseProjectionError("CODE_APPROVAL_NOT_RATIFIED")
    ratified_at_utc = code_approval.get("ratified_at_utc")
    if not isinstance(ratified_at_utc, str) or not ratified_at_utc:
        raise ReleaseProjectionError("CODE_APPROVAL_RATIFIED_AT_MISSING")
    successor_pin = code_approval.get("successor_candidate")
    if not isinstance(successor_pin, dict) or not {"path", "file_sha256", "payload_sha256"}.issubset(successor_pin):
        raise ReleaseProjectionError("CODE_APPROVAL_SUCCESSOR_PIN_INVALID")

    registry = copy.deepcopy(current_registry)
    registry["code_approval_evidence_ref"] = code_approval_path
    registry["code_approval_evidence_sha256"] = code_approval_file_sha256

    taxonomy = copy.deepcopy(current_taxonomy)
    taxonomy["code_approval_evidence_ref"] = code_approval_path
    taxonomy["code_approval_evidence_sha256"] = code_approval_file_sha256

    freeze = copy.deepcopy(current_freeze)
    freeze["code_approval_resolution"] = {
        "code_approval_evidence_ref": code_approval_path,
        "code_approval_evidence_sha256": code_approval_file_sha256,
        "ratified_at_utc": ratified_at_utc,
        "successor_candidate_path": successor_pin["path"],
        "successor_candidate_file_sha256": successor_pin["file_sha256"],
        "successor_candidate_payload_sha256": successor_pin["payload_sha256"],
    }

    return {"registry": registry, "taxonomy": taxonomy, "freeze": freeze}


def validate_committed_release(
    *,
    code_approval_path: Path,
    current_registry_path: Path,
    current_taxonomy_path: Path,
    current_freeze_path: Path,
    committed_registry_path: Path,
    committed_taxonomy_path: Path,
    committed_freeze_path: Path,
) -> None:
    """Raise unless the committed registry/taxonomy/freeze files are
    EXACTLY ``build_release_projection()``'s output given the code
    approval at ``code_approval_path`` and the pre-code-approval
    (content-only) registry/taxonomy/freeze at the ``current_*_path``
    arguments. This is the release-side mirror of
    ``identity/upbit_paper_identity_hardening_release.py::validate_committed_release()``
    for the code chain.
    """
    try:
        code_approval = json.loads(Path(code_approval_path).read_text(encoding="utf-8"))
        current_registry = json.loads(Path(current_registry_path).read_text(encoding="utf-8"))
        current_taxonomy = json.loads(Path(current_taxonomy_path).read_text(encoding="utf-8"))
        current_freeze = json.loads(Path(current_freeze_path).read_text(encoding="utf-8"))
        committed_registry = json.loads(Path(committed_registry_path).read_text(encoding="utf-8"))
        committed_taxonomy = json.loads(Path(committed_taxonomy_path).read_text(encoding="utf-8"))
        committed_freeze = json.loads(Path(committed_freeze_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseProjectionError(f"COMMITTED_RELEASE_READ_FAILED:{exc}") from exc

    expected = build_release_projection(
        code_approval=code_approval,
        code_approval_path=str(code_approval_path),
        code_approval_file_sha256=file_sha256(code_approval_path),
        current_registry=current_registry,
        current_taxonomy=current_taxonomy,
        current_freeze=current_freeze,
    )
    actual = {"registry": committed_registry, "taxonomy": committed_taxonomy, "freeze": committed_freeze}
    if actual != expected:
        raise ReleaseProjectionError("COMMITTED_RELEASE_DOCUMENT_MISMATCH")
