#!/usr/bin/env python3
"""P2-01 independent authority boundary for Theme Taxonomy graphs.

The graph document may claim that it was ratified, but that claim is never
authority.  This module accepts authority only from a committed registry row
whose complete determining payload is bound to a separate committed approval
evidence file.  Both files must match the exact bytes at a trusted immutable
git commit, and PIT usability begins no earlier than every relevant clock.

The repository registry is intentionally empty.  This is the mechanism, not a
taxonomy decision and not a source of inferred memberships or trading power.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "theme_taxonomy_authority_registry.json"
REGISTRY_SCHEMA = "theme_taxonomy_authority_registry/1"
EVIDENCE_SCHEMA = "theme_taxonomy_approval_evidence/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")

AUTHORITY_FALSE = {
    "theme_membership_activation_authorized": False,
    "theme_inference_authorized": False,
    "membership_inference_authorized": False,
    "membership_weight_authorized": False,
    "rotation_score_authorized": False,
    "candidate_ranking_authorized": False,
    "stage_promotion_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class ThemeTaxonomyAuthorityError(ValueError):
    """Fail-closed registry or provenance contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def payload_sha256(value) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def graph_payload(graph: dict) -> dict:
    """Return only the graph bytes a taxonomy approval must determine."""
    nodes = sorted(
        json.loads(json.dumps(graph.get("nodes"))),
        key=lambda item: item.get("theme_id", "") if isinstance(item, dict) else "",
    ) if isinstance(graph.get("nodes"), list) else graph.get("nodes")
    edges = sorted(
        json.loads(json.dumps(graph.get("edges"))),
        key=lambda item: item.get("edge_id", "") if isinstance(item, dict) else "",
    ) if isinstance(graph.get("edges"), list) else graph.get("edges")
    memberships = json.loads(json.dumps(graph.get("memberships")))
    if isinstance(memberships, list):
        for item in memberships:
            if isinstance(item, dict) and isinstance(item.get("evidence"), list):
                item["evidence"] = sorted(
                    item["evidence"],
                    key=lambda evidence: evidence.get("evidence_id", "")
                    if isinstance(evidence, dict) else "",
                )
        memberships = sorted(
            memberships,
            key=lambda item: item.get("membership_id", "") if isinstance(item, dict) else "",
        )
    return {
        "schema_version": graph.get("schema_version"),
        "taxonomy_id": graph.get("taxonomy_id"),
        "nodes": nodes,
        "edges": edges,
        "memberships": memberships,
    }


def determining_payload(record: dict) -> dict:
    fields = (
        "rule_id", "rule_version", "approval_status", "ratified_at",
        "effective_from", "effective_to", "taxonomy_id",
        "approved_graph_payload_sha256",
    )
    return {field: record.get(field) for field in fields}


def _utc(value: str, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ThemeTaxonomyAuthorityError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ThemeTaxonomyAuthorityError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ThemeTaxonomyAuthorityError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ThemeTaxonomyAuthorityError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ThemeTaxonomyAuthorityError(code)
    return value


def validate_record(record: dict) -> dict:
    fields = {
        "rule_id", "rule_version", "approval_status", "ratified_at",
        "effective_from", "effective_to", "taxonomy_id",
        "approved_graph_payload_sha256", "approval_evidence_ref",
        "approval_evidence_sha256",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise ThemeTaxonomyAuthorityError("AUTHORITY_RECORD_FIELDS_MISMATCH")
    _token(record["rule_id"], "RULE_ID_INVALID")
    _token(record["rule_version"], "RULE_VERSION_INVALID")
    _token(record["taxonomy_id"], "TAXONOMY_ID_INVALID")
    if record["approval_status"] not in {"PROPOSED", "RATIFIED", "REVOKED"}:
        raise ThemeTaxonomyAuthorityError("APPROVAL_STATUS_INVALID")
    ratified = _utc(record["ratified_at"], "RATIFIED_AT_INVALID")
    start = _utc(record["effective_from"], "EFFECTIVE_FROM_INVALID")
    end_value = record["effective_to"]
    end = None if end_value is None else _utc(end_value, "EFFECTIVE_TO_INVALID")
    if end is not None and end <= start:
        raise ThemeTaxonomyAuthorityError("EFFECTIVE_INTERVAL_INVALID")
    if record["approval_status"] == "RATIFIED" and ratified < start:
        # Ratification may precede effectivity.  The inverse is also allowed;
        # real_usable_from takes the maximum.  This branch is intentionally a
        # no-op and documents that neither order is treated as backdating.
        pass
    _sha(record["approved_graph_payload_sha256"], "GRAPH_PAYLOAD_SHA256_INVALID")
    ref = record["approval_evidence_ref"]
    if not isinstance(ref, str) or not ref or ref.startswith("/") or ".." in Path(ref).parts:
        raise ThemeTaxonomyAuthorityError("APPROVAL_EVIDENCE_REF_INVALID")
    _sha(record["approval_evidence_sha256"], "APPROVAL_EVIDENCE_SHA256_INVALID")
    return json.loads(json.dumps(record))


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    path = Path(path).resolve()
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ThemeTaxonomyAuthorityError("REGISTRY_READ_FAILED") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "records"}:
        raise ThemeTaxonomyAuthorityError("REGISTRY_FIELDS_MISMATCH")
    if value["schema_version"] != REGISTRY_SCHEMA or not isinstance(value["records"], list):
        raise ThemeTaxonomyAuthorityError("REGISTRY_SCHEMA_MISMATCH")
    records = [validate_record(record) for record in value["records"]]
    keys = [(r["rule_id"], r["rule_version"]) for r in records]
    if len(keys) != len(set(keys)):
        raise ThemeTaxonomyAuthorityError("AUTHORITY_RECORD_DUPLICATE")
    value["records"] = records
    value["_source_path"] = str(path)
    return value


def _run_git(repo: Path, *args: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def _repo_root(path: Path) -> Path | None:
    value = _run_git(path.parent, "rev-parse", "--show-toplevel")
    return Path(value).resolve() if value else None


def _relative(repo: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return None


def _trusted_commit(repo: Path, supplied: str | None) -> str | None:
    if supplied is None:
        return _run_git(repo, "rev-parse", "HEAD")
    if FULL_SHA_RE.fullmatch(supplied) is None:
        return None
    resolved = _run_git(repo, "rev-parse", "--verify", f"{supplied}^{{commit}}")
    return supplied if resolved == supplied else None


def _git_blob(repo: Path, commit: str, rel: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def _commits(repo: Path, commit: str, rel: str) -> list[str]:
    out = _run_git(repo, "log", "--format=%H", "--reverse", commit, "--", rel)
    return [] if not out else out.splitlines()


def _commit_time(repo: Path, commit: str) -> str | None:
    value = _run_git(repo, "show", "-s", "--format=%cI", commit)
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_seen_exact_bytes(repo: Path, commit: str, rel: str, expected: bytes) -> str | None:
    for candidate in _commits(repo, commit, rel):
        if _git_blob(repo, candidate, rel) == expected:
            return _commit_time(repo, candidate)
    return None


def _first_seen_record(repo: Path, commit: str, rel: str, expected: dict) -> str | None:
    for candidate in _commits(repo, commit, rel):
        blob = _git_blob(repo, candidate, rel)
        if blob is None:
            continue
        try:
            document = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        for row in document.get("records", []) if isinstance(document, dict) else []:
            if isinstance(row, dict) and determining_payload(row) == expected:
                return _commit_time(repo, candidate)
    return None


def _public_result(status: str, **diagnostics) -> dict:
    result = {"status": status, "authority": dict(AUTHORITY_FALSE)}
    result.update(diagnostics)
    return result


def resolve_graph_authority(
    graph: dict,
    as_of_date: str,
    registry_path: Path = REGISTRY_PATH,
    trusted_commit: str | None = None,
) -> dict:
    """Resolve a graph authority without trusting any claim inside `graph`."""
    try:
        decision_day = dt.date.fromisoformat(as_of_date)
        if decision_day.isoformat() != as_of_date:
            raise ValueError
    except (TypeError, ValueError):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_AS_OF_DATE_INVALID")
    try:
        registry = load_registry(registry_path)
    except ThemeTaxonomyAuthorityError:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_REGISTRY_INVALID")
    path = Path(registry["_source_path"])
    repo = _repo_root(path)
    if repo is None:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")
    commit = _trusted_commit(repo, trusted_commit)
    rel = _relative(repo, path)
    if commit is None or rel is None:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")
    try:
        disk = path.read_bytes()
    except OSError:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")
    if _git_blob(repo, commit, rel) != disk:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_TAMPERED")
    if trusted_commit is None:
        dirty = _run_git(repo, "status", "--porcelain", "--", rel)
        if dirty:
            return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")

    graph_hash = payload_sha256(graph_payload(graph))
    matching = [
        row for row in registry["records"]
        if row["taxonomy_id"] == graph.get("taxonomy_id")
        and row["approved_graph_payload_sha256"] == graph_hash
    ]
    if not matching:
        return _public_result(
            "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD",
            approved_graph_payload_sha256=graph_hash,
        )
    if len(matching) != 1:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_AMBIGUOUS_AUTHORITY_RECORD")
    row = matching[0]
    if row["approval_status"] != "RATIFIED":
        return _public_result("AUTHORITY_NOT_COMPUTABLE_UNRATIFIED_RECORD")

    evidence_path = (repo / row["approval_evidence_ref"]).resolve()
    try:
        evidence_path.relative_to(repo)
        evidence_bytes = evidence_path.read_bytes()
    except (ValueError, OSError):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")
    evidence_rel = _relative(repo, evidence_path)
    if (
        evidence_rel is None
        or sha256_bytes(evidence_bytes) != row["approval_evidence_sha256"]
        or _git_blob(repo, commit, evidence_rel) != evidence_bytes
    ):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")
    try:
        evidence = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")
    expected_evidence = {
        "schema_version": EVIDENCE_SCHEMA,
        "approved_full_payload_sha256": payload_sha256(determining_payload(row)),
        **determining_payload(row),
    }
    if evidence != expected_evidence:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")

    row_first_seen = _first_seen_record(repo, commit, rel, determining_payload(row))
    evidence_first_seen = _first_seen_exact_bytes(repo, commit, evidence_rel, evidence_bytes)
    if row_first_seen is None or evidence_first_seen is None:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED")
    try:
        usable = max(
            _utc(row["effective_from"], "EFFECTIVE_FROM_INVALID"),
            _utc(row["ratified_at"], "RATIFIED_AT_INVALID"),
            _utc(row_first_seen, "ROW_FIRST_SEEN_INVALID"),
            _utc(evidence_first_seen, "EVIDENCE_FIRST_SEEN_INVALID"),
        )
        effective_to = (
            None if row["effective_to"] is None
            else _utc(row["effective_to"], "EFFECTIVE_TO_INVALID")
        )
    except ThemeTaxonomyAuthorityError:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_TIME_INVALID")
    if usable.date() == decision_day:
        return _public_result(
            "AUTHORITY_NOT_COMPUTABLE_DATE_ONLY_PRECISION",
            real_usable_from=usable.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    decision_at = dt.datetime.combine(decision_day, dt.time.min, tzinfo=dt.timezone.utc)
    if usable > decision_at:
        return _public_result(
            "AUTHORITY_NOT_COMPUTABLE_PIT_VIOLATION",
            real_usable_from=usable.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    if effective_to is not None and decision_at >= effective_to:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_NO_ACTIVE_AUTHORITY_RECORD")

    authority = dict(AUTHORITY_FALSE)
    authority["theme_membership_activation_authorized"] = True
    return {
        "status": "AUTHORIZED",
        "authority": authority,
        "rule_id": row["rule_id"],
        "rule_version": row["rule_version"],
        "approved_graph_payload_sha256": graph_hash,
        "real_usable_from": usable.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trusted_commit": commit,
    }
