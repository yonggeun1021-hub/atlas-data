#!/usr/bin/env python3
"""Validate offline KOFIA liquidity responses without promoting them to a factor.

The official API documents observation dates and numeric fields, but does not
publish a temporal coverage range or a source release-time contract.  This
module therefore records a complete-response coverage observation while
keeping ``available_at`` and decision eligibility explicitly unqualified.

It never calls the network, reads a service key, or writes inside the tracked
repository tree.
"""

from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config" / "kofia_liquidity_contract.json"
UTC = dt.timezone.utc
KST = ZoneInfo("Asia/Seoul")
BAS_DT = re.compile(r"^[0-9]{8}$")
DECIMAL_TEXT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
GROUPED_DECIMAL_TEXT = re.compile(
    r"^(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})+)(?:\.[0-9]+)?$"
)
EXPECTED_NUMERIC_TRANSPORT_POLICY = {
    "official_swagger_type": "number",
    "observed_compatibility": "canonical_unsigned_decimal_string",
    "accepted_json_types": ["number", "canonical_numeric_string"],
    "accepted_string_pattern": r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
    "rejected_string_forms": [
        "blank",
        "surrounding_whitespace",
        "sign",
        "exponent",
        "group_separator",
    ],
}
EXPECTED_OPERATIONS = [
    {
        "name": "investor_deposits",
        "operation_id": "getSecuritiesMarketTotalCapitalInfo",
        "path": "/getSecuritiesMarketTotalCapitalInfo",
        "observation_date_field": "basDt",
        "primary_value_field": "invrDpsgAmt",
        "required_fields": [
            "onbdDrvPrdTrRcAdvAmt",
            "toCstRpchCndBndSlgBal",
            "brkTrdUcolMny",
            "brkTrdUcolMnyVsOppsTrdAmt",
            "ucolMnyVsOppsTrdRlImpt",
            "basDt",
            "invrDpsgAmt",
        ],
    },
    {
        "name": "credit_financing",
        "operation_id": "getGrantingOfCreditBalanceInfo",
        "path": "/getGrantingOfCreditBalanceInfo",
        "observation_date_field": "basDt",
        "primary_value_field": "crdTrFingWhl",
        "required_fields": [
            "basDt",
            "crdTrFingWhl",
            "crdTrFingScrs",
            "crdTrFingKosdaq",
            "crdTrLndrWhl",
            "crdTrLndrScrs",
            "crdTrLndrKosdaq",
            "sbscCapLn",
            "dpsgScrtMogFing",
        ],
    },
]


class KofiaContractError(RuntimeError):
    """Fail-closed KOFIA source qualification error."""


def fail(code: str, detail: str) -> None:
    raise KofiaContractError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("CONTRACT_INVALID", str(exc))

    expected = {
        "schema_version",
        "contract_version",
        "source_authority",
        "gateway_operator",
        "catalog_id",
        "catalog_url",
        "source_portal_url",
        "base_url",
        "authentication",
        "response_format",
        "success_result_code",
        "numeric_transport_policy",
        "qualification",
        "operations",
    }
    if set(contract) != expected or contract.get("schema_version") != 1:
        fail("CONTRACT_INVALID", "schema or fields")
    if contract.get("contract_version") != "kofia_liquidity_source/v2":
        fail("CONTRACT_INVALID", "contract_version")
    if contract.get("source_authority") != (
        "Korea Financial Investment Association"
    ):
        fail("CONTRACT_INVALID", "source_authority")
    if contract.get("gateway_operator") != "Financial Services Commission":
        fail("CONTRACT_INVALID", "gateway_operator")
    if contract.get("catalog_id") != "15094809":
        fail("CONTRACT_INVALID", "catalog_id")
    if contract.get("catalog_url") != (
        "https://www.data.go.kr/data/15094809/openapi.do"
    ):
        fail("CONTRACT_INVALID", "catalog_url")
    if contract.get("source_portal_url") != (
        "https://freesis.kofia.or.kr/stat/main.do"
    ):
        fail("CONTRACT_INVALID", "source_portal_url")
    if contract.get("base_url") != (
        "https://apis.data.go.kr/1160100/service/"
        "GetKofiaStatisticsInfoService"
    ):
        fail("CONTRACT_INVALID", "base_url")
    if (
        contract.get("authentication")
        != "data_go_kr_service_key_required"
        or contract.get("response_format") != "json"
        or contract.get("success_result_code") != "00"
    ):
        fail("CONTRACT_INVALID", "transport contract")
    if contract.get("numeric_transport_policy") != (
        EXPECTED_NUMERIC_TRANSPORT_POLICY
    ):
        fail("CONTRACT_INVALID", "numeric transport policy")

    qualification = contract.get("qualification")
    expected_qualification = {
        "historical_range_status": "unverified",
        "source_release_time_status": "unverified",
        "api_field_unit_status": "unverified",
        "available_at_policy": "first_seen_primary_evidence_required",
        "portal_update_cycle_is_available_at": False,
        "capture_time_is_available_at": False,
        "decision_eligible": False,
    }
    if qualification != expected_qualification:
        fail("CONTRACT_INVALID", "qualification boundary")

    if contract.get("operations") != EXPECTED_OPERATIONS:
        fail("CONTRACT_INVALID", "official operation schema")
    return contract


def parse_captured_at(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail("CAPTURE_TIME_INVALID", str(value))
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        fail("CAPTURE_TIME_INVALID", str(exc))
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        fail("CAPTURE_TIME_INVALID", value)
    return parsed.astimezone(UTC)


def parse_observation_date(value: object, label: str) -> dt.date:
    if not isinstance(value, str) or BAS_DT.fullmatch(value) is None:
        fail("OBSERVATION_DATE_INVALID", label)
    try:
        return dt.datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        fail("OBSERVATION_DATE_INVALID", label)


def safe_value_shape(value: object) -> str:
    """Describe an unexpected source value without logging its contents."""
    if isinstance(value, str):
        stripped = value.strip()
        return (
            f"str(length={len(value)},stripped_length={len(stripped)},"
            f"decimal_text={str(DECIMAL_TEXT.fullmatch(stripped) is not None).lower()},"
            "grouped_decimal_text="
            f"{str(GROUPED_DECIMAL_TEXT.fullmatch(stripped) is not None).lower()})"
        )
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, list):
        return f"list(length={len(value)})"
    if isinstance(value, dict):
        return f"object(field_count={len(value)})"
    return type(value).__name__


def parse_nonnegative_number(value: object, label: str) -> Decimal:
    if isinstance(value, str):
        if DECIMAL_TEXT.fullmatch(value) is None:
            fail("VALUE_TEXT_INVALID", f"{label} observed={safe_value_shape(value)}")
        parsed = Decimal(value)
    elif isinstance(value, Decimal):
        parsed = value
    else:
        fail("VALUE_TYPE_INVALID", f"{label} observed={safe_value_shape(value)}")
    if not parsed.is_finite() or parsed < 0:
        fail("VALUE_INVALID", label)
    return parsed


def parse_nonnegative_integer(value: object, label: str) -> int:
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, Decimal) and value.is_finite():
        integral = value.to_integral_value()
        if value == integral and integral >= 0:
            return int(integral)
    fail("PAGINATION_INVALID", label)


def read_payload(path: Path) -> tuple[bytes, object]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        fail("RAW_RESPONSE_INVALID", str(exc))
    try:
        payload = json.loads(
            raw,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=reject_json_constant,
        )
    except (json.JSONDecodeError, InvalidOperation) as exc:
        fail("RAW_RESPONSE_INVALID", str(exc))
    return raw, payload


def response_root(payload: object) -> dict:
    if not isinstance(payload, dict):
        fail("PAYLOAD_SHAPE_INVALID", "top level")
    if set(payload) == {"response"}:
        payload = payload["response"]
    if not isinstance(payload, dict) or set(payload) != {"header", "body"}:
        fail("PAYLOAD_SHAPE_INVALID", "response container")
    return payload


def response_rows(payload: object, operation: dict) -> dict:
    root = response_root(payload)
    header = root["header"]
    if not isinstance(header, dict) or set(header) != {
        "resultCode",
        "resultMsg",
    }:
        fail("PAYLOAD_SHAPE_INVALID", "header")
    if header["resultCode"] != "00":
        fail(
            "SOURCE_ERROR",
            f"{header.get('resultCode')}: {header.get('resultMsg')}",
        )
    if not isinstance(header["resultMsg"], str):
        fail("PAYLOAD_SHAPE_INVALID", "resultMsg")

    body = root["body"]
    if not isinstance(body, dict) or set(body) != {
        "numOfRows",
        "pageNo",
        "totalCount",
        "items",
    }:
        fail("PAYLOAD_SHAPE_INVALID", "body")
    page_no = parse_nonnegative_integer(body["pageNo"], "pageNo")
    num_rows = parse_nonnegative_integer(body["numOfRows"], "numOfRows")
    total_count = parse_nonnegative_integer(body["totalCount"], "totalCount")
    items = body["items"]
    if not isinstance(items, dict) or set(items) != {"item"}:
        fail("PAYLOAD_SHAPE_INVALID", "items")
    rows = items["item"]
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        fail("PAYLOAD_EMPTY", operation["name"])
    if page_no != 1 or total_count != len(rows) or num_rows < total_count:
        fail(
            "COVERAGE_PROBE_INCOMPLETE",
            f"page={page_no} rows={len(rows)} total={total_count} "
            f"page_size={num_rows}",
        )

    required = set(operation["required_fields"])
    normalized = []
    dates = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != required:
            fail("ROW_SCHEMA_INVALID", f"{operation['name']} row {index}")
        observed = parse_observation_date(
            row[operation["observation_date_field"]],
            f"{operation['name']} row {index}",
        )
        dates.append(observed)
        values = {}
        for field in operation["required_fields"]:
            if field == operation["observation_date_field"]:
                continue
            values[field] = format(
                parse_nonnegative_number(
                    row[field],
                    f"{operation['name']} row {index} {field}",
                ),
                "f",
            )
        normalized.append(
            {
                "observation_date": observed.isoformat(),
                "values": values,
            }
        )

    if len(dates) != len(set(dates)):
        fail("OBSERVATION_DATE_DUPLICATE", operation["name"])
    normalized.sort(key=lambda item: item["observation_date"])
    return {
        "rows": normalized,
        "total_count": total_count,
        "earliest": min(dates),
        "latest": max(dates),
    }


def operation_map(contract: dict) -> dict[str, dict]:
    return {item["name"]: item for item in contract["operations"]}


def build_qualification(
    response_paths: dict[str, Path],
    captured_at: str,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    parsed_capture = parse_captured_at(captured_at)
    operations = operation_map(contract)
    if set(response_paths) != set(operations):
        fail("INPUT_INVENTORY_INVALID", str(sorted(response_paths)))

    results = {}
    capture_kst_date = parsed_capture.astimezone(KST).date()
    for name in sorted(operations):
        raw, payload = read_payload(Path(response_paths[name]))
        parsed = response_rows(payload, operations[name])
        if parsed["latest"] > capture_kst_date:
            fail(
                "OBSERVATION_FROM_FUTURE",
                f"{name}: {parsed['latest'].isoformat()}",
            )
        latest_row = parsed["rows"][-1]
        results[name] = {
            "operation_id": operations[name]["operation_id"],
            "endpoint": contract["base_url"] + operations[name]["path"],
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "row_count": parsed["total_count"],
            "full_response_observed": True,
            "earliest_observation_date": parsed["earliest"].isoformat(),
            "latest_observation_date": parsed["latest"].isoformat(),
            "primary_value_field": operations[name]["primary_value_field"],
            "latest_primary_value_raw": latest_row["values"][
                operations[name]["primary_value_field"]
            ],
            "api_field_unit_status": "unverified",
        }

    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "catalog_id": contract["catalog_id"],
        "captured_at_utc": parsed_capture.isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
        "coverage_evidence_status": "complete_response_observed",
        "historical_range_status": "unverified",
        "source_release_time_status": "unverified",
        "available_at": None,
        "available_at_evidence": "none",
        "portal_update_cycle_used_as_available_at": False,
        "capture_time_used_as_available_at": False,
        "operations": results,
        "decision_eligible": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def write_qualification(payload: dict, target: Path) -> Path:
    target = Path(target).resolve()
    try:
        target.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        fail("TRACKED_OUTPUT_FORBIDDEN", str(target))
    if not target.parent.is_dir():
        fail("OUTPUT_PARENT_MISSING", str(target.parent))
    if target.exists():
        fail("OUTPUT_EXISTS", str(target))

    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an offline KOFIA source qualification report."
    )
    parser.add_argument("--investor-deposits", type=Path, required=True)
    parser.add_argument("--credit-financing", type=Path, required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload = build_qualification(
        {
            "investor_deposits": args.investor_deposits,
            "credit_financing": args.credit_financing,
        },
        args.captured_at,
    )
    write_qualification(payload, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
