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

Every one of those fixed values is compared as an exact JSON value, reusing the
upstream readiness comparison. Python treats `False`, `0` and `0.0` as equal, so
plain equality accepted an authority flag written as `0`, `review_only` written
as `1`, or capital written as `False` or `0.0` — including a packet that kept its
original hash. The contract, the upstream fixed-value guards and the final
expected-packet comparison now reject those scalar aliases. Valid packets are
unchanged: the same inputs still produce the same bytes and the same hashes.

This is an implemented safety and review boundary. P8-13 remains in development
until the required policies have separate authority records and can pass their
own evidence, CIO review and user-ratification gates.
