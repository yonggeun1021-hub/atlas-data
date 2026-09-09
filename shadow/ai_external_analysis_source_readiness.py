#!/usr/bin/env python3
"""Reference-only DART/SEC readiness adapter for AI analysis Shadow.

The adapter reads immutable Git objects with the existing source-owner
validators. It records hashes and identities, never source text. Missing event
time, freshness policy, and consecutive history remain explicit UNKNOWNs and
therefore cannot become an input to a model executor.
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "ai_external_analysis_source_readiness_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


class SourceReadinessError(ValueError):
    pass


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SEC = _load("atlas_ai_shadow_sec_content", "collectors/sec_filing_content.py")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceReadinessError(f"JSON_READ_FAILED:{path}") from exc
    if not isinstance(value, dict):
        raise SourceReadinessError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "ai_external_analysis_source_readiness/1",
        "latest_sources": {
            "DART": {
                "content_run": "data/latest_dart_content.json",
                "metadata": "data/latest_dart.json",
            },
            "SEC": {
                "content_run": "data/latest_sec_content.json",
                "metadata": "data/latest_sec.json",
            },
        },
        "owner_paths": [
            ".github/workflows/collect.yml",
            "bridge/tsm_sec_monthly_rule_evidence.py",
            "collectors/dart.py",
            "collectors/dart_filing_content.py",
            "collectors/sec.py",
            "collectors/sec_filing_content.py",
            "config/dart_filing_content_contract.json",
            "config/sec_filing_content_contract.json",
            "data/observations/rule_evidence_bindings/2026-08-20/tsm-sec-monthly-rule-evidence-e24e8e6b047312b5.json",
            "discovery/dart_event_observation.py",
        ],
        "time_semantics": {
            "available_at": "EXISTING_RETRIEVED_AT_UTC",
            "event_at_when_only_filing_date_exists": None,
            "event_time_precision_when_only_filing_date_exists": "DATE_ONLY",
            "fresh_through": None,
            "freshness_status": "UNKNOWN_NO_SOURCE_OWNER_POLICY",
        },
        "condition_order": [
            "latest_actual_source",
            "market_symbol_identity",
            "event_available_fresh_through",
            "source_owner_binding",
            "original_or_approved_excerpt_hash",
            "continuous_missing_delay_state",
        ],
        "authority": {
            "reference_recording_authorized": True,
            "source_interpretation_authorized": False,
            "model_source_inference_authorized": False,
            "score_change_authorized": False,
            "selection_change_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def validate_contract(value: object) -> dict:
    if value != _expected_contract():
        raise SourceReadinessError("CONTRACT_TAMPER_OR_DRIFT")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(path))


def _utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise SourceReadinessError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceReadinessError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise SourceReadinessError(code)
    return parsed.astimezone(dt.timezone.utc)


def _run_git(repo: Path, args: list[str], *, binary: bool = False):
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            stderr=subprocess.PIPE,
            text=not binary,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace") if binary else exc.stderr
        raise SourceReadinessError(f"GIT_READ_FAILED:{detail.strip()}") from exc


def _validate_commit(repo: Path, source_commit: str) -> str:
    if not isinstance(source_commit, str) or COMMIT_RE.fullmatch(source_commit) is None:
        raise SourceReadinessError("SOURCE_COMMIT_MUST_BE_FULL_SHA")
    resolved = _run_git(repo, ["rev-parse", "--verify", f"{source_commit}^{{commit}}"])
    if resolved.strip() != source_commit:
        raise SourceReadinessError("SOURCE_COMMIT_RESOLUTION_MISMATCH")
    return source_commit


def _git_blob(repo: Path, commit: str, relative: str) -> bytes:
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise SourceReadinessError("SOURCE_PATH_INVALID")
    return _run_git(repo, ["show", f"{commit}:{relative}"], binary=True)


def _git_json(repo: Path, commit: str, relative: str) -> tuple[dict, bytes]:
    blob = _git_blob(repo, commit, relative)
    try:
        value = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceReadinessError(f"SOURCE_JSON_INVALID:{relative}") from exc
    if not isinstance(value, dict):
        raise SourceReadinessError(f"SOURCE_JSON_OBJECT_REQUIRED:{relative}")
    return value, blob


def _sha_ref(repo: Path, commit: str, relative: str) -> dict:
    blob = _git_blob(repo, commit, relative)
    return {
        "path": relative,
        "blobSha256": hashlib.sha256(blob).hexdigest(),
        "sourceCommit": commit,
    }


def _validated_counts(run: dict, source_name: str) -> dict:
    records = run.get("records")
    counts = run.get("counts")
    if not isinstance(records, list) or not isinstance(counts, dict):
        raise SourceReadinessError(f"{source_name}_RUN_SHAPE_INVALID")
    expected = {
        "captured": sum(row.get("operation") == "captured" for row in records),
        "failed": sum(row.get("operation") == "failed" for row in records),
        "not_applicable": sum(row.get("content_status") == "NOT_APPLICABLE" for row in records),
        "skipped": sum(row.get("operation") == "skipped" for row in records),
    }
    if counts != expected:
        raise SourceReadinessError(f"{source_name}_RUN_COUNTS_INVALID")
    if run.get("run_status") not in {"OK", "DEGRADED", "FAILED"}:
        raise SourceReadinessError(f"{source_name}_RUN_STATUS_INVALID")
    if (run["run_status"] == "OK") != (counts["failed"] == 0):
        raise SourceReadinessError(f"{source_name}_RUN_STATUS_COUNT_MISMATCH")
    return copy.deepcopy(counts)


def _run_state(source_name: str, run: dict, retained_count: int) -> dict:
    counts = _validated_counts(run, source_name)
    failed = counts["failed"]
    if failed:
        new_content = "PARTIAL_OR_FAILED_COLLECTION"
        collection = "FAILED_IN_CURRENT_RUN"
    elif counts["captured"]:
        new_content = "NEW_CONTENT_CAPTURED"
        collection = "CURRENT_RUN_OK"
    elif retained_count:
        new_content = "NO_NEW_DOWNLOAD_RETAINED_CONTENT_REUSED"
        collection = "CURRENT_RUN_OK"
    else:
        new_content = "NO_NEW_RELEVANT_FILING"
        collection = "CURRENT_RUN_OK"
    return {
        "newContentState": new_content,
        "collectionFailureState": collection,
        "missingState": (
            "NOT_MISSING_RETAINED_REFERENCE_PRESENT"
            if retained_count
            else "NOT_MISSING_SUCCESSFUL_EMPTY_RESULT"
            if not failed
            else "UNKNOWN_COLLECTION_FAILED"
        ),
        "stalenessState": "UNKNOWN_NO_SOURCE_OWNER_POLICY",
        "consecutiveFailureState": "UNKNOWN_HISTORY_NOT_BOUND",
        "consecutiveFailureCount": None,
    }


def _fact_refs(manifest: dict) -> list[dict]:
    result = []
    for index, fact in enumerate(manifest.get("extracted", [])):
        if not isinstance(fact, dict):
            raise SourceReadinessError("EXTRACTED_FACT_INVALID")
        fact_hash = payload_sha256(fact)
        result.append({
            "factId": f"SEC_EXTRACTED_FACT_SHA256_{fact_hash}",
            "factSha256": fact_hash,
            "extractorVersion": manifest["extractor_version"],
            "index": index,
            "label": fact.get("label"),
        })
    return result


def _sec_sources(repo: Path, commit: str, run: dict, evaluated: dt.datetime) -> list[dict]:
    result = []
    for row in run["records"]:
        if not (
            row.get("operation") == "skipped"
            and row.get("skip_reason") == "already_captured"
            and row.get("content_status") == "OK"
        ):
            continue
        ticker = row.get("ticker")
        identity = row.get("filing_identity")
        if (
            not isinstance(ticker, str)
            or TICKER_RE.fullmatch(ticker) is None
            or not isinstance(identity, dict)
            or ACCESSION_RE.fullmatch(str(identity.get("accession"))) is None
            or re.fullmatch(r"^\d{10}$", str(identity.get("cik"))) is None
        ):
            raise SourceReadinessError("SEC_RETAINED_IDENTITY_INVALID")
        relative = f"data/sec_content/{ticker}/{identity['accession']}/_manifest.json"
        manifest, manifest_blob = _git_json(repo, commit, relative)
        if manifest != row:
            raise SourceReadinessError(f"SEC_LATEST_MANIFEST_MISMATCH:{ticker}:{identity['accession']}")
        raw_by_name = {}
        document_refs = []
        for document in manifest["documents"]:
            name = document["document_name"]
            raw_path = f"data/sec_content/{ticker}/{identity['accession']}/{name}.gz"
            try:
                raw = gzip.decompress(_git_blob(repo, commit, raw_path))
            except (gzip.BadGzipFile, OSError) as exc:
                raise SourceReadinessError(f"SEC_RAW_GZIP_INVALID:{raw_path}") from exc
            if hashlib.sha256(raw).hexdigest() != document["content_sha256"]:
                raise SourceReadinessError(f"SEC_RAW_HASH_MISMATCH:{raw_path}")
            raw_by_name[name] = raw
            document_refs.append({
                "path": raw_path,
                "contentSha256": document["content_sha256"],
                "kind": document["kind"],
            })
        try:
            SEC.validate_manifest(manifest, raw_by_name, SEC.load_contract())
        except SEC.SecContentError as exc:
            raise SourceReadinessError(f"SEC_OWNER_VALIDATION_FAILED:{exc}") from exc
        available = _utc(manifest.get("retrieved_at_utc"), "SEC_RETRIEVED_AT_INVALID")
        if available > evaluated:
            raise SourceReadinessError("SEC_SOURCE_FROM_FUTURE")
        result.append({
            "sourceId": f"SEC_{identity['cik']}_{identity['accession']}",
            "sourceType": "US_SEC_EDGAR_FILING",
            "manifestPath": relative,
            "manifestSha256": hashlib.sha256(manifest_blob).hexdigest(),
            "identity": {
                "market": "US",
                "symbol": ticker,
                "cik": identity["cik"],
                "accession": identity["accession"],
            },
            "eventDate": manifest["filing_date"],
            "eventAtUtc": None,
            "eventTimePrecision": "DATE_ONLY",
            "availableAtUtc": manifest["retrieved_at_utc"],
            "freshThroughUtc": None,
            "freshnessStatus": "UNKNOWN_NO_SOURCE_OWNER_POLICY",
            "documentRefs": document_refs,
            "factRefs": _fact_refs(manifest),
        })
    return result


def _derive_packet(
    repo: Path,
    source_commit: str,
    evaluated_at_utc: str,
    contract: dict,
) -> dict:
    repo = Path(repo).resolve()
    commit = _validate_commit(repo, source_commit)
    evaluated = _utc(evaluated_at_utc, "EVALUATED_AT_INVALID")
    latest = {}
    for source_name, paths in contract["latest_sources"].items():
        run, run_blob = _git_json(repo, commit, paths["content_run"])
        metadata_blob = _git_blob(repo, commit, paths["metadata"])
        if run.get("source_sha256") != hashlib.sha256(metadata_blob).hexdigest():
            raise SourceReadinessError(f"{source_name}_LATEST_SOURCE_HASH_MISMATCH")
        observed = _utc(run.get("observed_at_utc"), f"{source_name}_OBSERVED_AT_INVALID")
        if observed > evaluated:
            raise SourceReadinessError(f"{source_name}_RUN_FROM_FUTURE")
        _validated_counts(run, source_name)
        latest[source_name] = (run, run_blob, paths)

    sec_sources = _sec_sources(repo, commit, latest["SEC"][0], evaluated)
    run_states = {}
    latest_refs = []
    for source_name in ("DART", "SEC"):
        run, run_blob, paths = latest[source_name]
        retained_count = len(sec_sources) if source_name == "SEC" else 0
        run_states[source_name] = _run_state(source_name, run, retained_count)
        latest_refs.append({
            "source": source_name,
            "contentRunPath": paths["content_run"],
            "contentRunSha256": hashlib.sha256(run_blob).hexdigest(),
            "metadataPath": paths["metadata"],
            "metadataSha256": run["source_sha256"],
            "observedAtUtc": run["observed_at_utc"],
            "runStatus": run["run_status"],
            "counts": copy.deepcopy(run["counts"]),
        })

    owner_refs = [_sha_ref(repo, commit, path) for path in contract["owner_paths"]]
    conditions = [
        {"id": "latest_actual_source", "ready": False, "status": "RETAINED_SOURCE_PRESENT_SHADOW_FRESHNESS_UNBOUND"},
        {"id": "market_symbol_identity", "ready": bool(sec_sources), "status": "READY_REFERENCE_IDENTITY" if sec_sources else "UNKNOWN_NO_RETAINED_SOURCE"},
        {"id": "event_available_fresh_through", "ready": False, "status": "UNKNOWN_EVENT_TIME_AND_FRESH_THROUGH_POLICY"},
        {"id": "source_owner_binding", "ready": True, "status": "READY_REFERENCE_ONLY_OWNER_BINDING"},
        {"id": "original_or_approved_excerpt_hash", "ready": bool(sec_sources), "status": "READY_OWNER_VALIDATED_RETAINED_BYTES" if sec_sources else "UNKNOWN_NO_RETAINED_SOURCE"},
        {"id": "continuous_missing_delay_state", "ready": False, "status": "UNKNOWN_CONSECUTIVE_HISTORY_NOT_BOUND"},
    ]
    if [row["id"] for row in conditions] != contract["condition_order"]:
        raise SourceReadinessError("CONDITION_ORDER_INTERNAL_ERROR")
    all_ready = all(row["ready"] for row in conditions)
    packet = {
        "schemaVersion": "ai_external_analysis_source_readiness_packet/1",
        "contractVersion": contract["contract_version"],
        "sourceCommit": commit,
        "evaluatedAtUtc": evaluated_at_utc,
        "status": "READY" if all_ready else "DATA_QUALIFICATION_WAIT",
        "latestSourceRefs": latest_refs,
        "retainedSourceRefs": sec_sources,
        "ownerRefs": owner_refs,
        "runStates": run_states,
        "conditions": conditions,
        "allSixConditionsReady": all_ready,
        "stage3InputSources": [],
        "modelSourceInferenceAuthorized": False,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packetSha256"] = payload_sha256(packet)
    return packet


def build_packet(
    repo: Path,
    source_commit: str,
    evaluated_at_utc: str,
    contract: dict | None = None,
) -> dict:
    checked_contract = validate_contract(contract) if contract is not None else load_contract()
    packet = _derive_packet(repo, source_commit, evaluated_at_utc, checked_contract)
    return validate_packet(packet, repo, source_commit, evaluated_at_utc, checked_contract)


def validate_packet(
    packet: object,
    repo: Path,
    source_commit: str,
    evaluated_at_utc: str,
    contract: dict | None = None,
) -> dict:
    checked_contract = validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(packet, dict) or set(packet) != {
        "schemaVersion", "contractVersion", "sourceCommit", "evaluatedAtUtc", "status",
        "latestSourceRefs", "retainedSourceRefs", "ownerRefs", "runStates", "conditions",
        "allSixConditionsReady", "stage3InputSources", "modelSourceInferenceAuthorized",
        "authority", "packetSha256",
    }:
        raise SourceReadinessError("PACKET_FIELDS_INVALID")
    claimed = packet.get("packetSha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packetSha256")
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise SourceReadinessError("PACKET_SHA256_INVALID")
    if payload_sha256(unsigned) != claimed:
        raise SourceReadinessError("PACKET_SHA256_MISMATCH")
    rebuilt = _derive_packet(repo, source_commit, evaluated_at_utc, checked_contract)
    if packet != rebuilt:
        raise SourceReadinessError("PACKET_SEMANTIC_TAMPER_OR_DRIFT")
    return copy.deepcopy(packet)
