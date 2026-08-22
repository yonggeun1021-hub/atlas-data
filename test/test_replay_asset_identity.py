#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- asset identity / PIT-eligible universe
regression (CIO review round 3, flaws 1 and 2/condition-6b).
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import asset_identity as ai  # noqa: E402

TAXONOMY_RELATIVE_PATH = "config/crypto_breadth_exclusion_taxonomy.json"


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


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


class EffectiveFromNeverBackdatedVsRealGitHistoryTests(unittest.TestCase):
    """CIO review round 4, item 6: `effective_from` alone must never be
    trusted as PIT eligibility -- the record's real commit/approval-
    observable time must independently satisfy PIT eligibility too. This
    reproduces the manual investigation performed for this PR as a
    permanent, automated regression: for every commit that ever touched
    the real taxonomy file, every eligible_crypto record's declared
    `effective_from` must be >= the UTC calendar date of the git commit
    that first introduced it -- i.e. never claiming eligibility earlier
    than the file was actually, git-observably ratified.

    ★ CIO review round 4 follow-up (CI shallow-clone failure): this test
      does NOT skip, and does NOT work around a shallow checkout itself
      (e.g. via `git fetch --unshallow`) -- a shallow clone is a CI
      configuration defect fixed at the workflow level
      (`.github/workflows/actions-pass.yml`'s checkout step now uses
      `fetch-depth: 0`, matching the pattern this repo already uses in
      six other workflows). If full history genuinely is not available at
      runtime for any reason, this test MUST FAIL explicitly -- there is
      no silent pass-through and no soft-skip path anywhere below.
      `test_full_real_commit_history_was_actually_observed` makes this
      concrete: it hard-codes the 4 real commit SHAs manually verified for
      this PR and asserts every one of them was actually read, so the
      other tests in this class cannot pass merely by "happening not to
      fail" against a truncated history.
    """

    # The 4 real commits that have ever touched the taxonomy file, verified
    # manually via `git log --format="%H" -- config/crypto_breadth_exclusion_taxonomy.json`
    # during this PR's round-4 CIO review response. If this file is ever
    # legitimately modified again, ADD to this set -- never remove from it
    # (removing an entry would silently weaken the "provably read full
    # history" guarantee this test exists to provide).
    EXPECTED_REAL_COMMITS = frozenset({
        "17e0ccada61b8d7f437ea3075dbf9c3337d551f6",
        "2113e2799bac6fd58620de75c041ba3cfa7b3fcb",
        "63ba480fc052e33a33897a2058766d8ec52e7171",
        "c2e8aee13212ca67b21f8a8451434403efec8776",
    })

    def _commits_oldest_first(self):
        # No try/except around the git subprocess calls anywhere in this
        # class -- if git is unavailable or the history is truncated, the
        # resulting exception (or the assertions below) must fail the test,
        # never be swallowed into a skip.
        raw = _git("log", "--reverse", "--format=%H %aI", "--", TAXONOMY_RELATIVE_PATH)
        out = []
        for line in raw.strip().splitlines():
            sha, iso = line.split(" ", 1)
            utc_date = dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc).date().isoformat()
            out.append((sha, utc_date))
        return out

    def test_full_real_commit_history_was_actually_observed(self):
        # ★ Proves this test class exercised FULL history, not a shallow
        #   subset that merely happened not to trip the backdating check.
        observed = {sha for sha, _ in self._commits_oldest_first()}
        missing = self.EXPECTED_REAL_COMMITS - observed
        self.assertEqual(
            missing, set(),
            f"expected all 4 real commits that ever touched {TAXONOMY_RELATIVE_PATH} to be "
            f"visible in git history; missing {sorted(missing)} -- this means the checkout is "
            f"shallow (or git history is otherwise truncated) and the backdating-verification "
            f"test below cannot be trusted to have checked the real ratification history. "
            f"Fix the checkout step's fetch-depth, do not skip or work around this in test code.",
        )

    def test_no_eligible_record_claims_effective_from_earlier_than_its_real_first_git_appearance(self):
        commits = self._commits_oldest_first()
        first_seen_utc_date: dict[str, str] = {}
        for sha, utc_date in commits:
            raw = _git("show", f"{sha}:{TAXONOMY_RELATIVE_PATH}")
            doc = json.loads(raw)
            for rec in doc.get("records", []):
                if rec.get("category") != "eligible_crypto":
                    continue
                asset = rec["canonical_asset_id"]
                if asset not in first_seen_utc_date:
                    first_seen_utc_date[asset] = utc_date

        current = {r["canonical_asset_id"]: r["effective_from"] for r in ai.crypto_eligible_records()}
        checked = 0
        for asset, effective_from in current.items():
            first_git_date = first_seen_utc_date.get(asset)
            self.assertIsNotNone(
                first_git_date,
                f"{asset} is a current eligible_crypto record but was never observed in any "
                f"historical commit body -- either history is truncated or this record was "
                f"introduced by an uncommitted/unverifiable change.",
            )
            checked += 1
            self.assertGreaterEqual(
                effective_from, first_git_date,
                f"{asset}: effective_from={effective_from} predates its real git-observable "
                f"first appearance ({first_git_date}) -- this would be exactly the backdating "
                f"CIO review round 4 item 6 warns against.",
            )
        self.assertGreater(checked, 0, "sanity: should have cross-checked at least one real asset")


if __name__ == "__main__":
    unittest.main()
