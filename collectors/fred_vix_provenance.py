#!/usr/bin/env python3
"""Append-only, independently replayable FRED VIX raw evidence.

This module stores public FRED response bytes only.  It does not retain an API
key, account data, broker data, a policy classification, or trading authority.
The evidence revision is content-and-capture addressed so repeated workflow
runs cannot overwrite a prior observation.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import re


UTC = dt.timezone.utc
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SCHEMA_VERSION = "fred_vix_raw_evidence/1"
TRANSFORM_VERSION = "fred_vix_observation/1"
RAW_RETENTION = "APPEND_ONLY_CONTENT_ADDRESSED"
SOURCE_REQUEST = {
    "endpoint": "https://api.stlouisfed.org/fred/series/observations",
    "file_type": "json",
    "output_type": 1,
    "series_id": "VIXCLS",
}
AUTHORITY = {
    "evidence_capture_only": True,
    "regime_interpretation_authorized": False,
    "direction_authorized": False,
    "confidence_authorized": False,
    "threshold_authorized": False,
    "market_ranking_authorized": False,
    "action_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class FredVixEvidenceError(ValueError):
    """The retained response cannot prove the declared VIX observation."""


def fail(code: str) -> None:
    raise FredVixEvidenceError(code)


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
    except ValueError:
        fail(code)


def _parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code)
    if parsed.isoformat() != value:
        fail(code)
    return parsed


def derive_observation(raw: bytes) -> dict:
    """Re-derive the latest finalized VIX observation from exact raw bytes."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("FRED_RAW_JSON_INVALID")
    if not isinstance(payload, dict):
        fail("FRED_RAW_ROOT_INVALID")
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        fail("FRED_OBSERVATIONS_MISSING")
    valid: list[dict] = []
    prior_date: dt.date | None = None
    for row in observations:
        if not isinstance(row, dict):
            fail("FRED_OBSERVATION_INVALID")
        row_date = _parse_date(row.get("date"), "FRED_OBSERVATION_DATE_INVALID")
        if prior_date is not None and row_date < prior_date:
            fail("FRED_OBSERVATIONS_NOT_ASCENDING")
        prior_date = row_date
        value = row.get("value")
        if value in (None, "."):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            fail("FRED_OBSERVATION_VALUE_INVALID")
        if not math.isfinite(numeric) or numeric < 0:
            fail("FRED_OBSERVATION_VALUE_INVALID")
        valid.append({
            "observation_date": row_date.isoformat(),
            "value": str(value),
            "realtime_start": row.get("realtime_start"),
            "realtime_end": row.get("realtime_end"),
        })
    if not valid:
        fail("FRED_VALUES_MISSING")
    return {"series_id": "VIXCLS", **valid[-1]}


def _revision_basis(captured_at_utc: str, raw_sha256: str, observation: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "transform_version": TRANSFORM_VERSION,
        "captured_at_utc": captured_at_utc,
        "source_request": SOURCE_REQUEST,
        "raw_response_sha256": raw_sha256,
        "observation": observation,
    }


def build_evidence_bundle(captured_at: dt.datetime, raw: bytes) -> dict:
    if captured_at.tzinfo is None:
        fail("CAPTURE_TIME_NAIVE")
    captured_at_utc = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    captured = _parse_utc(captured_at_utc, "CAPTURE_TIME_INVALID")
    observation = derive_observation(raw)
    if _parse_date(
        observation["observation_date"], "FRED_OBSERVATION_DATE_INVALID"
    ) > captured.date():
        fail("FRED_OBSERVATION_FROM_FUTURE")
    raw_sha256 = sha256_bytes(raw)
    revision_id = sha256_bytes(
        canonical_bytes(_revision_basis(captured_at_utc, raw_sha256, observation))
    )
    day = captured.date().isoformat()
    base = f"evidence/free_market_data/fred/raw/{day}/{revision_id}"
    manifest = {
        **_revision_basis(captured_at_utc, raw_sha256, observation),
        "revision_id": revision_id,
        "raw_retention": RAW_RETENTION,
        "authority": AUTHORITY,
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    raw_gzip_bytes = gzip.compress(raw, mtime=0)
    pointer = {
        "evidence_revision_id": revision_id,
        "manifest_path": f"{base}/manifest.json",
        "manifest_file_sha256": sha256_bytes(manifest_bytes),
        "raw_path": f"{base}/fred_vixcls.json.gz",
        "raw_file_sha256": sha256_bytes(raw_gzip_bytes),
        "raw_response_sha256": raw_sha256,
    }
    return {
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "raw_gzip_bytes": raw_gzip_bytes,
        "pointer": pointer,
    }


def _write_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            fail("APPEND_ONLY_COLLISION")
        return
    path.write_bytes(data)


def publish_evidence_bundle(root: Path, bundle: dict) -> None:
    if not isinstance(bundle, dict):
        fail("EVIDENCE_BUNDLE_INVALID")
    try:
        raw = gzip.decompress(bundle["raw_gzip_bytes"])
        captured = _parse_utc(
            bundle["manifest"].get("captured_at_utc"), "CAPTURE_TIME_INVALID"
        )
    except (KeyError, TypeError, OSError, EOFError):
        fail("EVIDENCE_BUNDLE_INVALID")
    if bundle != build_evidence_bundle(captured, raw):
        fail("EVIDENCE_BUNDLE_TAMPERED")
    pointer = bundle["pointer"]
    manifest_path = _safe_evidence_path(
        root, pointer["manifest_path"], "/manifest.json"
    )
    raw_path = _safe_evidence_path(
        root, pointer["raw_path"], "/fred_vixcls.json.gz"
    )
    _write_once(raw_path, bundle["raw_gzip_bytes"])
    _write_once(manifest_path, bundle["manifest_bytes"])


def _safe_evidence_path(root: Path, value: object, suffix: str) -> Path:
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        fail("EVIDENCE_PATH_INVALID")
    if not value.startswith("evidence/free_market_data/fred/raw/") or not value.endswith(suffix):
        fail("EVIDENCE_PATH_INVALID")
    resolved_root = root.resolve()
    resolved = (root / value).resolve()
    if resolved_root not in resolved.parents:
        fail("EVIDENCE_PATH_INVALID")
    return resolved


def validate_evidence(
    root: Path, pointer: dict, *, decision_at: str | None = None
) -> dict:
    expected_keys = {
        "evidence_revision_id", "manifest_path", "manifest_file_sha256",
        "raw_path", "raw_file_sha256", "raw_response_sha256",
    }
    if not isinstance(pointer, dict) or set(pointer) != expected_keys:
        fail("EVIDENCE_POINTER_INVALID")
    for key in (
        "evidence_revision_id", "manifest_file_sha256", "raw_file_sha256",
        "raw_response_sha256",
    ):
        if not isinstance(pointer.get(key), str) or HEX64.fullmatch(pointer[key]) is None:
            fail("EVIDENCE_POINTER_INVALID")
    manifest_path = _safe_evidence_path(root, pointer["manifest_path"], "/manifest.json")
    raw_path = _safe_evidence_path(root, pointer["raw_path"], "/fred_vixcls.json.gz")
    try:
        manifest_bytes = manifest_path.read_bytes()
        raw_gzip_bytes = raw_path.read_bytes()
    except OSError:
        fail("EVIDENCE_FILE_MISSING")
    if sha256_bytes(manifest_bytes) != pointer["manifest_file_sha256"]:
        fail("MANIFEST_BYTES_MISMATCH")
    if sha256_bytes(raw_gzip_bytes) != pointer["raw_file_sha256"]:
        fail("RAW_FILE_BYTES_MISMATCH")
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("MANIFEST_JSON_INVALID")
    try:
        raw = gzip.decompress(raw_gzip_bytes)
    except (OSError, EOFError):
        fail("RAW_GZIP_INVALID")
    if sha256_bytes(raw) != pointer["raw_response_sha256"]:
        fail("RAW_RESPONSE_HASH_MISMATCH")
    captured = _parse_utc(manifest.get("captured_at_utc"), "CAPTURE_TIME_INVALID")
    expected = build_evidence_bundle(captured, raw)
    if manifest != expected["manifest"] or pointer != expected["pointer"]:
        fail("EVIDENCE_REDERIVATION_MISMATCH")
    if decision_at is not None and captured > _parse_utc(decision_at, "DECISION_TIME_INVALID"):
        fail("EVIDENCE_FROM_FUTURE")
    return {
        "captured_at_utc": manifest["captured_at_utc"],
        "observation": manifest["observation"],
        "pointer": dict(pointer),
        "transform_version": manifest["transform_version"],
        "authority": dict(manifest["authority"]),
    }
