#!/usr/bin/env python3
"""Reference-only DART/SEC readiness adapter for AI analysis Shadow.

The adapter reads immutable Git objects with the existing source-owner
validators. It records hashes and identities, never source text. Missing event
time, freshness policy, and consecutive history remain explicit UNKNOWNs and
therefore cannot become an input to a model executor.
"""
from __future__ import annotations

import argparse
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
import tempfile


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
DART = _load("atlas_ai_shadow_dart_content", "collectors/dart_filing_content.py")


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
        "run_ledger": {
            "max_records": 32,
            "statuses": ["OK_NEW", "OK_EMPTY", "FAILED", "DELAYED_OR_SKIPPED"],
        },
        "admission_authority": {
            "source_observation_only": True,
            "interpretation_authorized": False,
            "model_source_inference_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
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
    try:
        exact = canonical_json(value) == canonical_json(_expected_contract())
    except (TypeError, ValueError):
        exact = False
    if not exact:
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


def _git_blob_present(repo: Path, commit: str, relative: str) -> bool:
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{relative}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    return completed.returncode == 0


def _git_json(repo: Path, commit: str, relative: str) -> tuple[dict, bytes]:
    blob = _git_blob(repo, commit, relative)
    try:
        value = json.loads(blob)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceReadinessError(f"SOURCE_JSON_INVALID:{relative}") from exc
    if not isinstance(value, dict):
        raise SourceReadinessError(f"SOURCE_JSON_OBJECT_REQUIRED:{relative}")
    return value, blob


def _git_paths(repo: Path, commit: str, prefix: str) -> list[str]:
    output = _run_git(repo, ["ls-tree", "-r", "--name-only", commit, "--", prefix])
    return [line for line in output.splitlines() if line]


def _sha_ref(repo: Path, commit: str, relative: str) -> dict:
    blob = _git_blob(repo, commit, relative)
    return {
        "path": relative,
        "blobSha256": hashlib.sha256(blob).hexdigest(),
        "sourceCommit": commit,
    }


def _verify_executed_owner_pins(repo: Path, commit: str) -> None:
    """Prove the validator and contract being executed are the pinned blobs."""
    for relative in (
        "collectors/dart_filing_content.py",
        "collectors/sec_filing_content.py",
        "config/dart_filing_content_contract.json",
        "config/sec_filing_content_contract.json",
    ):
        try:
            runtime = (ROOT / relative).read_bytes()
        except OSError as exc:
            raise SourceReadinessError(f"EXECUTED_OWNER_FILE_READ_FAILED:{relative}") from exc
        if runtime != _git_blob(repo, commit, relative):
            raise SourceReadinessError(f"EXECUTED_OWNER_PIN_MISMATCH:{relative}")


def _validated_counts(run: dict, source_name: str) -> dict:
    records = run.get("records")
    counts = run.get("counts")
    if not isinstance(records, list) or not isinstance(counts, dict):
        raise SourceReadinessError(f"{source_name}_RUN_SHAPE_INVALID")
    if not all(isinstance(row, dict) for row in records):
        raise SourceReadinessError(f"{source_name}_RUN_RECORD_INVALID")
    if set(counts) != {"captured", "failed", "not_applicable", "skipped"} or any(
        type(value) is not int or value < 0 for value in counts.values()
    ):
        raise SourceReadinessError(f"{source_name}_RUN_COUNTS_SHAPE_INVALID")
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


def _run_state(
    source_name: str,
    run: dict,
    retained_count: int,
    *,
    content_rows_validated: bool,
) -> dict:
    counts = _validated_counts(run, source_name)
    failed = counts["failed"]
    content_present = counts["captured"] + counts["skipped"] > 0
    if content_present and not content_rows_validated:
        new_content = "UNKNOWN_UNVALIDATED_DART_CONTENT"
        collection = "UNKNOWN_CONTENT_ROWS_NOT_VALIDATED"
    elif failed:
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
            else "UNKNOWN_UNVALIDATED_DART_CONTENT"
            if content_present and not content_rows_validated
            else "NOT_MISSING_SUCCESSFUL_EMPTY_RESULT"
            if not failed
            else "UNKNOWN_COLLECTION_FAILED"
        ),
        "stalenessState": "UNKNOWN_NO_SOURCE_OWNER_POLICY",
        "consecutiveFailureState": "UNKNOWN_HISTORY_NOT_BOUND",
        "consecutiveFailureCount": None,
    }


def _fact_refs(manifest: dict, source_id: str, manifest_sha256: str) -> list[dict]:
    result = []
    for index, fact in enumerate(manifest.get("extracted", [])):
        if not isinstance(fact, dict):
            raise SourceReadinessError("EXTRACTED_FACT_INVALID")
        fact_hash = payload_sha256(fact)
        identity = {
            "sourceId": source_id,
            "sourceManifestSha256": manifest_sha256,
            "extractorVersion": manifest["extractor_version"],
            "index": index,
            "factSha256": fact_hash,
        }
        result.append({
            "factId": f"SEC_EXTRACTED_FACT_REF_SHA256_{payload_sha256(identity)}",
            "factSha256": fact_hash,
            "sourceId": source_id,
            "sourceManifestSha256": manifest_sha256,
            "extractorVersion": manifest["extractor_version"],
            "index": index,
            "label": fact.get("label"),
        })
    return result


def _provider_event_at(metadata: dict, ticker: str, accession: str) -> str | None:
    stocks = metadata.get("stocks")
    stock = stocks.get(ticker) if isinstance(stocks, dict) else None
    filings = stock.get("filings_recent") if isinstance(stock, dict) else None
    if not isinstance(filings, list):
        raise SourceReadinessError(f"SEC_METADATA_FILINGS_INVALID:{ticker}")
    matches = [row for row in filings if isinstance(row, dict) and row.get("accession") == accession]
    if len(matches) != 1:
        raise SourceReadinessError(f"SEC_METADATA_IDENTITY_CARDINALITY:{ticker}:{accession}")
    row = matches[0]
    present = [
        row[key]
        for key in ("acceptanceDateTime", "acceptance_datetime", "accepted_at_utc")
        if key in row
    ]
    if not present:
        return None
    if len(present) != 1:
        raise SourceReadinessError("SEC_PROVIDER_EVENT_TIME_AMBIGUOUS")
    parsed = _utc(present[0], "SEC_PROVIDER_EVENT_TIME_INVALID")
    return parsed.isoformat().replace("+00:00", "Z")


def _available_bound(*values: str | None) -> tuple[str, list[str]]:
    present = [value for value in values if value is not None]
    parsed = [(_utc(value, "AVAILABLE_BOUND_TIME_INVALID"), value) for value in present]
    if not parsed:
        raise SourceReadinessError("AVAILABLE_BOUND_EMPTY")
    maximum = max(parsed, key=lambda row: row[0])[0]
    return maximum.isoformat().replace("+00:00", "Z"), present


def _latest_dart_material(repo: Path, commit: str, evaluated: dt.datetime) -> dict | None:
    paths = [
        path for path in _git_paths(repo, commit, "data/dart_content")
        if path.endswith("/_manifest.json")
    ]
    if not paths:
        return None
    owner_contract = DART.load_contract()
    candidates = []
    for relative in paths:
        manifest, manifest_blob = _git_json(repo, commit, relative)
        directory = str(Path(relative).parent)
        raw_zip = _git_blob(repo, commit, f"{directory}/_source.zip")
        raw_members = {}
        document_refs = []
        for document in manifest.get("documents", []):
            cache_name = document.get("cache_name")
            raw = gzip.decompress(_git_blob(repo, commit, f"{directory}/{cache_name}"))
            raw_members[cache_name] = raw
            document_refs.append({
                "path": f"{directory}/{cache_name}",
                "contentSha256": document["content_sha256"],
                "kind": "member",
            })
        try:
            DART.validate_manifest(manifest, raw_zip, raw_members, owner_contract)
        except DART.DartContentError as exc:
            raise SourceReadinessError(f"DART_OWNER_VALIDATION_FAILED:{exc}") from exc
        available = _utc(manifest.get("retrieved_at_utc"), "DART_RETRIEVED_AT_INVALID")
        if available > evaluated:
            raise SourceReadinessError("DART_SOURCE_FROM_FUTURE")
        identity = manifest["filing_identity"]
        candidates.append({
            "sourceId": f"DART_{identity['stock_code']}_{identity['rcept_no']}",
            "sourceType": "KR_DART_FILING",
            "manifestPath": relative,
            "manifestSha256": hashlib.sha256(manifest_blob).hexdigest(),
            "identity": {
                "market": "Korea",
                "symbol": manifest["ticker"],
                "receiptNumber": identity["rcept_no"],
            },
            "eventDate": dt.datetime.strptime(manifest["filing_date"], "%Y%m%d").date().isoformat(),
            "eventAtUtc": None,
            "eventTimePrecision": "DATE_ONLY",
            "availableAtUtc": manifest["retrieved_at_utc"],
            "availableAtBasis": [manifest["retrieved_at_utc"]],
            "freshThroughUtc": None,
            "freshnessStatus": "UNKNOWN_NO_SOURCE_OWNER_POLICY",
            "documentRefs": document_refs,
            "archiveRef": {
                "path": f"{directory}/_source.zip",
                "contentSha256": manifest["source_archive"]["content_sha256"],
            },
            "factRefs": [],
        })
    return max(candidates, key=lambda row: (row["eventDate"], row["availableAtUtc"], row["sourceId"]))


def _sec_sources(
    repo: Path,
    commit: str,
    metadata: dict,
    run: dict,
    evaluated: dt.datetime,
) -> list[dict]:
    _verify_executed_owner_pins(repo, commit)
    owner_contract = SEC.load_contract()
    result = []
    for row in run["records"]:
        if not (
            row.get("operation") in {"captured", "skipped"}
            and row.get("content_status") == "OK"
        ):
            continue
        if row.get("operation") == "skipped" and row.get("skip_reason") != "already_captured":
            raise SourceReadinessError("SEC_SKIPPED_CONTENT_REASON_INVALID")
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
        run_semantics = copy.deepcopy(row)
        manifest_semantics = copy.deepcopy(manifest)
        for value in (run_semantics, manifest_semantics):
            value.pop("operation", None)
            value.pop("skip_reason", None)
            value.pop("publication_status", None)
        if canonical_json(manifest_semantics) != canonical_json(run_semantics):
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
            SEC.validate_manifest(manifest, raw_by_name, owner_contract)
            SEC.validate_manifest(row, raw_by_name, owner_contract)
        except SEC.SecContentError as exc:
            raise SourceReadinessError(f"SEC_OWNER_VALIDATION_FAILED:{exc}") from exc
        provider_event = _provider_event_at(metadata, ticker, identity["accession"])
        available_at, available_basis = _available_bound(
            provider_event,
            manifest.get("retrieved_at_utc"),
            run.get("observed_at_utc"),
        )
        available = _utc(available_at, "SEC_AVAILABLE_AT_INVALID")
        if available > evaluated:
            raise SourceReadinessError("SEC_SOURCE_FROM_FUTURE")
        manifest_sha256 = hashlib.sha256(manifest_blob).hexdigest()
        source_id = f"SEC_{identity['cik']}_{identity['accession']}"
        result.append({
            "sourceId": source_id,
            "sourceType": "US_SEC_EDGAR_FILING",
            "manifestPath": relative,
            "manifestSha256": manifest_sha256,
            "identity": {
                "market": "US",
                "symbol": ticker,
                "cik": identity["cik"],
                "accession": identity["accession"],
            },
            "eventDate": manifest["filing_date"],
            "eventAtUtc": provider_event,
            "eventTimePrecision": "EXACT" if provider_event is not None else "DATE_ONLY",
            "availableAtUtc": available_at,
            "availableAtBasis": available_basis,
            "freshThroughUtc": None,
            "freshnessStatus": "UNKNOWN_NO_SOURCE_OWNER_POLICY",
            "documentRefs": document_refs,
            "factRefs": _fact_refs(manifest, source_id, manifest_sha256),
        })
    return result


def _latest_material_index(
    latest_refs: list[dict],
    sec_sources: list[dict],
    dart_latest: dict | None,
    evaluated: dt.datetime,
) -> dict:
    run_by_source = {row["source"]: row for row in latest_refs}
    result = {}
    for name, candidates in (
        ("DART", [] if dart_latest is None else [dart_latest]),
        ("SEC", sec_sources),
    ):
        run = run_by_source[name]
        counts = run["counts"]
        latest = max(
            candidates,
            key=lambda row: (row["eventDate"], row["availableAtUtc"], row["sourceId"]),
            default=None,
        )
        if counts["failed"]:
            status = "FAILED"
        elif counts["captured"]:
            status = "OK_NEW"
        elif counts["skipped"]:
            status = "OK_RETAINED_REUSED"
        else:
            status = "OK_EMPTY"
        age = None
        if latest is not None:
            age = int((evaluated - _utc(latest["availableAtUtc"], "LATEST_SOURCE_TIME_INVALID")).total_seconds())
            if age < 0:
                raise SourceReadinessError("LATEST_SOURCE_FROM_FUTURE")
        result[name] = {
            "currentRunIdentity": {
                "sourceCommit": run["sourceCommit"],
                "contentRunPath": run["contentRunPath"],
                "contentRunSha256": run["contentRunSha256"],
                "metadataPath": run["metadataPath"],
                "metadataSha256": run["metadataSha256"],
                "observedAtUtc": run["observedAtUtc"],
            },
            "counts": copy.deepcopy(counts),
            "status": status,
            "lastMaterialSource": copy.deepcopy(latest),
            "sourceAgeSeconds": age,
            "ageStatus": (
                "OBSERVED_AGE_ONLY_NOT_FRESHNESS"
                if latest is not None
                else "UNKNOWN_NO_MATERIAL_SOURCE_IN_CURRENT_INDEX"
            ),
        }
    return result


def _telemetry_record(
    repo: Path,
    commit: str,
    telemetry_path: str,
) -> dict:
    telemetry, telemetry_blob = _git_json(repo, commit, telemetry_path)
    date_path = str(Path(telemetry_path).parent)
    index_path = f"{date_path}/index.json"
    index, _ = _git_json(repo, commit, index_path)
    unsigned_index = copy.deepcopy(index)
    claimed_index_sha = unsigned_index.pop("index_sha256", None)
    if claimed_index_sha != payload_sha256(unsigned_index):
        raise SourceReadinessError("COLLECT_RUN_INDEX_SHA_MISMATCH")
    matches = [row for row in index.get("records", []) if row.get("path") == telemetry_path]
    if len(matches) != 1:
        raise SourceReadinessError("COLLECT_RUN_INDEX_CARDINALITY")
    telemetry_sha = hashlib.sha256(telemetry_blob).hexdigest()
    if matches[0].get("record_sha256") != telemetry_sha:
        raise SourceReadinessError("COLLECT_RUN_RECORD_SHA_MISMATCH")
    return {
        "value": telemetry,
        "path": telemetry_path,
        "sha256": telemetry_sha,
        "indexPath": index_path,
        "indexSha256": claimed_index_sha,
    }


def _run_ledger(
    repo: Path,
    source_commit: str,
    contract: dict,
    observation_origin: str,
) -> dict:
    maximum = contract["run_ledger"]["max_records"]
    output = _run_git(
        repo,
        [
            "log",
            f"--max-count={maximum + 1}",
            "--format=%H",
            source_commit,
            "--",
            "data/latest_sec_content.json",
            "data/latest_dart_content.json",
        ],
    )
    commits = [line for line in output.splitlines() if COMMIT_RE.fullmatch(line)]
    truncated = len(commits) > maximum
    commits = list(reversed(commits[:maximum]))
    records = []
    previous_sha = None
    for commit in commits:
        changed = _run_git(repo, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit])
        telemetry_paths = [
            path for path in changed.splitlines()
            if re.fullmatch(r"data/operations/collect_runs/\d{4}-\d{2}-\d{2}/run-\d+-attempt-\d+\.json", path)
        ]
        if not telemetry_paths:
            continue
        if len(telemetry_paths) != 1:
            raise SourceReadinessError("COLLECT_RUN_COMMIT_TELEMETRY_CARDINALITY")
        index_path = f"{Path(telemetry_paths[0]).parent}/index.json"
        required_paths = [telemetry_paths[0], index_path]
        for paths in contract["latest_sources"].values():
            required_paths.extend((paths["content_run"], paths["metadata"]))
        if not all(_git_blob_present(repo, commit, path) for path in required_paths):
            # Early telemetry records preceded the immutable index contract.
            # Promised but unavailable historical blobs are likewise not proof.
            continue
        telemetry = _telemetry_record(repo, commit, telemetry_paths[0])
        run_refs = {}
        failed = 0
        captured = 0
        observed = []
        for source_name, paths in contract["latest_sources"].items():
            run, run_blob = _git_json(repo, commit, paths["content_run"])
            metadata_blob = _git_blob(repo, commit, paths["metadata"])
            if run.get("source_sha256") != hashlib.sha256(metadata_blob).hexdigest():
                raise SourceReadinessError(f"LEDGER_{source_name}_SOURCE_HASH_MISMATCH")
            counts = _validated_counts(run, source_name)
            failed += counts["failed"]
            captured += counts["captured"]
            observed.append(_utc(run.get("observed_at_utc"), "LEDGER_RUN_TIME_INVALID"))
            run_refs[source_name] = {
                "contentRunPath": paths["content_run"],
                "contentRunSha256": hashlib.sha256(run_blob).hexdigest(),
                "metadataPath": paths["metadata"],
                "metadataSha256": run["source_sha256"],
                "counts": counts,
            }
        guard = telemetry["value"].get("guard")
        if not isinstance(guard, dict) or type(guard.get("skip")) is not bool:
            raise SourceReadinessError("COLLECT_RUN_GUARD_INVALID")
        if guard["skip"]:
            status = "DELAYED_OR_SKIPPED"
        elif failed:
            status = "FAILED"
        elif captured:
            status = "OK_NEW"
        else:
            status = "OK_EMPTY"
        if status not in contract["run_ledger"]["statuses"]:
            raise SourceReadinessError("LEDGER_STATUS_INTERNAL_ERROR")
        base = {
            "schemaVersion": "ai_external_analysis_source_run_record/1",
            "recordOrigin": (
                observation_origin
                if commit == source_commit
                else "BACKFILLED_VERIFIED_OBSERVATION_ONLY"
            ),
            "sourceCommit": commit,
            "telemetryPath": telemetry["path"],
            "telemetrySha256": telemetry["sha256"],
            "telemetryIndexPath": telemetry["indexPath"],
            "telemetryIndexSha256": telemetry["indexSha256"],
            "observedAtUtc": max(observed).isoformat().replace("+00:00", "Z"),
            "status": status,
            "guard": copy.deepcopy(guard),
            "slot": copy.deepcopy(telemetry["value"].get("slot")),
            "sourceRunRefs": run_refs,
            "previousRecordSha256": previous_sha,
        }
        base["recordSha256"] = payload_sha256(base)
        previous_sha = base["recordSha256"]
        records.append(base)
    consecutive_failures = 0
    consecutive_delayed = 0
    for record in reversed(records):
        if record["status"] == "FAILED":
            consecutive_failures += 1
        else:
            break
    for record in reversed(records):
        if record["status"] == "DELAYED_OR_SKIPPED":
            consecutive_delayed += 1
        else:
            break
    successes = [row for row in records if row["status"] in {"OK_NEW", "OK_EMPTY"}]
    ledger = {
        "schemaVersion": "ai_external_analysis_source_run_ledger/1",
        "ledgerOrigin": observation_origin,
        "sourceCommit": source_commit,
        "historyStartStatus": (
            "UNKNOWN_TRUNCATED_BEFORE_FIRST_RETAINED_RECORD"
            if truncated
            else "UNKNOWN_BEFORE_FIRST_VERIFIABLE_RECEIPT"
        ),
        "records": records,
        "summary": {
            "recordCount": len(records),
            "consecutiveFailureLowerBound": consecutive_failures,
            "consecutiveDelayedOrSkippedLowerBound": consecutive_delayed,
            "lastSuccessfulRunAtUtc": successes[-1]["observedAtUtc"] if successes else None,
            "countsAreLowerBounds": True,
        },
    }
    ledger["ledgerSha256"] = payload_sha256(ledger)
    return ledger


def _cron_expressions(workflow: bytes) -> list[str]:
    text = workflow.decode("utf-8")
    expressions = re.findall(r"^\s*-\s+cron:\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    if not expressions:
        raise SourceReadinessError("NOMINAL_SCHEDULE_NOT_FOUND")
    return expressions


def _next_nominal_due(after: dt.datetime, expressions: list[str]) -> str:
    candidates = []
    for expression in expressions:
        fields = expression.split()
        if len(fields) != 5 or fields[2:4] != ["*", "*"]:
            raise SourceReadinessError("NOMINAL_SCHEDULE_UNSUPPORTED")
        try:
            minute, hour = int(fields[0]), int(fields[1])
            start, end = (int(value) for value in fields[4].split("-", 1))
        except (ValueError, TypeError) as exc:
            raise SourceReadinessError("NOMINAL_SCHEDULE_UNSUPPORTED") from exc
        for offset in range(8):
            day = (after + dt.timedelta(days=offset)).date()
            cron_weekday = (day.weekday() + 1) % 7
            if start <= cron_weekday <= end:
                candidate = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=dt.timezone.utc)
                if candidate > after:
                    candidates.append(candidate)
                    break
    if not candidates:
        raise SourceReadinessError("NOMINAL_NEXT_DUE_NOT_FOUND")
    return min(candidates).isoformat().replace("+00:00", "Z")


def _freshness_coverage(
    repo: Path,
    commit: str,
    evaluated: dt.datetime,
    ledger: dict,
) -> dict:
    workflow = _git_blob(repo, commit, ".github/workflows/collect.yml")
    expressions = _cron_expressions(workflow)
    records = ledger["records"]
    intervals = []
    for previous, current in zip(records, records[1:]):
        intervals.append({
            "fromRecordSha256": previous["recordSha256"],
            "toRecordSha256": current["recordSha256"],
            "fromObservedAtUtc": previous["observedAtUtc"],
            "toObservedAtUtc": current["observedAtUtc"],
            "status": "EXACT_BETWEEN_VERIFIED_POLL_RECEIPTS",
        })
    return {
        "verifiedPollIntervals": intervals,
        "futureCoverageStatus": "UNKNOWN_NO_VERIFIED_FUTURE_POLL",
        "freshThroughUtc": None,
        "nominalSchedule": {
            "cronExpressions": expressions,
            "nextNominalDueAtUtc": _next_nominal_due(evaluated, expressions),
            "status": "BEST_EFFORT_NOMINAL_ONLY_NOT_DELIVERY_GUARANTEE",
        },
    }


def _admission_receipt(
    commit: str,
    latest_refs: list[dict],
    material_index: dict,
    owner_refs: list[dict],
    freshness: dict,
    ledger: dict,
    contract: dict,
    observation_origin: str,
) -> dict:
    receipt = {
        "schemaVersion": "ai_external_analysis_source_owner_admission/1",
        "receiptOrigin": observation_origin,
        "sourceCommit": commit,
        "status": (
            "NATURAL_OWNER_RECEIPT_VALIDATED_NOT_ADMITTED"
            if observation_origin == "NATURAL_SOURCE_OWNER_EMITTED"
            else "REFERENCE_VALIDATED_NOT_ADMITTED"
        ),
        "latestSourceRefs": copy.deepcopy(latest_refs),
        "latestMaterialSourceIndex": copy.deepcopy(material_index),
        "ownerRefs": copy.deepcopy(owner_refs),
        "freshnessCoverageSha256": payload_sha256(freshness),
        "runLedgerSha256": ledger["ledgerSha256"],
        "admissionAuthorized": False,
        "authority": copy.deepcopy(contract["admission_authority"]),
    }
    receipt["receiptSha256"] = payload_sha256(receipt)
    return receipt


def _condition_rows(sec_sources: list[dict], observation_origin: str) -> list[dict]:
    natural = observation_origin == "NATURAL_SOURCE_OWNER_EMITTED"
    return [
        {
            "id": "latest_actual_source",
            "ready": natural,
            "status": (
                "NATURAL_CURRENT_RUN_INDEX_READY"
                if natural
                else "INDEX_READY_BACKFILLED_NOT_NATURAL_ADMISSION"
            ),
        },
        {
            "id": "market_symbol_identity",
            "ready": bool(sec_sources),
            "status": "READY_REFERENCE_IDENTITY" if sec_sources else "UNKNOWN_NO_RETAINED_SOURCE",
        },
        {
            "id": "event_available_fresh_through",
            "ready": False,
            "status": "MECHANISM_READY_CURRENT_EVENT_TIME_OR_FRESHNESS_UNKNOWN",
        },
        {
            "id": "source_owner_binding",
            "ready": natural,
            "status": (
                "NATURAL_SOURCE_OWNER_RECEIPT_EMITTED"
                if natural
                else "REFERENCE_PINS_ONLY_NOT_ADMISSION"
            ),
        },
        {
            "id": "original_or_approved_excerpt_hash",
            "ready": bool(sec_sources),
            "status": (
                "READY_OWNER_VALIDATED_RETAINED_BYTES"
                if sec_sources
                else "UNKNOWN_NO_RETAINED_SOURCE"
            ),
        },
        {
            "id": "continuous_missing_delay_state",
            "ready": False,
            "status": (
                "NATURAL_LEDGER_FIRST_READBACK_HISTORY_INCOMPLETE"
                if natural
                else "BACKFILLED_LEDGER_NOT_NATURAL_PRODUCER_READBACK"
            ),
        },
    ]


def _shadow_source_match(admission: dict, retained_sources: list[dict]) -> dict:
    return {
        "schemaVersion": "ai_external_analysis_shadow_source_match/1",
        "status": "NOT_ADMITTED_CURRENT_NATURAL_EVIDENCE_INCOMPLETE",
        "sourceCommit": admission["sourceCommit"],
        "sourceOwnerAdmissionReceiptSha256": admission["receiptSha256"],
        "retainedSourceRefsSha256": payload_sha256(retained_sources),
        "stage3InputSourcesSha256": payload_sha256([]),
        "exactMatchRequired": True,
        "admissionAuthorized": False,
    }


def validate_shadow_source_match(packet: dict) -> dict:
    expected = _shadow_source_match(
        packet["sourceOwnerAdmissionReceipt"], packet["retainedSourceRefs"]
    )
    if canonical_json(packet.get("shadowSourceMatch")) != canonical_json(expected):
        raise SourceReadinessError("SHADOW_SOURCE_EXACT_MATCH_FAILED")
    if packet.get("stage3InputSources") != []:
        raise SourceReadinessError("SHADOW_SOURCE_ADMISSION_FORBIDDEN")
    return copy.deepcopy(expected)


def _derive_packet(
    repo: Path,
    source_commit: str,
    evaluated_at_utc: str,
    contract: dict,
    observation_origin: str,
) -> dict:
    if observation_origin not in {
        "BACKFILLED_VERIFIED_OBSERVATION_ONLY",
        "NATURAL_SOURCE_OWNER_EMITTED",
    }:
        raise SourceReadinessError("OBSERVATION_ORIGIN_INVALID")
    repo = Path(repo).resolve()
    commit = _validate_commit(repo, source_commit)
    evaluated = _utc(evaluated_at_utc, "EVALUATED_AT_INVALID")
    latest = {}
    for source_name, paths in contract["latest_sources"].items():
        run, run_blob = _git_json(repo, commit, paths["content_run"])
        metadata, metadata_blob = _git_json(repo, commit, paths["metadata"])
        if run.get("source_sha256") != hashlib.sha256(metadata_blob).hexdigest():
            raise SourceReadinessError(f"{source_name}_LATEST_SOURCE_HASH_MISMATCH")
        observed = _utc(run.get("observed_at_utc"), f"{source_name}_OBSERVED_AT_INVALID")
        if observed > evaluated:
            raise SourceReadinessError(f"{source_name}_RUN_FROM_FUTURE")
        _validated_counts(run, source_name)
        latest[source_name] = (run, run_blob, metadata, metadata_blob, paths)

    sec_sources = _sec_sources(
        repo,
        commit,
        latest["SEC"][2],
        latest["SEC"][0],
        evaluated,
    )
    dart_latest = _latest_dart_material(repo, commit, evaluated)
    run_states = {}
    latest_refs = []
    for source_name in ("DART", "SEC"):
        run, run_blob, _metadata, _metadata_blob, paths = latest[source_name]
        retained_count = len(sec_sources) if source_name == "SEC" else 0
        run_states[source_name] = _run_state(
            source_name,
            run,
            retained_count,
            content_rows_validated=source_name == "SEC",
        )
        latest_refs.append({
            "source": source_name,
            "sourceCommit": commit,
            "contentRunPath": paths["content_run"],
            "contentRunSha256": hashlib.sha256(run_blob).hexdigest(),
            "metadataPath": paths["metadata"],
            "metadataSha256": run["source_sha256"],
            "observedAtUtc": run["observed_at_utc"],
            "runStatus": run["run_status"],
            "counts": copy.deepcopy(run["counts"]),
        })

    owner_refs = [_sha_ref(repo, commit, path) for path in contract["owner_paths"]]
    material_index = _latest_material_index(
        latest_refs, sec_sources, dart_latest, evaluated
    )
    ledger = _run_ledger(repo, commit, contract, observation_origin)
    freshness = _freshness_coverage(repo, commit, evaluated, ledger)
    admission = _admission_receipt(
        commit,
        latest_refs,
        material_index,
        owner_refs,
        freshness,
        ledger,
        contract,
        observation_origin,
    )
    conditions = _condition_rows(sec_sources, observation_origin)
    if [row["id"] for row in conditions] != contract["condition_order"]:
        raise SourceReadinessError("CONDITION_ORDER_INTERNAL_ERROR")
    all_ready = all(row["ready"] for row in conditions)
    shadow_match = _shadow_source_match(admission, sec_sources)
    packet = {
        "schemaVersion": "ai_external_analysis_source_readiness_packet/1",
        "contractVersion": contract["contract_version"],
        "sourceCommit": commit,
        "evaluatedAtUtc": evaluated_at_utc,
        "observationOrigin": observation_origin,
        "status": "READY" if all_ready else "DATA_QUALIFICATION_WAIT",
        "latestSourceRefs": latest_refs,
        "retainedSourceRefs": sec_sources,
        "latestMaterialSourceIndex": material_index,
        "ownerRefs": owner_refs,
        "ownerReferencesBound": True,
        "sourceOwnerAdmissionReceipt": admission,
        "shadowSourceMatch": shadow_match,
        "freshnessCoverage": freshness,
        "runLedger": ledger,
        "runStates": run_states,
        "conditions": conditions,
        "allSixConditionsReady": all_ready,
        "mechanismReady": True,
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
    observation_origin: str = "BACKFILLED_VERIFIED_OBSERVATION_ONLY",
) -> dict:
    checked_contract = validate_contract(contract) if contract is not None else load_contract()
    packet = _derive_packet(
        repo, source_commit, evaluated_at_utc, checked_contract, observation_origin
    )
    return validate_packet(
        packet,
        repo,
        source_commit,
        evaluated_at_utc,
        checked_contract,
        observation_origin,
    )


def validate_packet(
    packet: object,
    repo: Path,
    source_commit: str,
    evaluated_at_utc: str,
    contract: dict | None = None,
    observation_origin: str = "BACKFILLED_VERIFIED_OBSERVATION_ONLY",
) -> dict:
    checked_contract = validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(packet, dict) or set(packet) != {
        "schemaVersion", "contractVersion", "sourceCommit", "evaluatedAtUtc",
        "observationOrigin", "status",
        "latestSourceRefs", "retainedSourceRefs", "latestMaterialSourceIndex",
        "ownerRefs", "ownerReferencesBound", "sourceOwnerAdmissionReceipt",
        "shadowSourceMatch",
        "freshnessCoverage", "runLedger", "runStates", "conditions",
        "allSixConditionsReady", "stage3InputSources", "modelSourceInferenceAuthorized",
        "mechanismReady", "authority", "packetSha256",
    }:
        raise SourceReadinessError("PACKET_FIELDS_INVALID")
    claimed = packet.get("packetSha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packetSha256")
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise SourceReadinessError("PACKET_SHA256_INVALID")
    if payload_sha256(unsigned) != claimed:
        raise SourceReadinessError("PACKET_SHA256_MISMATCH")
    validate_shadow_source_match(packet)
    rebuilt = _derive_packet(
        repo, source_commit, evaluated_at_utc, checked_contract, observation_origin
    )
    if canonical_json(packet) != canonical_json(rebuilt):
        raise SourceReadinessError("PACKET_SEMANTIC_TAMPER_OR_DRIFT")
    return copy.deepcopy(packet)


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _write_immutable(path: Path, value: dict) -> None:
    body = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != body:
            raise SourceReadinessError(f"IMMUTABLE_OUTPUT_COLLISION:{path}")
        return
    _atomic_write(path, body)


def write_observation(packet: dict, out_root: Path) -> dict:
    out_root = Path(out_root)
    day = packet["evaluatedAtUtc"][:10]
    directory = out_root / day
    receipt = packet["sourceOwnerAdmissionReceipt"]
    ledger = packet["runLedger"]
    packet_path = directory / f"readiness-{packet['packetSha256'][:16]}.json"
    admission_path = directory / f"admission-{receipt['receiptSha256'][:16]}.json"
    ledger_path = directory / f"ledger-{ledger['ledgerSha256'][:16]}.json"
    for path, value in (
        (packet_path, packet),
        (admission_path, receipt),
        (ledger_path, ledger),
    ):
        _write_immutable(path, value)
    index_path = out_root / "index.json"
    if index_path.exists():
        index = _read_json(index_path)
        unsigned = copy.deepcopy(index)
        claimed = unsigned.pop("indexSha256", None)
        if claimed != payload_sha256(unsigned):
            raise SourceReadinessError("OBSERVATION_INDEX_SHA_MISMATCH")
        if set(index) != {"schemaVersion", "records", "indexSha256"}:
            raise SourceReadinessError("OBSERVATION_INDEX_FIELDS_INVALID")
        index.pop("indexSha256")
    else:
        index = {"schemaVersion": "ai_external_analysis_source_readiness_index/1", "records": []}
    relative = lambda path: path.relative_to(out_root).as_posix()
    row = {
        "sourceCommit": packet["sourceCommit"],
        "evaluatedAtUtc": packet["evaluatedAtUtc"],
        "status": packet["status"],
        "readinessPath": relative(packet_path),
        "readinessSha256": packet["packetSha256"],
        "admissionPath": relative(admission_path),
        "admissionSha256": receipt["receiptSha256"],
        "ledgerPath": relative(ledger_path),
        "ledgerSha256": ledger["ledgerSha256"],
    }
    identities = {(item["sourceCommit"], item["evaluatedAtUtc"]) for item in index["records"]}
    identity = (row["sourceCommit"], row["evaluatedAtUtc"])
    if identity in identities:
        existing = next(
            item for item in index["records"]
            if (item["sourceCommit"], item["evaluatedAtUtc"]) == identity
        )
        if canonical_json(existing) != canonical_json(row):
            raise SourceReadinessError("OBSERVATION_INDEX_IDENTITY_COLLISION")
    else:
        index["records"].append(row)
    index["records"].sort(key=lambda item: (item["evaluatedAtUtc"], item["sourceCommit"]))
    index["indexSha256"] = payload_sha256(index)
    _atomic_write(
        index_path,
        (json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return {
        "readinessPath": str(packet_path),
        "admissionPath": str(admission_path),
        "ledgerPath": str(ledger_path),
        "indexPath": str(index_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--evaluated-at-utc", required=True)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=ROOT / "data" / "observations" / "ai_external_analysis_source_readiness",
    )
    args = parser.parse_args()
    packet = build_packet(
        args.repo,
        args.source_commit,
        args.evaluated_at_utc,
        observation_origin="NATURAL_SOURCE_OWNER_EMITTED",
    )
    paths = write_observation(packet, args.out_root)
    print(json.dumps({
        **paths,
        "status": packet["status"],
        "allSixConditionsReady": packet["allSixConditionsReady"],
        "modelSourceInferenceAuthorized": packet["modelSourceInferenceAuthorized"],
        "packetSha256": packet["packetSha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
