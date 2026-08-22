#!/usr/bin/env python3
"""P3-04 crypto taxonomy Identity Slice regression.

Covers the 53 assets identity-confirmed by 2+ independent official
sources (Kraken's own listing + a second independent public source,
usually CoinGecko) in this Slice: POL/SKY/LUNA rebrand-and-fork
continuity, PROS/US cross-project identity disambiguation, and 48
further native crypto projects. RE/PLAY had no confirmed identity from
2+ sources as of this Slice, and NIGHT had a genuine ticker collision
between two unrelated real projects sharing the name "Midnight"/ticker
NIGHT (exactly the case this Slice's own rule -- "ticker만으로 identity
확정 금지" -- exists to catch) -- all three stayed UNKNOWN here. They
were later explicitly ratified as `unverified_identity` (policy_version
v2, 2026-08-22, a real excluded category, never a guessed identity) --
see test_crypto_breadth_unverified_identity_real_evidence.py.
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

# Rebrand/fork continuity -- resolved with 2+ official sources each.
REBRAND_CONTINUITY = ["POL", "SKY", "LUNA"]

# Cross-project identity disambiguation -- resolved with 2+ sources each.
DISAMBIGUATED_IDENTITY = ["PROS", "US"]

REMAINING_IDENTITY_CONFIRMED = [
    "HYPE", "PUMP", "ONDO", "CRV", "PEPE", "WEMIX", "SHIB", "ENA", "SYN",
    "WLD", "FARTCOIN", "BICO", "KAITO", "FET", "PENGU", "GWEI", "HFT",
    "COTI", "MON", "EUL", "XDC", "XCN", "TRUMP", "AERO", "AKT", "MORPHO",
    "CSPR", "JUP", "LDO", "AKE", "ESPORTS", "CC", "CAP", "VVV", "BLESS",
    "UAI", "SPX", "USELESS", "BMT", "BILL", "EVAA", "APR", "ZBCN",
    "BLUAI", "VELVET", "PTB", "ZAMA", "WLFI",
]

ALL_RATIFIED = REBRAND_CONTINUITY + DISAMBIGUATED_IDENTITY + REMAINING_IDENTITY_CONFIRMED

# Genuinely unresolved as of this Slice -- later explicitly ratified as
# unverified_identity (policy_version v2, 2026-08-22), a real excluded
# category, not UNKNOWN. See test_crypto_breadth_unverified_identity_
# real_evidence.py for that classification's regression coverage.
STILL_UNKNOWN: list[str] = []


class IdentitySliceTest(unittest.TestCase):
    def setUp(self):
        self.policy = CRYPTO_BREADTH.load_exclusion_taxonomy()
        self.as_of = dt.date(2026, 8, 22)

    def test_total_ratified_count_is_53(self):
        self.assertEqual(len(ALL_RATIFIED), 53)
        self.assertEqual(len(set(ALL_RATIFIED)), 53)

    def test_all_53_classify_as_eligible_crypto(self):
        for asset in ALL_RATIFIED:
            with self.subTest(asset=asset):
                self.assertEqual(
                    CRYPTO_BREADTH.taxonomy_category(asset, self.as_of, self.policy),
                    "eligible_crypto",
                )

    def test_rebrand_continuity_assets_use_the_current_successor_ticker(self):
        # POL (MATIC successor), SKY (MKR successor), LUNA (Terra 2.0, not
        # LUNC) -- each resolved via 2 independent official sources
        # confirming the exact successor/distinct-chain relationship, not
        # from ticker string alone.
        for asset in REBRAND_CONTINUITY:
            with self.subTest(asset=asset):
                self.assertEqual(
                    CRYPTO_BREADTH.taxonomy_category(asset, self.as_of, self.policy),
                    "eligible_crypto",
                )
        # The legacy/alternate tickers this Slice deliberately does NOT
        # touch (MATIC/MKR/LUNC records were never in this repo's raw
        # snapshot and are not added here).
        for legacy in ("MATIC", "MKR", "LUNC"):
            with self.subTest(legacy=legacy):
                self.assertIsNone(
                    CRYPTO_BREADTH.taxonomy_category(legacy, self.as_of, self.policy)
                )

    def test_still_unknown_assets_are_not_assumed_include(self):
        for asset in STILL_UNKNOWN:
            with self.subTest(asset=asset):
                self.assertIsNone(
                    CRYPTO_BREADTH.taxonomy_category(asset, self.as_of, self.policy)
                )

    def test_ratification_is_not_retroactive(self):
        before = dt.date(2026, 8, 21)
        for asset in ALL_RATIFIED[:5]:
            with self.subTest(asset=asset):
                self.assertIsNone(
                    CRYPTO_BREADTH.taxonomy_category(asset, before, self.policy)
                )

    def test_prior_minimal_slice_31_plus_eurc_are_unchanged(self):
        prior_eligible = [
            "XRP", "ADA", "ZEC", "SUI", "DOGE", "XMR", "TAO", "XLM", "LINK",
            "AVAX", "LTC", "UNI", "AAVE", "NEAR", "TRX", "BNB", "ICP",
            "HBAR", "INJ", "ALGO", "TON", "KAS", "BCH", "DASH", "RENDER",
            "DOT", "ATOM", "QNT", "FLR", "FIL", "SEI",
        ]
        for asset in prior_eligible:
            with self.subTest(asset=asset):
                self.assertEqual(
                    CRYPTO_BREADTH.taxonomy_category(asset, self.as_of, self.policy),
                    "eligible_crypto",
                )
        self.assertEqual(
            CRYPTO_BREADTH.taxonomy_category("EURC", self.as_of, self.policy),
            "stablecoin",
        )

    def test_p3_04_real_raw_snapshot_replay_stays_blocked_coverage_still_insufficient(self):
        # Same real committed 2026-08-22 Kraken snapshot replayed again --
        # 53 newly-identity-confirmed assets (85 total across both Slices)
        # still fall short of the 90%/Top-100 coverage gate; the threshold
        # itself is never lowered to force a pass.
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
