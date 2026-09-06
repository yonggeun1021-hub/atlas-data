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
import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "capital_flow_posture_reference_policy_v1.json"
SOURCE_PATH = ROOT / "data" / "latest_paper_regime_reference.json"
LATEST_PATH = ROOT / "data" / "latest_capital_flow_posture_reference.json"
SCHEMA_VERSION = "capital_flow_posture_reference/v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")

# --------------------------------------------------------------------------
# The exact ten repository inputs this reference is derived from.
#
# These are the ONLY paths a frozen replay envelope may name.  An envelope
# cannot name an extra path, an absolute path, a traversal, a repository, a
# ref, a URL or a validation HEAD: the trusted repository root and validation
# HEAD stay caller-supplied API arguments, exactly as build_reference(root)
# already takes one.  Order is the contract's order and is preserved so the
# tables in the contract and the code read the same way.
# --------------------------------------------------------------------------
FLOW_POLICY_REL = "config/capital_flow_posture_reference_policy_v1.json"
PAPER_REGIME_PACKET_REL = "data/latest_paper_regime_reference.json"
CROSS_ASSET_CONTRACT_REL = "config/cross_asset_flow_evidence_contract.json"
PAPER_REGIME_POLICY_REL = "config/paper_regime_reference_policy_v1.json"
FREE_MARKET_DATA_REL = "data/latest_free_market_data.json"
KOREA_MARKET_SIGNALS_REL = "data/latest_korea_market_signals.json"
CRYPTO_REFRESH_STATUS_REL = "data/latest_crypto_regime_refresh_status.json"
TRANSITION_LEDGER_CONTRACT_REL = (
    "config/cross_market_flow_transition_ledger_contract.json"
)
TRANSITION_LEDGER_PREDECESSOR_REL = (
    "evidence/portfolio/cross_market_flow_transition_ledger/2026-09-02/"
    "58f34d06c92d66d96d64a0deb0261462aaae06a4ac99da7c43d4d2cfc35161cf/packet.json"
)
TRANSITION_LEDGER_POINTER_REL = "data/latest_cross_market_flow_transition_ledger.json"

FLOW_REPLAY_INPUT_PATHS = (
    FLOW_POLICY_REL,
    PAPER_REGIME_PACKET_REL,
    CROSS_ASSET_CONTRACT_REL,
    PAPER_REGIME_POLICY_REL,
    FREE_MARKET_DATA_REL,
    KOREA_MARKET_SIGNALS_REL,
    CRYPTO_REFRESH_STATUS_REL,
    TRANSITION_LEDGER_CONTRACT_REL,
    TRANSITION_LEDGER_PREDECESSOR_REL,
    TRANSITION_LEDGER_POINTER_REL,
)
# Inputs 1-7.  A proven-absent one of these means this Flow packet cannot be
# rebuilt at all -- it is a closure failure, never a "normal empty" result.
FLOW_REPLAY_REQUIRED_INPUT_PATHS = FLOW_REPLAY_INPUT_PATHS[:7]

FLOW_REPLAY_SCHEMA_VERSION = "capital_flow_replay_inputs/1"
FLOW_REPLAY_ENVELOPE_KEYS = frozenset({"schema_version", "source_commit", "files"})
FLOW_REPLAY_FILE_KEYS = frozenset({"state", "blob_oid", "sha256"})
FLOW_REPLAY_STATES = ("PRESENT", "ABSENT")
# Git SHA-1 object ids, lowercase and unabbreviated.  An abbreviated,
# uppercase or SHA-256 oid is rejected rather than resolved.
GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_BLOB_MODES = ("100644", "100755")


class CapitalFlowPostureReferenceError(ValueError):
    pass


class FlowReplayProvenanceError(CapitalFlowPostureReferenceError):
    """The frozen input tuple could not be PROVEN against real Git objects.

    Always hard.  Never downgraded to a historical diagnostic, an empty
    ledger state, a degraded row or a live re-read: an unprovable input is a
    different fact from a proven-but-semantically-invalid one.
    """


class UnreplayableFlowHistoryError(CapitalFlowPostureReferenceError):
    """Provenance held, but the proven closure cannot rebuild this Flow packet.

    Raised only after every one of the ten entries has been authenticated, so
    it always reports a real repository fact (a genuinely absent required
    input, or a genuinely inconsistent optional-ledger combination) and never
    a failure to verify.  It is an exception, not a passing row.
    """


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


# ---------------------------------------------------------------------------
# Frozen Flow replay inputs
#
# build_reference() above reads whatever is on disk right now.  That is correct
# for a fresh build and wrong for a replay: a later market-pointer or ledger
# move silently rewrites what a past briefing's Flow section says.  The
# functions below freeze the exact ten inputs a build was derived from as an
# envelope of Git object identities, and rebuild from THOSE bytes.
#
# The envelope carries digests and a source commit, never content.  Bytes are
# re-read from real local Git objects at replay time, so a packet cannot hand
# the validator the content it wants validated.  The trusted repository root
# and validation HEAD are caller context, never read from the envelope, the
# packet or a locator, and no git command here ever contacts a remote.
# ---------------------------------------------------------------------------


def _provenance(code: str, detail: str = "") -> FlowReplayProvenanceError:
    return FlowReplayProvenanceError(f"{code}:{detail}" if detail else code)


def _require(condition, code: str, detail: str = "") -> None:
    if not condition:
        raise _provenance(code, detail)


def _git(root: Path, *args: str, binary: bool = False):
    # Local replace refs must never rewrite the authenticated object graph.
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", *args],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise _provenance("FLOW_REPLAY_GIT_PROVENANCE_UNVERIFIED", " ".join(args)) from exc
    return completed.stdout if binary else completed.stdout.decode("utf-8")


def _git_result(root: Path, *args: str):
    """Run git WITHOUT raising on non-zero, returning ``(code, stdout)``.

    Used only where a specific non-zero exit is itself an answer -- "this
    repository has never held that object", "that commit is not an ancestor"
    -- so each of those gets its own diagnostic instead of collapsing into the
    generic unverified code.  No command here can trigger a network fetch of
    whatever an envelope happens to name.
    """
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", *args],
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise _provenance("FLOW_REPLAY_GIT_PROVENANCE_UNVERIFIED", " ".join(args)) from exc
    return completed.returncode, completed.stdout.decode("utf-8")


def _git_blob_oid(data: bytes) -> str:
    """The Git object id of ``data`` as a loose blob, computed here rather than
    asked of git, so stored bytes are checked against a stored oid
    independently of whatever the repository would answer."""
    return hashlib.sha1(f"blob {len(data)}".encode("ascii") + b"\0" + data).hexdigest()


def _require_repository_boundary(root: Path) -> None:
    """Prove ``root`` is the top of a real Git work tree before any lookup."""
    code, out = _git_result(root, "rev-parse", "--show-toplevel")
    _require(code == 0, "FLOW_REPLAY_REPOSITORY_BOUNDARY_UNVERIFIED", str(root))
    top = out.strip()
    _require(bool(top), "FLOW_REPLAY_REPOSITORY_BOUNDARY_UNVERIFIED", str(root))
    _require(
        Path(top).resolve() == root,
        "FLOW_REPLAY_REPOSITORY_BOUNDARY_MISMATCH",
        f"{top}!={root}",
    )


def _resolved_validation_head(root: Path, trusted_validation_head) -> str:
    """The trusted validation HEAD: the caller's, or this repository's own.

    Never taken from the envelope.  A caller-supplied value is resolved as a
    real commit object in the trusted repository, so naming a ref that does
    not exist fails rather than silently falling back to HEAD.
    """
    if trusted_validation_head is None:
        code, out = _git_result(root, "rev-parse", "HEAD")
        _require(code == 0, "FLOW_REPLAY_TRUSTED_VALIDATION_HEAD_UNRESOLVED", str(root))
        head = out.strip()
    else:
        _require(
            isinstance(trusted_validation_head, str) and trusted_validation_head,
            "FLOW_REPLAY_TRUSTED_VALIDATION_HEAD_INVALID",
            repr(trusted_validation_head),
        )
        code, out = _git_result(
            root, "rev-parse", "--verify", "--quiet",
            f"{trusted_validation_head}^{{commit}}",
        )
        _require(
            code == 0,
            "FLOW_REPLAY_TRUSTED_VALIDATION_HEAD_UNRESOLVED",
            trusted_validation_head,
        )
        head = out.strip()
    _require(
        GIT_OID_RE.fullmatch(head) is not None,
        "FLOW_REPLAY_TRUSTED_VALIDATION_HEAD_INVALID",
        head,
    )
    return head


def _tree_entry(root: Path, commit: str, relative: str):
    """``(mode, type, oid)`` for ``relative`` in ``commit``'s tree, or None when
    the tree genuinely does not contain that path.

    A failed ``cat-file`` is never read as absence: only this real tree lookup
    can prove a path was not committed.
    """
    code, out = _git_result(
        root, "ls-tree", "-z", "--full-tree", f"{commit}^{{tree}}", "--", relative
    )
    _require(code == 0, "FLOW_REPLAY_TREE_UNREADABLE", relative)
    records = [record for record in out.split("\0") if record]
    if not records:
        return None
    _require(len(records) == 1, "FLOW_REPLAY_TREE_ENTRY_AMBIGUOUS", relative)
    meta, separator, path = records[0].partition("\t")
    _require(
        separator == "\t" and path == relative,
        "FLOW_REPLAY_TREE_PATH_MISMATCH",
        relative,
    )
    fields = meta.split()
    _require(len(fields) == 3, "FLOW_REPLAY_TREE_ENTRY_INVALID", relative)
    mode, object_type, oid = fields
    _require(GIT_OID_RE.fullmatch(oid) is not None, "FLOW_REPLAY_TREE_OID_INVALID", relative)
    return mode, object_type, oid


def _checked_relative_path(relative: str) -> str:
    """One of the exact ten paths, spelled exactly.  Nothing else resolves."""
    _require(
        relative in FLOW_REPLAY_INPUT_PATHS,
        "FLOW_REPLAY_PATH_NOT_ALLOWED",
        str(relative),
    )
    return relative


def _authenticated_bytes(root: Path, relative: str, oid: str) -> bytes:
    """Raw committed bytes for ``oid``, re-checked against the id itself."""
    data = _git(root, "cat-file", "blob", oid, binary=True)
    _require(_git_blob_oid(data) == oid, "FLOW_REPLAY_BLOB_HASH_MISMATCH", relative)
    return data


def _verify_flow_replay_file(root: Path, commit: str, relative: str, entry):
    """Authenticated raw bytes for ``relative``, or None when the commit tree
    proves the path was genuinely absent at that commit."""
    _require(
        isinstance(entry, dict) and set(entry) == FLOW_REPLAY_FILE_KEYS,
        "FLOW_REPLAY_FILE_FIELDS_MISMATCH",
        relative,
    )
    state = entry["state"]
    blob_oid = entry["blob_oid"]
    sha256 = entry["sha256"]
    _require(state in FLOW_REPLAY_STATES, "FLOW_REPLAY_STATE_INVALID", relative)
    tree = _tree_entry(root, commit, relative)
    if state == "ABSENT":
        # An ABSENT tag cannot hide a committed entry of ANY kind -- blob,
        # tree, symlink or submodule.
        _require(tree is None, "FLOW_REPLAY_ABSENT_HIDES_COMMITTED_ENTRY", relative)
        _require(
            blob_oid is None and sha256 is None,
            "FLOW_REPLAY_ABSENT_FIELDS_INVALID",
            relative,
        )
        return None
    _require(tree is not None, "FLOW_REPLAY_PRESENT_NOT_IN_COMMIT_TREE", relative)
    mode, object_type, tree_oid = tree
    # A symlink (120000), a submodule gitlink (160000) or a tree is not a
    # regular file and is refused rather than dereferenced.
    _require(
        object_type == "blob" and mode in GIT_BLOB_MODES,
        "FLOW_REPLAY_BLOB_MODE_INVALID",
        f"{relative}:{mode}:{object_type}",
    )
    _require(
        isinstance(blob_oid, str) and GIT_OID_RE.fullmatch(blob_oid) is not None,
        "FLOW_REPLAY_BLOB_OID_INVALID",
        relative,
    )
    _require(blob_oid == tree_oid, "FLOW_REPLAY_BLOB_OID_MISMATCH", relative)
    _require(
        isinstance(sha256, str) and SHA256.fullmatch(sha256) is not None,
        "FLOW_REPLAY_SHA256_INVALID",
        relative,
    )
    data = _authenticated_bytes(root, relative, tree_oid)
    _require(
        hashlib.sha256(data).hexdigest() == sha256,
        "FLOW_REPLAY_SHA256_MISMATCH",
        relative,
    )
    return data


def _require_trusted_ancestor(root: Path, commit: str, trusted_head: str) -> None:
    # Object existence first, so "this repository has never held that object"
    # is reported as itself instead of as a failed ancestry test.
    code, resolved = _git_result(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    _require(code == 0, "FLOW_REPLAY_SOURCE_COMMIT_OBJECT_MISSING", commit)
    _require(resolved.strip() == commit, "FLOW_REPLAY_SOURCE_COMMIT_INVALID", commit)
    code, _ = _git_result(root, "merge-base", "--is-ancestor", commit, trusted_head)
    if code == 1:
        raise _provenance("FLOW_REPLAY_SOURCE_COMMIT_NOT_TRUSTED_ANCESTOR", commit)
    _require(code == 0, "FLOW_REPLAY_GIT_PROVENANCE_UNVERIFIED", "merge-base")


def _verify_flow_replay_inputs(
    envelope, trusted_repository_root: Path, trusted_validation_head
) -> dict:
    _require(
        isinstance(envelope, dict),
        "FLOW_REPLAY_ENVELOPE_INVALID",
        type(envelope).__name__,
    )
    _require(
        set(envelope) == FLOW_REPLAY_ENVELOPE_KEYS,
        "FLOW_REPLAY_ENVELOPE_FIELDS_MISMATCH",
        str(sorted(envelope)),
    )
    _require(
        envelope["schema_version"] == FLOW_REPLAY_SCHEMA_VERSION,
        "FLOW_REPLAY_SCHEMA_VERSION_INVALID",
        repr(envelope["schema_version"]),
    )
    commit = envelope["source_commit"]
    _require(
        isinstance(commit, str) and GIT_OID_RE.fullmatch(commit) is not None,
        "FLOW_REPLAY_SOURCE_COMMIT_INVALID",
        repr(commit),
    )
    files = envelope["files"]
    _require(
        isinstance(files, dict) and set(files) == set(FLOW_REPLAY_INPUT_PATHS),
        "FLOW_REPLAY_FILE_KEYS_MISMATCH",
        str(sorted(files)) if isinstance(files, dict) else type(files).__name__,
    )

    root = Path(trusted_repository_root).resolve()
    _require_repository_boundary(root)
    trusted_head = _resolved_validation_head(root, trusted_validation_head)
    _require_trusted_ancestor(root, commit, trusted_head)
    return {
        relative: _verify_flow_replay_file(root, commit, relative, files[relative])
        for relative in FLOW_REPLAY_INPUT_PATHS
    }


def verify_flow_replay_inputs(
    envelope,
    *,
    trusted_repository_root: Path = ROOT,
    trusted_validation_head=None,
) -> dict:
    """Authenticate one envelope and return ``{relative: bytes or None}``.

    Every failure is hard.  The blanket re-raise is deliberate: an unexpected
    error while PROVING provenance must never become indistinguishable from a
    proven-but-semantically-invalid input.
    """
    try:
        return _verify_flow_replay_inputs(
            envelope, trusted_repository_root, trusted_validation_head
        )
    except FlowReplayProvenanceError:
        raise
    except Exception as exc:  # noqa: BLE001 - unprovable input is a hard failure
        raise _provenance(
            "FLOW_REPLAY_PROVENANCE_UNVERIFIED", f"{type(exc).__name__}:{exc}"
        ) from exc


def _capture_flow_replay_file(root: Path, head: str, relative: str) -> dict:
    # Nothing dirty or uncommitted is ever frozen, even when the resulting
    # bytes would be semantically invalid: a genuinely broken committed input
    # must replay as the same deterministic verdict, never be recaptured.
    if _git(root, "status", "--porcelain", "--", relative).strip():
        raise _provenance("FLOW_REPLAY_WORKTREE_DIRTY", relative)
    path = root / relative
    tree = _tree_entry(root, head, relative)
    if tree is None:
        _require(
            not (path.exists() or path.is_symlink()),
            "FLOW_REPLAY_UNCOMMITTED",
            relative,
        )
        return {"state": "ABSENT", "blob_oid": None, "sha256": None}
    mode, object_type, oid = tree
    _require(
        object_type == "blob" and mode in GIT_BLOB_MODES,
        "FLOW_REPLAY_BLOB_MODE_INVALID",
        f"{relative}:{mode}:{object_type}",
    )
    committed = _authenticated_bytes(root, relative, oid)
    try:
        live = path.read_bytes()
    except OSError as exc:
        raise _provenance("FLOW_REPLAY_INPUT_MISSING", relative) from exc
    _require(live == committed, "FLOW_REPLAY_HEAD_BLOB_MISMATCH", relative)
    return {
        "state": "PRESENT",
        "blob_oid": oid,
        "sha256": hashlib.sha256(committed).hexdigest(),
    }


def capture_flow_replay_inputs(
    root: Path = ROOT, *, trusted_validation_head=None
) -> dict:
    """Freeze the exact ten inputs at one trusted HEAD, capture-once.

    Called on a fresh build only.  Validation never captures: replaying a
    persisted packet must read that packet's own source commit, never today's
    repository state.
    """
    try:
        repository = Path(root).resolve()
        _require_repository_boundary(repository)
        head = _resolved_validation_head(repository, trusted_validation_head)
        return {
            "schema_version": FLOW_REPLAY_SCHEMA_VERSION,
            "source_commit": head,
            "files": {
                relative: _capture_flow_replay_file(repository, head, relative)
                for relative in FLOW_REPLAY_INPUT_PATHS
            },
        }
    except FlowReplayProvenanceError:
        raise
    except Exception as exc:  # noqa: BLE001 - an unprovable capture is hard
        raise _provenance(
            "FLOW_REPLAY_CAPTURE_FAILED", f"{type(exc).__name__}:{exc}"
        ) from exc


def flow_replay_inputs_at_commit(
    source_commit: str,
    *,
    trusted_repository_root: Path = ROOT,
    trusted_validation_head=None,
) -> dict:
    """The real ten-file closure of an externally supplied historical commit.

    Every entry is read from that commit's ACTUAL tree, never from a claim, so
    the returned envelope is a description of the repository rather than of
    whatever a caller wished were true.  ``source_commit`` itself is external
    operator context: this module never derives it from a packet, a locator or
    the live HEAD.
    """
    try:
        root = Path(trusted_repository_root).resolve()
        _require(
            isinstance(source_commit, str) and GIT_OID_RE.fullmatch(source_commit) is not None,
            "FLOW_REPLAY_SOURCE_COMMIT_INVALID",
            repr(source_commit),
        )
        _require_repository_boundary(root)
        trusted_head = _resolved_validation_head(root, trusted_validation_head)
        code, out = _git_result(
            root, "rev-parse", "--verify", "--quiet", f"{source_commit}^{{commit}}"
        )
        _require(code == 0, "FLOW_REPLAY_SOURCE_COMMIT_OBJECT_MISSING", source_commit)
        resolved = out.strip()
        _require(
            resolved == source_commit,
            "FLOW_REPLAY_SOURCE_COMMIT_INVALID",
            resolved,
        )
        _require_trusted_ancestor(root, resolved, trusted_head)
        files = {}
        for relative in FLOW_REPLAY_INPUT_PATHS:
            tree = _tree_entry(root, resolved, relative)
            if tree is None:
                files[relative] = {"state": "ABSENT", "blob_oid": None, "sha256": None}
                continue
            mode, object_type, oid = tree
            _require(
                object_type == "blob" and mode in GIT_BLOB_MODES,
                "FLOW_REPLAY_BLOB_MODE_INVALID",
                f"{relative}:{mode}:{object_type}",
            )
            data = _authenticated_bytes(root, relative, oid)
            files[relative] = {
                "state": "PRESENT",
                "blob_oid": oid,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        return {
            "schema_version": FLOW_REPLAY_SCHEMA_VERSION,
            "source_commit": resolved,
            "files": files,
        }
    except FlowReplayProvenanceError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _provenance(
            "FLOW_REPLAY_HISTORICAL_CLOSURE_FAILED", f"{type(exc).__name__}:{exc}"
        ) from exc


@contextlib.contextmanager
def materialized_flow_replay_root(verified: dict):
    """Write the authenticated bytes into a fresh isolated root and yield it.

    Exact bytes at the exact relative path.  No JSON re-serialization, no
    whitespace normalization, no writing into the real ROOT, no module-level
    ROOT monkeypatch and no fixture substitution.  A proven-absent path is not
    created, so the producer sees the same tree shape the source commit had.
    """
    root = Path(tempfile.mkdtemp(prefix="flow-replay-")).resolve()
    try:
        for relative, data in verified.items():
            if data is None:
                continue
            target = root / _checked_relative_path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            # Re-read what actually landed: a materialization that did not
            # reproduce the authenticated bytes must not reach the producer.
            written = target.read_bytes()
            _require(
                written == data
                and hashlib.sha256(written).hexdigest()
                == hashlib.sha256(data).hexdigest()
                and _git_blob_oid(written) == _git_blob_oid(data),
                "FLOW_REPLAY_MATERIALIZATION_MISMATCH",
                relative,
            )
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _require_replayable_closure(verified: dict) -> None:
    """The proven closure must actually be able to rebuild this packet.

    Reached only after all ten entries are authenticated, so every failure
    here reports a real repository fact.  The optional P2-COM-03 ledger
    combinations are checked BEFORE the producer runs so a genuinely
    inconsistent chain is an explicit exception rather than something the
    producer might round off to an empty-history record.
    """
    absent_required = [
        relative for relative in FLOW_REPLAY_REQUIRED_INPUT_PATHS
        if verified[relative] is None
    ]
    if absent_required:
        raise UnreplayableFlowHistoryError(
            f"FLOW_REPLAY_REQUIRED_INPUT_ABSENT:{','.join(absent_required)}"
        )
    contract = verified[TRANSITION_LEDGER_CONTRACT_REL] is not None
    predecessor = verified[TRANSITION_LEDGER_PREDECESSOR_REL] is not None
    pointer = verified[TRANSITION_LEDGER_POINTER_REL] is not None
    if contract and predecessor and not pointer:
        # A ratified chain exists but its canonical pointer is gone.  That is
        # a recovery problem, never a licence to erase recorded history.
        raise UnreplayableFlowHistoryError(
            f"TRANSITION_LEDGER_POINTER_MISSING:{TRANSITION_LEDGER_POINTER_REL}"
        )
    if contract and pointer and not predecessor:
        raise UnreplayableFlowHistoryError(
            "TRANSITION_LEDGER_PREDECESSOR_REQUIRED:"
            f"{TRANSITION_LEDGER_PREDECESSOR_REL}"
        )


def _revalidate_production_pins(materialized_root: Path, verified: dict) -> dict:
    """Re-run the two production checks an isolated root would otherwise skip.

    C4 -- ``load_contract(path)`` decides ``production`` by comparing the path
    to the module's own CONTRACT_PATH, so a contract read from a temporary
    root would be validated in NON-production mode and the pinned-predecessor
    check would never run.  Call ``validate_contract(..., production=True)``
    explicitly on the materialized bytes instead, then re-derive the real
    predecessor file/content/hash-chain/height/tail from the isolated root.

    C5 -- ``_cross_asset_flow_contract_identity`` falls back to the repository
    ROOT copy when ``root != ROOT`` and the contract is missing, which exists
    for older downstream unit fixtures.  A replay must never reach it: require
    the authenticated file and pass its explicit path so the leaf validator
    and identity check run against exactly the frozen bytes.
    """
    policy = validate_policy(
        read_json(materialized_root / FLOW_POLICY_REL, "POLICY_INVALID")
    )

    # C5: the cross-asset flow contract is required, must be a real regular
    # file in the isolated root, and is validated by explicit path.
    cross_asset_path = materialized_root / CROSS_ASSET_CONTRACT_REL
    if verified[CROSS_ASSET_CONTRACT_REL] is None or not cross_asset_path.is_file():
        raise UnreplayableFlowHistoryError(
            f"FLOW_REPLAY_REQUIRED_INPUT_ABSENT:{CROSS_ASSET_CONTRACT_REL}"
        )
    _cross_asset_flow_contract_identity(
        policy, materialized_root, contract_path=cross_asset_path
    )

    # C4: production predecessor identity, on the frozen bytes.
    ledger_contract = None
    if verified[TRANSITION_LEDGER_CONTRACT_REL] is not None:
        ledger = transition_ledger_module()
        ledger_contract = _ledger_call(
            "TRANSITION_LEDGER_CONTRACT_INVALID",
            ledger.validate_contract,
            read_json(
                materialized_root / TRANSITION_LEDGER_CONTRACT_REL,
                "TRANSITION_LEDGER_CONTRACT_INVALID",
            ),
            production=True,
        )
        if verified[TRANSITION_LEDGER_PREDECESSOR_REL] is not None:
            _ledger_call(
                "TRANSITION_LEDGER_PREDECESSOR_INVALID",
                ledger.load_predecessor,
                ledger_contract,
                materialized_root,
            )
    return policy


def verified_flow_replay_closure(
    envelope,
    *,
    trusted_repository_root: Path = ROOT,
    trusted_validation_head=None,
) -> dict:
    """Authenticate an envelope AND prove its closure can rebuild this packet.

    Separated from the rebuild below so a caller assembling a larger document
    can settle both hard questions -- is this provable, and is it replayable --
    before doing any other work, rather than discovering an unprovable input
    after everything else has been built.
    """
    verified = verify_flow_replay_inputs(
        envelope,
        trusted_repository_root=trusted_repository_root,
        trusted_validation_head=trusted_validation_head,
    )
    _require_replayable_closure(verified)
    return verified


def build_reference_from_verified_inputs(verified: dict) -> dict:
    """Rebuild this reference from an already-authenticated closure.

    The live repository is never read for content: the bytes were taken from
    real Git objects, are materialized byte-for-byte into an isolated root,
    and the unchanged production builder runs against that root.  Producer
    semantic hashing and authority are untouched -- only where the inputs come
    from changes.
    """
    _require(
        isinstance(verified, dict) and set(verified) == set(FLOW_REPLAY_INPUT_PATHS),
        "FLOW_REPLAY_VERIFIED_CLOSURE_INVALID",
        str(sorted(verified)) if isinstance(verified, dict) else type(verified).__name__,
    )
    with materialized_flow_replay_root(verified) as materialized_root:
        _revalidate_production_pins(materialized_root, verified)
        return build_reference(materialized_root)


def build_reference_from_frozen_inputs(
    envelope,
    *,
    trusted_repository_root: Path = ROOT,
    trusted_validation_head=None,
) -> dict:
    """Authenticate one frozen input tuple and rebuild this reference from it."""
    return build_reference_from_verified_inputs(
        verified_flow_replay_closure(
            envelope,
            trusted_repository_root=trusted_repository_root,
            trusted_validation_head=trusted_validation_head,
        )
    )


def build_reference_from_source_commit(
    source_commit: str,
    *,
    trusted_repository_root: Path = ROOT,
    trusted_validation_head=None,
) -> dict:
    """Rebuild this reference from an externally supplied historical commit.

    The commit is trusted operator context.  Being an ancestor of the trusted
    validation HEAD proves the closure is authentic history; it does NOT prove
    this commit is the one that originally issued any particular packet.  That
    claim, if it is made at all, belongs to the caller that supplied it.
    """
    return build_reference_from_frozen_inputs(
        flow_replay_inputs_at_commit(
            source_commit,
            trusted_repository_root=trusted_repository_root,
            trusted_validation_head=trusted_validation_head,
        ),
        trusted_repository_root=trusted_repository_root,
        trusted_validation_head=trusted_validation_head,
    )


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
