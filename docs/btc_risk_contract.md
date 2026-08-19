# BTC Risk / Volatility Contract (P1-CR-05)

## Qualified input

The risk transform reuses the immutable Kraken BTC/USD UTC daily capture from
P1-CR-04.  It makes no additional API request and validates the raw response,
SHA-256, manifest, capture vintage, exact daily continuity, and structural
exclusion of the final current candle before calculating any feature.

All values are derived from finalized UTC closes.  The capture's current
not-yet-committed row cannot enter a transform or replay point.

## Realized volatility

`btc_risk/v1` defines 30-return annualized realized volatility as:

```text
r(d) = Close(d) / Close(d-1) - 1
RV30(d) = sqrt(mean(r(d-29..d)^2) * 365)
```

This is a root-mean-square estimator over simple close-to-close returns.  It
does not subtract the sample mean.  Decimal arithmetic uses 50-digit working
precision and `ROUND_HALF_EVEN` to 12 decimal places at the output boundary.

## Drawdown

The transform uses exactly 90 finalized closes and emits two descriptive
fractions:

- current drawdown from the highest close in the 90-close window;
- maximum peak-to-trough drawdown observed inside the same window.

Peak and trough dates are retained.  No interpolation, shorter fallback,
cross-venue substitution, or missing-day fill is permitted.

## Stress feature boundary

The stress transform is a versioned feature vector containing RV30, current
90-day drawdown, and maximum 90-day drawdown.  Its calibration status is
`UNDEFINED_UNCALIBRATED`; no threshold or stress class is applied.  A later
approval must supply calibration and replay evidence before any Regime use.

## Replay semantics

`btc_risk_replay/v1` evaluates every eligible prefix using only data at or
before that point.  This proves transform mechanics and prevents future-row
leakage.  Because all prefixes come from one later immutable capture, replay
mode is explicitly `as_captured_prefix_only` and is **not historical PIT**.
It cannot be used for threshold research, performance claims, or backtesting.

## Missing data and authority

A source error, checksum or manifest mismatch, stale boundary, malformed row,
or any gap in the finalized series fails closed.  The output does not authorize
a stress threshold, stress classification, Regime score, Production wiring,
or trading action.

Code and synthetic replay regressions do not close P1-CR-05.  Closure requires
the shared P1-CR-04 scheduled capture to run and reproduce the risk transform
from committed bytes.
