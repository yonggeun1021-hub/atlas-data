#!/usr/bin/env python3
"""P8-11 stage 2 — compare_pilots() regression, exercised on real 4-subject output."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "pilot_evidence_intake.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("pilot_comparison_module", SOURCE)


class PilotComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = MODULE.run_all_pilots()
        cls.comparison = MODULE.compare_pilots(cls.results)

    def test_all_four_subjects_present_with_label_and_reasons(self):
        self.assertEqual(set(self.comparison), set(MODULE.PILOT_SUBJECTS))
        for subject, row in self.comparison.items():
            with self.subTest(subject=subject):
                self.assertIn(row["label"], {
                    "TOP_OPPORTUNITY", "WATCH", "WAIT", "REJECT", "BLOCKED",
                })
                self.assertTrue(row["reasons"])
                self.assertIsInstance(row["metrics"], dict)

    def test_doosan_label_is_blocked(self):
        self.assertEqual(self.comparison["034020.KS"]["label"], "BLOCKED")

    def test_label_follows_documented_readable_rule_not_hidden_score(self):
        """Re-derive each label directly from opportunity_state per the
        documented rule in compare_pilots()'s docstring, independent of
        compare_pilots()'s own internals, and assert they match."""
        for subject, row in self.comparison.items():
            alpha = self.results[subject]["alpha_review"]
            state = alpha["opportunity_state"]
            ft = self.results[subject]["forward_thesis"]
            eg = self.results[subject]["expectations_gap"]["expectations_gap"]
            pr = self.results[subject]["price_reflection"]["price_reflection"]
            evidence_strength = len(ft["observed_facts"]) + len(ft["evidence_lineage"])
            data_gap_count = len(ft["unknowns"]) + len(eg["missing_inputs"]) + len(pr["missing_inputs"])

            if state == "BLOCKED":
                expected = "BLOCKED"
            elif state == "REJECTED":
                expected = "REJECT"
            elif (
                state == "ANTICIPATORY_REVIEW"
                and evidence_strength >= MODULE.TOP_OPPORTUNITY_MIN_EVIDENCE_COUNT
                and data_gap_count <= MODULE.TOP_OPPORTUNITY_MAX_GAP_COUNT
            ):
                expected = "TOP_OPPORTUNITY"
            elif state in ("ANTICIPATORY_REVIEW", "CONFIRMATION_REVIEW", "EARLY_DISCOVERY"):
                expected = "WATCH"
            else:
                expected = "WAIT"

            with self.subTest(subject=subject):
                self.assertEqual(row["label"], expected)

    def test_reasons_cite_opportunity_state_not_a_bare_number(self):
        for subject, row in self.comparison.items():
            with self.subTest(subject=subject):
                self.assertTrue(
                    any("opportunity_state=" in reason for reason in row["reasons"])
                )


if __name__ == "__main__":
    unittest.main()
