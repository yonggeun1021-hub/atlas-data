#!/usr/bin/env python3
"""P6-06 fail-closed Defensive Action Decision readiness boundary.

This capability slice inventories and semantically validates the existing P6
guardrails plus P2-COM-02's Cross-Market Capital Flow Engine, which is now a
supported source (``P2_FLOW_ENGINE``).  P1 Regime Decision and P2-COM-03's
Flow Transition Ledger remain unavailable-only:

- ``P1_REGIME_DECISION``: P1-COM-05 has only merged common-aggregation PIT
  replay (see ``regime/decision_authority.py``); it is not yet a final,
  ratified, runtime-consumer-wired Regime decision, so this slot must not be
  promoted or relabeled onto that mechanism.
- ``P2_FLOW_LEDGER``: the P2-COM-03 ledger is an append-only history, not a
  single point-in-time decision packet -- it carries no top-level
  ``generated_at``/``as_of_date`` of its own, so binding it independently
  would require inventing an undefined timestamp-selection rule.  Its
  evidence is already read transitively into ``P2_FLOW_ENGINE``'s
  ``flow_candidates.transition``/``persistence`` fields.

Missing or unratified inputs are BLOCKED; they must never be rendered as
NO_ACTION or translated into an action, allocation, instrument, size, order,
Production, or trading authority.
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
CONTRACT_PATH = ROOT / "config" / "defensive_action_decision_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,159}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class DefensiveActionDecisionError(ValueError):
    """Fail-closed P6-06 contract or source violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DefensiveActionDecisionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _load_validator(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_VALIDATOR_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CASH_EXPOSURE = _load_validator(
    "atlas_cash_exposure_for_p606", "portfolio/cash_exposure_action.py"
)
HEDGE_ELIGIBILITY = _load_validator(
    "atlas_hedge_eligibility_for_p606",
    "portfolio/hedge_instrument_eligibility.py",
)
BEAR_HEDGE_BUDGET = _load_validator(
    "atlas_bear_hedge_budget_for_p606", "portfolio/bear_hedge_risk_budget.py"
)
LONG_SHORT_INVARIANT = _load_validator(
    "atlas_long_short_for_p606", "portfolio/long_short_invariant.py"
)
REGIME_INVERSE = _load_validator(
    "atlas_regime_inverse_for_p606", "portfolio/regime_inverse_invariant.py"
)
CAPITAL_FLOW_ENGINE = _load_validator(
    "atlas_capital_flow_engine_for_p606",
    "portfolio/capital_flow_posture_reference.py",
)
RUNTIME_REGIME_READINESS = _load_validator(
    "atlas_runtime_regime_readiness_for_p606",
    "regime/runtime_regime_readiness.py",
)

def p1_regime_decision_unavailable_reasons(readiness_packet: dict) -> list[str]:
    """Exact, machine-readable ``P1_REGIME_DECISION`` blockers.

    ``P1_REGIME_DECISION`` stays an unavailable-only slot: this returns the
    ``unavailable_reasons`` list for it, never a source packet.  The readiness
    packet is re-derived and byte-compared by its own validator first, so a
    caller cannot shorten the blocker list, flip
    ``runtime_decision_available``, or claim a Regime through this path.

    Deliberately excluded: the readiness packet's own ``packet_sha256``.  That
    hash covers ``regime_output/v1`` envelopes, which embed the caller's
    invocation ``generated_at`` (and per-axis ``age_seconds``), so carrying it
    here would inject invocation-time noise into every consumer that
    fingerprints this packet's semantic content.  The blockers below are
    derived from real coverage and pinned contract state instead, and are
    independently recomputable from the same envelopes.
    """
    try:
        reasons = RUNTIME_REGIME_READINESS.unavailable_reasons(readiness_packet)
    except (ValueError, RuntimeError) as exc:
        raise DefensiveActionDecisionError(
            f"P1_REGIME_READINESS_INVALID:{exc}"
        ) from exc
    return _reasons(sorted(set(reasons)), "UNAVAILABLE_REASONS_INVALID:P1_REGIME_DECISION")


class _CapitalFlowEngineSourceAdapter:
    """Bind P2-COM-02's own re-derivation validator into the P6-06 source shape.

    ``capital_flow_posture_reference.py`` calls its packet identity field
    ``payload_sha256`` and its checker ``validate_reference`` (it also takes a
    ``root``, since it re-reads real evidence files to rebuild and compare).
    Every other P6-06 source calls the same idea ``packet_sha256`` /
    ``validate_packet``.  This adapter only renames the identity field so the
    existing generic ``_validate_source`` path can consume it unchanged; it
    performs no additional check and invents no new semantics.  ``validate_reference``
    already re-derives the full packet from the real committed evidence and
    fails closed (``REFERENCE_REDERIVATION_MISMATCH``) on any tamper, which is
    at least as strict as the generic self-rehash check the other sources rely on.
    """

    @staticmethod
    def validate_packet(packet: dict) -> dict:
        checked = CAPITAL_FLOW_ENGINE.validate_reference(packet)
        checked = dict(checked)
        checked["packet_sha256"] = checked.pop("payload_sha256")
        return checked


SOURCE_VALIDATORS = {
    "CASH_EXPOSURE_US": CASH_EXPOSURE,
    "CASH_EXPOSURE_KOREA": CASH_EXPOSURE,
    "CASH_EXPOSURE_CRYPTO": CASH_EXPOSURE,
    "HEDGE_ELIGIBILITY": HEDGE_ELIGIBILITY,
    "BEAR_HEDGE_BUDGET": BEAR_HEDGE_BUDGET,
    "LONG_SHORT_INVARIANT": LONG_SHORT_INVARIANT,
    "INVERSE_US": REGIME_INVERSE,
    "INVERSE_KOREA": REGIME_INVERSE,
    "INVERSE_CRYPTO": REGIME_INVERSE,
    "P2_FLOW_ENGINE": _CapitalFlowEngineSourceAdapter,
}
EXPECTED_MARKETS = {
    "CASH_EXPOSURE_US": "US",
    "CASH_EXPOSURE_KOREA": "KR",
    "CASH_EXPOSURE_CRYPTO": "CRYPTO",
    "INVERSE_US": "US",
    "INVERSE_KOREA": "KR",
    "INVERSE_CRYPTO": "CRYPTO",
}


def _expected_contract() -> dict:
    source_order = [
        "P1_REGIME_DECISION",
        "P2_FLOW_ENGINE",
        "P2_FLOW_LEDGER",
        "CASH_EXPOSURE_US",
        "CASH_EXPOSURE_KOREA",
        "CASH_EXPOSURE_CRYPTO",
        "HEDGE_ELIGIBILITY",
        "BEAR_HEDGE_BUDGET",
        "LONG_SHORT_INVARIANT",
        "INVERSE_US",
        "INVERSE_KOREA",
        "INVERSE_CRYPTO",
    ]
    return {
        "schema_version": 1,
        "contract_version": "defensive_action_decision_readiness/1",
        "output_schema_version": "defensive_action_decision_readiness_packet/1",
        "scope": "ZERO_CAPITAL_DECISION_REVIEW",
        "decision_vocabulary": [
            "CASH_PRIORITY",
            "REDUCE_REVIEW",
            "HEDGE_REVIEW",
            "INVERSE_REVIEW",
            "NO_ACTION",
        ],
        "runtime_decision_status": "BLOCKED",
        "runtime_evaluation_status": "NOT_EVALUATED",
        "source_order": source_order,
        "unavailable_only_source_slots": [
            "P1_REGIME_DECISION",
            "P2_FLOW_LEDGER",
        ],
        "source_specs": {
            "BEAR_HEDGE_BUDGET": {
                "schema_version": "bear_hedge_budget_packet/2",
                "contract_version": "bear_hedge_risk_budget/1",
                "statuses": ["BEAR_HEDGE_BUDGET_SET_VALIDATED"],
            },
            "CASH_EXPOSURE_CRYPTO": {
                "schema_version": "cash_exposure_action_packet/1",
                "contract_version": "cash_exposure_action_boundary/1",
                "statuses": ["CASH_EXPOSURE_ACTION_NOT_EVALUATED"],
            },
            "CASH_EXPOSURE_KOREA": {
                "schema_version": "cash_exposure_action_packet/1",
                "contract_version": "cash_exposure_action_boundary/1",
                "statuses": ["CASH_EXPOSURE_ACTION_NOT_EVALUATED"],
            },
            "CASH_EXPOSURE_US": {
                "schema_version": "cash_exposure_action_packet/1",
                "contract_version": "cash_exposure_action_boundary/1",
                "statuses": ["CASH_EXPOSURE_ACTION_NOT_EVALUATED"],
            },
            "HEDGE_ELIGIBILITY": {
                "schema_version": "hedge_instrument_eligibility_packet/2",
                "contract_version": "hedge_instrument_eligibility/1",
                "statuses": ["ELIGIBILITY_REGISTRY_VALIDATED"],
            },
            "INVERSE_CRYPTO": {
                "schema_version": "regime_inverse_invariant_packet/1",
                "contract_version": "regime_inverse_invariant/1",
                "statuses": ["INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED"],
            },
            "INVERSE_KOREA": {
                "schema_version": "regime_inverse_invariant_packet/1",
                "contract_version": "regime_inverse_invariant/1",
                "statuses": ["INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED"],
            },
            "INVERSE_US": {
                "schema_version": "regime_inverse_invariant_packet/1",
                "contract_version": "regime_inverse_invariant/1",
                "statuses": ["INVARIANT_ENFORCED_INVERSE_NOT_EVALUATED"],
            },
            "LONG_SHORT_INVARIANT": {
                "schema_version": "long_short_invariant_packet/1",
                "contract_version": "long_short_invariant/1",
                "statuses": ["INVARIANT_ENFORCED_SHORT_NOT_EVALUATED"],
            },
            "P2_FLOW_ENGINE": {
                "schema_version": "capital_flow_posture_reference/v1",
                "contract_version": "capital_flow_posture_reference_policy/v1",
                "statuses": [
                    "REFERENCE_AVAILABLE",
                    "PARTIAL_REFERENCE_AVAILABLE",
                ],
            },
        },
        "invariants": [
            "ACTION_PROPOSAL_NEVER_IMPLIES_ORDER",
            "HEDGE_ELIGIBILITY_NEVER_IMPLIES_ACTION_PROPOSAL",
            "LONG_FAIL_NEVER_IMPLIES_SHORT_PASS",
            "MISSING_OR_UNEVALUATED_INPUT_NEVER_IMPLIES_NO_ACTION",
            "RISK_OFF_STRESS_NEVER_IMPLIES_AUTO_INVERSE_ORDER",
        ],
        "authority": {
            "readiness_inventory_only": True,
            "policy_evaluation_authorized": False,
            "strategy_eligibility_authorized": False,
            "defensive_action_authorized": False,
            "no_action_inference_authorized": False,
            "instrument_selection_authorized": False,
            "risk_budget_allocation_authorized": False,
            "target_exposure_authorized": False,
            "position_size_authorized": False,
            "action_proposal_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or value != expected:
        raise DefensiveActionDecisionError("CONTRACT_MISMATCH")
    if set(value["source_specs"]) != set(SOURCE_VALIDATORS):
        raise DefensiveActionDecisionError("CONTRACT_SOURCE_SPECS_MISMATCH")
    if set(value["source_order"]) != (
        set(value["unavailable_only_source_slots"]) | set(value["source_specs"])
    ):
        raise DefensiveActionDecisionError("CONTRACT_SOURCE_ORDER_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise DefensiveActionDecisionError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise DefensiveActionDecisionError(code) from exc
    if parsed.isoformat() != value:
        raise DefensiveActionDecisionError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise DefensiveActionDecisionError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise DefensiveActionDecisionError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise DefensiveActionDecisionError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DefensiveActionDecisionError(code)
    return value


def _reasons(value, code: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or value != sorted(set(value))
        or any(not isinstance(item, str) or REASON_RE.fullmatch(item) is None for item in value)
    ):
        raise DefensiveActionDecisionError(code)
    return list(value)


def _assert_source_authority_closed(name: str, authority) -> None:
    if not isinstance(authority, dict):
        raise DefensiveActionDecisionError(f"SOURCE_AUTHORITY_INVALID:{name}")
    for key, value in authority.items():
        is_order_key = key == "order_authorized" or key.endswith("_order_authorized")
        if (is_order_key or key in {"production_authorized", "trading_authorized"}) and value is not False:
            raise DefensiveActionDecisionError(f"SOURCE_AUTHORITY_EXPANDED:{name}:{key}")


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
        raise DefensiveActionDecisionError(f"SOURCE_IDENTITY_INVALID:{name}")
    validator = SOURCE_VALIDATORS[name]
    try:
        checked = validator.validate_packet(copy.deepcopy(packet))
    except ValueError as exc:
        raise DefensiveActionDecisionError(f"SOURCE_SEMANTIC_INVALID:{name}:{exc}") from exc
    _assert_source_authority_closed(name, checked.get("authority"))
    if name in EXPECTED_MARKETS and checked.get("market") != EXPECTED_MARKETS[name]:
        raise DefensiveActionDecisionError(f"SOURCE_MARKET_MISMATCH:{name}")
    digest = _sha(checked.get("packet_sha256"), f"SOURCE_PACKET_SHA_INVALID:{name}")
    evidence_date = None
    if "generated_at" in checked:
        source_time = _utc(checked.get("generated_at"), f"SOURCE_TIME_INVALID:{name}")
        if source_time > generated_at:
            raise DefensiveActionDecisionError(f"SOURCE_FROM_FUTURE:{name}")
        if source_time[:10] > as_of_date:
            raise DefensiveActionDecisionError(f"SOURCE_AFTER_AS_OF_DATE:{name}")
        evidence_date = source_time[:10]
    elif "as_of_date" in checked:
        evidence_date = _date(checked.get("as_of_date"), f"SOURCE_DATE_INVALID:{name}")
        if evidence_date > as_of_date:
            raise DefensiveActionDecisionError(f"SOURCE_AFTER_AS_OF_DATE:{name}")
    return {
        "name": name,
        "availability": "AVAILABLE",
        "source_status": checked["status"],
        "source_market": checked.get("market"),
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
        raise DefensiveActionDecisionError("SOURCE_PACKET_KEYS_MISMATCH")
    if not isinstance(unavailable_reasons, dict) or set(unavailable_reasons) != expected:
        raise DefensiveActionDecisionError("UNAVAILABLE_REASON_KEYS_MISMATCH")
    rows = []
    unavailable_only = set(contract["unavailable_only_source_slots"])
    for name in contract["source_order"]:
        packet = source_packets[name]
        reasons = unavailable_reasons[name]
        if name in unavailable_only and packet is not None:
            raise DefensiveActionDecisionError(f"SOURCE_PACKET_NOT_YET_SUPPORTED:{name}")
        if packet is None:
            rows.append({
                "name": name,
                "availability": "UNAVAILABLE",
                "source_status": None,
                "source_market": None,
                "evidence_date": None,
                "source_packet_sha256": None,
                "unavailable_reasons": _reasons(
                    reasons, f"UNAVAILABLE_REASONS_INVALID:{name}"
                ),
            })
        else:
            if reasons != []:
                raise DefensiveActionDecisionError(
                    f"AVAILABLE_SOURCE_HAS_REASONS:{name}"
                )
            rows.append(
                _validate_source(name, packet, as_of_date, generated_at, contract)
            )
    return rows


DECISION_SOURCES = {
    "CASH_PRIORITY": [
        "P1_REGIME_DECISION", "P2_FLOW_ENGINE", "P2_FLOW_LEDGER",
        "CASH_EXPOSURE_US", "CASH_EXPOSURE_KOREA", "CASH_EXPOSURE_CRYPTO",
        "BEAR_HEDGE_BUDGET",
    ],
    "REDUCE_REVIEW": [
        "P1_REGIME_DECISION", "P2_FLOW_ENGINE", "P2_FLOW_LEDGER",
        "CASH_EXPOSURE_US", "CASH_EXPOSURE_KOREA", "CASH_EXPOSURE_CRYPTO",
        "BEAR_HEDGE_BUDGET",
    ],
    "HEDGE_REVIEW": [
        "P1_REGIME_DECISION", "P2_FLOW_ENGINE", "P2_FLOW_LEDGER",
        "HEDGE_ELIGIBILITY", "BEAR_HEDGE_BUDGET", "LONG_SHORT_INVARIANT",
    ],
    "INVERSE_REVIEW": [
        "P1_REGIME_DECISION", "P2_FLOW_ENGINE", "P2_FLOW_LEDGER",
        "HEDGE_ELIGIBILITY", "BEAR_HEDGE_BUDGET", "INVERSE_US",
        "INVERSE_KOREA", "INVERSE_CRYPTO",
    ],
    "NO_ACTION": [],
}


def _decision_rows(sources: list[dict], contract: dict) -> list[dict]:
    by_name = {row["name"]: row for row in sources}
    rows = []
    for decision in contract["decision_vocabulary"]:
        names = DECISION_SOURCES[decision] or contract["source_order"]
        reasons = ["DEFENSIVE_ACTION_POLICY_NOT_RATIFIED"]
        reasons.extend(
            f"SOURCE_UNAVAILABLE:{name}"
            for name in names
            if by_name[name]["availability"] == "UNAVAILABLE"
        )
        if decision == "NO_ACTION":
            reasons.append("MISSING_OR_UNEVALUATED_INPUT_IS_NOT_NO_ACTION")
        rows.append({
            "decision": decision,
            "evaluation_status": contract["runtime_evaluation_status"],
            "eligible": None,
            "review_proposal": None,
            "evidence_packet_sha256": [
                by_name[name]["source_packet_sha256"]
                for name in names
                if by_name[name]["availability"] == "AVAILABLE"
            ],
            "reasons": sorted(set(reasons)),
        })
    return rows


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
        raise DefensiveActionDecisionError("GENERATED_BEFORE_AS_OF_DATE")
    if policy_packet is not None:
        raise DefensiveActionDecisionError("UNRATIFIED_POLICY_PACKET_FORBIDDEN")
    sources = _source_rows(
        source_packets, unavailable_reasons, as_of, generated, contract
    )
    decisions = _decision_rows(sources, contract)
    unavailable = [row["name"] for row in sources if row["availability"] == "UNAVAILABLE"]
    return {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "as_of_date": as_of,
        "generated_at": generated,
        "scope": contract["scope"],
        "status": "DEFENSIVE_ACTION_READINESS_BLOCKED",
        "decision_status": contract["runtime_decision_status"],
        "decisions": decisions,
        "selected_action": None,
        "release_conditions": [
            "P1_REGIME_DECISION_POLICY_RATIFIED_AND_CONNECTED",
            "P2_FLOW_LEDGER_CONNECTED",
            "P6_DEFENSIVE_ACTION_POLICY_RATIFIED",
            "ALL_SOURCE_MARKET_AND_TIME_KEYS_ALIGNED",
            "INDEPENDENT_ACTION_RISK_CHECKS_AUTHORIZED",
        ],
        "risk_budget_allocation": None,
        "target_exposures": None,
        "selected_instrument": None,
        "position_size": None,
        "action_proposal": None,
        "order_intents": [],
        "policy_packet": None,
        "sources": sources,
        "summary": {
            "source_count": len(sources),
            "available_source_count": len(sources) - len(unavailable),
            "unavailable_source_count": len(unavailable),
            "unavailable_sources": unavailable,
            "decision_count": len(decisions),
            "evaluated_decision_count": 0,
            "eligible_decision_count": 0,
            "no_action": None,
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
            *(
                f"{name}_UNAVAILABLE"
                for name in contract["unavailable_only_source_slots"]
            ),
            "DEFENSIVE_ACTION_POLICY_NOT_RATIFIED",
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
        "scope", "status", "decision_status", "decisions", "selected_action",
        "release_conditions", "risk_budget_allocation", "target_exposures",
        "selected_instrument", "position_size", "action_proposal", "order_intents",
        "policy_packet", "sources", "summary", "source_packets",
        "unavailable_reasons", "lineage", "invariants", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise DefensiveActionDecisionError("OUTPUT_FIELDS_MISMATCH")
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
        raise DefensiveActionDecisionError("OUTPUT_DERIVATION_MISMATCH")
    if payload_sha256(expected) != digest:
        raise DefensiveActionDecisionError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise DefensiveActionDecisionError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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
            raise DefensiveActionDecisionError("BUNDLE_FIELDS_MISMATCH")
        packet = build_packet(
            bundle["source_packets"],
            bundle["unavailable_reasons"],
            as_of_date,
            generated_at,
            policy_packet=bundle["policy_packet"],
        )
        write_json_atomic(output_path, packet)
        return 0
    except (DefensiveActionDecisionError, OSError, TypeError, ValueError) as exc:
        print(f"Defensive action readiness failed: {exc}")
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
