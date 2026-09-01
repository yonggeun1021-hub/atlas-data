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
value-for-value unchanged; only the two new
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

Before projecting, the builder runs both current documents through the
same public ``verify_content_chain()`` implementation used at runtime.
The shipped registry's redundant ``source_candidate_packet`` pin must
match the approval; the shipped taxonomy has no such field and resolves
the same canonical base-candidate pin from its hash-verified content
approval.  This preserves the real committed shapes instead of requiring
a synthetic taxonomy-only field.

The original release projection works on plain dicts/paths its caller
supplies and never writes this repository's real committed configuration.
The append-only population-transition projection below reads the live
registry/taxonomy/freeze only to validate their already-ratified exact
content+code chains; it never mutates them. Without a genuine code approval
the validation fails closed and no successor can be emitted.
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

_UNIVERSE_SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_release_universe",
    ROOT / "universe" / "upbit_tradeable_universe.py",
)
UNIVERSE = importlib.util.module_from_spec(_UNIVERSE_SPEC)
_UNIVERSE_SPEC.loader.exec_module(UNIVERSE)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReleaseProjectionError(ValueError):
    """Fail-closed deterministic-projection violation."""


TRANSITION_SCHEMA_VERSION = "upbit_universe_same_vintage_transition/1"
TRANSITION_SOURCE_ROOT = "data/observations/upbit_tradeable_universe"


def formatted_json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseProjectionError(f"{label}_READ_FAILED:{exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseProjectionError(f"{label}_ROOT_INVALID")
    return value


def _transition_repo_path(relative_path: str, *, repo_root: Path, label: str) -> Path:
    """Return the lexical repo path after rejecting every symlink component."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ReleaseProjectionError(f"{label}_PATH_INVALID")
    relative = Path(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ReleaseProjectionError(f"{label}_PATH_INVALID")
    root = Path(repo_root).absolute()
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ReleaseProjectionError(f"{label}_PATH_OUTSIDE_REPOSITORY") from exc
    current = root
    if current.is_symlink():
        raise ReleaseProjectionError(f"{label}_SYMLINK_FORBIDDEN")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseProjectionError(f"{label}_SYMLINK_FORBIDDEN")
    return candidate


def _self_hash(value: dict, label: str) -> str:
    declared = value.get("payload_sha256")
    actual = payload_sha256({key: item for key, item in value.items() if key != "payload_sha256"})
    if not isinstance(declared, str) or declared != actual:
        raise ReleaseProjectionError(f"{label}_PAYLOAD_SHA256_MISMATCH")
    return actual


def _record_authority_closed(record: dict, *, allow_observation_pool_marker: bool = False) -> bool:
    return UNIVERSE._transition_authority_closed(
        record,
        allow_observation_pool_marker=allow_observation_pool_marker,
    )


def build_same_vintage_transition_projection(
    *,
    repo_root: Path,
    source_record_relative_path: str,
    successor_record_relative_path: str,
    successor_record: dict,
    evaluation_as_of: str,
) -> dict:
    """Build one append-only P3 successor plus its immutable manifest.

    This function lives in the exact-code-approved ``release_builder``
    file.  It accepts only the canonical same-date source record, preserves
    every raw/pre-ratification field, re-runs both live exact-release chains,
    and emits a manifest that pins this builder and the exact-code-approved
    universe consumer by path and live file hash.  It grants no authority.
    """
    repo_root = Path(repo_root)
    expected_source = f"{TRANSITION_SOURCE_ROOT}/{evaluation_as_of}/packet.json"
    if source_record_relative_path != expected_source:
        raise ReleaseProjectionError("TRANSITION_SOURCE_NOT_CANONICAL_SAME_VINTAGE")
    expected_prefix = f"{TRANSITION_SOURCE_ROOT}/{evaluation_as_of}/transitions/"
    if (
        not isinstance(successor_record_relative_path, str)
        or not successor_record_relative_path.startswith(expected_prefix)
        or not successor_record_relative_path.endswith("/packet.json")
    ):
        raise ReleaseProjectionError("TRANSITION_SUCCESSOR_PATH_INVALID")
    source_path = _transition_repo_path(
        source_record_relative_path,
        repo_root=repo_root,
        label="TRANSITION_SOURCE_RECORD",
    )
    successor_path = _transition_repo_path(
        successor_record_relative_path,
        repo_root=repo_root,
        label="TRANSITION_SUCCESSOR_RECORD",
    )
    if not source_path.is_file():
        raise ReleaseProjectionError("TRANSITION_SOURCE_RECORD_INVALID")

    try:
        source_bytes = source_path.read_bytes()
        source_record = json.loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseProjectionError(f"TRANSITION_SOURCE_RECORD_READ_FAILED:{exc}") from exc
    if not isinstance(source_record, dict):
        raise ReleaseProjectionError("TRANSITION_SOURCE_RECORD_ROOT_INVALID")
    source_payload_hash = _self_hash(source_record, "TRANSITION_SOURCE_RECORD")
    successor_record = copy.deepcopy(successor_record)
    successor_payload_hash = _self_hash(successor_record, "TRANSITION_SUCCESSOR_RECORD")
    expected_directory = f"{source_payload_hash}-to-{successor_payload_hash}"
    if successor_path.parent.name != expected_directory:
        raise ReleaseProjectionError("TRANSITION_CONTENT_ADDRESSED_PATH_MISMATCH")

    immutable_keys = (
        "schema_version", "snapshot_date", "generated_at",
        "raw_snapshot", "builder", "identity_review",
    )
    if not all(source_record.get(key) == successor_record.get(key) for key in immutable_keys):
        raise ReleaseProjectionError("TRANSITION_SAME_RAW_VINTAGE_MISMATCH")
    expected_raw = f"evidence/crypto/upbit/raw/{evaluation_as_of}"
    if (
        source_record.get("snapshot_date") != evaluation_as_of
        or successor_record.get("snapshot_date") != evaluation_as_of
        or (source_record.get("raw_snapshot") or {}).get("path") != expected_raw
        or (successor_record.get("raw_snapshot") or {}).get("path") != expected_raw
    ):
        raise ReleaseProjectionError("TRANSITION_RAW_PATH_MISMATCH")

    source_packet = source_record.get("packet") or {}
    successor_packet = successor_record.get("packet") or {}
    source_summary = source_packet.get("summary") or {}
    successor_summary = successor_packet.get("summary") or {}
    source_rows = source_packet.get("markets") or []
    successor_rows = successor_packet.get("markets") or []
    if (
        source_packet.get("policy_ratified") is not True
        or source_packet.get("taxonomy_ratified") is not False
        or (source_record.get("ratification") or {}).get("effective_for_snapshot") is not False
        or source_summary.get("tradeable_universe_count") != 0
        or source_summary.get("paper_eligible_count") != 0
        or source_summary.get("market_count") != len(source_rows)
        or (
            source_summary.get("observation_pool_count", 0)
            + source_summary.get("blocked_count", 0)
        ) != len(source_rows)
        or any(
            not isinstance(row, dict)
            or row.get("state") not in {"OBSERVATION_POOL", "BLOCKED"}
            for row in source_rows
        )
    ):
        raise ReleaseProjectionError("TRANSITION_SOURCE_STATE_NOT_POLICY_TRUE_TAXONOMY_FALSE")
    if (
        successor_packet.get("policy_ratified") is not True
        or successor_packet.get("taxonomy_ratified") is not True
        or (successor_record.get("ratification") or {}).get("effective_for_snapshot") is not True
        or successor_summary.get("market_count") != len(successor_rows)
        or successor_summary.get("tradeable_universe_count") != 0
        or successor_summary.get("paper_eligible_count") != 8
        or (
            successor_summary.get("observation_pool_count", 0)
            + successor_summary.get("blocked_count", 0)
            + successor_summary.get("paper_eligible_count", 0)
        ) != len(successor_rows)
        or any(
            not isinstance(row, dict)
            or row.get("state") not in {"OBSERVATION_POOL", "BLOCKED", "PAPER_ELIGIBLE"}
            for row in successor_rows
        )
    ):
        raise ReleaseProjectionError("TRANSITION_SUCCESSOR_NOT_EXACT_EIGHT_EFFECTIVE")
    if not _record_authority_closed(source_record, allow_observation_pool_marker=True):
        raise ReleaseProjectionError("TRANSITION_SOURCE_AUTHORITY_NOT_CLOSED")
    if not _record_authority_closed(successor_record):
        raise ReleaseProjectionError("TRANSITION_SUCCESSOR_AUTHORITY_NOT_CLOSED")

    registry = _read_json(repo_root / "config" / "upbit_asset_identity_registry.json", "TRANSITION_REGISTRY")
    taxonomy = _read_json(repo_root / "config" / "upbit_exclusion_taxonomy.json", "TRANSITION_TAXONOMY")
    freeze = _read_json(
        repo_root / "config" / "upbit_identity_taxonomy_governance_freeze.json",
        "TRANSITION_FREEZE",
    )
    if not GOVERNANCE.validate_exact_release(
        registry, content_field="mappings", evaluation_as_of=evaluation_as_of, repo_root=repo_root,
    ):
        raise ReleaseProjectionError("TRANSITION_REGISTRY_EXACT_RELEASE_FAILED")
    if not GOVERNANCE.validate_exact_release(
        taxonomy, content_field="records", evaluation_as_of=evaluation_as_of, repo_root=repo_root,
    ):
        raise ReleaseProjectionError("TRANSITION_TAXONOMY_EXACT_RELEASE_FAILED")

    released = freeze.get("released_paper_markets")
    paper_markets = sorted(
        row.get("market")
        for row in successor_packet.get("markets") or []
        if isinstance(row, dict) and row.get("state") == "PAPER_ELIGIBLE"
    )
    approval = freeze.get("approval_resolution") or {}
    source_pin = registry.get("source_candidate_packet") or {}
    exact_content_preserved = (
        isinstance(released, list)
        and len(released) == 8
        and len(set(released)) == 8
        and sorted(registry.get("mappings") or {}) == sorted(released)
        and paper_markets == sorted(released)
        and registry.get("approval_evidence_ref") == approval.get("approval_evidence_ref")
        and taxonomy.get("approval_evidence_ref") == approval.get("approval_evidence_ref")
        and registry.get("approval_evidence_sha256") == approval.get("approval_evidence_sha256")
        and taxonomy.get("approval_evidence_sha256") == approval.get("approval_evidence_sha256")
        and registry.get("approved_candidate_payload_sha256")
        == approval.get("registry_candidate_payload_sha256")
        and taxonomy.get("approved_candidate_payload_sha256")
        == approval.get("taxonomy_candidate_payload_sha256")
        and source_pin.get("path") == approval.get("candidate_packet_path")
        and source_pin.get("file_sha256") == approval.get("candidate_packet_file_sha256")
        and source_pin.get("payload_sha256") == approval.get("candidate_packet_payload_sha256")
    )
    if not exact_content_preserved:
        raise ReleaseProjectionError("TRANSITION_BASE_CONTENT_APPROVAL_DRIFT")

    try:
        deterministic_successor = UNIVERSE.rebuild_same_vintage_population_record(
            source_record,
            evaluation_as_of=evaluation_as_of,
            repo_root=repo_root,
        )
    except UNIVERSE.UpbitUniverseError as exc:
        raise ReleaseProjectionError(f"TRANSITION_DETERMINISTIC_REBUILD_FAILED:{exc}") from exc
    if successor_record != deterministic_successor:
        raise ReleaseProjectionError("TRANSITION_SUCCESSOR_NOT_DETERMINISTIC_REBUILD")

    code_resolution = freeze.get("code_approval_resolution")
    if not isinstance(code_resolution, dict):
        raise ReleaseProjectionError("TRANSITION_CODE_APPROVAL_RESOLUTION_MISSING")
    contract = GOVERNANCE.load_policy_contract(repo_root / "config" / "upbit_exact_release_binding_policy_contract.json")
    authority = copy.deepcopy(contract["authority"])
    if not authority or any(item is not False for item in authority.values()):
        raise ReleaseProjectionError("TRANSITION_AUTHORITY_CONTRACT_INVALID")

    builder_relative = str(Path(__file__).resolve().relative_to(ROOT))
    consumer_relative = str(GOVERNANCE.CONSUMER_PATH.relative_to(GOVERNANCE.ROOT))
    successor_bytes = formatted_json_bytes(successor_record)
    manifest = {
        "schema_version": TRANSITION_SCHEMA_VERSION,
        "snapshot_date": evaluation_as_of,
        "evaluation_as_of": evaluation_as_of,
        "transition_available_at": code_resolution.get("ratified_at_utc"),
        "source_record": {
            "path": source_record_relative_path,
            "file_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "payload_sha256": source_payload_hash,
        },
        "successor_record": {
            "path": successor_record_relative_path,
            "file_sha256": hashlib.sha256(successor_bytes).hexdigest(),
            "payload_sha256": successor_payload_hash,
        },
        "builder": {
            "path": builder_relative,
            "file_sha256": file_sha256(repo_root / builder_relative),
        },
        "consumer": {
            "path": consumer_relative,
            "file_sha256": file_sha256(repo_root / consumer_relative),
        },
        "base_content_approval": copy.deepcopy(approval),
        "exact_release_resolution": copy.deepcopy(code_resolution),
        "authority": authority,
    }
    manifest["payload_sha256"] = payload_sha256(manifest)
    return {
        "manifest": manifest,
        "successor_record": successor_record,
        "successor_bytes": successor_bytes,
    }


def _verified_content_source_pin(
    document: dict, *, label: str, content_field: str,
    evaluation_as_of: str, contract: dict, repo_root: Path,
) -> dict:
    ok, _candidate, pin = GOVERNANCE.verify_content_chain(
        document,
        content_field=content_field,
        evaluation_as_of=evaluation_as_of,
        contract=contract,
        repo_root=repo_root,
    )
    if not ok or pin is None:
        raise ReleaseProjectionError(f"{label}_CONTENT_APPROVAL_CHAIN_INVALID")
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
    field is preserved value-for-value (deep-copied, never mutated in
    place); only the code-approval pointer fields are added.

    Raises ``ReleaseProjectionError`` if the approval path is absolute,
    outside the repository, missing, or if
    ``governance/upbit_exact_release_binding.py::verify_content_chain()``
    and ``verify_code_chain()`` -- the SAME full validations the runtime
    validator performs -- do not accept it.  The registry's optional
    redundant source pin is cross-checked when present; the taxonomy's
    base pin is resolved from its content approval because the real
    committed taxonomy has no ``source_candidate_packet`` field.
    """
    try:
        approval_path = GOVERNANCE._resolve_repo_path(code_approval_relative_path, repo_root)
    except GOVERNANCE.ExactReleaseBindingError as exc:
        raise ReleaseProjectionError(f"CODE_APPROVAL_PATH_INVALID:{exc}") from exc
    if not approval_path.is_file():
        raise ReleaseProjectionError("CODE_APPROVAL_FILE_MISSING")
    code_approval_file_sha256 = file_sha256(approval_path)

    contract = GOVERNANCE.load_policy_contract(
        GOVERNANCE._resolve_repo_path(
            str(GOVERNANCE.POLICY_CONTRACT_PATH.relative_to(GOVERNANCE.ROOT)), repo_root,
        )
    )
    registry_source_pin = _verified_content_source_pin(
        current_registry, label="REGISTRY", content_field="mappings",
        evaluation_as_of=evaluation_as_of, contract=contract, repo_root=repo_root,
    )
    taxonomy_source_pin = _verified_content_source_pin(
        current_taxonomy, label="TAXONOMY", content_field="records",
        evaluation_as_of=evaluation_as_of, contract=contract, repo_root=repo_root,
    )
    if registry_source_pin != taxonomy_source_pin:
        raise ReleaseProjectionError("REGISTRY_AND_TAXONOMY_BASE_CANDIDATE_PIN_MISMATCH")

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
