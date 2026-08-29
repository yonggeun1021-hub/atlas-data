# Crypto Mac B ratification review packet — 2026-08-29

Status: **REVIEW MATERIAL ONLY**. This packet does not ratify a policy,
taxonomy, identity, freshness rule, candidate, or PAPER buy. It does not
authorize action generation, orders, Production, Trading, or REAL execution.

## Current natural evidence

| Layer | Observed state | Exact evidence file SHA-256 |
|---|---|---|
| P3-12 universe | 282 markets; all 282 `OBSERVATION_POOL`; 0 tradeable; 0 PAPER eligible | `data/observations/upbit_tradeable_universe/2026-08-29/packet.json` — `19ebf7c7d56fecff7b9eda74f973708b5e650c873de2c837cf3d66a7b4a4e5ec` |
| P3 identity review | 282 full unratified proposals; 0 duplicate-target findings; broad ratified canonical registry absent, so cross-registry check not run | `data/observations/upbit_identity_review/2026-08-29/packet.json` — `185062eecf97b95c6c5594d15378addb2b6bf277e1220914d3d001b80ff70608` |
| P4-07 market evidence | policy unratified; 0 eligible input markets, therefore 0 packets | `data/observations/upbit_market_evidence/2026-08-29/packet.json` — `cd164ca3cb237ff61f18315f32754b19581b31e01a146f489ad209a39ff0aeba` |
| P9-06 public realtime validation | BTC/ETH; 465 accepted messages; ticker/trade/orderbook/15m/1h/4h all 12 combinations observed; reconnect 0; transport status `FRESH`/coverage `COMPLETE` | `evidence/crypto/upbit/realtime_validation/2026-08-29/run_001.json` — `8f19492911646ed0b77cd1c4c31cd664c1d932e601918969fcf06729784a200a` |

The P9 validation sample is deliberately isolated from the normal realtime
decision source. It proves public transport/parser coverage only and cannot
promote BTC, ETH, or any other market.

## Exact proposal inputs awaiting a human decision

### P3-12 tradeable-universe policy

File SHA-256:
`config/upbit_tradeable_universe_policy.json` —
`37cdd87a174a739ebba2af97b2c601ac2cdb15b1a223ab7212489d6dd3f1a3fb`

Current status is `PROPOSED_PAPER_BASELINE_UNRATIFIED`. The exact proposed
values already in the repository are:

- finalized listing history: 90 days
- finalized turnover lookback: 30 days
- minimum 30-day average KRW turnover: KRW 5,000,000,000
- maximum spread: 20 bps
- maximum estimated PAPER slippage: 30 bps at KRW 1,000,000 notional
- maximum capture age: 30 hours
- unknown metric behavior: fail closed

No value was changed or inferred in this review packet.

### P3-12 taxonomy and identity

Taxonomy file SHA-256:
`config/upbit_exclusion_taxonomy.json` —
`2f5b2133c0c9fc9726a9ae89b451566d6e90d291e825df1e7e172a6f2ce1abb1`

The taxonomy remains `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY` and contains six
explicit stablecoin exclusions. The full 282 proposed market-to-asset
mappings are now preserved in the identity review bundle. Zero collision
findings means only that no two proposals target the same candidate id; it
does not prove identity because a ratified broad canonical registry is absent.

### P4-07 market-evidence quality policy

File SHA-256:
`config/upbit_market_evidence_policy.json` —
`11c8842eda93317169117d1741530e5fbdb4cf6ee76300a3450e01c0bdf3a15d`

This remains `PROPOSED_UNRATIFIED`. Its proposed `normal` evidence-quality
limits (100 bps spread, 150 bps estimated slippage) are not the P3 eligibility
limits (20/30 bps). The two scopes must be accepted, revised, or rejected
explicitly; this packet does not collapse them into one rule.

### P9-06 intraday freshness policy

File SHA-256:
`config/upbit_realtime_freshness_policy_proposal.json` —
`3beed98062e9ccbb40b105743f9a2b516147fbfb7483dee0371f6c2de397f4cc`

This remains `PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY`. The proposed CRYPTO
values are provider age 20 seconds and transport delay 3 seconds. The natural
30-second public sample proves that the channels work; it does not ratify or
calibrate those freshness values.

## Human decisions still required

1. Accept, revise, or reject the exact P3 policy values above.
2. Review the 282 identity proposals and provide/approve a broad canonical
   registry or equivalent evidence before any identity can become ratified.
3. Accept, revise, or reject the taxonomy categories/records.
4. Accept, revise, or reject the distinct P4 evidence-quality policy.
5. Accept, revise, or reject a real `intraday_freshness_policy/1` packet for
   CRYPTO; the proposal file itself is intentionally not executable policy.

Until those decisions are represented by the repository's existing
ratification mechanisms, P3 remains observation-only, P4 remains empty,
P5-08/P5-09 remain non-buying, and P8-16 reports the blockers rather than
manufacturing eligibility.
