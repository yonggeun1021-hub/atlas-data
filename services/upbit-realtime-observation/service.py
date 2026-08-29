#!/usr/bin/env python3
"""Standalone 24/7 Upbit public-market realtime observation service.

Entry point for the ``upbit-realtime-observation`` Docker service. Connects
to Upbit's **public** market-data WebSocket
(``wss://api.upbit.com/websocket/v1``), streams ``ticker``/``orderbook`` for
a fixed configured market set, keeps the latest price/change/volume/bid-ask
state in memory via ``observation_gate.ObservationGate`` (the pure,
independently-unit-tested state machine in this same directory), and serves
it over a minimal local read-only HTTP API (``/health``, ``/ready``,
``/snapshot``).

No API key or secret is used or needed -- Upbit's public market-data
WebSocket requires no authentication. This file never imports, builds, or
sends anything referencing ``myOrder``/``myAsset`` (Upbit's private/order
channels) and never calls any order/cancel/withdrawal endpoint of any kind.
Subscription-message construction is delegated unchanged to
``observation_gate.build_subscription_message``, which itself delegates to
``realtime.upbit_realtime_gate.build_subscription_message`` -- the same
tested function P9-06 uses, including its private-channel-forbidden guard.

This process holds state in memory only. It never writes to ``atlas-data``'s
``evidence/`` or ``data/`` directories and is not a GitHub Actions capture
step -- it is meant to run continuously on the operator's own Ubuntu host
via ``docker compose up -d`` (see ``README.md`` / ``compose.yaml``).

The ``websockets`` package is imported **lazily**, inside ``_stream_forever``
only -- never at module import time -- mirroring
``.github/scripts/upbit_realtime_capture.py``'s own discipline, so
``observation_gate.py`` and its test suite have zero dependency on it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

import observation_gate as OG  # noqa: E402

LOG = logging.getLogger("atlas.upbit-realtime-observation")

WS_ENDPOINT = "wss://api.upbit.com/websocket/v1"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class ServiceConfigError(ValueError):
    """Fail-closed startup configuration error."""


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ServiceConfigError(f"{name}_INVALID:{raw}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ServiceConfigError(f"{name}_INVALID:{raw}") from exc


def load_config_from_env() -> dict:
    markets_raw = os.getenv("ATLAS_UPBIT_OBS_MARKETS", ",".join(OG.DEFAULT_MARKETS))
    markets = OG.parse_market_list(markets_raw)
    bind = os.getenv("ATLAS_UPBIT_OBS_BIND", "127.0.0.1")
    # Fail closed rather than silently expose the API beyond loopback --
    # deliberate public exposure is an operator decision made in the reverse
    # proxy / firewall layer described in README.md, not a default here.
    require_loopback = os.getenv("ATLAS_UPBIT_OBS_ALLOW_NON_LOOPBACK_BIND", "false").strip().lower() != "true"
    if require_loopback and bind not in LOOPBACK_HOSTS:
        raise ServiceConfigError(
            "ATLAS_UPBIT_OBS_BIND_MUST_BE_LOOPBACK: set "
            "ATLAS_UPBIT_OBS_ALLOW_NON_LOOPBACK_BIND=true to override deliberately"
        )
    return {
        "markets": markets,
        "bind": bind,
        "port": _env_int("ATLAS_UPBIT_OBS_PORT", 8791),
        "base_backoff_seconds": _env_float("ATLAS_UPBIT_OBS_BASE_BACKOFF_SECONDS", 1.0),
        "max_backoff_seconds": _env_float("ATLAS_UPBIT_OBS_MAX_BACKOFF_SECONDS", 30.0),
        "max_staleness_seconds_by_kind": {
            "ticker": _env_int("ATLAS_UPBIT_OBS_TICKER_MAX_STALENESS_SECONDS", 30),
            "orderbook": _env_int("ATLAS_UPBIT_OBS_ORDERBOOK_MAX_STALENESS_SECONDS", 15),
        },
    }


# ---------------------------------------------------------------------------
# WebSocket I/O -- the only place this file touches a real socket
# ---------------------------------------------------------------------------

async def _stream_forever(gate: "OG.ObservationGate", markets: list, stop_event: "asyncio.Event") -> None:
    """Runs until ``stop_event`` is set, reconnecting forever (see
    ``observation_gate.PersistentConnectionState`` for why this never gives
    up permanently the way P9-06's bounded-run capture does)."""
    import websockets  # lazy import -- see module docstring

    ticket = f"atlas-upbit-realtime-observation-{OG.utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    subscribe_message = OG.build_subscription_message(markets, ticket=ticket)

    while not stop_event.is_set():
        try:
            async with websockets.connect(WS_ENDPOINT, ping_interval=20, open_timeout=15) as ws:
                gate.on_connected(OG.utc_now())
                LOG.info("connected to %s (%d markets)", WS_ENDPOINT, len(markets))
                await ws.send(json.dumps(subscribe_message))
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue
                    received_at = OG.utc_now()
                    try:
                        parsed_raw = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        gate.counts["rejected_malformed"] += 1
                        continue
                    gate.handle_message(parsed_raw, received_at=received_at)
        except (websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as exc:
            reason = f"{type(exc).__name__}:{exc}"
            gate.on_disconnect(OG.utc_now(), reason)
            LOG.warning("disconnected (%s); reconnecting with backoff", reason)
            attempt = gate.next_reconnect_attempt()
            if stop_event.is_set():
                break
            await asyncio.sleep(attempt["backoff_seconds"])


async def _run_async(gate: "OG.ObservationGate", markets: list) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()

    def _request_stop(*_args) -> None:
        LOG.info("shutdown signal received")
        stop_event.set()
        gate.request_stop()

    installed = []
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            pass  # platform without loop signal-handler support -- degrade, not fatal
    try:
        await _stream_forever(gate, markets, stop_event)
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


# ---------------------------------------------------------------------------
# HTTP API -- local, read-only, no write methods
# ---------------------------------------------------------------------------

def _make_handler(gate: "OG.ObservationGate", started_at_utc: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AtlasUpbitRealtimeObservation/1"

        def _send(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                # Liveness only -- the process is up and serving HTTP. Does
                # not imply the WebSocket is connected; use /ready for that.
                self._send(200, {
                    "service": "upbit-realtime-observation",
                    "status": "ok",
                    "startedAtUtc": started_at_utc,
                })
                return
            if self.path == "/ready":
                snapshot = gate.status_snapshot()
                ready = snapshot["connection_state"] == OG.CONNECTED
                self._send(200 if ready else 503, {
                    "service": "upbit-realtime-observation",
                    "ready": ready,
                    "connectionState": snapshot["connection_state"],
                    "overallFreshness": snapshot["overall_freshness"],
                    "startedAtUtc": started_at_utc,
                })
                return
            if self.path == "/snapshot":
                self._send(200, gate.status_snapshot())
                return
            self._send(404, {"error": "NOT_FOUND"})

        def do_POST(self) -> None:  # noqa: N802
            self._send(405, {"error": "READ_ONLY_SERVICE"})

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, fmt: str, *args: object) -> None:
            LOG.info("http %s", fmt % args)

    return Handler


def main() -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        config = load_config_from_env()
    except (OG.ObservationServiceError, ServiceConfigError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    gate = OG.ObservationGate(
        markets=config["markets"],
        max_staleness_seconds_by_kind=config["max_staleness_seconds_by_kind"],
        base_backoff_seconds=config["base_backoff_seconds"],
        max_backoff_seconds=config["max_backoff_seconds"],
    )
    started_at_utc = OG._iso_utc(OG.utc_now())

    server = ThreadingHTTPServer((config["bind"], config["port"]), _make_handler(gate, started_at_utc))
    http_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    http_thread.start()
    LOG.info(
        "HTTP API listening on %s:%s (markets=%s)", config["bind"], config["port"], ",".join(config["markets"]),
    )

    try:
        asyncio.run(_run_async(gate, config["markets"]))
    finally:
        server.shutdown()
        server.server_close()
        http_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
