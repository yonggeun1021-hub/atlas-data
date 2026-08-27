# P8-14 Flow-First Briefing Contract

The Flow-First adapter fixes the investor-facing root order to:

`Regime → Cross-Market Flow → Theme Rotation → Capital Action → Assets → Entry / Exit / Size`.

It derives presentation rows only from the already-built Daily Orchestrator
packet. `READY` means that the section's read model is present; it does not
mean an investment action is approved. Every section exposes its `as_of_date`,
evidence grade, UNKNOWN reason, and invalidation status. Because Atlas does not
yet have one ratified section-level evidence-grade or invalidation contract,
those fields remain explicitly `UNKNOWN` rather than being inferred.

P2-COM-01 Cross-Asset Flow is now connected to the completed Daily Orchestrator
packet. The section exposes exact DIRECT_FLOW / MARKET_IMPLIED_FLOW /
MACRO_CONTEXT counts, observation dates, and source lineage. It remains
`UNKNOWN` because lag, normalization, cross-market comparison, direction, and
invalidation policies are unratified. A connected evidence contract is not a
flow-direction authority.
Stablecoin, KRX investor-flow, prices, or macro context are not silently
relabeled as direct cross-market capital flow.

The `Capital Action` section now exposes the exact P6 Defensive Action
readiness packet, P7 Strategic Capital Posture readiness packet, and their P8-06
Action/Risk/Portfolio summary together. The two upstream packets remain
`PENDING/BLOCKED`; null actions and budgets are not converted into a positive
`NO_ACTION`, and the section retains false action/order eligibility.

This contract grants presentation-order authority only. Regime scoring,
cross-market flow inference, theme ranking, candidate promotion, capital
action, sizing, orders, Production, and trading authority remain false.
