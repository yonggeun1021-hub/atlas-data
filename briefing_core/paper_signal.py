#!/usr/bin/env python3
"""Append-only PAPER signal/result publication boundary.

This module is the only supported bridge from a 24-hour PAPER runtime into the
public repository.  It accepts data, never orders, and refuses every path owned
by Briefing, Portal, Notion or account state.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any


SIGNAL_SCHEMA = "atlas_paper_signal/1"
RESULT_SCHEMA = "atlas_paper_result/1"
ALLOWED_ROOTS = (
    PurePosixPath("runtime/paper/signals/v1"),
    PurePosixPath("runtime/paper/results/v1"),
)
FORBIDDEN_ROOTS = (
    PurePosixPath("briefing"),
    PurePosixPath("briefing_core"),
    PurePosixPath("data/briefing"),
    PurePosixPath("evidence/daily_briefing"),
    PurePosixPath("generated"),
    PurePosixPath("public"),
    PurePosixPath("notion"),
)


class PaperBoundaryError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _within(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or root in path.parents


def validate_output_path(relative_path: str) -> PurePosixPath:
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise PaperBoundaryError("PAPER_OUTPUT_PATH_INVALID")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or str(path) != relative_path:
        raise PaperBoundaryError("PAPER_OUTPUT_PATH_INVALID")
    if any(_within(path, root) for root in FORBIDDEN_ROOTS):
        raise PaperBoundaryError("PAPER_CORE_PATH_FORBIDDEN")
    if not any(_within(path, root) for root in ALLOWED_ROOTS):
        raise PaperBoundaryError("PAPER_OUTPUT_OUTSIDE_OWNED_ROOT")
    if path.suffix != ".json":
        raise PaperBoundaryError("PAPER_OUTPUT_EXTENSION_INVALID")
    return path


def validate_signal(value: dict) -> None:
    required = {
        "schema_version", "signal_id", "event_at", "market", "symbol",
        "signal_type", "payload", "lineage", "authority",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PaperBoundaryError("PAPER_SIGNAL_FIELDS_INVALID")
    if value.get("schema_version") != SIGNAL_SCHEMA:
        raise PaperBoundaryError("PAPER_SIGNAL_SCHEMA_INVALID")
    if not isinstance(value.get("signal_id"), str) or not value["signal_id"]:
        raise PaperBoundaryError("PAPER_SIGNAL_ID_INVALID")
    lineage = value.get("lineage")
    if (
        not isinstance(lineage, dict)
        or not isinstance(lineage.get("source_commit"), str)
        or len(lineage["source_commit"]) != 40
        or not isinstance(lineage.get("generation_id"), str)
        or len(lineage["generation_id"]) != 64
    ):
        raise PaperBoundaryError("PAPER_SIGNAL_LINEAGE_INVALID")
    expected_authority = {
        "account_mode": "PAPER",
        "real_capital": False,
        "order_authority": False,
        "production_authority": False,
        "trading_authority": False,
    }
    if value.get("authority") != expected_authority:
        raise PaperBoundaryError("PAPER_SIGNAL_AUTHORITY_INVALID")
    text = json.dumps(value, ensure_ascii=False).lower()
    for forbidden in ("api_secret", "broker_secret", "private_key", "access_token"):
        if forbidden in text:
            raise PaperBoundaryError("PAPER_SIGNAL_SECRET_FIELD_FORBIDDEN")


def validate_result(value: dict) -> None:
    required = {
        "schema_version", "result_id", "signal_id", "observed_at", "outcome",
        "payload", "lineage", "authority",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PaperBoundaryError("PAPER_RESULT_FIELDS_INVALID")
    if value.get("schema_version") != RESULT_SCHEMA:
        raise PaperBoundaryError("PAPER_RESULT_SCHEMA_INVALID")
    if not all(isinstance(value.get(key), str) and value[key] for key in (
        "result_id", "signal_id", "observed_at", "outcome"
    )):
        raise PaperBoundaryError("PAPER_RESULT_IDENTITY_INVALID")
    signal_view = {
        "schema_version": SIGNAL_SCHEMA,
        "signal_id": value["signal_id"],
        "event_at": value["observed_at"],
        "market": "RESULT_VALIDATION_ONLY",
        "symbol": "RESULT_VALIDATION_ONLY",
        "signal_type": "RESULT_VALIDATION_ONLY",
        "payload": value["payload"],
        "lineage": value["lineage"],
        "authority": value["authority"],
    }
    validate_signal(signal_view)


def publish(repo_root: Path, relative_path: str, value: dict) -> dict:
    if value.get("schema_version") == SIGNAL_SCHEMA:
        validate_signal(value)
    elif value.get("schema_version") == RESULT_SCHEMA:
        validate_result(value)
    else:
        raise PaperBoundaryError("PAPER_RECORD_SCHEMA_INVALID")
    path = repo_root / validate_output_path(relative_path)
    body = canonical(value) + b"\n"
    if path.exists():
        if path.read_bytes() == body:
            return {"result": "NO_CHANGE", "path": relative_path, "duplicate_count": 0}
        raise PaperBoundaryError("PAPER_APPEND_ONLY_CONFLICT")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"result": "APPLIED", "path": relative_path, "duplicate_count": 0}
