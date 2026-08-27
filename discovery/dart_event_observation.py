#!/usr/bin/env python3
"""P3-08 evidence-only DART filing observation population.

This adapter records only facts already present in the committed OpenDART
metadata and filing-content evidence.  It does not infer an event type,
direction, importance, Stage, Rule result, action, order, or trade.  Filing
dates have day precision, so every observation remains explicitly blocked for
event escalation even when the original ZIP bytes are available.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
COLLECTORS = ROOT / "collectors"
sys.path.insert(0, str(COLLECTORS))

import dart_filing_content as DART  # noqa: E402


SCHEMA_VERSION = "dart_event_observation_packet/1"
OBSERVATION_VERSION = "dart_event_observation/1"
DEFAULT_DART = ROOT / "data/latest_dart.json"
DEFAULT_CONTENT = ROOT / "data/latest_dart_content.json"
DEFAULT_DATA_ROOT = ROOT / "data"
DEFAULT_OUT_ROOT = ROOT / "data/observations/dart_event_observations"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STOCK_RE = re.compile(r"^\d{6}$")

AUTHORITY = {
    "observation_recording_only": True,
    "event_type_inference_authorized": False,
    "importance_classification_authorized": False,
    "interpretation_authorized": False,
    "candidate_promotion_authorized": False,
    "notification_authorized": False,
    "action_generation_authorized": False,
    "order_generation_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class DartEventObservationError(ValueError):
    """Fail-closed DART observation or provenance violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _source_ref(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return f"external_fixture/{Path(path).name}"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DartEventObservationError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise DartEventObservationError(f"JSON_NOT_OBJECT:{path}")
    return value


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise DartEventObservationError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DartEventObservationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise DartEventObservationError(code)
    return parsed.astimezone(dt.timezone.utc)


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str):
        raise DartEventObservationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise DartEventObservationError(code) from exc
    if parsed.strftime("%Y%m%d") != value:
        raise DartEventObservationError(code)
    return parsed


def _validate_source(source: dict, decision_at: dt.datetime) -> dict:
    if source.get("source") != "OpenDART (금융감독원)" or source.get("source_tier") != "Official":
        raise DartEventObservationError("DART_SOURCE_IDENTITY_INVALID")
    collected = _utc(source.get("collected_at_utc"), "DART_COLLECTED_AT_INVALID")
    decision_date = decision_at.astimezone(dt.timezone(dt.timedelta(hours=9))).date()
    if collected > decision_at:
        raise DartEventObservationError("DART_SOURCE_FROM_FUTURE")
    if source.get("collected_for_kst_date") != decision_date.isoformat():
        raise DartEventObservationError("DART_SOURCE_DATE_MISMATCH")
    keywords = source.get("filter_keywords")
    stocks = source.get("stocks")
    if not isinstance(keywords, list) or not keywords or not isinstance(stocks, dict):
        raise DartEventObservationError("DART_SOURCE_SHAPE_INVALID")
    observations = []
    failures = []
    ok_count = 0
    for ticker, stock in sorted(stocks.items()):
        if STOCK_RE.fullmatch(ticker) is None or not isinstance(stock, dict):
            raise DartEventObservationError("DART_STOCK_INVALID")
        if stock.get("status") == "FAILED":
            if (
                not isinstance(stock.get("name"), str)
                or not stock["name"]
                or not isinstance(stock.get("error"), str)
                or not stock["error"]
            ):
                raise DartEventObservationError(f"DART_STOCK_FAILURE_INVALID:{ticker}")
            failures.append({
                "ticker": ticker,
                "name": stock["name"],
                "atlas_stage": stock.get("atlas_stage"),
                "coverage": stock.get("coverage"),
                "status": "SOURCE_COLLECTION_FAILED",
                "reasons": ["DART_STOCK_COLLECTION_FAILED"],
            })
            continue
        if stock.get("status") != "ok":
            raise DartEventObservationError(f"DART_STOCK_STATUS_INVALID:{ticker}")
        ok_count += 1
        relevant = stock.get("relevant")
        if (
            not isinstance(relevant, list)
            or stock.get("relevant_count") != len(relevant)
            or type(stock.get("total_count")) is not int
            or stock["total_count"] < len(relevant)
        ):
            raise DartEventObservationError(f"DART_RELEVANT_SET_INVALID:{ticker}")
        for filing in relevant:
            normalized = copy.deepcopy(filing)
            normalized["title"] = DART.normalized_filing_title(filing.get("title"))
            try:
                rcept_no = DART.validate_filing_identity(normalized)
            except DART.DartContentError as exc:
                raise DartEventObservationError(f"DART_FILING_INVALID:{ticker}:{exc}") from exc
            if not any(keyword in normalized["title"] for keyword in keywords):
                raise DartEventObservationError(f"DART_RELEVANT_TITLE_UNPROVEN:{ticker}:{rcept_no}")
            if _date(normalized["date"], "DART_FILING_DATE_INVALID") > decision_date:
                raise DartEventObservationError(f"DART_FILING_FROM_FUTURE:{ticker}:{rcept_no}")
            observations.append({
                "ticker": ticker,
                "name": stock.get("name"),
                "atlas_stage": stock.get("atlas_stage"),
                "filing": normalized,
            })
    expected_summary = {"ok": ok_count, "failed": len(failures)}
    if source.get("summary") != expected_summary:
        raise DartEventObservationError("DART_SOURCE_SUMMARY_MISMATCH")
    if ok_count == 0:
        raise DartEventObservationError("DART_ALL_STOCKS_FAILED")
    return {"observations": observations, "failures": failures}


def _validate_content_run(
    run: dict, source_bytes: bytes, source: dict, decision_at: dt.datetime
) -> dict:
    contract = DART.load_contract()
    base_fields = {
        "schema_version", "source_file", "source_sha256", "collected_for_kst_date",
        "observed_at_utc", "contract_version", "run_status", "counts", "records",
        "authority",
    }
    if not isinstance(run, dict) or set(run) not in (base_fields, base_fields | {"reasons"}):
        raise DartEventObservationError("DART_CONTENT_RUN_FIELDS_MISMATCH")
    run_status = run.get("run_status")
    if (
        run.get("schema_version") != DART.RUN_SCHEMA_VERSION
        or run.get("contract_version") != contract["contract_version"]
        or run.get("authority") != contract["authority"]
        or run_status not in {"OK", "DEGRADED", "FAILED"}
        or run.get("collected_for_kst_date") != source.get("collected_for_kst_date")
        or run.get("source_sha256") != hashlib.sha256(source_bytes).hexdigest()
    ):
        raise DartEventObservationError("DART_CONTENT_RUN_IDENTITY_INVALID")
    if _utc(run.get("observed_at_utc"), "DART_CONTENT_OBSERVED_AT_INVALID") > decision_at:
        raise DartEventObservationError("DART_CONTENT_RUN_FROM_FUTURE")
    records = run.get("records")
    counts = run.get("counts")
    if not isinstance(records, list) or not isinstance(counts, dict):
        raise DartEventObservationError("DART_CONTENT_RUN_SHAPE_INVALID")
    if run_status == "FAILED":
        reasons = run.get("reasons")
        if (
            set(run) != base_fields | {"reasons"}
            or records != []
            or counts != {"captured": 0, "skipped": 0, "failed": 1, "not_applicable": 0}
            or not isinstance(reasons, list)
            or not reasons
            or not all(isinstance(reason, str) and reason for reason in reasons)
        ):
            raise DartEventObservationError("DART_CONTENT_FAILED_RUN_INVALID")
        return {"run_status": run_status, "records": {}, "reasons": copy.deepcopy(reasons)}
    if set(run) != base_fields:
        raise DartEventObservationError("DART_CONTENT_RUN_FIELDS_MISMATCH")
    expected_counts = {
        "captured": sum(row.get("operation") == "captured" for row in records),
        "skipped": sum(row.get("operation") == "skipped" for row in records),
        "failed": sum(row.get("operation") == "failed" for row in records),
        "not_applicable": sum(row.get("content_status") == "NOT_APPLICABLE" for row in records),
    }
    if counts != expected_counts:
        raise DartEventObservationError("DART_CONTENT_COUNTS_MISMATCH")
    if (run_status == "OK") != (counts["failed"] == 0):
        raise DartEventObservationError("DART_CONTENT_RUN_STATUS_MISMATCH")
    indexed = {}
    for row in records:
        identity = row.get("filing_identity") if isinstance(row, dict) else None
        if not isinstance(identity, dict):
            raise DartEventObservationError("DART_CONTENT_RECORD_IDENTITY_INVALID")
        key = (identity.get("stock_code"), identity.get("rcept_no"))
        if key in indexed:
            raise DartEventObservationError("DART_CONTENT_RECORD_DUPLICATE")
        indexed[key] = copy.deepcopy(row)
    return {"run_status": run_status, "records": indexed, "reasons": []}


def _content_evidence(
    data_root: Path, source_row: dict, content_row: dict, decision_at: dt.datetime
) -> dict:
    ticker = source_row["ticker"]
    filing = source_row["filing"]
    rcept_no = filing["rcept_no"]
    if (
        content_row.get("ticker") != ticker
        or content_row.get("filing_date") != filing["date"]
        or content_row.get("title") != filing["title"]
        or content_row.get("name") != source_row["name"]
        or content_row.get("atlas_stage") != source_row["atlas_stage"]
    ):
        raise DartEventObservationError(f"DART_CONTENT_SOURCE_MISMATCH:{ticker}:{rcept_no}")
    content_failed = (
        content_row.get("operation") == "failed"
        or content_row.get("publication_status") == "FAILED"
    )
    if content_failed:
        if (
            content_row.get("operation") != "failed"
            or content_row.get("publication_status") != "FAILED"
        ):
            raise DartEventObservationError(f"DART_CONTENT_FAILURE_STATUS_MISMATCH:{ticker}:{rcept_no}")
        reasons = content_row.get("reasons")
        if not isinstance(reasons, list) or not reasons:
            raise DartEventObservationError(f"DART_CONTENT_FAILURE_INVALID:{ticker}:{rcept_no}")
        return {
            "status": "CONTENT_CAPTURE_FAILED",
            "available_at": None,
            "source_uri": filing["url"],
            "source_sha256": None,
            "reasons": ["DART_CONTENT_CAPTURE_FAILED"],
        }
    if content_row.get("content_status") == "OK":
        try:
            manifest = DART.load_existing_manifest(data_root, ticker, rcept_no)
        except DART.DartContentError as exc:
            raise DartEventObservationError(
                f"DART_RAW_CONTENT_INVALID:{ticker}:{rcept_no}:{exc}"
            ) from exc
        if manifest is None:
            raise DartEventObservationError(f"DART_RAW_CONTENT_MISSING:{ticker}:{rcept_no}")
        comparable = copy.deepcopy(content_row)
        comparable.pop("publication_status", None)
        if comparable != manifest:
            raise DartEventObservationError(f"DART_CONTENT_MANIFEST_MISMATCH:{ticker}:{rcept_no}")
        retrieved = _utc(manifest["retrieved_at_utc"], "DART_CONTENT_RETRIEVED_AT_INVALID")
        if retrieved > decision_at:
            raise DartEventObservationError(f"DART_CONTENT_FROM_FUTURE:{ticker}:{rcept_no}")
        return {
            "status": "RAW_BYTES_VERIFIED_ITEM_EXTRACTION_UNRATIFIED",
            "available_at": manifest["retrieved_at_utc"],
            "source_uri": manifest["source_archive"]["source_uri"],
            "source_sha256": manifest["source_archive"]["content_sha256"],
            "reasons": ["DART_ITEM_EXTRACTION_POLICY_UNRATIFIED"],
        }
    if (
        content_row.get("content_status") != "NOT_APPLICABLE"
        or content_row.get("publication_status") != "NOT_APPLICABLE"
        or content_row.get("source_archive") is not None
        or content_row.get("documents") != []
        or content_row.get("extracted") != []
        or content_row.get("action") != "NO_CHANGE"
        or content_row.get("reasons") != ["STAGE_NOT_ASSIGNED_FOR_AUTO_CONSUMPTION"]
    ):
        raise DartEventObservationError(f"DART_CONTENT_BOUNDARY_INVALID:{ticker}:{rcept_no}")
    return {
        "status": "METADATA_ONLY_STAGE_NOT_ASSIGNED",
        "available_at": None,
        "source_uri": filing["url"],
        "source_sha256": None,
        "reasons": ["DART_CONTENT_NOT_APPLICABLE_STAGE_NOT_ASSIGNED"],
    }


def _failed_content_run_evidence(source_row: dict) -> dict:
    return {
        "status": "CONTENT_RUN_FAILED",
        "available_at": None,
        "source_uri": source_row["filing"]["url"],
        "source_sha256": None,
        "reasons": ["DART_CONTENT_RUN_FAILED"],
    }


def build_packet(
    *, decision_at: str, source_path: Path = DEFAULT_DART,
    content_path: Path = DEFAULT_CONTENT, data_root: Path = DEFAULT_DATA_ROOT,
) -> dict:
    decision = _utc(decision_at, "DECISION_AT_INVALID")
    source_path, content_path, data_root = Path(source_path), Path(content_path), Path(data_root)
    source_bytes = source_path.read_bytes()
    content_bytes = content_path.read_bytes()
    source = _read_json(source_path)
    content_run = _read_json(content_path)
    source_validation = _validate_source(source, decision)
    source_rows = source_validation["observations"]
    source_failures = source_validation["failures"]
    content_validation = _validate_content_run(content_run, source_bytes, source, decision)
    content_by_id = content_validation["records"]
    observations = []
    expected_ids = set()
    for row in source_rows:
        filing = row["filing"]
        key = (row["ticker"], filing["rcept_no"])
        expected_ids.add(key)
        if content_validation["run_status"] == "FAILED":
            evidence = _failed_content_run_evidence(row)
        elif key not in content_by_id:
            raise DartEventObservationError(f"DART_CONTENT_RECORD_MISSING:{key[0]}:{key[1]}")
        else:
            evidence = _content_evidence(data_root, row, content_by_id[key], decision)
        observation = {
            "schema_version": OBSERVATION_VERSION,
            "observation_id": f"DART_{filing['rcept_no']}_{row['ticker']}",
            "market": "KOREA",
            "subject_id": row["ticker"],
            "subject_name": row["name"],
            "source_kind": "DART_OPEN_API",
            "rcept_no": filing["rcept_no"],
            "filing_date": filing["date"],
            "filing_title": filing["title"],
            "filing_url": filing["url"],
            "event_at": None,
            "time_precision": "DATE_ONLY",
            "event_type": None,
            "direction": None,
            "importance": None,
            "evidence": evidence,
            "status": "OBSERVED_ESCALATION_BLOCKED",
            "blocked_reasons": sorted([
                "DART_EVENT_TYPE_INFERENCE_UNRATIFIED",
                "EVENT_TIMESTAMP_NOT_RETAINED_DATE_ONLY",
                "IMPORTANCE_POLICY_UNRATIFIED",
                *evidence["reasons"],
            ]),
        }
        observations.append(observation)
    extra = sorted(set(content_by_id) - expected_ids)
    if extra:
        raise DartEventObservationError(f"DART_CONTENT_RECORD_NOT_IN_SOURCE:{extra[0][0]}:{extra[0][1]}")
    observations.sort(key=lambda row: (row["filing_date"], row["rcept_no"], row["subject_id"]))
    content_failure_count = sum(
        row["evidence"]["status"] in {"CONTENT_CAPTURE_FAILED", "CONTENT_RUN_FAILED"}
        for row in observations
    )
    partial_failure = bool(source_failures or content_failure_count)
    packet = {
        "schema_version": SCHEMA_VERSION,
        "decision_at": decision_at,
        "source_date": source["collected_for_kst_date"],
        "status": (
            "DART_OBSERVATIONS_RECORDED_WITH_PARTIAL_FAILURES_ESCALATION_BLOCKED"
            if partial_failure
            else "DART_OBSERVATIONS_RECORDED_ESCALATION_BLOCKED"
        ),
        "lineage": {
            "source_path": _source_ref(source_path),
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "content_run_path": _source_ref(content_path),
            "content_run_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "content_contract_version": content_run["contract_version"],
        },
        "summary": {
            "source_ok_count": source["summary"]["ok"],
            "source_failed_count": source["summary"]["failed"],
            "relevant_filing_count": len(observations),
            "raw_bytes_verified_count": sum(
                row["evidence"]["status"] == "RAW_BYTES_VERIFIED_ITEM_EXTRACTION_UNRATIFIED"
                for row in observations
            ),
            "metadata_only_count": sum(
                row["evidence"]["status"] == "METADATA_ONLY_STAGE_NOT_ASSIGNED"
                for row in observations
            ),
            "content_failure_count": content_failure_count,
            "event_type_inferred_count": 0,
            "importance_classified_count": 0,
            "notification_sent_count": 0,
            "action_count": 0,
            "order_count": 0,
        },
        "source_failures": source_failures,
        "observations": observations,
        "authority": copy.deepcopy(AUTHORITY),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(
    packet: dict, *, source_path: Path = DEFAULT_DART,
    content_path: Path = DEFAULT_CONTENT, data_root: Path = DEFAULT_DATA_ROOT,
) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        raise DartEventObservationError("PACKET_SCHEMA_INVALID")
    expected = build_packet(
        decision_at=packet.get("decision_at"), source_path=source_path,
        content_path=content_path, data_root=data_root,
    )
    if packet != expected:
        raise DartEventObservationError("PACKET_DRIFT_OR_TAMPER")
    return copy.deepcopy(packet)


def publish_append_only(packet: dict, *, out_root: Path = DEFAULT_OUT_ROOT) -> tuple[Path, bool]:
    raw = (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    target = Path(out_root) / packet["source_date"] / f"packet-{packet['packet_sha256'][:16]}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if target.read_bytes() != raw:
            raise DartEventObservationError(f"CONTENT_ADDRESSED_PACKET_DRIFT:{target}")
        return target, False
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return target, True


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_DART)
    parser.add_argument("--content", type=Path, default=DEFAULT_CONTENT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = parser.parse_args(argv)
    packet = build_packet(
        decision_at=args.decision_at, source_path=args.source,
        content_path=args.content, data_root=args.data_root,
    )
    validate_packet(
        packet, source_path=args.source, content_path=args.content,
        data_root=args.data_root,
    )
    target, created = publish_append_only(packet, out_root=args.out_root)
    print(json.dumps({
        "status": "published" if created else "verified_existing",
        "path": target.as_posix(),
        "packet_sha256": packet["packet_sha256"],
        "summary": packet["summary"],
        "authority": packet["authority"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    try:
        return run()
    except (DartEventObservationError, DART.DartContentError, OSError) as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
