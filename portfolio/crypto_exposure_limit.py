#!/usr/bin/env python3
"""P7-05 explicit Crypto exposure, planned-loss, and volatility limit guard."""
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
        "output_schema_version": "crypto_exposure_packet/1",
        "repository_default_status": "BLOCKED_UNTIL_EXTERNAL_POLICY_RATIFIED",
        "approval_mode": "EXPLICIT_CIO_RATIFIED_ONLY",
        "market": "CRYPTO",
        "budget_unit": "NAV_FRACTION",
        "volatility_unit": "ANNUALIZED_FRACTION",
        "volatility_transform_version": "btc_risk/v1",
        "volatility_estimator": "sqrt_mean_squared_simple_returns",
        "volatility_lookback_returns": 30,
        "volatility_annualization_days": 365,
        "position_mode": "LONG_ONLY_EXPLICIT_HOLDINGS",
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
        if value.get(key) != expected_value:
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
        "ratified_at", "valid_from", "valid_to", "limits", "volatility_requirement",
        "policy_basis_ref", "policy_basis_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CryptoExposureLimitError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["policy_authority"]
    ):
        raise CryptoExposureLimitError("POLICY_IDENTITY_INVALID")
    policy_id = _id(value.get("policy_id"), "POLICY_ID_INVALID")
    ratified_at = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    start, end = _interval(value.get("valid_from"), value.get("valid_to"), policy_id)
    if ratified_at[:10] > start:
        raise CryptoExposureLimitError("POLICY_RATIFIED_AFTER_EFFECTIVE_START")
    if not _active(start, end, as_of):
        raise CryptoExposureLimitError("POLICY_NOT_EFFECTIVE")
    limits = value.get("limits")
    limit_fields = {
        "max_total_crypto_exposure", "max_single_crypto_exposure",
        "max_total_planned_loss", "max_single_planned_loss",
        "max_annualized_realized_volatility",
    }
    if not isinstance(limits, dict) or set(limits) != limit_fields:
        raise CryptoExposureLimitError("POLICY_LIMIT_FIELDS_MISMATCH")
    limits = {
        key: _number(limits.get(key), f"POLICY_LIMIT_INVALID:{key}", positive=True)
        for key in sorted(limit_fields)
    }
    requirement = value.get("volatility_requirement")
    expected_requirement = {
        "unit": contract["volatility_unit"],
        "transform_version": contract["volatility_transform_version"],
        "estimator": contract["volatility_estimator"],
        "lookback_returns": contract["volatility_lookback_returns"],
        "annualization_days": contract["volatility_annualization_days"],
    }
    if requirement != expected_requirement:
        raise CryptoExposureLimitError("POLICY_VOLATILITY_REQUIREMENT_MISMATCH")
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
        "volatility_requirement": copy.deepcopy(expected_requirement),
        "policy_basis_ref": _text(value.get("policy_basis_ref"), "POLICY_BASIS_REF_INVALID"),
        "policy_basis_sha256": _sha(value.get("policy_basis_sha256"), "POLICY_BASIS_SHA_INVALID"),
        "authority": copy.deepcopy(contract["policy_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise CryptoExposureLimitError("POLICY_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _position(row: dict) -> dict:
    fields = {
        "asset_id", "portfolio_weight", "planned_loss_nav_fraction",
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
        or value.get("lookback_returns") != contract["volatility_lookback_returns"]
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
        or value.get("authority") != contract["input_authority"]
    ):
        raise CryptoExposureLimitError("INPUT_IDENTITY_INVALID")
    if _date(value.get("as_of_date"), "INPUT_AS_OF_INVALID") != as_of:
        raise CryptoExposureLimitError("INPUT_AS_OF_MISMATCH")
    generated_at = _utc(value.get("generated_at_utc"), "GENERATED_AT_INVALID")
    raw_positions = value.get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        raise CryptoExposureLimitError("POSITIONS_EMPTY")
    positions = sorted((_position(row) for row in raw_positions), key=lambda row: row["asset_id"])
    asset_ids = [row["asset_id"] for row in positions]
    if len(asset_ids) != len(set(asset_ids)):
        raise CryptoExposureLimitError("POSITION_ASSET_DUPLICATE")
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


def _assessment(metric: str, subject_id: str, observed: float, maximum: float) -> dict:
    return {
        "metric": metric,
        "subject_id": subject_id,
        "observed": observed,
        "maximum": maximum,
        "result": "BREACH" if observed > maximum else "PASS",
    }


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
    source = checked["normalized"]
    limits = policy["normalized"]["limits"]
    positions = source["positions"]
    assessments = [
        _assessment(
            "TOTAL_CRYPTO_EXPOSURE", "CRYPTO",
            _rounded_sum(row["portfolio_weight"] for row in positions),
            limits["max_total_crypto_exposure"],
        )
    ]
    assessments.extend(
        _assessment(
            "SINGLE_CRYPTO_EXPOSURE", row["asset_id"], row["portfolio_weight"],
            limits["max_single_crypto_exposure"],
        )
        for row in positions
    )
    assessments.append(
        _assessment(
            "TOTAL_PLANNED_LOSS", "CRYPTO",
            _rounded_sum(row["planned_loss_nav_fraction"] for row in positions),
            limits["max_total_planned_loss"],
        )
    )
    assessments.extend(
        _assessment(
            "SINGLE_PLANNED_LOSS", row["asset_id"], row["planned_loss_nav_fraction"],
            limits["max_single_planned_loss"],
        )
        for row in positions
    )
    assessments.append(
        _assessment(
            "ANNUALIZED_REALIZED_VOLATILITY", "BTC_REFERENCE",
            source["volatility"]["annualized_fraction"],
            limits["max_annualized_realized_volatility"],
        )
    )
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
            "total_planned_loss": next(
                row["observed"] for row in assessments if row["metric"] == "TOTAL_PLANNED_LOSS"
            ),
            "upstream_market_theme_budget_status": source["market_theme_budget_status"],
            "breach_count": len(breaches),
        },
        "recommended_action": None,
        "target_crypto_exposure": None,
        "position_sizes": None,
        "order_intents": [],
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
            "NO_REPOSITORY_DEFAULT_CRYPTO_LIMIT",
            "BTC_STRESS_CALIBRATION_UNDEFINED",
            "NO_AUTOMATIC_POSITION_REDUCTION",
            "POSITION_SIZING_NOT_AUTHORIZED",
            "ORDER_NOT_AUTHORIZED",
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
