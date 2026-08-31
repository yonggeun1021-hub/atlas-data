# US PAPER funnel handoff to PAPER 9-3

`config/us_paper_funnel_handoff_contract.json` is the only integration-facing
artifact owned by US 1-2. It freezes the downstream input schemas and the
canonical funnel without implementing a second scorer, selector, or ledger.

PAPER 9-3 owns score calculation, Top10/Top3 always-output behavior, state
transitions, risk Hard Gate, and virtual-ledger lifecycle. Its fixed funnel is:

- Candidate: score >= 60
- Ready: score >= 70
- PAPER_BUY_ELIGIBLE: score >= 75 plus Hard Gate PASS plus completed-bar PASS
- Top10 and Top3 are always emitted, including blocked/insufficient states.

The two exact producer inputs join on `asset_id`:

1. `us_investable_registry_result/1`: row eligibility must be
   `ELIGIBLE_FOR_PAPER_DATA_REVIEW` and liquidity must be `PASS`.
2. `us_market_data_result/1`: packet status/freshness must be `PASS`/`FRESH`,
   the date-specific session must be open, and 15m/1h/1d series must all pass.

No score or Top-N output is generated in US 1-2. The handoff itself grants no
PAPER buy, broker OAuth/POST, real account/capital, Production, or Trading
authority.
