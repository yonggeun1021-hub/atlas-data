# Crypto Leadership Contract (P1-CR-07)

Status: offline data contract implemented; universe, calculation, and taxonomy
policies unratified; no leadership, Regime, Production, or trading authority.

## Purpose

This contract can reproduce raw relative-strength observations for assets and
for explicitly approved BTC, ETH, Alt, sector, and chain groups.  It consumes
the independent, append-only daily Kraken snapshots qualified by P1-CR-06.  It
does not call Kraken, capture new data, select an investable universe, declare a
leader, rank assets, create a score, or publish a tracked factor.

For every day in an approved lookback window, the raw daily gross return is:

```text
latest finalized close / previous finalized close
```

The current Kraken OHLC row remains excluded by the source contract.  An asset
or group cumulative gross return is the product of its exact daily gross
returns.  Raw relative strength is:

```text
cumulative gross return / BTC cumulative gross return - 1
```

This arithmetic definition is a supported contract capability, not approval of
a lookback, taxonomy, coverage minimum, classification, or threshold.

## Three independent approval gates

The helper requires all three policy inputs to be `RATIFIED` and effective for
the full window:

1. `crypto_breadth_universe_policy.json` and its exclusion taxonomy — the
   as-captured Top-100 source coverage universe from P1-CR-06.  It is not an
   investable universe.
2. `crypto_leadership_policy.json` — exact lookback, daily group aggregation,
   required groups, and minimum daily coverage.
3. `crypto_asset_taxonomy.json` — effective-dated canonical-asset mappings to
   BTC/ETH/Alt buckets, sectors, and chains.

The CR-06 universe and exclusion rules are ratified, but CR-07's leadership
lookback and sector/chain taxonomy remain `UNRATIFIED`.  The helper therefore
still refuses to calculate with all repository defaults.

## Point-in-time and coverage rules

The window is an exact sequence of contiguous UTC calendar days.  Each point is
rebuilt from that day's own captured `Assets`, `AssetPairs`, and OHLC bytes.
The newest catalog is never carried backward.  The effective taxonomy is also
looked up separately for every member on every day.

For an asset present in adjacent snapshots, the earlier latest finalized close
must equal the later previous finalized close.  A mismatch is source drift and
fails closed.  Assets that enter or leave the as-captured universe are listed
as `partial_window_assets`; they are not silently presented as full-window
asset observations.  Daily group membership may change point in time, but each
required group must meet its ratified minimum on every day.

The only supported group method in v1 is
`equal_weight_daily_rebalanced`: average the exact member gross returns for
that day, then compound the daily group returns.  Merely supporting this method
does not ratify it; the policy must select it explicitly.

Missing dates, current-catalog backfill, an unratified policy, taxonomy gaps or
overlaps, insufficient group coverage, checksum or manifest drift, partial raw
responses, and adjacent-close mismatches all fail.  None are converted to zero,
neutral, or unknown market direction.

## Authority boundary

Outputs are `OBSERVED_UNCLASSIFIED` and keep all of these false:

- leader classification;
- ranking;
- threshold;
- Regime score;
- Production wiring;
- trading action.

The deterministic arrays are ordered by canonical or policy identifier only.
Their order is not a market ranking.

## Offline command

After separately ratified policy files and qualified snapshots exist:

```bash
python3 .github/scripts/crypto_leadership.py transform \
  /tmp/crypto-breadth/raw \
  --universe-policy /tmp/ratified-crypto-universe.json \
  --exclusion-taxonomy /tmp/ratified-crypto-exclusion-taxonomy.json \
  --leadership-policy /tmp/ratified-crypto-leadership.json \
  --taxonomy /tmp/ratified-crypto-taxonomy.json \
  --end-date 2026-08-19 \
  --out /tmp/crypto-leadership.json
```

The command makes no network request and writes no tracked factor by default.
