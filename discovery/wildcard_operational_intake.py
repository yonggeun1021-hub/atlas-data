#!/usr/bin/env python3
"""Publish a committed, source-body-verified P3-11 wildcard submission.

The core wildcard contract records cases but intentionally has no tracked
publication path.  This operational boundary accepts only submission JSON and
primary-source bytes already present in one immutable repository commit.  It
rebuilds the core packet, binds every linked claim to exact source bytes and
publishes a content-addressed append-only envelope.  It never ranks, promotes,
proposes, orders, or trades.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "wildcard_operational_intake_contract.json"
SCHEMA_VERSION = "wildcard_operational_intake/v1"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_core():
    path = ROOT / "discovery" / "wildcard_discovery.py"
    spec = importlib.util.spec_from_file_location("atlas_wildcard_operational_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("CORE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _load_core()


class WildcardOperationalIntakeError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise WildcardOperationalIntakeError("UTC_INVALID")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise WildcardOperationalIntakeError("UTC_INVALID") from exc


def _safe_relative(value: str, prefix: str | None = None) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise WildcardOperationalIntakeError("PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise WildcardOperationalIntakeError("PATH_INVALID")
    normalized = path.as_posix()
    if prefix is not None and not normalized.startswith(prefix.rstrip("/") + "/"):
        raise WildcardOperationalIntakeError("PATH_OUTSIDE_CONTRACT_ROOT")
    return normalized


def _git(root: Path, *args: str, text: bool = False):
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=text,
        check=False,
    )
    if completed.returncode != 0:
        raise WildcardOperationalIntakeError("GIT_PROVENANCE_UNAVAILABLE")
    return completed.stdout


def _validate_commit(root: Path, commit: str) -> str:
    if not isinstance(commit, str) or FULL_SHA.fullmatch(commit) is None:
        raise WildcardOperationalIntakeError("SOURCE_COMMIT_NOT_IMMUTABLE_FULL_SHA")
    resolved = _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}", text=True).strip()
    if resolved != commit:
        raise WildcardOperationalIntakeError("SOURCE_COMMIT_NOT_IMMUTABLE_FULL_SHA")
    return commit


def _blob(root: Path, commit: str, relative: str) -> bytes:
    return _git(root, "show", f"{commit}:{relative}")


def _json_blob(root: Path, commit: str, relative: str) -> dict:
    raw = _blob(root, commit, relative)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WildcardOperationalIntakeError("SUBMISSION_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise WildcardOperationalIntakeError("SUBMISSION_JSON_INVALID")
    return value


def _commit_time(root: Path, commit: str) -> dt.datetime:
    value = _git(root, "show", "-s", "--format=%cI", commit, text=True).strip()
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WildcardOperationalIntakeError("GIT_TIME_INVALID") from exc
    if parsed.tzinfo is None:
        raise WildcardOperationalIntakeError("GIT_TIME_INVALID")
    return parsed.astimezone(dt.timezone.utc)


def _exact_content_first_seen(root: Path, commit: str, relative: str, raw: bytes) -> str:
    commits = _git(
        root,
        "log",
        "--format=%H",
        "--reverse",
        commit,
        "--",
        relative,
        text=True,
    ).splitlines()
    for candidate in commits:
        try:
            candidate_raw = _blob(root, candidate, relative)
        except WildcardOperationalIntakeError:
            continue
        if candidate_raw == raw:
            return _commit_time(root, candidate).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise WildcardOperationalIntakeError("EXACT_CONTENT_FIRST_SEEN_NOT_COMPUTABLE")


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": SCHEMA_VERSION,
        "source_contract_version": "wildcard_discovery/1",
        "source_packet_version": "wildcard_discovery_packet/1",
        "submission_root": "data/intake/wildcard",
        "publication_root": "evidence/operational/wildcard_discovery",
        "allowed_capture_kind": "PRIMARY_SOURCE",
        "publication_status": "WILDCARD_OPERATIONAL_INTAKE_PUBLISHED",
        "authority": {
            "intake_validation_authorized": True,
            "case_publication_authorized": True,
            "strength_claim_authorized": False,
            "importance_ranking_authorized": False,
            "candidate_eligibility_authorized": False,
            "stage_promotion_authorized": False,
            "rule_evaluation_authorized": False,
            "action_authorized": False,
            "proposal_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WildcardOperationalIntakeError("CONTRACT_INVALID") from exc
    if value != _expected_contract():
        raise WildcardOperationalIntakeError("CONTRACT_MISMATCH")
    return value


def _verify_checkout_path(root: Path, commit: str, relative: str) -> None:
    disk = root / relative
    try:
        disk_raw = disk.read_bytes()
    except OSError as exc:
        raise WildcardOperationalIntakeError("CHECKOUT_PATH_MISSING") from exc
    if disk_raw != _blob(root, commit, relative):
        raise WildcardOperationalIntakeError("CHECKOUT_PATH_NOT_AT_SOURCE_COMMIT")


def _linked_source_records(
    root: Path,
    commit: str,
    submission: dict,
    decision_at: dt.datetime,
    contract: dict,
    require_current_checkout: bool,
) -> list[dict]:
    records = []
    nominated_at = _utc(submission["nominated_at_utc"])
    for evidence in submission["evidence"]:
        if evidence["status"] != "EVIDENCE_LINKED":
            continue
        provenance = evidence.get("audit_provenance")
        if not isinstance(provenance, dict) or set(provenance) != {
            "record_locator",
            "capture_kind",
        }:
            raise WildcardOperationalIntakeError("SOURCE_PROVENANCE_FIELDS_INVALID")
        if provenance["capture_kind"] != contract["allowed_capture_kind"]:
            raise WildcardOperationalIntakeError("SOURCE_CAPTURE_KIND_INVALID")
        relative = _safe_relative(provenance["record_locator"])
        if require_current_checkout:
            _verify_checkout_path(root, commit, relative)
        raw = _blob(root, commit, relative)
        digest = hashlib.sha256(raw).hexdigest()
        if digest != evidence["source_identity"]["source_sha256"]:
            raise WildcardOperationalIntakeError("SOURCE_BODY_SHA_MISMATCH")
        first_seen = _exact_content_first_seen(root, commit, relative, raw)
        first_seen_at = _utc(first_seen)
        retrieved_at = _utc(evidence["source_identity"]["retrieved_at_utc"])
        effective_available_at = max(first_seen_at, retrieved_at)
        if retrieved_at > nominated_at or nominated_at > decision_at or effective_available_at > decision_at:
            raise WildcardOperationalIntakeError("SOURCE_BODY_PIT_ORDER_INVALID")
        records.append(
            {
                "evidence_id": evidence["evidence_id"],
                "record_locator": relative,
                "source_sha256": digest,
                "exact_content_first_seen_at": first_seen,
                "effective_available_at": effective_available_at.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            }
        )
    return sorted(records, key=lambda item: item["evidence_id"])


def build_envelope(
    submission_paths: list[str],
    source_commit: str,
    decision_at_utc: str,
    root: Path = ROOT,
    require_current_checkout: bool = False,
) -> dict:
    root = Path(root).resolve()
    contract = load_contract(root / "config" / CONTRACT_PATH.name)
    commit = _validate_commit(root, source_commit)
    decision_at = _utc(decision_at_utc)
    if require_current_checkout:
        head = _git(root, "rev-parse", "HEAD", text=True).strip()
        if head != commit:
            raise WildcardOperationalIntakeError("CHECKOUT_HEAD_MISMATCH")
    if not isinstance(submission_paths, list) or not submission_paths:
        raise WildcardOperationalIntakeError("SUBMISSIONS_EMPTY")
    normalized_paths = sorted(
        _safe_relative(path, contract["submission_root"]) for path in submission_paths
    )
    if len(normalized_paths) != len(set(normalized_paths)):
        raise WildcardOperationalIntakeError("SUBMISSION_PATH_DUPLICATE")
    submissions = []
    submission_lineage = []
    for relative in normalized_paths:
        if require_current_checkout:
            _verify_checkout_path(root, commit, relative)
        raw = _blob(root, commit, relative)
        submission = _json_blob(root, commit, relative)
        submissions.append(submission)
        first_seen = _exact_content_first_seen(root, commit, relative, raw)
        if _utc(first_seen) > decision_at:
            raise WildcardOperationalIntakeError("SUBMISSION_AVAILABLE_AFTER_DECISION")
        submission_lineage.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "exact_content_first_seen_at": first_seen,
            }
        )
    core_packet = CORE.build_packet(
        {
            "schema_version": CORE.INPUT_SCHEMA_VERSION,
            "as_of_utc": decision_at_utc,
            "submissions": submissions,
        }
    )
    source_records = []
    for submission in submissions:
        source_records.extend(
            _linked_source_records(
                root,
                commit,
                submission,
                decision_at,
                contract,
                require_current_checkout,
            )
        )
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "status": contract["publication_status"],
        "source_commit": commit,
        "decision_at_utc": decision_at_utc,
        "submission_lineage": submission_lineage,
        "source_body_lineage": sorted(
            source_records, key=lambda item: (item["evidence_id"], item["record_locator"])
        ),
        "packet": core_packet,
        "summary": {
            "submission_count": core_packet["submission_count"],
            "case_count": core_packet["case_count"],
            "pending_count": core_packet["pending_count"],
            "linked_source_body_count": len(source_records),
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    envelope["payload_sha256"] = payload_sha256(envelope)
    return envelope


def validate_envelope(value: dict, root: Path = ROOT) -> dict:
    if not isinstance(value, dict):
        raise WildcardOperationalIntakeError("ENVELOPE_INVALID")
    expected = build_envelope(
        [item["path"] for item in value.get("submission_lineage", [])],
        value.get("source_commit"),
        value.get("decision_at_utc"),
        root,
        require_current_checkout=False,
    )
    if value != expected:
        raise WildcardOperationalIntakeError("ENVELOPE_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


def publication_path(value: dict, root: Path = ROOT) -> Path:
    contract = load_contract(Path(root) / "config" / CONTRACT_PATH.name)
    day = value["decision_at_utc"][:10]
    return Path(root) / contract["publication_root"] / day / (
        f"wildcard-{value['payload_sha256'][:16]}.json"
    )


def publish(value: dict, root: Path = ROOT) -> Path:
    checked = validate_envelope(value, root)
    target = publication_path(checked, root)
    rendered = (json.dumps(checked, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if target.exists():
        if target.read_bytes() != rendered:
            raise WildcardOperationalIntakeError("APPEND_ONLY_PUBLICATION_DRIFT")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", action="append", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--decision-at-utc", required=True)
    args = parser.parse_args()
    envelope = build_envelope(
        args.submission,
        args.source_commit,
        args.decision_at_utc,
        ROOT,
        require_current_checkout=True,
    )
    target = publish(envelope, ROOT)
    print(
        "wildcard operational intake: "
        f"submissions={envelope['summary']['submission_count']} "
        f"cases={envelope['summary']['case_count']} "
        f"pending={envelope['summary']['pending_count']} path={target.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
