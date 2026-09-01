# KRX PAPER market-judgement input contract v1

`krx_market_judgement/1` connects retained official-KRX market evidence to an
exact, deterministic upstream source for PAPER consumers. It is an offline
read model. It performs no fetch, broker operation, candidate selection,
ledger mutation, order, or timer action.

## Canonical read-only inputs

The adapter requires the caller to pin the exact SHA-256 of both
`config/korea_leadership_policy.json` and
`data/latest_korea_market_signals.json`. The latest pointer must be byte
identical to its append-only
`data/observations/korea_market_signals/YYYY-MM-DD/packet.json` source. The
adapter also pins the repository's market-signal, minimum-coverage and Regime
decision-authority contracts through hashes in its own contract.

The input envelope preserves, without estimation:

- completed daily-bar identity, source time and exact source hashes;
- KOSPI and KOSDAQ advancing/declining/unchanged breadth;
- KOSPI and KOSDAQ trading value and turnover;
- every policy-covered sector's return and relative return versus its own
  KOSPI or KOSDAQ benchmark;
- all five observed Regime axes and their `5/5` coverage statement; and
- leadership, minimum-coverage and scoring-policy status independently.

The descriptive leader/laggard ordering remains non-investable. The adapter
does not turn relative strength into a market ranking, candidate or action.

## Literal-PASS and UNKNOWN invariant

The gate order is fixed. A defined KRX Regime would require every gate to be
the literal string `PASS`: safety, exact hash, completed bar, leadership
policy, 5/5 axes, KOSPI/KOSDAQ breadth, turnover, sector relative strength,
Regime scoring authority, TTL policy, freshness and a scored result.

The current repository has a ratified leadership policy and a valid 5/5
observation, but its Regime policy registry is absent; normalization,
freshness, direction, confidence, override, invalidation and hysteresis are
unratified; weights and thresholds are absent. No ratified TTL or scoring
result exists. Therefore v1 deliberately emits:

- `market_judgement_status: UNKNOWN`
- `regime: UNKNOWN`
- `recommendation: HOLD`
- `confidence: null`
- `action: null`

Five-of-five coverage is evidence availability, not scoring authority. A new
ratified scoring/TTL contract and adapter revision are required before a
defined Regime may be represented.

## Determinism and consumer handoff

The envelope and receipt use sorted canonical JSON SHA-256 identities. Output
files are immutable: identical reruns return `NO_CHANGE`; a same-path semantic
conflict fails closed. Outputs must be outside the repository. PAPER 12-4 and
12-1 consumers should pin the exact receipt file hash and the embedded
`receipt_sha256`, then retain the blocker list unchanged. Test fixtures are
`TEST_ONLY_NON_PROMOTABLE` and cannot enter PAPER evidence or performance.
