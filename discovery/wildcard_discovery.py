#!/usr/bin/env python3
"""P3-11 evidence-linked Wildcard Discovery case recorder.

Explicit out-of-theme nominations are kept separate from confirmed evidence.
At least one source-linked evidence item is required to record a case.  The
path never claims strength, ranks a candidate, promotes a Stage or creates an
action.  Unsupported nominations remain pending rather than becoming cases.
"""
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
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "wildcard_discovery_contract.json"
INPUT_SCHEMA_VERSION = "wildcard_discovery_input/1"
OUTPUT_SCHEMA_VERSION = "wildcard_discovery_packet/1"
CASE_SCHEMA_VERSION = "wildcard_discovery_case/1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class WildcardDiscoveryError(ValueError):
    """Fail-closed wildcard intake violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WildcardDiscoveryError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "wildcard_discovery/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "case_schema_version": CASE_SCHEMA_VERSION,
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "market_sources": {
            "CRYPTO": ["defillama_stablecoins_api", "kraken_public_api"],
            "KOREA": ["dart_open_api", "krx_open_api_stock_daily"],
            "US": ["microsoft_sec_issuer_disclosure", "sec_edgar", "tsmc_investor_relations"],
        },
        "source_hosts": {
            "dart_open_api": ["opendart.fss.or.kr"],
            "defillama_stablecoins_api": ["stablecoins.llama.fi"],
            "kraken_public_api": ["api.kraken.com"],
            "krx_open_api_stock_daily": ["data-dbg.krx.co.kr"],
            "microsoft_sec_issuer_disclosure": ["www.sec.gov"],
            "sec_edgar": ["data.sec.gov", "www.sec.gov"],
            "tsmc_investor_relations": ["investor.tsmc.com"],
        },
        "theme_membership_statuses": ["OUTSIDE_CURRENT_TAXONOMY", "UNRESOLVED"],
        "evidence_statuses": ["EVIDENCE_BLOCKED", "EVIDENCE_LINKED", "EVIDENCE_UNRESOLVED"],
        "case_evidence_statuses": ["EVIDENCE_LINKED", "EVIDENCE_PARTIAL"],
        "nomination_authority": "OBSERVATION_ONLY",
        "minimum_linked_evidence_for_case": 1,
        "maximum_evidence_items": 10,
        "policy_status": {
            "strength_threshold": "UNRATIFIED",
            "importance_ranking": "UNRATIFIED",
            "theme_taxonomy_completeness": "UNRATIFIED",
            "source_hierarchy": "UNRATIFIED",
            "candidate_ranking": "UNRATIFIED",
        },
        "authority": {
            "case_recording_only": True,
            "nomination_text_is_confirmed_fact": False,
            "strength_claim_authorized": False,
            "importance_ranking_authorized": False,
            "candidate_eligibility_authorized": False,
            "stage_promotion_authorized": False,
            "rule_evaluation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict):
        raise WildcardDiscoveryError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise WildcardDiscoveryError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise WildcardDiscoveryError("CONTRACT_FIELDS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _valid_date(value) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_utc(value) -> bool:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return False
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") == value
    except ValueError:
        return False


def _utc(value: str) -> dt.datetime:
    if not _valid_utc(value):
        raise WildcardDiscoveryError(f"UTC_INVALID:{value!r}")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _nonempty_text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise WildcardDiscoveryError(code)
    return value


def _validate_source(source: dict, market: str, nominated_at: dt.datetime, as_of: dt.datetime, contract: dict, context: str) -> dict:
    fields = {"source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"}
    if not isinstance(source, dict) or set(source) != fields:
        raise WildcardDiscoveryError(f"SOURCE_IDENTITY_FIELDS_MISMATCH:{context}")
    source_id = source.get("source_id")
    if source_id not in contract["market_sources"][market]:
        raise WildcardDiscoveryError(f"SOURCE_ID_NOT_ALLOWED:{context}:{source_id}")
    parsed = urlparse(str(source.get("source_url") or ""))
    if (
        parsed.scheme != "https" or parsed.hostname not in contract["source_hosts"][source_id]
        or parsed.username is not None or parsed.password is not None
    ):
        raise WildcardDiscoveryError(f"SOURCE_URL_INVALID:{context}")
    if not isinstance(source.get("source_sha256"), str) or SHA256_RE.fullmatch(source["source_sha256"]) is None:
        raise WildcardDiscoveryError(f"SOURCE_SHA256_INVALID:{context}")
    available = source.get("available_at")
    retrieved = source.get("retrieved_at_utc")
    if not (_valid_date(available) or _valid_utc(available)) or not _valid_utc(retrieved):
        raise WildcardDiscoveryError(f"SOURCE_TIME_INVALID:{context}")
    retrieved_time = _utc(retrieved)
    if _valid_date(available):
        available_after_retrieval = dt.date.fromisoformat(available) > retrieved_time.date()
        available_after_nomination = dt.date.fromisoformat(available) > nominated_at.date()
    else:
        available_time = _utc(available)
        available_after_retrieval = available_time > retrieved_time
        available_after_nomination = available_time > nominated_at
    if (
        available_after_retrieval or available_after_nomination
        or retrieved_time > nominated_at or nominated_at > as_of
    ):
        raise WildcardDiscoveryError(f"SOURCE_TEMPORAL_ORDER_INVALID:{context}")
    return copy.deepcopy(source)


def _validate_evidence(item: dict, market: str, nominated_at: dt.datetime, as_of: dt.datetime, contract: dict) -> dict:
    fields = {
        "evidence_id", "status", "claim_text", "missing_reasons",
        "source_identity", "audit_provenance",
    }
    if not isinstance(item, dict) or set(item) != fields:
        raise WildcardDiscoveryError("EVIDENCE_FIELDS_MISMATCH")
    evidence_id = item.get("evidence_id")
    if not isinstance(evidence_id, str) or TOKEN_RE.fullmatch(evidence_id) is None:
        raise WildcardDiscoveryError("EVIDENCE_ID_INVALID")
    status = item.get("status")
    if status not in contract["evidence_statuses"]:
        raise WildcardDiscoveryError(f"EVIDENCE_STATUS_INVALID:{evidence_id}:{status}")
    reasons = item.get("missing_reasons")
    if not isinstance(reasons, list) or not all(isinstance(value, str) and value for value in reasons):
        raise WildcardDiscoveryError(f"MISSING_REASONS_INVALID:{evidence_id}")
    if status != "EVIDENCE_LINKED":
        if item.get("claim_text") is not None or item.get("source_identity") is not None or item.get("audit_provenance") is not None or not reasons:
            raise WildcardDiscoveryError(f"UNLINKED_EVIDENCE_INCONSISTENT:{evidence_id}")
        return copy.deepcopy(item)
    claim = _nonempty_text(item.get("claim_text"), f"EVIDENCE_CLAIM_INVALID:{evidence_id}")
    if reasons:
        raise WildcardDiscoveryError(f"LINKED_EVIDENCE_INCONSISTENT:{evidence_id}")
    provenance = item.get("audit_provenance")
    if not isinstance(provenance, dict) or not provenance or not all(isinstance(key, str) and key for key in provenance):
        raise WildcardDiscoveryError(f"AUDIT_PROVENANCE_INVALID:{evidence_id}")
    checked = copy.deepcopy(item)
    checked["claim_text"] = claim
    checked["source_identity"] = _validate_source(
        item.get("source_identity"), market, nominated_at, as_of, contract, evidence_id
    )
    return checked


def _case_id(submission: dict) -> str:
    seed = {
        "submission_id": submission["submission_id"],
        "market": submission["market"],
        "asset_id": submission["asset_id"],
        "observed_on": submission["observed_on"],
    }
    return "RADAR-WC-" + payload_sha256(seed)[:16].upper()


def _case_from_submission(submission: dict, contract: dict) -> dict | None:
    linked = [
        item for item in submission["evidence"] if item["status"] == "EVIDENCE_LINKED"
    ]
    unlinked = [
        item for item in submission["evidence"] if item["status"] != "EVIDENCE_LINKED"
    ]
    if len(linked) < contract["minimum_linked_evidence_for_case"]:
        return None
    return {
        "schema_version": contract["case_schema_version"],
        "case_id": _case_id(submission),
        "market": submission["market"],
        "asset_id": submission["asset_id"],
        "subject": submission["subject"],
        "observation_date": submission["observed_on"],
        "discovery_path": "WILDCARD_OUTSIDE_THEME",
        "theme_membership_status": submission["theme_membership_status"],
        "theme_ids": [],
        "why_found": {
            "basis": "EXPLICIT_WILDCARD_NOMINATION_WITH_SOURCE_LINKED_EVIDENCE",
            "strength_status": "UNRATIFIED",
            "importance_status": "UNRATIFIED",
        },
        "nomination": copy.deepcopy(submission["nomination"]),
        "evidence_status": "EVIDENCE_LINKED" if not unlinked else "EVIDENCE_PARTIAL",
        "linked_evidence": [
            {
                "evidence_id": item["evidence_id"],
                "claim_text": item["claim_text"],
                "claim_status": "SOURCE_LINKED_OBSERVATION_NOT_INTERPRETED",
                "source_identity": copy.deepcopy(item["source_identity"]),
                "audit_provenance": copy.deepcopy(item["audit_provenance"]),
            }
            for item in linked
        ],
        "unresolved_evidence": [
            {
                "evidence_id": item["evidence_id"],
                "status": item["status"],
                "missing_reasons": copy.deepcopy(item["missing_reasons"]),
            }
            for item in unlinked
        ],
        "strength_status": "UNRATIFIED",
        "importance": "UNRATIFIED",
        "candidate_eligible": False,
        "candidate_rank": None,
        "stage_transition": None,
        "rule_evaluation": None,
        "action": None,
    }


def _submission_result(value: dict, as_of: dt.datetime, contract: dict) -> tuple[dict, dict | None]:
    fields = {
        "submission_id", "market", "asset_id", "subject", "observed_on",
        "theme_membership_status", "theme_ids", "nominated_by", "nominated_at_utc",
        "nomination_authority", "submission_reason", "hypothesis", "evidence",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise WildcardDiscoveryError("SUBMISSION_FIELDS_MISMATCH")
    submission_id = value.get("submission_id")
    asset_id = value.get("asset_id")
    if not isinstance(submission_id, str) or TOKEN_RE.fullmatch(submission_id) is None:
        raise WildcardDiscoveryError("SUBMISSION_ID_INVALID")
    if not isinstance(asset_id, str) or TOKEN_RE.fullmatch(asset_id) is None:
        raise WildcardDiscoveryError(f"ASSET_ID_INVALID:{submission_id}")
    market = value.get("market")
    if market not in contract["allowed_markets"]:
        raise WildcardDiscoveryError(f"MARKET_INVALID:{submission_id}:{market}")
    subject = _nonempty_text(value.get("subject"), f"SUBJECT_INVALID:{submission_id}")
    observed_on = value.get("observed_on")
    if not _valid_date(observed_on) or dt.date.fromisoformat(observed_on) > as_of.date():
        raise WildcardDiscoveryError(f"OBSERVED_ON_INVALID:{submission_id}")
    theme_status = value.get("theme_membership_status")
    if theme_status not in contract["theme_membership_statuses"]:
        raise WildcardDiscoveryError(f"THEME_MEMBERSHIP_STATUS_INVALID:{submission_id}")
    if value.get("theme_ids") != []:
        raise WildcardDiscoveryError(f"WILDCARD_THEME_IDS_FORBIDDEN:{submission_id}")
    nominated_by = _nonempty_text(value.get("nominated_by"), f"NOMINATOR_INVALID:{submission_id}")
    nominated_at_utc = value.get("nominated_at_utc")
    if (
        not _valid_utc(nominated_at_utc)
        or _utc(nominated_at_utc) > as_of
        or dt.date.fromisoformat(observed_on) > _utc(nominated_at_utc).date()
    ):
        raise WildcardDiscoveryError(f"NOMINATED_AT_INVALID:{submission_id}")
    if value.get("nomination_authority") != contract["nomination_authority"]:
        raise WildcardDiscoveryError(f"NOMINATION_AUTHORITY_INVALID:{submission_id}")
    reason = _nonempty_text(value.get("submission_reason"), f"SUBMISSION_REASON_INVALID:{submission_id}")
    hypothesis = _nonempty_text(value.get("hypothesis"), f"HYPOTHESIS_INVALID:{submission_id}")
    evidence = value.get("evidence")
    if (
        not isinstance(evidence, list) or not evidence
        or len(evidence) > contract["maximum_evidence_items"]
    ):
        raise WildcardDiscoveryError(f"EVIDENCE_COUNT_INVALID:{submission_id}")
    checked = [
        _validate_evidence(item, market, _utc(nominated_at_utc), as_of, contract)
        for item in evidence
    ]
    checked.sort(key=lambda item: item["evidence_id"])
    evidence_ids = [item["evidence_id"] for item in checked]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise WildcardDiscoveryError(f"EVIDENCE_ID_DUPLICATE:{submission_id}")
    linked = [item for item in checked if item["status"] == "EVIDENCE_LINKED"]
    unlinked = [item for item in checked if item["status"] != "EVIDENCE_LINKED"]
    case_created = len(linked) >= contract["minimum_linked_evidence_for_case"]
    normalized = {
        "submission_id": submission_id, "market": market, "asset_id": asset_id,
        "subject": subject, "observed_on": observed_on,
        "theme_membership_status": theme_status, "theme_ids": [],
        "nomination": {
            "nominated_by": nominated_by, "nominated_at_utc": nominated_at_utc,
            "authority": contract["nomination_authority"], "submission_reason": reason,
            "hypothesis": hypothesis, "text_status": "UNCONFIRMED_NOMINATION_TEXT",
        },
        "linked_evidence_count": len(linked), "unlinked_evidence_count": len(unlinked),
        "case_created": case_created,
        "pending_reason": None if case_created else "NO_SOURCE_LINKED_EVIDENCE",
        "evidence": copy.deepcopy(checked),
    }
    return normalized, _case_from_submission(normalized, contract)


def _validate_output_submission(value: dict, as_of: dt.datetime, contract: dict) -> dict:
    fields = {
        "submission_id",
        "market",
        "asset_id",
        "subject",
        "observed_on",
        "theme_membership_status",
        "theme_ids",
        "nomination",
        "linked_evidence_count",
        "unlinked_evidence_count",
        "case_created",
        "pending_reason",
        "evidence",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise WildcardDiscoveryError("OUTPUT_SUBMISSION_FIELDS_MISMATCH")
    submission_id = value.get("submission_id")
    asset_id = value.get("asset_id")
    market = value.get("market")
    if not isinstance(submission_id, str) or TOKEN_RE.fullmatch(submission_id) is None:
        raise WildcardDiscoveryError("OUTPUT_SUBMISSION_ID_INVALID")
    if not isinstance(asset_id, str) or TOKEN_RE.fullmatch(asset_id) is None:
        raise WildcardDiscoveryError(f"OUTPUT_ASSET_ID_INVALID:{submission_id}")
    if market not in contract["allowed_markets"]:
        raise WildcardDiscoveryError(f"OUTPUT_MARKET_INVALID:{submission_id}")
    _nonempty_text(value.get("subject"), f"OUTPUT_SUBJECT_INVALID:{submission_id}")
    observed_on = value.get("observed_on")
    if not _valid_date(observed_on) or dt.date.fromisoformat(observed_on) > as_of.date():
        raise WildcardDiscoveryError(f"OUTPUT_OBSERVED_ON_INVALID:{submission_id}")
    if value.get("theme_membership_status") not in contract["theme_membership_statuses"]:
        raise WildcardDiscoveryError(f"OUTPUT_THEME_STATUS_INVALID:{submission_id}")
    if value.get("theme_ids") != []:
        raise WildcardDiscoveryError(f"OUTPUT_THEME_IDS_FORBIDDEN:{submission_id}")

    nomination = value.get("nomination")
    if not isinstance(nomination, dict) or set(nomination) != {
        "nominated_by",
        "nominated_at_utc",
        "authority",
        "submission_reason",
        "hypothesis",
        "text_status",
    }:
        raise WildcardDiscoveryError(f"OUTPUT_NOMINATION_FIELDS_MISMATCH:{submission_id}")
    _nonempty_text(
        nomination.get("nominated_by"), f"OUTPUT_NOMINATOR_INVALID:{submission_id}"
    )
    nominated_at_utc = nomination.get("nominated_at_utc")
    if (
        not _valid_utc(nominated_at_utc)
        or _utc(nominated_at_utc) > as_of
        or dt.date.fromisoformat(observed_on) > _utc(nominated_at_utc).date()
    ):
        raise WildcardDiscoveryError(f"OUTPUT_NOMINATED_AT_INVALID:{submission_id}")
    if (
        nomination.get("authority") != contract["nomination_authority"]
        or nomination.get("text_status") != "UNCONFIRMED_NOMINATION_TEXT"
    ):
        raise WildcardDiscoveryError(f"OUTPUT_NOMINATION_AUTHORITY_INVALID:{submission_id}")
    _nonempty_text(
        nomination.get("submission_reason"),
        f"OUTPUT_SUBMISSION_REASON_INVALID:{submission_id}",
    )
    _nonempty_text(
        nomination.get("hypothesis"), f"OUTPUT_HYPOTHESIS_INVALID:{submission_id}"
    )

    evidence = value.get("evidence")
    if (
        not isinstance(evidence, list)
        or not evidence
        or len(evidence) > contract["maximum_evidence_items"]
    ):
        raise WildcardDiscoveryError(f"OUTPUT_EVIDENCE_COUNT_INVALID:{submission_id}")
    checked = [
        _validate_evidence(
            item, market, _utc(nominated_at_utc), as_of, contract
        )
        for item in evidence
    ]
    evidence_ids = [item["evidence_id"] for item in checked]
    if evidence_ids != sorted(set(evidence_ids)):
        raise WildcardDiscoveryError(f"OUTPUT_EVIDENCE_ORDER_INVALID:{submission_id}")
    linked_count = sum(item["status"] == "EVIDENCE_LINKED" for item in checked)
    unlinked_count = len(checked) - linked_count
    case_created = linked_count >= contract["minimum_linked_evidence_for_case"]
    pending_reason = None if case_created else "NO_SOURCE_LINKED_EVIDENCE"
    if (
        type(value.get("linked_evidence_count")) is not int
        or value["linked_evidence_count"] != linked_count
        or type(value.get("unlinked_evidence_count")) is not int
        or value["unlinked_evidence_count"] != unlinked_count
        or value.get("case_created") is not case_created
        or value.get("pending_reason") != pending_reason
    ):
        raise WildcardDiscoveryError(f"OUTPUT_SUBMISSION_SUMMARY_MISMATCH:{submission_id}")
    return copy.deepcopy(value)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    """Validate a persisted wildcard packet from its retained submissions."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version",
        "contract_version",
        "as_of_utc",
        "status",
        "submission_count",
        "case_count",
        "pending_count",
        "submissions",
        "cases",
        "source_coverage",
        "policy_status",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise WildcardDiscoveryError("OUTPUT_FIELDS_MISMATCH")
    digest = packet.get("payload_sha256")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        raise WildcardDiscoveryError("OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != digest:
        raise WildcardDiscoveryError("OUTPUT_SHA256_MISMATCH")
    as_of_utc = packet.get("as_of_utc")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "WILDCARD_INTAKE_RECORDED"
        or not _valid_utc(as_of_utc)
    ):
        raise WildcardDiscoveryError("OUTPUT_IDENTITY_MISMATCH")
    submissions = packet.get("submissions")
    if not isinstance(submissions, list) or not submissions:
        raise WildcardDiscoveryError("OUTPUT_SUBMISSIONS_EMPTY")
    checked = [
        _validate_output_submission(value, _utc(as_of_utc), contract)
        for value in submissions
    ]
    submission_ids = [value["submission_id"] for value in checked]
    if submission_ids != sorted(set(submission_ids)):
        raise WildcardDiscoveryError("OUTPUT_SUBMISSION_ORDER_INVALID")
    expected_cases = [
        case
        for case in (_case_from_submission(value, contract) for value in checked)
        if case is not None
    ]
    expected_cases.sort(key=lambda value: value["case_id"])
    if packet.get("cases") != expected_cases:
        raise WildcardDiscoveryError("OUTPUT_CASE_DERIVATION_MISMATCH")
    expected_boundaries = [
        "STRENGTH_THRESHOLD_UNRATIFIED",
        "IMPORTANCE_RANKING_UNRATIFIED",
        "THEME_TAXONOMY_COMPLETENESS_UNRATIFIED",
        "SOURCE_HIERARCHY_UNRATIFIED",
        "CANDIDATE_RANKING_UNRATIFIED",
        "LIVE_WILDCARD_INTAKE_NOT_IMPLEMENTED",
        "TRACKED_CASE_PUBLICATION_NOT_IMPLEMENTED",
    ]
    case_count = len(expected_cases)
    if (
        type(packet.get("submission_count")) is not int
        or packet["submission_count"] != len(checked)
        or type(packet.get("case_count")) is not int
        or packet["case_count"] != case_count
        or type(packet.get("pending_count")) is not int
        or packet["pending_count"] != len(checked) - case_count
        or packet.get("source_coverage") != contract["market_sources"]
        or packet.get("policy_status") != contract["policy_status"]
        or packet.get("authority") != contract["authority"]
        or packet.get("unresolved_boundaries") != expected_boundaries
    ):
        raise WildcardDiscoveryError("OUTPUT_SUMMARY_OR_BOUNDARY_MISMATCH")
    return copy.deepcopy(packet)


def build_packet(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise WildcardDiscoveryError("INPUT_SCHEMA_MISMATCH")
    if set(value) != {"schema_version", "as_of_utc", "submissions"}:
        raise WildcardDiscoveryError("INPUT_FIELDS_MISMATCH")
    as_of_utc = value.get("as_of_utc")
    if not _valid_utc(as_of_utc):
        raise WildcardDiscoveryError("AS_OF_UTC_INVALID")
    submissions = value.get("submissions")
    if not isinstance(submissions, list) or not submissions:
        raise WildcardDiscoveryError("SUBMISSIONS_EMPTY")
    normalized = []
    cases = []
    seen = set()
    for raw in submissions:
        result, case = _submission_result(raw, _utc(as_of_utc), contract)
        if result["submission_id"] in seen:
            raise WildcardDiscoveryError(f"SUBMISSION_ID_DUPLICATE:{result['submission_id']}")
        seen.add(result["submission_id"])
        normalized.append(result)
        if case is not None:
            cases.append(case)
    normalized.sort(key=lambda item: item["submission_id"])
    cases.sort(key=lambda item: item["case_id"])
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"], "as_of_utc": as_of_utc,
        "status": "WILDCARD_INTAKE_RECORDED", "submission_count": len(normalized),
        "case_count": len(cases), "pending_count": len(normalized) - len(cases),
        "submissions": normalized, "cases": cases,
        "source_coverage": copy.deepcopy(contract["market_sources"]),
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "STRENGTH_THRESHOLD_UNRATIFIED", "IMPORTANCE_RANKING_UNRATIFIED",
            "THEME_TAXONOMY_COMPLETENESS_UNRATIFIED", "SOURCE_HIERARCHY_UNRATIFIED",
            "CANDIDATE_RANKING_UNRATIFIED", "LIVE_WILDCARD_INTAKE_NOT_IMPLEMENTED",
            "TRACKED_CASE_PUBLICATION_NOT_IMPLEMENTED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise WildcardDiscoveryError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(input_path)))
        return 0
    except (WildcardDiscoveryError, OSError, TypeError, ValueError) as exc:
        print(f"wildcard discovery failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Record evidence-linked wildcard Discovery Cases")
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
