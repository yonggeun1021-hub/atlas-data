#!/usr/bin/env python3
"""Actions runner reporting must track the live approved-test population."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "run_all.py"
FI_PATH = ROOT / "test" / "test_fault_injection.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "actions-pass.yml"

SPEC = importlib.util.spec_from_file_location("atlas_run_all", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RunnerReportingTest(unittest.TestCase):
    def test_label_uses_current_approved_test_count(self):
        expected = f"[4/5] 승인 회귀 {len(RUNNER.APPROVED_TESTS)}파일"
        self.assertEqual(RUNNER.approved_test_label(), expected)

    def test_approved_population_matches_test_directory(self):
        actual = sorted(
            f"test/{path.name}"
            for path in (ROOT / "test").glob("test_*.py")
            if path.name != "test_fault_injection.py"
        )
        self.assertEqual(sorted(RUNNER.APPROVED_TESTS), actual)

    def test_stale_fourteen_test_wording_is_removed(self):
        runner = RUNNER_PATH.read_text(encoding="utf-8")
        fault = FI_PATH.read_text(encoding="utf-8")
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertNotIn("승인 회귀 14파일", runner)
        self.assertNotIn("approved 14-test omission", fault)
        self.assertNotIn("승인 회귀 14파일", workflow)
        self.assertIn("approved-test omission", fault)


if __name__ == "__main__":
    unittest.main()
