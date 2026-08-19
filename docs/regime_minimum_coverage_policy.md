# Ratified Regime Minimum Coverage Policy

Status: ratified 2026-08-20; coverage eligibility only.

## Approved minimum

`regime_minimum_coverage/v1` requires all five common Regime axes:

1. `TREND`
2. `BREADTH`
3. `RISK_VOL`
4. `LIQUIDITY`
5. `LEADERSHIP`

The exact minimum is `5/5`. If any axis is `UNDEFINED`, the result is
`BLOCKED`, every missing axis is named, and the market remains `UNKNOWN`.
Missing evidence is never converted to `NEUTRAL`.

## What `COVERAGE_MET` means

When all five axes are `DEFINED`, the audit result is `COVERAGE_MET`. This is
only proof that the approved cardinality minimum was met. It is deliberately
not named `PASS`, and `classification_eligible` remains false.

Freshness thresholds are not ratified yet. Therefore a five-axis result still
contains `FRESHNESS_POLICY_UNRATIFIED` and cannot enter classification. This
prevents old evidence from receiving authority merely because it is present.

Score, weights, thresholds, Regime classification, hysteresis, strategy
eligibility, Production wiring, and trading actions remain unauthorized.

## Compatibility and source binding

The policy consumes a validated `regime_output/v1` envelope and binds the full
source through canonical SHA-256. `regime_output/v1` remains immutable and
pre-score; this new policy does not rewrite its historical contract. Consumers
must present the source envelope again when validating the coverage audit.

Changing evidence, timestamps, warnings, coverage, reasons, policy fields, or
authority flags invalidates the derived artifact. Object-key reordering does
not change the canonical source hash.
