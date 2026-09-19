# Stage 1 runtime UNKNOWN input-gap delta — 2026-09-13

This is a delta-only follow-up to the retained Stage 1/Stage 6 PIT history
boundary audit and recent normal/failure/no-change audit. Those audits were
not repeated.

## Closed by this change

`regime/runtime_regime_readiness.py` now consumes the already-ratified,
market-scoped readiness overlay from
`regime/market_scoped_pit_acceptance.py` and the exact committed no-bundle
status in `data/latest_market_scoped_pit_acceptance.json`.

The runtime readiness packet therefore reports the current policy truth:

- US: signed-axis normalization and semantic freshness are ratified;
  `POLICY_READY_PIT_EVIDENCE_PENDING`.
- KR: signed-axis normalization and semantic freshness are ratified;
  `POLICY_READY_PIT_EVIDENCE_PENDING`.
- CRYPTO: signed-axis normalization remains unratified;
  `SIGNED_NORMALIZATION_POLICY_UNRATIFIED`.

The former stale US/KR `SIGNED_NORMALIZATION_POLICY_UNRATIFIED` blockers are
removed. All three markets retain `PIT_REPLAY_NOT_ACCEPTED`, and every runtime,
classification, Stage, Buy, Action, capital, Order, Production, and trading
authority flag remains false.

The consumer accepts only a byte-identical re-derivation of the currently
committed no-bundle PIT status. A future claimed acceptance status fails closed
until its real evidence bundle is explicitly connected and re-derived.

## Remaining actual conditions

| Market | Exact remaining source or decision | Accountable owner | Condition that closes it |
| --- | --- | --- | --- |
| US | Real historical BREADTH and LEADERSHIP reconstruction capability; the existing population is structurally capped at 3/5 axes | Source: `US_PAPER_PROXY_SOURCE_OWNER`; PIT bundle publisher/acceptance owner: `UNASSIGNED_IN_COMMITTED_PUBLIC_CONTRACT` | Build the missing real historical axes, then supply the full real sequence to the ratified market-scoped validator; 5/5 axes, no lookahead, byte-identical rerun, exact common-v1 replay, and all four regimes must pass without cherry-picking |
| KR | Real 5/5 historical KRX population bundle; no bundle is retained yet | Source: `KRX_OFFICIAL_FIVE_AXIS_SOURCE_OWNER`; PIT bundle publisher/acceptance owner: `UNASSIGNED_IN_COMMITTED_PUBLIC_CONTRACT` | Use the normal KRX secret/network path over the full available real date range, retain the bundle, and pass all six ratified market-scoped PIT conditions |
| CRYPTO | Signed-axis normalization policy decision; retained leadership history is also incomplete | CIO for normalization; `CR_06_CR_07_SOURCE_OWNER` for natural leadership history | CIO ratifies an exact Crypto normalization identity, then a full real market sequence passes the same six PIT conditions. Accumulating days alone does not ratify the policy |
| All markets | Runtime binding and final result authority | Decision: CIO; runtime-binding implementation owner: `UNASSIGNED_IN_COMMITTED_PUBLIC_CONTRACT` | After market PIT evidence is accepted, separately ratify runtime binding and the Regime result. PIT acceptance alone must not open runtime or trading authority |

The retained current-reference observations do not close these historical PIT
conditions: US is 15/15 on its 2026-09-11 proxy set, KR has five observed axes
for 2026-09-10, and CRYPTO has a 5/5 current reference for 2026-09-12 while its
natural leadership history is 4/7 pilot days and 4/30 primary days. These are
useful current descriptions, not accepted full-sequence PIT evidence.

## Current presentation boundary

PAPER reference output may continue to display a descriptive `NEUTRAL` when
its own source contract permits it. Runtime Regime remains `UNKNOWN`. The two
states have different authority and are not interchangeable.

This change does not modify Stage 2 packet wiring, Stage 3 candidate wiring,
the existing three-market replay harness, P6/P7 decision code, VCA, order
paths, credentials, or private account evidence.
