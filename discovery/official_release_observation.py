#!/usr/bin/env python3
"""P4-04 provider-free TSMC official-release observation population.

The adapter consumes only the exact SEC 6-K bytes already retained by P4-02.
It identifies the already-approved TSMC consolidated monthly-revenue table and
records the company's published values without applying thresholds or meaning.
Other TSMC 6-K filings remain explicitly outside this observation population.

No network client, source fallback, Rule evaluation, Stage mutation, action,
order, production, or trading authority exists in this module.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
COLLECTORS = ROOT / "collectors"
sys.path.insert(0, str(COLLECTORS))

import sec_filing_content as SEC  # noqa: E402
import tsmc_sec_monthly_probe as TSMC  # noqa: E402


SCHEMA_VERSION = "official_release_observation_packet/1"
OBSERVATION_VERSION = "official_release_observation/1"
DEFAULT_DATA_ROOT = ROOT / "data"
DEFAULT_OUT_ROOT = ROOT / "data/observations/official_release_observations"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

AUTHORITY = {
    "observation_recording_only": True,
    "source_ranking_authorized": False,
    "interpretation_authorized": False,
    "rule_evaluation_authorized": False,
    "stage_change_authorized": False,
    "action_generation_authorized": False,
    "order_generation_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class OfficialReleaseObservationError(ValueError):
    """Fail-closed retained-source, observation, or publication violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: str, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise OfficialReleaseObservationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise OfficialReleaseObservationError(code) from exc
    return parsed


def _source_ref(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return f"external_fixture/{path.name}"


def _read_json_bytes(path: Path) -> tuple[dict, bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialReleaseObservationError(f"MANIFEST_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise OfficialReleaseObservationError(f"MANIFEST_NOT_OBJECT:{path}")
    return value, raw


def _load_validated_filing(
    manifest_path: Path, *, decision_time: dt.datetime
) -> tuple[dict, dict[str, bytes], bytes, dt.datetime] | None:
    manifest, manifest_bytes = _read_json_bytes(manifest_path)
    retrieved = _utc(manifest.get("retrieved_at_utc"), "MANIFEST_RETRIEVED_AT_INVALID")
    # A historical rebuild must not inspect or validate raw bytes that were not
    # yet available.  Otherwise a later corrupt/unrelated filing could change an
    # earlier packet even though it was outside that packet's PIT population.
    if retrieved > decision_time:
        return None
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise OfficialReleaseObservationError("MANIFEST_DOCUMENTS_EMPTY")
    raw_by_name = {}
    for row in documents:
        name = row.get("document_name") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name:
            raise OfficialReleaseObservationError("MANIFEST_DOCUMENT_NAME_INVALID")
        raw_path = manifest_path.parent / f"{name}.gz"
        try:
            raw_by_name[name] = gzip.decompress(raw_path.read_bytes())
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise OfficialReleaseObservationError(f"RAW_CACHE_INVALID:{name}") from exc
    try:
        checked = SEC.validate_manifest(copy.deepcopy(manifest), raw_by_name)
    except SEC.SecContentError as exc:
        raise OfficialReleaseObservationError(f"SEC_MANIFEST_INVALID:{exc}") from exc
    return checked, raw_by_name, manifest_bytes, retrieved


def _lineage(manifest_path: Path, manifest: dict, manifest_bytes: bytes) -> dict:
    primary = [row for row in manifest["documents"] if row.get("kind") == "primary"]
    if len(primary) != 1:
        raise OfficialReleaseObservationError("PRIMARY_DOCUMENT_CARDINALITY_INVALID")
    row = primary[0]
    raw_path = manifest_path.parent / f"{row['document_name']}.gz"
    return {
        "accession": manifest["filing_identity"]["accession"],
        "manifest_ref": _source_ref(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "primary_document_ref": _source_ref(raw_path),
        "primary_document_name": row["document_name"],
        "primary_source_uri": row["source_uri"],
        "primary_content_sha256": row["content_sha256"],
        "retrieved_at_utc": manifest["retrieved_at_utc"],
    }


def _observation_from_retained(
    manifest_path: Path, manifest: dict, raw_by_name: dict[str, bytes], manifest_bytes: bytes
) -> dict:
    lineage = _lineage(manifest_path, manifest, manifest_bytes)
    raw = raw_by_name[lineage["primary_document_name"]]
    try:
        parsed = TSMC.parse_retained_monthly_report(manifest, raw)
    except TSMC.SecProbeError as exc:
        if str(exc) == "MONTHLY_REPORT_IDENTITY_MISSING":
            return {
                "status": "NOT_MONTHLY_REVENUE_REPORT",
                "reason": "APPROVED_MONTHLY_REVENUE_IDENTITY_ABSENT",
                "lineage": lineage,
            }
        raise OfficialReleaseObservationError(
            f"IDENTIFIED_MONTHLY_REPORT_INVALID:{lineage['accession']}:{exc}"
        ) from exc
    return {
        "schema_version": OBSERVATION_VERSION,
        "status": "OBSERVED",
        "subject": "TSM",
        "measurement_set": "TSMC_CONSOLIDATED_MONTHLY_REVENUE",
        "economic_period": parsed["target_month"],
        "published_at": parsed["published_at"],
        "unit": "NT$ million",
        "published_values": copy.deepcopy(parsed["observation"]),
        "table_locator": copy.deepcopy(parsed["table_locator"]),
        "crosscheck": copy.deepcopy(parsed["crosscheck"]),
        "lineage": lineage,
        "interpretation_status": "UNDETERMINED",
        "rule_impact": "NONE",
        "stage_change": None,
        "trade_proposal": None,
    }


def build_packet(*, data_root: Path, decision_at: str) -> dict:
    """Build one PIT-safe packet; ``decision_at`` is an upper bound only."""
    decision_time = _utc(decision_at, "DECISION_AT_INVALID")
    manifest_paths = sorted((Path(data_root) / "sec_content" / "TSM").glob("*/_manifest.json"))
    if not manifest_paths:
        raise OfficialReleaseObservationError("NO_RETAINED_TSM_MANIFESTS")

    observations = []
    exclusions = []
    eligible_times = []
    for path in manifest_paths:
        loaded = _load_validated_filing(path, decision_time=decision_time)
        if loaded is None:
            continue
        manifest, raw_by_name, manifest_bytes, retrieved = loaded
        eligible_times.append(retrieved)
        row = _observation_from_retained(path, manifest, raw_by_name, manifest_bytes)
        if row["status"] == "OBSERVED":
            observations.append(row)
        else:
            exclusions.append(row)
    if not eligible_times:
        raise OfficialReleaseObservationError("NO_PIT_ELIGIBLE_TSM_MANIFESTS")

    observations.sort(key=lambda row: (row["economic_period"], row["lineage"]["accession"]))
    exclusions.sort(key=lambda row: row["lineage"]["accession"])
    periods = [row["economic_period"] for row in observations]
    if len(periods) != len(set(periods)):
        raise OfficialReleaseObservationError("MONTHLY_PERIOD_AMBIGUOUS")
    evidence_as_of = max(eligible_times).strftime("%Y-%m-%dT%H:%M:%SZ")
    packet = {
        "schema_version": SCHEMA_VERSION,
        "evidence_as_of": evidence_as_of,
        "source_contract": "P4-02_RETAINED_SEC_6K_EXACT_BYTES",
        "source_hierarchy_status": "UNRATIFIED_NO_GLOBAL_RANKING",
        "subject": "TSM",
        "counts": {
            "pit_eligible_manifests": len(eligible_times),
            "observed_monthly_revenue": len(observations),
            "excluded_non_monthly_revenue": len(exclusions),
        },
        "observations": observations,
        "excluded_filings": exclusions,
        "authority": copy.deepcopy(AUTHORITY),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, *, data_root: Path) -> dict:
    if not isinstance(packet, dict):
        raise OfficialReleaseObservationError("PACKET_NOT_OBJECT")
    digest = packet.get("packet_sha256")
    unsigned = {key: copy.deepcopy(value) for key, value in packet.items() if key != "packet_sha256"}
    if not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None:
        raise OfficialReleaseObservationError("PACKET_SHA256_INVALID")
    if payload_sha256(unsigned) != digest:
        raise OfficialReleaseObservationError("PACKET_HASH_MISMATCH")
    if packet.get("authority") != AUTHORITY:
        raise OfficialReleaseObservationError("PACKET_AUTHORITY_MISMATCH")
    rebuilt = build_packet(data_root=Path(data_root), decision_at=packet.get("evidence_as_of"))
    if rebuilt != packet:
        raise OfficialReleaseObservationError("PACKET_INDEPENDENT_REBUILD_MISMATCH")
    return copy.deepcopy(packet)


def publish_packet(packet: dict, *, data_root: Path, out_root: Path) -> Path:
    checked = validate_packet(packet, data_root=Path(data_root))
    day = checked["evidence_as_of"][:10]
    target = Path(out_root) / day / f"tsm-monthly-revenue-{checked['packet_sha256'][:16]}.json"
    payload = json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != payload:
            raise OfficialReleaseObservationError("APPEND_ONLY_PACKET_DRIFT")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
    return target


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--decision-at", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    packet = build_packet(data_root=args.data_root, decision_at=args.decision_at or _now())
    target = publish_packet(packet, data_root=args.data_root, out_root=args.out_root)
    print(json.dumps({"status": "OK", "packet": str(target), "counts": packet["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
