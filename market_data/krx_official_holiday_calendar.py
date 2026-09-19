#!/usr/bin/env python3
"""Derive date-specific KRX session packets from an official holiday capture.

The capture is produced from KRX Global page [01023], not from a broker
calendar endpoint.  It embeds the provider response bytes exactly and records
the response availability instant.  Weekends and dates listed by KRX are
closed; other weekdays use KRX's published regular equity-session hours.
"""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path


PROVIDER_ID = "KRX_GLOBAL_MARKET_CLOSING_HOLIDAY_01023"
MARKET_RULE_SOURCE = "KRX_EQUITY_MARKET_OPERATION_RULES"
CAPTURE_SCHEMA = "krx_official_holiday_capture/1"
DERIVATION_SCHEMA = "krx_official_holiday_calendar_derivation/1"
PAGE_URL = (
    "https://global.krx.co.kr/contents/GLB/05/0501/0501110000/"
    "GLB0501110000.jsp"
)
OTP_URL = "https://global.krx.co.kr/contents/COM/GenerateOTP.jspx"
DATA_URL = "https://global.krx.co.kr/contents/GLB/99/GLB99000001.jspx"
BLD = "GLB/05/0501/0501110000/glb0501110000_01"
MARKET_RULE_URL = (
    "https://global.krx.co.kr/contents/GLB/06/0602/0602010201/"
    "GLB0602010201T1.jsp"
)
DAY_CODES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


class KrxOfficialHolidayCalendarError(ValueError):
    """Fail-closed official KRX holiday-source error."""


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise KrxOfficialHolidayCalendarError("DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _object(raw: bytes, code: str) -> dict:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                KrxOfficialHolidayCalendarError("NONFINITE_JSON")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KrxOfficialHolidayCalendarError(code) from exc
    if not isinstance(value, dict):
        raise KrxOfficialHolidayCalendarError(code)
    return value


def _instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise KrxOfficialHolidayCalendarError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KrxOfficialHolidayCalendarError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KrxOfficialHolidayCalendarError(code)
    return parsed


def _date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        raise KrxOfficialHolidayCalendarError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KrxOfficialHolidayCalendarError(code) from exc
    if parsed.isoformat() != value:
        raise KrxOfficialHolidayCalendarError(code)
    return parsed


def validate_capture(capture_raw: bytes) -> dict:
    capture = _object(capture_raw, "CAPTURE_JSON_INVALID")
    if capture_raw != canonical_bytes(capture):
        raise KrxOfficialHolidayCalendarError("CAPTURE_BYTES_NOT_CANONICAL")
    required = {
        "schema_version", "provider_id", "market", "venue_scope", "year",
        "page_url", "otp_url", "data_url", "bld", "market_rule_url", "request",
        "capture_started_at", "response_received_at", "page_raw_sha256",
        "otp_retained", "response", "authority",
    }
    if set(capture) != required or capture.get("schema_version") != CAPTURE_SCHEMA:
        raise KrxOfficialHolidayCalendarError("CAPTURE_FIELDS_INVALID")
    if (
        capture.get("provider_id") != PROVIDER_ID
        or capture.get("market") != "KOREA"
        or capture.get("venue_scope") != "KRX_ONLY"
        or capture.get("page_url") != PAGE_URL
        or capture.get("otp_url") != OTP_URL
        or capture.get("data_url") != DATA_URL
        or capture.get("bld") != BLD
        or capture.get("market_rule_url") != MARKET_RULE_URL
        or capture.get("otp_retained") is not False
    ):
        raise KrxOfficialHolidayCalendarError("CAPTURE_IDENTITY_INVALID")
    year = capture.get("year")
    request = capture.get("request")
    if (
        type(year) is not int
        or not 2009 <= year <= 2100
        or request != {
            "search_bas_yy": str(year),
            "gridTp": "KRX",
            "pagePath": "",
            "network_operations": ["PAGE_GET", "OTP_GET", "HOLIDAY_POST"],
            "redirects_allowed": False,
        }
    ):
        raise KrxOfficialHolidayCalendarError("CAPTURE_REQUEST_INVALID")
    started = _instant(capture.get("capture_started_at"), "CAPTURE_TIME_INVALID")
    received = _instant(capture.get("response_received_at"), "CAPTURE_TIME_INVALID")
    if started > received:
        raise KrxOfficialHolidayCalendarError("CAPTURE_TIME_ORDER_INVALID")
    if (
        not isinstance(capture.get("page_raw_sha256"), str)
        or len(capture["page_raw_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in capture["page_raw_sha256"])
    ):
        raise KrxOfficialHolidayCalendarError("PAGE_HASH_INVALID")
    response = capture.get("response")
    if not isinstance(response, dict) or set(response) != {
        "http_status", "content_type", "final_url", "redirect_count",
        "raw_base64", "raw_sha256",
    }:
        raise KrxOfficialHolidayCalendarError("RESPONSE_FIELDS_INVALID")
    if (
        response.get("http_status") != 200
        or not isinstance(response.get("content_type"), str)
        or response["content_type"].split(";", 1)[0].strip().lower()
        not in {"application/json", "text/html"}
        or response.get("final_url") != DATA_URL
        or response.get("redirect_count") != 0
    ):
        raise KrxOfficialHolidayCalendarError("RESPONSE_TRANSPORT_INVALID")
    try:
        provider_raw = base64.b64decode(response.get("raw_base64", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise KrxOfficialHolidayCalendarError("RESPONSE_BASE64_INVALID") from exc
    if not provider_raw or digest(provider_raw) != response.get("raw_sha256"):
        raise KrxOfficialHolidayCalendarError("RESPONSE_HASH_MISMATCH")
    authority = capture.get("authority")
    if authority != {
        "market_calendar_observation_only": True,
        "candidate_authorized": False,
        "entry_authorized": False,
        "order_authorized": False,
        "trading_authorized": False,
        "real_capital_authorized": False,
    }:
        raise KrxOfficialHolidayCalendarError("CAPTURE_AUTHORITY_INVALID")

    payload = _object(provider_raw, "PROVIDER_JSON_INVALID")
    if set(payload) != {"block1"} or not isinstance(payload["block1"], list):
        raise KrxOfficialHolidayCalendarError("PROVIDER_SCHEMA_INVALID")
    closures = {}
    prior = None
    for row in payload["block1"]:
        if not isinstance(row, dict) or set(row) != {
            "calnd_dd", "dy_tp_cd", "calnd_dd_dy", "kr_dy_tp", "holdy_eng_nm"
        }:
            raise KrxOfficialHolidayCalendarError("HOLIDAY_ROW_FIELDS_INVALID")
        day = _date(row["calnd_dd"], "HOLIDAY_DATE_INVALID")
        if day.year != year or row["calnd_dd_dy"] != day.isoformat():
            raise KrxOfficialHolidayCalendarError("HOLIDAY_YEAR_OR_DATE_INVALID")
        if row["dy_tp_cd"] != DAY_CODES[day.weekday()]:
            raise KrxOfficialHolidayCalendarError("HOLIDAY_WEEKDAY_INVALID")
        if day.weekday() >= 5:
            raise KrxOfficialHolidayCalendarError("WEEKEND_MUST_NOT_BE_LISTED")
        if prior is not None and day <= prior:
            raise KrxOfficialHolidayCalendarError("HOLIDAY_ORDER_OR_DUPLICATE_INVALID")
        if not isinstance(row["kr_dy_tp"], str) or not isinstance(row["holdy_eng_nm"], str):
            raise KrxOfficialHolidayCalendarError("HOLIDAY_TEXT_INVALID")
        closures[day.isoformat()] = copy.deepcopy(row)
        prior = day
    if not closures:
        raise KrxOfficialHolidayCalendarError("HOLIDAY_LIST_EMPTY")
    return {
        "capture": capture,
        "capture_started_at": started,
        "response_received_at": received,
        "provider_raw": provider_raw,
        "provider_raw_sha256": response["raw_sha256"],
        "closures": closures,
    }


def build_calendar_packet(
    capture_raw: bytes, source_ref: str, session_date: str
) -> tuple[dict, dict]:
    checked = validate_capture(capture_raw)
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise KrxOfficialHolidayCalendarError("SOURCE_REF_INVALID")
    day = _date(session_date, "SESSION_DATE_INVALID")
    if day.year != checked["capture"]["year"]:
        raise KrxOfficialHolidayCalendarError("SESSION_YEAR_MISMATCH")
    listed = checked["closures"].get(day.isoformat())
    weekend = day.weekday() >= 5
    closed = weekend or listed is not None
    status = "CLOSED" if closed else "OPEN_REGULAR"
    available = checked["response_received_at"].isoformat()
    source_sha256 = digest(capture_raw)
    calendar = {
        "session_date": day.isoformat(),
        "status": status,
        "timezone": "Asia/Seoul",
        "open_at": None if closed else f"{day.isoformat()}T09:00:00+09:00",
        "close_at": None if closed else f"{day.isoformat()}T15:30:00+09:00",
        "observed_at": available,
        "available_at": available,
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "provider_id": PROVIDER_ID,
        "market_rule_source": MARKET_RULE_SOURCE,
    }
    packet = {
        "schema_version": "krx_date_specific_session_source/1",
        "as_of_date": day.isoformat(),
        "official_response_ref": source_ref,
        "official_response_sha256": source_sha256,
        "calendar": calendar,
    }
    reason = (
        "WEEKEND_KRX_RULE"
        if weekend
        else (listed["holdy_eng_nm"] or "KRX_LISTED_HOLIDAY")
        if listed is not None
        else "WEEKDAY_NOT_IN_OFFICIAL_KRX_CLOSURE_LIST"
    )
    receipt = {
        "schema_version": DERIVATION_SCHEMA,
        "status": status,
        "session_date": day.isoformat(),
        "reason": reason,
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "provider_response_sha256": checked["provider_raw_sha256"],
        "source_available_at": available,
        "calendar_packet_sha256": digest(canonical_bytes(packet)),
        "listed_holiday_row": copy.deepcopy(listed),
        "authority": copy.deepcopy(checked["capture"]["authority"]),
    }
    return packet, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("session_date")
    parser.add_argument("output", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    packet, receipt = build_calendar_packet(
        args.capture.read_bytes(), args.capture.as_posix(), args.session_date
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(packet))
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_bytes(canonical_bytes(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
