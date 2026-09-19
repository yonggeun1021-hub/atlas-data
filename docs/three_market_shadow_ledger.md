# P10-01 3-Market Shadow Ledger

`shadow/three_market_shadow_ledger.py` records each validated P8-02 Unified
Decision together with its P9-03 ENTRY / EXIT eligibility and P9-05 intraday
risk packets as an append-only, hash-chained Shadow observation. Every record
contains the exact three source packets, their SHA-256 lineage, compact US,
Korea, and Crypto Regime snapshots, Rotation / Discovery counts, eligibility
counts, and the intraday alert count.

Contract v4 revalidates the P9 packets with their production validators. The
eligibility packet must reference the exact Unified Decision packet, and the
risk packet must contain that exact eligibility packet plus an exact validated
P9-02 important-event packet and exact validated P7-03 concentration/P7-06
planned-loss packets. A self-rehashed semantic or authority mutation therefore
fails closed instead of becoming Shadow history.

The ledger is permanently zero-capital: `real_capital_deployed="0"` and
`real_order_count=0`. It does not interpret decisions, generate actions, make
performance claims, allocate capital, or create orders. Eligibility and ALERT
values are evidence only. A duplicate decision ID with the same three packets is
an idempotent retry; the same ID with different evidence is a hard conflict.
Earlier dates or a repeated slot cannot be appended.

`shadow/three_market_shadow_operational_readiness.py` is the committed-Daily
adapter under the separately versioned readiness `/2` contract. It appends only when that immutable Daily packet contains all three
validated P10 inputs at its source commit; missing P9 inputs remain blocked.
The adapter accepts an optional prior ledger and an external output target, preserving
the ledger's idempotent forward-only chain. It does not schedule itself,
backfill history, classify a non-UNKNOWN regime, select a benchmark, or create
Paper/Real/order authority.

An existing external output ledger requires byte- and semantic-exact current
prior history as `--ledger`. Both v4 ledgers are validated before source
evaluation; a stale or differently encoded prior fails closed rather than
replacing history.

The ledger CLI writes only outside the repository. Live morning/evening
scheduling and tracked operational history remain separate Exit Gate work.

```bash
python shadow/three_market_shadow_ledger.py /tmp/unified-decision.json \
  /tmp/entry-exit-trigger-eligibility.json \
  /tmp/intraday-risk-escalation.json \
  --recorded-at 2026-08-21T02:15:00Z \
  --ledger /tmp/prior-shadow-ledger.json \
  --out /tmp/shadow-ledger.json
```
