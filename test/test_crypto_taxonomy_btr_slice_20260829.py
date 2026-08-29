#!/usr/bin/env python3
"""P1-CR-06 cutoff taxonomy slice for Bitlayer BTR.

The 2026-08-29 natural review inventory contains one cutoff-relevant unknown,
BTR/USD.  The retained Kraken catalog and two independent official identity
sources resolve it as Bitlayer's governance token.  The record is effective
2026-08-29 and therefore must not rewrite the retained snapshot whose own
evaluation as-of date is 2026-08-28.
"""
from __future__ import annotations

import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "evidence/crypto/breadth/raw/2026-08-29"


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_btr_slice", ".github/scripts/crypto_breadth.py")
POPULATE = _load(
    "crypto_forward_universe_btr_slice",
    ".github/scripts/crypto_forward_universe_populate.py",
)


@unittest.skipUnless(SNAPSHOT.is_dir(), "real 2026-08-29 snapshot not present")
class CryptoTaxonomyBtrSlice20260829Test(unittest.TestCase):
    def setUp(self):
        self.policy = CB.load_exclusion_taxonomy()
        records = [
            row for row in self.policy["records"]
            if row["canonical_asset_id"] == "BTR"
        ]
        self.assertEqual(len(records), 1)
        self.record = records[0]

    def test_exact_identity_is_bound_to_retained_enabled_online_kraken_pair(self):
        with gzip.open(SNAPSHOT / "kraken_assets.json.gz", "rt") as handle:
            asset = json.load(handle)["result"]["BTR"]
        with gzip.open(SNAPSHOT / "kraken_asset_pairs.json.gz", "rt") as handle:
            pair = json.load(handle)["result"]["BTR/USD"]
        self.assertEqual(asset["status"], "enabled")
        self.assertEqual(asset["altname"], "BTR")
        self.assertEqual(pair["status"], "online")
        self.assertEqual(pair["base"], "BTR")
        self.assertEqual(pair["quote"], "USD")
        self.assertEqual(pair["wsname"], "BTR/USD")

    def test_slice_is_effective_dated_and_not_backdated(self):
        self.assertEqual(self.record["category"], "eligible_crypto")
        self.assertEqual(self.record["effective_from"], "2026-08-29")
        self.assertIsNone(self.record["effective_to"])
        self.assertIn("Kraken official listing notice", self.record["reason"])
        self.assertIn("Bitlayer official BTR token documentation", self.record["reason"])
        self.assertIsNone(
            CB.taxonomy_category("BTR", dt.date(2026, 8, 28), self.policy)
        )
        self.assertEqual(
            CB.taxonomy_category("BTR", dt.date(2026, 8, 29), self.policy),
            "eligible_crypto",
        )

    def test_top_100_and_90_percent_gates_are_unchanged(self):
        universe = CB.load_universe_policy()
        self.assertEqual(universe["target_asset_count"], 100)
        self.assertEqual(universe["minimum_observation_coverage_bps"], 9000)

    def test_retained_natural_replay_stays_blocked_at_its_original_pit(self):
        result = CB.build_transform(SNAPSHOT)
        self.assertEqual(result["as_of_date"], "2026-08-28")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["unknown_reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        self.assertEqual(
            [
                row["canonical_asset_id"]
                for row in result["universe"]["taxonomy_unknown_before_cutoff"]
            ],
            ["BTR"],
        )

    def test_logic_only_counterfactual_closes_exact_gap_without_authority(self):
        policy = json.loads(
            (ROOT / "config/crypto_breadth_exclusion_taxonomy.json").read_text(
                encoding="utf-8"
            )
        )
        record = next(
            row for row in policy["records"]
            if row["canonical_asset_id"] == "BTR"
        )
        record["effective_from"] = "2026-08-28"
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = Path(tmp) / "logic-only-taxonomy.json"
            policy_path.write_text(
                json.dumps(policy, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result = CB.build_transform(
                SNAPSHOT,
                exclusion_taxonomy_path=policy_path,
            )
            population = POPULATE.rebuild(
                "2026-08-29",
                taxonomy_path=policy_path,
            )

        self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
        self.assertIsNone(result["unknown_reason"])
        self.assertEqual(result["universe"]["selected_asset_count"], 100)
        self.assertEqual(result["universe"]["observation_coverage_bps"], 10000)
        self.assertEqual(result["universe"]["taxonomy_unknown_before_cutoff"], [])
        self.assertEqual(population["status"], "ready")
        packet = population["record"]["packet"]
        self.assertEqual(packet["selected_count"], 100)
        self.assertEqual(packet["target_count"], 100)
        self.assertEqual(len(packet["source_attribute_rows"]), 100)
        self.assertTrue(packet["authority"]["breadth_source_coverage_universe_only"])
        for key, value in packet["authority"].items():
            if key != "breadth_source_coverage_universe_only":
                self.assertFalse(value, key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
