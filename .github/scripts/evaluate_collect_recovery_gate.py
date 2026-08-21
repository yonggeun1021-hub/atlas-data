#!/usr/bin/env python3
"""Evaluate and optionally record the P0-02 06:57 Recovery Action Gate.

This helper composes the existing current-file briefing readiness evaluator. It
never dispatches a workflow, reruns a collector, changes a schedule, or sends an
alert.  The output states which notification or human-approved recovery path is
required; delivery and execution remain external.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
CONTRACT_PATH = ROOT / "config" / "collect_recovery_gate_contract.json"
READINESS_PATH = ROOT / ".github" / "scripts" / "check_briefing_readiness.py"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KST_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RecoveryGateError(ValueError):
    """Fail-closed recovery gate contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryGateError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "collect_recovery_gate/1",
        "packet_schema_version": "collect_recovery_gate_packet/1",
        "timezone": "Asia/Seoul",
        "gate_open_time_kst": "06:57:00",
        "candidate_window_end_kst": "06:58:30",
        "warning_window_end_kst": "07:00:00",
        "timing_statuses": ["NOT_OPEN", "WITHIN_CANDIDATE_WINDOW", "LATE_WARNING_REVIEW_REQUIRED", "ROLE_UNSUITABLE"],
        "classifications": ["RECOVERY_WINDOW_OPEN", "DATA_READY", "DATA_READY_BRIEFING_READ_MODEL_DEGRADED", "DATA_NOT_READY", "UNKNOWN_MANUAL_INSPECTION_REQUIRED"],
        "readiness_contract": "check_briefing_readiness.py/evaluate",
        "source_precedence": "CURRENT_RAW_COLLECTOR_PREDICATES_THEN_READ_MODEL",
        "automatic_dispatch_policy": "PROHIBITED",
        "authority": {
            "source_read_only_classification_only": True,
            "before_0657_recovery_authorized": False,
            "automatic_workflow_dispatch_authorized": False,
            "collector_rerun_for_read_model_degradation_authorized": False,
            "notification_delivery_authorized": False,
            "schedule_change_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RecoveryGateError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RecoveryGateError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _load_readiness_module():
    spec = importlib.util.spec_from_file_location("atlas_briefing_readiness", READINESS_PATH)
    if spec is None or spec.loader is None:
        raise RecoveryGateError("READINESS_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise RecoveryGateError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise RecoveryGateError(code) from exc
    if parsed.isoformat() != value:
        raise RecoveryGateError(code)
    return parsed


def _kst(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or KST_RE.fullmatch(value) is None:
        raise RecoveryGateError(code)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise RecoveryGateError(code) from exc
    if parsed.utcoffset() != dt.timedelta(hours=9) or parsed.isoformat(timespec="seconds") != value:
        raise RecoveryGateError(code)
    return parsed


def _at(date: dt.date, time_value: str) -> dt.datetime:
    return dt.datetime.combine(
        date,
        dt.time.fromisoformat(time_value),
        tzinfo=dt.timezone(dt.timedelta(hours=9)),
    )


def _timing(expected_date: dt.date, evaluated: dt.datetime, contract: dict) -> dict:
    gate = _at(expected_date, contract["gate_open_time_kst"])
    candidate_end = _at(expected_date, contract["candidate_window_end_kst"])
    warning_end = _at(expected_date, contract["warning_window_end_kst"])
    delay = int((evaluated - gate).total_seconds())
    if evaluated < gate:
        status = "NOT_OPEN"
        assessment = "RECOVERY_WINDOW_IN_PROGRESS_FINAL_FAIL_FORBIDDEN"
    elif evaluated <= candidate_end:
        status = "WITHIN_CANDIDATE_WINDOW"
        assessment = "GATE_ROLE_MAINTAIN_CANDIDATE"
    elif evaluated <= warning_end:
        status = "LATE_WARNING_REVIEW_REQUIRED"
        assessment = "GATE_TIMING_WARNING_REVIEW_REQUIRED"
    else:
        status = "ROLE_UNSUITABLE"
        assessment = "GATE_ROLE_UNSUITABLE"
    return {
        "scheduled_at_kst": gate.isoformat(timespec="seconds"),
        "delay_seconds": delay,
        "timing_status": status,
        "role_assessment": assessment,
    }


def _validate_readiness(value: dict, expected_date: str) -> dict:
    fields = {
        "schema_version", "expected_kst_date", "classification", "data_ready",
        "read_model_ready", "manual_inspection_required", "recovery_action",
        "sources", "read_model", "reasons",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RecoveryGateError("READINESS_FIELDS_MISMATCH")
    classification = value.get("classification")
    expected = {
        "data_ready_read_model_ready": (True, True, False, "none"),
        "data_ready_read_model_degraded": (True, False, False, "repair_read_model_only"),
        "data_not_ready": (False, False, False, "workflow_dispatch"),
        "unknown_manual_inspection_required": (False, False, True, "manual_inspection"),
    }
    if (
        value.get("schema_version") != 1
        or value.get("expected_kst_date") != expected_date
        or classification not in expected
        or tuple(value.get(key) for key in ("data_ready", "read_model_ready", "manual_inspection_required", "recovery_action")) != expected[classification]
        or not isinstance(value.get("sources"), dict)
        or not isinstance(value.get("read_model"), dict)
        or not isinstance(value.get("reasons"), list)
        or value["reasons"] != sorted(set(value["reasons"]))
        or any(not isinstance(reason, str) or not reason for reason in value["reasons"])
    ):
        raise RecoveryGateError("READINESS_CONTENT_INVALID")
    return copy.deepcopy(value)


def _decision(readiness: dict | None) -> dict:
    if readiness is None:
        return {
            "evaluation_status": "DEFERRED_UNTIL_0657",
            "classification": "RECOVERY_WINDOW_OPEN",
            "data_ready": None,
            "read_model_ready": None,
            "read_model_repair_candidate": False,
            "alert": {"required": False, "kind": "NONE"},
            "manual_recovery": {"required": False, "guidance_allowed": False, "cio_approval_required": True},
            "recovery_action": "none_before_0657",
            "reasons": ["RECOVERY_WINDOW_IN_PROGRESS"],
        }
    mapping = {
        "data_ready_read_model_ready": {
            "classification": "DATA_READY", "data_ready": True, "read_model_ready": True,
            "read_model_repair_candidate": False, "alert": {"required": False, "kind": "NONE"},
            "manual_recovery": {"required": False, "guidance_allowed": False, "cio_approval_required": True},
            "recovery_action": "none",
        },
        "data_ready_read_model_degraded": {
            "classification": "DATA_READY_BRIEFING_READ_MODEL_DEGRADED", "data_ready": True, "read_model_ready": False,
            "read_model_repair_candidate": True, "alert": {"required": True, "kind": "BRIEFING_READ_MODEL_DEGRADED"},
            "manual_recovery": {"required": False, "guidance_allowed": False, "cio_approval_required": True},
            "recovery_action": "repair_read_model_only_collector_rerun_prohibited",
        },
        "data_not_ready": {
            "classification": "DATA_NOT_READY", "data_ready": False, "read_model_ready": False,
            "read_model_repair_candidate": False, "alert": {"required": True, "kind": "DATA_NOT_READY"},
            "manual_recovery": {"required": True, "guidance_allowed": True, "cio_approval_required": True},
            "recovery_action": "manual_recovery_requires_cio_approval",
        },
        "unknown_manual_inspection_required": {
            "classification": "UNKNOWN_MANUAL_INSPECTION_REQUIRED", "data_ready": None, "read_model_ready": None,
            "read_model_repair_candidate": False, "alert": {"required": True, "kind": "MANUAL_INSPECTION_REQUIRED"},
            "manual_recovery": {"required": False, "guidance_allowed": False, "cio_approval_required": True},
            "recovery_action": "manual_inspection",
        },
    }
    result = copy.deepcopy(mapping[readiness["classification"]])
    result["evaluation_status"] = "EVALUATED_CURRENT_FILES"
    result["reasons"] = list(readiness["reasons"])
    return result


def _compose(expected_date: str, evaluated_at_kst: str, readiness: dict | None, contract: dict) -> dict:
    date = _date(expected_date, "EXPECTED_KST_DATE_INVALID")
    evaluated = _kst(evaluated_at_kst, "EVALUATED_AT_KST_INVALID")
    if evaluated.date() != date:
        raise RecoveryGateError("EVALUATED_DATE_MISMATCH")
    timing = _timing(date, evaluated, contract)
    if timing["timing_status"] == "NOT_OPEN":
        if readiness is not None:
            raise RecoveryGateError("READINESS_BEFORE_GATE_FORBIDDEN")
        checked_readiness = None
    else:
        checked_readiness = _validate_readiness(readiness, expected_date)
    decision = _decision(checked_readiness)
    return {
        "schema_version": contract["packet_schema_version"],
        "contract_version": contract["contract_version"],
        "expected_kst_date": expected_date,
        "evaluated_at_kst": evaluated_at_kst,
        "gate_timing": timing,
        "evaluation_status": decision["evaluation_status"],
        "classification": decision["classification"],
        "data_ready": decision["data_ready"],
        "read_model_ready": decision["read_model_ready"],
        "read_model_repair_candidate": decision["read_model_repair_candidate"],
        "alert": decision["alert"],
        "manual_recovery": decision["manual_recovery"],
        "recovery_action": decision["recovery_action"],
        "readiness": checked_readiness,
        "automatic_workflow_dispatch_authorized": False,
        "workflow_dispatch_executed": False,
        "reasons": decision["reasons"],
        "authority": copy.deepcopy(contract["authority"]),
    }


def build_packet(expected_date: str, evaluated_at_kst: str, data_root: Path = DATA, contract: dict | None = None, readiness_module=None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    date = _date(expected_date, "EXPECTED_KST_DATE_INVALID")
    evaluated = _kst(evaluated_at_kst, "EVALUATED_AT_KST_INVALID")
    if evaluated.date() != date:
        raise RecoveryGateError("EVALUATED_DATE_MISMATCH")
    gate = _at(date, contract["gate_open_time_kst"])
    readiness = None
    if evaluated >= gate:
        module = readiness_module if readiness_module is not None else _load_readiness_module()
        readiness = module.evaluate(expected_date, Path(data_root))
    packet = _compose(expected_date, evaluated_at_kst, readiness, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "expected_kst_date", "evaluated_at_kst",
        "gate_timing", "evaluation_status", "classification", "data_ready",
        "read_model_ready", "read_model_repair_candidate", "alert", "manual_recovery",
        "recovery_action", "readiness", "automatic_workflow_dispatch_authorized",
        "workflow_dispatch_executed", "reasons", "authority", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise RecoveryGateError("PACKET_FIELDS_MISMATCH")
    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise RecoveryGateError("PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise RecoveryGateError("PACKET_SHA_MISMATCH")
    expected = _compose(packet.get("expected_kst_date"), packet.get("evaluated_at_kst"), packet.get("readiness"), contract)
    if unsigned != expected:
        raise RecoveryGateError("PACKET_CONTENT_MISMATCH")
    return copy.deepcopy(packet)


def record_packet(data_root: Path, packet: dict) -> Path:
    packet = validate_packet(packet)
    stamp = packet["evaluated_at_kst"][11:19].replace(":", "")
    target = Path(data_root) / "operations" / "collect_recovery_gates" / packet["expected_kst_date"] / f"gate-{stamp}.json"
    if target.exists():
        raise RecoveryGateError(f"APPEND_ONLY_VIOLATION:{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(packet, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", required=True)
    parser.add_argument("--evaluated-at-kst", required=True)
    parser.add_argument("--data-root", type=Path, default=DATA)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    packet = build_packet(args.today, args.evaluated_at_kst, args.data_root)
    if args.record:
        record_packet(args.data_root, packet)
    print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    exit_codes = {
        "RECOVERY_WINDOW_OPEN": 0,
        "DATA_READY": 0,
        "DATA_READY_BRIEFING_READ_MODEL_DEGRADED": 2,
        "DATA_NOT_READY": 3,
        "UNKNOWN_MANUAL_INSPECTION_REQUIRED": 4,
    }
    return exit_codes[packet["classification"]]


if __name__ == "__main__":
    raise SystemExit(main())
