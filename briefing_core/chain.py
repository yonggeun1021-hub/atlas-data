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
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any
import zlib

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

CLAIM_SOURCE_BINDING_SCHEMA = "briefing_claim_source_binding/1"
GRADE_PRIMARY_DIRECT = "PRIMARY_DIRECT"
GRADE_OFFICIAL_STATEMENT_RELAY = "OFFICIAL_STATEMENT_RELAY"
GRADE_INTERNAL_LOGIC_CHECK = "INTERNAL_LOGIC_CHECK"
GRADE_UNKNOWN = "UNKNOWN"
RETAINED_MANIFEST_NAME = "_manifest.json"
RETAINED_DIGEST_NAME = "_sha256.txt"

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


def _delivery_claims(packet: dict, packet_ref: str) -> list[dict]:
    """Project bounded delivery facts from the exact sealed packet.

    The generic module claims below prove only that a component is present.
    They do not identify the independently dated observations or the numeric
    facts that the human-facing briefing renders.  Keep those facts in the
    existing ``claim_ledger/1`` claim shape and bind every verified statement
    to the exact packet bytes.  UNKNOWN statements intentionally carry no
    source reference: they describe conclusions the packet does not prove.

    ``_claim_source_bindings`` then attaches the exact committed primary
    provider evidence and the separate measurement/capture clocks for the
    externally sourced subset of these claims.
    """

    claims: list[dict] = []

    def fact(claim_id: str, statement: str) -> None:
        claims.append({
            "claim_id": claim_id,
            "kind": "FACT",
            "statement": statement,
            "status": "VERIFIED",
            "source_ref_paths": [packet_ref],
        })

    def unknown(claim_id: str, statement: str) -> None:
        claims.append({
            "claim_id": claim_id,
            "kind": "UNKNOWN",
            "statement": statement,
            "status": "UNKNOWN",
            "source_ref_paths": [],
        })

    components = {
        row.get("component_id"): row
        for row in packet.get("components", [])
        if isinstance(row, dict) and isinstance(row.get("component_id"), str)
    }
    counts = packet.get("component_status_counts")
    if isinstance(counts, dict):
        rendered = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
        fact(
            "numeric.components.status_counts",
            f"The sealed packet reports component status counts: {rendered}.",
        )

    free_market = components.get("FREE_MARKET_DATA") or {}
    free_packet = free_market.get("packet") or {}
    us_reference = free_packet.get("us_market_reference") or {}
    us_session_date = us_reference.get("as_of_session_date")
    vix = free_packet.get("vixcls") or {}
    vix_date = vix.get("date")
    if isinstance(us_session_date, str):
        fact(
            "freshness.us.market_session_date",
            f"The representative US market session evidence is dated {us_session_date}.",
        )
    if isinstance(vix_date, str):
        fact(
            "freshness.us.vix_observation_date",
            f"The FRED VIXCLS observation is dated {vix_date}.",
        )
    if vix.get("value") is not None:
        fact(
            "numeric.us.vixcls",
            f"The sealed packet reports VIXCLS={vix.get('value')} for {vix_date or 'UNKNOWN'}.",
        )
    if us_session_date and vix_date and us_session_date != vix_date:
        unknown(
            "boundary.us.independent_evidence_clocks",
            "The US market-session date and FRED VIX observation date are independent clocks; "
            "their mismatch does not establish a missing market session or a market-wide conclusion.",
        )
    if free_packet.get("scope_warning"):
        unknown(
            "boundary.us.market_wide_scope",
            "The retained IEX evidence is partial and does not establish a market-wide or causal US conclusion.",
        )

    btc_trend = components.get("BTC_TREND") or {}
    trend_packet = btc_trend.get("packet") or {}
    trend_date = trend_packet.get("latest_finalized_day")
    if isinstance(trend_date, str):
        fact(
            "freshness.crypto.btc_trend_finalized_date",
            f"The BTC trend measurement uses finalized daily closes through {trend_date}.",
        )
    elif btc_trend:
        frozen_sources = packet.get("frozen_sources")
        frozen_sources = frozen_sources if isinstance(frozen_sources, dict) else {}
        frozen_trend = frozen_sources.get("BTC_TREND")
        frozen_trend = frozen_trend if isinstance(frozen_trend, dict) else {}
        prior_trend = frozen_trend.get("prior_confirmed_reference")
        prior_trend = prior_trend if isinstance(prior_trend, dict) else {}
        prior_trend_date = prior_trend.get("measurement_date")
        trend_as_of = btc_trend.get("as_of_date")
        if (
            isinstance(prior_trend_date, str)
            and DATE.fullmatch(prior_trend_date) is not None
            and _iso_date(prior_trend_date) is not None
        ):
            trend_unknown = (
                "The current BTC trend finalized measurement date is UNKNOWN; "
                f"the exact packet records {prior_trend_date} only as a historical prior "
                "reference. "
            )
        else:
            trend_unknown = "The current BTC trend finalized measurement date is UNKNOWN. "
        trend_unknown += (
            "The current component as_of_date is null."
            if trend_as_of is None
            else "The current component as_of_date is present but is not substituted for it."
        )
        unknown(
            "freshness.crypto.btc_trend_finalized_date",
            trend_unknown,
        )
    if trend_packet.get("direction") is not None or trend_packet.get("dma_200") is not None:
        fact(
            "numeric.crypto.btc_trend",
            "The sealed packet reports BTC trend "
            f"direction={trend_packet.get('direction')} and dma_200={trend_packet.get('dma_200')}.",
        )

    btc_risk = components.get("BTC_RISK") or {}
    risk_packet = btc_risk.get("packet") or {}
    risk_point = risk_packet.get("risk_point") or {}
    risk_date = (
        risk_packet.get("latest_finalized_day")
        or risk_point.get("as_of_date")
    )
    if isinstance(risk_date, str):
        fact(
            "freshness.crypto.btc_risk_finalized_date",
            f"The BTC risk measurement uses finalized daily closes through {risk_date}.",
        )
    elif btc_risk:
        frozen_sources = packet.get("frozen_sources")
        frozen_sources = frozen_sources if isinstance(frozen_sources, dict) else {}
        frozen_risk = frozen_sources.get("BTC_RISK")
        frozen_risk = frozen_risk if isinstance(frozen_risk, dict) else {}
        prior_risk = frozen_risk.get("prior_confirmed_reference")
        prior_risk = prior_risk if isinstance(prior_risk, dict) else {}
        prior_risk_date = prior_risk.get("measurement_date")
        risk_as_of = btc_risk.get("as_of_date")
        if (
            isinstance(prior_risk_date, str)
            and DATE.fullmatch(prior_risk_date) is not None
            and _iso_date(prior_risk_date) is not None
        ):
            risk_unknown = (
                "The current BTC risk finalized measurement date is UNKNOWN; "
                f"the exact packet records {prior_risk_date} only as a historical prior "
                "reference. "
            )
        else:
            risk_unknown = "The current BTC risk finalized measurement date is UNKNOWN. "
        risk_unknown += (
            "The current component as_of_date is null."
            if risk_as_of is None
            else "The current component as_of_date is present but is not substituted for it."
        )
        unknown(
            "freshness.crypto.btc_risk_finalized_date",
            risk_unknown,
        )
    drawdown = risk_point.get("drawdown") or {}
    volatility = risk_point.get("realized_volatility") or {}
    if any(
        value is not None
        for value in (
            drawdown.get("current_fraction"),
            drawdown.get("maximum_fraction"),
            volatility.get("annualized_fraction"),
        )
    ):
        fact(
            "numeric.crypto.btc_risk",
            "The sealed packet reports BTC risk values "
            f"current_drawdown={drawdown.get('current_fraction')}, "
            f"maximum_drawdown={drawdown.get('maximum_fraction')}, and "
            f"annualized_realized_volatility={volatility.get('annualized_fraction')}.",
        )

    stablecoin = components.get("STABLECOIN_NET_ISSUANCE") or {}
    stable_packet = stablecoin.get("packet") or {}
    stable_date = stable_packet.get("observation_date") or stablecoin.get("as_of_date")
    if isinstance(stable_date, str):
        fact(
            "freshness.crypto.stablecoin_observation_date",
            f"The stablecoin net-issuance observation is dated {stable_date}.",
        )
    if (
        stable_packet.get("daily_net_issuance_native_usd_peg") is not None
        or stable_packet.get("weekly_net_issuance_native_usd_peg") is not None
    ):
        fact(
            "numeric.crypto.stablecoin_net_issuance",
            "The sealed packet reports stablecoin net issuance "
            f"daily={stable_packet.get('daily_net_issuance_native_usd_peg')} and "
            f"weekly={stable_packet.get('weekly_net_issuance_native_usd_peg')} "
            f"for {stable_date or 'UNKNOWN'}.",
        )

    korea = components.get("KOREA_MARKET_SIGNALS") or {}
    korea_packet = korea.get("packet") or {}
    confirmed_date = korea_packet.get("as_of_date") or korea.get("as_of_date")
    if isinstance(confirmed_date, str):
        fact(
            "freshness.krx.latest_confirmed_close_date",
            f"The confirmed Korea five-axis market observation is dated {confirmed_date}.",
        )

    post_close = components.get("KRX_POST_CLOSE") or {}
    post_packet = post_close.get("packet") or {}
    post_symbols = [row for row in post_packet.get("symbols", []) if isinstance(row, dict)]
    observed_dates = sorted({
        row.get("latest_observed_day")
        for row in post_symbols
        if isinstance(row.get("latest_observed_day"), str)
    })
    confirmed_dates = sorted({
        row.get("latest_trading_day")
        for row in post_symbols
        if isinstance(row.get("latest_trading_day"), str)
    })
    if observed_dates:
        fact(
            "freshness.krx.post_close_observed_dates",
            "The KRX post-close bundle contains observed, unconfirmed rows dated "
            + ", ".join(observed_dates) + ".",
        )
    if confirmed_dates:
        fact(
            "freshness.krx.post_close_confirmed_history_dates",
            "The KRX post-close decision history remains confirmed only through "
            + ", ".join(confirmed_dates) + ".",
        )
    post_summary = post_packet.get("summary") or {}
    if any(
        post_summary.get(key) is not None
        for key in (
            "observed_symbol_count",
            "decision_eligible_symbol_count",
            "confirmed_same_day_count",
        )
    ):
        fact(
            "numeric.krx.post_close_summary",
            "The KRX post-close bundle reports "
            f"observed_symbols={post_summary.get('observed_symbol_count')}, "
            f"decision_eligible_symbols={post_summary.get('decision_eligible_symbol_count')}, and "
            f"confirmed_same_day={post_summary.get('confirmed_same_day_count')}.",
        )
    if post_packet.get("observation_status") == "observed_unconfirmed":
        unknown(
            "boundary.krx.same_day_confirmation",
            "The same-day KRX post-close rows are observed but unconfirmed and cannot establish "
            "a confirmed close, rule input, or investment conclusion until the existing confirmation path does so.",
        )

    dynamic = components.get("DYNAMIC_CLOCK") or {}
    dynamic_packet = dynamic.get("packet") or {}
    dynamic_date = dynamic_packet.get("decision_date") or packet.get("decision_date")
    markets = dynamic_packet.get("markets") or {}
    aggregate = {"overdue": 0, "due_today": 0, "upcoming": 0, "unclassified": 0, "total": 0}
    if isinstance(dynamic_date, str):
        for market, market_packet in sorted(markets.items()):
            if not isinstance(market_packet, dict):
                continue
            rows = [row for row in market_packet.get("watch_review", []) if isinstance(row, dict)]
            due = {"overdue": 0, "due_today": 0, "upcoming": 0, "unclassified": 0}
            for row in rows:
                next_review = row.get("next_review_at")
                if not isinstance(next_review, str) or DATE.fullmatch(next_review) is None:
                    due["unclassified"] += 1
                elif next_review < dynamic_date:
                    due["overdue"] += 1
                elif next_review == dynamic_date:
                    due["due_today"] += 1
                else:
                    due["upcoming"] += 1
            for key in due:
                aggregate[key] += due[key]
            aggregate["total"] += len(rows)
            fact(
                f"review_due.dynamic_clock.{str(market).lower()}",
                f"At decision date {dynamic_date}, {market} WATCH_REVIEW has "
                f"overdue={due['overdue']}, due_today={due['due_today']}, "
                f"upcoming={due['upcoming']}, unclassified={due['unclassified']}, "
                f"total={len(rows)}.",
            )
        if markets:
            fact(
                "review_due.dynamic_clock.all",
                f"At decision date {dynamic_date}, all WATCH_REVIEW queues have "
                f"overdue={aggregate['overdue']}, due_today={aggregate['due_today']}, "
                f"upcoming={aggregate['upcoming']}, unclassified={aggregate['unclassified']}, "
                f"total={aggregate['total']}.",
            )
            unknown(
                "boundary.dynamic_clock.review_due_not_promotion",
                "A due or overdue WATCH_REVIEW date is a review-routing state only; it does not "
                "authorize candidate promotion, entry, action, order, production, or trading.",
            )

    rotation = components.get("ROTATION_DISCOVERY") or {}
    rotation_packet = rotation.get("packet") or {}
    discovery = rotation_packet.get("discovery") or {}
    signal = rotation_packet.get("signal_observations") or {}
    if discovery or signal:
        fact(
            "numeric.rotation.discovery_summary",
            "The sealed packet reports rotation discovery "
            f"cases={discovery.get('case_count')}, new_candidates={len(discovery.get('new_candidates', []))}, "
            f"existing_candidate_changes={len(discovery.get('existing_candidate_changes', []))}, and "
            f"signal_observations={signal.get('observation_count')}.",
        )
        unknown(
            "boundary.rotation.observation_not_promotion",
            "Rotation discovery and Dynamic Clock observations do not establish candidate promotion "
            "or an investment action when the packet says promotion is not authorized.",
        )

    acceleration = components.get("BUSINESS_ACCELERATION") or {}
    acceleration_packet = acceleration.get("packet") or {}
    for index, series in enumerate(acceleration_packet.get("series", []), start=1):
        if not isinstance(series, dict):
            continue
        fact(
            f"numeric.business_acceleration.series_{index}",
            "The sealed packet reports business-acceleration series "
            f"metric={series.get('metric')}, pattern={series.get('pattern')}, "
            f"values={series.get('values_pct')}, candidate_eligible={series.get('candidate_eligible')}.",
        )

    release = components.get("OFFICIAL_RELEASE_SUMMARY") or {}
    release_packet = release.get("packet") or {}
    release_counts = release_packet.get("counts") or {}
    if release_counts:
        fact(
            "numeric.official_release.summary_counts",
            "The sealed packet reports official-release "
            f"observations={release_counts.get('observed_registered_releases')} and "
            f"summary_items={release_counts.get('observed_summary_items')}.",
        )
    release_item_count = 0
    for observation_index, observation in enumerate(release_packet.get("observations", []), start=1):
        if not isinstance(observation, dict):
            continue
        published_at = observation.get("published_at")
        if isinstance(published_at, str):
            fact(
                f"date.official_release.observation_{observation_index}",
                f"The retained official release for {observation.get('subject')} was published on {published_at}.",
            )
        for item in observation.get("summary_items", []):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            release_item_count += 1
            fact(
                f"official_release.attributed_summary_{release_item_count}",
                "The retained official release states: " + item["text"],
            )
    if release_item_count:
        unknown(
            "boundary.official_release.causality",
            "Company-stated explanations in the retained official release are attributed source facts; "
            "independent market causality, importance, ranking, and investment interpretation remain unverified.",
        )

    return claims


def _iso_date(value: Any) -> str | None:
    """Return a valid calendar date or the UTC date of a zoned instant."""
    if isinstance(value, str) and DATE.fullmatch(value) is not None:
        try:
            return dt.date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    instant = _capture_instant(value)
    return instant.date().isoformat() if instant is not None else None


def _capture_instant(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        instant = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None
        return instant.astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        return None


def _capture_clock_reasons(declared: Any, source: Any, prefix: str) -> list[str]:
    """Compare instants, accepting equivalent explicit timezone offsets."""
    if declared is None or source is None:
        return [f"{prefix}_CLOCK_MISSING"]
    declared_instant, source_instant = _capture_instant(declared), _capture_instant(source)
    if declared_instant is None or source_instant is None:
        return [f"{prefix}_CLOCK_INVALID"]
    if declared_instant != source_instant:
        return [f"{prefix}_CLOCK_MISMATCH"]
    return []


def _declared_generation(value: dict | None) -> str | None:
    """Read only a top-level generation authority, never a nested lineage."""
    if value is None:
        return None
    direct = value.get("generation_id")
    if isinstance(direct, str):
        return direct
    generation = value.get("generation")
    if isinstance(generation, dict) and isinstance(generation.get("generation_id"), str):
        return generation["generation_id"]
    return None


class _EvidenceBinder:
    """Resolve committed provider evidence at the exact pinned source commit.

    A path is never accepted because its name looks right.  Every reference is
    read through ``git show <source_commit>:<path>`` and must match a digest the
    sealed packet or its retained capture manifest already declared, keeping the
    original compressed-file and uncompressed-response digest semantics apart.
    A file that declares a different read-model generation is refused so that a
    later ledger reader cannot mix lineages.
    """

    def __init__(self, repo_root: Path, source_commit: str, generation_id: str) -> None:
        self._repo_root = repo_root
        self._source_commit = source_commit
        self._generation_id = generation_id
        self._bodies: dict[str, bytes | None] = {}
        self._parsed: dict[str, dict | None] = {}
        self.refs: dict[str, str] = {}

    def body(self, path: Any) -> bytes | None:
        if not isinstance(path, str) or not path:
            return None
        if path not in self._bodies:
            try:
                _safe_path(path)
            except ChainError:
                self._bodies[path] = None
            else:
                self._bodies[path] = _git_optional_bytes(
                    self._repo_root, self._source_commit, path
                )
        return self._bodies[path]

    def json(self, path: Any) -> dict | None:
        if not isinstance(path, str) or not path:
            return None
        if path not in self._parsed:
            body = self.body(path)
            value: Any = None
            if body is not None:
                try:
                    value = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    value = None
            self._parsed[path] = value if isinstance(value, dict) else None
        return self._parsed[path]

    @staticmethod
    def _content(path: str, body: bytes) -> bytes | None:
        """Return the provider response bytes behind a retained artifact."""
        if not path.endswith(".gz"):
            return body
        try:
            return gzip.decompress(body)
        except (OSError, EOFError, zlib.error):
            return None

    def bind(
        self,
        path: Any,
        *,
        file_sha256: Any = None,
        content_sha256: Any = None,
        structural: bool = False,
    ) -> tuple[str | None, list[str]]:
        """Bind one committed file and return ``(path, reason_codes)``.

        ``structural`` is reserved for the fixed retained-manifest names inside
        an evidence directory the sealed packet itself declares.  Such a file
        carries no self-declared digest, so it only becomes usable evidence once
        a response digest it names verifies below.
        """
        if not isinstance(path, str) or not path:
            return None, ["SOURCE_PATH_MISSING"]
        if file_sha256 is None and content_sha256 is None and not structural:
            return None, ["SOURCE_DIGEST_NOT_DECLARED"]
        body = self.body(path)
        if body is None:
            return None, ["SOURCE_PATH_UNAVAILABLE"]
        if file_sha256 is not None:
            if not isinstance(file_sha256, str) or SHA256.fullmatch(file_sha256) is None:
                return None, ["SOURCE_FILE_DIGEST_INVALID"]
            if digest_bytes(body) != file_sha256:
                return None, ["SOURCE_FILE_DIGEST_MISMATCH"]
        if content_sha256 is not None:
            if not isinstance(content_sha256, str) or SHA256.fullmatch(content_sha256) is None:
                return None, ["SOURCE_CONTENT_DIGEST_INVALID"]
            content = self._content(path, body)
            if content is None:
                return None, ["SOURCE_COMPRESSED_UNREADABLE"]
            if digest_bytes(content) != content_sha256:
                return None, ["SOURCE_CONTENT_DIGEST_MISMATCH"]
        declared = _declared_generation(self.json(path))
        if declared is not None and declared != self._generation_id:
            return None, ["MIXED_GENERATION"]
        self.refs[path] = digest_bytes(body)
        return path, []


def _retained_manifest(
    binder: _EvidenceBinder, directory: Any
) -> tuple[dict | None, list[str], list[str]]:
    """Read the capture manifest inside a packet-declared evidence directory."""
    if not isinstance(directory, str) or not directory:
        return None, [], ["EVIDENCE_DIRECTORY_NOT_DECLARED"]
    path = f"{directory}/{RETAINED_MANIFEST_NAME}"
    manifest = binder.json(path)
    if manifest is None:
        return None, [], ["RETAINED_MANIFEST_UNAVAILABLE"]
    bound, reasons = binder.bind(path, structural=True)
    if bound is None:
        return None, [], [f"RETAINED_MANIFEST_{code}" for code in reasons]
    return manifest, [bound], []


def _retained_digest_index(
    binder: _EvidenceBinder, directory: str
) -> tuple[dict[str, str], list[str], list[str]]:
    """Read the retained ``sha256sum`` listing of uncompressed responses."""
    path = f"{directory}/{RETAINED_DIGEST_NAME}"
    body = binder.body(path)
    if body is None:
        return {}, [], ["RETAINED_DIGEST_INDEX_MISSING"]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return {}, [], ["RETAINED_DIGEST_INDEX_UNREADABLE"]
    index: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if (len(parts) != 2 or SHA256.fullmatch(parts[0]) is None
                or PurePosixPath(parts[1]).name != parts[1]
                or parts[1] in {".", ".."} or parts[1] in index):
            return {}, [], ["RETAINED_DIGEST_INDEX_MALFORMED"]
        index[parts[1]] = parts[0]
    if not index:
        return {}, [], ["RETAINED_DIGEST_INDEX_MALFORMED"]
    bound, reasons = binder.bind(path, structural=True)
    if bound is None:
        return {}, [], [f"RETAINED_DIGEST_INDEX_{code}" for code in reasons]
    return index, [bound], []


def _retained_response(
    binder: _EvidenceBinder,
    directory: str,
    file_name: Any,
    response_sha256: Any,
    digest_index: dict[str, str],
) -> tuple[str | None, list[str]]:
    """Bind one retained provider response against every retained digest."""
    if not isinstance(file_name, str) or not file_name:
        return None, ["RESPONSE_FILE_NOT_DECLARED"]
    stem = file_name[:-3] if file_name.endswith(".gz") else file_name
    retained = digest_index.get(stem)
    if retained is None:
        return None, ["RETAINED_DIGEST_ENTRY_MISSING"]
    if retained != response_sha256:
        return None, ["RETAINED_DIGEST_DISAGREEMENT"]
    return binder.bind(f"{directory}/{file_name}", content_sha256=response_sha256)


def _derivation_code(
    binder: _EvidenceBinder, *holders: Any
) -> tuple[str | None, list[str]]:
    """Pin the exact committed derivation code declared for a computed value."""
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        derivation = holder.get("derivation")
        if not isinstance(derivation, dict):
            continue
        bound, reasons = binder.bind(
            derivation.get("code_path"), file_sha256=derivation.get("code_sha256")
        )
        if bound is not None:
            return bound, []
        return None, [f"DERIVATION_{code}" for code in reasons]
    return None, ["DERIVATION_CODE_NOT_PINNED"]


def _claim_source_bindings(
    repo_root: Path,
    source_commit: str,
    *,
    packet: dict,
    packet_ref: str,
    claims: list[dict],
    generation_id: str,
    briefing_date: str,
) -> tuple[list[dict], list[dict]]:
    """Bind exact primary evidence and separate clocks to external claims.

    The delivery claims above prove only that the sealed aggregate packet
    reports a value.  An externally sourced fact must additionally name the
    exact committed provider document, and a computed fact must additionally
    name the pinned derivation code, before its grade may describe anything
    stronger than an internal consistency read.  Measurement, capture and first
    availability stay three separate clocks: a missing one is never replaced by
    the generation or briefing date, and capture alone grants no point-in-time
    permission.  Missing, hash-mismatched, unreadable, mixed-generation or
    future evidence leaves that one claim ``UNKNOWN`` without touching the claim
    id, statement, value, kind or any unrelated module semantics.
    """
    binder = _EvidenceBinder(repo_root, source_commit, generation_id)
    by_claim = {claim["claim_id"]: claim for claim in claims}
    bindings: list[dict] = []

    def record(
        claim_id: str,
        *,
        grade: str,
        observation_date: Any = None,
        observed_at: Any = None,
        compared_dates: Any = (),
        refs: Any = (),
        reasons: Any = (),
    ) -> None:
        claim = by_claim.get(claim_id)
        if claim is None:
            return
        reason_codes = set(reasons)
        if observation_date is None:
            reason_codes.add("OBSERVATION_DATE_MISSING")
        elif not isinstance(observation_date, str) or DATE.fullmatch(observation_date) is None or _iso_date(observation_date) is None:
            reason_codes.add("OBSERVATION_DATE_INVALID")
            observation_date = None
        if observed_at is None:
            reason_codes.add("CAPTURE_CLOCK_MISSING")
        elif _capture_instant(observed_at) is None:
            reason_codes.add("CAPTURE_CLOCK_INVALID")
            observed_at = None
        for value in (observation_date, _iso_date(observed_at)):
            if value is not None and value > briefing_date:
                reason_codes.add("FUTURE_EVIDENCE")
        ref_paths: list[str] = []
        if claim["kind"] == "FACT":
            for path in [packet_ref, *refs]:
                if isinstance(path, str) and path and path not in ref_paths:
                    ref_paths.append(path)
            for path in ref_paths:
                if path not in claim["source_ref_paths"]:
                    claim["source_ref_paths"].append(path)
        bindings.append({
            "claim_id": claim_id,
            "source_grade": GRADE_UNKNOWN if reason_codes else grade,
            "observation_date": observation_date or "UNKNOWN",
            "observed_at": observed_at or "UNKNOWN",
            "source_available_at": None,
            "point_in_time_admissible": False,
            "compared_dates": sorted({
                value for value in compared_dates if isinstance(value, str)
            }),
            "source_ref_paths": ref_paths,
            "reason_codes": sorted(reason_codes),
        })

    components = {
        row.get("component_id"): row
        for row in packet.get("components", [])
        if isinstance(row, dict) and isinstance(row.get("component_id"), str)
    }

    # FREE_MARKET_DATA: FRED VIXCLS and the representative US session reference.
    free = components.get("FREE_MARKET_DATA") or {}
    free_packet = free.get("packet") or {}
    vix = free_packet.get("vixcls") or {}
    us_reference = free_packet.get("us_market_reference") or {}
    vix_date = vix.get("date")
    us_session_date = us_reference.get("as_of_session_date")
    us_clocks = [
        value for value in (vix_date, us_session_date) if isinstance(value, str)
    ]
    capture_path, capture_reasons = binder.bind(
        free.get("source_packet_path"), file_sha256=free.get("source_packet_sha256")
    )
    capture = binder.json(capture_path) if capture_path is not None else None
    capture_observed_at = capture.get("observed_at_utc") if capture is not None else None
    unbound_capture = [f"US_CAPTURE_{code}" for code in capture_reasons]

    fred_evidence = free_packet.get("fred_evidence") or {}
    vix_refs = [capture_path] if capture_path is not None else []
    vix_reasons = list(unbound_capture)
    for path, file_sha256, content_sha256 in (
        (
            fred_evidence.get("manifest_path"),
            fred_evidence.get("manifest_file_sha256"),
            None,
        ),
        (
            fred_evidence.get("raw_path"),
            fred_evidence.get("raw_file_sha256"),
            fred_evidence.get("raw_response_sha256"),
        ),
    ):
        bound, reasons = binder.bind(
            path, file_sha256=file_sha256, content_sha256=content_sha256
        )
        if bound is None:
            vix_reasons.extend(f"FRED_{code}" for code in reasons)
        else:
            vix_refs.append(bound)
    capture_fred = capture.get("fred") if capture is not None else None
    fred_manifest_path = fred_evidence.get("manifest_path")
    fred_manifest = binder.json(fred_manifest_path) if fred_manifest_path in vix_refs else None
    fred_observed_at = fred_manifest.get("captured_at_utc") if fred_manifest is not None else None
    vix_reasons.extend(_capture_clock_reasons(capture_observed_at, fred_observed_at, "FRED_CAPTURE"))
    fred_observation = fred_manifest.get("observation") if fred_manifest is not None else None
    if not isinstance(fred_observation, dict) or fred_observation.get("observation_date") != vix_date:
        vix_reasons.append("FRED_MANIFEST_OBSERVATION_DATE_MISMATCH")
    if capture is not None:
        if not isinstance(capture_fred, dict):
            vix_reasons.append("US_CAPTURE_FRED_SECTION_MISSING")
        else:
            if capture_fred.get("observation_date") != vix_date:
                vix_reasons.append("US_CAPTURE_VIX_DATE_MISMATCH")
            if capture_fred.get("value") != vix.get("value"):
                vix_reasons.append("US_CAPTURE_VIX_VALUE_MISMATCH")
            if capture_fred.get("response_sha256") != fred_evidence.get(
                "raw_response_sha256"
            ):
                vix_reasons.append("US_CAPTURE_FRED_RESPONSE_DIGEST_MISMATCH")
    for claim_id in ("freshness.us.vix_observation_date", "numeric.us.vixcls"):
        record(
            claim_id,
            grade=GRADE_PRIMARY_DIRECT,
            observation_date=vix_date,
            observed_at=fred_observed_at,
            compared_dates=us_clocks,
            refs=vix_refs,
            reasons=vix_reasons,
        )

    alpaca_evidence = free_packet.get("alpaca_daily_evidence") or {}
    session_refs = [capture_path] if capture_path is not None else []
    session_reasons = list(unbound_capture)
    if capture is not None:
        capture_reference = capture.get("us_market_reference")
        if not isinstance(capture_reference, dict):
            session_reasons.append("US_CAPTURE_SESSION_REFERENCE_MISSING")
        elif capture_reference.get("as_of_session_date") != us_session_date:
            session_reasons.append("US_CAPTURE_SESSION_DATE_MISMATCH")
    bound, reasons = binder.bind(
        alpaca_evidence.get("raw_path"),
        content_sha256=alpaca_evidence.get("raw_response_sha256"),
    )
    if bound is None:
        session_reasons.extend(f"US_SESSION_{code}" for code in reasons)
    else:
        session_refs.append(bound)
    record(
        "freshness.us.market_session_date",
        grade=GRADE_PRIMARY_DIRECT,
        observation_date=us_session_date,
        observed_at=capture_observed_at,
        compared_dates=us_clocks,
        refs=session_refs,
        reasons=session_reasons,
    )
    record(
        "boundary.us.independent_evidence_clocks",
        grade=GRADE_UNKNOWN,
        compared_dates=us_clocks,
    )

    # BTC_TREND / BTC_RISK: retained Kraken capture behind a finalized close.
    for component_id, claim_key in (("BTC_TREND", "btc_trend"), ("BTC_RISK", "btc_risk")):
        component = components.get(component_id) or {}
        component_packet = component.get("packet") or {}
        directory = component.get("source_packet_path")
        risk_point = component_packet.get("risk_point") or {}
        finalized_day = component_packet.get("latest_finalized_day")
        if component_id == "BTC_RISK" and not isinstance(finalized_day, str):
            finalized_day = risk_point.get("as_of_date")
        capture_date = component_packet.get("capture_date") or component.get("as_of_date")
        manifest, refs, reasons = _retained_manifest(binder, directory)
        observed_at = None
        if manifest is not None:
            raw = manifest.get("raw")
            raw = raw if isinstance(raw, dict) else {}
            digest_index, digest_refs, digest_reasons = _retained_digest_index(binder, directory)
            refs.extend(digest_refs)
            reasons.extend(digest_reasons)
            bound, raw_reasons = _retained_response(
                binder, directory, raw.get("file"), raw.get("response_sha256"), digest_index
            )
            if bound is None:
                reasons.extend(f"BTC_RESPONSE_{code}" for code in raw_reasons)
            else:
                refs.append(bound)
            if raw.get("latest_finalized_day") != finalized_day:
                reasons.append("RETAINED_FINALIZED_DAY_MISMATCH")
            if component_id == "BTC_RISK" and risk_point.get("as_of_date") != finalized_day:
                reasons.append("RISK_MEASUREMENT_DATE_MISMATCH")
            if manifest.get("snapshot_date") != capture_date:
                reasons.append("RETAINED_CAPTURE_DATE_MISMATCH")
            fetched_at = manifest.get("fetched_at_utc")
            observed_at = fetched_at if isinstance(fetched_at, str) else None
        btc_clocks = [
            value for value in (finalized_day, capture_date) if isinstance(value, str)
        ]
        record(
            f"freshness.crypto.{claim_key}_finalized_date",
            grade=GRADE_PRIMARY_DIRECT,
            observation_date=finalized_day,
            observed_at=observed_at,
            compared_dates=btc_clocks,
            refs=refs,
            reasons=reasons,
        )
        code_path, code_reasons = _derivation_code(binder, component_packet, component, manifest)
        record(
            f"numeric.crypto.{claim_key}",
            grade=GRADE_INTERNAL_LOGIC_CHECK,
            observation_date=finalized_day,
            observed_at=observed_at,
            compared_dates=btc_clocks,
            refs=refs + ([code_path] if code_path is not None else []),
            reasons=reasons + code_reasons,
        )

    # STABLECOIN_NET_ISSUANCE: retained multi-endpoint capture.
    stablecoin = components.get("STABLECOIN_NET_ISSUANCE") or {}
    stable_packet = stablecoin.get("packet") or {}
    stable_directory = stablecoin.get("source_packet_path")
    stable_date = stable_packet.get("observation_date")
    manifest, stable_refs, stable_reasons = _retained_manifest(binder, stable_directory)
    stable_observed_at = None
    if manifest is not None:
        digest_index, digest_refs, digest_reasons = _retained_digest_index(binder, stable_directory)
        stable_refs.extend(digest_refs)
        stable_reasons.extend(digest_reasons)
        endpoints = manifest.get("endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            stable_reasons.append("RETAINED_ENDPOINTS_MISSING")
        else:
            fetched: list[str] = []
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    stable_reasons.append("RETAINED_ENDPOINT_INVALID")
                    continue
                bound, raw_reasons = _retained_response(
                    binder,
                    stable_directory,
                    endpoint.get("raw_file"),
                    endpoint.get("response_sha256"),
                    digest_index,
                )
                if bound is None:
                    stable_reasons.extend(f"STABLECOIN_RESPONSE_{code}" for code in raw_reasons)
                else:
                    stable_refs.append(bound)
                endpoint_clock = endpoint.get("fetched_at_utc")
                if endpoint_clock is None:
                    stable_reasons.append("ENDPOINT_CAPTURE_CLOCK_MISSING")
                elif _capture_instant(endpoint_clock) is None:
                    stable_reasons.append("ENDPOINT_CAPTURE_CLOCK_INVALID")
                else:
                    fetched.append(endpoint_clock)
            if fetched:
                stable_observed_at = max(fetched, key=_capture_instant)
        if manifest.get("snapshot_date") != stable_date:
            stable_reasons.append("RETAINED_OBSERVATION_DATE_MISMATCH")
    record(
        "freshness.crypto.stablecoin_observation_date",
        grade=GRADE_PRIMARY_DIRECT,
        observation_date=stable_date,
        observed_at=stable_observed_at,
        refs=stable_refs,
        reasons=stable_reasons,
    )
    code_path, code_reasons = _derivation_code(binder, stable_packet, stablecoin, manifest)
    record(
        "numeric.crypto.stablecoin_net_issuance",
        grade=GRADE_INTERNAL_LOGIC_CHECK,
        observation_date=stable_date,
        observed_at=stable_observed_at,
        refs=stable_refs + ([code_path] if code_path is not None else []),
        reasons=stable_reasons + code_reasons,
    )

    # OFFICIAL_RELEASE_SUMMARY: attributed primary release documents.
    release_packet = (components.get("OFFICIAL_RELEASE_SUMMARY") or {}).get("packet") or {}
    release_item_count = 0
    for observation_index, observation in enumerate(
        release_packet.get("observations", []), start=1
    ):
        if not isinstance(observation, dict):
            continue
        lineage = observation.get("lineage") or {}
        release_refs: list[str] = []
        release_reasons: list[str] = []
        for path, file_sha256, content_sha256 in (
            (lineage.get("manifest_ref"), lineage.get("manifest_sha256"), None),
            (
                lineage.get("release_document_ref"),
                None,
                lineage.get("release_content_sha256"),
            ),
        ):
            bound, reasons = binder.bind(
                path, file_sha256=file_sha256, content_sha256=content_sha256
            )
            if bound is None:
                release_reasons.extend(f"RELEASE_{code}" for code in reasons)
            else:
                release_refs.append(bound)
        published_at = observation.get("published_at")
        retrieved_at = lineage.get("retrieved_at_utc")
        manifest_ref = lineage.get("manifest_ref")
        release_manifest = binder.json(manifest_ref) if manifest_ref in release_refs else None
        manifest_retrieved_at = release_manifest.get("retrieved_at_utc") if release_manifest is not None else None
        release_reasons.extend(_capture_clock_reasons(retrieved_at, manifest_retrieved_at, "RELEASE_CAPTURE"))
        # The existing official-release observation producer binds published_at
        # to this SEC filing_date before producing the attributed summary.
        if release_manifest is None or release_manifest.get("filing_date") != published_at:
            release_reasons.append("RELEASE_MANIFEST_PUBLICATION_DATE_MISMATCH")
        release_clocks = [published_at, _iso_date(retrieved_at)]
        record(
            f"date.official_release.observation_{observation_index}",
            grade=GRADE_OFFICIAL_STATEMENT_RELAY,
            observation_date=published_at,
            observed_at=retrieved_at,
            compared_dates=release_clocks,
            refs=release_refs,
            reasons=release_reasons,
        )
        for item in observation.get("summary_items", []):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            release_item_count += 1
            record(
                f"official_release.attributed_summary_{release_item_count}",
                grade=GRADE_OFFICIAL_STATEMENT_RELAY,
                observation_date=published_at,
                observed_at=retrieved_at,
                compared_dates=release_clocks,
                refs=release_refs,
                reasons=release_reasons,
            )

    source_refs = [
        {"path": path, "sha256": sha256, "generation_id": generation_id}
        for path, sha256 in sorted(binder.refs.items())
    ]
    bindings.sort(key=lambda row: row["claim_id"])
    return bindings, source_refs


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
    delivery_claims = _delivery_claims(packet, packet_path)
    claim_source_bindings, primary_source_refs = _claim_source_bindings(
        repo_root,
        source_commit,
        packet=packet,
        packet_ref=packet_path,
        claims=delivery_claims,
        generation_id=generation_id,
        briefing_date=decision_date,
    )
    already_bound = {ref["path"] for ref in source_refs}
    source_refs.extend(
        ref for ref in primary_source_refs if ref["path"] not in already_bound
    )
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
        "delivery_claims": delivery_claims,
        "claim_source_binding_schema": CLAIM_SOURCE_BINDING_SCHEMA,
        "claim_source_bindings": claim_source_bindings,
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
    claims.extend(copy.deepcopy(envelope.get("delivery_claims", [])))
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
    """Project the strict ledger into the legacy ``claude_briefing_handoff/1``.

    A claim the input envelope bound to exact primary evidence carries that
    binding's grade, measurement date, capture instant and compared clocks.  A
    claim with no binding keeps the previous internal-consistency semantics: it
    describes the sealed packet of this briefing, not an external observation.
    """
    packet_ref = envelope["source_refs"][0]["path"]
    bindings = {
        row["claim_id"]: row for row in envelope.get("claim_source_bindings", [])
    }
    compat_claims = []
    for claim in claims:
        binding = bindings.get(claim["claim_id"])
        if binding is None:
            observation_date = envelope["briefing_date"]
            observed_at = "UNKNOWN"
            source_grade = (
                GRADE_INTERNAL_LOGIC_CHECK if claim["kind"] == "FACT" else GRADE_UNKNOWN
            )
            source_refs = [packet_ref] if claim["kind"] == "FACT" else []
            compared_dates: list[str] = []
        else:
            observation_date = binding["observation_date"]
            observed_at = binding["observed_at"]
            source_grade = binding["source_grade"]
            source_refs = list(binding["source_ref_paths"])
            compared_dates = list(binding["compared_dates"])
        compat_claims.append({
            "claim_id": claim["claim_id"],
            "statement": claim["statement"],
            "type": claim["kind"],
            "observation_date": observation_date,
            "observed_at": observed_at,
            "source_grade": source_grade,
            "source_refs": source_refs,
            "portal_visibility": True,
            "authority_impact": "NONE",
            "compared_dates": compared_dates,
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
