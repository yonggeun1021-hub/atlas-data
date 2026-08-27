#!/usr/bin/env python3
"""P3-01 committed source-coverage readiness regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "universe" / "global_asset_master_population_readiness.py"
SPEC = importlib.util.spec_from_file_location("global_asset_master_population_readiness", MODULE)
READINESS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(READINESS)


class GlobalAssetMasterPopulationReadinessTests(unittest.TestCase):
    def test_real_committed_inventory_is_honest_and_closed(self):
        packet = READINESS.build_readiness("2026-08-26")
        by_market = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(packet["status"], "BLOCKED_SOURCE_COVERAGE_INCOMPLETE")
        self.assertEqual(packet["ready_market_count"], 1)
        self.assertEqual(by_market["US"]["status"], "SOURCE_COVERAGE_READY")
        self.assertEqual(by_market["US"]["source_date"], "2026-08-25")
        self.assertGreater(by_market["US"]["record_count"], 10000)
        self.assertRegex(by_market["US"]["knowledge_first_seen_commit"], r"^[0-9a-f]{40}$")
        self.assertLessEqual(
            by_market["US"]["knowledge_first_seen_at"], "2026-08-26T23:59:59Z"
        )
        self.assertEqual(
            by_market["KOREA"]["reason"],
            "COMMITTED_EXACT_KRX_POPULATION_PACKET_MISSING",
        )
        self.assertEqual(by_market["CRYPTO"]["source_date"], "2026-08-26")
        self.assertTrue(
            by_market["CRYPTO"]["reason"].startswith("BREADTH_SELECTION_")
        )
        self.assertEqual(packet["freshness_policy"], "UNRATIFIED_NO_STALE_INFERENCE")
        self.assertEqual(
            packet["knowledge_time_policy"],
            "EXACT_CONTENT_GIT_FIRST_SEEN_ON_OR_BEFORE_AS_OF_END_UTC",
        )
        self.assertTrue(packet["authority"]["source_coverage_readiness_only"])
        for name, value in packet["authority"].items():
            if name != "source_coverage_readiness_only":
                self.assertFalse(value, name)
        READINESS.validate_readiness(packet)

    def test_future_population_directory_is_not_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "2026-08-27").mkdir()
            self.assertIsNone(READINESS._latest_date(root, "2026-08-26"))

    def test_backfilled_latest_us_packet_is_not_selected_before_exact_git_first_seen(self):
        packet = READINESS.build_readiness("2026-08-25")
        by_market = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(by_market["US"]["status"], "SOURCE_COVERAGE_READY")
        self.assertEqual(by_market["US"]["source_date"], "2026-08-24")
        self.assertNotEqual(by_market["US"]["source_date"], "2026-08-25")
        self.assertLessEqual(
            by_market["US"]["knowledge_first_seen_at"], "2026-08-25T23:59:59Z"
        )

    def test_shallow_history_fails_closed_instead_of_claiming_first_seen(self):
        with mock.patch.object(READINESS, "_git", return_value="true\n"):
            with self.assertRaisesRegex(
                READINESS.ReadinessError,
                "KNOWLEDGE_PROVENANCE_SHALLOW_HISTORY",
            ):
                READINESS._exact_content_first_seen(
                    ROOT
                    / "data"
                    / "observations"
                    / "us_global_universe"
                    / "2026-08-25"
                    / "packet.json"
                )

    def test_empty_roots_fail_closed_without_inventing_population(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packet = READINESS.build_readiness(
                "2026-08-26",
                us_raw_root=root / "us-raw",
                us_data_root=root / "us-data",
                crypto_raw_root=root / "crypto-raw",
                crypto_data_root=root / "crypto-data",
                korea_data_root=root / "korea-data",
            )
            self.assertEqual(packet["ready_market_count"], 0)
            self.assertEqual(
                {row["status"] for row in packet["markets"]},
                {"SOURCE_COVERAGE_NOT_READY"},
            )
            self.assertTrue(all(row["record_count"] is None for row in packet["markets"]))

    def test_rehashed_semantic_tamper_is_rejected(self):
        packet = READINESS.build_readiness("2026-08-26")
        tampered = copy.deepcopy(packet)
        tampered["ready_market_count"] = 3
        tampered["status"] = "THREE_MARKET_SOURCE_COVERAGE_READY"
        tampered["payload_sha256"] = READINESS.payload_sha256(
            {key: value for key, value in tampered.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(READINESS.ReadinessError, "PACKET_DRIFT_OR_TAMPER"):
            READINESS.validate_readiness(tampered)

    def test_unknown_korea_directory_is_not_silently_trusted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "2026-08-26").mkdir()
            with self.assertRaisesRegex(
                READINESS.ReadinessError, "KOREA_POPULATION_VALIDATOR_NOT_IMPLEMENTED"
            ):
                READINESS.build_readiness(
                    "2026-08-26",
                    us_raw_root=root / "us-raw",
                    us_data_root=root / "us-data",
                    crypto_raw_root=root / "crypto-raw",
                    crypto_data_root=root / "crypto-data",
                    korea_data_root=root,
                )

    def test_atomic_output_only_after_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "readiness.json"
            packet = READINESS.build_readiness("2026-08-26")
            READINESS.validate_readiness(packet)
            READINESS.write_json_atomic(target, packet)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), packet)


if __name__ == "__main__":
    unittest.main()
