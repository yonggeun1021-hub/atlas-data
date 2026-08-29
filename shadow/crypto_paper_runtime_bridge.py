#!/usr/bin/env python3
"""P5/P9 -> P10-11 private-runtime bridge for Crypto PAPER observations.

This module is deliberately offline and side-effect free.  It independently
rebuilds a committed ``crypto_paper_decision_snapshot_packet/1`` from its
exact public source files, then (only when caller-supplied private PAPER
account/economic inputs make P5-09 genuinely eligible) builds P10-11 PAPER
intents and exact public-orderbook snapshots.

The returned request is value-bearing and is therefore PRIVATE-RUNTIME ONLY.
This module never writes it to the public repository, opens a socket, reads a
credential, or calls an exchange endpoint.  Persisting/recovering the ledger
is owned by ``atlas-private-evidence``.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DECISION_PATH = ROOT / "decision" / "crypto_paper_decision_snapshot.py"
SIMULATOR_PATH = ROOT / "shadow" / "crypto_paper_simulator.py"
REALTIME_GATE_PATH = ROOT / "realtime" / "upbit_realtime_gate.py"

REQUEST_SCHEMA_VERSION = "crypto_paper_runtime_request/1"
RUNTIME_CONFIG_SCHEMA_VERSION = "crypto_paper_runtime_config/1"
RUNTIME_CONFIG_APPROVAL = "USER_RATIFIED_PAPER_RUNTIME"
LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION = "upbit_realtime_latest_public_messages/1"
PRIVATE_RUNTIME_MODE = "PRIVATE_RUNTIME_ONLY_DO_NOT_PUBLISH"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{2,20}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

AUTHORITY = {
    "paper_runtime_request_only": True,
    "network_access_authorized": False,
    "credential_access_authorized": False,
    "investment_eligibility_authorized": False,
    "action_authorized": False,
    "exchange_order_authorized": False,
    "broker_submission_authorized": False,
    "withdrawal_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_capital_authorized": False,
}


class CryptoPaperRuntimeBridgeError(ValueError):
    """Fail-closed bridge contract, lineage, or runtime-input violation."""


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoPaperRuntimeBridgeError(f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION = _load("crypto_paper_runtime_decision", DECISION_PATH)
SIMULATOR = _load("crypto_paper_runtime_simulator", SIMULATOR_PATH)
REALTIME = _load("crypto_paper_runtime_realtime", REALTIME_GATE_PATH)
PROMOTION = DECISION.PROMOTION
ELIGIBILITY = DECISION.ELIGIBILITY


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperRuntimeBridgeError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoPaperRuntimeBridgeError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def _safe_repo_path(relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise CryptoPaperRuntimeBridgeError("SOURCE_REF_PATH_INVALID")
    target = (ROOT / relative).resolve()
    if ROOT.resolve() not in target.parents:
        raise CryptoPaperRuntimeBridgeError("SOURCE_REF_PATH_ESCAPE")
    if target.is_symlink() or not target.is_file():
        raise CryptoPaperRuntimeBridgeError(f"SOURCE_REF_FILE_INVALID:{relative}")
    return target


def _require_sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CryptoPaperRuntimeBridgeError(code)
    return value


def _require_sha40(value: object, code: str) -> str:
    if not isinstance(value, str) or SHA40_RE.fullmatch(value) is None:
        raise CryptoPaperRuntimeBridgeError(code)
    return value


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CryptoPaperRuntimeBridgeError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoPaperRuntimeBridgeError(code) from exc
    return parsed


def _format_decimal(value: object, code: str, *, positive: bool = False) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CryptoPaperRuntimeBridgeError(code) from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise CryptoPaperRuntimeBridgeError(code)
    if parsed == 0:
        return "0"
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _assert_all_false_authority(block: object, code: str) -> None:
    if not isinstance(block, dict) or not block or any(value is not False for value in block.values()):
        raise CryptoPaperRuntimeBridgeError(code)


def _source_refs(packet: dict) -> dict[str, dict]:
    refs = packet.get("source_refs")
    if not isinstance(refs, list):
        raise CryptoPaperRuntimeBridgeError("DECISION_SOURCE_REFS_INVALID")
    by_role = {}
    for row in refs:
        if not isinstance(row, dict) or set(row) != {"role", "path", "sha256"}:
            raise CryptoPaperRuntimeBridgeError("DECISION_SOURCE_REF_FIELDS_INVALID")
        role = row.get("role")
        if role in by_role:
            raise CryptoPaperRuntimeBridgeError(f"DECISION_SOURCE_ROLE_DUPLICATE:{role}")
        path = _safe_repo_path(row.get("path"))
        expected = _require_sha256(row.get("sha256"), "DECISION_SOURCE_SHA_INVALID")
        if _file_sha256(path) != expected:
            raise CryptoPaperRuntimeBridgeError(f"DECISION_SOURCE_SHA_MISMATCH:{role}")
        by_role[role] = {"path": path, "record": _read_json(path)}
    return by_role


def _entries(packet: dict) -> dict:
    refs = _source_refs(packet)
    universe_ref = refs.get("upbit_tradeable_universe_packet")
    market_ref = refs.get("upbit_market_evidence_packet")
    realtime_ref = refs.get("upbit_realtime_capture_run")

    universe_entry = None
    if universe_ref is not None:
        record = universe_ref["record"]
        universe_entry = {
            "date": universe_ref["path"].parent.name,
            "path": universe_ref["path"],
            "record": record,
            "packet": record.get("packet") if isinstance(record, dict) else None,
        }
    market_entry = None
    if market_ref is not None:
        market_entry = {
            "date": market_ref["path"].parent.name,
            "path": market_ref["path"],
            "record": market_ref["record"],
        }
    realtime_entry = None
    if realtime_ref is not None:
        realtime_entry = {
            "date": realtime_ref["path"].parent.name,
            "path": realtime_ref["path"],
            "record": realtime_ref["record"],
        }
    return {
        "universe": universe_entry,
        "market_evidence": market_entry,
        "realtime": realtime_entry,
    }


def validate_decision_snapshot(
    value: object, *, expected_source_commit: str | None = None,
) -> dict:
    """Independently rederive a committed PR-#441 decision snapshot."""
    expected_fields = {
        "schema_version", "generated_at", "capture_date", "capture_hhmm",
        "source_commit", "generation_id", "duplicate_guard_key", "source_refs",
        "upbit_universe_snapshot_identity", "finalized_candle_attestation",
        "crypto_regime_five_axis", "funnel_counts", "candidates",
        "freshness_status", "authority", "previous_state_reference",
        "derivation_notes", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise CryptoPaperRuntimeBridgeError("DECISION_FIELDS_INVALID")
    if value.get("schema_version") != DECISION.OUTPUT_SCHEMA_VERSION:
        raise CryptoPaperRuntimeBridgeError("DECISION_SCHEMA_VERSION_INVALID")
    source_commit = _require_sha40(value.get("source_commit"), "DECISION_SOURCE_COMMIT_INVALID")
    if expected_source_commit is not None and source_commit != _require_sha40(
        expected_source_commit, "EXPECTED_SOURCE_COMMIT_INVALID"
    ):
        raise CryptoPaperRuntimeBridgeError("DECISION_SOURCE_COMMIT_MISMATCH")
    digest = _require_sha256(value.get("payload_sha256"), "DECISION_PAYLOAD_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperRuntimeBridgeError("DECISION_PAYLOAD_SHA_MISMATCH")
    _assert_all_false_authority(value.get("authority"), "DECISION_AUTHORITY_INVALID")
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        raise CryptoPaperRuntimeBridgeError("DECISION_CANDIDATES_INVALID")
    for index, row in enumerate(candidates):
        if not isinstance(row, dict):
            raise CryptoPaperRuntimeBridgeError(f"DECISION_CANDIDATE_INVALID:{index}")
        _assert_all_false_authority(
            row.get("authority"), f"DECISION_CANDIDATE_AUTHORITY_INVALID:{index}"
        )

    entries = _entries(value)
    rebuilt = DECISION.build_snapshot(
        generated_at=value["generated_at"],
        source_commit=source_commit,
        universe_entry=entries["universe"],
        market_evidence_entry=entries["market_evidence"],
        realtime_entry=entries["realtime"],
        previous_entry=value["previous_state_reference"],
        component_rows=None,
    )
    if DECISION.canonical_json(rebuilt) != DECISION.canonical_json(value):
        raise CryptoPaperRuntimeBridgeError("DECISION_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


def load_and_validate_decision_snapshot(
    path: Path, *, expected_source_commit: str | None = None,
) -> dict:
    path = Path(path).resolve()
    if ROOT.resolve() not in path.parents or path.is_symlink() or not path.is_file():
        raise CryptoPaperRuntimeBridgeError("DECISION_PATH_INVALID")
    return validate_decision_snapshot(
        _read_json(path), expected_source_commit=expected_source_commit,
    )


def validate_runtime_config(value: object) -> dict:
    fields = {
        "schema_version", "approval_status", "approved_by", "approved_at",
        "ledger_id", "initial_cash_krw", "fee_rate", "queue_fraction",
        "order_type", "limit_price_source", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_FIELDS_INVALID")
    if (
        value.get("schema_version") != RUNTIME_CONFIG_SCHEMA_VERSION
        or value.get("approval_status") != RUNTIME_CONFIG_APPROVAL
        or value.get("authority") != AUTHORITY
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_IDENTITY_INVALID")
    if not isinstance(value.get("approved_by"), str) or not value["approved_by"].strip():
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_APPROVED_BY_INVALID")
    _parse_utc(value.get("approved_at"), "RUNTIME_CONFIG_APPROVED_AT_INVALID")
    if not isinstance(value.get("ledger_id"), str):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_LEDGER_ID_INVALID")
    SIMULATOR._identifier(value["ledger_id"], "RUNTIME_CONFIG_LEDGER_ID_INVALID")
    initial_cash = _format_decimal(value.get("initial_cash_krw"), "RUNTIME_CONFIG_CASH_INVALID", positive=True)
    fee_rate = _format_decimal(value.get("fee_rate"), "RUNTIME_CONFIG_FEE_INVALID")
    queue_fraction = _format_decimal(
        value.get("queue_fraction"), "RUNTIME_CONFIG_QUEUE_INVALID", positive=True
    )
    if Decimal(fee_rate) >= 1 or Decimal(queue_fraction) > 1:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_RATE_RANGE_INVALID")
    order_type = value.get("order_type")
    limit_source = value.get("limit_price_source")
    if order_type not in {"LIMIT", "MARKET"}:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_ORDER_TYPE_INVALID")
    if order_type == "LIMIT" and limit_source not in {"ENTRY_ZONE_LOW", "ENTRY_ZONE_HIGH"}:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_LIMIT_SOURCE_INVALID")
    if order_type == "MARKET" and limit_source is not None:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_MARKET_LIMIT_SOURCE_FORBIDDEN")
    digest = _require_sha256(value.get("packet_sha256"), "RUNTIME_CONFIG_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.update({
        "initial_cash_krw": initial_cash,
        "fee_rate": fee_rate,
        "queue_fraction": queue_fraction,
    })
    unsigned = copy.deepcopy(normalized)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_CONFIG_SHA_MISMATCH")
    normalized["packet_sha256"] = digest
    return normalized


def build_runtime_config(**kwargs) -> dict:
    value = {
        "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION,
        "approval_status": kwargs["approval_status"],
        "approved_by": kwargs["approved_by"],
        "approved_at": kwargs["approved_at"],
        "ledger_id": kwargs["ledger_id"],
        "initial_cash_krw": _format_decimal(kwargs["initial_cash_krw"], "RUNTIME_CONFIG_CASH_INVALID", positive=True),
        "fee_rate": _format_decimal(kwargs["fee_rate"], "RUNTIME_CONFIG_FEE_INVALID"),
        "queue_fraction": _format_decimal(kwargs["queue_fraction"], "RUNTIME_CONFIG_QUEUE_INVALID", positive=True),
        "order_type": kwargs["order_type"],
        "limit_price_source": kwargs.get("limit_price_source"),
        "authority": copy.deepcopy(AUTHORITY),
    }
    value["packet_sha256"] = payload_sha256(value)
    return validate_runtime_config(value)


def paper_account_state_from_ledger(
    account_state: dict, *, open_position_risk: list[dict],
) -> dict:
    checked = SIMULATOR.validate_account_state(account_state)
    total_nav = Decimal(checked["total_nav"])
    if total_nav <= 0:
        raise CryptoPaperRuntimeBridgeError("PAPER_ACCOUNT_NAV_NOT_POSITIVE")
    if not isinstance(open_position_risk, list):
        raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_INVALID")
    risk_by_market = {}
    for row in open_position_risk:
        if not isinstance(row, dict) or set(row) != {"market", "planned_loss_krw"}:
            raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_FIELDS_INVALID")
        market = row.get("market")
        if not isinstance(market, str) or MARKET_RE.fullmatch(market) is None or market in risk_by_market:
            raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_MARKET_INVALID")
        risk_by_market[market] = Decimal(
            _format_decimal(row.get("planned_loss_krw"), "OPEN_POSITION_PLANNED_LOSS_INVALID")
        )
    positions = []
    for position in checked["positions"]:
        market = position["market"]
        if market not in risk_by_market:
            raise CryptoPaperRuntimeBridgeError(f"OPEN_POSITION_RISK_MISSING:{market}")
        positions.append({
            "market": market,
            "planned_loss_nav_fraction": _format_decimal(
                risk_by_market[market] / total_nav,
                "OPEN_POSITION_LOSS_FRACTION_INVALID",
            ),
            "portfolio_weight_nav_fraction": _format_decimal(
                Decimal(position["market_value"]) / total_nav,
                "OPEN_POSITION_WEIGHT_INVALID",
            ),
        })
    extra = sorted(set(risk_by_market) - {row["market"] for row in checked["positions"]})
    if extra:
        raise CryptoPaperRuntimeBridgeError("OPEN_POSITION_RISK_ORPHAN:" + ",".join(extra))
    return {"total_nav_krw": checked["total_nav"], "open_positions": positions}


def position_markets_from_ledger(ledger: dict) -> list[str]:
    """Return markets with positive virtual quantity after exact replay."""
    checked = SIMULATOR.validate_ledger(ledger)
    state = SIMULATOR._replay(checked["events"], SIMULATOR.load_contract())
    return sorted(
        market
        for market, position in state["positions"].items()
        if position["quantity"] > 0
    )


def _realtime_source(decision: dict) -> tuple[Path, dict]:
    refs = _source_refs(decision)
    entry = refs.get("upbit_realtime_capture_run")
    if entry is None:
        raise CryptoPaperRuntimeBridgeError("REALTIME_SOURCE_MISSING")
    record = entry["record"]
    if not isinstance(record, dict) or record.get("schema_version") != "upbit_realtime_capture_run/1":
        raise CryptoPaperRuntimeBridgeError("REALTIME_RECORD_INVALID")
    run = record.get("run")
    if not isinstance(run, dict) or record.get("source_sha256") != payload_sha256(run):
        raise CryptoPaperRuntimeBridgeError("REALTIME_RECORD_SHA_MISMATCH")
    if run.get("latest_public_messages_schema_version") != LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION:
        raise CryptoPaperRuntimeBridgeError("REALTIME_LATEST_PUBLIC_MESSAGES_UNAVAILABLE")
    if not isinstance(run.get("latest_public_messages"), dict):
        raise CryptoPaperRuntimeBridgeError("REALTIME_LATEST_PUBLIC_MESSAGES_INVALID")
    return entry["path"], record


def _latest_public_message(decision: dict, *, market: str, kind: str) -> tuple[dict, Path, dict]:
    path, record = _realtime_source(decision)
    run = record["run"]
    key = f"{kind}|-|{market}"
    row = run["latest_public_messages"].get(key)
    if not isinstance(row, dict) or set(row) != {
        "kind", "timeframe", "market", "received_at", "source_sha256", "raw"
    }:
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_{kind.upper()}_MISSING:{market}")
    if row["kind"] != kind or row["timeframe"] is not None or row["market"] != market:
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_{kind.upper()}_IDENTITY_INVALID:{market}")
    parsed = REALTIME.parse_message(row["raw"])
    if (
        parsed["kind"] != kind
        or parsed["market"] != market
        or parsed["payload_sha256"] != row["source_sha256"]
    ):
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_{kind.upper()}_SHA_MISMATCH:{market}")
    status = run.get("status") or {}
    if status.get("overall_status") != "FRESH":
        raise CryptoPaperRuntimeBridgeError("REALTIME_STATUS_NOT_FRESH")
    market_rows = [item for item in status.get("markets", []) if item.get("market") == market]
    if len(market_rows) != 1:
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_MARKET_STATUS_MISSING:{market}")
    if (market_rows[0].get("freshness_by_kind") or {}).get(kind, {}).get("status") != "FRESH":
        raise CryptoPaperRuntimeBridgeError(f"REALTIME_{kind.upper()}_NOT_FRESH:{market}")
    return row, path, record


def latest_mark_prices(decision: dict, markets: list[str]) -> tuple[dict[str, str], str, str]:
    marks = {}
    source_rows = []
    for market in sorted(set(markets)):
        row, path, _record = _latest_public_message(decision, market=market, kind="ticker")
        marks[market] = _format_decimal(row["raw"].get("trade_price"), "TICKER_PRICE_INVALID", positive=True)
        source_rows.append({"path": str(path.relative_to(ROOT)), "sha256": row["source_sha256"]})
    return marks, "public://upbit/realtime/latest-ticker", payload_sha256(source_rows)


def orderbook_snapshot(decision: dict, *, market: str) -> dict:
    row, path, _record = _latest_public_message(decision, market=market, kind="orderbook")
    raw = row["raw"]
    units = raw.get("orderbook_units")
    if not isinstance(units, list) or not units:
        raise CryptoPaperRuntimeBridgeError(f"ORDERBOOK_UNITS_MISSING:{market}")
    asks = []
    bids = []
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise CryptoPaperRuntimeBridgeError(f"ORDERBOOK_UNIT_INVALID:{market}:{index}")
        asks.append({
            "price": _format_decimal(unit.get("ask_price"), "ORDERBOOK_ASK_PRICE_INVALID", positive=True),
            "quantity": _format_decimal(unit.get("ask_size"), "ORDERBOOK_ASK_SIZE_INVALID", positive=True),
        })
        bids.append({
            "price": _format_decimal(unit.get("bid_price"), "ORDERBOOK_BID_PRICE_INVALID", positive=True),
            "quantity": _format_decimal(unit.get("bid_size"), "ORDERBOOK_BID_SIZE_INVALID", positive=True),
        })
    try:
        received = dt.datetime.strptime(row["received_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=dt.timezone.utc
        )
    except (TypeError, ValueError) as exc:
        raise CryptoPaperRuntimeBridgeError("ORDERBOOK_RECEIVED_AT_INVALID") from exc
    captured_at = received.strftime("%Y-%m-%dT%H:%M:%SZ")
    return SIMULATOR.build_snapshot(
        snapshot_id=f"P9.{market}.{row['source_sha256'][:24].upper()}",
        market=market,
        captured_at=captured_at,
        freshness_status="FRESH",
        ask_levels=asks,
        bid_levels=bids,
        source_ref=f"{path.relative_to(ROOT)}#latest_public_messages/{market}/orderbook",
        source_sha256=row["source_sha256"],
    )


def _promotion_packet(decision: dict) -> dict | None:
    entries = _entries(decision)
    universe_entry = entries["universe"]
    market_entry = entries["market_evidence"]
    if universe_entry is None or universe_entry["packet"] is None:
        return None
    if market_entry is None or market_entry["date"] != universe_entry["date"]:
        market_by_market = {}
    else:
        market_by_market = market_entry["record"].get("packets", {})
    regime = DECISION.build_regime_snapshot(decision["generated_at"], None)
    try:
        return PROMOTION.build_promotion_packet(
            universe_entry["packet"],
            regime,
            market_by_market,
            None,
            evaluation_as_of=universe_entry["packet"]["evaluation_as_of"],
        )
    except PROMOTION.CryptoCandidatePromotionError:
        return None


def build_runtime_request(
    decision: dict,
    *,
    expected_source_commit: str,
    public_code_commit_sha: str | None = None,
    account_state: dict | None,
    open_position_risk: list[dict] | None,
    runtime_config: dict | None,
    known_idempotency_keys=None,
) -> dict:
    decision = validate_decision_snapshot(
        decision, expected_source_commit=expected_source_commit,
    )
    code_commit = _require_sha40(
        public_code_commit_sha or expected_source_commit,
        "PUBLIC_CODE_COMMIT_INVALID",
    )
    config = validate_runtime_config(runtime_config) if runtime_config is not None else None
    missing = []
    if account_state is None:
        missing.append("PAPER_ACCOUNT_STATE")
    if open_position_risk is None:
        missing.append("OPEN_POSITION_RISK")
    if config is None:
        missing.append("USER_RATIFIED_RUNTIME_CONFIG")

    promotion = _promotion_packet(decision)
    eligibility = None
    requests = []
    match_snapshots = []
    blockers = []

    # A current run's retained orderbook predates the decision assembled at
    # the tail of that run, so it may support the decision but cannot fill a
    # newly submitted order.  It can only match orders carried from a prior
    # ledger state.  New intents wait for a later capture.
    if account_state is not None:
        checked_account = SIMULATOR.validate_account_state(account_state)
        intent_by_order_id = {
            event["order_id"]: event["payload"]["intent"]
            for event in checked_account["source_ledger"]["events"]
            if event["event_type"] == "ORDER_SUBMITTED"
        }
        open_orders_by_market = {}
        for order in checked_account["orders"]:
            if order["status"] in {"OPEN", "PARTIALLY_FILLED"}:
                open_orders_by_market.setdefault(order["market"], []).append(order)
        for market, orders in sorted(open_orders_by_market.items()):
            try:
                snapshot = orderbook_snapshot(decision, market=market)
            except CryptoPaperRuntimeBridgeError as exc:
                blockers.append(f"MATCH_SNAPSHOT_UNAVAILABLE:{market}:{exc}")
                continue
            captured = _parse_utc(snapshot["captured_at"], "ORDERBOOK_CAPTURED_AT_INVALID")
            eligible_order_ids = []
            for order in orders:
                intent = intent_by_order_id.get(order["order_id"])
                if intent is None:
                    raise CryptoPaperRuntimeBridgeError(
                        f"OPEN_ORDER_INTENT_MISSING:{order['order_id']}"
                    )
                submitted = _parse_utc(
                    intent["submitted_at"], "OPEN_ORDER_SUBMITTED_AT_INVALID"
                )
                expires = _parse_utc(intent["expires_at"], "OPEN_ORDER_EXPIRY_INVALID")
                if captured < submitted:
                    blockers.append(f"MATCH_SNAPSHOT_PRECEDES_OPEN_ORDER:{order['order_id']}")
                elif captured >= expires:
                    blockers.append(f"OPEN_ORDER_EXPIRY_REACHED:{order['order_id']}")
                else:
                    eligible_order_ids.append(order["order_id"])
            if eligible_order_ids:
                match_snapshots.append({
                    "market": market,
                    "order_ids": sorted(eligible_order_ids),
                    "snapshot": snapshot,
                })
    if promotion is None:
        blockers.append("PROMOTION_PACKET_UNAVAILABLE")
    elif missing:
        blockers.extend("RUNTIME_INPUT_MISSING:" + item for item in missing)
    else:
        paper_account = paper_account_state_from_ledger(
            account_state, open_position_risk=open_position_risk or [],
        )
        eligibility = ELIGIBILITY.build_eligibility_packet(
            promotion,
            evaluation_as_of=promotion["evaluation_as_of"],
            paper_account_state=paper_account,
            fee_rate=config["fee_rate"],
            known_idempotency_keys=known_idempotency_keys,
        )
        eligibility = ELIGIBILITY.validate_output(eligibility)
        for row in eligibility["candidates"]:
            if row["eligibility_state"] != "PAPER_BUY_ELIGIBLE":
                continue
            draft = row["order_draft"]
            submitted_at = decision["generated_at"]
            if _parse_utc(draft["expires_at"], "ORDER_DRAFT_EXPIRY_INVALID") <= _parse_utc(
                submitted_at, "DECISION_GENERATED_AT_INVALID"
            ):
                blockers.append(f"ORDER_DRAFT_EXPIRED:{row['market']}")
                continue
            snapshot = orderbook_snapshot(decision, market=row["market"])
            limit_price = None
            if config["order_type"] == "LIMIT":
                key = "low" if config["limit_price_source"] == "ENTRY_ZONE_LOW" else "high"
                limit_price = draft["entry_zone"][key]
            guard = draft["duplicate_guard_key"]
            order_id = f"PAPER.BUY.{row['market']}.{hashlib.sha256(guard.encode()).hexdigest()[:24].upper()}"
            intent = SIMULATOR.build_intent(
                order_id=order_id,
                idempotency_key=guard,
                market=row["market"],
                side="BUY",
                order_type=config["order_type"],
                quantity=draft["quantity"],
                limit_price=limit_price,
                fee_rate=config["fee_rate"],
                queue_fraction=config["queue_fraction"],
                submitted_at=submitted_at,
                expires_at=draft["expires_at"],
                market_regime_status=promotion["source_packets"]["regime"].get("regime", "UNKNOWN"),
                source_plan_ref=f"public://crypto-paper-decision/{decision['generation_id']}",
                source_plan_sha256=decision["payload_sha256"],
                source_evidence_ref=snapshot["source_ref"],
                source_evidence_sha256=snapshot["source_sha256"],
            )
            requests.append({
                "market": row["market"],
                "planned_loss_krw": draft["planned_loss_krw"],
                "order_draft": copy.deepcopy(draft),
                "intent": intent,
                "source_snapshot": snapshot,
            })

    if requests:
        status = "PAPER_INTENTS_READY"
    elif match_snapshots:
        status = "PAPER_MATCHES_READY"
    elif promotion is None:
        status = "WAIT_PROMOTION_UNAVAILABLE"
    elif missing:
        status = "WAIT_RUNTIME_INPUTS_MISSING"
    else:
        status = "NO_ELIGIBLE_CANDIDATE"
    packet = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "mode": PRIVATE_RUNTIME_MODE,
        "status": status,
        "observed_at": decision["generated_at"],
        "decision_generation_id": decision["generation_id"],
        "decision_payload_sha256": decision["payload_sha256"],
        "decision_source_commit_sha": decision["source_commit"],
        "public_code_commit_sha": code_commit,
        "runtime_config_sha256": config["packet_sha256"] if config is not None else None,
        "eligibility": eligibility,
        "requests": requests,
        "match_snapshots": match_snapshots,
        "blockers": sorted(blockers),
        "authority": copy.deepcopy(AUTHORITY),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_runtime_request(packet)


def validate_runtime_request(value: object) -> dict:
    fields = {
        "schema_version", "mode", "status", "observed_at",
        "decision_generation_id", "decision_payload_sha256",
        "decision_source_commit_sha", "public_code_commit_sha",
        "runtime_config_sha256", "eligibility",
        "requests", "match_snapshots", "blockers", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_FIELDS_INVALID")
    if (
        value.get("schema_version") != REQUEST_SCHEMA_VERSION
        or value.get("mode") != PRIVATE_RUNTIME_MODE
        or value.get("authority") != AUTHORITY
    ):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_IDENTITY_INVALID")
    _parse_utc(value.get("observed_at"), "RUNTIME_REQUEST_OBSERVED_AT_INVALID")
    _require_sha256(value.get("decision_generation_id"), "RUNTIME_REQUEST_GENERATION_INVALID")
    _require_sha256(value.get("decision_payload_sha256"), "RUNTIME_REQUEST_DECISION_SHA_INVALID")
    _require_sha40(value.get("decision_source_commit_sha"), "RUNTIME_REQUEST_DECISION_COMMIT_INVALID")
    _require_sha40(value.get("public_code_commit_sha"), "RUNTIME_REQUEST_PUBLIC_COMMIT_INVALID")
    if value.get("runtime_config_sha256") is not None:
        _require_sha256(value["runtime_config_sha256"], "RUNTIME_REQUEST_CONFIG_SHA_INVALID")
    eligibility = value.get("eligibility")
    if eligibility is not None:
        ELIGIBILITY.validate_output(eligibility)
    requests = value.get("requests")
    if not isinstance(requests, list):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_ROWS_INVALID")
    for index, row in enumerate(requests):
        if not isinstance(row, dict) or set(row) != {
            "market", "planned_loss_krw", "order_draft", "intent", "source_snapshot"
        }:
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_ROW_FIELDS_INVALID:{index}")
        intent = SIMULATOR.validate_intent(row["intent"])
        snapshot = SIMULATOR.validate_snapshot(row["source_snapshot"])
        if intent["market"] != row["market"] or snapshot["market"] != row["market"]:
            raise CryptoPaperRuntimeBridgeError(f"RUNTIME_REQUEST_ROW_MARKET_MISMATCH:{index}")
        _format_decimal(row["planned_loss_krw"], "RUNTIME_REQUEST_PLANNED_LOSS_INVALID")
    match_snapshots = value.get("match_snapshots")
    if not isinstance(match_snapshots, list):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_MATCH_SNAPSHOTS_INVALID")
    for index, row in enumerate(match_snapshots):
        if not isinstance(row, dict) or set(row) != {"market", "order_ids", "snapshot"}:
            raise CryptoPaperRuntimeBridgeError(
                f"RUNTIME_REQUEST_MATCH_SNAPSHOT_FIELDS_INVALID:{index}"
            )
        snapshot = SIMULATOR.validate_snapshot(row["snapshot"])
        if snapshot["market"] != row["market"]:
            raise CryptoPaperRuntimeBridgeError(
                f"RUNTIME_REQUEST_MATCH_SNAPSHOT_MARKET_MISMATCH:{index}"
            )
        order_ids = row["order_ids"]
        if (
            not isinstance(order_ids, list)
            or not order_ids
            or order_ids != sorted(set(order_ids))
            or not all(isinstance(order_id, str) and order_id for order_id in order_ids)
        ):
            raise CryptoPaperRuntimeBridgeError(
                f"RUNTIME_REQUEST_MATCH_ORDER_IDS_INVALID:{index}"
            )
    blockers = value.get("blockers")
    if not isinstance(blockers, list) or not all(isinstance(row, str) and row for row in blockers):
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_BLOCKERS_INVALID")
    if value["status"] == "PAPER_INTENTS_READY" and not requests:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_READY_WITHOUT_INTENTS")
    if requests and value["status"] != "PAPER_INTENTS_READY":
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_INTENTS_WITH_NONREADY_STATUS")
    if value["status"] == "PAPER_MATCHES_READY" and not match_snapshots:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_MATCH_READY_WITHOUT_SNAPSHOT")
    if match_snapshots and not requests and value["status"] != "PAPER_MATCHES_READY":
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_MATCH_WITH_NONREADY_STATUS")
    digest = _require_sha256(value.get("packet_sha256"), "RUNTIME_REQUEST_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoPaperRuntimeBridgeError("RUNTIME_REQUEST_SHA_MISMATCH")
    return copy.deepcopy(value)
