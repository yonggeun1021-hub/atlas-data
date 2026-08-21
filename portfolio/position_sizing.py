#!/usr/bin/env python3
"""P7-02 externally ratified Portfolio position sizing engine."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "position_sizing_contract.json"
BUCKET_SOURCE = ROOT / "portfolio" / "bucket_membership.py"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")


class PositionSizingError(ValueError):
    """Fail-closed P7-02 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PositionSizingError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _load_bucket_module():
    spec = importlib.util.spec_from_file_location("p702_bucket_membership", BUCKET_SOURCE)
    if spec is None or spec.loader is None:
        raise PositionSizingError("BUCKET_VALIDATOR_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "position_sizing/1",
        "input_schema_version": "position_sizing_input/1",
        "policy_schema_version": "position_sizing_policy/1",
        "output_schema_version": "position_sizing_packet/1",
        "formula_id": "MIN_RATIFIED_LIMITS_THEN_TARGET_UTILIZATION_V1",
        "markets": ["US", "KOREA", "CRYPTO"],
        "evidence_states": [
            "backtest_only", "forward_early", "forward_established", "operating"
        ],
        "limit_order": [
            "DEPLOYMENT_HEADROOM", "CASH_AVAILABLE", "BUCKET_HEADROOM",
            "POSITION_MAX", "EVIDENCE_MAX", "PORTFOLIO_LOSS_HEADROOM",
            "PER_POSITION_LOSS",
        ],
        "repository_default_policy": "ABSENT",
        "policy_requirement": "EXTERNAL_RATIFIED_SIZING_POLICY_REQUIRED",
        "rounding_digits": 12,
        "input_authority": {
            "candidate_and_portfolio_state_only": True,
            "sizing_formula_authorized": False,
            "target_selection_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "policy_authority": {
            "sizing_parameters_only": True,
            "candidate_selection_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "ratified_limit_calculation_only": True,
            "candidate_selection_authorized": False,
            "entry_trigger_authorized": False,
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
        raise PositionSizingError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise PositionSizingError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> str:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise PositionSizingError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PositionSizingError(code) from exc
    if parsed.isoformat() != value:
        raise PositionSizingError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise PositionSizingError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PositionSizingError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise PositionSizingError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise PositionSizingError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PositionSizingError(code)
    return value


def _sha(value, code: str, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PositionSizingError(code)
    return value


def _decimal(value, code: str, *, positive: bool, maximum_one: bool = False) -> Decimal:
    if not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None:
        raise PositionSizingError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PositionSizingError(code) from exc
    if (positive and parsed <= 0) or (not positive and parsed < 0):
        raise PositionSizingError(code)
    if maximum_one and parsed > 1:
        raise PositionSizingError(code)
    return parsed


def _rounded(value: Decimal, digits: int) -> str:
    quantum = Decimal(1).scaleb(-digits)
    result = value.quantize(quantum, rounding=ROUND_HALF_UP)
    text = format(result, "f").rstrip("0").rstrip(".")
    return text or "0"


def _normalize_assignment_set(value: dict) -> dict:
    normalized = copy.deepcopy(value)
    if isinstance(normalized, dict):
        if isinstance(normalized.get("buckets"), list):
            normalized["buckets"] = sorted(
                normalized["buckets"], key=lambda row: row.get("bucket_id", "")
            )
        if isinstance(normalized.get("assignments"), list):
            normalized["assignments"] = sorted(
                normalized["assignments"],
                key=lambda row: (
                    row.get("asset_id", ""), row.get("valid_from", ""),
                    row.get("bucket_id", ""),
                ),
            )
    return normalized


def _validate_policy(value: dict, as_of_date: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_id", "status", "ratified_by",
        "ratified_at", "effective_from", "effective_to", "formula_id",
        "max_planned_loss_per_position_nav_fraction", "target_utilization_fraction",
        "policy_basis_ref", "policy_basis_sha256", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PositionSizingError("POLICY_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("formula_id") != contract["formula_id"]
        or value.get("authority") != contract["policy_authority"]
    ):
        raise PositionSizingError("POLICY_IDENTITY_INVALID")
    ratified_at = _utc(value.get("ratified_at"), "POLICY_RATIFIED_AT_INVALID")
    effective_from = _date(value.get("effective_from"), "POLICY_EFFECTIVE_FROM_INVALID")
    effective_to = None if value.get("effective_to") is None else _date(
        value["effective_to"], "POLICY_EFFECTIVE_TO_INVALID"
    )
    if ratified_at[:10] > effective_from or (
        effective_to is not None and effective_to <= effective_from
    ):
        raise PositionSizingError("POLICY_INTERVAL_INVALID")
    if as_of_date < effective_from or (
        effective_to is not None and as_of_date >= effective_to
    ):
        raise PositionSizingError("POLICY_NOT_EFFECTIVE")
    max_loss = _decimal(
        value.get("max_planned_loss_per_position_nav_fraction"),
        "POLICY_MAX_POSITION_LOSS_INVALID",
        positive=True,
        maximum_one=True,
    )
    utilization = _decimal(
        value.get("target_utilization_fraction"),
        "POLICY_TARGET_UTILIZATION_INVALID",
        positive=True,
        maximum_one=True,
    )
    digest = _sha(value.get("packet_sha256"), "POLICY_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise PositionSizingError("POLICY_SHA_MISMATCH")
    return {
        "policy_id": _token(value.get("policy_id"), "POLICY_ID_INVALID"),
        "max_planned_loss_per_position": max_loss,
        "target_utilization": utilization,
        "policy_basis_ref": _text(value.get("policy_basis_ref"), "POLICY_BASIS_REF_INVALID"),
        "policy_basis_sha256": _sha(value.get("policy_basis_sha256"), "POLICY_BASIS_SHA_INVALID"),
        "packet_sha256": digest,
        "packet": copy.deepcopy(value),
    }


def _validate_input(value: dict, as_of_date: str, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "snapshot_id", "as_of_date",
        "candidate", "portfolio_state", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise PositionSizingError("INPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("as_of_date") != as_of_date
        or value.get("authority") != contract["input_authority"]
    ):
        raise PositionSizingError("INPUT_IDENTITY_INVALID")
    candidate = value.get("candidate")
    candidate_fields = {
        "asset_id", "market", "evidence_state", "entry_price", "planned_stop_price",
        "asset_identity_sha256", "discovery_result_sha256", "rule_result_sha256",
    }
    if not isinstance(candidate, dict) or set(candidate) != candidate_fields:
        raise PositionSizingError("CANDIDATE_FIELDS_MISMATCH")
    market = candidate.get("market")
    evidence_state = candidate.get("evidence_state")
    if market not in contract["markets"] or evidence_state not in contract["evidence_states"]:
        raise PositionSizingError("CANDIDATE_IDENTITY_INVALID")
    entry = _decimal(candidate.get("entry_price"), "ENTRY_PRICE_INVALID", positive=True)
    stop = _decimal(candidate.get("planned_stop_price"), "PLANNED_STOP_PRICE_INVALID", positive=True)
    if stop >= entry:
        raise PositionSizingError("PLANNED_STOP_NOT_BELOW_ENTRY")
    checked_candidate = {
        "asset_id": _token(candidate.get("asset_id"), "CANDIDATE_ASSET_ID_INVALID"),
        "market": market,
        "evidence_state": evidence_state,
        "entry_price": candidate["entry_price"],
        "planned_stop_price": candidate["planned_stop_price"],
        "asset_identity_sha256": _sha(candidate.get("asset_identity_sha256"), "ASSET_IDENTITY_SHA_INVALID"),
        "discovery_result_sha256": _sha(candidate.get("discovery_result_sha256"), "DISCOVERY_RESULT_SHA_INVALID"),
        "rule_result_sha256": _sha(candidate.get("rule_result_sha256"), "RULE_RESULT_SHA_INVALID"),
    }
    state = value.get("portfolio_state")
    state_fields = {
        "current_deployed_nav_fraction", "cash_available_nav_fraction",
        "bucket_current_exposure_nav_fraction",
        "current_portfolio_planned_loss_nav_fraction", "portfolio_snapshot_sha256",
        "loss_state_sha256", "concentration_guard_packet_sha256",
        "market_theme_budget_packet_sha256", "crypto_exposure_limit_packet_sha256",
    }
    if not isinstance(state, dict) or set(state) != state_fields:
        raise PositionSizingError("PORTFOLIO_STATE_FIELDS_MISMATCH")
    fractions = {
        key: _decimal(state.get(key), f"PORTFOLIO_FRACTION_INVALID:{key}", positive=False, maximum_one=True)
        for key in (
            "current_deployed_nav_fraction", "cash_available_nav_fraction",
            "bucket_current_exposure_nav_fraction",
            "current_portfolio_planned_loss_nav_fraction",
        )
    }
    if fractions["current_deployed_nav_fraction"] + fractions["cash_available_nav_fraction"] > 1:
        raise PositionSizingError("DEPLOYED_PLUS_CASH_EXCEEDS_NAV")
    if fractions["bucket_current_exposure_nav_fraction"] > fractions["current_deployed_nav_fraction"]:
        raise PositionSizingError("BUCKET_EXPOSURE_EXCEEDS_DEPLOYED")
    crypto_sha = _sha(
        state.get("crypto_exposure_limit_packet_sha256"),
        "CRYPTO_EXPOSURE_SHA_INVALID",
        nullable=True,
    )
    if (market == "CRYPTO") != (crypto_sha is not None):
        raise PositionSizingError("CRYPTO_LINEAGE_PRESENCE_MISMATCH")
    checked_state = {
        **{key: state[key] for key in fractions},
        "portfolio_snapshot_sha256": _sha(state.get("portfolio_snapshot_sha256"), "PORTFOLIO_SNAPSHOT_SHA_INVALID"),
        "loss_state_sha256": _sha(state.get("loss_state_sha256"), "LOSS_STATE_SHA_INVALID"),
        "concentration_guard_packet_sha256": _sha(state.get("concentration_guard_packet_sha256"), "CONCENTRATION_GUARD_SHA_INVALID"),
        "market_theme_budget_packet_sha256": _sha(state.get("market_theme_budget_packet_sha256"), "MARKET_THEME_BUDGET_SHA_INVALID"),
        "crypto_exposure_limit_packet_sha256": crypto_sha,
    }
    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "snapshot_id": _token(value.get("snapshot_id"), "SNAPSHOT_ID_INVALID"),
        "as_of_date": as_of_date,
        "candidate": checked_candidate,
        "portfolio_state": checked_state,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "INPUT_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise PositionSizingError("INPUT_SHA_MISMATCH")
    return {
        "normalized": normalized,
        "packet_sha256": digest,
        "entry": entry,
        "stop": stop,
        "fractions": fractions,
    }


def _validate_sources(assignment_set: dict, constitution: dict, sizing_input: dict, sizing_policy: dict, as_of_date: str, contract: dict) -> dict:
    as_of_date = _date(as_of_date, "AS_OF_DATE_INVALID")
    bucket_module = _load_bucket_module()
    normalized_assignment = _normalize_assignment_set(assignment_set)
    try:
        bucket_packet = bucket_module.build_packet(
            normalized_assignment, constitution, as_of_date
        )
    except Exception as exc:
        raise PositionSizingError(f"BUCKET_MEMBERSHIP_VALIDATION_FAILED:{exc}") from exc
    checked_input = _validate_input(sizing_input, as_of_date, contract)
    checked_policy = _validate_policy(sizing_policy, as_of_date, contract)
    candidate = checked_input["normalized"]["candidate"]
    memberships = [
        row for row in bucket_packet["active_memberships"]
        if row["asset_id"] == candidate["asset_id"]
    ]
    if len(memberships) != 1:
        raise PositionSizingError("CANDIDATE_ACTIVE_MEMBERSHIP_COUNT_INVALID")
    membership = memberships[0]
    if (
        membership["subject_kind"] != "CANDIDATE"
        or membership["market"] != candidate["market"]
        or membership["asset_identity_sha256"] != candidate["asset_identity_sha256"]
        or membership["discovery_result_sha256"] != candidate["discovery_result_sha256"]
        or membership["rule_result_sha256"] != candidate["rule_result_sha256"]
    ):
        raise PositionSizingError("CANDIDATE_MEMBERSHIP_LINEAGE_MISMATCH")
    return {
        "assignment_set": normalized_assignment,
        "constitution": copy.deepcopy(constitution),
        "bucket_packet": bucket_packet,
        "input": checked_input,
        "policy": checked_policy,
        "membership": membership,
        "as_of_date": as_of_date,
    }


def _pct_fraction(value) -> Decimal:
    return Decimal(str(value)) / Decimal(100)


def _assemble(checked: dict, contract: dict) -> dict:
    source = checked["input"]["normalized"]
    candidate = source["candidate"]
    state = checked["input"]["fractions"]
    constitution = checked["constitution"]
    policy = checked["policy"]
    stop_distance = (checked["input"]["entry"] - checked["input"]["stop"]) / checked["input"]["entry"]
    max_stop = _pct_fraction(constitution["B5_stop_loss_pct"])
    max_deployed = Decimal(1) - _pct_fraction(constitution["B2_cash_floor_pct"])
    limits = {
        "DEPLOYMENT_HEADROOM": max(Decimal(0), max_deployed - state["current_deployed_nav_fraction"]),
        "CASH_AVAILABLE": state["cash_available_nav_fraction"],
        "BUCKET_HEADROOM": max(Decimal(0), _pct_fraction(constitution["B3_bucket_max_pct"]) - state["bucket_current_exposure_nav_fraction"]),
        "POSITION_MAX": _pct_fraction(constitution["B4_position_max_pct"]),
        "EVIDENCE_MAX": _pct_fraction(constitution["B7_evidence_state_max_pct"][candidate["evidence_state"]]),
        "PORTFOLIO_LOSS_HEADROOM": max(Decimal(0), _pct_fraction(constitution["B6_portfolio_max_loss_pct"]) - state["current_portfolio_planned_loss_nav_fraction"]) / stop_distance,
        "PER_POSITION_LOSS": policy["max_planned_loss_per_position"] / stop_distance,
    }
    blockers = []
    if stop_distance > max_stop:
        blockers.append("STOP_DISTANCE_EXCEEDS_CONSTITUTION")
    for name, value in limits.items():
        if value == 0:
            blockers.append(f"NO_{name}")
    maximum = Decimal(0) if blockers else min(limits.values())
    target = maximum * policy["target_utilization"]
    digits = contract["rounding_digits"]
    status = "SIZING_BLOCKED" if blockers else "MAXIMUM_AND_TARGET_SIZED_NO_ACTION_AUTHORITY"
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": status,
        "snapshot_id": source["snapshot_id"],
        "as_of_date": checked["as_of_date"],
        "asset_id": candidate["asset_id"],
        "market": candidate["market"],
        "bucket_id": checked["membership"]["bucket_id"],
        "evidence_state": candidate["evidence_state"],
        "stop_distance_fraction": _rounded(stop_distance, digits),
        "limits": [
            {
                "limit": name,
                "maximum_position_weight_nav_fraction": _rounded(limits[name], digits),
            }
            for name in contract["limit_order"]
        ],
        "binding_limits": [] if blockers else [
            name for name in contract["limit_order"] if limits[name] == maximum
        ],
        "maximum_position_weight_nav_fraction": _rounded(maximum, digits),
        "target_position_weight_nav_fraction": _rounded(target, digits),
        "planned_loss_at_max_nav_fraction": _rounded(maximum * stop_distance, digits),
        "planned_loss_at_target_nav_fraction": _rounded(target * stop_distance, digits),
        "blocking_reasons": sorted(set(blockers)),
        "action": None,
        "entry_trigger": None,
        "order_intent": None,
        "source_packets": {
            "assignment_set": copy.deepcopy(checked["assignment_set"]),
            "constitution": copy.deepcopy(constitution),
            "bucket_membership": copy.deepcopy(checked["bucket_packet"]),
            "sizing_input": copy.deepcopy(source),
            "sizing_policy": copy.deepcopy(policy["packet"]),
        },
        "lineage": {
            "bucket_membership_packet_sha256": checked["bucket_packet"]["packet_sha256"],
            "sizing_input_packet_sha256": checked["input"]["packet_sha256"],
            "sizing_policy_packet_sha256": policy["packet_sha256"],
            "constitution_sha256": payload_sha256(constitution),
            **{
                key: value for key, value in source["portfolio_state"].items()
                if key.endswith("sha256")
            },
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REPOSITORY_DEFAULT_SIZING_POLICY_ABSENT",
            "LIVE_PORTFOLIO_STATE_NOT_WIRED",
            "CANDIDATE_SELECTION_AND_ENTRY_NOT_AUTHORIZED",
            "ACTION_ORDER_PRODUCTION_TRADING_NOT_AUTHORIZED",
        ],
    }


def build_packet(assignment_set: dict, constitution: dict, sizing_input: dict, sizing_policy: dict, as_of_date: str, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    checked = _validate_sources(
        assignment_set, constitution, sizing_input, sizing_policy, as_of_date, contract
    )
    packet = _assemble(checked, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "snapshot_id", "as_of_date",
        "asset_id", "market", "bucket_id", "evidence_state", "stop_distance_fraction",
        "limits", "binding_limits", "maximum_position_weight_nav_fraction",
        "target_position_weight_nav_fraction", "planned_loss_at_max_nav_fraction",
        "planned_loss_at_target_nav_fraction", "blocking_reasons", "action",
        "entry_trigger", "order_intent", "source_packets", "lineage", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise PositionSizingError("OUTPUT_FIELDS_MISMATCH")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {
        "assignment_set", "constitution", "bucket_membership", "sizing_input", "sizing_policy"
    }:
        raise PositionSizingError("OUTPUT_SOURCES_INVALID")
    lineage = packet.get("lineage")
    input_value = copy.deepcopy(sources["sizing_input"])
    input_value["packet_sha256"] = lineage.get("sizing_input_packet_sha256") if isinstance(lineage, dict) else None
    checked = _validate_sources(
        sources["assignment_set"], sources["constitution"], input_value,
        sources["sizing_policy"], packet.get("as_of_date"), contract,
    )
    if sources["bucket_membership"] != checked["bucket_packet"]:
        raise PositionSizingError("OUTPUT_BUCKET_PACKET_MISMATCH")
    expected = _assemble(checked, contract)
    actual = copy.deepcopy(packet)
    digest = _sha(actual.pop("packet_sha256", None), "OUTPUT_SHA_INVALID")
    if actual != expected:
        raise PositionSizingError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise PositionSizingError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise PositionSizingError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(assignment_path: Path, constitution_path: Path, input_path: Path, policy_path: Path, as_of_date: str, output_path: Path) -> int:
    try:
        packet = build_packet(
            _read_json(assignment_path), _read_json(constitution_path),
            _read_json(input_path), _read_json(policy_path), as_of_date,
        )
        write_json_atomic(output_path, packet)
        return 0
    except (PositionSizingError, OSError, TypeError, ValueError) as exc:
        print(f"Position sizing failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assignment_set", type=Path)
    parser.add_argument("constitution", type=Path)
    parser.add_argument("sizing_input", type=Path)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(
        args.assignment_set, args.constitution, args.sizing_input, args.policy,
        args.as_of_date, args.out,
    )


if __name__ == "__main__":
    raise SystemExit(main())
