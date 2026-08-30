#!/usr/bin/env python3
"""P3-12-ID-01 -- Upbit Bounded Identity Registry (pre-ratification candidate).

P3-12-TAX-01 drafted taxonomy content for 81 Upbit KRW markets, corroborated
against the RATIFIED Kraken breadth taxonomy purely by *canonical asset id*
match. This module answers the question P3-12-TAX-01 explicitly deferred:
**is Upbit's market actually the same real-world project as the one Kraken's
taxonomy (or any other independent source) identifies by that id -- not
merely the same ticker string?**

Ticker equality alone is never sufficient. This module NEVER classifies a
market `VERIFIED_CANDIDATE` from a name/symbol match alone -- every verdict
is computed from a curated, human/agent-researched evidence record citing
actual official sources (project sites, chain explorers, CoinGecko),
checked into this repository as data (`config/upbit_bounded_identity_evidence.json`),
never fetched live at evaluation time. This module contains NO parallel
identity-proposal or collision-detection logic of its own -- collision
detection is delegated entirely to the existing, already-tested
`identity/upbit_market_identity_proposal.py::identity_review_findings()`,
and taxonomy-record-active-as-of resolution reuses
`universe/upbit_shadow_validation_harness.py::_effective_taxonomy_category()`
unchanged.

Scope is bounded: only Upbit markets whose candidate canonical id already
has an *active-as-of-`evaluation_as_of`* record in
`config/upbit_exclusion_taxonomy.json` are considered at all (81 assets as
of 2026-08-29/30) -- CHIP and the ~200 other Upbit-only, taxonomy-uncovered
assets are explicitly out of scope for this WBS, not silently ignored.

This module NEVER:

* ratifies a registry -- every output stays `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`;
* changes `approval_status` on any taxonomy or policy config file;
* calls a live network/API at evaluation time (all independent evidence is
  pre-researched, hash-free-text data checked into this repo, dated with
  `researched_at`/`valid_until` for staleness checking);
* grants investable/PAPER/order/Production/Trading authority.
"""
from __future__ import annotations

import copy
import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "config" / "upbit_bounded_identity_evidence.json"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_id01", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_id01", "identity/upbit_market_identity_proposal.py")
HARNESS = _load_module("upbit_shadow_validation_harness_for_id01", "universe/upbit_shadow_validation_harness.py")

SCHEMA_VERSION = "upbit_bounded_identity_registry/1"
REVIEW_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"

VERDICT_VERIFIED = "VERIFIED_CANDIDATE"
VERDICT_HOLD_MISSING_SECOND_SOURCE = "HOLD_MISSING_SECOND_SOURCE"
VERDICT_HOLD_CONTRACT_MISMATCH = "HOLD_CONTRACT_MISMATCH"
VERDICT_HOLD_REBRAND_UNRESOLVED = "HOLD_REBRAND_UNRESOLVED"
VERDICT_HOLD_TICKER_COLLISION = "HOLD_TICKER_COLLISION"
VERDICT_HOLD_SOURCE_STALE = "HOLD_SOURCE_STALE"
VERDICT_HOLD_IDENTITY_COLLISION = "HOLD_IDENTITY_COLLISION_UNRESOLVED"
ALL_VERDICTS = {
    VERDICT_VERIFIED, VERDICT_HOLD_MISSING_SECOND_SOURCE, VERDICT_HOLD_CONTRACT_MISMATCH,
    VERDICT_HOLD_REBRAND_UNRESOLVED, VERDICT_HOLD_TICKER_COLLISION, VERDICT_HOLD_SOURCE_STALE,
    VERDICT_HOLD_IDENTITY_COLLISION,
}

# CIO-directed (2026-08-30): RE never promotes to the registry regardless of
# any evidence -- Kraken's own RATIFIED taxonomy already documents a real
# ticker collision on this exact canonical symbol. This mirrors
# universe/upbit_taxonomy_schema_eligible_candidate.py's own
# _HIGH_PRIORITY_HOLD_MARKETS pattern -- an explicit, named exception, never
# a silently-applied rule.
_FORCED_HOLDS = {
    "RE": VERDICT_HOLD_TICKER_COLLISION,
}

_ADDR_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


class BoundedIdentityRegistryError(ValueError):
    """Fail-closed P3-12-ID-01 violation."""


canonical_json = HARNESS.canonical_json
payload_sha256 = HARNESS.payload_sha256
file_sha256 = HARNESS.file_sha256
git_commit_sha = HARNESS.git_commit_sha
_effective_taxonomy_category = HARNESS._effective_taxonomy_category


def load_identity_evidence(path: Path = EVIDENCE_PATH) -> dict:
    """Load the curated, read-only-research identity evidence file. Never
    fetches anything live -- this is checked-in data, one research pass per
    asset, dated with `researched_at` for staleness checking. Returns the
    `{canonical_asset_id: evidence}` mapping `build_registry_candidate()`
    expects as `evidence_by_id`.
    """
    import json

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundedIdentityRegistryError(f"IDENTITY_EVIDENCE_READ_FAILED:{exc}") from exc
    if not isinstance(doc, dict) or not {"schema_version", "assets"}.issubset(doc):
        raise BoundedIdentityRegistryError("IDENTITY_EVIDENCE_FIELDS_INVALID")
    if doc.get("review_status") == "RATIFIED":
        raise BoundedIdentityRegistryError("IDENTITY_EVIDENCE_MUST_NEVER_BE_RATIFIED")
    return doc["assets"]


def normalize_contract_address(value: str | None) -> str | None:
    """Deterministic, format-insensitive normalization: strip surrounding
    whitespace, lowercase, and lowercase any ``chain:0xHEX`` style prefix
    the evidence text used, so two evidence rows that name the same address
    in different case/formatting never look like a mismatch. Never
    validates checksum -- that would require a live call this module
    deliberately never makes.
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned.lower()


def _is_stale(evidence: dict, evaluation_as_of: str) -> bool:
    """Identity-evidence freshness is deliberately decoupled from market-data
    freshness: unlike a price/turnover snapshot, an identity fact (is Upbit's
    market the same project as an independent source) does not go stale on
    an hourly basis, and research is normally conducted AFTER the market
    snapshot/taxonomy draft it corroborates -- that is the expected order of
    operations for this WBS, not a data integrity problem. So a
    ``researched_at`` date later than ``evaluation_as_of`` is NOT itself
    treated as invalid.

    What DOES fail closed:

    * a missing ``researched_at`` -- no provenance date recorded at all;
    * an explicit ``effective_from`` (when the reviewer set one) that is
      still in the future relative to ``evaluation_as_of`` -- evidence the
      reviewer themselves marked "not applicable yet";
    * an explicit ``valid_until`` that has already passed as of
      ``evaluation_as_of`` -- evidence the reviewer themselves marked
      expired.
    """
    researched_at = evidence.get("researched_at")
    if not isinstance(researched_at, str):
        return True  # missing provenance date -- fail closed, never assume fresh
    effective_from = evidence.get("effective_from")
    if isinstance(effective_from, str) and effective_from > evaluation_as_of:
        return True
    valid_until = evidence.get("valid_until")
    if valid_until is not None and valid_until < evaluation_as_of:
        return True
    return False


def compute_verdict(canonical_id: str, evidence: dict | None, *, evaluation_as_of: str) -> tuple:
    """Pure function: (verdict, basis). Never raises on missing/incomplete
    evidence -- absence of proof is always a HOLD, never an error.
    """
    if canonical_id in _FORCED_HOLDS:
        return _FORCED_HOLDS[canonical_id], (
            f"CIO-directed forced hold for {canonical_id}: an independently RATIFIED registry "
            "already documents a real ticker collision on this exact canonical symbol; never "
            "promoted regardless of any research evidence."
        )

    if not evidence:
        return VERDICT_HOLD_MISSING_SECOND_SOURCE, "No independent evidence record exists for this canonical id."

    override = evidence.get("manual_override_verdict")
    if override:
        if override not in ALL_VERDICTS:
            raise BoundedIdentityRegistryError(f"MANUAL_OVERRIDE_VERDICT_INVALID:{canonical_id}:{override}")
        return override, evidence.get("manual_override_reason") or "Reviewer-curated manual override."

    if _is_stale(evidence, evaluation_as_of):
        return VERDICT_HOLD_SOURCE_STALE, (
            f"Evidence researched_at={evidence.get('researched_at')!r} / "
            f"effective_from={evidence.get('effective_from')!r} / "
            f"valid_until={evidence.get('valid_until')!r} is not valid as of {evaluation_as_of}."
        )

    if not evidence.get("official_project_sources"):
        return VERDICT_HOLD_MISSING_SECOND_SOURCE, "No official independent source URL recorded."

    if evidence.get("ticker_collision_risk") is True:
        return VERDICT_HOLD_TICKER_COLLISION, "Research found evidence of a distinct project sharing this ticker."

    confidence = evidence.get("name_match_confidence")
    if confidence != "high":
        return VERDICT_HOLD_MISSING_SECOND_SOURCE, (
            f"name_match_confidence={confidence!r} does not meet the 'high' bar required for promotion."
        )

    if evidence.get("rebrand_or_token_swap_history") and evidence.get("rebrand_resolved") is False:
        return VERDICT_HOLD_REBRAND_UNRESOLVED, (
            "A rebrand/token-swap history was found and explicitly marked unresolved by the reviewer."
        )

    if evidence.get("asset_type") == "token" and not evidence.get("chain_or_platform"):
        return VERDICT_HOLD_MISSING_SECOND_SOURCE, (
            "asset_type='token' but no chain/platform was independently confirmed."
        )

    basis_parts = [f"name_match_confidence=high", f"asset_type={evidence.get('asset_type')}"]
    if evidence.get("chain_or_platform"):
        basis_parts.append(f"chain_or_platform={evidence['chain_or_platform']}")
    if evidence.get("contract_address"):
        basis_parts.append("contract_address confirmed")
    if evidence.get("rebrand_or_token_swap_history"):
        basis_parts.append("rebrand history confirmed resolved")
    return VERDICT_VERIFIED, "; ".join(basis_parts)


def _upbit_evidence_row(core: dict, capture_contract: dict, market: str) -> dict:
    entry = core["markets"][market]
    market_all_file = capture_contract["market_all_raw_file"]
    return {
        "market": market,
        "korean_name": entry.get("korean_name"),
        "english_name": entry.get("english_name"),
        "source_url": capture_contract["market_all_endpoint"],
        "response_sha256": core["component_hashes"][market_all_file],
        "available_at": core["available_at"],
    }


def _independent_evidence_row(evidence: dict | None) -> dict | None:
    if not evidence:
        return None
    return {
        "official_project_sources": list(evidence.get("official_project_sources") or []),
        "asset_type": evidence.get("asset_type"),
        "chain_or_platform": evidence.get("chain_or_platform"),
        "contract_address": normalize_contract_address(evidence.get("contract_address")),
        "rebrand_or_token_swap_history": evidence.get("rebrand_or_token_swap_history"),
        "rebrand_resolved": evidence.get("rebrand_resolved"),
        "ticker_collision_risk": evidence.get("ticker_collision_risk"),
        "name_match_confidence": evidence.get("name_match_confidence"),
        "notes": evidence.get("notes"),
        "researched_at": evidence.get("researched_at"),
        "effective_from": evidence.get("effective_from"),
        "valid_until": evidence.get("valid_until"),
    }


def build_registry_candidate(
    *, core: dict, capture_contract: dict, taxonomy: dict, proposals: list, blocked_markets: set,
    evidence_by_id: dict, evaluation_as_of: str,
) -> dict:
    """Pure function of its arguments. Scope is exactly the Upbit markets
    whose candidate canonical id has an active-as-of-`evaluation_as_of`
    taxonomy record -- everything else (CHIP, the ~200 other Upbit-only
    assets) is out of scope and never appears in either output list.
    """
    # A genuine DUPLICATE_CANONICAL_TARGET collision is expected to already
    # be reflected in `blocked_markets` (computed upstream by the caller via
    # identity_review_findings()/blocked_markets() -- this module has no
    # parallel collision-detection logic of its own). Group by candidate id
    # first (never collapsing to a single market early) so that EVERY
    # colliding market is individually accounted for in hold_list below --
    # never silently dropped.
    proposals_by_candidate: dict = {}
    for proposal in proposals:
        candidate = proposal["claim"]["candidateCanonicalAssetId"]
        market = proposal["claim"]["upbitMarket"]
        proposals_by_candidate.setdefault(candidate, []).append(market)

    registry_candidates = []
    hold_list = []
    evidence_rows = []
    seen_markets: set = set()

    for row in sorted(taxonomy["records"], key=lambda r: r["canonical_asset_id"]):
        canonical_id = row["canonical_asset_id"]
        category = _effective_taxonomy_category(canonical_id, evaluation_as_of, taxonomy)
        if category is None:
            continue  # not active as of evaluation_as_of -- out of scope, not held

        candidate_markets = proposals_by_candidate.get(canonical_id, [])
        if not candidate_markets:
            hold_list.append({
                "market": None, "canonical_asset_id": canonical_id,
                "verdict": VERDICT_HOLD_MISSING_SECOND_SOURCE,
                "verdict_basis": "No Upbit market currently proposes this canonical id.",
            })
            continue

        blocked_here = [m for m in candidate_markets if m in blocked_markets]
        unblocked_here = [m for m in candidate_markets if m not in blocked_markets]

        if blocked_here:
            # Every colliding market for this candidate id is held
            # individually -- never silently dropped -- regardless of
            # whether some other market for the same id was NOT blocked
            # (that would itself indicate identity_review_findings() failed
            # to block the whole collision set, a fail-closed bug worth
            # surfacing loudly rather than partially promoting one side).
            for market in blocked_here:
                if market in seen_markets:
                    raise BoundedIdentityRegistryError(f"DUPLICATE_MARKET_IN_TAXONOMY_RECORDS:{market}")
                seen_markets.add(market)
                hold_list.append({
                    "market": market, "canonical_asset_id": canonical_id,
                    "verdict": VERDICT_HOLD_IDENTITY_COLLISION,
                    "verdict_basis": "Unresolved DUPLICATE_CANONICAL_TARGET identity collision.",
                })
            if unblocked_here:
                raise BoundedIdentityRegistryError(
                    f"PARTIAL_COLLISION_BLOCK_UNEXPECTED:{canonical_id}:blocked={blocked_here}:unblocked={unblocked_here}"
                )
            continue

        if len(unblocked_here) > 1:
            raise BoundedIdentityRegistryError(
                f"DUPLICATE_CANONICAL_TARGET_UNEXPECTED_IN_ID01_SCOPE:{canonical_id}:{unblocked_here}"
            )

        market = unblocked_here[0]
        if market in seen_markets:
            raise BoundedIdentityRegistryError(f"DUPLICATE_MARKET_IN_TAXONOMY_RECORDS:{market}")
        seen_markets.add(market)

        evidence = evidence_by_id.get(canonical_id)
        verdict, basis = compute_verdict(canonical_id, evidence, evaluation_as_of=evaluation_as_of)
        upbit_evidence = _upbit_evidence_row(core, capture_contract, market)
        independent_evidence = _independent_evidence_row(evidence)

        evidence_rows.append({
            "market": market, "canonical_asset_id": canonical_id, "taxonomy_category": category,
            "verdict": verdict, "verdict_basis": basis, "evaluation_as_of": evaluation_as_of,
            "upbit_evidence": upbit_evidence, "independent_evidence": independent_evidence,
        })

        if verdict == VERDICT_VERIFIED:
            registry_candidates.append({
                "market": market, "canonical_asset_id": canonical_id,
                "effective_from": evaluation_as_of, "effective_to": None,
            })
        else:
            hold_list.append({"market": market, "canonical_asset_id": canonical_id, "verdict": verdict, "verdict_basis": basis})

    registry_candidates.sort(key=lambda r: r["market"])
    hold_list.sort(key=lambda r: (r["market"] or "", r["canonical_asset_id"]))
    evidence_rows.sort(key=lambda r: r["market"])

    return {"registry_candidates": registry_candidates, "hold_list": hold_list, "evidence": evidence_rows}


def registry_candidate_as_mapping(registry_candidates: list) -> dict:
    """``{upbit_market: canonical_asset_id}`` shape, the exact input shape
    `universe/upbit_tradeable_universe.py::build_classification()`'s
    ``ratified_identity_registry`` argument expects. Never written to disk;
    exists only for in-memory shadow re-evaluation.
    """
    return {row["market"]: row["canonical_asset_id"] for row in registry_candidates}


def shadow_apply_funnel(
    *, core: dict, real_policy: dict, real_taxonomy: dict, registry_mapping: dict,
    blocked_markets: set, evaluation_as_of: str,
) -> dict:
    """Re-runs the exact P3-12 shadow-apply mechanism
    (`universe/upbit_shadow_validation_harness.py::shadow_ratify()` +
    `universe/upbit_tradeable_universe.py::build_classification()`,
    BOTH unmodified) with this WBS's own VERIFIED_CANDIDATE-only identity
    registry substituted for the shadow harness's own broader
    "every non-colliding proposal" registry. No parallel classification
    logic -- this is the same real classifier, same shadow-apply discipline,
    a narrower identity input only.
    """
    shadow_policy_doc = HARNESS.shadow_ratify(real_policy)
    shadow_taxonomy_doc = HARNESS.shadow_ratify(real_taxonomy)
    return UNI.build_classification(
        core, evaluation_as_of=evaluation_as_of, policy=shadow_policy_doc, taxonomy=shadow_taxonomy_doc,
        ratified_identity_registry=registry_mapping, blocked_markets=blocked_markets,
    )
