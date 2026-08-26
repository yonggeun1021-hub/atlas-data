# P2-COM-01 Cross-Asset Flow Evidence Contract

This contract limits every statement that “money moved” to its actual evidence
class: `DIRECT_FLOW`, `MARKET_IMPLIED_FLOW`, `MACRO_CONTEXT`, or `UNKNOWN`.

The first operational adapter preserves three existing read models without
normalizing or comparing them:

- stablecoin net issuance: direct Crypto flow evidence;
- KRX watchlist participant net demand: direct Korea flow evidence, still
  `OBSERVED_UNCONFIRMED` on the same day;
- VIXCLS: US macro context only, never direct flow.

No present source supports a cross-market market-implied-flow statement. That
class therefore remains explicitly `UNKNOWN`. Different observation dates are
never compared. No freshness window, lag rule, normalization, direction,
weighting, market ranking, candidate promotion, action, order, Production, or
trading authority is introduced.

`PIPELINE_VALIDATED` means only that the source Daily Orchestrator component
was present and validated by its upstream pipeline. It is not a qualitative
investment grade and does not authorize interpretation.
