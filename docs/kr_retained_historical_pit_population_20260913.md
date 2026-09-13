# KR retained historical PIT population — 2026-09-13

This closes the executable part of the KR `NO_EVIDENCE_BUNDLE_SUPPLIED` gap
using only material already committed to `atlas-data`. No provider request,
credential, synthetic record, backdated availability, or selected historical
episode is used.

## Actual retained period

The full committed `data/observations/korea_market_signals/*/packet.json` set
contains ten official-KRX five-axis observations:

`2026-08-28`, `2026-08-31`, `2026-09-01`, `2026-09-02`, `2026-09-03`,
`2026-09-04`, `2026-09-07`, `2026-09-08`, `2026-09-09`, `2026-09-10`.

Every retained packet passes the original
`.github/scripts/korea_market_signals.py::validate_packet` validator with 5/5
axes. The new retained-population builder then reuses
`regime.kr_historical_replay_population._observed_record` and
`regime.paper_regime_reference.build_kr`; it introduces no measurement,
threshold, classification, or hysteresis rule.

Each manifest row pins the retained path, file hash, producer payload hash,
previous/effective trading dates, generated/available timestamps, and matching
official calendar evidence where one is committed. Four dates (`2026-09-07`
through `2026-09-10`) have a separate committed official calendar snapshot.
The earlier six remain explicitly
`DIRECT_KRX_SESSION_RESPONSE_NO_SEPARATE_CALENDAR_SNAPSHOT`; their official KRX
session responses establish the observed session, and no weekday inference is
substituted.

## Strict result

The resulting bundle passes both:

1. `regime.kr_historical_replay_population.validate_population`, including
   source identity, request hashes, timestamps, five-axis re-derivation, and
   no-lookahead checks; and
2. `regime.kr_retained_historical_population.validate_population`, which
   re-discovers the complete retained set and requires byte-identical
   re-derivation. Removing one retained date or changing retained bytes fails
   closed.

The market-scoped PIT validator evaluates all ten dates. Confirmed regimes are
`NEUTRAL` and `RISK_OFF`. `RISK_ON` and `STRESS` are absent after exact common-v1
hysteresis replay, so the correct result remains:

`NOT_ACCEPTED / PIT_ACCEPTANCE_CONDITION_FAILED:6`.

This is progress from zero supplied KR dates to ten validated real dates. It is
not PIT acceptance and does not open runtime Regime authority.

## Stage 2 / Stage 3 handoff compatibility

This historical bundle is source-qualification evidence, not a current D/E
runtime packet. Direct admission is rejected by the existing consumers:

- Stage 1 current tuple input: `INPUT_CONTRACT_INVALID`;
- Stage 3 candidate-selection source: `SOURCE_BRIEFING_SCHEMA_INVALID`.

Therefore the bundle must be consumed only by the market-scoped PIT acceptance
path. It must not be inserted into Stage 2 or Stage 3 D/E wiring. Once a future
full retained sequence naturally contains all four required confirmed regimes,
the acceptance result can be handed to runtime readiness under its existing
separate authority boundary.

## Remaining KR condition

No further data download is needed to prove the currently retained ten-date
population. KR PIT acceptance still requires the unselected full real sequence
to naturally contain confirmed `RISK_ON` and `STRESS` episodes. Extending the
historical range beyond the ten committed packets requires the existing KRX
historical generator through the normal secret/network path; that provider
operation remains separate from this retained-only implementation.

Runtime classification, Stage, Buy, Action, capital, Order, Production,
trading, and REAL authority remain false.
