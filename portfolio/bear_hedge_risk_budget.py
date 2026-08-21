#!/usr/bin/env python3
"""P6-03 explicit-only Bear / Hedge risk-budget registry validator."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "bear_hedge_risk_budget_contract.json"
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class BearHedgeBudgetError(ValueError):
    """Fail-closed Bear/Hedge budget contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BearHedgeBudgetError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "bear_hedge_risk_budget/1",
        "input_schema_version": "bear_hedge_budget_set/1",
        "output_schema_version": "bear_hedge_budget_packet/2",
        "repository_default_status": "BLOCKED_UNTIL_EXTERNAL_BUDGET_RATIFIED",
        "approval_mode": "EXPLICIT_CIO_RATIFIED_ONLY",
        "effective_interval": "[valid_from, valid_to)",
        "allowed_scope_types": ["MARKET", "PORTFOLIO_TOTAL"],
        "allowed_scope_ids": ["CRYPTO", "GLOBAL", "KOREA", "US"],
        "budget_unit": "NAV_FRACTION",
        "long_budget_separation": "EXACT_DISTINCT_SHA_REQUIRED",
        "input_authority": {
            "budget_definition_authorized": True,
            "automatic_budget_allocation_authorized": False,
            "hedge_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "budget_registry_validation_only": True,
            "repository_default_budget_authorized": False,
            "automatic_budget_allocation_authorized": False,
            "hedge_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise BearHedgeBudgetError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise BearHedgeBudgetError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise BearHedgeBudgetError(code)
    return value


def _id(value, code: str) -> str:
    value = _text(value, code)
    if ID_RE.fullmatch(value) is None:
        raise BearHedgeBudgetError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BearHedgeBudgetError(code)
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise BearHedgeBudgetError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise BearHedgeBudgetError(code) from exc
    if parsed.isoformat() != value:
        raise BearHedgeBudgetError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise BearHedgeBudgetError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise BearHedgeBudgetError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise BearHedgeBudgetError(code)
    return value


def _number(value, code: str, *, positive: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BearHedgeBudgetError(code)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise BearHedgeBudgetError(code)
    return value


def _interval(start, end, context: str) -> tuple[str, str | None]:
    start = _date(start, f"VALID_FROM_INVALID:{context}")
    if end is not None:
        end = _date(end, f"VALID_TO_INVALID:{context}")
        if end <= start:
            raise BearHedgeBudgetError(f"EFFECTIVE_INTERVAL_EMPTY:{context}")
    return start, end


def _active(start: str, end: str | None, as_of: str) -> bool:
    return start <= as_of and (end is None or as_of < end)


def _overlap(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def _record(row: dict, contract: dict) -> dict:
    fields = {
        "risk_budget_id", "scope_type", "scope_id", "valid_from", "valid_to",
        "max_loss", "max_gross_exposure", "holding_horizon_days", "unit",
        "separate_from_long_budget", "eligible_instrument_registry_sha256",
        "budget_basis_ref", "budget_basis_sha256", "notes",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise BearHedgeBudgetError("BUDGET_RECORD_FIELDS_MISMATCH")
    budget_id = _id(row.get("risk_budget_id"), "RISK_BUDGET_ID_INVALID")
    scope_type = row.get("scope_type")
    scope_id = row.get("scope_id")
    if scope_type not in contract["allowed_scope_types"]:
        raise BearHedgeBudgetError(f"SCOPE_TYPE_INVALID:{budget_id}:{scope_type}")
    if scope_id not in contract["allowed_scope_ids"]:
        raise BearHedgeBudgetError(f"SCOPE_ID_INVALID:{budget_id}:{scope_id}")
    if (scope_type == "PORTFOLIO_TOTAL") != (scope_id == "GLOBAL"):
        raise BearHedgeBudgetError(f"SCOPE_PAIR_INVALID:{budget_id}")
    start, end = _interval(row.get("valid_from"), row.get("valid_to"), budget_id)
    horizon = row.get("holding_horizon_days")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
        raise BearHedgeBudgetError(f"HOLDING_HORIZON_INVALID:{budget_id}")
    notes = row.get("notes")
    if not isinstance(notes, list) or notes != sorted(set(notes)) or any(
        not isinstance(item, str) or not item.strip() or item != item.strip()
        for item in notes
    ):
        raise BearHedgeBudgetError(f"NOTES_INVALID:{budget_id}")
    if row.get("separate_from_long_budget") is not True:
        raise BearHedgeBudgetError(f"LONG_BUDGET_SEPARATION_REQUIRED:{budget_id}")
    if row.get("unit") != contract["budget_unit"]:
        raise BearHedgeBudgetError(f"BUDGET_UNIT_INVALID:{budget_id}")
    return {
        "risk_budget_id": budget_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "valid_from": start,
        "valid_to": end,
        "max_loss": _number(row.get("max_loss"), f"MAX_LOSS_INVALID:{budget_id}", positive=True),
        "max_gross_exposure": _number(
            row.get("max_gross_exposure"),
            f"MAX_GROSS_EXPOSURE_INVALID:{budget_id}",
            positive=True,
        ),
        "holding_horizon_days": horizon,
        "unit": contract["budget_unit"],
        "separate_from_long_budget": True,
        "eligible_instrument_registry_sha256": _sha(
            row.get("eligible_instrument_registry_sha256"),
            f"ELIGIBILITY_REGISTRY_SHA_INVALID:{budget_id}",
        ),
        "budget_basis_ref": _text(
            row.get("budget_basis_ref"), f"BUDGET_BASIS_REF_INVALID:{budget_id}"
        ),
        "budget_basis_sha256": _sha(
            row.get("budget_basis_sha256"), f"BUDGET_BASIS_SHA_INVALID:{budget_id}"
        ),
        "notes": list(notes),
    }


def _validate_set(value: dict, as_of_date: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "budget_set_id", "status",
        "ratified_by", "ratified_at", "valid_from", "valid_to",
        "portfolio_loss_budget_ref", "portfolio_loss_budget_sha256",
        "long_budget_ref", "long_budget_sha256", "records", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise BearHedgeBudgetError("BUDGET_SET_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["input_authority"]
    ):
        raise BearHedgeBudgetError("BUDGET_SET_IDENTITY_INVALID")
    set_id = _text(value.get("budget_set_id"), "BUDGET_SET_ID_INVALID")
    ratified_at = _utc(value.get("ratified_at"), "BUDGET_RATIFIED_AT_INVALID")
    start, end = _interval(value.get("valid_from"), value.get("valid_to"), set_id)
    if ratified_at[:10] > start:
        raise BearHedgeBudgetError("BUDGET_RATIFIED_AFTER_EFFECTIVE_START")
    if not _active(start, end, as_of_date):
        raise BearHedgeBudgetError("BUDGET_SET_NOT_EFFECTIVE")
    portfolio_sha = _sha(
        value.get("portfolio_loss_budget_sha256"), "PORTFOLIO_LOSS_BUDGET_SHA_INVALID"
    )
    long_sha = _sha(value.get("long_budget_sha256"), "LONG_BUDGET_SHA_INVALID")
    if portfolio_sha == long_sha:
        raise BearHedgeBudgetError("LONG_BUDGET_SHA_MUST_BE_DISTINCT")
    raw = value.get("records")
    if not isinstance(raw, list) or not raw:
        raise BearHedgeBudgetError("BUDGET_RECORDS_EMPTY")
    records = sorted(
        (_record(row, contract) for row in raw),
        key=lambda row: (row["risk_budget_id"], row["valid_from"]),
    )
    groups: dict[str, list[dict]] = {}
    for row in records:
        if row["valid_from"] < start or (
            end is not None and (row["valid_to"] is None or row["valid_to"] > end)
        ):
            raise BearHedgeBudgetError(
                f"BUDGET_OUTSIDE_SET_INTERVAL:{row['risk_budget_id']}"
            )
        groups.setdefault(row["risk_budget_id"], []).append(row)
    active = []
    for budget_id, rows in sorted(groups.items()):
        identity = {(row["scope_type"], row["scope_id"], row["unit"]) for row in rows}
        if len(identity) != 1:
            raise BearHedgeBudgetError(f"BUDGET_IDENTITY_DRIFT:{budget_id}")
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                if _overlap(
                    left["valid_from"], left["valid_to"],
                    right["valid_from"], right["valid_to"],
                ):
                    raise BearHedgeBudgetError(f"BUDGET_INTERVAL_OVERLAP:{budget_id}")
        current = [row for row in rows if _active(row["valid_from"], row["valid_to"], as_of_date)]
        if len(current) > 1:
            raise BearHedgeBudgetError(f"ACTIVE_BUDGET_COUNT_INVALID:{budget_id}")
        active.extend(copy.deepcopy(current))
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "budget_set_id": set_id,
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": ratified_at,
        "valid_from": start,
        "valid_to": end,
        "portfolio_loss_budget_ref": _text(
            value.get("portfolio_loss_budget_ref"), "PORTFOLIO_LOSS_BUDGET_REF_INVALID"
        ),
        "portfolio_loss_budget_sha256": portfolio_sha,
        "long_budget_ref": _text(value.get("long_budget_ref"), "LONG_BUDGET_REF_INVALID"),
        "long_budget_sha256": long_sha,
        "records": records,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise BearHedgeBudgetError("BUDGET_SET_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest, "active": active}


def _source_packet(validated: dict) -> dict:
    packet = copy.deepcopy(validated["normalized"])
    packet["packet_sha256"] = validated["packet_sha256"]
    return packet


def _assemble(checked: dict, as_of: str, contract: dict) -> dict:
    active = checked["active"]
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "BEAR_HEDGE_BUDGET_SET_VALIDATED",
        "as_of_date": as_of,
        "budget_set_id": checked["normalized"]["budget_set_id"],
        "active_budgets": active,
        "summary": {
            "active_count": len(active),
            "portfolio_total_count": sum(row["scope_type"] == "PORTFOLIO_TOTAL" for row in active),
            "market_count": sum(row["scope_type"] == "MARKET" for row in active),
            "scope_ids": sorted(row["scope_id"] for row in active),
        },
        "budget_usage": None,
        "hedge_size": None,
        "order_intents": [],
        "source_packets": {"BUDGET_SET": _source_packet(checked)},
        "lineage": {
            "budget_set_packet_sha256": checked["packet_sha256"],
            "portfolio_loss_budget_sha256": checked["normalized"]["portfolio_loss_budget_sha256"],
            "long_budget_sha256": checked["normalized"]["long_budget_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "LIVE_PORTFOLIO_USAGE_NOT_CONNECTED",
            "HEDGE_SIZING_NOT_AUTHORIZED",
            "ORDER_NOT_AUTHORIZED",
        ],
    }


def build_packet(budget_set: dict, as_of_date: str, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    as_of = _date(as_of_date, "AS_OF_DATE_INVALID")
    checked = _validate_set(budget_set, as_of, contract)
    packet = _assemble(checked, as_of, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "as_of_date",
        "budget_set_id", "active_budgets", "summary", "budget_usage",
        "hedge_size", "order_intents", "source_packets", "lineage", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise BearHedgeBudgetError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
    ):
        raise BearHedgeBudgetError("OUTPUT_IDENTITY_INVALID")
    as_of = _date(packet.get("as_of_date"), "OUTPUT_AS_OF_DATE_INVALID")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {"BUDGET_SET"}:
        raise BearHedgeBudgetError("OUTPUT_SOURCE_PACKETS_INVALID")
    checked = _validate_set(sources["BUDGET_SET"], as_of, contract)
    expected = _assemble(checked, as_of, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_PACKET_SHA_INVALID")
    if actual != expected:
        raise BearHedgeBudgetError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise BearHedgeBudgetError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise BearHedgeBudgetError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(budget_set_path: Path, as_of_date: str, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(budget_set_path), as_of_date))
        return 0
    except (BearHedgeBudgetError, OSError, TypeError, ValueError) as exc:
        print(f"Bear/Hedge budget failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("budget_set", type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.budget_set, args.as_of_date, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
