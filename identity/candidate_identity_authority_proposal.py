#!/usr/bin/env python3
"""Build a non-authoritative CIO review packet for candidate identity gaps.

The packet turns already-committed taxonomy and Kraken catalog evidence into
explicit *proposals*.  It never writes canonical authority configuration and
every proposed row remains ``PROPOSED_UNRATIFIED`` with all execution authority
false.  A mechanically complete proposal is only ready for human identity
review; it is not an identity resolution, candidate-validity decision, or
entry permission.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.candidate_identity_gap_inventory import (
    DEFAULT_OUTPUT as DEFAULT_GAPS,
    DEFAULT_TAXONOMY,
    DIAGNOSTIC_MATCH,
    _load_taxonomy,
    validate_inventory,
)
from identity.candidate_identity_observation import (
    DEFAULT_OUTPUT as DEFAULT_OBSERVATION,
    DEFAULT_REPORT,
)
from identity import canonical_identity as ci


SCHEMA_VERSION = "candidate_identity_authority_proposal/1"
PROPOSAL_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"
COMPLETE = "MECHANICALLY_COMPLETE_FOR_CIO_IDENTITY_REVIEW"
INCOMPLETE = "EVIDENCE_INCOMPLETE_NOT_PROPOSED"
AUTHORITY_ALL_FALSE = {
    "stage_promotion_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class CandidateIdentityAuthorityProposalError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_kraken_capture(root: Path, decision_date: str) -> tuple[dict, dict]:
    try:
        decision = dt.date.fromisoformat(decision_date)
    except ValueError as exc:
        raise CandidateIdentityAuthorityProposalError("DECISION_DATE_INVALID") from exc
    choices = sorted(
        p for p in root.iterdir()
        if p.is_dir() and p.name <= decision.isoformat() and (p / "_manifest.json").is_file()
    )
    if not choices:
        raise CandidateIdentityAuthorityProposalError("KRAKEN_CAPTURE_NOT_AVAILABLE")
    capture = choices[-1]
    manifest_path = capture / "_manifest.json"
    pairs_path = capture / "kraken_asset_pairs.json.gz"
    manifest = json.loads(manifest_path.read_text())
    fetched = dt.datetime.fromisoformat(manifest["fetched_at_utc"].replace("Z", "+00:00"))
    decision_end = dt.datetime.combine(decision, dt.time.max, tzinfo=dt.timezone.utc)
    if fetched > decision_end:
        raise CandidateIdentityAuthorityProposalError("KRAKEN_CAPTURE_FUTURE_DATED")
    raw = gzip.open(pairs_path, "rb").read()
    expected = manifest["raw"]["asset_pairs"]
    if expected["file"] != pairs_path.name or expected["response_sha256"] != hashlib.sha256(raw).hexdigest():
        raise CandidateIdentityAuthorityProposalError("KRAKEN_CAPTURE_HASH_MISMATCH")
    doc = json.loads(raw)
    if doc.get("error") != [] or not isinstance(doc.get("result"), dict):
        raise CandidateIdentityAuthorityProposalError("KRAKEN_CAPTURE_SCHEMA_INVALID")
    lineage = {
        "capture_date": capture.name,
        "fetched_at_utc": manifest["fetched_at_utc"],
        "manifest_path": str(manifest_path.relative_to(ROOT)),
        "manifest_bytes_sha256": _file_sha(manifest_path),
        "asset_pairs_path": str(pairs_path.relative_to(ROOT)),
        "asset_pairs_response_sha256": expected["response_sha256"],
    }
    return doc["result"], lineage


def _proposal(gap: dict, pairs: dict) -> dict:
    diagnostics = gap["provider_pair_diagnostics"]
    if len(diagnostics) != 1:
        return {"candidate_id": gap["candidate_id"], "market": gap["market"], "subject": gap["subject"], "review_status": INCOMPLETE, "reason_codes": ["SOURCE_PAIR_CARDINALITY_UNSUPPORTED"], "proposed_rows": None, "authority": dict(AUTHORITY_ALL_FALSE)}
    diagnostic = diagnostics[0]
    source_id = diagnostic.get("source_asset_id")
    asset = diagnostic.get("taxonomy_canonical_asset_id")
    pair = pairs.get(source_id) if isinstance(source_id, str) else None
    reasons = []
    if diagnostic.get("diagnostic_status") != DIAGNOSTIC_MATCH:
        reasons.append("RATIFIED_TAXONOMY_MATCH_MISSING")
    if not isinstance(pair, dict):
        reasons.append("KRAKEN_PAIR_NOT_IN_EXACT_CAPTURE")
    elif not (pair.get("wsname") == source_id and pair.get("base") == asset and pair.get("quote") == "USD" and pair.get("status") == "online"):
        reasons.append("KRAKEN_PAIR_IDENTITY_FIELDS_MISMATCH")
    if reasons:
        return {"candidate_id": gap["candidate_id"], "market": gap["market"], "subject": gap["subject"], "review_status": INCOMPLETE, "reason_codes": reasons, "proposed_rows": None, "authority": dict(AUTHORITY_ALL_FALSE)}
    issuer = f"CRYPTO:{asset}"
    listing = f"KRAKEN:{asset}-USD:SPOT"
    proposed = {
        "issuer": {"canonical_issuer_id": issuer, "issuer_name_reference": asset},
        "instrument": {"canonical_instrument_id": issuer, "canonical_issuer_id": issuer, "instrument_type": "CRYPTO_ASSET"},
        "listing": {"listing_id": listing, "canonical_instrument_id": issuer, "market": "CRYPTO", "exchange": "KRAKEN", "currency": "USD", "ticker": source_id},
        "source_alias": {"source_name": diagnostic["source_name"], "source_asset_id": source_id, "listing_id": listing},
    }
    return {"candidate_id": gap["candidate_id"], "market": gap["market"], "subject": gap["subject"], "review_status": COMPLETE, "reason_codes": [], "proposal_status": PROPOSAL_STATUS, "proposal_naming_basis": "MECHANICAL_CONVENTION_PROPOSED_UNRATIFIED", "taxonomy_effective_from": diagnostic["taxonomy_effective_from"], "proposed_rows": proposed, "authority": dict(AUTHORITY_ALL_FALSE)}


def _validate_source_gap_inventory(
    gaps: dict,
    taxonomy_path: Path,
    *,
    observation_path: Path,
    report_path: Path,
    authority_path: Path,
    scope_authority_path: Path,
) -> tuple[dict, dict[str, dict]]:
    """Independently rebuild the source inventory from its canonical inputs.

    A packet hash is not provenance: a caller could alter the inventory and
    recompute that hash.  The proposal therefore refuses to consume an
    inventory unless the existing inventory contract can reproduce it from
    the committed Dynamic Clock observation/report, both authority documents,
    and the exact taxonomy bytes.
    """
    try:
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        authority = ci.load_authority(authority_path)
        scope_authority = ci.load_scope_authority(scope_authority_path)
        taxonomy_doc, taxonomy_records = _load_taxonomy(taxonomy_path)
        validate_inventory(
            gaps,
            observation,
            report,
            authority,
            scope_authority,
            taxonomy_doc,
            taxonomy_records,
            taxonomy_bytes_sha256=_file_sha(taxonomy_path),
        )
    except Exception as exc:
        raise CandidateIdentityAuthorityProposalError(
            "SOURCE_GAP_INVENTORY_INDEPENDENT_VALIDATION_FAILED"
        ) from exc
    return taxonomy_doc, taxonomy_records


def build_packet(
    gaps: dict,
    taxonomy_path: Path,
    raw_root: Path,
    *,
    observation_path: Path = DEFAULT_OBSERVATION,
    report_path: Path = DEFAULT_REPORT,
    authority_path: Path = ci.SECURITY_IDENTITY_PATH,
    scope_authority_path: Path = ci.MARKET_ACCOUNT_SCOPE_PATH,
) -> dict:
    if gaps.get("schema_version") != "candidate_identity_gap_inventory/1":
        raise CandidateIdentityAuthorityProposalError("GAP_INVENTORY_SCHEMA_INVALID")
    if gaps.get("policy_boundary", {}).get("authority_rows_created") != 0:
        raise CandidateIdentityAuthorityProposalError("GAP_INVENTORY_AUTHORITY_ESCALATION")
    taxonomy, _ = _validate_source_gap_inventory(
        gaps,
        taxonomy_path,
        observation_path=observation_path,
        report_path=report_path,
        authority_path=authority_path,
        scope_authority_path=scope_authority_path,
    )
    pairs, capture = _load_kraken_capture(raw_root, gaps["decision_date"])
    proposals = sorted((_proposal(gap, pairs) for gap in gaps["identity_gaps"]), key=lambda x: (x["market"], x["subject"], x["candidate_id"]))
    counts = {}
    for row in proposals:
        counts[row["review_status"]] = counts.get(row["review_status"], 0) + 1
    packet = {
        "schema_version": SCHEMA_VERSION,
        "decision_date": gaps["decision_date"],
        "source_gap_inventory_packet_sha256": gaps["packet_sha256"],
        "source_taxonomy": {"path": str(taxonomy_path.relative_to(ROOT)), "bytes_sha256": _file_sha(taxonomy_path), "policy_version": taxonomy["policy_version"], "approval_status": taxonomy["approval_status"]},
        "source_kraken_capture": capture,
        "summary": {"gap_count": len(gaps["identity_gaps"]), "proposal_count": len(proposals), "review_status_counts": dict(sorted(counts.items())), "canonical_authority_rows_created": 0},
        "proposals": proposals,
        "policy_boundary": {"proposal_is_identity_authority": False, "canonical_config_modified": False, "candidate_validity_evaluated": False, "entry_eligibility_evaluated": False, "money_action": "NONE"},
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = _sha(packet)
    return packet


def validate_packet(
    packet: dict,
    gaps: dict,
    taxonomy_path: Path,
    raw_root: Path,
    **source_paths: Path,
) -> dict:
    if packet != build_packet(gaps, taxonomy_path, raw_root, **source_paths):
        raise CandidateIdentityAuthorityProposalError("PROPOSAL_PACKET_MISMATCH")
    return packet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--raw-root", type=Path, default=ROOT / "evidence/crypto/breadth/raw")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/identity/proposals/candidate_identity_authority_proposal.json")
    args = parser.parse_args()
    gaps = json.loads(args.gaps.read_text())
    packet = build_packet(gaps, args.taxonomy, args.raw_root)
    validate_packet(packet, gaps, args.taxonomy, args.raw_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not args.output.exists() or args.output.read_text() != encoded:
        args.output.write_text(encoded)
    print(json.dumps(packet["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
