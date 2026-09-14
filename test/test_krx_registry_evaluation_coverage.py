#!/usr/bin/env python3
"""KRX private-registry to public aggregate coverage regression."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "krx_registry_evaluation_coverage",
    ROOT / "discovery" / "krx_registry_evaluation_coverage.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

UNIVERSE_PATH = (
    ROOT / "data" / "observations" / "krx_global_universe" / "2026-08-28" / "packet.json"
)
NATURAL_OUTPUT_PATH = (
    ROOT / "data" / "observations" / "krx_registry_evaluation_coverage"
    / "2026-08-28" / "packet.json"
)
PRIVATE_COMMIT = "9a22be425fb296411f467b799a482db6271eb990"


def _row(
    suffix: int,
    *,
    screening: str = "CATEGORICAL_CANDIDATE",
    decision: str = "UNKNOWN",
    reasons: list[str] | None = None,
    blockers: list[str] | None = None,
    product: str = "COMMON_STOCK",
) -> dict:
    short = f"{suffix:06d}"
    standard = f"KR7000{suffix:06d}3"
    return {
        "security_id": f"KR:XKRX:{standard}",
        "standard_code": standard,
        "short_code": short,
        "display_name": f"PRIVATE-{suffix}",
        "market": "KOSPI",
        "product_type": product,
        "screening_state": screening,
        "decision_eligibility": decision,
        "eligibility_reason_codes": sorted(reasons or []),
        "decision_blocker_codes": sorted(blockers or []),
        "krx_cross_source_status": "MATCHED",
        "code_reuse_status": "NOT_COMPUTABLE_NO_PRIOR_KIS_REGISTRY",
        "evidence_sha256": f"{suffix:064x}",
        "as_of": "2026-08-30T10:05:14Z",
    }


def _write_private_pair(root: Path, mutate=None) -> tuple[Path, Path]:
    universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    rows = [
        _row(
            1,
            blockers=[
                "KRX_DELISTING_SCHEDULE_EVIDENCE_MISSING",
                "LIQUIDITY_AND_EXECUTION_THRESHOLDS_UNRATIFIED",
            ],
        ),
        _row(
            2,
            screening="EXCLUDED",
            decision="EXCLUDED",
            reasons=["PRODUCT_ETN"],
            product="ETN",
        ),
        _row(
            3,
            screening="UNKNOWN",
            reasons=["KIS_SECURITY_GROUP_UNDOCUMENTED:ZZ"],
            blockers=["CATEGORICAL_SCREENING_UNKNOWN"],
            product="UNKNOWN",
        ),
    ]
    registry = {
        "schema_version": "krx_investable_registry/1",
        "contract_version": "krx_investable_registry/1",
        "snapshot_captured_at_utc": "2026-08-30T10:05:14Z",
        "effective_available_at_utc": "2026-08-30T10:05:14Z",
        "latest_completed_session_date": "2026-08-28",
        "latest_session_evidence": {},
        "krx_snapshot_as_of_date": "2026-08-28",
        "krx_snapshot_freshness": "CURRENT",
        "history_status": "NOT_COMPUTABLE_NO_PRIOR_KIS_REGISTRY",
        "source_lineage": {
            "kis_parser_commit": "b4e6249714418aa57833d1cbbbced39cbcc5b125",
            "kis_masters": [{
                "market": "KOSPI",
                "row_count": 3,
                "archive_sha256": "a" * 64,
                "master_sha256": "b" * 64,
            }],
            "krx_packet_sha256": universe["payload_sha256"],
            "krx_source_snapshots": copy.deepcopy(universe["source_snapshots"]),
        },
        "summary": {
            "total_count": 3,
            "market_counts": {"KOSPI": 3},
            "product_counts": {"COMMON_STOCK": 1, "ETN": 1, "UNKNOWN": 1},
            "screening_counts": {
                "CATEGORICAL_CANDIDATE": 1, "EXCLUDED": 1, "UNKNOWN": 1,
            },
            "decision_counts": {"ELIGIBLE": 0, "EXCLUDED": 1, "UNKNOWN": 2},
            "krx_orphan_standard_code_count": 0,
            "kis_stock_scope_missing_from_krx_count": 1,
            "duplicate_standard_code_count": 0,
            "duplicate_short_code_count": 0,
            "code_reuse_count": 0,
            "measurement_coverage": {
                "turnover": 0, "order_book_depth": 0, "spread": 0, "slippage": 0,
            },
        },
        "measurement_policy": {},
        "execution_measurement_evidence": None,
        "krx_paper_gate_compatibility": {},
        "distribution_boundary": {},
        "authority": {
            "registry_evidence_only": True,
            "investable_universe_authorized": False,
            "strategy_entry_authorized": False,
            "paper_order_authorized": False,
            "real_order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "records": rows,
    }
    if mutate is not None:
        mutate(registry)
    registry["payload_sha256"] = MODULE.payload_sha256(registry)
    registry_path = root / "registry.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    registry_bytes = registry_path.read_bytes()
    manifest = {
        "schema_version": "krx_execution_liquidity_registry_input/1",
        "session_date": "2026-08-28",
        "registry_path": "registry.json.gz",
        "registry_compressed_sha256": "c" * 64,
        "registry_compressed_byte_length": 1,
        "registry_raw_sha256": hashlib.sha256(registry_bytes).hexdigest(),
        "registry_raw_byte_length": len(registry_bytes),
        "registry_payload_sha256": registry["payload_sha256"],
        "record_count": len(registry["records"]),
        "categorical_candidate_count": sum(
            row["screening_state"] == "CATEGORICAL_CANDIDATE"
            for row in registry["records"]
        ),
        "source_private_only": True,
        "authority": {
            "investable_universe_authorized": False,
            "paper_order_authorized": False,
            "production_authorized": False,
            "real_order_authorized": False,
            "strategy_entry_authorized": False,
            "trading_authorized": False,
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return registry_path, manifest_path


class NaturalCoverageTests(unittest.TestCase):
    def test_committed_receipt_reports_real_counts_without_candidate_inflation(self):
        packet = json.loads(NATURAL_OUTPUT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(MODULE.validate_output(packet), packet)
        self.assertEqual(packet["coverage"]["source_coverage_universe_count"], 2767)
        self.assertEqual(packet["coverage"]["registry_evaluation_input_count"], 4390)
        self.assertEqual(packet["coverage"]["evaluated_record_count"], 4390)
        self.assertEqual(
            packet["coverage"]["screening_counts"],
            {"CATEGORICAL_CANDIDATE": 3415, "EXCLUDED": 944, "UNKNOWN": 31},
        )
        self.assertEqual(
            packet["coverage"]["decision_counts"],
            {"ELIGIBLE": 0, "EXCLUDED": 944, "UNKNOWN": 3446},
        )
        self.assertEqual(packet["coverage"]["actual_discovery_target_count"], "미집계")
        self.assertEqual(packet["coverage"]["final_candidate_count"], "미집계")
        self.assertTrue(packet["interpretation"]["denominators_are_not_equivalent"])
        self.assertTrue(packet["interpretation"]["categorical_candidate_is_not_discovery_candidate"])

    def test_natural_receipt_binds_exact_public_source_packet(self):
        packet = json.loads(NATURAL_OUTPUT_PATH.read_text(encoding="utf-8"))
        universe = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(packet["source_universe"]["packet_sha256"], universe["payload_sha256"])
        self.assertEqual(packet["source_universe"]["count"], universe["total_count"])
        self.assertFalse(packet["source_universe"]["investable_universe_authorized"])
        self.assertEqual(packet["registry_evaluation_source"]["private_source_commit"], PRIVATE_COMMIT)


class ProducerBoundaryTests(unittest.TestCase):
    def _build(self, mutate=None):
        temp = tempfile.TemporaryDirectory(prefix="krx_eval_coverage_")
        self.addCleanup(temp.cleanup)
        registry, manifest = _write_private_pair(Path(temp.name), mutate)
        return MODULE.build_coverage(
            UNIVERSE_PATH,
            registry,
            manifest,
            private_source_commit=PRIVATE_COMMIT,
        )

    def test_existing_screening_is_connected_with_aggregate_reasons(self):
        packet = self._build()
        self.assertEqual(packet["coverage"]["source_coverage_universe_count"], 2767)
        self.assertEqual(packet["coverage"]["evaluated_record_count"], 3)
        self.assertEqual(packet["screening_exclusion_reason_counts"], {"PRODUCT_ETN": 1})
        self.assertEqual(
            packet["decision_unknown_blocker_counts"],
            {
                "CATEGORICAL_SCREENING_UNKNOWN": 1,
                "KRX_DELISTING_SCHEDULE_EVIDENCE_MISSING": 1,
                "LIQUIDITY_AND_EXECUTION_THRESHOLDS_UNRATIFIED": 1,
            },
        )
        self.assertEqual(MODULE.validate_output(packet), packet)

    def test_exact_krx_packet_lineage_is_required(self):
        with self.assertRaisesRegex(
            MODULE.KrxRegistryEvaluationCoverageError,
            "KRX_REGISTRY_LINEAGE_MISMATCH",
        ):
            self._build(
                lambda registry: registry["source_lineage"].__setitem__(
                    "krx_packet_sha256", "0" * 64
                )
            )

    def test_duplicate_identity_is_rejected_even_when_registry_and_manifest_are_rehashed(self):
        def duplicate(registry):
            row = copy.deepcopy(registry["records"][0])
            registry["records"].append(row)
            registry["source_lineage"]["kis_masters"][0]["row_count"] = 4
            registry["summary"]["total_count"] = 4
            registry["summary"]["market_counts"]["KOSPI"] = 4
            registry["summary"]["product_counts"]["COMMON_STOCK"] = 2
            registry["summary"]["screening_counts"]["CATEGORICAL_CANDIDATE"] = 2
            registry["summary"]["decision_counts"]["UNKNOWN"] = 3

        with self.assertRaisesRegex(
            MODULE.KrxRegistryEvaluationCoverageError,
            "PRIVATE_REGISTRY_DUPLICATE:security_id",
        ):
            self._build(duplicate)

    def test_existing_rules_cannot_be_rewritten_as_eligible(self):
        def promote(registry):
            registry["records"][0]["decision_eligibility"] = "ELIGIBLE"
            registry["summary"]["decision_counts"] = {
                "ELIGIBLE": 1, "EXCLUDED": 1, "UNKNOWN": 1,
            }

        with self.assertRaisesRegex(
            MODULE.KrxRegistryEvaluationCoverageError,
            "UNAUTHORIZED_ELIGIBILITY_PROMOTION",
        ):
            self._build(promote)

    def test_output_tamper_fails_closed(self):
        packet = self._build()
        packet["coverage"]["actual_discovery_target_count"] = 3415
        with self.assertRaisesRegex(
            MODULE.KrxRegistryEvaluationCoverageError, "OUTPUT_HASH_MISMATCH"
        ):
            MODULE.validate_output(packet)


if __name__ == "__main__":
    unittest.main()
