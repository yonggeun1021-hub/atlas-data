#!/usr/bin/env python3
"""Deterministic KRX completed-bar and session/freshness boundary.

This module performs no network or broker call.  It validates retained KIS
GET-only observations against a date-specific KRX session snapshot.  Every
decision is a pure function of the supplied packet and ``decision_at``.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_market_data_contract.json"
P9_FRESHNESS_PATH = ROOT / "execution" / "intraday_freshness.py"
KST = ZoneInfo("Asia/Seoul")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.\d+)?$")

_P9_SPEC = importlib.util.spec_from_file_location(
    "krx_market_data_p9_freshness", P9_FRESHNESS_PATH
)
if _P9_SPEC is None or _P9_SPEC.loader is None:
    raise RuntimeError("P9_FRESHNESS_MODULE_LOAD_FAILED")
P9_FRESHNESS = importlib.util.module_from_spec(_P9_SPEC)
_P9_SPEC.loader.exec_module(P9_FRESHNESS)
P9_CONTRACT = P9_FRESHNESS.load_contract()


class KrxMarketDataError(ValueError):
    """Fail-closed contract violation."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KrxMarketDataError(f"JSON_READ_FAILED:{path}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise KrxMarketDataError("CONTRACT_NOT_OBJECT")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "krx_completed_market_data/1"
        or value.get("market") != "KOREA"
        or value.get("venue_scope") != "KRX_ONLY"
        or value.get("timezone") != "Asia/Seoul"
        or value.get("utc_offset") != "+09:00"
        or value.get("dst_observed_as_of") is not False
        or value.get("timezone_rules_as_of") != "2026-08-30"
    ):
        raise KrxMarketDataError("CONTRACT_IDENTITY_INVALID")
    if value.get("authority", {}).get("market_data_observation_only") is not True:
        raise KrxMarketDataError("CONTRACT_AUTHORITY_INVALID")
    if any(
        value["authority"].get(field) is not False
        for field in (
            "universe_eligibility_authorized", "candidate_authorized",
            "entry_authorized", "hold_exit_authorized", "action_authorized",
            "internal_virtual_fill_authorized", "kis_mock_order_authorized",
            "real_capital_authorized", "production_authorized", "trading_authorized",
        )
    ):
        raise KrxMarketDataError("CONTRACT_AUTHORITY_OPEN")
    if value.get("supported_timeframes", {}).get("4h") != {
        "required_for_consumer": False,
        "status": "UNRATIFIED_SESSION_BOUNDARY",
    }:
        raise KrxMarketDataError("FOUR_HOUR_BOUNDARY_INVALID")
    if value.get("freshness") != {
        "latest_bar_rule": "EXACT_LATEST_COMPLETED_SESSION_INTERVAL",
        "p9_contract_path": "config/intraday_freshness_guard_contract.json",
        "p9_contract_version": "intraday_freshness_guard/1",
        "repository_default_policy": "ABSENT",
        "policy_requirement": "EXTERNAL_RATIFIED_POLICY_REQUIRED",
        "official_provider_sla": "UNKNOWN",
    }:
        raise KrxMarketDataError("FRESHNESS_CONTRACT_INVALID")
    return copy.deepcopy(value)


def _instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise KrxMarketDataError(code)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise KrxMarketDataError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KrxMarketDataError(code)
    return parsed


def _date(value: object, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise KrxMarketDataError(code)
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KrxMarketDataError(code) from exc


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise KrxMarketDataError(code)
    return value


def _positive_decimal(value: object, code: str, *, allow_zero: bool = False) -> str:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise KrxMarketDataError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise KrxMarketDataError(code) from exc
    if parsed < 0 or (not allow_zero and parsed == 0):
        raise KrxMarketDataError(code)
    return value


def _utc_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _p9_freshness(
    *,
    identity: str,
    price: str,
    volume: str,
    provider_at: dt.datetime,
    received_at: dt.datetime,
    observed_at: dt.datetime,
    source_ref: str,
    source_sha256: str,
    policy: object,
) -> dict:
    asset_id = "KRX.DATA." + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24].upper()
    batch = {
        "schema_version": P9_CONTRACT["snapshot_schema_version"],
        "contract_version": P9_CONTRACT["contract_version"],
        "batch_id": "KRX.MARKET.DATA.FRESHNESS",
        "observed_at": _utc_z(observed_at),
        "quotes": [{
            "asset_id": asset_id,
            "market": "KOREA",
            "price": price,
            "volume": volume,
            "quote_currency": "KRW",
            "provider_id": "KIS_OPEN_API",
            "provider_timestamp": _utc_z(provider_at),
            "received_at": _utc_z(received_at),
            "source_ref": source_ref,
            "source_sha256": source_sha256,
        }],
        "authority": copy.deepcopy(P9_CONTRACT["input_authority"]),
    }
    batch["packet_sha256"] = P9_FRESHNESS.payload_sha256(batch)
    try:
        return P9_FRESHNESS.evaluate_freshness(batch, policy, P9_CONTRACT)
    except (ValueError, TypeError) as exc:
        raise KrxMarketDataError(f"P9_FRESHNESS_POLICY_OR_INPUT_INVALID:{exc}") from exc


def _source(value: object, decision_at: dt.datetime, close_at: dt.datetime) -> dict:
    required = {
        "provider_id", "endpoint_id", "observed_at", "available_at",
        "generated_at", "snapshot_ref", "snapshot_sha256", "capture_kind",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise KrxMarketDataError("SOURCE_FIELDS_INVALID")
    if value["provider_id"] != "KIS_OPEN_API":
        raise KrxMarketDataError("SOURCE_PROVIDER_INVALID")
    if value["endpoint_id"] not in {
        "FHKST03010230", "FHKST03010200", "FHKST03010100"
    }:
        raise KrxMarketDataError("SOURCE_ENDPOINT_INVALID")
    observed = _instant(value["observed_at"], "SOURCE_OBSERVED_AT_INVALID")
    available = _instant(value["available_at"], "SOURCE_AVAILABLE_AT_INVALID")
    generated = _instant(value["generated_at"], "SOURCE_GENERATED_AT_INVALID")
    if not close_at <= observed <= available <= generated <= decision_at:
        raise KrxMarketDataError("SOURCE_TIME_ORDER_INVALID")
    if value["capture_kind"] not in {"ORIGINAL", "BACKFILL"}:
        raise KrxMarketDataError("SOURCE_CAPTURE_KIND_INVALID")
    if not isinstance(value["snapshot_ref"], str) or not value["snapshot_ref"]:
        raise KrxMarketDataError("SOURCE_SNAPSHOT_REF_INVALID")
    _sha(value["snapshot_sha256"], "SOURCE_SNAPSHOT_SHA_INVALID")
    return {
        **copy.deepcopy(value),
        "_observed": observed,
        "_available": available,
        "_generated": generated,
    }


def validate_calendar(value: object, decision_at: dt.datetime, contract: dict) -> dict:
    required = {
        "session_date", "status", "timezone", "open_at", "close_at",
        "observed_at", "available_at", "source_ref", "source_sha256",
        "provider_id", "market_rule_source",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise KrxMarketDataError("CALENDAR_FIELDS_INVALID")
    session_date = _date(value["session_date"], "CALENDAR_DATE_INVALID")
    if value["timezone"] != contract["timezone"]:
        raise KrxMarketDataError("CALENDAR_TIMEZONE_INVALID")
    if value["status"] not in contract["calendar"]["allowed_statuses"]:
        raise KrxMarketDataError("CALENDAR_STATUS_INVALID")
    observed = _instant(value["observed_at"], "CALENDAR_OBSERVED_AT_INVALID")
    available = _instant(value["available_at"], "CALENDAR_AVAILABLE_AT_INVALID")
    if observed > available or available > decision_at:
        raise KrxMarketDataError("CALENDAR_TIME_ORDER_INVALID")
    if value["provider_id"] != contract["calendar"]["open_source_identity"]:
        raise KrxMarketDataError("CALENDAR_PROVIDER_INVALID")
    if value["market_rule_source"] != contract["calendar"]["market_rule_identity"]:
        raise KrxMarketDataError("CALENDAR_RULE_SOURCE_INVALID")
    _sha(value["source_sha256"], "CALENDAR_SOURCE_SHA_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"]:
        raise KrxMarketDataError("CALENDAR_SOURCE_REF_INVALID")
    if value["status"] == "OPEN_REGULAR":
        expected_open = dt.datetime.combine(
            session_date, dt.time.fromisoformat(contract["regular_session"]["open"]), KST
        )
        expected_close = dt.datetime.combine(
            session_date, dt.time.fromisoformat(contract["regular_session"]["close"]), KST
        )
        opened = _instant(value["open_at"], "CALENDAR_OPEN_INVALID")
        closed = _instant(value["close_at"], "CALENDAR_CLOSE_INVALID")
        if opened != expected_open or closed != expected_close:
            raise KrxMarketDataError("SPECIAL_SESSION_UNRATIFIED")
    elif value["open_at"] is not None or value["close_at"] is not None:
        raise KrxMarketDataError("CLOSED_OR_UNKNOWN_SESSION_HAS_BOUNDS")
    else:
        opened = closed = None
    return {
        **copy.deepcopy(value),
        "_date": session_date,
        "_open": opened,
        "_close": closed,
    }


def expected_intervals(
    timeframe: str,
    calendar: dict,
    decision_at: dt.datetime,
    contract: dict | None = None,
) -> list[tuple[dt.datetime, dt.datetime]]:
    contract = load_contract() if contract is None else contract
    if timeframe == "4h":
        raise KrxMarketDataError("FOUR_HOUR_SESSION_BOUNDARY_UNRATIFIED")
    if timeframe not in {"15m", "1h", "1d"}:
        raise KrxMarketDataError("TIMEFRAME_UNSUPPORTED")
    checked = validate_calendar(calendar, decision_at, contract)
    if checked["status"] != "OPEN_REGULAR":
        return []
    opened, closed = checked["_open"], checked["_close"]
    seconds = contract["supported_timeframes"][timeframe]["duration_seconds"]
    if timeframe == "1d":
        return [(opened, closed)] if closed <= decision_at else []
    step = dt.timedelta(seconds=seconds)
    intervals = []
    current = opened
    while current + step <= closed and current + step <= decision_at:
        intervals.append((current, current + step))
        current += step
    return intervals


def _adjustment(value: object, price_basis: str, decision_at: dt.datetime) -> dict:
    required = {
        "status", "factor", "action_refs", "snapshot_ref",
        "snapshot_sha256", "available_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise KrxMarketDataError("ADJUSTMENT_FIELDS_INVALID")
    if not isinstance(value["action_refs"], list) or any(
        not isinstance(ref, str) or not ref for ref in value["action_refs"]
    ):
        raise KrxMarketDataError("ADJUSTMENT_REFS_INVALID")
    if price_basis == "RAW":
        if value["status"] not in {"NONE", "DISCLOSED_NOT_APPLIED"}:
            raise KrxMarketDataError("RAW_ADJUSTMENT_STATUS_INVALID")
        if any(value[field] is not None for field in (
            "factor", "snapshot_ref", "snapshot_sha256", "available_at"
        )):
            raise KrxMarketDataError("RAW_ADJUSTMENT_METADATA_INVALID")
    elif price_basis == "ADJUSTED":
        if value["status"] != "APPLIED" or not value["action_refs"]:
            raise KrxMarketDataError("ADJUSTED_ACTION_EVIDENCE_REQUIRED")
        _positive_decimal(value["factor"], "ADJUSTMENT_FACTOR_INVALID")
        if not isinstance(value["snapshot_ref"], str) or not value["snapshot_ref"]:
            raise KrxMarketDataError("ADJUSTMENT_SNAPSHOT_REF_INVALID")
        _sha(value["snapshot_sha256"], "ADJUSTMENT_SNAPSHOT_SHA_INVALID")
        available = _instant(value["available_at"], "ADJUSTMENT_AVAILABLE_AT_INVALID")
        if available > decision_at:
            raise KrxMarketDataError("CORPORATE_ACTION_NOT_POINT_IN_TIME_AVAILABLE")
    else:
        raise KrxMarketDataError("PRICE_BASIS_INVALID")
    return copy.deepcopy(value)


def _bar(value: object, timeframe: str, price_basis: str, decision_at: dt.datetime) -> dict:
    required = {
        "open_at", "close_at", "open", "high", "low", "close", "volume",
        "source", "adjustment",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise KrxMarketDataError("BAR_FIELDS_INVALID")
    opened = _instant(value["open_at"], "BAR_OPEN_INVALID")
    closed = _instant(value["close_at"], "BAR_CLOSE_INVALID")
    if opened.astimezone(KST).utcoffset() != dt.timedelta(hours=9):
        raise KrxMarketDataError("DST_OR_OFFSET_INVALID")
    for field in ("open", "high", "low", "close"):
        _positive_decimal(value[field], f"BAR_{field.upper()}_INVALID")
    _positive_decimal(value["volume"], "BAR_VOLUME_INVALID", allow_zero=True)
    if not Decimal(value["low"]) <= min(Decimal(value["open"]), Decimal(value["close"])):
        raise KrxMarketDataError("BAR_OHLC_INVALID")
    if not Decimal(value["high"]) >= max(Decimal(value["open"]), Decimal(value["close"])):
        raise KrxMarketDataError("BAR_OHLC_INVALID")
    source = _source(value["source"], decision_at, closed)
    if timeframe in {"15m", "1h"} and source["endpoint_id"] not in {
        "FHKST03010230", "FHKST03010200"
    }:
        raise KrxMarketDataError("INTRADAY_SOURCE_ENDPOINT_INVALID")
    if timeframe == "1d" and source["endpoint_id"] != "FHKST03010100":
        raise KrxMarketDataError("DAILY_SOURCE_ENDPOINT_INVALID")
    if timeframe != "1d" and price_basis != "RAW":
        raise KrxMarketDataError("INTRADAY_ADJUSTED_SERIES_UNSUPPORTED")
    adjustment = _adjustment(value["adjustment"], price_basis, decision_at)
    return {
        **copy.deepcopy(value),
        "_open": opened,
        "_close": closed,
        "_source": source,
        "_adjustment": adjustment,
    }


def aggregate_normalized_minutes(
    value: object,
    timeframe: str,
    calendar: dict,
    decision_at: dt.datetime,
    contract: dict | None = None,
) -> dict:
    """Aggregate complete, normalized KIS one-minute rows into 15m/1h bars.

    The official sample does not settle whether ``stck_cntg_hour`` labels the
    start or end of a minute.  Therefore raw KIS rows are not accepted here:
    an upstream adapter must carry a separately ratified
    ``INTERVAL_START_RATIFIED`` mapping. Missing minutes are never filled with
    the previous price and incomplete buckets are omitted for the downstream
    gap gate to reject.
    """
    contract = load_contract() if contract is None else contract
    required = {
        "asset_id", "price_basis", "timestamp_semantics", "minutes", "source",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise KrxMarketDataError("NORMALIZED_MINUTE_PACKET_FIELDS_INVALID")
    if timeframe not in {"15m", "1h"}:
        raise KrxMarketDataError("MINUTE_AGGREGATION_TIMEFRAME_INVALID")
    if value["price_basis"] != "RAW":
        raise KrxMarketDataError("INTRADAY_ADJUSTED_SERIES_UNSUPPORTED")
    if value["timestamp_semantics"] != "INTERVAL_START_RATIFIED":
        raise KrxMarketDataError("KIS_MINUTE_TIMESTAMP_SEMANTICS_UNKNOWN")
    if not isinstance(value["minutes"], list):
        raise KrxMarketDataError("NORMALIZED_MINUTES_NOT_LIST")
    intervals = expected_intervals(timeframe, calendar, decision_at, contract)
    minute_step = dt.timedelta(minutes=1)
    allowed_minutes = {
        opened + index * minute_step
        for opened, closed in intervals
        for index in range(int((closed - opened).total_seconds() // 60))
    }
    minute_fields = {"interval_start", "open", "high", "low", "close", "volume"}
    by_minute: dict[dt.datetime, dict] = {}
    for raw in value["minutes"]:
        if not isinstance(raw, dict) or set(raw) != minute_fields:
            raise KrxMarketDataError("NORMALIZED_MINUTE_FIELDS_INVALID")
        started = _instant(raw["interval_start"], "NORMALIZED_MINUTE_TIME_INVALID")
        if started not in allowed_minutes:
            raise KrxMarketDataError("MINUTE_OUT_OF_SESSION_OR_PARTIAL")
        for field in ("open", "high", "low", "close"):
            _positive_decimal(raw[field], f"MINUTE_{field.upper()}_INVALID")
        _positive_decimal(raw["volume"], "MINUTE_VOLUME_INVALID", allow_zero=True)
        if started in by_minute:
            if canonical_json(by_minute[started]) != canonical_json(raw):
                raise KrxMarketDataError("CONFLICTING_MINUTE_DUPLICATE")
            continue
        by_minute[started] = copy.deepcopy(raw)
    bars = []
    adjustment = {
        "status": "NONE", "factor": None, "action_refs": [],
        "snapshot_ref": None, "snapshot_sha256": None, "available_at": None,
    }
    for opened, closed in intervals:
        starts = [
            opened + index * minute_step
            for index in range(int((closed - opened).total_seconds() // 60))
        ]
        if any(started not in by_minute for started in starts):
            continue
        rows = [by_minute[started] for started in starts]
        bars.append({
            "open_at": opened.isoformat(timespec="seconds"),
            "close_at": closed.isoformat(timespec="seconds"),
            "open": rows[0]["open"],
            "high": str(max(Decimal(row["high"]) for row in rows)),
            "low": str(min(Decimal(row["low"]) for row in rows)),
            "close": rows[-1]["close"],
            "volume": str(sum((Decimal(row["volume"]) for row in rows), Decimal("0"))),
            "source": copy.deepcopy(value["source"]),
            "adjustment": copy.deepcopy(adjustment),
        })
    return {
        "asset_id": value["asset_id"],
        "timeframe": timeframe,
        "price_basis": "RAW",
        "bars": bars,
    }


def assess_series(
    series: object,
    calendar: dict,
    decision_at: dt.datetime,
    freshness_policy: object,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    required = {"asset_id", "timeframe", "price_basis", "bars"}
    if not isinstance(series, dict) or set(series) != required:
        raise KrxMarketDataError("SERIES_FIELDS_INVALID")
    timeframe = series["timeframe"]
    if timeframe == "4h":
        raise KrxMarketDataError("FOUR_HOUR_SESSION_BOUNDARY_UNRATIFIED")
    if series["price_basis"] not in contract["price_basis"]["allowed"]:
        raise KrxMarketDataError("PRICE_BASIS_INVALID")
    if not isinstance(series["asset_id"], str) or not series["asset_id"]:
        raise KrxMarketDataError("ASSET_ID_INVALID")
    if not isinstance(series["bars"], list):
        raise KrxMarketDataError("BARS_NOT_LIST")
    expected = expected_intervals(timeframe, calendar, decision_at, contract)
    expected_set = set(expected)
    by_interval: dict[tuple[dt.datetime, dt.datetime], dict] = {}
    exact_duplicates = 0
    reasons: list[str] = []
    for raw in series["bars"]:
        try:
            row = _bar(raw, timeframe, series["price_basis"], decision_at)
        except KrxMarketDataError as exc:
            reasons.append(str(exc))
            continue
        key = (row["_open"], row["_close"])
        if key not in expected_set:
            reasons.append(
                "PARTIAL_OR_OUT_OF_SESSION_BAR:" + row["_open"].isoformat()
            )
            continue
        if key in by_interval:
            if canonical_json(raw) == canonical_json(by_interval[key]["raw"]):
                exact_duplicates += 1
            else:
                reasons.append("CONFLICTING_DUPLICATE:" + row["_open"].isoformat())
            continue
        by_interval[key] = {"row": row, "raw": copy.deepcopy(raw)}
    missing = [item for item in expected if item not in by_interval]
    reasons.extend("GAP:" + opened.isoformat() for opened, _ in missing)
    ordered = [by_interval[item]["raw"] for item in expected if item in by_interval]
    freshness_result = None
    if ordered and not missing:
        latest = by_interval[expected[-1]]["row"]
        source = latest["_source"]
        freshness_packet = _p9_freshness(
            identity=f"{series['asset_id']}:{timeframe}:{latest['open_at']}",
            price=latest["close"],
            volume=latest["volume"],
            provider_at=source["_observed"],
            received_at=source["_available"],
            observed_at=decision_at,
            source_ref=source["snapshot_ref"],
            source_sha256=source["snapshot_sha256"],
            policy=freshness_policy,
        )
        freshness_result = freshness_packet["results"][0]
        reasons.extend(
            "P9_01_" + reason for reason in freshness_result["stale_reasons"]
        )
    unique_reasons = sorted(set(reasons))
    return {
        "asset_id": series["asset_id"],
        "timeframe": timeframe,
        "price_basis": series["price_basis"],
        "status": "PASS" if not unique_reasons and bool(expected) else "BLOCKED",
        "freshness_status": "FRESH" if not unique_reasons and bool(expected) else "STALE_OR_UNKNOWN",
        "expected_interval_count": len(expected),
        "accepted_bar_count": len(ordered),
        "exact_duplicate_count": exact_duplicates,
        "p9_freshness": freshness_result,
        "p9_policy_lineage": (
            {
                "policy_id": freshness_packet["policy_id"],
                "policy_sha256": freshness_packet["lineage"]["policy_sha256"],
            }
            if freshness_result is not None else None
        ),
        "bars": ordered,
        "reasons": unique_reasons or ([] if expected else ["NO_COMPLETED_INTERVAL"]),
    }


def replay_visible_bars(bars: list[dict], replay_as_of: dt.datetime) -> list[dict]:
    """Return only rows whose source and adjustment were available at replay time."""
    visible = []
    for row in bars:
        source = row.get("source", {})
        try:
            available = _instant(source.get("available_at"), "SOURCE_AVAILABLE_AT_INVALID")
        except KrxMarketDataError:
            continue
        adjustment = row.get("adjustment", {})
        adjustment_at = adjustment.get("available_at")
        if available > replay_as_of:
            continue
        if adjustment_at is not None and _instant(
            adjustment_at, "ADJUSTMENT_AVAILABLE_AT_INVALID"
        ) > replay_as_of:
            continue
        visible.append(copy.deepcopy(row))
    return visible


def assess_market_state(
    value: object,
    calendar: dict,
    decision_at: dt.datetime,
    freshness_policy: object,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    checked_calendar = validate_calendar(calendar, decision_at, contract)
    if checked_calendar["status"] == "UNKNOWN":
        return {"market_operability": "UNKNOWN", "reasons": ["SESSION_UNKNOWN"], "universe_eligibility": None}
    if checked_calendar["status"] == "CLOSED" or not (
        checked_calendar["_open"] <= decision_at < checked_calendar["_close"]
    ):
        return {"market_operability": "CLOSED_SESSION", "reasons": [], "universe_eligibility": None}
    required = {
        "asset_id", "as_of", "available_at", "source_ref", "source_sha256",
        "provider_id", "price_limits", "tick_size", "volatility_interruption",
        "trading_halt", "market_circuit_breaker",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise KrxMarketDataError("MARKET_STATE_FIELDS_INVALID")
    as_of = _instant(value["as_of"], "MARKET_STATE_AS_OF_INVALID")
    available = _instant(value["available_at"], "MARKET_STATE_AVAILABLE_AT_INVALID")
    if as_of > available or available > decision_at:
        raise KrxMarketDataError("MARKET_STATE_TIME_ORDER_INVALID")
    if value["provider_id"] != "KIS_OPEN_API":
        raise KrxMarketDataError("MARKET_STATE_PROVIDER_INVALID")
    _sha(value["source_sha256"], "MARKET_STATE_SOURCE_SHA_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"]:
        raise KrxMarketDataError("MARKET_STATE_SOURCE_REF_INVALID")
    state_freshness_packet = _p9_freshness(
        identity=f"{value['asset_id']}:MARKET_STATE:{value['as_of']}",
        price=value.get("price_limits", {}).get("base_price", "1"),
        volume="0",
        provider_at=as_of,
        received_at=available,
        observed_at=decision_at,
        source_ref=value["source_ref"],
        source_sha256=value["source_sha256"],
        policy=freshness_policy,
    )
    state_freshness = state_freshness_packet["results"][0]
    if state_freshness["freshness_status"] != "FRESH":
        return {
            "market_operability": "UNKNOWN",
            "reasons": ["P9_01_" + reason for reason in state_freshness["stale_reasons"]],
            "p9_freshness": state_freshness,
            "p9_policy_lineage": {
                "policy_id": state_freshness_packet["policy_id"],
                "policy_sha256": state_freshness_packet["lineage"]["policy_sha256"],
            },
            "universe_eligibility": None,
        }
    reasons = []
    price_limits = value["price_limits"]
    tick = value["tick_size"]
    vi = value["volatility_interruption"]
    halt = value["trading_halt"]
    circuit = value["market_circuit_breaker"]
    if not isinstance(price_limits, dict) or price_limits.get("status") != "KNOWN":
        reasons.append("PRICE_LIMITS_UNKNOWN")
    else:
        for field in ("base_price", "lower_price", "upper_price"):
            _positive_decimal(price_limits.get(field), f"PRICE_LIMIT_{field.upper()}_INVALID")
        if not (
            Decimal(price_limits["lower_price"])
            <= Decimal(price_limits["base_price"])
            <= Decimal(price_limits["upper_price"])
        ):
            raise KrxMarketDataError("PRICE_LIMIT_ORDER_INVALID")
    if not isinstance(tick, dict) or tick.get("status") != "KNOWN":
        reasons.append("TICK_SIZE_UNKNOWN")
    else:
        _positive_decimal(tick.get("krw"), "TICK_SIZE_INVALID")
        if isinstance(price_limits, dict) and price_limits.get("status") == "KNOWN":
            tick_value = Decimal(tick["krw"])
            if any(
                Decimal(price_limits[field]) % tick_value != 0
                for field in ("base_price", "lower_price", "upper_price")
            ):
                raise KrxMarketDataError("PRICE_LIMIT_TICK_ALIGNMENT_INVALID")
    if not isinstance(vi, dict) or vi.get("status") not in {"INACTIVE", "ACTIVE", "UNKNOWN"}:
        raise KrxMarketDataError("VI_STATUS_INVALID")
    if not isinstance(halt, dict) or halt.get("status") not in {"TRADING", "HALTED", "UNKNOWN"}:
        raise KrxMarketDataError("HALT_STATUS_INVALID")
    if not isinstance(circuit, dict) or circuit.get("status") not in {"INACTIVE", "ACTIVE", "UNKNOWN"}:
        raise KrxMarketDataError("CIRCUIT_STATUS_INVALID")
    if vi.get("status") == "UNKNOWN":
        reasons.append("VI_UNKNOWN")
    if halt.get("status") == "UNKNOWN":
        reasons.append("TRADING_HALT_UNKNOWN")
    if circuit.get("status") == "UNKNOWN":
        reasons.append("CIRCUIT_BREAKER_UNKNOWN")
    blocked = halt.get("status") == "HALTED" or circuit.get("status") == "ACTIVE"
    if blocked:
        operability = "NOT_ORDERABLE_MARKET_STATE"
    elif reasons:
        operability = "UNKNOWN"
    elif vi.get("status") == "ACTIVE":
        operability = "VI_CALL_AUCTION_OBSERVATION_ONLY"
    else:
        operability = "ORDERABLE_OBSERVATION_ONLY"
    return {
        "market_operability": operability,
        "reasons": sorted(reasons),
        "as_of": value["as_of"],
        "source_ref": value["source_ref"],
        "source_sha256": value["source_sha256"],
        "p9_freshness": state_freshness,
        "p9_policy_lineage": {
            "policy_id": state_freshness_packet["policy_id"],
            "policy_sha256": state_freshness_packet["lineage"]["policy_sha256"],
        },
        "facts": {
            key: copy.deepcopy(value[key])
            for key in (
                "price_limits", "tick_size", "volatility_interruption",
                "trading_halt", "market_circuit_breaker",
            )
        },
        "universe_eligibility": None,
    }


def evaluate_packet(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    required = {
        "schema_version", "decision_at", "calendar", "series", "market_state",
        "freshness_policy", "authority",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise KrxMarketDataError("PACKET_FIELDS_INVALID")
    if value["schema_version"] != "krx_market_data_input/1":
        raise KrxMarketDataError("PACKET_SCHEMA_INVALID")
    if value["authority"] != contract["authority"]:
        raise KrxMarketDataError("PACKET_AUTHORITY_INVALID")
    decision_at = _instant(value["decision_at"], "DECISION_AT_INVALID")
    if not isinstance(value["series"], list) or not value["series"]:
        raise KrxMarketDataError("PACKET_SERIES_INVALID")
    checked_calendar = validate_calendar(value["calendar"], decision_at, contract)
    results = [
        assess_series(
            row, value["calendar"], decision_at, value["freshness_policy"], contract
        )
        for row in value["series"]
    ]
    market_state = assess_market_state(
        value["market_state"], value["calendar"], decision_at,
        value["freshness_policy"], contract,
    )
    reasons = sorted({reason for row in results for reason in row["reasons"]})
    if checked_calendar["status"] == "UNKNOWN":
        reasons.append("SESSION_UNKNOWN")
    status = "PASS" if all(row["status"] == "PASS" for row in results) else "BLOCKED"
    result = {
        "schema_version": "krx_market_data_result/1",
        "contract_version": contract["contract_version"],
        "decision_at": value["decision_at"],
        "session_date": value["calendar"]["session_date"],
        "session_status": checked_calendar["status"],
        "status": status,
        "freshness_status": "FRESH" if status == "PASS" else "STALE_OR_UNKNOWN",
        "series": results,
        "market_state": market_state,
        "reasons": sorted(set(reasons)),
        "four_hour_bar_required": False,
        "authority": copy.deepcopy(contract["authority"]),
    }
    result["packet_sha256"] = payload_sha256(result)
    return result
