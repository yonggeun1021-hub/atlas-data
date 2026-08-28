#!/usr/bin/env python3
"""Independent, fail-closed review for the KIS valuation freshness proposal."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

from portfolio_risk.kis_valuation_freshness_policy_proposal import (
    AUTHORITY_ALL_FALSE,
    MAX_PAIR_GAP_SECONDS,
    MAX_SOURCE_AGE_SECONDS,
    PRIVATE_PRECEDENT_COMMIT,
    PRIVATE_PRECEDENT_PATH,
    PRIVATE_PRECEDENT_SHA256,
    PROPOSAL_STATUS,
    SCHEMA_VERSION,
    TARGET_CONTRACT_VERSION,
    freshness_policy_proposal,
    payload_sha256,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_FORBIDDEN_KEYS = {
    "approval_status", "approvalStatus", "ratified_at", "ratifiedAt",
    "broker_verified", "brokerVerified", "tradingAuthority", "orderAuthority",
}
_FORBIDDEN_VALUES = {"RATIFIED", "BROKER_VERIFIED"}


class KisValuationFreshnessPolicyReviewError(ValueError):
    pass


def _scan_forbidden(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _FORBIDDEN_KEYS:
                raise KisValuationFreshnessPolicyReviewError(
                    f"EMBEDDED_AUTHORITY_FIELD_FORBIDDEN:{key}"
                )
            _scan_forbidden(nested)
    elif isinstance(value, list):
        for nested in value:
            _scan_forbidden(nested)
    elif isinstance(value, str) and value in _FORBIDDEN_VALUES:
        raise KisValuationFreshnessPolicyReviewError(
            f"EMBEDDED_AUTHORITY_VALUE_FORBIDDEN:{value}"
        )


def _git(checkout: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(checkout), *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise KisValuationFreshnessPolicyReviewError(
            "PRIVATE_OPERATING_PRECEDENT_GIT_RESOLUTION_FAILED"
        ) from exc
    return completed.stdout


def _resolve_private_precedent(checkout: Path | None) -> tuple[bytes | None, list[str]]:
    if checkout is None:
        return None, ["PRIVATE_OPERATING_PRECEDENT_REPRODUCTION_REQUIRED"]
    checkout = Path(checkout)
    if not checkout.is_absolute() or checkout.is_symlink() or not checkout.is_dir():
        return None, ["PRIVATE_OPERATING_PRECEDENT_CHECKOUT_INVALID"]
    try:
        _git(checkout, "cat-file", "-e", f"{PRIVATE_PRECEDENT_COMMIT}^{{commit}}")
        raw = _git(
            checkout, "show", f"{PRIVATE_PRECEDENT_COMMIT}:{PRIVATE_PRECEDENT_PATH}"
        )
    except KisValuationFreshnessPolicyReviewError as exc:
        return None, [str(exc)]
    if len(raw) > _MAX_SOURCE_BYTES:
        return None, ["PRIVATE_OPERATING_PRECEDENT_BYTES_TOO_LARGE"]
    if hashlib.sha256(raw).hexdigest() != PRIVATE_PRECEDENT_SHA256:
        return None, ["PRIVATE_OPERATING_PRECEDENT_HASH_MISMATCH"]
    return raw, []


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return tuple(reversed(parts))
    return None


def _review_precedent_semantics(raw: bytes | None) -> list[str]:
    if raw is None:
        return []
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError):
        return ["PRIVATE_OPERATING_PRECEDENT_PYTHON_INVALID"]

    dataclass_default = False
    env_default = False
    allowed_range = False
    stale_comparison = False
    confirmation_default = False
    confirmation_env_default = False
    confirmation_allowed_range = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "max_decision_age_seconds"
            and isinstance(node.value, ast.Constant)
            and node.value.value == MAX_SOURCE_AGE_SECONDS
        ):
            dataclass_default = True
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "confirmation_ttl_seconds"
            and isinstance(node.value, ast.Constant)
            and node.value.value == MAX_PAIR_GAP_SECONDS
        ):
            confirmation_default = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "get" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "ATLAS_PAPER_MAX_DECISION_AGE_SECONDS"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == str(MAX_SOURCE_AGE_SECONDS)
            ):
                env_default = True
            if (
                node.func.attr == "get" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "ATLAS_PAPER_CONFIRMATION_TTL_SECONDS"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == str(MAX_PAIR_GAP_SECONDS)
            ):
                confirmation_env_default = True
        if isinstance(node, ast.Compare):
            if (
                isinstance(node.left, ast.Constant) and node.left.value == 30
                and len(node.ops) == 2
                and all(isinstance(operator, ast.LtE) for operator in node.ops)
                and len(node.comparators) == 2
                and isinstance(node.comparators[0], ast.Name)
                and node.comparators[0].id == "max_decision_age"
                and isinstance(node.comparators[1], ast.Constant)
                and node.comparators[1].value == 900
            ):
                allowed_range = True
            if (
                isinstance(node.left, ast.Constant) and node.left.value == 30
                and len(node.ops) == 2
                and all(isinstance(operator, ast.LtE) for operator in node.ops)
                and len(node.comparators) == 2
                and isinstance(node.comparators[0], ast.Name)
                and node.comparators[0].id == "confirmation_ttl"
                and isinstance(node.comparators[1], ast.Constant)
                and node.comparators[1].value == 300
            ):
                confirmation_allowed_range = True
            if (
                isinstance(node.left, ast.Name) and node.left.id == "age"
                and len(node.ops) == 1 and isinstance(node.ops[0], ast.Gt)
                and len(node.comparators) == 1
                and _attribute_path(node.comparators[0])
                == ("self", "config", "max_decision_age_seconds")
            ):
                stale_comparison = True

    reasons = []
    if not dataclass_default:
        reasons.append("PRIVATE_PRECEDENT_DECISION_AGE_DEFAULT_NOT_REPRODUCED")
    if not env_default:
        reasons.append("PRIVATE_PRECEDENT_ENVIRONMENT_DEFAULT_NOT_REPRODUCED")
    if not allowed_range:
        reasons.append("PRIVATE_PRECEDENT_ALLOWED_RANGE_NOT_REPRODUCED")
    if not stale_comparison:
        reasons.append("PRIVATE_PRECEDENT_INCLUSIVE_BOUNDARY_NOT_REPRODUCED")
    if not confirmation_default:
        reasons.append("PRIVATE_PRECEDENT_CONFIRMATION_TTL_DEFAULT_NOT_REPRODUCED")
    if not confirmation_env_default:
        reasons.append("PRIVATE_PRECEDENT_CONFIRMATION_TTL_ENV_DEFAULT_NOT_REPRODUCED")
    if not confirmation_allowed_range:
        reasons.append("PRIVATE_PRECEDENT_CONFIRMATION_TTL_RANGE_NOT_REPRODUCED")
    return reasons


def review_freshness_policy_proposal(
    proposal: object,
    *,
    private_checkout: Path | None = None,
) -> dict:
    """Review proposal shape and independently reproduce its operating precedent."""
    _scan_forbidden(proposal)
    reasons: list[str] = []
    if not isinstance(proposal, dict):
        reasons.append("PROPOSAL_NOT_OBJECT")
        proposal = {}
    expected = freshness_policy_proposal()
    if proposal.get("schemaVersion") != SCHEMA_VERSION:
        reasons.append("PROPOSAL_SCHEMA_VERSION_INVALID")
    if proposal.get("proposalStatus") != PROPOSAL_STATUS:
        reasons.append("PROPOSAL_STATUS_INVALID")
    if proposal.get("targetContractVersion") != TARGET_CONTRACT_VERSION:
        reasons.append("PROPOSAL_TARGET_CONTRACT_INVALID")
    supplied_hash = proposal.get("proposalSha256")
    if _SHA256_RE.fullmatch(str(supplied_hash)) is None:
        reasons.append("PROPOSAL_HASH_INVALID")
    elif supplied_hash != payload_sha256(
        {key: value for key, value in proposal.items() if key != "proposalSha256"}
    ):
        reasons.append("PROPOSAL_HASH_MISMATCH")
    if proposal != expected:
        reasons.append("PROPOSAL_DIFFERS_FROM_CANONICAL_GENERATOR_OUTPUT")
    authority = proposal.get("authority")
    if (
        not isinstance(authority, dict)
        or set(authority) != set(AUTHORITY_ALL_FALSE)
        or any(type(value) is not bool for value in authority.values())
        or authority != AUTHORITY_ALL_FALSE
    ):
        reasons.append("AUTHORITY_NOT_ALL_FALSE")
    if any(proposal.get(field) is not False for field in (
        "canonicalAuthorityConfigMutated",
        "existingPortfolioAccountFactV2Mutated",
        "valuationSemanticProposalMutated",
    )):
        reasons.append("MUTATION_BOUNDARY_INVALID")

    policy = proposal.get("candidatePolicy")
    if not isinstance(policy, dict):
        reasons.append("CANDIDATE_POLICY_INVALID")
    else:
        if policy.get("maxSourceAgeSeconds") != MAX_SOURCE_AGE_SECONDS:
            reasons.append("MAX_SOURCE_AGE_NOT_EXACT_PROPOSAL")
        if policy.get("maxPairGapSeconds") != MAX_PAIR_GAP_SECONDS:
            reasons.append("MAX_PAIR_GAP_NOT_EXACT_PROPOSAL")
        pair_gap = policy.get("maxPairGapSeconds")
        source_age = policy.get("maxSourceAgeSeconds")
        if (
            isinstance(pair_gap, bool) or not isinstance(pair_gap, int)
            or isinstance(source_age, bool) or not isinstance(source_age, int)
            or pair_gap > source_age
        ):
            reasons.append("PAIR_GAP_EXCEEDS_SOURCE_WINDOW")
        if policy.get("callerOverridePermitted") is not False:
            reasons.append("CALLER_OVERRIDE_FORBIDDEN")
        if policy.get("comparison") != (
            "NONNEGATIVE_AGE_AND_ABSOLUTE_GAP_LESS_THAN_OR_EQUAL"
        ):
            reasons.append("BOUNDARY_COMPARISON_INVALID")

    applicability = proposal.get("applicability")
    if not isinstance(applicability, dict) or applicability != expected["applicability"]:
        reasons.append("APPLICABILITY_BOUNDARY_INVALID")

    raw, source_reasons = _resolve_private_precedent(private_checkout)
    reasons.extend(source_reasons)
    reasons.extend(_review_precedent_semantics(raw))
    reasons = sorted(set(reasons))
    return {
        "reviewStatus": "REVIEW_READY_FOR_CIO" if not reasons else "REVIEW_INCOMPLETE",
        "proposalSha256": proposal.get("proposalSha256"),
        "precedentCommitSha": PRIVATE_PRECEDENT_COMMIT,
        "precedentContentSha256": PRIVATE_PRECEDENT_SHA256,
        "reasons": reasons,
        "canonicalAuthorityConfigMutated": False,
        "authority": dict(AUTHORITY_ALL_FALSE),
    }


if __name__ == "__main__":
    print(json.dumps(
        review_freshness_policy_proposal(freshness_policy_proposal()),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ))
