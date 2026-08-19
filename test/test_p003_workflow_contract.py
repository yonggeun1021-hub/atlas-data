#!/usr/bin/env python3
"""P0-03 Daily Collect briefing repair-path workflow contract regression.

증명하는 것:
1. Guard=fresh 는 collector 재수집만 막는다.
2. KRX/DART/SEC와 D1은 기존 Guard 조건을 유지한다.
3. briefing read model build는 Guard=fresh 에서도 실행 가능하다.
4. Commit data도 read-model-only repair를 저장할 수 있다.
5. Guard=fresh 안내문이 workflow 전체 종료라고 잘못 표현하지 않는다.
6. 임시 schedule 없이 정규 3회 수집 슬롯만 유지한다.

증명하지 못하는 것:
- 실제 GitHub Actions runner 실행 결과
- GitHub scheduler queue/start timing
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WF_PATH = ROOT / ".github/workflows/collect.yml"
ACTIONS_PASS_PATH = ROOT / ".github/workflows/actions-pass.yml"

with WF_PATH.open(encoding="utf-8") as fh:
    WF = yaml.safe_load(fh)

with ACTIONS_PASS_PATH.open(encoding="utf-8") as fh:
    ACTIONS_PASS_WF = yaml.safe_load(fh)

STEPS = WF["jobs"]["collect"]["steps"]
ACTIONS_PASS_STEPS = ACTIONS_PASS_WF["jobs"]["actions-pass"]["steps"]


def step(name):
    for item in STEPS:
        if item.get("name") == name:
            return item
    return None


def actions_pass_step(name):
    for item in ACTIONS_PASS_STEPS:
        if item.get("name") == name:
            return item
    return None


class P003WorkflowContractTest(unittest.TestCase):
    def require_step(self, name):
        found = step(name)
        self.assertIsNotNone(found, f"missing workflow step: {name}")
        return found

    def test_collectors_still_obey_guard(self):
        krx = self.require_step("Collect KRX")
        dart = self.require_step("Collect DART")
        sec = self.require_step("Collect SEC")

        self.assertEqual(
            krx.get("if"),
            "steps.guard.outputs.skip != 'yes'",
        )
        self.assertEqual(
            dart.get("if"),
            "always() && steps.guard.outputs.skip != 'yes'",
        )
        self.assertEqual(
            sec.get("if"),
            "always() && steps.guard.outputs.skip != 'yes'",
        )

    def test_d1_still_obeys_guard(self):
        gate = self.require_step("D1 게이트 — SEC 산출물 신선도 확인")

        self.assertEqual(
            gate.get("if"),
            "always() && steps.guard.outputs.skip != 'yes'",
        )

    def test_briefing_build_runs_even_when_guard_is_fresh(self):
        build = self.require_step("Build briefing read model (P0-03)")

        self.assertEqual(build.get("if"), "always()")
        self.assertIn(
            "build_briefing_inputs.py",
            build.get("run", ""),
        )

    def test_commit_can_persist_read_model_only_repair(self):
        commit = self.require_step("Commit data")

        self.assertEqual(commit.get("if"), "always()")
        self.assertIn(
            "git diff --staged --quiet",
            commit.get("run", ""),
        )

    def test_readiness_gate_rechecks_current_files_after_build(self):
        gate = self.require_step(
            "Verify 06:55 briefing readiness contract (P0-03)"
        )

        self.assertEqual(gate.get("if"), "always()")
        self.assertIn(
            "check_briefing_readiness.py",
            gate.get("run", ""),
        )
        self.assertIn("--today", gate.get("run", ""))
        self.assertIn("--write-health", gate.get("run", ""))

    def test_guard_fresh_message_means_collector_skip_only(self):
        notice = self.require_step("Collector 재수집 생략 사유 표시")

        self.assertEqual(
            notice.get("if"),
            "steps.guard.outputs.skip == 'yes'",
        )

        text = notice.get("run", "")
        self.assertIn("collector 재수집", text)
        self.assertIn("read model", text)
        self.assertNotIn("아무것도 하지 않고 종료", text)

    def test_setup_python_remains_collector_only(self):
        setup = next(
            (
                item
                for item in STEPS
                if item.get("uses") == "actions/setup-python@v5"
            ),
            None,
        )

        self.assertIsNotNone(setup)
        self.assertEqual(
            setup.get("if"),
            "steps.guard.outputs.skip != 'yes'",
        )

    def test_actions_pass_does_not_delete_tracked_briefing_bundle(self):
        regression = actions_pass_step(
            "P0-03 briefing read model regression"
        )

        self.assertIsNotNone(regression)
        command = regression.get("run", "")
        self.assertIn("test/test_briefing_inputs.py", command)
        self.assertNotIn("rm -rf data/briefing", command)

    def test_daily_collect_has_only_production_schedule_slots(self):
        triggers = WF.get("on", WF.get(True))
        schedules = triggers["schedule"]
        crons = {item["cron"] for item in schedules}

        self.assertEqual(
            crons,
            {
                "5 21 * * 0-4",
                "25 21 * * 0-4",
                "45 21 * * 0-4",
            },
        )


if __name__ == "__main__":
    unittest.main()
