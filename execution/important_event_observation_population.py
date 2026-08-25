#!/usr/bin/env python3
"""P9-02 provider-free observation population from committed P3-08 cases.

The adapter deliberately stops before importance classification.  P3-08 SEC
cases currently retain a filing *date* but not an authoritative filing
timestamp.  The P9-02 observation contract requires second precision, so every
adapted row remains evidence ``BLOCKED`` with
``EVENT_TIME_PRECISION_DATE_ONLY``.  A later RATIFIED importance policy cannot
therefore escalate these rows accidentally.

No provider, policy, notification, candidate, action, order, Production, or
trading authority is introduced here.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_DIR = ROOT / "discovery"
EXECUTION_DIR = ROOT / "execution"
sys.path.insert(0, str(DISCOVERY_DIR))
sys.path.insert(0, str(EXECUTION_DIR))

import event_case as CASE  # noqa: E402
import event_population as EVENT_POPULATION  # noqa: E402
import important_event_detector as DETECTOR  # noqa: E402


SCHEMA_VERSION = "important_event_observation_population/1"
EVENT_TOKEN_RE = re.compile(r"[^A-Z0-9_.:]+")
AUTHORITY = {
    "observation_population_only": True,
    "importance_classification_authorized": False,
    "candidate_promotion_authorized": False,
    "notification_authorized": False,
    "action_generation_authorized": False,
    "order_generation_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}
UNRESOLVED_BOUNDARIES = [
    "EVENT_TIMESTAMP_NOT_RETAINED_DATE_ONLY",
    "REPOSITORY_DEFAULT_IMPORTANCE_POLICY_ABSENT",
    "DART_NEWS_CRYPTO_ADAPTERS_NOT_WIRED",
    "NOTIFICATION_DELIVERY_NOT_IMPLEMENTED",
    "ACTION_ORDER_PRODUCTION_TRADING_NOT_AUTHORIZED",
]


class ImportantEventObservationPopulationError(ValueError):
    """Fail-closed source-lineage or append-only publication violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportantEventObservationPopulationError(
            f"JSON_READ_FAILED:{path}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ImportantEventObservationPopulationError(f"JSON_NOT_OBJECT:{path}")
    return value


def _utc(value: str, code: str) -> dt.datetime:
    try:
        return DETECTOR._utc(value, code)
    except DETECTOR.ImportantEventDetectorError as exc:
        raise ImportantEventObservationPopulationError(str(exc)) from exc


def _event_type_token(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImportantEventObservationPopulationError("SOURCE_EVENT_TYPE_INVALID")
    token = EVENT_TOKEN_RE.sub("_", value.strip().upper()).strip("_")
    if DETECTOR.TOKEN_RE.fullmatch(token) is None:
        raise ImportantEventObservationPopulationError(
            f"SOURCE_EVENT_TYPE_NOT_NORMALIZABLE:{value!r}"
        )
    return token


def _event_id(case_id: str) -> str:
    if not isinstance(case_id, str) or not case_id:
        raise ImportantEventObservationPopulationError("SOURCE_CASE_ID_INVALID")
    return f"SEC_EVENT_{hashlib.sha256(case_id.encode('utf-8')).hexdigest()[:24].upper()}"


def _date_floor_utc(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ImportantEventObservationPopulationError("SOURCE_EVENT_DATE_INVALID") from exc
    if parsed.isoformat() != value:
        raise ImportantEventObservationPopulationError("SOURCE_EVENT_DATE_INVALID")
    return f"{value}T00:00:00Z"


def build_event_batch(source_packet: dict, observed_at: str) -> dict:
    """Re-derive a P9-02 batch from a fully validated P3-08 source packet."""
    observed = _utc(observed_at, "OBSERVED_AT_INVALID")
    try:
        source = CASE.validate_packet(copy.deepcopy(source_packet))
    except CASE.EventCaseError as exc:
        raise ImportantEventObservationPopulationError(
            f"SOURCE_EVENT_PACKET_INVALID:{exc}"
        ) from exc

    events = []
    for case in source["cases"]:
        event_at = _date_floor_utc(case["event_date"])
        lineage = case["evidence_lineage"]
        # The detector/2 schema has no separate date-only field.  Keep the
        # mechanically encoded UTC date floor visibly non-authoritative and
        # BLOCKED; it is never an asserted filing timestamp.
        reasons = [
            "EVENT_AT_DATE_FLOOR_PLACEHOLDER",
            "EVENT_TIME_PRECISION_DATE_ONLY",
        ]
        if case["evidence_status"] == CASE.EVIDENCE_LINKED:
            if not isinstance(lineage, dict):
                raise ImportantEventObservationPopulationError(
                    f"LINKED_CASE_LINEAGE_MISSING:{case['case_id']}"
                )
            available_at = lineage["retrieved_at_utc"]
            source_ref = lineage["source_url"]
            source_sha256 = lineage["source_sha256"]
        else:
            available_at = observed_at
            source_ref = f"atlas:event-discovery-case:{case['case_id']}"
            source_sha256 = source["packet_sha256"]
            reasons.append("SOURCE_EVIDENCE_UNRESOLVED")
        available = _utc(available_at, "SOURCE_AVAILABLE_AT_INVALID")
        if available > observed:
            raise ImportantEventObservationPopulationError(
                f"SOURCE_AVAILABLE_AFTER_OBSERVATION:{case['case_id']}"
            )
        if _utc(event_at, "SOURCE_EVENT_AT_INVALID") > available:
            raise ImportantEventObservationPopulationError(
                f"SOURCE_EVENT_AFTER_AVAILABLE:{case['case_id']}"
            )
        events.append({
            "event_id": _event_id(case["case_id"]),
            "market": case["market"],
            "subject_id": case["subject"],
            "source_kind": "SEC_EDGAR",
            "event_type": _event_type_token(case["event_type"]),
            "event_at": event_at,
            "available_at": available_at,
            "received_at": available_at,
            "source_ref": source_ref,
            "source_sha256": source_sha256,
            "evidence_status": "BLOCKED",
            "blocked_reasons": sorted(reasons),
        })
    events.sort(key=lambda row: (row["available_at"], row["event_id"]))
    contract = DETECTOR.load_contract()
    body = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": f"SEC_EVENT_BATCH_{source['packet_sha256'][:24].upper()}",
        "observed_at": observed_at,
        "events": events,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    body["packet_sha256"] = DETECTOR.payload_sha256(body)
    try:
        DETECTOR._validate_events(copy.deepcopy(body), observed, contract)
    except DETECTOR.ImportantEventDetectorError as exc:
        raise ImportantEventObservationPopulationError(
            f"EVENT_BATCH_INVALID:{exc}"
        ) from exc
    return body


def _summary(source_packet: dict, batch: dict) -> dict:
    return {
        "source_cases": len(source_packet["cases"]),
        "source_evidence_linked": source_packet["summary"][CASE.EVIDENCE_LINKED],
        "source_evidence_unresolved": source_packet["summary"][CASE.EVIDENCE_UNRESOLVED],
        "observation_events": len(batch["events"]),
        "confirmed_events": sum(row["evidence_status"] == "CONFIRMED" for row in batch["events"]),
        "blocked_events": sum(row["evidence_status"] == "BLOCKED" for row in batch["events"]),
        "date_only_blocked_events": sum(
            "EVENT_TIME_PRECISION_DATE_ONLY" in row["blocked_reasons"]
            for row in batch["events"]
        ),
        "importance_classified_events": 0,
        "notification_sent_count": 0,
        "action_count": 0,
        "order_count": 0,
    }


def build_packet(source_packet: dict, observed_at: str) -> dict:
    source = CASE.validate_packet(copy.deepcopy(source_packet))
    batch = build_event_batch(source, observed_at)
    packet = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at,
        "status": "OBSERVATION_BATCH_POPULATED_DETECTION_BLOCKED",
        "source_packet": source,
        "event_batch": batch,
        "summary": _summary(source, batch),
        "authority": copy.deepcopy(AUTHORITY),
        "unresolved_boundaries": list(UNRESOLVED_BOUNDARIES),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet)


def validate_packet(packet: dict) -> dict:
    fields = {
        "schema_version", "observed_at", "status", "source_packet", "event_batch",
        "summary", "authority", "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ImportantEventObservationPopulationError("PACKET_FIELDS_MISMATCH")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise ImportantEventObservationPopulationError("PACKET_SCHEMA_MISMATCH")
    expected = build_event_batch(packet.get("source_packet"), packet.get("observed_at"))
    try:
        source = CASE.validate_packet(copy.deepcopy(packet["source_packet"]))
    except CASE.EventCaseError as exc:
        raise ImportantEventObservationPopulationError(
            f"SOURCE_EVENT_PACKET_INVALID:{exc}"
        ) from exc
    if (
        packet.get("status") != "OBSERVATION_BATCH_POPULATED_DETECTION_BLOCKED"
        or packet.get("event_batch") != expected
        or packet.get("summary") != _summary(source, expected)
        or packet.get("authority") != AUTHORITY
        or packet.get("unresolved_boundaries") != UNRESOLVED_BOUNDARIES
    ):
        raise ImportantEventObservationPopulationError("PACKET_CONTENT_MISMATCH")
    digest = packet.get("packet_sha256")
    if not isinstance(digest, str) or DETECTOR.SHA256_RE.fullmatch(digest) is None:
        raise ImportantEventObservationPopulationError("PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ImportantEventObservationPopulationError("PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def _packet_bytes(packet: dict) -> bytes:
    return (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def require_published_source_packet(
    *, repo_root: Path, event_root: Path, decision_at: str
) -> dict:
    result = EVENT_POPULATION.build_population_inputs(
        repo_root=repo_root, decision_at=decision_at
    )
    source = result["packet"]
    decision_date = EVENT_POPULATION._decision_time(decision_at).astimezone(
        EVENT_POPULATION.KST
    ).date().isoformat()
    path = Path(event_root) / decision_date / f"packet-{source['packet_sha256'][:16]}.json"
    if not path.is_file():
        raise ImportantEventObservationPopulationError(
            f"PUBLISHED_SOURCE_PACKET_MISSING:{path}"
        )
    raw = path.read_bytes()
    if raw != EVENT_POPULATION._packet_bytes(source):
        raise ImportantEventObservationPopulationError(
            f"PUBLISHED_SOURCE_PACKET_BYTES_MISMATCH:{path}"
        )
    return _read_json(path)


def publish_append_only(*, out_root: Path, observed_at: str, packet: dict) -> tuple[Path, bool]:
    checked = validate_packet(copy.deepcopy(packet))
    date = _utc(observed_at, "OBSERVED_AT_INVALID").astimezone(
        EVENT_POPULATION.KST
    ).date().isoformat()
    raw = _packet_bytes(checked)
    path = Path(out_root) / date / f"packet-{checked['packet_sha256'][:16]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ImportantEventObservationPopulationError(
                f"CONTENT_ADDRESSED_PACKET_DRIFT:{path}"
            )
        return path, False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path, True


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--event-root", type=Path,
        default=ROOT / "data/observations/event_discovery_cases",
    )
    parser.add_argument(
        "--out-root", type=Path,
        default=ROOT / "data/observations/important_event_observations",
    )
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args(argv)
    source = require_published_source_packet(
        repo_root=args.repo_root,
        event_root=args.event_root,
        decision_at=args.observed_at,
    )
    packet = build_packet(source, args.observed_at)
    path, created = publish_append_only(
        out_root=args.out_root, observed_at=args.observed_at, packet=packet
    )
    print(json.dumps({
        "status": "published" if created else "verified_existing",
        "path": path.as_posix(),
        "source_cases": packet["summary"]["source_cases"],
        "observation_events": packet["summary"]["observation_events"],
        "confirmed_events": packet["summary"]["confirmed_events"],
        "blocked_events": packet["summary"]["blocked_events"],
        "packet_sha256": packet["packet_sha256"],
        "authority": packet["authority"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    try:
        return run()
    except ImportantEventObservationPopulationError as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
