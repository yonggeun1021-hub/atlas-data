#!/usr/bin/env python3
"""Official-calendar complete KR historical range regression."""

import ast
import datetime as dt
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_contiguous_historical_range as MODULE  # noqa: E402


class KrContiguousHistoricalRangeTests(unittest.TestCase):
    def test_official_calendar_selects_entire_open_range(self):
        dates, receipts = MODULE.official_open_dates("2026-08-03", "2026-08-18")
        self.assertEqual(
            dates,
            [
                "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
                "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12",
                "2026-08-13", "2026-08-14", "2026-08-18",
            ],
        )
        reasons = {row["session_date"]: row["reason"] for row in receipts}
        self.assertEqual(reasons["2026-08-17"], "Substitution Holiday")
        self.assertEqual(reasons["2026-08-15"], "WEEKEND_KRX_RULE")

    def test_policy_floor_and_open_boundaries_fail_closed(self):
        with self.assertRaisesRegex(
            MODULE.KrContiguousHistoricalRangeError,
            "RANGE_PRECEDES_LEADERSHIP_POLICY:2026-08-01",
        ):
            MODULE.official_open_dates("2026-07-31", "2026-08-03")
        with self.assertRaisesRegex(
            MODULE.KrContiguousHistoricalRangeError,
            "RANGE_BOUNDARY_NOT_OPEN_REGULAR",
        ):
            MODULE.official_open_dates("2026-08-01", "2026-08-03")

    def test_no_network_implementation_or_regime_date_selection(self):
        tree = ast.parse(
            (ROOT / "regime/kr_contiguous_historical_range.py").read_text()
        )
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse({"requests", "urllib", "socket", "http"} & imports)
        dates, _ = MODULE.official_open_dates("2026-09-01", "2026-09-10")
        self.assertEqual(dates[0], "2026-09-01")
        self.assertEqual(dates[-1], "2026-09-10")
        self.assertEqual(len(dates), 8)

    def test_dedicated_workflow_is_read_only_and_artifact_only(self):
        workflow = (
            ROOT / ".github/workflows/kr-contiguous-historical-range.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("python3 -m regime.kr_contiguous_historical_range", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        for prohibited in ("git push", "contents: write", "data/observations/"):
            self.assertNotIn(prohibited, workflow)


if __name__ == "__main__":
    unittest.main()
