# Gate 2 Regime Source/Owner Registry v2

`config/regime_source_owner_registry_v2.json` records the architecture-only
decision `CIO-GATE2-3MARKET-REGIME-SOURCE-FIRST-B-2026-09-01`. It is a
repository alignment artifact, not a runtime policy input.

## What v2 aligns

- The already-ratified common PAPER baseline is represented separately from
  market-specific normalization, freshness, and replay acceptance.
- KRX reuses the official five-axis producer and the existing PAPER 12-5
  natural read-only judgement owner.
- US uses date-specific NYSE snapshots with Nasdaq cross-checks, the exact
  15-ETF PAPER reference universe, and the exact 12-group SPY-relative
  leadership layer. The finished-session and natural judgement owners are
  designated, but their natural snapshot/runner implementations are still
  pending.
- Crypto reuses the ratified Kraken breadth policy and the CR-06-derived
  BTC/ETH/ALT leadership layer. Sector/chain coverage remains an unknown group
  layer. The Gate 2 natural judgement owner is designated, but its public owner
  package is still pending.
- The aggregate remains blocked while its exact pins mix natural, baseline,
  and test-only evidence classes.

## Fail-closed boundary

The legacy `regime_decision_authority/v1` runtime contract is unchanged and
continues to emit `UNKNOWN`. This registry does not authorize runtime wiring,
signed normalization, TTL/freshness values, four-regime PIT acceptance,
candidate creation, ledger mutation, or any real/live/order/Production/Trading
effect. Fixture and no-input baseline receipts remain non-promotable.

The current acceptance states are exact:

- KRX: `BLOCKED_SIGNED_NORMALIZATION_TTL_PIT_REPLAY`
- US: `BLOCKED_FINISHED_SESSION_TTL_PIT_REPLAY`
- Crypto: `BLOCKED_OVERALL_FRESHNESS_PIT_REPLAY`
- Aggregate: `BLOCKED_MIXED_EVIDENCE_CLASSES`
- Runtime/output: `UNKNOWN/HOLD/WAIT`

## Next acceptance work

Each designated owner must produce content-addressed natural evidence with
official session identity, completed-bar checks, exact source/policy/contract
hashes, freshness, deterministic rerun and tamper rejection. Market-specific
signed normalization and freshness must be separately ratified, and the
bull/bear/sideways/stress PIT replay must be accepted before any market result
or aggregate pin can advance.
