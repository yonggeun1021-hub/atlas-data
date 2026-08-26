# P8-14 Flow-First Briefing Contract

The Flow-First adapter fixes the investor-facing root order to:

`Regime → Cross-Market Flow → Theme Rotation → Capital Action → Assets → Entry / Exit / Size`.

It derives presentation rows only from the already-built Daily Orchestrator
packet. `READY` means that the section's read model is present; it does not
mean an investment action is approved. Every section exposes its `as_of_date`,
evidence grade, UNKNOWN reason, and invalidation status. Because Atlas does not
yet have one ratified section-level evidence-grade or invalidation contract,
those fields remain explicitly `UNKNOWN` rather than being inferred.

P2-COM-01 Cross-Asset Flow is not yet available. The Cross-Market Flow section
therefore remains visible as `UNKNOWN / P2_COM_01_CROSS_ASSET_FLOW_NOT_AVAILABLE`.
Stablecoin, KRX investor-flow, prices, or macro context are not silently
relabeled as direct cross-market capital flow.

This contract grants presentation-order authority only. Regime scoring,
cross-market flow inference, theme ranking, candidate promotion, capital
action, sizing, orders, Production, and trading authority remain false.
