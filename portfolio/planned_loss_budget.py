#!/usr/bin/env python3
"""P7-06 planned-stop loss versus ratified Portfolio Constitution budget."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "planned_loss_budget_contract.json"
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{1,95}$")
CURRENCY_RE = re.compile(r"^[A-Z][A-Z0-9]{2,11}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_constitution_module():
    path = ROOT / "portfolio" / "constitution.py"
    spec = importlib.util.spec_from_file_location("atlas_portfolio_constitution", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"CONSTITUTION_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


constitution_module = _load_constitution_module()


class PlannedLossBudgetError(ValueError):
    """Fail-closed P7-06 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlannedLossBudgetError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "planned_loss_budget/1",
        "input_schema_version": "planned_loss_input/1",
        "output_schema_version": "planned_loss_packet/1",
        "canonical_constitution": "config/constitution.json",
        "repository_default_status": "BLOCKED_UNTIL_CONSTITUTION_B2_B7_RATIFIED",
        "constitution_status_required": "ratified",
        "position_mode": "LONG_ONLY_EXPLICIT_PLANNED_STOP",
        "weight_unit": "NAV_FRACTION",
        "price_rule": "PLANNED_STOP_STRICTLY_BELOW_ENTRY",
        "loss_formula": "position_weight_nav_fraction*(entry_price-planned_stop_price)/entry_price",
        "output_decimal_places": 12,
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "input_authority": {
            "planned_stop_measurement_authorized": True,
            "constitution_definition_authorized": False,
            "automatic_exit_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "planned_loss_budget_evaluation_only": True,
            "repository_default_constitution_authorized": False,
            "automatic_exit_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PlannedLossBudgetError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise PlannedLossBudgetError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PlannedLossBudgetError(code)
    return value


def _id(value, code: str) -> str:
    value = _text(value, code)
    if ID_RE.fullmatch(value) is None:
        raise PlannedLossBudgetError(code)
    return value


def _sha(value, code: str, *, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PlannedLossBudgetError(code)
    return value


def _number(value, code: str, *, positive: bool = False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlannedLossBudgetError(code)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise PlannedLossBudgetError(code)
    if maximum is not None and value > maximum:
        raise PlannedLossBudgetError(code)
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise PlannedLossBudgetError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PlannedLossBudgetError(code) from exc
    if parsed.isoformat() != value:
        raise PlannedLossBudgetError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise PlannedLossBudgetError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PlannedLossBudgetError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise PlannedLossBudgetError(code)
    return value


def _rounded(value: float, contract: dict) -> float:
    return round(value, contract["output_decimal_places"])


def _rounded_sum(values, contract: dict) -> float:
    return _rounded(math.fsum(values), contract)


def _validate_constitution(value: dict, as_of: str) -> dict:
    fields = {
        "_comment", "status", "ratified_at", "constitution_version",
        "B1_bucket_definition", "B2_cash_floor_pct", "B3_bucket_max_pct",
        "B4_position_max_pct", "B5_stop_loss_pct", "B6_portfolio_max_loss_pct",
        "B7_evidence_state_max_pct", "amendment_log",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PlannedLossBudgetError("CONSTITUTION_FIELDS_MISMATCH")
    if value.get("status") != "ratified":
        raise PlannedLossBudgetError("CONSTITUTION_NOT_RATIFIED")
    ratified_at = _utc(value.get("ratified_at"), "CONSTITUTION_RATIFIED_AT_INVALID")
    if ratified_at[:10] > as_of:
        raise PlannedLossBudgetError("CONSTITUTION_RATIFIED_AFTER_AS_OF")
    _id(value.get("constitution_version"), "CONSTITUTION_VERSION_INVALID")
    if value.get("B1_bucket_definition") is None:
        raise PlannedLossBudgetError("CONSTITUTION_B1_NOT_RATIFIED")
    for key in (
        "B2_cash_floor_pct", "B3_bucket_max_pct", "B4_position_max_pct",
        "B5_stop_loss_pct", "B6_portfolio_max_loss_pct",
    ):
        _number(value.get(key), f"CONSTITUTION_PERCENT_INVALID:{key}", maximum=100)
    if value["B5_stop_loss_pct"] <= 0 or value["B6_portfolio_max_loss_pct"] <= 0:
        raise PlannedLossBudgetError("CONSTITUTION_LOSS_LIMIT_NON_POSITIVE")
    evidence = value.get("B7_evidence_state_max_pct")
    if not isinstance(evidence, dict) or set(evidence) != set(constitution_module.EVIDENCE_ORDER):
        raise PlannedLossBudgetError("CONSTITUTION_B7_FIELDS_MISMATCH")
    for key, amount in evidence.items():
        _number(amount, f"CONSTITUTION_B7_INVALID:{key}", maximum=100)
    if not isinstance(value.get("amendment_log"), list):
        raise PlannedLossBudgetError("CONSTITUTION_AMENDMENT_LOG_INVALID")
    _text(value.get("_comment"), "CONSTITUTION_COMMENT_INVALID")
    checked = constitution_module.check(copy.deepcopy(value))
    if checked.get("status") != "ratified" or checked.get("buy_allowed") is not True:
        raise PlannedLossBudgetError("CONSTITUTION_CONTRADICTORY")
    return {
        "normalized": copy.deepcopy(value),
        "sha256": payload_sha256(value),
        "check": copy.deepcopy(checked),
    }


def _position(row: dict, contract: dict) -> dict:
    fields = {
        "asset_id", "market", "currency", "position_weight_nav_fraction",
        "entry_price", "planned_stop_price", "planned_loss_nav_fraction",
        "position_record_sha256", "asset_identity_sha256",
        "bucket_membership_packet_sha256", "position_sizing_packet_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise PlannedLossBudgetError("POSITION_FIELDS_MISMATCH")
    asset_id = _id(row.get("asset_id"), "ASSET_ID_INVALID")
    market = row.get("market")
    if market not in contract["allowed_markets"]:
        raise PlannedLossBudgetError(f"MARKET_INVALID:{asset_id}:{market}")
    currency = row.get("currency")
    if not isinstance(currency, str) or CURRENCY_RE.fullmatch(currency) is None:
        raise PlannedLossBudgetError(f"CURRENCY_INVALID:{asset_id}")
    weight = _number(
        row.get("position_weight_nav_fraction"),
        f"POSITION_WEIGHT_INVALID:{asset_id}",
        positive=True,
    )
    entry = _number(row.get("entry_price"), f"ENTRY_PRICE_INVALID:{asset_id}", positive=True)
    stop = _number(
        row.get("planned_stop_price"), f"PLANNED_STOP_PRICE_INVALID:{asset_id}", positive=True
    )
    if stop >= entry:
        raise PlannedLossBudgetError(f"PLANNED_STOP_NOT_BELOW_ENTRY:{asset_id}")
    raw_stop_distance = (entry - stop) / entry
    stop_distance = _rounded(raw_stop_distance, contract)
    computed_loss = _rounded(weight * raw_stop_distance, contract)
    stated_loss = _number(
        row.get("planned_loss_nav_fraction"), f"PLANNED_LOSS_INVALID:{asset_id}", positive=True
    )
    if stated_loss != computed_loss:
        raise PlannedLossBudgetError(f"PLANNED_LOSS_FORMULA_MISMATCH:{asset_id}")
    return {
        "asset_id": asset_id,
        "market": market,
        "currency": currency,
        "position_weight_nav_fraction": weight,
        "entry_price": entry,
        "planned_stop_price": stop,
        "stop_distance_fraction": stop_distance,
        "planned_loss_nav_fraction": computed_loss,
        "position_record_sha256": _sha(
            row.get("position_record_sha256"), f"POSITION_RECORD_SHA_INVALID:{asset_id}"
        ),
        "asset_identity_sha256": _sha(
            row.get("asset_identity_sha256"), f"ASSET_IDENTITY_SHA_INVALID:{asset_id}"
        ),
        "bucket_membership_packet_sha256": _sha(
            row.get("bucket_membership_packet_sha256"),
            f"BUCKET_MEMBERSHIP_PACKET_SHA_INVALID:{asset_id}",
        ),
        "position_sizing_packet_sha256": _sha(
            row.get("position_sizing_packet_sha256"),
            f"POSITION_SIZING_PACKET_SHA_INVALID:{asset_id}",
        ),
    }


def _validate_input(value: dict, as_of: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "snapshot_id", "as_of_date",
        "generated_at_utc", "portfolio_snapshot_sha256",
        "concentration_guard_packet_sha256", "market_theme_budget_packet_sha256",
        "crypto_exposure_limit_packet_sha256", "positions", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PlannedLossBudgetError("INPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise PlannedLossBudgetError("INPUT_IDENTITY_INVALID")
    if _date(value.get("as_of_date"), "INPUT_AS_OF_INVALID") != as_of:
        raise PlannedLossBudgetError("INPUT_AS_OF_MISMATCH")
    raw = value.get("positions")
    if not isinstance(raw, list) or not raw:
        raise PlannedLossBudgetError("POSITIONS_EMPTY")
    positions = sorted((_position(row, contract) for row in raw), key=lambda row: row["asset_id"])
    asset_ids = [row["asset_id"] for row in positions]
    if len(asset_ids) != len(set(asset_ids)):
        raise PlannedLossBudgetError("POSITION_ASSET_DUPLICATE")
    crypto_sha = _sha(
        value.get("crypto_exposure_limit_packet_sha256"),
        "CRYPTO_EXPOSURE_LIMIT_PACKET_SHA_INVALID",
        nullable=True,
    )
    has_crypto = any(row["market"] == "CRYPTO" for row in positions)
    if has_crypto != (crypto_sha is not None):
        raise PlannedLossBudgetError("CRYPTO_EXPOSURE_LINEAGE_PRESENCE_MISMATCH")
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
        "market_theme_budget_packet_sha256": _sha(
            value.get("market_theme_budget_packet_sha256"),
            "MARKET_THEME_BUDGET_PACKET_SHA_INVALID",
        ),
        "crypto_exposure_limit_packet_sha256": crypto_sha,
        "positions": positions,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = value.get("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(normalized):
        raise PlannedLossBudgetError("INPUT_PACKET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest}


def _assessment(metric: str, asset_id: str, observed: float, maximum: float) -> dict:
    return {
        "metric": metric,
        "asset_id": asset_id,
        "observed": observed,
        "maximum": maximum,
        "result": "BREACH" if observed > maximum else "PASS",
    }


def build_packet(
    input_value: dict,
    constitution_value: dict,
    as_of_date: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    as_of = _date(as_of_date, "AS_OF_DATE_INVALID")
    constitution = _validate_constitution(constitution_value, as_of)
    checked = _validate_input(input_value, as_of, contract)
    source = checked["normalized"]
    positions = source["positions"]
    max_position = constitution["normalized"]["B4_position_max_pct"] / 100
    max_stop = constitution["normalized"]["B5_stop_loss_pct"] / 100
    max_total_loss = constitution["normalized"]["B6_portfolio_max_loss_pct"] / 100
    assessments = []
    for row in positions:
        assessments.extend([
            _assessment(
                "POSITION_WEIGHT", row["asset_id"], row["position_weight_nav_fraction"],
                max_position,
            ),
            _assessment(
                "STOP_DISTANCE", row["asset_id"], row["stop_distance_fraction"], max_stop
            ),
            _assessment(
                "POSITION_PLANNED_LOSS", row["asset_id"],
                row["planned_loss_nav_fraction"],
                _rounded(row["position_weight_nav_fraction"] * max_stop, contract),
            ),
        ])
    total_loss = _rounded_sum(
        (row["planned_loss_nav_fraction"] for row in positions), contract
    )
    assessments.append(
        _assessment("PORTFOLIO_TOTAL_PLANNED_LOSS", "PORTFOLIO", total_loss, max_total_loss)
    )
    breaches = [
        {"metric": row["metric"], "asset_id": row["asset_id"]}
        for row in assessments if row["result"] == "BREACH"
    ]
    market_losses = {
        market: _rounded_sum(
            (row["planned_loss_nav_fraction"] for row in positions if row["market"] == market),
            contract,
        )
        for market in sorted({row["market"] for row in positions})
    }
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "LIMIT_BREACH" if breaches else "WITHIN_RATIFIED_LOSS_BUDGET",
        "as_of_date": as_of,
        "snapshot_id": source["snapshot_id"],
        "constitution_version": constitution["normalized"]["constitution_version"],
        "positions": positions,
        "assessments": assessments,
        "breaches": breaches,
        "summary": {
            "position_count": len(positions),
            "total_planned_loss_nav_fraction": total_loss,
            "portfolio_loss_budget_nav_fraction": max_total_loss,
            "planned_loss_by_market": market_losses,
            "breach_count": len(breaches),
        },
        "recommended_exit": None,
        "stop_order_intents": [],
        "position_sizes": None,
        "lineage": {
            "input_packet_sha256": checked["packet_sha256"],
            "constitution_sha256": constitution["sha256"],
            "portfolio_snapshot_sha256": source["portfolio_snapshot_sha256"],
            "concentration_guard_packet_sha256": source["concentration_guard_packet_sha256"],
            "market_theme_budget_packet_sha256": source["market_theme_budget_packet_sha256"],
            "crypto_exposure_limit_packet_sha256": source["crypto_exposure_limit_packet_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REPOSITORY_CONSTITUTION_NOT_RATIFIED",
            "NO_AUTOMATIC_EXIT",
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
        raise PlannedLossBudgetError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(input_path: Path, constitution_path: Path, as_of_date: str, output_path: Path) -> int:
    try:
        packet = build_packet(_read_json(input_path), _read_json(constitution_path), as_of_date)
        write_json_atomic(output_path, packet)
        return 0
    except (PlannedLossBudgetError, OSError, TypeError, ValueError) as exc:
        print(f"Planned-loss budget failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("constitution", type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.constitution, args.as_of_date, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
