#!/usr/bin/env python3
"""Build an immutable Portal Projection v2 from a validated briefing bundle.

This module is deliberately an intake boundary, not a validator of investment
truth.  A human/ChatGPT validation report must bind the exact briefing, claim
ledger, display proposal, source commit, and generation before an envelope can
be published.  UNKNOWN claims stay UNKNOWN and never enter ``verified_facts``.

The resulting envelope grants no Stage, Buy, Action, Order, Production, or
trading authority.  Post-delivery changes additionally require an existing
Ed25519-signed CIO ruling and remain non-redeliverable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


CLAIM_SCHEMA = "claim_ledger/1"
REPORT_SCHEMA = "briefing_validation_report/1"
DISPLAY_SCHEMA = "portal_display_proposal/1"
ENVELOPE_SCHEMA = "portal_projection/2"
BUNDLE_SCHEMA = "validated_briefing_portal_bundle/1"
INDEX_SCHEMA = "validated_briefing_portal_index/1"
READY_STATE = "READY_FOR_CHATGPT_VALIDATION"
VALIDATED_STATE = "VALIDATED"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BRIEFING_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)$")
PROJECTION_PATHS = {
    "generated/atlas-public-snapshot.json",
    "public/portal-projection-status.json",
}
SLOT_DIR = {"AM": "morning", "PM": "evening"}
SAFETY_ATTESTATION = {
    "read_only": True,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
    "broker_credentials_present": False,
}
CLAIM_FIELDS = {
    "claim_id", "kind", "statement", "status", "source_ref_paths",
}
REF_FIELDS = {"path", "sha256", "generation_id"}
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
POST_DELIVERY_FIELDS = {
    "post_delivery_change_key", "signed_ruling_path",
    "signed_ruling_sha256", "redelivery",
}
REVISION_ARTIFACT_NAMES = {
    "briefing.md", "claim-ledger.json", "validation-report.json",
    "display-proposal.json", "portal-projection.json",
}
REVISION_FILE_NAMES = REVISION_ARTIFACT_NAMES | {"bundle.json"}
BUNDLE_FIELDS = {
    "schema_version", "briefing_id", "briefing_date", "slot", "revision",
    "projection_id", "source_commit", "generation_id", "classification",
    "artifacts", "post_delivery_change_key", "redelivery", "authority",
}
ARTIFACT_FIELDS = {"path", "sha256", "bytes"}


class PortalProducerError(RuntimeError):
    """Fail-closed contract error."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any) -> str:
    return digest_bytes(canonical(value))


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PortalProducerError(f"{code}:{type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise PortalProducerError(f"{code}:NOT_OBJECT")
    return value


def _exact_keys(value: dict, expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise PortalProducerError(code)


def _safe_repo_path(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise PortalProducerError(code)
    parts = Path(value).parts
    if ".." in parts or "." in parts or "\\" in value:
        raise PortalProducerError(code)
    return value


def _contains_forbidden_authority(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{path}.{key}"
            lowered = str(key).lower()
            if re.search(r"broker.*credential|api[_-]?key|secret|token|private[_-]?pin", lowered):
                if child not in (False, None):
                    raise PortalProducerError(
                        f"BROKER_OR_SECRET_MATERIAL_BLOCKED:{current}"
                    )
            if re.search(
                r"(stage|buy|action|order|production|trading).*authority|order_write",
                lowered,
            ) and child is not False:
                raise PortalProducerError(f"AUTHORITY_ESCALATION_BLOCKED:{current}")
            _contains_forbidden_authority(child, current)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _contains_forbidden_authority(child, f"{path}[{index}]")


def _validate_attestation(value: Any) -> None:
    if (not isinstance(value, dict) or set(value) != set(SAFETY_ATTESTATION)
            or any(type(value[key]) is not bool for key in SAFETY_ATTESTATION)
            or value != SAFETY_ATTESTATION):
        raise PortalProducerError("SAFETY_ATTESTATION_FAILED")


def _git_bytes(repo_root: Path, commit: str, path: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise PortalProducerError(f"GIT_RETRIEVAL_UNAVAILABLE:{type(exc).__name__}") from None
    if result.returncode != 0:
        raise PortalProducerError(f"SOURCE_RETRIEVAL_UNAVAILABLE:{path}")
    return result.stdout


def _declared_generations(value: Any) -> set[str]:
    """Read only known top-level generation authorities, not nested components."""
    if not isinstance(value, dict):
        return set()
    found: set[str] = set()
    direct = value.get("generation_id")
    if isinstance(direct, str):
        found.add(direct)
    generation = value.get("generation")
    if isinstance(generation, dict) and isinstance(generation.get("generation_id"), str):
        found.add(generation["generation_id"])
    packet = value.get("packet")
    if isinstance(packet, dict):
        nested = packet.get("generation")
        if isinstance(nested, dict) and isinstance(nested.get("generation_id"), str):
            found.add(nested["generation_id"])
    return found


def _validate_refs(repo_root: Path, ledger: dict) -> dict[str, bytes]:
    refs = ledger.get("source_refs")
    generation_id = ledger.get("generation_id")
    commit = ledger.get("source_commit")
    if not isinstance(refs, list) or not refs:
        raise PortalProducerError("SOURCE_REFS_MISSING")
    if not isinstance(commit, str) or FULL_SHA.fullmatch(commit) is None:
        raise PortalProducerError("SOURCE_COMMIT_UNKNOWN")
    if not isinstance(generation_id, str) or SHA256.fullmatch(generation_id) is None:
        raise PortalProducerError("GENERATION_ID_UNKNOWN")
    resolved: dict[str, bytes] = {}
    for ref in refs:
        if not isinstance(ref, dict):
            raise PortalProducerError("SOURCE_REF_INVALID")
        _exact_keys(ref, REF_FIELDS, "SOURCE_REF_FIELDS_MISMATCH")
        path = _safe_repo_path(ref.get("path"), "SOURCE_REF_PATH_INVALID")
        if path in resolved:
            raise PortalProducerError("SOURCE_REF_DUPLICATE")
        if ref.get("generation_id") != generation_id:
            raise PortalProducerError("MIXED_GENERATION")
        expected_sha = ref.get("sha256")
        if not isinstance(expected_sha, str) or SHA256.fullmatch(expected_sha) is None:
            raise PortalProducerError("SOURCE_REF_HASH_INVALID")
        body = _git_bytes(repo_root, commit, path)
        if digest_bytes(body) != expected_sha:
            raise PortalProducerError(f"SOURCE_HASH_MISMATCH:{path}")
        try:
            parsed = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        declared = _declared_generations(parsed)
        if declared and declared != {generation_id}:
            raise PortalProducerError(f"MIXED_GENERATION:{path}")
        resolved[path] = body
    return resolved


def _validate_identity(value: dict, ledger: dict, code: str) -> None:
    for field in ("briefing_id", "briefing_date", "slot", "generation_id", "source_commit"):
        if value.get(field) != ledger.get(field):
            raise PortalProducerError(f"{code}:{field}")


def validate_claim_ledger(repo_root: Path, ledger: dict) -> dict[str, bytes]:
    _exact_keys(ledger, CLAIM_LEDGER_FIELDS, "CLAIM_LEDGER_FIELDS_MISMATCH")
    if ledger.get("schema_version") != CLAIM_SCHEMA:
        raise PortalProducerError("CLAIM_LEDGER_SCHEMA_INVALID")
    if ledger.get("state") != READY_STATE:
        raise PortalProducerError("CLAIM_LEDGER_NOT_READY")
    briefing_id = str(ledger.get("briefing_id", ""))
    match = BRIEFING_ID.fullmatch(briefing_id)
    slot = ledger.get("slot")
    date = ledger.get("briefing_date")
    if match is None or slot not in SLOT_DIR or date != match.group(1):
        raise PortalProducerError("CLAIM_LEDGER_IDENTITY_INVALID")
    if match.group(2) != slot.lower() or DATE.fullmatch(str(date)) is None:
        raise PortalProducerError("CLAIM_LEDGER_IDENTITY_INVALID")
    _validate_attestation(ledger.get("safety_attestation"))
    refs = _validate_refs(repo_root, ledger)
    ref_paths = set(refs)
    claims = ledger.get("claims")
    if not isinstance(claims, list) or not claims:
        raise PortalProducerError("CLAIMS_MISSING")
    seen: set[str] = set()
    allowed = {
        "FACT": "VERIFIED",
        "INFERENCE": "INFERRED",
        "UNKNOWN": "UNKNOWN",
    }
    for claim in claims:
        if not isinstance(claim, dict):
            raise PortalProducerError("CLAIM_INVALID")
        _exact_keys(claim, CLAIM_FIELDS, "CLAIM_FIELDS_MISMATCH")
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", claim_id):
            raise PortalProducerError("CLAIM_ID_INVALID")
        if claim_id in seen:
            raise PortalProducerError("CLAIM_ID_DUPLICATE")
        seen.add(claim_id)
        kind = claim.get("kind")
        if kind not in allowed or claim.get("status") != allowed[kind]:
            raise PortalProducerError("CLAIM_CLASSIFICATION_INVALID")
        if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
            raise PortalProducerError("CLAIM_STATEMENT_INVALID")
        paths = claim.get("source_ref_paths")
        if (not isinstance(paths, list) or any(not isinstance(path, str) for path in paths)
                or len(paths) != len(set(paths))):
            raise PortalProducerError("CLAIM_SOURCE_REFS_INVALID")
        if not set(paths).issubset(ref_paths):
            raise PortalProducerError("CLAIM_SOURCE_REF_UNBOUND")
        if kind == "FACT" and not paths:
            raise PortalProducerError("VERIFIED_FACT_SOURCE_MISSING")
    _contains_forbidden_authority(ledger)
    return refs


def validate_display_proposal(display: dict, ledger: dict) -> None:
    _exact_keys(display, DISPLAY_FIELDS, "DISPLAY_PROPOSAL_FIELDS_MISMATCH")
    if display.get("schema_version") != DISPLAY_SCHEMA:
        raise PortalProducerError("DISPLAY_PROPOSAL_SCHEMA_INVALID")
    if display.get("briefing_id") != ledger.get("briefing_id"):
        raise PortalProducerError("DISPLAY_PROPOSAL_IDENTITY_MISMATCH")
    changes = display.get("changes")
    if not isinstance(changes, list) or not changes:
        raise PortalProducerError("DISPLAY_PROPOSAL_EMPTY")
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "content"}:
            raise PortalProducerError("DISPLAY_CHANGE_FIELDS_MISMATCH")
        path = change.get("path")
        if path not in PROJECTION_PATHS:
            raise PortalProducerError(f"NON_ALLOWLIST_DIFF_BLOCKED:{path}")
        if path in seen:
            raise PortalProducerError("DISPLAY_CHANGE_PATH_DUPLICATE")
        seen.add(path)
        _contains_forbidden_authority(change.get("content"), f"display.{path}")


def _load_finalization_module(repo_root: Path):
    path = repo_root / ".github/scripts/briefing_finalization.py"
    spec = importlib.util.spec_from_file_location("atlas_briefing_finalization", path)
    if spec is None or spec.loader is None:
        raise PortalProducerError("FINALIZATION_MODULE_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_post_delivery_ruling(
    repo_root: Path, ledger: dict, post_delivery: dict, source_bytes: dict[str, bytes]
) -> None:
    _exact_keys(post_delivery, POST_DELIVERY_FIELDS, "POST_DELIVERY_FIELDS_MISMATCH")
    change_key = post_delivery.get("post_delivery_change_key")
    if not isinstance(change_key, str) or SHA256.fullmatch(change_key) is None:
        raise PortalProducerError("POST_DELIVERY_CHANGE_KEY_INVALID")
    if post_delivery.get("redelivery") != "FORBIDDEN":
        raise PortalProducerError("POST_DELIVERY_REDELIVERY_FORBIDDEN")
    path = _safe_repo_path(
        post_delivery.get("signed_ruling_path"), "SIGNED_RULING_PATH_INVALID"
    )
    ruling_bytes = source_bytes.get(path)
    if ruling_bytes is None:
        raise PortalProducerError("SIGNED_RULING_NOT_A_SOURCE_REF")
    expected_sha = post_delivery.get("signed_ruling_sha256")
    if expected_sha != digest_bytes(ruling_bytes):
        raise PortalProducerError("SIGNED_RULING_HASH_MISMATCH")
    try:
        ruling = json.loads(ruling_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PortalProducerError("SIGNED_RULING_INVALID_JSON") from None
    if not isinstance(ruling, dict):
        raise PortalProducerError("SIGNED_RULING_INVALID")
    if ruling.get("post_delivery_change_key") != change_key:
        raise PortalProducerError("SIGNED_RULING_CHANGE_MISMATCH")
    capital_impact = ruling.get("capital_impact")
    resolved_by = ruling.get("resolved_by")
    action_taken = ruling.get("action_taken", "")
    if capital_impact not in {"NONE", "PRESENT"} or not resolved_by:
        raise PortalProducerError("SIGNED_RULING_CONTENT_INVALID")
    if capital_impact == "PRESENT" and not str(action_taken).strip():
        raise PortalProducerError("SIGNED_RULING_ACTION_MISSING")
    bf = _load_finalization_module(repo_root)
    try:
        public_key = bf.load_public_key(repo_root)
        signature = bytes.fromhex(str(ruling.get("signature", "")))
        message = bf.change_resolution_message(
            ledger["briefing_id"],
            change_key,
            capital_impact,
            resolved_by,
            action_taken,
            ruling.get("contract_version", bf.CONTRACT_VERSION),
        )
    except (ValueError, getattr(bf, "FinalizationError", RuntimeError)) as exc:
        raise PortalProducerError(f"SIGNED_RULING_UNTRUSTED:{type(exc).__name__}") from None
    if not bf.ed25519.verify(signature, message, public_key):
        raise PortalProducerError("SIGNED_RULING_SIGNATURE_INVALID")


def validate_report(
    repo_root: Path,
    report: dict,
    ledger: dict,
    briefing_bytes: bytes,
    claim_ledger_bytes: bytes,
    display_bytes: bytes,
    source_bytes: dict[str, bytes],
) -> None:
    _exact_keys(report, REPORT_FIELDS, "VALIDATION_REPORT_FIELDS_MISMATCH")
    if report.get("schema_version") != REPORT_SCHEMA:
        raise PortalProducerError("VALIDATION_REPORT_SCHEMA_INVALID")
    _validate_identity(report, ledger, "VALIDATION_REPORT_IDENTITY_MISMATCH")
    if report.get("completion_state") != VALIDATED_STATE:
        raise PortalProducerError("BRIEFING_NOT_VALIDATED")
    verdict = report.get("verdict")
    corrections = report.get("corrections")
    if verdict not in {"PASS", "PASS_WITH_CORRECTION"} or not isinstance(corrections, list):
        raise PortalProducerError("VALIDATION_VERDICT_INVALID")
    if (verdict == "PASS") != (not corrections):
        raise PortalProducerError("VALIDATION_CORRECTION_MISMATCH")
    if report.get("briefing_sha256") != digest_bytes(briefing_bytes):
        raise PortalProducerError("BRIEFING_HASH_MISMATCH")
    if report.get("claim_ledger_sha256") != digest_bytes(claim_ledger_bytes):
        raise PortalProducerError("CLAIM_LEDGER_HASH_MISMATCH")
    if report.get("display_proposal_sha256") != digest_bytes(display_bytes):
        raise PortalProducerError("DISPLAY_PROPOSAL_HASH_MISMATCH")
    validated_at = str(report.get("validated_at_kst"))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00", validated_at) is None:
        raise PortalProducerError("VALIDATED_AT_KST_INVALID")
    try:
        validated = dt.datetime.fromisoformat(validated_at)
    except ValueError:
        raise PortalProducerError("VALIDATED_AT_KST_INVALID") from None
    if validated.utcoffset() != dt.timedelta(hours=9):
        raise PortalProducerError("VALIDATED_AT_KST_INVALID")
    if validated.strftime("%Y-%m-%d") != ledger.get("briefing_date"):
        raise PortalProducerError("VALIDATION_DATE_MISMATCH")
    unknown_exists = any(claim.get("kind") == "UNKNOWN" for claim in ledger["claims"])
    expected_escalation = "ESCALATE" if unknown_exists else "NONE"
    if report.get("unknown_escalation") != expected_escalation:
        raise PortalProducerError("UNKNOWN_ESCALATION_REQUIRED")
    _validate_attestation(report.get("safety_attestation"))
    _contains_forbidden_authority(corrections, "corrections")
    post_delivery = report.get("post_delivery")
    if post_delivery is None:
        return
    if verdict != "PASS_WITH_CORRECTION":
        raise PortalProducerError("POST_DELIVERY_REQUIRES_CORRECTION_VERDICT")
    if not isinstance(post_delivery, dict):
        raise PortalProducerError("POST_DELIVERY_INVALID")
    _verify_post_delivery_ruling(repo_root, ledger, post_delivery, source_bytes)


def build_envelope(ledger: dict, report: dict, display: dict) -> dict:
    verified = [
        {
            "claim_id": claim["claim_id"],
            "statement": claim["statement"],
            "source_ref_paths": claim["source_ref_paths"],
        }
        for claim in ledger["claims"]
        if claim["kind"] == "FACT"
    ]
    unknown = [
        {
            "claim_id": claim["claim_id"],
            "statement": claim["statement"],
            "source_ref_paths": claim["source_ref_paths"],
            "escalation": "UNKNOWN",
        }
        for claim in ledger["claims"]
        if claim["kind"] == "UNKNOWN"
    ]
    core = {
        "schema_version": ENVELOPE_SCHEMA,
        "briefing_date": ledger["briefing_date"],
        "slot": ledger["slot"],
        "validated_at_kst": report["validated_at_kst"],
        "completion_state": VALIDATED_STATE,
        "source_commit": ledger["source_commit"],
        "generation_id": ledger["generation_id"],
        "source_refs": ledger["source_refs"],
        "verified_facts": verified,
        "display_proposal": display["changes"],
        "unknown_blocked": unknown,
        "safety_attestation": SAFETY_ATTESTATION,
    }
    suffix = digest({
        **core,
        "briefing_id": ledger["briefing_id"],
        "claim_ledger_sha256": digest(ledger),
        "validation_report_sha256": digest(report),
    })[:24]
    return {
        "schema_version": core["schema_version"],
        "briefing_date": core["briefing_date"],
        "slot": core["slot"],
        "validated_at_kst": core["validated_at_kst"],
        "completion_state": core["completion_state"],
        "projection_id": f"{ledger['briefing_date']}-{ledger['slot']}-{suffix}",
        "source_commit": core["source_commit"],
        "generation_id": core["generation_id"],
        "source_refs": core["source_refs"],
        "verified_facts": core["verified_facts"],
        "display_proposal": core["display_proposal"],
        "unknown_blocked": core["unknown_blocked"],
        "safety_attestation": core["safety_attestation"],
    }


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _artifact(name: str, body: bytes) -> dict:
    return {"path": name, "sha256": digest_bytes(body), "bytes": len(body)}


def _revision_bodies(
    briefing_bytes: bytes,
    ledger: dict,
    report: dict,
    display: dict,
    envelope: dict,
    revision: int,
) -> dict[str, bytes]:
    bodies = {
        "briefing.md": briefing_bytes,
        "claim-ledger.json": canonical(ledger) + b"\n",
        "validation-report.json": canonical(report) + b"\n",
        "display-proposal.json": canonical(display) + b"\n",
        "portal-projection.json": canonical(envelope) + b"\n",
    }
    manifest = {
        "schema_version": BUNDLE_SCHEMA,
        "briefing_id": ledger["briefing_id"],
        "briefing_date": ledger["briefing_date"],
        "slot": ledger["slot"],
        "revision": revision,
        "projection_id": envelope["projection_id"],
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
        "classification": "APPLY_CANDIDATE",
        "artifacts": [_artifact(name, body) for name, body in bodies.items()],
        "post_delivery_change_key": (
            report["post_delivery"].get("post_delivery_change_key")
            if report.get("post_delivery") else None
        ),
        "redelivery": "FORBIDDEN",
        "authority": SAFETY_ATTESTATION,
    }
    bodies["bundle.json"] = canonical(manifest) + b"\n"
    return bodies


def _read_stored_revision(
    repo_root: Path, directory: Path, *, briefing_date: str, slot: str,
) -> dict:
    if (directory.is_symlink() or not directory.is_dir()
            or directory.resolve() != directory.absolute()):
        raise PortalProducerError("IMMUTABLE_REVISION_SYMLINK_BLOCKED")
    match = re.fullmatch(r"rev-(\d{3})", directory.name)
    if match is None:
        raise PortalProducerError("IMMUTABLE_REVISION_PATH_INVALID")
    revision = int(match.group(1))
    children = list(directory.iterdir())
    if {child.name for child in children} != REVISION_FILE_NAMES:
        raise PortalProducerError("IMMUTABLE_REVISION_INCOMPLETE")
    bodies: dict[str, bytes] = {}
    for child in children:
        if child.is_symlink() or not child.is_file():
            raise PortalProducerError("IMMUTABLE_REVISION_ARTIFACT_INVALID")
        try:
            bodies[child.name] = child.read_bytes()
        except OSError as exc:
            raise PortalProducerError(
                f"IMMUTABLE_REVISION_UNREADABLE:{type(exc).__name__}"
            ) from None

    bundle = _read_json(directory / "bundle.json", "IMMUTABLE_BUNDLE_UNREADABLE")
    envelope = _read_json(
        directory / "portal-projection.json", "IMMUTABLE_ENVELOPE_UNREADABLE"
    )
    ledger = _read_json(directory / "claim-ledger.json", "IMMUTABLE_LEDGER_UNREADABLE")
    report = _read_json(directory / "validation-report.json", "IMMUTABLE_REPORT_UNREADABLE")
    display = _read_json(directory / "display-proposal.json", "IMMUTABLE_DISPLAY_UNREADABLE")
    _exact_keys(bundle, BUNDLE_FIELDS, "IMMUTABLE_BUNDLE_FIELDS_MISMATCH")
    if (bundle.get("schema_version") != BUNDLE_SCHEMA
            or bundle.get("briefing_id") != f"{briefing_date}-{slot.lower()}"
            or bundle.get("briefing_date") != briefing_date
            or bundle.get("slot") != slot
            or isinstance(bundle.get("revision"), bool)
            or not isinstance(bundle.get("revision"), int)
            or bundle.get("revision") != revision
            or bundle.get("classification") != "APPLY_CANDIDATE"
            or bundle.get("redelivery") != "FORBIDDEN"):
        raise PortalProducerError("IMMUTABLE_BUNDLE_IDENTITY_INVALID")
    _validate_attestation(bundle.get("authority"))
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, list):
        raise PortalProducerError("IMMUTABLE_BUNDLE_ARTIFACTS_INVALID")
    artifact_map: dict[str, dict] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise PortalProducerError("IMMUTABLE_BUNDLE_ARTIFACT_INVALID")
        _exact_keys(artifact, ARTIFACT_FIELDS, "IMMUTABLE_BUNDLE_ARTIFACT_FIELDS_MISMATCH")
        name = artifact.get("path")
        if not isinstance(name, str) or name not in REVISION_ARTIFACT_NAMES:
            raise PortalProducerError("IMMUTABLE_BUNDLE_ARTIFACT_PATH_INVALID")
        if name in artifact_map:
            raise PortalProducerError("IMMUTABLE_BUNDLE_ARTIFACT_DUPLICATE")
        artifact_map[name] = artifact
    if set(artifact_map) != REVISION_ARTIFACT_NAMES:
        raise PortalProducerError("IMMUTABLE_BUNDLE_ARTIFACT_SET_MISMATCH")
    for name in REVISION_ARTIFACT_NAMES:
        record = artifact_map[name]
        body = bodies[name]
        recorded_bytes = record.get("bytes")
        if (isinstance(recorded_bytes, bool) or not isinstance(recorded_bytes, int)
                or record.get("sha256") != digest_bytes(body)
                or recorded_bytes != len(body)):
            raise PortalProducerError(f"IMMUTABLE_BUNDLE_ARTIFACT_HASH_MISMATCH:{name}")

    if canonical(ledger) + b"\n" != bodies["claim-ledger.json"]:
        raise PortalProducerError("IMMUTABLE_LEDGER_NOT_CANONICAL")
    if canonical(report) + b"\n" != bodies["validation-report.json"]:
        raise PortalProducerError("IMMUTABLE_REPORT_NOT_CANONICAL")
    if canonical(display) + b"\n" != bodies["display-proposal.json"]:
        raise PortalProducerError("IMMUTABLE_DISPLAY_NOT_CANONICAL")

    # A matching manifest only proves that the stored files agree with one
    # another.  Re-run the production validators so a complete, consistently
    # re-hashed bundle cannot turn invalid claims, mutable source refs, or a
    # forbidden authority value into accepted immutable history.
    refs = validate_claim_ledger(repo_root, ledger)
    validate_display_proposal(display, ledger)
    validate_report(
        repo_root,
        report,
        ledger,
        bodies["briefing.md"],
        bodies["claim-ledger.json"],
        bodies["display-proposal.json"],
        refs,
    )
    expected_change_key = (
        report["post_delivery"].get("post_delivery_change_key")
        if isinstance(report.get("post_delivery"), dict) else None
    )
    if bundle.get("post_delivery_change_key") != expected_change_key:
        raise PortalProducerError("IMMUTABLE_BUNDLE_CHANGE_KEY_MISMATCH")
    try:
        rebuilt_envelope = build_envelope(ledger, report, display)
    except (KeyError, TypeError) as exc:
        raise PortalProducerError(
            f"IMMUTABLE_ENVELOPE_REBUILD_INVALID:{type(exc).__name__}"
        ) from None
    if canonical(rebuilt_envelope) + b"\n" != bodies["portal-projection.json"]:
        raise PortalProducerError("IMMUTABLE_ENVELOPE_REBUILD_MISMATCH")

    projection_id = envelope.get("projection_id")
    source_commit = envelope.get("source_commit")
    generation_id = envelope.get("generation_id")
    if (envelope.get("schema_version") != ENVELOPE_SCHEMA
            or envelope.get("completion_state") != VALIDATED_STATE
            or not isinstance(projection_id, str) or not projection_id
            or bundle.get("projection_id") != projection_id
            or bundle.get("source_commit") != source_commit
            or bundle.get("generation_id") != generation_id
            or envelope.get("briefing_date") != briefing_date
            or envelope.get("slot") != slot):
        raise PortalProducerError("IMMUTABLE_ENVELOPE_BUNDLE_IDENTITY_MISMATCH")
    if not isinstance(source_commit, str) or FULL_SHA.fullmatch(source_commit) is None:
        raise PortalProducerError("IMMUTABLE_SOURCE_COMMIT_INVALID")
    if not isinstance(generation_id, str) or SHA256.fullmatch(generation_id) is None:
        raise PortalProducerError("IMMUTABLE_GENERATION_INVALID")
    _validate_attestation(envelope.get("safety_attestation"))
    envelope_path = (directory / "portal-projection.json").relative_to(repo_root).as_posix()
    return {
        "revision": revision,
        "projection_id": projection_id,
        "envelope_path": envelope_path,
        "envelope_sha256": digest_bytes(bodies["portal-projection.json"]),
        "source_commit": source_commit,
        "generation_id": generation_id,
    }


def _scan_stored_revisions(
    repo_root: Path, slot_root: Path, *, briefing_date: str, slot: str,
) -> list[dict]:
    if not slot_root.exists():
        return []
    directories = sorted(
        (path for path in slot_root.iterdir() if re.fullmatch(r"rev-\d{3}", path.name)),
        key=lambda path: path.name,
    )
    entries = [
        _read_stored_revision(
            repo_root, directory, briefing_date=briefing_date, slot=slot,
        )
        for directory in directories
    ]
    if [entry["revision"] for entry in entries] != list(range(1, len(entries) + 1)):
        raise PortalProducerError("IMMUTABLE_REVISION_SEQUENCE_INVALID")
    return entries


def _load_index(path: Path, repo_root: Path, slot_root: Path) -> dict:
    if path.is_symlink():
        raise PortalProducerError("PORTAL_INDEX_SYMLINK_BLOCKED")
    if not path.exists():
        return {"schema_version": INDEX_SCHEMA, "latest_revision": 0,
                "latest_projection_id": None, "revisions": []}
    value = _read_json(path, "PORTAL_INDEX_UNREADABLE")
    if set(value) != {"schema_version", "latest_revision", "latest_projection_id", "revisions"}:
        raise PortalProducerError("PORTAL_INDEX_FIELDS_MISMATCH")
    if value.get("schema_version") != INDEX_SCHEMA or not isinstance(value.get("revisions"), list):
        raise PortalProducerError("PORTAL_INDEX_INVALID")
    revisions = value["revisions"]
    latest_revision = value.get("latest_revision")
    latest_projection_id = value.get("latest_projection_id")
    if (not isinstance(latest_revision, int) or isinstance(latest_revision, bool)
            or latest_revision < 0 or latest_revision > 999):
        raise PortalProducerError("PORTAL_INDEX_LATEST_REVISION_INVALID")
    expected_root = slot_root.relative_to(repo_root).as_posix()
    seen_revisions: set[int] = set()
    seen_projection_ids: set[str] = set()
    previous_revision = 0
    for entry in revisions:
        if not isinstance(entry, dict) or set(entry) != {
            "revision", "projection_id", "envelope_path", "envelope_sha256",
            "source_commit", "generation_id",
        }:
            raise PortalProducerError("PORTAL_INDEX_ENTRY_INVALID")
        revision = entry.get("revision")
        if (not isinstance(revision, int) or isinstance(revision, bool)
                or revision < 1 or revision > 999 or revision <= previous_revision
                or revision in seen_revisions):
            raise PortalProducerError("PORTAL_INDEX_REVISION_INVALID")
        projection_id = entry.get("projection_id")
        if (not isinstance(projection_id, str) or not projection_id
                or projection_id in seen_projection_ids):
            raise PortalProducerError("PORTAL_INDEX_PROJECTION_ID_INVALID")
        expected_path = (
            f"{expected_root}/rev-{revision:03d}/portal-projection.json"
        )
        if entry.get("envelope_path") != expected_path:
            raise PortalProducerError("PORTAL_INDEX_ENVELOPE_PATH_INVALID")
        if (not isinstance(entry.get("envelope_sha256"), str)
                or SHA256.fullmatch(entry["envelope_sha256"]) is None):
            raise PortalProducerError("PORTAL_INDEX_ENVELOPE_HASH_INVALID")
        if (not isinstance(entry.get("source_commit"), str)
                or FULL_SHA.fullmatch(entry["source_commit"]) is None):
            raise PortalProducerError("PORTAL_INDEX_SOURCE_COMMIT_INVALID")
        if (not isinstance(entry.get("generation_id"), str)
                or SHA256.fullmatch(entry["generation_id"]) is None):
            raise PortalProducerError("PORTAL_INDEX_GENERATION_INVALID")
        seen_revisions.add(revision)
        seen_projection_ids.add(projection_id)
        previous_revision = revision
    expected_latest = revisions[-1]["revision"] if revisions else 0
    expected_projection = revisions[-1]["projection_id"] if revisions else None
    if latest_revision != expected_latest or latest_projection_id != expected_projection:
        raise PortalProducerError("PORTAL_INDEX_LATEST_IDENTITY_INVALID")
    return value


def publish_bundle(
    repo_root: Path,
    out_root: Path,
    briefing_bytes: bytes,
    ledger: dict,
    report: dict,
    display: dict,
    envelope: dict,
) -> dict:
    slot_root = out_root / SLOT_DIR[ledger["slot"]] / ledger["briefing_date"]
    resolved_slot_root = slot_root.resolve()
    try:
        resolved_slot_root.relative_to(repo_root)
    except ValueError:
        raise PortalProducerError("OUTPUT_SLOT_OUTSIDE_REPOSITORY") from None
    if resolved_slot_root != slot_root.absolute():
        raise PortalProducerError("OUTPUT_PATH_SYMLINK_BLOCKED")
    index_path = slot_root / "index.json"
    index = _load_index(index_path, repo_root, slot_root)
    envelope_bytes = canonical(envelope) + b"\n"
    projection_id = envelope["projection_id"]

    # The index is only a locator.  The immutable revision directories are
    # the evidence authority, so every replay revalidates their complete
    # sibling bundle and may only repair an index that is an exact prefix of
    # that fully-verified append-only history.
    stored = _scan_stored_revisions(
        repo_root, slot_root,
        briefing_date=ledger["briefing_date"], slot=ledger["slot"],
    )
    indexed = index["revisions"]
    if len(indexed) > len(stored) or indexed != stored[:len(indexed)]:
        raise PortalProducerError("PORTAL_INDEX_DISK_LINEAGE_MISMATCH")
    if indexed != stored:
        repaired = {
            "schema_version": INDEX_SCHEMA,
            "latest_revision": stored[-1]["revision"] if stored else 0,
            "latest_projection_id": stored[-1]["projection_id"] if stored else None,
            "revisions": stored,
        }
        _atomic_write(index_path, canonical(repaired) + b"\n")

    for entry in stored:
        if entry["projection_id"] != projection_id:
            continue
        expected = _revision_bodies(
            briefing_bytes, ledger, report, display, envelope, entry["revision"]
        )
        directory = (repo_root / entry["envelope_path"]).parent
        for name, body in expected.items():
            if (directory / name).read_bytes() != body:
                raise PortalProducerError("PROJECTION_ID_CONFLICT")
        return {
            "result": "NO_CHANGE",
            "projection_id": projection_id,
            "envelope_path": entry["envelope_path"],
            "envelope_sha256": entry["envelope_sha256"],
            "source_commit": envelope["source_commit"],
        }

    revisions = stored
    revision = len(revisions) + 1
    if revision > 999:
        raise PortalProducerError("IMMUTABLE_REVISION_SPACE_EXHAUSTED")
    final_dir = slot_root / f"rev-{revision:03d}"
    if final_dir.is_symlink() or final_dir.exists():
        raise PortalProducerError("IMMUTABLE_REVISION_CONFLICT")
    slot_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".rev-{revision:03d}.", dir=slot_root))
    try:
        bodies = _revision_bodies(
            briefing_bytes, ledger, report, display, envelope, revision
        )
        for name, body in bodies.items():
            (temporary / name).write_bytes(body)
        os.replace(temporary, final_dir)
    except BaseException:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()
        raise

    envelope_path = (final_dir / "portal-projection.json").relative_to(repo_root).as_posix()
    entry = {
        "revision": revision,
        "projection_id": projection_id,
        "envelope_path": envelope_path,
        "envelope_sha256": digest_bytes(envelope_bytes),
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
    }
    updated = {
        "schema_version": INDEX_SCHEMA,
        "latest_revision": revision,
        "latest_projection_id": projection_id,
        "revisions": [*revisions, entry],
    }
    _atomic_write(index_path, canonical(updated) + b"\n")
    return {"result": "APPLIED", **entry}


def build(args: argparse.Namespace) -> dict:
    repo_root = Path(args.repo_root).resolve()
    briefing_path = Path(args.briefing).resolve()
    ledger_path = Path(args.claim_ledger).resolve()
    report_path = Path(args.validation_report).resolve()
    display_path = Path(args.display_proposal).resolve()
    try:
        briefing_bytes = briefing_path.read_bytes()
    except OSError as exc:
        raise PortalProducerError(f"BRIEFING_UNREADABLE:{type(exc).__name__}") from None
    if not briefing_bytes.strip():
        raise PortalProducerError("BRIEFING_EMPTY")
    ledger = _read_json(ledger_path, "CLAIM_LEDGER_UNREADABLE")
    report = _read_json(report_path, "VALIDATION_REPORT_UNREADABLE")
    display = _read_json(display_path, "DISPLAY_PROPOSAL_UNREADABLE")
    ledger_bytes = ledger_path.read_bytes()
    report_bytes = report_path.read_bytes()
    display_bytes = display_path.read_bytes()
    if ledger_bytes != canonical(ledger) + b"\n":
        raise PortalProducerError("CLAIM_LEDGER_NOT_CANONICAL")
    if report_bytes != canonical(report) + b"\n":
        raise PortalProducerError("VALIDATION_REPORT_NOT_CANONICAL")
    if display_bytes != canonical(display) + b"\n":
        raise PortalProducerError("DISPLAY_PROPOSAL_NOT_CANONICAL")
    refs = validate_claim_ledger(repo_root, ledger)
    validate_display_proposal(display, ledger)
    validate_report(
        repo_root,
        report,
        ledger,
        briefing_bytes,
        ledger_bytes,
        display_bytes,
        refs,
    )
    envelope = build_envelope(ledger, report, display)
    out_root = Path(args.out_root)
    if not out_root.is_absolute():
        out_root = repo_root / out_root
    try:
        out_root.resolve().relative_to(repo_root)
    except ValueError:
        raise PortalProducerError("OUTPUT_ROOT_OUTSIDE_REPOSITORY") from None
    return publish_bundle(
        repo_root, out_root.resolve(), briefing_bytes, ledger, report, display, envelope
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--repo-root", default=".")
    build_parser.add_argument("--briefing", required=True)
    build_parser.add_argument("--claim-ledger", required=True)
    build_parser.add_argument("--validation-report", required=True)
    build_parser.add_argument("--display-proposal", required=True)
    build_parser.add_argument(
        "--out-root", default="evidence/validated_briefing_portal"
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = build(args)
    except PortalProducerError as exc:
        print(f"BLOCKED:{exc}")
        return 2
    print(canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
