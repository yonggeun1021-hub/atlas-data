"""Frozen, consistent snapshot of rolling pointers for data-timing-free tests.

Scheduled workflows rewrite related rolling pointers on different clocks:
``data/stage_history.json`` and ``data/briefing/krx/*.json`` by the daily
collect (05:55-06:35 KST), the Dynamic Clock candidate validity / identity
observations right after it, and the committed bounded reviews
``data/latest_{korea,us}_symbol_market_review.json`` hours later
(korea-market-signals.yml / free-market-data.yml).  Between those runs the
live tree is legitimately not one snapshot, so a regression that rebuilds a
past output from the live pointers fails on data timing, not code.

The fixture holds the exact git blobs of those pointers at one commit where
every consumer test passed.  Every read re-verifies the git blob id, sha256
and size recorded in the manifest, and the large dated inputs the tests keep
reading in place must still hash to the recorded values.
"""
from __future__ import annotations

import ast
import contextlib
import gzip
import hashlib
import json
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "test" / "fixtures" / "rolling_pointer_snapshot_20260913"
MANIFEST = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
KR_CAPTURE_DIR = "evidence/regime/kr_information_system/2026-09-11/source-capture"
VALIDITY_PATH = "evidence/operational/dynamic_clock/candidate_validity_window_assessment.json"
IDENTITY_PATH = "evidence/operational/dynamic_clock/candidate_identity_observation.json"
STAGE_HISTORY_PATH = "data/stage_history.json"


class SnapshotIntegrityError(AssertionError):
    """The frozen snapshot or an in-place dated input is not the recorded bytes."""


def git_blob_oid(raw: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def fixture_bytes(repo_path: str) -> bytes:
    entry = next((row for row in MANIFEST["files"] if row["repo_path"] == repo_path), None)
    if entry is None:
        raise SnapshotIntegrityError(f"NOT_IN_SNAPSHOT:{repo_path}")
    raw = gzip.decompress((FIXTURE_ROOT / entry["fixture_path"]).read_bytes())
    if (
        len(raw) != entry["bytes"]
        or hashlib.sha256(raw).hexdigest() != entry["sha256"]
        or git_blob_oid(raw) != entry["git_blob_oid"]
    ):
        raise SnapshotIntegrityError(f"SNAPSHOT_BYTES_MISMATCH:{repo_path}")
    return raw


def verify_in_place_inputs() -> None:
    for entry in MANIFEST["immutable_inputs_in_place"]:
        path = ROOT / entry["repo_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise SnapshotIntegrityError(f"DATED_INPUT_REWRITTEN:{entry['repo_path']}")


def valid_stages() -> tuple[str, ...]:
    """The Notion stage vocabulary, read from its single source of truth.

    A test that asserts a live pipeline stage must not restate the
    enumeration: ``collectors/common.py`` owns it.  That module imports
    ``requests`` at the top level, which the offline regression shards do
    not install, so the literal is read with ``ast`` instead of importing
    it.  A rename or a new stage therefore reaches the assertion, while a
    watchlist stage change does not.
    """
    source = (ROOT / "collectors" / "common.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "VALID_STAGES" for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise SnapshotIntegrityError("VALID_STAGES_NOT_FOUND:collectors/common.py")


def materialize(dest: Path) -> Path:
    """Write every frozen pointer under ``dest`` at its repository path."""
    verify_in_place_inputs()
    dest = Path(dest)
    for entry in MANIFEST["files"]:
        target = dest / entry["repo_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fixture_bytes(entry["repo_path"]))
    return dest


def read_json(snapshot_root: Path, repo_path: str):
    return json.loads((Path(snapshot_root) / repo_path).read_text(encoding="utf-8"))


def kr_inputs(snapshot_root: Path) -> dict:
    snapshot_root = Path(snapshot_root)
    return {
        "session_date": MANIFEST["session_dates"]["KR"],
        "universe_path": ROOT / "data" / "observations" / "krx_global_universe" / MANIFEST["session_dates"]["KR"] / "packet.json",
        "market_signals_path": snapshot_root / "data" / "latest_korea_market_signals.json",
        "stage_history_path": snapshot_root / STAGE_HISTORY_PATH,
        "bounded_review_path": snapshot_root / "data" / "latest_korea_symbol_market_review.json",
        "watchlist_root": snapshot_root / "data" / "briefing" / "krx",
        "capture_dir": ROOT / KR_CAPTURE_DIR,
    }


def us_inputs(snapshot_root: Path) -> dict:
    snapshot_root = Path(snapshot_root)
    return {
        "session_date": MANIFEST["session_dates"]["US"],
        "universe_path": ROOT / "data" / "observations" / "us_global_universe" / MANIFEST["session_dates"]["US"] / "packet.json",
        "market_data_path": snapshot_root / "data" / "latest_free_market_data.json",
        "stage_history_path": snapshot_root / STAGE_HISTORY_PATH,
        "bounded_review_path": snapshot_root / "data" / "latest_us_symbol_market_review.json",
    }


@contextlib.contextmanager
def pinned_candidate_receipt_sources(module, snapshot_root: Path):
    """Point the candidate evidence lifecycle receipt's rolling-pointer
    defaults (stage history, Dynamic Clock validity and identity) at the
    frozen snapshot on every call path, including ``lookup_symbol`` ->
    ``validate_receipt`` -> ``build_receipt`` re-derivation. Only default
    source locations change; no receipt logic is replaced."""
    snapshot_root = Path(snapshot_root)
    sources = {
        "stage_history_path": snapshot_root / STAGE_HISTORY_PATH,
        "validity_path": snapshot_root / VALIDITY_PATH,
    }
    with mock.patch.object(
        module.build_receipt, "__kwdefaults__", dict(module.build_receipt.__kwdefaults__, **sources)
    ), mock.patch.object(
        module.validate_receipt, "__kwdefaults__", dict(module.validate_receipt.__kwdefaults__, **sources)
    ), mock.patch.object(
        module.load_gate_connection_sources,
        "__kwdefaults__",
        dict(module.load_gate_connection_sources.__kwdefaults__, identity_path=snapshot_root / IDENTITY_PATH),
    ):
        yield sources
