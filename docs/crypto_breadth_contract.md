# Crypto Breadth / Alt Participation Contract (P1-CR-06)

Status: source, replay, and daily capture contracts implemented; the universe
rule is ratified effective 2026-08-19; the first qualified live snapshot is
still pending.  There is no classification, Regime, Production, or trading
authority.

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
   stablecoin, wrapped, staked, and commodity-linked assets;
5. select the first 100 eligible assets.  BTC participates in selection but is
   emitted only as a reference and is excluded from the Alt breadth fraction.

The T-1 ranking endpoint prevents the T price move being measured from choosing
its own membership.  A pair without exact 30-day ranking history is explicitly
rank-ineligible.  An unclassified asset encountered before the 100th eligible
member makes the whole result `UNKNOWN`; it is never included by default.

A newly listed online pair can legitimately have only the current row, or only
one finalized row plus the current row.  Atlas preserves that complete source
response but keeps the pair rank-ineligible until all 30 required finalized
ranking dates exist.  Short listing history is not a partial-source failure and
is never padded or backfilled.

This universe is expressly `breadth_source_coverage_not_investable`.  It does
not claim liquidity, capacity, tradability for an Atlas portfolio, or exchange
coverage beyond Kraken.

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
