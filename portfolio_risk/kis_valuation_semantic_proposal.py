#!/usr/bin/env python3
"""Review-only KIS PAPER valuation-semantic proposal.

This module records the exact semantic mappings that a future additive
``portfolio_account_fact/3`` bridge may use.  It is deliberately not an
authority registry and never mutates the existing ``portfolio_account_fact/2``
contract.  In particular, a proposal hash is not ratification, official field
labels are not operational reconciliation evidence, and a read-only account
fact never grants Stage, Buy, Action, Order, Production, or Trading authority.

The proposed v3 shape also avoids preserving v2's ambiguous account-wide
``buyingPower`` field.  KIS ``inquire-psbl-order`` is instrument/query specific,
so ``nrcvb_buy_amt`` is proposed only as an instrument buy-capacity fact.
"""
from __future__ import annotations

import hashlib
import json


SCHEMA_VERSION = "kis_valuation_semantic_mapping_proposal/1"
PROPOSAL_ID = "atlas.portfolio-risk.kis-paper-valuation-semantics"
PROPOSAL_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"
REVIEW_AS_OF = "2026-08-29T00:00:00Z"
TARGET_CONTRACT_VERSION = "portfolio_account_fact/3"

KIS_OFFICIAL_REPO = "koreainvestment/open-trading-api"
KIS_OFFICIAL_COMMIT = "b4e6249714418aa57833d1cbbbced39cbcc5b125"
KIS_VALUATION_EVIDENCE_MANIFEST = {
    "examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py":
        "5897fd3ce320a8d9683208689727714c037241b2010cc89f4c7e6c63b6255c89",
    "examples_llm/domestic_stock/inquire_psbl_order/chk_inquire_psbl_order.py":
        "2e4e1a42625ed86a165fb3779d32cb9ac5a21d8c9e1f0e8807c4600d6f1e3d5b",
}

AUTHORITY_ALL_FALSE = {
    "review_only": True,
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


def _official_evidence(path: str, claim: str) -> dict:
    return {
        "repo": KIS_OFFICIAL_REPO,
        "commitSha": KIS_OFFICIAL_COMMIT,
        "filePath": path,
        "contentSha256": KIS_VALUATION_EVIDENCE_MANIFEST[path],
        "claim": claim,
    }


def valuation_semantic_mapping_proposal() -> dict:
    """Return the single canonical, non-authoritative proposal packet."""
    mappings = [
        {
            "semanticId": "ACCOUNT_NET_ASSET_KRW",
            "sourceEndpoint": "inquire_balance.output2",
            "rawKisField": "nass_amt",
            "officialKoreanMeaning": "순자산금액",
            "targetPath": "account.netAssetKrw",
        },
        {
            "semanticId": "ACCOUNT_CASH_DEPOSIT_TOTAL_KRW",
            "sourceEndpoint": "inquire_balance.output2",
            "rawKisField": "dnca_tot_amt",
            "officialKoreanMeaning": "예수금총금액",
            "targetPath": "account.cashDepositTotalKrw",
        },
        {
            "semanticId": "POSITION_MARKET_VALUE_KRW",
            "sourceEndpoint": "inquire_balance.output1",
            "rawKisField": "evlu_amt",
            "officialKoreanMeaning": "평가금액",
            "targetPath": "positions[].marketValueKrw",
        },
        {
            "semanticId": "POSITION_UNREALIZED_PL_KRW",
            "sourceEndpoint": "inquire_balance.output1",
            "rawKisField": "evlu_pfls_amt",
            "officialKoreanMeaning": "평가손익금액",
            "targetPath": "positions[].unrealizedPlKrw",
        },
        {
            "semanticId": "INSTRUMENT_NO_RECEIVABLE_BUY_AMOUNT_KRW",
            "sourceEndpoint": "inquire_psbl_order.output",
            "rawKisField": "nrcvb_buy_amt",
            "officialKoreanMeaning": "미수없는매수금액",
            "targetPath": "instrumentBuyCapacity[].noReceivableBuyAmountKrw",
        },
        {
            "semanticId": "INSTRUMENT_NO_RECEIVABLE_BUY_QUANTITY",
            "sourceEndpoint": "inquire_psbl_order.output",
            "rawKisField": "nrcvb_buy_qty",
            "officialKoreanMeaning": "미수없는매수수량",
            "targetPath": "instrumentBuyCapacity[].noReceivableBuyQuantity",
        },
        {
            "semanticId": "INSTRUMENT_CAPACITY_CALCULATION_PRICE_KRW",
            "sourceEndpoint": "inquire_psbl_order.output",
            "rawKisField": "psbl_qty_calc_unpr",
            "officialKoreanMeaning": "가능수량계산단가",
            "targetPath": "instrumentBuyCapacity[].quantityCalculationPriceKrw",
        },
    ]
    packet = {
        "schemaVersion": SCHEMA_VERSION,
        "proposalId": PROPOSAL_ID,
        "reviewAsOf": REVIEW_AS_OF,
        "proposalStatus": PROPOSAL_STATUS,
        "targetContractVersion": TARGET_CONTRACT_VERSION,
        "providerTuple": {
            "provider": "KIS_PAPER_ACCOUNT",
            "accountScope": "KOREA",
            "currency": "KRW",
            "positionSourceName": "kis_paper_domestic_balance",
        },
        "mappings": mappings,
        "requiredOperationalRelationships": [
            "sum(positions[].evlu_amt)==output2.evlu_amt_smtl_amt",
            "sum(positions[].evlu_pfls_amt)==output2.evlu_pfls_smtl_amt",
            "output2.scts_evlu_amt==sum(positions[].evlu_amt)",
            "output2.tot_evlu_amt==output2.dnca_tot_amt+output2.scts_evlu_amt",
            "output2.nass_amt==output2.dnca_tot_amt+sum(positions[].evlu_amt)",
            "buy-capacity accountIdentityHash matches the balance snapshot",
            "buy-capacity sourceAssetId has an exact RATIFIED source alias",
            "balance and buy-capacity availableAt values are PIT-safe and fresh",
        ],
        "explicitExclusions": [
            {
                "rawKisField": "ord_psbl_cash",
                "forbiddenTarget": "account.cashDepositTotalKrw",
                "reason": "주문가능현금 is not 예수금총금액",
            },
            {
                "rawKisField": "ord_psbl_cash",
                "forbiddenTarget": "account.buyingPower",
                "reason": "no account-wide generic buyingPower is defined by this proposal",
            },
            {
                "rawKisField": "nrcvb_buy_amt",
                "forbiddenTarget": "account.buyingPower",
                "reason": "the KIS query is instrument specific and must retain its instrument binding",
            },
            {
                "rawKisField": "tot_evlu_amt",
                "forbiddenTarget": "account.netAssetKrw",
                "reason": "총평가금액 is retained for reconciliation and is not relabelled 순자산",
            },
        ],
        "evidenceLineage": [
            _official_evidence(
                "examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py",
                "Official KIS field labels for balance position and account valuation outputs.",
            ),
            _official_evidence(
                "examples_llm/domestic_stock/inquire_psbl_order/chk_inquire_psbl_order.py",
                "Official KIS field labels for instrument-specific no-receivable buy capacity.",
            ),
        ],
        "privateOperationalEvidence": {
            "status": "REQUIRED_NOT_EMBEDDED_IN_PUBLIC_PROPOSAL",
            "requiredContracts": [
                "kis_paper_valuation_relationship_attestation/1",
                "kis_paper_buy_capacity_attestation/1",
            ],
            "moneyValuesPermittedInPublicArtifact": False,
            "accountIdentityPermittedInPublicArtifact": False,
            "positionSymbolsPermittedInPublicArtifact": False,
        },
        "canonicalAuthorityConfigMutated": False,
        "existingPortfolioAccountFactV2Mutated": False,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    packet["proposalSha256"] = payload_sha256(packet)
    return packet


if __name__ == "__main__":
    print(canonical_json(valuation_semantic_mapping_proposal()))
