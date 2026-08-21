#!/usr/bin/env python3
"""P8-03 READY != ENTRY and Signal != Order boundary validator."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "ready_signal_order_boundary_contract.json"
INPUT_SCHEMA_VERSION = "ready_signal_observation_packet/1"
OUTPUT_SCHEMA_VERSION = "ready_signal_order_boundary_packet/1"
SUBJECT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ReadySignalOrderBoundaryError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadySignalOrderBoundaryError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "ready_signal_order_boundary/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "ready_statuses": ["READY", "NOT_READY", "NOT_EVALUATED"],
        "signal_statuses": ["PRESENT", "ABSENT", "NOT_EVALUATED"],
        "entry_trigger_status": "NOT_EVALUATED",
        "order_status": "NOT_EVALUATED",
        "invariants": [
            "READY_NEVER_IMPLIES_ENTRY_TRIGGER",
            "SIGNAL_NEVER_IMPLIES_ORDER",
        ],
        "decision_contract_status": "P8_02_CONTRACT_AVAILABLE_NO_ACTION_AUTHORITY",
        "input_authority": {
            "ready_observation_only": True,
            "signal_observation_only": True,
            "entry_trigger_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "boundary_enforcement_only": True,
            "ready_to_entry_translation_authorized": False,
            "signal_to_order_translation_authorized": False,
            "entry_trigger_authorized": False,
            "order_authorized": False,
            "position_sizing_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ReadySignalOrderBoundaryError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ReadySignalOrderBoundaryError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ReadySignalOrderBoundaryError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ReadySignalOrderBoundaryError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ReadySignalOrderBoundaryError(code)
    return value


def _sha(value, code: str, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ReadySignalOrderBoundaryError(code)
    return value


def _text(value, code: str, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ReadySignalOrderBoundaryError(code)
    return value


def classify_ready(ready_status: str, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if ready_status not in contract["ready_statuses"]:
        raise ReadySignalOrderBoundaryError(f"READY_STATUS_INVALID:{ready_status}")
    reasons = ["SEPARATE_ENTRY_TRIGGER_EVALUATION_REQUIRED"]
    if ready_status == "READY":
        reasons.insert(0, "READY_IS_CANDIDATE_QUALIFICATION_NOT_ENTRY")
    else:
        reasons.insert(0, "READY_STATUS_HAS_NO_ENTRY_AUTHORITY")
    return {
        "ready_status": ready_status,
        "entry_trigger": None,
        "entry_trigger_status": contract["entry_trigger_status"],
        "entry_boundary_status": "ENFORCED",
        "entry_reasons": reasons,
    }


def classify_signal(signal_status: str, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if signal_status not in contract["signal_statuses"]:
        raise ReadySignalOrderBoundaryError(f"SIGNAL_STATUS_INVALID:{signal_status}")
    reasons = [
        "SEPARATE_ORDER_ELIGIBILITY_EVALUATION_REQUIRED",
        "POSITION_SIZING_NOT_AUTHORIZED",
        "PORTFOLIO_RISK_CHECK_NOT_AUTHORIZED",
    ]
    if signal_status == "PRESENT":
        reasons.insert(0, "SIGNAL_IS_OBSERVATION_NOT_ORDER")
    else:
        reasons.insert(0, "SIGNAL_STATUS_HAS_NO_ORDER_AUTHORITY")
    return {
        "signal_status": signal_status,
        "order_intent": None,
        "order_status": contract["order_status"],
        "order_boundary_status": "ENFORCED",
        "order_reasons": reasons,
    }


def assert_entry_not_derived(ready_status: str, proposed_entry_trigger) -> None:
    classify_ready(ready_status)
    if proposed_entry_trigger is not None:
        raise ReadySignalOrderBoundaryError(
            f"DERIVED_ENTRY_TRIGGER_FORBIDDEN:{ready_status}"
        )


def assert_order_not_derived(signal_status: str, proposed_order) -> None:
    classify_signal(signal_status)
    if proposed_order is not None:
        raise ReadySignalOrderBoundaryError(
            f"DERIVED_ORDER_FORBIDDEN:{signal_status}"
        )


def _validate_subject(row: dict, contract: dict) -> dict:
    fields = {
        "subject_id", "market", "ready_status", "ready_source_ref",
        "ready_source_sha256", "signal_status", "signal_id",
        "signal_source_ref", "signal_source_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ReadySignalOrderBoundaryError("SUBJECT_FIELDS_MISMATCH")
    subject_id = row.get("subject_id")
    if not isinstance(subject_id, str) or SUBJECT_ID_RE.fullmatch(subject_id) is None:
        raise ReadySignalOrderBoundaryError(f"SUBJECT_ID_INVALID:{subject_id}")
    market = row.get("market")
    if market not in contract["allowed_markets"]:
        raise ReadySignalOrderBoundaryError(f"MARKET_INVALID:{subject_id}:{market}")
    ready_status = row.get("ready_status")
    if ready_status not in contract["ready_statuses"]:
        raise ReadySignalOrderBoundaryError(f"READY_STATUS_INVALID:{subject_id}")
    ready_ref = _text(
        row.get("ready_source_ref"),
        f"READY_SOURCE_REF_INVALID:{subject_id}",
        nullable=True,
    )
    ready_sha = _sha(
        row.get("ready_source_sha256"),
        f"READY_SOURCE_SHA_INVALID:{subject_id}",
        nullable=True,
    )
    if ready_status == "NOT_EVALUATED":
        if ready_ref is not None or ready_sha is not None:
            raise ReadySignalOrderBoundaryError(
                f"READY_NOT_EVALUATED_HAS_LINEAGE:{subject_id}"
            )
    elif ready_ref is None or ready_sha is None:
        raise ReadySignalOrderBoundaryError(f"READY_LINEAGE_INCOMPLETE:{subject_id}")

    signal_status = row.get("signal_status")
    if signal_status not in contract["signal_statuses"]:
        raise ReadySignalOrderBoundaryError(f"SIGNAL_STATUS_INVALID:{subject_id}")
    signal_id = _text(
        row.get("signal_id"), f"SIGNAL_ID_INVALID:{subject_id}", nullable=True
    )
    signal_ref = _text(
        row.get("signal_source_ref"),
        f"SIGNAL_SOURCE_REF_INVALID:{subject_id}",
        nullable=True,
    )
    signal_sha = _sha(
        row.get("signal_source_sha256"),
        f"SIGNAL_SOURCE_SHA_INVALID:{subject_id}",
        nullable=True,
    )
    if signal_status == "PRESENT":
        if signal_id is None or TOKEN_RE.fullmatch(signal_id) is None:
            raise ReadySignalOrderBoundaryError(f"SIGNAL_ID_INVALID:{subject_id}")
        if signal_ref is None or signal_sha is None:
            raise ReadySignalOrderBoundaryError(f"SIGNAL_LINEAGE_INCOMPLETE:{subject_id}")
    elif signal_status == "ABSENT":
        if signal_id is not None or signal_ref is None or signal_sha is None:
            raise ReadySignalOrderBoundaryError(f"SIGNAL_ABSENCE_LINEAGE_INVALID:{subject_id}")
    elif any(item is not None for item in (signal_id, signal_ref, signal_sha)):
        raise ReadySignalOrderBoundaryError(
            f"SIGNAL_NOT_EVALUATED_HAS_LINEAGE:{subject_id}"
        )
    return {
        "subject_id": subject_id,
        "market": market,
        "ready_status": ready_status,
        "ready_source_ref": ready_ref,
        "ready_source_sha256": ready_sha,
        "signal_status": signal_status,
        "signal_id": signal_id,
        "signal_source_ref": signal_ref,
        "signal_source_sha256": signal_sha,
    }


def _validate_input(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "packet_id", "as_of_utc",
        "subjects", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ReadySignalOrderBoundaryError("INPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise ReadySignalOrderBoundaryError("INPUT_IDENTITY_INVALID")
    packet_id = _text(value.get("packet_id"), "PACKET_ID_INVALID")
    as_of_utc = _utc(value.get("as_of_utc"), "AS_OF_UTC_INVALID")
    raw_rows = value.get("subjects")
    if not isinstance(raw_rows, list):
        raise ReadySignalOrderBoundaryError("SUBJECTS_NOT_LIST")
    rows = sorted(
        (_validate_subject(row, contract) for row in raw_rows),
        key=lambda row: row["subject_id"],
    )
    ids = [row["subject_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ReadySignalOrderBoundaryError("SUBJECT_ID_DUPLICATE")
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "packet_id": packet_id,
        "as_of_utc": as_of_utc,
        "subjects": rows,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "INPUT_PACKET_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise ReadySignalOrderBoundaryError("INPUT_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _assemble(checked: dict, contract: dict) -> dict:
    source = checked["normalized"]
    rows = []
    ready_count = 0
    signal_count = 0
    for item in source["subjects"]:
        ready = classify_ready(item["ready_status"], contract)
        signal = classify_signal(item["signal_status"], contract)
        ready_count += item["ready_status"] == "READY"
        signal_count += item["signal_status"] == "PRESENT"
        rows.append({**copy.deepcopy(item), **ready, **signal})
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "ACTION_BOUNDARY_ENFORCED_NO_ACTION_AUTHORITY",
        "packet_id": source["packet_id"],
        "as_of_utc": source["as_of_utc"],
        "summary": {
            "subject_count": len(rows),
            "ready_count": ready_count,
            "signal_present_count": signal_count,
            "entry_trigger_count": 0,
            "order_intent_count": 0,
        },
        "subjects": rows,
        "lineage": {"input_packet_sha256": checked["packet_sha256"]},
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "P8_02_AVAILABLE_ACTION_AUTHORITY_CLOSED",
            "ENTRY_TRIGGER_POLICY_UNRATIFIED",
            "ORDER_ELIGIBILITY_POLICY_UNRATIFIED",
            "POSITION_SIZING_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    return packet


def build_packet(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    checked = _validate_input(value, contract)
    packet = _assemble(checked, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "packet_id", "as_of_utc",
        "summary", "subjects", "lineage", "authority", "unresolved_boundaries",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ReadySignalOrderBoundaryError("OUTPUT_FIELDS_MISMATCH")
    lineage = packet.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {"input_packet_sha256"}:
        raise ReadySignalOrderBoundaryError("OUTPUT_LINEAGE_INVALID")
    subjects = packet.get("subjects")
    input_fields = {
        "subject_id", "market", "ready_status", "ready_source_ref",
        "ready_source_sha256", "signal_status", "signal_id",
        "signal_source_ref", "signal_source_sha256",
    }
    if not isinstance(subjects, list):
        raise ReadySignalOrderBoundaryError("OUTPUT_SUBJECTS_INVALID")
    raw_subjects = []
    for row in subjects:
        if not isinstance(row, dict) or not input_fields.issubset(row):
            raise ReadySignalOrderBoundaryError("OUTPUT_SUBJECT_FIELDS_INVALID")
        raw_subjects.append({key: copy.deepcopy(row[key]) for key in input_fields})
    source = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "packet_id": packet.get("packet_id"),
        "as_of_utc": packet.get("as_of_utc"),
        "subjects": raw_subjects,
        "authority": copy.deepcopy(contract["input_authority"]),
        "packet_sha256": lineage["input_packet_sha256"],
    }
    checked = _validate_input(source, contract)
    expected = _assemble(checked, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_PACKET_SHA_INVALID")
    if actual != expected:
        raise ReadySignalOrderBoundaryError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise ReadySignalOrderBoundaryError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ReadySignalOrderBoundaryError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(input_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(input_path)))
        return 0
    except (ReadySignalOrderBoundaryError, OSError, TypeError, ValueError) as exc:
        print(f"Ready/Signal action boundary failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enforce READY != ENTRY and Signal != Order"
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
