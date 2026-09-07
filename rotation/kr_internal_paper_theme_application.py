#!/usr/bin/env python3
"""Bounded two-stock KR internal-paper Theme application.

This module does not modify the global Theme graph, source allowlist, or
authority registry.  It verifies one independently approved source profile,
validates the existing Global Asset Master and Korea Leadership packets, and
emits an application-local membership view for exactly one observation date.
The view expires at the next KST midnight; evaluation and any supplied forward
execution timestamp must both fall inside the actual timestamp interval.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from identity import canonical_identity as CI
from rotation import korea_capital_rotation as KCR
from rotation import theme_taxonomy_authority as TTA


CONTRACT_PATH = ROOT / "config" / "kr_internal_paper_theme_application_contract.json"
REGISTRY_PATH = ROOT / "config" / "kr_internal_paper_theme_source_admission_registry.json"
NEXT_SESSION_CONTRACT_PATH = ROOT / "config" / "kr_internal_paper_theme_next_session_contract.json"
CONTRACT_SCHEMA = "kr_internal_paper_theme_application_contract/1"
REGISTRY_SCHEMA = "kr_internal_paper_theme_source_admission_registry/1"
EVIDENCE_SCHEMA = "kr_internal_paper_theme_source_admission_evidence/1"
OUTPUT_SCHEMA = "kr_internal_paper_theme_application/1"
NEXT_SESSION_CONTRACT_SCHEMA = "kr_internal_paper_theme_next_session_contract/2"
NEXT_SESSION_OUTPUT_SCHEMA = "kr_internal_paper_theme_next_session_application/2"
KST = ZoneInfo("Asia/Seoul")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

DETERMINING_FIELDS = (
    "rule_id", "rule_version", "approval_status", "ratified_at",
    "effective_from", "effective_to", "application_scope",
    "allowed_asset_ids", "allowed_canonical_instrument_ids", "theme_id",
    "rotation_series_identity", "source_admission_ids",
    "source_manifest_path", "source_manifest_sha256", "source_snapshots",
    "proposal_id", "proposal_artifact_sha256", "temporal_correction_id",
    "temporal_correction_recorded_at", "rule_decision_artifact_sha256",
    "historical_backfill_authorized", "global_taxonomy_authority_changed",
    "stage_promotion_authorized", "production_authorized", "real_authority",
    "order_authorized", "trading_authorized",
)

AUTHORITY_FALSE = {
    "global_taxonomy_authority_changed": False,
    "global_source_allowlist_changed": False,
    "global_authority_registry_changed": False,
    "official_krx_index_constituency_claimed": False,
    "krx_issued_theme_id_claimed": False,
    "historical_backfill_authorized": False,
    "baseline_entry_eligibility_authorized": False,
    "new_entry_authorized": False,
    "stage_promotion_authorized": False,
    "production_authorized": False,
    "real_authority": False,
    "order_authorized": False,
    "trading_authorized": False,
}

NEXT_SESSION_AUTHORITY = {
    "previous_completed_session_context_input_only": True,
    "baseline_entry_eligibility_authorized": False,
    "new_entry_authorized": False,
    "regime_gate_authorized": False,
    "global_taxonomy_authority_changed": False,
    "stage_promotion_authorized": False,
    "production_authorized": False,
    "real_authority": False,
    "order_authorized": False,
    "trading_authorized": False,
    "profitability_claimed": False,
}


class ThemeApplicationError(ValueError):
    """Fail-closed bounded application violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> tuple[bytes, dict]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ThemeApplicationError(f"JSON_READ_FAILED:{path}") from exc
    if not isinstance(value, dict):
        raise ThemeApplicationError(f"JSON_OBJECT_REQUIRED:{path}")
    return raw, value


def _timestamp(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ThemeApplicationError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ThemeApplicationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ThemeApplicationError(code)
    return parsed.astimezone(dt.timezone.utc)


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str):
        raise ThemeApplicationError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ThemeApplicationError(code) from exc
    if parsed.isoformat() != value:
        raise ThemeApplicationError(code)
    return parsed


def _repo_and_commit(path: Path, trusted_commit: str) -> tuple[Path, str]:
    repo = TTA._repo_root(Path(path).resolve())
    if repo is None:
        raise ThemeApplicationError("GIT_REPOSITORY_UNVERIFIED")
    commit = TTA._trusted_commit(repo, trusted_commit)
    if commit is None:
        raise ThemeApplicationError("TRUSTED_COMMIT_INVALID")
    return repo, commit


def _require_exact_committed_bytes(repo: Path, commit: str, path: Path, raw: bytes, code: str) -> str:
    relative = TTA._relative(repo, Path(path))
    if relative is None or TTA._git_blob(repo, commit, relative) != raw:
        raise ThemeApplicationError(code)
    first_seen = TTA._first_seen_exact_bytes(repo, commit, relative, raw)
    if first_seen is None:
        raise ThemeApplicationError(f"{code}_FIRST_SEEN_UNVERIFIED")
    return first_seen


def _expected_contract() -> dict:
    return {
        "schema_version": CONTRACT_SCHEMA,
        "proposal_id": "KR_INTERNAL_PAPER_TWO_STOCK_THEME_APPLICATION_CONTRACT_V1",
        "application_scope": "KR_INTERNAL_PAPER_BASELINE_V0_ENTRY_FILTER",
        "market": "KOREA",
        "allowed_asset_ids": ["KR:XKRX:000660", "KR:XKRX:005930"],
        "allowed_canonical_instrument_ids": ["KRX:000660:COMMON", "KRX:005930:COMMON"],
        "rule_id": "KR.KOSPI.INDUSTRY_MIDDLE_DIVISION_TO_THEME_V1",
        "theme_id": "THEME.KR.KOSPI.ELECTRICAL_ELECTRONIC_EQUIPMENT",
        "rotation_series_identity": "KOSPI::전기전자",
        "industry_code_pattern": "^03-26-[0-9]{2}$",
        "membership_semantics": "INTERNAL_INDUSTRY_PROXY_NOT_OFFICIAL_KRX_INDEX_CONSTITUENCY",
        "source_manifest_path": "evidence/theme_taxonomy/source_snapshots/2026-09-08/manifest.json",
        "source_manifest_sha256": "8f3ed580e33d5f76390107d3bdb8fd256cbd31522e0e6891b4271c625aae1ef0",
        "source_first_seen_commit": "60c3a6e1569e6c61eda518ddafc981fdd0680a71",
        "source_first_seen_at_utc": "2026-09-07T15:32:29Z",
        "source_admission_ids": ["krx_kind_listing_notice", "krx_kospi_industrial_classification_pdf"],
        "proposal_artifact": {
            "path": "outputs/KR_ROTATION_REAL_INPUT_CLOSURE.json",
            "sha256": "ba83919845616f05678afae0f63e7927aa4688d0ad5a08d9f6f66389589bc7c0",
        },
        "rule_decision_artifact": {
            "path": "outputs/KR_INTERNAL_PAPER_TWO_STOCK_THEME_RULE_ADOPTION.json",
            "sha256": "ecc1729160bbfef1bebd4a63a5ec1dc7e4917f2d1f4aac7ab37059d55d0dc2f7",
        },
        "temporal_correction": {
            "correction_id": "KR_INTERNAL_PAPER_TWO_STOCK_THEME_ACTUAL_TIME_CORRECTION_V1",
            "source_thread_id": "01a0712e-eb4f-71d1-b487-f326d6a6bcc3",
            "recorded_after_approval_at_utc": "2026-09-07T16:05:50Z",
            "membership_from_rule": "max(observation_date_start_kst, admission_real_usable_from)",
            "membership_to_rule": "next_calendar_date_start_kst",
            "active_predicate": "membership_from <= timestamp < membership_to",
            "evaluation_at_must_be_active": True,
            "forward_execution_at_must_be_active_when_supplied": True,
            "empty_interval_emits_active_membership": False,
            "next_day_carry_authorized": False,
        },
        "observation_alignment_rule": "master.as_of_date == leadership.observation_date == evaluation_observation_date",
        "availability_rule": "all source, admission, master, and leadership actual availability/first-seen timestamps must be <= evaluation_at",
        "admission_real_usable_from_rule": "max(ratified_at, effective_from, registry_record_first_seen_at, approval_evidence_first_seen_at, source_manifest_first_seen_at, every_source_snapshot_first_seen_at)",
        "authority": {"bounded_internal_paper_entry_filter_input_only": True, **AUTHORITY_FALSE},
    }


def validate_contract(value: dict) -> dict:
    if value != _expected_contract():
        raise ThemeApplicationError("CONTRACT_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return validate_contract(_read_json(Path(path))[1])


def _expected_next_session_contract() -> dict:
    return {
        "schema_version": NEXT_SESSION_CONTRACT_SCHEMA,
        "decision_id": "KR_INTERNAL_PAPER_PREVIOUS_COMPLETED_SESSION_CONTEXT_V1",
        "application_scope": "KR_INTERNAL_PAPER_BASELINE_V0_ENTRY_FILTER",
        "base_profile": {
            "proposal_id": "KR_INTERNAL_PAPER_TWO_STOCK_THEME_APPLICATION_CONTRACT_V1",
            "commit": "d14b17a1c33beb1e25bfc5a9921e588ddf8e5971",
            "contract_path": "config/kr_internal_paper_theme_application_contract.json",
            "contract_sha256": "77453d7c6637b0a5b1538b8587e557ad3f5998c39e68527d1158121ec24d0472",
        },
        "decision_evidence": {
            "path": "evidence/authority/kr_internal_paper_previous_completed_session_context_adoption_20260908.json",
            "sha256": "2571be782cb70473aaba49ed6c6a2fc0e67cd6f6a5a1af3d8ec66433aec5022b",
        },
        "session_relation": {
            "validator": ".github/scripts/korea_market_signals.py::validate_packet",
            "schema_version": "korea_market_signals_observation/1",
            "required_relation": "packet.previous_date == D AND packet.as_of_date == E AND independently verified calendar proves no OPEN_REGULAR session between D and E",
            "calendar_validator": "market_data/krx_session_bars.py::validate_calendar",
            "calendar_validator_sha256": "79e0058a6ed4540b953e9bbb975296a58fcbe6b0f245a299fae65bec5176dbd0",
            "calendar_contract": "config/krx_market_data_contract.json",
            "calendar_contract_sha256": "437b07ec2f1c35ee56236a5044e73bc9b566faa2350d7fe9bc14292ce8061649",
            "calendar_source_schema_version": "krx_date_specific_session_source/1",
            "calendar_coverage": "exact committed snapshot for every calendar date D through E; D and E OPEN_REGULAR; every intervening date CLOSED",
            "calendar_day_subtraction_authorized": False,
            "d_minus_two_fallback_authorized": False,
        },
        "context_session": {
            "label": "PREVIOUS_COMPLETED_SESSION_CONTEXT",
            "required_same_date_inputs": ["D master", "D leadership"],
            "must_be_available_before_evaluation": True,
            "d_price_as_execution_fill_authorized": False,
        },
        "execution_session": {
            "required_same_date_inputs": [
                "E master", "E candidate identity", "E prospective application membership",
            ],
            "market_timezone": "Asia/Seoul",
            "regular_session_close_local": "15:30:00",
            "decision_to_forward_execution_max_seconds": 600,
            "active_predicate": "membership_from <= evaluation_at <= forward_execution_at < E_regular_session_close",
            "e_plus_one_carry_authorized": False,
        },
        "membership": {
            "membership_from_rule": "max(E 00:00:00 KST, source_admission_real_usable_from, D/E_decision_real_usable_from)",
            "membership_to_rule": "E 15:30:00 KST",
            "d_master_interval_extension_authorized": False,
            "backdating_authorized": False,
        },
        "separate_required_inputs": [
            "Exact D rotation TOP bucket",
            "Qualified current E price/spread/impact and causal execution observations",
            "Actual E tradability evidence",
            "Any separately required E-day regime state",
        ],
        "authority": dict(NEXT_SESSION_AUTHORITY),
    }


def load_next_session_contract(path: Path = NEXT_SESSION_CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))[1]
    if value != _expected_next_session_contract():
        raise ThemeApplicationError("NEXT_SESSION_CONTRACT_MISMATCH")
    return copy.deepcopy(value)


def resolve_next_session_decision(
    trusted_commit: str,
    contract_path: Path = NEXT_SESSION_CONTRACT_PATH,
) -> dict:
    contract_raw, contract = _read_json(Path(contract_path))
    if contract != _expected_next_session_contract():
        raise ThemeApplicationError("NEXT_SESSION_CONTRACT_MISMATCH")
    repo, commit = _repo_and_commit(Path(contract_path), trusted_commit)
    contract_first_seen = _require_exact_committed_bytes(
        repo, commit, Path(contract_path), contract_raw,
        "NEXT_SESSION_CONTRACT_NOT_EXACT_COMMITTED_BYTES",
    )
    base = contract["base_profile"]
    base_path = repo / base["contract_path"]
    base_raw = base_path.read_bytes()
    if (
        sha256_bytes(base_raw) != base["contract_sha256"]
        or TTA._git_blob(repo, base["commit"], base["contract_path"]) != base_raw
        or TTA._git_blob(repo, commit, base["contract_path"]) != base_raw
    ):
        raise ThemeApplicationError("NEXT_SESSION_BASE_PROFILE_PIN_MISMATCH")
    evidence_path = repo / contract["decision_evidence"]["path"]
    evidence_raw, evidence = _read_json(evidence_path)
    if sha256_bytes(evidence_raw) != contract["decision_evidence"]["sha256"]:
        raise ThemeApplicationError("NEXT_SESSION_DECISION_EVIDENCE_SHA_MISMATCH")
    evidence_first_seen = _require_exact_committed_bytes(
        repo, commit, evidence_path, evidence_raw,
        "NEXT_SESSION_DECISION_EVIDENCE_NOT_EXACT_COMMITTED_BYTES",
    )
    if (
        evidence.get("schema_version")
        != "kr_internal_paper_previous_completed_session_context_adoption/1"
        or evidence.get("decision_id") != contract["decision_id"]
        or evidence.get("application_scope") != contract["application_scope"]
        or evidence.get("status") != "ADOPTED_UNVALIDATED_INTERNAL_PAPER_HYPOTHESIS"
        or evidence.get("authority_changes") != {
            "real": False,
            "production": False,
            "global_taxonomy": False,
            "profitability_claim": False,
        }
    ):
        raise ThemeApplicationError("NEXT_SESSION_DECISION_EVIDENCE_MISMATCH")
    recorded = _timestamp(
        evidence.get("recorded_after_decision_at_utc"),
        "NEXT_SESSION_DECISION_TIME_INVALID",
    )
    usable = max(
        recorded,
        _timestamp(evidence_first_seen, "NEXT_SESSION_DECISION_FIRST_SEEN_INVALID"),
        _timestamp(contract_first_seen, "NEXT_SESSION_CONTRACT_FIRST_SEEN_INVALID"),
    )
    return {
        "status": "ADOPTED_EXACT_D_TO_E_SCOPE",
        "decision_id": contract["decision_id"],
        "contract_first_seen_at": contract_first_seen,
        "decision_evidence_first_seen_at": evidence_first_seen,
        "decision_real_usable_from": usable.isoformat().replace("+00:00", "Z"),
        "authority": dict(NEXT_SESSION_AUTHORITY),
    }


def determining_payload(record: dict) -> dict:
    return {field: copy.deepcopy(record.get(field)) for field in DETERMINING_FIELDS}


def _validate_registry_document(value: dict, contract: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema_version", "records"}:
        raise ThemeApplicationError("REGISTRY_FIELDS_MISMATCH")
    if value.get("schema_version") != REGISTRY_SCHEMA:
        raise ThemeApplicationError("REGISTRY_SCHEMA_MISMATCH")
    records = value.get("records")
    if not isinstance(records, list) or len(records) != 1:
        raise ThemeApplicationError("REGISTRY_EXACT_ONE_RECORD_REQUIRED")
    record = records[0]
    expected_fields = set(DETERMINING_FIELDS) | {"approval_evidence_ref", "approval_evidence_sha256"}
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise ThemeApplicationError("REGISTRY_RECORD_FIELDS_MISMATCH")
    if record["approval_status"] != "RATIFIED" or record["effective_to"] is not None:
        raise ThemeApplicationError("REGISTRY_RECORD_NOT_RATIFIED")
    for key in ("approval_evidence_sha256", "source_manifest_sha256", "proposal_artifact_sha256", "rule_decision_artifact_sha256"):
        if not isinstance(record[key], str) or SHA256_RE.fullmatch(record[key]) is None:
            raise ThemeApplicationError(f"REGISTRY_SHA_INVALID:{key}")
    expected = {
        "rule_id": contract["rule_id"],
        "application_scope": contract["application_scope"],
        "allowed_asset_ids": contract["allowed_asset_ids"],
        "allowed_canonical_instrument_ids": contract["allowed_canonical_instrument_ids"],
        "theme_id": contract["theme_id"],
        "rotation_series_identity": contract["rotation_series_identity"],
        "source_admission_ids": contract["source_admission_ids"],
        "source_manifest_path": contract["source_manifest_path"],
        "source_manifest_sha256": contract["source_manifest_sha256"],
        "proposal_id": contract["proposal_id"],
        "proposal_artifact_sha256": contract["proposal_artifact"]["sha256"],
        "temporal_correction_id": contract["temporal_correction"]["correction_id"],
        "temporal_correction_recorded_at": contract["temporal_correction"]["recorded_after_approval_at_utc"],
        "rule_decision_artifact_sha256": contract["rule_decision_artifact"]["sha256"],
    }
    if any(record.get(key) != expected_value for key, expected_value in expected.items()):
        raise ThemeApplicationError("REGISTRY_SCOPE_OR_DECISION_MISMATCH")
    if any(record[key] is not False for key in (
        "historical_backfill_authorized", "global_taxonomy_authority_changed",
        "stage_promotion_authorized", "production_authorized", "real_authority",
        "order_authorized", "trading_authorized",
    )):
        raise ThemeApplicationError("REGISTRY_AUTHORITY_EXPANDED")
    _timestamp(record["ratified_at"], "RATIFIED_AT_INVALID")
    _timestamp(record["effective_from"], "EFFECTIVE_FROM_INVALID")
    return copy.deepcopy(record)


def _record_first_seen(repo: Path, commit: str, relative: str, expected: dict) -> str | None:
    for candidate in TTA._commits(repo, commit, relative):
        blob = TTA._git_blob(repo, candidate, relative)
        if blob is None:
            continue
        try:
            document = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        for row in document.get("records", []) if isinstance(document, dict) else []:
            if isinstance(row, dict) and determining_payload(row) == expected:
                return TTA._commit_time(repo, candidate)
    return None


def _manifest_source_map(manifest: dict) -> dict[str, dict]:
    rows = manifest.get("sources")
    if not isinstance(rows, list) or len(rows) != 3:
        raise ThemeApplicationError("SOURCE_MANIFEST_EXACT_THREE_REQUIRED")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source_id"), str):
            raise ThemeApplicationError("SOURCE_MANIFEST_ROW_INVALID")
        if row["source_id"] in result:
            raise ThemeApplicationError("SOURCE_MANIFEST_DUPLICATE")
        result[row["source_id"]] = row
    return result


def resolve_source_admission(
    trusted_commit: str,
    contract_path: Path = CONTRACT_PATH,
    registry_path: Path = REGISTRY_PATH,
) -> dict:
    contract_raw, contract_value = _read_json(Path(contract_path))
    contract = validate_contract(contract_value)
    repo, commit = _repo_and_commit(Path(contract_path), trusted_commit)
    _require_exact_committed_bytes(repo, commit, Path(contract_path), contract_raw, "CONTRACT_NOT_EXACT_COMMITTED_BYTES")

    registry_raw, registry_value = _read_json(Path(registry_path))
    record = _validate_registry_document(registry_value, contract)
    registry_first_seen = _require_exact_committed_bytes(
        repo, commit, Path(registry_path), registry_raw, "REGISTRY_NOT_EXACT_COMMITTED_BYTES"
    )
    registry_relative = TTA._relative(repo, Path(registry_path))
    row_first_seen = _record_first_seen(repo, commit, registry_relative, determining_payload(record))
    if row_first_seen is None or row_first_seen != registry_first_seen:
        raise ThemeApplicationError("REGISTRY_RECORD_FIRST_SEEN_UNVERIFIED")

    evidence_path = repo / record["approval_evidence_ref"]
    evidence_raw, evidence = _read_json(evidence_path)
    if sha256_bytes(evidence_raw) != record["approval_evidence_sha256"]:
        raise ThemeApplicationError("APPROVAL_EVIDENCE_SHA_MISMATCH")
    evidence_first_seen = _require_exact_committed_bytes(
        repo, commit, evidence_path, evidence_raw, "APPROVAL_EVIDENCE_NOT_EXACT_COMMITTED_BYTES"
    )
    if set(evidence) != {
        "schema_version", "decision_id", "decision_source",
        "recorded_after_approval_at_utc", "timestamp_basis",
        "approved_full_payload_sha256", "determining_payload",
    } or evidence.get("schema_version") != EVIDENCE_SCHEMA:
        raise ThemeApplicationError("APPROVAL_EVIDENCE_FIELDS_MISMATCH")
    approved_payload = evidence.get("determining_payload")
    if approved_payload != determining_payload(record):
        raise ThemeApplicationError("APPROVAL_EVIDENCE_PAYLOAD_MISMATCH")
    if evidence.get("approved_full_payload_sha256") != payload_sha256(approved_payload):
        raise ThemeApplicationError("APPROVAL_EVIDENCE_PAYLOAD_SHA_MISMATCH")
    if evidence.get("recorded_after_approval_at_utc") != record["ratified_at"]:
        raise ThemeApplicationError("APPROVAL_EVIDENCE_TIME_MISMATCH")

    manifest_path = repo / record["source_manifest_path"]
    manifest_raw, manifest = _read_json(manifest_path)
    if sha256_bytes(manifest_raw) != record["source_manifest_sha256"]:
        raise ThemeApplicationError("SOURCE_MANIFEST_SHA_MISMATCH")
    manifest_first_seen = _require_exact_committed_bytes(
        repo, commit, manifest_path, manifest_raw, "SOURCE_MANIFEST_NOT_EXACT_COMMITTED_BYTES"
    )
    source_commit = contract["source_first_seen_commit"]
    manifest_relative = TTA._relative(repo, manifest_path)
    if (
        TTA._git_blob(repo, source_commit, manifest_relative) != manifest_raw
        or TTA._commit_time(repo, source_commit) != contract["source_first_seen_at_utc"]
        or manifest_first_seen != contract["source_first_seen_at_utc"]
    ):
        raise ThemeApplicationError("SOURCE_FIRST_SEEN_PIN_MISMATCH")

    manifest_rows = _manifest_source_map(manifest)
    source_snapshot_first_seen = {}
    for source in record["source_snapshots"]:
        row = manifest_rows.get(source["source_id"])
        if row is None or {
            "source_url": row.get("source_url"),
            "path": row.get("path"),
            "source_sha256": row.get("source_sha256"),
        } != {
            "source_url": source["url"],
            "path": source["path"],
            "source_sha256": source["sha256"],
        }:
            raise ThemeApplicationError(f"SOURCE_MANIFEST_BINDING_MISMATCH:{source['source_id']}")
        source_path = repo / source["path"]
        try:
            source_raw = source_path.read_bytes()
        except OSError as exc:
            raise ThemeApplicationError(f"SOURCE_BYTES_MISSING:{source['source_id']}") from exc
        if sha256_bytes(source_raw) != source["sha256"]:
            raise ThemeApplicationError(f"SOURCE_BYTES_SHA_MISMATCH:{source['source_id']}")
        source_snapshot_first_seen[source["source_id"]] = _require_exact_committed_bytes(
            repo, commit, source_path, source_raw,
            f"SOURCE_NOT_EXACT_COMMITTED_BYTES:{source['source_id']}",
        )

    real_usable = max(
        _timestamp(record["ratified_at"], "RATIFIED_AT_INVALID"),
        _timestamp(record["effective_from"], "EFFECTIVE_FROM_INVALID"),
        _timestamp(row_first_seen, "REGISTRY_FIRST_SEEN_INVALID"),
        _timestamp(evidence_first_seen, "EVIDENCE_FIRST_SEEN_INVALID"),
        _timestamp(manifest_first_seen, "MANIFEST_FIRST_SEEN_INVALID"),
        *(
            _timestamp(value, f"SOURCE_FIRST_SEEN_INVALID:{source_id}")
            for source_id, value in source_snapshot_first_seen.items()
        ),
    )
    return {
        "status": "RATIFIED_EXACT_SCOPE",
        "trusted_commit": commit,
        "rule_id": record["rule_id"],
        "application_scope": record["application_scope"],
        "allowed_asset_ids": copy.deepcopy(record["allowed_asset_ids"]),
        "allowed_canonical_instrument_ids": copy.deepcopy(record["allowed_canonical_instrument_ids"]),
        "theme_id": record["theme_id"],
        "rotation_series_identity": record["rotation_series_identity"],
        "source_manifest_first_seen_at": manifest_first_seen,
        "source_snapshot_first_seen_at": source_snapshot_first_seen,
        "approval_evidence_first_seen_at": evidence_first_seen,
        "registry_record_first_seen_at": row_first_seen,
        "admission_real_usable_from": real_usable.isoformat().replace("+00:00", "Z"),
        "authority": dict(AUTHORITY_FALSE),
    }


def membership_window(observation_date: str, admission_real_usable_from: str) -> dict:
    day = _date(observation_date, "OBSERVATION_DATE_INVALID")
    usable = _timestamp(admission_real_usable_from, "ADMISSION_REAL_USABLE_FROM_INVALID")
    start_of_day = dt.datetime.combine(day, dt.time.min, tzinfo=KST).astimezone(dt.timezone.utc)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, tzinfo=KST).astimezone(dt.timezone.utc)
    start = max(start_of_day, usable)
    return {
        "membership_from": start.isoformat().replace("+00:00", "Z"),
        "membership_to": end.isoformat().replace("+00:00", "Z"),
        "nonempty": start < end,
    }


def evaluate_membership_times(
    observation_date: str,
    admission_real_usable_from: str,
    evaluation_at: str,
    forward_execution_at: str | None = None,
) -> dict:
    window = membership_window(observation_date, admission_real_usable_from)
    evaluation = _timestamp(evaluation_at, "EVALUATION_AT_INVALID")
    execution = None if forward_execution_at is None else _timestamp(
        forward_execution_at, "FORWARD_EXECUTION_AT_INVALID"
    )
    start = _timestamp(window["membership_from"], "MEMBERSHIP_FROM_INVALID")
    end = _timestamp(window["membership_to"], "MEMBERSHIP_TO_INVALID")
    evaluation_active = window["nonempty"] and start <= evaluation < end
    execution_active = None if execution is None else window["nonempty"] and start <= execution < end
    active = window["nonempty"] and evaluation_active and execution_active is not False
    if not window["nonempty"]:
        status = "UNKNOWN_EMPTY_MEMBERSHIP_INTERVAL"
    elif not evaluation_active:
        status = "UNKNOWN_EVALUATION_OUTSIDE_MEMBERSHIP_INTERVAL"
    elif execution_active is False:
        status = "UNKNOWN_FORWARD_EXECUTION_OUTSIDE_MEMBERSHIP_INTERVAL"
    else:
        status = "ACTIVE_AT_ALL_SUPPLIED_ACTUAL_TIMES"
    return {
        **window,
        "evaluation_at": evaluation.isoformat().replace("+00:00", "Z"),
        "evaluation_active": evaluation_active,
        "forward_execution_at": None if execution is None else execution.isoformat().replace("+00:00", "Z"),
        "forward_execution_active": execution_active,
        "active": active,
        "status": status,
    }


def _load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise ThemeApplicationError(f"MODULE_LOAD_FAILED:{relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_exact_packet(path: Path, repo: Path, commit: str, code: str) -> tuple[dict, str]:
    raw, value = _read_json(path)
    first_seen = _require_exact_committed_bytes(repo, commit, path, raw, code)
    return value, first_seen


def verify_immediate_session_calendar(
    session_calendar_packet_paths: list[Path],
    context_session_date: str,
    execution_session_date: str,
    evaluation_at: str,
    repo: Path,
    commit: str,
) -> dict:
    """Prove that E is the first OPEN_REGULAR KRX session after D.

    Every calendar date from D through E must have an exact committed
    date-specific CTCA0903R envelope.  The existing KRX calendar validator
    checks provider identity, market-rule identity, regular-session bounds,
    and point-in-time availability.  Calendar-day enumeration is used only to
    demand complete evidence; it never infers whether a date is open.
    """
    if not isinstance(session_calendar_packet_paths, list) or not session_calendar_packet_paths:
        raise ThemeApplicationError("SESSION_CALENDAR_EVIDENCE_MISSING")
    context_day = _date(context_session_date, "CONTEXT_SESSION_DATE_INVALID")
    execution_day = _date(execution_session_date, "EXECUTION_SESSION_DATE_INVALID")
    if context_day >= execution_day:
        raise ThemeApplicationError("SESSION_CALENDAR_DATE_ORDER_INVALID")
    expected_days = [
        context_day + dt.timedelta(days=offset)
        for offset in range((execution_day - context_day).days + 1)
    ]
    if len(session_calendar_packet_paths) != len(expected_days):
        raise ThemeApplicationError("SESSION_CALENDAR_COVERAGE_INCOMPLETE")

    evaluation = _timestamp(evaluation_at, "SESSION_CALENDAR_EVALUATION_AT_INVALID")
    calendar_binding = _expected_next_session_contract()["session_relation"]
    validator_path = ROOT / calendar_binding["calendar_validator"].split("::", 1)[0]
    calendar_contract_path = ROOT / calendar_binding["calendar_contract"]
    if sha256_bytes(validator_path.read_bytes()) != calendar_binding["calendar_validator_sha256"]:
        raise ThemeApplicationError("SESSION_CALENDAR_VALIDATOR_HASH_MISMATCH")
    if sha256_bytes(calendar_contract_path.read_bytes()) != calendar_binding["calendar_contract_sha256"]:
        raise ThemeApplicationError("SESSION_CALENDAR_CONTRACT_HASH_MISMATCH")
    validator = _load_module(
        "kr_internal_paper_krx_session_calendar",
        "market_data/krx_session_bars.py",
    )
    try:
        validator_contract = validator.load_contract()
    except validator.KrxMarketDataError as exc:
        raise ThemeApplicationError(f"SESSION_CALENDAR_CONTRACT_INVALID:{exc}") from exc

    entries = []
    for index, (supplied_path, expected_day) in enumerate(
        zip(session_calendar_packet_paths, expected_days)
    ):
        path = Path(supplied_path)
        if not path.is_absolute():
            path = repo / path
        envelope, first_seen = _load_exact_packet(
            path, repo, commit, "SESSION_CALENDAR_NOT_EXACT_COMMITTED_BYTES"
        )
        if set(envelope) != {
            "schema_version", "as_of_date", "official_response_ref",
            "official_response_sha256", "calendar",
        } or envelope.get("schema_version") != "krx_date_specific_session_source/1":
            raise ThemeApplicationError("SESSION_CALENDAR_ENVELOPE_INVALID")
        if envelope.get("as_of_date") != expected_day.isoformat():
            raise ThemeApplicationError("SESSION_CALENDAR_COVERAGE_ORDER_MISMATCH")
        try:
            checked = validator.validate_calendar(
                envelope.get("calendar"), evaluation, validator_contract
            )
        except validator.KrxMarketDataError as exc:
            raise ThemeApplicationError(f"SESSION_CALENDAR_INVALID:{exc}") from exc
        if checked["session_date"] != expected_day.isoformat():
            raise ThemeApplicationError("SESSION_CALENDAR_DATE_MISMATCH")
        if (
            checked["source_ref"] != envelope.get("official_response_ref")
            or checked["source_sha256"] != envelope.get("official_response_sha256")
        ):
            raise ThemeApplicationError("SESSION_CALENDAR_SOURCE_BINDING_MISMATCH")
        first_seen_at = _timestamp(
            first_seen, "SESSION_CALENDAR_FIRST_SEEN_INVALID"
        )
        if first_seen_at > evaluation:
            raise ThemeApplicationError("SESSION_CALENDAR_FUTURE_AT_EVALUATION")
        if checked["status"] == "UNKNOWN":
            raise ThemeApplicationError("SESSION_CALENDAR_STATUS_UNKNOWN")
        boundary = index in {0, len(expected_days) - 1}
        if boundary and checked["status"] != "OPEN_REGULAR":
            raise ThemeApplicationError("SESSION_CALENDAR_BOUNDARY_NOT_OPEN_REGULAR")
        if not boundary and checked["status"] != "CLOSED":
            raise ThemeApplicationError("SESSION_CALENDAR_INTERVENING_OPEN_SESSION")
        entries.append({
            "session_date": expected_day.isoformat(),
            "status": checked["status"],
            "source_file_sha256": sha256_bytes(path.read_bytes()),
            "official_response_sha256": checked["source_sha256"],
            "available_at": checked["available_at"],
            "first_seen_at": first_seen,
        })

    receipt = {
        "schema_version": "kr_internal_paper_verified_session_calendar/1",
        "context_session_date": context_day.isoformat(),
        "execution_session_date": execution_day.isoformat(),
        "sessions": entries,
    }
    return {
        "verified": True,
        "calendar_receipt_sha256": payload_sha256(receipt),
        "calendar_validator_sha256": calendar_binding["calendar_validator_sha256"],
        "calendar_contract_sha256": calendar_binding["calendar_contract_sha256"],
        "context_session_close_at": (
            dt.datetime.combine(
                context_day,
                dt.time.fromisoformat(validator_contract["regular_session"]["close"]),
                KST,
            ).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        ),
        "execution_session_close_at": (
            dt.datetime.combine(
                execution_day,
                dt.time.fromisoformat(validator_contract["regular_session"]["close"]),
                KST,
            ).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        ),
        "sessions": entries,
    }


def derive_verified_session_boundary(
    session_relation_packet_path: Path,
    session_calendar_packet_paths: list[Path],
    context_session_date: str,
    execution_session_date: str,
    context_session_close_at: str,
    execution_session_close_at: str,
    evaluation_at: str,
    trusted_commit: str,
) -> dict:
    """Derive the D-to-E freshness TTL from the existing verified relation.

    The relation packet is the same immutable, committed input used by the
    bounded next-session profile.  Both close timestamps must be the regular
    15:30 KST close of the dates named by that packet.  The caller cannot turn
    a duration into a rolling window: evaluation must occur after the relation
    became usable and strictly before E close.
    """
    contract = load_next_session_contract()
    repo, commit = _repo_and_commit(NEXT_SESSION_CONTRACT_PATH, trusted_commit)
    relation_raw_bytes, relation_first_seen = _load_exact_packet(
        Path(session_relation_packet_path), repo, commit,
        "SESSION_RELATION_NOT_EXACT_COMMITTED_BYTES",
    )
    relation_module = _load_module(
        "kr_internal_paper_verified_session_boundary",
        ".github/scripts/korea_market_signals.py",
    )
    try:
        relation = relation_module.validate_packet(relation_raw_bytes)
    except relation_module.KoreaMarketSignalsError as exc:
        raise ThemeApplicationError(f"SESSION_RELATION_INVALID:{exc}") from exc

    context_day = _date(context_session_date, "CONTEXT_SESSION_DATE_INVALID")
    execution_day = _date(execution_session_date, "EXECUTION_SESSION_DATE_INVALID")
    if (
        relation.get("previous_date") != context_day.isoformat()
        or relation.get("as_of_date") != execution_day.isoformat()
    ):
        raise ThemeApplicationError("SESSION_RELATION_D_E_MISMATCH")

    calendar = verify_immediate_session_calendar(
        session_calendar_packet_paths,
        context_session_date,
        execution_session_date,
        evaluation_at,
        repo,
        commit,
    )

    context_close = _timestamp(
        context_session_close_at, "CONTEXT_SESSION_CLOSE_AT_INVALID"
    )
    execution_close = _timestamp(
        execution_session_close_at, "EXECUTION_SESSION_CLOSE_AT_INVALID"
    )
    regular_close = dt.time.fromisoformat(
        contract["execution_session"]["regular_session_close_local"]
    )
    for value, day, code in (
        (context_close, context_day, "CONTEXT_SESSION_CLOSE_BOUNDARY_MISMATCH"),
        (execution_close, execution_day, "EXECUTION_SESSION_CLOSE_BOUNDARY_MISMATCH"),
    ):
        local = value.astimezone(KST)
        if local.date() != day or local.timetz().replace(tzinfo=None) != regular_close:
            raise ThemeApplicationError(code)
    if not context_close < execution_close:
        raise ThemeApplicationError("SESSION_CLOSE_ORDER_INVALID")
    if (
        context_close.isoformat().replace("+00:00", "Z")
        != calendar["context_session_close_at"]
        or execution_close.isoformat().replace("+00:00", "Z")
        != calendar["execution_session_close_at"]
    ):
        raise ThemeApplicationError("SESSION_CLOSE_CALENDAR_BINDING_MISMATCH")

    evaluation = _timestamp(evaluation_at, "SESSION_BOUNDARY_EVALUATION_AT_INVALID")
    relation_available = _timestamp(
        relation.get("available_at"), "SESSION_RELATION_AVAILABLE_AT_INVALID"
    )
    first_seen = _timestamp(
        relation_first_seen, "SESSION_RELATION_FIRST_SEEN_INVALID"
    )
    usable_from = max(relation_available, first_seen)
    if usable_from > evaluation:
        raise ThemeApplicationError("SESSION_RELATION_FUTURE_AT_EVALUATION")
    if evaluation >= execution_close:
        raise ThemeApplicationError("SESSION_BOUNDARY_EXPIRED")
    ttl = (execution_close - context_close).total_seconds()
    if not ttl.is_integer() or ttl <= 0:
        raise ThemeApplicationError("DERIVED_SESSION_TTL_INVALID")

    relation_bytes = Path(session_relation_packet_path).read_bytes()
    return {
        "schema_version": "kr_paper_runtime_session_boundary_freshness/2",
        "context_session_date": context_day.isoformat(),
        "execution_session_date": execution_day.isoformat(),
        "context_session_close_at": context_close.isoformat().replace("+00:00", "Z"),
        "execution_session_close_at": execution_close.isoformat().replace("+00:00", "Z"),
        "calendar_receipt_sha256": calendar["calendar_receipt_sha256"],
        "session_calendar": copy.deepcopy(calendar["sessions"]),
        "session_relation_file_sha256": sha256_bytes(relation_bytes),
        "session_relation_payload_sha256": relation["payload_sha256"],
        "session_relation_first_seen_at": relation_first_seen,
        "session_relation_usable_from": usable_from.isoformat().replace("+00:00", "Z"),
        "derived_ttl_seconds": int(ttl),
        "trusted_commit": commit,
    }


def _validate_leadership_wrapper(value: dict) -> dict:
    fields = {
        "generated_at", "leadership_packet", "leadership_packet_sha256", "markets",
        "observation_date", "outcome", "payload_sha256", "prior_date", "reason",
        "schema_version",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ThemeApplicationError("LEADERSHIP_WRAPPER_FIELDS_MISMATCH")
    unsigned = copy.deepcopy(value)
    digest = unsigned.pop("payload_sha256")
    if digest != payload_sha256(unsigned):
        raise ThemeApplicationError("LEADERSHIP_WRAPPER_SHA_MISMATCH")
    leadership = value["leadership_packet"]
    if value["leadership_packet_sha256"] != leadership.get("payload_sha256"):
        raise ThemeApplicationError("LEADERSHIP_PACKET_SHA_BINDING_MISMATCH")
    try:
        validated = KCR._validate_upstream(leadership, "bounded_current", KCR.load_contract())
    except KCR.KoreaCapitalRotationError as exc:
        raise ThemeApplicationError(f"LEADERSHIP_PACKET_INVALID:{exc}") from exc
    if value["observation_date"] != leadership["observation_date"]:
        raise ThemeApplicationError("LEADERSHIP_WRAPPER_DATE_MISMATCH")
    return validated


def evaluate_application(
    master_packet_path: Path,
    leadership_packet_path: Path,
    evaluation_at: str,
    trusted_commit: str,
    forward_execution_at: str | None = None,
) -> dict:
    admission = resolve_source_admission(trusted_commit)
    repo, commit = _repo_and_commit(CONTRACT_PATH, trusted_commit)
    master_packet, master_first_seen = _load_exact_packet(
        Path(master_packet_path), repo, commit, "MASTER_PACKET_NOT_EXACT_COMMITTED_BYTES"
    )
    leadership_wrapper, leadership_first_seen = _load_exact_packet(
        Path(leadership_packet_path), repo, commit, "LEADERSHIP_PACKET_NOT_EXACT_COMMITTED_BYTES"
    )
    population = _load_module(
        "kr_internal_paper_korea_global_universe_population",
        ".github/scripts/korea_global_universe_populate.py",
    )
    try:
        master = population.validate_packet(master_packet)
    except population.PopulationError as exc:
        raise ThemeApplicationError(f"MASTER_PACKET_INVALID:{exc}") from exc
    leadership = _validate_leadership_wrapper(leadership_wrapper)
    observation_date = master["as_of_date"]
    if leadership_wrapper["observation_date"] != observation_date:
        raise ThemeApplicationError("OBSERVATION_DATE_MISMATCH")

    rows = {row["asset_id"]: row for row in master["asset_master"]["records"]}
    authority = CI.load_authority()
    expected = {
        "KR:XKRX:000660": ("000660", "KRX:000660:COMMON", "DART:00164779", "XKRX:000660"),
        "KR:XKRX:005930": ("005930", "KRX:005930:COMMON", "DART:00126380", "XKRX:005930"),
    }
    identities = []
    for asset_id in admission["allowed_asset_ids"]:
        row = rows.get(asset_id)
        if row is None or row.get("market") != "KOREA" or row.get("asset_class") != "EQUITY":
            raise ThemeApplicationError(f"TARGET_ASSET_MISSING_OR_INVALID:{asset_id}")
        ticker, instrument_id, issuer_id, listing_id = expected[asset_id]
        if row.get("primary_symbol") != ticker or not any(
            membership.get("membership_type") == "UNIVERSE"
            and membership.get("membership_id") == "KOSPI"
            for membership in row.get("active_memberships", [])
        ):
            raise ThemeApplicationError(f"TARGET_ASSET_NOT_ACTIVE_KOSPI:{asset_id}")
        resolved = CI.resolve_instrument_identity(
            "krx_open_api_stock_daily", ticker, "KOREA", observation_date,
            authority, trusted_commit=commit,
        )
        if (
            resolved.get("status") != CI.RESOLVED
            or resolved.get("canonical_instrument_id") != instrument_id
            or resolved.get("canonical_issuer_id") != issuer_id
            or resolved.get("listing_id") != listing_id
        ):
            raise ThemeApplicationError(f"CANONICAL_IDENTITY_NOT_EXACT:{asset_id}:{resolved.get('status')}")
        identities.append({
            "asset_id": asset_id,
            "canonical_instrument_id": instrument_id,
            "canonical_issuer_id": issuer_id,
            "listing_id": listing_id,
        })

    series = leadership["rows"].get(admission["rotation_series_identity"])
    if series is None or series["role"] not in {"SECTOR", "THEME"}:
        raise ThemeApplicationError("BOUND_ROTATION_SERIES_MISSING")
    source_times = [
        _timestamp(source["retrieved_at_utc"], "MASTER_RETRIEVED_AT_INVALID")
        for source in master["source_snapshots"]
    ]
    available_by = max(
        source_times
        + [leadership["available_at"], _timestamp(master_first_seen, "MASTER_FIRST_SEEN_INVALID"),
           _timestamp(leadership_first_seen, "LEADERSHIP_FIRST_SEEN_INVALID")]
    )
    evaluation = _timestamp(evaluation_at, "EVALUATION_AT_INVALID")
    temporal = evaluate_membership_times(
        observation_date, admission["admission_real_usable_from"],
        evaluation_at, forward_execution_at,
    )
    inputs_available = available_by <= evaluation
    authorized = temporal["active"] and inputs_available
    if authorized:
        status = "ACTIVE_BOUNDED_INTERNAL_PAPER_INPUT"
    elif not inputs_available:
        status = "UNKNOWN_INPUTS_NOT_AVAILABLE_BY_EVALUATION"
    else:
        status = temporal["status"]
    output = {
        "schema_version": OUTPUT_SCHEMA,
        "status": status,
        "application_scope": admission["application_scope"],
        "observation_date": observation_date,
        "theme_id": admission["theme_id"],
        "rotation_series_identity": admission["rotation_series_identity"],
        "membership_semantics": "INTERNAL_INDUSTRY_PROXY_NOT_OFFICIAL_KRX_INDEX_CONSTITUENCY",
        "membership_window": temporal,
        "inputs_available_by_evaluation": inputs_available,
        "latest_required_input_available_at": available_by.isoformat().replace("+00:00", "Z"),
        "assets": identities,
        "series_observation": {
            "role": series["role"],
            "benchmark_identity": series["benchmark_identity"],
            "relative_strength_vs_benchmark": str(series["relative_strength_vs_benchmark"]),
        },
        "lineage": {
            "trusted_commit": commit,
            "source_admission_real_usable_from": admission["admission_real_usable_from"],
            "master_payload_sha256": master["payload_sha256"],
            "master_first_seen_at": master_first_seen,
            "leadership_payload_sha256": leadership_wrapper["payload_sha256"],
            "leadership_first_seen_at": leadership_first_seen,
        },
        "authority": {
            "bounded_internal_paper_entry_filter_input_authorized": authorized,
            **AUTHORITY_FALSE,
        },
    }
    output["payload_sha256"] = payload_sha256(output)
    return output


def _target_identities_for_session(master: dict, session_date: str, commit: str) -> list[dict]:
    rows = {row["asset_id"]: row for row in master["asset_master"]["records"]}
    authority = CI.load_authority()
    expected = {
        "KR:XKRX:000660": ("000660", "KRX:000660:COMMON", "DART:00164779", "XKRX:000660"),
        "KR:XKRX:005930": ("005930", "KRX:005930:COMMON", "DART:00126380", "XKRX:005930"),
    }
    identities = []
    for asset_id in sorted(expected):
        row = rows.get(asset_id)
        if row is None or row.get("market") != "KOREA" or row.get("asset_class") != "EQUITY":
            raise ThemeApplicationError(f"TARGET_ASSET_MISSING_OR_INVALID:{asset_id}")
        ticker, instrument_id, issuer_id, listing_id = expected[asset_id]
        if row.get("primary_symbol") != ticker or not any(
            membership.get("membership_type") == "UNIVERSE"
            and membership.get("membership_id") == "KOSPI"
            for membership in row.get("active_memberships", [])
        ):
            raise ThemeApplicationError(f"TARGET_ASSET_NOT_ACTIVE_KOSPI:{asset_id}")
        resolved = CI.resolve_instrument_identity(
            "krx_open_api_stock_daily", ticker, "KOREA", session_date,
            authority, trusted_commit=commit,
        )
        if (
            resolved.get("status") != CI.RESOLVED
            or resolved.get("canonical_instrument_id") != instrument_id
            or resolved.get("canonical_issuer_id") != issuer_id
            or resolved.get("listing_id") != listing_id
        ):
            raise ThemeApplicationError(
                f"CANONICAL_IDENTITY_NOT_EXACT:{asset_id}:{resolved.get('status')}"
            )
        identities.append({
            "asset_id": asset_id,
            "canonical_instrument_id": instrument_id,
            "canonical_issuer_id": issuer_id,
            "listing_id": listing_id,
        })
    return identities


def _master_latest_available_at(master: dict, first_seen: str) -> dt.datetime:
    return max(
        [_timestamp(first_seen, "MASTER_FIRST_SEEN_INVALID")]
        + [
            _timestamp(source["retrieved_at_utc"], "MASTER_RETRIEVED_AT_INVALID")
            for source in master["source_snapshots"]
        ]
    )


def evaluate_next_session_application(
    context_master_packet_path: Path,
    context_leadership_packet_path: Path,
    execution_master_packet_path: Path,
    session_relation_packet_path: Path,
    session_calendar_packet_paths: list[Path],
    evaluation_at: str,
    forward_execution_at: str,
    trusted_commit: str,
) -> dict:
    """Validate D context for only the immediately following execution session E.

    D and E are taken from independently validated packets.  The function
    never computes a session by subtracting calendar days, extends D's master
    interval, or authorizes an entry.  It only emits a bounded input that a
    separate private consumer may combine with current E execution evidence,
    an exact D TOP bucket, and any separately required E regime state.
    """
    admission = resolve_source_admission(trusted_commit)
    decision = resolve_next_session_decision(trusted_commit)
    contract = load_next_session_contract()
    repo, commit = _repo_and_commit(NEXT_SESSION_CONTRACT_PATH, trusted_commit)

    d_master_raw, d_master_first_seen = _load_exact_packet(
        Path(context_master_packet_path), repo, commit,
        "CONTEXT_MASTER_NOT_EXACT_COMMITTED_BYTES",
    )
    d_leadership_wrapper, d_leadership_first_seen = _load_exact_packet(
        Path(context_leadership_packet_path), repo, commit,
        "CONTEXT_LEADERSHIP_NOT_EXACT_COMMITTED_BYTES",
    )
    e_master_raw, e_master_first_seen = _load_exact_packet(
        Path(execution_master_packet_path), repo, commit,
        "EXECUTION_MASTER_NOT_EXACT_COMMITTED_BYTES",
    )
    relation_raw, relation_first_seen = _load_exact_packet(
        Path(session_relation_packet_path), repo, commit,
        "SESSION_RELATION_NOT_EXACT_COMMITTED_BYTES",
    )

    population = _load_module(
        "kr_internal_paper_next_session_global_universe_population",
        ".github/scripts/korea_global_universe_populate.py",
    )
    try:
        d_master = population.validate_packet(d_master_raw)
        e_master = population.validate_packet(e_master_raw)
    except population.PopulationError as exc:
        raise ThemeApplicationError(f"NEXT_SESSION_MASTER_INVALID:{exc}") from exc
    d_leadership = _validate_leadership_wrapper(d_leadership_wrapper)
    relation_module = _load_module(
        "kr_internal_paper_next_session_relation",
        ".github/scripts/korea_market_signals.py",
    )
    try:
        relation = relation_module.validate_packet(relation_raw)
    except relation_module.KoreaMarketSignalsError as exc:
        raise ThemeApplicationError(f"SESSION_RELATION_INVALID:{exc}") from exc

    context_date = d_master["as_of_date"]
    execution_date = e_master["as_of_date"]
    if d_leadership_wrapper["observation_date"] != context_date:
        raise ThemeApplicationError("CONTEXT_SAME_DATE_MISMATCH")
    relation_exact = (
        relation.get("previous_date") == context_date
        and relation.get("as_of_date") == execution_date
    )
    try:
        session_calendar = verify_immediate_session_calendar(
            session_calendar_packet_paths,
            context_date,
            execution_date,
            evaluation_at,
            repo,
            commit,
        )
        session_calendar_reason = None
    except ThemeApplicationError as exc:
        session_calendar = None
        session_calendar_reason = str(exc).split(":", 1)[0]

    context_identities = _target_identities_for_session(d_master, context_date, commit)
    execution_identities = _target_identities_for_session(e_master, execution_date, commit)
    series = d_leadership["rows"].get(admission["rotation_series_identity"])
    if series is None or series["role"] not in {"SECTOR", "THEME"}:
        raise ThemeApplicationError("CONTEXT_BOUND_ROTATION_SERIES_MISSING")

    evaluation = _timestamp(evaluation_at, "NEXT_SESSION_EVALUATION_AT_INVALID")
    execution = _timestamp(
        forward_execution_at, "NEXT_SESSION_FORWARD_EXECUTION_AT_INVALID"
    )
    e_day = _date(execution_date, "EXECUTION_SESSION_DATE_INVALID")
    e_start = dt.datetime.combine(e_day, dt.time.min, tzinfo=KST).astimezone(dt.timezone.utc)
    close_time = dt.time.fromisoformat(contract["execution_session"]["regular_session_close_local"])
    e_close = dt.datetime.combine(e_day, close_time, tzinfo=KST).astimezone(dt.timezone.utc)
    if session_calendar is not None:
        e_close = _timestamp(
            session_calendar["execution_session_close_at"],
            "EXECUTION_SESSION_CALENDAR_CLOSE_INVALID",
        )
    membership_from = max(
        e_start,
        _timestamp(admission["admission_real_usable_from"], "ADMISSION_REAL_USABLE_FROM_INVALID"),
        _timestamp(decision["decision_real_usable_from"], "NEXT_SESSION_DECISION_REAL_USABLE_INVALID"),
    )

    context_available_by = max(
        _master_latest_available_at(d_master, d_master_first_seen),
        d_leadership["available_at"],
        _timestamp(d_leadership_first_seen, "CONTEXT_LEADERSHIP_FIRST_SEEN_INVALID"),
    )
    execution_availability = [
        _master_latest_available_at(e_master, e_master_first_seen),
        _timestamp(relation.get("available_at"), "SESSION_RELATION_AVAILABLE_AT_INVALID"),
        _timestamp(relation_first_seen, "SESSION_RELATION_FIRST_SEEN_INVALID"),
    ]
    if session_calendar is not None:
        execution_availability.extend(
            _timestamp(row[field], "SESSION_CALENDAR_AVAILABILITY_INVALID")
            for row in session_calendar["sessions"]
            for field in ("available_at", "first_seen_at")
        )
    execution_available_by = max(execution_availability)
    latest_available = max(context_available_by, execution_available_by)
    inputs_available = latest_available <= evaluation
    interval_nonempty = membership_from < e_close
    evaluation_active = interval_nonempty and membership_from <= evaluation < e_close
    execution_active = interval_nonempty and membership_from <= execution < e_close
    ordered = evaluation <= execution
    ttl_seconds = contract["execution_session"]["decision_to_forward_execution_max_seconds"]
    within_ttl = ordered and (execution - evaluation).total_seconds() <= ttl_seconds
    active = (
        relation_exact and session_calendar is not None
        and inputs_available and evaluation_active
        and execution_active and within_ttl
    )

    if not relation_exact:
        status = "UNKNOWN_CONTEXT_NOT_IMMEDIATE_PREVIOUS_SESSION"
    elif session_calendar is None:
        status = "UNKNOWN_" + session_calendar_reason
    elif not interval_nonempty:
        status = "UNKNOWN_EMPTY_EXECUTION_MEMBERSHIP_INTERVAL"
    elif not inputs_available:
        status = "UNKNOWN_INPUT_AVAILABLE_AFTER_EVALUATION"
    elif not evaluation_active:
        status = "UNKNOWN_EVALUATION_OUTSIDE_EXECUTION_SESSION_MEMBERSHIP"
    elif not execution_active:
        status = "UNKNOWN_EXECUTION_MEMBERSHIP_EXPIRED"
    elif not within_ttl:
        status = "UNKNOWN_FORWARD_EXECUTION_ORDER_OR_600_SECOND_TTL"
    else:
        status = "ACTIVE_PREVIOUS_COMPLETED_SESSION_CONTEXT_INPUT"

    output = {
        "schema_version": NEXT_SESSION_OUTPUT_SCHEMA,
        "status": status,
        "application_scope": admission["application_scope"],
        "context_label": "PREVIOUS_COMPLETED_SESSION_CONTEXT",
        "context_session_date": context_date,
        "execution_session_date": execution_date,
        "session_relation_exact": relation_exact,
        "session_calendar_verified": session_calendar is not None,
        "session_calendar_reason": session_calendar_reason,
        "theme_id": admission["theme_id"],
        "rotation_series_identity": admission["rotation_series_identity"],
        "context_series_observation": {
            "role": series["role"],
            "benchmark_identity": series["benchmark_identity"],
            "relative_strength_vs_benchmark": str(series["relative_strength_vs_benchmark"]),
            "top_bucket_verified": False,
        },
        "context_identities": context_identities,
        "execution_identities": execution_identities,
        "execution_membership": {
            "membership_from": membership_from.isoformat().replace("+00:00", "Z"),
            "membership_to": e_close.isoformat().replace("+00:00", "Z"),
            "evaluation_at": evaluation.isoformat().replace("+00:00", "Z"),
            "forward_execution_at": execution.isoformat().replace("+00:00", "Z"),
            "interval_nonempty": interval_nonempty,
            "evaluation_active": evaluation_active,
            "forward_execution_active": execution_active,
            "decision_to_execution_seconds": (
                (execution - evaluation).total_seconds() if ordered else None
            ),
            "within_600_second_window": within_ttl,
        },
        "inputs_available_by_evaluation": inputs_available,
        "latest_required_input_available_at": latest_available.isoformat().replace("+00:00", "Z"),
        "lineage": {
            "trusted_commit": commit,
            "decision_id": decision["decision_id"],
            "source_admission_real_usable_from": admission["admission_real_usable_from"],
            "next_session_decision_real_usable_from": decision["decision_real_usable_from"],
            "context_master_payload_sha256": d_master["payload_sha256"],
            "context_master_first_seen_at": d_master_first_seen,
            "context_leadership_payload_sha256": d_leadership_wrapper["payload_sha256"],
            "context_leadership_first_seen_at": d_leadership_first_seen,
            "execution_master_payload_sha256": e_master["payload_sha256"],
            "execution_master_first_seen_at": e_master_first_seen,
            "session_relation_payload_sha256": relation["payload_sha256"],
            "session_relation_first_seen_at": relation_first_seen,
            "session_calendar_receipt_sha256": (
                None if session_calendar is None
                else session_calendar["calendar_receipt_sha256"]
            ),
            "session_calendar": (
                [] if session_calendar is None
                else copy.deepcopy(session_calendar["sessions"])
            ),
        },
        "separate_required_inputs": copy.deepcopy(contract["separate_required_inputs"]),
        "authority": {
            "previous_completed_session_context_input_authorized": active,
            **NEXT_SESSION_AUTHORITY,
        },
    }
    output["payload_sha256"] = payload_sha256(output)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trusted-commit", required=True)
    parser.add_argument("--verify-sources", action="store_true")
    parser.add_argument("--master", type=Path)
    parser.add_argument("--leadership", type=Path)
    parser.add_argument("--evaluation-at")
    parser.add_argument("--forward-execution-at")
    args = parser.parse_args(argv)
    try:
        if args.verify_sources:
            result = resolve_source_admission(args.trusted_commit)
        else:
            if args.master is None or args.leadership is None or args.evaluation_at is None:
                raise ThemeApplicationError("MASTER_LEADERSHIP_AND_EVALUATION_REQUIRED")
            result = evaluate_application(
                args.master, args.leadership, args.evaluation_at,
                args.trusted_commit, args.forward_execution_at,
            )
    except ThemeApplicationError as exc:
        print(f"KR internal-paper Theme application failed reason={exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
