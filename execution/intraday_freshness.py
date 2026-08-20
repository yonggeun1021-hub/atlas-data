#!/usr/bin/env python3
"""P9-01 policy-gated intraday quote freshness guard."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "intraday_freshness_guard_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


class IntradayFreshnessError(ValueError):
    """Fail-closed P9-01 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntradayFreshnessError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "intraday_freshness_guard/1",
        "snapshot_schema_version": "intraday_quote_batch/1",
        "policy_schema_version": "intraday_freshness_policy/1",
        "output_schema_version": "intraday_freshness_result/1",
        "markets": ["US", "KOREA", "CRYPTO"],
        "freshness_statuses": ["FRESH", "STALE"],
        "stale_reasons": [
            "PROVIDER_AGE_EXCEEDED", "TRANSPORT_DELAY_EXCEEDED"
        ],
        "repository_default_policy": "ABSENT",
        "policy_requirement": "EXTERNAL_RATIFIED_POLICY_REQUIRED",
        "time_semantics": "PROVIDER_TIMESTAMP_LE_RECEIVED_AT_LE_OBSERVED_AT",
        "threshold_semantics": "LESS_THAN_OR_EQUAL_IS_FRESH",
        "input_authority": {
            "quote_observation_only": True,
            "entry_eligibility_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "freshness_guard_only": True,
            "provider_selection_authorized": False,
            "entry_eligibility_authorized": False,
            "exit_eligibility_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "broker_submission_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise IntradayFreshnessError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise IntradayFreshnessError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise IntradayFreshnessError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise IntradayFreshnessError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise IntradayFreshnessError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise IntradayFreshnessError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise IntradayFreshnessError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise IntradayFreshnessError(code)
    return value


def _decimal(value, *, positive: bool, code: str) -> str:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise IntradayFreshnessError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise IntradayFreshnessError(code) from exc
    if (positive and parsed <= 0) or (not positive and parsed < 0):
        raise IntradayFreshnessError(code)
    return value


def _digest(value: dict, field: str, code: str) -> str:
    digest = _sha(value.get(field), code)
    normalized = copy.deepcopy(value)
    normalized.pop(field)
    if payload_sha256(normalized) != digest:
        raise IntradayFreshnessError(f"{code}_MISMATCH")
    return digest


def _validate_policy(value: dict, observed: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "policy_id", "approval_status", "ratified_by",
        "ratified_at_utc", "effective_from_utc", "effective_to_utc",
        "input_contract_version", "max_provider_age_seconds_by_market",
        "max_transport_delay_seconds_by_market", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayFreshnessError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("approval_status") != "RATIFIED"
        or value.get("input_contract_version") != contract["contract_version"]
    ):
        raise IntradayFreshnessError("POLICY_IDENTITY_INVALID")
    _token(value.get("policy_id"), "POLICY_ID_INVALID")
    _text(value.get("ratified_by"), "POLICY_RATIFIED_BY_INVALID")
    ratified = _utc(value.get("ratified_at_utc"), "POLICY_RATIFIED_AT_INVALID")
    start = _utc(value.get("effective_from_utc"), "POLICY_EFFECTIVE_FROM_INVALID")
    end = _utc(value.get("effective_to_utc"), "POLICY_EFFECTIVE_TO_INVALID")
    if ratified > start:
        raise IntradayFreshnessError("POLICY_RATIFIED_AFTER_EFFECTIVE_START")
    if end <= start or not (start <= observed < end):
        raise IntradayFreshnessError("POLICY_NOT_EFFECTIVE")
    thresholds = {}
    for field in (
        "max_provider_age_seconds_by_market",
        "max_transport_delay_seconds_by_market",
    ):
        mapping = value.get(field)
        if not isinstance(mapping, dict) or list(mapping) != contract["markets"]:
            raise IntradayFreshnessError(f"POLICY_THRESHOLD_MARKETS_INVALID:{field}")
        if any(type(item) is not int or item < 1 for item in mapping.values()):
            raise IntradayFreshnessError(f"POLICY_THRESHOLD_VALUE_INVALID:{field}")
        thresholds[field] = copy.deepcopy(mapping)
    digest = _digest(value, "packet_sha256", "POLICY_SHA_INVALID")
    return {
        "policy_id": value["policy_id"],
        "ratified_by": value["ratified_by"],
        "ratified_at_utc": value["ratified_at_utc"],
        "effective_from_utc": value["effective_from_utc"],
        "effective_to_utc": value["effective_to_utc"],
        **thresholds,
        "packet_sha256": digest,
    }


def _validate_batch(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "quotes", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayFreshnessError("BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["snapshot_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise IntradayFreshnessError("BATCH_IDENTITY_INVALID")
    batch_id = _token(value.get("batch_id"), "BATCH_ID_INVALID")
    observed = _utc(value.get("observed_at"), "BATCH_OBSERVED_AT_INVALID")
    raw_quotes = value.get("quotes")
    if not isinstance(raw_quotes, list):
        raise IntradayFreshnessError("QUOTES_NOT_LIST")
    fields_quote = {
        "asset_id", "market", "price", "volume", "quote_currency",
        "provider_id", "provider_timestamp", "received_at", "source_ref",
        "source_sha256",
    }
    quotes = []
    seen = set()
    for index, row in enumerate(raw_quotes):
        context = f"quote:{index}"
        if not isinstance(row, dict) or set(row) != fields_quote:
            raise IntradayFreshnessError(f"QUOTE_FIELDS_MISMATCH:{context}")
        market = row.get("market")
        if market not in contract["markets"]:
            raise IntradayFreshnessError(f"QUOTE_MARKET_INVALID:{context}:{market}")
        asset_id = _token(row.get("asset_id"), f"QUOTE_ASSET_INVALID:{context}")
        if asset_id in seen:
            raise IntradayFreshnessError(f"QUOTE_ASSET_DUPLICATE:{asset_id}")
        seen.add(asset_id)
        provider_at = _utc(
            row.get("provider_timestamp"), f"QUOTE_PROVIDER_TIME_INVALID:{context}"
        )
        received_at = _utc(row.get("received_at"), f"QUOTE_RECEIVED_AT_INVALID:{context}")
        if not (provider_at <= received_at <= observed):
            raise IntradayFreshnessError(f"QUOTE_TIME_ORDER_INVALID:{asset_id}")
        quotes.append({
            "asset_id": asset_id,
            "market": market,
            "price": _decimal(row.get("price"), positive=True, code=f"QUOTE_PRICE_INVALID:{context}"),
            "volume": _decimal(row.get("volume"), positive=False, code=f"QUOTE_VOLUME_INVALID:{context}"),
            "quote_currency": _token(
                row.get("quote_currency"), f"QUOTE_CURRENCY_INVALID:{context}"
            ),
            "provider_id": _token(row.get("provider_id"), f"PROVIDER_ID_INVALID:{context}"),
            "provider_timestamp": row["provider_timestamp"],
            "received_at": row["received_at"],
            "source_ref": _text(row.get("source_ref"), f"SOURCE_REF_INVALID:{context}"),
            "source_sha256": _sha(
                row.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"
            ),
        })
    quotes.sort(key=lambda item: (contract["markets"].index(item["market"]), item["asset_id"]))
    _digest(value, "packet_sha256", "BATCH_SHA_INVALID")
    normalized_batch = {
        "schema_version": contract["snapshot_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": batch_id,
        "observed_at": value["observed_at"],
        "quotes": quotes,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    return {
        "batch_id": batch_id,
        "observed_at": value["observed_at"],
        "observed": observed,
        "quotes": quotes,
        "packet_sha256": payload_sha256(normalized_batch),
    }


def evaluate_freshness(
    quote_batch: dict,
    policy: dict,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    batch = _validate_batch(quote_batch, contract)
    checked_policy = _validate_policy(policy, batch["observed"], contract)
    results = []
    counts = {status: 0 for status in contract["freshness_statuses"]}
    for quote in batch["quotes"]:
        provider_at = _utc(quote["provider_timestamp"], "QUOTE_PROVIDER_TIME_INVALID")
        received_at = _utc(quote["received_at"], "QUOTE_RECEIVED_AT_INVALID")
        age = int((batch["observed"] - provider_at).total_seconds())
        transport = int((received_at - provider_at).total_seconds())
        market = quote["market"]
        max_age = checked_policy["max_provider_age_seconds_by_market"][market]
        max_transport = checked_policy["max_transport_delay_seconds_by_market"][market]
        reasons = []
        if age > max_age:
            reasons.append("PROVIDER_AGE_EXCEEDED")
        if transport > max_transport:
            reasons.append("TRANSPORT_DELAY_EXCEEDED")
        status = "FRESH" if not reasons else "STALE"
        counts[status] += 1
        results.append({
            **copy.deepcopy(quote),
            "provider_age_seconds": age,
            "transport_delay_seconds": transport,
            "max_provider_age_seconds": max_age,
            "max_transport_delay_seconds": max_transport,
            "freshness_status": status,
            "stale_reasons": reasons,
            "fresh_for_intraday_consumption": status == "FRESH",
            "entry_eligibility": None,
            "action": None,
            "order": None,
        })
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "FRESHNESS_EVALUATED_NO_ENTRY_AUTHORITY",
        "batch_id": batch["batch_id"],
        "observed_at": batch["observed_at"],
        "policy": checked_policy,
        "summary": {
            "quote_count": len(results),
            "fresh_count": counts["FRESH"],
            "stale_count": counts["STALE"],
            "entry_eligible_count": 0,
            "orders_created": 0,
        },
        "results": results,
        "lineage": {"quote_batch_sha256": batch["packet_sha256"]},
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "MARKET_DATA_FEED_SELECTION_NOT_AUTHORIZED",
            "REPOSITORY_DEFAULT_FRESHNESS_POLICY_ABSENT",
            "ENTRY_EXIT_ELIGIBILITY_NOT_AUTHORIZED",
            "ACTION_ORDER_GENERATION_NOT_AUTHORIZED",
            "PRODUCTION_WIRING_NOT_IMPLEMENTED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_output(packet, contract)


def validate_output(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "batch_id", "observed_at",
        "policy", "summary", "results", "lineage", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise IntradayFreshnessError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "FRESHNESS_EVALUATED_NO_ENTRY_AUTHORITY"
        or packet.get("authority") != contract["authority"]
    ):
        raise IntradayFreshnessError("OUTPUT_IDENTITY_INVALID")
    observed = _utc(packet.get("observed_at"), "OUTPUT_OBSERVED_AT_INVALID")
    policy = packet.get("policy")
    if not isinstance(policy, dict) or set(policy) != {
        "policy_id", "ratified_by", "ratified_at_utc", "effective_from_utc",
        "effective_to_utc", "max_provider_age_seconds_by_market",
        "max_transport_delay_seconds_by_market", "packet_sha256",
    }:
        raise IntradayFreshnessError("OUTPUT_POLICY_INVALID")
    _token(policy.get("policy_id"), "OUTPUT_POLICY_ID_INVALID")
    _text(policy.get("ratified_by"), "OUTPUT_POLICY_RATIFIED_BY_INVALID")
    ratified = _utc(policy.get("ratified_at_utc"), "OUTPUT_POLICY_RATIFIED_AT_INVALID")
    effective_from = _utc(
        policy.get("effective_from_utc"), "OUTPUT_POLICY_EFFECTIVE_FROM_INVALID"
    )
    effective_to = _utc(
        policy.get("effective_to_utc"), "OUTPUT_POLICY_EFFECTIVE_TO_INVALID"
    )
    if ratified > effective_from or effective_to <= effective_from or not (
        effective_from <= observed < effective_to
    ):
        raise IntradayFreshnessError("OUTPUT_POLICY_TIMING_INVALID")
    if any(
        not isinstance(policy.get(field), dict)
        or list(policy[field]) != contract["markets"]
        or any(type(value) is not int or value < 1 for value in policy[field].values())
        for field in (
            "max_provider_age_seconds_by_market",
            "max_transport_delay_seconds_by_market",
        )
    ):
        raise IntradayFreshnessError("OUTPUT_POLICY_THRESHOLDS_INVALID")
    _sha(policy.get("packet_sha256"), "OUTPUT_POLICY_SHA_INVALID")
    results = packet.get("results")
    if not isinstance(results, list):
        raise IntradayFreshnessError("OUTPUT_RESULTS_NOT_LIST")
    counts = {"FRESH": 0, "STALE": 0}
    keys = []
    row_fields = {
        "asset_id", "market", "price", "volume", "quote_currency", "provider_id",
        "provider_timestamp", "received_at", "source_ref", "source_sha256",
        "provider_age_seconds", "transport_delay_seconds", "max_provider_age_seconds",
        "max_transport_delay_seconds", "freshness_status", "stale_reasons",
        "fresh_for_intraday_consumption", "entry_eligibility", "action", "order",
    }
    for row in results:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise IntradayFreshnessError("OUTPUT_RESULT_FIELDS_MISMATCH")
        market = row.get("market")
        provider_at = _utc(row.get("provider_timestamp"), "OUTPUT_PROVIDER_TIME_INVALID")
        received_at = _utc(row.get("received_at"), "OUTPUT_RECEIVED_AT_INVALID")
        age = int((observed - provider_at).total_seconds())
        transport = int((received_at - provider_at).total_seconds())
        if market not in contract["markets"] or not (provider_at <= received_at <= observed):
            raise IntradayFreshnessError("OUTPUT_RESULT_TIME_OR_MARKET_INVALID")
        max_age = policy["max_provider_age_seconds_by_market"][market]
        max_transport = policy["max_transport_delay_seconds_by_market"][market]
        reasons = []
        if age > max_age:
            reasons.append("PROVIDER_AGE_EXCEEDED")
        if transport > max_transport:
            reasons.append("TRANSPORT_DELAY_EXCEEDED")
        status = "FRESH" if not reasons else "STALE"
        if (
            row.get("provider_age_seconds") != age
            or row.get("transport_delay_seconds") != transport
            or row.get("max_provider_age_seconds") != max_age
            or row.get("max_transport_delay_seconds") != max_transport
            or row.get("freshness_status") != status
            or row.get("stale_reasons") != reasons
            or row.get("fresh_for_intraday_consumption") is not (status == "FRESH")
            or row.get("entry_eligibility") is not None
            or row.get("action") is not None
            or row.get("order") is not None
        ):
            raise IntradayFreshnessError("OUTPUT_RESULT_DERIVATION_INVALID")
        _token(row.get("asset_id"), "OUTPUT_ASSET_INVALID")
        _token(row.get("quote_currency"), "OUTPUT_CURRENCY_INVALID")
        _token(row.get("provider_id"), "OUTPUT_PROVIDER_INVALID")
        _text(row.get("source_ref"), "OUTPUT_SOURCE_REF_INVALID")
        _decimal(row.get("price"), positive=True, code="OUTPUT_PRICE_INVALID")
        _decimal(row.get("volume"), positive=False, code="OUTPUT_VOLUME_INVALID")
        _sha(row.get("source_sha256"), "OUTPUT_SOURCE_SHA_INVALID")
        keys.append((contract["markets"].index(market), row["asset_id"]))
        counts[status] += 1
    if keys != sorted(set(keys)):
        raise IntradayFreshnessError("OUTPUT_RESULT_ORDER_INVALID")
    expected_summary = {
        "quote_count": len(results),
        "fresh_count": counts["FRESH"],
        "stale_count": counts["STALE"],
        "entry_eligible_count": 0,
        "orders_created": 0,
    }
    if packet.get("summary") != expected_summary:
        raise IntradayFreshnessError("OUTPUT_SUMMARY_INVALID")
    lineage = packet.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {"quote_batch_sha256"}:
        raise IntradayFreshnessError("OUTPUT_LINEAGE_INVALID")
    _sha(lineage.get("quote_batch_sha256"), "OUTPUT_LINEAGE_SHA_INVALID")
    expected_boundaries = [
        "MARKET_DATA_FEED_SELECTION_NOT_AUTHORIZED",
        "REPOSITORY_DEFAULT_FRESHNESS_POLICY_ABSENT",
        "ENTRY_EXIT_ELIGIBILITY_NOT_AUTHORIZED",
        "ACTION_ORDER_GENERATION_NOT_AUTHORIZED",
        "PRODUCTION_WIRING_NOT_IMPLEMENTED",
    ]
    if packet.get("unresolved_boundaries") != expected_boundaries:
        raise IntradayFreshnessError("OUTPUT_BOUNDARIES_INVALID")
    digest = _sha(packet.get("packet_sha256"), "OUTPUT_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise IntradayFreshnessError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise IntradayFreshnessError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(batch_path: Path, policy_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(
            output_path, evaluate_freshness(_read_json(batch_path), _read_json(policy_path))
        )
        return 0
    except (IntradayFreshnessError, OSError, TypeError, ValueError) as exc:
        print(f"Intraday freshness guard failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate intraday quote freshness")
    parser.add_argument("quote_batch", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.quote_batch, args.policy, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
