#!/usr/bin/env python3
"""P9-06 Upbit real-time WebSocket finalized-candle & orderbook gate.

Public market-data WebSocket only (``wss://api.upbit.com/websocket/v1``),
subscribing to ``ticker``/``trade``/``orderbook``/``candle.{15m,60m,240m}``.
No API key/secret is used or needed -- Upbit's public market-data WS
requires no authentication. This module never builds, parses, or accepts a
message for ``myOrder``/``myAsset`` (Upbit's private/order channels), and
never calls any order/withdrawal/private REST endpoint. See
``build_subscription_message`` and ``PRIVATE_WS_TYPES_FORBIDDEN`` below.

This is deliberately a **reusable, fully-testable, deployment-agnostic**
Python module -- a connection state machine, message parsers, a duplicate
guard, a sequence/out-of-order tracker, a finalized-candle idempotency
ledger, and a freshness evaluator -- with **no actual socket code and no
``websockets`` import** anywhere in this file. It has no opinion about
whether it is driven by a bounded-duration cron capture job (this repo's
existing GitHub Actions architecture; see
``.github/scripts/upbit_realtime_capture.py``) or, later, a genuinely
persistent daemon on separate infrastructure -- the same state machine
works either way. See ``docs/upbit_realtime_gate_contract.md`` for the
full architecture rationale.

Reuses, never reimplements:

* ``microstructure/upbit_candle_finalization.py`` -- the *exact* P4-07
  "is this candle finalized?" boundary primitive
  (``classify_candles``/``merge_finalized_no_overwrite``/``detect_gaps``/
  ``group_contiguous_gaps``), imported unchanged. Upbit's public WS candle
  stream ships the *same* field names as the REST candle endpoint
  (``candle_date_time_utc``, ``opening_price``, ``high_price``,
  ``low_price``, ``trade_price``, ``candle_acc_trade_price``,
  ``candle_acc_trade_volume`` -- verified live against
  ``wss://api.upbit.com/websocket/v1`` on 2026-08-29), so WS candle rows
  feed into ``classify_candles`` completely unchanged. A candle's
  finalization state is a pure function of its own open time + timeframe +
  wall clock -- never of *which* transport (REST or WS) delivered the row
  -- so this reuse is exact, not approximate.
* ``execution/intraday_freshness.py`` -- P9-01's external-RATIFIED-policy
  freshness guard, called directly (not reimplemented) by
  ``evaluate_via_intraday_freshness_guard``. This repository ships no
  default/ratified CRYPTO threshold (same ``repository_default_policy:
  ABSENT`` discipline P9-01 itself established), so in production this call
  always fails closed to ``UNKNOWN`` until a human ratifies a real
  ``intraday_freshness_policy/1`` packet for the ``CRYPTO`` market -- see
  ``config/upbit_realtime_freshness_policy_proposal.json``.

Upbit's public WS has **no daily candle stream** (only
``candle.1s``/``1m``/``3m``/``5m``/``10m``/``15m``/``30m``/``60m``/``240m``)
-- verified against ``docs.upbit.com/kr/reference/websocket-candle`` on
2026-08-29. ``1d`` finalized-candle tracking stays a REST-only P4-07
concern; this module only tracks ``15m``/``1h``/``4h`` over WS.

Every output row/status snapshot's ``authority`` block is hardcoded
all-``false``: this module produces evidence and operational status, never
a decision, entry, or order.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from microstructure import upbit_candle_finalization as finalization  # noqa: E402

_INTRADAY_FRESHNESS_SPEC = importlib.util.spec_from_file_location(
    "intraday_freshness_for_upbit_realtime_gate",
    ROOT / "execution" / "intraday_freshness.py",
)
INTRADAY_FRESHNESS = importlib.util.module_from_spec(_INTRADAY_FRESHNESS_SPEC)
assert _INTRADAY_FRESHNESS_SPEC.loader is not None
_INTRADAY_FRESHNESS_SPEC.loader.exec_module(INTRADAY_FRESHNESS)


UTC = dt.timezone.utc
CONTRACT_PATH = ROOT / "config" / "upbit_realtime_gate_contract.json"
FRESHNESS_POLICY_PROPOSAL_PATH = ROOT / "config" / "upbit_realtime_freshness_policy_proposal.json"

OUTPUT_SCHEMA_VERSION = "upbit_realtime_gate_status/1"

FRESH = "FRESH"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
WAIT = "WAIT"
GATE_STATUSES = (FRESH, STALE, UNKNOWN, WAIT)

CONNECTING = "CONNECTING"
CONNECTED = "CONNECTED"
RECONNECTING = "RECONNECTING"
WAIT_MAX_RETRIES_EXCEEDED = "WAIT_MAX_RETRIES_EXCEEDED"
STOPPED = "STOPPED"

PUBLIC_MESSAGE_TYPES = ("ticker", "trade", "orderbook")
CANDLE_WS_TYPE_BY_TIMEFRAME = {"15m": "candle.15m", "1h": "candle.60m", "4h": "candle.240m"}
TIMEFRAME_BY_CANDLE_WS_TYPE = {v: k for k, v in CANDLE_WS_TYPE_BY_TIMEFRAME.items()}
PRIVATE_WS_TYPES_FORBIDDEN = ("myOrder", "myAsset")
MARKET_CODE_RE = re.compile(r"^KRW-[A-Z0-9]+$")

_GATE_AUTHORITY = {
    "decision_eligible": False,
    "entry_eligibility_authorized": False,
    "exit_eligibility_authorized": False,
    "action_generation_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "private_channel_subscribed": False,
    "order_channel_subscribed": False,
}


class RealtimeGateError(ValueError):
    """Fail-closed P9-06 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealtimeGateError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def _require_aware(value: dt.datetime, code: str) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise RealtimeGateError(code)
    return value.astimezone(UTC)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict) or value.get("contract_version") != "upbit_realtime_gate_contract/1":
        raise RealtimeGateError("CONTRACT_FIELD_MISMATCH:contract_version")
    if (
        value.get("auth_required") is not False
        or value.get("order_or_withdrawal_endpoints_called") is not False
        or value.get("private_channel_subscribed") is not False
    ):
        raise RealtimeGateError("CONTRACT_SAFETY_INVARIANT_VIOLATED")
    if set(value.get("public_message_types", [])) != set(PUBLIC_MESSAGE_TYPES):
        raise RealtimeGateError("CONTRACT_MESSAGE_TYPES_MISMATCH")
    if value.get("candle_ws_type_by_timeframe") != CANDLE_WS_TYPE_BY_TIMEFRAME:
        raise RealtimeGateError("CONTRACT_CANDLE_TYPES_MISMATCH")
    if set(value.get("private_channel_types_forbidden", [])) != set(PRIVATE_WS_TYPES_FORBIDDEN):
        raise RealtimeGateError("CONTRACT_PRIVATE_TYPES_MISMATCH")
    return copy.deepcopy(value)


def load_freshness_policy_proposal(path: Path = FRESHNESS_POLICY_PROPOSAL_PATH) -> dict:
    """Non-authoritative: read-only display of the proposed CRYPTO
    thresholds pending human ratification. Never fed programmatically into
    ``evaluate_via_intraday_freshness_guard`` -- production always passes
    ``ratified_policy=None`` until a real ``intraday_freshness_policy/1``
    packet is ratified.
    """
    value = _read_json(Path(path))
    if not isinstance(value, dict) or value.get("approval_status") == "RATIFIED":
        raise RealtimeGateError("FRESHNESS_PROPOSAL_MUST_NOT_BE_RATIFIED_HERE")
    return copy.deepcopy(value)


# ---------------------------------------------------------------------------
# Identity scoping -- P3-12 eligible-market set only, never auto-expanded
# ---------------------------------------------------------------------------

def eligible_markets_from_universe_packet(path) -> list[str]:
    """Markets already at ``TRADEABLE_UNIVERSE`` or ``PAPER_ELIGIBLE`` in the
    most recently *committed* P3-12 classification packet. Mirrors
    ``.github/scripts/upbit_microstructure_capture.py::load_target_markets``
    verbatim (same filter, same empty-is-not-an-error discipline) -- P9-06
    needs this as pure/reusable logic, not just capture-script glue.

    Returns an empty list (never an error) when no packet/path/eligible
    market exists -- expected while P3-12's policy/taxonomy/identity remain
    unratified, never a bug. This is the **only** source of the dynamic
    subscription list: a market present on Kraken, or in Upbit's raw
    ``market/all`` response, but not at ``TRADEABLE_UNIVERSE``/
    ``PAPER_ELIGIBLE`` here is never subscribed to -- no auto-promotion,
    same invariant P3-12/P4-07 already established.
    """
    if path is None or not Path(path).exists():
        return []
    record = _read_json(Path(path))
    packet = record.get("packet", record) if isinstance(record, dict) else {}
    markets = packet.get("markets", []) if isinstance(packet, dict) else []
    eligible = {"TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE"}
    return sorted(
        row["market"] for row in markets
        if isinstance(row, dict) and row.get("state") in eligible and isinstance(row.get("market"), str)
    )


# ---------------------------------------------------------------------------
# Subscription message -- public channels only, never private/order
# ---------------------------------------------------------------------------

def build_subscription_message(
    markets: list, *, ticket: str, candle_timeframes: tuple = ("15m", "1h", "4h"),
) -> list:
    """The exact Upbit public-WS subscribe payload shape:
    ``[{"ticket": ...}, {"type": ..., "codes": [...]}, ..., {"format": "DEFAULT"}]``.
    Never emits a type in ``PRIVATE_WS_TYPES_FORBIDDEN`` -- fails closed
    (raises) rather than silently building a private-channel subscription.
    """
    if not markets:
        raise RealtimeGateError("SUBSCRIPTION_MARKETS_EMPTY")
    if not isinstance(ticket, str) or not ticket:
        raise RealtimeGateError("SUBSCRIPTION_TICKET_INVALID")
    codes = sorted(set(markets))
    for code in codes:
        if not MARKET_CODE_RE.fullmatch(code):
            raise RealtimeGateError(f"SUBSCRIPTION_MARKET_INVALID:{code}")
    types = list(PUBLIC_MESSAGE_TYPES)
    for timeframe in candle_timeframes:
        if timeframe not in CANDLE_WS_TYPE_BY_TIMEFRAME:
            raise RealtimeGateError(f"SUBSCRIPTION_TIMEFRAME_UNSUPPORTED_OVER_WS:{timeframe}")
        types.append(CANDLE_WS_TYPE_BY_TIMEFRAME[timeframe])
    for message_type in types:
        if message_type in PRIVATE_WS_TYPES_FORBIDDEN:
            raise RealtimeGateError(f"PRIVATE_CHANNEL_FORBIDDEN:{message_type}")
    message = [{"ticket": ticket}]
    for message_type in types:
        message.append({"type": message_type, "codes": codes})
    message.append({"format": "DEFAULT"})
    return message


# ---------------------------------------------------------------------------
# Message parsing -- fail closed on anything malformed/unknown
# ---------------------------------------------------------------------------

REQUIRED_FIELDS_BY_TYPE = {
    "ticker": ("type", "code", "trade_price", "timestamp", "trade_timestamp", "stream_type"),
    "trade": (
        "type", "code", "trade_price", "trade_volume", "timestamp",
        "trade_timestamp", "sequential_id", "ask_bid", "stream_type",
    ),
    "orderbook": ("type", "code", "timestamp", "orderbook_units", "stream_type"),
}
REQUIRED_CANDLE_WS_FIELDS = (
    "type", "code", "candle_date_time_utc", "opening_price", "high_price", "low_price",
    "trade_price", "candle_acc_trade_price", "candle_acc_trade_volume", "timestamp", "stream_type",
)
_STREAM_TYPES = ("SNAPSHOT", "REALTIME")


def _is_candle_ws_type(message_type) -> bool:
    return isinstance(message_type, str) and message_type in TIMEFRAME_BY_CANDLE_WS_TYPE


def parse_message(raw: dict) -> dict:
    """Fail-closed structural validation + normalization of one inbound
    Upbit public WS message. Raises ``RealtimeGateError`` on anything
    malformed/unknown -- callers (``RealtimeGate.handle_message``) catch
    this and turn it into a ``REJECTED_MALFORMED`` result rather than
    crashing the connection loop; a malformed message is never silently
    treated as fresh/valid evidence.
    """
    if not isinstance(raw, dict):
        raise RealtimeGateError("MESSAGE_NOT_OBJECT")
    message_type = raw.get("type")
    if isinstance(message_type, str) and message_type in PRIVATE_WS_TYPES_FORBIDDEN:
        raise RealtimeGateError(f"PRIVATE_CHANNEL_MESSAGE_REJECTED:{message_type}")
    if message_type in REQUIRED_FIELDS_BY_TYPE:
        required = REQUIRED_FIELDS_BY_TYPE[message_type]
        kind = message_type
        timeframe = None
    elif _is_candle_ws_type(message_type):
        required = REQUIRED_CANDLE_WS_FIELDS
        kind = "candle"
        timeframe = TIMEFRAME_BY_CANDLE_WS_TYPE[message_type]
    else:
        raise RealtimeGateError(f"MESSAGE_TYPE_UNKNOWN:{message_type!r}")
    for field in required:
        if raw.get(field) is None:
            raise RealtimeGateError(f"MESSAGE_FIELD_MISSING:{message_type}:{field}")
    code = raw.get("code")
    if not isinstance(code, str) or not MARKET_CODE_RE.fullmatch(code):
        raise RealtimeGateError(f"MESSAGE_CODE_INVALID:{code!r}")
    if raw.get("stream_type") not in _STREAM_TYPES:
        raise RealtimeGateError(f"MESSAGE_STREAM_TYPE_INVALID:{raw.get('stream_type')!r}")
    for numeric_field in ("timestamp",):
        if not isinstance(raw.get(numeric_field), (int, float)) or isinstance(raw.get(numeric_field), bool):
            raise RealtimeGateError(f"MESSAGE_TIMESTAMP_INVALID:{message_type}:{numeric_field}")
    if kind == "trade":
        sid = raw.get("sequential_id")
        if not isinstance(sid, int) or isinstance(sid, bool):
            raise RealtimeGateError("MESSAGE_SEQUENTIAL_ID_INVALID")
    return {
        "kind": kind,
        "ws_type": message_type,
        "timeframe": timeframe,
        "market": code,
        "stream_type": raw["stream_type"],
        "raw": raw,
        "payload_sha256": payload_sha256(raw),
    }


def natural_key(parsed: dict) -> tuple:
    """The identity a duplicate/out-of-order check is keyed on:

    * ``trade``    -- ``sequential_id`` (Upbit's own documented unique,
      monotonically increasing per-market execution number).
    * ``candle``   -- ``(timeframe, candle_date_time_utc)`` (open time).
    * ``ticker``/``orderbook`` -- ``timestamp`` (ms). Upbit's own docs note
      the *same* ``candle_date_time`` (and, in practice, orderbook/ticker
      snapshots) can be retransmitted -- this key is deliberately paired
      with an exact-payload check in ``DuplicateGuard``, not used alone, so
      a same-timestamp-different-content update is never discarded.
    """
    kind, raw, market = parsed["kind"], parsed["raw"], parsed["market"]
    if kind == "trade":
        return ("trade", market, raw["sequential_id"])
    if kind == "candle":
        return ("candle", parsed["timeframe"], market, raw["candle_date_time_utc"])
    return (kind, market, raw["timestamp"])


# ---------------------------------------------------------------------------
# Duplicate guard
# ---------------------------------------------------------------------------

class DuplicateGuard:
    """Rejects an **exact** duplicate message (same natural key AND same
    payload) deterministically. A same-key, different-payload message (e.g.
    two orderbook snapshots sharing a millisecond timestamp, or a
    retransmitted in-progress candle with updated OHLCV) is a genuine new
    observation -- accepted, never silently discarded, matching Upbit's own
    documented candle-retransmission behavior.
    """

    def __init__(self):
        self._seen: dict = {}

    def check(self, key: tuple, payload_sha: str) -> str:
        prior = self._seen.get(key)
        if prior is None:
            self._seen[key] = payload_sha
            return "NEW"
        if prior == payload_sha:
            return "DUPLICATE_IGNORED"
        self._seen[key] = payload_sha
        return "NEW_KEY_COLLISION"

    def __len__(self) -> int:
        return len(self._seen)


# ---------------------------------------------------------------------------
# Sequence / out-of-order tracker
# ---------------------------------------------------------------------------

class SequenceTracker:
    """Flags (never raises for) an out-of-order message: a ``trade`` whose
    ``sequential_id`` regresses versus the highest already seen for that
    market, or a ``ticker``/``orderbook``/``candle`` whose ``timestamp``
    regresses versus the highest already seen for that
    (kind[, timeframe], market). An out-of-order message never advances the
    "latest" pointer and is never merged into committed state -- it also
    never crashes or corrupts the tracker's own state.
    """

    def __init__(self):
        self._last_trade_sequential_id: dict = {}
        self._last_timestamp: dict = {}

    def check(self, parsed: dict) -> str:
        kind, market, raw = parsed["kind"], parsed["market"], parsed["raw"]
        if kind == "trade":
            sid = raw["sequential_id"]
            last = self._last_trade_sequential_id.get(market)
            if last is not None and sid < last:
                return "OUT_OF_ORDER"
            self._last_trade_sequential_id[market] = sid if last is None else max(last, sid)
            return "IN_ORDER"
        key = (kind, parsed["timeframe"], market) if kind == "candle" else (kind, market)
        ts = raw["timestamp"]
        last = self._last_timestamp.get(key)
        if last is not None and ts < last:
            return "OUT_OF_ORDER"
        self._last_timestamp[key] = ts if last is None else max(last, ts)
        return "IN_ORDER"


# ---------------------------------------------------------------------------
# Reconnect state machine -- pure, no socket
# ---------------------------------------------------------------------------

def next_backoff_seconds(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    if attempt < 1:
        raise RealtimeGateError("RECONNECT_ATTEMPT_INVALID")
    return min(base_seconds * (2 ** (attempt - 1)), max_seconds)


class ConnectionStateMachine:
    """Tracks reconnect attempts/backoff/disconnect-intervals so the actual
    async connection loop (``.github/scripts/upbit_realtime_capture.py``)
    has an explicit, independently-testable fail-close decision at every
    step instead of ad hoc retry code. No socket, no wall-clock read inside
    this class -- every timestamp is caller-supplied.
    """

    def __init__(self, *, max_attempts: int, base_backoff_seconds: float, max_backoff_seconds: float):
        if max_attempts < 1:
            raise RealtimeGateError("RECONNECT_MAX_ATTEMPTS_INVALID")
        if base_backoff_seconds <= 0 or max_backoff_seconds <= 0:
            raise RealtimeGateError("RECONNECT_BACKOFF_INVALID")
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.state = CONNECTING
        self.attempt = 0
        self.reconnect_count = 0
        self.disconnect_intervals: list = []
        self.last_disconnect_reason = None
        self._disconnected_at = None

    def on_connected(self, at: dt.datetime) -> None:
        at = _require_aware(at, "CONNECTION_ON_CONNECTED_NAIVE")
        if self._disconnected_at is not None:
            gap_seconds = (at - self._disconnected_at).total_seconds()
            self.disconnect_intervals.append({
                "disconnected_at": self._disconnected_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "reconnected_at": at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "gap_seconds": gap_seconds,
            })
            self._disconnected_at = None
        self.state = CONNECTED
        self.attempt = 0

    def on_disconnect(self, at: dt.datetime, reason: str) -> None:
        at = _require_aware(at, "CONNECTION_ON_DISCONNECT_NAIVE")
        if not isinstance(reason, str) or not reason:
            raise RealtimeGateError("CONNECTION_DISCONNECT_REASON_INVALID")
        self._disconnected_at = at
        self.last_disconnect_reason = reason
        self.state = RECONNECTING

    def next_attempt(self) -> dict:
        if self.state != RECONNECTING:
            raise RealtimeGateError("NEXT_ATTEMPT_WHEN_NOT_RECONNECTING")
        self.attempt += 1
        if self.attempt > self.max_attempts:
            self.state = WAIT_MAX_RETRIES_EXCEEDED
            return {"action": "FAIL_CLOSED", "state": self.state}
        self.reconnect_count += 1
        backoff = next_backoff_seconds(
            self.attempt, base_seconds=self.base_backoff_seconds, max_seconds=self.max_backoff_seconds,
        )
        return {"action": "RETRY", "attempt": self.attempt, "backoff_seconds": backoff}

    def request_stop(self) -> None:
        """Explicit clean-stop entry point -- the async loop checks this
        every iteration; there is no other way for this state machine to
        keep retrying forever once called.
        """
        self.state = STOPPED


# ---------------------------------------------------------------------------
# Finalized-candle idempotency ledger -- built on P4-07's primitive
# ---------------------------------------------------------------------------

class CandleLedger:
    """Per (market, timeframe) finalized-candle idempotency, built directly
    on ``finalization.classify_candles``/``merge_finalized_no_overwrite`` --
    the exact P4-07 primitive, unchanged. A finalized candle re-delivered
    across a reconnect (identical raw bytes) is a harmless no-op; different
    raw bytes for an already-committed open time fails closed
    (``CandleFinalizationError: COMMITTED_CANDLE_MISMATCH``), propagated
    unchanged. An in-progress candle is never merged into committed state --
    it simply never appears in ``added_open_times``.
    """

    def __init__(self):
        self.committed: dict = {}

    def ingest(self, market: str, timeframe: str, raw_candle_row: dict, *, as_of: dt.datetime) -> dict:
        key = (market, timeframe)
        classified = finalization.classify_candles([raw_candle_row], timeframe, as_of)
        existing = self.committed.get(key, {})
        merged = finalization.merge_finalized_no_overwrite(existing, classified["finalized"])
        self.committed[key] = merged["merged"]
        return {
            "added_open_times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in merged["added_open_times"]],
            "in_progress_count": len(classified["in_progress"]),
            "duplicate_row_count": classified["duplicate_row_count"],
        }

    def finalized_open_times(self, market: str, timeframe: str) -> set:
        return set(self.committed.get((market, timeframe), {}))

    def finalized_count(self, market: str, timeframe: str) -> int:
        return len(self.committed.get((market, timeframe), {}))


def candle_gap_windows(
    committed_open_times, timeframe: str, window_start: dt.datetime, window_end: dt.datetime,
) -> list:
    """Literal reuse of P4-07's ``detect_gaps``/``group_contiguous_gaps`` on
    a market/timeframe's *committed finalized* open times -- the backfill
    trigger for the candle dimension of P9-06's gap detection.
    """
    missing = finalization.detect_gaps(committed_open_times, timeframe, window_start, window_end)
    return finalization.group_contiguous_gaps(missing, timeframe)


def connection_gap_windows(disconnect_intervals: list, *, min_gap_seconds) -> list:
    """The trade/ticker/orderbook dimension of gap detection: an interval
    the connection was actually down for at least ``min_gap_seconds`` is an
    unambiguous, deterministically-derived gap (not inferred from
    ``sequential_id`` spacing, which is not a small consecutive-integer
    space -- see module docstring / docs/upbit_realtime_gate_contract.md)
    -- each such window is a REST backfill candidate.
    """
    return [
        dict(interval) for interval in disconnect_intervals
        if interval["gap_seconds"] >= min_gap_seconds
    ]


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def evaluate_stream_freshness(last_message_at, now, max_staleness_seconds) -> dict:
    """Standalone real-time freshness check -- age of the most recent
    accepted message for a market/kind versus wall clock, fail-closed to
    ``UNKNOWN`` on a missing timestamp or an impossible ordering (``now``
    before ``last_message_at``). Never silently defaults to ``FRESH``.
    """
    if last_message_at is None or now is None or max_staleness_seconds is None:
        return {"status": UNKNOWN, "age_seconds": None, "max_staleness_seconds": max_staleness_seconds}
    last_message_at = _require_aware(last_message_at, "FRESHNESS_LAST_MESSAGE_NAIVE")
    now = _require_aware(now, "FRESHNESS_NOW_NAIVE")
    if now < last_message_at:
        return {"status": UNKNOWN, "age_seconds": None, "max_staleness_seconds": int(max_staleness_seconds)}
    age_seconds = int((now - last_message_at).total_seconds())
    max_staleness = int(max_staleness_seconds)
    status = FRESH if age_seconds <= max_staleness else STALE
    return {"status": status, "age_seconds": age_seconds, "max_staleness_seconds": max_staleness}


def quote_row_from_ticker(parsed: dict, *, received_at: dt.datetime) -> dict:
    """Builds one ``intraday_quote_batch/1``-shaped quote row (P9-01's own
    input shape) from a parsed ``ticker`` message, for
    ``evaluate_via_intraday_freshness_guard``.
    """
    if parsed["kind"] != "ticker":
        raise RealtimeGateError("QUOTE_ROW_REQUIRES_TICKER")
    raw = parsed["raw"]
    market = parsed["market"]
    provider_at = dt.datetime.fromtimestamp(raw["trade_timestamp"] / 1000, tz=UTC)
    received_at = _require_aware(received_at, "QUOTE_ROW_RECEIVED_AT_NAIVE")
    if received_at < provider_at:
        received_at = provider_at
    return {
        "asset_id": f"CRYPTO.UPBIT.{market}",
        "market": "CRYPTO",
        "price": str(raw["trade_price"]),
        "volume": str(raw.get("trade_volume", 0)),
        "quote_currency": "KRW",
        "provider_id": "UPBIT.WS.PUBLIC",
        "provider_timestamp": provider_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "received_at": received_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_ref": "wss://api.upbit.com/websocket/v1#ticker",
        "source_sha256": parsed["payload_sha256"],
    }


def evaluate_via_intraday_freshness_guard(
    quote_rows: list, *, observed_at: dt.datetime, batch_id: str, ratified_policy: dict = None,
) -> dict:
    """Literal reuse of P9-01's ``execution/intraday_freshness.py::
    evaluate_freshness`` -- not reimplemented. Fails closed to ``UNKNOWN``
    when no externally RATIFIED policy is supplied (the repository ships
    none by design), or when the supplied policy is rejected by P9-01's own
    validator for any reason (never silently falls back to a guessed
    threshold).
    """
    contract = INTRADAY_FRESHNESS.load_contract()
    observed_at = _require_aware(observed_at, "GUARD_OBSERVED_AT_NAIVE")
    batch = {
        "schema_version": contract["snapshot_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": batch_id,
        "observed_at": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quotes": quote_rows,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    batch["packet_sha256"] = INTRADAY_FRESHNESS.payload_sha256(batch)
    if ratified_policy is None:
        return {"status": UNKNOWN, "reason": "P9_01_RATIFIED_POLICY_ABSENT", "result": None}
    try:
        result = INTRADAY_FRESHNESS.evaluate_freshness(batch, ratified_policy, contract)
        return {"status": "EVALUATED", "reason": None, "result": result}
    except INTRADAY_FRESHNESS.IntradayFreshnessError as exc:
        return {"status": UNKNOWN, "reason": f"P9_01_GUARD_REJECTED:{exc}", "result": None}


# ---------------------------------------------------------------------------
# The gate -- ties everything together
# ---------------------------------------------------------------------------

class RealtimeGate:
    """The P9-06 orchestrator: identity-scoped market list, duplicate guard,
    out-of-order tracker, finalized-candle ledger, connection state
    machine, and a ``status_snapshot`` health/ready/metrics surface. Every
    method is deterministic given its explicit inputs -- no network, no
    internal wall-clock read (every timestamp is caller-supplied) -- so the
    entire class is unit-testable with mocked messages and no live
    connection.
    """

    def __init__(
        self, *, markets: list, max_reconnect_attempts: int, base_backoff_seconds: float,
        max_backoff_seconds: float, max_staleness_seconds_by_kind: dict, connection_gap_min_seconds,
    ):
        if not isinstance(markets, (list, tuple, set)):
            raise RealtimeGateError("GATE_MARKETS_INVALID")
        # An empty market list is valid and expected -- same "OBSERVATION_POOL
        # is normal, not a bug" discipline P3-12/P4-07 established: while
        # P3-12's policy/taxonomy/identity remain unratified, zero markets
        # reach TRADEABLE_UNIVERSE/PAPER_ELIGIBLE, so a P9-06 gate with no
        # markets to subscribe to is the expected production state today.
        self.markets = sorted(set(markets))
        self.connection = ConnectionStateMachine(
            max_attempts=max_reconnect_attempts, base_backoff_seconds=base_backoff_seconds,
            max_backoff_seconds=max_backoff_seconds,
        )
        self.duplicate_guard = DuplicateGuard()
        self.sequence = SequenceTracker()
        self.candles = CandleLedger()
        self.max_staleness_seconds_by_kind = dict(max_staleness_seconds_by_kind)
        self.connection_gap_min_seconds = connection_gap_min_seconds
        self.last_message_at: dict = {}
        self.counts = {
            "accepted": 0, "duplicate_ignored": 0, "out_of_order": 0,
            "rejected_malformed": 0, "rejected_out_of_scope_market": 0,
        }

    def handle_message(self, raw: dict, *, received_at: dt.datetime, as_of: dt.datetime = None) -> dict:
        """Never raises: any failure mode (malformed input, out-of-scope
        market) is caught and returned as a structured, non-fatal result so
        the connection loop that calls this can never crash on a single bad
        message.
        """
        received_at = _require_aware(received_at, "HANDLE_MESSAGE_RECEIVED_AT_NAIVE")
        as_of = received_at if as_of is None else _require_aware(as_of, "HANDLE_MESSAGE_AS_OF_NAIVE")
        try:
            parsed = parse_message(raw)
        except RealtimeGateError as exc:
            self.counts["rejected_malformed"] += 1
            return {"action": "REJECTED_MALFORMED", "reason": str(exc)}
        if parsed["market"] not in self.markets:
            self.counts["rejected_out_of_scope_market"] += 1
            return {
                "action": "REJECTED_OUT_OF_SCOPE_MARKET",
                "market": parsed["market"],
                "reason": f"MARKET_NOT_ELIGIBLE:{parsed['market']}",
            }
        key = natural_key(parsed)
        dup = self.duplicate_guard.check(key, parsed["payload_sha256"])
        if dup == "DUPLICATE_IGNORED":
            self.counts["duplicate_ignored"] += 1
            return {"action": "DUPLICATE_IGNORED", "market": parsed["market"], "kind": parsed["kind"]}
        order = self.sequence.check(parsed)
        if order == "OUT_OF_ORDER":
            self.counts["out_of_order"] += 1
            return {"action": "OUT_OF_ORDER_FLAGGED", "market": parsed["market"], "kind": parsed["kind"]}
        last_key = (parsed["kind"], parsed["timeframe"], parsed["market"]) if parsed["kind"] == "candle" \
            else (parsed["kind"], None, parsed["market"])
        self.last_message_at[last_key] = received_at
        result = {"action": "ACCEPTED", "market": parsed["market"], "kind": parsed["kind"]}
        if parsed["kind"] == "candle":
            result["candle_ingest"] = self.candles.ingest(
                parsed["market"], parsed["timeframe"], parsed["raw"], as_of=as_of,
            )
        self.counts["accepted"] += 1
        return result

    def on_disconnect(self, at: dt.datetime, reason: str) -> None:
        self.connection.on_disconnect(at, reason)

    def on_connected(self, at: dt.datetime) -> None:
        self.connection.on_connected(at)

    def next_reconnect_attempt(self) -> dict:
        return self.connection.next_attempt()

    def request_stop(self) -> None:
        self.connection.request_stop()

    def status_snapshot(self, now: dt.datetime) -> dict:
        now = _require_aware(now, "STATUS_SNAPSHOT_NOW_NAIVE")
        per_market = []
        # Worst-status aggregation across every market/kind: UNKNOWN (missing
        # or impossible-ordering evidence) outranks STALE (a known-negative
        # but at least legible signal), which outranks FRESH -- never
        # silently averaged or defaulted to the best-looking status.
        severity = {FRESH: 0, STALE: 1, UNKNOWN: 2}
        # Empty scope is a valid operational state while P3-12 is not
        # ratified, but it is not a freshness observation.  Starting the
        # reduction at FRESH would turn "nothing was subscribed or seen"
        # into a successful market-data result.
        worst = UNKNOWN if not self.markets else FRESH
        for market in self.markets:
            row = {"market": market, "freshness_by_kind": {}}
            for kind in PUBLIC_MESSAGE_TYPES:
                last_at = self.last_message_at.get((kind, None, market))
                max_stale = self.max_staleness_seconds_by_kind.get(kind)
                fresh = evaluate_stream_freshness(last_at, now, max_stale)
                row["freshness_by_kind"][kind] = fresh
                if severity[fresh["status"]] > severity[worst]:
                    worst = fresh["status"]
            per_market.append(row)
        gap_windows = connection_gap_windows(
            self.connection.disconnect_intervals, min_gap_seconds=self.connection_gap_min_seconds,
        )
        overall_status = WAIT if self.connection.state == WAIT_MAX_RETRIES_EXCEEDED else worst
        if gap_windows and overall_status == FRESH:
            overall_status = UNKNOWN
        snapshot = {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "connection_state": self.connection.state,
            "reconnect_count": self.connection.reconnect_count,
            "last_disconnect_reason": self.connection.last_disconnect_reason,
            "overall_status": overall_status,
            "counts": dict(self.counts),
            "markets": per_market,
            "pending_connection_gap_windows": gap_windows,
            "duplicate_guard_size": len(self.duplicate_guard),
            "authority": dict(_GATE_AUTHORITY),
        }
        snapshot["payload_sha256"] = payload_sha256(snapshot)
        return snapshot
