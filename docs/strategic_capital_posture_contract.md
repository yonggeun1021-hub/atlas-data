# P7-12 Strategic Capital Posture readiness contract

`portfolio/strategic_capital_posture.py` is a zero-capital readiness boundary,
not an allocation engine.  It inventories the P1 Regime, P2 Flow/Rotation,
P6 Defensive Action, and P7 portfolio-risk packets required before a
cross-market budget can be evaluated.  Every supported P6/P7 packet is run
through its original validator again at the consumption boundary; schema,
status, hash, time, and closed execution authority are not trusted merely
because a JSON object exists.

Point-in-time validation uses each source contract's effective availability
field, not only its date label.  P6 is bounded by its top-level `generated_at`;
the self-validating P7 concentration, market/theme, crypto, and planned-loss
packets are bounded by their embedded input `generated_at_utc`; currency
exposure is bounded by `available_at`.  A semantically valid upstream packet
whose effective availability is later than the P7-12 consumer `generated_at`
fails closed as `SOURCE_FROM_FUTURE`.

The current repository has no ratified P1 Regime Decision, P2 cross-market
Flow/Rotation production contract, or Strategic Capital Posture policy.
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
