#!/usr/bin/env python3
"""P4-02 SEC filing primary/exhibit acquisition and evidence extraction.

This module implements the approved Evidence-layer boundary only:

* arrival, content consumption, extraction and interpretation are separate axes;
* Stage comes from the upstream PM Watchlist snapshot and is never inferred here;
* material primary documents and every discovered ``EX-99.*`` are acquired together;
* size, identity, ambiguity and source-mutation failures are fail-closed;
* canonical evidence is hash + URL + extracted quote/offset.  Raw bytes are cache;
* no bullish/bearish meaning, Rule result, Production state or trading action is made.

The CLI can repair content when the daily metadata collector was already fresh.  It
therefore validates ``collected_for_kst_date`` independently before using the source.
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
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "sec_filing_content_contract.json"
DEFAULT_SOURCE = ROOT / "data" / "latest_sec.json"
DEFAULT_DATA_ROOT = ROOT / "data"

SCHEMA_VERSION = "sec_filing_content/1"
RUN_SCHEMA_VERSION = "sec_filing_content_run/1"
ALLOWED_SEC_HOSTS = {"www.sec.gov", "sec.gov"}
MAX_HTTP_RESPONSE_BYTES = 20971521
POLITE_DELAY_SECONDS = 0.15
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
UTC_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_DOCUMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
DOC_BLOCK_RE = re.compile(r"<DOCUMENT>(.*?)(?:</DOCUMENT>|\Z)", re.I | re.S)
SGML_FIELD_RE = re.compile(
    r"^<(TYPE|SEQUENCE|FILENAME|DESCRIPTION)>(.*)$", re.I | re.M
)


class SecContentError(ValueError):
    """Fail-closed SEC content contract violation."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecContentError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise SecContentError(f"JSON_TOP_LEVEL_NOT_OBJECT:{path}")
    return value


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = _read_json(path)
    if contract.get("schema_version") != 1:
        raise SecContentError("CONTRACT_SCHEMA_MISMATCH")
    authority = contract.get("authority") or {}
    if authority != {
        "evidence_only": True,
        "interpretation_authorized": False,
        "rule_evaluation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }:
        raise SecContentError("AUTHORITY_BOUNDARY_MISMATCH")
    return contract


def _base_status(discovery: str, content: str, evidence: str) -> dict:
    return {
        "discovery_status": discovery,
        "content_status": content,
        "evidence_status": evidence,
        "interpretation_status": "UNDETERMINED",
        "rule_impact": "NONE",
        "action": "NO_CHANGE",
    }


def form_class(form: str, contract: dict) -> str:
    form = (form or "").strip().upper()
    policy = contract["form_policy"]
    base = form.removesuffix("/A")
    if base in policy["material"]:
        return "MATERIAL"
    if base in policy["context_exact"] or any(
        base.startswith(prefix) for prefix in policy["context_prefixes"]
    ):
        return "CONTEXT"
    if base in policy["out_of_scope_exact"]:
        return "OUT_OF_SCOPE_FOR_AUTO_CONSUMPTION"
    return "UNCLASSIFIED"


def filing_plan(filing: dict, stage: str | None, contract: dict) -> dict:
    """Return acquisition policy without inferring Stage or filing importance."""
    cls = form_class(filing.get("form", ""), contract)
    plan = {
        "form_classification": cls,
        "capture_policy": "index_only",
        **_base_status("OK", "NOT_APPLICABLE", "NOT_APPLICABLE"),
        "reasons": [],
    }
    if cls != "MATERIAL":
        plan["reasons"] = [f"FORM_{cls}"]
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


def parse_sgml_documents(source: bytes | str) -> list[dict]:
    text = source.decode("utf-8", errors="replace") if isinstance(source, bytes) else source
    documents = []
    for block in DOC_BLOCK_RE.findall(text):
        header = re.split(r"<TEXT>", block, maxsplit=1, flags=re.I)[0]
        row = {"type": "", "sequence": "", "filename": "", "description": ""}
        for key, value in SGML_FIELD_RE.findall(header):
            field = "filename" if key.upper() == "FILENAME" else key.lower()
            row[field] = value.strip()
        if row["type"] or row["filename"]:
            documents.append(row)
    return documents


def parse_index_types(source: bytes | str) -> dict[str, str]:
    text = source.decode("utf-8", errors="replace") if isinstance(source, bytes) else source
    found: dict[str, str] = {}
    for row in re.findall(r"<tr\b.*?</tr>", text, re.I | re.S):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.I | re.S)
        if len(cells) < 4:
            continue
        link_cell = cells[2]
        href = re.search(r"href=[\"']([^\"']+)[\"']", link_cell, re.I)
        target = href.group(1) if href else ""
        parsed = urlparse(target)
        # Inline-XBRL index pages may wrap the actual document in a ``doc`` query.
        target = parse_qs(parsed.query).get("doc", [parsed.path])[0]
        name = os.path.basename(target)
        kind = re.sub(r"<[^>]+>", " ", cells[3])
        kind = re.sub(r"\s+", " ", kind).strip()
        if name:
            found[name] = kind
    return found


def select_ex99_documents(
    sgml_documents: list[dict], index_types: dict[str, str], contract: dict
) -> list[dict]:
    prefix = contract["document_policy"]["exhibit_type_prefix"].upper()
    selected = [row for row in sgml_documents if row["type"].upper().startswith(prefix)]
    limit = contract["document_policy"]["max_exhibits"]
    if len(selected) > limit:
        raise SecContentError(f"EXHIBIT_LIMIT_EXCEEDED:{len(selected)}>{limit}")

    names = set()
    for row in selected:
        name = row["filename"].strip()
        if not name or not SAFE_DOCUMENT_RE.fullmatch(name) or name in names:
            raise SecContentError(f"EXHIBIT_IDENTITY_INVALID:{name!r}")
        names.add(name)
        secondary = index_types.get(name)
        if secondary is None:
            raise SecContentError(f"EXHIBIT_SECONDARY_IDENTITY_MISSING:{name}")
        if secondary.upper().replace(" ", "") != row["type"].upper().replace(" ", ""):
            raise SecContentError(
                f"EXHIBIT_IDENTITY_CONFLICT:{name}:{row['type']}:{secondary}"
            )
    return selected


def validate_primary_document(
    *,
    primary_name: str,
    filing_form: str,
    sgml_documents: list[dict],
    index_types: dict[str, str],
) -> dict:
    if not sgml_documents or not index_types:
        raise SecContentError("DOCUMENT_IDENTITY_SCHEMA_EMPTY")
    matches = [row for row in sgml_documents if row["filename"] == primary_name]
    if len(matches) != 1:
        raise SecContentError(
            f"PRIMARY_SGML_IDENTITY_CARDINALITY:{primary_name}:{len(matches)}"
        )
    primary = matches[0]
    expected = (filing_form or "").strip().upper()
    if primary["type"].strip().upper() != expected:
        raise SecContentError(
            f"PRIMARY_FORM_CONFLICT:{primary_name}:{primary['type']}:{expected}"
        )
    secondary = index_types.get(primary_name)
    if secondary is None:
        raise SecContentError(f"PRIMARY_SECONDARY_IDENTITY_MISSING:{primary_name}")
    if secondary.upper().replace(" ", "") != expected.replace(" ", ""):
        raise SecContentError(
            f"PRIMARY_SECONDARY_FORM_CONFLICT:{primary_name}:{secondary}:{expected}"
        )
    return primary


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalized_visible_text(source: bytes) -> str:
    parser = _VisibleText()
    parser.feed(source.decode("utf-8", errors="replace"))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _quote_for_match(text: str, start: int, end: int) -> tuple[str, int]:
    left = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start))
    left = 0 if left < 0 else left + 2
    stops = [p for p in (text.find(". ", end), text.find("; ", end)) if p >= 0]
    right = min(stops) + 1 if stops else len(text)
    quote = text[left:right].strip()
    quote_start = text.find(quote, left, right + 1)
    if not quote or quote_start < 0:
        raise SecContentError("QUOTE_BOUNDARY_FAILED")
    return quote, quote_start


def _exact_evidence(
    text: str,
    *,
    label: str,
    pattern: str,
    currency: str,
    unit: str,
) -> dict:
    matches = list(re.finditer(pattern, text, re.I))
    if len(matches) != 1:
        raise SecContentError(f"EXTRACTION_CARDINALITY:{label}:{len(matches)}")
    match = matches[0]
    quote, char_offset = _quote_for_match(text, match.start(), match.end())
    return {
        "label": label,
        "value": match.group("value").replace(",", ""),
        "raw_value": match.group("raw"),
        "unit": unit,
        "currency": currency,
        "quote": quote,
        "char_offset": char_offset,
        "match_offset": match.start(),
        "offset_basis": "normalized_visible_text",
    }


def extract_registered_evidence(
    *, ticker: str, accession: str, primary_source: bytes
) -> tuple[list[dict], list[str]]:
    """Run only an explicitly registered extractor; never guess from document text."""
    if (ticker.upper(), accession) != ("TSM", "0001046179-26-000536"):
        return [], ["EXTRACTOR_NOT_REGISTERED"]
    text = normalized_visible_text(primary_source)
    specs = (
        {
            "label": "capital_appropriations",
            "pattern": r"(?P<raw>US\$\s*(?P<value>29,442\.50)\s+million)",
            "currency": "USD",
            "unit": "million",
        },
        {
            "label": "sony_jv_subscription_cap",
            "pattern": r"(?P<raw>not more than\s+(?P<value>282)\s+billion Japanese yen)",
            "currency": "JPY",
            "unit": "billion",
        },
        {
            "label": "cash_dividend_per_share",
            "pattern": r"(?P<raw>NT\$\s*(?P<value>7\.0)\s+per share)",
            "currency": "TWD",
            "unit": "per_share",
        },
    )
    return [_exact_evidence(text, **spec) for spec in specs], []


def _validate_sec_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SEC_HOSTS:
        raise SecContentError(f"SOURCE_URI_NOT_SEC_ARCHIVES:{url}")
    if not parsed.path.startswith("/Archives/edgar/data/"):
        raise SecContentError(f"SOURCE_URI_NOT_SEC_ARCHIVES:{url}")


def _document_name(url: str) -> str:
    name = os.path.basename(urlparse(url).path)
    if not SAFE_DOCUMENT_RE.fullmatch(name):
        raise SecContentError(f"DOCUMENT_NAME_INVALID:{name!r}")
    return name


def source_urls(cik: str, filing: dict) -> dict:
    if not re.fullmatch(r"\d{10}", str(cik)):
        raise SecContentError(f"CIK_INVALID:{cik!r}")
    cik_digits = str(int(cik))
    accession = filing.get("accession", "")
    if not ACCESSION_RE.fullmatch(accession):
        raise SecContentError(f"ACCESSION_INVALID:{accession!r}")
    accession_compact = accession.replace("-", "")
    primary = filing.get("url", "")
    index = filing.get("index_url", "")
    _validate_sec_url(primary)
    _validate_sec_url(index)
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_digits}/{accession_compact}"
    expected_prefix = f"/Archives/edgar/data/{cik_digits}/{accession_compact}/"
    if not urlparse(primary).path.startswith(expected_prefix):
        raise SecContentError("PRIMARY_IDENTITY_PATH_MISMATCH")
    if not urlparse(index).path.startswith(expected_prefix):
        raise SecContentError("INDEX_IDENTITY_PATH_MISMATCH")
    if _document_name(index) not in {
        f"{accession}-index.htm",
        f"{accession}-index.html",
    }:
        raise SecContentError("INDEX_DOCUMENT_NAME_MISMATCH")
    return {
        "base": base,
        "primary": primary,
        "index": index,
        # SEC archive directory drops hyphens, but the full-submission filename keeps them.
        "submission": f"{base}/{accession}.txt",
    }


def default_fetcher(user_agent: str):
    if not user_agent or "@" not in user_agent:
        raise SecContentError("SEC_USER_AGENT_MISSING_CONTACT")

    def fetch(url: str) -> bytes:
        _validate_sec_url(url)
        time.sleep(POLITE_DELAY_SECONDS)
        request = Request(
            url,
            headers={"User-Agent": user_agent, "Accept-Encoding": "identity"},
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - URL allowlisted above
            final_url = response.geturl()
            _validate_sec_url(final_url)
            if final_url != url:
                raise SecContentError(f"SOURCE_REDIRECT_IDENTITY_CHANGED:{url}:{final_url}")
            return response.read(MAX_HTTP_RESPONSE_BYTES)

    return fetch


def _document_record(kind: str, source_uri: str, raw: bytes) -> dict:
    return {
        "kind": kind,
        "source_uri": source_uri,
        "document_name": _document_name(source_uri),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "content_bytes": len(raw),
    }


def capture_filing(
    *,
    ticker: str,
    cik: str,
    stage: str | None,
    filing: dict,
    fetcher,
    retrieved_at_utc: str,
    contract: dict,
    existing_manifest: dict | None = None,
    force_refresh: bool = False,
) -> tuple[dict, dict[str, bytes]]:
    """Capture one filing.  Returned raw bytes are not written by this pure boundary."""
    plan = filing_plan(filing, stage, contract)
    accession = filing.get("accession", "")
    identity = {"cik": cik, "accession": accession}
    result = {
        "schema_version": SCHEMA_VERSION,
        "ticker": ticker,
        "atlas_stage": stage,
        "form": filing.get("form"),
        "filing_date": filing.get("date"),
        "filing_identity": identity,
        "extractor_version": contract["extractor_version"],
        **plan,
        "documents": [],
        "extracted": [],
    }
    if plan["capture_policy"] == "index_only":
        return result, {}

    if existing_manifest and not force_refresh:
        same_identity = existing_manifest.get("filing_identity") == identity
        same_extractor = existing_manifest.get("extractor_version") == contract["extractor_version"]
        if same_identity and same_extractor and existing_manifest.get("content_status") == "OK":
            skipped = copy.deepcopy(existing_manifest)
            skipped["atlas_stage"] = stage
            skipped["raw_cache_policy"] = (
                "permanent"
                if stage in contract["retention_policy"]["permanent_raw_stages"]
                else f"delete_after_{contract['retention_policy']['raw_cache_days']}_days_allowed"
            )
            skipped["operation"] = "skipped"
            skipped["skip_reason"] = "already_captured"
            return skipped, {}

    try:
        urls = source_urls(cik, filing)
        submission = fetcher(urls["submission"])
        index = fetcher(urls["index"])
        max_index = contract["document_policy"]["max_submission_index_bytes"]
        if len(submission) > max_index or len(index) > max_index:
            raise SecContentError("SUBMISSION_INDEX_OVERSIZE")
        identity_evidence = {
            "full_submission": _document_record("identity", urls["submission"], submission),
            "filing_index": _document_record("identity", urls["index"], index),
        }
        sgml_documents = parse_sgml_documents(submission)
        index_types = parse_index_types(index)
        primary_name = _document_name(urls["primary"])
        validate_primary_document(
            primary_name=primary_name,
            filing_form=filing.get("form", ""),
            sgml_documents=sgml_documents,
            index_types=index_types,
        )
        exhibits = select_ex99_documents(sgml_documents, index_types, contract)
        sources = [("primary", urls["primary"])] + [
            ("exhibit", f"{urls['base']}/{row['filename']}") for row in exhibits
        ]
        raw_by_name: dict[str, bytes] = {}
        documents = []
        for kind, url in sources:
            raw = fetcher(url)
            limit = contract["document_policy"]["max_document_bytes"]
            if len(raw) > limit:
                raise SecContentError(
                    f"DOCUMENT_OVERSIZE:{_document_name(url)}:{len(raw)}>{limit}"
                )
            record = _document_record(kind, url, raw)
            if record["document_name"] in raw_by_name:
                raise SecContentError(
                    f"DOCUMENT_IDENTITY_DUPLICATE:{record['document_name']}"
                )
            raw_by_name[record["document_name"]] = raw
            documents.append(record)

        if existing_manifest:
            old = {
                (d["document_name"], d["source_uri"]): d["content_sha256"]
                for d in existing_manifest.get("documents", [])
            }
            for document in documents:
                key = (document["document_name"], document["source_uri"])
                if key in old and old[key] != document["content_sha256"]:
                    raise SecContentError(f"SOURCE_MUTATED:{document['document_name']}")
            old_identity = existing_manifest.get("identity_evidence") or {}
            for key, record in identity_evidence.items():
                old_record = old_identity.get(key)
                if old_record and old_record.get("content_sha256") != record["content_sha256"]:
                    raise SecContentError(f"SOURCE_MUTATED:{record['document_name']}")

        try:
            extracted, extraction_reasons = extract_registered_evidence(
                ticker=ticker,
                accession=accession,
                primary_source=raw_by_name[primary_name],
            )
            evidence_status = "OK" if extracted and not extraction_reasons else "PENDING"
        except SecContentError as exc:
            # Content acquisition succeeded. Extraction failure is a separate axis and
            # must never erase that fact or discard the immutable raw evidence.
            extracted, extraction_reasons = [], [str(exc)]
            evidence_status = "FAILED"
        result.update(
            content_status="OK",
            evidence_status=evidence_status,
            reasons=extraction_reasons,
            retrieved_at_utc=retrieved_at_utc,
            documents=documents,
            identity_evidence=identity_evidence,
            extracted=extracted,
            canonical_identity="hash+url+extracted",
            raw_cache_policy=(
                "permanent"
                if stage in contract["retention_policy"]["permanent_raw_stages"]
                else f"delete_after_{contract['retention_policy']['raw_cache_days']}_days_allowed"
            ),
            operation="captured",
        )
        return result, raw_by_name
    except Exception as exc:  # fail-closed record; caller decides process exit
        reason = str(exc) if isinstance(exc, SecContentError) else f"{type(exc).__name__}:{exc}"
        result.update(
            content_status="PENDING",
            evidence_status="PENDING",
            reasons=[reason],
            operation="failed",
        )
        return result, {}


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _gzip_bytes(raw: bytes) -> bytes:
    return gzip.compress(raw, compresslevel=9, mtime=0)


def manifest_dir(data_root: Path, ticker: str, accession: str) -> Path:
    if not re.fullmatch(r"[A-Z0-9.-]+", ticker) or not ACCESSION_RE.fullmatch(accession):
        raise SecContentError("OUTPUT_IDENTITY_INVALID")
    return data_root / "sec_content" / ticker / accession


def load_existing_manifest(data_root: Path, ticker: str, accession: str) -> dict | None:
    path = manifest_dir(data_root, ticker, accession) / "_manifest.json"
    return _read_json(path) if path.exists() else None


def _manifest_fingerprint(manifest: dict) -> dict:
    documents = {
        (row["document_name"], row["source_uri"]): row["content_sha256"]
        for row in manifest.get("documents", [])
    }
    identity = {
        key: (
            row.get("document_name"),
            row.get("source_uri"),
            row.get("content_sha256"),
        )
        for key, row in (manifest.get("identity_evidence") or {}).items()
    }
    return {"documents": documents, "identity_evidence": identity}


def _validate_raw_payload(manifest: dict, raw_by_name: dict[str, bytes]) -> None:
    expected = {
        row["document_name"]: row["content_sha256"]
        for row in manifest.get("documents", [])
    }
    if raw_by_name and set(raw_by_name) != set(expected):
        raise SecContentError("RAW_CACHE_SET_MISMATCH")
    for name, raw in raw_by_name.items():
        if hashlib.sha256(raw).hexdigest() != expected[name]:
            raise SecContentError(f"RAW_CACHE_HASH_MISMATCH:{name}")


def persist_success(
    data_root: Path, manifest: dict, raw_by_name: dict[str, bytes]
) -> None:
    if manifest.get("content_status") != "OK":
        raise SecContentError("PERSIST_NON_OK_FORBIDDEN")
    _validate_raw_payload(manifest, raw_by_name)
    directory = manifest_dir(
        data_root, manifest["ticker"], manifest["filing_identity"]["accession"]
    )
    manifest_path = directory / "_manifest.json"
    if directory.exists():
        if not manifest_path.exists():
            raise SecContentError("PARTIAL_CACHE_WITHOUT_MANIFEST")
        old = _read_json(manifest_path)
        if _manifest_fingerprint(old) != _manifest_fingerprint(manifest):
            raise SecContentError("SOURCE_MUTATED_FAIL_CLOSED_NO_OVERWRITE")
        for document in manifest["documents"]:
            name = document["document_name"]
            target = directory / f"{name}.gz"
            if not target.exists():
                raise SecContentError(f"RAW_CACHE_MISSING:{name}")
            try:
                cached = gzip.decompress(target.read_bytes())
            except (OSError, EOFError) as exc:
                raise SecContentError(f"RAW_CACHE_INVALID:{name}") from exc
            if hashlib.sha256(cached).hexdigest() != document["content_sha256"]:
                raise SecContentError(f"RAW_CACHE_MUTATION:{name}")
        _atomic_write(manifest_path, _json_bytes(manifest))
        return

    if not raw_by_name:
        raise SecContentError("RAW_CACHE_REQUIRED_FOR_NEW_CAPTURE")
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{directory.name}.tmp.", dir=directory.parent)
    )
    try:
        for name, raw in raw_by_name.items():
            if not SAFE_DOCUMENT_RE.fullmatch(name):
                raise SecContentError(f"DOCUMENT_NAME_INVALID:{name!r}")
            _atomic_write(staging / f"{name}.gz", _gzip_bytes(raw))
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
    except ValueError as exc:
        raise SecContentError(f"EXPECTED_DATE_INVALID:{expected_kst_date}") from exc
    try:
        dt.datetime.strptime(retrieved_at_utc, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise SecContentError(f"OBSERVED_AT_INVALID:{retrieved_at_utc}") from exc
    source = _read_json(source_path)
    if source.get("collected_for_kst_date") != expected_kst_date:
        raise SecContentError(
            f"SOURCE_DATE_MISMATCH:{source.get('collected_for_kst_date')}:{expected_kst_date}"
        )
    stocks = source.get("stocks")
    if not isinstance(stocks, dict):
        raise SecContentError("SOURCE_STOCKS_MISSING")

    records = []
    counts = {"captured": 0, "skipped": 0, "failed": 0, "not_applicable": 0}
    for ticker, stock in sorted(stocks.items()):
        if not isinstance(stock, dict) or stock.get("status") != "ok":
            continue
        cik = stock.get("cik")
        stage = stock.get("atlas_stage")
        filings = stock.get("filings_recent") or []
        for filing in filings:
            if not isinstance(filing, dict):
                continue
            plan = filing_plan(filing, stage, contract)
            if plan["form_classification"] != "MATERIAL":
                continue
            accession = filing.get("accession", "")
            record = None
            try:
                existing = load_existing_manifest(data_root, ticker, accession)
                record, raw = capture_filing(
                    ticker=ticker,
                    cik=cik,
                    stage=stage,
                    filing=filing,
                    fetcher=fetcher,
                    retrieved_at_utc=retrieved_at_utc,
                    contract=contract,
                    existing_manifest=existing,
                )
                operation = record.get("operation")
                if record["content_status"] == "NOT_APPLICABLE":
                    record["publication_status"] = "NOT_APPLICABLE"
                    counts["not_applicable"] += 1
                elif operation == "captured":
                    record["publication_status"] = "OK"
                    persist_success(data_root, record, raw)
                    counts["captured"] += 1
                elif operation == "skipped":
                    # Skip is itself an auditable operation. It also persists current
                    # Stage and retention policy without another SEC request.
                    record["publication_status"] = "OK"
                    persist_success(data_root, record, {})
                    counts["skipped"] += 1
                else:
                    record["publication_status"] = "NOT_PUBLISHED"
                    counts["failed"] += 1
            except Exception as exc:
                if record is None:
                    record = {
                        "schema_version": SCHEMA_VERSION,
                        "ticker": ticker,
                        "atlas_stage": stage,
                        "form": filing.get("form"),
                        "filing_date": filing.get("date"),
                        "filing_identity": {"cik": cik, "accession": accession},
                        "extractor_version": contract["extractor_version"],
                        **plan,
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
    _atomic_write(data_root / "latest_sec_content.json", _json_bytes(run))
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
    _atomic_write(data_root / "latest_sec_content.json", _json_bytes(run))
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
        fetcher = default_fetcher(os.getenv("SEC_USER_AGENT", "").strip())
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
    sys.exit(main())
