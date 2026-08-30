# Upbit Market Evidence & Microstructure Contract (P4-07)

Status: exact-hash post-ratification consumer implemented; the first natural
PAPER-8 run remains the completion gate. This is **REST evidence only** --
P9-06 owns realtime ingestion. No order placement, cancellation, strategy
promotion, PAPER exit, or execution authority is created here.

Dependency: P3-12 (`universe/upbit_tradeable_universe.py`,
`identity/upbit_market_identity_proposal.py`, merged on `main`) + Upbit's
public quotation REST API + append-only evidence storage.

## What this extends, not duplicates

P3-12 already captures a daily `market/all`, `ticker`, `orderbook`, and
`candles/days` snapshot to build the `OBSERVATION_POOL -> TRADEABLE_UNIVERSE
-> PAPER_ELIGIBLE` classification. P4-07 does **not** re-fetch or re-derive
any of that. It instead:

1. Reads the most recently *committed* P3-12 classification record
   (`data/observations/upbit_tradeable_universe/<date>/packet.json`) to
   determine which markets are already at `TRADEABLE_UNIVERSE` or
   `PAPER_ELIGIBLE` -- capturing microstructure for markets nothing
   downstream can act on yet would be wasted network load. P4-07 consumes
   only a hash-valid record and inner packet hashes, the raw manifest hash,
   the ratified policy/taxonomy/effective-time lineage, and the complete
   market set before any provider call -- but the P3-12 ratification this
   bridge was originally anchored to (policy/taxonomy/identity registry
   ratified via PR #465, initial natural anchor record hash
   `a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12`, PAPER
   8: `BTC, ETH, LINK, SHIB, SOL, SUI, WLD, XRP`) was found to violate the
   identity source-authority evidence contract (CoinGecko-only citations,
   the KRW-LIT/Lighter-vs-Litentry conflict) and was **CIO-revoked,
   fail-closed** -- see `config/upbit_governance_revocations.json` and
   `config/upbit_p3_p4_bridge_contract.json`'s `approval_status:
   SUSPENDED_INVALID_UPSTREAM`. That anchor is never treated as active
   again, and `microstructure/upbit_p3_p4_bridge.py` refuses any
   provider call while suspended (`P3_ANCHOR_REVOKED`/`P3_BRIDGE_SUSPENDED`).
   Since P3-12's policy/taxonomy/identity are back to
   `PROPOSED_*_UNRATIFIED` (their pre-#465 state) and no new anchor has
   been separately CIO-ratified, this list is currently **empty in
   production**; an empty capture is a normal, successful,
   append-only-empty snapshot, exactly as
   `docs/upbit_tradeable_universe_contract.md` documents for
   `OBSERVATION_POOL` -- expected, not a bug. An earlier packet is never
   retroactively promoted with a later registry, and a revoked anchor is
   never silently reused either.
2. Captures a wider set of evidence per market: 15m/1h/4h/1d candles (not
   just daily), recent public trade ticks, and an orderbook snapshot with
   computed spread/depth/estimated-slippage and an explicit freshness
   contract.
3. **Reuses**, never duplicates, P3-12's spread/slippage formulas --
   `microstructure/upbit_market_evidence.py` imports
   `universe/upbit_tradeable_universe.py`'s `_spread_bps`/
   `_estimate_slippage_bps` unchanged (the same
   `importlib.util.spec_from_file_location` pattern that module itself uses
   to import `upbit_market_capture.py`).

## The candle-finalization boundary (the core new primitive)

Nothing in this repository, before this module, had an explicit "is this
sub-daily candle actually closed?" concept.
`config/crypto_breadth_contract.json`'s `current_candle_policy:
exclude_last_row_always` and `config/upbit_market_capture_contract.json`'s
`current_candle_policy: exclude_first_row_always` are both *daily-only*
idioms hardcoded to "drop exactly one row". P4-07 generalizes this into a
standalone, timeframe-aware boundary check
(`microstructure/upbit_candle_finalization.py`):

```
A candle is FINALIZED iff its close time has already elapsed as of the
evaluation instant (as_of), inclusive:  close_time <= as_of
```

`is_candle_finalized()` is the single pure function this reduces to, for
every one of `15m` / `1h` / `4h` / `1d`. `classify_candles()` wraps it into
a batch operation: parse, validate every required OHLCV field (fail closed
on any `UNKNOWN`/missing field), dedupe by open time (first-occurrence-wins,
same discipline as `upbit_market_capture.py::krw_markets`), reject any
candle whose OPEN time is itself later than `as_of` (`FUTURE_DATED_CANDLE`
-- that is not "in progress", it is corrupt/future-dated input), and
partition the result into `finalized` vs `in_progress`. **A candle in
`in_progress` is never usable as decision evidence** -- it simply does not
appear in `finalized_candles` in the derived evidence packet.

This primitive is deliberately standalone (no capture/network dependency)
so that P9-06 (real-time WebSocket layer), P5-08, P5-09, and P8-16 can all
import and depend on it directly without depending on this PR's REST
capture machinery.

## Gap detection and backfill

Because this is REST (not a persistent WebSocket connection), "reconnect"
here means retry-with-backoff on a transient HTTP failure
(`upbit_microstructure_capture.py::fetch_with_retry`, exponential backoff,
configurable `retry_max_attempts`/`retry_backoff_base_seconds`, fails
closed -- raises `FETCH_FAILED_MAX_RETRIES` -- after the last attempt,
never silently drops a market's evidence or returns a partial/empty
response as if it were real data).

"Backfill" means detecting and filling a gap in the append-only evidence
history for a market/timeframe (e.g. a day the capture cron failed):

- `expected_open_times(timeframe, window_start, window_end)` -- every
  timeframe-aligned candle open time in a window.
- `detect_gaps(present_open_times, timeframe, window_start, window_end)` --
  the subset of those not already present in committed evidence.
- `group_contiguous_gaps(missing_open_times, timeframe)` -- groups adjacent
  missing candles into the smallest number of re-query windows.
- `merge_finalized_no_overwrite(committed, new_finalized)` -- merges
  freshly re-captured finalized rows into the committed set. An
  already-committed open time is **never** silently overwritten: identical
  raw bytes are a harmless idempotent no-op; **different** raw bytes for an
  already-committed open time fails closed
  (`COMMITTED_CANDLE_MISMATCH`) -- out-of-order/late-arriving evidence for a
  past window must never silently rewrite history.

## Spread, depth, slippage

- **Spread**: `universe/upbit_tradeable_universe.py`'s `_spread_bps` on the
  orderbook snapshot's best bid/ask, reported in bps, with a `spread_status`
  (`NORMAL` / `ABNORMAL_EXCLUDED` / `NOT_COMPUTABLE`) against the policy's
  `max_spread_bps_normal` -- an extreme spread is flagged and excluded, not
  silently accepted as normal.
- **Depth**: cumulative KRW notional at the policy's `orderbook_depth_levels`
  on both the bid and ask side of the same snapshot.
- **Slippage**: `universe/upbit_tradeable_universe.py`'s
  `_estimate_slippage_bps` -- a volume-weighted-average-price walk of the
  captured ask-side depth for the policy's
  `paper_slippage_estimate_notional_krw`, versus best ask. `NOT_COMPUTABLE`
  (captured depth cannot fill the notional) or `ABNORMAL_EXCLUDED` (exceeds
  `max_slippage_bps_normal`) are both distinct from `NORMAL` and are never
  silently treated as a usable estimate.

These are single PIT snapshots at capture time, not intraday medians --
same honesty discipline P3-12's own doc states for its spread field.

## Freshness contract

Every evidence artifact (a timeframe's finalized candles, the trade-tick
snapshot, the orderbook snapshot) carries an explicit
`freshness: {status, age_seconds, max_staleness_seconds}`:

- `FRESH` -- `age_seconds <= max_staleness_seconds`, where `age_seconds` is
  `captured_at` minus the artifact's own reference instant (a candle's
  finalized close time; a trade/orderbook snapshot's own observed instant).
- `STALE` -- `age_seconds > max_staleness_seconds`.
- `UNKNOWN` -- fail-closed default when either timestamp is missing, or
  when `captured_at` precedes the reference instant (an impossible
  ordering -- evidence cannot be observed before the instant it describes).
  Never silently defaulted to `FRESH`.

The ratified PAPER-only policy packet is
`P4_07_UPBIT_PUBLIC_MARKET_EVIDENCE_PAPER_V1`, version
`upbit_market_evidence_policy/v1`, exact canonical packet hash
`26d921e4b98f91010b4397d6642c1dc6021d06ef134977cc80a94692e6e1df5e`.
It fixes 5-level depth, KRW 1,000,000 impact, 100/150bp quality caps,
15m/1h/4h/1d maximum ages 1,800/7,200/28,800/172,800 seconds, trade 600
seconds, and book 300 seconds. This naming is consistent with,
but not wired into, P9-01's `execution/intraday_freshness.py`
(`FRESH`/`STALE` naming, `provider_age_seconds` idiom) -- integrating P4-07
evidence into P9-01's guard machinery is P9-06's job, not this PR's.

## Evidence and determinism

`evidence/crypto/upbit/microstructure/<date>-p3-<record-hash-prefix>/` holds gzip-compressed raw
response bytes (an ndjson bundle per timeframe's candles, one for trade
ticks, and one batched orderbook snapshot), a `_manifest.json` with a
SHA-256 per component and `downloaded_at_utc`, and is append-only --
`upbit_microstructure_capture.py::capture_snapshot()` refuses to overwrite
an existing exact lineage key. Any tampered or
hash-mismatched raw file is rejected (`RAW_FILE_HASH_MISMATCH`) before a
single market is derived.

`microstructure/upbit_market_evidence.py`'s derivation functions are pure
functions of their arguments: no wall-clock or random value inside the
derivation math itself (only the capture layer's `captured_at` carries a
timestamp). The same raw evidence and policy always produce byte-identical
output --
`test_upbit_market_evidence_microstructure.py::FullPacketTests::test_determinism_same_input_twice_identical_output`.

`.github/scripts/upbit_microstructure_populate.py` mirrors
`upbit_universe_populate.py`'s idempotency discipline: rerunning against
the same committed raw snapshot re-derives and verifies a byte-identical
packet against what is already published, or fails closed
(`EXISTING_PACKET_DRIFT_OR_TAMPER`) on drift.

## Authority

Every derived row/packet's `authority` block
(`decision_eligible`, `entry_eligibility_authorized`,
`exit_eligibility_authorized`, `action_generation_authorized`,
`order_authorized`, `production_authorized`, `trading_authorized`) is
hardcoded `false` in code, unconditionally. This module produces evidence,
never a decision, entry, or order. Turning this evidence into decision/order
authority is a separate, later, explicitly-ratified change (P9-06 /
P5-08 / P5-09 / P8-13's job, not this one's).

## Offline commands

The `a9be9c63...` anchor below is the illustrative, historical example this
module was originally built and tested against; that specific anchor is now
**revoked** (see above) and `upbit_p3_p4_bridge.py` will refuse to run
against it while `config/upbit_p3_p4_bridge_contract.json` stays
`SUSPENDED_INVALID_UPSTREAM`. Re-running the command below requires a new,
separately CIO-ratified anchor.

```bash
python3 .github/scripts/upbit_microstructure_capture.py \
  --snapshot-root /tmp/upbit-microstructure/raw --snapshot-date 2026-08-30 \
  --snapshot-key 2026-08-30-p3-a9be9c63f9a39d1a \
  --universe-packet data/observations/upbit_tradeable_universe/2026-08-30/packet.json \
  --expected-universe-record-sha256 a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12

python3 .github/scripts/upbit_microstructure_populate.py 2026-08-30-p3-a9be9c63f9a39d1a \
  --raw-root /tmp/upbit-microstructure/raw --data-root /tmp/upbit-microstructure/data
```

Neither command calls a network provider outside the four public GET
endpoint families listed above (`candles/minutes/{15,60,240}`,
`candles/days`, `trades/ticks`, `orderbook`), adds a portfolio policy, or
opens a Production or trading path.
