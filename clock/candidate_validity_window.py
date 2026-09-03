#!/usr/bin/env python3
"""Ratified P8-12 Recommendation A temporal-freshness assessment.

The result is diagnostic temporal state only.  It cannot create candidate,
risk, entry, order, broker, capital, production, or trading authority.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock.candidate_lifecycle_observation import (
    CHAIN_NATURAL,
    CONTRACT_VERSION as LIFECYCLE_CONTRACT_VERSION,
    FIRST_SEEN_FORWARD,
    NATURAL_SAMPLE,
    load_and_validate_lifecycle_observation,
)
from clock.candidate_validity_observation import load_and_validate_observation
from clock.review_candidate import AUTHORITY_ALL_FALSE
from decision.reflection_evidence_authority import validate_authority as validate_p8_10_authority
from identity import candidate_identity_observation as candidate_identity
from identity import canonical_identity
from replay.opportunity_trigger import canonical_json, payload_sha256


CONTRACT_VERSION = "candidate_validity_window_assessment/1"
REGISTRY_PATH = ROOT / "config" / "candidate_validity_window_authority_registry.json"
DEFAULT_DYNAMIC_ROOT = ROOT / "evidence" / "operational" / "dynamic_clock"
DEFAULT_LIFECYCLE_ROOT = DEFAULT_DYNAMIC_ROOT / "candidate_lifecycle_observations"
DEFAULT_IDENTITY_PATH = DEFAULT_DYNAMIC_ROOT / "candidate_identity_observation.json"
DEFAULT_OUTPUT = DEFAULT_DYNAMIC_ROOT / "candidate_validity_window_assessment.json"

RULE_ID = "P8-12-CANDIDATE-VALIDITY-WINDOW"
RULE_VERSION = "1"
RATIFIED_AT = "2026-09-02T21:18:58Z"
EFFECTIVE_FROM = "2026-09-02T21:18:58Z"
CONTENT_REF = "config/candidate_validity_window_authority_content_v1.json"
CONTENT_SHA256 = "d2396d935ee1a4805b86577c039825aa2e549acda8393a6458ad80563be284a2"
EVIDENCE_REF = "evidence/authority/p8_12_candidate_validity_window_approval_20260903.json"
EVIDENCE_SHA256 = "342482f1501eeeb196d211cfef3f460f43b21fa05fd6f4cab465f24902017813"
WINDOW_SECONDS = 172800

ELIGIBLE_TRIGGERS = frozenset({
    "FLOW_REVERSAL",
    "INVALIDATION_TRIGGER",
    "PRICE_CONFIRMATION",
    "RELATIVE_STRENGTH_REVERSAL",
})
UNVALIDATED_TRIGGERS = frozenset({
    "CATALYST_APPROACH",
    "EXPECTATION_DISLOCATION",
    "FUNDAMENTAL_REVISION",
})
KNOWN_TRIGGERS = ELIGIBLE_TRIGGERS | UNVALIDATED_TRIGGERS

FRESH = "FRESH_TEMPORAL"
STALE = "STALE_TEMPORAL"
MISSING = "MISSING_INVALID"
NO_T0 = "NOT_COMPUTABLE_NO_EXACT_FORWARD_T0"
UNVALIDATED = "NOT_COMPUTABLE_UNVALIDATED_TRIGGER_FAMILY"
P8_10_LINK_FAILED = "NOT_COMPUTABLE_P8_10_LINK_FAILED"

UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class CandidateValidityWindowError(ValueError):
    pass


def _utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise CandidateValidityWindowError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise CandidateValidityWindowError(code) from exc
    return parsed


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CandidateValidityWindowError(code) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateValidityWindowError(f"{code}_UNREADABLE") from exc
    if not isinstance(value, dict):
        raise CandidateValidityWindowError(f"{code}_NOT_OBJECT")
    return value


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CandidateValidityWindowError("AUTHORITY_ARTIFACT_UNREADABLE") from exc


def _resolve_ref(root: Path, value: object, expected: str, code: str) -> Path:
    if value != expected:
        raise CandidateValidityWindowError(code)
    candidate = (root / expected).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise CandidateValidityWindowError(code) from exc
    if not candidate.is_file():
        raise CandidateValidityWindowError(code)
    return candidate


def validate_window_authority(
    evaluation_at_utc: str,
    *,
    registry_path: Path = REGISTRY_PATH,
    root: Path = ROOT,
) -> dict:
    """Validate exact Recommendation A authority for this evaluation."""
    evaluation = _utc(evaluation_at_utc, "AUTHORITY_EVALUATION_AT_INVALID")
    registry = _read_json(Path(registry_path), "AUTHORITY_REGISTRY_MISSING")
    if set(registry) != {"schema_version", "records"} or registry.get(
        "schema_version"
    ) != "candidate_validity_window_authority_registry/1":
        raise CandidateValidityWindowError("AUTHORITY_REGISTRY_IDENTITY_MISMATCH")
    records = registry.get("records")
    if not isinstance(records, list):
        raise CandidateValidityWindowError("AUTHORITY_RECORDS_INVALID")
    matching = [
        row for row in records
        if isinstance(row, dict) and row.get("rule_id") == RULE_ID
    ]
    if len(matching) != 1:
        raise CandidateValidityWindowError("AUTHORITY_RECORD_MISSING_OR_AMBIGUOUS")
    record = matching[0]
    expected_record = {
        "rule_id": RULE_ID,
        "rule_version": RULE_VERSION,
        "record_state": "CURRENT",
        "ratified_at": RATIFIED_AT,
        "effective_from": EFFECTIVE_FROM,
        "content_ref": CONTENT_REF,
        "content_sha256": CONTENT_SHA256,
        "authority_evidence_ref": EVIDENCE_REF,
        "authority_evidence_sha256": EVIDENCE_SHA256,
    }
    if record != expected_record:
        raise CandidateValidityWindowError("AUTHORITY_IMMUTABLE_IDENTITY_MISMATCH")
    if any(SHA_RE.fullmatch(record[field]) is None for field in (
        "content_sha256", "authority_evidence_sha256"
    )):
        raise CandidateValidityWindowError("AUTHORITY_HASH_INVALID")
    ratified = _utc(record["ratified_at"], "AUTHORITY_RATIFIED_AT_INVALID")
    effective = _utc(record["effective_from"], "AUTHORITY_EFFECTIVE_FROM_INVALID")
    if effective < ratified:
        raise CandidateValidityWindowError("AUTHORITY_EFFECTIVE_FROM_PRECEDES_RATIFICATION")
    if evaluation < ratified:
        raise CandidateValidityWindowError("AUTHORITY_RATIFICATION_IN_FUTURE")
    if evaluation < effective:
        raise CandidateValidityWindowError("AUTHORITY_NOT_YET_EFFECTIVE")

    root = Path(root)
    content_path = _resolve_ref(root, record["content_ref"], CONTENT_REF, "AUTHORITY_CONTENT_MISSING")
    evidence_path = _resolve_ref(root, record["authority_evidence_ref"], EVIDENCE_REF, "AUTHORITY_EVIDENCE_MISSING")
    if _sha256_file(content_path) != CONTENT_SHA256:
        raise CandidateValidityWindowError("AUTHORITY_CONTENT_TAMPERED")
    if _sha256_file(evidence_path) != EVIDENCE_SHA256:
        raise CandidateValidityWindowError("AUTHORITY_EVIDENCE_TAMPERED")
    content = _read_json(content_path, "AUTHORITY_CONTENT_MISSING")
    evidence = _read_json(evidence_path, "AUTHORITY_EVIDENCE_MISSING")
    if (
        content.get("schema_version") != "candidate_validity_window_authority_content/1"
        or content.get("rule_id") != RULE_ID
        or content.get("rule_version") != RULE_VERSION
        or content.get("scope") != "TEMPORAL_FRESHNESS_ONLY"
        or content.get("eligible_trigger_families") != sorted(ELIGIBLE_TRIGGERS)
        or content.get("unvalidated_trigger_families") != sorted(UNVALIDATED_TRIGGERS)
        or content.get("window", {}).get("elapsed_seconds") != WINDOW_SECONDS
        or content.get("t0_policy", {}).get("historical_backfill_authorized") is not False
    ):
        raise CandidateValidityWindowError("AUTHORITY_CONTENT_SEMANTICS_MISMATCH")
    boundary = content.get("authority_boundary")
    if (
        not isinstance(boundary, dict)
        or boundary.get("candidate") != "NONE"
        or boundary.get("capital") != 0
        or boundary.get("trade_proposal") is not None
        or any(value is not False for key, value in boundary.items() if key not in {
            "candidate", "capital", "trade_proposal"
        })
    ):
        raise CandidateValidityWindowError("AUTHORITY_BOUNDARY_OPENED")
    if (
        evidence.get("schema_version") != "candidate_validity_window_authority_approval/1"
        or evidence.get("source_kind") != "SUPERVISING_CIO_DECISION_UNDER_EXPLICIT_USER_DELEGATION"
        or evidence.get("source_thread_id") != "01a0485e-777d-7462-9454-f602a86edcd9"
        or evidence.get("implementation_task_id") != "01a063f3-7a08-7cf3-9ed1-4ec96dce4e96"
        or evidence.get("approved_recommendation") != "A"
        or evidence.get("rule_id") != RULE_ID
        or evidence.get("rule_version") != RULE_VERSION
        or evidence.get("ratified_at") != RATIFIED_AT
        or evidence.get("effective_from") != EFFECTIVE_FROM
        or evidence.get("historical_backfill_authorized") is not False
    ):
        raise CandidateValidityWindowError("AUTHORITY_EVIDENCE_IDENTITY_MISMATCH")
    return {
        "record": copy.deepcopy(record),
        "content": copy.deepcopy(content),
        "authority_evidence": copy.deepcopy(evidence),
    }


def _relative_under(path: Path, root: Path, code: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise CandidateValidityWindowError(code) from exc


def _safe_relative(value: object, prefix: str, code: str) -> str:
    if not isinstance(value, str):
        raise CandidateValidityWindowError(code)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix:
        raise CandidateValidityWindowError(code)
    return path.as_posix()


def _load_source_validity(document: dict, dynamic_root: Path) -> tuple[dict, dict]:
    source = document.get("source_candidate_validity")
    if not isinstance(source, dict):
        raise CandidateValidityWindowError("SOURCE_VALIDITY_METADATA_INVALID")
    relative = _safe_relative(
        source.get("path"), "candidate_validity_observations", "SOURCE_VALIDITY_PATH_INVALID"
    )
    path = (dynamic_root / relative).resolve()
    _relative_under(path, dynamic_root, "SOURCE_VALIDITY_ESCAPES_ROOT")
    probe = _read_json(path, "SOURCE_VALIDITY_MISSING")
    trigger_kind = probe.get("source_run", {}).get("trigger_kind")
    try:
        validated = load_and_validate_observation(
            path, dynamic_root, trigger_kind=trigger_kind
        )
    except Exception as exc:
        raise CandidateValidityWindowError(
            f"SOURCE_VALIDITY_REVALIDATION_FAILED:{type(exc).__name__}"
        ) from exc
    if validated.get("observation_sha256") != source.get("observation_sha256"):
        raise CandidateValidityWindowError("SOURCE_VALIDITY_HASH_MISMATCH")
    return validated, probe


def _candidate_map(observation: dict) -> dict[str, dict]:
    result = {}
    for market, block in sorted(observation.get("by_market", {}).items()):
        for candidate in block.get("candidates", []):
            key = payload_sha256({"market": market, "subject": candidate.get("subject")})
            if key in result:
                raise CandidateValidityWindowError("SOURCE_STABLE_KEY_DUPLICATE")
            triggers = candidate.get("trigger_types")
            if (
                not isinstance(triggers, list)
                or not triggers
                or any(trigger not in KNOWN_TRIGGERS for trigger in triggers)
            ):
                raise CandidateValidityWindowError("SOURCE_TRIGGER_FAMILY_INVALID")
            result[key] = copy.deepcopy(candidate)
    return result


def _load_chain(tip_path: Path, dynamic_root: Path) -> list[tuple[Path, dict, dict]]:
    tip_path = tip_path.resolve()
    _relative_under(tip_path, dynamic_root, "LIFECYCLE_TIP_ESCAPES_ROOT")
    chain = []
    path = tip_path
    seen = set()
    while True:
        if path in seen:
            raise CandidateValidityWindowError("LIFECYCLE_CHAIN_CYCLE")
        seen.add(path)
        try:
            document = load_and_validate_lifecycle_observation(
                path, dynamic_clock_root=dynamic_root
            )
        except Exception as exc:
            raise CandidateValidityWindowError(
                f"LIFECYCLE_REVALIDATION_FAILED:{type(exc).__name__}"
            ) from exc
        if (
            document.get("contract_version") != LIFECYCLE_CONTRACT_VERSION
            or document.get("sample_qualification") != NATURAL_SAMPLE
            or document.get("chain_qualification") != CHAIN_NATURAL
            or document.get("chain_advancing") is not True
        ):
            raise CandidateValidityWindowError("LIFECYCLE_NOT_NATURAL_CHAIN_TIP")
        source, _ = _load_source_validity(document, dynamic_root)
        chain.append((path, document, source))
        prior = document.get("prior_lifecycle")
        if prior is None:
            break
        relative = _safe_relative(
            prior.get("path") if isinstance(prior, dict) else None,
            "candidate_lifecycle_observations",
            "LIFECYCLE_PRIOR_PATH_INVALID",
        )
        path = (dynamic_root / relative).resolve()
    return chain


def discover_natural_tip(lifecycle_root: Path, dynamic_root: Path) -> Path:
    """Find the sole validated natural tip; forks fail closed."""
    documents = []
    for path in sorted(lifecycle_root.glob("*/lifecycle-*.json")):
        try:
            document = load_and_validate_lifecycle_observation(
                path, dynamic_clock_root=dynamic_root
            )
        except Exception as exc:
            raise CandidateValidityWindowError(
                f"LIFECYCLE_REVALIDATION_FAILED:{path.name}:{type(exc).__name__}"
            ) from exc
        if (
            document.get("sample_qualification") == NATURAL_SAMPLE
            and document.get("chain_qualification") == CHAIN_NATURAL
            and document.get("chain_advancing") is True
        ):
            documents.append((path, document))
    if not documents:
        raise CandidateValidityWindowError("NO_NATURAL_LIFECYCLE_CHAIN")
    parents = {
        document["prior_lifecycle"]["lifecycle_observation_sha256"]
        for _, document in documents if document.get("prior_lifecycle") is not None
    }
    tips = [
        path for path, document in documents
        if document.get("lifecycle_observation_sha256") not in parents
    ]
    if len(tips) != 1:
        raise CandidateValidityWindowError("NATURAL_LIFECYCLE_TIP_AMBIGUOUS")
    return tips[0]


def _retained_report(source_observation: dict, dynamic_root: Path) -> dict:
    retained = source_observation.get("source_dynamic_clock", {}).get("retained_report")
    if not isinstance(retained, dict):
        raise CandidateValidityWindowError("SOURCE_REPORT_METADATA_INVALID")
    relative = _safe_relative(
        retained.get("path"), "candidate_validity_source_reports", "SOURCE_REPORT_PATH_INVALID"
    )
    report_path = (dynamic_root / relative).resolve()
    _relative_under(report_path, dynamic_root, "SOURCE_REPORT_ESCAPES_ROOT")
    return _read_json(report_path, "SOURCE_REPORT_MISSING")


def _validate_identity(identity_path: Path, reports: list[dict]) -> dict:
    identity = _read_json(identity_path, "CANDIDATE_IDENTITY_OBSERVATION_MISSING")
    authority = canonical_identity.load_authority()
    scope = canonical_identity.load_scope_authority()
    failures = []
    for report in reports:
        try:
            candidate_identity.validate_observation(identity, report, authority, scope)
            return identity
        except Exception as exc:
            failures.append(type(exc).__name__)
    raise CandidateValidityWindowError(
        "CANDIDATE_IDENTITY_OBSERVATION_INVALID:" + ",".join(failures)
    )


def _identity_map(identity: dict) -> dict[tuple[str, str], dict]:
    result = {}
    for row in identity.get("observations", []):
        key = (row.get("market"), row.get("subject"))
        if key in result:
            raise CandidateValidityWindowError("CANDIDATE_IDENTITY_DUPLICATE")
        result[key] = row
    return result


def _report_map(report: dict) -> dict[tuple[str, str], dict]:
    result = {}
    for market, block in sorted(report.get("by_market", {}).items()):
        for candidate in block.get("review_queue", []):
            key = (market, candidate.get("subject"))
            if key in result:
                raise CandidateValidityWindowError("SOURCE_REPORT_CANDIDATE_DUPLICATE")
            result[key] = candidate
    return result


def classify_temporal_record(
    record: dict,
    trigger_types: list[str],
    evaluation_at_utc: str,
    *,
    p8_10_link_failed: bool = False,
) -> dict:
    """Classify one validated lifecycle record under Recommendation A."""
    evaluation = _utc(evaluation_at_utc, "EVALUATION_AT_INVALID")
    if not trigger_types or any(trigger not in KNOWN_TRIGGERS for trigger in trigger_types):
        raise CandidateValidityWindowError("TRIGGER_FAMILY_INVALID")
    state = record.get("state")
    if state not in {"ACTIVE", "ABSENT_OBSERVED"}:
        raise CandidateValidityWindowError("LIFECYCLE_STATE_INVALID")

    if state == "ABSENT_OBSERVED":
        eligible_status = MISSING
        t0 = None
        expires_at = None
    else:
        t0 = record.get("last_changed_observed_at_utc")
        exact_forward = record.get("first_seen_status") == FIRST_SEEN_FORWARD
        if not exact_forward or t0 is None:
            eligible_status = NO_T0
            t0 = None
            expires_at = None
        elif p8_10_link_failed:
            _utc(t0, "T0_INVALID")
            eligible_status = P8_10_LINK_FAILED
            expires_at = None
        else:
            start = _utc(t0, "T0_INVALID")
            if evaluation < start:
                raise CandidateValidityWindowError("EVALUATION_PRECEDES_T0")
            end = start + dt.timedelta(seconds=WINDOW_SECONDS)
            expires_at = end.strftime("%Y-%m-%dT%H:%M:%SZ")
            eligible_status = FRESH if evaluation < end else STALE

    by_trigger = []
    for trigger in sorted(trigger_types):
        by_trigger.append({
            "trigger_type": trigger,
            "temporal_status": (
                eligible_status if trigger in ELIGIBLE_TRIGGERS else UNVALIDATED
            ),
        })
    eligible_statuses = [
        row["temporal_status"] for row in by_trigger
        if row["trigger_type"] in ELIGIBLE_TRIGGERS
    ]
    aggregate = eligible_statuses[0] if eligible_statuses else UNVALIDATED
    if any(status != aggregate for status in eligible_statuses):
        raise CandidateValidityWindowError("ELIGIBLE_TRIGGER_STATUS_DIVERGENCE")
    return {
        "temporal_status": aggregate,
        "t0_operational_evaluated_at_utc": t0,
        "expires_at_utc": expires_at,
        "window_seconds": WINDOW_SECONDS,
        "trigger_assessments": by_trigger,
    }


def build_assessment(
    *,
    lifecycle_tip_path: Path,
    evaluation_at_utc: str,
    identity_path: Path = DEFAULT_IDENTITY_PATH,
    dynamic_root: Path = DEFAULT_DYNAMIC_ROOT,
) -> dict:
    """Build an independently revalidated temporal-only assessment."""
    authority = validate_window_authority(evaluation_at_utc)
    try:
        p8_10 = validate_p8_10_authority(evaluation_at_utc)
    except Exception as exc:
        raise CandidateValidityWindowError(
            f"P8_10_AUTHORITY_STRUCTURE_INVALID:{type(exc).__name__}"
        ) from exc
    dynamic_root = dynamic_root.resolve()
    chain = _load_chain(lifecycle_tip_path, dynamic_root)
    tip_path, tip, current_source = chain[0]
    current_candidates = _candidate_map(current_source)

    latest_candidate_by_key = dict(current_candidates)
    for _path, _document, source in chain[1:]:
        for key, candidate in _candidate_map(source).items():
            latest_candidate_by_key.setdefault(key, candidate)

    report = _retained_report(current_source, dynamic_root)
    rolling_report_path = dynamic_root / "dynamic_clock_report.json"
    identity_reports = [report]
    if rolling_report_path.is_file():
        rolling_report = _read_json(rolling_report_path, "ROLLING_REPORT_MISSING")
        if rolling_report != report:
            identity_reports.insert(0, rolling_report)
    identity = _validate_identity(Path(identity_path), identity_reports)
    identities = _identity_map(identity)
    report_candidates = _report_map(report)

    records = []
    for record in tip.get("state_records", []):
        key = record.get("stable_candidate_key")
        source_candidate = latest_candidate_by_key.get(key)
        if source_candidate is None:
            raise CandidateValidityWindowError("TRIGGER_LINEAGE_MISSING")
        market = record.get("market")
        subject = record.get("subject")
        identity_row = identities.get((market, subject))
        report_candidate = report_candidates.get((market, subject))
        price_link = None if report_candidate is None else report_candidate.get(
            "price_reflection_status"
        )
        link_failed = (
            isinstance(price_link, dict)
            and price_link.get("status") == "NOT_LINKED_THIS_SLICE"
            and "link failed closed" in str(price_link.get("reason", ""))
        )
        temporal = classify_temporal_record(
            record,
            source_candidate["trigger_types"],
            evaluation_at_utc,
            p8_10_link_failed=link_failed,
        )
        rows = {
            "stable_candidate_key": key,
            "market": market,
            "subject": subject,
            "lifecycle_state": record.get("state"),
            "lifecycle_event_at_tip": record.get("lifecycle_event"),
            **temporal,
            "canonical_identity_status": (
                "NOT_APPLICABLE_ABSENT"
                if record.get("state") == "ABSENT_OBSERVED"
                else identity_row.get("identity", {}).get("status")
                if identity_row is not None
                else "IDENTITY_NOT_COMPUTABLE_MISSING_CURRENT_OBSERVATION"
            ),
            "account_scope_status": (
                "NOT_APPLICABLE_ABSENT"
                if record.get("state") == "ABSENT_OBSERVED"
                else identity_row.get("account_scope", {}).get("status")
                if identity_row is not None
                else "SCOPE_NOT_COMPUTABLE_MISSING_CURRENT_OBSERVATION"
            ),
            "p8_10_price_reflection_link_status": (
                "NOT_APPLICABLE_ABSENT"
                if record.get("state") == "ABSENT_OBSERVED"
                else price_link.get("status")
                if isinstance(price_link, dict)
                else "NOT_AVAILABLE"
            ),
            "entry_eligibility_status": "NOT_EVALUATED_BY_THIS_CONTRACT",
            "risk_capacity_status": "NOT_EVALUATED_BY_THIS_CONTRACT",
            "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
        }
        records.append(rows)

    counts = Counter(row["temporal_status"] for row in records)
    document = {
        "contract_version": CONTRACT_VERSION,
        "wbs_item": "P8-12 Candidate Validity Window Recommendation A",
        "assessment_scope": "TEMPORAL_FRESHNESS_ONLY",
        "evaluation_at_utc": evaluation_at_utc,
        "authority": {
            "rule_id": authority["record"]["rule_id"],
            "rule_version": authority["record"]["rule_version"],
            "ratified_at": authority["record"]["ratified_at"],
            "effective_from": authority["record"]["effective_from"],
            "content_sha256": authority["record"]["content_sha256"],
            "authority_evidence_sha256": authority["record"]["authority_evidence_sha256"],
        },
        "source_lifecycle": {
            "path": _relative_under(tip_path, dynamic_root, "LIFECYCLE_TIP_ESCAPES_ROOT"),
            "lifecycle_observation_sha256": tip["lifecycle_observation_sha256"],
            "operational_evaluated_at_utc": tip["operational_evaluated_at_utc"],
            "natural_chain_artifact_count": len(chain),
        },
        "source_identity": {
            "path": _relative_under(Path(identity_path), dynamic_root, "IDENTITY_PATH_ESCAPES_ROOT"),
            "packet_sha256": identity["packet_sha256"],
            "identity_resolved_count": identity["summary"]["identity_resolved_count"],
            "candidate_count": identity["summary"]["candidate_count"],
        },
        "p8_10_authority_structure": {
            "rule_id": p8_10["record"]["rule_id"],
            "rule_version": p8_10["record"]["rule_version"],
            "effective_from": p8_10["record"]["effective_from"],
            "reflection_classification_authorized_by_this_contract": False,
        },
        "window_seconds": WINDOW_SECONDS,
        "interval": "[t0,t0+172800)",
        "candidate_count": len(records),
        "temporal_status_counts": dict(sorted(counts.items())),
        "candidate_assessments": sorted(
            records, key=lambda row: (row["market"], row["subject"], row["stable_candidate_key"])
        ),
        "downstream_locks": {
            "canonical_security_identity_authority": False,
            "risk_capacity": False,
            "p5_06": False,
            "p7_08": False,
            "p8_13": False,
            "stage": False,
            "buy": False,
            "action": False,
            "order": False,
            "broker": False,
            "real_capital": False,
            "live": False,
            "production": False,
            "trading": False,
        },
        "operational_authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    document["assessment_sha256"] = payload_sha256(document)
    return document


def write_assessment(document: dict, output: Path = DEFAULT_OUTPUT) -> Path:
    payload = (canonical_json(document) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() == payload:
        return output
    output.write_bytes(payload)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-at-utc", required=True)
    parser.add_argument("--dynamic-root", type=Path, default=DEFAULT_DYNAMIC_ROOT)
    parser.add_argument("--lifecycle-tip", type=Path)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    tip = args.lifecycle_tip or discover_natural_tip(
        args.dynamic_root / "candidate_lifecycle_observations", args.dynamic_root
    )
    document = build_assessment(
        lifecycle_tip_path=tip,
        evaluation_at_utc=args.evaluation_at_utc,
        identity_path=args.identity,
        dynamic_root=args.dynamic_root,
    )
    path = write_assessment(document, args.output)
    print(json.dumps({
        "path": path.as_posix(),
        "candidate_count": document["candidate_count"],
        "temporal_status_counts": document["temporal_status_counts"],
        "authority": document["operational_authority"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
