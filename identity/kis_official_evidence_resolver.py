#!/usr/bin/env python3
"""Read-only exact-byte resolver for KIS proposal evidence.

The proposal packet names a public repository, an exact commit and content
SHA-256 values. This module independently reads those git objects from a
separately obtained checkout and reproduces every byte hash. It never uses
the worktree copy of a file, follows no branch/tag, performs no network
request, and writes no authority/configuration.

The checkout path is operator supplied; the repository, commit and manifest
used by ``reproduce_kis_official_evidence`` are not. A successful resolution
only means that the cited bytes were reproduced. It grants no provider,
identity, investability, action, order, Production or trading authority.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Mapping

from identity.kis_provenance_proposal import (
    KIS_PINNED_EVIDENCE_MANIFEST,
    _KIS_OPEN_TRADING_API_PINNED_COMMIT,
    _KIS_OPEN_TRADING_API_REPO,
    payload_sha256,
)

SCHEMA_VERSION = "external_git_evidence_resolution/1"
MAX_EVIDENCE_FILE_BYTES = 1_000_000
AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "provider_authorized": False,
    "identity_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class KisOfficialEvidenceResolutionError(ValueError):
    pass


def _git(checkout: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        raise KisOfficialEvidenceResolutionError(
            f"GIT_COMMAND_FAILED:{args[0] if args else 'UNKNOWN'}"
        ) from None
    return result.stdout


def _validate_manifest(manifest: Mapping[str, str]) -> None:
    if not isinstance(manifest, Mapping) or not manifest:
        raise KisOfficialEvidenceResolutionError("EVIDENCE_MANIFEST_EMPTY_OR_INVALID")
    for path_text, expected_hash in manifest.items():
        if not isinstance(path_text, str):
            raise KisOfficialEvidenceResolutionError("EVIDENCE_PATH_INVALID")
        path = PurePosixPath(path_text)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise KisOfficialEvidenceResolutionError(f"EVIDENCE_PATH_INVALID:{path_text}")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_hash)
        ):
            raise KisOfficialEvidenceResolutionError(
                f"EVIDENCE_EXPECTED_HASH_INVALID:{path_text}"
            )


def _resolve_git_evidence(
    checkout: Path,
    *,
    repo: str,
    commit_sha: str,
    manifest: Mapping[str, str],
) -> dict:
    """Generic exact-git-object resolver used by the fixed KIS wrapper.

    Parameters other than ``checkout`` exist so the resolver itself can be
    regression-tested with a disposable local git repository. Production
    review calls only ``reproduce_kis_official_evidence``, which fixes them
    to the KIS constants.
    """
    checkout = Path(checkout)
    if not checkout.is_absolute():
        raise KisOfficialEvidenceResolutionError("EVIDENCE_CHECKOUT_MUST_BE_ABSOLUTE")
    if checkout.is_symlink() or not checkout.is_dir():
        raise KisOfficialEvidenceResolutionError("EVIDENCE_CHECKOUT_INVALID")
    if (
        not isinstance(commit_sha, str)
        or len(commit_sha) != 40
        or any(char not in "0123456789abcdef" for char in commit_sha)
    ):
        raise KisOfficialEvidenceResolutionError("EVIDENCE_COMMIT_SHA_INVALID")
    _validate_manifest(manifest)

    checkout = checkout.resolve()
    repo_root = Path(
        _git(checkout, "rev-parse", "--show-toplevel").decode("utf-8").strip()
    ).resolve()
    if repo_root != checkout:
        raise KisOfficialEvidenceResolutionError("EVIDENCE_CHECKOUT_NOT_REPOSITORY_ROOT")
    head = _git(checkout, "rev-parse", "HEAD").decode("ascii").strip()
    if head != commit_sha:
        raise KisOfficialEvidenceResolutionError("EVIDENCE_CHECKOUT_HEAD_MISMATCH")
    _git(checkout, "cat-file", "-e", f"{commit_sha}^{{commit}}")

    files = []
    for path_text, expected_hash in sorted(manifest.items()):
        try:
            content = _git(checkout, "show", f"{commit_sha}:{path_text}")
        except KisOfficialEvidenceResolutionError:
            raise KisOfficialEvidenceResolutionError(
                f"EVIDENCE_GIT_OBJECT_READ_FAILED:{path_text}"
            ) from None
        if len(content) > MAX_EVIDENCE_FILE_BYTES:
            raise KisOfficialEvidenceResolutionError(
                f"EVIDENCE_FILE_TOO_LARGE:{path_text}"
            )
        observed_hash = hashlib.sha256(content).hexdigest()
        if observed_hash != expected_hash:
            raise KisOfficialEvidenceResolutionError(
                f"EVIDENCE_CONTENT_HASH_MISMATCH:{path_text}"
            )
        files.append({
            "filePath": path_text,
            "contentSha256": observed_hash,
            "byteLength": len(content),
        })

    resolution = {
        "schemaVersion": SCHEMA_VERSION,
        "resolutionStatus": "EXACT_GIT_BYTES_REPRODUCED",
        "repo": repo,
        "commitSha": commit_sha,
        "files": files,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    resolution["resolutionSha256"] = payload_sha256(resolution)
    return resolution


def reproduce_kis_official_evidence(checkout: Path) -> dict:
    return _resolve_git_evidence(
        checkout,
        repo=_KIS_OPEN_TRADING_API_REPO,
        commit_sha=_KIS_OPEN_TRADING_API_PINNED_COMMIT,
        manifest=KIS_PINNED_EVIDENCE_MANIFEST,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args(argv)
    try:
        result = reproduce_kis_official_evidence(args.checkout)
    except KisOfficialEvidenceResolutionError as error:
        print(json.dumps({
            "resolutionStatus": "FAILED",
            "errorCode": str(error),
            "authority": AUTHORITY_ALL_FALSE,
        }, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
