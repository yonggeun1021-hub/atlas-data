#!/usr/bin/env python3
"""Immutable Flow-First to PAPER decision receipt for KRX, US, and Crypto.

The bridge is a pure, zero-transport consumer.  It never invents a score,
instrument, action, trade plan, or risk value.  Existing candidate funnels are
reduced independently per market.  Missing/null/stale/hash-mismatched evidence
and every non-literal PASS fail closed to ``action=null`` / ``WAIT``.
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
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "paper_decision_bridge_contract.json"
SCHEMA_PATH = ROOT / "schemas" / "paper_decision_bridge.schema.json"
INPUT_SCHEMA_VERSION = "paper_decision_bridge_input/1"
OUTPUT_SCHEMA_VERSION = "paper_decision_bridge_receipt/1"
CONTRACT_VERSION = "paper_decision_bridge_contract/1"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"IMPORT_FAILED:{relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FUNNEL = _load("paper_decision_bridge_funnel", "decision/common_paper_candidate_funnel.py")
REGIME_HEADER = _load("paper_decision_bridge_regime", "briefing/three_market_regime_header.py")


class PaperDecisionBridgeError(ValueError):
    """Fail-closed input, contract, receipt, or persistence error."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperDecisionBridgeError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PaperDecisionBridgeError(f"UTC_INVALID:{label}")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PaperDecisionBridgeError(f"UTC_INVALID:{label}") from exc
    if parsed.tzinfo != dt.timezone.utc:
        raise PaperDecisionBridgeError(f"UTC_INVALID:{label}")
    return parsed


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict):
        raise PaperDecisionBridgeError("CONTRACT_NOT_OBJECT")
    if value.get("schema_version") != 1 or value.get("contract_version") != CONTRACT_VERSION:
        raise PaperDecisionBridgeError("CONTRACT_VERSION_MISMATCH")
    if value.get("markets") != ["KRX", "US", "CRYPTO"]:
        raise PaperDecisionBridgeError("CONTRACT_MARKETS_MISMATCH")
    if value.get("thresholds") != {"candidate": 60, "ready": 70, "paper_buy_eligible": 75}:
        raise PaperDecisionBridgeError("CONTRACT_THRESHOLDS_MISMATCH")
    if value.get("top3_semantics") != "UP_TO_THREE_INSTRUMENTS_WITHIN_ONE_MARKET_NOT_THREE_MARKETS":
        raise PaperDecisionBridgeError("CONTRACT_TOP3_SEMANTICS_INVALID")
    if any(flag is not False for flag in value.get("permanent_false_authority", {}).values()):
        raise PaperDecisionBridgeError("CONTRACT_PERMANENT_AUTHORITY_INVALID")
    return copy.deepcopy(value)


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict) or value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise PaperDecisionBridgeError("SCHEMA_INVALID")
    return copy.deepcopy(value)


def _source(value: object, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"ref", "sha256"}:
        raise PaperDecisionBridgeError(f"SOURCE_FIELDS_INVALID:{label}")
    ref, claimed = value["ref"], value["sha256"]
    if ref is None or claimed is None:
        return {"ref": ref, "sha256": claimed, "verified": False, "reason": "EXACT_SOURCE_OR_HASH_MISSING"}
    if not isinstance(ref, str) or not ref or not isinstance(claimed, str) or SHA_RE.fullmatch(claimed) is None:
        raise PaperDecisionBridgeError(f"SOURCE_VALUE_INVALID:{label}")
    path = Path(ref)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        return {"ref": ref, "sha256": claimed, "verified": False, "reason": "EXACT_SOURCE_FILE_MISSING"}
    actual = file_sha256(path)
    if actual != claimed:
        return {"ref": ref, "sha256": claimed, "verified": False, "reason": f"EXACT_SOURCE_HASH_MISMATCH:{actual}"}
    return {"ref": ref, "sha256": claimed, "verified": True, "reason": "EXACT_SOURCE_HASH_VERIFIED"}


def _gate(value: object, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"status", "reason", "sources"}:
        raise PaperDecisionBridgeError(f"GATE_FIELDS_INVALID:{label}")
    if value["status"] not in ("PASS", "FAIL", None):
        raise PaperDecisionBridgeError(f"GATE_STATUS_INVALID:{label}")
    if not isinstance(value["reason"], str) or not value["reason"]:
        raise PaperDecisionBridgeError(f"GATE_REASON_INVALID:{label}")
    if not isinstance(value["sources"], list):
        raise PaperDecisionBridgeError(f"GATE_SOURCES_INVALID:{label}")
    sources = [_source(item, f"{label}.sources[{index}]") for index, item in enumerate(value["sources"])]
    status = value["status"]
    reason = value["reason"]
    if status == "PASS" and (not sources or not all(item["verified"] for item in sources)):
        status = "FAIL"
        reason = "PASS_SOURCE_HASH_NOT_VERIFIED"
    return {"status": status, "reason": reason, "sources": sources}


def _validate_market_shape(value: object, market: str, contract: dict) -> dict:
    required = {
        "market", "sourceTimestamp", "ttlSeconds", "exactSources", "leadership",
        "observedRanking", "riskPacket", "lifecycleGates", "traceStages", "candidates",
    }
    if not isinstance(value, dict) or set(value) != required or value.get("market") != market:
        raise PaperDecisionBridgeError(f"MARKET_FIELDS_INVALID:{market}")
    if value["sourceTimestamp"] is not None:
        _parse_utc(value["sourceTimestamp"], f"{market}.sourceTimestamp")
    if value["ttlSeconds"] is not None and (
        isinstance(value["ttlSeconds"], bool)
        or not isinstance(value["ttlSeconds"], int)
        or value["ttlSeconds"] <= 0
    ):
        raise PaperDecisionBridgeError(f"TTL_INVALID:{market}")
    if not isinstance(value["exactSources"], list) or not value["exactSources"]:
        raise PaperDecisionBridgeError(f"EXACT_SOURCES_INVALID:{market}")
    exact_sources = [_source(item, f"{market}.exactSources[{index}]") for index, item in enumerate(value["exactSources"])]

    leadership = value["leadership"]
    leadership_fields = {"transformVersion", "approvalStatus", "groupCoverageStatus", "observationStatus", "reason"}
    if not isinstance(leadership, dict) or set(leadership) != leadership_fields:
        raise PaperDecisionBridgeError(f"LEADERSHIP_FIELDS_INVALID:{market}")
    expected_leadership = contract["leadership_policy"][market]
    for key, expected_key in (("transformVersion", "transform_version"), ("approvalStatus", "approval_status"), ("groupCoverageStatus", "group_coverage_status")):
        if leadership[key] != expected_leadership[expected_key]:
            raise PaperDecisionBridgeError(f"LEADERSHIP_POLICY_MISMATCH:{market}:{key}")
    if leadership["observationStatus"] not in ("PASS", "FAIL", None) or not isinstance(leadership["reason"], str) or not leadership["reason"]:
        raise PaperDecisionBridgeError(f"LEADERSHIP_STATUS_INVALID:{market}")

    ranking = value["observedRanking"]
    if not isinstance(ranking, dict) or set(ranking) != {"universe", "top10", "top3"}:
        raise PaperDecisionBridgeError(f"RANKING_FIELDS_INVALID:{market}")
    for key, limit in (("universe", None), ("top10", 10), ("top3", 3)):
        rows = ranking[key]
        if not isinstance(rows, list) or any(not isinstance(item, str) or not item for item in rows) or len(set(rows)) != len(rows):
            raise PaperDecisionBridgeError(f"RANKING_VALUE_INVALID:{market}:{key}")
        if limit is not None and len(rows) > limit:
            raise PaperDecisionBridgeError(f"RANKING_LIMIT_INVALID:{market}:{key}")
    if any(item not in ranking["universe"] for item in ranking["top10"]) or any(item not in ranking["top10"] for item in ranking["top3"]):
        raise PaperDecisionBridgeError(f"RANKING_LINEAGE_INVALID:{market}")

    lifecycle = value["lifecycleGates"]
    if not isinstance(lifecycle, dict) or set(lifecycle) != set(contract["market_lifecycle_gates"]):
        raise PaperDecisionBridgeError(f"LIFECYCLE_GATES_INVALID:{market}")
    lifecycle = {gate_id: _gate(lifecycle[gate_id], f"{market}.lifecycleGates.{gate_id}") for gate_id in contract["market_lifecycle_gates"]}

    trace = value["traceStages"]
    if not isinstance(trace, dict) or set(trace) != set(contract["trace_order"]):
        raise PaperDecisionBridgeError(f"TRACE_STAGES_INVALID:{market}")
    trace = {stage: _gate(trace[stage], f"{market}.traceStages.{stage}") for stage in contract["trace_order"]}

    risk = value["riskPacket"]
    risk_fields = {"status", "cashAction", "exposureAction", "inverseAction", "hedgeAction", "reason", "sources"}
    if not isinstance(risk, dict) or set(risk) != risk_fields or risk["status"] not in ("PASS", "FAIL", None):
        raise PaperDecisionBridgeError(f"RISK_PACKET_INVALID:{market}")
    if not isinstance(risk["reason"], str) or not risk["reason"] or not isinstance(risk["sources"], list):
        raise PaperDecisionBridgeError(f"RISK_PACKET_REASON_INVALID:{market}")
    risk_sources = [_source(item, f"{market}.riskPacket.sources[{index}]") for index, item in enumerate(risk["sources"])]
    risk_status = risk["status"]
    risk_reason = risk["reason"]
    if risk_status == "PASS" and (not risk_sources or not all(item["verified"] for item in risk_sources)):
        risk_status, risk_reason = "FAIL", "RISK_PACKET_SOURCE_HASH_NOT_VERIFIED"

    if not isinstance(value["candidates"], list):
        raise PaperDecisionBridgeError(f"CANDIDATES_INVALID:{market}")
    return {
        **copy.deepcopy(value),
        "exactSources": exact_sources,
        "lifecycleGates": lifecycle,
        "traceStages": trace,
        "riskPacket": {**copy.deepcopy(risk), "status": risk_status, "reason": risk_reason, "sources": risk_sources},
    }


def _validated_trade_plan(candidate: dict, market: str, index: int) -> tuple[dict, dict, list[dict]]:
    fields = {"displayName", "tickerCode", "upstreamAction", "tradePlan", "tradePlanGate", "exactSources", "funnelCandidate"}
    if not isinstance(candidate, dict) or set(candidate) != fields:
        raise PaperDecisionBridgeError(f"CANDIDATE_FIELDS_INVALID:{market}:{index}")
    if not isinstance(candidate["displayName"], str) or not candidate["displayName"] or not isinstance(candidate["tickerCode"], str) or not candidate["tickerCode"]:
        raise PaperDecisionBridgeError(f"CANDIDATE_IDENTITY_INVALID:{market}:{index}")
    if candidate["upstreamAction"] not in ("BUY", "HOLD", "SELL", "WAIT", None):
        raise PaperDecisionBridgeError(f"UPSTREAM_ACTION_INVALID:{market}:{index}")
    plan = candidate["tradePlan"]
    plan_fields = {"entryPrice", "stopPrice", "takeProfitPrice", "quantity", "expiresAt"}
    if not isinstance(plan, dict) or set(plan) != plan_fields:
        raise PaperDecisionBridgeError(f"TRADE_PLAN_FIELDS_INVALID:{market}:{index}")
    for key in ("entryPrice", "stopPrice", "takeProfitPrice", "quantity"):
        if plan[key] is not None and (not isinstance(plan[key], str) or not plan[key]):
            raise PaperDecisionBridgeError(f"TRADE_PLAN_VALUE_INVALID:{market}:{index}:{key}")
    if plan["expiresAt"] is not None:
        _parse_utc(plan["expiresAt"], f"{market}.candidates[{index}].tradePlan.expiresAt")
    gate = _gate(candidate["tradePlanGate"], f"{market}.candidates[{index}].tradePlanGate")
    sources = [_source(item, f"{market}.candidates[{index}].exactSources[{i}]") for i, item in enumerate(candidate["exactSources"])]
    any_plan_value = any(value is not None for value in plan.values())
    if any_plan_value and (gate["status"] != "PASS" or not sources or not all(item["verified"] for item in sources)):
        plan = {key: None for key in plan_fields}
        gate = {"status": "FAIL", "reason": "UNVERIFIED_TRADE_PLAN_VALUES_DROPPED", "sources": gate["sources"]}
    return copy.deepcopy(plan), gate, sources


def _market_regime(regime_header: dict | None, market: str) -> tuple[dict, list[str]]:
    if regime_header is None:
        return {"regime": "UNKNOWN", "direction": "UNKNOWN", "confidence": None, "coverage": None}, ["THREE_MARKET_REGIME_HEADER_MISSING"]
    row = next((item for item in regime_header["markets"] if item["market"] == {"KRX": "KR", "US": "US", "CRYPTO": "CRYPTO"}[market]), None)
    if row is None:
        return {"regime": "UNKNOWN", "direction": "UNKNOWN", "confidence": None, "coverage": None}, ["REGIME_MARKET_ROW_MISSING"]
    reasons = []
    if row["regime"] == "UNKNOWN":
        reasons.append("REGIME_UNKNOWN")
    if row["coverage"]["defined_count"] != row["coverage"]["required_count"]:
        reasons.append("REGIME_COVERAGE_INCOMPLETE")
    return {"regime": row["regime"], "direction": row["direction"], "confidence": row["confidence"], "coverage": copy.deepcopy(row["coverage"])}, reasons


def _trace_rows(market: dict, contract: dict, regime_valid: bool) -> list[dict]:
    rows = []
    previous_connected = True
    for stage in contract["trace_order"]:
        observed = market["traceStages"][stage]
        authority = contract["component_contracts"][stage]
        status = observed["status"]
        reasons = [] if status == "PASS" else [observed["reason"]]
        if stage == "THREE_MARKET_REGIME_HEADER" and not regime_valid:
            status = "FAIL"
            reasons.append("THREE_MARKET_REGIME_HEADER_INVALID_OR_MISSING")
        if not previous_connected:
            reasons.append("UPSTREAM_TRACE_DISCONNECTED")
        input_connected = previous_connected and status == "PASS"
        paper_transition = input_connected and authority["paper_transition_authorized"]
        if input_connected and not authority["paper_transition_authorized"]:
            reasons.append("COMPONENT_HAS_NO_PAPER_TRANSITION_AUTHORITY")
        rows.append({
            "stage": stage,
            "inputConnected": previous_connected,
            "outputStatus": status,
            "contract": authority["contract"],
            "authorityPaperTransition": authority["paper_transition_authorized"],
            "paperTransitioned": paper_transition,
            "sources": copy.deepcopy(observed["sources"]),
            "reasons": sorted(set(reasons)) or ["LITERAL_PASS"],
        })
        previous_connected = input_connected
    return rows


def _reduce_market(market: dict, evaluation_at: str, evidence_class: str, contract: dict, regime_header: dict | None, regime_valid: bool) -> dict:
    market_id = market["market"]
    evaluation_dt = _parse_utc(evaluation_at, "evaluationAt")
    source_dt = (
        _parse_utc(market["sourceTimestamp"], f"{market_id}.sourceTimestamp")
        if market["sourceTimestamp"] is not None else None
    )
    expires_dt = (
        source_dt + dt.timedelta(seconds=market["ttlSeconds"])
        if source_dt is not None and market["ttlSeconds"] is not None else None
    )
    source_fresh = bool(
        source_dt is not None and expires_dt is not None
        and source_dt <= evaluation_dt < expires_dt
    )
    exact_sources_pass = all(item["verified"] for item in market["exactSources"])

    lifecycle = copy.deepcopy(market["lifecycleGates"])
    if source_dt is None or expires_dt is None:
        lifecycle["FRESHNESS"] = {
            "status": "FAIL",
            "reason": "MARKET_SOURCE_TIMESTAMP_OR_TTL_MISSING",
            "sources": lifecycle["FRESHNESS"]["sources"],
        }
    elif not source_fresh:
        lifecycle["FRESHNESS"] = {"status": "FAIL", "reason": "MARKET_SOURCE_TTL_STALE_OR_FUTURE", "sources": lifecycle["FRESHNESS"]["sources"]}
    if not exact_sources_pass:
        lifecycle["FRESHNESS"] = {"status": "FAIL", "reason": "MARKET_EXACT_SOURCE_HASH_UNVERIFIED", "sources": lifecycle["FRESHNESS"]["sources"]}
    leadership = market["leadership"]
    if leadership["approvalStatus"] != "RATIFIED":
        lifecycle["LEADERSHIP_APPROVAL"] = {"status": "FAIL", "reason": "LEADERSHIP_POLICY_UNRATIFIED", "sources": lifecycle["LEADERSHIP_APPROVAL"]["sources"]}
    if leadership["groupCoverageStatus"] == "UNRATIFIED":
        lifecycle["LEADERSHIP_COVERAGE"] = {"status": "FAIL", "reason": "LEADERSHIP_GROUP_COVERAGE_UNRATIFIED", "sources": lifecycle["LEADERSHIP_COVERAGE"]["sources"]}
    if market["riskPacket"]["status"] != "PASS":
        lifecycle["RISK_PACKET"] = {"status": market["riskPacket"]["status"], "reason": market["riskPacket"]["reason"], "sources": market["riskPacket"]["sources"]}

    regime, regime_reasons = _market_regime(regime_header, market_id)
    if regime["regime"] == "UNKNOWN" or regime_reasons:
        lifecycle["MARKET_JUDGEMENT"] = {"status": "FAIL", "reason": ",".join(regime_reasons or ["REGIME_UNKNOWN"]), "sources": lifecycle["MARKET_JUDGEMENT"]["sources"]}

    funnel_candidates = []
    candidate_meta = {}
    for index, candidate in enumerate(market["candidates"]):
        plan, plan_gate, exact_sources = _validated_trade_plan(candidate, market_id, index)
        funnel_candidate = copy.deepcopy(candidate["funnelCandidate"])
        expected_market = contract["funnel_market_map"][market_id]
        if funnel_candidate.get("market") != expected_market:
            raise PaperDecisionBridgeError(f"FUNNEL_MARKET_MISMATCH:{market_id}:{index}")
        funnel_candidates.append(funnel_candidate)
        candidate_id = funnel_candidate.get("candidateId")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in candidate_meta:
            raise PaperDecisionBridgeError(f"CANDIDATE_ID_INVALID_OR_DUPLICATE:{market_id}:{index}")
        candidate_meta[candidate_id] = {
            "displayName": candidate["displayName"], "tickerCode": candidate["tickerCode"],
            "upstreamAction": candidate["upstreamAction"], "tradePlan": plan,
            "tradePlanGate": plan_gate, "exactSources": exact_sources,
        }
    funnel = FUNNEL.reduce_funnel({
        "schemaVersion": FUNNEL.INPUT_SCHEMA_VERSION,
        "contractVersion": FUNNEL.CONTRACT_VERSION,
        "evaluationAt": evaluation_at,
        "candidates": funnel_candidates,
    })

    all_lifecycle_pass = all(lifecycle[gate]["status"] == "PASS" for gate in contract["market_lifecycle_gates"])
    trace = _trace_rows(market, contract, regime_valid)
    all_trace_connected = all(row["outputStatus"] == "PASS" for row in trace)
    runtime_action_authority = all(row["authorityPaperTransition"] for row in trace)
    results = []
    for row in funnel["universe"]:
        meta = candidate_meta[row["candidateId"]]
        reasons = list(row["reasons"])
        candidate_sources_pass = bool(meta["exactSources"]) and all(item["verified"] for item in meta["exactSources"])
        upstream_action = meta["upstreamAction"]
        if evidence_class == "FIXTURE":
            reasons.append("FIXTURE_NOT_PROMOTABLE")
        if not all_lifecycle_pass:
            reasons.append("MARKET_LIFECYCLE_GATES_INCOMPLETE")
        if not all_trace_connected:
            reasons.append("FLOW_FIRST_TRACE_DISCONNECTED")
        if not runtime_action_authority:
            reasons.append("PINNED_COMPONENT_AUTHORITY_BLOCKS_PAPER_TRANSITION")
        if not candidate_sources_pass:
            reasons.append("CANDIDATE_EXACT_SOURCE_HASH_UNVERIFIED")
        if upstream_action is None:
            reasons.append("UPSTREAM_ACTION_MISSING")
        buy_eligible = row["highestStage"] == "PAPER_BUY_ELIGIBLE"
        action_allowed = (
            evidence_class == "NATURAL_READ_ONLY" and all_lifecycle_pass and all_trace_connected
            and runtime_action_authority and candidate_sources_pass and upstream_action is not None
        )
        if upstream_action == "BUY" and not buy_eligible:
            action_allowed = False
            reasons.append("BUY_REQUIRES_PAPER_BUY_ELIGIBLE")
        if upstream_action in ("HOLD", "SELL") and lifecycle["EXIT_ELIGIBILITY"]["status"] != "PASS":
            action_allowed = False
            reasons.append("HOLD_SELL_REQUIRES_EXIT_ELIGIBILITY_PASS")
        if upstream_action == "WAIT":
            action_allowed = False
        action = upstream_action if action_allowed else None
        recommendation = (
            action
            if action is not None
            else upstream_action if upstream_action in ("HOLD", "WAIT") else "WAIT"
        )
        results.append({
            "display_name": meta["displayName"],
            "ticker_code": meta["tickerCode"],
            "market": market_id,
            "candidateId": row["candidateId"],
            "score": row["score"],
            "scoreComponents": copy.deepcopy(row["scoreBreakdown"]),
            "rankWithinMarket": row["rank"],
            "funnel": {"highestStage": row["highestStage"], "flags": copy.deepcopy(row["funnelFlags"])},
            "hardGates": copy.deepcopy(row["hardGates"]),
            "sourceTimestamp": row["sourceTimestamp"],
            "ttlSeconds": row["ttlSeconds"],
            "expiresAt": row["expiresAt"],
            "tradePlan": copy.deepcopy(meta["tradePlan"]),
            "tradePlanGate": copy.deepcopy(meta["tradePlanGate"]),
            "upstreamAction": upstream_action,
            "action": action,
            "recommendation": recommendation,
            "reasons": sorted(set(reasons)),
            "exactSources": copy.deepcopy(meta["exactSources"]),
        })

    ranking = market["observedRanking"]
    computed = {
        "universe": [row["candidateId"] for row in funnel["universe"]],
        "top10": [row["candidateId"] for row in funnel["top10"]],
        "top3": [row["candidateId"] for row in funnel["top3"]],
    }
    market_reasons = sorted(set(
        regime_reasons
        + [gate["reason"] for gate in lifecycle.values() if gate["status"] != "PASS"]
        + [reason for row in trace for reason in row["reasons"] if reason != "LITERAL_PASS"]
    ))
    actions = [row["action"] for row in results if row["action"] is not None]
    recommendations = {row["recommendation"] for row in results}
    market_recommendation = (
        actions[0]
        if len(actions) == 1
        else "HOLD" if recommendations == {"HOLD"} else "WAIT"
    )
    return {
        "market": market_id,
        "sourceTimestamp": market["sourceTimestamp"],
        "ttlSeconds": market["ttlSeconds"],
        "expiresAt": (
            expires_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
            if expires_dt is not None else None
        ),
        "sourceFresh": source_fresh,
        "exactSources": copy.deepcopy(market["exactSources"]),
        "regime": regime,
        "leadership": copy.deepcopy(leadership),
        "riskPacket": copy.deepcopy(market["riskPacket"]),
        "lifecycleGates": lifecycle,
        "rankings": {"observed": copy.deepcopy(ranking), "computedWithinMarket": computed, "crossMarketRankingAuthorized": False},
        "trace": trace,
        "results": results,
        "action": actions[0] if len(actions) == 1 else None,
        "recommendation": market_recommendation,
        "reasons": market_reasons or ["NO_ACTIONABLE_CANDIDATE"],
    }


def _wave10_gate(status: str | None, reason: str, source: dict | None = None) -> dict:
    return {
        "status": status,
        "reason": reason,
        "sources": [copy.deepcopy(source)] if source is not None else [],
    }


def _literal_gate_status(value: object) -> str | None:
    return value if value in ("PASS", "FAIL") else None


def _wave10_market(
    market: str,
    source_timestamp: str | None,
    source: dict,
    leadership: dict,
    ranking: dict,
    lifecycle_statuses: dict,
    trace_statuses: dict,
    contract: dict,
) -> dict:
    return {
        "market": market,
        "sourceTimestamp": source_timestamp,
        "ttlSeconds": None,
        "exactSources": [copy.deepcopy(source)],
        "leadership": copy.deepcopy(leadership),
        "observedRanking": copy.deepcopy(ranking),
        "riskPacket": {
            "status": None,
            "cashAction": None,
            "exposureAction": None,
            "inverseAction": None,
            "hedgeAction": None,
            "reason": "NATURAL_RISK_PACKET_UNOBSERVED",
            "sources": [copy.deepcopy(source)],
        },
        "lifecycleGates": {
            gate: _wave10_gate(
                *lifecycle_statuses.get(gate, (None, f"{gate}_UNOBSERVED")), source
            )
            for gate in contract["market_lifecycle_gates"]
        },
        "traceStages": {
            stage: _wave10_gate(
                *trace_statuses.get(stage, (None, f"{stage}_UNOBSERVED")), source
            )
            for stage in contract["trace_order"]
        },
        "candidates": [],
    }


def build_wave10_natural_input(
    krx_report_path: Path,
    us_report_path: Path,
    crypto_report_path: Path,
    evaluation_at: str,
) -> dict:
    """Normalize the three read-only Wave 10 reports without filling gaps."""
    _parse_utc(evaluation_at, "evaluationAt")
    contract = load_contract()
    paths = {
        "KRX": Path(krx_report_path),
        "US": Path(us_report_path),
        "CRYPTO": Path(crypto_report_path),
    }
    reports = {market: _read_json(path) for market, path in paths.items()}
    for market, report in reports.items():
        if not isinstance(report, dict):
            raise PaperDecisionBridgeError(f"WAVE10_REPORT_NOT_OBJECT:{market}")
    expected_schemas = {
        "KRX": "krx_paper_natural_scheduled_gate_canonical_report/1",
        "US": "us_paper_10_4_natural_scheduled_gate_report/1",
        "CRYPTO": "crypto_spot_paper_10_2_natural_canary_preparation_report/1",
    }
    for market, schema in expected_schemas.items():
        if reports[market].get("schemaVersion") != schema:
            raise PaperDecisionBridgeError(f"WAVE10_{market}_SCHEMA_INVALID")
    sources = {
        market: {"ref": str(path.resolve()), "sha256": file_sha256(path)}
        for market, path in paths.items()
    }
    leadership = {}
    for market, policy in contract["leadership_policy"].items():
        leadership[market] = {
            "transformVersion": policy["transform_version"],
            "approvalStatus": policy["approval_status"],
            "groupCoverageStatus": policy["group_coverage_status"],
            "observationStatus": None,
            "reason": "NATURAL_LEADERSHIP_OBSERVATION_UNOBSERVED",
        }

    krx_audit = reports["KRX"].get("admissionReceipt", {}).get("gateAudit", {})
    krx_completed = [
        krx_audit.get(key, {}).get("status")
        for key in ("COMPLETED_15M", "COMPLETED_1H", "COMPLETED_1D")
    ]
    krx_lifecycle = {
        "MARKET_JUDGEMENT": (None, "THREE_MARKET_REGIME_HEADER_UNOBSERVED"),
        "MARKET_APPROVAL": (
            _literal_gate_status(krx_audit.get("KRX_HARD_GATE_LITERAL_PASS", {}).get("status")),
            krx_audit.get("KRX_HARD_GATE_LITERAL_PASS", {}).get("reasonCode", "KRX_MARKET_GATE_UNOBSERVED"),
        ),
        "LEADERSHIP_APPROVAL": ("PASS", "KOREA_LEADERSHIP_POLICY_RATIFIED"),
        "LEADERSHIP_COVERAGE": ("PASS", "KOREA_GROUP_COVERAGE_NOT_APPLICABLE"),
        "COMPLETED_BAR": (
            "PASS" if krx_completed and all(item == "PASS" for item in krx_completed) else None,
            "KRX_COMPLETED_BARS_NOT_LITERAL_PASS",
        ),
        "FRESHNESS": (
            _literal_gate_status(krx_audit.get("FRESHNESS_TTL", {}).get("status")),
            krx_audit.get("FRESHNESS_TTL", {}).get("reasonCode", "KRX_TTL_UNOBSERVED"),
        ),
        "ENTRY_ELIGIBILITY": (
            _literal_gate_status(krx_audit.get("COMMON_FUNNEL_PAPER_BUY_ELIGIBLE", {}).get("status")),
            krx_audit.get("COMMON_FUNNEL_PAPER_BUY_ELIGIBLE", {}).get("reasonCode", "KRX_ENTRY_UNOBSERVED"),
        ),
        "EXIT_ELIGIBILITY": (None, "KRX_EXIT_ELIGIBILITY_UNOBSERVED"),
        "RISK_PACKET": (None, "KRX_RISK_PACKET_UNOBSERVED"),
        "LEDGER_INTEGRITY": (
            _literal_gate_status(krx_audit.get("LEDGER_RECONCILIATION", {}).get("status")),
            krx_audit.get("LEDGER_RECONCILIATION", {}).get("reasonCode", "KRX_LEDGER_UNOBSERVED"),
        ),
    }
    us_lifecycle = {
        "MARKET_JUDGEMENT": (None, "THREE_MARKET_REGIME_HEADER_UNOBSERVED"),
        "MARKET_APPROVAL": (None, "US_MARKET_APPROVAL_UNOBSERVED"),
        "LEADERSHIP_APPROVAL": (None, "US_LEADERSHIP_POLICY_UNRATIFIED"),
        "LEADERSHIP_COVERAGE": (None, "US_LEADERSHIP_COVERAGE_UNRATIFIED"),
        "COMPLETED_BAR": (None, "US_COMPLETED_BARS_UNOBSERVED"),
        "FRESHNESS": (None, "US_TTL_UNOBSERVED"),
        "ENTRY_ELIGIBILITY": (None, "US_NATURAL_ENTRY_INPUT_ABSENT"),
        "EXIT_ELIGIBILITY": (None, "US_EXIT_ELIGIBILITY_UNOBSERVED"),
        "RISK_PACKET": (None, "US_RISK_PACKET_UNOBSERVED"),
        "LEDGER_INTEGRITY": (None, "US_LEDGER_UNOBSERVED"),
    }
    crypto_natural = reports["CRYPTO"].get("natural", {})
    crypto_ranking = {
        key: list(crypto_natural.get(key, []))
        for key in ("universe", "top10", "top3")
    }
    crypto_lifecycle = {
        "MARKET_JUDGEMENT": (None, "THREE_MARKET_REGIME_HEADER_UNOBSERVED"),
        "MARKET_APPROVAL": (None, "CRYPTO_MARKET_APPROVAL_UNOBSERVED"),
        "LEADERSHIP_APPROVAL": ("PASS", "CRYPTO_LEADERSHIP_POLICY_RATIFIED"),
        "LEADERSHIP_COVERAGE": (None, "CRYPTO_GROUP_COVERAGE_UNRATIFIED"),
        "COMPLETED_BAR": ("FAIL", "COMPLETED_BAR_STALE_OR_ATTESTATION_MISMATCH"),
        "FRESHNESS": ("FAIL", "NATURAL_REPORT_TTL_NOT_PROVIDED"),
        "ENTRY_ELIGIBILITY": (None, "NATURAL_FOUR_COMPONENT_SCORE_MISSING"),
        "EXIT_ELIGIBILITY": (None, "CRYPTO_EXIT_ELIGIBILITY_UNOBSERVED"),
        "RISK_PACKET": (None, "CRYPTO_RISK_PACKET_UNOBSERVED"),
        "LEDGER_INTEGRITY": (
            "PASS" if crypto_natural.get("reconciliation") == "MATCHED" else None,
            "CRYPTO_RECONCILIATION_STATUS",
        ),
    }
    common_trace = {
        "FLOW_FIRST_BRIEFING": (None, "FLOW_FIRST_RUNTIME_PACKET_UNOBSERVED"),
        "THREE_MARKET_REGIME_HEADER": (None, "THREE_MARKET_REGIME_HEADER_UNOBSERVED"),
        "LEADERSHIP": (None, "NATURAL_LEADERSHIP_OBSERVATION_UNOBSERVED"),
        "CAPITAL_ROTATION": (None, "NATURAL_CAPITAL_ROTATION_UNOBSERVED"),
        "CASH_EXPOSURE_ACTION": (None, "CASH_EXPOSURE_NOT_EVALUATED"),
        "DEFENSIVE_ACTION_DECISION": (None, "DEFENSIVE_ACTION_BLOCKED"),
        "HEDGE_INSTRUMENT_ELIGIBILITY": (None, "HEDGE_REGISTRY_UNRATIFIED_OR_UNSUPPORTED"),
        "BEAR_HEDGE_RISK_BUDGET": (None, "HEDGE_BUDGET_UNRATIFIED"),
        "STRATEGIC_CAPITAL_POSTURE": (None, "STRATEGIC_POSTURE_BLOCKED"),
        "COMMON_CANDIDATE_FUNNEL": (None, "NATURAL_PAPER_BUY_ELIGIBLE_UNOBSERVED"),
        "ENTRY_EXIT_TRIGGER_ELIGIBILITY": (None, "TRIGGER_POLICY_ABSENT"),
        "LEDGER": (None, "PAPER_LEDGER_TRANSITION_NOT_AUTHORIZED"),
    }
    crypto_trace = copy.deepcopy(common_trace)
    crypto_trace["CAPITAL_ROTATION"] = (None, "CRYPTO_CAPITAL_ROTATION_CONTRACT_ABSENT")
    crypto_trace["COMMON_CANDIDATE_FUNNEL"] = ("FAIL", "NATURAL_FOUR_COMPONENT_SCORE_MISSING")
    return {
        "schemaVersion": INPUT_SCHEMA_VERSION,
        "contractVersion": CONTRACT_VERSION,
        "evaluationAt": evaluation_at,
        "evidenceClass": "NATURAL_READ_ONLY",
        "regimeHeader": None,
        "regimeHeaderSource": {"ref": None, "sha256": None},
        "markets": [
            _wave10_market(
                "KRX", reports["KRX"].get("generatedAtUtc"), sources["KRX"], leadership["KRX"],
                {"universe": [], "top10": [], "top3": []}, krx_lifecycle, common_trace, contract,
            ),
            _wave10_market(
                "US", reports["US"].get("evaluatedAtUtc"), sources["US"], leadership["US"],
                {"universe": [], "top10": [], "top3": []}, us_lifecycle, common_trace, contract,
            ),
            _wave10_market(
                "CRYPTO", None, sources["CRYPTO"], leadership["CRYPTO"], crypto_ranking,
                crypto_lifecycle, crypto_trace, contract,
            ),
        ],
    }


def build_receipt(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    if not isinstance(value, dict):
        raise PaperDecisionBridgeError("INPUT_NOT_OBJECT")
    required = {"schemaVersion", "contractVersion", "evaluationAt", "evidenceClass", "regimeHeader", "regimeHeaderSource", "markets"}
    if set(value) != required or value.get("schemaVersion") != INPUT_SCHEMA_VERSION or value.get("contractVersion") != CONTRACT_VERSION:
        raise PaperDecisionBridgeError("INPUT_FIELDS_OR_VERSION_INVALID")
    evaluation_at = value["evaluationAt"]
    _parse_utc(evaluation_at, "evaluationAt")
    if value["evidenceClass"] not in ("NATURAL_READ_ONLY", "FIXTURE"):
        raise PaperDecisionBridgeError("EVIDENCE_CLASS_INVALID")
    regime_source = _source(value["regimeHeaderSource"], "regimeHeaderSource")
    regime_header = None
    regime_valid = False
    regime_error = None
    if value["regimeHeader"] is None:
        regime_error = "THREE_MARKET_REGIME_HEADER_MISSING"
    else:
        try:
            regime_header = REGIME_HEADER.validate_header(copy.deepcopy(value["regimeHeader"]))
            regime_valid = regime_source["verified"]
            if not regime_valid:
                regime_error = "THREE_MARKET_REGIME_HEADER_SOURCE_HASH_UNVERIFIED"
        except Exception as exc:
            regime_error = f"THREE_MARKET_REGIME_HEADER_INVALID:{exc}"
            regime_header = None

    markets_input = value["markets"]
    if not isinstance(markets_input, list) or len(markets_input) != 3:
        raise PaperDecisionBridgeError("THREE_MARKETS_REQUIRED")
    by_market = {}
    for item in markets_input:
        if not isinstance(item, dict) or item.get("market") not in contract["markets"] or item["market"] in by_market:
            raise PaperDecisionBridgeError("MARKET_ID_INVALID_OR_DUPLICATE")
        by_market[item["market"]] = item
    if list(sorted(by_market, key=contract["markets"].index)) != contract["markets"]:
        raise PaperDecisionBridgeError("MARKET_SET_INVALID")
    markets = [
        _reduce_market(
            _validate_market_shape(by_market[market], market, contract), evaluation_at,
            value["evidenceClass"], contract, regime_header, regime_valid,
        )
        for market in contract["markets"]
    ]
    all_unknown = all(row["regime"]["regime"] == "UNKNOWN" for row in markets)
    actions = [row["action"] for row in markets if row["action"] is not None]
    receipt = {
        "schemaVersion": OUTPUT_SCHEMA_VERSION,
        "contractVersion": CONTRACT_VERSION,
        "decisionIdentitySha256": payload_sha256(value),
        "evidenceClass": value["evidenceClass"],
        "evaluationAt": evaluation_at,
        "regimeHeader": {
            "status": "PASS" if regime_valid else "FAIL",
            "reason": "EXACT_HEADER_VALIDATED" if regime_valid else regime_error,
            "source": regime_source,
        },
        "markets": markets,
        "summary": {
            "marketCount": 3,
            "marketIndependentGates": True,
            "crossMarketCandidateRankingAuthorized": False,
            "top3MeansThreeMarkets": False,
            "allMarketsUnknown": all_unknown,
            "paperTransitionCount": len(actions),
            "ledgerMutationCount": 0,
            "action": actions[0] if len(actions) == 1 else None,
            "recommendation": actions[0] if len(actions) == 1 else "WAIT",
            "sameIdentityDisposition": "NO_CHANGE",
        },
        "authority": copy.deepcopy(contract["permanent_false_authority"]),
    }
    if all_unknown:
        receipt["summary"]["action"] = None
        receipt["summary"]["recommendation"] = "WAIT"
    receipt["receiptSha256"] = payload_sha256(receipt)
    return validate_receipt(receipt, contract)


def validate_receipt(receipt: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    if not isinstance(receipt, dict):
        raise PaperDecisionBridgeError("RECEIPT_NOT_OBJECT")
    digest = receipt.get("receiptSha256")
    if not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None:
        raise PaperDecisionBridgeError("RECEIPT_SHA_INVALID")
    normalized = copy.deepcopy(receipt)
    normalized.pop("receiptSha256", None)
    if payload_sha256(normalized) != digest:
        raise PaperDecisionBridgeError("RECEIPT_SHA_MISMATCH")
    if receipt.get("schemaVersion") != OUTPUT_SCHEMA_VERSION or receipt.get("contractVersion") != CONTRACT_VERSION:
        raise PaperDecisionBridgeError("RECEIPT_VERSION_INVALID")
    if [row.get("market") for row in receipt.get("markets", [])] != contract["markets"]:
        raise PaperDecisionBridgeError("RECEIPT_MARKET_ORDER_INVALID")
    if any(value is not False for value in receipt.get("authority", {}).values()):
        raise PaperDecisionBridgeError("RECEIPT_AUTHORITY_INVALID")
    if receipt["summary"]["allMarketsUnknown"] and (receipt["summary"]["action"] is not None or receipt["summary"]["recommendation"] != "WAIT"):
        raise PaperDecisionBridgeError("UNKNOWN_MARKETS_MUST_WAIT")
    return copy.deepcopy(receipt)


def persist_immutable_receipt(receipt: dict, directory: Path) -> tuple[Path, str]:
    checked = validate_receipt(receipt)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{checked['decisionIdentitySha256']}.json"
    payload = json.dumps(checked, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PaperDecisionBridgeError(f"RECEIPT_READ_FAILED:{path}:{exc}") from exc
        if existing != payload:
            raise PaperDecisionBridgeError("IMMUTABLE_RECEIPT_CONFLICT")
        return path, "NO_CHANGE"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path, "CREATED"


def _write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
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


def _run_value(value: dict, output_path: Path, receipt_dir: Path) -> int:
    try:
        receipt = build_receipt(value)
        receipt_path, disposition = persist_immutable_receipt(receipt, receipt_dir)
        _write_json_atomic(output_path, {
            "schemaVersion": "paper_decision_bridge_run/1",
            "disposition": disposition,
            "receiptPath": str(receipt_path),
            "receipt": receipt,
        })
        return 0
    except (PaperDecisionBridgeError, OSError, TypeError, ValueError) as exc:
        print(f"PAPER decision bridge failed: {exc}")
        return 1


def run(input_path: Path, output_path: Path, receipt_dir: Path) -> int:
    try:
        value = _read_json(input_path)
    except PaperDecisionBridgeError as exc:
        print(f"PAPER decision bridge failed: {exc}")
        return 1
    return _run_value(value, output_path, receipt_dir)


def run_wave10(
    krx_report: Path,
    us_report: Path,
    crypto_report: Path,
    evaluation_at: str,
    output_path: Path,
    receipt_dir: Path,
) -> int:
    try:
        value = build_wave10_natural_input(
            krx_report, us_report, crypto_report, evaluation_at
        )
    except (PaperDecisionBridgeError, OSError, TypeError, ValueError) as exc:
        print(f"PAPER decision bridge failed: {exc}")
        return 1
    return _run_value(value, output_path, receipt_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immutable three-market PAPER decision receipts")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--krx-wave10-report", type=Path)
    parser.add_argument("--us-wave10-report", type=Path)
    parser.add_argument("--crypto-wave10-report", type=Path)
    parser.add_argument("--evaluation-at")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    args = parser.parse_args()
    wave10 = (args.krx_wave10_report, args.us_wave10_report, args.crypto_wave10_report)
    if args.input is not None:
        if any(item is not None for item in wave10) or args.evaluation_at is not None:
            parser.error("--input cannot be combined with Wave 10 report options")
        return run(args.input, args.out, args.receipt_dir)
    if any(item is None for item in wave10) or args.evaluation_at is None:
        parser.error("provide --input or all three Wave 10 reports plus --evaluation-at")
    return run_wave10(*wave10, args.evaluation_at, args.out, args.receipt_dir)


if __name__ == "__main__":
    raise SystemExit(main())
