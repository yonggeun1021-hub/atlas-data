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

- **Exit manager** (`portfolio/crypto_paper_exit_manager.py`). It accepts a `/2`
  view as the current account and as the plan's entry account, because entry
  economics come from the filled order, not from marks. For a FRESH position
  evaluation is unchanged, so its stop or harvest can select a PAPER SELL. For
  an `UNKNOWN` position only a non-FRESH observation is accepted, and it gives
  `WAIT_STALE_EVIDENCE`. A FRESH observation against an UNKNOWN mark fails
  closed.
- **Runtime bridge** (`shadow/crypto_paper_runtime_bridge.py`). With an unknown
  NAV no new entry can be sized. The request records
  `PAPER_ACCOUNT_NAV_UNKNOWN:<markets>`, builds no eligibility, and returns
  status `WAIT_ACCOUNT_NAV_UNKNOWN`. Carried-order matches still proceed per
  market. A legacy `/2` request never carries a `/2` account.
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
packets (`generated_at`). Decisions are daily at 07:00Z, while the private
runtime runs every 5 minutes. The ratified 30-minute alert could therefore
first fire about a day late.

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

The 30 minutes remain an engineering alert budget, not a policy threshold. No
alert changes an entry or exit outcome.
