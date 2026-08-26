# P5-06 → P7-08 → P8-13 Shadow Entry Review Contract

Status: `PROVISIONAL_CIO_ZERO_CAPITAL_REVIEW_ONLY`

This contract removes the false choice between “buy now” and “show nothing.”
Atlas may surface a PIT-safe candidate for human review while every capital
and trading policy remains unratified and locked.

## What it does

It independently validates the current Dynamic Clock report and canonical
identity observation, then gives each candidate one review disposition:

- `MOMENTUM_PROBE_REVIEW`: resolved identity, supported live trigger and a
  linked `STRONG_MOMENTUM` or `MODERATE` price-state diagnosis.
- `REVERSAL_PROBE_REVIEW`: linked `WEAK` price state plus the existing
  independent-confirmation threshold of two distinct trigger types.
- `WAIT_FOR_PULLBACK_REVIEW`: linked `OVEREXTENDED` price state.
- `WATCH_REVIEW`: evidence is real but more confirmation is needed.
- `NOT_REVIEWABLE`: identity, PIT, price or supported-trigger evidence is
  missing.

The price state remains diagnostic and its threshold basis remains
`PROVISIONAL`. Reflection Status remains `UNKNOWN`. Neither field can create
an executable entry.

## Hard boundary

Every packet and every candidate has:

- `trade_proposal: null`
- `capital: 0`
- `quantity`, `entry_zone`, `invalidation`, and `max_loss`: `null`
- Stage, Buy, Action, Order, Production and trading authority: `false`
- candidate-validity, entry, position-management and position-size policy:
  `UNRATIFIED` / `NOT_COMPUTABLE_POLICY_UNRATIFIED`

No forward return, MFE, MAE, later audit result or other post-hoc outcome is
present in the operational packet. The validator rebuilds the packet from
its exact upstream sources; recomputing hashes after changing an output does
not make the change valid.

## Operational evidence

`p8-12-dynamic-clock.yml` writes both a latest view and an append-only,
content-addressed exact-run history record. Natural workflow runs, manual
dispatches and local reproductions are explicitly labeled and are never
silently treated as the same evidence class.

This is the first review surface for P8-13. It is not an Entry Proposal, a
position size, a recommendation to buy, or a grant of broker authority.
