#!/usr/bin/env python3
"""Evidence-bound candidate inclusion, lifecycle, and system-Stage receipt.

This sidecar connects retained PM Watchlist rationale and the ratified P8-12
temporal-freshness assessment.  It also applies the separately ratified P3-05
system Candidate policy without mutating or depending on a manual Stage tag.
Missing gate inputs hold; no mapping, score, or threshold is inferred.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from replay.opportunity_trigger import canonical_json, payload_sha256


CONTRACT_VERSION = "candidate_evidence_lifecycle_receipt/2"
SCHEMA_VERSION = 2

WATCHLIST_PATH = ROOT / "_watchlist_rows.json"
STAGE_HISTORY_PATH = ROOT / "data" / "stage_history.json"
VALIDITY_PATH = (
    ROOT
    / "evidence"
    / "operational"
    / "dynamic_clock"
    / "candidate_validity_window_assessment.json"
)
THEME_REGISTRY_PATH = ROOT / "config" / "theme_taxonomy_source_fact_registry.json"
THEME_AUTHORITY_PATH = ROOT / "config" / "theme_taxonomy_authority_registry.json"
KOREA_SECTOR_BINDING_PATH = (
    ROOT / "config" / "korea_rotation_sector_identity_binding_document.json"
)
ROTATION_LEDGER_CONTRACT_PATH = ROOT / "config" / "rotation_state_ledger_contract.json"
IDENTITY_OBSERVATION_PATH = (
    ROOT
    / "evidence"
    / "operational"
    / "dynamic_clock"
    / "candidate_identity_observation.json"
)
KOREA_REVIEW_CONTRACT_PATH = ROOT / "config" / "korea_symbol_market_review_contract.json"
US_REVIEW_CONTRACT_PATH = ROOT / "config" / "us_symbol_market_review_contract.json"
STAGE4_FUNNEL_CONTRACT_PATH = ROOT / "config" / "common_paper_candidate_funnel_contract.json"
STAGE4_FUNNEL_SCHEMA_PATH = ROOT / "schemas" / "common_paper_candidate_funnel.schema.json"
STAGE_POLICY_PATH = ROOT / "config" / "candidate_stage_evaluation_policy_v1.json"
STAGE_POLICY_REGISTRY_PATH = (
    ROOT / "config" / "candidate_stage_evaluation_policy_registry.json"
)
STAGE_POLICY_APPROVAL_PATH = (
    ROOT
    / "evidence"
    / "authority"
    / "candidate_stage_evaluation_policy_approval_20260913.json"
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
KOREA_SYMBOL_RE = re.compile(r"^\d{6}$")
STAGES = (None, "Discovery", "Candidate", "Ready", "Buy")
STAGE_RANK = {stage: rank for rank, stage in enumerate(STAGES)}

NEW = "NEW"
MAINTAINED = "MAINTAINED"
PROMOTED = "PROMOTED"
DEMOTED = "DEMOTED"
DROPPED = "DROPPED"
LIFECYCLE_STATUSES = frozenset({NEW, MAINTAINED, PROMOTED, DEMOTED, DROPPED})

CONNECTED = "CONNECTED_BY_THIS_RECEIPT"
NO_EVIDENCE = "NO_EVIDENCE"
NOT_YET_AVAILABLE = "NOT_YET_AVAILABLE_AT_EVALUATION"
POLICY_UNDEFINED = "POLICY_UNDEFINED"
REQUIRED_STAGE_GATES = (
    "canonical_population_membership",
    "resolved_security_identity",
    "market_native_evaluation_coverage",
    "evidence_quality_status",
    "translation_status",
    "expectations_gap_status",
    "invalidation_status",
    "freshness_status",
    "active_veto_status",
)
HOLD_GATE_STATUSES = frozenset({"MISSING", "UNKNOWN", "STALE", "FAIL", "ACTIVE_VETO"})

AUTHORITY = {
    "read_only": True,
    "candidate_generation": False,
    "candidate_ranking": False,
    "system_candidate_stage_evaluation": True,
    "stage4_internal_paper_candidate_handoff": True,
    "manual_stage_mutation": False,
    "stage_promotion": True,
    "stage_exclusion": False,
    "rotation": False,
    "buy": False,
    "action": False,
    "order": False,
    "production": False,
    "trading": False,
    "real_capital": False,
}


class CandidateEvidenceLifecycleError(ValueError):
    pass


def _fail(code: str) -> None:
    raise CandidateEvidenceLifecycleError(code)


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateEvidenceLifecycleError(code) from exc
    if not isinstance(value, dict):
        _fail(f"{code}_NOT_OBJECT")
    return value


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidateEvidenceLifecycleError("SOURCE_FILE_UNREADABLE") from exc


def _path_label(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def _date(value: object, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CandidateEvidenceLifecycleError(code) from exc
    if parsed.isoformat() != value:
        _fail(code)
    return parsed


def _datetime(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateEvidenceLifecycleError(code) from exc
    if parsed.tzinfo is None:
        _fail(code)
    return parsed


def normalize_symbol(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("SYMBOL_INVALID")
    symbol = value.strip().upper()
    if symbol.endswith(".KS") and KOREA_SYMBOL_RE.fullmatch(symbol[:-3]):
        return symbol[:-3]
    return symbol


def load_watchlist(path: Path = WATCHLIST_PATH) -> dict:
    document = _read_json(path, "WATCHLIST_READ_FAILED")
    if (
        document.get("source_ref") != "collection://0d145a42-f565-43bc-97ec-cfb474d0f8ea"
        or document.get("source_name") != "Notion PM Watchlist"
        or "review-required source" not in str(document.get("note_편입사유", ""))
    ):
        _fail("WATCHLIST_SOURCE_IDENTITY_INVALID")
    captured = _datetime(document.get("fetched_at"), "WATCHLIST_FETCHED_AT_INVALID")
    results = document.get("results")
    if not isinstance(results, list) or not results:
        _fail("WATCHLIST_RESULTS_INVALID")
    rows: dict[str, dict] = {}
    for row in results:
        if not isinstance(row, dict):
            _fail("WATCHLIST_ROW_INVALID")
        symbol = normalize_symbol(row.get("티커"))
        if symbol in rows:
            _fail("WATCHLIST_SYMBOL_DUPLICATE")
        if (
            not isinstance(row.get("url"), str)
            or not row["url"].startswith("https://app.notion.com/")
            or not isinstance(row.get("종목"), str)
            or not isinstance(row.get("편입 사유"), str)
            or not row["편입 사유"].strip()
        ):
            _fail("WATCHLIST_ROW_SEMANTICS_INVALID")
        rows[symbol] = copy.deepcopy(row)
    return {
        "document": document,
        "rows": rows,
        "captured_at": captured,
        "file_sha256": _file_sha256(path),
        "path": Path(path),
    }


def load_stage_history(path: Path = STAGE_HISTORY_PATH) -> dict:
    document = _read_json(path, "STAGE_HISTORY_READ_FAILED")
    if not document:
        _fail("STAGE_HISTORY_EMPTY")
    normalized: dict[str, dict[str, dict]] = {}
    for date_key, rows in document.items():
        _date(date_key, "STAGE_HISTORY_DATE_INVALID")
        if not isinstance(rows, dict):
            _fail("STAGE_HISTORY_ROWS_INVALID")
        normalized_rows: dict[str, dict] = {}
        for raw_symbol, row in rows.items():
            symbol = normalize_symbol(raw_symbol)
            if symbol in normalized_rows or not isinstance(row, dict):
                _fail("STAGE_HISTORY_SYMBOL_INVALID")
            if (
                row.get("stage") not in STAGES
                or not isinstance(row.get("name"), str)
                or not isinstance(row.get("coverage"), bool)
                or not isinstance(row.get("collected"), bool)
            ):
                _fail("STAGE_HISTORY_ROW_INVALID")
            normalized_rows[symbol] = copy.deepcopy(row)
        normalized[date_key] = normalized_rows
    return {
        "document": normalized,
        "file_sha256": _file_sha256(path),
        "path": Path(path),
    }


def _validate_self_hash(document: dict, field: str, code: str) -> None:
    claimed = document.get(field)
    if not isinstance(claimed, str) or SHA_RE.fullmatch(claimed) is None:
        _fail(code)
    payload = copy.deepcopy(document)
    payload.pop(field, None)
    if payload_sha256(payload) != claimed:
        _fail(code)


def load_validity_assessment(path: Path = VALIDITY_PATH) -> dict:
    document = _read_json(path, "VALIDITY_ASSESSMENT_READ_FAILED")
    _validate_self_hash(document, "assessment_sha256", "VALIDITY_ASSESSMENT_HASH_MISMATCH")
    authority = document.get("authority") or {}
    if (
        document.get("contract_version") != "candidate_validity_window_assessment/1"
        or document.get("assessment_scope") != "TEMPORAL_FRESHNESS_ONLY"
        or authority.get("rule_id") != "P8-12-CANDIDATE-VALIDITY-WINDOW"
        or authority.get("rule_version") != "1"
        or document.get("window_seconds") != 172800
        or any(document.get("downstream_locks", {}).values())
    ):
        _fail("VALIDITY_ASSESSMENT_SEMANTICS_INVALID")
    operational = document.get("operational_authority")
    if not isinstance(operational, dict) or any(
        value not in (False, None, 0) for value in operational.values()
    ):
        _fail("VALIDITY_ASSESSMENT_AUTHORITY_OPENED")
    rows = document.get("candidate_assessments")
    if not isinstance(rows, list):
        _fail("VALIDITY_ASSESSMENT_ROWS_INVALID")
    by_subject: dict[tuple[str, str], dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail("VALIDITY_ASSESSMENT_ROW_INVALID")
        key = (row.get("market"), row.get("subject"))
        if not all(isinstance(value, str) for value in key) or key in by_subject:
            _fail("VALIDITY_ASSESSMENT_ROW_IDENTITY_INVALID")
        if row.get("authority") != operational:
            _fail("VALIDITY_ASSESSMENT_ROW_AUTHORITY_INVALID")
        by_subject[key] = copy.deepcopy(row)
    evaluated = _datetime(document.get("evaluation_at_utc"), "VALIDITY_EVALUATION_AT_INVALID")
    return {
        "document": document,
        "rows": by_subject,
        "evaluated_at": evaluated,
        "file_sha256": _file_sha256(path),
        "path": Path(path),
    }


def load_gate_connection_sources(
    *,
    identity_path: Path = IDENTITY_OBSERVATION_PATH,
    korea_review_contract_path: Path = KOREA_REVIEW_CONTRACT_PATH,
    us_review_contract_path: Path = US_REVIEW_CONTRACT_PATH,
    stage4_contract_path: Path = STAGE4_FUNNEL_CONTRACT_PATH,
    stage4_schema_path: Path = STAGE4_FUNNEL_SCHEMA_PATH,
) -> dict:
    identity = _read_json(identity_path, "IDENTITY_OBSERVATION_READ_FAILED")
    _validate_self_hash(identity, "packet_sha256", "IDENTITY_OBSERVATION_HASH_MISMATCH")
    if (
        identity.get("schema_version") != "candidate_identity_observation/1"
        or not isinstance(identity.get("observations"), list)
        or any(identity.get("authority", {}).values())
    ):
        _fail("IDENTITY_OBSERVATION_SEMANTICS_INVALID")
    identities = {}
    for observation in identity["observations"]:
        if not isinstance(observation, dict):
            _fail("IDENTITY_OBSERVATION_ROW_INVALID")
        market = observation.get("market")
        subject = observation.get("subject")
        if not isinstance(market, str) or not isinstance(subject, str):
            _fail("IDENTITY_OBSERVATION_ROW_INVALID")
        key = (market, normalize_symbol(subject))
        if key in identities:
            _fail("IDENTITY_OBSERVATION_ROW_DUPLICATE")
        identities[key] = copy.deepcopy(observation)

    review_contracts = {}
    for market, path, version in (
        ("KOREA", korea_review_contract_path, "korea_symbol_market_review/1"),
        ("US", us_review_contract_path, "us_symbol_market_review/1"),
    ):
        contract = _read_json(path, f"{market}_REVIEW_CONTRACT_READ_FAILED")
        subjects = contract.get("supported_pipeline_subjects")
        if (
            contract.get("contract_version") != version
            or not isinstance(subjects, list)
            or not subjects
            or any(contract.get("authority", {}).values())
        ):
            _fail(f"{market}_REVIEW_CONTRACT_INVALID")
        review_contracts[market] = {
            "document": contract,
            "subjects": frozenset(normalize_symbol(subject) for subject in subjects),
            "path": Path(path),
            "file_sha256": _file_sha256(path),
        }

    stage4_contract = _read_json(stage4_contract_path, "STAGE4_CONTRACT_READ_FAILED")
    stage4_schema = _read_json(stage4_schema_path, "STAGE4_SCHEMA_READ_FAILED")
    if (
        stage4_contract.get("contract_version")
        != "common_paper_candidate_funnel_contract/1"
        or stage4_contract.get("input_schema_version")
        != "common_paper_candidate_funnel_input/1"
        or stage4_contract.get("output_schema_version")
        != "common_paper_candidate_funnel_output/1"
        or stage4_contract.get("paper_internal_authority", {}).get("PAPER_INTERNAL_AUTO")
        is not True
        or any(stage4_contract.get("permanent_false_authority", {}).values())
        or stage4_schema.get("$id")
        != "https://atlas.local/schemas/common_paper_candidate_funnel.schema.json"
    ):
        _fail("STAGE4_CONTRACT_SEMANTICS_INVALID")
    return {
        "identity": {
            "document": identity,
            "rows": identities,
            "path": Path(identity_path),
            "file_sha256": _file_sha256(identity_path),
        },
        "review_contracts": review_contracts,
        "stage4": {
            "contract": stage4_contract,
            "contract_path": Path(stage4_contract_path),
            "contract_sha256": _file_sha256(stage4_contract_path),
            "schema_path": Path(stage4_schema_path),
            "schema_sha256": _file_sha256(stage4_schema_path),
        },
    }


def _gate_connection_audit(
    row: dict,
    inclusion: dict,
    validity: dict,
    sources: dict,
    root: Path,
    stage_history_path: Path,
) -> dict:
    market = row["market"]
    symbol = row["symbol"]
    stage_source = {
        "path": _path_label(stage_history_path, root),
        "current_stage_observation": row.get("current_stage"),
        "current_coverage_observation": row.get("current_coverage"),
    }
    identity_source = sources["identity"]
    identity_row = identity_source["rows"].get((market, symbol))
    if identity_row is None:
        identity_status = "SOURCE_ROW_ABSENT"
        identity_detail = None
    else:
        identity_detail = identity_row.get("identity", {}).get("status")
        identity_status = (
            "SOURCE_AVAILABLE_NOT_ADMITTED_AS_STAGE_GATE"
            if identity_detail == "RESOLVED"
            else "SOURCE_PRESENT_NOT_COMPUTABLE"
        )
    review = sources["review_contracts"][market]
    in_review_scope = symbol in review["subjects"]
    invalidation_available = bool(inclusion.get("exclusion_condition"))
    validity_available = validity.get("status") == "ASSESSED"
    audits = [
        {
            "gate": "canonical_population_membership",
            "connection_status": "SOURCE_AVAILABLE_NOT_ADMITTED_AS_STAGE_GATE",
            "source": stage_source,
            "owner": "MARKET_POPULATION_EVALUATOR_OWNER",
            "required_recovery": "EMIT_CANDIDATE_STAGE_GATE_INPUT_WITH_RATIFIED_POPULATION_MEMBERSHIP_PASS_SEMANTICS",
        },
        {
            "gate": "resolved_security_identity",
            "connection_status": identity_status,
            "source": {
                "path": _path_label(identity_source["path"], root),
                "file_sha256": identity_source["file_sha256"],
                "identity_status": identity_detail,
            },
            "owner": "CANONICAL_SECURITY_IDENTITY_AUTHORITY_OWNER",
            "required_recovery": (
                "ADMIT_EXISTING_RESOLVED_IDENTITY_TO_STAGE_GATE_INPUT"
                if identity_detail == "RESOLVED"
                else "CREATE_OR_CONNECT_RATIFIED_CANONICAL_IDENTITY_RECORD"
            ),
        },
        {
            "gate": "market_native_evaluation_coverage",
            "connection_status": (
                "BOUNDED_EVALUATOR_CONTRACT_AVAILABLE_GATE_ADAPTER_MISSING"
                if in_review_scope
                else "SUBJECT_OUTSIDE_BOUNDED_EVALUATOR_CONTRACT"
            ),
            "source": {
                "path": _path_label(review["path"], root),
                "file_sha256": review["file_sha256"],
                "contract_version": review["document"]["contract_version"],
                "subject_in_supported_pipeline_subjects": in_review_scope,
            },
            "owner": f"{market}_MARKET_NATIVE_EVALUATOR_OWNER",
            "required_recovery": (
                "VALIDATE_CURRENT_OUTPUT_AND_EMIT_GATE_INPUT"
                if in_review_scope
                else "EXPAND_POPULATION_EVALUATION_COVERAGE_IN_OWNER_LANE"
            ),
        },
        {
            "gate": "evidence_quality_status",
            "connection_status": "NO_PER_SYMBOL_RATIFIED_GATE_SOURCE",
            "source": None,
            "owner": f"{market}_MARKET_NATIVE_EVALUATOR_OWNER",
            "required_recovery": "EMIT_RATIFIED_EVIDENCE_QUALITY_GATE_RESULT_AND_REFS",
        },
        {
            "gate": "translation_status",
            "connection_status": "NO_CURRENT_PER_SYMBOL_GATE_SOURCE",
            "source": None,
            "owner": "ALPHA_REVIEW_TRANSLATION_OWNER",
            "required_recovery": "EMIT_CURRENT_TRANSLATION_GATE_RESULT_AND_REFS",
        },
        {
            "gate": "expectations_gap_status",
            "connection_status": "NO_CURRENT_PER_SYMBOL_GATE_SOURCE",
            "source": None,
            "owner": "EXPECTATIONS_GAP_EVALUATOR_OWNER",
            "required_recovery": "EMIT_CURRENT_EXPECTATIONS_GAP_GATE_RESULT_AND_REFS",
        },
        {
            "gate": "invalidation_status",
            "connection_status": (
                "REVIEW_PROSE_AVAILABLE_NOT_MACHINE_GATE"
                if invalidation_available
                else "NO_CURRENT_PER_SYMBOL_GATE_SOURCE"
            ),
            "source": (
                {
                    "path": inclusion.get("source", {}).get("path"),
                    "file_sha256": inclusion.get("source", {}).get("file_sha256"),
                    "authority_semantics": inclusion.get("authority_semantics"),
                }
                if invalidation_available
                else None
            ),
            "owner": "MARKET_NATIVE_INVALIDATION_EVALUATOR_OWNER",
            "required_recovery": "EMIT_MACHINE_VALIDATED_INVALIDATION_GATE_RESULT_AND_REFS",
        },
        {
            "gate": "freshness_status",
            "connection_status": (
                "P8_12_SOURCE_AVAILABLE_SCOPE_INCOMPATIBLE_WITH_STAGE_GATE"
                if validity_available
                else "NO_CURRENT_PER_SYMBOL_GATE_SOURCE"
            ),
            "source": validity.get("source") if validity_available else None,
            "owner": "MARKET_NATIVE_STAGE_FRESHNESS_OWNER",
            "required_recovery": "EMIT_STAGE_SCOPED_FRESHNESS_GATE_RESULT_WITH_EXPLICIT_REVIEW_OR_EXPIRY_TIME",
        },
        {
            "gate": "active_veto_status",
            "connection_status": "NO_CURRENT_PER_SYMBOL_GATE_SOURCE",
            "source": None,
            "owner": "STAGE_VETO_POLICY_OWNER",
            "required_recovery": "EMIT_CURRENT_ACTIVE_VETO_GATE_RESULT_AND_REFS",
        },
    ]
    return {
        "status": "AUDITED_NO_GATE_STATUS_INFERRED",
        "gate_count": len(audits),
        "gates": audits,
    }


def load_policy_boundaries(
    *,
    theme_registry_path: Path = THEME_REGISTRY_PATH,
    theme_authority_path: Path = THEME_AUTHORITY_PATH,
    korea_sector_binding_path: Path = KOREA_SECTOR_BINDING_PATH,
    rotation_ledger_contract_path: Path = ROTATION_LEDGER_CONTRACT_PATH,
) -> dict:
    registry = _read_json(theme_registry_path, "THEME_REGISTRY_READ_FAILED")
    authority = _read_json(theme_authority_path, "THEME_AUTHORITY_READ_FAILED")
    korea_binding = _read_json(korea_sector_binding_path, "KOREA_SECTOR_BINDING_READ_FAILED")
    rotation = _read_json(rotation_ledger_contract_path, "ROTATION_LEDGER_READ_FAILED")
    _validate_self_hash(korea_binding, "payload_sha256", "KOREA_SECTOR_BINDING_HASH_MISMATCH")

    registry_authority = registry.get("authority") or {}
    if (
        registry.get("schema_version") != "theme_taxonomy_source_fact_registry/1"
        or registry_authority.get("theme_membership_authorized") is not False
        or registry_authority.get("sector_chain_membership_authorized") is not False
        or authority != {"schema_version": "theme_taxonomy_authority_registry/1", "records": []}
        or korea_binding.get("contract_version") != "korea_sector_identity_binding/1"
        or korea_binding.get("binding_status") != "RATIFIED"
        or rotation.get("contract_version") != "rotation_state_ledger/1"
        or rotation.get("repository_default_policy") != "ABSENT"
        or rotation.get("authority", {}).get("candidate_ranking_authorized") is not False
        or rotation.get("authority", {}).get("stage_promotion_authorized") is not False
    ):
        _fail("POLICY_BOUNDARY_SEMANTICS_INVALID")

    sources = registry.get("sources") or []
    us_sources = [row for row in sources if row.get("market") == "US"]
    if not us_sources or any(
        row.get("approval_status") != "UNRATIFIED"
        or row.get("record_count") != 0
        or row.get("theme_membership_authorized") is not False
        for row in us_sources
    ):
        _fail("US_MEMBERSHIP_BOUNDARY_INVALID")
    return {
        "theme_registry": registry,
        "theme_registry_sha256": _file_sha256(theme_registry_path),
        "theme_authority_sha256": _file_sha256(theme_authority_path),
        "korea_sector_binding": korea_binding,
        "korea_sector_binding_sha256": _file_sha256(korea_sector_binding_path),
        "rotation_ledger": rotation,
        "rotation_ledger_sha256": _file_sha256(rotation_ledger_contract_path),
        "paths": {
            "theme_registry": Path(theme_registry_path),
            "theme_authority": Path(theme_authority_path),
            "korea_sector_binding": Path(korea_sector_binding_path),
            "rotation_ledger": Path(rotation_ledger_contract_path),
        },
    }


def load_stage_evaluation_policy(
    *,
    policy_path: Path = STAGE_POLICY_PATH,
    registry_path: Path = STAGE_POLICY_REGISTRY_PATH,
    approval_path: Path = STAGE_POLICY_APPROVAL_PATH,
) -> dict:
    policy = _read_json(policy_path, "STAGE_POLICY_READ_FAILED")
    registry = _read_json(registry_path, "STAGE_POLICY_REGISTRY_READ_FAILED")
    approval = _read_json(approval_path, "STAGE_POLICY_APPROVAL_READ_FAILED")
    records = registry.get("records")
    if (
        registry.get("schema_version")
        != "candidate_stage_evaluation_policy_registry/1"
        or not isinstance(records, list)
        or len(records) != 1
    ):
        _fail("STAGE_POLICY_REGISTRY_INVALID")
    record = records[0]
    if (
        policy.get("schema_version") != "candidate_stage_evaluation_policy/1"
        or policy.get("policy_id") != "P3-05-SEPARATE-SYSTEM-EVALUATED-STAGE"
        or policy.get("policy_version") != "1"
        or policy.get("option") != "A_SEPARATE_SYSTEM_EVALUATED_STAGE"
        or policy.get("decision_expression") != "ALL_REQUIRED_GATES_PASS"
        or policy.get("accepted_gate_status") != "PASS"
        or policy.get("manual_system_boundary")
        != "MANUAL_NOTION_TAG_RETAINED_AS_SOURCE_FACT_NEVER_REQUIRED_AS_SYSTEM_PROMOTION_INPUT"
        or policy.get("manual_stage_mutation_authorized") is not False
        or policy.get("missing_data_policy") != "HOLD_OR_NOT_COMPUTABLE_NEVER_PASS"
        or policy.get("numeric_thresholds_added") is not False
        or policy.get("system_stage_output") != "Candidate"
        or policy.get("input_contract_version") != "candidate_stage_gate_input/1"
    ):
        _fail("STAGE_POLICY_SEMANTICS_INVALID")
    gates = policy.get("required_gate_order")
    expected_transitions = [
        {"from": "EVALUATION_ELIGIBLE", "to": "Candidate", "decision": "PROMOTE"},
        {"from": "Candidate", "to": "Candidate", "decision": "MAINTAIN"},
    ]
    if (
        gates != list(REQUIRED_STAGE_GATES)
        or not isinstance(policy.get("hold_gate_statuses"), list)
        or set(policy["hold_gate_statuses"]) != HOLD_GATE_STATUSES
        or policy.get("allowed_transition_graph") != expected_transitions
        or policy.get("demotion_drop_policy")
        != "ONLY_SEPARATELY_RATIFIED_MARKET_NATIVE_INVALIDATION_OR_EXIT_MAY_DEMOTE_OR_DROP_MISSING_EVIDENCE_NEVER_DOES"
        or policy.get("reentry_policy")
        != "FRESH_FULL_EVALUATION_AND_NEW_EVIDENCE_REFS_REQUIRED"
    ):
        _fail("STAGE_POLICY_GATES_INVALID")
    expected_authority = {
        "system_candidate_stage_evaluation": True,
        "stage4_internal_paper_candidate_handoff": True,
        "manual_stage_mutation": False,
        "candidate_ranking": False,
        "buy": False,
        "action": False,
        "order": False,
        "production": False,
        "trading": False,
        "real_capital": False,
    }
    if policy.get("authority") != expected_authority:
        _fail("STAGE_POLICY_AUTHORITY_INVALID")
    expected_approved_scope = {
        "manual_watchlist_stage_is_observation_only": True,
        "system_evaluated_stage_is_independent": True,
        "all_required_current_gates_must_pass": True,
        "missing_unknown_stale_or_active_veto_holds": True,
        "stage4_internal_paper_candidate_handoff": True,
    }
    expected_not_approved = {
        "NUMERIC_INVESTMENT_THRESHOLD",
        "CROSS_MARKET_SCORE",
        "SECURITY_TO_THEME_INFERENCE",
        "MANUAL_NOTION_STAGE_MUTATION",
        "READY_OR_BUY_STAGE",
        "CAPITAL_ALLOCATION",
        "POSITION_SIZE",
        "ORDER_OR_BROKER_ACTION",
        "PRODUCTION_OR_LIVE_TRADING_AUTHORITY",
    }
    if (
        approval.get("schema_version") != "candidate_stage_evaluation_policy_approval/1"
        or approval.get("source_kind") != "EXPLICIT_USER_APPROVAL_IN_IMPLEMENTATION_THREAD"
        or approval.get("approved_recommendation") != policy["option"]
        or approval.get("policy_id") != policy["policy_id"]
        or approval.get("policy_version") != policy["policy_version"]
        or approval.get("historical_backfill_authorized") is not False
        or approval.get("approved_scope") != expected_approved_scope
        or not isinstance(approval.get("not_approved"), list)
        or set(approval["not_approved"]) != expected_not_approved
    ):
        _fail("STAGE_POLICY_APPROVAL_INVALID")
    effective = _datetime(
        approval.get("effective_from"), "STAGE_POLICY_EFFECTIVE_FROM_INVALID"
    )
    ratified = _datetime(
        approval.get("ratified_at"), "STAGE_POLICY_RATIFIED_AT_INVALID"
    )
    if effective != ratified:
        _fail("STAGE_POLICY_EFFECTIVE_TIME_INVALID")
    expected_record = {
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "record_state": "CURRENT",
        "ratified_at": approval["ratified_at"],
        "effective_from": approval["effective_from"],
        "content_ref": "config/candidate_stage_evaluation_policy_v1.json",
        "content_sha256": _file_sha256(policy_path),
        "authority_evidence_ref": "evidence/authority/candidate_stage_evaluation_policy_approval_20260913.json",
        "authority_evidence_sha256": _file_sha256(approval_path),
    }
    if record != expected_record:
        _fail("STAGE_POLICY_REGISTRY_BINDING_INVALID")
    return {
        "policy": policy,
        "approval": approval,
        "record": record,
        "effective_from": effective,
        "paths": {
            "policy": Path(policy_path),
            "registry": Path(registry_path),
            "approval": Path(approval_path),
        },
        "hashes": {
            "policy": _file_sha256(policy_path),
            "registry": _file_sha256(registry_path),
            "approval": _file_sha256(approval_path),
        },
    }


def _validate_gate_input(
    row: dict,
    gate_input: dict | None,
    stage_policy: dict,
    evaluation_at: dt.datetime,
) -> dict:
    policy = stage_policy["policy"]
    base = {
        "manual_watchlist_stage_observation": row.get("current_stage"),
        "manual_stage_used_as_promotion_input": False,
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "policy_effective_from": stage_policy["approval"]["effective_from"],
        "system_evaluated_stage": None,
        "derived_transition": "HOLD",
        "system_candidate_eligible": False,
        "stage4_internal_paper_eligible": False,
    }
    if evaluation_at < stage_policy["effective_from"]:
        return {
            **base,
            "input_status": "POLICY_NOT_YET_EFFECTIVE",
            "first_blocker": "POLICY_NOT_YET_EFFECTIVE",
            "gate_results": [],
        }
    if gate_input is None:
        return {
            **base,
            "input_status": "NOT_CONNECTED",
            "first_blocker": "canonical_population_membership:GATE_INPUT_NOT_CONNECTED",
            "gate_results": [],
        }
    if not isinstance(gate_input, dict):
        _fail("STAGE_GATE_INPUT_INVALID")
    input_time = _datetime(
        gate_input.get("evaluation_at_utc"), "STAGE_GATE_INPUT_TIME_INVALID"
    )
    if input_time < stage_policy["effective_from"] or input_time > evaluation_at:
        _fail("STAGE_GATE_INPUT_TIME_OUT_OF_BOUNDS")
    if (
        gate_input.get("contract_version") != policy["input_contract_version"]
        or normalize_symbol(gate_input.get("symbol")) != row["symbol"]
        or gate_input.get("market") != row["market"]
        or not isinstance(gate_input.get("market_native_evidence_contract"), str)
        or not gate_input["market_native_evidence_contract"].strip()
        or not isinstance(gate_input.get("reviewer_identity"), str)
        or not gate_input["reviewer_identity"].strip()
    ):
        _fail("STAGE_GATE_INPUT_IDENTITY_INVALID")
    review_time = _datetime(
        gate_input.get("review_or_expiry_time_utc"),
        "STAGE_GATE_INPUT_REVIEW_TIME_INVALID",
    )
    if review_time < input_time:
        _fail("STAGE_GATE_INPUT_REVIEW_TIME_PRECEDES_EVALUATION")
    gates = gate_input.get("gates")
    required = policy["required_gate_order"]
    if not isinstance(gates, dict) or set(gates) != set(required):
        _fail("STAGE_GATE_INPUT_GATES_INVALID")
    allowed = {policy["accepted_gate_status"], *policy["hold_gate_statuses"]}
    results = []
    first_blocker = None
    for name in required:
        gate = gates[name]
        if not isinstance(gate, dict) or gate.get("status") not in allowed:
            _fail("STAGE_GATE_RESULT_INVALID")
        refs = gate.get("evidence_refs")
        if not isinstance(refs, list) or (gate["status"] == "PASS" and not refs):
            _fail("STAGE_GATE_EVIDENCE_REFS_INVALID")
        normalized_refs = []
        for evidence in refs:
            if (
                not isinstance(evidence, dict)
                or not isinstance(evidence.get("ref"), str)
                or not evidence["ref"].strip()
                or not isinstance(evidence.get("sha256"), str)
                or SHA_RE.fullmatch(evidence["sha256"]) is None
            ):
                _fail("STAGE_GATE_EVIDENCE_REF_INVALID")
            available = _datetime(
                evidence.get("available_at_utc"),
                "STAGE_GATE_EVIDENCE_AVAILABLE_AT_INVALID",
            )
            if available > input_time:
                _fail("STAGE_GATE_EVIDENCE_NOT_POINT_IN_TIME")
            normalized_refs.append(copy.deepcopy(evidence))
        result = {
            "gate": name,
            "status": gate["status"],
            "evidence_refs": normalized_refs,
        }
        results.append(result)
        if first_blocker is None and gate["status"] != policy["accepted_gate_status"]:
            first_blocker = f"{name}:{gate['status']}"
    passed = first_blocker is None
    return {
        **base,
        "input_status": "VALIDATED",
        "evaluation_at_utc": gate_input["evaluation_at_utc"],
        "review_or_expiry_time_utc": gate_input["review_or_expiry_time_utc"],
        "reviewer_identity": gate_input["reviewer_identity"],
        "market_native_evidence_contract": gate_input["market_native_evidence_contract"],
        "gate_results": results,
        "first_blocker": first_blocker,
        "system_evaluated_stage": policy["system_stage_output"] if passed else None,
        "derived_transition": "PROMOTE" if passed else "HOLD",
        "system_candidate_eligible": passed,
        "stage4_internal_paper_eligible": False,
    }


def _lifecycle_status(previous: dict | None, current: dict | None) -> str:
    if previous is None and current is not None:
        return NEW
    if previous is not None and (
        current is None or (previous.get("coverage") and not current.get("coverage"))
    ):
        return DROPPED
    if previous is None or current is None:
        _fail("LIFECYCLE_STATE_INVALID")
    before = STAGE_RANK[previous.get("stage")]
    after = STAGE_RANK[current.get("stage")]
    if after > before:
        return PROMOTED
    if after < before:
        return DEMOTED
    return MAINTAINED


def classify_stage_history(stage_history: dict, as_of_date: str | None = None) -> dict:
    dates = sorted(stage_history)
    if not dates:
        _fail("STAGE_HISTORY_EMPTY")
    selected = as_of_date or dates[-1]
    if selected not in stage_history:
        _fail("AS_OF_DATE_NOT_IN_STAGE_HISTORY")
    index = dates.index(selected)
    previous_date = dates[index - 1] if index else None
    previous_rows = {} if previous_date is None else stage_history[previous_date]
    current_rows = stage_history[selected]
    rows = []
    for symbol in sorted(set(previous_rows) | set(current_rows)):
        previous = previous_rows.get(symbol)
        current = current_rows.get(symbol)
        rows.append({
            "symbol": symbol,
            "market": "KOREA" if KOREA_SYMBOL_RE.fullmatch(symbol) else "US",
            "name": (current or previous)["name"],
            "lifecycle_status": _lifecycle_status(previous, current),
            "previous_stage": None if previous is None else previous.get("stage"),
            "current_stage": None if current is None else current.get("stage"),
            "previous_coverage": None if previous is None else previous.get("coverage"),
            "current_coverage": None if current is None else current.get("coverage"),
        })
    return {"as_of_date": selected, "previous_date": previous_date, "rows": rows}


def _inclusion_evidence(symbol: str, watchlist: dict, as_of_date: str, root: Path) -> dict:
    row = watchlist["rows"].get(symbol)
    source = {
        "path": _path_label(watchlist["path"], root),
        "file_sha256": watchlist["file_sha256"],
        "source_ref": watchlist["document"]["source_ref"],
        "source_name": watchlist["document"]["source_name"],
        "captured_at": watchlist["document"]["fetched_at"],
        "availability_semantics": "CAPTURED_AT_IS_EARLIEST_REPOSITORY_AVAILABILITY_KNOWN_HERE",
    }
    available_date = watchlist["captured_at"].astimezone(ZoneInfo("Asia/Seoul")).date()
    if available_date > _date(as_of_date, "AS_OF_DATE_INVALID"):
        return {"status": NOT_YET_AVAILABLE, "reason": None, "source": source}
    if row is None:
        return {"status": NO_EVIDENCE, "reason": None, "source": source}
    return {
        "status": "RETAINED_REVIEW_REQUIRED_EVIDENCE",
        "reason": row["편입 사유"],
        "exclusion_condition": row.get("탈락 조건"),
        "source": {**source, "notion_row_url": row["url"]},
        "authority_semantics": "HISTORICAL_REVIEW_EVIDENCE_ONLY_NOT_CANDIDATE_OR_STAGE_AUTHORITY",
    }


def _transition_reason(row: dict) -> dict:
    status = row["lifecycle_status"]
    if status == MAINTAINED:
        return {
            "status": "DERIVED_FROM_STAGE_HISTORY",
            "code": "STAGE_AND_COVERAGE_UNCHANGED",
            "text": None,
        }
    return {
        "status": NO_EVIDENCE,
        "code": "DECISION_REASON_NOT_RECORDED_IN_STAGE_HISTORY",
        "text": None,
    }


def _validity_evidence(symbol: str, market: str, validity: dict, as_of_date: str, root: Path) -> dict:
    source = {
        "path": _path_label(validity["path"], root),
        "file_sha256": validity["file_sha256"],
        "assessment_sha256": validity["document"]["assessment_sha256"],
        "evaluated_at_utc": validity["document"]["evaluation_at_utc"],
        "rule_id": validity["document"]["authority"]["rule_id"],
        "rule_version": validity["document"]["authority"]["rule_version"],
    }
    available_date = validity["evaluated_at"].astimezone(ZoneInfo("Asia/Seoul")).date()
    if available_date > _date(as_of_date, "AS_OF_DATE_INVALID"):
        return {"status": NOT_YET_AVAILABLE, "source": source}
    row = validity["rows"].get((market, symbol))
    if row is None:
        return {"status": NO_EVIDENCE, "source": source}
    return {
        "status": "ASSESSED",
        "temporal_status": row.get("temporal_status"),
        "lifecycle_state": row.get("lifecycle_state"),
        "expires_at_utc": row.get("expires_at_utc"),
        "t0_operational_evaluated_at_utc": row.get("t0_operational_evaluated_at_utc"),
        "source": source,
        "scope_boundary": "TEMPORAL_FRESHNESS_ONLY_NEVER_STAGE_PROMOTION_OR_EXCLUSION",
    }


def _sector_rotation_boundary(market: str, policy: dict, root: Path) -> dict:
    paths = policy["paths"]
    common = {
        "theme_source_registry": {
            "path": _path_label(paths["theme_registry"], root),
            "file_sha256": policy["theme_registry_sha256"],
            "theme_membership_authorized": False,
        },
        "theme_authority_registry": {
            "path": _path_label(paths["theme_authority"], root),
            "file_sha256": policy["theme_authority_sha256"],
            "record_count": 0,
        },
    }
    if market == "KOREA":
        market_boundary = {
            "status": "SECTOR_SERIES_BINDING_EXISTS_SECURITY_BINDING_ABSENT",
            "path": _path_label(paths["korea_sector_binding"], root),
            "file_sha256": policy["korea_sector_binding_sha256"],
            "contract_version": policy["korea_sector_binding"]["contract_version"],
            "binding_status": policy["korea_sector_binding"]["binding_status"],
            "scope": "SECTOR_SERIES_IDENTITY_TO_THEME_ID_ONLY",
        }
    else:
        market_boundary = {
            "status": "UNRATIFIED_EMPTY_US_MEMBERSHIP_SOURCE",
            "record_count": 0,
            "scope": "NO_SECURITY_TO_THEME_MEMBERSHIP",
        }
    return {
        "symbol_to_sector_binding": {
            "status": POLICY_UNDEFINED,
            "reason": "NO_RATIFIED_SECURITY_TO_SECTOR_OR_THEME_MEMBERSHIP",
            "evidence": {**common, "market_boundary": market_boundary},
        },
        "rotation_ledger_link": {
            "status": POLICY_UNDEFINED,
            "reason": "P2_05_STATE_POLICY_ABSENT_AND_NO_SYMBOL_TO_ROTATION_ENTITY_BINDING",
            "evidence": {
                "path": _path_label(paths["rotation_ledger"], root),
                "file_sha256": policy["rotation_ledger_sha256"],
                "contract_version": policy["rotation_ledger"]["contract_version"],
                "repository_default_policy": "ABSENT",
            },
        },
    }


def build_receipt(
    *,
    generated_at_utc: str,
    as_of_date: str | None = None,
    root: Path = ROOT,
    watchlist_path: Path = WATCHLIST_PATH,
    stage_history_path: Path = STAGE_HISTORY_PATH,
    validity_path: Path = VALIDITY_PATH,
    system_gate_inputs: dict[str, dict] | None = None,
) -> dict:
    generated = _datetime(generated_at_utc, "GENERATED_AT_INVALID")
    if generated.utcoffset() != dt.timedelta(0):
        _fail("GENERATED_AT_NOT_UTC")
    root = Path(root)
    watchlist = load_watchlist(watchlist_path)
    stage = load_stage_history(stage_history_path)
    validity = load_validity_assessment(validity_path)
    policy = load_policy_boundaries()
    stage_policy = load_stage_evaluation_policy()
    connection_sources = load_gate_connection_sources()
    delta = classify_stage_history(stage["document"], as_of_date)
    selected_date = delta["as_of_date"]
    if generated.astimezone(ZoneInfo("Asia/Seoul")).date() < _date(selected_date, "AS_OF_DATE_INVALID"):
        _fail("GENERATED_AT_PRECEDES_AS_OF_DATE")

    normalized_gate_inputs: dict[str, dict] = {}
    if system_gate_inputs is not None:
        if not isinstance(system_gate_inputs, dict):
            _fail("STAGE_GATE_INPUTS_INVALID")
        for raw_symbol, gate_input in system_gate_inputs.items():
            symbol = normalize_symbol(raw_symbol)
            if symbol in normalized_gate_inputs:
                _fail("STAGE_GATE_INPUT_SYMBOL_DUPLICATE")
            normalized_gate_inputs[symbol] = copy.deepcopy(gate_input)

    records = []
    sector_cache = {
        market: _sector_rotation_boundary(market, policy, root)
        for market in ("KOREA", "US")
    }
    for row in delta["rows"]:
        inclusion = _inclusion_evidence(row["symbol"], watchlist, selected_date, root)
        validity_evidence = _validity_evidence(
            row["symbol"], row["market"], validity, selected_date, root
        )
        system_evaluation = _validate_gate_input(
            row,
            normalized_gate_inputs.get(row["symbol"]),
            stage_policy,
            generated,
        )
        records.append({
            **row,
            "evidence_as_of": {"value": selected_date, "precision": "DATE_ONLY"},
            "classification_rule": {
                "rule_id": "MECHANICAL-STAGE-HISTORY-DELTA",
                "rule_version": "1",
                "semantics": "OBSERVED_DELTA_ONLY_NOT_STAGE_POLICY",
            },
            "transition_reason": _transition_reason(row),
            "inclusion_reason": inclusion,
            "candidate_validity": validity_evidence,
            "gate_connection_audit": _gate_connection_audit(
                row,
                inclusion,
                validity_evidence,
                connection_sources,
                root,
                stage_history_path,
            ),
            "system_stage_evaluation": system_evaluation,
            "sector_rotation": copy.deepcopy(sector_cache[row["market"]]),
        })

    counts = Counter(row["lifecycle_status"] for row in records)
    system_candidate_symbols = [
        row["symbol"]
        for row in records
        if row["system_stage_evaluation"]["system_candidate_eligible"]
    ]
    document = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "generated_at_utc": generated_at_utc,
        "generated_at_semantics": "RECEIPT_BUILD_TIME_ONLY_NEVER_SOURCE_TIME",
        "as_of_date": selected_date,
        "previous_evaluation_date": delta["previous_date"],
        "source_lineage": {
            "stage_history": {
                "path": _path_label(stage["path"], root),
                "file_sha256": stage["file_sha256"],
            },
            "watchlist": {
                "path": _path_label(watchlist["path"], root),
                "file_sha256": watchlist["file_sha256"],
                "source_ref": watchlist["document"]["source_ref"],
                "captured_at": watchlist["document"]["fetched_at"],
            },
            "candidate_validity": {
                "path": _path_label(validity["path"], root),
                "file_sha256": validity["file_sha256"],
                "assessment_sha256": validity["document"]["assessment_sha256"],
            },
            "candidate_stage_policy": {
                "policy_path": _path_label(stage_policy["paths"]["policy"], root),
                "policy_sha256": stage_policy["hashes"]["policy"],
                "registry_path": _path_label(stage_policy["paths"]["registry"], root),
                "registry_sha256": stage_policy["hashes"]["registry"],
                "authority_evidence_path": _path_label(stage_policy["paths"]["approval"], root),
                "authority_evidence_sha256": stage_policy["hashes"]["approval"],
            },
            "candidate_identity_observation": {
                "path": _path_label(connection_sources["identity"]["path"], root),
                "file_sha256": connection_sources["identity"]["file_sha256"],
                "packet_sha256": connection_sources["identity"]["document"]["packet_sha256"],
            },
            "market_native_review_contracts": {
                market: {
                    "path": _path_label(source["path"], root),
                    "file_sha256": source["file_sha256"],
                    "contract_version": source["document"]["contract_version"],
                }
                for market, source in connection_sources["review_contracts"].items()
            },
            "stage4_common_funnel": {
                "contract_path": _path_label(connection_sources["stage4"]["contract_path"], root),
                "contract_sha256": connection_sources["stage4"]["contract_sha256"],
                "contract_version": connection_sources["stage4"]["contract"]["contract_version"],
                "input_schema_version": connection_sources["stage4"]["contract"]["input_schema_version"],
                "schema_path": _path_label(connection_sources["stage4"]["schema_path"], root),
                "schema_sha256": connection_sources["stage4"]["schema_sha256"],
            },
        },
        "gap_classification": [
            {
                "field": "inclusion_reason",
                "prior_root_cause": "IMPLEMENTATION_NOT_CONNECTED",
                "current_status": CONNECTED,
                "boundary": "REVIEW_REQUIRED_EVIDENCE_NOT_STAGE_AUTHORITY",
            },
            {
                "field": "candidate_validity_and_expiry",
                "prior_root_cause": "CONSUMER_MISSING_EXISTING_RATIFIED_RULE",
                "current_status": CONNECTED,
                "boundary": "P8_12_TEMPORAL_FRESHNESS_ONLY",
            },
            {
                "field": "symbol_to_sector_binding",
                "root_cause": POLICY_UNDEFINED,
                "current_status": "BLOCKED_NO_INFERENCE",
            },
            {
                "field": "rotation_ledger_link",
                "root_cause": POLICY_UNDEFINED,
                "current_status": "BLOCKED_NO_INFERENCE",
            },
            {
                "field": "stage_promotion_hold_exclusion_rule",
                "prior_root_cause": POLICY_UNDEFINED,
                "current_status": CONNECTED,
                "boundary": "RATIFIED_CANDIDATE_ONLY_ALL_REQUIRED_NO_NUMERIC_THRESHOLD",
            },
        ],
        "policy_decision": {
            "status": (
                "RATIFIED_ACTIVE"
                if generated >= stage_policy["effective_from"]
                else "RATIFIED_NOT_YET_EFFECTIVE"
            ),
            "selected_option": stage_policy["policy"]["option"],
            "policy_id": stage_policy["policy"]["policy_id"],
            "policy_version": stage_policy["policy"]["policy_version"],
            "ratified_at": stage_policy["approval"]["ratified_at"],
            "effective_from": stage_policy["approval"]["effective_from"],
            "gate_semantics": "ALL_REQUIRED_RATIFIED_FIELDS_MUST_PASS_UNKNOWN_MISSING_STALE_FAIL_OR_ACTIVE_VETO_HOLDS",
            "missing_data_policy": stage_policy["policy"]["missing_data_policy"],
            "manual_system_boundary": stage_policy["policy"]["manual_system_boundary"],
            "market_policy": stage_policy["policy"]["market_policy"],
            "numeric_thresholds_added": False,
        },
        "lifecycle_summary": {
            "record_count": len(records),
            "status_counts": {status: counts.get(status, 0) for status in sorted(LIFECYCLE_STATUSES)},
        },
        "lifecycle_records": records,
        "downstream_handoff": {
            "consumer": "STAGE4_INTERNAL_PAPER_CANDIDATE_INPUT",
            "status": (
                "SYSTEM_CANDIDATE_AVAILABLE_STAGE4_COMMON_FUNNEL_INPUT_REQUIRED"
                if system_candidate_symbols
                else (
                    "RATIFIED_STAGE_POLICY_ACTIVE_NO_SYSTEM_CANDIDATE"
                    if generated >= stage_policy["effective_from"]
                    else "RATIFIED_POLICY_NOT_YET_EFFECTIVE"
                )
            ),
            "evidence_query_record_count": len(records),
            "system_candidate_record_count": len(system_candidate_symbols),
            "system_candidate_symbols": system_candidate_symbols,
            "stage4_eligible_record_count": 0,
            "stage4_eligible_symbols": [],
            "first_blocker_counts": dict(
                Counter(
                    row["system_stage_evaluation"]["first_blocker"]
                    for row in records
                    if not row["system_stage_evaluation"]["system_candidate_eligible"]
                )
            ),
            "policy_binding": {
                "policy_id": stage_policy["policy"]["policy_id"],
                "policy_version": stage_policy["policy"]["policy_version"],
                "effective_from": stage_policy["approval"]["effective_from"],
            },
            "stage4_contract_reuse": {
                "contract_version": connection_sources["stage4"]["contract"]["contract_version"],
                "input_schema_version": connection_sources["stage4"]["contract"]["input_schema_version"],
                "contract_sha256": connection_sources["stage4"]["contract_sha256"],
                "schema_sha256": connection_sources["stage4"]["schema_sha256"],
                "adapter_status": "NOT_CONNECTED",
                "required_candidate_fields": [
                    "scoreBreakdown",
                    "completedBarTrigger",
                    "hardGates",
                    "risk",
                    "sourceTimestamp",
                    "ttlSeconds",
                    "sourceRefs",
                ],
                "boundary": "SYSTEM_CANDIDATE_ALONE_NEVER_ASSERTS_STAGE4_OR_PAPER_BUY_ELIGIBILITY",
            },
        },
        "authority": copy.deepcopy(AUTHORITY),
    }
    document["receipt_sha256"] = payload_sha256(document)
    return document


def validate_receipt(
    document: dict,
    *,
    root: Path = ROOT,
    watchlist_path: Path = WATCHLIST_PATH,
    stage_history_path: Path = STAGE_HISTORY_PATH,
    validity_path: Path = VALIDITY_PATH,
    system_gate_inputs: dict[str, dict] | None = None,
    rederive: bool = True,
) -> dict:
    _validate_self_hash(document, "receipt_sha256", "RECEIPT_HASH_MISMATCH")
    if (
        document.get("schema_version") != SCHEMA_VERSION
        or document.get("contract_version") != CONTRACT_VERSION
        or document.get("authority") != AUTHORITY
        or not isinstance(document.get("lifecycle_records"), list)
    ):
        _fail("RECEIPT_SEMANTICS_INVALID")
    records = document["lifecycle_records"]
    counts = Counter()
    seen = set()
    for row in records:
        symbol = row.get("symbol")
        if symbol in seen or row.get("lifecycle_status") not in LIFECYCLE_STATUSES:
            _fail("RECEIPT_LIFECYCLE_ROW_INVALID")
        seen.add(symbol)
        counts[row["lifecycle_status"]] += 1
        if row.get("classification_rule", {}).get("semantics") != "OBSERVED_DELTA_ONLY_NOT_STAGE_POLICY":
            _fail("RECEIPT_CLASSIFICATION_AUTHORITY_INVALID")
        if row.get("sector_rotation", {}).get("symbol_to_sector_binding", {}).get("status") != POLICY_UNDEFINED:
            _fail("RECEIPT_SYMBOL_SECTOR_POLICY_INVENTED")
        evaluation = row.get("system_stage_evaluation") or {}
        system_eligible = evaluation.get("system_candidate_eligible")
        stage4_eligible = evaluation.get("stage4_internal_paper_eligible")
        audit = row.get("gate_connection_audit") or {}
        if (
            evaluation.get("manual_stage_used_as_promotion_input") is not False
            or system_eligible not in (True, False)
            or stage4_eligible is not False
            or audit.get("status") != "AUDITED_NO_GATE_STATUS_INFERRED"
            or [gate.get("gate") for gate in audit.get("gates", [])]
            != list(REQUIRED_STAGE_GATES)
            or (
                system_eligible
                and (
                    evaluation.get("system_evaluated_stage") != "Candidate"
                    or evaluation.get("derived_transition") != "PROMOTE"
                    or evaluation.get("first_blocker") is not None
                )
            )
            or (
                not system_eligible
                and (
                    evaluation.get("system_evaluated_stage") is not None
                    or evaluation.get("derived_transition") != "HOLD"
                    or not isinstance(evaluation.get("first_blocker"), str)
                )
            )
        ):
            _fail("RECEIPT_SYSTEM_STAGE_EVALUATION_INVALID")
    expected_counts = {
        status: counts.get(status, 0) for status in sorted(LIFECYCLE_STATUSES)
    }
    if document.get("lifecycle_summary") != {
        "record_count": len(records),
        "status_counts": expected_counts,
    }:
        _fail("RECEIPT_SUMMARY_MISMATCH")
    policy_decision = document.get("policy_decision") or {}
    if (
        policy_decision.get("status") not in {"RATIFIED_ACTIVE", "RATIFIED_NOT_YET_EFFECTIVE"}
        or policy_decision.get("selected_option") != "A_SEPARATE_SYSTEM_EVALUATED_STAGE"
        or policy_decision.get("missing_data_policy") != "HOLD_OR_NOT_COMPUTABLE_NEVER_PASS"
        or policy_decision.get("manual_system_boundary") != "MANUAL_NOTION_TAG_RETAINED_AS_SOURCE_FACT_NEVER_REQUIRED_AS_SYSTEM_PROMOTION_INPUT"
        or policy_decision.get("numeric_thresholds_added") is not False
    ):
        _fail("RECEIPT_POLICY_DECISION_BOUNDARY_INVALID")
    handoff = document.get("downstream_handoff") or {}
    system_candidate_symbols = [
        row["symbol"]
        for row in records
        if row["system_stage_evaluation"]["system_candidate_eligible"]
    ]
    if system_candidate_symbols:
        expected_status = "SYSTEM_CANDIDATE_AVAILABLE_STAGE4_COMMON_FUNNEL_INPUT_REQUIRED"
    elif policy_decision.get("status") == "RATIFIED_ACTIVE":
        expected_status = "RATIFIED_STAGE_POLICY_ACTIVE_NO_SYSTEM_CANDIDATE"
    else:
        expected_status = "RATIFIED_POLICY_NOT_YET_EFFECTIVE"
    if (
        handoff.get("status") != expected_status
        or handoff.get("system_candidate_record_count")
        != len(system_candidate_symbols)
        or handoff.get("system_candidate_symbols") != system_candidate_symbols
        or handoff.get("stage4_eligible_record_count") != 0
        or handoff.get("stage4_eligible_symbols") != []
        or handoff.get("evidence_query_record_count") != len(records)
        or handoff.get("stage4_contract_reuse", {}).get("adapter_status")
        != "NOT_CONNECTED"
        or handoff.get("stage4_contract_reuse", {}).get("boundary")
        != "SYSTEM_CANDIDATE_ALONE_NEVER_ASSERTS_STAGE4_OR_PAPER_BUY_ELIGIBILITY"
    ):
        _fail("RECEIPT_DOWNSTREAM_HANDOFF_INVALID")
    if rederive:
        rebuilt = build_receipt(
            generated_at_utc=document.get("generated_at_utc"),
            as_of_date=document.get("as_of_date"),
            root=root,
            watchlist_path=watchlist_path,
            stage_history_path=stage_history_path,
            validity_path=validity_path,
            system_gate_inputs=system_gate_inputs,
        )
        if rebuilt != document:
            _fail("RECEIPT_SOURCE_REDERIVATION_MISMATCH")
    return copy.deepcopy(document)


def lookup_symbol(document: dict, symbol: str) -> dict:
    validate_receipt(document)
    normalized = normalize_symbol(symbol)
    matches = [row for row in document["lifecycle_records"] if row["symbol"] == normalized]
    if len(matches) != 1:
        _fail("SYMBOL_NOT_IN_RECEIPT")
    return copy.deepcopy(matches[0])


def load_system_gate_inputs_from_market_native_adapter(
    *, evaluation_at_utc: str, review_or_expiry_time_utc: str | None = None
) -> dict[str, dict]:
    """Optional hook: connect discovery.candidate_stage_gate_input_adapter's
    current output (STAGE3-CANDIDATE-GATE-INPUT-ADAPTER-001) as this
    receipt's ``system_gate_inputs``. Imported lazily so this module carries
    no unconditional dependency on the adapter. The adapter alone decides
    per-symbol gate results for the five bounded Korea/US subjects; every
    other subject and every other gate is unaffected. Absent unless a
    caller explicitly asks for it (see ``--connect-market-native-adapter``).
    """
    from discovery.candidate_stage_gate_input_adapter import build_gate_inputs

    return build_gate_inputs(
        evaluation_at_utc=evaluation_at_utc,
        review_or_expiry_time_utc=review_or_expiry_time_utc,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-at-utc", required=True)
    parser.add_argument("--as-of-date")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--connect-market-native-adapter",
        action="store_true",
        help=(
            "Connect discovery.candidate_stage_gate_input_adapter's current "
            "output as system_gate_inputs. Default off; other eight required "
            "gates remain MISSING regardless, so this alone still cannot "
            "promote a system Candidate."
        ),
    )
    args = parser.parse_args()
    system_gate_inputs = None
    if args.connect_market_native_adapter:
        system_gate_inputs = load_system_gate_inputs_from_market_native_adapter(
            evaluation_at_utc=args.generated_at_utc,
        )
    document = build_receipt(
        generated_at_utc=args.generated_at_utc,
        as_of_date=args.as_of_date,
        system_gate_inputs=system_gate_inputs,
    )
    validate_receipt(document)
    payload = canonical_json(document) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
