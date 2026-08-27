# Regime Replay Harness Contract (P1-COM-04)

## Purpose

The harness verifies one narrow property before Atlas has an approved Regime
score: replaying the same validated evidence must produce the same canonical
`regime_output/v1` envelope. It covers US, Korea, and Crypto in one report and
retains the fields needed to explain the result.

This is a determinism check, not a historical performance claim. The current
source contract is `PRE_SCORE_UNKNOWN_ONLY`, so every accepted case remains
`UNKNOWN`.

## Input boundary

Input contains sorted, unique case IDs. Each case supplies a `first` envelope
and an independently obtained `rerun` envelope. Both must pass the complete
`regime_output/v1` validator before comparison. At least one valid pair is
required for each market:

- `US`
- `KR`
- `CRYPTO`

The harness rejects missing markets, duplicate or unsorted IDs, invalid source
envelopes, floating-point values, and any canonical-byte difference between a
first run and its rerun.

## Explainable report

For every accepted case, the report retains:

- the canonical source-output SHA-256;
- market, generated time, regime, direction, and confidence;
- required/defined/missing axis coverage;
- oldest observation and latest availability boundaries;
- evidence SHA-256 by axis;
- source warnings.

The report itself is deterministic and can be revalidated against the exact
source payload. A modified report or source fails closed.

## Authorization boundary

`DETERMINISM_VERIFIED_PRE_SCORE` means only that the supplied replay pair is
valid and byte-identical. It does not mean the market has enough sensor
coverage, that a Regime has been classified, or that a strategy is eligible.

The minimum-coverage policy is still `UNRATIFIED`. Thresholds, weights, Regime
score/classification, hysteresis, strategy eligibility, Production wiring, and
trading action all remain unauthorized. The module has no network call,
scheduled workflow, tracked report, or paid data dependency.

## Canonical replay-population readiness

`regime_replay_population_readiness/v1` closes the gap between an available
replay capability and an actually eligible historical case population. It pins
the exact replay-harness contract bytes and independently validates the retained
P1-COM-05 candidate evidence chain before reporting readiness.

The current canonical candidate supports only `MINIMUM_COVERAGE`. The other
eight required components—normalization, classification, direction, confidence,
stress override, invalidation, hysteresis, and replay acceptance—remain blocked.
The candidate is therefore `CANDIDATE_BLOCKED`, eligible markets and cases are
both zero, and no historical outcome is evaluated. The readiness status is
`NOT_COMPUTABLE_POLICY_CANDIDATE_BLOCKED`; it is not an empty successful replay.

If a later candidate becomes ready, this v1 module deliberately fails instead
of manufacturing a case population. A separately reviewed population contract
must then define PIT-safe cases and comparison semantics. The readiness slice
does not create thresholds, cases, performance claims, workflows, or any Stage,
Buy, Action, Proposal, Order, Production, or trading authority.
