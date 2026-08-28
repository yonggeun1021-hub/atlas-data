#!/usr/bin/env python3
"""P3-12 mechanical, unratified proposals for canonical asset <-> Upbit KRW
market identity.

Same discipline as `identity/kis_provenance_proposal.py` and
`identity/candidate_identity_authority_proposal.py`: this module NEVER
writes to any canonical config, never sets a proposal's status to
``RATIFIED``, and never grants investable/tradeable/PAPER-eligible/order
authority. A proposal produced here is a reviewable artifact, not an
authority record -- only a separate, later, human-reviewed change to
``config/upbit_asset_identity_exceptions.json`` (or an equivalent ratified
registry) can ever make one RATIFIED. `universe/upbit_tradeable_universe.py`
must never advance a market past ``OBSERVATION_POOL`` on the strength of a
proposal produced here alone.

Default identity rule (mechanical, not a per-instrument claim): an Upbit
``KRW-<BASE>`` market's candidate canonical asset id is ``<BASE>`` itself,
mirroring how ``config/crypto_asset_identity_exceptions.json`` treats a
Kraken asset id as its own canonical id absent an explicit exception. A
record in ``config/upbit_asset_identity_exceptions.json`` overrides that
default only after ratification; this module reads the file only to
propose against it, never to auto-apply an exception as if it were already
ratified.

Two independent kinds of identity finding are surfaced, both BLOCKED and
never silently dropped or auto-resolved:

* ``DUPLICATE_CANONICAL_TARGET`` -- two or more distinct Upbit markets
  resolve (by default rule or by an as-yet-unratified exception) to the
  same candidate canonical asset id.
* ``NO_CANONICAL_CROSS_REFERENCE`` -- a market's candidate canonical asset
  id is not attested by any independently known canonical registry passed
  to ``identity_review_findings`` (e.g. the Upbit taxonomy file itself, or
  -- for cross-exchange labeling only, never as a promotion input -- the
  Kraken exclusion taxonomy's known canonical ids).
"""
from __future__ import annotations

import hashlib
import json
import re

SCHEMA_VERSION = "upbit_market_identity_proposal/1"
PROPOSAL_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"
AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "action_authorized": False,
    "order_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "investable_eligible_authorized": False,
    "paper_eligible_authorized": False,
}
_FORBIDDEN_STATUS_STRINGS = ("RATIFIED", "BROKER_VERIFIED")
_MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,20}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class UpbitMarketIdentityProposalError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def default_candidate_canonical_asset_id(market_code: str) -> str:
    if not isinstance(market_code, str) or not _MARKET_RE.fullmatch(market_code):
        raise UpbitMarketIdentityProposalError(f"MARKET_CODE_INVALID:{market_code!r}")
    return market_code.split("-", 1)[1]


def _active_exception(base_symbol: str, review_as_of: str, exceptions_doc: dict | None) -> dict | None:
    if not exceptions_doc:
        return None
    matches = [
        record for record in exceptions_doc.get("records", [])
        if record.get("source_asset_id") == base_symbol
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise UpbitMarketIdentityProposalError(f"IDENTITY_EXCEPTION_RECORD_OVERLAP:{base_symbol}")
    return matches[0]


def build_proposal(
    market_row: dict,
    *,
    review_as_of: str,
    source_url: str,
    response_sha256: str,
    available_at: str,
    exceptions_doc: dict | None = None,
) -> dict:
    """Build one PROPOSED_UNRATIFIED identity proposal for a captured Upbit
    KRW market row. ``market_row`` is the exact object returned per-market
    by ``GET /v1/market/all?is_details=true`` (``market``, ``korean_name``,
    ``english_name``), never a re-derived summary.
    """
    market = market_row.get("market")
    base_symbol = default_candidate_canonical_asset_id(market)
    if not isinstance(response_sha256, str) or not _SHA256_RE.fullmatch(response_sha256):
        raise UpbitMarketIdentityProposalError(f"EVIDENCE_HASH_INVALID:{response_sha256!r}")
    if not isinstance(available_at, str) or not _UTC_RE.fullmatch(available_at):
        raise UpbitMarketIdentityProposalError(f"AVAILABLE_AT_INVALID:{available_at!r}")

    exception_record = _active_exception(base_symbol, review_as_of, exceptions_doc)
    if exception_record is not None:
        candidate = exception_record["canonical_asset_id"]
        exception_note = (
            f"Overridden by proposed (not yet ratified) exception record "
            f"source_asset_id={base_symbol} -> canonical_asset_id={candidate}."
        )
        exception_status = "PROPOSED_EXCEPTION_APPLIED_UNRATIFIED"
    else:
        candidate = base_symbol
        exception_note = "No identity exception proposed; default rule applied (base symbol == canonical id)."
        exception_status = "DEFAULT_RULE_NO_EXCEPTION"

    claim = {
        "upbitMarket": market,
        "quoteCurrency": "KRW",
        "baseSymbol": base_symbol,
        "candidateCanonicalAssetId": candidate,
        "koreanName": market_row.get("korean_name"),
        "englishName": market_row.get("english_name"),
        "exceptionStatus": exception_status,
        "assertion": (
            f"Upbit KRW market {market} (base symbol {base_symbol}) denotes the "
            f"same real-world crypto asset as candidate canonical asset id "
            f"{candidate}. {exception_note}"
        ),
    }
    evidence = [
        {
            "kind": "UPBIT_PUBLIC_MARKET_ALL_SNAPSHOT",
            "sourceUrl": source_url,
            "responseSha256": response_sha256,
            "availableAt": available_at,
            "marketRow": {
                "market": market,
                "korean_name": market_row.get("korean_name"),
                "english_name": market_row.get("english_name"),
            },
        },
    ]
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "proposalId": f"atlas.identity.alias.upbit-{market.lower()}",
        "reviewAsOf": review_as_of,
        "proposalStatus": PROPOSAL_STATUS,
        "claim": claim,
        "evidenceLineage": evidence,
        "authority": dict(AUTHORITY_ALL_FALSE),
        "canonicalAuthorityConfigMutated": False,
    }
    payload["proposalSha256"] = payload_sha256(payload)
    return payload


def build_proposals(
    market_rows: list,
    *,
    review_as_of: str,
    source_url: str,
    response_sha256: str,
    available_at: str,
    exceptions_doc: dict | None = None,
) -> list:
    return [
        build_proposal(
            row,
            review_as_of=review_as_of,
            source_url=source_url,
            response_sha256=response_sha256,
            available_at=available_at,
            exceptions_doc=exceptions_doc,
        )
        for row in market_rows
    ]


def identity_review_findings(proposals: list, known_canonical_ids: set | None = None) -> list:
    """Fail-closed, never-auto-resolved identity findings across a proposal
    set. Returns a sorted list of BLOCKED entries; an empty list means every
    proposal is a unique, cross-referenced candidate -- it does NOT mean any
    proposal is ratified.
    """
    by_canonical: dict[str, list[str]] = {}
    for proposal in proposals:
        candidate = proposal["claim"]["candidateCanonicalAssetId"]
        by_canonical.setdefault(candidate, []).append(proposal["claim"]["upbitMarket"])

    findings = []
    for candidate, markets in by_canonical.items():
        if len(markets) > 1:
            findings.append({
                "finding": "DUPLICATE_CANONICAL_TARGET",
                "candidateCanonicalAssetId": candidate,
                "upbitMarkets": sorted(markets),
                "status": "BLOCKED",
                "resolution": "NEVER_AUTO_RESOLVED_REQUIRES_HUMAN_RATIFICATION",
            })
    if known_canonical_ids is not None:
        for proposal in proposals:
            candidate = proposal["claim"]["candidateCanonicalAssetId"]
            if candidate not in known_canonical_ids:
                findings.append({
                    "finding": "NO_CANONICAL_CROSS_REFERENCE",
                    "candidateCanonicalAssetId": candidate,
                    "upbitMarkets": [proposal["claim"]["upbitMarket"]],
                    "status": "BLOCKED",
                    "resolution": "NEVER_AUTO_RESOLVED_REQUIRES_HUMAN_RATIFICATION",
                })
    findings.sort(key=lambda row: (row["finding"], row["candidateCanonicalAssetId"]))
    return findings


def blocked_markets(findings: list) -> set:
    """The set of Upbit market codes that appear in any BLOCKED finding --
    consumed by the classifier to force those markets to the BLOCKED state
    regardless of any other metric.
    """
    out: set = set()
    for finding in findings:
        out.update(finding["upbitMarkets"])
    return out
