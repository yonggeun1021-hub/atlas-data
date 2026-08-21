#!/usr/bin/env python3
"""P9-04 duplicate Action/Order ID guard with no execution authority."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "action_order_idempotency_contract.json"
LEDGER_SCHEMA_VERSION = "action_order_idempotency_ledger/1"
ATTEMPT_SCHEMA_VERSION = "action_order_attempt_batch/1"
OUTPUT_SCHEMA_VERSION = "action_order_idempotency_result/2"
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ActionOrderIdempotencyError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionOrderIdempotencyError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "action_order_idempotency_guard/1",
        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
        "attempt_batch_schema_version": ATTEMPT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "identity_fields": ["idempotency_key", "action_id", "order_id"],
        "payload_binding": "OPAQUE_INTENT_SHA256",
        "ledger_mode": "APPEND_ONLY_CANDIDATE_OUTPUT",
        "duplicate_result": "DUPLICATE_RETRY_BLOCKED",
        "novel_result": "NOVEL_RECORDED_EXECUTION_NOT_AUTHORIZED",
        "collision_policy": "HARD_FAIL",
        "event_identity_policy": "EVENT_ID_MAY_HAVE_DISTINCT_EXPLICIT_ORDERS",
        "ledger_authority": {
            "idempotency_history_only": True,
            "order_execution_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "input_authority": {
            "attempt_observation_only": True,
            "order_execution_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "duplicate_guard_only": True,
            "action_creation_authorized": False,
            "order_creation_authorized": False,
            "order_execution_authorized": False,
            "broker_submission_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ActionOrderIdempotencyError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ActionOrderIdempotencyError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ActionOrderIdempotencyError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ActionOrderIdempotencyError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ActionOrderIdempotencyError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ActionOrderIdempotencyError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ActionOrderIdempotencyError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ActionOrderIdempotencyError(code)
    return value


def _record_identity(row: dict) -> tuple:
    return (
        row["event_id"], row["action_id"], row["order_id"],
        row["market"], row["intent_sha256"],
    )


def _validate_record(row: dict, contract: dict, context: str) -> dict:
    fields = {
        "idempotency_key", "event_id", "action_id", "order_id", "market",
        "intent_sha256", "first_seen_at", "source_ref", "source_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ActionOrderIdempotencyError(f"LEDGER_RECORD_FIELDS_MISMATCH:{context}")
    market = row.get("market")
    if market not in contract["allowed_markets"]:
        raise ActionOrderIdempotencyError(f"MARKET_INVALID:{context}:{market}")
    return {
        "idempotency_key": _token(
            row.get("idempotency_key"), f"IDEMPOTENCY_KEY_INVALID:{context}"
        ),
        "event_id": _token(row.get("event_id"), f"EVENT_ID_INVALID:{context}"),
        "action_id": _token(row.get("action_id"), f"ACTION_ID_INVALID:{context}"),
        "order_id": _token(row.get("order_id"), f"ORDER_ID_INVALID:{context}"),
        "market": market,
        "intent_sha256": _sha(
            row.get("intent_sha256"), f"INTENT_SHA_INVALID:{context}"
        ),
        "first_seen_at": _utc(
            row.get("first_seen_at"), f"FIRST_SEEN_AT_INVALID:{context}"
        ),
        "source_ref": _text(row.get("source_ref"), f"SOURCE_REF_INVALID:{context}"),
        "source_sha256": _sha(
            row.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"
        ),
    }


def _assert_unique_records(rows: list[dict]) -> None:
    keys = {}
    action_ids = {}
    order_ids = {}
    for row in rows:
        key = row["idempotency_key"]
        if key in keys:
            raise ActionOrderIdempotencyError(f"LEDGER_IDEMPOTENCY_KEY_DUPLICATE:{key}")
        keys[key] = row
        for field, registry, code in (
            ("action_id", action_ids, "LEDGER_ACTION_ID_DUPLICATE"),
            ("order_id", order_ids, "LEDGER_ORDER_ID_DUPLICATE"),
        ):
            value = row[field]
            if value in registry:
                raise ActionOrderIdempotencyError(f"{code}:{value}")
            registry[value] = key


def _validate_ledger(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "ledger_id", "records",
        "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ActionOrderIdempotencyError("LEDGER_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["ledger_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["ledger_authority"]
    ):
        raise ActionOrderIdempotencyError("LEDGER_IDENTITY_INVALID")
    ledger_id = _text(value.get("ledger_id"), "LEDGER_ID_INVALID")
    raw_rows = value.get("records")
    if not isinstance(raw_rows, list):
        raise ActionOrderIdempotencyError("LEDGER_RECORDS_NOT_LIST")
    rows = sorted(
        (
            _validate_record(row, contract, f"ledger:{index}")
            for index, row in enumerate(raw_rows)
        ),
        key=lambda row: (row["first_seen_at"], row["idempotency_key"]),
    )
    _assert_unique_records(rows)
    normalized = {
        "schema_version": contract["ledger_schema_version"],
        "contract_version": contract["contract_version"],
        "ledger_id": ledger_id,
        "records": rows,
        "authority": copy.deepcopy(contract["ledger_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "LEDGER_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise ActionOrderIdempotencyError("LEDGER_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _validate_attempt(row: dict, contract: dict, observed_at: str, context: str) -> dict:
    fields = {
        "idempotency_key", "event_id", "action_id", "order_id", "market",
        "intent_sha256", "attempted_at", "source_ref", "source_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ActionOrderIdempotencyError(f"ATTEMPT_FIELDS_MISMATCH:{context}")
    attempted_at = _utc(
        row.get("attempted_at"), f"ATTEMPTED_AT_INVALID:{context}"
    )
    if attempted_at > observed_at:
        raise ActionOrderIdempotencyError(f"ATTEMPT_FROM_FUTURE:{context}")
    market = row.get("market")
    if market not in contract["allowed_markets"]:
        raise ActionOrderIdempotencyError(f"MARKET_INVALID:{context}:{market}")
    return {
        "idempotency_key": _token(
            row.get("idempotency_key"), f"IDEMPOTENCY_KEY_INVALID:{context}"
        ),
        "event_id": _token(row.get("event_id"), f"EVENT_ID_INVALID:{context}"),
        "action_id": _token(row.get("action_id"), f"ACTION_ID_INVALID:{context}"),
        "order_id": _token(row.get("order_id"), f"ORDER_ID_INVALID:{context}"),
        "market": market,
        "intent_sha256": _sha(
            row.get("intent_sha256"), f"INTENT_SHA_INVALID:{context}"
        ),
        "attempted_at": attempted_at,
        "source_ref": _text(row.get("source_ref"), f"SOURCE_REF_INVALID:{context}"),
        "source_sha256": _sha(
            row.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"
        ),
    }


def _validate_batch(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "attempts", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ActionOrderIdempotencyError("BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["attempt_batch_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise ActionOrderIdempotencyError("BATCH_IDENTITY_INVALID")
    batch_id = _text(value.get("batch_id"), "BATCH_ID_INVALID")
    observed_at = _utc(value.get("observed_at"), "BATCH_OBSERVED_AT_INVALID")
    raw_rows = value.get("attempts")
    if not isinstance(raw_rows, list):
        raise ActionOrderIdempotencyError("ATTEMPTS_NOT_LIST")
    rows = sorted(
        (
            _validate_attempt(row, contract, observed_at, f"attempt:{index}")
            for index, row in enumerate(raw_rows)
        ),
        key=lambda row: (row["attempted_at"], row["idempotency_key"]),
    )
    normalized = {
        "schema_version": contract["attempt_batch_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": batch_id,
        "observed_at": observed_at,
        "attempts": rows,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "BATCH_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise ActionOrderIdempotencyError("BATCH_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _checked_source_packet(checked: dict) -> dict:
    value = copy.deepcopy(checked["normalized"])
    value["packet_sha256"] = checked["packet_sha256"]
    return value


def _assemble_result(checked_ledger: dict, checked_batch: dict, contract: dict) -> dict:
    ledger_rows = copy.deepcopy(checked_ledger["normalized"]["records"])
    by_key = {row["idempotency_key"]: row for row in ledger_rows}
    action_ids = {row["action_id"]: row["idempotency_key"] for row in ledger_rows}
    order_ids = {row["order_id"]: row["idempotency_key"] for row in ledger_rows}
    decisions = []
    novel = 0
    duplicate = 0
    for attempt in checked_batch["normalized"]["attempts"]:
        key = attempt["idempotency_key"]
        existing = by_key.get(key)
        if existing is not None:
            if _record_identity(existing) != _record_identity(attempt):
                raise ActionOrderIdempotencyError(
                    f"IDEMPOTENCY_KEY_PAYLOAD_COLLISION:{key}"
                )
            duplicate += 1
            result = contract["duplicate_result"]
            matched = payload_sha256(existing)
        else:
            action_owner = action_ids.get(attempt["action_id"])
            if action_owner is not None:
                raise ActionOrderIdempotencyError(
                    f"ACTION_ID_KEY_COLLISION:{attempt['action_id']}:{action_owner}:{key}"
                )
            order_owner = order_ids.get(attempt["order_id"])
            if order_owner is not None:
                raise ActionOrderIdempotencyError(
                    f"ORDER_ID_KEY_COLLISION:{attempt['order_id']}:{order_owner}:{key}"
                )
            record = {
                "idempotency_key": key,
                "event_id": attempt["event_id"],
                "action_id": attempt["action_id"],
                "order_id": attempt["order_id"],
                "market": attempt["market"],
                "intent_sha256": attempt["intent_sha256"],
                "first_seen_at": attempt["attempted_at"],
                "source_ref": attempt["source_ref"],
                "source_sha256": attempt["source_sha256"],
            }
            ledger_rows.append(record)
            by_key[key] = record
            action_ids[record["action_id"]] = key
            order_ids[record["order_id"]] = key
            novel += 1
            result = contract["novel_result"]
            matched = payload_sha256(record)
        decisions.append({
            "idempotency_key": key,
            "event_id": attempt["event_id"],
            "action_id": attempt["action_id"],
            "order_id": attempt["order_id"],
            "attempted_at": attempt["attempted_at"],
            "result": result,
            "matched_record_sha256": matched,
            "execution_authorized": False,
            "broker_submission": None,
        })
    ledger_rows.sort(key=lambda row: (row["first_seen_at"], row["idempotency_key"]))
    _assert_unique_records(ledger_rows)
    updated_ledger = {
        "schema_version": contract["ledger_schema_version"],
        "contract_version": contract["contract_version"],
        "ledger_id": checked_ledger["normalized"]["ledger_id"],
        "records": ledger_rows,
        "authority": copy.deepcopy(contract["ledger_authority"]),
    }
    updated_ledger["packet_sha256"] = payload_sha256(updated_ledger)
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "DUPLICATE_GUARD_EVALUATED_EXECUTION_NOT_AUTHORIZED",
        "batch_id": checked_batch["normalized"]["batch_id"],
        "observed_at": checked_batch["normalized"]["observed_at"],
        "summary": {
            "attempt_count": len(decisions),
            "novel_recorded_count": novel,
            "duplicate_blocked_count": duplicate,
            "orders_created": 0,
            "orders_submitted": 0,
        },
        "decisions": decisions,
        "updated_ledger_candidate": updated_ledger,
        "source_packets": {
            "prior_ledger": _checked_source_packet(checked_ledger),
            "attempt_batch": _checked_source_packet(checked_batch),
        },
        "lineage": {
            "prior_ledger_sha256": checked_ledger["packet_sha256"],
            "attempt_batch_sha256": checked_batch["packet_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "ACTION_ID_GENERATION_POLICY_NOT_IMPLEMENTED",
            "ORDER_ID_GENERATION_POLICY_NOT_IMPLEMENTED",
            "EXECUTION_PATH_NOT_AUTHORIZED",
            "BROKER_SUBMISSION_NOT_IMPLEMENTED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_result(packet: dict, contract: dict | None = None) -> dict:
    """Revalidate persisted guard output from its exact embedded source packets."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "batch_id",
        "observed_at", "summary", "decisions", "updated_ledger_candidate",
        "source_packets", "lineage", "authority", "unresolved_boundaries",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ActionOrderIdempotencyError("OUTPUT_FIELDS_MISMATCH")
    digest = _sha(packet.get("packet_sha256"), "OUTPUT_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ActionOrderIdempotencyError("OUTPUT_SHA_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status")
        != "DUPLICATE_GUARD_EVALUATED_EXECUTION_NOT_AUTHORIZED"
        or packet.get("authority") != contract["authority"]
    ):
        raise ActionOrderIdempotencyError("OUTPUT_IDENTITY_INVALID")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {
        "prior_ledger", "attempt_batch"
    }:
        raise ActionOrderIdempotencyError("OUTPUT_SOURCE_PACKETS_INVALID")
    checked_ledger = _validate_ledger(sources["prior_ledger"], contract)
    checked_batch = _validate_batch(sources["attempt_batch"], contract)
    if (
        canonical_json(sources["prior_ledger"])
        != canonical_json(_checked_source_packet(checked_ledger))
        or canonical_json(sources["attempt_batch"])
        != canonical_json(_checked_source_packet(checked_batch))
    ):
        raise ActionOrderIdempotencyError("OUTPUT_SOURCE_PACKETS_NOT_NORMALIZED")
    expected = _assemble_result(checked_ledger, checked_batch, contract)
    if canonical_json(packet) != canonical_json(expected):
        raise ActionOrderIdempotencyError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def build_result(
    ledger: dict,
    attempt_batch: dict,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    checked_ledger = _validate_ledger(ledger, contract)
    checked_batch = _validate_batch(attempt_batch, contract)
    return validate_result(
        _assemble_result(checked_ledger, checked_batch, contract), contract
    )


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ActionOrderIdempotencyError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(ledger_path: Path, batch_path: Path, output_path: Path) -> int:
    try:
        result = build_result(_read_json(ledger_path), _read_json(batch_path))
        write_json_atomic(output_path, result)
        return 0
    except (ActionOrderIdempotencyError, OSError, TypeError, ValueError) as exc:
        print(f"Action/Order idempotency guard failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Guard duplicate Action/Order IDs without executing orders"
    )
    parser.add_argument("ledger", type=Path)
    parser.add_argument("attempt_batch", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.ledger, args.attempt_batch, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
