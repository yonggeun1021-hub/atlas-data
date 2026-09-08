#!/usr/bin/env python3
"""Account-fact consumption authority for ``portfolio_account_fact/3``.

This registry is the one missing gate between the existing readiness
evaluator -- which stops at ``NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED``
by construction -- and a produced read-only account fact.  It is a
SEPARATE document from the ratified valuation authority so that document
stays byte-stable, and it ships EMPTY: zero records means every resolve is
blocked and nothing downstream changes.

What a record can authorize is deliberately one bit wide:
``accountFactAuthorized``.  Portfolio Risk Input, sizing, Stage, Buy,
Action, Order, Production, Trading and REAL capital stay false, and a
record whose authority block claims otherwise is refused outright rather
than silently downgraded.

Verification reuses the discipline already implemented for the valuation
authority -- three-way memory/disk/git-blob provenance against one
externally resolved immutable commit, recomputed business payload hash,
git-verified row and approval first-seen, and a four-way point-in-time
``usableFrom`` -- so no new trust mechanism is invented here.  The caller
identifier is an exact-match routing scope read from the ratified record;
it is never authentication, and a caller can never authorize itself.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess

from portfolio_risk import portfolio_account_fact_v3 as fact_v3


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "portfolio_account_fact_consumption_authority.json"
SCHEMA_VERSION = "portfolio_account_fact_consumption_authority/1"
POLICY_VERSION = "portfolio_account_fact_consumption_authority/v1"
APPROVAL_SCHEMA_VERSION = "portfolio_account_fact_consumption_authority_approval/1"
AUTHORITY_KIND = "ACCOUNT_FACT_CONSUMPTION"
RULE_ID = "atlas.portfolio-risk.kis-paper-account-fact-v3-consumption"

RATIFIED = "RATIFIED"
RESOLVED = "RESOLVED"
NOT_COMPUTABLE_NO_AUTHORITY_RECORD = "NOT_COMPUTABLE_NO_AUTHORITY_RECORD"
NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE = (
    "NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE"
)
ACCOUNT_FACT_CONSUMER_NOT_PERMITTED = "ACCOUNT_FACT_CONSUMER_NOT_PERMITTED"
ACCOUNT_FACT_AUTHORITY_CAPACITY_BINDING_MISMATCH = (
    "ACCOUNT_FACT_AUTHORITY_CAPACITY_BINDING_MISMATCH"
)

PERMITTED_USE = (
    "READ_ONLY_KIS_PAPER_PORTFOLIO_ACCOUNT_FACT_V3_PRODUCTION_AND_VALIDATION_ONLY"
)
APPROVAL_DECISION_STATUS = "CIO_RATIFIED_READ_ONLY_ACCOUNT_FACT_CONSUMPTION"
APPROVAL_BOUNDARY = (
    "READ_ONLY_ACCOUNT_FACT_PRODUCTION_AND_VALIDATION_ONLY_NO_RISK_SIZING_"
    "STAGE_BUY_ACTION_ORDER_PRODUCTION_TRADING_OR_REAL_AUTHORITY"
)

PROVIDER_TUPLE = dict(fact_v3.PROVIDER_TUPLE)
TARGET_CONTRACT_VERSION = fact_v3.TARGET_CONTRACT_VERSION
SOURCE_BUNDLE_CONTRACT_VERSION = fact_v3.SOURCE_BUNDLE_VERSION

# Exactly the all-false-except-one shape already used by
# kis_valuation_authority.SEMANTIC_AUTHORITY / FRESHNESS_AUTHORITY.
ACCOUNT_FACT_CONSUMPTION_AUTHORITY = {
    "accountFactAuthorized": True,
    "valuationSemanticAuthorized": False,
    "freshnessPolicyAuthorized": False,
    "riskInputAuthorized": False,
    "stageAuthorized": False,
    "buyAuthorized": False,
    "actionAuthorized": False,
    "orderAuthorized": False,
    "productionAuthorized": False,
    "tradingAuthorized": False,
    "realCapitalAuthorized": False,
}
ACCOUNT_FACT_CONSUMPTION_AUTHORITY_ALL_FALSE = {
    key: False for key in ACCOUNT_FACT_CONSUMPTION_AUTHORITY
}

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")

_DOCUMENT_FIELDS = {
    "schemaVersion", "policyVersion", "evidenceBasis",
    "accountFactConsumptionAuthorityRecords",
}
_ROW_FIELDS = {
    "ruleId", "ruleVersion", "authorityKind", "approvalStatus",
    "ratifiedAt", "firstSeenAt", "effectiveFrom", "effectiveTo",
    "approvalEvidenceRef", "approvalEvidenceSha256", "businessPayloadSha256",
    "providerTuple", "targetContractVersion", "sourceBundleContractVersion",
    "proposalSha256", "boundSemanticAuthorityBusinessPayloadSha256",
    "boundFreshnessAuthorityBusinessPayloadSha256",
    "exactCapacityCanonicalInstrumentId", "exactCapacityListingId",
    "permittedConsumers", "permittedUse", "approvalBasis",
    "empiricalValidationStatus", "authority",
}
_BUSINESS_PAYLOAD_FIELDS = (
    "authorityKind", "providerTuple", "targetContractVersion",
    "sourceBundleContractVersion", "proposalSha256",
    "boundSemanticAuthorityBusinessPayloadSha256",
    "boundFreshnessAuthorityBusinessPayloadSha256",
    "exactCapacityCanonicalInstrumentId", "exactCapacityListingId",
    "permittedConsumers", "permittedUse", "approvalBasis",
    "empiricalValidationStatus", "authority",
)
_APPROVAL_FIELDS = {
    "schemaVersion", "approvalStatus", "ratifiedAt", "authorityKind",
    "ruleId", "ruleVersion", "approvedBusinessPayloadSha256",
    "sourceEvidence", "assertion", "decision", "boundary",
}
_APPROVAL_ASSERTION_FIELDS = {
    "providerTuple", "targetContractVersion", "sourceBundleContractVersion",
    "proposalSha256", "boundSemanticAuthorityBusinessPayloadSha256",
    "boundFreshnessAuthorityBusinessPayloadSha256",
    "exactCapacityCanonicalInstrumentId", "exactCapacityListingId",
    "permittedConsumers", "riskInputIncluded", "sizingIncluded",
    "orderIncluded", "productionIncluded",
}
_APPROVAL_DECISION_FIELDS = {
    "decisionStatus", "basis", "retroactiveUsePermitted",
    "realCapitalUseIncluded",
}


class PortfolioAccountFactConsumptionAuthorityError(ValueError):
    pass


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise PortfolioAccountFactConsumptionAuthorityError(code)
    try:
        parsed = dt.datetime.strptime(value, _TIMESTAMP_FORMAT).replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        raise PortfolioAccountFactConsumptionAuthorityError(code) from None
    if parsed.strftime(_TIMESTAMP_FORMAT) != value:
        raise PortfolioAccountFactConsumptionAuthorityError(code)
    return parsed


def business_payload(row: dict) -> dict:
    return {field: row.get(field) for field in _BUSINESS_PAYLOAD_FIELDS}


def require_consumer_id(consumer_id: object) -> str:
    """A routing scope, never authentication -- but it must be explicit."""
    if not isinstance(consumer_id, str) or not consumer_id.strip():
        raise PortfolioAccountFactConsumptionAuthorityError(
            "ACCOUNT_FACT_CONSUMER_ID_REQUIRED"
        )
    return consumer_id


def _validate_permitted_consumers(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_PERMITTED_CONSUMERS_INVALID"
        )
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or "*" in entry:
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_PERMITTED_CONSUMERS_INVALID"
            )
    if len(set(value)) != len(value):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_PERMITTED_CONSUMERS_DUPLICATE"
        )
    return list(value)


def _validate_row(row: object) -> dict:
    if not isinstance(row, dict) or set(row) != _ROW_FIELDS:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_ROW_FIELDS_INVALID"
        )
    if row.get("authorityKind") != AUTHORITY_KIND:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_ROW_KIND_INVALID"
        )
    if row.get("approvalStatus") != RATIFIED:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_ROW_NOT_RATIFIED"
        )
    if (
        row.get("ruleId") != RULE_ID
        or type(row.get("ruleVersion")) is not int
        or row.get("ruleVersion") != 1
    ):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_RULE_ID_VERSION_INVALID"
        )
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
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_EFFECTIVE_WINDOW_INVALID"
            )
    if first_seen != ratified or effective != ratified:
        # No retroactive authorization: a record cannot claim to have been
        # effective before it was ratified and first seen.
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_INITIAL_TIMESTAMPS_DIVERGE"
        )
    if row.get("providerTuple") != PROVIDER_TUPLE:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_PROVIDER_TUPLE_INVALID"
        )
    if row.get("targetContractVersion") != TARGET_CONTRACT_VERSION:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_TARGET_CONTRACT_INVALID"
        )
    if row.get("sourceBundleContractVersion") != SOURCE_BUNDLE_CONTRACT_VERSION:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_SOURCE_BUNDLE_CONTRACT_INVALID"
        )
    for field in (
        "approvalEvidenceSha256", "businessPayloadSha256", "proposalSha256",
        "boundSemanticAuthorityBusinessPayloadSha256",
        "boundFreshnessAuthorityBusinessPayloadSha256",
    ):
        if _SHA256_RE.fullmatch(str(row.get(field, ""))) is None:
            raise PortfolioAccountFactConsumptionAuthorityError(
                f"AUTHORITY_HASH_INVALID:{field}"
            )
    if not isinstance(row.get("approvalEvidenceRef"), str) or not row[
        "approvalEvidenceRef"
    ]:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_REF_INVALID"
        )
    _validate_permitted_consumers(row.get("permittedConsumers"))
    if row.get("permittedUse") != PERMITTED_USE:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_PERMITTED_USE_INVALID"
        )
    for field in ("approvalBasis", "empiricalValidationStatus"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise PortfolioAccountFactConsumptionAuthorityError(
                f"AUTHORITY_TEXT_FIELD_INVALID:{field}"
            )
    authority = row.get("authority")
    if (
        not isinstance(authority, dict)
        or authority != ACCOUNT_FACT_CONSUMPTION_AUTHORITY
        or any(type(value) is not bool for value in authority.values())
    ):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_BOUNDARY_INVALID"
        )
    if row["businessPayloadSha256"] != payload_sha256(business_payload(row)):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_BUSINESS_HASH_MISMATCH"
        )
    return dict(row)


def validate_authority_document(authority: object) -> dict:
    if not isinstance(authority, dict):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_DOCUMENT_NOT_OBJECT"
        )
    clean = {
        key: value for key, value in authority.items() if not key.startswith("_")
    }
    if set(clean) != _DOCUMENT_FIELDS:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_DOCUMENT_FIELDS_INVALID"
        )
    if clean.get("schemaVersion") != SCHEMA_VERSION:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_SCHEMA_INVALID"
        )
    if clean.get("policyVersion") != POLICY_VERSION:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_POLICY_VERSION_INVALID"
        )
    if not isinstance(clean.get("evidenceBasis"), str):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_EVIDENCE_BASIS_INVALID"
        )
    records = clean.get("accountFactConsumptionAuthorityRecords")
    if not isinstance(records, list) or len(records) > 1:
        # Structurally 0 or 1. Zero is the committed default and resolves
        # blocked; it is not an error.
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_AT_MOST_ONE_RECORD_REQUIRED"
        )
    for record in records:
        _validate_row(record)
    return dict(authority)


def load_authority(path: Path = CONFIG_PATH) -> dict:
    path = Path(path).resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_DOCUMENT_READ_FAILED"
        ) from error
    document = validate_authority_document(document)
    document["_sourcePath"] = str(path)
    return document


# ---------------------------------------------------------------------------
# Git provenance -- one externally resolved immutable commit for every
# document in the operation, never a per-document HEAD lookup that could
# drift mid-operation.
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_GIT_RESOLUTION_FAILED"
        ) from error


def _repo_and_relative(path: Path) -> tuple[Path, str]:
    try:
        repo = Path(
            _git(path.parent, "rev-parse", "--show-toplevel").decode().strip()
        ).resolve()
        relative = path.relative_to(repo).as_posix()
    except (ValueError, UnicodeDecodeError):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_SOURCE_NOT_IN_GIT_REPO"
        ) from None
    return repo, relative


def resolve_trusted_commit(
    *, source_paths: list[str], trusted_commit: str | None = None,
) -> tuple[Path, str]:
    """Resolve ONE immutable commit for every supplied authority document.

    Every document must live in the same repository, so a single recorded
    ``trustedCommit`` really does pin all of them.  Default mode resolves
    the repository HEAD exactly once and additionally requires a clean
    worktree for each document; an explicit pin must already be a full
    immutable object id that rev-parses to itself.
    """
    if not source_paths:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_SOURCE_PATH_REQUIRED"
        )
    located: list[tuple[Path, str]] = []
    for raw in source_paths:
        if not isinstance(raw, str) or not raw:
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_SOURCE_PATH_REQUIRED"
            )
        path = Path(raw)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_SOURCE_PATH_INVALID"
            )
        located.append(_repo_and_relative(path))
    repos = {str(repo) for repo, _ in located}
    if len(repos) != 1:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_DOCUMENT_REPOSITORY_MISMATCH"
        )
    repo = located[0][0]
    if trusted_commit is None:
        commit = _git(repo, "rev-parse", "HEAD").decode().strip()
        if _FULL_COMMIT_RE.fullmatch(commit) is None:
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE"
            )
        for _, relative in located:
            if _git(repo, "status", "--porcelain", "--", relative).decode().strip():
                raise PortfolioAccountFactConsumptionAuthorityError(
                    "AUTHORITY_SOURCE_WORKTREE_DIRTY"
                )
        return repo, commit
    if _FULL_COMMIT_RE.fullmatch(str(trusted_commit)) is None:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE"
        )
    resolved = _git(
        repo, "rev-parse", "--verify", f"{trusted_commit}^{{commit}}"
    ).decode().strip()
    if resolved != trusted_commit:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE"
        )
    return repo, trusted_commit


def _document_provenance(authority: dict, repo: Path, commit: str) -> str:
    source = authority.get("_sourcePath")
    if not isinstance(source, str) or not source:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "ACCOUNT_FACT_AUTHORITY_FILE_PROVENANCE_REQUIRED"
        )
    path = Path(source)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_SOURCE_PATH_INVALID"
        )
    disk = path.read_bytes()
    try:
        disk_doc = json.loads(disk.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_DISK_BYTES_INVALID"
        ) from None
    memory = {
        key: value for key, value in authority.items() if not key.startswith("_")
    }
    if memory != disk_doc:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_MEMORY_DISK_MISMATCH"
        )
    try:
        relative = path.relative_to(repo).as_posix()
    except ValueError:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_DOCUMENT_REPOSITORY_MISMATCH"
        ) from None
    if _git(repo, "show", f"{commit}:{relative}") != disk:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_DISK_COMMIT_MISMATCH"
        )
    return relative


def _commits_for_path(repo: Path, commit: str, relative: str) -> list[str]:
    output = _git(
        repo, "log", "--reverse", "--format=%H", commit, "--", relative
    ).decode().splitlines()
    return [value for value in output if _FULL_COMMIT_RE.fullmatch(value)]


def _commit_time(repo: Path, commit: str) -> dt.datetime:
    timestamp = _git(repo, "show", "-s", "--format=%cI", commit).decode().strip()
    return dt.datetime.fromisoformat(
        timestamp.replace("Z", "+00:00")
    ).astimezone(dt.timezone.utc).replace(microsecond=0)


def _row_first_seen(
    repo: Path, commit: str, relative: str, row: dict
) -> dt.datetime:
    for candidate in _commits_for_path(repo, commit, relative):
        try:
            value = json.loads(
                _git(repo, "show", f"{candidate}:{relative}").decode()
            )
        except (
            json.JSONDecodeError, UnicodeDecodeError,
            PortfolioAccountFactConsumptionAuthorityError,
        ):
            continue
        records = (
            value.get("accountFactConsumptionAuthorityRecords", [])
            if isinstance(value, dict) else []
        )
        if any(candidate_row == row for candidate_row in records):
            return _commit_time(repo, candidate)
    raise PortfolioAccountFactConsumptionAuthorityError(
        "AUTHORITY_ROW_FIRST_SEEN_NOT_VERIFIED"
    )


def _approval_first_seen(
    repo: Path, commit: str, relative: str, expected_bytes: bytes
) -> dt.datetime:
    for candidate in _commits_for_path(repo, commit, relative):
        try:
            value = _git(repo, "show", f"{candidate}:{relative}")
        except PortfolioAccountFactConsumptionAuthorityError:
            continue
        if value == expected_bytes:
            return _commit_time(repo, candidate)
    raise PortfolioAccountFactConsumptionAuthorityError(
        "AUTHORITY_APPROVAL_FIRST_SEEN_NOT_VERIFIED"
    )


def _verify_approval(repo: Path, commit: str, row: dict) -> dt.datetime:
    approval_path = (repo / row["approvalEvidenceRef"]).resolve()
    try:
        relative = approval_path.relative_to(repo).as_posix()
    except ValueError:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_PATH_INVALID"
        ) from None
    if approval_path.is_symlink() or not approval_path.is_file():
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_FILE_INVALID"
        )
    disk = approval_path.read_bytes()
    if hashlib.sha256(disk).hexdigest() != row["approvalEvidenceSha256"]:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_HASH_MISMATCH"
        )
    if _git(repo, "show", f"{commit}:{relative}") != disk:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_COMMIT_MISMATCH"
        )
    try:
        approval = json.loads(disk.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_JSON_INVALID"
        ) from None
    if not isinstance(approval, dict) or set(approval) != _APPROVAL_FIELDS:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_FIELDS_INVALID"
        )
    if type(approval.get("ruleVersion")) is not int:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_RULE_VERSION_TYPE_INVALID"
        )
    if (
        approval.get("schemaVersion") != APPROVAL_SCHEMA_VERSION
        or approval.get("approvalStatus") != RATIFIED
        or approval.get("ratifiedAt") != row["ratifiedAt"]
        or approval.get("authorityKind") != row["authorityKind"]
        or approval.get("ruleId") != row["ruleId"]
        or approval.get("ruleVersion") != row["ruleVersion"]
        or approval.get("approvedBusinessPayloadSha256")
        != row["businessPayloadSha256"]
    ):
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_BINDING_MISMATCH"
        )
    _verify_approval_assertion(approval, row)
    _verify_approval_decision(approval)
    if approval.get("boundary") != APPROVAL_BOUNDARY:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_BOUNDARY_INVALID"
        )
    _verify_approval_sources(repo, commit, approval)
    return _approval_first_seen(repo, commit, relative, disk)


def _verify_approval_assertion(approval: dict, row: dict) -> None:
    assertion = approval.get("assertion")
    if not isinstance(assertion, dict) or set(assertion) != _APPROVAL_ASSERTION_FIELDS:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_ASSERTION_FIELDS_INVALID"
        )
    for field in (
        "riskInputIncluded", "sizingIncluded", "orderIncluded",
        "productionIncluded",
    ):
        if type(assertion.get(field)) is not bool or assertion[field] is not False:
            raise PortfolioAccountFactConsumptionAuthorityError(
                f"AUTHORITY_APPROVAL_BOOLEAN_TYPE_INVALID:{field}"
            )
    expected = {
        "providerTuple": PROVIDER_TUPLE,
        "targetContractVersion": row["targetContractVersion"],
        "sourceBundleContractVersion": row["sourceBundleContractVersion"],
        "proposalSha256": row["proposalSha256"],
        "boundSemanticAuthorityBusinessPayloadSha256": row[
            "boundSemanticAuthorityBusinessPayloadSha256"
        ],
        "boundFreshnessAuthorityBusinessPayloadSha256": row[
            "boundFreshnessAuthorityBusinessPayloadSha256"
        ],
        "exactCapacityCanonicalInstrumentId": row[
            "exactCapacityCanonicalInstrumentId"
        ],
        "exactCapacityListingId": row["exactCapacityListingId"],
        "permittedConsumers": row["permittedConsumers"],
        "riskInputIncluded": False,
        "sizingIncluded": False,
        "orderIncluded": False,
        "productionIncluded": False,
    }
    if assertion != expected:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_ASSERTION_INVALID"
        )


def _verify_approval_decision(approval: dict) -> None:
    decision = approval.get("decision")
    if not isinstance(decision, dict) or set(decision) != _APPROVAL_DECISION_FIELDS:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_DECISION_FIELDS_INVALID"
        )
    if decision.get("decisionStatus") != APPROVAL_DECISION_STATUS:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_DECISION_INVALID"
        )
    if not isinstance(decision.get("basis"), str) or not decision["basis"].strip():
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_DECISION_INVALID"
        )
    for field in ("retroactiveUsePermitted", "realCapitalUseIncluded"):
        if type(decision.get(field)) is not bool or decision[field] is not False:
            raise PortfolioAccountFactConsumptionAuthorityError(
                f"AUTHORITY_APPROVAL_BOOLEAN_TYPE_INVALID:{field}"
            )


def _verify_approval_sources(repo: Path, commit: str, approval: dict) -> None:
    sources = approval.get("sourceEvidence")
    if not isinstance(sources, list) or not sources:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_APPROVAL_SOURCES_INVALID"
        )
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_APPROVAL_SOURCE_FIELDS_INVALID"
            )
        if not isinstance(source["path"], str) or source["path"] in seen:
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_APPROVAL_SOURCE_PATH_INVALID"
            )
        seen.add(source["path"])
        source_path = (repo / source["path"]).resolve()
        try:
            source_relative = source_path.relative_to(repo).as_posix()
        except ValueError:
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_APPROVAL_SOURCE_PATH_INVALID"
            ) from None
        source_bytes = _git(repo, "show", f"{commit}:{source_relative}")
        if (
            _SHA256_RE.fullmatch(str(source.get("sha256", ""))) is None
            or hashlib.sha256(source_bytes).hexdigest() != source["sha256"]
        ):
            raise PortfolioAccountFactConsumptionAuthorityError(
                "AUTHORITY_APPROVAL_SOURCE_HASH_MISMATCH"
            )


def _blocked(status: str, **extra: object) -> dict:
    result = {
        "status": status,
        "authority": dict(ACCOUNT_FACT_CONSUMPTION_AUTHORITY_ALL_FALSE),
    }
    result.update(extra)
    return result


def resolve_account_fact_consumption_authority(
    *, decision_at: str, authority: dict, consumer_id: str,
    repo: Path | None = None, trusted_commit: str | None = None,
    provider_tuple: dict = PROVIDER_TUPLE,
    target_contract_version: str = TARGET_CONTRACT_VERSION,
    source_bundle_contract_version: str = SOURCE_BUNDLE_CONTRACT_VERSION,
) -> dict:
    """Resolve the consumption record for ``consumer_id`` at ``decision_at``.

    ``repo``/``trusted_commit`` come from :func:`resolve_trusted_commit` so
    the whole operation is pinned to exactly one immutable commit.  When
    they are omitted, they are resolved here from this document alone.
    """
    consumer_id = require_consumer_id(consumer_id)
    decision = _parse_utc(decision_at, "AUTHORITY_DECISION_AT_INVALID")
    validate_authority_document(authority)
    if (
        provider_tuple != PROVIDER_TUPLE
        or target_contract_version != TARGET_CONTRACT_VERSION
        or source_bundle_contract_version != SOURCE_BUNDLE_CONTRACT_VERSION
    ):
        return _blocked(NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
    records = authority["accountFactConsumptionAuthorityRecords"]
    if not records:
        # The committed default. No git work is done and nothing is
        # produced: an empty registry is a real, fail-closed answer.
        return _blocked(NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
    row = records[0]
    if (
        row["exactCapacityCanonicalInstrumentId"]
        != fact_v3.EXACT_CAPACITY_CANONICAL_INSTRUMENT_ID
        or row["exactCapacityListingId"] != fact_v3.EXACT_CAPACITY_LISTING_ID
    ):
        return _blocked(ACCOUNT_FACT_AUTHORITY_CAPACITY_BINDING_MISMATCH)

    if repo is None or trusted_commit is None:
        repo, trusted_commit = resolve_trusted_commit(
            source_paths=[authority.get("_sourcePath")],
            trusted_commit=trusted_commit,
        )
    relative = _document_provenance(authority, repo, trusted_commit)
    first_row = _row_first_seen(repo, trusted_commit, relative, row)
    first_approval = _verify_approval(repo, trusted_commit, row)
    claimed = _parse_utc(row["firstSeenAt"], "AUTHORITY_FIRST_SEEN_AT_INVALID")
    if first_row != claimed or first_approval != claimed:
        raise PortfolioAccountFactConsumptionAuthorityError(
            "AUTHORITY_FIRST_SEEN_CLAIM_MISMATCH"
        )
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
        return _blocked(
            NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE,
            realUsableFrom=usable.strftime(_TIMESTAMP_FORMAT),
        )
    if consumer_id not in row["permittedConsumers"]:
        return _blocked(
            ACCOUNT_FACT_CONSUMER_NOT_PERMITTED,
            realUsableFrom=usable.strftime(_TIMESTAMP_FORMAT),
        )
    return {
        "status": RESOLVED,
        "authorityKind": row["authorityKind"],
        "ruleId": row["ruleId"],
        "ruleVersion": row["ruleVersion"],
        "consumerId": consumer_id,
        "permittedConsumers": list(row["permittedConsumers"]),
        "permittedUse": row["permittedUse"],
        "realUsableFrom": usable.strftime(_TIMESTAMP_FORMAT),
        "businessPayloadSha256": row["businessPayloadSha256"],
        "approvalEvidenceSha256": row["approvalEvidenceSha256"],
        "boundSemanticAuthorityBusinessPayloadSha256": row[
            "boundSemanticAuthorityBusinessPayloadSha256"
        ],
        "boundFreshnessAuthorityBusinessPayloadSha256": row[
            "boundFreshnessAuthorityBusinessPayloadSha256"
        ],
        "trustedCommit": trusted_commit,
        "authority": dict(row["authority"]),
    }
