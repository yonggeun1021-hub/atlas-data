#!/usr/bin/env python3
"""P3-12-TAX-01 -- Upbit taxonomy schema & eligible-content candidate builder.

The P3-12 Shadow Validation Harness (`universe/upbit_shadow_validation_harness.py`)
found the single bottleneck blocking every P3-12 funnel scenario: even if
policy/taxonomy/identity were ratified exactly as currently written,
``config/upbit_exclusion_taxonomy.json`` carries **zero `eligible_category`
records** -- only 6 stablecoin exclusions -- so `unknown_asset_policy:
fail_closed_unknown` catches nearly every real asset as `TAXONOMY_UNKNOWN`.

This module deterministically drafts the taxonomy content that would close
that gap, following CIO-ratified classification principles (2026-08-30):

* A market's candidate canonical asset id gets a **new draft record ONLY
  if** an independently RATIFIED registry -- `config/crypto_breadth_exclusion_taxonomy.json`
  -- already carries an *effective-dated-active-as-of-evaluation_as_of*
  record for that exact same canonical id. Name/symbol pattern hints alone
  (e.g. a market's name merely containing "USD") are NEVER sufficient to
  draft a record here -- they only ever route a market to the hold list for
  human research. This is the single mechanical gate every classification
  in this module goes through; there is no per-asset special case.
* ``commodity_linked`` is added as a new excluded category (mirroring the
  Kraken taxonomy's own precedent) because at least one real Upbit market
  (XAUT / "Tether Gold") is a commodity-linked token with no home in
  Upbit's existing category schema.
* ``leveraged``/``derivative_like`` remain reserved excluded categories
  with **no drafted records** -- neither this repository nor Kraken's own
  taxonomy has ever defined criteria for them, and no Upbit market in
  today's snapshot has independent corroboration for either category. A
  future ratification of those criteria is a separate, prior decision.
* A market whose candidate canonical id has NO independent corroboration at
  all (the majority of Upbit-only listings) is never guessed into
  `eligible_crypto` -- it is routed to the hold list, fail-closed, and the
  real taxonomy leaves it `TAXONOMY_UNKNOWN` until a human adds evidence.
* A market with an unresolved `DUPLICATE_CANONICAL_TARGET` identity
  collision is never drafted into any category -- collision resolution is
  strictly upstream of taxonomy classification.

This module NEVER:

* changes `approval_status` on the taxonomy document it drafts (stays
  exactly what the input document already had -- see `build_candidate()`);
* invents a classification criterion beyond "does an independently RATIFIED
  registry already say so, right now";
* calls the Upbit order/withdrawal/private API (reads the same
  already-captured, hash-validated public snapshot every other P3-12
  module reads);
* grants investable/PAPER/order/Production/Trading authority.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_tax01", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_tax01", "identity/upbit_market_identity_proposal.py")
HARNESS = _load_module("upbit_shadow_validation_harness_for_tax01", "universe/upbit_shadow_validation_harness.py")

SCHEMA_VERSION = "upbit_taxonomy_schema_eligible_candidate/1"
NEW_CATEGORY_COMMODITY_LINKED = "commodity_linked"
GENERATION_RULE = "KRAKEN_ACTIVE_RECORD_CATEGORY_MATCH"

# CIO-ratified (2026-08-30) explicit exceptions -- never derived from a name
# pattern, always a hold regardless of what an independent registry would
# otherwise suggest.
_HIGH_PRIORITY_HOLD_MARKETS = {
    "KRW-RE": "IDENTITY_TICKER_COLLISION_PRECEDENT_KRAKEN_UNVERIFIED_IDENTITY",
}
_NO_AUTO_CLASSIFY_MARKETS = {
    # CHIP ("USD.AI") must never become `stablecoin` purely because its name
    # contains "USD" -- no official issuer/independent corroboration exists.
    "KRW-CHIP": "NO_INDEPENDENT_STABLECOIN_ISSUER_CORROBORATION",
}


class TaxonomyCandidateError(ValueError):
    """Fail-closed P3-12-TAX-01 candidate-builder violation."""


canonical_json = HARNESS.canonical_json
payload_sha256 = HARNESS.payload_sha256
file_sha256 = HARNESS.file_sha256
git_commit_sha = HARNESS.git_commit_sha
load_kraken_breadth_taxonomy = HARNESS.load_kraken_breadth_taxonomy
_active_kraken_record = HARNESS._active_kraken_record


def _upbit_listing_evidence(core: dict, capture_contract: dict, market: str) -> dict:
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


def _kraken_corroboration_evidence(record: dict) -> dict:
    return {
        "canonical_asset_id": record["canonical_asset_id"],
        "category": record["category"],
        "effective_from": record.get("effective_from"),
        "effective_to": record.get("effective_to"),
        "reason": record.get("reason"),
        "source": "config/crypto_breadth_exclusion_taxonomy.json (RATIFIED v2)",
    }


def _draft_reason(category: str, *, candidate_id: str, upbit_evidence: dict, kraken_evidence: dict,
                   as_of: str) -> str:
    market = upbit_evidence["market"]
    english = upbit_evidence.get("english_name")
    confirmed = (
        f"Confirmed live on Upbit {market} (english_name={english}) {as_of} via GET /v1/market/all."
    )
    kraken_reason = (kraken_evidence["reason"] or "").rstrip()
    if kraken_reason and not kraken_reason.endswith((".", "!", "?")):
        kraken_reason += "."
    corroborated = (
        f"Corroborated as '{category}' by config/crypto_breadth_exclusion_taxonomy.json "
        f"(RATIFIED v2, active as of {as_of}): {kraken_reason}"
    )
    if category == "unverified_identity":
        return (
            f"{english} -- ticker identity conflict risk. {corroborated} Applied conservatively to "
            f"Upbit {market} pending a second independent source distinguishing Upbit's listed "
            f"project from the collision Kraken's taxonomy documents for canonical id {candidate_id}. "
            "A manual identity-layer hold is retained regardless of this taxonomy classification -- "
            "see the shadow harness's identity_review.manual_review_queue."
        )
    if category == NEW_CATEGORY_COMMODITY_LINKED:
        return (
            f"{english} -- commodity-linked token, not a cryptocurrency-native asset. {corroborated} "
            f"{confirmed}"
        )
    if category == "stablecoin":
        return f"{english} -- USD/EUR-pegged stablecoin. {corroborated} {confirmed}"
    # eligible_crypto and any other future category: a neutral, generic
    # confirmation string, never inventing per-asset narrative text.
    return f"{corroborated} {confirmed}"


def build_candidate(
    *, core: dict, capture_contract: dict, real_taxonomy: dict, kraken_records_by_id: dict,
    proposals: list, blocked_markets: set, evaluation_as_of: str,
) -> dict:
    """Pure function of its arguments. Returns a dict with:

    - ``candidate_taxonomy``: the full draft taxonomy document (deep copy of
      ``real_taxonomy`` with ``commodity_linked`` added to
      ``excluded_categories`` if not already present, and new records
      appended -- ``approval_status`` is copied byte-for-byte unchanged).
    - ``new_records``: exactly the records appended, in the same order.
    - ``evidence``: one entry per new record with full Upbit+Kraken lineage.
    - ``hold_list``: markets that were NOT drafted into any new record,
      each with an explicit reason.
    - ``schema_gaps``: Kraken-corroborated categories with no Upbit taxonomy
      equivalent, even after adding ``commodity_linked``.
    """
    existing_active_ids = set()
    for row in real_taxonomy["records"]:
        category = HARNESS._effective_taxonomy_category(row["canonical_asset_id"], evaluation_as_of, real_taxonomy)
        if category is not None:
            existing_active_ids.add(row["canonical_asset_id"])

    candidate_taxonomy = copy.deepcopy(real_taxonomy)
    excluded_categories = list(candidate_taxonomy["excluded_categories"])
    if NEW_CATEGORY_COMMODITY_LINKED not in excluded_categories:
        excluded_categories.append(NEW_CATEGORY_COMMODITY_LINKED)
    candidate_taxonomy["excluded_categories"] = excluded_categories
    allowed_categories = {candidate_taxonomy["eligible_category"], *excluded_categories}

    new_records = []
    new_record_ids: dict = {}  # canonical_asset_id -> market, to catch an in-run duplicate
    evidence = []
    hold_list = []
    schema_gaps = []

    for proposal in sorted(proposals, key=lambda p: p["claim"]["upbitMarket"]):
        market = proposal["claim"]["upbitMarket"]
        candidate_id = proposal["claim"]["candidateCanonicalAssetId"]

        if market in blocked_markets:
            hold_list.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "reason": "IDENTITY_COLLISION_UNRESOLVED", "disposition": "NO_RECORD_DRAFTED",
            })
            continue

        if candidate_id in existing_active_ids:
            # Already covered by an existing, untouched taxonomy record --
            # never duplicated, never re-derived.
            continue

        if market in _NO_AUTO_CLASSIFY_MARKETS:
            hold_list.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "reason": _NO_AUTO_CLASSIFY_MARKETS[market], "disposition": "NO_RECORD_DRAFTED",
            })
            continue

        kraken_raw = kraken_records_by_id.get(candidate_id)
        kraken_record = _active_kraken_record(candidate_id, evaluation_as_of, kraken_records_by_id)
        if kraken_raw is not None and kraken_record is None:
            # Present in the registry, but not active as of evaluation_as_of
            # (not-yet-effective or expired) -- explicitly surfaced, never
            # silently treated as either "corroborated" or "absent".
            hold_list.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "reason": "CONFLICTING_OR_STALE_KRAKEN_RECORD", "disposition": "NO_RECORD_DRAFTED",
                "kraken_record_effective_from": kraken_raw.get("effective_from"),
                "kraken_record_effective_to": kraken_raw.get("effective_to"),
            })
            continue

        if kraken_record is None:
            reason = _HIGH_PRIORITY_HOLD_MARKETS.get(market, "NO_INDEPENDENT_CORROBORATION_UPBIT_ONLY")
            hold_list.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "reason": reason, "disposition": "NO_RECORD_DRAFTED",
            })
            continue

        category = kraken_record["category"]
        if category not in allowed_categories:
            schema_gaps.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "kraken_ratified_category": category,
                "issue": (
                    f"Kraken RATIFIED taxonomy classifies {candidate_id} as '{category}', which has "
                    "no equivalent in Upbit's taxonomy schema even after adding commodity_linked -- "
                    "a prior CIO decision is needed before this asset can be classified at all."
                ),
            })
            hold_list.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "reason": "TAXONOMY_SCHEMA_GAP", "disposition": "NO_RECORD_DRAFTED",
            })
            continue

        if candidate_id in new_record_ids:
            raise TaxonomyCandidateError(
                f"CANDIDATE_DUPLICATE_CANONICAL_ID:{candidate_id}:{new_record_ids[candidate_id]}:{market}"
            )
        new_record_ids[candidate_id] = market

        upbit_evidence = _upbit_listing_evidence(core, capture_contract, market)
        kraken_evidence = _kraken_corroboration_evidence(kraken_record)
        record = {
            "canonical_asset_id": candidate_id,
            "category": category,
            "effective_from": evaluation_as_of,
            "effective_to": None,
            "reason": _draft_reason(
                category, candidate_id=candidate_id, upbit_evidence=upbit_evidence,
                kraken_evidence=kraken_evidence, as_of=evaluation_as_of,
            ),
        }
        new_records.append(record)
        evidence.append({
            "canonical_asset_id": candidate_id,
            "category": category,
            "effective_from": evaluation_as_of,
            "upbit_market": market,
            "upbit_listing_evidence": upbit_evidence,
            "kraken_corroboration": kraken_evidence,
            "generation_rule": GENERATION_RULE,
            "evaluation_as_of": evaluation_as_of,
        })
        if market in _HIGH_PRIORITY_HOLD_MARKETS:
            # Gets a draft record AND stays on the hold list -- CIO
            # explicitly asked both to be true for identity-ambiguous ids.
            hold_list.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "reason": _HIGH_PRIORITY_HOLD_MARKETS[market],
                "disposition": f"DRAFT_RECORD_PREPARED_CATEGORY_{category.upper()}_STILL_NEEDS_CIO_SIGNOFF",
            })

    new_records.sort(key=lambda row: row["canonical_asset_id"])
    evidence.sort(key=lambda row: row["canonical_asset_id"])
    candidate_taxonomy["records"] = candidate_taxonomy["records"] + new_records
    hold_list.sort(key=lambda row: row["market"])
    schema_gaps.sort(key=lambda row: row["market"])

    return {
        "candidate_taxonomy": candidate_taxonomy,
        "new_records": new_records,
        "evidence": evidence,
        "hold_list": hold_list,
        "schema_gaps": schema_gaps,
    }
