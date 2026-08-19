# Crypto Breadth / Alt Participation Contract (P1-CR-06)

Status: source and replay contract implemented; universe policy unratified; no
Regime or Production authority.

## Purpose

This contract makes one narrow observation reproducible: for an exact UTC day,
did BTC advance while the assets admitted by the same-day Crypto breadth
universe advanced, declined, or stayed unchanged?  The output is raw counts and
fractions.  It is not a breadth regime, risk-on/risk-off label, score, or order
input.

The source is Kraken Spot public market data:

- `Assets?assetVersion=1` captures the asset catalog and status;
- `AssetPairs?assetVersion=1&aclass_base=currency` captures tradable pairs;
- `OHLC?pair={PAIR}&interval=1440&assetVersion=1` captures each selected daily
  close series.

Primary source documentation:

- https://docs.kraken.com/api-reference/market-data/get-asset-info
- https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs
- https://docs.kraken.com/api-reference/market-data/get-ohlc-data

Kraken documents that OHLC returns no more than 720 recent entries and always
includes the current, not-yet-committed timeframe.  The helper therefore
removes the final row for every pair and requires exact finalized rows for T and
T-1.  It never treats the current candle as evidence.

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
  ohlc/{sha256(pair_id)}.json.gz
```

The manifest binds the exact raw bytes, catalog counts, OHLC pair list, source
semantics, and identity-exception policy.  Re-running `manifest` for the same
snapshot is an append-only violation.  `replay` reads independent daily
snapshots; it does not carry the newest catalog backward.

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

## Universe approval gate

`config/crypto_breadth_universe_policy.json` is deliberately
`UNRATIFIED`.  The repository has not approved the final exclusion list or
minimum breadth coverage.  The production helper refuses `transform` and
`replay` until a versioned policy has all of the following:

- `approval_status = RATIFIED` and an effective date;
- exact allowed asset and pair statuses;
- an explicit canonical-asset exclusion list;
- a ratified minimum asset count;
- the fixed source-coverage rule over the captured pair catalog.

This universe is expressly `breadth_source_coverage_not_investable`.  It does
not claim liquidity, capacity, tradability for an Atlas portfolio, or exchange
coverage beyond Kraken.

## Output and missing policy

For each included member, the helper emits canonical/source identity, exact
pair, T-1 and T dates, both closes, and `ADVANCE`, `DECLINE`, or `UNCHANGED`.
BTC is kept as a separate reference.  `alt_participation` excludes BTC and
contains only asset counts and fractions.

Every pair selected by the ratified policy must have a valid OHLC response.  A
missing pair, source error, date gap, partial catalog, checksum mismatch,
identity collision, or absent BTC reference fails closed.  Missing data is
never converted to zero or neutral.

All outputs keep these authorities false:

- breadth classification;
- threshold;
- Regime score;
- Production wiring;
- trading action.

## Offline commands

The helper makes no request.  Capture is intentionally not connected to a
workflow in this change.  Once a separately reviewed capture exists:

```bash
python3 .github/scripts/crypto_breadth.py manifest \
  --snapshot-dir /tmp/crypto-breadth/raw/2026-08-20 \
  --capture-version crypto-breadth-capture/v1

python3 .github/scripts/crypto_breadth.py validate \
  /tmp/crypto-breadth/raw/2026-08-20

python3 .github/scripts/crypto_breadth.py transform \
  /tmp/crypto-breadth/raw/2026-08-20 \
  --universe-policy /tmp/ratified-crypto-breadth-policy.json \
  --out /tmp/crypto-breadth.json

python3 .github/scripts/crypto_breadth.py replay \
  /tmp/crypto-breadth/raw \
  --universe-policy /tmp/ratified-crypto-breadth-policy.json \
  --out /tmp/crypto-breadth-replay.json
```

No command writes a tracked breadth factor by default.
