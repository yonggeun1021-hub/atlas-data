#!/usr/bin/env python3
"""P7-12 fail-closed Strategic Capital Posture readiness boundary.

This capability inventories and revalidates the upstream P6/P7 risk packets
needed before a cross-market capital posture can exist.  P1 Regime Decision,
P2 cross-market Flow/Rotation, and an allocation policy remain unratified, so
missing inputs stay BLOCKED.  They must never become zero budgets, NO_ACTION,
an allocation proposal, an order, Production, or trading authority.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "strategic_capital_posture_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,159}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class StrategicCapitalPostureError(ValueError):
    """Fail-closed P7-12 contract or source violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategicCapitalPostureError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _load_validator(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_VALIDATOR_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEFENSIVE_ACTION = _load_validator(
    "atlas_defensive_action_for_p712", "portfolio/defensive_action_decision.py"
)
CONCENTRATION = _load_validator(
    "atlas_concentration_for_p712", "portfolio/concentration_correlation_guard.py"
)
MARKET_THEME = _load_validator(
    "atlas_market_theme_for_p712", "portfolio/market_theme_exposure_budget.py"
)
CRYPTO_LIMIT = _load_validator(
    "atlas_crypto_limit_for_p712", "portfolio/crypto_exposure_limit.py"
)
PLANNED_LOSS = _load_validator(
    "atlas_planned_loss_for_p712", "portfolio/planned_loss_budget.py"
)
CURRENCY = _load_validator(
    "atlas_currency_for_p712", "portfolio/currency_exposure.py"
)


SOURCE_VALIDATORS = {
    "P6_DEFENSIVE_ACTION": DEFENSIVE_ACTION,
    "P7_CONCENTRATION_GUARD": CONCENTRATION,
    "P7_MARKET_THEME_BUDGET": MARKET_THEME,
    "P7_CRYPTO_EXPOSURE_LIMIT": CRYPTO_LIMIT,
    "P7_PLANNED_LOSS_BUDGET": PLANNED_LOSS,
    "P7_CURRENCY_EXPOSURE": CURRENCY,
}


def _expected_contract() -> dict:
    source_order = [
        "P1_REGIME_DECISION",
        "P2_CROSS_MARKET_FLOW",
        "P2_ROTATION_STATE",
        "P6_DEFENSIVE_ACTION",
        "P7_CONCENTRATION_GUARD",
        "P7_MARKET_THEME_BUDGET",
        "P7_CRYPTO_EXPOSURE_LIMIT",
        "P7_PLANNED_LOSS_BUDGET",
        "P7_CURRENCY_EXPOSURE",
    ]
    return {
        "schema_version": 1,
        "contract_version": "strategic_capital_posture_readiness/1",
        "output_schema_version": "strategic_capital_posture_readiness_packet/1",
        "scope": "ZERO_CAPITAL_CROSS_MARKET_BUDGET_READINESS",
        "markets": ["CRYPTO", "KOREA", "US"],
        "runtime_status": "BLOCKED",
        "runtime_evaluation_status": "NOT_EVALUATED",
        "budget_unit": "NAV_FRACTION",
        "source_order": source_order,
        "unavailable_only_source_slots": [
            "P1_REGIME_DECISION",
            "P2_CROSS_MARKET_FLOW",
            "P2_ROTATION_STATE",
        ],
        "source_specs": {
            "P6_DEFENSIVE_ACTION": {
                "schema_version": "defensive_action_decision_readiness_packet/1",
                "contract_version": "defensive_action_decision_readiness/1",
                "statuses": ["DEFENSIVE_ACTION_READINESS_BLOCKED"],
                "effective_available_at_path": ["generated_at"],
            },
            "P7_CONCENTRATION_GUARD": {
                "schema_version": "concentration_correlation_packet/2",
                "contract_version": "concentration_correlation_guard/2",
                "statuses": ["LIMIT_BREACH", "WITHIN_RATIFIED_LIMITS"],
                "effective_available_at_path": [
                    "source_packets", "INPUT", "generated_at_utc"
                ],
            },
            "P7_MARKET_THEME_BUDGET": {
                "schema_version": "market_theme_exposure_packet/2",
                "contract_version": "market_theme_exposure_budget/1",
                "statuses": ["LIMIT_BREACH", "WITHIN_RATIFIED_BUDGET"],
                "effective_available_at_path": [
                    "source_packets", "INPUT", "generated_at_utc"
                ],
            },
            "P7_CRYPTO_EXPOSURE_LIMIT": {
                "schema_version": "crypto_exposure_packet/2",
                "contract_version": "crypto_exposure_limit/1",
                "statuses": ["LIMIT_BREACH", "WITHIN_RATIFIED_LIMITS"],
                "effective_available_at_path": [
                    "source_packets", "INPUT", "generated_at_utc"
                ],
            },
            "P7_PLANNED_LOSS_BUDGET": {
                "schema_version": "planned_loss_packet/2",
                "contract_version": "planned_loss_budget/2",
                "statuses": ["LIMIT_BREACH", "WITHIN_RATIFIED_LOSS_BUDGET"],
                "effective_available_at_path": [
                    "source_packets", "INPUT", "generated_at_utc"
                ],
            },
            "P7_CURRENCY_EXPOSURE": {
                "schema_version": "currency_exposure_packet/1",
                "contract_version": "currency_exposure/1",
                "statuses": ["RAW_QUOTE_CURRENCY_EXPOSURE_ONLY"],
                "effective_available_at_path": ["available_at"],
            },
        },
        "constraint_checks": [
            "ALLOCATION_SUM",
            "CURRENCY_BOUNDARY",
            "OVERLAP_EXPOSURE",
        ],
        "invariants": [
            "BLOCKED_OR_MISSING_INPUT_NEVER_IMPLIES_ZERO_BUDGET",
            "CURRENCY_AMOUNTS_NEVER_SUM_WITHOUT_RATIFIED_CONVERSION",
            "DEFENSIVE_ACTION_NEVER_IMPLIES_CAPITAL_ALLOCATION",
            "MISSING_INPUT_NEVER_IMPLIES_NO_ACTION",
            "OVERLAPPING_EXPOSURE_NEVER_COUNTS_AS_INDEPENDENT_HEADROOM",
            "READINESS_NEVER_IMPLIES_ORDER_OR_TRADING_AUTHORITY",
        ],
        "authority": {
            "readiness_inventory_only": True,
            "strategic_posture_authorized": False,
            "capital_budget_definition_authorized": False,
            "cross_market_allocation_authorized": False,
            "cash_reserve_authorized": False,
            "hedge_budget_authorized": False,
            "gross_net_risk_authorized": False,
            "theme_headroom_authorized": False,
            "currency_conversion_authorized": False,
            "action_proposal_authorized": False,
            "position_size_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or value != expected:
        raise StrategicCapitalPostureError("CONTRACT_MISMATCH")
    if set(value["source_specs"]) != set(SOURCE_VALIDATORS):
        raise StrategicCapitalPostureError("CONTRACT_SOURCE_SPECS_MISMATCH")
    if set(value["source_order"]) != (
        set(value["unavailable_only_source_slots"]) | set(value["source_specs"])
    ):
        raise StrategicCapitalPostureError("CONTRACT_SOURCE_ORDER_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise StrategicCapitalPostureError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise StrategicCapitalPostureError(code) from exc
    if parsed.isoformat() != value:
        raise StrategicCapitalPostureError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise StrategicCapitalPostureError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise StrategicCapitalPostureError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise StrategicCapitalPostureError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise StrategicCapitalPostureError(code)
    return value


def _reasons(value, code: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or value != sorted(set(value))
        or any(not isinstance(item, str) or REASON_RE.fullmatch(item) is None for item in value)
    ):
        raise StrategicCapitalPostureError(code)
    return list(value)


def _assert_execution_authority_closed(name: str, authority) -> None:
    if not isinstance(authority, dict):
        raise StrategicCapitalPostureError(f"SOURCE_AUTHORITY_INVALID:{name}")
    for key, value in authority.items():
        execution_key = (
            key == "order_authorized"
            or key.endswith("_order_authorized")
            or key in {"production_authorized", "trading_authorized"}
        )
        if execution_key and value is not False:
            raise StrategicCapitalPostureError(f"SOURCE_AUTHORITY_EXPANDED:{name}:{key}")


def _source_effective_available_at(name: str, packet: dict, spec: dict) -> str:
    value = packet
    path = spec.get("effective_available_at_path")
    if (
        not isinstance(path, list)
        or not path
        or any(not isinstance(key, str) or not key for key in path)
    ):
        raise StrategicCapitalPostureError(
            f"SOURCE_EFFECTIVE_AVAILABLE_AT_PATH_INVALID:{name}"
        )
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise StrategicCapitalPostureError(
                f"SOURCE_EFFECTIVE_AVAILABLE_AT_MISSING:{name}"
            )
        value = value[key]
    return _utc(value, f"SOURCE_EFFECTIVE_AVAILABLE_AT_INVALID:{name}")


def _validate_source(
    name: str,
    packet: dict,
    as_of_date: str,
    generated_at: str,
    contract: dict,
) -> dict:
    spec = contract["source_specs"][name]
    if not isinstance(packet, dict) or (
        packet.get("schema_version") != spec["schema_version"]
        or packet.get("contract_version") != spec["contract_version"]
        or packet.get("status") not in spec["statuses"]
    ):
        raise StrategicCapitalPostureError(f"SOURCE_IDENTITY_INVALID:{name}")
    try:
        checked = SOURCE_VALIDATORS[name].validate_packet(copy.deepcopy(packet))
    except ValueError as exc:
        raise StrategicCapitalPostureError(f"SOURCE_SEMANTIC_INVALID:{name}:{exc}") from exc
    _assert_execution_authority_closed(name, checked.get("authority"))
    digest = _sha(checked.get("packet_sha256"), f"SOURCE_PACKET_SHA_INVALID:{name}")
    effective_available_at = _source_effective_available_at(name, checked, spec)
    if effective_available_at > generated_at:
        raise StrategicCapitalPostureError(f"SOURCE_FROM_FUTURE:{name}")
    if "as_of_date" in checked:
        evidence_date = _date(checked.get("as_of_date"), f"SOURCE_DATE_INVALID:{name}")
    else:
        evidence_date = effective_available_at[:10]
    if evidence_date is not None and evidence_date > as_of_date:
        raise StrategicCapitalPostureError(f"SOURCE_AFTER_AS_OF_DATE:{name}")
    return {
        "name": name,
        "availability": "AVAILABLE",
        "source_status": checked["status"],
        "evidence_date": evidence_date,
        "source_packet_sha256": digest,
        "unavailable_reasons": [],
    }


def _source_rows(
    source_packets: dict,
    unavailable_reasons: dict,
    as_of_date: str,
    generated_at: str,
    contract: dict,
) -> list[dict]:
    expected = set(contract["source_order"])
    if not isinstance(source_packets, dict) or set(source_packets) != expected:
        raise StrategicCapitalPostureError("SOURCE_PACKET_KEYS_MISMATCH")
    if not isinstance(unavailable_reasons, dict) or set(unavailable_reasons) != expected:
        raise StrategicCapitalPostureError("UNAVAILABLE_REASON_KEYS_MISMATCH")
    unavailable_only = set(contract["unavailable_only_source_slots"])
    rows = []
    for name in contract["source_order"]:
        packet = source_packets[name]
        reasons = unavailable_reasons[name]
        if name in unavailable_only and packet is not None:
            raise StrategicCapitalPostureError(f"SOURCE_PACKET_NOT_YET_SUPPORTED:{name}")
        if packet is None:
            rows.append({
                "name": name,
                "availability": "UNAVAILABLE",
                "source_status": None,
                "evidence_date": None,
                "source_packet_sha256": None,
                "unavailable_reasons": _reasons(
                    reasons, f"UNAVAILABLE_REASONS_INVALID:{name}"
                ),
            })
        else:
            if reasons != []:
                raise StrategicCapitalPostureError(f"AVAILABLE_SOURCE_HAS_REASONS:{name}")
            rows.append(_validate_source(name, packet, as_of_date, generated_at, contract))
    return rows


def _constraint_rows(contract: dict) -> list[dict]:
    return [
        {
            "check": name,
            "evaluation_status": contract["runtime_evaluation_status"],
            "observed": None,
            "limit": None,
            "unit": contract["budget_unit"] if name == "ALLOCATION_SUM" else None,
            "result": None,
            "reason": "REQUIRED_RATIFIED_INPUTS_OR_POLICY_UNAVAILABLE",
        }
        for name in contract["constraint_checks"]
    ]


def _assemble(
    source_packets: dict,
    unavailable_reasons: dict,
    as_of_date: str,
    generated_at: str,
    policy_packet,
    contract: dict,
) -> dict:
    as_of = _date(as_of_date, "AS_OF_DATE_INVALID")
    generated = _utc(generated_at, "GENERATED_AT_INVALID")
    if generated[:10] < as_of:
        raise StrategicCapitalPostureError("GENERATED_BEFORE_AS_OF_DATE")
    if policy_packet is not None:
        raise StrategicCapitalPostureError("UNRATIFIED_POLICY_PACKET_FORBIDDEN")
    sources = _source_rows(
        source_packets, unavailable_reasons, as_of, generated, contract
    )
    unavailable = [row["name"] for row in sources if row["availability"] == "UNAVAILABLE"]
    binding_reasons = [
        "STRATEGIC_CAPITAL_POSTURE_POLICY_NOT_RATIFIED",
        "NUMERIC_BUDGET_VALUES_NOT_AUTHORIZED",
    ] + [f"SOURCE_UNAVAILABLE:{name}" for name in unavailable]
    market_budget = {market: None for market in contract["markets"]}
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "as_of_date": as_of,
        "generated_at": generated,
        "scope": contract["scope"],
        "status": "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED",
        "decision_status": contract["runtime_status"],
        "risk_posture": None,
        "market_budget": market_budget,
        "cash_reserve": None,
        "hedge_budget": None,
        "max_gross_risk": None,
        "max_net_risk": None,
        "theme_headroom": None,
        "budget_unit": contract["budget_unit"],
        "binding_reasons": sorted(binding_reasons),
        "constraint_checks": _constraint_rows(contract),
        "allocation_proposal": None,
        "target_exposures": None,
        "position_sizes": None,
        "order_intents": [],
        "policy_packet": None,
        "sources": sources,
        "summary": {
            "source_count": len(sources),
            "available_source_count": len(sources) - len(unavailable),
            "unavailable_source_count": len(unavailable),
            "unavailable_sources": unavailable,
            "evaluated_constraint_count": 0,
            "numeric_budget_field_count": 0,
        },
        "source_packets": copy.deepcopy(source_packets),
        "unavailable_reasons": copy.deepcopy(unavailable_reasons),
        "lineage": {
            "source_packet_sha256": {
                row["name"]: row["source_packet_sha256"] for row in sources
            },
        },
        "invariants": copy.deepcopy(contract["invariants"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "P1_REGIME_DECISION_UNAVAILABLE",
            "P2_CROSS_MARKET_FLOW_UNAVAILABLE",
            "P2_ROTATION_STATE_UNAVAILABLE",
            "STRATEGIC_CAPITAL_POSTURE_POLICY_NOT_RATIFIED",
            "BUDGET_SUM_NOT_EVALUATED",
            "OVERLAP_EXPOSURE_NOT_EVALUATED",
            "CURRENCY_BOUNDARY_NOT_EVALUATED",
            "ACTION_PROPOSAL_NOT_AUTHORIZED",
            "ORDER_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }


def build_packet(
    source_packets: dict,
    unavailable_reasons: dict,
    as_of_date: str,
    generated_at: str,
    *,
    policy_packet=None,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    packet = _assemble(
        source_packets,
        unavailable_reasons,
        as_of_date,
        generated_at,
        policy_packet,
        contract,
    )
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "as_of_date", "generated_at",
        "scope", "status", "decision_status", "risk_posture", "market_budget",
        "cash_reserve", "hedge_budget", "max_gross_risk", "max_net_risk",
        "theme_headroom", "budget_unit", "binding_reasons", "constraint_checks",
        "allocation_proposal", "target_exposures", "position_sizes", "order_intents",
        "policy_packet", "sources", "summary", "source_packets",
        "unavailable_reasons", "lineage", "invariants", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise StrategicCapitalPostureError("OUTPUT_FIELDS_MISMATCH")
    expected = _assemble(
        packet.get("source_packets"),
        packet.get("unavailable_reasons"),
        packet.get("as_of_date"),
        packet.get("generated_at"),
        packet.get("policy_packet"),
        contract,
    )
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_PACKET_SHA_INVALID")
    if actual != expected:
        raise StrategicCapitalPostureError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise StrategicCapitalPostureError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise StrategicCapitalPostureError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(bundle_path: Path, as_of_date: str, generated_at: str, output_path: Path) -> int:
    try:
        bundle = _read_json(bundle_path)
        if not isinstance(bundle, dict) or set(bundle) != {
            "source_packets", "unavailable_reasons", "policy_packet"
        }:
            raise StrategicCapitalPostureError("BUNDLE_FIELDS_MISMATCH")
        packet = build_packet(
            bundle["source_packets"],
            bundle["unavailable_reasons"],
            as_of_date,
            generated_at,
            policy_packet=bundle["policy_packet"],
        )
        write_json_atomic(output_path, packet)
        return 0
    except (StrategicCapitalPostureError, OSError, TypeError, ValueError) as exc:
        print(f"Strategic capital posture readiness failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.bundle, args.as_of_date, args.generated_at, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
