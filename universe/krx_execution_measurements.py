#!/usr/bin/env python3
"""Build private KRX/KIS execution measurements and a redacted public summary.

All provider I/O happens before this offline transformer.  KRX turnover and
KIS ten-level order-book responses are read-only evidence; the module has no
HTTP client, credential handling, broker POST, order, or policy-ratification
path.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_execution_measurement_contract.json"
INPUT_SCHEMA_VERSION = "krx_execution_measurement_input/1"
PRIVATE_SCHEMA_VERSION = "krx_execution_measurement_private/1"
PUBLIC_SCHEMA_VERSION = "krx_execution_measurement_public/1"
CAPTURE_SCHEMA_VERSION = "kis_domestic_order_book_capture/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHORT_CODE_RE = re.compile(r"^[A-Z0-9]{1,9}$")


class MeasurementError(ValueError):
    """Evidence cannot be accepted without violating the contract."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_bytes_json(path: Path, code: str) -> tuple[bytes, dict]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeasurementError(f"{code}:{type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise MeasurementError(f"{code}:ROOT_NOT_OBJECT")
    return raw, value


def _parse_time(value: object, code: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise MeasurementError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MeasurementError(code)
    return parsed.astimezone(dt.timezone.utc)


def _decimal(value: object, code: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise MeasurementError(code) from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise MeasurementError(code)
    return parsed


def _decimal_text(value: Decimal, places: int | None = None) -> str:
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places))
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    _, value = _read_bytes_json(path, "CONTRACT_READ_FAILED")
    if value.get("schema_version") != 1:
        raise MeasurementError("CONTRACT_SCHEMA_MISMATCH")
    if value.get("contract_version") != "krx_execution_measurement/1":
        raise MeasurementError("CONTRACT_VERSION_MISMATCH")
    if value.get("input_schema_version") != INPUT_SCHEMA_VERSION:
        raise MeasurementError("CONTRACT_INPUT_SCHEMA_MISMATCH")
    if value.get("private_output_schema_version") != PRIVATE_SCHEMA_VERSION:
        raise MeasurementError("CONTRACT_PRIVATE_SCHEMA_MISMATCH")
    if value.get("public_output_schema_version") != PUBLIC_SCHEMA_VERSION:
        raise MeasurementError("CONTRACT_PUBLIC_SCHEMA_MISMATCH")
    families = value.get("measurement_families")
    if families != ["order_book_depth", "slippage", "spread", "turnover"]:
        raise MeasurementError("CONTRACT_FAMILIES_MISMATCH")
    policy = value.get("policy_candidates")
    if not isinstance(policy, dict) or set(policy) != set(families):
        raise MeasurementError("CONTRACT_POLICY_INVALID")
    for family in families:
        row = policy[family]
        if not isinstance(row, dict) or row.get("status") != "UNRATIFIED":
            raise MeasurementError("CONTRACT_POLICY_RATIFIED")
        if any(item is not None for key, item in row.items() if key != "status"):
            raise MeasurementError("CONTRACT_POLICY_THRESHOLD_SET")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("read_only_evidence") is not True:
        raise MeasurementError("CONTRACT_AUTHORITY_INVALID")
    if any(item is not False for key, item in authority.items() if key != "read_only_evidence"):
        raise MeasurementError("CONTRACT_AUTHORITY_PROMOTED")
    source = value.get("kis_order_book_source", {})
    if source.get("http_method") != "GET" or source.get("venue_code") != "J":
        raise MeasurementError("CONTRACT_KIS_READ_ONLY_BOUNDARY_INVALID")
    if value.get("krx_turnover_source", {}).get("http_method") != "GET":
        raise MeasurementError("CONTRACT_KRX_READ_ONLY_BOUNDARY_INVALID")
    return copy.deepcopy(value)


def identity_snapshot(records: list[dict]) -> tuple[str, list[dict]]:
    rows = sorted(
        [
            {
                "market": row.get("market"),
                "security_id": row.get("security_id"),
                "short_code": row.get("short_code"),
            }
            for row in records
        ],
        key=lambda row: (str(row["market"]), str(row["security_id"])),
    )
    if any(not all(isinstance(row[key], str) and row[key] for key in row) for row in rows):
        raise MeasurementError("REGISTRY_IDENTITY_INVALID")
    if len({row["security_id"] for row in rows}) != len(rows):
        raise MeasurementError("REGISTRY_SECURITY_ID_DUPLICATE")
    if len({row["short_code"] for row in rows}) != len(rows):
        raise MeasurementError("REGISTRY_SHORT_CODE_DUPLICATE")
    return payload_sha256(rows), rows


def _load_registry(path: Path, contract: dict) -> tuple[dict, str, list[dict]]:
    _, registry = _read_bytes_json(path, "REGISTRY_READ_FAILED")
    digest = registry.get("payload_sha256")
    unsigned = copy.deepcopy(registry)
    unsigned.pop("payload_sha256", None)
    if digest != payload_sha256(unsigned):
        raise MeasurementError("REGISTRY_PAYLOAD_SHA_MISMATCH")
    if registry.get("schema_version") != contract["registry_schema_version"]:
        raise MeasurementError("REGISTRY_SCHEMA_MISMATCH")
    if registry.get("authority", {}).get("real_order_authorized") is not False:
        raise MeasurementError("REGISTRY_AUTHORITY_PROMOTED")
    identity_sha, identity_rows = identity_snapshot(registry.get("records", []))
    return registry, identity_sha, identity_rows


def _validate_input(value: dict, contract: dict) -> dict:
    if value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise MeasurementError("INPUT_SCHEMA_MISMATCH")
    captured = _parse_time(value.get("captured_at_utc"), "CAPTURED_AT_INVALID")
    if captured > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5):
        raise MeasurementError("CAPTURED_AT_IN_FUTURE")
    day = value.get("completed_session_date")
    try:
        if not isinstance(day, str) or dt.date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError as exc:
        raise MeasurementError("COMPLETED_SESSION_DATE_INVALID") from exc
    if not isinstance(value.get("registry_path"), str) or not value["registry_path"]:
        raise MeasurementError("REGISTRY_PATH_INVALID")
    turnover = value.get("krx_turnover_snapshots", [])
    if not isinstance(turnover, list):
        raise MeasurementError("KRX_SNAPSHOTS_INVALID")
    markets = []
    for source in turnover:
        if not isinstance(source, dict):
            raise MeasurementError("KRX_SNAPSHOT_INVALID")
        market = source.get("market")
        if market not in contract["markets"] or market in markets:
            raise MeasurementError("KRX_SNAPSHOT_MARKET_INVALID")
        markets.append(market)
        if not isinstance(source.get("path"), str) or not source["path"]:
            raise MeasurementError("KRX_SNAPSHOT_PATH_INVALID")
        if source.get("source_url") != contract["krx_turnover_source"]["market_endpoints"][market]:
            raise MeasurementError("KRX_SNAPSHOT_URL_MISMATCH")
        _parse_time(source.get("retrieved_at_utc"), "KRX_RETRIEVED_AT_INVALID")
    orderbooks = value.get("kis_order_book_capture_path")
    if orderbooks is not None and (not isinstance(orderbooks, str) or not orderbooks):
        raise MeasurementError("KIS_CAPTURE_PATH_INVALID")
    return copy.deepcopy(value)


def _load_turnover(value: dict, day: str, contract: dict) -> tuple[dict[tuple[str, str], Decimal], list[dict]]:
    mapping: dict[tuple[str, str], Decimal] = {}
    lineage = []
    expected_day = day.replace("-", "")
    source_contract = contract["krx_turnover_source"]
    for source in value["krx_turnover_snapshots"]:
        raw, packet = _read_bytes_json(Path(source["path"]), "KRX_SNAPSHOT_READ_FAILED")
        rows = packet.get(source_contract["response_block"])
        if not isinstance(rows, list) or not rows:
            raise MeasurementError("KRX_RESPONSE_ROWS_MISSING")
        market = source["market"]
        for row in rows:
            if not isinstance(row, dict) or any(field not in row for field in source_contract["required_fields"]):
                raise MeasurementError("KRX_REQUIRED_FIELD_MISSING")
            if str(row["BAS_DD"]) != expected_day:
                raise MeasurementError("KRX_BUSINESS_DATE_MISMATCH")
            short = str(row["ISU_CD"]).strip()
            if not SHORT_CODE_RE.fullmatch(short):
                raise MeasurementError("KRX_SHORT_CODE_INVALID")
            key = (market, short)
            if key in mapping:
                raise MeasurementError("KRX_SHORT_CODE_DUPLICATE")
            mapping[key] = _decimal(row["ACC_TRDVAL"], "KRX_TURNOVER_INVALID")
        lineage.append({
            "market": market,
            "source_id": source_contract["source_id"],
            "source_url": source["source_url"],
            "retrieved_at_utc": _parse_time(source["retrieved_at_utc"], "INVALID").isoformat().replace("+00:00", "Z"),
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "source_byte_length": len(raw),
            "row_count": len(rows),
            "as_of_date": day,
        })
    return mapping, sorted(lineage, key=lambda row: row["market"])


def _book_side(output: dict, side: str, levels: int) -> list[tuple[Decimal, Decimal]]:
    price_prefix, quantity_prefix = (
        ("askp", "askp_rsqn") if side == "buy" else ("bidp", "bidp_rsqn")
    )
    rows = []
    for level in range(1, levels + 1):
        price_key = f"{price_prefix}{level}"
        quantity_key = f"{quantity_prefix}{level}"
        if price_key not in output or quantity_key not in output:
            raise MeasurementError("KIS_ORDER_BOOK_LEVEL_FIELD_MISSING")
        price = _decimal(output[price_key], "KIS_ORDER_BOOK_PRICE_INVALID")
        quantity = _decimal(output[quantity_key], "KIS_ORDER_BOOK_QUANTITY_INVALID")
        if price == 0 and quantity == 0:
            continue
        if price <= 0 or quantity <= 0:
            raise MeasurementError("KIS_ORDER_BOOK_LEVEL_PARTIAL")
        rows.append((price, quantity))
    if not rows:
        raise MeasurementError("KIS_ORDER_BOOK_SIDE_EMPTY")
    prices = [row[0] for row in rows]
    if side == "buy" and prices != sorted(prices):
        raise MeasurementError("KIS_ASK_LEVEL_ORDER_INVALID")
    if side == "sell" and prices != sorted(prices, reverse=True):
        raise MeasurementError("KIS_BID_LEVEL_ORDER_INVALID")
    return rows


def _curve(rows: list[tuple[Decimal, Decimal]], side: str) -> list[dict]:
    best = rows[0][0]
    cumulative_quantity = Decimal(0)
    cumulative_notional = Decimal(0)
    curve = []
    for index, (price, quantity) in enumerate(rows, start=1):
        cumulative_quantity += quantity
        cumulative_notional += price * quantity
        vwap = cumulative_notional / cumulative_quantity
        impact = (
            (vwap / best - Decimal(1)) * Decimal(10000)
            if side == "buy"
            else (Decimal(1) - vwap / best) * Decimal(10000)
        )
        curve.append({
            "level": index,
            "cumulative_quantity": _decimal_text(cumulative_quantity),
            "cumulative_notional_krw": _decimal_text(cumulative_notional),
            "vwap_krw": _decimal_text(vwap, 8),
            "impact_bps": _decimal_text(impact, 8),
        })
    return curve


def _load_orderbooks(value: dict, day: str, contract: dict) -> tuple[dict[str, dict], list[dict]]:
    path = value.get("kis_order_book_capture_path")
    if path is None:
        return {}, []
    raw, packet = _read_bytes_json(Path(path), "KIS_CAPTURE_READ_FAILED")
    if packet.get("schema_version") != CAPTURE_SCHEMA_VERSION:
        raise MeasurementError("KIS_CAPTURE_SCHEMA_MISMATCH")
    if packet.get("session_date") != day or packet.get("session_state") != "COMPLETED":
        raise MeasurementError("KIS_CAPTURE_SESSION_NOT_COMPLETED")
    session_sha = packet.get("completed_session_evidence_sha256")
    if not SHA256_RE.fullmatch(str(session_sha or "")):
        raise MeasurementError("KIS_CAPTURE_SESSION_EVIDENCE_SHA_INVALID")
    captures = packet.get("captures")
    if not isinstance(captures, list):
        raise MeasurementError("KIS_CAPTURES_INVALID")
    source = contract["kis_order_book_source"]
    output_by_security = {}
    for capture in captures:
        if not isinstance(capture, dict):
            raise MeasurementError("KIS_CAPTURE_INVALID")
        if capture.get("http_method") != "GET" or capture.get("endpoint_path") != source["endpoint_path"]:
            raise MeasurementError("KIS_CAPTURE_READ_ONLY_BOUNDARY_INVALID")
        if capture.get("tr_id") != source["tr_id"] or capture.get("venue_code") != source["venue_code"]:
            raise MeasurementError("KIS_CAPTURE_SOURCE_IDENTITY_INVALID")
        captured = _parse_time(capture.get("captured_at_utc"), "KIS_CAPTURE_TIME_INVALID")
        if captured.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat() != day:
            raise MeasurementError("KIS_CAPTURE_LOCAL_DATE_MISMATCH")
        security_id = capture.get("security_id")
        if not isinstance(security_id, str) or not security_id or security_id in output_by_security:
            raise MeasurementError("KIS_CAPTURE_SECURITY_ID_INVALID")
        response = capture.get("response")
        if not isinstance(response, dict) or str(response.get("rt_cd")) != source["accepted_response_code"]:
            raise MeasurementError("KIS_CAPTURE_RESPONSE_FAILED")
        output = response.get(source["response_block"])
        if not isinstance(output, dict) or not re.fullmatch(r"[0-9]{6}", str(output.get("aspr_acpt_hour") or "")):
            raise MeasurementError("KIS_ORDER_BOOK_ACCEPT_TIME_INVALID")
        asks = _book_side(output, "buy", source["levels"])
        bids = _book_side(output, "sell", source["levels"])
        if asks[0][0] <= bids[0][0]:
            raise MeasurementError("KIS_ORDER_BOOK_CROSSED")
        buy_curve = _curve(asks, "buy")
        sell_curve = _curve(bids, "sell")
        mid = (asks[0][0] + bids[0][0]) / Decimal(2)
        output_by_security[security_id] = {
            "captured_at_utc": captured.isoformat().replace("+00:00", "Z"),
            "order_book_accept_time_kst": output["aspr_acpt_hour"],
            "venue_code": source["venue_code"],
            "ask_depth_krw": _decimal_text(sum(price * quantity for price, quantity in asks)),
            "bid_depth_krw": _decimal_text(sum(price * quantity for price, quantity in bids)),
            "displayed_depth_krw": _decimal_text(
                sum(price * quantity for price, quantity in asks + bids)
            ),
            "spread_bps": _decimal_text((asks[0][0] - bids[0][0]) / mid * Decimal(10000), 8),
            "buy_slippage_curve": buy_curve,
            "sell_slippage_curve": sell_curve,
            "buy_full_depth_impact_bps": buy_curve[-1]["impact_bps"],
            "sell_full_depth_impact_bps": sell_curve[-1]["impact_bps"],
            "source_capture_sha256": payload_sha256(capture),
        }
    lineage = [{
        "source_id": "kis_domestic_stock_order_book",
        "source_repository": source["repository"],
        "source_commit": source["pinned_commit"],
        "endpoint_path": source["endpoint_path"],
        "tr_id": source["tr_id"],
        "http_method": "GET",
        "venue_code": "J",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_byte_length": len(raw),
        "capture_count": len(captures),
        "session_date": day,
        "completed_session_evidence_sha256": session_sha,
    }]
    return output_by_security, lineage


def _distribution(values: list[Decimal], minimum: int) -> dict:
    values = sorted(values)
    result = {"sample_count": len(values), "method": "NEAREST_RANK", "suppressed": len(values) < minimum}
    keys = (("min", 0), ("p25", 0.25), ("p50", 0.50), ("p75", 0.75), ("max", 1))
    for key, quantile in keys:
        if not values or len(values) < minimum:
            result[key] = None
        elif quantile == 0:
            result[key] = _decimal_text(values[0], 8)
        else:
            index = min(len(values) - 1, math.ceil(quantile * len(values)) - 1)
            result[key] = _decimal_text(values[index], 8)
    return result


def validate_private_packet(value: dict, contract: dict | None = None) -> dict:
    """Validate a private packet and return only its public-safe projection."""
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    digest = value.get("payload_sha256")
    unsigned = copy.deepcopy(value)
    unsigned.pop("payload_sha256", None)
    if digest != payload_sha256(unsigned):
        raise MeasurementError("PRIVATE_PACKET_PAYLOAD_SHA_MISMATCH")
    if value.get("schema_version") != PRIVATE_SCHEMA_VERSION:
        raise MeasurementError("PRIVATE_PACKET_SCHEMA_MISMATCH")
    if value.get("contract_version") != contract["contract_version"]:
        raise MeasurementError("PRIVATE_PACKET_CONTRACT_MISMATCH")
    if value.get("authority") != contract["authority"]:
        raise MeasurementError("PRIVATE_PACKET_AUTHORITY_MISMATCH")
    if value.get("policy_candidates") != contract["policy_candidates"]:
        raise MeasurementError("PRIVATE_PACKET_POLICY_MISMATCH")
    if value.get("distribution_boundary") != contract["distribution_boundary"]:
        raise MeasurementError("PRIVATE_PACKET_DISTRIBUTION_BOUNDARY_MISMATCH")
    if not SHA256_RE.fullmatch(str(value.get("registry_identity_snapshot_sha256") or "")):
        raise MeasurementError("PRIVATE_PACKET_IDENTITY_SHA_INVALID")
    if not SHA256_RE.fullmatch(str(value.get("completed_session_evidence_sha256") or "")):
        raise MeasurementError("PRIVATE_PACKET_SESSION_SHA_INVALID")
    completed_day = value.get("completed_session_date")
    try:
        if not isinstance(completed_day, str) or dt.date.fromisoformat(completed_day).isoformat() != completed_day:
            raise ValueError
    except ValueError as exc:
        raise MeasurementError("PRIVATE_PACKET_COMPLETED_DATE_INVALID") from exc
    records = value.get("records")
    if not isinstance(records, list):
        raise MeasurementError("PRIVATE_PACKET_RECORDS_INVALID")
    identities = []
    for row in records:
        if not isinstance(row, dict) or set(row) != {
            "as_of_date", "evidence_sha256", "market", "measurement_reason_codes",
            "order_book", "security_id", "turnover_krw",
        }:
            raise MeasurementError("PRIVATE_PACKET_RECORD_SHAPE_INVALID")
        if row["market"] not in contract["markets"] or not isinstance(row["security_id"], str):
            raise MeasurementError("PRIVATE_PACKET_RECORD_IDENTITY_INVALID")
        if row["as_of_date"] != completed_day:
            raise MeasurementError("PRIVATE_PACKET_RECORD_DATE_MISMATCH")
        if row["security_id"] in identities:
            raise MeasurementError("PRIVATE_PACKET_RECORD_DUPLICATE")
        identities.append(row["security_id"])
        if row["turnover_krw"] is not None:
            _decimal(row["turnover_krw"], "PRIVATE_PACKET_TURNOVER_INVALID")
        book = row["order_book"]
        if book is not None:
            expected_book_keys = {
                "ask_depth_krw", "bid_depth_krw", "buy_full_depth_impact_bps",
                "buy_slippage_curve", "captured_at_utc", "displayed_depth_krw",
                "order_book_accept_time_kst", "sell_full_depth_impact_bps",
                "sell_slippage_curve", "source_capture_sha256", "spread_bps",
                "venue_code",
            }
            if not isinstance(book, dict) or set(book) != expected_book_keys or book["venue_code"] != "J":
                raise MeasurementError("PRIVATE_PACKET_ORDER_BOOK_SHAPE_INVALID")
            captured = _parse_time(book["captured_at_utc"], "PRIVATE_PACKET_ORDER_BOOK_TIME_INVALID")
            if captured.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat() != completed_day:
                raise MeasurementError("PRIVATE_PACKET_ORDER_BOOK_DATE_MISMATCH")
            if not re.fullmatch(r"[0-9]{6}", str(book["order_book_accept_time_kst"])):
                raise MeasurementError("PRIVATE_PACKET_ORDER_BOOK_ACCEPT_TIME_INVALID")
            for key in (
                "ask_depth_krw", "bid_depth_krw", "buy_full_depth_impact_bps",
                "displayed_depth_krw", "sell_full_depth_impact_bps", "spread_bps",
            ):
                _decimal(book[key], "PRIVATE_PACKET_ORDER_BOOK_VALUE_INVALID")
            if not SHA256_RE.fullmatch(str(book["source_capture_sha256"])):
                raise MeasurementError("PRIVATE_PACKET_ORDER_BOOK_SHA_INVALID")
            for curve_key in ("buy_slippage_curve", "sell_slippage_curve"):
                curve = book[curve_key]
                if not isinstance(curve, list) or not curve:
                    raise MeasurementError("PRIVATE_PACKET_SLIPPAGE_CURVE_INVALID")
                for index, point in enumerate(curve, start=1):
                    if not isinstance(point, dict) or set(point) != {
                        "cumulative_notional_krw", "cumulative_quantity", "impact_bps",
                        "level", "vwap_krw",
                    } or point["level"] != index:
                        raise MeasurementError("PRIVATE_PACKET_SLIPPAGE_POINT_INVALID")
                    for key in ("cumulative_notional_krw", "cumulative_quantity", "impact_bps", "vwap_krw"):
                        _decimal(point[key], "PRIVATE_PACKET_SLIPPAGE_VALUE_INVALID")
    computed_coverage = {
        "candidate_count": len(records),
        "turnover": sum(row["turnover_krw"] is not None for row in records),
        "order_book_depth": sum(row["order_book"] is not None for row in records),
        "spread": sum(row["order_book"] is not None for row in records),
        "slippage": sum(row["order_book"] is not None for row in records),
    }
    if value.get("coverage") != computed_coverage:
        raise MeasurementError("PRIVATE_PACKET_COVERAGE_MISMATCH")
    minimum = contract["distribution"]["minimum_public_sample_count"]
    computed_distributions = {
        "turnover_krw": _distribution([Decimal(row["turnover_krw"]) for row in records if row["turnover_krw"] is not None], minimum),
        "displayed_depth_krw": _distribution([Decimal(row["order_book"]["displayed_depth_krw"]) for row in records if row["order_book"] is not None], minimum),
        "spread_bps": _distribution([Decimal(row["order_book"]["spread_bps"]) for row in records if row["order_book"] is not None], minimum),
        "buy_full_depth_impact_bps": _distribution([Decimal(row["order_book"]["buy_full_depth_impact_bps"]) for row in records if row["order_book"] is not None], minimum),
        "sell_full_depth_impact_bps": _distribution([Decimal(row["order_book"]["sell_full_depth_impact_bps"]) for row in records if row["order_book"] is not None], minimum),
    }
    if value.get("measured_distributions") != computed_distributions:
        raise MeasurementError("PRIVATE_PACKET_DISTRIBUTION_MISMATCH")
    lineage = value.get("source_lineage")
    if not isinstance(lineage, dict) or set(lineage) != {"krx_turnover", "kis_order_books"}:
        raise MeasurementError("PRIVATE_PACKET_LINEAGE_INVALID")
    if not isinstance(lineage["krx_turnover"], list) or not isinstance(lineage["kis_order_books"], list):
        raise MeasurementError("PRIVATE_PACKET_LINEAGE_LIST_INVALID")
    krx_keys = {
        "as_of_date", "market", "retrieved_at_utc", "row_count", "source_byte_length",
        "source_id", "source_sha256", "source_url",
    }
    for source in lineage["krx_turnover"]:
        if not isinstance(source, dict) or set(source) != krx_keys:
            raise MeasurementError("PRIVATE_PACKET_KRX_LINEAGE_SHAPE_INVALID")
        market = source["market"]
        if source["source_url"] != contract["krx_turnover_source"]["market_endpoints"].get(market):
            raise MeasurementError("PRIVATE_PACKET_KRX_LINEAGE_URL_INVALID")
        if source["source_id"] != contract["krx_turnover_source"]["source_id"] or source["as_of_date"] != completed_day:
            raise MeasurementError("PRIVATE_PACKET_KRX_LINEAGE_IDENTITY_INVALID")
        if not isinstance(source["row_count"], int) or source["row_count"] <= 0:
            raise MeasurementError("PRIVATE_PACKET_KRX_LINEAGE_COUNT_INVALID")
        if not isinstance(source["source_byte_length"], int) or source["source_byte_length"] <= 0:
            raise MeasurementError("PRIVATE_PACKET_KRX_LINEAGE_LENGTH_INVALID")
        if not SHA256_RE.fullmatch(str(source["source_sha256"])):
            raise MeasurementError("PRIVATE_PACKET_KRX_LINEAGE_SHA_INVALID")
    kis_keys = {
        "capture_count", "completed_session_evidence_sha256", "endpoint_path",
        "http_method", "session_date", "source_byte_length", "source_commit",
        "source_id", "source_repository", "source_sha256", "tr_id", "venue_code",
    }
    if len(lineage["kis_order_books"]) > 1:
        raise MeasurementError("PRIVATE_PACKET_KIS_LINEAGE_COUNT_INVALID")
    for source in lineage["kis_order_books"]:
        if not isinstance(source, dict) or set(source) != kis_keys:
            raise MeasurementError("PRIVATE_PACKET_KIS_LINEAGE_SHAPE_INVALID")
        expected = contract["kis_order_book_source"]
        if (source["http_method"], source["venue_code"], source["tr_id"], source["endpoint_path"]) != (
            "GET", "J", expected["tr_id"], expected["endpoint_path"]
        ):
            raise MeasurementError("PRIVATE_PACKET_KIS_LINEAGE_SOURCE_INVALID")
        if (source["source_repository"], source["source_commit"], source["session_date"], source["completed_session_evidence_sha256"]) != (
            expected["repository"], expected["pinned_commit"], completed_day,
            value["completed_session_evidence_sha256"],
        ):
            raise MeasurementError("PRIVATE_PACKET_KIS_LINEAGE_IDENTITY_INVALID")
        observed_books = sum(row["order_book"] is not None for row in records)
        if source["capture_count"] != observed_books:
            raise MeasurementError("PRIVATE_PACKET_KIS_LINEAGE_CAPTURE_COUNT_INVALID")
        if not isinstance(source["source_byte_length"], int) or source["source_byte_length"] <= 0:
            raise MeasurementError("PRIVATE_PACKET_KIS_LINEAGE_LENGTH_INVALID")
        if not SHA256_RE.fullmatch(str(source["source_sha256"])):
            raise MeasurementError("PRIVATE_PACKET_KIS_LINEAGE_SHA_INVALID")
    return {
        "schema_version": value["schema_version"],
        "contract_version": value["contract_version"],
        "completed_session_date": value["completed_session_date"],
        "registry_identity_snapshot_sha256": value["registry_identity_snapshot_sha256"],
        "completed_session_evidence_sha256": value["completed_session_evidence_sha256"],
        "source_lineage": copy.deepcopy(lineage),
        "coverage": computed_coverage,
        "measured_distributions": computed_distributions,
        "policy_candidates": copy.deepcopy(value["policy_candidates"]),
        "authority": copy.deepcopy(value["authority"]),
        "private_measurement_payload_sha256": digest,
    }


def build_measurements(value: dict, contract: dict | None = None) -> tuple[dict, dict]:
    expected = load_contract()
    contract = expected if contract is None else copy.deepcopy(contract)
    if contract != expected:
        raise MeasurementError("CONTRACT_CONTENT_MISMATCH")
    value = _validate_input(value, contract)
    registry, identity_sha, identity_rows = _load_registry(Path(value["registry_path"]), contract)
    day = value["completed_session_date"]
    if registry.get("latest_completed_session_date") != day:
        raise MeasurementError("REGISTRY_COMPLETED_SESSION_DATE_MISMATCH")
    if registry.get("krx_snapshot_as_of_date") != day or registry.get("krx_snapshot_freshness") != "CURRENT":
        raise MeasurementError("REGISTRY_KRX_SNAPSHOT_NOT_CURRENT")
    turnover, krx_lineage = _load_turnover(value, day, contract)
    orderbooks, kis_lineage = _load_orderbooks(value, day, contract)
    session_sha = registry.get("latest_session_evidence", {}).get("source_sha256")
    if kis_lineage and kis_lineage[0]["completed_session_evidence_sha256"] != session_sha:
        raise MeasurementError("KIS_CAPTURE_SESSION_EVIDENCE_MISMATCH")

    records = []
    candidates = [row for row in registry["records"] if row.get("screening_state") == "CATEGORICAL_CANDIDATE"]
    candidate_ids = {row["security_id"] for row in candidates}
    if set(orderbooks) - candidate_ids:
        raise MeasurementError("KIS_CAPTURE_OUTSIDE_CANDIDATE_SCOPE")
    for row in candidates:
        measurement = {
            "security_id": row["security_id"],
            "market": row["market"],
            "as_of_date": day,
            "turnover_krw": None,
            "order_book": None,
            "measurement_reason_codes": [],
        }
        value_turnover = turnover.get((row["market"], row["short_code"]))
        if value_turnover is None:
            measurement["measurement_reason_codes"].append("KRX_TURNOVER_NOT_COVERED")
        else:
            measurement["turnover_krw"] = _decimal_text(value_turnover)
        if row["security_id"] not in orderbooks:
            measurement["measurement_reason_codes"].append("KIS_ORDER_BOOK_NOT_CAPTURED")
        else:
            measurement["order_book"] = orderbooks[row["security_id"]]
        measurement["measurement_reason_codes"].sort()
        measurement["evidence_sha256"] = payload_sha256({
            "identity_snapshot_sha256": identity_sha,
            "measurement": measurement,
        })
        records.append(measurement)
    records.sort(key=lambda row: (row["market"], row["security_id"]))

    coverage = {
        "candidate_count": len(candidates),
        "turnover": sum(row["turnover_krw"] is not None for row in records),
        "order_book_depth": sum(row["order_book"] is not None for row in records),
        "spread": sum(row["order_book"] is not None for row in records),
        "slippage": sum(row["order_book"] is not None for row in records),
    }
    minimum = contract["distribution"]["minimum_public_sample_count"]
    distributions = {
        "turnover_krw": _distribution([Decimal(row["turnover_krw"]) for row in records if row["turnover_krw"] is not None], minimum),
        "displayed_depth_krw": _distribution([Decimal(row["order_book"]["displayed_depth_krw"]) for row in records if row["order_book"] is not None], minimum),
        "spread_bps": _distribution([Decimal(row["order_book"]["spread_bps"]) for row in records if row["order_book"] is not None], minimum),
        "buy_full_depth_impact_bps": _distribution([Decimal(row["order_book"]["buy_full_depth_impact_bps"]) for row in records if row["order_book"] is not None], minimum),
        "sell_full_depth_impact_bps": _distribution([Decimal(row["order_book"]["sell_full_depth_impact_bps"]) for row in records if row["order_book"] is not None], minimum),
    }
    private = {
        "schema_version": PRIVATE_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "captured_at_utc": value["captured_at_utc"],
        "completed_session_date": day,
        "registry_identity_snapshot_sha256": identity_sha,
        "registry_private_payload_sha256": registry["payload_sha256"],
        "completed_session_evidence_sha256": session_sha,
        "source_lineage": {"krx_turnover": krx_lineage, "kis_order_books": kis_lineage},
        "coverage": coverage,
        "measured_distributions": distributions,
        "policy_candidates": copy.deepcopy(contract["policy_candidates"]),
        "distribution_boundary": copy.deepcopy(contract["distribution_boundary"]),
        "authority": copy.deepcopy(contract["authority"]),
        "records": records,
    }
    private["payload_sha256"] = payload_sha256(private)
    safe = validate_private_packet(private, contract)
    public = {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "captured_at_utc": value["captured_at_utc"],
        "completed_session_date": day,
        "registry_identity_snapshot_sha256": safe["registry_identity_snapshot_sha256"],
        "completed_session_evidence_sha256": safe["completed_session_evidence_sha256"],
        "source_lineage": safe["source_lineage"],
        "coverage": safe["coverage"],
        "measured_distributions": safe["measured_distributions"],
        "policy_candidates": safe["policy_candidates"],
        "distribution_boundary": copy.deepcopy(contract["distribution_boundary"]),
        "authority": safe["authority"],
        "private_measurement_payload_sha256": safe["private_measurement_payload_sha256"],
    }
    public["payload_sha256"] = payload_sha256(public)
    return private, public


def write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--private-out", required=True, type=Path)
    parser.add_argument("--public-summary-out", required=True, type=Path)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)
    try:
        _, value = _read_bytes_json(args.input, "INPUT_READ_FAILED")
        contract = load_contract(args.contract)
        private, public = build_measurements(value, contract)
        write_json_atomic(args.private_out, private)
        write_json_atomic(args.public_summary_out, public)
    except MeasurementError as exc:
        print(f"KRX execution measurement failed reason={exc}")
        return 1
    print(
        "KRX execution measurement "
        f"coverage={public['coverage']} "
        f"session={public['completed_session_date']} "
        f"sha256={public['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
