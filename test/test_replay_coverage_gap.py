#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- Coverage Gap KPI block regression (CIO review
round 3, flaw 4)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay import coverage_gap as cg  # noqa: E402


def entry(subject, date, data_available):
    return {"subject": subject, "decision_date": date, "data_available": data_available}


class CoverageGapReportTests(unittest.TestCase):
    def test_auditable_coverage_pct_computed_correctly(self):
        entries = [
            entry("X", "2026-08-01", True),
            entry("X", "2026-08-02", False),
            entry("X", "2026-08-03", False),
            entry("X", "2026-08-04", True),
        ]
        report = cg.build_coverage_gap_report(entries)
        self.assertEqual(report["auditable_entries"], 2)
        self.assertEqual(report["unauditable_entries"], 2)
        self.assertAlmostEqual(report["auditable_coverage_pct"], 50.0)

    def test_unauditable_days_lists_only_unauditable_dates(self):
        entries = [entry("X", "2026-08-01", True), entry("X", "2026-08-02", False)]
        report = cg.build_coverage_gap_report(entries)
        self.assertEqual(report["unauditable_days"], ["2026-08-02"])

    def test_unauditable_subjects_entirely_only_when_zero_data_ever(self):
        entries = [
            entry("A", "2026-08-01", False), entry("A", "2026-08-02", False),
            entry("B", "2026-08-01", True), entry("B", "2026-08-02", False),
        ]
        report = cg.build_coverage_gap_report(entries)
        self.assertEqual(report["unauditable_subjects_entirely"], ["A"])

    def test_missing_evidence_types_lists_real_repo_facts(self):
        entries = [entry("BTC", "2026-07-22", False)]
        report = cg.build_coverage_gap_report(entries)
        self.assertTrue(any("2026-08-13" in m for m in report["missing_evidence_types"]))
        self.assertTrue(any("ETF" in m for m in report["missing_evidence_types"]))

    def test_empty_population_does_not_crash(self):
        report = cg.build_coverage_gap_report([])
        self.assertEqual(report["total_entries"], 0)
        self.assertIsNone(report["auditable_coverage_pct"])


if __name__ == "__main__":
    unittest.main()
