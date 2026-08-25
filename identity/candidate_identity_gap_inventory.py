#!/usr/bin/env python3
"""Read-only evidence inventory for unresolved candidate identities.

The inventory answers one narrow question: which exact provider identity
pairs are still unresolved by the canonical Identity Authority, and what
already-ratified taxonomy evidence is mechanically adjacent to each gap?

Taxonomy category is never promoted to canonical identity.  In particular,
``eligible_crypto`` does not prove issuer, instrument, listing, investability,
candidate validity, entry eligibility, or a money action.  This module emits
review evidence only; it cannot create or ratify authority rows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity as ci
from identity.candidate_identity_observation import (
    AUTHORITY_ALL_FALSE,
    DEFAULT_OUTPUT as DEFAULT_OBSERVATION,
    DEFAULT_REPORT,
    validate_observation,
)


DEFAULT_TAXONOMY = ROOT / "config/crypto_breadth_exclusion_taxonomy.json"
DEFAULT_OUTPUT = ROOT / "evidence/operational/dynamic_clock/candidate_identity_gap_inventory.json"
SCHEMA_VERSION = "candidate_identity_gap_inventory/1"
SUPPORTED_TAXONOMY_POLICY = "crypto_breadth_exclusion_taxonomy/v2"
DIAGNOSTIC_MATCH = "MECHANICAL_TAXONOMY_SYMBOL_MATCH_DIAGNOSTIC"
DIAGNOSTIC_EXCLUDED = "MECHANICAL_TAXONOMY_EXCLUDED_CATEGORY_DIAGNOSTIC"
DIAGNOSTIC_NO_RECORD = "TAXONOMY_RECORD_NOT_FOUND"
DIAGNOSTIC_UNSUPPORTED_PAIR = "SOURCE_PAIR_NOT_MECHANICALLY_COMPARABLE"


class CandidateIdentityGapInventoryError(ValueError):
    pass


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _load_taxonomy(path: Path) -> tuple[dict, dict[str, dict]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("approval_status") != "RATIFIED":
        raise CandidateIdentityGapInventoryError("TAXONOMY_NOT_RATIFIED")
    if doc.get("policy_version") != SUPPORTED_TAXONOMY_POLICY:
        raise CandidateIdentityGapInventoryError("TAXONOMY_POLICY_VERSION_UNSUPPORTED")
    eligible_category = doc.get("eligible_category")
    excluded_categories = doc.get("excluded_categories")
    if not isinstance(eligible_category, str) or not isinstance(excluded_categories, list):
        raise CandidateIdentityGapInventoryError("TAXONOMY_CATEGORY_VOCABULARY_INVALID")
    allowed_categories = {eligible_category, *excluded_categories}
    records: dict[str, dict] = {}
    for row in doc.get("records", []):
        asset_id = row.get("canonical_asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            raise CandidateIdentityGapInventoryError("TAXONOMY_ASSET_ID_INVALID")
        if asset_id in records:
            raise CandidateIdentityGapInventoryError("TAXONOMY_ASSET_ID_DUPLICATE")
        if row.get("category") not in allowed_categories:
            raise CandidateIdentityGapInventoryError("TAXONOMY_CATEGORY_INVALID")
        effective_from = row.get("effective_from")
        effective_to = row.get("effective_to")
        try:
            start = dt.date.fromisoformat(effective_from)
            end = None if effective_to is None else dt.date.fromisoformat(effective_to)
        except (TypeError, ValueError) as exc:
            raise CandidateIdentityGapInventoryError("TAXONOMY_EFFECTIVE_INTERVAL_INVALID") from exc
        if end is not None and end < start:
            raise CandidateIdentityGapInventoryError("TAXONOMY_EFFECTIVE_INTERVAL_INVALID")
        records[asset_id] = row
    return doc, records


def _taxonomy_diagnostic(pair: dict, decision_date: str, records: dict[str, dict]) -> dict:
    source_name = pair.get("source_name")
    source_asset_id = pair.get("source_asset_id")
    result = {
        "source_name": source_name,
        "source_asset_id": source_asset_id,
        "diagnostic_status": DIAGNOSTIC_UNSUPPORTED_PAIR,
        "taxonomy_canonical_asset_id": None,
        "taxonomy_category": None,
        "taxonomy_effective_from": None,
        "taxonomy_effective_to": None,
        "identity_authority_effect": "NONE",
    }
    if source_name != "kraken_spot_ohlc" or not isinstance(source_asset_id, str):
        return result
    parts = source_asset_id.split("/")
    if len(parts) != 2 or parts[1] != "USD" or not parts[0]:
        return result
    asset_id = parts[0]
    row = records.get(asset_id)
    if row is None:
        result["diagnostic_status"] = DIAGNOSTIC_NO_RECORD
        return result
    effective_from = row.get("effective_from")
    effective_to = row.get("effective_to")
    if not isinstance(effective_from, str) or effective_from > decision_date:
        result["diagnostic_status"] = DIAGNOSTIC_NO_RECORD
        return result
    if effective_to is not None and effective_to < decision_date:
        result["diagnostic_status"] = DIAGNOSTIC_NO_RECORD
        return result
    category = row.get("category")
    result.update({
        "diagnostic_status": (
            DIAGNOSTIC_MATCH if category == "eligible_crypto" else DIAGNOSTIC_EXCLUDED
        ),
        "taxonomy_canonical_asset_id": asset_id,
        "taxonomy_category": category,
        "taxonomy_effective_from": effective_from,
        "taxonomy_effective_to": effective_to,
    })
    return result


def build_inventory(
    observation: dict,
    report: dict,
    authority: dict,
    scope_authority: dict,
    taxonomy_doc: dict,
    taxonomy_records: dict[str, dict],
    *,
    taxonomy_bytes_sha256: str,
) -> dict:
    validate_observation(observation, report, authority, scope_authority)
    decision_date = observation["decision_date"]
    gaps = []
    for candidate in observation["observations"]:
        if candidate["identity"]["status"] == ci.RESOLVED:
            continue
        pair_diagnostics = [
            _taxonomy_diagnostic(pair, decision_date, taxonomy_records)
            for pair in candidate["source_pair_observations"]
        ]
        gaps.append({
            "candidate_id": candidate["candidate_id"],
            "market": candidate["market"],
            "subject": candidate["subject"],
            "identity_status": candidate["identity"]["status"],
            "provider_pair_diagnostics": pair_diagnostics,
            "authority_record_status": "PROPOSED_UNRATIFIED_NOT_CREATED",
            "boundary": "EVIDENCE_REVIEW_CANDIDATE_ONLY",
            "authority": dict(AUTHORITY_ALL_FALSE),
        })
    gaps.sort(key=lambda row: (row["market"], row["subject"], row["candidate_id"]))
    diagnostic_counts: dict[str, int] = {}
    for row in gaps:
        for pair in row["provider_pair_diagnostics"]:
            status = pair["diagnostic_status"]
            diagnostic_counts[status] = diagnostic_counts.get(status, 0) + 1
    payload = {
        "schema_version": SCHEMA_VERSION,
        "decision_date": decision_date,
        "source_candidate_identity_observation_sha256": observation["packet_sha256"],
        "source_dynamic_clock_report_canonical_sha256": (
            observation["source_dynamic_clock_report_canonical_sha256"]
        ),
        "source_taxonomy": {
            "policy_version": taxonomy_doc["policy_version"],
            "approval_status": taxonomy_doc["approval_status"],
            "bytes_sha256": taxonomy_bytes_sha256,
        },
        "summary": {
            "candidate_count": observation["summary"]["candidate_count"],
            "identity_resolved_count": observation["summary"]["identity_resolved_count"],
            "identity_gap_count": len(gaps),
            "provider_pair_diagnostic_counts": dict(sorted(diagnostic_counts.items())),
        },
        "identity_gaps": gaps,
        "policy_boundary": {
            "taxonomy_category_is_identity_authority": False,
            "authority_rows_created": 0,
            "candidate_validity_evaluated": False,
            "entry_eligibility_evaluated": False,
            "money_action": "NONE",
        },
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    payload["packet_sha256"] = _sha256(payload)
    return payload


def validate_inventory(
    packet: dict,
    observation: dict,
    report: dict,
    authority: dict,
    scope_authority: dict,
    taxonomy_doc: dict,
    taxonomy_records: dict[str, dict],
    *,
    taxonomy_bytes_sha256: str,
) -> dict:
    expected = build_inventory(
        observation,
        report,
        authority,
        scope_authority,
        taxonomy_doc,
        taxonomy_records,
        taxonomy_bytes_sha256=taxonomy_bytes_sha256,
    )
    if packet != expected:
        raise CandidateIdentityGapInventoryError("IDENTITY_GAP_INVENTORY_MISMATCH")
    if packet["summary"]["candidate_count"] != (
        packet["summary"]["identity_resolved_count"] + packet["summary"]["identity_gap_count"]
    ):
        raise CandidateIdentityGapInventoryError("IDENTITY_GAP_SUMMARY_NOT_RECONCILED")
    if any(value is not False for value in packet["authority"].values()):
        raise CandidateIdentityGapInventoryError("IDENTITY_GAP_AUTHORITY_MUST_BE_FALSE")
    return packet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--observation", type=Path, default=DEFAULT_OBSERVATION)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    observation = json.loads(args.observation.read_text(encoding="utf-8"))
    authority = ci.load_authority()
    scope_authority = ci.load_scope_authority()
    taxonomy_doc, taxonomy_records = _load_taxonomy(args.taxonomy)
    taxonomy_sha = hashlib.sha256(args.taxonomy.read_bytes()).hexdigest()
    packet = build_inventory(
        observation,
        report,
        authority,
        scope_authority,
        taxonomy_doc,
        taxonomy_records,
        taxonomy_bytes_sha256=taxonomy_sha,
    )
    validate_inventory(
        packet,
        observation,
        report,
        authority,
        scope_authority,
        taxonomy_doc,
        taxonomy_records,
        taxonomy_bytes_sha256=taxonomy_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not args.output.exists() or args.output.read_text(encoding="utf-8") != encoded:
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps(packet["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
