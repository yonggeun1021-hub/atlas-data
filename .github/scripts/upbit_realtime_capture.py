#!/usr/bin/env python3
"""P9-06 bounded-duration Upbit public WebSocket capture.

Connects to Upbit's public market-data WebSocket
(``wss://api.upbit.com/websocket/v1``), subscribes to
``ticker``/``trade``/``orderbook``/``candle.{15m,60m,240m}`` for the current
P3-12 eligible-market set, runs for a **bounded duration** (handling
reconnect-with-backoff within that window), writes an append-only evidence
snapshot, and exits cleanly. Public market data only -- no API key/secret,
never ``myOrder``/``myAsset``, never an order/withdrawal/private endpoint.

An explicit ``PUBLIC_TRANSPORT_VALIDATION_ONLY`` mode may instead read the
fixed reference markets in
``config/upbit_public_validation_anchor_contract.json``.  Those observations
are written under a separate evidence root and
are forbidden from entering P3/P5/P8 or any order/decision path.  The mode
exists only to prove the public WebSocket transport and parsers with natural
bytes while the P3-12 eligibility authority remains unratified/empty.

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
import time
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UTC = dt.timezone.utc
WS_ENDPOINT = "wss://api.upbit.com/websocket/v1"
CANDLE_TIMEFRAMES = ("15m", "1h", "4h")
LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION = "upbit_realtime_latest_public_messages/1"
ELIGIBLE_UNIVERSE_MODE = "P3_ELIGIBLE_UNIVERSE"
PUBLIC_VALIDATION_MODE = "PUBLIC_TRANSPORT_VALIDATION_ONLY"
REST_BACKFILL_MIN_REQUEST_INTERVAL_SECONDS = 0.125


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


def load_validation_anchor_contract(path: Path) -> dict:
    """Load a non-strategic, public-transport-only market anchor set.

    This loader is deliberately local to the I/O wrapper.  The realtime
    decision gate never imports the anchor contract, so reference markets
    cannot silently expand the P3-12 eligible universe.
    """
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealtimeCaptureError(f"VALIDATION_ANCHOR_CONTRACT_UNREADABLE:{exc}") from exc
    if contract.get("contract_version") != "upbit_public_validation_anchor_contract/1":
        raise RealtimeCaptureError("VALIDATION_ANCHOR_CONTRACT_VERSION_INVALID")
    if contract.get("capture_mode") != PUBLIC_VALIDATION_MODE:
        raise RealtimeCaptureError("VALIDATION_ANCHOR_MODE_INVALID")
    markets = contract.get("markets")
    if (
        not isinstance(markets, list)
        or not markets
        or markets != sorted(set(markets))
        or any(not isinstance(market, str) or not market.startswith("KRW-") for market in markets)
    ):
        raise RealtimeCaptureError("VALIDATION_ANCHOR_MARKETS_INVALID")
    required_false = (
        "feeds_tradeable_universe",
        "feeds_candidate_promotion",
        "feeds_buy_decision",
        "feeds_briefing_decision",
        "entry_eligibility_authorized",
        "action_generation_authorized",
        "order_authorized",
        "production_authorized",
        "trading_authorized",
        "private_channel_subscribed",
        "order_or_withdrawal_endpoints_called",
    )
    if any(contract.get(field) is not False for field in required_false):
        raise RealtimeCaptureError("VALIDATION_ANCHOR_AUTHORITY_INVALID")
    if contract.get("auth_required") is not False:
        raise RealtimeCaptureError("VALIDATION_ANCHOR_AUTH_INVALID")
    return contract


def validate_evidence_root(capture_mode: str, evidence_root: Path) -> None:
    """Prevent validation-only bytes and decision-source bytes from mixing."""
    is_validation_root = Path(evidence_root).name == "realtime_validation"
    if capture_mode == PUBLIC_VALIDATION_MODE and not is_validation_root:
        raise RealtimeCaptureError("VALIDATION_EVIDENCE_ROOT_NOT_ISOLATED")
    if capture_mode == ELIGIBLE_UNIVERSE_MODE and is_validation_root:
        raise RealtimeCaptureError("ELIGIBLE_UNIVERSE_EVIDENCE_ROOT_INVALID")


def build_gate(markets: list, contract: dict) -> "GATE.RealtimeGate":
    return GATE.RealtimeGate(
        markets=markets,
        max_reconnect_attempts=contract["reconnect_default_max_attempts"],
        base_backoff_seconds=contract["reconnect_default_base_backoff_seconds"],
        max_backoff_seconds=contract["reconnect_default_max_backoff_seconds"],
        max_staleness_seconds_by_kind=contract["default_max_staleness_seconds_by_kind"],
        connection_gap_min_seconds=contract["connection_gap_min_seconds_for_backfill"],
        provider_gap_threshold_seconds_by_kind=contract["provider_gap_threshold_seconds_by_kind"],
        max_backfill_window_seconds=contract["rest_backfill_max_window_seconds"],
        max_backfill_rows=contract["rest_backfill_max_rows"],
    )


def _public_rest_get(url: str, timeout_seconds: int) -> bytes:
    if not url.startswith("https://api.upbit.com/v1/") or any(
        fragment in url for fragment in ("/orders", "/withdraw", "/deposit", "/accounts")
    ):
        raise RealtimeCaptureError("REST_BACKFILL_ENDPOINT_FORBIDDEN")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Project-Atlas-upbit-realtime-recovery/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read()
    if not body:
        raise RealtimeCaptureError("REST_BACKFILL_EMPTY_RESPONSE")
    return body


def plan_public_rest_backfill(gaps: list, markets: list, contract: dict) -> list[dict]:
    """Create a deterministic, bounded public-GET request plan.

    A gap beyond the ratified operational bound remains pending and emits no
    request.  Connection-wide gaps expand only across the already-scoped
    P3 markets; no observation-pool market is introduced here.
    """
    max_rows = contract["rest_backfill_max_rows"]
    max_window = contract["rest_backfill_max_window_seconds"]
    max_requests = contract["rest_backfill_max_requests_per_run"]
    planned = {}
    for gap in gaps:
        if not isinstance(gap, dict) or gap.get("status") != "PENDING" or gap.get("bounded") is not True:
            continue
        start = GATE._parse_utc(gap.get("from"), "REST_PLAN_GAP_FROM_INVALID")
        end = GATE._parse_utc(gap.get("to"), "REST_PLAN_GAP_TO_INVALID")
        duration = int((end - start).total_seconds())
        if duration < 1 or duration > max_window:
            continue
        target_markets = [gap["market"]] if gap.get("market") else sorted(set(markets))
        gap_planned = {}
        for market in target_markets:
            if market not in markets:
                raise RealtimeCaptureError(f"REST_BACKFILL_MARKET_OUT_OF_SCOPE:{market}")
            request_range = {"from": GATE._iso_utc(start), "to": GATE._iso_utc(end)}
            trade_count = max_rows
            trade_query = urllib.parse.urlencode({
                "market": market,
                # Upbit's trades/ticks endpoint accepts a UTC time-of-day,
                # unlike candle endpoints whose `to` is ISO 8601.
                "to": end.strftime("%H:%M:%S"),
                "count": trade_count,
            })
            trade_url = f"{contract['rest_public_trades_endpoint']}?{trade_query}"
            trade_key = ("trade", None, market, request_range["from"], request_range["to"])
            trade_request = {
                "kind": "trade", "timeframe": None, "market": market, "method": "GET",
                "url": trade_url, "count": trade_count, "auth_required": False,
                "request_range": request_range,
            }
            trade_request["request_id"] = GATE.payload_sha256(trade_request)
            trade_request["gap_ids"] = [gap["gap_id"]]
            gap_planned[trade_key] = trade_request
            for timeframe in CANDLE_TIMEFRAMES:
                spec = GATE.finalization.TIMEFRAMES[timeframe]
                count = min(max_rows, max(1, (duration + spec["unit_seconds"] - 1) // spec["unit_seconds"] + 2))
                endpoint = contract["rest_public_candles_minutes_endpoint_template"].format(
                    UNIT=spec["upbit_unit"],
                )
                query = urllib.parse.urlencode({
                    "market": market,
                    "to": GATE._iso_utc(end),
                    "count": count,
                })
                request = {
                    "kind": "candle", "timeframe": timeframe, "market": market, "method": "GET",
                    "url": f"{endpoint}?{query}", "count": count, "auth_required": False,
                    "request_range": request_range,
                }
                request["request_id"] = GATE.payload_sha256(request)
                request["gap_ids"] = [gap["gap_id"]]
                key = ("candle", timeframe, market, request_range["from"], request_range["to"])
                gap_planned[key] = request
        prospective = set(planned).union(gap_planned)
        if len(prospective) > max_requests:
            continue
        for key, request in gap_planned.items():
            if key in planned:
                request["gap_ids"] = sorted(set(planned[key]["gap_ids"] + request["gap_ids"]))
            planned[key] = request
    return [planned[key] for key in sorted(planned)]


def execute_public_rest_backfill(
    requests: list, *, gap_ids: list, fetcher=_public_rest_get, clock=utc_now,
    evidence_class: str = GATE.NATURAL_AUTOMATED, timeout_seconds: int = 15,
    sleeper=time.sleep,
    min_request_interval_seconds: float = REST_BACKFILL_MIN_REQUEST_INTERVAL_SECONDS,
) -> dict:
    responses = []
    last_received_at = None
    for index, request in enumerate(requests):
        if index:
            sleeper(min_request_interval_seconds)
        requested_at = clock().astimezone(UTC)
        raw = fetcher(request["url"], timeout_seconds)
        received_at = clock().astimezone(UTC)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RealtimeCaptureError(f"REST_BACKFILL_JSON_INVALID:{exc}") from exc
        if not isinstance(payload, list):
            raise RealtimeCaptureError("REST_BACKFILL_PAYLOAD_NOT_LIST")
        if len(payload) > request["count"]:
            raise RealtimeCaptureError("REST_BACKFILL_RETURNED_COUNT_EXCEEDED")
        range_start = GATE._parse_utc(request["request_range"]["from"], "REST_BACKFILL_FROM_INVALID")
        range_end = GATE._parse_utc(request["request_range"]["to"], "REST_BACKFILL_TO_INVALID")
        provider_times = GATE._returned_provider_times(request, payload)
        payload = [
            row for row, provider_at in zip(payload, provider_times)
            if range_start <= provider_at <= range_end
        ]
        responses.append(GATE.backfill_response_record(
            request=request, payload=payload, requested_at=requested_at, received_at=received_at,
        ))
        last_received_at = received_at
    generated_at = last_received_at or clock().astimezone(UTC)
    return GATE.build_backfill_receipt(
        gap_ids=gap_ids, responses=responses, evidence_class=evidence_class, generated_at=generated_at,
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


def public_transport_validation_summary(markets: list, latest_public_messages: dict) -> dict:
    """Report exact public-channel observation coverage, with no policy use."""
    expected_keys = []
    for market in markets:
        expected_keys.extend(
            [
                f"ticker|-|{market}",
                f"trade|-|{market}",
                f"orderbook|-|{market}",
                *(f"candle|{timeframe}|{market}" for timeframe in CANDLE_TIMEFRAMES),
            ]
        )
    observed_keys = sorted(set(latest_public_messages).intersection(expected_keys))
    missing_keys = sorted(set(expected_keys).difference(observed_keys))
    return {
        "status": "COMPLETE" if not missing_keys else "INCOMPLETE",
        "expected_public_channel_keys": sorted(expected_keys),
        "observed_public_channel_keys": observed_keys,
        "missing_public_channel_keys": missing_keys,
        "decision_eligible": False,
        "entry_eligibility_authorized": False,
        "action_generation_authorized": False,
        "order_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }


async def _connect_and_stream(
    gate: "GATE.RealtimeGate", markets: list, *, contract: dict,
    deadline: dt.datetime, stop_event: "asyncio.Event",
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
    recovery_receipts: list = []
    recovery_errors: list = []
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
    pending = gate.pending_gap_windows()
    requests = plan_public_rest_backfill(pending, markets, contract)
    if requests:
        try:
            receipt = await asyncio.to_thread(
                execute_public_rest_backfill,
                requests,
                gap_ids=sorted({gap_id for request in requests for gap_id in request["gap_ids"]}),
                timeout_seconds=contract["rest_backfill_timeout_seconds"],
            )
            gate.apply_backfill_receipt(receipt, revalidated_at=utc_now())
            recovery_receipts.append(receipt)
        except (RealtimeCaptureError, GATE.RealtimeGateError, OSError) as exc:
            recovery_errors.append({"code": "PUBLIC_REST_BACKFILL_FAILED", "detail": str(exc)})
    elif pending:
        recovery_errors.append({
            "code": "PENDING_GAP_OUTSIDE_BOUNDED_BACKFILL",
            "gap_ids": [row["gap_id"] for row in pending],
        })
    return {
        "message_log": message_log,
        "latest_public_messages_schema_version": LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION,
        "latest_public_messages": latest_public_messages,
        "public_rest_backfill_receipts": recovery_receipts,
        "public_rest_backfill_errors": recovery_errors,
    }


async def run_capture_async(
    markets: list, contract: dict, *, duration_seconds: float,
    capture_mode: str = ELIGIBLE_UNIVERSE_MODE,
) -> dict:
    if capture_mode not in (ELIGIBLE_UNIVERSE_MODE, PUBLIC_VALIDATION_MODE):
        raise RealtimeCaptureError("CAPTURE_MODE_INVALID")
    if capture_mode == PUBLIC_VALIDATION_MODE and not markets:
        raise RealtimeCaptureError("VALIDATION_ANCHOR_MARKETS_EMPTY")
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
            streamed = await _connect_and_stream(
                gate, markets, contract=contract, deadline=deadline, stop_event=stop_event,
            )
        else:
            streamed = {
                "message_log": [],
                "latest_public_messages_schema_version": LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION,
                "latest_public_messages": {},
                "public_rest_backfill_receipts": [],
                "public_rest_backfill_errors": [],
            }
    finally:
        for sig in installed_handlers:
            loop.remove_signal_handler(sig)

    ended_at = utc_now()
    status = gate.status_snapshot(ended_at)
    quote_rows = []
    for item in streamed["latest_public_messages"].values():
        if item.get("kind") != "ticker":
            continue
        parsed = GATE.parse_message(item["raw"])
        received_at = dt.datetime.strptime(
            item["received_at"], "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=UTC)
        quote_rows.append(GATE.quote_row_from_ticker(parsed, received_at=received_at))
    if quote_rows:
        try:
            freshness_policy_result = GATE.evaluate_with_ratified_freshness_policy(
                quote_rows,
                observed_at=ended_at,
                batch_id=f"P9_06_{ended_at.strftime('%Y%m%dT%H%M%SZ')}",
                contract=contract,
            )
        except GATE.RealtimeGateError as exc:
            freshness_policy_result = {"status": GATE.UNKNOWN, "reason": str(exc), "result": None}
    else:
        freshness_policy_result = {
            "status": GATE.UNKNOWN,
            "reason": "P9_06_RATIFIED_POLICY_INPUT_NO_TICKER",
            "result": None,
        }
    freshness_rows = (freshness_policy_result.get("result") or {}).get("results", [])
    all_quotes_fresh = bool(freshness_rows) and all(
        row.get("freshness_status") == GATE.FRESH for row in freshness_rows
    )
    action_gate_status = GATE.FRESH if (
        all_quotes_fresh
        and not status["pending_gap_windows"]
        and status["finalized_candle_ledger_count"] > 0
    ) else GATE.UNKNOWN
    run_record = {
        "evidence_class": GATE.NATURAL_AUTOMATED,
        "capture_mode": capture_mode,
        "started_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": ended_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_duration_seconds": duration_seconds,
        "markets": markets,
        "message_log": streamed["message_log"],
        "latest_public_messages_schema_version": streamed[
            "latest_public_messages_schema_version"
        ],
        "latest_public_messages": streamed["latest_public_messages"],
        "public_rest_backfill_receipts": streamed.get("public_rest_backfill_receipts", []),
        "public_rest_backfill_errors": streamed.get("public_rest_backfill_errors", []),
        "ratified_freshness_policy": {
            "path": contract["ratified_freshness_policy_path"],
            "packet_sha256": contract["ratified_freshness_policy_sha256"],
            "consumer_result": freshness_policy_result,
        },
        "finalized_only_action_gate": {
            "status": action_gate_status,
            "finalized_candle_only": True,
            "finalized_candle_ledger_count": status["finalized_candle_ledger_count"],
            "in_progress_candle_count": status["in_progress_candle_count"],
            "pending_gap_count": len(status["pending_gap_windows"]),
            "decision_eligible": False,
            "action_generation_authorized": False,
            "order_authorized": False,
        },
        "status": status,
        "candle_ledger": {
            f"{market}|{timeframe}": sorted(t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in open_times)
            for (market, timeframe), open_times in (
                (key, gate.candles.finalized_open_times(*key)) for key in gate.candles.committed
            )
        },
    }
    if capture_mode == PUBLIC_VALIDATION_MODE:
        run_record["transport_validation"] = public_transport_validation_summary(
            markets,
            streamed["latest_public_messages"],
        )
    return run_record


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
    market_source = parser.add_mutually_exclusive_group()
    market_source.add_argument("--universe-packet", type=Path, help="P3-12 classification packet.json")
    market_source.add_argument(
        "--validation-anchor-contract",
        type=Path,
        help="PUBLIC_TRANSPORT_VALIDATION_ONLY reference-market contract",
    )
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--snapshot-date", type=dt.date.fromisoformat, default=None)
    args = parser.parse_args(argv)

    contract = GATE.load_contract()
    duration = args.duration_seconds or contract["bounded_run_default_duration_seconds"]
    if args.validation_anchor_contract is not None:
        anchor_contract = load_validation_anchor_contract(args.validation_anchor_contract)
        capture_mode = PUBLIC_VALIDATION_MODE
        markets = anchor_contract["markets"]
    else:
        capture_mode = ELIGIBLE_UNIVERSE_MODE
        markets = GATE.eligible_markets_from_universe_packet(args.universe_packet)
    validate_evidence_root(capture_mode, args.evidence_root)
    snapshot_date = args.snapshot_date or utc_now().date()

    run_record = asyncio.run(
        run_capture_async(
            markets,
            contract,
            duration_seconds=duration,
            capture_mode=capture_mode,
        )
    )
    target = write_evidence_snapshot(args.evidence_root, snapshot_date, run_record)
    print(json.dumps({
        "path": str(target),
        "capture_mode": capture_mode,
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
