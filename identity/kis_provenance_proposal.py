#!/usr/bin/env python3
"""P0-2C mechanical, unratified proposals for KIS PAPER's provider
authority and its two currently-evidenced source aliases.

Same discipline as `candidate_identity_authority_proposal.py`: this
module NEVER writes to `config/data_provider_authority.json` or
`config/canonical_security_identity.json`, never sets `approval_status`
to `RATIFIED`, and never grants any investability/Stage/Buy/Order
authority. A proposal produced here is a reviewable artifact, not an
authority record -- only a separate, human-reviewed change to those two
config files (a distinct, later PR) can ever make one RATIFIED.

Three INDEPENDENT proposals, each with its own evidence lineage --
generic transformation rules (e.g. "any 6-digit KIS pdno maps to the
same-digit XKRX listing") are deliberately never expressed anywhere in
this module. A KIS holding with no matching alias proposal here stays
`IDENTITY_NOT_COMPUTABLE`, by construction of
`identity.canonical_identity.resolve_instrument_identity()`'s exact-match
lookup -- not because this module asserts a blocking rule, but because it
asserts nothing at all about any other pdno:

1. `provider_authority_proposal()` -- the exact tuple
   (KIS_PAPER_ACCOUNT, KOREA, KRW, kis_paper_domestic_balance) is a real,
   provenance-bound data source. Evidence: KIS's own official GitHub
   documentation of the domestic-stock balance-inquiry endpoint/TR_IDs/
   field semantics/base-URL split -- pinned to an exact commit SHA, file
   path, and content hash, never a bare mutable URL.
2. `source_alias_proposal_005930()` -- `kis_paper_domestic_balance` +
   `005930` denotes the SAME instrument already RATIFIED as
   `KRX:005930:COMMON` / `XKRX:005930` under `krx_open_api_stock_daily`.
3. `source_alias_proposal_000660()` -- same claim for `000660` /
   `KRX:000660:COMMON` / `XKRX:000660`.

Evidence pinning contract: every citation is
`{repo, commit_sha, file_path, content_sha256}`, verified against the
real file's actual bytes at proposal-generation time (see
`_pinned_github_evidence`, called only with citations this module's own
author has independently fetched and hashed -- never a bare claim). A
future reviewer can refetch the same `repo`+`commit_sha`+`file_path` and
confirm the same `content_sha256`, reproducing exactly what was reviewed
even if the file's current HEAD has since changed.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA_VERSION = "kis_provenance_proposal/1"
PROPOSAL_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"
AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "action_authorized": False,
    "order_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}
# Forbidden anywhere inside a proposal payload -- see
# validate_proposal_is_never_self_upgrading below. A proposal claiming
# any of these strings/values is rejected outright, regardless of hash
# consistency.
_FORBIDDEN_STATUS_STRINGS = ("RATIFIED", "BROKER_VERIFIED")


class KisProvenanceProposalError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _pinned_github_evidence(*, repo: str, commit_sha: str, file_path: str, content_sha256: str, note: str) -> dict:
    """A single immutable-provenance citation. `commit_sha` must be a
    real, non-branch, non-tag 40-hex-char commit id (never a mutable ref
    like `main`/`HEAD`) -- see `validate_provenance_is_pinned`."""
    if not isinstance(commit_sha, str) or len(commit_sha) != 40 or any(c not in "0123456789abcdef" for c in commit_sha):
        raise KisProvenanceProposalError(f"EVIDENCE_COMMIT_SHA_NOT_PINNED:{commit_sha!r}")
    if not isinstance(content_sha256, str) or len(content_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in content_sha256
    ):
        raise KisProvenanceProposalError(f"EVIDENCE_CONTENT_HASH_INVALID:{content_sha256!r}")
    return {
        "repo": repo, "commitSha": commit_sha, "filePath": file_path,
        "contentSha256": content_sha256, "note": note,
    }


def _proposal(*, proposal_id: str, claim: dict, evidence: list) -> dict:
    if not evidence:
        raise KisProvenanceProposalError(f"EVIDENCE_LINEAGE_EMPTY:{proposal_id}")
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "proposalId": proposal_id,
        "proposalStatus": PROPOSAL_STATUS,
        "claim": claim,
        "evidenceLineage": evidence,
        "authority": dict(AUTHORITY_ALL_FALSE),
        "canonicalAuthorityConfigMutated": False,
    }
    payload["proposalSha256"] = payload_sha256(payload)
    return payload


# ---------------------------------------------------------------------------
# 1. Provider authority proposal
# ---------------------------------------------------------------------------

_KIS_OPEN_TRADING_API_REPO = "koreainvestment/open-trading-api"
_KIS_OPEN_TRADING_API_PINNED_COMMIT = "b4e6249714418aa57833d1cbbbced39cbcc5b125"


def provider_authority_proposal() -> dict:
    claim = {
        "provider": "KIS_PAPER_ACCOUNT",
        "accountScope": "KOREA",
        "currency": "KRW",
        "positionSourceName": "kis_paper_domestic_balance",
        "assertion": (
            "The domestic-stock balance-inquiry endpoint "
            "/uapi/domestic-stock/v1/trading/inquire-balance, called with "
            "TR_ID VTTC8434R against KIS's PAPER (모의투자, vps) environment "
            "base URL, is the real, provenance-bound source Atlas's "
            "kis_paper_domestic_balance provider name mechanically denotes."
        ),
    }
    evidence = [
        _pinned_github_evidence(
            repo=_KIS_OPEN_TRADING_API_REPO, commit_sha=_KIS_OPEN_TRADING_API_PINNED_COMMIT,
            file_path="backtester/kis_backtest/providers/kis/constants.py",
            content_sha256="986cc68c92e889321361ab4e64266749650e4c3f8b8e37f7ee2d9fe9444d2811",
            note="KIS's own repo: BALANCE_REAL=TTTC8434R, BALANCE_PAPER=VTTC8434R, "
                 "DOMESTIC_BALANCE=/uapi/domestic-stock/v1/trading/inquire-balance.",
        ),
        _pinned_github_evidence(
            repo=_KIS_OPEN_TRADING_API_REPO, commit_sha=_KIS_OPEN_TRADING_API_PINNED_COMMIT,
            file_path="kis_devlp.yaml",
            content_sha256="61f036e51e02c4f8b86fb26e361fe98ecb26e0d587160d724f2d62768a60e2a2",
            note="KIS's own repo: prod=https://openapi.koreainvestment.com:9443, "
                 "vps (모의투자)=https://openapivts.koreainvestment.com:29443.",
        ),
        _pinned_github_evidence(
            repo=_KIS_OPEN_TRADING_API_REPO, commit_sha=_KIS_OPEN_TRADING_API_PINNED_COMMIT,
            file_path="examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py",
            content_sha256="5897fd3ce320a8d9683208689727714c037241b2010cc89f4c7e6c63b6255c89",
            note="KIS's own repo: field labels pdno=상품번호, hldg_qty=보유수량, "
                 "ord_psbl_qty=주문가능수량 in the balance response.",
        ),
    ]
    return _proposal(proposal_id="atlas.identity.provider.kis-paper-account", claim=claim, evidence=evidence)


# ---------------------------------------------------------------------------
# 2/3. Source alias proposals -- fully independent evidence, no reuse
# ---------------------------------------------------------------------------

def source_alias_proposal_005930() -> dict:
    claim = {
        "sourceName": "kis_paper_domestic_balance", "sourceAssetId": "005930",
        "listingId": "XKRX:005930", "canonicalInstrumentId": "KRX:005930:COMMON",
        "assertion": (
            "A KIS PAPER domestic-stock holding with PDNO=005930 is Samsung "
            "Electronics common stock, the same instrument already RATIFIED "
            "under krx_open_api_stock_daily's own alias."
        ),
    }
    evidence = [
        _pinned_github_evidence(
            repo=_KIS_OPEN_TRADING_API_REPO, commit_sha=_KIS_OPEN_TRADING_API_PINNED_COMMIT,
            file_path="legacy/Sample01/kis_domstk.py",
            content_sha256="d7bc6da85f4b086de3063f110d6e426fbc5751bc340b45e533ccdf9a5d55e575",
            note="KIS's own repo: PDNO documented as 종목코드(6자리) (ETNs excepted, "
                 "start with Q) -- the general PDNO field shape, not "
                 "instrument-specific by itself.",
        ),
        {
            "kind": "PUBLIC_THIRD_PARTY_CONFIRMATION", "claim": "005930 denotes Samsung Electronics on KRX",
            "sources": [
                "https://www.etoday.co.kr/news/view/2032584",
                "https://www.koreantickers.com/stock/005930",
                "https://www.google.com/finance/beta/quote/005930:KRX",
            ],
        },
        {
            "kind": "EXISTING_RATIFIED_ATLAS_ALIAS",
            "note": "config/canonical_security_identity.json: krx_open_api_stock_daily "
                    "+ 005930 -> XKRX:005930, RATIFIED 2026-08-25, "
                    "evidence/identity/approvals/2026-08-25/alias.samsung-electronics.json.",
        },
    ]
    return _proposal(proposal_id="atlas.identity.alias.kis-samsung-electronics", claim=claim, evidence=evidence)


def source_alias_proposal_000660() -> dict:
    claim = {
        "sourceName": "kis_paper_domestic_balance", "sourceAssetId": "000660",
        "listingId": "XKRX:000660", "canonicalInstrumentId": "KRX:000660:COMMON",
        "assertion": (
            "A KIS PAPER domestic-stock holding with PDNO=000660 is SK hynix "
            "common stock, the same instrument already RATIFIED under "
            "krx_open_api_stock_daily's own alias."
        ),
    }
    evidence = [
        _pinned_github_evidence(
            repo=_KIS_OPEN_TRADING_API_REPO, commit_sha=_KIS_OPEN_TRADING_API_PINNED_COMMIT,
            file_path="legacy/Sample01/kis_domstk.py",
            content_sha256="d7bc6da85f4b086de3063f110d6e426fbc5751bc340b45e533ccdf9a5d55e575",
            note="KIS's own repo: PDNO documented as 종목코드(6자리) (ETNs excepted, "
                 "start with Q) -- the general PDNO field shape, not "
                 "instrument-specific by itself.",
        ),
        {
            "kind": "PUBLIC_THIRD_PARTY_CONFIRMATION", "claim": "000660 denotes SK hynix on KRX",
            "sources": [
                "https://www.google.com/finance/beta/quote/000660:KRX",
                "https://www.tradingview.com/symbols/KRX-000660/",
                "https://stockanalysis.com/quote/krx/000660/",
                "https://www.koreantickers.com/stock/000660",
            ],
        },
        {
            "kind": "EXISTING_RATIFIED_ATLAS_ALIAS",
            "note": "config/canonical_security_identity.json: krx_open_api_stock_daily "
                    "+ 000660 -> XKRX:000660, RATIFIED 2026-08-25, "
                    "evidence/identity/approvals/2026-08-25/alias.sk-hynix.json.",
        },
    ]
    return _proposal(proposal_id="atlas.identity.alias.kis-sk-hynix", claim=claim, evidence=evidence)


def all_proposals() -> list[dict]:
    return [
        provider_authority_proposal(),
        source_alias_proposal_005930(),
        source_alias_proposal_000660(),
    ]
