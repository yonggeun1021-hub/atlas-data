# P7-04 Market / Theme Exposure Budget Contract

`portfolio/market_theme_exposure_budget.py` evaluates measured market and
theme NAV exposures against an external, effective-dated policy. Every budget
row is keyed by scope, market, scope identity, and the source market's runtime
Regime. Missing active coverage fails closed instead of selecting a fallback.

Version 1 is pinned to the current `regime_output/v1` runtime boundary:
`PRE_SCORE_UNKNOWN_ONLY`, Regime `UNKNOWN`, direction `UNKNOWN`, and null
confidence. It cannot accept a scored Regime by relabeling it; a future Regime
contract expansion requires an explicit P7-04 contract revision.

Market rows are backed by the portfolio/concentration packet. Theme rows also
require exact rotation lineage. The input packet preserves the portfolio,
P7-03, taxonomy, rotation, exposure-source, and three market Regime hashes.
The repository provides no default maximum exposure and accepts only a
`CIO`-ratified policy packet.

`WITHIN_RATIFIED_BUDGET` and `LIMIT_BREACH` are risk evaluations only. They do
not infer Rotation, score Regime, rebalance, choose target exposures, size a
position, or create an order. Those outputs remain null/empty and all
Production/trading authority remains false. CLI output inside the repository
tree is forbidden.
