#!/usr/bin/env python3
"""P8 Atlas Daily Briefing Integration v1 — provider-free daily orchestrator.

This module calls no live provider and fetches nothing itself. It reads only
already-persisted, already-validated evidence and packets already committed
to this repository, and assembles them -- using the existing production
builders under regime/, rotation/, discovery/, rules/, bridge/, portfolio/,
briefing/, and decision/ -- into one daily briefing packet.

A component with no real committed input today is reported PENDING,
POLICY_BLOCKED, DATA_BLOCKED, or UNAVAILABLE with an honest reason. Nothing
here fabricates a neutral/zero/PASS value, grants Regime score, Rotation
ranking, Discovery promotion, Rule PASS/FAIL, Portfolio sizing, action,
order, Production, or trading authority. Every one of those stays exactly as
false as the component it wraps already declares.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "daily_orchestrator_contract.json"
EVIDENCE_ROOT = ROOT / "evidence" / "daily_briefing"
KST = ZoneInfo("Asia/Seoul")
UTC = dt.timezone.utc
STATUS_VALUES = frozenset({
    "READY", "PENDING", "UNKNOWN", "DEGRADED", "POLICY_BLOCKED",
    "DATA_BLOCKED", "UNAVAILABLE",
})


class DailyOrchestratorError(RuntimeError):
    """Fail-closed daily briefing orchestration violation."""


def fail(code: str, detail: str) -> None:
    raise DailyOrchestratorError(f"{code}: {detail}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_READ_FAILED", f"{path}:{exc}")


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = _read_json(path)
    expected = {
        "schema_version", "contract_version", "output_schema_version", "slots",
        "markets", "component_status_values", "component_order",
        "evening_only_components", "capture_mode", "authority", "publication",
    }
    if set(contract) != expected or contract.get("schema_version") != 1:
        fail("CONTRACT_INVALID", "schema or fields")
    if set(contract["component_status_values"]) != STATUS_VALUES:
        fail("CONTRACT_INVALID", "component_status_values")
    for key in (
        "source_interpretation_authorized", "regime_score_authorized",
        "rotation_ranking_authorized", "discovery_promotion_authorized",
        "rule_pass_fail_authorized", "portfolio_sizing_authorized",
        "action_generation_authorized", "order_generation_authorized",
        "production_authorized", "trading_authorized",
    ):
        if contract["authority"].get(key) is not False:
            fail("CONTRACT_INVALID", f"authority.{key} must remain false")
    return contract


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail("MODULE_LOAD_FAILED", relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGIME = _load("atlas_daily_regime", "regime/output_contract.py")
HEADER = _load("atlas_daily_header", "briefing/three_market_regime_header.py")
LEDGER = _load("atlas_daily_ledger", "rotation/rotation_state_ledger.py")
DISCOVERY = _load("atlas_daily_discovery", "discovery/event_case.py")
ROTATION_DISCOVERY = _load("atlas_daily_rotation_discovery", "briefing/rotation_discovery.py")
BINDING = _load("atlas_daily_binding", "bridge/rule_evidence_binding.py")
EVALUATOR = _load("atlas_daily_evaluator", "rules/deterministic_rule_evaluator.py")
ACTION_BOUNDARY = _load("atlas_daily_action_boundary", "decision/ready_signal_order_boundary.py")
UNIFIED = _load("atlas_daily_unified", "decision/unified_decision_contract.py")
CASH_EXPOSURE = _load("atlas_daily_cash_exposure", "portfolio/cash_exposure_action.py")
INVERSE = _load("atlas_daily_inverse", "portfolio/regime_inverse_invariant.py")
LONG_SHORT = _load("atlas_daily_long_short", "portfolio/long_short_invariant.py")
ACTION_SUMMARY = _load("atlas_daily_action_summary", "briefing/action_risk_portfolio_summary.py")
KRX_POST_CLOSE = _load("atlas_daily_krx_post_close", "briefing/krx_post_close.py")
BRIEFING_READINESS = _load("atlas_daily_readiness", ".github/scripts/check_briefing_readiness.py")
BTC_TREND = _load("atlas_daily_btc_trend", ".github/scripts/btc_trend.py")
BTC_RISK = _load("atlas_daily_btc_risk", ".github/scripts/btc_risk.py")
STABLECOIN = _load("atlas_daily_stablecoin", ".github/scripts/stablecoin_net_issuance.py")
US_BREADTH = _load("atlas_daily_us_breadth", ".github/scripts/us_breadth_forward.py")
CRYPTO_BREADTH = _load("atlas_daily_crypto_breadth", ".github/scripts/crypto_breadth.py")
KOFIA = _load("atlas_daily_kofia", ".github/scripts/kofia_first_seen.py")


def component_row(
    component_id: str,
    status: str,
    reason: str | None,
    *,
    as_of_date: str | None = None,
    generated_at: str | None = None,
    available_at: str | None = None,
    source_packet_path: str | None = None,
    source_packet_sha256: str | None = None,
    validated: bool = False,
    authority: dict | None = None,
    contract_version: str | None = None,
    packet: dict | None = None,
) -> dict:
    if status not in STATUS_VALUES:
        fail("COMPONENT_STATUS_INVALID", f"{component_id}:{status}")
    if status != "READY" and reason is None:
        fail("COMPONENT_REASON_REQUIRED", component_id)
    return {
        "component_id": component_id,
        "contract_version": contract_version,
        "status": status,
        "reason": reason,
        "as_of_date": as_of_date,
        "generated_at": generated_at,
        "available_at": available_at,
        "source_packet_path": source_packet_path,
        "source_packet_sha256": source_packet_sha256,
        "validated": validated,
        "authority": copy.deepcopy(authority) if authority is not None else None,
        "decision_eligible": False,
        "action_eligible": False,
        "order_eligible": False,
        "packet": copy.deepcopy(packet) if packet is not None else None,
    }


def _blocked(component_id: str, status: str, reason: str, contract_version: str | None = None) -> dict:
    return component_row(component_id, status, reason, contract_version=contract_version)


def _degraded_from_exception(component_id: str, exc: Exception) -> dict:
    return component_row(
        component_id, "DEGRADED", f"{type(exc).__name__}:{exc}"
    )


def _latest_dated_dir(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = sorted(
        (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.name,
    )
    return candidates[-1] if candidates else None


def _latest_run_dir(day_dir: Path) -> Path | None:
    if not day_dir.exists():
        return None
    candidates = sorted(
        (path for path in day_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name,
    )
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# Step 0 / read-model health + KRX/DART/SEC compact pre-open read model
# ---------------------------------------------------------------------------


def build_step0_health(decision_date: str) -> dict:
    try:
        payload = BRIEFING_READINESS.evaluate(decision_date, BRIEFING_READINESS.DATA)
    except Exception as exc:  # noqa: BLE001 - isolate any read-model failure
        return _degraded_from_exception("STEP0_READ_MODEL_HEALTH", exc)
    classification = payload.get("classification")
    status_map = {
        "data_ready_read_model_ready": "READY",
        "data_ready_read_model_degraded": "DEGRADED",
        "data_not_ready": "DATA_BLOCKED",
        "unknown_manual_inspection_required": "UNKNOWN",
    }
    status = status_map.get(classification, "UNKNOWN")
    reason = None if status == "READY" else (
        ";".join(payload.get("reasons") or []) or classification or "UNCLASSIFIED"
    )
    return component_row(
        "STEP0_READ_MODEL_HEALTH",
        status,
        reason,
        as_of_date=decision_date,
        source_packet_path="data/briefing_status.json",
        validated=True,
        packet=payload,
    )


def build_krx_preopen_compact(decision_date: str, step0_packet: dict | None) -> dict:
    if step0_packet is None:
        return _blocked(
            "KRX_PREOPEN_COMPACT", "UNKNOWN", "STEP0_READ_MODEL_HEALTH_UNAVAILABLE"
        )
    sources = step0_packet.get("sources", {})
    krx = sources.get("krx")
    if krx is None:
        return _blocked("KRX_PREOPEN_COMPACT", "DATA_BLOCKED", "KRX_SOURCE_MISSING")
    # Distinguish a collector-data failure (the raw collectors themselves
    # are not ready for this date) from a read-model-only failure (the
    # collectors are fine but the compact per-symbol view has an issue) --
    # these are different failure classes with different remediation paths,
    # and must not be collapsed into one generic DEGRADED status.
    if not step0_packet.get("data_ready"):
        status, reason = "DATA_BLOCKED", "COLLECTOR_DATA_NOT_READY_FOR_DECISION_DATE"
    elif not step0_packet.get("read_model_ready"):
        status, reason = "DEGRADED", "READ_MODEL_ONLY_NOT_FULLY_READY"
    else:
        status, reason = "READY", None
    return component_row(
        "KRX_PREOPEN_COMPACT",
        status,
        reason,
        as_of_date=krx.get("collected_for_kst_date"),
        source_packet_path=krx.get("path"),
        source_packet_sha256=krx.get("source_sha256"),
        validated=True,
        packet={"krx": krx, "dart": sources.get("dart"), "sec": sources.get("sec")},
    )


# ---------------------------------------------------------------------------
# KRX post-close (evening only)
# ---------------------------------------------------------------------------


def build_krx_post_close(decision_date: str, generated_at_utc: dt.datetime) -> dict:
    generated_at_kst = generated_at_utc.astimezone(KST).isoformat(timespec="seconds")
    try:
        packet = KRX_POST_CLOSE.build_packet(ROOT / "data", decision_date, generated_at_kst)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("KRX_POST_CLOSE", exc)
    status = packet.get("status")
    if status == "READY_OBSERVED_UNCONFIRMED":
        result_status, reason = "READY", None
    else:
        result_status, reason = "UNKNOWN", status or "UNKNOWN_STATUS"
    return component_row(
        "KRX_POST_CLOSE",
        result_status,
        reason,
        as_of_date=decision_date,
        generated_at=generated_at_kst,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


# ---------------------------------------------------------------------------
# DART / SEC filing content status (read persisted status files only)
# ---------------------------------------------------------------------------


def _filing_content_status(component_id: str, status_file: str) -> dict:
    path = ROOT / status_file
    if not path.exists():
        return _blocked(component_id, "UNAVAILABLE", "STATUS_FILE_MISSING")
    try:
        payload = _read_json(path)
    except DailyOrchestratorError as exc:
        return component_row(component_id, "DEGRADED", str(exc))
    records = payload.get("records")
    count = len(records) if isinstance(records, list) else records
    if payload.get("run_status") != "OK":
        status, reason = "DEGRADED", f"run_status={payload.get('run_status')}"
    elif not count:
        status, reason = "DATA_BLOCKED", "NO_MATCHING_FILING_CAPTURED_YET"
    else:
        status, reason = "READY", None
    return component_row(
        component_id,
        status,
        reason,
        as_of_date=payload.get("collected_for_kst_date"),
        generated_at=payload.get("observed_at_utc"),
        source_packet_path=status_file,
        source_packet_sha256=payload.get("source_sha256"),
        validated=True,
        authority=payload.get("authority"),
        contract_version=payload.get("contract_version"),
        packet={"run_status": payload.get("run_status"), "counts": payload.get("counts"), "record_count": count},
    )


def build_dart_filing_content() -> dict:
    return _filing_content_status("DART_FILING_CONTENT", "data/latest_dart_content.json")


def build_sec_filing_content() -> dict:
    return _filing_content_status("SEC_FILING_CONTENT", "data/latest_sec_content.json")


# ---------------------------------------------------------------------------
# KOFIA first-seen (persisted evidence only; no provider call)
# ---------------------------------------------------------------------------


def build_kofia_first_seen() -> dict:
    evidence_root = ROOT / "evidence" / "kofia" / "first_seen"
    day_dir = _latest_dated_dir(evidence_root)
    run_dir = _latest_run_dir(day_dir) if day_dir is not None else None
    if run_dir is None:
        return _blocked("KOFIA_FIRST_SEEN", "UNAVAILABLE", "NO_COMMITTED_EVIDENCE")
    try:
        observation = KOFIA.validate_capture(run_dir, evidence_root)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("KOFIA_FIRST_SEEN", exc)
    return component_row(
        "KOFIA_FIRST_SEEN",
        "POLICY_BLOCKED",
        "SOURCE_AVAILABLE_AT_AND_API_UNIT_UNRATIFIED",
        as_of_date=day_dir.name,
        generated_at=observation.get("captured_at_utc"),
        source_packet_path=str(run_dir.relative_to(ROOT)),
        validated=True,
        packet={
            "captured_at_utc": observation.get("captured_at_utc"),
            "available_at": observation.get("available_at"),
            "decision_eligible": observation.get("decision_eligible"),
        },
    )


# ---------------------------------------------------------------------------
# Crypto/US sensors already committed as live evidence
# ---------------------------------------------------------------------------


def build_us_breadth_membership(decision_date: str) -> dict:
    raw_root = US_BREADTH.RAW_ROOT
    try:
        chain = US_BREADTH.replay_archive(raw_root)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("US_BREADTH_MEMBERSHIP", exc)
    if not chain:
        return _blocked("US_BREADTH_MEMBERSHIP", "UNAVAILABLE", "ARCHIVE_EMPTY")
    latest_date = chain[-1]["snapshot_date"]
    try:
        universe = US_BREADTH.universe_as_of(latest_date, raw_root)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("US_BREADTH_MEMBERSHIP", exc)
    return component_row(
        "US_BREADTH_MEMBERSHIP",
        "READY",
        None,
        as_of_date=universe["snapshot_date"],
        source_packet_path=f"evidence/us_breadth/raw/{universe['snapshot_date']}",
        validated=True,
        packet={
            "snapshot_date": universe["snapshot_date"],
            "member_count": len(universe["members"]),
            "historical_universe_policy": universe["historical_universe_policy"],
        },
    )


def _latest_evidence_snapshot(relative_root: str) -> Path | None:
    return _latest_dated_dir(ROOT / relative_root)


def build_btc_trend() -> dict:
    snapshot = _latest_evidence_snapshot("evidence/crypto/btc/raw")
    if snapshot is None:
        return _blocked("BTC_TREND", "UNAVAILABLE", "NO_COMMITTED_EVIDENCE")
    try:
        packet = BTC_TREND.build_transform(snapshot)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("BTC_TREND", exc)
    return component_row(
        "BTC_TREND",
        "READY",
        None,
        as_of_date=snapshot.name,
        source_packet_path=f"evidence/crypto/btc/raw/{snapshot.name}",
        validated=True,
        authority={k: v for k, v in packet.items() if k.endswith("_authorized")},
        contract_version=packet.get("transform_version"),
        packet={"direction": packet.get("direction"), "dma_200": packet.get("dma_200") if "dma_200" in packet else None},
    )


def build_btc_risk() -> dict:
    snapshot = _latest_evidence_snapshot("evidence/crypto/btc/raw")
    if snapshot is None:
        return _blocked("BTC_RISK", "UNAVAILABLE", "NO_COMMITTED_EVIDENCE")
    try:
        packet = BTC_RISK.build_transform(snapshot)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("BTC_RISK", exc)
    return component_row(
        "BTC_RISK",
        "READY",
        None,
        as_of_date=snapshot.name,
        source_packet_path=f"evidence/crypto/btc/raw/{snapshot.name}",
        validated=True,
        contract_version=packet.get("transform_version"),
        packet={
            "status": packet.get("status"),
            "risk_point": packet.get("risk_point"),
        },
    )


def build_stablecoin() -> dict:
    snapshot = _latest_evidence_snapshot("evidence/stablecoin/raw")
    if snapshot is None:
        return _blocked("STABLECOIN_NET_ISSUANCE", "UNAVAILABLE", "NO_COMMITTED_EVIDENCE")
    try:
        packet = STABLECOIN.build_transform(snapshot)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("STABLECOIN_NET_ISSUANCE", exc)
    return component_row(
        "STABLECOIN_NET_ISSUANCE",
        "READY",
        None,
        as_of_date=snapshot.name,
        source_packet_path=f"evidence/stablecoin/raw/{snapshot.name}",
        validated=True,
        packet={"daily_status": packet.get("daily_status")},
    )


def build_crypto_breadth() -> dict:
    snapshot = _latest_evidence_snapshot("evidence/crypto/breadth/raw")
    if snapshot is None:
        return _blocked("CRYPTO_BREADTH", "UNAVAILABLE", "NO_COMMITTED_EVIDENCE")
    try:
        packet = CRYPTO_BREADTH.build_transform(snapshot)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("CRYPTO_BREADTH", exc)
    status = packet.get("status")
    if status == "UNKNOWN":
        result_status = "POLICY_BLOCKED"
        reason = packet.get("unknown_reason") or "UNKNOWN"
    else:
        result_status, reason = "READY", None
    return component_row(
        "CRYPTO_BREADTH",
        result_status,
        reason,
        as_of_date=snapshot.name,
        source_packet_path=f"evidence/crypto/breadth/raw/{snapshot.name}",
        validated=True,
        packet={"status": status, "selected_asset_count": packet.get("selected_asset_count")},
    )


# ---------------------------------------------------------------------------
# Regime (always buildable today: no live axis adapter exists yet, so every
# market is honestly UNKNOWN -- never fabricated as any other regime).
# ---------------------------------------------------------------------------


def build_regime_outputs(generated_at: str) -> dict[str, dict]:
    outputs = {}
    for market in REGIME.load_contract()["markets"]:
        outputs[market] = REGIME.build_unknown_output(market, generated_at)
    return outputs


def build_three_market_header(regime_outputs: dict[str, dict], slot: str, generated_at: str) -> dict:
    try:
        packet = HEADER.build_header(list(regime_outputs.values()), slot, generated_at)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("THREE_MARKET_REGIME_HEADER", exc)
    return component_row(
        "THREE_MARKET_REGIME_HEADER",
        "PENDING",
        "ALL_MARKETS_UNKNOWN_NO_LIVE_AXIS_ADAPTER_WIRED",
        as_of_date=generated_at[:10],
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


# ---------------------------------------------------------------------------
# Rotation / Discovery (honest empty state: no ratified rotation/discovery
# policy exists, so the ledger and discovery packet are built empty rather
# than fabricated).
# ---------------------------------------------------------------------------


def build_rotation_discovery(slot: str, generated_at: str) -> dict:
    ledger = LEDGER.empty_ledger()
    empty_bindings = {
        "schema_version": DISCOVERY.BINDING_SCHEMA_VERSION,
        "binding_set_id": "DAILY_ORCHESTRATOR_NO_LIVE_BINDINGS",
        "bindings": [],
    }
    try:
        packet = ROTATION_DISCOVERY.build_briefing(
            ledger, [], empty_bindings, slot, generated_at
        )
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("ROTATION_DISCOVERY", exc)
    return component_row(
        "ROTATION_DISCOVERY",
        "PENDING",
        "NO_RATIFIED_ROTATION_OR_DISCOVERY_POLICY",
        as_of_date=generated_at[:10],
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


# ---------------------------------------------------------------------------
# Rule evaluation (real deterministic run today: 0/25 Rules are consumable,
# so every Rule is honestly UNKNOWN/UNDEFINED -- never PASS/FAIL).
# ---------------------------------------------------------------------------


def build_rule_evaluation() -> dict:
    try:
        binding_packet = BINDING.build_packet(
            envelopes=[],
            bindings={
                "schema_version": BINDING.BINDING_SCHEMA_VERSION,
                "binding_set_id": "DAILY_ORCHESTRATOR_NO_LIVE_BINDINGS",
                "bindings": [],
            },
        )
        packet = EVALUATOR.build_packet(binding_packet)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("RULE_EVALUATION", exc)
    return component_row(
        "RULE_EVALUATION",
        "POLICY_BLOCKED",
        "ZERO_OF_TWENTY_FIVE_RULES_CONSUMABLE_BY_EVALUATOR",
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


# ---------------------------------------------------------------------------
# Action boundary (honest empty state: no READY candidates from Discovery
# or Rule exist yet, so the subject list is empty, not fabricated).
# ---------------------------------------------------------------------------


def build_action_boundary(generated_at: str) -> dict:
    contract = ACTION_BOUNDARY.load_contract()
    value = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "packet_id": f"daily-orchestrator-{generated_at}",
        "as_of_utc": generated_at,
        "subjects": [],
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    value["packet_sha256"] = payload_sha256(value)
    try:
        packet = ACTION_BOUNDARY.build_packet(value, contract)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("ACTION_BOUNDARY", exc)
    return component_row(
        "ACTION_BOUNDARY",
        "PENDING",
        "NO_READY_CANDIDATES_FROM_DISCOVERY_OR_RULE_YET",
        as_of_date=generated_at[:10],
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


# ---------------------------------------------------------------------------
# Unified Decision + Action/Risk/Portfolio summary
# ---------------------------------------------------------------------------


def build_unified_decision(
    component_rows: dict[str, dict],
    decision_date: str,
    slot: str,
    generated_at: str,
) -> dict:
    contract = UNIFIED.load_contract()
    packet_map = {
        "REGIME": component_rows["THREE_MARKET_REGIME_HEADER"],
        "ROTATION_DISCOVERY": component_rows["ROTATION_DISCOVERY"],
        "RULE": component_rows["RULE_EVALUATION"],
        "PORTFOLIO_BUCKET": component_rows["PORTFOLIO_BUCKET"],
        "PORTFOLIO_CURRENCY": component_rows["PORTFOLIO_CURRENCY"],
        "ACTION_BOUNDARY": component_rows["ACTION_BOUNDARY"],
    }
    components = {}
    unavailable_reasons = {}
    for name in contract["component_order"]:
        row = packet_map[name]
        if row["packet"] is not None and row["validated"]:
            components[name] = row["packet"]
            unavailable_reasons[name] = []
        else:
            components[name] = None
            unavailable_reasons[name] = [row["reason"] or "UNAVAILABLE"]
    try:
        packet = UNIFIED.build_packet(
            components, unavailable_reasons, decision_date, slot, generated_at
        )
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("UNIFIED_DECISION", exc)
    available = packet["summary"]["available_component_count"]
    total = packet["summary"]["component_count"]
    status = "READY" if available == total else "PENDING"
    reason = None if status == "READY" else f"{available}/{total}_COMPONENTS_AVAILABLE"
    return component_row(
        "UNIFIED_DECISION",
        status,
        reason,
        as_of_date=decision_date,
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


def build_regime_invariant_pair(market: str, regime_output: dict) -> tuple[dict, dict]:
    try:
        cash_packet = CASH_EXPOSURE.build_packet(regime_output)
    except Exception as exc:  # noqa: BLE001
        cash_row = _degraded_from_exception(f"CASH_EXPOSURE_{market}", exc)
    else:
        cash_row = component_row(
            f"CASH_EXPOSURE_{market}",
            "PENDING",
            "REGIME_UNKNOWN_NOT_EVALUATED",
            source_packet_sha256=cash_packet.get("packet_sha256"),
            validated=True,
            authority={k: v for k, v in cash_packet.items() if k.endswith("_authorized")},
            contract_version=cash_packet.get("transform_version"),
            packet=cash_packet,
        )
    try:
        inverse_packet = INVERSE.build_packet(regime_output)
    except Exception as exc:  # noqa: BLE001
        inverse_row = _degraded_from_exception(f"INVERSE_{market}", exc)
    else:
        inverse_row = component_row(
            f"INVERSE_{market}",
            "PENDING",
            "REGIME_UNKNOWN_NOT_EVALUATED",
            source_packet_sha256=inverse_packet.get("packet_sha256"),
            validated=True,
            authority={k: v for k, v in inverse_packet.items() if k.endswith("_authorized")},
            contract_version=inverse_packet.get("transform_version"),
            packet=inverse_packet,
        )
    return cash_row, inverse_row


def build_long_short_invariant(rule_packet: dict | None) -> dict:
    if rule_packet is None:
        return _blocked("LONG_SHORT_INVARIANT", "UNAVAILABLE", "RULE_EVALUATION_UNAVAILABLE")
    try:
        packet = LONG_SHORT.build_packet(rule_packet)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("LONG_SHORT_INVARIANT", exc)
    return component_row(
        "LONG_SHORT_INVARIANT",
        "PENDING",
        "NO_RULE_PASS_FAIL_TO_EVALUATE",
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority={k: v for k, v in packet.items() if k.endswith("_authorized")},
        contract_version=packet.get("transform_version"),
        packet=packet,
    )


_POLICY_BLOCKED_ACTION_SOURCES = {
    "HEDGE_ELIGIBILITY": "NO_CIO_RATIFIED_HEDGE_INSTRUMENT_REGISTRY",
    "BEAR_HEDGE_BUDGET": "NO_CIO_RATIFIED_BEAR_HEDGE_BUDGET_SET",
    "POSITION_SIZING": "NO_CIO_RATIFIED_SIZING_POLICY_OR_CONSTITUTION",
    "CONCENTRATION_GUARD": "NO_CIO_RATIFIED_CONCENTRATION_POLICY",
    "MARKET_THEME_BUDGET": "NO_CIO_RATIFIED_THEME_BUDGET",
    "CRYPTO_EXPOSURE_LIMIT": "NO_CIO_RATIFIED_CRYPTO_LIMIT_POLICY",
    "PLANNED_LOSS_BUDGET": "NO_RATIFIED_CONSTITUTION",
}


def build_action_risk_summary(component_rows: dict[str, dict], generated_at: str) -> dict:
    contract = ACTION_SUMMARY.load_contract()
    name_map = {
        "UNIFIED_DECISION": "UNIFIED_DECISION",
        "CASH_EXPOSURE_US": "CASH_EXPOSURE_US",
        "CASH_EXPOSURE_KOREA": "CASH_EXPOSURE_KOREA",
        "CASH_EXPOSURE_CRYPTO": "CASH_EXPOSURE_CRYPTO",
        "LONG_SHORT_INVARIANT": "LONG_SHORT_INVARIANT",
        "INVERSE_US": "INVERSE_US",
        "INVERSE_KOREA": "INVERSE_KOREA",
        "INVERSE_CRYPTO": "INVERSE_CRYPTO",
    }
    source_packets = {}
    unavailable_reasons = {}
    for name in contract["source_order"]:
        if name in name_map:
            row = component_rows[name_map[name]]
            if row["packet"] is not None and row["validated"]:
                source_packets[name] = row["packet"]
                unavailable_reasons[name] = []
                continue
        source_packets[name] = None
        unavailable_reasons[name] = [
            _POLICY_BLOCKED_ACTION_SOURCES.get(name, "NOT_BUILT_IN_DAILY_ORCHESTRATOR_V1")
        ]
    try:
        packet = ACTION_SUMMARY.build_summary(source_packets, unavailable_reasons, generated_at)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("ACTION_RISK_PORTFOLIO_SUMMARY", exc)
    return component_row(
        "ACTION_RISK_PORTFOLIO_SUMMARY",
        "PENDING",
        "MOST_UPSTREAM_SOURCES_NOT_YET_LIVE",
        as_of_date=generated_at[:10],
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_packet(
    slot: str,
    decision_date: str,
    generated_at: str,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    if slot not in contract["slots"]:
        fail("SLOT_INVALID", slot)
    try:
        generated_at_dt = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        fail("GENERATED_AT_INVALID", generated_at)
    if generated_at_dt.tzinfo is None:
        fail("GENERATED_AT_INVALID", "must include a timezone offset")

    rows: dict[str, dict] = {}

    step0 = build_step0_health(decision_date)
    rows["STEP0_READ_MODEL_HEALTH"] = step0
    rows["KRX_PREOPEN_COMPACT"] = build_krx_preopen_compact(decision_date, step0["packet"])

    if "KRX_POST_CLOSE" in contract["evening_only_components"] and slot == "evening":
        rows["KRX_POST_CLOSE"] = build_krx_post_close(decision_date, generated_at_dt)
    else:
        rows["KRX_POST_CLOSE"] = _blocked(
            "KRX_POST_CLOSE", "PENDING", "MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY"
        )

    rows["DART_FILING_CONTENT"] = build_dart_filing_content()
    rows["SEC_FILING_CONTENT"] = build_sec_filing_content()
    rows["KOFIA_FIRST_SEEN"] = build_kofia_first_seen()
    rows["US_BREADTH_MEMBERSHIP"] = build_us_breadth_membership(decision_date)
    rows["BTC_TREND"] = build_btc_trend()
    rows["BTC_RISK"] = build_btc_risk()
    rows["STABLECOIN_NET_ISSUANCE"] = build_stablecoin()
    rows["CRYPTO_BREADTH"] = build_crypto_breadth()

    regime_outputs = build_regime_outputs(generated_at)
    rows["THREE_MARKET_REGIME_HEADER"] = build_three_market_header(
        regime_outputs, slot, generated_at
    )
    rows["ROTATION_DISCOVERY"] = build_rotation_discovery(slot, generated_at)
    rows["RULE_EVALUATION"] = build_rule_evaluation()
    rows["PORTFOLIO_BUCKET"] = _blocked(
        "PORTFOLIO_BUCKET", "POLICY_BLOCKED", "CONSTITUTION_NOT_RATIFIED"
    )
    rows["PORTFOLIO_CURRENCY"] = _blocked(
        "PORTFOLIO_CURRENCY", "UNAVAILABLE", "NO_LIVE_ASSET_MASTER_OR_POSITION_SNAPSHOT"
    )
    rows["ACTION_BOUNDARY"] = build_action_boundary(generated_at)
    rows["UNIFIED_DECISION"] = build_unified_decision(rows, decision_date, slot, generated_at)

    for market, key in (("US", "US"), ("KR", "KOREA"), ("CRYPTO", "CRYPTO")):
        cash_row, inverse_row = build_regime_invariant_pair(key, regime_outputs[market])
        rows[f"CASH_EXPOSURE_{key}"] = cash_row
        rows[f"INVERSE_{key}"] = inverse_row

    rows["LONG_SHORT_INVARIANT"] = build_long_short_invariant(
        rows["RULE_EVALUATION"]["packet"]
    )
    for name, reason in _POLICY_BLOCKED_ACTION_SOURCES.items():
        rows[name] = _blocked(name, "POLICY_BLOCKED", reason)

    rows["ACTION_RISK_PORTFOLIO_SUMMARY"] = build_action_risk_summary(rows, generated_at)

    if set(rows) != set(contract["component_order"]):
        fail(
            "COMPONENT_SET_MISMATCH",
            f"missing={set(contract['component_order']) - set(rows)} "
            f"unexpected={set(rows) - set(contract['component_order'])}",
        )
    ordered_components = [rows[name] for name in contract["component_order"]]

    counts: dict[str, int] = {status: 0 for status in STATUS_VALUES}
    for row in ordered_components:
        counts[row["status"]] += 1

    packet = {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "output_schema_version": contract["output_schema_version"],
        "slot": slot,
        "decision_date": decision_date,
        "generated_at": generated_at,
        "capture_mode": contract["capture_mode"],
        "component_status_counts": counts,
        "components": ordered_components,
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REGIME_AXIS_LIVE_ADAPTER_NOT_WIRED",
            "ROTATION_AND_DISCOVERY_POLICY_UNRATIFIED",
            "RULE_REGISTRY_NOT_CONSUMABLE",
            "PORTFOLIO_CONSTITUTION_NOT_RATIFIED",
            "ACTION_AND_ORDER_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    if not isinstance(packet, dict):
        fail("OUTPUT_INVALID", "root must be object")
    digest = packet.get("packet_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256", None)
    if payload_sha256(unsigned) != digest:
        fail("OUTPUT_SHA_MISMATCH", "packet_sha256")
    rebuilt = build_packet(
        packet["slot"], packet["decision_date"], packet["generated_at"], contract
    )
    if rebuilt != packet:
        fail("OUTPUT_MISMATCH", "rebuilt packet does not match persisted packet")
    return packet


# ---------------------------------------------------------------------------
# Human-readable renderer
# ---------------------------------------------------------------------------


_SECTION_GROUPS = [
    ("Data / Read-model health", ["STEP0_READ_MODEL_HEALTH", "KRX_PREOPEN_COMPACT", "KRX_POST_CLOSE"]),
    ("Filing & source evidence", ["DART_FILING_CONTENT", "SEC_FILING_CONTENT", "KOFIA_FIRST_SEEN"]),
    ("Sensors", [
        "US_BREADTH_MEMBERSHIP", "BTC_TREND", "BTC_RISK",
        "STABLECOIN_NET_ISSUANCE", "CRYPTO_BREADTH",
    ]),
    ("3-Market Regime", ["THREE_MARKET_REGIME_HEADER"]),
    ("Rotation / Theme", ["ROTATION_DISCOVERY"]),
    ("New Discovery / candidate change", ["ROTATION_DISCOVERY"]),
    ("Rule status", ["RULE_EVALUATION"]),
    ("Portfolio / Risk", [
        "PORTFOLIO_BUCKET", "PORTFOLIO_CURRENCY", "CASH_EXPOSURE_US",
        "CASH_EXPOSURE_KOREA", "CASH_EXPOSURE_CRYPTO", "INVERSE_US",
        "INVERSE_KOREA", "INVERSE_CRYPTO", "LONG_SHORT_INVARIANT",
        "HEDGE_ELIGIBILITY", "BEAR_HEDGE_BUDGET", "POSITION_SIZING",
        "CONCENTRATION_GUARD", "MARKET_THEME_BUDGET", "CRYPTO_EXPOSURE_LIMIT",
        "PLANNED_LOSS_BUDGET",
    ]),
    ("Decision & action boundary", ["ACTION_BOUNDARY", "UNIFIED_DECISION", "ACTION_RISK_PORTFOLIO_SUMMARY"]),
]

_STATUS_MARK = {
    "READY": "OK", "PENDING": "PENDING", "UNKNOWN": "UNKNOWN",
    "DEGRADED": "DEGRADED", "POLICY_BLOCKED": "POLICY_BLOCKED",
    "DATA_BLOCKED": "DATA_BLOCKED", "UNAVAILABLE": "UNAVAILABLE",
}


def render_markdown(packet: dict) -> str:
    by_id = {row["component_id"]: row for row in packet["components"]}
    lines = [
        f"# Atlas Daily Briefing — {packet['decision_date']} ({packet['slot']})",
        "",
        f"Generated at: {packet['generated_at']}",
        f"Component status counts: {packet['component_status_counts']}",
        "",
        "No action, order, Production, or trading authority is granted by this "
        "briefing. All such fields remain false/null.",
        "",
    ]
    seen = set()
    for title, ids in _SECTION_GROUPS:
        rows = [by_id[cid] for cid in ids if cid in by_id and cid not in seen]
        seen.update(ids)
        if not rows:
            continue
        lines.append(f"## {title}")
        for row in rows:
            mark = _STATUS_MARK.get(row["status"], row["status"])
            reason = f" — {row['reason']}" if row["reason"] else ""
            lines.append(f"- **{row['component_id']}**: {mark}{reason}")
            if row["source_packet_path"]:
                lines.append(f"  - source: `{row['source_packet_path']}`")
            if row["source_packet_sha256"]:
                lines.append(f"  - sha256: `{row['source_packet_sha256']}`")
        lines.append("")
    pending = [row["component_id"] for row in packet["components"] if row["status"] not in ("READY",)]
    lines.append("## PENDING / UNKNOWN / DEGRADED / BLOCKED components")
    lines.append(", ".join(pending) if pending else "(none)")
    lines.append("")
    lines.append("## Unresolved boundaries")
    for boundary in packet["unresolved_boundaries"]:
        lines.append(f"- {boundary}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Publication (atomic, append-only, outside-repo-forbidden like its peers is
# NOT applied here on purpose: the orchestrator's whole point is to persist
# one committed daily record, unlike the ad hoc P8 packet builders it calls).
# ---------------------------------------------------------------------------


def publish(slot: str, decision_date: str, generated_at: str, evidence_root: Path = EVIDENCE_ROOT) -> Path:
    packet = build_packet(slot, decision_date, generated_at)
    validate_packet(packet)
    rendered = render_markdown(packet)
    target_dir = Path(evidence_root) / slot / decision_date
    if target_dir.exists():
        fail("APPEND_ONLY_VIOLATION", str(target_dir))
    temp_dir = target_dir.parent / f".{target_dir.name}.tmp.{os.getpid()}"
    temp_dir.mkdir(parents=True)
    try:
        (temp_dir / "packet.json").write_text(
            json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (temp_dir / "briefing.md").write_text(rendered, encoding="utf-8")
        temp_dir.replace(target_dir)
    finally:
        if temp_dir.exists():
            import shutil

            shutil.rmtree(temp_dir)
    return target_dir


def run(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--slot", required=True, choices=["morning", "evening"])
    build.add_argument("--decision-date", required=True)
    build.add_argument("--generated-at", required=True)
    build.add_argument("--out", type=Path)

    pub = sub.add_parser("publish")
    pub.add_argument("--slot", required=True, choices=["morning", "evening"])
    pub.add_argument("--decision-date", required=True)
    pub.add_argument("--generated-at", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("packet_path", type=Path)

    args = parser.parse_args(argv)
    if args.command == "build":
        packet = build_packet(args.slot, args.decision_date, args.generated_at)
        text = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out is None:
            print(text)
        else:
            args.out.write_text(text, encoding="utf-8")
            print(args.out)
        return 0
    if args.command == "publish":
        target = publish(args.slot, args.decision_date, args.generated_at)
        print(target)
        return 0
    packet = _read_json(args.packet_path)
    validate_packet(packet)
    print(
        "Atlas daily briefing PASS"
        f" slot={packet['slot']} decision_date={packet['decision_date']}"
        f" counts={packet['component_status_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
