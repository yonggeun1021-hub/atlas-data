#!/usr/bin/env python3
"""Actions runner reporting must track the live approved-test population."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import contextlib
import io
import os
import subprocess
import tempfile


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


class RunnerFailFastTest(unittest.TestCase):
    def run_approved(self, fail_fast, failed=None):
        runner = RUNNER.Runner(fail_fast=fail_fast)
        calls = []
        def child(script):
            calls.append(script)
            return subprocess.CompletedProcess([], int(script == failed), "", "ERROR: test_bad (Fixture)\nValueError: broken")
        with mock.patch.object(runner, "child", side_effect=child), contextlib.redirect_stdout(io.StringIO()):
            result = runner.approved_tests()
        return runner, calls, result

    def test_green_executes_every_approved_suite_exactly_once(self):
        _, calls, result = self.run_approved(True)
        self.assertTrue(result)
        self.assertCountEqual(calls, RUNNER.APPROVED_TESTS)
        self.assertEqual(len(calls), len(set(calls)))
        self.assertEqual(calls[:2], ["test/test_runner_reporting.py", "test/test_daily_orchestrator.py"])

    def test_daily_failure_stops_before_remaining_suites(self):
        runner, calls, result = self.run_approved(True, "test/test_daily_orchestrator.py")
        self.assertFalse(result)
        self.assertEqual(len(calls), 2)
        self.assertIn("test_bad", runner.failures[0])

    def test_default_diagnostic_mode_still_collects_all_failures(self):
        _, calls, result = self.run_approved(False, "test/test_daily_orchestrator.py")
        self.assertFalse(result)
        self.assertCountEqual(calls, RUNNER.APPROVED_TESTS)

    def test_population_mismatch_runs_no_child(self):
        runner = RUNNER.Runner(fail_fast=True)
        with mock.patch.object(RUNNER.os, "listdir", return_value=[]), mock.patch.object(runner, "child") as child:
            self.assertFalse(runner.approved_tests())
            child.assert_not_called()

    def test_real_child_both_streams_retained_and_secret_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "fixture.py"
            script.write_text("import os, sys\nprint('A' * 3000)\nprint(os.environ['ATLAS_TEST_SECRET'])\nprint('ERROR: test_first (Fixture)\\nTraceback\\nValueError: first', file=sys.stderr)\nprint('B' * 3000, file=sys.stderr)\nsys.exit(1)\n")
            logs = Path(temp) / "logs"
            runner = RUNNER.Runner(log_dir=str(logs))
            with mock.patch.object(RUNNER, "ROOT", temp), mock.patch.dict(os.environ, {"ATLAS_TEST_SECRET": "sentinel-private-value"}):
                result = runner.child("fixture.py")
            self.assertEqual(result.returncode, 1)
            self.assertEqual((logs / "fixture.py.stdout.log").read_text(), result.stdout)
            self.assertEqual((logs / "fixture.py.stderr.log").read_text(), result.stderr)
            self.assertIn("A" * 3000, result.stdout)
            self.assertIn("B" * 3000, result.stderr)
            self.assertNotIn("sentinel-private-value", result.stdout)
            self.assertIn("[REDACTED]", result.stdout)

    def test_six_error_names_and_causes_survive_long_summary(self):
        raw = "\n".join(f"ERROR: test_{i} (Fixture)\nTraceback\n" + " frame\n" * 60 + f"ValueError: cause_{i}" for i in range(6))
        summary = RUNNER.failure_summary(raw)
        for i in range(6):
            self.assertIn(f"test_{i}", summary)
            self.assertIn(f"cause_{i}", summary)
        self.assertLess(len(summary), len(raw))

    def exercise_main(self, stage):
        runner = RUNNER.Runner(fail_fast=True)
        calls = []
        def action(name, value):
            def invoke(*args):
                calls.append(name)
                if name == stage:
                    runner.fail(name, "injected failure")
                    return False
                return value
            return invoke
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--authoritative", "--fail-fast"]))
            stack.enter_context(mock.patch.object(RUNNER, "Runner", return_value=runner))
            stack.enter_context(mock.patch.object(RUNNER, "disposable_checkout_proof", return_value=[]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            for name, value in [("test_set", True), ("snapshot", {"kept": "path"}), ("rebuild", True), ("compare", True), ("boundary", True), ("approved_tests", True), ("fault_injection", True)]:
                stack.enter_context(mock.patch.object(runner, name, side_effect=action(name, value)))
            result = RUNNER.main()
        return calls, result

    def test_early_failure_never_runs_later_gate_stages(self):
        for stage in ("test_set", "snapshot", "rebuild", "compare", "boundary", "approved_tests", "fault_injection"):
            with self.subTest(stage=stage):
                calls, result = self.exercise_main(stage)
                self.assertEqual(result, 1)
                # Final boundary check is retained after FI to catch mutations.
                self.assertEqual(calls[-1], "boundary" if stage == "fault_injection" else stage)
                if stage == "approved_tests":
                    self.assertNotIn("fault_injection", calls)

    def test_green_requires_rebuild_compare_full_regression_fi_and_boundary(self):
        calls, result = self.exercise_main(None)
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["test_set", "snapshot", "rebuild", "compare", "boundary", "approved_tests", "fault_injection", "boundary"])

    def test_credential_forms_redacted(self):
        text = "Authorization: Bearer abc123\nghp_sensitive123\n-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----"
        clean = RUNNER.redact_diagnostics(text)
        self.assertNotIn("abc123", clean)
        self.assertNotIn("sensitive123", clean)
        self.assertNotIn("private-material", clean)

    def test_dirty_or_non_disposable_checkout_stops_before_mutation(self):
        with mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--authoritative", "--fail-fast"]), mock.patch.object(RUNNER, "disposable_checkout_proof", return_value=["dirty checkout"]), mock.patch.object(RUNNER.Runner, "snapshot") as snapshot, mock.patch.object(RUNNER.Runner, "child") as child, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(RUNNER.main(), 1)
            snapshot.assert_not_called()
            child.assert_not_called()

    def test_log_directory_cannot_dirty_checkout(self):
        with mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--log-dir", str(ROOT / "logs")]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                RUNNER.main()
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
