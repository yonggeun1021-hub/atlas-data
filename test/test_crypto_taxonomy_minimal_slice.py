#!/usr/bin/env python3
"""P3-04 crypto taxonomy minimal ratified Slice regression.

Covers only the exact set of records ratified in this Slice -- 31 large,
well-established native crypto assets (source-coverage taxonomy only,
never an investability/liquidity/tradability claim) plus EURC as a
fiat-pegged stablecoin exclusion. Everything else (aliases needing
canonical-identity confirmation, and genuinely unresolved tickers) must
stay UNKNOWN -- this Slice never assumes an unclear asset is INCLUDE.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CRYPTO_BREADTH = _load("crypto_breadth", ".github/scripts/crypto_breadth.py")

RATIFIED_ELIGIBLE = [
    "XRP", "ADA", "ZEC", "SUI", "DOGE", "XMR", "TAO", "XLM", "LINK", "AVAX",
    "LTC", "UNI", "AAVE", "NEAR", "TRX", "BNB", "ICP", "HBAR", "INJ", "ALGO",
    "TON", "KAS", "BCH", "DASH", "RENDER", "DOT", "ATOM", "QNT", "FLR", "FIL", "SEI",
]

# Deliberately NOT ratified in this Slice -- must stay UNKNOWN.
NOT_RATIFIED = [
    "PROS", "POL", "LUNA", "SKY",  # alias/canonical-identity confirmation needed
    "RE", "PLAY", "US",  # unresolved -- no confirmed project identity
    "HYPE", "WEMIX", "PEPE", "SHIB",  # part of the wider 88 proposal, not this minimal Slice
]


class MinimalTaxonomySliceTest(unittest.TestCase):
    def setUp(self):
        self.policy = CRYPTO_BREADTH.load_exclusion_taxonomy()
        self.as_of = dt.date(2026, 8, 22)

    def test_ratified_assets_classify_as_eligible_crypto(self):
        for asset in RATIFIED_ELIGIBLE:
            with self.subTest(asset=asset):
                self.assertEqual(
                    CRYPTO_BREADTH.taxonomy_category(asset, self.as_of, self.policy),
                    "eligible_crypto",
                )

    def test_eurc_classifies_as_stablecoin_exclusion(self):
        self.assertEqual(
            CRYPTO_BREADTH.taxonomy_category("EURC", self.as_of, self.policy), "stablecoin"
        )

    def test_unratified_assets_stay_unknown_not_assumed_include(self):
        for asset in NOT_RATIFIED:
            with self.subTest(asset=asset):
                self.assertIsNone(
                    CRYPTO_BREADTH.taxonomy_category(asset, self.as_of, self.policy)
                )

    def test_ratification_is_not_retroactive(self):
        # effective_from=2026-08-22 -- the day before must still see these
        # assets as unclassified, exactly like every pre-existing record's
        # own effective-dating already enforces.
        before = dt.date(2026, 8, 21)
        for asset in RATIFIED_ELIGIBLE[:5]:
            with self.subTest(asset=asset):
                self.assertIsNone(
                    CRYPTO_BREADTH.taxonomy_category(asset, before, self.policy)
                )

    def test_existing_20_pre_slice_records_are_unchanged(self):
        pre_existing = {
            "BTC": "eligible_crypto", "ETH": "eligible_crypto", "SOL": "eligible_crypto",
            "USDT": "stablecoin", "USDC": "stablecoin", "DAI": "stablecoin",
            "WBTC": "wrapped", "TBTC": "wrapped", "PAXG": "commodity_linked",
            "XAUT": "commodity_linked", "AUD": "fiat", "CAD": "fiat",
        }
        for asset, expected in pre_existing.items():
            with self.subTest(asset=asset):
                self.assertEqual(
                    CRYPTO_BREADTH.taxonomy_category(asset, self.as_of, self.policy),
                    expected,
                )

    def test_p3_04_real_raw_snapshot_replay_stays_blocked_coverage_still_insufficient(self):
        # Same real committed 2026-08-22 raw Kraken snapshot the live
        # P1-CR-06 run (32548813057) captured, replayed before and after
        # this taxonomy change -- the outcome must stay blocked (this
        # Slice classifies 32 assets, far short of the 90%/Top-100
        # coverage gate; the threshold itself is never lowered to force
        # a pass).
        populate = _load(
            "crypto_forward_universe_populate",
            ".github/scripts/crypto_forward_universe_populate.py",
        )
        result = populate.populate("2026-08-22")
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["reason"], "BREADTH_SELECTION_UNKNOWN:TAXONOMY_COVERAGE_UNKNOWN")
        self.assertIsNone(result["path"])
        self.assertIsNone(result["payload_sha256"])


if __name__ == "__main__":
    unittest.main()
