#!/usr/bin/env python3
"""P2-05 repository-evidence-only operational readiness inventory.

This command never accepts a caller-supplied state policy or ledger. It reports
whether the repository contains the three independently required inputs for an
operational state history: a full producer packet, an externally ratified state
policy, and append-only ledger evidence. A briefing pointer is lineage evidence,
not a substitute for the full packet or ledger.
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
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "rotation_state_ledger_operational_readiness_contract.json"
LEDGER_CONTRACT_PATH = ROOT / "config" / "rotation_state_ledger_contract.json"
SCHEMA_VERSION = "rotation_state_ledger_operational_readiness/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER = _load_module(
    "atlas_rotation_state_ledger_for_readiness",
    ROOT / "rotation" / "rotation_state_ledger.py",
)


class RotationStateLedgerReadinessError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationStateLedgerReadinessError(f"JSON_READ_FAILED:{path}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": SCHEMA_VERSION,
        "markets": ["US", "KOREA", "CRYPTO"],
        "ledger_contract_version": "rotation_state_ledger/1",
        "repository_default_state_policy": "ABSENT",
        "repository_operational_ledger_evidence": "ABSENT",
        "market_rotation_evidence": {
            "US": None,
            "KOREA": "data/latest_korea_rotation.json",
            "CRYPTO": None,
        },
        "readiness_requirement": [
            "FULL_PRODUCTION_ROTATION_PACKET",
            "EXTERNAL_RATIFIED_STATE_POLICY",
            "APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE",
        ],
        "authority": {
            "readiness_inventory_only": True,
            "p2_state_vocabulary_authorized": False,
            "state_ledger_authorized": False,
            "regime_input_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "briefing_wiring_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _load_contract(root: Path) -> dict:
    value = _read_json(root / "config" / CONTRACT_PATH.name)
    if value != _expected_contract():
        raise RotationStateLedgerReadinessError("READINESS_CONTRACT_MISMATCH")
    return value


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise RotationStateLedgerReadinessError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise RotationStateLedgerReadinessError(code) from exc
    if parsed.isoformat() != value:
        raise RotationStateLedgerReadinessError(code)
    return value


def _timestamp(value, code: str) -> str:
    if not isinstance(value, str):
        raise RotationStateLedgerReadinessError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RotationStateLedgerReadinessError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RotationStateLedgerReadinessError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RotationStateLedgerReadinessError(code)
    return value


def _git(root: Path, *args: str, binary: bool = False):
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RotationStateLedgerReadinessError(
            "EVIDENCE_GIT_PROVENANCE_UNVERIFIED"
        ) from exc
    return completed.stdout if binary else completed.stdout.decode("utf-8")


def _verify_head_blob(path: Path, root: Path) -> str:
    path = Path(path).resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RotationStateLedgerReadinessError("EVIDENCE_PATH_INVALID") from exc
    if _git(root, "status", "--porcelain", "--", relative).strip():
        raise RotationStateLedgerReadinessError("EVIDENCE_WORKTREE_DIRTY")
    head = _git(root, "rev-parse", "HEAD").strip()
    committed = _git(root, "show", f"{head}:{relative}", binary=True)
    try:
        current = path.read_bytes()
    except OSError as exc:
        raise RotationStateLedgerReadinessError("EVIDENCE_MISSING") from exc
    if current != committed:
        raise RotationStateLedgerReadinessError("EVIDENCE_HEAD_BLOB_MISMATCH")
    return head


def _validate_korea_pointer(value: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "as_of_date", "generated_at",
        "run_status", "rotation", "breadth", "authority", "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RotationStateLedgerReadinessError("KOREA_POINTER_FIELDS_MISMATCH")
    digest = _sha(value.get("payload_sha256"), "KOREA_POINTER_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("payload_sha256")
    if payload_sha256(payload) != digest:
        raise RotationStateLedgerReadinessError("KOREA_POINTER_SHA_MISMATCH")
    rotation = value.get("rotation")
    authority = value.get("authority")
    if (
        value.get("schema_version") != "korea_rotation_briefing_pointer/3"
        or value.get("contract_version") != "korea_capital_rotation/4"
        or value.get("run_status") != "OK"
        or not isinstance(rotation, dict)
        or set(rotation) != {"status", "rotation_policy_effective", "packet_sha256"}
        or rotation.get("status") != "ROTATION_BUCKETS_OBSERVED"
        or rotation.get("rotation_policy_effective") is not True
        or not isinstance(authority, dict)
        or not authority
        or any(item is not False for item in authority.values())
    ):
        raise RotationStateLedgerReadinessError("KOREA_POINTER_SEMANTIC_INVALID")
    _date(value.get("as_of_date"), "KOREA_POINTER_DATE_INVALID")
    _timestamp(value.get("generated_at"), "KOREA_POINTER_TIME_INVALID")
    _sha(rotation.get("packet_sha256"), "KOREA_ROTATION_PACKET_SHA_INVALID")
    return copy.deepcopy(value)


def _market_row(market: str, contract: dict, root: Path) -> dict:
    pointer_rel = contract["market_rotation_evidence"][market]
    if pointer_rel is None:
        upstream_status = "ROTATION_EVIDENCE_NOT_COMMITTED"
        pointer_sha = rotation_packet_sha = rotation_as_of = None
        pointer_commit = None
    else:
        pointer_path = root / pointer_rel
        pointer_commit = _verify_head_blob(pointer_path, root)
        pointer = _validate_korea_pointer(_read_json(pointer_path))
        upstream_status = "POINTER_ONLY_FULL_ROTATION_PACKET_NOT_COMMITTED"
        pointer_sha = pointer["payload_sha256"]
        rotation_packet_sha = pointer["rotation"]["packet_sha256"]
        rotation_as_of = pointer["as_of_date"]
    blockers = [
        "FULL_PRODUCTION_ROTATION_PACKET_MISSING",
        "EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
        "APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
    ]
    return {
        "market": market,
        "readiness_status": "NOT_READY",
        "upstream_rotation_evidence_status": upstream_status,
        "upstream_pointer_path": pointer_rel,
        "upstream_pointer_commit": pointer_commit,
        "upstream_pointer_sha256": pointer_sha,
        "upstream_rotation_packet_sha256": rotation_packet_sha,
        "upstream_rotation_as_of_date": rotation_as_of,
        "state_policy_status": "ABSENT_BY_REPOSITORY_CONTRACT",
        "ledger_evidence_status": "ABSENT",
        "ledger_record_count": 0,
        "blockers": blockers,
    }


def build_readiness(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    contract = _load_contract(root)
    ledger_contract = LEDGER.load_contract(root / "config" / LEDGER_CONTRACT_PATH.name)
    if (
        ledger_contract["contract_version"] != contract["ledger_contract_version"]
        or ledger_contract["repository_default_policy"] != "ABSENT"
    ):
        raise RotationStateLedgerReadinessError("LEDGER_CONTRACT_BOUNDARY_MISMATCH")
    markets = [_market_row(market, contract, root) for market in contract["markets"]]
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "evidence_basis": "REPOSITORY_COMMITTED_EVIDENCE_ONLY",
        "overall_status": "BLOCKED_NO_MARKET_HAS_OPERATIONAL_STATE_HISTORY",
        "ready_market_count": 0,
        "required_market_count": len(markets),
        "markets": markets,
        "authority": copy.deepcopy(contract["authority"]),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_readiness(value: dict, root: Path = ROOT) -> dict:
    expected = build_readiness(root)
    if value != expected:
        raise RotationStateLedgerReadinessError("READINESS_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


def write_json_atomic(path: Path, value: dict, root: Path = ROOT) -> None:
    path = Path(path).resolve()
    try:
        path.relative_to(Path(root).resolve())
    except ValueError:
        pass
    else:
        raise RotationStateLedgerReadinessError("TRACKED_OUTPUT_FORBIDDEN")
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
        "rotation state ledger readiness: "
        f"status={packet['overall_status']} ready=0/{packet['required_market_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
