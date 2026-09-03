#!/usr/bin/env python3
"""Exact P4-02 retained TSM monthly revenue -> P5-03 link-only binding.

The adapter freezes two registered observations against RULE-0007/RULE-0008.
It never evaluates a threshold or creates a Rule result, candidate, Stage,
Action, Order, or trading authority.
"""
from __future__ import annotations

import argparse
import calendar
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge import rule_evidence_binding as BIND  # noqa: E402
from discovery import official_release_observation as SOURCE  # noqa: E402
from collectors import c4_sec_edgar_check as C4  # noqa: E402
from collectors import sec_filing_content as SEC  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "tsm_sec_monthly_rule_evidence_contract.json"
RULES_PATH = ROOT / "config" / "rules.json"
DEFAULT_DATA_ROOT = ROOT / "data"
DEFAULT_OBSERVATION_ROOT = ROOT / "data" / "observations" / "official_release_observations"
DEFAULT_OUT_ROOT = ROOT / "data" / "observations" / "rule_evidence_bindings"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


class TsmRuleEvidenceError(ValueError):
    """Fail-closed exact-binding violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> tuple[dict, bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TsmRuleEvidenceError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise TsmRuleEvidenceError(f"JSON_NOT_OBJECT:{path}")
    return value, raw


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value, _ = _read_json(path)
    expected_rules = {
        "RULE-0007": "e69be9bebbc9ed7e1487cf479aca3ba0ff6397d293f443d414fba2b56de8fe3e",
        "RULE-0008": "a1607620f9ced118e0d98f6a21b3ee5ae6b736d15b643bfe0939ca1590e0b31e",
    }
    expected_measurements = [
        {
            "measurement_identity": "TSMC consolidated net revenue monthly YoY",
            "published_value_field": "monthly_yoy_pct_published",
            "column_identity": "monthly_yoy",
            "unit": "pct",
        },
        {
            "measurement_identity": "TSMC consolidated net revenue cumulative YoY",
            "published_value_field": "cumulative_yoy_pct_published",
            "column_identity": "cumulative_yoy",
            "unit": "pct",
        },
    ]
    expected_authority = {
        "linkage_only": True,
        "source_ranking_authorized": False,
        "interpretation_authorized": False,
        "rule_evaluation_authorized": False,
        "stage_change_authorized": False,
        "buy_authorized": False,
        "action_generation_authorized": False,
        "order_generation_authorized": False,
        "real_authorized": False,
        "live_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if (
        value.get("schema_version") != 1
        or value.get("contract_version") != "tsm_sec_monthly_rule_evidence/1"
        or value.get("source_packet_schema") != SOURCE.SCHEMA_VERSION
        or value.get("source_contract") != "P4-02_RETAINED_SEC_6K_EXACT_BYTES"
        or value.get("source_packet_selection") != "LATEST_UNIQUE_CONTENT_ADDRESSED_PACKET"
        or value.get("subject") != "TSM"
        or value.get("source_id") != "P4-02_RETAINED_SEC_6K_EXACT_BYTES"
        or value.get("binding_set_id")
        != "P4-02_TSM_SEC_MONTHLY_REVENUE_TO_RULE-0007_RULE-0008_V1"
        or value.get("selection_mode") != "ALL_REQUIRED"
        or value.get("period_selection") != "LATEST_UNIQUE_ECONOMIC_PERIOD"
        or value.get("registered_rules") != expected_rules
        or value.get("registered_measurements") != expected_measurements
        or value.get("identity_retention") != {
            "full_submission": "URL_SHA_LINEAGE_ONLY_BODY_NOT_PRESERVED",
            "filing_index": "URL_SHA_LINEAGE_ONLY_BODY_NOT_PRESERVED",
            "primary_document_raw_cache": "REUSE_P4_02_STAGE_POLICY_UNCHANGED",
        }
        or value.get("authority") != expected_authority
    ):
        raise TsmRuleEvidenceError("CONTRACT_MISMATCH")
    return copy.deepcopy(value)


def _repo_ref(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return f"external_fixture/{Path(path).name}"


def _month_end(value: str) -> str:
    match = MONTH_RE.fullmatch(value or "")
    if not match:
        raise TsmRuleEvidenceError("ECONOMIC_PERIOD_INVALID")
    year, month = map(int, match.groups())
    if not 1 <= month <= 12:
        raise TsmRuleEvidenceError("ECONOMIC_PERIOD_INVALID")
    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def select_observation_packet(
    observation_root: Path, data_root: Path = DEFAULT_DATA_ROOT
) -> Path:
    candidates = []
    for path in sorted(Path(observation_root).glob("*/tsm-monthly-revenue-*.json")):
        value, _ = _read_json(path)
        if value.get("schema_version") != SOURCE.SCHEMA_VERSION:
            raise TsmRuleEvidenceError(f"SOURCE_PACKET_SCHEMA_MISMATCH:{path}")
        evidence_as_of = value.get("evidence_as_of")
        if not isinstance(evidence_as_of, str):
            raise TsmRuleEvidenceError(f"SOURCE_PACKET_AS_OF_INVALID:{path}")
        # Historical content-addressed packets may remain after a stricter
        # independent validator supersedes them. They are audit records, not
        # eligible binding inputs. Selection is only among packets that still
        # rebuild exactly from their governed PIT bytes.
        try:
            SOURCE.validate_packet(copy.deepcopy(value), data_root=Path(data_root))
        except SOURCE.OfficialReleaseObservationError:
            continue
        candidates.append((evidence_as_of, path))
    if not candidates:
        raise TsmRuleEvidenceError("SOURCE_PACKET_MISSING")
    latest = max(item[0] for item in candidates)
    selected = [path for as_of, path in candidates if as_of == latest]
    if len(selected) != 1:
        raise TsmRuleEvidenceError("SOURCE_PACKET_LATEST_AMBIGUOUS")
    return selected[0]


def _validated_source_packet(path: Path, data_root: Path) -> tuple[dict, bytes]:
    packet, raw = _read_json(path)
    try:
        checked = SOURCE.validate_packet(copy.deepcopy(packet), data_root=Path(data_root))
    except SOURCE.OfficialReleaseObservationError as exc:
        raise TsmRuleEvidenceError(f"SOURCE_PACKET_INVALID:{exc}") from exc
    if checked.get("source_contract") != "P4-02_RETAINED_SEC_6K_EXACT_BYTES":
        raise TsmRuleEvidenceError("SOURCE_CONTRACT_MISMATCH")
    if checked.get("subject") != "TSM":
        raise TsmRuleEvidenceError("SOURCE_SUBJECT_MISMATCH")
    expected_name = f"tsm-monthly-revenue-{checked['packet_sha256'][:16]}.json"
    if Path(path).name != expected_name:
        raise TsmRuleEvidenceError("SOURCE_PACKET_CONTENT_ADDRESS_MISMATCH")
    return checked, raw


def _latest_observation(packet: dict) -> dict:
    rows = packet.get("observations")
    if not isinstance(rows, list) or not rows:
        raise TsmRuleEvidenceError("REGISTERED_OBSERVATION_MISSING")
    periods = [row.get("economic_period") if isinstance(row, dict) else None for row in rows]
    if any(MONTH_RE.fullmatch(period or "") is None for period in periods):
        raise TsmRuleEvidenceError("ECONOMIC_PERIOD_INVALID")
    latest = max(periods)
    selected = [row for row in rows if row.get("economic_period") == latest]
    if len(selected) != 1:
        raise TsmRuleEvidenceError("LATEST_ECONOMIC_PERIOD_AMBIGUOUS")
    row = selected[0]
    if (
        row.get("schema_version") != SOURCE.OBSERVATION_VERSION
        or row.get("status") != "OBSERVED"
        or row.get("subject") != "TSM"
        or row.get("measurement_set") != "TSMC_CONSOLIDATED_MONTHLY_REVENUE"
        or row.get("interpretation_status") != "UNDETERMINED"
        or row.get("rule_impact") != "NONE"
        or row.get("stage_change") is not None
        or row.get("trade_proposal") is not None
    ):
        raise TsmRuleEvidenceError("REGISTERED_OBSERVATION_BOUNDARY_MISMATCH")
    return copy.deepcopy(row)


def _manifest_and_raw(observation: dict, data_root: Path) -> tuple[dict, bytes, bytes]:
    lineage = observation.get("lineage")
    accession = lineage.get("accession") if isinstance(lineage, dict) else None
    if not isinstance(accession, str):
        raise TsmRuleEvidenceError("OBSERVATION_LINEAGE_INVALID")
    manifest_path = Path(data_root) / "sec_content" / "TSM" / accession / "_manifest.json"
    manifest, manifest_bytes = _read_json(manifest_path)
    if (
        hashlib.sha256(manifest_bytes).hexdigest() != lineage.get("manifest_sha256")
        or manifest.get("filing_identity", {}).get("accession") != accession
    ):
        raise TsmRuleEvidenceError("MANIFEST_IDENTITY_CONFLICT")
    documents = manifest.get("documents")
    primary = [row for row in documents or [] if isinstance(row, dict) and row.get("kind") == "primary"]
    if len(primary) != 1:
        raise TsmRuleEvidenceError("PRIMARY_DOCUMENT_CARDINALITY_INVALID")
    primary = primary[0]
    raw_path = manifest_path.parent / f"{primary.get('document_name')}.gz"
    try:
        raw = gzip.decompress(raw_path.read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise TsmRuleEvidenceError("PRIMARY_RAW_CACHE_INVALID") from exc
    try:
        checked = SEC.validate_manifest(copy.deepcopy(manifest), {primary["document_name"]: raw})
    except SEC.SecContentError as exc:
        raise TsmRuleEvidenceError(f"MANIFEST_OR_RAW_INVALID:{exc}") from exc
    if (
        primary.get("source_uri") != lineage.get("primary_source_uri")
        or primary.get("content_sha256") != lineage.get("primary_content_sha256")
        or primary.get("document_name") != lineage.get("primary_document_name")
        or checked.get("retrieved_at_utc") != lineage.get("retrieved_at_utc")
    ):
        raise TsmRuleEvidenceError("OBSERVATION_PRIMARY_LINEAGE_CONFLICT")
    return checked, manifest_bytes, raw


def _exact_row_locator(observation: dict, raw: bytes) -> tuple[str, int, dict]:
    locator = observation.get("table_locator")
    if not isinstance(locator, dict):
        raise TsmRuleEvidenceError("TABLE_LOCATOR_INVALID")
    parser = C4.TableCollector()
    parser.feed(raw.decode("utf-8", errors="replace"))
    table_index = locator.get("table_index")
    row_index = locator.get("data_row_index")
    if type(table_index) is not int or type(row_index) is not int:
        raise TsmRuleEvidenceError("TABLE_LOCATOR_INVALID")
    try:
        rows = C4.drop_empty_columns(parser.tables[table_index])
        row = rows[row_index]
        header = C4.build_header(rows, row_index)
    except (IndexError, C4.HeaderPreconditionError) as exc:
        raise TsmRuleEvidenceError("TABLE_LOCATOR_OUT_OF_RANGE") from exc
    period = observation["economic_period"]
    year, month = map(int, period.split("-"))
    bound, problems = C4.bind_columns(header, row, C4.MONTHS[month - 1], year)
    if bound is None:
        raise TsmRuleEvidenceError(f"TABLE_COLUMN_BINDING_FAILED:{problems}")
    quote = " ".join(row)
    normalized = SEC.normalized_visible_text(raw)
    matches = [match.start() for match in re.finditer(re.escape(quote), normalized)]
    if len(matches) != 1:
        raise TsmRuleEvidenceError(f"EXACT_QUOTE_CARDINALITY:{len(matches)}")
    return quote, matches[0], bound


def _identity_record(row: dict, retention: str) -> dict:
    fields = {"kind", "source_uri", "document_name", "content_sha256", "content_bytes"}
    if not isinstance(row, dict) or set(row) != fields or row.get("kind") != "identity":
        raise TsmRuleEvidenceError("IDENTITY_EVIDENCE_INVALID")
    if SHA_RE.fullmatch(row.get("content_sha256") or "") is None:
        raise TsmRuleEvidenceError("IDENTITY_EVIDENCE_SHA_INVALID")
    return {
        "source_uri": row["source_uri"],
        "content_sha256": row["content_sha256"],
        "content_bytes": row["content_bytes"],
        "document_name": row["document_name"],
        "retention": retention,
        "body_preserved_in_binding": False,
    }


def _envelopes(
    source_packet: dict,
    source_path: Path,
    source_bytes: bytes,
    observation: dict,
    manifest: dict,
    manifest_bytes: bytes,
    raw: bytes,
    contract: dict,
) -> list[dict]:
    quote, char_offset, bound = _exact_row_locator(observation, raw)
    identities = manifest.get("identity_evidence")
    if not isinstance(identities, dict) or set(identities) != {"full_submission", "filing_index"}:
        raise TsmRuleEvidenceError("IDENTITY_EVIDENCE_SET_INVALID")
    identity_lineage = {
        key: _identity_record(row, contract["identity_retention"][key])
        for key, row in identities.items()
    }
    period_end = _month_end(observation["economic_period"])
    values = observation.get("published_values")
    if not isinstance(values, dict):
        raise TsmRuleEvidenceError("PUBLISHED_VALUES_INVALID")
    primary = manifest["documents"][0]
    source_identity = {
        "source_id": contract["source_id"],
        "source_url": primary["source_uri"],
        "source_sha256": primary["content_sha256"],
        "available_at": manifest["retrieved_at_utc"],
        "retrieved_at_utc": manifest["retrieved_at_utc"],
    }
    out = []
    for spec in contract["registered_measurements"]:
        raw_value = values.get(spec["published_value_field"])
        if not isinstance(raw_value, str) or not raw_value:
            raise TsmRuleEvidenceError(
                f"REGISTERED_MEASUREMENT_MISSING:{spec['published_value_field']}"
            )
        column_identity = spec["column_identity"]
        if bound.get(column_identity) != raw_value:
            raise TsmRuleEvidenceError(
                f"REGISTERED_MEASUREMENT_CONFLICT:{spec['measurement_identity']}"
            )
        column_index = bound.get("_column_index", {}).get(column_identity)
        if type(column_index) is not int:
            raise TsmRuleEvidenceError("REGISTERED_COLUMN_IDENTITY_MISSING")
        audit = {
            "capture_kind": "P4_02_RETAINED_EXACT_SEC_BYTES",
            "source_packet": {
                "ref": _repo_ref(source_path),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                "packet_sha256": source_packet["packet_sha256"],
            },
            "manifest": {
                "ref": observation["lineage"]["manifest_ref"],
                "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "accession": manifest["filing_identity"]["accession"],
            },
            "identity_evidence": copy.deepcopy(identity_lineage),
            "primary_document": {
                "ref": observation["lineage"]["primary_document_ref"],
                "source_uri": primary["source_uri"],
                "content_sha256": primary["content_sha256"],
                "raw_cache_policy": manifest["raw_cache_policy"],
            },
            "source_locator": {
                "table_index": observation["table_locator"]["table_index"],
                "data_row_index": observation["table_locator"]["data_row_index"],
                "column_index": column_index,
                "column_identity": column_identity,
            },
            "quote": quote,
            "char_offset": char_offset,
            "offset_basis": "normalized_visible_text",
        }
        out.append({
            "schema_version": BIND.ENVELOPE_SCHEMA_VERSION,
            "subject": "TSM",
            "measurement_identity": spec["measurement_identity"],
            "economic_period_end": period_end,
            "status": BIND.EVIDENCE_AVAILABLE,
            "reasons": [],
            "consumable": True,
            "blocked_by": [],
            "acquisition_provenance_present": True,
            "source_identity": copy.deepcopy(source_identity),
            "audit_provenance": audit,
            "observation": {
                "raw_value": raw_value,
                "numeric_value": raw_value,
                "unit": spec["unit"],
                "quote": quote,
                "char_offset": char_offset,
                "offset_basis": "normalized_visible_text",
            },
        })
    return out


def _binding_document(envelopes: list[dict], contract: dict) -> dict:
    keys = [
        {
            "subject": envelope["subject"],
            "measurement_identity": envelope["measurement_identity"],
            "economic_period_end": envelope["economic_period_end"],
        }
        for envelope in envelopes
    ]
    return {
        "schema_version": BIND.BINDING_SCHEMA_VERSION,
        "binding_set_id": contract["binding_set_id"],
        "bindings": [
            {
                "rule_id": rule_id,
                "selection_mode": "ALL_REQUIRED",
                "evidence_keys": copy.deepcopy(keys),
            }
            for rule_id in sorted(contract["registered_rules"])
        ],
    }


def build_packet(
    *, observation_packet: Path, data_root: Path = DEFAULT_DATA_ROOT,
    contract: dict | None = None,
) -> dict:
    contract = load_contract() if contract is None else copy.deepcopy(contract)
    source_packet, source_bytes = _validated_source_packet(observation_packet, data_root)
    observation = _latest_observation(source_packet)
    manifest, manifest_bytes, raw = _manifest_and_raw(observation, data_root)
    envelopes = _envelopes(
        source_packet, observation_packet, source_bytes, observation,
        manifest, manifest_bytes, raw, contract,
    )
    rules = BIND.load_rules(RULES_PATH)
    registry = {row["rule_id"]: row for row in rules["rules"]}
    for rule_id, condition_sha in contract["registered_rules"].items():
        rule = registry.get(rule_id)
        if rule is None or rule.get("subject") != "TSM" or rule.get("condition_text_sha256") != condition_sha:
            raise TsmRuleEvidenceError(f"REGISTERED_RULE_IDENTITY_CONFLICT:{rule_id}")
    try:
        packet = BIND.build_packet(
            envelopes=envelopes,
            bindings=_binding_document(envelopes, contract),
            rules=rules,
            contract=BIND.load_contract(),
        )
    except BIND.RuleEvidenceBindingError as exc:
        raise TsmRuleEvidenceError(f"P5_03_BINDING_FAILED:{exc}") from exc
    for row in packet["rules"]:
        if row["rule_id"] in contract["registered_rules"]:
            if row["link_status"] != BIND.LINK_AVAILABLE or row["selection_mode"] != "ALL_REQUIRED":
                raise TsmRuleEvidenceError(f"REGISTERED_LINK_NOT_AVAILABLE:{row['rule_id']}")
        if row["rule_result"] is not None or row["evaluation_status"] != BIND.EVALUATION_NOT_AUTHORIZED:
            raise TsmRuleEvidenceError("RULE_EVALUATION_BOUNDARY_VIOLATED")
    return packet


def validate_packet(packet: dict, *, data_root: Path = DEFAULT_DATA_ROOT) -> dict:
    if not isinstance(packet, dict):
        raise TsmRuleEvidenceError("PACKET_NOT_OBJECT")
    try:
        BIND.validate_packet(copy.deepcopy(packet))
    except BIND.RuleEvidenceBindingError as exc:
        raise TsmRuleEvidenceError(f"P5_03_PACKET_INVALID:{exc}") from exc
    envelopes = packet.get("frozen_evidence_envelopes")
    if not isinstance(envelopes, list) or len(envelopes) != 2:
        raise TsmRuleEvidenceError("FROZEN_ENVELOPE_CARDINALITY_INVALID")
    refs = {
        envelope.get("audit_provenance", {}).get("source_packet", {}).get("ref")
        for envelope in envelopes if isinstance(envelope, dict)
    }
    if len(refs) != 1 or None in refs:
        raise TsmRuleEvidenceError("SOURCE_PACKET_REFERENCE_CONFLICT")
    ref = next(iter(refs))
    source_path = ROOT / ref
    if not source_path.is_file():
        raise TsmRuleEvidenceError("SOURCE_PACKET_REFERENCE_MISSING")
    expected = build_packet(observation_packet=source_path, data_root=data_root)
    if canonical_json(expected) != canonical_json(packet):
        raise TsmRuleEvidenceError("PACKET_INDEPENDENT_REBUILD_MISMATCH")
    return copy.deepcopy(packet)


def publish_packet(
    packet: dict, *, out_root: Path = DEFAULT_OUT_ROOT,
    data_root: Path = DEFAULT_DATA_ROOT,
) -> Path:
    checked = validate_packet(packet, data_root=data_root)
    evidence_as_of = checked["frozen_evidence_envelopes"][0]["source_identity"]["retrieved_at_utc"]
    target = Path(out_root) / evidence_as_of[:10] / (
        f"tsm-sec-monthly-rule-evidence-{checked['packet_sha256'][:16]}.json"
    )
    payload = json.dumps(checked, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != payload:
            raise TsmRuleEvidenceError("APPEND_ONLY_PACKET_DRIFT")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return target


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the exact TSM P4-02 -> P5-03 link")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--observation-root", type=Path, default=DEFAULT_OBSERVATION_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = parser.parse_args(argv)
    source_path = select_observation_packet(args.observation_root, args.data_root)
    packet = build_packet(observation_packet=source_path, data_root=args.data_root)
    target = publish_packet(packet, out_root=args.out_root, data_root=args.data_root)
    print(json.dumps({
        "status": "LINK_ONLY",
        "packet": str(target),
        "linked_rules": sorted(load_contract()["registered_rules"]),
        "rule_result": None,
    }, ensure_ascii=False))
    return 0


def main() -> int:
    try:
        return run()
    except TsmRuleEvidenceError as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
