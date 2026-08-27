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
import datetime as dt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "crypto_taxonomy_gap"
SCHEMA_VERSION = "crypto_taxonomy_gap_inventory/1"


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
) -> dict:
    validate_source_date(source_date)
    snapshot_dir = Path(raw_root) / source_date
    if not snapshot_dir.is_dir():
        raise InventoryError(f"RAW_BUNDLE_MISSING:{source_date}")

    transform = CB.build_transform(
        snapshot_dir,
        universe_policy_path=universe_policy_path,
        exclusion_taxonomy_path=taxonomy_path,
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
) -> None:
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError("INVENTORY_SCHEMA_INVALID")
    source_date = record.get("source_date")
    if not isinstance(source_date, str):
        raise InventoryError("INVENTORY_SOURCE_DATE_INVALID")
    rebuilt = build_inventory(
        source_date, raw_root, universe_policy_path, taxonomy_path, identity_path
    )
    if record != rebuilt:
        raise InventoryError("INVENTORY_DRIFT_OR_TAMPER")


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
        validate_inventory(
            existing, raw_root, universe_policy_path, taxonomy_path, identity_path
        )
        return {
            "outcome": "verified_existing",
            "path": str(target),
            "payload_sha256": existing["payload_sha256"],
        }

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
