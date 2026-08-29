# Upbit realtime observation service

A standalone, observation-only, 24/7 process that streams Upbit's **public**
market-data WebSocket and exposes the latest price/change/volume/bid-ask
state over a local read-only HTTP API. It is designed to run persistently on
the user's own Ubuntu server, separate from this repository's GitHub Actions
automation (`.github/workflows/upbit-realtime-capture.yml`, P9-06's bounded
cron capture) -- see [`docs/upbit_realtime_observation_service_contract.md`](../../docs/upbit_realtime_observation_service_contract.md)
for the full contract.

## What this is not

- **Not** a candidate/eligibility producer. It never reads, imports, or
  writes `universe/upbit_tradeable_universe.py` (P3-12) state. It cannot
  promote a market into `TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE`/any
  candidate/decision-authority state -- there is no code path from this
  service into that module at all.
- **Not** an evidence-capture job. It never writes to `atlas-data`'s
  `evidence/` or `data/` directories. State lives in the process's memory
  only and is lost on restart -- there is nothing here for
  `.github/workflows/*` to consume, and nothing here consumes their output
  either.
- **Not** an account/order integration. No Upbit API key or secret is used
  or needed. Only the public `ticker` and `orderbook` WebSocket channels are
  ever subscribed to. `myOrder`/`myAsset` (Upbit's private/order channels)
  and every order/cancel/withdrawal endpoint are hard-forbidden in code (see
  "Safety invariants" below), not merely by policy.

## Fixed authority boundary

Every `/snapshot` response's `authority` block is hardcoded all-`false` in
code, unconditionally: `decision_eligible`, `entry_eligibility_authorized`,
`exit_eligibility_authorized`, `action_generation_authorized`,
`order_authorized`, `production_authorized`, `trading_authorized`,
`private_channel_subscribed`, `order_channel_subscribed`,
`candidate_promotion_authorized`, `tradeable_universe_write_authorized`,
`paper_eligibility_authorized`. `observation_only: true` and
`feeds_tradeable_universe`/`feeds_candidate_promotion`/
`feeds_paper_eligibility`/`feeds_decision_or_order_path` are all `false`.

## Safety invariants (never violated)

- No API key/secret anywhere in this service -- Upbit's public
  `wss://api.upbit.com/websocket/v1` requires no authentication.
- Only `ticker`/`orderbook` are ever subscribed to or handled.
  `PRIVATE_WS_TYPES_FORBIDDEN` (`myOrder`, `myAsset`) is enforced by
  `realtime/upbit_realtime_gate.py::parse_message` and its private-channel
  deny-list. The local subscription builder narrows the public request to
  exactly `ticker` and `orderbook`.
- No order/withdrawal/private REST endpoint is ever called -- there is no
  HTTP client to Upbit's REST API anywhere in this service, only the
  WebSocket.
- The local HTTP API is read-only: every non-`GET` method returns
  `405 READ_ONLY_SERVICE`.
- Stale, disconnected, or never-seen data is never silently reported as
  fresh -- see the four-value freshness contract in the docs contract file.

## Architecture

- `observation_gate.py` -- pure, fully-testable state machine (no socket, no
  `websockets`/`asyncio` import). Adapts three pieces of
  `realtime/upbit_realtime_gate.py` (P9-06) for continuous 24/7 operation
  instead of P9-06's ~240-second bounded cron run; see that file's module
  docstring "Reuse vs. adapt" section for the exact reasoning. Unit-tested in
  `test/test_upbit_realtime_observation_service.py` with mocked messages,
  no live connection.
- `service.py` -- the async I/O wrapper: opens the real WebSocket
  (`websockets` package, imported lazily), drives `observation_gate.py`
  forever with reconnect-with-backoff, and serves `/health`, `/ready`,
  `/snapshot` over `http.server.ThreadingHTTPServer` (stdlib only, no web
  framework dependency).

## HTTP API

| Route | Method | Purpose |
| --- | --- | --- |
| `/health` | GET | Liveness only -- process is up and serving HTTP. Always 200 once started. |
| `/ready` | GET | 200 when the WebSocket is `CONNECTED`, 503 otherwise. |
| `/snapshot` | GET | Full state -- see the JSON contract in `docs/upbit_realtime_observation_service_contract.md`. Always 200; the payload itself tells the truth about freshness/connection even when not ready. |

Every other method on every path returns `405 READ_ONLY_SERVICE` (non-`GET`)
or `404 NOT_FOUND` (unknown `GET` path).

## Running on a fresh Ubuntu host

Prerequisites: Docker Engine with the Compose plugin (`docker compose
version`).

```bash
git clone https://github.com/yonggeun1021-hub/atlas-data.git
cd atlas-data/services/upbit-realtime-observation
cp .env.example .env       # edit market list / Portal URL if desired
docker compose up -d --build
curl -sS http://127.0.0.1:8792/health
curl -sS http://127.0.0.1:8792/ready
curl -sS http://127.0.0.1:8792/snapshot | head -c 500
```

`restart: unless-stopped` in `compose.yaml` means the container restarts
automatically after a host reboot or a container crash; the service itself
also never permanently gives up on a WebSocket reconnect (see
`observation_gate.PersistentConnectionState`) -- the two together are the
"24/7" guarantee.

To stop: `docker compose down` (from this directory). To view logs:
`docker compose logs -f upbit-realtime-observation`.

## Outbound-only Portal delivery

`compose.yaml` keeps the HTTP API on `127.0.0.1:8792`. When
`ATLAS_PORTAL_PUSH_URL` is configured, the daemon signs each snapshot with
the existing operator Ed25519 key and sends it to Sites over outbound HTTPS.
No inbound firewall rule, public host port, reverse proxy, or exchange
credential is required.

## Configuration

See `.env.example` for the full list (market set, bind host/port, backoff,
per-channel staleness thresholds). Upbit credentials are never required or
accepted. Portal delivery uses the operator's existing mounted signing key
and Sites bypass environment file; neither value is stored in this repo.

## Local (non-Docker) run, for development

```bash
pip install "websockets>=12.0,<16" "cryptography>=43,<46"
ATLAS_UPBIT_OBS_MARKETS=KRW-BTC,KRW-ETH python3 service.py
```

## Tests

```bash
python3 test/test_upbit_realtime_observation_service.py
```

Entirely mocked WebSocket messages -- no live connection required. A
separate, clearly-labelled manual smoke test against the real public
WebSocket is documented in that test file's module docstring; it is not part
of the automated regression.
