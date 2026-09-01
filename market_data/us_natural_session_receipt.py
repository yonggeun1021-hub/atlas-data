#!/usr/bin/env python3
"""Build a network-free US natural finished-session receipt.

The producer consumes caller-captured official NYSE/Nasdaq calendar facts and
caller-captured original one-minute bars.  It performs no collection and
persists no prices.  Missing, partial, disputed, or unfinished inputs produce
an immutable WAIT receipt with zero mutation authority.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "us_natural_session_receipt_contract.json"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.\d+)?$")

_SESSION_SPEC = importlib.util.spec_from_file_location(
    "us_natural_session_receipt_session_bars", ROOT / "market_data" / "us_session_bars.py"
)
if _SESSION_SPEC is None or _SESSION_SPEC.loader is None:
    raise RuntimeError("US_SESSION_BARS_MODULE_LOAD_FAILED")
SESSION_BARS = importlib.util.module_from_spec(_SESSION_SPEC)
_SESSION_SPEC.loader.exec_module(SESSION_BARS)


class UsNaturalSessionError(ValueError):
    """Natural receipt input or receipt validation failed closed."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UsNaturalSessionError(f"JSON_READ_FAILED:{path}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise UsNaturalSessionError("CONTRACT_NOT_OBJECT")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "us_natural_session_receipt/1"
        or value.get("market") != "US"
        or value.get("timezone") != "America/New_York"
    ):
        raise UsNaturalSessionError("CONTRACT_IDENTITY_INVALID")
    if value.get("freshness") != {
        "numeric_ttl_seconds": None,
        "repository_default_policy": "ABSENT",
        "provider_sla": "UNRATIFIED",
        "gate_status": "HOLD",
    }:
        raise UsNaturalSessionError("CONTRACT_FRESHNESS_BOUNDARY_INVALID")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("observation_only") is not True:
        raise UsNaturalSessionError("CONTRACT_AUTHORITY_INVALID")
    if any(flag is not False for name, flag in authority.items() if name != "observation_only"):
        raise UsNaturalSessionError("CONTRACT_AUTHORITY_OPEN")
    return copy.deepcopy(value)


def _instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise UsNaturalSessionError(code)
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise UsNaturalSessionError(code) from exc
    return parsed


def _date(value: object, code: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise UsNaturalSessionError(code) from exc


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise UsNaturalSessionError(code)
    return value


def _decimal(value: object, code: str, *, zero: bool = False) -> Decimal:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise UsNaturalSessionError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise UsNaturalSessionError(code) from exc
    if parsed < 0 or (parsed == 0 and not zero):
        raise UsNaturalSessionError(code)
    return parsed


def _without_hash(value: dict, field: str) -> dict:
    result = copy.deepcopy(value)
    result.pop(field, None)
    return result


def _calendar_from_bundle(bundle: object, evaluated_at: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "session_date", "timezone", "status", "open_at",
        "close_at", "captured_at", "sources", "bundle_sha256",
    }
    if not isinstance(bundle, dict) or set(bundle) != fields:
        raise UsNaturalSessionError("CALENDAR_BUNDLE_FIELDS_INVALID")
    if bundle["schema_version"] != "us_official_calendar_consensus/1":
        raise UsNaturalSessionError("CALENDAR_BUNDLE_SCHEMA_INVALID")
    if bundle["timezone"] != contract["timezone"]:
        raise UsNaturalSessionError("CALENDAR_BUNDLE_TIMEZONE_INVALID")
    if payload_sha256(_without_hash(bundle, "bundle_sha256")) != _sha(
        bundle["bundle_sha256"], "CALENDAR_BUNDLE_SHA_INVALID"
    ):
        raise UsNaturalSessionError("CALENDAR_BUNDLE_SHA_MISMATCH")
    session_date = _date(bundle["session_date"], "CALENDAR_DATE_INVALID")
    captured_at = _instant(bundle["captured_at"], "CALENDAR_CAPTURED_AT_INVALID")
    if captured_at > evaluated_at:
        raise UsNaturalSessionError("CALENDAR_CAPTURED_IN_FUTURE")
    sources = bundle["sources"]
    expected_sources = {
        row["source_id"]: row["source_url"] for row in contract["official_calendar_sources"]
    }
    if not isinstance(sources, list) or len(sources) != len(expected_sources):
        raise UsNaturalSessionError("CALENDAR_SOURCE_COUNT_INVALID")
    seen: set[str] = set()
    source_fields = {
        "source_id", "source_url", "source_record_id", "session_date", "status",
        "open_at", "close_at", "observed_at", "captured_at", "source_sha256",
    }
    for row in sources:
        if not isinstance(row, dict) or set(row) != source_fields:
            raise UsNaturalSessionError("CALENDAR_SOURCE_FIELDS_INVALID")
        source_id = row["source_id"]
        if source_id in seen or expected_sources.get(source_id) != row["source_url"]:
            raise UsNaturalSessionError("CALENDAR_SOURCE_IDENTITY_INVALID")
        seen.add(source_id)
        if (
            row["session_date"] != bundle["session_date"]
            or row["status"] != bundle["status"]
            or row["open_at"] != bundle["open_at"]
            or row["close_at"] != bundle["close_at"]
        ):
            raise UsNaturalSessionError("CALENDAR_SOURCE_CONSENSUS_MISMATCH")
        observed = _instant(row["observed_at"], "CALENDAR_SOURCE_OBSERVED_AT_INVALID")
        captured = _instant(row["captured_at"], "CALENDAR_SOURCE_CAPTURED_AT_INVALID")
        if observed > captured or captured > captured_at or captured > evaluated_at:
            raise UsNaturalSessionError("CALENDAR_SOURCE_TIME_ORDER_INVALID")
        if not isinstance(row["source_record_id"], str) or not row["source_record_id"].strip():
            raise UsNaturalSessionError("CALENDAR_SOURCE_RECORD_ID_INVALID")
        _sha(row["source_sha256"], "CALENDAR_SOURCE_SHA_INVALID")
    if seen != set(expected_sources):
        raise UsNaturalSessionError("CALENDAR_SOURCE_SET_INVALID")
    calendar = {
        "session_date": session_date.isoformat(),
        "status": bundle["status"],
        "timezone": bundle["timezone"],
        "open_at": bundle["open_at"],
        "close_at": bundle["close_at"],
        "observed_at": min(row["observed_at"] for row in sources),
        "available_at": bundle["captured_at"],
        "source_class": "OFFICIAL_EXCHANGE_CALENDAR",
        "source_id": "NYSE.NASDAQ.CALENDAR",
        "source_ref": " | ".join(sorted(row["source_url"] for row in sources)),
        "source_sha256": bundle["bundle_sha256"],
    }
    SESSION_BARS.validate_calendar(calendar, evaluated_at, SESSION_BARS.load_contract())
    return calendar


def _validate_minute_capture(
    capture: object, calendar: dict, evaluated_at: dt.datetime
) -> list[dict]:
    fields = {
        "schema_version", "evidence_class", "capture_mode", "fixture", "session_date",
        "asset_id", "symbol", "provider_id", "feed_scope", "observed_at",
        "available_at", "source_ref", "source_sha256", "redistribution_status",
        "bars", "capture_sha256",
    }
    if not isinstance(capture, dict) or set(capture) != fields:
        raise UsNaturalSessionError("MINUTE_CAPTURE_FIELDS_INVALID")
    if (
        capture["schema_version"] != "us_original_minute_capture/1"
        or capture["evidence_class"] != "NATURAL_ORIGINAL"
        or capture["capture_mode"] != "EXTERNAL_RESULT_INJECTED_READ_ONLY"
        or capture["fixture"] is not False
    ):
        raise UsNaturalSessionError("MINUTE_CAPTURE_IDENTITY_INVALID")
    if capture["session_date"] != calendar["session_date"]:
        raise UsNaturalSessionError("MINUTE_CAPTURE_SESSION_MISMATCH")
    if payload_sha256(_without_hash(capture, "capture_sha256")) != _sha(
        capture["capture_sha256"], "MINUTE_CAPTURE_SHA_INVALID"
    ):
        raise UsNaturalSessionError("MINUTE_CAPTURE_SHA_MISMATCH")
    observed = _instant(capture["observed_at"], "MINUTE_CAPTURE_OBSERVED_AT_INVALID")
    available = _instant(capture["available_at"], "MINUTE_CAPTURE_AVAILABLE_AT_INVALID")
    if observed > available or available > evaluated_at:
        raise UsNaturalSessionError("MINUTE_CAPTURE_TIME_ORDER_INVALID")
    for field in ("asset_id", "symbol", "provider_id", "feed_scope", "source_ref"):
        if not isinstance(capture[field], str) or not capture[field].strip():
            raise UsNaturalSessionError(f"MINUTE_CAPTURE_{field.upper()}_INVALID")
    _sha(capture["source_sha256"], "MINUTE_CAPTURE_SOURCE_SHA_INVALID")
    if capture["redistribution_status"] not in {"GRANTED", "NOT_GRANTED", "UNKNOWN"}:
        raise UsNaturalSessionError("MINUTE_CAPTURE_REDISTRIBUTION_INVALID")
    rows = capture["bars"]
    if not isinstance(rows, list):
        raise UsNaturalSessionError("MINUTE_BARS_NOT_LIST")
    bar_fields = {"open_at", "close_at", "open", "high", "low", "close", "volume"}
    checked: list[dict] = []
    seen: set[tuple[dt.datetime, dt.datetime]] = set()
    checked_calendar = SESSION_BARS.validate_calendar(
        calendar, evaluated_at, SESSION_BARS.load_contract()
    )
    session_open = checked_calendar["_open"]
    session_close = checked_calendar["_close"]
    if session_open is None or session_close is None:
        raise UsNaturalSessionError("MINUTE_CAPTURE_NON_OPEN_SESSION")
    for raw in rows:
        if not isinstance(raw, dict) or set(raw) != bar_fields:
            raise UsNaturalSessionError("MINUTE_BAR_FIELDS_INVALID")
        opened = SESSION_BARS._ny_exact(raw["open_at"], "MINUTE_BAR_OPEN_AT_INVALID")
        closed = SESSION_BARS._ny_exact(raw["close_at"], "MINUTE_BAR_CLOSE_AT_INVALID")
        if closed - opened != dt.timedelta(minutes=1) or closed > evaluated_at:
            raise UsNaturalSessionError("MINUTE_BAR_TIME_INVALID")
        if opened < session_open or closed > session_close:
            raise UsNaturalSessionError("MINUTE_BAR_OUTSIDE_REGULAR_SESSION")
        key = (opened, closed)
        if key in seen:
            raise UsNaturalSessionError("MINUTE_BAR_DUPLICATE")
        seen.add(key)
        prices = {
            name: _decimal(raw[name], f"MINUTE_BAR_{name.upper()}_INVALID")
            for name in ("open", "high", "low", "close")
        }
        _decimal(raw["volume"], "MINUTE_BAR_VOLUME_INVALID", zero=True)
        if (
            prices["low"] > min(prices["open"], prices["close"])
            or prices["high"] < max(prices["open"], prices["close"])
            or prices["low"] > prices["high"]
        ):
            raise UsNaturalSessionError("MINUTE_BAR_OHLC_INVALID")
        checked.append({"raw": copy.deepcopy(raw), "open": opened, "close": closed})
    checked.sort(key=lambda row: row["open"])
    if checked and (observed < checked[-1]["close"] or available < observed):
        raise UsNaturalSessionError("MINUTE_CAPTURE_SOURCE_PRECEDES_BARS")
    return checked


def _interval_minutes(opened: dt.datetime, closed: dt.datetime) -> Iterable[tuple[dt.datetime, dt.datetime]]:
    cursor = opened
    while cursor < closed:
        end = cursor + dt.timedelta(minutes=1)
        yield cursor, end
        cursor = end


def _coverage(
    timeframe: str, calendar: dict, evaluated_at: dt.datetime, minute_rows: list[dict], capture_sha: str
) -> dict:
    intervals = SESSION_BARS.expected_intervals(
        timeframe, calendar, evaluated_at, SESSION_BARS.load_contract()
    )
    minute_map = {(row["open"], row["close"]): row["raw"] for row in minute_rows}
    completed: list[dict] = []
    missing: list[str] = []
    for opened, closed in intervals:
        keys = list(_interval_minutes(opened, closed))
        if any(key not in minute_map for key in keys):
            missing.append(opened.isoformat())
            continue
        source_minutes = [minute_map[key] for key in keys]
        completed.append(
            {
                "open_at": opened.isoformat(),
                "close_at": closed.isoformat(),
                "minute_count": len(source_minutes),
                "source_payload_sha256": payload_sha256(source_minutes),
            }
        )
    return {
        "timeframe": timeframe,
        "expected_interval_count": len(intervals),
        "completed_interval_count": len(completed),
        "coverage_status": "COMPLETE" if intervals and not missing else "PARTIAL_OR_ABSENT",
        "first_completed_open_at": completed[0]["open_at"] if completed else None,
        "last_completed_close_at": completed[-1]["close_at"] if completed else None,
        "completed_intervals_sha256": payload_sha256(
            {"capture_sha256": capture_sha, "intervals": completed}
        ) if completed else None,
        "missing_interval_open_at": missing,
        "raw_prices_persisted": False,
        "aggregate_prices_persisted": False,
    }


def build_receipt(
    *,
    session_date: str,
    evaluated_at_utc: str,
    next_natural_observation_at_utc: str,
    calendar_bundle: object | None,
    minute_capture: object | None,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    _date(session_date, "SESSION_DATE_INVALID")
    evaluated_at = _instant(evaluated_at_utc, "EVALUATED_AT_INVALID")
    next_at = _instant(next_natural_observation_at_utc, "NEXT_OBSERVATION_AT_INVALID")
    if next_at <= evaluated_at:
        raise UsNaturalSessionError("NEXT_OBSERVATION_NOT_FUTURE")
    blockers: list[str] = []
    calendar = None
    minute_rows: list[dict] = []
    calendar_valid = False
    minute_capture_valid = False
    calendar_result = {
        "status": "ABSENT",
        "session_status": "UNKNOWN",
        "source_bundle_sha256": None,
        "source_ids": [row["source_id"] for row in contract["official_calendar_sources"]],
    }
    if calendar_bundle is None:
        blockers.append("OFFICIAL_NYSE_NASDAQ_DATE_SPECIFIC_CALENDAR_ABSENT")
    else:
        try:
            calendar = _calendar_from_bundle(calendar_bundle, evaluated_at, contract)
            if calendar["session_date"] != session_date:
                raise UsNaturalSessionError("CALENDAR_REQUESTED_SESSION_MISMATCH")
            calendar_result = {
                "status": "VERIFIED_CONSENSUS",
                "session_status": calendar["status"],
                "source_bundle_sha256": calendar["source_sha256"],
                "source_ids": [row["source_id"] for row in contract["official_calendar_sources"]],
            }
            calendar_valid = True
        except (UsNaturalSessionError, SESSION_BARS.UsMarketDataError) as exc:
            blockers.append("OFFICIAL_CALENDAR_INVALID:" + str(exc))
    if minute_capture is None:
        blockers.extend(["COMPLETED_15M_SERIES_ABSENT", "COMPLETED_1H_SERIES_ABSENT"])
    elif calendar is None:
        blockers.append("MINUTE_CAPTURE_NOT_EVALUATED_WITHOUT_VALID_CALENDAR")
    else:
        try:
            minute_rows = _validate_minute_capture(minute_capture, calendar, evaluated_at)
            minute_capture_valid = True
        except UsNaturalSessionError as exc:
            blockers.append("MINUTE_CAPTURE_INVALID:" + str(exc))
    coverage = [
        {
            "timeframe": timeframe,
            "expected_interval_count": 0,
            "completed_interval_count": 0,
            "coverage_status": "NOT_EVALUATED",
            "first_completed_open_at": None,
            "last_completed_close_at": None,
            "completed_intervals_sha256": None,
            "missing_interval_open_at": [],
            "raw_prices_persisted": False,
            "aggregate_prices_persisted": False,
        }
        for timeframe in ("15m", "1h")
    ]
    session_finished = False
    if calendar is not None and calendar["status"] in {"OPEN_REGULAR", "OPEN_EARLY_CLOSE"}:
        checked_calendar = SESSION_BARS.validate_calendar(
            calendar, evaluated_at, SESSION_BARS.load_contract()
        )
        session_finished = evaluated_at >= checked_calendar["_close"]
        if not session_finished:
            blockers.append("SESSION_NOT_FINISHED")
        if minute_rows:
            capture_sha = minute_capture["capture_sha256"]  # type: ignore[index]
            coverage = [
                _coverage(timeframe, calendar, evaluated_at, minute_rows, capture_sha)
                for timeframe in ("15m", "1h")
            ]
            for row in coverage:
                if row["coverage_status"] != "COMPLETE":
                    blockers.append("COMPLETED_" + row["timeframe"].upper() + "_SERIES_INCOMPLETE")
    elif calendar is not None and calendar["status"] == "CLOSED":
        blockers.append("OFFICIAL_CLOSED_SESSION_WAIT")
    elif calendar is not None:
        blockers.append("OFFICIAL_SESSION_STATUS_UNKNOWN")
    gate1_pass = (
        calendar_result["status"] == "VERIFIED_CONSENSUS"
        and session_finished
        and all(row["coverage_status"] == "COMPLETE" for row in coverage)
    )
    if not gate1_pass:
        blockers.append("FINISHED_SESSION_ADMISSION_RECEIPT_NOT_PASS")
    blockers.extend(
        [
            "US_FRESHNESS_REPOSITORY_DEFAULT_POLICY_ABSENT",
            "US_TTL_NOT_RATIFIED",
            "US_PROVIDER_SLA_UNRATIFIED",
        ]
    )
    receipt = {
        "schema_version": "us_natural_session_receipt/1",
        "contract_version": contract["contract_version"],
        "market": "US",
        "session_date": session_date,
        "evaluated_at_utc": evaluated_at_utc,
        "evidence_class": (
            "NATURAL_ORIGINAL_HASH_ONLY"
            if calendar_valid and minute_capture_valid
            else (
                "NATURAL_INPUT_PROVIDED_NOT_ADMITTED"
                if calendar_bundle is not None or minute_capture is not None
                else "NATURAL_INPUT_ABSENCE_AUDIT"
            )
        ),
        "calendar": calendar_result,
        "completed_timeframes": coverage,
        "gate1": {
            "name": "FINISHED_SESSION_SOURCE",
            "status": "PASS" if gate1_pass else "UNKNOWN",
            "session_finished": session_finished,
            "natural_receipt_complete": gate1_pass,
        },
        "gate2": {
            "name": "FRESHNESS",
            "status": "HOLD",
            "numeric_ttl_seconds": None,
            "repository_default_policy": "ABSENT",
            "provider_sla": "UNRATIFIED",
        },
        "status": "HOLD",
        "recommendation": "WAIT",
        "blockers": sorted(set(blockers)),
        "next_natural_observation_at_utc": next_natural_observation_at_utc,
        "authority": copy.deepcopy(contract["authority"]),
        "side_effects": {
            "broker": 0,
            "network": 0,
            "oauth": 0,
            "order": 0,
            "cancel": 0,
            "paper_mutation": 0,
            "ledger_mutation": 0,
        },
    }
    receipt["receipt_sha256"] = payload_sha256(receipt)
    return receipt


def verify_receipt(receipt: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    if not isinstance(receipt, dict):
        raise UsNaturalSessionError("RECEIPT_NOT_OBJECT")
    supplied = _sha(receipt.get("receipt_sha256"), "RECEIPT_SHA_INVALID")
    if payload_sha256(_without_hash(receipt, "receipt_sha256")) != supplied:
        raise UsNaturalSessionError("RECEIPT_SHA_MISMATCH")
    if receipt.get("contract_version") != contract["contract_version"]:
        raise UsNaturalSessionError("RECEIPT_CONTRACT_MISMATCH")
    if receipt.get("authority") != contract["authority"]:
        raise UsNaturalSessionError("RECEIPT_AUTHORITY_MISMATCH")
    if receipt.get("gate2") != {
        "name": "FRESHNESS",
        "status": "HOLD",
        "numeric_ttl_seconds": None,
        "repository_default_policy": "ABSENT",
        "provider_sla": "UNRATIFIED",
    }:
        raise UsNaturalSessionError("RECEIPT_GATE2_BOUNDARY_INVALID")
    if any(receipt.get("side_effects", {}).values()):
        raise UsNaturalSessionError("RECEIPT_SIDE_EFFECT_OPEN")
    return copy.deepcopy(receipt)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date")
    parser.add_argument("--evaluated-at-utc")
    parser.add_argument("--next-natural-observation-at-utc")
    parser.add_argument("--calendar-bundle", type=Path)
    parser.add_argument("--minute-capture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        receipt = verify_receipt(_read_json(args.verify))
        print("VERIFIED:" + receipt["receipt_sha256"])
        return 0
    required = {
        "session_date": args.session_date,
        "evaluated_at_utc": args.evaluated_at_utc,
        "next_natural_observation_at_utc": args.next_natural_observation_at_utc,
        "output": args.output,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error("missing required build arguments: " + ", ".join(missing))
    receipt = build_receipt(
        session_date=args.session_date,
        evaluated_at_utc=args.evaluated_at_utc,
        next_natural_observation_at_utc=args.next_natural_observation_at_utc,
        calendar_bundle=_read_json(args.calendar_bundle) if args.calendar_bundle else None,
        minute_capture=_read_json(args.minute_capture) if args.minute_capture else None,
    )
    _write(args.output, receipt)
    print(receipt["gate1"]["status"] + ":" + receipt["gate2"]["status"] + ":" + receipt["receipt_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
