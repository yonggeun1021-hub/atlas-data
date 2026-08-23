# P7-11 Profit Harvesting Baseline Audit

**BASELINE AUDIT ONLY -- not an operational Harvest Engine, not a sell-policy ratification.** Every record's `authority` block is hard-`False`/`review_only`. The official population is PIT-safe (real contemporaneous Trigger + gradable entry, outcome-independent) -- see `docs/profit_harvest_baseline_audit.md` for the full methodology. Produces no sell threshold, liquidation rule, quantity, Trade Proposal, or order.

- report_asof_evidence_date: `2026-08-23`
- repo_history_starts_at: `2026-08-13`

## Market population boundary

| Market | population_label | kpi_population_status | episodes | harvest_opp | hold_benefit | defense | flat | not_gradable |
|---|---|---|---|---|---|---|---|---|
| BTC | DEDICATED_COLLECTOR | OK | 2 | 0 | 1 | 0 | 1 | 0 |
| KOREA | CURRENT_WATCHLIST_DIAGNOSTIC_COHORT | NOT_COMPUTABLE_NO_HISTORICAL_PIT_WATCHLIST_EVIDENCE | 5 | 0 | 2 | 0 | 3 | 0 |
| CRYPTO | PIT_RATIFIED_ELIGIBLE_UNIVERSE | NOT_COMPUTABLE_MOSTLY_PRE_2026_08_19 | 4 | 1 | 0 | 0 | 3 | 0 |

## Reconciliation (real-trigger+gradable rows -> PIT episodes)

21 rows checked, 21 reconciled, 0 unreconciled (see `reconciliation.json` for the full row-by-row mapping; unreconciled must always be 0).

## Priority subjects (BTC / 005930 / 000660)

| Subject | Category | Episode start | MFE | MAE | Time-to-MFE | 5d fwd return | Terminal return | Confirmed giveback after MFE |
|---|---|---|---|---|---|---|---|---|
| 000660 | FLAT_NO_MATERIAL_OUTCOME | 2026-08-13 | 5.72% | -12.33% | 2d | N/A | -0.24% | -17.08% |
| 000660 | HOLD_BENEFIT | 2026-08-18 | 11.33% | -3.82% | 2d | N/A | 9.45% | NOT_COMPUTABLE_NO_TRADING_DAY_AFTER_MFE |
| 005930 | FLAT_NO_MATERIAL_OUTCOME | 2026-08-13 | 4.73% | -10.36% | 2d | N/A | -1.45% | -14.41% |
| 005930 | HOLD_BENEFIT | 2026-08-18 | 8.55% | -1.99% | 2d | N/A | 7.75% | NOT_COMPUTABLE_NO_TRADING_DAY_AFTER_MFE |
| BTC | HOLD_BENEFIT | 2026-08-20 | 8.92% | -0.00% | 1d | N/A | 5.59% | -4.40% |
| BTC | FLAT_NO_MATERIAL_OUTCOME | 2026-08-21 | 0.60% | -2.95% | 1d | N/A | -1.59% | NOT_COMPUTABLE_NO_TRADING_DAY_AFTER_MFE |

## PR #210 auxiliary cohort (comparison only, NOT the official population)

8 Miss/Defense episodes from PR #210's own, already-outcome-selected KPI framing -- kept here purely for comparison against this audit's PIT-safe official population. See `pr210_auxiliary_cohort.json`.

## Coverage gap (DATA_FAILURE)

See `coverage_gap.json` -- reused verbatim from `replay.coverage_gap` (auditable_coverage_pct=45.88607594936709, a blended cross-market operational metric only -- never a performance KPI).

## Policy input packet (research-only, UNRATIFIED)

See `policy_input_packet.json`. Every scenario comparison record carries `approval_status=UNRATIFIED`, `scenario_type=ANALYTICAL_SCENARIO_ONLY`, `action_authorized=false`, `order_authorized=false`, `grid_status=ANALYTICAL_GRID_UNRATIFIED`. This is INPUT for a future, separate CIO policy design decision on P7-11 -- not a policy itself. No aggregate verdict is ever produced -- only real sample counts and raw per-episode facts.

- Early exit at 1d vs full hold: n=4, status=NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED
- Early exit at 3d vs full hold: n=0, status=NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED
- Early exit at 5d vs full hold: n=0, status=NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED

## Authority

Every record in this artifact carries `authority.action_authorized=false`, `order_authorized=false`, `stage_authorized=false`, `buy_authorized=false`, `production_authorized=false`, `trading_authorized=false`. No code path in `harvest_audit/` ever sets any of these to `true`.
