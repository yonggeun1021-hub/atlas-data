#!/usr/bin/env python3
"""P6-01 fail-closed Cash / Exposure Reduction action boundary.

Cash retention and long-exposure reduction are independent portfolio actions;
they are not aliases for a short, hedge, inverse instrument, or order.  The
current Regime contract is UNKNOWN-only and no cash/exposure policy or
portfolio risk budget is ratified, so this module deliberately emits a
tamper-evident NOT_EVALUATED packet with every action and target left null.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "cash_exposure_action_contract.json"
UPSTREAM_PATH = ROOT / "regime" / "output_contract.py"


class CashExposureActionError(ValueError):
    """Fail-closed cash/exposure boundary violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CashExposureActionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "cash_exposure_action_boundary/1",
        "output_schema_version": "cash_exposure_action_packet/1",
        "upstream_contract_version": "regime_output/v1",
        "upstream_contract_mode": "PRE_SCORE_UNKNOWN_ONLY",
        "runtime_authorized_regimes": ["UNKNOWN"],
        "action_vocabulary": ["HOLD_CASH", "REDUCE_EXPOSURE", "NO_CHANGE"],
        "runtime_evaluation_status": "NOT_EVALUATED",
        "invariant": (
            "CASH_AND_EXPOSURE_REDUCTION_ARE_INDEPENDENT_FROM_SHORT_HEDGE_AND_ORDER"
        ),
        "independent_prerequisites": [
            "REGIME_CLASSIFICATION_AUTHORIZED",
            "PORTFOLIO_EXPOSURE_SNAPSHOT_AVAILABLE",
            "CASH_EXPOSURE_POLICY_RATIFIED",
            "PORTFOLIO_RISK_BUDGET_RATIFIED",
            "ACTION_RISK_CHECKS_AUTHORIZED",
        ],
        "upstream_authority": {
            "minimum_coverage_gate_ratified": False,
            "thresholds_authorized": False,
            "weights_authorized": False,
            "regime_score_authorized": False,
            "strategy_eligibility_authorized": False,
            "production_wiring_authorized": False,
            "trading_action_authorized": False,
        },
        "authority": {
            "independent_action_boundary_only": True,
            "cash_action_authorized": False,
            "exposure_reduction_authorized": False,
            "target_weight_authorized": False,
            "position_adjustment_authorized": False,
            "short_authorized": False,
            "hedge_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CashExposureActionError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CashExposureActionError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _load_upstream_module():
    spec = importlib.util.spec_from_file_location(
        "atlas_regime_output_for_cash_exposure",
        UPSTREAM_PATH,
    )
    if spec is None or spec.loader is None:
        raise CashExposureActionError("UPSTREAM_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_upstream(value: dict, contract: dict) -> dict:
    upstream = _load_upstream_module()
    try:
        upstream_contract = upstream.load_contract()
        checked = upstream.validate_output(copy.deepcopy(value), upstream_contract)
    except upstream.OutputContractError as exc:
        raise CashExposureActionError(f"UPSTREAM_OUTPUT_INVALID:{exc}") from exc
    if (
        checked.get("contract_version") != contract["upstream_contract_version"]
        or checked.get("contract_mode") != contract["upstream_contract_mode"]
        or checked.get("regime") not in contract["runtime_authorized_regimes"]
        or checked.get("authority") != contract["upstream_authority"]
    ):
        raise CashExposureActionError("UPSTREAM_OUTPUT_IDENTITY_INVALID")
    return copy.deepcopy(checked)


def independent_action_boundary(regime: str, contract: dict | None = None) -> dict:
    """Return the only authorized action result before policy ratification."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if regime not in contract["runtime_authorized_regimes"]:
        raise CashExposureActionError(f"REGIME_NOT_AUTHORIZED:{regime}")
    return {
        "evaluation_status": contract["runtime_evaluation_status"],
        "cash_action": None,
        "exposure_reduction_action": None,
        "target_cash_weight": None,
        "target_gross_exposure": None,
        "position_adjustments": [],
        "short_intents": [],
        "hedge_intents": [],
        "order_intents": [],
        "reasons": [
            "REGIME_CLASSIFICATION_NOT_AUTHORIZED",
            "PORTFOLIO_EXPOSURE_SNAPSHOT_UNAVAILABLE",
            "CASH_EXPOSURE_POLICY_NOT_RATIFIED",
            "PORTFOLIO_RISK_BUDGET_NOT_RATIFIED",
            "ACTION_RISK_CHECKS_NOT_AUTHORIZED",
        ],
    }


def assert_no_unauthorized_action(
    *,
    cash_action=None,
    exposure_reduction_action=None,
    target_cash_weight=None,
    target_gross_exposure=None,
    position_adjustments=None,
    short_intents=None,
    hedge_intents=None,
    order_intents=None,
) -> None:
    """Reject any action smuggled across the current NOT_EVALUATED boundary."""
    scalar = (
        cash_action,
        exposure_reduction_action,
        target_cash_weight,
        target_gross_exposure,
    )
    collections = (
        position_adjustments,
        short_intents,
        hedge_intents,
        order_intents,
    )
    if any(value is not None for value in scalar) or any(
        value not in (None, []) for value in collections
    ):
        raise CashExposureActionError("UNAUTHORIZED_ACTION_SMUGGLING")


def _compose(upstream: dict, contract: dict) -> dict:
    boundary = independent_action_boundary(upstream["regime"], contract)
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "CASH_EXPOSURE_ACTION_NOT_EVALUATED",
        "market": upstream["market"],
        "generated_at": upstream["generated_at"],
        "regime": upstream["regime"],
        "direction": upstream["direction"],
        "confidence": upstream["confidence"],
        **boundary,
        "lineage": {
            "upstream_regime_output_sha256": payload_sha256(upstream),
            "upstream_contract_version": upstream["contract_version"],
            "upstream_contract_mode": upstream["contract_mode"],
        },
        "independent_action_fields": [
            "cash_action",
            "exposure_reduction_action",
        ],
        "invariant": contract["invariant"],
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": copy.deepcopy(contract["independent_prerequisites"]),
    }


def build_packet(regime_output: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    upstream = _validate_upstream(regime_output, contract)
    packet = _compose(upstream, contract)
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, upstream, contract)


def validate_packet(
    packet: dict,
    regime_output: dict,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    upstream = _validate_upstream(regime_output, contract)
    if not isinstance(packet, dict) or "packet_sha256" not in packet:
        raise CashExposureActionError("PACKET_FIELDS_MISMATCH")
    unsigned = copy.deepcopy(packet)
    digest = unsigned.pop("packet_sha256")
    if not isinstance(digest, str) or digest != payload_sha256(unsigned):
        raise CashExposureActionError("PACKET_SHA_MISMATCH")
    if unsigned != _compose(upstream, contract):
        raise CashExposureActionError("PACKET_CONTENT_MISMATCH")
    assert_no_unauthorized_action(
        cash_action=packet["cash_action"],
        exposure_reduction_action=packet["exposure_reduction_action"],
        target_cash_weight=packet["target_cash_weight"],
        target_gross_exposure=packet["target_gross_exposure"],
        position_adjustments=packet["position_adjustments"],
        short_intents=packet["short_intents"],
        hedge_intents=packet["hedge_intents"],
        order_intents=packet["order_intents"],
    )
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise CashExposureActionError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(regime_output_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(regime_output_path)))
        return 0
    except (CashExposureActionError, OSError, TypeError, ValueError) as exc:
        print(f"Cash/Exposure action boundary failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the fail-closed P6-01 Cash/Exposure action boundary"
    )
    parser.add_argument("regime_output", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.regime_output, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
