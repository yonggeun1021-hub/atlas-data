#!/usr/bin/env python3
"""Validate and compare append-only DefiLlama stablecoin captures.

The endpoint contract deliberately separates historical-series revision events
from changes in call-time live snapshots.  It never calls DefiLlama, computes a
Regime score, or rewrites captured response bytes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "stablecoin_endpoint_contract.json"
RAW_ROOT = ROOT / "evidence" / "stablecoin" / "raw"
SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$")
VERSION = re.compile(r"^stablecoin-capture/v[1-9][0-9]*$")
UTC = dt.timezone.utc


class ContractError(RuntimeError):
    """Fail-closed stablecoin evidence contract violation."""


def fail(code: str, detail: str) -> None:
    raise ContractError(f"{code}: {detail}")


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", str(exc))

    if contract.get("schema_version") != 1:
        fail("CONTRACT_INVALID", "schema_version must be 1")
    if contract.get("capture_mode") != "direct_fetch_append_only":
        fail("CONTRACT_INVALID", "capture_mode must be direct_fetch_append_only")
    try:
        coverage_start = dt.date.fromisoformat(contract["pit_coverage_start"])
        manifest_start = dt.date.fromisoformat(
            contract["manifest_required_from"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        fail("CONTRACT_INVALID", f"invalid policy date: {exc}")
    if manifest_start < coverage_start:
        fail("CONTRACT_INVALID", "manifest date precedes PIT coverage")
    endpoints = contract.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        fail("CONTRACT_INVALID", "endpoints must be a non-empty list")

    required = {
        "name",
        "raw_file",
        "endpoint",
        "semantics",
        "payload_kind",
        "records_path",
        "primary_key",
        "minimum_records",
        "required_fields",
    }
    for item in endpoints:
        if not isinstance(item, dict) or set(item) != required:
            fail("CONTRACT_INVALID", "endpoint fields do not match schema")
        if not all(
            isinstance(item[field], str) and item[field]
            for field in ("name", "raw_file", "endpoint", "primary_key")
        ):
            fail("CONTRACT_INVALID", "endpoint identity fields must be strings")
        if "/" in item["raw_file"] or not item["raw_file"].endswith(
            ".json.gz"
        ):
            fail("CONTRACT_INVALID", f"invalid raw_file: {item['raw_file']}")
        if not item["endpoint"].startswith("https://"):
            fail("CONTRACT_INVALID", "endpoint must use https")
        if item["payload_kind"] not in {"array", "object_array"}:
            fail("CONTRACT_INVALID", "unknown payload_kind")
        if item["payload_kind"] == "object_array" and not isinstance(
            item["records_path"], str
        ):
            fail("CONTRACT_INVALID", "object_array requires records_path")
        if item["payload_kind"] == "array" and item["records_path"] is not None:
            fail("CONTRACT_INVALID", "array records_path must be null")
        if not isinstance(item["minimum_records"], int) or item[
            "minimum_records"
        ] < 1:
            fail("CONTRACT_INVALID", "minimum_records must be positive")
        fields = item["required_fields"]
        if not isinstance(fields, list) or not fields or not all(
            isinstance(field, str) and field for field in fields
        ):
            fail("CONTRACT_INVALID", "required_fields must be strings")
        if item["primary_key"] not in fields:
            fail("CONTRACT_INVALID", "primary_key must be required")

    names = [item["name"] for item in endpoints]
    raw_files = [item["raw_file"] for item in endpoints]
    if len(names) != len(set(names)) or len(raw_files) != len(set(raw_files)):
        fail("CONTRACT_INVALID", "endpoint names and raw files must be unique")
    if set(item.get("semantics") for item in endpoints) - {
        "historical_series",
        "live_snapshot",
    }:
        fail("CONTRACT_INVALID", "unknown endpoint semantics")
    return contract


def parse_snapshot_date(snapshot_dir: Path) -> dt.date:
    if not snapshot_dir.is_dir():
        fail(
            "NO_VINTAGE_MECHANISM",
            f"snapshot directory is missing: {snapshot_dir}",
        )
    try:
        return dt.date.fromisoformat(snapshot_dir.name)
    except ValueError:
        fail("SNAPSHOT_DATE_INVALID", snapshot_dir.name)


def parse_downloaded_at(snapshot_dir: Path, snapshot_date: dt.date) -> str:
    path = snapshot_dir / "_downloaded_at.txt"
    try:
        value = path.read_text(encoding="utf-8").strip()
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OSError, ValueError) as exc:
        fail("FETCHED_AT_INVALID", str(exc))

    if not value.endswith("Z") or parsed.tzinfo is None:
        fail("FETCHED_AT_INVALID", "timestamp must be UTC ISO-8601 ending in Z")
    if parsed.astimezone(UTC).date() != snapshot_date:
        fail("FETCHED_AT_DATE_MISMATCH", value)
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_checksums(snapshot_dir: Path) -> dict[str, str]:
    path = snapshot_dir / "_sha256.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail("CHECKSUM_FILE_INVALID", str(exc))
    if not lines:
        fail("CHECKSUM_FILE_INVALID", "checksum file is empty")

    checksums = {}
    for line in lines:
        match = SHA_LINE.fullmatch(line)
        if match is None:
            fail("CHECKSUM_FILE_INVALID", f"malformed line: {line!r}")
        digest, filename = match.groups()
        if filename in checksums:
            fail("CHECKSUM_FILE_INVALID", f"duplicate filename: {filename}")
        checksums[filename] = digest
    return checksums


def read_response(snapshot_dir: Path, raw_file: str) -> tuple[bytes, object]:
    path = snapshot_dir / raw_file
    try:
        with gzip.open(path, "rb") as stream:
            raw = stream.read()
    except (OSError, EOFError) as exc:
        fail("RAW_RESPONSE_INVALID", f"{raw_file}: {exc}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail("RAW_RESPONSE_INVALID", f"{raw_file}: {exc}")
    return raw, payload


def endpoint_records(endpoint: dict, payload: object) -> list[dict]:
    kind = endpoint.get("payload_kind")
    if kind == "array":
        records = payload
    elif kind == "object_array" and isinstance(payload, dict):
        records = payload.get(endpoint.get("records_path"))
    else:
        records = None

    if not isinstance(records, list):
        fail("PAYLOAD_SHAPE_INVALID", endpoint["name"])
    if len(records) < endpoint.get("minimum_records", 0):
        fail(
            "PAYLOAD_TOO_SMALL",
            f"{endpoint['name']}: {len(records)} records",
        )

    required = set(endpoint.get("required_fields", []))
    primary_key = endpoint.get("primary_key")
    keys = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            fail("PAYLOAD_SHAPE_INVALID", f"{endpoint['name']} row {index}")
        missing = required - set(record)
        if missing:
            fail(
                "PAYLOAD_FIELD_MISSING",
                f"{endpoint['name']} row {index}: {sorted(missing)}",
            )
        key = record.get(primary_key)
        if not isinstance(key, str) or not key:
            fail(
                "PRIMARY_KEY_INVALID",
                f"{endpoint['name']} row {index}: {primary_key}",
            )
        if endpoint.get("semantics") == "historical_series" and not key.isdigit():
            fail("PRIMARY_KEY_INVALID", f"{endpoint['name']}: {key}")
        keys.append(key)
    if len(keys) != len(set(keys)):
        fail("PRIMARY_KEY_DUPLICATE", endpoint["name"])
    return records


def snapshot_core(snapshot_dir: Path, contract: dict) -> dict:
    snapshot_dir = Path(snapshot_dir)
    snapshot_date = parse_snapshot_date(snapshot_dir)
    fetched_at = parse_downloaded_at(snapshot_dir, snapshot_date)
    checksums = parse_checksums(snapshot_dir)
    expected_names = {
        Path(endpoint["raw_file"]).name.removesuffix(".gz")
        for endpoint in contract["endpoints"]
    }
    if set(checksums) != expected_names:
        fail(
            "CHECKSUM_INVENTORY_MISMATCH",
            f"expected={sorted(expected_names)} actual={sorted(checksums)}",
        )

    endpoint_results = []
    payloads = {}
    for endpoint in contract["endpoints"]:
        raw, payload = read_response(snapshot_dir, endpoint["raw_file"])
        checksum_name = Path(endpoint["raw_file"]).name.removesuffix(".gz")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != checksums[checksum_name]:
            fail("CHECKSUM_MISMATCH", endpoint["name"])
        records = endpoint_records(endpoint, payload)
        endpoint_results.append(
            {
                "name": endpoint["name"],
                "endpoint": endpoint["endpoint"],
                "semantics": endpoint["semantics"],
                "raw_file": endpoint["raw_file"],
                "response_sha256": digest,
                "byte_length": len(raw),
                "record_count": len(records),
            }
        )
        payloads[endpoint["name"]] = payload

    return {
        "snapshot_date": snapshot_date.isoformat(),
        "fetched_at_utc": fetched_at,
        "endpoints": endpoint_results,
        "payloads": payloads,
    }


def expected_manifest(core: dict, contract: dict, collector_version: str) -> dict:
    return {
        "schema_version": 1,
        "snapshot_date": core["snapshot_date"],
        "capture_mode": contract["capture_mode"],
        "pit_coverage_start": contract["pit_coverage_start"],
        "endpoints": [
            {
                "name": item["name"],
                "endpoint": item["endpoint"],
                "semantics": item["semantics"],
                "raw_file": item["raw_file"],
                "fetched_at_utc": core["fetched_at_utc"],
                "response_sha256": item["response_sha256"],
                "byte_length": item["byte_length"],
                "collector_version": collector_version,
            }
            for item in core["endpoints"]
        ],
    }


def build_manifest(
    snapshot_dir: Path,
    collector_version: str,
    contract: dict | None = None,
) -> Path:
    if VERSION.fullmatch(collector_version) is None:
        fail("COLLECTOR_VERSION_INVALID", collector_version)
    contract = load_contract() if contract is None else contract
    core = snapshot_core(Path(snapshot_dir), contract)
    target = Path(snapshot_dir) / "_manifest.json"
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", f"manifest already exists: {target}")

    payload = expected_manifest(core, contract, collector_version)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def validate_manifest(
    snapshot_dir: Path,
    core: dict,
    contract: dict,
) -> str:
    path = snapshot_dir / "_manifest.json"
    required_from = dt.date.fromisoformat(contract["manifest_required_from"])
    snapshot_date = dt.date.fromisoformat(core["snapshot_date"])
    if not path.exists():
        if snapshot_date >= required_from:
            fail("MANIFEST_REQUIRED", core["snapshot_date"])
        return "legacy_pre_manifest"

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("MANIFEST_INVALID", str(exc))
    endpoints = manifest.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints or not all(
        isinstance(item, dict) for item in endpoints
    ):
        fail("MANIFEST_INVALID", "endpoints must be a non-empty object list")
    version_values = [item.get("collector_version") for item in endpoints]
    if not all(isinstance(value, str) for value in version_values):
        fail("COLLECTOR_VERSION_INVALID", str(version_values))
    versions = set(version_values)
    if len(versions) != 1:
        fail("MANIFEST_INVALID", "one collector_version is required")
    collector_version = next(iter(versions))
    if not isinstance(collector_version, str) or VERSION.fullmatch(
        collector_version
    ) is None:
        fail("COLLECTOR_VERSION_INVALID", str(collector_version))
    if manifest != expected_manifest(core, contract, collector_version):
        fail("MANIFEST_MISMATCH", core["snapshot_date"])
    return "complete"


def validate_snapshot(
    snapshot_dir: Path,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    snapshot_dir = Path(snapshot_dir)
    core = snapshot_core(snapshot_dir, contract)
    metadata_status = validate_manifest(snapshot_dir, core, contract)
    return {
        "schema_version": 1,
        "snapshot_date": core["snapshot_date"],
        "pit_status": "qualified_direct_capture",
        "metadata_status": metadata_status,
        "endpoints": core["endpoints"],
    }


def index_records(endpoint: dict, payload: object) -> dict[str, dict]:
    records = endpoint_records(endpoint, payload)
    key = endpoint["primary_key"]
    return {record[key]: record for record in records}


def compare_historical(endpoint: dict, previous: object, current: object) -> dict:
    old = index_records(endpoint, previous)
    new = index_records(endpoint, current)
    old_keys = set(old)
    new_keys = set(new)
    overlap = old_keys & new_keys
    revised = sorted(
        (key for key in overlap if old[key] != new[key]),
        key=int,
    )
    removed = sorted(old_keys - new_keys, key=int)
    old_max = max((int(key) for key in old_keys), default=-1)
    added = new_keys - old_keys
    backfilled = sorted((key for key in added if int(key) <= old_max), key=int)
    appended = sorted((key for key in added if int(key) > old_max), key=int)

    events = []
    if revised:
        events.append("historical_revision")
    if removed:
        events.append("historical_reindex")
    if backfilled:
        events.append("historical_backfill")
    if appended:
        events.append("forward_append")
    if not events:
        events.append("unchanged")
    return {
        "semantics": "historical_series",
        "events": events,
        "counts": {
            "overlap": len(overlap),
            "revised": len(revised),
            "removed": len(removed),
            "backfilled": len(backfilled),
            "appended": len(appended),
        },
        "dates": {
            "revised": revised,
            "removed": removed,
            "backfilled": backfilled,
            "appended": appended,
        },
    }


def compare_live_snapshot(
    endpoint: dict,
    previous: object,
    current: object,
) -> dict:
    old = index_records(endpoint, previous)
    new = index_records(endpoint, current)
    old_keys = set(old)
    new_keys = set(new)
    overlap = old_keys & new_keys
    changed = sorted(key for key in overlap if old[key] != new[key])
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    is_changed = bool(changed or added or removed)
    return {
        "semantics": "live_snapshot",
        "event": "snapshot_changed" if is_changed else "snapshot_unchanged",
        "revision_inference": "not_applicable",
        "counts": {
            "changed": len(changed),
            "added": len(added),
            "removed": len(removed),
        },
    }


def compare_snapshots(
    previous_dir: Path,
    current_dir: Path,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    previous_dir = Path(previous_dir)
    current_dir = Path(current_dir)
    previous_validation = validate_snapshot(previous_dir, contract)
    current_validation = validate_snapshot(current_dir, contract)
    if current_validation["snapshot_date"] <= previous_validation["snapshot_date"]:
        fail("SNAPSHOT_ORDER_INVALID", "current must be later than previous")

    previous = snapshot_core(previous_dir, contract)
    current = snapshot_core(current_dir, contract)
    comparisons = {}
    for endpoint in contract["endpoints"]:
        name = endpoint["name"]
        if endpoint["semantics"] == "historical_series":
            result = compare_historical(
                endpoint,
                previous["payloads"][name],
                current["payloads"][name],
            )
        else:
            result = compare_live_snapshot(
                endpoint,
                previous["payloads"][name],
                current["payloads"][name],
            )
        old_meta = next(item for item in previous["endpoints"] if item["name"] == name)
        new_meta = next(item for item in current["endpoints"] if item["name"] == name)
        result["response_sha_changed"] = (
            old_meta["response_sha256"] != new_meta["response_sha256"]
        )
        comparisons[name] = result

    return {
        "schema_version": 1,
        "previous_snapshot_date": previous_validation["snapshot_date"],
        "current_snapshot_date": current_validation["snapshot_date"],
        "pit_policy": "direct_capture_only",
        "endpoints": comparisons,
    }


def json_print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def run(argv=None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--snapshot-dir", type=Path, required=True)
    manifest.add_argument("--collector-version", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("snapshot_dir", type=Path)

    compare = subparsers.add_parser("compare")
    compare.add_argument("previous_dir", type=Path)
    compare.add_argument("current_dir", type=Path)

    validate_all = subparsers.add_parser("validate-all")
    validate_all.add_argument("--raw-root", type=Path, default=RAW_ROOT)

    args = parser.parse_args(argv)
    if args.command == "manifest":
        target = build_manifest(args.snapshot_dir, args.collector_version)
        print(target)
    elif args.command == "validate":
        json_print(validate_snapshot(args.snapshot_dir))
    elif args.command == "compare":
        json_print(compare_snapshots(args.previous_dir, args.current_dir))
    else:
        if not args.raw_root.is_dir():
            fail(
                "NO_VINTAGE_MECHANISM",
                f"raw root is missing: {args.raw_root}",
            )
        snapshots = [
            validate_snapshot(path)
            for path in sorted(args.raw_root.iterdir())
            if path.is_dir()
        ]
        json_print({"schema_version": 1, "snapshots": snapshots})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except ContractError as exc:
        print(f"stablecoin contract FAIL: {exc}")
        raise SystemExit(1)
