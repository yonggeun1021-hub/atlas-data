#!/usr/bin/env python3
"""P6-04 Long FAIL must never be translated into Short PASS."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rules import deterministic_rule_evaluator as RULE_EVALUATOR  # noqa: E402
CONTRACT_PATH = ROOT / "config" / "long_short_invariant_contract.json"
RULES_PATH = ROOT / "config" / "rules.json"
OUTPUT_SCHEMA_VERSION = "long_short_invariant_packet/1"
LONG_RESULTS = ("PASS", "FAIL", "UNKNOWN", "UNDEFINED")
RULE_ID_RE = re.compile(r"^RULE-\d{4}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LongShortInvariantError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _exact_value_equal(actual: object, expected: object) -> bool:
    """Compare JSON values without Python's bool/int aliasing."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _exact_value_equal(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact_value_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LongShortInvariantError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "long_short_invariant/1",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "upstream_contract_version": "deterministic_rule_evaluator/2",
        "upstream_schema_version": "deterministic_rule_evaluation_packet/2",
        "upstream_status": "BOUNDARY_CLASSIFIED_PASS_FAIL_NOT_AUTHORIZED",
        "canonical_rule_count": 25,
        "accepted_long_results": list(LONG_RESULTS),
        "derived_short_evaluation_status": "NOT_EVALUATED",
        "invariant": "LONG_FAIL_NEVER_IMPLIES_SHORT_PASS",
        "independent_prerequisites": [
            "HEDGE_INSTRUMENT_ELIGIBILITY_RATIFIED",
            "BEAR_HEDGE_RISK_BUDGET_RATIFIED",
            "INDEPENDENT_SHORT_RULE_EVALUATION",
        ],
        "upstream_authority": {
            "boundary_classification_only": True,
            "evidence_interpretation_authorized": False,
            "threshold_application_authorized": False,
            "pass_fail_authorized": False,
            "downstream_action_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "invariant_enforcement_only": True,
            "short_candidate_selection_authorized": False,
            "short_evaluation_authorized": False,
            "short_pass_authorized": False,
            "hedge_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise LongShortInvariantError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if not _exact_value_equal(value.get(key), expected_value):
            raise LongShortInvariantError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise LongShortInvariantError(code)
    return value


def classify_long_result(long_result: str, contract: dict | None = None) -> dict:
    """Return the only authorized short-side consequence of a long result."""
    contract = _validate_contract(contract) if contract is not None else load_contract()
    if long_result not in contract["accepted_long_results"]:
        raise LongShortInvariantError(f"LONG_RESULT_INVALID:{long_result}")
    reasons = [
        "INDEPENDENT_SHORT_RULE_EVALUATION_REQUIRED",
        "HEDGE_INSTRUMENT_ELIGIBILITY_NOT_RATIFIED",
        "BEAR_HEDGE_RISK_BUDGET_NOT_RATIFIED",
    ]
    if long_result == "FAIL":
        reasons.insert(0, "LONG_FAIL_DOES_NOT_IMPLY_SHORT_PASS")
    else:
        reasons.insert(0, "LONG_RESULT_HAS_NO_SHORT_AUTHORITY")
    return {
        "long_result": long_result,
        "short_result": None,
        "short_evaluation_status": contract["derived_short_evaluation_status"],
        "invariant_status": "ENFORCED",
        "reasons": reasons,
    }


def assert_short_result_not_derived(long_result: str, proposed_short_result) -> None:
    classify_long_result(long_result)
    if proposed_short_result is not None:
        raise LongShortInvariantError(
            f"DERIVED_SHORT_RESULT_FORBIDDEN:{long_result}:{proposed_short_result}"
        )


def _validate_upstream_packet(value: dict, contract: dict) -> dict:
    packet_fields = {
        "schema_version", "contract_version", "status", "binding_set_id",
        "frozen_binding_packet", "summary", "rules", "lineage", "authority",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != packet_fields:
        raise LongShortInvariantError("UPSTREAM_PACKET_FIELDS_MISMATCH")
    digest = _sha(value.get("packet_sha256"), "UPSTREAM_PACKET_SHA_INVALID")
    payload = copy.deepcopy(value)
    payload.pop("packet_sha256")
    if payload_sha256(payload) != digest:
        raise LongShortInvariantError("UPSTREAM_PACKET_SHA_MISMATCH")
    if (
        value.get("schema_version") != contract["upstream_schema_version"]
        or value.get("contract_version") != contract["upstream_contract_version"]
        or value.get("status") != contract["upstream_status"]
        or not _exact_value_equal(
            value.get("authority"), contract["upstream_authority"]
        )
    ):
        raise LongShortInvariantError("UPSTREAM_PACKET_IDENTITY_INVALID")
    if not isinstance(value.get("binding_set_id"), str) or not value["binding_set_id"]:
        raise LongShortInvariantError("UPSTREAM_BINDING_SET_ID_INVALID")
    lineage = value.get("lineage")
    if not isinstance(lineage, dict) or set(lineage) != {
        "rule_registry_sha256", "binding_packet_sha256",
        "binding_set_sha256", "evidence_set_sha256",
    }:
        raise LongShortInvariantError("UPSTREAM_LINEAGE_INVALID")
    for item in lineage.values():
        _sha(item, "UPSTREAM_LINEAGE_SHA_INVALID")
    if not isinstance(value.get("unresolved_boundaries"), list):
        raise LongShortInvariantError("UPSTREAM_BOUNDARIES_INVALID")

    rows = value.get("rules")
    if not isinstance(rows, list) or len(rows) != contract["canonical_rule_count"]:
        raise LongShortInvariantError("UPSTREAM_RULE_COUNT_INVALID")
    expected_rule_fields = {
        "rule_id", "subject", "rule_kind", "downstream_effect",
        "condition_text_sha256", "definition_status", "data_status",
        "source_qualification", "rule_ssot_evaluator_status",
        "rule_ssot_blocked_by", "link_status", "link_reasons",
        "evidence_reference_set_sha256", "result", "reasons",
        "evaluation_spec_sha256",
    }
    checked = []
    seen = set()
    counts = {status: 0 for status in LONG_RESULTS}
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_rule_fields:
            raise LongShortInvariantError("UPSTREAM_RULE_FIELDS_MISMATCH")
        rule_id = row.get("rule_id")
        result = row.get("result")
        if (
            not isinstance(rule_id, str)
            or RULE_ID_RE.fullmatch(rule_id) is None
            or rule_id in seen
            or not isinstance(row.get("subject"), str)
            or not row["subject"]
            or result not in LONG_RESULTS
        ):
            raise LongShortInvariantError(f"UPSTREAM_RULE_INVALID:{rule_id}")
        _sha(row.get("condition_text_sha256"), f"UPSTREAM_CONDITION_SHA_INVALID:{rule_id}")
        _sha(
            row.get("evidence_reference_set_sha256"),
            f"UPSTREAM_EVIDENCE_SHA_INVALID:{rule_id}",
        )
        if result in {"PASS", "FAIL"}:
            raise LongShortInvariantError("UPSTREAM_PASS_FAIL_WITHOUT_AUTHORITY")
        seen.add(rule_id)
        counts[result] += 1
        checked.append(copy.deepcopy(row))
    if [row["rule_id"] for row in checked] != sorted(seen):
        raise LongShortInvariantError("UPSTREAM_RULE_ORDER_INVALID")
    expected_summary = {
        "total_rules": contract["canonical_rule_count"],
        **counts,
    }
    if value.get("summary") != expected_summary:
        raise LongShortInvariantError("UPSTREAM_SUMMARY_MISMATCH")
    try:
        RULE_EVALUATOR.validate_packet(value)
    except RULE_EVALUATOR.DeterministicRuleEvaluatorError as exc:
        raise LongShortInvariantError(
            f"UPSTREAM_SEMANTIC_INVALID:{exc}"
        ) from exc
    return {
        "packet_sha256": digest,
        "binding_set_id": value["binding_set_id"],
        "rules": checked,
        "lineage": copy.deepcopy(lineage),
    }


def build_packet(upstream_packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    checked = _validate_upstream_packet(upstream_packet, contract)
    counts = {status: 0 for status in LONG_RESULTS}
    output = []
    for row in checked["rules"]:
        boundary = classify_long_result(row["result"], contract)
        counts[row["result"]] += 1
        output.append({
            "rule_id": row["rule_id"],
            "subject": row["subject"],
            "condition_text_sha256": row["condition_text_sha256"],
            **boundary,
        })
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "status": "INVARIANT_ENFORCED_SHORT_NOT_EVALUATED",
        "binding_set_id": checked["binding_set_id"],
        "summary": {
            "total_rules": len(output),
            "long_results": counts,
            "short_results_created": 0,
            "short_pass": 0,
            "short_not_evaluated": len(output),
        },
        "rules": output,
        "lineage": {
            "upstream_evaluator_packet_sha256": checked["packet_sha256"],
            **checked["lineage"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": copy.deepcopy(contract["independent_prerequisites"]),
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, contract)


def validate_packet(packet: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {
        "schema_version", "contract_version", "status", "binding_set_id",
        "summary", "rules", "lineage", "authority", "unresolved_boundaries",
        "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise LongShortInvariantError("OUTPUT_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != contract["output_schema_version"]
        or packet.get("contract_version") != contract["contract_version"]
        or packet.get("status") != "INVARIANT_ENFORCED_SHORT_NOT_EVALUATED"
        or not _exact_value_equal(packet.get("authority"), contract["authority"])
        or not isinstance(packet.get("binding_set_id"), str)
        or not packet["binding_set_id"]
    ):
        raise LongShortInvariantError("OUTPUT_IDENTITY_INVALID")
    canonical = _read_json(RULES_PATH)
    canonical_rows = canonical.get("rules") if isinstance(canonical, dict) else None
    if not isinstance(canonical_rows, list) or len(canonical_rows) != contract["canonical_rule_count"]:
        raise LongShortInvariantError("OUTPUT_CANONICAL_RULES_INVALID")
    registry = {row.get("rule_id"): row for row in canonical_rows}
    rows = packet.get("rules")
    row_fields = {
        "rule_id", "subject", "condition_text_sha256", "long_result",
        "short_result", "short_evaluation_status", "invariant_status", "reasons",
    }
    if not isinstance(rows, list) or len(rows) != contract["canonical_rule_count"]:
        raise LongShortInvariantError("OUTPUT_RULE_COUNT_INVALID")
    counts = {status: 0 for status in LONG_RESULTS}
    checked_ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_fields:
            raise LongShortInvariantError("OUTPUT_RULE_FIELDS_MISMATCH")
        rule_id = row.get("rule_id")
        rule = registry.get(rule_id)
        result = row.get("long_result")
        if (
            rule is None
            or row.get("subject") != rule.get("subject")
            or row.get("condition_text_sha256") != rule.get("condition_text_sha256")
            or result not in LONG_RESULTS
        ):
            raise LongShortInvariantError(f"OUTPUT_RULE_IDENTITY_INVALID:{rule_id}")
        if result in {"PASS", "FAIL"}:
            raise LongShortInvariantError("OUTPUT_PASS_FAIL_WITHOUT_AUTHORITY")
        expected = classify_long_result(result, contract)
        if any(
            not _exact_value_equal(row.get(key), value)
            for key, value in expected.items()
        ):
            raise LongShortInvariantError(f"OUTPUT_RULE_DERIVATION_MISMATCH:{rule_id}")
        counts[result] += 1
        checked_ids.append(rule_id)
    if checked_ids != sorted(registry):
        raise LongShortInvariantError("OUTPUT_RULE_ORDER_INVALID")
    if not _exact_value_equal(packet.get("summary"), {
        "total_rules": len(rows),
        "long_results": counts,
        "short_results_created": 0,
        "short_pass": 0,
        "short_not_evaluated": len(rows),
    }):
        raise LongShortInvariantError("OUTPUT_SUMMARY_MISMATCH")
    lineage = packet.get("lineage")
    lineage_fields = {
        "upstream_evaluator_packet_sha256", "rule_registry_sha256",
        "binding_packet_sha256", "binding_set_sha256", "evidence_set_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise LongShortInvariantError("OUTPUT_LINEAGE_FIELDS_MISMATCH")
    for key in lineage_fields:
        _sha(lineage.get(key), f"OUTPUT_LINEAGE_SHA_INVALID:{key}")
    if lineage["rule_registry_sha256"] != payload_sha256(canonical):
        raise LongShortInvariantError("OUTPUT_RULE_REGISTRY_SHA_MISMATCH")
    if packet.get("unresolved_boundaries") != contract["independent_prerequisites"]:
        raise LongShortInvariantError("OUTPUT_BOUNDARIES_MISMATCH")
    digest = _sha(packet.get("packet_sha256"), "OUTPUT_PACKET_SHA_INVALID")
    normalized = copy.deepcopy(packet)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise LongShortInvariantError("OUTPUT_PACKET_SHA_MISMATCH")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise LongShortInvariantError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(upstream_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(upstream_path)))
        return 0
    except (LongShortInvariantError, OSError, TypeError, ValueError) as exc:
        print(f"Long/Short invariant failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enforce Long FAIL != Short PASS without creating short authority"
    )
    parser.add_argument("rule_evaluator_packet", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.rule_evaluator_packet, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
