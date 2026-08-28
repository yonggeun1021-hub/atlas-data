#!/usr/bin/env python3
"""Independent review of `identity.kis_provenance_proposal` artifacts.

Never trusts a proposal's own claimed correctness -- re-derives every
check from the proposal's raw fields (plus, for the alias proposals, the
CURRENT `security_identity` authority document) each time, the same
"never trust a stored value" discipline every other validator in this
codebase already follows.

Two distinct verdict classes, matching the CIO's stated boundary:

- INCOMPLETE (`review_status: "REVIEW_INCOMPLETE"`, reasons listed): the
  proposal is honestly not yet reviewable as-is -- an unpinned/mutable
  evidence reference, a hash that no longer matches, a tuple that
  doesn't match the real `PROVIDER_IMPLEMENTATIONS` shape, or an alias
  target that isn't (or is no longer) RATIFIED. None of these are
  integrity violations by themselves; they just mean review can't
  proceed yet.
- REJECTED (raises `KisProvenanceProposalReviewError`): an actual
  integrity violation -- a forbidden authority-escalating string/value
  smuggled into the payload, or (checked separately, across a PAIR of
  alias proposals) instrument-specific evidence reused between two
  different alias claims.

Passing review (`REVIEW_COMPLETE`) means exactly one thing: this artifact
is now in a state a human CIO could review and decide RATIFIED/rejected
on. It is never itself an authority grant -- no function in this module
writes to any config file, and none can ever return anything resembling
`RATIFIED`/`BROKER_VERIFIED`.
"""
from __future__ import annotations

from identity.kis_provenance_proposal import (
    AUTHORITY_ALL_FALSE,
    PROPOSAL_STATUS,
    SCHEMA_VERSION,
    _FORBIDDEN_STATUS_STRINGS,
    payload_sha256,
)

_PROPOSAL_FIELDS = {
    "schemaVersion", "proposalId", "proposalStatus", "claim",
    "evidenceLineage", "authority", "canonicalAuthorityConfigMutated", "proposalSha256",
}


class KisProvenanceProposalReviewError(ValueError):
    pass


def _reject_forbidden_strings(proposal: dict) -> None:
    """A rehash can never grant authority: checks the SPECIFIC
    authority-bearing fields (never a blind substring scan across the
    whole payload -- legitimate narrative content routinely and safely
    says things like "already RATIFIED under krx_open_api_stock_daily",
    and PROPOSAL_STATUS itself contains "RATIFIED" as a substring of
    "UNRATIFIED"; scanning naively would reject every real proposal).
    This runs before anything else -- a proposal that fails this is
    rejected outright, never merely "incomplete"."""
    status = proposal.get("proposalStatus")
    if isinstance(status, str) and status in _FORBIDDEN_STATUS_STRINGS:
        raise KisProvenanceProposalReviewError(f"FORBIDDEN_STATUS_STRING_PRESENT:{status}")
    authority = proposal.get("authority")
    if not isinstance(authority, dict) or set(authority) != set(AUTHORITY_ALL_FALSE):
        return  # shape issue -- _review_common_shape's own checks report this as INCOMPLETE
    if any(v is not False for k, v in authority.items() if k != "review_only"):
        raise KisProvenanceProposalReviewError("AUTHORITY_NOT_ALL_FALSE")
    if authority.get("review_only") is not True:
        raise KisProvenanceProposalReviewError("AUTHORITY_NOT_ALL_FALSE")
    if proposal.get("canonicalAuthorityConfigMutated") is not False:
        raise KisProvenanceProposalReviewError("CANONICAL_AUTHORITY_CONFIG_MUTATION_CLAIMED")


def _incomplete(reasons: list) -> dict:
    return {"reviewStatus": "REVIEW_INCOMPLETE", "reasons": reasons}


def _complete() -> dict:
    return {"reviewStatus": "REVIEW_COMPLETE", "reasons": []}


def _review_common_shape(proposal: dict) -> list:
    reasons = []
    if not isinstance(proposal, dict) or set(proposal) != _PROPOSAL_FIELDS:
        return ["PROPOSAL_FIELDS_INVALID"]
    if proposal.get("schemaVersion") != SCHEMA_VERSION:
        reasons.append("SCHEMA_VERSION_MISMATCH")
    if proposal.get("proposalStatus") != PROPOSAL_STATUS:
        reasons.append("PROPOSAL_STATUS_NOT_PROPOSED_UNRATIFIED")
    claimed_hash = proposal.get("proposalSha256")
    recomputed = payload_sha256({k: v for k, v in proposal.items() if k != "proposalSha256"})
    if not isinstance(claimed_hash, str) or claimed_hash != recomputed:
        reasons.append("PROPOSAL_HASH_MISMATCH")
    evidence = proposal.get("evidenceLineage")
    if not isinstance(evidence, list) or not evidence:
        reasons.append("EVIDENCE_LINEAGE_EMPTY_OR_INVALID")
    else:
        for entry in evidence:
            if not isinstance(entry, dict):
                reasons.append("EVIDENCE_ENTRY_NOT_A_DICT")
                continue
            if entry.get("kind") in ("PUBLIC_THIRD_PARTY_CONFIRMATION", "EXISTING_RATIFIED_ATLAS_ALIAS"):
                continue  # non-pinned-github evidence kinds, checked structurally only
            commit_sha = entry.get("commitSha")
            if not isinstance(commit_sha, str) or len(commit_sha) != 40 or commit_sha in ("main", "HEAD", ""):
                reasons.append(f"EVIDENCE_COMMIT_SHA_NOT_PINNED:{entry.get('filePath')}")
            content_hash = entry.get("contentSha256")
            if not isinstance(content_hash, str) or len(content_hash) != 64:
                reasons.append(f"EVIDENCE_CONTENT_HASH_MISSING:{entry.get('filePath')}")
    return reasons


def review_provider_authority_proposal(proposal: dict, *, provider_implementations: dict) -> dict:
    """`provider_implementations`: the CURRENT (public, already-committed)
    `portfolio_risk.portfolio_snapshot_v2.PROVIDER_IMPLEMENTATIONS` dict
    -- re-checked against, never assumed."""
    _reject_forbidden_strings(proposal)
    reasons = _review_common_shape(proposal)
    claim = proposal.get("claim", {})
    real = provider_implementations.get(claim.get("provider"))
    if real is None:
        reasons.append("PROVIDER_NOT_IN_CURRENT_IMPLEMENTATION_REGISTRY")
    else:
        if claim.get("accountScope") != real.get("account_scope"):
            reasons.append("CLAIM_ACCOUNT_SCOPE_MISMATCH")
        if claim.get("currency") != real.get("currency"):
            reasons.append("CLAIM_CURRENCY_MISMATCH")
        if claim.get("positionSourceName") != real.get("position_source_name"):
            reasons.append("CLAIM_POSITION_SOURCE_NAME_MISMATCH")
    return _incomplete(reasons) if reasons else _complete()


def review_source_alias_proposal(proposal: dict, *, security_identity: dict) -> dict:
    """`security_identity`: the CURRENT (public, already-committed)
    `config/canonical_security_identity.json` document -- re-checked
    against, never assumed. The claimed listing/instrument must be
    CURRENTLY present and RATIFIED there (under ANY source_name -- this
    proposal only adds a new alias pointing at it, never a new
    listing/instrument)."""
    _reject_forbidden_strings(proposal)
    reasons = _review_common_shape(proposal)
    claim = proposal.get("claim", {})
    listing_id = claim.get("listingId")
    instrument_id = claim.get("canonicalInstrumentId")
    listings = {row.get("listing_id"): row for row in security_identity.get("listings", [])}
    instruments = {row.get("canonical_instrument_id"): row for row in security_identity.get("instruments", [])}
    listing_row = listings.get(listing_id)
    instrument_row = instruments.get(instrument_id)
    if listing_row is None or listing_row.get("approval_status") != "RATIFIED":
        reasons.append("TARGET_LISTING_NOT_CURRENTLY_RATIFIED")
    elif listing_row.get("canonical_instrument_id") != instrument_id:
        reasons.append("TARGET_LISTING_INSTRUMENT_MISMATCH")
    if instrument_row is None or instrument_row.get("approval_status") != "RATIFIED":
        reasons.append("TARGET_INSTRUMENT_NOT_CURRENTLY_RATIFIED")
    # Already-proposed alias for the SAME (source_name, source_asset_id)
    # must not already exist as a RATIFIED row -- this proposal would be
    # redundant, and reviewing a redundant proposal as "complete" invites
    # silent double-approval.
    already_aliased = any(
        row.get("source_name") == claim.get("sourceName")
        and row.get("source_asset_id") == claim.get("sourceAssetId")
        for row in security_identity.get("source_aliases", [])
    )
    if already_aliased:
        reasons.append("ALIAS_ALREADY_EXISTS_PROPOSAL_REDUNDANT")
    return _incomplete(reasons) if reasons else _complete()


def reject_if_evidence_reused_across_alias_proposals(proposal_a: dict, proposal_b: dict) -> None:
    """Instrument-specific evidence (public third-party confirmation of
    WHAT a specific ticker is, or a specific existing RATIFIED alias) is
    never valid support for a DIFFERENT alias's claim -- only the
    general, non-instrument-specific PDNO-field-shape citation may
    legitimately appear in both. Raises on any instrument-specific
    overlap; never silently drops the duplicate."""
    def _instrument_specific_keys(proposal: dict) -> set:
        keys = set()
        for entry in proposal.get("evidenceLineage", []):
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") == "PUBLIC_THIRD_PARTY_CONFIRMATION":
                keys.add(("PUBLIC_THIRD_PARTY_CONFIRMATION", entry.get("claim")))
            elif entry.get("kind") == "EXISTING_RATIFIED_ATLAS_ALIAS":
                keys.add(("EXISTING_RATIFIED_ATLAS_ALIAS", entry.get("note")))
        return keys

    overlap = _instrument_specific_keys(proposal_a) & _instrument_specific_keys(proposal_b)
    if overlap:
        raise KisProvenanceProposalReviewError(f"INSTRUMENT_SPECIFIC_EVIDENCE_REUSED_ACROSS_PROPOSALS:{overlap}")
