#!/usr/bin/env python3
"""Deterministic, zero-transport US market-judgement receipt.

This module validates exact source/policy pins, source time and TTL, source
coverage, the finished-session boundary, and all five Regime axes.  The
current repository policy facts intentionally produce 0/5 coverage and a
fail-closed UNKNOWN/HOLD.  It cannot emit PASS, BUY, an order, or a ledger
mutation.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import minimum_coverage  # noqa: E402
from regime import output_contract  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "us_market_judgement_contract.json"
SCHEMA_PATH = ROOT / "schemas" / "us_market_judgement_input.schema.json"
LEADERSHIP_POLICY_PATH = ROOT / "config" / "us_leadership_policy.json"
UNIVERSE_POLICY_PATH = ROOT / "config" / "us_leadership_universe_policy.json"
BREADTH_POLICY_PATH = ROOT / "config" / "us_breadth_forward_contract.json"
REGIME_OUTPUT_PATH = ROOT / "config" / "regime_output_contract.json"
MINIMUM_COVERAGE_PATH = ROOT / "config" / "regime_minimum_coverage_policy.json"

INPUT_SCHEMA_VERSION = "us_market_judgement_input/1"
RECEIPT_SCHEMA_VERSION = "us_market_judgement_receipt/1"
CONTRACT_VERSION = "us_market_judgement/1"
BRIDGE_SCHEMA_VERSION = "us_market_judgement_bridge_projection/1"
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class USMarketJudgementError(RuntimeError):
    """Fail-closed contract, input, receipt, or persistence violation."""


def fail(code: str, detail: str = "") -> None:
    raise USMarketJudgementError(f"{code}:{detail}" if detail else code)


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("JSON_INVALID", f"{path}:{exc}")


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        fail("FILE_HASH_FAILED", f"{path}:{exc}")
    return digest.hexdigest()


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail("TIMESTAMP_INVALID", label)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        fail("TIMESTAMP_INVALID", label)


def _parse_date(value: object, label: str) -> dt.date:
    if not isinstance(value, str):
        fail("DATE_INVALID", label)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail("DATE_INVALID", label)
    if parsed.isoformat() != value:
        fail("DATE_INVALID", label)
    return parsed


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(path)
    expected_fields = {
        "schema_version", "contract_version", "input_schema_version",
        "receipt_schema_version", "market", "market_timezone", "source_order",
        "axis_source_bindings", "axis_prerequisite_sources", "source_statuses",
        "policy_statuses", "coverage_statuses", "session_statuses",
        "required_axis_count", "required_policy_status",
        "required_coverage_status", "required_finished_session_status",
        "current_policy_facts", "fail_closed", "consumer_pins", "authority",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        fail("CONTRACT_INVALID", "fields")
    pinned = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "market": "US",
        "market_timezone": "America/New_York",
        "source_order": [
            "US_UNIVERSE", "US_BREADTH", "US_SECTOR_LEADERSHIP",
            "US_PRICE_BREADTH", "US_MARKET_FLOW", "US_TREND", "US_RISK_VOL",
            "US_FINISHED_SESSION",
        ],
        "axis_source_bindings": {
            "TREND": "US_TREND",
            "BREADTH": "US_PRICE_BREADTH",
            "RISK_VOL": "US_RISK_VOL",
            "LIQUIDITY": "US_MARKET_FLOW",
            "LEADERSHIP": "US_SECTOR_LEADERSHIP",
        },
        "axis_prerequisite_sources": [
            "US_UNIVERSE", "US_BREADTH", "US_FINISHED_SESSION"
        ],
        "required_axis_count": 5,
        "required_policy_status": "RATIFIED",
        "required_coverage_status": "COVERAGE_MET",
        "required_finished_session_status": "FINISHED",
        "current_policy_facts": {
            "leadership_policy_version": "us_leadership/draft-v1",
            "leadership_policy_status": "UNRATIFIED",
            "universe_policy_version": "us_leadership_universe/draft-v1",
            "universe_policy_status": "UNRATIFIED",
            "price_breadth_authorized": False,
            "regime_classification_authorized": False,
        },
        "fail_closed": {
            "judgement": "UNKNOWN", "status": "HOLD", "recommendation": "WAIT",
            "action": None, "regime": "UNKNOWN", "direction": "UNKNOWN",
            "confidence": None,
        },
    }
    for key, expected in pinned.items():
        if value.get(key) != expected:
            fail("CONTRACT_INVALID", key)
    if value["source_statuses"] != ["AVAILABLE", "MISSING", "UNKNOWN"]:
        fail("CONTRACT_INVALID", "source_statuses")
    if value["policy_statuses"] != [
        "RATIFIED", "UNRATIFIED", "ABSENT", "NOT_AUTHORIZED"
    ]:
        fail("CONTRACT_INVALID", "policy_statuses")
    if value["coverage_statuses"] != ["COVERAGE_MET", "INCOMPLETE", "UNKNOWN"]:
        fail("CONTRACT_INVALID", "coverage_statuses")
    if value["session_statuses"] != ["FINISHED", "IN_PROGRESS", "UNKNOWN"]:
        fail("CONTRACT_INVALID", "session_statuses")
    authority = value["authority"]
    if authority.get("paper_observation_only") is not True:
        fail("CONTRACT_INVALID", "paper_observation_only")
    if any(item is not False for key, item in authority.items() if key != "paper_observation_only"):
        fail("CONTRACT_INVALID", "authority")
    return copy.deepcopy(value)


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    value = _read_json(path)
    if not isinstance(value, dict) or value.get("$schema") != (
        "https://json-schema.org/draft/2020-12/schema"
    ):
        fail("SCHEMA_INVALID")
    return copy.deepcopy(value)


def _policy_checks(contract: dict) -> list[dict]:
    leadership = _read_json(LEADERSHIP_POLICY_PATH)
    universe = _read_json(UNIVERSE_POLICY_PATH)
    breadth = _read_json(BREADTH_POLICY_PATH)
    regime_contract = _read_json(REGIME_OUTPUT_PATH)
    minimum = _read_json(MINIMUM_COVERAGE_PATH)
    facts = contract["current_policy_facts"]
    if (
        leadership.get("policy_version") != facts["leadership_policy_version"]
        or leadership.get("approval_status") != facts["leadership_policy_status"]
    ):
        fail("CURRENT_POLICY_FACT_DRIFT", "US_LEADERSHIP")
    if (
        universe.get("policy_version") != facts["universe_policy_version"]
        or universe.get("approval_status") != facts["universe_policy_status"]
    ):
        fail("CURRENT_POLICY_FACT_DRIFT", "US_UNIVERSE")
    if breadth.get("price_breadth_authorized") is not facts["price_breadth_authorized"]:
        fail("CURRENT_POLICY_FACT_DRIFT", "US_PRICE_BREADTH")
    classification_authorized = any(
        value != "UNKNOWN" for value in regime_contract.get("runtime_authorized_regimes", [])
    )
    if classification_authorized is not facts["regime_classification_authorized"]:
        fail("CURRENT_POLICY_FACT_DRIFT", "REGIME_CLASSIFICATION")
    minimum_coverage.validate_contract(minimum)
    return [
        {
            "policyId": "US_LEADERSHIP_POLICY",
            "version": leadership["policy_version"],
            "status": leadership["approval_status"],
            "satisfied": False,
            "blocker": "US_LEADERSHIP_POLICY_UNRATIFIED",
            "pin": {"ref": "config/us_leadership_policy.json", "sha256": file_sha256(LEADERSHIP_POLICY_PATH)},
        },
        {
            "policyId": "US_UNIVERSE_POLICY",
            "version": universe["policy_version"],
            "status": universe["approval_status"],
            "satisfied": False,
            "blocker": "US_UNIVERSE_POLICY_UNRATIFIED",
            "pin": {"ref": "config/us_leadership_universe_policy.json", "sha256": file_sha256(UNIVERSE_POLICY_PATH)},
        },
        {
            "policyId": "US_PRICE_BREADTH_AUTHORITY",
            "version": "us_breadth_forward_contract/1",
            "status": "NOT_AUTHORIZED",
            "satisfied": False,
            "blocker": "US_PRICE_BREADTH_NOT_AUTHORIZED",
            "pin": {"ref": "config/us_breadth_forward_contract.json", "sha256": file_sha256(BREADTH_POLICY_PATH)},
        },
        {
            "policyId": "REGIME_MINIMUM_COVERAGE",
            "version": minimum["contract_version"],
            "status": minimum["policy_status"],
            "satisfied": True,
            "blocker": None,
            "pin": {"ref": "config/regime_minimum_coverage_policy.json", "sha256": file_sha256(MINIMUM_COVERAGE_PATH)},
        },
        {
            "policyId": "REGIME_CLASSIFICATION",
            "version": regime_contract["contract_version"],
            "status": "NOT_AUTHORIZED",
            "satisfied": False,
            "blocker": "REGIME_CLASSIFICATION_NOT_AUTHORIZED",
            "pin": {"ref": "config/regime_output_contract.json", "sha256": file_sha256(REGIME_OUTPUT_PATH)},
        },
    ]


def _resolve_pin(value: object, label: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"ref", "sha256"}:
        fail("PIN_INVALID", f"{label}.fields")
    ref, claimed = value["ref"], value["sha256"]
    if ref is None and claimed is None:
        return {"ref": None, "sha256": None, "verified": False, "reason": "PIN_MISSING"}
    if (
        not isinstance(ref, str) or not ref
        or not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None
    ):
        fail("PIN_INVALID", label)
    path = Path(ref)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        return {"ref": ref, "sha256": claimed, "verified": False, "reason": "PIN_FILE_MISSING"}
    actual = file_sha256(path)
    if actual != claimed:
        return {"ref": ref, "sha256": claimed, "verified": False, "reason": f"PIN_HASH_MISMATCH:{actual}"}
    return {"ref": ref, "sha256": claimed, "verified": True, "reason": "PIN_HASH_VERIFIED"}


def _validate_source(
    value: object,
    source_id: str,
    evaluation: dt.datetime,
    session_date: dt.date,
    contract: dict,
) -> dict:
    fields = {
        "sourceId", "status", "observationDate", "sourceTime", "ttlSeconds",
        "pin", "policy", "coverage", "sessionStatus",
    }
    if not isinstance(value, dict) or set(value) != fields or value.get("sourceId") != source_id:
        fail("SOURCE_INVALID", f"{source_id}.fields")
    status = value["status"]
    if status not in contract["source_statuses"]:
        fail("SOURCE_INVALID", f"{source_id}.status")
    source_pin = _resolve_pin(value["pin"], f"{source_id}.pin")
    policy = value["policy"]
    if not isinstance(policy, dict) or set(policy) != {"version", "approvalStatus", "pin"}:
        fail("SOURCE_INVALID", f"{source_id}.policy")
    if policy["approvalStatus"] not in contract["policy_statuses"]:
        fail("SOURCE_INVALID", f"{source_id}.policy.status")
    if policy["version"] is not None and (
        not isinstance(policy["version"], str) or not policy["version"]
    ):
        fail("SOURCE_INVALID", f"{source_id}.policy.version")
    policy_pin = _resolve_pin(policy["pin"], f"{source_id}.policy.pin")
    coverage = value["coverage"]
    if not isinstance(coverage, dict) or set(coverage) != {
        "status", "observedCount", "requiredCount"
    }:
        fail("SOURCE_INVALID", f"{source_id}.coverage")
    if coverage["status"] not in contract["coverage_statuses"]:
        fail("SOURCE_INVALID", f"{source_id}.coverage.status")
    observed, required = coverage["observedCount"], coverage["requiredCount"]
    if (observed is None) != (required is None):
        fail("SOURCE_INVALID", f"{source_id}.coverage.counts")
    if observed is not None and (
        type(observed) is not int or type(required) is not int
        or observed < 0 or required < 1 or observed > required
    ):
        fail("SOURCE_INVALID", f"{source_id}.coverage.counts")
    expected_coverage_status = (
        "COVERAGE_MET" if observed is not None and observed == required
        else "INCOMPLETE" if observed is not None else "UNKNOWN"
    )
    if coverage["status"] != expected_coverage_status:
        fail("SOURCE_INVALID", f"{source_id}.coverage.derivation")
    session_status = value["sessionStatus"]
    if source_id == "US_FINISHED_SESSION":
        if session_status not in contract["session_statuses"]:
            fail("SOURCE_INVALID", f"{source_id}.sessionStatus")
    elif session_status is not None:
        fail("SOURCE_INVALID", f"{source_id}.sessionStatus")

    reasons: list[str] = []
    observation = None
    source_time = None
    expires_at = None
    fresh = False
    if status != "AVAILABLE":
        reasons.append(f"{source_id}_{status}")
        if any(value[key] is not None for key in ("observationDate", "sourceTime", "ttlSeconds")):
            fail("SOURCE_INVALID", f"{source_id}.unavailable_has_time")
        if source_pin["ref"] is not None:
            fail("SOURCE_INVALID", f"{source_id}.unavailable_has_pin")
    else:
        observation = _parse_date(value["observationDate"], f"{source_id}.observationDate")
        source_time = _parse_utc(value["sourceTime"], f"{source_id}.sourceTime")
        ttl = value["ttlSeconds"]
        if type(ttl) is not int or ttl < 1:
            fail("SOURCE_INVALID", f"{source_id}.ttlSeconds")
        expires_at = source_time + dt.timedelta(seconds=ttl)
        if observation > session_date:
            reasons.append(f"{source_id}_OBSERVATION_FROM_FUTURE")
        if source_time > evaluation:
            reasons.append(f"{source_id}_SOURCE_TIME_FROM_FUTURE")
        elif evaluation >= expires_at:
            reasons.append(f"{source_id}_TTL_EXPIRED")
        else:
            fresh = True
        if not source_pin["verified"]:
            reasons.append(f"{source_id}_{source_pin['reason'].split(':', 1)[0]}")
    if policy["approvalStatus"] != contract["required_policy_status"]:
        reasons.append(f"{source_id}_POLICY_{policy['approvalStatus']}")
    if policy["version"] is None or not policy_pin["verified"]:
        reasons.append(f"{source_id}_POLICY_PIN_NOT_VERIFIED")
    elif policy_pin["ref"] is not None:
        policy_path = Path(policy_pin["ref"])
        if not policy_path.is_absolute():
            policy_path = ROOT / policy_path
        policy_content = _read_json(policy_path)
        if not isinstance(policy_content, dict):
            reasons.append(f"{source_id}_POLICY_CONTENT_INVALID")
        else:
            content_version = policy_content.get(
                "policy_version", policy_content.get("contract_version")
            )
            content_status = policy_content.get(
                "approval_status", policy_content.get("policy_status")
            )
            if (
                content_version != policy["version"]
                or content_status != policy["approvalStatus"]
            ):
                reasons.append(f"{source_id}_POLICY_CONTENT_MISMATCH")
    if coverage["status"] != contract["required_coverage_status"]:
        reasons.append(f"{source_id}_COVERAGE_{coverage['status']}")
    if source_id == "US_FINISHED_SESSION":
        if session_status != contract["required_finished_session_status"]:
            reasons.append(f"US_FINISHED_SESSION_{session_status}")
        if observation is not None and observation != session_date:
            reasons.append("US_FINISHED_SESSION_DATE_MISMATCH")

    if source_id == "US_UNIVERSE":
        reasons.append("US_UNIVERSE_POLICY_UNRATIFIED")
    if source_id == "US_SECTOR_LEADERSHIP":
        reasons.append("US_LEADERSHIP_POLICY_UNRATIFIED")
    if source_id == "US_PRICE_BREADTH":
        reasons.append("US_PRICE_BREADTH_NOT_AUTHORIZED")
    reasons = list(dict.fromkeys(reasons))
    return {
        "sourceId": source_id,
        "status": status,
        "observationDate": value["observationDate"],
        "sourceTime": value["sourceTime"],
        "ttlSeconds": value["ttlSeconds"],
        "expiresAt": (
            expires_at.strftime("%Y-%m-%dT%H:%M:%SZ") if expires_at else None
        ),
        "fresh": fresh,
        "pin": source_pin,
        "policy": {
            "version": policy["version"],
            "approvalStatus": policy["approvalStatus"],
            "pin": policy_pin,
        },
        "coverage": copy.deepcopy(coverage),
        "sessionStatus": session_status,
        "qualified": not reasons,
        "reasons": reasons or ["SOURCE_QUALIFIED"],
    }


def validate_input(value: object, contract: Optional[dict] = None) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    fields = {"schemaVersion", "market", "evidenceClass", "evaluationAt", "sessionDate", "sources"}
    if not isinstance(value, dict) or set(value) != fields:
        fail("INPUT_INVALID", "fields")
    if value["schemaVersion"] != INPUT_SCHEMA_VERSION or value["market"] != "US":
        fail("INPUT_INVALID", "identity")
    if value["evidenceClass"] not in {
        "NATURAL_READ_ONLY", "SYNTHETIC_CONTRACT_TEST", "NO_INPUT_BASELINE"
    }:
        fail("INPUT_INVALID", "evidenceClass")
    evaluation = _parse_utc(value["evaluationAt"], "evaluationAt")
    session_date = _parse_date(value["sessionDate"], "sessionDate")
    sources = value["sources"]
    if not isinstance(sources, list) or [
        item.get("sourceId") if isinstance(item, dict) else None for item in sources
    ] != contract["source_order"]:
        fail("INPUT_INVALID", "source_order")
    normalized_sources = [
        _validate_source(row, source_id, evaluation, session_date, contract)
        for row, source_id in zip(sources, contract["source_order"])
    ]
    return {
        "schemaVersion": value["schemaVersion"],
        "market": value["market"],
        "evidenceClass": value["evidenceClass"],
        "evaluationAt": value["evaluationAt"],
        "sessionDate": value["sessionDate"],
        "sources": normalized_sources,
    }


def _bridge_projection(
    normalized: dict,
    source_checks: list[dict],
    regime_output: dict,
    coverage_gate: dict,
    blockers: list[str],
    contract: dict,
) -> dict:
    verified = [
        {"ref": row["pin"]["ref"], "sha256": row["pin"]["sha256"]}
        for row in source_checks if row["pin"]["verified"]
    ]
    qualified = [row for row in source_checks if row["qualified"]]
    source_timestamp = max((row["sourceTime"] for row in qualified), default=None)
    ttl_seconds = min((row["ttlSeconds"] for row in qualified), default=None)
    return {
        "schemaVersion": BRIDGE_SCHEMA_VERSION,
        "market": "US",
        "sourceTimestamp": source_timestamp,
        "ttlSeconds": ttl_seconds,
        "exactSources": verified,
        "marketJudgement": {
            "status": "HOLD",
            "judgement": "UNKNOWN",
            "regime": "UNKNOWN",
            "direction": "UNKNOWN",
            "confidence": None,
            "coverage": coverage_gate["coverage"],
            "blockers": blockers,
        },
        "leadership": {
            "transformVersion": "us_leadership/v1",
            "policyVersion": contract["current_policy_facts"]["leadership_policy_version"],
            "approvalStatus": "UNRATIFIED",
            "groupCoverageStatus": "UNRATIFIED",
            "observationStatus": None,
            "reason": "US_LEADERSHIP_POLICY_UNRATIFIED",
        },
        "lifecycleGate": {
            "gateId": "MARKET_JUDGEMENT",
            "status": None,
            "reason": blockers[0],
            "sources": verified,
        },
        "recommendation": "WAIT",
        "action": None,
        "evidenceClass": normalized["evidenceClass"],
        "regimeOutputSha256": payload_sha256(regime_output),
    }


def build_receipt(value: object, contract: Optional[dict] = None) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    normalized = validate_input(value, contract)
    policy_checks = _policy_checks(contract)
    source_checks = normalized["sources"]
    by_id = {row["sourceId"]: row for row in source_checks}
    prerequisites = contract["axis_prerequisite_sources"]
    axis_checks = []
    factors = {}
    for axis, source_id in contract["axis_source_bindings"].items():
        reasons = []
        if not by_id[source_id]["qualified"]:
            reasons.extend(by_id[source_id]["reasons"])
        for prerequisite in prerequisites:
            if not by_id[prerequisite]["qualified"]:
                reasons.append(f"{axis}_PREREQUISITE_{prerequisite}_NOT_QUALIFIED")
        reasons = list(dict.fromkeys(reasons))
        axis_checks.append({
            "axis": axis,
            "sourceId": source_id,
            "status": "UNDEFINED" if reasons else "DEFINED",
            "reasons": reasons or ["AXIS_EVIDENCE_QUALIFIED"],
        })
        if reasons:
            factors[axis] = {
                "status": "UNDEFINED",
                "warnings": [f"US_{axis}_INPUT_NOT_QUALIFIED"],
            }
        else:
            source = by_id[source_id]
            factors[axis] = {
                "status": "DEFINED",
                "observation_date": source["observationDate"],
                "available_at": source["sourceTime"],
                "transform_version": f"us_market_judgement_{axis.lower()}/v1",
                "evidence": {
                    "uri": source["pin"]["ref"],
                    "sha256": source["pin"]["sha256"],
                },
                "warnings": ["REGIME_INTERPRETATION_UNAUTHORIZED"],
            }
    regime = output_contract.build_unknown_output(
        "US", normalized["evaluationAt"], factors=factors
    )
    coverage_gate = minimum_coverage.evaluate_minimum_coverage(regime)
    blockers = []
    for row in policy_checks:
        if row["blocker"] is not None:
            blockers.append(row["blocker"])
    for row in source_checks:
        if not row["qualified"]:
            blockers.extend(row["reasons"])
    for row in axis_checks:
        if row["status"] == "UNDEFINED":
            blockers.append(f"{row['axis']}_AXIS_UNDEFINED")
    blockers.extend(coverage_gate["reasons"])
    blockers = list(dict.fromkeys(blockers))
    projection = _bridge_projection(
        normalized, source_checks, regime, coverage_gate, blockers, contract
    )
    consumer_pins = {
        consumer: {
            **copy.deepcopy(spec),
            "sha256": payload_sha256(
                regime if consumer == "paper_12_4" else projection
            ),
        }
        for consumer, spec in contract["consumer_pins"].items()
    }
    payload = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "contractVersion": CONTRACT_VERSION,
        "market": "US",
        "evaluationAt": normalized["evaluationAt"],
        "sessionDate": normalized["sessionDate"],
        "evidenceClass": normalized["evidenceClass"],
        "inputSha256": payload_sha256(value),
        "implementationPins": {
            "contract": {
                "ref": "config/us_market_judgement_contract.json",
                "sha256": file_sha256(CONTRACT_PATH),
            },
            "inputSchema": {
                "ref": "schemas/us_market_judgement_input.schema.json",
                "sha256": file_sha256(SCHEMA_PATH),
            },
            "runtime": {
                "ref": "regime/us_market_judgement.py",
                "sha256": file_sha256(Path(__file__)),
            },
        },
        "status": "HOLD",
        "judgement": "UNKNOWN",
        "recommendation": "WAIT",
        "action": None,
        "sourceChecks": source_checks,
        "policyChecks": policy_checks,
        "axisChecks": axis_checks,
        "regimeOutput": regime,
        "coverageGate": coverage_gate,
        "paperDecisionBridgeProjection": projection,
        "consumerPins": consumer_pins,
        "blockers": blockers,
        "authority": copy.deepcopy(contract["authority"]),
    }
    payload["receiptSha256"] = payload_sha256(payload)
    return validate_receipt(payload, value, contract, _derivation_check=False)


def validate_receipt(
    receipt: object,
    source_input: object,
    contract: Optional[dict] = None,
    *,
    _derivation_check: bool = True,
) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    if not isinstance(receipt, dict):
        fail("RECEIPT_INVALID", "object")
    digest = receipt.get("receiptSha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        fail("RECEIPT_INVALID", "sha256")
    unhashed = copy.deepcopy(receipt)
    unhashed.pop("receiptSha256", None)
    if payload_sha256(unhashed) != digest:
        fail("RECEIPT_SHA_MISMATCH")
    if (
        receipt.get("schemaVersion") != RECEIPT_SCHEMA_VERSION
        or receipt.get("contractVersion") != CONTRACT_VERSION
        or receipt.get("market") != "US"
        or receipt.get("status") != "HOLD"
        or receipt.get("judgement") != "UNKNOWN"
        or receipt.get("recommendation") != "WAIT"
        or receipt.get("action") is not None
        or receipt.get("regimeOutput", {}).get("coverage", {}).get("ratio") != "0/5"
        or receipt.get("coverageGate", {}).get("gate_result") != "BLOCKED"
    ):
        fail("RECEIPT_FAIL_CLOSED_INVARIANT")
    if any(value is not False for key, value in receipt.get("authority", {}).items() if key != "paper_observation_only"):
        fail("RECEIPT_AUTHORITY_INVALID")
    if receipt.get("authority", {}).get("paper_observation_only") is not True:
        fail("RECEIPT_AUTHORITY_INVALID")
    projection = receipt.get("paperDecisionBridgeProjection")
    pins = receipt.get("consumerPins")
    if (
        not isinstance(pins, dict)
        or pins.get("paper_12_4", {}).get("sha256") != payload_sha256(receipt["regimeOutput"])
        or pins.get("paper_12_1", {}).get("sha256") != payload_sha256(projection)
    ):
        fail("RECEIPT_CONSUMER_PIN_INVALID")
    if payload_sha256(source_input) != receipt.get("inputSha256"):
        fail("RECEIPT_INPUT_PIN_INVALID")
    if _derivation_check:
        expected = build_receipt(source_input, contract)
        if canonical_bytes(receipt) != canonical_bytes(expected):
            fail("RECEIPT_DERIVATION_MISMATCH")
    return copy.deepcopy(receipt)


def missing_source(source_id: str) -> dict:
    return {
        "sourceId": source_id,
        "status": "MISSING",
        "observationDate": None,
        "sourceTime": None,
        "ttlSeconds": None,
        "pin": {"ref": None, "sha256": None},
        "policy": {
            "version": None,
            "approvalStatus": "ABSENT",
            "pin": {"ref": None, "sha256": None},
        },
        "coverage": {"status": "UNKNOWN", "observedCount": None, "requiredCount": None},
        "sessionStatus": "UNKNOWN" if source_id == "US_FINISHED_SESSION" else None,
    }


def build_no_input_baseline(evaluation_at: str, session_date: str) -> dict:
    contract = load_contract()
    return {
        "schemaVersion": INPUT_SCHEMA_VERSION,
        "market": "US",
        "evidenceClass": "NO_INPUT_BASELINE",
        "evaluationAt": evaluation_at,
        "sessionDate": session_date,
        "sources": [missing_source(source_id) for source_id in contract["source_order"]],
    }


def _atomic_write(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def persist_immutable_receipt(receipt: dict, directory: Path) -> tuple[Path, str]:
    digest = receipt["receiptSha256"]
    target = Path(directory) / f"{digest}.json"
    if target.exists():
        if target.read_bytes() != canonical_bytes(receipt) + b"\n":
            fail("IMMUTABLE_RECEIPT_CONFLICT", str(target))
        return target, "NO_CHANGE"
    _atomic_write(target, receipt)
    try:
        target.chmod(0o444)
    except OSError as exc:
        fail("RECEIPT_PERMISSION_FAILED", str(exc))
    return target, "CREATED"


def run(input_path: Path, output_path: Path, receipt_dir: Path) -> int:
    try:
        source_input = _read_json(input_path)
        receipt = build_receipt(source_input)
        receipt_path, disposition = persist_immutable_receipt(receipt, receipt_dir)
        _atomic_write(output_path, {
            "disposition": disposition,
            "receiptPath": str(receipt_path),
            "receiptSha256": receipt["receiptSha256"],
            "status": receipt["status"],
            "judgement": receipt["judgement"],
            "recommendation": receipt["recommendation"],
            "action": receipt["action"],
            "coverage": receipt["regimeOutput"]["coverage"]["ratio"],
            "consumerPins": receipt["consumerPins"],
            "blockers": receipt["blockers"],
        })
        return 0
    except (USMarketJudgementError, output_contract.OutputContractError, minimum_coverage.MinimumCoverageError, OSError) as exc:
        print(f"US market judgement failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a fail-closed US market-judgement receipt")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.out, args.receipt_dir)


if __name__ == "__main__":
    raise SystemExit(main())
