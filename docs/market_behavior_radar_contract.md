# Market Behavior Radar Contract (P3-07)

Status: policy-neutral relative-strength and volume-ratio feature capability
implemented. Default anomaly thresholds, benchmark selection, lookback,
cross-market cadence, source hierarchy, candidate ranking, and live population
remain absent, unratified, or unimplemented.

## Purpose

`discovery/market_behavior.py` consumes caller-supplied exact-session price and
volume series for US, Korea, or Crypto assets. Every window names its market,
benchmark asset, price basis, exact expected sessions, and source identity. The
helper records transparent price/volume behavior features; it does not discover
a benchmark, choose an adjustment basis, fetch data, or infer an anomaly.

The accepted partial-coverage source identities are:

- US: `tiingo_us_daily_price` on `api.tiingo.com`;
- Korea: `krx_open_api_stock_daily` on `data-dbg.krx.co.kr`; and
- Crypto: `kraken_public_api` on `api.kraken.com`.

This allowlist confirms provider identity only. It is not a source ranking or
fallback order.

## Raw feature contract

At least three ordered, unique, exact session dates are required. Every series
in a window must cover exactly those dates and use the window's explicit price
basis. Close and volume values must be finite decimal strings; closes are
positive and volumes are nonnegative.

For asset cumulative gross return `Ga` and benchmark cumulative gross return
`Gb`, relative strength is:

```text
Ga / Gb - 1
```

The latest session volume is also divided independently by:

- the arithmetic mean of all prior-window volumes; and
- the median of all prior-window volumes.

These are two named observations, not interchangeable anomaly definitions. A
zero prior baseline produces `null` and `ZERO_BASELINE_UNKNOWN`; it is never
replaced with zero, neutral, infinity, or another series. All divisions use
50-digit intermediate precision and 12-decimal HALF_EVEN output.

Output includes the asset and benchmark source identities, exact session
boundary, explicit price basis, and feature values. It never includes raw or
reconstructive price/volume rows.

## Optional externally ratified policy

The repository intentionally contains no default candidate-policy file. A
caller may supply `market_behavior_candidate_policy/1` explicitly. A usable
policy must be `RATIFIED`, carry a nonempty ratifier and UTC ratification time,
be ratified no later than the packet `as_of_utc`, and contain a unique rule for
the exact market/window pair.

Each rule explicitly supplies:

- relative-strength minimum;
- one volume method: `LATEST_VS_PRIOR_MEAN` or
  `LATEST_VS_PRIOR_MEDIAN`; and
- volume-ratio minimum.

A case is recorded only when both numeric comparisons pass. An absent,
unratified, out-of-period, or nonmatching policy creates no case. Unratified
policy objects may not carry ratification proof. Policy rules never provide a
default benchmark, session window, source, or cadence; those remain explicit
input responsibilities.

## Case and authority boundary

A policy-gated case preserves the asset and benchmark lineage, window,
transparent comparison values, policy ID, policy SHA-256, ratifier, and
ratification timestamp. Every case retains:

- `importance = UNRATIFIED`;
- `candidate_rank = null`;
- `investable_eligible = false`;
- `stage_transition = null`; and
- `action = null`.

Source ranking, importance ranking, candidate ranking, Rule evaluation, Stage
promotion, Production, and trading authorities are false. A radar case cannot
be converted into Ready, Buy, or an order by this capability.

## Offline command

Raw feature observation:

```bash
python3 discovery/market_behavior.py /tmp/market-behavior-input.json \
  --out /tmp/market-behavior.json
```

Optional externally ratified policy evaluation:

```bash
python3 discovery/market_behavior.py /tmp/market-behavior-input.json \
  --policy /tmp/ratified-market-behavior-policy.json \
  --out /tmp/market-behavior.json
```

The module has no network client and writes only the requested atomic output.
It does not publish a tracked radar, choose operational thresholds, modify
existing Discovery cases, or wire a workflow.
