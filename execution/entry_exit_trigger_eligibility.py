#!/usr/bin/env python3
"""P9-03 fail-closed ENTRY / EXIT trigger eligibility audit."""
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


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "entry_exit_trigger_eligibility_contract.json"
UNIFIED_SOURCE = ROOT / "decision" / "unified_decision_contract.py"
FRESHNESS_SOURCE = ROOT / "execution" / "intraday_freshness.py"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EntryExitTriggerEligibilityError(ValueError):
    """Fail-closed P9-03 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EntryExitTriggerEligibilityError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _load_validator(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise EntryExitTriggerEligibilityError(f"VALIDATOR_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "entry_exit_trigger_eligibility/1",
        "output_schema_version": "entry_exit_trigger_eligibility_packet/1",
        "source_contracts": {
            "UNIFIED_DECISION": {
                "schema_version": "unified_daily_decision/1",
                "contract_version": "unified_decision_contract/1",
                "status": "DAILY_DECISION_ASSEMBLED_NO_ACTION_AUTHORITY",
                "validator": "decision/unified_decision_contract.py",
            },
            "INTRADAY_FRESHNESS": {
                "schema_version": "intraday_freshness_result/1",
                "contract_version": "intraday_freshness_guard/1",
                "status": "FRESHNESS_EVALUATED_NO_ENTRY_AUTHORITY",
                "validator": "execution/intraday_freshness.py",
            },
        },
        "trigger_kinds": ["ENTRY", "EXIT"],
        "evaluation_status": "NOT_EVALUATED",
        "repository_default_trigger_policy": "ABSENT",
        "policy_requirement": "EXTERNAL_RATIFIED_TRIGGER_POLICY_REQUIRED",
        "invariants": [
            "READY_AND_FRESH_ARE_NECESSARY_NOT_SUFFICIENT_FOR_ENTRY",
            "GENERIC_SIGNAL_HAS_NO_ENTRY_EXIT_KIND_AUTHORITY",
            "EXIT_REQUIRES_SEPARATE_POSITION_RULE_AND_TRIGGER_EVIDENCE",
            "RISK_OR_FRESHNESS_STATE_NEVER_AUTO_GENERATES_ACTION",
            "TRIGGER_ELIGIBILITY_NEVER_IMPLIES_ORDER",
        ],
        "authority": {
            "structural_eligibility_audit_only": True,
            "trigger_policy_authorized": False,
            "signal_kind_interpretation_authorized": False,
            "entry_eligibility_authorized": False,
            "exit_eligibility_authorized": False,
            "action_generation_authorized": False,
            "position_sizing_authorized": False,
            "order_generation_authorized": False,
            "broker_submission_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise EntryExitTriggerEligibilityError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise EntryExitTriggerEligibilityError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise EntryExitTriggerEligibilityError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise EntryExitTriggerEligibilityError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise EntryExitTriggerEligibilityError(code)
    return parsed


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise EntryExitTriggerEligibilityError(code)
    return value


def _validate_source_identity(name: str, packet: dict, contract: dict) -> None:
    spec = contract["source_contracts"][name]
    if (
        not isinstance(packet, dict)
        or packet.get("schema_version") != spec["schema_version"]
        or packet.get("contract_version") != spec["contract_version"]
        or packet.get("status") != spec["status"]
    ):
        raise EntryExitTriggerEligibilityError(f"SOURCE_IDENTITY_INVALID:{name}")


def _validate_sources(unified: dict, freshness: dict, generated_at: str, contract: dict) -> dict:
    _validate_source_identity("UNIFIED_DECISION", unified, contract)
    _validate_source_identity("INTRADAY_FRESHNESS", freshness, contract)
    unified_validator = _load_validator("p903_unified_validator", UNIFIED_SOURCE)
    freshness_validator = _load_validator("p903_freshness_validator", FRESHNESS_SOURCE)
    try:
        checked_unified = unified_validator.validate_packet(unified)
    except Exception as exc:
        raise EntryExitTriggerEligibilityError(f"SOURCE_VALIDATION_FAILED:UNIFIED_DECISION:{exc}") from exc
    try:
        checked_freshness = freshness_validator.validate_output(freshness)
    except Exception as exc:
        raise EntryExitTriggerEligibilityError(f"SOURCE_VALIDATION_FAILED:INTRADAY_FRESHNESS:{exc}") from exc

    generated = _utc(generated_at, "GENERATED_AT_INVALID")
    decision_time = _utc(checked_unified["generated_at"], "UNIFIED_GENERATED_AT_INVALID")
    observed_time = _utc(checked_freshness["observed_at"], "FRESHNESS_OBSERVED_AT_INVALID")
    if decision_time > generated:
        raise EntryExitTriggerEligibilityError("UNIFIED_DECISION_FROM_FUTURE")
    if observed_time > generated:
        raise EntryExitTriggerEligibilityError("INTRADAY_FRESHNESS_FROM_FUTURE")
    if generated.strftime("%Y-%m-%d") != checked_unified["decision_date"]:
        raise EntryExitTriggerEligibilityError("DECISION_DATE_GENERATED_DATE_MISMATCH")
    return {"unified": checked_unified, "freshness": checked_freshness}


def _action_boundary(unified: dict) -> dict | None:
    for row in unified["components"]:
        if row["component"] == "ACTION_BOUNDARY":
            return row["source_packet"] if row["availability"] == "AVAILABLE" else None
    raise EntryExitTriggerEligibilityError("ACTION_BOUNDARY_COMPONENT_MISSING")


def _entry_blockers(subject: dict, quote: dict | None, market_mismatch: bool, pipeline_complete: bool) -> list[str]:
    blockers = [
        "ENTRY_TRIGGER_POLICY_UNRATIFIED",
        "GENERIC_SIGNAL_KIND_UNRESOLVED",
        "RULE_PASS_FAIL_NOT_AUTHORIZED",
        "PORTFOLIO_ENTRY_ELIGIBILITY_NOT_AUTHORIZED",
    ]
    if not pipeline_complete:
        blockers.append("UNIFIED_PIPELINE_INCOMPLETE")
    if subject["ready_status"] != "READY":
        blockers.append("READY_NOT_CONFIRMED")
    if subject["signal_status"] != "PRESENT":
        blockers.append("SIGNAL_NOT_PRESENT")
    if quote is None:
        blockers.append("QUOTE_MARKET_MISMATCH" if market_mismatch else "INTRADAY_QUOTE_UNAVAILABLE")
    elif quote["freshness_status"] != "FRESH":
        blockers.append("INTRADAY_QUOTE_STALE")
    return sorted(set(blockers))


def _exit_blockers(quote: dict | None, market_mismatch: bool, pipeline_complete: bool) -> list[str]:
    blockers = [
        "EXIT_TRIGGER_POLICY_UNRATIFIED",
        "GENERIC_SIGNAL_KIND_UNRESOLVED",
        "POSITION_STATE_NOT_AVAILABLE",
        "RULE_EXIT_ELIGIBILITY_NOT_AUTHORIZED",
        "PORTFOLIO_EXIT_ELIGIBILITY_NOT_AUTHORIZED",
    ]
    if not pipeline_complete:
        blockers.append("UNIFIED_PIPELINE_INCOMPLETE")
    if quote is None:
        blockers.append("QUOTE_MARKET_MISMATCH" if market_mismatch else "INTRADAY_QUOTE_UNAVAILABLE")
    elif quote["freshness_status"] != "FRESH":
        blockers.append("INTRADAY_QUOTE_STALE")
    return sorted(set(blockers))


def _assemble(checked: dict, generated_at: str, contract: dict) -> dict:
    unified = checked["unified"]
    freshness = checked["freshness"]
    boundary = _action_boundary(unified)
    quote_by_key = {(row["asset_id"], row["market"]): row for row in freshness["results"]}
    quote_markets = {}
    for row in freshness["results"]:
        quote_markets.setdefault(row["asset_id"], set()).add(row["market"])
    pipeline_complete = bool(unified["summary"]["pipeline_complete"])
    subjects = []
    source_subjects = [] if boundary is None else boundary["subjects"]
    for subject in source_subjects:
        key = (subject["subject_id"], subject["market"])
        quote = quote_by_key.get(key)
        market_mismatch = quote is None and subject["subject_id"] in quote_markets
        subjects.append({
            "subject_id": subject["subject_id"],
            "market": subject["market"],
            "ready_status": subject["ready_status"],
            "signal_status": subject["signal_status"],
            "intraday_freshness_status": None if quote is None else quote["freshness_status"],
            "fresh_for_intraday_consumption": None if quote is None else quote["fresh_for_intraday_consumption"],
            "entry": {
                "evaluation_status": contract["evaluation_status"],
                "eligible": None,
                "trigger": None,
                "blocking_reasons": _entry_blockers(subject, quote, market_mismatch, pipeline_complete),
            },
            "exit": {
                "evaluation_status": contract["evaluation_status"],
                "eligible": None,
                "trigger": None,
                "blocking_reasons": _exit_blockers(quote, market_mismatch, pipeline_complete),
            },
            "action": None,
            "position_size": None,
            "order_intent": None,
            "lineage": {
                "ready_signal_source_ref": subject["ready_source_ref"],
                "ready_signal_source_sha256": subject["ready_source_sha256"],
                "signal_source_ref": subject["signal_source_ref"],
                "signal_source_sha256": subject["signal_source_sha256"],
                "quote_source_ref": None if quote is None else quote["source_ref"],
                "quote_source_sha256": None if quote is None else quote["source_sha256"],
            },
        })
    status = (
        "TRIGGER_ELIGIBILITY_AUDITED_NO_ACTION_AUTHORITY"
        if boundary is not None
        else "TRIGGER_ELIGIBILITY_UNAVAILABLE_ACTION_BOUNDARY_MISSING"
    )
    unresolved = [
        "ENTRY_EXIT_TRIGGER_POLICY_UNRATIFIED",
        "GENERIC_SIGNAL_KIND_UNRESOLVED",
        "RULE_PASS_FAIL_NOT_AUTHORIZED",
        "POSITION_STATE_NOT_CONNECTED",
        "PORTFOLIO_ELIGIBILITY_NOT_AUTHORIZED",
        "ACTION_ORDER_PRODUCTION_TRADING_NOT_AUTHORIZED",
    ]
    if boundary is None:
        unresolved.insert(0, "ACTION_BOUNDARY_SOURCE_UNAVAILABLE")
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": status,
        "generated_at": generated_at,
        "decision_id": unified["decision_id"],
        "summary": {
            "subject_count": len(subjects),
            "ready_count": sum(row["ready_status"] == "READY" for row in subjects),
            "fresh_quote_count": sum(row["intraday_freshness_status"] == "FRESH" for row in subjects),
            "entry_eligible_count": 0,
            "exit_eligible_count": 0,
            "actions_created": 0,
            "orders_created": 0,
        },
        "subjects": subjects,
        "source_packets": {
            "UNIFIED_DECISION": copy.deepcopy(unified),
            "INTRADAY_FRESHNESS": copy.deepcopy(freshness),
        },
        "lineage": {
            "unified_decision_packet_sha256": unified["packet_sha256"],
            "intraday_freshness_packet_sha256": freshness["packet_sha256"],
            "action_boundary_packet_sha256": None if boundary is None else boundary["packet_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": unresolved,
    }


def build_packet(unified: dict, freshness: dict, generated_at: str, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    checked = _validate_sources(unified, freshness, generated_at, contract)
    packet = _assemble(checked, generated_at, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "generated_at", "decision_id",
        "summary", "subjects", "source_packets", "lineage", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise EntryExitTriggerEligibilityError("OUTPUT_FIELDS_MISMATCH")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {"UNIFIED_DECISION", "INTRADAY_FRESHNESS"}:
        raise EntryExitTriggerEligibilityError("OUTPUT_SOURCES_INVALID")
    generated_at = packet.get("generated_at")
    checked = _validate_sources(
        sources["UNIFIED_DECISION"], sources["INTRADAY_FRESHNESS"], generated_at, contract
    )
    expected = _assemble(checked, generated_at, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_SHA_INVALID")
    if actual != expected:
        raise EntryExitTriggerEligibilityError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise EntryExitTriggerEligibilityError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise EntryExitTriggerEligibilityError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(unified_path: Path, freshness_path: Path, generated_at: str, output_path: Path) -> int:
    try:
        packet = build_packet(_read_json(unified_path), _read_json(freshness_path), generated_at)
        write_json_atomic(output_path, packet)
        return 0
    except (EntryExitTriggerEligibilityError, OSError, TypeError, ValueError) as exc:
        print(f"ENTRY/EXIT trigger eligibility failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unified_decision", type=Path)
    parser.add_argument("intraday_freshness", type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.unified_decision, args.intraday_freshness, args.generated_at, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
