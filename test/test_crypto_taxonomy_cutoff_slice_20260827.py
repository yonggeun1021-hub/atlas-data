#!/usr/bin/env python3
"""P1-CR-06 cutoff-relevant taxonomy Slice (effective 2026-08-27).

The Slice ratifies source identities only.  It does not change the Top-100 or
90% gates and it does not grant investability, Regime, Production, or trading
authority.  The retained 2026-08-27 snapshot has an as-of date of 2026-08-26,
so the real replay must remain unchanged; a non-persisted one-day policy
counterfactual proves only that these exact 42 records are sufficient for the
existing cutoff-aware algorithm if the next natural ranking is unchanged.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_cutoff_slice_20260827", ".github/scripts/crypto_breadth.py")

SLICE_ASSETS = [
    "ACU", "APT", "ARB", "ASTER", "BABY", "BABYSHARK", "BONK", "CVX",
    "DCR", "DOG", "EIGEN", "ESP", "ETHFI", "FLOKI", "ICNT", "JASMY",
    "JTO", "KNTQ", "KTA", "LIT", "M", "MANA", "MELANIA", "MINA", "NIL",
    "OP", "PENDLE", "PLUME", "POPCAT", "PYTH", "RIZE", "SCRT", "STRK",
    "STX", "SYRUP", "TIA", "VIRTUAL", "WIF", "XAN", "XNY", "XPL", "ZRO",
]


class CryptoTaxonomyCutoffSlice20260827Test(unittest.TestCase):
    def setUp(self):
        self.policy = CB.load_exclusion_taxonomy()
        self.records = {
            row["canonical_asset_id"]: row
            for row in self.policy["records"]
            if row["effective_from"] == "2026-08-27"
        }

    def test_slice_is_exactly_42_unique_source_identity_records(self):
        self.assertEqual(len(SLICE_ASSETS), 42)
        self.assertEqual(len(set(SLICE_ASSETS)), 42)
        self.assertEqual(sorted(self.records), SLICE_ASSETS)
        for asset in SLICE_ASSETS:
            with self.subTest(asset=asset):
                record = self.records[asset]
                self.assertEqual(record["category"], "eligible_crypto")
                self.assertIsNone(record["effective_to"])
                self.assertIn("retained Kraken online USD pair", record["reason"])

    def test_slice_is_effective_dated_and_never_backdated(self):
        before = dt.date(2026, 8, 26)
        effective = dt.date(2026, 8, 27)
        for asset in SLICE_ASSETS:
            with self.subTest(asset=asset):
                self.assertIsNone(CB.taxonomy_category(asset, before, self.policy))
                self.assertEqual(
                    CB.taxonomy_category(asset, effective, self.policy),
                    "eligible_crypto",
                )

    def test_top_100_and_90_percent_gates_are_unchanged(self):
        universe = CB.load_universe_policy()
        self.assertEqual(universe["target_asset_count"], 100)
        self.assertEqual(universe["minimum_observation_coverage_bps"], 9000)
        self.assertEqual(self.policy["policy_version"], "crypto_breadth_exclusion_taxonomy/v2")

    def test_real_20260827_replay_remains_pit_blocked_before_effective_date(self):
        result = CB.build_transform(ROOT / "evidence/crypto/breadth/raw/2026-08-27")
        self.assertEqual(result["as_of_date"], "2026-08-26")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        self.assertEqual(result["universe"]["known_eligible_count_so_far"], 87)
        self.assertEqual(len(result["universe"]["taxonomy_unknown_before_cutoff"]), 515)

    def test_logic_only_counterfactual_closes_existing_cutoff_without_new_authority(self):
        # This temporary policy is never persisted or treated as PIT evidence.
        # It isolates the existing selection algorithm from the natural next-day
        # capture by making only this Slice visible to the retained 8/27 replay.
        policy = json.loads(
            (ROOT / "config/crypto_breadth_exclusion_taxonomy.json").read_text(
                encoding="utf-8"
            )
        )
        for row in policy["records"]:
            if row["canonical_asset_id"] in SLICE_ASSETS:
                row["effective_from"] = "2026-08-26"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logic-only-taxonomy.json"
            path.write_text(
                json.dumps(policy, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result = CB.build_transform(
                ROOT / "evidence/crypto/breadth/raw/2026-08-27",
                exclusion_taxonomy_path=path,
            )

        self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
        self.assertIsNone(result["unknown_reason"])
        self.assertEqual(result["universe"]["selected_asset_count"], 100)
        self.assertEqual(result["universe"]["observation_coverage_bps"], 10000)
        self.assertEqual(result["universe"]["taxonomy_unknown_before_cutoff"], [])
        self.assertEqual(result["alt_participation"]["asset_count"], 99)
        self.assertEqual(result["alt_participation"]["classification"], "UNDEFINED")
        self.assertFalse(result["alt_participation"]["thresholds_applied"])
        for key in (
            "breadth_classification_authorized",
            "threshold_authorized",
            "regime_score_authorized",
            "production_wiring_authorized",
            "trading_action_authorized",
        ):
            self.assertFalse(result[key], key)


if __name__ == "__main__":
    unittest.main()
