#!/usr/bin/env python3
"""P8-02 deterministic Regime-to-Portfolio daily decision assembly contract."""
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
CONTRACT_PATH = ROOT / "config" / "unified_decision_contract.json"
OUTPUT_SCHEMA_VERSION = "unified_daily_decision/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KST = dt.timezone(dt.timedelta(hours=9), name="Asia/Seoul")


def _load_validator(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"COMPONENT_VALIDATOR_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGIME = _load_validator(
    "unified_decision_regime_validator",
    "briefing/three_market_regime_header.py",
)
ROTATION_DISCOVERY = _load_validator(
    "unified_decision_rotation_validator",
    "briefing/rotation_discovery.py",
)
RULE = _load_validator(
    "unified_decision_rule_validator",
    "rules/deterministic_rule_evaluator.py",
)
PORTFOLIO_BUCKET = _load_validator(
    "unified_decision_bucket_validator",
    "portfolio/bucket_membership.py",
)
PORTFOLIO_CURRENCY = _load_validator(
    "unified_decision_currency_validator",
    "portfolio/currency_exposure.py",
)
ACTION_BOUNDARY = _load_validator(
    "unified_decision_action_validator",
    "decision/ready_signal_order_boundary.py",
)
COMPONENT_VALIDATORS = {
    "REGIME": (REGIME, "validate_header"),
    "ROTATION_DISCOVERY": (ROTATION_DISCOVERY, "validate_briefing"),
    "RULE": (RULE, "validate_packet"),
    "PORTFOLIO_BUCKET": (PORTFOLIO_BUCKET, "validate_packet"),
    "PORTFOLIO_CURRENCY": (PORTFOLIO_CURRENCY, "validate_packet"),
    "ACTION_BOUNDARY": (ACTION_BOUNDARY, "validate_packet"),
}


class UnifiedDecisionContractError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UnifiedDecisionContractError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "contract_version", "output_schema_version", "slots",
        "component_order", "pipeline_order", "components", "authority",
    }:
        raise UnifiedDecisionContractError("CONTRACT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "unified_decision_contract/1"
        or value.get("output_schema_version") != OUTPUT_SCHEMA_VERSION
        or value.get("slots") != ["morning", "evening"]
        or value.get("component_order") != [
            "REGIME", "ROTATION_DISCOVERY", "RULE", "PORTFOLIO_BUCKET",
            "PORTFOLIO_CURRENCY", "ACTION_BOUNDARY",
        ]
        or value.get("pipeline_order") != [
            "REGIME", "ROTATION_DISCOVERY", "RULE", "PORTFOLIO",
        ]
        or value.get("authority") != {
            "daily_decision_assembly_only": True,
            "source_interpretation_authorized": False,
            "stage_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "portfolio_sizing_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
    ):
        raise UnifiedDecisionContractError("CONTRACT_IDENTITY_INVALID")
    components = value.get("components")
    if not isinstance(components, dict) or list(components) != value["component_order"]:
        raise UnifiedDecisionContractError("CONTRACT_COMPONENT_ORDER_INVALID")
    for name, spec in components.items():
        if not isinstance(spec, dict) or set(spec) != {
            "schema_version", "contract_version", "status", "required_fields",
            "source_time_field", "authority",
        }:
            raise UnifiedDecisionContractError(f"CONTRACT_COMPONENT_INVALID:{name}")
        if (
            not all(isinstance(spec[key], str) and spec[key] for key in (
                "schema_version", "contract_version", "status"
            ))
            or not isinstance(spec["required_fields"], list)
            or sorted(set(spec["required_fields"])) != sorted(spec["required_fields"])
            or "packet_sha256" not in spec["required_fields"]
            or spec["source_time_field"] not in ({None} | set(spec["required_fields"]))
            or not isinstance(spec["authority"], dict)
            or spec["authority"].get("production_authorized") is not False
            or spec["authority"].get("trading_authorized") is not False
        ):
            raise UnifiedDecisionContractError(f"CONTRACT_COMPONENT_INVALID:{name}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> str:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise UnifiedDecisionContractError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise UnifiedDecisionContractError(code) from exc
    if parsed.isoformat() != value:
        raise UnifiedDecisionContractError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise UnifiedDecisionContractError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise UnifiedDecisionContractError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise UnifiedDecisionContractError(code)
    return value


def _kst_operating_date(value: str) -> str:
    """Return the Asia/Seoul operating date for a validated UTC instant.

    ``generated_at`` is retained as UTC while ``decision_date`` is the KST
    operating date used by the scheduled briefing workflow.  The weekday
    morning run occurs after midnight in Seoul but on the previous UTC
    calendar date, so comparing the two strings' first ten characters would
    incorrectly reject every normal morning packet.
    """
    parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    return parsed.astimezone(KST).date().isoformat()


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise UnifiedDecisionContractError(code)
    return value


def _reasons(value, code: str, empty_allowed: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or value != sorted(set(value))
        or (not empty_allowed and not value)
        or any(not isinstance(item, str) or REASON_RE.fullmatch(item) is None for item in value)
    ):
        raise UnifiedDecisionContractError(code)
    return list(value)


def _validate_source_time(name: str, packet: dict, spec: dict, decision_date: str, generated_at: str):
    field = spec["source_time_field"]
    if field is None:
        return None
    value = packet[field]
    if field == "as_of_date":
        checked = _date(value, f"COMPONENT_TIME_INVALID:{name}")
        if checked > decision_date:
            raise UnifiedDecisionContractError(f"COMPONENT_FROM_FUTURE:{name}")
        return checked
    checked = _utc(value, f"COMPONENT_TIME_INVALID:{name}")
    if checked > generated_at:
        raise UnifiedDecisionContractError(f"COMPONENT_FROM_FUTURE:{name}")
    return checked


def _validate_source(name: str, packet: dict, decision_date: str, slot: str, generated_at: str, contract: dict) -> dict:
    spec = contract["components"][name]
    if not isinstance(packet, dict) or set(packet) != set(spec["required_fields"]):
        raise UnifiedDecisionContractError(f"COMPONENT_FIELDS_MISMATCH:{name}")
    if (
        packet.get("schema_version") != spec["schema_version"]
        or packet.get("contract_version") != spec["contract_version"]
        or packet.get("status") != spec["status"]
        or packet.get("authority") != spec["authority"]
    ):
        raise UnifiedDecisionContractError(f"COMPONENT_IDENTITY_INVALID:{name}")
    if "slot" in packet and packet["slot"] != slot:
        raise UnifiedDecisionContractError(f"COMPONENT_SLOT_MISMATCH:{name}")
    digest = _sha(packet.get("packet_sha256"), f"COMPONENT_SHA_INVALID:{name}")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise UnifiedDecisionContractError(f"COMPONENT_SHA_MISMATCH:{name}")
    source_as_of = _validate_source_time(
        name, packet, spec, decision_date, generated_at
    )
    validator, method_name = COMPONENT_VALIDATORS[name]
    try:
        packet = getattr(validator, method_name)(copy.deepcopy(packet))
    except Exception as exc:
        raise UnifiedDecisionContractError(
            f"COMPONENT_SEMANTIC_INVALID:{name}:{exc}"
        ) from exc
    return {
        "packet": copy.deepcopy(packet),
        "packet_sha256": digest,
        "source_as_of": source_as_of,
    }


def _component_rows(components: dict, unavailable_reasons: dict, decision_date: str, slot: str, generated_at: str, contract: dict) -> list[dict]:
    if not isinstance(components, dict) or set(components) != set(contract["component_order"]):
        raise UnifiedDecisionContractError("COMPONENT_INPUT_KEYS_MISMATCH")
    if not isinstance(unavailable_reasons, dict) or set(unavailable_reasons) != set(contract["component_order"]):
        raise UnifiedDecisionContractError("UNAVAILABLE_REASON_KEYS_MISMATCH")
    rows = []
    for name in contract["component_order"]:
        source = components[name]
        reasons = unavailable_reasons[name]
        if source is None:
            rows.append({
                "component": name,
                "availability": "UNAVAILABLE",
                "source_status": None,
                "source_as_of": None,
                "source_packet_sha256": None,
                "unavailable_reasons": _reasons(
                    reasons, f"UNAVAILABLE_REASONS_INVALID:{name}"
                ),
                "source_packet": None,
            })
            continue
        if reasons != []:
            raise UnifiedDecisionContractError(f"AVAILABLE_COMPONENT_HAS_REASONS:{name}")
        checked = _validate_source(
            name, source, decision_date, slot, generated_at, contract
        )
        rows.append({
            "component": name,
            "availability": "AVAILABLE",
            "source_status": source["status"],
            "source_as_of": checked["source_as_of"],
            "source_packet_sha256": checked["packet_sha256"],
            "unavailable_reasons": [],
            "source_packet": checked["packet"],
        })
    return rows


def _pipeline(rows: list[dict], contract: dict) -> list[dict]:
    available = {row["component"]: row["availability"] == "AVAILABLE" for row in rows}
    requirements = {
        "REGIME": ["REGIME"],
        "ROTATION_DISCOVERY": ["REGIME", "ROTATION_DISCOVERY"],
        "RULE": ["REGIME", "ROTATION_DISCOVERY", "RULE"],
        "PORTFOLIO": [
            "REGIME", "ROTATION_DISCOVERY", "RULE",
            "PORTFOLIO_BUCKET", "PORTFOLIO_CURRENCY",
        ],
    }
    result = []
    for stage in contract["pipeline_order"]:
        required = requirements[stage]
        missing = [name for name in required if not available[name]]
        own = required[-2:] if stage == "PORTFOLIO" else [required[-1]]
        own_available = sum(available[name] for name in own)
        state = (
            "AVAILABLE" if own_available == len(own)
            else "UNAVAILABLE" if own_available == 0
            else "PARTIAL"
        )
        result.append({
            "stage": stage,
            "state": state,
            "required_components": required,
            "missing_components": missing,
            "upstream_gate_satisfied": not missing,
        })
    return result


def _decision(rows: list[dict]) -> dict:
    missing = [row["component"] for row in rows if row["availability"] == "UNAVAILABLE"]
    blocking = [f"MISSING_COMPONENT:{name}" for name in missing]
    blocking.extend([
        "REGIME_INTERPRETATION_NOT_AUTHORIZED",
        "STAGE_PROMOTION_NOT_AUTHORIZED",
        "RULE_PASS_FAIL_NOT_AUTHORIZED",
        "PORTFOLIO_SIZING_NOT_AUTHORIZED",
        "ACTION_GENERATION_NOT_AUTHORIZED",
        "ORDER_GENERATION_NOT_AUTHORIZED",
        "PRODUCTION_NOT_AUTHORIZED",
    ])
    return {
        "state": "NO_ACTION_AUTHORIZED",
        "action": None,
        "entry_trigger": None,
        "position_size": None,
        "order_intent": None,
        "blocking_reasons": sorted(set(blocking)),
    }


def build_packet(
    components: dict,
    unavailable_reasons: dict,
    decision_date: str,
    slot: str,
    generated_at: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    decision_date = _date(decision_date, "DECISION_DATE_INVALID")
    generated_at = _utc(generated_at, "GENERATED_AT_INVALID")
    if _kst_operating_date(generated_at) != decision_date:
        raise UnifiedDecisionContractError("GENERATED_DATE_MISMATCH")
    if slot not in contract["slots"]:
        raise UnifiedDecisionContractError(f"SLOT_INVALID:{slot}")
    rows = _component_rows(
        components, unavailable_reasons, decision_date, slot, generated_at, contract
    )
    pipeline = _pipeline(rows, contract)
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "decision_id": f"atlas-{decision_date}-{slot}",
        "decision_date": decision_date,
        "slot": slot,
        "generated_at": generated_at,
        "status": "DAILY_DECISION_ASSEMBLED_NO_ACTION_AUTHORITY",
        "pipeline": pipeline,
        "components": rows,
        "summary": {
            "component_count": len(rows),
            "available_component_count": sum(
                row["availability"] == "AVAILABLE" for row in rows
            ),
            "unavailable_component_count": sum(
                row["availability"] == "UNAVAILABLE" for row in rows
            ),
            "pipeline_stage_count": len(pipeline),
            "pipeline_complete": all(row["upstream_gate_satisfied"] for row in pipeline),
        },
        "decision": _decision(rows),
        "lineage": {
            "component_packet_sha256": {
                row["component"]: row["source_packet_sha256"] for row in rows
            }
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "LIVE_DAILY_WIRING_NOT_IMPLEMENTED",
            "REGIME_INTERPRETATION_NOT_AUTHORIZED",
            "STAGE_PROMOTION_NOT_AUTHORIZED",
            "RULE_PASS_FAIL_NOT_AUTHORIZED",
            "PORTFOLIO_POLICY_PARTIALLY_UNRATIFIED",
            "ACTION_AND_ORDER_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "decision_id", "decision_date", "slot",
        "generated_at", "status", "pipeline", "components", "summary", "decision",
        "lineage", "authority", "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise UnifiedDecisionContractError("PACKET_FIELDS_MISMATCH")
    decision_date = _date(packet.get("decision_date"), "DECISION_DATE_INVALID")
    generated_at = _utc(packet.get("generated_at"), "GENERATED_AT_INVALID")
    slot = packet.get("slot")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or slot not in contract["slots"]
        or packet.get("decision_id") != f"atlas-{decision_date}-{slot}"
        or _kst_operating_date(generated_at) != decision_date
        or packet.get("status") != "DAILY_DECISION_ASSEMBLED_NO_ACTION_AUTHORITY"
        or packet.get("authority") != contract["authority"]
    ):
        raise UnifiedDecisionContractError("PACKET_IDENTITY_INVALID")
    rows = packet.get("components")
    if not isinstance(rows, list) or [row.get("component") for row in rows if isinstance(row, dict)] != contract["component_order"]:
        raise UnifiedDecisionContractError("COMPONENT_ROWS_ORDER_INVALID")
    source_inputs = {}
    reason_inputs = {}
    row_fields = {
        "component", "availability", "source_status", "source_as_of",
        "source_packet_sha256", "unavailable_reasons", "source_packet",
    }
    for row in rows:
        name = row["component"]
        if set(row) != row_fields or row.get("availability") not in {"AVAILABLE", "UNAVAILABLE"}:
            raise UnifiedDecisionContractError(f"COMPONENT_ROW_INVALID:{name}")
        source_inputs[name] = row["source_packet"]
        reason_inputs[name] = row["unavailable_reasons"]
    expected_rows = _component_rows(
        source_inputs, reason_inputs, decision_date, slot, generated_at, contract
    )
    if rows != expected_rows:
        raise UnifiedDecisionContractError("COMPONENT_ROWS_MISMATCH")
    expected_pipeline = _pipeline(rows, contract)
    if packet.get("pipeline") != expected_pipeline:
        raise UnifiedDecisionContractError("PIPELINE_MISMATCH")
    expected_summary = {
        "component_count": len(rows),
        "available_component_count": sum(row["availability"] == "AVAILABLE" for row in rows),
        "unavailable_component_count": sum(row["availability"] == "UNAVAILABLE" for row in rows),
        "pipeline_stage_count": len(expected_pipeline),
        "pipeline_complete": all(row["upstream_gate_satisfied"] for row in expected_pipeline),
    }
    if packet.get("summary") != expected_summary:
        raise UnifiedDecisionContractError("SUMMARY_MISMATCH")
    if packet.get("decision") != _decision(rows):
        raise UnifiedDecisionContractError("DECISION_MISMATCH")
    expected_lineage = {
        "component_packet_sha256": {
            row["component"]: row["source_packet_sha256"] for row in rows
        }
    }
    if packet.get("lineage") != expected_lineage:
        raise UnifiedDecisionContractError("LINEAGE_MISMATCH")
    if packet.get("unresolved_boundaries") != [
        "LIVE_DAILY_WIRING_NOT_IMPLEMENTED",
        "REGIME_INTERPRETATION_NOT_AUTHORIZED",
        "STAGE_PROMOTION_NOT_AUTHORIZED",
        "RULE_PASS_FAIL_NOT_AUTHORIZED",
        "PORTFOLIO_POLICY_PARTIALLY_UNRATIFIED",
        "ACTION_AND_ORDER_NOT_AUTHORIZED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]:
        raise UnifiedDecisionContractError("BOUNDARIES_MISMATCH")
    digest = _sha(packet.get("packet_sha256"), "PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise UnifiedDecisionContractError("PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise UnifiedDecisionContractError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
        value = _read_json(input_path)
        if not isinstance(value, dict) or set(value) != {
            "decision_date", "slot", "generated_at", "components",
            "unavailable_reasons",
        }:
            raise UnifiedDecisionContractError("INPUT_ENVELOPE_FIELDS_MISMATCH")
        packet = build_packet(
            value["components"], value["unavailable_reasons"],
            value["decision_date"], value["slot"], value["generated_at"],
        )
        write_json_atomic(output_path, packet)
        return 0
    except (UnifiedDecisionContractError, OSError, TypeError, ValueError) as exc:
        print(f"Unified Decision Contract failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
