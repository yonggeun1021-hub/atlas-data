#!/usr/bin/env python3
"""P10-12 fail-closed crypto strategy re-review trigger readiness.

The command reports whether any canonical condition exists for reopening
strategy review.  It never interprets missing private capital, an UNKNOWN
Regime, a newly visible venue feed, or a current fee quote as a trigger.
Candidate NONE and live engine count zero remain locked.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    ROOT / "config/crypto_strategy_rereview_trigger_readiness_contract.json"
)
DEFAULT_REGIME_STATUS = ROOT / "data/latest_crypto_regime_refresh_status.json"
DEFAULT_SOURCE_INVENTORY = ROOT / "config/data_coverage_registry.json"
SCHEMA_VERSION = "crypto_strategy_rereview_trigger_readiness/1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


TRIGGER_RESULTS = {
    "CAPITAL_AT_LEAST_10000_USD": {
        "result": "NOT_PROVEN",
        "evidence_status": "NOT_COMPUTABLE",
        "blocker": "CANONICAL_ACCOUNT_CAPITAL_PACKET_MISSING",
    },
    "RATIFIED_MARKET_REGIME_CHANGE": {
        "result": "NOT_PROVEN",
        "evidence_status": "NOT_COMPUTABLE",
        "blocker": "RATIFIED_BASELINE_AND_CURRENT_REGIME_MISSING",
    },
    "GENUINELY_NEW_MEASUREMENT_SOURCE": {
        "result": "NOT_PROVEN",
        "evidence_status": "FAIL",
        "blocker": "RATIFIED_MEASUREMENT_FAMILY_BASELINE_MISSING",
    },
    "MATERIAL_EXCHANGE_POLICY_OR_COST_CHANGE": {
        "result": "NOT_PROVEN",
        "evidence_status": "FAIL",
        "blocker": "CANONICAL_HISTORY_AND_MATERIALITY_POLICY_MISSING",
    },
}


AUTHORITY_ALL_FALSE = {
    "readiness_observation_only": True,
    "trigger_ratification_authorized": False,
    "strategy_ideation_authorized": False,
    "backtest_authorized": False,
    "event_study_authorized": False,
    "candidate_authorized": False,
    "paper_engine_authorized": False,
    "live_engine_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "proposal_authorized": False,
    "order_authorized": False,
    "capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class CryptoRereviewReadinessError(ValueError):
    """Canonical re-review evidence or contract is invalid."""


def fail(code: str, detail: str = "") -> None:
    suffix = f":{detail}" if detail else ""
    raise CryptoRereviewReadinessError(f"{code}{suffix}")


def canonical_json(value: object) -> str:
    def reject_float(item: object, label: str = "payload") -> None:
        if isinstance(item, float):
            fail("FLOAT_NOT_ALLOWED", label)
        if isinstance(item, dict):
            for key, nested in item.items():
                reject_float(nested, f"{label}.{key}")
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                reject_float(nested, f"{label}[{index}]")

    reject_float(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        fail("CANONICAL_JSON_INVALID", str(exc))


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _load_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoRereviewReadinessError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def _safe_path(relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        fail("CONTRACT_PATH_INVALID")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or "\\" in relative:
        fail("CONTRACT_PATH_INVALID", relative)
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise CryptoRereviewReadinessError("CONTRACT_PATH_INVALID") from exc
    return path


def _expected_contract() -> dict:
    return {
        "schema_version": "crypto_strategy_rereview_trigger_readiness_contract/1",
        "document_status": "RATIFIED_FAIL_CLOSED_READINESS_BOUNDARY",
        "wbs_binding": {
            "primary_row": "P10-12",
            "primary_title": "Crypto PAPER Counterfactual Validation & Live Review Gate",
            "supporting_rows": ["P1-CR-08", "P1-CR-09", "P4-07"],
        },
        "canonical_inputs": {
            "crypto_regime_status": "data/latest_crypto_regime_refresh_status.json",
            "source_inventory": "config/data_coverage_registry.json",
        },
        "rereview_triggers": [
            {
                "trigger_id": "CAPITAL_AT_LEAST_10000_USD",
                "requirement": "canonical account capital is at least USD 10000",
                "current_evidence_contract": "MISSING_NO_CANONICAL_ACCOUNT_CAPITAL_PACKET",
            },
            {
                "trigger_id": "RATIFIED_MARKET_REGIME_CHANGE",
                "requirement": "a ratified market regime differs from a ratified baseline regime",
                "current_evidence_contract": "MISSING_RATIFIED_BASELINE_AND_CURRENT_REGIME",
            },
            {
                "trigger_id": "GENUINELY_NEW_MEASUREMENT_SOURCE",
                "requirement": "a new measurement family is absent from the ratified failed-strategy baseline and has PIT-safe source lineage",
                "current_evidence_contract": "MISSING_RATIFIED_MEASUREMENT_FAMILY_BASELINE",
            },
            {
                "trigger_id": "MATERIAL_EXCHANGE_POLICY_OR_COST_CHANGE",
                "requirement": "a canonical exchange policy or all-in cost observation changed materially under a ratified materiality policy",
                "current_evidence_contract": "MISSING_CANONICAL_HISTORY_AND_MATERIALITY_POLICY",
            },
        ],
        "mechanism_qualification": {
            "entry_condition": "AT_LEAST_ONE_REREVIEW_TRIGGER_PROVEN",
            "questions": [
                "Q1_MARKET_INEFFICIENCY",
                "Q2_WHY_PERSIST",
                "Q3_WHY_CAPTUREABLE_AFTER_COSTS",
                "Q4_WHAT_FALSIFIES_MECHANISM",
            ],
            "required_answer_count": 4,
            "event_study_required_gate_count": 7,
            "event_study_required_pass_count": 7,
        },
        "locked_state": {
            "candidate": "NONE",
            "live_engine_count": 0,
            "paper_shadow_preparation_only": True,
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict:
    value = _load_json(path, "CONTRACT_INVALID")
    if value != _expected_contract():
        fail("CONTRACT_MISMATCH")
    for relative in value["canonical_inputs"].values():
        if not _safe_path(relative).is_file():
            fail("CANONICAL_INPUT_MISSING", relative)
    return copy.deepcopy(value)


def _validate_regime_status(value: dict) -> dict:
    claimed = value.get("payload_sha256")
    if not isinstance(claimed, str) or SHA256.fullmatch(claimed) is None:
        fail("REGIME_PAYLOAD_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        fail("REGIME_PAYLOAD_SHA_MISMATCH")
    decision = value.get("official_decision")
    authority = value.get("authority")
    if not isinstance(decision, dict) or not isinstance(authority, dict):
        fail("REGIME_STATUS_SCHEMA_INVALID")
    if authority.get("read_only_reference") is not True:
        fail("REGIME_REFERENCE_AUTHORITY_INVALID")
    for key, item in authority.items():
        if key.endswith("_authorized") and item is not False:
            fail("REGIME_AUTHORITY_ESCALATION", key)
    runtime_regime = decision.get("runtime_regime")
    if not isinstance(runtime_regime, str) or not runtime_regime:
        fail("REGIME_RUNTIME_VALUE_INVALID")
    return copy.deepcopy(value)


def _validate_source_inventory(value: dict) -> dict:
    if value.get("schema_version") != 2:
        fail("SOURCE_INVENTORY_SCHEMA_UNSUPPORTED")
    if not isinstance(value.get("registry_version"), str):
        fail("SOURCE_INVENTORY_VERSION_INVALID")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        fail("SOURCE_INVENTORY_EMPTY")
    if "strategy_measurement_inventory" in value:
        fail("UNRATIFIED_MEASUREMENT_BASELINE_FIELD_PRESENT")
    return copy.deepcopy(value)


def _git_head() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CryptoRereviewReadinessError("SOURCE_COMMIT_UNAVAILABLE") from exc
    value = completed.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        fail("SOURCE_COMMIT_INVALID")
    return value


def build_readiness(
    contract: dict | None = None,
    regime_status: dict | None = None,
    source_inventory: dict | None = None,
    *,
    source_commit: str | None = None,
) -> dict:
    locked = load_contract() if contract is None else contract
    if locked != _expected_contract():
        fail("CONTRACT_MISMATCH")
    regime = _validate_regime_status(
        _load_json(DEFAULT_REGIME_STATUS, "REGIME_STATUS_INVALID")
        if regime_status is None
        else regime_status
    )
    inventory = _validate_source_inventory(
        _load_json(DEFAULT_SOURCE_INVENTORY, "SOURCE_INVENTORY_INVALID")
        if source_inventory is None
        else source_inventory
    )
    commit = source_commit or _git_head()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        fail("SOURCE_COMMIT_INVALID")

    trigger_contracts = {item["trigger_id"]: item for item in locked["rereview_triggers"]}
    triggers = []
    for trigger_id, result in TRIGGER_RESULTS.items():
        item = copy.deepcopy(result)
        item["trigger_id"] = trigger_id
        item["requirement"] = trigger_contracts[trigger_id]["requirement"]
        item["observed"] = None
        if trigger_id == "RATIFIED_MARKET_REGIME_CHANGE":
            item["observed"] = {
                "official_runtime_regime": regime["official_decision"]["runtime_regime"],
                "classification_status": regime["official_decision"].get("classification_status"),
                "ratified_baseline_regime": None,
            }
        elif trigger_id == "GENUINELY_NEW_MEASUREMENT_SOURCE":
            item["observed"] = {
                "audit_source_count": len(inventory["sources"]),
                "ratified_strategy_measurement_family_count": None,
            }
        triggers.append(item)

    packet = {
        "schema_version": SCHEMA_VERSION,
        "as_of": regime["generated_at"],
        "wbs_binding": copy.deepcopy(locked["wbs_binding"]),
        "source": {
            "public_code_commit_sha": commit,
            "contract_sha256": payload_sha256(locked),
            "crypto_regime_status_sha256": regime["payload_sha256"],
            "source_inventory_sha256": payload_sha256(inventory),
            "source_inventory_version": inventory["registry_version"],
        },
        "locked_state": copy.deepcopy(locked["locked_state"]),
        "triggers": triggers,
        "summary": {
            "trigger_count": 4,
            "proven_trigger_count": 0,
            "rereview_gate": "CLOSED_NO_CANONICAL_TRIGGER_PROVEN",
            "candidate": "NONE",
            "live_engine_count": 0,
        },
        "mechanism_qualification": {
            "status": "NOT_EVALUATED_TRIGGER_NOT_PROVEN",
            "answers": {question: None for question in locked["mechanism_qualification"]["questions"]},
            "answered_count": 0,
            "required_answer_count": 4,
        },
        "event_study": {
            "status": "NOT_EVALUATED_TRIGGER_NOT_PROVEN",
            "passed_gate_count": 0,
            "required_pass_count": 7,
            "candidate_count": 0,
        },
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return packet


def validate_readiness(packet: dict, **inputs) -> dict:
    expected = build_readiness(**inputs)
    if packet != expected:
        fail("READINESS_REDERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_output(packet: dict, path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        fail("TRACKED_OUTPUT_FORBIDDEN", str(path))
    encoded = (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_atomic(resolved, encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--regime-status", type=Path, default=DEFAULT_REGIME_STATUS)
    parser.add_argument("--source-inventory", type=Path, default=DEFAULT_SOURCE_INVENTORY)
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    contract = load_contract(args.contract)
    regime = _load_json(args.regime_status, "REGIME_STATUS_INVALID")
    inventory = _load_json(args.source_inventory, "SOURCE_INVENTORY_INVALID")
    packet = build_readiness(
        contract,
        regime,
        inventory,
        source_commit=args.source_commit,
    )
    validate_readiness(
        packet,
        contract=contract,
        regime_status=regime,
        source_inventory=inventory,
        source_commit=args.source_commit,
    )
    write_output(packet, args.out)
    print(json.dumps(packet["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
