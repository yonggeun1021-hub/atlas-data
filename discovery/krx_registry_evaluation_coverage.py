#!/usr/bin/env python3
"""Project an existing private KRX registry evaluation into public-safe coverage.

The existing KIS registry performs categorical screening over the KIS master
population.  The official KRX packet is a different, stock-source-coverage
population.  This consumer binds the exact same-date source lineage and reports
both denominators without treating either one as an approved discovery universe.

Only aggregate counts and hashes leave the private boundary.  No symbol, name,
per-symbol state, strategy rule, threshold, candidate, or order is produced.
"""
from __future__ import annotations

import argparse
from collections import Counter
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

from universe import global_asset_master as GAM  # noqa: E402


SCHEMA_VERSION = "krx_registry_evaluation_coverage/1"
NOT_COUNTED = "미집계"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

AUTHORITY = {
    "aggregate_coverage_only": True,
    "investable_universe_authorized": False,
    "strategy_entry_authorized": False,
    "candidate_promotion_authorized": False,
    "paper_order_authorized": False,
    "real_order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class KrxRegistryEvaluationCoverageError(ValueError):
    """An exact-source or aggregate coverage claim failed closed."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KrxRegistryEvaluationCoverageError(code) from exc
    if not isinstance(value, dict):
        raise KrxRegistryEvaluationCoverageError(code)
    return value


def _validate_self_hash(value: dict, field: str, code: str) -> None:
    claimed = value.get(field)
    unsigned = copy.deepcopy(value)
    unsigned.pop(field, None)
    if not isinstance(claimed, str) or payload_sha256(unsigned) != claimed:
        raise KrxRegistryEvaluationCoverageError(code)


def _require_false_authority(authority: object, *, evidence_key: str | None = None) -> None:
    if not isinstance(authority, dict) or not authority:
        raise KrxRegistryEvaluationCoverageError("AUTHORITY_INVALID")
    if evidence_key is not None and authority.get(evidence_key) is not True:
        raise KrxRegistryEvaluationCoverageError("AUTHORITY_INVALID")
    if any(
        value is not False
        for key, value in authority.items()
        if key != evidence_key
    ):
        raise KrxRegistryEvaluationCoverageError("AUTHORITY_OPEN")


def _validate_universe(path: Path) -> dict:
    packet = _read_json(path, "KRX_UNIVERSE_READ_FAILED")
    _validate_self_hash(packet, "payload_sha256", "KRX_UNIVERSE_HASH_MISMATCH")
    if (
        packet.get("schema_version") != "krx_global_universe_packet/1"
        or packet.get("contract_version") != "krx_global_universe_adapter/1"
        or packet.get("status") != "SOURCE_COVERAGE_UNIVERSE_VALIDATED"
        or packet.get("membership_semantics")
        != "exact_trading_date_source_coverage_not_investable"
        or packet.get("policy_status") != {
            "investable_universe_policy": "UNRATIFIED",
            "liquidity_policy": "UNRATIFIED",
            "listing_delisting_policy": "UNRATIFIED",
            "source_coverage_membership": "IMPLEMENTED",
            "theme_taxonomy": "UNRATIFIED",
            "tradability_policy": "UNRATIFIED",
        }
    ):
        raise KrxRegistryEvaluationCoverageError("KRX_UNIVERSE_HEADER_INVALID")
    authority = packet.get("authority")
    if (
        not isinstance(authority, dict)
        or authority.get("source_coverage_universe_only") is not True
        or any(
            value is not False
            for key, value in authority.items()
            if key != "source_coverage_universe_only"
        )
    ):
        raise KrxRegistryEvaluationCoverageError("KRX_UNIVERSE_AUTHORITY_INVALID")
    try:
        master = GAM.validate_packet(packet.get("asset_master"))
    except GAM.GlobalAssetMasterError as exc:
        raise KrxRegistryEvaluationCoverageError(
            f"KRX_ASSET_MASTER_INVALID:{exc}"
        ) from exc
    sources = packet.get("source_snapshots")
    if not isinstance(sources, list) or not sources:
        raise KrxRegistryEvaluationCoverageError("KRX_SOURCE_SNAPSHOTS_INVALID")
    market_counts = Counter()
    for source in sources:
        if (
            not isinstance(source, dict)
            or set(source) != {
                "market", "available_at", "retrieved_at_utc", "source_sha256",
                "universe_count",
            }
            or source.get("available_at") != packet.get("as_of_date")
            or not isinstance(source.get("market"), str)
            or not isinstance(source.get("source_sha256"), str)
            or SHA256_RE.fullmatch(source["source_sha256"]) is None
            or type(source.get("universe_count")) is not int
            or source["universe_count"] < 0
        ):
            raise KrxRegistryEvaluationCoverageError("KRX_SOURCE_SNAPSHOTS_INVALID")
        market_counts[source["market"]] += source["universe_count"]
    if (
        dict(market_counts) != packet.get("market_counts")
        or sum(market_counts.values()) != packet.get("total_count")
        or packet.get("total_count") != master.get("record_count")
        or packet.get("as_of_date") != master.get("as_of_date")
    ):
        raise KrxRegistryEvaluationCoverageError("KRX_UNIVERSE_COUNT_INVALID")
    return packet


def _count_rows(rows: list[dict], field: str) -> dict[str, int]:
    counts = Counter(row[field] for row in rows)
    return dict(sorted(counts.items()))


def _count_codes(rows: list[dict], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        counts.update(row[field])
    return dict(sorted(counts.items()))


def _validate_registry(registry_path: Path, manifest_path: Path, universe: dict) -> tuple[dict, dict]:
    registry = _read_json(registry_path, "PRIVATE_REGISTRY_READ_FAILED")
    manifest = _read_json(manifest_path, "PRIVATE_MANIFEST_READ_FAILED")
    if (
        manifest.get("schema_version")
        != "krx_execution_liquidity_registry_input/1"
        or manifest.get("source_private_only") is not True
    ):
        raise KrxRegistryEvaluationCoverageError("PRIVATE_MANIFEST_HEADER_INVALID")
    _require_false_authority(manifest.get("authority"))
    if (
        manifest.get("registry_raw_sha256") != _file_sha256(registry_path)
        or manifest.get("registry_raw_byte_length") != registry_path.stat().st_size
    ):
        raise KrxRegistryEvaluationCoverageError("PRIVATE_REGISTRY_FILE_MISMATCH")
    _validate_self_hash(registry, "payload_sha256", "PRIVATE_REGISTRY_HASH_MISMATCH")
    if (
        registry.get("schema_version") != "krx_investable_registry/1"
        or registry.get("contract_version") != "krx_investable_registry/1"
        or manifest.get("registry_payload_sha256") != registry.get("payload_sha256")
        or manifest.get("session_date") != registry.get("latest_completed_session_date")
        or registry.get("latest_completed_session_date") != universe.get("as_of_date")
        or registry.get("krx_snapshot_as_of_date") != universe.get("as_of_date")
        or registry.get("krx_snapshot_freshness") != "CURRENT"
    ):
        raise KrxRegistryEvaluationCoverageError("PRIVATE_REGISTRY_HEADER_INVALID")
    _require_false_authority(registry.get("authority"), evidence_key="registry_evidence_only")
    lineage = registry.get("source_lineage")
    if (
        not isinstance(lineage, dict)
        or lineage.get("krx_packet_sha256") != universe.get("payload_sha256")
        or lineage.get("krx_source_snapshots") != universe.get("source_snapshots")
    ):
        raise KrxRegistryEvaluationCoverageError("KRX_REGISTRY_LINEAGE_MISMATCH")
    kis_masters = lineage.get("kis_masters")
    if not isinstance(kis_masters, list) or not kis_masters:
        raise KrxRegistryEvaluationCoverageError("KIS_MASTER_LINEAGE_INVALID")
    for source in kis_masters:
        if (
            not isinstance(source, dict)
            or type(source.get("row_count")) is not int
            or source["row_count"] < 0
            or SHA256_RE.fullmatch(str(source.get("archive_sha256"))) is None
            or SHA256_RE.fullmatch(str(source.get("master_sha256"))) is None
        ):
            raise KrxRegistryEvaluationCoverageError("KIS_MASTER_LINEAGE_INVALID")

    rows = registry.get("records")
    summary = registry.get("summary")
    if not isinstance(rows, list) or not isinstance(summary, dict):
        raise KrxRegistryEvaluationCoverageError("PRIVATE_REGISTRY_RECORDS_INVALID")
    required = {
        "security_id", "standard_code", "short_code", "display_name", "market",
        "product_type", "screening_state", "decision_eligibility",
        "eligibility_reason_codes", "decision_blocker_codes",
        "krx_cross_source_status", "code_reuse_status", "evidence_sha256", "as_of",
    }
    for row in rows:
        if (
            not isinstance(row, dict)
            or not required.issubset(row)
            or row.get("screening_state")
            not in {"CATEGORICAL_CANDIDATE", "EXCLUDED", "UNKNOWN"}
            or row.get("decision_eligibility") not in {"ELIGIBLE", "EXCLUDED", "UNKNOWN"}
            or not isinstance(row.get("eligibility_reason_codes"), list)
            or not isinstance(row.get("decision_blocker_codes"), list)
            or row["eligibility_reason_codes"] != sorted(set(row["eligibility_reason_codes"]))
            or row["decision_blocker_codes"] != sorted(set(row["decision_blocker_codes"]))
            or SHA256_RE.fullmatch(str(row.get("evidence_sha256"))) is None
        ):
            raise KrxRegistryEvaluationCoverageError("PRIVATE_REGISTRY_RECORD_INVALID")
        if (row["screening_state"] == "EXCLUDED") != (
            row["decision_eligibility"] == "EXCLUDED"
        ):
            raise KrxRegistryEvaluationCoverageError("SCREENING_DECISION_STATE_MISMATCH")
        if row["screening_state"] != "EXCLUDED" and row["decision_eligibility"] != "UNKNOWN":
            raise KrxRegistryEvaluationCoverageError("UNAUTHORIZED_ELIGIBILITY_PROMOTION")
    for field in ("security_id", "standard_code", "short_code"):
        values = [row[field] for row in rows]
        if len(values) != len(set(values)):
            raise KrxRegistryEvaluationCoverageError(f"PRIVATE_REGISTRY_DUPLICATE:{field}")

    screening = _count_rows(rows, "screening_state")
    decision = _count_rows(rows, "decision_eligibility")
    markets = _count_rows(rows, "market")
    products = _count_rows(rows, "product_type")
    expected_screening = {
        key: screening.get(key, 0)
        for key in ("CATEGORICAL_CANDIDATE", "EXCLUDED", "UNKNOWN")
    }
    expected_decision = {
        key: decision.get(key, 0)
        for key in ("ELIGIBLE", "EXCLUDED", "UNKNOWN")
    }
    if (
        summary.get("total_count") != len(rows)
        or summary.get("market_counts") != markets
        or summary.get("product_counts") != products
        or summary.get("screening_counts") != expected_screening
        or summary.get("decision_counts") != expected_decision
        or summary.get("duplicate_standard_code_count") != 0
        or summary.get("duplicate_short_code_count") != 0
        or manifest.get("record_count") != len(rows)
        or manifest.get("categorical_candidate_count")
        != expected_screening["CATEGORICAL_CANDIDATE"]
        or sum(source["row_count"] for source in kis_masters) != len(rows)
    ):
        raise KrxRegistryEvaluationCoverageError("PRIVATE_REGISTRY_SUMMARY_MISMATCH")
    return registry, manifest


def build_coverage(
    universe_path: Path,
    registry_path: Path,
    registry_manifest_path: Path,
    *,
    private_source_commit: str,
) -> dict:
    """Validate exact inputs and return a public-safe aggregate receipt."""
    if COMMIT_RE.fullmatch(private_source_commit) is None:
        raise KrxRegistryEvaluationCoverageError("PRIVATE_SOURCE_COMMIT_INVALID")
    universe = _validate_universe(Path(universe_path))
    registry, manifest = _validate_registry(
        Path(registry_path), Path(registry_manifest_path), universe
    )
    rows = registry["records"]
    excluded = [row for row in rows if row["screening_state"] == "EXCLUDED"]
    unknown = [row for row in rows if row["screening_state"] == "UNKNOWN"]
    decision_unknown = [row for row in rows if row["decision_eligibility"] == "UNKNOWN"]
    summary = registry["summary"]
    try:
        universe_relative_path = Path(universe_path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise KrxRegistryEvaluationCoverageError("PUBLIC_UNIVERSE_PATH_OUTSIDE_REPOSITORY") from exc
    packet = {
        "schema_version": SCHEMA_VERSION,
        "market": "KOREA",
        "generated_at": registry["snapshot_captured_at_utc"],
        "evaluation_session_date": registry["latest_completed_session_date"],
        "source_universe": {
            "path": universe_relative_path,
            "file_sha256": _file_sha256(Path(universe_path)),
            "packet_sha256": universe["payload_sha256"],
            "count": universe["total_count"],
            "market_counts": copy.deepcopy(universe["market_counts"]),
            "semantics": universe["membership_semantics"],
            "investable_universe_authorized": False,
        },
        "registry_evaluation_source": {
            "private_source_commit": private_source_commit,
            "private_manifest_file_sha256": _file_sha256(Path(registry_manifest_path)),
            "private_registry_file_sha256": manifest["registry_raw_sha256"],
            "private_registry_payload_sha256": registry["payload_sha256"],
            "kis_master_record_count": summary["total_count"],
            "semantics": "EXISTING_KIS_MASTER_CATEGORICAL_SCREENING_EVIDENCE",
        },
        "coverage": {
            "source_coverage_universe_count": universe["total_count"],
            "registry_evaluation_input_count": summary["total_count"],
            "evaluated_record_count": len(rows),
            "screening_counts": copy.deepcopy(summary["screening_counts"]),
            "decision_counts": copy.deepcopy(summary["decision_counts"]),
            "actual_discovery_target_count": NOT_COUNTED,
            "final_candidate_count": NOT_COUNTED,
        },
        "source_membership_reconciliation": {
            "krx_orphan_standard_code_count": summary["krx_orphan_standard_code_count"],
            "kis_stock_scope_missing_from_krx_count": summary["kis_stock_scope_missing_from_krx_count"],
            "cross_source_status_counts": _count_rows(rows, "krx_cross_source_status"),
            "duplicate_standard_code_count": summary["duplicate_standard_code_count"],
            "duplicate_short_code_count": summary["duplicate_short_code_count"],
        },
        "screening_exclusion_reason_counts": _count_codes(excluded, "eligibility_reason_codes"),
        "screening_unknown_reason_counts": _count_codes(unknown, "eligibility_reason_codes"),
        "decision_unknown_blocker_counts": _count_codes(decision_unknown, "decision_blocker_codes"),
        "interpretation": {
            "denominators_are_not_equivalent": True,
            "reason": "KIS_MASTER_SCREENING_INCLUDES_NON_KRX_STOCK_PRODUCTS",
            "categorical_candidate_is_not_discovery_candidate": True,
            "source_coverage_is_not_investable_universe": True,
            "decision_eligible_count_is_not_candidate_count": True,
            "reason_counts_are_multivalued_non_additive": True,
            "registry_evaluation_is_not_recomputed_with_later_evidence": True,
            "policy_gap": "FINAL_INVESTABLE_UNIVERSE_AND_LIQUIDITY_EXECUTION_THRESHOLDS_UNRATIFIED",
        },
        "state_lifetime": {
            "snapshot_only": True,
            "automatic_carry_forward": False,
            "reevaluation_required": True,
        },
        "authority": copy.deepcopy(AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_output(packet: dict) -> dict:
    expected = {
        "schema_version", "market", "generated_at", "evaluation_session_date",
        "source_universe", "registry_evaluation_source", "coverage",
        "source_membership_reconciliation", "screening_exclusion_reason_counts",
        "screening_unknown_reason_counts", "decision_unknown_blocker_counts",
        "interpretation", "state_lifetime", "authority", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected:
        raise KrxRegistryEvaluationCoverageError("OUTPUT_SCHEMA_MISMATCH")
    _validate_self_hash(packet, "payload_sha256", "OUTPUT_HASH_MISMATCH")
    if packet.get("schema_version") != SCHEMA_VERSION or packet.get("market") != "KOREA":
        raise KrxRegistryEvaluationCoverageError("OUTPUT_HEADER_INVALID")
    if packet.get("authority") != AUTHORITY:
        raise KrxRegistryEvaluationCoverageError("OUTPUT_AUTHORITY_INVALID")
    coverage = packet.get("coverage")
    if (
        not isinstance(coverage, dict)
        or coverage.get("actual_discovery_target_count") != NOT_COUNTED
        or coverage.get("final_candidate_count") != NOT_COUNTED
        or coverage.get("evaluated_record_count") != coverage.get("registry_evaluation_input_count")
        or sum(coverage.get("screening_counts", {}).values()) != coverage.get("evaluated_record_count")
        or sum(coverage.get("decision_counts", {}).values()) != coverage.get("evaluated_record_count")
        or packet.get("interpretation", {}).get("denominators_are_not_equivalent") is not True
        or packet.get("interpretation", {}).get("reason_counts_are_multivalued_non_additive") is not True
        or packet.get("interpretation", {}).get("registry_evaluation_is_not_recomputed_with_later_evidence") is not True
        or packet.get("state_lifetime") != {
            "snapshot_only": True,
            "automatic_carry_forward": False,
            "reevaluation_required": True,
        }
    ):
        raise KrxRegistryEvaluationCoverageError("OUTPUT_COVERAGE_INVALID")
    return copy.deepcopy(packet)


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp = Path(handle.name)
    os.replace(temp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--private-registry", required=True, type=Path)
    parser.add_argument("--private-manifest", required=True, type=Path)
    parser.add_argument("--private-source-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        packet = build_coverage(
            args.universe,
            args.private_registry,
            args.private_manifest,
            private_source_commit=args.private_source_commit,
        )
        validate_output(packet)
        write_json_atomic(args.output, packet)
    except KrxRegistryEvaluationCoverageError as exc:
        print(f"KRX registry evaluation coverage failed reason={exc}")
        return 1
    print(
        "KRX registry evaluation coverage "
        f"source={packet['coverage']['source_coverage_universe_count']} "
        f"evaluated={packet['coverage']['evaluated_record_count']} "
        f"screening={packet['coverage']['screening_counts']} "
        f"decision={packet['coverage']['decision_counts']} "
        f"sha256={packet['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
