# Crypto Breadth / Alt Participation Contract (P1-CR-06)

Status: source, replay, and daily capture contracts implemented; the universe
rule is ratified effective 2026-08-19; the first-qualified-live-Top-100 Exit
Gate is satisfied by the committed natural 2026-08-30 and 2026-08-31 capture
chains.  There is no classification, Regime, Production, or trading authority.

The Exit Gate is capability evidence, not a promise that every later daily
snapshot will remain qualified.  The 2026-09-01 and 2026-09-02 chains returned
to `TAXONOMY_COVERAGE_UNKNOWN` as new cutoff-relevant identities appeared. That
point-in-time result does not revoke the already-proven capture/validation
capability, and it must not be relabeled as a neutral or complete market view.

## Purpose

This contract makes one narrow observation reproducible: for an exact UTC day,
did BTC advance while the assets admitted by the same-day Crypto breadth
universe advanced, declined, or stayed unchanged?  The output is raw counts and
fractions.  It is not a breadth regime, risk-on/risk-off label, score, or order
input.

The source is Kraken Spot public market data:

- `Assets?assetVersion=1` captures the asset catalog and status;
- `AssetPairs?assetVersion=1&aclass_base=currency` captures tradable pairs;
- `OHLC?pair={PAIR}&interval=1440&since={SINCE}&assetVersion=1` captures each
  candidate USD pair's daily close, VWAP, and base volume series.

Primary source documentation:

- https://docs.kraken.com/api-reference/market-data/get-asset-info
- https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs
- https://docs.kraken.com/api-reference/market-data/get-ohlc-data

Kraken documents that OHLC returns no more than 720 recent entries and always
includes the current, not-yet-committed timeframe.  The helper therefore
removes the final row for every pair.  It never treats the current candle as
evidence.

Kraken can represent an interval with no trades as a flat OHLC row whose VWAP,
base volume, and trade count are all zero.  Atlas accepts that sentinel only
when all four OHLC prices are identical and all three activity fields are zero.
A zero VWAP with any reported activity, a partially zero activity tuple, or a
non-flat no-trade row fails closed.  This source-shape rule does not change the
existing rule that the final current row is always excluded from observations.

## Point-in-time universe boundary

`Assets` and `AssetPairs` are current catalogs, not a historical membership
service.  A catalog fetched today must never be used to reconstruct yesterday's
eligible assets.  A replay point is valid only when that point has its own
append-only date directory:

```text
{snapshot_root}/{UTC_VINTAGE_DATE}/
  _downloaded_at.txt
  _sha256.txt
  _manifest.json
  kraken_assets.json.gz
  kraken_asset_pairs.json.gz
  kraken_ohlc_responses.ndjson.gz
```

The OHLC bundle has one sorted NDJSON record per pair containing the pair ID,
the source-body SHA-256, and the exact source body in base64.  This preserves
every response byte while avoiding roughly 630 separate Git files per day.
The outer checksum and every inner checksum are verified.  The manifest binds
the exact raw bytes, catalog counts, OHLC pair list, source semantics, and
identity-exception policy.  Re-running `manifest` for the same snapshot is an
append-only violation.  `replay` reads independent daily snapshots; it does
not carry the newest catalog backward.

## Identity and rename/reuse

Pair display text and ticker aliases are not identity.  The effective-dated
table `config/crypto_asset_identity_exceptions.json` maps a Kraken
`source_asset_id` to a stable `canonical_asset_id`.  Overlapping ranges fail.
If two simultaneously selected source assets resolve to one canonical asset,
the transform also fails instead of double-counting it.  A later rename or
ticker reuse requires a new non-overlapping record and a new immutable policy
version; silent edits are forbidden.

Kraken's `assetVersion=1` returns display identifiers such as BTC and USD while
the source documentation also shows legacy XXBT/XBT identifiers.  The v1 table
records that known BTC alias boundary explicitly.  It does not infer identity
by removing X/Z prefixes or by parsing a pair string.

## Ratified universe rule

`config/crypto_breadth_universe_policy.json` is `RATIFIED` effective
2026-08-19.  For an observation at T, membership is selected as follows:

1. use enabled assets and online Kraken Spot pairs quoted in USD;
2. for each candidate, sum `daily VWAP × base volume` over the exact 30
   finalized UTC days ending at T-1;
3. rank descending by that USD turnover, with canonical asset ID and pair ID
   as deterministic tie-breakers;
4. apply the versioned taxonomy in
   `config/crypto_breadth_exclusion_taxonomy.json` and exclude fiat,
   stablecoin, wrapped, staked, commodity-linked, and unverified-identity
   assets;
5. select the first 100 eligible assets.  BTC participates in selection but is
   emitted only as a reference and is excluded from the Alt breadth fraction.

The T-1 ranking endpoint prevents the T price move being measured from choosing
its own membership.  A pair without exact 30-day ranking history is explicitly
rank-ineligible.  An unclassified asset encountered before the 100th eligible
member makes the whole result `UNKNOWN`; it is never included by default. An
explicitly `unverified_identity`-classified asset (policy_version v2,
2026-08-22 -- canonical on-chain/project identity could not be confirmed by
two independent sources) is structurally different: it is a real, ratified
exclusion, so the ranking loop skips it and keeps going, exactly like any
other excluded category -- it does not itself make the whole result
`UNKNOWN`, and it is never a claim that the asset is investment-unsuitable,
only that it is conservatively out of the source-coverage universe.

A newly listed online pair can legitimately have only the current row, or only
one finalized row plus the current row.  Atlas preserves that complete source
response but keeps the pair rank-ineligible until all 30 required finalized
ranking dates exist.  Short listing history is not a partial-source failure and
is never padded or backfilled.

This universe is expressly `breadth_source_coverage_not_investable`.  It does
not claim liquidity, capacity, tradability for an Atlas portfolio, or exchange
coverage beyond Kraken.

### Cutoff-aware scan audit (2026-08-22)

An audit confirmed `qualified_members()` already implements exactly the
algorithm above, not a "classify the provider's entire universe" policy.
The ranking loop's own `if len(selected) == target: break` means a
candidate ranked below the point the target-th `eligible_crypto` asset is
found is never visited at all, let alone required to carry a taxonomy
record — proven directly in `test_crypto_breadth_cutoff_aware_scan.py`
(an asset with no taxonomy record whatsoever, ranked below a satisfied
target, never appears in `taxonomy_unknown_before_cutoff`; an `EXCLUDED`
asset within the scan range is skipped and backfilled from the next
rank; an `UNKNOWN` asset *within* the scan range still blocks, because
its resolution could change which asset actually fills a slot; a
mutation that promotes a below-cutoff unknown into the scan range flips
the result to blocked). No code change to the scan's own logic was made
because none was needed.

The real 2026-08-22 snapshot's own `TAXONOMY_COVERAGE_UNKNOWN` result is
not evidence against this: `known_eligible_count_so_far` (see below)
reports **87** — only 87 assets have ever been individually ratified
`eligible_crypto` in this repository's history, 13 short of
`target_asset_count=100`. Because the scan cannot reach target, it is
structurally forced to walk the full ranked list looking for enough
eligible candidates, correctly treating every `UNKNOWN` it passes along
the way as selection-relevant (any one of them could supply one of the
missing 13 slots). This is a genuine ratification-coverage shortfall —
more crypto assets need to be individually taxonomy-ratified — not a
scan-order defect and not evidence that a "100% provider universe"
policy is required.

`known_eligible_count_so_far` is included in `qualified_members()`'s
`TAXONOMY_COVERAGE_UNKNOWN` diagnostics (and surfaced in the committed
`universe` output) specifically so this distinction — real shortfall vs.
a specific blocking candidate near the cutoff — is visible directly in
committed evidence, without needing to re-derive it by hand.

The scheduled capture also publishes an append-only
`data/observations/crypto_taxonomy_gap/<source_date>/packet.json`. This is a
`REVIEW_INVENTORY_ONLY` artifact rebuilt from the same production transform:
it binds the raw manifest, universe policy and taxonomy hashes and preserves
the ranked UNKNOWN, EXCLUDED and rank-ineligible rows. It does not add a
taxonomy category, ratify a record, reduce the Top-100/90% gates, or authorize
investability, Stage, Production, or trading. Its purpose is to turn the live
coverage blocker into a deterministic review queue, not to decide the queue.

### Effective-dated cutoff Slice (2026-08-27)

The 2026-08-27 review inventory put 42 previously unclassified assets at ranks
69 through 141 before the taxonomy cutoff. Each exact Kraken identity was
confirmed against the retained, enabled Assets catalog and online USD pair
catalog, then independently matched to a project/foundation protocol document,
token contract, migration notice, or exact-contract asset report. The retained
pair-catalog body is
`evidence/crypto/breadth/raw/2026-08-27/kraken_asset_pairs.json.gz` with SHA-256
`90d105c571b464ffea2a1a21a814f5f5ae3da9f63086e49e49fee55c53ec1a61`.
Ticker text alone was not accepted; the reason field records the disambiguating
identity for migration/collision-sensitive cases such as LIT, NIL, M, DOG,
BABYSHARK, SYRUP, POPCAT, EIGEN, and ETHFI.

The resulting source-coverage records are effective 2026-08-27 for exactly:

```text
ACU APT ARB ASTER BABY BABYSHARK BONK CVX DCR DOG EIGEN ESP ETHFI FLOKI
ICNT JASMY JTO KNTQ KTA LIT M MANA MELANIA MINA NIL OP PENDLE PLUME POPCAT
PYTH RIZE SCRT STRK STX SYRUP TIA VIRTUAL WIF XAN XNY XPL ZRO
```

They are all `eligible_crypto` only in the narrow breadth source-coverage
taxonomy. This is not an investability, capacity, venue-selection, security,
or trading judgment. `target_asset_count=100` and
`minimum_observation_coverage_bps=9000` are unchanged, as are all false
classification/threshold/Regime/Production/trading authority flags.

### Effective-dated BTR cutoff slice (2026-08-29)

The natural 2026-08-29 review inventory reduced the cutoff-relevant taxonomy
gap to one asset, `BTR/USD` at rank 91.  The retained Kraken Assets catalog
marks `BTR` enabled and its AssetPairs catalog marks `BTR/USD` online.  Kraken's
official [listing notice](https://blog.kraken.com/product/asset-listings/btr-is-available-for-trading)
identifies the pair as Bitlayer, and Bitlayer's official
[BTR token documentation](https://docs.bitlayer.org/docs/Learn/Introduction/BTRToken/en/)
independently identifies BTR as the ecosystem governance token.  The exact
record is therefore source-identity classified `eligible_crypto` effective
2026-08-29.

The retained snapshot evaluates at 2026-08-28, so it remains byte-for-byte and
semantically blocked by `TAXONOMY_COVERAGE_UNKNOWN`; the new record is not
backdated.  A test-only replay that changes only the temporary effective date
shows the existing algorithm would select and observe 100/100 with zero
cutoff-relevant unknowns.  That counterfactual is not natural evidence.  The
next capture whose own `as_of_date` is on or after 2026-08-29 must prove the
result independently.  Top-100, 90% coverage, investability, Regime,
Production, and trading rules and authorities are unchanged.

The retained 2026-08-27 capture has `as_of_date=2026-08-26`, so replaying it
with the production policy still returns the original
`TAXONOMY_COVERAGE_UNKNOWN` (87 known eligible assets, 515 unknown rows). This
is the required non-retroactive PIT result. A test-only, non-persisted replay
that moves only these 42 effective dates back one day isolates the existing
gate logic: the same raw snapshot selects 100 assets, observes all 100, leaves
zero unknown rows before the cutoff, and emits only
`OBSERVED_UNCLASSIFIED`. That counterfactual is not historical evidence and
cannot close the operational Gate; the first natural capture whose
`as_of_date` is on or after 2026-08-27 must do that independently.

## Output and missing policy

For each included member, the helper emits canonical/source identity, exact
pair, T-1 and T dates, both closes, and `ADVANCE`, `DECLINE`, or `UNCHANGED`.
BTC is kept as a separate reference.  `alt_participation` excludes BTC and
contains only asset counts and fractions.

The collector must capture every matching USD candidate pair; a missing pair,
source error, partial catalog, checksum mismatch, or identity collision fails
the atomic capture.  After a deterministic Top 100 exists, T/T-1 direction is
calculated only for members with both closes.  Coverage of 90% or more remains
an explicitly labeled raw observation with the missing members listed.  Below
90%, or with a missing BTC reference, the breadth output is `UNKNOWN` and both
BTC and Alt measurements are null.  Missing data is never converted to zero or
neutral.

All outputs keep these authorities false:

- breadth classification;
- threshold;
- Regime score;
- Production wiring;
- trading action.

## Capture and offline commands

The scheduled workflow runs at 00:40 UTC, uses no key or paid service, and
paces Kraken public OHLC calls at 1.05 seconds per request.  Roughly 630 current
USD pairs take about 11 minutes.  It stages the entire snapshot outside the
final evidence path, validates every response and hash, then performs one
append-only move.  A failed or partial run is not committed.

The offline transform helper itself never calls the network:

```bash
python3 .github/scripts/crypto_breadth.py manifest \
  --snapshot-dir /tmp/crypto-breadth/raw/2026-08-20 \
  --capture-version crypto-breadth-capture/v2

python3 .github/scripts/crypto_breadth.py validate \
  /tmp/crypto-breadth/raw/2026-08-20

python3 .github/scripts/crypto_breadth.py transform \
  /tmp/crypto-breadth/raw/2026-08-20 \
  --universe-policy /tmp/ratified-crypto-breadth-policy.json \
  --exclusion-taxonomy /tmp/ratified-crypto-breadth-taxonomy.json \
  --out /tmp/crypto-breadth.json

python3 .github/scripts/crypto_breadth.py replay \
  /tmp/crypto-breadth/raw \
  --universe-policy /tmp/ratified-crypto-breadth-policy.json \
  --exclusion-taxonomy /tmp/ratified-crypto-breadth-taxonomy.json \
  --out /tmp/crypto-breadth-replay.json
```

No command writes a tracked breadth factor by default.
