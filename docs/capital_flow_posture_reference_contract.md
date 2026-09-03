# P2-COM-02 Cross-Market Capital Flow reference contract

`portfolio/capital_flow_posture_reference.py` is a read-only PAPER diagnostic.
It compares same-date P1 market reference scores and emits relative candidates;
it does not infer actual donor/receiver money flow, allocate capital, or create an
action or order.

## Exact upstream identity

Every packet binds two independently revalidated inputs into `generation_id`:

1. the exact P1 PAPER Regime packet path, file SHA-256, schema version, contract
   version, payload SHA-256, and generation ID; and
2. the exact P2-COM-01 Cross-Asset Flow Evidence contract path, file SHA-256,
   schema version, contract version, output schema version, required `UNKNOWN`
   cross-market assessment, and false actual-flow authority.

Changing either byte identity or semantic contract changes the generation ID.
A re-signed output cannot pass `validate_reference()` unless it is re-derived
from the exact retained inputs.

## Relative candidate semantics

When a unique same-date leader and laggard exist, the packet emits only:

- `RELATIVE_ATTRACTOR` for the leader;
- `RELATIVE_DONOR` for the laggard; and
- `RELATIVE_STRENGTH_REFERENCE` as the evidence class.

`actual_flow_claim` remains `UNKNOWN`. `confidence` remains `null`.
Transition confirmation and persistence are owned by the separate P2-COM-03
append-only ledger, while confidence and invalidation remain
`NOT_COMPUTABLE_POLICY_UNRATIFIED` until separately ratified policy exists.

## Authority boundary

The only true authorities are display and relative-strength comparison. Gross
exposure, cash target, cross-market allocation, position size, Stage, Buy,
Action, Order, Production, and Trading authority are all exactly boolean
`false`. Numeric zero cannot substitute for a boolean, and `null` values are not
promoted into numeric targets.
