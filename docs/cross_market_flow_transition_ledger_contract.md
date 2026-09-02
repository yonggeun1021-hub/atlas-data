# P2-COM-03 Cross-Market Flow Transition Ledger contract

This ledger consumes only the exact immutable
`capital_flow_posture_reference/v1` output produced by P2-COM-02. It does not
read market inputs independently, recompute scores, create a flow inference,
or change P1-COM-05 or P2-COM-02 policy.

Each accepted observation appends one hash-chained entry containing the exact
P2-COM-02 packet, file SHA-256, payload SHA-256, generation ID, policy lineage,
and upstream source lineage. `previous_state` and `current_state` are copies of
the producer's cross-market state. Existing `UNKNOWN` values and JSON `null`
values remain unchanged.

## Two clocks, one order key

Contract `/1` used a single field for two different facts: the market session
date when a comparison existed, and the producer wall clock when it did not.
Those clocks move differently, so a genuine `UNKNOWN` to comparable recovery
could arrive with a session date at or behind the last stored key and be
rejected as stale or same-date drift. The transition the ledger exists to
record was the transition it could not record.

`/2` separates them:

- `source_generated_date_kst` is the only order key. It is the producer
  `generated_at` converted to a fixed `+09:00` date. Asia/Seoul has no daylight
  saving, so the offset is exact and needs no time-zone database. The name
  claims only what it proves: the date the source packet was generated. It is
  not a ledger runtime observation date, because `generated_at` is inherited
  from upstream, not stamped when the ledger runs.
- `comparison_as_of_date` stays a market fact inside
  `current_state.cross_market_flow`. It is never an order key, and it may stall
  or move backwards without blocking an append.

Monotonicity, revision drift, and staleness are decided only on the order key.

## Continuing the /1 chain

A `/2` ledger always continues the frozen `/1` chain. The `/1` evidence file is
pinned by path, file SHA-256, payload SHA-256, tail entry SHA-256, and height in
both the contract file and the code, and the ledger's declared `predecessor` is
never trusted: it must equal the projection re-derived from that verified file.

The projection carries exactly what continuity needs — per-state first-seen and
observation tallies, the tail state, tail persistence, and aggregate mode counts
— so `first_seen`, the four persistence counters, `observation_mode_counts`,
`counted_natural_observations`, and revision height continue across the
migration instead of restarting. `observation_count_scope` is
`CUMULATIVE_INCLUDING_PREDECESSOR` so a reader cannot mistake the totals for
`/2`-only counts. The first `/2` entry has revision `predecessor.height + 1`,
its `previous_state` is the `/1` tail state, and `INITIAL` is not a legal `/2`
transition.

`/1` evidence is never rewritten. A production fresh chain is forbidden: a
missing pointer or missing predecessor evidence fails closed with
`PREDECESSOR_REQUIRED_MISSING` rather than starting a new history.

## Observation modes and counting

- `NATURAL` is an unforced scheduled run and counts toward persistence.
- `MANUAL` is an explicitly dispatched observation and does not count.
- `RECOVERY` is a retry or repair observation and does not count.
- `REPLAY` is a historical reconstruction label and does not count.

`first_seen` records when the exact semantic state was first observed, including
observations on the `/1` chain. `confirmed_at` is always `null` because no
P2-COM-03 confirmation policy has been ratified. Persistence is descriptive
only: it records all observations and NATURAL observations without interpreting
a count as confirmation.

## Idempotence and fail-closed rules

An identical P2-COM-02 source packet is a no-op that writes nothing at all,
including while the canonical pointer is still `/1`; the pointer is not rewritten
into `/2` shape by a repeat observation. A different packet on an
already-ingested `source_generated_date_kst` is revision drift and fails closed.
An older key is stale and fails closed. There is deliberately no wall-clock
freshness threshold.

Transition labels are structural audit facts, not investment states:

- `REVERSAL` requires an exact leader/laggard swap with both sides non-null.
- `INVALIDATION` is a comparable P2-COM-02 state becoming `UNKNOWN`.
- `RECOVERY` is an `UNKNOWN` state becoming comparable again.
- `CHANGED` and `UNCHANGED` describe the remaining cases. `INITIAL` exists only
  on the `/1` chain.

No transition label confirms actual donor/receiver money flow. Numeric
thresholds, market weights, capital, Stage, Buy, Action, Order, Production, and
Trading authority remain absent or false.

## What this contract does not close

Integrity here is recomputable hashes, not signatures. A single tampered field
is caught because every derived value is recomputed from the embedded source
packet, and the predecessor identity is anchored in two places. Both anchors are
still inside this repository: a writer able to change code, contract, ledger, and
evidence together can produce a self-consistent forgery. Closing that needs an
external head/height anchor with a distinct signer, which is tracked separately
and is not implemented here. `REPLAY` also remains a label only — the producer
re-derivation check means a historical packet cannot be re-ingested once the
inputs move on. Both are deliberately out of `/2` scope.
