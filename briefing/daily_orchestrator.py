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
import re
import subprocess
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
MACHINE_REASON_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


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
LIVE_AXIS_ADAPTER = _load(
    "atlas_daily_live_axis_adapter", "regime/live_axis_adapter.py"
)
FRED_VIX_PROVENANCE = _load(
    "atlas_daily_fred_vix_provenance", "collectors/fred_vix_provenance.py"
)
FREE_MARKET_DATA_CAPTURE = _load(
    "atlas_daily_free_market_data_capture", "collectors/free_market_data.py"
)
HEADER = _load("atlas_daily_header", "briefing/three_market_regime_header.py")
LEDGER = _load("atlas_daily_ledger", "rotation/rotation_state_ledger.py")
DISCOVERY = _load("atlas_daily_discovery", "discovery/event_case.py")
EVENT_POPULATION = _load(
    "atlas_daily_event_population", "discovery/event_population.py"
)
BUSINESS_ACCELERATION_POPULATION = _load(
    "atlas_daily_business_acceleration_population",
    "discovery/business_acceleration_population.py",
)
OFFICIAL_RELEASE_SUMMARY = _load(
    "atlas_daily_official_release_summary",
    "discovery/official_release_summary_observation.py",
)
ROTATION_DISCOVERY = _load("atlas_daily_rotation_discovery", "briefing/rotation_discovery.py")
BINDING = _load("atlas_daily_binding", "bridge/rule_evidence_binding.py")
EVALUATOR = _load("atlas_daily_evaluator", "rules/deterministic_rule_evaluator.py")
ACTION_BOUNDARY = _load("atlas_daily_action_boundary", "decision/ready_signal_order_boundary.py")
DYNAMIC_CLOCK_SIGNAL = _load(
    "atlas_daily_dynamic_clock_signal_observation",
    "decision/dynamic_clock_signal_observation.py",
)
UNIFIED = _load("atlas_daily_unified", "decision/unified_decision_contract.py")
INVESTMENT_REVIEW = _load(
    "atlas_daily_investment_review", "decision/investment_decision_review.py"
)
INVESTMENT_SHADOW = _load(
    "atlas_daily_investment_shadow", "shadow/investment_review_shadow_ledger.py"
)
CASH_EXPOSURE = _load("atlas_daily_cash_exposure", "portfolio/cash_exposure_action.py")
CAPITAL_FLOW_ENGINE = _load(
    "atlas_daily_capital_flow_engine",
    "portfolio/capital_flow_posture_reference.py",
)
INVERSE = _load("atlas_daily_inverse", "portfolio/regime_inverse_invariant.py")
LONG_SHORT = _load("atlas_daily_long_short", "portfolio/long_short_invariant.py")
DEFENSIVE_ACTION_DECISION = _load(
    "atlas_daily_defensive_action_decision",
    "portfolio/defensive_action_decision.py",
)
STRATEGIC_CAPITAL_POSTURE = _load(
    "atlas_daily_strategic_capital_posture",
    "portfolio/strategic_capital_posture.py",
)
ACTION_SUMMARY = _load("atlas_daily_action_summary", "briefing/action_risk_portfolio_summary.py")
FLOW_FIRST_BRIEFING = _load(
    "atlas_daily_flow_first_briefing", "briefing/flow_first_briefing.py"
)
KRX_POST_CLOSE = _load("atlas_daily_krx_post_close", "briefing/krx_post_close.py")
BRIEFING_READINESS = _load("atlas_daily_readiness", ".github/scripts/check_briefing_readiness.py")
# P8-11 stage 2 -- Forward Alpha Review Pilot summary. Loaded defensively: a
# load failure here must never take down the whole orchestrator (see
# build_forward_alpha_review_status()'s own try/except for the analogous
# call-time guard). This is the ONLY `_load(...)` call in this file wrapped
# this way, precisely because it is new and additive.
try:
    PILOT_ALPHA_REVIEW = _load(
        "atlas_daily_pilot_alpha_review", "decision/pilot_evidence_intake.py"
    )
except Exception:  # noqa: BLE001
    PILOT_ALPHA_REVIEW = None
# P8-12 -- Dynamic Clock (Opportunity Trigger + tiered Review Queue).
# Loaded defensively for the same reason as PILOT_ALPHA_REVIEW above: a
# load failure here must never take down the whole orchestrator.
try:
    DYNAMIC_CLOCK = _load("atlas_daily_dynamic_clock", "clock/run_dynamic_clock.py")
except Exception:  # noqa: BLE001
    DYNAMIC_CLOCK = None
# P5-06 -> P7-08 -> P8-13 review-only bridge. This is deliberately loaded
# independently from DYNAMIC_CLOCK: the bridge validates the exact committed
# Dynamic Clock/identity/contract generation and exposes only the resulting
# zero-capital human-review surface.
try:
    SHADOW_ENTRY_REVIEW = _load(
        "atlas_daily_shadow_entry_review", "decision/shadow_entry_review.py"
    )
except Exception:  # noqa: BLE001
    SHADOW_ENTRY_REVIEW = None
BTC_TREND = _load("atlas_daily_btc_trend", ".github/scripts/btc_trend.py")
BTC_RISK = _load("atlas_daily_btc_risk", ".github/scripts/btc_risk.py")
STABLECOIN = _load("atlas_daily_stablecoin", ".github/scripts/stablecoin_net_issuance.py")
US_BREADTH = _load("atlas_daily_us_breadth", ".github/scripts/us_breadth_forward.py")
CRYPTO_BREADTH = _load("atlas_daily_crypto_breadth", ".github/scripts/crypto_breadth.py")
CRYPTO_LEADERSHIP = _load(
    "atlas_daily_crypto_leadership", ".github/scripts/crypto_leadership.py"
)
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


def _unavailable_reason(name: str, row: dict) -> str:
    """Return one contract-safe reason while preserving the diagnostic row.

    Component rows intentionally retain human-readable exception text.  The
    downstream decision contracts accept only bounded uppercase reason codes,
    so forwarding an exception class verbatim would turn one unavailable
    component into a second, avoidable orchestration failure.
    """
    reason = row.get("reason")
    if isinstance(reason, str) and MACHINE_REASON_RE.fullmatch(reason):
        return reason
    status = row.get("status")
    if status not in STATUS_VALUES:
        status = "UNAVAILABLE"
    return f"{name}_{status}"


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
    regardless of which builder produced it, and applied IMMEDIATELY after
    each row is built (see build_packet()) -- never after a downstream
    aggregator (UNIFIED_DECISION, ACTION_RISK_PORTFOLIO_SUMMARY) has
    already consumed it:

    1. as_of_date must not be after decision_date -- evidence dated later
       than the day being decided for is a future leak.
    2. available_at (when the underlying source declares one) must not be
       after generated_at -- evidence that only became available after
       this packet was generated did not exist yet at generation time and
       must not be promoted to a decision-ready status.
    3. The row's own generated_at -- which several real sensors set to a
       genuine, independent capture/observation timestamp rather than the
       packet's own invocation time (KOFIA_FIRST_SEEN: captured_at_utc;
       DART_FILING_CONTENT/SEC_FILING_CONTENT: observed_at_utc;
       KRX_POST_CLOSE: its own generated_at_kst) -- must not be after the
       packet's own generated_at either. For the synthetic components that
       simply pass the packet's own generated_at straight through
       (THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, ACTION_BOUNDARY,
       UNIFIED_DECISION, ...), this is always exactly equal, never after,
       so it is a no-op for them.

    A row that violates any of these is downgraded to DATA_BLOCKED with a
    boundary-specific reason, its packet cleared. This is deliberately
    generic -- a future sensor cannot silently smuggle future-dated,
    not-yet-available, or future-captured evidence past this check simply
    by not implementing its own guard. Today every real source's
    available_at is null (unratified) and no real sensor's own
    captured/observed timestamp has ever exceeded the packet's own
    generated_at, so this is defense-in-depth rather than something live
    evidence currently triggers; see test_daily_orchestrator.py for the
    monkeypatched proof that the mechanism itself works, including that a
    violation is caught before it can reach UNIFIED_DECISION or
    ACTION_RISK_PORTFOLIO_SUMMARY.

    A row whose temporal basis is genuinely unknown (no as_of_date and no
    available_at/generated_at at all -- e.g. a fully blocked/unavailable
    row) has nothing here to violate and passes through unchanged; it was
    never promoted to READY/validated in the first place by its own
    builder, so there is nothing to demote.
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
    source_generated_at = row.get("generated_at")
    if source_generated_at is not None:
        try:
            source_generated_at_dt = dt.datetime.fromisoformat(
                str(source_generated_at).replace("Z", "+00:00")
            )
        except ValueError:
            return component_row(
                row["component_id"], "DATA_BLOCKED", "SOURCE_GENERATED_AT_UNPARSEABLE",
                as_of_date=as_of_date,
            )
        if source_generated_at_dt.tzinfo is None:
            source_generated_at_dt = source_generated_at_dt.replace(tzinfo=UTC)
        if source_generated_at_dt > generated_at_dt:
            return component_row(
                row["component_id"], "DATA_BLOCKED",
                "SOURCE_GENERATED_AT_AFTER_PACKET_GENERATED_AT",
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


def _read_source_collected_at_utc(data_root: Path, name: str) -> str | None:
    """Raw collected_at_utc string for data/latest_{name}.json (name is
    "krx"/"dart"/"sec"), or None if the file is missing/unreadable or the
    field itself is missing/not a string. Validity (parseable/timezone-
    aware/exactly UTC) is deliberately NOT checked here -- that happens in
    _qualify_collected_at_utc, a pure function operating on the frozen
    raw value, so an invalid-but-present string is still faithfully
    frozen and independently re-qualifiable later, not silently
    discarded into a bare None at fetch time."""
    path = Path(data_root) / f"latest_{name}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("collected_at_utc")
    return value if isinstance(value, str) else None


def _qualify_collected_at_utc(raw_values: dict[str, str | None]) -> dict:
    """Pure function: given the three raw collected_at_utc strings (keys
    "krx"/"dart"/"sec", any of which may be None or invalid), decide
    whether STEP0_READ_MODEL_HEALTH/KRX_PREOPEN_COMPACT have a real,
    trustworthy temporal basis at all.

    Every one of the three must independently be present, a string,
    ISO-8601 parseable, timezone-aware, and exactly UTC (+00:00) -- a
    naive timestamp or one with a non-UTC offset is exactly the kind of
    ambiguity a real "was this actually UTC" check exists to catch, not
    something to silently accept as if it were UTC. Missing/invalid
    disqualifies the *whole* triple: a packet must not be judged
    temporally safe based on only two of its three read-model sources.

    On success: {"ok": True, "generated_at": <latest of the three,
    ISO format>} -- the most conservative (latest) of the three, matching
    every other conservative-timestamp choice in this module.
    On failure: {"ok": False, "reason": <first disqualifying reason found,
    checked in krx/dart/sec order>} -- never a promotion to READY.
    """
    parsed: dict[str, dt.datetime] = {}
    for name in ("krx", "dart", "sec"):
        value = raw_values.get(name)
        label = name.upper()
        if value is None:
            return {"ok": False, "reason": f"{label}_COLLECTED_AT_UTC_MISSING"}
        try:
            candidate = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return {"ok": False, "reason": f"{label}_COLLECTED_AT_UTC_UNPARSEABLE"}
        if candidate.tzinfo is None:
            return {"ok": False, "reason": f"{label}_COLLECTED_AT_UTC_NAIVE"}
        if candidate.utcoffset() != dt.timedelta(0):
            return {"ok": False, "reason": f"{label}_COLLECTED_AT_UTC_NOT_UTC"}
        parsed[name] = candidate
    return {"ok": True, "generated_at": max(parsed.values()).isoformat()}


def _fetch_step0_snapshot(decision_date: str) -> dict:
    """Raw, unclassified snapshot of the read-model health source for
    decision_date, read from live collector state right now. This exact
    dict is what gets frozen into packet["frozen_sources"] at build time,
    so a later validate_packet() call can re-derive
    STEP0_READ_MODEL_HEALTH's (and, since it is built from this same
    snapshot, KRX_PREOPEN_COMPACT's) status purely from that frozen value
    -- no live re-fetch of the mutable data/ pointer needed, ever again.
    The raw (unvalidated) collected_at_utc string per source is frozen
    here; _qualify_collected_at_utc() -- a pure function with no I/O of
    its own -- re-derives the same qualification verdict from it forever,
    whether called live or replayed from frozen_sources.
    """
    try:
        payload = BRIEFING_READINESS.evaluate(decision_date, BRIEFING_READINESS.DATA)
    except Exception as exc:  # noqa: BLE001 - isolate any read-model failure
        return {"kind": "error", "value": f"{type(exc).__name__}:{exc}"}
    collected_at_utc_raw = {
        name: _read_source_collected_at_utc(BRIEFING_READINESS.DATA, name)
        for name in ("krx", "dart", "sec")
    }
    return {"kind": "payload", "value": payload, "collected_at_utc_raw": collected_at_utc_raw}


def _classify_step0(decision_date: str, snapshot: dict) -> dict:
    """Pure function (no I/O): derive the STEP0_READ_MODEL_HEALTH row from
    an already-fetched (live or frozen-replayed) snapshot."""
    if snapshot["kind"] == "error":
        return component_row("STEP0_READ_MODEL_HEALTH", "DEGRADED", snapshot["value"])
    payload = snapshot["value"]
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
    qualification = _qualify_collected_at_utc(snapshot.get("collected_at_utc_raw") or {})
    if not qualification["ok"]:
        # Fail-closed regardless of what the read-model itself claims: a
        # missing/invalid/naive/non-UTC collected_at_utc means there is no
        # trustworthy temporal basis to promote this to READY on, and
        # this row cannot honestly claim eternal re-derivability either
        # (a future qualification of the same frozen raw value must be
        # able to reach the same disqualifying verdict, which it can --
        # but the row itself was never actually validated against real
        # timing, so validated stays False here, unlike the qualified
        # case below).
        if status == "READY":
            status = "DEGRADED"
        reason = f"TEMPORAL_QUALIFICATION_FAILED:{qualification['reason']}"
        return component_row(
            "STEP0_READ_MODEL_HEALTH",
            status,
            reason,
            as_of_date=decision_date,
            source_packet_path="data/briefing_status.json",
            validated=False,
            packet=payload,
        )
    return component_row(
        "STEP0_READ_MODEL_HEALTH",
        status,
        reason,
        as_of_date=decision_date,
        # The real, conservative collection timestamp (fed to
        # _enforce_temporal_boundary below), not left null -- a packet
        # generated before krx/dart/sec were actually collected must not
        # read them as READY.
        generated_at=qualification["generated_at"],
        source_packet_path="data/briefing_status.json",
        # True: this component's underlying input is now frozen into
        # packet["frozen_sources"] at build time (see _fetch_step0_
        # snapshot), so it genuinely IS independently re-derivable at any
        # future point from that frozen value alone -- no live re-fetch of
        # the mutable data/ pointer required. See FROZEN_SOURCE_COMPONENTS.
        validated=True,
        packet=payload,
    )


def build_step0_health(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_step0_snapshot(decision_date)
    return _classify_step0(decision_date, snapshot)


def build_krx_preopen_compact(
    decision_date: str,
    step0_packet: dict | None,
    collected_at_utc_raw: dict[str, str | None] | None = None,
) -> dict:
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
    qualification = _qualify_collected_at_utc(collected_at_utc_raw or {})
    if not step0_packet.get("data_ready"):
        status, reason = "DATA_BLOCKED", "COLLECTOR_DATA_NOT_READY_FOR_DECISION_DATE"
    elif not step0_packet.get("read_model_ready"):
        status, reason = "DEGRADED", "READ_MODEL_ONLY_NOT_FULLY_READY"
    elif not qualification["ok"]:
        # Same fail-closed rule as STEP0_READ_MODEL_HEALTH: if the shared
        # krx/dart/sec collected_at_utc triple did not qualify, KRX_
        # PREOPEN_COMPACT must never be READY on its own -- there is no
        # path where STEP0 fails temporal qualification but this sibling,
        # built from the exact same sources, is still promoted.
        status, reason = "DEGRADED", f"TEMPORAL_QUALIFICATION_FAILED:{qualification['reason']}"
    else:
        status, reason = "READY", None
    return component_row(
        "KRX_PREOPEN_COMPACT",
        status,
        reason,
        as_of_date=krx.get("collected_for_kst_date"),
        # Same conservative collected_at_utc as STEP0_READ_MODEL_HEALTH --
        # both derive from the same krx/dart/sec mutable-pointer read.
        # Left None whenever qualification failed, matching the row's own
        # validated=False below.
        generated_at=qualification["generated_at"] if qualification["ok"] else None,
        source_packet_path=krx.get("path"),
        source_packet_sha256=krx.get("source_sha256"),
        # True only when the shared timestamp triple actually qualified --
        # derived purely from step0_packet plus that qualification, whose
        # own underlying input is frozen into packet["frozen_sources"] at
        # build time -- see FROZEN_SOURCE_COMPONENTS.
        validated=qualification["ok"],
        packet={"krx": krx, "dart": sources.get("dart"), "sec": sources.get("sec")},
    )


# ---------------------------------------------------------------------------
# KRX post-close (evening only)
# ---------------------------------------------------------------------------


def _read_krx_post_close_observed_at(target: Path) -> str | None:
    """Conservative real observation timestamp for a KRX post-close
    bundle: the LATEST of source.json's own collected_at_utc and every
    per-symbol observed_at_kst. Latest, not earliest -- the packet must
    not claim readiness before the most-delayed observation in the bundle
    actually happened. Returns None if nothing parseable is found."""
    candidates: list[str] = []
    source_path = target / "source.json"
    if source_path.exists():
        try:
            source = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            source = {}
        value = source.get("collected_at_utc")
        if isinstance(value, str):
            candidates.append(value)
    symbols_dir = target / "symbols"
    if symbols_dir.is_dir():
        for path in sorted(symbols_dir.glob("*.json")):
            try:
                view = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            value = (view.get("observed_row") or {}).get("observed_at_kst")
            if isinstance(value, str):
                candidates.append(value)
    parsed = []
    for value in candidates:
        try:
            parsed.append(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            continue
    if not parsed:
        return None
    return max(parsed).isoformat()


def _fetch_krx_post_close_snapshot(decision_date: str) -> dict:
    """Presence/absence of the KRX post-close bundle, resolved live right
    now, plus (if present) the conservative real observation timestamp.
    Frozen into packet["frozen_sources"]: like the other per-date evidence
    archives, this bundle's *content* is immutable once present (COLLECTOR.
    check_bundle re-validates it against its own committed source.json
    every time), but its *presence* is not -- a bundle that does not exist
    yet at build time (correctly UNKNOWN) can be created later the same
    evening, and re-deriving an old revision after that would wrongly
    promote it to READY on independent re-validation."""
    target = ROOT / "data" / "observations" / "krx_post_close" / decision_date
    # KRX_POST_CLOSE.COLLECTOR is that module's own already-loaded
    # reference to collectors/krx_post_close.py -- reused here rather than
    # loading a second copy of the same collector module.
    if not KRX_POST_CLOSE.COLLECTOR.check_bundle(decision_date, data_root=ROOT / "data"):
        return {"kind": "absent"}
    return {"kind": "present", "observed_at": _read_krx_post_close_observed_at(target)}


def _classify_krx_post_close(
    decision_date: str, generated_at_utc: dt.datetime, snapshot: dict
) -> dict:
    generated_at_kst = generated_at_utc.astimezone(KST).isoformat(timespec="seconds")
    if snapshot["kind"] == "absent":
        # Bypass KRX_POST_CLOSE.build_packet() entirely for the frozen
        # "absent" case: calling it would re-run COLLECTOR.check_bundle()
        # against LIVE state, which may now return True if the bundle has
        # since arrived -- exactly the staleness this freeze exists to
        # prevent. Construct the same UNKNOWN packet that module's own
        # build_packet() would have produced, purely, with no I/O.
        contract = KRX_POST_CLOSE.load_contract()
        logical_root = f"data/observations/krx_post_close/{decision_date}"
        packet = KRX_POST_CLOSE._unknown_packet(
            decision_date, generated_at_kst, contract, logical_root,
            "POST_CLOSE_BUNDLE_MISSING_OR_INVALID",
        )
        packet["packet_sha256"] = KRX_POST_CLOSE.payload_sha256(packet)
    else:
        # The bundle is immutable once present, so re-reading it here
        # (unlike re-reading a mutable rolling pointer) reproduces the
        # exact same content it did at build time -- a genuine, safe
        # independent re-derivation.
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
        # The real, conservative observation timestamp when known (fed to
        # _enforce_temporal_boundary below), not the packet's own
        # invocation-time generated_at_kst -- a packet generated at
        # exactly the 18:00 KST evening floor must not read a bundle whose
        # real observations only landed at 18:11.
        generated_at=snapshot.get("observed_at") or generated_at_kst,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


def build_krx_post_close(
    decision_date: str, generated_at_utc: dt.datetime, snapshot: dict | None = None
) -> dict:
    if snapshot is None:
        snapshot = _fetch_krx_post_close_snapshot(decision_date)
    return _classify_krx_post_close(decision_date, generated_at_utc, snapshot)


# ---------------------------------------------------------------------------
# DART / SEC filing content status (read persisted status files only)
# ---------------------------------------------------------------------------


def _fetch_filing_snapshot(status_file: str) -> dict:
    """Raw snapshot of a DART/SEC mutable status pointer file, read live
    right now. Frozen into packet["frozen_sources"] at build time -- see
    _fetch_step0_snapshot for why.

    The status document's ``source_sha256`` names the upstream metadata
    pointer that produced it, not this content-status file itself.  Preserve
    the exact bytes digest separately so the component's
    ``source_packet_path`` and ``source_packet_sha256`` describe the same
    artifact.
    """
    path = ROOT / status_file
    if not path.exists():
        return {"kind": "missing", "value": None}
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"kind": "error", "value": f"JSON_READ_FAILED:{path}:{exc}"}
    return {
        "kind": "payload",
        "value": payload,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _classify_filing_content(
    component_id: str, status_file: str, decision_date: str, snapshot: dict
) -> dict:
    """Pure function (no I/O): derive a DART/SEC filing-content row from an
    already-fetched (live or frozen-replayed) snapshot. data/latest_{dart,
    sec}_content.json is itself a mutable rolling pointer -- collect.yml
    overwrites it every collection cycle, with no per-date archive behind
    it -- but that no longer matters for independent re-derivation: the
    exact snapshot this row was built from is frozen into packet[
    "frozen_sources"], so re-deriving it later never needs to re-read that
    live pointer again. Reading it for a decision_date other than what it
    currently attests to is still, correctly, DATA_BLOCKED -- a
    future/wrong-date leak -- not a revalidation limitation."""
    if snapshot["kind"] == "missing":
        return _blocked(component_id, "UNAVAILABLE", "STATUS_FILE_MISSING")
    if snapshot["kind"] == "error":
        return component_row(component_id, "DEGRADED", snapshot["value"])
    payload = snapshot["value"]
    if payload.get("collected_for_kst_date") != decision_date:
        return component_row(
            component_id,
            "DATA_BLOCKED",
            "NO_CONTENT_STATUS_FOR_DECISION_DATE",
            as_of_date=payload.get("collected_for_kst_date"),
            source_packet_path=status_file,
            # True: the mismatch itself is now a pure function of the
            # frozen snapshot + decision_date, re-derivable forever.
            validated=True,
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
        # New snapshots bind the exact bytes at source_packet_path.  The
        # fallback keeps historical frozen packets independently replayable;
        # those immutable revisions predate the explicit content digest and
        # remain audit-only rather than being silently rewritten.
        source_packet_sha256=(
            snapshot.get("content_sha256") or payload.get("source_sha256")
        ),
        # True: this snapshot is frozen into packet["frozen_sources"] at
        # build time -- see FROZEN_SOURCE_COMPONENTS.
        validated=True,
        authority=payload.get("authority"),
        contract_version=payload.get("contract_version"),
        packet={"run_status": payload.get("run_status"), "counts": payload.get("counts"), "record_count": count},
    )


def build_dart_filing_content(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_filing_snapshot("data/latest_dart_content.json")
    return _classify_filing_content(
        "DART_FILING_CONTENT", "data/latest_dart_content.json", decision_date, snapshot
    )


def build_sec_filing_content(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_filing_snapshot("data/latest_sec_content.json")
    return _classify_filing_content(
        "SEC_FILING_CONTENT", "data/latest_sec_content.json", decision_date, snapshot
    )


# ---------------------------------------------------------------------------
# Korea Capital Rotation (P2-03 -> rotation_state_ledger -> briefing wiring)
# ---------------------------------------------------------------------------


def _fetch_korea_rotation_snapshot() -> dict:
    return _fetch_filing_snapshot("data/latest_korea_rotation.json")


def _classify_korea_rotation(decision_date: str, snapshot: dict) -> dict:
    """Korea Capital Rotation (P2-03) observation, sourced from the
    committed rolling pointer data/latest_korea_rotation.json --
    refreshed by rotation/korea_capital_rotation_ledger_wire.py from a
    real rotation_state_ledger.apply_rotation() call, never derived
    here. This function does not recompute AVAILABLE/BLOCKED/UNKNOWN/
    STALE; it only surfaces the pointer's own already-independently-
    re-derived breadth.status verbatim -- never relabeling it NEUTRAL,
    PASS, or AVAILABLE. The row's own status reflects only whether a
    genuine rotation observation exists for this exact decision_date;
    a BLOCKED/UNKNOWN/STALE Breadth context still yields a real
    (POLICY_BLOCKED) row with the Breadth blocker surfaced in its
    packet, not a missing row."""
    if snapshot["kind"] == "missing":
        return _blocked("KOREA_ROTATION", "PENDING", "NO_ROTATION_POINTER_PUBLISHED")
    if snapshot["kind"] == "error":
        return component_row("KOREA_ROTATION", "DEGRADED", snapshot["value"])
    payload = snapshot["value"]
    if payload.get("as_of_date") != decision_date:
        return component_row(
            "KOREA_ROTATION",
            "PENDING",
            "NO_ROTATION_OBSERVATION_FOR_DECISION_DATE",
            as_of_date=payload.get("as_of_date"),
            source_packet_path="data/latest_korea_rotation.json",
            validated=True,
        )
    breadth = payload.get("breadth") if isinstance(payload.get("breadth"), dict) else {}
    rotation = payload.get("rotation") if isinstance(payload.get("rotation"), dict) else {}
    breadth_status = breadth.get("status")
    rotation_policy_effective = rotation.get("rotation_policy_effective")
    if payload.get("run_status") != "OK":
        status, reason = "DEGRADED", f"run_status={payload.get('run_status')}"
    elif breadth_status == "AVAILABLE" and rotation_policy_effective:
        status, reason = "READY", None
    else:
        # Breadth and Leadership/rotation-policy are two independent
        # boundaries -- both are surfaced explicitly, never collapsed
        # into a single generic reason, so a reader can tell which one
        # (or both) is the actual blocker.
        status = "POLICY_BLOCKED"
        parts = [f"KOREA_BREADTH_{breadth_status}:{breadth.get('reason')}"]
        if not rotation_policy_effective:
            parts.append(f"KOREA_ROTATION_{rotation.get('status')}:ROTATION_POLICY_NOT_RATIFIED")
        reason = "|".join(parts)
    return component_row(
        "KOREA_ROTATION",
        status,
        reason,
        as_of_date=payload.get("as_of_date"),
        generated_at=payload.get("generated_at"),
        source_packet_path="data/latest_korea_rotation.json",
        source_packet_sha256=payload.get("payload_sha256"),
        # True: this snapshot is frozen into packet["frozen_sources"] at
        # build time -- see FROZEN_SOURCE_COMPONENTS.
        validated=True,
        authority=payload.get("authority"),
        contract_version=payload.get("contract_version"),
        packet={
            "rotation_status": rotation.get("status"),
            "rotation_policy_effective": rotation.get("rotation_policy_effective"),
            "breadth_status": breadth_status,
            "breadth_reason": breadth.get("reason"),
            "breadth_decision_eligible": breadth.get("decision_eligible"),
            "breadth_markets": breadth.get("markets"),
            "breadth_source_context_path": breadth.get("source_context_path"),
            "breadth_source_context_sha256": breadth.get("source_context_sha256"),
            # Informational only -- retrospective/narrative evidence,
            # never a decision input; does not affect status/reason
            # above. See rotation/korea_capital_rotation_ledger_wire.py's
            # build_confirmed_history_context().
            "confirmed_history": payload.get("confirmed_history"),
        },
    )


def build_korea_rotation(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_korea_rotation_snapshot()
    return _classify_korea_rotation(decision_date, snapshot)


# ---------------------------------------------------------------------------
# Korea market-wide five-signal observation (official KRX, policy-neutral)
# ---------------------------------------------------------------------------


def _fetch_korea_market_signals_snapshot() -> dict:
    return _fetch_filing_snapshot("data/latest_korea_market_signals.json")


def _classify_korea_market_signals(decision_date: str, snapshot: dict) -> dict:
    component_id = "KOREA_MARKET_SIGNALS"
    if snapshot["kind"] == "missing":
        return _blocked(component_id, "PENDING", "NO_KOREA_MARKET_SIGNALS_PUBLISHED")
    if snapshot["kind"] == "error":
        return component_row(component_id, "DEGRADED", snapshot["value"])
    payload = snapshot["value"]
    try:
        validated = LIVE_AXIS_ADAPTER.KOREA_MARKET_SIGNALS.validate_packet(payload)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception(component_id, exc)
    if validated["as_of_date"] > decision_date:
        return component_row(
            component_id,
            "DATA_BLOCKED",
            "KOREA_MARKET_SIGNALS_FROM_FUTURE",
            as_of_date=validated["as_of_date"],
            generated_at=validated["generated_at"],
            available_at=validated["available_at"],
            validated=True,
        )
    if validated.get("coverage", {}).get("ratio") != "5/5":
        return component_row(
            component_id,
            "DATA_BLOCKED",
            "KOREA_MARKET_SIGNALS_COVERAGE_INCOMPLETE",
            as_of_date=validated["as_of_date"],
            generated_at=validated["generated_at"],
            available_at=validated["available_at"],
            validated=True,
        )
    return component_row(
        component_id,
        "READY",
        None,
        as_of_date=validated["as_of_date"],
        generated_at=validated["generated_at"],
        available_at=validated["available_at"],
        source_packet_path=(
            "data/observations/korea_market_signals/"
            f"{validated['as_of_date']}/packet.json"
        ),
        # briefing_core/2 rehashes the exact Git bytes at source_packet_path.
        # payload_sha256 is the packet's self-hash (the canonical object with
        # that field removed), so it must not be relabelled as a file hash.
        # The rolling pointer and dated packet are byte-identical at capture
        # time; preserve that exact byte digest in the frozen snapshot.
        source_packet_sha256=(
            snapshot.get("content_sha256") or validated["payload_sha256"]
        ),
        validated=True,
        authority=validated["authority"],
        contract_version=validated["contract_version"],
        packet=validated,
    )


def build_korea_market_signals(snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_korea_market_signals_snapshot()
    return _classify_korea_market_signals("9999-12-31", snapshot)


# ---------------------------------------------------------------------------
# KOFIA first-seen (persisted evidence only; no provider call)
# ---------------------------------------------------------------------------


def _read_downloaded_at(resolved_dir: Path) -> str | None:
    """Every real collector for these evidence archives (BTC, stablecoin,
    crypto breadth, US breadth) writes a top-level _downloaded_at.txt
    alongside the raw capture -- the real UTC instant the source was
    actually fetched, distinct from (and usually much earlier in the day
    than) the packet's own generated_at. Returns None if genuinely
    absent, which callers must treat as "temporal basis unknown", never
    silently as "fine"."""
    path = resolved_dir / "_downloaded_at.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _downloaded_at_guard(component_id: str, snapshot: dict) -> dict | None:
    """None if the snapshot carries a real downloaded_at timestamp (safe
    to proceed to READY/validated=True); otherwise a DEGRADED row. A
    component whose temporal basis is genuinely unknown must never be
    promoted to a decision-ready, validated status -- unlike every real
    committed archive today (which always has _downloaded_at.txt), a
    missing timestamp means there is nothing to prove this evidence was
    not fetched after the packet claims to have been generated."""
    if snapshot.get("downloaded_at") is not None:
        return None
    return component_row(component_id, "DEGRADED", "DOWNLOADED_AT_MISSING")


def _fetch_dated_evidence_snapshot(root: Path, decision_date: str) -> dict:
    """Raw snapshot for an exact-date evidence archive (BTC/stablecoin/
    crypto breadth/KOFIA), read live right now. Frozen into
    packet["frozen_sources"] at build time: presence/absence of the
    directory at build time is fixed forever, so a later
    validate_packet() call can never see a directory that didn't exist
    yet at build time and wrongly promote a DATA_BLOCKED revision to
    READY just because the same-dated capture landed afterward. Once
    present, the directory is a genuinely immutable, append-only, per-date
    archive (unlike the mutable rolling pointers _fetch_step0_snapshot/
    _fetch_filing_snapshot freeze) -- re-reading it later reproduces the
    same content, so only the presence/absence fact, not its bytes, needs
    freezing."""
    resolved = _dated_dir_for_decision(root, decision_date)
    if resolved is None:
        return {"kind": "absent"}
    return {
        "kind": "present",
        "resolved_dir": str(resolved.relative_to(ROOT)),
        "downloaded_at": _read_downloaded_at(resolved),
    }


def _fetch_kofia_snapshot(decision_date: str) -> dict:
    evidence_root = ROOT / "evidence" / "kofia" / "first_seen"
    day_dir = _dated_dir_for_decision(evidence_root, decision_date)
    run_dir = _latest_run_dir(day_dir) if day_dir is not None else None
    if run_dir is None:
        return {"kind": "absent"}
    return {
        "kind": "present",
        "resolved_dir": str(run_dir.relative_to(ROOT)),
        "as_of_date": day_dir.name,
    }


def _classify_kofia(snapshot: dict) -> dict:
    if snapshot["kind"] == "absent":
        return _blocked(
            "KOFIA_FIRST_SEEN", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE"
        )
    run_dir = ROOT / snapshot["resolved_dir"]
    evidence_root = ROOT / "evidence" / "kofia" / "first_seen"
    try:
        observation = KOFIA.validate_capture(run_dir, evidence_root)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("KOFIA_FIRST_SEEN", exc)
    return component_row(
        "KOFIA_FIRST_SEEN",
        "POLICY_BLOCKED",
        "SOURCE_AVAILABLE_AT_AND_API_UNIT_UNRATIFIED",
        as_of_date=snapshot["as_of_date"],
        generated_at=observation.get("captured_at_utc"),
        # Surfaced at the row level too (not only nested inside packet), so
        # the common _enforce_temporal_boundary() check in build_packet()
        # can see it uniformly like every other component's available_at.
        available_at=observation.get("available_at"),
        source_packet_path=snapshot["resolved_dir"],
        # True: frozen into packet["frozen_sources"] at build time -- see
        # FROZEN_SOURCE_COMPONENTS.
        validated=True,
        packet={
            "captured_at_utc": observation.get("captured_at_utc"),
            "available_at": observation.get("available_at"),
            "decision_eligible": observation.get("decision_eligible"),
        },
    )


def build_kofia_first_seen(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_kofia_snapshot(decision_date)
    return _classify_kofia(snapshot)


# ---------------------------------------------------------------------------
# Crypto/US sensors already committed as live evidence
# ---------------------------------------------------------------------------


def _fetch_us_breadth_snapshot(decision_date: str, raw_root: Path) -> dict:
    # universe_as_of() already forward-fills to the latest snapshot with
    # snapshot_date <= decision_date and refuses anything after it -- this
    # is precisely the as-of-date-safe API the module offers for exactly
    # this purpose. Using anything else (e.g. "whichever snapshot is
    # currently the newest in the repo") would read future-dated evidence
    # whenever decision_date is not literally today.
    try:
        universe = US_BREADTH.universe_as_of(decision_date, raw_root)
    except US_BREADTH.ContractError as exc:
        return {"kind": "unresolved", "value": f"{type(exc).__name__}:{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"kind": "error", "value": f"{type(exc).__name__}:{exc}"}
    snapshot_dir = Path(raw_root) / universe["snapshot_date"]
    return {
        "kind": "resolved",
        "snapshot_date": universe["snapshot_date"],
        "downloaded_at": _read_downloaded_at(snapshot_dir),
    }


def _classify_us_breadth(raw_root: Path, snapshot: dict) -> dict:
    if snapshot["kind"] == "unresolved":
        return component_row("US_BREADTH_MEMBERSHIP", "DATA_BLOCKED", snapshot["value"])
    if snapshot["kind"] == "error":
        return component_row("US_BREADTH_MEMBERSHIP", "DEGRADED", snapshot["value"])
    guard = _downloaded_at_guard("US_BREADTH_MEMBERSHIP", snapshot)
    if guard is not None:
        return guard
    # Re-pin to the EXACT resolved snapshot_date frozen at build time --
    # calling universe_as_of() with that snapshot's own date as the as-of
    # target always resolves to exactly that snapshot (it is <= itself and
    # is the latest such), immune to any later-archived snapshot added to
    # the archive since (which would only matter for dates AFTER it).
    try:
        universe = US_BREADTH.universe_as_of(snapshot["snapshot_date"], raw_root)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("US_BREADTH_MEMBERSHIP", exc)
    return component_row(
        "US_BREADTH_MEMBERSHIP",
        "READY",
        None,
        as_of_date=universe["snapshot_date"],
        generated_at=snapshot["downloaded_at"],
        source_packet_path=f"evidence/us_breadth/raw/{universe['snapshot_date']}",
        # True: frozen into packet["frozen_sources"] at build time -- see
        # FROZEN_SOURCE_COMPONENTS.
        validated=True,
        packet={
            "snapshot_date": universe["snapshot_date"],
            "member_count": len(universe["members"]),
            "historical_universe_policy": universe["historical_universe_policy"],
        },
    )


def build_us_breadth_membership(decision_date: str, snapshot: dict | None = None) -> dict:
    raw_root = US_BREADTH.RAW_ROOT
    if snapshot is None:
        snapshot = _fetch_us_breadth_snapshot(decision_date, raw_root)
    return _classify_us_breadth(raw_root, snapshot)


def _fetch_free_market_data_snapshot() -> dict:
    path = ROOT / "data" / "latest_free_market_data.json"
    if not path.exists():
        return {"kind": "missing"}
    try:
        raw = path.read_bytes()
        return {
            "kind": "ready",
            "value": json.loads(raw.decode("utf-8")),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"kind": "error", "value": f"{type(exc).__name__}:{exc}"}


def _classify_free_market_data(snapshot: dict, decision_date: str | None = None) -> dict:
    if snapshot["kind"] == "missing":
        return _blocked("FREE_MARKET_DATA", "UNAVAILABLE", "LATEST_POINTER_MISSING")
    if snapshot["kind"] == "error":
        return component_row("FREE_MARKET_DATA", "DEGRADED", snapshot["value"])
    payload = snapshot["value"]
    authority = payload.get("authority")
    alpaca = payload.get("alpaca", {})
    bars = alpaca.get("bars")
    fred = payload.get("fred", {})
    required_false = (
        "market_wide_price_authorized", "entry_authorized", "action_authorized",
        "order_authorized", "broker_submission_authorized", "production_authorized",
        "trading_authorized",
    )
    schema_version = payload.get("schema_version")
    if (
        schema_version not in {
            "free_market_data_capture/1",
            "free_market_data_capture/2",
            "free_market_data_capture/3",
            "free_market_data_capture/4",
            "free_market_data_capture/5",
        }
        or not isinstance(authority, dict)
        or authority.get("evidence_capture_only") is not True
        or any(authority.get(key) is not False for key in required_false)
        or alpaca.get("feed") != "iex"
        or not isinstance(bars, list)
        or fred.get("series_id") != "VIXCLS"
    ):
        return component_row("FREE_MARKET_DATA", "DEGRADED", "CAPTURE_CONTRACT_INVALID")
    # v3 is the first capture contract published after this freshness gate
    # existed. Enforcing it only for v3 preserves byte-identical rebuilds of
    # already-published v1/v2 briefing packets while preventing a newly
    # generated briefing from presenting a prior KST day's pointer as READY.
    if schema_version in {"free_market_data_capture/3", "free_market_data_capture/4", "free_market_data_capture/5"} and decision_date is not None:
        try:
            expected_date = dt.date.fromisoformat(decision_date)
            observed_at = payload.get("observed_at_utc")
            if not isinstance(observed_at, str):
                raise ValueError("observed_at_utc missing")
            normalized = observed_at[:-1] + "+00:00" if observed_at.endswith("Z") else observed_at
            parsed = dt.datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                raise ValueError("observed_at_utc naive")
            capture_date = parsed.astimezone(KST).date()
        except (TypeError, ValueError):
            return component_row("FREE_MARKET_DATA", "DEGRADED", "CAPTURE_TIME_INVALID")
        if capture_date < expected_date:
            if schema_version != "free_market_data_capture/5" or (
                expected_date - capture_date
            ).days > 4:
                return component_row(
                    "FREE_MARKET_DATA", "DATA_BLOCKED", "CAPTURE_STALE_FOR_DECISION_DATE"
                )
        if capture_date > expected_date:
            return component_row(
                "FREE_MARKET_DATA", "DATA_BLOCKED", "CAPTURE_FUTURE_FOR_DECISION_DATE"
            )
    alpaca_status = alpaca.get("status", "READY")
    if schema_version in {"free_market_data_capture/3", "free_market_data_capture/4", "free_market_data_capture/5"}:
        expected_retention = (
            "TRANSIENT_NOT_PERSISTED"
            if schema_version == "free_market_data_capture/3"
            else "APPEND_ONLY_CONTENT_ADDRESSED"
        )
        if (
            fred.get("status") != "READY"
            or fred.get("raw_retention") != expected_retention
            or not isinstance(fred.get("response_sha256"), str)
            or len(fred["response_sha256"]) != 64
        ):
            return component_row("FREE_MARKET_DATA", "DEGRADED", "FRED_DERIVED_CONTRACT_INVALID")
        if schema_version in {"free_market_data_capture/4", "free_market_data_capture/5"}:
            expected_contract = (
                "free_market_data/2"
                if schema_version == "free_market_data_capture/4"
                else "free_market_data/3"
            )
            if payload.get("contract_version") != expected_contract:
                return component_row(
                    "FREE_MARKET_DATA", "DEGRADED", "CAPTURE_CONTRACT_INVALID"
                )
            unsigned = copy.deepcopy(payload)
            claimed_packet_sha256 = unsigned.pop("packet_sha256", None)
            if claimed_packet_sha256 != payload_sha256(unsigned):
                return component_row("FREE_MARKET_DATA", "DEGRADED", "CAPTURE_PACKET_HASH_INVALID")
            try:
                replay = FRED_VIX_PROVENANCE.validate_evidence(
                    ROOT,
                    fred.get("evidence"),
                    decision_at=payload.get("observed_at_utc"),
                )
            except Exception:
                return component_row(
                    "FREE_MARKET_DATA", "DEGRADED", "FRED_APPEND_ONLY_EVIDENCE_INVALID"
                )
            observation = replay["observation"]
            if (
                fred.get("response_sha256") != replay["pointer"]["raw_response_sha256"]
                or fred.get("series_id") != observation.get("series_id")
                or fred.get("observation_date") != observation.get("observation_date")
                or fred.get("value") != observation.get("value")
                or fred.get("realtime_start") != observation.get("realtime_start")
                or fred.get("realtime_end") != observation.get("realtime_end")
                or payload.get("observed_at_utc") != replay.get("captured_at_utc")
            ):
                return component_row(
                    "FREE_MARKET_DATA", "DEGRADED", "FRED_APPEND_ONLY_REDERIVATION_MISMATCH"
                )
            if schema_version == "free_market_data_capture/5":
                liquidity = payload.get("fred_liquidity")
                if not isinstance(liquidity, dict):
                    return component_row(
                        "FREE_MARKET_DATA", "DEGRADED", "FRED_LIQUIDITY_COMPONENT_INVALID"
                    )
                liquidity_status = liquidity.get("status")
                liquidity_rows = liquidity.get("series")
                if liquidity_status == "READY":
                    if (
                        liquidity.get("derivation_version") != "fred_liquidity_current/v1"
                        or liquidity.get("raw_retention")
                        != "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED"
                        or not isinstance(liquidity_rows, list)
                        or {row.get("series_id") for row in liquidity_rows if isinstance(row, dict)}
                        != {"WRESBAL", "TOTBKCR"}
                        or liquidity.get("derived_payload_sha256")
                        != payload_sha256(liquidity_rows)
                    ):
                        return component_row(
                            "FREE_MARKET_DATA", "DEGRADED", "FRED_LIQUIDITY_DERIVATION_INVALID"
                        )
                elif not (
                    isinstance(liquidity_status, str)
                    and liquidity_status.startswith("FRED_LIQUIDITY_CAPTURE_FAILED:")
                    and liquidity_rows == []
                ):
                    return component_row(
                        "FREE_MARKET_DATA", "DEGRADED", "FRED_LIQUIDITY_COMPONENT_INVALID"
                    )
        if alpaca_status == "READY":
            if not bars or not alpaca.get("daily_bars"):
                return component_row("FREE_MARKET_DATA", "DEGRADED", "ALPACA_READY_EVIDENCE_INCOMPLETE")
            if schema_version == "free_market_data_capture/5":
                try:
                    replay = FREE_MARKET_DATA_CAPTURE.validate_alpaca_daily_evidence(
                        ROOT, payload
                    )
                except Exception:
                    return component_row(
                        "FREE_MARKET_DATA", "DEGRADED", "ALPACA_DAILY_EVIDENCE_INVALID"
                    )
                if decision_date is not None:
                    reference = replay["reference"]
                    try:
                        session_date = dt.date.fromisoformat(
                            reference["as_of_session_date"]
                        )
                        expected_date = dt.date.fromisoformat(decision_date)
                    except (KeyError, TypeError, ValueError):
                        return component_row(
                            "FREE_MARKET_DATA", "DEGRADED", "US_MARKET_SESSION_DATE_INVALID"
                        )
                    if session_date > expected_date:
                        return component_row(
                            "FREE_MARKET_DATA", "DATA_BLOCKED", "US_MARKET_SESSION_FROM_FUTURE"
                        )
                    if (expected_date - session_date).days > 4:
                        return component_row(
                            "FREE_MARKET_DATA", "DATA_BLOCKED", "US_MARKET_SESSION_STALE"
                        )
        elif not (
            isinstance(alpaca_status, str)
            and (
                alpaca_status.startswith("BLOCKED_BY_")
                or alpaca_status.startswith("ALPACA_CAPTURE_FAILED:")
            )
            and not bars
            and not alpaca.get("daily_bars")
            and alpaca.get("raw_sha256") is None
            and alpaca.get("daily_raw_sha256") is None
        ):
            return component_row("FREE_MARKET_DATA", "DEGRADED", "ALPACA_COMPONENT_CONTRACT_INVALID")
    elif not bars:
        return component_row("FREE_MARKET_DATA", "DEGRADED", "LEGACY_ALPACA_BARS_MISSING")
    component_status = "READY" if alpaca_status == "READY" else "DEGRADED"
    component_reason = None if component_status == "READY" else alpaca_status
    return component_row(
        "FREE_MARKET_DATA", component_status, component_reason,
        as_of_date=fred.get("observation_date"),
        generated_at=payload.get("observed_at_utc"),
        available_at=payload.get("observed_at_utc"),
        source_packet_path="data/latest_free_market_data.json",
        # packet_sha256 is the document's self-hash, not the SHA-256 of the
        # file bytes named above. Bind the exact frozen pointer bytes so the
        # immutable briefing core can independently rehash the same object.
        source_packet_sha256=(
            snapshot.get("content_sha256") or payload.get("packet_sha256")
        ),
        validated=True,
        authority=authority,
        contract_version=payload.get("contract_version"),
        packet={
            "vixcls": {"date": fred.get("observation_date"), "value": fred.get("value")},
            **({"fred_evidence": fred.get("evidence")} if schema_version in {"free_market_data_capture/4", "free_market_data_capture/5"} else {}),
            **({
                "fred_liquidity": payload.get("fred_liquidity"),
                "us_market_reference": payload.get("us_market_reference"),
                "alpaca_daily_evidence": {
                    "raw_path": replay["raw_path"],
                    "raw_response_sha256": replay["raw_response_sha256"],
                } if alpaca_status == "READY" else None,
            } if schema_version == "free_market_data_capture/5" else {}),
            "alpaca_iex_bars": bars,
            "alpaca_status": alpaca_status,
            "source_scope": alpaca.get("source_scope"),
            "scope_warning": "IEX_PARTIAL_EVIDENCE_ONLY_NOT_MARKET_WIDE_OR_TRADE_AUTHORITY",
        },
    )


def build_free_market_data(
    snapshot: dict | None = None, decision_date: str | None = None,
) -> dict:
    return _classify_free_market_data(
        _fetch_free_market_data_snapshot() if snapshot is None else snapshot,
        decision_date,
    )


def _classify_btc_trend(snapshot: dict) -> dict:
    if snapshot["kind"] == "absent":
        return _blocked("BTC_TREND", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE")
    guard = _downloaded_at_guard("BTC_TREND", snapshot)
    if guard is not None:
        return guard
    resolved = ROOT / snapshot["resolved_dir"]
    try:
        packet = BTC_TREND.build_transform(resolved)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("BTC_TREND", exc)
    return component_row(
        "BTC_TREND",
        "READY",
        None,
        as_of_date=resolved.name,
        generated_at=snapshot["downloaded_at"],
        source_packet_path=snapshot["resolved_dir"],
        # True: frozen into packet["frozen_sources"] at build time -- see
        # FROZEN_SOURCE_COMPONENTS.
        validated=True,
        authority={k: v for k, v in packet.items() if k.endswith("_authorized")},
        contract_version=packet.get("transform_version"),
        packet={"direction": packet.get("direction"), "dma_200": packet.get("dma_200") if "dma_200" in packet else None},
    )


def build_btc_trend(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "btc" / "raw", decision_date
        )
    return _classify_btc_trend(snapshot)


def _classify_btc_risk(snapshot: dict) -> dict:
    if snapshot["kind"] == "absent":
        return _blocked("BTC_RISK", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE")
    guard = _downloaded_at_guard("BTC_RISK", snapshot)
    if guard is not None:
        return guard
    resolved = ROOT / snapshot["resolved_dir"]
    try:
        packet = BTC_RISK.build_transform(resolved)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("BTC_RISK", exc)
    return component_row(
        "BTC_RISK",
        "READY",
        None,
        as_of_date=resolved.name,
        generated_at=snapshot["downloaded_at"],
        source_packet_path=snapshot["resolved_dir"],
        validated=True,
        contract_version=packet.get("transform_version"),
        packet={
            "status": packet.get("status"),
            "risk_point": packet.get("risk_point"),
        },
    )


def build_btc_risk(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "btc" / "raw", decision_date
        )
    return _classify_btc_risk(snapshot)


def _classify_stablecoin(snapshot: dict) -> dict:
    if snapshot["kind"] == "absent":
        return _blocked(
            "STABLECOIN_NET_ISSUANCE", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE"
        )
    guard = _downloaded_at_guard("STABLECOIN_NET_ISSUANCE", snapshot)
    if guard is not None:
        return guard
    resolved = ROOT / snapshot["resolved_dir"]
    try:
        packet = STABLECOIN.build_transform(resolved)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("STABLECOIN_NET_ISSUANCE", exc)
    latest_row = packet["rows"][-1] if packet.get("rows") else {}
    return component_row(
        "STABLECOIN_NET_ISSUANCE",
        "READY",
        None,
        as_of_date=resolved.name,
        generated_at=snapshot["downloaded_at"],
        source_packet_path=snapshot["resolved_dir"],
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


def build_stablecoin(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "stablecoin" / "raw", decision_date
        )
    return _classify_stablecoin(snapshot)


def _crypto_breadth_coverage_diagnostics(packet: dict) -> dict:
    """Surface crypto_breadth.py's own already-computed taxonomy-coverage
    diagnostics (qualified_members()'s ``diagnostics``/``universe`` block),
    unmodified and never re-derived here.  This is presence/ratio reporting
    only -- it does not gate READY/POLICY_BLOCKED, which stays exactly
    ``packet["status"]`` as before. ``coverage_ratio_bps`` reports resolved
    cutoff slots (target minus unresolved-before-cutoff), so it can never
    display 100% while an above-cutoff taxonomy blocker still exists; every other field is
    a direct passthrough. A count that ``qualified_members()`` never reached
    (e.g. ``known_eligible_count_so_far`` outside the TAXONOMY_COVERAGE_
    UNKNOWN branch) stays ``None`` rather than being defaulted or guessed.
    """
    universe = packet.get("universe")
    if not isinstance(universe, dict):
        return {
            "selected_asset_count": None,
            "target_asset_count": None,
            "known_eligible_count_so_far": None,
            "resolved_cutoff_slot_count": None,
            "taxonomy_unknown_before_cutoff_count": None,
            "taxonomy_unknown_before_cutoff_assets": None,
            "coverage_ratio_bps": None,
        }
    target = universe.get("target_asset_count")
    known_so_far = universe.get("known_eligible_count_so_far")
    selected = universe.get("selected_asset_count")
    unknown_before_cutoff = universe.get("taxonomy_unknown_before_cutoff")
    unknown_assets = None
    if isinstance(unknown_before_cutoff, list):
        unknown_assets = sorted(
            item["canonical_asset_id"]
            for item in unknown_before_cutoff
            if isinstance(item, dict) and isinstance(item.get("canonical_asset_id"), str)
        )
    resolved_slots = None
    coverage_ratio_bps = None
    if isinstance(target, int) and target > 0:
        if isinstance(unknown_before_cutoff, list):
            resolved_slots = max(target - len(unknown_before_cutoff), 0)
        elif isinstance(selected, int):
            resolved_slots = min(max(selected, 0), target)
        if resolved_slots is not None:
            coverage_ratio_bps = (resolved_slots * 10000) // target
    return {
        "selected_asset_count": selected,
        "target_asset_count": target,
        "known_eligible_count_so_far": known_so_far,
        "resolved_cutoff_slot_count": resolved_slots,
        "taxonomy_unknown_before_cutoff_count": (
            len(unknown_before_cutoff)
            if isinstance(unknown_before_cutoff, list)
            else None
        ),
        "taxonomy_unknown_before_cutoff_assets": unknown_assets,
        "coverage_ratio_bps": coverage_ratio_bps,
    }


def _classify_crypto_breadth(snapshot: dict) -> dict:
    if snapshot["kind"] == "absent":
        return _blocked("CRYPTO_BREADTH", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE")
    guard = _downloaded_at_guard("CRYPTO_BREADTH", snapshot)
    if guard is not None:
        return guard
    resolved = ROOT / snapshot["resolved_dir"]
    try:
        packet = CRYPTO_BREADTH.build_transform(resolved)
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
        as_of_date=resolved.name,
        generated_at=snapshot["downloaded_at"],
        source_packet_path=snapshot["resolved_dir"],
        validated=True,
        packet={"status": status} | _crypto_breadth_coverage_diagnostics(packet),
    )


def build_crypto_breadth(decision_date: str, snapshot: dict | None = None) -> dict:
    if snapshot is None:
        snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "breadth" / "raw", decision_date
        )
    return _classify_crypto_breadth(snapshot)


def _classify_crypto_leadership(snapshot: dict) -> dict:
    if snapshot["kind"] == "absent":
        return _blocked(
            "CRYPTO_LEADERSHIP", "DATA_BLOCKED", "NO_CAPTURE_FOR_DECISION_DATE"
        )
    guard = _downloaded_at_guard("CRYPTO_LEADERSHIP", snapshot)
    if guard is not None:
        return guard
    resolved = ROOT / snapshot["resolved_dir"]
    try:
        vintage = dt.date.fromisoformat(resolved.name)
    except ValueError:
        return _blocked(
            "CRYPTO_LEADERSHIP", "DATA_BLOCKED", "SOURCE_VINTAGE_DATE_INVALID"
        )
    archive_root = resolved.parent
    end_date = (vintage - dt.timedelta(days=1)).isoformat()
    try:
        packet = CRYPTO_LEADERSHIP.build_transform(
            archive_root, end_date=end_date
        )
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("CRYPTO_LEADERSHIP", exc)
    status = packet.get("status")
    if status == "OBSERVED_UNCLASSIFIED":
        result_status, reason = "READY", None
    else:
        window_reasons = {
            window.get("unknown_reason")
            for window in packet.get("windows", [])
            if window.get("status") != "OBSERVED_UNCLASSIFIED"
        }
        if "INSUFFICIENT_CONTIGUOUS_HISTORY" in window_reasons:
            reason = "DUAL_WINDOW_NATURAL_HISTORY_INCOMPLETE"
        elif "SOURCE_POINT_UNKNOWN" in window_reasons:
            reason = "DUAL_WINDOW_SOURCE_POINT_UNKNOWN"
        else:
            reason = "DUAL_WINDOW_NOT_OBSERVED"
        result_status = "POLICY_BLOCKED"
    return component_row(
        "CRYPTO_LEADERSHIP",
        result_status,
        reason,
        # The axis adapter deliberately interprets this as the capture
        # vintage and subtracts one day to reproduce the Leadership end date.
        as_of_date=resolved.name,
        generated_at=snapshot["downloaded_at"],
        source_packet_path=str(archive_root.relative_to(ROOT)),
        validated=True,
        authority={k: v for k, v in packet.items() if k.endswith("_authorized")},
        contract_version=packet.get("contract_version"),
        packet={"status": status},
    )


def build_crypto_leadership(
    decision_date: str, snapshot: dict | None = None
) -> dict:
    if snapshot is None:
        snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "breadth" / "raw", decision_date
        )
    return _classify_crypto_leadership(snapshot)


# ---------------------------------------------------------------------------
# Regime. Qualified source evidence can define individual axes, but no score,
# threshold, direction, market preference, or action is authorized here.
# ---------------------------------------------------------------------------


def build_regime_outputs(
    generated_at: str, component_rows: dict | None = None,
) -> dict[str, dict]:
    factors = (
        LIVE_AXIS_ADAPTER.build_axis_factors(component_rows, generated_at)
        if component_rows is not None
        else {market: {} for market in REGIME.load_contract()["markets"]}
    )
    outputs = {}
    for market in REGIME.load_contract()["markets"]:
        outputs[market] = REGIME.build_unknown_output(
            market, generated_at, factors.get(market, {})
        )
    return outputs


def build_three_market_header(regime_outputs: dict[str, dict], slot: str, generated_at: str) -> dict:
    try:
        packet = HEADER.build_header(list(regime_outputs.values()), slot, generated_at)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("THREE_MARKET_REGIME_HEADER", exc)
    defined_count = sum(
        output.get("coverage", {}).get("defined_count", 0)
        for output in regime_outputs.values()
    )
    reason = (
        "LIVE_AXIS_EVIDENCE_WIRED_REGIME_SCORING_UNRATIFIED"
        if defined_count
        else "NO_QUALIFIED_LIVE_AXIS_EVIDENCE"
    )
    return component_row(
        "THREE_MARKET_REGIME_HEADER",
        "PENDING",
        reason,
        as_of_date=generated_at[:10],
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


# ---------------------------------------------------------------------------
# Rotation / Discovery.  Rotation remains honestly empty because no ratified
# cross-market rotation policy exists.  Discovery, however, consumes the real
# committed SEC D1 population and only the filing-content bindings whose
# retained bytes independently pass P3-08 verification.  Recording an event
# case is not ranking, promotion, Rule, action, or trading authority.
# ---------------------------------------------------------------------------


def build_rotation_discovery(
    slot: str, generated_at: str, dynamic_report: dict | None = None
) -> dict:
    ledger = LEDGER.empty_ledger()
    try:
        population = EVENT_POPULATION.build_population_inputs(
            repo_root=ROOT, decision_at=generated_at
        )
        wildcard_envelopes = ROTATION_DISCOVERY.load_operational_wildcard_envelopes(
            generated_at, ROOT
        )
        dart_observation_packet = (
            ROTATION_DISCOVERY.load_operational_dart_observation_packet(
                generated_at, ROOT
            )
        )
        packet = ROTATION_DISCOVERY.build_briefing(
            ledger,
            population["records"],
            population["evidence_bindings"],
            slot,
            generated_at,
            dynamic_report=dynamic_report,
            wildcard_envelopes=wildcard_envelopes,
            wildcard_root=ROOT,
            dart_observation_packet=dart_observation_packet,
            dart_root=ROOT,
        )
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("ROTATION_DISCOVERY", exc)
    case_count = packet["discovery"]["case_count"]
    signal_count = packet["signal_observations"]["observation_count"]
    wildcard_count = packet["wildcard_observations"]["observation_count"]
    dart_count = packet["dart_observations"]["observation_count"]
    dart_partial_failure = bool(
        packet["dart_observations"]["source_failed_count"]
        or packet["dart_observations"]["content_failure_count"]
    )
    source_dates = [population["source_as_of_date"]]
    if dart_observation_packet is not None:
        source_dates.append(dart_observation_packet["source_date"])
    return component_row(
        "ROTATION_DISCOVERY",
        "PENDING",
        (
            "DART_OBSERVATIONS_PRESENT_WITH_PARTIAL_FAILURES_ESCALATION_BLOCKED"
            if dart_partial_failure
            else "DART_OBSERVATIONS_PRESENT_ESCALATION_BLOCKED"
            if dart_count
            else "WILDCARD_OBSERVATIONS_PRESENT_NO_IMPORTANCE_OR_PROMOTION_AUTHORITY"
            if wildcard_count
            else "SIGNAL_OBSERVATIONS_PRESENT_NO_IMPORTANCE_OR_PROMOTION_AUTHORITY"
            if signal_count
            else (
                "EVENT_CASES_RECORDED_NO_IMPORTANCE_OR_PROMOTION_AUTHORITY"
                if case_count
                else "NO_CASE_OR_SIGNAL_OBSERVATION_AVAILABLE"
            )
        ),
        as_of_date=(
            max(value for value in source_dates if value)
            if any(source_dates)
            else generated_at[:10]
        ),
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


def build_business_acceleration_status(generated_at: str) -> dict:
    """Expose policy-neutral real acceleration cases as an additive component."""
    try:
        packet = BUSINESS_ACCELERATION_POPULATION.build_population(
            repo_root=ROOT, decision_at=generated_at
        )
        BUSINESS_ACCELERATION_POPULATION.validate_population(packet, repo_root=ROOT)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("BUSINESS_ACCELERATION", exc)
    populated = packet["status"] == BUSINESS_ACCELERATION_POPULATION.STATUS_POPULATED
    reports = packet["source_reports"]
    return component_row(
        "BUSINESS_ACCELERATION",
        "PENDING" if populated else "DATA_BLOCKED",
        (
            "RADAR_CASE_RECORDED_IMPORTANCE_AND_RANKING_UNRATIFIED"
            if packet["summary"]["case_count"]
            else (
                "RADAR_SERIES_POPULATED_NO_TWO_STEP_CASE"
                if populated
                else packet["status"]
            )
        ),
        as_of_date=(reports[-1]["published_at"] if reports else generated_at[:10]),
        generated_at=generated_at,
        source_packet_sha256=packet["population_sha256"],
        validated=True,
        authority=packet["authority"],
        contract_version=BUSINESS_ACCELERATION_POPULATION.SCHEMA_VERSION,
        packet=packet,
    )


def build_official_release_summary_status(generated_at: str) -> dict:
    """Expose exact retained release facts without interpreting or ranking them."""
    try:
        packet = OFFICIAL_RELEASE_SUMMARY.build_packet(
            data_root=ROOT / "data", decision_at=generated_at
        )
        OFFICIAL_RELEASE_SUMMARY.validate_packet(packet, data_root=ROOT / "data")
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("OFFICIAL_RELEASE_SUMMARY", exc)
    return component_row(
        "OFFICIAL_RELEASE_SUMMARY",
        "PENDING",
        "OFFICIAL_FACTS_OBSERVED_INTERPRETATION_AND_RANKING_UNRATIFIED",
        as_of_date=packet["evidence_as_of"][:10],
        generated_at=packet["evidence_as_of"],
        available_at=packet["evidence_as_of"],
        source_packet_sha256=packet["packet_sha256"],
        validated=True,
        authority=packet["authority"],
        contract_version=packet["schema_version"],
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


def build_action_boundary(generated_at: str, dynamic_report: dict | None = None) -> dict:
    contract = ACTION_BOUNDARY.load_contract()
    try:
        if dynamic_report is None:
            value = {
                "schema_version": contract["input_schema_version"],
                "contract_version": contract["contract_version"],
                "packet_id": f"daily-orchestrator-{generated_at}",
                "as_of_utc": generated_at,
                "subjects": [],
                "authority": copy.deepcopy(contract["input_authority"]),
            }
            value["packet_sha256"] = payload_sha256(value)
        else:
            signal_observation, value = DYNAMIC_CLOCK_SIGNAL.build_boundary_input(
                dynamic_report,
                generated_at,
                boundary_contract=contract,
            )
            # The downstream input hash binds every subject and each signal
            # source ref, which itself embeds this adapter packet's source
            # report hash.  No extra unvalidated component field is needed.
            if signal_observation["subject_count"] != len(value["subjects"]):
                fail(
                    "DYNAMIC_CLOCK_SIGNAL_SUBJECT_COUNT_MISMATCH",
                    f"adapter={signal_observation['subject_count']}:boundary={len(value['subjects'])}",
                )
        packet = ACTION_BOUNDARY.build_packet(value, contract)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("ACTION_BOUNDARY", exc)
    subject_count = packet["summary"]["subject_count"]
    return component_row(
        "ACTION_BOUNDARY",
        "READY" if subject_count else "PENDING",
        (
            "DYNAMIC_CLOCK_SIGNAL_OBSERVATIONS_BOUND_READY_NOT_EVALUATED_NO_ACTION_AUTHORITY"
            if subject_count
            else "NO_DYNAMIC_CLOCK_SIGNAL_OBSERVATIONS_AVAILABLE"
        ),
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
            unavailable_reasons[name] = [_unavailable_reason(name, row)]
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


def build_investment_decision_review_status(
    rule_row: dict, decision_date: str, slot: str, generated_at: str
) -> dict:
    """Expose the current P8-07 gate without inventing a Thesis or Rule result."""
    rule_packet = rule_row.get("packet") if isinstance(rule_row, dict) else None
    rule_sha = rule_packet.get("packet_sha256") if isinstance(rule_packet, dict) else None
    blockers = [
        "EXTERNALLY_RATIFIED_TSM_RULE_PACKET_NOT_AVAILABLE",
        "TSM_THESIS_PACKET_NOT_AVAILABLE",
    ]
    if rule_packet is None or not rule_row.get("validated"):
        blockers.append("P5_RULE_PACKET_NOT_AVAILABLE")
    else:
        blockers.extend([
            "P5_PASS_FAIL_NOT_AUTHORIZED",
            "P5_DOWNSTREAM_ACTION_NOT_AUTHORIZED",
        ])
    blockers = sorted(set(blockers))
    packet = {
        "schema_version": "investment_decision_briefing_status/1",
        "contract_version": "daily_investment_decision_review/1",
        "decision_date": decision_date,
        "slot": slot,
        "generated_at": generated_at,
        "subject": "TSM",
        "review_outcome": "BLOCKED",
        "blockers": blockers,
        "trade_proposal": None,
        "money_action": "NONE",
        "lineage": {
            "p5_rule_packet_sha256": rule_sha,
            "p8_contract_version": INVESTMENT_REVIEW.load_contract()["contract_version"],
        },
        "authority": {
            "briefing_status_only": True,
            "thesis_generation_authorized": False,
            "rule_pass_fail_authorized": False,
            "proposal_generation_authorized": False,
            "capital_authorized": False,
            "stage_change_authorized": False,
            "order_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["packet_sha256"] = INVESTMENT_REVIEW.payload_sha256(packet)
    return component_row(
        "INVESTMENT_DECISION_REVIEW",
        "POLICY_BLOCKED",
        "P5_OR_THESIS_AUTHORITY_NOT_AVAILABLE",
        as_of_date=decision_date,
        generated_at=generated_at,
        source_packet_sha256=packet["packet_sha256"],
        validated=True,
        authority=packet["authority"],
        contract_version=packet["contract_version"],
        packet=packet,
    )


def build_investment_review_shadow_status(
    review_row: dict, decision_date: str, generated_at: str
) -> dict:
    review_packet = review_row.get("packet") if isinstance(review_row, dict) else None
    packet = {
        "schema_version": "investment_review_shadow_briefing_status/1",
        "contract_version": "daily_investment_review_shadow/1",
        "decision_date": decision_date,
        "generated_at": generated_at,
        "review_outcome": (
            review_packet.get("review_outcome") if isinstance(review_packet, dict) else "BLOCKED"
        ),
        "ledger_record_created": False,
        "reason": "P8_07_REVIEW_NOT_PASS_OR_RATIFIED",
        "capital": {"authorized": False, "amount": 0},
        "action": None,
        "order": None,
        "stage_change": None,
        "lineage": {
            "decision_review_packet_sha256": (
                review_packet.get("packet_sha256") if isinstance(review_packet, dict) else None
            ),
            "p10_contract_version": INVESTMENT_SHADOW.load_contract()["contract_version"],
        },
        "authority": {
            "briefing_status_only": True,
            "shadow_eligibility_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "stage_change_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["packet_sha256"] = INVESTMENT_REVIEW.payload_sha256(packet)
    return component_row(
        "INVESTMENT_REVIEW_SHADOW",
        "POLICY_BLOCKED",
        "NO_RATIFIED_PASS_REVIEW_TO_RECORD",
        as_of_date=decision_date,
        generated_at=generated_at,
        source_packet_sha256=packet["packet_sha256"],
        validated=True,
        authority=packet["authority"],
        contract_version=packet["contract_version"],
        packet=packet,
    )


def build_forward_alpha_review_status(decision_date: str, slot: str, generated_at: str) -> dict:
    """P8-11 stage 2 -- additive Forward Alpha Review Pilot summary.

    Wires in the pre-built Alpha Review outcomes for the four Pilot subjects
    (TSM / 298040.KS / 267260.KS / 034020.KS) by calling
    `decision/pilot_evidence_intake.py:run_all_pilots()` + `compare_pilots()`
    directly (not by reading a pre-generated file off disk -- this repo's
    committed evidence for the four Pilot subjects is itself already static,
    so `run_all_pilots()` is cheap, deterministic, and byte-identical on
    every call; see that module's own determinism regression).

    This is purely informational review-only summary data: no Rule PASS/FAIL,
    no Stage/Candidate/Ready/Buy promotion, no trade_proposal, and shadow
    capital is always 0 with human_approval_required always true -- exactly
    the same authority posture `decision/alpha_review.py` and
    `shadow/alpha_shadow_ledger.py` already enforce; this function only
    summarizes their output, never grants anything beyond it.

    Fail-closed like every other builder in this file: if the Pilot module
    failed to load, or `run_all_pilots()`/`compare_pilots()` raises for any
    reason (e.g. the real evidence files this depends on later change shape),
    this returns a DEGRADED/UNAVAILABLE row with the exception recorded as
    the reason -- it never lets an exception propagate and take down the
    rest of the daily briefing packet.
    """
    if PILOT_ALPHA_REVIEW is None:
        return component_row(
            "FORWARD_ALPHA_REVIEW", "UNAVAILABLE", "PILOT_ALPHA_REVIEW_MODULE_LOAD_FAILED",
        )
    try:
        pilot_decision_date = PILOT_ALPHA_REVIEW.PILOT_DECISION_DATE
        pilot_generated_at = PILOT_ALPHA_REVIEW.PILOT_GENERATED_AT
        results = PILOT_ALPHA_REVIEW.run_all_pilots(pilot_decision_date, pilot_generated_at)
        comparison = PILOT_ALPHA_REVIEW.compare_pilots(results)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("FORWARD_ALPHA_REVIEW", exc)

    subjects = {}
    for subject, bundle in results.items():
        alpha = bundle["alpha_review"]
        shadow = bundle["shadow_ledger_entry"]
        subjects[subject] = {
            "opportunity_state": alpha["opportunity_state"],
            "p5_rule_status": alpha["p5_rule_status"],
            "portfolio_status": alpha["portfolio_status"],
            "trade_proposal": alpha["trade_proposal"],
            "next_review_date": alpha["next_review_date"],
            "shadow_action": shadow["shadow_proposal"]["action"],
            "shadow_capital": shadow["shadow_proposal"]["capital"],
            "shadow_human_approval_required": shadow["shadow_proposal"]["human_approval_required"],
            "comparison_label": comparison[subject]["label"],
            "alpha_review_packet_sha256": alpha["packet_sha256"],
        }

    packet = {
        "schema_version": "forward_alpha_review_briefing_status/1",
        "contract_version": "daily_forward_alpha_review/1",
        "decision_date": decision_date,
        "slot": slot,
        "generated_at": generated_at,
        "pilot_evidence_decision_date": pilot_decision_date,
        "pilot_evidence_generated_at": pilot_generated_at,
        "pilot_subjects": subjects,
        "note": (
            "Pilot subjects only (TSM/298040.KS/267260.KS/034020.KS), not "
            "universe-wide. Review-only: no Stage/Candidate/Ready/Buy "
            "promotion, trade_proposal always null, shadow capital always 0, "
            "human_approval_required always true."
        ),
        "authority": {
            "briefing_status_only": True,
            "alpha_review_assembly_only": True,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "portfolio_decision_authorized": False,
            "trade_proposal_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return component_row(
        "FORWARD_ALPHA_REVIEW",
        "READY",
        None,
        as_of_date=pilot_decision_date,
        generated_at=pilot_generated_at,
        source_packet_sha256=packet["packet_sha256"],
        validated=True,
        authority=packet["authority"],
        contract_version=packet["contract_version"],
        packet=packet,
    )


def build_dynamic_clock_status(
    decision_date: str,
    slot: str,
    generated_at: str,
    *,
    report: dict | None = None,
    source_error: Exception | str | None = None,
) -> dict:
    """P8-12 -- Opportunity Trigger + Dynamic Review Clock briefing section.

    Calls `clock/run_dynamic_clock.py:run()` + `build_briefing_section()`
    directly (not by reading a pre-generated file off disk) so this always
    reflects whatever evidence is currently committed -- the same
    "recompute from real evidence, not a cached read" posture as
    `build_forward_alpha_review_status()` above. `run()` makes zero
    provider/network calls of its own (see
    `docs/dynamic_clock_contract.md`); it only reads the market evidence
    other collectors already committed.

    Purely informational: new/immediate-review/watch-review/expired
    triggers and NOT_COMPUTABLE trigger types, per market. No Rule PASS/
    FAIL, no Stage/Candidate/Ready/Buy promotion, no trade_proposal --
    every record's own `authority` block is already hard-`False`/`None`
    (see `clock/review_candidate.py`); this function only summarizes that
    output, never grants anything beyond it.

    Fail-closed like every other builder in this file: any load/exception
    here returns a DEGRADED/UNAVAILABLE row rather than taking down the
    rest of the daily briefing packet.
    """
    if DYNAMIC_CLOCK is None:
        return component_row("DYNAMIC_CLOCK", "UNAVAILABLE", "DYNAMIC_CLOCK_MODULE_LOAD_FAILED")
    if source_error is not None:
        if isinstance(source_error, str):
            return component_row("DYNAMIC_CLOCK", "DEGRADED", source_error)
        return _degraded_from_exception("DYNAMIC_CLOCK", source_error)
    try:
        # ★ CIO review round 2, item 5: pass the briefing's own real
        #   decision_date through so episode staleness is evaluated as of
        #   TODAY, not silently capped at whatever the last evidence
        #   capture date happens to be (this is an external date the
        #   caller already computed via a real `date` command -- see
        #   .github/workflows/daily-briefing.yml -- never datetime.now()
        #   inside this module or clock/run_dynamic_clock.py).
        if report is None:
            report = DYNAMIC_CLOCK.run(decision_date=decision_date)
        section = DYNAMIC_CLOCK.build_briefing_section(report)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("DYNAMIC_CLOCK", exc)

    packet = {
        "schema_version": "dynamic_clock_briefing_status/1",
        "contract_version": "daily_dynamic_clock/1",
        "decision_date": decision_date,
        "slot": slot,
        "generated_at": generated_at,
        "report_asof_evidence_date": report["report_asof_evidence_date"],
        # ★ CIO review round 2, item 7: surfaced explicitly so this
        #   component's own READY status is never mistaken for "the
        #   cadence/tiering policy itself is finally ratified".
        "policy_approval_status": report["policy_approval_status"],
        "policy_version": report["policy_version"],
        "markets": section["markets"],
        "note": (
            "Trigger firing is a re-review REQUEST only, never a Buy signal or Action/Order/"
            "trading authority. Only IMMEDIATE_REVIEW-tier subject candidates carry "
            "human_review_required=True; WATCH_REVIEW/OBSERVATION_ONLY are preserved for audit, "
            "not deleted. See evidence/operational/dynamic_clock/dynamic_clock_report.json for the "
            "full raw_trigger_ledger."
        ),
        "authority": {
            "briefing_status_only": True,
            "trigger_detection_assembly_only": True,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "portfolio_decision_authorized": False,
            "trade_proposal_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return component_row(
        "DYNAMIC_CLOCK",
        "READY",
        None,
        as_of_date=report["report_asof_evidence_date"],
        generated_at=generated_at,
        source_packet_sha256=packet["packet_sha256"],
        validated=True,
        authority=packet["authority"],
        contract_version=packet["contract_version"],
        packet=packet,
    )


def _fetch_dynamic_clock_snapshot(decision_date: str) -> dict:
    """Freeze the exact Dynamic Clock report used by this publication.

    Dynamic Clock reads an append-only collection whose input set can still
    grow later on the same decision date.  The report is shared by three
    daily-briefing projections, so freezing it once here preserves their
    common publication-time source identity without copying output rows or
    touching the upstream P8-12 operational artifact.
    """
    if DYNAMIC_CLOCK is None:
        return {"kind": "unavailable"}
    try:
        report = DYNAMIC_CLOCK.run(decision_date=decision_date)
    except Exception as exc:  # noqa: BLE001 - freeze the fail-closed verdict too
        return {"kind": "error", "value": f"{type(exc).__name__}:{exc}"}
    return {
        "kind": "report",
        "report_sha256": payload_sha256(report),
        "report": report,
    }


def _resolve_dynamic_clock_snapshot(
    snapshot: dict, decision_date: str
) -> tuple[dict | None, str | None]:
    """Validate and resolve one frozen Dynamic Clock source snapshot."""
    if not isinstance(snapshot, dict):
        fail("DYNAMIC_CLOCK_SOURCE_INVALID", "snapshot must be object")
    kind = snapshot.get("kind")
    if kind == "unavailable":
        if set(snapshot) != {"kind"}:
            fail("DYNAMIC_CLOCK_SOURCE_INVALID", "unavailable shape")
        return None, None
    if kind == "error":
        if set(snapshot) != {"kind", "value"} or not isinstance(
            snapshot.get("value"), str
        ):
            fail("DYNAMIC_CLOCK_SOURCE_INVALID", "error shape")
        return None, snapshot["value"]
    if kind != "report":
        fail("DYNAMIC_CLOCK_SOURCE_INVALID", f"kind={kind!r}")
    if set(snapshot) != {"kind", "report_sha256", "report"}:
        fail("DYNAMIC_CLOCK_SOURCE_INVALID", "report shape")
    report = snapshot.get("report")
    report_sha256 = snapshot.get("report_sha256")
    if not isinstance(report, dict) or not isinstance(report_sha256, str):
        fail("DYNAMIC_CLOCK_SOURCE_INVALID", "report/hash type")
    if payload_sha256(report) != report_sha256:
        fail("DYNAMIC_CLOCK_SOURCE_SHA256_MISMATCH", "report_sha256")
    if report.get("decision_date") != decision_date:
        fail(
            "DYNAMIC_CLOCK_SOURCE_DECISION_DATE_MISMATCH",
            f"source={report.get('decision_date')!r}:packet={decision_date!r}",
        )
    return report, None


_SHADOW_REVIEW_PACKET_PATH = Path(
    "evidence/operational/dynamic_clock/shadow_entry_review.json"
)
_SHADOW_REVIEW_REPORT_PATH = Path(
    "evidence/operational/dynamic_clock/dynamic_clock_report.json"
)
_SHADOW_REVIEW_IDENTITY_PATH = Path(
    "evidence/operational/dynamic_clock/candidate_identity_observation.json"
)
_SHADOW_REVIEW_CONTRACT_PATH = Path("config/shadow_entry_review_contract.json")
_SHADOW_REVIEW_FORBIDDEN_POST_HOC_KEYS = frozenset({
    "forward_return", "mfe", "mae", "post_hoc", "audit",
})
_SHADOW_REVIEW_VALIDATION_CACHE: dict[str, dict] = {}


def _contains_shadow_review_post_hoc_key(value) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in _SHADOW_REVIEW_FORBIDDEN_POST_HOC_KEYS):
                return True
            if _contains_shadow_review_post_hoc_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_shadow_review_post_hoc_key(item) for item in value)
    return False


def _shadow_review_source_cache_key() -> str:
    """Fingerprint every byte that can affect bridge validation.

    Full bridge validation intentionally walks git provenance for every
    identity row and is expensive. A daily packet build may be repeated many
    times in one process (semantic rebuild tests and append-only publication).
    Caching is safe only when the four direct inputs, both identity authority
    documents, every approval-evidence byte referenced by those documents,
    the exact git HEAD, and the validator callable itself are unchanged.
    """
    paths = [
        ROOT / _SHADOW_REVIEW_PACKET_PATH,
        ROOT / _SHADOW_REVIEW_REPORT_PATH,
        ROOT / _SHADOW_REVIEW_IDENTITY_PATH,
        ROOT / _SHADOW_REVIEW_CONTRACT_PATH,
        ROOT / "config/canonical_security_identity.json",
        ROOT / "config/market_account_scope_map.json",
    ]
    for authority_path in paths[-2:]:
        authority = _read_json(authority_path)
        for value in authority.values():
            if not isinstance(value, list):
                continue
            for row in value:
                if not isinstance(row, dict):
                    continue
                evidence_ref = row.get("approval_evidence_ref")
                if isinstance(evidence_ref, str):
                    paths.append(ROOT / evidence_ref)
    digest = hashlib.sha256()
    for path in sorted(set(paths), key=lambda value: value.as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    digest.update(head.encode("ascii"))
    digest.update(str(id(SHADOW_ENTRY_REVIEW.validate_packet)).encode("ascii"))
    return digest.hexdigest()


def _validated_shadow_review_source() -> dict:
    cache_key = _shadow_review_source_cache_key()
    cached = _SHADOW_REVIEW_VALIDATION_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    shadow_packet = _read_json(ROOT / _SHADOW_REVIEW_PACKET_PATH)
    report = _read_json(ROOT / _SHADOW_REVIEW_REPORT_PATH)
    identity_packet = _read_json(ROOT / _SHADOW_REVIEW_IDENTITY_PATH)
    review_contract = _read_json(ROOT / _SHADOW_REVIEW_CONTRACT_PATH)
    trigger_kind = shadow_packet.get("source", {}).get("trigger_kind")
    validated = SHADOW_ENTRY_REVIEW.validate_packet(
        shadow_packet,
        report,
        identity_packet,
        review_contract,
        trigger_kind=trigger_kind,
    )
    result = {"packet": validated, "trigger_kind": trigger_kind}
    _SHADOW_REVIEW_VALIDATION_CACHE[cache_key] = copy.deepcopy(result)
    return result


def _review_due_status(next_review_at: str, decision_date: str) -> str:
    try:
        review_day = dt.date.fromisoformat(next_review_at)
        decision_day = dt.date.fromisoformat(decision_date)
    except (TypeError, ValueError):
        fail("SHADOW_ENTRY_REVIEW_TIME_INVALID", str(next_review_at))
    if review_day < decision_day:
        return "REVIEW_OVERDUE"
    if review_day == decision_day:
        return "REVIEW_DUE_TODAY"
    return "REVIEW_UPCOMING"


def build_shadow_entry_review_status(
    decision_date: str, slot: str, generated_at: str
) -> dict:
    """Validated, bounded, zero-capital human-review briefing surface.

    This component never turns an unratified policy into an entry proposal.
    It reads the four committed bridge inputs, makes the production bridge
    independently rebuild the complete packet, then retains only the
    explicitly reviewable rows. The non-reviewable population remains
    visible as a count, not a briefing flood. No forward outcome, MFE/MAE or
    post-hoc audit field is admitted.
    """
    component_id = "SHADOW_ENTRY_REVIEW"
    if SHADOW_ENTRY_REVIEW is None:
        return component_row(
            component_id, "UNAVAILABLE", "SHADOW_ENTRY_REVIEW_MODULE_LOAD_FAILED"
        )
    try:
        validation = _validated_shadow_review_source()
        validated = validation["packet"]
        trigger_kind = validation["trigger_kind"]
        if _contains_shadow_review_post_hoc_key(validated):
            fail("SHADOW_ENTRY_REVIEW_POST_HOC_FIELD_FORBIDDEN", component_id)
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception(component_id, exc)

    source_decision_date = validated["decision_date"]
    source_generated_at = validated["operational_evaluation"]["evaluated_at_utc"]
    if source_decision_date != decision_date:
        return component_row(
            component_id,
            "DATA_BLOCKED",
            "SHADOW_ENTRY_REVIEW_DECISION_DATE_MISMATCH",
            as_of_date=source_decision_date,
            generated_at=source_generated_at,
            available_at=source_generated_at,
            source_packet_path=_SHADOW_REVIEW_PACKET_PATH.as_posix(),
            source_packet_sha256=validated["packet_sha256"],
            validated=True,
            authority=validated["authority"],
            contract_version=validated["schema_version"],
        )

    review_items = []
    for row in validated["review_items"]:
        if row["p8_13_review_surface"] != "ZERO_CAPITAL_HUMAN_REVIEW_ITEM":
            continue
        money_boundary = copy.deepcopy(row["money_boundary"])
        if money_boundary.get("capital") != 0 or money_boundary.get("trade_proposal") is not None:
            fail("SHADOW_ENTRY_REVIEW_MONEY_BOUNDARY_INVALID", row["subject"])
        if any(
            money_boundary.get(key) is not False
            for key in (
                "stage_promotion_authority", "buy_authority", "action_authority",
                "order_authority", "production_authority", "trading_authority",
            )
        ):
            fail("SHADOW_ENTRY_REVIEW_AUTHORITY_ESCALATION", row["subject"])
        review_items.append({
            "subject": row["subject"],
            "market": row["market"],
            "canonical_instrument_id": row["canonical_instrument_id"],
            "identity_status": row["identity_status"],
            "trigger_types": copy.deepcopy(row["trigger_types"]),
            "confirmation_count": row["confirmation_count"],
            "decision_at": row["decision_at"],
            "next_review_at": row["next_review_at"],
            "review_due_status": _review_due_status(
                row["next_review_at"], decision_date
            ),
            "price_state": row["price_state"],
            "reflection_status": row["reflection_status"],
            "review_state": row["review_state"],
            "participation_state": row["participation_state"],
            "review_reason": row["review_reason"],
            "p8_13_review_surface": row["p8_13_review_surface"],
            "money_boundary": money_boundary,
        })

    expected_count = validated["summary"]["zero_capital_review_item_count"]
    if len(review_items) != expected_count:
        fail(
            "SHADOW_ENTRY_REVIEW_BOUNDED_COUNT_MISMATCH",
            f"{len(review_items)}!={expected_count}",
        )
    sample_status = {
        "UPSTREAM_WORKFLOW_RUN": "NATURAL_OPERATIONAL_SAMPLE",
        "MANUAL_WORKFLOW_DISPATCH": "MANUAL_DIAGNOSTIC_SAMPLE",
        "LOCAL_REPRODUCTION": "LOCAL_REPRODUCTION_ONLY",
    }.get(trigger_kind)
    if sample_status is None:
        fail("SHADOW_ENTRY_REVIEW_TRIGGER_KIND_INVALID", str(trigger_kind))

    packet = {
        "schema_version": "shadow_entry_review_briefing_status/1",
        "contract_version": "daily_shadow_entry_review/1",
        "decision_date": decision_date,
        "slot": slot,
        "generated_at": generated_at,
        "source_operational_evaluated_at": source_generated_at,
        "sample_status": sample_status,
        "source": {
            "shadow_entry_review_path": _SHADOW_REVIEW_PACKET_PATH.as_posix(),
            "shadow_entry_review_packet_sha256": validated["packet_sha256"],
            "dynamic_clock_report_path": _SHADOW_REVIEW_REPORT_PATH.as_posix(),
            "dynamic_clock_report_sha256": validated["source"]["dynamic_clock_report_sha256"],
            "candidate_identity_path": _SHADOW_REVIEW_IDENTITY_PATH.as_posix(),
            "candidate_identity_packet_sha256": validated["source"]["candidate_identity_packet_sha256"],
            "contract_path": _SHADOW_REVIEW_CONTRACT_PATH.as_posix(),
            "contract_sha256": validated["source"]["contract_sha256"],
            "trigger_kind": trigger_kind,
        },
        "policy_status": copy.deepcopy(validated["policy_status"]),
        "summary": copy.deepcopy(validated["summary"]),
        "review_items": review_items,
        "why_not_executable": [
            "CANDIDATE_VALIDITY_POLICY_UNRATIFIED",
            "ENTRY_POLICY_UNRATIFIED",
            "POSITION_MANAGEMENT_POLICY_UNRATIFIED",
            "POSITION_SIZE_POLICY_UNRATIFIED",
        ],
        "authority": copy.deepcopy(validated["authority"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return component_row(
        component_id,
        "READY",
        None,
        as_of_date=source_decision_date,
        generated_at=source_generated_at,
        available_at=source_generated_at,
        source_packet_path=_SHADOW_REVIEW_PACKET_PATH.as_posix(),
        source_packet_sha256=validated["packet_sha256"],
        validated=True,
        authority=validated["authority"],
        contract_version=packet["contract_version"],
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


def build_capital_flow_posture_reference() -> dict:
    """P2-COM-02's cross-market flow reference, wired as P6-06's P2_FLOW_ENGINE
    source.  It re-reads and re-derives its own real committed evidence
    (`data/latest_paper_regime_reference.json` plus the P2-COM-03 ledger it
    consumes) -- there is nothing frozen/snapshotted to pass in here, unlike
    the raw-archive sources fetched above.  This is a diagnostic reference,
    never a decision: it stays PENDING with `readiness_inventory_only`-style
    authority regardless of what its own status says.
    """
    try:
        packet = CAPITAL_FLOW_ENGINE.build_reference()
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("P2_FLOW_ENGINE", exc)
    return component_row(
        "P2_FLOW_ENGINE",
        "PENDING",
        "FLOW_REFERENCE_IS_DIAGNOSTIC_NOT_A_DEFENSIVE_ACTION_DECISION",
        as_of_date=packet.get("generated_at", "")[:10] or None,
        generated_at=packet.get("generated_at"),
        source_packet_sha256=packet.get("payload_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
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


def build_defensive_action_decision(
    component_rows: dict[str, dict], decision_date: str, generated_at: str
) -> dict:
    contract = DEFENSIVE_ACTION_DECISION.load_contract()
    unsupported = set(contract["unavailable_only_source_slots"])
    source_packets = {}
    unavailable_reasons = {}
    for name in contract["source_order"]:
        if name in unsupported:
            source_packets[name] = None
            unavailable_reasons[name] = [f"{name}_PRODUCTION_CONTRACT_UNAVAILABLE"]
            continue
        row = component_rows[name]
        if row["packet"] is not None and row["validated"]:
            source_packets[name] = row["packet"]
            unavailable_reasons[name] = []
        else:
            source_packets[name] = None
            unavailable_reasons[name] = [_unavailable_reason(name, row)]
    try:
        packet = DEFENSIVE_ACTION_DECISION.build_packet(
            source_packets,
            unavailable_reasons,
            decision_date,
            generated_at,
            contract=contract,
        )
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("DEFENSIVE_ACTION_DECISION", exc)
    available = packet["summary"]["available_source_count"]
    total = packet["summary"]["source_count"]
    return component_row(
        "DEFENSIVE_ACTION_DECISION",
        "PENDING",
        f"{available}/{total}_SOURCES_AVAILABLE_POLICY_NOT_RATIFIED",
        as_of_date=decision_date,
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


def build_strategic_capital_posture(
    component_rows: dict[str, dict], decision_date: str, generated_at: str
) -> dict:
    contract = STRATEGIC_CAPITAL_POSTURE.load_contract()
    name_map = {
        "P6_DEFENSIVE_ACTION": "DEFENSIVE_ACTION_DECISION",
        "P7_CONCENTRATION_GUARD": "CONCENTRATION_GUARD",
        "P7_MARKET_THEME_BUDGET": "MARKET_THEME_BUDGET",
        "P7_CRYPTO_EXPOSURE_LIMIT": "CRYPTO_EXPOSURE_LIMIT",
        "P7_PLANNED_LOSS_BUDGET": "PLANNED_LOSS_BUDGET",
        "P7_CURRENCY_EXPOSURE": "PORTFOLIO_CURRENCY",
    }
    unsupported = set(contract["unavailable_only_source_slots"])
    source_packets = {}
    unavailable_reasons = {}
    for name in contract["source_order"]:
        if name in unsupported:
            source_packets[name] = None
            unavailable_reasons[name] = [f"{name}_PRODUCTION_CONTRACT_UNAVAILABLE"]
            continue
        row = component_rows[name_map[name]]
        if row["packet"] is not None and row["validated"]:
            source_packets[name] = row["packet"]
            unavailable_reasons[name] = []
        else:
            source_packets[name] = None
            unavailable_reasons[name] = [_unavailable_reason(name, row)]
    try:
        packet = STRATEGIC_CAPITAL_POSTURE.build_packet(
            source_packets,
            unavailable_reasons,
            decision_date,
            generated_at,
            contract=contract,
        )
    except Exception as exc:  # noqa: BLE001
        return _degraded_from_exception("STRATEGIC_CAPITAL_POSTURE", exc)
    available = packet["summary"]["available_source_count"]
    total = packet["summary"]["source_count"]
    return component_row(
        "STRATEGIC_CAPITAL_POSTURE",
        "PENDING",
        f"{available}/{total}_SOURCES_AVAILABLE_POLICY_NOT_RATIFIED",
        as_of_date=decision_date,
        generated_at=generated_at,
        source_packet_sha256=packet.get("packet_sha256"),
        validated=True,
        authority=packet.get("authority"),
        contract_version=packet.get("contract_version"),
        packet=packet,
    )


def build_action_risk_summary(component_rows: dict[str, dict], generated_at: str) -> dict:
    contract = ACTION_SUMMARY.load_contract()
    name_map = {
        "UNIFIED_DECISION": "UNIFIED_DECISION",
        "DEFENSIVE_ACTION_DECISION": "DEFENSIVE_ACTION_DECISION",
        "STRATEGIC_CAPITAL_POSTURE": "STRATEGIC_CAPITAL_POSTURE",
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


# STEP0_READ_MODEL_HEALTH / KRX_PREOPEN_COMPACT / DART_FILING_CONTENT /
# SEC_FILING_CONTENT read data/briefing_status.json's inputs and
# data/latest_{dart,sec}_content.json -- *mutable rolling pointer* files
# that collect.yml overwrites every collection cycle, with no per-date
# archive behind them. Two prior designs both failed here: (1) blindly
# trusting the persisted row (frozen_rows) let semantic tamper of exactly
# these four rows slip past undetected; (2) always re-fetching the live
# pointer meant an honest, untampered packet could legitimately fail
# revalidation days (or minutes) later purely because the pointer moved on
# -- neither tampered nor stale, just no longer re-derivable from live
# state.
#
# The actual fix: freeze the *input* snapshot these four are built from
# (packet["frozen_sources"], populated by build_packet() every time,
# whether building fresh or replaying) rather than either the live pointer
# or the output row. validate_packet() re-derives these four rows purely
# from that frozen input -- a real, independent computation, not a
# blind-trust shortcut -- so a semantic tamper of the row is still caught
# (it now disagrees with a fresh re-derivation from the frozen input,
# embedded in the very same packet), while an untampered packet remains
# independently verifiable forever, with no live data/ access and no
# dependency on today's rolling pointer state. This is why these four are
# now validated=True like every other component, and why there is no
# "cannot be independently revalidated" boundary listed any more.
# KOFIA_FIRST_SEEN/US_BREADTH_MEMBERSHIP/BTC_TREND/BTC_RISK/
# STABLECOIN_NET_ISSUANCE/CRYPTO_BREADTH read a genuinely immutable,
# append-only, per-date evidence archive -- unlike the three above, their
# *content*, once present, never changes. Their staleness risk is
# different: a directory that did not exist yet at build time (correctly
# DATA_BLOCKED) can be created later the same day, and re-deriving an old
# revision after that would wrongly promote it to READY -- not because
# the immutable content changed, but because presence/absence itself is
# not retroactively knowable without recording it. Freezing just that
# presence/absence fact (plus, once present, the resolved directory name)
# is therefore sufficient here -- no digest of the immutable bytes is
# needed, unlike the three above.
# DYNAMIC_CLOCK is another input-set boundary: even though its evidence is
# append-only, a second BTC capture can land later on the same decision date.
# Freeze the one shared report plus its digest so every downstream projection
# replays the publication-time input identity instead of rescanning.
FROZEN_SOURCE_COMPONENTS = frozenset({
    "STEP0_READ_MODEL_HEALTH", "DART_FILING_CONTENT", "SEC_FILING_CONTENT",
    "KOFIA_FIRST_SEEN", "US_BREADTH_MEMBERSHIP", "BTC_TREND", "BTC_RISK",
    "STABLECOIN_NET_ISSUANCE", "CRYPTO_BREADTH", "CRYPTO_LEADERSHIP",
    "KRX_POST_CLOSE", "FREE_MARKET_DATA", "KOREA_ROTATION",
    "KOREA_MARKET_SIGNALS", "DYNAMIC_CLOCK",
})
# KRX_PREOPEN_COMPACT is not fetched separately -- it is derived purely
# from STEP0_READ_MODEL_HEALTH's own frozen input, so freezing that one
# snapshot covers both. BTC_TREND and BTC_RISK read the same evidence
# directory but are frozen (and re-derived) independently -- one extra,
# cheap snapshot rather than a special-cased shared one. KRX_POST_CLOSE
# (evening only) is the same presence/absence-plus-real-observation-time
# pattern as the six above, applied to
# data/observations/krx_post_close/{decision_date}/.


def build_packet(
    slot: str,
    decision_date: str,
    generated_at: str,
    contract: dict | None = None,
    frozen_sources: dict[str, dict] | None = None,
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
    frozen_sources = frozen_sources or {}
    if not set(frozen_sources) <= FROZEN_SOURCE_COMPONENTS:
        fail(
            "FROZEN_SOURCES_INVALID",
            str(set(frozen_sources) - FROZEN_SOURCE_COMPONENTS),
        )

    rows: dict[str, dict] = {}

    # Common fail-closed time boundary (_enforce_temporal_boundary), applied
    # to each row IMMEDIATELY as it is built -- never after a downstream
    # aggregator (UNIFIED_DECISION, ACTION_RISK_PORTFOLIO_SUMMARY) has
    # already consumed it. A row that violates the boundary is downgraded
    # to DATA_BLOCKED before it is ever placed where an aggregator could
    # read it, so future-dated or not-yet-available evidence can never
    # leak into an aggregate even transiently within a single build.
    def _boundary(row: dict) -> dict:
        return _enforce_temporal_boundary(row, decision_date, generated_at_dt)

    step0_snapshot = frozen_sources.get("STEP0_READ_MODEL_HEALTH")
    if step0_snapshot is None:
        step0_snapshot = _fetch_step0_snapshot(decision_date)
    step0 = _boundary(_classify_step0(decision_date, step0_snapshot))
    rows["STEP0_READ_MODEL_HEALTH"] = step0
    rows["KRX_PREOPEN_COMPACT"] = _boundary(
        build_krx_preopen_compact(
            decision_date, step0["packet"], step0_snapshot.get("collected_at_utc_raw")
        )
    )

    krx_post_close_snapshot = None
    if "KRX_POST_CLOSE" in contract["evening_only_components"] and slot == "evening":
        krx_post_close_snapshot = frozen_sources.get("KRX_POST_CLOSE")
        if krx_post_close_snapshot is None:
            krx_post_close_snapshot = _fetch_krx_post_close_snapshot(decision_date)
        rows["KRX_POST_CLOSE"] = _boundary(
            _classify_krx_post_close(decision_date, generated_at_dt, krx_post_close_snapshot)
        )
    else:
        morning_reason = "MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY"
        if (
            slot == "morning"
            and dt.date.fromisoformat(decision_date).weekday() >= 5
        ):
            morning_reason = (
                "WEEKEND_MORNING_MARKET_CLOSED_NO_NEW_SESSION_"
                "LATEST_CONFIRMED_EVIDENCE"
            )
        rows["KRX_POST_CLOSE"] = _blocked(
            "KRX_POST_CLOSE", "PENDING", morning_reason
        )

    dart_snapshot = frozen_sources.get("DART_FILING_CONTENT")
    if dart_snapshot is None:
        dart_snapshot = _fetch_filing_snapshot("data/latest_dart_content.json")
    rows["DART_FILING_CONTENT"] = _boundary(_classify_filing_content(
        "DART_FILING_CONTENT", "data/latest_dart_content.json", decision_date, dart_snapshot
    ))
    sec_snapshot = frozen_sources.get("SEC_FILING_CONTENT")
    if sec_snapshot is None:
        sec_snapshot = _fetch_filing_snapshot("data/latest_sec_content.json")
    rows["SEC_FILING_CONTENT"] = _boundary(_classify_filing_content(
        "SEC_FILING_CONTENT", "data/latest_sec_content.json", decision_date, sec_snapshot
    ))
    kofia_snapshot = frozen_sources.get("KOFIA_FIRST_SEEN")
    if kofia_snapshot is None:
        kofia_snapshot = _fetch_kofia_snapshot(decision_date)
    rows["KOFIA_FIRST_SEEN"] = _boundary(_classify_kofia(kofia_snapshot))

    us_breadth_raw_root = US_BREADTH.RAW_ROOT
    us_breadth_snapshot = frozen_sources.get("US_BREADTH_MEMBERSHIP")
    if us_breadth_snapshot is None:
        us_breadth_snapshot = _fetch_us_breadth_snapshot(decision_date, us_breadth_raw_root)
    rows["US_BREADTH_MEMBERSHIP"] = _boundary(
        _classify_us_breadth(us_breadth_raw_root, us_breadth_snapshot)
    )

    free_market_snapshot = frozen_sources.get("FREE_MARKET_DATA")
    if free_market_snapshot is None:
        free_market_snapshot = _fetch_free_market_data_snapshot()
    rows["FREE_MARKET_DATA"] = _boundary(
        _classify_free_market_data(free_market_snapshot, decision_date)
    )

    btc_snapshot = frozen_sources.get("BTC_TREND")
    if btc_snapshot is None:
        btc_snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "btc" / "raw", decision_date
        )
    rows["BTC_TREND"] = _boundary(_classify_btc_trend(btc_snapshot))

    btc_risk_snapshot = frozen_sources.get("BTC_RISK")
    if btc_risk_snapshot is None:
        btc_risk_snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "btc" / "raw", decision_date
        )
    rows["BTC_RISK"] = _boundary(_classify_btc_risk(btc_risk_snapshot))

    stablecoin_snapshot = frozen_sources.get("STABLECOIN_NET_ISSUANCE")
    if stablecoin_snapshot is None:
        stablecoin_snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "stablecoin" / "raw", decision_date
        )
    rows["STABLECOIN_NET_ISSUANCE"] = _boundary(_classify_stablecoin(stablecoin_snapshot))

    crypto_breadth_snapshot = frozen_sources.get("CRYPTO_BREADTH")
    if crypto_breadth_snapshot is None:
        crypto_breadth_snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "breadth" / "raw", decision_date
        )
    rows["CRYPTO_BREADTH"] = _boundary(_classify_crypto_breadth(crypto_breadth_snapshot))

    # CRYPTO_LEADERSHIP reads the exact same CR-06 archive as CRYPTO_BREADTH
    # (same source_roots entry in regime/crypto_live_component_registry.py)
    # but is frozen independently -- same pattern as BTC_TREND/BTC_RISK
    # sharing evidence/crypto/btc/raw above. build_crypto_leadership() and
    # _classify_crypto_leadership() already existed (P1-CR-08) for
    # regime/crypto_live_component_registry.py's own direct rebuild path;
    # this is the first time either is called from the main daily packet.
    crypto_leadership_snapshot = frozen_sources.get("CRYPTO_LEADERSHIP")
    if crypto_leadership_snapshot is None:
        crypto_leadership_snapshot = _fetch_dated_evidence_snapshot(
            ROOT / "evidence" / "crypto" / "breadth" / "raw", decision_date
        )
    rows["CRYPTO_LEADERSHIP"] = _boundary(
        _classify_crypto_leadership(crypto_leadership_snapshot)
    )

    korea_market_signals_snapshot = frozen_sources.get("KOREA_MARKET_SIGNALS")
    if korea_market_signals_snapshot is None:
        korea_market_signals_snapshot = _fetch_korea_market_signals_snapshot()
    rows["KOREA_MARKET_SIGNALS"] = _boundary(
        _classify_korea_market_signals(decision_date, korea_market_signals_snapshot)
    )

    # Dynamic Clock is computed once and shared by P8-05 presentation,
    # P8-03 signal boundary, and the Dynamic Clock component.  Its exact
    # report and hash are frozen at the daily-briefing producer boundary:
    # a later same-decision-date BTC capture may legitimately produce a new
    # publication revision, but must never alter validation of this one.
    dynamic_clock_snapshot = frozen_sources.get("DYNAMIC_CLOCK")
    if dynamic_clock_snapshot is None:
        dynamic_clock_snapshot = _fetch_dynamic_clock_snapshot(decision_date)
    dynamic_report, dynamic_report_error = _resolve_dynamic_clock_snapshot(
        dynamic_clock_snapshot, decision_date
    )

    regime_outputs = build_regime_outputs(generated_at, rows)
    rows["THREE_MARKET_REGIME_HEADER"] = _boundary(build_three_market_header(
        regime_outputs, slot, generated_at
    ))
    rows["ROTATION_DISCOVERY"] = _boundary(
        build_rotation_discovery(slot, generated_at, dynamic_report)
    )
    rows["BUSINESS_ACCELERATION"] = _boundary(
        build_business_acceleration_status(generated_at)
    )
    rows["OFFICIAL_RELEASE_SUMMARY"] = _boundary(
        build_official_release_summary_status(generated_at)
    )
    korea_rotation_snapshot = frozen_sources.get("KOREA_ROTATION")
    if korea_rotation_snapshot is None:
        korea_rotation_snapshot = _fetch_korea_rotation_snapshot()
    rows["KOREA_ROTATION"] = _boundary(
        _classify_korea_rotation(decision_date, korea_rotation_snapshot)
    )
    rows["RULE_EVALUATION"] = _boundary(build_rule_evaluation())
    rows["PORTFOLIO_BUCKET"] = _blocked(
        "PORTFOLIO_BUCKET", "POLICY_BLOCKED", "CONSTITUTION_NOT_RATIFIED"
    )
    rows["PORTFOLIO_CURRENCY"] = _blocked(
        "PORTFOLIO_CURRENCY", "UNAVAILABLE", "NO_LIVE_ASSET_MASTER_OR_POSITION_SNAPSHOT"
    )
    rows["ACTION_BOUNDARY"] = _boundary(
        build_action_boundary(generated_at, dynamic_report)
    )
    # UNIFIED_DECISION reads REGIME/ROTATION_DISCOVERY/RULE/PORTFOLIO_*/
    # ACTION_BOUNDARY -- every one of those rows above has already passed
    # through _boundary() by this point, so any future-dated/not-yet-
    # available upstream value has already been downgraded before
    # build_unified_decision() ever sees it.
    rows["UNIFIED_DECISION"] = _boundary(
        build_unified_decision(rows, decision_date, slot, generated_at)
    )
    rows["INVESTMENT_DECISION_REVIEW"] = _boundary(
        build_investment_decision_review_status(
            rows["RULE_EVALUATION"], decision_date, slot, generated_at
        )
    )

    for market, key in (("US", "US"), ("KR", "KOREA"), ("CRYPTO", "CRYPTO")):
        cash_row, inverse_row = build_regime_invariant_pair(key, regime_outputs[market])
        rows[f"CASH_EXPOSURE_{key}"] = _boundary(cash_row)
        rows[f"INVERSE_{key}"] = _boundary(inverse_row)

    rows["LONG_SHORT_INVARIANT"] = _boundary(
        build_long_short_invariant(rows["RULE_EVALUATION"]["packet"])
    )
    for name, reason in _POLICY_BLOCKED_ACTION_SOURCES.items():
        rows[name] = _blocked(name, "POLICY_BLOCKED", reason)
    rows["P2_FLOW_ENGINE"] = _boundary(build_capital_flow_posture_reference())

    rows["DEFENSIVE_ACTION_DECISION"] = _boundary(
        build_defensive_action_decision(rows, decision_date, generated_at)
    )
    rows["STRATEGIC_CAPITAL_POSTURE"] = _boundary(
        build_strategic_capital_posture(rows, decision_date, generated_at)
    )

    # ACTION_RISK_PORTFOLIO_SUMMARY reads the two fail-closed P6/P7 readiness
    # packets plus UNIFIED_DECISION/CASH_EXPOSURE_*/LONG_SHORT_INVARIANT/
    # INVERSE_* -- all already boundary-checked above.
    rows["ACTION_RISK_PORTFOLIO_SUMMARY"] = _boundary(
        build_action_risk_summary(rows, generated_at)
    )
    rows["INVESTMENT_REVIEW_SHADOW"] = _boundary(
        build_investment_review_shadow_status(
            rows["INVESTMENT_DECISION_REVIEW"], decision_date, generated_at
        )
    )
    # P8-11 stage 2 -- additive, informational-only. Does not feed
    # UNIFIED_DECISION (see build_unified_decision()'s own fixed packet_map)
    # or any action/order/Production/trading path.
    rows["FORWARD_ALPHA_REVIEW"] = _boundary(
        build_forward_alpha_review_status(decision_date, slot, generated_at)
    )
    # P8-12 -- additive, informational-only. Does not feed UNIFIED_DECISION
    # or any action/order/Production/trading path.
    rows["DYNAMIC_CLOCK"] = _boundary(
        build_dynamic_clock_status(
            decision_date,
            slot,
            generated_at,
            report=dynamic_report,
            source_error=dynamic_report_error,
        )
    )
    # P5-06 -> P7-08 -> P8-13 review-only bridge. Additive and strictly
    # downstream of Dynamic Clock; never consumed by UNIFIED_DECISION or an
    # action/order/capital path.
    rows["SHADOW_ENTRY_REVIEW"] = _boundary(
        build_shadow_entry_review_status(decision_date, slot, generated_at)
    )

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
        # The exact input snapshots every FROZEN_SOURCE_COMPONENTS row was
        # built from. Part of the hashed packet like everything else, so it
        # is tamper-protected the same way; validate_packet() feeds it
        # straight back into build_packet() to independently re-derive
        # those rows without ever touching live, mutable state, and
        # without letting evidence that did not exist yet at build time
        # (an archive directory created later the same day) silently
        # promote an old DATA_BLOCKED revision to READY on re-validation.
        "frozen_sources": {
            "STEP0_READ_MODEL_HEALTH": step0_snapshot,
            "DART_FILING_CONTENT": dart_snapshot,
            "SEC_FILING_CONTENT": sec_snapshot,
            "KOFIA_FIRST_SEEN": kofia_snapshot,
            "US_BREADTH_MEMBERSHIP": us_breadth_snapshot,
            "FREE_MARKET_DATA": free_market_snapshot,
            "BTC_TREND": btc_snapshot,
            "BTC_RISK": btc_risk_snapshot,
            "STABLECOIN_NET_ISSUANCE": stablecoin_snapshot,
            "CRYPTO_BREADTH": crypto_breadth_snapshot,
            "CRYPTO_LEADERSHIP": crypto_leadership_snapshot,
            "KOREA_MARKET_SIGNALS": korea_market_signals_snapshot,
            "KOREA_ROTATION": korea_rotation_snapshot,
            "DYNAMIC_CLOCK": dynamic_clock_snapshot,
            # Only present for the evening slot, where KRX_POST_CLOSE is
            # actually fetched -- the morning slot's static PENDING row has
            # no snapshot to freeze.
            **(
                {"KRX_POST_CLOSE": krx_post_close_snapshot}
                if krx_post_close_snapshot is not None
                else {}
            ),
        },
        "unresolved_boundaries": [
            "REGIME_POLICY_VALUES_UNRATIFIED",
            "ROTATION_AND_DISCOVERY_POLICY_UNRATIFIED",
            "RULE_REGISTRY_NOT_CONSUMABLE",
            "PORTFOLIO_CONSTITUTION_NOT_RATIFIED",
            "ACTION_AND_ORDER_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
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
    # Unconditional full rebuild-and-compare, with no blind-trust exemption
    # for any component. Every FROZEN_SOURCE_COMPONENTS row (STEP0/DART/
    # SEC's mutable rolling pointer; KOFIA/US_BREADTH/BTC_TREND/BTC_RISK/
    # STABLECOIN/CRYPTO_BREADTH's genuinely immutable but presence-may-
    # arrive-later evidence archives; DYNAMIC_CLOCK's same-date-growing
    # report input set) is rebuilt from packet[
    # "frozen_sources"] -- the exact input snapshot persisted inside this
    # very packet at build time -- rather than live, current-moment state,
    # which may have moved on (mutable pointer) or newly appeared (an
    # archive directory created later the same day) since. Every other
    # component is rebuilt from its own real, per-date-pinned evidence on
    # disk directly, unaffected by anything that has happened since. Both
    # paths are genuine, independent re-derivations, not a blind acceptance
    # of the persisted row: a semantic tamper of any row still fails here,
    # because the rebuild never reads the tampered row itself as an input
    # to reproduce -- it re-derives from source data (real disk evidence,
    # or this packet's own frozen_sources) that the tamper never touched.
    frozen_sources = packet.get("frozen_sources") or {}
    if "DYNAMIC_CLOCK" not in frozen_sources:
        # Legacy packets did not persist enough input identity to distinguish
        # their publication-time report from a later same-date capture.  Do
        # not silently re-read today's larger input set and return a verdict
        # that depends on validation time.
        fail("DYNAMIC_CLOCK_SOURCE_NOT_FROZEN", "frozen_sources.DYNAMIC_CLOCK")
    rebuilt = build_packet(
        packet["slot"], packet["decision_date"], packet["generated_at"], contract,
        frozen_sources=frozen_sources,
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
        "US_BREADTH_MEMBERSHIP", "FREE_MARKET_DATA", "BTC_TREND", "BTC_RISK",
        "STABLECOIN_NET_ISSUANCE", "CRYPTO_BREADTH", "CRYPTO_LEADERSHIP",
        "KOREA_MARKET_SIGNALS",
    ]),
    ("3-Market Regime", ["THREE_MARKET_REGIME_HEADER"]),
    ("Rotation / Theme", ["ROTATION_DISCOVERY", "KOREA_ROTATION"]),
    ("New Discovery / candidate change", [
        "ROTATION_DISCOVERY", "BUSINESS_ACCELERATION", "OFFICIAL_RELEASE_SUMMARY",
    ]),
    ("Rule status", ["RULE_EVALUATION"]),
    ("Portfolio / Risk", [
        "PORTFOLIO_BUCKET", "PORTFOLIO_CURRENCY", "CASH_EXPOSURE_US",
        "CASH_EXPOSURE_KOREA", "CASH_EXPOSURE_CRYPTO", "INVERSE_US",
        "INVERSE_KOREA", "INVERSE_CRYPTO", "LONG_SHORT_INVARIANT",
        "HEDGE_ELIGIBILITY", "BEAR_HEDGE_BUDGET", "POSITION_SIZING",
        "CONCENTRATION_GUARD", "MARKET_THEME_BUDGET", "CRYPTO_EXPOSURE_LIMIT",
        "PLANNED_LOSS_BUDGET", "P2_FLOW_ENGINE", "STRATEGIC_CAPITAL_POSTURE",
    ]),
    ("Decision Review", ["INVESTMENT_DECISION_REVIEW"]),
    ("Decision & action boundary", [
        "ACTION_BOUNDARY", "UNIFIED_DECISION", "DEFENSIVE_ACTION_DECISION",
        "ACTION_RISK_PORTFOLIO_SUMMARY",
    ]),
    ("Shadow learning record", ["INVESTMENT_REVIEW_SHADOW"]),
    ("Forward Alpha Review (Pilot)", ["FORWARD_ALPHA_REVIEW"]),
    ("Dynamic Clock (Opportunity Trigger / Review Queue)", ["DYNAMIC_CLOCK"]),
    ("Zero-capital human review (P5-06 / P7-08 / P8-13)", ["SHADOW_ENTRY_REVIEW"]),
]

_STATUS_MARK = {
    "READY": "OK", "PENDING": "PENDING", "UNKNOWN": "UNKNOWN",
    "DEGRADED": "DEGRADED", "POLICY_BLOCKED": "POLICY_BLOCKED",
    "DATA_BLOCKED": "DATA_BLOCKED", "UNAVAILABLE": "UNAVAILABLE",
}


def _format_component_detail(
    row: dict, decision_date: str | None = None
) -> list[str]:
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
        elif cid == "FREE_MARKET_DATA":
            vix = packet.get("vixcls", {})
            bars = packet.get("alpaca_iex_bars", [])
            if decision_date and row.get("as_of_date") != decision_date:
                # US evidence is never a substitute for the KRX briefing
                # date.  Keep its own date visible, but do not present an
                # older close as if it described the current KST session.
                lines.append(
                    "    - US close values withheld: independent session evidence "
                    f"is dated {row.get('as_of_date') or 'UNKNOWN'}, not {decision_date}"
                )
            else:
                lines.append(
                    f"    - VIXCLS={vix.get('value')} as_of={vix.get('date')}"
                )
                lines.append(
                    "    - Alpaca IEX partial: "
                    + (
                        ", ".join(f"{bar.get('symbol')}={bar.get('close')}" for bar in bars)
                        if bars else f"{packet.get('alpaca_status')}"
                    )
                )
            lines.append(f"    - scope: {packet.get('scope_warning')}")
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
            if packet.get("target_asset_count") is not None:
                lines.append(
                    "    - taxonomy_coverage: "
                    f"known_eligible={packet.get('known_eligible_count_so_far')} "
                    f"resolved_cutoff_slots={packet.get('resolved_cutoff_slot_count')} "
                    f"target={packet.get('target_asset_count')} "
                    f"coverage_ratio_bps={packet.get('coverage_ratio_bps')} "
                    f"unresolved_before_cutoff="
                    f"{packet.get('taxonomy_unknown_before_cutoff_assets')}"
                )
        elif cid == "CRYPTO_LEADERSHIP":
            lines.append(f"    - status={packet.get('status')}")
        elif cid == "KOREA_MARKET_SIGNALS":
            axes = packet.get("axes", {})
            trend = axes.get("TREND", {}).get("measurement", {}).get("benchmarks", {})
            breadth = axes.get("BREADTH", {}).get("measurement", {}).get("combined", {})
            liquidity = axes.get("LIQUIDITY", {}).get("measurement", {}).get("combined", {})
            leaders = (
                axes.get("LEADERSHIP", {})
                .get("measurement", {})
                .get("largest_relative_returns", [])[:3]
            )
            if decision_date and row.get("as_of_date") != decision_date:
                lines.append(
                    "    - 한국 종가 수치 보류: 최신 보존 관측일="
                    f"{row.get('as_of_date') or 'UNKNOWN'}; {decision_date} 종가로 재표기하지 않음"
                )
            else:
                lines.append(
                    f"    - 기준일={packet.get('as_of_date')} "
                    f"코스피={trend.get('KOSPI', {}).get('one_session_return_pct')}% "
                    f"코스닥={trend.get('KOSDAQ', {}).get('one_session_return_pct')}%"
                )
                lines.append(
                    f"    - 상승={breadth.get('advancing_count')} "
                    f"하락={breadth.get('declining_count')} "
                    f"보합={breadth.get('unchanged_count')} "
                    f"거래대금변화={liquidity.get('trading_value_change_pct')}%"
                )
                if leaders:
                    lines.append(
                        "    - 상대강도 상위 관측: "
                        + ", ".join(
                            f"{item.get('market')} {item.get('sector_name')} "
                            f"{item.get('relative_return_vs_benchmark_pct')}%p"
                            for item in leaders
                        )
                        + " (투자순위 아님)"
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
        elif cid == "KOREA_ROTATION":
            lines.append(
                f"    - rotation={packet.get('rotation_status')} "
                f"policy_effective={packet.get('rotation_policy_effective')}"
            )
            lines.append(
                f"    - breadth={packet.get('breadth_status')} "
                f"decision_eligible={packet.get('breadth_decision_eligible')} "
                f"reason={packet.get('breadth_reason')}"
            )
            for market, fact in sorted((packet.get("breadth_markets") or {}).items()):
                lines.append(
                    f"    - breadth[{market}]: as_of_date={fact.get('as_of_date')} "
                    f"available_at={fact.get('available_at')} "
                    f"lineage_sha256={fact.get('lineage_sha256')}"
                )
        elif cid == "ROTATION_DISCOVERY":
            summary = packet.get("summary", {})
            lines.append(
                f"    - rotation_changes={summary.get('rotation_change_count')} "
                f"discovery_cases={summary.get('discovery_case_count')} "
                f"new_candidates={summary.get('new_candidate_count')} "
                f"existing_candidate_changes={summary.get('existing_candidate_change_count')} "
                f"signal_observations={summary.get('signal_observation_count')} "
                f"dart_observations={summary.get('dart_observation_count')} "
                f"ready={summary.get('ready_count')} entry={summary.get('entry_trigger_count')}"
            )
            dart = packet.get("dart_observations", {})
            if (
                dart.get("observation_count")
                or dart.get("source_failed_count")
                or dart.get("content_failure_count")
            ):
                lines.append(
                    f"    - DART observations={dart.get('observation_count')} "
                    f"raw_verified={dart.get('raw_bytes_verified_count')} "
                    f"metadata_only={dart.get('metadata_only_count')} "
                    f"source_failed={dart.get('source_failed_count')} "
                    f"content_failed={dart.get('content_failure_count')} "
                    "event_type=UNRATIFIED importance=UNRATIFIED "
                    "promotion=NOT_AUTHORIZED"
                )
                for observation in dart.get("observations", [])[:10]:
                    lines.append(
                        f"    - DART {observation.get('subject_id')} "
                        f"{observation.get('subject_name')}: "
                        f"{observation.get('filing_title')} "
                        f"evidence={observation.get('evidence_status')} "
                        "action=null"
                    )
                omitted = dart.get("observation_count", 0) - 10
                if omitted > 0:
                    lines.append(f"    - DART +{omitted} additional observations omitted")
            signal = packet.get("signal_observations", {})
            if signal:
                lines.append(
                    f"    - signal_markets={signal.get('market_counts')} "
                    f"tier_diagnostic_only={signal.get('tier_counts_diagnostic_only')} "
                    "promotion=NOT_AUTHORIZED"
                )
            wildcard = packet.get("wildcard_observations", {})
            if wildcard:
                lines.append(
                    f"    - wildcard_observations={wildcard.get('observation_count')} "
                    f"cases={wildcard.get('case_count')} pending={wildcard.get('pending_count')} "
                    "importance=UNRATIFIED promotion=NOT_AUTHORIZED"
                )
        elif cid == "BUSINESS_ACCELERATION":
            summary = packet.get("summary", {})
            lines.append(
                f"    - scope={packet.get('scope')} reports={summary.get('eligible_report_count')} "
                f"series={summary.get('series_count')} cases={summary.get('case_count')}"
            )
            radar = packet.get("radar_packet") or {}
            for result in radar.get("series_results", []):
                lines.append(
                    f"    - {result.get('subject')} {result.get('series_id')}: "
                    f"pattern={result.get('pattern')} values_pct={result.get('values_pct')} "
                    f"candidate_eligible={result.get('candidate_eligible')}"
                )
        elif cid == "OFFICIAL_RELEASE_SUMMARY":
            lines.append(
                f"    - subject={packet.get('subject')} "
                f"observed_releases={packet.get('counts', {}).get('observed_registered_releases')} "
                f"summary_items={packet.get('counts', {}).get('observed_summary_items')} "
                "interpretation=UNDETERMINED ranking=UNRATIFIED"
            )
            for observation in packet.get("observations", []):
                lines.append(
                    f"    - {observation.get('subject')}: "
                    f"{observation.get('release_title')} "
                    f"published_at={observation.get('published_at')}"
                )
                for item in observation.get("summary_items", []):
                    lines.append(
                        f"      - official_summary_{item.get('ordinal')}: "
                        f"{item.get('text')}"
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
        elif cid == "INVESTMENT_DECISION_REVIEW":
            lines.append(
                f"    - subject={packet.get('subject')} "
                f"review={packet.get('review_outcome')} "
                f"trade_proposal={packet.get('trade_proposal')} "
                f"money_action={packet.get('money_action')}"
            )
            for blocker in packet.get("blockers", []):
                lines.append(f"    - blocker={blocker}")
        elif cid == "INVESTMENT_REVIEW_SHADOW":
            lines.append(
                f"    - ledger_record_created={packet.get('ledger_record_created')} "
                f"capital={packet.get('capital')} action={packet.get('action')} "
                f"order={packet.get('order')} stage_change={packet.get('stage_change')}"
            )
        elif cid == "ACTION_RISK_PORTFOLIO_SUMMARY":
            summary = packet.get("summary", {})
            lines.append(
                f"    - available_sources={summary.get('available_source_count')}/"
                f"{summary.get('source_count')} "
                f"evaluated_actions={summary.get('evaluated_action_count')} "
                f"risk_breach_sources={summary.get('risk_breach_source_count')}"
            )
        elif cid == "DEFENSIVE_ACTION_DECISION":
            summary = packet.get("summary", {})
            lines.append(
                f"    - decision_status={packet.get('decision_status')} "
                f"available_sources={summary.get('available_source_count')}/"
                f"{summary.get('source_count')} "
                f"evaluated_decisions={summary.get('evaluated_decision_count')} "
                f"no_action={summary.get('no_action')}"
            )
            lines.append(
                f"    - selected_action={packet.get('selected_action')} "
                f"action_proposal={packet.get('action_proposal')} "
                f"orders={len(packet.get('order_intents', []))}"
            )
        elif cid == "STRATEGIC_CAPITAL_POSTURE":
            summary = packet.get("summary", {})
            lines.append(
                f"    - decision_status={packet.get('decision_status')} "
                f"available_sources={summary.get('available_source_count')}/"
                f"{summary.get('source_count')} "
                f"market_budget={packet.get('market_budget')}"
            )
            lines.append(
                f"    - cash_reserve={packet.get('cash_reserve')} "
                f"hedge_budget={packet.get('hedge_budget')} "
                f"max_gross={packet.get('max_gross_risk')} "
                f"max_net={packet.get('max_net_risk')} "
                f"theme_headroom={packet.get('theme_headroom')}"
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
        elif cid == "FORWARD_ALPHA_REVIEW":
            subjects = packet.get("pilot_subjects", {})
            lines.append(f"    - pilot_subjects={sorted(subjects)}")
            for subject, row in sorted(subjects.items()):
                lines.append(
                    f"    - {subject}: opportunity_state={row.get('opportunity_state')} "
                    f"shadow_action={row.get('shadow_action')} "
                    f"comparison_label={row.get('comparison_label')}"
                )
        elif cid == "DYNAMIC_CLOCK":
            lines.append(f"    - policy_approval_status={packet.get('policy_approval_status')}")
            markets = packet.get("markets", {})
            for market, m in sorted(markets.items()):
                tier_counts = m.get("tier_counts", {})
                lines.append(
                    f"    - {market}: raw_triggers(audit only)={m.get('raw_trigger_count_audit_only')} "
                    f"immediate_review={tier_counts.get('IMMEDIATE_REVIEW')} "
                    f"watch_review={tier_counts.get('WATCH_REVIEW')} "
                    f"observation_only={tier_counts.get('OBSERVATION_ONLY')} "
                    f"expired={len(m.get('expired_triggers', []))} "
                    f"calendar_confidence={m.get('calendar_confidence')} "
                    f"not_computable={m.get('not_computable_trigger_types')}"
                )
                # NOTE: every field rendered per candidate below (subject,
                # tier, trigger_types+confirmation_count, price_state,
                # reflection_status, data_state, threshold_basis,
                # price_as_of, reason, authority, money_action) is the
                # EXACT allowlist the integration spec's section 7
                # requires -- `reason` is always template-derived, never a
                # forward-return/MFE/post-hoc-audit figure (section 8).
                # Both IMMEDIATE_REVIEW and WATCH_REVIEW candidates are
                # rendered -- IMMEDIATE_REVIEW is 0 today (no RATIFIED-basis
                # linkage exists yet), so showing WATCH_REVIEW too is what
                # actually keeps already-moving subjects like BTC/삼성전자/
                # SK하이닉스 visible in the briefing rather than falling
                # through the cracks (section 2's stated purpose).
                # Presentation-only cap (this rendering layer alone, NEVER
                # the underlying data): WATCH_REVIEW can be dozens-large for
                # CRYPTO -- fully enumerating it in the daily markdown would
                # recreate the exact "flood" every prior review round
                # pushed back on, even though it is no longer 99 raw
                # triggers. `build_briefing_section()`'s own JSON output
                # (evidence/operational/dynamic_clock/briefing_section.json)
                # keeps every WATCH_REVIEW candidate in full -- nothing is
                # dropped from the actual data, only from this one rendered
                # view.
                _RENDER_CAP = 15
                for tier_key, tier_label in (("immediate_review", "IMMEDIATE_REVIEW"), ("watch_review", "WATCH_REVIEW")):
                    candidates = m.get(tier_key, [])
                    for c in candidates[:_RENDER_CAP]:
                        lines.append(
                            f"      - {tier_label} {c.get('subject')} "
                            f"trigger_types={c.get('trigger_types')} "
                            f"price_state={c.get('price_state')} "
                            f"reflection_status={c.get('reflection_status')} "
                            f"data_state={c.get('data_state')} "
                            f"threshold_basis={c.get('threshold_basis')} "
                            f"price_as_of={c.get('price_as_of')} "
                            f"next_review_at={c.get('next_review_at')} "
                            f"authority={c.get('authority')} money_action={c.get('money_action')} "
                            f"reason={c.get('reason')}"
                        )
                    if len(candidates) > _RENDER_CAP:
                        lines.append(
                            f"      - ... +{len(candidates) - _RENDER_CAP} more {tier_label} candidates "
                            "(full list: evidence/operational/dynamic_clock/briefing_section.json)"
                        )
        elif cid == "SHADOW_ENTRY_REVIEW":
            summary = packet.get("summary", {})
            lines.append(
                f"    - sample_status={packet.get('sample_status')} "
                f"candidates={summary.get('candidate_count')} "
                f"zero_capital_review_items={summary.get('zero_capital_review_item_count')} "
                f"probe_reviews={summary.get('probe_review_count')}"
            )
            for item in packet.get("review_items", []):
                lines.append(
                    f"    - {item.get('subject')} ({item.get('market')}): "
                    f"review_state={item.get('review_state')} "
                    f"participation={item.get('participation_state')} "
                    f"price_state={item.get('price_state')} "
                    f"review_due={item.get('review_due_status')} "
                    f"next_review_at={item.get('next_review_at')} "
                    f"reason={item.get('review_reason')} capital=0 trade_proposal=null"
                )
            lines.append(
                "    - why_not_executable="
                + ",".join(packet.get("why_not_executable", []))
            )
    except (AttributeError, TypeError, KeyError):
        # A packet shape the renderer does not recognize must never break
        # the whole briefing render -- fall back to no detail line rather
        # than raising, the status/reason line above still stands.
        return []
    return lines


def _market_session_freshness_lines(packet: dict, by_id: dict[str, dict]) -> list[str]:
    """Render a visible three-market board against independent evidence clocks.

    This is a presentation boundary only: it neither infers an exchange
    holiday nor changes a component's status.  A same-date validated source
    can be described as current.  A market whose own close/session evidence is
    not current remains visible as PENDING; it is never omitted because KRX,
    US, or Crypto happened to have a different clock.
    """
    decision_date = packet["decision_date"]

    def source_date(component_id: str) -> str:
        row = by_id.get(component_id) or {}
        return row.get("as_of_date") or "UNKNOWN"

    krx = by_id.get("KOREA_MARKET_SIGNALS") or {}
    krx_fresh = krx.get("status") == "READY" and krx.get("as_of_date") == decision_date
    us = by_id.get("FREE_MARKET_DATA") or {}
    us_fresh = us.get("status") == "READY" and us.get("as_of_date") == decision_date
    crypto_ids = ("BTC_TREND", "BTC_RISK", "STABLECOIN_NET_ISSUANCE")
    crypto_dates = [source_date(component_id) for component_id in crypto_ids]
    crypto_fresh = all(date == decision_date for date in crypto_dates)

    lines = ["## 3-market session board"]

    lines.extend([
        "### KRX · 한국",
        ("- session: FRESH_CLOSE" if krx_fresh else "- session: FRESH_CLOSE_PENDING")
        + f"; evidence_date={source_date('KOREA_MARKET_SIGNALS')}",
        "- latest_completed_close_date: " + source_date("KOREA_MARKET_SIGNALS"),
    ])
    if krx_fresh:
        lines.extend(_format_component_detail(krx, decision_date))
    else:
        lines.append(
            "- KOSPI/KOSDAQ close values: pending a same-date validated close; "
            "older evidence is not relabelled as today."
        )
        lines.append(
            "- verified sector/event summary: pending same-date KRX source evidence."
        )

    lines.extend([
        "### US · 미국",
        ("- session: CURRENT_SESSION_EVIDENCE" if us_fresh else "- session: INDEPENDENT_SESSION_PENDING")
        + f"; evidence_date={source_date('FREE_MARKET_DATA')}",
        "- latest_verified_us_evidence_date: " + source_date("FREE_MARKET_DATA"),
    ])
    if us_fresh:
        lines.extend(_format_component_detail(us, decision_date))
    else:
        lines.append(
            "- US close/sector/event summary: pending independently dated "
            "validated US session evidence; no KRX-date substitution."
        )

    lines.extend([
        "### Crypto · 코인",
        (
            "- session: CONTINUOUS_CURRENT_EVIDENCE"
            if crypto_fresh else "- session: CONTINUOUS_EVIDENCE_PENDING"
        ) + f"; evidence_dates={','.join(crypto_dates)}",
        "- continuous_observation_date: " + (decision_date if crypto_fresh else "PENDING"),
    ])
    if crypto_fresh:
        for component_id in crypto_ids:
            component = by_id.get(component_id) or {}
            if component.get("component_id"):
                lines.extend(_format_component_detail(component, decision_date))
    else:
        lines.append(
            "- Crypto topic/sector/event summary: pending complete continuous source evidence."
        )

    lines.append("")
    return lines


def render_markdown(packet: dict) -> str:
    by_id = {row["component_id"]: row for row in packet["components"]}
    flow_first = FLOW_FIRST_BRIEFING.build_packet(packet)
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
    lines.extend(_market_session_freshness_lines(packet, by_id))
    decision_day = dt.date.fromisoformat(packet["decision_date"])
    if packet["slot"] == "morning" and decision_day.weekday() >= 5:
        step0 = by_id.get("STEP0_READ_MODEL_HEALTH") or {}
        sources = ((step0.get("packet") or {}).get("sources") or {})
        observed_dates = {
            value.get("collected_for_kst_date")
            for value in sources.values()
            if isinstance(value, dict) and isinstance(value.get("collected_for_kst_date"), str)
        }
        latest_confirmed = observed_dates.pop() if len(observed_dates) == 1 else "UNKNOWN"
        lines.extend([
            "## Weekend market session context",
            "- market_session: MARKET_CLOSED",
            "- new_session: NONE",
            f"- latest_confirmed_evidence_date: {latest_confirmed}",
            "- latest_confirmed_evidence_relabelled_as_today: false",
            "",
        ])
    for index, section in enumerate(flow_first["sections"], start=1):
        lines.append(f"## {index}. {section['title']}")
        lines.append(f"- status: {section['status']}")
        lines.append(f"- as_of: {section['as_of_date'] or 'UNKNOWN'}")
        lines.append(
            f"- evidence_grade: {section['evidence_grade']} "
            f"({section['evidence_grade_reason']})"
        )
        if section["unknown_reason"]:
            lines.append(f"- unknown_reason: {section['unknown_reason']}")
        lines.append(
            f"- invalidation: {section['invalidation']['status']} "
            f"({section['invalidation']['reason']})"
        )
        if section["source_components"]:
            lines.append(
                "- sources: "
                + ", ".join(
                    f"{source['component_id']}={source['status']}"
                    for source in section["source_components"]
                )
            )
        if section["section_id"] == "CROSS_MARKET_FLOW":
            evidence = section["cross_asset_flow_evidence"]
            lines.append(
                f"- evidence_class_counts: {evidence['evidence_class_counts']}"
            )
            lines.append(
                f"- evidence_status_counts: {evidence['evidence_status_counts']}"
            )
            lines.append(
                "- comparison_observation_dates: "
                + str(evidence["comparison_observation_dates"])
            )
            lines.append(
                "- flow_direction: UNKNOWN (no cross-market comparison authority)"
            )
        lines.append("")

    lines.append("# Supporting Evidence and System Health")
    lines.append("")
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
            lines.extend(_format_component_detail(row, packet["decision_date"]))
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
    several downstream *synthetic* packets built from it -- REGIME (hence
    also CASH_EXPOSURE_*/INVERSE_*, built from Regime's output),
    THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, ACTION_BOUNDARY,
    UNIFIED_DECISION, DEFENSIVE_ACTION_DECISION, STRATEGIC_CAPITAL_POSTURE,
    ACTION_RISK_PORTFOLIO_SUMMARY -- collectively
    _GENERATED_AT_TAINTED_SELF_HASH_COMPONENTS. It does not merely
    reappear verbatim there -- those packets also embed *hashes computed
    over* their own generated_at-tainted content (packet_sha256,
    source_sha256), which differ unpredictably even after the literal
    timestamp string is removed, since a hash cannot be un-derived.

    _strip_fingerprint_noise() is applied ONLY to that known, fixed set of
    components -- never universally. A blanket "drop every key ending in
    sha256" rule would ALSO drop real signal: STEP0_READ_MODEL_HEALTH /
    KRX_PREOPEN_COMPACT embed real, generated_at-independent per-source
    hashes (sources.krx/dart/sec.source_sha256) that genuinely change when
    the underlying collected file's bytes change even while status/counts
    stay identical -- the same literal key name ("source_sha256") means
    something completely different depending on which component it is
    nested inside, so the strip must be component-scoped, not key-name
    global. Every real-evidence or real-deterministic-computation
    component (STEP0/KRX_PREOPEN/DART/SEC/KOFIA/US_BREADTH/BTC_TREND/
    BTC_RISK/STABLECOIN/CRYPTO_BREADTH/RULE_EVALUATION/LONG_SHORT_
    INVARIANT) is hashed at full fidelity, nested hashes included.
    """
    fingerprint: dict[str, str] = {}
    for row in packet["components"]:
        material = {key: value for key, value in row.items() if key != "generated_at"}
        if row["component_id"] in _GENERATED_AT_TAINTED_SELF_HASH_COMPONENTS:
            material["packet"] = _strip_fingerprint_noise(material.get("packet"))
            # This component's own row-level source_packet_sha256 IS its
            # packet's self-hash (packet.get("packet_sha256")), and that
            # packet's content is itself built from the generated_at
            # argument -- so the hash changes on every call even with
            # identical real content. Real evidence-based components are
            # NOT in this set -- their source_packet_sha256 (when set)
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
    "BUSINESS_ACCELERATION",
    "ACTION_BOUNDARY", "UNIFIED_DECISION", "ACTION_RISK_PORTFOLIO_SUMMARY",
    "DEFENSIVE_ACTION_DECISION", "STRATEGIC_CAPITAL_POSTURE",
    "INVESTMENT_DECISION_REVIEW", "INVESTMENT_REVIEW_SHADOW",
    "CASH_EXPOSURE_US", "CASH_EXPOSURE_KOREA", "CASH_EXPOSURE_CRYPTO",
    "INVERSE_US", "INVERSE_KOREA", "INVERSE_CRYPTO",
    # FORWARD_ALPHA_REVIEW's packet embeds the live decision_date/slot/
    # generated_at directly (see build_forward_alpha_review_status()), so
    # its own source_packet_sha256 is likewise a generated_at-tainted
    # self-hash, not an independent real-evidence signal -- the actual
    # Pilot evidence it summarizes is pinned to
    # decision/pilot_evidence_intake.py's own fixed PILOT_DECISION_DATE/
    # PILOT_GENERATED_AT and does not change per daily-briefing invocation.
    "FORWARD_ALPHA_REVIEW",
    # DYNAMIC_CLOCK's packet embeds the live decision_date/slot/generated_at
    # directly too (see build_dynamic_clock_status()), for the same reason
    # -- the actual Dynamic Clock content it summarizes is pinned to real
    # committed evidence capture dates (report_asof_evidence_date per
    # market), not to this daily-briefing invocation's own generated_at.
    "DYNAMIC_CLOCK",
})


_FINGERPRINT_NOISE_KEYS = frozenset({
    "age_seconds", "as_of_utc", "decision_at", "packet_id", "source_as_of"
})


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
      generated_at-derived (age_seconds, as_of_utc, packet_id, source_as_of).

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
