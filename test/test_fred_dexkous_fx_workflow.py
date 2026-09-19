#!/usr/bin/env python3
"""fred-dexkous-fx.yml structure: daily schedule (user-approved 2026-09-15)
plus workflow_dispatch, FRED_API_KEY scoped to one step, offline
regression before capture, bounded push-retry commit scoped to the
evidence tree only. Offline YAML parse only -- the workflow is never
dispatched from tests."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "fred-dexkous-fx.yml"


def load(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def triggers(document: dict) -> set:
    return set(document.get("on", document.get(True)))


def steps(document: dict) -> list[dict]:
    return [step for job in document["jobs"].values() for step in job["steps"]]


class FredDexkousFxWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.document, self.text = load(WORKFLOW)

    def test_triggers_are_schedule_and_workflow_dispatch_only(self):
        self.assertEqual(triggers(self.document), {"schedule", "workflow_dispatch"})
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("push:", self.text)

    def test_approved_cron_is_active_and_matches_the_approved_string(self):
        on_block = self.document.get("on", self.document.get(True))
        self.assertEqual(on_block["schedule"], [{"cron": "40 21 * * 0-5"}])
        self.assertIn("'40 21 * * 0-5'", self.text)

    def test_cron_timing_is_rejustified_against_h10_weekly_publication(self):
        self.assertIn("H.10 release is a WEEKLY release", self.text)
        self.assertIn("Monday", self.text)

    def test_secret_scoped_to_exactly_one_step(self):
        referenced = set(re.findall(r"secrets\.([A-Z_]+)", self.text))
        self.assertEqual(referenced, {"FRED_API_KEY"})
        secret_steps = 0
        for step in steps(self.document):
            env = step.get("env", {})
            if any("secrets." in str(v) for v in env.values()):
                secret_steps += 1
        self.assertEqual(secret_steps, 1)

    def test_offline_regression_runs_before_capture(self):
        names = [step.get("name") for step in steps(self.document)]
        offline = next(i for i, n in enumerate(names) if n and "Offline contract regression" in n)
        capture = next(i for i, n in enumerate(names) if n and "Capture FRED DEXKOUS" in n)
        self.assertLess(offline, capture)

    def test_commit_step_touches_only_the_dexkous_evidence_tree(self):
        commit_step = next(step for step in steps(self.document) if "git add" in step.get("run", ""))
        add_line = next(line.strip() for line in commit_step["run"].splitlines() if line.strip().startswith("git add "))
        self.assertEqual(add_line.split()[2:], ["evidence/fred_dexkous_fx"])

    def test_uses_pinned_action_shas(self):
        for step in steps(self.document):
            uses = step.get("uses")
            if uses:
                self.assertRegex(uses, r"@[0-9a-f]{40}\s*(#.*)?$")

    def test_backfill_input_maps_to_backfill_flag(self):
        on_block = self.document.get("on", self.document.get(True))
        self.assertIn("backfill", on_block["workflow_dispatch"]["inputs"])
        self.assertIn("--backfill", self.text)

    def test_commit_step_has_bounded_push_retry_with_pull_rebase(self):
        # 2026-09-15 incident: the first live run failed on a push race
        # with spdr-sector-holdings.yml, with no retry at all.
        commit_step = next(step for step in steps(self.document) if "git add" in step.get("run", ""))
        run = commit_step["run"]
        self.assertIn("git pull --rebase", run)
        self.assertIn("max_attempts=3", run)
        self.assertIn("until git push", run)
        # A real (non-race) failure must still fail the job, not loop
        # forever or succeed silently.
        self.assertIn("exit 1", run)


if __name__ == "__main__":
    unittest.main()
