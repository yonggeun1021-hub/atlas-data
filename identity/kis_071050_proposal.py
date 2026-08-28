#!/usr/bin/env python3
"""Proposal-only 071050 identity and KIS source-alias evidence packets.

These packets are review inputs, never authority records.  They deliberately
do not write canonical identity/provider configuration, invent a generic
six-digit PDNO mapping rule, or treat the failed live product-info read as
positive evidence.
"""
from __future__ import annotations

import hashlib
import json


SCHEMA_VERSION = "kis_071050_evidence_proposal/1"
PROPOSAL_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"
KIS_REPOSITORY = "koreainvestment/open-trading-api"
KIS_PINNED_COMMIT = "b4e6249714418aa57833d1cbbbced39cbcc5b125"
ATLAS_REPOSITORY = "yonggeun1021-hub/atlas-data"
ATLAS_SOURCE_COMMIT = "5daa75caa529fee6bbd3c5a48cc7ed82cf6ec2b5"

AUTHORITY_ALL_FALSE = {
    "investment_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authorized": False,
}

KIS_IDENTITY_EVIDENCE_MANIFEST = {
    "stocks_info/kis_kospi_code_mst.py":
        "135ed22451832935f3d962d1997a05726aaec84cc9d1472ff40ea24685edd0d4",
    "stocks_info/종목마스터정보(코스피).h":
        "383cb7a4bb6f7359bc742781afd18f87c95e9a939502d2e548593a1be0de24e4",
}
KIS_ALIAS_EVIDENCE_MANIFEST = {
    "legacy/Sample01/kis_api01.py":
        "35af050e8c5b1860a227dda02dffb362db3b63122fc52711743b428023e68a24",
    "legacy/Sample01/kis_domstk.py":
        "d7bc6da85f4b086de3063f110d6e426fbc5751bc340b45e533ccdf9a5d55e575",
    "legacy/postman/모의계좌_POSTMAN_샘플코드_v1.6.json":
        "196f9390fc722b0648d63e8afc6ccc84c596aa7268c68358234cd863af355b72",
}
ATLAS_IDENTITY_EVIDENCE_MANIFEST = {
    "config/corp_map.json":
        "714e0eeb9e796122df268066e46574cd3f1e455b0b251b75dc8ae5dc899ea52a",
}

PUBLIC_MASTER_OBSERVATION = {
    "capturedAt": "2026-08-28T15:42:55Z",
    "archiveSha256": "8de794458d38e4304b0b1f69c9de0f2b4ab71ea5781585653d83b2d5c0d13be1",
    "masterMember": "kospi_code.mst",
    "masterSha256": "abfec9c79eca665741b6189fc88214961088067782791f9c90aa0715c510b4a2",
    "rowLineNumber": 1035,
    "rawBase64": (
        "MDcxMDUwICAgS1I3MDcxMDUwMDA5x9GxubHdwLbB9sHWICAgICAgICAgICAgICAgICAgICAgICAg"
        "ICAgIFNUMTAwMjEwMDAwMDAwMCBOTjZZTlkgTllOTk5OTk5OME5OTllOTk5OMDAwMTkxNDAwMDAw"
        "MDEwMDAwMU5OTjAwTk5OMDAwMDAwMDIwWTA5MDAwMDAwMDExMTIxODAwMDAwMDAwNTAwMDIwMDMw"
        "NzIxMDAwMDAwMDAwMDU1NzI1MDAwMDAwMDAwMjc4NjI5OTYwMDAwMTIgICAgICAgMCBOWVkwMDAx"
        "MDQxNjAwMDAwMTEwNjMwMDAwMTIzNjIwOTE2NzAwMDAyMS44NjIwMjYwMzMxMDAwMTA2NjU5ICAg"
        "Tk5Z"
    ),
    # SHA-256 of the exact decoded 288-byte row above, reproduced by the
    # canonical private parser from the cited archive/master pair.
    "rowSha256": "aa3dc58fe82e95d22013d2f312b8cab9e84b63833836513b1decfc1716416286",
    "observation": {
        "shortCode": "071050",
        "standardProductNumber": "KR7071050009",
        "koreanName": "한국금융지주",
        "securityGroupCode": "ST",
        "preferredStockClassCode": "0",
        "officialMeaning": "COMMON_STOCK",
    },
    "mappingStatus": "OBSERVED_NOT_RATIFIED",
    "independentReproductionStatus": "CIO_REPRODUCED_EXACT_HASH_MATCH",
}

FAILED_LIVE_PRODUCT_INFO = {
    "status": "NOT_OBTAINED_FAIL_CLOSED",
    "brokerReadAttempted": True,
    "positiveEvidenceAccepted": False,
    "orderSubmissionAttempted": False,
}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _official_evidence(
    *, evidence_domain: str, manifest: dict[str, str], required_fragments: dict[str, list[str]],
) -> dict:
    return {
        "kind": "PINNED_OFFICIAL_KIS_STATIC_EVIDENCE",
        "evidenceDomain": evidence_domain,
        "repository": KIS_REPOSITORY,
        "commitSha": KIS_PINNED_COMMIT,
        "files": [
            {
                "filePath": path,
                "contentSha256": digest,
                "requiredFragments": list(required_fragments[path]),
            }
            for path, digest in sorted(manifest.items())
        ],
    }


def _proposal(*, proposal_id: str, proposal_kind: str, claim: dict, evidence: list[dict]) -> dict:
    packet = {
        "schemaVersion": SCHEMA_VERSION,
        "proposalId": proposal_id,
        "proposalKind": proposal_kind,
        "proposalStatus": PROPOSAL_STATUS,
        "claim": claim,
        "evidence": evidence,
        "canonicalAuthorityConfigMutated": False,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    packet["proposalSha256"] = payload_sha256(packet)
    return packet


def instrument_identity_proposal_071050() -> dict:
    claim = {
        "subject": "071050",
        "canonicalIssuerId": "DART:00432102",
        "canonicalInstrumentId": "KRX:071050:COMMON",
        "listingId": "XKRX:071050",
        "standardProductNumber": "KR7071050009",
        "koreanName": "한국금융지주",
        "instrumentType": "COMMON_STOCK",
        "scope": "EXACT_INSTRUMENT_ONLY_NO_GENERIC_SYMBOL_RULE",
    }
    official = _official_evidence(
        evidence_domain="071050_ISSUER_INSTRUMENT_LISTING_IDENTITY",
        manifest=KIS_IDENTITY_EVIDENCE_MANIFEST,
        required_fragments={
            "stocks_info/kis_kospi_code_mst.py": [
                "rf1_1 = rf1[0:9].rstrip()",
                "rf1_2 = rf1[9:21].rstrip()",
                "rf1_3 = rf1[21:].strip()",
                "part1_columns = ['단축코드', '표준코드', '한글명']",
                "'우선주'",
            ],
            "stocks_info/종목마스터정보(코스피).h": [
                "mksc_shrn_iscd",
                "stnd_iscd",
                "hts_kor_isnm",
                "scrt_grp_cls_code",
                "우선주 구분 코드 (0:해당없음(보통주)",
            ],
        },
    )
    atlas = {
        "kind": "PINNED_ATLAS_OBSERVATION_EVIDENCE",
        "evidenceDomain": "071050_ISSUER_INSTRUMENT_LISTING_IDENTITY",
        "repository": ATLAS_REPOSITORY,
        "commitSha": ATLAS_SOURCE_COMMIT,
        "files": [
            {"filePath": path, "contentSha256": digest}
            for path, digest in sorted(ATLAS_IDENTITY_EVIDENCE_MANIFEST.items())
        ],
        "observedBindings": {
            "corpMap": {"symbol": "071050", "dartCorpCode": "00432102"},
        },
        "authorityStatus": "OBSERVED_NOT_RATIFIED",
    }
    master = {
        "kind": "PUBLIC_KIS_MASTER_EXACT_ROW_OBSERVATION",
        "evidenceDomain": "071050_ISSUER_INSTRUMENT_LISTING_IDENTITY",
        **json.loads(json.dumps(PUBLIC_MASTER_OBSERVATION, ensure_ascii=False)),
    }
    return _proposal(
        proposal_id="atlas.identity.proposal.kis-071050-instrument",
        proposal_kind="ISSUER_INSTRUMENT_LISTING_IDENTITY",
        claim=claim,
        evidence=[official, atlas, master],
    )


def source_alias_proposal_071050() -> dict:
    identity = instrument_identity_proposal_071050()
    claim = {
        "sourceName": "kis_paper_domestic_balance",
        "sourceAssetId": "071050",
        "listingId": "XKRX:071050",
        "canonicalInstrumentId": "KRX:071050:COMMON",
        "scope": "EXACT_SOURCE_PAIR_ONLY_NO_GENERIC_SIX_DIGIT_PDNO_RULE",
    }
    official = _official_evidence(
        evidence_domain="071050_EXACT_KIS_SOURCE_ALIAS",
        manifest=KIS_ALIAS_EVIDENCE_MANIFEST,
        required_fragments={
            "legacy/Sample01/kis_api01.py": [
                "종목번호 6자리",
                'itm_no="071050"',
                "get_inquire_price(itm_no=\"071050\")",
                "get_quotations_inquire_price(itm_no=\"071050\")",
            ],
            "legacy/Sample01/kis_domstk.py": [
                "def get_order_cash",
                '"PDNO": itm_no',
                "종목코드(6자리)",
            ],
            "legacy/postman/모의계좌_POSTMAN_샘플코드_v1.6.json": [
                '\\"PDNO\\": \\"071050\\"',
            ],
        },
    )
    target = {
        "kind": "PROPOSED_IDENTITY_TARGET_REFERENCE_NOT_AUTHORITY",
        "evidenceDomain": "071050_EXACT_KIS_SOURCE_ALIAS",
        "proposalId": identity["proposalId"],
        "proposalSha256": identity["proposalSha256"],
        "proposalStatus": identity["proposalStatus"],
        "canonicalIssuerId": identity["claim"]["canonicalIssuerId"],
        "canonicalInstrumentId": identity["claim"]["canonicalInstrumentId"],
        "listingId": identity["claim"]["listingId"],
        "standardProductNumber": identity["claim"]["standardProductNumber"],
        "preferredStockClassCode": PUBLIC_MASTER_OBSERVATION["observation"]["preferredStockClassCode"],
    }
    failure = {
        "kind": "LIVE_PRODUCT_INFO_READ_RESULT",
        "evidenceDomain": "071050_EXACT_KIS_SOURCE_ALIAS",
        **dict(FAILED_LIVE_PRODUCT_INFO),
    }
    return _proposal(
        proposal_id="atlas.identity.proposal.kis-balance-071050-alias",
        proposal_kind="EXACT_SOURCE_ALIAS",
        claim=claim,
        evidence=[official, target, failure],
    )


def all_071050_proposals() -> list[dict]:
    return [instrument_identity_proposal_071050(), source_alias_proposal_071050()]
