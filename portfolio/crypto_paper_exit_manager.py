#!/usr/bin/env python3
"""P7-13 deterministic Crypto PAPER exit and position-management review."""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "crypto_paper_exit_manager_contract.json"
SIMULATOR_PATH = ROOT / "shadow" / "crypto_paper_simulator.py"
SIM_SPEC = importlib.util.spec_from_file_location("p713_crypto_paper_simulator", SIMULATOR_PATH)
SIMULATOR = importlib.util.module_from_spec(SIM_SPEC)
assert SIM_SPEC.loader is not None
SIM_SPEC.loader.exec_module(SIMULATOR)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,20}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class CryptoPaperExitManagerError(ValueError):
    """Fail-closed P7-13 contract or derivation violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperExitManagerError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_paper_exit_manager/1",
        "plan_schema_version": "crypto_paper_exit_plan/1",
        "observation_schema_version": "crypto_paper_exit_observation/1",
        "output_schema_version": "crypto_paper_exit_decision/1",
        "source_account_schema_version": "crypto_paper_account_state/1",
        "source_account_contract_version": "crypto_paper_simulator/1",
        "mode": "PAPER_LAB_ONLY",
        "priority_categories": [
            "HARD_EXIT", "SECURITY_LIQUIDITY", "RISK_REGIME", "TREND",
            "PROFIT_TRAIL", "TIME_REVIEW",
        ],
        "conditions": [
            "KILL_SWITCH_TRIGGERED", "SECURITY_BLOCKED", "LIQUIDITY_BLOCKED",
            "RISK_BUDGET_BREACH", "REGIME_FAIL", "TREND_BROKEN",
            "PRICE_AT_OR_BELOW", "PRICE_AT_OR_ABOVE",
            "DRAWDOWN_FROM_PRIOR_HIGH_AT_OR_ABOVE", "TIME_AT_OR_AFTER",
        ],
        "actions": ["HOLD", "REDUCE", "HARVEST_PARTIAL", "TRAIL", "EXIT_REVIEW"],
        "quantity_actions": ["REDUCE", "HARVEST_PARTIAL", "EXIT_REVIEW"],
        "signal_vocabularies": {
            "kill_switch": ["TRIGGERED", "CLEAR", "UNKNOWN"],
            "security": ["BLOCKED", "CLEAR", "UNKNOWN"],
            "liquidity": ["BLOCKED", "CLEAR", "UNKNOWN"],
            "risk_budget": ["BREACH", "CLEAR", "UNKNOWN"],
            "regime": ["FAIL", "PASS", "UNKNOWN", "NOT_EVALUATED"],
            "trend": ["BROKEN", "INTACT", "UNKNOWN"],
        },
        "condition_threshold_policy": {
            "NONE": [
                "KILL_SWITCH_TRIGGERED", "SECURITY_BLOCKED", "LIQUIDITY_BLOCKED",
                "RISK_BUDGET_BREACH", "REGIME_FAIL", "TREND_BROKEN",
            ],
            "POSITIVE_PRICE": ["PRICE_AT_OR_BELOW", "PRICE_AT_OR_ABOVE"],
            "FRACTION_0_TO_1": ["DRAWDOWN_FROM_PRIOR_HIGH_AT_OR_ABOVE"],
            "UTC_TIMESTAMP": ["TIME_AT_OR_AFTER"],
        },
        "unknown_policy": "FIRST_UNRESOLVED_PLANNED_TRIGGER_WAITS_FAIL_CLOSED",
        "high_watermark_policy": "EVALUATE_AGAINST_PRIOR_THEN_ADVANCE_WITH_CURRENT_PRICE",
        "duplicate_trigger_policy": "DETERMINISTIC_PAPER_ORDER_ID_ALREADY_PRESENT_MEANS_ALREADY_APPLIED",
        "plan_authority": {
            "paper_exit_plan_only": True,
            "investment_policy_ratified": False,
            "live_exit_authorized": False,
            "quantity_authorized": False,
            "exchange_order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "observation_authority": {
            "paper_exit_observation_only": True,
            "market_judgment_authorized": False,
            "live_exit_authorized": False,
            "exchange_order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "paper_exit_review_only": True,
            "market_judgment_authorized": False,
            "investment_eligibility_authorized": False,
            "live_exit_authorized": False,
            "live_quantity_authorized": False,
            "action_authorized": False,
            "exchange_order_authorized": False,
            "broker_submission_authorized": False,
            "withdrawal_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CryptoPaperExitManagerError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CryptoPaperExitManagerError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CryptoPaperExitManagerError(code)
    return value


def _identifier(value, code: str) -> str:
    value = _text(value, code)
    if ID_RE.fullmatch(value) is None:
        raise CryptoPaperExitManagerError(code)
    return value


def _market(value, code: str = "MARKET_INVALID") -> str:
    if not isinstance(value, str) or MARKET_RE.fullmatch(value) is None:
        raise CryptoPaperExitManagerError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CryptoPaperExitManagerError(code)
    return value


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CryptoPaperExitManagerError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoPaperExitManagerError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CryptoPaperExitManagerError(code)
    return parsed


def _format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise CryptoPaperExitManagerError("DECIMAL_NON_FINITE")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _decimal(value, code: str, *, positive: bool = False, maximum: Decimal | None = None) -> Decimal:
    if not isinstance(value, str):
        raise CryptoPaperExitManagerError(code)
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CryptoPaperExitManagerError(code) from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise CryptoPaperExitManagerError(code)
    if value != _format_decimal(parsed):
        raise CryptoPaperExitManagerError(f"{code}:NON_CANONICAL")
    if max(0, -parsed.as_tuple().exponent) > 18:
        raise CryptoPaperExitManagerError(f"{code}:SCALE_EXCEEDED")
    if maximum is not None and parsed > maximum:
        raise CryptoPaperExitManagerError(code)
    return parsed


def _floor(value: Decimal, scale: int = 18) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + scale + 10)
        return value.quantize(quantum, rounding=ROUND_DOWN)


def _with_sha(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["packet_sha256"] = payload_sha256(result)
    return result


def _entry_order(account: dict, order_id: str) -> dict:
    rows = [row for row in account["orders"] if row["order_id"] == order_id]
    if len(rows) != 1:
        raise CryptoPaperExitManagerError("SOURCE_ENTRY_ORDER_NOT_FOUND")
    order = rows[0]
    if order["side"] != "BUY" or Decimal(order["filled_quantity"]) <= 0:
        raise CryptoPaperExitManagerError("SOURCE_ENTRY_ORDER_NOT_FILLED_BUY")
    return order


def _condition_threshold_kind(condition: str, contract: dict) -> str:
    for kind, conditions in contract["condition_threshold_policy"].items():
        if condition in conditions:
            return kind
    raise CryptoPaperExitManagerError(f"CONDITION_THRESHOLD_POLICY_MISSING:{condition}")


def _validate_trigger(value: dict, index: int, contract: dict) -> dict:
    fields = {
        "trigger_id", "category", "condition", "threshold", "action",
        "quantity_fraction", "paper_order_id", "paper_order_idempotency_key",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperExitManagerError(f"TRIGGER_FIELDS_MISMATCH:{index}")
    category = value.get("category")
    condition = value.get("condition")
    action = value.get("action")
    if category not in contract["priority_categories"]:
        raise CryptoPaperExitManagerError(f"TRIGGER_CATEGORY_INVALID:{index}")
    if condition not in contract["conditions"]:
        raise CryptoPaperExitManagerError(f"TRIGGER_CONDITION_INVALID:{index}")
    if action not in contract["actions"]:
        raise CryptoPaperExitManagerError(f"TRIGGER_ACTION_INVALID:{index}")
    threshold = value.get("threshold")
    threshold_kind = _condition_threshold_kind(condition, contract)
    if threshold_kind == "NONE":
        if threshold is not None:
            raise CryptoPaperExitManagerError(f"TRIGGER_THRESHOLD_MUST_BE_NULL:{index}")
    elif threshold_kind == "POSITIVE_PRICE":
        threshold = _format_decimal(_decimal(threshold, f"TRIGGER_PRICE_INVALID:{index}", positive=True))
    elif threshold_kind == "FRACTION_0_TO_1":
        threshold = _format_decimal(
            _decimal(threshold, f"TRIGGER_FRACTION_THRESHOLD_INVALID:{index}", positive=True, maximum=Decimal("1"))
        )
    elif threshold_kind == "UTC_TIMESTAMP":
        _utc(threshold, f"TRIGGER_TIME_INVALID:{index}")

    quantity_fraction = value.get("quantity_fraction")
    paper_order_id = value.get("paper_order_id")
    paper_key = value.get("paper_order_idempotency_key")
    if action in contract["quantity_actions"]:
        quantity_fraction = _format_decimal(
            _decimal(quantity_fraction, f"TRIGGER_QUANTITY_FRACTION_INVALID:{index}", positive=True, maximum=Decimal("1"))
        )
        paper_order_id = _identifier(paper_order_id, f"TRIGGER_PAPER_ORDER_ID_INVALID:{index}")
        paper_key = _identifier(paper_key, f"TRIGGER_PAPER_KEY_INVALID:{index}")
    elif quantity_fraction is not None or paper_order_id is not None or paper_key is not None:
        raise CryptoPaperExitManagerError(f"NON_QUANTITY_TRIGGER_ORDER_IDENTITY_FORBIDDEN:{index}")
    return {
        "trigger_id": _identifier(value.get("trigger_id"), f"TRIGGER_ID_INVALID:{index}"),
        "category": category,
        "condition": condition,
        "threshold": threshold,
        "action": action,
        "quantity_fraction": quantity_fraction,
        "paper_order_id": paper_order_id,
        "paper_order_idempotency_key": paper_key,
    }


def build_exit_plan(
    *, plan_id: str, market: str, source_entry_order_id: str,
    created_at: str, triggers: list[dict], source_entry_account: dict,
    source_entry_plan_ref: str, source_entry_plan_sha256: str,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    account = SIMULATOR.validate_account_state(source_entry_account)
    order = _entry_order(account, source_entry_order_id)
    if market != order["market"]:
        raise CryptoPaperExitManagerError("PLAN_MARKET_ENTRY_ORDER_MISMATCH")
    if created_at != account["observed_at"]:
        raise CryptoPaperExitManagerError("PLAN_CREATED_AT_MUST_EQUAL_ENTRY_ACCOUNT_OBSERVED_AT")
    quantity = Decimal(order["filled_quantity"])
    entry_price = _floor(Decimal(order["gross_value"]) / quantity)
    value = {
        "schema_version": contract["plan_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "plan_id": plan_id,
        "market": market,
        "source_entry_order_id": source_entry_order_id,
        "initial_quantity": _format_decimal(quantity),
        "entry_price": _format_decimal(entry_price),
        "created_at": created_at,
        "triggers": copy.deepcopy(triggers),
        "source_entry_plan_ref": source_entry_plan_ref,
        "source_entry_plan_sha256": source_entry_plan_sha256,
        "source_entry_account": account,
        "authority": copy.deepcopy(contract["plan_authority"]),
    }
    return validate_exit_plan(_with_sha(value), contract)


def validate_exit_plan(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "mode", "plan_id", "market",
        "source_entry_order_id", "initial_quantity", "entry_price", "created_at",
        "triggers", "source_entry_plan_ref", "source_entry_plan_sha256",
        "source_entry_account", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperExitManagerError("PLAN_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["plan_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("mode") != contract["mode"]
        or value.get("authority") != contract["plan_authority"]
    ):
        raise CryptoPaperExitManagerError("PLAN_IDENTITY_INVALID")
    account = SIMULATOR.validate_account_state(value.get("source_entry_account"))
    if account.get("schema_version") != contract["source_account_schema_version"]:
        raise CryptoPaperExitManagerError("PLAN_SOURCE_ACCOUNT_SCHEMA_INVALID")
    plan_id = _identifier(value.get("plan_id"), "PLAN_ID_INVALID")
    market = _market(value.get("market"))
    order_id = _identifier(value.get("source_entry_order_id"), "SOURCE_ENTRY_ORDER_ID_INVALID")
    order = _entry_order(account, order_id)
    if order["market"] != market:
        raise CryptoPaperExitManagerError("PLAN_MARKET_ENTRY_ORDER_MISMATCH")
    if value.get("created_at") != account["observed_at"]:
        raise CryptoPaperExitManagerError("PLAN_CREATED_AT_MUST_EQUAL_ENTRY_ACCOUNT_OBSERVED_AT")
    _utc(value.get("created_at"), "PLAN_CREATED_AT_INVALID")
    expected_quantity = Decimal(order["filled_quantity"])
    expected_entry_price = _floor(Decimal(order["gross_value"]) / expected_quantity)
    if _decimal(value.get("initial_quantity"), "PLAN_INITIAL_QUANTITY_INVALID", positive=True) != expected_quantity:
        raise CryptoPaperExitManagerError("PLAN_INITIAL_QUANTITY_ENTRY_ORDER_MISMATCH")
    if _decimal(value.get("entry_price"), "PLAN_ENTRY_PRICE_INVALID", positive=True) != expected_entry_price:
        raise CryptoPaperExitManagerError("PLAN_ENTRY_PRICE_ENTRY_ORDER_MISMATCH")
    raw_triggers = value.get("triggers")
    if not isinstance(raw_triggers, list) or not raw_triggers:
        raise CryptoPaperExitManagerError("PLAN_TRIGGERS_EMPTY")
    triggers = [_validate_trigger(row, index, contract) for index, row in enumerate(raw_triggers)]
    category_rank = {name: rank for rank, name in enumerate(contract["priority_categories"])}
    if [category_rank[row["category"]] for row in triggers] != sorted(category_rank[row["category"]] for row in triggers):
        raise CryptoPaperExitManagerError("PLAN_TRIGGER_PRIORITY_ORDER_INVALID")
    ids = [row["trigger_id"] for row in triggers]
    if len(ids) != len(set(ids)):
        raise CryptoPaperExitManagerError("PLAN_TRIGGER_ID_DUPLICATE")
    paper_ids = [row["paper_order_id"] for row in triggers if row["paper_order_id"] is not None]
    paper_keys = [row["paper_order_idempotency_key"] for row in triggers if row["paper_order_idempotency_key"] is not None]
    if len(paper_ids) != len(set(paper_ids)) or len(paper_keys) != len(set(paper_keys)):
        raise CryptoPaperExitManagerError("PLAN_PAPER_ORDER_IDENTITY_DUPLICATE")
    normalized = copy.deepcopy(value)
    normalized.update({
        "plan_id": plan_id,
        "market": market,
        "source_entry_order_id": order_id,
        "initial_quantity": _format_decimal(expected_quantity),
        "entry_price": _format_decimal(expected_entry_price),
        "triggers": triggers,
        "source_entry_plan_ref": _text(value.get("source_entry_plan_ref"), "SOURCE_ENTRY_PLAN_REF_INVALID"),
        "source_entry_plan_sha256": _sha(value.get("source_entry_plan_sha256"), "SOURCE_ENTRY_PLAN_SHA_INVALID"),
        "source_entry_account": account,
    })
    digest = _sha(value.get("packet_sha256"), "PLAN_PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(normalized)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperExitManagerError("PLAN_PACKET_SHA_MISMATCH")
    normalized["packet_sha256"] = digest
    return normalized


def build_observation(
    *, observation_id: str, market: str, observed_at: str, current_price: str,
    prior_high_watermark: str, freshness_status: str, signals: dict,
    source_ref: str, source_sha256: str, contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    value = {
        "schema_version": contract["observation_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "observation_id": observation_id,
        "market": market,
        "observed_at": observed_at,
        "current_price": current_price,
        "prior_high_watermark": prior_high_watermark,
        "freshness_status": freshness_status,
        "signals": copy.deepcopy(signals),
        "source_ref": source_ref,
        "source_sha256": source_sha256,
        "authority": copy.deepcopy(contract["observation_authority"]),
    }
    return validate_observation(_with_sha(value), contract)


def validate_observation(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "mode", "observation_id", "market",
        "observed_at", "current_price", "prior_high_watermark", "freshness_status",
        "signals", "source_ref", "source_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperExitManagerError("OBSERVATION_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["observation_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("mode") != contract["mode"]
        or value.get("authority") != contract["observation_authority"]
    ):
        raise CryptoPaperExitManagerError("OBSERVATION_IDENTITY_INVALID")
    freshness = value.get("freshness_status")
    if freshness not in {"FRESH", "STALE", "UNKNOWN"}:
        raise CryptoPaperExitManagerError("OBSERVATION_FRESHNESS_INVALID")
    signals = value.get("signals")
    if not isinstance(signals, dict) or set(signals) != set(contract["signal_vocabularies"]):
        raise CryptoPaperExitManagerError("OBSERVATION_SIGNAL_FIELDS_MISMATCH")
    for key, allowed in contract["signal_vocabularies"].items():
        if signals.get(key) not in allowed:
            raise CryptoPaperExitManagerError(f"OBSERVATION_SIGNAL_INVALID:{key}")
    normalized = copy.deepcopy(value)
    normalized.update({
        "observation_id": _identifier(value.get("observation_id"), "OBSERVATION_ID_INVALID"),
        "market": _market(value.get("market")),
        "current_price": _format_decimal(_decimal(value.get("current_price"), "CURRENT_PRICE_INVALID", positive=True)),
        "prior_high_watermark": _format_decimal(
            _decimal(value.get("prior_high_watermark"), "PRIOR_HIGH_WATERMARK_INVALID", positive=True)
        ),
        "source_ref": _text(value.get("source_ref"), "OBSERVATION_SOURCE_REF_INVALID"),
        "source_sha256": _sha(value.get("source_sha256"), "OBSERVATION_SOURCE_SHA_INVALID"),
    })
    _utc(value.get("observed_at"), "OBSERVATION_AT_INVALID")
    digest = _sha(value.get("packet_sha256"), "OBSERVATION_PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(normalized)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperExitManagerError("OBSERVATION_PACKET_SHA_MISMATCH")
    normalized["packet_sha256"] = digest
    return normalized


def _condition_result(trigger: dict, observation: dict) -> bool | None:
    condition = trigger["condition"]
    signals = observation["signals"]
    signal_map = {
        "KILL_SWITCH_TRIGGERED": ("kill_switch", "TRIGGERED", {"UNKNOWN"}),
        "SECURITY_BLOCKED": ("security", "BLOCKED", {"UNKNOWN"}),
        "LIQUIDITY_BLOCKED": ("liquidity", "BLOCKED", {"UNKNOWN"}),
        "RISK_BUDGET_BREACH": ("risk_budget", "BREACH", {"UNKNOWN"}),
        "REGIME_FAIL": ("regime", "FAIL", {"UNKNOWN", "NOT_EVALUATED"}),
        "TREND_BROKEN": ("trend", "BROKEN", {"UNKNOWN"}),
    }
    if condition in signal_map:
        field, positive, unknowns = signal_map[condition]
        if signals[field] in unknowns:
            return None
        return signals[field] == positive
    current = Decimal(observation["current_price"])
    if condition == "PRICE_AT_OR_BELOW":
        return current <= Decimal(trigger["threshold"])
    if condition == "PRICE_AT_OR_ABOVE":
        return current >= Decimal(trigger["threshold"])
    if condition == "DRAWDOWN_FROM_PRIOR_HIGH_AT_OR_ABOVE":
        prior = Decimal(observation["prior_high_watermark"])
        drawdown = max(Decimal("0"), (prior - current) / prior)
        return drawdown >= Decimal(trigger["threshold"])
    if condition == "TIME_AT_OR_AFTER":
        return _utc(observation["observed_at"], "OBSERVATION_AT_INVALID") >= _utc(
            trigger["threshold"], "TRIGGER_TIME_INVALID"
        )
    raise CryptoPaperExitManagerError(f"CONDITION_UNIMPLEMENTED:{condition}")


def _current_position(account: dict, market: str) -> dict | None:
    rows = [row for row in account["positions"] if row["market"] == market]
    if len(rows) > 1:
        raise CryptoPaperExitManagerError("CURRENT_POSITION_DUPLICATE")
    return rows[0] if rows else None


def _assemble(plan: dict, account: dict, observation: dict, contract: dict) -> dict:
    if plan["market"] != observation["market"]:
        raise CryptoPaperExitManagerError("PLAN_OBSERVATION_MARKET_MISMATCH")
    entry_ledger = plan["source_entry_account"]["source_ledger"]
    current_ledger = account["source_ledger"]
    if entry_ledger["ledger_id"] != current_ledger["ledger_id"]:
        raise CryptoPaperExitManagerError("CURRENT_ACCOUNT_LEDGER_ID_MISMATCH")
    if current_ledger["events"][: len(entry_ledger["events"])] != entry_ledger["events"]:
        raise CryptoPaperExitManagerError("CURRENT_ACCOUNT_NOT_DESCENDANT_OF_ENTRY_ACCOUNT")
    if account["observed_at"] != observation["observed_at"]:
        raise CryptoPaperExitManagerError("ACCOUNT_OBSERVATION_TIME_MISMATCH")
    if _utc(observation["observed_at"], "OBSERVATION_AT_INVALID") < _utc(plan["created_at"], "PLAN_CREATED_AT_INVALID"):
        raise CryptoPaperExitManagerError("OBSERVATION_PRECEDES_PLAN")
    if account["source"]["mark_source_sha256"] != observation["source_sha256"]:
        raise CryptoPaperExitManagerError("ACCOUNT_OBSERVATION_SOURCE_MISMATCH")
    position = _current_position(account, plan["market"])
    if position is not None and position["mark_price"] != observation["current_price"]:
        raise CryptoPaperExitManagerError("ACCOUNT_OBSERVATION_PRICE_MISMATCH")
    current = Decimal(observation["current_price"])
    prior_high = Decimal(observation["prior_high_watermark"])
    if prior_high < Decimal(plan["entry_price"]):
        raise CryptoPaperExitManagerError("PRIOR_HIGH_WATERMARK_BELOW_ENTRY")
    next_high = max(prior_high, current)

    status = "NO_TRIGGER_HOLD"
    action = "HOLD"
    selected = None
    target_quantity = "0"
    order_identity = None
    blockers = []

    if observation["freshness_status"] != "FRESH":
        status = "WAIT_STALE_EVIDENCE"
        action = None
        target_quantity = None
        blockers = [f"OBSERVATION_{observation['freshness_status']}"]
    elif position is None or Decimal(position["quantity"]) <= 0:
        status = "WAIT_POSITION_UNAVAILABLE"
        action = None
        target_quantity = None
        blockers = ["PAPER_POSITION_UNAVAILABLE"]
    else:
        current_quantity = Decimal(position["quantity"])
        existing_order_ids = {row["order_id"] for row in account["orders"]}
        for trigger in plan["triggers"]:
            result = _condition_result(trigger, observation)
            if result is None:
                status = "WAIT_UNKNOWN_EVIDENCE"
                action = None
                target_quantity = None
                blockers = [f"PLANNED_TRIGGER_INPUT_UNKNOWN:{trigger['trigger_id']}"]
                break
            if not result:
                continue
            selected = trigger["trigger_id"]
            action = trigger["action"]
            if trigger["action"] in contract["quantity_actions"]:
                if trigger["paper_order_id"] in existing_order_ids:
                    status = "TRIGGER_ALREADY_APPLIED"
                    action = None
                    target_quantity = None
                    blockers = [f"PAPER_ORDER_ALREADY_PRESENT:{trigger['paper_order_id']}"]
                else:
                    requested = _floor(Decimal(plan["initial_quantity"]) * Decimal(trigger["quantity_fraction"]))
                    target_quantity = _format_decimal(min(requested, current_quantity))
                    status = "TRIGGER_SELECTED_REVIEW_ONLY"
                    order_identity = {
                        "order_id": trigger["paper_order_id"],
                        "idempotency_key": trigger["paper_order_idempotency_key"],
                        "side": "SELL",
                        "market": plan["market"],
                    }
            else:
                status = "TRIGGER_SELECTED_REVIEW_ONLY"
                target_quantity = "0"
            break

    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "status": status,
        "plan_id": plan["plan_id"],
        "market": plan["market"],
        "observed_at": observation["observed_at"],
        "action": action,
        "selected_trigger_id": selected,
        "target_quantity": target_quantity,
        "paper_order_identity_candidate": order_identity,
        "human_review_required": True,
        "prior_high_watermark": observation["prior_high_watermark"],
        "next_high_watermark": _format_decimal(next_high),
        "blockers": blockers,
        "source_packets": {
            "exit_plan": copy.deepcopy(plan),
            "current_account": copy.deepcopy(account),
            "observation": copy.deepcopy(observation),
        },
        "lineage": {
            "exit_plan_sha256": plan["packet_sha256"],
            "current_account_sha256": account["packet_sha256"],
            "observation_sha256": observation["packet_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
    }
    return _with_sha(packet)


def evaluate_exit(
    exit_plan: dict, current_account: dict, observation: dict,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    plan = validate_exit_plan(exit_plan, contract)
    account = SIMULATOR.validate_account_state(current_account)
    observed = validate_observation(observation, contract)
    return validate_output(_assemble(plan, account, observed, contract), contract)


def validate_output(value: dict, contract: dict | None = None) -> dict:
    """Revalidate all embedded packets and fully rebuild the exit decision."""
    contract = load_contract() if contract is None else _validate_contract(contract)
    fields = {
        "schema_version", "contract_version", "mode", "status", "plan_id", "market",
        "observed_at", "action", "selected_trigger_id", "target_quantity",
        "paper_order_identity_candidate", "human_review_required", "prior_high_watermark",
        "next_high_watermark", "blockers", "source_packets", "lineage", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperExitManagerError("OUTPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["output_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("mode") != contract["mode"]
        or value.get("authority") != contract["authority"]
        or value.get("human_review_required") is not True
    ):
        raise CryptoPaperExitManagerError("OUTPUT_IDENTITY_INVALID")
    digest = _sha(value.get("packet_sha256"), "OUTPUT_PACKET_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperExitManagerError("OUTPUT_PACKET_SHA_MISMATCH")
    sources = value.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {"exit_plan", "current_account", "observation"}:
        raise CryptoPaperExitManagerError("OUTPUT_SOURCE_FIELDS_MISMATCH")
    plan = validate_exit_plan(sources["exit_plan"], contract)
    account = SIMULATOR.validate_account_state(sources["current_account"])
    observation = validate_observation(sources["observation"], contract)
    expected_lineage = {
        "exit_plan_sha256": plan["packet_sha256"],
        "current_account_sha256": account["packet_sha256"],
        "observation_sha256": observation["packet_sha256"],
    }
    if value.get("lineage") != expected_lineage:
        raise CryptoPaperExitManagerError("OUTPUT_LINEAGE_MISMATCH")
    expected = _assemble(plan, account, observation, contract)
    if value != expected:
        raise CryptoPaperExitManagerError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(value)
