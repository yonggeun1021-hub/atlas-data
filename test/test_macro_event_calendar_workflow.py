#!/usr/bin/env python3
"""macro-event-calendar.yml structure: workflow_dispatch ONLY (no schedule
active -- the proposed cron lives in a comment pending explicit user
approval, the same way kr-paper-runtime-daily-publish.yml stayed
dispatch-only before its schedule was approved), pinned action SHAs,
offline regression before capture, bounded push-retry commit scoped to
this collector's own evidence tree and latest pointer only. Offline YAML
parse only -- the workflow is never dispatched from tests."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "macro-event-calendar.yml"


def load(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def triggers(document: dict) -> set:
    return set(document.get("on", document.get(True)))


def steps(document: dict) -> list[dict]:
    return [step for job in document["jobs"].values() for step in job["steps"]]


class MacroEventCalendarWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.document, self.text = load(WORKFLOW)

    def test_triggers_are_workflow_dispatch_only_no_schedule(self):
        self.assertEqual(triggers(self.document), {"workflow_dispatch"})
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("push:", self.text)
        on_block = self.document.get("on", self.document.get(True))
        self.assertNotIn("schedule", on_block)

    def test_proposed_cron_is_documented_but_not_active(self):
        # The proposed cron string must appear only inside a comment line,
        # never inside a real "schedule:" trigger block.
        self.assertIn("'15 22 * * *'", self.text)
        self.assertNotIn("schedule:", self.text)
        for line in self.text.splitlines():
            if "'15 22 * * *'" in line:
                self.assertTrue(line.strip().startswith("#"))

    def test_no_secret_is_referenced(self):
        self.assertEqual(re.findall(r"secrets\.[A-Z_]+", self.text), [])

    def test_offline_regression_runs_before_capture(self):
        names = [step.get("name") for step in steps(self.document)]
        offline = next(i for i, n in enumerate(names) if n and "Offline contract regression" in n)
        capture = next(i for i, n in enumerate(names) if n and "Capture macro event calendar" in n)
        self.assertLess(offline, capture)

    def test_commit_step_touches_only_this_collectors_evidence_and_pointer(self):
        commit_step = next(step for step in steps(self.document) if "git add" in step.get("run", ""))
        add_line = next(line.strip() for line in commit_step["run"].splitlines() if line.strip().startswith("git add "))
        self.assertEqual(
            add_line.split()[2:],
            ["evidence/macro_event_calendar", "data/latest_macro_event_calendar.json"],
        )

    def test_uses_pinned_action_shas(self):
        for step in steps(self.document):
            uses = step.get("uses")
            if uses:
                self.assertRegex(uses, r"@[0-9a-f]{40}\s*(#.*)?$")

    def test_sources_and_all_years_inputs_map_to_cli_flags(self):
        on_block = self.document.get("on", self.document.get(True))
        inputs = on_block["workflow_dispatch"]["inputs"]
        self.assertIn("sources", inputs)
        self.assertIn("all_years", inputs)
        self.assertIn("--sources", self.text)
        self.assertIn("--all-years", self.text)

    def test_commit_step_has_bounded_push_retry_via_shared_script(self):
        # 2026-09-18 consolidation: this step's own inline retry loop (it had
        # no `set -e`, so a conflicted rebase would not abort) is now
        # .github/scripts/push_to_default_branch.sh -- see
        # test/test_push_retry_consolidation.py for its own bounded/
        # fail-closed proof.
        commit_step = next(step for step in steps(self.document) if "git add" in step.get("run", ""))
        run = commit_step["run"]
        self.assertIn("push_to_default_branch.sh", run)
        self.assertRegex(run, r'push_to_default_branch\.sh\s+"\$DEFAULT_BRANCH"\s+3\b')


if __name__ == "__main__":
    unittest.main()
