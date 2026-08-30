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
  ``evaluate_via_intraday_freshness_guard``. P9-06's production consumer
  accepts only the contract-pinned exact hash of
  ``config/upbit_realtime_freshness_policy_ratified.json`` and scopes it to
  CRYPTO quotes.  The older proposal file remains non-authoritative and can
  never pass the production loader.

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
RATIFIED_FRESHNESS_POLICY_PATH = ROOT / "config" / "upbit_realtime_freshness_policy_ratified.json"

OUTPUT_SCHEMA_VERSION = "upbit_realtime_gate_status/1"

FRESH = "FRESH"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
WAIT = "WAIT"
GATE_STATUSES = (FRESH, STALE, UNKNOWN, WAIT)

NATURAL_AUTOMATED = "NATURAL_AUTOMATED"
PIT_REPLAY = "PIT_REPLAY"
SYNTHETIC_FIXTURE = "SYNTHETIC_FIXTURE"
EVIDENCE_CLASSES = (NATURAL_AUTOMATED, PIT_REPLAY, SYNTHETIC_FIXTURE)

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


def _iso_utc(value: dt.datetime) -> str:
    return _require_aware(value, "UTC_TIMESTAMP_NAIVE").strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise RealtimeGateError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise RealtimeGateError(code) from exc
    if _iso_utc(parsed) != value:
        raise RealtimeGateError(code)
    return parsed


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
    if value.get("ratified_freshness_policy_path") != "config/upbit_realtime_freshness_policy_ratified.json":
        raise RealtimeGateError("CONTRACT_RATIFIED_POLICY_PATH_MISMATCH")
    policy_hash = value.get("ratified_freshness_policy_sha256")
    if not isinstance(policy_hash, str) or re.fullmatch(r"[0-9a-f]{64}", policy_hash) is None:
        raise RealtimeGateError("CONTRACT_RATIFIED_POLICY_HASH_INVALID")
    for field in (
        "rest_backfill_max_window_seconds", "rest_backfill_max_rows",
        "rest_backfill_max_requests_per_run", "rest_backfill_timeout_seconds",
    ):
        if type(value.get(field)) is not int or value[field] < 1:
            raise RealtimeGateError(f"CONTRACT_BACKFILL_BOUND_INVALID:{field}")
    thresholds = value.get("provider_gap_threshold_seconds_by_kind")
    if not isinstance(thresholds, dict) or set(thresholds) != {"ticker", "trade", "orderbook", "candle"}:
        raise RealtimeGateError("CONTRACT_PROVIDER_GAP_THRESHOLDS_INVALID")
    if any(type(item) is not int or item < 1 for item in thresholds.values()):
        raise RealtimeGateError("CONTRACT_PROVIDER_GAP_THRESHOLD_INVALID")
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


def load_ratified_freshness_policy(
    path: Path = RATIFIED_FRESHNESS_POLICY_PATH, *, observed_at: dt.datetime,
    contract: dict = None,
) -> dict:
    """Load only the exact-hash Notion-ratified P9-06 policy packet.

    The proposal file cannot pass this loader: it has a different schema,
    no self hash, and no contract-pinned exact hash.  The effective window is
    checked here before any quote bytes are evaluated, so a once-ratified but
    stale packet cannot be consumed.
    """
    observed_at = _require_aware(observed_at, "FRESHNESS_POLICY_OBSERVED_AT_NAIVE")
    contract = copy.deepcopy(contract) if contract is not None else load_contract()
    value = _read_json(Path(path))
    fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from_utc", "effective_to_utc",
        "input_contract_version", "max_provider_age_seconds_by_market",
        "max_transport_delay_seconds_by_market", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RealtimeGateError("FRESHNESS_POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != "intraday_freshness_policy/1"
        or value.get("approval_status") != "RATIFIED"
        or value.get("input_contract_version") != "intraday_freshness_guard/1"
    ):
        raise RealtimeGateError("FRESHNESS_POLICY_IDENTITY_INVALID")
    normalized = copy.deepcopy(value)
    claimed = normalized.pop("packet_sha256", None)
    expected = contract.get("ratified_freshness_policy_sha256")
    if claimed != expected or payload_sha256(normalized) != claimed:
        raise RealtimeGateError("FRESHNESS_POLICY_EXACT_HASH_MISMATCH")
    ratified = _parse_utc(value["ratified_at_utc"], "FRESHNESS_POLICY_RATIFIED_AT_INVALID")
    start = _parse_utc(value["effective_from_utc"], "FRESHNESS_POLICY_EFFECTIVE_FROM_INVALID")
    end = _parse_utc(value["effective_to_utc"], "FRESHNESS_POLICY_EFFECTIVE_TO_INVALID")
    if ratified > start or not (start <= observed_at < end):
        raise RealtimeGateError("FRESHNESS_POLICY_NOT_EFFECTIVE")
    if (
        value.get("max_provider_age_seconds_by_market", {}).get("CRYPTO") != 20
        or value.get("max_transport_delay_seconds_by_market", {}).get("CRYPTO") != 3
    ):
        raise RealtimeGateError("FRESHNESS_POLICY_CRYPTO_THRESHOLDS_MISMATCH")
    return copy.deepcopy(value)


def summarize_evidence_classes(records: list) -> dict:
    counts = {item: 0 for item in EVIDENCE_CLASSES}
    natural_days = set()
    for record in records:
        evidence_class = record.get("evidence_class") if isinstance(record, dict) else None
        if evidence_class not in counts:
            raise RealtimeGateError(f"EVIDENCE_CLASS_INVALID:{evidence_class}")
        counts[evidence_class] += 1
        if evidence_class == NATURAL_AUTOMATED:
            started_at = _parse_utc(record.get("started_at"), "EVIDENCE_STARTED_AT_INVALID")
            natural_days.add(started_at.date().isoformat())
    return {
        "natural_sample_count": counts[NATURAL_AUTOMATED],
        "p10_12_natural_day_count": len(natural_days),
        "pit_replay_count": counts[PIT_REPLAY],
        "synthetic_fixture_count": counts[SYNTHETIC_FIXTURE],
    }


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
    unchanged. An in-progress candle is preserved in a separate mutable
    bucket and can move to the immutable ledger only when its close boundary
    elapses or a public REST recovery returns it after close.
    """

    def __init__(self):
        self.committed: dict = {}
        self.in_progress: dict = {}

    @staticmethod
    def _canonical_row(raw_candle_row: dict) -> dict:
        """Transport-neutral candle body used as the ledger value.

        WS adds ``type/code/stream_type`` while REST adds ``market/unit``.
        Those transport envelopes must not create two logical candles for
        the same market/timeframe/open_time identity.
        """
        return {field: raw_candle_row.get(field) for field in finalization.REQUIRED_CANDLE_FIELDS}

    def ingest(self, market: str, timeframe: str, raw_candle_row: dict, *, as_of: dt.datetime) -> dict:
        key = (market, timeframe)
        canonical_row = self._canonical_row(raw_candle_row)
        classified = finalization.classify_candles([canonical_row], timeframe, as_of)
        existing = self.committed.get(key, {})
        merged = finalization.merge_finalized_no_overwrite(existing, classified["finalized"])
        self.committed[key] = merged["merged"]
        pending = self.in_progress.setdefault(key, {})
        for entry in classified["in_progress"]:
            pending[entry["open_time"]] = entry
        for open_time in merged["added_open_times"]:
            pending.pop(open_time, None)
        return {
            "added_open_times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in merged["added_open_times"]],
            "in_progress_count": len(classified["in_progress"]),
            "duplicate_row_count": classified["duplicate_row_count"],
        }

    def promote_closed(self, *, as_of: dt.datetime) -> dict:
        as_of = _require_aware(as_of, "CANDLE_PROMOTION_AS_OF_NAIVE")
        added = []
        for key in sorted(self.in_progress):
            market, timeframe = key
            pending = self.in_progress[key]
            rows = [pending[open_time]["raw"] for open_time in sorted(pending)]
            if not rows:
                continue
            classified = finalization.classify_candles(rows, timeframe, as_of)
            merged = finalization.merge_finalized_no_overwrite(
                self.committed.get(key, {}), classified["finalized"],
            )
            self.committed[key] = merged["merged"]
            for open_time in merged["added_open_times"]:
                pending.pop(open_time, None)
                added.append({
                    "market": market,
                    "timeframe": timeframe,
                    "open_time": _iso_utc(open_time),
                })
        return {"added_count": len(added), "added": added}

    def finalized_open_times(self, market: str, timeframe: str) -> set:
        return set(self.committed.get((market, timeframe), {}))

    def finalized_count(self, market: str, timeframe: str) -> int:
        return len(self.committed.get((market, timeframe), {}))

    def total_finalized_count(self) -> int:
        return sum(len(rows) for rows in self.committed.values())

    def in_progress_count(self, market: str, timeframe: str) -> int:
        return len(self.in_progress.get((market, timeframe), {}))

    def total_in_progress_count(self) -> int:
        return sum(len(rows) for rows in self.in_progress.values())


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


def _returned_provider_times(request: dict, payload: list) -> list[dt.datetime]:
    values = []
    for row in payload:
        if not isinstance(row, dict):
            raise RealtimeGateError("BACKFILL_PAYLOAD_ROW_INVALID")
        if request.get("kind") == "candle":
            values.append(finalization.parse_candle_open_time(row))
        elif request.get("kind") == "trade":
            timestamp = row.get("trade_timestamp", row.get("timestamp"))
            if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
                raise RealtimeGateError("BACKFILL_TRADE_PROVIDER_TIME_INVALID")
            values.append(dt.datetime.fromtimestamp(timestamp / 1000, tz=UTC))
        else:
            raise RealtimeGateError(f"BACKFILL_REQUEST_KIND_INVALID:{request.get('kind')}")
    return values


def backfill_response_record(
    *, request: dict, payload: list, requested_at: dt.datetime, received_at: dt.datetime,
) -> dict:
    requested_at = _require_aware(requested_at, "BACKFILL_REQUESTED_AT_NAIVE")
    received_at = _require_aware(received_at, "BACKFILL_RECEIVED_AT_NAIVE")
    if received_at < requested_at:
        raise RealtimeGateError("BACKFILL_TRANSPORT_TIME_INVALID")
    if not isinstance(request, dict) or request.get("method") != "GET" or request.get("auth_required") is not False:
        raise RealtimeGateError("BACKFILL_REQUEST_PUBLIC_GET_REQUIRED")
    if type(request.get("count")) is not int or request["count"] < 1:
        raise RealtimeGateError("BACKFILL_REQUEST_COUNT_INVALID")
    request_identity = {
        field: request.get(field) for field in (
            "kind", "timeframe", "market", "method", "url", "count", "auth_required", "request_range",
        )
    }
    if request.get("request_id") != payload_sha256(request_identity):
        raise RealtimeGateError("BACKFILL_REQUEST_ID_MISMATCH")
    url = request.get("url")
    if not isinstance(url, str) or not url.startswith("https://api.upbit.com/v1/"):
        raise RealtimeGateError("BACKFILL_REQUEST_ENDPOINT_FORBIDDEN")
    if any(fragment in url for fragment in ("/orders", "/withdraw", "/deposit", "/accounts")):
        raise RealtimeGateError("BACKFILL_REQUEST_ENDPOINT_FORBIDDEN")
    if not isinstance(payload, list):
        raise RealtimeGateError("BACKFILL_PAYLOAD_NOT_LIST")
    provider_times = _returned_provider_times(request, payload)
    request_range = copy.deepcopy(request.get("request_range"))
    if not isinstance(request_range, dict) or set(request_range) != {"from", "to"}:
        raise RealtimeGateError("BACKFILL_REQUEST_RANGE_INVALID")
    range_start = _parse_utc(request_range["from"], "BACKFILL_REQUEST_FROM_INVALID")
    range_end = _parse_utc(request_range["to"], "BACKFILL_REQUEST_TO_INVALID")
    if range_end <= range_start:
        raise RealtimeGateError("BACKFILL_REQUEST_RANGE_INVALID")
    if any(provider_at < range_start or provider_at > range_end for provider_at in provider_times):
        raise RealtimeGateError("BACKFILL_RETURNED_RANGE_OUTSIDE_REQUEST")
    returned_range = {
        "from": _iso_utc(min(provider_times)) if provider_times else None,
        "to": _iso_utc(max(provider_times)) if provider_times else None,
        "row_count": len(payload),
    }
    return {
        "request_id": request.get("request_id"),
        "requested_count": request.get("count"),
        "kind": request.get("kind"),
        "timeframe": request.get("timeframe"),
        "market": request.get("market"),
        "method": "GET",
        "url": url,
        "auth_required": False,
        "request_range": request_range,
        "returned_range": returned_range,
        "provider_time": {
            "first_returned_at": returned_range["from"],
            "last_returned_at": returned_range["to"],
        },
        "transport_time": {
            "requested_at": _iso_utc(requested_at),
            "received_at": _iso_utc(received_at),
            "duration_milliseconds": int((received_at - requested_at).total_seconds() * 1000),
        },
        "payload_sha256": payload_sha256(payload),
        "payload": copy.deepcopy(payload),
    }


def build_backfill_receipt(
    *, gap_ids: list, responses: list, evidence_class: str, generated_at: dt.datetime,
) -> dict:
    if evidence_class not in EVIDENCE_CLASSES:
        raise RealtimeGateError(f"EVIDENCE_CLASS_INVALID:{evidence_class}")
    if not isinstance(gap_ids, list) or any(
        not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None for item in gap_ids
    ):
        raise RealtimeGateError("BACKFILL_GAP_IDS_INVALID")
    if not isinstance(responses, list):
        raise RealtimeGateError("BACKFILL_RESPONSES_INVALID")
    receipt = {
        "schema_version": "upbit_public_rest_backfill_receipt/1",
        "evidence_class": evidence_class,
        "source": "UPBIT_PUBLIC_REST",
        "generated_at": _iso_utc(generated_at),
        "gap_ids": sorted(set(gap_ids)),
        "responses": copy.deepcopy(responses),
        "auth_required": False,
        "private_or_order_endpoint_called": False,
        "authority": dict(_GATE_AUTHORITY),
    }
    receipt["receipt_sha256"] = payload_sha256(receipt)
    return receipt


def validate_backfill_receipt(receipt: dict) -> dict:
    fields = {
        "schema_version", "evidence_class", "source", "generated_at", "gap_ids",
        "responses", "auth_required", "private_or_order_endpoint_called", "authority",
        "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise RealtimeGateError("BACKFILL_RECEIPT_FIELDS_MISMATCH")
    if (
        receipt.get("schema_version") != "upbit_public_rest_backfill_receipt/1"
        or receipt.get("source") != "UPBIT_PUBLIC_REST"
        or receipt.get("evidence_class") not in EVIDENCE_CLASSES
        or receipt.get("auth_required") is not False
        or receipt.get("private_or_order_endpoint_called") is not False
        or receipt.get("authority") != _GATE_AUTHORITY
    ):
        raise RealtimeGateError("BACKFILL_RECEIPT_IDENTITY_INVALID")
    claimed = receipt.get("receipt_sha256")
    normalized = copy.deepcopy(receipt)
    normalized.pop("receipt_sha256", None)
    if not isinstance(claimed, str) or payload_sha256(normalized) != claimed:
        raise RealtimeGateError("BACKFILL_RECEIPT_HASH_MISMATCH")
    _parse_utc(receipt.get("generated_at"), "BACKFILL_RECEIPT_GENERATED_AT_INVALID")
    if not isinstance(receipt.get("gap_ids"), list) or any(
        not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
        for item in receipt["gap_ids"]
    ):
        raise RealtimeGateError("BACKFILL_RECEIPT_GAP_IDS_INVALID")
    for response in receipt.get("responses", []):
        if not isinstance(response, dict):
            raise RealtimeGateError("BACKFILL_RESPONSE_INVALID")
        if payload_sha256(response.get("payload")) != response.get("payload_sha256"):
            raise RealtimeGateError("BACKFILL_RESPONSE_PAYLOAD_HASH_MISMATCH")
        rebuilt = backfill_response_record(
            request={
                "request_id": response.get("request_id"),
                "kind": response.get("kind"),
                "timeframe": response.get("timeframe"),
                "market": response.get("market"),
                "method": response.get("method"),
                "url": response.get("url"),
                "count": response.get("requested_count"),
                "auth_required": response.get("auth_required"),
                "request_range": response.get("request_range"),
            },
            payload=response.get("payload"),
            requested_at=_parse_utc(
                response.get("transport_time", {}).get("requested_at"),
                "BACKFILL_RESPONSE_REQUESTED_AT_INVALID",
            ),
            received_at=_parse_utc(
                response.get("transport_time", {}).get("received_at"),
                "BACKFILL_RESPONSE_RECEIVED_AT_INVALID",
            ),
        )
        if rebuilt != response:
            raise RealtimeGateError("BACKFILL_RESPONSE_REVALIDATION_MISMATCH")
    return copy.deepcopy(receipt)


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


def evaluate_with_ratified_freshness_policy(
    quote_rows: list, *, observed_at: dt.datetime, batch_id: str,
    policy_path: Path = RATIFIED_FRESHNESS_POLICY_PATH, contract: dict = None,
) -> dict:
    """P9-06 production consumer for the exact-hash CRYPTO policy only."""
    if not isinstance(quote_rows, list) or any(
        not isinstance(row, dict) or row.get("market") != "CRYPTO" for row in quote_rows
    ):
        raise RealtimeGateError("P9_06_FRESHNESS_CONSUMER_CRYPTO_ONLY")
    policy = load_ratified_freshness_policy(
        policy_path, observed_at=observed_at, contract=contract,
    )
    return evaluate_via_intraday_freshness_guard(
        quote_rows, observed_at=observed_at, batch_id=batch_id, ratified_policy=policy,
    )


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
        provider_gap_threshold_seconds_by_kind: dict = None,
        max_backfill_window_seconds: int = 300, max_backfill_rows: int = 20,
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
        self.provider_gap_threshold_seconds_by_kind = dict(
            provider_gap_threshold_seconds_by_kind
            or {"ticker": 5, "trade": 5, "orderbook": 5, "candle": 20}
        )
        self.max_backfill_window_seconds = int(max_backfill_window_seconds)
        self.max_backfill_rows = int(max_backfill_rows)
        if self.max_backfill_window_seconds < 1 or self.max_backfill_rows < 1:
            raise RealtimeGateError("GATE_BACKFILL_BOUND_INVALID")
        self.last_message_at: dict = {}
        self.last_provider_at: dict = {}
        self._gaps: dict = {}
        self.receipt_ledger: dict = {}
        self.counts = {
            "accepted": 0, "duplicate_ignored": 0, "out_of_order": 0,
            "rejected_malformed": 0, "rejected_out_of_scope_market": 0,
            "rest_backfill_receipts_applied": 0, "rest_backfill_receipts_revalidated": 0,
            "rest_backfill_candles_added": 0,
        }

    @staticmethod
    def _provider_at(parsed: dict) -> dt.datetime:
        raw = parsed["raw"]
        timestamp = raw.get("trade_timestamp") if parsed["kind"] in ("ticker", "trade") else raw.get("timestamp")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            raise RealtimeGateError("MESSAGE_PROVIDER_TIMESTAMP_INVALID")
        return dt.datetime.fromtimestamp(timestamp / 1000, tz=UTC)

    def _record_gap(
        self, *, source: str, kind: str, timeframe, market, start: dt.datetime, end: dt.datetime,
    ) -> dict:
        start = _require_aware(start, "GAP_START_NAIVE")
        end = _require_aware(end, "GAP_END_NAIVE")
        if end <= start:
            raise RealtimeGateError("GAP_WINDOW_INVALID")
        body = {
            "schema_version": "upbit_realtime_gap/1",
            "source": source,
            "kind": kind,
            "timeframe": timeframe,
            "market": market,
            "from": _iso_utc(start),
            "to": _iso_utc(end),
            "duration_seconds": int((end - start).total_seconds()),
            "bounded": (end - start).total_seconds() <= self.max_backfill_window_seconds,
            "max_backfill_window_seconds": self.max_backfill_window_seconds,
        }
        gap_id = payload_sha256(body)
        row = {**body, "gap_id": gap_id, "status": "PENDING"}
        self._gaps.setdefault(gap_id, row)
        return copy.deepcopy(self._gaps[gap_id])

    def pending_gap_windows(self) -> list:
        return [copy.deepcopy(self._gaps[key]) for key in sorted(self._gaps) if self._gaps[key]["status"] == "PENDING"]

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
            provider_at = self._provider_at(parsed)
            provider_key = (parsed["kind"], parsed["timeframe"], parsed["market"])
            last_provider_at = self.last_provider_at.get(provider_key)
            if last_provider_at is not None and last_provider_at > provider_at:
                self._record_gap(
                    source="WS_SEQUENCE_REGRESSION", kind=parsed["kind"], timeframe=parsed["timeframe"],
                    market=parsed["market"], start=provider_at, end=last_provider_at,
                )
            return {"action": "OUT_OF_ORDER_FLAGGED", "market": parsed["market"], "kind": parsed["kind"]}
        provider_at = self._provider_at(parsed)
        provider_key = (parsed["kind"], parsed["timeframe"], parsed["market"])
        last_provider_at = self.last_provider_at.get(provider_key)
        gap = None
        threshold = self.provider_gap_threshold_seconds_by_kind.get(parsed["kind"])
        if last_provider_at is not None and threshold is not None:
            elapsed = (provider_at - last_provider_at).total_seconds()
            if elapsed > threshold:
                gap = self._record_gap(
                    source="WS_PROVIDER_TIME_GAP", kind=parsed["kind"], timeframe=parsed["timeframe"],
                    market=parsed["market"], start=last_provider_at, end=provider_at,
                )
        self.last_provider_at[provider_key] = provider_at
        last_key = (parsed["kind"], parsed["timeframe"], parsed["market"]) if parsed["kind"] == "candle" \
            else (parsed["kind"], None, parsed["market"])
        self.last_message_at[last_key] = received_at
        result = {"action": "ACCEPTED", "market": parsed["market"], "kind": parsed["kind"]}
        if parsed["kind"] == "candle":
            result["candle_ingest"] = self.candles.ingest(
                parsed["market"], parsed["timeframe"], parsed["raw"], as_of=as_of,
            )
        if gap is not None:
            result["detected_gap"] = gap
        self.counts["accepted"] += 1
        return result

    def on_disconnect(self, at: dt.datetime, reason: str) -> None:
        self.connection.on_disconnect(at, reason)

    def on_connected(self, at: dt.datetime) -> None:
        before = len(self.connection.disconnect_intervals)
        self.connection.on_connected(at)
        if len(self.connection.disconnect_intervals) > before:
            interval = self.connection.disconnect_intervals[-1]
            if interval["gap_seconds"] >= self.connection_gap_min_seconds:
                self._record_gap(
                    source="WS_CONNECTION_GAP", kind="connection", timeframe=None, market=None,
                    start=dt.datetime.strptime(
                        interval["disconnected_at"], "%Y-%m-%dT%H:%M:%S.%fZ",
                    ).replace(tzinfo=UTC),
                    end=dt.datetime.strptime(
                        interval["reconnected_at"], "%Y-%m-%dT%H:%M:%S.%fZ",
                    ).replace(tzinfo=UTC),
                )

    def next_reconnect_attempt(self) -> dict:
        return self.connection.next_attempt()

    def request_stop(self) -> None:
        self.connection.request_stop()

    def apply_backfill_receipt(self, receipt: dict, *, revalidated_at: dt.datetime) -> dict:
        receipt = validate_backfill_receipt(receipt)
        revalidated_at = _require_aware(revalidated_at, "BACKFILL_REVALIDATED_AT_NAIVE")
        receipt_hash = receipt["receipt_sha256"]
        if receipt_hash in self.receipt_ledger:
            self.counts["rest_backfill_receipts_revalidated"] += 1
            return {
                "action": "IDEMPOTENT_REVALIDATED",
                "receipt_sha256": receipt_hash,
                "added_candle_count": 0,
            }
        unknown_gap_ids = [gap_id for gap_id in receipt["gap_ids"] if gap_id not in self._gaps]
        if unknown_gap_ids:
            raise RealtimeGateError(f"BACKFILL_RECEIPT_UNKNOWN_GAP:{unknown_gap_ids[0]}")
        response_keys = {
            (response.get("kind"), response.get("timeframe"), response.get("market"))
            for response in receipt["responses"]
        }
        required_dimensions = {
            ("trade", None), ("candle", "15m"), ("candle", "1h"), ("candle", "4h"),
        }
        for gap_id in receipt["gap_ids"]:
            gap = self._gaps[gap_id]
            required_markets = [gap["market"]] if gap.get("market") else self.markets
            for market in required_markets:
                if any((kind, timeframe, market) not in response_keys for kind, timeframe in required_dimensions):
                    raise RealtimeGateError(f"BACKFILL_RECEIPT_COVERAGE_INCOMPLETE:{gap_id}:{market}")
        added = 0
        for response in receipt["responses"]:
            if response["kind"] != "candle":
                continue
            market = response.get("market")
            timeframe = response.get("timeframe")
            if market not in self.markets or timeframe not in CANDLE_WS_TYPE_BY_TIMEFRAME:
                raise RealtimeGateError("BACKFILL_CANDLE_SCOPE_INVALID")
            for row in response["payload"]:
                ingest = self.candles.ingest(market, timeframe, row, as_of=revalidated_at)
                added += len(ingest["added_open_times"])
        for gap_id in receipt["gap_ids"]:
            self._gaps[gap_id]["status"] = "RESOLVED"
            self._gaps[gap_id]["resolved_by_receipt_sha256"] = receipt_hash
        application = {
            "receipt_sha256": receipt_hash,
            "applied_at": _iso_utc(revalidated_at),
            "added_candle_count": added,
            "evidence_class": receipt["evidence_class"],
        }
        self.receipt_ledger[receipt_hash] = application
        self.counts["rest_backfill_receipts_applied"] += 1
        self.counts["rest_backfill_candles_added"] += added
        return {"action": "RECEIPT_APPLIED", **application}

    def status_snapshot(self, now: dt.datetime) -> dict:
        now = _require_aware(now, "STATUS_SNAPSHOT_NOW_NAIVE")
        promotion = self.candles.promote_closed(as_of=now)
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
        gap_windows = self.pending_gap_windows()
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
            "pending_connection_gap_windows": [
                row for row in gap_windows if row["source"] == "WS_CONNECTION_GAP"
            ],
            "pending_gap_windows": gap_windows,
            "receipt_ledger_count": len(self.receipt_ledger),
            "finalized_candle_ledger_count": self.candles.total_finalized_count(),
            "in_progress_candle_count": self.candles.total_in_progress_count(),
            "closed_candle_promotion": promotion,
            "duplicate_guard_size": len(self.duplicate_guard),
            "authority": dict(_GATE_AUTHORITY),
        }
        snapshot["payload_sha256"] = payload_sha256(snapshot)
        return snapshot
