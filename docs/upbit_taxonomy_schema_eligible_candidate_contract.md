# P3-12-TAX-01 -- Upbit taxonomy schema & eligible-content candidate contract

Status: **PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY**. This module drafts content
into `config/upbit_exclusion_taxonomy.json` but never changes that file's
`approval_status` -- it stays exactly what it already was
(`PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`). Nothing here ratifies a taxonomy,
opens a ready-for-merge PR, or grants investable/PAPER/order/Production/
Trading authority.

## The gap this closes

The P3-12 Shadow Validation Harness
(`universe/upbit_shadow_validation_harness.py`, `docs/upbit_shadow_validation_harness_contract.md`)
found that ratifying `config/upbit_exclusion_taxonomy.json` exactly as it
shipped -- 6 stablecoin exclusion records and nothing else -- would still
leave the funnel at 0 `TRADEABLE_UNIVERSE` / 0 `PAPER_ELIGIBLE`, because the
file carries **zero `eligible_category` records** and
`unknown_asset_policy: fail_closed_unknown` catches every asset without an
explicit record. This module drafts the missing content, following
CIO-ratified classification principles (2026-08-30).

## The one mechanical gate every classification goes through

A market's candidate canonical asset id gets a **new draft record if and
only if** an independently RATIFIED registry --
`config/crypto_breadth_exclusion_taxonomy.json`, loaded through the same
shared, fail-closed contract the shadow harness uses
(`identity/candidate_identity_gap_inventory.py::_load_taxonomy()`) -- already
carries a record for that exact canonical id that is *effective-dated
active as of `evaluation_as_of`*. The drafted category is exactly the
Kraken record's own category, mapped 1:1 (`eligible_crypto` -> `eligible_crypto`,
`stablecoin` -> `stablecoin`, `unverified_identity` -> `unverified_identity`,
`commodity_linked` -> `commodity_linked`).

**Name/symbol pattern hints are never sufficient to draft a record.** A
market whose name merely contains "USD" is not thereby a stablecoin here --
see the CHIP case below. Pattern hints remain useful only as the shadow
harness's own separate, non-authoritative `taxonomy_audit.candidates` review
list.

## CIO-ratified exceptions and additions (2026-08-30)

* **`commodity_linked` added as a new excluded category** in
  `config/upbit_exclusion_taxonomy.json`'s `excluded_categories` -- mirrors
  Kraken's own precedent category, needed because `KRW-XAUT` ("Tether
  Gold") has no home in Upbit's taxonomy schema otherwise.
* **`KRW-XAUT` drafted as `commodity_linked`** -- the general Kraken-active-record
  rule above applied to this specific case; not a hardcoded special-case
  branch (removing the CIO's stated exception would leave XAUT correctly
  unclassified as a `TAXONOMY_SCHEMA_GAP` instead, not silently promoted).
* **`KRW-RE` drafted as `unverified_identity`, never `eligible_crypto`**,
  *and* stays on the hold list regardless. Kraken's own RATIFIED taxonomy
  already documents a real ticker collision on the identical canonical
  symbol `RE` ("No canonical on-chain/project identity confirmed by two
  independent sources... Kraken's own AssetPairs/Assets metadata carries no
  chain/contract/genesis identity field to disambiguate"). The taxonomy
  layer is resolved (excluded, safe), but the identity-layer ambiguity is a
  separate, still-open concern -- see the shadow harness's
  `identity_review.manual_review_queue`, which continues to flag `KRW-RE`.
* **`KRW-CHIP` ("USD.AI") is never auto-classified as `stablecoin`**, even
  though its name contains "USD" and even in a fixture where a Kraken
  record would otherwise corroborate it -- CHIP is an explicit,
  CIO-directed no-auto-classify exception
  (`_NO_AUTO_CLASSIFY_MARKETS`) because no official issuer or independent
  corroboration exists. It routes to the hold list with a reason distinct
  from the generic Upbit-only bucket
  (`NO_INDEPENDENT_STABLECOIN_ISSUER_CORROBORATION`).
* **`leveraged`/`derivative_like` remain reserved excluded categories with
  zero drafted records.** Neither this repository nor Kraken's own taxonomy
  has ever defined criteria for them, and no Upbit market in today's
  snapshot has independent corroboration for either -- ratifying a
  definition for these categories is a separate, prior decision this module
  does not make.
* **201 Upbit-only assets with no independent corroboration at all are
  never guessed into `eligible_crypto`** -- each routes to the hold list,
  fail-closed, reason `NO_INDEPENDENT_CORROBORATION_UPBIT_ONLY`.

## Fail-closed handling of registry edge cases

* **Conflicting/stale Kraken record** -- a candidate id present in the
  Kraken registry but not *active* as of `evaluation_as_of` (not yet
  effective, or already expired) is held, never drafted, reason
  `CONFLICTING_OR_STALE_KRAKEN_RECORD`.
* **Category outside Upbit's vocabulary even after adding
  `commodity_linked`** (e.g. a hypothetical Kraken `fiat`/`staked` match --
  none exist in today's real snapshot) -- reported as a `schema_gaps` entry
  *and* held, reason `TAXONOMY_SCHEMA_GAP`; never silently assigned to the
  nearest existing category.
* **Unresolved identity collision** -- a market with an unresolved
  `DUPLICATE_CANONICAL_TARGET` finding is excluded from candidate
  generation entirely (held, reason `IDENTITY_COLLISION_UNRESOLVED`), same
  discipline as every other P3-12 module.
* **Already-recorded canonical id** -- a candidate id already covered by an
  effective-dated record in the real taxonomy (the original 6 stablecoin
  exclusions) is never duplicated or re-derived; the existing record is
  copied through byte-for-byte.
* **In-run duplicate canonical id** -- if two distinct, non-colliding
  markets somehow produced the same candidate id (cannot happen via the
  real default-rule + collision-detection path, but defended anyway), the
  builder raises `TaxonomyCandidateError` rather than silently picking one.

## Reproducibility / evidence

`build_candidate()` is a pure function of its arguments (the parsed core
snapshot, the real taxonomy, the Kraken records-by-id map, the identity
proposals/blocked-markets set, and `evaluation_as_of`) -- no wall-clock or
random value. `.github/scripts/upbit_taxonomy_schema_eligible_candidate_build.py`
is the impure entry point: it resolves the code commit SHA (fails closed,
same idiom as the shadow harness), optionally writes the candidate taxonomy
into `config/upbit_exclusion_taxonomy.json` (preserving `approval_status`
and every other untouched field byte-for-byte, atomic temp+replace write),
and persists an append-only, tamper-checked evidence packet -- including a
self-hash verification on rerun, same as the shadow harness's own fix --
under `data/observations/upbit_taxonomy_schema_eligible_candidate/<date>/packet.json`.

## Downstream side effect: other packets must be regenerated

Editing `config/upbit_exclusion_taxonomy.json`'s content changes the file's
hash, which two other, unrelated evidence packets record as provenance:
`data/observations/upbit_identity_review/<date>/packet.json` (source file
hash) and `data/observations/upbit_p3_12_shadow_validation/<date>/packet.json`
(shadow-apply result, which now genuinely changes -- see
`docs/p3_12_tax_01_taxonomy_candidate_cio_decision_packet_20260830.md` §3).
Both were regenerated as part of this change; their own tamper-detection
tests correctly caught the drift before regeneration.

## Offline commands

```bash
python3 .github/scripts/upbit_taxonomy_schema_eligible_candidate_build.py 2026-08-29
```

Reads the already-committed raw snapshot and the current
`config/upbit_exclusion_taxonomy.json`/`config/crypto_breadth_exclusion_taxonomy.json`,
writes the candidate content into `config/upbit_exclusion_taxonomy.json`
(pass `--no-write-taxonomy` to build the evidence packet only), and writes
`data/observations/upbit_taxonomy_schema_eligible_candidate/2026-08-29/packet.json`.
Calls no network endpoint, never flips `approval_status`.
