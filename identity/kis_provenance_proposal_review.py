#!/usr/bin/env python3
"""Fail-closed review for the KIS provenance proposal artifacts.

This module distinguishes three facts that the original draft
conflated: a proposal self-hash is consistent, a citation names an
immutable external git object, and the cited external bytes were
independently reproduced. With no external checkout only the first two
are available and review remains ``REVIEW_INCOMPLETE``. When a separately
obtained checkout is supplied, the sibling exact-git-object resolver
reproduces all official bytes before the provider packet may advance to
``REVIEW_READY_FOR_CIO``. Alias packets remain incomplete while their
instrument-specific evidence is mutable/unpinned.

``REVIEW_READY_FOR_CIO`` is not ratification and cannot mutate either
authority registry. It is only a mechanical-review state for a later,
separate human authority decision.

The reviewer reads Atlas runtime registries and canonical identity from
their real committed modules/files. Callers cannot inject a friendlier
registry or a re-signed in-memory identity document.
"""
from __future__ import annotations

from pathlib import Path

from identity import canonical_identity
from identity.kis_provenance_proposal import (
    AUTHORITY_ALL_FALSE,
    KIS_PINNED_EVIDENCE_MANIFEST,
    PROPOSAL_STATUS,
    SCHEMA_VERSION,
    _FORBIDDEN_STATUS_STRINGS,
    payload_sha256,
    provider_authority_proposal,
    source_alias_proposal_000660,
    source_alias_proposal_005930,
)
from portfolio_risk import portfolio_snapshot_v2
from identity.kis_official_evidence_resolver import (
    KisOfficialEvidenceResolutionError,
    reproduce_kis_official_evidence,
)

_PROPOSAL_FIELDS = {
    "schemaVersion", "proposalId", "reviewAsOf", "proposalStatus", "claim",
    "evidenceLineage", "authority", "canonicalAuthorityConfigMutated", "proposalSha256",
}
_OFFICIAL_REPO = "koreainvestment/open-trading-api"
_OFFICIAL_COMMIT = "b4e6249714418aa57833d1cbbbced39cbcc5b125"
_FORBIDDEN_EMBEDDED_AUTHORITY_KEYS = {
    "approval_status", "approvalStatus", "ratified_at", "ratifiedAt",
    "authority_status", "authorityStatus", "broker_verified", "brokerVerified",
}


class KisProvenanceProposalReviewError(ValueError):
    pass


def _reject_forbidden_authority(proposal: dict) -> None:
    status = proposal.get("proposalStatus")
    if isinstance(status, str) and status in _FORBIDDEN_STATUS_STRINGS:
        raise KisProvenanceProposalReviewError(f"FORBIDDEN_STATUS_STRING_PRESENT:{status}")
    authority = proposal.get("authority")
    if isinstance(authority, dict) and set(authority) == set(AUTHORITY_ALL_FALSE):
        if authority.get("review_only") is not True or any(
            value is not False for key, value in authority.items() if key != "review_only"
        ):
            raise KisProvenanceProposalReviewError("AUTHORITY_NOT_ALL_FALSE")
    if proposal.get("canonicalAuthorityConfigMutated") is not False:
        raise KisProvenanceProposalReviewError("CANONICAL_AUTHORITY_CONFIG_MUTATION_CLAIMED")

    def walk(value, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in _FORBIDDEN_EMBEDDED_AUTHORITY_KEYS:
                    raise KisProvenanceProposalReviewError(
                        f"EMBEDDED_AUTHORITY_FIELD_FORBIDDEN:{child_path}"
                    )
                walk(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str) and value in _FORBIDDEN_STATUS_STRINGS:
            raise KisProvenanceProposalReviewError(
                f"EMBEDDED_AUTHORITY_VALUE_FORBIDDEN:{path}:{value}"
            )

    # Top-level proposalStatus/authority have their own exact contract
    # above. Claims and evidence may discuss authority narratively, but
    # cannot carry a machine-readable approval field or exact authority
    # value that a downstream consumer could mistake for a grant.
    walk(proposal.get("claim"), "claim")
    walk(proposal.get("evidenceLineage"), "evidenceLineage")


def _review_result(reasons: list[str]) -> dict:
    unique = sorted(set(reasons))
    return {
        "reviewStatus": "REVIEW_INCOMPLETE" if unique else "REVIEW_READY_FOR_CIO",
        "reasons": unique,
    }


def _review_exact_canonical_proposal(proposal: dict, expected: dict) -> list[str]:
    """Reject a re-signed packet that is not the proposal this module owns.

    A self-hash only proves internal consistency.  Without this comparison a
    caller could delete every official KIS citation, replace it with an
    unrelated Atlas reference, recompute ``proposalSha256`` and incorrectly
    reach ``REVIEW_READY_FOR_CIO``.  Review readiness is therefore available
    only to the exact, code-owned canonical proposal packet.
    """
    if proposal != expected:
        return ["PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT"]
    return []


def _review_common_shape(
    proposal: dict,
    *,
    reproduced_paths: set[str] | None = None,
) -> list[str]:
    reasons: list[str] = []
    if not isinstance(proposal, dict) or set(proposal) != _PROPOSAL_FIELDS:
        return ["PROPOSAL_FIELDS_INVALID"]
    if proposal.get("schemaVersion") != SCHEMA_VERSION:
        reasons.append("SCHEMA_VERSION_MISMATCH")
    if proposal.get("proposalStatus") != PROPOSAL_STATUS:
        reasons.append("PROPOSAL_STATUS_NOT_PROPOSED_UNRATIFIED")
    if not isinstance(proposal.get("reviewAsOf"), str):
        reasons.append("REVIEW_AS_OF_MISSING")
    claimed_hash = proposal.get("proposalSha256")
    recomputed = payload_sha256({key: value for key, value in proposal.items() if key != "proposalSha256"})
    if not isinstance(claimed_hash, str) or claimed_hash != recomputed:
        reasons.append("PROPOSAL_HASH_MISMATCH")
    if proposal.get("authority") != AUTHORITY_ALL_FALSE:
        reasons.append("AUTHORITY_SHAPE_NOT_ALL_FALSE")

    evidence = proposal.get("evidenceLineage")
    if not isinstance(evidence, list) or not evidence:
        reasons.append("EVIDENCE_LINEAGE_EMPTY_OR_INVALID")
        return reasons

    for entry in evidence:
        if not isinstance(entry, dict):
            reasons.append("EVIDENCE_ENTRY_NOT_A_DICT")
            continue
        kind = entry.get("kind")
        if kind == "MUTABLE_PUBLIC_INSTRUMENT_CONFIRMATION":
            if entry.get("pinStatus") != "UNPINNED_MUTABLE":
                reasons.append("MUTABLE_EVIDENCE_PIN_STATUS_INVALID")
            reasons.append("MUTABLE_INSTRUMENT_EVIDENCE_UNPINNED")
            continue
        if kind == "ATLAS_CANONICAL_TARGET_REFERENCE":
            required = {
                "kind", "sourceName", "sourceAssetId", "market", "listingId", "canonicalInstrumentId",
            }
            if set(entry) != required:
                reasons.append("ATLAS_TARGET_REFERENCE_FIELDS_INVALID")
            continue

        # Official GitHub citations have no kind. Their pins are checked
        # against the fixed manifest, but external bytes are deliberately
        # not claimed as reproduced by this local review.
        if entry.get("repo") != _OFFICIAL_REPO:
            reasons.append("OFFICIAL_EVIDENCE_REPO_MISMATCH")
        if entry.get("commitSha") != _OFFICIAL_COMMIT:
            reasons.append("OFFICIAL_EVIDENCE_COMMIT_MISMATCH")
        path = entry.get("filePath")
        expected_hash = KIS_PINNED_EVIDENCE_MANIFEST.get(path)
        if expected_hash is None:
            reasons.append(f"OFFICIAL_EVIDENCE_PATH_NOT_IN_MANIFEST:{path}")
        elif entry.get("contentSha256") != expected_hash:
            reasons.append(f"OFFICIAL_EVIDENCE_HASH_DIFFERS_FROM_MANIFEST:{path}")
        if reproduced_paths is None or path not in reproduced_paths:
            reasons.append(f"EXTERNAL_SOURCE_BYTES_REPRODUCTION_REQUIRED:{path}")
    return reasons


def _reproduced_paths(official_checkout: Path | None) -> tuple[set[str] | None, list[str]]:
    if official_checkout is None:
        return None, []
    try:
        resolution = reproduce_kis_official_evidence(Path(official_checkout))
    except KisOfficialEvidenceResolutionError as error:
        return None, [f"EXTERNAL_SOURCE_REPRODUCTION_FAILED:{error}"]
    if (
        resolution.get("resolutionStatus") != "EXACT_GIT_BYTES_REPRODUCED"
        or resolution.get("repo") != _OFFICIAL_REPO
        or resolution.get("commitSha") != _OFFICIAL_COMMIT
    ):
        return None, ["EXTERNAL_SOURCE_REPRODUCTION_RESULT_INVALID"]
    return {
        row["filePath"] for row in resolution.get("files", [])
        if isinstance(row, dict) and row.get("contentSha256") == KIS_PINNED_EVIDENCE_MANIFEST.get(row.get("filePath"))
    }, []


def review_provider_authority_proposal(
    proposal: dict,
    official_checkout: Path | None = None,
) -> dict:
    """Review against the actual runtime implementation registry."""
    _reject_forbidden_authority(proposal)
    reproduced_paths, reproduction_reasons = _reproduced_paths(official_checkout)
    reasons = _review_common_shape(proposal, reproduced_paths=reproduced_paths)
    reasons.extend(_review_exact_canonical_proposal(proposal, provider_authority_proposal()))
    reasons.extend(reproduction_reasons)
    claim = proposal.get("claim", {})
    expected_claim_fields = {"provider", "accountScope", "currency", "positionSourceName", "assertion"}
    if not isinstance(claim, dict) or set(claim) != expected_claim_fields:
        reasons.append("PROVIDER_CLAIM_FIELDS_INVALID")
    real = portfolio_snapshot_v2.PROVIDER_IMPLEMENTATIONS.get(claim.get("provider"))
    if real is None:
        reasons.append("PROVIDER_NOT_IN_CURRENT_IMPLEMENTATION_REGISTRY")
    else:
        if claim.get("accountScope") != real.get("account_scope"):
            reasons.append("CLAIM_ACCOUNT_SCOPE_MISMATCH")
        if claim.get("currency") != real.get("currency"):
            reasons.append("CLAIM_CURRENCY_MISMATCH")
        if claim.get("positionSourceName") != real.get("position_source_name"):
            reasons.append("CLAIM_POSITION_SOURCE_NAME_MISMATCH")
    return _review_result(reasons)


def _review_alias_evidence_binding(proposal: dict) -> list[str]:
    reasons: list[str] = []
    claim = proposal.get("claim", {})
    for entry in proposal.get("evidenceLineage", []):
        if not isinstance(entry, dict) or entry.get("kind") not in {
            "MUTABLE_PUBLIC_INSTRUMENT_CONFIRMATION", "ATLAS_CANONICAL_TARGET_REFERENCE",
        }:
            continue
        for entry_key, claim_key in (
            ("sourceAssetId", "sourceAssetId"),
            ("listingId", "listingId"),
            ("canonicalInstrumentId", "canonicalInstrumentId"),
        ):
            if entry.get(entry_key) != claim.get(claim_key):
                reasons.append(f"INSTRUMENT_EVIDENCE_CLAIM_BINDING_MISMATCH:{entry_key}")
    return reasons


def review_source_alias_proposal(
    proposal: dict,
    official_checkout: Path | None = None,
) -> dict:
    """Review the target through Atlas's git-provenance-checked resolver.

    Existing Atlas identity proves only that the target instrument
    exists. It does not prove that a KIS ``pdno`` denotes that target.
    """
    _reject_forbidden_authority(proposal)
    reproduced_paths, reproduction_reasons = _reproduced_paths(official_checkout)
    reasons = _review_common_shape(proposal, reproduced_paths=reproduced_paths)
    proposal_id = proposal.get("proposalId") if isinstance(proposal, dict) else None
    expected_by_id = {
        "atlas.identity.alias.kis-samsung-electronics": source_alias_proposal_005930,
        "atlas.identity.alias.kis-sk-hynix": source_alias_proposal_000660,
    }
    expected_factory = expected_by_id.get(proposal_id)
    if expected_factory is None:
        reasons.append("SOURCE_ALIAS_PROPOSAL_ID_UNSUPPORTED")
    else:
        reasons.extend(_review_exact_canonical_proposal(proposal, expected_factory()))
    reasons.extend(reproduction_reasons)
    reasons.extend(_review_alias_evidence_binding(proposal))
    claim = proposal.get("claim", {})
    expected_claim_fields = {
        "sourceName", "sourceAssetId", "listingId", "canonicalInstrumentId", "assertion",
    }
    if not isinstance(claim, dict) or set(claim) != expected_claim_fields:
        reasons.append("SOURCE_ALIAS_CLAIM_FIELDS_INVALID")
    target_refs = [
        entry for entry in proposal.get("evidenceLineage", [])
        if isinstance(entry, dict) and entry.get("kind") == "ATLAS_CANONICAL_TARGET_REFERENCE"
    ]
    if len(target_refs) != 1:
        reasons.append("EXACTLY_ONE_ATLAS_TARGET_REFERENCE_REQUIRED")
        return _review_result(reasons)
    target = target_refs[0]
    try:
        authority = canonical_identity.load_authority()
        resolved = canonical_identity.resolve_instrument_identity(
            target.get("sourceName"), target.get("sourceAssetId"), target.get("market"),
            proposal.get("reviewAsOf"), authority,
        )
    except (canonical_identity.IdentityError, OSError, ValueError, TypeError):
        reasons.append("ATLAS_CANONICAL_TARGET_AUTHORITY_UNAVAILABLE")
        return _review_result(reasons)
    if resolved.get("status") != canonical_identity.RESOLVED:
        reasons.append(f"ATLAS_CANONICAL_TARGET_NOT_RESOLVED:{resolved.get('status')}")
    else:
        if resolved.get("listing_id") != claim.get("listingId"):
            reasons.append("TARGET_LISTING_MISMATCH")
        if resolved.get("canonical_instrument_id") != claim.get("canonicalInstrumentId"):
            reasons.append("TARGET_INSTRUMENT_MISMATCH")
    return _review_result(reasons)


def reject_if_evidence_reused_across_alias_proposals(proposal_a: dict, proposal_b: dict) -> None:
    """Reject exact instrument-specific evidence reuse across subjects."""
    def keys(proposal: dict) -> set[tuple]:
        out: set[tuple] = set()
        for entry in proposal.get("evidenceLineage", []):
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            if kind == "MUTABLE_PUBLIC_INSTRUMENT_CONFIRMATION":
                out.add((kind, tuple(entry.get("sources", []))))
            elif kind == "ATLAS_CANONICAL_TARGET_REFERENCE":
                out.add((kind, entry.get("sourceName"), entry.get("sourceAssetId"),
                         entry.get("listingId"), entry.get("canonicalInstrumentId")))
        return out

    overlap = keys(proposal_a) & keys(proposal_b)
    if overlap:
        raise KisProvenanceProposalReviewError(
            f"INSTRUMENT_SPECIFIC_EVIDENCE_REUSED_ACROSS_PROPOSALS:{overlap}"
        )
