#!/usr/bin/env python3
"""Atlas confirmed-rule registry v1 validator (``config/rule_registry_v1.json``).

User ratification RULE-GOVERNANCE-EVIDENCE-GATED-ADJUSTMENT (2026-09-15)
requires a registry of every user-confirmed rule so each PAPER/REAL decision can
be evaluated per rule.  This module only *validates and reads* that registry.
It never decides, sizes, blocks, or orders anything.

Every guarantee below is checked from committed bytes only (offline):

* rule ids are unique and are exactly the fixed v1 id set;
* every rule has at least one ``USER_RATIFICATION`` source record, copied
  byte-exact under ``evidence/authority/`` (sha256 and byte length match);
* every CIO addendum names (by sha256) a user ratification of the same rule;
* the record id inside each source record matches the registry row;
* every machine-readable parameter, effective instant, evidence level, review
  trigger and minimum sample points into the source record with a JSON pointer
  and is either equal to the record value (``EXACT``) or a verbatim substring
  of it (``PARSED_FROM_TEXT``) -- no parameter without a source;
* versions are positive integers, monotone along each ``lineage_key`` in
  effective order, and a registry-internal ``supersedes`` points backwards;
* a rule without pre-registered review triggers must say
  ``trigger_pending_user_confirmation: true``.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RELATIVE_PATH = "config/rule_registry_v1.json"
REGISTRY_PATH = ROOT / REGISTRY_RELATIVE_PATH
SCHEMA_VERSION = "atlas_rule_registry/1"
AUTHORITY_DIR = "evidence/authority/"

# Fixed by CLAUDE_CIO for v1.  Adding/removing a rule is a registry version
# change, never a silent edit.
REQUIRED_RULE_IDS = (
    "RULE.ALLOCATION.V2",
    "RULE.HEDGE.INVERSE.V1",
    "RULE.LIQUIDITY.KRUS.V1",
    "RULE.CRYPTO.RUNTIME.V1",
    "RULE.CRYPTO.FRESHNESS.PER_MARKET.V1",
    "RULE.US.SESSION_CALENDAR.V1",
    "RULE.CRYPTO.TAXONOMY.ADD_20260914",
    "RULE.ROTATION.CRYPTO.V1",
    "RULE.ROTATION.US.V1P",
    "RULE.ROTATION.KR.V1T",
    "RULE.ROTATION.COMMON_T1T2_NEUTRAL.V1",
    "RULE.ROTATION.RELEASE_HANDLING.V1",
    "RULE.GOVERNANCE.EVIDENCE_GATED.V1",
)
STATUSES = ("RATIFIED", "PROVISIONAL", "TEMPORARY")
SCORECARD_FAMILIES = (
    "entry", "exit", "allocation", "hedge", "gate", "liquidity", "data-source", "governance",
)
SOURCE_ROLES = ("USER_RATIFICATION", "CIO_ADDENDUM")
MARKETS = ("US", "KR", "CRYPTO")
MODES = ("PAPER", "REAL")
MATCH_KINDS = ("EXACT", "PARSED_FROM_TEXT")
EFFECTIVE_BASES = ("UTC_EXACT", "KST_MINUTE_TO_UTC")
EVIDENCE_LEVELS = (
    "INITIAL_DEFAULT_UNVALIDATED",
    "PROVISIONAL_FORWARD_ACCEPTANCE",
    "ESTIMATE_ONLY",
    "NOT_STATED_IN_RECORD",
)

RULE_ID_RE = re.compile(r"^RULE\.[A-Z0-9_]+(\.[A-Z0-9_]+)+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
KST_MINUTE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})\+09:00$")

TOP_FIELDS = {
    "schema_version", "registry_id", "description", "scope_note",
    "status_vocabulary", "scorecard_metric_families", "rules",
}
ROW_FIELDS = {
    "rule_id", "version", "lineage_key", "status", "title_ko", "markets", "modes",
    "source_records", "effective_from", "key_parameters",
    "evidence_level_at_decision", "review_triggers",
    "trigger_pending_user_confirmation", "scorecard_metric_family",
    "minimum_sample", "supersedes", "implementation_bindings",
}
SOURCE_FIELDS = {"role", "record_id", "repo_path", "original_filename", "sha256", "bytes"}
# Every sourced item names a source record index, a JSON pointer into it and a
# match kind.  EXACT: ``value`` equals the record value and ``text`` is null.
# PARSED_FROM_TEXT: ``text`` is a verbatim substring of the record string and
# ``value`` is its machine-readable transcription.
POINTER_FIELDS = {"source", "record_pointer", "match", "text"}
PARAM_FIELDS = POINTER_FIELDS | {"value"}
EFFECTIVE_FIELDS = {"source", "record_pointer", "utc", "basis"}
EVIDENCE_FIELDS = POINTER_FIELDS | {"level"}
TRIGGER_FIELDS = POINTER_FIELDS | {"trigger_id", "condition"}
SAMPLE_FIELDS = POINTER_FIELDS | {"value", "unit"}
SUPERSEDES_FIELDS = {"rule_id", "record_id", "sha256", "in_registry"}
BINDING_FIELDS = {"path", "binds_record_sha256"}


class RuleRegistryError(ValueError):
    """Fail-closed registry violation."""


def _fail(code: str, detail: str = "") -> None:
    raise RuleRegistryError(f"{code}:{detail}" if detail else code)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise RuleRegistryError(f"FILE_UNREADABLE:{path}") from exc


def resolve_pointer(document, pointer: str):
    """RFC 6901 JSON pointer resolution (fail-closed)."""
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        _fail("POINTER_INVALID", str(pointer))
    node = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                _fail("POINTER_UNRESOLVED", pointer)
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or int(token) >= len(node):
                _fail("POINTER_UNRESOLVED", pointer)
            node = node[int(token)]
        else:
            _fail("POINTER_UNRESOLVED", pointer)
    return node


def _closed(value, fields: set, code: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code, str(sorted(value) if isinstance(value, dict) else type(value).__name__))
    return value


def _record_id(record: dict):
    for key in ("ratification_id", "addendum_id", "id"):
        if isinstance(record.get(key), str):
            return record[key]
    return None


def _check_sourced(item: dict, records: list, rule_id: str, label: str, *, has_value: bool = True):
    source = item.get("source")
    if type(source) is not int or not 0 <= source < len(records):
        _fail("SOURCE_INDEX_INVALID", f"{rule_id}:{label}")
    target = resolve_pointer(records[source], item.get("record_pointer"))
    match = item.get("match")
    text = item.get("text")
    if match == "EXACT":
        if not has_value or text is not None or target != item.get("value"):
            _fail("PARAMETER_NOT_EQUAL_TO_RECORD", f"{rule_id}:{label}")
    elif match == "PARSED_FROM_TEXT":
        if not isinstance(target, str) or not isinstance(text, str) or not text or text not in target:
            _fail("PARAMETER_TEXT_NOT_IN_RECORD", f"{rule_id}:{label}")
    else:
        _fail("MATCH_KIND_INVALID", f"{rule_id}:{label}")


def _parse_utc(value: str, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code, str(value))
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def kst_minute_to_utc(value: str) -> str:
    match = KST_MINUTE_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        _fail("KST_MINUTE_INVALID", str(value))
    local = dt.datetime.strptime(f"{match.group(1)}T{match.group(2)}:{match.group(3)}", "%Y-%m-%dT%H:%M")
    return (local - dt.timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_row(row: dict, root: Path) -> list:
    _closed(row, ROW_FIELDS, "ROW_FIELDS_INVALID")
    rule_id = row["rule_id"]
    if not isinstance(rule_id, str) or RULE_ID_RE.fullmatch(rule_id) is None:
        _fail("RULE_ID_INVALID", str(rule_id))
    if type(row["version"]) is not int or row["version"] < 1:
        _fail("VERSION_INVALID", rule_id)
    if not isinstance(row["lineage_key"], str) or not rule_id.startswith(row["lineage_key"] + "."):
        _fail("LINEAGE_KEY_INVALID", rule_id)
    if row["status"] not in STATUSES:
        _fail("STATUS_INVALID", rule_id)
    if not isinstance(row["title_ko"], str) or not row["title_ko"]:
        _fail("TITLE_INVALID", rule_id)
    if not isinstance(row["markets"], list) or not row["markets"] or any(m not in MARKETS for m in row["markets"]) \
            or len(set(row["markets"])) != len(row["markets"]):
        _fail("MARKETS_INVALID", rule_id)
    modes = row["modes"]
    if not isinstance(modes, dict) or set(modes) != set(MODES) or any(
            not isinstance(v, str) or not v for v in modes.values()):
        _fail("MODES_INVALID", rule_id)

    sources = row["source_records"]
    if not isinstance(sources, list) or not sources:
        _fail("RULE_WITHOUT_SOURCE_RECORD", rule_id)
    records = []
    ratification_shas = set()
    for index, source in enumerate(sources):
        _closed(source, SOURCE_FIELDS, "SOURCE_FIELDS_INVALID")
        if source["role"] not in SOURCE_ROLES:
            _fail("SOURCE_ROLE_INVALID", rule_id)
        repo_path = source["repo_path"]
        if not isinstance(repo_path, str) or not repo_path.startswith(AUTHORITY_DIR) or ".." in repo_path:
            _fail("SOURCE_PATH_INVALID", f"{rule_id}:{repo_path}")
        path = root / repo_path
        if not path.is_file():
            _fail("SOURCE_RECORD_MISSING", f"{rule_id}:{repo_path}")
        raw = path.read_bytes()
        if not isinstance(source["sha256"], str) or SHA256_RE.fullmatch(source["sha256"]) is None:
            _fail("SOURCE_SHA_INVALID", rule_id)
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            _fail("SOURCE_RECORD_HASH_MISMATCH", f"{rule_id}:{repo_path}")
        if len(raw) != source["bytes"]:
            _fail("SOURCE_RECORD_SIZE_MISMATCH", f"{rule_id}:{repo_path}")
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuleRegistryError(f"SOURCE_RECORD_INVALID_JSON:{rule_id}:{repo_path}") from exc
        if not isinstance(record, dict) or _record_id(record) != source["record_id"]:
            _fail("SOURCE_RECORD_ID_MISMATCH", f"{rule_id}:{repo_path}")
        if not isinstance(source["original_filename"], str) or not source["original_filename"].endswith(".json"):
            _fail("SOURCE_ORIGINAL_FILENAME_INVALID", rule_id)
        if source["role"] == "USER_RATIFICATION":
            ratification_shas.add(source["sha256"])
        records.append((source, raw, record))
    if sources[0]["role"] != "USER_RATIFICATION" or not ratification_shas:
        _fail("PRIMARY_USER_RATIFICATION_REQUIRED", rule_id)
    for source, raw, _record in records:
        if source["role"] == "CIO_ADDENDUM" and not any(
                sha.encode("ascii") in raw for sha in ratification_shas):
            _fail("ADDENDUM_NOT_BOUND_TO_RATIFICATION", f"{rule_id}:{source['repo_path']}")
    documents = [record for _s, _r, record in records]

    effective = _closed(row["effective_from"], EFFECTIVE_FIELDS, "EFFECTIVE_FIELDS_INVALID")
    _parse_utc(effective["utc"], "EFFECTIVE_UTC_INVALID")
    if effective["basis"] not in EFFECTIVE_BASES:
        _fail("EFFECTIVE_BASIS_INVALID", rule_id)
    source_value = resolve_pointer(documents[_source_index(effective, documents, rule_id)], effective["record_pointer"])
    expected = source_value if effective["basis"] == "UTC_EXACT" else kst_minute_to_utc(source_value)
    if expected != effective["utc"]:
        _fail("EFFECTIVE_FROM_NOT_FROM_RECORD", rule_id)

    params = row["key_parameters"]
    if not isinstance(params, dict) or not params:
        _fail("KEY_PARAMETERS_MISSING", rule_id)
    for name, item in params.items():
        _closed(item, PARAM_FIELDS, "PARAMETER_FIELDS_INVALID")
        _check_sourced(item, documents, rule_id, f"key_parameters.{name}")

    evidence = row["evidence_level_at_decision"]
    if not isinstance(evidence, dict) or evidence.get("level") not in EVIDENCE_LEVELS:
        _fail("EVIDENCE_LEVEL_INVALID", rule_id)
    if evidence["level"] == "NOT_STATED_IN_RECORD":
        if set(evidence) != {"level"}:
            _fail("EVIDENCE_LEVEL_INVALID", rule_id)
    else:
        _closed(evidence, EVIDENCE_FIELDS, "EVIDENCE_LEVEL_INVALID")
        _check_sourced(evidence, documents, rule_id, "evidence_level_at_decision", has_value=False)

    triggers = row["review_triggers"]
    pending = row["trigger_pending_user_confirmation"]
    if type(pending) is not bool:
        _fail("TRIGGER_PENDING_FLAG_INVALID", rule_id)
    if triggers is None:
        if pending is not True:
            _fail("TRIGGER_NULL_REQUIRES_PENDING_TRUE", rule_id)
    else:
        if pending is not False or not isinstance(triggers, list) or not triggers:
            _fail("TRIGGERS_INVALID", rule_id)
        seen = set()
        for item in triggers:
            _closed(item, TRIGGER_FIELDS, "TRIGGER_FIELDS_INVALID")
            if not isinstance(item["trigger_id"], str) or item["trigger_id"] in seen:
                _fail("TRIGGER_ID_INVALID", rule_id)
            seen.add(item["trigger_id"])
            if not isinstance(item["condition"], str) or not item["condition"]:
                _fail("TRIGGER_CONDITION_INVALID", rule_id)
            _check_sourced(item, documents, rule_id, f"review_triggers.{item['trigger_id']}", has_value=False)

    if row["scorecard_metric_family"] not in SCORECARD_FAMILIES:
        _fail("SCORECARD_FAMILY_INVALID", rule_id)

    sample = row["minimum_sample"]
    if sample is not None:
        _closed(sample, SAMPLE_FIELDS, "MINIMUM_SAMPLE_INVALID")
        if type(sample["value"]) is not int or sample["value"] < 1 or not isinstance(sample["unit"], str):
            _fail("MINIMUM_SAMPLE_INVALID", rule_id)
        if sample["match"] != "PARSED_FROM_TEXT":
            _fail("MINIMUM_SAMPLE_INVALID", rule_id)
        _check_sourced(sample, documents, rule_id, "minimum_sample", has_value=False)

    supersedes = row["supersedes"]
    if supersedes is not None:
        _closed(supersedes, SUPERSEDES_FIELDS, "SUPERSEDES_INVALID")
        if type(supersedes["in_registry"]) is not bool or not isinstance(supersedes["record_id"], str) \
                or not isinstance(supersedes["sha256"], str) or SHA256_RE.fullmatch(supersedes["sha256"]) is None:
            _fail("SUPERSEDES_INVALID", rule_id)
        if supersedes["in_registry"] is (supersedes["rule_id"] is None):
            _fail("SUPERSEDES_RULE_ID_INVALID", rule_id)
        # The superseded record must be named by hash inside the primary record.
        if supersedes["sha256"].encode("ascii") not in records[0][1]:
            _fail("SUPERSEDES_NOT_NAMED_BY_PRIMARY_RECORD", rule_id)

    bindings = row["implementation_bindings"]
    if not isinstance(bindings, list):
        _fail("BINDINGS_INVALID", rule_id)
    for binding in bindings:
        _closed(binding, BINDING_FIELDS, "BINDING_FIELDS_INVALID")
        path = root / binding["path"]
        if ".." in binding["path"] or not path.is_file():
            _fail("BINDING_PATH_MISSING", f"{rule_id}:{binding['path']}")
        if type(binding["binds_record_sha256"]) is not bool:
            _fail("BINDING_FLAG_INVALID", rule_id)
        if binding["binds_record_sha256"] and not any(
                sha.encode("ascii") in path.read_bytes() for sha in ratification_shas):
            _fail("BINDING_DOES_NOT_CONTAIN_RECORD_SHA", f"{rule_id}:{binding['path']}")
    return documents


def _source_index(item: dict, documents: list, rule_id: str) -> int:
    source = item.get("source")
    if type(source) is not int or not 0 <= source < len(documents):
        _fail("SOURCE_INDEX_INVALID", rule_id)
    return source


def validate_registry(registry: dict, root: Path = ROOT) -> dict:
    """Validate a parsed registry against committed records under ``root``."""
    _closed(registry, TOP_FIELDS, "REGISTRY_FIELDS_INVALID")
    if registry["schema_version"] != SCHEMA_VERSION:
        _fail("REGISTRY_SCHEMA_INVALID")
    if registry["status_vocabulary"] != list(STATUSES):
        _fail("STATUS_VOCABULARY_INVALID")
    if registry["scorecard_metric_families"] != list(SCORECARD_FAMILIES):
        _fail("SCORECARD_FAMILIES_INVALID")
    rows = registry["rules"]
    if not isinstance(rows, list) or not rows:
        _fail("RULES_MISSING")
    ids = [row.get("rule_id") if isinstance(row, dict) else None for row in rows]
    if len(ids) != len(set(ids)):
        _fail("RULE_ID_DUPLICATE", str(sorted({i for i in ids if ids.count(i) > 1})))
    if set(ids) != set(REQUIRED_RULE_IDS) or len(ids) != len(REQUIRED_RULE_IDS):
        _fail("RULE_ID_SET_MISMATCH", str(sorted(set(ids) ^ set(REQUIRED_RULE_IDS))))
    for row in rows:
        _validate_row(row, root)

    by_id = {row["rule_id"]: row for row in rows}
    lineages: dict = {}
    for row in rows:
        lineages.setdefault(row["lineage_key"], []).append(row)
    for key, members in lineages.items():
        ordered = sorted(members, key=lambda r: (r["effective_from"]["utc"], r["version"]))
        versions = [r["version"] for r in ordered]
        if len(set(versions)) != len(versions) or versions != sorted(versions):
            _fail("VERSIONS_NOT_MONOTONE", key)
    for row in rows:
        supersedes = row["supersedes"]
        if supersedes is None:
            continue
        if supersedes["in_registry"]:
            old = by_id.get(supersedes["rule_id"])
            if old is None:
                _fail("SUPERSEDED_RULE_MISSING", row["rule_id"])
            if old["lineage_key"] != row["lineage_key"] or old["version"] >= row["version"] \
                    or old["effective_from"]["utc"] >= row["effective_from"]["utc"]:
                _fail("SUPERSEDES_NOT_BACKWARD", row["rule_id"])
        elif supersedes["rule_id"] in by_id:
            _fail("SUPERSEDES_IN_REGISTRY_FLAG_WRONG", row["rule_id"])
    return copy.deepcopy(registry)


def load_registry(path: Path = REGISTRY_PATH, *, root: Path = ROOT) -> dict:
    try:
        registry = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuleRegistryError(f"REGISTRY_UNREADABLE:{path}") from exc
    return validate_registry(registry, root)


def registry_sha256(path: Path = REGISTRY_PATH) -> str:
    """Identity of the exact registry bytes every ``rule_refs`` entry names."""
    return file_sha256(path)


def rule_index(registry: dict) -> dict:
    return {row["rule_id"]: row for row in registry["rules"]}


def primary_record_sha256(row: dict) -> str:
    return row["source_records"][0]["sha256"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
    except RuleRegistryError as exc:
        print(f"FAIL_RULE_REGISTRY:{exc}")
        return 1
    print(json.dumps({
        "status": "PASS_RULE_REGISTRY_VALID",
        "registry_sha256": registry_sha256(args.registry),
        "rule_count": len(registry["rules"]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
