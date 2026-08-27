#!/usr/bin/env python3
"""Populate exact positive and negative P1-COM-05 policy evidence.

The user-ratified P1-COM-02 five-of-five policy supports MINIMUM_COVERAGE.  The
P1-COM-05 authority boundary explicitly proves that MARKET_NORMALIZATION is
unratified; it is retained as UNSUPPORTED_EVIDENCE, not converted into a value.
Every remaining component stays unspecified and the candidate stays blocked.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import minimum_coverage as MINIMUM  # noqa: E402
from regime import policy_candidate as CANDIDATE  # noqa: E402
from regime import decision_authority as DECISION  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "regime_policy_candidate_population_contract.json"
CONTRACT_BYTES_SHA256 = "0d0adf64b56a14cde3186d04bcbb5583b6783eaafe08130dd7f5144b74253bf0"


class PolicyCandidatePopulationError(RuntimeError):
    """Fail-closed population or retained-artifact error."""


def fail(code: str, detail: str) -> None:
    raise PolicyCandidatePopulationError(f"{code}: {detail}")


def reject_json_constant(value: str) -> None:
    fail("NUMBER_INVALID", value)


def load_json_bytes(raw: bytes, label: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail("JSON_INVALID", f"{label}: {exc}")


def canonical_bytes(value: object) -> bytes:
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


def render_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("RENDER_JSON_INVALID", str(exc))
    return (encoded + "\n").encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        fail("PATH_INVALID", label)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        fail("PATH_INVALID", label)
    return value


def resolve_path(root: Path, relative: str, label: str) -> Path:
    root = Path(root).resolve()
    path = (root / safe_relative_path(relative, label)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        fail("PATH_INVALID", f"{label}: outside root")
    return path


def _validate_population_contract_shape(contract: object) -> dict:
    expected = {
        "schema_version",
        "contract_version",
        "contract_mode",
        "candidate_contract",
        "source_policy",
        "unratified_policy_boundary",
        "candidate",
        "artifact_paths",
        "expected_population",
        "authority",
    }
    if not isinstance(contract, dict) or set(contract) != expected:
        fail("POPULATION_CONTRACT_INVALID", "schema")
    pinned = {
        "schema_version": 1,
        "contract_version": "regime_policy_candidate_population/v2",
        "contract_mode": "SHADOW_DIAGNOSTIC_ONLY",
    }
    if any(contract.get(key) != value for key, value in pinned.items()):
        fail("POPULATION_CONTRACT_INVALID", "identity")
    for group in (
        "candidate_contract",
        "source_policy",
        "unratified_policy_boundary",
        "candidate",
        "artifact_paths",
    ):
        if not isinstance(contract[group], dict):
            fail("POPULATION_CONTRACT_INVALID", group)
    for key, value in contract["artifact_paths"].items():
        safe_relative_path(value, f"artifact_paths.{key}")
    authority = contract["authority"]
    if not isinstance(authority, dict):
        fail("POPULATION_CONTRACT_INVALID", "authority")
    if authority.get("candidate_evidence_population_authorized") is not True:
        fail("POPULATION_CONTRACT_INVALID", "population authority")
    if any(
        value is not False
        for key, value in authority.items()
        if key != "candidate_evidence_population_authorized"
    ):
        fail("POPULATION_CONTRACT_INVALID", "downstream authority")
    return contract


def load_population_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        fail("POPULATION_CONTRACT_INVALID", str(exc))
    if sha256(raw) != CONTRACT_BYTES_SHA256:
        fail("POPULATION_CONTRACT_SHA_MISMATCH", str(path))
    return _validate_population_contract_shape(load_json_bytes(raw, str(path)))


def validate_population_contract(contract: object) -> dict:
    pinned = load_population_contract()
    validated = _validate_population_contract_shape(contract)
    if canonical_bytes(validated) != canonical_bytes(pinned):
        fail("POPULATION_CONTRACT_INVALID", "not pinned contract")
    return validated


def load_source_contracts(
    source_root: Path,
    contract: dict,
) -> tuple[dict, dict, dict]:
    candidate_ref = contract["candidate_contract"]
    candidate_path = resolve_path(
        source_root,
        candidate_ref["path"],
        "candidate_contract.path",
    )
    minimum_ref = contract["source_policy"]
    minimum_path = resolve_path(source_root, minimum_ref["path"], "source_policy.path")
    boundary_ref = contract["unratified_policy_boundary"]
    boundary_path = resolve_path(
        source_root,
        boundary_ref["path"],
        "unratified_policy_boundary.path",
    )
    try:
        candidate_raw = candidate_path.read_bytes()
        minimum_raw = minimum_path.read_bytes()
        boundary_raw = boundary_path.read_bytes()
    except OSError as exc:
        fail("SOURCE_POLICY_MISSING", str(exc))
    if sha256(candidate_raw) != candidate_ref["sha256"]:
        fail("CANDIDATE_CONTRACT_SHA_MISMATCH", candidate_ref["path"])
    if sha256(minimum_raw) != minimum_ref["sha256"]:
        fail("MINIMUM_COVERAGE_SOURCE_SHA_MISMATCH", minimum_ref["path"])
    if sha256(boundary_raw) != boundary_ref["sha256"]:
        fail("UNRATIFIED_BOUNDARY_SOURCE_SHA_MISMATCH", boundary_ref["path"])
    try:
        candidate_contract = CANDIDATE.validate_contract(
            load_json_bytes(candidate_raw, candidate_ref["path"])
        )
        minimum_contract = MINIMUM.validate_contract(
            load_json_bytes(minimum_raw, minimum_ref["path"])
        )
        boundary_contract = DECISION.validate_contract(
            load_json_bytes(boundary_raw, boundary_ref["path"])
        )
    except (
        CANDIDATE.PolicyCandidateError,
        MINIMUM.MinimumCoverageError,
        DECISION.DecisionAuthorityError,
    ) as exc:
        fail("SOURCE_POLICY_INVALID", str(exc))
    if candidate_contract["contract_version"] != candidate_ref["contract_version"]:
        fail("SOURCE_POLICY_INVALID", "candidate contract version")
    if minimum_contract["contract_version"] != minimum_ref["contract_version"]:
        fail("SOURCE_POLICY_INVALID", "minimum coverage contract version")
    if minimum_contract["policy_name"] != minimum_ref["policy_name"]:
        fail("SOURCE_POLICY_INVALID", "minimum coverage policy name")
    if minimum_contract["policy_status"] != minimum_ref["policy_status"]:
        fail("SOURCE_POLICY_INVALID", "minimum coverage policy status")
    if boundary_contract["contract_version"] != boundary_ref["contract_version"]:
        fail("SOURCE_POLICY_INVALID", "unratified boundary contract version")
    if boundary_contract["repository_policy_registry_status"] != boundary_ref[
        "repository_policy_registry_status"
    ]:
        fail("SOURCE_POLICY_INVALID", "repository policy registry status")
    source_component = boundary_ref["source_policy_component"]
    if boundary_contract["policy_component_status"].get(source_component) != boundary_ref[
        "component_status"
    ]:
        fail("SOURCE_POLICY_INVALID", "normalization component status")
    if boundary_contract["policy_reason_codes"].get(source_component) != boundary_ref[
        "reason_code"
    ]:
        fail("SOURCE_POLICY_INVALID", "normalization reason code")
    if boundary_contract["authority"].get(boundary_ref["authority_key"]) is not boundary_ref[
        "authority_value"
    ]:
        fail("SOURCE_POLICY_INVALID", "normalization authority")
    return candidate_contract, minimum_contract, boundary_contract


def minimum_coverage_value(minimum_contract: dict) -> dict:
    # Preserve the ratified contract without translating, filling, or selecting
    # a subset of its semantics.  The candidate remains independently DRAFT.
    return copy.deepcopy(minimum_contract)


def expected_minimum_coverage_evidence(
    contract: dict,
    candidate_contract: dict,
    minimum_contract: dict,
) -> dict:
    source = contract["source_policy"]
    candidate = contract["candidate"]
    return {
        "schema_version": 1,
        "contract_version": candidate_contract["evidence_document_version"],
        "evidence_id": candidate["evidence_id"],
        "evidence_kind": "CIO_DOCTRINE",
        "published_at": source["published_at"],
        "available_at": source["available_at"],
        "valid_through": None,
        "source_locator": (
            f"github://yonggeun1021-hub/atlas-data/pull/{source['pull_request']}"
            f"?source={source['source_commit']}&merge={source['merge_commit']}"
            f"&path={source['path']};notion:{source['canonical_wbs_url']}"
        ),
        "parameter_claims": [
            {
                "parameter_id": candidate["parameter_id"],
                "claim_type": "EXPLICIT_PARAMETER_VALUE",
                "supported_value": minimum_coverage_value(minimum_contract),
                "observation_count": None,
                "distinct_observation_dates": None,
                "derivation": "USER_RATIFIED_REPOSITORY_POLICY",
            }
        ],
        "caveats": [
            "CLASSIFICATION_UNAUTHORIZED",
            "COVERAGE_ONLY",
            "FRESHNESS_POLICY_UNRATIFIED",
        ],
    }


def expected_normalization_negative_evidence(
    contract: dict,
    candidate_contract: dict,
    boundary_contract: dict,
) -> dict:
    source = contract["unratified_policy_boundary"]
    source_component = source["source_policy_component"]
    return {
        "schema_version": 1,
        "contract_version": candidate_contract["evidence_document_version"],
        "evidence_id": source["evidence_id"],
        "evidence_kind": "UNSUPPORTED",
        "published_at": source["published_at"],
        "available_at": source["available_at"],
        "valid_through": None,
        "source_locator": (
            f"github://yonggeun1021-hub/atlas-data/pull/{source['pull_request']}"
            f"?source={source['source_commit']}&merge={source['merge_commit']}"
            f"&path={source['path']}"
        ),
        "parameter_claims": [
            {
                "parameter_id": source["candidate_component"],
                "claim_type": "UNSUPPORTED",
                "supported_value": None,
                "observation_count": None,
                "distinct_observation_dates": None,
                "derivation": "REPOSITORY_UNRATIFIED_POLICY_BOUNDARY",
            }
        ],
        "caveats": [
            boundary_contract["policy_reason_codes"][source_component],
            "POLICY_REGISTRY_ABSENT",
            "STRUCTURAL_NORMALIZATION_IS_NOT_POLICY_NORMALIZATION",
        ],
    }


def expected_manifest(
    contract: dict,
    candidate_contract: dict,
    minimum_contract: dict,
    minimum_evidence_raw: bytes,
    normalization_evidence_raw: bytes,
) -> dict:
    candidate = contract["candidate"]
    evidence_path = contract["artifact_paths"]["evidence"]
    boundary = contract["unratified_policy_boundary"]
    normalization_evidence_path = contract["artifact_paths"]["normalization_evidence"]
    parameters = []
    for component in candidate_contract["required_components"]:
        if component == candidate["populated_component"]:
            parameters.append(
                {
                    "component": component,
                    "parameter_id": candidate["parameter_id"],
                    "value_type": "STRUCTURED",
                    "proposed_value": minimum_coverage_value(minimum_contract),
                    "evidence_refs": [
                        {
                            "path": evidence_path,
                            "sha256": sha256(minimum_evidence_raw),
                            "evidence_id": candidate["evidence_id"],
                        }
                    ],
                }
            )
        elif component == boundary["candidate_component"]:
            parameters.append(
                {
                    "component": component,
                    "parameter_id": component,
                    "value_type": "UNSPECIFIED",
                    "proposed_value": None,
                    "evidence_refs": [
                        {
                            "path": normalization_evidence_path,
                            "sha256": sha256(normalization_evidence_raw),
                            "evidence_id": boundary["evidence_id"],
                        }
                    ],
                }
            )
        else:
            parameters.append(
                {
                    "component": component,
                    "parameter_id": component,
                    "value_type": "UNSPECIFIED",
                    "proposed_value": None,
                    "evidence_refs": [],
                }
            )
    return {
        "schema_version": 1,
        "contract_version": candidate_contract["candidate_manifest_version"],
        "candidate_id": candidate["candidate_id"],
        "market": candidate["market"],
        "decision_at": candidate["decision_at"],
        "policy_status": candidate["policy_status"],
        "parameters": parameters,
    }


def write_artifact(root: Path, relative: str, value: object) -> Path:
    path = resolve_path(root, relative, "artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_bytes(value))
    return path


def build_population(
    artifact_root: Path = ROOT,
    source_root: Path = ROOT,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_population_contract(
        load_population_contract() if contract is None else contract
    )
    candidate_contract, minimum_contract, boundary_contract = load_source_contracts(
        source_root,
        contract,
    )
    with tempfile.TemporaryDirectory() as raw:
        temporary_root = Path(raw)
        minimum_evidence = expected_minimum_coverage_evidence(
            contract,
            candidate_contract,
            minimum_contract,
        )
        minimum_evidence_path = write_artifact(
            temporary_root,
            contract["artifact_paths"]["evidence"],
            minimum_evidence,
        )
        normalization_evidence = expected_normalization_negative_evidence(
            contract,
            candidate_contract,
            boundary_contract,
        )
        normalization_evidence_path = write_artifact(
            temporary_root,
            contract["artifact_paths"]["normalization_evidence"],
            normalization_evidence,
        )
        manifest = expected_manifest(
            contract,
            candidate_contract,
            minimum_contract,
            minimum_evidence_path.read_bytes(),
            normalization_evidence_path.read_bytes(),
        )
        write_artifact(
            temporary_root,
            contract["artifact_paths"]["manifest"],
            manifest,
        )
        try:
            inventory = CANDIDATE.build_candidate_inventory(
                manifest,
                temporary_root,
                candidate_contract,
            )
        except CANDIDATE.PolicyCandidateError as exc:
            fail("CANDIDATE_INVENTORY_BUILD_FAILED", str(exc))
        write_artifact(
            temporary_root,
            contract["artifact_paths"]["inventory"],
            inventory,
        )
        validate_population(temporary_root, source_root, contract)
        for relative in contract["artifact_paths"].values():
            source_path = resolve_path(temporary_root, relative, "temporary artifact")
            target_path = resolve_path(artifact_root, relative, "target artifact")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
    return validate_population(artifact_root, source_root, contract)


def validate_population(
    artifact_root: Path = ROOT,
    source_root: Path = ROOT,
    contract: Optional[dict] = None,
) -> dict:
    contract = validate_population_contract(
        load_population_contract() if contract is None else contract
    )
    candidate_contract, minimum_contract, boundary_contract = load_source_contracts(
        source_root,
        contract,
    )
    paths = {
        key: resolve_path(artifact_root, relative, f"artifact_paths.{key}")
        for key, relative in contract["artifact_paths"].items()
    }
    try:
        evidence_raw = paths["evidence"].read_bytes()
        normalization_evidence_raw = paths["normalization_evidence"].read_bytes()
        manifest_raw = paths["manifest"].read_bytes()
        inventory_raw = paths["inventory"].read_bytes()
    except OSError as exc:
        fail("POPULATION_ARTIFACT_MISSING", str(exc))

    evidence = expected_minimum_coverage_evidence(
        contract,
        candidate_contract,
        minimum_contract,
    )
    if evidence_raw != render_bytes(evidence):
        fail("EVIDENCE_ARTIFACT_MISMATCH", str(paths["evidence"]))
    normalization_evidence = expected_normalization_negative_evidence(
        contract,
        candidate_contract,
        boundary_contract,
    )
    if normalization_evidence_raw != render_bytes(normalization_evidence):
        fail(
            "NORMALIZATION_EVIDENCE_ARTIFACT_MISMATCH",
            str(paths["normalization_evidence"]),
        )
    manifest = expected_manifest(
        contract,
        candidate_contract,
        minimum_contract,
        evidence_raw,
        normalization_evidence_raw,
    )
    if manifest_raw != render_bytes(manifest):
        fail("MANIFEST_ARTIFACT_MISMATCH", str(paths["manifest"]))
    try:
        expected_inventory = CANDIDATE.build_candidate_inventory(
            manifest,
            artifact_root,
            candidate_contract,
        )
    except CANDIDATE.PolicyCandidateError as exc:
        fail("CANDIDATE_INVENTORY_BUILD_FAILED", str(exc))
    if inventory_raw != render_bytes(expected_inventory):
        fail("INVENTORY_ARTIFACT_MISMATCH", str(paths["inventory"]))

    supported = [
        item["component"]
        for item in expected_inventory["parameters"]
        if item["status"] == "SUPPORTED"
    ]
    expected_population = contract["expected_population"]
    if supported != expected_population["supported_components"]:
        fail("POPULATION_SEMANTICS_INVALID", "supported components")
    explicit_negative = [
        item["component"]
        for item in expected_inventory["parameters"]
        if item["blocking_reasons"] == ["UNSUPPORTED_EVIDENCE", "VALUE_UNSPECIFIED"]
    ]
    if explicit_negative != expected_population["explicit_negative_components"]:
        fail("POPULATION_SEMANTICS_INVALID", "explicit negative components")
    evidence_missing = [
        item["component"]
        for item in expected_inventory["parameters"]
        if "EVIDENCE_MISSING" in item["blocking_reasons"]
    ]
    if len(evidence_missing) != expected_population[
        "missing_evidence_component_count"
    ]:
        fail("POPULATION_SEMANTICS_INVALID", "missing evidence component count")
    if len(expected_inventory["blocked_components"]) != expected_population[
        "blocked_component_count"
    ]:
        fail("POPULATION_SEMANTICS_INVALID", "blocked component count")
    if expected_inventory["candidate_status"] != expected_population["candidate_status"]:
        fail("POPULATION_SEMANTICS_INVALID", "candidate status")
    if expected_inventory["replay"]["population_status"] != expected_population[
        "replay_population_status"
    ]:
        fail("POPULATION_SEMANTICS_INVALID", "replay population")
    if expected_inventory["replay"]["candidate_input_eligible"] is not False:
        fail("POPULATION_SEMANTICS_INVALID", "replay eligibility")
    if expected_inventory["authority"] != candidate_contract["authority"]:
        fail("POPULATION_SEMANTICS_INVALID", "candidate inventory authority")

    return {
        "contract_version": contract["contract_version"],
        "candidate_id": expected_inventory["candidate_id"],
        "candidate_status": expected_inventory["candidate_status"],
        "supported_components": supported,
        "explicit_negative_components": explicit_negative,
        "missing_evidence_components": evidence_missing,
        "blocked_components": list(expected_inventory["blocked_components"]),
        "source_policy": {
            "path": contract["source_policy"]["path"],
            "sha256": contract["source_policy"]["sha256"],
            "source_commit": contract["source_policy"]["source_commit"],
            "merge_commit": contract["source_policy"]["merge_commit"],
            "pull_request": contract["source_policy"]["pull_request"],
        },
        "unratified_policy_boundary": {
            "path": contract["unratified_policy_boundary"]["path"],
            "sha256": contract["unratified_policy_boundary"]["sha256"],
            "source_commit": contract["unratified_policy_boundary"][
                "source_commit"
            ],
            "merge_commit": contract["unratified_policy_boundary"][
                "merge_commit"
            ],
            "pull_request": contract["unratified_policy_boundary"][
                "pull_request"
            ],
        },
        "artifact_sha256": {
            "evidence": sha256(evidence_raw),
            "normalization_evidence": sha256(normalization_evidence_raw),
            "manifest": sha256(manifest_raw),
            "inventory": sha256(inventory_raw),
        },
        "replay_population_status": expected_inventory["replay"]["population_status"],
        "authority": dict(contract["authority"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "validate"):
        command = sub.add_parser(name)
        command.add_argument("--artifact-root", type=Path, default=ROOT)
        command.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_population(args.artifact_root, args.source_root)
        else:
            result = validate_population(args.artifact_root, args.source_root)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False))
        return 0
    except PolicyCandidatePopulationError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
