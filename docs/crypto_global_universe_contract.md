# Crypto Breadth Source-Coverage Universe Contract (P3-04)

Status: ratified P1-CR-06 breadth-selection output to P3-01 Global Asset Master
adapter implemented. Listing, delisting, liquidity for investability, tradability,
custody, additional-exchange coverage, and live Master population remain
unratified or unimplemented.

## Reused authority and non-expansion rule

`universe/crypto_global_universe.py` reuses the existing ratified
`crypto_breadth_universe/v1` and exclusion taxonomy exactly within their
declared scope: `breadth_source_coverage_not_investable`.

The adapter does not create a second selection rule. It validates one complete
append-only Kraken snapshot with the existing P1-CR-06 source parser, manifest,
identity exceptions, 30-finalized-day USD turnover ranking, T-1 endpoint,
taxonomy, and Top-N tie-break contract. An unratified policy, changed universe
kind, unknown taxonomy, incomplete source, checksum drift, identity collision,
or current-candle contamination fails before a Master packet is emitted.

The word `RATIFIED` on the breadth policy authorizes only reproducible breadth
source coverage. It does not authorize portfolio eligibility. The adapter
therefore labels the result `KRAKEN_BREADTH_SOURCE_COVERAGE`, never
`INVESTABLE_UNIVERSE`.

## Full-coverage requirement

P1-CR-06 can publish an unclassified breadth observation with at least 90%
T/T-1 observation coverage. P3-04 uses a stricter boundary: every selected
Top-N member must be observed. If `selected_asset_count`,
`observed_asset_count`, or returned member count differs from the ratified
target, Master population fails closed.

This prevents a partially observed breadth packet from silently becoming a
complete source-coverage Master.

## Identity, aliases, and dates

Each selected canonical asset becomes `CRYPTO:KRAKEN:{canonical_asset_id}` in
P3-01. The exact Kraken asset ID and pair ID remain separate identifiers.
Canonical ID, source asset ID, source altname, and active effective-dated
identity-exception aliases are preserved as aliases without prefix stripping or
pair-string inference. Simultaneous canonical collisions fail in the existing
P1-CR-06 selection layer and again in P3-01 identity validation.

The membership economic `as_of_date` is the breadth observation day, while
`knowledge_as_of_utc` is the next snapshot's actual fetch time. Each membership
is valid only for `[breadth_as_of_date, next_calendar_date)`. A newer current
catalog is never applied to an older date.

## Composite exact-source lineage

The required P3-01 source fields bind the exact Kraken AssetPairs response URL
and response SHA-256. Additional lineage components preserve:

- validated snapshot manifest SHA-256 and capture version;
- exact Assets and AssetPairs endpoint response hashes;
- complete OHLC bundle hash;
- each member's exact OHLC response hash and pair ID;
- breadth-universe policy SHA-256;
- exclusion-taxonomy SHA-256; and
- effective-dated identity-policy SHA-256.

The manifest binds all candidates, not only the eventual member's OHLC. This is
required because a member's rank depends on the complete captured comparison
set.

## Preserved observations and closed decisions

The adapter preserves source/canonical identity, asset and pair status, pair
aliases, breadth pre-taxonomy rank, selected rank, 30-day USD turnover, and
taxonomy category. These fields are explicitly `breadth_scope_only`.

Every row retains:

- `liquidity_for_investability = null`;
- `tradability_decision = null`;
- `custody_decision = null`; and
- `investable_eligible = false`.

Breadth rank as investability, liquidity filtering, tradability filtering,
custody filtering, investable-universe approval, current-catalog backfill,
Stage promotion, Production, and trading authorities are false. Coverage is
Kraken-only and cannot claim whole-market availability.

## Offline command

```bash
python3 universe/crypto_global_universe.py \
  /tmp/crypto-breadth/raw/YYYY-MM-DD \
  --out /tmp/crypto-global-universe.json
```

The adapter has no network client and writes atomically only to the requested
path. It adds no workflow, tracked Master, new provider request, portfolio
policy, Production path, or trading path.
