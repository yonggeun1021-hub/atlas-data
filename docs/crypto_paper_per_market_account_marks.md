# Crypto PAPER per-market marks and wall-clock stale alerts (D1, D2)

Status: design note for two review findings on the per-market realtime freshness
work (user ratification `CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914`,
record sha256 `043932a4…`). Both must be in place before the first natural
Crypto PAPER BUY fill. Scope is `INTERNAL_VIRTUAL_PAPER` only; every exchange,
Production, Trading and REAL authority stays false.

## D1: a stale held market must not freeze FRESH markets

### Problem

The P10-11 account view (`crypto_paper_account_state/1`) has one account-level
`mark_freshness_status` that must be `FRESH`, and it requires a mark for every
open position. When one held market is STALE, the view cannot be built.
Without a view, the private runtime cannot evaluate any exit, so a FRESH
market's stop-loss would wait on an unrelated stale market. The ratification
says a STALE market caps only itself.

### Design

`shadow/crypto_paper_simulator.py` adds a second, per-market account view,
`crypto_paper_account_state/2`, built by `build_account_state_per_market()`.
The `/1` view, the simulator contract file and every existing ledger rule are
unchanged.

- The caller names every open-position market as `FRESH` (a mark price is
  required) or `UNKNOWN` (no price may be supplied). Any mismatch fails closed.
- Each position carries `mark_status`. An `UNKNOWN` position keeps quantity,
  cost basis, average cost and realized P&L. Its mark, market value and
  unrealized P&L are `null`. No stale or inferred price is ever used.
- If any position is `UNKNOWN`, the account's `position_market_value`,
  `total_nav` and `unrealized_pnl` are `null`. Cash and realized P&L stay exact.
- `source.mark_freshness_status` is `PER_MARKET`. `validate_account_state()`
  rebuilds either schema from the embedded ledger, so a forged total or status
  fails derivation.

Consumers:

- **Exit manager** (`portfolio/crypto_paper_exit_manager.py`). Its contract
  names both views: `source_account_schema_version` (`/1`) and
  `per_market_source_account_schema_version` (`/2`), plus
  `unknown_mark_position_policy`. It accepts either view as the current account
  and as the plan's entry account, because entry economics come from the filled
  order, not from marks. For a FRESH position evaluation is unchanged, so its
  stop or harvest can select a PAPER SELL. For an `UNKNOWN` position only a
  non-FRESH observation is accepted, and it gives `WAIT_STALE_EVIDENCE`. That
  observation's price is not a usable mark, so `next_high_watermark` stays at
  `prior_high_watermark`. A FRESH observation against an UNKNOWN mark fails
  closed. `/1` outputs are byte-identical to before.
- **Runtime bridge** (`shadow/crypto_paper_runtime_bridge.py`). With an unknown
  NAV no new entry can be sized. The request records
  `PAPER_ACCOUNT_NAV_UNKNOWN:<markets>`, builds no eligibility, and returns
  status `WAIT_ACCOUNT_NAV_UNKNOWN`. Carried-order matches still proceed per
  market. A legacy `/2` request never carries a `/2` account
  (`RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_ACCOUNT`, also on revalidation).
- **Counterfactual validation** (`validation/crypto_paper_counterfactual.py`).
  A null NAV is never valued. A daily batch whose account view has no NAV fails
  closed with `ACCOUNT_STATE_NAV_UNKNOWN`; a null NAV row reaching the drawdown
  metric fails with `NAV_SERIES_VALUE_UNKNOWN`.
- **Private runtime** (atlas-private-evidence, a separate PR). It replaces
  `HOLD_PER_MARKET_HELD_POSITION_NOT_FRESH` with a normal observation over a
  `/2` view when some held market has no usable mark:
  - FRESH markets are matched, expired and exit-evaluated.
  - UNKNOWN markets are a per-market HOLD with the stale-hold reason, and the
    exit manager is not called for them.
  - The portal projection is not rebuilt from an account with null values;
    the last projection stays published, and the result says so. Rendering
    UNKNOWN positions is a later portal change.

### What stays blocked

- Any new PAPER entry while a held position is UNKNOWN, because NAV and risk
  sizing are unknown.
- Exit execution for the UNKNOWN market itself (ratified HOLD).

## D2: wall-clock alert on every runtime cycle

### Problem

`portfolio/crypto_paper_stale_hold.py` measures stale time between decision
packets (`generated_at`). Decision packets are not daily. They are written by
the Upbit realtime capture workflow (`upbit-realtime-capture.yml`, cron about
every 30 minutes), and only when a run succeeds and evaluates. The real cadence
has irregular gaps: on 2026-09-13 the packets are at 00:22, 14:47, 18:14,
21:12 and 23:14 UTC, a gap of more than 14 hours after the first. The private
runtime runs every 5 minutes. Measured between decisions, the ratified
30-minute alert could first fire hours late. When decisions stop entirely, it
never fires at all.

### Design (private runtime only; the public helper stays pure)

- Each run reads the wall clock once (`now`, UTC, injectable in tests) and
  keeps the decision-time state machine as the source of `stale_since`.
- For every market the current stale-hold state marks
  `HOLD_NO_PAPER_EXIT_EXECUTION`, if `now − stale_since > 30 minutes`, the run
  emits `HELD_POSITION_REALTIME_NOT_FRESH_WALL_CLOCK_BEYOND_ALERT_BUDGET`
  with `stale_since`, `wall_clock_now` and `wall_clock_stale_seconds`. It is
  repeated on every 5-minute run while the condition holds, including replays
  of the same decision that write nothing.
- When a state is first persisted with a market whose `stale_since` equals
  that observation, the run emits an immediate
  `HELD_POSITION_REALTIME_NOT_FRESH_HOLD_STARTED` notice.
- Both go to the result and to stderr (host journal) as redacted rows: market
  code, status, timestamps and seconds only. No quantity, price, cash or P&L.
- A wall clock earlier than `stale_since` never raises an alert and is
  reported as `wall_clock_behind_stale_since`.
- The decision-time alert from the public helper is unchanged.

### Decision-age alert (decisions stopped)

The stale-hold state only moves when a new decision arrives. If the capture
workflow stops producing decisions, `stale_since` never advances, and a market
that was FRESH in the last decision would keep looking FRESH. The wall-clock
alert above cannot see this, because nothing is marked stale.

- Each run computes `decision_age_seconds = now − generated_at` of the latest
  decision it consumed.
- If no new decision has arrived for more than N minutes, the run emits
  `CRYPTO_PAPER_DECISION_AGE_BEYOND_ALERT_BUDGET` with `decision_generated_at`,
  `wall_clock_now` and `decision_age_seconds`. It repeats on every run while
  the condition holds, whether or not any position is held.
- N is **PROPOSED, requires ratification**. This note sets no value. Until N is
  ratified, the run still reports `decision_age_seconds` on every result, but
  it emits no alert.
- FRESH is never assumed from the wall clock. A market's FRESH status is only
  "FRESH at the decision's capture time". The run records it that way, next to
  the decision age. It never extends FRESH to `now`, and it never infers a new
  stale-hold start for a market it has not observed.
- Whether an aged decision should also stop new PAPER entries or exits is a
  policy question. It is **PROPOSED, requires ratification**, and this note does
  not change it.
- A wall clock earlier than `generated_at` raises no alert and is reported as
  `wall_clock_behind_decision`. Rows are redacted like the stale-hold alerts.

The 30 minutes remain an engineering alert budget, not a policy threshold. No
alert changes an entry or exit outcome.
