#!/usr/bin/env python3
"""P3-12 historical pre-ratification Shadow Validation Harness.

Answers exactly one historical question for the CIO, repeatably: *if the
then-PROPOSED_UNRATIFIED Upbit tradeable-universe policy, exclusion taxonomy, and
per-market identity proposals were ratified exactly as they are currently
written -- no threshold changed, no taxonomy category added, no identity
guessed -- what would the resulting universe/PAPER-eligibility funnel look
like, and which markets still cannot be judged at all?*

This module NEVER:

* writes to any canonical config file (`config/upbit_tradeable_universe_policy.json`,
  `config/upbit_exclusion_taxonomy.json`, `config/upbit_asset_identity_exceptions.json`);
* changes a threshold or taxonomy category definition (every numeric limit and
  category name used below is read verbatim from the committed, still-unratified
  config files -- see `shadow_ratify()`);
* calls the Upbit order/withdrawal/private API (it only reads the same
  already-captured, hash-validated public snapshot `universe/upbit_tradeable_universe.py`
  itself reads);
* grants investable/PAPER/order/Production/Trading authority (every `authority`
  field below is hardcoded `False`, same discipline as
  `identity/upbit_market_identity_proposal.py` and
  `universe/upbit_tradeable_universe.py`).

The "shadow apply" step builds two in-memory-only documents that are never
written to disk: a copy of the real policy/taxonomy with `approval_status`
forced to `RATIFIED`, and a hypothetical `{market: canonical_asset_id}`
identity registry built purely from today's own PROPOSED_UNRATIFIED identity
proposals (excluding every market with an unresolved `DUPLICATE_CANONICAL_TARGET`
collision, which is never guessed). Those in-memory documents are fed into
the exact same `universe/upbit_tradeable_universe.py::build_classification()`
the real production populate script calls -- no parallel/duplicate
classification logic exists here, so this harness can never silently drift
from the real gate semantics it is shadowing.

Everything else this module adds on top -- the taxonomy category audit, the
identity cross-reference signal, the manual-review queue, the slippage
curve -- is read-only analysis over that one classification result, clearly
labeled informational, and never fed back into a gating decision.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_shadow_harness", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_shadow_harness", "identity/upbit_market_identity_proposal.py")
# CIO review (2026-08-29, PR #459): reuse the already-tested Kraken breadth
# taxonomy fail-closed contract (RATIFIED status, exact policy_version pin,
# category vocabulary, canonical-id duplicate rejection, effective-interval
# validation) instead of maintaining a parallel, looser validator here.
GAP_INVENTORY = _load_module(
    "candidate_identity_gap_inventory_for_shadow_harness", "identity/candidate_identity_gap_inventory.py",
)

RAW_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "raw"
POLICY_PATH = UNI.POLICY_PATH
TAXONOMY_PATH = UNI.TAXONOMY_PATH
IDENTITY_EXCEPTIONS_PATH = ROOT / "config" / "upbit_asset_identity_exceptions.json"
KRAKEN_BREADTH_TAXONOMY_PATH = ROOT / "config" / "crypto_breadth_exclusion_taxonomy.json"

SCHEMA_VERSION = "upbit_p3_12_shadow_validation_harness/1"
REVIEW_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"

# Only these three excluded categories have any ratified worked-example
# definition text anywhere in this repository (config/upbit_exclusion_taxonomy.json's
# own 6 stablecoin records, plus config/crypto_breadth_exclusion_taxonomy.json's
# wrapped/unverified_identity records). "leveraged" and "derivative_like" are
# listed in Upbit's excluded_categories but have zero ratified records or
# defined criteria anywhere -- surfaced as an explicit gap, never silently
# invented here.
_CATEGORIES_WITH_RATIFIED_DEFINITION_PRECEDENT = {"stablecoin", "wrapped", "unverified_identity"}

_LIQUIDITY_REASONS = {
    "LISTING_HISTORY_BELOW_THRESHOLD", "TURNOVER_HISTORY_INCOMPLETE", "TURNOVER_BELOW_THRESHOLD",
}
_SPREAD_REASONS = {"SPREAD_NOT_COMPUTABLE", "SPREAD_ABOVE_THRESHOLD"}
_SLIPPAGE_REASONS = {"SLIPPAGE_NOT_COMPUTABLE", "SLIPPAGE_ABOVE_THRESHOLD"}
_FRESHNESS_REASONS = {"STALE_CAPTURE"}
_NOT_COMPUTABLE_REASONS = {"SPREAD_NOT_COMPUTABLE", "SLIPPAGE_NOT_COMPUTABLE"}

# Coarse, mechanical name patterns only -- every hit is surfaced as a
# CANDIDATE for human taxonomy review, never auto-applied as a category. A
# symbol-suffix heuristic for "leveraged" tickers (e.g. matching a trailing
# "UP"/"DOWN"/"3L"/"3S") was deliberately NOT added here: tried against
# today's real 282-market Upbit KRW list, it false-positived on ordinary,
# already Kraken-corroborated eligible_crypto governance tokens (e.g. "JUP",
# "SYRUP", both merely ending in the letters "UP") with no way to
# distinguish those from a real leveraged-token ticker convention absent a
# ratified base-asset registry to check the prefix against. Only explicit,
# whole-word descriptive text in the market's own name is used instead --
# conservative by design, since no ratified "leveraged" category definition
# exists anywhere in this repository yet (see category_definition_gaps).
_STABLE_NAME_HINTS = ("usd", "dollar", "eur", "stablecoin", "pegged")
_WRAPPED_NAME_RE = re.compile(r"wrapped", re.IGNORECASE)
_DERIVATIVE_NAME_RE = re.compile(r"perpetual|futures|\boption\b|derivative", re.IGNORECASE)
_LEVERAGE_NAME_RE = re.compile(r"\bleveraged\b|\bbull\b|\bbear\b|\blong\b|\bshort\b", re.IGNORECASE)

_FLAG_TO_CATEGORY = {
    "stablecoin_name_pattern": "stablecoin",
    "wrapped_name_pattern": "wrapped",
    "leveraged_name_pattern": "leveraged",
    "derivative_like_name_pattern": "derivative_like",
}


class ShadowValidationHarnessError(ValueError):
    """Fail-closed P3-12 shadow validation harness violation."""


# ---------------------------------------------------------------------------
# Small local helpers -- reuse UNI's public canonical_json/payload_sha256
# (identical hashing discipline everywhere in this repo) rather than
# duplicating hash logic.
# ---------------------------------------------------------------------------

canonical_json = UNI.canonical_json
payload_sha256 = UNI.payload_sha256


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ShadowValidationHarnessError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def git_commit_sha(root: Path = ROOT) -> str:
    """Exact commit SHA of the working tree this evaluation ran against.

    Mirrors ``rules/ratified_rule_decision.py``'s ``_git()`` /
    ``portfolio/profit_harvest_readiness.py``'s ``current_source_commit()``
    idiom: a real ``git`` subprocess call, never a swallowed exception --
    a git failure must fail this harness closed, not silently stamp an
    unverifiable or fabricated commit SHA into an evidence packet.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ShadowValidationHarnessError("CODE_COMMIT_SHA_UNAVAILABLE") from exc
    sha = result.stdout.decode("utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ShadowValidationHarnessError(f"CODE_COMMIT_SHA_MALFORMED:{sha!r}")
    return sha


def load_kraken_breadth_taxonomy(path: Path = KRAKEN_BREADTH_TAXONOMY_PATH) -> tuple:
    """Load + fail-closed validate the Kraken breadth exclusion taxonomy by
    delegating to the shared, already-tested contract
    ``identity/candidate_identity_gap_inventory.py::_load_taxonomy()``:
    ``approval_status == "RATIFIED"``, exact ``policy_version`` pin
    (``crypto_breadth_exclusion_taxonomy/v2``), eligible/excluded category
    vocabulary shape, every record's category is a member of that
    vocabulary, canonical-id duplicates rejected, and every
    effective_from/effective_to is a valid, non-reversed ISO date interval.
    No parallel/looser validator is maintained here.

    Returns ``(doc, records_by_canonical_id)``. ``records_by_canonical_id``
    has exactly one row per canonical id (duplicates already rejected) --
    whether that single row is *active* as of a given evaluation date is a
    separate, later check (see ``_active_kraken_record``), mirroring
    ``candidate_identity_gap_inventory.py``'s own ``_taxonomy_diagnostic``.
    """
    try:
        doc, records_by_id = GAP_INVENTORY._load_taxonomy(Path(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowValidationHarnessError(f"KRAKEN_BREADTH_TAXONOMY_READ_FAILED:{exc}") from exc
    except GAP_INVENTORY.CandidateIdentityGapInventoryError as exc:
        raise ShadowValidationHarnessError(f"KRAKEN_BREADTH_TAXONOMY_INVALID:{exc}") from exc
    return doc, records_by_id


def _active_kraken_record(canonical_id: str, as_of: str, kraken_records_by_id: dict) -> dict | None:
    """The single Kraken breadth-taxonomy record for ``canonical_id`` if (and
    only if) it is effective-dated *active* as of ``as_of`` -- not yet
    effective, or already expired, both resolve to ``None``, mirroring
    ``candidate_identity_gap_inventory.py::_taxonomy_diagnostic``'s own
    ``NO_RECORD`` handling. Never raises on a future/expired record; that is
    the normal, expected shape of an effective-dated registry, not a fault.
    """
    row = kraken_records_by_id.get(canonical_id)
    if row is None:
        return None
    effective_from = row.get("effective_from")
    effective_to = row.get("effective_to")
    if not isinstance(effective_from, str) or effective_from > as_of:
        return None
    if effective_to is not None and effective_to < as_of:
        return None
    return row


def load_identity_exceptions(path: Path = IDENTITY_EXCEPTIONS_PATH) -> dict | None:
    if not Path(path).exists():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Identity: proposals, shadow (never-written) registry, cross-reference signal
# ---------------------------------------------------------------------------

def build_identity_proposals(core: dict, capture_contract: dict, *, review_as_of: str,
                              exceptions_doc: dict | None = None) -> list:
    market_all_file = capture_contract["market_all_raw_file"]
    proposals = []
    for market, entry in sorted(core["markets"].items()):
        if not entry.get("market_all_available"):
            continue
        proposals.append(
            IDP.build_proposal(
                {"market": market, "korean_name": entry.get("korean_name"), "english_name": entry.get("english_name")},
                review_as_of=review_as_of,
                source_url=capture_contract["market_all_endpoint"],
                response_sha256=core["component_hashes"][market_all_file],
                available_at=core["available_at"],
                exceptions_doc=exceptions_doc,
            )
        )
    return proposals


def shadow_identity_registry(proposals: list, findings: list) -> dict:
    """Hypothetical, in-memory-only ``{market: canonical_asset_id}`` registry,
    as if every non-colliding PROPOSED_UNRATIFIED identity proposal already
    on file today were ratified exactly as written. Never written to disk.
    A market with an unresolved ``DUPLICATE_CANONICAL_TARGET`` finding is
    excluded, never guessed at.
    """
    blocked = IDP.blocked_markets(findings)
    return {
        proposal["claim"]["upbitMarket"]: proposal["claim"]["candidateCanonicalAssetId"]
        for proposal in proposals
        if proposal["claim"]["upbitMarket"] not in blocked
    }


def kraken_cross_reference_signal(proposals: list, kraken_records_by_id: dict, *, as_of: str) -> dict:
    """Per-market informational signal only: does an independent, already
    RATIFIED registry (Kraken breadth exclusion taxonomy) know this candidate
    canonical asset id, *active as of ``as_of``*, at all, and if so does it
    flag the id itself as ticker-collision ``unverified_identity``? A record
    that exists but is not yet effective or has already expired as of
    ``as_of`` is treated as absent, never as a stale-but-still-counted match.
    Never fed into ``build_classification`` -- production's own
    ``.github/scripts/upbit_universe_populate.py`` deliberately omits this
    exact cross-reference for the documented reason that Kraken's universe
    scope does not cover most legitimate Upbit-only assets (see that
    module's docstring). This harness surfaces it anyway, but strictly as a
    manual-review priority signal, never as a promotion/exclusion input.
    """
    signal = {}
    for proposal in proposals:
        market = proposal["claim"]["upbitMarket"]
        candidate = proposal["claim"]["candidateCanonicalAssetId"]
        record = _active_kraken_record(candidate, as_of, kraken_records_by_id)
        signal[market] = {
            "candidate_canonical_asset_id": candidate,
            "present_in_kraken_ratified_registry": record is not None,
            "kraken_ratified_category": record["category"] if record else None,
            "kraken_flagged_unverified_identity": bool(record and record["category"] == "unverified_identity"),
        }
    return signal


def identity_manual_review_queue(proposals: list, findings: list, cross_reference: dict) -> list:
    """High-value-only manual-review items -- an actual unresolved collision,
    or independent (Kraken-ratified) corroboration that this exact candidate
    canonical id has a known ticker-collision/ambiguous-identity problem
    elsewhere. Deliberately excludes the much larger "simply absent from the
    Kraken registry" tier (most legitimate Upbit-only assets hit that) as a
    line item -- that tier is reported only as an aggregate count in
    ``identity_review.cross_reference`` so the queue stays actionable.
    """
    blocked = IDP.blocked_markets(findings)
    queue = []
    for proposal in proposals:
        market = proposal["claim"]["upbitMarket"]
        candidate = proposal["claim"]["candidateCanonicalAssetId"]
        sig = cross_reference.get(market, {})
        reasons = []
        if market in blocked:
            reasons.append("DUPLICATE_CANONICAL_TARGET_COLLISION")
        if sig.get("kraken_flagged_unverified_identity"):
            reasons.append("KRAKEN_RATIFIED_UNVERIFIED_IDENTITY_SAME_CANONICAL_ID")
        if reasons:
            queue.append({
                "market": market,
                "candidate_canonical_asset_id": candidate,
                "reasons": reasons,
                "disposition": "NEEDS_MANUAL_REVIEW_BEFORE_IDENTITY_RATIFICATION",
            })
    queue.sort(key=lambda row: row["market"])
    return queue


# ---------------------------------------------------------------------------
# Shadow-apply: in-memory-only RATIFIED copies, never written to disk
# ---------------------------------------------------------------------------

def shadow_ratify(document: dict) -> dict:
    shadow = copy.deepcopy(document)
    shadow["approval_status"] = "RATIFIED"
    shadow["shadow_ratification_note"] = (
        "approval_status overridden to RATIFIED only inside this in-memory "
        "shadow evaluation copy. The committed config file on disk was never "
        "modified; see universe/upbit_shadow_validation_harness.py."
    )
    return shadow


def shadow_taxonomy_with_kraken_corroborated_eligible_records(
    taxonomy: dict, corroborated_eligible_ids: list, *, as_of: str,
) -> dict:
    """A SECOND, clearly-labeled hypothetical taxonomy, separate from the
    primary "ratify exactly as written" shadow scenario.

    ``config/upbit_exclusion_taxonomy.json`` today carries zero
    ``eligible_category`` records -- only exclusion records -- so under
    ``unknown_asset_policy: fail_closed_unknown`` almost every real asset
    resolves to ``TAXONOMY_UNKNOWN`` even once the file itself is ratified.
    This function illustrates what changes if the CIO *also* separately
    ratifies one additional, mechanically-derived batch of eligible_crypto
    records -- one per canonical asset id already independently corroborated
    by an existing RATIFIED registry (`config/crypto_breadth_exclusion_taxonomy.json`).
    It never invents a judgment: every added record's evidence is that
    external ratified corroboration, verbatim. This is exploratory-only,
    reported as a labeled supplemental scenario, never the primary shadow
    result, and never written to disk.
    """
    shadow = shadow_ratify(taxonomy)
    added = []
    for canonical_id in sorted(corroborated_eligible_ids):
        record = {
            "canonical_asset_id": canonical_id,
            "category": taxonomy["eligible_category"],
            "effective_from": as_of,
            "effective_to": None,
            "reason": (
                f"HYPOTHETICAL supplemental record for shadow-validation illustration only -- "
                f"never ratified. Corroborated as '{taxonomy['eligible_category']}' by the "
                f"independently RATIFIED config/crypto_breadth_exclusion_taxonomy.json (v2)."
            ),
        }
        shadow["records"] = shadow["records"] + [record]
        added.append(record)
    shadow["hypothetical_supplemental_records_added"] = len(added)
    return shadow


# ---------------------------------------------------------------------------
# Taxonomy category audit -- pattern-based CANDIDATE flags only, never an
# auto-applied category. Reuses the exact same effective-dated-record lookup
# semantics as universe/upbit_tradeable_universe.py::_taxonomy_category
# (duplicated here, read-only, audit-only -- the real gate's own private
# function remains the single source of truth for actual classification).
# ---------------------------------------------------------------------------

def _effective_taxonomy_category(canonical_asset_id: str, as_of: str, taxonomy: dict) -> str | None:
    matches = []
    for row in taxonomy["records"]:
        if row.get("canonical_asset_id") != canonical_asset_id:
            continue
        start = row.get("effective_from")
        end = row.get("effective_to")
        if not isinstance(start, str) or start > as_of:
            continue
        if end is not None and end < as_of:
            continue
        matches.append(row)
    if len(matches) > 1:
        raise ShadowValidationHarnessError(f"TAXONOMY_RECORD_OVERLAP:{canonical_asset_id}")
    return matches[0]["category"] if matches else None


def taxonomy_pattern_flags(korean_name, english_name) -> list:
    blob = f"{korean_name or ''} {english_name or ''}".lower()
    flags = []
    if any(hint in blob for hint in _STABLE_NAME_HINTS):
        flags.append("stablecoin_name_pattern")
    if _WRAPPED_NAME_RE.search(blob):
        flags.append("wrapped_name_pattern")
    if _LEVERAGE_NAME_RE.search(blob):
        flags.append("leveraged_name_pattern")
    if _DERIVATIVE_NAME_RE.search(blob):
        flags.append("derivative_like_name_pattern")
    return flags


def taxonomy_audit(core: dict, proposals: list, taxonomy: dict, kraken_records_by_id: dict, *, as_of: str) -> dict:
    already_recorded = []
    candidates = []
    schema_gaps = []
    corroborated_eligible_ids = set()

    for proposal in proposals:
        market = proposal["claim"]["upbitMarket"]
        candidate_id = proposal["claim"]["candidateCanonicalAssetId"]
        entry = core["markets"].get(market, {})
        existing_category = _effective_taxonomy_category(candidate_id, as_of, taxonomy)

        if existing_category is not None:
            already_recorded.append({
                "market": market, "candidate_canonical_asset_id": candidate_id,
                "existing_category": existing_category,
            })
            continue

        flags = taxonomy_pattern_flags(entry.get("korean_name"), entry.get("english_name"))
        # Only an effective-dated-active Kraken record is usable evidence --
        # a not-yet-effective or already-expired record is treated the same
        # as absent (see _active_kraken_record).
        kraken_record = _active_kraken_record(candidate_id, as_of, kraken_records_by_id)

        basis = []
        suggested_categories = {_FLAG_TO_CATEGORY[flag] for flag in flags}
        if flags:
            basis.append(f"name_pattern:{','.join(flags)}")
        if kraken_record is not None:
            kraken_category = kraken_record["category"]
            if kraken_category == taxonomy["eligible_category"]:
                # Positive corroboration only ("independently known-legitimate
                # asset") -- never a reason to flag this market for exclusion
                # review. Counted in aggregate, not listed as a candidate row.
                corroborated_eligible_ids.add(candidate_id)
            elif kraken_category in taxonomy["excluded_categories"]:
                basis.append(
                    f"kraken_ratified_registry_category={kraken_category} "
                    f"(config/crypto_breadth_exclusion_taxonomy.json RATIFIED v2; "
                    f"reason: {kraken_record.get('reason')})"
                )
                suggested_categories.add(kraken_category)
            else:
                basis.append(
                    f"kraken_ratified_registry_category={kraken_category} "
                    f"(config/crypto_breadth_exclusion_taxonomy.json RATIFIED v2; "
                    f"reason: {kraken_record.get('reason')})"
                )
                schema_gaps.append({
                    "market": market,
                    "candidate_canonical_asset_id": candidate_id,
                    "kraken_ratified_category": kraken_category,
                    "source_name_english": entry.get("english_name"),
                    "as_of": as_of,
                    "issue": (
                        f"config/crypto_breadth_exclusion_taxonomy.json (RATIFIED) classifies "
                        f"{candidate_id} as '{kraken_category}', a category absent from "
                        f"Upbit's own taxonomy schema (excluded_categories="
                        f"{taxonomy['excluded_categories']}, eligible_category="
                        f"'{taxonomy['eligible_category']}'). CIO decision needed before ratification: "
                        "add an equivalent category to config/upbit_exclusion_taxonomy.json, or "
                        "explicitly assign this asset to an existing category with its own reason."
                    ),
                })

        if not basis:
            continue
        candidates.append({
            "market": market,
            "candidate_canonical_asset_id": candidate_id,
            "source_name_korean": entry.get("korean_name"),
            "source_name_english": entry.get("english_name"),
            "as_of": as_of,
            "evidence_basis": basis,
            "suggested_categories": sorted(suggested_categories),
            "disposition": "CANDIDATE_NEEDS_TAXONOMY_REVIEW",
        })

    candidates.sort(key=lambda row: row["market"])
    schema_gaps.sort(key=lambda row: row["market"])
    already_recorded.sort(key=lambda row: row["market"])

    category_definition_gaps = sorted(
        category for category in taxonomy["excluded_categories"]
        if category not in _CATEGORIES_WITH_RATIFIED_DEFINITION_PRECEDENT
    )

    return {
        "already_recorded": already_recorded,
        "candidates": candidates,
        "schema_gaps": schema_gaps,
        "corroborated_eligible_count": len(corroborated_eligible_ids),
        "corroborated_eligible_canonical_asset_ids": sorted(corroborated_eligible_ids),
        "category_definition_gaps": category_definition_gaps,
        "category_definition_gap_note": (
            "These excluded_categories appear in config/upbit_exclusion_taxonomy.json but have "
            "zero ratified records or worked criteria anywhere in this repository -- this harness "
            "does not invent a definition for them. A market can only be pattern-flagged into one "
            "of these categories by an explicit keyword/symbol hint (see taxonomy_pattern_flags); "
            "ratifying a criteria definition for these categories is a separate, prior CIO decision."
        ) if category_definition_gaps else None,
    }


# ---------------------------------------------------------------------------
# Funnel / distribution / slippage-curve reporting -- read-only over an
# already-built classification packet, never a second gating mechanism.
# ---------------------------------------------------------------------------

def _reason_distribution(packet: dict) -> dict:
    buckets: dict = {}
    for row in packet["markets"]:
        buckets[row["reason"]] = buckets.get(row["reason"], 0) + 1
    return dict(sorted(buckets.items()))


def _gate_pass_fail_distribution(packet: dict) -> dict:
    def count(reason_set):
        return sum(1 for row in packet["markets"] if row["reason"] in reason_set)

    total = len(packet["markets"])
    return {
        "market_count": total,
        "liquidity": {"fail": count(_LIQUIDITY_REASONS), "not_failed_here": total - count(_LIQUIDITY_REASONS)},
        "spread": {"fail": count(_SPREAD_REASONS), "not_failed_here": total - count(_SPREAD_REASONS)},
        "slippage": {"fail": count(_SLIPPAGE_REASONS), "not_failed_here": total - count(_SLIPPAGE_REASONS)},
        "freshness": {"fail": count(_FRESHNESS_REASONS), "not_failed_here": total - count(_FRESHNESS_REASONS)},
    }


def _unresolved_no_data_items(packet: dict) -> list:
    items = [
        {"market": row["market"], "reason": row["reason"]}
        for row in packet["markets"]
        if row["reason"] and (row["reason"].startswith("MISSING_FIELD:") or row["reason"] in _NOT_COMPUTABLE_REASONS)
    ]
    items.sort(key=lambda row: row["market"])
    return items


def slippage_curve(core: dict, shadow_packet: dict, policy: dict, *, multiples=("0.5", "1", "3", "5")) -> list:
    """Estimated PAPER slippage at several multiples of the policy's own
    notional, for every market the shadow run reached TRADEABLE_UNIVERSE or
    better. Reuses the classifier's own private
    ``_estimate_slippage_bps`` unchanged (same precedent as
    ``microstructure/upbit_market_evidence.py``) so this can never silently
    drift from the real gate's math. Reporting only -- the policy's single
    official notional remains the sole gating value; no new threshold.
    """
    base_notional = Decimal(str(policy["paper_slippage_estimate_notional_krw"]))
    tradeable_or_better = {
        row["market"] for row in shadow_packet["markets"]
        if row["state"] in (UNI.STATE_TRADEABLE_UNIVERSE, UNI.STATE_PAPER_ELIGIBLE)
    }
    rows = []
    for market in sorted(tradeable_or_better):
        entry = core["markets"][market]
        best_ask = Decimal(str(entry["best_ask"]))
        curve = {}
        for multiple in multiples:
            notional = (base_notional * Decimal(multiple)).quantize(Decimal("1"))
            estimate = UNI._estimate_slippage_bps(entry["ask_levels"], best_ask, notional)
            curve[str(notional)] = str(estimate) if estimate is not None else None
        rows.append({"market": market, "slippage_bps_by_notional_krw": curve})
    return rows


# ---------------------------------------------------------------------------
# Top-level packet builder -- pure function of its arguments: no wall-clock
# or random value anywhere in this function. The same core snapshot,
# policy/taxonomy/exceptions/kraken-taxonomy documents, evaluation_as_of, and
# code_commit_sha always produce byte-identical output.
# ---------------------------------------------------------------------------

def build_shadow_packet(
    *, core: dict, capture_contract: dict, real_policy: dict, real_taxonomy: dict,
    exceptions_doc: dict | None, kraken_records_by_id: dict, evaluation_as_of: str,
    code_commit_sha: str, file_hashes: dict,
) -> dict:
    review_as_of = core["snapshot_date"]
    proposals = build_identity_proposals(core, capture_contract, review_as_of=review_as_of, exceptions_doc=exceptions_doc)
    findings = IDP.identity_review_findings(proposals, known_canonical_ids=None)
    blocked = IDP.blocked_markets(findings)
    registry = shadow_identity_registry(proposals, findings)
    cross_reference = kraken_cross_reference_signal(proposals, kraken_records_by_id, as_of=evaluation_as_of)

    # NOTE (CIO review, PR #459): this "before" baseline deliberately injects
    # `blocked_markets` computed from today's identity proposals, exactly as
    # real production does today (`.github/scripts/upbit_universe_populate.py
    # ::rebuild()` always passes today's mechanical DUPLICATE_CANONICAL_TARGET
    # collision set into build_classification, regardless of ratification
    # status -- collision detection needs no ratification to be safe to
    # enforce). This is why the field below is named to say so explicitly:
    # it is a faithful reproduction of today's real production output, not a
    # "ratification-free" hypothetical -- see funnel_definitions below.
    before_packet = UNI.build_classification(
        core, evaluation_as_of=evaluation_as_of, policy=real_policy, taxonomy=real_taxonomy,
        ratified_identity_registry={}, blocked_markets=blocked,
    )
    shadow_policy_doc = shadow_ratify(real_policy)
    shadow_taxonomy_doc = shadow_ratify(real_taxonomy)
    after_packet = UNI.build_classification(
        core, evaluation_as_of=evaluation_as_of, policy=shadow_policy_doc, taxonomy=shadow_taxonomy_doc,
        ratified_identity_registry=registry, blocked_markets=blocked,
    )

    audit = taxonomy_audit(core, proposals, real_taxonomy, kraken_records_by_id, as_of=evaluation_as_of)
    review_queue = identity_manual_review_queue(proposals, findings, cross_reference)
    present_count = sum(1 for row in cross_reference.values() if row["present_in_kraken_ratified_registry"])

    # Supplemental, clearly-hypothetical second scenario -- see
    # shadow_taxonomy_with_kraken_corroborated_eligible_records()'s docstring.
    # Never the primary reported result; illustrates what it would take to
    # actually unblock the funnel beyond ratifying today's file verbatim.
    supplemental_taxonomy_doc = shadow_taxonomy_with_kraken_corroborated_eligible_records(
        real_taxonomy, audit["corroborated_eligible_canonical_asset_ids"], as_of=evaluation_as_of,
    )
    supplemental_after_packet = UNI.build_classification(
        core, evaluation_as_of=evaluation_as_of, policy=shadow_policy_doc, taxonomy=supplemental_taxonomy_doc,
        ratified_identity_registry=registry, blocked_markets=blocked,
    )
    curve = slippage_curve(core, after_packet, real_policy)
    supplemental_curve = slippage_curve(core, supplemental_after_packet, real_policy)

    packet = {
        "schema_version": SCHEMA_VERSION,
        "review_status": REVIEW_STATUS,
        "snapshot_date": core["snapshot_date"],
        "evaluation_as_of": evaluation_as_of,
        "generated_at": core["available_at"],
        "code_commit_sha": code_commit_sha,
        "source": {
            "raw_snapshot_path": f"evidence/crypto/upbit/raw/{core['snapshot_date']}",
            "raw_manifest_sha256": core["manifest_sha256"],
            **file_hashes,
        },
        "shadow_apply_boundary": {
            "mutates_canonical_config_files": False,
            "identity_registry_source": (
                "in-memory hypothetical registry built purely from today's PROPOSED_UNRATIFIED "
                "identity proposals; never written to disk; excludes every colliding market"
            ),
            "policy_source": (
                "in-memory copy of config/upbit_tradeable_universe_policy.json with approval_status "
                "forced to RATIFIED for this evaluation only -- every threshold value unchanged"
            ),
            "taxonomy_source": (
                "in-memory copy of config/upbit_exclusion_taxonomy.json with approval_status forced "
                "to RATIFIED for this evaluation only -- every category/record unchanged"
            ),
            "thresholds_changed": False,
            "taxonomy_categories_or_records_changed": False,
        },
        "identity_review": {
            "proposal_count": len(proposals),
            "collision_findings": findings,
            "blocked_market_count": len(blocked),
            "shadow_registry_size": len(registry),
            "cross_reference": {
                "known_registry": "config/crypto_breadth_exclusion_taxonomy.json (RATIFIED v2, Kraken-scope only)",
                "informational_only_not_a_ratification_input": True,
                "present_in_registry_count": present_count,
                "absent_from_registry_count": len(proposals) - present_count,
            },
            "manual_review_queue": review_queue,
        },
        "taxonomy_audit": audit,
        "funnel": {
            "before_current_production_mechanical_collision_included": before_packet["summary"],
            "after_shadow_if_ratified_as_currently_proposed": after_packet["summary"],
        },
        "funnel_definitions": {
            "before_current_production_mechanical_collision_included": (
                "Exact reproduction of today's real production output "
                "(.github/scripts/upbit_universe_populate.py::rebuild()): "
                "ratified_identity_registry={} (no ratified registry exists), but "
                "blocked_markets IS populated from today's mechanical, "
                "ratification-independent DUPLICATE_CANONICAL_TARGET collision check "
                "-- production applies this collision hold today regardless of "
                "ratification status, so this baseline must too, to stay a faithful "
                "'before' reproduction rather than an idealized zero-collision one. "
                "Today's real collision count is 0, so this has no numeric effect on "
                "today's packet, but an identity-collision fixture would show up in "
                "this baseline too, not only in the shadow scenario below."
            ),
            "after_shadow_if_ratified_as_currently_proposed": (
                "In-memory-only RATIFIED policy/taxonomy copies (every threshold and "
                "category unchanged) plus the shadow identity registry built from "
                "today's non-colliding proposals -- see shadow_apply_boundary."
            ),
        },
        "funnel_supplemental_hypothetical": {
            "note": (
                "NOT the primary shadow result. config/upbit_exclusion_taxonomy.json ships with zero "
                "eligible_category records, so ratifying it exactly as written (the 'after' funnel "
                "above) still resolves almost every market to TAXONOMY_UNKNOWN. This scenario shows "
                "the additional, separate effect of also ratifying one mechanically-derived "
                "eligible_crypto record per canonical asset id already corroborated by the "
                "independently RATIFIED config/crypto_breadth_exclusion_taxonomy.json -- see "
                "shadow_taxonomy_with_kraken_corroborated_eligible_records()."
            ),
            "hypothetical_records_added": supplemental_taxonomy_doc["hypothetical_supplemental_records_added"],
            "after_with_kraken_corroborated_eligible_records": supplemental_after_packet["summary"],
            "slippage_curve_sample": supplemental_curve,
        },
        "reason_distribution": {
            "before_current_production_mechanical_collision_included": _reason_distribution(before_packet),
            "after": _reason_distribution(after_packet),
            "after_with_kraken_corroborated_eligible_records_hypothetical": _reason_distribution(supplemental_after_packet),
        },
        "gate_pass_fail_distribution": _gate_pass_fail_distribution(after_packet),
        "slippage_curve_sample": curve,
        "unresolved_no_data_items": _unresolved_no_data_items(after_packet),
        "markets_after_shadow_apply": after_packet["markets"],
        "authority": {
            "review_only": True,
            "canonical_config_mutation_authorized": False,
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "policy_ratification_authorized": False,
            "tradeable_universe_promotion_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "decision_eligible": False,
            "action_generation_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def evaluate(snapshot_date: str, *, raw_root: Path = RAW_ROOT, evaluation_as_of: str | None = None,
             code_commit_sha: str | None = None) -> dict:
    """Impure entry point: reads the already-committed, hash-validated raw
    snapshot and config files from disk, resolves the code commit SHA, then
    delegates to the pure ``build_shadow_packet``.
    """
    directory = Path(raw_root) / snapshot_date
    if not directory.is_dir():
        raise ShadowValidationHarnessError(f"RAW_SNAPSHOT_MISSING:{snapshot_date}")
    capture_contract = UNI.UPBIT_CAPTURE.load_contract()
    try:
        core = UNI.load_snapshot_core(directory, capture_contract)
    except UNI.UPBIT_CAPTURE.CaptureError as exc:
        raise ShadowValidationHarnessError(f"RAW_SNAPSHOT_INVALID:{snapshot_date}:{exc}") from exc

    real_policy = UNI.load_policy()
    real_taxonomy = UNI.load_taxonomy()
    exceptions_doc = load_identity_exceptions()
    _kraken_doc, kraken_records_by_id = load_kraken_breadth_taxonomy()
    resolved_commit = code_commit_sha or git_commit_sha()

    file_hashes = {
        "universe_policy_path": "config/upbit_tradeable_universe_policy.json",
        "universe_policy_file_sha256": file_sha256(POLICY_PATH),
        "taxonomy_path": "config/upbit_exclusion_taxonomy.json",
        "taxonomy_file_sha256": file_sha256(TAXONOMY_PATH),
        "kraken_breadth_taxonomy_path": "config/crypto_breadth_exclusion_taxonomy.json",
        "kraken_breadth_taxonomy_file_sha256": file_sha256(KRAKEN_BREADTH_TAXONOMY_PATH),
    }
    if exceptions_doc is not None:
        file_hashes["identity_exceptions_path"] = "config/upbit_asset_identity_exceptions.json"
        file_hashes["identity_exceptions_file_sha256"] = file_sha256(IDENTITY_EXCEPTIONS_PATH)

    return build_shadow_packet(
        core=core, capture_contract=capture_contract, real_policy=real_policy, real_taxonomy=real_taxonomy,
        exceptions_doc=exceptions_doc, kraken_records_by_id=kraken_records_by_id,
        evaluation_as_of=evaluation_as_of or snapshot_date, code_commit_sha=resolved_commit,
        file_hashes=file_hashes,
    )
