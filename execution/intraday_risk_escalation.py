#!/usr/bin/env python3
"""P9-05 externally ratified intraday risk escalation evaluator."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "intraday_risk_escalation_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


class IntradayRiskEscalationError(ValueError):
    """Fail-closed P9-05 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntradayRiskEscalationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "intraday_risk_escalation/1",
        "input_schema_version": "intraday_risk_observation_batch/1",
        "policy_schema_version": "intraday_risk_escalation_policy/1",
        "output_schema_version": "intraday_risk_escalation_packet/1",
        "markets": ["US", "KOREA", "CRYPTO"],
        "metrics": [
            "DRAWDOWN_FRACTION",
            "DOWN_GAP_FRACTION",
            "SPREAD_BPS",
            "RELATIVE_VOLUME_FRACTION",
        ],
        "risk_statuses": ["NORMAL", "ALERT"],
        "repository_default_policy": "ABSENT",
        "policy_requirement": "EXTERNAL_RATIFIED_POLICY_REQUIRED",
        "threshold_semantics": {
            "max_threshold": "GREATER_THAN_IS_ALERT",
            "min_threshold": "LESS_THAN_IS_ALERT",
        },
        "rounding_digits": 12,
        "input_authority": {
            "normalized_intraday_risk_observation_only": True,
            "risk_policy_authorized": False,
            "exposure_reduction_authorized": False,
            "stop_candidate_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "policy_authority": {
            "risk_threshold_policy_only": True,
            "exposure_reduction_authorized": False,
            "stop_candidate_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "intraday_risk_evaluation_only": True,
            "upstream_semantic_interpretation_authorized": False,
            "exposure_reduction_candidate_authorized": False,
            "stop_candidate_authorized": False,
            "action_generation_authorized": False,
            "position_sizing_authorized": False,
            "order_generation_authorized": False,
            "notification_authorized": False,
            "broker_submission_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise IntradayRiskEscalationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise IntradayRiskEscalationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise IntradayRiskEscalationError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise IntradayRiskEscalationError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise IntradayRiskEscalationError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    return value


def _decimal(value, code: str, *, positive: bool) -> Decimal:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise IntradayRiskEscalationError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise IntradayRiskEscalationError(code) from exc
    if (positive and parsed <= 0) or (not positive and parsed < 0):
        raise IntradayRiskEscalationError(code)
    return parsed


def _rounded(value: Decimal, digits: int) -> str:
    quantum = Decimal(1).scaleb(-digits)
    result = value.quantize(quantum, rounding=ROUND_HALF_UP)
    text = format(result, "f").rstrip("0").rstrip(".")
    return text or "0"


def _validate_policy(value: dict, observed: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_id", "status", "ratified_by",
        "ratified_at", "effective_from", "effective_to", "thresholds_by_market",
        "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskEscalationError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["policy_authority"]
    ):
        raise IntradayRiskEscalationError("POLICY_IDENTITY_INVALID")
    policy_id = _token(value.get("policy_id"), "POLICY_ID_INVALID")
    ratified = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    effective_from = _utc(value.get("effective_from"), "POLICY_EFFECTIVE_FROM_INVALID")
    effective_to = _utc(value.get("effective_to"), "POLICY_EFFECTIVE_TO_INVALID")
    if ratified > effective_from or effective_to <= effective_from:
        raise IntradayRiskEscalationError("POLICY_INTERVAL_INVALID")
    if not effective_from <= observed < effective_to:
        raise IntradayRiskEscalationError("POLICY_NOT_EFFECTIVE")
    thresholds = value.get("thresholds_by_market")
    if not isinstance(thresholds, dict) or set(thresholds) != set(contract["markets"]):
        raise IntradayRiskEscalationError("POLICY_MARKET_COVERAGE_INVALID")
    threshold_fields = {
        "max_drawdown_fraction", "max_down_gap_fraction", "max_spread_bps",
        "min_relative_volume_fraction", "policy_basis_ref", "policy_basis_sha256",
    }
    normalized_thresholds = {}
    for market in contract["markets"]:
        row = thresholds[market]
        if not isinstance(row, dict) or set(row) != threshold_fields:
            raise IntradayRiskEscalationError(f"POLICY_THRESHOLD_FIELDS_MISMATCH:{market}")
        min_relative = _decimal(
            row.get("min_relative_volume_fraction"),
            f"POLICY_MIN_RELATIVE_VOLUME_INVALID:{market}",
            positive=True,
        )
        if min_relative > 1:
            raise IntradayRiskEscalationError(f"POLICY_MIN_RELATIVE_VOLUME_INVALID:{market}")
        normalized_thresholds[market] = {
            "max_drawdown_fraction": row["max_drawdown_fraction"],
            "max_down_gap_fraction": row["max_down_gap_fraction"],
            "max_spread_bps": row["max_spread_bps"],
            "min_relative_volume_fraction": row["min_relative_volume_fraction"],
            "policy_basis_ref": _text(row.get("policy_basis_ref"), f"POLICY_BASIS_REF_INVALID:{market}"),
            "policy_basis_sha256": _sha(row.get("policy_basis_sha256"), f"POLICY_BASIS_SHA_INVALID:{market}"),
        }
        _decimal(row.get("max_drawdown_fraction"), f"POLICY_MAX_DRAWDOWN_INVALID:{market}", positive=True)
        _decimal(row.get("max_down_gap_fraction"), f"POLICY_MAX_GAP_INVALID:{market}", positive=True)
        _decimal(row.get("max_spread_bps"), f"POLICY_MAX_SPREAD_INVALID:{market}", positive=True)
    digest = _sha(value.get("packet_sha256"), "POLICY_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise IntradayRiskEscalationError("POLICY_SHA_MISMATCH")
    return {
        "policy_id": policy_id,
        "packet_sha256": digest,
        "thresholds_by_market": normalized_thresholds,
    }


def _validate_batch(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "observations", "upstream_lineage", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskEscalationError("BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise IntradayRiskEscalationError("BATCH_IDENTITY_INVALID")
    observed_at = value.get("observed_at")
    observed = _utc(observed_at, "BATCH_OBSERVED_AT_INVALID")
    upstream = value.get("upstream_lineage")
    upstream_fields = {
        "entry_exit_trigger_eligibility_packet_sha256",
        "important_event_detection_packet_sha256",
        "concentration_guard_packet_sha256",
        "planned_loss_budget_packet_sha256",
    }
    if not isinstance(upstream, dict) or set(upstream) != upstream_fields:
        raise IntradayRiskEscalationError("UPSTREAM_LINEAGE_FIELDS_MISMATCH")
    checked_upstream = {
        key: _sha(upstream.get(key), f"UPSTREAM_SHA_INVALID:{key}")
        for key in sorted(upstream)
    }
    raw = value.get("observations")
    if not isinstance(raw, list):
        raise IntradayRiskEscalationError("OBSERVATIONS_NOT_LIST")
    observation_fields = {
        "subject_id", "market", "reference_close", "open_price", "last_price",
        "bid_price", "ask_price", "cumulative_volume", "expected_volume_to_time",
        "provider_timestamp", "received_at", "source_ref", "source_sha256",
    }
    rows = []
    for index, row in enumerate(raw):
        context = f"observation:{index}"
        if not isinstance(row, dict) or set(row) != observation_fields:
            raise IntradayRiskEscalationError(f"OBSERVATION_FIELDS_MISMATCH:{context}")
        market = row.get("market")
        if market not in contract["markets"]:
            raise IntradayRiskEscalationError(f"OBSERVATION_MARKET_INVALID:{context}")
        provider = _utc(row.get("provider_timestamp"), f"PROVIDER_TIME_INVALID:{context}")
        received = _utc(row.get("received_at"), f"RECEIVED_AT_INVALID:{context}")
        if not provider <= received <= observed:
            raise IntradayRiskEscalationError(f"OBSERVATION_TIME_ORDER_INVALID:{context}")
        bid = _decimal(row.get("bid_price"), f"BID_PRICE_INVALID:{context}", positive=True)
        ask = _decimal(row.get("ask_price"), f"ASK_PRICE_INVALID:{context}", positive=True)
        if bid > ask:
            raise IntradayRiskEscalationError(f"CROSSED_QUOTE_INVALID:{context}")
        for field in ("reference_close", "open_price", "last_price", "expected_volume_to_time"):
            _decimal(row.get(field), f"{field.upper()}_INVALID:{context}", positive=True)
        _decimal(row.get("cumulative_volume"), f"CUMULATIVE_VOLUME_INVALID:{context}", positive=False)
        rows.append({
            "subject_id": _token(row.get("subject_id"), f"SUBJECT_ID_INVALID:{context}"),
            "market": market,
            "reference_close": row["reference_close"],
            "open_price": row["open_price"],
            "last_price": row["last_price"],
            "bid_price": row["bid_price"],
            "ask_price": row["ask_price"],
            "cumulative_volume": row["cumulative_volume"],
            "expected_volume_to_time": row["expected_volume_to_time"],
            "provider_timestamp": row["provider_timestamp"],
            "received_at": row["received_at"],
            "source_ref": _text(row.get("source_ref"), f"SOURCE_REF_INVALID:{context}"),
            "source_sha256": _sha(row.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"),
        })
    rows.sort(key=lambda row: (contract["markets"].index(row["market"]), row["subject_id"]))
    keys = [(row["market"], row["subject_id"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise IntradayRiskEscalationError("OBSERVATION_DUPLICATE")
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": _token(value.get("batch_id"), "BATCH_ID_INVALID"),
        "observed_at": observed_at,
        "observations": rows,
        "upstream_lineage": copy.deepcopy(value["upstream_lineage"]),
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "BATCH_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise IntradayRiskEscalationError("BATCH_SHA_MISMATCH")
    return {
        "normalized": normalized,
        "observed": observed,
        "packet_sha256": digest,
        "upstream_lineage": checked_upstream,
    }


def _metric(metric: str, observed: Decimal, threshold: Decimal, alert: bool, digits: int) -> dict:
    return {
        "metric": metric,
        "observed": _rounded(observed, digits),
        "threshold": _rounded(threshold, digits),
        "result": "ALERT" if alert else "PASS",
    }


def _evaluate_row(row: dict, thresholds: dict, contract: dict) -> dict:
    reference = Decimal(row["reference_close"])
    opened = Decimal(row["open_price"])
    last = Decimal(row["last_price"])
    bid = Decimal(row["bid_price"])
    ask = Decimal(row["ask_price"])
    cumulative = Decimal(row["cumulative_volume"])
    expected = Decimal(row["expected_volume_to_time"])
    drawdown = max(Decimal(0), (reference - last) / reference)
    down_gap = max(Decimal(0), (reference - opened) / reference)
    midpoint = (bid + ask) / Decimal(2)
    spread_bps = ((ask - bid) / midpoint) * Decimal(10000)
    relative_volume = cumulative / expected
    max_drawdown = Decimal(thresholds["max_drawdown_fraction"])
    max_gap = Decimal(thresholds["max_down_gap_fraction"])
    max_spread = Decimal(thresholds["max_spread_bps"])
    min_volume = Decimal(thresholds["min_relative_volume_fraction"])
    metrics = [
        _metric("DRAWDOWN_FRACTION", drawdown, max_drawdown, drawdown > max_drawdown, contract["rounding_digits"]),
        _metric("DOWN_GAP_FRACTION", down_gap, max_gap, down_gap > max_gap, contract["rounding_digits"]),
        _metric("SPREAD_BPS", spread_bps, max_spread, spread_bps > max_spread, contract["rounding_digits"]),
        _metric("RELATIVE_VOLUME_FRACTION", relative_volume, min_volume, relative_volume < min_volume, contract["rounding_digits"]),
    ]
    alert_reasons = [item["metric"] for item in metrics if item["result"] == "ALERT"]
    return {
        "subject_id": row["subject_id"],
        "market": row["market"],
        "risk_status": "ALERT" if alert_reasons else "NORMAL",
        "metrics": metrics,
        "alert_reasons": alert_reasons,
        "exposure_reduction_candidate": None,
        "stop_candidate": None,
        "action": None,
        "position_size": None,
        "order_intent": None,
        "source_observation": copy.deepcopy(row),
    }


def _assemble(batch: dict, policy: dict, contract: dict) -> dict:
    rows = [
        _evaluate_row(
            row,
            policy["thresholds_by_market"][row["market"]],
            contract,
        )
        for row in batch["normalized"]["observations"]
    ]
    metric_counts = {
        metric: sum(
            item["metric"] == metric and item["result"] == "ALERT"
            for row in rows
            for item in row["metrics"]
        )
        for metric in contract["metrics"]
    }
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "INTRADAY_RISK_EVALUATED_NO_ACTION_AUTHORITY",
        "batch_id": batch["normalized"]["batch_id"],
        "observed_at": batch["normalized"]["observed_at"],
        "policy_id": policy["policy_id"],
        "results": rows,
        "summary": {
            "subject_count": len(rows),
            "normal_count": sum(row["risk_status"] == "NORMAL" for row in rows),
            "alert_count": sum(row["risk_status"] == "ALERT" for row in rows),
            "alert_count_by_metric": metric_counts,
            "exposure_reduction_candidate_count": 0,
            "stop_candidate_count": 0,
            "action_count": 0,
            "order_count": 0,
        },
        "source_batch": copy.deepcopy(batch["normalized"]),
        "policy_packet": copy.deepcopy(policy["packet"]),
        "lineage": {
            "observation_batch_sha256": batch["packet_sha256"],
            "policy_sha256": policy["packet_sha256"],
            **copy.deepcopy(batch["upstream_lineage"]),
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "UPSTREAM_PACKETS_ARE_LINEAGE_ONLY_NOT_SEMANTIC_AUTHORITY",
            "EXPOSURE_REDUCTION_POLICY_NOT_AUTHORIZED",
            "STOP_CANDIDATE_POLICY_NOT_AUTHORIZED",
            "NOTIFICATION_NOT_AUTHORIZED",
            "ACTION_ORDER_PRODUCTION_TRADING_NOT_AUTHORIZED",
        ],
    }


def build_packet(batch_value: dict, policy_value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    batch = _validate_batch(batch_value, contract)
    policy = _validate_policy(policy_value, batch["observed"], contract)
    policy["packet"] = copy.deepcopy(policy_value)
    packet = _assemble(batch, policy, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "batch_id", "observed_at",
        "policy_id", "results", "summary", "source_batch", "policy_packet",
        "lineage", "authority", "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise IntradayRiskEscalationError("OUTPUT_FIELDS_MISMATCH")
    batch_value = copy.deepcopy(packet.get("source_batch"))
    if not isinstance(batch_value, dict):
        raise IntradayRiskEscalationError("OUTPUT_SOURCE_BATCH_INVALID")
    batch_value["packet_sha256"] = packet.get("lineage", {}).get("observation_batch_sha256")
    batch = _validate_batch(batch_value, contract)
    policy_value = packet.get("policy_packet")
    policy = _validate_policy(policy_value, batch["observed"], contract)
    policy["packet"] = copy.deepcopy(policy_value)
    expected = _assemble(batch, policy, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_SHA_INVALID")
    if actual != expected:
        raise IntradayRiskEscalationError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise IntradayRiskEscalationError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise IntradayRiskEscalationError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
        write_json_atomic(output_path, build_packet(_read_json(batch_path), _read_json(policy_path)))
        return 0
    except (IntradayRiskEscalationError, OSError, TypeError, ValueError) as exc:
        print(f"Intraday risk escalation failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observation_batch", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.observation_batch, args.policy, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
