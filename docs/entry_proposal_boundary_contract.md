# P8-13 Entry Proposal Boundary

This boundary implements the P8-13 handoff without pretending that Atlas has
ratified entry, risk, sizing, position-management, or execution policy.

The exact P5-06/P7-08 readiness packet is independently validated first. Only
rows already marked `diagnostic_reviewable=true` become human-review material.
They remain diagnostic observations. They are not entry proposals.

The contract fixes these outputs:

- `p8_13_boundary = IMPLEMENTED_FAIL_CLOSED`
- `status = LOCKED_POLICY_UNRATIFIED`
- `proposed_action = NONE`
- entry zone, invalidation, risk budget, max loss, position size and quantity
  are `null`
- trade proposal and order intent are `null`
- capital is `0`
- Stage, Buy, Action, Order, Production and trading authority are all `false`

The validator rebuilds the packet from the exact Dynamic Clock, identity,
Shadow Entry Review, P5-06/P7-08 readiness and P8-13 contract inputs. Re-signing
a modified packet cannot turn diagnostic material into a money action.

This is an implemented safety and review boundary. P8-13 remains in development
until the required policies have separate authority records and can pass their
own evidence, CIO review and user-ratification gates.
