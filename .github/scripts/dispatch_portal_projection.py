#!/usr/bin/env python3
"""Dispatch one committed Portal Projection v2 envelope to atlas-portal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_FIELDS = {
    "schema_version", "briefing_date", "slot", "validated_at_kst",
    "completion_state", "projection_id", "source_commit", "generation_id",
    "source_refs", "verified_facts", "display_proposal", "unknown_blocked",
    "safety_attestation",
}


class DispatchError(RuntimeError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_path(value: str) -> str:
    if not value or value.startswith("/") or "\\" in value or ".." in Path(value).parts:
        raise DispatchError("ENVELOPE_PATH_INVALID")
    return value


def _git_show(repo_root: Path, commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=repo_root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise DispatchError("COMMITTED_ENVELOPE_UNAVAILABLE")
    return result.stdout


def prepare_payload(
    repo_root: Path, envelope_commit: str, envelope_path: str,
    expected_sha256: str,
) -> dict:
    if FULL_SHA.fullmatch(envelope_commit) is None:
        raise DispatchError("ENVELOPE_COMMIT_INVALID")
    if SHA256.fullmatch(expected_sha256) is None:
        raise DispatchError("ENVELOPE_SHA256_INVALID")
    path = _safe_path(envelope_path)
    body = _git_show(repo_root, envelope_commit, path)
    actual_sha = hashlib.sha256(body).hexdigest()
    if actual_sha != expected_sha256:
        raise DispatchError("COMMITTED_ENVELOPE_HASH_MISMATCH")
    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DispatchError("COMMITTED_ENVELOPE_INVALID_JSON") from None
    if not isinstance(envelope, dict) or set(envelope) != REQUIRED_FIELDS:
        raise DispatchError("COMMITTED_ENVELOPE_FIELDS_MISMATCH")
    if envelope.get("schema_version") != "portal_projection/2":
        raise DispatchError("COMMITTED_ENVELOPE_SCHEMA_INVALID")
    source_commit = envelope.get("source_commit")
    if not isinstance(source_commit, str) or FULL_SHA.fullmatch(source_commit) is None:
        raise DispatchError("SOURCE_COMMIT_INVALID")
    if envelope.get("completion_state") != "VALIDATED":
        raise DispatchError("BRIEFING_NOT_VALIDATED")
    return {
        "event_type": "portal_projection_validated_v2",
        "client_payload": {
            "envelope_commit": envelope_commit,
            "source_commit": source_commit,
            "envelope_path": path,
            "envelope_sha256": actual_sha,
            "projection_id": envelope.get("projection_id"),
        },
    }


def dispatch(payload: dict, repository: str, token: str, api_root: str) -> None:
    if not token.strip():
        raise DispatchError("ATLAS_PORTAL_DISPATCH_TOKEN_REQUIRED")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise DispatchError("TARGET_REPOSITORY_INVALID")
    request = urllib.request.Request(
        f"{api_root.rstrip('/')}/repos/{repository}/dispatches",
        data=_canonical(payload).encode("utf-8"), method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "atlas-validated-briefing-projection/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 204:
                raise DispatchError(f"PORTAL_DISPATCH_UNEXPECTED_STATUS:{response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise DispatchError(f"PORTAL_DISPATCH_HTTP_{exc.code}:{detail}") from None
    except OSError as exc:
        raise DispatchError(f"PORTAL_DISPATCH_TRANSPORT:{type(exc).__name__}") from None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--repo-root", default=".")
    result.add_argument("--envelope-commit", required=True)
    result.add_argument("--envelope-path", required=True)
    result.add_argument("--envelope-sha256", required=True)
    result.add_argument("--repository", default="yonggeun1021-hub/atlas-portal")
    result.add_argument("--api-root", default="https://api.github.com")
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        payload = prepare_payload(
            Path(args.repo_root).resolve(), args.envelope_commit,
            args.envelope_path, args.envelope_sha256,
        )
        if args.dry_run:
            print(_canonical(payload))
            return 0
        dispatch(
            payload, args.repository,
            os.environ.get("ATLAS_PORTAL_DISPATCH_TOKEN", ""), args.api_root,
        )
    except DispatchError as exc:
        print(f"BLOCKED:{exc}")
        return 2
    print(_canonical({
        "result": "DISPATCHED",
        "projection_id": payload["client_payload"]["projection_id"],
        "envelope_commit": payload["client_payload"]["envelope_commit"],
        "envelope_sha256": payload["client_payload"]["envelope_sha256"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
