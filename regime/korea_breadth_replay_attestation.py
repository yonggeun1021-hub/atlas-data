#!/usr/bin/env python3
"""Validate the approved, sanitized private KRX replay attestation.

The public repository never receives KRX response bodies or per-symbol rows.
It records only the exact private proof identity and the boolean result that
the independently captured source bytes reproduced the already-retained
public aggregate facts.  This attestation does not authorize a Breadth score,
axis promotion, Regime interpretation, or any trading action.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_RELATIVE_PATH = "config/korea_breadth_replay_attestation_contract.json"
SCHEMA_VERSION = "korea_breadth_replay_attestation/1"


class KoreaBreadthReplayAttestationError(ValueError):
    """The sanitized replay attestation does not match the approved proof."""


def fail(code: str, detail: object = "") -> None:
    raise KoreaBreadthReplayAttestationError(f"{code}:{detail}")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": SCHEMA_VERSION,
        "approved_proof": {
            "attestation_relative_path": (
                "data/observations/korea_breadth_replay_attestation/2026-08-26/"
                "run-33089264800-attempt-1/attestation.json"
            ),
            "observation_date": "2026-08-26",
            "private_repository": "yonggeun1021-hub/atlas-private-evidence",
            "private_capture_source_commit_sha": (
                "e415f331bb346849616a9c11d7e83fa73bae2f2d"
            ),
            "private_evidence_commit_sha": (
                "5fbc9283211ffa773f4bcd573020ee5201afd766"
            ),
            "private_manifest_sha256": (
                "e2ca51c2a03c7ed1d0eef50746db3673864e756b506ede3b93fca2dc8f0367e9"
            ),
            "private_workflow_path": (
                ".github/workflows/krx-breadth-raw-capture.yml"
            ),
            "private_workflow_run_id": "33089264800",
            "private_workflow_run_attempt": 1,
            "private_evidence_committed_at": "2026-08-27T15:42:44Z",
            "public_code_commit_sha": (
                "8b9e0414ed94d4485085f6f2e0b67f98b9a7c979"
            ),
            "public_bundle_relative_path": (
                "data/observations/korea_breadth_aggregate/2026-08-26/"
                "run-33049365069-attempt-1"
            ),
            "public_bundle_payload_sha256": (
                "352ad44a23d3e1a57ff7305a68ddbdf30c55bf388260d2eb969e44d43e3a6b38"
            ),
            "packet_link_count": 4,
            "raw_response_count": 8,
        },
        "required_result": {
            "replay_status": "MATCHED",
            "source_response_hashes_all_match": True,
            "stable_aggregate_facts_all_match": True,
            "independent_source_replay_available": True,
        },
        "disclosure_boundary": {
            "raw_response_bodies_public": False,
            "raw_response_hashes_republished": False,
            "per_symbol_rows_public": False,
            "api_key_public": False,
            "sanitized_boolean_attestation_only": True,
        },
        "policy_boundary": {
            "breadth_scoring_policy_ratified": False,
            "axis_promotion_authorized": False,
            "classification_authorized": False,
        },
        "authority": {
            "threshold_authorized": False,
            "regime_score_authorized": False,
            "strategy_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "capital_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def load_contract(path: Path | None = None, *, root: Path | None = None) -> dict:
    root = ROOT if root is None else Path(root)
    path = root / CONTRACT_RELATIVE_PATH if path is None else Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", exc)
    if value != _expected_contract():
        fail("CONTRACT_INVALID", "pinned semantics")
    return copy.deepcopy(value)


def build_attestation(contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    if contract != _expected_contract():
        fail("CONTRACT_INVALID", "pinned semantics")
    packet = {
        "schema_version": SCHEMA_VERSION,
        "proof": copy.deepcopy(contract["approved_proof"]),
        "result": copy.deepcopy(contract["required_result"]),
        "disclosure_boundary": copy.deepcopy(contract["disclosure_boundary"]),
        "policy_boundary": copy.deepcopy(contract["policy_boundary"]),
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_attestation(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    if contract != _expected_contract():
        fail("CONTRACT_INVALID", "pinned semantics")
    if not isinstance(value, dict):
        fail("ATTESTATION_INVALID", "object required")
    expected_keys = {
        "schema_version",
        "proof",
        "result",
        "disclosure_boundary",
        "policy_boundary",
        "authority",
        "payload_sha256",
    }
    if set(value) != expected_keys:
        fail("ATTESTATION_FIELDS_INVALID", sorted(value))
    if value != build_attestation(contract):
        fail("ATTESTATION_NOT_APPROVED", "exact approved proof required")
    return copy.deepcopy(value)


def load_approved_attestation(*, root: Path | None = None) -> dict:
    root = ROOT if root is None else Path(root)
    contract = load_contract(root=root)
    relative = contract["approved_proof"]["attestation_relative_path"]
    path_fragment = Path(relative)
    if path_fragment.is_absolute() or ".." in path_fragment.parts:
        fail("ATTESTATION_PATH_INVALID", relative)
    path = (root / path_fragment).resolve()
    if root.resolve() not in path.parents:
        fail("ATTESTATION_PATH_INVALID", relative)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("ATTESTATION_INVALID", exc)
    return validate_attestation(value, contract)
