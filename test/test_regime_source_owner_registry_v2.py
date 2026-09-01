#!/usr/bin/env python3
"""Static, fail-closed regressions for the Gate 2 source/owner v2 registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "regime_source_owner_registry_v2.json"


def file_sha256(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


class RegimeSourceOwnerRegistryV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_exact_decision_identity_and_common_v1_separation(self):
        value = self.registry
        self.assertEqual(value["schema_version"], 2)
        self.assertEqual(
            value["registry_version"], "regime_source_owner_registry/v2"
        )
        self.assertEqual(value["registry_mode"], "SOURCE_OWNER_ARCHITECTURE_ONLY")
        self.assertEqual(
            value["decision"],
            {
                "identity": "CIO-GATE2-3MARKET-REGIME-SOURCE-FIRST-B-2026-09-01",
                "status": "CIO_APPROVED_ARCHITECTURE_SCOPE_ONLY",
                "option": "OPTION_B_SOURCE_FIRST_LAYERED_RATIFICATION",
                "effective_date": "2026-09-01",
                "packet_sha256": "bdeb9b9970c71d38a9650f2374b9078e1f76ef4eeddf5acb34c6a890e9b7591c",
            },
        )
        common = value["common_v1_alignment"]
        self.assertEqual(common["policy_status"], "RATIFIED_PAPER_BASELINE_V1")
        self.assertEqual(
            common["repository_v2_alignment_status"],
            "ALIGNED_ARCHITECTURE_ONLY_RUNTIME_NOT_WIRED",
        )
        self.assertEqual(common["required_axes"], [
            "TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"
        ])
        self.assertEqual(set(common["weights"].values()), {1})
        self.assertFalse(
            common["market_specific_normalization_freshness_and_replay_inherited"]
        )
        legacy = common["legacy_runtime_contract"]
        self.assertEqual(legacy["status"], "UNCHANGED_FAIL_CLOSED")
        self.assertEqual(file_sha256(legacy["path"]), legacy["sha256"])

    def test_market_and_aggregate_acceptance_remain_blocked(self):
        markets = self.registry["markets"]
        self.assertEqual(
            markets["KRX"]["acceptance_status"],
            "BLOCKED_SIGNED_NORMALIZATION_TTL_PIT_REPLAY",
        )
        self.assertEqual(
            markets["US"]["acceptance_status"],
            "BLOCKED_FINISHED_SESSION_TTL_PIT_REPLAY",
        )
        self.assertEqual(
            markets["CRYPTO"]["acceptance_status"],
            "BLOCKED_OVERALL_FRESHNESS_PIT_REPLAY",
        )
        self.assertEqual(
            self.registry["aggregate"]["acceptance_status"],
            "BLOCKED_MIXED_EVIDENCE_CLASSES",
        )
        self.assertEqual(
            self.registry["aggregate"]["runtime_output_status"],
            "UNKNOWN/HOLD/WAIT",
        )
        self.assertFalse(self.registry["aggregate"]["pin_update_allowed"])
        for market in markets.values():
            self.assertEqual(
                market["architecture_status"],
                "CIO_APPROVED_ARCHITECTURE_SCOPE_ONLY",
            )
            self.assertEqual(market["pit_replay_acceptance"], "NOT_ACCEPTED")

    def test_krx_reuses_exact_official_source_and_existing_owner(self):
        krx = self.registry["markets"]["KRX"]
        self.assertEqual(krx["source_scope"], "KRX_OFFICIAL_FIVE_AXIS")
        self.assertEqual(
            krx["source_owner"]["source_name"],
            "KRX_OPEN_API_STOCK_AND_INDEX_DAILY",
        )
        self.assertEqual(
            krx["natural_receipt_owner"]["owner_status"],
            "BOUND_EXISTING_NATURAL_READ_ONLY",
        )
        self.assertIsNone(krx["signed_normalization_policy"])
        self.assertIsNone(krx["ttl_seconds"])
        self._assert_pins(krx["source_owner"])
        self._assert_pins(krx["natural_receipt_owner"])

    def test_us_exact_calendar_proxy_and_pending_natural_owners(self):
        us = self.registry["markets"]["US"]
        self.assertEqual(
            [row["source_id"] for row in us["official_calendar"]["sources"]],
            ["NYSE_HOLIDAYS_AND_TRADING_HOURS", "NASDAQ_TRADING_SCHEDULE"],
        )
        proxy = us["paper_proxy"]
        self.assertEqual(proxy["exact_15_etfs"], [
            "IWM", "QQQ", "SMH", "SPY", "XLB", "XLC", "XLE", "XLF",
            "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
        ])
        self.assertEqual(proxy["leadership_12_group_proxies"], [
            "SMH", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP",
            "XLRE", "XLU", "XLV", "XLY",
        ])
        self.assertEqual(proxy["leadership_benchmark"], "SPY")
        self.assertEqual(proxy["leadership_window_finalized_sessions"], 20)
        self.assertIn("IMPLEMENTATION_PENDING", us["finished_session_owner"]["owner_status"])
        self.assertIn("IMPLEMENTATION_PENDING", us["natural_receipt_owner"]["owner_status"])
        self.assertIsNone(us["signed_normalization_policy"])
        self.assertIsNone(us["ttl_seconds"])
        self._assert_pins(us["source_owner"])
        self._assert_pins(us["finished_session_owner"])
        self._assert_pins(us["natural_receipt_owner"])

    def test_crypto_reuses_ratified_sources_without_promoting_group_layer(self):
        crypto = self.registry["markets"]["CRYPTO"]
        self.assertEqual(crypto["breadth_source"]["policy_status"], "RATIFIED")
        self.assertEqual(crypto["leadership_source"]["bucket_layer"], "BTC_ETH_ALT")
        self.assertEqual(
            crypto["leadership_source"]["sector_chain_group_layer"],
            "UNKNOWN_GROUP_LAYER",
        )
        self.assertEqual(
            crypto["leadership_source"]["group_coverage_policy_status"],
            "UNRATIFIED",
        )
        self.assertIn(
            "IMPLEMENTATION_PENDING",
            crypto["natural_receipt_owner"]["owner_status"],
        )
        self.assertIsNone(crypto["overall_freshness_policy"])
        self._assert_pins(crypto["breadth_source"])
        self._assert_pins(crypto["leadership_source"])
        self._assert_pins(crypto["source_owner"])
        self._assert_pins(crypto["status_owner"])

    def test_no_authority_promotion(self):
        authority = self.registry["authority"]
        self.assertTrue(authority["source_owner_architecture_authorized"])
        for key, value in authority.items():
            if key != "source_owner_architecture_authorized":
                self.assertFalse(value, key)
        self.assertEqual(set(self.registry["forbidden_promotions"]), {
            "MARKET_POLICY_RATIFICATION",
            "SIGNED_NORMALIZATION_RATIFICATION",
            "TTL_OR_FRESHNESS_RATIFICATION",
            "PIT_REPLAY_ACCEPTANCE",
            "REGIME_RESULT_RATIFICATION",
            "FIXTURE_OR_BASELINE_PROMOTION",
            "THRESHOLD_OVERRIDE",
            "CANDIDATE_FORCING",
        })

    def test_owner_ids_are_unique_and_all_pinned_paths_exist(self):
        owners = []
        for market in self.registry["markets"].values():
            for key, value in market.items():
                if key.endswith("owner") and isinstance(value, dict):
                    owners.append(value["owner_id"])
                    for path_key, path_value in value.items():
                        if path_key.endswith("_path"):
                            self.assertTrue((ROOT / path_value).is_file(), path_value)
        owners.append(self.registry["aggregate"]["owner_id"])
        self.assertEqual(len(owners), len(set(owners)))
        self._assert_pins(self.registry["aggregate"])

    def _assert_pins(self, value: dict) -> None:
        for key, expected in value.items():
            if not key.endswith("_sha256"):
                continue
            path_key = key.removesuffix("_sha256") + "_path"
            if path_key in value:
                self.assertEqual(file_sha256(value[path_key]), expected, path_key)


if __name__ == "__main__":
    unittest.main()
