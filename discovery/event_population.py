#!/usr/bin/env python3
"""P3-08 operational population from committed SEC D1 and content evidence.

This module performs no provider calls and invents no event, importance, or
promotion policy.  It reuses :mod:`discovery.event_case` unchanged, deriving
an explicit evidence binding only when the committed SEC filing-content
manifest and its locally retained gzip payload independently prove the exact
record accession, URL, filing date, content hash, and retrieval time.

The resulting event packet remains case-recording-only.  Missing content is
not an error and remains ``EVIDENCE_UNRESOLVED``; malformed or contradictory
retained content fails closed instead of being silently ignored.
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
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_DIR = ROOT / "discovery"
COLLECTORS_DIR = ROOT / "collectors"
sys.path.insert(0, str(DISCOVERY_DIR))
sys.path.insert(0, str(COLLECTORS_DIR))

import event_case as CASE  # noqa: E402
import sec_filing_content as SEC  # noqa: E402


KST = ZoneInfo("Asia/Seoul")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_SUBJECT_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")


class EventPopulationError(ValueError):
    """Fail-closed retained-evidence or append-only publication violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventPopulationError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise EventPopulationError(f"JSON_NOT_OBJECT:{path}")
    return value


def _parse_date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise EventPopulationError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise EventPopulationError(code) from exc
    if parsed.isoformat() != value:
        raise EventPopulationError(code)
    return parsed


def _parse_utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise EventPopulationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise EventPopulationError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise EventPopulationError(code)
    return parsed


def _decision_time(value: str) -> dt.datetime:
    return _parse_utc(value, "DECISION_AT_INVALID")


def _records_at_decision(records_path: Path, decision_at: dt.datetime) -> list[dict]:
    decision_date = decision_at.astimezone(KST).date()
    try:
        records = CASE.load_jsonl(records_path)
    except CASE.EventCaseError as exc:
        raise EventPopulationError(f"D1_RECORDS_INVALID:{exc}") from exc
    selected = []
    for record in records:
        if not isinstance(record, dict):
            raise EventPopulationError("D1_RECORD_NOT_OBJECT")
        filing_date = _parse_date(record.get("filing_date"), "D1_FILING_DATE_INVALID")
        collected_for = _parse_date(
            record.get("source_collected_for"), "D1_SOURCE_COLLECTED_FOR_INVALID"
        )
        if filing_date <= decision_date and collected_for <= decision_date:
            selected.append(copy.deepcopy(record))
    return selected


def _case_eligible(record: dict) -> bool:
    return record.get("resolution") in {"resolved", "partial"} and bool(
        record.get("event_types")
    )


def _manifest_path(data_root: Path, record: dict) -> Path:
    subject = record.get("ticker")
    accession = record.get("accession")
    if not isinstance(subject, str) or SAFE_SUBJECT_RE.fullmatch(subject) is None:
        raise EventPopulationError("D1_SUBJECT_PATH_UNSAFE")
    if not isinstance(accession, str) or CASE.ACCESSION_RE.fullmatch(accession) is None:
        raise EventPopulationError("D1_ACCESSION_PATH_UNSAFE")
    return data_root / "sec_content" / subject / accession / "_manifest.json"


def _primary_document(record: dict, manifest: dict) -> dict:
    identity = manifest.get("filing_identity")
    if not isinstance(identity, dict) or identity.get("accession") != record["accession"]:
        raise EventPopulationError(f"MANIFEST_ACCESSION_MISMATCH:{record['accession']}")
    if manifest.get("filing_date") != record["filing_date"]:
        raise EventPopulationError(f"MANIFEST_FILING_DATE_MISMATCH:{record['accession']}")
    if manifest.get("content_status") != "OK" or manifest.get("publication_status") != "OK":
        raise EventPopulationError(f"MANIFEST_CONTENT_NOT_OK:{record['accession']}")
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise EventPopulationError(f"MANIFEST_DOCUMENTS_INVALID:{record['accession']}")
    matches = [
        row for row in documents
        if isinstance(row, dict)
        and row.get("kind") == "primary"
        and row.get("source_uri") == record.get("url")
    ]
    if len(matches) != 1:
        raise EventPopulationError(f"MANIFEST_PRIMARY_IDENTITY_MISMATCH:{record['accession']}")
    return matches[0]


def _retained_document_bytes(manifest_path: Path, manifest: dict) -> dict[str, bytes]:
    """Read only names already accepted by the canonical SEC validator."""
    raw_by_name = {}
    for document in manifest["documents"]:
        name = document["document_name"]
        if SEC.SAFE_DOCUMENT_RE.fullmatch(name) is None:
            # This is independently checked by ``validate_manifest``.  Keep the
            # read-boundary guard here so an unsafe name is never used as a path.
            raise EventPopulationError("MANIFEST_DOCUMENT_NAME_UNSAFE")
        gzip_path = manifest_path.parent / f"{name}.gz"
        try:
            with gzip.open(gzip_path, "rb") as handle:
                raw_by_name[name] = handle.read()
        except (OSError, EOFError) as exc:
            raise EventPopulationError(
                f"RETAINED_CONTENT_READ_FAILED:{manifest['filing_identity']['accession']}:{exc}"
            ) from exc
    return raw_by_name


def _binding_for_record(
    data_root: Path, record: dict, decision_at: dt.datetime
) -> dict | None:
    manifest_path = _manifest_path(data_root, record)
    if not manifest_path.exists():
        return None
    manifest = _read_json(manifest_path)
    try:
        # Reuse the existing P4-02 contract rather than accepting a weaker
        # population-local approximation of the manifest schema/authority.
        manifest = SEC.validate_manifest(manifest)
    except SEC.SecContentError as exc:
        raise EventPopulationError(
            f"SEC_CONTENT_MANIFEST_INVALID:{record['accession']}:{exc}"
        ) from exc
    document = _primary_document(record, manifest)
    retrieved_at_text = manifest.get("retrieved_at_utc")
    retrieved_at = _parse_utc(
        retrieved_at_text, f"MANIFEST_RETRIEVED_AT_INVALID:{record['accession']}"
    )
    if retrieved_at > decision_at:
        return None
    raw_by_name = _retained_document_bytes(manifest_path, manifest)
    try:
        # The canonical validator independently checks every retained primary
        # and exhibit byte length/hash and re-derives registered extraction.
        manifest = SEC.validate_manifest(manifest, raw_by_name=raw_by_name)
    except SEC.SecContentError as exc:
        raise EventPopulationError(
            f"SEC_CONTENT_BYTES_INVALID:{record['accession']}:{exc}"
        ) from exc
    document = _primary_document(record, manifest)
    evidence = {
        "schema_version": CASE.EVIDENCE_SCHEMA_VERSION,
        "source_system": "SEC_EDGAR",
        "subject": record["ticker"],
        "event_date": record["filing_date"],
        "source_identity": {
            "source_id": "sec_edgar",
            "accession": record["accession"],
            "source_url": record["url"],
            "source_sha256": document["content_sha256"],
            "available_at": record["filing_date"],
            "retrieved_at_utc": retrieved_at_text,
        },
    }
    return {
        "source_record_key": CASE.D1.record_key(record),
        "evidence": evidence,
    }


def build_population_inputs(
    *, repo_root: Path = ROOT, decision_at: str,
    records_path: Path | None = None, data_root: Path | None = None,
) -> dict:
    """Return exact D1 records, verified bindings, and their event packet."""
    repo_root = Path(repo_root)
    records_path = Path(records_path) if records_path is not None else repo_root / "data/event_records.jsonl"
    data_root = Path(data_root) if data_root is not None else repo_root / "data"
    decision = _decision_time(decision_at)
    records = _records_at_decision(records_path, decision)
    bindings = []
    for record in records:
        if not _case_eligible(record):
            continue
        row = _binding_for_record(data_root, record, decision)
        if row is not None:
            bindings.append(row)
    bindings.sort(key=lambda item: item["source_record_key"])
    binding_body = {
        "schema_version": CASE.BINDING_SCHEMA_VERSION,
        "binding_set_id": f"committed-sec-content-{payload_sha256(bindings)[:24]}",
        "bindings": bindings,
    }
    try:
        packet = CASE.build_packet(records=records, evidence_bindings=binding_body)
    except CASE.EventCaseError as exc:
        raise EventPopulationError(f"EVENT_PACKET_INVALID:{exc}") from exc
    collected_dates = sorted({record["source_collected_for"] for record in records})
    return {
        "decision_at": decision_at,
        "source_as_of_date": collected_dates[-1] if collected_dates else None,
        "records": records,
        "evidence_bindings": binding_body,
        "packet": packet,
    }


def _packet_bytes(packet: dict) -> bytes:
    return (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def publish_append_only(*, out_root: Path, decision_at: str, packet: dict) -> tuple[Path, bool]:
    decision_date = _decision_time(decision_at).astimezone(KST).date().isoformat()
    try:
        checked = CASE.validate_packet(copy.deepcopy(packet))
    except CASE.EventCaseError as exc:
        raise EventPopulationError(f"EVENT_PACKET_INVALID:{exc}") from exc
    raw = _packet_bytes(checked)
    path = Path(out_root) / decision_date / f"packet-{checked['packet_sha256'][:16]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise EventPopulationError(f"EXISTING_PACKET_READ_FAILED:{path}:{exc}") from exc
        if existing != raw:
            raise EventPopulationError(f"CONTENT_ADDRESSED_PACKET_DRIFT:{path}")
        return path, False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path, True


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Populate committed P3-08 SEC event cases")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = build_population_inputs(
        repo_root=args.repo_root,
        decision_at=args.decision_at,
        records_path=args.records,
        data_root=args.data_root,
    )
    path, created = publish_append_only(
        out_root=args.out_root, decision_at=args.decision_at, packet=result["packet"]
    )
    packet = result["packet"]
    print(json.dumps({
        "status": "published" if created else "verified_existing",
        "path": path.as_posix(),
        "source_as_of_date": result["source_as_of_date"],
        "source_records": packet["summary"]["source_records"],
        "cases": packet["summary"]["cases"],
        "evidence_linked": packet["summary"][CASE.EVIDENCE_LINKED],
        "evidence_unresolved": packet["summary"][CASE.EVIDENCE_UNRESOLVED],
        "packet_sha256": packet["packet_sha256"],
        "authority": packet["authority"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    try:
        return run()
    except EventPopulationError as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
