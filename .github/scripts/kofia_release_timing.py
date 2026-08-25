#!/usr/bin/env python3
"""Build a provider-free KOFIA release-timing observation.

This module measures only what the append-only Atlas probe sequence proves:
an exact row was absent at a verified probe and present by a later verified
probe.  It deliberately does not turn either bound into KOFIA ``available_at``
and does not authorize Regime, Production, or trading use.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
KOFIA_SCRIPT = ROOT / ".github" / "scripts" / "kofia_first_seen.py"
SPEC = importlib.util.spec_from_file_location("kofia_first_seen", KOFIA_SCRIPT)
capture = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(capture)

CONTRACT_PATH = ROOT / "config" / "kofia_release_timing_contract.json"
PACKET_SCHEMA_VERSION = "kofia_release_timing_observation/1"
KST = dt.timezone(dt.timedelta(hours=9))


class ReleaseTimingError(RuntimeError):
    """Fail-closed release-timing observation error."""


def fail(code: str, detail: str) -> None:
    raise ReleaseTimingError(f"{code}: {detail}")


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", str(exc))
    expected = {
        "schema_version",
        "contract_version",
        "source_capture_contract_version",
        "source_contract_version",
        "source_evidence_root",
        "observation_semantics",
        "window_lower_bound_semantics",
        "window_upper_bound_semantics",
        "available_at",
        "release_timing_policy_status",
        "historical_range_status",
        "api_field_unit_status",
        "decision_eligible",
        "regime_score_authorized",
        "production_wiring_authorized",
        "trading_action_authorized",
    }
    if set(contract) != expected or contract.get("schema_version") != 1:
        fail("CONTRACT_INVALID", "schema or fields")
    if contract.get("contract_version") != PACKET_SCHEMA_VERSION:
        fail("CONTRACT_INVALID", "contract_version")
    if contract.get("source_capture_contract_version") != "kofia-first-seen-capture/v2":
        fail("CONTRACT_INVALID", "source_capture_contract_version")
    if contract.get("source_contract_version") != "kofia_liquidity_source/v3":
        fail("CONTRACT_INVALID", "source_contract_version")
    if contract.get("source_evidence_root") != "evidence/kofia/first_seen":
        fail("CONTRACT_INVALID", "source_evidence_root")
    if contract.get("observation_semantics") != (
        "atlas_probe_window_only_not_source_release_time"
    ):
        fail("CONTRACT_INVALID", "observation_semantics")
    if contract.get("window_lower_bound_semantics") != (
        "latest_verified_probe_without_exact_row_hash_strictly_before_first_seen"
    ):
        fail("CONTRACT_INVALID", "window_lower_bound_semantics")
    if contract.get("window_upper_bound_semantics") != (
        "earliest_verified_atlas_capture_of_exact_row_hash"
    ):
        fail("CONTRACT_INVALID", "window_upper_bound_semantics")
    if contract.get("available_at") is not None:
        fail("CONTRACT_INVALID", "available_at")
    if contract.get("release_timing_policy_status") != "UNRATIFIED":
        fail("CONTRACT_INVALID", "release_timing_policy_status")
    if contract.get("historical_range_status") != "unverified":
        fail("CONTRACT_INVALID", "historical_range_status")
    if contract.get("api_field_unit_status") != "conflicting_primary_evidence":
        fail("CONTRACT_INVALID", "api_field_unit_status")
    if any(
        contract.get(key) is not False
        for key in (
            "decision_eligible",
            "regime_score_authorized",
            "production_wiring_authorized",
            "trading_action_authorized",
        )
    ):
        fail("CONTRACT_INVALID", "authority")
    return contract


def _bundle_inventory(
    evidence_root: Path, max_captured_at: str | None = None
) -> list[tuple[str, Path, dict]]:
    if not evidence_root.is_dir():
        fail("EVIDENCE_ROOT_MISSING", str(evidence_root))
    bundles: list[tuple[str, Path, dict]] = []
    max_capture = (
        capture.parse_captured_at(max_captured_at)
        if max_captured_at is not None
        else None
    )
    for bundle in evidence_root.glob("*/*"):
        if not bundle.is_dir() or bundle.is_symlink():
            continue
        try:
            captured_text = (bundle / "_captured_at.txt").read_text(encoding="utf-8")
            observation_text = (bundle / "_observation.json").read_text(encoding="utf-8")
            observation = json.loads(observation_text)
        except (OSError, json.JSONDecodeError) as exc:
            fail("EVIDENCE_INVALID", f"{bundle}: {exc}")
        if captured_text != captured_text.strip() + "\n":
            fail("EVIDENCE_NONCANONICAL", f"{bundle}/_captured_at.txt")
        if canonical_bytes(observation) != observation_text.encode("utf-8"):
            fail("EVIDENCE_NONCANONICAL", f"{bundle}/_observation.json")
        captured_at = captured_text.strip()
        captured_time = capture.parse_captured_at(captured_at)
        if max_capture is not None and captured_time > max_capture:
            continue
        bundles.append((captured_at, bundle, observation))
    bundles.sort(key=lambda item: (item[0], item[1].parent.name, item[1].name))
    if not bundles:
        fail("EVIDENCE_EMPTY", str(evidence_root))
    return bundles


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _validated_timeline(
    evidence_root: Path, max_captured_at: str | None = None
) -> tuple[list[dict], dict]:
    capture_contract = capture.load_capture_contract()
    source_contract = capture.liquidity.load_contract()
    # This public replay validates every committed bundle from retained raw
    # bytes before the derived observer reads any committed observation JSON.
    trusted = capture.read_prior_first_seen(
        evidence_root,
        capture_contract,
        source_contract,
        max_captured_at=max_captured_at,
    )
    bundles = _bundle_inventory(evidence_root, max_captured_at=max_captured_at)
    timeline = []
    for captured_at, bundle, observation in bundles:
        if observation.get("mode") != "first_seen":
            fail("EVIDENCE_MODE_INVALID", str(bundle))
        timeline.append(
            {
                "captured_at_utc": captured_at,
                "bundle_path": _relative_to_root(bundle),
                "manifest_sha256": file_sha256(bundle / "_manifest.json"),
                "observation_sha256": file_sha256(bundle / "_observation.json"),
                "operations": observation.get("operations"),
            }
        )
    if not all(isinstance(item["operations"], dict) for item in timeline):
        fail("EVIDENCE_OBSERVATION_INVALID", "operations")
    return timeline, trusted


def _build_observations(timeline: list[dict], trusted: dict) -> list[dict]:
    probed_hashes: dict[tuple[str, str], list[tuple[str, set[str]]]] = {}
    for capture_row in timeline:
        captured_at = capture_row["captured_at_utc"]
        for operation, payload in sorted(capture_row["operations"].items()):
            if not isinstance(payload, dict):
                fail("EVIDENCE_OBSERVATION_INVALID", operation)
            by_date: dict[str, set[str]] = {}
            for day in payload.get("missing_query_dates", []):
                by_date[day] = set()
            for row in payload.get("observed_rows", []):
                if not isinstance(row, dict):
                    fail("EVIDENCE_OBSERVATION_INVALID", operation)
                day = row.get("observation_date")
                row_sha = row.get("row_sha256")
                if not isinstance(day, str) or not isinstance(row_sha, str):
                    fail("EVIDENCE_OBSERVATION_INVALID", operation)
                by_date.setdefault(day, set()).add(row_sha)
            for day, hashes in by_date.items():
                probed_hashes.setdefault((operation, day), []).append(
                    (captured_at, hashes)
                )

    rows = []
    for (operation, day, row_sha), first_seen_at in sorted(trusted.items()):
        first_seen = capture.parse_captured_at(first_seen_at)
        candidates = [
            captured_at
            for captured_at, hashes in probed_hashes.get((operation, day), [])
            if capture.parse_captured_at(captured_at) < first_seen
            and row_sha not in hashes
        ]
        lower = max(candidates, key=capture.parse_captured_at) if candidates else None
        lower_dt = capture.parse_captured_at(lower) if lower else None
        first_seen_kst = first_seen.astimezone(KST)
        observation_date = dt.date.fromisoformat(day)
        if first_seen_kst.date() < observation_date:
            fail("FIRST_SEEN_PRECEDES_OBSERVATION_DATE", f"{operation} {day}")
        rows.append(
            {
                "operation": operation,
                "observation_date": day,
                "row_sha256": row_sha,
                "last_verified_exact_row_absent_probe_at_utc": lower,
                "first_verified_present_probe_at_utc": first_seen_at,
                "first_verified_present_probe_at_kst": first_seen_kst.isoformat(
                    timespec="seconds"
                ),
                "availability_window_seconds": (
                    int((first_seen - lower_dt).total_seconds())
                    if lower_dt is not None
                    else None
                ),
                "calendar_lag_days_at_first_seen": (
                    first_seen_kst.date() - observation_date
                ).days,
                "window_status": (
                    "BOUNDED_BY_VERIFIED_EXACT_ROW_ABSENT_AND_PRESENT_PROBES"
                    if lower is not None
                    else "UPPER_BOUND_ONLY_NO_EARLIER_VERIFIED_EXACT_ROW_ABSENT_PROBE"
                ),
            }
        )
    return rows


def _summaries(rows: list[dict]) -> dict:
    summaries = {}
    for operation in sorted({row["operation"] for row in rows}):
        selected = [row for row in rows if row["operation"] == operation]
        lags = [row["calendar_lag_days_at_first_seen"] for row in selected]
        dates = sorted({row["observation_date"] for row in selected})
        revisions = {}
        for day in dates:
            count = sum(row["observation_date"] == day for row in selected)
            if count > 1:
                revisions[day] = count
        summaries[operation] = {
            "exact_row_count": len(selected),
            "observation_date_count": len(dates),
            "bounded_window_count": sum(
                row["last_verified_exact_row_absent_probe_at_utc"] is not None
                for row in selected
            ),
            "upper_bound_only_count": sum(
                row["last_verified_exact_row_absent_probe_at_utc"] is None
                for row in selected
            ),
            "earliest_observation_date": dates[0] if dates else None,
            "latest_observation_date": dates[-1] if dates else None,
            "minimum_calendar_lag_days": min(lags) if lags else None,
            "maximum_calendar_lag_days": max(lags) if lags else None,
            "revision_counts_by_observation_date": revisions,
        }
    return summaries


def build_packet(
    evidence_root: Path,
    contract_path: Path = CONTRACT_PATH,
    as_of_capture_utc: str | None = None,
) -> dict:
    contract = load_contract(contract_path)
    expected_root = (ROOT / contract["source_evidence_root"]).resolve()
    if Path(evidence_root).resolve() != expected_root:
        fail(
            "EVIDENCE_ROOT_MISMATCH",
            f"expected {expected_root}, got {Path(evidence_root).resolve()}",
        )
    timeline, trusted = _validated_timeline(
        Path(evidence_root), max_captured_at=as_of_capture_utc
    )
    rows = _build_observations(timeline, trusted)
    packet = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "source_contract_version": contract["source_contract_version"],
        "source_capture_contract_version": contract[
            "source_capture_contract_version"
        ],
        "source_evidence_root": contract["source_evidence_root"],
        "as_of_capture_utc": timeline[-1]["captured_at_utc"],
        "source_bundle_count": len(timeline),
        "source_bundles": [
            {key: row[key] for key in (
                "bundle_path",
                "captured_at_utc",
                "manifest_sha256",
                "observation_sha256",
            )}
            for row in timeline
        ],
        "observation_semantics": contract["observation_semantics"],
        "window_lower_bound_semantics": contract[
            "window_lower_bound_semantics"
        ],
        "window_upper_bound_semantics": contract[
            "window_upper_bound_semantics"
        ],
        "observations": rows,
        "summary_by_operation": _summaries(rows),
        "release_timing_policy_status": contract[
            "release_timing_policy_status"
        ],
        "historical_range_status": contract["historical_range_status"],
        "api_field_unit_status": contract["api_field_unit_status"],
        "available_at": None,
        "unresolved_boundaries": [
            "SOURCE_RELEASE_TIME_POLICY_UNRATIFIED",
            "SOURCE_AVAILABLE_AT_UNRATIFIED",
            "DURABLE_HISTORICAL_RANGE_UNVERIFIED",
            "API_FIELD_UNIT_CONFLICTING_PRIMARY_EVIDENCE",
        ],
        "authority": {
            "decision_eligible": False,
            "regime_score_authorized": False,
            "production_wiring_authorized": False,
            "trading_action_authorized": False,
        },
    }
    packet["payload_sha256"] = digest(packet)
    return packet


def validate_packet(
    packet: dict,
    evidence_root: Path,
    contract_path: Path = CONTRACT_PATH,
) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != (
        PACKET_SCHEMA_VERSION
    ):
        fail("PACKET_INVALID", "schema_version")
    claimed = packet.get("payload_sha256")
    without_hash = dict(packet)
    without_hash.pop("payload_sha256", None)
    if claimed != digest(without_hash):
        fail("PACKET_HASH_MISMATCH", "payload_sha256")
    expected = build_packet(
        evidence_root,
        contract_path,
        as_of_capture_utc=packet.get("as_of_capture_utc"),
    )
    if packet != expected:
        fail("PACKET_SEMANTIC_MISMATCH", "independent rebuild differs")
    return packet


def expected_output_relative(packet: dict) -> Path:
    bundles = packet.get("source_bundles")
    if not isinstance(bundles, list) or not bundles:
        fail("PACKET_INVALID", "source_bundles")
    source = bundles[-1].get("bundle_path")
    if not isinstance(source, str):
        fail("PACKET_INVALID", "latest bundle path")
    parts = Path(source).parts
    if len(parts) != 5 or parts[:3] != ("evidence", "kofia", "first_seen"):
        fail("PACKET_INVALID", "latest bundle path identity")
    return Path("data", "observations", "kofia_release_timing", parts[3], parts[4], "packet.json")


def _enforce_tracked_output_identity(packet: dict, path: Path) -> None:
    try:
        relative = Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    expected = expected_output_relative(packet)
    if relative != expected:
        fail("OUTPUT_PATH_IDENTITY_MISMATCH", f"expected {expected}, got {relative}")


def write_packet(packet: dict, out: Path) -> None:
    out = Path(out)
    _enforce_tracked_output_identity(packet, out)
    if out.exists():
        fail("APPEND_ONLY_VIOLATION", str(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(canonical_bytes(packet))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--evidence-root", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("packet", type=Path)
    validate.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "build":
        packet = build_packet(args.evidence_root)
        write_packet(packet, args.out)
        validate_packet(packet, args.evidence_root)
        print(
            "KOFIA release timing observation PASS "
            f"bundles={packet['source_bundle_count']} "
            f"rows={len(packet['observations'])} authority=false"
        )
        return 0
    try:
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("PACKET_INVALID", str(exc))
    _enforce_tracked_output_identity(packet, args.packet)
    validate_packet(packet, args.evidence_root)
    print(
        "KOFIA release timing validation PASS "
        f"bundles={packet['source_bundle_count']} "
        f"rows={len(packet['observations'])} authority=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseTimingError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        raise SystemExit(2)
