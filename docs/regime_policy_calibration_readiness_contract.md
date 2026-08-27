# Regime Policy Calibration Readiness v1

`regime/policy_calibration_readiness.py` implements the user-ratified
P1-COM-05 process boundary: evidence and replay methodology first (B), followed
by market-by-market Shadow calibration only when each market reaches validated
five-of-five coverage (C).

## What the packet proves

The command independently replays every retained raw source currently bound by
`regime_live_axis_adapter/v4`:

- FRED VIX append-only evidence for `US/RISK_VOL`
- Kraken BTC append-only snapshots for `CRYPTO/TREND` and
  `CRYPTO/RISK_VOL`
- DefiLlama stablecoin append-only snapshots for `CRYPTO/LIQUIDITY`

Each source is checked by its existing production validator. The packet records
the exact raw URI, hash, observation date, Atlas availability time, revision
count, distinct observation count, and retained calendar span. A configured
binding without valid retained evidence is not counted as ready.

The five required axes and five-of-five minimum are read from the already
ratified `regime_minimum_coverage/v1` contract. Missing axes retain the live
adapter's specific blocker where one exists; no watchlist, membership roster,
or lineage-only receipt is promoted to a market-wide axis.

## Current expected result

At the v1 baseline the repository replays:

- US: `1/5` (`RISK_VOL`)
- Korea: `0/5`
- Crypto: `3/5` (`TREND`, `RISK_VOL`, `LIQUIDITY`)

All three markets therefore remain `NOT_READY_AXIS_COVERAGE`. The readiness
order `CRYPTO, US, KR` is an engineering gap order only; it is explicitly not a
market ranking or capital preference.

## Fail-closed boundary

This contract ratifies only the B+C development process. It does not invent a
minimum history length, normalization, classification, direction, confidence,
stress override, invalidation, hysteresis, or replay winner rule. The current
policy candidate stays `CANDIDATE_BLOCKED`; generated, selected, recommended,
and ratified policy candidate counts remain zero.

The packet opens no Runtime Regime, strategy, Stage, Buy, Action, Proposal,
Order, capital, Production, or trading authority. Persisted packets are
accepted only when an independent rebuild from the canonical contracts and raw
evidence produces byte-identical semantics; changing and re-signing a result is
rejected.

The command has no provider or workflow integration. Its output path must be
outside the repository, preventing a diagnostic run from mutating tracked
evidence or operational state.
