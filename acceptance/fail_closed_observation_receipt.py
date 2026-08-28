#!/usr/bin/env python3
"""P8-15 attested observer for genuine scheduled Daily Briefing failures.

The successful Daily Briefing workflow cannot prove its own fail-closed
behavior.  This module is consumed by a separate ``workflow_run`` workflow,
which receives GitHub-authored completion metadata, validates the pinned
upstream workflow bytes without executing them, and emits an append-only
receipt.  The receipt is counted only after GitHub build-provenance
attestation is stored and reverified offline against a contract-pinned trusted
root.

No receipt grants investment, action, order, Production, or trading authority.
Manual dispatch, cancellation, skipped/successful runs, and non-workflow_run
callers are excluded rather than converted into a failure sample.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_DIR = re.compile(r"^run-(\d+)-attempt-(\d+)$")
UPSTREAM_WORKFLOW_NAME = "Atlas Daily Briefing Integration v1"
UPSTREAM_WORKFLOW_PATH = ".github/workflows/daily-briefing.yml"
OBSERVER_WORKFLOW_NAME = "Observe Atlas Daily Briefing Fail-Closed"
SIGNER_WORKFLOW = (
    "yonggeun1021-hub/atlas-data/"
    ".github/workflows/observe-daily-briefing-fail-closed.yml"
)
REPOSITORY = "yonggeun1021-hub/atlas-data"
FAILURE_CONCLUSIONS = {"failure", "timed_out"}
EXPECTED_FILES = {
    "receipt.json",
    "attestation.jsonl",
    "trusted_root.jsonl",
    "observation.json",
}
AUTHORITY = {
    "evidence_observation_only": True,
    "regime_authority": False,
    "strategy_authority": False,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class FailClosedReceiptError(RuntimeError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise FailClosedReceiptError(f"{code}{': ' + detail if detail else ''}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: dict, hash_field: str) -> str:
    payload = dict(value)
    payload.pop(hash_field, None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _positive_int(value, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(code)
    return value


def _full_sha(value, code: str) -> str:
    if not isinstance(value, str) or FULL_SHA.fullmatch(value) is None:
        fail(code)
    return value


def _sha256(value, code: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        fail(code)
    return value


def _timestamp(value, code: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail(code)
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise FailClosedReceiptError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        fail(code)
    return value


def _load_json_bytes(value: bytes, code: str) -> dict:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FailClosedReceiptError(code) from exc
    if not isinstance(parsed, dict):
        fail(code)
    return parsed


def _git_blob(repo_root: Path, commit: str, path: str) -> bytes:
    _full_sha(commit, "FAIL_CLOSED_UPSTREAM_HEAD_INVALID")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{commit}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        fail("FAIL_CLOSED_PINNED_WORKFLOW_UNAVAILABLE")
    return result.stdout


def qualification(observer_event: str, upstream_event: str, conclusion: str) -> str:
    if observer_event != "workflow_run":
        return "OBSERVER_EVENT_EXCLUDED"
    if upstream_event != "schedule":
        return "NON_SCHEDULE_UPSTREAM_EXCLUDED"
    if conclusion not in FAILURE_CONCLUSIONS:
        return "NON_FAILURE_UPSTREAM_EXCLUDED"
    return "GENUINE_SCHEDULED_FAIL_CLOSED_RUN"


def build_receipt(
    repo_root: Path,
    *,
    observer_event: str,
    observer_run_id: int,
    observer_run_attempt: int,
    observer_head_sha: str,
    upstream_workflow_name: str,
    upstream_workflow_path: str,
    upstream_event: str,
    upstream_conclusion: str,
    upstream_run_id: int,
    upstream_run_attempt: int,
    upstream_head_sha: str,
    upstream_started_at: str,
    upstream_completed_at: str,
) -> tuple[str, dict | None]:
    status = qualification(observer_event, upstream_event, upstream_conclusion)
    if status != "GENUINE_SCHEDULED_FAIL_CLOSED_RUN":
        return status, None
    if (
        upstream_workflow_name != UPSTREAM_WORKFLOW_NAME
        or upstream_workflow_path != UPSTREAM_WORKFLOW_PATH
    ):
        fail("FAIL_CLOSED_UPSTREAM_WORKFLOW_IDENTITY_INVALID")
    observer_run_id = _positive_int(observer_run_id, "FAIL_CLOSED_OBSERVER_RUN_ID_INVALID")
    observer_run_attempt = _positive_int(
        observer_run_attempt, "FAIL_CLOSED_OBSERVER_RUN_ATTEMPT_INVALID"
    )
    upstream_run_id = _positive_int(upstream_run_id, "FAIL_CLOSED_SUBJECT_RUN_ID_INVALID")
    upstream_run_attempt = _positive_int(
        upstream_run_attempt, "FAIL_CLOSED_SUBJECT_RUN_ATTEMPT_INVALID"
    )
    _full_sha(observer_head_sha, "FAIL_CLOSED_OBSERVER_HEAD_INVALID")
    _full_sha(upstream_head_sha, "FAIL_CLOSED_SUBJECT_HEAD_INVALID")
    started_at = _timestamp(upstream_started_at, "FAIL_CLOSED_STARTED_AT_INVALID")
    completed_at = _timestamp(upstream_completed_at, "FAIL_CLOSED_COMPLETED_AT_INVALID")
    if completed_at < started_at:
        fail("FAIL_CLOSED_COMPLETED_BEFORE_STARTED")
    workflow_bytes = _git_blob(repo_root, upstream_head_sha, UPSTREAM_WORKFLOW_PATH)
    if f"name: {UPSTREAM_WORKFLOW_NAME}".encode("utf-8") not in workflow_bytes:
        fail("FAIL_CLOSED_PINNED_WORKFLOW_IDENTITY_INVALID")
    receipt = {
        "schema_version": "capital_rotation_fail_closed_observation/1",
        "wbs_item": "P8-15",
        "sample_qualification": status,
        "observer": {
            "workflow": OBSERVER_WORKFLOW_NAME,
            "event_name": observer_event,
            "run_id": observer_run_id,
            "run_attempt": observer_run_attempt,
            "workflow_head_sha": observer_head_sha,
        },
        "subject": {
            "workflow_name": upstream_workflow_name,
            "workflow_path": upstream_workflow_path,
            "event_name": upstream_event,
            "conclusion": upstream_conclusion,
            "run_id": upstream_run_id,
            "run_attempt": upstream_run_attempt,
            "head_sha": upstream_head_sha,
            "run_started_at": started_at,
            "completed_at": completed_at,
            "workflow_sha256": bytes_sha256(workflow_bytes),
        },
        "completion_state": "GITHUB_WORKFLOW_RUN_FAILURE_OBSERVED",
        "authority": dict(AUTHORITY),
    }
    receipt["receipt_sha256"] = payload_sha256(receipt, "receipt_sha256")
    return status, receipt


def validate_receipt(repo_root: Path, value: dict) -> dict:
    if set(value) != {
        "schema_version", "wbs_item", "sample_qualification", "observer",
        "subject", "completion_state", "authority", "receipt_sha256",
    }:
        fail("FAIL_CLOSED_RECEIPT_FIELDS_MISMATCH")
    observer = value.get("observer")
    subject = value.get("subject")
    if not isinstance(observer, dict) or set(observer) != {
        "workflow", "event_name", "run_id", "run_attempt", "workflow_head_sha",
    }:
        fail("FAIL_CLOSED_OBSERVER_FIELDS_MISMATCH")
    if not isinstance(subject, dict) or set(subject) != {
        "workflow_name", "workflow_path", "event_name", "conclusion", "run_id",
        "run_attempt", "head_sha", "run_started_at", "completed_at",
        "workflow_sha256",
    }:
        fail("FAIL_CLOSED_SUBJECT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != "capital_rotation_fail_closed_observation/1"
        or value.get("wbs_item") != "P8-15"
        or observer.get("workflow") != OBSERVER_WORKFLOW_NAME
        or subject.get("workflow_name") != UPSTREAM_WORKFLOW_NAME
        or subject.get("workflow_path") != UPSTREAM_WORKFLOW_PATH
        or value.get("completion_state") != "GITHUB_WORKFLOW_RUN_FAILURE_OBSERVED"
        or value.get("authority") != AUTHORITY
    ):
        fail("FAIL_CLOSED_RECEIPT_IDENTITY_INVALID")
    expected = qualification(
        observer.get("event_name"), subject.get("event_name"), subject.get("conclusion")
    )
    if (
        expected != "GENUINE_SCHEDULED_FAIL_CLOSED_RUN"
        or value.get("sample_qualification") != expected
    ):
        fail("FAIL_CLOSED_QUALIFICATION_INVALID")
    for row, prefix in ((observer, "OBSERVER"), (subject, "SUBJECT")):
        _positive_int(row.get("run_id"), f"FAIL_CLOSED_{prefix}_RUN_ID_INVALID")
        _positive_int(
            row.get("run_attempt"), f"FAIL_CLOSED_{prefix}_RUN_ATTEMPT_INVALID"
        )
    _full_sha(observer.get("workflow_head_sha"), "FAIL_CLOSED_OBSERVER_HEAD_INVALID")
    _full_sha(subject.get("head_sha"), "FAIL_CLOSED_SUBJECT_HEAD_INVALID")
    started_at = _timestamp(subject.get("run_started_at"), "FAIL_CLOSED_STARTED_AT_INVALID")
    completed_at = _timestamp(subject.get("completed_at"), "FAIL_CLOSED_COMPLETED_AT_INVALID")
    if completed_at < started_at:
        fail("FAIL_CLOSED_COMPLETED_BEFORE_STARTED")
    workflow_bytes = _git_blob(repo_root, subject["head_sha"], UPSTREAM_WORKFLOW_PATH)
    _sha256(subject.get("workflow_sha256"), "FAIL_CLOSED_WORKFLOW_HASH_INVALID")
    if (
        subject["workflow_sha256"] != bytes_sha256(workflow_bytes)
        or f"name: {UPSTREAM_WORKFLOW_NAME}".encode("utf-8") not in workflow_bytes
    ):
        fail("FAIL_CLOSED_PINNED_WORKFLOW_MISMATCH")
    _sha256(value.get("receipt_sha256"), "FAIL_CLOSED_RECEIPT_HASH_INVALID")
    if value["receipt_sha256"] != payload_sha256(value, "receipt_sha256"):
        fail("FAIL_CLOSED_RECEIPT_HASH_MISMATCH")
    return value


def package_path(root: Path, receipt: dict) -> Path:
    subject = receipt["subject"]
    return root / f"run-{subject['run_id']}-attempt-{subject['run_attempt']}"


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        temporary.write_bytes(value)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_package(root: Path, receipt: dict) -> tuple[Path, bool]:
    target = package_path(root, receipt)
    rendered = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    receipt_path = target / "receipt.json"
    if target.exists():
        existing = set(item.name for item in target.iterdir())
        if existing == EXPECTED_FILES and receipt_path.read_bytes() == rendered:
            return receipt_path, False
        if existing == {"receipt.json"} and receipt_path.read_bytes() == rendered:
            return receipt_path, True
        fail("FAIL_CLOSED_APPEND_ONLY_CONFLICT", target.as_posix())
    target.mkdir(parents=True)
    _atomic_write(receipt_path, rendered)
    return receipt_path, True


Verifier = Callable[[Path, Path, Path, dict], None]


def _verification_command(receipt: Path, bundle: Path, root: Path, value: dict) -> list[str]:
    return [
        "gh", "attestation", "verify", str(receipt),
        "--repo", REPOSITORY,
        "--bundle", str(bundle),
        "--custom-trusted-root", str(root),
        "--signer-workflow", SIGNER_WORKFLOW,
        "--source-digest", value["observer"]["workflow_head_sha"],
        "--source-ref", "refs/heads/main",
        "--deny-self-hosted-runners",
        "--no-public-good",
        "--format", "json",
    ]


def verify_attestation(receipt: Path, bundle: Path, root: Path, value: dict) -> None:
    result = subprocess.run(
        _verification_command(receipt, bundle, root, value),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        fail("FAIL_CLOSED_ATTESTATION_VERIFICATION_FAILED")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FailClosedReceiptError("FAIL_CLOSED_ATTESTATION_OUTPUT_INVALID") from exc
    if not isinstance(parsed, list) or not parsed:
        fail("FAIL_CLOSED_ATTESTATION_OUTPUT_INVALID")


def _observation_record(
    receipt: dict,
    receipt_bytes: bytes,
    bundle_bytes: bytes,
    root_bytes: bytes,
) -> dict:
    record = {
        "schema_version": "fail_closed_observation_import/1",
        "wbs_item": "P8-15",
        "receipt_sha256": bytes_sha256(receipt_bytes),
        "attestation_bundle_sha256": bytes_sha256(bundle_bytes),
        "trusted_root_sha256": bytes_sha256(root_bytes),
        "attestation_policy": {
            "predicate_type": "https://slsa.dev/provenance/v1",
            "repository": REPOSITORY,
            "signer_workflow": SIGNER_WORKFLOW,
            "source_digest": receipt["observer"]["workflow_head_sha"],
            "source_ref": "refs/heads/main",
            "self_hosted_runners_allowed": False,
            "offline_bundle_reverification": True,
        },
        "authority": dict(AUTHORITY),
    }
    record["observation_sha256"] = payload_sha256(record, "observation_sha256")
    return record


def validate_package(
    repo_root: Path,
    package: Path,
    *,
    fail_root: Path,
    expected_trusted_root_sha256: str,
    verifier: Verifier | None = None,
) -> dict:
    if not package.is_dir() or set(item.name for item in package.iterdir()) != EXPECTED_FILES:
        fail("FAIL_CLOSED_PACKAGE_FILES_MISMATCH")
    receipt_path = package / "receipt.json"
    bundle_path = package / "attestation.jsonl"
    root_path = package / "trusted_root.jsonl"
    observation_path = package / "observation.json"
    receipt_bytes = receipt_path.read_bytes()
    bundle_bytes = bundle_path.read_bytes()
    root_bytes = root_path.read_bytes()
    receipt = validate_receipt(
        repo_root, _load_json_bytes(receipt_bytes, "FAIL_CLOSED_RECEIPT_UNREADABLE")
    )
    expected_package = package_path(fail_root, receipt)
    match = PACKAGE_DIR.fullmatch(package.name)
    if (
        match is None
        or int(match.group(1)) != receipt["subject"]["run_id"]
        or int(match.group(2)) != receipt["subject"]["run_attempt"]
        or package.resolve() != expected_package.resolve()
    ):
        fail("FAIL_CLOSED_PACKAGE_PATH_MISMATCH")
    _sha256(expected_trusted_root_sha256, "FAIL_CLOSED_TRUSTED_ROOT_CONTRACT_INVALID")
    if bytes_sha256(root_bytes) != expected_trusted_root_sha256:
        fail("FAIL_CLOSED_TRUSTED_ROOT_NOT_CONTRACT_PINNED")
    observed = _load_json_bytes(
        observation_path.read_bytes(), "FAIL_CLOSED_OBSERVATION_UNREADABLE"
    )
    expected_record = _observation_record(
        receipt, receipt_bytes, bundle_bytes, root_bytes
    )
    if observed != expected_record:
        fail("FAIL_CLOSED_OBSERVATION_DRIFT_OR_TAMPER")
    (verifier or verify_attestation)(
        receipt_path, bundle_path, root_path, receipt
    )
    return receipt


def iter_receipts(
    repo_root: Path,
    fail_root: Path,
    *,
    expected_trusted_root_sha256: str,
    verifier: Verifier | None = None,
) -> list[dict]:
    if not fail_root.exists():
        return []
    packages = sorted(fail_root.glob("run-*-attempt-*"))
    stray = [
        path for path in fail_root.rglob("*")
        if path.is_file() and not any(parent in packages for parent in path.parents)
    ]
    if stray:
        fail("UNTRUSTED_FAIL_CLOSED_RECEIPT_PRESENT", stray[0].as_posix())
    return [
        validate_package(
            repo_root,
            package,
            fail_root=fail_root,
            expected_trusted_root_sha256=expected_trusted_root_sha256,
            verifier=verifier,
        )
        for package in packages
    ]


def seal_package(
    repo_root: Path,
    package: Path,
    bundle_source: Path,
    *,
    expected_trusted_root_sha256: str,
) -> bool:
    if not package.is_dir() or set(item.name for item in package.iterdir()) != {"receipt.json"}:
        fail("FAIL_CLOSED_UNSEALED_PACKAGE_INVALID")
    receipt_path = package / "receipt.json"
    receipt_bytes = receipt_path.read_bytes()
    receipt = validate_receipt(
        repo_root, _load_json_bytes(receipt_bytes, "FAIL_CLOSED_RECEIPT_UNREADABLE")
    )
    bundle_bytes = bundle_source.read_bytes()
    trusted = subprocess.run(
        ["gh", "attestation", "trusted-root"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if trusted.returncode or not trusted.stdout:
        fail("FAIL_CLOSED_TRUSTED_ROOT_DOWNLOAD_FAILED")
    root_bytes = trusted.stdout
    if bytes_sha256(root_bytes) != expected_trusted_root_sha256:
        fail("FAIL_CLOSED_TRUSTED_ROOT_NOT_CONTRACT_PINNED")
    bundle_path = package / "attestation.jsonl"
    root_path = package / "trusted_root.jsonl"
    _atomic_write(bundle_path, bundle_bytes)
    _atomic_write(root_path, root_bytes)
    verify_attestation(receipt_path, bundle_path, root_path, receipt)
    record = _observation_record(receipt, receipt_bytes, bundle_bytes, root_bytes)
    _atomic_write(
        package / "observation.json",
        (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    validate_package(
        repo_root,
        package,
        fail_root=package.parent,
        expected_trusted_root_sha256=expected_trusted_root_sha256,
    )
    return True


def _common_prepare_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--observer-event", required=True)
    parser.add_argument("--observer-run-id", type=int, required=True)
    parser.add_argument("--observer-run-attempt", type=int, required=True)
    parser.add_argument("--observer-head-sha", required=True)
    parser.add_argument("--upstream-workflow-name", required=True)
    parser.add_argument("--upstream-workflow-path", required=True)
    parser.add_argument("--upstream-event", required=True)
    parser.add_argument("--upstream-conclusion", required=True)
    parser.add_argument("--upstream-run-id", type=int, required=True)
    parser.add_argument("--upstream-run-attempt", type=int, required=True)
    parser.add_argument("--upstream-head-sha", required=True)
    parser.add_argument("--upstream-started-at", required=True)
    parser.add_argument("--upstream-completed-at", required=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--fail-root", type=Path, required=True)
    _common_prepare_arguments(prepare)
    seal = sub.add_parser("seal")
    seal.add_argument("--package", type=Path, required=True)
    seal.add_argument("--bundle", type=Path, required=True)
    seal.add_argument("--trusted-root-sha256", required=True)
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.command == "prepare":
        status, receipt = build_receipt(
            repo_root,
            observer_event=args.observer_event,
            observer_run_id=args.observer_run_id,
            observer_run_attempt=args.observer_run_attempt,
            observer_head_sha=args.observer_head_sha,
            upstream_workflow_name=args.upstream_workflow_name,
            upstream_workflow_path=args.upstream_workflow_path,
            upstream_event=args.upstream_event,
            upstream_conclusion=args.upstream_conclusion,
            upstream_run_id=args.upstream_run_id,
            upstream_run_attempt=args.upstream_run_attempt,
            upstream_head_sha=args.upstream_head_sha,
            upstream_started_at=args.upstream_started_at,
            upstream_completed_at=args.upstream_completed_at,
        )
        print(f"fail_closed_observation_status={status}")
        if receipt is None:
            print("fail_closed_receipt_path=")
            print("fail_closed_package_path=")
            return 0
        fail_root = args.fail_root if args.fail_root.is_absolute() else repo_root / args.fail_root
        receipt_path, _ = prepare_package(fail_root, receipt)
        print(f"fail_closed_receipt_path={receipt_path.relative_to(repo_root).as_posix()}")
        print(f"fail_closed_package_path={receipt_path.parent.relative_to(repo_root).as_posix()}")
        return 0
    package = args.package if args.package.is_absolute() else repo_root / args.package
    bundle = args.bundle if args.bundle.is_absolute() else repo_root / args.bundle
    seal_package(
        repo_root,
        package,
        bundle,
        expected_trusted_root_sha256=args.trusted_root_sha256,
    )
    print("fail_closed_package_sealed=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
