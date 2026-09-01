#!/usr/bin/env python3
"""Build an offline KRX Gate 1 calendar/completed-bar receipt.

This producer performs no network, credential, broker, writer, or ledger work.
It accepts only exact-hash retained inputs and reuses the existing KRX session
and completed-bar validator. Missing official calendar, natural minute data,
or normalization authority is recorded as UNKNOWN/HOLD without mutation.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "krx_gate1_natural_receipt_contract.json"
VALIDATOR_PATH = ROOT / "market_data" / "krx_session_bars.py"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class KrxGate1ReceiptError(ValueError):
    """Fail-closed input, lineage, or receipt violation."""


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise KrxGate1ReceiptError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_bytes(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise KrxGate1ReceiptError(code) from exc


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(_read_bytes(path, code).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KrxGate1ReceiptError(code) from exc
    if not isinstance(value, dict):
        raise KrxGate1ReceiptError(code)
    return value


def _file_sha256(path: Path, code: str) -> str:
    return hashlib.sha256(_read_bytes(path, code)).hexdigest()


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise KrxGate1ReceiptError(code)
    return value


def _git_sha(value: object, code: str) -> str:
    if not isinstance(value, str) or GIT_SHA_RE.fullmatch(value) is None:
        raise KrxGate1ReceiptError(code)
    return value


def _parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        raise KrxGate1ReceiptError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise KrxGate1ReceiptError(code) from exc
    if parsed.isoformat() != value:
        raise KrxGate1ReceiptError(code)
    return parsed


def _parse_instant(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise KrxGate1ReceiptError(code)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise KrxGate1ReceiptError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise KrxGate1ReceiptError(code)
    return parsed


def _safe(root: Path, relative: object, code: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise KrxGate1ReceiptError(code)
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        raise KrxGate1ReceiptError(code)
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise KrxGate1ReceiptError(code) from exc
    return resolved


def _load_validator(path: Path = VALIDATOR_PATH):
    spec = importlib.util.spec_from_file_location("krx_gate1_existing_validator", path)
    if spec is None or spec.loader is None:
        raise KrxGate1ReceiptError("COMPLETED_BAR_VALIDATOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_contract(path: Path = CONTRACT_PATH, root: Path = ROOT) -> dict:
    value = _read_json(path, "CONTRACT_INVALID")
    required = {
        "schema_version", "contract_version", "receipt_version", "market",
        "timezone", "run_mode", "approval_receipt", "exact_pins",
        "official_session_source", "normalized_minute_source",
        "required_completed_series", "evidence_classes", "fail_closed",
        "authority",
    }
    if set(value) != required:
        raise KrxGate1ReceiptError("CONTRACT_SCHEMA_INVALID")
    if (
        value["schema_version"] != 1
        or value["contract_version"] != "krx_gate1_natural_receipt/1"
        or value["receipt_version"] != "krx_gate1_natural_receipt/1"
        or value["market"] != "KRX"
        or value["timezone"] != "Asia/Seoul"
        or value["run_mode"] != "PAPER_READ_ONLY"
    ):
        raise KrxGate1ReceiptError("CONTRACT_IDENTITY_INVALID")
    approval = value["approval_receipt"]
    if approval != {
        "id": "CIO_GATE1_TTL_SLA_OPTION_B_20260901",
        "decision": "APPROVED_OPTION_B",
        "numeric_ttl_seconds": None,
        "repository_default": "ABSENT",
        "provider_sla": "UNKNOWN",
        "governance": "OBSERVATION_REQUIRED",
    }:
        raise KrxGate1ReceiptError("APPROVAL_RECEIPT_INVALID")
    authority = value["authority"]
    if authority.get("market_data_observation_only") is not True:
        raise KrxGate1ReceiptError("AUTHORITY_INVALID")
    if any(
        item is not False
        for key, item in authority.items()
        if key != "market_data_observation_only"
    ):
        raise KrxGate1ReceiptError("AUTHORITY_OPEN")
    pins = value["exact_pins"]
    _git_sha(pins.get("public_base_commit"), "PUBLIC_BASE_PIN_INVALID")
    _git_sha(
        pins.get("natural_judgement_producer_merge"),
        "NATURAL_PRODUCER_MERGE_PIN_INVALID",
    )
    for name in (
        "completed_bar_validator",
        "market_data_contract",
        "natural_judgement_producer",
        "natural_judgement_contract",
    ):
        binding = pins.get(name)
        if not isinstance(binding, dict) or not {"path", "sha256"} <= set(binding):
            raise KrxGate1ReceiptError(f"PIN_INVALID:{name}")
        pinned_path = _safe(root, binding["path"], f"PIN_PATH_INVALID:{name}")
        if _file_sha256(pinned_path, f"PIN_FILE_MISSING:{name}") != _sha(
            binding["sha256"], f"PIN_SHA_INVALID:{name}"
        ):
            raise KrxGate1ReceiptError(f"PIN_SHA_MISMATCH:{name}")
        if "contract_version" in binding:
            pinned = _read_json(pinned_path, f"PIN_JSON_INVALID:{name}")
            if pinned.get("contract_version") != binding["contract_version"]:
                raise KrxGate1ReceiptError(f"PIN_VERSION_MISMATCH:{name}")
    return copy.deepcopy(value)


def _binding(root: Path, value: object, label: str) -> tuple[Path, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise KrxGate1ReceiptError(f"{label}_BINDING_INVALID")
    path = _safe(root, value["path"], f"{label}_PATH_INVALID")
    expected = _sha(value["sha256"], f"{label}_SHA_INVALID")
    actual = _file_sha256(path, f"{label}_FILE_MISSING")
    if actual != expected:
        raise KrxGate1ReceiptError(f"{label}_SHA_MISMATCH")
    return path, actual


def _validate_manifest(value: dict, contract: dict) -> tuple[dt.date, dt.datetime]:
    required = {
        "schema_version", "as_of_date", "decision_at", "evidence_class",
        "calendar_binding", "normalized_minute_packet_binding",
        "normalization_receipt_binding", "source_inventory", "authority",
    }
    if set(value) != required or value["schema_version"] != "krx_gate1_natural_input/1":
        raise KrxGate1ReceiptError("INPUT_MANIFEST_INVALID")
    as_of = _parse_date(value["as_of_date"], "INPUT_DATE_INVALID")
    decision_at = _parse_instant(value["decision_at"], "DECISION_AT_INVALID")
    if decision_at.date() < as_of:
        raise KrxGate1ReceiptError("DECISION_BEFORE_SESSION_DATE")
    if value["evidence_class"] not in contract["evidence_classes"]:
        raise KrxGate1ReceiptError("EVIDENCE_CLASS_INVALID")
    if value["authority"] != contract["authority"]:
        raise KrxGate1ReceiptError("INPUT_AUTHORITY_INVALID")
    inventory = value["source_inventory"]
    if not isinstance(inventory, dict) or set(inventory) != {
        "official_date_specific_calendar", "natural_normalized_minutes",
        "minute_timestamp_normalization", "numeric_ttl_seconds",
        "repository_default", "provider_sla",
    }:
        raise KrxGate1ReceiptError("SOURCE_INVENTORY_INVALID")
    if (
        inventory["numeric_ttl_seconds"] is not None
        or inventory["repository_default"] != "ABSENT"
        or inventory["provider_sla"] != "UNKNOWN"
    ):
        raise KrxGate1ReceiptError("TTL_SLA_AUTHORITY_EXPANSION_REJECTED")
    return as_of, decision_at


def _calendar_source(
    path: Path,
    as_of: dt.date,
    decision_at: dt.datetime,
    validator: Any,
    contract: dict,
) -> tuple[dict, dict]:
    envelope = _read_json(path, "CALENDAR_SOURCE_INVALID")
    if set(envelope) != {
        "schema_version", "as_of_date", "official_response_ref",
        "official_response_sha256", "calendar",
    } or envelope["schema_version"] != "krx_date_specific_session_source/1":
        raise KrxGate1ReceiptError("CALENDAR_SOURCE_INVALID")
    if _parse_date(envelope["as_of_date"], "CALENDAR_DATE_INVALID") != as_of:
        raise KrxGate1ReceiptError("CALENDAR_DATE_MISMATCH")
    _sha(envelope["official_response_sha256"], "CALENDAR_RESPONSE_SHA_INVALID")
    if not isinstance(envelope["official_response_ref"], str) or not envelope[
        "official_response_ref"
    ]:
        raise KrxGate1ReceiptError("CALENDAR_RESPONSE_REF_INVALID")
    checked = validator.validate_calendar(
        envelope["calendar"], decision_at, validator.load_contract()
    )
    if checked["_date"] != as_of:
        raise KrxGate1ReceiptError("CALENDAR_SESSION_DATE_MISMATCH")
    if checked["source_ref"] != envelope["official_response_ref"]:
        raise KrxGate1ReceiptError("CALENDAR_RESPONSE_REF_MISMATCH")
    if checked["source_sha256"] != envelope["official_response_sha256"]:
        raise KrxGate1ReceiptError("CALENDAR_RESPONSE_SHA_MISMATCH")
    expected = contract["official_session_source"]
    if (
        checked["provider_id"] != expected["provider_id"]
        or checked["market_rule_source"] != expected["market_rule_source"]
    ):
        raise KrxGate1ReceiptError("CALENDAR_OFFICIAL_IDENTITY_MISMATCH")
    return envelope, checked


def _normalization_receipt(
    path: Path,
    as_of: dt.date,
    evidence_class: str,
) -> dict:
    value = _read_json(path, "NORMALIZATION_RECEIPT_INVALID")
    required = {
        "schema_version", "approval_status", "timestamp_semantics",
        "effective_from", "source_ref", "source_sha256",
        "fixture_promotion_authorized",
    }
    if set(value) != required or value["schema_version"] != (
        "krx_minute_timestamp_normalization_receipt/1"
    ):
        raise KrxGate1ReceiptError("NORMALIZATION_RECEIPT_INVALID")
    if value["timestamp_semantics"] != "INTERVAL_START_RATIFIED":
        raise KrxGate1ReceiptError("MINUTE_TIMESTAMP_NORMALIZATION_UNRATIFIED")
    if _parse_date(value["effective_from"], "NORMALIZATION_DATE_INVALID") > as_of:
        raise KrxGate1ReceiptError("NORMALIZATION_NOT_YET_EFFECTIVE")
    _sha(value["source_sha256"], "NORMALIZATION_SOURCE_SHA_INVALID")
    if not isinstance(value["source_ref"], str) or not value["source_ref"]:
        raise KrxGate1ReceiptError("NORMALIZATION_SOURCE_REF_INVALID")
    expected = (
        "RATIFIED"
        if evidence_class == "NATURAL_READ_ONLY"
        else "TEST_RATIFIED_NON_PROMOTABLE"
    )
    if value["approval_status"] != expected:
        raise KrxGate1ReceiptError("NORMALIZATION_APPROVAL_INVALID")
    if value["fixture_promotion_authorized"] is not False:
        raise KrxGate1ReceiptError("FIXTURE_PROMOTION_AUTHORITY_OPEN")
    return value


def _series_receipt(
    packet: dict,
    calendar: dict,
    decision_at: dt.datetime,
    validator: Any,
) -> dict:
    series_result: dict[str, dict] = {}
    validator_contract = validator.load_contract()
    for timeframe, expected_count in (("15m", 26), ("1h", 6)):
        timeframe_packet = copy.deepcopy(packet)
        if timeframe == "1h":
            allowed_starts = {
                opened + dt.timedelta(minutes=index)
                for opened, closed in validator.expected_intervals(
                    timeframe, calendar, decision_at, validator_contract
                )
                for index in range(int((closed - opened).total_seconds() // 60))
            }
            timeframe_packet["minutes"] = [
                row
                for row in timeframe_packet["minutes"]
                if _parse_instant(
                    row.get("interval_start"), "MINUTE_INTERVAL_START_INVALID"
                ) in allowed_starts
            ]
        series = validator.aggregate_normalized_minutes(
            timeframe_packet, timeframe, calendar, decision_at, validator_contract
        )
        intervals = validator.expected_intervals(
            timeframe, calendar, decision_at, validator_contract
        )
        accepted = []
        for row in series["bars"]:
            checked = validator._bar(row, timeframe, "RAW", decision_at)
            accepted.append((checked["_open"], checked["_close"]))
        if accepted != intervals or len(accepted) != expected_count:
            raise KrxGate1ReceiptError(f"{timeframe.upper()}_COMPLETED_SERIES_GAP")
        series_result[timeframe] = {
            "status": "PASS",
            "expected_interval_count": expected_count,
            "accepted_bar_count": len(accepted),
            "first_open_at": series["bars"][0]["open_at"],
            "last_close_at": series["bars"][-1]["close_at"],
            "series_sha256": payload_sha256(series),
        }
    series_result["1d"] = {
        "status": "UNCONFIRMED_NOT_PROMOTED",
        "accepted_bar_count": 0,
    }
    return series_result


def build_receipt(
    manifest_path: Path,
    *,
    root: Path = ROOT,
    contract: dict | None = None,
) -> dict:
    contract = copy.deepcopy(contract or load_contract(root=root))
    manifest = _read_json(manifest_path, "INPUT_MANIFEST_INVALID")
    as_of, decision_at = _validate_manifest(manifest, contract)
    evidence_class = manifest["evidence_class"]
    validator = _load_validator(_safe(
        root,
        contract["exact_pins"]["completed_bar_validator"]["path"],
        "VALIDATOR_PATH_INVALID",
    ))

    calendar_binding = _binding(root, manifest["calendar_binding"], "CALENDAR")
    minute_binding = _binding(
        root, manifest["normalized_minute_packet_binding"], "MINUTE_PACKET"
    )
    normalization_binding = _binding(
        root, manifest["normalization_receipt_binding"], "NORMALIZATION_RECEIPT"
    )

    blockers: list[str] = []
    calendar_receipt = {
        "status": "UNKNOWN",
        "session_date": as_of.isoformat(),
        "session_status": "UNKNOWN",
        "source_path": None,
        "source_file_sha256": None,
        "official_response_ref": None,
        "official_response_sha256": None,
    }
    completed_series = {
        "15m": {"status": "UNKNOWN", "expected_interval_count": 26, "accepted_bar_count": 0, "series_sha256": None},
        "1h": {"status": "UNKNOWN", "expected_interval_count": 6, "accepted_bar_count": 0, "series_sha256": None},
        "1d": {"status": "UNCONFIRMED_NOT_PROMOTED", "accepted_bar_count": 0},
    }

    if calendar_binding is None:
        blockers.append("OFFICIAL_DATE_SPECIFIC_CALENDAR_RECEIPT_ABSENT")
    if minute_binding is None:
        blockers.append("NATURAL_NORMALIZED_MINUTE_PACKET_ABSENT")
    if normalization_binding is None:
        blockers.append("KIS_MINUTE_TIMESTAMP_NORMALIZATION_UNRATIFIED")

    source_complete = False
    if calendar_binding and minute_binding and normalization_binding:
        calendar_path, calendar_file_sha = calendar_binding
        minute_path, minute_file_sha = minute_binding
        normalization_path, normalization_file_sha = normalization_binding
        calendar_envelope, checked_calendar = _calendar_source(
            calendar_path, as_of, decision_at, validator, contract
        )
        if checked_calendar["status"] != "OPEN_REGULAR":
            blockers.append("SESSION_NOT_OPEN_REGULAR")
        else:
            normalization = _normalization_receipt(
                normalization_path, as_of, evidence_class
            )
            minute_packet = _read_json(minute_path, "MINUTE_PACKET_INVALID")
            if minute_packet.get("timestamp_semantics") != normalization[
                "timestamp_semantics"
            ]:
                raise KrxGate1ReceiptError("MINUTE_NORMALIZATION_BINDING_MISMATCH")
            completed_series = _series_receipt(
                minute_packet,
                calendar_envelope["calendar"],
                decision_at,
                validator,
            )
            source_complete = True
            calendar_receipt = {
                "status": "PASS",
                "session_date": as_of.isoformat(),
                "session_status": checked_calendar["status"],
                "source_path": manifest["calendar_binding"]["path"],
                "source_file_sha256": calendar_file_sha,
                "official_response_ref": calendar_envelope["official_response_ref"],
                "official_response_sha256": calendar_envelope[
                    "official_response_sha256"
                ],
            }
            completed_series["source"] = {
                "asset_id": minute_packet.get("asset_id"),
                "minute_packet_path": manifest[
                    "normalized_minute_packet_binding"
                ]["path"],
                "minute_packet_sha256": minute_file_sha,
                "normalization_receipt_path": manifest[
                    "normalization_receipt_binding"
                ]["path"],
                "normalization_receipt_sha256": normalization_file_sha,
            }

    blockers.extend([
        "NUMERIC_TTL_ABSENT_OPTION_B",
        "PROVIDER_SLA_UNKNOWN",
        "OBSERVATION_REQUIRED",
    ])
    if evidence_class == "TEST_ONLY_NON_PROMOTABLE":
        blockers.append("TEST_EVIDENCE_NON_PROMOTABLE")

    gate1_status = "UNKNOWN"
    source_status = "INCOMPLETE"
    if source_complete and evidence_class == "NATURAL_READ_ONLY":
        gate1_status = "PASS"
        source_status = "COMPLETE"
    elif source_complete:
        gate1_status = "TEST_ONLY"
        source_status = "TEST_ONLY"

    receipt = {
        "schema_version": contract["receipt_version"],
        "contract_version": contract["contract_version"],
        "as_of_date": as_of.isoformat(),
        "decision_at": manifest["decision_at"],
        "evidence_class": evidence_class,
        "source_status": source_status,
        "calendar": calendar_receipt,
        "completed_series": completed_series,
        "ttl_sla_governance": copy.deepcopy(contract["approval_receipt"]),
        "gate1_status": gate1_status,
        "runtime": {
            "status": "UNKNOWN",
            "action": "HOLD",
            "writer_invocation_count": 0,
            "ledger_mutation_count": 0,
            "order_count": 0,
            "cancel_count": 0,
        },
        "blockers": sorted(set(blockers)),
        "lineage": {
            "input_manifest_path": manifest_path.resolve().relative_to(
                root.resolve()
            ).as_posix(),
            "input_manifest_sha256": _file_sha256(
                manifest_path, "INPUT_MANIFEST_MISSING"
            ),
            "public_base_commit": contract["exact_pins"]["public_base_commit"],
            "completed_bar_validator": copy.deepcopy(
                contract["exact_pins"]["completed_bar_validator"]
            ),
            "market_data_contract": copy.deepcopy(
                contract["exact_pins"]["market_data_contract"]
            ),
            "natural_judgement_producer_merge": contract["exact_pins"][
                "natural_judgement_producer_merge"
            ],
            "natural_judgement_producer": copy.deepcopy(
                contract["exact_pins"]["natural_judgement_producer"]
            ),
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    receipt["payload_sha256"] = payload_sha256(receipt)
    validate_receipt(receipt, contract)
    return receipt


def validate_receipt(receipt: object, contract: dict | None = None) -> dict:
    contract = copy.deepcopy(contract or load_contract())
    required = {
        "schema_version", "contract_version", "as_of_date", "decision_at",
        "evidence_class", "source_status", "calendar", "completed_series",
        "ttl_sla_governance", "gate1_status", "runtime", "blockers",
        "lineage", "authority", "payload_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise KrxGate1ReceiptError("RECEIPT_SCHEMA_INVALID")
    expected_sha = payload_sha256({
        key: copy.deepcopy(value)
        for key, value in receipt.items()
        if key != "payload_sha256"
    })
    if receipt["payload_sha256"] != expected_sha:
        raise KrxGate1ReceiptError("RECEIPT_SHA_MISMATCH")
    if (
        receipt["schema_version"] != contract["receipt_version"]
        or receipt["contract_version"] != contract["contract_version"]
        or receipt["ttl_sla_governance"] != contract["approval_receipt"]
        or receipt["authority"] != contract["authority"]
    ):
        raise KrxGate1ReceiptError("RECEIPT_CONTRACT_MISMATCH")
    runtime = receipt["runtime"]
    if runtime != {
        "status": "UNKNOWN",
        "action": "HOLD",
        "writer_invocation_count": 0,
        "ledger_mutation_count": 0,
        "order_count": 0,
        "cancel_count": 0,
    }:
        raise KrxGate1ReceiptError("RUNTIME_AUTHORITY_EXPANSION_REJECTED")
    if receipt["completed_series"].get("1d") != {
        "status": "UNCONFIRMED_NOT_PROMOTED",
        "accepted_bar_count": 0,
    }:
        raise KrxGate1ReceiptError("DAILY_BAR_PROMOTION_REJECTED")
    if receipt["evidence_class"] == "TEST_ONLY_NON_PROMOTABLE" and receipt[
        "gate1_status"
    ] == "PASS":
        raise KrxGate1ReceiptError("TEST_EVIDENCE_PROMOTION_REJECTED")
    if receipt["gate1_status"] == "PASS":
        if (
            receipt["evidence_class"] != "NATURAL_READ_ONLY"
            or receipt["source_status"] != "COMPLETE"
            or receipt["calendar"].get("status") != "PASS"
            or receipt["calendar"].get("session_status") != "OPEN_REGULAR"
            or receipt["completed_series"].get("15m", {}).get("status") != "PASS"
            or receipt["completed_series"].get("15m", {}).get(
                "accepted_bar_count"
            ) != 26
            or receipt["completed_series"].get("1h", {}).get("status") != "PASS"
            or receipt["completed_series"].get("1h", {}).get(
                "accepted_bar_count"
            ) != 6
        ):
            raise KrxGate1ReceiptError("GATE1_PASS_WITHOUT_COMPLETE_NATURAL_SOURCE")
    if receipt["gate1_status"] == "UNKNOWN" and receipt["source_status"] != (
        "INCOMPLETE"
    ):
        raise KrxGate1ReceiptError("UNKNOWN_SOURCE_STATUS_INVALID")
    _parse_date(receipt["as_of_date"], "RECEIPT_DATE_INVALID")
    _parse_instant(receipt["decision_at"], "RECEIPT_DECISION_AT_INVALID")
    if not isinstance(receipt["blockers"], list) or receipt["blockers"] != sorted(
        set(receipt["blockers"])
    ):
        raise KrxGate1ReceiptError("RECEIPT_BLOCKERS_INVALID")
    return copy.deepcopy(receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--check-receipt", type=Path)
    args = parser.parse_args()
    receipt = build_receipt(args.manifest)
    if args.check_receipt is not None:
        expected = _read_json(args.check_receipt, "CHECK_RECEIPT_INVALID")
        validate_receipt(expected)
        if canonical_json(receipt) != canonical_json(expected):
            raise KrxGate1ReceiptError("CHECK_RECEIPT_MISMATCH")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
