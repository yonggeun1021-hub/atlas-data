#!/usr/bin/env python3
"""Dispatch a data-only, main-ancestry-verified Portal Projection v2.

The executable is always loaded from the repository default branch. The
caller-selected ``envelope_commit`` is never checked out and none of its code
is executed; its envelope, bundle, index, and evidence are read as bytes via
the GitHub Contents API and independently rebuilt before dispatch.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_ed25519 as ed25519  # noqa: E402


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ENVELOPE_PATH = re.compile(
    r"^evidence/validated_briefing_portal/"
    r"(morning|evening)/(\d{4}-\d{2}-\d{2})/rev-(\d{3})/"
    r"portal-projection\.json$"
)
SLOT = {"morning": "AM", "evening": "PM"}
SAFETY = {
    "read_only": True,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
    "broker_credentials_present": False,
}
ENVELOPE_FIELDS = {
    "schema_version", "briefing_date", "slot", "validated_at_kst",
    "completion_state", "projection_id", "source_commit", "generation_id",
    "source_refs", "verified_facts", "display_proposal", "unknown_blocked",
    "safety_attestation",
}
REF_FIELDS = {"path", "sha256", "generation_id"}
CLAIM_FIELDS = {"claim_id", "kind", "statement", "status", "source_ref_paths"}
CLAIM_LEDGER_FIELDS = {
    "schema_version", "state", "briefing_id", "briefing_date", "slot",
    "generation_id", "source_commit", "source_refs", "claims",
    "safety_attestation",
}
REPORT_FIELDS = {
    "schema_version", "briefing_id", "briefing_date", "slot",
    "generation_id", "source_commit", "validated_at_kst",
    "completion_state", "verdict", "briefing_sha256",
    "claim_ledger_sha256", "display_proposal_sha256",
    "unknown_escalation", "corrections", "post_delivery",
    "safety_attestation",
}
DISPLAY_FIELDS = {"schema_version", "briefing_id", "changes"}
BUNDLE_FIELDS = {
    "schema_version", "briefing_id", "briefing_date", "slot", "revision",
    "projection_id", "source_commit", "generation_id", "classification",
    "artifacts", "post_delivery_change_key", "redelivery", "authority",
}
INDEX_FIELDS = {
    "schema_version", "latest_revision", "latest_projection_id", "revisions",
}
INDEX_ENTRY_FIELDS = {
    "revision", "projection_id", "envelope_path", "envelope_sha256",
    "source_commit", "generation_id",
}
ARTIFACT_FIELDS = {"path", "sha256", "bytes"}
ARTIFACT_NAMES = {
    "briefing.md", "claim-ledger.json", "validation-report.json",
    "display-proposal.json", "portal-projection.json",
}
DISPLAY_ALLOWLIST = {
    "generated/atlas-public-snapshot.json",
    "generated/validated-briefing-recovery.json",
    "public/portal-projection-status.json",
}
POST_DELIVERY_FIELDS = {
    "post_delivery_change_key", "signed_ruling_path",
    "signed_ruling_sha256", "redelivery",
}
RULING_FIELDS = {
    "contract_version", "post_delivery_change_key", "capital_impact",
    "resolved_by", "action_taken", "signature",
}
FINALIZATION_CONTRACTS = {"briefing_finalization/17", "briefing_finalization/18"}


class DispatchError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any) -> str:
    return digest_bytes(canonical(value))


def _exact(value: dict, keys: set[str], code: str) -> None:
    if set(value) != keys:
        raise DispatchError(code)


def _json_bytes(body: bytes, code: str) -> dict:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DispatchError(f"{code}:INVALID_JSON") from None
    if not isinstance(value, dict):
        raise DispatchError(f"{code}:NOT_OBJECT")
    if body != canonical(value) + b"\n":
        raise DispatchError(f"{code}:NOT_CANONICAL")
    return value


def _safe_repo_path(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise DispatchError(code)
    if any(part in {".", ".."} for part in Path(value).parts):
        raise DispatchError(code)
    return value


def _contains_forbidden_authority(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            current = f"{path}.{key}"
            if re.search(r"broker.*credential|api[_-]?key|secret|token|private[_-]?pin", lowered):
                if child not in (False, None):
                    raise DispatchError(f"BROKER_OR_SECRET_MATERIAL_BLOCKED:{current}")
            if re.search(
                r"(stage|buy|action|order|production|trading).*authority|order_write",
                lowered,
            ) and child is not False:
                raise DispatchError(f"AUTHORITY_ESCALATION_BLOCKED:{current}")
            _contains_forbidden_authority(child, current)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _contains_forbidden_authority(child, f"{path}[{index}]")


def _declared_generations(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return set()
    found: set[str] = set()
    if isinstance(value.get("generation_id"), str):
        found.add(value["generation_id"])
    generation = value.get("generation")
    if isinstance(generation, dict) and isinstance(generation.get("generation_id"), str):
        found.add(generation["generation_id"])
    packet = value.get("packet")
    nested = packet.get("generation") if isinstance(packet, dict) else None
    if isinstance(nested, dict) and isinstance(nested.get("generation_id"), str):
        found.add(nested["generation_id"])
    return found


def _change_resolution_message(
    briefing_id: str,
    change_key: str,
    capital_impact: str,
    resolved_by: str,
    action_taken: str,
    contract_version: str,
) -> bytes:
    return canonical({
        "contract_version": contract_version,
        "purpose": "atlas.briefing_finalization.post_delivery_resolution",
        "briefing_id": briefing_id,
        "post_delivery_change_key": change_key,
        "capital_impact": capital_impact,
        "resolved_by": resolved_by,
        "action_taken": action_taken,
    })


def _trusted_public_key(public_key_bytes: bytes, expected_fingerprint: str) -> bytes:
    if not expected_fingerprint or SHA256.fullmatch(expected_fingerprint.lower()) is None:
        raise DispatchError("FINALIZATION_APPROVAL_ANCHOR_MISSING")
    if not public_key_bytes:
        raise DispatchError("FINALIZATION_APPROVAL_PUBKEY_MISSING")
    try:
        public_key = bytes.fromhex(public_key_bytes.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        raise DispatchError("FINALIZATION_APPROVAL_PUBKEY_MALFORMED") from None
    if len(public_key) != 32:
        raise DispatchError("FINALIZATION_APPROVAL_PUBKEY_MALFORMED")
    if digest_bytes(public_key) != expected_fingerprint.lower():
        raise DispatchError("FINALIZATION_APPROVAL_PUBKEY_UNTRUSTED")
    return public_key


def _verify_post_delivery_ruling(
    report: dict,
    source_refs: list[dict],
    source_bodies: dict[str, bytes],
    briefing_id: str,
    public_key_bytes: bytes,
    approval_fingerprint: str,
) -> str | None:
    post_delivery = report.get("post_delivery")
    if post_delivery is None:
        return None
    if report.get("verdict") != "PASS_WITH_CORRECTION" or not report.get("corrections"):
        raise DispatchError("POST_DELIVERY_REQUIRES_CORRECTION_VERDICT")
    if not isinstance(post_delivery, dict):
        raise DispatchError("POST_DELIVERY_INVALID")
    _exact(post_delivery, POST_DELIVERY_FIELDS, "POST_DELIVERY_FIELDS_MISMATCH")
    change_key = post_delivery.get("post_delivery_change_key")
    if not isinstance(change_key, str) or SHA256.fullmatch(change_key) is None:
        raise DispatchError("POST_DELIVERY_CHANGE_KEY_INVALID")
    if post_delivery.get("redelivery") != "FORBIDDEN":
        raise DispatchError("POST_DELIVERY_REDELIVERY_FORBIDDEN")
    ruling_path = _safe_repo_path(
        post_delivery.get("signed_ruling_path"), "SIGNED_RULING_PATH_INVALID"
    )
    ruling_sha = post_delivery.get("signed_ruling_sha256")
    if not isinstance(ruling_sha, str) or SHA256.fullmatch(ruling_sha) is None:
        raise DispatchError("SIGNED_RULING_HASH_INVALID")
    matching_refs = [ref for ref in source_refs if ref.get("path") == ruling_path]
    if len(matching_refs) != 1:
        raise DispatchError("SIGNED_RULING_NOT_A_SOURCE_REF")
    if matching_refs[0].get("sha256") != ruling_sha:
        raise DispatchError("SIGNED_RULING_SOURCE_REF_HASH_MISMATCH")
    ruling_bytes = source_bodies.get(ruling_path)
    if ruling_bytes is None:
        raise DispatchError("SIGNED_RULING_NOT_A_SOURCE_REF")
    if digest_bytes(ruling_bytes) != ruling_sha:
        raise DispatchError("SIGNED_RULING_HASH_MISMATCH")
    try:
        ruling = json.loads(ruling_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DispatchError("SIGNED_RULING_INVALID_JSON") from None
    if not isinstance(ruling, dict):
        raise DispatchError("SIGNED_RULING_INVALID")
    _exact(ruling, RULING_FIELDS, "SIGNED_RULING_FIELDS_MISMATCH")
    if ruling.get("contract_version") not in FINALIZATION_CONTRACTS:
        raise DispatchError("SIGNED_RULING_CONTRACT_INVALID")
    if ruling.get("post_delivery_change_key") != change_key:
        raise DispatchError("SIGNED_RULING_CHANGE_MISMATCH")
    capital_impact = ruling.get("capital_impact")
    resolved_by = ruling.get("resolved_by")
    action_taken = ruling.get("action_taken")
    if (
        capital_impact not in {"NONE", "PRESENT"}
        or not isinstance(resolved_by, str)
        or not resolved_by.strip()
        or not isinstance(action_taken, str)
    ):
        raise DispatchError("SIGNED_RULING_CONTENT_INVALID")
    if capital_impact == "PRESENT" and not action_taken.strip():
        raise DispatchError("SIGNED_RULING_ACTION_MISSING")
    signature_text = ruling.get("signature")
    if not isinstance(signature_text, str):
        raise DispatchError("SIGNED_RULING_SIGNATURE_INVALID")
    try:
        signature = bytes.fromhex(signature_text)
    except ValueError:
        raise DispatchError("SIGNED_RULING_SIGNATURE_INVALID") from None
    public_key = _trusted_public_key(public_key_bytes, approval_fingerprint)
    message = _change_resolution_message(
        briefing_id,
        change_key,
        capital_impact,
        resolved_by,
        action_taken,
        ruling["contract_version"],
    )
    if not ed25519.verify(signature, message, public_key):
        raise DispatchError("SIGNED_RULING_SIGNATURE_INVALID")
    return change_key


class GitHubDataClient:
    def __init__(self, token: str, repository: str, api_root: str = "https://api.github.com"):
        if not token.strip():
            raise DispatchError("GITHUB_TOKEN_REQUIRED")
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
            raise DispatchError("SOURCE_REPOSITORY_INVALID")
        self.token = token
        self.repository = repository
        self.api_root = api_root.rstrip("/")

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(
            f"{self.api_root}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "atlas-portal-dispatch-verifier/2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise DispatchError(f"GITHUB_DATA_HTTP_{exc.code}:{detail}") from None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DispatchError(f"GITHUB_DATA_UNAVAILABLE:{type(exc).__name__}") from None
        if not isinstance(value, dict):
            raise DispatchError("GITHUB_DATA_RESPONSE_INVALID")
        return value

    def require_main_ancestor(self, commit: str, default_branch: str) -> None:
        self.require_ancestor(commit, default_branch)

    def require_ancestor(self, ancestor: str, descendant: str) -> None:
        if FULL_SHA.fullmatch(ancestor) is None:
            raise DispatchError("COMMIT_INVALID")
        if FULL_SHA.fullmatch(descendant) is None and re.fullmatch(r"[A-Za-z0-9._/-]+", descendant) is None:
            raise DispatchError("COMMIT_OR_BRANCH_INVALID")
        head = urllib.parse.quote(descendant, safe="")
        comparison = self._get(
            f"/repos/{self.repository}/compare/{ancestor}...{head}"
        )
        if comparison.get("status") not in {"ahead", "identical"}:
            raise DispatchError(f"COMMIT_LINEAGE_REJECTED:{ancestor}:{descendant}")
        base = comparison.get("base_commit") or {}
        if base.get("sha") != ancestor:
            raise DispatchError(f"COMMIT_LINEAGE_REJECTED:{ancestor}:{descendant}")

    def get_bytes(self, commit: str, path: str) -> bytes:
        safe_path = urllib.parse.quote(_safe_repo_path(path, "CONTENT_PATH_INVALID"), safe="/")
        ref = urllib.parse.quote(commit, safe="")
        response = self._get(
            f"/repos/{self.repository}/contents/{safe_path}?ref={ref}"
        )
        if response.get("type") != "file":
            raise DispatchError(f"CONTENT_NOT_FILE:{path}")
        # The Contents API omits inline base64 for files larger than 1 MiB.
        # Resolve those bytes through the immutable Git blob named by the
        # Contents response; never follow a mutable download URL.
        if response.get("encoding") == "none" and not response.get("content"):
            blob_sha = response.get("sha")
            if not isinstance(blob_sha, str) or FULL_SHA.fullmatch(blob_sha) is None:
                raise DispatchError(f"CONTENT_BLOB_SHA_INVALID:{path}")
            blob = self._get(
                f"/repos/{self.repository}/git/blobs/{blob_sha}"
            )
            if blob.get("sha") != blob_sha or blob.get("encoding") != "base64":
                raise DispatchError(f"CONTENT_BLOB_INVALID:{path}")
            response = {**blob, "size": response.get("size")}
        elif response.get("encoding") != "base64":
            raise DispatchError(f"CONTENT_ENCODING_INVALID:{path}")
        try:
            encoded = "".join(str(response.get("content", "")).split())
            body = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise DispatchError(f"CONTENT_BASE64_INVALID:{path}") from None
        if response.get("size") != len(body):
            raise DispatchError(f"CONTENT_SIZE_MISMATCH:{path}")
        return body


def _validate_envelope(envelope: dict, path_match: re.Match[str]) -> None:
    _exact(envelope, ENVELOPE_FIELDS, "ENVELOPE_FIELDS_MISMATCH")
    if envelope.get("schema_version") != "portal_projection/2":
        raise DispatchError("ENVELOPE_SCHEMA_INVALID")
    if envelope.get("completion_state") != "VALIDATED":
        raise DispatchError("BRIEFING_NOT_VALIDATED")
    slot_dir, path_date, _ = path_match.groups()
    if envelope.get("briefing_date") != path_date or envelope.get("slot") != SLOT[slot_dir]:
        raise DispatchError("ENVELOPE_PATH_IDENTITY_MISMATCH")
    if DATE.fullmatch(path_date) is None:
        raise DispatchError("BRIEFING_DATE_INVALID")
    source_commit = envelope.get("source_commit")
    generation = envelope.get("generation_id")
    if not isinstance(source_commit, str) or FULL_SHA.fullmatch(source_commit) is None:
        raise DispatchError("SOURCE_COMMIT_INVALID")
    if not isinstance(generation, str) or SHA256.fullmatch(generation) is None:
        raise DispatchError("GENERATION_ID_INVALID")
    validated_at = str(envelope.get("validated_at_kst", ""))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00", validated_at) is None:
        raise DispatchError("VALIDATED_AT_KST_INVALID")
    try:
        parsed_at = dt.datetime.fromisoformat(validated_at)
    except ValueError:
        raise DispatchError("VALIDATED_AT_KST_INVALID") from None
    if parsed_at.strftime("%Y-%m-%d") != path_date:
        raise DispatchError("VALIDATED_DATE_MISMATCH")
    if not isinstance(envelope.get("projection_id"), str) or not envelope["projection_id"].startswith(
        f"{path_date}-{SLOT[slot_dir]}-"
    ):
        raise DispatchError("PROJECTION_IDENTITY_INVALID")
    if envelope.get("safety_attestation") != SAFETY:
        raise DispatchError("SAFETY_ATTESTATION_FAILED")
    refs = envelope.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise DispatchError("SOURCE_REFS_MISSING")
    seen: set[str] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            raise DispatchError("SOURCE_REF_INVALID")
        _exact(ref, REF_FIELDS, "SOURCE_REF_FIELDS_MISMATCH")
        ref_path = _safe_repo_path(ref.get("path"), "SOURCE_REF_PATH_INVALID")
        if ref_path in seen:
            raise DispatchError("SOURCE_REF_DUPLICATE")
        seen.add(ref_path)
        if ref.get("generation_id") != generation:
            raise DispatchError("MIXED_GENERATION")
        if not isinstance(ref.get("sha256"), str) or SHA256.fullmatch(ref["sha256"]) is None:
            raise DispatchError("SOURCE_REF_HASH_INVALID")
    for key in ("verified_facts", "display_proposal", "unknown_blocked"):
        if not isinstance(envelope.get(key), list):
            raise DispatchError("ENVELOPE_COLLECTION_INVALID")
    for change in envelope["display_proposal"]:
        if not isinstance(change, dict) or set(change) != {"path", "content"}:
            raise DispatchError("DISPLAY_CHANGE_FIELDS_MISMATCH")
        if change.get("path") not in DISPLAY_ALLOWLIST:
            raise DispatchError("NON_ALLOWLIST_DIFF_BLOCKED")
    _contains_forbidden_authority(envelope)


def _validate_claims(ledger: dict, source_paths: set[str]) -> tuple[list[dict], list[dict]]:
    _exact(ledger, CLAIM_LEDGER_FIELDS, "CLAIM_LEDGER_FIELDS_MISMATCH")
    if ledger.get("schema_version") != "claim_ledger/1" or ledger.get("state") != "READY_FOR_CHATGPT_VALIDATION":
        raise DispatchError("CLAIM_LEDGER_STATE_INVALID")
    claims = ledger.get("claims")
    if not isinstance(claims, list) or not claims:
        raise DispatchError("CLAIMS_MISSING")
    allowed = {"FACT": "VERIFIED", "INFERENCE": "INFERRED", "UNKNOWN": "UNKNOWN"}
    seen: set[str] = set()
    verified: list[dict] = []
    unknown: list[dict] = []
    for claim in claims:
        if not isinstance(claim, dict):
            raise DispatchError("CLAIM_INVALID")
        _exact(claim, CLAIM_FIELDS, "CLAIM_FIELDS_MISMATCH")
        claim_id = claim.get("claim_id")
        kind = claim.get("kind")
        paths = claim.get("source_ref_paths")
        if not isinstance(claim_id, str) or claim_id in seen:
            raise DispatchError("CLAIM_ID_INVALID_OR_DUPLICATE")
        seen.add(claim_id)
        if kind not in allowed or claim.get("status") != allowed[kind]:
            raise DispatchError("CLAIM_CLASSIFICATION_INVALID")
        if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
            raise DispatchError("CLAIM_STATEMENT_INVALID")
        if (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)
                or len(paths) != len(set(paths)) or not set(paths).issubset(source_paths)):
            raise DispatchError("CLAIM_SOURCE_REFS_INVALID")
        if kind == "FACT":
            if not paths:
                raise DispatchError("VERIFIED_FACT_SOURCE_MISSING")
            verified.append({"claim_id": claim_id, "statement": claim["statement"],
                             "source_ref_paths": paths})
        elif kind == "UNKNOWN":
            unknown.append({"claim_id": claim_id, "statement": claim["statement"],
                            "source_ref_paths": paths, "escalation": "UNKNOWN"})
    return verified, unknown


def validate_dispatch_candidate(
    client: Any,
    envelope_commit: str,
    envelope_path: str,
    expected_sha256: str,
    default_branch: str,
    approval_public_key_bytes: bytes = b"",
    approval_fingerprint: str = "",
) -> dict:
    if FULL_SHA.fullmatch(envelope_commit) is None:
        raise DispatchError("ENVELOPE_COMMIT_INVALID")
    if SHA256.fullmatch(expected_sha256) is None:
        raise DispatchError("ENVELOPE_SHA256_INVALID")
    path_match = ENVELOPE_PATH.fullmatch(envelope_path)
    if path_match is None:
        raise DispatchError("ENVELOPE_PATH_PREFIX_REJECTED")
    if not default_branch or re.fullmatch(r"[A-Za-z0-9._/-]+", default_branch) is None:
        raise DispatchError("DEFAULT_BRANCH_INVALID")
    client.require_main_ancestor(envelope_commit, default_branch)

    envelope_bytes = client.get_bytes(envelope_commit, envelope_path)
    if digest_bytes(envelope_bytes) != expected_sha256:
        raise DispatchError("COMMITTED_ENVELOPE_HASH_MISMATCH")
    envelope = _json_bytes(envelope_bytes, "COMMITTED_ENVELOPE")
    _validate_envelope(envelope, path_match)
    source_commit = envelope["source_commit"]
    client.require_main_ancestor(source_commit, default_branch)
    client.require_ancestor(source_commit, envelope_commit)
    source_bodies: dict[str, bytes] = {}
    for ref in envelope["source_refs"]:
        source_body = client.get_bytes(source_commit, ref["path"])
        source_bodies[ref["path"]] = source_body
        if digest_bytes(source_body) != ref["sha256"]:
            raise DispatchError(f"SOURCE_HASH_MISMATCH:{ref['path']}")
        try:
            source_json = json.loads(source_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            source_json = None
        declared = _declared_generations(source_json)
        if declared and declared != {envelope["generation_id"]}:
            raise DispatchError(f"MIXED_GENERATION:{ref['path']}")

    slot_dir, date, revision_text = path_match.groups()
    revision = int(revision_text)
    revision_root = envelope_path.rsplit("/", 1)[0]
    date_root = revision_root.rsplit("/", 1)[0]
    artifact_bodies = {
        name: client.get_bytes(envelope_commit, f"{revision_root}/{name}")
        for name in ARTIFACT_NAMES
    }
    if artifact_bodies["portal-projection.json"] != envelope_bytes:
        raise DispatchError("ENVELOPE_ARTIFACT_BYTES_MISMATCH")
    bundle_bytes = client.get_bytes(envelope_commit, f"{revision_root}/bundle.json")
    index_bytes = client.get_bytes(envelope_commit, f"{date_root}/index.json")
    bundle = _json_bytes(bundle_bytes, "BUNDLE")
    index = _json_bytes(index_bytes, "INDEX")

    _exact(bundle, BUNDLE_FIELDS, "BUNDLE_FIELDS_MISMATCH")
    if bundle.get("schema_version") != "validated_briefing_portal_bundle/1":
        raise DispatchError("BUNDLE_SCHEMA_INVALID")
    if bundle.get("classification") != "APPLY_CANDIDATE" or bundle.get("redelivery") != "FORBIDDEN":
        raise DispatchError("BUNDLE_CLASSIFICATION_INVALID")
    if bundle.get("authority") != SAFETY:
        raise DispatchError("BUNDLE_AUTHORITY_INVALID")
    for key in ("briefing_date", "slot", "projection_id", "source_commit", "generation_id"):
        if bundle.get(key) != envelope.get(key):
            raise DispatchError(f"BUNDLE_IDENTITY_MISMATCH:{key}")
    if bundle.get("revision") != revision:
        raise DispatchError("BUNDLE_REVISION_MISMATCH")
    expected_briefing_id = f"{date}-{SLOT[slot_dir].lower()}"
    if bundle.get("briefing_id") != expected_briefing_id:
        raise DispatchError("BUNDLE_BRIEFING_ID_MISMATCH")
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list):
        raise DispatchError("BUNDLE_ARTIFACTS_INVALID")
    artifact_map: dict[str, dict] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise DispatchError("BUNDLE_ARTIFACT_INVALID")
        _exact(artifact, ARTIFACT_FIELDS, "BUNDLE_ARTIFACT_FIELDS_MISMATCH")
        name = artifact.get("path")
        if name in artifact_map:
            raise DispatchError("BUNDLE_ARTIFACT_DUPLICATE")
        artifact_map[name] = artifact
    if set(artifact_map) != ARTIFACT_NAMES:
        raise DispatchError("BUNDLE_ARTIFACT_SET_MISMATCH")
    for name, body in artifact_bodies.items():
        record = artifact_map[name]
        if record.get("sha256") != digest_bytes(body) or record.get("bytes") != len(body):
            raise DispatchError(f"BUNDLE_ARTIFACT_HASH_MISMATCH:{name}")

    ledger = _json_bytes(artifact_bodies["claim-ledger.json"], "CLAIM_LEDGER")
    report = _json_bytes(artifact_bodies["validation-report.json"], "VALIDATION_REPORT")
    display = _json_bytes(artifact_bodies["display-proposal.json"], "DISPLAY_PROPOSAL")
    _exact(report, REPORT_FIELDS, "VALIDATION_REPORT_FIELDS_MISMATCH")
    _exact(display, DISPLAY_FIELDS, "DISPLAY_PROPOSAL_FIELDS_MISMATCH")
    if report.get("schema_version") != "briefing_validation_report/1":
        raise DispatchError("VALIDATION_REPORT_SCHEMA_INVALID")
    if display.get("schema_version") != "portal_display_proposal/1":
        raise DispatchError("DISPLAY_PROPOSAL_SCHEMA_INVALID")
    for value in (ledger, report):
        for key in ("briefing_date", "slot", "source_commit", "generation_id"):
            if value.get(key) != envelope.get(key):
                raise DispatchError(f"VALIDATED_ARTIFACT_IDENTITY_MISMATCH:{key}")
        if value.get("briefing_id") != expected_briefing_id:
            raise DispatchError("VALIDATED_ARTIFACT_BRIEFING_ID_MISMATCH")
        if value.get("safety_attestation") != SAFETY:
            raise DispatchError("VALIDATED_ARTIFACT_SAFETY_INVALID")
    if ledger.get("source_refs") != envelope.get("source_refs"):
        raise DispatchError("CLAIM_LEDGER_SOURCE_REFS_MISMATCH")
    if display.get("briefing_id") != expected_briefing_id:
        raise DispatchError("DISPLAY_PROPOSAL_BRIEFING_ID_MISMATCH")
    if report.get("completion_state") != "VALIDATED" or report.get("validated_at_kst") != envelope.get("validated_at_kst"):
        raise DispatchError("VALIDATION_REPORT_STATE_MISMATCH")
    corrections = report.get("corrections")
    if not isinstance(corrections, list):
        raise DispatchError("VALIDATION_CORRECTIONS_INVALID")
    if (report.get("verdict") == "PASS") != (not corrections):
        raise DispatchError("VALIDATION_VERDICT_MISMATCH")
    if corrections and report.get("verdict") != "PASS_WITH_CORRECTION":
        raise DispatchError("VALIDATION_VERDICT_MISMATCH")
    if report.get("briefing_sha256") != digest_bytes(artifact_bodies["briefing.md"]):
        raise DispatchError("BRIEFING_HASH_MISMATCH")
    if report.get("claim_ledger_sha256") != digest_bytes(artifact_bodies["claim-ledger.json"]):
        raise DispatchError("CLAIM_LEDGER_HASH_MISMATCH")
    if report.get("display_proposal_sha256") != digest_bytes(artifact_bodies["display-proposal.json"]):
        raise DispatchError("DISPLAY_PROPOSAL_HASH_MISMATCH")
    verified, unknown = _validate_claims(
        ledger, {ref["path"] for ref in envelope["source_refs"]}
    )
    if verified != envelope.get("verified_facts") or unknown != envelope.get("unknown_blocked"):
        raise DispatchError("CLAIM_PROJECTION_MISMATCH")
    expected_escalation = "ESCALATE" if unknown else "NONE"
    if report.get("unknown_escalation") != expected_escalation:
        raise DispatchError("UNKNOWN_ESCALATION_MISMATCH")
    if not isinstance(display.get("changes"), list) or display["changes"] != envelope.get("display_proposal"):
        raise DispatchError("DISPLAY_PROJECTION_MISMATCH")
    expected_change_key = _verify_post_delivery_ruling(
        report,
        envelope["source_refs"],
        source_bodies,
        expected_briefing_id,
        approval_public_key_bytes,
        approval_fingerprint,
    )
    if bundle.get("post_delivery_change_key") != expected_change_key:
        raise DispatchError("BUNDLE_CHANGE_KEY_MISMATCH")
    _contains_forbidden_authority({"ledger": ledger, "report": report, "display": display})

    core = {key: envelope[key] for key in ENVELOPE_FIELDS if key != "projection_id"}
    suffix = digest({
        **core,
        "briefing_id": expected_briefing_id,
        "claim_ledger_sha256": digest(ledger),
        "validation_report_sha256": digest(report),
    })[:24]
    if envelope["projection_id"] != f"{date}-{SLOT[slot_dir]}-{suffix}":
        raise DispatchError("PROJECTION_ID_REBUILD_MISMATCH")

    _exact(index, INDEX_FIELDS, "INDEX_FIELDS_MISMATCH")
    if index.get("schema_version") != "validated_briefing_portal_index/1":
        raise DispatchError("INDEX_SCHEMA_INVALID")
    if index.get("latest_revision") != revision or index.get("latest_projection_id") != envelope["projection_id"]:
        raise DispatchError("INDEX_LATEST_IDENTITY_MISMATCH")
    matches = []
    for entry in index.get("revisions", []):
        if not isinstance(entry, dict):
            raise DispatchError("INDEX_ENTRY_INVALID")
        _exact(entry, INDEX_ENTRY_FIELDS, "INDEX_ENTRY_FIELDS_MISMATCH")
        if entry.get("revision") == revision:
            matches.append(entry)
    expected_entry = {
        "revision": revision,
        "projection_id": envelope["projection_id"],
        "envelope_path": envelope_path,
        "envelope_sha256": expected_sha256,
        "source_commit": source_commit,
        "generation_id": envelope["generation_id"],
    }
    if matches != [expected_entry]:
        raise DispatchError("INDEX_LINEAGE_MISMATCH")

    return {
        "event_type": "portal_projection_validated_v2",
        "client_payload": {
            "envelope_commit": envelope_commit,
            "source_commit": source_commit,
            "envelope_path": envelope_path,
            "envelope_sha256": expected_sha256,
            "projection_id": envelope["projection_id"],
        },
    }


def dispatch(payload: dict, repository: str, token: str, api_root: str) -> None:
    if not token.strip():
        raise DispatchError("ATLAS_PORTAL_DISPATCH_TOKEN_REQUIRED")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise DispatchError("TARGET_REPOSITORY_INVALID")
    request = urllib.request.Request(
        f"{api_root.rstrip('/')}/repos/{repository}/dispatches",
        data=canonical(payload), method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "atlas-validated-briefing-projection/2",
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
    result.add_argument("--envelope-commit", required=True)
    result.add_argument("--envelope-path", required=True)
    result.add_argument("--envelope-sha256", required=True)
    result.add_argument("--default-branch", required=True)
    result.add_argument("--source-repository", default="yonggeun1021-hub/atlas-data")
    result.add_argument("--target-repository", default="yonggeun1021-hub/atlas-portal")
    result.add_argument("--api-root", default="https://api.github.com")
    result.add_argument("--dry-run", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        client = GitHubDataClient(
            os.environ.get("GITHUB_TOKEN", ""), args.source_repository, args.api_root
        )
        try:
            approval_public_key = Path("config/atlas_approval_pubkey.txt").read_bytes()
        except OSError:
            approval_public_key = b""
        payload = validate_dispatch_candidate(
            client, args.envelope_commit, args.envelope_path,
            args.envelope_sha256, args.default_branch,
            approval_public_key,
            os.environ.get("ATLAS_APPROVAL_PUBKEY_FINGERPRINT", "").strip(),
        )
        if args.dry_run:
            print(canonical(payload).decode("utf-8"))
            return 0
        dispatch(
            payload, args.target_repository,
            os.environ.get("ATLAS_PORTAL_DISPATCH_TOKEN", ""), args.api_root,
        )
    except DispatchError as exc:
        print(f"BLOCKED:{exc}")
        return 2
    print(canonical({
        "result": "DISPATCHED",
        "projection_id": payload["client_payload"]["projection_id"],
        "envelope_commit": payload["client_payload"]["envelope_commit"],
        "envelope_sha256": payload["client_payload"]["envelope_sha256"],
    }).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
