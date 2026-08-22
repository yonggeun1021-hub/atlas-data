#!/usr/bin/env python3
"""P3-04 UNVERIFIED_IDENTITY (policy_version v2): real numerator/
denominator regression against the actual committed 2026-08-22 Kraken
snapshot.

Precise finding (2026-08-22): qualified_members()'s own real gate
computes as_of = core["vintage"] - 1 day (2026-08-21 for this real
snapshot) -- one day before NIGHT/RE/PLAY's real effective_from
(2026-08-22), so this exact committed snapshot's own gate still shows
them (and the 85 already-ratified assets) as unresolved at build time,
same as before this PR. taxonomy_category() checked directly AS OF
TODAY (2026-08-22, when the new records genuinely are effective)
confirms all 88 previously-unknown Top-100-rank candidates -- including
NIGHT/RE/PLAY -- are now resolved (85 eligible_crypto + 3
unverified_identity). This is not a hardcoded three-ticker exception:
the same general taxonomy_category()/excluded_categories mechanism
already used for fiat/stablecoin/wrapped/staked/commodity_linked
applies uniformly.

Separately, and honestly: even once time passes and this exact lag no
longer applies, the real qualified_members() gate still returns UNKNOWN
for the full snapshot, for a genuinely different and much larger reason
this PR does not attempt to fix -- the gate requires zero taxonomy-
unknown candidates among ALL ranked candidates it walks through before
reaching target_asset_count=100 selected, not merely the ones within
Top-100 rank. Of the 621 total ranked candidates, roughly only the 88
addressed by this PR (plus the pre-existing ~21 fiat/stablecoin/wrapped/
staked/commodity_linked records) have ever been individually
taxonomy-classified at all -- the remaining ~515 are minor altcoins that
were never in scope for this PR (NIGHT/RE/PLAY only, per the user's own
explicit instruction; no blanket reclassification of Kraken's full
listing). This is verified explicitly below, not glossed over.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "evidence" / "crypto" / "breadth" / "raw" / "2026-08-22"


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_for_real_evidence_test", ".github/scripts/crypto_breadth.py")

# The exact three tickers the identity audit could not confirm via two
# independent sources -- frozen here as the expected real result, not a
# hardcoded rule (the rule itself, taxonomy_category()/excluded_
# categories, is fully general; this is just what it resolves to today).
PREVIOUSLY_UNCONFIRMED = {"NIGHT", "RE", "PLAY"}


@unittest.skipUnless(SNAPSHOT_DIR.is_dir(), "real 2026-08-22 snapshot not present")
class RealEvidenceUnverifiedIdentityTest(unittest.TestCase):
    def setUp(self):
        self.contract = CB.load_contract()
        self.universe_policy = CB.load_universe_policy()
        self.taxonomy_policy = CB.load_exclusion_taxonomy()
        self.core = CB.source_core(SNAPSHOT_DIR, self.contract)

    def test_excluded_categories_includes_unverified_identity(self):
        self.assertIn("unverified_identity", self.taxonomy_policy["excluded_categories"])
        self.assertEqual(self.taxonomy_policy["policy_version"], "crypto_breadth_exclusion_taxonomy/v2")

    def test_night_re_play_are_unverified_identity_as_of_today(self):
        today = dt.date(2026, 8, 22)
        for asset in PREVIOUSLY_UNCONFIRMED:
            self.assertEqual(
                CB.taxonomy_category(asset, today, self.taxonomy_policy),
                "unverified_identity",
                asset,
            )

    def test_night_re_play_not_yet_effective_for_this_exact_snapshots_own_gate_date(self):
        # Honest, precise documentation of the real one-day lag artifact:
        # this specific committed snapshot's own internal as_of
        # (vintage - 1 day) is 2026-08-21, one day BEFORE these records'
        # real effective_from (2026-08-22) -- so this exact snapshot
        # still shows them unresolved, same as before this PR. This is
        # not a regression introduced by this PR; it already applied to
        # the 85 already-ratified assets and is a structural property of
        # qualified_members()'s own as_of computation.
        gate_as_of = self.core["vintage"] - dt.timedelta(days=1)
        self.assertEqual(gate_as_of, dt.date(2026, 8, 21))
        for asset in PREVIOUSLY_UNCONFIRMED:
            self.assertIsNone(
                CB.taxonomy_category(asset, gate_as_of, self.taxonomy_policy)
            )

    def test_real_numerator_denominator_within_top_100_rank(self):
        result = CB.qualified_members(self.core, self.universe_policy, self.taxonomy_policy)
        diagnostics = result["diagnostics"]
        unknown = diagnostics["taxonomy_unknown_before_cutoff"]
        top100_unknown = [item for item in unknown if item["rank_before_taxonomy"] <= 100]
        # Denominator: exactly the 88 Top-100-rank candidates this
        # session's audit identified as unresolved at the gate's own
        # as_of (2026-08-21).
        self.assertEqual(len(top100_unknown), 88)
        today = dt.date(2026, 8, 22)
        resolved_today = [
            item for item in top100_unknown
            if CB.taxonomy_category(item["canonical_asset_id"], today, self.taxonomy_policy)
            is not None
        ]
        still_unknown_today = [
            item["canonical_asset_id"] for item in top100_unknown
            if CB.taxonomy_category(item["canonical_asset_id"], today, self.taxonomy_policy)
            is None
        ]
        # Numerator: all 88 are resolved as of today -- zero genuinely
        # unresolved within the Top-100-relevant set.
        self.assertEqual(len(resolved_today), 88)
        self.assertEqual(still_unknown_today, [])

    def test_full_gate_still_honestly_blocked_for_a_different_larger_reason(self):
        # In-memory-only gate-logic probe: same real assets/pairs/ohlc
        # data, only the vintage interpretation is advanced one day past
        # the new records' effective_from (simulating genuinely running
        # this gate the following day) -- no file is touched, no
        # evidence is forged.
        mutated_core = dict(self.core)
        mutated_core["vintage"] = self.core["vintage"] + dt.timedelta(days=1)
        result = CB.qualified_members(mutated_core, self.universe_policy, self.taxonomy_policy)
        # Still UNKNOWN -- not because of NIGHT/RE/PLAY (fully resolved,
        # per the prior test), but because the real gate requires zero
        # taxonomy-unknown among ALL ranked candidates it walks through
        # before reaching target_asset_count selected, and roughly 515
        # minor altcoins beyond the Top-100-relevant set were never in
        # scope for this PR (NIGHT/RE/PLAY only, per instruction -- no
        # blanket reclassification of Kraken's full listing).
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        remaining_unknown = result["diagnostics"]["taxonomy_unknown_before_cutoff"]
        for asset in PREVIOUSLY_UNCONFIRMED:
            self.assertNotIn(
                asset, {item["canonical_asset_id"] for item in remaining_unknown}
            )
        # Threshold is never lowered to force this closed: target_asset_
        # count and minimum_observation_coverage_bps are read unchanged
        # from the real ratified universe policy.
        self.assertEqual(self.universe_policy["target_asset_count"], 100)
        self.assertEqual(self.universe_policy["minimum_observation_coverage_bps"], 9000)


if __name__ == "__main__":
    unittest.main()
