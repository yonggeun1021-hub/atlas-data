#!/usr/bin/env python3
"""Audit proposed identity rows for cross-row contradictions without ratifying them.

This layer consumes the independently reproducible candidate identity proposal
packet.  It checks whether a proposed identifier is assigned contradictory
payloads, or whether the same provider alias is assigned to different listings.
It never writes canonical authority configuration and never turns a coherent
proposal into an approved identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.candidate_identity_authority_proposal import (
    AUTHORITY_ALL_FALSE,
    COMPLETE,
    DEFAULT_GAPS,
    DEFAULT_TAXONOMY,
    INCOMPLETE,
    PROPOSAL_STATUS,
    validate_packet as validate_proposal_packet,
)


SCHEMA_VERSION = "candidate_identity_authority_review_inventory/1"
COHERENT = "MECHANICALLY_COHERENT_FOR_CIO_REVIEW"
CONFLICT = "MECHANICAL_CROSS_ROW_CONFLICT_REQUIRES_REVIEW"
EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE_NOT_REVIEWABLE"
DEFAULT_PROPOSAL = ROOT / "evidence/identity/proposals/candidate_identity_authority_proposal.json"
DEFAULT_OUTPUT = ROOT / "evidence/identity/proposals/candidate_identity_authority_review_inventory.json"


class CandidateIdentityAuthorityReviewInventoryError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _conflicts(proposals: list[dict]) -> dict[str, set[str]]:
    """Return conflict codes per candidate; never chooses a winning row."""
    conflicts: dict[str, set[str]] = {row["candidate_id"]: set() for row in proposals}
    indexes: dict[str, dict[object, tuple[bytes, str]]] = {
        "ISSUER_ID_CONTRADICTORY_PAYLOAD": {},
        "INSTRUMENT_ID_CONTRADICTORY_PAYLOAD": {},
        "LISTING_ID_CONTRADICTORY_PAYLOAD": {},
        "SOURCE_ALIAS_CONTRADICTORY_LISTING": {},
    }

    for row in proposals:
        if row.get("review_status") != COMPLETE:
            continue
        candidate_id = row["candidate_id"]
        proposed = row["proposed_rows"]
        checks = (
            ("ISSUER_ID_CONTRADICTORY_PAYLOAD", proposed["issuer"]["canonical_issuer_id"], proposed["issuer"]),
            ("INSTRUMENT_ID_CONTRADICTORY_PAYLOAD", proposed["instrument"]["canonical_instrument_id"], proposed["instrument"]),
            ("LISTING_ID_CONTRADICTORY_PAYLOAD", proposed["listing"]["listing_id"], proposed["listing"]),
            (
                "SOURCE_ALIAS_CONTRADICTORY_LISTING",
                (proposed["source_alias"]["source_name"], proposed["source_alias"]["source_asset_id"]),
                {"listing_id": proposed["source_alias"]["listing_id"]},
            ),
        )
        for code, key, payload in checks:
            encoded = _canonical(payload)
            prior = indexes[code].get(key)
            if prior is None:
                indexes[code][key] = (encoded, candidate_id)
            elif prior[0] != encoded:
                conflicts[candidate_id].add(code)
                conflicts[prior[1]].add(code)
    return conflicts


def _validate_source_proposal(
    proposal_packet: dict,
    *,
    proposal_path: Path,
    gaps_path: Path,
    taxonomy_path: Path,
    raw_root: Path,
) -> None:
    try:
        stored = json.loads(proposal_path.read_text())
        if stored != proposal_packet:
            raise CandidateIdentityAuthorityReviewInventoryError("SOURCE_PROPOSAL_BYTES_MISMATCH")
        gaps = json.loads(gaps_path.read_text())
        validate_proposal_packet(stored, gaps, taxonomy_path, raw_root)
    except CandidateIdentityAuthorityReviewInventoryError:
        raise
    except Exception as exc:
        raise CandidateIdentityAuthorityReviewInventoryError(
            "SOURCE_PROPOSAL_INDEPENDENT_VALIDATION_FAILED"
        ) from exc


def build_inventory(
    proposal_packet: dict,
    *,
    proposal_path: Path = DEFAULT_PROPOSAL,
    gaps_path: Path = DEFAULT_GAPS,
    taxonomy_path: Path = DEFAULT_TAXONOMY,
    raw_root: Path = ROOT / "evidence/crypto/breadth/raw",
) -> dict:
    _validate_source_proposal(
        proposal_packet,
        proposal_path=proposal_path,
        gaps_path=gaps_path,
        taxonomy_path=taxonomy_path,
        raw_root=raw_root,
    )
    if proposal_packet.get("schema_version") != "candidate_identity_authority_proposal/1":
        raise CandidateIdentityAuthorityReviewInventoryError("PROPOSAL_SCHEMA_INVALID")
    if proposal_packet.get("summary", {}).get("canonical_authority_rows_created") != 0:
        raise CandidateIdentityAuthorityReviewInventoryError("PROPOSAL_AUTHORITY_ALREADY_CREATED")
    if proposal_packet.get("authority") != AUTHORITY_ALL_FALSE:
        raise CandidateIdentityAuthorityReviewInventoryError("PROPOSAL_AUTHORITY_OPENED")
    proposals = proposal_packet.get("proposals")
    if not isinstance(proposals, list):
        raise CandidateIdentityAuthorityReviewInventoryError("PROPOSAL_ROWS_INVALID")

    conflict_map = _conflicts(proposals)
    rows = []
    counts: dict[str, int] = {}
    conflict_candidates = 0
    for proposal in proposals:
        candidate_id = proposal["candidate_id"]
        codes = sorted(conflict_map[candidate_id])
        if proposal.get("review_status") == INCOMPLETE:
            status = EVIDENCE_INCOMPLETE
            codes = list(proposal.get("reason_codes", []))
        elif proposal.get("review_status") != COMPLETE or proposal.get("proposal_status") != PROPOSAL_STATUS:
            raise CandidateIdentityAuthorityReviewInventoryError("PROPOSAL_REVIEW_BOUNDARY_INVALID")
        elif codes:
            status = CONFLICT
            conflict_candidates += 1
        else:
            status = COHERENT
        counts[status] = counts.get(status, 0) + 1
        rows.append({
            "candidate_id": candidate_id,
            "market": proposal["market"],
            "subject": proposal["subject"],
            "review_status": status,
            "reason_codes": codes,
            "proposal_packet_row_sha256": _sha(proposal),
            "authority": dict(AUTHORITY_ALL_FALSE),
        })

    inventory = {
        "schema_version": SCHEMA_VERSION,
        "decision_date": proposal_packet["decision_date"],
        "source_proposal": {
            "path": str(proposal_path.relative_to(ROOT)),
            "bytes_sha256": _file_sha(proposal_path),
            "packet_sha256": proposal_packet["packet_sha256"],
        },
        "summary": {
            "population_count": len(rows),
            "review_status_counts": dict(sorted(counts.items())),
            "conflict_candidate_count": conflict_candidates,
            "canonical_authority_rows_created": 0,
        },
        "rows": sorted(rows, key=lambda row: (row["market"], row["subject"], row["candidate_id"])),
        "policy_boundary": {
            "mechanical_coherence_is_identity_approval": False,
            "canonical_config_modified": False,
            "candidate_validity_evaluated": False,
            "entry_eligibility_evaluated": False,
            "money_action": "NONE",
        },
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    inventory["packet_sha256"] = _sha(inventory)
    return inventory


def validate_inventory(inventory: dict, proposal_packet: dict, **source_paths: Path) -> dict:
    if inventory != build_inventory(proposal_packet, **source_paths):
        raise CandidateIdentityAuthorityReviewInventoryError("REVIEW_INVENTORY_MISMATCH")
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proposal", type=Path, default=DEFAULT_PROPOSAL)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "evidence/crypto/breadth/raw")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    proposal = json.loads(args.proposal.read_text())
    source_paths = {
        "proposal_path": args.proposal,
        "gaps_path": args.gaps,
        "taxonomy_path": args.taxonomy,
        "raw_root": args.raw_root,
    }
    inventory = build_inventory(proposal, **source_paths)
    validate_inventory(inventory, proposal, **source_paths)
    encoded = json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not args.output.exists() or args.output.read_text() != encoded:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(json.dumps(inventory["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
