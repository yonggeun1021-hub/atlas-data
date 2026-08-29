# Regime Policy Calibration Readiness v2

`regime/policy_calibration_readiness.py` implements the user-ratified
P1-COM-05 process boundary: evidence and replay methodology first (B), followed
by market-by-market Shadow calibration only when each market reaches validated
five-of-five coverage (C).

## What the packet proves

The command independently replays every retained raw source currently bound by
`regime_live_axis_adapter/v7`:

- FRED VIX append-only evidence for `US/RISK_VOL`
- Alpaca IEX SPY/QQQ/IWM daily evidence for `US/TREND` once the first v5
  capture is retained
- FRED WRESBAL/TOTBKCR no-raw derived evidence for `US/LIQUIDITY` once the
  first v5 capture is retained; its URI is `atlas-derived://`, not falsely
  labelled as retained raw response evidence
- Kraken BTC append-only snapshots for `CRYPTO/TREND` and
  `CRYPTO/RISK_VOL`
- DefiLlama stablecoin append-only snapshots for `CRYPTO/LIQUIDITY`
  (Upbit microstructure evidence is a second qualifying `CRYPTO/LIQUIDITY`
  input in the live adapter, but this history scanner still replays the
  stablecoin side only -- see P1-CR-08's PR notes)
- Kraken-derived Crypto Breadth (CR-06) snapshots for `CRYPTO/BREADTH`
- Crypto Breadth-derived dual-window relative-strength history for
  `CRYPTO/LEADERSHIP` (CR-07); this is real-evidence-backed but currently
  short of the 30-day primary window, so it retains no history yet
- Official KRX combined market observations for all five Korea axes. One
  validated packet contributes one retained observation to each axis without
  creating a Regime label.

Each source is checked by its existing production validator. The packet records
the exact raw URI, hash, observation date, Atlas availability time, revision
count, distinct observation count, and retained calendar span. A configured
binding without valid retained evidence is not counted as ready.

The five required axes and five-of-five minimum are read from the already
ratified `regime_minimum_coverage/v1` contract. Missing axes retain the live
adapter's specific blocker where one exists; no watchlist, membership roster,
or lineage-only receipt is promoted to a market-wide axis.

## Current expected result

Before the first `korea_market_signals/1` packet is retained, the repository
replays:

- US: `1/5` (`RISK_VOL`) in the pre-v5 retained archive; `3/5`
  (`TREND`, `RISK_VOL`, `LIQUIDITY`) after the first validated v5 capture
- Korea: `0/5`; it becomes `5/5` evidence coverage when the first combined
  official-KRX packet is retained, while classification remains unratified
- Crypto: `4/5` (`TREND`, `BREADTH`, `RISK_VOL`, `LIQUIDITY`) -- `BREADTH` newly
  retains history from this PR (one validated 2026-08-28 observation to
  date; earlier committed days are real, genuine
  `TAXONOMY_COVERAGE_UNKNOWN` blocks, not retained). `LEADERSHIP` retains no
  history yet -- `NO_VALIDATED_EVIDENCE` -- because its dual-window
  methodology needs a longer unbroken run of Crypto Breadth snapshots than
  currently exists in committed evidence.

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
