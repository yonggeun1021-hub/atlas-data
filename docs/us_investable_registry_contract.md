# US investable registry contract

`universe/us_investable_registry.py` is the US-only boundary after the existing
P3-02 forward source-coverage adapter. It does not modify or reinterpret
`universe/us_global_universe.py`.

The evaluator requires an external, effective `us_liquidity_policy/1` packet
and point-in-time facts for security type, active listing, halt state,
scheduled delisting, corporate-action state, and liquidity. The repository has
no default liquidity thresholds. Synthetic test values prove arithmetic only;
they are not policy.

Common stock and ETF are independent classifications:

- `ETF` requires an official ETF flag or official security master.
- `COMMON_STOCK` requires an official security master or equivalent. Nasdaq
  Symbol Directory `ETF=N` is insufficient because it can include other
  instrument types.

The following always exclude the row: OTC or unsupported venue, test/unknown
issue, non-normal financial status, inactive/unknown listing, halted/unknown
state, scheduled/unknown delisting, unresolved corporate action, unproven type,
or liquidity below the ratified policy. Any fact available after `decision_at`
is a hard PIT error.

The output phrase `ELIGIBLE_FOR_PAPER_DATA_REVIEW` grants no candidate, entry,
action, order, Production, Trading, real-account, or real-capital authority.
Raw source and price/volume rows are not authorized for public persistence.
