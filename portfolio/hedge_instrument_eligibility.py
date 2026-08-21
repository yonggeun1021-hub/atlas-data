#!/usr/bin/env python3
"""P6-02 explicit-only index/sector hedge eligibility registry validator."""
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
CONTRACT_PATH = ROOT / "config" / "hedge_instrument_eligibility_contract.json"
INSTRUMENT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class HedgeEligibilityError(ValueError):
    """Fail-closed hedge eligibility registry violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HedgeEligibilityError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "hedge_instrument_eligibility/1",
        "input_schema_version": "hedge_instrument_registry/1",
        "output_schema_version": "hedge_instrument_eligibility_packet/2",
        "repository_default_status": "BLOCKED_UNTIL_EXTERNAL_REGISTRY_RATIFIED",
        "approval_mode": "EXPLICIT_CIO_RATIFIED_ONLY",
        "effective_interval": "[valid_from, valid_to)",
        "allowed_markets": ["KOREA", "US"],
        "allowed_hedge_scopes": ["INDEX", "SECTOR"],
        "required_evidence": ["cost_evidence", "tracking_error_evidence"],
        "input_authority": {
            "registry_eligibility_authorized": True,
            "automatic_instrument_selection_authorized": False,
            "hedge_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "eligibility_registry_validation_only": True,
            "repository_default_registry_authorized": False,
            "automatic_instrument_selection_authorized": False,
            "hedge_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise HedgeEligibilityError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise HedgeEligibilityError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise HedgeEligibilityError(code)
    return value


def _code(value, code: str) -> str:
    value = _text(value, code)
    if CODE_RE.fullmatch(value) is None:
        raise HedgeEligibilityError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise HedgeEligibilityError(code)
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise HedgeEligibilityError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HedgeEligibilityError(code) from exc
    if parsed.isoformat() != value:
        raise HedgeEligibilityError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise HedgeEligibilityError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise HedgeEligibilityError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise HedgeEligibilityError(code)
    return value


def _interval(start, end, context: str) -> tuple[str, str | None]:
    start = _date(start, f"VALID_FROM_INVALID:{context}")
    if end is not None:
        end = _date(end, f"VALID_TO_INVALID:{context}")
        if end <= start:
            raise HedgeEligibilityError(f"EFFECTIVE_INTERVAL_EMPTY:{context}")
    return start, end


def _active(start: str, end: str | None, as_of: str) -> bool:
    return start <= as_of and (end is None or as_of < end)


def _overlap(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def _evidence(value, label: str) -> dict:
    fields = {
        "metric", "value", "unit", "as_of_date", "available_at",
        "source_ref", "source_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise HedgeEligibilityError(f"EVIDENCE_FIELDS_MISMATCH:{label}")
    number = value.get("value")
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise HedgeEligibilityError(f"EVIDENCE_VALUE_INVALID:{label}")
    if not math.isfinite(number) or number < 0:
        raise HedgeEligibilityError(f"EVIDENCE_VALUE_INVALID:{label}")
    as_of = _date(value.get("as_of_date"), f"EVIDENCE_DATE_INVALID:{label}")
    available = _utc(value.get("available_at"), f"EVIDENCE_AVAILABLE_INVALID:{label}")
    if available[:10] < as_of:
        raise HedgeEligibilityError(f"EVIDENCE_AVAILABLE_BEFORE_AS_OF:{label}")
    return {
        "metric": _code(value.get("metric"), f"EVIDENCE_METRIC_INVALID:{label}"),
        "value": number,
        "unit": _code(value.get("unit"), f"EVIDENCE_UNIT_INVALID:{label}"),
        "as_of_date": as_of,
        "available_at": available,
        "source_ref": _text(value.get("source_ref"), f"EVIDENCE_REF_INVALID:{label}"),
        "source_sha256": _sha(
            value.get("source_sha256"), f"EVIDENCE_SHA_INVALID:{label}"
        ),
    }


def _record(row: dict, contract: dict) -> dict:
    fields = {
        "instrument_id", "market", "venue", "symbol", "currency",
        "instrument_type", "hedge_scope", "hedged_exposure_id", "eligible",
        "valid_from", "valid_to", "cost_evidence", "tracking_error_evidence",
        "decision_reasons", "decision_basis_ref", "decision_basis_sha256",
        "restrictions",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise HedgeEligibilityError("INSTRUMENT_FIELDS_MISMATCH")
    instrument_id = row.get("instrument_id")
    if not isinstance(instrument_id, str) or INSTRUMENT_ID_RE.fullmatch(instrument_id) is None:
        raise HedgeEligibilityError(f"INSTRUMENT_ID_INVALID:{instrument_id}")
    market = row.get("market")
    scope = row.get("hedge_scope")
    if market not in contract["allowed_markets"]:
        raise HedgeEligibilityError(f"MARKET_INVALID:{instrument_id}:{market}")
    if scope not in contract["allowed_hedge_scopes"]:
        raise HedgeEligibilityError(f"HEDGE_SCOPE_INVALID:{instrument_id}:{scope}")
    eligible = row.get("eligible")
    if not isinstance(eligible, bool):
        raise HedgeEligibilityError(f"ELIGIBILITY_INVALID:{instrument_id}")
    start, end = _interval(row.get("valid_from"), row.get("valid_to"), instrument_id)
    reasons = row.get("decision_reasons")
    restrictions = row.get("restrictions")
    if (
        not isinstance(reasons, list)
        or not reasons
        or reasons != sorted(set(reasons))
        or any(not isinstance(item, str) or CODE_RE.fullmatch(item) is None for item in reasons)
    ):
        raise HedgeEligibilityError(f"DECISION_REASONS_INVALID:{instrument_id}")
    if (
        not isinstance(restrictions, list)
        or restrictions != sorted(set(restrictions))
        or any(not isinstance(item, str) or CODE_RE.fullmatch(item) is None for item in restrictions)
    ):
        raise HedgeEligibilityError(f"RESTRICTIONS_INVALID:{instrument_id}")
    return {
        "instrument_id": instrument_id,
        "market": market,
        "venue": _code(row.get("venue"), f"VENUE_INVALID:{instrument_id}"),
        "symbol": _text(row.get("symbol"), f"SYMBOL_INVALID:{instrument_id}"),
        "currency": _code(row.get("currency"), f"CURRENCY_INVALID:{instrument_id}"),
        "instrument_type": _code(
            row.get("instrument_type"), f"INSTRUMENT_TYPE_INVALID:{instrument_id}"
        ),
        "hedge_scope": scope,
        "hedged_exposure_id": _text(
            row.get("hedged_exposure_id"), f"EXPOSURE_ID_INVALID:{instrument_id}"
        ),
        "eligible": eligible,
        "valid_from": start,
        "valid_to": end,
        "cost_evidence": _evidence(row.get("cost_evidence"), f"{instrument_id}:cost"),
        "tracking_error_evidence": _evidence(
            row.get("tracking_error_evidence"), f"{instrument_id}:tracking_error"
        ),
        "decision_reasons": list(reasons),
        "decision_basis_ref": _text(
            row.get("decision_basis_ref"), f"DECISION_BASIS_REF_INVALID:{instrument_id}"
        ),
        "decision_basis_sha256": _sha(
            row.get("decision_basis_sha256"),
            f"DECISION_BASIS_SHA_INVALID:{instrument_id}",
        ),
        "restrictions": list(restrictions),
    }


def _validate_registry(value: dict, as_of_date: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "registry_id", "status",
        "ratified_by", "ratified_at", "valid_from", "valid_to", "records",
        "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise HedgeEligibilityError("REGISTRY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["input_authority"]
    ):
        raise HedgeEligibilityError("REGISTRY_IDENTITY_INVALID")
    registry_id = _text(value.get("registry_id"), "REGISTRY_ID_INVALID")
    ratified_at = _utc(value.get("ratified_at"), "REGISTRY_RATIFIED_AT_INVALID")
    start, end = _interval(value.get("valid_from"), value.get("valid_to"), registry_id)
    if ratified_at[:10] > start:
        raise HedgeEligibilityError("REGISTRY_RATIFIED_AFTER_EFFECTIVE_START")
    if not _active(start, end, as_of_date):
        raise HedgeEligibilityError("REGISTRY_NOT_EFFECTIVE")
    raw = value.get("records")
    if not isinstance(raw, list) or not raw:
        raise HedgeEligibilityError("REGISTRY_RECORDS_EMPTY")
    records = sorted(
        (_record(row, contract) for row in raw),
        key=lambda row: (row["instrument_id"], row["valid_from"]),
    )
    groups: dict[str, list[dict]] = {}
    for row in records:
        if row["valid_from"] < start or (
            end is not None and (row["valid_to"] is None or row["valid_to"] > end)
        ):
            raise HedgeEligibilityError(
                f"INSTRUMENT_OUTSIDE_REGISTRY_INTERVAL:{row['instrument_id']}"
            )
        groups.setdefault(row["instrument_id"], []).append(row)
    active = []
    for instrument_id, rows in sorted(groups.items()):
        identity = {
            (
                row["market"], row["venue"], row["symbol"], row["currency"],
                row["instrument_type"], row["hedge_scope"], row["hedged_exposure_id"],
            )
            for row in rows
        }
        if len(identity) != 1:
            raise HedgeEligibilityError(f"INSTRUMENT_IDENTITY_DRIFT:{instrument_id}")
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                if _overlap(
                    left["valid_from"], left["valid_to"],
                    right["valid_from"], right["valid_to"],
                ):
                    raise HedgeEligibilityError(f"INSTRUMENT_INTERVAL_OVERLAP:{instrument_id}")
        current = [
            row for row in rows
            if _active(row["valid_from"], row["valid_to"], as_of_date)
        ]
        if len(current) > 1:
            raise HedgeEligibilityError(f"ACTIVE_INSTRUMENT_COUNT_INVALID:{instrument_id}")
        active.extend(copy.deepcopy(current))
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "registry_id": registry_id,
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": ratified_at,
        "valid_from": start,
        "valid_to": end,
        "records": records,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise HedgeEligibilityError("REGISTRY_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest, "active": active}


def _source_packet(validated: dict) -> dict:
    packet = copy.deepcopy(validated["normalized"])
    packet["packet_sha256"] = validated["packet_sha256"]
    return packet


def _assemble(checked: dict, as_of: str, contract: dict) -> dict:
    active = checked["active"]
    eligible = [row for row in active if row["eligible"]]
    ineligible = [row for row in active if not row["eligible"]]
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "ELIGIBILITY_REGISTRY_VALIDATED",
        "as_of_date": as_of,
        "registry_id": checked["normalized"]["registry_id"],
        "active_records": active,
        "eligible_instruments": [row["instrument_id"] for row in eligible],
        "summary": {
            "active_count": len(active),
            "eligible_count": len(eligible),
            "ineligible_count": len(ineligible),
            "by_scope": {
                scope: sum(row["hedge_scope"] == scope for row in active)
                for scope in contract["allowed_hedge_scopes"]
            },
        },
        "selected_instrument": None,
        "hedge_size": None,
        "order_intents": [],
        "source_packets": {"REGISTRY": _source_packet(checked)},
        "lineage": {
            "registry_packet_sha256": checked["packet_sha256"],
            "registry_content_sha256": payload_sha256(checked["normalized"]),
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "AUTOMATIC_INSTRUMENT_SELECTION_NOT_AUTHORIZED",
            "HEDGE_SIZING_NOT_AUTHORIZED",
            "ORDER_NOT_AUTHORIZED",
        ],
    }


def build_packet(
    registry: dict,
    as_of_date: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    as_of = _date(as_of_date, "AS_OF_DATE_INVALID")
    checked = _validate_registry(registry, as_of, contract)
    packet = _assemble(checked, as_of, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "as_of_date",
        "registry_id", "active_records", "eligible_instruments", "summary",
        "selected_instrument", "hedge_size", "order_intents", "source_packets",
        "lineage", "authority", "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise HedgeEligibilityError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
    ):
        raise HedgeEligibilityError("OUTPUT_IDENTITY_INVALID")
    as_of = _date(packet.get("as_of_date"), "OUTPUT_AS_OF_DATE_INVALID")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {"REGISTRY"}:
        raise HedgeEligibilityError("OUTPUT_SOURCE_PACKETS_INVALID")
    checked = _validate_registry(sources["REGISTRY"], as_of, contract)
    expected = _assemble(checked, as_of, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_PACKET_SHA_INVALID")
    if actual != expected:
        raise HedgeEligibilityError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise HedgeEligibilityError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise HedgeEligibilityError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(registry_path: Path, as_of_date: str, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(registry_path), as_of_date))
        return 0
    except (HedgeEligibilityError, OSError, TypeError, ValueError) as exc:
        print(f"Hedge eligibility failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.registry, args.as_of_date, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
