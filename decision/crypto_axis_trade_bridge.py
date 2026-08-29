#!/usr/bin/env python3
"""P5-10 read-only bridge from Crypto five-axis evidence to symbol rules.

The bridge connects one fully revalidated ``crypto_paper_decision_snapshot``
to per-symbol entry and exit contexts.  It does not invent an aggregate
Regime formula or numeric thresholds.  Until that policy is ratified, every
non-blocked entry remains WAIT and every axis-derived exit signal remains
UNKNOWN.  Existing hard-exit/security/liquidity priority in P7-13 is copied
verbatim and remains independent of this bridge.

No exchange, broker, order, account, withdrawal, or network endpoint is
called.  Every authority flag is false.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "crypto_axis_trade_bridge_contract.json"
OUTPUT_ROOT = ROOT / "evidence" / "crypto_axis_trade_bridge"
OUTPUT_SCHEMA_VERSION = "crypto_axis_trade_bridge_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CryptoAxisTradeBridgeError(ValueError):
    """Fail-closed P5-10 bridge contract or derivation violation."""


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoAxisTradeBridgeError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION = _load(
    "crypto_axis_trade_bridge_decision_snapshot",
    "decision/crypto_paper_decision_snapshot.py",
)
EXIT_MANAGER = _load(
    "crypto_axis_trade_bridge_exit_manager",
    "portfolio/crypto_paper_exit_manager.py",
)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoAxisTradeBridgeError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_axis_trade_bridge/1",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "mode": "PAPER_REVIEW_ONLY",
        "required_axes": ["TREND", "RISK_VOL", "LIQUIDITY", "BREADTH", "LEADERSHIP"],
        "aggregate_policy_status": "UNRATIFIED",
        "aggregate_regimes_currently_authorized": ["UNKNOWN"],
        "entry_policy": {
            "missing_axis_state": "WAIT",
            "unratified_aggregate_state": "WAIT",
            "upstream_blocked_state": "BLOCKED",
            "publish_order_draft": False,
        },
        "exit_policy": {
            "pre_fill_state": "NOT_APPLICABLE_UNTIL_VIRTUAL_FILL",
            "post_fill_manager_contract_version": "crypto_paper_exit_manager/1",
            "aggregate_signal_when_unratified": "UNKNOWN",
            "trend_signal_when_interpretation_unauthorized": "UNKNOWN",
            "priority_categories": [
                "HARD_EXIT", "SECURITY_LIQUIDITY", "RISK_REGIME", "TREND",
                "PROFIT_TRAIL", "TIME_REVIEW",
            ],
        },
        "axis_bindings": {
            "TREND": {
                "entry_consumers": ["P5-08_TREND"],
                "exit_consumers": ["P7-13_TREND"],
            },
            "RISK_VOL": {
                "entry_consumers": ["P5-09_REGIME_RISK_CONTEXT"],
                "exit_consumers": ["P7-13_RISK_REGIME"],
            },
            "LIQUIDITY": {
                "entry_consumers": ["P5-08_LIQUIDITY", "P5-09_BLOCKER_FRESHNESS"],
                "exit_consumers": ["P7-13_SECURITY_LIQUIDITY"],
            },
            "BREADTH": {
                "entry_consumers": ["P1-CR-08_AGGREGATE_REGIME"],
                "exit_consumers": ["P7-13_RISK_REGIME"],
            },
            "LEADERSHIP": {
                "entry_consumers": ["P5-08_RELATIVE_STRENGTH", "P1-CR-08_AGGREGATE_REGIME"],
                "exit_consumers": ["P7-13_RISK_REGIME"],
            },
        },
        "authority": {
            "market_judgment_authorized": False,
            "entry_eligibility_authorized": False,
            "exit_action_authorized": False,
            "order_authorized": False,
            "exchange_order_authorized": False,
            "broker_submission_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }


def _require_all_false(authority: object, code: str = "AUTHORITY_INVALID") -> None:
    if not isinstance(authority, dict) or not authority or any(value is not False for value in authority.values()):
        raise CryptoAxisTradeBridgeError(code)


def validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CryptoAxisTradeBridgeError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CryptoAxisTradeBridgeError(f"CONTRACT_FIELD_MISMATCH:{key}")
    _require_all_false(value["authority"], "CONTRACT_AUTHORITY_INVALID")
    exit_contract = EXIT_MANAGER.load_contract()
    if value["exit_policy"]["post_fill_manager_contract_version"] != exit_contract["contract_version"]:
        raise CryptoAxisTradeBridgeError("EXIT_MANAGER_CONTRACT_VERSION_MISMATCH")
    if value["exit_policy"]["priority_categories"] != exit_contract["priority_categories"]:
        raise CryptoAxisTradeBridgeError("EXIT_PRIORITY_DRIFT")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(Path(path)))


def _axis_coverage(snapshot: dict, contract: dict) -> dict:
    source_axes = snapshot.get("crypto_regime_five_axis")
    required = contract["required_axes"]
    if not isinstance(source_axes, dict) or set(source_axes) != set(required):
        raise CryptoAxisTradeBridgeError("FIVE_AXIS_SET_MISMATCH")
    axes = {}
    for axis in required:
        row = source_axes[axis]
        if not isinstance(row, dict) or row.get("status") not in {"DEFINED", "UNDEFINED"}:
            raise CryptoAxisTradeBridgeError(f"AXIS_STATUS_INVALID:{axis}")
        axes[axis] = {
            "status": row["status"],
            "observation_date": row.get("observation_date"),
            "available_at": row.get("available_at"),
            "warnings": copy.deepcopy(row.get("warnings") or []),
            "bindings": copy.deepcopy(contract["axis_bindings"][axis]),
        }
    missing = [axis for axis in required if axes[axis]["status"] != "DEFINED"]
    return {
        "required_count": len(required),
        "defined_count": len(required) - len(missing),
        "all_defined": not missing,
        "missing_axes": missing,
        "axes": axes,
    }


def _entry_context(candidate: dict, coverage: dict, contract: dict) -> dict:
    upstream_state = candidate["state"]
    reasons = [f"UPSTREAM_STATE:{upstream_state}"]
    if coverage["missing_axes"]:
        reasons.append("OFFICIAL_AXES_INCOMPLETE:" + ",".join(coverage["missing_axes"]))
    if contract["aggregate_policy_status"] != "RATIFIED":
        reasons.append("AGGREGATE_POLICY_UNRATIFIED")
    if upstream_state == "BLOCKED":
        state = contract["entry_policy"]["upstream_blocked_state"]
    else:
        state = contract["entry_policy"]["unratified_aggregate_state"]
    return {
        "state": state,
        "reasons": reasons,
        "aggregate_regime": "UNKNOWN",
        "order_draft": None,
        "automatic_entry_generated": False,
    }


def _exit_context(contract: dict) -> dict:
    return {
        "state": contract["exit_policy"]["pre_fill_state"],
        "regime_signal": contract["exit_policy"]["aggregate_signal_when_unratified"],
        "trend_signal": contract["exit_policy"]["trend_signal_when_interpretation_unauthorized"],
        "post_fill_manager_contract_version": contract["exit_policy"]["post_fill_manager_contract_version"],
        "priority_categories": copy.deepcopy(contract["exit_policy"]["priority_categories"]),
        "hard_exit_priority_preserved": True,
        "automatic_exit_generated": False,
        "reasons": [
            "NO_VIRTUAL_FILL_POSITION_IN_PUBLIC_DECISION_PACKET",
            "AGGREGATE_POLICY_UNRATIFIED",
            "P7_13_HARD_EXIT_SECURITY_LIQUIDITY_PRIORITY_PRESERVED",
        ],
    }


def _build_symbol_rules(snapshot: dict, coverage: dict, contract: dict) -> list[dict]:
    rows = []
    for candidate in snapshot["candidates"]:
        rows.append({
            "market": candidate["market"],
            "canonical_asset_id": candidate.get("canonical_asset_id"),
            "upstream_state": candidate["state"],
            "upstream_reason": candidate["reason"],
            "entry": _entry_context(candidate, coverage, contract),
            "exit": _exit_context(contract),
        })
    return rows


def build_bridge(decision_snapshot: dict, *, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else validate_contract(contract)
    try:
        snapshot = DECISION.validate_output(decision_snapshot)
    except DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise CryptoAxisTradeBridgeError(f"SOURCE_DECISION_INVALID:{exc}") from exc
    coverage = _axis_coverage(snapshot, contract)
    symbol_rules = _build_symbol_rules(snapshot, coverage, contract)
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "generated_at": snapshot["generated_at"],
        "operational_date_kst": snapshot["operational_date_kst"],
        "source_generation_id": snapshot["generation_id"],
        "source_decision_sha256": snapshot["payload_sha256"],
        "five_axis": coverage,
        "aggregate_policy": {
            "status": contract["aggregate_policy_status"],
            "regime": "UNKNOWN",
            "authorized_regimes": copy.deepcopy(contract["aggregate_regimes_currently_authorized"]),
        },
        "symbol_rules": symbol_rules,
        "summary": {
            "symbol_count": len(symbol_rules),
            "entry_wait_count": sum(row["entry"]["state"] == "WAIT" for row in symbol_rules),
            "entry_blocked_count": sum(row["entry"]["state"] == "BLOCKED" for row in symbol_rules),
            "automatic_entry_count": 0,
            "automatic_exit_count": 0,
        },
        "authority": copy.deepcopy(contract["authority"]),
        "source": {
            "decision_snapshot": copy.deepcopy(decision_snapshot),
            "contract": copy.deepcopy(contract),
        },
    }
    _require_all_false(packet["authority"])
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_output(packet: dict) -> dict:
    expected_keys = {
        "schema_version", "contract_version", "mode", "generated_at",
        "operational_date_kst", "source_generation_id", "source_decision_sha256",
        "five_axis", "aggregate_policy", "symbol_rules", "summary", "authority",
        "source", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoAxisTradeBridgeError("OUTPUT_SCHEMA_MISMATCH")
    if packet.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CryptoAxisTradeBridgeError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    claimed = packet.get("packet_sha256")
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        raise CryptoAxisTradeBridgeError("PACKET_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != claimed:
        raise CryptoAxisTradeBridgeError("PACKET_SHA256_MISMATCH")
    _require_all_false(packet.get("authority"))
    source = packet.get("source")
    if not isinstance(source, dict) or set(source) != {"decision_snapshot", "contract"}:
        raise CryptoAxisTradeBridgeError("SOURCE_SCHEMA_MISMATCH")
    rebuilt = build_bridge(source["decision_snapshot"], contract=source["contract"])
    if canonical_json(rebuilt) != canonical_json(packet):
        raise CryptoAxisTradeBridgeError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def output_path(packet: dict, output_root: Path = OUTPUT_ROOT) -> Path:
    source = packet["source"]["decision_snapshot"]
    return (
        Path(output_root) / source["capture_date"] / source["capture_hhmm"] /
        source["generation_id"] / "packet.json"
    )


def populate(decision_packet_path: Path, *, output_root: Path = OUTPUT_ROOT) -> dict:
    packet = build_bridge(_read_json(Path(decision_packet_path)))
    validate_output(packet)
    target = output_path(packet, output_root)
    if target.exists():
        existing = _read_json(target)
        validate_output(existing)
        if existing != packet:
            raise CryptoAxisTradeBridgeError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{target}")
        return {
            "outcome": "verified_existing", "path": str(target),
            "packet_sha256": packet["packet_sha256"],
            "source_generation_id": packet["source_generation_id"],
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".packet.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(packet, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {
        "outcome": "populated", "path": str(target),
        "packet_sha256": packet["packet_sha256"],
        "source_generation_id": packet["source_generation_id"],
    }


def _write_github_output(result: dict) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        for key in ("outcome", "path", "packet_sha256", "source_generation_id"):
            handle.write(f"{key}={result[key]}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-packet", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    result = populate(args.decision_packet, output_root=args.output_root)
    _write_github_output(result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
