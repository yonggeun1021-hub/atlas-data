# P5-06 / P7-08 Entry Policy Readiness Boundary

This boundary answers one operational question: **why can Atlas review a
candidate but still not create an executable P8-13 Entry Proposal?**

It consumes the exact validated Dynamic Clock report, candidate identity
observation, and zero-capital Shadow Entry Review packet. It preserves every
candidate as a diagnostic observation, then independently fixes these output
facts:

- candidate validity policy: `NOT_COMPUTABLE_AUTHORITY_UNRATIFIED`
- entry eligibility: `LOCKED_AUTHORITY_UNRATIFIED`
- position management: `LOCKED_AUTHORITY_UNRATIFIED`
- position size: `NOT_COMPUTABLE_AUTHORITY_UNRATIFIED`
- P8-13 Entry Proposal: `LOCKED_NOT_STARTED`
- executable candidates, entry proposals, and order intents: all zero

The contract contains no risk budget, stop distance, maximum loss, quantity,
or other numeric policy. Those fields remain `null`. `RADAR` and
`PROBE_REVIEW` are diagnostic participation states only; neither is an
executable portfolio state.

The validator rebuilds the complete packet from the upstream evidence and
contracts. A modified output remains invalid even if its hashes are
recalculated. The Dynamic Clock workflow writes a latest packet plus a
content-addressed append-only history record.

Contract, validated-upstream, and output identity checks use recursive
exact-type comparison. Python scalar aliases therefore cannot cross this
authority boundary: `true`/`false` are not accepted as `1`/`0`, integers are
not accepted as integral floats, and an aliased zero-capital value or
authority flag remains invalid even when the surrounding structure compares
equal under ordinary Python equality.

This mechanism does not ratify any policy and does not complete P5-06,
P7-08, or P8-13. Stage, Buy, Action, Order, Production, and trading authority
remain false.
