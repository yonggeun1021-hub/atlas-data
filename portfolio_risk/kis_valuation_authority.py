#!/usr/bin/env python3
"""CIO-ratified KIS PAPER valuation data-policy authority.

This registry is deliberately narrower than an account-fact or risk-input
authority.  It can authorize (a) the exact official KIS field-to-Atlas
semantic mappings and (b) a conservative source-age/pair-gap policy for the
exact KIS PAPER provider tuple.  It cannot authorize account-fact production,
Portfolio Risk Input, sizing, Stage, Buy, Action, Order, Production, Trading,
or REAL use.

The freshness values are a normative CIO governance choice, not an empirical
claim.  The approval record preserves that live-pair validation was absent at
ratification.  A caller cannot erase that limitation by rehashing a packet.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess

from portfolio_risk.kis_valuation_freshness_policy_proposal import (
    freshness_policy_proposal,
)
from portfolio_risk.kis_valuation_semantic_proposal import (
    valuation_semantic_mapping_proposal,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "kis_valuation_authority.json"
SCHEMA_VERSION = "kis_valuation_authority/1"
POLICY_VERSION = "kis_valuation_authority/v1"
RATIFIED = "RATIFIED"
RESOLVED = "RESOLVED"
NOT_COMPUTABLE_NO_AUTHORITY_RECORD = "NOT_COMPUTABLE_NO_AUTHORITY_RECORD"
NOT_COMPUTABLE_AUTHORITY_UNRATIFIED = "NOT_COMPUTABLE_AUTHORITY_UNRATIFIED"
NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE = (
    "NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE"
)
NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED = (
    "NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED"
)
NOT_COMPUTABLE_DOCUMENT_TAMPERED = "NOT_COMPUTABLE_DOCUMENT_TAMPERED"
SEMANTIC_KIND = "VALUATION_SEMANTIC_MAPPING"
FRESHNESS_KIND = "VALUATION_FRESHNESS_POLICY"
SEMANTIC_RULE_ID = "atlas.portfolio-risk.kis-paper-valuation-semantics"
FRESHNESS_RULE_ID = "atlas.portfolio-risk.kis-paper-valuation-freshness"
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")

PROVIDER_TUPLE = {
    "provider": "KIS_PAPER_ACCOUNT",
    "accountScope": "KOREA",
    "currency": "KRW",
    "positionSourceName": "kis_paper_domestic_balance",
}

SEMANTIC_AUTHORITY = {
    "valuationSemanticAuthorized": True,
    "freshnessPolicyAuthorized": False,
    "accountFactAuthorized": False,
    "riskInputAuthorized": False,
    "stageAuthorized": False,
    "buyAuthorized": False,
    "actionAuthorized": False,
    "orderAuthorized": False,
    "productionAuthorized": False,
    "tradingAuthorized": False,
    "realCapitalAuthorized": False,
}
FRESHNESS_AUTHORITY = {
    **SEMANTIC_AUTHORITY,
    "valuationSemanticAuthorized": False,
    "freshnessPolicyAuthorized": True,
}
COMBINED_NON_CONSUMER_AUTHORITY = {
    **SEMANTIC_AUTHORITY,
    "freshnessPolicyAuthorized": True,
}

_DOCUMENT_FIELDS = {
    "schemaVersion", "policyVersion", "evidenceBasis",
    "valuationSemanticAuthorityRecords", "freshnessPolicyAuthorityRecords",
}
_COMMON_ROW_FIELDS = {
    "ruleId", "ruleVersion", "authorityKind", "approvalStatus",
    "ratifiedAt", "firstSeenAt", "effectiveFrom", "effectiveTo",
    "approvalEvidenceRef", "approvalEvidenceSha256",
    "businessPayloadSha256", "providerTuple", "targetContractVersion",
    "proposalSha256", "authority",
}
_SEMANTIC_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "approvedMappings", "approvalBasis", "empiricalValidationStatus",
}
_FRESHNESS_ROW_FIELDS = _COMMON_ROW_FIELDS | {
    "clockField", "maxSourceAgeSeconds", "maxPairGapSeconds", "comparison",
    "bothSourcesRequired", "callerOverridePermitted", "approvalBasis",
    "empiricalValidationStatus", "livePairSampleCountAtRatification",
    "atomicCaptureSessionBindingPresentAtRatification",
    "retroactiveApplicationPermitted", "permittedUse",
}
_APPROVAL_FIELDS = {
    "schemaVersion", "approvalStatus", "ratifiedAt", "authorityKind",
    "ruleId", "ruleVersion", "approvedBusinessPayloadSha256",
    "sourceEvidence", "assertion", "decision", "boundary",
}


class KisValuationAuthorityError(ValueError):
    pass


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise KisValuationAuthorityError(code)
    try:
        parsed = dt.datetime.strptime(value, _TIMESTAMP_FORMAT).replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        raise KisValuationAuthorityError(code) from None
    if parsed.strftime(_TIMESTAMP_FORMAT) != value:
        raise KisValuationAuthorityError(code)
    return parsed


def _business_payload(row: dict) -> dict:
    if row.get("authorityKind") == SEMANTIC_KIND:
        fields = (
            "authorityKind", "providerTuple", "targetContractVersion",
            "proposalSha256", "approvedMappings", "approvalBasis",
            "empiricalValidationStatus", "authority",
        )
    elif row.get("authorityKind") == FRESHNESS_KIND:
        fields = (
            "authorityKind", "providerTuple", "targetContractVersion",
            "proposalSha256", "clockField", "maxSourceAgeSeconds",
            "maxPairGapSeconds", "comparison", "bothSourcesRequired",
            "callerOverridePermitted", "approvalBasis",
            "empiricalValidationStatus", "livePairSampleCountAtRatification",
            "atomicCaptureSessionBindingPresentAtRatification",
            "retroactiveApplicationPermitted", "permittedUse", "authority",
        )
    else:
        raise KisValuationAuthorityError("AUTHORITY_KIND_INVALID")
    return {field: row.get(field) for field in fields}


def _validate_common_row(row: object, expected_kind: str) -> dict:
    expected_fields = (
        _SEMANTIC_ROW_FIELDS if expected_kind == SEMANTIC_KIND
        else _FRESHNESS_ROW_FIELDS
    )
    if not isinstance(row, dict) or set(row) != expected_fields:
        raise KisValuationAuthorityError("AUTHORITY_ROW_FIELDS_INVALID")
    if row.get("authorityKind") != expected_kind:
        raise KisValuationAuthorityError("AUTHORITY_ROW_KIND_INVALID")
    if row.get("approvalStatus") != RATIFIED:
        raise KisValuationAuthorityError("AUTHORITY_ROW_NOT_RATIFIED")
    expected_rule_id = (
        SEMANTIC_RULE_ID if expected_kind == SEMANTIC_KIND else FRESHNESS_RULE_ID
    )
    if (
        type(row.get("ruleVersion")) is not int
        or row.get("ruleVersion") != 1
        or row.get("ruleId") != expected_rule_id
    ):
        raise KisValuationAuthorityError("AUTHORITY_RULE_ID_VERSION_INVALID")
    ratified = _parse_utc(row.get("ratifiedAt"), "AUTHORITY_RATIFIED_AT_INVALID")
    first_seen = _parse_utc(
        row.get("firstSeenAt"), "AUTHORITY_FIRST_SEEN_AT_INVALID"
    )
    effective = _parse_utc(
        row.get("effectiveFrom"), "AUTHORITY_EFFECTIVE_FROM_INVALID"
    )
    if row.get("effectiveTo") is not None:
        ending = _parse_utc(
            row.get("effectiveTo"), "AUTHORITY_EFFECTIVE_TO_INVALID"
        )
        if ending <= effective:
            raise KisValuationAuthorityError("AUTHORITY_EFFECTIVE_WINDOW_INVALID")
    if first_seen != ratified or effective != ratified:
        raise KisValuationAuthorityError("AUTHORITY_INITIAL_TIMESTAMPS_DIVERGE")
    if row.get("providerTuple") != PROVIDER_TUPLE:
        raise KisValuationAuthorityError("AUTHORITY_PROVIDER_TUPLE_INVALID")
    if row.get("targetContractVersion") != "portfolio_account_fact/3":
        raise KisValuationAuthorityError("AUTHORITY_TARGET_CONTRACT_INVALID")
    for field in (
        "approvalEvidenceSha256", "businessPayloadSha256", "proposalSha256"
    ):
        if _SHA256_RE.fullmatch(str(row.get(field, ""))) is None:
            raise KisValuationAuthorityError(f"AUTHORITY_HASH_INVALID:{field}")
    if not isinstance(row.get("approvalEvidenceRef"), str):
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_REF_INVALID")
    authority = row.get("authority")
    expected_authority = (
        SEMANTIC_AUTHORITY if expected_kind == SEMANTIC_KIND
        else FRESHNESS_AUTHORITY
    )
    if (
        authority != expected_authority
        or not isinstance(authority, dict)
        or any(type(value) is not bool for value in authority.values())
    ):
        raise KisValuationAuthorityError("AUTHORITY_BOUNDARY_INVALID")
    if row["businessPayloadSha256"] != payload_sha256(_business_payload(row)):
        raise KisValuationAuthorityError("AUTHORITY_BUSINESS_HASH_MISMATCH")
    return dict(row)


def _validate_semantic_row(row: object) -> dict:
    value = _validate_common_row(row, SEMANTIC_KIND)
    proposal = valuation_semantic_mapping_proposal()
    if value["proposalSha256"] != proposal["proposalSha256"]:
        raise KisValuationAuthorityError("SEMANTIC_PROPOSAL_HASH_MISMATCH")
    if value.get("approvedMappings") != proposal["mappings"]:
        raise KisValuationAuthorityError("SEMANTIC_MAPPING_SET_MISMATCH")
    if value.get("approvalBasis") != (
        "CIO_RATIFIED_EXACT_OFFICIAL_FIELD_MEANINGS_AND_TARGET_PATHS"
    ):
        raise KisValuationAuthorityError("SEMANTIC_APPROVAL_BASIS_INVALID")
    if value.get("empiricalValidationStatus") != (
        "OFFICIAL_MEANINGS_AND_PRIVATE_RELATIONSHIPS_REPRODUCED_"
        "LIVE_FRESHNESS_NOT_PART_OF_THIS_AUTHORITY"
    ):
        raise KisValuationAuthorityError("SEMANTIC_EMPIRICAL_STATUS_INVALID")
    return value


def _validate_freshness_row(row: object) -> dict:
    value = _validate_common_row(row, FRESHNESS_KIND)
    proposal = freshness_policy_proposal()
    policy = proposal["candidatePolicy"]
    if value["proposalSha256"] != proposal["proposalSha256"]:
        raise KisValuationAuthorityError("FRESHNESS_PROPOSAL_HASH_MISMATCH")
    expected = {
        "clockField": policy["clockField"],
        "maxSourceAgeSeconds": policy["maxSourceAgeSeconds"],
        "maxPairGapSeconds": policy["maxPairGapSeconds"],
        "comparison": policy["comparison"],
        "bothSourcesRequired": True,
        "callerOverridePermitted": False,
        "approvalBasis": (
            "CIO_NORMATIVE_CONSERVATIVE_GOVERNANCE_CHOICE_"
            "NOT_EMPIRICALLY_DERIVED"
        ),
        "empiricalValidationStatus": (
            "NOT_ESTABLISHED_AT_RATIFICATION_MONITOR_SHADOW_ONLY"
        ),
        "livePairSampleCountAtRatification": 0,
        "atomicCaptureSessionBindingPresentAtRatification": False,
        "retroactiveApplicationPermitted": False,
        "permittedUse": (
            "KIS_PAPER_PORTFOLIO_ACCOUNT_FACT_V3_DATA_FRESHNESS_ONLY_"
            "NO_RISK_OR_TRADING_AUTHORITY"
        ),
    }
    for field, expected_value in expected.items():
        actual = value.get(field)
        if type(expected_value) is bool:
            exact = type(actual) is bool and actual is expected_value
        elif type(expected_value) is int:
            exact = type(actual) is int and actual == expected_value
        else:
            exact = actual == expected_value
        if not exact:
            raise KisValuationAuthorityError(
                f"FRESHNESS_POLICY_FIELD_INVALID:{field}"
            )
    if value["maxPairGapSeconds"] > value["maxSourceAgeSeconds"]:
        raise KisValuationAuthorityError("FRESHNESS_PAIR_GAP_EXCEEDS_SOURCE_AGE")
    return value


def validate_authority_document(authority: object) -> dict:
    if not isinstance(authority, dict):
        raise KisValuationAuthorityError("AUTHORITY_DOCUMENT_NOT_OBJECT")
    clean = {key: value for key, value in authority.items() if not key.startswith("_")}
    if set(clean) != _DOCUMENT_FIELDS:
        raise KisValuationAuthorityError("AUTHORITY_DOCUMENT_FIELDS_INVALID")
    if clean.get("schemaVersion") != SCHEMA_VERSION:
        raise KisValuationAuthorityError("AUTHORITY_SCHEMA_INVALID")
    if clean.get("policyVersion") != POLICY_VERSION:
        raise KisValuationAuthorityError("AUTHORITY_POLICY_VERSION_INVALID")
    if not isinstance(clean.get("evidenceBasis"), str):
        raise KisValuationAuthorityError("AUTHORITY_EVIDENCE_BASIS_INVALID")
    semantic = clean.get("valuationSemanticAuthorityRecords")
    freshness = clean.get("freshnessPolicyAuthorityRecords")
    if not isinstance(semantic, list) or not isinstance(freshness, list):
        raise KisValuationAuthorityError("AUTHORITY_RECORD_LIST_INVALID")
    if len(semantic) != 1 or len(freshness) != 1:
        raise KisValuationAuthorityError("AUTHORITY_EXACT_SINGLETON_RECORDS_REQUIRED")
    _validate_semantic_row(semantic[0])
    _validate_freshness_row(freshness[0])
    return dict(authority)


def load_authority(path: Path = CONFIG_PATH) -> dict:
    path = Path(path).resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise KisValuationAuthorityError("AUTHORITY_DOCUMENT_READ_FAILED") from error
    document = validate_authority_document(document)
    document["_sourcePath"] = str(path)
    return document


def _git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise KisValuationAuthorityError("AUTHORITY_GIT_RESOLUTION_FAILED") from error


def _repo_and_relative(path: Path) -> tuple[Path, str]:
    try:
        repo = Path(_git(path.parent, "rev-parse", "--show-toplevel").decode().strip())
        relative = path.relative_to(repo).as_posix()
    except (ValueError, UnicodeDecodeError):
        raise KisValuationAuthorityError("AUTHORITY_SOURCE_NOT_IN_GIT_REPO") from None
    return repo, relative


def _trusted_commit(repo: Path, trusted_commit: str | None) -> str:
    if trusted_commit is None:
        return _git(repo, "rev-parse", "HEAD").decode().strip()
    if _FULL_COMMIT_RE.fullmatch(str(trusted_commit)) is None:
        raise KisValuationAuthorityError("AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE")
    resolved = _git(repo, "rev-parse", "--verify", f"{trusted_commit}^{{commit}}").decode().strip()
    if resolved != trusted_commit:
        raise KisValuationAuthorityError("AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE")
    return trusted_commit


def _document_provenance(
    authority: dict, trusted_commit: str | None
) -> tuple[Path, str, str]:
    source = authority.get("_sourcePath")
    if not isinstance(source, str):
        raise KisValuationAuthorityError("AUTHORITY_SOURCE_PATH_REQUIRED")
    path = Path(source)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise KisValuationAuthorityError("AUTHORITY_SOURCE_PATH_INVALID")
    disk = path.read_bytes()
    try:
        disk_doc = json.loads(disk.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise KisValuationAuthorityError("AUTHORITY_DISK_BYTES_INVALID") from None
    memory = {key: value for key, value in authority.items() if not key.startswith("_")}
    if memory != disk_doc:
        raise KisValuationAuthorityError("AUTHORITY_MEMORY_DISK_MISMATCH")
    repo, relative = _repo_and_relative(path)
    commit = _trusted_commit(repo, trusted_commit)
    if trusted_commit is None:
        dirty = _git(repo, "status", "--porcelain", "--", relative).decode().strip()
        if dirty:
            raise KisValuationAuthorityError("AUTHORITY_SOURCE_WORKTREE_DIRTY")
    if _git(repo, "show", f"{commit}:{relative}") != disk:
        raise KisValuationAuthorityError("AUTHORITY_DISK_COMMIT_MISMATCH")
    return repo, relative, commit


def _commits_for_path(repo: Path, commit: str, relative: str) -> list[str]:
    output = _git(
        repo, "log", "--reverse", "--format=%H", commit, "--", relative
    ).decode().splitlines()
    return [value for value in output if _FULL_COMMIT_RE.fullmatch(value)]


def _row_first_seen(repo: Path, commit: str, relative: str, row: dict) -> dt.datetime:
    for candidate in _commits_for_path(repo, commit, relative):
        try:
            value = json.loads(_git(repo, "show", f"{candidate}:{relative}").decode())
        except (json.JSONDecodeError, UnicodeDecodeError, KisValuationAuthorityError):
            continue
        records = (
            value.get("valuationSemanticAuthorityRecords", [])
            + value.get("freshnessPolicyAuthorityRecords", [])
        ) if isinstance(value, dict) else []
        if any(candidate_row == row for candidate_row in records):
            timestamp = _git(repo, "show", "-s", "--format=%cI", candidate).decode().strip()
            parsed = dt.datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            ).astimezone(dt.timezone.utc)
            return parsed.replace(microsecond=0)
    raise KisValuationAuthorityError("AUTHORITY_ROW_FIRST_SEEN_NOT_VERIFIED")


def _approval_first_seen(
    repo: Path, commit: str, relative: str, expected_bytes: bytes
) -> dt.datetime:
    for candidate in _commits_for_path(repo, commit, relative):
        try:
            value = _git(repo, "show", f"{candidate}:{relative}")
        except KisValuationAuthorityError:
            continue
        if value == expected_bytes:
            timestamp = _git(repo, "show", "-s", "--format=%cI", candidate).decode().strip()
            return dt.datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            ).astimezone(
                dt.timezone.utc
            ).replace(microsecond=0)
    raise KisValuationAuthorityError("AUTHORITY_APPROVAL_FIRST_SEEN_NOT_VERIFIED")


def _verify_approval(
    repo: Path, commit: str, row: dict
) -> tuple[dt.datetime, dict]:
    approval_path = (repo / row["approvalEvidenceRef"]).resolve()
    try:
        relative = approval_path.relative_to(repo).as_posix()
    except ValueError:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_PATH_INVALID") from None
    if approval_path.is_symlink() or not approval_path.is_file():
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_FILE_INVALID")
    disk = approval_path.read_bytes()
    if hashlib.sha256(disk).hexdigest() != row["approvalEvidenceSha256"]:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_HASH_MISMATCH")
    if _git(repo, "show", f"{commit}:{relative}") != disk:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_COMMIT_MISMATCH")
    try:
        approval = json.loads(disk.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_JSON_INVALID") from None
    if not isinstance(approval, dict) or set(approval) != _APPROVAL_FIELDS:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_FIELDS_INVALID")
    _validate_approval_scalar_types(approval, row)
    if (
        approval.get("schemaVersion") != "kis_valuation_authority_approval/1"
        or approval.get("approvalStatus") != RATIFIED
        or approval.get("ratifiedAt") != row["ratifiedAt"]
        or approval.get("authorityKind") != row["authorityKind"]
        or approval.get("ruleId") != row["ruleId"]
        or approval.get("ruleVersion") != row["ruleVersion"]
        or approval.get("approvedBusinessPayloadSha256")
        != row["businessPayloadSha256"]
    ):
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_BINDING_MISMATCH")
    sources = approval.get("sourceEvidence")
    if not isinstance(sources, list) or not sources:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_SOURCES_INVALID")
    expected_source_paths = (
        {
            "portfolio_risk/kis_valuation_semantic_proposal.py",
            "portfolio_risk/kis_valuation_semantic_review.py",
        }
        if row["authorityKind"] == SEMANTIC_KIND
        else {
            "portfolio_risk/kis_valuation_freshness_policy_proposal.py",
            "portfolio_risk/kis_valuation_freshness_policy_review.py",
        }
    )
    if {
        source.get("path") for source in sources if isinstance(source, dict)
    } != expected_source_paths or len(sources) != len(expected_source_paths):
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_SOURCE_SET_INVALID")
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
            raise KisValuationAuthorityError("AUTHORITY_APPROVAL_SOURCE_FIELDS_INVALID")
        source_path = (repo / str(source["path"])).resolve()
        try:
            source_relative = source_path.relative_to(repo).as_posix()
        except ValueError:
            raise KisValuationAuthorityError("AUTHORITY_APPROVAL_SOURCE_PATH_INVALID") from None
        source_bytes = _git(repo, "show", f"{commit}:{source_relative}")
        if (
            _SHA256_RE.fullmatch(str(source.get("sha256", ""))) is None
            or hashlib.sha256(source_bytes).hexdigest() != source["sha256"]
        ):
            raise KisValuationAuthorityError("AUTHORITY_APPROVAL_SOURCE_HASH_MISMATCH")
    assertion = approval.get("assertion")
    decision = approval.get("decision")
    if row["authorityKind"] == SEMANTIC_KIND:
        expected_assertion = {
            "providerTuple": PROVIDER_TUPLE,
            "targetContractVersion": "portfolio_account_fact/3",
            "proposalSha256": row["proposalSha256"],
            "approvedScope": (
                "EXACT_OFFICIAL_KIS_FIELD_MEANINGS_AND_LISTED_TARGET_PATHS_ONLY"
            ),
            "freshnessIncluded": False,
            "accountFactProductionIncluded": False,
            "portfolioRiskIncluded": False,
        }
        expected_decision = {
            "decisionStatus": "CIO_RATIFIED_SEMANTIC_SUBSET",
            "basis": (
                "Official KIS pinned bytes and private source relationships "
                "independently reproduced the exact field meanings and exclusions. "
                "Freshness remains a separate policy authority."
            ),
            "liveFreshnessEvidenceRequiredForThisSemanticDecision": False,
        }
        expected_boundary = (
            "SEMANTIC_MAPPING_ONLY_NO_ACCOUNT_FACT_RISK_STAGE_BUY_ACTION_ORDER_"
            "PRODUCTION_TRADING_OR_REAL_AUTHORITY"
        )
    else:
        expected_assertion = {
            "providerTuple": PROVIDER_TUPLE,
            "targetContractVersion": "portfolio_account_fact/3",
            "proposalSha256": row["proposalSha256"],
            "maxSourceAgeSeconds": row["maxSourceAgeSeconds"],
            "maxPairGapSeconds": row["maxPairGapSeconds"],
            "comparison": row["comparison"],
            "livePairSampleCountAtRatification": 0,
            "atomicCaptureSessionBindingPresentAtRatification": False,
            "empiricalValidationStatus": (
                "NOT_ESTABLISHED_AT_RATIFICATION_MONITOR_SHADOW_ONLY"
            ),
        }
        expected_decision = {
            "decisionStatus": "CIO_NORMATIVE_CONSERVATIVE_POLICY_RATIFICATION",
            "basis": (
                "The limits are an explicit conservative governance choice for "
                "read-only KIS PAPER accountFact/3 freshness, not a claim derived "
                "from valuation pair observations."
            ),
            "proposalReviewBlockerAcknowledged": (
                "VALUATION_PAIR_GAP_EVIDENCE_UNVALIDATED_NO_LIVE_PAIR_SAMPLE"
            ),
            "empiricalEvidenceClaimed": False,
            "retroactiveUsePermitted": False,
        }
        expected_boundary = (
            "DATA_FRESHNESS_ONLY_NO_ACCOUNT_FACT_RISK_STAGE_BUY_ACTION_ORDER_"
            "PRODUCTION_TRADING_OR_REAL_AUTHORITY"
        )
    if assertion != expected_assertion:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_ASSERTION_INVALID")
    if decision != expected_decision:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_DECISION_INVALID")
    if approval.get("boundary") != expected_boundary:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_BOUNDARY_INVALID")
    return _approval_first_seen(repo, commit, relative, disk), approval


def _validate_approval_scalar_types(approval: dict, row: dict) -> None:
    """Close Python's bool/int equality alias at the approval boundary."""
    if type(approval.get("ruleVersion")) is not int:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_RULE_VERSION_TYPE_INVALID")
    assertion = approval.get("assertion")
    decision = approval.get("decision")
    if not isinstance(assertion, dict) or not isinstance(decision, dict):
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_DECISION_SHAPE_INVALID")
    if row.get("authorityKind") == SEMANTIC_KIND:
        for field in (
            "freshnessIncluded", "accountFactProductionIncluded",
            "portfolioRiskIncluded",
        ):
            if type(assertion.get(field)) is not bool:
                raise KisValuationAuthorityError(
                    f"AUTHORITY_APPROVAL_BOOLEAN_TYPE_INVALID:{field}"
                )
        if type(decision.get("liveFreshnessEvidenceRequiredForThisSemanticDecision")) is not bool:
            raise KisValuationAuthorityError(
                "AUTHORITY_APPROVAL_BOOLEAN_TYPE_INVALID:"
                "liveFreshnessEvidenceRequiredForThisSemanticDecision"
            )
    elif row.get("authorityKind") == FRESHNESS_KIND:
        for field in (
            "maxSourceAgeSeconds", "maxPairGapSeconds",
            "livePairSampleCountAtRatification",
        ):
            if type(assertion.get(field)) is not int:
                raise KisValuationAuthorityError(
                    f"AUTHORITY_APPROVAL_INTEGER_TYPE_INVALID:{field}"
                )
        if type(assertion.get("atomicCaptureSessionBindingPresentAtRatification")) is not bool:
            raise KisValuationAuthorityError(
                "AUTHORITY_APPROVAL_BOOLEAN_TYPE_INVALID:"
                "atomicCaptureSessionBindingPresentAtRatification"
            )
        for field in ("empiricalEvidenceClaimed", "retroactiveUsePermitted"):
            if type(decision.get(field)) is not bool:
                raise KisValuationAuthorityError(
                    f"AUTHORITY_APPROVAL_BOOLEAN_TYPE_INVALID:{field}"
                )
    else:
        raise KisValuationAuthorityError("AUTHORITY_APPROVAL_KIND_INVALID")


def _resolve_row(
    row: dict, *, decision_at: str, authority: dict,
    trusted_commit: str | None,
) -> dict:
    decision = _parse_utc(decision_at, "AUTHORITY_DECISION_AT_INVALID")
    repo, relative, commit = _document_provenance(authority, trusted_commit)
    first_row = _row_first_seen(repo, commit, relative, row)
    first_approval, approval = _verify_approval(repo, commit, row)
    claimed = _parse_utc(row["firstSeenAt"], "AUTHORITY_FIRST_SEEN_AT_INVALID")
    if first_row != claimed or first_approval != claimed:
        raise KisValuationAuthorityError("AUTHORITY_FIRST_SEEN_CLAIM_MISMATCH")
    usable = max(
        _parse_utc(row["ratifiedAt"], "AUTHORITY_RATIFIED_AT_INVALID"),
        _parse_utc(row["effectiveFrom"], "AUTHORITY_EFFECTIVE_FROM_INVALID"),
        first_row,
        first_approval,
    )
    ending = row.get("effectiveTo")
    if decision < usable or (
        ending is not None
        and decision >= _parse_utc(ending, "AUTHORITY_EFFECTIVE_TO_INVALID")
    ):
        return {
            "status": NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE,
            "realUsableFrom": usable.strftime(_TIMESTAMP_FORMAT),
            "authority": {key: False for key in COMBINED_NON_CONSUMER_AUTHORITY},
        }
    return {
        "status": RESOLVED,
        "authorityKind": row["authorityKind"],
        "ruleId": row["ruleId"],
        "ruleVersion": row["ruleVersion"],
        "realUsableFrom": usable.strftime(_TIMESTAMP_FORMAT),
        "businessPayloadSha256": row["businessPayloadSha256"],
        "approvalEvidenceSha256": row["approvalEvidenceSha256"],
        "approvalDecision": approval["decision"],
        "authority": dict(row["authority"]),
    }


def resolve_semantic_authority(
    *, decision_at: str, authority: dict,
    provider_tuple: dict = PROVIDER_TUPLE,
    target_contract_version: str = "portfolio_account_fact/3",
    trusted_commit: str | None = None,
) -> dict:
    validate_authority_document(authority)
    if provider_tuple != PROVIDER_TUPLE or target_contract_version != "portfolio_account_fact/3":
        return {
            "status": NOT_COMPUTABLE_NO_AUTHORITY_RECORD,
            "authority": {key: False for key in SEMANTIC_AUTHORITY},
        }
    return _resolve_row(
        authority["valuationSemanticAuthorityRecords"][0],
        decision_at=decision_at, authority=authority,
        trusted_commit=trusted_commit,
    )


def resolve_freshness_authority(
    *, decision_at: str, authority: dict,
    provider_tuple: dict = PROVIDER_TUPLE,
    target_contract_version: str = "portfolio_account_fact/3",
    trusted_commit: str | None = None,
) -> dict:
    validate_authority_document(authority)
    if provider_tuple != PROVIDER_TUPLE or target_contract_version != "portfolio_account_fact/3":
        return {
            "status": NOT_COMPUTABLE_NO_AUTHORITY_RECORD,
            "authority": {key: False for key in FRESHNESS_AUTHORITY},
        }
    result = _resolve_row(
        authority["freshnessPolicyAuthorityRecords"][0],
        decision_at=decision_at, authority=authority,
        trusted_commit=trusted_commit,
    )
    if result["status"] == RESOLVED:
        row = authority["freshnessPolicyAuthorityRecords"][0]
        result["policy"] = {
            "clockField": row["clockField"],
            "maxSourceAgeSeconds": row["maxSourceAgeSeconds"],
            "maxPairGapSeconds": row["maxPairGapSeconds"],
            "comparison": row["comparison"],
            "bothSourcesRequired": row["bothSourcesRequired"],
            "callerOverridePermitted": row["callerOverridePermitted"],
            "approvalBasis": row["approvalBasis"],
            "empiricalValidationStatus": row["empiricalValidationStatus"],
            "permittedUse": row["permittedUse"],
        }
    return result
