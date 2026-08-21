#!/usr/bin/env python3
"""P10-04 exact Unified Decision change lineage recorder."""
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
CONTRACT_PATH = ROOT / "config" / "decision_change_lineage_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


def _load_unified_validator():
    path = ROOT / "decision" / "unified_decision_contract.py"
    spec = importlib.util.spec_from_file_location(
        "atlas_decision_change_unified", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"UNIFIED_VALIDATOR_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


UNIFIED = _load_unified_validator()


class DecisionChangeLineageError(ValueError):
    """Fail-closed P10-04 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionChangeLineageError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 2,
        "contract_version": "decision_change_lineage/2",
        "snapshot_schema_version": "decision_snapshot_reference/2",
        "claim_batch_schema_version": "decision_change_claim_batch/2",
        "output_schema_version": "decision_change_lineage_packet/2",
        "unified_decision_schema_version": "unified_daily_decision/1",
        "unified_decision_contract_version": "unified_decision_contract/1",
        "markets": ["COMMON"],
        "decision_key": "ATLAS.UNIFIED.DAILY",
        "subject_id": "ATLAS.UNIFIED.THREE_MARKET",
        "change_types": ["CREATED", "UNCHANGED", "CHANGED", "RETIRED"],
        "reason_code_pattern": "^[A-Z][A-Z0-9_]*$",
        "changed_evidence_policy": "REASON_AND_EVIDENCE_REQUIRED",
        "unchanged_evidence_policy": "REASON_AND_EVIDENCE_MUST_BE_EMPTY",
        "lineage_policy": "PRIOR_MUST_EQUAL_PREVIOUS_CURRENT_WITHIN_BATCH",
        "decision_payload_binding": "EXACT_VALIDATED_UNIFIED_DECISION_PACKET",
        "source_sha_semantics": "MUST_EQUAL_UNIFIED_DECISION_PACKET_SHA256",
        "repository_decision_contract": "unified_decision_contract/1_VALIDATED",
        "input_authority": {
            "change_claim_observation_only": True,
            "decision_creation_authorized": False,
            "decision_change_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "lineage_recording_only": True,
            "decision_interpretation_authorized": False,
            "decision_creation_authorized": False,
            "decision_change_authorized": False,
            "candidate_promotion_authorized": False,
            "action_generation_authorized": False,
            "order_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise DecisionChangeLineageError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise DecisionChangeLineageError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise DecisionChangeLineageError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise DecisionChangeLineageError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise DecisionChangeLineageError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise DecisionChangeLineageError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise DecisionChangeLineageError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DecisionChangeLineageError(code)
    return value


def _digest(value: dict, field: str, code: str) -> str:
    digest = _sha(value.get(field), code)
    normalized = copy.deepcopy(value)
    normalized.pop(field)
    if payload_sha256(normalized) != digest:
        raise DecisionChangeLineageError(f"{code}_MISMATCH")
    return digest


def _validate_snapshot(
    value: object,
    decision_key: str,
    market: str,
    subject_id: str,
    change_time: dt.datetime,
    contract: dict,
    context: str,
) -> dict | None:
    if value is None:
        return None
    fields = {
        "schema_version", "decision_key", "market", "subject_id", "decided_at",
        "decision_sha256", "source_ref", "source_sha256", "decision_packet",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise DecisionChangeLineageError(f"SNAPSHOT_FIELDS_MISMATCH:{context}")
    if (
        value.get("schema_version") != contract["snapshot_schema_version"]
        or value.get("decision_key") != decision_key
        or value.get("market") != market
        or value.get("subject_id") != subject_id
    ):
        raise DecisionChangeLineageError(f"SNAPSHOT_IDENTITY_MISMATCH:{context}")
    decided = _utc(value.get("decided_at"), f"SNAPSHOT_TIME_INVALID:{context}")
    if decided > change_time:
        raise DecisionChangeLineageError(f"SNAPSHOT_FROM_FUTURE:{context}")
    try:
        decision_packet = UNIFIED.validate_packet(value.get("decision_packet"))
    except Exception as exc:
        raise DecisionChangeLineageError(
            f"UNIFIED_DECISION_INVALID:{context}:{exc}"
        ) from exc
    if (
        decision_packet.get("schema_version")
        != contract["unified_decision_schema_version"]
        or decision_packet.get("contract_version")
        != contract["unified_decision_contract_version"]
    ):
        raise DecisionChangeLineageError(
            f"UNIFIED_DECISION_IDENTITY_INVALID:{context}"
        )
    decision_sha = _sha(
        value.get("decision_sha256"), f"DECISION_SHA_INVALID:{context}"
    )
    source_sha = _sha(
        value.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"
    )
    if decision_sha != decision_packet["packet_sha256"]:
        raise DecisionChangeLineageError(f"DECISION_PACKET_SHA_MISMATCH:{context}")
    if source_sha != decision_packet["packet_sha256"]:
        raise DecisionChangeLineageError(f"SOURCE_PACKET_SHA_MISMATCH:{context}")
    if value["decided_at"] != decision_packet["generated_at"]:
        raise DecisionChangeLineageError(f"DECISION_PACKET_TIME_MISMATCH:{context}")
    return {
        "schema_version": contract["snapshot_schema_version"],
        "decision_key": decision_key,
        "market": market,
        "subject_id": subject_id,
        "decided_at": value["decided_at"],
        "decision_sha256": decision_sha,
        "source_ref": _text(value.get("source_ref"), f"SOURCE_REF_INVALID:{context}"),
        "source_sha256": source_sha,
        "decision_packet": decision_packet,
    }


def _derive_change_type(prior: dict | None, current: dict | None) -> str:
    if prior is None and current is None:
        raise DecisionChangeLineageError("SNAPSHOT_PAIR_EMPTY")
    if prior is None:
        return "CREATED"
    if current is None:
        return "RETIRED"
    if prior["decision_sha256"] == current["decision_sha256"]:
        return "UNCHANGED"
    return "CHANGED"


def _validate_reasons(value: object, contract: dict, context: str) -> list[str]:
    if not isinstance(value, list):
        raise DecisionChangeLineageError(f"REASONS_NOT_LIST:{context}")
    pattern = re.compile(contract["reason_code_pattern"])
    if (
        any(not isinstance(item, str) or pattern.fullmatch(item) is None for item in value)
        or value != sorted(set(value))
    ):
        raise DecisionChangeLineageError(f"REASONS_INVALID:{context}")
    return list(value)


def _validate_evidence(
    value: object, change_time: dt.datetime, context: str
) -> list[dict]:
    if not isinstance(value, list):
        raise DecisionChangeLineageError(f"EVIDENCE_NOT_LIST:{context}")
    fields = {"evidence_id", "uri", "available_at", "source_sha256"}
    rows = []
    for index, row in enumerate(value):
        label = f"{context}:{index}"
        if not isinstance(row, dict) or set(row) != fields:
            raise DecisionChangeLineageError(f"EVIDENCE_FIELDS_MISMATCH:{label}")
        available = _utc(row.get("available_at"), f"EVIDENCE_TIME_INVALID:{label}")
        if available > change_time:
            raise DecisionChangeLineageError(f"EVIDENCE_FROM_FUTURE:{label}")
        rows.append({
            "evidence_id": _token(row.get("evidence_id"), f"EVIDENCE_ID_INVALID:{label}"),
            "uri": _text(row.get("uri"), f"EVIDENCE_URI_INVALID:{label}"),
            "available_at": row["available_at"],
            "source_sha256": _sha(
                row.get("source_sha256"), f"EVIDENCE_SHA_INVALID:{label}"
            ),
        })
    rows.sort(key=lambda row: row["evidence_id"])
    ids = [row["evidence_id"] for row in rows]
    if ids != sorted(set(ids)):
        raise DecisionChangeLineageError(f"EVIDENCE_ID_DUPLICATE:{context}")
    return rows


def _validate_batch(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "batch_id", "observed_at",
        "claims", "authority", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise DecisionChangeLineageError("BATCH_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["claim_batch_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
    ):
        raise DecisionChangeLineageError("BATCH_IDENTITY_INVALID")
    batch_id = _token(value.get("batch_id"), "BATCH_ID_INVALID")
    observed = _utc(value.get("observed_at"), "BATCH_OBSERVED_AT_INVALID")
    raw_claims = value.get("claims")
    if not isinstance(raw_claims, list):
        raise DecisionChangeLineageError("CLAIMS_NOT_LIST")
    claim_fields = {
        "decision_key", "market", "subject_id", "change_observed_at",
        "prior_snapshot", "current_snapshot", "reason_codes", "evidence",
    }
    claims = []
    for index, claim in enumerate(raw_claims):
        context = f"claim:{index}"
        if not isinstance(claim, dict) or set(claim) != claim_fields:
            raise DecisionChangeLineageError(f"CLAIM_FIELDS_MISMATCH:{context}")
        decision_key = _token(
            claim.get("decision_key"), f"DECISION_KEY_INVALID:{context}"
        )
        market = claim.get("market")
        if market not in contract["markets"]:
            raise DecisionChangeLineageError(f"MARKET_INVALID:{context}:{market}")
        subject_id = _token(claim.get("subject_id"), f"SUBJECT_ID_INVALID:{context}")
        if (
            decision_key != contract["decision_key"]
            or market != "COMMON"
            or subject_id != contract["subject_id"]
        ):
            raise DecisionChangeLineageError(f"DECISION_SCOPE_INVALID:{context}")
        change_time = _utc(
            claim.get("change_observed_at"), f"CHANGE_TIME_INVALID:{context}"
        )
        if change_time > observed:
            raise DecisionChangeLineageError(f"CHANGE_FROM_FUTURE:{context}")
        prior = _validate_snapshot(
            claim.get("prior_snapshot"), decision_key, market, subject_id,
            change_time, contract, f"{context}:prior",
        )
        current = _validate_snapshot(
            claim.get("current_snapshot"), decision_key, market, subject_id,
            change_time, contract, f"{context}:current",
        )
        if prior is not None and current is not None and (
            _utc(prior["decided_at"], "PRIOR_TIME_INVALID")
            > _utc(current["decided_at"], "CURRENT_TIME_INVALID")
        ):
            raise DecisionChangeLineageError(f"SNAPSHOT_TIME_REVERSED:{context}")
        change_type = _derive_change_type(prior, current)
        reasons = _validate_reasons(claim.get("reason_codes"), contract, context)
        evidence = _validate_evidence(claim.get("evidence"), change_time, context)
        if change_type == "UNCHANGED":
            if reasons or evidence:
                raise DecisionChangeLineageError(f"UNCHANGED_HAS_REASON_OR_EVIDENCE:{context}")
        elif not reasons or not evidence:
            raise DecisionChangeLineageError(f"CHANGE_REASON_EVIDENCE_REQUIRED:{context}")
        claims.append({
            "decision_key": decision_key,
            "market": market,
            "subject_id": subject_id,
            "change_observed_at": claim["change_observed_at"],
            "prior_snapshot": prior,
            "current_snapshot": current,
            "change_type": change_type,
            "reason_codes": reasons,
            "evidence": evidence,
        })
    claims.sort(key=lambda row: (row["decision_key"], row["change_observed_at"]))
    keys = [(row["decision_key"], row["change_observed_at"]) for row in claims]
    if keys != sorted(set(keys)):
        raise DecisionChangeLineageError("CLAIM_IDENTITY_DUPLICATE")
    previous_by_key = {}
    for claim in claims:
        key = claim["decision_key"]
        if key in previous_by_key and claim["prior_snapshot"] != previous_by_key[key]:
            raise DecisionChangeLineageError(f"CLAIM_CHAIN_BROKEN:{key}")
        previous_by_key[key] = claim["current_snapshot"]
    _digest(value, "packet_sha256", "BATCH_SHA_INVALID")
    semantic = {
        "schema_version": contract["claim_batch_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": batch_id,
        "observed_at": value["observed_at"],
        "claims": claims,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    normalized_claims = []
    for claim in claims:
        normalized_claim = copy.deepcopy(claim)
        normalized_claim.pop("change_type")
        normalized_claims.append(normalized_claim)
    normalized_packet = {
        "schema_version": contract["claim_batch_schema_version"],
        "contract_version": contract["contract_version"],
        "batch_id": batch_id,
        "observed_at": value["observed_at"],
        "claims": normalized_claims,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    normalized_packet["packet_sha256"] = payload_sha256(normalized_packet)
    return {
        "batch_id": batch_id,
        "observed_at": value["observed_at"],
        "claims": claims,
        "semantic_sha256": payload_sha256(semantic),
        "normalized_packet": normalized_packet,
    }


def build_lineage(claim_batch: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    batch = _validate_batch(claim_batch, contract)
    counts = {change_type: 0 for change_type in contract["change_types"]}
    entries = []
    for claim in batch["claims"]:
        counts[claim["change_type"]] += 1
        entry = copy.deepcopy(claim)
        entry["decision_payload"] = None
        entry["decision_interpretation"] = None
        entry["action"] = None
        entry["entry_sha256"] = payload_sha256(entry)
        entries.append(entry)
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "DECISION_CHANGE_LINEAGE_RECORDED_NO_DECISION_AUTHORITY",
        "batch_id": batch["batch_id"],
        "observed_at": batch["observed_at"],
        "summary": {
            "entry_count": len(entries),
            **{f"{key.lower()}_count": counts[key] for key in contract["change_types"]},
            "decisions_created": 0,
            "decisions_changed": 0,
            "actions_created": 0,
        },
        "entries": entries,
        "lineage": {"claim_batch_sha256": batch["semantic_sha256"]},
        "source_claim_batch": copy.deepcopy(batch["normalized_packet"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "LIVE_DECISION_LINEAGE_WIRING_NOT_IMPLEMENTED",
            "DECISION_INTERPRETATION_NOT_AUTHORIZED",
            "SHADOW_LEDGER_LINEAGE_WIRING_NOT_IMPLEMENTED",
            "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_output(packet, contract)


def validate_output(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "batch_id", "observed_at",
        "summary", "entries", "lineage", "authority", "unresolved_boundaries",
        "source_claim_batch", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise DecisionChangeLineageError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status")
        != "DECISION_CHANGE_LINEAGE_RECORDED_NO_DECISION_AUTHORITY"
        or packet.get("authority") != contract["authority"]
    ):
        raise DecisionChangeLineageError("OUTPUT_IDENTITY_INVALID")
    _token(packet.get("batch_id"), "OUTPUT_BATCH_ID_INVALID")
    observed = _utc(packet.get("observed_at"), "OUTPUT_OBSERVED_AT_INVALID")
    source_batch = packet.get("source_claim_batch")
    source = _validate_batch(source_batch, contract)
    if (
        packet["batch_id"] != source["batch_id"]
        or packet["observed_at"] != source["observed_at"]
    ):
        raise DecisionChangeLineageError("OUTPUT_SOURCE_IDENTITY_MISMATCH")
    entries = packet.get("entries")
    if not isinstance(entries, list):
        raise DecisionChangeLineageError("OUTPUT_ENTRIES_NOT_LIST")
    entry_fields = {
        "decision_key", "market", "subject_id", "change_observed_at",
        "prior_snapshot", "current_snapshot", "change_type", "reason_codes",
        "evidence", "decision_payload", "decision_interpretation", "action",
        "entry_sha256",
    }
    counts = {change_type: 0 for change_type in contract["change_types"]}
    keys = []
    previous_by_key = {}
    for index, entry in enumerate(entries):
        context = f"entry:{index}"
        if not isinstance(entry, dict) or set(entry) != entry_fields:
            raise DecisionChangeLineageError(f"OUTPUT_ENTRY_FIELDS_MISMATCH:{context}")
        decision_key = _token(
            entry.get("decision_key"), f"OUTPUT_DECISION_KEY_INVALID:{context}"
        )
        market = entry.get("market")
        if market not in contract["markets"]:
            raise DecisionChangeLineageError(f"OUTPUT_MARKET_INVALID:{context}")
        subject_id = _token(entry.get("subject_id"), f"OUTPUT_SUBJECT_INVALID:{context}")
        change_time = _utc(
            entry.get("change_observed_at"), f"OUTPUT_CHANGE_TIME_INVALID:{context}"
        )
        if change_time > observed:
            raise DecisionChangeLineageError(f"OUTPUT_CHANGE_FROM_FUTURE:{context}")
        prior = _validate_snapshot(
            entry.get("prior_snapshot"), decision_key, market, subject_id,
            change_time, contract, f"{context}:prior",
        )
        current = _validate_snapshot(
            entry.get("current_snapshot"), decision_key, market, subject_id,
            change_time, contract, f"{context}:current",
        )
        if prior is not None and current is not None and (
            _utc(prior["decided_at"], "OUTPUT_PRIOR_TIME_INVALID")
            > _utc(current["decided_at"], "OUTPUT_CURRENT_TIME_INVALID")
        ):
            raise DecisionChangeLineageError(
                f"OUTPUT_SNAPSHOT_TIME_REVERSED:{context}"
            )
        change_type = _derive_change_type(prior, current)
        reasons = _validate_reasons(entry.get("reason_codes"), contract, context)
        evidence = _validate_evidence(entry.get("evidence"), change_time, context)
        if entry.get("change_type") != change_type:
            raise DecisionChangeLineageError(f"OUTPUT_CHANGE_TYPE_INVALID:{context}")
        if change_type == "UNCHANGED":
            if reasons or evidence:
                raise DecisionChangeLineageError(f"OUTPUT_UNCHANGED_EVIDENCE_INVALID:{context}")
        elif not reasons or not evidence:
            raise DecisionChangeLineageError(f"OUTPUT_CHANGE_EVIDENCE_REQUIRED:{context}")
        if (
            entry.get("decision_payload") is not None
            or entry.get("decision_interpretation") is not None
            or entry.get("action") is not None
        ):
            raise DecisionChangeLineageError(f"OUTPUT_AUTHORITY_EXPANSION:{context}")
        digest = _sha(entry.get("entry_sha256"), f"OUTPUT_ENTRY_SHA_INVALID:{context}")
        normalized = copy.deepcopy(entry)
        normalized.pop("entry_sha256")
        if payload_sha256(normalized) != digest:
            raise DecisionChangeLineageError(f"OUTPUT_ENTRY_SHA_MISMATCH:{context}")
        key = (decision_key, entry["change_observed_at"])
        keys.append(key)
        if decision_key in previous_by_key and prior != previous_by_key[decision_key]:
            raise DecisionChangeLineageError(f"OUTPUT_CHAIN_BROKEN:{decision_key}")
        previous_by_key[decision_key] = current
        counts[change_type] += 1
    if keys != sorted(set(keys)):
        raise DecisionChangeLineageError("OUTPUT_ENTRY_ORDER_INVALID")
    expected_summary = {
        "entry_count": len(entries),
        **{f"{key.lower()}_count": counts[key] for key in contract["change_types"]},
        "decisions_created": 0,
        "decisions_changed": 0,
        "actions_created": 0,
    }
    if packet.get("summary") != expected_summary:
        raise DecisionChangeLineageError("OUTPUT_SUMMARY_INVALID")
    expected_entries = []
    for claim in source["claims"]:
        expected_entry = copy.deepcopy(claim)
        expected_entry["decision_payload"] = None
        expected_entry["decision_interpretation"] = None
        expected_entry["action"] = None
        expected_entry["entry_sha256"] = payload_sha256(expected_entry)
        expected_entries.append(expected_entry)
    if entries != expected_entries:
        raise DecisionChangeLineageError("OUTPUT_SOURCE_DERIVATION_MISMATCH")
    lineage = packet.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {"claim_batch_sha256"}:
        raise DecisionChangeLineageError("OUTPUT_LINEAGE_INVALID")
    if (
        _sha(lineage.get("claim_batch_sha256"), "OUTPUT_LINEAGE_SHA_INVALID")
        != source["semantic_sha256"]
    ):
        raise DecisionChangeLineageError("OUTPUT_LINEAGE_SHA_MISMATCH")
    expected_boundaries = [
        "LIVE_DECISION_LINEAGE_WIRING_NOT_IMPLEMENTED",
        "DECISION_INTERPRETATION_NOT_AUTHORIZED",
        "SHADOW_LEDGER_LINEAGE_WIRING_NOT_IMPLEMENTED",
        "PRODUCTION_NOT_AUTHORIZED",
    ]
    if packet.get("unresolved_boundaries") != expected_boundaries:
        raise DecisionChangeLineageError("OUTPUT_BOUNDARIES_INVALID")
    digest = _sha(packet.get("packet_sha256"), "OUTPUT_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise DecisionChangeLineageError("OUTPUT_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise DecisionChangeLineageError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(batch_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_lineage(_read_json(batch_path)))
        return 0
    except (DecisionChangeLineageError, OSError, TypeError, ValueError) as exc:
        print(f"Decision change lineage failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record exact Unified Decision change lineage"
    )
    parser.add_argument("claim_batch", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.claim_batch, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
