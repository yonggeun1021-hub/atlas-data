#!/usr/bin/env python3
"""Standalone 24/7 Upbit public-market realtime observation service.

Entry point for the ``upbit-realtime-observation`` Docker service. Connects
to Upbit's **public** market-data WebSocket
(``wss://api.upbit.com/websocket/v1``), discovers current KRW markets through
the public market-list REST endpoint, streams ``ticker`` for that full set
and ``orderbook`` for a configured deep tier, and keeps the latest state in
memory via ``observation_gate.ObservationGate`` (the pure,
independently-unit-tested state machine in this same directory), and serves
it over a minimal local read-only HTTP API (``/health``, ``/ready``,
``/snapshot``).

No API key or secret is used or needed -- Upbit's public market-data
WebSocket requires no authentication. This file never imports, builds, or
sends anything referencing ``myOrder``/``myAsset`` (Upbit's private/order
channels) and never calls any order/cancel/withdrawal endpoint of any kind.
Subscription-message construction is isolated in
``observation_gate.build_subscription_message`` and requests only the two
channels this service actually consumes (``ticker`` and ``orderbook``).

Public candle REST reads are paced and use only completed intervals to build
reference-only 15m/1h/4h/1d trend and relative-strength facts. This process
holds state in memory only. It never writes to ``atlas-data``'s
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
import base64
import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

import observation_gate as OG  # noqa: E402
import public_market_intelligence as PMI  # noqa: E402

LOG = logging.getLogger("atlas.upbit-realtime-observation")

WS_ENDPOINT = "wss://api.upbit.com/websocket/v1"
PUBLIC_REST_BASE = "https://api.upbit.com"
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ServiceConfigError(f"{name}_INVALID:{raw}")


def load_config_from_env() -> dict:
    markets_raw = os.getenv("ATLAS_UPBIT_OBS_MARKETS", ",".join(OG.DEFAULT_MARKETS))
    baseline_markets = OG.parse_market_list(markets_raw)
    deep_raw = os.getenv("ATLAS_UPBIT_OBS_DEEP_MARKETS", markets_raw)
    deep_markets = OG.parse_market_list(deep_raw)
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
    portal_push_url = os.getenv("ATLAS_PORTAL_PUSH_URL", "").strip() or None
    signing_key_path_raw = os.getenv("ATLAS_SIGNING_KEY_PATH", "").strip() or None
    site_bypass_token = os.getenv("ATLAS_SITE_BYPASS_TOKEN", "").strip() or None
    if bool(portal_push_url) != bool(signing_key_path_raw):
        raise ServiceConfigError("ATLAS_PORTAL_PUSH_URL_AND_SIGNING_KEY_PATH_REQUIRED_TOGETHER")
    if portal_push_url:
        parsed = urlparse(portal_push_url)
        if parsed.scheme != "https" or parsed.path != "/api/internal/upbit-realtime-observation/snapshot":
            raise ServiceConfigError("ATLAS_PORTAL_PUSH_URL_INVALID")
        if site_bypass_token and parsed.hostname != "atlas.ddcloud.co.kr":
            raise ServiceConfigError("ATLAS_SITE_BYPASS_TOKEN_HOST_INVALID")
    return {
        "markets": baseline_markets,
        "deep_markets": deep_markets,
        "discover_all_krw": _env_bool("ATLAS_UPBIT_OBS_DISCOVER_ALL_KRW", True),
        "public_rest_base": PUBLIC_REST_BASE,
        "candle_intelligence_enabled": _env_bool("ATLAS_UPBIT_OBS_CANDLE_INTELLIGENCE_ENABLED", True),
        "candle_refresh_seconds": max(_env_int("ATLAS_UPBIT_OBS_CANDLE_REFRESH_SECONDS", 1800), 300),
        "public_rest_min_interval_seconds": max(_env_float("ATLAS_UPBIT_OBS_PUBLIC_REST_MIN_INTERVAL_SECONDS", 0.14), 0.11),
        "bind": bind,
        "port": _env_int("ATLAS_UPBIT_OBS_PORT", 8792),
        "base_backoff_seconds": _env_float("ATLAS_UPBIT_OBS_BASE_BACKOFF_SECONDS", 1.0),
        "max_backoff_seconds": _env_float("ATLAS_UPBIT_OBS_MAX_BACKOFF_SECONDS", 30.0),
        "max_staleness_seconds_by_kind": {
            "ticker": _env_int("ATLAS_UPBIT_OBS_TICKER_MAX_STALENESS_SECONDS", 30),
            "orderbook": _env_int("ATLAS_UPBIT_OBS_ORDERBOOK_MAX_STALENESS_SECONDS", 15),
        },
        "portal_push_url": portal_push_url,
        "signing_key_path": Path(signing_key_path_raw) if signing_key_path_raw else None,
        "site_bypass_token": site_bypass_token,
        "push_interval_seconds": min(max(_env_int("ATLAS_PUSH_INTERVAL_SECONDS", 10), 3), 60),
    }


class PortalPushState:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.last_push_at = None
        self.last_push_error = None


def _load_signing_key(path: Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    value = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(value, Ed25519PrivateKey):
        raise ServiceConfigError("ATLAS_SIGNING_KEY_MUST_BE_ED25519")
    return value


def _push_snapshot(url: str, private_key, site_bypass_token: str | None, snapshot: dict) -> None:
    body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sent_at = snapshot["generated_at_utc"]
    signature = private_key.sign(sent_at.encode("utf-8") + b"\n" + body)
    headers = {
        "content-type": "application/json",
        "x-atlas-key-id": "atlas-server-1",
        "x-atlas-sent-at": sent_at,
        "x-atlas-signature": base64.b64encode(signature).decode("ascii"),
    }
    if site_bypass_token:
        headers["OAI-Sites-Authorization"] = f"Bearer {site_bypass_token}"
    request = Request(url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=15) as response:
        if response.status != 202:
            raise RuntimeError(f"PORTAL_PUSH_HTTP_{response.status}")


async def _push_forever(gate: "OG.ObservationGate", config: dict, state: PortalPushState, stop_event: "asyncio.Event") -> None:
    if not config["portal_push_url"]:
        return
    private_key = _load_signing_key(config["signing_key_path"])
    while not stop_event.is_set():
        snapshot = gate.status_snapshot()
        try:
            await asyncio.to_thread(
                _push_snapshot, config["portal_push_url"], private_key,
                config["site_bypass_token"], snapshot,
            )
            state.last_push_at = snapshot["generated_at_utc"]
            state.last_push_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.last_push_error = f"{type(exc).__name__}:{exc}"
            LOG.warning("portal snapshot push failed: %s", state.last_push_error)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=config["push_interval_seconds"])
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# WebSocket I/O -- the only place this file touches a real socket
# ---------------------------------------------------------------------------

async def _stream_forever(
    gate: "OG.ObservationGate", markets: list, orderbook_markets: list, stop_event: "asyncio.Event",
) -> None:
    """Runs until ``stop_event`` is set, reconnecting forever (see
    ``observation_gate.PersistentConnectionState`` for why this never gives
    up permanently the way P9-06's bounded-run capture does)."""
    import websockets  # lazy import -- see module docstring

    ticket = f"atlas-upbit-realtime-observation-{OG.utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    subscribe_message = OG.build_subscription_message(
        markets, orderbook_markets=orderbook_markets, ticket=ticket,
    )

    while not stop_event.is_set():
        try:
            async with websockets.connect(WS_ENDPOINT, ping_interval=20, open_timeout=15) as ws:
                gate.on_connected(OG.utc_now())
                LOG.info(
                    "connected to %s (ticker=%d, orderbook=%d)",
                    WS_ENDPOINT, len(markets), len(orderbook_markets),
                )
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


def _public_json(base_url: str, path: str, params: dict | None = None):
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{base_url}{path}{query}",
        headers={"accept": "application/json", "user-agent": "AtlasPublicObservation/2"},
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"UPBIT_PUBLIC_REST_HTTP_{response.status}")
        return json.loads(response.read().decode("utf-8"))


def _resolve_market_coverage(config: dict) -> tuple[list[str], list[str], dict, str]:
    if not config["discover_all_krw"]:
        return config["markets"], config["deep_markets"], {}, "CONFIGURED_BASELINE"
    try:
        payload = _public_json(config["public_rest_base"], "/v1/market/all", {"is_details": "true"})
        markets, metadata = PMI.parse_krw_market_catalog(payload)
        deep = [market for market in config["deep_markets"] if market in markets]
        if not deep:
            deep = [market for market in OG.DEFAULT_MARKETS if market in markets]
        return markets, deep, metadata, "UPBIT_PUBLIC_REST_STARTUP_DISCOVERY"
    except Exception as exc:
        LOG.warning("full KRW discovery unavailable; using configured baseline (%s:%s)", type(exc).__name__, exc)
        return config["markets"], config["deep_markets"], {}, "CONFIGURED_FALLBACK_AFTER_DISCOVERY_ERROR"


def _fetch_candles(base_url: str, market: str, timeframe: str, count: int):
    if timeframe == "1d":
        path = "/v1/candles/days"
    else:
        unit = {"15m": 15, "1h": 60, "4h": 240}[timeframe]
        path = f"/v1/candles/minutes/{unit}"
    return _public_json(base_url, path, {"market": market, "count": count})


async def _refresh_candle_intelligence_forever(
    gate: "OG.ObservationGate", markets: list[str], config: dict, stop_event: "asyncio.Event",
) -> None:
    if not config["candle_intelligence_enabled"]:
        return
    request_counts = {"15m": 3, "1h": 3, "4h": 25, "1d": 61}
    while not stop_event.is_set():
        cycle_started = OG.utc_now()
        rows: dict[str, dict] = {}
        failures = 0
        for market in markets:
            if stop_event.is_set():
                return
            payloads: dict[str, object] = {}
            try:
                for timeframe, count in request_counts.items():
                    payloads[timeframe] = await asyncio.to_thread(
                        _fetch_candles, config["public_rest_base"], market, timeframe, count,
                    )
                    await asyncio.sleep(config["public_rest_min_interval_seconds"])
                rows[market] = PMI.analyze_finalized_candles(
                    market,
                    day_rows=payloads["1d"], h4_rows=payloads["4h"],
                    h1_rows=payloads["1h"], m15_rows=payloads["15m"],
                    now=OG.utc_now(),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                rows[market] = {
                    "market": market,
                    "status": "UNKNOWN",
                    "calculated_at_utc": OG._iso_utc(OG.utc_now()),
                    "reference_only": True,
                    "finalized_candles": {},
                    "trend": {"status": "UNKNOWN", "reason": f"PUBLIC_CANDLE_REFRESH_ERROR:{type(exc).__name__}"},
                    "relative_strength": {"status": "UNKNOWN", "reason": "PUBLIC_CANDLE_REFRESH_ERROR"},
                }
        if rows:
            gate.set_candle_intelligence_batch(PMI.complete_cross_section_relative_strength(rows))
        elapsed = (OG.utc_now() - cycle_started).total_seconds()
        LOG.info(
            "public finalized-candle cycle complete (markets=%d failures=%d elapsed=%.1fs)",
            len(rows), failures, elapsed,
        )
        wait_seconds = max(5.0, config["candle_refresh_seconds"] - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            pass


async def _run_async(
    gate: "OG.ObservationGate", markets: list, orderbook_markets: list,
    config: dict, push_state: PortalPushState,
) -> None:
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
        await asyncio.gather(
            _stream_forever(gate, markets, orderbook_markets, stop_event),
            _push_forever(gate, config, push_state, stop_event),
            _refresh_candle_intelligence_forever(gate, markets, config, stop_event),
        )
    finally:
        for sig in installed:
            loop.remove_signal_handler(sig)


# ---------------------------------------------------------------------------
# HTTP API -- local, read-only, no write methods
# ---------------------------------------------------------------------------

def _make_handler(gate: "OG.ObservationGate", started_at_utc: str, push_state: PortalPushState | None = None):
    push_state = push_state or PortalPushState(False)
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
                    "portalPushEnabled": push_state.enabled,
                    "lastPushAt": push_state.last_push_at,
                    "lastPushError": push_state.last_push_error,
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

    markets, orderbook_markets, market_metadata, market_source = _resolve_market_coverage(config)
    # Full-market payloads are intentionally pushed no more frequently than
    # every ten seconds, even if an old .env still contains the original 5s
    # setting used for the ten-market pilot.
    if len(markets) > 50:
        config["push_interval_seconds"] = max(config["push_interval_seconds"], 10)
    gate = OG.ObservationGate(
        markets=markets,
        orderbook_markets=orderbook_markets,
        market_metadata=market_metadata,
        market_source=market_source,
        max_staleness_seconds_by_kind=config["max_staleness_seconds_by_kind"],
        base_backoff_seconds=config["base_backoff_seconds"],
        max_backoff_seconds=config["max_backoff_seconds"],
    )
    started_at_utc = OG._iso_utc(OG.utc_now())
    push_state = PortalPushState(config["portal_push_url"] is not None)

    server = ThreadingHTTPServer((config["bind"], config["port"]), _make_handler(gate, started_at_utc, push_state))
    http_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    http_thread.start()
    LOG.info(
        "HTTP API listening on %s:%s (ticker=%d orderbook=%d source=%s)",
        config["bind"], config["port"], len(markets), len(orderbook_markets), market_source,
    )

    try:
        asyncio.run(_run_async(gate, markets, orderbook_markets, config, push_state))
    finally:
        server.shutdown()
        server.server_close()
        http_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
