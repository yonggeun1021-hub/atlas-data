#!/usr/bin/env python3
"""Proposal-only freshness policy for KIS PAPER valuation evidence.

The packet in this module is a normative candidate for a later, separate CIO
ratification.  It does not make KIS valuation semantics, ``accountFact/3``,
Portfolio Risk Input, sizing, or trading usable.  The diagnostic helper is
deliberately labelled ``PROPOSED`` and always returns all operative authority
false so callers cannot mistake a boundary calculation for policy authority.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json


SCHEMA_VERSION = "kis_valuation_freshness_policy_proposal/1"
PROPOSAL_ID = "atlas.portfolio-risk.kis-paper-valuation-freshness"
PROPOSAL_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"
TARGET_CONTRACT_VERSION = "portfolio_account_fact/3"
REVIEW_AS_OF = "2026-08-28T22:43:31Z"

MAX_SOURCE_AGE_SECONDS = 300
MAX_PAIR_GAP_SECONDS = 120

PRIVATE_PRECEDENT_REPO = "yonggeun1021-hub/atlas-private-evidence"
PRIVATE_PRECEDENT_COMMIT = "72300ef09b4b8ce501588492e970f9e24bd9c4db"
PRIVATE_PRECEDENT_PATH = "private_evidence/kis_paper_order.py"
PRIVATE_PRECEDENT_SHA256 = (
    "d46f950acc64c78da36eba12b1f36e915dc0f5c48fa79c7bd41a538ae778332a"
)

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "freshness_policy_authorized": False,
    "valuation_semantic_authorized": False,
    "account_fact_authorized": False,
    "risk_input_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def freshness_policy_proposal() -> dict:
    """Return the exact non-authoritative 300s/120s policy candidate."""
    packet = {
        "schemaVersion": SCHEMA_VERSION,
        "proposalId": PROPOSAL_ID,
        "proposalStatus": PROPOSAL_STATUS,
        "reviewAsOf": REVIEW_AS_OF,
        "targetContractVersion": TARGET_CONTRACT_VERSION,
        "providerTuple": {
            "provider": "KIS_PAPER_ACCOUNT",
            "accountScope": "KOREA",
            "currency": "KRW",
            "positionSourceName": "kis_paper_domestic_balance",
        },
        "sourceRecordContracts": [
            "kis_paper_full_account_snapshot/3",
            "kis_paper_buy_capacity_snapshot/1",
        ],
        "candidatePolicy": {
            "clockField": "availableAt",
            "decisionClockField": "reviewAsOf",
            "maxSourceAgeSeconds": MAX_SOURCE_AGE_SECONDS,
            "maxPairGapSeconds": MAX_PAIR_GAP_SECONDS,
            "comparison": "NONNEGATIVE_AGE_AND_ABSOLUTE_GAP_LESS_THAN_OR_EQUAL",
            "bothSourcesRequired": True,
            "callerOverridePermitted": False,
        },
        "operationalPrecedent": {
            "status": "OPERATING_PRECEDENT_NOT_POLICY_AUTHORITY",
            "repo": PRIVATE_PRECEDENT_REPO,
            "commitSha": PRIVATE_PRECEDENT_COMMIT,
            "filePath": PRIVATE_PRECEDENT_PATH,
            "contentSha256": PRIVATE_PRECEDENT_SHA256,
            "claimsToReproduce": {
                "defaultDecisionAgeSeconds": 300,
                "environmentDefaultDecisionAgeSeconds": 300,
                "allowedDecisionAgeRangeSeconds": [30, 900],
                "staleWhenAgeStrictlyGreaterThanConfiguredMaximum": True,
                "defaultHumanConfirmationTtlSeconds": 120,
                "environmentDefaultHumanConfirmationTtlSeconds": 120,
                "allowedHumanConfirmationTtlRangeSeconds": [30, 300],
            },
        },
        "selectionRationale": {
            "maxSourceAge": (
                "Literal reuse of the existing KIS PAPER human-approved decision "
                "age default; the valuation path is not allowed a looser window."
            ),
            "maxPairGap": (
                "Literal reuse of the existing 120-second KIS PAPER human "
                "confirmation TTL as the tighter coherence window between the "
                "two account observations."
            ),
            "numericRiskBudgetOrPositionSizeSelected": False,
        },
        "applicability": {
            "effectiveOnlyAfterSeparateRatification": True,
            "retroactiveApplicationPermitted": False,
            "syntheticOrFixtureEvidenceOperationallyQualifies": False,
            "staleOrFutureEvidenceFailsClosed": True,
        },
        "canonicalAuthorityConfigMutated": False,
        "existingPortfolioAccountFactV2Mutated": False,
        "valuationSemanticProposalMutated": False,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    packet["proposalSha256"] = payload_sha256(packet)
    return packet


def _parse_utc(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError) as exc:
        raise ValueError("PROPOSED_FRESHNESS_TIMESTAMP_INVALID") from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def diagnose_proposed_freshness(
    *, relationship_available_at: str, buy_capacity_available_at: str,
    review_as_of: str,
) -> dict:
    """Evaluate the proposal without granting freshness or consumer authority."""
    relationship = _parse_utc(relationship_available_at)
    capacity = _parse_utc(buy_capacity_available_at)
    review = _parse_utc(review_as_of)
    relationship_age = int((review - relationship).total_seconds())
    capacity_age = int((review - capacity).total_seconds())
    pair_gap = int(abs((relationship - capacity).total_seconds()))
    pit_safe = relationship_age >= 0 and capacity_age >= 0
    within = (
        pit_safe
        and relationship_age <= MAX_SOURCE_AGE_SECONDS
        and capacity_age <= MAX_SOURCE_AGE_SECONDS
        and pair_gap <= MAX_PAIR_GAP_SECONDS
    )
    return {
        "diagnosticStatus": (
            "DIAGNOSTIC_WITHIN_PROPOSED_WINDOW"
            if within else "DIAGNOSTIC_OUTSIDE_PROPOSED_WINDOW"
        ),
        "relationshipAgeSeconds": relationship_age,
        "buyCapacityAgeSeconds": capacity_age,
        "pairGapSeconds": pair_gap,
        "pitSafe": pit_safe,
        "policyAuthorityPresent": False,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }


if __name__ == "__main__":
    print(canonical_json(freshness_policy_proposal()))
