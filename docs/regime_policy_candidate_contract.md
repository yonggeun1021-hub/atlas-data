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
