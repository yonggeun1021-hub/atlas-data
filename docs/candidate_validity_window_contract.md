# P8-12 Candidate Validity Window — Recommendation A

## Scope

This contract classifies temporal freshness only. It does not decide canonical
security identity, Risk Capacity, P5-06/P7-08 policy, P8-13 entry eligibility,
position size, capital, orders, broker actions, or any REAL/live/Production/
Trading action.

The authority record is
`P8-12-CANDIDATE-VALIDITY-WINDOW` version `1`. Its exact content and approval
evidence are pinned by SHA-256 in
`config/candidate_validity_window_authority_registry.json`. The rule is
forward-only from `2026-09-02T21:18:58Z`; historical backfill is forbidden.

## Ratified rule

The following observed short-trigger families use a UTC elapsed-time window
of exactly `172800` seconds:

- `FLOW_REVERSAL`
- `INVALIDATION_TRIGGER`
- `PRICE_CONFIRMATION`
- `RELATIVE_STRENGTH_REVERSAL`

For each active candidate, `t0` is the latest qualifying, exact, natural
forward lifecycle transition's `operational_evaluated_at_utc`. Qualifying
events are `FIRST_SEEN_EXACT_FORWARD_ONLY`, `CONTINUING_CHANGED`, and
`REAPPEARED_OBSERVED_FORWARD_ONLY`.

The interval is half-open: `[t0,t0+172800)`. A candidate is
`FRESH_TEMPORAL` strictly before the endpoint and `STALE_TEMPORAL` at or after
the endpoint.

`CONTINUING_UNCHANGED`, manual/local observations, retries, and duplicate
evidence do not refresh `t0`. A first natural absence immediately produces
`MISSING_INVALID`, with no grace period. Reappearance begins a new `t0` and
does not carry forward unused time. A pre-baseline candidate without an exact
forward `t0` remains `NOT_COMPUTABLE_NO_EXACT_FORWARD_T0`.

The following unobserved families remain
`NOT_COMPUTABLE_UNVALIDATED_TRIGGER_FAMILY`:

- `CATALYST_APPROACH`
- `EXPECTATION_DISLOCATION`
- `FUNDAMENTAL_REVISION`

## Evidence and fail-closed validation

The assessment must independently rebuild the append-only natural lifecycle
tip through all parents and retained Candidate Validity/Dynamic Clock source
artifacts. Canonical bytes, content hashes, parent links, exact UTC timestamps,
PIT order, the current candidate-identity observation, and the P8-10 authority
structure must all validate.

An unresolved canonical identity is reported separately and does not become an
identity grant. A subject for which P8-10 has no supported evidence source may
still receive a temporal diagnosis, while an explicit P8-10 link failure is
`NOT_COMPUTABLE_P8_10_LINK_FAILED`. Any global authority, chain, source,
identity-observation, hash, parent, PIT, or P8-10 structure failure aborts the
assessment instead of producing a fresh result.

## Output and operational boundary

`clock/candidate_validity_window.py` writes only
`evidence/operational/dynamic_clock/candidate_validity_window_assessment.json`
on the existing P8-12 workflow. The output contains no quantity, sizing,
capital allocation, order intent, or broker instruction. Candidate remains
`NONE`; capital remains `0`; trade proposal remains `null`; Stage, Buy,
Action, Order, Production, and Trading authority remain `false`.

Workflow dispatch remains diagnostic/manual evidence and cannot advance the
natural lifecycle chain. Only an upstream successful `workflow_run` can create
a new natural source observation, and even that run must pass all independent
validation above.
