#!/usr/bin/env python3
"""Actions runner reporting must track the live approved-test population."""

import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap

import yaml


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "run_all.py"
FI_PATH = ROOT / "test" / "test_fault_injection.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "actions-pass.yml"
US_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "us-paper-market-data-contract.yml"
US_BOUNDARY_MODULES = (
    "validation/tests/test_us_investable_registry.py",
    "validation/tests/test_us_session_bars.py",
    "validation/tests/test_us_paper_market_data_boundary.py",
)

SPEC = importlib.util.spec_from_file_location("atlas_run_all", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def exercise_main(stage, argv=("run_all.py", "--authoritative", "--fail-fast"), shard=None):
    """Drive main() with every gate stage stubbed, optionally failing one of them."""
    runner = RUNNER.Runner(fail_fast=True, shard=shard)
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
        stack.enter_context(mock.patch.object(RUNNER.sys, "argv", list(argv)))
        stack.enter_context(mock.patch.object(RUNNER, "Runner", return_value=runner))
        stack.enter_context(mock.patch.object(RUNNER, "disposable_checkout_proof", return_value=[]))
        output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        for name, value in [("test_set", True), ("snapshot", {"kept": "path"}), ("rebuild", True), ("compare", True), ("boundary", True), ("approved_tests", True), ("fault_injection", True)]:
            stack.enter_context(mock.patch.object(runner, name, side_effect=action(name, value)))
        result = RUNNER.main()
    return calls, result, output.getvalue()


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

    def exercise_main(self, stage, argv=("run_all.py", "--authoritative", "--fail-fast"), shard=None):
        return exercise_main(stage, argv=argv, shard=shard)

    def test_early_failure_never_runs_later_gate_stages(self):
        for stage in ("test_set", "snapshot", "rebuild", "compare", "boundary", "approved_tests", "fault_injection"):
            with self.subTest(stage=stage):
                calls, result, _ = self.exercise_main(stage)
                self.assertEqual(result, 1)
                # Final boundary check is retained after FI to catch mutations.
                self.assertEqual(calls[-1], "boundary" if stage == "fault_injection" else stage)
                if stage == "approved_tests":
                    self.assertNotIn("fault_injection", calls)

    def test_green_requires_rebuild_compare_full_regression_fi_and_boundary(self):
        calls, result, output = self.exercise_main(None)
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["test_set", "snapshot", "rebuild", "compare", "boundary", "approved_tests", "fault_injection", "boundary"])
        # Default unsharded output is unchanged: it still makes the global claim.
        self.assertIn("Actions PASS = YES", output)

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


PRIORITY = ["test/test_runner_reporting.py", "test/test_daily_orchestrator.py"]


def workflow_jobs(text):
    """Split a workflow into its top-level job blocks without a YAML dependency."""
    jobs, current, inside = {}, None, False
    for line in text.splitlines():
        if line.rstrip() == "jobs:":
            inside = True
            continue
        if not inside:
            continue
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            jobs[current] = []
            continue
        if current is not None:
            jobs[current].append(line)
    return {name: "\n".join(lines) for name, lines in jobs.items()}


STEP_START = re.compile(r"^      - ([A-Za-z0-9_-]+):\s*(.*)$")
STEP_KEY = re.compile(r"^        ([A-Za-z0-9_-]+):\s*(.*)$")


def workflow_steps(text):
    """Split a single-job workflow's step list into ordered maps without a YAML dependency.

    Every step keeps its own raw block under ``body`` so a test can assert on a
    multi-line ``run:`` script as well as on the scalar keys.
    """
    steps = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        start = STEP_START.match(line)
        if start:
            steps.append({"body": [line], start.group(1): start.group(2).strip()})
            continue
        if not steps or not line.startswith("        "):
            continue
        steps[-1]["body"].append(line)
        key = STEP_KEY.match(line)
        if key:
            steps[-1][key.group(1)] = key.group(2).strip()
    for step in steps:
        step["body"] = "\n".join(step["body"])
    return steps


def aggregate_script(text):
    """Extract the actual final-aggregate script that the workflow executes."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().endswith("<<'PY'"):
            body = []
            for follow in lines[index + 1:]:
                if follow.strip() == "PY":
                    return textwrap.dedent("\n".join(body)) + "\n"
                body.append(follow)
    raise AssertionError("final aggregate script is missing from the US workflow")


class RegressionShardPopulationTest(unittest.TestCase):
    def test_two_shards_are_complete_disjoint_and_duplicate_free(self):
        first, second = RUNNER.regression_shards()
        self.assertTrue(first and second)
        self.assertEqual(set(first) & set(second), set())
        self.assertCountEqual(first + second, RUNNER.APPROVED_TESTS)
        self.assertEqual(len(first + second), len(set(first + second)))
        self.assertEqual(len(first) + len(second), len(RUNNER.APPROVED_TESTS))

    def test_each_shard_preserves_declared_relative_order(self):
        declared = {t: i for i, t in enumerate(RUNNER.APPROVED_TESTS)}
        for chunk in RUNNER.regression_shards():
            positions = [declared[t] for t in chunk]
            self.assertEqual(positions, sorted(positions))

    def test_partition_is_deterministic_for_the_same_population(self):
        self.assertEqual(RUNNER.regression_shards(), RUNNER.regression_shards())

    def test_duplicate_or_empty_population_is_rejected(self):
        duplicated = list(RUNNER.APPROVED_TESTS) + [RUNNER.APPROVED_TESTS[0]]
        with self.assertRaises(ValueError) as error:
            RUNNER.regression_shards(tests=duplicated)
        self.assertIn(RUNNER.APPROVED_TESTS[0], str(error.exception))
        with self.assertRaises(ValueError):
            RUNNER.regression_shards(tests=[])
        with self.assertRaises(ValueError):
            RUNNER.regression_shards(tests=["test/test_only_one.py"])

    def test_only_the_adopted_two_shard_count_is_supported(self):
        self.assertEqual(RUNNER.REGRESSION_SHARD_COUNT, 2)
        for count in (0, 1, 3, 4):
            with self.subTest(count=count), self.assertRaises(ValueError):
                RUNNER.regression_shards(count=count)

    def test_estimated_seconds_are_weights_only_and_never_the_population(self):
        # A missing, partial, or stale timing table only degrades balance — it can
        # never shrink, extend, duplicate, or reorder the approved manifest.
        stale = dict(RUNNER.REGRESSION_ESTIMATED_SECONDS,
                     **{"test/test_module_that_no_longer_exists.py": 9999.0})
        for table in ({}, stale):
            with self.subTest(entries=len(table)):
                with mock.patch.object(RUNNER, "REGRESSION_ESTIMATED_SECONDS", table):
                    first, second = RUNNER.regression_shards()
                self.assertTrue(first and second)
                self.assertEqual(set(first) & set(second), set())
                self.assertCountEqual(first + second, RUNNER.APPROVED_TESTS)


class RegressionShardExecutionTest(unittest.TestCase):
    def run_shard(self, shard):
        runner = RUNNER.Runner(fail_fast=True, shard=shard)
        calls = []

        def child(script):
            calls.append(script)
            return subprocess.CompletedProcess([], 0, "", "")

        with mock.patch.object(runner, "child", side_effect=child), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = runner.approved_tests()
        return calls, result, output.getvalue()

    def test_default_run_still_selects_the_whole_approved_manifest(self):
        self.assertEqual(RUNNER.Runner().selected_regression(), list(RUNNER.APPROVED_TESTS))

    def test_shard_runs_only_its_own_modules_with_priority_first(self):
        for shard in (1, 2):
            with self.subTest(shard=shard):
                chunk = RUNNER.regression_shards()[shard - 1]
                calls, result, output = self.run_shard(shard)
                self.assertTrue(result)
                self.assertCountEqual(calls, chunk)
                self.assertEqual(len(calls), len(set(calls)))
                owned = [p for p in PRIORITY if p in chunk]
                self.assertEqual(calls[:len(owned)], owned)
                self.assertEqual(calls[len(owned):], [t for t in chunk if t not in PRIORITY])
                self.assertIn(f"shard {shard}/2", output)
                self.assertIn(f"선택 {len(chunk)} / 승인 전체 {len(RUNNER.APPROVED_TESTS)}파일", output)
                self.assertIn("PARTIAL", output)

    def test_both_shards_together_run_every_approved_module_exactly_once(self):
        executed = self.run_shard(1)[0] + self.run_shard(2)[0]
        self.assertCountEqual(executed, RUNNER.APPROVED_TESTS)
        self.assertEqual(len(executed), len(set(executed)))

    def test_malformed_population_stops_the_shard_before_any_child(self):
        runner = RUNNER.Runner(fail_fast=True, shard=1)
        with mock.patch.object(RUNNER, "regression_shards", side_effect=ValueError("중복")), \
                mock.patch.object(runner, "child") as child, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(runner.approved_tests())
        child.assert_not_called()
        self.assertIn("regression-shard", runner.failures[0])

    def test_partial_shard_never_claims_the_global_actions_pass(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(RUNNER.finish(RUNNER.Runner(shard=1)), 0)
        self.assertNotIn("Actions PASS = YES", output.getvalue())
        self.assertIn("PARTIAL", output.getvalue())

    def test_unsharded_success_output_is_unchanged(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(RUNNER.finish(RUNNER.Runner()), 0)
        self.assertIn("Actions PASS = YES", output.getvalue())
        self.assertNotIn("PARTIAL", output.getvalue())

    def test_failing_shard_still_fails_the_run(self):
        runner = RUNNER.Runner(fail_fast=True, shard=1)
        runner.fail("regression", "injected")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(RUNNER.finish(runner), 1)
        self.assertIn("Actions PASS = NO", output.getvalue())


class RegressionShardObligationTest(unittest.TestCase):
    SHARD_ARGV = ("run_all.py", "--authoritative", "--fail-fast",
                  "--regression-shard-count", "2", "--regression-shard-index", "1")

    def test_shard_keeps_rebuild_byte_authority_regression_and_fi(self):
        calls, result, output = exercise_main(None, argv=self.SHARD_ARGV, shard=1)
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["test_set", "snapshot", "rebuild", "compare", "boundary",
                                 "approved_tests", "fault_injection", "boundary"])
        self.assertNotIn("Actions PASS = YES", output)

    def test_shard_early_failure_still_stops_later_gate_stages(self):
        for stage in ("snapshot", "rebuild", "compare", "approved_tests", "fault_injection"):
            with self.subTest(stage=stage):
                calls, result, _ = exercise_main(stage, argv=self.SHARD_ARGV, shard=1)
                self.assertEqual(result, 1)
                self.assertEqual(calls[-1], "boundary" if stage == "fault_injection" else stage)


class RegressionShardCliTest(unittest.TestCase):
    def reject(self, argv):
        with mock.patch.object(RUNNER.sys, "argv", ["run_all.py"] + argv), \
                mock.patch.object(RUNNER, "Runner") as runner_class, \
                contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                RUNNER.main()
        self.assertEqual(error.exception.code, 2)
        runner_class.assert_not_called()

    def test_incomplete_out_of_range_or_unsupported_shard_requests_do_no_work(self):
        for argv in (
            ["--authoritative", "--regression-shard-index", "1"],
            ["--authoritative", "--regression-shard-count", "2"],
            ["--authoritative", "--regression-shard-count", "3", "--regression-shard-index", "1"],
            ["--authoritative", "--regression-shard-count", "1", "--regression-shard-index", "1"],
            ["--authoritative", "--regression-shard-count", "2", "--regression-shard-index", "0"],
            ["--authoritative", "--regression-shard-count", "2", "--regression-shard-index", "3"],
            ["--authoritative", "--regression-shard-count", "2", "--regression-shard-index", "two"],
        ):
            with self.subTest(argv=argv):
                self.reject(argv)

    def test_shard_may_not_skip_authoritative_validation_or_fault_injection(self):
        self.reject(["--regression-shard-count", "2", "--regression-shard-index", "1"])
        self.reject(["--authoritative", "--no-fi",
                     "--regression-shard-count", "2", "--regression-shard-index", "1"])

    def test_existing_no_fi_recursion_path_remains_usable_unsharded(self):
        runner = RUNNER.Runner(fail_fast=True)
        with mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--no-fi"]), \
                mock.patch.object(RUNNER, "Runner", return_value=runner), \
                mock.patch.object(runner, "approved_tests", return_value=True), \
                mock.patch.object(runner, "test_set", return_value=True), \
                mock.patch.object(runner, "boundary", return_value=True), \
                mock.patch.object(runner, "fault_injection") as fi, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            RUNNER.main()
        fi.assert_not_called()
        self.assertIn("건너뜀 (--no-fi", output.getvalue())


class UsWorkflowTwoShardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = US_WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.jobs = workflow_jobs(cls.text)

    def test_two_child_jobs_carry_different_explicit_shard_arguments(self):
        for job, index in (("regression-shard-1", "1"), ("regression-shard-2", "2")):
            self.assertIn(job, self.jobs)
            body = self.jobs[job]
            self.assertIn(f"run_all.py --authoritative --fail-fast "
                          f"--regression-shard-count 2 --regression-shard-index {index}", body)
            self.assertIn("ATLAS_DISPOSABLE_CHECKOUT=1", body)
            self.assertIn("needs: focused", body)
            self.assertIn("timeout-minutes: 60", body)
        self.assertNotEqual(self.jobs["regression-shard-1"], self.jobs["regression-shard-2"])
        self.assertEqual(self.text.count("--regression-shard-index 1"), 1)
        self.assertEqual(self.text.count("--regression-shard-index 2"), 1)

    def test_each_shard_repeats_us_modules_and_the_no_mutation_check(self):
        for job in ("regression-shard-1", "regression-shard-2"):
            body = self.jobs[job]
            for module in US_BOUNDARY_MODULES:
                self.assertIn(module, body)
            self.assertIn("git diff --exit-code", body)
            self.assertIn("python-version: '3.11'", body)
            self.assertIn("actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97", body)

    def test_no_shard_skips_fault_injection_or_authoritative_validation(self):
        self.assertNotIn("--no-fi", self.text)
        self.assertEqual(self.text.count("--authoritative"), 2)

    def test_final_full_job_is_an_always_aggregate_of_focused_and_both_shards(self):
        self.assertIn("full", self.jobs)
        body = self.jobs["full"]
        self.assertIn("needs: [focused, regression-shard-1, regression-shard-2]", body)
        self.assertIn("if: always()", body)
        self.assertIn("timeout-minutes: 60", body)
        self.assertIn('REQUIRED = ["focused", "regression-shard-1", "regression-shard-2"]', body)

    def test_focused_semantics_permissions_and_triggers_are_unchanged(self):
        focused = self.jobs["focused"]
        self.assertIn("timeout-minutes: 10", focused)
        for module in US_BOUNDARY_MODULES:
            self.assertIn(module, focused)
        self.assertIn("git diff --exit-code", focused)
        self.assertNotIn("needs:", focused)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("branches: [main]", self.text)
        self.assertEqual(self.text.count('- "validation/tests/test_us_*"'), 2)


class UsAggregateScriptTest(unittest.TestCase):
    def aggregate(self, needs):
        script = aggregate_script(US_WORKFLOW_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "aggregate.py"
            path.write_text(script, encoding="utf-8")
            return subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                                  env=dict(os.environ, ATLAS_NEEDS_JSON=json.dumps(needs)))

    @staticmethod
    def outcomes(overrides=None):
        needs = {name: {"result": "success"} for name in
                 ("focused", "regression-shard-1", "regression-shard-2")}
        needs.update(overrides or {})
        return needs

    def test_all_three_dependencies_successful_passes(self):
        result = self.aggregate(self.outcomes())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("= YES", result.stdout)

    def test_any_non_success_dependency_outcome_is_rejected(self):
        for key in ("focused", "regression-shard-1", "regression-shard-2"):
            for outcome in ("failure", "cancelled", "skipped", "neutral", ""):
                with self.subTest(key=key, outcome=outcome):
                    result = self.aggregate(self.outcomes({key: {"result": outcome}}))
                    self.assertEqual(result.returncode, 1, result.stdout)
                    self.assertIn("= NO", result.stdout)

    def test_missing_or_unexpected_dependency_is_rejected(self):
        for key in ("focused", "regression-shard-1", "regression-shard-2"):
            with self.subTest(missing=key):
                needs = self.outcomes()
                needs.pop(key)
                result = self.aggregate(needs)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("missing", result.stdout)
        result = self.aggregate(self.outcomes({"regression-shard-3": {"result": "success"}}))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("unexpected dependency keys", result.stdout)

    def test_dependency_entry_without_a_result_is_rejected(self):
        result = self.aggregate(self.outcomes({"regression-shard-2": {}}))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("missing", result.stdout)


class ActionsPassDiagnosticIsolationTest(unittest.TestCase):
    """A 403 from the artifact service may not overturn a required gate.

    Run 34246738933 (fb2bb2dc) finished every regression and the (then
    monolithic) Actions PASS gate with `success`, then `FinalizeArtifact:
    (403) Forbidden` on the non-authoritative diagnostic upload failed the
    job and left the mandatory dirty-artifact check `skipped`. Only that one
    transport is best effort.

    ★ CIO CI-sharding 지시 2026-09-12 — 이제 gate 하나가 아니라 세 job
      (structural / regression / fault-injection) 각각이 자기 diagnostic
      업로드 · dirty 검사를 갖는다. 이 클래스는 세 job 전체에 같은 격리
      계약이 반복되는지 확인한다 — 하나로 합치지 않는다(각 job 이 서로
      기다리지 않고 독립적으로 authoritative 하다는 계약의 일부).
    """

    # job → (gate step name, upload step name, uploaded artifact name prefix)
    GATE_JOBS = {
        "structural": ("Structural integrity gate — builder ①→⑭ 재빌드 · byte 비교 · 경계",
                       "Preserve complete redacted structural diagnostics",
                       "actions-pass-structural-logs-${{ github.run_id }}-${{ github.run_attempt }}"),
        "regression": ("Regression shard ${{ matrix.shard }}/4",
                       "Preserve complete redacted regression shard diagnostics",
                       "actions-pass-regression-logs-${{ github.run_id }}-${{ github.run_attempt }}-shard-${{ matrix.shard }}"),
        "fault-injection": ("Fault Injection suite (FI-1/2/4/5/6 — FI-3 KNOWN GAP / NOT GATED)",
                            "Preserve complete redacted fault-injection diagnostics",
                            "actions-pass-fi-logs-${{ github.run_id }}-${{ github.run_attempt }}"),
    }
    DIRTY = "작업 후 dirty artifact 가 없는지"

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.jobs_text = workflow_jobs(cls.text)
        cls.job_steps = {name: workflow_steps(body) for name, body in cls.jobs_text.items()}

    def by_name(self, job):
        return {s["name"]: s for s in self.job_steps[job] if "name" in s}

    def step(self, job, name):
        by_name = self.by_name(job)
        self.assertIn(name, by_name, f"{name!r} missing from job {job!r}")
        return by_name[name]

    def index(self, job, name):
        return [s.get("name") for s in self.job_steps[job]].index(name)

    def test_every_gate_job_has_exactly_one_tolerant_upload_step(self):
        for job, (_, upload, _) in self.GATE_JOBS.items():
            with self.subTest(job=job):
                tolerant = [s.get("name") for s in self.job_steps[job]
                           if s.get("continue-on-error") == "true"]
                self.assertEqual(tolerant, [upload])
        # preflight and actions-pass-full run no destructive/authoritative
        # gate of their own — neither carries a diagnostic upload or a
        # continue-on-error escape hatch.
        for job in ("preflight", "actions-pass-full"):
            with self.subTest(job=job):
                self.assertEqual([s for s in self.job_steps[job]
                                  if s.get("continue-on-error") == "true"], [])
        # Step scope only — never a job-wide or workflow-wide escape hatch —
        # so every declaration in the whole file sits at step-key indentation,
        # one per gate job, none anywhere else.
        declarations = [line for line in self.text.splitlines()
                        if "continue-on-error" in line and not line.lstrip().startswith("#")]
        self.assertEqual(declarations, ["        continue-on-error: true"] * len(self.GATE_JOBS))

    def test_every_gate_step_and_dirty_check_still_fails_authoritatively(self):
        for job, (gate, _, _) in self.GATE_JOBS.items():
            with self.subTest(job=job, step=gate):
                step = self.step(job, gate)
                self.assertNotIn("continue-on-error", step)
                self.assertNotIn("if", step)  # default success()-gated and mandatory
            with self.subTest(job=job, step=self.DIRTY):
                dirty = self.step(job, self.DIRTY)
                self.assertNotIn("continue-on-error", dirty)
                self.assertNotIn("if", dirty)
                self.assertEqual(dirty["run"], "git diff --exit-code")
        self.assertIn("--phase structural", self.step("structural", self.GATE_JOBS["structural"][0])["run"])
        self.assertIn("--phase regression", self.step("regression", self.GATE_JOBS["regression"][0])["run"])
        self.assertIn("--phase fi", self.step("fault-injection", self.GATE_JOBS["fault-injection"][0])["run"])
        for job, (gate, _, _) in self.GATE_JOBS.items():
            self.assertIn("--fail-fast", self.step(job, gate)["run"])
        # preflight also ends on its own mandatory, unconditional dirty check.
        preflight_dirty = self.by_name("preflight")[self.DIRTY]
        self.assertNotIn("continue-on-error", preflight_dirty)
        self.assertEqual(preflight_dirty["run"], "git diff --exit-code")

    def test_dirty_check_stays_reachable_after_a_failed_diagnostic_upload(self):
        for job, (gate, upload, _) in self.GATE_JOBS.items():
            with self.subTest(job=job):
                # continue-on-error keeps the upload step conclusion `success`,
                # so the default success() condition on the dirty check is
                # still satisfied when only the upload failed — it may never
                # be dropped or made conditional, and it is always the job's
                # last step.
                self.assertLess(self.index(job, gate), self.index(job, upload))
                self.assertLess(self.index(job, upload), self.index(job, self.DIRTY))
                self.assertEqual(self.job_steps[job][-1].get("name"), self.DIRTY)
        self.assertEqual(self.job_steps["preflight"][-1].get("name"), self.DIRTY)

    def test_upload_identity_pin_redaction_and_retention_are_unchanged_per_job(self):
        for job, (gate, upload_name, artifact_name) in self.GATE_JOBS.items():
            with self.subTest(job=job):
                step = self.step(job, upload_name)
                self.assertEqual(step["if"], "${{ always() }}")
                self.assertEqual(
                    step["uses"],
                    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a  # v7.0.1",
                )
                for entry in (
                    f"name: {artifact_name}",
                    "path: ${{ runner.temp }}/atlas-gate-logs/",
                    "if-no-files-found: ignore",
                    "retention-days: 7",
                ):
                    self.assertIn(f"          {entry}", step["body"])
                # The redacted diagnostics themselves are produced by the gate step.
                self.assertIn('--log-dir "$RUNNER_TEMP/atlas-gate-logs"', self.step(job, gate)["run"])

    def test_uploaded_artifact_names_are_unique_across_jobs_and_include_run_attempt_and_shard(self):
        names = [artifact for _, _, artifact in self.GATE_JOBS.values()]
        self.assertEqual(len(names), len(set(names)))
        for artifact in names:
            self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}", artifact)
        self.assertIn("${{ matrix.shard }}", self.GATE_JOBS["regression"][2])

    def test_failed_upload_is_reported_and_never_claimed_as_retained(self):
        for job, (_, upload_name, _) in self.GATE_JOBS.items():
            with self.subTest(job=job):
                upload = self.step(job, upload_name)
                self.assertEqual(upload["id"], "gate_diagnostics")
                report = [s for s in self.job_steps[job]
                         if "steps.gate_diagnostics.outcome" in s["body"] and s is not upload]
                self.assertEqual(len(report), 1)
                body = report[0]["body"]
                self.assertIn("always() && steps.gate_diagnostics.outcome == 'failure'", body)
                self.assertIn("::warning", body)
                self.assertIn("NOT retained", body)
                self.assertIn("outcome=${{ steps.gate_diagnostics.outcome }}", body)
                self.assertNotIn("continue-on-error", report[0])
                self.assertLess(self.index(job, report[0]["name"]), self.index(job, self.DIRTY))

    def test_permissions_triggers_and_secrets_surface_are_unchanged(self):
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertNotIn("GITHUB_TOKEN", self.text)
        self.assertNotIn("schedule:", self.text)
        self.assertIn("on:\n  pull_request:\n  workflow_dispatch:\n", self.text)
        self.assertIn("ATLAS_DISPOSABLE_CHECKOUT", self.text)
        # The approval public-key anchor is checked exactly once, in preflight.
        self.assertEqual(self.text.count("secrets."), 1)
        self.assertIn("secrets.ATLAS_APPROVAL_PUBKEY_FINGERPRINT",
                      self.jobs_text["preflight"])
        for job in ("structural", "regression", "fault-injection", "actions-pass-full"):
            self.assertNotIn("secrets.", self.jobs_text[job])
        # No retry/backoff was introduced around the diagnostic transport.
        self.assertNotIn("retry", self.text)

    def test_python_version_and_checkout_pin_repeated_identically_per_job(self):
        for job in ("preflight", "structural", "regression", "fault-injection"):
            with self.subTest(job=job):
                self.assertIn("python-version: '3.11'", self.jobs_text[job])
                self.assertIn(
                    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1",
                    self.jobs_text[job],
                )
        # Only the regression matrix needs full git history (PIT backdating
        # proof over real commit history) — fetch-depth: 0 stays scoped to it.
        # ★ Parsed YAML, not a raw substring search — the surrounding banner
        #   comments mention "fetch-depth" in prose and would false-positive
        #   a plain text `in` check on a job whose body a naive line-splitter
        #   attributes trailing inter-job comments to.
        jobs = yaml.safe_load(self.text)["jobs"]

        def checkout_with(job):
            for step in jobs[job]["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    return step.get("with", {})
            self.fail(f"job {job!r} has no actions/checkout step")

        self.assertEqual(checkout_with("regression").get("fetch-depth"), 0)
        for job in ("preflight", "structural", "fault-injection"):
            self.assertNotIn("fetch-depth", checkout_with(job))

    def test_aggregate_job_needs_every_authoritative_job_and_runs_no_new_check(self):
        aggregate = yaml.safe_load(self.text)["jobs"]["actions-pass-full"]
        self.assertEqual(sorted(aggregate["needs"]),
                         sorted(["preflight", "structural", "regression", "fault-injection"]))
        steps = aggregate["steps"]
        self.assertEqual(len(steps), 1)
        self.assertNotIn("run_all.py", steps[0]["run"])

    def test_regression_matrix_is_exactly_four_shards_zero_based(self):
        doc = yaml.safe_load(self.text)
        matrix = doc["jobs"]["regression"]["strategy"]["matrix"]
        self.assertEqual(matrix["shard"], [0, 1, 2, 3])
        self.assertEqual(doc["jobs"]["regression"]["strategy"]["fail-fast"], False)
        self.assertIn("--shard-count 4", self.jobs_text["regression"])
        self.assertIn("--shard-index ${{ matrix.shard }}", self.jobs_text["regression"])


class ShardPreflightRegressionTest(unittest.TestCase):
    def test_malformed_population_without_fail_fast_stops_before_any_work(self):
        original = list(RUNNER.APPROVED_TESTS)
        cases = (
            (original + [original[0]], [Path(p).name for p in original]),
            ([], []),
            ([original[0]], [Path(original[0]).name]),
        )
        for population, actual_names in cases:
            with self.subTest(population_size=len(population)):
                runner = RUNNER.Runner(shard=1)
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(RUNNER, "APPROVED_TESTS", population))
                    stack.enter_context(mock.patch.object(RUNNER.os, "listdir", return_value=actual_names + ["test_fault_injection.py"]))
                    stack.enter_context(mock.patch.object(RUNNER.sys, "argv", [
                        "run_all.py", "--authoritative", "--regression-shard-count", "2", "--regression-shard-index", "1",
                    ]))
                    stack.enter_context(mock.patch.object(RUNNER, "Runner", return_value=runner))
                    temporary = stack.enter_context(mock.patch.object(RUNNER.tempfile, "TemporaryDirectory"))
                    guard = stack.enter_context(mock.patch.object(RUNNER, "disposable_checkout_proof"))
                    children = [stack.enter_context(mock.patch.object(runner, name)) for name in
                                ("snapshot", "rebuild", "compare", "approved_tests", "fault_injection", "boundary", "child")]
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    result = RUNNER.main()
                self.assertEqual(result, 1)
                temporary.assert_not_called()
                guard.assert_not_called()
                for child in children:
                    child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
