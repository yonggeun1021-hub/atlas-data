#!/usr/bin/env python3
"""P7-05 explicit Crypto exposure, planned-loss, and volatility limit guard."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "crypto_exposure_limit_contract.json"
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{1,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class CryptoExposureLimitError(ValueError):
    """Fail-closed P7-05 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _same_exact(actual, expected) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _same_exact(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_exact(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoExposureLimitError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_exposure_limit/1",
        "policy_schema_version": "crypto_exposure_policy/1",
        "input_schema_version": "crypto_exposure_input/1",
        "output_schema_version": "crypto_exposure_packet/2",
        "repository_default_status": "PAPER_LIMITS_RATIFIED_NATURAL_VERIFICATION_PENDING",
        "approval_mode": "EXPLICIT_CIO_RATIFIED_ONLY",
        "market": "CRYPTO",
        "budget_unit": "NAV_FRACTION",
        "volatility_unit": "ANNUALIZED_FRACTION",
        "volatility_transform_version": "btc_risk/v1",
        "volatility_estimator": "sqrt_mean_squared_simple_returns",
        "volatility_lookback_returns": 30,
        "volatility_annualization_days": 365,
        "position_mode": "LONG_ONLY_EXPLICIT_HOLDINGS",
        "canonical_wbs": {
            "page_id": "3bf9f2d7-3c84-816c-ac6a-e59938e2d99d",
            "order": 705,
            "work_item": "P7-05",
            "title": "Crypto separate exposure limit",
            "status": "🔵 검증대기",
            "snapshot_sha256": (
                "09cab6d8c7065a5952fbc480195117ac92e3331164d94a5179b6fb8c0763744f"
            ),
            "row_sha256": (
                "0881d4d107b79b9580a39e2b64f2a22b0cc1c9cb443d0a5e65eea7217d29d5bd"
            ),
        },
        "ratified_paper_limits": {
            "max_per_trade_planned_loss_nav_fraction": "0.0025",
            "max_total_crypto_exposure_nav_fraction": "0.05",
            "max_single_asset_exposure_nav_fraction": "0.02",
            "max_concurrent_positions": 3,
        },
        "unresolved_limits": {
            "max_total_planned_loss": {"value": None, "state": "UNKNOWN"},
            "max_annualized_realized_volatility": {
                "value": None,
                "state": "UNKNOWN",
            },
        },
        "accepted_market_theme_budget_statuses": [
            "LIMIT_BREACH", "WITHIN_RATIFIED_BUDGET"
        ],
        "input_authority": {
            "crypto_exposure_measurement_authorized": True,
            "crypto_limit_definition_authorized": False,
            "automatic_position_reduction_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "policy_authority": {
            "crypto_limit_definition_authorized": True,
            "automatic_position_reduction_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "crypto_exposure_limit_evaluation_only": True,
            "repository_default_policy_authorized": False,
            "stress_regime_interpretation_authorized": False,
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
        raise CryptoExposureLimitError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if not _same_exact(value.get(key), expected_value):
            raise CryptoExposureLimitError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CryptoExposureLimitError(code)
    return value


def _id(value, code: str) -> str:
    value = _text(value, code)
    if ID_RE.fullmatch(value) is None:
        raise CryptoExposureLimitError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CryptoExposureLimitError(code)
    return value


def _number(value, code: str, *, positive: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CryptoExposureLimitError(code)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise CryptoExposureLimitError(code)
    return value


def _fraction_text(value, code: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0(?:\.\d+)?", value):
        raise CryptoExposureLimitError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CryptoExposureLimitError(code) from exc
    if parsed < 0 or parsed > 1:
        raise CryptoExposureLimitError(code)
    return value


def _positive_int(value, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise CryptoExposureLimitError(code)
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise CryptoExposureLimitError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CryptoExposureLimitError(code) from exc
    if parsed.isoformat() != value:
        raise CryptoExposureLimitError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CryptoExposureLimitError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CryptoExposureLimitError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise CryptoExposureLimitError(code)
    return value


def _interval(start, end, context: str) -> tuple[str, str | None]:
    start = _date(start, f"VALID_FROM_INVALID:{context}")
    if end is not None:
        end = _date(end, f"VALID_TO_INVALID:{context}")
        if end <= start:
            raise CryptoExposureLimitError(f"EFFECTIVE_INTERVAL_EMPTY:{context}")
    return start, end


def _active(start: str, end: str | None, as_of: str) -> bool:
    return start <= as_of and (end is None or as_of < end)


def _rounded_sum(values) -> float:
    return round(math.fsum(values), 12)


def _validate_policy(value: dict, as_of: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_id", "status", "ratified_by",
        "ratified_at", "valid_from", "valid_to", "active_limits", "unresolved_limits",
        "volatility_requirement", "policy_basis_ref", "policy_basis_sha256", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoExposureLimitError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED_PAPER_ONLY"
        or value.get("ratified_by") != "CIO"
        or not _same_exact(value.get("authority"), contract["policy_authority"])
    ):
        raise CryptoExposureLimitError("POLICY_IDENTITY_INVALID")
    policy_id = _id(value.get("policy_id"), "POLICY_ID_INVALID")
    ratified_at = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    start, end = _interval(value.get("valid_from"), value.get("valid_to"), policy_id)
    if ratified_at[:10] > start:
        raise CryptoExposureLimitError("POLICY_RATIFIED_AFTER_EFFECTIVE_START")
    if not _active(start, end, as_of):
        raise CryptoExposureLimitError("POLICY_NOT_EFFECTIVE")
    limits = value.get("active_limits")
    expected_limits = contract["ratified_paper_limits"]
    if not isinstance(limits, dict) or set(limits) != set(expected_limits):
        raise CryptoExposureLimitError("POLICY_LIMIT_FIELDS_MISMATCH")
    normalized_limits = {
        "max_per_trade_planned_loss_nav_fraction": _fraction_text(
            limits.get("max_per_trade_planned_loss_nav_fraction"),
            "POLICY_LIMIT_INVALID:max_per_trade_planned_loss_nav_fraction",
        ),
        "max_total_crypto_exposure_nav_fraction": _fraction_text(
            limits.get("max_total_crypto_exposure_nav_fraction"),
            "POLICY_LIMIT_INVALID:max_total_crypto_exposure_nav_fraction",
        ),
        "max_single_asset_exposure_nav_fraction": _fraction_text(
            limits.get("max_single_asset_exposure_nav_fraction"),
            "POLICY_LIMIT_INVALID:max_single_asset_exposure_nav_fraction",
        ),
        "max_concurrent_positions": _positive_int(
            limits.get("max_concurrent_positions"),
            "POLICY_LIMIT_INVALID:max_concurrent_positions",
        ),
    }
    if normalized_limits != expected_limits:
        raise CryptoExposureLimitError("POLICY_LIMITS_NOT_CANONICAL_PAPER_V1")
    if not _same_exact(value.get("unresolved_limits"), contract["unresolved_limits"]):
        raise CryptoExposureLimitError("POLICY_UNRESOLVED_LIMITS_MISMATCH")
    requirement = value.get("volatility_requirement")
    expected_requirement = {
        "unit": contract["volatility_unit"],
        "transform_version": contract["volatility_transform_version"],
        "estimator": contract["volatility_estimator"],
        "lookback_returns": contract["volatility_lookback_returns"],
        "annualization_days": contract["volatility_annualization_days"],
    }
    if not _same_exact(requirement, expected_requirement):
        raise CryptoExposureLimitError("POLICY_VOLATILITY_REQUIREMENT_MISMATCH")
    normalized = {
        "schema_version": contract["policy_schema_version"],
        "contract_version": contract["contract_version"],
        "policy_id": policy_id,
        "status": "RATIFIED_PAPER_ONLY",
        "ratified_by": "CIO",
        "ratified_at": ratified_at,
        "valid_from": start,
        "valid_to": end,
        "active_limits": copy.deepcopy(normalized_limits),
        "unresolved_limits": copy.deepcopy(contract["unresolved_limits"]),
        "volatility_requirement": copy.deepcopy(expected_requirement),
        "policy_basis_ref": _text(value.get("policy_basis_ref"), "POLICY_BASIS_REF_INVALID"),
        "policy_basis_sha256": _sha(value.get("policy_basis_sha256"), "POLICY_BASIS_SHA_INVALID"),
        "authority": copy.deepcopy(contract["policy_authority"]),
    }
    expected_ref = f"notion-page:{contract['canonical_wbs']['page_id']}"
    if (
        normalized["policy_basis_ref"] != expected_ref
        or normalized["policy_basis_sha256"] != contract["canonical_wbs"]["row_sha256"]
    ):
        raise CryptoExposureLimitError("POLICY_BASIS_NOT_CANONICAL_WBS_ROW")
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise CryptoExposureLimitError("POLICY_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _position(row: dict) -> dict:
    fields = {
        "position_id", "asset_id", "portfolio_weight", "planned_loss_nav_fraction",
        "position_record_sha256", "asset_identity_sha256",
        "crypto_universe_membership_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise CryptoExposureLimitError("POSITION_FIELDS_MISMATCH")
    asset_id = _id(row.get("asset_id"), "ASSET_ID_INVALID")
    weight = _number(row.get("portfolio_weight"), f"POSITION_WEIGHT_INVALID:{asset_id}", positive=True)
    planned_loss = _number(
        row.get("planned_loss_nav_fraction"), f"PLANNED_LOSS_INVALID:{asset_id}"
    )
    if planned_loss > weight:
        raise CryptoExposureLimitError(f"PLANNED_LOSS_EXCEEDS_POSITION:{asset_id}")
    return {
        "position_id": _id(row.get("position_id"), "POSITION_ID_INVALID"),
        "asset_id": asset_id,
        "portfolio_weight": weight,
        "planned_loss_nav_fraction": planned_loss,
        "position_record_sha256": _sha(
            row.get("position_record_sha256"), f"POSITION_RECORD_SHA_INVALID:{asset_id}"
        ),
        "asset_identity_sha256": _sha(
            row.get("asset_identity_sha256"), f"ASSET_IDENTITY_SHA_INVALID:{asset_id}"
        ),
        "crypto_universe_membership_sha256": _sha(
            row.get("crypto_universe_membership_sha256"),
            f"CRYPTO_UNIVERSE_MEMBERSHIP_SHA_INVALID:{asset_id}",
        ),
    }


def _volatility(value: dict, as_of: str, generated_at: str, contract: dict) -> dict:
    fields = {
        "status", "as_of_date", "available_at_utc", "annualized_fraction",
        "unit", "transform_version", "estimator", "lookback_returns",
        "annualization_days", "source_snapshot_sha256", "observation_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoExposureLimitError("VOLATILITY_FIELDS_MISMATCH")
    available = _utc(value.get("available_at_utc"), "VOLATILITY_AVAILABLE_AT_INVALID")
    if (
        value.get("status") != "DEFINED"
        or _date(value.get("as_of_date"), "VOLATILITY_AS_OF_INVALID") != as_of
        or available > generated_at
        or value.get("unit") != contract["volatility_unit"]
        or value.get("transform_version") != contract["volatility_transform_version"]
        or value.get("estimator") != contract["volatility_estimator"]
        or type(value.get("lookback_returns")) is not int
        or value.get("lookback_returns") != contract["volatility_lookback_returns"]
        or type(value.get("annualization_days")) is not int
        or value.get("annualization_days") != contract["volatility_annualization_days"]
    ):
        raise CryptoExposureLimitError("VOLATILITY_IDENTITY_INVALID")
    return {
        "status": "DEFINED",
        "as_of_date": as_of,
        "available_at_utc": available,
        "annualized_fraction": _number(
            value.get("annualized_fraction"), "VOLATILITY_VALUE_INVALID"
        ),
        "unit": contract["volatility_unit"],
        "transform_version": contract["volatility_transform_version"],
        "estimator": contract["volatility_estimator"],
        "lookback_returns": contract["volatility_lookback_returns"],
        "annualization_days": contract["volatility_annualization_days"],
        "source_snapshot_sha256": _sha(
            value.get("source_snapshot_sha256"), "VOLATILITY_SOURCE_SNAPSHOT_SHA_INVALID"
        ),
        "observation_sha256": _sha(
            value.get("observation_sha256"), "VOLATILITY_OBSERVATION_SHA_INVALID"
        ),
    }


def _validate_input(value: dict, as_of: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "snapshot_id", "as_of_date",
        "generated_at_utc", "portfolio_snapshot_sha256",
        "crypto_universe_packet_sha256", "market_theme_budget_packet_sha256",
        "market_theme_budget_status", "positions", "volatility", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoExposureLimitError("INPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or not _same_exact(value.get("authority"), contract["input_authority"])
    ):
        raise CryptoExposureLimitError("INPUT_IDENTITY_INVALID")
    if _date(value.get("as_of_date"), "INPUT_AS_OF_INVALID") != as_of:
        raise CryptoExposureLimitError("INPUT_AS_OF_MISMATCH")
    generated_at = _utc(value.get("generated_at_utc"), "GENERATED_AT_INVALID")
    raw_positions = value.get("positions")
    if not isinstance(raw_positions, list):
        raise CryptoExposureLimitError("POSITIONS_INVALID")
    positions = sorted(
        (_position(row) for row in raw_positions),
        key=lambda row: (row["asset_id"], row["position_id"]),
    )
    position_ids = [row["position_id"] for row in positions]
    if len(position_ids) != len(set(position_ids)):
        raise CryptoExposureLimitError("POSITION_ID_DUPLICATE")
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "snapshot_id": _id(value.get("snapshot_id"), "SNAPSHOT_ID_INVALID"),
        "as_of_date": as_of,
        "generated_at_utc": generated_at,
        "portfolio_snapshot_sha256": _sha(
            value.get("portfolio_snapshot_sha256"), "PORTFOLIO_SNAPSHOT_SHA_INVALID"
        ),
        "crypto_universe_packet_sha256": _sha(
            value.get("crypto_universe_packet_sha256"), "CRYPTO_UNIVERSE_PACKET_SHA_INVALID"
        ),
        "market_theme_budget_packet_sha256": _sha(
            value.get("market_theme_budget_packet_sha256"),
            "MARKET_THEME_BUDGET_PACKET_SHA_INVALID",
        ),
        "market_theme_budget_status": value.get("market_theme_budget_status"),
        "positions": positions,
        "volatility": _volatility(value.get("volatility"), as_of, generated_at, contract),
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    if normalized["market_theme_budget_status"] not in contract[
        "accepted_market_theme_budget_statuses"
    ]:
        raise CryptoExposureLimitError("MARKET_THEME_BUDGET_STATUS_INVALID")
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise CryptoExposureLimitError("INPUT_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _assessment(metric: str, subject_id: str, observed, maximum) -> dict:
    if isinstance(maximum, str):
        breached = Decimal(str(observed)) > Decimal(maximum)
    elif type(maximum) is int and type(observed) is int:
        breached = observed > maximum
    else:
        raise CryptoExposureLimitError(f"ASSESSMENT_TYPES_INVALID:{metric}")
    return {
        "metric": metric,
        "subject_id": subject_id,
        "observed": observed,
        "maximum": maximum,
        "result": "BREACH" if breached else "PASS",
    }


def _not_computable_assessment(metric: str, subject_id: str) -> dict:
    return {
        "metric": metric,
        "subject_id": subject_id,
        "observed": None,
        "maximum": None,
        "result": "NOT_COMPUTABLE",
        "reason": "UNRATIFIED_LIMIT",
    }


def _source_packet(validated: dict) -> dict:
    packet = copy.deepcopy(validated["normalized"])
    packet["packet_sha256"] = validated["packet_sha256"]
    return packet


def _assemble(checked: dict, policy: dict, as_of: str, contract: dict) -> dict:
    source = checked["normalized"]
    limits = policy["normalized"]["active_limits"]
    positions = source["positions"]
    exposure_by_asset = {}
    for row in positions:
        exposure_by_asset.setdefault(row["asset_id"], []).append(row["portfolio_weight"])
    assessments = [
        _assessment(
            "TOTAL_CRYPTO_EXPOSURE", "CRYPTO",
            _rounded_sum(row["portfolio_weight"] for row in positions),
            limits["max_total_crypto_exposure_nav_fraction"],
        )
    ]
    assessments.extend(
        _assessment(
            "SINGLE_ASSET_CRYPTO_EXPOSURE", asset_id, _rounded_sum(weights),
            limits["max_single_asset_exposure_nav_fraction"],
        )
        for asset_id, weights in sorted(exposure_by_asset.items())
    )
    assessments.extend(
        _assessment(
            "PER_TRADE_PLANNED_LOSS", row["position_id"],
            row["planned_loss_nav_fraction"],
            limits["max_per_trade_planned_loss_nav_fraction"],
        )
        for row in positions
    )
    assessments.append(
        _assessment(
            "CONCURRENT_POSITIONS", "CRYPTO", len(positions),
            limits["max_concurrent_positions"],
        )
    )
    assessments.extend([
        _not_computable_assessment("TOTAL_PLANNED_LOSS", "CRYPTO"),
        _not_computable_assessment(
            "ANNUALIZED_REALIZED_VOLATILITY", "BTC_REFERENCE"
        ),
    ])
    breaches = [
        {"metric": row["metric"], "subject_id": row["subject_id"]}
        for row in assessments if row["result"] == "BREACH"
    ]
    if source["market_theme_budget_status"] == "LIMIT_BREACH":
        breaches.insert(0, {
            "metric": "UPSTREAM_MARKET_THEME_BUDGET",
            "subject_id": "CRYPTO",
        })
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "LIMIT_BREACH" if breaches else "WITHIN_RATIFIED_LIMITS",
        "as_of_date": as_of,
        "snapshot_id": source["snapshot_id"],
        "policy_id": policy["normalized"]["policy_id"],
        "assessments": assessments,
        "breaches": breaches,
        "summary": {
            "crypto_position_count": len(positions),
            "total_crypto_exposure": assessments[0]["observed"],
            "total_planned_loss": _rounded_sum(
                row["planned_loss_nav_fraction"] for row in positions
            ),
            "upstream_market_theme_budget_status": source["market_theme_budget_status"],
            "breach_count": len(breaches),
        },
        "recommended_action": None,
        "target_crypto_exposure": None,
        "position_sizes": None,
        "order_intents": [],
        "source_packets": {
            "INPUT": _source_packet(checked),
            "POLICY": _source_packet(policy),
        },
        "lineage": {
            "input_packet_sha256": checked["packet_sha256"],
            "policy_packet_sha256": policy["packet_sha256"],
            "portfolio_snapshot_sha256": source["portfolio_snapshot_sha256"],
            "crypto_universe_packet_sha256": source["crypto_universe_packet_sha256"],
            "market_theme_budget_packet_sha256": source["market_theme_budget_packet_sha256"],
            "volatility_source_snapshot_sha256": source["volatility"]["source_snapshot_sha256"],
            "volatility_observation_sha256": source["volatility"]["observation_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "NO_REPOSITORY_DEFAULT_CRYPTO_POLICY",
            "TOTAL_PLANNED_LOSS_LIMIT_UNRATIFIED",
            "REALIZED_VOLATILITY_LIMIT_UNRATIFIED",
            "NATURAL_VIRTUAL_ACCOUNT_VERIFICATION_PENDING",
            "BTC_STRESS_CALIBRATION_UNDEFINED",
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
    checked = _validate_input(input_value, as_of, contract)
    policy = _validate_policy(policy_value, as_of, contract)
    packet = _assemble(checked, policy, as_of, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "as_of_date",
        "snapshot_id", "policy_id", "assessments", "breaches", "summary",
        "recommended_action", "target_crypto_exposure", "position_sizes",
        "order_intents", "source_packets", "lineage", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise CryptoExposureLimitError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
    ):
        raise CryptoExposureLimitError("OUTPUT_IDENTITY_INVALID")
    as_of = _date(packet.get("as_of_date"), "OUTPUT_AS_OF_DATE_INVALID")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {"INPUT", "POLICY"}:
        raise CryptoExposureLimitError("OUTPUT_SOURCE_PACKETS_INVALID")
    checked = _validate_input(sources["INPUT"], as_of, contract)
    policy = _validate_policy(sources["POLICY"], as_of, contract)
    expected = _assemble(checked, policy, as_of, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_PACKET_SHA_INVALID")
    if actual != expected:
        raise CryptoExposureLimitError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise CryptoExposureLimitError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise CryptoExposureLimitError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
    except (CryptoExposureLimitError, OSError, TypeError, ValueError) as exc:
        print(f"Crypto exposure limit failed: {exc}")
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
