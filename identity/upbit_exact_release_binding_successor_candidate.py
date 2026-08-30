#!/usr/bin/env python3
"""P3-12-GOV-05: append-only successor candidate for the runtime
exact-approval-binding change.

This documents the exact hashes this branch's identity/taxonomy binding
change touches, for a FUTURE, separate, explicit CIO re-approval. It grants
no authority by itself (``release_ready``/``exact_hash_cio_approval_present``
stay ``False``) and never re-collects the first-party identity evidence PR
#494 already captured -- it only references that evidence and the prior
approval/candidate by their exact, already-committed hashes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_APPROVAL_PATH = (
    ROOT / "evidence/crypto/upbit/identity/approvals/2026-08-30/p3-12-paper-eight-exact-hash-v2.json"
)
BASE_CANDIDATE_PATH = (
    ROOT / "data/observations/upbit_paper_identity_hardening_candidate/2026-08-30/20260830T111117Z/packet.json"
)
BASE_CANDIDATE_BUILDER_PATH = ROOT / "identity" / "upbit_paper_identity_hardening_candidate.py"
REGISTRY_PATH = ROOT / "config" / "upbit_asset_identity_registry.json"
TAXONOMY_PATH = ROOT / "config" / "upbit_exclusion_taxonomy.json"
CONSUMER_PATH = ROOT / "universe" / "upbit_tradeable_universe.py"
VALIDATOR_PATH = ROOT / "governance" / "upbit_exact_release_binding.py"
BINDING_CONTRACT_PATH = ROOT / "config" / "upbit_exact_release_binding_contract.json"
THIS_BUILDER_PATH = Path(__file__).resolve()
FIRST_PARTY_EVIDENCE_ROOT = "evidence/crypto/upbit/identity/first_party/2026-08-30/20260830T111117Z"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_successor_candidate(*, generated_at: str) -> dict:
    registry = _read_json(REGISTRY_PATH)
    taxonomy = _read_json(TAXONOMY_PATH)
    packet = {
        "schema_version": "upbit_exact_release_binding_successor_candidate/1",
        "generated_at": generated_at,
        "scope": "P3_12_GOV_05_RUNTIME_EXACT_APPROVAL_BINDING",
        "review_status": "PENDING_EXACT_HASH_REAPPROVAL",
        "supersedes": {
            "base_approval_evidence": {
                "path": str(BASE_APPROVAL_PATH.relative_to(ROOT)),
                "file_sha256": file_sha256(BASE_APPROVAL_PATH),
            },
            "base_candidate_packet": {
                "path": str(BASE_CANDIDATE_PATH.relative_to(ROOT)),
                "file_sha256": file_sha256(BASE_CANDIDATE_PATH),
            },
            "base_candidate_builder": {
                "path": str(BASE_CANDIDATE_BUILDER_PATH.relative_to(ROOT)),
                "file_sha256": file_sha256(BASE_CANDIDATE_BUILDER_PATH),
            },
        },
        "unchanged_approved_content": {
            "note": (
                "The approved registry/taxonomy CONTENT (exactly the eight "
                "BTC/ETH/LINK/SHIB/SOL/SUI/WLD/XRP mappings and records) is "
                "not modified by this branch -- only the runtime binding "
                "mechanism around it changes."
            ),
            "registry_approved_candidate_payload_sha256": registry.get("approved_candidate_payload_sha256"),
            "taxonomy_approved_candidate_payload_sha256": taxonomy.get("approved_candidate_payload_sha256"),
            "registry_file_sha256": file_sha256(REGISTRY_PATH),
            "taxonomy_file_sha256": file_sha256(TAXONOMY_PATH),
        },
        "changed_consumer": {
            "path": str(CONSUMER_PATH.relative_to(ROOT)),
            "file_sha256": file_sha256(CONSUMER_PATH),
        },
        "new_runtime_validator": {
            "path": str(VALIDATOR_PATH.relative_to(ROOT)),
            "file_sha256": file_sha256(VALIDATOR_PATH),
        },
        "new_binding_contract": {
            "path": str(BINDING_CONTRACT_PATH.relative_to(ROOT)),
            "file_sha256": file_sha256(BINDING_CONTRACT_PATH),
        },
        "this_successor_candidate_builder": {
            "path": str(THIS_BUILDER_PATH.relative_to(ROOT)),
            "file_sha256": file_sha256(THIS_BUILDER_PATH),
        },
        "first_party_evidence_reference": {
            "path": FIRST_PARTY_EVIDENCE_ROOT,
            "note": "Referenced, not re-captured -- see supersedes.base_candidate_packet for the original capture.",
        },
        "release_ready": False,
        "exact_hash_cio_approval_present": False,
        "authority": {
            "identity_authorized": False,
            "taxonomy_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "candidate_promotion_authorized": False,
            "paper_exit_authorized": False,
            "exchange_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "real_capital_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["payload_sha256"] = payload_sha256({key: value for key, value in packet.items() if key != "payload_sha256"})
    return packet


if __name__ == "__main__":
    import sys

    generated_at = sys.argv[1] if len(sys.argv) > 1 else "2026-08-30T00:00:00Z"
    print(json.dumps(build_successor_candidate(generated_at=generated_at), indent=2, sort_keys=True, ensure_ascii=False))
