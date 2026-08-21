# P10-01 3-Market Shadow Ledger

`shadow/three_market_shadow_ledger.py` records each validated P8-02 Unified
Decision as an append-only, hash-chained Shadow observation. Every record
contains the exact Unified Decision packet plus compact US, Korea, and Crypto
Regime snapshots and Rotation / Discovery counts.

The ledger is permanently zero-capital: `real_capital_deployed="0"` and
`real_order_count=0`. It does not interpret decisions, generate actions, make
performance claims, allocate capital, or create orders. A duplicate decision ID
with the same packet is an idempotent retry; the same ID with different content
is a hard conflict. Earlier dates or a repeated slot cannot be appended.

The CLI writes only outside the repository. Live morning/evening scheduling and
tracked operational history remain separate Exit Gate work.

```bash
python shadow/three_market_shadow_ledger.py /tmp/unified-decision.json \
  --recorded-at 2026-08-21T02:15:00Z \
  --ledger /tmp/prior-shadow-ledger.json \
  --out /tmp/shadow-ledger.json
```
