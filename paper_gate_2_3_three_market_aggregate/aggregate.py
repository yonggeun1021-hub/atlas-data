"""Exact-pinned Gate 2 Regime and Gate 3 Rotation aggregation.

The module consumes normalized descriptors of already-produced owner receipts.
It never reads a market source, recomputes a market score, ratifies a policy,
or creates PAPER/order authority.  Semantic failure is isolated to the market
whose source is missing or does not match its exact pin.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


PACKAGE_ROOT = Path(__file__).resolve().parent
INPUT_SCHEMA = "paper_gate_2_3_input/v1"
AGGREGATE_SCHEMA = "paper_gate_2_3_aggregate_receipt/v1"
MARKET_REGIME_SCHEMA = "paper_gate_2_market_regime_receipt/v1"
MARKET_ROTATION_SCHEMA = "paper_gate_3_market_rotation_receipt/v1"
HEADER_SCHEMA = "paper_gate_2_3_three_market_header/v1"
LEDGER_SCHEMA = "paper_gate_2_3_transition_ledger/v1"
MARKETS = ("KRX", "US", "CRYPTO")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

OWNER_DEPENDENCY = {
    "KRX": "PAPER_12_5_KRX_MARKET_JUDGEMENT",
    "US": "PAPER_12_6_US_MARKET_JUDGEMENT",
    "CRYPTO": "PAPER_12_11_CRYPTO_MARKET_JUDGEMENT",
}

AUTHORITY_FALSE = {
    "policy_ratification_authorized": False,
    "threshold_definition_authorized": False,
    "signed_direction_authorized": False,
    "regime_classification_authorized": False,
    "hysteresis_authorized": False,
    "rotation_authorized": False,
    "paper_authorized": False,
    "candidate_authorized": False,
    "strategy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "broker_authorized": False,
    "ledger_mutation_authorized": False,
    "real_authorized": False,
    "live_authorized": False,
    "real_capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

ZERO_EFFECTS = {
    "network_calls": 0,
    "credential_reads": 0,
    "http_get_calls": 0,
    "http_post_calls": 0,
    "oauth_calls": 0,
    "broker_calls": 0,
    "orders": 0,
    "cancels": 0,
    "account_mutations": 0,
    "ledger_mutations": 0,
    "runtime_mutations": 0,
    "portal_mutations": 0,
    "timer_installs": 0,
}


class AggregateError(ValueError):
    """Structural, pin, integrity, or replay validation error."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_exact_pins() -> dict[str, Any]:
    return json.loads((PACKAGE_ROOT / "exact_pins.v1.json").read_text(encoding="utf-8"))


EXACT_PINS = _load_exact_pins()
EXACT_PINS_SHA256 = file_sha256(PACKAGE_ROOT / "exact_pins.v1.json")
EXPECTED_EXACT_PINS_SHA256 = "5190d62fb60a9ddb9151fd42870b9f1274be9e2457c9f7c0d97ddc1e21b6c807"


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AggregateError(f"{label}:UTC_Z_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AggregateError(f"{label}:INVALID_TIMESTAMP") from exc
    if parsed.utcoffset() != timedelta(0):
        raise AggregateError(f"{label}:UTC_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _receipt_hash(value: Mapping[str, Any], field: str) -> str:
    row = copy.deepcopy(dict(value))
    claimed = row.pop(field, None)
    if not _is_sha(claimed) or canonical_sha256(row) != claimed:
        raise AggregateError(f"{field}:HASH_INVALID")
    return claimed


def validate_exact_pins(pins: Mapping[str, Any] = EXACT_PINS) -> None:
    if EXACT_PINS_SHA256 != EXPECTED_EXACT_PINS_SHA256:
        raise AggregateError("EXACT_PINS_FILE_SHA256_INVALID")
    row = dict(pins)
    if set(row) != {"schema_version", "mode", "dependencies", "authority"}:
        raise AggregateError("EXACT_PINS_KEYS_INVALID")
    if (
        row["schema_version"] != "paper_gate_2_3_exact_pins/v1"
        or row["mode"] != "READ_ONLY_EXACT_RECEIPT_PIN"
        or row["authority"] != {
            "policy_ratification_authorized": False,
            "threshold_definition_authorized": False,
            "paper_authorized": False,
            "strategy_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        }
    ):
        raise AggregateError("EXACT_PINS_BOUNDARY_INVALID")
    dependencies = row["dependencies"]
    expected_ids = {
        "PAPER_12_4_THREE_MARKET_REGIME",
        "PAPER_12_5_KRX_MARKET_JUDGEMENT",
        "PAPER_12_6_US_MARKET_JUDGEMENT",
        "PAPER_12_11_CRYPTO_MARKET_JUDGEMENT",
        "CRYPTO_SPOT_ADAPTER_PRIVATE_RELEASE",
        "COMMON_PAPER_CANDIDATE_FUNNEL_PUBLIC",
        "P2_COM_02_THREE_MARKET_FLOW_ROTATION",
    }
    if not isinstance(dependencies, dict) or set(dependencies) != expected_ids:
        raise AggregateError("EXACT_PINS_DEPENDENCY_SET_INVALID")
    for dependency_id, dependency in dependencies.items():
        if not isinstance(dependency, dict):
            raise AggregateError(f"{dependency_id}:PIN_OBJECT_REQUIRED")
        if COMMIT_RE.fullmatch(str(dependency.get("source_commit"))) is None:
            raise AggregateError(f"{dependency_id}:COMMIT_PIN_INVALID")
        for key, value in dependency.items():
            if key.endswith("sha256") and not _is_sha(value):
                raise AggregateError(f"{dependency_id}:{key}:SHA_INVALID")
        for value in dependency.get("receipt_pins", {}).values():
            if not _is_sha(value):
                raise AggregateError(f"{dependency_id}:RECEIPT_PIN_INVALID")
    for market, dependency_id in OWNER_DEPENDENCY.items():
        dependency = dependencies[dependency_id]
        if not _is_sha(dependency.get("receipt_sha256")):
            raise AggregateError(f"{market}:OWNER_RECEIPT_PIN_INVALID")
        facts = dependency.get("expected_facts")
        if not isinstance(facts, dict) or facts.get("judgement") != "UNKNOWN" or facts.get("disposition") != "HOLD":
            raise AggregateError(f"{market}:FAIL_CLOSED_FACTS_INVALID")
    private_release = dependencies["CRYPTO_SPOT_ADAPTER_PRIVATE_RELEASE"]
    public_funnel = dependencies["COMMON_PAPER_CANDIDATE_FUNNEL_PUBLIC"]
    if private_release.get("approved_public_commit") != public_funnel["source_commit"]:
        raise AggregateError("CRYPTO_PRIVATE_PUBLIC_PIN_MISMATCH")
    crypto_facts = dependencies[OWNER_DEPENDENCY["CRYPTO"]]["expected_facts"]
    if (
        crypto_facts.get("natural_candidate_count") != 8
        or crypto_facts.get("investment_paper_count") != 0
        or crypto_facts.get("candidate_evidence_gaps") != [
            "CRYPTO_REGIME_EVIDENCE_INCOMPLETE",
            "CRYPTO_RELATIVE_STRENGTH_EVIDENCE_INCOMPLETE",
            "CRYPTO_LIQUIDITY_EVIDENCE_INCOMPLETE",
            "CRYPTO_FOUR_COMPONENT_SCORE_EVIDENCE_INCOMPLETE",
        ]
    ):
        raise AggregateError("CRYPTO_RELEASE_FACTS_INVALID")


def _validate_input(value: Mapping[str, Any]) -> tuple[dict[str, Any], datetime]:
    row = dict(value)
    required = {
        "schema_version",
        "evaluation_id",
        "evaluated_at_utc",
        "exact_pins_sha256",
        "markets",
    }
    if set(row) != required or row.get("schema_version") != INPUT_SCHEMA:
        raise AggregateError("INPUT_SCHEMA_OR_KEYS_INVALID")
    if row["exact_pins_sha256"] != EXACT_PINS_SHA256:
        raise AggregateError("INPUT_EXACT_PINS_SHA256_INVALID")
    if not isinstance(row["evaluation_id"], str) or not row["evaluation_id"]:
        raise AggregateError("INPUT_EVALUATION_ID_INVALID")
    evaluated_at = _parse_utc(row["evaluated_at_utc"], "evaluated_at_utc")
    if not isinstance(row["markets"], list) or len(row["markets"]) > 3:
        raise AggregateError("INPUT_MARKETS_INVALID")
    names = [item.get("market") for item in row["markets"] if isinstance(item, dict)]
    if len(names) != len(row["markets"]) or len(set(names)) != len(names) or any(name not in MARKETS for name in names):
        raise AggregateError("INPUT_MARKET_SET_INVALID")
    return row, evaluated_at


def _source_result(
    market: str,
    source: Mapping[str, Any] | None,
    evaluated_at: datetime,
) -> dict[str, Any]:
    pin = EXACT_PINS["dependencies"][OWNER_DEPENDENCY[market]]
    expected_facts = pin["expected_facts"]
    blockers: list[str] = []
    if source is None:
        return {
            "source_status": "MISSING",
            "source_commit": pin["source_commit"],
            "owner_receipt_sha256": None,
            "facts": copy.deepcopy(expected_facts),
            "checks": {
                "exact_receipt_pin": "MISSING",
                "completed_bar": "UNKNOWN",
                "source_time": "UNKNOWN",
                "ttl_freshness": "UNKNOWN",
                "coverage": "UNKNOWN",
                "pit": "UNKNOWN",
                "signed_direction": "UNKNOWN",
                "hysteresis": "UNKNOWN",
            },
            "blockers": [f"{market}_OWNER_RECEIPT_MISSING"],
        }

    row = dict(source)
    if set(row) != {"market", "source_commit", "receipt_sha256", "facts"}:
        blockers.append("OWNER_RECEIPT_DESCRIPTOR_KEYS_INVALID")
    if row.get("market") != market:
        blockers.append("CROSS_MARKET_RECEIPT_CONTAMINATION")
    if row.get("source_commit") != pin["source_commit"]:
        blockers.append("SOURCE_COMMIT_PIN_MISMATCH")
    if row.get("receipt_sha256") != pin["receipt_sha256"]:
        blockers.append("OWNER_RECEIPT_SHA256_MISMATCH")
    facts = row.get("facts")
    if facts != expected_facts:
        blockers.append("OWNER_RECEIPT_FACTS_MISMATCH")
        facts = copy.deepcopy(expected_facts)
    else:
        facts = copy.deepcopy(facts)

    completed_bar = "PASS" if facts["completed_bar_status"] == "PASS" else "UNKNOWN"
    source_time = "UNKNOWN"
    parsed_source_time: datetime | None = None
    if facts["source_time_utc"] is not None:
        try:
            parsed_source_time = _parse_utc(facts["source_time_utc"], f"{market}.source_time")
            if parsed_source_time > evaluated_at:
                source_time = "FAIL"
                blockers.append("SOURCE_TIME_FROM_FUTURE")
            else:
                source_time = "PASS"
        except AggregateError:
            source_time = "FAIL"
            blockers.append("SOURCE_TIME_INVALID")

    ttl_freshness = "UNKNOWN"
    ttl_seconds = facts["ttl_seconds"]
    if isinstance(ttl_seconds, int) and ttl_seconds > 0 and parsed_source_time is not None:
        if parsed_source_time + timedelta(seconds=ttl_seconds) < evaluated_at:
            ttl_freshness = "FAIL"
            blockers.append("SOURCE_TTL_EXPIRED")
        else:
            ttl_freshness = "PASS"
    elif ttl_seconds is None:
        blockers.append("TTL_OR_FRESHNESS_POLICY_UNRATIFIED")
    else:
        blockers.append("TTL_INVALID")

    coverage_value = facts["coverage"]
    coverage = "PASS"
    if (
        facts["coverage_policy_status"] != "RATIFIED"
        or coverage_value["defined_count"] != coverage_value["required_count"]
    ):
        coverage = "FAIL"
        blockers.append("COVERAGE_POLICY_OR_COUNT_NOT_READY")

    natural = facts["evidence_origin"] == "NATURAL_READ_ONLY"
    pit = "PASS" if natural and completed_bar == "PASS" and source_time == "PASS" else "UNKNOWN"
    if pit != "PASS":
        blockers.append("PIT_ELIGIBILITY_NOT_PROVEN")

    signed_direction = "UNKNOWN"
    if facts["signed_direction_policy_status"] != "RATIFIED":
        blockers.append("SIGNED_DIRECTION_POLICY_UNRATIFIED")
    else:
        blockers.append("SIGNED_DIRECTION_VALUE_ABSENT")

    hysteresis = "UNKNOWN"
    if facts["hysteresis_policy_status"] != "RATIFIED":
        blockers.append("HYSTERESIS_POLICY_UNRATIFIED")
    else:
        blockers.append("HYSTERESIS_STATE_ABSENT")

    if facts["leadership_policy_status"] != "RATIFIED":
        blockers.append("LEADERSHIP_POLICY_UNRATIFIED")
    if facts["scoring_policy_status"] != "RATIFIED":
        blockers.append("REGIME_SCORING_POLICY_UNRATIFIED")
    if facts["rotation_policy_status"] != "RATIFIED":
        blockers.append("ROTATION_POLICY_UNRATIFIED")
    if facts["evidence_origin"] != "NATURAL_READ_ONLY":
        blockers.append("NATURAL_OWNER_RECEIPT_ABSENT")

    source_status = "REJECTED" if any(
        item in blockers
        for item in (
            "OWNER_RECEIPT_DESCRIPTOR_KEYS_INVALID",
            "CROSS_MARKET_RECEIPT_CONTAMINATION",
            "SOURCE_COMMIT_PIN_MISMATCH",
            "OWNER_RECEIPT_SHA256_MISMATCH",
            "OWNER_RECEIPT_FACTS_MISMATCH",
            "SOURCE_TIME_FROM_FUTURE",
            "SOURCE_TIME_INVALID",
        )
    ) else "VALIDATED_FAIL_CLOSED"
    return {
        "source_status": source_status,
        "source_commit": row.get("source_commit"),
        "owner_receipt_sha256": row.get("receipt_sha256") if _is_sha(row.get("receipt_sha256")) else None,
        "facts": facts,
        "checks": {
            "exact_receipt_pin": "PASS" if source_status == "VALIDATED_FAIL_CLOSED" else "FAIL",
            "completed_bar": completed_bar,
            "source_time": source_time,
            "ttl_freshness": ttl_freshness,
            "coverage": coverage,
            "pit": pit,
            "signed_direction": signed_direction,
            "hysteresis": hysteresis,
        },
        "blockers": sorted(set(blockers)),
    }


def _state_hash(value: Mapping[str, Any], excluded: set[str]) -> str:
    state = {key: copy.deepcopy(item) for key, item in value.items() if key not in excluded}
    return canonical_sha256(state)


def _regime_receipt(market: str, source: Mapping[str, Any], evaluated_at_utc: str) -> dict[str, Any]:
    blockers = list(source["blockers"])
    blockers.extend(["REGIME_CLASSIFICATION_NOT_AUTHORIZED", "PAPER_AUTHORITY_FALSE"])
    source_lineage = {
        "source_commit": source["source_commit"],
        "owner_receipt_sha256": source["owner_receipt_sha256"],
        "paper_12_4_receipt_sha256": EXACT_PINS["dependencies"]["PAPER_12_4_THREE_MARKET_REGIME"]["receipt_pins"][market],
    }
    candidate_connection = None
    if market == "CRYPTO":
        private_release = EXACT_PINS["dependencies"]["CRYPTO_SPOT_ADAPTER_PRIVATE_RELEASE"]
        public_funnel = EXACT_PINS["dependencies"]["COMMON_PAPER_CANDIDATE_FUNNEL_PUBLIC"]
        source_lineage.update({
            "crypto_adapter_private_merge": private_release["source_commit"],
            "common_funnel_public_commit": public_funnel["source_commit"],
        })
        candidate_connection = {
            "natural_candidate_count": source["facts"]["natural_candidate_count"],
            "investment_paper_count": source["facts"]["investment_paper_count"],
            "investment_paper_status": "BLOCKED",
            "evidence_gaps": copy.deepcopy(source["facts"]["candidate_evidence_gaps"]),
        }
        blockers.extend(source["facts"]["candidate_evidence_gaps"])
    receipt = {
        "schema_version": MARKET_REGIME_SCHEMA,
        "gate": 2,
        "market": market,
        "evaluated_at_utc": evaluated_at_utc,
        "source_status": source["source_status"],
        "source_lineage": source_lineage,
        "candidate_connection": candidate_connection,
        "validation": copy.deepcopy(source["checks"]),
        "coverage": copy.deepcopy(source["facts"]["coverage"]),
        "policy_status": {
            "leadership": source["facts"]["leadership_policy_status"],
            "coverage": source["facts"]["coverage_policy_status"],
            "scoring": source["facts"]["scoring_policy_status"],
            "signed_direction": source["facts"]["signed_direction_policy_status"],
            "hysteresis": source["facts"]["hysteresis_policy_status"],
        },
        "signed_direction": None,
        "hysteresis_state": None,
        "receipt_status": "WAIT",
        "regime": "UNKNOWN",
        "disposition": "HOLD",
        "blockers": sorted(set(blockers)),
        "authority": copy.deepcopy(AUTHORITY_FALSE),
    }
    receipt["state_sha256"] = _state_hash(receipt, {"evaluated_at_utc"})
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _rotation_receipt(
    market: str,
    source: Mapping[str, Any],
    regime: Mapping[str, Any],
    evaluated_at_utc: str,
) -> dict[str, Any]:
    declared = source["facts"]["declared_rotation_state"]
    receipt_status = declared if declared in {"PENDING", "DEGRADED"} else "PENDING"
    if source["source_status"] != "VALIDATED_FAIL_CLOSED":
        receipt_status = "PENDING"
    blockers = list(source["blockers"])
    blockers.extend([
        "REGIME_UNKNOWN",
        "SIGNED_DIRECTION_ABSENT",
        "HYSTERESIS_STATE_ABSENT",
        "ROTATION_READINESS_BLOCKED",
        "PAPER_AUTHORITY_FALSE",
    ])
    receipt = {
        "schema_version": MARKET_ROTATION_SCHEMA,
        "gate": 3,
        "market": market,
        "evaluated_at_utc": evaluated_at_utc,
        "source_lineage": {
            "regime_receipt_sha256": regime["receipt_sha256"],
            "owner_receipt_sha256": source["owner_receipt_sha256"],
            "p2_com_02_rotation_receipt_sha256": EXACT_PINS["dependencies"]["P2_COM_02_THREE_MARKET_FLOW_ROTATION"]["receipt_pins"][market],
        },
        "receipt_status": receipt_status,
        "rotation_readiness": "BLOCKED",
        "signed_direction": None,
        "hysteresis_state": None,
        "rotation_weights": None,
        "strategy": None,
        "disposition": "HOLD",
        "blockers": sorted(set(blockers)),
        "authority": copy.deepcopy(AUTHORITY_FALSE),
    }
    receipt["state_sha256"] = _state_hash(receipt, {"evaluated_at_utc"})
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _headers(regimes: list[dict[str, Any]], rotations: list[dict[str, Any]]) -> dict[str, Any]:
    regime_header = {
        "schema_version": HEADER_SCHEMA,
        "gate": 2,
        "status": "PENDING",
        "market_receipts": {item["market"]: item["receipt_sha256"] for item in regimes},
        "market_status": {item["market"]: item["receipt_status"] for item in regimes},
        "blocked_markets": [item["market"] for item in regimes if item["receipt_status"] != "READY"],
        "paper_disposition": "HOLD",
        "authority": copy.deepcopy(AUTHORITY_FALSE),
    }
    regime_header["state_sha256"] = canonical_sha256(regime_header)
    regime_header["header_sha256"] = canonical_sha256(regime_header)

    statuses = [item["receipt_status"] for item in rotations]
    rotation_status = "PENDING" if all(item == "PENDING" for item in statuses) else "DEGRADED"
    rotation_header = {
        "schema_version": HEADER_SCHEMA,
        "gate": 3,
        "status": rotation_status,
        "rotation_readiness": "BLOCKED",
        "market_receipts": {item["market"]: item["receipt_sha256"] for item in rotations},
        "market_status": {item["market"]: item["receipt_status"] for item in rotations},
        "blocked_markets": [item["market"] for item in rotations],
        "signed_direction": None,
        "hysteresis_state": None,
        "rotation_weights": None,
        "strategy": None,
        "paper_disposition": "HOLD",
        "authority": copy.deepcopy(AUTHORITY_FALSE),
    }
    rotation_header["state_sha256"] = canonical_sha256(rotation_header)
    rotation_header["header_sha256"] = canonical_sha256(rotation_header)
    return {"regime": regime_header, "rotation": rotation_header}


def _prior_states(previous: Mapping[str, Any] | None) -> dict[tuple[int, str], str]:
    if previous is None:
        return {}
    _validate_structure(previous)
    states: dict[tuple[int, str], str] = {}
    for entry in previous["transition_ledger"]["entries"]:
        states[(entry["gate"], entry["scope"])] = entry["current_state_sha256"]
    return states


def _transition_ledger(
    regimes: list[dict[str, Any]],
    rotations: list[dict[str, Any]],
    headers: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    prior = _prior_states(previous)
    rows: list[tuple[int, str, str, str]] = []
    for receipt in regimes:
        rows.append((2, receipt["market"], receipt["state_sha256"], receipt["receipt_sha256"]))
    rows.append((2, "THREE_MARKET", headers["regime"]["state_sha256"], headers["regime"]["header_sha256"]))
    for receipt in rotations:
        rows.append((3, receipt["market"], receipt["state_sha256"], receipt["receipt_sha256"]))
    rows.append((3, "THREE_MARKET", headers["rotation"]["state_sha256"], headers["rotation"]["header_sha256"]))
    entries: list[dict[str, Any]] = []
    previous_entry_sha256: str | None = None
    for gate, scope, current_state, source_receipt in rows:
        prior_state = prior.get((gate, scope))
        transition = "INITIAL" if prior_state is None else ("NO_CHANGE" if prior_state == current_state else "CHANGED")
        entry = {
            "gate": gate,
            "scope": scope,
            "transition": transition,
            "previous_state_sha256": prior_state,
            "current_state_sha256": current_state,
            "source_receipt_sha256": source_receipt,
            "previous_entry_sha256": previous_entry_sha256,
        }
        entry["entry_sha256"] = canonical_sha256(entry)
        previous_entry_sha256 = entry["entry_sha256"]
        entries.append(entry)
    ledger = {
        "schema_version": LEDGER_SCHEMA,
        "entry_count": len(entries),
        "entries": entries,
        "tail_sha256": previous_entry_sha256,
        "authority": {"audit_transition_authorized": True, "state_confirmation_authorized": False, "paper_authorized": False},
    }
    ledger["ledger_sha256"] = canonical_sha256(ledger)
    return ledger


def build_aggregate(
    input_bundle: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_exact_pins()
    root, evaluated_at = _validate_input(input_bundle)
    supplied = {item["market"]: item for item in root["markets"]}
    sources = {
        market: _source_result(market, supplied.get(market), evaluated_at)
        for market in MARKETS
    }
    regimes = [
        _regime_receipt(market, sources[market], root["evaluated_at_utc"])
        for market in MARKETS
    ]
    regime_by_market = {item["market"]: item for item in regimes}
    rotations = [
        _rotation_receipt(
            market,
            sources[market],
            regime_by_market[market],
            root["evaluated_at_utc"],
        )
        for market in MARKETS
    ]
    headers = _headers(regimes, rotations)
    ledger = _transition_ledger(regimes, rotations, headers, previous)
    aggregate = {
        "schema_version": AGGREGATE_SCHEMA,
        "evaluation_id": root["evaluation_id"],
        "evaluated_at_utc": root["evaluated_at_utc"],
        "exact_pins_sha256": EXACT_PINS_SHA256,
        "input_sha256": canonical_sha256(root),
        "source_lineage": {
            key: value["source_commit"]
            for key, value in EXACT_PINS["dependencies"].items()
        },
        "market_regime_receipts": regimes,
        "market_rotation_receipts": rotations,
        "headers": headers,
        "transition_ledger": ledger,
        "summary": {
            "market_order": list(MARKETS),
            "market_isolation": True,
            "three_market_regime_header": "PENDING",
            "rotation_discovery": headers["rotation"]["status"],
            "judgement": "UNKNOWN",
            "disposition": "HOLD",
            "candidate_state": "NONE",
            "crypto_natural_candidate_count": 8,
            "crypto_investment_paper_count": 0,
            "strategy_engine_count": 0,
            "live_engine_count": 0,
        },
        "effects": copy.deepcopy(ZERO_EFFECTS),
        "authority": copy.deepcopy(AUTHORITY_FALSE),
    }
    aggregate["aggregate_sha256"] = canonical_sha256(aggregate)
    _validate_structure(aggregate)
    return aggregate


def _validate_structure(value: Mapping[str, Any]) -> None:
    row = dict(value)
    required = {
        "schema_version", "evaluation_id", "evaluated_at_utc", "exact_pins_sha256",
        "input_sha256", "source_lineage", "market_regime_receipts",
        "market_rotation_receipts", "headers", "transition_ledger", "summary",
        "effects", "authority", "aggregate_sha256",
    }
    if set(row) != required or row["schema_version"] != AGGREGATE_SCHEMA:
        raise AggregateError("AGGREGATE_SCHEMA_OR_KEYS_INVALID")
    _parse_utc(row["evaluated_at_utc"], "aggregate.evaluated_at_utc")
    if row["exact_pins_sha256"] != EXACT_PINS_SHA256 or row["authority"] != AUTHORITY_FALSE or row["effects"] != ZERO_EFFECTS:
        raise AggregateError("AGGREGATE_BOUNDARY_INVALID")
    if [item.get("market") for item in row["market_regime_receipts"]] != list(MARKETS):
        raise AggregateError("REGIME_MARKET_ORDER_INVALID")
    if [item.get("market") for item in row["market_rotation_receipts"]] != list(MARKETS):
        raise AggregateError("ROTATION_MARKET_ORDER_INVALID")
    for receipt in row["market_regime_receipts"]:
        if (
            receipt.get("receipt_status") != "WAIT"
            or receipt.get("regime") != "UNKNOWN"
            or receipt.get("disposition") != "HOLD"
            or receipt.get("signed_direction") is not None
            or receipt.get("hysteresis_state") is not None
            or receipt.get("authority") != AUTHORITY_FALSE
        ):
            raise AggregateError(f"{receipt.get('market')}:REGIME_FAIL_CLOSED_INVALID")
        _receipt_hash(receipt, "receipt_sha256")
        state = copy.deepcopy(receipt)
        state.pop("receipt_sha256", None)
        claimed_state = state.pop("state_sha256", None)
        state.pop("evaluated_at_utc", None)
        if not _is_sha(claimed_state) or canonical_sha256(state) != claimed_state:
            raise AggregateError(f"{receipt.get('market')}:REGIME_STATE_HASH_INVALID")
    for receipt in row["market_rotation_receipts"]:
        if (
            receipt.get("receipt_status") not in {"PENDING", "DEGRADED"}
            or receipt.get("rotation_readiness") != "BLOCKED"
            or receipt.get("disposition") != "HOLD"
            or any(receipt.get(key) is not None for key in ("signed_direction", "hysteresis_state", "rotation_weights", "strategy"))
            or receipt.get("authority") != AUTHORITY_FALSE
        ):
            raise AggregateError(f"{receipt.get('market')}:ROTATION_FAIL_CLOSED_INVALID")
        _receipt_hash(receipt, "receipt_sha256")
        state = copy.deepcopy(receipt)
        state.pop("receipt_sha256", None)
        claimed_state = state.pop("state_sha256", None)
        state.pop("evaluated_at_utc", None)
        if not _is_sha(claimed_state) or canonical_sha256(state) != claimed_state:
            raise AggregateError(f"{receipt.get('market')}:ROTATION_STATE_HASH_INVALID")
    if row["headers"]["regime"].get("status") != "PENDING":
        raise AggregateError("REGIME_HEADER_PROMOTION_FORBIDDEN")
    rotation_header = row["headers"]["rotation"]
    if rotation_header.get("status") not in {"PENDING", "DEGRADED"} or rotation_header.get("rotation_readiness") != "BLOCKED":
        raise AggregateError("ROTATION_HEADER_PROMOTION_FORBIDDEN")
    for name, header in row["headers"].items():
        unsigned = copy.deepcopy(header)
        claimed_header = unsigned.pop("header_sha256", None)
        if not _is_sha(claimed_header) or canonical_sha256(unsigned) != claimed_header:
            raise AggregateError(f"{name.upper()}_HEADER_HASH_INVALID")
        claimed_state = unsigned.pop("state_sha256", None)
        if not _is_sha(claimed_state) or canonical_sha256(unsigned) != claimed_state:
            raise AggregateError(f"{name.upper()}_HEADER_STATE_HASH_INVALID")
    ledger = row["transition_ledger"]
    ledger_copy = copy.deepcopy(ledger)
    claimed_ledger = ledger_copy.pop("ledger_sha256", None)
    if not _is_sha(claimed_ledger) or canonical_sha256(ledger_copy) != claimed_ledger:
        raise AggregateError("TRANSITION_LEDGER_HASH_INVALID")
    if ledger.get("entry_count") != 8 or len(ledger.get("entries", [])) != 8:
        raise AggregateError("TRANSITION_LEDGER_ENTRY_COUNT_INVALID")
    previous_entry = None
    for entry in ledger["entries"]:
        if entry.get("previous_entry_sha256") != previous_entry:
            raise AggregateError("TRANSITION_LEDGER_CHAIN_INVALID")
        entry_copy = copy.deepcopy(entry)
        claimed_entry = entry_copy.pop("entry_sha256", None)
        if not _is_sha(claimed_entry) or canonical_sha256(entry_copy) != claimed_entry:
            raise AggregateError("TRANSITION_LEDGER_ENTRY_HASH_INVALID")
        previous_entry = claimed_entry
    if ledger.get("tail_sha256") != previous_entry:
        raise AggregateError("TRANSITION_LEDGER_TAIL_INVALID")
    expected_lineage = {
        key: dependency["source_commit"]
        for key, dependency in EXACT_PINS["dependencies"].items()
    }
    if row["source_lineage"] != expected_lineage:
        raise AggregateError("AGGREGATE_SOURCE_LINEAGE_INVALID")
    if row["summary"] != {
        "market_order": list(MARKETS),
        "market_isolation": True,
        "three_market_regime_header": "PENDING",
        "rotation_discovery": rotation_header["status"],
        "judgement": "UNKNOWN",
        "disposition": "HOLD",
        "candidate_state": "NONE",
        "crypto_natural_candidate_count": 8,
        "crypto_investment_paper_count": 0,
        "strategy_engine_count": 0,
        "live_engine_count": 0,
    }:
        raise AggregateError("AGGREGATE_SUMMARY_INVALID")
    _receipt_hash(row, "aggregate_sha256")


def validate_aggregate(
    aggregate: Mapping[str, Any],
    input_bundle: Mapping[str, Any],
    previous: Mapping[str, Any] | None = None,
) -> None:
    _validate_structure(aggregate)
    rebuilt = build_aggregate(input_bundle, previous)
    if canonical_bytes(rebuilt) != canonical_bytes(aggregate):
        raise AggregateError("AGGREGATE_REBUILD_MISMATCH")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")
