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


def _enforce_temporal_boundary(
    row: dict, decision_date: str, generated_at_dt: dt.datetime
) -> dict:
    """Fail-closed common time boundary applied to every component row,
    regardless of which builder produced it:

    1. as_of_date must not be after decision_date -- evidence dated later
       than the day being decided for is a future leak.
    2. available_at (when the underlying source declares one) must not be
       after generated_at -- evidence that only became available after
       this packet was generated did not exist yet at generation time and
       must not be promoted to a decision-ready status.

    A row that violates either boundary is downgraded to DATA_BLOCKED with
    a boundary-specific reason, its packet cleared. This is deliberately
    generic and applied once, centrally, in build_packet() -- a future
    sensor cannot silently smuggle future-dated or not-yet-available
    evidence past this check simply by not implementing its own guard.
    Today every real source's available_at is null (unratified), so this
    is defense-in-depth rather than something live evidence currently
    triggers; see test_daily_orchestrator.py for the monkeypatched proof
    that the mechanism itself works.
    """
    as_of_date = row.get("as_of_date")
    if isinstance(as_of_date, str) and as_of_date > decision_date:
        return component_row(
            row["component_id"], "DATA_BLOCKED", "AS_OF_DATE_AFTER_DECISION_DATE",
            as_of_date=as_of_date,
        )
    available_at = row.get("available_at")
    if available_at is not None:
        try:
            available_at_dt = dt.datetime.fromisoformat(
                str(available_at).replace("Z", "+00:00")
            )
        except ValueError:
            return component_row(
                row["component_id"], "DATA_BLOCKED", "AVAILABLE_AT_UNPARSEABLE",
                as_of_date=as_of_date,
            )
        if available_at_dt.tzinfo is None:
            available_at_dt = available_at_dt.replace(tzinfo=UTC)
        if available_at_dt > generated_at_dt:
            return component_row(
                row["component_id"], "DATA_BLOCKED", "AVAILABLE_AT_AFTER_GENERATED_AT",
                as_of_date=as_of_date,
            )
    return row


def _dated_dir_for_decision(root: Path, decision_date: str) -> Path | None:
    """Return root/decision_date iff it exists as a real directory.

    Never falls back to "whatever is latest" -- a daily-capture archive is
    keyed by capture date, and reading a *different* date's directory and
    presenting it as this decision_date's evidence would either leak future
    information (if a later date's evidence happens to already be
    committed, e.g. during a same-day rerun after a new capture landed) or
    silently substitute stale data for a day that genuinely has none. Both
    are wrong; DATA_BLOCKED for this exact date is the only honest result
    when the exact directory is absent.
    """
    candidate = Path(root) / decision_date
    if candidate.is_dir() and not candidate.is_symlink():
        return candidate
    return None


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
        # False, not True: this reads a mutable rolling pointer with no
        # per-date archive (see NON_REVALIDATABLE_COMPONENTS), so this row
        # cannot honestly promise to be independently re-derivable at any
        # future point the way every other component's validated=True does.
        validated=False,
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
        # False, not True: see NON_REVALIDATABLE_COMPONENTS -- this reads
        # the same mutable rolling pointer as STEP0_READ_MODEL_HEALTH.
        validated=False,
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


def _filing_content_status(component_id: str, status_file: str, decision_date: str) -> dict:
    # data/latest_{dart,sec}_content.json is a mutable rolling pointer --
    # collect.yml overwrites it every collection cycle. There is no
    # per-date archive for it. That makes two things true: (1) reading it
    # for a decision_date other than "whatever it currently says" would be
    # a future/wrong-date leak, so this component is DATA_BLOCKED for any
    # decision_date it does not currently attest to; (2) independent
    # re-validation of a past day's status is not reconstructable from
    # this file once it has rolled forward -- see NON_REVALIDATABLE_
    # COMPONENTS and validate_packet() for how that is handled honestly
    # rather than silently declared stable forever.
    path = ROOT / status_file
    if not path.exists():
        return _blocked(component_id, "UNAVAILABLE", "STATUS_FILE_MISSING")
    try:
        payload = _read_json(path)
    except DailyOrchestratorError as exc:
        return component_row(component_id, "DEGRADED", str(exc))
    if payload.get("collected_for_kst_date") != decision_date:
        return component_row(
            component_id,
            "DATA_BLOCKED",
            "NO_CONTENT_STATUS_FOR_DECISION_DATE",
            as_of_date=payload.get("collected_for_kst_date"),
            source_packet_path=status_file,
            validated=False,
        )
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
        # False, not True: see NON_REVALIDATABLE_COMPONENTS -- this reads
        # the same class of mutable rolling pointer as STEP0/KRX_PREOPEN.
        validated=False,
        authority=payload.get("authority"),
        contract_version=payload.get("contract_version"),
        packet={"run_status": payload.get("run_status"), "counts": payload.get("counts"), "record_count": count},
    )


def build_dart_filing_content(decision_date: str) -> dict:
    return _filing_content_status(
        "DART_FILING_CONTENT", "data/latest_dart_content.json", decision_date
    )


def build_sec_filing_content(decision_date: str) -> dict:
    return _filing_content_status(
        "SEC_FILING_CONTENT", "data/latest_sec_content.json", decision_date
    )


# ---------------------------------------------------------------------------
# KOFIA first-seen (persisted evidence only; no provider call)
# ---------------------------------------------------------------------------


def build_kofia_first_seen(decision_date: str) -> dict:
    evidence_root = ROOT / "evidence" / "kofia" / "first_seen"
    day_dir = _dated_dir_for_decision(evidence_root, decision_date)
    run_dir = _latest_run_dir(day_dir) if day_dir is not None else None
    if run_dir is None:
        return _blocked(
            "KOFIA_FIRST_SEEN", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE"
        )
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
        # Surfaced at the row level too (not only nested inside packet), so
        # the common _enforce_temporal_boundary() check in build_packet()
        # can see it uniformly like every other component's available_at.
        available_at=observation.get("available_at"),
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
    # universe_as_of() already forward-fills to the latest snapshot with
    # snapshot_date <= decision_date and refuses anything after it -- this
    # is precisely the as-of-date-safe API the module offers for exactly
    # this purpose. Using anything else (e.g. "whichever snapshot is
    # currently the newest in the repo") would read future-dated evidence
    # whenever decision_date is not literally today.
    try:
        universe = US_BREADTH.universe_as_of(decision_date, raw_root)
    except US_BREADTH.ContractError as exc:
        return component_row(
            "US_BREADTH_MEMBERSHIP", "DATA_BLOCKED", f"{type(exc).__name__}:{exc}"
        )
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


def build_btc_trend(decision_date: str) -> dict:
    snapshot = _dated_dir_for_decision(ROOT / "evidence" / "crypto" / "btc" / "raw", decision_date)
    if snapshot is None:
        return _blocked("BTC_TREND", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE")
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


def build_btc_risk(decision_date: str) -> dict:
    snapshot = _dated_dir_for_decision(ROOT / "evidence" / "crypto" / "btc" / "raw", decision_date)
    if snapshot is None:
        return _blocked("BTC_RISK", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE")
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


def build_stablecoin(decision_date: str) -> dict:
    snapshot = _dated_dir_for_decision(ROOT / "evidence" / "stablecoin" / "raw", decision_date)
    if snapshot is None:
        return _blocked(
            "STABLECOIN_NET_ISSUANCE", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE"
        )
    try:
        packet = STABLECOIN.build_transform(snapshot)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("STABLECOIN_NET_ISSUANCE", exc)
    latest_row = packet["rows"][-1] if packet.get("rows") else {}
    return component_row(
        "STABLECOIN_NET_ISSUANCE",
        "READY",
        None,
        as_of_date=snapshot.name,
        source_packet_path=f"evidence/stablecoin/raw/{snapshot.name}",
        validated=True,
        packet={
            "observation_date": latest_row.get("observation_date"),
            "daily_net_issuance_native_usd_peg": latest_row.get(
                "daily_net_issuance_native_usd_peg"
            ),
            "daily_status": latest_row.get("daily_status"),
            "weekly_net_issuance_native_usd_peg": latest_row.get(
                "weekly_net_issuance_native_usd_peg"
            ),
            "weekly_status": latest_row.get("weekly_status"),
        },
    )


def build_crypto_breadth(decision_date: str) -> dict:
    snapshot = _dated_dir_for_decision(ROOT / "evidence" / "crypto" / "breadth" / "raw", decision_date)
    if snapshot is None:
        return _blocked("CRYPTO_BREADTH", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE")
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


# These four components read a *mutable rolling pointer* file
# (data/briefing_status.json's inputs, data/latest_{dart,sec}_content.json)
# that collect.yml overwrites every collection cycle, with no per-date
# archive behind it. Every other component now reads a genuinely immutable,
# append-only, per-date evidence directory (after the decision_date pinning
# above), so re-deriving them independently at any future validation time is
# both possible and required, and validate_packet() does exactly that for
# all 32 components uniformly -- there is no blind-trust shortcut for any
# of them. An earlier revision of this module *did* special-case these four
# by trusting the persisted row outright instead of re-deriving it; that
# shortcut was removed because it meant a semantic tamper of exactly these
# four rows, followed by recomputing the outer packet_sha256 over the
# tampered payload, was NOT caught -- the row was inserted verbatim rather
# than independently re-checked. validate_packet() no longer has any such
# exemption: tampering any of these four rows and rehashing now fails
# exactly like tampering any other row, by test.
#
# The honest cost of removing that shortcut: because these four have no
# per-date archive, an independent rebuild of them can only match the
# persisted row while the mutable pointer file still reflects the same
# content it did at build time (typically: same collection cycle, often the
# same day). Once collect.yml's next cycle overwrites the pointer, a later
# call to validate_packet() on an otherwise-honest, untampered packet will
# legitimately see these four rows diverge from a fresh rebuild and reject
# the packet with OUTPUT_MISMATCH -- not because it was tampered, but
# because there is nothing left to independently re-derive it from. This is
# a disclosed, tested, structural limitation of a source with no archive
# (see unresolved_boundaries), not a silent gap and not a false-positive
# tamper signal to be papered over with a new ad hoc hash policy. Each of
# these four rows is also marked validated=False rather than True, since
# "validated" for every other component means "independently re-derivable
# at any future point," which is not a promise these four can honestly
# make.
NON_REVALIDATABLE_COMPONENTS = frozenset({
    "STEP0_READ_MODEL_HEALTH", "KRX_PREOPEN_COMPACT",
    "DART_FILING_CONTENT", "SEC_FILING_CONTENT",
})


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

    rows["DART_FILING_CONTENT"] = build_dart_filing_content(decision_date)
    rows["SEC_FILING_CONTENT"] = build_sec_filing_content(decision_date)
    rows["KOFIA_FIRST_SEEN"] = build_kofia_first_seen(decision_date)
    rows["US_BREADTH_MEMBERSHIP"] = build_us_breadth_membership(decision_date)
    rows["BTC_TREND"] = build_btc_trend(decision_date)
    rows["BTC_RISK"] = build_btc_risk(decision_date)
    rows["STABLECOIN_NET_ISSUANCE"] = build_stablecoin(decision_date)
    rows["CRYPTO_BREADTH"] = build_crypto_breadth(decision_date)

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

    # Common fail-closed time boundary, applied uniformly to every row
    # regardless of which builder produced it -- see
    # _enforce_temporal_boundary(). This pass runs over the final row set,
    # after UNIFIED_DECISION/ACTION_RISK_PORTFOLIO_SUMMARY have already
    # consumed their upstream rows; today that ordering is harmless because
    # neither aggregator consumes a component with a real (non-null)
    # available_at (REGIME/ROTATION_DISCOVERY/RULE/PORTFOLIO/ACTION_
    # BOUNDARY are all built empty/UNKNOWN today, and CASH_EXPOSURE/
    # INVERSE/LONG_SHORT never set available_at). A sensor that both sets a
    # real available_at *and* feeds an aggregator would need this check
    # moved earlier, per-row, rather than as one pass at the end -- noted
    # here rather than silently assumed.
    for name in rows:
        rows[name] = _enforce_temporal_boundary(rows[name], decision_date, generated_at_dt)

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
            # STEP0_READ_MODEL_HEALTH/KRX_PREOPEN_COMPACT/DART_FILING_
            # CONTENT/SEC_FILING_CONTENT read a mutable rolling pointer with
            # no per-date archive: validate_packet() no longer trusts them
            # blindly (that let semantic tamper slip past undetected), but
            # that means a genuinely honest, untampered packet can still
            # legitimately fail re-validation for these four once the
            # pointer has moved on -- see NON_REVALIDATABLE_COMPONENTS.
            "ROLLING_POINTER_COMPONENTS_MAY_FAIL_LATER_REVALIDATION_NOT_TAMPER",
            # Same-day recovery (publish() adding a new revision once
            # DATA_BLOCKED components recover) is a real, tested code path,
            # but nothing currently re-invokes it automatically during the
            # day: the workflow has exactly the two approved scheduled
            # entry points (07:05/18:30 KST) plus manual workflow_dispatch.
            # A provider-free automatic same-day retry trigger is not
            # implemented; adding an unapproved new cron to manufacture one
            # was deliberately not done here -- this is left as an honest
            # WBS blocker rather than a silently-claimed capability.
            "SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def _verify_self_hash(packet: dict) -> None:
    """Cheap tamper check only: does packet_sha256 match the packet's own
    bytes? Deliberately not a full independent rebuild -- see the caller in
    publish() for why re-deriving an existing revision from current repo
    state is the wrong check while more evidence for the same decision_date
    may still be legitimately arriving.
    """
    if not isinstance(packet, dict):
        fail("OUTPUT_INVALID", "root must be object")
    digest = packet.get("packet_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256", None)
    if payload_sha256(unsigned) != digest:
        fail("OUTPUT_SHA_MISMATCH", "packet_sha256")


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    _verify_self_hash(packet)
    # Unconditional full rebuild-and-compare, with no exemption for any
    # component -- see the comment on NON_REVALIDATABLE_COMPONENTS for why a
    # prior blind-trust shortcut for those four was removed: it let a
    # semantic tamper of exactly those rows, followed by recomputing the
    # outer packet_sha256, slip past undetected. Every one of the 32 rows
    # must now match a genuinely independent rebuild, with no exceptions.
    #
    # The honest cost: STEP0_READ_MODEL_HEALTH / KRX_PREOPEN_COMPACT /
    # DART_FILING_CONTENT / SEC_FILING_CONTENT read a mutable rolling
    # pointer with no per-date archive, so a rebuild of *those four*
    # specifically can only match the persisted row while the pointer file
    # still reflects what it did at build time. A packet may therefore
    # legitimately fail OUTPUT_MISMATCH later purely because that pointer
    # moved on -- not because anything was tampered. That is a disclosed,
    # structural limitation of a source with no archive (see
    # unresolved_boundaries), not a false-positive tamper signal.
    rebuilt = build_packet(
        packet["slot"], packet["decision_date"], packet["generated_at"], contract,
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


def _format_component_detail(row: dict) -> list[str]:
    """Real, human-meaningful values pulled from a component's own
    retained packet -- never a raw JSON dump. A component with no packet
    (blocked/unavailable) contributes nothing here; its status + reason
    line above already says why there is nothing to show.
    """
    cid = row["component_id"]
    packet = row.get("packet")
    if not packet:
        return []
    lines: list[str] = []
    try:
        if cid == "STEP0_READ_MODEL_HEALTH":
            for name in ("krx", "dart", "sec"):
                source = (packet.get("sources") or {}).get(name)
                if source:
                    lines.append(
                        f"    - {name}: ok={source.get('ok')} failed={source.get('failed')}"
                    )
        elif cid == "KRX_PREOPEN_COMPACT":
            for name in ("krx", "dart", "sec"):
                source = packet.get(name)
                if source:
                    lines.append(
                        f"    - {name}: ok={source.get('ok')} failed={source.get('failed')} "
                        f"date={source.get('collected_for_kst_date')}"
                    )
        elif cid == "KRX_POST_CLOSE":
            summary = packet.get("summary", {})
            lines.append(
                f"    - observed_unconfirmed: symbols={summary.get('observed_symbol_count')} "
                f"decision_eligible={summary.get('decision_eligible_symbol_count')} "
                f"confirmed_same_day={summary.get('confirmed_same_day_count')}"
            )
        elif cid in ("DART_FILING_CONTENT", "SEC_FILING_CONTENT"):
            lines.append(
                f"    - records={packet.get('record_count')} "
                f"run_status={packet.get('run_status')}"
            )
        elif cid == "KOFIA_FIRST_SEEN":
            lines.append(
                f"    - captured_at={packet.get('captured_at_utc')} "
                f"available_at={packet.get('available_at')}"
            )
        elif cid == "US_BREADTH_MEMBERSHIP":
            lines.append(
                f"    - snapshot_date={packet.get('snapshot_date')} "
                f"members={packet.get('member_count')}"
            )
        elif cid == "BTC_TREND":
            lines.append(
                f"    - direction={packet.get('direction')} 200dma={packet.get('dma_200')}"
            )
        elif cid == "BTC_RISK":
            point = packet.get("risk_point", {})
            drawdown = point.get("drawdown", {})
            vol = point.get("realized_volatility", {})
            lines.append(
                f"    - current_drawdown={drawdown.get('current_fraction')} "
                f"max_drawdown={drawdown.get('maximum_fraction')} "
                f"realized_vol_annualized={vol.get('annualized_fraction')}"
            )
        elif cid == "STABLECOIN_NET_ISSUANCE":
            lines.append(
                f"    - {packet.get('observation_date')}: "
                f"daily_net_issuance={packet.get('daily_net_issuance_native_usd_peg')} "
                f"({packet.get('daily_status')}), "
                f"weekly_net_issuance={packet.get('weekly_net_issuance_native_usd_peg')} "
                f"({packet.get('weekly_status')})"
            )
        elif cid == "CRYPTO_BREADTH":
            lines.append(
                f"    - status={packet.get('status')} "
                f"selected_assets={packet.get('selected_asset_count')}"
            )
        elif cid == "THREE_MARKET_REGIME_HEADER":
            for market in packet.get("markets", []):
                coverage = market.get("coverage", {})
                lines.append(
                    f"    - {market.get('market')}: regime={market.get('regime')} "
                    f"direction={market.get('direction')} "
                    f"confidence={market.get('confidence')} "
                    f"coverage={coverage.get('ratio')}"
                )
        elif cid == "ROTATION_DISCOVERY":
            summary = packet.get("summary", {})
            lines.append(
                f"    - rotation_changes={summary.get('rotation_change_count')} "
                f"discovery_cases={summary.get('discovery_case_count')} "
                f"new_candidates={summary.get('new_candidate_count')} "
                f"existing_candidate_changes={summary.get('existing_candidate_change_count')}"
            )
        elif cid == "RULE_EVALUATION":
            summary = packet.get("summary", {})
            lines.append(
                f"    - total_rules={summary.get('total_rules')} "
                f"PASS={summary.get('PASS')} FAIL={summary.get('FAIL')} "
                f"UNKNOWN={summary.get('UNKNOWN')} UNDEFINED={summary.get('UNDEFINED')}"
            )
        elif cid == "UNIFIED_DECISION":
            decision = packet.get("decision", {})
            summary = packet.get("summary", {})
            lines.append(
                f"    - state={decision.get('state')} action={decision.get('action')} "
                f"order_intent={decision.get('order_intent')} "
                f"available_components={summary.get('available_component_count')}/"
                f"{summary.get('component_count')}"
            )
        elif cid == "ACTION_RISK_PORTFOLIO_SUMMARY":
            summary = packet.get("summary", {})
            lines.append(
                f"    - available_sources={summary.get('available_source_count')}/"
                f"{summary.get('source_count')} "
                f"evaluated_actions={summary.get('evaluated_action_count')} "
                f"risk_breach_sources={summary.get('risk_breach_source_count')}"
            )
        elif cid.startswith("CASH_EXPOSURE_"):
            lines.append(
                f"    - regime={packet.get('regime')} "
                f"cash_action={packet.get('cash_action')} "
                f"evaluation_status={packet.get('evaluation_status')}"
            )
        elif cid.startswith("INVERSE_"):
            lines.append(
                f"    - regime={packet.get('regime')} "
                f"inverse_signal={packet.get('inverse_signal')} "
                f"invariant_status={packet.get('invariant_status')}"
            )
        elif cid == "LONG_SHORT_INVARIANT":
            summary = packet.get("summary", {})
            lines.append(
                f"    - long_results={summary.get('long_results')} "
                f"short_pass={summary.get('short_pass')} "
                f"short_not_evaluated={summary.get('short_not_evaluated')}"
            )
    except (AttributeError, TypeError, KeyError):
        # A packet shape the renderer does not recognize must never break
        # the whole briefing render -- fall back to no detail line rather
        # than raising, the status/reason line above still stands.
        return []
    return lines


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
            lines.extend(_format_component_detail(row))
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
# Publication (atomic, append-only revisions, outside-repo-forbidden like its
# peers is NOT applied here on purpose: the orchestrator's whole point is to
# persist a committed daily record, unlike the ad hoc P8 packet builders it
# calls).
#
# evidence/daily_briefing/{slot}/{decision_date}/ holds one or more
# rev-NNN/ directories (packet.json + briefing.md each) plus a
# deterministic index.json naming the latest one. A first publish for a
# (slot, decision_date) that produced many DATA_BLOCKED/DEGRADED components
# (e.g. because a sensor's capture had not landed yet) is not a dead end:
# calling publish() again the same day re-aggregates from whatever
# evidence exists *now* -- still provider-free, still decision_date-pinned
# -- and adds a new revision only if that materially changed something.
# No prior revision is ever overwritten or deleted.
# ---------------------------------------------------------------------------

INDEX_SCHEMA_VERSION = 1


def _read_index(date_dir: Path) -> dict | None:
    index_path = date_dir / "index.json"
    if not index_path.exists():
        return None
    return _read_json(index_path)


def _component_semantic_fingerprint(packet: dict) -> dict[str, str]:
    """Per-component fingerprint used to decide whether a same-day
    republish materially changed anything worth a new revision for.

    Deliberately NOT just {component_id: status}: a component can stay
    READY -> READY across two builds while its actual retained value,
    reason, source path/sha, or authority silently changed underneath --
    e.g. a corrected same-day KRX re-collection, a filing count that grew,
    or a source path that moved -- and a status-only comparison would miss
    every one of those as "nothing changed". This hashes every row field
    except `generated_at`, which legitimately differs on every publish()
    call (it is the orchestrator's own invocation timestamp) even when
    nothing substantive changed.

    That same invocation timestamp is also threaded, as an argument, into
    several downstream synthetic packets built from it -- REGIME (hence
    also CASH_EXPOSURE_*/INVERSE_*, built from Regime's output),
    THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, ACTION_BOUNDARY,
    UNIFIED_DECISION, ACTION_RISK_PORTFOLIO_SUMMARY. It does not merely
    reappear verbatim there -- those packets also embed *hashes computed
    over* their own generated_at-tainted content (packet_sha256,
    source_sha256), which differ unpredictably even after the literal
    timestamp string is removed, since a hash cannot be un-derived.
    _strip_fingerprint_noise() therefore drops that known, fixed set of
    derived/invocation-timestamp key names from the row's nested `packet`
    content before hashing. Every one of those keys is either purely
    generated_at-derived or already duplicated by a row-level field that
    IS still hashed (e.g. a nested `source_sha256` is dropped here, but
    the row's own `source_packet_sha256` is not) -- so this loses no real
    signal. Any genuinely real evidence value (direction, drawdown,
    issuance amount, member count, captured_at_utc, observed_at_utc) is
    unaffected and still detected as a material change.
    """
    fingerprint: dict[str, str] = {}
    for row in packet["components"]:
        material = {key: value for key, value in row.items() if key != "generated_at"}
        material["packet"] = _strip_fingerprint_noise(material.get("packet"))
        if row["component_id"] in _GENERATED_AT_TAINTED_SELF_HASH_COMPONENTS:
            # These components' own row-level source_packet_sha256 IS their
            # packet's self-hash (packet.get("packet_sha256")), and that
            # packet's content is itself built from the generated_at
            # argument -- so the hash changes on every call even with
            # identical real content, same as the nested packet_sha256/
            # source_sha256 fields _strip_fingerprint_noise() already
            # drops. Real evidence-based components (KRX_PREOPEN_COMPACT,
            # DART/SEC, KOFIA, BTC/stablecoin/US-breadth/crypto-breadth)
            # are NOT in this set -- their source_packet_sha256 (when set)
            # reflects real, generated_at-independent source content and
            # must remain part of the fingerprint.
            material["source_packet_sha256"] = None
        fingerprint[row["component_id"]] = payload_sha256(material)
    return fingerprint


# Components whose packet is built purely from decision_date/slot/
# generated_at plus already-empty/UNKNOWN upstream state -- no real
# external evidence feeds them today -- and whose row-level
# source_packet_sha256 is therefore their own generated_at-tainted
# self-hash rather than a real, independent source signal. See the comment
# in _component_semantic_fingerprint() above.
_GENERATED_AT_TAINTED_SELF_HASH_COMPONENTS = frozenset({
    "KRX_POST_CLOSE", "THREE_MARKET_REGIME_HEADER", "ROTATION_DISCOVERY",
    "ACTION_BOUNDARY", "UNIFIED_DECISION", "ACTION_RISK_PORTFOLIO_SUMMARY",
    "CASH_EXPOSURE_US", "CASH_EXPOSURE_KOREA", "CASH_EXPOSURE_CRYPTO",
    "INVERSE_US", "INVERSE_KOREA", "INVERSE_CRYPTO",
})


_FINGERPRINT_NOISE_KEYS = frozenset({"as_of_utc", "packet_id", "source_as_of"})


def _is_fingerprint_noise_key(key: str) -> bool:
    return (
        key in _FINGERPRINT_NOISE_KEYS
        or key.endswith("sha256")
        or "generated_at" in key
    )


def _strip_fingerprint_noise(value):
    """Recursively drop known generated_at-derived noise from nested
    packet content before fingerprinting:

    - Any key containing "generated_at" as a substring (generated_at,
      source_generated_at, generated_at_kst, and any future field named
      the same way) -- the literal invocation timestamp, which
      legitimately differs on every publish() call.
    - Any key ending in "sha256" (packet_sha256, source_sha256,
      input_packet_sha256, ...) -- a hash *computed over*
      generated_at-tainted content, which cannot be un-derived by
      blanking the literal timestamp string it was built from.
    - A small fixed set of other timestamp/id fields that are equally
      generated_at-derived (as_of_utc, packet_id, source_as_of).

    This never drops a row's own top-level source_packet_sha256 (handled
    separately, per component, in _component_semantic_fingerprint()) or
    any plain real value (direction, drawdown, issuance amount, member
    count, status, reason, captured_at_utc, observed_at_utc) -- only
    nested hash/timestamp fields matching the rules above.
    """
    if isinstance(value, dict):
        return {
            key: _strip_fingerprint_noise(item)
            for key, item in value.items()
            if not _is_fingerprint_noise_key(key)
        }
    if isinstance(value, list):
        return [_strip_fingerprint_noise(item) for item in value]
    return value


def _write_index_atomic(date_dir: Path, index: dict) -> None:
    temp = date_dir / f".index.json.tmp.{os.getpid()}"
    try:
        temp.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(date_dir / "index.json")
    finally:
        if temp.exists():
            temp.unlink()


def publish(
    slot: str, decision_date: str, generated_at: str, evidence_root: Path = EVIDENCE_ROOT
) -> dict:
    packet = build_packet(slot, decision_date, generated_at)
    validate_packet(packet)
    rendered = render_markdown(packet)

    date_dir = Path(evidence_root) / slot / decision_date
    index = _read_index(date_dir)
    revisions = list(index["revisions"]) if index else []

    if revisions:
        latest_entry = revisions[-1]
        latest_dir = date_dir / latest_entry["path"]
        try:
            latest_packet = _read_json(latest_dir / "packet.json")
            # A cheap self-hash check, not a full validate_packet()
            # rebuild-and-compare: while a decision_date's evidence is
            # still actively arriving (exactly the same-day recovery case
            # this revision scheme exists for), a component that was
            # DATA_BLOCKED when the existing revision was published can
            # legitimately resolve to real evidence now -- that is the
            # trigger for a new revision, not evidence the existing one was
            # tampered. A full independent re-derivation would conflate the
            # two. Self-hash tamper (edited bytes, stale digest) is still
            # caught; a full independent re-check of a revision that is not
            # being superseded remains available via validate_packet().
            _verify_self_hash(latest_packet)
        except DailyOrchestratorError as exc:
            # Never skip past a bundle without checking it: a corrupted
            # existing revision must be surfaced, not silently ignored in
            # favor of quietly adding a new one on top of it.
            fail("EXISTING_REVISION_INVALID", f"{latest_dir}: {exc}")
        if latest_packet["packet_sha256"] != latest_entry["packet_sha256"]:
            fail("INDEX_ENTRY_MISMATCH", str(latest_dir))
        if _component_semantic_fingerprint(latest_packet) == _component_semantic_fingerprint(
            packet
        ):
            # Every decision_date-pinned component resolves to the same
            # immutable evidence it did before (see NON_REVALIDATABLE_
            # COMPONENTS for the disclosed exception), so an identical
            # semantic fingerprint -- status, reason, retained values,
            # source path/sha, authority, all of it, not just status --
            # means provider-free re-aggregation truly found nothing new.
            # Publishing an identical-in-substance revision would be noise,
            # not recovery -- reuse the existing one.
            return {"path": latest_dir, "revision": latest_entry["revision"], "created": False}

    revision_number = len(revisions) + 1
    revision_name = f"rev-{revision_number:03d}"
    target_dir = date_dir / revision_name
    if target_dir.exists():
        fail("APPEND_ONLY_VIOLATION", str(target_dir))

    date_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = date_dir / f".{revision_name}.tmp.{os.getpid()}"
    if temp_dir.exists():
        _rmtree(temp_dir)
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
            _rmtree(temp_dir)

    new_index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "slot": slot,
        "decision_date": decision_date,
        "revisions": revisions + [{
            "revision": revision_number,
            "path": revision_name,
            "packet_sha256": packet["packet_sha256"],
            "generated_at": generated_at,
            "component_status_counts": packet["component_status_counts"],
        }],
        "latest_revision": revision_number,
    }
    _write_index_atomic(date_dir, new_index)
    return {"path": target_dir, "revision": revision_number, "created": True}


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)


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
        result = publish(args.slot, args.decision_date, args.generated_at)
        print(f"path={result['path']}")
        print(f"revision={result['revision']}")
        print(f"created={'true' if result['created'] else 'false'}")
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
