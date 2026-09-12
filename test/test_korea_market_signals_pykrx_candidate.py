#!/usr/bin/env python3
"""Offline checks for the read-only pykrx KR PAPER source candidate."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / ".github/scripts/korea_market_signals_pykrx_candidate.py"
SPEC = importlib.util.spec_from_file_location("korea_market_signals_pykrx_candidate", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PykrxCandidateIdentityTest(unittest.TestCase):
    def test_ratified_names_resolve_after_exact_pykrx_rendering(self):
        expected = {"kospi": 25, "kosdaq": 23}
        for market, count in expected.items():
            mapping = MODULE.canonical_index_name_map(market)
            self.assertEqual(len(mapping), count)
            for rendered, canonical in mapping.items():
                self.assertEqual(rendered, MODULE.pykrx_rendered_index_name(canonical))
                self.assertEqual(mapping[rendered], canonical)
        self.assertEqual(
            MODULE.canonical_index_name_map("kospi")["IT서비스"], "IT 서비스"
        )
        self.assertEqual(
            MODULE.canonical_index_name_map("kosdaq")["운송창고"], "운송·창고"
        )

    def test_partial_leadership_remains_fail_closed(self):
        leadership = {
            "coverage": {
                "KOSDAQ": {"observed_sector_count": 12, "ratified_identity_count": 23},
                "KOSPI": {"observed_sector_count": 14, "ratified_identity_count": 25},
            }
        }
        with self.assertRaisesRegex(
            MODULE.CandidateError,
            r"LEADERSHIP_COVERAGE_INCOMPLETE:KOSDAQ=12/22,KOSPI=14/24",
        ):
            MODULE.require_complete_leadership(leadership)

    def test_complete_leadership_is_accepted(self):
        MODULE.require_complete_leadership(
            {
                "coverage": {
                    "KOSDAQ": {"observed_sector_count": 22, "ratified_identity_count": 23},
                    "KOSPI": {"observed_sector_count": 24, "ratified_identity_count": 25},
                }
            }
        )


if __name__ == "__main__":
    unittest.main()
