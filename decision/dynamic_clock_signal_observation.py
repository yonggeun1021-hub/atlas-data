#!/usr/bin/env python3
"""P8-03 Dynamic Clock candidate -> signal-observation adapter.

This module connects real, independently validated Dynamic Clock review
candidates to the existing READY != ENTRY / Signal != Order boundary.  A
Dynamic Clock trigger is represented only as a ``PRESENT`` observation.  It
never supplies READY lineage, never creates an entry trigger, and never
creates an order.

The BTC -> CRYPTO projection is only a vocabulary adapter because the target
boundary contract does not have a separate BTC market token.  It is not a
security-identity or account-scope mapping; the source market and subject are
preserved in every lineage reference and the boundary subject id is a
content-addressed opaque id.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/dynamic_clock_signal_observation_contract.json"
KST = dt.timezone(dt.timedelta(hours=9), name="Asia/Seoul")


class DynamicClockSignalObservationError(ValueError):
    """Fail-closed adapter contract violation."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    if spec is None or spec.loader is None:
        raise DynamicClockSignalObservationError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REVIEW_CANDIDATE = _load_module(
    "atlas_p8_03_review_candidate", "clock/review_candidate.py"
)
ACTION_BOUNDARY = _load_module(
    "atlas_p8_03_ready_signal_order_boundary",
    "decision/ready_signal_order_boundary.py",
)


def _validate_contract(contract: object) -> dict:
    if not isinstance(contract, dict):
        raise DynamicClockSignalObservationError("CONTRACT_NOT_OBJECT")
    if set(contract) != {
        "schema_version", "contract_version", "source_markets",
        "boundary_markets", "market_vocabulary_projection", "ready_status",
        "signal_status", "authority",
    }:
        raise DynamicClockSignalObservationError("CONTRACT_FIELDS_MISMATCH")
    if contract.get("schema_version") != 1:
        raise DynamicClockSignalObservationError("CONTRACT_SCHEMA_MISMATCH")
    if contract.get("contract_version") != "dynamic_clock_signal_observation/1":
        raise DynamicClockSignalObservationError("CONTRACT_VERSION_MISMATCH")
    if contract.get("source_markets") != ["BTC", "CRYPTO", "KOREA"]:
        raise DynamicClockSignalObservationError("SOURCE_MARKETS_MISMATCH")
    if contract.get("boundary_markets") != ["CRYPTO", "KOREA"]:
        raise DynamicClockSignalObservationError("BOUNDARY_MARKETS_MISMATCH")
    expected_projection = {
        "BTC": {
            "boundary_market": "CRYPTO",
            "status": "PRESENTATION_VOCABULARY_ONLY_NOT_SECURITY_IDENTITY",
        },
        "CRYPTO": {
            "boundary_market": "CRYPTO",
            "status": "EXACT_VOCABULARY_MATCH",
        },
        "KOREA": {
            "boundary_market": "KOREA",
            "status": "EXACT_VOCABULARY_MATCH",
        },
    }
    if contract.get("market_vocabulary_projection") != expected_projection:
        raise DynamicClockSignalObservationError("MARKET_PROJECTION_MISMATCH")
    if contract.get("ready_status") != "NOT_EVALUATED":
        raise DynamicClockSignalObservationError("READY_STATUS_MUST_BE_NOT_EVALUATED")
    if contract.get("signal_status") != "PRESENT":
        raise DynamicClockSignalObservationError("SIGNAL_STATUS_MISMATCH")
    authority = contract.get("authority")
    if not isinstance(authority, dict):
        raise DynamicClockSignalObservationError("AUTHORITY_INVALID")
    if authority.get("dynamic_review_trigger_observation_only") is not True:
        raise DynamicClockSignalObservationError("OBSERVATION_AUTHORITY_MISSING")
    if any(
        value is not False
        for key, value in authority.items()
        if key != "dynamic_review_trigger_observation_only"
    ):
        raise DynamicClockSignalObservationError("AUTHORITY_EXPANDED")
    return copy.deepcopy(contract)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DynamicClockSignalObservationError("CONTRACT_READ_FAILED") from exc
    return _validate_contract(value)


def _utc(value: object) -> str:
    if not isinstance(value, str):
        raise DynamicClockSignalObservationError("AS_OF_UTC_INVALID")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise DynamicClockSignalObservationError("AS_OF_UTC_INVALID") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise DynamicClockSignalObservationError("AS_OF_UTC_INVALID")
    return value


def _validate_report(report: object, contract: dict, as_of_utc: str) -> list[dict]:
    if not isinstance(report, dict) or set(report) != {
        "wbs_item", "report_asof_evidence_date", "decision_date", "mode",
        "operational_evaluation", "repo_history_starts_at",
        "policy_approval_status", "policy_version", "authority_note", "by_market",
    }:
        raise DynamicClockSignalObservationError("REPORT_FIELDS_MISMATCH")
    by_market = report.get("by_market")
    if not isinstance(by_market, dict) or list(by_market) != contract["source_markets"]:
        # The exact market set matters, not insertion order.  Sorting below
        # is deterministic; this comparison separately rejects additions.
        if not isinstance(by_market, dict) or set(by_market) != set(contract["source_markets"]):
            raise DynamicClockSignalObservationError("REPORT_MARKETS_MISMATCH")
    # Dynamic Clock decision_date is a KST operational date, while the
    # boundary timestamp is canonically stored in UTC.  Comparing the raw UTC
    # calendar date rejects every valid run between 00:00 and 08:59 KST.
    as_of_date = (
        dt.datetime.strptime(as_of_utc, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=dt.timezone.utc)
        .astimezone(KST)
        .date()
    )
    try:
        decision_date = dt.date.fromisoformat(report["decision_date"])
    except (TypeError, ValueError) as exc:
        raise DynamicClockSignalObservationError("REPORT_DECISION_DATE_INVALID") from exc
    if decision_date > as_of_date:
        raise DynamicClockSignalObservationError("REPORT_DECISION_AFTER_BOUNDARY_AS_OF")
    rows = []
    for market in contract["source_markets"]:
        market_result = by_market[market]
        if not isinstance(market_result, dict) or market_result.get("market") != market:
            raise DynamicClockSignalObservationError(f"MARKET_RESULT_INVALID:{market}")
        queue = market_result.get("review_queue")
        if not isinstance(queue, list):
            raise DynamicClockSignalObservationError(f"REVIEW_QUEUE_INVALID:{market}")
        for candidate in queue:
            checked = REVIEW_CANDIDATE.validate_review_candidate(candidate)
            if checked.get("market") != market:
                raise DynamicClockSignalObservationError("CANDIDATE_MARKET_MISMATCH")
            if checked.get("decision_at") != report["decision_date"]:
                raise DynamicClockSignalObservationError(
                    "CANDIDATE_REPORT_DECISION_DATE_MISMATCH"
                )
            trigger_types = checked.get("trigger_types")
            if not isinstance(trigger_types, list) or not trigger_types:
                raise DynamicClockSignalObservationError("CANDIDATE_TRIGGER_TYPES_MISSING")
            rows.append(checked)
    keys = [(row["market"], row["subject"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise DynamicClockSignalObservationError("CANDIDATE_SUBJECT_DUPLICATE")
    return rows


def _opaque_subject_id(source_market: str, source_subject: str) -> str:
    digest = payload_sha256({"market": source_market, "subject": source_subject})
    return f"DCLK.{source_market}.{digest[:24].upper()}"


def _signal_id(candidate: dict) -> str:
    digest = payload_sha256({
        "market": candidate["market"],
        "subject": candidate["subject"],
        "trigger_types": candidate["trigger_types"],
    })
    return f"DCLK.SIGNAL.{digest[:32].upper()}"


def build_packet(
    report: dict,
    as_of_utc: str,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    packet = _derive_packet(report, as_of_utc, contract)
    return validate_packet(packet, report, contract)


def validate_packet(packet: dict, report: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    if not isinstance(packet, dict):
        raise DynamicClockSignalObservationError("OUTPUT_NOT_OBJECT")
    unsigned = copy.deepcopy(packet)
    digest = unsigned.pop("packet_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise DynamicClockSignalObservationError("OUTPUT_SHA_MISMATCH")
    rebuilt = _derive_packet(report, packet.get("as_of_utc"), contract)
    if packet != rebuilt:
        raise DynamicClockSignalObservationError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def _derive_packet(report: dict, as_of_utc: str, contract: dict) -> dict:
    """Internal non-recursive builder used only by ``validate_packet``."""
    as_of_utc = _utc(as_of_utc)
    candidates = _validate_report(report, contract, as_of_utc)
    report_sha = payload_sha256(report)
    rows = []
    for candidate in sorted(candidates, key=lambda row: (row["market"], row["subject"])):
        source_market = candidate["market"]
        projection = contract["market_vocabulary_projection"][source_market]
        source_subject = candidate["subject"]
        rows.append({
            "source_market": source_market,
            "source_subject": source_subject,
            "source_candidate_record_hash": candidate["record_hash"],
            "source_trigger_types": copy.deepcopy(candidate["trigger_types"]),
            "source_tier_observed_not_used_for_authority": candidate["tier"],
            "boundary_subject_id": _opaque_subject_id(source_market, source_subject),
            "boundary_market": projection["boundary_market"],
            "market_projection_status": projection["status"],
            "ready_status": contract["ready_status"],
            "signal_status": contract["signal_status"],
            "signal_id": _signal_id(candidate),
            "signal_source_ref": f"DYNAMIC_CLOCK:{report_sha}:{source_market}:{source_subject}",
            "signal_source_sha256": candidate["record_hash"],
        })
    packet = {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "as_of_utc": as_of_utc,
        "source_report_sha256": report_sha,
        "subject_count": len(rows),
        "subjects": rows,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def build_boundary_input(
    report: dict,
    as_of_utc: str,
    contract: dict | None = None,
    boundary_contract: dict | None = None,
) -> tuple[dict, dict]:
    """Return ``(adapter_packet, ready_signal_boundary_input)``.

    The boundary input deliberately contains no READY lineage.  Candidate
    tier, confirmation count, and price state cannot influence readiness,
    entry, sizing, or order fields because those fields do not exist here.
    """
    adapter = build_packet(report, as_of_utc, contract)
    ready_contract = ACTION_BOUNDARY.load_contract()
    if boundary_contract is not None and boundary_contract != ready_contract:
        raise DynamicClockSignalObservationError("BOUNDARY_CONTRACT_OVERRIDE_FORBIDDEN")
    subjects = sorted([
        {
            "subject_id": row["boundary_subject_id"],
            "market": row["boundary_market"],
            "ready_status": "NOT_EVALUATED",
            "ready_source_ref": None,
            "ready_source_sha256": None,
            "signal_status": "PRESENT",
            "signal_id": row["signal_id"],
            "signal_source_ref": row["signal_source_ref"],
            "signal_source_sha256": row["signal_source_sha256"],
        }
        for row in adapter["subjects"]
    ], key=lambda row: row["subject_id"])
    value = {
        "schema_version": ready_contract["input_schema_version"],
        "contract_version": ready_contract["contract_version"],
        "packet_id": f"dynamic-clock-signal-observation-{adapter['packet_sha256'][:24]}",
        "as_of_utc": adapter["as_of_utc"],
        "subjects": subjects,
        "authority": copy.deepcopy(ready_contract["input_authority"]),
    }
    value["packet_sha256"] = ACTION_BOUNDARY.payload_sha256(value)
    # Execute the real downstream validator now, not only in the caller.
    ACTION_BOUNDARY.build_packet(value, ready_contract)
    return adapter, value
