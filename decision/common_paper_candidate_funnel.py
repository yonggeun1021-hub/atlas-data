"""Deterministic Korea/US/Crypto candidate funnel for internal virtual PAPER.

This module is deliberately a pure reducer.  It performs no provider, broker,
ledger, account, or network call.  Market-specific producers retain ownership
of their score components and evidence; this common layer validates and sums
their exact point breakdown, applies the ratified funnel thresholds, derives
TTL expiry, and fails closed on every missing/null Hard Gate.

Human approval and user-receipt fields are absent from the input on purpose:
they cannot block internal virtual PAPER.  They also cannot grant eligibility.
Eligibility still requires score >= 75, every Hard Gate PASS, and a completed
bar trigger.  REAL/live/real-capital/Production/Trading and every broker POST
remain permanently false.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "common_paper_candidate_funnel_contract.json"
SCHEMA_PATH = ROOT / "schemas" / "common_paper_candidate_funnel.schema.json"

INPUT_SCHEMA_VERSION = "common_paper_candidate_funnel_input/1"
OUTPUT_SCHEMA_VERSION = "common_paper_candidate_funnel_output/1"
CONTRACT_VERSION = "common_paper_candidate_funnel_contract/1"
MARKETS = ("KOREA", "US", "CRYPTO")
CANDIDATE_LANES = ("PRIMARY_LONG", "DEFENSIVE_ACTION")
PERFORMANCE_LANES = ("SYSTEM_CANARY", "INVESTMENT_PAPER", "SYSTEM_HEDGE_CANARY", "INVESTMENT_HEDGE_PAPER")
DEFENSIVE_ACTIONS = ("NONE", "CASH", "REDUCE", "HEDGE", "INVERSE")
GATE_STATUSES = ("PASS", "FAIL", None)
HARD_GATES = (
    "IDENTITY_TRADEABLE",
    "COMPLETED_BAR",
    "FRESHNESS",
    "LIQUIDITY",
    "DUPLICATE_IDEMPOTENCY",
    "LEDGER_INTEGRITY",
    "RESTART_RECOVERY",
    "RISK_BUDGET",
    "MARKET_SPECIFIC_SAFETY",
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class CommonPaperCandidateFunnelError(ValueError):
    """Fail-closed contract or packet error."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CommonPaperCandidateFunnelError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict):
        raise CommonPaperCandidateFunnelError("CONTRACT_NOT_OBJECT")
    if value.get("schema_version") != 1 or value.get("contract_version") != CONTRACT_VERSION:
        raise CommonPaperCandidateFunnelError("CONTRACT_VERSION_MISMATCH")
    if (
        tuple(value.get("markets", [])) != MARKETS
        or tuple(value.get("candidate_lanes", [])) != CANDIDATE_LANES
        or tuple(value.get("performance_lanes", [])) != PERFORMANCE_LANES
        or tuple(value.get("defensive_actions", [])) != DEFENSIVE_ACTIONS
    ):
        raise CommonPaperCandidateFunnelError("CONTRACT_SCOPE_MISMATCH")
    if tuple(value.get("hard_gates", [])) != HARD_GATES:
        raise CommonPaperCandidateFunnelError("CONTRACT_HARD_GATES_MISMATCH")
    funnel = value.get("funnel", {})
    if (
        funnel.get("top10_limit") != 10
        or funnel.get("top3_limit") != 3
        or funnel.get("candidate_min_score") != 60
        or funnel.get("ready_min_score") != 70
        or funnel.get("paper_buy_eligible_min_score") != 75
        or funnel.get("score_total") != 100
    ):
        raise CommonPaperCandidateFunnelError("CONTRACT_FUNNEL_MISMATCH")
    internal = value.get("paper_internal_authority", {})
    if internal.get("PAPER_INTERNAL_AUTO") is not True:
        raise CommonPaperCandidateFunnelError("CONTRACT_INTERNAL_AUTO_NOT_TRUE")
    for key in ("humanApprovalRequired", "userReceiptRequired", "external_system_calls_authorized"):
        if internal.get(key) is not False:
            raise CommonPaperCandidateFunnelError(f"CONTRACT_AUTHORITY_INVALID:{key}")
    permanent = value.get("permanent_false_authority", {})
    if not permanent or any(item is not False for item in permanent.values()):
        raise CommonPaperCandidateFunnelError("CONTRACT_PERMANENT_FALSE_AUTHORITY_INVALID")
    if internal.get("broker_mock_post_count") != 0:
        raise CommonPaperCandidateFunnelError("CONTRACT_BROKER_MOCK_POST_NONZERO")
    us_transport = value.get("us_paper_transport_boundary", {})
    if us_transport.get("profiles") != [
        {"id": "ALPACA_PAPER", "priority": 1},
        {"id": "KIS_US_PAPER", "priority": 2},
    ]:
        raise CommonPaperCandidateFunnelError("CONTRACT_US_TRANSPORT_PRIORITY_INVALID")
    if (
        us_transport.get("explicit_selector_required") is not True
        or us_transport.get("automatic_fallback_authorized") is not False
        or us_transport.get("internal_virtual_paper_grants_external_transport") is not False
        or us_transport.get("missing_credentials_or_admission_policy") != "NETWORK_GET_POST_ZERO"
        or any(us_transport.get(key) != 0 for key in ("network_call_count", "get_call_count", "post_call_count"))
    ):
        raise CommonPaperCandidateFunnelError("CONTRACT_US_TRANSPORT_BOUNDARY_INVALID")
    return copy.deepcopy(value)


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict) or value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise CommonPaperCandidateFunnelError("JSON_SCHEMA_INVALID")
    if not isinstance(value.get("$defs", {}).get("input"), dict) or not isinstance(value["$defs"].get("output"), dict):
        raise CommonPaperCandidateFunnelError("JSON_SCHEMA_DEFINITIONS_MISSING")
    return copy.deepcopy(value)


def _require_exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise CommonPaperCandidateFunnelError(f"SCHEMA_KEYS_MISMATCH:{label}:{actual}")
    return value


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CommonPaperCandidateFunnelError(f"UTC_INVALID:{label}")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CommonPaperCandidateFunnelError(f"UTC_INVALID:{label}") from exc
    if parsed.tzinfo != dt.timezone.utc:
        raise CommonPaperCandidateFunnelError(f"UTC_INVALID:{label}")
    return parsed


def _utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CommonPaperCandidateFunnelError(f"DECIMAL_INVALID:{label}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise CommonPaperCandidateFunnelError(f"DECIMAL_INVALID:{label}")
    return parsed


def _validate_score_breakdown(value: object, label: str) -> tuple[list[dict], int]:
    if not isinstance(value, list) or not value:
        raise CommonPaperCandidateFunnelError(f"SCORE_BREAKDOWN_INVALID:{label}")
    rows: list[dict] = []
    component_ids: set[str] = set()
    max_total = 0
    score = 0
    expected = {"componentId", "points", "maxPoints", "reason", "sourceRef"}
    for index, item in enumerate(value):
        row = _require_exact_keys(item, expected, f"{label}[{index}]")
        component_id = row["componentId"]
        if not isinstance(component_id, str) or not component_id or component_id in component_ids:
            raise CommonPaperCandidateFunnelError(f"SCORE_COMPONENT_ID_INVALID:{label}:{index}")
        component_ids.add(component_id)
        points, maximum = row["points"], row["maxPoints"]
        if isinstance(points, bool) or not isinstance(points, int) or isinstance(maximum, bool) or not isinstance(maximum, int):
            raise CommonPaperCandidateFunnelError(f"SCORE_COMPONENT_POINTS_INVALID:{label}:{component_id}")
        if maximum <= 0 or points < 0 or points > maximum:
            raise CommonPaperCandidateFunnelError(f"SCORE_COMPONENT_RANGE_INVALID:{label}:{component_id}")
        if not isinstance(row["reason"], str) or not row["reason"] or not isinstance(row["sourceRef"], str) or not row["sourceRef"]:
            raise CommonPaperCandidateFunnelError(f"SCORE_COMPONENT_EVIDENCE_INVALID:{label}:{component_id}")
        rows.append(copy.deepcopy(row))
        max_total += maximum
        score += points
    if max_total != 100:
        raise CommonPaperCandidateFunnelError(f"SCORE_MAX_TOTAL_NOT_100:{label}:{max_total}")
    return rows, score


def _validate_gate(value: object, label: str) -> dict:
    row = _require_exact_keys(value, {"status", "reason", "evidenceRef"}, label)
    if row["status"] not in GATE_STATUSES:
        raise CommonPaperCandidateFunnelError(f"HARD_GATE_STATUS_INVALID:{label}")
    for key in ("reason", "evidenceRef"):
        if row[key] is not None and (not isinstance(row[key], str) or not row[key]):
            raise CommonPaperCandidateFunnelError(f"HARD_GATE_FIELD_INVALID:{label}:{key}")
    if row["reason"] is None:
        raise CommonPaperCandidateFunnelError(f"HARD_GATE_REASON_MISSING:{label}")
    if row["status"] == "PASS" and row["evidenceRef"] is None:
        raise CommonPaperCandidateFunnelError(f"HARD_GATE_PASS_EVIDENCE_MISSING:{label}")
    return copy.deepcopy(row)


def _effective_gate(source: dict | None, gate_id: str) -> dict:
    if source is None:
        return {"status": None, "reason": f"MISSING_HARD_GATE:{gate_id}", "evidenceRef": None}
    return _validate_gate(source, f"hardGates.{gate_id}")


def _validate_candidate(value: object, index: int, evaluation_at: dt.datetime, contract: dict) -> dict:
    expected = {
        "market", "candidateId", "symbol", "candidateLane", "lane", "defensiveAction", "sourceTimestamp", "ttlSeconds",
        "scoreBreakdown", "completedBarTrigger", "hardGates", "risk", "sourceRefs",
    }
    row = _require_exact_keys(value, expected, f"candidates[{index}]")
    if row["market"] not in MARKETS or row["candidateLane"] not in CANDIDATE_LANES or row["lane"] not in PERFORMANCE_LANES:
        raise CommonPaperCandidateFunnelError(f"CANDIDATE_SCOPE_INVALID:{index}")
    if row["defensiveAction"] not in DEFENSIVE_ACTIONS:
        raise CommonPaperCandidateFunnelError(f"DEFENSIVE_ACTION_INVALID:{index}")
    if (row["candidateLane"] == "PRIMARY_LONG") != (row["defensiveAction"] == "NONE"):
        raise CommonPaperCandidateFunnelError(f"DEFENSIVE_ACTION_LANE_MISMATCH:{index}")
    hedge_lane = row["lane"] in {"SYSTEM_HEDGE_CANARY", "INVESTMENT_HEDGE_PAPER"}
    if hedge_lane != (row["candidateLane"] == "DEFENSIVE_ACTION"):
        raise CommonPaperCandidateFunnelError(f"PERFORMANCE_LANE_MISMATCH:{index}")
    for key in ("candidateId", "symbol"):
        if not isinstance(row[key], str) or not row[key]:
            raise CommonPaperCandidateFunnelError(f"CANDIDATE_IDENTITY_INVALID:{index}:{key}")
    source_at = _parse_utc(row["sourceTimestamp"], f"candidates[{index}].sourceTimestamp")
    ttl = row["ttlSeconds"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise CommonPaperCandidateFunnelError(f"TTL_INVALID:{index}")
    expiry_at = source_at + dt.timedelta(seconds=ttl)
    score_breakdown, score = _validate_score_breakdown(row["scoreBreakdown"], f"candidates[{index}].scoreBreakdown")

    trigger = _require_exact_keys(row["completedBarTrigger"], {"status", "barId", "completedAt"}, f"candidates[{index}].completedBarTrigger")
    if trigger["status"] not in GATE_STATUSES:
        raise CommonPaperCandidateFunnelError(f"COMPLETED_BAR_STATUS_INVALID:{index}")
    completed_at = None
    if trigger["completedAt"] is not None:
        completed_at = _parse_utc(trigger["completedAt"], f"candidates[{index}].completedBarTrigger.completedAt")
        if completed_at > evaluation_at:
            raise CommonPaperCandidateFunnelError(f"COMPLETED_BAR_FUTURE_DATED:{index}")
    if trigger["barId"] is not None and (not isinstance(trigger["barId"], str) or not trigger["barId"]):
        raise CommonPaperCandidateFunnelError(f"COMPLETED_BAR_ID_INVALID:{index}")
    trigger_pass = trigger["status"] == "PASS" and trigger["barId"] is not None and completed_at is not None

    hard_gate_input = row["hardGates"]
    if not isinstance(hard_gate_input, dict) or not set(hard_gate_input).issubset(HARD_GATES):
        raise CommonPaperCandidateFunnelError(f"HARD_GATES_SCHEMA_INVALID:{index}")
    gates = {gate_id: _effective_gate(hard_gate_input.get(gate_id), gate_id) for gate_id in HARD_GATES}
    if not trigger_pass:
        status = "FAIL" if trigger["status"] == "FAIL" or trigger["status"] == "PASS" else None
        gates["COMPLETED_BAR"] = {
            "status": status,
            "reason": "COMPLETED_BAR_TRIGGER_NOT_PROVEN",
            "evidenceRef": gates["COMPLETED_BAR"].get("evidenceRef"),
        }
    elif gates["COMPLETED_BAR"]["status"] != "PASS":
        trigger_pass = False

    future_dated = source_at > evaluation_at
    expired = evaluation_at >= expiry_at
    if future_dated:
        gates["FRESHNESS"] = {
            "status": "FAIL",
            "reason": "SOURCE_TIMESTAMP_FUTURE_DATED",
            "evidenceRef": gates["FRESHNESS"].get("evidenceRef"),
        }
    elif expired:
        gates["FRESHNESS"] = {
            "status": "FAIL",
            "reason": "SOURCE_TTL_EXPIRED",
            "evidenceRef": gates["FRESHNESS"].get("evidenceRef"),
        }

    risk = _require_exact_keys(
        row["risk"],
        {"currentOpenLongPositionCount", "currentOpenHedgePositionCount", "plannedLossNavFraction", "longMarketExposureNavFraction", "hedgeMarketExposureNavFraction"},
        f"candidates[{index}].risk",
    )
    long_open_count = risk["currentOpenLongPositionCount"]
    hedge_open_count = risk["currentOpenHedgePositionCount"]
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in (long_open_count, hedge_open_count)):
        raise CommonPaperCandidateFunnelError(f"OPEN_POSITION_COUNT_INVALID:{index}")
    planned_loss = _decimal(risk["plannedLossNavFraction"], f"candidates[{index}].plannedLossNavFraction")
    long_exposure = _decimal(risk["longMarketExposureNavFraction"], f"candidates[{index}].longMarketExposureNavFraction")
    hedge_exposure = _decimal(risk["hedgeMarketExposureNavFraction"], f"candidates[{index}].hedgeMarketExposureNavFraction")
    risk_policy = contract["risk"]
    hedge_ratio_pass = hedge_exposure <= long_exposure * Decimal(risk_policy["max_hedge_to_long_beta_ratio"])
    hedge_bucket_pass = (
        hedge_exposure <= Decimal(risk_policy["max_single_hedge_instrument_nav_fraction"])
        and (hedge_ratio_pass if hedge_exposure > 0 else True)
    )
    position_count_pass = (
        hedge_open_count < risk_policy["max_hedge_positions_per_market"]
        if row["candidateLane"] == "DEFENSIVE_ACTION"
        else long_open_count < risk_policy["max_long_positions_per_market"]
    )
    risk_pass = (
        position_count_pass
        and planned_loss > 0
        and planned_loss <= Decimal(risk_policy["max_planned_loss_nav_fraction_per_trade"])
        and long_exposure <= Decimal(risk_policy["max_long_market_exposure_nav_fraction"])
        and hedge_bucket_pass
    )
    if not risk_pass:
        gates["RISK_BUDGET"] = {
            "status": "FAIL",
            "reason": "VIRTUAL_NAV_RISK_BUDGET_EXCEEDED_OR_INVALID",
            "evidenceRef": gates["RISK_BUDGET"].get("evidenceRef"),
        }

    refs = row["sourceRefs"]
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref for ref in refs) or len(set(refs)) != len(refs):
        raise CommonPaperCandidateFunnelError(f"SOURCE_REFS_INVALID:{index}")

    return {
        "market": row["market"],
        "candidateId": row["candidateId"],
        "symbol": row["symbol"],
        "candidateLane": row["candidateLane"],
        "lane": row["lane"],
        "defensiveAction": row["defensiveAction"],
        "sourceTimestamp": row["sourceTimestamp"],
        "ttlSeconds": ttl,
        "expiresAt": _utc_text(expiry_at),
        "expired": expired,
        "score": score,
        "scoreBreakdown": score_breakdown,
        "completedBarTrigger": copy.deepcopy(trigger),
        "hardGates": gates,
        "risk": {
            **copy.deepcopy(risk),
            "exposureAccounting": {
                "longExposureBucketNavFraction": risk["longMarketExposureNavFraction"],
                "hedgeExposureBucketNavFraction": risk["hedgeMarketExposureNavFraction"],
                "longPositionCount": risk["currentOpenLongPositionCount"],
                "hedgePositionCount": risk["currentOpenHedgePositionCount"],
                "nettedMarketExposureNavFraction": None,
                "hedgeExcludedFromLongExposure": True,
                "hedgePositionExcludedFromLongPositionCount": True,
                "longHedgeCrossBucketNettingAuthorized": False,
            },
        },
        "sourceRefs": copy.deepcopy(refs),
    }


def _mark_duplicate_gates(rows: list[dict]) -> None:
    by_identity: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (row["market"], row["candidateId"], row["sourceTimestamp"])
        by_identity.setdefault(key, []).append(row)
    for key, matches in by_identity.items():
        if len(matches) > 1:
            for row in matches:
                row["hardGates"]["DUPLICATE_IDEMPOTENCY"] = {
                    "status": "FAIL",
                    "reason": "DUPLICATE_CANDIDATE_IDENTITY",
                    "evidenceRef": canonical_json(key),
                }


def _finalize_row(row: dict, rank: int, top10_limit: int, top3_limit: int, contract: dict) -> dict:
    result = copy.deepcopy(row)
    score = result["score"]
    funnel = contract["funnel"]
    all_gates_pass = all(result["hardGates"][gate_id]["status"] == "PASS" for gate_id in HARD_GATES)
    completed_bar_pass = (
        result["completedBarTrigger"]["status"] == "PASS"
        and result["completedBarTrigger"]["barId"] is not None
        and result["completedBarTrigger"]["completedAt"] is not None
        and result["hardGates"]["COMPLETED_BAR"]["status"] == "PASS"
    )
    flags = {
        "universe": True,
        "top10": rank <= top10_limit,
        "top3": rank <= top3_limit,
        "candidate": score >= funnel["candidate_min_score"],
        "ready": score >= funnel["ready_min_score"],
        "paperBuyEligible": (
            score >= funnel["paper_buy_eligible_min_score"]
            and all_gates_pass
            and completed_bar_pass
            and result["defensiveAction"] not in {"CASH", "REDUCE"}
        ),
    }
    if flags["paperBuyEligible"]:
        highest_stage = "PAPER_BUY_ELIGIBLE"
    elif flags["ready"]:
        highest_stage = "READY"
    elif flags["candidate"]:
        highest_stage = "CANDIDATE"
    elif flags["top3"]:
        highest_stage = "TOP3"
    elif flags["top10"]:
        highest_stage = "TOP10"
    else:
        highest_stage = "UNIVERSE"

    reasons: list[str] = []
    if score < funnel["candidate_min_score"]:
        reasons.append(f"SCORE_BELOW_CANDIDATE:{score}<{funnel['candidate_min_score']}")
    elif score < funnel["ready_min_score"]:
        reasons.append(f"SCORE_BELOW_READY:{score}<{funnel['ready_min_score']}")
    elif score < funnel["paper_buy_eligible_min_score"]:
        reasons.append(f"SCORE_BELOW_PAPER_BUY_ELIGIBLE:{score}<{funnel['paper_buy_eligible_min_score']}")
    for gate_id in HARD_GATES:
        gate = result["hardGates"][gate_id]
        if gate["status"] != "PASS":
            reasons.append(f"HARD_GATE_{gate['status'] or 'NULL'}:{gate_id}:{gate['reason']}")
    if result["defensiveAction"] in {"CASH", "REDUCE"}:
        reasons.append("DEFENSIVE_ACTION_IS_NOT_A_BUY")
    if not reasons:
        reasons.append("ALL_THRESHOLDS_AND_HARD_GATES_PASSED")

    result.update({
        "rank": rank,
        "funnelFlags": flags,
        "highestStage": highest_stage,
        "reasons": reasons,
        "authority": {
            "PAPER_INTERNAL_AUTO": True,
            "humanApprovalRequired": False,
            "userReceiptRequired": False,
            "internalVirtualLedgerMutationEligible": flags["paperBuyEligible"],
            **copy.deepcopy(contract["permanent_false_authority"]),
        },
    })
    return result


def _lane_summary(rows: list[dict], lane: str) -> dict:
    selected = [row for row in rows if row["lane"] == lane]
    return {
        "universeCount": len(selected),
        "candidateCount": sum(row["funnelFlags"]["candidate"] for row in selected),
        "readyCount": sum(row["funnelFlags"]["ready"] for row in selected),
        "paperBuyEligibleCount": sum(row["funnelFlags"]["paperBuyEligible"] for row in selected),
        "performanceCohort": lane,
    }


def reduce_funnel(payload: object, contract_path: Path = CONTRACT_PATH) -> dict:
    """Validate and reduce one deterministic three-market funnel packet."""
    contract = load_contract(contract_path)
    envelope = _require_exact_keys(payload, {"schemaVersion", "contractVersion", "evaluationAt", "candidates"}, "input")
    if envelope["schemaVersion"] != INPUT_SCHEMA_VERSION or envelope["contractVersion"] != CONTRACT_VERSION:
        raise CommonPaperCandidateFunnelError("INPUT_VERSION_MISMATCH")
    evaluation_at = _parse_utc(envelope["evaluationAt"], "evaluationAt")
    if not isinstance(envelope["candidates"], list):
        raise CommonPaperCandidateFunnelError("CANDIDATES_NOT_ARRAY")
    rows = [_validate_candidate(value, index, evaluation_at, contract) for index, value in enumerate(envelope["candidates"])]
    _mark_duplicate_gates(rows)
    market_order = {market: index for index, market in enumerate(MARKETS)}
    rows.sort(key=lambda row: (-row["score"], market_order[row["market"]], row["candidateId"], row["sourceTimestamp"]))
    rows = [
        _finalize_row(row, rank, contract["funnel"]["top10_limit"], contract["funnel"]["top3_limit"], contract)
        for rank, row in enumerate(rows, 1)
    ]

    top10 = [row for row in rows if row["funnelFlags"]["top10"]]
    top3 = [row for row in rows if row["funnelFlags"]["top3"]]
    candidate_rows = [row for row in rows if row["funnelFlags"]["candidate"]]
    ready_rows = [row for row in rows if row["funnelFlags"]["ready"]]
    eligible_rows = [row for row in rows if row["funnelFlags"]["paperBuyEligible"]]
    identity_source = {
        "schemaVersion": envelope["schemaVersion"],
        "contractVersion": envelope["contractVersion"],
        "evaluationAt": envelope["evaluationAt"],
        "candidates": sorted(copy.deepcopy(envelope["candidates"]), key=canonical_json),
    }
    output = {
        "schemaVersion": OUTPUT_SCHEMA_VERSION,
        "contractVersion": CONTRACT_VERSION,
        "evaluationAt": envelope["evaluationAt"],
        "evaluationId": payload_sha256(identity_source),
        "summary": {
            "universeCount": len(rows),
            "top10Count": len(top10),
            "top3Count": len(top3),
            "candidateCount": len(candidate_rows),
            "readyCount": len(ready_rows),
            "paperBuyEligibleCount": len(eligible_rows),
            "top10UnderfilledReason": None if len(top10) == 10 else f"UNIVERSE_HAS_ONLY_{len(rows)}_ROWS",
            "top3UnderfilledReason": None if len(top3) == 3 else f"UNIVERSE_HAS_ONLY_{len(rows)}_ROWS",
            "lanes": {lane: _lane_summary(rows, lane) for lane in PERFORMANCE_LANES},
            "lanePerformanceSeparated": True,
            "combinedPerformanceAuthorized": False,
            "systemCanaryInvestmentPerformanceAuthorized": False,
            "systemHedgeCanaryInvestmentPerformanceAuthorized": False,
            "brokerMockPostCount": 0,
            "externalSystemCallCount": 0,
            "externalNetworkGetCount": 0,
            "externalNetworkPostCount": 0,
        },
        "universe": rows,
        "top10": top10,
        "top3": top3,
        "candidates": candidate_rows,
        "ready": ready_rows,
        "paperBuyEligible": eligible_rows,
        "authority": {
            "PAPER_INTERNAL_AUTO": True,
            "humanApprovalRequired": False,
            "userReceiptRequired": False,
            "allHardGatesRequired": True,
            "completedBarTriggerRequired": True,
            **copy.deepcopy(contract["permanent_false_authority"]),
        },
    }
    output["payloadSha256"] = payload_sha256(output)
    validate_output(output, contract)
    return output


def _validate_effective_output_row(row: object, rank: int, evaluation_at: dt.datetime, contract: dict) -> dict:
    expected_keys = {
        "market", "candidateId", "symbol", "candidateLane", "lane", "defensiveAction",
        "sourceTimestamp", "ttlSeconds", "expiresAt", "expired", "score", "scoreBreakdown",
        "completedBarTrigger", "hardGates", "risk", "sourceRefs", "rank", "funnelFlags",
        "highestStage", "reasons", "authority",
    }
    value = _require_exact_keys(row, expected_keys, f"output.universe[{rank - 1}]")
    if value["market"] not in MARKETS or value["candidateLane"] not in CANDIDATE_LANES or value["lane"] not in PERFORMANCE_LANES:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_ROW_SCOPE_INVALID:{rank}")
    if value["defensiveAction"] not in DEFENSIVE_ACTIONS:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_DEFENSIVE_ACTION_INVALID:{rank}")
    if (value["candidateLane"] == "PRIMARY_LONG") != (value["defensiveAction"] == "NONE"):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_DEFENSIVE_ACTION_LANE_MISMATCH:{rank}")
    hedge_lane = value["lane"] in {"SYSTEM_HEDGE_CANARY", "INVESTMENT_HEDGE_PAPER"}
    if hedge_lane != (value["candidateLane"] == "DEFENSIVE_ACTION"):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_PERFORMANCE_LANE_MISMATCH:{rank}")
    if any(not isinstance(value[key], str) or not value[key] for key in ("candidateId", "symbol")):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_ROW_IDENTITY_INVALID:{rank}")

    source_at = _parse_utc(value["sourceTimestamp"], f"output.universe[{rank - 1}].sourceTimestamp")
    ttl = value["ttlSeconds"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_TTL_INVALID:{rank}")
    expected_expiry = source_at + dt.timedelta(seconds=ttl)
    if value["expiresAt"] != _utc_text(expected_expiry) or value["expired"] is not (evaluation_at >= expected_expiry):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_EXPIRY_INVALID:{rank}")
    score_breakdown, score = _validate_score_breakdown(value["scoreBreakdown"], f"output.universe[{rank - 1}].scoreBreakdown")
    if score_breakdown != value["scoreBreakdown"] or value["score"] != score:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_SCORE_INVALID:{rank}")

    trigger = _require_exact_keys(
        value["completedBarTrigger"], {"status", "barId", "completedAt"}, f"output.universe[{rank - 1}].completedBarTrigger"
    )
    if trigger["status"] not in GATE_STATUSES:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_COMPLETED_BAR_STATUS_INVALID:{rank}")
    if trigger["barId"] is not None and (not isinstance(trigger["barId"], str) or not trigger["barId"]):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_COMPLETED_BAR_ID_INVALID:{rank}")
    if trigger["completedAt"] is not None and _parse_utc(trigger["completedAt"], f"output.completedAt[{rank}]") > evaluation_at:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_COMPLETED_BAR_FUTURE_DATED:{rank}")

    gates = value["hardGates"]
    if not isinstance(gates, dict) or tuple(gates) != HARD_GATES:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_HARD_GATES_SCHEMA_INVALID:{rank}")
    for gate_id in HARD_GATES:
        _validate_gate(gates[gate_id], f"output.universe[{rank - 1}].hardGates.{gate_id}")
    if (source_at > evaluation_at or evaluation_at >= expected_expiry) and gates["FRESHNESS"]["status"] != "FAIL":
        raise CommonPaperCandidateFunnelError(f"OUTPUT_FRESHNESS_FAIL_CLOSED_BYPASS:{rank}")
    completed_proven = trigger["status"] == "PASS" and trigger["barId"] is not None and trigger["completedAt"] is not None
    if not completed_proven and gates["COMPLETED_BAR"]["status"] == "PASS":
        raise CommonPaperCandidateFunnelError(f"OUTPUT_COMPLETED_BAR_FAIL_CLOSED_BYPASS:{rank}")

    risk = _require_exact_keys(
        value["risk"],
        {
            "currentOpenLongPositionCount", "currentOpenHedgePositionCount", "plannedLossNavFraction",
            "longMarketExposureNavFraction", "hedgeMarketExposureNavFraction", "exposureAccounting",
        },
        f"output.universe[{rank - 1}].risk",
    )
    long_count, hedge_count = risk["currentOpenLongPositionCount"], risk["currentOpenHedgePositionCount"]
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in (long_count, hedge_count)):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_POSITION_COUNT_INVALID:{rank}")
    planned_loss = _decimal(risk["plannedLossNavFraction"], f"output.plannedLoss[{rank}]")
    long_exposure = _decimal(risk["longMarketExposureNavFraction"], f"output.longExposure[{rank}]")
    hedge_exposure = _decimal(risk["hedgeMarketExposureNavFraction"], f"output.hedgeExposure[{rank}]")
    accounting = _require_exact_keys(
        risk["exposureAccounting"],
        {
            "longExposureBucketNavFraction", "hedgeExposureBucketNavFraction", "longPositionCount",
            "hedgePositionCount", "nettedMarketExposureNavFraction", "hedgeExcludedFromLongExposure",
            "hedgePositionExcludedFromLongPositionCount", "longHedgeCrossBucketNettingAuthorized",
        },
        f"output.universe[{rank - 1}].risk.exposureAccounting",
    )
    expected_accounting = {
        "longExposureBucketNavFraction": risk["longMarketExposureNavFraction"],
        "hedgeExposureBucketNavFraction": risk["hedgeMarketExposureNavFraction"],
        "longPositionCount": long_count,
        "hedgePositionCount": hedge_count,
        "nettedMarketExposureNavFraction": None,
        "hedgeExcludedFromLongExposure": True,
        "hedgePositionExcludedFromLongPositionCount": True,
        "longHedgeCrossBucketNettingAuthorized": False,
    }
    if accounting != expected_accounting:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_EXPOSURE_ACCOUNTING_INVALID:{rank}")
    policy = contract["risk"]
    position_count_pass = (
        hedge_count < policy["max_hedge_positions_per_market"]
        if value["candidateLane"] == "DEFENSIVE_ACTION"
        else long_count < policy["max_long_positions_per_market"]
    )
    local_risk_pass = (
        position_count_pass
        and planned_loss > 0
        and planned_loss <= Decimal(policy["max_planned_loss_nav_fraction_per_trade"])
        and long_exposure <= Decimal(policy["max_long_market_exposure_nav_fraction"])
        and hedge_exposure <= Decimal(policy["max_single_hedge_instrument_nav_fraction"])
        and (hedge_exposure == 0 or hedge_exposure <= long_exposure * Decimal(policy["max_hedge_to_long_beta_ratio"]))
    )
    if not local_risk_pass and gates["RISK_BUDGET"]["status"] == "PASS":
        raise CommonPaperCandidateFunnelError(f"OUTPUT_RISK_FAIL_CLOSED_BYPASS:{rank}")
    if not isinstance(value["sourceRefs"], list) or not value["sourceRefs"] or len(set(value["sourceRefs"])) != len(value["sourceRefs"]):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_SOURCE_REFS_INVALID:{rank}")
    if any(not isinstance(ref, str) or not ref for ref in value["sourceRefs"]):
        raise CommonPaperCandidateFunnelError(f"OUTPUT_SOURCE_REFS_INVALID:{rank}")

    base = copy.deepcopy(value)
    for key in ("rank", "funnelFlags", "highestStage", "reasons", "authority"):
        base.pop(key)
    expected = _finalize_row(base, rank, contract["funnel"]["top10_limit"], contract["funnel"]["top3_limit"], contract)
    if value != expected:
        raise CommonPaperCandidateFunnelError(f"OUTPUT_ROW_DERIVATION_MISMATCH:{rank}")
    return value


def validate_output(value: object, contract: dict | None = None) -> dict:
    """Independently revalidate output structure, hash, funnel and authority."""
    contract = contract or load_contract()
    expected = {
        "schemaVersion", "contractVersion", "evaluationAt", "evaluationId", "summary",
        "universe", "top10", "top3", "candidates", "ready", "paperBuyEligible",
        "authority", "payloadSha256",
    }
    packet = _require_exact_keys(value, expected, "output")
    if packet["schemaVersion"] != OUTPUT_SCHEMA_VERSION or packet["contractVersion"] != CONTRACT_VERSION:
        raise CommonPaperCandidateFunnelError("OUTPUT_VERSION_MISMATCH")
    _parse_utc(packet["evaluationAt"], "output.evaluationAt")
    for key in ("evaluationId", "payloadSha256"):
        if not isinstance(packet[key], str) or not _SHA_RE.fullmatch(packet[key]):
            raise CommonPaperCandidateFunnelError(f"OUTPUT_SHA_INVALID:{key}")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payloadSha256")
    if payload_sha256(unsigned) != packet["payloadSha256"]:
        raise CommonPaperCandidateFunnelError("OUTPUT_PAYLOAD_SHA_MISMATCH")
    if not isinstance(packet["universe"], list):
        raise CommonPaperCandidateFunnelError("OUTPUT_UNIVERSE_NOT_ARRAY")
    evaluation_at = _parse_utc(packet["evaluationAt"], "output.evaluationAt")
    rows = [_validate_effective_output_row(row, rank, evaluation_at, contract) for rank, row in enumerate(packet["universe"], 1)]
    market_order = {market: index for index, market in enumerate(MARKETS)}
    expected_order = sorted(rows, key=lambda row: (-row["score"], market_order[row["market"]], row["candidateId"], row["sourceTimestamp"]))
    if rows != expected_order:
        raise CommonPaperCandidateFunnelError("OUTPUT_ORDER_INVALID")
    duplicate_counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        identity = (row["market"], row["candidateId"], row["sourceTimestamp"])
        duplicate_counts[identity] = duplicate_counts.get(identity, 0) + 1
    for row in rows:
        identity = (row["market"], row["candidateId"], row["sourceTimestamp"])
        if duplicate_counts[identity] > 1 and row["hardGates"]["DUPLICATE_IDEMPOTENCY"]["status"] != "FAIL":
            raise CommonPaperCandidateFunnelError("OUTPUT_DUPLICATE_FAIL_CLOSED_BYPASS")
    if len(packet["top10"]) > 10 or len(packet["top3"]) > 3:
        raise CommonPaperCandidateFunnelError("OUTPUT_RANK_LIMIT_EXCEEDED")
    if packet["top10"] != packet["universe"][:10] or packet["top3"] != packet["universe"][:3]:
        raise CommonPaperCandidateFunnelError("OUTPUT_RANK_SLICE_MISMATCH")
    if packet["candidates"] != [row for row in packet["universe"] if row["funnelFlags"]["candidate"]]:
        raise CommonPaperCandidateFunnelError("OUTPUT_CANDIDATE_SLICE_MISMATCH")
    if packet["ready"] != [row for row in packet["universe"] if row["funnelFlags"]["ready"]]:
        raise CommonPaperCandidateFunnelError("OUTPUT_READY_SLICE_MISMATCH")
    if packet["paperBuyEligible"] != [row for row in packet["universe"] if row["funnelFlags"]["paperBuyEligible"]]:
        raise CommonPaperCandidateFunnelError("OUTPUT_ELIGIBLE_SLICE_MISMATCH")
    expected_summary = {
        "universeCount": len(rows),
        "top10Count": min(len(rows), 10),
        "top3Count": min(len(rows), 3),
        "candidateCount": sum(row["funnelFlags"]["candidate"] for row in rows),
        "readyCount": sum(row["funnelFlags"]["ready"] for row in rows),
        "paperBuyEligibleCount": sum(row["funnelFlags"]["paperBuyEligible"] for row in rows),
        "top10UnderfilledReason": None if len(rows) >= 10 else f"UNIVERSE_HAS_ONLY_{len(rows)}_ROWS",
        "top3UnderfilledReason": None if len(rows) >= 3 else f"UNIVERSE_HAS_ONLY_{len(rows)}_ROWS",
        "lanes": {lane: _lane_summary(rows, lane) for lane in PERFORMANCE_LANES},
        "lanePerformanceSeparated": True,
        "combinedPerformanceAuthorized": False,
        "systemCanaryInvestmentPerformanceAuthorized": False,
        "systemHedgeCanaryInvestmentPerformanceAuthorized": False,
        "brokerMockPostCount": 0,
        "externalSystemCallCount": 0,
        "externalNetworkGetCount": 0,
        "externalNetworkPostCount": 0,
    }
    if packet["summary"] != expected_summary:
        raise CommonPaperCandidateFunnelError("OUTPUT_SUMMARY_DERIVATION_MISMATCH")
    authority = packet["authority"]
    if authority.get("PAPER_INTERNAL_AUTO") is not True or authority.get("humanApprovalRequired") is not False or authority.get("userReceiptRequired") is not False:
        raise CommonPaperCandidateFunnelError("OUTPUT_INTERNAL_AUTHORITY_INVALID")
    for key in contract["permanent_false_authority"]:
        if authority.get(key) is not False:
            raise CommonPaperCandidateFunnelError(f"OUTPUT_PERMANENT_AUTHORITY_NOT_FALSE:{key}")
    if any(packet["summary"].get(key) != 0 for key in (
        "brokerMockPostCount", "externalSystemCallCount", "externalNetworkGetCount", "externalNetworkPostCount",
    )):
        raise CommonPaperCandidateFunnelError("OUTPUT_EXTERNAL_CALL_COUNT_NONZERO")
    return copy.deepcopy(packet)


def reduce_funnel_file(path: Path) -> dict:
    return reduce_funnel(_read_json(Path(path)))
