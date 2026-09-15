#!/usr/bin/env python3
"""Canonical ``rule_refs`` and append-only ``rule_lineage_event/1`` records.

This is the decision-lineage library the P2 rule scorecard will consume.  It
names *which registered rule* (``config/rule_registry_v1.json``) produced,
blocked, sized or exited a decision.  It never computes a decision itself.

``rule_refs`` entry (closed field set)::

    {"rule_id", "version", "registry_sha256", "source_record_sha256", "role"}

* ``role`` is one of APPLIED | BLOCKED_BY | SIZED_BY | EXITED_BY | SUPERSEDED_BY;
* ``version`` and ``source_record_sha256`` must equal the registry row;
* ``registry_sha256`` is the sha256 of the exact registry file bytes;
* the canonical list is sorted by (rule_id, role) with no duplicate pair.

``rule_lineage_event/1`` (closed field set)::

    {"schema_version", "event_id", "decision_id", "producer", "market",
     "instrument", "timestamp_utc", "event_type", "gate", "outcome",
     "rule_refs", "unapplied_rules", "inputs_sha256", "source_packet"}

* ``event_type`` is DECISION | BLOCK | ORDER | FILL | EXIT;
* ``unapplied_rules`` lists registered rules that govern this decision's
  subject but that the producer does not execute today (reason code only) --
  the scorecard reports them as NOT_EVALUATED instead of silently skipping;
* ``event_id`` is the sha256 of the canonical event without ``event_id``.

Sidecar ``rule_lineage_sidecar/1`` groups the events derived from exactly one
producer packet, keyed by that packet's ``payload_sha256``.  A sidecar is
write-once: an existing file is verified byte-for-byte and never rewritten, so
the evidence tree stays append-only.  Sidecars never modify the source packet.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from governance import rule_registry as REGISTRY  # noqa: E402


# SUPERSEDED_BY (additive, 2026-09-15): cites the successor rule when a
# consumer displays wording that a later rule replaced (e.g. PR #756 overlays
# the rotation policy's old held-position action with
# RULE.EXIT.RELEASE_FULL_SELL.V1).  It never marks a decision as applied.
ROLES = ("APPLIED", "BLOCKED_BY", "SIZED_BY", "EXITED_BY", "SUPERSEDED_BY")
EVENT_TYPES = ("DECISION", "BLOCK", "ORDER", "FILL", "EXIT")
EVENT_SCHEMA_VERSION = "rule_lineage_event/1"
SIDECAR_SCHEMA_VERSION = "rule_lineage_sidecar/1"
REF_FIELDS = {"rule_id", "version", "registry_sha256", "source_record_sha256", "role"}
UNAPPLIED_FIELDS = {"rule_id", "reason_code"}
EVENT_FIELDS = {
    "schema_version", "event_id", "decision_id", "producer", "market", "instrument",
    "timestamp_utc", "event_type", "gate", "outcome", "rule_refs", "unapplied_rules",
    "inputs_sha256", "source_packet",
}
SOURCE_PACKET_FIELDS = {"path", "schema_version", "payload_sha256"}
SIDECAR_FIELDS = {
    "schema_version", "producer", "registry_path", "registry_sha256", "source_packet",
    "decision_outcome_changed", "events", "payload_sha256",
}
MARKETS = ("US", "KR", "CRYPTO")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9_:.\-]{2,160}$")


class RuleLineageError(ValueError):
    """Fail-closed rule_refs / lineage violation."""


def _fail(code: str, detail: str = "") -> None:
    raise RuleLineageError(f"{code}:{detail}" if detail else code)


canonical_json = REGISTRY.canonical_json
payload_sha256 = REGISTRY.payload_sha256


class RegistryContext:
    """A validated registry plus the identity of its exact bytes."""

    def __init__(self, registry: dict, sha256: str, relative_path: str = REGISTRY.REGISTRY_RELATIVE_PATH):
        if not isinstance(sha256, str) or SHA256_RE.fullmatch(sha256) is None:
            _fail("REGISTRY_SHA_INVALID")
        self.registry = registry
        self.sha256 = sha256
        self.relative_path = relative_path
        self.rules = REGISTRY.rule_index(registry)

    @classmethod
    def load(cls, path: Path = REGISTRY.REGISTRY_PATH, *, root: Path = REGISTRY.ROOT) -> "RegistryContext":
        registry = REGISTRY.load_registry(path, root=root)
        return cls(registry, REGISTRY.registry_sha256(path))


def make_rule_ref(context: RegistryContext, rule_id: str, role: str) -> dict:
    row = context.rules.get(rule_id)
    if row is None:
        _fail("RULE_NOT_REGISTERED", str(rule_id))
    if role not in ROLES:
        _fail("ROLE_INVALID", str(role))
    if not REGISTRY.is_decided(row):
        # A not-decided item can be reported as a gap, never cited as applied.
        _fail("RULE_NOT_DECIDED", rule_id)
    return {
        "rule_id": rule_id,
        "version": row["version"],
        "registry_sha256": context.sha256,
        "source_record_sha256": REGISTRY.primary_record_sha256(row),
        "role": role,
    }


def validate_rule_refs(refs, context: RegistryContext) -> list:
    """Validate and return the canonical (sorted, duplicate-free) list."""
    if not isinstance(refs, list):
        _fail("RULE_REFS_NOT_LIST")
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != REF_FIELDS:
            _fail("RULE_REF_FIELDS_INVALID")
        expected = make_rule_ref(context, ref["rule_id"], ref["role"])
        if ref != expected:
            _fail("RULE_REF_NOT_REGISTRY_EXACT", ref["rule_id"])
        key = (ref["rule_id"], ref["role"])
        if key in seen:
            _fail("RULE_REF_DUPLICATE", f"{key[0]}:{key[1]}")
        seen.add(key)
    canonical = sorted((copy.deepcopy(ref) for ref in refs), key=lambda r: (r["rule_id"], r["role"]))
    return canonical


def canonical_rule_refs(pairs, context: RegistryContext) -> list:
    """Build a canonical list from ``(rule_id, role)`` pairs (duplicates merged)."""
    unique = sorted({(rule_id, role) for rule_id, role in pairs})
    return validate_rule_refs([make_rule_ref(context, rule_id, role) for rule_id, role in unique], context)


def _validate_unapplied(items, context: RegistryContext, applied_ids: set) -> list:
    if not isinstance(items, list):
        _fail("UNAPPLIED_RULES_NOT_LIST")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != UNAPPLIED_FIELDS:
            _fail("UNAPPLIED_RULE_FIELDS_INVALID")
        if item["rule_id"] not in context.rules:
            _fail("UNAPPLIED_RULE_NOT_REGISTERED", str(item["rule_id"]))
        if item["rule_id"] in applied_ids or item["rule_id"] in seen:
            _fail("UNAPPLIED_RULE_CONFLICT", item["rule_id"])
        if not isinstance(item["reason_code"], str) or TOKEN_RE.fullmatch(item["reason_code"]) is None:
            _fail("UNAPPLIED_REASON_INVALID", item["rule_id"])
        seen.add(item["rule_id"])
    return sorted((copy.deepcopy(i) for i in items), key=lambda i: i["rule_id"])


def _validate_source_packet(value) -> dict:
    if not isinstance(value, dict) or set(value) != SOURCE_PACKET_FIELDS:
        _fail("SOURCE_PACKET_FIELDS_INVALID")
    if not isinstance(value["path"], str) or value["path"].startswith("/") or ".." in value["path"]:
        _fail("SOURCE_PACKET_PATH_INVALID")
    if not isinstance(value["schema_version"], str) or not value["schema_version"]:
        _fail("SOURCE_PACKET_SCHEMA_INVALID")
    if not isinstance(value["payload_sha256"], str) or SHA256_RE.fullmatch(value["payload_sha256"]) is None:
        _fail("SOURCE_PACKET_SHA_INVALID")
    return value


def build_lineage_event(
    context: RegistryContext, *, decision_id: str, producer: str, market: str,
    instrument, timestamp_utc: str, event_type: str, gate, outcome: dict,
    rule_refs: list, unapplied_rules: list, inputs, source_packet: dict,
) -> dict:
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "decision_id": decision_id,
        "producer": producer,
        "market": market,
        "instrument": instrument,
        "timestamp_utc": timestamp_utc,
        "event_type": event_type,
        "gate": gate,
        "outcome": copy.deepcopy(outcome),
        "rule_refs": validate_rule_refs(rule_refs, context),
        "unapplied_rules": sorted(copy.deepcopy(unapplied_rules), key=lambda i: i.get("rule_id", "")),
        "inputs_sha256": payload_sha256(inputs),
        "source_packet": copy.deepcopy(source_packet),
    }
    event["event_id"] = payload_sha256(event)
    return validate_lineage_event(event, context)


def validate_lineage_event(event, context: RegistryContext) -> dict:
    if not isinstance(event, dict) or set(event) != EVENT_FIELDS:
        _fail("EVENT_FIELDS_INVALID")
    if event["schema_version"] != EVENT_SCHEMA_VERSION:
        _fail("EVENT_SCHEMA_INVALID")
    for key in ("decision_id", "producer"):
        if not isinstance(event[key], str) or not event[key]:
            _fail("EVENT_IDENTITY_INVALID", key)
    if event["market"] not in MARKETS:
        _fail("EVENT_MARKET_INVALID")
    if event["instrument"] is not None and (not isinstance(event["instrument"], str) or not event["instrument"]):
        _fail("EVENT_INSTRUMENT_INVALID")
    if not isinstance(event["timestamp_utc"], str) or UTC_RE.fullmatch(event["timestamp_utc"]) is None:
        _fail("EVENT_TIMESTAMP_INVALID")
    if event["event_type"] not in EVENT_TYPES:
        _fail("EVENT_TYPE_INVALID")
    if event["gate"] is not None and (not isinstance(event["gate"], str) or not event["gate"]):
        _fail("EVENT_GATE_INVALID")
    if not isinstance(event["outcome"], dict):
        _fail("EVENT_OUTCOME_INVALID")
    refs = validate_rule_refs(event["rule_refs"], context)
    if refs != event["rule_refs"]:
        _fail("EVENT_RULE_REFS_NOT_CANONICAL")
    if event["event_type"] == "BLOCK" and not any(r["role"] == "BLOCKED_BY" for r in refs) \
            and not event["unapplied_rules"]:
        # A BLOCK must name the blocking rule, or state that no registered
        # rule is executed by the producer for that block.
        _fail("BLOCK_EVENT_WITHOUT_BLOCKING_RULE")
    unapplied = _validate_unapplied(event["unapplied_rules"], context, {r["rule_id"] for r in refs})
    if unapplied != event["unapplied_rules"]:
        _fail("EVENT_UNAPPLIED_NOT_CANONICAL")
    if not isinstance(event["inputs_sha256"], str) or SHA256_RE.fullmatch(event["inputs_sha256"]) is None:
        _fail("EVENT_INPUTS_SHA_INVALID")
    _validate_source_packet(event["source_packet"])
    unsigned = {k: v for k, v in event.items() if k != "event_id"}
    if event["event_id"] != payload_sha256(unsigned):
        _fail("EVENT_ID_MISMATCH")
    return copy.deepcopy(event)


def build_sidecar(context: RegistryContext, *, producer: str, source_packet: dict, events: list) -> dict:
    ordered = sorted(
        (validate_lineage_event(event, context) for event in events),
        key=lambda e: (e["decision_id"], e["gate"] or "", e["event_type"], e["event_id"]),
    )
    sidecar = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "producer": producer,
        "registry_path": context.relative_path,
        "registry_sha256": context.sha256,
        "source_packet": copy.deepcopy(_validate_source_packet(source_packet)),
        # Lineage is additive only; the producer's own packet is never edited.
        "decision_outcome_changed": False,
        "events": ordered,
    }
    sidecar["payload_sha256"] = payload_sha256(sidecar)
    return validate_sidecar(sidecar, context)


def validate_sidecar(sidecar, context: RegistryContext) -> dict:
    if not isinstance(sidecar, dict) or set(sidecar) != SIDECAR_FIELDS:
        _fail("SIDECAR_FIELDS_INVALID")
    if sidecar["schema_version"] != SIDECAR_SCHEMA_VERSION:
        _fail("SIDECAR_SCHEMA_INVALID")
    if sidecar["registry_sha256"] != context.sha256 or sidecar["registry_path"] != context.relative_path:
        _fail("SIDECAR_REGISTRY_MISMATCH")
    if sidecar["decision_outcome_changed"] is not False:
        _fail("SIDECAR_MUST_NOT_CHANGE_DECISIONS")
    source = _validate_source_packet(sidecar["source_packet"])
    if not isinstance(sidecar["events"], list):
        _fail("SIDECAR_EVENTS_INVALID")
    ids = set()
    for event in sidecar["events"]:
        validate_lineage_event(event, context)
        if event["producer"] != sidecar["producer"] or event["source_packet"] != source:
            _fail("SIDECAR_EVENT_SOURCE_MISMATCH")
        if event["event_id"] in ids:
            _fail("SIDECAR_EVENT_DUPLICATE")
        ids.add(event["event_id"])
    unsigned = {k: v for k, v in sidecar.items() if k != "payload_sha256"}
    if sidecar["payload_sha256"] != payload_sha256(unsigned):
        _fail("SIDECAR_SHA_MISMATCH")
    return copy.deepcopy(sidecar)


def sidecar_text(sidecar: dict) -> str:
    return json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_sidecar_append_only(path: Path, sidecar: dict) -> str:
    """Write once; an existing file must already hold the identical bytes."""
    path = Path(path)
    text = sidecar_text(sidecar)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            _fail("SIDECAR_APPEND_ONLY_CONFLICT", str(path))
        return "verified_existing"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()
    return "written"
