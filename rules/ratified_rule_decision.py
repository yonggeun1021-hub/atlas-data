#!/usr/bin/env python3
"""P5-02 exact-provenance validator for externally ratified Rule results.

PASS/FAIL is never calculated here. A packet is consumable only when a clean,
committed approval envelope binds its complete decision-determining payload.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "ratified_rule_decision_contract.json"
RULES_PATH = ROOT / "config" / "rules.json"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{2,255}$")


class RatifiedRuleDecisionError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RatifiedRuleDecisionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 2,
        "contract_version": "ratified_rule_decision/2",
        "output_schema_version": "ratified_rule_decision_packet/2",
        "authority_evidence_schema_version": "ratified_rule_authority_evidence/1",
        "slice_id": "TSM_DECISION_SLICE_V1",
        "subject": "TSM",
        "required_rule_ids": [f"RULE-{n:04d}" for n in range(3, 10)],
        "result_statuses": ["PASS", "FAIL"],
        "provenance_mode": "EXACT_BYTES_CLEAN_GIT_FULL_HISTORY",
        "authority": {
            "external_ratified_result_validation_only": True,
            "approval_evidence_exact_provenance_required": True,
            "threshold_invention_authorized": False,
            "rule_re_evaluation_authorized": False,
            "stage_change_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    if not isinstance(value, dict) or value != _expected_contract():
        raise RatifiedRuleDecisionError("CONTRACT_IDENTITY_INVALID")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read(path))


def load_rules(path: Path = RULES_PATH) -> dict:
    value = _read(path)
    if (not isinstance(value, dict) or value.get("artifact") != "Rule SSOT (config/rules.json)"
            or value.get("authority") is not True or not isinstance(value.get("rules"), list)):
        raise RatifiedRuleDecisionError("RULE_SSOT_INVALID")
    return value


def _sha(value, code):
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise RatifiedRuleDecisionError(code)
    return value


def _ref(value, code):
    if not isinstance(value, str) or REF_RE.fullmatch(value) is None:
        raise RatifiedRuleDecisionError(code)
    return value


def _utc(value, code) -> datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise RatifiedRuleDecisionError(code)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise RatifiedRuleDecisionError(code) from exc


def determining_payload(results, evidence_set_sha256, evaluated_at, evaluated_by,
                        authority_ref, rules, contract) -> dict:
    return {
        "slice_id": contract["slice_id"], "subject": contract["subject"],
        "evaluated_at": evaluated_at, "evaluated_by": evaluated_by,
        "authority_ref": authority_ref, "evidence_set_sha256": evidence_set_sha256,
        "rule_registry_sha256": payload_sha256(rules), "results": copy.deepcopy(results),
    }


def _git(root: Path, *args: str, binary=False):
    try:
        done = subprocess.run(["git", *args], cwd=root, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RatifiedRuleDecisionError("AUTHORITY_PROVENANCE_UNVERIFIED") from exc
    return done.stdout if binary else done.stdout.decode()


def _verify_canonical_rules_at_head() -> None:
    relative = RULES_PATH.resolve().relative_to(ROOT.resolve()).as_posix()
    if _git(ROOT, "status", "--porcelain", "--", relative).strip():
        raise RatifiedRuleDecisionError("RULE_SSOT_DIRTY")
    if RULES_PATH.read_bytes() != _git(ROOT, "show", f"HEAD:{relative}", binary=True):
        raise RatifiedRuleDecisionError("RULE_SSOT_HEAD_MISMATCH")


def _authority_path(ref: str, root: Path) -> tuple[Path, str]:
    _ref(ref, "AUTHORITY_EVIDENCE_REF_INVALID")
    relative = Path(ref)
    if relative.is_absolute() or ".." in relative.parts or relative.parts[0] == "test":
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_PATH_FORBIDDEN")
    resolved = (root / relative).resolve()
    try:
        normalized = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_PATH_FORBIDDEN") from exc
    if normalized != relative.as_posix() or not resolved.is_file():
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_MISSING")
    return resolved, normalized


def _exact_first_seen(root: Path, relative: str, current: bytes) -> tuple[str, str]:
    commits = [x for x in _git(root, "log", "--reverse", "--format=%H", "--", relative).splitlines() if x]
    if not commits:
        raise RatifiedRuleDecisionError("AUTHORITY_PROVENANCE_UNVERIFIED")
    for commit in commits:
        if COMMIT_RE.fullmatch(commit) is None:
            continue
        try:
            blob = _git(root, "show", f"{commit}:{relative}", binary=True)
        except RatifiedRuleDecisionError:
            continue
        if blob == current:
            iso = _git(root, "show", "-s", "--format=%cI", commit).strip()
            at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return commit, at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise RatifiedRuleDecisionError("AUTHORITY_EXACT_CONTENT_NOT_IN_HISTORY")


def _verify_authority(binding: dict, determining: dict, root: Path, contract: dict) -> None:
    if not isinstance(binding, dict) or set(binding) != {
        "ref", "sha256", "first_seen_commit", "first_seen_at", "usable_from"
    }:
        raise RatifiedRuleDecisionError("AUTHORITY_BINDING_FIELDS_MISMATCH")
    if _git(root, "rev-parse", "--is-shallow-repository").strip() != "false":
        raise RatifiedRuleDecisionError("AUTHORITY_FULL_GIT_HISTORY_REQUIRED")
    path, relative = _authority_path(binding.get("ref"), root)
    if _git(root, "status", "--porcelain", "--", relative).strip():
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_DIRTY")
    current = path.read_bytes()
    if current != _git(root, "show", f"HEAD:{relative}", binary=True):
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_HEAD_MISMATCH")
    digest = hashlib.sha256(current).hexdigest()
    if _sha(binding.get("sha256"), "AUTHORITY_EVIDENCE_SHA_INVALID") != digest:
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_SHA_MISMATCH")
    first_commit, first_at = _exact_first_seen(root, relative, current)
    if binding.get("first_seen_commit") != first_commit:
        raise RatifiedRuleDecisionError("AUTHORITY_FIRST_SEEN_COMMIT_MISMATCH")
    if binding.get("first_seen_at") != first_at:
        raise RatifiedRuleDecisionError("AUTHORITY_FIRST_SEEN_AT_MISMATCH")
    evidence = _read(path)
    if not isinstance(evidence, dict) or set(evidence) != {
        "schema_version", "approval_status", "authority_ref", "approved_by",
        "evaluated_at", "ratified_at", "approved_decision_payload_sha256"
    }:
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_FIELDS_MISMATCH")
    if (evidence.get("schema_version") != contract["authority_evidence_schema_version"]
            or evidence.get("approval_status") != "RATIFIED"
            or evidence.get("authority_ref") != determining["authority_ref"]
            or evidence.get("evaluated_at") != determining["evaluated_at"]
            or evidence.get("approved_decision_payload_sha256") != payload_sha256(determining)):
        raise RatifiedRuleDecisionError("AUTHORITY_EVIDENCE_SEMANTIC_MISMATCH")
    _ref(evidence.get("approved_by"), "AUTHORITY_APPROVED_BY_INVALID")
    evaluated = _utc(evidence.get("evaluated_at"), "AUTHORITY_EVALUATED_AT_INVALID")
    ratified = _utc(evidence.get("ratified_at"), "AUTHORITY_RATIFIED_AT_INVALID")
    first_seen = _utc(first_at, "AUTHORITY_FIRST_SEEN_AT_INVALID")
    if evaluated > ratified or ratified > first_seen:
        raise RatifiedRuleDecisionError("AUTHORITY_TIME_ORDER_INVALID")
    usable = max(ratified, first_seen).strftime("%Y-%m-%dT%H:%M:%SZ")
    if binding.get("usable_from") != usable:
        raise RatifiedRuleDecisionError("AUTHORITY_USABLE_FROM_MISMATCH")


def _validate_results(results, rules, contract) -> dict:
    registry = {row["rule_id"]: row for row in rules["rules"]}
    if not isinstance(results, list) or [r.get("rule_id") for r in results] != contract["required_rule_ids"]:
        raise RatifiedRuleDecisionError("RESULT_RULE_SET_INVALID")
    counts = {"PASS": 0, "FAIL": 0}
    for row in results:
        canonical = registry.get(row.get("rule_id")) if isinstance(row, dict) else None
        if (not isinstance(row, dict) or set(row) != {"rule_id", "subject", "condition_text_sha256",
                "result", "evidence_reference_ids", "reason"} or canonical is None
                or canonical.get("subject") != contract["subject"]
                or row.get("subject") != canonical.get("subject")
                or row.get("condition_text_sha256") != canonical.get("condition_text_sha256")
                or row.get("result") not in contract["result_statuses"]
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()
                or not isinstance(row.get("evidence_reference_ids"), list)
                or not row["evidence_reference_ids"]
                or row["evidence_reference_ids"] != sorted(set(row["evidence_reference_ids"]))
                or any(not isinstance(x, str) or REF_RE.fullmatch(x) is None
                       for x in row["evidence_reference_ids"])):
            raise RatifiedRuleDecisionError(f"RESULT_INVALID:{row.get('rule_id') if isinstance(row, dict) else None}")
        counts[row["result"]] += 1
    return counts


def validate_packet(value: dict, rules=None, contract=None, repository_root: Path = ROOT) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    if rules is None:
        if Path(repository_root).resolve() != ROOT.resolve():
            raise RatifiedRuleDecisionError("RULE_REPOSITORY_OVERRIDE_FORBIDDEN")
        _verify_canonical_rules_at_head()
        rules = load_rules()
    else:
        rules = copy.deepcopy(rules)
    fields = {"schema_version", "contract_version", "slice_id", "subject", "evaluated_at",
              "evaluated_by", "authority_ref", "evidence_set_sha256", "rule_registry_sha256",
              "results", "summary", "authority_evidence", "authority", "packet_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise RatifiedRuleDecisionError("PACKET_FIELDS_MISMATCH")
    if (value.get("schema_version") != contract["output_schema_version"]
            or value.get("contract_version") != contract["contract_version"]
            or value.get("slice_id") != contract["slice_id"] or value.get("subject") != contract["subject"]
            or value.get("authority") != contract["authority"]):
        raise RatifiedRuleDecisionError("PACKET_IDENTITY_INVALID")
    _utc(value.get("evaluated_at"), "EVALUATED_AT_INVALID")
    _ref(value.get("evaluated_by"), "EVALUATED_BY_INVALID")
    _ref(value.get("authority_ref"), "AUTHORITY_REF_INVALID")
    _sha(value.get("evidence_set_sha256"), "EVIDENCE_SET_SHA_INVALID")
    if value.get("rule_registry_sha256") != payload_sha256(rules):
        raise RatifiedRuleDecisionError("RULE_REGISTRY_SHA_MISMATCH")
    counts = _validate_results(value.get("results"), rules, contract)
    if value.get("summary") != {"total": len(value["results"]), **counts}:
        raise RatifiedRuleDecisionError("SUMMARY_MISMATCH")
    determining = determining_payload(value["results"], value["evidence_set_sha256"],
        value["evaluated_at"], value["evaluated_by"], value["authority_ref"], rules, contract)
    _verify_authority(value.get("authority_evidence"), determining, Path(repository_root), contract)
    digest = _sha(value.get("packet_sha256"), "PACKET_SHA_INVALID")
    payload = copy.deepcopy(value); payload.pop("packet_sha256")
    if payload_sha256(payload) != digest:
        raise RatifiedRuleDecisionError("PACKET_SHA_MISMATCH")
    return copy.deepcopy(value)


def build_packet(results, evidence_set_sha256, evaluated_at, evaluated_by, authority_ref,
                 authority_evidence, rules=None, contract=None, repository_root: Path = ROOT) -> dict:
    contract = load_contract() if contract is None else _validate_contract(contract)
    rules = load_rules() if rules is None else copy.deepcopy(rules)
    counts = _validate_results(results, rules, contract)
    packet = {"schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"], "slice_id": contract["slice_id"],
        "subject": contract["subject"], "evaluated_at": evaluated_at, "evaluated_by": evaluated_by,
        "authority_ref": authority_ref, "evidence_set_sha256": evidence_set_sha256,
        "rule_registry_sha256": payload_sha256(rules), "results": copy.deepcopy(results),
        "summary": {"total": len(results), **counts},
        "authority_evidence": copy.deepcopy(authority_evidence), "authority": copy.deepcopy(contract["authority"])}
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, rules, contract, repository_root)
