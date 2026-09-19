#!/usr/bin/env python3
"""Qualify owner-pinned retained KIS H0STCNT0 rows as raw live observations.

This module never promotes the rows to complete minute bars.  H0STCNT0 has no
retained provider sequence or connection-gap ledger, so the strongest result
is a date-specific, read-only trade-observation qualification.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from market_data import krx_session_calendar_v2 as KRX


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "kis_realtime_trade_observation_contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA1 = re.compile(r"^[0-9a-f]{40}$")
KST = ZoneInfo("Asia/Seoul")


class KisRealtimeTradeObservationError(ValueError):
    """Fail-closed input or qualification error."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise KisRealtimeTradeObservationError(code)


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise KisRealtimeTradeObservationError("CANONICAL_JSON_INVALID") from exc


def digest(raw: bytes) -> str:
    require(isinstance(raw, bytes), "BYTES_REQUIRED")
    return hashlib.sha256(raw).hexdigest()


def _object(raw: bytes, code: str) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "DUPLICATE_JSON_KEY")
            value[key] = item
        return value

    try:
        value = json.loads(
            raw, object_pairs_hook=pairs,
            parse_constant=lambda _: require(False, "NONFINITE_JSON"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise KisRealtimeTradeObservationError(code) from exc
    require(isinstance(value, dict), code)
    return value


def _keys(value: object, expected: set[str], code: str) -> dict:
    require(isinstance(value, dict) and set(value) == expected, code)
    return value


def _sha256(value: object, code: str) -> str:
    require(isinstance(value, str) and SHA256.fullmatch(value) is not None, code)
    return value


def _instant(value: object, code: str) -> dt.datetime:
    require(isinstance(value, str), code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KisRealtimeTradeObservationError(code) from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, code)
    return parsed


def _date(value: object, code: str) -> dt.date:
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), code)
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KisRealtimeTradeObservationError(code) from exc


def _trusted(raw: bytes, expected_sha256: str, label: str) -> dict:
    _sha256(expected_sha256, label + "_TRUST_ANCHOR_INVALID")
    require(digest(raw) == expected_sha256, label + "_HASH_MISMATCH")
    return _object(raw, label + "_JSON_INVALID")


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _object(path.read_bytes(), "CONTRACT_INVALID")
    required = {
        "schema_version", "contract_version", "market", "venue_scope",
        "environment", "timezone", "evidence_classes", "official_source",
        "deployed_source_pins", "retained_row", "approved_symbols", "calendar",
        "timestamp_semantics", "qualification_scope", "authority",
    }
    _keys(value, required, "CONTRACT_SCHEMA_INVALID")
    require(
        value["schema_version"] == 1
        and value["contract_version"] == "kis_realtime_trade_observation/1"
        and value["market"] == "KOREA"
        and value["venue_scope"] == "KRX_ONLY"
        and value["environment"] == "PAPER"
        and value["timezone"] == "Asia/Seoul",
        "CONTRACT_IDENTITY_INVALID",
    )
    require(
        value["evidence_classes"] == ["LIVE_NATURAL", "SYNTHETIC_OFFLINE_FIXTURE"],
        "EVIDENCE_CLASSES_INVALID",
    )
    official = value["official_source"]
    require(
        official == {
            "provider_id": "KIS_OPEN_API",
            "transport": "WEBSOCKET",
            "tr_id": "H0STCNT0",
            "purpose": "KRX_REALTIME_TRADE_OBSERVATION_READ_ONLY",
            "repository": "koreainvestment/open-trading-api",
            "commit": "b4e6249714418aa57833d1cbbbced39cbcc5b125",
            "path": "examples_user/domestic_stock/domestic_stock_functions_ws.py",
            "git_blob_sha1": "644e117d95df9995f3835a0ff34d4bf7e09cde4a",
            "field_count": 46,
            "field_positions_zero_based": {
                "symbol": 0, "stock_execution_time": 1, "price": 2,
                "execution_volume": 12, "business_date": 33,
            },
        },
        "OFFICIAL_SOURCE_PIN_INVALID",
    )
    require(GIT_SHA1.fullmatch(official["commit"]) is not None, "OFFICIAL_COMMIT_INVALID")
    pins = value["deployed_source_pins"]
    _keys(pins, {"collector_sha256", "observation_retention_sha256"}, "DEPLOYED_PINS_INVALID")
    for item in pins.values():
        _sha256(item, "DEPLOYED_PIN_INVALID")
    retained = _keys(
        value["retained_row"],
        {
            "schema_version", "source_kind", "provider_tr_id",
            "required_fields", "raw_and_parsed_hash_revalidation_required",
            "raw_to_parsed_field_equality_required",
        },
        "RETAINED_ROW_CONTRACT_INVALID",
    )
    require(
        retained["schema_version"] == "kis_quote_observation_record/1"
        and retained["source_kind"] == "DECODED_TRADE_RECORD"
        and retained["provider_tr_id"] == "H0STCNT0"
        and retained["required_fields"] == [
            "record_id", "schema_version", "source_kind", "provider_tr_id",
            "source_record_json", "source_record_sha256", "parsed_quote_json",
            "parsed_quote_sha256", "observed_at", "record_attempted_at",
        ]
        and retained["raw_and_parsed_hash_revalidation_required"] is True
        and retained["raw_to_parsed_field_equality_required"] is True,
        "RETAINED_ROW_CONTRACT_INVALID",
    )
    require(value["approved_symbols"] == ["000660", "005930", "071050"], "SYMBOL_SCOPE_INVALID")
    calendar = _keys(
        value["calendar"],
        {
            "schema_version", "required_status", "validator_path",
            "validator_sha256", "contract_path", "contract_sha256",
        },
        "CALENDAR_CONTRACT_INVALID",
    )
    require(
        calendar["schema_version"] == "krx_date_specific_session_source/1"
        and calendar["required_status"] == "OPEN_REGULAR",
        "CALENDAR_CONTRACT_INVALID",
    )
    for binding in (("validator_path", "validator_sha256"), ("contract_path", "contract_sha256")):
        source = ROOT / calendar[binding[0]]
        require(source.is_file(), "CALENDAR_BINDING_MISSING")
        require(digest(source.read_bytes()) == calendar[binding[1]], "CALENDAR_BINDING_HASH_MISMATCH")
    require(
        value["timestamp_semantics"] == {
            "trade_instant": "BSOP_DATE_PLUS_STCK_CNTG_HOUR_ASIA_SEOUL",
            "minute_floor_use": "OBSERVATION_GROUPING_ONLY",
            "availability": "OWNER_EXPORT_AVAILABLE_AT_CONSERVATIVE_BOUND",
            "required_order": (
                "TRADE_AT_LE_OBSERVED_AT_LE_RECORD_ATTEMPTED_AT_LE_"
                "EXPORT_STARTED_AT_LE_EXPORT_AVAILABLE_AT_LE_EVALUATION_AT"
            ),
        },
        "TIMESTAMP_SEMANTICS_INVALID",
    )
    scope = value["qualification_scope"]
    require(
        scope == {
            "qualified": "LIVE_NATURAL_RAW_TRADE_OBSERVATION",
            "completed_minute_ohlcv_qualified": False,
            "all_trades_exhaustiveness_qualified": False,
            "provider_sequence_available": False,
            "connection_gap_ledger_available": False,
            "original_provider_message_bytes_retained": False,
            "completed_bar_provider_allowlist_changed": False,
            "decision_eligibility_authorized": False,
        },
        "QUALIFICATION_SCOPE_INVALID",
    )
    authority = value["authority"]
    require(
        set(authority) == {
            "market_data_observation_only", "candidate_eligibility_authorized",
            "entry_authorized", "internal_virtual_fill_authorized",
            "kis_mock_order_authorized", "real_capital_authorized",
            "production_authorized", "order_authorized", "trading_authorized",
        },
        "AUTHORITY_INVALID",
    )
    require(authority.get("market_data_observation_only") is True, "AUTHORITY_INVALID")
    require(
        all(item is False for key, item in authority.items() if key != "market_data_observation_only"),
        "AUTHORITY_OPEN",
    )
    return copy.deepcopy(value)


def _calendar(
    raw: bytes, expected_sha256: str, session_date: str,
    evaluation_at: dt.datetime, contract: dict,
) -> dict:
    envelope = _trusted(raw, expected_sha256, "CALENDAR_PACKET")
    _keys(
        envelope,
        {"schema_version", "as_of_date", "official_response_ref",
         "official_response_sha256", "calendar"},
        "CALENDAR_PACKET_SCHEMA_INVALID",
    )
    require(
        envelope["schema_version"] == contract["calendar"]["schema_version"]
        and envelope["as_of_date"] == session_date,
        "CALENDAR_PACKET_SCOPE_INVALID",
    )
    try:
        checked = KRX.validate_calendar(
            envelope["calendar"], evaluation_at, KRX.load_contract()
        )
    except KRX.KrxMarketDataError as exc:
        raise KisRealtimeTradeObservationError(f"CALENDAR_INVALID:{exc}") from exc
    require(
        checked["session_date"] == session_date
        and checked["status"] == contract["calendar"]["required_status"],
        "SESSION_NOT_OPEN_REGULAR",
    )
    require(
        checked["source_ref"] == envelope["official_response_ref"]
        and checked["source_sha256"] == envelope["official_response_sha256"],
        "CALENDAR_SOURCE_BINDING_MISMATCH",
    )
    return checked


def _owner(
    raw: bytes, expected_sha256: str, row_packet_sha256: str,
    session_date: str, evidence_class: str, contract: dict,
) -> dict:
    value = _trusted(raw, expected_sha256, "OWNER_RECEIPT")
    _keys(
        value,
        {
            "schema_version", "evidence_class", "market", "environment",
            "session_date", "owner_receipt_ref", "row_packet_sha256",
            "row_range", "symbols", "export_started_at", "export_available_at",
            "decoded_source_record_bytes_retained", "official_source", "deployed_source_pins",
            "authority",
        },
        "OWNER_RECEIPT_SCHEMA_INVALID",
    )
    require(
        value["schema_version"] == "kis_realtime_trade_export_owner_receipt/1"
        and value["evidence_class"] == evidence_class
        and value["market"] == contract["market"]
        and value["environment"] == contract["environment"]
        and value["session_date"] == session_date
        and value["row_packet_sha256"] == row_packet_sha256,
        "OWNER_RECEIPT_SCOPE_INVALID",
    )
    require(
        isinstance(value["owner_receipt_ref"], str) and bool(value["owner_receipt_ref"].strip()),
        "OWNER_RECEIPT_REF_INVALID",
    )
    require(
        value["symbols"] == contract["approved_symbols"]
        and value["decoded_source_record_bytes_retained"] is True
        and value["official_source"] == contract["official_source"]
        and value["deployed_source_pins"] == contract["deployed_source_pins"]
        and value["authority"] == contract["authority"],
        "OWNER_RECEIPT_BINDING_MISMATCH",
    )
    _keys(value["row_range"], {"first", "last", "count"}, "OWNER_ROW_RANGE_INVALID")
    first, last, count = (
        value["row_range"]["first"], value["row_range"]["last"],
        value["row_range"]["count"],
    )
    require(
        all(type(item) is int for item in (first, last, count))
        and first > 0 and last >= first and count == last - first + 1,
        "OWNER_ROW_RANGE_INVALID",
    )
    return value


def _number(raw: str, kind, code: str):
    try:
        return kind(raw.strip() or "0")
    except (AttributeError, ValueError) as exc:
        raise KisRealtimeTradeObservationError(code) from exc


def _row(value: object, session_date: str, contract: dict) -> dict:
    expected = set(contract["retained_row"]["required_fields"])
    row = _keys(value, expected, "ROW_SCHEMA_INVALID")
    require(type(row["record_id"]) is int and row["record_id"] > 0, "RECORD_ID_INVALID")
    require(
        row["schema_version"] == contract["retained_row"]["schema_version"]
        and row["source_kind"] == contract["retained_row"]["source_kind"]
        and row["provider_tr_id"] == contract["retained_row"]["provider_tr_id"],
        "ROW_SOURCE_IDENTITY_INVALID",
    )
    require(isinstance(row["source_record_json"], str), "SOURCE_RECORD_BYTES_INVALID")
    require(isinstance(row["parsed_quote_json"], str), "PARSED_QUOTE_BYTES_INVALID")
    require(
        digest(row["source_record_json"].encode("utf-8"))
        == _sha256(row["source_record_sha256"], "SOURCE_RECORD_SHA_INVALID"),
        "SOURCE_RECORD_HASH_MISMATCH",
    )
    require(
        digest(row["parsed_quote_json"].encode("utf-8"))
        == _sha256(row["parsed_quote_sha256"], "PARSED_QUOTE_SHA_INVALID"),
        "PARSED_QUOTE_HASH_MISMATCH",
    )
    # The retained source record is an array, while the enclosing row packet
    # and parsed quote are objects with duplicate-key rejection.
    try:
        source = json.loads(row["source_record_json"], parse_constant=lambda _: require(False, "NONFINITE_JSON"))
    except (json.JSONDecodeError, TypeError) as exc:
        raise KisRealtimeTradeObservationError("SOURCE_RECORD_JSON_INVALID") from exc
    require(
        isinstance(source, list)
        and len(source) == contract["official_source"]["field_count"]
        and all(isinstance(item, str) for item in source),
        "SOURCE_RECORD_FIELDS_INVALID",
    )
    parsed = _object(row["parsed_quote_json"].encode("utf-8"), "PARSED_QUOTE_JSON_INVALID")
    parsed_fields = {
        "symbol", "trade_time", "price", "change", "change_rate", "open",
        "high", "low", "ask", "bid", "trade_volume", "cumulative_volume",
        "business_date", "observed_at",
    }
    _keys(parsed, parsed_fields, "PARSED_QUOTE_FIELDS_INVALID")
    positions = contract["official_source"]["field_positions_zero_based"]
    require(
        parsed["symbol"] == source[positions["symbol"]]
        and parsed["trade_time"] == source[positions["stock_execution_time"]]
        and parsed["price"] == _number(source[positions["price"]], int, "SOURCE_PRICE_INVALID")
        and parsed["trade_volume"] == _number(source[positions["execution_volume"]], int, "SOURCE_VOLUME_INVALID")
        and parsed["business_date"] == source[positions["business_date"]],
        "RAW_PARSED_OFFICIAL_FIELD_MISMATCH",
    )
    mappings = (
        (4, "change", int), (5, "change_rate", float), (7, "open", int),
        (8, "high", int), (9, "low", int), (10, "ask", int), (11, "bid", int),
        (13, "cumulative_volume", int),
    )
    require(
        all(parsed[name] == _number(source[index], kind, "SOURCE_NUMERIC_INVALID")
            for index, name, kind in mappings),
        "RAW_PARSED_VALUE_MISMATCH",
    )
    require(parsed["symbol"] in contract["approved_symbols"], "UNAPPROVED_SYMBOL")
    compact = session_date.replace("-", "")
    require(parsed["business_date"] == compact, "BUSINESS_DATE_MISMATCH")
    require(
        isinstance(parsed["trade_time"], str)
        and re.fullmatch(r"\d{6}", parsed["trade_time"]) is not None,
        "TRADE_TIME_INVALID",
    )
    try:
        trade_at = dt.datetime.strptime(
            compact + parsed["trade_time"], "%Y%m%d%H%M%S"
        ).replace(tzinfo=KST)
    except ValueError as exc:
        raise KisRealtimeTradeObservationError("TRADE_TIME_INVALID") from exc
    observed = _instant(row["observed_at"], "OBSERVED_AT_INVALID")
    attempted = _instant(row["record_attempted_at"], "RECORD_ATTEMPTED_AT_INVALID")
    require(parsed["observed_at"] == row["observed_at"], "OBSERVED_AT_BINDING_MISMATCH")
    require(trade_at <= observed <= attempted, "ROW_TIME_ORDER_INVALID")
    return {
        "record_id": row["record_id"],
        "symbol": parsed["symbol"],
        "trade_at": trade_at,
        "observation_minute": trade_at.replace(second=0, microsecond=0),
        "observed_at": observed,
        "record_attempted_at": attempted,
        "source_record_sha256": row["source_record_sha256"],
        "parsed_quote_sha256": row["parsed_quote_sha256"],
    }


def qualify_kis_realtime_trade_observation(
    *,
    row_packet: bytes,
    expected_row_packet_sha256: str,
    owner_receipt: bytes,
    expected_owner_receipt_sha256: str,
    calendar_packet: bytes,
    expected_calendar_packet_sha256: str,
    evaluation_at: str,
    contract: dict | None = None,
) -> dict:
    """Return a qualification derived from externally pinned inputs.

    The expected hashes are trust anchors owned by the protected deployment;
    callers must not derive and accept them from untrusted submitted bytes.
    """
    repository_contract = load_contract()
    if contract is not None:
        require(
            canonical_bytes(contract) == canonical_bytes(repository_contract),
            "CALLER_CONTRACT_OVERRIDE_REJECTED",
        )
    contract = repository_contract
    evaluation = _instant(evaluation_at, "EVALUATION_AT_INVALID")
    rows_sha = _sha256(expected_row_packet_sha256, "ROW_PACKET_TRUST_ANCHOR_INVALID")
    rows_doc = _trusted(row_packet, rows_sha, "ROW_PACKET")
    _keys(rows_doc, {"schema_version", "evidence_class", "session_date", "rows"}, "ROW_PACKET_SCHEMA_INVALID")
    require(rows_doc["schema_version"] == "kis_realtime_trade_row_packet/1", "ROW_PACKET_SCHEMA_INVALID")
    evidence_class = rows_doc["evidence_class"]
    require(evidence_class in contract["evidence_classes"], "EVIDENCE_CLASS_INVALID")
    session_date = _date(rows_doc["session_date"], "SESSION_DATE_INVALID").isoformat()
    owner = _owner(
        owner_receipt, expected_owner_receipt_sha256, rows_sha,
        session_date, evidence_class, contract,
    )
    checked_calendar = _calendar(
        calendar_packet, expected_calendar_packet_sha256,
        session_date, evaluation, contract,
    )
    export_started = _instant(owner["export_started_at"], "EXPORT_STARTED_AT_INVALID")
    export_available = _instant(owner["export_available_at"], "EXPORT_AVAILABLE_AT_INVALID")
    require(export_started <= export_available <= evaluation, "EXPORT_TIME_ORDER_INVALID")
    require(isinstance(rows_doc["rows"], list) and bool(rows_doc["rows"]), "ROWS_REQUIRED")
    checked = [_row(row, session_date, contract) for row in rows_doc["rows"]]
    ids = [row["record_id"] for row in checked]
    owner_range = owner["row_range"]
    require(
        ids == list(range(owner_range["first"], owner_range["last"] + 1))
        and len(ids) == owner_range["count"],
        "ROW_RANGE_OR_TRUNCATION_MISMATCH",
    )
    require(
        all(row["record_attempted_at"] <= export_started for row in checked),
        "ROW_NOT_AVAILABLE_AT_EXPORT",
    )
    require(
        all(
            checked_calendar["_open"] <= row["trade_at"] < checked_calendar["_close"]
            for row in checked
        ),
        "TRADE_OUTSIDE_OPEN_REGULAR_SESSION",
    )
    per_symbol = []
    for symbol in contract["approved_symbols"]:
        subset = [row for row in checked if row["symbol"] == symbol]
        require(bool(subset), "APPROVED_SYMBOL_OBSERVATION_MISSING")
        redacted = [
            {
                "record_id": row["record_id"],
                "trade_at": row["trade_at"].isoformat(timespec="seconds"),
                "observed_at": row["observed_at"].isoformat(),
                "record_attempted_at": row["record_attempted_at"].isoformat(),
                "source_record_sha256": row["source_record_sha256"],
                "parsed_quote_sha256": row["parsed_quote_sha256"],
            }
            for row in subset
        ]
        per_symbol.append({
            "symbol": symbol,
            "record_count": len(subset),
            "first_trade_at": min(row["trade_at"] for row in subset).isoformat(timespec="seconds"),
            "last_trade_at": max(row["trade_at"] for row in subset).isoformat(timespec="seconds"),
            "first_observation_minute": min(row["observation_minute"] for row in subset).isoformat(timespec="seconds"),
            "last_observation_minute": max(row["observation_minute"] for row in subset).isoformat(timespec="seconds"),
            "row_chain_sha256": digest(canonical_bytes(redacted)),
        })
    live = evidence_class == "LIVE_NATURAL"
    result = {
        "schema_version": "kis_realtime_trade_observation_qualification/1",
        "contract_version": contract["contract_version"],
        "market": contract["market"],
        "environment": contract["environment"],
        "evidence_class": evidence_class,
        "session_date": session_date,
        "evaluation_at": evaluation.isoformat(),
        "status": (
            "QUALIFIED_LIVE_NATURAL_RAW_OBSERVATION"
            if live else "TEST_ONLY_NON_PROMOTABLE"
        ),
        "actual_source_qualification": (
            "QUALIFIED_LIVE_NATURAL_RAW_OBSERVATION_ONLY"
            if live else "TEST_ONLY_NON_PROMOTABLE"
        ),
        "actual_source_admitted": live,
        "paper_market_data_source_qualified": live,
        "source_pins": {
            "row_packet_sha256": rows_sha,
            "owner_receipt_sha256": expected_owner_receipt_sha256,
            "calendar_packet_sha256": expected_calendar_packet_sha256,
            "calendar_official_response_sha256": checked_calendar["source_sha256"],
            "collector_sha256": contract["deployed_source_pins"]["collector_sha256"],
            "observation_retention_sha256": contract["deployed_source_pins"]["observation_retention_sha256"],
            "official_source_commit": contract["official_source"]["commit"],
            "official_source_blob_sha1": contract["official_source"]["git_blob_sha1"],
        },
        "owner_receipt_ref": owner["owner_receipt_ref"],
        "row_range": copy.deepcopy(owner_range),
        "candidates": per_symbol,
        "timestamp_semantics": contract["timestamp_semantics"]["trade_instant"],
        "minute_floor_use": contract["timestamp_semantics"]["minute_floor_use"],
        "availability_semantics": contract["timestamp_semantics"]["availability"],
        "raw_and_parsed_hashes_revalidated": True,
        "retained_decoded_source_record_bytes_revalidated": True,
        "original_provider_message_bytes_retained": False,
        "raw_provider_bytes_authenticated": False,
        "completed_minute_ohlcv_qualified": False,
        "all_trades_exhaustiveness_qualified": False,
        "provider_sequence_available": False,
        "connection_gap_ledger_available": False,
        "completed_bar_provider_allowlist_changed": False,
        "authority": copy.deepcopy(contract["authority"]),
    }
    result["qualification_sha256"] = digest(canonical_bytes(result))
    return result


def validate_qualification(packet: dict, **inputs) -> dict:
    expected = qualify_kis_realtime_trade_observation(**inputs)
    require(canonical_bytes(packet) == canonical_bytes(expected), "QUALIFICATION_REDERIVATION_MISMATCH")
    return copy.deepcopy(expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("row_packet", type=Path)
    parser.add_argument("owner_receipt", type=Path)
    parser.add_argument("calendar_packet", type=Path)
    parser.add_argument("--row-sha256", required=True)
    parser.add_argument("--owner-sha256", required=True)
    parser.add_argument("--calendar-sha256", required=True)
    parser.add_argument("--evaluation-at", required=True)
    args = parser.parse_args()
    result = qualify_kis_realtime_trade_observation(
        row_packet=args.row_packet.read_bytes(),
        expected_row_packet_sha256=args.row_sha256,
        owner_receipt=args.owner_receipt.read_bytes(),
        expected_owner_receipt_sha256=args.owner_sha256,
        calendar_packet=args.calendar_packet.read_bytes(),
        expected_calendar_packet_sha256=args.calendar_sha256,
        evaluation_at=args.evaluation_at,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
