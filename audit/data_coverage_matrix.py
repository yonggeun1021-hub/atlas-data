#!/usr/bin/env python3
"""Build the P4-01 source/freshness/cost/fallback audit matrix.

The matrix inventories every current Regime axis, Discovery WBS input, and
authoritative Rule SSOT member.  It records unresolved dimensions as gaps; it
does not select a source, ratify a policy, connect an evaluator, or authorize
paid data, Production, or trading.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "data_coverage_matrix_contract.json"
REGISTRY_PATH = ROOT / "config" / "data_coverage_registry.json"
REGIME_PATH = ROOT / "config" / "regime_output_contract.json"
RULES_PATH = ROOT / "config" / "rules.json"

CONSUMER_ID = re.compile(
    r"^(?:REGIME:(?:US|KR|CRYPTO):[A-Z_]+|DISCOVERY:P3-[0-9]{2}|RULE-[0-9]{4})$"
)
SOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DataCoverageError(RuntimeError):
    """Fail-closed matrix contract, registry, or derivation violation."""


def fail(code: str, detail: str) -> None:
    raise DataCoverageError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, f"{path}: {exc}")
    if not isinstance(value, dict):
        fail(code, f"{path}: object required")
    return value


def ensure_no_float(value: object, label: str = "input") -> None:
    if isinstance(value, (float, Decimal)):
        fail("FLOAT_NOT_ALLOWED", label)
    if isinstance(value, dict):
        for key, item in value.items():
            ensure_no_float(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_no_float(item, f"{label}[{index}]")


def canonical_bytes(value: object) -> bytes:
    ensure_no_float(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))
    return encoded.encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = load_json(path, "CONTRACT_INVALID")
    pinned = {
        "schema_version": 2,
        "contract_version": "data_coverage_matrix/v2",
        "expected_consumer_counts": {
            "REGIME": 15,
            "DISCOVERY": 11,
            "RULE": 25,
            "TOTAL": 51,
        },
        "regime_source_contract": "config/regime_output_contract.json",
        "rule_source_contract": "config/rules.json",
        "required_dimensions": ["source", "freshness", "cost", "fallback"],
        "source_statuses": [
            "QUALIFIED",
            "PARTIAL",
            "UNRATIFIED",
            "UNRESOLVED",
            "UNRECORDED",
            "NOT_IMPLEMENTED",
        ],
        "freshness_statuses": [
            "DEFINED",
            "PARTIAL",
            "UNRATIFIED",
            "UNRESOLVED",
        ],
        "cost_statuses": [
            "FREE",
            "FREE_TIER",
            "PAID_REAPPROVAL_REQUIRED",
            "UNRESOLVED",
        ],
        "fallback_statuses": ["DEFINED", "UNRATIFIED", "UNRESOLVED"],
        "operationally_complete_statuses": {
            "source": ["QUALIFIED"],
            "freshness": ["DEFINED"],
            "cost": ["FREE", "FREE_TIER"],
            "fallback": ["DEFINED"],
        },
        "paid_source_policy": (
            "USER_REAPPROVAL_REQUIRED_BEFORE_SELECTION_OR_PURCHASE"
        ),
        "authority_mode": "AUDIT_ONLY_NO_RUNTIME_AUTHORITY",
        "dimension_claim_scope": "DECLARED_AUDIT_CLASSIFICATION_ONLY",
        "source_evidence_provenance_mode": (
            "EXACT_CONTENT_FIRST_SEEN_FULL_GIT_HISTORY"
        ),
    }
    if set(contract) != set(pinned) or any(
        contract.get(key) != value for key, value in pinned.items()
    ):
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def validate_status_block(
    block: object,
    statuses: list,
    detail_key: str,
    label: str,
) -> dict:
    if not isinstance(block, dict) or set(block) != {"status", detail_key}:
        fail("REGISTRY_INVALID", f"{label} schema")
    if block["status"] not in statuses:
        fail("REGISTRY_INVALID", f"{label} status")
    detail = block[detail_key]
    if detail is not None and (not isinstance(detail, str) or not detail):
        fail("REGISTRY_INVALID", f"{label} {detail_key}")
    if block["status"] == "DEFINED" and detail is None:
        fail("REGISTRY_INVALID", f"{label} defined without {detail_key}")
    return block


def validate_source_refs(
    source_ids: object,
    source_status: object,
    sources: dict,
    contract: dict,
    label: str,
) -> list:
    if source_status not in contract["source_statuses"]:
        fail("REGISTRY_INVALID", f"{label} source_status")
    if not isinstance(source_ids, list) or source_ids != sorted(set(source_ids)):
        fail("REGISTRY_INVALID", f"{label} source_ids order or duplicate")
    if any(source_id not in sources for source_id in source_ids):
        fail("SOURCE_REF_UNKNOWN", label)
    mapped = {"QUALIFIED", "PARTIAL", "UNRATIFIED"}
    if (source_status in mapped) != bool(source_ids):
        fail("SOURCE_STATUS_CONTRADICTION", label)
    return source_ids


def _git(*args: str, binary: bool = False) -> bytes | str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        fail("SOURCE_EVIDENCE_PROVENANCE_UNVERIFIED", "git history unavailable")
    return completed.stdout if binary else completed.stdout.decode("utf-8")


def verify_default_document_at_head(path: Path, code: str) -> None:
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    if _git("status", "--porcelain", "--", relative).strip():
        fail(code, f"{relative}: dirty")
    blob = _git("show", f"HEAD:{relative}", binary=True)
    if blob != path.read_bytes():
        fail(code, f"{relative}: HEAD blob mismatch")


def _repo_relative_evidence_path(value: object, source_id: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value:
        fail("REGISTRY_INVALID", f"source {source_id} evidence_ref")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        fail("SOURCE_EVIDENCE_PATH_INVALID", source_id)
    resolved = (ROOT / relative).resolve()
    try:
        normalized = resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        fail("SOURCE_EVIDENCE_PATH_INVALID", source_id)
    if normalized != relative.as_posix() or not resolved.is_file():
        fail("SOURCE_EVIDENCE_MISSING", str(value))
    return resolved, normalized


def _exact_content_first_seen(path: str, current: bytes) -> tuple[str, str]:
    commits = [
        item
        for item in _git("log", "--reverse", "--format=%H", "--", path).splitlines()
        if item
    ]
    if not commits:
        fail("SOURCE_EVIDENCE_PROVENANCE_UNVERIFIED", f"{path}: no history")
    for commit in commits:
        try:
            blob = subprocess.run(
                ["git", "show", f"{commit}:{path}"],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            continue
        if blob == current:
            timestamp = _git("show", "-s", "--format=%cI", commit).strip()
            return commit, timestamp
    fail("SOURCE_EVIDENCE_PROVENANCE_UNVERIFIED", f"{path}: exact blob absent")


def validate_source_evidence(item: dict, source_id: str) -> dict:
    evidence, relative = _repo_relative_evidence_path(
        item["evidence_ref"], source_id
    )
    if _git("status", "--porcelain", "--", relative).strip():
        fail("SOURCE_EVIDENCE_DIRTY", relative)
    current = evidence.read_bytes()
    digest = hashlib.sha256(current).hexdigest()
    if SHA256.fullmatch(item["evidence_sha256"] or "") is None:
        fail("REGISTRY_INVALID", f"source {source_id} evidence_sha256")
    if digest != item["evidence_sha256"]:
        fail("SOURCE_EVIDENCE_HASH_MISMATCH", relative)
    declared_commit = item["evidence_first_seen_commit"]
    declared_at = item["evidence_first_seen_at"]
    if FULL_GIT_SHA.fullmatch(declared_commit or "") is None:
        fail("REGISTRY_INVALID", f"source {source_id} first_seen_commit")
    if not isinstance(declared_at, str) or not declared_at:
        fail("REGISTRY_INVALID", f"source {source_id} first_seen_at")
    actual_commit, actual_at = _exact_content_first_seen(relative, current)
    if (declared_commit, declared_at) != (actual_commit, actual_at):
        fail("SOURCE_EVIDENCE_FIRST_SEEN_MISMATCH", relative)
    return {
        "evidence_ref": relative,
        "evidence_sha256": digest,
        "evidence_first_seen_commit": actual_commit,
        "evidence_first_seen_at": actual_at,
        "provenance_status": "EXACT_CONTENT_FIRST_SEEN_VERIFIED",
    }


def validate_sources(registry: dict, contract: dict) -> dict:
    items = registry.get("sources")
    if not isinstance(items, list) or not items:
        fail("REGISTRY_INVALID", "sources")
    ids = []
    sources = {}
    required = {
        "source_id",
        "name",
        "cost_status",
        "evidence_ref",
        "evidence_sha256",
        "evidence_first_seen_commit",
        "evidence_first_seen_at",
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != required:
            fail("REGISTRY_INVALID", f"source {index} schema")
        source_id = item["source_id"]
        if not isinstance(source_id, str) or SOURCE_ID.fullmatch(source_id) is None:
            fail("REGISTRY_INVALID", f"source {index} id")
        if item["cost_status"] not in contract["cost_statuses"]:
            fail("REGISTRY_INVALID", f"source {source_id} cost")
        for key in ("name",):
            if not isinstance(item[key], str) or not item[key]:
                fail("REGISTRY_INVALID", f"source {source_id} {key}")
        verified_item = dict(item)
        verified_item["verified_evidence_provenance"] = validate_source_evidence(
            item, source_id
        )
        ids.append(source_id)
        sources[source_id] = verified_item
    if ids != sorted(set(ids)):
        fail("REGISTRY_INVALID", "source order or duplicate")
    return sources


def validate_regime_consumers(
    registry: dict,
    regime_contract: dict,
    sources: dict,
    contract: dict,
) -> list:
    items = registry.get("regime_consumers")
    if not isinstance(items, list):
        fail("REGISTRY_INVALID", "regime_consumers")
    required = {
        "consumer_id",
        "market",
        "axis",
        "input_requirement",
        "source_ids",
        "source_status",
        "freshness",
        "fallback",
        "note",
    }
    expected = sorted(
        f"REGIME:{market}:{axis}"
        for market in regime_contract["markets"]
        for axis in regime_contract["required_axes"]
    )
    observed = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != required:
            fail("REGISTRY_INVALID", f"regime {index} schema")
        consumer_id = item["consumer_id"]
        if consumer_id != f"REGIME:{item['market']}:{item['axis']}":
            fail("REGISTRY_INVALID", f"regime {index} identity")
        validate_source_refs(
            item["source_ids"],
            item["source_status"],
            sources,
            contract,
            consumer_id,
        )
        validate_status_block(
            item["freshness"],
            contract["freshness_statuses"],
            "policy",
            f"{consumer_id}.freshness",
        )
        validate_status_block(
            item["fallback"],
            contract["fallback_statuses"],
            "behavior",
            f"{consumer_id}.fallback",
        )
        for key in ("input_requirement", "note"):
            if not isinstance(item[key], str) or not item[key]:
                fail("REGISTRY_INVALID", f"{consumer_id} {key}")
        observed.append(consumer_id)
    if observed != expected:
        fail("REGIME_INVENTORY_INCOMPLETE", f"expected={expected} observed={observed}")
    return items


def validate_discovery_consumers(
    registry: dict,
    sources: dict,
    contract: dict,
) -> list:
    items = registry.get("discovery_consumers")
    if not isinstance(items, list):
        fail("REGISTRY_INVALID", "discovery_consumers")
    required = {
        "consumer_id",
        "wbs_url",
        "work_item",
        "markets",
        "input_requirement",
        "source_ids",
        "source_status",
        "freshness",
        "fallback",
        "note",
    }
    expected = [f"DISCOVERY:P3-{number:02d}" for number in range(1, 12)]
    observed = []
    allowed_markets = {"Common", "US", "Korea", "Crypto"}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != required:
            fail("REGISTRY_INVALID", f"discovery {index} schema")
        consumer_id = item["consumer_id"]
        if CONSUMER_ID.fullmatch(consumer_id or "") is None:
            fail("REGISTRY_INVALID", f"discovery {index} identity")
        if not isinstance(item["wbs_url"], str) or not item["wbs_url"].startswith(
            "https://app.notion.com/"
        ):
            fail("REGISTRY_INVALID", f"{consumer_id} wbs_url")
        markets = item["markets"]
        if (
            not isinstance(markets, list)
            or not markets
            or len(markets) != len(set(markets))
            or any(market not in allowed_markets for market in markets)
        ):
            fail("REGISTRY_INVALID", f"{consumer_id} markets")
        validate_source_refs(
            item["source_ids"],
            item["source_status"],
            sources,
            contract,
            consumer_id,
        )
        validate_status_block(
            item["freshness"],
            contract["freshness_statuses"],
            "policy",
            f"{consumer_id}.freshness",
        )
        validate_status_block(
            item["fallback"],
            contract["fallback_statuses"],
            "behavior",
            f"{consumer_id}.fallback",
        )
        for key in ("work_item", "input_requirement", "note"):
            if not isinstance(item[key], str) or not item[key]:
                fail("REGISTRY_INVALID", f"{consumer_id} {key}")
        observed.append(consumer_id)
    if observed != expected:
        fail(
            "DISCOVERY_INVENTORY_INCOMPLETE",
            f"expected={expected} observed={observed}",
        )
    return items


def validate_rule_mapping(
    registry: dict,
    rules_contract: dict,
    sources: dict,
    contract: dict,
) -> tuple[dict, dict, dict]:
    mapping = registry.get("rule_mapping")
    if not isinstance(mapping, dict) or set(mapping) != {
        "source_refs",
        "freshness",
        "fallback",
        "note",
    }:
        fail("REGISTRY_INVALID", "rule_mapping schema")
    validate_status_block(
        mapping["freshness"],
        contract["freshness_statuses"],
        "policy",
        "rule_mapping.freshness",
    )
    validate_status_block(
        mapping["fallback"],
        contract["fallback_statuses"],
        "behavior",
        "rule_mapping.fallback",
    )
    if not isinstance(mapping["note"], str) or not mapping["note"]:
        fail("REGISTRY_INVALID", "rule_mapping note")

    rules = rules_contract.get("rules")
    if (
        not isinstance(rules, list)
        or rules_contract.get("rule_count") != len(rules)
        or len(rules) != contract["expected_consumer_counts"]["RULE"]
    ):
        fail("RULE_INVENTORY_INCOMPLETE", "rule_count")
    rule_ids = [item.get("rule_id") for item in rules if isinstance(item, dict)]
    if len(rule_ids) != len(rules) or rule_ids != sorted(set(rule_ids)):
        fail("RULE_INVENTORY_INCOMPLETE", "rule ids")

    refs = mapping["source_refs"]
    if not isinstance(refs, list):
        fail("REGISTRY_INVALID", "rule source_refs")
    ref_ids = []
    source_refs = {}
    for index, item in enumerate(refs):
        if not isinstance(item, dict) or set(item) != {"rule_id", "source_ids"}:
            fail("REGISTRY_INVALID", f"rule source_ref {index}")
        rule_id = item["rule_id"]
        if rule_id not in rule_ids:
            fail("RULE_SOURCE_REF_UNKNOWN", str(rule_id))
        validate_source_refs(
            item["source_ids"],
            "QUALIFIED",
            sources,
            contract,
            rule_id,
        )
        ref_ids.append(rule_id)
        source_refs[rule_id] = item["source_ids"]
    if ref_ids != sorted(set(ref_ids)):
        fail("REGISTRY_INVALID", "rule source_ref order or duplicate")
    resolved = sorted(
        item["rule_id"]
        for item in rules
        if item.get("source_qualification") == "SOURCE_RESOLVED"
    )
    if ref_ids != resolved:
        fail("RULE_RESOLVED_SOURCE_MAP_INCOMPLETE", f"expected={resolved} observed={ref_ids}")
    return source_refs, mapping["freshness"], mapping["fallback"]


def cost_status(source_ids: list, sources: dict) -> str:
    if not source_ids:
        return "UNRESOLVED"
    statuses = {sources[source_id]["cost_status"] for source_id in source_ids}
    for status in (
        "PAID_REAPPROVAL_REQUIRED",
        "UNRESOLVED",
        "FREE_TIER",
        "FREE",
    ):
        if status in statuses:
            return status
    fail("COST_STATUS_INVALID", repr(statuses))


def base_entry(
    consumer_id: str,
    layer: str,
    requirement: str,
    source_ids: list,
    source_status: str,
    freshness: dict,
    fallback: dict,
    note: str,
    sources: dict,
) -> dict:
    return {
        "consumer_id": consumer_id,
        "layer": layer,
        "input_requirement": requirement,
        "source": {
            "status": source_status,
            "source_ids": source_ids,
        },
        "freshness": freshness,
        "cost": {"status": cost_status(source_ids, sources)},
        "fallback": fallback,
        "note": note,
    }


def build_entries(
    registry: dict,
    rules_contract: dict,
    sources: dict,
    source_refs: dict,
    rule_freshness: dict,
    rule_fallback: dict,
) -> list:
    entries = []
    for item in registry["regime_consumers"]:
        entry = base_entry(
            item["consumer_id"],
            "REGIME",
            item["input_requirement"],
            item["source_ids"],
            item["source_status"],
            item["freshness"],
            item["fallback"],
            item["note"],
            sources,
        )
        entry["market"] = item["market"]
        entry["axis"] = item["axis"]
        entries.append(entry)
    for item in registry["discovery_consumers"]:
        entry = base_entry(
            item["consumer_id"],
            "DISCOVERY",
            item["input_requirement"],
            item["source_ids"],
            item["source_status"],
            item["freshness"],
            item["fallback"],
            item["note"],
            sources,
        )
        entry["work_item"] = item["work_item"]
        entry["markets"] = item["markets"]
        entry["wbs_url"] = item["wbs_url"]
        entries.append(entry)
    for rule in rules_contract["rules"]:
        rule_id = rule["rule_id"]
        qualification = rule.get("source_qualification")
        if qualification == "SOURCE_RESOLVED":
            source_status = "QUALIFIED"
        elif qualification == "SOURCE_UNRESOLVED":
            source_status = "UNRESOLVED"
        elif qualification is None:
            source_status = "UNRECORDED"
        else:
            fail("RULE_SOURCE_STATUS_INVALID", f"{rule_id}: {qualification!r}")
        entry = base_entry(
            rule_id,
            "RULE",
            rule["condition_text"],
            source_refs.get(rule_id, []),
            source_status,
            rule_freshness,
            rule_fallback,
            "Rule mapping mirrors SSOT state; no source or timing is inferred.",
            sources,
        )
        entry["subject"] = rule["subject"]
        entry["upstream_state"] = {
            "definition_status": rule["definition_status"],
            "data_status": rule["data_status"],
            "source_qualification": qualification,
            "evaluator_status": rule["evaluator_status"],
        }
        entries.append(entry)
    entries.sort(key=lambda item: item["consumer_id"])
    ids = [item["consumer_id"] for item in entries]
    if ids != sorted(set(ids)) or any(
        CONSUMER_ID.fullmatch(consumer_id) is None for consumer_id in ids
    ):
        fail("CONSUMER_ID_INVALID", "order, duplicate, or format")
    return entries


def status_counts(entries: list, contract: dict) -> dict:
    vocabulary = {
        "source": contract["source_statuses"],
        "freshness": contract["freshness_statuses"],
        "cost": contract["cost_statuses"],
        "fallback": contract["fallback_statuses"],
    }
    result = {}
    for dimension in contract["required_dimensions"]:
        counts = {status: 0 for status in vocabulary[dimension]}
        for entry in entries:
            counts[entry[dimension]["status"]] += 1
        result[dimension] = counts
    return result


def gap_rows(entries: list, contract: dict) -> list:
    complete = contract["operationally_complete_statuses"]
    gaps = []
    for entry in entries:
        dimensions = [
            dimension
            for dimension in contract["required_dimensions"]
            if entry[dimension]["status"] not in complete[dimension]
        ]
        if dimensions:
            gaps.append(
                {
                    "consumer_id": entry["consumer_id"],
                    "gap_dimensions": dimensions,
                }
            )
    return gaps


def build_matrix(
    contract_path: Path = CONTRACT_PATH,
    registry_path: Path = REGISTRY_PATH,
    regime_path: Path = REGIME_PATH,
    rules_path: Path = RULES_PATH,
) -> dict:
    defaults = (
        (Path(contract_path), CONTRACT_PATH, "CONTRACT_PROVENANCE_UNVERIFIED"),
        (Path(registry_path), REGISTRY_PATH, "REGISTRY_PROVENANCE_UNVERIFIED"),
        (Path(regime_path), REGIME_PATH, "REGIME_PROVENANCE_UNVERIFIED"),
        (Path(rules_path), RULES_PATH, "RULE_PROVENANCE_UNVERIFIED"),
    )
    for supplied, canonical, code in defaults:
        if supplied.resolve() == canonical.resolve():
            verify_default_document_at_head(canonical, code)
    contract = load_contract(contract_path)
    registry = load_json(registry_path, "REGISTRY_INVALID")
    regime_contract = load_json(regime_path, "REGIME_CONTRACT_INVALID")
    rules_contract = load_json(rules_path, "RULE_CONTRACT_INVALID")
    ensure_no_float(registry, "registry")
    ensure_no_float(regime_contract, "regime_contract")
    ensure_no_float(rules_contract, "rules_contract")

    if set(registry) != {
        "schema_version",
        "registry_version",
        "inventory_basis",
        "sources",
        "regime_consumers",
        "discovery_consumers",
        "rule_mapping",
    }:
        fail("REGISTRY_INVALID", "top-level schema")
    if registry["schema_version"] != 2:
        fail("REGISTRY_INVALID", "schema_version")
    if not isinstance(registry["registry_version"], str):
        fail("REGISTRY_INVALID", "registry_version")
    expected_basis = {
        "regime": contract["regime_source_contract"],
        "discovery": "Atlas Master WBS P3 Discovery snapshot 2026-08-20",
        "rule": contract["rule_source_contract"],
    }
    if registry["inventory_basis"] != expected_basis:
        fail("REGISTRY_INVALID", "inventory_basis")
    if (
        regime_contract.get("contract_version") != "regime_output/v1"
        or regime_contract.get("markets") != ["US", "KR", "CRYPTO"]
        or regime_contract.get("required_axes")
        != ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
    ):
        fail("REGIME_CONTRACT_INVALID", "identity or vocabulary")
    if rules_contract.get("authority") is not True:
        fail("RULE_CONTRACT_INVALID", "authority")

    sources = validate_sources(registry, contract)
    validate_regime_consumers(registry, regime_contract, sources, contract)
    validate_discovery_consumers(registry, sources, contract)
    source_refs, rule_freshness, rule_fallback = validate_rule_mapping(
        registry, rules_contract, sources, contract
    )
    entries = build_entries(
        registry,
        rules_contract,
        sources,
        source_refs,
        rule_freshness,
        rule_fallback,
    )
    layer_counts = {
        layer: sum(entry["layer"] == layer for entry in entries)
        for layer in ("REGIME", "DISCOVERY", "RULE")
    }
    expected_counts = contract["expected_consumer_counts"]
    observed_counts = layer_counts | {"TOTAL": len(entries)}
    if observed_counts != expected_counts:
        fail(
            "CONSUMER_INVENTORY_INCOMPLETE",
            f"expected={expected_counts} observed={observed_counts}",
        )
    gaps = gap_rows(entries, contract)
    paid = [
        entry["consumer_id"]
        for entry in entries
        if entry["cost"]["status"] == "PAID_REAPPROVAL_REQUIRED"
    ]
    return {
        "schema_version": 2,
        "contract_version": contract["contract_version"],
        "registry_version": registry["registry_version"],
        "authority_mode": contract["authority_mode"],
        "dimension_claim_scope": contract["dimension_claim_scope"],
        "source_evidence_provenance_mode": contract[
            "source_evidence_provenance_mode"
        ],
        "inventory_basis": registry["inventory_basis"],
        "input_sha256": {
            "contract": canonical_sha256(contract),
            "registry": canonical_sha256(registry),
            "regime_contract": canonical_sha256(regime_contract),
            "rule_contract": canonical_sha256(rules_contract),
        },
        "inventory_complete": True,
        "operationally_complete": not gaps,
        "runtime_evidence_eligibility": "NOT_AUTHORIZED_BY_THIS_AUDIT",
        "consumer_counts": observed_counts,
        "dimension_status_counts": status_counts(entries, contract),
        "gap_count": len(gaps),
        "gaps": gaps,
        "source_catalog": [sources[source_id] for source_id in sorted(sources)],
        "entries": entries,
        "paid_source_policy": contract["paid_source_policy"],
        "paid_source_reapproval_required_for": paid,
        "source_selection_authorized": False,
        "source_qualification_authorized": False,
        "freshness_runtime_use_authorized": False,
        "fallback_runtime_use_authorized": False,
        "freshness_policy_ratification_authorized": False,
        "fallback_policy_ratification_authorized": False,
        "evaluator_wiring_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def validate_matrix(
    matrix: object,
    contract_path: Path = CONTRACT_PATH,
    registry_path: Path = REGISTRY_PATH,
    regime_path: Path = REGIME_PATH,
    rules_path: Path = RULES_PATH,
) -> dict:
    expected = build_matrix(
        contract_path=contract_path,
        registry_path=registry_path,
        regime_path=regime_path,
        rules_path=rules_path,
    )
    if not isinstance(matrix, dict):
        fail("MATRIX_INVALID", "object required")
    if canonical_bytes(matrix) != canonical_bytes(expected):
        fail("MATRIX_DERIVATION_MISMATCH", "matrix != input-derived matrix")
    return matrix


def write_output(payload: dict, target: Path) -> Path:
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.tmp.",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--out", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("matrix", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            result = validate_matrix(load_json(args.matrix, "MATRIX_INVALID"))
        else:
            result = build_matrix()
        if args.command == "build" and args.out is not None:
            print(write_output(result, args.out))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except DataCoverageError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
