#!/usr/bin/env python3
"""Build a PAPER-only bridge from market mood to future capital allocation.

Regime answers how much total risk may eventually be taken.  Cross-market
flow and relative strength answer where that risk may eventually be placed.
This packet exposes the wiring and current evidence gap, but deliberately
leaves every percentage, action, order, and trading authority closed.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "capital_flow_posture_reference_policy_v1.json"
SOURCE_PATH = ROOT / "data" / "latest_paper_regime_reference.json"
LATEST_PATH = ROOT / "data" / "latest_capital_flow_posture_reference.json"
SCHEMA_VERSION = "capital_flow_posture_reference/v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CapitalFlowPostureReferenceError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise CapitalFlowPostureReferenceError(f"{code}:{detail}" if detail else code)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fail("SOURCE_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAPER_REGIME = _load_module(
    "atlas_capital_flow_paper_regime",
    ROOT / "regime" / "paper_regime_reference.py",
)
CROSS_ASSET_FLOW = _load_module(
    "atlas_capital_flow_cross_asset_contract",
    ROOT / "rotation" / "cross_asset_flow_evidence.py",
)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CapitalFlowPostureReferenceError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CapitalFlowPostureReferenceError(f"SOURCE_MISSING:{path}") from exc


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapitalFlowPostureReferenceError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def validate_policy(policy: object) -> dict:
    if not isinstance(policy, dict):
        fail("POLICY_INVALID")
    required = {
        "schema_version", "contract_version", "mode", "status",
        "source_contract_version", "cross_asset_flow_contract", "market_order",
        "comparison", "capital_model", "flow_candidate_semantics", "authority",
    }
    if set(policy) != required:
        fail("POLICY_FIELDS_MISMATCH")
    if type(policy.get("schema_version")) is not int or policy.get("schema_version") != 1 or policy.get("contract_version") != "capital_flow_posture_reference_policy/v1":
        fail("POLICY_VERSION_INVALID")
    if policy.get("mode") != "PAPER_DIAGNOSTIC_NOT_ALLOCATION":
        fail("POLICY_MODE_INVALID")
    if policy.get("status") != "CIO_INTENT_RECORDED_NUMERIC_BUDGET_UNRATIFIED":
        fail("POLICY_STATUS_INVALID")
    if policy.get("source_contract_version") != "paper_regime_reference_policy/v1":
        fail("POLICY_SOURCE_INVALID")
    if policy.get("cross_asset_flow_contract") != {
        "path": "config/cross_asset_flow_evidence_contract.json",
        "contract_version": "cross_asset_flow_evidence/1",
        "output_schema_version": "cross_asset_flow_evidence_packet/1",
        "required_cross_market_assessment_status": "UNKNOWN",
    }:
        fail("POLICY_CROSS_ASSET_FLOW_CONTRACT_INVALID")
    if policy.get("market_order") != ["US", "KR", "CRYPTO"]:
        fail("POLICY_MARKETS_INVALID")
    if policy.get("comparison") != {
        "minimum_comparable_markets": 2,
        "require_same_as_of_date": True,
        "score_basis": "EQUAL_WEIGHT_FIVE_AXIS_SUM_MINUS5_TO_PLUS5",
        "actual_flow_inference": "FORBIDDEN_WITHOUT_COMPARABLE_DIRECT_FLOW_EVIDENCE",
    }:
        fail("POLICY_COMPARISON_INVALID")
    if policy.get("capital_model") != {
        "total_exposure_driver": "REGIME",
        "within_envelope_allocation_driver": "CROSS_MARKET_FLOW_AND_RELATIVE_STRENGTH",
        "cash_role": "RESIDUAL_AND_DEFENSIVE_ASSET",
        "numeric_budget_status": "UNRATIFIED_REPLAY_REQUIRED",
        "missing_market_policy": "WAIT_NO_NUMERIC_TARGET",
    }:
        fail("POLICY_CAPITAL_MODEL_INVALID")
    if policy.get("flow_candidate_semantics") != {
        "basis": "SAME_DATE_RELATIVE_STRENGTH_REFERENCE_ONLY",
        "receiver_label": "RELATIVE_ATTRACTOR",
        "donor_label": "RELATIVE_DONOR",
        "actual_flow_claim": "FORBIDDEN",
        "transition_source": "P2_COM_03_APPEND_ONLY_LEDGER",
        "confidence_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        "persistence_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        "invalidation_status": "NOT_COMPUTABLE_POLICY_UNRATIFIED",
    }:
        fail("POLICY_FLOW_CANDIDATE_SEMANTICS_INVALID")
    authority = policy.get("authority")
    expected_authority = {
        "paper_reference_display_authorized": True,
        "relative_strength_comparison_authorized": True,
        "actual_flow_claim_authorized": False,
        "gross_exposure_authorized": False,
        "cash_target_authorized": False,
        "cross_market_allocation_authorized": False,
        "position_size_authorized": False,
        "stage_authorized": False,
        "buy_authorized": False,
        "action_authorized": False,
        "order_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if not isinstance(authority, dict) or set(authority) != set(expected_authority):
        fail("POLICY_AUTHORITY_INVALID")
    if any(type(value) is not bool for value in authority.values()) or authority != expected_authority:
        fail("POLICY_AUTHORITY_INVALID")
    return copy.deepcopy(policy)


def _cross_asset_flow_contract_identity(
    policy: dict,
    root: Path,
    contract_path: Path | None = None,
) -> dict:
    spec = policy["cross_asset_flow_contract"]
    path = root / spec["path"] if contract_path is None else Path(contract_path)
    # Older downstream unit fixtures copied only the producer's direct policy
    # and P1 packet.  They may use the immutable repository P2-COM-01 contract,
    # but production (root == ROOT) never falls back when a dependency is absent.
    if contract_path is None and root != ROOT and not path.is_file():
        path = ROOT / spec["path"]
    try:
        contract = CROSS_ASSET_FLOW.load_contract(path)
    except Exception as exc:
        raise CapitalFlowPostureReferenceError(
            f"CROSS_ASSET_FLOW_CONTRACT_REVALIDATION_FAILED:{exc}"
        ) from exc
    if (
        contract.get("contract_version") != spec["contract_version"]
        or contract.get("output_schema_version") != spec["output_schema_version"]
        or contract.get("explicit_unknowns") != [{
            "evidence_class": "MARKET_IMPLIED_FLOW",
            "market": "COMMON",
            "subject": "CROSS_MARKET_RELATIVE_FLOW",
            "reason": "COMPARABLE_MULTI_DATE_MARKET_SERIES_NOT_AVAILABLE",
        }]
        or contract.get("authority", {}).get("cross_market_flow_claim_authorized") is not False
    ):
        fail("CROSS_ASSET_FLOW_CONTRACT_IDENTITY_INVALID")
    return {
        "source_type": "P2_COM_01_CROSS_ASSET_FLOW_CONTRACT",
        "path": spec["path"],
        "sha256": file_sha256(path),
        "schema_version": contract["schema_version"],
        "contract_version": contract["contract_version"],
        "output_schema_version": contract["output_schema_version"],
        "cross_market_assessment_status": spec[
            "required_cross_market_assessment_status"
        ],
        "cross_market_flow_claim_authorized": False,
    }


def _flow_candidates(flow: dict, policy: dict) -> dict:
    semantics = policy["flow_candidate_semantics"]
    leader = flow["relative_strength_leader"]
    laggard = flow["relative_strength_laggard"]
    relative_pair_available = leader is not None and laggard is not None
    return {
        "basis": semantics["basis"],
        "evidence_class": (
            "RELATIVE_STRENGTH_REFERENCE" if relative_pair_available else "UNKNOWN"
        ),
        "receiver_candidate": {
            "market": leader,
            "classification": (
                semantics["receiver_label"] if leader is not None else "UNKNOWN"
            ),
        },
        "donor_candidate": {
            "market": laggard,
            "classification": (
                semantics["donor_label"] if laggard is not None else "UNKNOWN"
            ),
        },
        "actual_flow_claim": "UNKNOWN",
        "actual_flow_claim_reason": flow["actual_money_flow_reason"],
        "confidence": None,
        "confidence_status": semantics["confidence_status"],
        "transition": {
            "status": "UNKNOWN",
            "source": semantics["transition_source"],
        },
        "persistence": {
            "status": semantics["persistence_status"],
            "confirmed_at": None,
            "observation_count": None,
        },
        "invalidation": {
            "status": semantics["invalidation_status"],
            "reason": "NO_RATIFIED_CROSS_MARKET_FLOW_INVALIDATION_POLICY",
        },
    }


def _same_date_group(markets: list[dict]) -> tuple[str | None, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in markets:
        score = row["paper_reference"]["score"]
        as_of = row["as_of_date"]
        if isinstance(score, int) and isinstance(as_of, str):
            groups.setdefault(as_of, []).append(row)
    eligible = [
        (date, rows) for date, rows in groups.items()
        if len(rows) >= 2
    ]
    if not eligible:
        return None, []
    return sorted(eligible, key=lambda item: (len(item[1]), item[0]))[-1]


def _market_reviews(markets: list[dict], comparable: list[dict]) -> list[dict]:
    comparable_ids = {row["market"] for row in comparable}
    scores = {row["market"]: row["paper_reference"]["score"] for row in comparable}
    maximum = max(scores.values()) if scores else None
    minimum = min(scores.values()) if scores else None
    unique_max = maximum is not None and list(scores.values()).count(maximum) == 1
    unique_min = minimum is not None and list(scores.values()).count(minimum) == 1
    result = []
    for row in markets:
        market = row["market"]
        score = row["paper_reference"]["score"]
        if market not in comparable_ids:
            if row.get("classification_status") == "WAIT_MARKET_NORMALIZATION_POLICY":
                review = "WAIT_FOR_CLASSIFICATION_POLICY"
                explanation = "신호 5개는 모두 확인됐지만 코인 전용 판정 규칙 검증이 끝날 때까지 비중 우선순위를 정하지 않습니다."
            else:
                review = "WAIT_FOR_COMPLETE_REGIME"
                explanation = "공식 시장 신호 5개가 모두 확인될 때까지 비중 우선순위를 정하지 않습니다."
        elif unique_max and score == maximum and maximum != minimum:
            review = "RELATIVE_STRENGTH_LEADER_REFERENCE"
            explanation = "같은 날짜에 비교 가능한 시장 중 점수가 가장 높습니다. 실제 자금 유입을 뜻하지는 않습니다."
        elif unique_min and score == minimum and maximum != minimum:
            review = "RELATIVE_STRENGTH_LAGGARD_REFERENCE"
            explanation = "같은 날짜에 비교 가능한 시장 중 점수가 가장 낮습니다. 실제 자금 유출을 뜻하지는 않습니다."
        else:
            review = "MIXED_REFERENCE"
            explanation = "같은 날짜의 시장 점수가 같거나 우열이 뚜렷하지 않습니다."
        result.append({
            "market": market,
            "as_of_date": row["as_of_date"],
            "candidate_regime": row["paper_reference"]["candidate_regime"],
            "regime_score": score,
            "review_priority": review,
            "target_weight_pct": None,
            "explanation_ko": explanation,
        })
    return result


def _total_exposure(markets: list[dict]) -> dict:
    regimes = [row["paper_reference"]["candidate_regime"] for row in markets]
    complete = all(value != "UNKNOWN" for value in regimes)
    if "STRESS" in regimes:
        review = "DEFENSIVE_REVIEW"
        reason = "한 시장에서 불안 경보가 확인돼 전체 위험 노출을 낮추는 검토가 우선입니다."
    elif not complete and any(
        row.get("classification_status") == "WAIT_MARKET_NORMALIZATION_POLICY"
        for row in markets
    ):
        review = "WAIT_CLASSIFICATION_POLICY"
        reason = "세 시장의 입력은 확인됐지만 코인 판정 규칙 검증이 남아 있어 전체 투자비중 숫자를 만들지 않습니다."
    elif not complete:
        review = "WAIT_INCOMPLETE_MARKET_SET"
        reason = "세 시장 중 판정이 끝나지 않은 곳이 있어 전체 투자비중 숫자를 만들지 않습니다."
    elif regimes.count("RISK_ON") >= 2:
        review = "EXPANSION_REVIEW"
        reason = "세 시장 중 둘 이상이 위험 선호여서 전체 투자비중 확대 검토가 가능합니다."
    elif regimes.count("RISK_OFF") >= 2:
        review = "DEFENSIVE_REVIEW"
        reason = "세 시장 중 둘 이상이 위험 회피여서 전체 투자비중 축소 검토가 우선입니다."
    else:
        review = "HOLD_REVIEW"
        reason = "시장 분위기가 엇갈려 현재 전체 투자비중을 유지하는 검토가 우선입니다."
    return {
        "input_state": "COMPLETE" if complete else "INCOMPLETE",
        "review": review,
        "invested_target_pct": None,
        "cash_target_pct": None,
        "numeric_budget_status": "UNRATIFIED_REPLAY_REQUIRED",
        "explanation_ko": reason,
    }


def build_reference(root: Path = ROOT) -> dict:
    policy_path = root / "config" / "capital_flow_posture_reference_policy_v1.json"
    source_path = root / "data" / "latest_paper_regime_reference.json"
    policy = validate_policy(read_json(policy_path, "POLICY_INVALID"))
    flow_contract_identity = _cross_asset_flow_contract_identity(policy, root)
    source = read_json(source_path, "SOURCE_INVALID")
    try:
        PAPER_REGIME.validate_reference(source, root)
    except Exception as exc:
        raise CapitalFlowPostureReferenceError(f"SOURCE_REVALIDATION_FAILED:{exc}") from exc
    if source.get("contract_version") != policy["source_contract_version"]:
        fail("SOURCE_CONTRACT_INVALID")

    source_markets = source["markets"]
    comparison_date, comparable = _same_date_group(source_markets)
    reviews = _market_reviews(source_markets, comparable)
    leaders = [row["market"] for row in reviews if row["review_priority"] == "RELATIVE_STRENGTH_LEADER_REFERENCE"]
    laggards = [row["market"] for row in reviews if row["review_priority"] == "RELATIVE_STRENGTH_LAGGARD_REFERENCE"]
    comparable_count = len(comparable)
    if comparable_count == 3:
        comparison_status = "THREE_MARKET_RELATIVE_STRENGTH_REFERENCE"
        comparison_reason = "세 시장을 같은 날짜의 5축 점수로 비교했습니다. 이는 실제 자금 이동 증거가 아니라 상대 강도 참고입니다."
    elif comparable_count >= 2:
        comparison_status = "PARTIAL_RELATIVE_STRENGTH_REFERENCE"
        comparison_reason = "같은 날짜의 일부 시장만 비교할 수 있어 상대 강도만 표시하고 시장 간 자금 이동은 확정하지 않습니다."
    else:
        comparison_status = "UNKNOWN"
        comparison_reason = "같은 날짜에 비교 가능한 시장이 둘보다 적어 상대 강도도 계산하지 않습니다."

    sources = [{
        "source_type": "P1_PAPER_REGIME_REFERENCE_PACKET",
        "path": "data/latest_paper_regime_reference.json",
        "sha256": file_sha256(source_path),
        "schema_version": source["schema_version"],
        "contract_version": source["contract_version"],
        "payload_sha256": source["payload_sha256"],
        "generation_id": source["generation_id"],
    }, flow_contract_identity]
    generation_id = payload_sha256({
        "policy_sha256": file_sha256(policy_path),
        "sources": sources,
    })
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": policy["contract_version"],
        "mode": policy["mode"],
        "status": "PARTIAL_REFERENCE_AVAILABLE" if comparable_count < 3 else "REFERENCE_AVAILABLE",
        "generated_at": source["generated_at"],
        "generation_id": generation_id,
        "policy": {
            "path": "config/capital_flow_posture_reference_policy_v1.json",
            "sha256": file_sha256(policy_path),
            "status": policy["status"],
            "capital_model": copy.deepcopy(policy["capital_model"]),
        },
        "sources": sources,
        "cross_market_flow": {
            "actual_money_flow": "UNKNOWN",
            "actual_money_flow_reason": "COMPARABLE_DIRECT_DONOR_RECEIVER_EVIDENCE_NOT_AVAILABLE",
            "comparison_status": comparison_status,
            "comparison_as_of_date": comparison_date,
            "comparable_market_count": comparable_count,
            "required_market_count": 3,
            "relative_strength_leader": leaders[0] if len(leaders) == 1 else None,
            "relative_strength_laggard": laggards[0] if len(laggards) == 1 else None,
            "explanation_ko": comparison_reason,
        },
        "flow_candidates": None,
        "total_exposure_review": _total_exposure(source_markets),
        "market_allocation_reviews": reviews,
        "authority": copy.deepcopy(policy["authority"]),
    }
    packet["flow_candidates"] = _flow_candidates(packet["cross_market_flow"], policy)
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_reference(packet: dict, root: Path = ROOT) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        fail("REFERENCE_SCHEMA_INVALID")
    unsigned = copy.deepcopy(packet)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or SHA256.fullmatch(claimed) is None or payload_sha256(unsigned) != claimed:
        fail("REFERENCE_SHA_INVALID")
    expected = build_reference(root)
    if packet != expected:
        fail("REFERENCE_REDERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def write_packet(packet: dict, root: Path = ROOT) -> tuple[Path, Path]:
    comparison_date = packet["cross_market_flow"]["comparison_as_of_date"]
    evidence_date = comparison_date or packet["generated_at"][:10]
    evidence = root / "evidence" / "portfolio" / "capital_flow_posture_reference" / evidence_date / packet["generation_id"] / "packet.json"
    latest = root / "data" / "latest_capital_flow_posture_reference.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if evidence.exists() and evidence.read_text(encoding="utf-8") != text:
        fail("APPEND_ONLY_EVIDENCE_CONFLICT")
    evidence.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return evidence, latest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        validate_reference(read_json(args.verify, "REFERENCE_INVALID"))
        print("PASS_CAPITAL_FLOW_POSTURE_REFERENCE_VERIFIED")
        return 0
    packet = build_reference()
    if args.write:
        evidence, latest = write_packet(packet)
        print(json.dumps({
            "status": packet["status"],
            "evidence": str(evidence.relative_to(ROOT)),
            "latest": str(latest.relative_to(ROOT)),
            "generation_id": packet["generation_id"],
        }, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
