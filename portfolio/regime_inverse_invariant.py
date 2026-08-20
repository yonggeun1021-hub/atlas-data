#!/usr/bin/env python3
"""P6-05 RISK_OFF/STRESS must never create an automatic inverse order."""
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
CONTRACT_PATH = ROOT / "config" / "regime_inverse_invariant_contract.json"
UPSTREAM_PATH = ROOT / "regime" / "output_contract.py"
OUTPUT_SCHEMA_VERSION = "regime_inverse_invariant_packet/1"
REGIMES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", "UNKNOWN")


class RegimeInverseInvariantError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegimeInverseInvariantError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "regime_inverse_invariant/1",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "upstream_contract_version": "regime_output/v1",
        "upstream_contract_mode": "PRE_SCORE_UNKNOWN_ONLY",
        "regime_vocabulary": list(REGIMES),
        "runtime_authorized_regimes": ["UNKNOWN"],
        "derived_inverse_evaluation_status": "NOT_EVALUATED",
        "invariant": "RISK_OFF_STRESS_NEVER_IMPLIES_AUTO_INVERSE_ORDER",
        "independent_prerequisites": [
            "HEDGE_INSTRUMENT_ELIGIBILITY_RATIFIED",
            "BEAR_HEDGE_RISK_BUDGET_RATIFIED",
            "INVERSE_STRATEGY_RULE_EVALUATION",
            "ORDER_RISK_CHECKS_AUTHORIZED",
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
            "invariant_enforcement_only": True,
            "inverse_instrument_selection_authorized": False,
            "inverse_strategy_evaluation_authorized": False,
            "inverse_signal_authorized": False,
            "inverse_order_authorized": False,
            "hedge_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise RegimeInverseInvariantError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RegimeInverseInvariantError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _load_upstream_module():
    spec = importlib.util.spec_from_file_location(
        "atlas_regime_output_contract_for_inverse_invariant",
        UPSTREAM_PATH,
    )
    if spec is None or spec.loader is None:
        raise RegimeInverseInvariantError("UPSTREAM_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify_regime(regime: str, contract: dict | None = None) -> dict:
    """Return the only authorized inverse-side consequence of a Regime value."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if regime not in contract["regime_vocabulary"]:
        raise RegimeInverseInvariantError(f"REGIME_INVALID:{regime}")
    reasons = [
        "HEDGE_INSTRUMENT_ELIGIBILITY_NOT_RATIFIED",
        "BEAR_HEDGE_RISK_BUDGET_NOT_RATIFIED",
        "INDEPENDENT_INVERSE_STRATEGY_EVALUATION_REQUIRED",
        "ORDER_RISK_CHECKS_NOT_AUTHORIZED",
    ]
    if regime in {"RISK_OFF", "STRESS"}:
        reasons.insert(0, "REGIME_DOES_NOT_IMPLY_INVERSE_ORDER")
    else:
        reasons.insert(0, "REGIME_HAS_NO_INVERSE_ORDER_AUTHORITY")
    return {
        "regime": regime,
        "inverse_instrument": None,
        "inverse_signal": None,
        "inverse_order_intent": None,
        "inverse_evaluation_status": contract["derived_inverse_evaluation_status"],
        "invariant_status": "ENFORCED",
        "reasons": reasons,
    }


def assert_inverse_order_not_derived(regime: str, proposed_order) -> None:
    classify_regime(regime)
    if proposed_order is not None:
        raise RegimeInverseInvariantError(
            f"DERIVED_INVERSE_ORDER_FORBIDDEN:{regime}"
        )


def _validate_upstream_output(value: dict, contract: dict) -> dict:
    upstream = _load_upstream_module()
    try:
        upstream_contract = upstream.load_contract()
        checked = upstream.validate_output(copy.deepcopy(value), upstream_contract)
    except upstream.OutputContractError as exc:
        raise RegimeInverseInvariantError(f"UPSTREAM_OUTPUT_INVALID:{exc}") from exc
    if (
        checked.get("contract_version") != contract["upstream_contract_version"]
        or checked.get("contract_mode") != contract["upstream_contract_mode"]
        or upstream_contract.get("regime_vocabulary") != contract["regime_vocabulary"]
        or upstream_contract.get("runtime_authorized_regimes")
        != contract["runtime_authorized_regimes"]
        or checked.get("authority") != contract["upstream_authority"]
    ):
        raise RegimeInverseInvariantError("UPSTREAM_OUTPUT_IDENTITY_INVALID")
    return copy.deepcopy(checked)


def build_packet(regime_output: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    checked = _validate_upstream_output(regime_output, contract)
    boundary = classify_regime(checked["regime"], contract)
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED",
        "market": checked["market"],
        "generated_at": checked["generated_at"],
        "direction": checked["direction"],
        "confidence": checked["confidence"],
        **boundary,
        "lineage": {
            "upstream_regime_output_sha256": payload_sha256(checked),
            "upstream_contract_version": checked["contract_version"],
            "upstream_contract_mode": checked["contract_mode"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": copy.deepcopy(contract["independent_prerequisites"]),
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
        raise RegimeInverseInvariantError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
    except (RegimeInverseInvariantError, OSError, TypeError, ValueError) as exc:
        print(f"Regime/Inverse invariant failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enforce RISK_OFF/STRESS != automatic inverse order"
    )
    parser.add_argument("regime_output", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.regime_output, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
