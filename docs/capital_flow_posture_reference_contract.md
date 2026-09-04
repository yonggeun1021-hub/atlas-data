# P2-COM-02 Cross-Market Capital Flow reference contract

`portfolio/capital_flow_posture_reference.py` is a read-only PAPER diagnostic.
It compares same-date P1 market reference scores and emits relative candidates;
it does not infer actual donor/receiver money flow, allocate capital, or create an
action or order.

## Exact upstream identity

Every packet binds three independently revalidated inputs into `generation_id`:

1. the exact P1 PAPER Regime packet path, file SHA-256, schema version, contract
   version, payload SHA-256, and generation ID;
2. the exact P2-COM-01 Cross-Asset Flow Evidence contract path, file SHA-256,
   schema version, contract version, output schema version, required `UNKNOWN`
   cross-market assessment, and false actual-flow authority; and
3. the exact P2-COM-03 transition-ledger chain that was consumed — contract path
   and file SHA-256, contract version, the pinned predecessor payload SHA-256
   and height, and the consumed head entry SHA-256, ledger revision, observed
   timestamp, and source-generated KST date.

Changing any byte identity or semantic contract changes the generation ID.
A re-signed output cannot pass `validate_reference()` unless it is re-derived
from the exact retained inputs.

## Relative candidate semantics

When a unique same-date leader and laggard exist, the packet emits only:

- `RELATIVE_ATTRACTOR` for the leader;
- `RELATIVE_DONOR` for the laggard; and
- `RELATIVE_STRENGTH_REFERENCE` as the evidence class.

`actual_flow_claim` remains `UNKNOWN`. `confidence` remains `null`.

## Recorded transition and persistence

The P2-COM-03 append-only ledger owns transition history. This packet does not
re-implement it and does not throw it away: it loads
`data/latest_cross_market_flow_transition_ledger.json` through the ledger
module's own `load_contract()`, `load_predecessor()`, `verify_predecessor_ledger()`
and `validate_ledger()`, and reports what the chain already recorded:

- `previous_semantic_state` and its SHA-256, taken from the consumed head;
- `recorded_type`, the transition type the ledger last recorded;
- `pending_type`, this packet's state compared with that head using the ledger's
  own transition function, explicitly labelled `DERIVED_NOT_YET_RECORDED_BY_P2_COM_03`;
- `first_seen`, `observation_count`, `natural_observation_count`, and both current
  streak counts for this packet's semantic state.

Counting follows the ledger contract's `persistence_count_policy` exactly: only
`NATURAL` observations raise the natural counters, and `MANUAL`, `RECOVERY`, and
`REPLAY` are never promoted into natural evidence. The producer's numbers are
the ledger's own accounting **minus the append this packet has not yet earned**,
so `counts_current_packet` is always `false`.

### Self-observation and determinism

P2-COM-03 consumes this packet, so a ledger read back after the append contains
the observation of this very packet. Every entry whose `observed_at` equals this
packet's `generated_at` is dropped before anything is read. The ledger binds
`observed_at` to the producer `generated_at` and refuses two entries on one
source-generated KST date, so that equality selects exactly this packet's own
observations. The rebuild is therefore byte-identical before and after the
append, and the packet can never cite itself as its own prior evidence.

### Fail-closed boundaries

- A pointer that is present but unreadable, of an unsupported contract version,
  tampered, re-signed, or whose predecessor projection or embedded lineage
  drifts fails closed and produces no packet.
- A pointer that is **missing while the contract's pinned predecessor evidence
  exists** fails closed: an established chain whose canonical pointer vanished
  is a recovery problem, never a licence to erase recorded history.
- When no prior history is consumable at all — no ratified chain in the tree, or
  the only observation on the chain is this packet — transition stays `UNKNOWN`
  and persistence stays `NOT_COMPUTABLE_TRANSITION_LEDGER_ABSENT`. These cases
  collapse to one identical record on purpose, so the packet cannot distinguish
  them by its own bytes.

Recorded history is evidence, not confirmation. `confirmed_at` stays `null` and
`confirmation_status` stays `NOT_COMPUTABLE_POLICY_UNRATIFIED` because the
ledger's `confirmation_policy` is `UNRATIFIED_CONFIRMED_AT_NULL`; confidence and
invalidation likewise remain `NOT_COMPUTABLE_POLICY_UNRATIFIED` until separately
ratified policy exists. A persisting relative-strength state is still not proof
of actual money flow.

## Authority boundary

The only true authorities are display and relative-strength comparison. Gross
exposure, cash target, cross-market allocation, position size, Stage, Buy,
Action, Order, Production, and Trading authority are all exactly boolean
`false`. Numeric zero cannot substitute for a boolean, and `null` values are not
promoted into numeric targets.
