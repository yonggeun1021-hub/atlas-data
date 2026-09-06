#!/usr/bin/env python3
"""Pinned, versioned briefing core with optional module isolation.

``briefing_core/2`` does not replace the existing daily-orchestrator or Portal
contracts.  It freezes their exact inputs, creates the previously external
handoff/claims boundary deterministically, and projects back to the existing
``claim_ledger/1`` and ``portal_projection/2`` contracts.

The only fail-closed errors at this layer are identity, date, lineage,
duplicate-publication and execution-authority violations.  A market/news/
rotation adapter failure is recorded as ``UNKNOWN`` for that module and never
turns an otherwise valid briefing snapshot into a global failure.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any

from . import major_events


CORE_CONTRACT = "briefing_core/2"
INPUT_SCHEMA = "briefing_input_envelope/2"
HANDOFF_SCHEMA = "briefing_handoff/2"
CLAUDE_COMPAT_SCHEMA = "claude_briefing_handoff/1"
CLAIM_LEDGER_SCHEMA = "claim_ledger/1"
DISPLAY_SCHEMA = "portal_display_proposal/1"
VALIDATION_SCHEMA = "briefing_validation_report/1"
PORTAL_SCHEMA = "portal_projection/2"
NOTION_RECEIPT_SCHEMA = "notion_briefing_receipt/2"
INDEX_SCHEMA = "briefing_chain_index/2"

STEP0_STATUS_PATH = "data/briefing/step0_status.json"
BRIEFING_STATUS_PATH = "data/briefing_status.json"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLOT_TO_LEGACY = {"morning": "AM", "evening": "PM"}

EXECUTION_TRUE_KEYS = {
    "stage_authority", "stage_authorized", "stage_change_authorized",
    "stage_promotion_authority", "stage_promotion_authorized",
    "buy_authority", "buy_authorized",
    "action_authority", "action_authorized",
    "order_authority", "order_authorized", "order_generation_authorized",
    "production_authority", "production_authorized",
    "trading_authority", "trading_authorized",
    "broker_credentials_present", "broker_credentials_used",
    "real_capital", "real_capital_authorized",
}

SAFETY_ATTESTATION = {
    "read_only": True,
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
    "broker_credentials_present": False,
}

CLAUDE_SAFETY_ATTESTATION = {
    "stage_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
    "broker_credentials_used": False,
}


class ChainError(RuntimeError):
    """A fail-closed core-contract error."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any) -> str:
    return digest_bytes(canonical(value))


def _safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ChainError("CORE_PATH_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ChainError(f"CORE_PATH_INVALID:{value}")
    return value


def _git_bytes(repo_root: Path, source_commit: str, path: str) -> bytes:
    _safe_path(path)
    try:
        return subprocess.check_output(
            ["git", "show", f"{source_commit}:{path}"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise ChainError(f"CORE_SOURCE_PATH_MISSING:{path}") from exc


def _git_optional_bytes(repo_root: Path, source_commit: str, path: str) -> bytes | None:
    try:
        return _git_bytes(repo_root, source_commit, path)
    except ChainError as exc:
        if str(exc) == f"CORE_SOURCE_PATH_MISSING:{path}":
            return None
        raise


def _require_commit(repo_root: Path, source_commit: str) -> None:
    if not isinstance(source_commit, str) or FULL_SHA.fullmatch(source_commit) is None:
        raise ChainError("CORE_SOURCE_COMMIT_INVALID")
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{source_commit}^{{commit}}"],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise ChainError("CORE_SOURCE_COMMIT_UNAVAILABLE")


def _json_bytes(body: bytes, code: str) -> dict:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChainError(code) from exc
    if not isinstance(value, dict):
        raise ChainError(code)
    return value


def _packet_self_hash(packet: dict) -> str:
    unsigned = copy.deepcopy(packet)
    claimed = unsigned.pop("packet_sha256", None)
    actual = digest(unsigned)
    if claimed != actual:
        raise ChainError("CORE_PACKET_SELF_HASH_MISMATCH")
    return actual


def _validate_dynamic_clock_frozen_source(packet: dict, expected_date: str) -> None:
    """Validate a present P8-12 source inside an immutable daily packet.

    Historical packets predate this frozen source and remain readable.  Once
    ``DYNAMIC_CLOCK`` is present, however, the immutable input-envelope reader
    must bind the exact variant and, for a report, its canonical bytes and
    decision date.  Re-signing the outer packet is not enough to legitimize a
    changed or malformed source identity.
    """
    frozen_sources = packet.get("frozen_sources")
    if frozen_sources is None:
        return
    if type(frozen_sources) is not dict:  # noqa: E721 - exact JSON boundary
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:frozen_sources_type")
    if "DYNAMIC_CLOCK" not in frozen_sources:
        return
    source = frozen_sources["DYNAMIC_CLOCK"]
    if type(source) is not dict:  # noqa: E721 - exact JSON boundary
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:source_type")
    kind = source.get("kind")
    if type(kind) is not str:  # noqa: E721 - reject bool/string aliases
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:kind_type")
    if kind == "unavailable":
        if set(source) != {"kind"}:
            raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:unavailable_shape")
        return
    if kind == "error":
        if set(source) != {"kind", "value"} or type(source.get("value")) is not str:
            raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:error_shape")
        return
    if kind != "report" or set(source) != {"kind", "report_sha256", "report"}:
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:report_shape")
    report = source.get("report")
    report_sha256 = source.get("report_sha256")
    if type(report) is not dict or type(report_sha256) is not str:
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:report_hash_type")
    if SHA256.fullmatch(report_sha256) is None:
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_INVALID:report_sha256")
    if digest(report) != report_sha256:
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_SHA_MISMATCH")
    if report.get("decision_date") != expected_date:
        raise ChainError("CORE_DYNAMIC_CLOCK_SOURCE_DATE_MISMATCH")


def _walk_generation_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "generation_id":
                if not isinstance(nested, str) or SHA256.fullmatch(nested) is None:
                    raise ChainError("CORE_GENERATION_INVALID")
                found.add(nested)
            found.update(_walk_generation_ids(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(_walk_generation_ids(nested))
    return found


def _source_generation_id(value: dict, code: str) -> str:
    generation = value.get("generation")
    if not isinstance(generation, dict):
        raise ChainError(code)
    generation_id = generation.get("generation_id")
    if not isinstance(generation_id, str) or SHA256.fullmatch(generation_id) is None:
        raise ChainError(code)
    return generation_id


def _canonical_generation_id(
    repo_root: Path,
    source_commit: str,
    packet: dict,
) -> str:
    """Resolve the read-model generation without conflating source lineages.

    Current daily packets may contain valid nested generation IDs for several
    independently versioned source packets.  The briefing root generation is
    therefore read from the two canonical Step 0 read-model files at the exact
    source commit.  Historical commits that predate both files retain the
    original singleton rule.
    """
    nested_generation_ids = _walk_generation_ids(packet)
    step0_bytes = _git_optional_bytes(repo_root, source_commit, STEP0_STATUS_PATH)
    health_bytes = _git_optional_bytes(repo_root, source_commit, BRIEFING_STATUS_PATH)

    if step0_bytes is None and health_bytes is None:
        if len(nested_generation_ids) != 1:
            raise ChainError("CORE_GENERATION_NOT_SINGLETON")
        return next(iter(nested_generation_ids))
    if step0_bytes is None or health_bytes is None:
        raise ChainError("CORE_CANONICAL_GENERATION_SOURCE_MISSING")

    step0 = _json_bytes(step0_bytes, "CORE_STEP0_STATUS_INVALID_JSON")
    health = _json_bytes(health_bytes, "CORE_BRIEFING_STATUS_INVALID_JSON")
    step0_generation_id = _source_generation_id(
        step0, "CORE_STEP0_GENERATION_INVALID"
    )
    health_generation_id = _source_generation_id(
        health, "CORE_BRIEFING_STATUS_GENERATION_INVALID"
    )
    if step0_generation_id != health_generation_id:
        raise ChainError("CORE_CANONICAL_GENERATION_MISMATCH")

    components = packet.get("components")
    if isinstance(components, list):
        for component in components:
            if (
                not isinstance(component, dict)
                or component.get("component_id") != "STEP0_READ_MODEL_HEALTH"
            ):
                continue
            embedded_packet = component.get("packet")
            if not isinstance(embedded_packet, dict):
                continue
            embedded_generation = embedded_packet.get("generation")
            if embedded_generation is None:
                continue
            if not isinstance(embedded_generation, dict):
                raise ChainError("CORE_EMBEDDED_STEP0_GENERATION_INVALID")
            embedded_generation_id = embedded_generation.get("generation_id")
            if (
                not isinstance(embedded_generation_id, str)
                or SHA256.fullmatch(embedded_generation_id) is None
            ):
                raise ChainError("CORE_EMBEDDED_STEP0_GENERATION_INVALID")
            if embedded_generation_id != step0_generation_id:
                raise ChainError("CORE_EMBEDDED_STEP0_GENERATION_MISMATCH")

    return step0_generation_id


def _assert_execution_locked(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in EXECUTION_TRUE_KEYS and nested not in (False, None, 0, "PAPER"):
                raise ChainError(f"CORE_EXECUTION_AUTHORITY_VIOLATION:{path}.{key}")
            if key == "account_mode" and nested not in (None, "PAPER", "SHADOW", "READ_ONLY"):
                raise ChainError(f"CORE_ACCOUNT_BOUNDARY_VIOLATION:{path}.{key}")
            if key in {"account_number", "broker_account_id", "private_cash_balance"} and nested not in (None, ""):
                raise ChainError(f"CORE_ACCOUNT_BOUNDARY_VIOLATION:{path}.{key}")
            _assert_execution_locked(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_execution_locked(nested, f"{path}[{index}]")


def _load_registry(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChainError("CORE_MODULE_REGISTRY_UNREADABLE") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "briefing_module_registry/2"
        or not isinstance(value.get("modules"), list)
    ):
        raise ChainError("CORE_MODULE_REGISTRY_INVALID")
    seen: set[str] = set()
    for module in value["modules"]:
        if not isinstance(module, dict):
            raise ChainError("CORE_MODULE_REGISTRY_INVALID")
        required = {
            "module_id", "adapter_contract", "enabled", "required",
            "component_ids", "failure_policy",
        }
        if set(module) != required:
            raise ChainError("CORE_MODULE_REGISTRY_FIELDS_INVALID")
        module_id = module.get("module_id")
        if (
            not isinstance(module_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", module_id)
            or module_id in seen
        ):
            raise ChainError("CORE_MODULE_ID_INVALID")
        seen.add(module_id)
        if module.get("required") is not False or module.get("failure_policy") != "ITEM_UNKNOWN_CONTINUE":
            raise ChainError(f"CORE_OPTIONAL_MODULE_POLICY_INVALID:{module_id}")
        if not isinstance(module.get("component_ids"), list):
            raise ChainError(f"CORE_MODULE_COMPONENTS_INVALID:{module_id}")
    return value


def _component_binding(
    repo_root: Path, source_commit: str, component: dict
) -> tuple[str, str | None]:
    path = component.get("source_packet_path")
    expected = component.get("source_packet_sha256")
    if path is None and expected is None:
        return "PACKET_BOUND_ONLY", None
    if not isinstance(path, str):
        return "SOURCE_BINDING_UNAVAILABLE", "SOURCE_PATH_MISSING"
    if expected is None:
        return "PACKET_BOUND_ONLY", None
    if not isinstance(expected, str) or SHA256.fullmatch(expected) is None:
        return "SOURCE_BINDING_INVALID", "SOURCE_SHA256_INVALID"
    try:
        actual = digest_bytes(_git_bytes(repo_root, source_commit, path))
    except ChainError:
        return "SOURCE_BINDING_UNAVAILABLE", "SOURCE_PATH_UNAVAILABLE"
    if actual != expected:
        return "SOURCE_BINDING_MISMATCH", "SOURCE_SHA256_MISMATCH"
    return "EXACT_SOURCE_BOUND", None


def _module_results(
    repo_root: Path,
    source_commit: str,
    packet: dict,
    registry: dict,
) -> list[dict]:
    components = {
        row.get("component_id"): row
        for row in packet.get("components", [])
        if isinstance(row, dict) and isinstance(row.get("component_id"), str)
    }
    results: list[dict] = []
    for spec in registry["modules"]:
        rows: list[dict] = []
        reasons: list[str] = []
        for component_id in spec["component_ids"]:
            row = components.get(component_id)
            if row is None:
                rows.append({
                    "component_id": component_id,
                    "declared_status": "UNAVAILABLE",
                    "effective_status": "UNKNOWN",
                    "binding_status": "SOURCE_BINDING_UNAVAILABLE",
                    "reason": "COMPONENT_MISSING",
                })
                reasons.append(f"{component_id}:COMPONENT_MISSING")
                continue
            binding, binding_reason = _component_binding(repo_root, source_commit, row)
            declared = row.get("status") if isinstance(row.get("status"), str) else "UNKNOWN"
            effective = declared
            reason = row.get("reason")
            if binding in {"SOURCE_BINDING_MISMATCH", "SOURCE_BINDING_INVALID"}:
                effective = "UNKNOWN"
                reason = binding_reason
            if effective != "READY":
                reasons.append(f"{component_id}:{reason or effective}")
            rows.append({
                "component_id": component_id,
                "declared_status": declared,
                "effective_status": effective,
                "binding_status": binding,
                "reason": reason,
            })
        ready = sum(row["effective_status"] == "READY" for row in rows)
        if spec["enabled"] is not True:
            status = "UNAVAILABLE"
            reasons.append("MODULE_SHADOW_DISABLED")
        elif not rows or ready == 0:
            status = "UNAVAILABLE"
        elif ready == len(rows):
            status = "AVAILABLE"
        else:
            status = "PARTIAL"
        results.append({
            "module_id": spec["module_id"],
            "adapter_contract": spec["adapter_contract"],
            "required": False,
            "status": status,
            "failure_policy": "ITEM_UNKNOWN_CONTINUE",
            "components": rows,
            "reason_codes": sorted(set(reasons)),
        })
    return results


def _major_event_registry(
    repo_root: Path,
    source_commit: str,
    decision_date: str,
    slot: str,
    explicit_path: str | None,
) -> tuple[dict, str | None, bytes | None]:
    if explicit_path is None:
        index_path = f"evidence/briefing_events/{decision_date}/{slot}/index.json"
        index_bytes = _git_optional_bytes(repo_root, source_commit, index_path)
        if index_bytes is None:
            return {
                "schema_version": major_events.REGISTRY_SCHEMA,
                "briefing_date": decision_date,
                "slot": SLOT_TO_LEGACY[slot],
                "source_status": "UNAVAILABLE",
                "events": [],
            }, None, None
        index = _json_bytes(index_bytes, "CORE_MAJOR_EVENT_INDEX_INVALID_JSON")
        revisions = index.get("revisions")
        latest = index.get("latest_revision")
        if (
            index.get("schema_version") != "major_event_registry_index/1"
            or not isinstance(revisions, list)
            or not revisions
            or latest != len(revisions)
            or revisions[-1].get("revision") != latest
        ):
            raise ChainError("CORE_MAJOR_EVENT_INDEX_INVALID")
        relative = revisions[-1].get("path")
        expected = revisions[-1].get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ChainError("CORE_MAJOR_EVENT_INDEX_INVALID")
        explicit_path = f"evidence/briefing_events/{decision_date}/{slot}/{relative}"
        body = _git_bytes(repo_root, source_commit, explicit_path)
        if digest_bytes(body) != expected:
            raise ChainError("CORE_MAJOR_EVENT_REGISTRY_HASH_MISMATCH")
    else:
        explicit_path = _safe_path(explicit_path)
        body = _git_bytes(repo_root, source_commit, explicit_path)
    registry = _json_bytes(body, "CORE_MAJOR_EVENT_REGISTRY_INVALID_JSON")
    try:
        major_events.validate_registry(
            registry,
            briefing_date=decision_date,
            slot=SLOT_TO_LEGACY[slot],
        )
    except major_events.MajorEventError as exc:
        raise ChainError(f"CORE_{exc}") from exc
    return registry, explicit_path, body


def build_input_envelope(
    repo_root: Path,
    *,
    source_commit: str,
    packet_path: str,
    briefing_path: str,
    decision_date: str,
    slot: str,
    registry_path: Path | None = None,
    major_event_registry_path: str | None = None,
) -> dict:
    """Freeze one exact commit/generation before any downstream work."""
    repo_root = repo_root.resolve()
    _require_commit(repo_root, source_commit)
    if DATE.fullmatch(str(decision_date)) is None:
        raise ChainError("CORE_DECISION_DATE_INVALID")
    if slot not in SLOT_TO_LEGACY:
        raise ChainError("CORE_SLOT_INVALID")
    packet_path = _safe_path(packet_path)
    briefing_path = _safe_path(briefing_path)
    packet_bytes = _git_bytes(repo_root, source_commit, packet_path)
    briefing_bytes = _git_bytes(repo_root, source_commit, briefing_path)
    packet = _json_bytes(packet_bytes, "CORE_PACKET_INVALID_JSON")
    if packet.get("decision_date") != decision_date or packet.get("slot") != slot:
        raise ChainError("CORE_DATE_SLOT_LINEAGE_MISMATCH")
    packet_sha = _packet_self_hash(packet)
    _validate_dynamic_clock_frozen_source(packet, decision_date)
    generation_id = _canonical_generation_id(repo_root, source_commit, packet)
    _assert_execution_locked(packet)
    registry = _load_registry(
        registry_path or repo_root / "config/briefing_module_registry_v2.json"
    )
    modules = _module_results(repo_root, source_commit, packet, registry)
    source_refs = [
        {
            "path": packet_path,
            "sha256": digest_bytes(packet_bytes),
            "generation_id": generation_id,
        },
        {
            "path": briefing_path,
            "sha256": digest_bytes(briefing_bytes),
            "generation_id": generation_id,
        },
    ]
    event_registry, event_registry_path, event_registry_bytes = _major_event_registry(
        repo_root,
        source_commit,
        decision_date,
        slot,
        major_event_registry_path,
    )
    if event_registry_path is not None and event_registry_bytes is not None:
        source_refs.append({
            "path": event_registry_path,
            "sha256": digest_bytes(event_registry_bytes),
            "generation_id": generation_id,
        })
    snapshot = {
        "source_commit": source_commit,
        "generation_id": generation_id,
        "source_refs": source_refs,
    }
    envelope = {
        "schema_version": INPUT_SCHEMA,
        "core_contract": CORE_CONTRACT,
        "briefing_id": f"{decision_date}-{SLOT_TO_LEGACY[slot].lower()}",
        "briefing_date": decision_date,
        "slot": SLOT_TO_LEGACY[slot],
        "source_commit": source_commit,
        "generation_id": generation_id,
        "source_snapshot_id": digest(snapshot),
        "source_refs": source_refs,
        "packet_self_sha256": packet_sha,
        "modules": modules,
        "major_event_registry_path": event_registry_path,
        "major_event_registry": event_registry,
        "core_failure_policy": {
            "fail_closed": [
                "DATE", "ACCOUNT", "ORDER_AUTHORITY", "LINEAGE", "DUPLICATE_CONFLICT"
            ],
            "optional_module": "ITEM_UNKNOWN_CONTINUE",
        },
        "safety_attestation": SAFETY_ATTESTATION,
    }
    envelope["input_envelope_id"] = digest(envelope)
    return envelope


def _claims(envelope: dict) -> list[dict]:
    packet_ref = envelope["source_refs"][0]["path"]
    claims = [{
        "claim_id": "core.lineage",
        "kind": "FACT",
        "statement": (
            "The briefing input is pinned to exact source commit "
            f"{envelope['source_commit']} and generation {envelope['generation_id']}."
        ),
        "status": "VERIFIED",
        "source_ref_paths": [packet_ref],
    }]
    for module in envelope["modules"]:
        module_id = module["module_id"]
        ready = [
            row["component_id"] for row in module["components"]
            if row["effective_status"] == "READY"
        ]
        if ready:
            claims.append({
                "claim_id": f"module.{module_id}.available",
                "kind": "FACT",
                "statement": (
                    f"The sealed packet contains available {module_id} components: "
                    + ", ".join(ready) + "."
                ),
                "status": "VERIFIED",
                "source_ref_paths": [packet_ref],
            })
        if module["status"] != "AVAILABLE":
            claims.append({
                "claim_id": f"module.{module_id}.unknown",
                "kind": "UNKNOWN",
                "statement": (
                    f"The {module_id} module is {module['status']}; unavailable items "
                    "must be shown as 확인 불가 without blocking other modules."
                ),
                "status": "UNKNOWN",
                "source_ref_paths": [],
            })
    registry_path = envelope.get("major_event_registry_path")
    registry = envelope.get("major_event_registry", {})
    if registry.get("source_status") == "AVAILABLE" and registry_path:
        for event in registry["events"]:
            for event_claim in event["claims"]:
                kind = event_claim["classification"]
                claims.append({
                    "claim_id": f"event.{event['event_id']}.{event_claim['claim_id']}",
                    "kind": kind,
                    "statement": event_claim["statement_ko"],
                    "status": {
                        "FACT": "VERIFIED", "INFERENCE": "INFERRED", "UNKNOWN": "UNKNOWN"
                    }[kind],
                    "source_ref_paths": [registry_path] if kind == "FACT" else [],
                })
    else:
        claims.append({
            "claim_id": "major_events.verification_unavailable",
            "kind": "UNKNOWN",
            "statement": "주요 뉴스 검증 불가: 시장 전체·Risk On/Off·자금배분 결론을 확정하지 않습니다.",
            "status": "UNKNOWN",
            "source_ref_paths": [],
        })
    return claims


def _claim_ledger(envelope: dict, claims: list[dict]) -> dict:
    return {
        "schema_version": CLAIM_LEDGER_SCHEMA,
        "state": "READY_FOR_CHATGPT_VALIDATION",
        "briefing_id": envelope["briefing_id"],
        "briefing_date": envelope["briefing_date"],
        "slot": envelope["slot"],
        "generation_id": envelope["generation_id"],
        "source_commit": envelope["source_commit"],
        "source_refs": envelope["source_refs"],
        "claims": claims,
        "safety_attestation": SAFETY_ATTESTATION,
    }


def _claude_compat_handoff(envelope: dict, claims: list[dict]) -> dict:
    packet_ref = envelope["source_refs"][0]["path"]
    compat_claims = []
    for claim in claims:
        compat_claims.append({
            "claim_id": claim["claim_id"],
            "statement": claim["statement"],
            "type": claim["kind"],
            "observation_date": envelope["briefing_date"],
            "observed_at": "UNKNOWN",
            "source_grade": (
                "INTERNAL_LOGIC_CHECK" if claim["kind"] == "FACT" else "UNKNOWN"
            ),
            "source_refs": [packet_ref] if claim["kind"] == "FACT" else [],
            "portal_visibility": True,
            "authority_impact": "NONE",
            "compared_dates": [],
        })
    blocked = [
        f"{module['module_id']}:{module['status']}"
        for module in envelope["modules"] if module["status"] != "AVAILABLE"
    ]
    return {
        "schema_version": CLAUDE_COMPAT_SCHEMA,
        "briefing_date": envelope["briefing_date"],
        "slot": envelope["slot"],
        "draft_status": "DRAFT",
        "validation_handoff_status": "READY_FOR_CHATGPT_VALIDATION",
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
        "claims": compat_claims,
        "unknown_or_blocked": blocked,
        "portal_candidate": {
            "classification": (
                "APPLY_CANDIDATE"
                if any(m["status"] in {"AVAILABLE", "PARTIAL"} for m in envelope["modules"])
                else "BLOCKED_CANDIDATE"
            )
        },
        "safety_attestation": CLAUDE_SAFETY_ATTESTATION,
    }


def _handoff(envelope: dict, ledger: dict, claude_handoff: dict) -> dict:
    return {
        "schema_version": HANDOFF_SCHEMA,
        "core_contract": CORE_CONTRACT,
        "briefing_id": envelope["briefing_id"],
        "briefing_date": envelope["briefing_date"],
        "slot": envelope["slot"],
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
        "input_envelope_id": envelope["input_envelope_id"],
        "claim_ledger_schema": ledger["schema_version"],
        "claim_count": len(ledger["claims"]),
        "claim_ledger_sha256": digest(ledger),
        "legacy_handoff_schema": claude_handoff["schema_version"],
        "legacy_handoff_sha256": digest(claude_handoff),
        "analyst_adapter": {
            "adapter_contract": "analyst_briefing_adapter/1",
            "required": False,
            "status": "UNAVAILABLE",
            "failure_policy": "ITEM_UNKNOWN_CONTINUE",
            "reason": "NO_EXTERNAL_ANALYST_PAYLOAD_BOUND",
        },
        "major_event_coverage": major_events.unavailable_coverage(),
        "correction_history": [],
        "safety_attestation": SAFETY_ATTESTATION,
    }


def build_display_proposal(envelope: dict, event_coverage: dict | None = None) -> dict:
    modules = {
        module["module_id"]: {
            "status": module["status"],
            "reason_codes": module["reason_codes"],
        }
        for module in envelope["modules"]
    }
    return {
        "schema_version": DISPLAY_SCHEMA,
        "briefing_id": envelope["briefing_id"],
        "changes": [{
            "path": "generated/atlas-public-snapshot.json",
            "content": {
                "briefing_id": envelope["briefing_id"],
                "briefing_core_contract": CORE_CONTRACT,
                "source_commit": envelope["source_commit"],
                "generation_id": envelope["generation_id"],
                "module_availability": modules,
                "today_key_events": (
                    [
                        {
                            "event_id": event["event_id"],
                            "headline_ko": event["headline_ko"],
                            "facts": [claim["statement_ko"] for claim in event["facts"]],
                            "inferences": [claim["statement_ko"] for claim in event["inferences"]],
                            "unknowns": [claim["statement_ko"] for claim in event["unknowns"]],
                            "transmission_channels": event["transmission_channels"],
                        }
                        for event in (event_coverage or {}).get("events", [])
                    ]
                ),
                "major_news_status": (event_coverage or {}).get("user_message_ko", "주요 뉴스 검증 불가"),
                "complete_market_conclusion_allowed": (event_coverage or {}).get(
                    "complete_market_conclusion_allowed", False
                ),
                "unknown_display_policy": "확인 불가",
                "authority": {
                    "stage_authority": False,
                    "buy_authority": False,
                    "action_authority": False,
                    "order_authority": False,
                    "production_authority": False,
                    "trading_authority": False,
                },
            },
        }],
    }


def build_chain_artifacts(envelope: dict, briefing_bytes: bytes | None = None) -> dict[str, Any]:
    claims = _claims(envelope)
    ledger = _claim_ledger(envelope, claims)
    claude_handoff = _claude_compat_handoff(envelope, claims)
    handoff = _handoff(envelope, ledger, claude_handoff)
    registry = envelope["major_event_registry"]
    if registry.get("source_status") == "AVAILABLE":
        pre_validation = major_events.validate_coverage(handoff, registry)
        if pre_validation["status"] != "CORRECTION_REQUIRED":
            raise ChainError("MAJOR_EVENT_CORRECTION_LOOP_NOT_ENTERED")
        handoff = major_events.correct_handoff(handoff, registry)
        post_validation = major_events.validate_coverage(handoff, registry)
        if post_validation["status"] != "PASS" or post_validation["portal_allowed"] is not True:
            raise ChainError("MAJOR_EVENT_COVERAGE_MISSING")
    else:
        handoff["major_event_coverage"] = major_events.unavailable_coverage()
        pre_validation = major_events.validate_coverage(handoff, registry)
        post_validation = pre_validation
        if post_validation["status"] != "DEGRADED":
            raise ChainError("MAJOR_EVENT_DEGRADED_DISCLOSURE_MISSING")
    event_validation = {
        "schema_version": major_events.VALIDATION_SCHEMA,
        "pre_correction": pre_validation,
        "post_correction": post_validation,
        "correction_count": len(handoff["correction_history"]),
        "portal_allowed": post_validation["portal_allowed"],
        "overwrite_performed": False,
    }
    display = build_display_proposal(envelope, handoff["major_event_coverage"])
    artifacts: dict[str, Any] = {
        "input-envelope.json": envelope,
        "handoff.json": handoff,
        "claude-handoff-v1.json": claude_handoff,
        "claim-ledger.json": ledger,
        "display-proposal.json": display,
        "major-event-validation.json": event_validation,
    }
    if registry.get("source_status") == "AVAILABLE" and briefing_bytes is not None:
        try:
            corrected = major_events.render_corrected_briefing(
                briefing_bytes, handoff["major_event_coverage"]
            )
        except major_events.MajorEventError as exc:
            raise ChainError(f"CORE_{exc}") from exc
        artifacts["corrected-briefing.md"] = corrected
        artifacts["correction-manifest.json"] = {
            "schema_version": "briefing_correction_manifest/1",
            "briefing_id": envelope["briefing_id"],
            "source_commit": envelope["source_commit"],
            "generation_id": envelope["generation_id"],
            "reason_codes": ["MAJOR_EVENT_COVERAGE_MISSING"],
            "source_briefing_sha256": envelope["source_refs"][1]["sha256"],
            "corrected_briefing_sha256": digest_bytes(corrected),
            "overwrites_source": False,
        }
    return artifacts


def _file_body(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    return canonical(value) + b"\n"


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _stored_artifact_hash(artifacts: dict[str, Any]) -> str:
    return digest({
        name: digest_bytes(_file_body(value))
        for name, value in sorted(artifacts.items())
    })


def publish_chain(repo_root: Path, artifacts: dict[str, Any]) -> dict:
    envelope = artifacts.get("input-envelope.json")
    if not isinstance(envelope, dict):
        raise ChainError("CORE_INPUT_ENVELOPE_MISSING")
    date = envelope["briefing_date"]
    slot_dir = "morning" if envelope["slot"] == "AM" else "evening"
    root = repo_root / "data/briefing/chain_v2" / date / slot_dir
    index_path = root / "index.json"
    artifact_hash = _stored_artifact_hash(artifacts)
    chain_id = (
        f"{envelope['briefing_id']}:{envelope['source_commit']}:"
        f"{envelope['generation_id']}"
    )
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ChainError("CORE_INDEX_INVALID") from exc
        if index.get("schema_version") != INDEX_SCHEMA or not isinstance(index.get("revisions"), list):
            raise ChainError("CORE_INDEX_INVALID")
    else:
        index = {"schema_version": INDEX_SCHEMA, "latest_revision": 0, "revisions": []}
    matches = [row for row in index["revisions"] if row.get("chain_id") == chain_id]
    if matches:
        row = matches[0]
        if row.get("artifact_set_sha256") != artifact_hash:
            raise ChainError("CORE_DUPLICATE_ID_CONFLICT")
        revision_root = root / row["path"]
        for name, value in artifacts.items():
            if not (revision_root / name).is_file() or (revision_root / name).read_bytes() != _file_body(value):
                raise ChainError("CORE_STORED_REVISION_TAMPERED")
        return {
            "result": "NO_CHANGE", "chain_id": chain_id,
            "path": revision_root.relative_to(repo_root).as_posix(),
            "revision": row["revision"], "duplicate_count": 0,
        }
    revision = int(index["latest_revision"]) + 1
    revision_name = f"rev-{revision:03d}"
    revision_root = root / revision_name
    if revision_root.exists():
        raise ChainError("CORE_REVISION_PATH_CONFLICT")
    revision_root.mkdir(parents=True)
    for name, value in artifacts.items():
        _atomic_write(revision_root / name, _file_body(value))
    index["latest_revision"] = revision
    index["revisions"].append({
        "revision": revision,
        "path": revision_name,
        "chain_id": chain_id,
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
        "artifact_set_sha256": artifact_hash,
    })
    _atomic_write(index_path, _file_body(index))
    return {
        "result": "APPLIED", "chain_id": chain_id,
        "path": revision_root.relative_to(repo_root).as_posix(),
        "revision": revision, "duplicate_count": 0,
    }


def fixture_validation_report(
    ledger: dict,
    briefing_bytes: bytes,
    display: dict,
    *,
    validated_at_kst: str,
) -> dict:
    """Create a fixture-only PASS for exact system/availability claims.

    This helper is deliberately not called by the production ``build`` CLI.
    A real market-semantic PASS remains owned by the named validator.
    """
    claims_body = _file_body(ledger)
    display_body = _file_body(display)
    return {
        "schema_version": VALIDATION_SCHEMA,
        "briefing_id": ledger["briefing_id"],
        "briefing_date": ledger["briefing_date"],
        "slot": ledger["slot"],
        "generation_id": ledger["generation_id"],
        "source_commit": ledger["source_commit"],
        "validated_at_kst": validated_at_kst,
        "completion_state": "VALIDATED",
        "verdict": "PASS",
        "briefing_sha256": digest_bytes(briefing_bytes),
        "claim_ledger_sha256": digest_bytes(claims_body),
        "display_proposal_sha256": digest_bytes(display_body),
        "unknown_escalation": (
            "ESCALATE" if any(c["kind"] == "UNKNOWN" for c in ledger["claims"])
            else "NONE"
        ),
        "corrections": [],
        "post_delivery": None,
        "safety_attestation": SAFETY_ATTESTATION,
    }


def notion_receipt(portal_envelope: dict, *, portal_state: str, portal_url: str) -> dict:
    if portal_envelope.get("schema_version") != PORTAL_SCHEMA:
        raise ChainError("NOTION_ADAPTER_PORTAL_SCHEMA_INVALID")
    if portal_state not in {"APPLIED", "NO_CHANGE"}:
        raise ChainError("NOTION_ADAPTER_PORTAL_NOT_VERIFIED")
    if not isinstance(portal_url, str) or not portal_url.startswith("https://"):
        raise ChainError("NOTION_ADAPTER_PORTAL_URL_INVALID")
    _assert_execution_locked(portal_envelope)
    receipt_id = (
        f"{portal_envelope['projection_id']}:"
        f"{digest(portal_envelope)}"
    )
    return {
        "schema_version": NOTION_RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "projection_id": portal_envelope["projection_id"],
        "briefing_date": portal_envelope["briefing_date"],
        "slot": portal_envelope["slot"],
        "source_commit": portal_envelope["source_commit"],
        "generation_id": portal_envelope["generation_id"],
        "portal_envelope_sha256": digest(portal_envelope),
        "portal_state": portal_state,
        "portal_url": portal_url,
        "readback_verified": True,
        "duplicate_count": 0,
        "safety_attestation": SAFETY_ATTESTATION,
    }


def publish_notion_receipt(
    repo_root: Path,
    receipt: dict,
    *,
    out_root: str = "data/briefing/notion_receipts_v2",
) -> dict:
    """Persist a content-bound receipt with exact replay idempotency."""
    if receipt.get("schema_version") != NOTION_RECEIPT_SCHEMA:
        raise ChainError("NOTION_RECEIPT_SCHEMA_INVALID")
    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or not receipt_id:
        raise ChainError("NOTION_RECEIPT_ID_INVALID")
    _assert_execution_locked(receipt)
    slot = str(receipt.get("slot", "")).lower()
    if slot not in {"am", "pm"} or DATE.fullmatch(str(receipt.get("briefing_date"))) is None:
        raise ChainError("NOTION_RECEIPT_IDENTITY_INVALID")
    root = repo_root / _safe_path(out_root)
    identity_hash = digest_bytes(receipt_id.encode("utf-8"))
    path = root / receipt["briefing_date"] / slot / f"{identity_hash}.json"
    body = _file_body(receipt)
    if path.exists():
        if path.read_bytes() != body:
            raise ChainError("NOTION_RECEIPT_DUPLICATE_CONFLICT")
        return {
            "result": "NO_CHANGE",
            "path": path.relative_to(repo_root).as_posix(),
            "duplicate_count": 0,
        }
    _atomic_write(path, body)
    return {
        "result": "APPLIED",
        "path": path.relative_to(repo_root).as_posix(),
        "duplicate_count": 0,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--repo-root", default=".")
    build.add_argument("--source-commit", required=True)
    build.add_argument("--packet-path", required=True)
    build.add_argument("--briefing-path", required=True)
    build.add_argument("--decision-date", required=True)
    build.add_argument("--slot", choices=tuple(SLOT_TO_LEGACY), required=True)
    build.add_argument("--module-registry")
    build.add_argument("--major-event-registry-path")
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "build":
        repo_root = Path(args.repo_root).resolve()
        envelope = build_input_envelope(
            repo_root,
            source_commit=args.source_commit,
            packet_path=args.packet_path,
            briefing_path=args.briefing_path,
            decision_date=args.decision_date,
            slot=args.slot,
            registry_path=(Path(args.module_registry) if args.module_registry else None),
            major_event_registry_path=args.major_event_registry_path,
        )
        briefing_bytes = _git_bytes(repo_root, args.source_commit, args.briefing_path)
        result = publish_chain(
            repo_root,
            build_chain_artifacts(envelope, briefing_bytes=briefing_bytes),
        )
        for key in ("result", "chain_id", "path", "revision", "duplicate_count"):
            print(f"{key}={result[key]}")
        return 0
    raise ChainError("CORE_COMMAND_INVALID")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ChainError as exc:
        print(f"STOP:{exc}", file=os.sys.stderr)
        raise SystemExit(2) from None
