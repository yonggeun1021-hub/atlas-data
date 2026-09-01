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

`first_seen` records when the exact semantic state was first observed.
`confirmed_at` is always `null` because no P2-COM-03 confirmation policy has
been ratified. Persistence is descriptive only: it records all observations
and NATURAL observations without interpreting a count as confirmation.

Observation modes have fixed counting semantics:

- `NATURAL` is an unforced scheduled run and counts toward persistence.
- `MANUAL` is an explicitly dispatched observation and does not count.
- `RECOVERY` is a retry or repair observation and does not count.
- `REPLAY` is a historical reconstruction and does not count.

Applying the identical P2-COM-02 source packet again is a byte-identical no-op,
including after restart. A different packet for an already ingested effective
observation date is revision drift and fails closed. An older observation date
also fails closed. There is deliberately no wall-clock freshness threshold.

Transition labels are structural audit facts, not investment states:

- `REVERSAL` requires an exact leader/laggard swap with both sides non-null.
- `INVALIDATION` is a comparable P2-COM-02 state becoming `UNKNOWN`.
- `RECOVERY` is an `UNKNOWN` state becoming comparable again.
- `CHANGED`, `UNCHANGED`, and `INITIAL` describe the remaining cases.

No transition label confirms actual donor/receiver money flow. Numeric
thresholds, market weights, capital, Stage, Buy, Action, Order, Production, and
Trading authority remain absent or false.
