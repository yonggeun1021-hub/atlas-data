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
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
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
DYNAMIC_SIGNAL = _load_module(
    "atlas_rotation_discovery_dynamic_signal",
    ROOT / "decision" / "dynamic_clock_signal_observation.py",
)
WILDCARD_INTAKE = _load_module(
    "atlas_wildcard_operational_intake",
    ROOT / "discovery" / "wildcard_operational_intake.py",
)
DART_OBSERVATION = _load_module(
    "atlas_dart_event_observation",
    ROOT / "discovery" / "dart_event_observation.py",
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
        "contract_version": "rotation_discovery_briefing/4",
        "output_schema_version": "rotation_discovery_briefing_packet/4",
        "rotation_source_contract": "rotation_state_ledger/1",
        "discovery_source_contract": "event_discovery_case/2",
        "signal_observation_source_contract": "dynamic_clock_signal_observation/1",
        "wildcard_source_contract": "wildcard_operational_intake/v1",
        "dart_observation_source_contract": "dart_event_observation_packet/1",
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


def _signal_observation_section(dynamic_report: dict | None, generated_at: str, contract: dict) -> dict:
    """Expose real Dynamic Clock subjects without calling them candidates.

    These are signal observations only.  They cannot populate the existing
    ``new_candidates`` or ``existing_candidate_changes`` fields because
    doing so would silently grant the importance/promotion authority that
    P8-05 deliberately does not own.
    """
    if dynamic_report is None:
        return {
            "status": "NOT_AVAILABLE",
            "observation_count": 0,
            "market_counts": {market: 0 for market in ("BTC", "CRYPTO", "KOREA")},
            "tier_counts_diagnostic_only": {
                tier: 0 for tier in ("IMMEDIATE_REVIEW", "WATCH_REVIEW", "OBSERVATION_ONLY")
            },
            "observations": [],
            "source_packet_sha256": None,
        }
    try:
        source = DYNAMIC_SIGNAL.build_packet(dynamic_report, generated_at)
    except DYNAMIC_SIGNAL.DynamicClockSignalObservationError as exc:
        raise RotationDiscoveryBriefingError(
            f"DYNAMIC_SIGNAL_INPUT_INVALID:{exc}"
        ) from exc
    if source["contract_version"] != contract["signal_observation_source_contract"]:
        raise RotationDiscoveryBriefingError("DYNAMIC_SIGNAL_CONTRACT_INVALID")
    rows = []
    market_counts = {market: 0 for market in ("BTC", "CRYPTO", "KOREA")}
    tier_counts = {
        tier: 0 for tier in ("IMMEDIATE_REVIEW", "WATCH_REVIEW", "OBSERVATION_ONLY")
    }
    for row in source["subjects"]:
        tier = row["source_tier_observed_not_used_for_authority"]
        if tier not in tier_counts:
            raise RotationDiscoveryBriefingError("DYNAMIC_SIGNAL_TIER_INVALID")
        market_counts[row["source_market"]] += 1
        tier_counts[tier] += 1
        rows.append({
            "boundary_subject_id": row["boundary_subject_id"],
            "source_market": row["source_market"],
            "source_subject": row["source_subject"],
            "trigger_types": copy.deepcopy(row["source_trigger_types"]),
            "tier_diagnostic_only": tier,
            "signal_id": row["signal_id"],
            "candidate_record_sha256": row["source_candidate_record_hash"],
            "market_projection_status": row["market_projection_status"],
            "ready_status": "NOT_EVALUATED",
            "promotion_status": "PROMOTION_NOT_AUTHORIZED",
            "action": None,
        })
    return {
        "status": "SIGNAL_OBSERVATIONS_PRESENT_NO_PROMOTION_AUTHORITY",
        "observation_count": len(rows),
        "market_counts": market_counts,
        "tier_counts_diagnostic_only": tier_counts,
        "observations": rows,
        "source_packet_sha256": source["packet_sha256"],
    }


def load_operational_wildcard_envelopes(
    generated_at: str, root: Path = ROOT
) -> list[dict]:
    """Load the latest committed envelope revision for each submission path.

    The loader never invents a nomination and never carries a future envelope
    into a historical briefing.  Every eligible envelope is independently
    re-derived from its immutable source commit before it can be returned.
    """
    generated = _utc(generated_at, "WILDCARD_GENERATED_AT_INVALID")
    root = Path(root)
    publication_root = root / "evidence" / "operational" / "wildcard_discovery"
    if not publication_root.exists():
        return []
    latest_by_submission: dict[str, tuple[dt.datetime, str, dict]] = {}
    for path in sorted(publication_root.glob("*/*.json")):
        raw = _read_json(path)
        decision_value = raw.get("decision_at_utc") if isinstance(raw, dict) else None
        decision = _utc(decision_value, "WILDCARD_DECISION_AT_INVALID")
        if decision > generated:
            continue
        try:
            envelope = WILDCARD_INTAKE.validate_envelope(raw, root)
        except WILDCARD_INTAKE.WildcardOperationalIntakeError as exc:
            raise RotationDiscoveryBriefingError(
                f"WILDCARD_ENVELOPE_INVALID:{path}:{exc}"
            ) from exc
        if envelope["contract_version"] != contract_value(root)["wildcard_source_contract"]:
            raise RotationDiscoveryBriefingError("WILDCARD_CONTRACT_INVALID")
        if WILDCARD_INTAKE.publication_path(envelope, root).resolve() != path.resolve():
            raise RotationDiscoveryBriefingError(
                f"WILDCARD_PUBLICATION_LOCATOR_INVALID:{path}"
            )
        digest = envelope["payload_sha256"]
        for lineage in envelope["submission_lineage"]:
            key = lineage["path"]
            previous = latest_by_submission.get(key)
            if previous is not None and previous[0] == decision and previous[1] != digest:
                raise RotationDiscoveryBriefingError(
                    f"WILDCARD_ENVELOPE_REVISION_AMBIGUOUS:{key}:{decision_value}"
                )
            if previous is None or (decision, digest) > (previous[0], previous[1]):
                latest_by_submission[key] = (decision, digest, envelope)
    unique = {item[1]: item[2] for item in latest_by_submission.values()}
    return [unique[key] for key in sorted(unique)]


def contract_value(root: Path = ROOT) -> dict:
    """Load this read-model contract from an explicit repository root."""
    return load_contract(Path(root) / "config" / CONTRACT_PATH.name)


def _wildcard_observation_section(
    envelopes: list[dict] | None,
    generated: dt.datetime,
    contract: dict,
    root: Path,
) -> dict:
    envelopes = [] if envelopes is None else copy.deepcopy(envelopes)
    if not isinstance(envelopes, list):
        raise RotationDiscoveryBriefingError("WILDCARD_ENVELOPES_INVALID")
    checked_envelopes = []
    observations = []
    seen_envelope_sha = set()
    seen_submission_paths = set()
    seen_observation_ids = set()
    for envelope in envelopes:
        try:
            checked = WILDCARD_INTAKE.validate_envelope(envelope, root)
        except WILDCARD_INTAKE.WildcardOperationalIntakeError as exc:
            raise RotationDiscoveryBriefingError(
                f"WILDCARD_ENVELOPE_INVALID:{exc}"
            ) from exc
        if checked["contract_version"] != contract["wildcard_source_contract"]:
            raise RotationDiscoveryBriefingError("WILDCARD_CONTRACT_INVALID")
        envelope_sha = checked["payload_sha256"]
        if envelope_sha in seen_envelope_sha:
            raise RotationDiscoveryBriefingError("WILDCARD_ENVELOPE_DUPLICATE")
        seen_envelope_sha.add(envelope_sha)
        paths = {item["path"] for item in checked["submission_lineage"]}
        if paths & seen_submission_paths:
            raise RotationDiscoveryBriefingError("WILDCARD_SUBMISSION_REVISION_DUPLICATE")
        seen_submission_paths.update(paths)
        decision = _utc(checked["decision_at_utc"], "WILDCARD_DECISION_AT_INVALID")
        if decision > generated:
            raise RotationDiscoveryBriefingError("WILDCARD_ENVELOPE_FROM_FUTURE")
        checked_envelopes.append(checked)
        source_commit = checked["source_commit"]
        for case in checked["packet"]["cases"]:
            observation_id = ("CASE", case["case_id"])
            if observation_id in seen_observation_ids:
                raise RotationDiscoveryBriefingError("WILDCARD_OBSERVATION_DUPLICATE")
            seen_observation_ids.add(observation_id)
            observations.append({
                "observation_type": "EVIDENCE_LINKED_CASE",
                "case_id": case["case_id"],
                "submission_id": None,
                "market": case["market"],
                "asset_id": case["asset_id"],
                "subject": case["subject"],
                "observation_date": case["observation_date"],
                "evidence_status": case["evidence_status"],
                "strength_status": case["strength_status"],
                "importance_status": case["importance"],
                "candidate_eligible": False,
                "ready_status": "NOT_EVALUATED",
                "promotion_status": "PROMOTION_NOT_AUTHORIZED",
                "action": None,
                "source_commit": source_commit,
                "source_envelope_sha256": envelope_sha,
                "decision_at_utc": checked["decision_at_utc"],
            })
        for submission in checked["packet"]["submissions"]:
            if submission["case_created"]:
                continue
            observation_id = ("PENDING", submission["submission_id"])
            if observation_id in seen_observation_ids:
                raise RotationDiscoveryBriefingError("WILDCARD_OBSERVATION_DUPLICATE")
            seen_observation_ids.add(observation_id)
            observations.append({
                "observation_type": "PENDING_SUBMISSION",
                "case_id": None,
                "submission_id": submission["submission_id"],
                "market": submission["market"],
                "asset_id": submission["asset_id"],
                "subject": submission["subject"],
                "observation_date": submission["observed_on"],
                "evidence_status": submission["pending_reason"],
                "strength_status": "UNRATIFIED",
                "importance_status": "UNRATIFIED",
                "candidate_eligible": False,
                "ready_status": "NOT_EVALUATED",
                "promotion_status": "PROMOTION_NOT_AUTHORIZED",
                "action": None,
                "source_commit": source_commit,
                "source_envelope_sha256": envelope_sha,
                "decision_at_utc": checked["decision_at_utc"],
            })
    checked_envelopes.sort(key=lambda value: (value["decision_at_utc"], value["payload_sha256"]))
    observations.sort(key=lambda value: (
        value["market"], value["asset_id"], value["observation_type"],
        value["case_id"] or value["submission_id"], value["source_envelope_sha256"],
    ))
    return {
        "status": (
            "VERIFIED_WILDCARD_OBSERVATIONS_PRESENT_NO_PROMOTION_AUTHORITY"
            if observations else "NOT_AVAILABLE"
        ),
        "envelope_count": len(checked_envelopes),
        "observation_count": len(observations),
        "case_count": sum(row["observation_type"] == "EVIDENCE_LINKED_CASE" for row in observations),
        "pending_count": sum(row["observation_type"] == "PENDING_SUBMISSION" for row in observations),
        "observations": observations,
        "source_envelopes": checked_envelopes,
    }


def _repo_source_path(root: Path, value: object, code: str) -> Path:
    if not isinstance(value, str) or not value or value.startswith("external_fixture/"):
        raise RotationDiscoveryBriefingError(code)
    candidate = (Path(root) / value).resolve()
    try:
        candidate.relative_to(Path(root).resolve())
    except ValueError as exc:
        raise RotationDiscoveryBriefingError(code) from exc
    return candidate


def _validated_dart_observation_packet(packet: dict, root: Path) -> dict:
    if not isinstance(packet, dict):
        raise RotationDiscoveryBriefingError("DART_OBSERVATION_PACKET_INVALID")
    lineage = packet.get("lineage")
    if not isinstance(lineage, dict):
        raise RotationDiscoveryBriefingError("DART_OBSERVATION_LINEAGE_INVALID")
    source_path = _repo_source_path(
        root, lineage.get("source_path"), "DART_OBSERVATION_SOURCE_PATH_INVALID"
    )
    content_path = _repo_source_path(
        root,
        lineage.get("content_run_path"),
        "DART_OBSERVATION_CONTENT_PATH_INVALID",
    )
    try:
        return DART_OBSERVATION.validate_packet(
            copy.deepcopy(packet),
            source_path=source_path,
            content_path=content_path,
            data_root=Path(root) / "data",
        )
    except DART_OBSERVATION.DartEventObservationError as exc:
        raise RotationDiscoveryBriefingError(
            f"DART_OBSERVATION_PACKET_INVALID:{exc}"
        ) from exc


def load_operational_dart_observation_packet(
    generated_at: str, root: Path = ROOT
) -> dict | None:
    """Return the latest PIT-eligible immutable DART observation packet."""
    generated = _utc(generated_at, "DART_OBSERVATION_GENERATED_AT_INVALID")
    root = Path(root)
    publication_root = root / "data" / "observations" / "dart_event_observations"
    if not publication_root.exists():
        return None
    eligible = []
    for path in sorted(publication_root.glob("*/*.json")):
        packet = _read_json(path)
        if packet.get("schema_version") != "dart_event_observation_packet/1":
            raise RotationDiscoveryBriefingError("DART_OBSERVATION_PACKET_INVALID")
        declared_sha = packet.get("packet_sha256")
        if not isinstance(declared_sha, str) or SHA256_RE.fullmatch(declared_sha) is None:
            raise RotationDiscoveryBriefingError("DART_OBSERVATION_PACKET_SHA_INVALID")
        unsigned = copy.deepcopy(packet)
        unsigned.pop("packet_sha256", None)
        if payload_sha256(unsigned) != declared_sha:
            raise RotationDiscoveryBriefingError("DART_OBSERVATION_PACKET_SHA_MISMATCH")
        decision = _utc(
            packet.get("decision_at"), "DART_OBSERVATION_DECISION_AT_INVALID"
        )
        if decision > generated:
            continue
        source_date = _date(
            packet.get("source_date"), "DART_OBSERVATION_SOURCE_DATE_INVALID"
        )
        if source_date > generated.astimezone(
            dt.timezone(dt.timedelta(hours=9))
        ).date():
            raise RotationDiscoveryBriefingError("DART_OBSERVATION_FROM_FUTURE")
        expected = (
            publication_root
            / packet["source_date"]
            / f"packet-{declared_sha[:16]}.json"
        )
        if path.resolve() != expected.resolve():
            raise RotationDiscoveryBriefingError(
                "DART_OBSERVATION_PUBLICATION_LOCATOR_INVALID"
            )
        eligible.append((decision, declared_sha, packet))
    if not eligible:
        return None
    eligible.sort(key=lambda item: (item[0], item[1]))
    latest = eligible[-1]
    if any(
        item[0] == latest[0] and item[1] != latest[1]
        for item in eligible[:-1]
    ):
        raise RotationDiscoveryBriefingError("DART_OBSERVATION_REVISION_AMBIGUOUS")
    return _validated_dart_observation_packet(latest[2], root)


def _dart_observation_section(
    packet: dict | None, generated: dt.datetime, root: Path
) -> dict:
    if packet is None:
        return {
            "status": "NOT_AVAILABLE",
            "observation_count": 0,
            "raw_bytes_verified_count": 0,
            "metadata_only_count": 0,
            "observations": [],
            "source_packet": None,
            "source_packet_sha256": None,
        }
    checked = _validated_dart_observation_packet(packet, root)
    if checked["schema_version"] != "dart_event_observation_packet/1":
        raise RotationDiscoveryBriefingError("DART_OBSERVATION_CONTRACT_INVALID")
    if _utc(checked["decision_at"], "DART_OBSERVATION_DECISION_AT_INVALID") > generated:
        raise RotationDiscoveryBriefingError("DART_OBSERVATION_FROM_FUTURE")
    observations = []
    for row in checked["observations"]:
        if (
            row.get("event_type") is not None
            or row.get("direction") is not None
            or row.get("importance") is not None
            or row.get("status") != "OBSERVED_ESCALATION_BLOCKED"
        ):
            raise RotationDiscoveryBriefingError("DART_OBSERVATION_AUTHORITY_OPENED")
        observations.append({
            "observation_id": row["observation_id"],
            "subject_id": row["subject_id"],
            "subject_name": row["subject_name"],
            "filing_date": row["filing_date"],
            "filing_title": row["filing_title"],
            "filing_url": row["filing_url"],
            "time_precision": row["time_precision"],
            "evidence_status": row["evidence"]["status"],
            "evidence_available_at": row["evidence"]["available_at"],
            "blocked_reasons": copy.deepcopy(row["blocked_reasons"]),
            "event_type": None,
            "direction": None,
            "importance": None,
            "ready_status": "NOT_EVALUATED",
            "promotion_status": "PROMOTION_NOT_AUTHORIZED",
            "action": None,
        })
    return {
        "status": "DART_FILING_OBSERVATIONS_PRESENT_ESCALATION_BLOCKED",
        "observation_count": len(observations),
        "raw_bytes_verified_count": checked["summary"]["raw_bytes_verified_count"],
        "metadata_only_count": checked["summary"]["metadata_only_count"],
        "observations": observations,
        "source_packet": copy.deepcopy(checked),
        "source_packet_sha256": checked["packet_sha256"],
    }


def build_briefing(
    rotation_ledger: dict,
    discovery_records: list[dict],
    evidence_bindings: dict,
    slot: str,
    generated_at: str,
    contract: dict | None = None,
    dynamic_report: dict | None = None,
    wildcard_envelopes: list[dict] | None = None,
    wildcard_root: Path = ROOT,
    dart_observation_packet: dict | None = None,
    dart_root: Path = ROOT,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if slot not in contract["slots"]:
        raise RotationDiscoveryBriefingError(f"SLOT_INVALID:{slot}")
    generated = _utc(generated_at, "GENERATED_AT_INVALID")
    rotation = _rotation_section(rotation_ledger, generated, contract)
    discovery = _discovery_section(
        discovery_records, evidence_bindings, generated, contract
    )
    signal_observations = _signal_observation_section(
        dynamic_report, generated_at, contract
    )
    wildcard_observations = _wildcard_observation_section(
        wildcard_envelopes, generated, contract, Path(wildcard_root)
    )
    dart_observations = _dart_observation_section(
        dart_observation_packet, generated, Path(dart_root)
    )
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "slot": slot,
        "generated_at": generated_at,
        "status": contract["status"],
        "rotation": rotation,
        "discovery": discovery,
        "signal_observations": signal_observations,
        "wildcard_observations": wildcard_observations,
        "dart_observations": dart_observations,
        "summary": {
            "rotation_change_count": rotation["latest_change_count"],
            "discovery_case_count": discovery["case_count"],
            "new_candidate_count": 0,
            "existing_candidate_change_count": 0,
            "signal_observation_count": signal_observations["observation_count"],
            "wildcard_observation_count": wildcard_observations["observation_count"],
            "dart_observation_count": dart_observations["observation_count"],
            "ready_count": 0,
            "entry_trigger_count": 0,
            "ranked_candidate": None,
            "action": None,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "DISCOVERY_IMPORTANCE_POLICY_UNRATIFIED",
            "DISCOVERY_INTERPRETATION_NOT_AUTHORIZED",
            "DART_OBSERVATION_ESCALATION_BLOCKED",
            "CANDIDATE_STAGE_PROMOTION_NOT_AUTHORIZED",
            "CANDIDATE_RANKING_NOT_AUTHORIZED",
            "ACTION_GENERATION_NOT_AUTHORIZED",
            "PRODUCTION_WIRING_NOT_IMPLEMENTED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_briefing(
        packet,
        contract,
        wildcard_root=wildcard_root,
        dart_root=dart_root,
    )


def validate_briefing(
    packet: dict,
    contract: dict | None = None,
    wildcard_root: Path = ROOT,
    dart_root: Path = ROOT,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "slot", "generated_at", "status",
        "rotation", "discovery", "signal_observations", "wildcard_observations",
        "dart_observations",
        "summary", "authority", "unresolved_boundaries",
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
    signal_observations = packet.get("signal_observations")
    wildcard_observations = packet.get("wildcard_observations")
    dart_observations = packet.get("dart_observations")
    if (
        not isinstance(rotation, dict)
        or not isinstance(discovery, dict)
        or not isinstance(signal_observations, dict)
        or not isinstance(wildcard_observations, dict)
        or not isinstance(dart_observations, dict)
    ):
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
    signal_fields = {
        "status", "observation_count", "market_counts",
        "tier_counts_diagnostic_only", "observations", "source_packet_sha256",
    }
    if set(signal_observations) != signal_fields:
        raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_SECTION_FIELDS_INVALID")
    observations = signal_observations.get("observations")
    if not isinstance(observations, list):
        raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_ROWS_INVALID")
    observation_fields = {
        "boundary_subject_id", "source_market", "source_subject", "trigger_types",
        "tier_diagnostic_only", "signal_id", "candidate_record_sha256",
        "market_projection_status", "ready_status", "promotion_status", "action",
    }
    source_markets = ("BTC", "CRYPTO", "KOREA")
    tiers = ("IMMEDIATE_REVIEW", "WATCH_REVIEW", "OBSERVATION_ONLY")
    derived_market_counts = {market: 0 for market in source_markets}
    derived_tier_counts = {tier: 0 for tier in tiers}
    observation_keys = []
    for row in observations:
        if not isinstance(row, dict) or set(row) != observation_fields:
            raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_ROW_FIELDS_INVALID")
        market = row.get("source_market")
        tier = row.get("tier_diagnostic_only")
        trigger_types = row.get("trigger_types")
        if (
            market not in source_markets
            or tier not in tiers
            or not isinstance(row.get("source_subject"), str)
            or not row["source_subject"]
            or not isinstance(trigger_types, list)
            or not trigger_types
            or trigger_types != sorted(set(trigger_types))
            or row.get("ready_status") != "NOT_EVALUATED"
            or row.get("promotion_status") != "PROMOTION_NOT_AUTHORIZED"
            or row.get("action") is not None
        ):
            raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_ROW_VALUE_INVALID")
        for field in ("candidate_record_sha256",):
            if not isinstance(row.get(field), str) or SHA256_RE.fullmatch(row[field]) is None:
                raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_ROW_SHA_INVALID")
        for field in ("boundary_subject_id", "signal_id", "market_projection_status"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_ROW_VALUE_INVALID")
        derived_market_counts[market] += 1
        derived_tier_counts[tier] += 1
        observation_keys.append((market, row["source_subject"]))
    if observation_keys != sorted(set(observation_keys)):
        raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_ROW_ORDER_INVALID")
    expected_signal_status = (
        "SIGNAL_OBSERVATIONS_PRESENT_NO_PROMOTION_AUTHORITY"
        if observations else "NOT_AVAILABLE"
    )
    if (
        signal_observations.get("status") != expected_signal_status
        or signal_observations.get("observation_count") != len(observations)
        or signal_observations.get("market_counts") != derived_market_counts
        or signal_observations.get("tier_counts_diagnostic_only") != derived_tier_counts
    ):
        raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_SUMMARY_INVALID")
    signal_source_sha = signal_observations.get("source_packet_sha256")
    if observations:
        if not isinstance(signal_source_sha, str) or SHA256_RE.fullmatch(signal_source_sha) is None:
            raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_SOURCE_SHA_INVALID")
    elif signal_source_sha is not None:
        raise RotationDiscoveryBriefingError("BRIEFING_SIGNAL_SOURCE_SHA_INVALID")
    wildcard_source_envelopes = wildcard_observations.get("source_envelopes")
    if not isinstance(wildcard_source_envelopes, list):
        raise RotationDiscoveryBriefingError("BRIEFING_WILDCARD_ENVELOPES_INVALID")
    expected_wildcard = _wildcard_observation_section(
        wildcard_source_envelopes, generated, contract, Path(wildcard_root)
    )
    if wildcard_observations != expected_wildcard:
        raise RotationDiscoveryBriefingError("BRIEFING_WILDCARD_DERIVATION_MISMATCH")
    dart_source_packet = dart_observations.get("source_packet")
    expected_dart = _dart_observation_section(
        dart_source_packet, generated, Path(dart_root)
    )
    if dart_observations != expected_dart:
        raise RotationDiscoveryBriefingError("BRIEFING_DART_DERIVATION_MISMATCH")
    expected_summary = {
        "rotation_change_count": len(rotation["latest_changes"]),
        "discovery_case_count": len(discovery["cases"]),
        "new_candidate_count": 0,
        "existing_candidate_change_count": 0,
        "signal_observation_count": len(observations),
        "wildcard_observation_count": wildcard_observations["observation_count"],
        "dart_observation_count": dart_observations["observation_count"],
        "ready_count": 0,
        "entry_trigger_count": 0,
        "ranked_candidate": None,
        "action": None,
    }
    if packet.get("summary") != expected_summary:
        raise RotationDiscoveryBriefingError("BRIEFING_SUMMARY_INVALID")
    expected_boundaries = [
        "DISCOVERY_IMPORTANCE_POLICY_UNRATIFIED",
        "DISCOVERY_INTERPRETATION_NOT_AUTHORIZED",
        "DART_OBSERVATION_ESCALATION_BLOCKED",
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
