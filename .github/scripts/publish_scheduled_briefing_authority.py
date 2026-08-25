#!/usr/bin/env python3
"""P0-06 append-only bootstrap for scheduled briefing consumers.

The scheduled consumer cannot resolve ``main`` through GitHub's API, but it
can read raw content at an already-known immutable commit.  This adapter
publishes the missing commit pointer at a date-and-slot-specific path that did
not exist before that scheduled slot and may never be overwritten.  A missing,
stale, malformed, or conflicting pointer is therefore a fail-closed result,
never permission to fall back to a floating artifact URL.

Only the bootstrap pointer uses ``main``.  Every read-model artifact URL in the
pointer is pinned to one exact commit and one generation.
"""

from __future__ import annotations

import argparse
from datetime import date as calendar_date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import fetch_briefing_read_model as read_model  # noqa: E402


CONTRACT_PATH = Path("config/scheduled_briefing_retrieval_contract.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ScheduledAuthorityError(RuntimeError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise ScheduledAuthorityError(f"{code}{': ' + detail if detail else ''}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        fail("GIT_READ_FAILED", args[0] if args else "git")
    return result.stdout


def verify_immutable_commit(repo_root: Path, source_commit: str) -> None:
    if not isinstance(source_commit, str) or not FULL_SHA.fullmatch(source_commit):
        fail("SOURCE_COMMIT_NOT_IMMUTABLE")
    resolved = _git(repo_root, "rev-parse", "--verify", f"{source_commit}^{{commit}}")
    if resolved.decode("ascii").strip() != source_commit:
        fail("SOURCE_COMMIT_NOT_IMMUTABLE")


def _safe_repo_path(path: str) -> str:
    if not isinstance(path, str):
        fail("SOURCE_PATH_UNSAFE")
    parsed = PurePosixPath(path)
    if not path or parsed.is_absolute() or ".." in parsed.parts:
        fail("SOURCE_PATH_UNSAFE", path)
    return parsed.as_posix()


def git_blob(repo_root: Path, source_commit: str, path: str) -> bytes:
    verify_immutable_commit(repo_root, source_commit)
    path = _safe_repo_path(path)
    return _git(repo_root, "show", f"{source_commit}:{path}")


def _json_object(raw: bytes, code: str) -> dict:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail(code)
    if not isinstance(value, dict):
        fail(code)
    return value


def _validate_adapter_contract(contract: dict) -> dict:
    expected = {
        "schema_version", "repository", "branch", "source_contract_path",
        "allowed_slots", "delivery_locator_path", "bootstrap_path_template", "bootstrap_url_template",
        "immutable_raw_url_template", "bootstrap_policy", "stale_policy",
        "max_revisions_per_slot", "unavailable_status", "authority",
    }
    if set(contract) != expected:
        fail("ADAPTER_CONTRACT_FIELDS_MISMATCH")
    if contract["schema_version"] != "scheduled_briefing_retrieval_authority/2":
        fail("ADAPTER_CONTRACT_VERSION_UNSUPPORTED")
    if contract["repository"] != "yonggeun1021-hub/atlas-data" or contract["branch"] != "main":
        fail("ADAPTER_REPOSITORY_IDENTITY_MISMATCH")
    if contract["source_contract_path"] != "config/read_model_authority_contract.json":
        fail("ADAPTER_SOURCE_CONTRACT_PATH_MISMATCH")
    if contract["delivery_locator_path"] != "data/briefing/daily_briefing_sources.json":
        fail("ADAPTER_DELIVERY_LOCATOR_PATH_MISMATCH")
    if contract["allowed_slots"] != ["morning", "evening"]:
        fail("ADAPTER_SLOT_CONTRACT_MISMATCH")
    if contract["bootstrap_policy"] != "UNIQUE_DATE_SLOT_APPEND_ONLY_SEQUENTIAL_REVISIONS":
        fail("ADAPTER_BOOTSTRAP_POLICY_MISMATCH")
    if contract["max_revisions_per_slot"] != 99:
        fail("ADAPTER_REVISION_LIMIT_MISMATCH")
    if contract["stale_policy"] != "EXPECTED_DATE_AND_GENERATION_MUST_MATCH_OR_FAIL_CLOSED":
        fail("ADAPTER_STALE_POLICY_MISMATCH")
    if contract["unavailable_status"] != "RETRIEVAL_AUTHORITY_UNAVAILABLE":
        fail("ADAPTER_UNAVAILABLE_STATUS_MISMATCH")
    bootstrap_path = contract["bootstrap_path_template"]
    bootstrap_url = contract["bootstrap_url_template"]
    immutable_url = contract["immutable_raw_url_template"]
    if bootstrap_path != "evidence/scheduled_briefing_retrieval/{expected_kst_date}/{slot}/rev-{revision}.json":
        fail("ADAPTER_BOOTSTRAP_PATH_MISMATCH")
    if bootstrap_url != (
        "https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/main/"
        "evidence/scheduled_briefing_retrieval/{expected_kst_date}/{slot}/rev-{revision}.json"
    ):
        fail("ADAPTER_BOOTSTRAP_URL_MISMATCH")
    if immutable_url != (
        "https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/"
        "{source_commit}/{path}"
    ):
        fail("ADAPTER_IMMUTABLE_URL_MISMATCH")
    expected_authority = {
        "retrieval_pointer_only": True,
        "collector_authority": False,
        "stage_authority": False,
        "buy_authority": False,
        "action_authority": False,
        "order_authority": False,
        "production_authority": False,
        "trading_authority": False,
    }
    if contract.get("authority") != expected_authority:
        fail("ADAPTER_AUTHORITY_BOUNDARY_INVALID")
    return contract


def load_contract_at_commit(repo_root: Path, source_commit: str) -> tuple[dict, dict]:
    adapter = _validate_adapter_contract(
        _json_object(git_blob(repo_root, source_commit, CONTRACT_PATH.as_posix()), "ADAPTER_CONTRACT_JSON_INVALID")
    )
    source_path = adapter["source_contract_path"]
    source_raw = git_blob(repo_root, source_commit, source_path)
    # Reuse P0-05's validator rather than silently forking its authority rules.
    import tempfile
    with tempfile.TemporaryDirectory() as name:
        path = Path(name) / "read_model_authority_contract.json"
        path.write_bytes(source_raw)
        source = read_model.load_contract(path)
    if source["repository"] != adapter["repository"] or source["branch"] != adapter["branch"]:
        fail("ADAPTER_SOURCE_CONTRACT_IDENTITY_MISMATCH")
    return adapter, source


def _artifact_date(path: str, value: dict, required: list[str]) -> str | None:
    if path in required:
        observed = value.get("expected_kst_date")
        return observed if isinstance(observed, str) else None
    source = value.get("source")
    if not isinstance(source, dict):
        return None
    observed = source.get("collected_for_kst_date")
    return observed if isinstance(observed, str) else None


def _artifact_record(adapter: dict, source_commit: str, path: str, raw: bytes) -> dict:
    return {
        "path": path,
        "git_blob_sha1": read_model.git_blob_sha1(raw),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "immutable_url": adapter["immutable_raw_url_template"].format(
            source_commit=source_commit, path=path
        ),
    }


def _delivery_records(
    repo_root: Path,
    adapter: dict,
    source_commit: str,
    slot: str,
    expected_kst_date: str,
) -> tuple[dict, list[dict]]:
    """Bind the H-24 locator and every byte-addressed delivery artifact.

    The locator must already exist in ``source_commit``.  This is why the
    workflow publishes the briefing/locator commit first and the bootstrap in
    a second commit.  Recording a pre-publication checkout SHA would create a
    syntactically valid pointer that cannot deliver H-24 from that commit.
    """
    locator_path = adapter["delivery_locator_path"]
    locator_raw = git_blob(repo_root, source_commit, locator_path)
    locator = _json_object(locator_raw, "DELIVERY_LOCATOR_JSON_INVALID")
    required = {
        "schema_version", "slot", "decision_date", "revision", "index_path",
        "index_sha256", "packet_path", "packet_file_sha256", "packet_sha256",
        "briefing_path", "briefing_sha256", "delivery_scope", "authority",
    }
    if set(locator) != required or locator.get("schema_version") != "daily_briefing_delivery/1":
        fail("DELIVERY_LOCATOR_SCHEMA_INVALID")
    if locator.get("slot") != slot or locator.get("decision_date") != expected_kst_date:
        fail("DELIVERY_LOCATOR_IDENTITY_MISMATCH")
    if locator.get("delivery_scope") != [
        "INVESTMENT_DECISION_REVIEW", "INVESTMENT_REVIEW_SHADOW"
    ]:
        fail("DELIVERY_LOCATOR_SCOPE_MISMATCH")
    if not isinstance(locator.get("packet_sha256"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", locator["packet_sha256"]
    ):
        fail("DELIVERY_PACKET_SHA_INVALID")
    if locator.get("authority") != {
        "stage": False, "buy": False, "action": False, "order": False,
        "production": False, "trading": False,
    }:
        fail("DELIVERY_LOCATOR_AUTHORITY_ESCALATION")
    base = f"evidence/daily_briefing/{slot}/{expected_kst_date}/"
    expected_index_path = f"evidence/daily_briefing/{slot}/{expected_kst_date}/index.json"
    if locator.get("index_path") != expected_index_path:
        fail("DELIVERY_INDEX_PATH_IDENTITY_MISMATCH")
    index_raw = git_blob(repo_root, source_commit, expected_index_path)
    index = _json_object(index_raw, "DELIVERY_INDEX_JSON_INVALID")
    revisions = index.get("revisions")
    latest = index.get("latest_revision")
    if (
        index.get("schema_version") != 1
        or index.get("slot") != slot
        or index.get("decision_date") != expected_kst_date
        or not isinstance(revisions, list)
        or not revisions
        or latest != len(revisions)
        or locator.get("revision") != latest
    ):
        fail("DELIVERY_INDEX_IDENTITY_MISMATCH")
    latest_entry = revisions[-1]
    revision_name = f"rev-{latest:03d}"
    if (
        latest_entry.get("revision") != latest
        or latest_entry.get("path") != revision_name
        or latest_entry.get("packet_sha256") != locator.get("packet_sha256")
        or locator.get("packet_path") != f"evidence/daily_briefing/{slot}/{expected_kst_date}/{revision_name}/packet.json"
        or locator.get("briefing_path") != f"evidence/daily_briefing/{slot}/{expected_kst_date}/{revision_name}/briefing.md"
    ):
        fail("DELIVERY_LATEST_REVISION_IDENTITY_MISMATCH")
    records = [_artifact_record(adapter, source_commit, locator_path, locator_raw)]
    for path_field, hash_field in (
        ("index_path", "index_sha256"),
        ("packet_path", "packet_file_sha256"),
        ("briefing_path", "briefing_sha256"),
    ):
        path = _safe_repo_path(locator.get(path_field))
        if not path.startswith(base):
            fail("DELIVERY_ARTIFACT_PATH_IDENTITY_MISMATCH", path)
        raw = git_blob(repo_root, source_commit, path)
        if hashlib.sha256(raw).hexdigest() != locator.get(hash_field):
            fail("DELIVERY_ARTIFACT_HASH_MISMATCH", path)
        records.append(_artifact_record(adapter, source_commit, path, raw))
    packet = _json_object(
        git_blob(repo_root, source_commit, locator["packet_path"]),
        "DELIVERY_PACKET_JSON_INVALID",
    )
    if (
        packet.get("slot") != slot
        or packet.get("decision_date") != expected_kst_date
        or packet.get("packet_sha256") != locator.get("packet_sha256")
    ):
        fail("DELIVERY_PACKET_IDENTITY_MISMATCH")
    return locator, records


def build_envelope(
    repo_root: Path,
    source_commit: str,
    slot: str,
    expected_kst_date: str,
    revision: int = 1,
) -> dict:
    verify_immutable_commit(repo_root, source_commit)
    if slot not in ("morning", "evening"):
        fail("SLOT_UNSUPPORTED", slot)
    if not ISO_DATE.fullmatch(expected_kst_date):
        fail("EXPECTED_KST_DATE_INVALID")
    try:
        calendar_date.fromisoformat(expected_kst_date)
    except ValueError:
        fail("EXPECTED_KST_DATE_INVALID")
    adapter, source = load_contract_at_commit(repo_root, source_commit)
    if slot not in adapter["allowed_slots"]:
        fail("SLOT_UNSUPPORTED", slot)
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or not 1 <= revision <= adapter["max_revisions_per_slot"]
    ):
        fail("REVISION_INVALID")
    revision_text = f"{revision:03d}"

    parsed: dict[str, dict] = {}
    records = []
    for path in source["required_artifacts"]:
        raw = git_blob(repo_root, source_commit, path)
        value = _json_object(raw, "SOURCE_ARTIFACT_JSON_INVALID")
        if _artifact_date(path, value, source["required_artifacts"]) != expected_kst_date:
            fail("SOURCE_ARTIFACT_STALE_DATE", path)
        parsed[path] = value
        records.append(_artifact_record(adapter, source_commit, path, raw))

    step_path, health_path = source["required_artifacts"]
    step_generation = parsed[step_path].get("generation")
    health_generation = parsed[health_path].get("generation")
    if not isinstance(step_generation, dict) or not isinstance(health_generation, dict):
        fail("SOURCE_GENERATION_METADATA_MISSING")
    generation_id = step_generation.get("generation_id")
    if not isinstance(generation_id, str) or not re.fullmatch(r"[0-9a-f]{64}", generation_id):
        fail("SOURCE_GENERATION_ID_INVALID")
    if health_generation.get("generation_id") != generation_id:
        fail("SOURCE_MIXED_GENERATION_READ")

    bootstrap_path = adapter["bootstrap_path_template"].format(
        expected_kst_date=expected_kst_date, slot=slot, revision=revision_text
    )
    bootstrap_url = adapter["bootstrap_url_template"].format(
        expected_kst_date=expected_kst_date, slot=slot, revision=revision_text
    )
    compact_urls = {
        market: adapter["immutable_raw_url_template"].format(
            source_commit=source_commit, path=template
        )
        for market, template in sorted(source["compact_path_templates"].items())
    }
    delivery_locator, delivery_records = _delivery_records(
        repo_root, adapter, source_commit, slot, expected_kst_date
    )
    return {
        "schema_version": adapter["schema_version"],
        "slot": slot,
        "expected_kst_date": expected_kst_date,
        "revision": revision,
        "source_commit": source_commit,
        "generation_id": generation_id,
        "bootstrap_path": bootstrap_path,
        "bootstrap_url": bootstrap_url,
        "bootstrap_policy": adapter["bootstrap_policy"],
        "stale_detection": "PASS",
        "required_artifacts": records,
        "delivery_locator": delivery_locator,
        "delivery_artifacts": delivery_records,
        "compact_immutable_url_templates": compact_urls,
        "consumer_rules": {
            "bootstrap_missing_or_invalid": adapter["unavailable_status"],
            "expected_date_mismatch": adapter["unavailable_status"],
            "generation_mismatch": adapter["unavailable_status"],
            "bootstrap_query_nonce_required": True,
            "floating_artifact_fallback_allowed": False,
            "prior_date_fallback_allowed": False,
            "revision_discovery": "ASCENDING_FROM_001_STOP_AT_FIRST_MISSING_USE_HIGHEST_VALID",
        },
        "authority": adapter["authority"],
    }


def validate_envelope(repo_root: Path, envelope: dict) -> None:
    if not isinstance(envelope, dict):
        fail("ENVELOPE_NOT_OBJECT")
    required = {
        "schema_version", "slot", "expected_kst_date", "revision", "source_commit",
        "generation_id", "bootstrap_path", "bootstrap_url", "bootstrap_policy",
        "stale_detection", "required_artifacts", "compact_immutable_url_templates",
        "delivery_locator", "delivery_artifacts", "consumer_rules", "authority",
    }
    if set(envelope) != required:
        fail("ENVELOPE_FIELDS_MISMATCH")
    rebuilt = build_envelope(
        repo_root,
        envelope.get("source_commit"),
        envelope.get("slot"),
        envelope.get("expected_kst_date"),
        envelope.get("revision"),
    )
    if envelope != rebuilt:
        fail("ENVELOPE_DRIFT_OR_TAMPER")


def validate_expected_identity(
    repo_root: Path,
    envelope: dict,
    path: Path,
    source_commit: str,
    slot: str,
    expected_kst_date: str,
) -> None:
    if (
        envelope.get("source_commit") != source_commit
        or envelope.get("slot") != slot
        or envelope.get("expected_kst_date") != expected_kst_date
    ):
        fail("ENVELOPE_EXPECTED_IDENTITY_MISMATCH")
    expected_path = (repo_root / envelope.get("bootstrap_path", "")).resolve()
    if path.resolve() != expected_path:
        fail("ENVELOPE_PATH_IDENTITY_MISMATCH")


def publish(repo_root: Path, source_commit: str, slot: str, expected_kst_date: str) -> tuple[Path, bool]:
    adapter, _ = load_contract_at_commit(repo_root, source_commit)
    base = repo_root / "evidence/scheduled_briefing_retrieval" / expected_kst_date / slot
    existing_paths = sorted(base.glob("rev-*.json")) if base.exists() else []
    expected_names = [f"rev-{number:03d}.json" for number in range(1, len(existing_paths) + 1)]
    if [path.name for path in existing_paths] != expected_names:
        fail("BOOTSTRAP_REVISION_SEQUENCE_INVALID")
    prior = []
    for path in existing_paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fail("BOOTSTRAP_EXISTING_UNREADABLE")
        validate_envelope(repo_root, value)
        prior.append(value)
    if prior:
        latest = prior[-1]
        candidate = build_envelope(
            repo_root, source_commit, slot, expected_kst_date, latest["revision"]
        )
        if latest["generation_id"] == candidate["generation_id"]:
            old_fingerprints = [
                (row["path"], row["git_blob_sha1"], row["content_sha256"])
                for row in latest["required_artifacts"]
            ]
            new_fingerprints = [
                (row["path"], row["git_blob_sha1"], row["content_sha256"])
                for row in candidate["required_artifacts"]
            ]
            if old_fingerprints != new_fingerprints:
                fail("SOURCE_GENERATION_REUSED_WITH_DIFFERENT_BYTES")
            return existing_paths[-1], False
    revision = len(existing_paths) + 1
    if revision > adapter["max_revisions_per_slot"]:
        fail("BOOTSTRAP_REVISION_LIMIT_EXCEEDED")
    envelope = build_envelope(repo_root, source_commit, slot, expected_kst_date, revision)
    target = repo_root / envelope["bootstrap_path"]
    rendered = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        fail("BOOTSTRAP_SLOT_ALREADY_BOUND")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(rendered, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target, True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("publish", "validate"))
    parser.add_argument("--slot", required=True, choices=("morning", "evening"))
    parser.add_argument("--expected-kst-date", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--authority-path", type=Path)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    if args.command == "publish":
        path, changed = publish(
            args.repo_root, args.source_commit, args.slot, args.expected_kst_date
        )
        print(f"authority_path={path.relative_to(args.repo_root).as_posix()}")
        print(f"authority_changed={'true' if changed else 'false'}")
        return 0
    if args.authority_path is None:
        fail("AUTHORITY_PATH_REQUIRED_FOR_VALIDATE")
    path = args.authority_path
    if not path.is_absolute():
        path = args.repo_root / path
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("BOOTSTRAP_UNREADABLE")
    validate_envelope(args.repo_root, envelope)
    validate_expected_identity(
        args.repo_root,
        envelope,
        path,
        args.source_commit,
        args.slot,
        args.expected_kst_date,
    )
    print(json.dumps({
        "status": "PASS",
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
        "expected_kst_date": envelope["expected_kst_date"],
        "slot": envelope["slot"],
        "revision": envelope["revision"],
        "stale_detection": envelope["stale_detection"],
        "authority": envelope["authority"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
