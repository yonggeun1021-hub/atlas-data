#!/usr/bin/env python3
"""P8-14 policy-neutral Flow-First briefing presentation contract.

The adapter reorders already-built Daily Orchestrator components.  It never
scores Regime, infers cross-market flow, promotes a candidate, sizes capital,
or creates an entry/exit/order.  Missing upstream contracts remain visible as
UNKNOWN or POLICY_BLOCKED instead of being omitted from the briefing.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/flow_first_briefing_contract.json"


class FlowFirstBriefingError(ValueError):
    """Fail-closed Flow-First presentation contract violation."""


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    if spec is None or spec.loader is None:
        raise FlowFirstBriefingError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CROSS_ASSET_FLOW = _load_module(
    "atlas_flow_first_cross_asset_flow",
    "rotation/cross_asset_flow_evidence.py",
)

def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FlowFirstBriefingError("CONTRACT_READ_FAILED") from exc
    return _validate_contract(contract)


def _validate_contract(contract: dict) -> dict:
    expected = {
        "schema_version",
        "contract_version",
        "output_schema_version",
        "source_contract_version",
        "source_output_schema_version",
        "cross_asset_flow_contract_version",
        "cross_asset_flow_output_schema_version",
        "section_order",
        "sections",
        "status_values",
        "evidence_grade_values",
        "authority",
    }
    if set(contract) != expected:
        raise FlowFirstBriefingError("CONTRACT_FIELDS_MISMATCH")
    section_order = contract["section_order"]
    sections = contract["sections"]
    if not isinstance(section_order, list) or not isinstance(sections, dict):
        raise FlowFirstBriefingError("CONTRACT_SECTIONS_INVALID")
    if section_order != list(sections) or len(section_order) != len(set(section_order)):
        raise FlowFirstBriefingError("CONTRACT_SECTION_ORDER_INVALID")
    status_values = contract["status_values"]
    if not isinstance(status_values, list) or set(status_values) != set(_STATUS_PRIORITY) or len(status_values) != len(_STATUS_PRIORITY):
        raise FlowFirstBriefingError("CONTRACT_STATUS_VALUES_INVALID")
    if contract["evidence_grade_values"] != ["UNKNOWN"]:
        raise FlowFirstBriefingError("CONTRACT_EVIDENCE_GRADE_VALUES_INVALID")
    authority = contract["authority"]
    if authority.get("presentation_order_authorized") is not True:
        raise FlowFirstBriefingError("CONTRACT_PRESENTATION_AUTHORITY_MISSING")
    if any(value is not False for key, value in authority.items() if key != "presentation_order_authorized"):
        raise FlowFirstBriefingError("CONTRACT_AUTHORITY_MUST_BE_FALSE")
    for section_id, spec in sections.items():
        expected_spec = {"title", "source_components"}
        if not spec.get("source_components"):
            expected_spec.add("unavailable_reason")
        if set(spec) != expected_spec or not isinstance(spec.get("title"), str):
            raise FlowFirstBriefingError(f"CONTRACT_SECTION_SPEC_INVALID:{section_id}")
    return contract


def _build_cross_market_flow_section(
    daily_packet: dict,
    spec: dict,
    contract: dict,
) -> dict:
    evidence = CROSS_ASSET_FLOW.build_packet(daily_packet)
    CROSS_ASSET_FLOW.validate_packet(evidence, daily_packet)
    if evidence.get("contract_version") != contract["cross_asset_flow_contract_version"]:
        raise FlowFirstBriefingError("CROSS_ASSET_FLOW_CONTRACT_MISMATCH")
    if evidence.get("output_schema_version") != contract["cross_asset_flow_output_schema_version"]:
        raise FlowFirstBriefingError("CROSS_ASSET_FLOW_SCHEMA_MISMATCH")
    assessment = evidence["cross_market_assessment"]
    if (
        assessment.get("status") != "UNKNOWN"
        or assessment.get("flow_direction") is not None
        or assessment.get("from_market") is not None
        or assessment.get("to_market") is not None
    ):
        raise FlowFirstBriefingError("CROSS_MARKET_FLOW_AUTHORITY_EXPANDED")
    source_components: dict[str, dict] = {}
    status_counts: dict[str, int] = {}
    source_reasons = []
    for row in evidence["evidence_rows"]:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        source = row.get("source")
        if isinstance(source, dict):
            component_id = source["component_id"]
            source_components[component_id] = {
                "component_id": component_id,
                "status": row["status"],
                "reason": row["invalidation"]["reason"],
                "as_of_date": (
                    str(row["observation_at"])[:10]
                    if row["observation_at"] is not None
                    else None
                ),
                "available_at": row["available_at"],
                "source_packet_path": source["source_packet_path"],
                "source_packet_sha256": source["source_packet_sha256"],
                "validated": source["upstream_validated"],
            }
        if row["status"] != "AVAILABLE":
            source_reasons.append({
                "evidence_id": row["evidence_id"],
                "reason": row["invalidation"]["reason"],
            })
    dates = assessment["comparison_observation_dates"]
    as_of_date = dates[0] if len(dates) == 1 else None
    return {
        "section_id": "CROSS_MARKET_FLOW",
        "title": spec["title"],
        "status": "UNKNOWN",
        "as_of_date": as_of_date,
        "evidence_grade": "UNKNOWN",
        "evidence_grade_reason": "CROSS_MARKET_EVIDENCE_GRADE_AGGREGATION_UNRATIFIED",
        "unknown_reason": assessment["reason"],
        "invalidation": {
            "status": "UNKNOWN",
            "reason": "CROSS_MARKET_INVALIDATION_POLICY_UNRATIFIED",
        },
        "source_components": [source_components[key] for key in sorted(source_components)],
        "source_reasons": source_reasons,
        "cross_asset_flow_evidence": {
            "contract_version": evidence["contract_version"],
            "packet_sha256": evidence["packet_sha256"],
            "evidence_class_counts": copy.deepcopy(evidence["evidence_class_counts"]),
            "evidence_status_counts": status_counts,
            "comparison_observation_dates": copy.deepcopy(dates),
            "flow_direction": None,
            "from_market": None,
            "to_market": None,
        },
        "decision_eligible": False,
        "action_eligible": False,
        "order_eligible": False,
    }


def _verify_daily_packet(packet: dict, contract: dict) -> None:
    if not isinstance(packet, dict):
        raise FlowFirstBriefingError("SOURCE_PACKET_NOT_OBJECT")
    if packet.get("contract_version") != contract["source_contract_version"]:
        raise FlowFirstBriefingError("SOURCE_CONTRACT_MISMATCH")
    if packet.get("output_schema_version") != contract["source_output_schema_version"]:
        raise FlowFirstBriefingError("SOURCE_OUTPUT_SCHEMA_MISMATCH")
    if packet.get("slot") not in {"morning", "evening"}:
        raise FlowFirstBriefingError("SOURCE_SLOT_INVALID")
    try:
        decision_date = dt.date.fromisoformat(packet["decision_date"])
        generated_at = dt.datetime.fromisoformat(packet["generated_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise FlowFirstBriefingError("SOURCE_TIME_INVALID") from exc
    if generated_at.tzinfo is None or generated_at.utcoffset() != dt.timedelta(0):
        raise FlowFirstBriefingError("SOURCE_GENERATED_AT_NOT_UTC")
    digest = packet.get("packet_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise FlowFirstBriefingError("SOURCE_PACKET_SHA_MISMATCH")
    components = packet.get("components")
    if not isinstance(components, list):
        raise FlowFirstBriefingError("SOURCE_COMPONENTS_INVALID")
    ids = [row.get("component_id") for row in components if isinstance(row, dict)]
    if len(ids) != len(components) or len(ids) != len(set(ids)):
        raise FlowFirstBriefingError("SOURCE_COMPONENT_IDS_INVALID")
    for row in components:
        if row.get("status") not in _STATUS_PRIORITY:
            raise FlowFirstBriefingError("SOURCE_COMPONENT_STATUS_INVALID")
        if any(row.get(key) is not False for key in ("decision_eligible", "action_eligible", "order_eligible")):
            raise FlowFirstBriefingError("SOURCE_COMPONENT_AUTHORITY_INVALID")
        as_of = row.get("as_of_date")
        if as_of is not None:
            try:
                as_of_date = dt.date.fromisoformat(as_of)
            except (TypeError, ValueError) as exc:
                raise FlowFirstBriefingError("SOURCE_COMPONENT_AS_OF_INVALID") from exc
            if as_of_date > decision_date and not (
                row.get("status") == "DATA_BLOCKED"
                and row.get("reason") == "AS_OF_DATE_AFTER_DECISION_DATE"
            ):
                raise FlowFirstBriefingError("SOURCE_COMPONENT_FROM_FUTURE")
    authority = packet.get("authority")
    if not isinstance(authority, dict):
        raise FlowFirstBriefingError("SOURCE_AUTHORITY_INVALID")
    for key in (
        "action_generation_authorized", "order_generation_authorized",
        "production_authorized", "trading_authorized",
    ):
        if authority.get(key) is not False:
            raise FlowFirstBriefingError("SOURCE_AUTHORITY_EXPANDED")


_STATUS_PRIORITY = {
    "DATA_BLOCKED": 7,
    "UNAVAILABLE": 6,
    "POLICY_BLOCKED": 5,
    "DEGRADED": 4,
    "UNKNOWN": 3,
    "PENDING": 2,
    "READY": 1,
}


def _section_status(rows: list[dict]) -> str:
    return max((row["status"] for row in rows), key=_STATUS_PRIORITY.__getitem__)


def _source_ref(row: dict) -> dict:
    return {
        "component_id": row["component_id"],
        "status": row["status"],
        "reason": row.get("reason"),
        "as_of_date": row.get("as_of_date"),
        "available_at": row.get("available_at"),
        "source_packet_path": row.get("source_packet_path"),
        "source_packet_sha256": row.get("source_packet_sha256"),
        "validated": row.get("validated") is True,
    }


def _build_section(section_id: str, spec: dict, by_id: dict[str, dict]) -> dict:
    required = spec.get("source_components")
    if not isinstance(required, list):
        raise FlowFirstBriefingError(f"SECTION_SOURCE_COMPONENTS_INVALID:{section_id}")
    if not required:
        status = "UNKNOWN"
        reason = spec.get("unavailable_reason")
        if not isinstance(reason, str) or not reason:
            raise FlowFirstBriefingError(f"SECTION_UNAVAILABLE_REASON_MISSING:{section_id}")
        rows: list[dict] = []
    else:
        missing = [component_id for component_id in required if component_id not in by_id]
        if missing:
            raise FlowFirstBriefingError(f"SECTION_SOURCE_COMPONENT_MISSING:{section_id}")
        rows = [by_id[component_id] for component_id in required]
        if any(row.get("status") not in _STATUS_PRIORITY for row in rows):
            raise FlowFirstBriefingError(f"SECTION_SOURCE_STATUS_INVALID:{section_id}")
        status = _section_status(rows)
        reason = None if status == "READY" else "SOURCE_COMPONENT_NOT_READY"

    as_of_values = sorted(
        {row.get("as_of_date") for row in rows if isinstance(row.get("as_of_date"), str)}
    )
    if len(as_of_values) > 1:
        status = "DATA_BLOCKED"
        reason = "SOURCE_AS_OF_DATE_MISMATCH"
        as_of_date = None
    else:
        as_of_date = as_of_values[0] if as_of_values else None

    source_reasons = [
        {"component_id": row["component_id"], "reason": row.get("reason")}
        for row in rows
        if row.get("status") != "READY"
    ]
    return {
        "section_id": section_id,
        "title": spec["title"],
        "status": status,
        "as_of_date": as_of_date,
        "evidence_grade": "UNKNOWN",
        "evidence_grade_reason": "SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED",
        "unknown_reason": reason,
        "invalidation": {
            "status": "UNKNOWN",
            "reason": "UPSTREAM_INVALIDATION_NOT_STANDARDIZED",
        },
        "source_components": [_source_ref(row) for row in rows],
        "source_reasons": source_reasons,
        "decision_eligible": False,
        "action_eligible": False,
        "order_eligible": False,
    }


def build_packet(daily_packet: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    _verify_daily_packet(daily_packet, contract)
    by_id = {row["component_id"]: row for row in daily_packet["components"]}
    sections = []
    for section_id in contract["section_order"]:
        if section_id == "CROSS_MARKET_FLOW":
            sections.append(_build_cross_market_flow_section(
                daily_packet,
                contract["sections"][section_id],
                contract,
            ))
        else:
            sections.append(_build_section(
                section_id,
                contract["sections"][section_id],
                by_id,
            ))
    packet = {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "output_schema_version": contract["output_schema_version"],
        "decision_date": daily_packet["decision_date"],
        "slot": daily_packet["slot"],
        "generated_at": daily_packet["generated_at"],
        "source_daily_packet_sha256": daily_packet["packet_sha256"],
        "section_order": list(contract["section_order"]),
        "sections": sections,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, daily_packet: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    if not isinstance(packet, dict):
        raise FlowFirstBriefingError("OUTPUT_NOT_OBJECT")
    digest = packet.get("packet_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise FlowFirstBriefingError("OUTPUT_SHA_MISMATCH")
    rebuilt = build_packet(daily_packet, contract)
    if packet != rebuilt:
        raise FlowFirstBriefingError("OUTPUT_DERIVATION_MISMATCH")
    return packet
