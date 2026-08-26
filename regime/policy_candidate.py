#!/usr/bin/env python3
"""Build a fail-closed evidence inventory for P1-COM-05 policy candidates.

This module deliberately does not classify a market.  It verifies whether every
output-affecting parameter in a draft Regime policy has an exact, point-in-time
eligible evidence record.  An evidence-complete candidate may enter replay, but
it cannot select, recommend, ratify, or run a policy.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "regime_policy_candidate_contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_:-]*$")
WARNING = re.compile(r"^[A-Z][A-Z0-9_]*$")
DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


PINNED_CONTRACT = {
    "schema_version": 1,
    "contract_version": "regime_policy_candidate_evidence/v1",
    "contract_mode": "SHADOW_DIAGNOSTIC_ONLY",
    "candidate_manifest_version": "regime_policy_candidate_manifest/v1",
    "evidence_document_version": "regime_policy_parameter_evidence/v1",
    "policy_status": "DRAFT_NOT_RATIFIED",
    "candidate_statuses": ["CANDIDATE_BLOCKED", "CANDIDATE_READY"],
    "parameter_statuses": ["BLOCKED", "SUPPORTED"],
    "required_components": [
        "MARKET_NORMALIZATION",
        "MINIMUM_COVERAGE",
        "REGIME_CLASSIFICATION",
        "DIRECTION",
        "CONFIDENCE",
        "STRESS_OVERRIDE",
        "INVALIDATION",
        "HYSTERESIS",
        "REPLAY_ACCEPTANCE",
    ],
    "value_types": [
        "UNSPECIFIED",
        "NUMBER",
        "BOOLEAN",
        "TEXT",
        "STRUCTURED",
    ],
    "evidence_kinds": [
        "EMPIRICAL_DISTRIBUTION",
        "HISTORICAL_EPISODE",
        "CIO_DOCTRINE",
        "EXTERNAL_RESEARCH",
        "UNSUPPORTED",
    ],
    "claim_types": [
        "EXPLICIT_PARAMETER_VALUE",
        "EMPIRICAL_STATISTIC",
        "HISTORICAL_EPISODE_RULE",
        "QUALITATIVE_PRINCIPLE",
        "UNSUPPORTED",
    ],
    "blocked_reason_codes": [
        "VALUE_UNSPECIFIED",
        "EVIDENCE_MISSING",
        "UNSUPPORTED_EVIDENCE",
        "FUTURE_EVIDENCE",
        "STALE_EVIDENCE",
        "PARAMETER_CLAIM_MISSING",
        "VALUE_MISMATCH",
        "QUALITATIVE_ONLY",
        "EVIDENCE_KIND_CLAIM_MISMATCH",
        "SINGLE_OBSERVATION_STATISTIC",
    ],
    "evidence_policy": {
        "numeric_or_boolean_requires_explicit_equal_value": True,
        "qualitative_principle_never_supports_parameter_value": True,
        "single_observation_statistic_never_supports_policy_value": True,
        "evidence_must_be_available_by_decision_at": True,
        "expired_evidence_is_stale": True,
        "every_required_component_must_be_supported_for_ready": True,
        "candidate_ready_does_not_select_recommend_or_ratify": True,
    },
    "authority": {
        "candidate_evidence_inventory_authorized": True,
        "candidate_selection_authorized": False,
        "policy_recommendation_authorized": False,
        "policy_ratification_authorized": False,
        "runtime_classification_authorized": False,
        "hysteresis_authorized": False,
        "strategy_eligibility_authorized": False,
        "stage_authorized": False,
        "buy_authorized": False,
        "action_authorized": False,
        "proposal_authorized": False,
        "order_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    },
}


class PolicyCandidateError(RuntimeError):
    """Fail-closed policy-candidate evidence error."""


def fail(code: str, detail: str) -> None:
    raise PolicyCandidateError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_json(path: Path, code: str = "JSON_INVALID") -> object:
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_json_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, f"{path}: {exc}")


def ensure_no_float(value: object, label: str = "input") -> None:
    if isinstance(value, float):
        fail("FLOAT_NOT_ALLOWED", label)
    if isinstance(value, dict):
        for key, item in value.items():
            ensure_no_float(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_no_float(item, f"{label}[{index}]")


def canonical_bytes(value: object) -> bytes:
    ensure_no_float(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))
    return encoded.encode("utf-8")


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def validate_contract(contract: object) -> dict:
    if canonical_bytes(contract) != canonical_bytes(PINNED_CONTRACT):
        fail("CONTRACT_INVALID", "schema or pinned fail-closed semantics")
    return contract


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(load_json(path, "CONTRACT_INVALID"))


def parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        fail("TIMESTAMP_INVALID", label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        fail("TIMESTAMP_INVALID", f"{label}: {exc}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("TIMESTAMP_INVALID", f"{label}: timezone required")
    return parsed


def validate_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        fail("IDENTIFIER_INVALID", label)
    return value


def validate_warning_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        fail("CAVEATS_INVALID", label)
    if any(
        not isinstance(item, str) or WARNING.fullmatch(item) is None
        for item in value
    ):
        fail("CAVEATS_INVALID", label)
    if len(value) != len(set(value)):
        fail("CAVEATS_INVALID", f"{label}: duplicate")
    return sorted(value)


def validate_parameter_value(value_type: str, value: object, label: str) -> None:
    if value_type == "UNSPECIFIED":
        if value is not None:
            fail("PARAMETER_VALUE_INVALID", f"{label}: unspecified must be null")
    elif value_type == "NUMBER":
        is_integer = isinstance(value, int) and not isinstance(value, bool)
        is_decimal = isinstance(value, str) and DECIMAL.fullmatch(value) is not None
        if not (is_integer or is_decimal):
            fail("PARAMETER_VALUE_INVALID", f"{label}: canonical number required")
    elif value_type == "BOOLEAN":
        if not isinstance(value, bool):
            fail("PARAMETER_VALUE_INVALID", f"{label}: boolean required")
    elif value_type == "TEXT":
        if not isinstance(value, str) or not value:
            fail("PARAMETER_VALUE_INVALID", f"{label}: non-empty text required")
    elif value_type == "STRUCTURED":
        if not isinstance(value, (dict, list)) or not value:
            fail("PARAMETER_VALUE_INVALID", f"{label}: non-empty object/list required")
    else:
        fail("PARAMETER_VALUE_INVALID", f"{label}: {value_type}")


def validate_evidence_ref(value: object, label: str) -> dict:
    expected = {"path", "sha256", "evidence_id"}
    if not isinstance(value, dict) or set(value) != expected:
        fail("EVIDENCE_REF_INVALID", f"{label}: schema")
    path = value["path"]
    if not isinstance(path, str) or not path or "\\" in path:
        fail("EVIDENCE_REF_INVALID", f"{label}: path")
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or pure.suffix != ".json":
        fail("EVIDENCE_REF_INVALID", f"{label}: unsafe path")
    if not isinstance(value["sha256"], str) or SHA256.fullmatch(value["sha256"]) is None:
        fail("EVIDENCE_REF_INVALID", f"{label}: sha256")
    validate_identifier(value["evidence_id"], f"{label}.evidence_id")
    return value


def validate_manifest(manifest: object, contract: dict) -> dict:
    ensure_no_float(manifest, "candidate_manifest")
    expected = {
        "schema_version",
        "contract_version",
        "candidate_id",
        "market",
        "decision_at",
        "policy_status",
        "parameters",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected:
        fail("CANDIDATE_MANIFEST_INVALID", "schema")
    if manifest["schema_version"] != 1:
        fail("CANDIDATE_MANIFEST_INVALID", "schema_version")
    if manifest["contract_version"] != contract["candidate_manifest_version"]:
        fail("CANDIDATE_MANIFEST_INVALID", "contract_version")
    validate_identifier(manifest["candidate_id"], "candidate_id")
    if manifest["market"] not in {"COMMON", "US", "KOREA", "CRYPTO"}:
        fail("CANDIDATE_MANIFEST_INVALID", "market")
    parse_timestamp(manifest["decision_at"], "decision_at")
    if manifest["policy_status"] != contract["policy_status"]:
        fail("CANDIDATE_MANIFEST_INVALID", "policy_status")
    if not isinstance(manifest["parameters"], list):
        fail("CANDIDATE_MANIFEST_INVALID", "parameters")

    parameters = {}
    parameter_ids = set()
    parameter_schema = {
        "component",
        "parameter_id",
        "value_type",
        "proposed_value",
        "evidence_refs",
    }
    for index, parameter in enumerate(manifest["parameters"]):
        label = f"parameters[{index}]"
        if not isinstance(parameter, dict) or set(parameter) != parameter_schema:
            fail("PARAMETER_INVALID", f"{label}: schema")
        component = parameter["component"]
        if component not in contract["required_components"]:
            fail("PARAMETER_INVALID", f"{label}: component")
        if component in parameters:
            fail("PARAMETER_INVALID", f"{label}: duplicate component")
        parameter_id = validate_identifier(parameter["parameter_id"], f"{label}.id")
        if parameter_id in parameter_ids:
            fail("PARAMETER_INVALID", f"{label}: duplicate parameter_id")
        value_type = parameter["value_type"]
        if value_type not in contract["value_types"]:
            fail("PARAMETER_INVALID", f"{label}: value_type")
        validate_parameter_value(value_type, parameter["proposed_value"], label)
        refs = parameter["evidence_refs"]
        if not isinstance(refs, list):
            fail("PARAMETER_INVALID", f"{label}: evidence_refs")
        validated_refs = [
            validate_evidence_ref(item, f"{label}.evidence_refs[{ref_index}]")
            for ref_index, item in enumerate(refs)
        ]
        ref_keys = [(item["path"], item["evidence_id"]) for item in validated_refs]
        if len(ref_keys) != len(set(ref_keys)):
            fail("PARAMETER_INVALID", f"{label}: duplicate evidence ref")
        parameters[component] = parameter
        parameter_ids.add(parameter_id)
    if set(parameters) != set(contract["required_components"]):
        fail("PARAMETER_SET_INVALID", str(sorted(parameters)))
    return manifest


def validate_claim(claim: object, contract: dict, label: str) -> dict:
    expected = {
        "parameter_id",
        "claim_type",
        "supported_value",
        "observation_count",
        "distinct_observation_dates",
        "derivation",
    }
    if not isinstance(claim, dict) or set(claim) != expected:
        fail("EVIDENCE_DOCUMENT_INVALID", f"{label}: claim schema")
    validate_identifier(claim["parameter_id"], f"{label}.parameter_id")
    if claim["claim_type"] not in contract["claim_types"]:
        fail("EVIDENCE_DOCUMENT_INVALID", f"{label}: claim_type")
    for field in ("observation_count", "distinct_observation_dates"):
        count = claim[field]
        if count is not None and (
            not isinstance(count, int) or isinstance(count, bool) or count < 0
        ):
            fail("EVIDENCE_DOCUMENT_INVALID", f"{label}: {field}")
    validate_identifier(claim["derivation"], f"{label}.derivation")
    return claim


def validate_evidence_document(document: object, contract: dict) -> dict:
    ensure_no_float(document, "evidence_document")
    expected = {
        "schema_version",
        "contract_version",
        "evidence_id",
        "evidence_kind",
        "published_at",
        "available_at",
        "valid_through",
        "source_locator",
        "parameter_claims",
        "caveats",
    }
    if not isinstance(document, dict) or set(document) != expected:
        fail("EVIDENCE_DOCUMENT_INVALID", "schema")
    if document["schema_version"] != 1:
        fail("EVIDENCE_DOCUMENT_INVALID", "schema_version")
    if document["contract_version"] != contract["evidence_document_version"]:
        fail("EVIDENCE_DOCUMENT_INVALID", "contract_version")
    validate_identifier(document["evidence_id"], "evidence_id")
    if document["evidence_kind"] not in contract["evidence_kinds"]:
        fail("EVIDENCE_DOCUMENT_INVALID", "evidence_kind")
    published = parse_timestamp(document["published_at"], "published_at")
    available = parse_timestamp(document["available_at"], "available_at")
    if available < published:
        fail("EVIDENCE_DOCUMENT_INVALID", "available_at precedes published_at")
    valid_through = document["valid_through"]
    if valid_through is not None:
        valid = parse_timestamp(valid_through, "valid_through")
        if valid < available:
            fail("EVIDENCE_DOCUMENT_INVALID", "valid_through precedes available_at")
    if not isinstance(document["source_locator"], str) or not document["source_locator"]:
        fail("EVIDENCE_DOCUMENT_INVALID", "source_locator")
    claims = document["parameter_claims"]
    if not isinstance(claims, list):
        fail("EVIDENCE_DOCUMENT_INVALID", "parameter_claims")
    validated_claims = [
        validate_claim(claim, contract, f"parameter_claims[{index}]")
        for index, claim in enumerate(claims)
    ]
    claim_ids = [claim["parameter_id"] for claim in validated_claims]
    if len(claim_ids) != len(set(claim_ids)):
        fail("EVIDENCE_DOCUMENT_INVALID", "duplicate parameter claim")
    validate_warning_list(document["caveats"], "caveats")
    return document


def resolve_evidence_ref(
    reference: dict,
    evidence_root: Path,
    contract: dict,
) -> Optional[dict]:
    root = Path(evidence_root).resolve()
    path = (root / reference["path"]).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        fail("EVIDENCE_REF_INVALID", f"outside evidence root: {reference['path']}")
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"), parse_constant=reject_json_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail("EVIDENCE_DOCUMENT_INVALID", f"{reference['path']}: {exc}")
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != reference["sha256"]:
        fail("EVIDENCE_SHA_MISMATCH", reference["path"])
    validated = validate_evidence_document(document, contract)
    if validated["evidence_id"] != reference["evidence_id"]:
        fail("EVIDENCE_ID_MISMATCH", reference["path"])
    return validated


def claim_support_reason(parameter: dict, document: dict, claim: dict) -> Optional[str]:
    kind = document["evidence_kind"]
    claim_type = claim["claim_type"]
    if kind == "UNSUPPORTED" or claim_type == "UNSUPPORTED":
        return "UNSUPPORTED_EVIDENCE"
    if claim_type == "QUALITATIVE_PRINCIPLE":
        return "QUALITATIVE_ONLY"
    if canonical_bytes(claim["supported_value"]) != canonical_bytes(
        parameter["proposed_value"]
    ):
        return "VALUE_MISMATCH"
    required_claim = {
        "EMPIRICAL_DISTRIBUTION": "EMPIRICAL_STATISTIC",
        "HISTORICAL_EPISODE": "HISTORICAL_EPISODE_RULE",
        "CIO_DOCTRINE": "EXPLICIT_PARAMETER_VALUE",
        "EXTERNAL_RESEARCH": "EXPLICIT_PARAMETER_VALUE",
    }.get(kind)
    if claim_type != required_claim:
        return "EVIDENCE_KIND_CLAIM_MISMATCH"
    if (
        parameter["value_type"] in {"NUMBER", "BOOLEAN"}
        and kind in {"EMPIRICAL_DISTRIBUTION", "HISTORICAL_EPISODE"}
        and (
            claim["observation_count"] is None
            or claim["observation_count"] <= 1
            or claim["distinct_observation_dates"] is None
            or claim["distinct_observation_dates"] <= 1
        )
    ):
        return "SINGLE_OBSERVATION_STATISTIC"
    return None


def evaluate_parameter(
    parameter: dict,
    decision_at: datetime,
    evidence_root: Path,
    contract: dict,
) -> dict:
    reasons = []
    supporting = []
    if parameter["proposed_value"] is None:
        reasons.append("VALUE_UNSPECIFIED")
    if not parameter["evidence_refs"]:
        reasons.append("EVIDENCE_MISSING")

    for reference in parameter["evidence_refs"]:
        document = resolve_evidence_ref(reference, evidence_root, contract)
        if document is None:
            reasons.append("EVIDENCE_MISSING")
            continue
        available_at = parse_timestamp(document["available_at"], "available_at")
        if available_at > decision_at:
            reasons.append("FUTURE_EVIDENCE")
            continue
        if document["valid_through"] is not None and parse_timestamp(
            document["valid_through"], "valid_through"
        ) < decision_at:
            reasons.append("STALE_EVIDENCE")
            continue
        matching = [
            claim
            for claim in document["parameter_claims"]
            if claim["parameter_id"] == parameter["parameter_id"]
        ]
        if not matching:
            reasons.append("PARAMETER_CLAIM_MISSING")
            continue
        claim = matching[0]
        reason = claim_support_reason(parameter, document, claim)
        if reason is not None:
            reasons.append(reason)
            continue
        supporting.append(
            {
                "evidence_id": document["evidence_id"],
                "evidence_kind": document["evidence_kind"],
                "path": reference["path"],
                "sha256": reference["sha256"],
                "available_at": document["available_at"],
                "claim_type": claim["claim_type"],
                "derivation": claim["derivation"],
                "caveats": sorted(document["caveats"]),
            }
        )

    unique_reasons = sorted(set(reasons))
    is_supported = parameter["proposed_value"] is not None and bool(supporting)
    return {
        "component": parameter["component"],
        "parameter_id": parameter["parameter_id"],
        "value_type": parameter["value_type"],
        "proposed_value": parameter["proposed_value"],
        "status": "SUPPORTED" if is_supported else "BLOCKED",
        "blocking_reasons": [] if is_supported else unique_reasons,
        "non_supporting_evidence_reasons": unique_reasons if is_supported else [],
        "supporting_evidence": sorted(
            supporting,
            key=lambda item: (item["evidence_id"], item["path"]),
        ),
    }


def expected_inventory(
    manifest: dict,
    evidence_root: Path,
    contract: dict,
) -> dict:
    decision_at = parse_timestamp(manifest["decision_at"], "decision_at")
    by_component = {
        parameter["component"]: parameter for parameter in manifest["parameters"]
    }
    parameters = [
        evaluate_parameter(
            by_component[component],
            decision_at,
            evidence_root,
            contract,
        )
        for component in contract["required_components"]
    ]
    blocked_components = [
        item["component"] for item in parameters if item["status"] == "BLOCKED"
    ]
    status = "CANDIDATE_BLOCKED" if blocked_components else "CANDIDATE_READY"
    normalized_manifest = dict(manifest)
    normalized_manifest["parameters"] = []
    for component in contract["required_components"]:
        parameter = dict(by_component[component])
        parameter["evidence_refs"] = sorted(
            parameter["evidence_refs"],
            key=lambda item: (item["evidence_id"], item["path"]),
        )
        normalized_manifest["parameters"].append(parameter)
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "contract_mode": contract["contract_mode"],
        "candidate_id": manifest["candidate_id"],
        "candidate_status": status,
        "policy_status": contract["policy_status"],
        "market": manifest["market"],
        "decision_at": manifest["decision_at"],
        "source_refs": {
            "candidate_manifest_contract_version": manifest["contract_version"],
            "candidate_manifest_sha256": payload_sha256(normalized_manifest),
        },
        "parameters": parameters,
        "blocked_components": blocked_components,
        "replay": {
            "candidate_input_eligible": status == "CANDIDATE_READY",
            "population_status": "NOT_COMPUTABLE",
            "winner_selected": False,
        },
        "ratification": {
            "selected": False,
            "recommended": False,
            "ratified": False,
            "runtime_eligible": False,
        },
        "authority": dict(contract["authority"]),
    }


def build_candidate_inventory(
    candidate_manifest: object,
    evidence_root: Path = ROOT,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    manifest = validate_manifest(candidate_manifest, contract)
    inventory = expected_inventory(manifest, evidence_root, contract)
    return validate_candidate_inventory(inventory, manifest, evidence_root, contract)


def validate_candidate_inventory(
    inventory: object,
    candidate_manifest: object,
    evidence_root: Path = ROOT,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_contract(load_contract() if contract is None else contract)
    manifest = validate_manifest(candidate_manifest, contract)
    expected = expected_inventory(manifest, evidence_root, contract)
    if not isinstance(inventory, dict):
        fail("CANDIDATE_INVENTORY_INVALID", "object required")
    if canonical_bytes(inventory) != canonical_bytes(expected):
        fail("CANDIDATE_INVENTORY_DERIVATION_MISMATCH", "inventory != evidence")
    return inventory


def build_baseline_manifest(market: str, decision_at: str) -> dict:
    contract = load_contract()
    manifest = {
        "schema_version": 1,
        "contract_version": contract["candidate_manifest_version"],
        "candidate_id": "UNSPECIFIED_POLICY_CANDIDATE",
        "market": market,
        "decision_at": decision_at,
        "policy_status": contract["policy_status"],
        "parameters": [
            {
                "component": component,
                "parameter_id": component,
                "value_type": "UNSPECIFIED",
                "proposed_value": None,
                "evidence_refs": [],
            }
            for component in contract["required_components"]
        ],
    }
    return validate_manifest(manifest, contract)


def write_json(value: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    inventory = sub.add_parser("inventory")
    inventory.add_argument("candidate_manifest", type=Path)
    inventory.add_argument("--evidence-root", type=Path, default=ROOT)
    inventory.add_argument("--out", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("inventory", type=Path)
    validate.add_argument("--candidate-manifest", type=Path, required=True)
    validate.add_argument("--evidence-root", type=Path, default=ROOT)

    baseline = sub.add_parser("baseline")
    baseline.add_argument("--market", required=True)
    baseline.add_argument("--decision-at", required=True)
    baseline.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "baseline":
            result = build_candidate_inventory(
                build_baseline_manifest(args.market, args.decision_at),
                ROOT,
            )
        else:
            manifest = load_json(
                args.candidate_manifest,
                "CANDIDATE_MANIFEST_INVALID",
            )
            if args.command == "validate":
                result = validate_candidate_inventory(
                    load_json(args.inventory, "CANDIDATE_INVENTORY_INVALID"),
                    manifest,
                    args.evidence_root,
                )
            else:
                result = build_candidate_inventory(manifest, args.evidence_root)
        if getattr(args, "out", None) is not None:
            write_json(result, args.out)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
        return 0
    except PolicyCandidateError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
