#!/usr/bin/env python3
"""Capture immutable, first-party identity evidence for frozen PAPER assets.

The collector is deliberately bounded by the P3-12 governance-freeze file.
It reads public web documentation only, uses no credential, and grants no
identity, PAPER, exchange, order, Production, REAL, or Trading authority.
Captured bytes are evidence for a later reviewed registry proposal; they are
never consumed directly as trading authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Callable, NamedTuple, Optional
import urllib.error
import urllib.request
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "upbit_first_party_identity_capture_contract.json"
FREEZE_PATH = ROOT / "config" / "upbit_identity_taxonomy_governance_freeze.json"
UTC = dt.timezone.utc
USER_AGENT = "Project-Atlas-first-party-identity-evidence/1.0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CaptureError(RuntimeError):
    """Fail-closed capture or publication error."""


class FetchResult(NamedTuple):
    raw: bytes
    effective_url: str
    http_status: int
    content_type: str


def fail(code: str, detail: str) -> None:
    raise CaptureError(f"{code}:{detail}")


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail("TIMESTAMP_INVALID", f"{label}:{value!r}")
    try:
        parsed = dt.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        fail("TIMESTAMP_INVALID", f"{label}:{value!r}:{exc}")
    if parsed.tzinfo != UTC:
        fail("TIMESTAMP_INVALID", f"{label}:{value!r}")
    return parsed


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _host_matches(hostname: str, allowed_domain: str) -> bool:
    host = hostname.lower().rstrip(".")
    allowed = allowed_domain.lower().rstrip(".")
    return host == allowed or host.endswith(f".{allowed}")


def _safe_raw_file(value: object) -> str:
    if not isinstance(value, str):
        fail("CONTRACT_RAW_FILE_INVALID", repr(value))
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts or parsed.suffix != ".gz":
        fail("CONTRACT_RAW_FILE_INVALID", value)
    return value


def load_contract(path: Path = CONTRACT_PATH, freeze_path: Path = FREEZE_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
        freeze = json.loads(Path(freeze_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_READ_FAILED", str(exc))
    if contract.get("schema_version") != "upbit_first_party_identity_capture_contract/1":
        fail("CONTRACT_SCHEMA_INVALID", repr(contract.get("schema_version")))
    if contract.get("review_status") != "PROPOSED_EVIDENCE_ONLY_AUTHORITY_FALSE":
        fail("CONTRACT_REVIEW_STATUS_INVALID", repr(contract.get("review_status")))
    if contract.get("auth_required") is not False or contract.get("order_or_withdrawal_endpoints_called") is not False:
        fail("CONTRACT_SAFETY_INVARIANT_VIOLATED", "public read-only capture required")
    if contract.get("source_published_at_claimed") is not False:
        fail("CONTRACT_TIME_CLAIM_INVALID", "capture time is not source publication time")
    authority = contract.get("authority")
    if not isinstance(authority, dict) or not authority or any(value is not False for value in authority.values()):
        fail("CONTRACT_AUTHORITY_INVALID", repr(authority))
    max_bytes = contract.get("max_response_bytes")
    timeout = contract.get("timeout_seconds")
    if not isinstance(max_bytes, int) or not 1024 <= max_bytes <= 5_000_000:
        fail("CONTRACT_MAX_BYTES_INVALID", repr(max_bytes))
    if not isinstance(timeout, int) or not 1 <= timeout <= 120:
        fail("CONTRACT_TIMEOUT_INVALID", repr(timeout))

    assets = contract.get("assets")
    if not isinstance(assets, list) or not assets:
        fail("CONTRACT_ASSETS_INVALID", repr(assets))
    markets, canonical_ids, raw_files = [], [], []
    for row in assets:
        required = {
            "market", "canonical_asset_id", "source_type", "url",
            "validated_authority_domain", "allowed_redirect_domains",
            "raw_file", "required_markers",
        }
        if not isinstance(row, dict) or not required <= set(row):
            fail("CONTRACT_ASSET_FIELDS_INVALID", repr(row))
        market = row["market"]
        canonical_id = row["canonical_asset_id"]
        if not isinstance(market, str) or not market.startswith("KRW-"):
            fail("CONTRACT_MARKET_INVALID", repr(market))
        if not isinstance(canonical_id, str) or market != f"KRW-{canonical_id}":
            fail("CONTRACT_CANONICAL_ID_INVALID", f"{market}:{canonical_id}")
        if row["source_type"] not in {
            "PROJECT_FIRST_PARTY_PUBLIC_WEB",
            "PROJECT_FIRST_PARTY_PUBLIC_DOCUMENTATION",
        }:
            fail("CONTRACT_SOURCE_TYPE_INVALID", repr(row["source_type"]))
        parsed = urlparse(row["url"])
        authority_domain = row["validated_authority_domain"]
        redirect_domains = row["allowed_redirect_domains"]
        if parsed.scheme != "https" or not parsed.hostname:
            fail("CONTRACT_SOURCE_URL_INVALID", repr(row["url"]))
        if not isinstance(authority_domain, str) or not _host_matches(parsed.hostname, authority_domain):
            fail("CONTRACT_AUTHORITY_DOMAIN_INVALID", repr(row))
        if not isinstance(redirect_domains, list) or not redirect_domains or not all(
            isinstance(domain, str) and domain for domain in redirect_domains
        ):
            fail("CONTRACT_REDIRECT_DOMAINS_INVALID", repr(row))
        if not any(_host_matches(parsed.hostname, domain) for domain in redirect_domains):
            fail("CONTRACT_SOURCE_DOMAIN_NOT_ALLOWLISTED", row["url"])
        markers = row["required_markers"]
        if not isinstance(markers, list) or len(markers) < 2 or not all(
            isinstance(marker, str) and len(marker) >= 3 for marker in markers
        ):
            fail("CONTRACT_MARKERS_INVALID", repr(markers))
        markets.append(market)
        canonical_ids.append(canonical_id)
        raw_files.append(_safe_raw_file(row["raw_file"]))
    if len(markets) != len(set(markets)) or len(canonical_ids) != len(set(canonical_ids)) or len(raw_files) != len(set(raw_files)):
        fail("CONTRACT_ASSET_DUPLICATE", "market/canonical id/raw path must be unique")
    if sorted(markets) != sorted(freeze.get("blocked_paper_markets") or []):
        fail("CONTRACT_FREEZE_SCOPE_MISMATCH", repr(markets))
    return contract


def public_get(url: str, timeout_seconds: int, max_response_bytes: int) -> FetchResult:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            raw = response.read(max_response_bytes + 1)
            effective_url = response.geturl()
            content_type = response.headers.get_content_type()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail("FETCH_FAILED", f"{url}:{exc}")
    if status != 200:
        fail("SOURCE_HTTP_STATUS_INVALID", f"{url}:{status}")
    if not raw:
        fail("SOURCE_EMPTY", url)
    if len(raw) > max_response_bytes:
        fail("SOURCE_TOO_LARGE", f"{url}:{len(raw)}")
    return FetchResult(raw, effective_url, status, content_type)


def _validate_fetch(source: dict, result: FetchResult) -> None:
    parsed = urlparse(result.effective_url)
    if parsed.scheme != "https" or not parsed.hostname:
        fail("EFFECTIVE_URL_INVALID", result.effective_url)
    if not any(_host_matches(parsed.hostname, domain) for domain in source["allowed_redirect_domains"]):
        fail("REDIRECT_AUTHORITY_REJECTED", result.effective_url)
    lowered = result.raw.lower()
    missing = [marker for marker in source["required_markers"] if marker.encode("utf-8").lower() not in lowered]
    if missing:
        fail("IDENTITY_MARKER_MISSING", f"{source['market']}:{missing}")


def _write_gzip(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
            stream.write(raw)


def capture_snapshot(
    snapshot_root: Path,
    *,
    capture_id: Optional[str] = None,
    contract_path: Path = CONTRACT_PATH,
    freeze_path: Path = FREEZE_PATH,
    fetcher: Optional[Callable[[str, int, int], FetchResult]] = None,
    clock: Callable[[], dt.datetime] = utc_now,
) -> Path:
    contract = load_contract(contract_path, freeze_path)
    observed_at = clock().astimezone(UTC)
    capture_id = capture_id or observed_at.strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", capture_id):
        fail("CAPTURE_ID_INVALID", capture_id)
    target = Path(snapshot_root) / observed_at.date().isoformat() / capture_id
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = Path(tempfile.mkdtemp(prefix="upbit-first-party-", dir=str(target.parent)))
    staging = temporary_parent / capture_id
    staging.mkdir()
    fetch = fetcher or public_get
    try:
        sources = []
        for source in contract["assets"]:
            source_observed_at = clock().astimezone(UTC)
            result = fetch(source["url"], contract["timeout_seconds"], contract["max_response_bytes"])
            source_available_at = clock().astimezone(UTC)
            if source_available_at < source_observed_at:
                fail("CAPTURE_CLOCK_REVERSED", source["market"])
            _validate_fetch(source, result)
            _write_gzip(staging / source["raw_file"], result.raw)
            sources.append({
                "market": source["market"],
                "canonical_asset_id": source["canonical_asset_id"],
                "source_type": source["source_type"],
                "source_url": source["url"],
                "effective_url": result.effective_url,
                "validated_authority_domain": source["validated_authority_domain"],
                "raw_file": source["raw_file"],
                "content_sha256": hashlib.sha256(result.raw).hexdigest(),
                "observed_at": iso_utc(source_observed_at),
                "available_at": iso_utc(source_available_at),
                "source_published_at": None,
                "atlas_capture_time_is_source_published_at": False,
                "http_status": result.http_status,
                "content_type": result.content_type,
                "required_markers": source["required_markers"],
            })
        manifest = {
            "schema_version": "upbit_first_party_identity_capture/1",
            "capture_version": contract["capture_version"],
            "review_status": contract["review_status"],
            "capture_id": capture_id,
            "observed_at": iso_utc(observed_at),
            "available_at": iso_utc(clock().astimezone(UTC)),
            "contract_path": str(Path(contract_path).resolve().relative_to(ROOT)),
            "contract_sha256": file_sha256(contract_path),
            "governance_freeze_path": str(Path(freeze_path).resolve().relative_to(ROOT)),
            "governance_freeze_sha256": file_sha256(freeze_path),
            "asset_count": len(sources),
            "sources": sources,
            "auth_required": False,
            "order_or_withdrawal_endpoints_called": False,
            "authority": contract["authority"],
        }
        (staging / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        validate_snapshot(staging, contract_path=contract_path, freeze_path=freeze_path)
        staging.replace(target)
        shutil.rmtree(temporary_parent, ignore_errors=True)
        return target
    except Exception:
        shutil.rmtree(temporary_parent, ignore_errors=True)
        raise


def validate_snapshot(
    snapshot_dir: Path,
    *,
    contract_path: Path = CONTRACT_PATH,
    freeze_path: Path = FREEZE_PATH,
) -> dict:
    contract = load_contract(contract_path, freeze_path)
    manifest_path = Path(snapshot_dir) / "_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("MANIFEST_READ_FAILED", str(exc))
    if manifest.get("schema_version") != "upbit_first_party_identity_capture/1":
        fail("MANIFEST_SCHEMA_INVALID", repr(manifest.get("schema_version")))
    if manifest.get("contract_sha256") != file_sha256(contract_path):
        fail("MANIFEST_CONTRACT_HASH_MISMATCH", str(snapshot_dir))
    if manifest.get("governance_freeze_sha256") != file_sha256(freeze_path):
        fail("MANIFEST_FREEZE_HASH_MISMATCH", str(snapshot_dir))
    if manifest.get("auth_required") is not False or manifest.get("order_or_withdrawal_endpoints_called") is not False:
        fail("MANIFEST_SAFETY_INVARIANT_VIOLATED", str(snapshot_dir))
    authority = manifest.get("authority")
    if authority != contract["authority"] or any(value is not False for value in authority.values()):
        fail("MANIFEST_AUTHORITY_INVALID", str(snapshot_dir))
    sources = manifest.get("sources")
    if not isinstance(sources, list) or manifest.get("asset_count") != len(sources):
        fail("MANIFEST_SOURCE_COUNT_INVALID", str(snapshot_dir))
    contract_by_market = {row["market"]: row for row in contract["assets"]}
    if {row.get("market") for row in sources} != set(contract_by_market):
        fail("MANIFEST_MARKET_SCOPE_INVALID", str(snapshot_dir))
    seen_raw_files = set()
    manifest_observed_at = parse_utc(manifest.get("observed_at"), "manifest.observed_at")
    manifest_available_at = parse_utc(manifest.get("available_at"), "manifest.available_at")
    if manifest_available_at < manifest_observed_at:
        fail("MANIFEST_TIME_ORDER_INVALID", str(snapshot_dir))
    for row in sources:
        expected = contract_by_market[row["market"]]
        for key in (
            "canonical_asset_id", "source_type", "validated_authority_domain",
            "raw_file", "required_markers",
        ):
            if row.get(key) != expected[key]:
                fail("MANIFEST_SOURCE_BINDING_INVALID", f"{row['market']}:{key}")
        if row.get("source_url") != expected["url"]:
            fail("MANIFEST_SOURCE_BINDING_INVALID", f"{row['market']}:source_url")
        if row.get("source_published_at") is not None or row.get("atlas_capture_time_is_source_published_at") is not False:
            fail("MANIFEST_TIME_CLAIM_INVALID", row["market"])
        source_observed_at = parse_utc(row.get("observed_at"), f"{row['market']}.observed_at")
        source_available_at = parse_utc(row.get("available_at"), f"{row['market']}.available_at")
        if not manifest_observed_at <= source_observed_at <= source_available_at <= manifest_available_at:
            fail("MANIFEST_TIME_ORDER_INVALID", row["market"])
        if row.get("http_status") != 200:
            fail("MANIFEST_HTTP_STATUS_INVALID", row["market"])
        if not isinstance(row.get("content_sha256"), str) or not SHA256_RE.fullmatch(row["content_sha256"]):
            fail("MANIFEST_CONTENT_HASH_INVALID", row["market"])
        effective_host = urlparse(row.get("effective_url") or "").hostname or ""
        if not any(_host_matches(effective_host, domain) for domain in expected["allowed_redirect_domains"]):
            fail("MANIFEST_EFFECTIVE_AUTHORITY_INVALID", row["market"])
        raw_file = _safe_raw_file(row["raw_file"])
        if raw_file in seen_raw_files:
            fail("MANIFEST_RAW_FILE_DUPLICATE", raw_file)
        seen_raw_files.add(raw_file)
        try:
            with gzip.open(Path(snapshot_dir) / raw_file, "rb") as handle:
                raw = handle.read(contract["max_response_bytes"] + 1)
        except (OSError, gzip.BadGzipFile) as exc:
            fail("RAW_FILE_READ_FAILED", f"{raw_file}:{exc}")
        if len(raw) > contract["max_response_bytes"]:
            fail("RAW_FILE_TOO_LARGE", raw_file)
        if hashlib.sha256(raw).hexdigest() != row["content_sha256"]:
            fail("RAW_FILE_HASH_MISMATCH", raw_file)
        _validate_fetch(expected, FetchResult(raw, row["effective_url"], row["http_status"], row.get("content_type") or ""))
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--capture-id")
    args = parser.parse_args(argv)
    target = capture_snapshot(args.snapshot_root, capture_id=args.capture_id)
    manifest = validate_snapshot(target)
    print(json.dumps({
        "path": str(target),
        "asset_count": manifest["asset_count"],
        "review_status": manifest["review_status"],
        "authority": manifest["authority"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CaptureError as exc:
        print(f"FATAL:{exc}", file=sys.stderr)
        sys.exit(1)
