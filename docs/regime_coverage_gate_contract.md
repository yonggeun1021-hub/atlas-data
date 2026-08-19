# Regime Minimum Coverage Gate (P1-COM-02)

## Purpose

`regime_coverage_gate/v1` consumes a validated `regime_output/v1` envelope and
produces a deterministic eligibility audit. It does not classify a market.
The current minimum-coverage policy is `UNRATIFIED`, so its only authorized
result is:

```text
gate_result = BLOCKED
classification_eligible = false
regime = UNKNOWN
direction = UNKNOWN
confidence = null
```

This remains true even when all five axes are `DEFINED`. Complete evidence is
not permission to invent a minimum, score, threshold, or weight.

## Missing axes

Every undefined axis produces an explicit reason code. In particular,
`BREADTH_UNDEFINED` prevents an authoritative Regime without turning the
market into `NEUTRAL`. Missing axes are derived from the validated source
envelope and cannot be hidden by editing the gate artifact.

## Source binding

The gate stores a canonical SHA-256 of the full source envelope. Validation
requires the source envelope again, validates it under `regime_output/v1`, and
recomputes the complete gate result. Reordering JSON object keys does not
change the hash, while changing evidence, coverage, warnings, or timestamps
does.

## Authority boundary

This version permanently pins the policy to `UNRATIFIED` and the result to
`BLOCKED`. Editing the config to ratify a minimum or enable classification is
a contract violation. A future approved minimum requires a new version after
replay. Score, threshold, weight, strategy, Production, and trading authority
remain false.
