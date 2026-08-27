# P1-COM-05 Regime Policy Candidate Evidence Inventory v1

## Investment purpose

The Regime decision must help Atlas decide whether risk can be taken in US,
Korea, and Crypto before flow, theme, or asset selection is interpreted. That
requires an aggregation policy, but a convenient threshold is not evidence.

This slice replaces the earlier draft consensus classifier with a fail-closed
inventory. It asks one narrower question: **does every output-affecting policy
parameter have exact, point-in-time eligible evidence?** It does not classify a
market and it does not choose a policy.

## Current input and PIT boundary

The builder consumes a `regime_policy_candidate_manifest/v1` plus immutable
`regime_policy_parameter_evidence/v1` JSON documents. Every evidence reference
is a repository-relative path, byte SHA-256, and evidence ID. Evidence must be
available no later than the candidate's `decision_at`; an expired document is
`STALE_EVIDENCE`, and a future document is `FUTURE_EVIDENCE`.

Required policy components are:

- market-specific normalization
- minimum coverage
- Regime classification
- direction
- confidence
- stress override
- invalidation
- hysteresis
- replay acceptance

Missing or unspecified values remain blocked. They are never converted to zero,
neutral, false, or a default threshold.

## Evidence meaning

The supported evidence kinds are:

- `EMPIRICAL_DISTRIBUTION`
- `HISTORICAL_EPISODE`
- `CIO_DOCTRINE`
- `EXTERNAL_RESEARCH`
- `UNSUPPORTED`

An evidence kind is not automatically sufficient. Doctrine or external
research supports a parameter only when the source record contains an explicit,
equal parameter value. A qualitative principle never justifies a numeric,
boolean, text, or structured value. An empirical or historical statistic cannot
support a numeric/boolean policy value when it is derived from a single
observation or a single observation date.

The builder independently reloads the exact bytes and re-derives the inventory.
Path traversal, SHA drift, evidence-ID substitution, schema changes, or result
tampering fail closed.

## State semantics

- `CANDIDATE_BLOCKED`: one or more required components lacks a supported value.
- `CANDIDATE_READY`: every required component has at least one eligible evidence
  claim for the exact proposed value.

`CANDIDATE_READY` means only that the candidate can be supplied to a separate
replay comparison. It does **not** mean selected, recommended, ratified, correct,
or Production eligible. Replay population remains `NOT_COMPUTABLE` in this
inventory because the population and outcome comparison belong to P1-COM-04 and
P1-COM-03.

## Counterexamples defined before the happy path

The contract rejects or blocks:

- a `3-of-5`, weight, threshold, or confidence value with no evidence;
- a numeric value justified by doctrine that contains only a qualitative
  principle;
- a minimum, maximum, median, or other statistic from one observation;
- evidence first available after `decision_at`;
- stale evidence whose `valid_through` has passed;
- a missing file, mismatched claim, or `UNSUPPORTED` evidence;
- a re-signed manifest, evidence file, inventory, or authority mutation.

## Authority boundary

Only candidate evidence inventory is authorized. Candidate selection, policy
recommendation, policy ratification, runtime classification, hysteresis,
strategy, Stage, Buy, Action, Proposal, Order, Production, and trading authority
remain false. No workflow, cron, briefing, Portal, private-account, broker, or
order path is changed by this slice.

## First canonical population: minimum coverage only

`regime_policy_candidate_population/v1` binds the user-ratified P1-COM-02
`ALL_REQUIRED_AXES_5_OF_5` policy to `MINIMUM_COVERAGE`. The population contract
pins the exact policy and candidate-contract bytes, PR #73 source and merge
commits, the policy's availability time, and the canonical P1-COM-02 WBS page.
The builder independently reconstructs all three retained artifacts:

- `minimum_coverage_evidence.json`
- `candidate_manifest.json`
- `candidate_inventory.json`

Only `MINIMUM_COVERAGE` becomes `SUPPORTED`. The other eight components remain
`BLOCKED`: seven have exact negative `UNSUPPORTED_EVIDENCE`, while
`REPLAY_ACCEPTANCE` still has `EVIDENCE_MISSING`. The candidate as a whole
remains `CANDIDATE_BLOCKED` and replay remains `NOT_COMPUTABLE`.

The retained evidence is coverage-only. It preserves the policy's explicit
`UNKNOWN` fail-close and `UNKNOWN != NEUTRAL` semantics but does not turn its
freshness or classification blockers into values. Changing five-of-five to
four-of-five, replacing an axis, or re-signing a changed evidence/manifest/
inventory chain fails against the independently pinned source policy.

## Explicit negative evidence: market normalization

`regime_policy_candidate_population/v2` distinguishes missing evidence from an
exact source that says a policy is not authorized. It pins the bytes of
`regime_decision_authority/v1` from PR #334. That boundary states:

- the repository policy registry is `ABSENT`;
- `FACTOR_NORMALIZATION` is `UNRATIFIED`;
- the reason is `FACTOR_NORMALIZATION_POLICY_UNRATIFIED`;
- `factor_normalization_authorized=false`.

The resulting `MARKET_NORMALIZATION` evidence is deliberately classified as
`UNSUPPORTED`. Its candidate value remains `UNSPECIFIED`, so its blocking
reasons are `UNSUPPORTED_EVIDENCE` and `VALUE_UNSPECIFIED`, rather than
`EVIDENCE_MISSING`. The structural `normalize_factors()` operation in
`regime_output/v1` validates fields, axis identity, and timestamps; it is not a
market-specific orientation, scale, threshold, or weighting policy.

This negative evidence cannot be re-signed into support. Changing the pinned
boundary to `RATIFIED`, changing authority to true, or replacing the retained
unsupported document with a fabricated explicit value fails closed. The
candidate remains `CANDIDATE_BLOCKED`, replay remains `NOT_COMPUTABLE`, and the
same downstream authority boundary remains false.

## Explicit negative evidence: regime classification

`regime_policy_candidate_population/v3` binds the next dependency to the same
exact PR #334 authority boundary. The repository contract states that both
`AGGREGATION_WEIGHTS` and `CLASSIFICATION_THRESHOLDS` are `ABSENT`, with reason
codes `AGGREGATION_WEIGHTS_ABSENT` and `CLASSIFICATION_THRESHOLDS_ABSENT`, and
that `classification_authorized=false`.

The retained `REGIME_CLASSIFICATION` evidence is therefore `UNSUPPORTED`; it
contains no weight, threshold, score, or proposed value. Its candidate value
remains `UNSPECIFIED`, and its blocking reasons are
`UNSUPPORTED_EVIDENCE` plus `VALUE_UNSPECIFIED`. Qualitative CIO doctrine that
requires a classification policy does not authorize numeric values.

Fabricating a weight or threshold, changing the exact source bytes, enabling
classification authority, re-signing the negative evidence as doctrine, or
claiming a ready candidate fails closed. `MINIMUM_COVERAGE` remains the only
supported component; normalization and classification are explicit negative
evidence and the candidate and replay remain blocked.

## Explicit negative evidence: remaining policy boundaries

`regime_policy_candidate_population/v4` retains five additional facts from the
same exact PR #334 authority bytes. `DIRECTION`, `CONFIDENCE`,
`STRESS_OVERRIDE`, `INVALIDATION`, and `HYSTERESIS` are each `UNRATIFIED` with
their exact reason code. Global `policy_ratification_authorized=false` is
required for all five, and component-specific authority flags are also checked
where the source contract defines one.

Each component receives a separate `UNSUPPORTED` evidence document with no
parameter value. This is not a policy proposal and does not convert an
unratified component into a default. A changed source byte, fabricated value,
re-signed artifact chain, or downstream authority toggle fails closed.
The authority boundary does not define or ratify an acceptance policy, so v4
leaves `REPLAY_ACCEPTANCE` as the sole `EVIDENCE_MISSING` component.

## Explicit negative evidence: replay acceptance

`regime_policy_candidate_population/v5` closes the inventory distinction for
the last component without inventing an acceptance rule. It pins both the PR
#334 policy-registry boundary and the PR #67 replay harness. The former says the
repository policy registry is absent and policy/classification authority is
false. The latter authorizes only `PRE_SCORE_UNKNOWN_ONLY` canonical-byte
determinism and reports `DETERMINISM_VERIFIED_PRE_SCORE`; it contains no
acceptance threshold, classified outcome comparison, or winner-selection rule.

`REPLAY_ACCEPTANCE` therefore receives exact `UNSUPPORTED` evidence with a null
value. All eight blocked components now carry explicit negative evidence and
the missing-evidence count is zero, but the candidate remains
`CANDIDATE_BLOCKED` and replay remains `NOT_COMPUTABLE`. Re-signing the evidence
with a fabricated UNKNOWN ratio, flip target, transition-delay limit, stress
rule, or winner selector fails against independent source re-derivation.

## Replay-population consumer boundary

P1-COM-04 consumes this retained inventory through
`regime_replay_population_readiness/v1`. It does not reinterpret a blocked
candidate as an empty successful replay: while the candidate remains blocked,
eligible market count and case count are both zero, outcome evaluation is false,
and replay population remains explicitly `NOT_COMPUTABLE`. The consumer pins
the v5 population contract and preserves all eight explicit-negative
components without inventing a value.
