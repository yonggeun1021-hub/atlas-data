#!/usr/bin/env python3
"""P2-05 CIO-ratified per-market ``rotation_state_policy/1`` identity/evidence.

``rotation/rotation_state_ledger.py`` already implements the append-only,
external-policy-gated ledger mechanics and deliberately carries no repository
default state policy (``repository_default_policy: "ABSENT"`` in
``config/rotation_state_ledger_contract.json`` -- unchanged by this module).
Until now the only ``EMERGING/STRONG/WEAKENING`` mapping anywhere in this
repository was the literal test-only fixture in
``test/test_rotation_state_ledger.py`` (``policy_id = "{market}.STATE.TEST.V1"``,
``ratified_by = "test-cio"``).

This module adds the CIO's real ratification as a separate, narrowly-scoped
piece of evidence: the exact 9-cell structural-bucket-transition mapping, its
semantic definitions, and the per-market ``maximum_ledger_gap_days``, each
bound to a fixed ``ratified_by``/``ratified_at_utc``/``effective_from``. It
does **not** become a new repository default policy for the ledger -- nothing
here is auto-loaded by ``rotation_state_ledger.py``, and ``build_policy()``
below still requires the caller to supply the exact upstream rotation
contract version and rotation-policy SHA-256 of a real, already-produced
P2-02/03/04 packet before it will emit anything. Those two binding fields are
therefore never invented, cached, or defaulted here: for US and Crypto no
such packet exists yet (see ``rotation/rotation_state_ledger_operational_
readiness.py`` -- readiness stays 0/3), so ``build_policy()`` for those
markets simply has nothing real to bind to and cannot be used to fabricate
progress. Building a policy object is not itself evidence of operational
readiness; only ``rotation_state_ledger.apply_rotation()`` actually appending
a natural record is.

``build_policy()`` intentionally exposes no override parameters for the
ratified constants (mapping, vocabulary, gap, ratified_by, ratified_at,
effective_from) -- the only caller-supplied values are the two upstream
binding fields every application already required. This closes off the
self-ratification-bypass shape that PR #348 removed from the Korea
production proof script (a production script had copied the test fixture
mapping, self-labelled it ``RATIFIED``, and could write an external ledger):
there is no parameter surface here through which a caller could inject an
alternate mapping, ratifier, or approval status.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "rotation_state_policy_ratification_contract.json"
CONTRACT_VERSION = "rotation_state_policy_ratification/1"
POLICY_SCHEMA_VERSION = "rotation_state_policy/1"
MARKETS = ("US", "KOREA", "CRYPTO")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RotationStatePolicyRatificationError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationStatePolicyRatificationError(
            f"JSON_READ_FAILED:{path}:{exc}"
        ) from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "ledger_contract_version": "rotation_state_ledger/1",
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "ratified_by": "CIO",
        "ratified_at_utc": "2026-09-11T16:36:38Z",
        "effective_from": "2026-09-11",
        "state_vocabulary": ["EMERGING", "STRONG", "WEAKENING"],
        "state_by_bucket_transition": {
            "BOTTOM_TO_BOTTOM": "WEAKENING",
            "BOTTOM_TO_MIDDLE": "EMERGING",
            "BOTTOM_TO_TOP": "STRONG",
            "MIDDLE_TO_BOTTOM": "WEAKENING",
            "MIDDLE_TO_MIDDLE": "EMERGING",
            "MIDDLE_TO_TOP": "STRONG",
            "TOP_TO_BOTTOM": "WEAKENING",
            "TOP_TO_MIDDLE": "WEAKENING",
            "TOP_TO_TOP": "STRONG",
        },
        "state_semantics": {
            "STRONG": "CURRENT_BUCKET_TOP",
            "WEAKENING": "CURRENT_BUCKET_BOTTOM_OR_TOP_TO_MIDDLE",
            "EMERGING": "NON_WEAKENING_MIDDLE_STATE",
        },
        "policy_id_by_market": {
            "US": "US.ROTATION_STATE.CIO_RATIFIED.V1",
            "KOREA": "KOREA.ROTATION_STATE.CIO_RATIFIED.V1",
            "CRYPTO": "CRYPTO.ROTATION_STATE.CIO_RATIFIED.V1",
        },
        "markets": {
            "US": {
                "input_rotation_contract_version": "us_capital_rotation/2",
                "maximum_ledger_gap_days": 4,
                "operational_assumption": None,
            },
            "KOREA": {
                "input_rotation_contract_version": "korea_capital_rotation/4",
                "maximum_ledger_gap_days": 7,
                "operational_assumption": (
                    "ONE_FULL_P2_03_PACKET_PER_KRX_TRADING_DAY_POST_CLOSE"
                ),
            },
            "CRYPTO": {
                "input_rotation_contract_version": "crypto_rotation/2",
                "maximum_ledger_gap_days": 2,
                "operational_assumption": None,
            },
        },
        "korea_gap_derivation": {
            "canonical_calendar_source": (
                "evidence/market_calendar/krx_global_holiday/2026-09-09/"
                "capture-2026.json"
            ),
            "canonical_calendar_year": 2026,
            "natural_maximum_scheduled_gap_days": 6,
            "missed_session_grace_days": 1,
            "maximum_ledger_gap_days": 7,
            "cron_status": "P2-03_COMBINED_WORKFLOW_WORKFLOW_DISPATCH_ONLY_NO_SCHEDULE",
        },
        "authority": {
            "ratification_evidence_only": True,
            "repository_default_state_policy": False,
            "operational_readiness_authorized": False,
            "cadence_readiness_authorized": False,
            "p2_state_vocabulary_authorized": False,
            "state_ledger_authorized": False,
            "regime_input_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "briefing_wiring_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RotationStatePolicyRatificationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RotationStatePolicyRatificationError(
                f"CONTRACT_FIELD_MISMATCH:{key}"
            )
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def market_gap_days(market: str, contract: Optional[dict] = None) -> int:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if market not in contract["markets"]:
        raise RotationStatePolicyRatificationError(f"MARKET_UNSUPPORTED:{market}")
    return contract["markets"][market]["maximum_ledger_gap_days"]


def build_policy(
    market: str,
    rotation_contract_version: str,
    rotation_policy_sha256: str,
    contract: Optional[dict] = None,
) -> dict:
    """Build one CIO-ratified ``rotation_state_policy/1`` object for ``market``.

    ``rotation_contract_version`` and ``rotation_policy_sha256`` MUST come
    from a real, already-validated P2-02/03/04 rotation packet -- this
    function performs no producer validation itself and fabricates nothing:
    it only checks that the supplied contract version matches the one this
    market's ratification was scoped to, and that the SHA looks like a real
    SHA-256 digest. Everything else (mapping, vocabulary, gap, ratifier,
    ratification/effective timestamps, policy_id) comes from the CIO-ratified
    constants in ``config/rotation_state_policy_ratification_contract.json``
    and cannot be overridden by a caller -- this function accepts no other
    parameters.
    """
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if market not in contract["markets"]:
        raise RotationStatePolicyRatificationError(f"MARKET_UNSUPPORTED:{market}")
    market_entry = contract["markets"][market]
    if (
        not isinstance(rotation_contract_version, str)
        or rotation_contract_version != market_entry["input_rotation_contract_version"]
    ):
        raise RotationStatePolicyRatificationError(
            f"ROTATION_CONTRACT_VERSION_MISMATCH:{market}"
        )
    if (
        not isinstance(rotation_policy_sha256, str)
        or SHA256_RE.fullmatch(rotation_policy_sha256) is None
    ):
        raise RotationStatePolicyRatificationError(
            f"ROTATION_POLICY_SHA_INVALID:{market}"
        )
    policy = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": contract["policy_id_by_market"][market],
        "approval_status": "RATIFIED",
        "ratified_by": contract["ratified_by"],
        "ratified_at_utc": contract["ratified_at_utc"],
        "effective_from": contract["effective_from"],
        "effective_to": None,
        "market": market,
        "input_rotation_contract_version": rotation_contract_version,
        "input_rotation_policy_sha256": rotation_policy_sha256,
        "state_vocabulary": list(contract["state_vocabulary"]),
        "state_by_bucket_transition": dict(contract["state_by_bucket_transition"]),
        "maximum_ledger_gap_days": market_entry["maximum_ledger_gap_days"],
    }
    return policy


def policy_identity_sha256(policy: dict) -> str:
    """Content-addressed identity of one built policy object."""
    return payload_sha256(policy)
