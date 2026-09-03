#!/usr/bin/env python3
"""Lane P: HNT + SKR ratification in the crypto breadth exclusion taxonomy.

CIO decision (2026-09-04): HNT -> eligible_crypto, SKR -> eligible_crypto,
both bound to their retained Kraken online USD pair plus the project's own
official documentation (Helium for HNT, Solana Mobile for SKR). SN8 is
explicitly NOT classified and must remain absent from the taxonomy so the
existing fail_closed_unknown policy keeps returning UNKNOWN for it -- no
unverified_identity workaround, no new category, no retroactive credit
before the real ratification date.

This test proves the four required facts without writing any new NATURAL
evidence: it only reads the taxonomy config and the already-committed
2026-09-03 real Kraken snapshot, and it uses an in-memory-only vintage
mutation (same technique as test_crypto_breadth_unverified_identity_real_
evidence.py) to observe the gate's behavior on/after the real effective
date -- no file is written or backdated.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "evidence" / "crypto" / "breadth" / "raw" / "2026-09-03"


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_hnt_skr_ratification", ".github/scripts/crypto_breadth.py")

EFFECTIVE_FROM = dt.date(2026, 9, 4)
DAY_BEFORE = dt.date(2026, 9, 3)


class CryptoBreadthHntSkrTaxonomyRatificationTest(unittest.TestCase):
    def setUp(self):
        self.policy = CB.load_exclusion_taxonomy()

    def _record(self, asset_id: str) -> dict:
        records = [
            row for row in self.policy["records"]
            if row["canonical_asset_id"] == asset_id
        ]
        self.assertEqual(len(records), 1, asset_id)
        return records[0]

    def test_hnt_resolves_to_eligible_crypto(self):
        record = self._record("HNT")
        self.assertEqual(record["category"], "eligible_crypto")
        self.assertEqual(record["effective_from"], "2026-09-04")
        self.assertIsNone(record["effective_to"])
        self.assertIn("Kraken online USD pair", record["reason"])
        self.assertIn("Helium official documentation", record["reason"])
        self.assertEqual(
            CB.taxonomy_category("HNT", EFFECTIVE_FROM, self.policy),
            "eligible_crypto",
        )

    def test_skr_resolves_to_eligible_crypto(self):
        record = self._record("SKR")
        self.assertEqual(record["category"], "eligible_crypto")
        self.assertEqual(record["effective_from"], "2026-09-04")
        self.assertIsNone(record["effective_to"])
        self.assertIn("Kraken online USD pair", record["reason"])
        self.assertIn("Solana Mobile official documentation", record["reason"])
        self.assertEqual(
            CB.taxonomy_category("SKR", EFFECTIVE_FROM, self.policy),
            "eligible_crypto",
        )

    def test_sn8_remains_unknown_fail_closed_not_classified(self):
        # No record at all -- CIO explicitly withheld SN8. Confirm it is
        # genuinely absent (not classified under any category, including
        # unverified_identity) and that the general fail_closed_unknown
        # mechanism -- not a hardcoded SN8 rule -- is what returns None.
        self.assertNotIn(
            "SN8",
            {row["canonical_asset_id"] for row in self.policy["records"]},
        )
        self.assertEqual(self.policy["unknown_asset_policy"], "fail_closed_unknown")
        self.assertIsNone(
            CB.taxonomy_category("SN8", EFFECTIVE_FROM, self.policy)
        )
        self.assertIsNone(
            CB.taxonomy_category("SN8", dt.date(2099, 1, 1), self.policy)
        )

    def test_no_retroactive_natural_credit_before_effective_date(self):
        for asset_id in ("HNT", "SKR"):
            self.assertIsNone(
                CB.taxonomy_category(asset_id, DAY_BEFORE, self.policy),
                asset_id,
            )
            self.assertEqual(
                CB.taxonomy_category(asset_id, EFFECTIVE_FROM, self.policy),
                "eligible_crypto",
                asset_id,
            )

    def test_authority_and_schema_unchanged(self):
        # Same policy header as before this PR: no new category, same
        # source, same eligible_category, same excluded_categories list,
        # still RATIFIED at schema/header level, record ordering intact.
        self.assertEqual(self.policy["schema_version"], 1)
        self.assertEqual(
            self.policy["policy_version"], "crypto_breadth_exclusion_taxonomy/v2"
        )
        self.assertEqual(self.policy["approval_status"], "RATIFIED")
        self.assertEqual(self.policy["source_name"], "kraken_spot_market_data")
        self.assertEqual(self.policy["eligible_category"], "eligible_crypto")
        self.assertEqual(
            self.policy["excluded_categories"],
            [
                "commodity_linked",
                "fiat",
                "stablecoin",
                "staked",
                "unverified_identity",
                "wrapped",
            ],
        )
        valid_categories = {
            self.policy["eligible_category"], *self.policy["excluded_categories"]
        }
        for record in self.policy["records"]:
            self.assertIn(record["category"], valid_categories)


@unittest.skipUnless(SNAPSHOT_DIR.is_dir(), "real 2026-09-03 snapshot not present")
class CryptoBreadthHntSkrBreadthStillFailClosedOnSn8Test(unittest.TestCase):
    """E2E acceptance: taxonomy(HNT, SKR) -> resolved, taxonomy(SN8) ->
    UNKNOWN, breadth remains fail-closed on SN8. Uses only the already-
    committed real 2026-09-03 snapshot; the vintage is advanced in memory
    only (no file written/backdated) so the gate's own as_of moves past
    the real 2026-09-04 effective_from -- this is regression plumbing,
    not new NATURAL evidence."""

    def setUp(self):
        self.contract = CB.load_contract()
        self.universe_policy = CB.load_universe_policy()
        self.taxonomy_policy = CB.load_exclusion_taxonomy()
        self.core = CB.source_core(SNAPSHOT_DIR, self.contract)

    def test_hnt_and_skr_are_present_and_ranked_in_this_real_snapshot(self):
        pair_ids = set(self.core["pairs"])
        self.assertIn("HNT/USD", pair_ids)
        self.assertIn("SKR/USD", pair_ids)
        self.assertIn("SN8/USD", pair_ids)

    def test_breadth_still_fail_closed_because_sn8_remains_unresolved(self):
        # Advance the in-memory vintage so the gate's own as_of
        # (vintage - 1 day) reaches the real 2026-09-04 effective_from.
        mutated_core = dict(self.core)
        mutated_core["vintage"] = EFFECTIVE_FROM + dt.timedelta(days=1)
        result = CB.qualified_members(
            mutated_core, self.universe_policy, self.taxonomy_policy
        )
        unknown_ids = {
            item["canonical_asset_id"]
            for item in result["diagnostics"]["taxonomy_unknown_before_cutoff"]
        }
        # HNT and SKR are resolved -- no longer blocking the gate.
        self.assertNotIn("HNT", unknown_ids)
        self.assertNotIn("SKR", unknown_ids)
        # SN8 remains genuinely unresolved (CIO withheld it), and that
        # alone is still sufficient to keep the whole gate UNKNOWN --
        # this PR does not, and must not, claim BREADTH PASS.
        self.assertIn("SN8", unknown_ids)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        # Thresholds are read unchanged from the real ratified universe
        # policy -- this PR does not touch regime/universe thresholds.
        self.assertEqual(self.universe_policy["target_asset_count"], 100)
        self.assertEqual(
            self.universe_policy["minimum_observation_coverage_bps"], 9000
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
