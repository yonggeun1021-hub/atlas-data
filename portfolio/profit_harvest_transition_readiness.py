#!/usr/bin/env python3
"""P7-11 formal-transition evidence audit; never a Harvest action path.

The existing ``profit_harvest_readiness`` module is the canonical validator
for today's locked operational packet.  This module does not replace or
relax it.  It answers the separate adoption question: which independently
supplied proofs would have to exist before a settled Harvest receipt could be
handed to P7-10 without treating expected proceeds as available cash.

Value-bearing receipts are validated in memory and are never copied into the
money-free readiness output.  A structurally valid packet is not trusted by
its self-hash alone: the caller must separately supply the exact ratification
and scheduler-attestation hashes.  Even ``ADOPTION_READY_LOCAL_ONLY`` leaves
all recommendation, quantity, action, order, Production and Trading authority
closed and does not authorize a canonical WBS status change.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import re

from portfolio import profit_harvest_readiness
from replay.opportunity_trigger import payload_sha256


READINESS_SCHEMA_VERSION = "profit_harvest_transition_readiness/1"
RATIFICATION_SCHEMA_VERSION = "profit_harvest_transition_ratification/1"
SETTLEMENT_SCHEMA_VERSION = "settled_harvest_proceeds_evidence/1"
P8_13_LINK_SCHEMA_VERSION = "profit_harvest_p8_13_lineage/1"
P7_10_LINK_SCHEMA_VERSION = "profit_harvest_p7_10_consumer_link/1"
SCHEDULE_ATTESTATION_SCHEMA_VERSION = "profit_harvest_schedule_attestation/1"

P7_11_ROW = {
    "page_id": "3c49f2d7-3c84-8138-8644-eee246dd713f",
    "order": 711,
    "work_item": "P7-11 Profit Harvesting / Rapid Gain Realization Engine",
}
P8_13_PAGE_ID = "3c49f2d7-3c84-8106-83ee-d0f390af6860"
P7_10_PAGE_ID = "3c49f2d7-3c84-81d9-ac68-fd0830b45356"

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "evidence_validation_only": True,
    "canonical_status_change_authorized": False,
    "stage_promotion_authorized": False,
    "candidate_authorized": False,
    "buy_authorized": False,
    "harvest_review_authorized": False,
    "quantity_authorized": False,
    "reallocation_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

EVIDENCE_AUTHORITY = {
    "evidence_only": True,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

GATE_ORDER = (
    "exact_ratification",
    "settled_proceeds_origin",
    "settlement_identity",
    "positive_amount_representation",
    "p8_13_upstream_linkage",
    "p7_10_downstream_linkage",
    "first_genuine_scheduled_natural_evidence",
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_CURRENCY_RE = re.compile(r"[A-Z][A-Z0-9]{2,11}")
_POSITIVE_DECIMAL_RE = re.compile(r"(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|0\.[0-9]*[1-9])")


class ProfitHarvestTransitionReadinessError(ValueError):
    """A supplied authority, lineage or evidence claim is not exact."""


def _fields(value: object, expected: set[str], error: str) -> dict:
    if type(value) is not dict or set(value) != expected:
        raise ProfitHarvestTransitionReadinessError(error)
    return value


def _sha256(value: object, error: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ProfitHarvestTransitionReadinessError(error)
    return value


def _token(value: object, error: str) -> str:
    if type(value) is not str or _TOKEN_RE.fullmatch(value) is None:
        raise ProfitHarvestTransitionReadinessError(error)
    return value


def _utc(value: object, error: str) -> dt.datetime:
    if type(value) is not str:
        raise ProfitHarvestTransitionReadinessError(error)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise ProfitHarvestTransitionReadinessError(error) from exc


def _self_hash(value: dict, field: str, error: str) -> str:
    digest = _sha256(value.get(field), error)
    unsigned = {key: item for key, item in value.items() if key != field}
    if digest != payload_sha256(unsigned):
        raise ProfitHarvestTransitionReadinessError(error)
    return digest


def _authority(value: object, error: str) -> None:
    if not profit_harvest_readiness._exact_equal(value, EVIDENCE_AUTHORITY):
        raise ProfitHarvestTransitionReadinessError(error)


def _positive_decimal(value: object) -> str:
    if type(value) is not str or _POSITIVE_DECIMAL_RE.fullmatch(value) is None:
        raise ProfitHarvestTransitionReadinessError(
            "SETTLED_PROCEEDS_POSITIVE_CANONICAL_DECIMAL_REQUIRED"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ProfitHarvestTransitionReadinessError(
            "SETTLED_PROCEEDS_POSITIVE_CANONICAL_DECIMAL_REQUIRED"
        ) from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ProfitHarvestTransitionReadinessError(
            "SETTLED_PROCEEDS_POSITIVE_CANONICAL_DECIMAL_REQUIRED"
        )
    return value


def validate_ratification(value: dict, *, trusted_sha256: str) -> dict:
    value = _fields(
        value,
        {
            "schema_version",
            "scope",
            "wbs",
            "status",
            "ratified_by",
            "ratified_at_utc",
            "effective_from_utc",
            "policy_sha256",
            "authority",
            "packet_sha256",
        },
        "RATIFICATION_FIELDS_INVALID",
    )
    if (
        value["schema_version"] != RATIFICATION_SCHEMA_VERSION
        or value["scope"] != "P7_11_FORMAL_TRANSITION_EVIDENCE_ONLY"
        or value["status"] != "RATIFIED"
        or not profit_harvest_readiness._exact_equal(value["wbs"], P7_11_ROW)
    ):
        raise ProfitHarvestTransitionReadinessError("RATIFICATION_SCOPE_INVALID")
    _token(value["ratified_by"], "RATIFICATION_ACTOR_INVALID")
    ratified_at = _utc(value["ratified_at_utc"], "RATIFICATION_TIME_INVALID")
    effective_from = _utc(
        value["effective_from_utc"], "RATIFICATION_EFFECTIVE_TIME_INVALID"
    )
    if effective_from < ratified_at:
        raise ProfitHarvestTransitionReadinessError(
            "RATIFICATION_EFFECTIVE_BEFORE_APPROVAL"
        )
    _sha256(value["policy_sha256"], "RATIFIED_POLICY_SHA_INVALID")
    _authority(value["authority"], "RATIFICATION_AUTHORITY_ESCALATION")
    digest = _self_hash(value, "packet_sha256", "RATIFICATION_SHA_MISMATCH")
    if _sha256(trusted_sha256, "TRUSTED_RATIFICATION_SHA_INVALID") != digest:
        raise ProfitHarvestTransitionReadinessError(
            "RATIFICATION_NOT_INDEPENDENTLY_PINNED"
        )
    return copy.deepcopy(value)


def validate_settlement(value: dict) -> dict:
    value = _fields(
        value,
        {
            "schema_version",
            "settlement_status",
            "settlement_id",
            "market",
            "canonical_instrument_id",
            "ledger_id",
            "entry_order_id",
            "exit_order_ids",
            "sell_fill_ids",
            "settled_at_utc",
            "proceeds",
            "source",
            "authority",
            "packet_sha256",
        },
        "SETTLEMENT_FIELDS_INVALID",
    )
    if (
        value["schema_version"] != SETTLEMENT_SCHEMA_VERSION
        or value["settlement_status"] != "SETTLED_AVAILABLE_CASH"
    ):
        raise ProfitHarvestTransitionReadinessError("SETTLEMENT_STATUS_INVALID")
    for field in (
        "settlement_id",
        "market",
        "canonical_instrument_id",
        "ledger_id",
        "entry_order_id",
    ):
        _token(value[field], f"SETTLEMENT_{field.upper()}_INVALID")
    for field in ("exit_order_ids", "sell_fill_ids"):
        rows = value[field]
        if type(rows) is not list or not rows:
            raise ProfitHarvestTransitionReadinessError(
                f"SETTLEMENT_{field.upper()}_INVALID"
            )
        for item in rows:
            _token(item, f"SETTLEMENT_{field.upper()}_INVALID")
        if len(rows) != len(set(rows)):
            raise ProfitHarvestTransitionReadinessError(
                f"SETTLEMENT_{field.upper()}_INVALID"
            )
    _utc(value["settled_at_utc"], "SETTLEMENT_TIME_INVALID")
    proceeds = _fields(
        value["proceeds"], {"amount", "currency"}, "SETTLEMENT_PROCEEDS_INVALID"
    )
    _positive_decimal(proceeds["amount"])
    if type(proceeds["currency"]) is not str or _CURRENCY_RE.fullmatch(
        proceeds["currency"]
    ) is None:
        raise ProfitHarvestTransitionReadinessError("SETTLEMENT_CURRENCY_INVALID")
    source = _fields(
        value["source"],
        {
            "source_kind",
            "runtime_receipt_sha256",
            "ledger_receipt_sha256",
            "p7_13_exit_decision_sha256",
            "sell_fill_reconciliation_sha256",
        },
        "SETTLEMENT_SOURCE_INVALID",
    )
    if source["source_kind"] != "PRIVATE_VIRTUAL_LEDGER_SELL_FILL_RECONCILIATION":
        raise ProfitHarvestTransitionReadinessError("SETTLEMENT_ORIGIN_INVALID")
    for field in source:
        if field != "source_kind":
            _sha256(source[field], f"SETTLEMENT_{field.upper()}_INVALID")
    _authority(value["authority"], "SETTLEMENT_AUTHORITY_ESCALATION")
    _self_hash(value, "packet_sha256", "SETTLEMENT_SHA_MISMATCH")
    return copy.deepcopy(value)


def validate_p8_13_link(
    value: dict,
    *,
    settlement: dict,
    trusted_sha256: str,
) -> dict:
    value = _fields(
        value,
        {
            "schema_version",
            "wbs_page_id",
            "proposal_id",
            "proposed_action",
            "proposal_created_at_utc",
            "entry_proposal_boundary_sha256",
            "settlement_receipt_sha256",
            "authority",
            "packet_sha256",
        },
        "P8_13_LINK_FIELDS_INVALID",
    )
    if (
        value["schema_version"] != P8_13_LINK_SCHEMA_VERSION
        or value["wbs_page_id"] != P8_13_PAGE_ID
        or value["proposed_action"]
        not in {"HARVEST_REVIEW", "REDUCE_REVIEW", "EXIT_REVIEW"}
    ):
        raise ProfitHarvestTransitionReadinessError("P8_13_LINK_SCOPE_INVALID")
    _token(value["proposal_id"], "P8_13_PROPOSAL_ID_INVALID")
    proposal_created = _utc(
        value["proposal_created_at_utc"], "P8_13_PROPOSAL_TIME_INVALID"
    )
    if proposal_created > _utc(settlement["settled_at_utc"], "SETTLEMENT_TIME_INVALID"):
        raise ProfitHarvestTransitionReadinessError(
            "P8_13_PROPOSAL_AFTER_SETTLEMENT"
        )
    _sha256(
        value["entry_proposal_boundary_sha256"], "P8_13_BOUNDARY_SHA_INVALID"
    )
    if value["settlement_receipt_sha256"] != settlement["packet_sha256"]:
        raise ProfitHarvestTransitionReadinessError("P8_13_SETTLEMENT_LINK_MISMATCH")
    _authority(value["authority"], "P8_13_LINK_AUTHORITY_ESCALATION")
    digest = _self_hash(value, "packet_sha256", "P8_13_LINK_SHA_MISMATCH")
    if _sha256(trusted_sha256, "TRUSTED_P8_13_LINK_SHA_INVALID") != digest:
        raise ProfitHarvestTransitionReadinessError(
            "P8_13_LINK_NOT_INDEPENDENTLY_PINNED"
        )
    return copy.deepcopy(value)


def validate_p7_10_link(
    value: dict,
    *,
    settlement_sha256: str,
    trusted_sha256: str,
) -> dict:
    value = _fields(
        value,
        {
            "schema_version",
            "wbs_page_id",
            "consumer_schema_version",
            "consumer_contract_sha256",
            "accepted_input_schema_version",
            "settlement_receipt_sha256",
            "consumer_validation_status",
            "authority",
            "packet_sha256",
        },
        "P7_10_LINK_FIELDS_INVALID",
    )
    if (
        value["schema_version"] != P7_10_LINK_SCHEMA_VERSION
        or value["wbs_page_id"] != P7_10_PAGE_ID
        or value["accepted_input_schema_version"] != SETTLEMENT_SCHEMA_VERSION
        or value["consumer_validation_status"]
        != "EXACT_SETTLED_PROCEEDS_INPUT_SUPPORTED"
    ):
        raise ProfitHarvestTransitionReadinessError("P7_10_LINK_SCOPE_INVALID")
    _token(value["consumer_schema_version"], "P7_10_CONSUMER_SCHEMA_INVALID")
    _sha256(value["consumer_contract_sha256"], "P7_10_CONTRACT_SHA_INVALID")
    if value["settlement_receipt_sha256"] != settlement_sha256:
        raise ProfitHarvestTransitionReadinessError("P7_10_SETTLEMENT_LINK_MISMATCH")
    _authority(value["authority"], "P7_10_LINK_AUTHORITY_ESCALATION")
    digest = _self_hash(value, "packet_sha256", "P7_10_LINK_SHA_MISMATCH")
    if _sha256(trusted_sha256, "TRUSTED_P7_10_LINK_SHA_INVALID") != digest:
        raise ProfitHarvestTransitionReadinessError(
            "P7_10_LINK_NOT_INDEPENDENTLY_PINNED"
        )
    return copy.deepcopy(value)


def validate_schedule_attestation(
    value: dict,
    *,
    settlement: dict,
    trusted_sha256: str,
) -> dict:
    value = _fields(
        value,
        {
            "schema_version",
            "sample_origin",
            "trigger_kind",
            "scheduler_id",
            "run_id",
            "scheduled_for_utc",
            "started_at_utc",
            "completed_at_utc",
            "runtime_receipt_sha256",
            "settlement_receipt_sha256",
            "authority",
            "attestation_sha256",
        },
        "SCHEDULE_ATTESTATION_FIELDS_INVALID",
    )
    if (
        value["schema_version"] != SCHEDULE_ATTESTATION_SCHEMA_VERSION
        or value["sample_origin"] != "NATURAL_AUTOMATED"
        or value["trigger_kind"] != "SCHEDULE"
    ):
        raise ProfitHarvestTransitionReadinessError(
            "GENUINE_SCHEDULED_NATURAL_ORIGIN_INVALID"
        )
    _token(value["scheduler_id"], "SCHEDULER_ID_INVALID")
    _token(value["run_id"], "SCHEDULE_RUN_ID_INVALID")
    scheduled = _utc(value["scheduled_for_utc"], "SCHEDULED_FOR_INVALID")
    started = _utc(value["started_at_utc"], "SCHEDULE_STARTED_AT_INVALID")
    completed = _utc(value["completed_at_utc"], "SCHEDULE_COMPLETED_AT_INVALID")
    settled = _utc(settlement["settled_at_utc"], "SETTLEMENT_TIME_INVALID")
    if not scheduled <= started <= settled <= completed:
        raise ProfitHarvestTransitionReadinessError("SCHEDULE_TIME_ORDER_INVALID")
    runtime_sha = settlement["source"]["runtime_receipt_sha256"]
    if value["runtime_receipt_sha256"] != runtime_sha:
        raise ProfitHarvestTransitionReadinessError("SCHEDULE_RUNTIME_LINK_MISMATCH")
    if value["settlement_receipt_sha256"] != settlement["packet_sha256"]:
        raise ProfitHarvestTransitionReadinessError(
            "SCHEDULE_SETTLEMENT_LINK_MISMATCH"
        )
    _authority(value["authority"], "SCHEDULE_ATTESTATION_AUTHORITY_ESCALATION")
    digest = _self_hash(
        value, "attestation_sha256", "SCHEDULE_ATTESTATION_SHA_MISMATCH"
    )
    if _sha256(trusted_sha256, "TRUSTED_SCHEDULE_SHA_INVALID") != digest:
        raise ProfitHarvestTransitionReadinessError(
            "SCHEDULE_ATTESTATION_NOT_INDEPENDENTLY_PINNED"
        )
    return copy.deepcopy(value)


def build_transition_readiness(
    *,
    ratification: dict | None = None,
    trusted_ratification_sha256: str | None = None,
    settlement: dict | None = None,
    p8_13_link: dict | None = None,
    trusted_p8_13_link_sha256: str | None = None,
    p7_10_link: dict | None = None,
    trusted_p7_10_link_sha256: str | None = None,
    schedule_attestation: dict | None = None,
    trusted_schedule_attestation_sha256: str | None = None,
) -> dict:
    """Return money-free readiness while validating supplied proofs exactly."""
    gates = {name: "BLOCKED" for name in GATE_ORDER}
    source = {
        "ratification_sha256": None,
        "settlement_receipt_sha256": None,
        "p8_13_link_sha256": None,
        "p7_10_link_sha256": None,
        "schedule_attestation_sha256": None,
    }

    checked_ratification = None
    if ratification is not None:
        if trusted_ratification_sha256 is None:
            raise ProfitHarvestTransitionReadinessError(
                "TRUSTED_RATIFICATION_SHA_REQUIRED"
            )
        checked_ratification = validate_ratification(
            ratification, trusted_sha256=trusted_ratification_sha256
        )
        gates["exact_ratification"] = "PASS"
        source["ratification_sha256"] = checked_ratification["packet_sha256"]
    elif trusted_ratification_sha256 is not None:
        raise ProfitHarvestTransitionReadinessError(
            "RATIFICATION_PACKET_REQUIRED_FOR_TRUSTED_SHA"
        )

    checked_settlement = None
    if settlement is not None:
        checked_settlement = validate_settlement(settlement)
        gates["settled_proceeds_origin"] = "PASS"
        gates["settlement_identity"] = "PASS"
        gates["positive_amount_representation"] = "PASS"
        source["settlement_receipt_sha256"] = checked_settlement["packet_sha256"]

    if p8_13_link is not None:
        if checked_settlement is None:
            raise ProfitHarvestTransitionReadinessError(
                "P8_13_LINK_REQUIRES_SETTLEMENT"
            )
        if trusted_p8_13_link_sha256 is None:
            raise ProfitHarvestTransitionReadinessError(
                "TRUSTED_P8_13_LINK_SHA_REQUIRED"
            )
        checked = validate_p8_13_link(
            p8_13_link,
            settlement=checked_settlement,
            trusted_sha256=trusted_p8_13_link_sha256,
        )
        gates["p8_13_upstream_linkage"] = "PASS"
        source["p8_13_link_sha256"] = checked["packet_sha256"]
        if checked_ratification is not None:
            effective = _utc(
                checked_ratification["effective_from_utc"],
                "RATIFICATION_EFFECTIVE_TIME_INVALID",
            )
            proposal_created = _utc(
                checked["proposal_created_at_utc"], "P8_13_PROPOSAL_TIME_INVALID"
            )
            if proposal_created < effective:
                raise ProfitHarvestTransitionReadinessError(
                    "P8_13_PROPOSAL_PREDATES_RATIFICATION"
                )
    elif trusted_p8_13_link_sha256 is not None:
        raise ProfitHarvestTransitionReadinessError(
            "P8_13_LINK_REQUIRED_FOR_TRUSTED_SHA"
        )

    if p7_10_link is not None:
        if checked_settlement is None:
            raise ProfitHarvestTransitionReadinessError(
                "P7_10_LINK_REQUIRES_SETTLEMENT"
            )
        if trusted_p7_10_link_sha256 is None:
            raise ProfitHarvestTransitionReadinessError(
                "TRUSTED_P7_10_LINK_SHA_REQUIRED"
            )
        checked = validate_p7_10_link(
            p7_10_link,
            settlement_sha256=checked_settlement["packet_sha256"],
            trusted_sha256=trusted_p7_10_link_sha256,
        )
        gates["p7_10_downstream_linkage"] = "PASS"
        source["p7_10_link_sha256"] = checked["packet_sha256"]
    elif trusted_p7_10_link_sha256 is not None:
        raise ProfitHarvestTransitionReadinessError(
            "P7_10_LINK_REQUIRED_FOR_TRUSTED_SHA"
        )

    if schedule_attestation is not None:
        if checked_settlement is None:
            raise ProfitHarvestTransitionReadinessError(
                "SCHEDULE_ATTESTATION_REQUIRES_SETTLEMENT"
            )
        if trusted_schedule_attestation_sha256 is None:
            raise ProfitHarvestTransitionReadinessError(
                "TRUSTED_SCHEDULE_ATTESTATION_SHA_REQUIRED"
            )
        checked = validate_schedule_attestation(
            schedule_attestation,
            settlement=checked_settlement,
            trusted_sha256=trusted_schedule_attestation_sha256,
        )
        source["schedule_attestation_sha256"] = checked["attestation_sha256"]
        if checked_ratification is not None:
            effective = _utc(
                checked_ratification["effective_from_utc"],
                "RATIFICATION_EFFECTIVE_TIME_INVALID",
            )
            scheduled = _utc(
                checked["scheduled_for_utc"], "SCHEDULED_FOR_INVALID"
            )
            if scheduled < effective:
                raise ProfitHarvestTransitionReadinessError(
                    "NATURAL_EVIDENCE_PREDATES_RATIFICATION"
                )
            gates["first_genuine_scheduled_natural_evidence"] = "PASS"
    elif trusted_schedule_attestation_sha256 is not None:
        raise ProfitHarvestTransitionReadinessError(
            "SCHEDULE_ATTESTATION_REQUIRED_FOR_TRUSTED_SHA"
        )

    if gates["exact_ratification"] != "PASS":
        status = "BLOCKED_EXACT_RATIFICATION"
    elif any(
        gates[name] != "PASS"
        for name in (
            "settled_proceeds_origin",
            "settlement_identity",
            "positive_amount_representation",
            "first_genuine_scheduled_natural_evidence",
        )
    ):
        status = "WAITING_FIRST_GENUINE_SCHEDULED_NATURAL_EVIDENCE"
    elif any(
        gates[name] != "PASS"
        for name in ("p8_13_upstream_linkage", "p7_10_downstream_linkage")
    ):
        status = "BLOCKED_EXACT_LINEAGE_OR_CONSUMER_LINKAGE"
    else:
        status = "ADOPTION_READY_LOCAL_ONLY"

    blockers = [name.upper() for name in GATE_ORDER if gates[name] != "PASS"]
    packet = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "wbs": copy.deepcopy(P7_11_ROW),
        "source": source,
        "gates": gates,
        "summary": {
            "ratification_ready_count": int(gates["exact_ratification"] == "PASS"),
            "settled_proceeds_ready_count": int(
                gates["settled_proceeds_origin"] == "PASS"
                and gates["settlement_identity"] == "PASS"
                and gates["positive_amount_representation"] == "PASS"
            ),
            "p8_13_link_ready_count": int(
                gates["p8_13_upstream_linkage"] == "PASS"
            ),
            "p7_10_link_ready_count": int(
                gates["p7_10_downstream_linkage"] == "PASS"
            ),
            "genuine_scheduled_natural_ready_count": int(
                gates["first_genuine_scheduled_natural_evidence"] == "PASS"
            ),
        },
        "decision": {
            "status": status,
            "candidate": "NONE",
            "recommended_action": "NONE",
            "capital": 0,
            "expected_proceeds": None,
            "settled_proceeds": None,
            "harvest_proposal": None,
            "reallocation_proposal": None,
            "trade_proposal": None,
            "order_intent": None,
            "canonical_status_change_authorized": False,
        },
        "blockers": blockers,
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_transition_readiness(packet: dict, **inputs) -> dict:
    expected = build_transition_readiness(**inputs)
    if not profit_harvest_readiness._exact_equal(packet, expected):
        raise ProfitHarvestTransitionReadinessError(
            "PROFIT_HARVEST_TRANSITION_READINESS_TAMPER_OR_DRIFT"
        )
    return copy.deepcopy(packet)
