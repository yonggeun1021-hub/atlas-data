# Upbit Real-time WebSocket Finalized-Candle & Orderbook Gate (P9-06)

Status: real-time WebSocket layer implemented as a deployment-agnostic,
fully mock-tested state machine, driven today by a **bounded-duration**
capture job on this repo's existing GitHub Actions cron architecture. No
order placement, cancellation, or execution code exists in this PR.

Dependency: P4-07 (`microstructure/upbit_candle_finalization.py`,
`microstructure/upbit_market_evidence.py`, merged on `main`) + P3-12
(`universe/upbit_tradeable_universe.py`, merged) + Upbit's public
market-data WebSocket + P9-01's `execution/intraday_freshness.py`
(reused, not reimplemented). P5-09 is listed both ways in Notion as a
dependency; this build order is P4-07 -> P9-06, and P9-06 does not need
P5-09's PAPER-eligibility logic.

## Safety invariants (never violated)

* No API key/secret is used or needed -- Upbit's public market-data
  WebSocket (`wss://api.upbit.com/websocket/v1`) requires no
  authentication.
* Only `ticker`/`trade`/`orderbook`/`candle.{15m,60m,240m}` are ever
  subscribed to. `myOrder`/`myAsset` (Upbit's private/order channels) are
  hard-forbidden **in code**, not merely by policy --
  `realtime/upbit_realtime_gate.py::PRIVATE_WS_TYPES_FORBIDDEN`,
  enforced by both `parse_message` (rejects an inbound private-type
  message) and `build_subscription_message` (raises before ever building a
  private-channel subscribe payload). No order/withdrawal/private REST
  endpoint is ever called.
* Every output row/status snapshot's `authority` block
  (`decision_eligible`, `entry_eligibility_authorized`,
  `exit_eligibility_authorized`, `action_generation_authorized`,
  `order_authorized`, `production_authorized`, `trading_authorized`,
  `private_channel_subscribed`, `order_channel_subscribed`) is hardcoded
  `false` in code, unconditionally.
* UNKNOWN/stale/insufficient/ambiguous/tampered/duplicate/out-of-order
  data fails closed -- never silently defaulted to fresh/valid/PASS.

## Verified against Upbit's actual current public WS surface

Verified live against `wss://api.upbit.com/websocket/v1` and
`docs.upbit.com/kr/reference/websocket-*` on 2026-08-29 (not assumed from
training-era memory):

* `ticker`/`trade`/`orderbook` all exist as documented, with the field
  names this module validates against (`trade_price`, `timestamp`,
  `trade_timestamp`, `sequential_id`, `orderbook_units`, `stream_type`
  `SNAPSHOT`/`REALTIME`, etc.).
* A `candle.{unit}` stream **does exist** over WS (`candle.1s`/`1m`/`3m`/
  `5m`/`10m`/`15m`/`30m`/`60m`/`240m`), contrary to an initial assumption
  that it might not -- its response fields
  (`candle_date_time_utc`/`opening_price`/`high_price`/`low_price`/
  `trade_price`/`candle_acc_trade_price`/`candle_acc_trade_volume`) are
  **identical** to the REST candle endpoint's fields, so WS candle rows
  feed into `microstructure/upbit_candle_finalization.py::classify_candles`
  completely unchanged -- no adapter, no reimplementation.
* There is **no daily (`1d`) candle stream over WS**. `1d` finalized-candle
  tracking stays a REST-only P4-07 concern; this module only tracks
  `15m`/`1h`/`4h` over WS (`CANDLE_WS_TYPE_BY_TIMEFRAME`).
* `trade`'s `sequential_id` is Upbit's own documented unique,
  monotonically-increasing per-market execution identifier -- but it is
  **not** a small consecutive-integer space (live-observed values look
  like `17879341734670000`, i.e. timestamp-derived, not `1, 2, 3, ...`).
  Gap detection therefore cannot be "did `sequential_id` skip by more than
  one" -- see below.

## Reuse, not reinvention

* **Finalized-candle idempotency** (`realtime/upbit_realtime_gate.py::
  CandleLedger`) is built directly on
  `microstructure/upbit_candle_finalization.py`'s
  `classify_candles`/`merge_finalized_no_overwrite` -- the exact P4-07
  primitive, imported unchanged. A candle's finalization state
  (`close_time <= as_of`) is a pure function of its own open time +
  timeframe + wall clock, never of which transport (REST or WS) delivered
  the row -- so this reuse is exact. An in-progress candle is never merged
  into committed state (`test_in_progress_candle_never_finalized_reuses_
  p4_07_boundary`); a finalized candle re-delivered across a reconnect
  (identical bytes) is a harmless no-op; different bytes for an
  already-committed open time fails closed
  (`CandleFinalizationError: COMMITTED_CANDLE_MISMATCH`), propagated
  unchanged, never caught-and-silenced.
* **Candle-dimension gap detection** (`candle_gap_windows`) is a thin
  wrapper directly calling P4-07's `detect_gaps`/`group_contiguous_gaps`
  on a market/timeframe's committed finalized open times.
* **Real-time freshness evaluation**
  (`evaluate_via_intraday_freshness_guard`) calls P9-01's
  `execution/intraday_freshness.py::evaluate_freshness` **directly** --
  not reimplemented. This repository ships no default/ratified `CRYPTO`
  threshold (same `repository_default_policy: ABSENT` discipline P9-01
  itself established), so in production this call always fails closed to
  `UNKNOWN` (`P9_01_RATIFIED_POLICY_ABSENT`) until a human ratifies a real
  `intraday_freshness_policy/1` packet for the `CRYPTO` market -- see
  `config/upbit_realtime_freshness_policy_proposal.json` (a proposal
  artifact only, never fed programmatically into the guard).
* **Identity scoping** (`eligible_markets_from_universe_packet`) mirrors
  `.github/scripts/upbit_microstructure_capture.py::load_target_markets`
  verbatim: only markets already at `TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE`
  in the most recently *committed* P3-12 classification packet are ever
  subscribed to. Since P3-12's policy/taxonomy/identity remain unratified,
  this list is currently **empty in production** -- an empty subscription
  is a normal, successful outcome, not a bug, same discipline P3-12/P4-07
  established. A market present on Kraken, or anywhere else, is never
  auto-promoted into this set (`test_kraken_or_any_other_exchange_market_
  never_auto_subscribed`).

## Two dimensions of gap detection

Trade sequence numbers are not small consecutive integers, so "gap
detection" cannot mean "did the counter skip". Two distinct, both honest,
mechanisms are used instead:

1. **Connection-outage windows** (`connection_gap_windows`): the interval
   between an explicit disconnect and the next successful reconnect,
   tracked by `ConnectionStateMachine` itself -- unambiguous, deterministic,
   never inferred from message spacing. Any interval at least
   `connection_gap_min_seconds_for_backfill` long is a REST backfill
   candidate.
2. **Candle-dimension gaps** (`candle_gap_windows`): missing
   timeframe-aligned open times in the committed finalized-candle ledger,
   using P4-07's own primitive -- candles ARE expected at fixed intervals,
   unlike raw trade arrival spacing.

## Duplicate guard vs out-of-order tracker (two distinct checks)

* **`DuplicateGuard`**: rejects an **exact** duplicate -- same natural key
  (`trade`: `sequential_id`; `candle`: `(timeframe, open_time)`;
  `ticker`/`orderbook`: `timestamp`) **and** identical payload bytes. A
  same-key, different-payload message (e.g. two orderbook snapshots
  sharing a millisecond timestamp, or a retransmitted in-progress candle
  with updated OHLCV -- Upbit's own docs note `candle_date_time` can be
  retransmitted) is a genuine new observation, always accepted.
* **`SequenceTracker`**: flags (never raises for) an out-of-order message
  -- a `trade` whose `sequential_id` regresses, or a `ticker`/
  `orderbook`/`candle` whose `timestamp` regresses, versus the highest
  already seen for that key. An out-of-order message never advances the
  "latest" pointer and never corrupts tracked state.

## Reconnect state machine

`ConnectionStateMachine` is pure -- no socket, no wall-clock read inside
the class, every timestamp caller-supplied -- states
`CONNECTING -> CONNECTED -> RECONNECTING -> (RETRY | WAIT_MAX_RETRIES_
EXCEEDED)`, plus an explicit `STOPPED` reached only via `request_stop()`
(the kill-switch). Backoff is exponential and capped
(`next_backoff_seconds`: `base * 2**(attempt-1)`, capped at
`max_backoff_seconds`). Exceeding `max_attempts` fails closed to
`WAIT_MAX_RETRIES_EXCEEDED` -- calling `next_attempt()` again in that state
raises rather than silently retrying forever.

## Architecture decision: bounded-run-via-cron, not a persistent daemon

This repository's existing automation is GitHub Actions cron jobs that
run, capture, commit, and exit -- there is no long-running-process
infrastructure here for a genuinely persistent 24/7 WebSocket connection.
Forcing one into a `timeout-minutes`-bounded GitHub Actions job would be
dishonest about what is actually being tested/verified.

`realtime/upbit_realtime_gate.py` is therefore built **deployment-agnostic
first**: every class (`ConnectionStateMachine`, `DuplicateGuard`,
`SequenceTracker`, `CandleLedger`, `RealtimeGate`) has no opinion about how
long it runs or what drives it -- no `websockets` import, no `asyncio`
import, no socket, no wall-clock read anywhere in this file. Today,
`.github/scripts/upbit_realtime_capture.py` drives it for one bounded,
configurable-duration window per cron trigger (default 240s;
`.github/workflows/upbit-realtime-capture.yml` runs every 30 minutes,
`timeout-minutes: 10`), reconnecting within that window, writing an
append-only evidence snapshot, and exiting cleanly. A genuinely persistent
daemon on separate always-on infrastructure later would drive the exact
same `RealtimeGate` class unchanged -- that is a future, separate
infrastructure/deployment-track decision, explicitly out of scope for this
PR (which is the public data-contract repo, cron-based).

## The `websockets` dependency (new)

The `websockets>=12.0` package is added to `requirements.txt` --
**explicitly called out here, not added silently**. It is imported
**lazily**, inside `.github/scripts/upbit_realtime_capture.py`'s
`_connect_and_stream` function only, never at module import time and never
anywhere in `realtime/upbit_realtime_gate.py`. This means:

* `realtime/upbit_realtime_gate.py` and its full regression suite
  (`test/test_upbit_realtime_gate.py`, 44 tests, entirely mocked messages)
  have **zero** dependency on `websockets` -- confirmed by
  `test_module_has_no_websockets_or_asyncio_dependency` and
  `test_capture_script_never_imports_a_private_order_helper_and_uses_
  lazy_websockets_import`.
* `websockets` is **not** added to `requirements-ci.txt` -- consistent with
  that file's "fixture-only, no network-call-requiring package" discipline
  (`run_all.py`'s approved regression never opens a real socket).
* It is only required for an actual bounded capture run
  (`.github/scripts/upbit_realtime_capture.py`'s `main()` /
  `.github/workflows/upbit-realtime-capture.yml`'s `pip install
  "websockets>=12.0"` step), which is outside `run_all.py`'s approved
  regression, same as `pykrx`/`pandas`/`requests` already are for other
  collectors.

## Evidence and determinism

`.github/scripts/upbit_realtime_capture.py::write_evidence_snapshot`
writes one append-only run record per bounded capture under
`evidence/crypto/upbit/realtime/<date>/run_NNN.json` (never overwrites an
existing run file; multiple bounded runs on the same UTC date append new
numbered files). Each run record carries `schema_version`,
`transform_version`, the same `auth_required`/
`order_or_withdrawal_endpoints_called`/`private_channel_subscribed: false`
invariants as P4-07's manifest, a `source_sha256` over the run payload, the
full accepted/rejected/duplicate/out-of-order message log, the final
`RealtimeGate.status_snapshot()`, and the finalized-candle ledger.

## Offline commands

```bash
python3 test/test_upbit_realtime_gate.py

# Bounded-duration run (needs `pip install websockets` and live network;
# never part of run_all.py's offline regression):
python3 .github/scripts/upbit_realtime_capture.py \
  --evidence-root /tmp/upbit-realtime/evidence --duration-seconds 30 \
  --universe-packet data/observations/upbit_tradeable_universe/2026-08-29/packet.json
```

Neither command calls a network provider outside the public
`ticker`/`trade`/`orderbook`/`candle.*` WebSocket channels listed above,
adds a portfolio policy, or opens a Production or trading path.
