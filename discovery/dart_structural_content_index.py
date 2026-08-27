#!/usr/bin/env python3
"""P4-03 provider-free structural index for retained OpenDART documents.

This module consumes only the already-validated DART metadata/content run and
the immutable receipt ZIP/member cache.  It records document structure (tag,
table, row and cell counts plus a structure-only fingerprint) without retaining
text, attribute values, numbers, event meaning, or investment interpretation.

The moving ``latest`` inputs are copied byte-for-byte beside each append-only
packet.  Validation therefore re-derives from the exact captured inputs instead
of silently substituting a newer pointer.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import gzip
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
COLLECTORS = ROOT / "collectors"
DISCOVERY = ROOT / "discovery"
sys.path.insert(0, str(COLLECTORS))
sys.path.insert(0, str(DISCOVERY))

import dart_filing_content as DART  # noqa: E402
import dart_event_observation as DART_OBSERVATION  # noqa: E402


SCHEMA_VERSION = "dart_structural_content_index_packet/1"
DOCUMENT_SCHEMA_VERSION = "dart_structural_document_index/1"
DEFAULT_SOURCE = ROOT / "data/latest_dart.json"
DEFAULT_CONTENT = ROOT / "data/latest_dart_content.json"
DEFAULT_DATA_ROOT = ROOT / "data"
DEFAULT_OUT_ROOT = ROOT / "data/observations/dart_structural_content_index"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_RE = re.compile(r"^(source|content-run)-[0-9a-f]{16}\.json$")
MANIFEST_SNAPSHOT_RE = re.compile(
    r"^manifest-(\d{6})-(\d{14})-([0-9a-f]{16})\.json$"
)

AUTHORITY = {
    "structural_evidence_only": True,
    "semantic_item_extraction_authorized": False,
    "interpretation_authorized": False,
    "rule_evaluation_authorized": False,
    "stage_promotion_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class DartStructuralIndexError(ValueError):
    """Fail-closed structural-index or provenance violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise DartStructuralIndexError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DartStructuralIndexError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise DartStructuralIndexError(code)
    return parsed.astimezone(dt.timezone.utc)


def _json_object(raw: bytes, code: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DartStructuralIndexError(code) from exc
    if not isinstance(value, dict):
        raise DartStructuralIndexError(code)
    return value


def _evidence_as_of(source: dict, content_run: dict) -> str:
    """Return the deterministic PIT boundary owned by the exact inputs.

    The workflow's wall clock is only an upper bound proving that the inputs
    were already available.  Persisting that clock would create a different
    append-only packet on every same-input retry.  The packet therefore binds
    itself to the later of the two collectors' own timestamps instead.
    """
    value = max(
        _utc(source.get("collected_at_utc"), "DART_SOURCE_TIME_INVALID"),
        _utc(content_run.get("observed_at_utc"), "DART_CONTENT_TIME_INVALID"),
    )
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class _StructuralParser(HTMLParser):
    """Collect markup shape only; text and attribute values are discarded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[list[object]] = []
        self.start_tag_count = 0
        self.end_tag_count = 0
        self.table_count = 0
        self.row_count = 0
        self.cell_count = 0
        self.locator_attribute_count = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        attribute_names = sorted(
            name.lower() for name, _ in attrs if isinstance(name, str)
        )
        self.events.append(["start", lowered, attribute_names])
        self.start_tag_count += 1
        self.table_count += lowered == "table"
        self.row_count += lowered == "tr"
        self.cell_count += lowered in {"td", "th"}
        self.locator_attribute_count += sum(
            name in {"id", "name"} for name in attribute_names
        )

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.events.append(["end", tag.lower()])
        self.end_tag_count += 1

    def handle_endtag(self, tag: str) -> None:
        self.events.append(["end", tag.lower()])
        self.end_tag_count += 1

    def handle_data(self, data: str) -> None:
        # Explicitly discard all filing text and numeric values.
        del data


def structural_index(raw: bytes, member_name: str, text_extensions: set[str]) -> dict:
    extension = PurePosixPath(member_name).suffix.lower()
    if extension not in text_extensions:
        return {
            "status": "NOT_APPLICABLE_BINARY",
            "extension": extension,
            "start_tag_count": None,
            "end_tag_count": None,
            "table_count": None,
            "row_count": None,
            "cell_count": None,
            "locator_attribute_count": None,
            "structure_sha256": None,
        }
    try:
        decoded = DART.decode_document(raw)
        parser = _StructuralParser()
        parser.feed(decoded)
        parser.close()
    except Exception as exc:
        raise DartStructuralIndexError(
            f"STRUCTURAL_PARSE_FAILED:{type(exc).__name__}"
        ) from exc
    return {
        "status": "STRUCTURE_INDEXED_NO_SEMANTIC_ITEMS",
        "extension": extension,
        "start_tag_count": parser.start_tag_count,
        "end_tag_count": parser.end_tag_count,
        "table_count": parser.table_count,
        "row_count": parser.row_count,
        "cell_count": parser.cell_count,
        "locator_attribute_count": parser.locator_attribute_count,
        "structure_sha256": payload_sha256(parser.events),
    }


def _snapshot_names(source_bytes: bytes, content_bytes: bytes) -> tuple[str, str]:
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    content_sha = hashlib.sha256(content_bytes).hexdigest()
    return f"source-{source_sha[:16]}.json", f"content-run-{content_sha[:16]}.json"


def _raw_member(data_root: Path, manifest: dict, document: dict) -> bytes:
    identity = manifest["filing_identity"]
    directory = DART.manifest_dir(
        Path(data_root), identity["stock_code"], identity["rcept_no"]
    )
    target = directory / document["cache_name"]
    try:
        raw = gzip.decompress(target.read_bytes())
    except (OSError, EOFError) as exc:
        raise DartStructuralIndexError(
            f"RAW_MEMBER_CACHE_INVALID:{document['cache_name']}"
        ) from exc
    if (
        len(raw) != document["content_bytes"]
        or hashlib.sha256(raw).hexdigest() != document["content_sha256"]
    ):
        raise DartStructuralIndexError(
            f"RAW_MEMBER_CACHE_MUTATION:{document['cache_name']}"
        )
    return raw


def _manifest_bytes(data_root: Path, ticker: str, rcept_no: str) -> bytes:
    path = DART.manifest_dir(Path(data_root), ticker, rcept_no) / "_manifest.json"
    try:
        return path.read_bytes()
    except OSError as exc:
        raise DartStructuralIndexError(
            f"DART_MANIFEST_SNAPSHOT_READ_FAILED:{ticker}:{rcept_no}"
        ) from exc


def build_packet(
    *,
    decision_at: str,
    source_path: Path = DEFAULT_SOURCE,
    content_path: Path = DEFAULT_CONTENT,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> dict:
    requested_decision = _utc(decision_at, "DECISION_AT_INVALID")
    source_path = Path(source_path)
    content_path = Path(content_path)
    data_root = Path(data_root)
    source_bytes = source_path.read_bytes()
    content_bytes = content_path.read_bytes()
    source = _json_object(source_bytes, "DART_SOURCE_JSON_INVALID")
    content_run = _json_object(content_bytes, "DART_CONTENT_RUN_JSON_INVALID")
    evidence_as_of = _evidence_as_of(source, content_run)
    if _utc(evidence_as_of, "EVIDENCE_AS_OF_INVALID") > requested_decision:
        raise DartStructuralIndexError("EVIDENCE_AVAILABLE_AFTER_DECISION")

    # Reuse the operational P3-08 validator rather than maintaining a weaker
    # duplicate source/content-run contract here.  Only its already-verified
    # raw-content observations are eligible for structural indexing.
    observation_packet = DART_OBSERVATION.build_packet(
        decision_at=evidence_as_of,
        source_path=source_path,
        content_path=content_path,
        data_root=data_root,
    )
    DART_OBSERVATION.validate_packet(
        observation_packet,
        source_path=source_path,
        content_path=content_path,
        data_root=data_root,
    )
    source_name, content_name = _snapshot_names(source_bytes, content_bytes)
    contract = DART.load_contract()
    text_extensions = set(contract["archive_policy"]["text_member_extensions"])

    documents = []
    indexed_filings = []
    for observation in observation_packet["observations"]:
        if observation["evidence"]["status"] != (
            "RAW_BYTES_VERIFIED_ITEM_EXTRACTION_UNRATIFIED"
        ):
            continue
        ticker = observation["subject_id"]
        rcept_no = observation["rcept_no"]
        manifest = DART.load_existing_manifest(data_root, ticker, rcept_no)
        if manifest is None:
            raise DartStructuralIndexError(
                f"DART_MANIFEST_MISSING:{ticker}:{rcept_no}"
            )
        manifest_bytes = _manifest_bytes(data_root, ticker, rcept_no)
        if _json_object(manifest_bytes, "DART_MANIFEST_JSON_INVALID") != manifest:
            raise DartStructuralIndexError(
                f"DART_MANIFEST_BYTES_OBJECT_MISMATCH:{ticker}:{rcept_no}"
            )
        manifest_file_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        filing_document_count = 0
        for document in manifest["documents"]:
            raw = _raw_member(data_root, manifest, document)
            structure = structural_index(raw, document["member_name"], text_extensions)
            documents.append({
                "schema_version": DOCUMENT_SCHEMA_VERSION,
                "market": "KOREA",
                "subject_id": ticker,
                "rcept_no": rcept_no,
                "filing_date": observation["filing_date"],
                "available_at": observation["evidence"]["available_at"],
                "member_name": document["member_name"],
                "cache_name": document["cache_name"],
                "content_sha256": document["content_sha256"],
                "content_bytes": document["content_bytes"],
                "text_status": document["text_status"],
                "structural_index": structure,
                "semantic_items": [],
                "status": "STRUCTURE_ONLY_ITEM_EXTRACTION_UNRATIFIED",
            })
            filing_document_count += 1
        indexed_filings.append({
            "subject_id": ticker,
            "rcept_no": rcept_no,
            "manifest_snapshot_file": (
                f"manifest-{ticker}-{rcept_no}-{manifest_file_sha256[:16]}.json"
            ),
            "manifest_file_sha256": manifest_file_sha256,
            "manifest_payload_sha256": payload_sha256(manifest),
            "document_count": filing_document_count,
            "status": "STRUCTURAL_INDEX_READY_ITEM_EXTRACTION_UNRATIFIED",
        })

    documents.sort(key=lambda row: (row["subject_id"], row["rcept_no"], row["member_name"]))
    indexed_filings.sort(key=lambda row: (row["subject_id"], row["rcept_no"]))
    text_documents = [
        row for row in documents
        if row["structural_index"]["status"] == "STRUCTURE_INDEXED_NO_SEMANTIC_ITEMS"
    ]
    summary = {
        "source_observation_count": len(observation_packet["observations"]),
        "raw_bytes_verified_count": observation_packet["summary"]["raw_bytes_verified_count"],
        "indexed_filing_count": len(indexed_filings),
        "indexed_document_count": len(documents),
        "text_document_count": len(text_documents),
        "binary_document_count": len(documents) - len(text_documents),
        "table_count": sum(row["structural_index"]["table_count"] or 0 for row in documents),
        "row_count": sum(row["structural_index"]["row_count"] or 0 for row in documents),
        "cell_count": sum(row["structural_index"]["cell_count"] or 0 for row in documents),
        "semantic_item_count": 0,
        "rule_evaluation_count": 0,
        "stage_promotion_count": 0,
        "action_count": 0,
        "order_count": 0,
    }
    packet = {
        "schema_version": SCHEMA_VERSION,
        "decision_at": evidence_as_of,
        "decision_time_basis": "MAX_EXACT_SOURCE_AND_CONTENT_TIMESTAMPS",
        "source_date": source["collected_for_kst_date"],
        "status": (
            "STRUCTURAL_INDEX_RECORDED_ITEM_EXTRACTION_UNRATIFIED"
            if documents
            else "NO_RAW_CONTENT_AVAILABLE_STRUCTURAL_INDEX_EMPTY"
        ),
        "lineage": {
            "source_snapshot_file": source_name,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "content_run_snapshot_file": content_name,
            "content_run_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "content_contract_version": content_run["contract_version"],
            "observation_contract_version": observation_packet["schema_version"],
        },
        "summary": summary,
        "indexed_filings": indexed_filings,
        "documents": documents,
        "blocked_reasons": [
            "DART_ITEM_EXTRACTION_POLICY_UNRATIFIED",
            "STRUCTURAL_INDEX_IS_NOT_SEMANTIC_EVIDENCE",
        ],
        "authority": copy.deepcopy(AUTHORITY),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def _snapshot_path(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or SNAPSHOT_RE.fullmatch(name) is None:
        raise DartStructuralIndexError("SNAPSHOT_NAME_INVALID")
    target = directory / name
    if target.parent != directory:
        raise DartStructuralIndexError("SNAPSHOT_PATH_INVALID")
    return target


def _manifest_snapshot_path(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or MANIFEST_SNAPSHOT_RE.fullmatch(name) is None:
        raise DartStructuralIndexError("MANIFEST_SNAPSHOT_NAME_INVALID")
    target = directory / name
    if target.parent != directory:
        raise DartStructuralIndexError("MANIFEST_SNAPSHOT_PATH_INVALID")
    return target


def _materialize_exact_manifest_root(
    packet: dict,
    *,
    snapshot_dir: Path,
    raw_data_root: Path,
    target_root: Path,
) -> None:
    """Recreate only the exact manifests needed for independent validation.

    Raw archive/member bytes remain governed by the existing P4-03 retention
    contract.  They are copied from that cache and revalidated against each
    exact manifest snapshot; no current manifest metadata is substituted.
    """
    for filing in packet.get("indexed_filings") or []:
        ticker = filing.get("subject_id")
        rcept_no = filing.get("rcept_no")
        snapshot = _manifest_snapshot_path(
            snapshot_dir, filing.get("manifest_snapshot_file")
        )
        try:
            manifest_bytes = snapshot.read_bytes()
        except OSError as exc:
            raise DartStructuralIndexError("MANIFEST_SNAPSHOT_READ_FAILED") from exc
        if hashlib.sha256(manifest_bytes).hexdigest() != filing.get(
            "manifest_file_sha256"
        ):
            raise DartStructuralIndexError("MANIFEST_SNAPSHOT_HASH_MISMATCH")
        manifest = _json_object(manifest_bytes, "MANIFEST_SNAPSHOT_JSON_INVALID")
        if payload_sha256(manifest) != filing.get("manifest_payload_sha256"):
            raise DartStructuralIndexError("MANIFEST_SNAPSHOT_PAYLOAD_MISMATCH")
        identity = manifest.get("filing_identity") or {}
        if (
            identity.get("stock_code") != ticker
            or identity.get("rcept_no") != rcept_no
        ):
            raise DartStructuralIndexError("MANIFEST_SNAPSHOT_IDENTITY_MISMATCH")
        source_dir = DART.manifest_dir(raw_data_root, ticker, rcept_no)
        target_dir = DART.manifest_dir(target_root, ticker, rcept_no)
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "_manifest.json").write_bytes(manifest_bytes)
        required = ["_source.zip"] + [
            row["cache_name"] for row in manifest.get("documents") or []
        ]
        for name in required:
            source = source_dir / name
            if not source.is_file():
                raise DartStructuralIndexError(f"RAW_CACHE_FILE_MISSING:{name}")
            shutil.copyfile(source, target_dir / name)


def validate_packet(
    packet: dict,
    *,
    snapshot_dir: Path,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        raise DartStructuralIndexError("PACKET_SCHEMA_INVALID")
    lineage = packet.get("lineage")
    if not isinstance(lineage, dict):
        raise DartStructuralIndexError("PACKET_LINEAGE_INVALID")
    snapshot_dir = Path(snapshot_dir)
    source_path = _snapshot_path(snapshot_dir, lineage.get("source_snapshot_file"))
    content_path = _snapshot_path(snapshot_dir, lineage.get("content_run_snapshot_file"))
    try:
        source_bytes = source_path.read_bytes()
        content_bytes = content_path.read_bytes()
    except OSError as exc:
        raise DartStructuralIndexError("SNAPSHOT_READ_FAILED") from exc
    if (
        hashlib.sha256(source_bytes).hexdigest() != lineage.get("source_sha256")
        or hashlib.sha256(content_bytes).hexdigest() != lineage.get("content_run_sha256")
    ):
        raise DartStructuralIndexError("SNAPSHOT_HASH_MISMATCH")
    with tempfile.TemporaryDirectory() as temporary:
        exact_data_root = Path(temporary) / "data"
        _materialize_exact_manifest_root(
            packet,
            snapshot_dir=snapshot_dir,
            raw_data_root=Path(data_root),
            target_root=exact_data_root,
        )
        expected = build_packet(
            decision_at=packet.get("decision_at"),
            source_path=source_path,
            content_path=content_path,
            data_root=exact_data_root,
        )
    if packet != expected:
        raise DartStructuralIndexError("PACKET_DRIFT_OR_TAMPER")
    return copy.deepcopy(packet)


def _write_exclusive(target: Path, raw: bytes) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if target.read_bytes() != raw:
            raise DartStructuralIndexError(f"CONTENT_ADDRESSED_DRIFT:{target}")
        return False
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def publish_append_only(
    packet: dict,
    *,
    source_bytes: bytes,
    content_bytes: bytes,
    data_root: Path = DEFAULT_DATA_ROOT,
    out_root: Path = DEFAULT_OUT_ROOT,
) -> tuple[Path, bool]:
    lineage = packet.get("lineage") or {}
    if (
        hashlib.sha256(source_bytes).hexdigest() != lineage.get("source_sha256")
        or hashlib.sha256(content_bytes).hexdigest() != lineage.get("content_run_sha256")
    ):
        raise DartStructuralIndexError("PUBLICATION_SNAPSHOT_HASH_MISMATCH")
    # A public caller cannot persist a self-rehashed packet.  Rebuild from the
    # exact bytes before creating any output file.
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        source_input = temporary_root / lineage["source_snapshot_file"]
        content_input = temporary_root / lineage["content_run_snapshot_file"]
        source_input.write_bytes(source_bytes)
        content_input.write_bytes(content_bytes)
        expected = build_packet(
            decision_at=packet.get("decision_at"),
            source_path=source_input,
            content_path=content_input,
            data_root=Path(data_root),
        )
    if packet != expected:
        raise DartStructuralIndexError("PUBLICATION_PACKET_DRIFT_OR_TAMPER")

    directory = Path(out_root) / packet["source_date"]
    source_target = _snapshot_path(directory, lineage["source_snapshot_file"])
    content_target = _snapshot_path(directory, lineage["content_run_snapshot_file"])
    manifest_writes = []
    for filing in packet.get("indexed_filings") or []:
        ticker = filing["subject_id"]
        rcept_no = filing["rcept_no"]
        manifest_bytes = _manifest_bytes(Path(data_root), ticker, rcept_no)
        if hashlib.sha256(manifest_bytes).hexdigest() != filing[
            "manifest_file_sha256"
        ]:
            raise DartStructuralIndexError("PUBLICATION_MANIFEST_HASH_MISMATCH")
        manifest_target = _manifest_snapshot_path(
            directory, filing["manifest_snapshot_file"]
        )
        manifest_writes.append((manifest_target, manifest_bytes))
    # All derivation and byte/hash checks have passed.  Publish inputs first and
    # the packet last, so the packet's presence is the completion marker.
    _write_exclusive(source_target, source_bytes)
    _write_exclusive(content_target, content_bytes)
    for manifest_target, manifest_bytes in manifest_writes:
        _write_exclusive(manifest_target, manifest_bytes)
    packet_raw = (
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    target = directory / f"packet-{packet['packet_sha256'][:16]}.json"
    created = _write_exclusive(target, packet_raw)
    return target, created


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--content", type=Path, default=DEFAULT_CONTENT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = parser.parse_args(argv)
    source_bytes = args.source.read_bytes()
    content_bytes = args.content.read_bytes()
    packet = build_packet(
        decision_at=args.decision_at,
        source_path=args.source,
        content_path=args.content,
        data_root=args.data_root,
    )
    target, created = publish_append_only(
        packet,
        source_bytes=source_bytes,
        content_bytes=content_bytes,
        data_root=args.data_root,
        out_root=args.out_root,
    )
    validate_packet(packet, snapshot_dir=target.parent, data_root=args.data_root)
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
    except (
        DartStructuralIndexError,
        DART.DartContentError,
        DART_OBSERVATION.DartEventObservationError,
        OSError,
    ) as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
