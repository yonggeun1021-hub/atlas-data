#!/usr/bin/env python3
"""Evidence-only candidate inclusion and lifecycle receipt.

This sidecar connects retained PM Watchlist rationale and the ratified P8-12
temporal-freshness assessment without changing candidate, Stage, rotation, or
trading policy.  Missing security-to-sector and Stage-transition policy stays
explicitly undefined; no mapping or threshold is inferred from prose.
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


CONTRACT_VERSION = "candidate_evidence_lifecycle_receipt/1"
SCHEMA_VERSION = 1

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

AUTHORITY = {
    "read_only": True,
    "candidate_generation": False,
    "candidate_ranking": False,
    "stage_promotion": False,
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
) -> dict:
    generated = _datetime(generated_at_utc, "GENERATED_AT_INVALID")
    if generated.utcoffset() != dt.timedelta(0):
        _fail("GENERATED_AT_NOT_UTC")
    root = Path(root)
    watchlist = load_watchlist(watchlist_path)
    stage = load_stage_history(stage_history_path)
    validity = load_validity_assessment(validity_path)
    policy = load_policy_boundaries()
    delta = classify_stage_history(stage["document"], as_of_date)
    selected_date = delta["as_of_date"]
    if generated.astimezone(ZoneInfo("Asia/Seoul")).date() < _date(selected_date, "AS_OF_DATE_INVALID"):
        _fail("GENERATED_AT_PRECEDES_AS_OF_DATE")

    records = []
    sector_cache = {
        market: _sector_rotation_boundary(market, policy, root)
        for market in ("KOREA", "US")
    }
    for row in delta["rows"]:
        inclusion = _inclusion_evidence(row["symbol"], watchlist, selected_date, root)
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
            "candidate_validity": _validity_evidence(
                row["symbol"], row["market"], validity, selected_date, root
            ),
            "sector_rotation": copy.deepcopy(sector_cache[row["market"]]),
        })

    counts = Counter(row["lifecycle_status"] for row in records)
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
                "root_cause": POLICY_UNDEFINED,
                "current_status": "BLOCKED_NO_THRESHOLD_INVENTION",
            },
        ],
        "policy_decision_request": {
            "status": "AWAITING_CIO_RATIFICATION",
            "recommended_option": "A_SEPARATE_SYSTEM_EVALUATED_STAGE",
            "options": [
                {
                    "option": "A_SEPARATE_SYSTEM_EVALUATED_STAGE",
                    "effect": "RATIFIED_ALL_REQUIRED_GATES_DERIVE_SYSTEM_STAGE_WITHOUT_MUTATING_MANUAL_WATCHLIST_TAG",
                    "numeric_thresholds_added": False,
                },
                {
                    "option": "B_HUMAN_STAGE_REVIEW_DECISION_CONTRACT",
                    "effect": "EVIDENCE_OPENS_REVIEW_ONLY_EXPLICIT_CIO_PM_DECISION_CHANGES_STAGE",
                    "numeric_thresholds_added": False,
                },
                {
                    "option": "C_RETAIN_EVIDENCE_ONLY_HOLD",
                    "effect": "NO_STAGE_ADJUDICATION_PATH_ADDED",
                    "numeric_thresholds_added": False,
                },
            ],
            "minimum_fields_for_option_a": [
                "manual_watchlist_stage_observation",
                "system_evaluated_stage",
                "allowed_transition_graph",
                "canonical_population_membership",
                "resolved_security_identity",
                "market_native_evaluation_coverage",
                "evidence_quality_status",
                "translation_status",
                "expectations_gap_status",
                "invalidation_status",
                "freshness_status",
                "active_veto_status",
                "derived_transition_PROMOTE_HOLD_DEMOTE_or_DROP",
                "transition_reason",
                "evidence_refs_and_point_in_time_availability",
                "rule_version_and_effective_from",
                "review_or_expiry_time_without_invented_default",
                "reentry_decision_and_new_evidence_refs",
                "market_native_evidence_contract",
                "reviewer_identity",
            ],
            "option_a_gate_semantics": "ALL_REQUIRED_RATIFIED_FIELDS_MUST_PASS_UNKNOWN_MISSING_OR_STALE_HOLDS",
            "missing_data_policy": "HOLD_OR_NOT_COMPUTABLE_NEVER_PASS",
            "manual_system_boundary": "MANUAL_NOTION_TAG_RETAINED_AS_SOURCE_FACT_NEVER_REQUIRED_AS_SYSTEM_PROMOTION_INPUT",
            "market_policy": "COMMON_STAGE_ENVELOPE_MARKET_NATIVE_EVALUATORS_NO_CROSS_MARKET_SCORE_OR_SECTOR_THEME_INFERENCE",
        },
        "lifecycle_summary": {
            "record_count": len(records),
            "status_counts": {status: counts.get(status, 0) for status in sorted(LIFECYCLE_STATUSES)},
        },
        "lifecycle_records": records,
        "downstream_handoff": {
            "consumer": "STAGE4_INTERNAL_PAPER_CANDIDATE_INPUT",
            "status": "NOT_ADMISSIBLE_STAGE_POLICY_UNRATIFIED",
            "evidence_query_record_count": len(records),
            "stage4_eligible_record_count": 0,
            "first_blocker": "RATIFIED_STAGE_REVIEW_OR_PROMOTION_POLICY_ABSENT",
            "required_next_receipt": "CIO_RATIFIED_STAGE_POLICY_ID_VERSION_EFFECTIVE_FROM_AND_DECISION_EVIDENCE",
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
    expected_counts = {
        status: counts.get(status, 0) for status in sorted(LIFECYCLE_STATUSES)
    }
    if document.get("lifecycle_summary") != {
        "record_count": len(records),
        "status_counts": expected_counts,
    }:
        _fail("RECEIPT_SUMMARY_MISMATCH")
    policy_request = document.get("policy_decision_request") or {}
    if (
        policy_request.get("status") != "AWAITING_CIO_RATIFICATION"
        or policy_request.get("recommended_option") != "A_SEPARATE_SYSTEM_EVALUATED_STAGE"
        or policy_request.get("missing_data_policy") != "HOLD_OR_NOT_COMPUTABLE_NEVER_PASS"
        or policy_request.get("manual_system_boundary") != "MANUAL_NOTION_TAG_RETAINED_AS_SOURCE_FACT_NEVER_REQUIRED_AS_SYSTEM_PROMOTION_INPUT"
        or any(option.get("numeric_thresholds_added") is not False for option in policy_request.get("options", []))
    ):
        _fail("RECEIPT_POLICY_DECISION_BOUNDARY_INVALID")
    handoff = document.get("downstream_handoff") or {}
    if (
        handoff.get("status") != "NOT_ADMISSIBLE_STAGE_POLICY_UNRATIFIED"
        or handoff.get("stage4_eligible_record_count") != 0
        or handoff.get("evidence_query_record_count") != len(records)
    ):
        _fail("RECEIPT_DOWNSTREAM_AUTHORITY_OPENED")
    if rederive:
        rebuilt = build_receipt(
            generated_at_utc=document.get("generated_at_utc"),
            as_of_date=document.get("as_of_date"),
            root=root,
            watchlist_path=watchlist_path,
            stage_history_path=stage_history_path,
            validity_path=validity_path,
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-at-utc", required=True)
    parser.add_argument("--as-of-date")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    document = build_receipt(
        generated_at_utc=args.generated_at_utc,
        as_of_date=args.as_of_date,
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
