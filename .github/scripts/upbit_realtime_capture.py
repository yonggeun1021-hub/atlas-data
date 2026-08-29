#!/usr/bin/env python3
"""P9-06 bounded-duration Upbit public WebSocket capture.

Connects to Upbit's public market-data WebSocket
(``wss://api.upbit.com/websocket/v1``), subscribes to
``ticker``/``trade``/``orderbook``/``candle.{15m,60m,240m}`` for the current
P3-12 eligible-market set, runs for a **bounded duration** (handling
reconnect-with-backoff within that window), writes an append-only evidence
snapshot, and exits cleanly. Public market data only -- no API key/secret,
never ``myOrder``/``myAsset``, never an order/withdrawal/private endpoint.

Architecture note -- why bounded-run, not a persistent daemon: this repo's
existing automation is GitHub Actions cron jobs that run, capture, commit,
and exit; there is no long-running-process infrastructure here for a
genuinely persistent 24/7 WebSocket connection. All the reconnect/gap/
dedup/freshness *logic* lives in ``realtime/upbit_realtime_gate.py`` as a
deployment-agnostic, pure/testable state machine -- this script is just the
thin async I/O wrapper that drives it for one bounded window. The exact
same ``realtime/upbit_realtime_gate.py`` classes would work unchanged
behind a genuinely persistent daemon on separate infrastructure later; that
deployment question is explicitly out of scope for this PR.

The ``websockets`` package (see ``requirements.txt``) is imported **lazily**
inside ``_connect_and_stream``/``run_capture`` -- never at module import
time -- so this script's pure helpers (subscription building, evidence
writing, CLI parsing) can be imported and exercised without the package
installed; only an actual bounded run needs it.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UTC = dt.timezone.utc
WS_ENDPOINT = "wss://api.upbit.com/websocket/v1"
CANDLE_TIMEFRAMES = ("15m", "1h", "4h")
LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION = "upbit_realtime_latest_public_messages/1"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GATE = _load_module("upbit_realtime_gate_for_capture", "realtime/upbit_realtime_gate.py")


class RealtimeCaptureError(RuntimeError):
    """Fail-closed P9-06 capture/publication error."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_gate(markets: list, contract: dict) -> "GATE.RealtimeGate":
    return GATE.RealtimeGate(
        markets=markets,
        max_reconnect_attempts=contract["reconnect_default_max_attempts"],
        base_backoff_seconds=contract["reconnect_default_base_backoff_seconds"],
        max_backoff_seconds=contract["reconnect_default_max_backoff_seconds"],
        max_staleness_seconds_by_kind=contract["default_max_staleness_seconds_by_kind"],
        connection_gap_min_seconds=contract["connection_gap_min_seconds_for_backfill"],
    )


def retain_latest_public_message(
    latest: dict, *, raw: dict, result: dict, received_at: dt.datetime,
) -> None:
    """Retain one exact latest accepted public message per market/kind.

    P9-06 previously persisted only the gate result (``ACCEPTED`` plus the
    market/kind labels).  That proves transport health but discards the
    public orderbook bytes P10-11 needs for deterministic PAPER fill replay.
    This helper keeps only the latest accepted public message for each
    ``(kind, timeframe, market)`` tuple, avoiding an unbounded raw-message
    archive while preserving the exact hash-bound orderbook/ticker snapshot.
    Rejected, duplicate, and out-of-order messages never replace the retained
    latest value.
    """
    if not isinstance(latest, dict):
        raise RealtimeCaptureError("LATEST_PUBLIC_MESSAGES_INVALID")
    if not isinstance(result, dict) or result.get("action") != "ACCEPTED":
        return
    if not isinstance(received_at, dt.datetime) or received_at.tzinfo is None:
        raise RealtimeCaptureError("LATEST_PUBLIC_MESSAGE_RECEIVED_AT_INVALID")
    parsed = GATE.parse_message(raw)
    if parsed["market"] != result.get("market") or parsed["kind"] != result.get("kind"):
        raise RealtimeCaptureError("LATEST_PUBLIC_MESSAGE_RESULT_MISMATCH")
    timeframe = parsed["timeframe"] or "-"
    key = f"{parsed['kind']}|{timeframe}|{parsed['market']}"
    latest[key] = {
        "kind": parsed["kind"],
        "timeframe": parsed["timeframe"],
        "market": parsed["market"],
        "received_at": received_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "source_sha256": parsed["payload_sha256"],
        "raw": raw,
    }


async def _connect_and_stream(
    gate: "GATE.RealtimeGate", markets: list, *, deadline: dt.datetime, stop_event: "asyncio.Event",
) -> dict:
    """The only function in this script that imports ``websockets`` or
    touches a real socket. Runs until ``deadline`` or ``stop_event`` is set
    (clean kill-switch), reconnecting with the gate's own backoff state
    machine on disconnect, and never looping forever -- once
    ``ConnectionStateMachine.next_attempt()`` returns ``FAIL_CLOSED`` this
    coroutine returns without retrying again.
    """
    import websockets  # lazy import -- see module docstring

    message_log: list = []
    latest_public_messages: dict = {}
    ticket = f"atlas-p9-06-{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    subscribe_message = GATE.build_subscription_message(
        markets, ticket=ticket, candle_timeframes=CANDLE_TIMEFRAMES,
    )

    while utc_now() < deadline and not stop_event.is_set() and gate.connection.state != GATE.WAIT_MAX_RETRIES_EXCEEDED:
        try:
            async with websockets.connect(WS_ENDPOINT, ping_interval=20, open_timeout=15) as ws:
                gate.on_connected(utc_now())
                await ws.send(json.dumps(subscribe_message))
                while utc_now() < deadline and not stop_event.is_set():
                    remaining = (deadline - utc_now()).total_seconds()
                    if remaining <= 0:
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5.0))
                    except asyncio.TimeoutError:
                        continue
                    received_at = utc_now()
                    try:
                        parsed_raw = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        gate.counts["rejected_malformed"] += 1
                        message_log.append({
                            "received_at": received_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                            "result": {"action": "REJECTED_MALFORMED", "reason": "JSON_DECODE_FAILED"},
                        })
                        continue
                    result = gate.handle_message(parsed_raw, received_at=received_at)
                    retain_latest_public_message(
                        latest_public_messages,
                        raw=parsed_raw,
                        result=result,
                        received_at=received_at,
                    )
                    message_log.append({
                        "received_at": received_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        "result": result,
                    })
        except (websockets.exceptions.WebSocketException, OSError, asyncio.TimeoutError) as exc:
            gate.on_disconnect(utc_now(), f"{type(exc).__name__}:{exc}")
            attempt = gate.next_reconnect_attempt()
            if attempt["action"] == "FAIL_CLOSED":
                break
            if stop_event.is_set() or utc_now() >= deadline:
                break
            await asyncio.sleep(min(attempt["backoff_seconds"], max((deadline - utc_now()).total_seconds(), 0)))
    return {
        "message_log": message_log,
        "latest_public_messages_schema_version": LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION,
        "latest_public_messages": latest_public_messages,
    }


async def run_capture_async(
    markets: list, contract: dict, *, duration_seconds: float,
) -> dict:
    gate = build_gate(markets, contract)
    stop_event = asyncio.Event()

    def _request_stop(*_args) -> None:
        stop_event.set()
        gate.request_stop()

    loop = asyncio.get_event_loop()
    installed_handlers = []
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
            installed_handlers.append(sig)
        except (NotImplementedError, RuntimeError):
            pass  # platform without signal-handler support in this loop -- degrade gracefully, not fatal

    start = utc_now()
    deadline = start + dt.timedelta(seconds=duration_seconds)
    try:
        if markets:
            streamed = await _connect_and_stream(gate, markets, deadline=deadline, stop_event=stop_event)
        else:
            streamed = {
                "message_log": [],
                "latest_public_messages_schema_version": LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION,
                "latest_public_messages": {},
            }
    finally:
        for sig in installed_handlers:
            loop.remove_signal_handler(sig)

    status = gate.status_snapshot(utc_now())
    return {
        "started_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_duration_seconds": duration_seconds,
        "markets": markets,
        "message_log": streamed["message_log"],
        "latest_public_messages_schema_version": streamed[
            "latest_public_messages_schema_version"
        ],
        "latest_public_messages": streamed["latest_public_messages"],
        "status": status,
        "candle_ledger": {
            f"{market}|{timeframe}": sorted(t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in open_times)
            for (market, timeframe), open_times in (
                (key, gate.candles.finalized_open_times(*key)) for key in gate.candles.committed
            )
        },
    }


def write_evidence_snapshot(evidence_root: Path, snapshot_date: dt.date, run_record: dict) -> Path:
    """Append-only, atomic, same manifest/checksum discipline as
    ``upbit_microstructure_capture.py::capture_snapshot``: write to a temp
    dir, build a manifest, move into place only on success. Refuses to
    overwrite an already-committed date -- multiple bounded runs on the same
    UTC date append a numbered run file inside that date's directory instead
    (each individually append-only-checked), never overwriting a prior run.
    """
    target_dir = Path(evidence_root) / snapshot_date.isoformat()
    target_dir.mkdir(parents=True, exist_ok=True)
    existing_runs = sorted(target_dir.glob("run_*.json"))
    run_index = len(existing_runs) + 1
    target_file = target_dir / f"run_{run_index:03d}.json"
    if target_file.exists():
        raise RealtimeCaptureError(f"APPEND_ONLY_VIOLATION:{target_file}")
    fd, temp_path = tempfile.mkstemp(prefix=f".{target_file.name}.", dir=str(target_dir))
    payload = {
        "schema_version": "upbit_realtime_capture_run/1",
        "transform_version": "upbit_realtime_gate/1",
        "auth_required": False,
        "order_or_withdrawal_endpoints_called": False,
        "private_channel_subscribed": False,
        "run": run_record,
    }
    payload["source_sha256"] = payload_sha256(run_record)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target_file)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return target_file


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--universe-packet", type=Path, help="P3-12 classification packet.json")
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--snapshot-date", type=dt.date.fromisoformat, default=None)
    args = parser.parse_args(argv)

    contract = GATE.load_contract()
    duration = args.duration_seconds or contract["bounded_run_default_duration_seconds"]
    markets = GATE.eligible_markets_from_universe_packet(args.universe_packet)
    snapshot_date = args.snapshot_date or utc_now().date()

    run_record = asyncio.run(run_capture_async(markets, contract, duration_seconds=duration))
    target = write_evidence_snapshot(args.evidence_root, snapshot_date, run_record)
    print(json.dumps({
        "path": str(target),
        "market_count": len(markets),
        "overall_status": run_record["status"]["overall_status"],
        "accepted": run_record["status"]["counts"]["accepted"],
        "reconnect_count": run_record["status"]["reconnect_count"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RealtimeCaptureError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
