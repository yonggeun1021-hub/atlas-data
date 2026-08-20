#!/usr/bin/env python3
"""P7-01 explicit-only Portfolio bucket membership registry validator."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "bucket_membership_contract.json"
CONSTITUTION_PATH = ROOT / "config" / "constitution.json"
INPUT_SCHEMA_VERSION = "bucket_assignment_set/1"
OUTPUT_SCHEMA_VERSION = "bucket_membership_packet/1"
ASSET_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,95}$")
BUCKET_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class BucketMembershipError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BucketMembershipError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "bucket_membership/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "canonical_constitution": "config/constitution.json",
        "repository_default_status": "BLOCKED_UNTIL_CONSTITUTION_B1_RATIFIED",
        "assignment_mode": "EXPLICIT_RATIFIED_ONLY",
        "bucket_definition_binding": "OPAQUE_B1_SHA256_EXACT",
        "effective_interval": "[valid_from, valid_to)",
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "allowed_subject_kinds": ["CANDIDATE", "HOLDING"],
        "required_assignment_lineage": [
            "asset_identity_sha256",
            "discovery_result_sha256",
            "rule_result_sha256",
            "holding_record_sha256",
            "assignment_basis_ref",
            "assignment_basis_sha256",
        ],
        "input_authority": {
            "membership_assignment_authorized": True,
            "automatic_assignment_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "membership_registry_validation_only": True,
            "repository_default_policy_authorized": False,
            "automatic_assignment_authorized": False,
            "bucket_limit_authorized": False,
            "position_sizing_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise BucketMembershipError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise BucketMembershipError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _date(value, code: str) -> str:
    if not isinstance(value, str):
        raise BucketMembershipError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise BucketMembershipError(code) from exc
    if parsed.isoformat() != value:
        raise BucketMembershipError(code)
    return value


def _utc(value, code: str) -> str:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise BucketMembershipError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise BucketMembershipError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise BucketMembershipError(code)
    return value


def _sha(value, code: str, nullable: bool = False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BucketMembershipError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise BucketMembershipError(code)
    return value


def _interval(start, end, context: str) -> tuple[str, str | None]:
    start = _date(start, f"VALID_FROM_INVALID:{context}")
    if end is not None:
        end = _date(end, f"VALID_TO_INVALID:{context}")
        if end <= start:
            raise BucketMembershipError(f"EFFECTIVE_INTERVAL_EMPTY:{context}")
    return start, end


def _active(start: str, end: str | None, as_of_date: str) -> bool:
    return start <= as_of_date and (end is None or as_of_date < end)


def _overlap(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def _validate_constitution(value: dict) -> dict:
    required = {
        "status", "ratified_at", "constitution_version", "B1_bucket_definition",
        "B2_cash_floor_pct", "B3_bucket_max_pct", "B4_position_max_pct",
        "B5_stop_loss_pct", "B6_portfolio_max_loss_pct",
        "B7_evidence_state_max_pct", "amendment_log",
    }
    if not isinstance(value, dict) or not required.issubset(value):
        raise BucketMembershipError("CONSTITUTION_FIELDS_MISSING")
    if value.get("status") != "ratified":
        raise BucketMembershipError("CONSTITUTION_NOT_RATIFIED")
    _utc(value.get("ratified_at"), "CONSTITUTION_RATIFIED_AT_INVALID")
    _text(value.get("constitution_version"), "CONSTITUTION_VERSION_INVALID")
    if value.get("B1_bucket_definition") is None:
        raise BucketMembershipError("CONSTITUTION_B1_NOT_RATIFIED")
    return copy.deepcopy(value)


def _validate_bucket(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) != {
        "bucket_id", "definition_ref", "definition_sha256"
    }:
        raise BucketMembershipError("BUCKET_FIELDS_MISMATCH")
    bucket_id = row.get("bucket_id")
    if not isinstance(bucket_id, str) or BUCKET_ID_RE.fullmatch(bucket_id) is None:
        raise BucketMembershipError(f"BUCKET_ID_INVALID:{bucket_id}")
    return {
        "bucket_id": bucket_id,
        "definition_ref": _text(row.get("definition_ref"), f"BUCKET_REF_INVALID:{bucket_id}"),
        "definition_sha256": _sha(
            row.get("definition_sha256"), f"BUCKET_DEFINITION_SHA_INVALID:{bucket_id}"
        ),
    }


def _validate_assignment(row: dict, bucket_ids: set[str], contract: dict) -> dict:
    fields = {
        "asset_id", "subject_kind", "market", "bucket_id", "valid_from",
        "valid_to", "asset_identity_sha256", "discovery_result_sha256",
        "rule_result_sha256", "holding_record_sha256", "assignment_basis_ref",
        "assignment_basis_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise BucketMembershipError("ASSIGNMENT_FIELDS_MISMATCH")
    asset_id = row.get("asset_id")
    if not isinstance(asset_id, str) or ASSET_ID_RE.fullmatch(asset_id) is None:
        raise BucketMembershipError(f"ASSET_ID_INVALID:{asset_id}")
    kind = row.get("subject_kind")
    market = row.get("market")
    bucket_id = row.get("bucket_id")
    if kind not in contract["allowed_subject_kinds"]:
        raise BucketMembershipError(f"SUBJECT_KIND_INVALID:{asset_id}:{kind}")
    if market not in contract["allowed_markets"]:
        raise BucketMembershipError(f"MARKET_INVALID:{asset_id}:{market}")
    if bucket_id not in bucket_ids:
        raise BucketMembershipError(f"BUCKET_UNKNOWN:{asset_id}:{bucket_id}")
    start, end = _interval(row.get("valid_from"), row.get("valid_to"), asset_id)
    discovery = _sha(
        row.get("discovery_result_sha256"),
        f"DISCOVERY_SHA_INVALID:{asset_id}",
        nullable=True,
    )
    holding = _sha(
        row.get("holding_record_sha256"),
        f"HOLDING_SHA_INVALID:{asset_id}",
        nullable=True,
    )
    if kind == "CANDIDATE" and (discovery is None or holding is not None):
        raise BucketMembershipError(f"CANDIDATE_LINEAGE_INVALID:{asset_id}")
    if kind == "HOLDING" and holding is None:
        raise BucketMembershipError(f"HOLDING_LINEAGE_INVALID:{asset_id}")
    return {
        "asset_id": asset_id,
        "subject_kind": kind,
        "market": market,
        "bucket_id": bucket_id,
        "valid_from": start,
        "valid_to": end,
        "asset_identity_sha256": _sha(
            row.get("asset_identity_sha256"), f"ASSET_IDENTITY_SHA_INVALID:{asset_id}"
        ),
        "discovery_result_sha256": discovery,
        "rule_result_sha256": _sha(
            row.get("rule_result_sha256"), f"RULE_RESULT_SHA_INVALID:{asset_id}"
        ),
        "holding_record_sha256": holding,
        "assignment_basis_ref": _text(
            row.get("assignment_basis_ref"), f"ASSIGNMENT_BASIS_REF_INVALID:{asset_id}"
        ),
        "assignment_basis_sha256": _sha(
            row.get("assignment_basis_sha256"),
            f"ASSIGNMENT_BASIS_SHA_INVALID:{asset_id}",
        ),
    }


def _validate_assignment_set(
    value: dict,
    constitution: dict,
    as_of_date: str,
    contract: dict,
) -> dict:
    fields = {
        "schema_version", "contract_version", "assignment_set_id", "status",
        "ratified_by", "ratified_at", "valid_from", "valid_to",
        "constitution_version", "constitution_sha256",
        "b1_bucket_definition_sha256", "buckets", "assignments", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise BucketMembershipError("ASSIGNMENT_SET_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != contract["input_authority"]
    ):
        raise BucketMembershipError("ASSIGNMENT_SET_IDENTITY_INVALID")
    assignment_set_id = _text(
        value.get("assignment_set_id"), "ASSIGNMENT_SET_ID_INVALID"
    )
    ratified_at = _utc(value.get("ratified_at"), "ASSIGNMENT_RATIFIED_AT_INVALID")
    set_start, set_end = _interval(
        value.get("valid_from"), value.get("valid_to"), assignment_set_id
    )
    if ratified_at[:10] > set_start:
        raise BucketMembershipError("ASSIGNMENT_RATIFIED_AFTER_EFFECTIVE_START")
    if not _active(set_start, set_end, as_of_date):
        raise BucketMembershipError("ASSIGNMENT_SET_NOT_EFFECTIVE")
    if value.get("constitution_version") != constitution["constitution_version"]:
        raise BucketMembershipError("CONSTITUTION_VERSION_MISMATCH")
    if value.get("constitution_sha256") != payload_sha256(constitution):
        raise BucketMembershipError("CONSTITUTION_SHA_MISMATCH")
    if value.get("b1_bucket_definition_sha256") != payload_sha256(
        constitution["B1_bucket_definition"]
    ):
        raise BucketMembershipError("CONSTITUTION_B1_SHA_MISMATCH")

    raw_buckets = value.get("buckets")
    raw_assignments = value.get("assignments")
    if not isinstance(raw_buckets, list) or not raw_buckets:
        raise BucketMembershipError("BUCKETS_EMPTY")
    if not isinstance(raw_assignments, list) or not raw_assignments:
        raise BucketMembershipError("ASSIGNMENTS_EMPTY")
    buckets = sorted((_validate_bucket(row) for row in raw_buckets), key=lambda row: row["bucket_id"])
    bucket_ids = [row["bucket_id"] for row in buckets]
    if len(bucket_ids) != len(set(bucket_ids)):
        raise BucketMembershipError("BUCKET_ID_DUPLICATE")
    assignments = sorted(
        (
            _validate_assignment(row, set(bucket_ids), contract)
            for row in raw_assignments
        ),
        key=lambda row: (row["asset_id"], row["valid_from"], row["bucket_id"]),
    )
    groups: dict[str, list[dict]] = {}
    identities: dict[str, str] = {}
    for row in assignments:
        if row["valid_from"] < set_start or (
            set_end is not None
            and (row["valid_to"] is None or row["valid_to"] > set_end)
        ):
            raise BucketMembershipError(f"ASSIGNMENT_OUTSIDE_SET_INTERVAL:{row['asset_id']}")
        owner = identities.setdefault(row["asset_identity_sha256"], row["asset_id"])
        if owner != row["asset_id"]:
            raise BucketMembershipError(
                f"ASSET_IDENTITY_COLLISION:{owner}:{row['asset_id']}"
            )
        groups.setdefault(row["asset_id"], []).append(row)
    active = []
    for asset_id, rows in sorted(groups.items()):
        identity = {
            (row["subject_kind"], row["market"], row["asset_identity_sha256"])
            for row in rows
        }
        if len(identity) != 1:
            raise BucketMembershipError(f"SUBJECT_IDENTITY_DRIFT:{asset_id}")
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                if _overlap(
                    left["valid_from"], left["valid_to"],
                    right["valid_from"], right["valid_to"],
                ):
                    raise BucketMembershipError(f"BUCKET_ASSIGNMENT_OVERLAP:{asset_id}")
        current = [
            row for row in rows
            if _active(row["valid_from"], row["valid_to"], as_of_date)
        ]
        if len(current) != 1:
            raise BucketMembershipError(f"ACTIVE_MEMBERSHIP_COUNT_INVALID:{asset_id}")
        active.append(copy.deepcopy(current[0]))

    normalized = {
        "schema_version": contract["input_schema_version"],
        "contract_version": contract["contract_version"],
        "assignment_set_id": assignment_set_id,
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": ratified_at,
        "valid_from": set_start,
        "valid_to": set_end,
        "constitution_version": constitution["constitution_version"],
        "constitution_sha256": value["constitution_sha256"],
        "b1_bucket_definition_sha256": value["b1_bucket_definition_sha256"],
        "buckets": buckets,
        "assignments": assignments,
        "authority": copy.deepcopy(contract["input_authority"]),
    }
    digest = _sha(value.get("packet_sha256"), "ASSIGNMENT_SET_SHA_INVALID")
    if payload_sha256(normalized) != digest:
        raise BucketMembershipError("ASSIGNMENT_SET_SHA_MISMATCH")
    return {"normalized": normalized, "packet_sha256": digest, "active": active}


def build_packet(
    assignment_set: dict,
    constitution: dict,
    as_of_date: str,
    contract: dict | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    as_of_date = _date(as_of_date, "AS_OF_DATE_INVALID")
    constitution = _validate_constitution(constitution)
    checked = _validate_assignment_set(
        assignment_set, constitution, as_of_date, contract
    )
    normalized = checked["normalized"]
    counts = {market: 0 for market in contract["allowed_markets"]}
    kinds = {kind: 0 for kind in contract["allowed_subject_kinds"]}
    for row in checked["active"]:
        counts[row["market"]] += 1
        kinds[row["subject_kind"]] += 1
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "MEMBERSHIP_VALIDATED_EXPLICIT_ONLY",
        "as_of_date": as_of_date,
        "assignment_set_id": normalized["assignment_set_id"],
        "summary": {
            "bucket_count": len(normalized["buckets"]),
            "subject_count": len(checked["active"]),
            "active_membership_count": len(checked["active"]),
            "by_market": counts,
            "by_subject_kind": kinds,
        },
        "bucket_definitions": copy.deepcopy(normalized["buckets"]),
        "assignment_history": copy.deepcopy(normalized["assignments"]),
        "active_memberships": copy.deepcopy(checked["active"]),
        "lineage": {
            "assignment_set_sha256": checked["packet_sha256"],
            "constitution_sha256": normalized["constitution_sha256"],
            "b1_bucket_definition_sha256": normalized["b1_bucket_definition_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REPOSITORY_DEFAULT_CONSTITUTION_B1_NOT_RATIFIED",
            "BUCKET_LIMITS_NOT_AUTHORIZED",
            "POSITION_SIZING_NOT_AUTHORIZED",
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
        raise BucketMembershipError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
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


def run(
    assignment_set_path: Path,
    constitution_path: Path,
    as_of_date: str,
    output_path: Path,
) -> int:
    try:
        packet = build_packet(
            _read_json(assignment_set_path),
            _read_json(constitution_path),
            as_of_date,
        )
        write_json_atomic(output_path, packet)
        return 0
    except (BucketMembershipError, OSError, TypeError, ValueError) as exc:
        print(f"Bucket membership failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate explicit ratified Portfolio bucket membership"
    )
    parser.add_argument("assignment_set", type=Path)
    parser.add_argument("--constitution", type=Path, default=CONSTITUTION_PATH)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.assignment_set, args.constitution, args.as_of, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
