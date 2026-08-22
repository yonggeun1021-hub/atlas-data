#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- asset identity / PIT-eligible universe
regression (CIO review round 3, flaws 1 and 2/condition-6b).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import asset_identity as ai  # noqa: E402


class CryptoTaxonomyRealFileTests(unittest.TestCase):
    def test_real_taxonomy_file_exists_and_is_ratified(self):
        doc = json.loads(ai.CRYPTO_TAXONOMY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(doc["approval_status"], "RATIFIED")

    def test_eligible_records_all_have_a_real_effective_from_date(self):
        for rec in ai.crypto_eligible_records():
            self.assertRegex(rec["effective_from"], r"^\d{4}-\d{2}-\d{2}$")

    def test_eligible_records_are_a_small_fraction_of_the_full_taxonomy_file(self):
        doc = json.loads(ai.CRYPTO_TAXONOMY_PATH.read_text(encoding="utf-8"))
        eligible = ai.crypto_eligible_records()
        self.assertLess(len(eligible), len(doc["records"]))
        self.assertGreater(len(eligible), 0)


class PitEligibilityIsRealAndDateGatedTests(unittest.TestCase):
    def test_empty_before_the_earliest_real_effective_from(self):
        self.assertEqual(ai.crypto_pit_eligible_asset_ids("2020-01-01"), set())
        self.assertEqual(ai.crypto_pit_eligible_asset_ids("2026-07-22"), set())

    def test_nonempty_on_and_after_a_real_ratification_date(self):
        eligible = ai.crypto_pit_eligible_asset_ids("2026-08-22")
        self.assertGreater(len(eligible), 0)

    def test_eligibility_set_is_monotonically_non_decreasing_over_time(self):
        d1 = ai.crypto_pit_eligible_asset_ids("2026-08-19")
        d2 = ai.crypto_pit_eligible_asset_ids("2026-08-20")
        d3 = ai.crypto_pit_eligible_asset_ids("2026-08-22")
        self.assertTrue(d1.issubset(d2))
        self.assertTrue(d2.issubset(d3))

    def test_pair_ids_intersect_with_known_committed_pairs_never_invent_one(self):
        known = {"AAVE/USD"}  # deliberately tiny, to prove intersection actually filters
        pairs = ai.crypto_pit_eligible_pair_ids("2026-08-22", known)
        self.assertTrue(pairs.issubset(known))

    def test_btc_excluded_from_breadth_pair_ids_to_avoid_double_counting(self):
        known = {"BTC/USD", "AAVE/USD"}
        pairs = ai.crypto_pit_eligible_pair_ids("2026-08-22", known)
        self.assertNotIn("BTC/USD", pairs)


class AssetIdentityStatusTests(unittest.TestCase):
    def test_btc_is_always_pass(self):
        self.assertEqual(ai.asset_identity_status("BTC", "2026-07-22"), "PASS")

    def test_kr_code_in_universe_passes(self):
        self.assertEqual(ai.asset_identity_status("005930", "2026-08-13", {"005930"}), "PASS")

    def test_kr_code_not_in_universe_fails(self):
        self.assertEqual(ai.asset_identity_status("999999", "2026-08-13", {"005930"}), "FAIL")

    def test_kr_code_without_universe_context_is_not_computable(self):
        self.assertEqual(ai.asset_identity_status("005930", "2026-08-13", None), "NOT_COMPUTABLE")

    def test_crypto_pair_eligible_asset_passes_on_ratified_date(self):
        eligible_now = ai.crypto_pit_eligible_asset_ids("2026-08-22")
        some_asset = next(iter(eligible_now - ai.BREADTH_EXCLUDED_ASSETS))
        self.assertEqual(ai.asset_identity_status(f"{some_asset}/USD", "2026-08-22"), "PASS")

    def test_crypto_pair_before_ratification_fails(self):
        eligible_now = ai.crypto_pit_eligible_asset_ids("2026-08-22")
        some_asset = next(iter(eligible_now - ai.BREADTH_EXCLUDED_ASSETS))
        self.assertEqual(ai.asset_identity_status(f"{some_asset}/USD", "2026-07-22"), "FAIL")

    def test_unrecognized_subject_shape_is_not_computable(self):
        self.assertEqual(ai.asset_identity_status("???", "2026-08-13"), "NOT_COMPUTABLE")


if __name__ == "__main__":
    unittest.main()
