# Crypto PAPER 30-Day CIO Review — {{d0_date}} to {{day_30_date}}

> Required machine status: `READY_FOR_CIO_REVIEW_NOT_LIVE_AUTHORIZED`
> A 30-day PAPER result is review evidence, not Live approval.

## Gate and chain attestation

- D0 gate reference / SHA: {{gate_ref}} / {{gate_sha256}}
- Natural automated Days 1–30 present: {{yes_no}}
- Consecutive 24/7 calendar dates: {{yes_no}}
- Exact predecessor chain verified: {{yes_no}}
- Manual/replay/synthetic reports excluded from count: {{yes_no}}
- Public/private pins and lineage: {{pin_summary}}

## Performance versus counterfactuals

| Metric | PAPER | No trade | Delta |
| --- | ---: | ---: | ---: |
| Period net P&L after fees/slippage/partial fills | {{paper_net_pnl}} | 0 | {{delta_vs_no_trade}} |
| Max drawdown | {{mdd}} | 0 | {{mdd_delta}} |
| Planned loss | {{planned_loss}} | n/a | n/a |
| Realized loss | {{realized_loss}} | 0 | {{realized_loss_delta}} |

Describe alternative-exit and stale-block counterfactuals without using them to rewrite past candidate or exit decisions: {{counterfactual_summary}}.

## Regime and sample coverage

| Required sample | Evidence count | Adequate? | Notes |
| --- | ---: | --- | --- |
| Rising | {{rising_count}} | {{yes_no}} | {{notes}} |
| Falling | {{falling_count}} | {{yes_no}} | {{notes}} |
| Fast move | {{fast_move_count}} | {{yes_no}} | {{notes}} |
| Liquidity deterioration | {{liquidity_stress_count}} | {{yes_no}} | {{notes}} |

Insufficient coverage remains `NOT_COMPUTABLE` or requires continued PAPER observation; it is not filled with replay or synthetic evidence.

## Trade quality and error review

- MFE/MAE distribution and outliers: {{mfe_mae_summary}}
- Planned versus realized loss breaches: {{loss_breach_summary}}
- False positives: {{false_positive_summary}}
- Misses: {{miss_summary}}
- Stale incidents: {{stale_summary}}
- Duplicate attempts / accepted duplicates: {{duplicate_summary}}
- Silent errors and recovery time: {{silent_error_summary}}
- Kill switch and restart recovery: {{recovery_summary}}

## CIO decision packet

Choose one; none is automatic:

- Continue PAPER observation — insufficient regimes, sample, or operational evidence.
- Return to mechanism diagnosis — structural loss/error/lineage failure; no post-hoc threshold tuning.
- Open a separate Live-readiness WBS Gate — requires explicit user approval, least-privilege API, order limits, dual kill switch, runbook, rollback proof, and permanently disabled withdrawals.

## Mandatory unchanged authorities

- P10-12 completion authority: **False until canonical CIO decision**
- PAPER result → Live auto-transition: **Forbidden**
- Exchange order / withdrawal / Production / Trading / REAL authority: **False**
