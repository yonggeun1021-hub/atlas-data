#!/usr/bin/env python3
"""P4-07 Upbit public-market evidence & microstructure derivation.

REST-based evidence only -- real-time WebSocket ingestion is P9-06, not this
module. Given already-captured, hash-validated raw evidence (candles across
15m/1h/4h/1d, recent public trade ticks, an orderbook snapshot), this module
derives:

* finalized-vs-in-progress candles per timeframe
  (``microstructure/upbit_candle_finalization.py``, imported unchanged).
* spread (best bid/ask, bps) and depth (cumulative KRW at N levels) from an
  orderbook snapshot.
* an estimated PAPER-order slippage -- REUSES, not duplicates,
  ``universe/upbit_tradeable_universe.py``'s ``_spread_bps``/
  ``_estimate_slippage_bps`` (imported below via the same
  ``importlib.util.spec_from_file_location`` pattern P3-12 itself uses to
  import the capture module).
* an explicit ``freshness`` status (``FRESH``/``STALE``/``UNKNOWN``) per
  artifact, consistent with (but not wired into) P9-01's
  ``execution/intraday_freshness.py`` naming.

Every derivation function here is a pure function of its arguments -- no
wall-clock inside the derivation math itself (only the capture layer's
``captured_at``/``available_at`` carry a timestamp) -- so re-deriving from
the same raw evidence is byte-identical on rerun.

Every output row/packet's ``authority`` block is hardcoded all-``false``:
this module produces evidence, never a decision, entry, or order.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONTRACT_PATH = ROOT / "config" / "upbit_market_evidence_contract.json"
POLICY_PATH = ROOT / "config" / "upbit_market_evidence_policy.json"

_UNIVERSE_SPEC = importlib.util.spec_from_file_location(
    "upbit_tradeable_universe_for_microstructure",
    ROOT / "universe" / "upbit_tradeable_universe.py",
)
UPBIT_UNIVERSE = importlib.util.module_from_spec(_UNIVERSE_SPEC)
assert _UNIVERSE_SPEC.loader is not None
_UNIVERSE_SPEC.loader.exec_module(UPBIT_UNIVERSE)

from microstructure import upbit_candle_finalization as finalization  # noqa: E402


UTC = dt.timezone.utc
OUTPUT_SCHEMA_VERSION = "upbit_market_evidence_packet/1"

FRESH = "FRESH"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
FRESHNESS_STATUSES = (FRESH, STALE, UNKNOWN)

# Hardcoded, never policy-driven: no derivation function in this module may
# set any of these to true. Turning evidence into decision/order authority
# is a separate, later, explicitly-ratified change (P9-06/P5-08/P5-09/
# P8-16's job, not this one's).
_EVIDENCE_AUTHORITY = {
    "decision_eligible": False,
    "entry_eligibility_authorized": False,
    "exit_eligibility_authorized": False,
    "action_generation_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class MarketEvidenceError(ValueError):
    """Fail-closed P4-07 evidence/microstructure contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _decimal(value, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MarketEvidenceError(f"DECIMAL_INVALID:{label}:{value!r}") from exc


def _require_aware(value: dt.datetime, code: str) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise MarketEvidenceError(code)
    return value.astimezone(UTC)


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketEvidenceError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict) or value.get("contract_version") != "upbit_market_evidence_contract/1":
        raise MarketEvidenceError("CONTRACT_FIELD_MISMATCH:contract_version")
    if value.get("auth_required") is not False or value.get("order_or_withdrawal_endpoints_called") is not False:
        raise MarketEvidenceError("CONTRACT_SAFETY_INVARIANT_VIOLATED")
    if set(value.get("timeframes", [])) != set(finalization.TIMEFRAMES):
        raise MarketEvidenceError("CONTRACT_TIMEFRAMES_MISMATCH")
    return copy.deepcopy(value)


def load_policy(path: Path = POLICY_PATH) -> dict:
    doc = _read_json(Path(path))
    required = {
        "approval_status", "orderbook_depth_levels", "paper_slippage_estimate_notional_krw",
        "max_spread_bps_normal", "max_slippage_bps_normal", "max_staleness_seconds_by_timeframe",
        "max_trades_staleness_seconds", "max_orderbook_staleness_seconds",
    }
    if not isinstance(doc, dict) or not required.issubset(doc):
        raise MarketEvidenceError("POLICY_FIELDS_INVALID")
    if set(doc["max_staleness_seconds_by_timeframe"]) != set(finalization.TIMEFRAMES):
        raise MarketEvidenceError("POLICY_STALENESS_TIMEFRAMES_MISMATCH")
    return doc


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

def freshness_status(reference_time: dt.datetime | None, captured_at: dt.datetime | None, max_staleness_seconds) -> dict:
    """``reference_time`` is the instant the evidence is stale *relative to*
    -- a finalized candle's close time, or a trade/orderbook's own captured
    instant. ``captured_at`` is when this repository actually observed it.

    Fails closed to ``UNKNOWN`` (never a silent ``FRESH``/``STALE`` guess)
    when either timestamp is missing, or when ``captured_at`` precedes
    ``reference_time`` (an impossible ordering -- evidence cannot be
    observed before the instant it claims to describe).
    """
    if reference_time is None or captured_at is None:
        return {"status": UNKNOWN, "age_seconds": None, "max_staleness_seconds": max_staleness_seconds}
    reference_time = _require_aware(reference_time, "FRESHNESS_REFERENCE_TIME_NAIVE")
    captured_at = _require_aware(captured_at, "FRESHNESS_CAPTURED_AT_NAIVE")
    if captured_at < reference_time:
        return {"status": UNKNOWN, "age_seconds": None, "max_staleness_seconds": max_staleness_seconds}
    age_seconds = int((captured_at - reference_time).total_seconds())
    max_staleness = int(max_staleness_seconds)
    status = FRESH if age_seconds <= max_staleness else STALE
    return {"status": status, "age_seconds": age_seconds, "max_staleness_seconds": max_staleness}


# ---------------------------------------------------------------------------
# Candle (multi-timeframe) evidence
# ---------------------------------------------------------------------------

def build_candle_evidence(
    market: str, timeframe: str, raw_candles: list, *,
    as_of: dt.datetime, captured_at: dt.datetime, max_staleness_seconds: int,
) -> dict:
    classified = finalization.classify_candles(raw_candles, timeframe, as_of)
    finalized_rows = [
        {
            "open_time": entry["open_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "close_time": entry["close_time"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "opening_price": str(entry["raw"]["opening_price"]),
            "high_price": str(entry["raw"]["high_price"]),
            "low_price": str(entry["raw"]["low_price"]),
            "trade_price": str(entry["raw"]["trade_price"]),
            "candle_acc_trade_price": str(entry["raw"]["candle_acc_trade_price"]),
            "candle_acc_trade_volume": str(entry["raw"]["candle_acc_trade_volume"]),
        }
        for entry in classified["finalized"]
    ]
    latest_close = classified["finalized"][-1]["close_time"] if classified["finalized"] else None
    fresh = freshness_status(latest_close, captured_at, max_staleness_seconds)
    return {
        "market": market,
        "timeframe": timeframe,
        "finalized_candle_count": len(finalized_rows),
        "in_progress_candle_count": len(classified["in_progress"]),
        "duplicate_row_count": classified["duplicate_row_count"],
        "finalized_candles": finalized_rows,
        "latest_finalized_close_time": (
            latest_close.strftime("%Y-%m-%dT%H:%M:%SZ") if latest_close is not None else None
        ),
        "freshness": fresh,
        "authority": dict(_EVIDENCE_AUTHORITY),
    }


# ---------------------------------------------------------------------------
# Orderbook: spread / depth / slippage
# ---------------------------------------------------------------------------

def compute_depth(orderbook_units: list, levels: int) -> dict:
    if not orderbook_units:
        raise MarketEvidenceError("ORDERBOOK_UNITS_EMPTY")
    taken = orderbook_units[:levels]
    bid_depth = Decimal(0)
    ask_depth = Decimal(0)
    for unit in taken:
        bid_depth += _decimal(unit["bid_price"], "bid_price") * _decimal(unit["bid_size"], "bid_size")
        ask_depth += _decimal(unit["ask_price"], "ask_price") * _decimal(unit["ask_size"], "ask_size")
    return {
        "levels_requested": levels,
        "levels_available": len(taken),
        "bid_depth_krw": str(bid_depth),
        "ask_depth_krw": str(ask_depth),
    }


def build_orderbook_evidence(
    market: str, orderbook_row: dict, *,
    captured_at: dt.datetime, max_staleness_seconds: int,
    depth_levels: int, slippage_notional_krw, max_spread_bps_normal, max_slippage_bps_normal,
) -> dict:
    units = orderbook_row.get("orderbook_units")
    if not units:
        raise MarketEvidenceError(f"ORDERBOOK_UNITS_MISSING:{market}")
    best = units[0]
    for field in ("bid_price", "ask_price"):
        if best.get(field) is None:
            raise MarketEvidenceError(f"ORDERBOOK_FIELD_MISSING:{field}")
    best_bid = _decimal(best["bid_price"], "best_bid")
    best_ask = _decimal(best["ask_price"], "best_ask")

    spread_bps = UPBIT_UNIVERSE._spread_bps(best_bid, best_ask)
    spread_status = "NOT_COMPUTABLE" if spread_bps is None else (
        "NORMAL" if spread_bps <= _decimal(max_spread_bps_normal, "max_spread_bps_normal") else "ABNORMAL_EXCLUDED"
    )

    depth = compute_depth(units, depth_levels)

    ask_levels = [{"price": unit["ask_price"], "size": unit["ask_size"]} for unit in units]
    slippage_bps = UPBIT_UNIVERSE._estimate_slippage_bps(
        ask_levels, best_ask, _decimal(slippage_notional_krw, "slippage_notional_krw")
    )
    slippage_status = "NOT_COMPUTABLE" if slippage_bps is None else (
        "NORMAL" if slippage_bps <= _decimal(max_slippage_bps_normal, "max_slippage_bps_normal") else "ABNORMAL_EXCLUDED"
    )

    timestamp_ms = orderbook_row.get("timestamp")
    reference_time = None
    if isinstance(timestamp_ms, (int, float)):
        reference_time = dt.datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    fresh = freshness_status(reference_time, captured_at, max_staleness_seconds)

    return {
        "market": market,
        "best_bid": str(best_bid),
        "best_ask": str(best_ask),
        "spread_bps": str(spread_bps) if spread_bps is not None else None,
        "spread_status": spread_status,
        "depth": depth,
        "slippage_estimate_notional_krw": str(_decimal(slippage_notional_krw, "slippage_notional_krw")),
        "slippage_bps": str(slippage_bps) if slippage_bps is not None else None,
        "slippage_status": slippage_status,
        "freshness": fresh,
        "authority": dict(_EVIDENCE_AUTHORITY),
    }


# ---------------------------------------------------------------------------
# Trade ticks
# ---------------------------------------------------------------------------

REQUIRED_TRADE_FIELDS = ("trade_price", "trade_volume", "timestamp", "ask_bid")


def build_trades_evidence(
    market: str, raw_trades: list, *, captured_at: dt.datetime, max_staleness_seconds: int,
) -> dict:
    if not isinstance(raw_trades, list) or not raw_trades:
        raise MarketEvidenceError(f"TRADES_EMPTY_OR_INVALID:{market}")
    for row in raw_trades:
        for field in REQUIRED_TRADE_FIELDS:
            if not isinstance(row, dict) or row.get(field) is None:
                raise MarketEvidenceError(f"TRADE_FIELD_MISSING:{field}")
    timestamps = [row["timestamp"] for row in raw_trades]
    latest_ms = max(timestamps)
    latest_trade_time = dt.datetime.fromtimestamp(latest_ms / 1000, tz=UTC)
    prices = [_decimal(row["trade_price"], "trade_price") for row in raw_trades]
    fresh = freshness_status(latest_trade_time, captured_at, max_staleness_seconds)
    return {
        "market": market,
        "trade_count": len(raw_trades),
        "latest_trade_time": latest_trade_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_trade_price": str(min(prices)),
        "max_trade_price": str(max(prices)),
        "freshness": fresh,
        "authority": dict(_EVIDENCE_AUTHORITY),
    }


# ---------------------------------------------------------------------------
# Full per-market packet
# ---------------------------------------------------------------------------

def build_market_evidence_packet(
    market: str, *,
    candles_by_timeframe: dict, trades: list, orderbook_row: dict,
    as_of: dt.datetime, captured_at: dt.datetime, policy: dict,
) -> dict:
    """Aggregate one market's full P4-07 evidence: finalized candles across
    every configured timeframe, trade-tick evidence, and orderbook
    spread/depth/slippage evidence. Every required slice is validated
    independently -- a gap in one slice (e.g. missing orderbook for this
    market) fails only this packet, never any other market's packet.
    """
    as_of = _require_aware(as_of, "AS_OF_NAIVE")
    captured_at = _require_aware(captured_at, "CAPTURED_AT_NAIVE")
    if captured_at < as_of:
        raise MarketEvidenceError("CAPTURED_AT_BEFORE_AS_OF")

    max_staleness_by_timeframe = policy["max_staleness_seconds_by_timeframe"]
    candles = {}
    for timeframe in finalization.TIMEFRAMES:
        raw = candles_by_timeframe.get(timeframe)
        if not raw:
            raise MarketEvidenceError(f"CANDLES_MISSING:{market}:{timeframe}")
        candles[timeframe] = build_candle_evidence(
            market, timeframe, raw, as_of=as_of, captured_at=captured_at,
            max_staleness_seconds=max_staleness_by_timeframe[timeframe],
        )

    trades_evidence = build_trades_evidence(
        market, trades, captured_at=captured_at,
        max_staleness_seconds=policy["max_trades_staleness_seconds"],
    )

    if not orderbook_row:
        raise MarketEvidenceError(f"ORDERBOOK_MISSING:{market}")
    orderbook_evidence = build_orderbook_evidence(
        market, orderbook_row, captured_at=captured_at,
        max_staleness_seconds=policy["max_orderbook_staleness_seconds"],
        depth_levels=policy["orderbook_depth_levels"],
        slippage_notional_krw=policy["paper_slippage_estimate_notional_krw"],
        max_spread_bps_normal=policy["max_spread_bps_normal"],
        max_slippage_bps_normal=policy["max_slippage_bps_normal"],
    )

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "market": market,
        "as_of": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_at": captured_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "policy_version": policy.get("policy_version"),
        "policy_ratified": policy.get("approval_status") == "RATIFIED",
        "candles": candles,
        "trades": trades_evidence,
        "orderbook": orderbook_evidence,
        "authority": dict(_EVIDENCE_AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet
