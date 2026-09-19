# US/KR historical PIT evidence inventory — 2026-09-12

Read-only inventory of the repository's existing historical replay
population capability. This is **evidence only**: it selects no episode,
promotes no date, and does not change any market's PIT acceptance status
(see `config/market_scoped_pit_acceptance_contract_v1.json`, which remains
`NOT_ACCEPTED` for all three markets). It is intended to inform the *next*
step, not to take it.

## Scope

`regime/us_historical_replay_population.py` and
`regime/kr_historical_replay_population.py` (both P1-COM-05, merged prior to
this session) are the only existing modules capable of reconstructing real
per-date historical market evidence for a signed-axis replay. Both:

- accept a caller-supplied list of historical dates and reconstruct, for
  each, the same measurement/normalization the live daily collector would
  have produced *as of that date*;
- reuse `regime.paper_regime_reference`'s exact, unmodified per-axis
  arithmetic (proven by each module's own parity test against
  `PRR.build_us`/`PRR.build_kr`);
- independently re-derive and reject any lookahead (a consulted source date
  later than the requested date) before a record is ever emitted;
- never write their output inside this repository checkout (external `--out`
  or a private temp file only) — there is currently no committed artifact
  from either module anywhere in the tree.

## US — structurally capped at 3-of-5 axes, always

`regime/us_historical_replay_population.py` reconstructs **TREND, RISK_VOL,
LIQUIDITY only**. It does not, and structurally cannot, reconstruct BREADTH
or LEADERSHIP: those two axes are Alpaca-proxy-ETF measurements the module
explicitly has no authority to backfill (see its own docstring,
`regime/us_historical_replay_population.py:17-30`, and
`RATIFIED_CURRENT_REFERENCE_ONLY`/`us_breadth_authorized: false`).

Consequence: `regime.paper_regime_reference.classify` requires all 5 axes to
produce anything other than `UNKNOWN`
(`regime/paper_regime_reference.py:159-161`, `len(axes) != 5` → `UNKNOWN`).
Since this population module can never supply BREADTH/LEADERSHIP, **every US
historical date it can ever produce reconstructs to `UNKNOWN`, permanently,
regardless of how far back in time the reconstruction goes.** This is not a
"not enough history yet" gap — it is a structural capability gap. US PIT
acceptance (condition 2: required 5-of-5 axes) cannot be reached through this
module at all; a new US BREADTH/LEADERSHIP historical-reconstruction
capability would need to be built first, as a separate, later WBS slice. This
inventory does not propose that slice's design or scope.

Date range: no repository-side earliest-supported-date constant exists in
this module or in the FRED/Alpaca collector code it reuses
(`collectors/free_market_data.py`); coverage is bounded only by what the
Alpaca IEX daily-bars feed and FRED's ALFRED vintage endpoint answer for a
given anchor date at call time. This module requires live network access and
credentials (`FRED_API_KEY`, `ALPACA_MARKET_DATA_API_KEY`,
`ALPACA_MARKET_DATA_API_SECRET`, read from the environment) to produce real
(non-fixture) output; none of the three are available in this offline
authoritative-CI environment, so no real invocation was performed for this
inventory. The module's own test suite is explicitly offline/fixture-only
(`test/test_us_historical_replay_population.py:4-5`) and contains no real
example to cite a concrete real date range from.

## KR — potentially the real 5-of-5, bounded by live KRX availability

`regime/kr_historical_replay_population.py` reconstructs **all five axes**
by calling `regime.paper_regime_reference.build_kr(packet, policy)` directly
and unmodified (`regime/kr_historical_replay_population.py:324`) on a packet
shape identical to what `.github/scripts/korea_market_signals.py` produces
live. Unlike US, there is no structural axis gap here: a KR historical date
that resolves can, in principle, reach the required 5-of-5 and feed condition
6's regime-episode check.

Date range: also no repository-side earliest-supported-date constant exists
in this module or in `.github/scripts/korea_market_signals.py`. The only
bound found is a per-anchor lookback *window* for locating a completed
session *pair* around a given date
(`config/korea_market_signals_contract.json`:
`maximum_session_search_calendar_days: 10`), not a floor on how far back the
anchor itself may be. How far back real reconstruction can actually reach
depends entirely on what KRX's public Open API endpoints still answer for
historical dates.

This module requires a live KRX auth token (`auth_key`, caller-supplied) and
live network access (`urllib.request.urlopen` by default,
`regime/kr_historical_replay_population.py:336-338`) to produce real
(non-fixture) output. Neither is available in this offline authoritative-CI
environment, so no real invocation was performed for this inventory either.
Its test suite is likewise offline/fixture-only
(`test/test_kr_historical_replay_population.py:4-5`).

## What this means for `market_scoped_pit_acceptance`

`regime/market_scoped_pit_acceptance.py` (this session) validates whatever
real bundle a caller supplies; it does not fetch one itself, matching the
existing architecture (population and validation are always separate
modules in this repository — see `regime/replay_harness.py`,
`regime/decision_authority.py`'s replay validators, and
`regime/deterministic_replay_evidence.py`, none of which perform their own
provider I/O). As of this commit, no caller has ever supplied a real bundle
for either market, so `regime/market_scoped_pit_acceptance.py::build_status()`
honestly reports `NOT_ACCEPTED`/`NO_EVIDENCE_BUNDLE_SUPPLIED` for both US and
KR (`data/latest_market_scoped_pit_acceptance.json`).

## Explicitly not done here

- No episode (bull/bear/sideways/stress) date is selected or proposed.
- No credentials were requested, stored, or exercised.
- No population module was invoked against a live provider.
- No US BREADTH/LEADERSHIP historical-backfill design is proposed.
- No change was made to `market_scoped_pit_acceptance_contract_v1.json`'s
  `NOT_ACCEPTED` initial status, or to any market's acceptance state.

## Next executable step (not taken by this document)

A future WBS slice would need to: (1) obtain real FRED/Alpaca credentials
and a KRX auth token through the repository's normal secrets path, (2) run
`kr_historical_replay_population.build_population` for KR over as wide a
real date range as KRX's endpoints permit, and evaluate the resulting bundle
through `market_scoped_pit_acceptance.evaluate_market_pit_acceptance("KR",
bundle)` to see whether all four regimes actually occur in the real
sequence — with no episode pre-selection and no cherry-picking of the
result, exactly as this session's ratified condition 6 requires. For US, the
prerequisite is a separate BREADTH/LEADERSHIP historical-reconstruction
capability that does not exist yet; until it does, US PIT acceptance cannot
be reached through this population module regardless of date range.
