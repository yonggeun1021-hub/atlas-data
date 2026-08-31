#!/usr/bin/env python3
"""P3-12-GOV-05: append-only successor candidate for the runtime
exact-approval-binding change -- v2 schema (base candidate exact pins +
explicit code_binding block, not a bare reference-hash list).

This is an immutable proposal artifact, exactly like
``identity/upbit_paper_identity_hardening_candidate.py``'s own candidate
packets: it always declares ``release_ready``/``exact_hash_cio_approval_present``
as ``False`` and never re-collects the first-party identity evidence PR
#494 already captured -- it references the base (v2) candidate and
approval by their exact, already-committed hashes only. A future,
separate, explicit CIO decision would produce a NEW file -- a code
approval evidence document -- that names this successor candidate's own
exact hash; this module never produces that file itself.
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
CONSUMER_PATH = ROOT / "universe" / "upbit_tradeable_universe.py"
VALIDATOR_PATH = ROOT / "governance" / "upbit_exact_release_binding.py"
POLICY_CONTRACT_PATH = ROOT / "config" / "upbit_exact_release_binding_policy_contract.json"
RELEASE_BUILDER_PATH = ROOT / "identity" / "upbit_exact_release_binding_release.py"
THIS_BUILDER_PATH = Path(__file__).resolve()
FIRST_PARTY_EVIDENCE_ROOT = "evidence/crypto/upbit/identity/first_party/2026-08-30/20260830T111117Z"
SCHEMA_VERSION = "upbit_exact_release_binding_successor_candidate/2"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_successor_candidate(*, generated_at: str) -> dict:
    base_candidate = _read_json(BASE_CANDIDATE_PATH)
    packet = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "scope": "P3_12_GOV_05_RUNTIME_EXACT_APPROVAL_BINDING",
        "review_status": "PENDING_EXACT_HASH_REAPPROVAL",
        "base_candidate": {
            "path": str(BASE_CANDIDATE_PATH.relative_to(ROOT)),
            "file_sha256": file_sha256(BASE_CANDIDATE_PATH),
            "payload_sha256": base_candidate["payload_sha256"],
        },
        "base_approval_evidence": {
            "path": str(BASE_APPROVAL_PATH.relative_to(ROOT)),
            "file_sha256": file_sha256(BASE_APPROVAL_PATH),
        },
        "code_binding": {
            "consumer_file": {
                "path": str(CONSUMER_PATH.relative_to(ROOT)),
                "sha256": file_sha256(CONSUMER_PATH),
            },
            "validator_file": {
                "path": str(VALIDATOR_PATH.relative_to(ROOT)),
                "sha256": file_sha256(VALIDATOR_PATH),
            },
            "policy_contract": {
                "path": str(POLICY_CONTRACT_PATH.relative_to(ROOT)),
                "sha256": file_sha256(POLICY_CONTRACT_PATH),
            },
            "release_builder": {
                "path": str(RELEASE_BUILDER_PATH.relative_to(ROOT)),
                "sha256": file_sha256(RELEASE_BUILDER_PATH),
            },
        },
        "this_successor_candidate_builder": {
            "path": str(THIS_BUILDER_PATH.relative_to(ROOT)),
            "file_sha256": file_sha256(THIS_BUILDER_PATH),
        },
        "first_party_evidence_reference": {
            "path": FIRST_PARTY_EVIDENCE_ROOT,
            "note": "Referenced, not re-captured -- see base_candidate for the original capture.",
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
