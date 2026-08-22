#!/usr/bin/env python3
"""P8-12 AUDIT_CONFIRMED_MISS registry regression (item 4): reads PR #210's
real committed opportunity_miss_episodes.json, never invents an entry, and
fails closed (no match, not a crash) if that file is missing/malformed."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import clock.audit_confirmed_miss as acm  # noqa: E402


class RealEvidenceTests(unittest.TestCase):
    def test_btc_2026_08_20_is_a_confirmed_miss(self):
        match = acm.confirmed_miss_for("BTC", "2026-08-20")
        self.assertIsNotNone(match)
        self.assertEqual(match["root_cause"], "ACTION_CONVERSION_FAILURE")

    def test_eth_and_sol_2026_08_20_are_confirmed_misses(self):
        self.assertIsNotNone(acm.confirmed_miss_for("ETH/USD", "2026-08-20"))
        self.assertIsNotNone(acm.confirmed_miss_for("SOL/USD", "2026-08-20"))

    def test_005930_2026_08_19_is_a_confirmed_miss(self):
        self.assertIsNotNone(acm.confirmed_miss_for("005930", "2026-08-19"))

    def test_a_date_outside_any_real_episode_window_does_not_match(self):
        self.assertIsNone(acm.confirmed_miss_for("BTC", "2026-07-25"))

    def test_a_subject_never_in_the_real_file_does_not_match(self):
        self.assertIsNone(acm.confirmed_miss_for("TOTALLY_MADE_UP_SUBJECT", "2026-08-20"))

    def test_qualifying_root_cause_constant_is_action_conversion_failure(self):
        # The filter is an explicit constant, not implicit "whatever's in
        # the file" -- pins the intended semantics down directly.
        self.assertEqual(acm.QUALIFYING_ROOT_CAUSE, "ACTION_CONVERSION_FAILURE")

    def test_loaded_registry_only_contains_qualifying_root_cause_entries(self):
        for entry in acm._load():
            self.assertEqual(entry["root_cause"], acm.QUALIFYING_ROOT_CAUSE)


class FailClosedTests(unittest.TestCase):
    def test_missing_file_returns_no_match_not_a_crash(self):
        original = acm.MISS_EPISODES_PATH
        original_cache = acm._cache
        try:
            acm.MISS_EPISODES_PATH = Path("/nonexistent/opportunity_miss_episodes.json")
            acm._cache = None
            self.assertIsNone(acm.confirmed_miss_for("BTC", "2026-08-20"))
        finally:
            acm.MISS_EPISODES_PATH = original
            acm._cache = original_cache

    def test_malformed_file_returns_no_match_not_a_crash(self):
        import tempfile
        original = acm.MISS_EPISODES_PATH
        original_cache = acm._cache
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                fh.write("{not valid json")
                path = Path(fh.name)
            acm.MISS_EPISODES_PATH = path
            acm._cache = None
            self.assertIsNone(acm.confirmed_miss_for("BTC", "2026-08-20"))
        finally:
            acm.MISS_EPISODES_PATH = original
            acm._cache = original_cache
            path.unlink()


if __name__ == "__main__":
    unittest.main()
