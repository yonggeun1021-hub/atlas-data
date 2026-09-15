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
* the record id inside each source record matches the registry row, and a
  record that names a rule id or a status for the row must agree with it;
* every machine-readable parameter, effective instant, evidence level, review
  trigger, minimum sample, amendment and pending basis points into the source
  record with a JSON pointer and is either equal to the record value
  (``EXACT``) or a verbatim substring of it (``PARSED_FROM_TEXT``);
* every ``PARSED_FROM_TEXT`` item (quote *and* its transcribed value /
  condition) is pinned by digest in ``config/rule_registry_v1_parsed_pins.json``
  whose own sha256 is pinned below, so a value drift under an unchanged quote
  fails;
* versions are positive integers, monotone along each ``lineage_key`` in
  effective order; ``supersedes`` / ``superseded_by`` / ``SUPERSEDED`` agree,
  and so do partial ``supersedes_parts`` / ``superseded_parts``;
  ``amends`` / ``amended_by`` agree; ``RESOLVED`` rows name decided resolvers;
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
PINS_RELATIVE_PATH = "config/rule_registry_v1_parsed_pins.json"
# Changing any parsed quote/value requires changing the pin file *and* this
# constant -- a deliberate two-place edit visible in review.
PARSED_PINS_SHA256 = "e6f72bacc3296639d496248b183db369e09ede5308f4c9f6fbcd70655b5c402c"
SCHEMA_VERSION = "atlas_rule_registry/1"
PINS_SCHEMA_VERSION = "atlas_rule_registry_parsed_pins/1"
AUTHORITY_DIR = "evidence/authority/"

# Fixed by CLAUDE_CIO for v1.  Adding/removing a rule is a registry change in
# the same review, never a silent edit.
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
    # Ratified 2026-09-15 07:10 / 07:22 KST (recorded_at_kst as corrected by CIO).
    "RULE.ENTRY.PAPER_BASELINE_B.V1",
    "RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1",
    "RULE.KR.FIRST_CYCLE_CANARY_V0.V1",
    "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V1",
    # User wording correction 2026-09-15 07:51 KST; V1 row kept as SUPERSEDED.
    "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2",
    # Data-failure priority C (07:52 KST) and provisional exits (07:57 KST).
    "RULE.EXEC.DATA_FAILURE_PRIORITY.V1",
    "RULE.EXIT.RELEASE_FULL_SELL.V1",
    "RULE.EXIT.CRYPTO_TIME_STOP_21D.V1",
    "RULE.EXIT.SHADOW_CONTROLS.V1",
    "RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1",
    "RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1",
    "RULE.RISK.NAV_DRAWDOWN_LIFT.V1",
    # Execution contract D1/D3/D5-D11 (08:01 KST).
    "RULE.EXEC.TIME_CONTRACT.V1",
    "RULE.EXEC.QUALITY_LAYERS.V1",
    "RULE.EXEC.ALLOCATION_REDUCTION.V1",
    "RULE.EXEC.MULTI_MARKET_REALLOCATION.V1",
    "RULE.EXEC.REENTRY.V1",
    "RULE.EXEC.TOPUP_POSITION_LEVEL.V1",
    "RULE.EXEC.MONITORED_STOP_FILL_MODEL.V1",
    "RULE.VALIDATION.MECHANICAL_ONLY.V1",
    "RULE.SCORECARD.SINGLE_CONTRACT.V1",
    # US liquidity data source (08:06 KST); resolves the last pending row.
    "RULE.LIQUIDITY.US_SIP_SOURCE.V1",
    # Rotation interpretation: observation-count confirmation and data gaps (08:13 KST).
    "RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1",
    # Build plan section 9 P1-P6 (2026-09-15T00:27:55Z) and the rotation
    # maximum observation gap numbers (00:28:45Z).
    "RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1",
    "RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1",
    "RULE.EXIT.SHADOW_CONTROLS_KR_US_UNITS.V1",
    "RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1",
    "RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1",
    "RULE.GOVERNANCE.COOLING_OFF.V1",
    "RULE.ROTATION.MAX_OBSERVATION_GAP.V1",
    # Crypto PAPER v2 operation (2026-09-15T08:15:22Z).
    "RULE.CRYPTO.PAPER_V2_LEDGER_GENESIS.V1",
    "RULE.CRYPTO.PAPER_V2_ORDER_TYPE.V1",
    "RULE.CRYPTO.PAPER_V2_TCUT.V1",
    "RULE.EXEC.REDUCTION_PACE_UNKNOWN_CAP_AND_DRAWDOWN.V1",
    "RULE.PORTAL.CRYPTO_PAPER_PROJECTION_V2.V1",
    # Items a ratification record explicitly left undecided.  They carry no
    # parameters and can never be cited in rule_refs; RESOLVED ones name the
    # ratified rows that later decided them.
    "RULE.SIZE.PLANNED_LOSS_CAP.PENDING",
    "RULE.EXIT.PENDING",
    "RULE.EXECUTION.QUALITY_NUMBERS.PENDING",
    "RULE.CRYPTO.BTC_ETH_NAME_CAP.PENDING",
    "RULE.US.LIQUIDITY_IEX_TREATMENT.PENDING",
)
PENDING_STATUS = "PENDING_USER_DECISION"
RESOLVED_STATUS = "RESOLVED"
SUPERSEDED_STATUS = "SUPERSEDED"
# SUPERSEDED stays decided: decisions made while it was in force keep citing it.
DECIDED_STATUSES = ("RATIFIED", "PROVISIONAL", "TEMPORARY", SUPERSEDED_STATUS)
UNDECIDED_STATUSES = (PENDING_STATUS, RESOLVED_STATUS)
STATUSES = DECIDED_STATUSES + UNDECIDED_STATUSES
# Record status vocabulary -> registry status the row may carry (a SUPERSEDED
# row may carry any decided record status).
RECORD_STATUS_TO_ROW = {
    "RATIFIED": "RATIFIED",
    "RATIFIED_AS_PAPER_BASELINE": "RATIFIED",
    "PROVISIONAL": "PROVISIONAL",
    "TEMPORARY": "TEMPORARY",
}
SCORECARD_FAMILIES = (
    "entry", "exit", "allocation", "hedge", "gate", "liquidity", "data-source", "governance",
)
SOURCE_ROLES = ("USER_RATIFICATION", "CIO_ADDENDUM")
MARKETS = ("US", "KR", "CRYPTO")
MODES = ("PAPER", "REAL")
MATCH_KINDS = ("EXACT", "PARSED_FROM_TEXT")
EFFECTIVE_BASES = ("UTC_EXACT", "KST_MINUTE_TO_UTC")
AMENDMENT_RELATIONS = ("NARROWS_SCOPE", "FILLS_CONDITION", "SPECIFIES_DATA_SOURCE", "INTERPRETS")
EVIDENCE_LEVELS = (
    "INITIAL_DEFAULT_UNVALIDATED",
    "PROVISIONAL_FORWARD_ACCEPTANCE",
    "ESTIMATE_ONLY",
    "BASELINE_NO_EDGE_CLAIM",
    "STUDY_LEVEL_C_NO_RETURN_EDGE",
    "SOURCE_ACCESS_PROBE",
    "NOT_STATED_IN_RECORD",
)

RULE_ID_RE = re.compile(r"^RULE\.[A-Z0-9_]+(\.[A-Z0-9_]+)+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
KST_MINUTE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})\+09:00$")
# A CIO timestamp correction keeps the decision and records the replaced file
# hash in ``correction_note``; later records may still name that earlier hash.
PREVIOUS_SHA_RE = re.compile(r"previous file sha256 ([0-9a-f]{64})(?![0-9a-f])")
# "<RECORD_ID or FILENAME.json> (<hex prefix>…)" citation of another record.
ABBREVIATED_CITATION_RE = re.compile(r"([A-Z][A-Z0-9_]{2,160}(?:\.json)?) \(([0-9a-f]{8,63})\u2026\)")

TOP_FIELDS = {
    "schema_version", "registry_id", "description", "scope_note",
    "status_vocabulary", "scorecard_metric_families", "rules",
}
ROW_FIELDS = {
    "rule_id", "version", "lineage_key", "status", "status_source", "title_ko", "markets", "modes",
    "source_records", "effective_from", "key_parameters",
    "evidence_level_at_decision", "review_triggers",
    "trigger_pending_user_confirmation", "scorecard_metric_family",
    "minimum_sample", "supersedes", "superseded_by", "supersedes_parts", "superseded_parts",
    "amends", "amended_by",
    "implementation_bindings", "pending_basis", "resolved_by",
}
SOURCE_FIELDS = {"role", "record_id", "repo_path", "original_filename", "sha256", "bytes"}
# Every sourced item names a source record index, a JSON pointer into it and a
# match kind.  EXACT: ``value`` equals the record value and ``text`` is null.
# PARSED_FROM_TEXT: ``text`` is a verbatim substring of the record string and
# ``value`` (or condition/level/unit) is its pinned machine-readable reading.
POINTER_FIELDS = {"source", "record_pointer", "match", "text"}
PARAM_FIELDS = POINTER_FIELDS | {"value"}
EFFECTIVE_FIELDS = {"source", "record_pointer", "utc", "basis"}
EVIDENCE_FIELDS = POINTER_FIELDS | {"level"}
TRIGGER_FIELDS = POINTER_FIELDS | {"trigger_id", "condition"}
SAMPLE_FIELDS = POINTER_FIELDS | {"value", "unit"}
STATUS_SOURCE_FIELDS = {"source", "record_pointer", "record_value"}
SUPERSEDES_FIELDS = {"rule_id", "record_id", "sha256", "in_registry"}
SUPERSEDED_BY_FIELDS = {"rule_id", "record_id", "sha256"}
AMENDS_FIELDS = POINTER_FIELDS | {"rule_id", "relation", "sha256"}
SUPERSEDES_PART_FIELDS = {"rule_id", "key_parameter", "record_id", "sha256"}
SUPERSEDED_PART_FIELDS = {"key_parameter", "superseded_by"}
AMENDED_BY_FIELDS = {"rule_id", "sha256"}
RESOLVED_BY_FIELDS = {"rule_id", "sha256"}
BINDING_FIELDS = {"path", "binds_record_sha256"}
PENDING_BASIS_FIELDS = POINTER_FIELDS


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


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


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


def record_identities(raw: bytes, record: dict) -> set:
    """Current file sha256 plus earlier hashes named by its correction_note."""
    identities = {hashlib.sha256(raw).hexdigest()}
    note = record.get("correction_note") if isinstance(record, dict) else None
    if isinstance(note, str):
        # Only full 64-hex hashes written in THIS record's own correction note.
        identities.update(h for h in PREVIOUS_SHA_RE.findall(note) if SHA256_RE.fullmatch(h))
    return identities


def _source_identities(root: Path, source: dict) -> set:
    path = Path(root) / source["repo_path"]
    raw = path.read_bytes()
    return record_identities(raw, json.loads(raw.decode("utf-8")))


def _named_status_pointers(record: dict, rule_id: str) -> list:
    """Pointers to ``status`` fields of record nodes that name ``rule_id``."""
    found = []

    def walk(node, pointer):
        if isinstance(node, dict):
            if pointer and node.get("rule_id") == rule_id and "status" in node:
                found.append(f"{pointer}/status")
            for key, child in node.items():
                child_pointer = f"{pointer}/{_escape(key)}"
                if key == rule_id and isinstance(child, dict) and "status" in child:
                    found.append(f"{child_pointer}/status")
                walk(child, child_pointer)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{pointer}/{index}")

    walk(record, "")
    return sorted(set(found))


def parsed_items(registry: dict) -> dict:
    """Every PARSED_FROM_TEXT item keyed ``<rule_id>#<location>``."""
    items = {}
    for row in registry.get("rules") or []:
        if not isinstance(row, dict):
            continue
        rule_id = row.get("rule_id")
        groups = []
        for name, item in (row.get("key_parameters") or {}).items():
            groups.append((f"key_parameters.{name}", item))
        evidence = row.get("evidence_level_at_decision")
        if isinstance(evidence, dict) and "match" in evidence:
            groups.append(("evidence_level_at_decision", evidence))
        for item in row.get("review_triggers") or []:
            groups.append((f"review_triggers.{item.get('trigger_id') if isinstance(item, dict) else None}", item))
        if isinstance(row.get("minimum_sample"), dict):
            groups.append(("minimum_sample", row["minimum_sample"]))
        for index, item in enumerate(row.get("pending_basis") or []):
            groups.append((f"pending_basis.{index}", item))
        for item in row.get("amends") or []:
            groups.append((f"amends.{item.get('rule_id') if isinstance(item, dict) else None}", item))
        for location, item in groups:
            if isinstance(item, dict) and item.get("match") == "PARSED_FROM_TEXT":
                items[f"{rule_id}#{location}"] = item
    return items


def build_parsed_pins(registry: dict) -> dict:
    return {
        "schema_version": PINS_SCHEMA_VERSION,
        "note": (
            "sha256 of the canonical JSON of each PARSED_FROM_TEXT item (quote plus its transcribed "
            "value, condition, level, unit or relation). Regenerate only together with a reviewed "
            "registry change and update PARSED_PINS_SHA256 in governance/rule_registry.py."
        ),
        "pins": {key: payload_sha256(item) for key, item in sorted(parsed_items(registry).items())},
    }


def _validate_parsed_pins(registry: dict, root: Path) -> None:
    path = Path(root) / PINS_RELATIVE_PATH
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuleRegistryError(f"PARSED_PINS_MISSING:{path}") from exc
    if hashlib.sha256(raw).hexdigest() != PARSED_PINS_SHA256:
        _fail("PARSED_PINS_FILE_HASH_MISMATCH")
    try:
        pins = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuleRegistryError("PARSED_PINS_INVALID_JSON") from exc
    if not isinstance(pins, dict) or pins.get("schema_version") != PINS_SCHEMA_VERSION \
            or not isinstance(pins.get("pins"), dict):
        _fail("PARSED_PINS_SCHEMA_INVALID")
    items = parsed_items(registry)
    if set(items) != set(pins["pins"]):
        _fail("PARSED_PINS_KEY_SET_MISMATCH", str(sorted(set(items) ^ set(pins["pins"]))))
    for key, item in sorted(items.items()):
        if payload_sha256(item) != pins["pins"][key]:
            _fail("PARSED_VALUE_PIN_MISMATCH", key)


def _validate_row(row: dict, root: Path) -> list:
    _closed(row, ROW_FIELDS, "ROW_FIELDS_INVALID")
    rule_id = row["rule_id"]
    if not isinstance(rule_id, str) or RULE_ID_RE.fullmatch(rule_id) is None:
        _fail("RULE_ID_INVALID", str(rule_id))
    if row["status"] not in STATUSES:
        _fail("STATUS_INVALID", rule_id)
    undecided = row["status"] in UNDECIDED_STATUSES
    if type(row["version"]) is not int or (undecided and row["version"] != 0) \
            or (not undecided and row["version"] < 1):
        _fail("VERSION_INVALID", rule_id)
    if not isinstance(row["lineage_key"], str) or not rule_id.startswith(row["lineage_key"] + "."):
        _fail("LINEAGE_KEY_INVALID", rule_id)
    if not isinstance(row["title_ko"], str) or not row["title_ko"]:
        _fail("TITLE_INVALID", rule_id)
    if not isinstance(row["markets"], list) or not row["markets"] or any(m not in MARKETS for m in row["markets"]) \
            or len(set(row["markets"])) != len(row["markets"]):
        _fail("MARKETS_INVALID", rule_id)
    modes = row["modes"]
    if not isinstance(modes, dict) or set(modes) != set(MODES) or any(
            not isinstance(v, str) or not v for v in modes.values()):
        _fail("MODES_INVALID", rule_id)
    if row["scorecard_metric_family"] not in SCORECARD_FAMILIES:
        _fail("SCORECARD_FAMILY_INVALID", rule_id)

    sources = row["source_records"]
    if not isinstance(sources, list) or not sources:
        _fail("RULE_WITHOUT_SOURCE_RECORD", rule_id)
    records = []
    ratification_shas = set()
    ratification_identities = set()
    for source in sources:
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
            ratification_identities.update(record_identities(raw, record))
        records.append((source, raw, record))
    if sources[0]["role"] != "USER_RATIFICATION" or not ratification_shas:
        _fail("PRIMARY_USER_RATIFICATION_REQUIRED", rule_id)
    for source, raw, _record in records:
        if source["role"] == "CIO_ADDENDUM" and not any(
                sha.encode("ascii") in raw for sha in ratification_shas):
            _fail("ADDENDUM_NOT_BOUND_TO_RATIFICATION", f"{rule_id}:{source['repo_path']}")
    documents = [record for _s, _r, record in records]

    if undecided:
        _validate_undecided_row(row, documents)
        return documents
    if row["pending_basis"] is not None or row["resolved_by"] is not None:
        _fail("PENDING_FIELDS_ONLY_FOR_UNDECIDED_ROWS", rule_id)
    named_rule = documents[0].get("rule_id")
    if named_rule is not None and named_rule != rule_id:
        _fail("RECORD_NAMES_A_DIFFERENT_RULE_ID", rule_id)

    # Row status must agree with any status the primary record gives this rule.
    status_source = row["status_source"]
    named_pointers = _named_status_pointers(documents[0], rule_id)
    if status_source is None:
        if named_pointers:
            _fail("STATUS_SOURCE_REQUIRED", f"{rule_id}:{named_pointers}")
    else:
        _closed(status_source, STATUS_SOURCE_FIELDS, "STATUS_SOURCE_INVALID")
        index = _source_index(status_source, documents, rule_id)
        if named_pointers and (index != 0 or status_source["record_pointer"] not in named_pointers):
            _fail("STATUS_SOURCE_POINTER_MISMATCH", rule_id)
        record_value = resolve_pointer(documents[index], status_source["record_pointer"])
        if record_value != status_source["record_value"] or record_value not in RECORD_STATUS_TO_ROW:
            _fail("STATUS_SOURCE_VALUE_MISMATCH", rule_id)
        if row["status"] != SUPERSEDED_STATUS and RECORD_STATUS_TO_ROW[record_value] != row["status"]:
            _fail("ROW_STATUS_DISAGREES_WITH_RECORD", f"{rule_id}:{record_value}")

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
        if evidence["match"] != "PARSED_FROM_TEXT":
            _fail("EVIDENCE_LEVEL_INVALID", rule_id)
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
            if not isinstance(item["condition"], str) or not item["condition"] or item["match"] != "PARSED_FROM_TEXT":
                _fail("TRIGGER_CONDITION_INVALID", rule_id)
            _check_sourced(item, documents, rule_id, f"review_triggers.{item['trigger_id']}", has_value=False)

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
        # Out-of-registry predecessors must be named by hash inside the primary
        # record; in-registry ones are checked against their identities below.
        if not supersedes["in_registry"] and supersedes["sha256"].encode("ascii") not in records[0][1]:
            _fail("SUPERSEDES_NOT_NAMED_BY_PRIMARY_RECORD", rule_id)

    amends = row["amends"]
    if amends is not None:
        if not isinstance(amends, list) or not amends:
            _fail("AMENDS_INVALID", rule_id)
        for item in amends:
            _closed(item, AMENDS_FIELDS, "AMENDS_FIELDS_INVALID")
            if item["relation"] not in AMENDMENT_RELATIONS or item["match"] != "PARSED_FROM_TEXT" \
                    or not isinstance(item["sha256"], str) or SHA256_RE.fullmatch(item["sha256"]) is None:
                _fail("AMENDS_INVALID", rule_id)
            _check_sourced(item, documents, rule_id, f"amends.{item['rule_id']}", has_value=False)

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
                sha.encode("ascii") in path.read_bytes() for sha in ratification_identities):
            _fail("BINDING_DOES_NOT_CONTAIN_RECORD_SHA", f"{rule_id}:{binding['path']}")
    return documents


def _validate_undecided_row(row: dict, documents: list) -> None:
    """A not-decided item: sourced, parameter-free, never effective."""
    rule_id = row["rule_id"]
    if not rule_id.endswith(".PENDING"):
        _fail("PENDING_RULE_ID_MUST_END_WITH_PENDING", rule_id)
    if row["key_parameters"] != {} or row["effective_from"] is not None or row["status_source"] is not None \
            or row["evidence_level_at_decision"] is not None or row["review_triggers"] is not None \
            or row["trigger_pending_user_confirmation"] is not True or row["minimum_sample"] is not None \
            or row["supersedes"] is not None or row["superseded_by"] is not None \
            or row["supersedes_parts"] is not None or row["superseded_parts"] is not None \
            or row["amends"] is not None or row["amended_by"] is not None \
            or row["implementation_bindings"] != []:
        _fail("PENDING_ROW_MUST_CARRY_NO_DECISION", rule_id)
    basis = row["pending_basis"]
    if not isinstance(basis, list) or not basis:
        _fail("PENDING_BASIS_REQUIRED", rule_id)
    for index, item in enumerate(basis):
        _closed(item, PENDING_BASIS_FIELDS, "PENDING_BASIS_FIELDS_INVALID")
        if item["match"] != "PARSED_FROM_TEXT":
            _fail("PENDING_BASIS_INVALID", rule_id)
        _check_sourced(item, documents, rule_id, f"pending_basis.{index}", has_value=False)
    resolved_by = row["resolved_by"]
    if row["status"] == PENDING_STATUS:
        if resolved_by is not None:
            _fail("PENDING_ROW_HAS_RESOLVER", rule_id)
    elif not isinstance(resolved_by, list) or not resolved_by:
        _fail("RESOLVED_ROW_REQUIRES_RESOLVER", rule_id)
    else:
        for item in resolved_by:
            _closed(item, RESOLVED_BY_FIELDS, "RESOLVED_BY_FIELDS_INVALID")


def _source_index(item: dict, documents: list, rule_id: str) -> int:
    source = item.get("source")
    if type(source) is not int or not 0 <= source < len(documents):
        _fail("SOURCE_INDEX_INVALID", rule_id)
    return source


def _primary_names_any(root: Path, row: dict, identities: set, target: dict | None = None) -> bool:
    """Whether the row's primary record names one of the target record identities.

    A full 64-hex hash anywhere in the record bytes names it.  An abbreviated
    citation ``<record id or original filename> (<8+ hex prefix>\u2026)`` also
    names it, but only when the cited name is the target's own record id or
    original filename and the prefix starts one of its identities -- later
    ratification records cite earlier ones that way (2026-09-15 P1-P6 and
    max observation gap records).
    """
    raw = (Path(root) / row["source_records"][0]["repo_path"]).read_bytes()
    if any(identity.encode("ascii") in raw for identity in identities):
        return True
    if target is None:
        return False
    names = {target["record_id"], target["original_filename"]}
    text = raw.decode("utf-8")
    for name, prefix in ABBREVIATED_CITATION_RE.findall(text):
        if name in names and any(identity.startswith(prefix) for identity in identities):
            return True
    return False


def validate_registry(registry: dict, root: Path = ROOT) -> dict:
    """Validate a parsed registry against committed records and pins under ``root``."""
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

    def decided(rule_id):
        row = by_id.get(rule_id)
        return row is not None and row["status"] in DECIDED_STATUSES

    lineages: dict = {}
    for row in rows:
        if row["status"] in DECIDED_STATUSES:
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
        if not supersedes["in_registry"]:
            if supersedes["rule_id"] in by_id:
                _fail("SUPERSEDES_IN_REGISTRY_FLAG_WRONG", row["rule_id"])
            continue
        old = by_id.get(supersedes["rule_id"])
        if old is None or not decided(old["rule_id"]) or old["rule_id"] == row["rule_id"]:
            _fail("SUPERSEDED_RULE_INVALID", row["rule_id"])
        if old["effective_from"]["utc"] >= row["effective_from"]["utc"]:
            _fail("SUPERSEDES_NOT_BACKWARD", row["rule_id"])
        if old["lineage_key"] == row["lineage_key"] and old["version"] >= row["version"]:
            _fail("SUPERSEDES_NOT_BACKWARD", row["rule_id"])
        if old["status"] != SUPERSEDED_STATUS:
            _fail("SUPERSEDED_RULE_NOT_MARKED", row["rule_id"])
        primary = old["source_records"][0]
        if supersedes["sha256"] != primary["sha256"] or supersedes["record_id"] != primary["record_id"]:
            _fail("SUPERSEDES_RECORD_MISMATCH", row["rule_id"])
        if not _primary_names_any(root, row, _source_identities(root, primary), primary):
            _fail("SUPERSEDES_NOT_NAMED_BY_PRIMARY_RECORD", row["rule_id"])
    # SUPERSEDED <-> superseded_by <-> successor.supersedes must agree exactly.
    for row in rows:
        pointer = row["superseded_by"]
        if (row["status"] == SUPERSEDED_STATUS) != (pointer is not None):
            _fail("SUPERSEDED_STATUS_POINTER_MISMATCH", row["rule_id"])
        if pointer is None:
            continue
        _closed(pointer, SUPERSEDED_BY_FIELDS, "SUPERSEDED_BY_INVALID")
        successor = by_id.get(pointer["rule_id"])
        if successor is None or successor["status"] not in DECIDED_STATUSES or successor["status"] == SUPERSEDED_STATUS:
            _fail("SUPERSEDED_BY_SUCCESSOR_INVALID", row["rule_id"])
        back = successor["supersedes"] or {}
        if back.get("rule_id") != row["rule_id"] or back.get("in_registry") is not True:
            _fail("SUCCESSOR_DOES_NOT_SUPERSEDE", row["rule_id"])
        primary = successor["source_records"][0]
        if pointer["record_id"] != primary["record_id"] or pointer["sha256"] != primary["sha256"]:
            _fail("SUPERSEDED_BY_RECORD_MISMATCH", row["rule_id"])

    # Partial supersession: successor.supersedes_parts <-> target.superseded_parts.
    forward_parts = set()
    for row in rows:
        items = row["supersedes_parts"]
        if items is None:
            continue
        if not isinstance(items, list) or not items:
            _fail("SUPERSEDES_PARTS_INVALID", row["rule_id"])
        for item in items:
            _closed(item, SUPERSEDES_PART_FIELDS, "SUPERSEDES_PARTS_INVALID")
            target = by_id.get(item["rule_id"])
            if target is None or not decided(item["rule_id"]) or target["status"] == SUPERSEDED_STATUS \
                    or item["rule_id"] == row["rule_id"]:
                _fail("PART_SUPERSEDED_RULE_INVALID", row["rule_id"])
            if item["key_parameter"] not in target["key_parameters"]:
                _fail("PART_SUPERSEDED_PARAMETER_MISSING", row["rule_id"])
            primary = target["source_records"][0]
            if item["sha256"] != primary["sha256"] or item["record_id"] != primary["record_id"]:
                _fail("SUPERSEDES_PARTS_RECORD_MISMATCH", row["rule_id"])
            if not _primary_names_any(root, row, _source_identities(root, primary), primary):
                _fail("SUPERSEDES_PARTS_NOT_NAMED_BY_PRIMARY_RECORD", row["rule_id"])
            if target["effective_from"]["utc"] >= row["effective_from"]["utc"]:
                _fail("SUPERSEDES_PARTS_NOT_BACKWARD", row["rule_id"])
            forward_parts.add((item["rule_id"], item["key_parameter"], row["rule_id"], row["source_records"][0]["sha256"]))
    backward_parts = set()
    for row in rows:
        items = row["superseded_parts"]
        if items is None:
            continue
        if row["status"] == SUPERSEDED_STATUS or not isinstance(items, list) or not items:
            _fail("SUPERSEDED_PARTS_INVALID", row["rule_id"])
        for item in items:
            _closed(item, SUPERSEDED_PART_FIELDS, "SUPERSEDED_PARTS_INVALID")
            pointer = _closed(item["superseded_by"], SUPERSEDED_BY_FIELDS, "SUPERSEDED_PARTS_INVALID")
            successor = by_id.get(pointer["rule_id"])
            if successor is None or pointer["sha256"] != successor["source_records"][0]["sha256"] \
                    or pointer["record_id"] != successor["source_records"][0]["record_id"]:
                _fail("SUPERSEDED_PARTS_RECORD_MISMATCH", row["rule_id"])
            backward_parts.add((row["rule_id"], item["key_parameter"], pointer["rule_id"], pointer["sha256"]))
    if forward_parts != backward_parts:
        _fail("SUPERSEDES_PARTS_MISMATCH", str(sorted(forward_parts ^ backward_parts)))

    # amends (on the amending row) <-> amended_by (on the amended row).
    forward = set()
    for row in rows:
        for item in row["amends"] or []:
            target = by_id.get(item["rule_id"])
            if target is None or not decided(item["rule_id"]) or item["rule_id"] == row["rule_id"]:
                _fail("AMENDED_RULE_INVALID", row["rule_id"])
            target_source = next((s for s in target["source_records"] if s["sha256"] == item["sha256"]), None)
            if target_source is None:
                _fail("AMENDED_RECORD_NOT_A_SOURCE_OF_TARGET", row["rule_id"])
            if not _primary_names_any(root, row, _source_identities(root, target_source), target_source):
                _fail("AMENDED_RECORD_NOT_NAMED_BY_PRIMARY_RECORD", row["rule_id"])
            if target["effective_from"]["utc"] >= row["effective_from"]["utc"]:
                _fail("AMENDS_NOT_BACKWARD", row["rule_id"])
            forward.add((item["rule_id"], row["rule_id"], row["source_records"][0]["sha256"]))
    backward = set()
    for row in rows:
        amended_by = row["amended_by"]
        if amended_by is None:
            continue
        if not isinstance(amended_by, list) or not amended_by:
            _fail("AMENDED_BY_INVALID", row["rule_id"])
        for item in amended_by:
            _closed(item, AMENDED_BY_FIELDS, "AMENDED_BY_INVALID")
            backward.add((row["rule_id"], item["rule_id"], item["sha256"]))
    if forward != backward:
        _fail("AMENDS_AMENDED_BY_MISMATCH", str(sorted(forward ^ backward)))

    for row in rows:
        for item in row["resolved_by"] or []:
            resolver = by_id.get(item["rule_id"])
            if resolver is None or resolver["status"] not in DECIDED_STATUSES:
                _fail("RESOLVER_NOT_DECIDED", row["rule_id"])
            if item["sha256"] != resolver["source_records"][0]["sha256"]:
                _fail("RESOLVER_RECORD_MISMATCH", row["rule_id"])
    # Last: structural errors above report their own codes first.
    _validate_parsed_pins(registry, root)
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


def is_decided(row: dict) -> bool:
    return row["status"] in DECIDED_STATUSES


def in_force_at(row: dict, timestamp_utc: str, registry: dict) -> bool:
    """Whether a decided rule governed decisions at ``timestamp_utc``.

    From its own effective instant until its successor's effective instant.
    """
    if not is_decided(row) or row["effective_from"] is None or timestamp_utc < row["effective_from"]["utc"]:
        return False
    pointer = row["superseded_by"]
    if pointer is None:
        return True
    successor = rule_index(registry)[pointer["rule_id"]]
    return timestamp_utc < successor["effective_from"]["utc"]


def part_in_force_at(row: dict, key_parameter: str, timestamp_utc: str, registry: dict) -> bool:
    """Whether one key parameter of a rule governed decisions at ``timestamp_utc``."""
    if not in_force_at(row, timestamp_utc, registry) or key_parameter not in row["key_parameters"]:
        return False
    for item in row["superseded_parts"] or []:
        if item["key_parameter"] == key_parameter:
            successor = rule_index(registry)[item["superseded_by"]["rule_id"]]
            return timestamp_utc < successor["effective_from"]["utc"]
    return True


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
