#!/usr/bin/env python3
"""P10-06 durable, append-only storage for zero-capital review records.

This module deliberately delegates record semantics to
``investment_review_shadow_ledger``.  It only serializes already-valid
records, and replays them with the same validator before accepting another
append.  The sidecar lock makes append operations cooperative-process atomic;
it is not an authority, decision, or execution interface.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "shadow" / "investment_review_shadow_ledger.py"


def _load_ledger():
    spec = importlib.util.spec_from_file_location("p10_06_review_ledger", LEDGER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("REVIEW_LEDGER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER = _load_ledger()


class InvestmentReviewShadowStoreError(ValueError):
    """A stable failure for durable-record boundary violations."""


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _canonical_line(record: dict) -> bytes:
    return (LEDGER.canonical_json(record) + "\n").encode("utf-8")


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Yield while holding a cooperative exclusive lock for ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(_lock_path(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _records_unlocked(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InvestmentReviewShadowStoreError("STORE_READ_FAILED") from exc
    if not payload:
        return []
    rows: list[dict] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InvestmentReviewShadowStoreError(f"STORE_JSON_INVALID:{line_number}") from exc
        if not isinstance(row, dict):
            raise InvestmentReviewShadowStoreError(f"STORE_RECORD_INVALID:{line_number}")
        rows.append(row)
    return rows


def _validate_chain(rows: list[dict]) -> list[dict]:
    previous_hash = None
    for expected_sequence, row in enumerate(rows, start=1):
        try:
            checked = LEDGER.validate_record(row)
        except Exception as exc:  # Existing module owns record-level error details.
            raise InvestmentReviewShadowStoreError(f"STORE_RECORD_REJECTED:{expected_sequence}") from exc
        if checked["sequence"] != expected_sequence:
            raise InvestmentReviewShadowStoreError(f"STORE_SEQUENCE_GAP:{expected_sequence}")
        if checked["lineage"]["previous_record_sha256"] != previous_hash:
            raise InvestmentReviewShadowStoreError(f"STORE_CHAIN_LINK_INVALID:{expected_sequence}")
        previous_hash = checked["record_sha256"]
    return copy.deepcopy(rows)


def replay(path: Path) -> list[dict]:
    """Read and validate one complete committed chain without mutation.

    ``append()`` replaces the ledger path only after writing and syncing a
    complete temporary file.  A replay concurrent with an append can therefore
    observe either the old or the new complete file; it creates neither a
    parent directory nor a sidecar lock file.
    """
    target = Path(path)
    return _validate_chain(_records_unlocked(target))


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace(path: Path, candidate: dict) -> None:
    """Commit the next line by replacing the whole verified ledger atomically."""
    try:
        existing = path.read_bytes() if path.exists() else b""
    except OSError as exc:
        raise InvestmentReviewShadowStoreError("STORE_READ_FAILED") from exc
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    descriptor = None
    temporary_name = None
    replaced = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(existing)
            handle.write(_canonical_line(candidate))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
        replaced = True
        _fsync_parent(path)
    except OSError as exc:
        if replaced:
            raise InvestmentReviewShadowStoreError(
                "STORE_APPEND_COMMITTED_DURABILITY_UNCERTAIN"
            ) from exc
        raise InvestmentReviewShadowStoreError("STORE_APPEND_WRITE_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def append(path: Path, record: dict) -> dict:
    """Append one valid next record and return a detached copy.

    The record is validated before any ledger file is opened for write. While
    holding the sidecar lock, the existing chain is replayed again, so two
    writers cannot both append the same sequence or predecessor reference.
    A same-directory temporary file is fsynced and atomically replaced only
    after the complete next chain is written; callers outside this cooperative
    lock boundary are not serialized by this module.
    """
    try:
        candidate = LEDGER.validate_record(record)
    except Exception as exc:
        raise InvestmentReviewShadowStoreError("STORE_APPEND_RECORD_INVALID") from exc
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _locked(target):
        rows = _validate_chain(_records_unlocked(target))
        if any(row["record_sha256"] == candidate["record_sha256"] for row in rows):
            raise InvestmentReviewShadowStoreError("STORE_DUPLICATE_RECORD")
        expected_sequence = len(rows) + 1
        expected_previous = rows[-1]["record_sha256"] if rows else None
        if candidate["sequence"] != expected_sequence:
            raise InvestmentReviewShadowStoreError("STORE_APPEND_SEQUENCE_INVALID")
        if candidate["lineage"]["previous_record_sha256"] != expected_previous:
            raise InvestmentReviewShadowStoreError("STORE_APPEND_PREVIOUS_HASH_INVALID")
        _atomic_replace(target, candidate)
        return copy.deepcopy(candidate)
