# P7-12 Strategic Capital Posture readiness contract

`portfolio/strategic_capital_posture.py` is a zero-capital readiness boundary,
not an allocation engine.  It inventories the P1 Regime, P2 Flow/Rotation,
P6 Defensive Action, and P7 portfolio-risk packets required before a
cross-market budget can be evaluated.  Every supported P2/P6/P7 packet is run
through its original validator again at the consumption boundary; schema,
status, hash, time, and closed execution authority are not trusted merely
because a JSON object exists.

`P2_CROSS_MARKET_FLOW` is bound to the P2-COM-02 cross-market flow reference
(`capital_flow_posture_reference/v1`, statuses `REFERENCE_AVAILABLE` and
`PARTIAL_REFERENCE_AVAILABLE`) through the same rename-only adapter precedent
P6-06 already uses for its `P2_FLOW_ENGINE` slot: that producer names its
identity field `payload_sha256` and its checker `validate_reference`, and the
adapter renames only the identity field so the generic source path applies
unchanged.  The producer's own validator re-derives the packet from committed
evidence and fails closed on any tamper.  Binding this source supplies
evidence and lineage only; it asserts no money flow, creates no leader or
laggard claim, and unlocks no budget, posture, action, or authority.

Point-in-time validation uses each source contract's effective availability
field, not only its date label.  P2 cross-market flow and P6 are bounded by
their top-level `generated_at`; the self-validating P7 concentration,
market/theme, crypto, and planned-loss packets are bounded by their embedded
input `generated_at_utc`; currency exposure is bounded by `available_at`.  A
semantically valid upstream packet whose effective availability is later than
the P7-12 consumer `generated_at` fails closed as `SOURCE_FROM_FUTURE`.

The current repository has no ratified P1 Regime Decision, P2 Rotation State
production contract, or Strategic Capital Posture policy.  Those two slots stay
unavailable-only, and their `*_UNAVAILABLE` unresolved boundaries are derived
from the contract's `unavailable_only_source_slots` rather than a fixed list,
so the boundary list can never disagree with the slots themselves.
Consequently the only valid runtime result is
`STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED`.  `market_budget` exposes the
three market keys with `null` values, while `cash_reserve`, `hedge_budget`,
`max_gross_risk`, `max_net_risk`, and `theme_headroom` are also `null`.
Missing never means zero, and BLOCKED never means `NO_ACTION`.

Allocation-sum, overlap-exposure, and currency-boundary checks are present as
explicit `NOT_EVALUATED` rows.  They cannot report PASS until the required
source packets and a separately ratified policy exist.  In particular,
amounts in different quote currencies cannot be added without a ratified FX
conversion boundary, and overlapping market/theme exposures cannot be counted
as independent headroom.

This capability grants readiness-inventory authority only.  It grants no
Regime, Flow, Rotation, policy, budget, allocation, cash, hedge, gross/net
risk, theme headroom, FX conversion, action proposal, position sizing, order,
Production, or trading authority.  The CLI is offline and may write only to a
path outside the repository so a diagnostic run cannot create tracked state.

Completing this capability does not complete the P7-12 WBS Exit Gate.  Actual
numeric budgets and posture remain a separate CIO/user-ratified policy step.
