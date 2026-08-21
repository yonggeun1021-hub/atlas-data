#!/usr/bin/env python3
"""P10-03 evidence-bound Shadow error metric aggregation."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "shadow_error_metrics_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_comparison_validator():
    path = ROOT / "shadow" / "atlas_legacy_comparison.py"
    spec = importlib.util.spec_from_file_location("atlas_legacy_comparison_v4", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"COMPARISON_VALIDATOR_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMPARISON = _load_comparison_validator()


class ShadowErrorMetricsError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowErrorMetricsError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 3,
        "contract_version": "shadow_error_metrics/3",
        "input_schema_version": "shadow_error_assessment_batch/3",
        "output_schema_version": "shadow_error_metrics_packet/3",
        "comparison_schema_version": "atlas_legacy_comparison_packet/4",
        "comparison_contract_version": "atlas_legacy_comparison/4",
        "markets": ["COMMON", "US", "KOREA", "CRYPTO"],
        "metric_types": ["FALSE_POSITIVE", "MISS", "STALE", "SILENT_ERROR"],
        "metric_statuses": ["PRESENT", "ABSENT", "UNVERIFIED"],
        "assessment_policy": "EVERY_ASSESSMENT_CONTAINS_ALL_METRICS",
        "rate_policy": "PRESENT_DIVIDED_BY_PRESENT_PLUS_ABSENT_SIX_DP_HALF_EVEN",
        "zero_denominator_policy": "NULL_NOT_ZERO",
        "classification_policy": "EXTERNAL_EVIDENCE_BOUND_NO_INFERENCE",
        "input_authority": {
            "external_error_assessment_only": True,
            "metric_definition_authorized": False,
            "performance_interpretation_authorized": False,
            "strategy_change_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "error_metric_aggregation_only": True,
            "error_classification_authorized": False,
            "causal_interpretation_authorized": False,
            "performance_claim_authorized": False,
            "strategy_change_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ShadowErrorMetricsError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ShadowErrorMetricsError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ShadowErrorMetricsError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise ShadowErrorMetricsError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ShadowErrorMetricsError(code)
    return parsed


def _date(value, code: str) -> str:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ShadowErrorMetricsError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ShadowErrorMetricsError(code) from exc
    if parsed.isoformat() != value:
        raise ShadowErrorMetricsError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ShadowErrorMetricsError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ShadowErrorMetricsError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ShadowErrorMetricsError(code)
    return value


def _validate_metric(row: dict, assessment_time: dt.datetime, metric_type: str, contract: dict) -> dict:
    fields = {
        "metric_type", "status", "assessed_at", "evidence_ref", "evidence_sha256"
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ShadowErrorMetricsError(f"METRIC_FIELDS_MISMATCH:{metric_type}")
    assessed = _utc(row.get("assessed_at"), f"METRIC_TIME_INVALID:{metric_type}")
    status = row.get("status")
    if (
        row.get("metric_type") != metric_type
        or status not in contract["metric_statuses"]
        or assessed > assessment_time
    ):
        raise ShadowErrorMetricsError(f"METRIC_IDENTITY_INVALID:{metric_type}")
    evidence_ref = row.get("evidence_ref")
    evidence_sha = row.get("evidence_sha256")
    if status == "UNVERIFIED":
        if evidence_ref is not None or evidence_sha is not None:
            raise ShadowErrorMetricsError(f"UNVERIFIED_HAS_EVIDENCE:{metric_type}")
    else:
        _text(evidence_ref, f"METRIC_EVIDENCE_REF_INVALID:{metric_type}")
        _sha(evidence_sha, f"METRIC_EVIDENCE_SHA_INVALID:{metric_type}")
    return copy.deepcopy(row)


def _validate_comparison(value: dict, contract: dict) -> dict:
    try:
        checked = COMPARISON.validate_packet(value)
    except Exception as exc:
        raise ShadowErrorMetricsError(f"COMPARISON_PACKET_INVALID:{exc}") from exc
    if (
        checked.get("schema_version") != contract["comparison_schema_version"]
        or checked.get("contract_version")
        != contract["comparison_contract_version"]
    ):
        raise ShadowErrorMetricsError("COMPARISON_PACKET_IDENTITY_INVALID")
    return checked


def _validate_batch(value: dict, comparison: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "assessments", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ShadowErrorMetricsError("BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise ShadowErrorMetricsError("BATCH_IDENTITY_INVALID")
    batch_id = _token(value.get("batch_id"), "BATCH_ID_INVALID")
    observed = _utc(value.get("observed_at"), "BATCH_TIME_INVALID")
    comparison_time = _utc(
        comparison["observed_at"], "COMPARISON_OBSERVED_AT_INVALID"
    )
    if comparison_time > observed:
        raise ShadowErrorMetricsError("COMPARISON_PACKET_FROM_FUTURE")
    digest = _sha(value.get("packet_sha256"), "BATCH_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise ShadowErrorMetricsError("BATCH_SHA_MISMATCH")
    raw = value.get("assessments")
    if not isinstance(raw, list):
        raise ShadowErrorMetricsError("ASSESSMENTS_NOT_LIST")
    fields = {
        "assessment_id", "decision_id", "decision_date", "market", "window_id",
        "assessed_at", "comparison_ref", "comparison_sha256", "metrics",
    }
    assessments = []
    comparison_by_key = {
        (row["decision_id"], row["market"]): row
        for row in comparison["comparisons"]
    }
    comparison_by_decision = {}
    for item in comparison["comparisons"]:
        comparison_by_decision.setdefault(item["decision_id"], []).append(item)
    for index, row in enumerate(raw):
        context = f"assessment:{index}"
        if not isinstance(row, dict) or set(row) != fields:
            raise ShadowErrorMetricsError(f"ASSESSMENT_FIELDS_MISMATCH:{context}")
        assessed = _utc(row.get("assessed_at"), f"ASSESSMENT_TIME_INVALID:{context}")
        if assessed > observed:
            raise ShadowErrorMetricsError(f"ASSESSMENT_FROM_FUTURE:{context}")
        if assessed < comparison_time:
            raise ShadowErrorMetricsError(f"ASSESSMENT_BEFORE_COMPARISON:{context}")
        if row.get("market") not in contract["markets"]:
            raise ShadowErrorMetricsError(f"ASSESSMENT_MARKET_INVALID:{context}")
        checked = {
            "assessment_id": _token(row.get("assessment_id"), f"ASSESSMENT_ID_INVALID:{context}"),
            "decision_id": _text(row.get("decision_id"), f"DECISION_ID_INVALID:{context}"),
            "decision_date": _date(row.get("decision_date"), f"DECISION_DATE_INVALID:{context}"),
            "market": row["market"],
            "window_id": _token(row.get("window_id"), f"WINDOW_ID_INVALID:{context}"),
            "assessed_at": row["assessed_at"],
            "comparison_ref": _text(row.get("comparison_ref"), f"COMPARISON_REF_INVALID:{context}"),
            "comparison_sha256": _sha(row.get("comparison_sha256"), f"COMPARISON_SHA_INVALID:{context}"),
        }
        if checked["comparison_sha256"] != comparison["packet_sha256"]:
            raise ShadowErrorMetricsError(f"COMPARISON_SHA_MISMATCH:{context}")
        if checked["window_id"] != comparison["evaluation_window_id"]:
            raise ShadowErrorMetricsError(f"COMPARISON_WINDOW_MISMATCH:{context}")
        if checked["market"] == "COMMON":
            candidates = comparison_by_decision.get(checked["decision_id"], [])
            if not candidates:
                raise ShadowErrorMetricsError(f"COMPARISON_DECISION_MISSING:{context}")
            dates = {item["decision_date"] for item in candidates}
            if dates != {checked["decision_date"]}:
                raise ShadowErrorMetricsError(f"COMPARISON_DATE_MISMATCH:{context}")
        else:
            comparison_row = comparison_by_key.get(
                (checked["decision_id"], checked["market"])
            )
            if comparison_row is None:
                raise ShadowErrorMetricsError(f"COMPARISON_KEY_MISSING:{context}")
            if comparison_row["decision_date"] != checked["decision_date"]:
                raise ShadowErrorMetricsError(f"COMPARISON_DATE_MISMATCH:{context}")
        metrics = row.get("metrics")
        if not isinstance(metrics, list) or [item.get("metric_type") for item in metrics if isinstance(item, dict)] != contract["metric_types"]:
            raise ShadowErrorMetricsError(f"METRIC_SET_INVALID:{context}")
        checked["metrics"] = [
            _validate_metric(item, assessed, metric_type, contract)
            for item, metric_type in zip(metrics, contract["metric_types"])
        ]
        assessments.append(checked)
    assessments.sort(key=lambda row: (row["decision_date"], contract["markets"].index(row["market"]), row["assessment_id"]))
    ids = [row["assessment_id"] for row in assessments]
    keys = [(row["decision_id"], row["market"], row["window_id"]) for row in assessments]
    if len(ids) != len(set(ids)):
        raise ShadowErrorMetricsError("ASSESSMENT_ID_DUPLICATE")
    if len(keys) != len(set(keys)):
        raise ShadowErrorMetricsError("ASSESSMENT_KEY_DUPLICATE")
    return {"batch_id": batch_id, "observed_at": value["observed_at"], "assessments": assessments, "packet_sha256": digest}


def _rate(present: int, absent: int) -> str | None:
    denominator = present + absent
    if denominator == 0:
        return None
    return str(
        (Decimal(present) / Decimal(denominator)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_EVEN
        )
    )


def _aggregate(assessments: list[dict], contract: dict) -> list[dict]:
    rows = []
    for metric_type in contract["metric_types"]:
        statuses = [
            next(item for item in assessment["metrics"] if item["metric_type"] == metric_type)["status"]
            for assessment in assessments
        ]
        present = statuses.count("PRESENT")
        absent = statuses.count("ABSENT")
        unverified = statuses.count("UNVERIFIED")
        rows.append({
            "metric_type": metric_type,
            "present_count": present,
            "absent_count": absent,
            "unverified_count": unverified,
            "verified_denominator": present + absent,
            "rate": _rate(present, absent),
        })
    return rows


def build_packet(
    batch: dict, comparison_packet: dict, contract: dict | None = None
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    comparison = _validate_comparison(comparison_packet, contract)
    checked = _validate_batch(batch, comparison, contract)
    packet = build_packet_unchecked(
        checked, batch, comparison_packet, comparison, contract
    )
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(packet, dict):
        raise ShadowErrorMetricsError("PACKET_NOT_OBJECT")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {
        "ASSESSMENT_BATCH", "ATLAS_LEGACY_COMPARISON"
    }:
        raise ShadowErrorMetricsError("PACKET_SOURCE_FIELDS_MISMATCH")
    batch = sources["ASSESSMENT_BATCH"]
    comparison_packet = sources["ATLAS_LEGACY_COMPARISON"]
    comparison = _validate_comparison(comparison_packet, contract)
    checked = _validate_batch(batch, comparison, contract)
    expected = build_packet_unchecked(
        checked, batch, comparison_packet, comparison, contract
    )
    if packet != expected:
        raise ShadowErrorMetricsError("PACKET_CONTENT_MISMATCH")
    return copy.deepcopy(packet)


def build_packet_unchecked(
    checked: dict,
    batch: dict,
    comparison_packet: dict,
    comparison: dict,
    contract: dict,
) -> dict:
    assessments = checked["assessments"]
    metrics = _aggregate(assessments, contract)
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "ERROR_METRICS_AGGREGATED_NO_CAUSAL_AUTHORITY",
        "observed_at": checked["observed_at"],
        "assessment_count": len(assessments),
        "assessments": assessments,
        "metrics": metrics,
        "by_market": {market: sum(row["market"] == market for row in assessments) for market in contract["markets"]},
        "summary": {
            "metric_count": len(metrics),
            "fully_verified_assessment_count": sum(all(item["status"] != "UNVERIFIED" for item in row["metrics"]) for row in assessments),
            "any_error_present_assessment_count": sum(any(item["status"] == "PRESENT" for item in row["metrics"]) for row in assessments),
            "zero_denominator_metric_count": sum(row["rate"] is None for row in metrics),
            "causal_conclusion": None,
            "strategy_change": None,
        },
        "lineage": {
            "assessment_batch_id": checked["batch_id"],
            "assessment_batch_sha256": checked["packet_sha256"],
            "comparison_evaluation_window_id": comparison["evaluation_window_id"],
            "comparison_packet_sha256": comparison["packet_sha256"],
        },
        "source_packets": {
            "ASSESSMENT_BATCH": copy.deepcopy(batch),
            "ATLAS_LEGACY_COMPARISON": copy.deepcopy(comparison_packet),
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "METRIC_DEFINITIONS_REQUIRE_EXTERNAL_RATIFICATION",
            "LIVE_SHADOW_ASSESSMENTS_NOT_ESTABLISHED",
            "CAUSAL_INTERPRETATION_NOT_AUTHORIZED",
            "STRATEGY_CHANGE_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ShadowErrorMetricsError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(input_path: Path, comparison_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(
            output_path,
            build_packet(_read_json(input_path), _read_json(comparison_path)),
        )
        return 0
    except (ShadowErrorMetricsError, OSError, TypeError, ValueError) as exc:
        print(f"Shadow error metrics failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("comparison", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.comparison, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
