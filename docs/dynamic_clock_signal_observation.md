# P8-03 Dynamic Clock Signal Observation

This slice removes the Daily Orchestrator's hard-coded empty Action Boundary.
Every real Dynamic Clock review candidate is independently revalidated and
then represented as a `PRESENT` signal observation in the existing
`READY != ENTRY` / `Signal != Order` contract.

## Hard boundaries

- `ready_status` is always `NOT_EVALUATED`; READY lineage is always null.
- Dynamic Clock tier, confirmation count, price state, and post-hoc outcomes
  cannot open READY, Entry, Size, Action, or Order.
- Entry trigger count and order intent count remain zero.
- Candidate record hashes and the complete Dynamic Clock report hash are
  retained in signal lineage.
- BTC to CRYPTO is only a target-contract vocabulary projection. It is not a
  canonical security-identity or account-scope assertion.
- All Stage/Buy/Action/Order/Production/trading authority remains false.

If Dynamic Clock is unavailable, the Action Boundary stays empty and
fail-closed. No candidate or signal is fabricated.
