# Upbit realtime observation service contract

Status: implemented as a standalone Docker Compose service
(`services/upbit-realtime-observation/`) intended to run persistently on the
operator's own Ubuntu host, **not** on this repository's GitHub Actions
automation. Not deployed by this PR -- see "Deployment model" below.

This document is the JSON snapshot shape, the freshness semantics, and the
explicit observation-only boundary statement for `GET /snapshot`. See
`services/upbit-realtime-observation/README.md` for run instructions.

## Observation only -- never a candidate/decision input

This service:

- Never reads, imports, or references `universe/upbit_tradeable_universe.py`
  (P3-12) or any `TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE`/candidate-eligibility
  state.
- Never writes to any such state -- there is no code path from this service
  into P3-12/P5/P7/P8 authority anywhere in its source.
- Never writes to `atlas-data`'s `evidence/` or `data/` directories. It is
  not a GitHub Actions capture step and must never be confused with, or
  treated as feeding, the existing P3-12/P4-07/P9-06 evidence pipelines.
  State lives in the process's own memory only and is lost on restart.
- Hardcodes every authority/promotion flag to `false` in every snapshot (see
  below) -- this is enforced in code
  (`services/upbit-realtime-observation/observation_gate.py::
  _OBSERVATION_AUTHORITY`), not merely documented as policy.
- Uses no Upbit API key/secret and subscribes to no private/order channel --
  `myOrder`/`myAsset` are hard-forbidden by the reused, tested
  `realtime/upbit_realtime_gate.py::PRIVATE_WS_TYPES_FORBIDDEN` guard. No
  order/cancel/withdrawal endpoint is called anywhere in this service.

A future consumer (a public portal, a briefing step, anything else) reading
`/snapshot` receives current market observation only. Treating any field in
this response as a buy/sell signal, an eligibility decision, or an entry
proposal is a misuse of this contract.

## Freshness semantics

Every market/kind carries exactly one of four freshness values -- never a
silent default to `FRESH`:

| Value | Meaning |
| --- | --- |
| `FRESH` | The WebSocket connection is `CONNECTED` and the most recent accepted message for that market/kind is within its configured staleness window. |
| `STALE` | The WebSocket connection is `CONNECTED` but the most recent accepted message is older than its configured staleness window. |
| `DISCONNECTED` | The WebSocket connection is not currently `CONNECTED` (`CONNECTING`/`RECONNECTING`/`STOPPED`), regardless of how recent the last accepted message was. A stale connection is never reported as fresh merely because an old message happens to still fall inside the staleness window. |
| `NO_DATA` | The WebSocket connection is `CONNECTED` but no message has ever been accepted yet for that market/kind (also covers an internally-impossible timestamp ordering -- this contract has no separate `UNKNOWN` bucket). |

Default staleness windows: `ticker` 30 seconds, `orderbook` 15 seconds
(configurable via `ATLAS_UPBIT_OBS_TICKER_MAX_STALENESS_SECONDS` /
`ATLAS_UPBIT_OBS_ORDERBOOK_MAX_STALENESS_SECONDS`).

`markets.<code>.freshness` is the worse of `ticker_freshness.status` and
`orderbook_freshness.status` (severity order `FRESH < STALE < NO_DATA <
DISCONNECTED`). `overall_freshness` is the worst freshness across every
observed market.

## Duplicate and out-of-order handling

- **Exact-duplicate suppression**: an identical byte-for-byte retransmission
  of the most recently accepted message for a given `(kind, market)` is
  ignored and counted in `counts.duplicate_ignored`. This is deliberately
  narrower than P9-06's own full-history duplicate guard -- see
  `observation_gate.py`'s module docstring "Reuse vs. adapt" for why a 24/7
  daemon needs a bounded-memory adaptation instead of P9-06's per-run
  history.
- **Out-of-order flagging**: a `ticker`/`orderbook` message whose exchange
  timestamp regresses versus the highest already seen for that market/kind
  is flagged (`counts.out_of_order`) and never allowed to advance the
  "latest" state -- reused unchanged from
  `realtime/upbit_realtime_gate.py::SequenceTracker`.
- Neither check ever raises or crashes the connection loop; both are
  non-fatal, always-counted outcomes.

## Reconnection

Exponential backoff capped at a configured maximum
(`ATLAS_UPBIT_OBS_BASE_BACKOFF_SECONDS` /
`ATLAS_UPBIT_OBS_MAX_BACKOFF_SECONDS`, same formula as P9-06's
`next_backoff_seconds`, reused unchanged). Unlike P9-06's bounded-run gate,
this service **never permanently gives up** on its own -- there is no next
scheduled cron trigger to retry it, so it must keep trying forever with the
last-computed capped backoff. The only way it stops retrying is an explicit
process shutdown (`SIGTERM`/`SIGINT`, e.g. `docker compose down`/`restart`).
While disconnected or reconnecting, every market/kind reports `DISCONNECTED`
freshness -- never a silently-stale `FRESH`/`STALE` value computed from data
observed before the outage.

## `GET /snapshot` JSON shape

```json
{
  "schema_version": "upbit_realtime_observation_snapshot/1",
  "service": "upbit-realtime-observation",
  "generated_at_utc": "2026-08-29T12:00:00.000Z",
  "generated_at_kst": "2026-08-29T21:00:00.000+09:00",
  "connection_state": "CONNECTED",
  "reconnect_count": 0,
  "consecutive_failures": 0,
  "last_disconnect_reason": null,
  "overall_freshness": "FRESH",
  "markets": {
    "KRW-BTC": {
      "market": "KRW-BTC",
      "freshness": "FRESH",
      "ticker_freshness": {"status": "FRESH", "age_seconds": 2},
      "orderbook_freshness": {"status": "FRESH", "age_seconds": 1},
      "last_price": 123456000.0,
      "change_direction": "RISE",
      "change_rate": 0.0123,
      "signed_change_rate": 0.0123,
      "acc_trade_volume_24h": 1234.5678,
      "acc_trade_price_24h": 152389000000.0,
      "best_bid": {"price": 123455000.0, "size": 0.05},
      "best_ask": {"price": 123456000.0, "size": 0.03},
      "ticker_exchange_timestamp_utc": "2026-08-29T12:00:00.000Z",
      "orderbook_exchange_timestamp_utc": "2026-08-29T12:00:01.000Z",
      "received_at_utc": "2026-08-29T12:00:00.050Z",
      "received_at_kst": "2026-08-29T21:00:00.050+09:00",
      "orderbook_received_at_utc": "2026-08-29T12:00:01.030Z",
      "orderbook_received_at_kst": "2026-08-29T21:00:01.030+09:00"
    }
  },
  "counts": {
    "accepted": 0, "duplicate_ignored": 0, "out_of_order": 0,
    "rejected_malformed": 0, "rejected_out_of_scope_market": 0,
    "rejected_unsupported_kind": 0
  },
  "duplicate_guard_size": 0,
  "authority": {
    "decision_eligible": false,
    "entry_eligibility_authorized": false,
    "exit_eligibility_authorized": false,
    "action_generation_authorized": false,
    "order_authorized": false,
    "production_authorized": false,
    "trading_authorized": false,
    "private_channel_subscribed": false,
    "order_channel_subscribed": false,
    "candidate_promotion_authorized": false,
    "tradeable_universe_write_authorized": false,
    "paper_eligibility_authorized": false
  },
  "observation_only": true,
  "feeds_tradeable_universe": false,
  "feeds_candidate_promotion": false,
  "feeds_paper_eligibility": false,
  "feeds_decision_or_order_path": false,
  "payload_sha256": "<sha256 over the snapshot with this field omitted>"
}
```

Field notes for a future consumer (e.g. the `atlas-portal` PR referenced as
"item 2" of this same CIO task):

- `last_price`, `change_direction`/`change_rate`/`signed_change_rate`,
  `acc_trade_volume_24h`/`acc_trade_price_24h` come from Upbit's public
  `ticker` channel (`trade_price`/`change`/`change_rate`/
  `signed_change_rate`/`acc_trade_volume_24h`/`acc_trade_price_24h`
  verbatim).
- `best_bid`/`best_ask` come from Upbit's public `orderbook` channel's first
  `orderbook_units` entry (`bid_price`/`bid_size`/`ask_price`/`ask_size`).
- `received_at_utc`/`received_at_kst` are this service's own wall-clock
  receipt time for the `ticker` message (`orderbook_received_at_*` for the
  `orderbook` message), not Upbit's own timestamp.
  `ticker_exchange_timestamp_utc`/`orderbook_exchange_timestamp_utc` are
  Upbit's own `timestamp` field for each channel, converted to UTC.
- Any field can be `null` when that market/kind has never been observed
  (`NO_DATA`) -- check the corresponding `*_freshness.status` before trusting
  a value, never assume a non-`FRESH` value is still meaningful.
- `markets` only contains the fixed, configured observation set (see
  `services/upbit-realtime-observation/.env.example`,
  `ATLAS_UPBIT_OBS_MARKETS`) -- it is never the full Upbit market list and
  never P3-12's `TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE` set.
- `payload_sha256` is computed the same way as every other `atlas-data`
  evidence artifact (`sha256` over canonical JSON with the hash field itself
  omitted) for consistency, even though this snapshot is never persisted as
  evidence.

This is versioned via `schema_version`. A future breaking change increments
the version suffix (`/2`, ...); a consumer should reject an unrecognized
`schema_version` rather than guess at field meaning.

## Deployment model

Docker Compose (`services/upbit-realtime-observation/compose.yaml`) on the
operator's own Ubuntu host, started with `docker compose up -d --build`,
`restart: unless-stopped`. This is new infrastructure separate from this
repository's GitHub Actions cron automation
(`.github/workflows/upbit-realtime-capture.yml` and siblings) -- neither
reads from nor writes to the other. See
`services/upbit-realtime-observation/README.md` "Outbound-only Portal
delivery" for the signed Ubuntu-to-Sites path. The local API remains bound
to `127.0.0.1:8792`; the Ubuntu host needs outbound HTTPS only.

This PR does not deploy the service. Bringing it up on the operator's actual
Ubuntu host, and any resulting live-traffic observation, is a separate,
later, natural-operation step.
