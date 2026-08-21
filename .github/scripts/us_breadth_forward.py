#!/usr/bin/env python3
"""Validate append-only Nasdaq Symbol Directory captures and membership diffs.

The official files describe the current trading day's directory and are updated
intraday.  They are valid only from the day Atlas captures them forward.  This
helper never carries current membership backward, fetches prices, calculates
advance/decline breadth, or grants Regime/Production/trading authority.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "us_breadth_forward_contract.json"
RAW_ROOT = ROOT / "evidence" / "us_breadth" / "raw"
UTC = dt.timezone.utc
SHA_LINE = re.compile(r"^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$")
CREATION_LINE = re.compile(r"^File Creation Time: ([0-9]{10}:[0-9]{2})$")
COLLECTOR_VERSION = re.compile(r"^us-breadth-forward-capture/v[1-9][0-9]*$")
DATE_DIR = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class ContractError(RuntimeError):
    """Fail-closed forward US breadth source-contract violation."""


def fail(code: str, detail: str) -> None:
    raise ContractError(f"{code}: {detail}")


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", str(exc))

    if contract.get("schema_version") != 1:
        fail("CONTRACT_INVALID", "schema_version must be 1")
    if contract.get("approval_status") != "FORWARD_ONLY_RATIFIED":
        fail("CONTRACT_INVALID", "only the forward-only scope is ratified")
    if contract.get("capture_mode") != "direct_fetch_append_only":
        fail("CONTRACT_INVALID", "capture must be append-only")
    if contract.get("historical_universe_policy") != (
        "as_captured_forward_only_no_current_state_backfill"
    ):
        fail("CONTRACT_INVALID", "historical backfill boundary is missing")

    sources = contract.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        fail("CONTRACT_INVALID", "exactly two official directory files required")
    required = {
        "name",
        "endpoint",
        "raw_file",
        "identity_field",
        "exchange_field",
        "minimum_records",
        "required_fields",
    }
    names = []
    raw_files = []
    for source in sources:
        if not isinstance(source, dict) or set(source) != required:
            fail("CONTRACT_INVALID", "source fields do not match schema")
        if not source["endpoint"].startswith("https://www.nasdaqtrader.com/"):
            fail("CONTRACT_INVALID", "source must remain on Nasdaq Trader HTTPS")
        if not source["raw_file"].endswith(".txt.gz") or "/" in source["raw_file"]:
            fail("CONTRACT_INVALID", "raw filename invalid")
        fields = source["required_fields"]
        if not isinstance(fields, list) or not fields:
            fail("CONTRACT_INVALID", "required_fields must be non-empty")
        if source["identity_field"] not in fields:
            fail("CONTRACT_INVALID", "identity field must be required")
        if source["exchange_field"] not in (None, *fields):
            fail("CONTRACT_INVALID", "exchange field must be null or required")
        if not isinstance(source["minimum_records"], int) or source[
            "minimum_records"
        ] < 1:
            fail("CONTRACT_INVALID", "minimum_records must be positive")
        names.append(source["name"])
        raw_files.append(source["raw_file"])
    if len(names) != len(set(names)) or len(raw_files) != len(set(raw_files)):
        fail("CONTRACT_INVALID", "source names and raw files must be unique")

    for authority in (
        "historical_reconstruction_authorized",
        "price_breadth_authorized",
        "breadth_classification_authorized",
        "threshold_authorized",
        "regime_score_authorized",
        "production_wiring_authorized",
        "trading_action_authorized",
    ):
        if contract.get(authority) is not False:
            fail("CONTRACT_INVALID", f"{authority} must remain false")
    checkpoint = contract.get("paid_data_checkpoint", {})
    if checkpoint.get("status") != "USER_RECONFIRMATION_REQUIRED":
        fail("CONTRACT_INVALID", "paid-data user checkpoint is missing")
    if checkpoint.get("approved") is not False:
        fail("CONTRACT_INVALID", "paid data must not be pre-approved")
    if not checkpoint.get("stop_before"):
        fail("CONTRACT_INVALID", "paid-data stop list is empty")
    return contract


def parse_directory_date(path: Path) -> dt.date:
    try:
        return dt.date.fromisoformat(Path(path).name)
    except ValueError:
        fail("SNAPSHOT_DATE_INVALID", Path(path).name)


def parse_downloaded_at(snapshot_dir: Path) -> str:
    path = Path(snapshot_dir) / "_downloaded_at.txt"
    try:
        value = path.read_text(encoding="utf-8").strip()
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OSError, ValueError) as exc:
        fail("FETCHED_AT_INVALID", str(exc))
    if not value.endswith("Z") or parsed.tzinfo is None:
        fail("FETCHED_AT_INVALID", "timestamp must be UTC ISO-8601 ending in Z")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_checksums(snapshot_dir: Path) -> dict[str, str]:
    try:
        lines = (Path(snapshot_dir) / "_sha256.txt").read_text(
            encoding="utf-8"
        ).splitlines()
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


def read_raw(snapshot_dir: Path, raw_file: str) -> bytes:
    try:
        with gzip.open(Path(snapshot_dir) / raw_file, "rb") as stream:
            return stream.read()
    except (OSError, EOFError) as exc:
        fail("RAW_FILE_INVALID", f"{raw_file}: {exc}")


def parse_source(raw: bytes, source: dict) -> dict:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        fail("SOURCE_ENCODING_INVALID", source["name"])
    lines = text.splitlines()
    if len(lines) < 3:
        fail("SOURCE_FILE_TRUNCATED", source["name"])
    if lines[-1] == "":
        lines.pop()
    if len(lines) < 3:
        fail("SOURCE_FILE_TRUNCATED", source["name"])

    header = next(csv.reader([lines[0]], delimiter="|"))
    if header != source["required_fields"]:
        fail("SOURCE_HEADER_MISMATCH", source["name"])
    footer = next(csv.reader([lines[-1]], delimiter="|"))
    match = CREATION_LINE.fullmatch(footer[0])
    if match is None or any(footer[1:]):
        fail("SOURCE_FOOTER_INVALID", source["name"])
    creation_raw = match.group(1)
    try:
        creation = dt.datetime.strptime(creation_raw, "%m%d%Y%H:%M")
    except ValueError:
        fail("SOURCE_CREATION_TIME_INVALID", creation_raw)

    rows = []
    identities = set()
    reader = csv.DictReader(io.StringIO("\n".join(lines[:-1])), delimiter="|")
    if reader.fieldnames != source["required_fields"]:
        fail("SOURCE_HEADER_MISMATCH", source["name"])
    for index, row in enumerate(reader, start=2):
        if None in row or set(row) != set(source["required_fields"]):
            fail("SOURCE_ROW_SHAPE_INVALID", f"{source['name']} row {index}")
        if any(value is None for value in row.values()):
            fail("SOURCE_ROW_SHAPE_INVALID", f"{source['name']} row {index}")
        symbol = row[source["identity_field"]].strip()
        if not symbol:
            fail("SOURCE_IDENTITY_EMPTY", f"{source['name']} row {index}")
        identity = f"{source['name']}:{symbol}"
        if identity in identities:
            fail("SOURCE_IDENTITY_DUPLICATE", identity)
        identities.add(identity)
        rows.append({key: value.strip() for key, value in row.items()})
    if len(rows) < source["minimum_records"]:
        fail("SOURCE_RECORD_COUNT_TOO_SMALL", f"{source['name']}: {len(rows)}")
    return {
        "name": source["name"],
        "creation_time_raw": creation_raw,
        "source_date": creation.date(),
        "rows": rows,
        "identities": identities,
    }


def snapshot_core(snapshot_dir: Path, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    snapshot_dir = Path(snapshot_dir)
    snapshot_date = parse_directory_date(snapshot_dir)
    fetched_at = parse_downloaded_at(snapshot_dir)
    checksums = parse_checksums(snapshot_dir)
    expected_checksums = {
        source["raw_file"].removesuffix(".gz") for source in contract["sources"]
    }
    if set(checksums) != expected_checksums:
        fail(
            "CHECKSUM_INVENTORY_MISMATCH",
            f"expected={sorted(expected_checksums)} actual={sorted(checksums)}",
        )

    parsed_sources = []
    members = {}
    source_counts = {}
    endpoint_results = []
    for source in contract["sources"]:
        raw = read_raw(snapshot_dir, source["raw_file"])
        checksum_name = source["raw_file"].removesuffix(".gz")
        digest = hashlib.sha256(raw).hexdigest()
        if checksums[checksum_name] != digest:
            fail("CHECKSUM_MISMATCH", source["name"])
        parsed = parse_source(raw, source)
        parsed_sources.append(parsed)
        source_counts[source["name"]] = len(parsed["rows"])
        for row in parsed["rows"]:
            symbol = row[source["identity_field"]]
            identity = f"{source['name']}:{symbol}"
            members[identity] = {
                "source": source["name"],
                "symbol": symbol,
                "security_name": row["Security Name"],
                "exchange": (
                    row[source["exchange_field"]]
                    if source["exchange_field"] is not None
                    else "NASDAQ"
                ),
                "test_issue": row["Test Issue"],
                "etf": row["ETF"],
            }
        endpoint_results.append(
            {
                "name": source["name"],
                "endpoint": source["endpoint"],
                "raw_file": source["raw_file"],
                "response_sha256": digest,
                "byte_length": len(raw),
                "record_count": len(parsed["rows"]),
                "source_file_creation_time_raw": parsed["creation_time_raw"],
            }
        )

    source_dates = {item["source_date"] for item in parsed_sources}
    if len(source_dates) != 1:
        fail("SOURCE_DATE_MISMATCH", repr(sorted(source_dates)))
    source_date = source_dates.pop()
    if source_date != snapshot_date:
        fail(
            "SNAPSHOT_SOURCE_DATE_MISMATCH",
            f"directory={snapshot_date} source={source_date}",
        )
    return {
        "snapshot_date": snapshot_date.isoformat(),
        "fetched_at_utc": fetched_at,
        "members": members,
        "source_counts": source_counts,
        "endpoints": endpoint_results,
    }


def expected_manifest(core: dict, contract: dict, collector_version: str) -> dict:
    return {
        "schema_version": 1,
        "snapshot_date": core["snapshot_date"],
        "capture_mode": contract["capture_mode"],
        "source_semantics": contract["source_semantics"],
        "historical_universe_policy": contract["historical_universe_policy"],
        "universe_semantics": contract["universe_semantics"],
        "fetched_at_utc": core["fetched_at_utc"],
        "collector_version": collector_version,
        "endpoints": core["endpoints"],
        "paid_data_checkpoint": contract["paid_data_checkpoint"],
    }


def build_manifest(
    snapshot_dir: Path,
    collector_version: str,
    contract: dict | None = None,
) -> Path:
    if COLLECTOR_VERSION.fullmatch(collector_version) is None:
        fail("COLLECTOR_VERSION_INVALID", collector_version)
    contract = load_contract() if contract is None else contract
    core = snapshot_core(Path(snapshot_dir), contract)
    target = Path(snapshot_dir) / "_manifest.json"
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
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


def validate_manifest(snapshot_dir: Path, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    snapshot_dir = Path(snapshot_dir)
    try:
        manifest = json.loads(
            (snapshot_dir / "_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail("MANIFEST_INVALID", str(exc))
    version = manifest.get("collector_version")
    if not isinstance(version, str) or COLLECTOR_VERSION.fullmatch(version) is None:
        fail("MANIFEST_INVALID", "collector_version invalid")
    core = snapshot_core(snapshot_dir, contract)
    if manifest != expected_manifest(core, contract, version):
        fail("MANIFEST_MISMATCH", snapshot_dir.name)
    return core


def authority_boundary(contract: dict) -> dict:
    return {
        "historical_reconstruction_authorized": contract[
            "historical_reconstruction_authorized"
        ],
        "price_breadth_authorized": contract["price_breadth_authorized"],
        "breadth_classification_authorized": contract[
            "breadth_classification_authorized"
        ],
        "threshold_authorized": contract["threshold_authorized"],
        "regime_score_authorized": contract["regime_score_authorized"],
        "production_wiring_authorized": contract[
            "production_wiring_authorized"
        ],
        "trading_action_authorized": contract["trading_action_authorized"],
    }


def build_membership_diff(
    current: dict,
    previous: dict | None = None,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    current_ids = set(current["members"])
    result = {
        "schema_version": 1,
        "observation": "SOURCE_DIRECTORY_MEMBERSHIP_CHANGE_ONLY",
        "status": "BASELINE_ESTABLISHED" if previous is None else "PASS",
        "as_of_date": current["snapshot_date"],
        "previous_date": None if previous is None else previous["snapshot_date"],
        "universe_semantics": contract["universe_semantics"],
        "membership": {
            "previous_count": None,
            "current_count": len(current_ids),
            "unchanged_count": None,
            "entered_count": None,
            "exited_count": None,
            "changed_attributes_count": None,
            "entered_ids": None,
            "exited_ids": None,
            "changed_attribute_ids": None,
            "current_source_counts": current["source_counts"],
        },
        "price_breadth": {
            "status": "UNKNOWN_PRICE_SOURCE_UNAVAILABLE",
            "advancing_count": None,
            "declining_count": None,
            "unchanged_count": None,
            "advance_fraction": None,
        },
        "authority": authority_boundary(contract),
        "paid_data_checkpoint": contract["paid_data_checkpoint"],
    }
    if previous is None:
        return result
    if previous["snapshot_date"] >= current["snapshot_date"]:
        fail("SNAPSHOT_PAIR_NOT_ORDERED", "previous must precede current")
    previous_ids = set(previous["members"])
    shared = previous_ids & current_ids
    entered = sorted(current_ids - previous_ids)
    exited = sorted(previous_ids - current_ids)
    changed = sorted(
        identity
        for identity in shared
        if previous["members"][identity] != current["members"][identity]
    )
    result["membership"].update(
        {
            "previous_count": len(previous_ids),
            "unchanged_count": len(shared),
            "entered_count": len(entered),
            "exited_count": len(exited),
            "changed_attributes_count": len(changed),
            "entered_ids": entered,
            "exited_ids": exited,
            "changed_attribute_ids": changed,
            "previous_source_counts": previous["source_counts"],
        }
    )
    return result


def validate_snapshot_bundle(
    snapshot_dir: Path,
    previous_dir: Path | None = None,
    contract: dict | None = None,
) -> dict:
    """Revalidate an immutable snapshot and its exact membership diff."""

    contract = load_contract() if contract is None else contract
    snapshot_dir = Path(snapshot_dir)
    expected_files = {
        "_downloaded_at.txt",
        "_sha256.txt",
        "_manifest.json",
        "_membership_diff.json",
        *(source["raw_file"] for source in contract["sources"]),
    }
    try:
        children = list(snapshot_dir.iterdir())
    except OSError as exc:
        fail("BUNDLE_INVENTORY_INVALID", str(exc))
    actual_files = {child.name for child in children}
    if actual_files != expected_files:
        fail(
            "BUNDLE_INVENTORY_MISMATCH",
            f"expected={sorted(expected_files)} actual={sorted(actual_files)}",
        )
    if any(not child.is_file() or child.is_symlink() for child in children):
        fail("BUNDLE_INVENTORY_INVALID", "regular files only")

    current = validate_manifest(snapshot_dir, contract)
    manifest_path = snapshot_dir / "_manifest.json"
    try:
        manifest_raw = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_raw)
        downloaded_raw = (snapshot_dir / "_downloaded_at.txt").read_text(
            encoding="utf-8"
        )
        checksum_raw = (snapshot_dir / "_sha256.txt").read_text(
            encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail("BUNDLE_METADATA_INVALID", str(exc))
    expected_manifest_value = expected_manifest(
        current, contract, manifest["collector_version"]
    )
    expected_manifest_raw = (
        json.dumps(
            expected_manifest_value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if manifest_raw != expected_manifest_raw:
        fail("MANIFEST_BYTES_MISMATCH", snapshot_dir.name)
    if downloaded_raw != current["fetched_at_utc"] + "\n":
        fail("DOWNLOADED_AT_BYTES_MISMATCH", snapshot_dir.name)
    expected_checksum_raw = "".join(
        f"{endpoint['response_sha256']}  {endpoint['raw_file'].removesuffix('.gz')}\n"
        for endpoint in current["endpoints"]
    )
    if checksum_raw != expected_checksum_raw:
        fail("CHECKSUM_BYTES_MISMATCH", snapshot_dir.name)
    previous = None
    if previous_dir is not None:
        previous_dir = Path(previous_dir)
        if previous_dir.resolve() == snapshot_dir.resolve():
            fail("SNAPSHOT_PAIR_NOT_ORDERED", "previous equals current")
        previous = validate_manifest(previous_dir, contract)
    expected_diff = build_membership_diff(current, previous, contract)
    path = snapshot_dir / "_membership_diff.json"
    try:
        raw = path.read_text(encoding="utf-8")
        actual_diff = json.loads(
            raw,
            parse_constant=lambda value: fail(
                "MEMBERSHIP_DIFF_INVALID", f"constant={value}"
            ),
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail("MEMBERSHIP_DIFF_INVALID", str(exc))
    if not isinstance(actual_diff, dict):
        fail("MEMBERSHIP_DIFF_INVALID", "root must be object")
    if actual_diff != expected_diff:
        fail("MEMBERSHIP_DIFF_MISMATCH", snapshot_dir.name)
    expected_bytes = (
        json.dumps(expected_diff, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    if raw != expected_bytes:
        fail("MEMBERSHIP_DIFF_BYTES_MISMATCH", snapshot_dir.name)
    return current


def discover_archive_snapshots(raw_root: Path) -> list[Path]:
    """List every snapshot directory under raw_root in date order.

    Fails closed on any entry that is not a symlink-free directory named
    exactly YYYY-MM-DD: an unexpected file, a renamed/malformed directory,
    or a symlinked snapshot must stop archive-wide reconstruction rather
    than be silently skipped.
    """

    raw_root = Path(raw_root)
    if not raw_root.exists():
        return []
    if raw_root.is_symlink():
        fail("ARCHIVE_INVENTORY_INVALID", str(raw_root))
    snapshots = []
    for child in sorted(raw_root.iterdir(), key=lambda path: path.name):
        if child.is_symlink() or not child.is_dir() or (
            DATE_DIR.fullmatch(child.name) is None
        ):
            fail("ARCHIVE_INVENTORY_INVALID", str(child))
        snapshots.append(child)
    return snapshots


def replay_archive(
    raw_root: Path = RAW_ROOT, contract: dict | None = None
) -> list[dict]:
    """Revalidate every committed snapshot in captured order.

    Each bundle is validated against its true immediate predecessor in the
    archive -- never a caller-supplied one -- by walking the sorted archive
    and threading each snapshot_dir into the next call's previous_dir.
    validate_snapshot_bundle rebuilds the exact membership diff from that
    predecessor and requires a byte-identical match against the committed
    _membership_diff.json, so a snapshot whose persisted diff was built
    against any other baseline -- a skipped day, a deleted predecessor, a
    reordered or renamed capture -- fails closed on
    MEMBERSHIP_DIFF_MISMATCH before any of it can be trusted. An archive
    with no snapshots yet (the very first run) returns an empty chain.
    """

    contract = load_contract() if contract is None else contract
    chain = []
    previous_dir = None
    for snapshot_dir in discover_archive_snapshots(raw_root):
        chain.append(validate_snapshot_bundle(snapshot_dir, previous_dir, contract))
        previous_dir = snapshot_dir
    return chain


def universe_as_of(
    as_of_date: str, raw_root: Path = RAW_ROOT, contract: dict | None = None
) -> dict:
    """Reconstruct the as-captured universe on or before as_of_date.

    The archive is fully replayed first, so the returned membership is only
    ever drawn from a snapshot that survived every validate_snapshot_bundle
    check. Selection is strictly forward-fill: the latest snapshot with
    snapshot_date <= as_of_date, matching the ratified
    as_captured_forward_only_no_current_state_backfill boundary. A date
    before the archive's first snapshot is refused rather than backfilled
    from current membership.
    """

    contract = load_contract() if contract is None else contract
    try:
        target = dt.date.fromisoformat(as_of_date)
    except ValueError:
        fail("AS_OF_DATE_INVALID", as_of_date)
    chain = replay_archive(raw_root, contract)
    if not chain:
        fail("ARCHIVE_EMPTY", str(raw_root))
    eligible = [
        core
        for core in chain
        if dt.date.fromisoformat(core["snapshot_date"]) <= target
    ]
    if not eligible:
        fail(
            "AS_OF_BEFORE_ARCHIVE_BASELINE",
            f"{as_of_date} < {chain[0]['snapshot_date']}",
        )
    selected = eligible[-1]
    return {
        "as_of_date": as_of_date,
        "snapshot_date": selected["snapshot_date"],
        "historical_universe_policy": contract["historical_universe_policy"],
        "universe_semantics": contract["universe_semantics"],
        "members": selected["members"],
        "source_counts": selected["source_counts"],
    }


def write_json_append_only(payload: dict, target: Path) -> Path:
    target = Path(target)
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
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


def source_date_from_undated_capture(
    capture_dir: Path, contract: dict | None = None
) -> str:
    contract = load_contract() if contract is None else contract
    dates = set()
    for source in contract["sources"]:
        parsed = parse_source(read_raw(Path(capture_dir), source["raw_file"]), source)
        dates.add(parsed["source_date"])
    if len(dates) != 1:
        fail("SOURCE_DATE_MISMATCH", repr(sorted(dates)))
    return dates.pop().isoformat()


def run(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    source_date = sub.add_parser("source-date")
    source_date.add_argument("snapshot_dir", type=Path)

    manifest = sub.add_parser("manifest")
    manifest.add_argument("snapshot_dir", type=Path)
    manifest.add_argument("--collector-version", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("snapshot_dir", type=Path)

    validate_bundle = sub.add_parser("validate-bundle")
    validate_bundle.add_argument("snapshot_dir", type=Path)
    validate_bundle.add_argument("--previous-dir", type=Path)

    diff = sub.add_parser("diff")
    diff.add_argument("--current-dir", type=Path, required=True)
    diff.add_argument("--previous-dir", type=Path)
    diff.add_argument("--out", type=Path, required=True)

    replay = sub.add_parser("replay-archive")
    replay.add_argument("--raw-root", type=Path, default=RAW_ROOT)

    as_of = sub.add_parser("universe-as-of")
    as_of.add_argument("--date", required=True)
    as_of.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    args = parser.parse_args(argv)

    if args.command == "source-date":
        print(source_date_from_undated_capture(args.snapshot_dir))
        return 0
    if args.command == "manifest":
        path = build_manifest(args.snapshot_dir, args.collector_version)
        print(path)
        return 0
    if args.command == "validate":
        core = validate_manifest(args.snapshot_dir)
        print(
            "US breadth forward snapshot PASS"
            f" date={core['snapshot_date']}"
            f" members={len(core['members'])}"
            " price_breadth=UNKNOWN_PRICE_SOURCE_UNAVAILABLE"
        )
        return 0
    if args.command == "validate-bundle":
        core = validate_snapshot_bundle(
            args.snapshot_dir,
            previous_dir=args.previous_dir,
        )
        print(
            "US breadth forward bundle PASS"
            f" date={core['snapshot_date']}"
            f" members={len(core['members'])}"
            " membership_diff=REPRODUCED"
            " price_breadth=UNKNOWN_PRICE_SOURCE_UNAVAILABLE"
        )
        return 0
    if args.command == "replay-archive":
        chain = replay_archive(args.raw_root)
        earliest = chain[0]["snapshot_date"] if chain else None
        latest = chain[-1]["snapshot_date"] if chain else None
        print(
            "US breadth archive replay PASS"
            f" bundles={len(chain)}"
            f" earliest={earliest}"
            f" latest={latest}"
            " price_breadth=UNKNOWN_PRICE_SOURCE_UNAVAILABLE"
        )
        return 0
    if args.command == "universe-as-of":
        result = universe_as_of(args.date, args.raw_root)
        print(
            "US breadth universe_as_of PASS"
            f" as_of={result['as_of_date']}"
            f" snapshot_date={result['snapshot_date']}"
            f" members={len(result['members'])}"
            " price_breadth=UNKNOWN_PRICE_SOURCE_UNAVAILABLE"
        )
        return 0

    current = validate_manifest(args.current_dir)
    previous = validate_manifest(args.previous_dir) if args.previous_dir else None
    payload = build_membership_diff(current, previous)
    write_json_append_only(payload, args.out)
    print(
        "US breadth membership diff"
        f" status={payload['status']}"
        f" date={payload['as_of_date']}"
        " price_breadth=UNKNOWN_PRICE_SOURCE_UNAVAILABLE"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
