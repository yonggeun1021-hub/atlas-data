#!/usr/bin/env python3
"""P7-04 explicit Regime-keyed market/theme exposure budget evaluator."""
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
CONTRACT_PATH = ROOT / "config" / "market_theme_exposure_budget_contract.json"
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{1,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class MarketThemeExposureBudgetError(ValueError):
    """Fail-closed P7-04 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketThemeExposureBudgetError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "market_theme_exposure_budget/1",
        "policy_schema_version": "market_theme_budget_policy/1",
        "input_schema_version": "market_theme_exposure_input/1",
        "output_schema_version": "market_theme_exposure_packet/1",
        "repository_default_status": "BLOCKED_UNTIL_EXTERNAL_POLICY_RATIFIED",
        "approval_mode": "EXPLICIT_CIO_RATIFIED_ONLY",
        "budget_unit": "NAV_FRACTION",
        "source_regime_contract_version": "regime_output/v1",
        "source_regime_contract_mode": "PRE_SCORE_UNKNOWN_ONLY",
        "runtime_authorized_regimes": ["UNKNOWN"],
        "runtime_authorized_directions": ["UNKNOWN"],
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "allowed_scope_types": ["MARKET", "THEME"],
        "effective_interval": "[valid_from, valid_to)",
        "input_authority": {
            "exposure_measurement_authorized": True,
            "budget_definition_authorized": False,
            "automatic_rebalance_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "policy_authority": {
            "market_theme_budget_definition_authorized": True,
            "automatic_rebalance_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "market_theme_budget_evaluation_only": True,
            "repository_default_policy_authorized": False,
            "regime_scoring_authorized": False,
            "rotation_inference_authorized": False,
            "automatic_rebalance_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise MarketThemeExposureBudgetError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise MarketThemeExposureBudgetError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MarketThemeExposureBudgetError(code)
    return value


def _id(value, code: str) -> str:
    value = _text(value, code)
    if ID_RE.fullmatch(value) is None:
        raise MarketThemeExposureBudgetError(code)
    return value


def _sha(value, code: str, *, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise MarketThemeExposureBudgetError(code)
    return value


def _number(value, code: str, *, positive: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarketThemeExposureBudgetError(code)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise MarketThemeExposureBudgetError(code)
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise MarketThemeExposureBudgetError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise MarketThemeExposureBudgetError(code) from exc
    if parsed.isoformat() != value:
        raise MarketThemeExposureBudgetError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise MarketThemeExposureBudgetError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise MarketThemeExposureBudgetError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise MarketThemeExposureBudgetError(code)
    return value


def _interval(start, end, context: str) -> tuple[str, str | None]:
    start = _date(start, f"VALID_FROM_INVALID:{context}")
    if end is not None:
        end = _date(end, f"VALID_TO_INVALID:{context}")
        if end <= start:
            raise MarketThemeExposureBudgetError(f"EFFECTIVE_INTERVAL_EMPTY:{context}")
    return start, end


def _active(start: str, end: str | None, as_of: str) -> bool:
    return start <= as_of and (end is None or as_of < end)


def _overlap(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def _policy_record(row: dict, contract: dict) -> dict:
    fields = {
        "budget_id", "scope_type", "market", "scope_id", "regime",
        "max_exposure", "unit", "valid_from", "valid_to", "budget_basis_ref",
        "budget_basis_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise MarketThemeExposureBudgetError("BUDGET_RECORD_FIELDS_MISMATCH")
    budget_id = _id(row.get("budget_id"), "BUDGET_ID_INVALID")
    scope_type = row.get("scope_type")
    market = row.get("market")
    scope_id = _id(row.get("scope_id"), f"SCOPE_ID_INVALID:{budget_id}")
    regime = row.get("regime")
    if scope_type not in contract["allowed_scope_types"]:
        raise MarketThemeExposureBudgetError(f"SCOPE_TYPE_INVALID:{budget_id}")
    if market not in contract["allowed_markets"]:
        raise MarketThemeExposureBudgetError(f"MARKET_INVALID:{budget_id}")
    if regime not in contract["runtime_authorized_regimes"]:
        raise MarketThemeExposureBudgetError(f"REGIME_NOT_RUNTIME_AUTHORIZED:{budget_id}:{regime}")
    if scope_type == "MARKET" and scope_id != market:
        raise MarketThemeExposureBudgetError(f"MARKET_SCOPE_ID_MISMATCH:{budget_id}")
    start, end = _interval(row.get("valid_from"), row.get("valid_to"), budget_id)
    if row.get("unit") != contract["budget_unit"]:
        raise MarketThemeExposureBudgetError(f"BUDGET_UNIT_INVALID:{budget_id}")
    return {
        "budget_id": budget_id,
        "scope_type": scope_type,
        "market": market,
        "scope_id": scope_id,
        "regime": regime,
        "max_exposure": _number(
            row.get("max_exposure"), f"MAX_EXPOSURE_INVALID:{budget_id}", positive=True
        ),
        "unit": contract["budget_unit"],
        "valid_from": start,
        "valid_to": end,
        "budget_basis_ref": _text(
            row.get("budget_basis_ref"), f"BUDGET_BASIS_REF_INVALID:{budget_id}"
        ),
        "budget_basis_sha256": _sha(
            row.get("budget_basis_sha256"), f"BUDGET_BASIS_SHA_INVALID:{budget_id}"
        ),
    }


def _validate_policy(value: dict, as_of: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_set_id", "status",
        "ratified_by", "ratified_at", "valid_from", "valid_to", "records",
        "policy_basis_ref", "policy_basis_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise MarketThemeExposureBudgetError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["policy_authority"]
    ):
        raise MarketThemeExposureBudgetError("POLICY_IDENTITY_INVALID")
    policy_id = _id(value.get("policy_set_id"), "POLICY_SET_ID_INVALID")
    ratified_at = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    start, end = _interval(value.get("valid_from"), value.get("valid_to"), policy_id)
    if ratified_at[:10] > start:
        raise MarketThemeExposureBudgetError("POLICY_RATIFIED_AFTER_EFFECTIVE_START")
    if not _active(start, end, as_of):
        raise MarketThemeExposureBudgetError("POLICY_NOT_EFFECTIVE")
    raw = value.get("records")
    if not isinstance(raw, list) or not raw:
        raise MarketThemeExposureBudgetError("BUDGET_RECORDS_EMPTY")
    records = sorted(
        (_policy_record(row, contract) for row in raw),
        key=lambda row: (
            row["scope_type"], row["market"], row["scope_id"], row["regime"],
            row["valid_from"], row["budget_id"],
        ),
    )
    grouped: dict[tuple, list[dict]] = {}
    by_budget_id: dict[str, list[dict]] = {}
    active_records = []
    for row in records:
        if row["valid_from"] < start or (
            end is not None and (row["valid_to"] is None or row["valid_to"] > end)
        ):
            raise MarketThemeExposureBudgetError(f"BUDGET_OUTSIDE_POLICY_INTERVAL:{row['budget_id']}")
        key = (row["scope_type"], row["market"], row["scope_id"], row["regime"])
        grouped.setdefault(key, []).append(row)
        by_budget_id.setdefault(row["budget_id"], []).append(row)
        if _active(row["valid_from"], row["valid_to"], as_of):
            active_records.append(copy.deepcopy(row))
    for budget_id, rows in by_budget_id.items():
        identities = {
            (row["scope_type"], row["market"], row["scope_id"], row["regime"], row["unit"])
            for row in rows
        }
        if len(identities) != 1:
            raise MarketThemeExposureBudgetError(f"BUDGET_IDENTITY_DRIFT:{budget_id}")
    for key, rows in grouped.items():
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                if _overlap(left["valid_from"], left["valid_to"], right["valid_from"], right["valid_to"]):
                    raise MarketThemeExposureBudgetError(f"BUDGET_INTERVAL_OVERLAP:{key}")
    normalized = {
        "schema_version": contract["policy_schema_version"],
        "contract_version": contract["contract_version"],
        "policy_set_id": policy_id,
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": ratified_at,
        "valid_from": start,
        "valid_to": end,
        "records": records,
        "policy_basis_ref": _text(value.get("policy_basis_ref"), "POLICY_BASIS_REF_INVALID"),
        "policy_basis_sha256": _sha(value.get("policy_basis_sha256"), "POLICY_BASIS_SHA_INVALID"),
        "authority": copy.deepcopy(contract["policy_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise MarketThemeExposureBudgetError("POLICY_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest, "active": active_records}


def _regime(row: dict, contract: dict) -> dict:
    fields = {
        "market", "regime", "direction", "confidence", "contract_version",
        "contract_mode", "regime_packet_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise MarketThemeExposureBudgetError("REGIME_FIELDS_MISMATCH")
    market = row.get("market")
    if market not in contract["allowed_markets"]:
        raise MarketThemeExposureBudgetError(f"REGIME_MARKET_INVALID:{market}")
    if (
        row.get("regime") not in contract["runtime_authorized_regimes"]
        or row.get("direction") not in contract["runtime_authorized_directions"]
        or row.get("confidence") is not None
        or row.get("contract_version") != contract["source_regime_contract_version"]
        or row.get("contract_mode") != contract["source_regime_contract_mode"]
    ):
        raise MarketThemeExposureBudgetError(f"REGIME_RUNTIME_IDENTITY_INVALID:{market}")
    return {
        "market": market,
        "regime": row["regime"],
        "direction": row["direction"],
        "confidence": None,
        "contract_version": contract["source_regime_contract_version"],
        "contract_mode": contract["source_regime_contract_mode"],
        "regime_packet_sha256": _sha(
            row.get("regime_packet_sha256"), f"REGIME_PACKET_SHA_INVALID:{market}"
        ),
    }


def _exposure(row: dict, contract: dict) -> dict:
    fields = {
        "scope_type", "market", "scope_id", "exposure",
        "exposure_source_sha256", "rotation_packet_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise MarketThemeExposureBudgetError("EXPOSURE_FIELDS_MISMATCH")
    scope_type = row.get("scope_type")
    market = row.get("market")
    scope_id = _id(row.get("scope_id"), "EXPOSURE_SCOPE_ID_INVALID")
    if scope_type not in contract["allowed_scope_types"]:
        raise MarketThemeExposureBudgetError(f"EXPOSURE_SCOPE_TYPE_INVALID:{scope_id}")
    if market not in contract["allowed_markets"]:
        raise MarketThemeExposureBudgetError(f"EXPOSURE_MARKET_INVALID:{scope_id}")
    rotation_sha = _sha(
        row.get("rotation_packet_sha256"),
        f"ROTATION_PACKET_SHA_INVALID:{scope_id}",
        nullable=True,
    )
    if scope_type == "MARKET":
        if scope_id != market or rotation_sha is not None:
            raise MarketThemeExposureBudgetError(f"MARKET_EXPOSURE_IDENTITY_INVALID:{scope_id}")
    elif rotation_sha is None:
        raise MarketThemeExposureBudgetError(f"THEME_ROTATION_LINEAGE_REQUIRED:{scope_id}")
    return {
        "scope_type": scope_type,
        "market": market,
        "scope_id": scope_id,
        "exposure": _number(row.get("exposure"), f"EXPOSURE_INVALID:{scope_id}"),
        "exposure_source_sha256": _sha(
            row.get("exposure_source_sha256"), f"EXPOSURE_SOURCE_SHA_INVALID:{scope_id}"
        ),
        "rotation_packet_sha256": rotation_sha,
    }


def _validate_input(value: dict, as_of: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "snapshot_id", "as_of_date",
        "generated_at_utc", "portfolio_snapshot_sha256",
        "concentration_guard_packet_sha256", "theme_taxonomy_packet_sha256",
        "regimes", "exposures", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise MarketThemeExposureBudgetError("INPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise MarketThemeExposureBudgetError("INPUT_IDENTITY_INVALID")
    if _date(value.get("as_of_date"), "INPUT_AS_OF_INVALID") != as_of:
        raise MarketThemeExposureBudgetError("INPUT_AS_OF_MISMATCH")
    raw_regimes = value.get("regimes")
    if not isinstance(raw_regimes, list):
        raise MarketThemeExposureBudgetError("REGIMES_INVALID")
    regimes = sorted((_regime(row, contract) for row in raw_regimes), key=lambda row: row["market"])
    if [row["market"] for row in regimes] != contract["allowed_markets"]:
        raise MarketThemeExposureBudgetError("REGIME_MARKET_COVERAGE_INVALID")
    raw_exposures = value.get("exposures")
    if not isinstance(raw_exposures, list) or not raw_exposures:
        raise MarketThemeExposureBudgetError("EXPOSURES_EMPTY")
    exposures = sorted(
        (_exposure(row, contract) for row in raw_exposures),
        key=lambda row: (row["scope_type"], row["market"], row["scope_id"]),
    )
    keys = [(row["scope_type"], row["market"], row["scope_id"]) for row in exposures]
    if len(keys) != len(set(keys)):
        raise MarketThemeExposureBudgetError("EXPOSURE_SCOPE_DUPLICATE")
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "snapshot_id": _id(value.get("snapshot_id"), "SNAPSHOT_ID_INVALID"),
        "as_of_date": as_of,
        "generated_at_utc": _utc(value.get("generated_at_utc"), "GENERATED_AT_INVALID"),
        "portfolio_snapshot_sha256": _sha(
            value.get("portfolio_snapshot_sha256"), "PORTFOLIO_SNAPSHOT_SHA_INVALID"
        ),
        "concentration_guard_packet_sha256": _sha(
            value.get("concentration_guard_packet_sha256"),
            "CONCENTRATION_GUARD_PACKET_SHA_INVALID",
        ),
        "theme_taxonomy_packet_sha256": _sha(
            value.get("theme_taxonomy_packet_sha256"), "THEME_TAXONOMY_PACKET_SHA_INVALID"
        ),
        "regimes": regimes,
        "exposures": exposures,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise MarketThemeExposureBudgetError("INPUT_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


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
    regime_by_market = {row["market"]: row for row in source["regimes"]}
    active_by_key = {}
    for row in policy["active"]:
        key = (row["scope_type"], row["market"], row["scope_id"], row["regime"])
        if key in active_by_key:
            raise MarketThemeExposureBudgetError(f"ACTIVE_BUDGET_DUPLICATE:{key}")
        active_by_key[key] = row
    assessments = []
    used_budget_ids = set()
    for exposure in source["exposures"]:
        regime = regime_by_market[exposure["market"]]["regime"]
        key = (exposure["scope_type"], exposure["market"], exposure["scope_id"], regime)
        budget = active_by_key.get(key)
        if budget is None:
            raise MarketThemeExposureBudgetError(f"ACTIVE_BUDGET_COVERAGE_MISSING:{key}")
        used_budget_ids.add(budget["budget_id"])
        assessments.append({
            "scope_type": exposure["scope_type"],
            "market": exposure["market"],
            "scope_id": exposure["scope_id"],
            "regime": regime,
            "exposure": exposure["exposure"],
            "max_exposure": budget["max_exposure"],
            "unit": contract["budget_unit"],
            "result": "BREACH" if exposure["exposure"] > budget["max_exposure"] else "PASS",
            "budget_id": budget["budget_id"],
            "exposure_source_sha256": exposure["exposure_source_sha256"],
            "rotation_packet_sha256": exposure["rotation_packet_sha256"],
        })
    unused = sorted(
        row["budget_id"] for row in policy["active"] if row["budget_id"] not in used_budget_ids
    )
    breaches = [
        {"scope_type": row["scope_type"], "market": row["market"], "scope_id": row["scope_id"]}
        for row in assessments if row["result"] == "BREACH"
    ]
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "LIMIT_BREACH" if breaches else "WITHIN_RATIFIED_BUDGET",
        "as_of_date": as_of,
        "snapshot_id": source["snapshot_id"],
        "policy_set_id": policy["normalized"]["policy_set_id"],
        "assessments": assessments,
        "breaches": breaches,
        "summary": {
            "assessment_count": len(assessments),
            "market_assessment_count": sum(row["scope_type"] == "MARKET" for row in assessments),
            "theme_assessment_count": sum(row["scope_type"] == "THEME" for row in assessments),
            "breach_count": len(breaches),
            "unused_active_budget_ids": unused,
        },
        "recommended_rebalance": None,
        "target_exposures": None,
        "position_sizes": None,
        "order_intents": [],
        "lineage": {
            "input_packet_sha256": checked["packet_sha256"],
            "policy_packet_sha256": policy["packet_sha256"],
            "portfolio_snapshot_sha256": source["portfolio_snapshot_sha256"],
            "concentration_guard_packet_sha256": source["concentration_guard_packet_sha256"],
            "theme_taxonomy_packet_sha256": source["theme_taxonomy_packet_sha256"],
            "regime_packet_sha256_by_market": {
                row["market"]: row["regime_packet_sha256"] for row in source["regimes"]
            },
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "NO_REPOSITORY_DEFAULT_EXPOSURE_BUDGET",
            "CURRENT_REGIME_RUNTIME_UNKNOWN_ONLY",
            "NO_AUTOMATIC_REBALANCE",
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
        raise MarketThemeExposureBudgetError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
    except (MarketThemeExposureBudgetError, OSError, TypeError, ValueError) as exc:
        print(f"Market/theme exposure budget failed: {exc}")
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
