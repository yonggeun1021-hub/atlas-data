#!/usr/bin/env python3
"""Release the exact CIO-approved eight-market Upbit PAPER identity slice.

This module grants classification for Atlas's internal PAPER ledger only.  It
does not grant an exchange order, withdrawal, Production, Trading, or REAL
authority and it cannot create an entry signal by itself.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APPROVAL_PATH = ROOT / "evidence/crypto/upbit/identity/approvals/2026-08-30/p3-12-paper-eight-exact-hash-v2.json"
CANDIDATE_PATH = ROOT / "data/observations/upbit_paper_identity_hardening_candidate/2026-08-30/20260830T111117Z/packet.json"
REGISTRY_PATH = ROOT / "config/upbit_asset_identity_registry.json"
TAXONOMY_PATH = ROOT / "config/upbit_exclusion_taxonomy.json"
FREEZE_PATH = ROOT / "config/upbit_identity_taxonomy_governance_freeze.json"

EXPECTED_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-LINK", "KRW-SHIB",
    "KRW-SOL", "KRW-SUI", "KRW-WLD", "KRW-XRP",
]
EXPECTED_APPROVAL_TEXT = (
    "정정된 위 해시와 BTC·ETH·LINK·SHIB·SOL·SUI·WLD·XRP 8개 종목의 Atlas 내부 PAPER "
    "가상매매 전용 해제를 승인합니다. Upbit 실제 주문·출금·REAL·Production·Trading 권한은 승인하지 않습니다."
)
PAPER_SCOPE_KEYS = {
    "atlas_internal_paper_virtual_buy",
    "atlas_internal_paper_virtual_hold",
    "atlas_internal_paper_virtual_stop_loss",
    "atlas_internal_paper_virtual_take_profit",
    "atlas_internal_paper_virtual_sell",
    "atlas_internal_paper_ledger",
}
FORBIDDEN_AUTHORITY_KEYS = {
    "upbit_exchange_order_authorized",
    "upbit_withdrawal_authorized",
    "production_authorized",
    "real_capital_authorized",
    "trading_authorized",
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CANDIDATE = _load_module(
    "upbit_paper_identity_candidate_for_release",
    ROOT / "identity/upbit_paper_identity_hardening_candidate.py",
)


class ReleaseError(ValueError):
    """Fail-closed exact approval or release-document violation."""


def fail(code: str, detail: str) -> None:
    raise ReleaseError(f"{code}:{detail}")


def _read_json(path: Path) -> dict:
    if Path(path).is_symlink():
        fail("JSON_SYMLINK_FORBIDDEN", str(path))
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_READ_FAILED", f"{path}:{exc}")
    if not isinstance(value, dict):
        fail("JSON_ROOT_INVALID", str(path))
    return value


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _parse_utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        fail(code, repr(value))
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail(code, repr(value))
    if parsed.tzinfo is None:
        fail(code, repr(value))
    return parsed.astimezone(dt.timezone.utc)


def validate_approval(
    approval_path: Path = APPROVAL_PATH,
    candidate_path: Path = CANDIDATE_PATH,
) -> tuple[dict, dict]:
    approval = _read_json(approval_path)
    candidate = _read_json(candidate_path)
    CANDIDATE.validate_candidate(candidate)

    if approval.get("schema_version") != "upbit_paper_identity_exact_hash_approval/1":
        fail("APPROVAL_SCHEMA_INVALID", repr(approval.get("schema_version")))
    if approval.get("approval_status") != "RATIFIED" or approval.get("ratified_by") != "CIO_USER":
        fail("APPROVAL_STATUS_INVALID", repr(approval.get("approval_status")))
    if (approval.get("decision_evidence") or {}).get("approval_text") != EXPECTED_APPROVAL_TEXT:
        fail("APPROVAL_TEXT_INVALID", repr((approval.get("decision_evidence") or {}).get("approval_text")))
    if approval.get("approved_markets") != EXPECTED_MARKETS:
        fail("APPROVAL_MARKET_SCOPE_INVALID", repr(approval.get("approved_markets")))
    paper_scope = approval.get("approved_scope")
    if not isinstance(paper_scope, dict) or set(paper_scope) != PAPER_SCOPE_KEYS or any(value is not True for value in paper_scope.values()):
        fail("APPROVAL_PAPER_SCOPE_INVALID", repr(paper_scope))
    authority = approval.get("authority")
    if not isinstance(authority, dict) or set(authority) != FORBIDDEN_AUTHORITY_KEYS or any(value is not False for value in authority.values()):
        fail("APPROVAL_AUTHORITY_INVALID", repr(authority))

    pin = approval.get("candidate") or {}
    expected_pins = {
        "path": str(candidate_path.relative_to(ROOT)),
        "schema_version": candidate["schema_version"],
        "file_sha256": file_sha256(candidate_path),
        "payload_sha256": candidate["payload_sha256"],
        "registry_payload_sha256": candidate["proposed_registry_payload_sha256"],
        "taxonomy_payload_sha256": candidate["proposed_taxonomy_payload_sha256"],
        "consumer_file_sha256": candidate["consumer_file_sha256"],
        "candidate_builder_file_sha256": candidate["candidate_builder_file_sha256"],
        "consumer_contract_sha256": candidate["consumer_contract_sha256"],
    }
    if pin != expected_pins:
        fail("APPROVAL_EXACT_PIN_MISMATCH", repr(pin))
    if _parse_utc(approval.get("ratified_at_utc"), "APPROVAL_TIME_INVALID") <= _parse_utc(
        candidate.get("generated_at"), "CANDIDATE_TIME_INVALID"
    ):
        fail("APPROVAL_PRECEDES_CANDIDATE", approval["ratified_at_utc"])
    if sorted(candidate["proposed_registry"]["mappings"]) != EXPECTED_MARKETS:
        fail("CANDIDATE_MARKET_SCOPE_INVALID", repr(candidate["proposed_registry"]["mappings"]))
    if any(value is not False for value in candidate["authority"].values()):
        fail("CANDIDATE_AUTHORITY_INVALID", repr(candidate["authority"]))
    return copy.deepcopy(approval), copy.deepcopy(candidate)


def build_release_documents(
    approval_path: Path = APPROVAL_PATH,
    candidate_path: Path = CANDIDATE_PATH,
    freeze_path: Path = FREEZE_PATH,
) -> dict:
    approval, candidate = validate_approval(approval_path, candidate_path)
    proposed_registry = candidate["proposed_registry"]
    proposed_taxonomy = candidate["proposed_taxonomy"]
    approval_ref = str(approval_path.relative_to(ROOT))
    approval_sha = file_sha256(approval_path)
    ratified_at = approval["ratified_at_utc"]
    decision_url = approval["decision_evidence"]["notion_page"]
    source_manifest = Path(candidate["upstream"]["first_party_snapshot_path"]) / "_manifest.json"

    registry = {
        "schema_version": 1,
        "registry_version": "upbit_asset_identity_registry/v1",
        "approval_status": "RATIFIED",
        "previous_approval_status": "PENDING_GOVERNANCE_RESOLUTION",
        "scope": proposed_registry["scope"],
        "effective_from": candidate["evaluation_as_of"],
        "ratified_at_utc": ratified_at,
        "ratified_by": "CIO_USER",
        "decision_evidence_url": decision_url,
        "approval_evidence_ref": approval_ref,
        "approval_evidence_sha256": approval_sha,
        "approved_candidate_payload_sha256": candidate["proposed_registry_payload_sha256"],
        "source_candidate_packet": {
            "path": str(candidate_path.relative_to(ROOT)),
            "file_sha256": file_sha256(candidate_path),
            "payload_sha256": candidate["payload_sha256"],
            "snapshot_date": candidate["snapshot_date"],
            "evaluation_as_of": candidate["evaluation_as_of"],
            "review_status": candidate["review_status"],
        },
        "source_identity_evidence": {
            "path": str(source_manifest),
            "file_sha256": candidate["upstream"]["first_party_manifest_sha256"],
            "researched_at": candidate["evaluation_as_of"],
        },
        "unknown_market_policy": proposed_registry["unknown_market_policy"],
        "mappings": copy.deepcopy(proposed_registry["mappings"]),
        "authority": copy.deepcopy(proposed_registry["authority"]),
    }
    taxonomy = {
        "schema_version": 1,
        "policy_version": "upbit_exclusion_taxonomy/v1",
        "approval_status": "RATIFIED",
        "previous_approval_status": "PENDING_GOVERNANCE_RESOLUTION",
        "scope": proposed_taxonomy["scope"],
        "effective_from": candidate["evaluation_as_of"],
        "ratified_at_utc": ratified_at,
        "ratified_by": "CIO_USER",
        "decision_evidence_url": decision_url,
        "approval_evidence_ref": approval_ref,
        "approval_evidence_sha256": approval_sha,
        "approved_candidate_payload_sha256": candidate["proposed_taxonomy_payload_sha256"],
        "source_name": "upbit_first_party_exact_hash_identity_candidate",
        "eligible_category": proposed_taxonomy["eligible_category"],
        "excluded_categories": copy.deepcopy(proposed_taxonomy["excluded_categories"]),
        "unknown_asset_policy": proposed_taxonomy["unknown_asset_policy"],
        "records": copy.deepcopy(proposed_taxonomy["records"]),
        "authority": copy.deepcopy(proposed_taxonomy["authority"]),
    }
    freeze = _read_json(freeze_path)
    freeze["resolution_status"] = "RATIFIED_BY_EXPLICIT_CIO_DECISION"
    freeze["resolved_at_utc"] = ratified_at
    freeze["reason"] = (
        "The exact eight-market first-party identity and taxonomy candidate was approved for Atlas internal PAPER "
        "virtual trading only; historical frozen records remain preserved and actual Upbit authority remains closed."
    )
    freeze["paper_classification_scope_approved"] = True
    freeze["released_paper_markets"] = copy.deepcopy(EXPECTED_MARKETS)
    freeze["approval_resolution"] = {
        "approval_evidence_ref": approval_ref,
        "approval_evidence_sha256": approval_sha,
        "candidate_packet_path": str(candidate_path.relative_to(ROOT)),
        "candidate_packet_file_sha256": file_sha256(candidate_path),
        "candidate_packet_payload_sha256": candidate["payload_sha256"],
        "registry_candidate_payload_sha256": candidate["proposed_registry_payload_sha256"],
        "taxonomy_candidate_payload_sha256": candidate["proposed_taxonomy_payload_sha256"],
        "consumer_file_sha256": candidate["consumer_file_sha256"],
        "approved_scope": "ATLAS_INTERNAL_PAPER_VIRTUAL_TRADING_EIGHT_ONLY",
    }
    validate_release_documents(registry, taxonomy, freeze, approval, candidate)
    return {"registry": registry, "taxonomy": taxonomy, "freeze": freeze}


def validate_release_documents(registry: dict, taxonomy: dict, freeze: dict, approval: dict, candidate: dict) -> None:
    if registry.get("approval_status") != "RATIFIED" or taxonomy.get("approval_status") != "RATIFIED":
        fail("RELEASE_NOT_RATIFIED", "registry_or_taxonomy")
    if registry.get("mappings") != candidate["proposed_registry"]["mappings"]:
        fail("RELEASE_REGISTRY_MAPPING_MISMATCH", repr(registry.get("mappings")))
    if taxonomy.get("records") != candidate["proposed_taxonomy"]["records"]:
        fail("RELEASE_TAXONOMY_RECORD_MISMATCH", repr(taxonomy.get("records")))
    if sorted(registry.get("mappings") or {}) != EXPECTED_MARKETS:
        fail("RELEASE_MARKET_SCOPE_INVALID", repr(registry.get("mappings")))
    if freeze.get("resolution_status") != "RATIFIED_BY_EXPLICIT_CIO_DECISION":
        fail("RELEASE_GOVERNANCE_STATUS_INVALID", repr(freeze.get("resolution_status")))
    if freeze.get("released_paper_markets") != EXPECTED_MARKETS:
        fail("RELEASE_GOVERNANCE_SCOPE_INVALID", repr(freeze.get("released_paper_markets")))
    approval_sha = file_sha256(APPROVAL_PATH)
    if any(doc.get("approval_evidence_sha256") != approval_sha for doc in (registry, taxonomy)):
        fail("RELEASE_APPROVAL_FILE_HASH_MISMATCH", approval_sha)
    for label, authority in (
        ("registry", registry.get("authority")),
        ("taxonomy", taxonomy.get("authority")),
        ("freeze", freeze.get("authority")),
    ):
        if not isinstance(authority, dict) or not authority or any(value is not False for value in authority.values()):
            fail("RELEASE_AUTHORITY_OPEN", f"{label}:{authority!r}")
    if any(value is not False for value in approval["authority"].values()):
        fail("RELEASE_APPROVAL_AUTHORITY_OPEN", repr(approval["authority"]))


def validate_committed_release() -> None:
    approval, candidate = validate_approval()
    expected = build_release_documents()
    actual = {
        "registry": _read_json(REGISTRY_PATH),
        "taxonomy": _read_json(TAXONOMY_PATH),
        "freeze": _read_json(FREEZE_PATH),
    }
    if actual != expected:
        fail("COMMITTED_RELEASE_DOCUMENT_MISMATCH", "regenerate from approved exact candidate")
    validate_release_documents(actual["registry"], actual["taxonomy"], actual["freeze"], approval, candidate)


if __name__ == "__main__":
    validate_committed_release()
    print("UPBIT_PAPER_IDENTITY_EXACT_HASH_RELEASE_OK")
