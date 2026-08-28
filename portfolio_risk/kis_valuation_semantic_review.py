#!/usr/bin/env python3
"""Fail-closed review of the KIS valuation-semantic proposal.

Review readiness requires three independent layers: the exact canonical
proposal, reproduced official KIS git bytes, and two money-free private
operational attestations.  ``REVIEW_READY_FOR_CIO`` is still not authority.
"""
from __future__ import annotations

from pathlib import Path

from identity.kis_official_evidence_resolver import (
    KisOfficialEvidenceResolutionError,
    _resolve_git_evidence,
)
from portfolio_risk.kis_valuation_semantic_proposal import (
    AUTHORITY_ALL_FALSE,
    KIS_OFFICIAL_COMMIT,
    KIS_OFFICIAL_REPO,
    KIS_VALUATION_EVIDENCE_MANIFEST,
    PROPOSAL_STATUS,
    SCHEMA_VERSION,
    TARGET_CONTRACT_VERSION,
    canonical_json,
    payload_sha256,
    valuation_semantic_mapping_proposal,
)


RELATIONSHIP_ATTESTATION_VERSION = "kis_paper_valuation_relationship_attestation/1"
BUY_CAPACITY_ATTESTATION_VERSION = "kis_paper_buy_capacity_attestation/1"

_PROPOSAL_FIELDS = set(valuation_semantic_mapping_proposal())
_FORBIDDEN_AUTHORITY_KEYS = {
    "approval_status", "approvalStatus", "ratified_at", "ratifiedAt",
    "broker_verified", "brokerVerified", "tradingAuthority", "orderAuthority",
}
_RELATIONSHIP_TRUE_FIELDS = {
    "positionValuationComplete",
    "accountValuationPresent",
    "positionMarketValueSumMatchesValuationSum",
    "positionUnrealizedPlSumMatchesUnrealizedPlSum",
    "securitiesValuationMatchesPositionMarketValueSum",
    "totalValuationEqualsCashDepositPlusSecuritiesValuation",
    "netAssetEqualsCashDepositPlusPositionMarketValue",
}
_BUY_CAPACITY_TRUE_FIELDS = {
    "orderableCashPresent",
    "noReceivableBuyAmountPresent",
    "noReceivableBuyQuantityPresent",
    "noReceivableBuyAmountNotAboveOrderableCash",
    "quantityCalculationPriceMatchesQuote",
}
_COMMON_ATTESTATION_FIELDS = {
    "contractVersion", "status", "snapshotSchemaVersion",
    "semanticMappingRatified", "orderSubmissionAttempted", "authority",
    "attestationSha256",
}
_RELATIONSHIP_ATTESTATION_FIELDS = _COMMON_ATTESTATION_FIELDS | _RELATIONSHIP_TRUE_FIELDS
_BUY_CAPACITY_ATTESTATION_FIELDS = (
    _COMMON_ATTESTATION_FIELDS | _BUY_CAPACITY_TRUE_FIELDS | {"capacityKisFields"}
)


class KisValuationSemanticReviewError(ValueError):
    pass


def _walk_forbidden(value: object, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in _FORBIDDEN_AUTHORITY_KEYS:
                raise KisValuationSemanticReviewError(
                    f"EMBEDDED_AUTHORITY_FIELD_FORBIDDEN:{child_path}"
                )
            _walk_forbidden(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden(child, f"{path}[{index}]")
    elif isinstance(value, str) and value in {"RATIFIED", "BROKER_VERIFIED"}:
        raise KisValuationSemanticReviewError(
            f"EMBEDDED_AUTHORITY_VALUE_FORBIDDEN:{path}:{value}"
        )


def _review_proposal(proposal: object) -> list[str]:
    reasons: list[str] = []
    if not isinstance(proposal, dict) or set(proposal) != _PROPOSAL_FIELDS:
        return ["PROPOSAL_FIELDS_INVALID"]
    if proposal.get("schemaVersion") != SCHEMA_VERSION:
        reasons.append("SCHEMA_VERSION_INVALID")
    if proposal.get("proposalStatus") != PROPOSAL_STATUS:
        reasons.append("PROPOSAL_STATUS_NOT_UNRATIFIED")
    if proposal.get("targetContractVersion") != TARGET_CONTRACT_VERSION:
        reasons.append("TARGET_CONTRACT_VERSION_INVALID")
    authority = proposal.get("authority")
    if (
        authority != AUTHORITY_ALL_FALSE
        or not isinstance(authority, dict)
        or any(type(authority.get(key)) is not bool for key in AUTHORITY_ALL_FALSE)
    ):
        reasons.append("AUTHORITY_NOT_ALL_FALSE")
    if proposal.get("canonicalAuthorityConfigMutated") is not False:
        reasons.append("CANONICAL_AUTHORITY_CONFIG_MUTATION_CLAIMED")
    if proposal.get("existingPortfolioAccountFactV2Mutated") is not False:
        reasons.append("PORTFOLIO_ACCOUNT_FACT_V2_MUTATION_CLAIMED")
    expected_hash = payload_sha256({
        key: value for key, value in proposal.items() if key != "proposalSha256"
    })
    if proposal.get("proposalSha256") != expected_hash:
        reasons.append("PROPOSAL_HASH_MISMATCH")
    if canonical_json(proposal) != canonical_json(valuation_semantic_mapping_proposal()):
        reasons.append("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT")
    return reasons


def _review_official_bytes(checkout: Path | None) -> list[str]:
    if checkout is None:
        return ["EXTERNAL_SOURCE_BYTES_REPRODUCTION_REQUIRED"]
    try:
        resolution = _resolve_git_evidence(
            Path(checkout),
            repo=KIS_OFFICIAL_REPO,
            commit_sha=KIS_OFFICIAL_COMMIT,
            manifest=KIS_VALUATION_EVIDENCE_MANIFEST,
        )
    except KisOfficialEvidenceResolutionError as error:
        return [f"EXTERNAL_SOURCE_REPRODUCTION_FAILED:{error}"]
    if (
        resolution.get("resolutionStatus") != "EXACT_GIT_BYTES_REPRODUCED"
        or resolution.get("repo") != KIS_OFFICIAL_REPO
        or resolution.get("commitSha") != KIS_OFFICIAL_COMMIT
    ):
        return ["EXTERNAL_SOURCE_REPRODUCTION_RESULT_INVALID"]
    return []


def _review_common_attestation(attestation: object, version: str) -> list[str]:
    if attestation is None:
        return [f"PRIVATE_ATTESTATION_REQUIRED:{version}"]
    if not isinstance(attestation, dict):
        return [f"PRIVATE_ATTESTATION_INVALID:{version}"]
    reasons: list[str] = []
    if attestation.get("contractVersion") != version:
        reasons.append(f"PRIVATE_ATTESTATION_VERSION_INVALID:{version}")
    if attestation.get("semanticMappingRatified") is not False:
        reasons.append(f"PRIVATE_ATTESTATION_SELF_RATIFICATION_REJECTED:{version}")
    if attestation.get("orderSubmissionAttempted") is not False:
        reasons.append(f"PRIVATE_ATTESTATION_ORDER_ATTEMPT_REJECTED:{version}")
    authority = attestation.get("authority")
    if (
        authority != AUTHORITY_ALL_FALSE
        or not isinstance(authority, dict)
        or any(type(authority.get(key)) is not bool for key in AUTHORITY_ALL_FALSE)
    ):
        reasons.append(f"PRIVATE_ATTESTATION_AUTHORITY_INVALID:{version}")
    if any(key in attestation for key in (
        "accountIdentityHash", "evidencePath", "moneyValues", "positions", "sourceAssetId",
    )):
        reasons.append(f"PRIVATE_ATTESTATION_SENSITIVE_FIELD_FORBIDDEN:{version}")
    claimed_hash = attestation.get("attestationSha256")
    computed_hash = payload_sha256({
        key: value for key, value in attestation.items() if key != "attestationSha256"
    })
    if claimed_hash != computed_hash:
        reasons.append(f"PRIVATE_ATTESTATION_HASH_MISMATCH:{version}")
    return reasons


def _review_relationship_attestation(attestation: object) -> list[str]:
    reasons = _review_common_attestation(attestation, RELATIONSHIP_ATTESTATION_VERSION)
    if not isinstance(attestation, dict):
        return reasons
    if set(attestation) != _RELATIONSHIP_ATTESTATION_FIELDS:
        reasons.append("VALUATION_RELATIONSHIP_ATTESTATION_FIELDS_INVALID")
    if attestation.get("status") != "COMPLETE_RELATIONSHIP_OBSERVATION":
        reasons.append("VALUATION_RELATIONSHIP_OBSERVATION_INCOMPLETE")
    if attestation.get("snapshotSchemaVersion") != "kis_paper_full_account_snapshot/3":
        reasons.append("VALUATION_SNAPSHOT_SCHEMA_INVALID")
    for field in sorted(_RELATIONSHIP_TRUE_FIELDS):
        if attestation.get(field) is not True:
            reasons.append(f"VALUATION_RELATIONSHIP_NOT_PROVEN:{field}")
    return reasons


def _review_buy_capacity_attestation(attestation: object) -> list[str]:
    reasons = _review_common_attestation(attestation, BUY_CAPACITY_ATTESTATION_VERSION)
    if not isinstance(attestation, dict):
        return reasons
    if set(attestation) != _BUY_CAPACITY_ATTESTATION_FIELDS:
        reasons.append("BUY_CAPACITY_ATTESTATION_FIELDS_INVALID")
    if attestation.get("status") != "CAPTURED_BUY_CAPACITY_COMPLETE":
        reasons.append("BUY_CAPACITY_OBSERVATION_INCOMPLETE")
    if attestation.get("snapshotSchemaVersion") != "kis_paper_buy_capacity_snapshot/1":
        reasons.append("BUY_CAPACITY_SNAPSHOT_SCHEMA_INVALID")
    expected_fields = {
        "nrcvb_buy_amt", "nrcvb_buy_qty", "ord_psbl_cash", "psbl_qty_calc_unpr",
    }
    if set(attestation.get("capacityKisFields", [])) != expected_fields:
        reasons.append("BUY_CAPACITY_KIS_FIELDS_INCOMPLETE")
    for field in sorted(_BUY_CAPACITY_TRUE_FIELDS):
        if attestation.get(field) is not True:
            reasons.append(f"BUY_CAPACITY_RELATIONSHIP_NOT_PROVEN:{field}")
    return reasons


def review_valuation_semantic_mapping_proposal(
    proposal: dict,
    *,
    official_checkout: Path | None = None,
    relationship_attestation: dict | None = None,
    buy_capacity_attestation: dict | None = None,
) -> dict:
    """Return mechanical review readiness without creating authority."""
    _walk_forbidden(proposal)
    reasons = _review_proposal(proposal)
    reasons.extend(_review_official_bytes(official_checkout))
    reasons.extend(_review_relationship_attestation(relationship_attestation))
    reasons.extend(_review_buy_capacity_attestation(buy_capacity_attestation))
    unique = sorted(set(reasons))
    return {
        "reviewStatus": "REVIEW_INCOMPLETE" if unique else "REVIEW_READY_FOR_CIO",
        "reasons": unique,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
