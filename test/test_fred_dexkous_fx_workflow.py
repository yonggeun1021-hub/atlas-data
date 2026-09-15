#!/usr/bin/env python3
"""fred-dexkous-fx.yml structure: manual dispatch only, no cron enabled,
FRED_API_KEY scoped to one step, offline regression before capture, commit
scoped to the evidence tree only. Offline YAML parse only -- the workflow
is never dispatched from tests."""
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

    def test_workflow_dispatch_only_no_cron(self):
        self.assertEqual(triggers(self.document), {"workflow_dispatch"})
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("push:", self.text)
        for line in self.text.splitlines():
            self.assertFalse(re.match(r"^\s*schedule:\s*$", line), line)

    def test_proposed_cron_is_documented_but_commented_out(self):
        # The proposed schedule lives only in a comment / PR body, never as
        # an active trigger.
        self.assertIn("Proposed cron (NOT enabled", self.text)
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- cron:"):
                self.fail(f"an active cron trigger is present: {line!r}")

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


if __name__ == "__main__":
    unittest.main()
