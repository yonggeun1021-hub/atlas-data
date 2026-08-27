#!/usr/bin/env python3
"""P1-COM-04 replay-population readiness from canonical candidate evidence.

The deterministic pre-score replay harness is a capability, not a populated
historical case set. This command binds that capability to the canonical
P1-COM-05 evidence inventory and reports whether replay population is eligible.
It never invents missing policy parameters, cases, thresholds, or outcomes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "regime_replay_population_readiness_contract.json"
SCHEMA_VERSION = "regime_replay_population_readiness/v1"


def _load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POPULATION = _load_module(
    "atlas_policy_candidate_population_for_replay_readiness",
    "regime/policy_candidate_population.py",
)
REPLAY = _load_module(
    "atlas_replay_harness_for_population_readiness",
    "regime/replay_harness.py",
)


class ReplayPopulationReadinessError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_bytes(path: Path, code: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise ReplayPopulationReadinessError(code) from exc


def _read_json(path: Path, code: str):
    raw = _read_bytes(path, code)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplayPopulationReadinessError(code) from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": SCHEMA_VERSION,
        "contract_mode": "SHADOW_DIAGNOSTIC_ONLY",
        "replay_harness_contract": {
            "path": "config/regime_replay_harness_contract.json",
            "sha256": "d6703e9f58a54aa1e9f08ae001722ce1c8195091da3a576b6d35aa53cf98fe4a",
            "contract_version": "regime_replay_harness/v1",
        },
        "policy_candidate_population_contract": {
            "path": "config/regime_policy_candidate_population_contract.json",
            "sha256": "ddcba56291409fc5b8ac9e0a9324d6c6e779240ac8138468b310feb0b512db76",
            "contract_version": "regime_policy_candidate_population/v5",
        },
        "required_markets": ["US", "KR", "CRYPTO"],
        "required_candidate_status": "CANDIDATE_READY",
        "not_ready_status": "NOT_COMPUTABLE_POLICY_CANDIDATE_BLOCKED",
        "authority": {
            "readiness_inventory_only": True,
            "replay_population_authorized": False,
            "candidate_selection_authorized": False,
            "policy_recommendation_authorized": False,
            "policy_ratification_authorized": False,
            "regime_classification_authorized": False,
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


def _safe(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        raise ReplayPopulationReadinessError("CONTRACT_PATH_INVALID")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ReplayPopulationReadinessError("CONTRACT_PATH_INVALID") from exc
    return path


def _load_contract(root: Path) -> dict:
    value = _read_json(root / "config" / CONTRACT_PATH.name, "READINESS_CONTRACT_INVALID")
    if value != _expected_contract():
        raise ReplayPopulationReadinessError("READINESS_CONTRACT_MISMATCH")
    for name in ("replay_harness_contract", "policy_candidate_population_contract"):
        ref = value[name]
        raw = _read_bytes(_safe(root, ref["path"]), "BOUND_CONTRACT_MISSING")
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ReplayPopulationReadinessError(f"BOUND_CONTRACT_SHA_MISMATCH:{name}")
    return value


def build_readiness(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    contract = _load_contract(root)
    try:
        population = POPULATION.validate_population(root, root)
        replay_contract = REPLAY.load_contract(
            _safe(root, contract["replay_harness_contract"]["path"])
        )
    except (POPULATION.PolicyCandidatePopulationError, REPLAY.ReplayHarnessError) as exc:
        raise ReplayPopulationReadinessError("BOUND_SOURCE_VALIDATION_FAILED") from exc
    if population["contract_version"] != contract[
        "policy_candidate_population_contract"
    ]["contract_version"]:
        raise ReplayPopulationReadinessError("POPULATION_CONTRACT_VERSION_MISMATCH")
    if replay_contract["contract_version"] != contract["replay_harness_contract"][
        "contract_version"
    ]:
        raise ReplayPopulationReadinessError("REPLAY_CONTRACT_VERSION_MISMATCH")
    candidate_ready = population["candidate_status"] == contract["required_candidate_status"]
    if candidate_ready:
        raise ReplayPopulationReadinessError(
            "READY_CANDIDATE_REQUIRES_SEPARATE_CASE_POPULATION_IMPLEMENTATION"
        )
    blocked = list(population["blocked_components"])
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "contract_mode": contract["contract_mode"],
        "status": contract["not_ready_status"],
        "candidate": {
            "candidate_id": population["candidate_id"],
            "candidate_status": population["candidate_status"],
            "supported_components": list(population["supported_components"]),
            "explicit_negative_components": list(
                population["explicit_negative_components"]
            ),
            "missing_evidence_components": list(
                population["missing_evidence_components"]
            ),
            "blocked_components": blocked,
            "artifact_sha256": copy.deepcopy(population["artifact_sha256"]),
        },
        "replay_capability": {
            "harness_contract_version": replay_contract["contract_version"],
            "source_contract_mode": replay_contract["source_contract_mode"],
            "comparison_policy": replay_contract["comparison_policy"],
            "capability_available": True,
            "candidate_inventory_bound": True,
        },
        "population": {
            "required_markets": list(contract["required_markets"]),
            "eligible_market_count": 0,
            "eligible_case_count": 0,
            "case_population_status": "NOT_COMPUTABLE_CANDIDATE_INPUT_NOT_ELIGIBLE",
            "historical_outcome_evaluated": False,
        },
        "blockers": ["CANDIDATE_INPUT_NOT_ELIGIBLE"]
        + [f"POLICY_COMPONENT_BLOCKED:{component}" for component in blocked],
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_readiness(value: dict, root: Path = ROOT) -> dict:
    expected = build_readiness(root)
    if value != expected:
        raise ReplayPopulationReadinessError("READINESS_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


def write_json_atomic(path: Path, value: dict, root: Path = ROOT) -> None:
    path = Path(path).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError:
        pass
    else:
        raise ReplayPopulationReadinessError("TRACKED_OUTPUT_FORBIDDEN")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    packet = build_readiness()
    validate_readiness(packet)
    write_json_atomic(args.out, packet)
    print(
        "regime replay population readiness: "
        f"status={packet['status']} eligible_cases=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
