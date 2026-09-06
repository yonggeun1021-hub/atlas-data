"""Build a non-publishing GAM membership preview from explicit taxonomy refs.

This is the missing construction step before the existing source-binding
validator. A structural preview is never permission to populate a live master.
The disjoint source registries remain unresolved; no source label is inferred.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universe import global_asset_master as GAM

AssetMasterError = GAM.AssetMasterError
SCHEMA = "global_asset_master_theme_ingestion_preview/1"
REQUEST_FIELDS = set(GAM.BINDING_REFERENCE_FIELDS) | {"gam_source_identity"}
SOURCE_FIELDS = {
    "source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"
}
DERIVED_RECORD_FIELDS = {
    "active_aliases", "active_memberships", "universe_approved",
    "investable_eligible", "stage_transition",
}


def _requests(values):
    if not isinstance(values, list) or not values:
        raise AssetMasterError("INGESTION_REQUESTS_EMPTY_OR_INVALID")
    result, seen = [], set()
    for index, value in enumerate(values):
        if not isinstance(value, dict) or set(value) != REQUEST_FIELDS:
            raise AssetMasterError(f"INGESTION_REQUEST_FIELDS_MISMATCH:{index}")
        reference = GAM._theme_binding_reference(
            {key: value[key] for key in GAM.BINDING_REFERENCE_FIELDS}, index
        )
        source = value["gam_source_identity"]
        if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
            raise AssetMasterError(f"INGESTION_SOURCE_FIELDS_MISMATCH:{index}")
        # One target per call. Multiple sources/roles require an explicit
        # upstream selection, not last-writer-wins or silent deduplication.
        key = (reference["asset_id"], reference["gam_membership_id"])
        if key in seen:
            raise AssetMasterError(f"INGESTION_TARGET_DUPLICATE:{key}")
        seen.add(key)
        result.append({**reference, "gam_source_identity": copy.deepcopy(source)})
    return sorted(result, key=lambda row: tuple(row[k] for k in GAM.BINDING_REFERENCE_FIELDS))


def _master_input(packet):
    return {
        "schema_version": GAM.INPUT_SCHEMA_VERSION,
        "master_id": packet["master_id"],
        "as_of_date": packet["as_of_date"],
        "records": [
            {k: copy.deepcopy(v) for k, v in row.items() if k not in DERIVED_RECORD_FIELDS}
            for row in packet["records"]
        ],
    }


def build_theme_ingestion_preview(
    master_source, taxonomy_source, requests, *, trusted_commit,
    authority_registry_path=None,
):
    """Construct explicit memberships in memory, then verify both originals.

    ``gam_source_identity`` is a caller proposal, not a ratified conversion of
    the taxonomy's retrieval-channel label. The original taxonomy identity,
    every evidence row and role, and the existing unresolved comparison are
    retained. No files, configurations, registries or original objects change.
    """
    if not isinstance(trusted_commit, str) or GAM.TRUSTED_COMMIT_RE.fullmatch(trusted_commit) is None:
        raise AssetMasterError("BINDING_TRUSTED_COMMIT_INVALID")
    selections = _requests(requests)
    contract = GAM.load_contract()
    master = GAM._validated_master(copy.deepcopy(master_source), contract)
    TT = GAM._load_theme_taxonomy()
    registry = {} if authority_registry_path is None else {
        "authority_registry_path": Path(authority_registry_path)
    }
    try:
        taxonomy = TT.build_packet(
            copy.deepcopy(taxonomy_source), trusted_commit=trusted_commit, **registry
        )
    except TT.ThemeTaxonomyError as exc:
        raise AssetMasterError(f"TAXONOMY_SOURCE_INVALID:{exc}") from exc

    proposed = _master_input(master)
    records = {row["asset_id"]: row for row in proposed["records"]}
    members = {row["membership_id"]: row for row in taxonomy["memberships"]}
    references, retained, additions, unchanged = [], [], 0, 0
    for selection in selections:
        reference = {key: selection[key] for key in GAM.BINDING_REFERENCE_FIELDS}
        references.append(reference)
        record = records.get(selection["asset_id"])
        member = members.get(selection["taxonomy_membership_id"])
        if record is None:
            raise AssetMasterError(f"GAM_ASSET_NOT_FOUND:{selection['asset_id']}")
        if member is None:
            raise AssetMasterError(f"TAXONOMY_MEMBERSHIP_NOT_FOUND:{selection['taxonomy_membership_id']}")
        # The existing binding validator will reject asset/market/theme/source
        # mismatches; these candidates are never returned as an accepted master.
        row = {
            "membership_type": "THEME",
            "membership_id": selection["gam_membership_id"],
            "valid_from": member["valid_from"],
            "valid_to": member["valid_to"],
            "source_identity": copy.deepcopy(selection["gam_source_identity"]),
        }
        if row in record["memberships"]:
            unchanged += 1
        else:
            record["memberships"].append(row)
            additions += 1
        retained.append(copy.deepcopy(member))

    # Includes all legacy alias/membership/cross-record collision checks.
    candidate = GAM.build_master(proposed, contract)
    binding = GAM.validate_theme_source_binding(
        candidate, copy.deepcopy(taxonomy_source), references,
        trusted_commit=trusted_commit, **registry,
    )
    failures = sorted({
        reason for row in binding["bindings"] for reason in row["failure_reasons"]
    })
    blocked = bool(failures)
    authority = copy.deepcopy(master["authority"])
    authority["master_population_authorized"] = False
    payload = {
        "schema_version": SCHEMA,
        "status": "BLOCKED" if blocked else "STRUCTURAL_PREVIEW",
        "change": None if blocked else ("APPEND" if additions else "NO_CHANGE"),
        "addition_count": 0 if blocked else additions,
        "unchanged_count": 0 if blocked else unchanged,
        "candidate_master": None if blocked else candidate,
        "binding_report": binding,
        "requests": selections,
        "taxonomy_memberships": retained,
        "input_digests": {
            "master_source_sha256": GAM.payload_sha256(master_source),
            "taxonomy_source_sha256": GAM.payload_sha256(taxonomy_source),
            "requests_sha256": GAM.payload_sha256(selections),
            "original_master_payload_sha256": master["payload_sha256"],
            "taxonomy_payload_sha256": taxonomy["payload_sha256"],
            "trusted_commit": trusted_commit,
        },
        "failure_reasons": failures,
        "authority": authority,
        "unresolved_boundaries": copy.deepcopy(binding["unresolved_boundaries"]),
    }
    payload["payload_sha256"] = GAM.payload_sha256(payload)
    return payload


def validate_theme_ingestion_preview(
    preview, master_source, taxonomy_source, requests, *, trusted_commit,
    authority_registry_path=None,
):
    """Recompute the candidate and binding; a self-rehashed edit is not proof."""
    expected = build_theme_ingestion_preview(
        master_source, taxonomy_source, requests, trusted_commit=trusted_commit,
        authority_registry_path=authority_registry_path,
    )
    if GAM.canonical_json(preview) != GAM.canonical_json(expected):
        raise AssetMasterError("INGESTION_PREVIEW_DERIVATION_MISMATCH")
    return copy.deepcopy(expected)
