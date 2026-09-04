#!/usr/bin/env python3
"""Build a PAPER-only bridge from market mood to future capital allocation.

Regime answers how much total risk may eventually be taken.  Cross-market
flow and relative strength answer where that risk may eventually be placed.
This packet exposes the wiring and current evidence gap, but deliberately
leaves every percentage, action, order, and trading authority closed.

Transition and persistence are not invented here.  They are read back from the
exact, already-validated P2-COM-03 append-only ledger through that module's own
validator, so recorded history stops being discarded as ``UNKNOWN``.  The ledger
is the consumer of this packet, so the entries that record *this* packet are
excluded before anything is read; only history that predates this observation is
consumed, which keeps the rebuild deterministic and point-in-time honest.
Confirmation, confidence, and invalidation stay ``NOT_COMPUTABLE`` because no
ratified policy exists for them.
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

_TRANSITION_LEDGER = None


def transition_ledger_module():
    """Load P2-COM-03 lazily: it imports this module at its own module scope.

    Eager loading here would recurse forever.  Lazy loading terminates because
    the ledger only re-loads this producer -- it never calls back into
    ``build_reference`` while its own module body is executing.
    """
    global _TRANSITION_LEDGER
    if _TRANSITION_LEDGER is None:
        _TRANSITION_LEDGER = _load_module(
            "atlas_capital_flow_transition_ledger",
            ROOT / "portfolio" / "cross_market_flow_transition_ledger.py",
        )
    return _TRANSITION_LEDGER


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
        "comparison", "capital_model", "flow_candidate_semantics",
        "transition_ledger", "authority",
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
    if policy.get("transition_ledger") != {
        "contract_path": "config/cross_market_flow_transition_ledger_contract.json",
        "pointer_path": "data/latest_cross_market_flow_transition_ledger.json",
        "contract_version": "cross_market_flow_transition_ledger/2",
        "ledger_schema_version": "cross_market_flow_transition_ledger_packet/2",
        "predecessor_contract_version": "cross_market_flow_transition_ledger/1",
        "consumption": "EXACT_VALIDATED_RECORDED_HISTORY_ONLY",
        "self_observation_policy":
            "EXCLUDE_LEDGER_ENTRIES_OBSERVING_THIS_PACKET_GENERATED_AT",
        "counted_observation_modes": ["NATURAL"],
        "excluded_observation_modes": ["MANUAL", "RECOVERY", "REPLAY"],
        "confirmation_policy": "UNRATIFIED_CONFIRMED_AT_NULL",
        "invalidation_policy": "UNRATIFIED_NO_NUMERIC_THRESHOLD",
        "absent_pointer_policy":
            "UNKNOWN_UNLESS_A_RATIFIED_PREDECESSOR_CHAIN_EXISTS",
        "ratified_chain_pointer_missing_policy": "FAIL_CLOSED",
    }:
        fail("POLICY_TRANSITION_LEDGER_INVALID")
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


NO_PRIOR_HISTORY = "NO_PRIOR_RECORDED_HISTORY"
ABSENT_LEDGER_EVIDENCE = "NOT_COMPUTABLE_TRANSITION_LEDGER_ABSENT"
LEDGER_CONFIRMATION_STATUS = "NOT_COMPUTABLE_POLICY_UNRATIFIED"


def _ledger_call(code: str, function, *args, **kwargs):
    """Run a P2-COM-03 validator and re-raise its failure under this contract."""
    try:
        return function(*args, **kwargs)
    except CapitalFlowPostureReferenceError:
        raise
    except Exception as exc:
        raise CapitalFlowPostureReferenceError(f"{code}:{exc}") from exc


def _empty_ledger_evidence(spec: dict) -> dict:
    """Exactly what this packet may say when no prior history is consumable.

    The absent pointer, the contract-less tree, and a chain whose only
    observation is this packet all collapse to the same record on purpose: the
    consumable history is empty in every one of them, so the packet must not be
    able to tell them apart by its own bytes.
    """
    return {
        "source": {
            "source_type": "P2_COM_03_TRANSITION_LEDGER",
            "pointer_path": spec["pointer_path"],
            "contract_path": spec["contract_path"],
            "contract_sha256": None,
            "contract_version": None,
            "ledger_schema_version": None,
            "chain_status": NO_PRIOR_HISTORY,
            "predecessor_payload_sha256": None,
            "predecessor_height": None,
            "consumed_head_entry_sha256": None,
            "consumed_head_ledger_revision": None,
            "consumed_head_observed_at": None,
            "consumed_head_source_generated_date_kst": None,
        },
        "transition": {
            "status": "UNKNOWN",
            "source": "P2_COM_03_APPEND_ONLY_LEDGER",
            "evidence_status": NO_PRIOR_HISTORY,
            "recorded_type": None,
            "pending_type": None,
            "pending_type_status": ABSENT_LEDGER_EVIDENCE,
            "previous_semantic_state": None,
            "previous_semantic_state_sha256": None,
            "current_semantic_state_sha256": None,
            "state_matches_recorded_head": None,
        },
        "persistence": {
            "status": ABSENT_LEDGER_EVIDENCE,
            "confirmed_at": None,
            "confirmation_status": LEDGER_CONFIRMATION_STATUS,
            "confirmation_threshold": None,
            "first_seen": None,
            "observation_count": None,
            "natural_observation_count": None,
            "current_streak_observation_count": None,
            "current_streak_natural_count": None,
            "counts_current_packet": False,
            "counted_observation_modes": list(spec["counted_observation_modes"]),
            "excluded_observation_modes": list(spec["excluded_observation_modes"]),
        },
    }


def _consumable_history(ledger, spec: dict, contract: dict, pointer: dict,
                        predecessor: dict, generated_at: str,
                        root: Path, contract_path: Path) -> list:
    """Validate the pointer exactly, then drop this packet's own observations.

    P2-COM-03 consumes this packet, so a ledger read back after the append holds
    the very observation being described.  The ledger binds ``observed_at`` to
    the producer ``generated_at`` and refuses two entries on one
    source-generated KST date, so equality on ``generated_at`` selects exactly
    the entries that record this packet -- and nothing else.  Excluding them is
    what keeps the rebuild byte-identical across the append and keeps the packet
    from citing itself as its own prior evidence.
    """
    version = pointer.get("contract_version")
    if version == spec["predecessor_contract_version"]:
        frozen = _ledger_call(
            "TRANSITION_LEDGER_PREDECESSOR_INVALID",
            ledger.verify_predecessor_ledger,
            pointer,
        )
        if frozen["payload_sha256"] != predecessor["payload_sha256"]:
            fail("TRANSITION_LEDGER_PREDECESSOR_IDENTITY_MISMATCH")
        return []
    if version != spec["contract_version"] or version != contract["contract_version"]:
        fail("TRANSITION_LEDGER_CONTRACT_VERSION_UNSUPPORTED", str(version))
    validated = _ledger_call(
        "TRANSITION_LEDGER_VALIDATION_FAILED",
        ledger.validate_ledger,
        pointer,
        contract,
        root=root,
        contract_path=contract_path,
    )
    if validated.get("schema_version") != spec["ledger_schema_version"]:
        fail("TRANSITION_LEDGER_SCHEMA_VERSION_UNSUPPORTED")
    entries = validated["entries"]
    recorded = [item for item in entries if item["observed_at"] != generated_at]
    if len(recorded) != len(entries) and entries[: len(recorded)] != recorded:
        # a self observation can only ever be the append-only tail
        fail("TRANSITION_LEDGER_SELF_OBSERVATION_ORDER_INVALID")
    return recorded


def _transition_ledger_evidence(policy: dict, root: Path, core: dict) -> dict:
    """Read recorded P2-COM-03 history back into this packet, or stay UNKNOWN."""
    spec = policy["transition_ledger"]
    root = Path(root)
    contract_path = root / spec["contract_path"]
    pointer_path = root / spec["pointer_path"]
    generated_at = core["generated_at"]

    if not contract_path.is_file():
        # No ratified P2-COM-03 contract in this tree: nothing to consume, and
        # no ratified chain whose absence would be evidence of tampering.
        return _empty_ledger_evidence(spec)

    ledger = transition_ledger_module()
    contract = _ledger_call(
        "TRANSITION_LEDGER_CONTRACT_INVALID", ledger.load_contract, contract_path
    )
    ratified_chain = (root / contract["predecessor"]["evidence_path"]).is_file()
    if not pointer_path.is_file():
        if ratified_chain:
            # A ratified chain exists but its canonical pointer is gone.  That
            # is a recovery problem, never a licence to erase recorded history.
            fail("TRANSITION_LEDGER_POINTER_MISSING", spec["pointer_path"])
        return _empty_ledger_evidence(spec)

    pointer = read_json(pointer_path, "TRANSITION_LEDGER_READ_FAILED")
    predecessor = _ledger_call(
        "TRANSITION_LEDGER_PREDECESSOR_INVALID",
        ledger.load_predecessor,
        contract,
        root,
    )
    counted = [
        mode for mode in contract["observation_modes"]
        if contract["persistence_count_policy"][mode]
    ]
    uncounted = [
        mode for mode in contract["observation_modes"]
        if not contract["persistence_count_policy"][mode]
    ]
    if (
        counted != spec["counted_observation_modes"]
        or uncounted != spec["excluded_observation_modes"]
        or contract["confirmation_policy"] != spec["confirmation_policy"]
    ):
        fail("TRANSITION_LEDGER_PERSISTENCE_POLICY_MISMATCH")

    recorded = _consumable_history(
        ledger, spec, contract, pointer, predecessor, generated_at,
        root, contract_path,
    )
    tail = predecessor["tail"]
    if not recorded and tail["observed_at"] == generated_at:
        # the frozen chain's only visible observation is this packet itself
        if predecessor["height"] != 1:
            fail("TRANSITION_LEDGER_PREDECESSOR_SELF_OBSERVATION_UNRESOLVABLE")
        return _empty_ledger_evidence(spec)

    state = _ledger_call(
        "TRANSITION_LEDGER_STATE_PROJECTION_FAILED", ledger._current_state, core
    )
    semantic_sha = ledger.payload_sha256(ledger._semantic_state(state))
    head = recorded[-1] if recorded else tail
    head_same = head["current_semantic_state_sha256"] == semantic_sha
    head_persistence = head["persistence"]
    if head_persistence.get("confirmation_threshold") is not None:
        # no ratified confirmation policy exists, so no chain may carry one
        fail("TRANSITION_LEDGER_CONFIRMATION_THRESHOLD_UNRATIFIED")

    # exactly the ledger's own accounting for this state, minus the +1 that only
    # an actual P2-COM-03 append is allowed to add
    prior = predecessor["state_tally"].get(semantic_sha)
    matching = [
        item for item in recorded
        if item["current_semantic_state_sha256"] == semantic_sha
    ]
    if prior is not None:
        first_seen = prior["first_seen"]
    elif matching:
        first_seen = matching[0]["observed_at"]
    else:
        first_seen = None
    return {
        "source": {
            "source_type": "P2_COM_03_TRANSITION_LEDGER",
            "pointer_path": spec["pointer_path"],
            "contract_path": spec["contract_path"],
            "contract_sha256": file_sha256(contract_path),
            "contract_version": contract["contract_version"],
            "ledger_schema_version": contract["ledger_schema_version"],
            "chain_status": (
                "LEDGER_CONSUMED" if recorded else "PREDECESSOR_CHAIN_ONLY"
            ),
            "predecessor_payload_sha256": predecessor["payload_sha256"],
            "predecessor_height": predecessor["height"],
            "consumed_head_entry_sha256": head["entry_sha256"],
            "consumed_head_ledger_revision": (
                recorded[-1]["ledger_revision"] if recorded else predecessor["height"]
            ),
            "consumed_head_observed_at": head["observed_at"],
            "consumed_head_source_generated_date_kst": head[
                "source_generated_date_kst"
            ],
        },
        "transition": {
            "status": "RECORDED_HISTORY_OBSERVED",
            "source": "P2_COM_03_APPEND_ONLY_LEDGER",
            "evidence_status": (
                "LEDGER_CONSUMED" if recorded else "PREDECESSOR_CHAIN_ONLY"
            ),
            "recorded_type": (
                recorded[-1]["transition"]["type"] if recorded else None
            ),
            "pending_type": ledger._transition_type(head["current_state"], state),
            "pending_type_status": "DERIVED_NOT_YET_RECORDED_BY_P2_COM_03",
            "previous_semantic_state": ledger._semantic_state(head["current_state"]),
            "previous_semantic_state_sha256": head["current_semantic_state_sha256"],
            "current_semantic_state_sha256": semantic_sha,
            "state_matches_recorded_head": head_same,
        },
        "persistence": {
            "status": "RECORDED_OBSERVATION_COUNT_CONFIRMATION_UNRATIFIED",
            # the ledger pins confirmed_at to null until a confirmation policy
            # is ratified; it is copied from the head, never inferred here
            "confirmed_at": (
                recorded[-1]["confirmed_at"] if (recorded and head_same) else None
            ),
            "confirmation_status": LEDGER_CONFIRMATION_STATUS,
            "confirmation_threshold": None,
            "first_seen": first_seen,
            "observation_count": (
                (0 if prior is None else prior["observation_count_total"])
                + len(matching)
            ),
            "natural_observation_count": (
                (0 if prior is None else prior["natural_count_total"])
                + sum(item["observation_mode"] in counted for item in matching)
            ),
            "current_streak_observation_count": (
                head_persistence["current_streak_observation_count"] if head_same else 0
            ),
            "current_streak_natural_count": (
                head_persistence["current_streak_natural_count"] if head_same else 0
            ),
            "counts_current_packet": False,
            "counted_observation_modes": list(counted),
            "excluded_observation_modes": list(uncounted),
        },
    }


def _flow_candidates(flow: dict, policy: dict, ledger_evidence: dict) -> dict:
    """Relative strength stays a reference; recorded history stays recorded.

    ``actual_flow_claim`` is still ``UNKNOWN`` because a leader/laggard pair is
    not direct money flow.  What changes is that observed transition and
    persistence evidence from P2-COM-03 is carried through instead of being
    flattened back into ``UNKNOWN``.
    """
    semantics = policy["flow_candidate_semantics"]
    leader = flow["relative_strength_leader"]
    laggard = flow["relative_strength_laggard"]
    relative_pair_available = leader is not None and laggard is not None
    transition = copy.deepcopy(ledger_evidence["transition"])
    if transition["source"] != semantics["transition_source"]:
        fail("TRANSITION_SOURCE_IDENTITY_INVALID")
    persistence = copy.deepcopy(ledger_evidence["persistence"])
    if (
        persistence["confirmation_status"] != semantics["persistence_status"]
        or persistence["confirmed_at"] is not None
        or persistence["confirmation_threshold"] is not None
    ):
        fail("PERSISTENCE_CONFIRMATION_BOUNDARY_INVALID")
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
        "transition": transition,
        "persistence": persistence,
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

    core = {
        "status": "PARTIAL_REFERENCE_AVAILABLE" if comparable_count < 3 else "REFERENCE_AVAILABLE",
        "generated_at": source["generated_at"],
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
    }
    ledger_evidence = _transition_ledger_evidence(policy, root, core)

    sources = [{
        "source_type": "P1_PAPER_REGIME_REFERENCE_PACKET",
        "path": "data/latest_paper_regime_reference.json",
        "sha256": file_sha256(source_path),
        "schema_version": source["schema_version"],
        "contract_version": source["contract_version"],
        "payload_sha256": source["payload_sha256"],
        "generation_id": source["generation_id"],
    }, flow_contract_identity, ledger_evidence["source"]]
    generation_id = payload_sha256({
        "policy_sha256": file_sha256(policy_path),
        "sources": sources,
    })
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": policy["contract_version"],
        "mode": policy["mode"],
        "status": core["status"],
        "generated_at": core["generated_at"],
        "generation_id": generation_id,
        "policy": {
            "path": "config/capital_flow_posture_reference_policy_v1.json",
            "sha256": file_sha256(policy_path),
            "status": policy["status"],
            "capital_model": copy.deepcopy(policy["capital_model"]),
        },
        "sources": sources,
        "cross_market_flow": copy.deepcopy(core["cross_market_flow"]),
        "flow_candidates": None,
        "total_exposure_review": _total_exposure(source_markets),
        "market_allocation_reviews": reviews,
        "authority": copy.deepcopy(policy["authority"]),
    }
    packet["flow_candidates"] = _flow_candidates(
        packet["cross_market_flow"], policy, ledger_evidence
    )
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
