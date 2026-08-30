# P3-12-ID-01 -- Upbit Bounded Identity Registry contract

Status: **PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY**. Nothing here ratifies an
identity registry, changes `approval_status` on any taxonomy or policy
file, or grants investable/PAPER/order/Production/Trading authority.

## The question this closes

P3-12-TAX-01 drafted 75 taxonomy records purely from **ticker match**
against the RATIFIED Kraken breadth taxonomy: if Kraken's registry has an
active record for canonical id `X`, and an Upbit market's default identity
rule also resolves to candidate id `X`, a matching taxonomy record was
drafted. That proves the two registries *agree on the symbol* -- it does
not prove Upbit's market is *the same real-world project*. This module
answers that harder question for the 81 Upbit markets P3-12-TAX-01's
taxonomy already covers (RE included, out of an abundance of caution, but
forced-held -- see below).

## The one mechanical gate every verdict goes through

`compute_verdict()` NEVER returns `VERIFIED_CANDIDATE` from a ticker/symbol
match alone. It requires, in order (any failure holds, never raises):

1. **RE is forced-held** (`HOLD_TICKER_COLLISION`) regardless of any
   evidence -- Kraken's own RATIFIED taxonomy already documents a real
   ticker collision on this exact canonical symbol (2026-08-30 CIO
   directive).
2. **Evidence must exist** for this canonical id in
   `config/upbit_bounded_identity_evidence.json` -- absence is
   `HOLD_MISSING_SECOND_SOURCE`, never an assumption of correctness.
3. **Freshness**: a missing `researched_at`, or an evidence-marked
   `effective_from` still in the future, or a `valid_until` already passed,
   is `HOLD_SOURCE_STALE`. Note: `researched_at` being *later* than
   `evaluation_as_of` is NOT itself stale -- identity due diligence is
   normally conducted after the market/taxonomy snapshot it corroborates;
   see `_is_stale()`'s docstring.
4. **At least one official independent source** must be cited -- no URL
   recorded is `HOLD_MISSING_SECOND_SOURCE`.
5. **No found ticker collision** -- `ticker_collision_risk: true` is
   `HOLD_TICKER_COLLISION`, always, regardless of how confident the match
   otherwise looks.
6. **High name-match confidence** -- anything less than `"high"` (medium,
   low, unverifiable) is `HOLD_MISSING_SECOND_SOURCE`. This bar is
   deliberately strict; a researcher's own honest "medium" self-assessment
   is respected, not second-guessed upward.
7. **No unresolved rebrand/token-swap history** -- a rebrand note present
   with `rebrand_resolved: false` (or unset while a rebrand is present) is
   `HOLD_REBRAND_UNRESOLVED`.
8. **Token-type assets need a confirmed chain/platform** -- `asset_type:
   "token"` with no `chain_or_platform` recorded is
   `HOLD_MISSING_SECOND_SOURCE`, even if every other check passed.

Only after all eight pass does a market become `VERIFIED_CANDIDATE`. A
`manual_override_verdict` field (reviewer-curated, e.g. after spotting an
actual contract mismatch) can force any specific verdict for one asset,
always with a `manual_override_reason` -- this is the only path to
`HOLD_CONTRACT_MISMATCH`, since detecting a mismatch reliably from
unstructured research notes is not attempted programmatically.

## Scope: bounded, not exhaustive

Only Upbit markets whose candidate canonical id already has an
**active-as-of-`evaluation_as_of`** record in
`config/upbit_exclusion_taxonomy.json` are considered at all (81 assets:
the 72 `eligible_crypto` + 6 stablecoin + `USDG` + `XAUT` + `RE` records
P3-12-TAX-01 drafted/carried forward). `CHIP` and the ~200 other
taxonomy-uncovered Upbit-only assets are explicitly out of scope for this
WBS -- they never appear in `registry_candidates` or `hold_list`, not even
as a zero-evidence hold, because they were never in scope to begin with.
`_effective_taxonomy_category()` (reused unchanged from the shadow harness)
governs this -- a taxonomy record not yet effective or already expired as
of `evaluation_as_of` is silently out of scope too, not held.

## Evidence: curated, checked-in research -- never live-fetched at evaluation time

`config/upbit_bounded_identity_evidence.json` holds one entry per
researched canonical id (80 of the 81 -- RE is forced-held without needing
research). Each entry cites the official sources actually consulted
(CoinGecko, official project docs/sites, chain explorers) during a
2026-08-30 research pass, honestly recording cases where the researcher
could not fully confirm identity (`name_match_confidence: "medium"`) or
found a genuine ticker collision (`ticker_collision_risk: true`) --
including two operationally important findings:

* **`KRW-LIT`** -- Upbit's own `english_name` is "Lighter" (a zk-rollup
  perpetuals-DEX protocol, listed on Upbit ~2026-08-24), a **completely
  different, unrelated project** from Litentry/Heima, which the taxonomy
  record's Kraken-sourced `reason` text actually describes. Ticker
  collision correctly holds this market regardless of the taxonomy
  record's own text being about the wrong project -- see §4 below for the
  follow-up this implies for P3-12-TAX-01.
* **`KRW-RE`** -- forced-held per CIO directive (§ above), consistent with
  Kraken's own ratified `unverified_identity` classification for this
  exact symbol.

This file is never fetched or modified at evaluation time -- `build_registry_candidate()`
and `compute_verdict()` are pure functions of whatever this file (and the
other inputs) already say. Re-researching an asset means editing this file
in a future, separately-reviewed change, not a live call from this module.

## Shadow re-verification

`shadow_apply_funnel()` reuses `universe/upbit_shadow_validation_harness.py::shadow_ratify()`
(unmodified) and `universe/upbit_tradeable_universe.py::build_classification()`
(unmodified, the real classifier) with this module's own
`registry_candidate_as_mapping()` output substituted for the shadow
harness's own broader "every non-colliding proposal" identity registry. No
parallel classification logic exists here. Re-running against the real
2026-08-29 snapshot: **55 of 81** taxonomy-covered assets become
`VERIFIED_CANDIDATE`; the resulting funnel is **24 `PAPER_ELIGIBLE`, 0
`TRADEABLE_UNIVERSE`** -- 5 fewer than P3-12-TAX-01's taxonomy-only
supplemental estimate of 29 (28 `PAPER_ELIGIBLE` + 1 `TRADEABLE_UNIVERSE`).
See `docs/p3_12_id_01_bounded_identity_registry_cio_decision_packet_20260830.md`
§5 for the exact 5-market, per-asset explanation of that drop.

## Evaluation date note

`evaluation_as_of` for this WBS's shadow re-run is `2026-08-29` (the same
date as the underlying market snapshot), NOT `2026-08-30` (the date
identity research was actually performed) -- using `2026-08-30` here would
make the 2026-08-29 snapshot's `available_at` exceed the real (still
proposed, unratified) policy's `max_capture_age_hours: 30` threshold and
force every market to `STALE_CAPTURE`, which would test the policy
threshold, not the identity registry. Identity-evidence freshness
(`researched_at`/`effective_from`/`valid_until`) is intentionally decoupled
from this market-data-freshness concern -- see `_is_stale()`'s docstring.

## Offline commands

```bash
python3 .github/scripts/upbit_bounded_identity_registry_build.py 2026-08-29 --evaluation-as-of 2026-08-29
```

Reads the already-committed raw snapshot, `config/upbit_exclusion_taxonomy.json`,
and `config/upbit_bounded_identity_evidence.json`; writes
`data/observations/upbit_bounded_identity_registry/2026-08-29/packet.json`.
Never writes to any canonical config file, calls no network endpoint.
