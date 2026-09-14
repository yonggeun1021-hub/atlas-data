#!/usr/bin/env python3
"""Conditional LIGHTER addition from ratification
CRYPTO-BREADTH-TAXONOMY-ADDITIONS-20260914.

The Kraken identity was confirmed from Kraken's official Lighter asset page
(shortcode LIGHTER, LIGHTER-usd trading link, Lighter perp DEX token) and
Lighter official documentation. LIGHTER is eligible_crypto effective
2026-09-16, never earlier. Kraken LIT (Litentry) is a different asset and is
unchanged. Date-independent: reads only committed config and the committed
2026-09-14 raw Kraken snapshot.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "evidence" / "crypto" / "breadth" / "raw" / "2026-09-14"


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_lighter_identity_20260914", ".github/scripts/crypto_breadth.py")
EFFECTIVE_FROM = dt.date(2026, 9, 16)


class LighterIdentityRecordTest(unittest.TestCase):
    def setUp(self):
        self.policy = CB.load_exclusion_taxonomy()

    def _rows(self, asset_id: str) -> list:
        return [
            row for row in self.policy["records"]
            if row["canonical_asset_id"] == asset_id
        ]

    def test_lighter_record(self):
        rows = self._rows("LIGHTER")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "eligible_crypto")
        self.assertEqual(rows[0]["effective_from"], "2026-09-16")
        self.assertIsNone(rows[0]["effective_to"])
        self.assertIn("Kraken", rows[0]["reason"])
        self.assertIn("Lighter official documentation", rows[0]["reason"])

    def test_no_backfill_before_effective_date(self):
        self.assertIsNone(
            CB.taxonomy_category("LIGHTER", EFFECTIVE_FROM - dt.timedelta(days=1), self.policy)
        )
        self.assertEqual(
            CB.taxonomy_category("LIGHTER", EFFECTIVE_FROM, self.policy), "eligible_crypto"
        )

    def test_kraken_lit_litentry_record_unchanged(self):
        rows = self._rows("LIT")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["effective_from"], "2026-08-27")
        self.assertIn("Litentry", rows[0]["reason"])

    @unittest.skipUnless(SNAPSHOT_DIR.is_dir(), "committed 2026-09-14 snapshot not present")
    def test_literal_kraken_identity_is_retained(self):
        core = CB.source_core(SNAPSHOT_DIR)
        self.assertIn("LIGHTER", core["assets"])
        self.assertIn("LIGHTER/USD", core["pairs"])
        self.assertEqual(core["pairs"]["LIGHTER/USD"]["base"], "LIGHTER")
        self.assertEqual(core["pairs"]["LIGHTER/USD"]["quote"], "USD")


if __name__ == "__main__":
    unittest.main(verbosity=2)
