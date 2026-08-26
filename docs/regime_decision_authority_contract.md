# Regime Decision Authority Boundary (P1-COM-05)

## Current result

`regime_decision_authority/v1` binds one validated `regime_output/v1` packet to
its independently rebuilt `regime_minimum_coverage/v1` result. It distinguishes
two reasons Atlas cannot classify a market yet:

- `BLOCKED_COVERAGE`: one or more of the five required axes are undefined.
- `BLOCKED_POLICY_UNRATIFIED`: all five axes exist, but the decision policy is
  not authorized.

Both results retain `regime=UNKNOWN`, `direction=UNKNOWN`, and
`confidence=null`. `NEUTRAL` is never a missing-data or missing-policy fallback.

## Policy boundary

The repository has no approved decision-policy registry. The following
components are therefore explicitly absent or unratified: factor normalization,
freshness, aggregation weights, classification thresholds, direction,
confidence, stress override, invalidation, and hysteresis. An input packet may
not self-declare any of them approved; this version accepts no external policy
payload and exposes no classification path.

The output binds both source packets by canonical SHA-256. The minimum-coverage
packet is re-derived from the Regime output, so a re-signed or independently
edited gate cannot be substituted.

## What this does not authorize

This contract does not normalize factors, assign weights, set thresholds,
classify `RISK_ON`, `NEUTRAL`, `RISK_OFF`, or `STRESS`, calculate confidence,
run an approved replay, select a strategy, change a candidate or Stage, allocate
capital, submit orders, or enable Production/trading. Those require a separately
ratified policy and a future contract version.
