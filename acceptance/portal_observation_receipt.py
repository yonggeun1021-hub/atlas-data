#!/usr/bin/env python3
"""Trusted P8-15 Portal observation receipt import and offline validation.

The Portal repository owns viewer observation and GitHub artifact attestation.
Atlas-data accepts only an exact copy of that receipt accompanied by its
attestation bundle, a downloaded trusted root, and an append-only import
record.  A self-authored or merely self-hashed JSON file never qualifies.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Iterable


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PACKAGE_DIR = re.compile(r"^run-(\d+)-attempt-(\d+)$")
PORTAL_SOURCE_PATH = re.compile(
    r"^evidence/p8-15/portal-observations/(\d{4}-\d{2}-\d{2})/"
    r"run-(\d+)-attempt-(\d+)\.json$"
)
PORTAL_REPOSITORY = "yonggeun1021-hub/atlas-portal"
SIGNER_WORKFLOW = (
    "yonggeun1021-hub/atlas-portal/"
    ".github/workflows/observe-p8-15-portal.yml"
)
OBSERVER_WORKFLOW = "Observe Atlas P8-15 Portal Projection"
OBSERVER_SCHEDULE = "0 23 * * 1-5"
EXPECTED_FILES = {
    "receipt.json",
    "attestation.jsonl",
    "trusted_root.jsonl",
    "import.json",
}
RECEIPT_AUTHORITY = {
    "evidence_observation_only": True,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}
IMPORT_AUTHORITY = {
    "evidence_import_only": True,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class PortalReceiptError(RuntimeError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise PortalReceiptError(f"{code}{': ' + detail if detail else ''}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: dict, hash_field: str) -> str:
    payload = dict(value)
    payload.pop(hash_field, None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json_bytes(value: bytes, code: str) -> dict:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortalReceiptError(code) from exc
    if not isinstance(parsed, dict):
        fail(code)
    return parsed


def _full_sha(value, code: str) -> str:
    if not isinstance(value, str) or FULL_SHA.fullmatch(value) is None:
        fail(code)
    return value


def _sha256(value, code: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        fail(code)
    return value


def _positive_int(value, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(code)
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str) or DATE.fullmatch(value) is None:
        fail(code)
    try:
        if dt.date.fromisoformat(value).isoformat() != value:
            fail(code)
    except ValueError as exc:
        raise PortalReceiptError(code) from exc
    return value


def _timestamp(value, code: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail(code)
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PortalReceiptError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        fail(code)
    return value


def _safe_path(value, code: str) -> str:
    if not isinstance(value, str) or not value:
        fail(code)
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts:
        fail(code)
    return parsed.as_posix()


def _expected_qualification(observer: dict) -> str:
    if observer.get("event_name") == "workflow_dispatch":
        return "MANUAL_DIAGNOSTIC_EXCLUDED"
    if (
        observer.get("event_name") == "schedule"
        and observer.get("event_schedule") == OBSERVER_SCHEDULE
    ):
        return "NATURAL_SCHEDULED_PORTAL_OBSERVATION"
    return "NATURAL_PROVENANCE_NOT_COMPUTABLE"


def _assert_false_authority(value: dict, expected: dict, code: str) -> None:
    if value != expected:
        fail(code)


def validate_portal_receipt(value: dict) -> dict:
    if set(value) != {
        "schema_version", "wbs_item", "sample_qualification", "observer", "site",
        "natural_pair", "completion_state", "authority", "receipt_sha256",
    }:
        fail("PORTAL_RECEIPT_FIELDS_MISMATCH")
    observer = value.get("observer")
    site = value.get("site")
    natural_pair = value.get("natural_pair")
    if not isinstance(observer, dict) or set(observer) != {
        "workflow", "event_name", "event_schedule", "run_id", "run_attempt",
        "workflow_head_sha", "observed_at_utc",
    }:
        fail("PORTAL_OBSERVER_FIELDS_MISMATCH")
    if not isinstance(site, dict) or set(site) != {
        "url", "portal_source_commit", "api_url", "api_sha256", "page_url",
        "page_html_sha256",
    }:
        fail("PORTAL_SITE_FIELDS_MISMATCH")
    if not isinstance(natural_pair, dict) or set(natural_pair) != {
        "decision_date", "atlas_discovery_commit", "slots",
    }:
        fail("PORTAL_PAIR_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != "portal_projection_observation/1"
        or value.get("wbs_item") != "P8-15"
        or value.get("completion_state") != "VIEWER_HTML_AND_API_PAIR_VALIDATED"
        or observer.get("workflow") != OBSERVER_WORKFLOW
    ):
        fail("PORTAL_RECEIPT_IDENTITY_INVALID")
    _positive_int(observer.get("run_id"), "PORTAL_RUN_ID_INVALID")
    _positive_int(observer.get("run_attempt"), "PORTAL_RUN_ATTEMPT_INVALID")
    _full_sha(observer.get("workflow_head_sha"), "PORTAL_WORKFLOW_HEAD_INVALID")
    _timestamp(observer.get("observed_at_utc"), "PORTAL_OBSERVED_AT_INVALID")
    if value.get("sample_qualification") != _expected_qualification(observer):
        fail("PORTAL_QUALIFICATION_TAMPERED")
    if site.get("url") != "https://atlas-investment-console.yonggeun1021.chatgpt.site":
        fail("PORTAL_SITE_IDENTITY_INVALID")
    _full_sha(site.get("portal_source_commit"), "PORTAL_SOURCE_COMMIT_INVALID")
    _sha256(site.get("api_sha256"), "PORTAL_API_HASH_INVALID")
    _sha256(site.get("page_html_sha256"), "PORTAL_PAGE_HASH_INVALID")
    if site.get("api_url") != f"{site['url']}/api/v1/atlas/scheduled-briefing" or site.get("page_url") != f"{site['url']}/briefing":
        fail("PORTAL_SITE_LOCATOR_INVALID")
    decision_date = _date(natural_pair.get("decision_date"), "PORTAL_PAIR_DATE_INVALID")
    _full_sha(natural_pair.get("atlas_discovery_commit"), "PORTAL_ATLAS_COMMIT_INVALID")
    slots = natural_pair.get("slots")
    if not isinstance(slots, list) or len(slots) != 2:
        fail("PORTAL_SLOTS_INVALID")
    seen = set()
    for slot in slots:
        if not isinstance(slot, dict) or set(slot) != {
            "slot", "run_id", "run_attempt", "workflow_head_sha", "source_commit",
            "generation_id", "packet_sha256", "briefing_sha256",
        }:
            fail("PORTAL_SLOT_FIELDS_MISMATCH")
        if slot.get("slot") not in {"morning", "evening"} or slot["slot"] in seen:
            fail("PORTAL_SLOT_IDENTITY_INVALID")
        seen.add(slot["slot"])
        _positive_int(slot.get("run_id"), "PORTAL_SLOT_RUN_ID_INVALID")
        _positive_int(slot.get("run_attempt"), "PORTAL_SLOT_RUN_ATTEMPT_INVALID")
        _full_sha(slot.get("workflow_head_sha"), "PORTAL_SLOT_WORKFLOW_HEAD_INVALID")
        _full_sha(slot.get("source_commit"), "PORTAL_SLOT_SOURCE_COMMIT_INVALID")
        _sha256(slot.get("generation_id"), "PORTAL_SLOT_GENERATION_INVALID")
        _sha256(slot.get("packet_sha256"), "PORTAL_SLOT_PACKET_HASH_INVALID")
        _sha256(slot.get("briefing_sha256"), "PORTAL_SLOT_BRIEFING_HASH_INVALID")
    if seen != {"morning", "evening"}:
        fail("PORTAL_SLOTS_INVALID")
    _assert_false_authority(value.get("authority"), RECEIPT_AUTHORITY, "PORTAL_AUTHORITY_INVALID")
    _sha256(value.get("receipt_sha256"), "PORTAL_RECEIPT_HASH_INVALID")
    if value["receipt_sha256"] != payload_sha256(value, "receipt_sha256"):
        fail("PORTAL_RECEIPT_HASH_MISMATCH")
    if decision_date > observer["observed_at_utc"][:10]:
        fail("PORTAL_OBSERVED_BEFORE_DECISION_DATE")
    return value


def _verification_command(receipt_path: Path, bundle_path: Path, root_path: Path, receipt: dict) -> list[str]:
    return [
        "gh", "attestation", "verify", str(receipt_path),
        "--repo", PORTAL_REPOSITORY,
        "--bundle", str(bundle_path),
        "--custom-trusted-root", str(root_path),
        "--signer-workflow", SIGNER_WORKFLOW,
        "--source-digest", receipt["observer"]["workflow_head_sha"],
        "--source-ref", "refs/heads/main",
        "--deny-self-hosted-runners",
        "--no-public-good",
        "--format", "json",
    ]


Verifier = Callable[[Path, Path, Path, dict], None]


def verify_attestation(receipt_path: Path, bundle_path: Path, root_path: Path, receipt: dict) -> None:
    result = subprocess.run(
        _verification_command(receipt_path, bundle_path, root_path, receipt),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if result.returncode:
        fail("PORTAL_ATTESTATION_VERIFICATION_FAILED")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PortalReceiptError("PORTAL_ATTESTATION_OUTPUT_INVALID") from exc
    if not isinstance(parsed, list) or not parsed:
        fail("PORTAL_ATTESTATION_OUTPUT_INVALID")


def _package_path(portal_root: Path, receipt: dict) -> Path:
    observer = receipt["observer"]
    return (
        portal_root / receipt["natural_pair"]["decision_date"]
        / f"run-{observer['run_id']}-attempt-{observer['run_attempt']}"
    )


def validate_import_package(
    package: Path,
    *,
    portal_root: Path,
    verifier: Verifier | None = None,
    expected_trusted_root_sha256: str,
) -> dict:
    if not package.is_dir() or set(item.name for item in package.iterdir()) != EXPECTED_FILES:
        fail("PORTAL_IMPORT_PACKAGE_FILES_MISMATCH")
    receipt_path = package / "receipt.json"
    bundle_path = package / "attestation.jsonl"
    root_path = package / "trusted_root.jsonl"
    import_path = package / "import.json"
    receipt_bytes = receipt_path.read_bytes()
    bundle_bytes = bundle_path.read_bytes()
    root_bytes = root_path.read_bytes()
    if bytes_sha256(root_bytes) != expected_trusted_root_sha256:
        fail("PORTAL_TRUSTED_ROOT_NOT_CONTRACT_PINNED")
    receipt = validate_portal_receipt(_load_json_bytes(receipt_bytes, "PORTAL_RECEIPT_UNREADABLE"))
    imported = _load_json_bytes(import_path.read_bytes(), "PORTAL_IMPORT_RECORD_UNREADABLE")
    if set(imported) != {
        "schema_version", "wbs_item", "source_repository", "source_commit",
        "source_commit_role",
        "source_path", "receipt_sha256", "attestation_bundle_sha256",
        "trusted_root_sha256", "attestation_policy", "importer", "authority",
        "import_record_sha256",
    }:
        fail("PORTAL_IMPORT_RECORD_FIELDS_MISMATCH")
    if (
        imported.get("schema_version") != "portal_observation_import/1"
        or imported.get("wbs_item") != "P8-15"
        or imported.get("source_repository") != PORTAL_REPOSITORY
    ):
        fail("PORTAL_IMPORT_RECORD_IDENTITY_INVALID")
    _full_sha(imported.get("source_commit"), "PORTAL_IMPORT_SOURCE_COMMIT_INVALID")
    if imported.get("source_commit_role") != "DISCOVERY_COMMIT_NOT_ATTESTATION_IDENTITY":
        fail("PORTAL_IMPORT_SOURCE_COMMIT_ROLE_INVALID")
    source_path = _safe_path(imported.get("source_path"), "PORTAL_IMPORT_SOURCE_PATH_INVALID")
    match = PORTAL_SOURCE_PATH.fullmatch(source_path)
    if not match:
        fail("PORTAL_IMPORT_SOURCE_PATH_INVALID")
    date, run_id, run_attempt = match.groups()
    observer = receipt["observer"]
    if (
        date != receipt["natural_pair"]["decision_date"]
        or int(run_id) != observer["run_id"]
        or int(run_attempt) != observer["run_attempt"]
        or package.resolve() != _package_path(portal_root, receipt).resolve()
        or PACKAGE_DIR.fullmatch(package.name) is None
    ):
        fail("PORTAL_IMPORT_PATH_MISMATCH")
    for key, actual in (
        ("receipt_sha256", bytes_sha256(receipt_bytes)),
        ("attestation_bundle_sha256", bytes_sha256(bundle_bytes)),
        ("trusted_root_sha256", bytes_sha256(root_bytes)),
    ):
        _sha256(imported.get(key), f"PORTAL_IMPORT_{key.upper()}_INVALID")
        if imported[key] != actual:
            fail("PORTAL_IMPORT_BYTES_MISMATCH", key)
    policy = imported.get("attestation_policy")
    if policy != {
        "predicate_type": "https://slsa.dev/provenance/v1",
        "repository": PORTAL_REPOSITORY,
        "signer_workflow": SIGNER_WORKFLOW,
        "source_digest": observer["workflow_head_sha"],
        "source_ref": "refs/heads/main",
        "self_hosted_runners_allowed": False,
        "online_verification_performed": True,
        "offline_bundle_reverification": True,
    }:
        fail("PORTAL_ATTESTATION_POLICY_MISMATCH")
    importer = imported.get("importer")
    if not isinstance(importer, dict) or set(importer) != {
        "workflow", "event_name", "event_schedule", "run_id", "run_attempt",
        "workflow_head_sha",
    }:
        fail("PORTAL_IMPORTER_FIELDS_MISMATCH")
    if importer.get("workflow") != "Import P8-15 Portal Observation":
        fail("PORTAL_IMPORTER_IDENTITY_INVALID")
    if importer.get("event_name") not in {"schedule", "workflow_dispatch"}:
        fail("PORTAL_IMPORTER_EVENT_INVALID")
    _positive_int(importer.get("run_id"), "PORTAL_IMPORTER_RUN_ID_INVALID")
    _positive_int(importer.get("run_attempt"), "PORTAL_IMPORTER_RUN_ATTEMPT_INVALID")
    _full_sha(importer.get("workflow_head_sha"), "PORTAL_IMPORTER_HEAD_INVALID")
    _assert_false_authority(imported.get("authority"), IMPORT_AUTHORITY, "PORTAL_IMPORT_AUTHORITY_INVALID")
    _sha256(imported.get("import_record_sha256"), "PORTAL_IMPORT_RECORD_HASH_INVALID")
    if imported["import_record_sha256"] != payload_sha256(imported, "import_record_sha256"):
        fail("PORTAL_IMPORT_RECORD_HASH_MISMATCH")
    (verifier or verify_attestation)(receipt_path, bundle_path, root_path, receipt)
    return receipt


def iter_imported_receipts(
    portal_root: Path,
    *,
    verifier: Verifier | None = None,
    expected_trusted_root_sha256: str,
) -> list[dict]:
    if not portal_root.exists():
        return []
    packages = sorted(portal_root.glob("*/run-*-attempt-*"))
    stray = [
        path for path in portal_root.rglob("*")
        if path.is_file() and not any(parent in packages for parent in path.parents)
    ]
    if stray:
        fail("UNTRUSTED_PORTAL_RECEIPT_PRESENT", stray[0].as_posix())
    return [
        validate_import_package(
            package,
            portal_root=portal_root,
            verifier=verifier,
            expected_trusted_root_sha256=expected_trusted_root_sha256,
        )
        for package in packages
    ]


def _git_blob(repo_root: Path, commit: str, path: str) -> bytes:
    _full_sha(commit, "PORTAL_SOURCE_COMMIT_INVALID")
    result = subprocess.run(
        ["git", "-C", str(repo_root), "show", f"{commit}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        fail("PORTAL_SOURCE_BLOB_UNAVAILABLE")
    return result.stdout


def _append_package(target: Path, files: dict[str, bytes]) -> bool:
    if target.exists():
        if set(item.name for item in target.iterdir()) == set(files) and all(
            (target / name).read_bytes() == value for name, value in files.items()
        ):
            return False
        fail("PORTAL_IMPORT_APPEND_ONLY_CONFLICT", target.as_posix())
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    temporary.mkdir()
    try:
        for name, value in files.items():
            (temporary / name).write_bytes(value)
        temporary.replace(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return True


def import_receipt(
    *,
    portal_repo_root: Path,
    portal_commit: str,
    source_path: str,
    portal_root: Path,
    importer: dict,
    expected_trusted_root_sha256: str,
) -> tuple[Path, bool]:
    source_path = _safe_path(source_path, "PORTAL_IMPORT_SOURCE_PATH_INVALID")
    receipt_bytes = _git_blob(portal_repo_root, portal_commit, source_path)
    working_path = portal_repo_root / source_path
    if not working_path.is_file() or working_path.read_bytes() != receipt_bytes:
        fail("PORTAL_SOURCE_WORKTREE_MISMATCH")
    receipt = validate_portal_receipt(_load_json_bytes(receipt_bytes, "PORTAL_RECEIPT_UNREADABLE"))
    digest = bytes_sha256(receipt_bytes)
    with tempfile.TemporaryDirectory() as name:
        temporary = Path(name)
        artifact = temporary / "receipt.json"
        artifact.write_bytes(receipt_bytes)
        online = subprocess.run(
            [
                "gh", "attestation", "verify", str(artifact),
                "--repo", PORTAL_REPOSITORY,
                "--signer-workflow", SIGNER_WORKFLOW,
                "--source-digest", receipt["observer"]["workflow_head_sha"],
                "--source-ref", "refs/heads/main",
                "--deny-self-hosted-runners",
                "--no-public-good",
                "--format", "json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
        if online.returncode:
            fail("PORTAL_ATTESTATION_ONLINE_VERIFICATION_FAILED")
        try:
            online_result = json.loads(online.stdout)
        except json.JSONDecodeError as exc:
            raise PortalReceiptError("PORTAL_ATTESTATION_ONLINE_OUTPUT_INVALID") from exc
        if not isinstance(online_result, list) or not online_result:
            fail("PORTAL_ATTESTATION_ONLINE_OUTPUT_INVALID")
        downloaded = subprocess.run(
            ["gh", "attestation", "download", str(artifact), "--repo", PORTAL_REPOSITORY],
            cwd=temporary,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
        if downloaded.returncode:
            fail("PORTAL_ATTESTATION_DOWNLOAD_FAILED")
        bundles = list(temporary.glob(f"sha256*{digest}.jsonl"))
        if len(bundles) != 1:
            fail("PORTAL_ATTESTATION_BUNDLE_AMBIGUOUS")
        bundle_bytes = bundles[0].read_bytes()
        trusted = subprocess.run(
            ["gh", "attestation", "trusted-root"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if trusted.returncode or not trusted.stdout:
            fail("PORTAL_TRUSTED_ROOT_DOWNLOAD_FAILED")
        root_bytes = trusted.stdout
        if bytes_sha256(root_bytes) != expected_trusted_root_sha256:
            fail("PORTAL_TRUSTED_ROOT_NOT_CONTRACT_PINNED")
        bundle = temporary / "attestation.jsonl"
        root = temporary / "trusted_root.jsonl"
        bundle.write_bytes(bundle_bytes)
        root.write_bytes(root_bytes)
        verify_attestation(artifact, bundle, root, receipt)
    imported = {
        "schema_version": "portal_observation_import/1",
        "wbs_item": "P8-15",
        "source_repository": PORTAL_REPOSITORY,
        "source_commit": portal_commit,
        "source_commit_role": "DISCOVERY_COMMIT_NOT_ATTESTATION_IDENTITY",
        "source_path": source_path,
        "receipt_sha256": digest,
        "attestation_bundle_sha256": bytes_sha256(bundle_bytes),
        "trusted_root_sha256": bytes_sha256(root_bytes),
        "attestation_policy": {
            "predicate_type": "https://slsa.dev/provenance/v1",
            "repository": PORTAL_REPOSITORY,
            "signer_workflow": SIGNER_WORKFLOW,
            "source_digest": receipt["observer"]["workflow_head_sha"],
            "source_ref": "refs/heads/main",
            "self_hosted_runners_allowed": False,
            "online_verification_performed": True,
            "offline_bundle_reverification": True,
        },
        "importer": importer,
        "authority": dict(IMPORT_AUTHORITY),
    }
    imported["import_record_sha256"] = payload_sha256(imported, "import_record_sha256")
    target = _package_path(portal_root, receipt)
    files = {
        "receipt.json": receipt_bytes,
        "attestation.jsonl": bundle_bytes,
        "trusted_root.jsonl": root_bytes,
        "import.json": (json.dumps(imported, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    changed = _append_package(target, files)
    validate_import_package(
        target,
        portal_root=portal_root,
        expected_trusted_root_sha256=expected_trusted_root_sha256,
    )
    return target, changed


def _importer_from_args(args) -> dict:
    return {
        "workflow": "Import P8-15 Portal Observation",
        "event_name": args.event_name,
        "event_schedule": args.event_schedule or None,
        "run_id": int(args.run_id),
        "run_attempt": int(args.run_attempt),
        "workflow_head_sha": args.workflow_head_sha,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portal-repo-root", type=Path, required=True)
    parser.add_argument("--portal-commit", required=True)
    parser.add_argument("--portal-root", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-schedule", default="")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--workflow-head-sha", required=True)
    args = parser.parse_args(argv)
    importer = _importer_from_args(args)
    contract = _load_json_bytes(
        (Path(__file__).resolve().parents[1] / "config" / "capital_rotation_e2e_acceptance_contract.json").read_bytes(),
        "ACCEPTANCE_CONTRACT_UNREADABLE",
    )
    expected_trusted_root_sha256 = _sha256(
        contract.get("github_attestation_trusted_root_sha256"),
        "PORTAL_TRUSTED_ROOT_CONTRACT_INVALID",
    )
    source_root = args.portal_repo_root.resolve() / "evidence" / "p8-15" / "portal-observations"
    changed = 0
    if source_root.exists():
        for source in sorted(source_root.glob("*/run-*-attempt-*.json")):
            receipt = validate_portal_receipt(_load_json_bytes(source.read_bytes(), "PORTAL_RECEIPT_UNREADABLE"))
            if receipt["sample_qualification"] != "NATURAL_SCHEDULED_PORTAL_OBSERVATION":
                continue
            _, wrote = import_receipt(
                portal_repo_root=args.portal_repo_root.resolve(),
                portal_commit=args.portal_commit,
                source_path=source.relative_to(args.portal_repo_root.resolve()).as_posix(),
                portal_root=args.portal_root.resolve(),
                importer=importer,
                expected_trusted_root_sha256=expected_trusted_root_sha256,
            )
            changed += int(wrote)
    print(f"portal_import_changed_count={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
