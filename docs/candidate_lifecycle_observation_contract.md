# P8-12 Forward-only Candidate Lifecycle Shadow Observation

## Purpose

The Dynamic Clock now knows the exact UTC instant at which one operational
run evaluated its candidates.  That instant is **not** the historical trigger
time.  This contract therefore starts a new, forward-only observation chain:

- candidates already present in the first natural sample remain
  `PRE_BASELINE_FIRST_SEEN_NOT_COMPUTABLE`;
- a subject first appearing after a validated natural baseline receives the
  exact Atlas observation instant;
- later semantic change, first observed absence, and reappearance are stamped
  only when Atlas actually observes them;
- no old date-only field is rewritten or promoted to timestamp precision.

## Chain boundary

Only Candidate Validity v4 observations labelled
`NATURAL_OPERATIONAL_SAMPLE` advance the chain.  Manual and local runs are
standalone diagnostics.  Repeated evaluations of unchanged evidence are
retained as evaluations but explicitly labelled
`DUPLICATE_EVIDENCE_BASIS_EVALUATION_ONLY`; they are not distinct validity
evidence.

Each record is content-addressed and references both its independently
rebuildable Candidate Validity observation and the preceding natural
lifecycle record.  Loading a record recursively rebuilds the whole referenced
chain and rejects missing, non-canonical, path-traversing, hash-drifted, or
non-monotonic inputs.

## Semantics

The timestamp means “Atlas first observed this candidate/lifecycle event at
this operational evaluation.”  It never means the source event occurred at
that instant.  Market and subject form a stable observation key; no ticker,
provider, market-scope, or canonical-identity inference is performed.

## Authority

This is `PROVISIONAL_SHADOW_OBSERVATION_ONLY`.  Candidate freshness remains
`NOT_COMPUTABLE_CANDIDATE_FRESHNESS_UNRATIFIED`.  Risk Capacity and P8-13
remain locked.  Stage, Buy, Action, Order, Production, and trading authority
remain false and capital remains zero.
