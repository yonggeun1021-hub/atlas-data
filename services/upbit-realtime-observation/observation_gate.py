#!/usr/bin/env python3
"""Persistent-daemon Upbit public-market observation gate.

This is the pure, fully-testable state machine behind the standalone 24/7
``upbit-realtime-observation`` service (``services/upbit-realtime-observation/``).
It has **no socket code and no ``websockets``/``asyncio`` import anywhere in
this file** -- exactly the same discipline ``realtime/upbit_realtime_gate.py``
(P9-06) already established, so it is unit-testable with mocked messages and
no live connection. ``service.py`` in this same directory is the thin async
I/O wrapper that actually opens the WebSocket and runs this gate forever.

## Scope and boundary (read before changing this file)

* Public Upbit WebSocket channels only: ``ticker`` and ``orderbook``. This
  service deliberately does **not** track ``trade`` or ``candle.*`` --
  ``ticker`` already carries ``trade_price``, ``trade_volume``,
  ``acc_trade_volume_24h``, ``change_rate``/``signed_change_rate`` (the
  "latest price / change % / volume" the CIO task asked for), and
  ``orderbook`` carries best bid/ask. ``myOrder``/``myAsset`` and any
  order/withdrawal/private REST endpoint are never referenced anywhere in
  this file.
* This module never reads, imports, or references
  ``universe/upbit_tradeable_universe.py`` (P3-12) or any candidate/PAPER
  eligibility state. The market list is a fixed, independently-configured
  observation set (see ``DEFAULT_MARKETS`` / ``parse_market_list``), never
  derived from ``TRADEABLE_UNIVERSE``/``PAPER_ELIGIBLE``, and this module
  never writes to any of those states. See ``_OBSERVATION_AUTHORITY`` below
  -- every authority/promotion flag is hardcoded ``False``.
* This module never writes to ``atlas-data``'s own ``evidence/`` or
  ``data/`` directories. It holds the latest observation only in memory
  (``ObservationGate._latest``) and exposes it through ``status_snapshot``
  for ``service.py``'s HTTP layer to serve. It is not a capture/evidence
  pipeline and must never be confused with P3-12/P4-07/P9-06's evidence
  jobs, which remain the ``atlas-data`` GitHub Actions cron jobs unchanged
  by this file.

## Reuse vs. adapt -- what is imported unchanged from P9-06, and why two
pieces are deliberately *not* reused verbatim

Imported and used **unchanged** from ``realtime/upbit_realtime_gate.py``
(``GATE`` below): ``parse_message`` (fail-closed structural validation +
``PRIVATE_WS_TYPES_FORBIDDEN`` enforcement), ``build_subscription_message``
(the exact tested public-only subscribe payload, including its
private-channel-forbidden guard), ``MARKET_CODE_RE``, ``PUBLIC_MESSAGE_TYPES``,
``PRIVATE_WS_TYPES_FORBIDDEN``, ``SequenceTracker`` (out-of-order detection --
its internal state is bounded by subscribed-market count, not message count,
so it is safe to reuse verbatim for a long-running process), and
``next_backoff_seconds`` (the pure exponential-backoff-with-cap formula).

Two pieces are **adapted, not reused verbatim**, because P9-06 was built for
a ~240-second bounded cron run and this service runs for days/weeks
continuously:

1. ``DuplicateGuard`` (P9-06) keeps one payload hash per natural key
   (``ticker``/``orderbook``'s key includes a millisecond timestamp) for the
   life of the process. In a ~240s bounded run that dict is small and
   discarded on exit; in a 24/7 daemon it would grow by roughly one entry
   per accepted message forever -- an unbounded memory leak. ``LastSeenDuplicateGuard``
   below instead keeps only the single most-recently-seen payload hash per
   ``(kind, market)`` -- bounded by market count, not message count. This is
   a narrower, documented guarantee (catches Upbit's documented
   exact-immediate-retransmission case; does not catch a duplicate that
   reappears after other messages for the same market intervened), traded
   deliberately for a flat memory footprint.
2. ``ConnectionStateMachine`` (P9-06) intentionally fails closed to a
   permanent ``WAIT_MAX_RETRIES_EXCEEDED`` state after ``max_attempts`` --
   correct for a bounded run, where giving up just lets the process exit and
   the *next* cron trigger 30 minutes later retry cleanly. A 24/7 daemon has
   no next cron trigger: permanently giving up would mean the service goes
   dark until a human manually restarts it, defeating the point of a
   persistent service. ``PersistentConnectionState`` below therefore never
   reaches a permanent give-up state on its own -- it retries forever with
   the same capped-exponential-backoff schedule (reusing
   ``next_backoff_seconds`` unchanged) and always reports ``DISCONNECTED``
   (never a silently-stale ``CONNECTED``) while down. The only way it
   reaches a permanent ``STOPPED`` state is an explicit ``request_stop()``
   call from the process's own SIGTERM/SIGINT handler.

## Freshness contract

Every market/kind freshness value is one of exactly four values -- never a
silent default to ``FRESH``:

* ``FRESH`` -- the WebSocket is ``CONNECTED`` and the most recent accepted
  message for that market/kind is within its configured staleness window.
* ``STALE`` -- the WebSocket is ``CONNECTED`` but the most recent accepted
  message is older than its configured staleness window.
* ``DISCONNECTED`` -- the WebSocket is not currently ``CONNECTED``
  (``CONNECTING``/``RECONNECTING``/``STOPPED``), regardless of how recent the
  last accepted message was. A stale connection is never reported as fresh
  just because an old message happens to still be within the window.
* ``NO_DATA`` -- the WebSocket is ``CONNECTED`` but no message has ever been
  accepted yet for that market/kind (also used for P9-06's ``UNKNOWN`` case
  of an impossible/naive timestamp -- this service's contract has no
  ``UNKNOWN`` bucket, only these four values).

See ``docs/upbit_realtime_observation_service_contract.md`` for the full
JSON snapshot shape.

## Authority

Every snapshot's ``authority`` block is hardcoded all-``False`` in code,
unconditionally, matching P9-06's own discipline: this module produces
observation only, never a decision, recommendation, candidate-promotion,
entry, or order.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_gate_module():
    spec = importlib.util.spec_from_file_location(
        "upbit_realtime_gate_for_observation_service", ROOT / "realtime" / "upbit_realtime_gate.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GATE = _load_gate_module()

UTC = dt.timezone.utc
KST = dt.timezone(dt.timedelta(hours=9), name="KST")

SCHEMA_VERSION = "upbit_realtime_observation_snapshot/1"

FRESH = "FRESH"
STALE = "STALE"
DISCONNECTED = "DISCONNECTED"
NO_DATA = "NO_DATA"
FRESHNESS_STATUSES = (FRESH, STALE, DISCONNECTED, NO_DATA)
_FRESHNESS_SEVERITY = {FRESH: 0, STALE: 1, NO_DATA: 2, DISCONNECTED: 3}

CONNECTING = "CONNECTING"
CONNECTED = "CONNECTED"
RECONNECTING = "RECONNECTING"
STOPPED = "STOPPED"
CONNECTION_STATES = (CONNECTING, CONNECTED, RECONNECTING, STOPPED)

# This service tracks a deliberately narrower message-type set than P9-06 --
# see module docstring "Scope and boundary". ``trade``/``candle.*`` remain
# subscribed at the transport level (via GATE.build_subscription_message,
# reused unchanged) but are explicitly REJECTED_UNSUPPORTED_KIND here, never
# silently dropped and never used.
OBSERVATION_MESSAGE_TYPES = ("ticker", "orderbook")

DEFAULT_MAX_STALENESS_SECONDS_BY_KIND = {"ticker": 30, "orderbook": 15}

# Fixed, independently-configured observation set -- BTC/ETH plus major
# KRW-quoted coins. Never derived from universe/upbit_tradeable_universe.py
# (P3-12) or any TRADEABLE_UNIVERSE/PAPER_ELIGIBLE/candidate state. Override
# with ATLAS_UPBIT_OBS_MARKETS (comma-separated KRW-XXX codes) -- see
# service.py and .env.example.
DEFAULT_MARKETS = (
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-TRX", "KRW-AVAX", "KRW-DOT", "KRW-LINK",
)

_OBSERVATION_AUTHORITY = {
    "decision_eligible": False,
    "entry_eligibility_authorized": False,
    "exit_eligibility_authorized": False,
    "action_generation_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "private_channel_subscribed": False,
    "order_channel_subscribed": False,
    "candidate_promotion_authorized": False,
    "tradeable_universe_write_authorized": False,
    "paper_eligibility_authorized": False,
}


class ObservationServiceError(ValueError):
    """Fail-closed error for the persistent observation service."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def _require_aware(value: dt.datetime, code: str) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise ObservationServiceError(code)
    return value.astimezone(UTC)


def _iso_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_kst(value: dt.datetime) -> str:
    return value.astimezone(KST).isoformat(timespec="milliseconds")


def parse_market_list(raw: str) -> list[str]:
    """Pure helper: ``"KRW-BTC, KRW-ETH"`` -> ``["KRW-BTC", "KRW-ETH"]``.

    Fails closed on an empty result or an invalid market code rather than
    silently starting the service with zero/garbage markets.
    """
    if not isinstance(raw, str):
        raise ObservationServiceError("MARKET_LIST_NOT_STRING")
    codes = sorted({piece.strip().upper() for piece in raw.split(",") if piece.strip()})
    if not codes:
        raise ObservationServiceError("MARKET_LIST_EMPTY")
    bad = [code for code in codes if not GATE.MARKET_CODE_RE.fullmatch(code)]
    if bad:
        raise ObservationServiceError(f"MARKET_LIST_INVALID:{bad}")
    return codes


# ---------------------------------------------------------------------------
# Duplicate guard -- adapted for bounded memory over a long-running process
# ---------------------------------------------------------------------------

class LastSeenDuplicateGuard:
    """See module docstring "Reuse vs. adapt" item 1. Bounded by
    ``(kind, market)`` pair count, never by message count."""

    def __init__(self):
        self._last: dict = {}

    def check(self, key: tuple, payload_sha: str) -> str:
        prior = self._last.get(key)
        self._last[key] = payload_sha
        if prior is None:
            return "NEW"
        if prior == payload_sha:
            return "DUPLICATE_IGNORED"
        return "NEW_KEY_COLLISION"

    def __len__(self) -> int:
        return len(self._last)


# ---------------------------------------------------------------------------
# Reconnect state machine -- adapted for a 24/7 daemon that never gives up
# ---------------------------------------------------------------------------

class PersistentConnectionState:
    """See module docstring "Reuse vs. adapt" item 2. Pure -- no socket, no
    wall-clock read inside this class, every timestamp caller-supplied."""

    def __init__(self, *, base_backoff_seconds: float, max_backoff_seconds: float):
        if base_backoff_seconds <= 0 or max_backoff_seconds <= 0:
            raise ObservationServiceError("RECONNECT_BACKOFF_INVALID")
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.state = CONNECTING
        self.attempt = 0
        self.reconnect_count = 0
        self.consecutive_failures = 0
        self.last_disconnect_reason = None
        self.last_connected_at = None
        self.last_disconnected_at = None

    def on_connected(self, at: dt.datetime) -> None:
        at = _require_aware(at, "OBSERVATION_ON_CONNECTED_NAIVE")
        self.state = CONNECTED
        self.attempt = 0
        self.consecutive_failures = 0
        self.last_connected_at = at

    def on_disconnect(self, at: dt.datetime, reason: str) -> None:
        at = _require_aware(at, "OBSERVATION_ON_DISCONNECT_NAIVE")
        if not isinstance(reason, str) or not reason:
            raise ObservationServiceError("OBSERVATION_DISCONNECT_REASON_INVALID")
        self.state = RECONNECTING
        self.last_disconnect_reason = reason
        self.last_disconnected_at = at
        self.consecutive_failures += 1

    def next_attempt(self) -> dict:
        """Never fails closed to a permanent give-up state -- always
        ``RETRY`` with a capped backoff. Raises only if called after an
        explicit ``request_stop()`` (the process is shutting down)."""
        if self.state == STOPPED:
            raise ObservationServiceError("NEXT_ATTEMPT_AFTER_STOP")
        self.attempt += 1
        self.reconnect_count += 1
        backoff = GATE.next_backoff_seconds(
            self.attempt, base_seconds=self.base_backoff_seconds, max_seconds=self.max_backoff_seconds,
        )
        if backoff >= self.max_backoff_seconds:
            # Backoff has saturated at the cap -- stop letting `attempt` grow
            # without bound (a process with days/weeks of uptime and a flaky
            # network could otherwise grow `attempt` into the thousands;
            # 2 ** attempt would eventually overflow float range). Clamping
            # here keeps `next_backoff_seconds`'s pure formula unchanged
            # while this caller alone prevents unbounded growth.
            self.attempt = min(self.attempt, 64)
        return {"action": "RETRY", "attempt": self.attempt, "backoff_seconds": backoff}

    def request_stop(self) -> None:
        """Explicit clean-stop entry point -- the only way this state
        machine stops retrying. Called from the process's own
        SIGTERM/SIGINT handler in ``service.py``."""
        self.state = STOPPED


# ---------------------------------------------------------------------------
# The gate -- ties everything together
# ---------------------------------------------------------------------------

class ObservationGate:
    """The persistent-daemon orchestrator: fixed market list, duplicate
    guard, out-of-order tracker, connection state, and a
    ``status_snapshot`` producing this service's documented JSON contract.
    Every method is deterministic given its explicit inputs -- no network,
    no internal wall-clock read -- so the entire class is unit-testable with
    mocked messages and no live connection.
    """

    def __init__(
        self, *, markets, max_staleness_seconds_by_kind: dict = None,
        base_backoff_seconds: float = 1.0, max_backoff_seconds: float = 30.0,
    ):
        if not markets:
            raise ObservationServiceError("OBSERVATION_MARKETS_EMPTY")
        markets = sorted(set(markets))
        bad = [market for market in markets if not GATE.MARKET_CODE_RE.fullmatch(market)]
        if bad:
            raise ObservationServiceError(f"OBSERVATION_MARKET_INVALID:{bad}")
        self.markets = markets
        self.max_staleness_seconds_by_kind = dict(
            max_staleness_seconds_by_kind or DEFAULT_MAX_STALENESS_SECONDS_BY_KIND
        )
        self.connection = PersistentConnectionState(
            base_backoff_seconds=base_backoff_seconds, max_backoff_seconds=max_backoff_seconds,
        )
        self.duplicate_guard = LastSeenDuplicateGuard()
        self.sequence = GATE.SequenceTracker()
        self._latest: dict = {}
        self.counts = {
            "accepted": 0, "duplicate_ignored": 0, "out_of_order": 0,
            "rejected_malformed": 0, "rejected_out_of_scope_market": 0,
            "rejected_unsupported_kind": 0,
        }

    # -- message handling ---------------------------------------------------

    def handle_message(self, raw: dict, *, received_at: dt.datetime) -> dict:
        """Never raises: any failure mode is caught and returned as a
        structured, non-fatal result so the connection loop that calls this
        can never crash on a single bad message."""
        received_at = _require_aware(received_at, "OBSERVATION_HANDLE_MESSAGE_RECEIVED_AT_NAIVE")
        try:
            parsed = GATE.parse_message(raw)
        except GATE.RealtimeGateError as exc:
            self.counts["rejected_malformed"] += 1
            return {"action": "REJECTED_MALFORMED", "reason": str(exc)}
        if parsed["kind"] not in OBSERVATION_MESSAGE_TYPES:
            self.counts["rejected_unsupported_kind"] += 1
            return {"action": "REJECTED_UNSUPPORTED_KIND", "kind": parsed["kind"], "market": parsed["market"]}
        if parsed["market"] not in self.markets:
            self.counts["rejected_out_of_scope_market"] += 1
            return {
                "action": "REJECTED_OUT_OF_SCOPE_MARKET", "market": parsed["market"],
                "reason": f"MARKET_NOT_OBSERVED:{parsed['market']}",
            }
        dup_key = (parsed["kind"], parsed["market"])
        dup = self.duplicate_guard.check(dup_key, parsed["payload_sha256"])
        if dup == "DUPLICATE_IGNORED":
            self.counts["duplicate_ignored"] += 1
            return {"action": "DUPLICATE_IGNORED", "market": parsed["market"], "kind": parsed["kind"]}
        order = self.sequence.check(parsed)
        if order == "OUT_OF_ORDER":
            self.counts["out_of_order"] += 1
            return {"action": "OUT_OF_ORDER_FLAGGED", "market": parsed["market"], "kind": parsed["kind"]}
        self._ingest(parsed, received_at=received_at)
        self.counts["accepted"] += 1
        return {"action": "ACCEPTED", "market": parsed["market"], "kind": parsed["kind"]}

    def _ingest(self, parsed: dict, *, received_at: dt.datetime) -> None:
        market = parsed["market"]
        raw = parsed["raw"]
        entry = {
            "received_at": received_at,
            "exchange_timestamp_utc": _iso_utc(dt.datetime.fromtimestamp(raw["timestamp"] / 1000, tz=UTC)),
            "payload_sha256": parsed["payload_sha256"],
        }
        if parsed["kind"] == "ticker":
            entry.update({
                "last_price": raw.get("trade_price"),
                "change_direction": raw.get("change"),
                "change_rate": raw.get("change_rate"),
                "signed_change_rate": raw.get("signed_change_rate"),
                "change_price": raw.get("change_price"),
                "signed_change_price": raw.get("signed_change_price"),
                "last_trade_volume": raw.get("trade_volume"),
                "acc_trade_volume_24h": raw.get("acc_trade_volume_24h"),
                "acc_trade_price_24h": raw.get("acc_trade_price_24h"),
            })
        else:  # orderbook
            units = raw.get("orderbook_units") or []
            best = units[0] if isinstance(units, list) and units else {}
            entry.update({
                "best_bid_price": best.get("bid_price"),
                "best_bid_size": best.get("bid_size"),
                "best_ask_price": best.get("ask_price"),
                "best_ask_size": best.get("ask_size"),
                "orderbook_unit_count": len(units) if isinstance(units, list) else 0,
            })
        self._latest.setdefault(market, {})[parsed["kind"]] = entry

    # -- connection lifecycle -------------------------------------------------

    def on_connected(self, at: dt.datetime) -> None:
        self.connection.on_connected(at)

    def on_disconnect(self, at: dt.datetime, reason: str) -> None:
        self.connection.on_disconnect(at, reason)

    def next_reconnect_attempt(self) -> dict:
        return self.connection.next_attempt()

    def request_stop(self) -> None:
        self.connection.request_stop()

    # -- snapshot -------------------------------------------------------------

    def _freshness_for(self, entry, kind: str, *, now: dt.datetime) -> dict:
        if self.connection.state != CONNECTED:
            return {"status": DISCONNECTED, "age_seconds": None}
        if entry is None:
            return {"status": NO_DATA, "age_seconds": None}
        max_stale = self.max_staleness_seconds_by_kind.get(kind)
        result = GATE.evaluate_stream_freshness(entry["received_at"], now, max_stale)
        status = result["status"] if result["status"] != GATE.UNKNOWN else NO_DATA
        return {"status": status, "age_seconds": result["age_seconds"]}

    def status_snapshot(self, now: dt.datetime = None) -> dict:
        now = utc_now() if now is None else _require_aware(now, "OBSERVATION_SNAPSHOT_NOW_NAIVE")
        markets_out = {}
        overall = FRESH
        for market in self.markets:
            bucket = self._latest.get(market, {})
            ticker_entry = bucket.get("ticker")
            orderbook_entry = bucket.get("orderbook")
            ticker_fresh = self._freshness_for(ticker_entry, "ticker", now=now)
            orderbook_fresh = self._freshness_for(orderbook_entry, "orderbook", now=now)
            market_status = max(
                (ticker_fresh["status"], orderbook_fresh["status"]),
                key=lambda status: _FRESHNESS_SEVERITY[status],
            )
            if _FRESHNESS_SEVERITY[market_status] > _FRESHNESS_SEVERITY[overall]:
                overall = market_status
            markets_out[market] = {
                "market": market,
                "freshness": market_status,
                "ticker_freshness": ticker_fresh,
                "orderbook_freshness": orderbook_fresh,
                "last_price": (ticker_entry or {}).get("last_price"),
                "change_direction": (ticker_entry or {}).get("change_direction"),
                "change_rate": (ticker_entry or {}).get("change_rate"),
                "signed_change_rate": (ticker_entry or {}).get("signed_change_rate"),
                "acc_trade_volume_24h": (ticker_entry or {}).get("acc_trade_volume_24h"),
                "acc_trade_price_24h": (ticker_entry or {}).get("acc_trade_price_24h"),
                "best_bid": (
                    None if orderbook_entry is None else {
                        "price": orderbook_entry.get("best_bid_price"),
                        "size": orderbook_entry.get("best_bid_size"),
                    }
                ),
                "best_ask": (
                    None if orderbook_entry is None else {
                        "price": orderbook_entry.get("best_ask_price"),
                        "size": orderbook_entry.get("best_ask_size"),
                    }
                ),
                "ticker_exchange_timestamp_utc": (ticker_entry or {}).get("exchange_timestamp_utc"),
                "orderbook_exchange_timestamp_utc": (orderbook_entry or {}).get("exchange_timestamp_utc"),
                "received_at_utc": None if ticker_entry is None else _iso_utc(ticker_entry["received_at"]),
                "received_at_kst": None if ticker_entry is None else _iso_kst(ticker_entry["received_at"]),
                "orderbook_received_at_utc": (
                    None if orderbook_entry is None else _iso_utc(orderbook_entry["received_at"])
                ),
                "orderbook_received_at_kst": (
                    None if orderbook_entry is None else _iso_kst(orderbook_entry["received_at"])
                ),
            }
        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "service": "upbit-realtime-observation",
            "generated_at_utc": _iso_utc(now),
            "generated_at_kst": _iso_kst(now),
            "connection_state": self.connection.state,
            "reconnect_count": self.connection.reconnect_count,
            "consecutive_failures": self.connection.consecutive_failures,
            "last_disconnect_reason": self.connection.last_disconnect_reason,
            "overall_freshness": overall,
            "markets": markets_out,
            "counts": dict(self.counts),
            "duplicate_guard_size": len(self.duplicate_guard),
            "authority": dict(_OBSERVATION_AUTHORITY),
            "observation_only": True,
            "feeds_tradeable_universe": False,
            "feeds_candidate_promotion": False,
            "feeds_paper_eligibility": False,
            "feeds_decision_or_order_path": False,
        }
        snapshot["payload_sha256"] = GATE.payload_sha256(snapshot)
        return snapshot


def build_subscription_message(markets: list, *, ticket: str) -> list:
    """Thin pass-through to ``GATE.build_subscription_message`` -- reused
    unchanged, including its private-channel-forbidden guard. No candle
    timeframes are ever requested here (``candle_timeframes=()``); see
    module docstring "Scope and boundary"."""
    return GATE.build_subscription_message(markets, ticket=ticket, candle_timeframes=())
