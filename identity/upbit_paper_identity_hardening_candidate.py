#!/usr/bin/env python3
"""Build an exact-hash P3-12 PAPER identity/taxonomy approval candidate.

This module consumes a validated Upbit market snapshot and a validated
first-party identity snapshot. It produces review material only: all
authority remains false and `release_ready` is false until a later explicit
CIO decision names the exact proposed registry, taxonomy, and consumer hashes.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "upbit_paper_identity_hardening_contract.json"
FIRST_PARTY_CAPTURE_PATH = ROOT / ".github" / "scripts" / "upbit_first_party_identity_capture.py"
MARKET_CAPTURE_PATH = ROOT / ".github" / "scripts" / "upbit_market_capture.py"
FREEZE_PATH = ROOT / "config" / "upbit_identity_taxonomy_governance_freeze.json"
CONSUMER_PATH = Path(__file__).resolve()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


FP = _load_module("upbit_first_party_capture_for_hardening", FIRST_PARTY_CAPTURE_PATH)
MARKET = _load_module("upbit_market_capture_for_hardening", MARKET_CAPTURE_PATH)


class HardeningError(ValueError):
    """Fail-closed candidate construction error."""


def fail(code: str, detail: str) -> None:
    raise HardeningError(f"{code}:{detail}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_READ_FAILED", f"{path}:{exc}")
    if not isinstance(value, dict):
        fail("JSON_ROOT_INVALID", str(path))
    return value


def load_contract(path: Path = CONTRACT_PATH, freeze_path: Path = FREEZE_PATH) -> dict:
    contract = _read_json(path)
    freeze = _read_json(freeze_path)
    if contract.get("schema_version") != "upbit_paper_identity_hardening_contract/1":
        fail("CONTRACT_SCHEMA_INVALID", repr(contract.get("schema_version")))
    if contract.get("review_status") != "PROPOSED_UNRATIFIED_EXACT_HASH_APPROVAL_REQUIRED":
        fail("CONTRACT_REVIEW_STATUS_INVALID", repr(contract.get("review_status")))
    if contract.get("unknown_asset_policy") != "fail_closed_unknown":
        fail("CONTRACT_UNKNOWN_ASSET_POLICY_INVALID", repr(contract.get("unknown_asset_policy")))
    if contract.get("unknown_market_policy") != "fail_closed_unratified_identity":
        fail("CONTRACT_UNKNOWN_MARKET_POLICY_INVALID", repr(contract.get("unknown_market_policy")))
    authority = contract.get("authority")
    if not isinstance(authority, dict) or not authority or any(value is not False for value in authority.values()):
        fail("CONTRACT_AUTHORITY_INVALID", repr(authority))
    assets = contract.get("assets")
    if not isinstance(assets, list) or not assets:
        fail("CONTRACT_ASSETS_INVALID", repr(assets))
    markets = [row.get("market") for row in assets]
    canonical_ids = [row.get("canonical_asset_id") for row in assets]
    if len(markets) != len(set(markets)) or len(canonical_ids) != len(set(canonical_ids)):
        fail("CONTRACT_IDENTITY_COLLISION", repr(markets))
    if sorted(markets) != sorted(freeze.get("blocked_paper_markets") or []):
        fail("CONTRACT_FREEZE_SCOPE_MISMATCH", repr(markets))
    for row in assets:
        if row.get("market") != f"KRW-{row.get('canonical_asset_id')}":
            fail("CONTRACT_MARKET_ID_MISMATCH", repr(row))
        if row.get("category") != contract.get("eligible_category"):
            fail("CONTRACT_CATEGORY_INVALID", repr(row))
        if not all(isinstance(row.get(key), str) and row[key] for key in ("korean_name", "english_name")):
            fail("CONTRACT_NAME_INVALID", repr(row))
    return copy.deepcopy(contract)


def _load_market_names(snapshot_dir: Path) -> tuple[dict, dict]:
    manifest = MARKET.validate_snapshot(snapshot_dir)
    market_all_file = MARKET.load_contract()["market_all_raw_file"]
    try:
        with gzip.open(Path(snapshot_dir) / market_all_file, "rb") as handle:
            rows = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail("UPBIT_MARKET_SOURCE_READ_FAILED", str(exc))
    by_market = {}
    duplicates = set()
    for row in rows:
        market = row.get("market") if isinstance(row, dict) else None
        if market in by_market:
            duplicates.add(market)
        by_market[market] = row
    if duplicates:
        fail("UPBIT_MARKET_DUPLICATE", repr(sorted(duplicates)))
    return manifest, by_market


def build_candidate(
    *,
    first_party_snapshot_dir: Path,
    market_snapshot_dir: Path,
    contract_path: Path = CONTRACT_PATH,
    freeze_path: Path = FREEZE_PATH,
) -> dict:
    contract = load_contract(contract_path, freeze_path)
    first_party = FP.validate_snapshot(
        first_party_snapshot_dir,
        contract_path=FP.CONTRACT_PATH,
        freeze_path=freeze_path,
    )
    market_manifest, market_rows = _load_market_names(market_snapshot_dir)
    if FP.parse_utc(market_manifest["downloaded_at_utc"], "upbit_market.downloaded_at_utc") > FP.parse_utc(
        first_party["available_at"], "first_party.available_at"
    ):
        fail("UPBIT_MARKET_AVAILABLE_AFTER_CANDIDATE_EVIDENCE", market_manifest["downloaded_at_utc"])
    evidence_by_market = {row["market"]: row for row in first_party["sources"]}
    if set(evidence_by_market) != {row["market"] for row in contract["assets"]}:
        fail("FIRST_PARTY_SCOPE_MISMATCH", repr(sorted(evidence_by_market)))

    mappings = {}
    taxonomy_records = []
    evidence = []
    for expected in sorted(contract["assets"], key=lambda row: row["market"]):
        market = expected["market"]
        official = market_rows.get(market)
        if not isinstance(official, dict):
            fail("UPBIT_MARKET_MISSING", market)
        for key in ("korean_name", "english_name"):
            if official.get(key) != expected[key]:
                fail("UPBIT_MARKET_NAME_MISMATCH", f"{market}:{key}:{official.get(key)!r}")
        source = evidence_by_market[market]
        if source.get("canonical_asset_id") != expected["canonical_asset_id"]:
            fail("FIRST_PARTY_CANONICAL_ID_MISMATCH", market)
        mappings[market] = expected["canonical_asset_id"]
        taxonomy_records.append({
            "canonical_asset_id": expected["canonical_asset_id"],
            "category": expected["category"],
            "effective_from": first_party["available_at"][:10],
            "effective_to": None,
            "reason": (
                f"Upbit public listing binds {market} to korean_name={expected['korean_name']} and "
                f"english_name={expected['english_name']}; first-party project evidence is hash-bound "
                f"under {source['validated_authority_domain']}."
            ),
        })
        evidence.append({
            "market": market,
            "canonical_asset_id": expected["canonical_asset_id"],
            "upbit_listing": {
                "source_url": MARKET.load_contract()["market_all_endpoint"],
                "response_sha256": market_manifest["checksums"][MARKET.load_contract()["market_all_raw_file"]],
                "available_at": market_manifest["downloaded_at_utc"],
                "korean_name": official["korean_name"],
                "english_name": official["english_name"],
            },
            "project_first_party": {
                key: source[key]
                for key in (
                    "source_type", "source_url", "effective_url",
                    "validated_authority_domain", "content_sha256",
                    "observed_at", "available_at", "source_published_at",
                )
            },
            "collision_status": "NO_COLLISION_WITHIN_BOUNDED_EIGHT",
            "verdict": "VERIFIED_CANDIDATE_PENDING_EXACT_HASH_APPROVAL",
        })

    proposed_registry = {
        "registry_version": "upbit_asset_identity_registry/v2-candidate",
        "approval_status": "PENDING_EXACT_HASH_CIO_APPROVAL",
        "scope": contract["scope"],
        "effective_from": first_party["available_at"][:10],
        "unknown_market_policy": contract["unknown_market_policy"],
        "mappings": dict(sorted(mappings.items())),
        "authority": contract["authority"],
    }
    proposed_taxonomy = {
        "policy_version": "upbit-exclusion-taxonomy/v2-candidate",
        "approval_status": "PENDING_EXACT_HASH_CIO_APPROVAL",
        "scope": contract["scope"],
        "effective_from": first_party["available_at"][:10],
        "eligible_category": contract["eligible_category"],
        "excluded_categories": [],
        "unknown_asset_policy": contract["unknown_asset_policy"],
        "records": sorted(taxonomy_records, key=lambda row: row["canonical_asset_id"]),
        "authority": contract["authority"],
    }
    packet = {
        "schema_version": "upbit_paper_identity_hardening_candidate/1",
        "review_status": contract["review_status"],
        "generated_at": first_party["available_at"],
        "evaluation_as_of": first_party["available_at"][:10],
        "scope": contract["scope"],
        "upstream": {
            "first_party_snapshot_path": str(Path(first_party_snapshot_dir)),
            "first_party_manifest_sha256": file_sha256(Path(first_party_snapshot_dir) / "_manifest.json"),
            "first_party_capture_id": first_party["capture_id"],
            "upbit_market_snapshot_path": str(Path(market_snapshot_dir)),
            "upbit_market_manifest_sha256": file_sha256(Path(market_snapshot_dir) / "_manifest.json"),
            "upbit_market_available_at": market_manifest["downloaded_at_utc"],
            "governance_freeze_sha256": file_sha256(freeze_path),
        },
        "evidence": evidence,
        "proposed_registry": proposed_registry,
        "proposed_registry_payload_sha256": payload_sha256(proposed_registry),
        "proposed_taxonomy": proposed_taxonomy,
        "proposed_taxonomy_payload_sha256": payload_sha256(proposed_taxonomy),
        "consumer_file_sha256": file_sha256(CONSUMER_PATH),
        "consumer_contract_sha256": file_sha256(contract_path),
        "exact_hash_cio_approval_present": False,
        "release_ready": False,
        "authority": contract["authority"],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    validate_candidate(packet, contract_path=contract_path)
    return packet


def validate_candidate(packet: dict, *, contract_path: Path = CONTRACT_PATH) -> None:
    contract = load_contract(contract_path)
    stored = packet.get("payload_sha256")
    if stored != payload_sha256({key: value for key, value in packet.items() if key != "payload_sha256"}):
        fail("PACKET_SELF_HASH_MISMATCH", repr(stored))
    if packet.get("review_status") != contract["review_status"]:
        fail("PACKET_REVIEW_STATUS_INVALID", repr(packet.get("review_status")))
    if packet.get("release_ready") is not False or packet.get("exact_hash_cio_approval_present") is not False:
        fail("PACKET_PREMATURE_RELEASE", "approval must remain absent")
    if packet.get("authority") != contract["authority"] or any(value is not False for value in packet["authority"].values()):
        fail("PACKET_AUTHORITY_INVALID", repr(packet.get("authority")))
    if packet.get("consumer_file_sha256") != file_sha256(CONSUMER_PATH):
        fail("CONSUMER_FILE_HASH_MISMATCH", repr(packet.get("consumer_file_sha256")))
    if packet.get("consumer_contract_sha256") != file_sha256(contract_path):
        fail("CONSUMER_CONTRACT_HASH_MISMATCH", repr(packet.get("consumer_contract_sha256")))
    registry = packet.get("proposed_registry") or {}
    taxonomy = packet.get("proposed_taxonomy") or {}
    if packet.get("proposed_registry_payload_sha256") != payload_sha256(registry):
        fail("REGISTRY_HASH_MISMATCH", "proposed_registry")
    if packet.get("proposed_taxonomy_payload_sha256") != payload_sha256(taxonomy):
        fail("TAXONOMY_HASH_MISMATCH", "proposed_taxonomy")
    expected_markets = sorted(row["market"] for row in contract["assets"])
    if sorted(registry.get("mappings") or {}) != expected_markets:
        fail("REGISTRY_SCOPE_INVALID", repr(registry.get("mappings")))
    mapping_values = list(registry["mappings"].values())
    if len(mapping_values) != len(set(mapping_values)):
        fail("REGISTRY_CANONICAL_COLLISION", repr(mapping_values))
    taxonomy_ids = [row.get("canonical_asset_id") for row in taxonomy.get("records") or []]
    if sorted(taxonomy_ids) != sorted(mapping_values) or len(taxonomy_ids) != len(set(taxonomy_ids)):
        fail("TAXONOMY_SCOPE_OR_COLLISION_INVALID", repr(taxonomy_ids))
