#!/usr/bin/env python3
"""P7-03 policy-gated concentration and positive-correlation exposure guard."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "concentration_correlation_guard_contract.json"
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ConcentrationCorrelationError(ValueError):
    """Fail-closed P7-03 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConcentrationCorrelationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 2,
        "contract_version": "concentration_correlation_guard/2",
        "policy_schema_version": "concentration_correlation_policy/2",
        "input_schema_version": "concentration_correlation_input/2",
        "output_schema_version": "concentration_correlation_packet/2",
        "repository_default_status": "BLOCKED_UNTIL_EXTERNAL_POLICY_RATIFIED",
        "approval_mode": "EXPLICIT_CIO_RATIFIED_ONLY",
        "position_basis": "LONG_NAV_FRACTION",
        "theme_allocation_mode": "EXPLICIT_FRACTIONAL",
        "correlation_graph_rule": "POSITIVE_CORRELATION_AT_OR_ABOVE_RATIFIED_THRESHOLD",
        "pair_coverage": "COMPLETE_UNORDERED_ACTIVE_ASSET_PAIRS",
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "input_authority": {
            "guard_evaluation_input_authorized": True,
            "automatic_position_reduction_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "policy_authority": {
            "concentration_limit_definition_authorized": True,
            "correlation_method_definition_authorized": True,
            "automatic_position_reduction_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "concentration_correlation_evaluation_only": True,
            "repository_default_policy_authorized": False,
            "automatic_position_reduction_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ConcentrationCorrelationError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ConcentrationCorrelationError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConcentrationCorrelationError(code)
    return value


def _id(value, code: str) -> str:
    value = _text(value, code)
    if ID_RE.fullmatch(value) is None:
        raise ConcentrationCorrelationError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ConcentrationCorrelationError(code)
    return value


def _number(value, code: str, *, positive: bool = False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConcentrationCorrelationError(code)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise ConcentrationCorrelationError(code)
    if maximum is not None and value > maximum:
        raise ConcentrationCorrelationError(code)
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise ConcentrationCorrelationError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ConcentrationCorrelationError(code) from exc
    if parsed.isoformat() != value:
        raise ConcentrationCorrelationError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ConcentrationCorrelationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ConcentrationCorrelationError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ConcentrationCorrelationError(code)
    return value


def _interval(start, end, context: str) -> tuple[str, str | None]:
    start = _date(start, f"VALID_FROM_INVALID:{context}")
    if end is not None:
        end = _date(end, f"VALID_TO_INVALID:{context}")
        if end <= start:
            raise ConcentrationCorrelationError(f"EFFECTIVE_INTERVAL_EMPTY:{context}")
    return start, end


def _active(start: str, end: str | None, as_of: str) -> bool:
    return start <= as_of and (end is None or as_of < end)


def _rounded_sum(values) -> float:
    return round(math.fsum(values), 12)


def _validate_policy(value: dict, as_of: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_id", "status", "ratified_by",
        "ratified_at", "valid_from", "valid_to", "limits", "correlation",
        "policy_basis_ref", "policy_basis_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ConcentrationCorrelationError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["policy_authority"]
    ):
        raise ConcentrationCorrelationError("POLICY_IDENTITY_INVALID")
    policy_id = _id(value.get("policy_id"), "POLICY_ID_INVALID")
    ratified_at = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    start, end = _interval(value.get("valid_from"), value.get("valid_to"), policy_id)
    if ratified_at[:10] > start:
        raise ConcentrationCorrelationError("POLICY_RATIFIED_AFTER_EFFECTIVE_START")
    if not _active(start, end, as_of):
        raise ConcentrationCorrelationError("POLICY_NOT_EFFECTIVE")
    limits = value.get("limits")
    limit_fields = {
        "max_single_position_weight", "max_theme_exposure",
        "max_market_exposure", "max_correlated_cluster_exposure",
    }
    if not isinstance(limits, dict) or set(limits) != limit_fields:
        raise ConcentrationCorrelationError("POLICY_LIMIT_FIELDS_MISMATCH")
    limits = {
        key: _number(limits.get(key), f"POLICY_LIMIT_INVALID:{key}", positive=True)
        for key in sorted(limit_fields)
    }
    correlation = value.get("correlation")
    correlation_fields = {
        "method", "return_basis", "lookback_observations", "threshold",
        "pair_coverage_required",
    }
    if not isinstance(correlation, dict) or set(correlation) != correlation_fields:
        raise ConcentrationCorrelationError("POLICY_CORRELATION_FIELDS_MISMATCH")
    lookback = correlation.get("lookback_observations")
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 2:
        raise ConcentrationCorrelationError("POLICY_LOOKBACK_INVALID")
    if correlation.get("method") != "PEARSON":
        raise ConcentrationCorrelationError("POLICY_CORRELATION_METHOD_INVALID")
    correlation = {
        "method": "PEARSON",
        "return_basis": _text(
            correlation.get("return_basis"), "POLICY_RETURN_BASIS_INVALID"
        ),
        "lookback_observations": lookback,
        "threshold": _number(
            correlation.get("threshold"),
            "POLICY_CORRELATION_THRESHOLD_INVALID",
            positive=True,
            maximum=1,
        ),
        "pair_coverage_required": _number(
            correlation.get("pair_coverage_required"),
            "POLICY_PAIR_COVERAGE_INVALID",
            positive=True,
            maximum=1,
        ),
    }
    if correlation["pair_coverage_required"] != 1:
        raise ConcentrationCorrelationError("POLICY_PAIR_COVERAGE_MUST_BE_COMPLETE")
    normalized = {
        "schema_version": contract["policy_schema_version"],
        "contract_version": contract["contract_version"],
        "policy_id": policy_id,
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": ratified_at,
        "valid_from": start,
        "valid_to": end,
        "limits": limits,
        "correlation": correlation,
        "policy_basis_ref": _text(value.get("policy_basis_ref"), "POLICY_BASIS_REF_INVALID"),
        "policy_basis_sha256": _sha(
            value.get("policy_basis_sha256"), "POLICY_BASIS_SHA_INVALID"
        ),
        "authority": copy.deepcopy(contract["policy_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise ConcentrationCorrelationError("POLICY_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _position(row: dict, contract: dict) -> dict:
    fields = {
        "asset_id", "market", "portfolio_weight", "position_record_sha256",
        "asset_identity_sha256", "bucket_id", "theme_allocations",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ConcentrationCorrelationError("POSITION_FIELDS_MISMATCH")
    asset_id = _id(row.get("asset_id"), "ASSET_ID_INVALID")
    market = row.get("market")
    if market not in contract["allowed_markets"]:
        raise ConcentrationCorrelationError(f"MARKET_INVALID:{asset_id}:{market}")
    allocations = row.get("theme_allocations")
    if not isinstance(allocations, list) or not allocations:
        raise ConcentrationCorrelationError(f"THEME_ALLOCATIONS_EMPTY:{asset_id}")
    normalized_allocations = []
    seen = set()
    for allocation in allocations:
        if not isinstance(allocation, dict) or set(allocation) != {
            "theme_id", "fraction", "membership_evidence_sha256"
        }:
            raise ConcentrationCorrelationError(f"THEME_ALLOCATION_FIELDS_MISMATCH:{asset_id}")
        theme_id = _id(allocation.get("theme_id"), f"THEME_ID_INVALID:{asset_id}")
        if theme_id in seen:
            raise ConcentrationCorrelationError(f"THEME_DUPLICATE:{asset_id}:{theme_id}")
        seen.add(theme_id)
        normalized_allocations.append({
            "theme_id": theme_id,
            "fraction": _number(
                allocation.get("fraction"),
                f"THEME_FRACTION_INVALID:{asset_id}:{theme_id}",
                positive=True,
                maximum=1,
            ),
            "membership_evidence_sha256": _sha(
                allocation.get("membership_evidence_sha256"),
                f"THEME_MEMBERSHIP_SHA_INVALID:{asset_id}:{theme_id}",
            ),
        })
    normalized_allocations.sort(key=lambda item: item["theme_id"])
    if abs(_rounded_sum(item["fraction"] for item in normalized_allocations) - 1) > 1e-12:
        raise ConcentrationCorrelationError(f"THEME_FRACTIONS_MUST_SUM_TO_ONE:{asset_id}")
    return {
        "asset_id": asset_id,
        "market": market,
        "portfolio_weight": _number(
            row.get("portfolio_weight"), f"POSITION_WEIGHT_INVALID:{asset_id}", positive=True
        ),
        "position_record_sha256": _sha(
            row.get("position_record_sha256"), f"POSITION_RECORD_SHA_INVALID:{asset_id}"
        ),
        "asset_identity_sha256": _sha(
            row.get("asset_identity_sha256"), f"ASSET_IDENTITY_SHA_INVALID:{asset_id}"
        ),
        "bucket_id": _id(row.get("bucket_id"), f"BUCKET_ID_INVALID:{asset_id}"),
        "theme_allocations": normalized_allocations,
    }


def _correlation(row: dict, asset_ids: set[str], as_of: str) -> dict:
    fields = {
        "asset_a", "asset_b", "correlation", "as_of_date", "available_at_utc",
        "observation_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ConcentrationCorrelationError("CORRELATION_FIELDS_MISMATCH")
    left = _id(row.get("asset_a"), "CORRELATION_ASSET_A_INVALID")
    right = _id(row.get("asset_b"), "CORRELATION_ASSET_B_INVALID")
    if left not in asset_ids or right not in asset_ids or left == right:
        raise ConcentrationCorrelationError(f"CORRELATION_PAIR_INVALID:{left}:{right}")
    left, right = sorted((left, right))
    if _date(row.get("as_of_date"), "CORRELATION_AS_OF_INVALID") != as_of:
        raise ConcentrationCorrelationError(f"CORRELATION_AS_OF_MISMATCH:{left}:{right}")
    value = row.get("correlation")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConcentrationCorrelationError(f"CORRELATION_VALUE_INVALID:{left}:{right}")
    if value < -1 or value > 1:
        raise ConcentrationCorrelationError(f"CORRELATION_VALUE_INVALID:{left}:{right}")
    return {
        "asset_a": left,
        "asset_b": right,
        "correlation": value,
        "as_of_date": as_of,
        "available_at_utc": _utc(
            row.get("available_at_utc"), f"CORRELATION_AVAILABLE_AT_INVALID:{left}:{right}"
        ),
        "observation_sha256": _sha(
            row.get("observation_sha256"), f"CORRELATION_SHA_INVALID:{left}:{right}"
        ),
    }


def _validate_input(value: dict, as_of: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "snapshot_id", "as_of_date",
        "generated_at_utc", "portfolio_snapshot_sha256",
        "bucket_membership_packet_sha256", "theme_taxonomy_packet_sha256",
        "correlation_dataset_sha256", "positions", "correlations", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ConcentrationCorrelationError("INPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise ConcentrationCorrelationError("INPUT_IDENTITY_INVALID")
    if _date(value.get("as_of_date"), "INPUT_AS_OF_INVALID") != as_of:
        raise ConcentrationCorrelationError("INPUT_AS_OF_MISMATCH")
    raw_positions = value.get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        raise ConcentrationCorrelationError("POSITIONS_EMPTY")
    positions = sorted((_position(row, contract) for row in raw_positions), key=lambda x: x["asset_id"])
    asset_ids = [row["asset_id"] for row in positions]
    if len(asset_ids) != len(set(asset_ids)):
        raise ConcentrationCorrelationError("POSITION_ASSET_DUPLICATE")
    raw_correlations = value.get("correlations")
    if not isinstance(raw_correlations, list):
        raise ConcentrationCorrelationError("CORRELATIONS_INVALID")
    correlations = sorted(
        (_correlation(row, set(asset_ids), as_of) for row in raw_correlations),
        key=lambda row: (row["asset_a"], row["asset_b"]),
    )
    actual_pairs = [(row["asset_a"], row["asset_b"]) for row in correlations]
    if len(actual_pairs) != len(set(actual_pairs)):
        raise ConcentrationCorrelationError("CORRELATION_PAIR_DUPLICATE")
    expected_pairs = [
        (left, right)
        for index, left in enumerate(asset_ids)
        for right in asset_ids[index + 1:]
    ]
    if actual_pairs != expected_pairs:
        raise ConcentrationCorrelationError("CORRELATION_PAIR_COVERAGE_INCOMPLETE")
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "snapshot_id": _id(value.get("snapshot_id"), "SNAPSHOT_ID_INVALID"),
        "as_of_date": as_of,
        "generated_at_utc": _utc(value.get("generated_at_utc"), "GENERATED_AT_INVALID"),
        "portfolio_snapshot_sha256": _sha(
            value.get("portfolio_snapshot_sha256"), "PORTFOLIO_SNAPSHOT_SHA_INVALID"
        ),
        "bucket_membership_packet_sha256": _sha(
            value.get("bucket_membership_packet_sha256"), "BUCKET_MEMBERSHIP_SHA_INVALID"
        ),
        "theme_taxonomy_packet_sha256": _sha(
            value.get("theme_taxonomy_packet_sha256"), "THEME_TAXONOMY_SHA_INVALID"
        ),
        "correlation_dataset_sha256": _sha(
            value.get("correlation_dataset_sha256"), "CORRELATION_DATASET_SHA_INVALID"
        ),
        "positions": positions,
        "correlations": correlations,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise ConcentrationCorrelationError("INPUT_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _assessment(scope_type: str, scope_id: str, exposure: float, limit: float) -> dict:
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "exposure": exposure,
        "limit": limit,
        "result": "BREACH" if exposure > limit else "PASS",
    }


def _clusters(positions: list[dict], correlations: list[dict], threshold: float) -> list[list[str]]:
    parent = {row["asset_id"]: row["asset_id"] for row in positions}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for row in correlations:
        if row["correlation"] >= threshold:
            union(row["asset_a"], row["asset_b"])
    groups: dict[str, list[str]] = {}
    for asset_id in sorted(parent):
        groups.setdefault(find(asset_id), []).append(asset_id)
    return sorted((members for members in groups.values() if len(members) >= 2), key=lambda x: tuple(x))


def _source_packet(validated: dict) -> dict:
    packet = copy.deepcopy(validated["normalized"])
    packet["packet_sha256"] = validated["packet_sha256"]
    return packet


def _assemble(checked: dict, policy: dict, as_of: str, contract: dict) -> dict:
    source = checked["normalized"]
    limits = policy["normalized"]["limits"]
    positions = source["positions"]

    position_assessments = [
        _assessment(
            "POSITION", row["asset_id"], row["portfolio_weight"],
            limits["max_single_position_weight"],
        )
        for row in positions
    ]
    market_totals: dict[str, list[float]] = {}
    theme_totals: dict[str, list[float]] = {}
    weights = {row["asset_id"]: row["portfolio_weight"] for row in positions}
    for row in positions:
        market_totals.setdefault(row["market"], []).append(row["portfolio_weight"])
        for allocation in row["theme_allocations"]:
            theme_totals.setdefault(allocation["theme_id"], []).append(
                row["portfolio_weight"] * allocation["fraction"]
            )
    market_assessments = [
        _assessment("MARKET", scope_id, _rounded_sum(values), limits["max_market_exposure"])
        for scope_id, values in sorted(market_totals.items())
    ]
    theme_assessments = [
        _assessment("THEME", scope_id, _rounded_sum(values), limits["max_theme_exposure"])
        for scope_id, values in sorted(theme_totals.items())
    ]
    correlation_threshold = policy["normalized"]["correlation"]["threshold"]
    clusters = _clusters(positions, source["correlations"], correlation_threshold)
    cluster_assessments = []
    for members in clusters:
        row = _assessment(
            "CORRELATED_CLUSTER",
            "+".join(members),
            _rounded_sum(weights[item] for item in members),
            limits["max_correlated_cluster_exposure"],
        )
        row["members"] = members
        cluster_assessments.append(row)
    all_assessments = (
        position_assessments + market_assessments + theme_assessments + cluster_assessments
    )
    breaches = [
        {"scope_type": row["scope_type"], "scope_id": row["scope_id"]}
        for row in all_assessments if row["result"] == "BREACH"
    ]
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "LIMIT_BREACH" if breaches else "WITHIN_RATIFIED_LIMITS",
        "as_of_date": as_of,
        "policy_id": policy["normalized"]["policy_id"],
        "snapshot_id": source["snapshot_id"],
        "position_assessments": position_assessments,
        "market_assessments": market_assessments,
        "theme_assessments": theme_assessments,
        "correlated_cluster_assessments": cluster_assessments,
        "breaches": breaches,
        "summary": {
            "position_count": len(positions),
            "market_count": len(market_assessments),
            "theme_count": len(theme_assessments),
            "complete_pair_count": len(source["correlations"]),
            "correlation_edge_count": sum(
                row["correlation"] >= correlation_threshold for row in source["correlations"]
            ),
            "correlated_cluster_count": len(cluster_assessments),
            "breach_count": len(breaches),
        },
        "recommended_action": None,
        "target_weights": None,
        "position_size": None,
        "order_intents": [],
        "source_packets": {
            "INPUT": _source_packet(checked),
            "POLICY": _source_packet(policy),
        },
        "lineage": {
            "policy_packet_sha256": policy["packet_sha256"],
            "input_packet_sha256": checked["packet_sha256"],
            "portfolio_snapshot_sha256": source["portfolio_snapshot_sha256"],
            "bucket_membership_packet_sha256": source["bucket_membership_packet_sha256"],
            "theme_taxonomy_packet_sha256": source["theme_taxonomy_packet_sha256"],
            "correlation_dataset_sha256": source["correlation_dataset_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "NO_REPOSITORY_DEFAULT_LIMITS",
            "NO_AUTOMATIC_POSITION_REDUCTION",
            "POSITION_SIZING_NOT_AUTHORIZED",
            "ORDER_NOT_AUTHORIZED",
        ],
    }
    return packet


def build_packet(
    input_value: dict,
    policy_value: dict,
    as_of_date: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    as_of = _date(as_of_date, "AS_OF_DATE_INVALID")
    policy = _validate_policy(policy_value, as_of, contract)
    checked = _validate_input(input_value, as_of, contract)
    packet = _assemble(checked, policy, as_of, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "as_of_date", "policy_id",
        "snapshot_id", "position_assessments", "market_assessments",
        "theme_assessments", "correlated_cluster_assessments", "breaches",
        "summary", "recommended_action", "target_weights", "position_size",
        "order_intents", "source_packets", "lineage", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ConcentrationCorrelationError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
    ):
        raise ConcentrationCorrelationError("OUTPUT_IDENTITY_INVALID")
    as_of = _date(packet.get("as_of_date"), "OUTPUT_AS_OF_INVALID")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {"INPUT", "POLICY"}:
        raise ConcentrationCorrelationError("OUTPUT_SOURCE_PACKETS_INVALID")
    checked = _validate_input(sources["INPUT"], as_of, contract)
    policy = _validate_policy(sources["POLICY"], as_of, contract)
    expected = _assemble(checked, policy, as_of, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_PACKET_SHA_INVALID")
    if actual != expected:
        raise ConcentrationCorrelationError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise ConcentrationCorrelationError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ConcentrationCorrelationError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(input_path: Path, policy_path: Path, as_of_date: str, output_path: Path) -> int:
    try:
        packet = build_packet(_read_json(input_path), _read_json(policy_path), as_of_date)
        write_json_atomic(output_path, packet)
        return 0
    except (ConcentrationCorrelationError, OSError, TypeError, ValueError) as exc:
        print(f"Concentration/correlation guard failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("policy", type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.policy, args.as_of_date, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
