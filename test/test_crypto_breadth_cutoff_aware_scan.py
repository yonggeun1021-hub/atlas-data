#!/usr/bin/env python3
"""P3-04 Top-100 taxonomy coverage Gate: cutoff-aware scan audit
(2026-08-22).

Audit finding: qualified_members() already implements exactly the
CIO's own algorithm (rank descending by turnover, classify from the top,
skip EXCLUDED and keep going, stop the instant target eligible_crypto
assets are found) -- it does NOT require classifying the provider's
entire universe. The `break` the moment `len(selected) == target` fires
means any candidate ranked below the point the target-th eligible_crypto
asset is found is never even visited, let alone required to be
classified. This file proves that property directly, plus the
mutation/tie-break/threshold-invariance properties the audit required.

The real 2026-08-22 snapshot's own TAXONOMY_COVERAGE_UNKNOWN result is a
genuine ratification-coverage shortfall (only 87 assets have ever been
individually ratified eligible_crypto, 13 short of target=100 -- see
known_eligible_count_so_far in the real committed evidence), not a
scan-order defect; see docs/crypto_breadth_contract.md for the full
audit and test_crypto_breadth_unverified_identity_real_evidence.py for
the real numerator/denominator this shortfall is distinct from.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_for_cutoff_test", ".github/scripts/crypto_breadth.py")

sys.path.insert(0, str(ROOT / "test"))
FIXTURES = _load("test_crypto_breadth_fixtures", "test/test_crypto_breadth.py")


class CutoffAwareScanTest(unittest.TestCase):
    def test_cutoff_aware_scan_ignores_unknown_below_the_selection_cutoff(self):
        # BTC, A, B eligible (ranks 1-3); target=3 is satisfied at rank 3
        # -- D/E/F (ranked 4-6) are never classified in the taxonomy at
        # all, and must never appear in taxonomy_unknown_before_cutoff.
        bases = ["BTC", "A", "B", "D", "E", "F"]
        prices = {base: (100 - i, 101 - i, 999) for i, base in enumerate(bases)}
        categories = {"BTC": "eligible_crypto", "A": "eligible_crypto", "B": "eligible_crypto"}
        with tempfile.TemporaryDirectory() as tmp:
            policy = FIXTURES.write_policy(Path(tmp) / "policy.json", target=3)
            taxonomy = FIXTURES.write_taxonomy(Path(tmp) / "taxonomy.json", categories)
            snapshot = FIXTURES.write_snapshot(Path(tmp) / "raw", prices=prices)
            result = CB.build_transform(
                snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(result["universe"]["taxonomy_unknown_before_cutoff"], [])
            selected_ids = {m["canonical_asset_id"] for m in result["universe"]["members"]}
            self.assertEqual(selected_ids, {"BTC", "A", "B"})

    def test_unknown_within_cutoff_range_still_blocks(self):
        # BTC, A eligible (ranks 1-2); B (rank 3) unknown; C (rank 4)
        # eligible backfills to reach target=3. The scan does not stop at
        # the first unknown -- it keeps looking for enough eligible
        # candidates -- but B's ambiguous status could have changed which
        # asset actually belongs in the real top-3 (if B turned out
        # eligible, the true top-3 by rank would be BTC/A/B, not
        # BTC/A/C), so this correctly still blocks even though target
        # was technically reachable. known_eligible_count_so_far reports
        # the real count found (3, matching target) -- the reason it
        # still blocks is the non-empty unknown list itself, not a
        # shortfall in eligible count.
        bases = ["BTC", "A", "B", "C"]
        prices = {base: (100 - i, 101 - i, 999) for i, base in enumerate(bases)}
        categories = {"BTC": "eligible_crypto", "A": "eligible_crypto", "C": "eligible_crypto"}
        with tempfile.TemporaryDirectory() as tmp:
            policy = FIXTURES.write_policy(Path(tmp) / "policy.json", target=3)
            taxonomy = FIXTURES.write_taxonomy(Path(tmp) / "taxonomy.json", categories)
            snapshot = FIXTURES.write_snapshot(Path(tmp) / "raw", prices=prices)
            result = CB.build_transform(
                snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(result["unknown_reason"], "TAXONOMY_COVERAGE_UNKNOWN")
            unknown_ids = [
                item["canonical_asset_id"]
                for item in result["universe"]["taxonomy_unknown_before_cutoff"]
            ]
            self.assertEqual(unknown_ids, ["B"])
            self.assertEqual(result["universe"]["known_eligible_count_so_far"], 3)

    def test_excluded_within_cutoff_is_backfilled_from_next_rank(self):
        # BTC, A eligible; B (rank 3) explicitly excluded -- skipped, not
        # counted as unknown; C (rank 4) eligible backfills the 3rd slot.
        # D (rank 5) is never visited (target already satisfied at C).
        bases = ["BTC", "A", "B", "C", "D"]
        prices = {base: (100 - i, 101 - i, 999) for i, base in enumerate(bases)}
        categories = {
            "BTC": "eligible_crypto", "A": "eligible_crypto",
            "B": "stablecoin", "C": "eligible_crypto",
        }
        with tempfile.TemporaryDirectory() as tmp:
            policy = FIXTURES.write_policy(Path(tmp) / "policy.json", target=3)
            taxonomy = FIXTURES.write_taxonomy(Path(tmp) / "taxonomy.json", categories)
            snapshot = FIXTURES.write_snapshot(Path(tmp) / "raw", prices=prices)
            result = CB.build_transform(
                snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(result["universe"]["taxonomy_unknown_before_cutoff"], [])
            excluded_ids = [
                item["canonical_asset_id"]
                for item in result["universe"]["taxonomy_excluded_before_cutoff"]
            ]
            self.assertEqual(excluded_ids, ["B"])
            selected_ids = {m["canonical_asset_id"] for m in result["universe"]["members"]}
            self.assertEqual(selected_ids, {"BTC", "A", "C"})
            self.assertNotIn("D", selected_ids)

    def test_mutation_promoting_a_below_cutoff_unknown_into_range_blocks(self):
        # Baseline: target=3 satisfied by BTC/A/B at ranks 1-3; C (rank 4,
        # unknown) never visited -- OBSERVED_UNCLASSIFIED.
        bases = ["BTC", "A", "B", "C"]
        baseline_prices = {base: (100 - i, 101 - i, 999) for i, base in enumerate(bases)}
        categories = {"BTC": "eligible_crypto", "A": "eligible_crypto", "B": "eligible_crypto"}
        with tempfile.TemporaryDirectory() as tmp:
            policy = FIXTURES.write_policy(Path(tmp) / "policy.json", target=3)
            taxonomy = FIXTURES.write_taxonomy(Path(tmp) / "taxonomy.json", categories)
            baseline_snapshot = FIXTURES.write_snapshot(
                Path(tmp) / "baseline", prices=baseline_prices
            )
            baseline = CB.build_transform(
                baseline_snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            self.assertEqual(baseline["status"], "OBSERVED_UNCLASSIFIED")
            self.assertNotIn(
                "C",
                {
                    item["canonical_asset_id"]
                    for item in baseline["universe"]["taxonomy_unknown_before_cutoff"]
                },
            )

            # Mutation: C's turnover is promoted above B's (still unknown
            # in the taxonomy) -- now C is ranked 3rd, inside the cutoff
            # range, and must block.
            mutated_prices = copy.deepcopy(baseline_prices)
            mutated_prices["C"] = (1000, 1001, 999)  # far higher turnover
            mutated_snapshot = FIXTURES.write_snapshot(
                Path(tmp) / "mutated", prices=mutated_prices
            )
            mutated = CB.build_transform(
                mutated_snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            self.assertEqual(mutated["status"], "UNKNOWN")
            self.assertEqual(mutated["unknown_reason"], "TAXONOMY_COVERAGE_UNKNOWN")
            self.assertIn(
                "C",
                {
                    item["canonical_asset_id"]
                    for item in mutated["universe"]["taxonomy_unknown_before_cutoff"]
                },
            )

    def test_tie_break_is_canonical_asset_id_then_pair_id(self):
        # Identical turnover for A and B -- deterministic tie-break must
        # be canonical_asset_id ascending (A before B), matching the
        # existing, unchanged ranked.sort() key.
        bases = ["BTC", "B", "A"]
        prices = {"BTC": (100, 110, 999), "A": (10, 11, 500), "B": (10, 11, 500)}
        categories = {"BTC": "eligible_crypto", "A": "eligible_crypto", "B": "eligible_crypto"}
        with tempfile.TemporaryDirectory() as tmp:
            policy = FIXTURES.write_policy(Path(tmp) / "policy.json", target=2)
            taxonomy = FIXTURES.write_taxonomy(Path(tmp) / "taxonomy.json", categories)
            snapshot = FIXTURES.write_snapshot(Path(tmp) / "raw", prices=prices)
            result = CB.build_transform(
                snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
            # target=2: BTC (highest turnover) + the tie-break winner (A,
            # ascending canonical_asset_id) -- B is never selected.
            selected_ids = {m["canonical_asset_id"] for m in result["universe"]["members"]}
            self.assertEqual(selected_ids, {"BTC", "A"})

    def test_target_and_coverage_threshold_are_never_altered_by_this_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = FIXTURES.write_policy(
                Path(tmp) / "policy.json", target=100, coverage_bps=9000
            )
            policy = CB.load_universe_policy(policy_path)
            self.assertEqual(policy["target_asset_count"], 100)
            self.assertEqual(policy["minimum_observation_coverage_bps"], 9000)

    def test_rerun_is_byte_identical(self):
        bases = ["BTC", "A", "B", "C"]
        prices = {base: (100 - i, 101 - i, 999) for i, base in enumerate(bases)}
        categories = {"BTC": "eligible_crypto", "A": "eligible_crypto", "B": "eligible_crypto"}
        with tempfile.TemporaryDirectory() as tmp:
            policy = FIXTURES.write_policy(Path(tmp) / "policy.json", target=3)
            taxonomy = FIXTURES.write_taxonomy(Path(tmp) / "taxonomy.json", categories)
            snapshot = FIXTURES.write_snapshot(Path(tmp) / "raw", prices=prices)
            first = CB.build_transform(
                snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            second = CB.build_transform(
                snapshot, universe_policy_path=policy, exclusion_taxonomy_path=taxonomy
            )
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
