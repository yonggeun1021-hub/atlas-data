#!/usr/bin/env python3
"""P8-05 non-interpretive Rotation / Discovery briefing read model."""
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
CONTRACT_PATH = ROOT / "config" / "rotation_discovery_briefing_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROTATION = _load_module(
    "atlas_rotation_state_ledger", ROOT / "rotation" / "rotation_state_ledger.py"
)
DISCOVERY = _load_module(
    "atlas_event_discovery_case", ROOT / "discovery" / "event_case.py"
)


class RotationDiscoveryBriefingError(ValueError):
    """Fail-closed P8-05 input or output contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationDiscoveryBriefingError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "rotation_discovery_briefing/1",
        "output_schema_version": "rotation_discovery_briefing_packet/1",
        "rotation_source_contract": "rotation_state_ledger/1",
        "discovery_source_contract": "event_discovery_case/2",
        "market_order": ["US", "KOREA", "CRYPTO"],
        "rotation_states": ["EMERGING", "STRONG", "WEAKENING"],
        "evidence_statuses": [
            "EVIDENCE_LINKED", "EVIDENCE_BLOCKED", "EVIDENCE_UNRESOLVED"
        ],
        "slots": ["morning", "evening"],
        "status": "ROTATION_DISCOVERY_PRESENTED_NO_PROMOTION_AUTHORITY",
        "authority": {
            "briefing_read_model_only": True,
            "importance_ranking_authorized": False,
            "interpretation_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RotationDiscoveryBriefingError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RotationDiscoveryBriefingError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise RotationDiscoveryBriefingError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise RotationDiscoveryBriefingError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise RotationDiscoveryBriefingError(code)
    return parsed


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str):
        raise RotationDiscoveryBriefingError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise RotationDiscoveryBriefingError(code) from exc
    if parsed.isoformat() != value:
        raise RotationDiscoveryBriefingError(code)
    return parsed


def _rotation_section(ledger: dict, generated: dt.datetime, contract: dict) -> dict:
    try:
        checked = ROTATION.validate_ledger(copy.deepcopy(ledger))
    except ROTATION.RotationStateLedgerError as exc:
        raise RotationDiscoveryBriefingError(f"ROTATION_LEDGER_INVALID:{exc}") from exc
    if checked["contract_version"] != contract["rotation_source_contract"]:
        raise RotationDiscoveryBriefingError("ROTATION_CONTRACT_INVALID")
    for source in checked["source_packets"]:
        if _date(source["as_of_date"], "ROTATION_DATE_INVALID") > generated.date():
            raise RotationDiscoveryBriefingError(
                f"ROTATION_FROM_FUTURE:{source['market']}:{source['as_of_date']}"
            )

    latest = {}
    for record in checked["records"]:
        key = (record["market"], record["scope_id"], record["entity_id"])
        latest[key] = record
    market_index = {market: index for index, market in enumerate(contract["market_order"])}
    rows = []
    counts = {state: 0 for state in contract["rotation_states"]}
    for key in sorted(latest, key=lambda item: (market_index[item[0]], item[1], item[2])):
        record = latest[key]
        if _date(record["as_of_date"], "ROTATION_RECORD_DATE_INVALID") > generated.date():
            raise RotationDiscoveryBriefingError(
                f"ROTATION_RECORD_FROM_FUTURE:{record['entity_id']}"
            )
        counts[record["current_p2_state"]] += 1
        rows.append({
            "market": record["market"],
            "scope_id": record["scope_id"],
            "entity_id": record["entity_id"],
            "as_of_date": record["as_of_date"],
            "structural_bucket_transition": record["structural_bucket_transition"],
            "prior_state": record["prior_p2_state"],
            "current_state": record["current_p2_state"],
            "state_transition": record["state_transition"],
            "record_sha256": record["record_sha256"],
            "source_packet_sha256": record["input_packet_sha256"],
        })
    return {
        "ledger_status": checked["status"],
        "ledger_revision": checked["ledger_revision"],
        "latest_change_count": len(rows),
        "state_counts": counts,
        "latest_changes": rows,
        "source_boundaries": copy.deepcopy(checked["unresolved_boundaries"]),
        "source_ledger_sha256": checked["payload_sha256"],
    }


def _discovery_section(
    records: list[dict], bindings: dict, generated: dt.datetime, contract: dict
) -> dict:
    try:
        packet = DISCOVERY.build_packet(
            records=copy.deepcopy(records),
            evidence_bindings=copy.deepcopy(bindings),
        )
    except DISCOVERY.EventCaseError as exc:
        raise RotationDiscoveryBriefingError(f"DISCOVERY_INPUT_INVALID:{exc}") from exc
    if packet["contract_version"] != contract["discovery_source_contract"]:
        raise RotationDiscoveryBriefingError("DISCOVERY_CONTRACT_INVALID")
    cases = []
    for case in packet["cases"]:
        if _date(case["event_date"], "DISCOVERY_EVENT_DATE_INVALID") > generated.date():
            raise RotationDiscoveryBriefingError(
                f"DISCOVERY_EVENT_FROM_FUTURE:{case['case_id']}"
            )
        lineage = case["evidence_lineage"]
        if isinstance(lineage, dict):
            retrieved = lineage.get("retrieved_at_utc")
            if retrieved is not None and _utc(
                retrieved, "DISCOVERY_RETRIEVED_AT_INVALID"
            ) > generated:
                raise RotationDiscoveryBriefingError(
                    f"DISCOVERY_EVIDENCE_FROM_FUTURE:{case['case_id']}"
                )
        cases.append({
            "case_id": case["case_id"],
            "market": case["market"],
            "subject": case["subject"],
            "subject_name": case["subject_name"],
            "event_type": case["event_type"],
            "event_date": case["event_date"],
            "evidence_status": case["evidence_status"],
            "evidence_lineage": copy.deepcopy(lineage),
            "importance_status": case["importance_status"],
            "interpretation_status": case["interpretation_status"],
            "promotion_status": case["promotion_status"],
            "stage_transition": case["stage_transition"],
            "investment_action": case["investment_action"],
        })
    counts = {
        status: packet["summary"][status] for status in contract["evidence_statuses"]
    }
    return {
        "case_count": len(cases),
        "evidence_counts": counts,
        "cases": cases,
        "new_candidates": [],
        "existing_candidate_changes": [],
        "source_coverage": copy.deepcopy(packet["source_coverage"]),
        "source_packet_sha256": packet["packet_sha256"],
    }


def build_briefing(
    rotation_ledger: dict,
    discovery_records: list[dict],
    evidence_bindings: dict,
    slot: str,
    generated_at: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if slot not in contract["slots"]:
        raise RotationDiscoveryBriefingError(f"SLOT_INVALID:{slot}")
    generated = _utc(generated_at, "GENERATED_AT_INVALID")
    rotation = _rotation_section(rotation_ledger, generated, contract)
    discovery = _discovery_section(
        discovery_records, evidence_bindings, generated, contract
    )
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "slot": slot,
        "generated_at": generated_at,
        "status": contract["status"],
        "rotation": rotation,
        "discovery": discovery,
        "summary": {
            "rotation_change_count": rotation["latest_change_count"],
            "discovery_case_count": discovery["case_count"],
            "new_candidate_count": 0,
            "existing_candidate_change_count": 0,
            "ranked_candidate": None,
            "action": None,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "DISCOVERY_IMPORTANCE_POLICY_UNRATIFIED",
            "DISCOVERY_INTERPRETATION_NOT_AUTHORIZED",
            "CANDIDATE_STAGE_PROMOTION_NOT_AUTHORIZED",
            "CANDIDATE_RANKING_NOT_AUTHORIZED",
            "ACTION_GENERATION_NOT_AUTHORIZED",
            "PRODUCTION_WIRING_NOT_IMPLEMENTED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_briefing(packet, contract)


def validate_briefing(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "slot", "generated_at", "status",
        "rotation", "discovery", "summary", "authority", "unresolved_boundaries",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise RotationDiscoveryBriefingError("BRIEFING_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("slot") not in contract["slots"]
        or packet.get("status") != contract["status"]
        or packet.get("authority") != contract["authority"]
    ):
        raise RotationDiscoveryBriefingError("BRIEFING_IDENTITY_INVALID")
    generated = _utc(packet.get("generated_at"), "BRIEFING_GENERATED_AT_INVALID")
    rotation = packet.get("rotation")
    discovery = packet.get("discovery")
    if not isinstance(rotation, dict) or not isinstance(discovery, dict):
        raise RotationDiscoveryBriefingError("BRIEFING_SECTIONS_INVALID")
    rotation_fields = {
        "ledger_status", "ledger_revision", "latest_change_count", "state_counts",
        "latest_changes", "source_boundaries", "source_ledger_sha256",
    }
    discovery_fields = {
        "case_count", "evidence_counts", "cases", "new_candidates",
        "existing_candidate_changes", "source_coverage", "source_packet_sha256",
    }
    if set(rotation) != rotation_fields or set(discovery) != discovery_fields:
        raise RotationDiscoveryBriefingError("BRIEFING_SECTION_FIELDS_MISMATCH")
    if not isinstance(rotation["latest_changes"], list):
        raise RotationDiscoveryBriefingError("BRIEFING_ROTATION_ROWS_INVALID")
    change_fields = {
        "market", "scope_id", "entity_id", "as_of_date",
        "structural_bucket_transition", "prior_state", "current_state",
        "state_transition", "record_sha256", "source_packet_sha256",
    }
    market_index = {market: index for index, market in enumerate(contract["market_order"])}
    rotation_contract = ROTATION.load_contract()
    change_keys = []
    derived_state_counts = {state: 0 for state in contract["rotation_states"]}
    for row in rotation["latest_changes"]:
        if not isinstance(row, dict) or set(row) != change_fields:
            raise RotationDiscoveryBriefingError("BRIEFING_ROTATION_ROW_FIELDS_INVALID")
        market = row.get("market")
        prior = row.get("prior_state")
        current = row.get("current_state")
        if (
            market not in contract["market_order"]
            or current not in contract["rotation_states"]
            or (prior is not None and prior not in contract["rotation_states"])
            or row.get("structural_bucket_transition")
            not in rotation_contract["structural_bucket_transitions"]
            or row.get("state_transition")
            != (f"UNINITIALIZED_TO_{current}" if prior is None else f"{prior}_TO_{current}")
            or not isinstance(row.get("scope_id"), str)
            or not row["scope_id"].strip()
            or not isinstance(row.get("entity_id"), str)
            or not row["entity_id"].strip()
        ):
            raise RotationDiscoveryBriefingError("BRIEFING_ROTATION_ROW_VALUE_INVALID")
        if _date(row["as_of_date"], "BRIEFING_ROTATION_DATE_INVALID") > generated.date():
            raise RotationDiscoveryBriefingError("BRIEFING_ROTATION_FROM_FUTURE")
        for field in ("record_sha256", "source_packet_sha256"):
            value = row.get(field)
            if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                raise RotationDiscoveryBriefingError("BRIEFING_ROTATION_ROW_SHA_INVALID")
        change_keys.append((market_index[market], row["scope_id"], row["entity_id"]))
        derived_state_counts[current] += 1
    if change_keys != sorted(set(change_keys)):
        raise RotationDiscoveryBriefingError("BRIEFING_ROTATION_ROW_ORDER_INVALID")
    if (
        type(rotation["ledger_revision"]) is not int
        or rotation["ledger_revision"] < 0
        or rotation["ledger_status"] not in {"EMPTY", "STATE_HISTORY_OBSERVED"}
        or type(rotation["latest_change_count"]) is not int
        or rotation["latest_change_count"] != len(rotation["latest_changes"])
        or rotation.get("state_counts") != derived_state_counts
        or not isinstance(rotation["source_boundaries"], list)
        or any(not isinstance(item, str) or not item for item in rotation["source_boundaries"])
    ):
        raise RotationDiscoveryBriefingError("BRIEFING_ROTATION_SUMMARY_INVALID")
    if not isinstance(discovery["cases"], list):
        raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_CASES_INVALID")
    case_fields = {
        "case_id", "market", "subject", "subject_name", "event_type",
        "event_date", "evidence_status", "evidence_lineage", "importance_status",
        "interpretation_status", "promotion_status", "stage_transition",
        "investment_action",
    }
    case_ids = []
    derived_evidence_counts = {status: 0 for status in contract["evidence_statuses"]}
    for case in discovery["cases"]:
        if not isinstance(case, dict) or set(case) != case_fields:
            raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_CASE_FIELDS_INVALID")
        evidence_status = case.get("evidence_status")
        if (
            not isinstance(case.get("case_id"), str)
            or not case["case_id"].startswith("event-case-")
            or case.get("market") != "US"
            or not isinstance(case.get("subject"), str)
            or not case["subject"]
            or not isinstance(case.get("event_type"), str)
            or not case["event_type"]
            or evidence_status not in contract["evidence_statuses"]
            or case.get("importance_status") != "IMPORTANCE_UNRATIFIED"
            or case.get("interpretation_status") != "INTERPRETATION_NOT_AUTHORIZED"
            or case.get("promotion_status") != "PROMOTION_NOT_AUTHORIZED"
            or case.get("stage_transition") is not None
            or case.get("investment_action") is not None
        ):
            raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_CASE_VALUE_INVALID")
        if _date(case["event_date"], "BRIEFING_DISCOVERY_DATE_INVALID") > generated.date():
            raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_FROM_FUTURE")
        lineage = case["evidence_lineage"]
        if lineage is not None and not isinstance(lineage, dict):
            raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_LINEAGE_INVALID")
        if isinstance(lineage, dict) and lineage.get("retrieved_at_utc") is not None:
            if _utc(
                lineage["retrieved_at_utc"], "BRIEFING_DISCOVERY_LINEAGE_TIME_INVALID"
            ) > generated:
                raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_LINEAGE_FROM_FUTURE")
        case_ids.append(case["case_id"])
        derived_evidence_counts[evidence_status] += 1
    if case_ids != sorted(set(case_ids)):
        raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_CASE_ORDER_INVALID")
    if (
        type(discovery["case_count"]) is not int
        or discovery["case_count"] != len(discovery["cases"])
        or discovery.get("evidence_counts") != derived_evidence_counts
        or discovery["new_candidates"] != []
        or discovery["existing_candidate_changes"] != []
        or discovery.get("source_coverage") != DISCOVERY.load_contract()["source_coverage"]
    ):
        raise RotationDiscoveryBriefingError("BRIEFING_DISCOVERY_SUMMARY_INVALID")
    expected_summary = {
        "rotation_change_count": len(rotation["latest_changes"]),
        "discovery_case_count": len(discovery["cases"]),
        "new_candidate_count": 0,
        "existing_candidate_change_count": 0,
        "ranked_candidate": None,
        "action": None,
    }
    if packet.get("summary") != expected_summary:
        raise RotationDiscoveryBriefingError("BRIEFING_SUMMARY_INVALID")
    expected_boundaries = [
        "DISCOVERY_IMPORTANCE_POLICY_UNRATIFIED",
        "DISCOVERY_INTERPRETATION_NOT_AUTHORIZED",
        "CANDIDATE_STAGE_PROMOTION_NOT_AUTHORIZED",
        "CANDIDATE_RANKING_NOT_AUTHORIZED",
        "ACTION_GENERATION_NOT_AUTHORIZED",
        "PRODUCTION_WIRING_NOT_IMPLEMENTED",
    ]
    if packet.get("unresolved_boundaries") != expected_boundaries:
        raise RotationDiscoveryBriefingError("BRIEFING_BOUNDARIES_INVALID")
    for value, code in (
        (rotation["source_ledger_sha256"], "ROTATION_SOURCE_SHA_INVALID"),
        (discovery["source_packet_sha256"], "DISCOVERY_SOURCE_SHA_INVALID"),
        (packet.get("packet_sha256"), "BRIEFING_SHA_INVALID"),
    ):
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            raise RotationDiscoveryBriefingError(code)
    normalized = copy.deepcopy(packet)
    digest = normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise RotationDiscoveryBriefingError("BRIEFING_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise RotationDiscoveryBriefingError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(
    ledger_path: Path,
    records_path: Path,
    bindings_path: Path,
    slot: str,
    generated_at: str,
    output_path: Path,
) -> int:
    try:
        packet = build_briefing(
            _read_json(ledger_path),
            DISCOVERY.load_jsonl(records_path),
            _read_json(bindings_path),
            slot,
            generated_at,
        )
        write_json_atomic(output_path, packet)
        return 0
    except (
        RotationDiscoveryBriefingError,
        DISCOVERY.EventCaseError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Rotation / Discovery briefing failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a policy-neutral Rotation/Discovery briefing")
    parser.add_argument("rotation_ledger", type=Path)
    parser.add_argument("discovery_records", type=Path)
    parser.add_argument("evidence_bindings", type=Path)
    parser.add_argument("--slot", choices=("morning", "evening"), required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(
        args.rotation_ledger,
        args.discovery_records,
        args.evidence_bindings,
        args.slot,
        args.generated_at,
        args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
