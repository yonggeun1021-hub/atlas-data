#!/usr/bin/env python3
"""Build a deterministic P3-04 taxonomy review inventory.

The inventory is evidence about the current source-coverage blocker. It
reuses the production Crypto Breadth transform over an already-captured
Kraken snapshot and never calls a provider, classifies an asset, changes a
taxonomy record, or creates an investable universe.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import datetime as dt
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "crypto_taxonomy_gap"
SCHEMA_VERSION = "crypto_taxonomy_gap_inventory/1"
# Immutable, content-addressed copies of the exact taxonomy bytes an
# inventory was built from.  They live inside the source-date directory the
# capture workflow already commits wholesale, so replaying an older record
# never depends on the mutable current policy file.
TAXONOMY_REVISION_DIRNAME = "taxonomy_revisions"
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load_module("crypto_breadth_for_gap_inventory", ".github/scripts/crypto_breadth.py")


class InventoryError(ValueError):
    """Fail-closed taxonomy gap inventory violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def output_path(source_date: str, data_root: Path = DATA_ROOT) -> Path:
    validate_source_date(source_date)
    return Path(data_root) / source_date / "packet.json"


def source_ref(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        # Test-only injected policies remain deterministic without exposing a
        # machine-specific temporary directory in the packet.
        return f"external_fixture/{resolved.name}"


def taxonomy_revision_path(
    source_date: str, taxonomy_sha256: str, data_root: Path = DATA_ROOT
) -> Path:
    validate_source_date(source_date)
    if (
        not isinstance(taxonomy_sha256, str)
        or SHA256_HEX.fullmatch(taxonomy_sha256) is None
    ):
        raise InventoryError(f"TAXONOMY_REVISION_SHA_INVALID:{taxonomy_sha256!r}")
    return (
        Path(data_root)
        / source_date
        / TAXONOMY_REVISION_DIRNAME
        / f"{taxonomy_sha256}.json"
    )


def read_revision_bytes(path: Path, code: str) -> bytes:
    """Read a content-addressed revision without following a symlink.

    A symlink could point anywhere, including at the mutable current policy,
    so it is rejected before any byte is read rather than resolved.
    """
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise InventoryError(f"{code}:symlink:{path.name}")
    if not path.is_file():
        raise InventoryError(f"{code}:missing:{path.name}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise InventoryError(f"{code}:unreadable:{exc}") from exc


def resolve_taxonomy_revision(
    source_date: str, taxonomy_sha256: str, data_root: Path = DATA_ROOT
) -> Path:
    """Return the verified immutable taxonomy revision pinned by a record.

    Every unresolvable case fails closed: the caller must never fall back to
    the current taxonomy or accept the stored record on its own authority.
    """
    code = "HISTORICAL_TAXONOMY_REVISION_UNAVAILABLE"
    if (
        not isinstance(taxonomy_sha256, str)
        or SHA256_HEX.fullmatch(taxonomy_sha256) is None
    ):
        raise InventoryError(f"{code}:sha_invalid:{taxonomy_sha256!r}")
    path = taxonomy_revision_path(source_date, taxonomy_sha256, data_root)
    raw = read_revision_bytes(path, code)
    if hashlib.sha256(raw).hexdigest() != taxonomy_sha256:
        raise InventoryError(f"{code}:hash_mismatch:{taxonomy_sha256}")
    try:
        json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"{code}:malformed:{exc}") from exc
    return path


def retain_taxonomy_revision(
    source_date: str, taxonomy_path: Path, data_root: Path = DATA_ROOT
) -> Path:
    """Persist the exact taxonomy bytes this inventory will bind.

    Called before an inventory is published so a later taxonomy update can
    never leave a committed record pointing at absent or different bytes.
    """
    code = "TAXONOMY_REVISION_RETENTION_FAILED"
    try:
        raw = Path(taxonomy_path).read_bytes()
    except OSError as exc:
        raise InventoryError(f"{code}:source_unreadable:{exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    target = taxonomy_revision_path(source_date, digest, data_root)
    if target.is_symlink() or target.parent.is_symlink():
        raise InventoryError(f"{code}:symlink:{digest}")
    if target.exists():
        # Content-addressed and already retained: verify, never rewrite.
        if read_revision_bytes(target, code) != raw:
            raise InventoryError(f"{code}:existing_content_mismatch:{digest}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        # O_EXCL keeps a concurrent writer from sharing this scratch file;
        # os.fdopen takes ownership of the descriptor and closes it.
        handle = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Linking publishes the complete file atomically without replacing
            # a revision another writer retained after our initial check.
            os.link(temp, target)
        except FileExistsError:
            if read_revision_bytes(target, code) != raw:
                raise InventoryError(f"{code}:existing_content_mismatch:{digest}")
    except OSError as exc:
        raise InventoryError(f"{code}:{exc}") from exc
    finally:
        if temp.exists():
            temp.unlink()
    readback = read_revision_bytes(target, code)
    if hashlib.sha256(readback).hexdigest() != digest:
        raise InventoryError(f"{code}:readback:{digest}")
    return target


def validate_source_date(source_date: str) -> None:
    if not isinstance(source_date, str):
        raise InventoryError("SOURCE_DATE_INVALID")
    try:
        parsed = dt.date.fromisoformat(source_date)
    except ValueError as exc:
        raise InventoryError("SOURCE_DATE_INVALID") from exc
    if parsed.isoformat() != source_date:
        raise InventoryError("SOURCE_DATE_INVALID")


def build_inventory(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    universe_policy_path: Path = CB.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CB.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = CB.IDENTITY_EXCEPTIONS_PATH,
    taxonomy_bytes_path: Optional[Path] = None,
) -> dict:
    """Build the inventory for `source_date`.

    `taxonomy_path` is always the logical policy location recorded in
    lineage.  `taxonomy_bytes_path`, when given, supplies the immutable
    taxonomy bytes the rebuild actually reads, which is what lets a stored
    record be replayed under the revision it was pinned to.
    """
    validate_source_date(source_date)
    snapshot_dir = Path(raw_root) / source_date
    if not snapshot_dir.is_dir():
        raise InventoryError(f"RAW_BUNDLE_MISSING:{source_date}")

    transform = CB.build_transform(
        snapshot_dir,
        universe_policy_path=universe_policy_path,
        exclusion_taxonomy_path=(
            taxonomy_path if taxonomy_bytes_path is None else taxonomy_bytes_path
        ),
        identity_exceptions_path=identity_path,
    )
    universe = transform["universe"]
    for field in (
        "breadth_classification_authorized",
        "threshold_authorized",
        "regime_score_authorized",
        "production_wiring_authorized",
        "trading_action_authorized",
    ):
        if transform.get(field) is not False:
            raise InventoryError(f"SOURCE_AUTHORITY_NOT_FALSE:{field}")
    unknown = universe["taxonomy_unknown_before_cutoff"]
    excluded = universe["taxonomy_excluded_before_cutoff"]
    if transform["status"] == "UNKNOWN" and unknown:
        if transform["unknown_reason"] != "TAXONOMY_COVERAGE_UNKNOWN":
            raise InventoryError("UNKNOWN_REASON_INCONSISTENT_WITH_TAXONOMY_GAP")

    record = {
        "schema_version": SCHEMA_VERSION,
        "status": "REVIEW_INVENTORY_ONLY",
        "source_date": source_date,
        "as_of_date": transform["as_of_date"],
        "generated_at": transform["lineage"]["available_at"],
        "source_outcome": {
            "status": transform["status"],
            "unknown_reason": transform["unknown_reason"],
        },
        "lineage": {
            "raw_bundle_path": f"evidence/crypto/breadth/raw/{source_date}",
            "manifest_sha256": transform["lineage"]["manifest_sha256"],
            "capture_version": transform["lineage"]["capture_version"],
            "available_at": transform["lineage"]["available_at"],
            "identity_policy_version": transform["lineage"]["identity_policy_version"],
            "identity_policy_sha256": transform["lineage"]["identity_policy_sha256"],
            "universe_policy_path": source_ref(universe_policy_path),
            "universe_policy_version": universe["policy_version"],
            "universe_policy_sha256": universe["policy_sha256"],
            "taxonomy_path": source_ref(taxonomy_path),
            "taxonomy_policy_version": universe["taxonomy"]["policy_version"],
            "taxonomy_policy_sha256": universe["taxonomy"]["policy_sha256"],
            "taxonomy_approval_status": universe["taxonomy"]["approval_status"],
        },
        "selection_context": {
            "target_asset_count": universe["target_asset_count"],
            "ranked_candidate_count": universe["ranked_candidate_count"],
            "ranking_eligible_count": universe["ranked_candidate_count"],
            "ranking_ineligible_count": universe["ranking_ineligible_count"],
            "known_eligible_count_so_far": universe["known_eligible_count_so_far"],
            "unknown_before_cutoff_count": len(unknown),
            "excluded_before_cutoff_count": len(excluded),
        },
        "review_population": {
            "taxonomy_unknown_before_cutoff": unknown,
            "taxonomy_excluded_before_cutoff": excluded,
            "ranking_ineligible": universe["ranking_ineligible"],
        },
        "authority": {
            "classifications_created": 0,
            "records_ratified": 0,
            "taxonomy_authorized": False,
            "investability_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    record["payload_sha256"] = payload_sha256(record)
    return record


def validate_inventory(
    record: dict,
    raw_root: Path = RAW_ROOT,
    universe_policy_path: Path = CB.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CB.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = CB.IDENTITY_EXCEPTIONS_PATH,
    data_root: Path = DATA_ROOT,
) -> str:
    """Independently rebuild `record` and require exact byte equality.

    Returns the mode that verified the record.  A stored record whose only
    difference from the current inputs is an advanced taxonomy policy is
    replayed against the immutable revision it pinned; every other
    difference, and every unresolvable revision, still fails closed.
    """
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError("INVENTORY_SCHEMA_INVALID")
    source_date = record.get("source_date")
    if not isinstance(source_date, str):
        raise InventoryError("INVENTORY_SOURCE_DATE_INVALID")
    rebuilt = build_inventory(
        source_date, raw_root, universe_policy_path, taxonomy_path, identity_path
    )
    if record == rebuilt:
        return "current_inputs"

    lineage = record.get("lineage")
    current_lineage = rebuilt["lineage"]
    if not isinstance(lineage, dict):
        raise InventoryError("INVENTORY_DRIFT_OR_TAMPER")
    pinned_sha = lineage.get("taxonomy_policy_sha256")
    if (
        not isinstance(pinned_sha, str)
        or pinned_sha == current_lineage["taxonomy_policy_sha256"]
    ):
        # The taxonomy bytes have not moved, so the difference is real
        # drift or tampering and a revision replay must not launder it.
        raise InventoryError("INVENTORY_DRIFT_OR_TAMPER")
    # The logical policy location is checked against the supplied path we
    # were asked to validate against, never copied from the record.
    if lineage.get("taxonomy_path") != current_lineage["taxonomy_path"]:
        raise InventoryError("INVENTORY_DRIFT_OR_TAMPER")
    revision = resolve_taxonomy_revision(source_date, pinned_sha, data_root)
    replayed = build_inventory(
        source_date,
        raw_root,
        universe_policy_path,
        taxonomy_path,
        identity_path,
        taxonomy_bytes_path=revision,
    )
    if record != replayed:
        raise InventoryError("INVENTORY_DRIFT_OR_TAMPER")
    return "pinned_taxonomy_revision"


def populate(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    data_root: Path = DATA_ROOT,
    universe_policy_path: Path = CB.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CB.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = CB.IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    record = build_inventory(
        source_date, raw_root, universe_policy_path, taxonomy_path, identity_path
    )
    target = output_path(source_date, data_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InventoryError(f"EXISTING_INVENTORY_UNREADABLE:{exc}") from exc
        # An already-published record is verified as-is; it must not trigger
        # a snapshot of whatever the taxonomy happens to be today.
        validate_inventory(
            existing,
            raw_root,
            universe_policy_path,
            taxonomy_path,
            identity_path,
            data_root,
        )
        return {
            "outcome": "verified_existing",
            "path": str(target),
            "payload_sha256": existing["payload_sha256"],
        }

    # Retain the pinned taxonomy bytes first: an interrupted or failed
    # retention must leave no inventory referencing them.
    revision = retain_taxonomy_revision(source_date, taxonomy_path, data_root)
    if revision.stem != record["lineage"]["taxonomy_policy_sha256"]:
        raise InventoryError(
            "TAXONOMY_REVISION_RETENTION_FAILED:pin_mismatch:"
            f"{record['lineage']['taxonomy_policy_sha256']}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    try:
        temp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "outcome": "populated",
        "path": str(target),
        "payload_sha256": record["payload_sha256"],
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key in ("outcome", "path", "payload_sha256"):
            handle.write(f"{key}={result.get(key, '')}\n")


def run(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_date")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(args.source_date, args.raw_root, args.data_root)
    except (InventoryError, CB.BreadthError) as exc:
        _write_github_output({"outcome": "failed", "path": "", "payload_sha256": ""})
        print(f"P3-04 taxonomy gap inventory failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"P3-04 taxonomy gap inventory {result['outcome']}"
        f" date={args.source_date} path={result['path']}"
        f" sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
