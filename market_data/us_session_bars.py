#!/usr/bin/env python3
"""US regular-session completed-bar validation for PAPER data review.

The path is deliberately provider-neutral and network-free.  Date-specific
official calendar evidence, a ratified P9 freshness policy, source timestamps,
and corporate-action/symbol lineage are caller inputs.  Missing evidence never
becomes a weekday, no-halt, no-delisting, or fresh-data assumption.
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
CONTRACT_PATH = ROOT / "config" / "us_completed_market_data_contract.json"
P9_FRESHNESS_PATH = ROOT / "execution" / "intraday_freshness.py"
NY = ZoneInfo("America/New_York")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{1,127}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.\d+)?$")

_P9_SPEC = importlib.util.spec_from_file_location(
    "us_market_data_p9_freshness", P9_FRESHNESS_PATH
)
if _P9_SPEC is None or _P9_SPEC.loader is None:
    raise RuntimeError("P9_FRESHNESS_MODULE_LOAD_FAILED")
P9_FRESHNESS = importlib.util.module_from_spec(_P9_SPEC)
_P9_SPEC.loader.exec_module(P9_FRESHNESS)
P9_CONTRACT = P9_FRESHNESS.load_contract()


class UsMarketDataError(ValueError):
    """A US market-data contract invariant failed closed."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsMarketDataError(f"JSON_READ_FAILED:{path}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise UsMarketDataError("CONTRACT_NOT_OBJECT")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "us_completed_market_data/1"
        or value.get("market") != "US"
        or value.get("timezone") != "America/New_York"
        or value.get("dst_rule") != "IANA_TZDB_NO_FIXED_OFFSET"
    ):
        raise UsMarketDataError("CONTRACT_IDENTITY_INVALID")
    if set(value.get("supported_timeframes", {})) != {"15m", "1h", "1d"}:
        raise UsMarketDataError("CONTRACT_TIMEFRAMES_INVALID")
    if value.get("freshness", {}).get("repository_default_policy") != "ABSENT":
        raise UsMarketDataError("CONTRACT_FRESHNESS_DEFAULT_OPEN")
    if value.get("price_basis", {}).get("allowed") != ["RAW"]:
        raise UsMarketDataError("CONTRACT_PRICE_BASIS_INVALID")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("market_data_observation_only") is not True:
        raise UsMarketDataError("CONTRACT_AUTHORITY_INVALID")
    if any(v is not False for k, v in authority.items() if k != "market_data_observation_only"):
        raise UsMarketDataError("CONTRACT_AUTHORITY_OPEN")
    return copy.deepcopy(value)


def _instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise UsMarketDataError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsMarketDataError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UsMarketDataError(code)
    return parsed


def _date(value: object, code: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise UsMarketDataError(code) from exc


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise UsMarketDataError(code)
    return value


def _token(value: object, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise UsMarketDataError(code)
    return value


def _number(value: object, code: str, *, zero: bool = False) -> Decimal:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise UsMarketDataError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise UsMarketDataError(code) from exc
    if parsed < 0 or (not zero and parsed == 0):
        raise UsMarketDataError(code)
    return parsed


def _utc_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ny_exact(value: object, code: str) -> dt.datetime:
    parsed = _instant(value, code)
    converted = parsed.astimezone(NY)
    if (
        parsed.utcoffset() != converted.utcoffset()
        or parsed.replace(tzinfo=None) != converted.replace(tzinfo=None)
    ):
        raise UsMarketDataError(f"{code}_NOT_NEW_YORK_OFFSET")
    return converted


def validate_calendar(value: object, decision_at: dt.datetime, contract: dict) -> dict:
    fields = {
        "session_date", "status", "timezone", "open_at", "close_at",
        "observed_at", "available_at", "source_class", "source_id",
        "source_ref", "source_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsMarketDataError("CALENDAR_FIELDS_INVALID")
    session_date = _date(value["session_date"], "CALENDAR_DATE_INVALID")
    if value["timezone"] != contract["timezone"]:
        raise UsMarketDataError("CALENDAR_TIMEZONE_INVALID")
    status = value["status"]
    if status not in contract["calendar_statuses"]:
        raise UsMarketDataError("CALENDAR_STATUS_INVALID")
    observed = _instant(value["observed_at"], "CALENDAR_OBSERVED_AT_INVALID")
    available = _instant(value["available_at"], "CALENDAR_AVAILABLE_AT_INVALID")
    if observed > available or available > decision_at:
        raise UsMarketDataError("CALENDAR_TIME_ORDER_INVALID")
    if value["source_class"] != "OFFICIAL_EXCHANGE_CALENDAR":
        raise UsMarketDataError("CALENDAR_SOURCE_NOT_OFFICIAL")
    _token(value["source_id"], "CALENDAR_SOURCE_ID_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"].strip():
        raise UsMarketDataError("CALENDAR_SOURCE_REF_INVALID")
    _sha(value["source_sha256"], "CALENDAR_SOURCE_SHA_INVALID")
    if status in {"CLOSED", "UNKNOWN"}:
        if value["open_at"] is not None or value["close_at"] is not None:
            raise UsMarketDataError("NON_OPEN_SESSION_HAS_HOURS")
        return {**copy.deepcopy(value), "_date": session_date, "_open": None, "_close": None}
    opened = _ny_exact(value["open_at"], "CALENDAR_OPEN_INVALID")
    closed = _ny_exact(value["close_at"], "CALENDAR_CLOSE_INVALID")
    if opened.date() != session_date or closed.date() != session_date or opened >= closed:
        raise UsMarketDataError("CALENDAR_SESSION_DATE_OR_ORDER_INVALID")
    expected_close = dt.time(16, 0) if status == "OPEN_REGULAR" else dt.time(13, 0)
    if opened.time() != dt.time(9, 30) or closed.time() != expected_close:
        raise UsMarketDataError("CALENDAR_SESSION_HOURS_INVALID")
    return {**copy.deepcopy(value), "_date": session_date, "_open": opened, "_close": closed}


def expected_intervals(
    timeframe: str, calendar: object, decision_at: dt.datetime, contract: dict | None = None
) -> list[tuple[dt.datetime, dt.datetime]]:
    contract = load_contract() if contract is None else contract
    checked = validate_calendar(calendar, decision_at, contract)
    if checked["status"] in {"CLOSED", "UNKNOWN"}:
        return []
    opened, closed = checked["_open"], checked["_close"]
    if timeframe == "1d":
        return [(opened, closed)] if closed <= decision_at else []
    seconds = contract["supported_timeframes"].get(timeframe, {}).get("duration_seconds")
    if type(seconds) is not int:
        raise UsMarketDataError("TIMEFRAME_INVALID")
    step = dt.timedelta(seconds=seconds)
    intervals = []
    cursor = opened
    while cursor + step <= closed:
        end = cursor + step
        if end <= decision_at:
            intervals.append((cursor, end))
        cursor = end
    return intervals


def _source(value: object, bar_close: dt.datetime, decision_at: dt.datetime) -> dict:
    fields = {
        "provider_id", "feed_scope", "observed_at", "available_at", "generated_at",
        "first_seen_at", "original_available_at", "capture_kind", "snapshot_ref",
        "snapshot_sha256", "redistribution_status",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise UsMarketDataError("SOURCE_FIELDS_INVALID")
    provider = _token(value["provider_id"], "SOURCE_PROVIDER_INVALID")
    feed_scope = _token(value["feed_scope"], "SOURCE_FEED_SCOPE_INVALID")
    observed = _instant(value["observed_at"], "SOURCE_OBSERVED_AT_INVALID")
    available = _instant(value["available_at"], "SOURCE_AVAILABLE_AT_INVALID")
    generated = _instant(value["generated_at"], "SOURCE_GENERATED_AT_INVALID")
    first_seen = _instant(value["first_seen_at"], "SOURCE_FIRST_SEEN_AT_INVALID")
    if not bar_close <= observed <= available <= generated <= decision_at:
        raise UsMarketDataError("SOURCE_TIME_ORDER_INVALID")
    if not available <= first_seen <= generated:
        raise UsMarketDataError("SOURCE_FIRST_SEEN_ORDER_INVALID")
    capture_kind = value["capture_kind"]
    original = value["original_available_at"]
    if capture_kind == "ORIGINAL":
        if original is not None:
            raise UsMarketDataError("ORIGINAL_CAPTURE_HAS_BACKFILL_TIME")
        visible_at = available
    elif capture_kind == "BACKFILL":
        if original is None:
            raise UsMarketDataError("BACKFILL_ORIGINAL_AVAILABILITY_UNKNOWN")
        visible_at = _instant(original, "BACKFILL_ORIGINAL_AVAILABLE_AT_INVALID")
        if visible_at < bar_close or visible_at > first_seen or visible_at > decision_at:
            raise UsMarketDataError("BACKFILL_ORIGINAL_AVAILABILITY_ORDER_INVALID")
    else:
        raise UsMarketDataError("SOURCE_CAPTURE_KIND_INVALID")
    if value["redistribution_status"] not in {"GRANTED", "NOT_GRANTED", "UNKNOWN"}:
        raise UsMarketDataError("SOURCE_REDISTRIBUTION_INVALID")
    if not isinstance(value["snapshot_ref"], str) or not value["snapshot_ref"].strip():
        raise UsMarketDataError("SOURCE_SNAPSHOT_REF_INVALID")
    _sha(value["snapshot_sha256"], "SOURCE_SNAPSHOT_SHA_INVALID")
    return {
        **copy.deepcopy(value), "provider_id": provider, "feed_scope": feed_scope,
        "_observed": observed, "_available": available, "_visible_at": visible_at,
    }


def _symbol_timeline(value: object, decision_at: dt.datetime) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise UsMarketDataError("SYMBOL_TIMELINE_INVALID")
    fields = {"symbol", "effective_from", "effective_to", "available_at", "source_ref", "source_sha256"}
    rows = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != fields:
            raise UsMarketDataError("SYMBOL_TIMELINE_FIELDS_INVALID")
        start = _instant(raw["effective_from"], "SYMBOL_EFFECTIVE_FROM_INVALID")
        end = None if raw["effective_to"] is None else _instant(raw["effective_to"], "SYMBOL_EFFECTIVE_TO_INVALID")
        available = _instant(raw["available_at"], "SYMBOL_AVAILABLE_AT_INVALID")
        if (end is not None and end <= start) or available > decision_at:
            raise UsMarketDataError("SYMBOL_TIMELINE_ORDER_INVALID")
        _token(raw["symbol"], "SYMBOL_INVALID")
        _sha(raw["source_sha256"], "SYMBOL_SOURCE_SHA_INVALID")
        if not isinstance(raw["source_ref"], str) or not raw["source_ref"].strip():
            raise UsMarketDataError("SYMBOL_SOURCE_REF_INVALID")
        rows.append({**copy.deepcopy(raw), "_start": start, "_end": end})
    rows.sort(key=lambda row: row["_start"])
    for prior, current in zip(rows, rows[1:]):
        if prior["_end"] is None or prior["_end"] > current["_start"]:
            raise UsMarketDataError("SYMBOL_TIMELINE_OVERLAP")
    return rows


def _corporate_actions(value: object, decision_at: dt.datetime) -> list[dict]:
    if not isinstance(value, list):
        raise UsMarketDataError("CORPORATE_ACTIONS_NOT_LIST")
    fields = {
        "action_id", "type", "status", "announced_at", "effective_at", "available_at",
        "factor", "from_symbol", "to_symbol", "source_ref", "source_sha256",
    }
    rows = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != fields:
            raise UsMarketDataError("CORPORATE_ACTION_FIELDS_INVALID")
        action_id = _token(raw["action_id"], "CORPORATE_ACTION_ID_INVALID")
        if action_id in seen:
            raise UsMarketDataError("CORPORATE_ACTION_DUPLICATE")
        seen.add(action_id)
        if raw["type"] not in {"SPLIT", "CASH_DIVIDEND", "SYMBOL_CHANGE"}:
            raise UsMarketDataError("CORPORATE_ACTION_TYPE_INVALID")
        if raw["status"] not in {"ANNOUNCED", "APPLIED"}:
            raise UsMarketDataError("CORPORATE_ACTION_STATUS_INVALID")
        announced = _instant(raw["announced_at"], "CORPORATE_ACTION_ANNOUNCED_AT_INVALID")
        effective = _instant(raw["effective_at"], "CORPORATE_ACTION_EFFECTIVE_AT_INVALID")
        available = _instant(raw["available_at"], "CORPORATE_ACTION_AVAILABLE_AT_INVALID")
        if announced > effective or announced > available or available > decision_at:
            raise UsMarketDataError("CORPORATE_ACTION_TIME_ORDER_INVALID")
        if effective <= decision_at and raw["status"] != "APPLIED":
            raise UsMarketDataError("CORPORATE_ACTION_EFFECTIVE_NOT_APPLIED")
        if effective > decision_at and raw["status"] != "ANNOUNCED":
            raise UsMarketDataError("FUTURE_CORPORATE_ACTION_APPLIED")
        if raw["type"] == "SPLIT":
            factor = _number(raw["factor"], "SPLIT_FACTOR_INVALID")
            if factor == 1 or raw["from_symbol"] is not None or raw["to_symbol"] is not None:
                raise UsMarketDataError("SPLIT_FIELDS_INVALID")
        elif raw["type"] == "SYMBOL_CHANGE":
            _token(raw["from_symbol"], "SYMBOL_CHANGE_FROM_INVALID")
            _token(raw["to_symbol"], "SYMBOL_CHANGE_TO_INVALID")
            if raw["from_symbol"] == raw["to_symbol"] or raw["factor"] is not None:
                raise UsMarketDataError("SYMBOL_CHANGE_FIELDS_INVALID")
        elif any(raw[field] is not None for field in ("factor", "from_symbol", "to_symbol")):
            raise UsMarketDataError("DIVIDEND_FIELDS_INVALID")
        _sha(raw["source_sha256"], "CORPORATE_ACTION_SOURCE_SHA_INVALID")
        if not isinstance(raw["source_ref"], str) or not raw["source_ref"].strip():
            raise UsMarketDataError("CORPORATE_ACTION_SOURCE_REF_INVALID")
        rows.append({**copy.deepcopy(raw), "_effective": effective, "_available": available})
    rows.sort(key=lambda row: (row["_effective"], row["action_id"]))
    return rows


def _symbol_for(moment: dt.datetime, timeline: list[dict]) -> str | None:
    matches = [
        row["symbol"] for row in timeline
        if row["_start"] <= moment and (row["_end"] is None or moment < row["_end"])
    ]
    return matches[0] if len(matches) == 1 else None


def _bar(value: object, timeframe: str, decision_at: dt.datetime, timeline: list[dict]) -> dict:
    fields = {"symbol", "open_at", "close_at", "open", "high", "low", "close", "volume", "source"}
    if not isinstance(value, dict) or set(value) != fields:
        raise UsMarketDataError("BAR_FIELDS_INVALID")
    opened = _ny_exact(value["open_at"], "BAR_OPEN_AT_INVALID")
    closed = _ny_exact(value["close_at"], "BAR_CLOSE_AT_INVALID")
    if opened >= closed or closed > decision_at:
        raise UsMarketDataError("BAR_TIME_ORDER_INVALID")
    expected_symbol = _symbol_for(opened, timeline)
    if expected_symbol is None or value["symbol"] != expected_symbol:
        raise UsMarketDataError("BAR_SYMBOL_TIMELINE_MISMATCH")
    prices = {field: _number(value[field], f"BAR_{field.upper()}_INVALID") for field in ("open", "high", "low", "close")}
    volume = _number(value["volume"], "BAR_VOLUME_INVALID", zero=True)
    if prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(prices["open"], prices["close"]) or prices["low"] > prices["high"]:
        raise UsMarketDataError("BAR_OHLC_RELATION_INVALID")
    source = _source(value["source"], closed, decision_at)
    return {**copy.deepcopy(value), "_open": opened, "_close": closed, "_source": source, "_volume": volume}


def _p9_freshness(asset_id: str, timeframe: str, bar: dict, decision_at: dt.datetime, policy: object) -> dict:
    source = bar["_source"]
    quote_id = "US.DATA." + hashlib.sha256(f"{asset_id}:{timeframe}:{bar['open_at']}".encode()).hexdigest()[:24].upper()
    batch = {
        "schema_version": P9_CONTRACT["snapshot_schema_version"],
        "contract_version": P9_CONTRACT["contract_version"],
        "batch_id": "US.MARKET.DATA.FRESHNESS",
        "observed_at": _utc_z(decision_at),
        "quotes": [{
            "asset_id": quote_id,
            "market": "US",
            "price": bar["close"],
            "volume": bar["volume"],
            "quote_currency": "USD",
            "provider_id": source["provider_id"],
            "provider_timestamp": _utc_z(source["_observed"]),
            "received_at": _utc_z(source["_available"]),
            "source_ref": source["snapshot_ref"],
            "source_sha256": source["snapshot_sha256"],
        }],
        "authority": copy.deepcopy(P9_CONTRACT["input_authority"]),
    }
    batch["packet_sha256"] = P9_FRESHNESS.payload_sha256(batch)
    return P9_FRESHNESS.evaluate_freshness(batch, policy, P9_CONTRACT)


def assess_series(
    value: object, calendar: object, decision_at: dt.datetime, freshness_policy: object,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    fields = {"asset_id", "timeframe", "price_basis", "symbol_timeline", "corporate_actions", "bars"}
    if not isinstance(value, dict) or set(value) != fields:
        raise UsMarketDataError("SERIES_FIELDS_INVALID")
    asset_id = _token(value["asset_id"], "ASSET_ID_INVALID")
    timeframe = value["timeframe"]
    if timeframe not in contract["supported_timeframes"]:
        raise UsMarketDataError("TIMEFRAME_INVALID")
    if value["price_basis"] != "RAW":
        raise UsMarketDataError("ADJUSTED_BARS_UNRATIFIED")
    timeline = _symbol_timeline(value["symbol_timeline"], decision_at)
    actions = _corporate_actions(value["corporate_actions"], decision_at)
    checked_calendar = validate_calendar(calendar, decision_at, contract)
    for action in actions:
        if checked_calendar["_open"] is not None and checked_calendar["_open"] < action["_effective"] < checked_calendar["_close"]:
            raise UsMarketDataError("CORPORATE_ACTION_INSIDE_SESSION")
        if action["type"] == "SYMBOL_CHANGE":
            before = _symbol_for(action["_effective"] - dt.timedelta(microseconds=1), timeline)
            after = _symbol_for(action["_effective"], timeline)
            if before != action["from_symbol"] or after != action["to_symbol"]:
                raise UsMarketDataError("SYMBOL_CHANGE_TIMELINE_MISMATCH")
    expected = expected_intervals(timeframe, calendar, decision_at, contract)
    if not isinstance(value["bars"], list):
        raise UsMarketDataError("BARS_NOT_LIST")
    expected_set = set(expected)
    accepted: dict[tuple[dt.datetime, dt.datetime], dict] = {}
    exact_duplicates = 0
    reasons: list[str] = []
    for raw in value["bars"]:
        try:
            row = _bar(raw, timeframe, decision_at, timeline)
        except UsMarketDataError as exc:
            reasons.append(str(exc))
            continue
        key = (row["_open"], row["_close"])
        if key not in expected_set:
            reasons.append("PARTIAL_OR_OUT_OF_SESSION_BAR:" + row["open_at"])
            continue
        if key in accepted:
            if canonical_json(raw) == canonical_json(accepted[key]["raw"]):
                exact_duplicates += 1
            else:
                reasons.append("CONFLICTING_DUPLICATE:" + row["open_at"])
            continue
        accepted[key] = {"row": row, "raw": copy.deepcopy(raw)}
    missing = [item for item in expected if item not in accepted]
    reasons.extend("GAP:" + opened.isoformat() for opened, _ in missing)
    ordered = [accepted[item]["raw"] for item in expected if item in accepted]
    freshness = None
    lineage = None
    if ordered and not missing:
        latest = accepted[expected[-1]]["row"]
        try:
            packet = _p9_freshness(asset_id, timeframe, latest, decision_at, freshness_policy)
            freshness = packet["results"][0]
            lineage = {"policy_id": packet["policy_id"], "policy_sha256": packet["lineage"]["policy_sha256"]}
            reasons.extend("P9_01_" + reason for reason in freshness["stale_reasons"])
        except (ValueError, TypeError) as exc:
            reasons.append("P9_FRESHNESS_POLICY_OR_INPUT_INVALID:" + str(exc))
    if not expected:
        reasons.append("NO_COMPLETED_INTERVAL")
    unique = sorted(set(reasons))
    return {
        "asset_id": asset_id,
        "timeframe": timeframe,
        "price_basis": "RAW",
        "status": "PASS" if not unique else "BLOCKED",
        "freshness_status": "FRESH" if not unique else "STALE_OR_UNKNOWN",
        "expected_interval_count": len(expected),
        "accepted_bar_count": len(ordered),
        "exact_duplicate_count": exact_duplicates,
        "feed_scopes": sorted({row["source"]["feed_scope"] for row in ordered}),
        "public_raw_persistence_authorized": False,
        "corporate_action_count": len(actions),
        "p9_freshness": freshness,
        "p9_policy_lineage": lineage,
        "bars": ordered,
        "reasons": unique,
    }


def replay_visible_series(value: object, replay_as_of: dt.datetime) -> dict:
    """Return only bars and identity/action facts visible by ``replay_as_of``."""
    if not isinstance(value, dict):
        raise UsMarketDataError("SERIES_NOT_OBJECT")
    result = copy.deepcopy(value)
    visible_bars = []
    for row in result.get("bars", []):
        source = row.get("source", {})
        try:
            available = _instant(source.get("available_at"), "SOURCE_AVAILABLE_AT_INVALID")
            original = source.get("original_available_at")
            if source.get("capture_kind") == "BACKFILL":
                if original is None:
                    continue
                available = _instant(original, "BACKFILL_ORIGINAL_AVAILABLE_AT_INVALID")
        except UsMarketDataError:
            continue
        if available <= replay_as_of:
            visible_bars.append(row)
    result["bars"] = visible_bars
    result["corporate_actions"] = [
        row for row in result.get("corporate_actions", [])
        if _instant(row.get("available_at"), "CORPORATE_ACTION_AVAILABLE_AT_INVALID") <= replay_as_of
    ]
    result["symbol_timeline"] = [
        row for row in result.get("symbol_timeline", [])
        if _instant(row.get("available_at"), "SYMBOL_AVAILABLE_AT_INVALID") <= replay_as_of
    ]
    return result


def evaluate_packet(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    fields = {"schema_version", "decision_at", "calendar", "series", "freshness_policy", "authority"}
    if not isinstance(value, dict) or set(value) != fields:
        raise UsMarketDataError("PACKET_FIELDS_INVALID")
    if value["schema_version"] != "us_market_data_input/1":
        raise UsMarketDataError("PACKET_SCHEMA_INVALID")
    if value["authority"] != contract["authority"]:
        raise UsMarketDataError("PACKET_AUTHORITY_INVALID")
    decision_at = _instant(value["decision_at"], "DECISION_AT_INVALID")
    calendar = validate_calendar(value["calendar"], decision_at, contract)
    if not isinstance(value["series"], list):
        raise UsMarketDataError("PACKET_SERIES_NOT_LIST")
    if calendar["status"] in {"CLOSED", "UNKNOWN"}:
        if any(row.get("bars") for row in value["series"] if isinstance(row, dict)):
            raise UsMarketDataError("BARS_PRESENT_FOR_NON_OPEN_SESSION")
        result = {
            "schema_version": "us_market_data_result/1",
            "contract_version": contract["contract_version"],
            "decision_at": value["decision_at"],
            "session_date": value["calendar"]["session_date"],
            "session_status": calendar["status"],
            "status": "CLOSED_SESSION" if calendar["status"] == "CLOSED" else "BLOCKED_UNKNOWN_SESSION",
            "freshness_status": "NOT_APPLICABLE" if calendar["status"] == "CLOSED" else "UNKNOWN",
            "series": [],
            "reasons": [] if calendar["status"] == "CLOSED" else ["SESSION_UNKNOWN"],
            "authority": copy.deepcopy(contract["authority"]),
        }
        result["packet_sha256"] = payload_sha256(result)
        return result
    timeframes = [row.get("timeframe") for row in value["series"] if isinstance(row, dict)]
    if sorted(timeframes) != ["15m", "1d", "1h"]:
        raise UsMarketDataError("PACKET_REQUIRED_TIMEFRAMES_INVALID")
    results = [
        assess_series(row, value["calendar"], decision_at, value["freshness_policy"], contract)
        for row in value["series"]
    ]
    status = "PASS" if all(row["status"] == "PASS" for row in results) else "BLOCKED"
    reasons = sorted({reason for row in results for reason in row["reasons"]})
    result = {
        "schema_version": "us_market_data_result/1",
        "contract_version": contract["contract_version"],
        "decision_at": value["decision_at"],
        "session_date": value["calendar"]["session_date"],
        "session_status": calendar["status"],
        "status": status,
        "freshness_status": "FRESH" if status == "PASS" else "STALE_OR_UNKNOWN",
        "series": results,
        "reasons": reasons,
        "authority": copy.deepcopy(contract["authority"]),
    }
    result["packet_sha256"] = payload_sha256(result)
    return result
