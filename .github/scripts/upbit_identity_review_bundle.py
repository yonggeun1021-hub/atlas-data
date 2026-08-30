#!/usr/bin/env python3
"""Persist the full P3-12 Upbit identity proposal set for human review.

The daily universe packet intentionally keeps only proposal counts/findings.
That is sufficient for fail-closed classification but not for a human to
inspect all proposed market-to-asset mappings.  This companion producer
rebuilds the exact existing PROPOSED_UNRATIFIED proposals from the retained
public raw snapshot and stores them in a separate append-only review bundle.

It never edits a canonical identity/taxonomy/policy file and never grants
Universe, PAPER, decision, action, order, Production, Trading, or REAL
authority.  Absence of a ratified broad canonical registry is recorded as an
explicit review blocker rather than replaced by an inferred registry.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "upbit_identity_review"
SCHEMA_VERSION = "upbit_identity_review_bundle/1"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_identity_review", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_review", "identity/upbit_market_identity_proposal.py")


class IdentityReviewBundleError(ValueError):
    """Fail-closed identity review bundle violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    if not Path(path).is_file() or Path(path).is_symlink():
        raise IdentityReviewBundleError(f"SOURCE_FILE_INVALID:{path}")
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle(snapshot_date: str, raw_root: Path = RAW_ROOT) -> dict:
    snapshot = Path(raw_root) / snapshot_date
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise IdentityReviewBundleError(f"RAW_SNAPSHOT_INVALID:{snapshot_date}")
    capture_contract = UNI.UPBIT_CAPTURE.load_contract()
    try:
        core = UNI.load_snapshot_core(snapshot, capture_contract)
    except UNI.UPBIT_CAPTURE.CaptureError as exc:
        raise IdentityReviewBundleError(f"RAW_SNAPSHOT_INVALID:{snapshot_date}:{exc}") from exc

    exceptions_path = ROOT / "config" / "upbit_asset_identity_exceptions.json"
    exceptions_doc = None
    if exceptions_path.exists():
        exceptions_doc = json.loads(exceptions_path.read_text(encoding="utf-8"))
    market_all_file = capture_contract["market_all_raw_file"]
    proposals = [
        IDP.build_proposal(
            {
                "market": market,
                "korean_name": entry.get("korean_name"),
                "english_name": entry.get("english_name"),
            },
            review_as_of=snapshot_date,
            source_url=capture_contract["market_all_endpoint"],
            response_sha256=core["component_hashes"][market_all_file],
            available_at=core["available_at"],
            exceptions_doc=exceptions_doc,
        )
        for market, entry in sorted(core["markets"].items())
        if entry.get("market_all_available")
    ]
    findings = IDP.identity_review_findings(proposals, known_canonical_ids=None)
    policy_path = ROOT / "config" / "upbit_tradeable_universe_policy.json"
    taxonomy_path = ROOT / "config" / "upbit_exclusion_taxonomy.json"
    packet = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": snapshot_date,
        "generated_at": core["available_at"],
        "review_status": "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY",
        "source": {
            "raw_snapshot_path": f"evidence/crypto/upbit/raw/{snapshot_date}",
            "raw_manifest_sha256": core["manifest_sha256"],
            "market_all_file": market_all_file,
            "market_all_response_sha256": core["component_hashes"][market_all_file],
            "market_all_source_url": capture_contract["market_all_endpoint"],
            "available_at": core["available_at"],
            "universe_policy_path": "config/upbit_tradeable_universe_policy.json",
            "universe_policy_file_sha256": file_sha256(policy_path),
            "taxonomy_path": "config/upbit_exclusion_taxonomy.json",
            "taxonomy_file_sha256": file_sha256(taxonomy_path),
        },
        "review_boundary": {
            "broad_ratified_canonical_registry_status": "ABSENT",
            "cross_reference_check_status": "NOT_RUN_RATIFIED_BROAD_REGISTRY_ABSENT",
            "collision_check_status": "RUN",
            "meaning_of_zero_findings": (
                "No duplicate candidate target was found. This does not ratify any proposal "
                "and does not prove cross-registry identity."
            ),
        },
        "summary": {
            "proposal_count": len(proposals),
            "finding_count": len(findings),
            "blocked_market_count": len(IDP.blocked_markets(findings)),
        },
        "findings": findings,
        "proposals": proposals,
        "authority": {
            "review_only": True,
            "canonical_config_mutation_authorized": False,
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "policy_ratification_authorized": False,
            "tradeable_universe_promotion_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "decision_eligible": False,
            "action_generation_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def output_path(snapshot_date: str, data_root: Path = DATA_ROOT) -> Path:
    return Path(data_root) / snapshot_date / "packet.json"


def populate(snapshot_date: str, raw_root: Path = RAW_ROOT, data_root: Path = DATA_ROOT) -> dict:
    packet = build_bundle(snapshot_date, raw_root)
    target = output_path(snapshot_date, data_root)
    if target.exists():
        if target.is_symlink():
            raise IdentityReviewBundleError(f"EXISTING_PACKET_INVALID:{target}")
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IdentityReviewBundleError(f"EXISTING_PACKET_UNREADABLE:{snapshot_date}:{exc}") from exc
        existing_hash = existing.get("payload_sha256")
        if not isinstance(existing_hash, str) or len(existing_hash) != 64:
            raise IdentityReviewBundleError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}")
        if payload_sha256({key: value for key, value in existing.items() if key != "payload_sha256"}) != existing_hash:
            raise IdentityReviewBundleError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}")
        if existing != packet:
            # Policy/taxonomy files are evidence inputs only by historical
            # file hash for this review-only proposal bundle. A later
            # effective-dated ratification must not rewrite or re-date that
            # prior observation. Accept exactly that two-pin transition,
            # while requiring every other byte-semantic field to match the
            # deterministic rebuild from the same raw snapshot.
            historical = json.loads(json.dumps(existing))
            rebuilt = json.loads(json.dumps(packet))
            for candidate in (historical, rebuilt):
                candidate.pop("payload_sha256", None)
                source = candidate.get("source") or {}
                source.pop("universe_policy_file_sha256", None)
                source.pop("taxonomy_file_sha256", None)
            if historical != rebuilt:
                raise IdentityReviewBundleError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}")
            return {
                "outcome": "verified_historical", "path": str(target),
                "payload_sha256": existing_hash,
            }
        return {"outcome": "verified_existing", "path": str(target), "payload_sha256": packet["payload_sha256"]}
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    try:
        temp.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {"outcome": "populated", "path": str(target), "payload_sha256": packet["payload_sha256"]}


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key in ("outcome", "path", "payload_sha256"):
            handle.write(f"{key}={result.get(key, '')}\n")


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_date")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(args.snapshot_date, args.raw_root, args.data_root)
    except IdentityReviewBundleError as exc:
        _write_github_output({"outcome": "failed", "path": "", "payload_sha256": ""})
        print(f"P3-12 Upbit identity review bundle failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"P3-12 Upbit identity review bundle {result['outcome']} "
        f"date={args.snapshot_date} path={result['path']} sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
