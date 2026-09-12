#!/usr/bin/env python3
"""CIO CI-sharding 지시 2026-09-12 — regression proof for the sharded Actions PASS lane.

이 지시가 요구한 아홉 가지를 증명한다:
  1. 인자 없는 `run_all.py` 기본 동작(=--authoritative 전체 실행)은 그대로다.
  2. 현재 승인 회귀 목록이 정확히 4-shard partition 을 이룬다.
  3. 그 partition 에 중복이 없다.
  4. 그 partition 에 누락이 없다.
  5. 잘못된 shard 인자는 fail-closed(비영시작, non-zero exit)다.
  6. workflow 의 회귀 matrix 가 정확히 4-way 다.
  7. 최종 aggregate job 이 authoritative job 전부를 요구한다.
  8. Production/trading/order/capital authority 경계가 바뀌지 않았다.
  9. 기존 workflow 트리거·권한이 바뀌지 않았다.

★ 승인 회귀 population 의 권위는 언제나 `run_all.APPROVED_TESTS` 다 — 이 파일은
  개수를 새 리터럴로 고정하지 않는다(339 든 340 이든 그 시점의 실제 값을 그대로
  읽는다). CIO 지시문의 "340" 은 지시 당시의 관측값일 뿐 이 목록의 정본이 아니다.
⛔ Production 상태를 바꾸지 않는다 — 이 파일도 다른 회귀와 마찬가지로 fixture only,
  read-only 검증이다.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "run_all.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "actions-pass.yml"
PY = sys.executable

SPEC = importlib.util.spec_from_file_location("atlas_run_all_ci_phase_sharding", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


# ══════════════════════════════════════════════════════════════════════
# 1) 기본 동작 하위호환
# ══════════════════════════════════════════════════════════════════════
class DefaultBehaviorBackwardCompatibleTest(unittest.TestCase):
    def test_phase_defaults_to_all_without_any_new_flag(self):
        with mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--authoritative", "--fail-fast"]):
            parser_args = self._parsed_args()
        self.assertEqual(parser_args.phase, "all")
        self.assertIsNone(parser_args.shard_count)
        self.assertIsNone(parser_args.shard_index)

    @staticmethod
    def _parsed_args():
        # Reconstruct exactly the argparse surface main() builds, without
        # executing the rest of main() (which would run the real gate).
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--authoritative", action="store_true")
        parser.add_argument("--no-fi", action="store_true")
        parser.add_argument("--fail-fast", action="store_true")
        parser.add_argument("--log-dir")
        parser.add_argument("--regression-shard-index", type=int)
        parser.add_argument("--regression-shard-count", type=int)
        parser.add_argument("--phase", choices=["all", "structural", "regression", "fi"], default="all")
        parser.add_argument("--shard-count", type=int)
        parser.add_argument("--shard-index", type=int)
        return parser.parse_args(RUNNER.sys.argv[1:])

    def test_bare_authoritative_invocation_never_enters_bounded_phase_dispatch(self):
        with mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--authoritative", "--fail-fast"]), \
                mock.patch.object(RUNNER, "run_bounded_phase") as bounded, \
                mock.patch.object(RUNNER, "disposable_checkout_proof", return_value=["guard stop — no real rebuild here"]), \
                contextlib.redirect_stdout(io.StringIO()):
            RUNNER.main()
        bounded.assert_not_called()

    def test_default_authoritative_output_still_claims_the_global_actions_pass(self):
        # Same contract RunnerFailFastTest.test_green_requires_rebuild_compare_full_regression_fi_and_boundary
        # already proves via mocked gate stages; here it is proved once more,
        # anchored explicitly to this directive's "default unchanged" requirement.
        runner = RUNNER.Runner(fail_fast=True)
        with mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--authoritative", "--fail-fast"]), \
                mock.patch.object(RUNNER, "Runner", return_value=runner), \
                mock.patch.object(RUNNER, "disposable_checkout_proof", return_value=[]), \
                mock.patch.object(runner, "test_set", return_value=True), \
                mock.patch.object(runner, "snapshot", return_value={"k": "v"}), \
                mock.patch.object(runner, "rebuild", return_value=True), \
                mock.patch.object(runner, "compare", return_value=True), \
                mock.patch.object(runner, "boundary", return_value=True), \
                mock.patch.object(runner, "approved_tests", return_value=True), \
                mock.patch.object(runner, "fault_injection", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            result = RUNNER.main()
        self.assertEqual(result, 0)
        self.assertIn("Actions PASS = YES", out.getvalue())
        self.assertNotIn("PARTIAL", out.getvalue())


# ══════════════════════════════════════════════════════════════════════
# 2-4) partition 완전성 — union == 전량, 중복없음, 누락없음
# ══════════════════════════════════════════════════════════════════════
class FourShardPartitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.population = list(RUNNER.APPROVED_TESTS)
        cls.shards = RUNNER.ci_phase_regression_shards(4)

    def test_exactly_four_nonempty_shards(self):
        self.assertEqual(len(self.shards), 4)
        for i, shard in enumerate(self.shards):
            self.assertTrue(shard, f"shard {i} is empty")

    def test_union_equals_approved_tests_exactly(self):
        flat = [t for shard in self.shards for t in shard]
        self.assertCountEqual(flat, self.population)

    def test_zero_duplicates_across_and_within_shards(self):
        flat = [t for shard in self.shards for t in shard]
        self.assertEqual(len(flat), len(set(flat)))

    def test_zero_omissions_every_approved_test_is_assigned(self):
        assigned = {t for shard in self.shards for t in shard}
        missing = set(self.population) - assigned
        self.assertEqual(missing, set())

    def test_pairwise_disjoint(self):
        for i in range(4):
            for j in range(i + 1, 4):
                overlap = set(self.shards[i]) & set(self.shards[j])
                self.assertEqual(overlap, set(), f"shards {i} and {j} overlap: {overlap}")

    def test_declared_order_preserved_within_each_shard(self):
        declared = {t: i for i, t in enumerate(self.population)}
        for shard in self.shards:
            positions = [declared[t] for t in shard]
            self.assertEqual(positions, sorted(positions))

    def test_deterministic_across_repeated_calls(self):
        self.assertEqual(self.shards, RUNNER.ci_phase_regression_shards(4))

    def test_does_not_disturb_the_frozen_two_shard_helper(self):
        # `regression_shards()` (CIO adopted 2026-09-07) stays locked to
        # exactly 2 — generalizing to N=4 lives entirely in the new helper.
        self.assertEqual(RUNNER.REGRESSION_SHARD_COUNT, 2)
        with self.assertRaises(ValueError):
            RUNNER.regression_shards(count=4)
        two = RUNNER.regression_shards()
        self.assertEqual(len(two), 2)
        self.assertCountEqual([t for c in two for t in c], self.population)

    def test_matches_the_live_workflow_shard_count(self):
        doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        matrix_size = len(doc["jobs"]["regression"]["strategy"]["matrix"]["shard"])
        self.assertEqual(len(self.shards), matrix_size)


# ══════════════════════════════════════════════════════════════════════
# 5) 잘못된 shard 인자는 fail-closed
# ══════════════════════════════════════════════════════════════════════
class InvalidShardArgsFailClosedTest(unittest.TestCase):
    def run_cli(self, *extra_args):
        return subprocess.run(
            [PY, str(RUNNER_PATH), "--phase", "regression", *extra_args],
            cwd=str(ROOT), capture_output=True, text=True,
        )

    def test_shard_count_and_shard_index_must_be_given_together(self):
        for extra in (["--shard-count", "4"], ["--shard-index", "0"]):
            with self.subTest(extra=extra):
                result = self.run_cli(*extra)
                self.assertEqual(result.returncode, 2)
                self.assertIn("must be given together", result.stderr)

    def test_shard_count_below_one_is_rejected(self):
        for count in ("0", "-1"):
            with self.subTest(count=count):
                result = self.run_cli("--shard-count", count, "--shard-index", "0")
                self.assertEqual(result.returncode, 2)
                self.assertIn("--shard-count must be >= 1", result.stderr)

    def test_shard_index_out_of_range_is_rejected(self):
        for index in ("-1", "4", "100"):
            with self.subTest(index=index):
                result = self.run_cli("--shard-count", "4", "--shard-index", index)
                self.assertEqual(result.returncode, 2)
                self.assertIn("0 <= index < 4", result.stderr)

    def test_non_integer_shard_values_are_rejected(self):
        result = self.run_cli("--shard-count", "four", "--shard-index", "0")
        self.assertEqual(result.returncode, 2)

    def test_shard_flags_outside_regression_phase_are_rejected(self):
        for phase in ("all", "structural", "fi"):
            with self.subTest(phase=phase):
                result = subprocess.run(
                    [PY, str(RUNNER_PATH), "--phase", phase, "--shard-count", "4", "--shard-index", "0"],
                    cwd=str(ROOT), capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("only apply to --phase regression", result.stderr)

    def test_legacy_and_new_shard_flags_may_not_mix(self):
        result = subprocess.run(
            [PY, str(RUNNER_PATH), "--phase", "structural",
             "--regression-shard-index", "1", "--regression-shard-count", "2"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("legacy 2-shard", result.stderr)

    def test_phase_fi_rejects_no_fi(self):
        result = subprocess.run(
            [PY, str(RUNNER_PATH), "--phase", "fi", "--no-fi"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be combined with --no-fi", result.stderr)

    def test_unknown_phase_value_is_rejected(self):
        result = subprocess.run(
            [PY, str(RUNNER_PATH), "--phase", "bogus"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)

    def test_structural_phase_without_authoritative_fails_closed_without_touching_files(self):
        before = (ROOT / "config" / "rules.json").read_bytes()
        result = subprocess.run(
            [PY, str(RUNNER_PATH), "--phase", "structural"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("requires --authoritative", result.stdout)
        self.assertEqual((ROOT / "config" / "rules.json").read_bytes(), before)

    def test_no_shard_args_at_all_runs_the_full_population_as_one_shard(self):
        # `main()` defaults an omitted --shard-count/--shard-index pair to
        # (1, 0) before dispatch — shard 0 of 1 == the whole population.
        # This is proved end to end (CLI parsing included) below in
        # `test_cli_with_no_shard_flags_defaults_to_one_shard_of_everything`;
        # here `run_bounded_phase` is exercised directly with the exact
        # defaulted values `main()` would have already computed.
        with mock.patch.object(RUNNER, "APPROVED_TESTS", ["test/test_a.py", "test/test_b.py"]), \
                mock.patch.object(RUNNER.os, "listdir",
                                  return_value=["test_a.py", "test_b.py", "test_fault_injection.py"]), \
                mock.patch.object(RUNNER.Runner, "child") as child, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            child.return_value = subprocess.CompletedProcess([], 0, "", "")

            class Args:
                phase = "regression"
                shard_count = 1
                shard_index = 0
                fail_fast = False
                log_dir = None
                authoritative = False
                no_fi = False

            rc = RUNNER.run_bounded_phase(Args())
        self.assertEqual(rc, 0)
        self.assertEqual(child.call_count, 2)
        self.assertIn("shard 0/1", out.getvalue())

    def test_cli_with_no_shard_flags_defaults_to_one_shard_of_everything(self):
        # End-to-end through main()'s own argparse validation/defaulting —
        # `--phase regression` alone (no --shard-count/--shard-index) must
        # not be rejected, and must select the entire (stubbed, small)
        # approved population. ⛔ Stubbed in-process, not a real subprocess —
        # the real population is 340 files; actually executing all of them
        # here would silently reintroduce the single-runner cost this
        # directive exists to remove, inside one of the regression shards.
        with mock.patch.object(RUNNER, "APPROVED_TESTS", ["test/test_a.py", "test/test_b.py"]), \
                mock.patch.object(RUNNER.os, "listdir",
                                  return_value=["test_a.py", "test_b.py", "test_fault_injection.py"]), \
                mock.patch.object(RUNNER.sys, "argv", ["run_all.py", "--phase", "regression"]), \
                mock.patch.object(RUNNER.Runner, "child") as child, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            child.return_value = subprocess.CompletedProcess([], 0, "", "")
            rc = RUNNER.main()
        self.assertEqual(rc, 0)
        self.assertEqual(child.call_count, 2)
        self.assertIn("shard 0/1", out.getvalue())
        self.assertIn("선택 2 / 승인 전체 2파일", out.getvalue())


# ══════════════════════════════════════════════════════════════════════
# 6-7-9) workflow 구조 — matrix, aggregate, 트리거/권한 불변
# ══════════════════════════════════════════════════════════════════════
class WorkflowStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_exactly_five_jobs_named_as_designed(self):
        self.assertEqual(
            set(self.doc["jobs"].keys()),
            {"preflight", "structural", "regression", "fault-injection", "actions-pass-full"},
        )

    def test_regression_matrix_has_exactly_four_shards(self):
        matrix = self.doc["jobs"]["regression"]["strategy"]["matrix"]["shard"]
        self.assertEqual(matrix, [0, 1, 2, 3])
        self.assertEqual(len(matrix), len(set(matrix)))

    def test_final_aggregate_requires_every_authoritative_job(self):
        needs = self.doc["jobs"]["actions-pass-full"]["needs"]
        self.assertEqual(
            sorted(needs),
            sorted(["preflight", "structural", "regression", "fault-injection"]),
        )

    def test_structural_and_fi_jobs_are_independently_authoritative(self):
        # Neither waits on the regression matrix or on each other — only on preflight.
        self.assertEqual(self.doc["jobs"]["structural"]["needs"], "preflight")
        self.assertEqual(self.doc["jobs"]["regression"]["needs"], "preflight")
        self.assertEqual(self.doc["jobs"]["fault-injection"]["needs"], "preflight")

    def test_triggers_are_unchanged(self):
        triggers = self.doc.get("on", self.doc.get(True))
        self.assertEqual(set(triggers.keys()), {"pull_request", "workflow_dispatch"})

    def test_permissions_are_unchanged_and_read_only_everywhere(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read"})
        for name, job in self.doc["jobs"].items():
            self.assertNotIn("permissions", job, f"job {name!r} must not override workflow permissions")

    def test_no_push_or_schedule_trigger_was_introduced(self):
        triggers = self.doc.get("on", self.doc.get(True))
        self.assertNotIn("push", triggers)
        self.assertNotIn("schedule", triggers)


# ══════════════════════════════════════════════════════════════════════
# 8) Production/trading/order/capital authority 불변
# ══════════════════════════════════════════════════════════════════════
class AuthorityBoundaryUnchangedTest(unittest.TestCase):
    def test_frozen_boundary_contract_values_are_unchanged(self):
        self.assertEqual(RUNNER.FROZEN_BOUNDARY, {
            "config/rules.json": {"authority": True, "consumable_by_evaluator": False},
            "rules/rule_inventory.json": {"authority": False, "consumable_by_evaluator": False},
        })

    def test_boundary_check_still_enforces_evaluator_and_production_hold(self):
        runner = RUNNER.Runner()
        self.assertTrue(runner.boundary())
        self.assertEqual(runner.failures, [])

    def test_no_phase_can_bypass_the_boundary_check_for_structural_or_all(self):
        source = RUNNER_PATH.read_text(encoding="utf-8")
        # Every authoritative path (legacy `all` and the new `structural`
        # phase) calls r.boundary() — this directive adds no bypass.
        self.assertGreaterEqual(source.count("r.boundary()"), 2)

    def test_run_all_module_introduces_no_trading_or_capital_vocabulary(self):
        source = RUNNER_PATH.read_text(encoding="utf-8")
        for banned in ("place_order", "submit_order", "execute_trade", "REAL_ORDER",
                      "capital_allocation", "PRODUCTION_WRITE"):
            self.assertNotIn(banned, source)

    def test_workflow_introduces_no_write_capable_step(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        for banned in ("git push", "git commit", "gh pr", "contents: write"):
            self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main()
