# KRX Briefing Strategy SHADOW Contract

Status: `INTERFACE_IMPLEMENTED_POLICY_AND_UNIVERSE_UNRATIFIED`

This contract connects a briefing candidate to deterministic strategy
calculation without granting symbol selection, capital, order-draft, PAPER
write, or REAL authority. It is a mechanism and replay surface, not a ratified
investment policy and not an operational approval.

## Audited baseline

- The public KRX global universe is source coverage, not an eligible or
  investable universe. The engine therefore consumes an explicit eligibility
  record and never infers eligibility from a six-digit symbol, current
  watchlist, briefing presence, or ranking.
- The merged `krx_investable_registry/1` adds PIT identity and categorical
  screening evidence, but every non-excluded record's decision eligibility
  remains `UNKNOWN` until missing history, measurements, and policies exist.
  Its gate role is `NON_AUTHORITY_EVIDENCE_CANDIDATE`; investable-Universe,
  strategy-entry, PAPER, REAL, Production, and trading authorities remain
  false. This engine therefore still requires a separately supplied exact
  eligibility record and has no symbol-selection authority.
- The public Dynamic Clock has provisional price/invalidation/flow/relative-
  strength diagnostics, but its KRX price-series path can currently consume a
  same-day `confirmed=false` daily row. This engine does not consume that
  packet. A completed-bar adapter must independently exclude incomplete rows.
- The merged `krx_completed_market_data/1` contract now defines completed
  `15m`/`1h`/`1d` construction and leaves `4h` unratified. Its consumer
  contract deliberately leaves the SHADOW exact-hash pin unresolved and the
  P9 freshness repository default absent. Contract v1 therefore accepts only
  explicit bar-interface rows and does not treat caller claims or the merged
  producer mechanism itself as strategy authority.
- P10-09 is an authoritative `WAIT`-only PAPER Shadow observer. P10-10's
  existing `ATLAS_KRX_CONSERVATIVE_DIAGNOSTIC_V1` is explicitly
  `DIAGNOSTIC_DRAFT_NOT_AUTHORITY`: 30-second quote freshness, 25 bp maximum
  spread, observation after 09:15 KST, prior completed 15-minute high, 200 bp
  stop diagnosis and 400 bp harvest diagnosis. Those numbers are retained as
  provenance in `config/krx_shadow_strategy_policy_candidates.json`; they are
  not promoted into a repository default or a ratified strategy.
- Public Portfolio Constitution, candidate validity, Entry, Position
  Management, Size, Profit Harvest, and eligible-Universe authorities remain
  absent or unratified. Private account-fact readiness likewise remains
  unavailable. The repository therefore has no currently executable positive
  KRX strategy packet.

## Input interface

`krx_shadow_strategy_input/1` is hash-bound and requires each candidate to
carry:

1. exact canonical identity and separately supplied eligibility;
2. market/regime permission, relative-strength confirmation, and liquidity
   capacity, each with `available_at` and `valid_until`;
3. exactly the completed `15m`, `1h`, and `1d` bars;
4. a fresh quote and PIT-windowed SHADOW/PAPER position state;
5. an explicit entry/stop/first-target/final-target/expiry/invalidation plan;
6. explicit P10-10 session/quote-age/spread guards, tick size, entry/exit
   fees, stop slippage, a per-candidate allocation from one account risk
   budget, account capacity, and liquidity quantity cap.

No KRX `4h` bar is required. The continuous 09:00–15:30 KST session does not
have a naturally ratified four-hour boundary in Atlas, so adding `4h` to the
v1 input is rejected pending a separate session-boundary review.

The committed `test/fixtures/krx_shadow_strategy_input.json` is synthetic and
marked `TEST_FIXTURE_NOT_AUTHORITY`. It demonstrates the 005930 canary shape;
it is not a security-selection or risk-policy grant. Dynamic briefing mode
accepts multiple explicitly ranked candidates, but ranking does not create
eligibility.

## Decision semantics

The evaluator emits one of `ENTER`, `HOLD`, `EXIT`, or `NO_TRADE` plus a
separate `diagnostic_action`.

- `ENTER`: flat position, completed/fresh inputs, explicit market/relative-
  strength/liquidity permission, current price above the completed 15-minute
  reference, and ask no higher than the supplied maximum entry.
- `HOLD`: an open position remains inside stop, first/final target, expiry,
  invalidation, regime, and relative-strength boundaries.
- `EXIT`: stop, first target, final target, expiry, explicit invalidation,
  regime withdrawal, or relative-strength break is reached. The diagnostic
  stage preserves which condition fired. A first-target result may calculate
  only the supplied nonzero partial fraction of the existing position.
- `NO_TRADE`: incomplete/future/stale data; unresolved identity; missing or
  ineligible Universe evidence; duplicate decision; invalid gap/tick; canary
  scope violation; insufficient risk budget; or unratified policy authority.

Contract v1 has no trusted adapter that independently resolves and validates
the caller's eligibility, market, relative-strength, liquidity, plan, or risk
claims. Its ratified binding list is therefore deliberately empty and every
final `action` remains `NO_TRADE`, even if a caller labels an input
`RATIFIED_SHADOW_ONLY`. Positive SHADOW actions require a separately versioned
contract and exact upstream validators. Draft and test inputs keep their result
inside the separate `diagnostic` object; final action-stage, quantity, planned
entry and planned-loss fields remain null. This prevents a useful
counterfactual calculation from masquerading as approved policy.

The planned loss per share is deterministic:

```text
(planned entry - stop)
+ ceil(planned entry * entry fee bps / 10,000)
+ ceil(stop * exit fee bps / 10,000)
+ ceil(stop * stop-slippage bps / 10,000)
```

Quantity is the floor of the flat candidate's explicit pre-allocation from one
account risk budget divided by that loss, capped by supplied account and
liquidity capacity. Allocation IDs must be unique and all rows in the batch
must share one account budget identity, reported position count, committed
open-position risk, and total. Committed risk plus allocations for flat
candidates cannot exceed the account total. The reported open-position count
cannot be below the number of open candidate snapshots in the batch. The
engine does not source or ratify any of those values. Runtime packets
containing account values or quantities must remain in private storage and
must not be logged or committed publicly.

## Safety boundaries

- `PAPER_CANARY` accepts only `005930` and at most one open position.
- Duplicate decisions are keyed by symbol, exact policy ID/hash, and
  observation minute; changing a retry batch ID cannot bypass the key.
- Canonical identity must exactly bind `KRX:<six-digit symbol>:COMMON`.
- `15m`, `1h`, and `1d` labels are verified against exact KRX session lengths
  and boundaries; a mislabeled or partial bar is rejected. The `15m` row must
  also be the latest completed slot in the evaluated KRX business session.
- Quote age, spread and the inclusive 09:15–15:20 KST entry window are supplied
  by the explicit plan and enforced only for flat-position entry diagnosis.
  Open-position exit/hold diagnosis remains available from a generally fresh
  quote even outside that entry window or above the entry spread limit. Quote
  bid/last/ask ordering must be coherent.
- Every price in the plan and quote must align to the explicitly supplied tick
  size; no silent rounding is performed.
- A gap above the explicit maximum entry is `NO_TRADE`.
- Output always has `executable=false`, `order_draft=null`, and
  `submission_authority=null`.
- Symbol-selection, portfolio-action, order-draft, order-submission, PAPER
  write, Production trading, and REAL authority remain false.
- Validation rebuilds the complete result from embedded exact inputs, so
  changing and rehashing an output cannot change its meaning.

## Policy candidates and validation gate

`config/krx_shadow_strategy_policy_candidates.json` records three comparison
lanes without ratifying one:

1. replay the existing P10-10 15-minute breakout diagnostic unchanged;
2. compare a multi-timeframe breakout candidate after the 1-hour/daily,
   relative-strength, liquidity, gap, stop, target, and expiry definitions are
   proposed;
3. compare a pullback/re-entry candidate after the same missing definitions
   are supplied.

Thresholds that do not already exist are intentionally `null`. Before any
candidate can become `RATIFIED_SHADOW_ONLY`, replay must be chronological and
PIT-safe, use completed bars only, include fee/slippage/tick/gap sensitivity,
separate training/calibration from walk-forward out-of-sample periods, and
measure both missed upside and avoided downside. No minimum sample size or
acceptance threshold is invented here; CIO ratification remains explicit.

## Current integration blockers

- ratified eligible-Universe mapping from official KRX identity to briefing
  six-digit symbol;
- an exact-hash-bound adapter from the merged completed-market-data packet into
  this SHADOW interface, plus ratified P9 freshness and natural source lineage;
- ratified Korea regime/action and relative-strength decision semantics;
- ratified liquidity, tick-size, fees, taxes/slippage, risk-budget, sizing,
  stop, first/final target, expiry, and invalidation policies;
- private account-fact/position input and a separately versioned PAPER order-
  draft bridge after validation.

Existing KRX daily/post-close workflows can supply evidence but do not satisfy
the missing intraday or strategy gates. The existing KIS PAPER canary timer is
not modified or invoked by this work. Full repository CI remains a merge
regression only. The merged KRX PAPER gate contract currently remains
`LOCKED`: COMMON SAFETY is `UNKNOWN`, KRX SHADOW is own `PASS` but effective
`UNKNOWN`, and PAPER CANARY START is `FAIL`. Those fail-closed gate results must
be resolved separately before any future order-draft integration.
