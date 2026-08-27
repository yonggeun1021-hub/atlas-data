#!/usr/bin/env python3
"""P4-04 provider-free official-release summary observation.

This narrow adapter consumes the exact SEC filing bytes already retained and
validated by P4-02.  It records the complete ordered ``News Summary`` block of
one explicitly registered official earnings-release exhibit.  It does not
select favourable sentences, convert prose into financial signals, compare a
threshold, rank a source, evaluate a Rule, or change any investment authority.

The first registered population is Sandisk's fiscal-Q4-2026 Exhibit 99.1.  A
new release identity requires code review; the registry is implementation
scope, not a claim that the release is more important than another source.
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
DISCOVERY = ROOT / "discovery"
sys.path.insert(0, str(DISCOVERY))

import official_release_observation as BASE  # noqa: E402


SCHEMA_VERSION = "official_release_summary_packet/1"
OBSERVATION_VERSION = "official_release_summary_observation/1"
DEFAULT_DATA_ROOT = ROOT / "data"
DEFAULT_OUT_ROOT = ROOT / "data/observations/official_release_summary_observations"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")

REGISTERED_RELEASE = {
    "ticker": "SNDK",
    "accession": "0001628280-26-053346",
    "form": "8-K",
    "document_name": "sndkq4-26ex991xpressrelease.htm",
    "document_kind": "exhibit",
    "title": "Sandisk Reports Fiscal Fourth Quarter 2026 Financial Results",
    "summary_heading": "News Summary",
    "summary_end_marker": "MILPITAS, Calif. — August 5, 2026 —",
    "expected_summary_items": 5,
}

AUTHORITY = {
    "observation_recording_only": True,
    "fact_selection_authorized": False,
    "source_ranking_authorized": False,
    "interpretation_authorized": False,
    "rule_evaluation_authorized": False,
    "stage_change_authorized": False,
    "action_generation_authorized": False,
    "order_generation_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class OfficialReleaseSummaryError(ValueError):
    """Fail-closed retained-release observation violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _one_offset(text: str, needle: str, *, start: int = 0, code: str) -> tuple[int, int]:
    first = text.find(needle, start)
    if first < 0 or text.find(needle, first + 1) >= 0:
        raise OfficialReleaseSummaryError(code)
    return first, first + len(needle)


def _published_date(text: str, *, start: int) -> dt.date:
    match = re.search(
        r"MILPITAS, Calif\. — ([A-Z][a-z]+ \d{1,2}, \d{4}) —",
        text[start:],
    )
    if match is None:
        raise OfficialReleaseSummaryError("RELEASE_PUBLICATION_DATE_MISSING")
    try:
        return dt.datetime.strptime(match.group(1), "%B %d, %Y").date()
    except ValueError as exc:
        raise OfficialReleaseSummaryError("RELEASE_PUBLICATION_DATE_INVALID") from exc


def _lineage(manifest_path: Path, manifest: dict, manifest_bytes: bytes, row: dict) -> dict:
    raw_path = manifest_path.parent / f"{row['document_name']}.gz"
    return {
        "accession": manifest["filing_identity"]["accession"],
        "manifest_ref": BASE._source_ref(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "release_document_ref": BASE._source_ref(raw_path),
        "release_document_name": row["document_name"],
        "release_document_kind": row["kind"],
        "release_source_uri": row["source_uri"],
        "release_content_sha256": row["content_sha256"],
        "retrieved_at_utc": manifest["retrieved_at_utc"],
    }


def _release_observation(
    manifest_path: Path,
    manifest: dict,
    raw_by_name: dict[str, bytes],
    manifest_bytes: bytes,
) -> dict:
    registered = REGISTERED_RELEASE
    if manifest.get("ticker") != registered["ticker"]:
        raise OfficialReleaseSummaryError("REGISTERED_RELEASE_TICKER_MISMATCH")
    if manifest.get("form") != registered["form"]:
        raise OfficialReleaseSummaryError("REGISTERED_RELEASE_FORM_MISMATCH")
    if manifest.get("filing_identity", {}).get("accession") != registered["accession"]:
        raise OfficialReleaseSummaryError("REGISTERED_RELEASE_ACCESSION_MISMATCH")

    matches = [
        row
        for row in manifest["documents"]
        if row.get("document_name") == registered["document_name"]
    ]
    if len(matches) != 1:
        raise OfficialReleaseSummaryError("REGISTERED_RELEASE_DOCUMENT_CARDINALITY")
    document = matches[0]
    if document.get("kind") != registered["document_kind"]:
        raise OfficialReleaseSummaryError("REGISTERED_RELEASE_DOCUMENT_KIND_MISMATCH")

    text = BASE.SEC.normalized_visible_text(raw_by_name[registered["document_name"]])
    title_start, title_end = _one_offset(
        text,
        registered["title"],
        code="RELEASE_TITLE_CARDINALITY_INVALID",
    )
    heading_start, heading_end = _one_offset(
        text,
        registered["summary_heading"],
        start=title_end,
        code="RELEASE_SUMMARY_HEADING_CARDINALITY_INVALID",
    )
    end_start, _ = _one_offset(
        text,
        registered["summary_end_marker"],
        start=heading_end,
        code="RELEASE_SUMMARY_END_CARDINALITY_INVALID",
    )
    block = text[heading_end:end_start].strip()
    if not block.startswith("• "):
        raise OfficialReleaseSummaryError("RELEASE_SUMMARY_BLOCK_INVALID")
    items = [item.strip() for item in block.split("• ") if item.strip()]
    if len(items) != registered["expected_summary_items"]:
        raise OfficialReleaseSummaryError("RELEASE_SUMMARY_ITEM_COUNT_INVALID")

    summary_items = []
    cursor = heading_end
    for ordinal, item in enumerate(items, start=1):
        item_start = text.find(item, cursor, end_start)
        if item_start < 0:
            raise OfficialReleaseSummaryError("RELEASE_SUMMARY_ITEM_OFFSET_INVALID")
        item_end = item_start + len(item)
        summary_items.append(
            {
                "ordinal": ordinal,
                "text": item,
                "normalized_text_start": item_start,
                "normalized_text_end": item_end,
            }
        )
        cursor = item_end

    published = _published_date(text, start=heading_end)
    try:
        filing_date = dt.date.fromisoformat(manifest["filing_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialReleaseSummaryError("RELEASE_FILING_DATE_INVALID") from exc
    retrieved = BASE._utc(
        manifest["retrieved_at_utc"], "MANIFEST_RETRIEVED_AT_INVALID"
    ).date()
    if published != filing_date:
        raise OfficialReleaseSummaryError("RELEASE_PUBLICATION_FILING_DATE_MISMATCH")
    if published > retrieved:
        raise OfficialReleaseSummaryError("RELEASE_PUBLISHED_AFTER_CAPTURE")

    lineage = _lineage(manifest_path, manifest, manifest_bytes, document)
    return {
        "schema_version": OBSERVATION_VERSION,
        "status": "OBSERVED",
        "subject": registered["ticker"],
        "document_class": "SEC_EXHIBIT_99_OFFICIAL_RELEASE",
        "release_title": registered["title"],
        "published_at": published.isoformat(),
        "title_locator": {
            "normalized_text_start": title_start,
            "normalized_text_end": title_end,
        },
        "summary_heading": registered["summary_heading"],
        "summary_items": summary_items,
        "summary_block_sha256": payload_sha256(summary_items),
        "lineage": lineage,
        "interpretation_status": "UNDETERMINED",
        "rule_impact": "NONE",
        "stage_change": None,
        "trade_proposal": None,
    }


def _excluded(manifest_path: Path, manifest: dict, manifest_bytes: bytes) -> dict:
    primary = [row for row in manifest["documents"] if row.get("kind") == "primary"]
    if len(primary) != 1:
        raise OfficialReleaseSummaryError("PRIMARY_DOCUMENT_CARDINALITY_INVALID")
    return {
        "status": "NOT_REGISTERED_OFFICIAL_RELEASE",
        "reason": "NO_APPROVED_RELEASE_SUMMARY_ADAPTER",
        "subject": manifest.get("ticker"),
        "lineage": _lineage(manifest_path, manifest, manifest_bytes, primary[0]),
    }


def build_packet(*, data_root: Path, decision_at: str) -> dict:
    try:
        decision_time = BASE._utc(decision_at, "DECISION_AT_INVALID")
    except BASE.OfficialReleaseObservationError as exc:
        raise OfficialReleaseSummaryError(str(exc)) from exc
    subject_root = Path(data_root) / "sec_content" / REGISTERED_RELEASE["ticker"]
    manifest_paths = sorted(subject_root.glob("*/_manifest.json"))
    if not manifest_paths:
        raise OfficialReleaseSummaryError("NO_RETAINED_SUBJECT_MANIFESTS")

    observations = []
    exclusions = []
    eligible_times = []
    for manifest_path in manifest_paths:
        try:
            loaded = BASE._load_validated_filing(
                manifest_path, decision_time=decision_time
            )
        except BASE.OfficialReleaseObservationError as exc:
            raise OfficialReleaseSummaryError(str(exc)) from exc
        if loaded is None:
            continue
        manifest, raw_by_name, manifest_bytes, retrieved = loaded
        eligible_times.append(retrieved)
        accession = manifest.get("filing_identity", {}).get("accession")
        if accession == REGISTERED_RELEASE["accession"]:
            observations.append(
                _release_observation(
                    manifest_path, manifest, raw_by_name, manifest_bytes
                )
            )
        else:
            exclusions.append(_excluded(manifest_path, manifest, manifest_bytes))

    if not eligible_times:
        raise OfficialReleaseSummaryError("NO_PIT_ELIGIBLE_SUBJECT_MANIFESTS")
    if len(observations) != 1:
        raise OfficialReleaseSummaryError("REGISTERED_RELEASE_OBSERVATION_CARDINALITY")
    exclusions.sort(key=lambda row: row["lineage"]["accession"])
    evidence_as_of = max(eligible_times).strftime("%Y-%m-%dT%H:%M:%SZ")
    packet = {
        "schema_version": SCHEMA_VERSION,
        "evidence_as_of": evidence_as_of,
        "source_contract": "P4-02_RETAINED_SEC_EXACT_BYTES",
        "registration_scope": "NARROW_IMPLEMENTATION_SCOPE_NOT_SOURCE_AUTHORITY",
        "source_hierarchy_status": "UNRATIFIED_NO_GLOBAL_RANKING",
        "subject": REGISTERED_RELEASE["ticker"],
        "counts": {
            "pit_eligible_manifests": len(eligible_times),
            "observed_registered_releases": len(observations),
            "excluded_unregistered_filings": len(exclusions),
            "observed_summary_items": sum(
                len(row["summary_items"]) for row in observations
            ),
        },
        "observations": observations,
        "excluded_filings": exclusions,
        "authority": copy.deepcopy(AUTHORITY),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, *, data_root: Path) -> dict:
    if not isinstance(packet, dict):
        raise OfficialReleaseSummaryError("PACKET_NOT_OBJECT")
    digest = packet.get("packet_sha256")
    unsigned = {
        key: copy.deepcopy(value)
        for key, value in packet.items()
        if key != "packet_sha256"
    }
    if not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None:
        raise OfficialReleaseSummaryError("PACKET_SHA256_INVALID")
    if payload_sha256(unsigned) != digest:
        raise OfficialReleaseSummaryError("PACKET_HASH_MISMATCH")
    if packet.get("authority") != AUTHORITY:
        raise OfficialReleaseSummaryError("PACKET_AUTHORITY_MISMATCH")
    rebuilt = build_packet(
        data_root=Path(data_root), decision_at=packet.get("evidence_as_of")
    )
    if rebuilt != packet:
        raise OfficialReleaseSummaryError("PACKET_INDEPENDENT_REBUILD_MISMATCH")
    return copy.deepcopy(packet)


def publish_packet(packet: dict, *, data_root: Path, out_root: Path) -> Path:
    checked = validate_packet(packet, data_root=Path(data_root))
    day = checked["evidence_as_of"][:10]
    target = (
        Path(out_root)
        / day
        / f"sndk-release-summary-{checked['packet_sha256'][:16]}.json"
    )
    payload = json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != payload:
            raise OfficialReleaseSummaryError("APPEND_ONLY_PACKET_DRIFT")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, target)
    return target


def _now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--decision-at", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    packet = build_packet(
        data_root=args.data_root, decision_at=args.decision_at or _now()
    )
    target = publish_packet(
        packet, data_root=args.data_root, out_root=args.out_root
    )
    print(
        json.dumps(
            {"status": "OK", "packet": str(target), "counts": packet["counts"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
