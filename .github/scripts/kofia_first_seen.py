#!/usr/bin/env python3
"""Capture immutable KOFIA API evidence without inventing availability.

``first_seen`` probes exact recent calendar dates several times a day.  A row's
first-seen time means only the earliest Atlas capture with the same date and
content hash.  ``full_coverage`` paginates the current API response and records
the observed min/max dates.  Neither is an official release-time or durable
historical-range guarantee.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import kofia_liquidity as liquidity  # noqa: E402


CAPTURE_CONTRACT_PATH = ROOT / "config" / "kofia_first_seen_contract.json"
UTC = dt.timezone.utc
KST = ZoneInfo("Asia/Seoul")
RUN_ID = re.compile(r"^[1-9][0-9]*$")
CAPTURE_MODES = frozenset({"first_seen", "full_coverage"})


class CaptureError(RuntimeError):
    """Fail-closed KOFIA capture contract violation."""


def fail(code: str, detail: str) -> None:
    raise CaptureError(f"{code}: {detail}")


def load_capture_contract(path: Path = CAPTURE_CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CAPTURE_CONTRACT_INVALID", str(exc))
    expected = {
        "schema_version",
        "collector_version",
        "source_contract_version",
        "capture_mode",
        "probe_lookback_calendar_days",
        "probe_page_size",
        "full_coverage_page_size",
        "scheduled_slots_kst",
        "first_seen_semantics",
        "observed_range_semantics",
        "historical_range_status",
        "source_release_time_status",
        "api_field_unit_status",
        "available_at",
        "decision_eligible",
        "regime_score_authorized",
        "production_wiring_authorized",
        "trading_action_authorized",
    }
    if set(contract) != expected or contract.get("schema_version") != 1:
        fail("CAPTURE_CONTRACT_INVALID", "schema or fields")
    if contract["collector_version"] != "kofia-first-seen-capture/v1":
        fail("CAPTURE_CONTRACT_INVALID", "collector_version")
    if contract["source_contract_version"] != "kofia_liquidity_source/v1":
        fail("CAPTURE_CONTRACT_INVALID", "source contract")
    if contract["capture_mode"] != "direct_fetch_append_only":
        fail("CAPTURE_CONTRACT_INVALID", "capture mode")
    if contract["probe_lookback_calendar_days"] < 3:
        fail("CAPTURE_CONTRACT_INVALID", "lookback too small")
    if contract["probe_page_size"] < 1 or contract["full_coverage_page_size"] < 1:
        fail("CAPTURE_CONTRACT_INVALID", "page size")
    if contract["available_at"] is not None:
        fail("CAPTURE_CONTRACT_INVALID", "available_at must be null")
    for key in (
        "decision_eligible",
        "regime_score_authorized",
        "production_wiring_authorized",
        "trading_action_authorized",
    ):
        if contract[key] is not False:
            fail("CAPTURE_CONTRACT_INVALID", f"{key} must remain false")
    return contract


def parse_captured_at(value: str) -> dt.datetime:
    try:
        return liquidity.parse_captured_at(value)
    except liquidity.KofiaContractError as exc:
        fail("CAPTURE_TIME_INVALID", str(exc))


def positive_id(value: str, label: str) -> str:
    value = str(value or "").strip()
    if RUN_ID.fullmatch(value) is None:
        fail("CAPTURE_ID_INVALID", label)
    return value


def operation_map(source_contract: dict) -> dict[str, dict]:
    return {item["name"]: item for item in source_contract["operations"]}


def build_request(
    service_key: str,
    operation: dict,
    source_contract: dict,
    page_no: int,
    page_size: int,
    query_date: str | None = None,
) -> Request:
    key = unquote(str(service_key or "").strip())
    if not key:
        fail("SERVICE_KEY_MISSING", "DATA_GO_KR_SERVICE_KEY")
    if page_no < 1 or page_size < 1:
        fail("REQUEST_PAGINATION_INVALID", operation["name"])
    params = {
        "serviceKey": key,
        "resultType": "json",
        "pageNo": page_no,
        "numOfRows": page_size,
    }
    if query_date is not None:
        try:
            dt.datetime.strptime(query_date, "%Y%m%d")
        except (TypeError, ValueError):
            fail("REQUEST_DATE_INVALID", str(query_date))
        params["basDt"] = query_date
    url = source_contract["base_url"] + operation["path"]
    return Request(
        url + "?" + urlencode(params),
        headers={
            "Accept": "application/json",
            "User-Agent": "Atlas-P1-KR-03-First-Seen/1.0",
        },
        method="GET",
    )


def inspect_request(request: Request) -> dict:
    parsed = urlparse(request.full_url)
    names = sorted(part.split("=", 1)[0] for part in parsed.query.split("&"))
    return {
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "path": parsed.path,
        "query_keys": names,
        "service_key_present": "serviceKey" in names,
        "safe_endpoint": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
    }


def fetch_raw(request: Request, opener=urlopen, timeout: int = 60) -> bytes:
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            raw = response.read()
    except HTTPError as exc:
        fail("SOURCE_HTTP_ERROR", str(exc.code))
    except URLError:
        fail("SOURCE_NETWORK_ERROR", "request failed")
    if status != 200:
        fail("SOURCE_HTTP_ERROR", str(status))
    if not raw:
        fail("SOURCE_RESPONSE_EMPTY", "zero bytes")
    return raw


def reject_echoed_service_key(raw: bytes, service_key: str) -> None:
    candidates = {
        str(service_key or "").strip(),
        unquote(str(service_key or "").strip()),
    }
    if any(value and value.encode("utf-8") in raw for value in candidates):
        fail("SERVICE_KEY_ECHOED_BY_SOURCE", "response persistence forbidden")


def decode_payload(raw: bytes) -> object:
    try:
        return json.loads(
            raw,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=liquidity.reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidOperation) as exc:
        fail("SOURCE_RESPONSE_INVALID", str(exc))


def parse_items(items: object, total_count: int, operation: str) -> list[dict]:
    if total_count == 0 and items in (None, "", {}):
        return []
    if not isinstance(items, dict) or set(items) != {"item"}:
        fail("SOURCE_ITEMS_INVALID", operation)
    rows = items["item"]
    if total_count == 0 and rows in (None, "", []):
        return []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        fail("SOURCE_ITEMS_INVALID", operation)
    return rows


def parse_page(
    raw: bytes,
    operation: dict,
    expected_page: int,
    expected_query_date: str | None = None,
) -> dict:
    try:
        root = liquidity.response_root(decode_payload(raw))
    except liquidity.KofiaContractError as exc:
        fail("SOURCE_RESPONSE_INVALID", str(exc))
    header = root.get("header")
    if not isinstance(header, dict) or set(header) != {"resultCode", "resultMsg"}:
        fail("SOURCE_HEADER_INVALID", operation["name"])
    if str(header["resultCode"]) != "00":
        fail("SOURCE_RESULT_ERROR", str(header["resultCode"]))

    body = root.get("body")
    if not isinstance(body, dict) or set(body) != {
        "numOfRows",
        "pageNo",
        "totalCount",
        "items",
    }:
        fail("SOURCE_BODY_INVALID", operation["name"])
    try:
        page_no = liquidity.parse_nonnegative_integer(body["pageNo"], "pageNo")
        page_size = liquidity.parse_nonnegative_integer(
            body["numOfRows"], "numOfRows"
        )
        total_count = liquidity.parse_nonnegative_integer(
            body["totalCount"], "totalCount"
        )
    except liquidity.KofiaContractError as exc:
        fail("SOURCE_PAGINATION_INVALID", str(exc))
    if page_no != expected_page or page_size < 1:
        fail("SOURCE_PAGINATION_INVALID", operation["name"])

    rows = parse_items(body["items"], total_count, operation["name"])
    required = set(operation["required_fields"])
    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != required:
            fail("SOURCE_ROW_SCHEMA_INVALID", f"{operation['name']} row {index}")
        try:
            observed = liquidity.parse_observation_date(
                row[operation["observation_date_field"]], operation["name"]
            )
        except liquidity.KofiaContractError as exc:
            fail("SOURCE_ROW_DATE_INVALID", str(exc))
        day = observed.strftime("%Y%m%d")
        if expected_query_date is not None and day != expected_query_date:
            fail("SOURCE_QUERY_DATE_MISMATCH", f"{expected_query_date}!={day}")
        values = {}
        for field in operation["required_fields"]:
            if field == operation["observation_date_field"]:
                continue
            try:
                value = liquidity.parse_nonnegative_number(
                    row[field], f"{operation['name']} {field}"
                )
            except liquidity.KofiaContractError as exc:
                fail("SOURCE_ROW_VALUE_INVALID", str(exc))
            values[field] = format(value, "f")
        normalized.append(
            {
                "observation_date": observed.isoformat(),
                "values": values,
            }
        )
    if expected_query_date is not None:
        if total_count != len(rows) or total_count > 1:
            fail("SOURCE_EXACT_DATE_COVERAGE_INVALID", operation["name"])
    elif len(rows) > page_size:
        fail("SOURCE_PAGINATION_INVALID", operation["name"])
    return {
        "page_no": page_no,
        "page_size": page_size,
        "total_count": total_count,
        "rows": normalized,
    }


def row_hash(row: dict) -> str:
    raw = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def write_gzip(raw: bytes, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
            stream.write(raw)


def manifest_entry(
    raw: bytes,
    relative_path: str,
    operation: dict,
    page: dict,
    query_date: str | None,
) -> dict:
    return {
        "operation": operation["name"],
        "operation_id": operation["operation_id"],
        "endpoint": operation["path"],
        "query_date": query_date,
        "page_no": page["page_no"],
        "page_size": page["page_size"],
        "total_count": page["total_count"],
        "returned_rows": len(page["rows"]),
        "raw_file": relative_path,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
    }


def read_prior_first_seen(evidence_root: Path) -> dict[tuple[str, str, str], str]:
    result = {}
    root = Path(evidence_root)
    if not root.exists():
        return result
    for path in sorted(root.glob("**/_observation.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            fail("PRIOR_EVIDENCE_INVALID", str(path))
        if payload.get("mode") != "first_seen":
            continue
        for operation, item in payload.get("operations", {}).items():
            for row in item.get("observed_rows", []):
                key = (operation, row["observation_date"], row["row_sha256"])
                seen = row["atlas_first_seen_at_utc"]
                result[key] = min(result.get(key, seen), seen)
    return result


def authority_fields(capture_contract: dict) -> dict:
    return {
        "historical_range_status": capture_contract["historical_range_status"],
        "source_release_time_status": capture_contract[
            "source_release_time_status"
        ],
        "api_field_unit_status": capture_contract["api_field_unit_status"],
        "available_at": capture_contract["available_at"],
        "atlas_first_seen_is_source_available_at": False,
        "decision_eligible": capture_contract["decision_eligible"],
        "regime_score_authorized": capture_contract[
            "regime_score_authorized"
        ],
        "production_wiring_authorized": capture_contract[
            "production_wiring_authorized"
        ],
        "trading_action_authorized": capture_contract[
            "trading_action_authorized"
        ],
    }


def build_probe_observation(
    parsed: dict[str, list[dict]],
    query_dates: list[str],
    captured_at: str,
    prior: dict[tuple[str, str, str], str],
    capture_contract: dict,
    source_contract: dict,
) -> dict:
    operations = operation_map(source_contract)
    output = {}
    for name in sorted(operations):
        rows_by_date = {}
        for page in parsed[name]:
            for row in page["rows"]:
                day = row["observation_date"]
                if day in rows_by_date:
                    fail("PROBE_DATE_DUPLICATE", f"{name} {day}")
                digest = row_hash(row)
                key = (name, day, digest)
                first_seen = prior.get(key, captured_at)
                try:
                    parsed_first_seen = parse_captured_at(first_seen)
                    parsed_capture = parse_captured_at(captured_at)
                except CaptureError:
                    fail("PRIOR_EVIDENCE_INVALID", f"{name} {day}")
                if parsed_first_seen > parsed_capture:
                    fail("PRIOR_EVIDENCE_AFTER_CAPTURE", f"{name} {day}")
                rows_by_date[day] = {
                    "observation_date": day,
                    "primary_value_field": operations[name]["primary_value_field"],
                    "primary_value_raw": row["values"][
                        operations[name]["primary_value_field"]
                    ],
                    "row_sha256": digest,
                    "atlas_first_seen_at_utc": first_seen,
                    "first_seen_semantics": capture_contract[
                        "first_seen_semantics"
                    ],
                }
        observed = [rows_by_date[key] for key in sorted(rows_by_date)]
        output[name] = {
            "latest_observation_date": (
                observed[-1]["observation_date"] if observed else None
            ),
            "observed_rows": observed,
            "missing_query_dates": [
                dt.datetime.strptime(day, "%Y%m%d").date().isoformat()
                for day in query_dates
                if dt.datetime.strptime(day, "%Y%m%d").date().isoformat()
                not in rows_by_date
            ],
        }
    return {
        "schema_version": 1,
        "contract_version": source_contract["contract_version"],
        "mode": "first_seen",
        "captured_at_utc": captured_at,
        "first_seen_authority": "atlas_observation_only_not_source_release_time",
        "operations": output,
        **authority_fields(capture_contract),
    }


def build_full_observation(
    parsed: dict[str, list[dict]],
    captured_at: str,
    capture_contract: dict,
    source_contract: dict,
) -> dict:
    operations = operation_map(source_contract)
    capture_kst_date = parse_captured_at(captured_at).astimezone(KST).date()
    output = {}
    for name in sorted(operations):
        pages = parsed[name]
        if not pages:
            fail("FULL_COVERAGE_OPERATION_MISSING", name)
        expected_total = pages[0]["total_count"]
        rows = [row for page in pages for row in page["rows"]]
        if any(page["total_count"] != expected_total for page in pages):
            fail("FULL_COVERAGE_TOTAL_CHANGED", name)
        dates = [row["observation_date"] for row in rows]
        if len(rows) != expected_total:
            fail("FULL_COVERAGE_INCOMPLETE", f"{name}: {len(rows)}/{expected_total}")
        if not rows:
            fail("FULL_COVERAGE_EMPTY", name)
        if len(dates) != len(set(dates)):
            fail("FULL_COVERAGE_DATE_DUPLICATE", name)
        rows.sort(key=lambda row: row["observation_date"])
        if dt.date.fromisoformat(rows[-1]["observation_date"]) > capture_kst_date:
            fail("FULL_COVERAGE_FUTURE_OBSERVATION", name)
        latest = rows[-1] if rows else None
        output[name] = {
            "observed_range_semantics": capture_contract[
                "observed_range_semantics"
            ],
            "row_count": len(rows),
            "earliest_observation_date": rows[0]["observation_date"] if rows else None,
            "latest_observation_date": latest["observation_date"] if latest else None,
            "primary_value_field": operations[name]["primary_value_field"],
            "latest_primary_value_raw": (
                latest["values"][operations[name]["primary_value_field"]]
                if latest
                else None
            ),
        }
    result = {
        "schema_version": 1,
        "contract_version": source_contract["contract_version"],
        "mode": "full_coverage",
        "captured_at_utc": captured_at,
        "coverage_evidence_status": "complete_response_observed",
        "observed_range_is_durable_source_guarantee": False,
        "operations": output,
        **authority_fields(capture_contract),
    }
    result["source_release_time_status"] = "unverified"
    return result


def capture(
    mode: str,
    output_dir: Path,
    captured_at: str,
    run_id: str,
    run_attempt: str,
    service_key: str,
    evidence_root: Path,
    opener=urlopen,
    capture_contract: dict | None = None,
    source_contract: dict | None = None,
) -> Path:
    if mode not in CAPTURE_MODES:
        fail("CAPTURE_MODE_INVALID", mode)
    capture_contract = (
        load_capture_contract() if capture_contract is None else capture_contract
    )
    source_contract = (
        liquidity.load_contract() if source_contract is None else source_contract
    )
    parsed_time = parse_captured_at(captured_at)
    captured_at = parsed_time.isoformat(timespec="seconds").replace("+00:00", "Z")
    run_id = positive_id(run_id, "run_id")
    run_attempt = positive_id(run_attempt, "run_attempt")
    output_dir = Path(output_dir)
    if output_dir.exists():
        fail("APPEND_ONLY_VIOLATION", str(output_dir))
    output_dir.mkdir(parents=True)

    operations = operation_map(source_contract)
    entries = []
    parsed = {name: [] for name in operations}
    if mode == "first_seen":
        days = capture_contract["probe_lookback_calendar_days"]
        kst_day = parsed_time.astimezone(KST).date()
        query_dates = [
            (kst_day - dt.timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(days)
        ]
        for name in sorted(operations):
            operation = operations[name]
            for query_date in query_dates:
                request = build_request(
                    service_key,
                    operation,
                    source_contract,
                    1,
                    capture_contract["probe_page_size"],
                    query_date,
                )
                raw = fetch_raw(request, opener=opener)
                reject_echoed_service_key(raw, service_key)
                page = parse_page(raw, operation, 1, query_date)
                relative = f"raw/{name}/{query_date}.json.gz"
                write_gzip(raw, output_dir / relative)
                entries.append(
                    manifest_entry(raw, relative, operation, page, query_date)
                )
                parsed[name].append(page)
        prior = read_prior_first_seen(evidence_root)
        observation = build_probe_observation(
            parsed,
            query_dates,
            captured_at,
            prior,
            capture_contract,
            source_contract,
        )
    else:
        query_dates = []
        page_size = capture_contract["full_coverage_page_size"]
        for name in sorted(operations):
            operation = operations[name]
            page_no = 1
            collected = 0
            expected_total = None
            while True:
                request = build_request(
                    service_key, operation, source_contract, page_no, page_size
                )
                raw = fetch_raw(request, opener=opener)
                reject_echoed_service_key(raw, service_key)
                page = parse_page(raw, operation, page_no)
                if expected_total is None:
                    expected_total = page["total_count"]
                elif page["total_count"] != expected_total:
                    fail("FULL_COVERAGE_TOTAL_CHANGED", name)
                relative = f"raw/{name}/page-{page_no:04d}.json.gz"
                write_gzip(raw, output_dir / relative)
                entries.append(manifest_entry(raw, relative, operation, page, None))
                parsed[name].append(page)
                collected += len(page["rows"])
                if collected >= expected_total:
                    break
                if not page["rows"]:
                    fail("FULL_COVERAGE_PAGINATION_STALLED", name)
                page_no += 1
        observation = build_full_observation(
            parsed, captured_at, capture_contract, source_contract
        )

    manifest = {
        "schema_version": 1,
        "collector_version": capture_contract["collector_version"],
        "source_contract_version": source_contract["contract_version"],
        "capture_mode": capture_contract["capture_mode"],
        "mode": mode,
        "captured_at_utc": captured_at,
        "capture_date_kst": parsed_time.astimezone(KST).date().isoformat(),
        "github": {"run_id": run_id, "run_attempt": run_attempt},
        "raw_responses": entries,
        "service_key_persisted": False,
    }
    (output_dir / "_captured_at.txt").write_text(
        captured_at + "\n", encoding="utf-8"
    )
    (output_dir / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "_observation.json").write_text(
        json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_dir


def validate_capture(
    capture_dir: Path,
    evidence_root: Path,
    capture_contract: dict | None = None,
    source_contract: dict | None = None,
) -> dict:
    capture_contract = (
        load_capture_contract() if capture_contract is None else capture_contract
    )
    source_contract = (
        liquidity.load_contract() if source_contract is None else source_contract
    )
    capture_dir = Path(capture_dir)
    try:
        manifest = json.loads(
            (capture_dir / "_manifest.json").read_text(encoding="utf-8")
        )
        observation = json.loads(
            (capture_dir / "_observation.json").read_text(encoding="utf-8")
        )
        captured_at = (capture_dir / "_captured_at.txt").read_text(
            encoding="utf-8"
        ).strip()
    except (OSError, json.JSONDecodeError) as exc:
        fail("CAPTURE_FILES_INVALID", str(exc))
    if manifest.get("captured_at_utc") != captured_at:
        fail("CAPTURE_TIME_MISMATCH", capture_dir.name)
    parsed_time = parse_captured_at(captured_at)
    if manifest.get("capture_date_kst") != parsed_time.astimezone(KST).date().isoformat():
        fail("CAPTURE_DATE_MISMATCH", capture_dir.name)
    if manifest.get("collector_version") != capture_contract["collector_version"]:
        fail("MANIFEST_INVALID", "collector_version")
    if manifest.get("source_contract_version") != source_contract["contract_version"]:
        fail("MANIFEST_INVALID", "source_contract_version")
    if manifest.get("service_key_persisted") is not False:
        fail("MANIFEST_INVALID", "service key boundary")
    if set(manifest) != {
        "schema_version",
        "collector_version",
        "source_contract_version",
        "capture_mode",
        "mode",
        "captured_at_utc",
        "capture_date_kst",
        "github",
        "raw_responses",
        "service_key_persisted",
    } or manifest.get("schema_version") != 1:
        fail("MANIFEST_INVALID", "schema or fields")
    mode = manifest.get("mode")
    if mode not in CAPTURE_MODES or observation.get("mode") != mode:
        fail("MANIFEST_INVALID", "mode")
    positive_id(manifest.get("github", {}).get("run_id"), "run_id")
    positive_id(manifest.get("github", {}).get("run_attempt"), "run_attempt")

    operations = operation_map(source_contract)
    parsed = {name: [] for name in operations}
    query_dates = []
    entries = manifest.get("raw_responses")
    if not isinstance(entries, list) or not entries:
        fail("MANIFEST_INVALID", "raw responses")
    raw_paths = set()
    for entry in entries:
        if not isinstance(entry, dict):
            fail("MANIFEST_INVALID", "raw entry")
        if set(entry) != {
            "operation",
            "operation_id",
            "endpoint",
            "query_date",
            "page_no",
            "page_size",
            "total_count",
            "returned_rows",
            "raw_file",
            "response_sha256",
            "byte_length",
        } or not isinstance(entry.get("page_no"), int):
            fail("MANIFEST_INVALID", "raw entry schema")
        name = entry.get("operation")
        if name not in operations:
            fail("MANIFEST_INVALID", "operation")
        relative = entry.get("raw_file")
        if not isinstance(relative, str):
            fail("MANIFEST_INVALID", "raw path")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            fail("MANIFEST_INVALID", "raw path escape")
        expected_relative = (
            f"raw/{name}/{entry.get('query_date')}.json.gz"
            if mode == "first_seen"
            else f"raw/{name}/page-{entry.get('page_no'):04d}.json.gz"
        )
        if relative != expected_relative or relative in raw_paths:
            fail("MANIFEST_INVALID", "raw path identity")
        raw_paths.add(relative)
        raw_path = capture_dir / relative_path
        try:
            with gzip.open(raw_path, "rb") as stream:
                raw = stream.read()
        except (OSError, EOFError) as exc:
            fail("RAW_RESPONSE_INVALID", str(exc))
        if hashlib.sha256(raw).hexdigest() != entry.get("response_sha256"):
            fail("RAW_RESPONSE_HASH_MISMATCH", entry["raw_file"])
        if len(raw) != entry.get("byte_length"):
            fail("RAW_RESPONSE_LENGTH_MISMATCH", entry["raw_file"])
        page = parse_page(
            raw,
            operations[name],
            entry["page_no"],
            entry.get("query_date"),
        )
        expected = manifest_entry(
            raw,
            entry["raw_file"],
            operations[name],
            page,
            entry.get("query_date"),
        )
        if entry != expected:
            fail("MANIFEST_ENTRY_MISMATCH", entry["raw_file"])
        parsed[name].append(page)
        if entry.get("query_date") is not None:
            query_dates.append(entry["query_date"])

    if mode == "first_seen":
        unique_dates = sorted(set(query_dates), reverse=True)
        expected_observation = build_probe_observation(
            parsed,
            unique_dates,
            captured_at,
            read_prior_first_seen(evidence_root),
            capture_contract,
            source_contract,
        )
    else:
        expected_observation = build_full_observation(
            parsed, captured_at, capture_contract, source_contract
        )
    if observation != expected_observation:
        fail("OBSERVATION_MISMATCH", capture_dir.name)
    serialized = json.dumps(manifest) + json.dumps(observation)
    if "serviceKey" in serialized or "DATA_GO_KR_SERVICE_KEY" in serialized:
        fail("SERVICE_KEY_PERSISTENCE_FORBIDDEN", capture_dir.name)
    return observation


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--mode", choices=sorted(CAPTURE_MODES), required=True)
    capture_parser.add_argument("--output-dir", type=Path, required=True)
    capture_parser.add_argument("--captured-at", required=True)
    capture_parser.add_argument("--run-id", required=True)
    capture_parser.add_argument("--run-attempt", required=True)
    capture_parser.add_argument("--evidence-root", type=Path, required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("capture_dir", type=Path)
    validate_parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "capture":
        service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
        path = capture(
            args.mode,
            args.output_dir,
            args.captured_at,
            args.run_id,
            args.run_attempt,
            service_key,
            args.evidence_root,
        )
        print(f"KOFIA capture complete mode={args.mode} path={path}")
        return 0
    observation = validate_capture(args.capture_dir, args.evidence_root)
    print(
        "KOFIA capture PASS"
        f" mode={observation['mode']}"
        f" captured_at={observation['captured_at_utc']}"
        " available_at=null decision_eligible=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
