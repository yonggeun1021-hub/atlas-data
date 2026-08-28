#!/usr/bin/env python3
"""Independent fail-closed review of the two 071050 proposal packets."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import subprocess
import zipfile
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
_MASTER_TRAILING_FIELD_BYTES = 227
_PREFERRED_STOCK_FIELD_OFFSET = 158
_PREFERRED_STOCK_MEANINGS = {
    "0": "COMMON_STOCK",
    "1": "PREFERRED_STOCK_OLD",
    "2": "PREFERRED_STOCK_NEW",
}
_PUBLIC_MASTER_BINDING = {
    "archiveSha256": "8de794458d38e4304b0b1f69c9de0f2b4ab71ea5781585653d83b2d5c0d13be1",
    "masterMember": "kospi_code.mst",
    "masterSha256": "abfec9c79eca665741b6189fc88214961088067782791f9c90aa0715c510b4a2",
    "rowLineNumber": 1035,
    "rowSha256": "aa3dc58fe82e95d22013d2f312b8cab9e84b63833836513b1decfc1716416286",
}
_MAX_PUBLIC_MASTER_ARCHIVE_BYTES = 5_000_000
_MAX_PUBLIC_MASTER_BYTES = 20_000_000


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


def _parse_public_master_row(raw_row: bytes) -> dict:
    """Parse only the identity fields defined by the pinned KIS header/parser.

    The public observation carries the exact raw row, so review does not trust
    the separately serialized observation object to prove its own claims.
    """
    if len(raw_row) <= _MASTER_TRAILING_FIELD_BYTES:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_TOO_SHORT")
    part1 = raw_row[:-_MASTER_TRAILING_FIELD_BYTES]
    part2 = raw_row[-_MASTER_TRAILING_FIELD_BYTES:]
    try:
        short_code = part1[0:9].decode("cp949").strip()
        standard_number = part1[9:21].decode("ascii").strip()
        korean_name = part1[21:].decode("cp949").strip()
        security_group = part2[0:2].decode("ascii").strip()
        preferred_code = part2[
            _PREFERRED_STOCK_FIELD_OFFSET:_PREFERRED_STOCK_FIELD_OFFSET + 1
        ].decode("ascii").strip()
    except UnicodeDecodeError:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_ENCODING_INVALID") from None
    if preferred_code not in _PREFERRED_STOCK_MEANINGS:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_SHARE_CLASS_CODE_INVALID")
    return {
        "shortCode": short_code,
        "standardProductNumber": standard_number,
        "koreanName": korean_name,
        "securityGroupCode": security_group,
        "preferredStockClassCode": preferred_code,
        "officialMeaning": _PREFERRED_STOCK_MEANINGS[preferred_code],
    }


def _verify_public_master_exact_row(packet: dict) -> tuple[dict, bytes, dict]:
    matches = [
        evidence for evidence in packet["evidence"]
        if evidence.get("kind") == "PUBLIC_KIS_MASTER_EXACT_ROW_OBSERVATION"
    ]
    if len(matches) != 1:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_EVIDENCE_NOT_UNIQUE")
    evidence = matches[0]
    if any(evidence.get(field) != expected for field, expected in _PUBLIC_MASTER_BINDING.items()):
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_MASTER_ROW_BINDING_MISMATCH")
    raw_base64 = evidence.get("rawBase64")
    if not isinstance(raw_base64, str):
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_BASE64_INVALID")
    try:
        raw_row = base64.b64decode(raw_base64, validate=True)
    except (binascii.Error, ValueError):
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_BASE64_INVALID") from None
    if len(raw_row) != 288:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_LENGTH_INVALID")
    if hashlib.sha256(raw_row).hexdigest() != evidence.get("rowSha256"):
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_HASH_MISMATCH")
    parsed = _parse_public_master_row(raw_row)
    if parsed != evidence.get("observation"):
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_OBSERVATION_MISMATCH")
    if evidence.get("rowLineNumber") != 1035:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ROW_LINE_MISMATCH")
    claim = packet["claim"]
    expected_claim = {
        "shortCode": claim.get("subject"),
        "standardProductNumber": claim.get("standardProductNumber"),
        "koreanName": claim.get("koreanName"),
        "securityGroupCode": "ST",
        "preferredStockClassCode": "0",
        "officialMeaning": claim.get("instrumentType"),
    }
    if parsed != expected_claim:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_RAW_ROW_CLAIM_MISMATCH")
    return evidence, raw_row, parsed


def _verify_public_master_archive(packet: dict, archive: bytes) -> None:
    evidence, embedded_row, _ = _verify_public_master_exact_row(packet)
    if not isinstance(archive, bytes) or not archive or len(archive) > _MAX_PUBLIC_MASTER_ARCHIVE_BYTES:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_SIZE_INVALID")
    if hashlib.sha256(archive).hexdigest() != evidence["archiveSha256"]:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_HASH_MISMATCH")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            if (
                len(members) != 1
                or members[0].filename != evidence["masterMember"]
                or members[0].is_dir()
            ):
                raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_MEMBER_INVALID")
            if members[0].file_size > _MAX_PUBLIC_MASTER_BYTES:
                raise Kis071050ProposalReviewError("PUBLIC_MASTER_SIZE_INVALID")
            master = bundle.read(members[0])
    except Kis071050ProposalReviewError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_INVALID") from None
    if not master or len(master) > _MAX_PUBLIC_MASTER_BYTES:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_SIZE_INVALID")
    if hashlib.sha256(master).hexdigest() != evidence["masterSha256"]:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_HASH_MISMATCH")
    rows = master.splitlines()
    line_number = evidence["rowLineNumber"]
    if line_number > len(rows):
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ROW_LINE_NOT_PRESENT")
    selected_row = rows[line_number - 1]
    matches: list[bytes] = []
    for row in rows:
        try:
            parsed = _parse_public_master_row(row)
        except Kis071050ProposalReviewError:
            continue
        if parsed["shortCode"] == packet["claim"]["subject"]:
            matches.append(row)
    if len(matches) != 1:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_EXACT_SYMBOL_NOT_UNIQUE")
    if selected_row != matches[0] or selected_row != embedded_row:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_EMBEDDED_ROW_NOT_FROM_ARCHIVE")


def _operator_archive_bytes(
    *, archive: bytes | None, archive_path: Path | None,
) -> bytes | None:
    if archive is not None and archive_path is not None:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_INPUT_AMBIGUOUS")
    if archive_path is None:
        return archive
    path = Path(archive_path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_PATH_INVALID")
    if path.stat().st_size > _MAX_PUBLIC_MASTER_ARCHIVE_BYTES:
        raise Kis071050ProposalReviewError("PUBLIC_MASTER_ARCHIVE_SIZE_INVALID")
    return path.read_bytes()


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
    public_master_archive: bytes | None = None,
    public_master_archive_path: Path | None = None,
) -> dict:
    packet = validate_identity_proposal(packet)
    reasons: list[str] = []
    try:
        _verify_public_master_exact_row(packet)
    except Kis071050ProposalReviewError as error:
        reasons.append(f"PUBLIC_MASTER_EXACT_ROW_FAILED:{error}")
    try:
        archive = _operator_archive_bytes(
            archive=public_master_archive, archive_path=public_master_archive_path,
        )
        if archive is None:
            reasons.append("PUBLIC_MASTER_ARCHIVE_REPRODUCTION_REQUIRED")
        else:
            _verify_public_master_archive(packet, archive)
    except Kis071050ProposalReviewError as error:
        reasons.append(f"PUBLIC_MASTER_ARCHIVE_FAILED:{error}")
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
