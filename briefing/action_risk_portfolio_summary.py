#!/usr/bin/env python3
"""P8-06 fail-closed Action / Bear-Hedge / Portfolio briefing summary."""
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
CONTRACT_PATH = ROOT / "config" / "action_risk_portfolio_summary_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,159}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _load_unified():
    path = ROOT / "decision" / "unified_decision_contract.py"
    spec = importlib.util.spec_from_file_location("atlas_unified_for_p806", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"UNIFIED_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UNIFIED = _load_unified()


class ActionRiskPortfolioSummaryError(ValueError):
    """Fail-closed P8-06 source or summary violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionRiskPortfolioSummaryError(f"JSON_READ_FAILED:{path}:{exc}") from exc


SOURCE_IDENTITIES = {
    "BEAR_HEDGE_BUDGET": (
        "bear_hedge_budget_packet/1", "bear_hedge_risk_budget/1",
        ["BEAR_HEDGE_BUDGET_SET_VALIDATED"],
        "config/bear_hedge_risk_budget_contract.json",
    ),
    "CASH_EXPOSURE_CRYPTO": (
        "cash_exposure_action_packet/1", "cash_exposure_action_boundary/1",
        ["CASH_EXPOSURE_ACTION_NOT_EVALUATED"],
        "config/cash_exposure_action_contract.json",
    ),
    "CASH_EXPOSURE_KOREA": (
        "cash_exposure_action_packet/1", "cash_exposure_action_boundary/1",
        ["CASH_EXPOSURE_ACTION_NOT_EVALUATED"],
        "config/cash_exposure_action_contract.json",
    ),
    "CASH_EXPOSURE_US": (
        "cash_exposure_action_packet/1", "cash_exposure_action_boundary/1",
        ["CASH_EXPOSURE_ACTION_NOT_EVALUATED"],
        "config/cash_exposure_action_contract.json",
    ),
    "CONCENTRATION_GUARD": (
        "concentration_correlation_packet/1", "concentration_correlation_guard/1",
        ["LIMIT_BREACH", "WITHIN_RATIFIED_LIMITS"],
        "config/concentration_correlation_guard_contract.json",
    ),
    "CRYPTO_EXPOSURE_LIMIT": (
        "crypto_exposure_packet/1", "crypto_exposure_limit/1",
        ["LIMIT_BREACH", "WITHIN_RATIFIED_LIMITS"],
        "config/crypto_exposure_limit_contract.json",
    ),
    "HEDGE_ELIGIBILITY": (
        "hedge_instrument_eligibility_packet/1", "hedge_instrument_eligibility/1",
        ["ELIGIBILITY_REGISTRY_VALIDATED"],
        "config/hedge_instrument_eligibility_contract.json",
    ),
    "INVERSE_CRYPTO": (
        "regime_inverse_invariant_packet/1", "regime_inverse_invariant/1",
        ["INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED"],
        "config/regime_inverse_invariant_contract.json",
    ),
    "INVERSE_KOREA": (
        "regime_inverse_invariant_packet/1", "regime_inverse_invariant/1",
        ["INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED"],
        "config/regime_inverse_invariant_contract.json",
    ),
    "INVERSE_US": (
        "regime_inverse_invariant_packet/1", "regime_inverse_invariant/1",
        ["INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED"],
        "config/regime_inverse_invariant_contract.json",
    ),
    "LONG_SHORT_INVARIANT": (
        "long_short_invariant_packet/1", "long_short_invariant/1",
        ["INVARIANT_ENFORCED_SHORT_NOT_EVALUATED"],
        "config/long_short_invariant_contract.json",
    ),
    "MARKET_THEME_BUDGET": (
        "market_theme_exposure_packet/1", "market_theme_exposure_budget/1",
        ["LIMIT_BREACH", "WITHIN_RATIFIED_BUDGET"],
        "config/market_theme_exposure_budget_contract.json",
    ),
    "PLANNED_LOSS_BUDGET": (
        "planned_loss_packet/1", "planned_loss_budget/1",
        ["LIMIT_BREACH", "WITHIN_RATIFIED_LOSS_BUDGET"],
        "config/planned_loss_budget_contract.json",
    ),
    "UNIFIED_DECISION": (
        "unified_daily_decision/1", "unified_decision_contract/1",
        ["DAILY_DECISION_ASSEMBLED_NO_ACTION_AUTHORITY"],
        "config/unified_decision_contract.json",
    ),
}


def _expected_contract() -> dict:
    source_order = [
        "UNIFIED_DECISION", "CASH_EXPOSURE_US", "CASH_EXPOSURE_KOREA",
        "CASH_EXPOSURE_CRYPTO", "LONG_SHORT_INVARIANT", "INVERSE_US",
        "INVERSE_KOREA", "INVERSE_CRYPTO", "HEDGE_ELIGIBILITY",
        "BEAR_HEDGE_BUDGET", "CONCENTRATION_GUARD", "MARKET_THEME_BUDGET",
        "CRYPTO_EXPOSURE_LIMIT", "PLANNED_LOSS_BUDGET",
    ]
    return {
        "schema_version": 1,
        "contract_version": "action_risk_portfolio_summary/1",
        "output_schema_version": "action_risk_portfolio_summary_packet/1",
        "slots": ["morning", "evening"],
        "action_categories": ["BUY", "WATCH", "REDUCE", "HEDGE", "EXIT", "NOTHING"],
        "runtime_action_status": "NOT_EVALUATED",
        "source_order": source_order,
        "required_sources": ["UNIFIED_DECISION"],
        "risk_sources": [
            "CONCENTRATION_GUARD", "MARKET_THEME_BUDGET",
            "CRYPTO_EXPOSURE_LIMIT", "PLANNED_LOSS_BUDGET",
        ],
        "source_specs": {
            name: {
                "schema_version": identity[0],
                "contract_version": identity[1],
                "statuses": identity[2],
                "authority_contract": identity[3],
            }
            for name, identity in SOURCE_IDENTITIES.items()
        },
        "authority": {
            "briefing_read_model_only": True,
            "source_interpretation_authorized": False,
            "action_generation_authorized": False,
            "nothing_action_inference_authorized": False,
            "position_adjustment_authorized": False,
            "hedge_selection_authorized": False,
            "exit_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    fields = {
        "schema_version", "contract_version", "output_schema_version", "slots",
        "action_categories", "runtime_action_status", "source_order",
        "required_sources", "risk_sources", "source_specs", "authority",
    }
    if not isinstance(value, dict) or set(value) != fields or value != expected:
        raise ActionRiskPortfolioSummaryError("CONTRACT_MISMATCH")
    if sorted(value["source_specs"]) != sorted(value["source_order"]):
        raise ActionRiskPortfolioSummaryError("CONTRACT_SOURCE_SPECS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ActionRiskPortfolioSummaryError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ActionRiskPortfolioSummaryError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ActionRiskPortfolioSummaryError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ActionRiskPortfolioSummaryError(code)
    return value


def _reasons(value, code: str, *, empty_allowed=False) -> list[str]:
    if (
        not isinstance(value, list)
        or value != sorted(set(value))
        or (not empty_allowed and not value)
        or any(not isinstance(item, str) or REASON_RE.fullmatch(item) is None for item in value)
    ):
        raise ActionRiskPortfolioSummaryError(code)
    return list(value)


def _source_authority(spec: dict) -> dict:
    path = ROOT / spec["authority_contract"]
    value = _read_json(path)
    authority = value.get("authority")
    if not isinstance(authority, dict):
        raise ActionRiskPortfolioSummaryError(
            f"SOURCE_AUTHORITY_CONTRACT_INVALID:{spec['authority_contract']}"
        )
    return copy.deepcopy(authority)


FORBIDDEN_ACTION_KEYS = {
    "action", "cash_action", "exposure_reduction_action", "recommended_action",
    "recommended_rebalance", "recommended_exit", "target_cash_weight",
    "target_gross_exposure", "target_weights", "target_exposures",
    "target_crypto_exposure", "position_size", "position_sizes", "selected_instrument",
    "hedge_size", "budget_usage", "short_result", "inverse_instrument",
    "inverse_signal", "inverse_order_intent", "order_intent", "order_intents",
    "stop_order_intents", "position_adjustments", "short_intents", "hedge_intents",
    "entry_trigger", "buy_signal", "watch_action", "reduce_action", "exit_action",
    "exit_signal", "hedge_signal", "investment_action", "stage_transition",
    "nothing_action", "recommended_allocation", "order", "orders",
}


def _assert_no_action_smuggling(value, path="source") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_ACTION_KEYS and item not in (None, [], {}):
                raise ActionRiskPortfolioSummaryError(
                    f"SOURCE_ACTION_SMUGGLING:{path}.{key}"
                )
            _assert_no_action_smuggling(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_action_smuggling(item, f"{path}[{index}]")


def _validate_source(name: str, packet: dict, contract: dict) -> dict:
    spec = contract["source_specs"][name]
    if not isinstance(packet, dict):
        raise ActionRiskPortfolioSummaryError(f"SOURCE_NOT_OBJECT:{name}")
    if (
        packet.get("schema_version") != spec["schema_version"]
        or packet.get("contract_version") != spec["contract_version"]
        or packet.get("status") not in spec["statuses"]
        or packet.get("authority") != _source_authority(spec)
    ):
        raise ActionRiskPortfolioSummaryError(f"SOURCE_IDENTITY_INVALID:{name}")
    digest = _sha(packet.get("packet_sha256"), f"SOURCE_SHA_INVALID:{name}")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ActionRiskPortfolioSummaryError(f"SOURCE_PACKET_SHA_MISMATCH:{name}")
    _assert_no_action_smuggling(packet, name)
    if name == "UNIFIED_DECISION":
        try:
            UNIFIED.validate_packet(copy.deepcopy(packet))
        except UNIFIED.UnifiedDecisionContractError as exc:
            raise ActionRiskPortfolioSummaryError(f"UNIFIED_DECISION_INVALID:{exc}") from exc
    breaches = packet.get("breaches", [])
    if name in contract["risk_sources"]:
        if not isinstance(breaches, list):
            raise ActionRiskPortfolioSummaryError(f"SOURCE_BREACHES_INVALID:{name}")
    elif "breaches" in packet and not isinstance(breaches, list):
        raise ActionRiskPortfolioSummaryError(f"SOURCE_BREACHES_INVALID:{name}")
    return {
        "name": name,
        "availability": "AVAILABLE",
        "source_status": packet["status"],
        "source_packet_sha256": digest,
        "unavailable_reasons": [],
        "breaches": copy.deepcopy(breaches) if name in contract["risk_sources"] else [],
    }


def _source_rows(source_packets: dict, unavailable_reasons: dict, contract: dict) -> list[dict]:
    expected = set(contract["source_order"])
    if not isinstance(source_packets, dict) or set(source_packets) != expected:
        raise ActionRiskPortfolioSummaryError("SOURCE_PACKET_KEYS_MISMATCH")
    if not isinstance(unavailable_reasons, dict) or set(unavailable_reasons) != expected:
        raise ActionRiskPortfolioSummaryError("UNAVAILABLE_REASON_KEYS_MISMATCH")
    rows = []
    for name in contract["source_order"]:
        packet = source_packets[name]
        reasons = unavailable_reasons[name]
        if packet is None:
            if name in contract["required_sources"]:
                raise ActionRiskPortfolioSummaryError(f"REQUIRED_SOURCE_UNAVAILABLE:{name}")
            rows.append({
                "name": name,
                "availability": "UNAVAILABLE",
                "source_status": None,
                "source_packet_sha256": None,
                "unavailable_reasons": _reasons(
                    reasons, f"UNAVAILABLE_REASONS_INVALID:{name}"
                ),
                "breaches": [],
            })
        else:
            if reasons != []:
                raise ActionRiskPortfolioSummaryError(f"AVAILABLE_SOURCE_HAS_REASONS:{name}")
            rows.append(_validate_source(name, packet, contract))
    return rows


ACTION_SOURCES = {
    "BUY": ["UNIFIED_DECISION"],
    "WATCH": ["UNIFIED_DECISION"],
    "REDUCE": ["CASH_EXPOSURE_US", "CASH_EXPOSURE_KOREA", "CASH_EXPOSURE_CRYPTO"],
    "HEDGE": [
        "LONG_SHORT_INVARIANT", "INVERSE_US", "INVERSE_KOREA", "INVERSE_CRYPTO",
        "HEDGE_ELIGIBILITY", "BEAR_HEDGE_BUDGET",
    ],
    "EXIT": ["UNIFIED_DECISION", "PLANNED_LOSS_BUDGET"],
    "NOTHING": ["UNIFIED_DECISION"],
}


def _action_rows(source_rows: list[dict], contract: dict) -> list[dict]:
    by_name = {row["name"]: row for row in source_rows}
    rows = []
    for category in contract["action_categories"]:
        sources = ACTION_SOURCES[category]
        evidence = [
            by_name[name]["source_packet_sha256"]
            for name in sources if by_name[name]["availability"] == "AVAILABLE"
        ]
        reasons = [f"{category}_ACTION_NOT_AUTHORIZED"]
        reasons.extend(
            f"SOURCE_UNAVAILABLE:{name}"
            for name in sources if by_name[name]["availability"] == "UNAVAILABLE"
        )
        if category == "NOTHING":
            reasons.append("ABSENCE_OF_ACTION_IS_NOT_NOTHING_ACTION")
        rows.append({
            "category": category,
            "evaluation_status": contract["runtime_action_status"],
            "action": None,
            "evidence_packet_sha256": evidence,
            "reasons": sorted(set(reasons)),
        })
    return rows


def build_summary(
    source_packets: dict,
    unavailable_reasons: dict,
    generated_at: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    generated_at = _utc(generated_at, "GENERATED_AT_INVALID")
    sources = _source_rows(source_packets, unavailable_reasons, contract)
    unified_packet = source_packets["UNIFIED_DECISION"]
    if unified_packet["generated_at"] > generated_at:
        raise ActionRiskPortfolioSummaryError("UNIFIED_DECISION_FROM_FUTURE")
    if unified_packet["generated_at"][:10] != generated_at[:10]:
        raise ActionRiskPortfolioSummaryError("SUMMARY_DATE_MISMATCH")
    actions = _action_rows(sources, contract)
    risk_findings = [
        {
            "source": row["name"],
            "availability": row["availability"],
            "source_status": row["source_status"],
            "source_packet_sha256": row["source_packet_sha256"],
            "breaches": copy.deepcopy(row["breaches"]),
            "unavailable_reasons": list(row["unavailable_reasons"]),
        }
        for row in sources if row["name"] in contract["risk_sources"]
    ]
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "decision_id": unified_packet["decision_id"],
        "decision_date": unified_packet["decision_date"],
        "slot": unified_packet["slot"],
        "generated_at": generated_at,
        "status": "ACTION_RISK_PORTFOLIO_PRESENTED_NO_ACTION_AUTHORITY",
        "actions": actions,
        "risk_findings": risk_findings,
        "sources": sources,
        "summary": {
            "source_count": len(sources),
            "available_source_count": sum(row["availability"] == "AVAILABLE" for row in sources),
            "unavailable_source_count": sum(row["availability"] == "UNAVAILABLE" for row in sources),
            "risk_breach_source_count": sum(bool(row["breaches"]) for row in risk_findings),
            "action_category_count": len(actions),
            "evaluated_action_count": 0,
            "nothing_action": None,
        },
        "lineage": {
            "unified_decision_packet_sha256": unified_packet["packet_sha256"],
            "source_packet_sha256": {
                row["name"]: row["source_packet_sha256"] for row in sources
            },
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "RULE_PASS_FAIL_NOT_AUTHORIZED",
            "CASH_EXPOSURE_ACTION_NOT_AUTHORIZED",
            "HEDGE_AND_INVERSE_NOT_EVALUATED",
            "PORTFOLIO_POLICIES_PARTIALLY_UNRATIFIED",
            "EXIT_GENERATION_NOT_AUTHORIZED",
            "NOTHING_ACTION_NOT_INFERRED",
            "ORDER_GENERATION_NOT_AUTHORIZED",
            "PRODUCTION_NOT_AUTHORIZED",
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
        raise ActionRiskPortfolioSummaryError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(bundle_path: Path, generated_at: str, output_path: Path) -> int:
    try:
        bundle = _read_json(bundle_path)
        if not isinstance(bundle, dict) or set(bundle) != {"source_packets", "unavailable_reasons"}:
            raise ActionRiskPortfolioSummaryError("BUNDLE_FIELDS_MISMATCH")
        packet = build_summary(
            bundle["source_packets"], bundle["unavailable_reasons"], generated_at
        )
        write_json_atomic(output_path, packet)
        return 0
    except (ActionRiskPortfolioSummaryError, OSError, TypeError, ValueError) as exc:
        print(f"Action/risk/portfolio summary failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.bundle, args.generated_at, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
