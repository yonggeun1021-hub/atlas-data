#!/usr/bin/env python3
"""P11 PIT Replay -- existing-ruleset baseline regression. Confirms this
module only ever READS decision/alpha_review.py's source text (never
imports/executes/modifies it) and that its two structural claims are
re-derivable from the literal committed source."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import existing_ruleset_baseline as erb  # noqa: E402


class ExistingRulesetBaselineTests(unittest.TestCase):
    def test_module_never_imports_or_execs_alpha_review(self):
        source = (ROOT / "replay" / "existing_ruleset_baseline.py").read_text(encoding="utf-8")
        for forbidden in ("import decision", "from decision", "importlib", "exec(", "eval("):
            self.assertNotIn(forbidden, source)
        self.assertIn("read_text", source)  # read-only source citation, same as grep

    def test_trade_proposal_always_null_claim_is_verified_against_real_source(self):
        claim = erb.verify_trade_proposal_always_null()
        self.assertIs(claim["value"], True)
        self.assertEqual(claim["source"], "decision/alpha_review.py")

    def test_default_review_cadence_claim_is_verified_against_real_source(self):
        claim = erb.verify_default_review_cadence_days()
        self.assertEqual(claim["value"], 30)

    def test_baseline_summary_contains_both_claims(self):
        summary = erb.baseline_summary()
        self.assertEqual(set(summary), {"trade_proposal_always_null", "default_review_cadence_days"})

    def test_existing_ruleset_action_is_structurally_none_regardless_of_trigger_presence(self):
        for has_trigger in (True, False):
            action = erb.existing_ruleset_action_for(has_trigger)
            self.assertIsNone(action["trade_proposal"])
            self.assertEqual(action["recommended_action"], "NONE")
            self.assertFalse(action["action_convertible"])
            self.assertEqual(action["next_review_cadence_days"], 30)

    def test_error_raised_if_marker_absent_from_supplied_text(self):
        # Simulate a stale-citation scenario without touching the real file.
        original = erb.ALPHA_REVIEW_SOURCE
        try:
            erb.ALPHA_REVIEW_SOURCE = ROOT / "README.md"  # a real file without the markers
            with self.assertRaises(erb.ExistingRulesetBaselineError):
                erb.verify_trade_proposal_always_null()
        finally:
            erb.ALPHA_REVIEW_SOURCE = original


if __name__ == "__main__":
    unittest.main()
