# P3-12 Shadow Validation -- CIO decision packet -- 2026-08-29

Status: **REVIEW MATERIAL ONLY**. Nothing in this document ratifies a
policy, taxonomy, or identity mapping, and no ratification PR has been
opened. `RATIFIED` values are unchanged; every threshold and taxonomy
category is reported exactly as currently committed. Production/Trading/
REAL/PAPER order authority remain `false` everywhere referenced here.

Evidence packet: `data/observations/upbit_p3_12_shadow_validation/2026-08-29/packet.json`
(`payload_sha256` inside the file; recompute with
`universe/upbit_shadow_validation_harness.py::payload_sha256`). Harness
source: `universe/upbit_shadow_validation_harness.py` +
`.github/scripts/upbit_shadow_validation_harness_run.py`. Contract:
`docs/upbit_shadow_validation_harness_contract.md`. Code commit SHA is
recorded inside the packet itself (`code_commit_sha`).

**Follow-up (2026-08-30, P3-12-TAX-01):** the single bottleneck this
document identified -- zero `eligible_category` records in
`config/upbit_exclusion_taxonomy.json` -- now has a drafted (still
`PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`, still-unmerged draft PR) fix. See
`docs/p3_12_tax_01_taxonomy_candidate_cio_decision_packet_20260830.md`.
The evidence packet path above has been regenerated against that draft
content; the numbers in this document's §2 below describe the *prior*
(pre-P3-12-TAX-01) state and are kept as the historical record CIO already
reviewed and approved via PR #459.

**Revision note (2026-08-29, post-CIO-review on PR #459):** this revision
fixes an existing-packet self-hash tamper gap, replaces the Kraken breadth
taxonomy loader with the shared, already-tested fail-closed contract from
`identity/candidate_identity_gap_inventory.py`, makes all Kraken
corroboration `evaluation_as_of`-active-record-aware, and renames the
"before" baseline field to state precisely what it includes. None of these
fixes changed today's reported funnel numbers (see §2) -- they close
regression gaps that would only have mattered under future config drift or
a tampered existing packet. Full detail:
`docs/upbit_shadow_validation_harness_contract.md`.

## 1. The bottleneck this closes

Today, in production: **282 Upbit KRW markets, 282 `OBSERVATION_POOL`, 0
`TRADEABLE_UNIVERSE`, 0 `PAPER_ELIGIBLE`** -- confirmed by both this
harness's own recomputed `before_current_production_mechanical_collision_included`
funnel and the existing natural evidence at
`data/observations/upbit_tradeable_universe/2026-08-29/packet.json`. That
is the correct, intentional current state (policy/taxonomy/identity all
`PROPOSED_UNRATIFIED`), not a bug -- but it is also why realtime
subscription and market evidence downstream both currently see zero
candidates.

## 2. Funnel: before -> after, in numbers

| Scenario | market_count | OBSERVATION_POOL | TRADEABLE_UNIVERSE | PAPER_ELIGIBLE | BLOCKED |
|---|---|---|---|---|---|
| **Before** (current production; policy/taxonomy/identity all unratified; today's mechanical identity-collision hold included, exactly as real production applies it -- see packet's `funnel_definitions`) | 282 | 282 | 0 | 0 | 0 |
| **After -- ratify policy + taxonomy + identity exactly as currently written** | 282 | 282 | 0 | 0 | 0 |
| **Supplemental, hypothetical only -- after, PLUS also add one `eligible_crypto` taxonomy record per Kraken-corroborated asset (active as of 2026-08-29)** | 282 | 253 | 1 | **28** | 0 |

**Read this carefully: ratifying today's committed policy and taxonomy
files exactly as written does NOT connect the funnel by itself.**
`config/upbit_exclusion_taxonomy.json` currently contains **zero
`eligible_category` records** -- only 6 stablecoin exclusion records. Under
its own `unknown_asset_policy: fail_closed_unknown`, every asset without an
explicit record -- 268 of 282 today -- resolves to `TAXONOMY_UNKNOWN` and
stays `OBSERVATION_POOL`, identity and policy ratification notwithstanding.
This is not a defect in the classifier; it is the taxonomy file's own
current content. The CIO needs to decide, separately, whether/how to add
per-asset `eligible_crypto` records (see §5, "Taxonomy" verdict).

The third row is **not a proposal** -- it mechanically adds one
`eligible_crypto` record per canonical id already independently corroborated
by the RATIFIED `config/crypto_breadth_exclusion_taxonomy.json` (72 ids), to
give a concrete sense of scale. It shows that closing just that one gap
would likely produce on the order of **28 PAPER_ELIGIBLE markets** from
today's snapshot alone -- a real, non-zero funnel, and a strong signal that
the taxonomy's missing eligible-side records, not the policy thresholds, are
the binding constraint right now.

### Primary-scenario reason distribution (all 282 markets, "ratify as written")

| Reason | Count |
|---|---|
| `TAXONOMY_UNKNOWN` | 268 |
| `INVESTMENT_WARNING_ACTIVE` | 8 |
| `TAXONOMY_EXCLUDED:stablecoin` | 6 |

### Supplemental-scenario reason distribution (282 markets, "+ 72 corroborated eligible_crypto records")

| Reason | Count |
|---|---|
| `TAXONOMY_UNKNOWN` | 197 |
| `SPREAD_ABOVE_THRESHOLD` | 33 |
| `PAPER_ELIGIBLE_ALL_GATES_PASSED` | 28 |
| `INVESTMENT_WARNING_ACTIVE` | 8 |
| `LISTING_HISTORY_BELOW_THRESHOLD` | 7 |
| `TAXONOMY_EXCLUDED:stablecoin` | 6 |
| `TURNOVER_BELOW_THRESHOLD` | 2 |
| `SLIPPAGE_ABOVE_THRESHOLD` | 1 |

### Liquidity / spread / slippage / freshness gate distribution (primary scenario)

All 282 markets currently stop at the taxonomy gate before ever reaching
the liquidity/spread/slippage/freshness gates, so those gates show 0 fails
in the primary scenario -- not because every market passes them, but
because the taxonomy gate (above) is upstream and binding first. The
supplemental scenario's reason table above is the first real look at where
the liquidity/spread/slippage gates themselves bind once the taxonomy gate
is notionally cleared: spread is the single largest supplemental-scenario
failure reason (33 markets), ahead of listing history (7) and turnover (2).
Slippage is essentially not binding at the policy's own notional (1
market). No market is `STALE_CAPTURE` in either scenario.

### Estimated slippage by order size (supplemental scenario, sample)

For every market that reaches `TRADEABLE_UNIVERSE` or better under the
supplemental scenario, the packet's `funnel_supplemental_hypothetical.slippage_curve_sample`
reports the classifier's own PIT volume-weighted slippage estimate at 0.5x,
1x, 3x, and 5x the policy's own notional (KRW 500,000 / 1,000,000 /
3,000,000 / 5,000,000) -- reusing the exact same math the real gate uses,
never a new threshold. 29 markets have a sample row; several (e.g.
`KRW-AAVE`, `KRW-AVAX`) show ~0 bps slippage even at 5x notional (very deep
book), while others (e.g. `KRW-ATOM`) show slippage rising from ~4 bps at
0.5x notional to ~7.5 bps at 5x -- exactly the kind of order-size-dependent
detail the policy's own single-notional gate cannot show by itself.

## 3. Identity findings

* **282 identity proposals** reviewed, **0 `DUPLICATE_CANONICAL_TARGET`
  collisions** -- no two Upbit markets currently propose the same candidate
  canonical asset id.
* Kraken RATIFIED-registry cross-reference (informational only, never a
  gate): 81 of 282 candidate ids are independently known to Kraken's
  registry at all; 201 are Upbit-only and have no independent corroboration
  either way (expected -- Upbit lists many Korea-specific/small-cap assets
  Kraken does not).
* **One high-priority manual-review item: `KRW-RE` ("Re" / "리").** Kraken's
  own RATIFIED taxonomy already documents, for the identical canonical
  symbol `RE`, a real ticker collision between two unrelated, unaffiliated
  projects ("No canonical on-chain/project identity confirmed by two
  independent sources... Kraken's own AssetPairs/Assets metadata carries no
  chain/contract/genesis identity field to disambiguate"). Upbit's own
  default identity rule would map `KRW-RE` to the same bare symbol `RE`.
  This does not by itself prove Upbit's `KRW-RE` is the same collision-prone
  project -- but it is a strong, independently-sourced reason to require a
  second confirming source before ratifying `KRW-RE`'s identity, exactly the
  same evidentiary bar Kraken's own `unverified_identity` records already
  use elsewhere in this repository.
* No other rebrand/token-swap or same-name-different-asset signal was found
  in today's snapshot beyond this one item -- but note the harness has no
  general-purpose way to detect a rebrand/token-swap from `market/all` data
  alone (no chain/contract identifier is exposed by that endpoint); absence
  of a flag here is not proof of absence of a rebrand.

## 4. Taxonomy audit findings (against today's full 282-market list)

| Market | Candidate ID | Suggested category | Basis |
|---|---|---|---|
| `KRW-USD1` | USD1 | stablecoin | Name pattern: "World Liberty Financial USD" |
| `KRW-USDG` | USDG | stablecoin | Name pattern: "Global Dollar"; independently corroborated as `stablecoin` by Kraken RATIFIED taxonomy |
| `KRW-CHIP` | CHIP | stablecoin (low confidence) | Name pattern: "USD.AI" -- needs verification, may be a yield/compute token merely USD-branded, not necessarily 1:1 pegged |
| `KRW-RE` | RE | unverified_identity | Kraken RATIFIED taxonomy already classifies this exact id as `unverified_identity` (see §3) |
| `KRW-XAUT` | XAUT | **schema gap, not a category match** | "Tether Gold" -- Kraken RATIFIED taxonomy classifies XAUT as `commodity_linked`, a category that does not exist in Upbit's own taxonomy schema at all |

* **72 canonical ids** are independently corroborated as `eligible_crypto`
  by the Kraken RATIFIED registry but have **no taxonomy record of any kind**
  in `config/upbit_exclusion_taxonomy.json` yet (this is the gap behind
  §2's supplemental scenario).
* **6 canonical ids** already have an effective taxonomy record (the
  existing stablecoin exclusions: USDT, USDC, USDE, USDS, RLUSD, EURC).
* **Category definition gap:** `leveraged` and `derivative_like` are both
  listed in `config/upbit_exclusion_taxonomy.json`'s `excluded_categories`
  but have **zero ratified records or worked criteria anywhere in this
  repository** (Kraken's own taxonomy doesn't define these two categories
  either). No market in today's snapshot pattern-matched either category
  (no wrapped/leveraged/derivative-branded tokens found on Upbit KRW spot),
  but the category definitions themselves remain an open gap that should be
  resolved -- or explicitly deferred -- independently of today's ratify
  decision.

## 5. Items that cannot be judged from available data

* **Identity**: 201 of 282 candidate canonical ids have no independent
  cross-exchange corroboration one way or the other (see §3) -- this is
  expected given Upbit's Korea-specific listings, not a data defect, but it
  means "0 collision findings" must not be read as "identity confirmed."
* **Taxonomy**: `KRW-CHIP` ("USD.AI") needs a human check of whether it is
  actually a 1:1 USD-pegged instrument or merely USD-branded.
* **Liquidity/spread/slippage**: 0 markets in today's snapshot are
  `MISSING_FIELD:*`, `SPREAD_NOT_COMPUTABLE`, or `SLIPPAGE_NOT_COMPUTABLE`
  in either scenario -- today's capture is complete for all 282 markets, so
  there is currently no "cannot judge, no data" backlog on that axis.

## 6. Per-layer verdict for the CIO

| Layer | Verdict | Why |
|---|---|---|
| **Policy** (`config/upbit_tradeable_universe_policy.json`) | **Approvable as-is, for a first ratification** | The exact proposed thresholds (90-day listing, 30-day turnover ≥ KRW 5B, ≤20 bps spread, ≤30 bps slippage at KRW 1M) are unchanged by this review. This harness only *shadow-applied* them; it did not evaluate whether the numbers themselves are the right numbers -- that judgment is out of this review's scope (see safety boundary). The supplemental-scenario reason distribution suggests spread (33 fails) is the largest binding constraint among these thresholds, worth knowing before final sign-off but not a defect in the policy file itself. |
| **Identity** | **Approvable, minus one hold** | 0 collision findings across 282 proposals is a real, mechanically-verified result. Hold `KRW-RE` for a second confirming source before ratifying its specific mapping (§3); the other 281 proposals have no comparable red flag, though 201 also have no independent corroboration either way -- that absence is disclosed, not resolved, by this review. |
| **Taxonomy** | **Needs additional evidence before it can unblock the funnel** | The file itself (categories + the 6 existing stablecoin records) is internally consistent and can be ratified as a *mechanism*, but ratifying it alone leaves the funnel at 0/0 (§2). Before ratification meaningfully changes anything, the CIO should decide: (a) whether/how to add `eligible_crypto` records (the 72-id gap), (b) the 5 candidate records surfaced in §4, (c) the `KRW-XAUT` schema gap (no `commodity_linked` category exists), and (d) whether/when to define `leveraged`/`derivative_like` criteria at all. |

## 7. Atomic ratification PR plan (not opened by this review)

A future ratification PR, when the CIO is ready, should be scoped as one
atomic change per decision layer, in this dependency order (each step is
independently revertable and independently testable against the existing
`test_upbit_tradeable_universe.py` fail-closed gates):

1. **Taxonomy schema decision** -- resolve `KRW-XAUT`'s `commodity_linked`
   gap (add the category or assign XAUT elsewhere) and decide the
   `leveraged`/`derivative_like` definition question, *before* adding bulk
   eligible-side records, so the schema itself doesn't need a second pass.
2. **Taxonomy content ratification** -- flip
   `config/upbit_exclusion_taxonomy.json`'s `approval_status` to `RATIFIED`
   together with, in the same PR: the 5 candidate records from §4 (each with
   its own two-source-quality reason text, matching the existing 6 records'
   evidentiary bar) and a CIO-approved subset (or all) of the 72
   Kraken-corroborated `eligible_crypto` records from §2/§4. Re-run this
   harness against the same snapshot date as a pre-merge check -- the
   resulting `funnel_supplemental_hypothetical` numbers should become the
   new *primary* funnel once this PR lands.
3. **Identity ratification** -- create the real ratified per-market
   identity registry file this repository currently lacks (or the
   equivalent mechanism `universe/upbit_tradeable_universe.py`'s
   `ratified_identity_registry` argument expects), holding back `KRW-RE`
   pending its second source (§3, §6).
4. **Policy ratification** -- flip
   `config/upbit_tradeable_universe_policy.json`'s `approval_status` to
   `RATIFIED` last, once taxonomy and identity are both real, so the first
   live `TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE` markets reflect a fully
   ratified stack, not a partial one.
5. **Re-run and re-diff** this harness's evidence packet immediately after
   each step lands, and attach the new packet's `payload_sha256` to the PR
   description, so every step's actual effect on the funnel is recorded,
   not assumed.

This plan is a sequencing proposal only -- it does not itself ratify
anything, and no PR implementing any of these five steps has been opened by
this review.

## 8. Safety boundary confirmation

* No `RATIFIED` value was changed by this review; every `approval_status`
  in every committed config file is unchanged.
* No ratification PR was opened.
* No threshold or taxonomy category definition was invented or altered --
  every number in §2/§6 comes from the committed policy/taxonomy files
  verbatim; the supplemental scenario adds only records whose category and
  evidence are copied from an existing RATIFIED registry, never invented.
* No Upbit order/withdrawal/private endpoint was called (`universe/upbit_shadow_validation_harness.py`
  only reads the same already-captured, hash-validated public snapshot the
  real classifier reads).
* Every `authority` field in the evidence packet and in every module this
  review touched is hardcoded `false` except `review_only`/`review_status`
  markers.
* KIS/Portal work areas were not touched by this review.
