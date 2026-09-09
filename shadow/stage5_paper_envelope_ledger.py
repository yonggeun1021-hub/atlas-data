#!/usr/bin/env python3
"""Stage5 fixture-only adapter from a validated Stage4 envelope to P10-11.

This deliberately does not decide whether an instrument should be bought,
what it should cost, or when a real event may run.  It accepts only a
hash-bound, closed-authority Stage4-shaped envelope and hands caller-supplied
fixture plan/snapshot values to the existing offline P10-11 simulator.  The
adapter adds the missing cross-boundary guarantees: decision-source binding,
closed-candle/PIT ordering, and a semantic result reconciliation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shadow import crypto_paper_simulator as SIMULATOR


CONTRACT_PATH = ROOT / "config" / "stage5_paper_envelope_ledger_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class Stage5PaperEnvelopeError(ValueError):
    """Fail-closed Stage5 fixture-envelope or reconciliation violation."""


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise Stage5PaperEnvelopeError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _exact(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(_exact(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_exact(a, b) for a, b in zip(left, right))
    return left == right


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise Stage5PaperEnvelopeError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise Stage5PaperEnvelopeError(code)
    return value


def _utc(value: object, code: str):
    try:
        return SIMULATOR._utc(value, code)
    except SIMULATOR.CryptoPaperSimulatorError as exc:
        raise Stage5PaperEnvelopeError(code) from exc


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage5PaperEnvelopeError("CONTRACT_READ_FAILED") from exc
    if not isinstance(value, dict):
        raise Stage5PaperEnvelopeError("CONTRACT_READ_FAILED")
    return value


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "stage5_paper_envelope_ledger/1",
        "input_schema_version": "stage4_paper_decision_envelope/1",
        "output_schema_version": "stage5_paper_envelope_result/1",
        "mode": "PAPER_CONTRACT_FIXTURE_ONLY",
        "decision_statuses": ["NOT_EVALUATED", "VALIDATED"],
        "same_candle_execution_policy": "DECISION_CANDLE_MUST_CLOSE_AND_BE_AVAILABLE_BEFORE_PLAN_OR_MATCH",
        "persistence_policy": "REUSE_EXTERNAL_CONTENT_ADDRESSED_SIMULATOR_SNAPSHOTS_ONLY",
        "economic_defaults": "NONE_CALLER_SUPPLIED_FIXTURE_VALUES_ONLY",
        "authority": {
            "stage4_decision_authorized": False,
            "candidate_authorized": False,
            "entry_authorized": False,
            "position_size_authorized": False,
            "virtual_ledger_operational_authorized": False,
            "broker_submission_authorized": False,
            "exchange_order_authorized": False,
            "capital_movement_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }


def validate_contract(value: object) -> dict:
    expected = _expected_contract()
    if not _exact(value, expected):
        raise Stage5PaperEnvelopeError("CONTRACT_FIELDS_OR_AUTHORITY_INVALID")
    return copy.deepcopy(expected)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(path))


def _decision_unsigned(value: dict) -> dict:
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256", None)
    return unsigned


def validate_decision(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else validate_contract(contract)
    fields = {
        "schema_version", "decision_id", "status", "candle_open_at", "candle_closed_at",
        "available_at", "decided_at", "source_ref", "source_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise Stage5PaperEnvelopeError("DECISION_FIELDS_INVALID")
    if value.get("schema_version") != contract["input_schema_version"]:
        raise Stage5PaperEnvelopeError("DECISION_SCHEMA_INVALID")
    if value.get("status") not in contract["decision_statuses"]:
        raise Stage5PaperEnvelopeError("DECISION_STATUS_INVALID")
    if not _exact(value.get("authority"), contract["authority"]):
        raise Stage5PaperEnvelopeError("DECISION_AUTHORITY_ESCALATION")
    _text(value.get("decision_id"), "DECISION_ID_INVALID")
    _text(value.get("source_ref"), "DECISION_SOURCE_REF_INVALID")
    _sha(value.get("source_sha256"), "DECISION_SOURCE_SHA_INVALID")
    open_at = _utc(value.get("candle_open_at"), "DECISION_CANDLE_OPEN_INVALID")
    close_at = _utc(value.get("candle_closed_at"), "DECISION_CANDLE_CLOSE_INVALID")
    available_at = _utc(value.get("available_at"), "DECISION_AVAILABLE_AT_INVALID")
    decided_at = _utc(value.get("decided_at"), "DECISION_DECIDED_AT_INVALID")
    if not open_at < close_at <= available_at <= decided_at:
        raise Stage5PaperEnvelopeError("DECISION_PIT_ORDER_INVALID")
    digest = _sha(value.get("packet_sha256"), "DECISION_PACKET_SHA_INVALID")
    if payload_sha256(_decision_unsigned(value)) != digest:
        raise Stage5PaperEnvelopeError("DECISION_PACKET_SHA_MISMATCH")
    return copy.deepcopy(value)


def validate_envelope(value: object, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else validate_contract(contract)
    fields = {"schema_version", "contract_version", "mode", "envelope_id", "decision", "plan", "snapshot", "authority", "packet_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise Stage5PaperEnvelopeError("ENVELOPE_FIELDS_INVALID")
    if value.get("schema_version") != contract["input_schema_version"] or value.get("contract_version") != contract["contract_version"] or value.get("mode") != contract["mode"]:
        raise Stage5PaperEnvelopeError("ENVELOPE_IDENTITY_INVALID")
    if not _exact(value.get("authority"), contract["authority"]):
        raise Stage5PaperEnvelopeError("ENVELOPE_AUTHORITY_ESCALATION")
    _text(value.get("envelope_id"), "ENVELOPE_ID_INVALID")
    decision = validate_decision(value.get("decision"), contract)
    plan = value.get("plan")
    plan_fields = {
        "plan_id", "ledger_id", "initial_cash", "opened_at", "opening_idempotency_key",
        "order_id", "submit_idempotency_key", "match_idempotency_key", "side", "order_type",
        "quantity", "limit_price", "fee_rate", "queue_fraction", "submitted_at", "expires_at",
        "match_at", "mark_price", "account_observed_at",
    }
    if not isinstance(plan, dict) or set(plan) != plan_fields:
        raise Stage5PaperEnvelopeError("PLAN_FIELDS_INVALID")
    for name in ("plan_id", "ledger_id", "opening_idempotency_key", "order_id", "submit_idempotency_key", "match_idempotency_key"):
        try:
            SIMULATOR._identifier(plan.get(name), f"PLAN_{name.upper()}_INVALID")
        except SIMULATOR.CryptoPaperSimulatorError as exc:
            raise Stage5PaperEnvelopeError(f"PLAN_{name.upper()}_INVALID") from exc
    if len({plan["opening_idempotency_key"], plan["submit_idempotency_key"], plan["match_idempotency_key"]}) != 3:
        raise Stage5PaperEnvelopeError("PLAN_IDEMPOTENCY_KEYS_NOT_DISTINCT")
    try:
        SIMULATOR._decimal(plan.get("initial_cash"), "PLAN_INITIAL_CASH_INVALID", positive=True)
        SIMULATOR._decimal(plan.get("quantity"), "PLAN_QUANTITY_INVALID", positive=True)
        SIMULATOR._decimal(plan.get("fee_rate"), "PLAN_FEE_RATE_INVALID", maximum=SIMULATOR.Decimal("1"))
        SIMULATOR._decimal(plan.get("queue_fraction"), "PLAN_QUEUE_FRACTION_INVALID", positive=True, maximum=SIMULATOR.Decimal("1"))
        SIMULATOR._decimal(plan.get("mark_price"), "PLAN_MARK_PRICE_INVALID", positive=True)
    except SIMULATOR.CryptoPaperSimulatorError as exc:
        raise Stage5PaperEnvelopeError("PLAN_DECIMAL_INVALID") from exc
    if plan.get("side") not in SIMULATOR.load_contract()["supported_sides"] or plan.get("order_type") not in SIMULATOR.load_contract()["supported_order_types"]:
        raise Stage5PaperEnvelopeError("PLAN_ORDER_SHAPE_INVALID")
    if plan["order_type"] == "LIMIT":
        try:
            SIMULATOR._decimal(plan.get("limit_price"), "PLAN_LIMIT_INVALID", positive=True)
        except SIMULATOR.CryptoPaperSimulatorError as exc:
            raise Stage5PaperEnvelopeError("PLAN_LIMIT_INVALID") from exc
    elif plan.get("limit_price") is not None:
        raise Stage5PaperEnvelopeError("PLAN_MARKET_LIMIT_FORBIDDEN")
    opened = _utc(plan.get("opened_at"), "PLAN_OPENED_AT_INVALID")
    submitted = _utc(plan.get("submitted_at"), "PLAN_SUBMITTED_AT_INVALID")
    expires = _utc(plan.get("expires_at"), "PLAN_EXPIRES_AT_INVALID")
    match_at = _utc(plan.get("match_at"), "PLAN_MATCH_AT_INVALID")
    observed = _utc(plan.get("account_observed_at"), "PLAN_ACCOUNT_OBSERVED_AT_INVALID")
    snapshot = SIMULATOR.validate_snapshot(value.get("snapshot"))
    captured = _utc(snapshot["captured_at"], "SNAPSHOT_CAPTURED_AT_INVALID")
    if captured <= _utc(decision["candle_closed_at"], "DECISION_CANDLE_CLOSE_INVALID"):
        raise Stage5PaperEnvelopeError("SAME_CANDLE_EXECUTION_FORBIDDEN")
    if not (
        opened <= _utc(decision["decided_at"], "DECISION_DECIDED_AT_INVALID")
        < submitted <= captured <= match_at < expires
        and match_at <= observed
    ):
        raise Stage5PaperEnvelopeError("PLAN_PIT_ORDER_INVALID")
    digest = _sha(value.get("packet_sha256"), "ENVELOPE_PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise Stage5PaperEnvelopeError("ENVELOPE_PACKET_SHA_MISMATCH")
    return copy.deepcopy(value)


def _derive(envelope: dict, contract: dict) -> dict:
    checked = validate_envelope(envelope, contract)
    decision, plan, snapshot = checked["decision"], checked["plan"], checked["snapshot"]
    intent = SIMULATOR.build_intent(
        order_id=plan["order_id"], idempotency_key=plan["submit_idempotency_key"],
        market=snapshot["market"], side=plan["side"], order_type=plan["order_type"],
        quantity=plan["quantity"], limit_price=plan["limit_price"], fee_rate=plan["fee_rate"],
        queue_fraction=plan["queue_fraction"], submitted_at=plan["submitted_at"], expires_at=plan["expires_at"],
        market_regime_status=decision["status"], source_plan_ref=f"stage4://decision/{decision['decision_id']}",
        source_plan_sha256=decision["packet_sha256"], source_evidence_ref=snapshot["source_ref"],
        source_evidence_sha256=snapshot["source_sha256"],
    )
    ledger = SIMULATOR.create_ledger(ledger_id=plan["ledger_id"], initial_cash=plan["initial_cash"], opened_at=plan["opened_at"], idempotency_key=plan["opening_idempotency_key"])
    ledger = SIMULATOR.submit_order(ledger, intent)
    ledger = SIMULATOR.match_order(ledger, order_id=intent["order_id"], snapshot=snapshot, event_at=plan["match_at"], idempotency_key=plan["match_idempotency_key"])
    fill_event = ledger["events"][-1]
    if fill_event["event_type"] != "FILL_APPLIED":
        raise Stage5PaperEnvelopeError("FIXTURE_MUST_PRODUCE_VIRTUAL_FILL")
    account = SIMULATOR.build_account_state(
        ledger, observed_at=plan["account_observed_at"], mark_prices={snapshot["market"]: plan["mark_price"]},
        mark_freshness_status="FRESH", mark_source_ref=f"stage5://mark/{snapshot['snapshot_id']}", mark_source_sha256=snapshot["packet_sha256"],
    )
    order = next(row for row in account["orders"] if row["order_id"] == intent["order_id"])
    position = next((row for row in account["positions"] if row["market"] == snapshot["market"]), None)
    if order["filled_quantity"] != fill_event["payload"]["filled_quantity"] or position is None:
        raise Stage5PaperEnvelopeError("PLAN_FILL_BALANCE_RECONCILIATION_FAILED")
    result = {
        "schema_version": contract["output_schema_version"], "contract_version": contract["contract_version"], "mode": contract["mode"],
        "envelope_id": checked["envelope_id"],
        "source": {"envelope_sha256": checked["packet_sha256"], "decision_id": decision["decision_id"], "decision_packet_sha256": decision["packet_sha256"], "decision_source_sha256": decision["source_sha256"]},
        "virtual_plan": intent, "virtual_fill_event": fill_event, "ledger": ledger, "account_state": account,
        "reconciliation": {"status": "RECONCILED", "order_id": intent["order_id"], "plan_sha256": intent["packet_sha256"], "fill_event_sha256": fill_event["event_sha256"], "ledger_sha256": ledger["packet_sha256"], "account_state_sha256": account["packet_sha256"], "filled_quantity": order["filled_quantity"], "position_quantity": position["quantity"], "cash": account["cash"]},
        "authority": copy.deepcopy(contract["authority"]),
    }
    result["packet_sha256"] = payload_sha256(result)
    return result


def build_result(envelope: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else validate_contract(contract)
    return validate_result(_derive(envelope, contract), envelope, contract)


def validate_result(value: object, envelope: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else validate_contract(contract)
    expected = _derive(envelope, contract)
    if not _exact(value, expected):
        raise Stage5PaperEnvelopeError("RESULT_SEMANTIC_TAMPER_OR_REPLAY_DRIFT")
    return copy.deepcopy(expected)
