# Common three-market PAPER candidate funnel

This public, account-neutral contract normalizes market-owned Korea, US, and
Crypto candidate packets into one deterministic funnel:

`Universe → Top10 → Top3 → Candidate(60) → Ready(70) → PAPER_BUY_ELIGIBLE(75 + every Hard Gate + completed bar)`

The common scorer only sums exact upstream point allocations whose maximums
total 100. It does not invent market weights, thresholds, signals, or candidate
facts. Stable ordering is score descending, contract market order, candidate ID,
then source timestamp. Top10 and Top3 arrays are always present; when the
universe is smaller, the summary preserves the explicit underfill reason.

Every row retains score breakdown, source timestamp, TTL, derived expiry,
effective Hard Gates, risk fields, lane, source references, and non-promotion
reasons. Missing or null Hard Gates, expiry, duplicate identity, completed-bar
absence, a pre-existing market position, planned loss above 0.25% virtual NAV,
or projected market exposure above 5% blocks PAPER eligibility.

Primary-long and `DEFENSIVE_ACTION` candidates are explicit, orthogonal lanes.
Defensive actions are `CASH`, `REDUCE`, `HEDGE`, or `INVERSE`, and their
performance cohorts are split again into `SYSTEM_HEDGE_CANARY` and
`INVESTMENT_HEDGE_PAPER`. Hedge exposure is retained in a separate bucket,
never added to or netted against long market exposure; hedge positions likewise
do not consume the one-long-position bucket. Each bucket has its own one-position
limit. The common reducer
enforces the 50% hedge-to-long-beta ceiling and 20% single hedge instrument
ceiling while P6-07 retains ownership of detailed hedge sizing and effectiveness.

`SYSTEM_CANARY` and `INVESTMENT_PAPER` are distinct performance cohorts. Human
approval and user receipt are not input gates for the internal virtual ledger,
so `PAPER_INTERNAL_AUTO=true`, `humanApprovalRequired=false`, and
`userReceiptRequired=false`. Eligibility grants no external execution: broker
mock POST count and external-call count are zero, while REAL, live, real-capital,
Production, Trading, broker real POST, and broker PAPER POST remain false.

For US adapters, private compatibility evidence #120 is recorded only as a
compatibility pin, never as transport authority. `ALPACA_PAPER` is priority 1
and `KIS_US_PAPER` priority 2, but selection must be explicit and automatic
fallback is forbidden. Missing credentials or any admission evidence keeps
network, GET, and POST counts at zero; internal virtual eligibility never opens
an external broker transport.

Market-specific tasks should consume this contract only after its exact merge
SHA exists and should supply their own exact-merge `sourceRefs`. Fixtures are
synthetic contract tests, not natural candidates or performance evidence.
