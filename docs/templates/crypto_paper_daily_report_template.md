# Crypto PAPER Daily Validation — {{report_date}}

> Classification: {{sample_origin}}
> Official D0: {{official_status}} / Day {{day_number_or_na}}
> This report is diagnostic only and grants no Live, exchange-order, withdrawal, Production, Trading, or REAL authority.

## Exact lineage

| Role | Source | Available at | SHA-256 | Verified |
| --- | --- | --- | --- | --- |
| D0 gate | {{d0_gate_ref}} | {{d0_gate_available_at}} | {{d0_gate_sha256}} | {{yes_no}} |
| Ledger | {{ledger_ref}} | {{ledger_available_at}} | {{ledger_sha256}} | {{yes_no}} |
| Account state | {{account_ref}} | {{account_available_at}} | {{account_sha256}} | {{yes_no}} |
| NAV / marks / plan / errors | {{supporting_refs}} | {{latest_available_at}} | {{supporting_hashes}} | {{yes_no}} |

## Performance and no-trade benchmark

| Metric | Value |
| --- | ---: |
| Initial virtual cash | {{initial_cash}} |
| Final virtual NAV | {{final_nav}} |
| Net PAPER P&L after fee/slippage/partial fills | {{net_pnl}} |
| No-trade P&L | 0 |
| Delta versus no trade | {{delta_vs_no_trade}} |
| Fees | {{total_fee}} |
| Estimated VWAP-versus-best slippage cost | {{slippage_cost}} |
| Fill / partial-fill events | {{fill_count}} / {{partial_fill_count}} |

## Risk and excursions

- MDD: {{mdd_amount}} ({{mdd_pct}}%) from {{peak_at}} to {{trough_at}}.
- Planned loss total: {{planned_loss_total}}.
- Realized loss total: {{realized_loss_total_or_not_computable}}.
- MFE/MAE by pre-linked trade: {{trade_excursion_summary}}.

## Error metrics

| Metric | Present | Absent | Unverified | Verified rate |
| --- | ---: | ---: | ---: | ---: |
| False positive | {{fp_present}} | {{fp_absent}} | {{fp_unverified}} | {{fp_rate}} |
| Miss | {{miss_present}} | {{miss_absent}} | {{miss_unverified}} | {{miss_rate}} |
| Stale | {{stale_present}} | {{stale_absent}} | {{stale_unverified}} | {{stale_rate}} |
| Duplicate | {{duplicate_present}} | {{duplicate_absent}} | {{duplicate_unverified}} | {{duplicate_rate}} |
| Silent error | {{silent_present}} | {{silent_absent}} | {{silent_unverified}} | {{silent_rate}} |

## Operator notes

- Natural / manual / replay / synthetic distinction: {{origin_explanation}}.
- UNKNOWN / NOT_COMPUTABLE items: {{unknown_items}}.
- Restart / kill-switch observation: {{operational_observation}}.
- Follow-up evidence required: {{follow_up}}.

## Authority confirmation

- WBS P10-12 promoted: **No**
- Live review automatically opened: **No**
- Exchange / withdrawal / REAL authority: **False**
