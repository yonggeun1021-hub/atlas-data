# P7-11 Profit Harvesting Baseline Audit

**BASELINE AUDIT ONLY -- not an operational Harvest Engine, not a sell-policy ratification.** Every record's `authority` block is hard-`False`/`review_only`. This report measures how already-approved PR #210 Miss/Defense episodes' price paths actually unfolded after a hypothetical PIT-safe entry -- it produces no sell threshold, liquidation rule, quantity, Trade Proposal, or order.

- report_asof_evidence_date: `2026-08-23`
- repo_history_starts_at: `2026-08-13`

## Market population boundary

| Market | population_label | kpi_population_status | episodes | harvest_opp | defense | not_gradable |
|---|---|---|---|---|---|---|
| BTC | DEDICATED_COLLECTOR | OK | 1 | 1 | 0 | 0 |
| KOREA | CURRENT_WATCHLIST_DIAGNOSTIC_COHORT | NOT_COMPUTABLE_NO_HISTORICAL_PIT_WATCHLIST_EVIDENCE | 5 | 1 | 4 | 0 |
| CRYPTO | PIT_RATIFIED_ELIGIBLE_UNIVERSE | NOT_COMPUTABLE_MOSTLY_PRE_2026_08_19 | 2 | 2 | 0 | 0 |

## Priority subjects (BTC / 005930 / 000660)

| Subject | Category | Episode start | MFE | MAE | Time-to-MFE | 5d fwd return | Terminal return | Max giveback after MFE |
|---|---|---|---|---|---|---|---|---|
| 000660 | DEFENSE_EPISODE | 2026-08-13 | 5.72% | -12.33% | 2d | N/A | -0.24% | -17.08% |
| 005930 | DEFENSE_EPISODE | 2026-08-13 | 4.73% | -10.36% | 2d | N/A | -1.45% | -14.41% |
| 005930 | HARVEST_OPPORTUNITY_DIAGNOSTIC | 2026-08-19 | 6.23% | -1.75% | 1d | N/A | 5.45% | -7.51% |
| BTC | HARVEST_OPPORTUNITY_DIAGNOSTIC | 2026-08-20 | 8.92% | -0.00% | 1d | N/A | 5.59% | -8.19% |

## Coverage gap (DATA_FAILURE, excluded from every Miss/Defense KPI)

See `coverage_gap.json` -- reused verbatim from `replay.coverage_gap` (auditable_coverage_pct=45.88607594936709, a blended cross-market operational metric only -- never a performance KPI).

## Policy input packet (research-only, UNRATIFIED)

See `policy_input_packet.json`. Every scenario comparison record carries `approval_status=UNRATIFIED`, `scenario_type=ANALYTICAL_SCENARIO_ONLY`, `action_authorized=false`, `order_authorized=false`. This is INPUT for a future, separate CIO policy design decision on P7-11 -- not a policy itself.

- Early exit at 1d vs full hold: NOT_COMPUTABLE_INSUFFICIENT_SAMPLE (n=4)
- Early exit at 3d vs full hold: NOT_COMPUTABLE_INSUFFICIENT_SAMPLE (n=0)
- Early exit at 5d vs full hold: NOT_COMPUTABLE_INSUFFICIENT_SAMPLE (n=0)

## Authority

Every record in this artifact carries `authority.action_authorized=false`, `order_authorized=false`, `stage_authorized=false`, `buy_authorized=false`, `production_authorized=false`, `trading_authorized=false`. No code path in `harvest_audit/` ever sets any of these to `true`.
