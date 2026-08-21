#!/usr/bin/env python3
"""P4-03 OpenDART original-document acquisition.

The existing DART collector discovers relevant filings.  This helper consumes the
official receipt ZIP without pretending that a title is filing content.  It preserves
the complete archive and a member index, while item extraction remains fail-closed
until an explicit policy is ratified.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "dart_filing_content_contract.json"
DEFAULT_SOURCE = ROOT / "data" / "latest_dart.json"
DEFAULT_DATA_ROOT = ROOT / "data"

SCHEMA_VERSION = "dart_filing_content/1"
RUN_SCHEMA_VERSION = "dart_filing_content_run/1"
ENDPOINT = "https://opendart.fss.or.kr/api/document.xml"
ALLOWED_HOST = "opendart.fss.or.kr"
RCEPT_NO_RE = re.compile(r"^\d{14}$")
STOCK_CODE_RE = re.compile(r"^\d{6}$")
UTC_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SOURCE_URI_RE = re.compile(
    r"^https://dart\.fss\.or\.kr/dsaf001/main\.do\?rcpNo=(\d{14})$"
)
XML_ENCODING_RE = re.compile(
    br"<\?xml[^>]+encoding=[\"']([A-Za-z0-9._-]+)[\"']", re.I
)


class DartContentError(ValueError):
    """Fail-closed DART content contract violation."""


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DartContentError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise DartContentError(f"JSON_TOP_LEVEL_NOT_OBJECT:{path}")
    return value


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = _read_json(path)
    if contract.get("schema_version") != 1:
        raise DartContentError("CONTRACT_SCHEMA_MISMATCH")
    if contract.get("authority") != {
        "evidence_only": True,
        "item_extraction_authorized": False,
        "interpretation_authorized": False,
        "rule_evaluation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }:
        raise DartContentError("AUTHORITY_BOUNDARY_MISMATCH")
    return contract


def _base_status(content: str, evidence: str) -> dict:
    return {
        "discovery_status": "OK",
        "content_status": content,
        "evidence_status": evidence,
        "interpretation_status": "UNDETERMINED",
        "rule_impact": "NONE",
        "action": "NO_CHANGE",
    }


def filing_plan(filing: dict, stage: str | None, contract: dict) -> dict:
    """Return a content plan without inferring filing importance or Stage."""
    title = filing.get("title")
    keywords = contract["filing_policy"]["material_title_keywords"]
    relevant = isinstance(title, str) and any(word in title for word in keywords)
    plan = {
        "filing_classification": (
            "MATERIAL_RELEVANT_TITLE" if relevant else "UNCLASSIFIED_TITLE"
        ),
        "capture_policy": "index_only",
        **_base_status("NOT_APPLICABLE", "NOT_APPLICABLE"),
        "reasons": [],
    }
    if not relevant:
        plan["reasons"] = ["TITLE_NOT_IN_RATIFIED_RELEVANT_SET"]
        return plan

    stages = contract["stage_policy"]
    if stage in stages["required"]:
        plan.update(
            capture_policy="required",
            content_status="PENDING",
            evidence_status="PENDING",
            reasons=["CONTENT_NOT_ACQUIRED"],
        )
    elif stage in stages["best_effort"]:
        plan.update(
            capture_policy="best_effort",
            content_status="PENDING",
            evidence_status="PENDING",
            reasons=["CONTENT_NOT_ACQUIRED"],
        )
    else:
        plan["reasons"] = ["STAGE_NOT_ASSIGNED_FOR_AUTO_CONSUMPTION"]
    return plan


def validate_filing_identity(filing: dict) -> str:
    rcept_no = filing.get("rcept_no")
    if not isinstance(rcept_no, str) or not RCEPT_NO_RE.fullmatch(rcept_no):
        raise DartContentError(f"RCEPT_NO_INVALID:{rcept_no!r}")
    if not isinstance(filing.get("date"), str) or not re.fullmatch(
        r"\d{8}", filing["date"]
    ):
        raise DartContentError("FILING_DATE_INVALID")
    match = SOURCE_URI_RE.fullmatch(filing.get("url") or "")
    if match is None or match.group(1) != rcept_no:
        raise DartContentError("FILING_URL_IDENTITY_MISMATCH")
    return rcept_no


def canonical_source_uri(rcept_no: str) -> str:
    if not RCEPT_NO_RE.fullmatch(rcept_no):
        raise DartContentError(f"RCEPT_NO_INVALID:{rcept_no!r}")
    return f"{ENDPOINT}?rcept_no={rcept_no}"


def _validate_provider_url(url: str, rcept_no: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALLOWED_HOST
        or parsed.path != "/api/document.xml"
    ):
        raise DartContentError("DART_PROVIDER_REDIRECT_INVALID")
    values = parse_qs(parsed.query).get("rcept_no", [])
    if values != [rcept_no]:
        raise DartContentError("DART_PROVIDER_RECEIPT_IDENTITY_CHANGED")


def default_fetcher(api_key: str, max_zip_bytes: int):
    if not re.fullmatch(r"[A-Za-z0-9]{40}", api_key or ""):
        raise DartContentError("DART_API_KEY_MISSING_OR_INVALID")

    def fetch(rcept_no: str) -> bytes:
        if not RCEPT_NO_RE.fullmatch(rcept_no):
            raise DartContentError(f"RCEPT_NO_INVALID:{rcept_no!r}")
        request_url = f"{ENDPOINT}?{urlencode({'crtfc_key': api_key, 'rcept_no': rcept_no})}"
        request = Request(
            request_url,
            headers={"Accept": "application/zip, application/xml"},
        )
        try:
            with urlopen(request, timeout=45) as response:  # noqa: S310 - fixed host
                _validate_provider_url(response.geturl(), rcept_no)
                return response.read(max_zip_bytes + 1)
        except DartContentError:
            raise
        except (HTTPError, URLError, OSError) as exc:
            # Never stringify the exception: urllib errors may include the secret URL.
            raise DartContentError(
                f"DART_FETCH_FAILED:{type(exc).__name__}"
            ) from exc

    return fetch


def _decode_document(raw: bytes) -> str:
    declared = XML_ENCODING_RE.search(raw[:512])
    encodings = []
    if declared:
        encodings.append(declared.group(1).decode("ascii", errors="ignore"))
    encodings.extend(["utf-8", "cp949", "euc-kr"])
    tried = set()
    for encoding in encodings:
        key = encoding.lower()
        if key in tried:
            continue
        tried.add(key)
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise DartContentError("DOCUMENT_ENCODING_UNSUPPORTED")


def normalized_visible_text(raw: bytes) -> str:
    parser = _VisibleText()
    try:
        parser.feed(_decode_document(raw))
    except Exception as exc:
        if isinstance(exc, DartContentError):
            raise
        raise DartContentError(f"DOCUMENT_PARSE_FAILED:{type(exc).__name__}") from exc
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _error_status(raw: bytes) -> str | None:
    if raw.startswith(b"PK"):
        return None
    text = raw[:4096].decode("utf-8", errors="ignore")
    match = re.search(r"<status>\s*([^<]+)\s*</status>", text, re.I)
    return match.group(1).strip() if match else None


def _validate_member_name(name: str, max_chars: int) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise DartContentError(f"ARCHIVE_MEMBER_NAME_INVALID:{name!r}")
    if len(name) > max_chars:
        raise DartContentError(
            f"ARCHIVE_MEMBER_NAME_OVERSIZE:{len(name)}>{max_chars}"
        )
    path = PurePosixPath(name)
    if path.is_absolute() or len(path.parts) != 1 or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise DartContentError(f"ARCHIVE_MEMBER_PATH_INVALID:{name!r}")


def parse_archive(raw_zip: bytes, contract: dict) -> tuple[list[dict], dict[str, bytes]]:
    policy = contract["archive_policy"]
    if len(raw_zip) > policy["max_zip_bytes"]:
        raise DartContentError("ARCHIVE_RESPONSE_OVERSIZE")
    status_code = _error_status(raw_zip)
    if status_code is not None:
        raise DartContentError(f"DART_API_ERROR:{status_code}")

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_zip))
    except zipfile.BadZipFile as exc:
        raise DartContentError("ARCHIVE_INVALID_ZIP") from exc

    with archive:
        infos = archive.infolist()
        if not infos:
            raise DartContentError("ARCHIVE_EMPTY")
        if len(infos) > policy["max_members"]:
            raise DartContentError(
                f"ARCHIVE_MEMBER_LIMIT_EXCEEDED:{len(infos)}>{policy['max_members']}"
            )
        names = set()
        total = 0
        documents = []
        raw_by_cache_name = {}
        for index, info in enumerate(infos, 1):
            _validate_member_name(
                info.filename, policy["max_member_name_chars"]
            )
            if info.filename in names:
                raise DartContentError(f"ARCHIVE_MEMBER_DUPLICATE:{info.filename}")
            names.add(info.filename)
            if info.is_dir():
                raise DartContentError("ARCHIVE_DIRECTORY_MEMBER_NOT_ALLOWED")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise DartContentError("ARCHIVE_SYMLINK_MEMBER_NOT_ALLOWED")
            if info.flag_bits & 0x1:
                raise DartContentError("ARCHIVE_ENCRYPTED_MEMBER_NOT_ALLOWED")
            if info.file_size > policy["max_member_bytes"]:
                raise DartContentError(
                    f"ARCHIVE_MEMBER_OVERSIZE:{info.filename}:{info.file_size}"
                )
            total += info.file_size
            if total > policy["max_total_uncompressed_bytes"]:
                raise DartContentError("ARCHIVE_TOTAL_UNCOMPRESSED_OVERSIZE")
            try:
                member = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise DartContentError(
                    f"ARCHIVE_MEMBER_READ_FAILED:{info.filename}"
                ) from exc
            if len(member) != info.file_size:
                raise DartContentError(f"ARCHIVE_MEMBER_TRUNCATED:{info.filename}")
            digest = hashlib.sha256(member).hexdigest()
            cache_name = f"member-{index:03d}-{digest[:16]}.gz"
            raw_by_cache_name[cache_name] = member
            document = {
                "member_name": info.filename,
                "cache_name": cache_name,
                "content_sha256": digest,
                "content_bytes": len(member),
            }
            extension = PurePosixPath(info.filename).suffix.lower()
            if extension in policy["text_member_extensions"]:
                text = normalized_visible_text(member)
                document.update(
                    text_status="OK",
                    normalized_text_sha256=hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                    normalized_text_chars=len(text),
                    offset_basis="normalized_visible_text",
                )
            else:
                document.update(
                    text_status="NOT_APPLICABLE_BINARY",
                    normalized_text_sha256=None,
                    normalized_text_chars=None,
                    offset_basis=None,
                )
            documents.append(document)
    return documents, raw_by_cache_name


def _raw_cache_policy(plan: dict, stage: str | None, contract: dict) -> str:
    retention = contract["retention_policy"]
    if (
        plan["capture_policy"] == "required"
        or stage in retention["permanent_raw_stages"]
    ):
        return "permanent"
    return f"delete_after_{retention['raw_cache_days']}_days_allowed"


def capture_filing(
    *,
    ticker: str,
    stage: str | None,
    filing: dict,
    fetcher,
    retrieved_at_utc: str,
    contract: dict,
    existing_manifest: dict | None = None,
    force_refresh: bool = False,
) -> tuple[dict, bytes | None, dict[str, bytes]]:
    plan = filing_plan(filing, stage, contract)
    rcept_no = filing.get("rcept_no", "")
    identity = {"stock_code": ticker, "rcept_no": rcept_no}
    result = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "name": filing.get("corp_name"),
        "atlas_stage": stage,
        "filing_date": filing.get("date"),
        "title": filing.get("title"),
        "filing_identity": identity,
        "extractor_version": contract["extractor_version"],
        **plan,
        "source_archive": None,
        "documents": [],
        "extracted": [],
        "raw_cache_policy": _raw_cache_policy(plan, stage, contract),
    }
    if plan["capture_policy"] == "index_only":
        return result, None, {}

    try:
        rcept_no = validate_filing_identity(filing)
        identity["rcept_no"] = rcept_no
        if existing_manifest and not force_refresh:
            if (
                existing_manifest.get("filing_identity") == identity
                and existing_manifest.get("extractor_version")
                == contract["extractor_version"]
                and existing_manifest.get("content_status") == "OK"
            ):
                skipped = copy.deepcopy(existing_manifest)
                skipped["atlas_stage"] = stage
                skipped["raw_cache_policy"] = _raw_cache_policy(
                    plan, stage, contract
                )
                skipped["operation"] = "skipped"
                skipped["skip_reason"] = "already_captured"
                return validate_manifest(skipped, contract=contract), None, {}

        raw_zip = fetcher(rcept_no)
        documents, raw_members = parse_archive(raw_zip, contract)
        source_archive = {
            "source_uri": canonical_source_uri(rcept_no),
            "rcept_no": rcept_no,
            "content_sha256": hashlib.sha256(raw_zip).hexdigest(),
            "content_bytes": len(raw_zip),
        }
        if existing_manifest:
            old = (existing_manifest.get("source_archive") or {}).get(
                "content_sha256"
            )
            if old and old != source_archive["content_sha256"]:
                raise DartContentError(
                    "SOURCE_MUTATED_FAIL_CLOSED_NO_OVERWRITE"
                )
        result.update(
            source_archive=source_archive,
            documents=documents,
            retrieved_at_utc=retrieved_at_utc,
            content_status="OK",
            evidence_status="PENDING",
            reasons=["ITEM_EXTRACTION_POLICY_UNRATIFIED"],
            operation="captured",
        )
        return validate_manifest(
            result, raw_zip, raw_members, contract
        ), raw_zip, raw_members
    except Exception as exc:
        result.update(
            content_status="PENDING",
            evidence_status="PENDING",
            reasons=[f"{type(exc).__name__}:{exc}"],
            operation="failed",
            source_archive=None,
            documents=[],
            extracted=[],
        )
        return result, None, {}


def _valid_date_yyyymmdd(value) -> bool:
    if not isinstance(value, str) or re.fullmatch(r"\d{8}", value) is None:
        return False
    try:
        return dt.datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value
    except ValueError:
        return False


def _valid_utc(value) -> bool:
    if not isinstance(value, str) or UTC_ISO_RE.fullmatch(value) is None:
        return False
    try:
        return (
            dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            == value
        )
    except ValueError:
        return False


def validate_manifest(
    manifest: dict,
    raw_zip: bytes | None = None,
    raw_members: dict[str, bytes] | None = None,
    contract: dict | None = None,
) -> dict:
    """Validate one persisted successful DART receipt and optional raw cache."""
    contract = contract if contract is not None else load_contract()
    base_fields = {
        "schema_version",
        "ticker",
        "name",
        "atlas_stage",
        "filing_date",
        "title",
        "filing_identity",
        "extractor_version",
        "filing_classification",
        "capture_policy",
        "discovery_status",
        "content_status",
        "evidence_status",
        "interpretation_status",
        "rule_impact",
        "action",
        "reasons",
        "source_archive",
        "documents",
        "extracted",
        "raw_cache_policy",
        "retrieved_at_utc",
        "operation",
    }
    allowed_fields = [base_fields]
    allowed_fields.append(base_fields | {"publication_status"})
    allowed_fields.append(base_fields | {"skip_reason"})
    allowed_fields.append(base_fields | {"publication_status", "skip_reason"})
    if not isinstance(manifest, dict) or set(manifest) not in allowed_fields:
        raise DartContentError("MANIFEST_FIELDS_MISMATCH")
    ticker = manifest.get("ticker")
    name = manifest.get("name")
    stage = manifest.get("atlas_stage")
    filing_date = manifest.get("filing_date")
    title = manifest.get("title")
    retrieved_at_utc = manifest.get("retrieved_at_utc")
    if not isinstance(ticker, str) or STOCK_CODE_RE.fullmatch(ticker) is None:
        raise DartContentError("MANIFEST_STOCK_CODE_INVALID")
    if name is not None and (
        not isinstance(name, str) or not name.strip() or name.strip() != name
    ):
        raise DartContentError("MANIFEST_COMPANY_NAME_INVALID")
    if not isinstance(title, str) or not title.strip() or title.strip() != title:
        raise DartContentError("MANIFEST_TITLE_INVALID")
    if stage not in (
        set(contract["stage_policy"]["required"])
        | set(contract["stage_policy"]["best_effort"])
    ):
        raise DartContentError("MANIFEST_STAGE_INVALID")
    if (
        not _valid_date_yyyymmdd(filing_date)
        or not _valid_utc(retrieved_at_utc)
        or dt.datetime.strptime(filing_date, "%Y%m%d").date()
        > dt.datetime.strptime(retrieved_at_utc, "%Y-%m-%dT%H:%M:%SZ").date()
    ):
        raise DartContentError("MANIFEST_TIME_INVALID")
    identity = manifest.get("filing_identity")
    if not isinstance(identity, dict) or set(identity) != {"stock_code", "rcept_no"}:
        raise DartContentError("MANIFEST_FILING_IDENTITY_FIELDS_MISMATCH")
    rcept_no = identity.get("rcept_no")
    if identity.get("stock_code") != ticker:
        raise DartContentError("MANIFEST_STOCK_IDENTITY_MISMATCH")
    if not isinstance(rcept_no, str) or RCEPT_NO_RE.fullmatch(rcept_no) is None:
        raise DartContentError("MANIFEST_RCEPT_NO_INVALID")

    plan = filing_plan({"title": title}, stage, contract)
    expected_cache_policy = _raw_cache_policy(plan, stage, contract)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("extractor_version") != contract["extractor_version"]
        or plan["filing_classification"] != "MATERIAL_RELEVANT_TITLE"
        or manifest.get("filing_classification") != plan["filing_classification"]
        or manifest.get("capture_policy") != plan["capture_policy"]
        or manifest.get("discovery_status") != "OK"
        or manifest.get("content_status") != "OK"
        or manifest.get("evidence_status") != "PENDING"
        or manifest.get("interpretation_status") != "UNDETERMINED"
        or manifest.get("rule_impact") != "NONE"
        or manifest.get("action") != "NO_CHANGE"
        or manifest.get("reasons") != ["ITEM_EXTRACTION_POLICY_UNRATIFIED"]
        or manifest.get("extracted") != []
        or manifest.get("raw_cache_policy") != expected_cache_policy
        or manifest.get("publication_status", "OK") != "OK"
    ):
        raise DartContentError("MANIFEST_STATUS_OR_AUTHORITY_MISMATCH")
    operation = manifest.get("operation")
    if operation not in {"captured", "skipped"}:
        raise DartContentError("MANIFEST_OPERATION_INVALID")
    if (
        operation == "skipped"
        and manifest.get("skip_reason") != "already_captured"
    ) or (operation == "captured" and "skip_reason" in manifest):
        raise DartContentError("MANIFEST_SKIP_REASON_MISMATCH")

    archive = manifest.get("source_archive")
    if not isinstance(archive, dict) or set(archive) != {
        "source_uri",
        "rcept_no",
        "content_sha256",
        "content_bytes",
    }:
        raise DartContentError("MANIFEST_SOURCE_ARCHIVE_FIELDS_MISMATCH")
    digest = archive.get("content_sha256")
    size = archive.get("content_bytes")
    if (
        archive.get("source_uri") != canonical_source_uri(rcept_no)
        or archive.get("rcept_no") != rcept_no
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or type(size) is not int
        or size <= 0
        or size > contract["archive_policy"]["max_zip_bytes"]
    ):
        raise DartContentError("MANIFEST_SOURCE_ARCHIVE_IDENTITY_MISMATCH")

    documents = manifest.get("documents")
    policy = contract["archive_policy"]
    if (
        not isinstance(documents, list)
        or not documents
        or len(documents) > policy["max_members"]
    ):
        raise DartContentError("MANIFEST_DOCUMENT_COUNT_INVALID")
    member_names = []
    cache_names = []
    total_bytes = 0
    fields = {
        "member_name",
        "cache_name",
        "content_sha256",
        "content_bytes",
        "text_status",
        "normalized_text_sha256",
        "normalized_text_chars",
        "offset_basis",
    }
    for index, document in enumerate(documents, 1):
        if not isinstance(document, dict) or set(document) != fields:
            raise DartContentError(f"MANIFEST_DOCUMENT_FIELDS_MISMATCH:{index}")
        member_name = document.get("member_name")
        _validate_member_name(member_name, policy["max_member_name_chars"])
        content_sha256 = document.get("content_sha256")
        content_bytes = document.get("content_bytes")
        if (
            not isinstance(content_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None
            or type(content_bytes) is not int
            or content_bytes < 0
            or content_bytes > policy["max_member_bytes"]
            or document.get("cache_name")
            != f"member-{index:03d}-{content_sha256[:16]}.gz"
        ):
            raise DartContentError(f"MANIFEST_DOCUMENT_IDENTITY_MISMATCH:{index}")
        total_bytes += content_bytes
        extension = PurePosixPath(member_name).suffix.lower()
        if extension in policy["text_member_extensions"]:
            text_digest = document.get("normalized_text_sha256")
            text_chars = document.get("normalized_text_chars")
            if (
                document.get("text_status") != "OK"
                or not isinstance(text_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", text_digest) is None
                or type(text_chars) is not int
                or text_chars < 0
                or document.get("offset_basis") != "normalized_visible_text"
            ):
                raise DartContentError(f"MANIFEST_TEXT_INDEX_INVALID:{index}")
        elif (
            document.get("text_status") != "NOT_APPLICABLE_BINARY"
            or document.get("normalized_text_sha256") is not None
            or document.get("normalized_text_chars") is not None
            or document.get("offset_basis") is not None
        ):
            raise DartContentError(f"MANIFEST_BINARY_INDEX_INVALID:{index}")
        member_names.append(member_name)
        cache_names.append(document["cache_name"])
    if (
        len(member_names) != len(set(member_names))
        or len(cache_names) != len(set(cache_names))
        or total_bytes > policy["max_total_uncompressed_bytes"]
    ):
        raise DartContentError("MANIFEST_DOCUMENT_SET_INVALID")

    if raw_zip is not None or raw_members is not None:
        if not isinstance(raw_zip, bytes) or not isinstance(raw_members, dict):
            raise DartContentError("RAW_CACHE_INPUT_INCOMPLETE")
        if len(raw_zip) != size or hashlib.sha256(raw_zip).hexdigest() != digest:
            raise DartContentError("RAW_ARCHIVE_MUTATION")
        expected_documents, expected_members = parse_archive(raw_zip, contract)
        if documents != expected_documents or raw_members != expected_members:
            raise DartContentError("MANIFEST_ARCHIVE_DERIVATION_MISMATCH")
    return copy.deepcopy(manifest)


def manifest_dir(data_root: Path, ticker: str, rcept_no: str) -> Path:
    if not STOCK_CODE_RE.fullmatch(ticker or ""):
        raise DartContentError(f"STOCK_CODE_INVALID:{ticker!r}")
    if not RCEPT_NO_RE.fullmatch(rcept_no or ""):
        raise DartContentError(f"RCEPT_NO_INVALID:{rcept_no!r}")
    return data_root / "dart_content" / ticker / rcept_no


def load_existing_manifest(
    data_root: Path,
    ticker: str,
    rcept_no: str,
    contract: dict | None = None,
) -> dict | None:
    directory = manifest_dir(data_root, ticker, rcept_no)
    path = directory / "_manifest.json"
    if not path.exists():
        return None
    manifest = validate_manifest(_read_json(path), contract=contract)
    raw_zip, raw_members = _validate_existing_cache(directory, manifest)
    return validate_manifest(manifest, raw_zip, raw_members, contract)


def _json_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _gzip_bytes(raw: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as stream:
        stream.write(raw)
    return output.getvalue()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _validate_existing_cache(
    directory: Path, manifest: dict
) -> tuple[bytes, dict[str, bytes]]:
    source_path = directory / "_source.zip"
    archive = manifest.get("source_archive") or {}
    if not source_path.is_file():
        raise DartContentError("RAW_ARCHIVE_MISSING")
    raw_zip = source_path.read_bytes()
    if hashlib.sha256(raw_zip).hexdigest() != archive.get(
        "content_sha256"
    ):
        raise DartContentError("RAW_ARCHIVE_MUTATION")
    raw_members = {}
    for document in manifest.get("documents") or []:
        target = directory / document["cache_name"]
        if not target.is_file():
            raise DartContentError(
                f"RAW_MEMBER_CACHE_MISSING:{document['cache_name']}"
            )
        try:
            raw = gzip.decompress(target.read_bytes())
        except (OSError, EOFError) as exc:
            raise DartContentError(
                f"RAW_MEMBER_CACHE_INVALID:{document['cache_name']}"
            ) from exc
        if hashlib.sha256(raw).hexdigest() != document["content_sha256"]:
            raise DartContentError(
                f"RAW_MEMBER_CACHE_MUTATION:{document['cache_name']}"
            )
        raw_members[document["cache_name"]] = raw
    return raw_zip, raw_members


def persist_success(
    data_root: Path,
    manifest: dict,
    raw_zip: bytes | None,
    raw_members: dict[str, bytes],
    contract: dict | None = None,
) -> None:
    contract = contract if contract is not None else load_contract()
    validate_manifest(manifest, contract=contract)
    identity = manifest["filing_identity"]
    directory = manifest_dir(
        data_root, identity["stock_code"], identity["rcept_no"]
    )
    manifest_path = directory / "_manifest.json"
    if directory.exists():
        existing = _read_json(manifest_path)
        old_sha = (existing.get("source_archive") or {}).get("content_sha256")
        new_sha = (manifest.get("source_archive") or {}).get("content_sha256")
        if old_sha != new_sha:
            raise DartContentError(
                "SOURCE_MUTATED_FAIL_CLOSED_NO_OVERWRITE"
            )
        existing = validate_manifest(existing, contract=contract)
        cached_zip, cached_members = _validate_existing_cache(directory, existing)
        validate_manifest(existing, cached_zip, cached_members, contract)
        validate_manifest(manifest, cached_zip, cached_members, contract)
        _atomic_write(manifest_path, _json_bytes(manifest))
        return

    if raw_zip is None or not raw_members:
        raise DartContentError("RAW_CACHE_REQUIRED_FOR_NEW_CAPTURE")
    validate_manifest(manifest, raw_zip, raw_members, contract)
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{directory.name}.tmp.", dir=directory.parent)
    )
    try:
        _atomic_write(staging / "_source.zip", raw_zip)
        expected_cache = {doc["cache_name"] for doc in manifest["documents"]}
        if set(raw_members) != expected_cache:
            raise DartContentError("RAW_MEMBER_CACHE_INVENTORY_MISMATCH")
        for cache_name, raw in raw_members.items():
            if not re.fullmatch(r"member-\d{3}-[0-9a-f]{16}\.gz", cache_name):
                raise DartContentError(f"RAW_CACHE_NAME_INVALID:{cache_name}")
            _atomic_write(staging / cache_name, _gzip_bytes(raw))
        _atomic_write(staging / "_manifest.json", _json_bytes(manifest))
        os.replace(staging, directory)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def run_capture(
    *,
    source_path: Path,
    data_root: Path,
    expected_kst_date: str,
    retrieved_at_utc: str,
    fetcher,
    contract: dict,
) -> dict:
    try:
        dt.date.fromisoformat(expected_kst_date)
    except (TypeError, ValueError) as exc:
        raise DartContentError(
            f"EXPECTED_DATE_INVALID:{expected_kst_date}"
        ) from exc
    if not UTC_ISO_RE.fullmatch(retrieved_at_utc or ""):
        raise DartContentError(f"OBSERVED_AT_INVALID:{retrieved_at_utc}")
    source = _read_json(source_path)
    if source.get("collected_for_kst_date") != expected_kst_date:
        raise DartContentError(
            f"SOURCE_DATE_MISMATCH:{source.get('collected_for_kst_date')}:{expected_kst_date}"
        )
    stocks = source.get("stocks")
    if not isinstance(stocks, dict):
        raise DartContentError("SOURCE_STOCKS_MISSING")

    records = []
    counts = {"captured": 0, "skipped": 0, "failed": 0, "not_applicable": 0}
    seen_receipts = set()
    for ticker, stock in sorted(stocks.items()):
        if not isinstance(stock, dict) or stock.get("status") != "ok":
            continue
        stage = stock.get("atlas_stage")
        filings = stock.get("relevant") or []
        if not isinstance(filings, list):
            raise DartContentError(f"SOURCE_RELEVANT_INVALID:{ticker}")
        for source_filing in filings:
            if not isinstance(source_filing, dict):
                raise DartContentError(f"SOURCE_FILING_INVALID:{ticker}")
            filing = {**source_filing, "corp_name": stock.get("name")}
            rcept_no = filing.get("rcept_no", "")
            identity_key = (ticker, rcept_no)
            if identity_key in seen_receipts:
                raise DartContentError(f"SOURCE_FILING_DUPLICATE:{ticker}:{rcept_no}")
            seen_receipts.add(identity_key)
            record = None
            try:
                existing = load_existing_manifest(
                    data_root, ticker, rcept_no, contract
                )
                record, raw_zip, raw_members = capture_filing(
                    ticker=ticker,
                    stage=stage,
                    filing=filing,
                    fetcher=fetcher,
                    retrieved_at_utc=retrieved_at_utc,
                    contract=contract,
                    existing_manifest=existing,
                )
                if record["content_status"] == "NOT_APPLICABLE":
                    record["publication_status"] = "NOT_APPLICABLE"
                    counts["not_applicable"] += 1
                elif record.get("operation") == "captured":
                    persist_success(
                        data_root, record, raw_zip, raw_members, contract
                    )
                    record["publication_status"] = "OK"
                    counts["captured"] += 1
                elif record.get("operation") == "skipped":
                    persist_success(data_root, record, None, {}, contract)
                    record["publication_status"] = "OK"
                    counts["skipped"] += 1
                else:
                    record["publication_status"] = "FAILED"
                    counts["failed"] += 1
            except Exception as exc:
                if record is None:
                    plan = filing_plan(filing, stage, contract)
                    record = {
                        "schema_version": SCHEMA_VERSION,
                        "ticker": ticker,
                        "name": stock.get("name"),
                        "atlas_stage": stage,
                        "filing_date": filing.get("date"),
                        "title": filing.get("title"),
                        "filing_identity": {
                            "stock_code": ticker,
                            "rcept_no": rcept_no,
                        },
                        "extractor_version": contract["extractor_version"],
                        **plan,
                        "source_archive": None,
                        "documents": [],
                        "extracted": [],
                    }
                record["operation"] = "failed"
                record["publication_status"] = "FAILED"
                record["reasons"] = list(record.get("reasons") or []) + [
                    f"PERSIST_OR_CACHE_FAILED:{type(exc).__name__}:{exc}"
                ]
                counts["failed"] += 1
            records.append(record)

    run = {
        "schema_version": RUN_SCHEMA_VERSION,
        "source_file": str(source_path),
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "collected_for_kst_date": expected_kst_date,
        "observed_at_utc": retrieved_at_utc,
        "contract_version": contract["contract_version"],
        "run_status": "OK" if counts["failed"] == 0 else "DEGRADED",
        "counts": counts,
        "records": records,
        "authority": contract["authority"],
    }
    _atomic_write(data_root / "latest_dart_content.json", _json_bytes(run))
    return run


def publish_failure_run(
    *,
    source_path: Path,
    data_root: Path,
    expected_kst_date: str,
    observed_at_utc: str,
    contract: dict,
    error: Exception,
) -> dict:
    source_sha256 = None
    if source_path.is_file():
        source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    run = {
        "schema_version": RUN_SCHEMA_VERSION,
        "source_file": str(source_path),
        "source_sha256": source_sha256,
        "collected_for_kst_date": expected_kst_date,
        "observed_at_utc": observed_at_utc,
        "contract_version": contract["contract_version"],
        "run_status": "FAILED",
        "counts": {"captured": 0, "skipped": 0, "failed": 1, "not_applicable": 0},
        "records": [],
        "reasons": [f"{type(error).__name__}:{error}"],
        "authority": contract["authority"],
    }
    _atomic_write(data_root / "latest_dart_content.json", _json_bytes(run))
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--expected-kst-date", required=True)
    parser.add_argument("--observed-at-utc", required=True)
    args = parser.parse_args(argv)
    contract = load_contract()
    try:
        fetcher = default_fetcher(
            os.getenv("DART_API_KEY", "").strip(),
            contract["archive_policy"]["max_zip_bytes"],
        )
        run = run_capture(
            source_path=args.source,
            data_root=args.data_root,
            expected_kst_date=args.expected_kst_date,
            retrieved_at_utc=args.observed_at_utc,
            fetcher=fetcher,
            contract=contract,
        )
    except Exception as exc:
        run = publish_failure_run(
            source_path=args.source,
            data_root=args.data_root,
            expected_kst_date=args.expected_kst_date,
            observed_at_utc=args.observed_at_utc,
            contract=contract,
            error=exc,
        )
    print(json.dumps(run["counts"], ensure_ascii=False, sort_keys=True))
    return 1 if run["counts"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
