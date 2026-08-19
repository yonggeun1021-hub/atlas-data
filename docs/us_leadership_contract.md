# US Leadership Contract (P1-US-06)

Status: offline contract capability implemented; source, benchmark, lookback,
point-in-time universe, and taxonomy policies remain unratified. No Leadership,
Regime, Production, or trading authority is granted.

## Purpose

This transform answers a cross-sectional question that US Trend does not:
which eligible assets and approved groups outperformed the approved market
benchmark, and how broad that relative outperformance was. It does not decide
whether the market itself rose or fell.

For each exact expected US session, asset gross return is close(t) / close(t-1).
Full-window raw relative strength is:

```text
asset cumulative gross return / benchmark cumulative gross return - 1
```

Daily relative participation is the fraction of eligible non-benchmark assets
whose daily gross return is strictly greater than the benchmark daily gross
return. A falling asset can therefore participate in Leadership when it falls
less than the benchmark. This must not be relabeled as positive market breadth
or Trend.

## Approval gates

Calculation requires three separately `RATIFIED` policies:

1. `us_leadership_policy.json`: source, price basis, benchmark, lookback,
   calendar, minimum coverage, and required groups.
2. `us_leadership_universe_policy.json`: effective-dated point-in-time source
   coverage membership. It is not an investable universe.
3. `us_asset_taxonomy.json`: effective-dated group assignments without overlap.

All repository defaults are intentionally `UNRATIFIED`. P1-US-04 has not yet
qualified historical PIT membership and delisted OHLCV, so this implementation
does not select SPY, QQQ, any universe, lookback, or taxonomy by itself.

## Temporal and retention boundary

`atlas_price_pit_contract.py` remains the temporal authority. Forward Shadow
inputs must be captured after the 20:15 America/New_York qualification cutoff.
Current RAW historical backfill is causal-research-only; adjusted history is
revised-sensitivity-only. Neither becomes an archived historical vintage.

Vendor price rows are consumed only from memory/stdin. Output contains bounded
cumulative relative-strength observations, daily participation counts, and
policy/source hashes, but no price rows or reconstructive series. Missing
sessions, split events, membership/taxonomy gaps or overlaps, insufficient
group coverage, source mismatch, and unratified policies fail closed.

## Authority boundary

Alphabetical output order is deterministic and is not a ranking. All leader
classification, ranking, Trend direction, Breadth direction, threshold,
Regime, Production, and trading flags remain false.

## Offline command

After separately ratified temporary policies and a transient input exist:

```bash
python3 .github/scripts/us_leadership.py transform \
  --leadership-policy /tmp/us-leadership.json \
  --universe-policy /tmp/us-universe.json \
  --taxonomy /tmp/us-taxonomy.json \
  --out /tmp/us-leadership-output.json < /tmp/transient-panel.json
```

The command makes no network request and does not write a tracked factor.
