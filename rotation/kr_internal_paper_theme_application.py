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
CONTRACT_SCHEMA = "kr_internal_paper_theme_application_contract/1"
REGISTRY_SCHEMA = "kr_internal_paper_theme_source_admission_registry/1"
EVIDENCE_SCHEMA = "kr_internal_paper_theme_source_admission_evidence/1"
OUTPUT_SCHEMA = "kr_internal_paper_theme_application/1"
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
    "stage_promotion_authorized": False,
    "production_authorized": False,
    "real_authority": False,
    "order_authorized": False,
    "trading_authorized": False,
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
