#!/usr/bin/env python3
"""P9-02 externally ratified important filing/news event detector."""
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
CONTRACT_PATH = ROOT / "config" / "important_event_detector_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
# Exchange symbols can legitimately be one or two characters (for example,
# ``A`` and ``MU``).  They are identifiers, not the generic contract tokens
# above, so validating them with TOKEN_RE would reject a valid observation and
# fail the whole batch.
SUBJECT_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ImportantEventDetectorError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportantEventDetectorError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 2,
        "contract_version": "important_event_detector/2",
        "policy_schema_version": "important_event_policy/2",
        "input_schema_version": "important_event_observation_batch/2",
        "output_schema_version": "important_event_detection_packet/2",
        "markets": ["US", "KOREA", "CRYPTO"],
        "source_kinds": ["SEC_EDGAR", "DART_OPEN_API", "OFFICIAL_NEWS"],
        "importance_levels": ["IMPORTANT", "ROUTINE"],
        "detection_statuses": ["ESCALATED", "ROUTINE", "UNASSESSED", "BLOCKED"],
        "timing_statuses": ["ON_TIME", "LATE", "NOT_APPLICABLE"],
        "repository_default_policy": "ABSENT",
        "matching_policy": "EXACT_SOURCE_MARKET_EVENT_TYPE",
        "escalation_policy": "RATIFIED_IMPORTANT_AND_CONFIRMED_ONLY",
        "notification_policy": "NOT_SENT_BY_DETECTOR",
        "input_authority": {
            "normalized_event_observation_only": True,
            "importance_classification_authorized": False,
            "notification_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "ratified_policy_event_detection_only": True,
            "event_type_inference_authorized": False,
            "importance_policy_creation_authorized": False,
            "candidate_promotion_authorized": False,
            "notification_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ImportantEventDetectorError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ImportantEventDetectorError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ImportantEventDetectorError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise ImportantEventDetectorError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ImportantEventDetectorError(code)
    return parsed


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ImportantEventDetectorError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ImportantEventDetectorError(code) from exc
    if parsed.isoformat() != value:
        raise ImportantEventDetectorError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ImportantEventDetectorError(code)
    return value


def _subject_id(value, code: str) -> str:
    if not isinstance(value, str) or SUBJECT_ID_RE.fullmatch(value) is None:
        raise ImportantEventDetectorError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ImportantEventDetectorError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ImportantEventDetectorError(code)
    return value


def _positive_int(value, code: str) -> int:
    if type(value) is not int or value < 1:
        raise ImportantEventDetectorError(code)
    return value


def _validate_policy(value: dict, detected_at: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_id", "status", "ratified_by",
        "ratified_at", "effective_from", "effective_to", "rules", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ImportantEventDetectorError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != {
            "importance_policy_only": True,
            "event_type_inference_authorized": False,
            "notification_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
    ):
        raise ImportantEventDetectorError("POLICY_IDENTITY_INVALID")
    policy_id = _token(value.get("policy_id"), "POLICY_ID_INVALID")
    ratified = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    start = _date(value.get("effective_from"), "POLICY_EFFECTIVE_FROM_INVALID")
    end = None if value.get("effective_to") is None else _date(
        value["effective_to"], "POLICY_EFFECTIVE_TO_INVALID"
    )
    if ratified > detected_at:
        raise ImportantEventDetectorError("POLICY_RATIFIED_AFTER_DETECTION")
    if ratified.date() > start or (end is not None and end < start):
        raise ImportantEventDetectorError("POLICY_INTERVAL_INVALID")
    if detected_at.date() < start or (end is not None and detected_at.date() > end):
        raise ImportantEventDetectorError("POLICY_NOT_EFFECTIVE")
    digest = _sha(value.get("packet_sha256"), "POLICY_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise ImportantEventDetectorError("POLICY_SHA_MISMATCH")
    raw_rules = value.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ImportantEventDetectorError("POLICY_RULES_EMPTY")
    rule_fields = {
        "rule_id", "source_kind", "market", "event_type", "importance",
        "max_detection_delay_seconds", "policy_basis_ref", "policy_basis_sha256",
    }
    rules = []
    for index, row in enumerate(raw_rules):
        context = f"rule:{index}"
        if not isinstance(row, dict) or set(row) != rule_fields:
            raise ImportantEventDetectorError(f"POLICY_RULE_FIELDS_MISMATCH:{context}")
        if (
            row.get("source_kind") not in contract["source_kinds"]
            or row.get("market") not in contract["markets"]
            or row.get("importance") not in contract["importance_levels"]
        ):
            raise ImportantEventDetectorError(f"POLICY_RULE_IDENTITY_INVALID:{context}")
        rules.append({
            "rule_id": _token(row.get("rule_id"), f"RULE_ID_INVALID:{context}"),
            "source_kind": row["source_kind"],
            "market": row["market"],
            "event_type": _token(row.get("event_type"), f"EVENT_TYPE_INVALID:{context}"),
            "importance": row["importance"],
            "max_detection_delay_seconds": _positive_int(
                row.get("max_detection_delay_seconds"), f"DETECTION_DELAY_INVALID:{context}"
            ),
            "policy_basis_ref": _text(row.get("policy_basis_ref"), f"POLICY_BASIS_REF_INVALID:{context}"),
            "policy_basis_sha256": _sha(row.get("policy_basis_sha256"), f"POLICY_BASIS_SHA_INVALID:{context}"),
        })
    rules.sort(key=lambda row: (contract["source_kinds"].index(row["source_kind"]), contract["markets"].index(row["market"]), row["event_type"]))
    ids = [row["rule_id"] for row in rules]
    keys = [(row["source_kind"], row["market"], row["event_type"]) for row in rules]
    if len(ids) != len(set(ids)):
        raise ImportantEventDetectorError("POLICY_RULE_ID_DUPLICATE")
    if len(keys) != len(set(keys)):
        raise ImportantEventDetectorError("POLICY_RULE_MATCH_DUPLICATE")
    return {"policy_id": policy_id, "packet_sha256": digest, "rules": rules}


def _validate_events(value: dict, detected_at: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at", "events",
        "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ImportantEventDetectorError("EVENT_BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise ImportantEventDetectorError("EVENT_BATCH_IDENTITY_INVALID")
    batch_id = _token(value.get("batch_id"), "EVENT_BATCH_ID_INVALID")
    observed = _utc(value.get("observed_at"), "EVENT_BATCH_TIME_INVALID")
    if observed > detected_at:
        raise ImportantEventDetectorError("EVENT_BATCH_FROM_FUTURE")
    digest = _sha(value.get("packet_sha256"), "EVENT_BATCH_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise ImportantEventDetectorError("EVENT_BATCH_SHA_MISMATCH")
    raw = value.get("events")
    if not isinstance(raw, list):
        raise ImportantEventDetectorError("EVENTS_NOT_LIST")
    event_fields = {
        "event_id", "market", "subject_id", "source_kind", "event_type",
        "event_at", "available_at", "received_at", "source_ref", "source_sha256",
        "evidence_status", "blocked_reasons",
    }
    events = []
    for index, row in enumerate(raw):
        context = f"event:{index}"
        if not isinstance(row, dict) or set(row) != event_fields:
            raise ImportantEventDetectorError(f"EVENT_FIELDS_MISMATCH:{context}")
        event_at = _utc(row.get("event_at"), f"EVENT_AT_INVALID:{context}")
        available = _utc(row.get("available_at"), f"AVAILABLE_AT_INVALID:{context}")
        received = _utc(row.get("received_at"), f"RECEIVED_AT_INVALID:{context}")
        if not event_at <= available <= received <= observed:
            raise ImportantEventDetectorError(f"EVENT_TIME_ORDER_INVALID:{context}")
        if (
            row.get("market") not in contract["markets"]
            or row.get("source_kind") not in contract["source_kinds"]
            or row.get("evidence_status") not in {"CONFIRMED", "BLOCKED"}
        ):
            raise ImportantEventDetectorError(f"EVENT_IDENTITY_INVALID:{context}")
        reasons = row.get("blocked_reasons")
        if (
            not isinstance(reasons, list)
            or reasons != sorted(set(reasons))
            or any(not isinstance(item, str) or TOKEN_RE.fullmatch(item) is None for item in reasons)
            or (row["evidence_status"] == "CONFIRMED" and reasons)
            or (row["evidence_status"] == "BLOCKED" and not reasons)
        ):
            raise ImportantEventDetectorError(f"EVENT_BLOCK_REASONS_INVALID:{context}")
        events.append({
            "event_id": _token(row.get("event_id"), f"EVENT_ID_INVALID:{context}"),
            "market": row["market"],
            "subject_id": _subject_id(
                row.get("subject_id"), f"SUBJECT_ID_INVALID:{context}"
            ),
            "source_kind": row["source_kind"],
            "event_type": _token(row.get("event_type"), f"EVENT_TYPE_INVALID:{context}"),
            "event_at": row["event_at"],
            "available_at": row["available_at"],
            "received_at": row["received_at"],
            "source_ref": _text(row.get("source_ref"), f"SOURCE_REF_INVALID:{context}"),
            "source_sha256": _sha(row.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"),
            "evidence_status": row["evidence_status"],
            "blocked_reasons": list(reasons),
        })
    events.sort(key=lambda row: (row["available_at"], row["event_id"]))
    ids = [row["event_id"] for row in events]
    if len(ids) != len(set(ids)):
        raise ImportantEventDetectorError("EVENT_ID_DUPLICATE")
    return {"batch_id": batch_id, "packet_sha256": digest, "events": events}


def _detections(events: list[dict], rules: list[dict], detected_at: str) -> list[dict]:
    rule_by_key = {(row["source_kind"], row["market"], row["event_type"]): row for row in rules}
    detected = _utc(detected_at, "DETECTED_AT_INVALID")
    rows = []
    for event in events:
        rule = rule_by_key.get((event["source_kind"], event["market"], event["event_type"]))
        delay = int((detected - _utc(event["available_at"], "AVAILABLE_AT_INVALID")).total_seconds())
        if delay < 0:
            raise ImportantEventDetectorError(f"DETECTION_BEFORE_AVAILABLE:{event['event_id']}")
        if event["evidence_status"] == "BLOCKED":
            status, timing = "BLOCKED", "NOT_APPLICABLE"
        elif rule is None:
            status, timing = "UNASSESSED", "NOT_APPLICABLE"
        elif rule["importance"] == "ROUTINE":
            status, timing = "ROUTINE", "NOT_APPLICABLE"
        else:
            status = "ESCALATED"
            timing = "ON_TIME" if delay <= rule["max_detection_delay_seconds"] else "LATE"
        rows.append({
            "event_id": event["event_id"],
            "market": event["market"],
            "subject_id": event["subject_id"],
            "source_kind": event["source_kind"],
            "event_type": event["event_type"],
            "evidence_status": event["evidence_status"],
            "matched_rule": copy.deepcopy(rule),
            "importance": None if rule is None else rule["importance"],
            "detection_status": status,
            "detection_delay_seconds": delay,
            "timing_status": timing,
            "notification_status": "NOT_SENT",
            "action": None,
            "order_intent": None,
            "source_event": copy.deepcopy(event),
        })
    return rows


def build_packet(event_batch: dict, policy: dict, detected_at: str, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    detected = _utc(detected_at, "DETECTED_AT_INVALID")
    checked_policy = _validate_policy(policy, detected, contract)
    checked_events = _validate_events(event_batch, detected, contract)
    rows = _detections(checked_events["events"], checked_policy["rules"], detected_at)
    counts = {status: sum(row["detection_status"] == status for row in rows) for status in contract["detection_statuses"]}
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "detected_at": detected_at,
        "status": "IMPORTANT_EVENTS_DETECTED_NOTIFICATION_NOT_SENT",
        "detections": rows,
        "summary": {
            "event_count": len(rows),
            **counts,
            "late_escalation_count": sum(row["detection_status"] == "ESCALATED" and row["timing_status"] == "LATE" for row in rows),
            "notification_sent_count": 0,
            "action_count": 0,
            "order_count": 0,
        },
        "lineage": {
            "event_batch_id": checked_events["batch_id"],
            "event_batch_sha256": checked_events["packet_sha256"],
            "policy_id": checked_policy["policy_id"],
            "policy_sha256": checked_policy["packet_sha256"],
        },
        "source_packets": {
            "EVENT_BATCH": copy.deepcopy(event_batch),
            "POLICY": copy.deepcopy(policy),
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "LIVE_SEC_DART_NEWS_ADAPTERS_NOT_WIRED",
            "REPOSITORY_DEFAULT_IMPORTANCE_POLICY_ABSENT",
            "NOTIFICATION_DELIVERY_NOT_IMPLEMENTED",
            "ACTION_AND_ORDER_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(packet, dict) or set(packet) != {
        "schema_version", "contract_version", "detected_at", "status", "detections",
        "summary", "lineage", "source_packets", "authority",
        "unresolved_boundaries", "packet_sha256",
    }:
        raise ImportantEventDetectorError("PACKET_FIELDS_MISMATCH")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {"EVENT_BATCH", "POLICY"}:
        raise ImportantEventDetectorError("PACKET_SOURCE_FIELDS_MISMATCH")
    event_batch = sources["EVENT_BATCH"]
    policy = sources["POLICY"]
    detected = _utc(packet.get("detected_at"), "DETECTED_AT_INVALID")
    checked_policy = _validate_policy(policy, detected, contract)
    checked_events = _validate_events(event_batch, detected, contract)
    rows = _detections(checked_events["events"], checked_policy["rules"], packet["detected_at"])
    counts = {status: sum(row["detection_status"] == status for row in rows) for status in contract["detection_statuses"]}
    expected_summary = {
        "event_count": len(rows), **counts,
        "late_escalation_count": sum(row["detection_status"] == "ESCALATED" and row["timing_status"] == "LATE" for row in rows),
        "notification_sent_count": 0, "action_count": 0, "order_count": 0,
    }
    expected_lineage = {
        "event_batch_id": checked_events["batch_id"],
        "event_batch_sha256": checked_events["packet_sha256"],
        "policy_id": checked_policy["policy_id"],
        "policy_sha256": checked_policy["packet_sha256"],
    }
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "IMPORTANT_EVENTS_DETECTED_NOTIFICATION_NOT_SENT"
        or packet.get("detections") != rows
        or packet.get("summary") != expected_summary
        or packet.get("lineage") != expected_lineage
        or packet.get("authority") != contract["authority"]
        or packet.get("unresolved_boundaries") != [
            "LIVE_SEC_DART_NEWS_ADAPTERS_NOT_WIRED",
            "REPOSITORY_DEFAULT_IMPORTANCE_POLICY_ABSENT",
            "NOTIFICATION_DELIVERY_NOT_IMPLEMENTED",
            "ACTION_AND_ORDER_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ]
    ):
        raise ImportantEventDetectorError("PACKET_CONTENT_MISMATCH")
    digest = _sha(packet.get("packet_sha256"), "PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise ImportantEventDetectorError("PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ImportantEventDetectorError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(event_path: Path, policy_path: Path, detected_at: str, output_path: Path) -> int:
    try:
        write_json_atomic(
            output_path,
            build_packet(_read_json(event_path), _read_json(policy_path), detected_at),
        )
        return 0
    except (ImportantEventDetectorError, OSError, TypeError, ValueError) as exc:
        print(f"Important event detector failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("policy", type=Path)
    parser.add_argument("--detected-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.events, args.policy, args.detected_at, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
