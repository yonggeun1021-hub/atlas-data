# Regime Common Output Contract (P1-COM-01)

## Current authorization boundary

The contract records the approved future vocabulary for US, Korea, and Crypto,
but `regime_output/v1` runs in `PRE_SCORE_UNKNOWN_ONLY` mode.  Runtime output is
restricted to:

```text
regime = UNKNOWN
direction = UNKNOWN
confidence = null
```

`NEUTRAL` is an observed market state candidate.  `UNKNOWN` means Atlas lacks
an authoritative data contract, axis coverage, or approved scoring method.
The validator rejects any attempt to use `NEUTRAL` as a missing-data fallback.

## Five-axis envelope

Every output contains the same ordered axes:

1. `TREND`
2. `BREADTH`
3. `RISK_VOL`
4. `LIQUIDITY`
5. `LEADERSHIP`

An axis is either `DEFINED` with an observation date, UTC availability time,
age, transform version, evidence URI, and SHA-256; or `UNDEFINED` with null
evidence fields and an explicit warning.  Missing axes are never silently
removed from the envelope.

## Coverage and timestamps

Coverage lists required, defined, and missing axes and retains an unratified
minimum-gate status.  `evidence_as_of.oldest_observation_date` is the oldest
defined-axis observation, while `available_as_of` is the latest defined-axis
availability.  Each axis keeps its own timestamp and age so a fresh axis cannot
hide an older one.

All availability timestamps are UTC seconds with a trailing `Z`.  Observation
dates are checked against the output market's local timezone.  Future evidence
or availability fails closed.

## No score path

This module builds and validates an output envelope only.  It does not compute
thresholds, weights, confidence, direction, Regime scores, strategy eligibility,
Production wiring, or trading actions.  All related authority flags are false.
Moving beyond UNKNOWN requires a new approved contract version after replay and
minimum-coverage ratification.
