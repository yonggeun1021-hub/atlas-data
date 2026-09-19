#!/usr/bin/env python3
"""Derive an OPEN_REGULAR calendar packet from retained KRX observations.

The input is the exact, append-only ``source.json`` produced by
``collectors/krx_post_close.py``.  This adapter uses only the existence of
date-specific, positive OHLCV rows for both bounded PAPER symbols.  It does
not promote those prices or investor-flow values to final data and it grants
no order, account, production, or real-capital authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
REQUIRED_SYMBOLS = ("000660", "005930")
SOURCE_LABEL = "KRX 정보데이터시스템 (pykrx)"
SOURCE_TIER = "Official"
PROVIDER_ID = "KRX_INFORMATION_DATA_SYSTEM_EXACT_DATE_OHLCV"
MARKET_RULE_SOURCE = "KRX_EQUITY_MARKET_OPERATION_RULES"
PROFILE = "krx_post_close_open_session_evidence/1"


class KrxPostCloseCalendarError(ValueError):
    """Fail-closed KRX observation adapter violation."""


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        raise KrxPostCloseCalendarError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KrxPostCloseCalendarError(code) from exc
    if parsed.isoformat() != value:
        raise KrxPostCloseCalendarError(code)
    return parsed


def _instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise KrxPostCloseCalendarError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KrxPostCloseCalendarError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KrxPostCloseCalendarError(code)
    return parsed


def _positive_number(value: object, code: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise KrxPostCloseCalendarError(code)


def build_calendar_packet(
    source_raw: bytes,
    source_ref: str,
    session_date: str,
) -> tuple[dict, dict]:
    """Return a v1 calendar envelope and a bounded derivation receipt."""
    try:
        source = json.loads(source_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KrxPostCloseCalendarError("SOURCE_JSON_INVALID") from exc
    if not isinstance(source, dict):
        raise KrxPostCloseCalendarError("SOURCE_NOT_OBJECT")
    if source_raw != canonical_bytes(source):
        raise KrxPostCloseCalendarError("SOURCE_BYTES_NOT_CANONICAL")
    if not isinstance(source_ref, str) or not source_ref:
        raise KrxPostCloseCalendarError("SOURCE_REF_INVALID")

    requested = _date(session_date, "SESSION_DATE_INVALID")
    capture_day = _date(
        source.get("collected_for_kst_date"), "SOURCE_CAPTURE_DATE_INVALID"
    )
    if requested > capture_day:
        raise KrxPostCloseCalendarError("SESSION_AFTER_CAPTURE_DATE")
    if (
        source.get("source") != SOURCE_LABEL
        or source.get("source_tier") != SOURCE_TIER
        or source.get("collector_version") != "v4.1"
        or source.get("same_day_confirmation") != "next_day"
    ):
        raise KrxPostCloseCalendarError("SOURCE_IDENTITY_INVALID")

    collected_kst = _instant(
        source.get("collected_at_kst"), "SOURCE_COLLECTED_AT_INVALID"
    ).astimezone(KST)
    collected_utc = _instant(
        source.get("collected_at_utc"), "SOURCE_COLLECTED_AT_INVALID"
    )
    if (
        collected_kst.date() != capture_day
        or collected_kst.time() < dt.time(15, 30)
        or abs((collected_kst.astimezone(dt.timezone.utc) - collected_utc).total_seconds()) > 2
    ):
        raise KrxPostCloseCalendarError("SOURCE_COLLECTION_TIME_INVALID")

    stocks = source.get("stocks")
    summary = source.get("summary")
    if not isinstance(stocks, dict) or not isinstance(summary, dict):
        raise KrxPostCloseCalendarError("SOURCE_COVERAGE_INVALID")
    if (
        type(summary.get("ok")) is not int
        or type(summary.get("failed")) is not int
        or summary["failed"] != 0
        or summary["ok"] != len(stocks)
    ):
        raise KrxPostCloseCalendarError("SOURCE_PARTIAL_RESPONSE")

    observations = []
    for symbol in REQUIRED_SYMBOLS:
        stock = stocks.get(symbol)
        if not isinstance(stock, dict) or stock.get("status") != "ok":
            raise KrxPostCloseCalendarError(f"REQUIRED_SYMBOL_INVALID:{symbol}")
        daily = stock.get("daily")
        row = daily.get(session_date) if isinstance(daily, dict) else None
        if not isinstance(row, dict):
            raise KrxPostCloseCalendarError(f"EXACT_DATE_ROW_MISSING:{symbol}")
        expected_confirmation = (
            (False, "deferred_to_next_day")
            if requested == capture_day
            else (True, "prior_session")
        )
        if (row.get("confirmed"), row.get("confirm_reason")) != expected_confirmation:
            raise KrxPostCloseCalendarError(f"ROW_CONFIRMATION_STATE_INVALID:{symbol}")
        for field in ("open", "high", "low", "close", "volume"):
            _positive_number(row.get(field), f"ROW_{field.upper()}_INVALID:{symbol}")
        if not (row["low"] <= row["open"] <= row["high"] and row["low"] <= row["close"] <= row["high"]):
            raise KrxPostCloseCalendarError(f"ROW_OHLC_RELATION_INVALID:{symbol}")
        observed = _instant(
            row.get("observed_at_kst"), f"ROW_OBSERVED_AT_INVALID:{symbol}"
        ).astimezone(KST)
        if observed.date() != capture_day or observed.time() < dt.time(15, 30):
            raise KrxPostCloseCalendarError(f"ROW_OBSERVED_AT_INVALID:{symbol}")
        observations.append({
            "symbol": symbol,
            "observed_at": observed.isoformat(),
            "confirmed": row["confirmed"],
            "confirm_reason": row["confirm_reason"],
            "ohlcv_sha256": digest(canonical_bytes({
                field: row[field] for field in ("open", "high", "low", "close", "volume")
            })),
        })

    available = max(_instant(row["observed_at"], "ROW_OBSERVED_AT_INVALID") for row in observations)
    source_sha256 = digest(source_raw)
    calendar = {
        "session_date": session_date,
        "status": "OPEN_REGULAR",
        "timezone": "Asia/Seoul",
        "open_at": f"{session_date}T09:00:00+09:00",
        "close_at": f"{session_date}T15:30:00+09:00",
        "observed_at": available.isoformat(),
        "available_at": available.isoformat(),
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "provider_id": PROVIDER_ID,
        "market_rule_source": MARKET_RULE_SOURCE,
    }
    packet = {
        "schema_version": "krx_date_specific_session_source/1",
        "as_of_date": session_date,
        "official_response_ref": source_ref,
        "official_response_sha256": source_sha256,
        "calendar": calendar,
    }
    receipt = {
        "schema_version": PROFILE,
        "status": "OPEN_REGULAR_DERIVED_FROM_EXACT_KRX_OBSERVATIONS",
        "session_date": session_date,
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "source_capture_date": capture_day.isoformat(),
        "calendar_packet_sha256": digest(canonical_bytes(packet)),
        "required_symbols": list(REQUIRED_SYMBOLS),
        "observations": observations,
        "price_or_flow_finality_promoted": False,
        "authority": {
            "market_calendar_observation_only": True,
            "candidate_authorized": False,
            "entry_authorized": False,
            "order_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }
    return packet, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("session_date")
    parser.add_argument("output", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    packet, receipt = build_calendar_packet(
        args.source.read_bytes(), args.source.as_posix(), args.session_date
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_bytes(packet))
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_bytes(canonical_bytes(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
