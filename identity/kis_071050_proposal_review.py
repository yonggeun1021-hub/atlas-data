#!/usr/bin/env python3
"""Independent fail-closed review of the two 071050 proposal packets."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from identity.kis_071050_proposal import (
    ATLAS_IDENTITY_EVIDENCE_MANIFEST,
    ATLAS_REPOSITORY,
    ATLAS_SOURCE_COMMIT,
    AUTHORITY_ALL_FALSE,
    KIS_ALIAS_EVIDENCE_MANIFEST,
    KIS_IDENTITY_EVIDENCE_MANIFEST,
    KIS_PINNED_COMMIT,
    KIS_REPOSITORY,
    PROPOSAL_STATUS,
    SCHEMA_VERSION,
    instrument_identity_proposal_071050,
    payload_sha256,
    source_alias_proposal_071050,
)
from identity.kis_official_evidence_resolver import (
    KisOfficialEvidenceResolutionError,
    _resolve_git_evidence,
)


_FIELDS = {
    "schemaVersion", "proposalId", "proposalKind", "proposalStatus", "claim",
    "evidence", "canonicalAuthorityConfigMutated", "authority", "proposalSha256",
}
_FORBIDDEN_STATUSES = {"RATIFIED", "BROKER_VERIFIED"}


class Kis071050ProposalReviewError(ValueError):
    pass


def _walk_safety(value: object, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "canonicalAuthorityConfigMutated" and child is not False:
                raise Kis071050ProposalReviewError(f"CONFIG_MUTATION_FORBIDDEN:{child_path}")
            if key.endswith("_authorized") and child is not False:
                raise Kis071050ProposalReviewError(f"AUTHORITY_TRUE_FORBIDDEN:{child_path}")
            _walk_safety(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_safety(child, f"{path}[{index}]")
    elif isinstance(value, str) and value in _FORBIDDEN_STATUSES:
        raise Kis071050ProposalReviewError(f"FORBIDDEN_STATUS:{path}:{value}")


def _validate_exact(packet: object, expected: dict) -> dict:
    if not isinstance(packet, dict) or set(packet) != _FIELDS:
        raise Kis071050ProposalReviewError("PROPOSAL_FIELDS_INVALID")
    _walk_safety(packet)
    if packet.get("schemaVersion") != SCHEMA_VERSION:
        raise Kis071050ProposalReviewError("PROPOSAL_SCHEMA_INVALID")
    if packet.get("proposalStatus") != PROPOSAL_STATUS:
        raise Kis071050ProposalReviewError("PROPOSAL_STATUS_INVALID")
    if packet.get("canonicalAuthorityConfigMutated") is not False:
        raise Kis071050ProposalReviewError("CONFIG_MUTATION_FORBIDDEN")
    if packet.get("authority") != AUTHORITY_ALL_FALSE:
        raise Kis071050ProposalReviewError("AUTHORITY_NOT_ALL_FALSE")
    unsigned = {key: value for key, value in packet.items() if key != "proposalSha256"}
    if packet.get("proposalSha256") != payload_sha256(unsigned):
        raise Kis071050ProposalReviewError("PROPOSAL_HASH_MISMATCH")
    if packet != expected:
        raise Kis071050ProposalReviewError("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT")
    return dict(packet)


def validate_identity_proposal(packet: object) -> dict:
    return _validate_exact(packet, instrument_identity_proposal_071050())


def validate_alias_proposal(packet: object) -> dict:
    return _validate_exact(packet, source_alias_proposal_071050())


def _git_blob(checkout: Path, commit: str, path: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(checkout), "show", f"{commit}:{path}"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        raise Kis071050ProposalReviewError(f"EVIDENCE_GIT_OBJECT_READ_FAILED:{path}") from None


def _verify_required_fragments(checkout: Path, packet: dict) -> None:
    official = packet["evidence"][0]
    if official.get("kind") != "PINNED_OFFICIAL_KIS_STATIC_EVIDENCE":
        raise Kis071050ProposalReviewError("OFFICIAL_EVIDENCE_KIND_INVALID")
    for entry in official.get("files", []):
        path = entry["filePath"]
        text = _git_blob(checkout, KIS_PINNED_COMMIT, path).decode("utf-8")
        for fragment in entry.get("requiredFragments", []):
            if fragment not in text:
                raise Kis071050ProposalReviewError(
                    f"OFFICIAL_EVIDENCE_REQUIRED_FRAGMENT_MISSING:{path}"
                )


def _verify_atlas_identity_semantics(checkout: Path) -> None:
    corp_map = json.loads(_git_blob(checkout, ATLAS_SOURCE_COMMIT, "config/corp_map.json"))
    if corp_map.get("071050") != "00432102":
        raise Kis071050ProposalReviewError("ATLAS_DART_BINDING_MISMATCH")


def _review_result(packet: dict, reasons: list[str]) -> dict:
    reasons = sorted(set(reasons))
    return {
        "proposalId": packet["proposalId"],
        "proposalStatus": packet["proposalStatus"],
        "reviewStatus": "REVIEW_INCOMPLETE" if reasons else "REVIEW_READY_FOR_CIO",
        "reasons": reasons,
        "canonicalAuthorityConfigMutated": False,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }


def review_identity_proposal(
    packet: object,
    *,
    official_checkout: Path | None = None,
    atlas_checkout: Path | None = None,
) -> dict:
    packet = validate_identity_proposal(packet)
    reasons: list[str] = []
    if official_checkout is None:
        reasons.append("OFFICIAL_KIS_EXACT_BYTES_REPRODUCTION_REQUIRED")
    else:
        try:
            _resolve_git_evidence(
                Path(official_checkout), repo=KIS_REPOSITORY,
                commit_sha=KIS_PINNED_COMMIT, manifest=KIS_IDENTITY_EVIDENCE_MANIFEST,
            )
            _verify_required_fragments(Path(official_checkout), packet)
        except (KisOfficialEvidenceResolutionError, Kis071050ProposalReviewError) as error:
            reasons.append(f"OFFICIAL_KIS_EVIDENCE_FAILED:{error}")
    if atlas_checkout is None:
        reasons.append("ATLAS_EXACT_BYTES_REPRODUCTION_REQUIRED")
    else:
        try:
            _resolve_git_evidence(
                Path(atlas_checkout), repo=ATLAS_REPOSITORY,
                commit_sha=ATLAS_SOURCE_COMMIT, manifest=ATLAS_IDENTITY_EVIDENCE_MANIFEST,
            )
            _verify_atlas_identity_semantics(Path(atlas_checkout))
        except (KisOfficialEvidenceResolutionError, Kis071050ProposalReviewError) as error:
            reasons.append(f"ATLAS_IDENTITY_EVIDENCE_FAILED:{error}")
    return _review_result(packet, reasons)


def review_alias_proposal(
    packet: object,
    *,
    identity_packet: object,
    official_checkout: Path | None = None,
) -> dict:
    packet = validate_alias_proposal(packet)
    identity_packet = validate_identity_proposal(identity_packet)
    target = packet["evidence"][1]
    if target.get("proposalId") != identity_packet["proposalId"] or target.get(
        "proposalSha256"
    ) != identity_packet["proposalSha256"]:
        raise Kis071050ProposalReviewError("ALIAS_TARGET_IDENTITY_PROPOSAL_MISMATCH")
    failure = packet["evidence"][2]
    if failure != {
        "kind": "LIVE_PRODUCT_INFO_READ_RESULT",
        "evidenceDomain": "071050_EXACT_KIS_SOURCE_ALIAS",
        "status": "NOT_OBTAINED_FAIL_CLOSED",
        "brokerReadAttempted": True,
        "positiveEvidenceAccepted": False,
        "orderSubmissionAttempted": False,
    }:
        raise Kis071050ProposalReviewError("FAILED_LIVE_READ_CONTRACT_INVALID")
    reasons: list[str] = []
    if official_checkout is None:
        reasons.append("OFFICIAL_KIS_EXACT_BYTES_REPRODUCTION_REQUIRED")
    else:
        try:
            _resolve_git_evidence(
                Path(official_checkout), repo=KIS_REPOSITORY,
                commit_sha=KIS_PINNED_COMMIT, manifest=KIS_ALIAS_EVIDENCE_MANIFEST,
            )
            _verify_required_fragments(Path(official_checkout), packet)
        except (KisOfficialEvidenceResolutionError, Kis071050ProposalReviewError) as error:
            reasons.append(f"OFFICIAL_KIS_EVIDENCE_FAILED:{error}")
    return _review_result(packet, reasons)
