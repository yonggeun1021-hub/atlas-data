#!/usr/bin/env python3
"""spdr-sector-holdings.yml structure: manual dispatch only, no cron
enabled, no secret used, offline regression before capture, commit scoped
to derived evidence + latest pointer only (never a raw workbook). Offline
YAML parse only -- the workflow is never dispatched from tests."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "spdr-sector-holdings.yml"


def load(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def triggers(document: dict) -> set:
    return set(document.get("on", document.get(True)))


def steps(document: dict) -> list[dict]:
    return [step for job in document["jobs"].values() for step in job["steps"]]


class SpdrSectorHoldingsWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.document, self.text = load(WORKFLOW)

    def test_workflow_dispatch_only_no_cron(self):
        self.assertEqual(triggers(self.document), {"workflow_dispatch"})
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("push:", self.text)
        for line in self.text.splitlines():
            self.assertFalse(re.match(r"^\s*schedule:\s*$", line), line)

    def test_proposed_cron_is_documented_but_not_active(self):
        self.assertIn("Proposed cron (NOT enabled", self.text)
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- cron:"):
                self.fail(f"an active cron trigger is present: {line!r}")

    def test_no_secret_is_referenced(self):
        self.assertNotIn("secrets.", self.text)

    def test_offline_regression_runs_before_capture(self):
        names = [step.get("name") for step in steps(self.document)]
        offline = next(i for i, n in enumerate(names) if n and "Offline contract regression" in n)
        capture = next(i for i, n in enumerate(names) if n and "Capture SPDR sector holdings" in n)
        self.assertLess(offline, capture)

    def test_offline_regression_covers_both_new_modules(self):
        run = next(step["run"] for step in steps(self.document) if step.get("name") and "Offline contract regression" in step["name"])
        self.assertIn("test/test_spdr_sector_holdings.py", run)
        self.assertIn("test/test_us_spdr_sector_mapping.py", run)

    def test_commit_step_never_touches_a_raw_workbook_path(self):
        commit_step = next(step for step in steps(self.document) if "git add" in step.get("run", ""))
        add_line = next(line.strip() for line in commit_step["run"].splitlines() if line.strip().startswith("git add "))
        paths = add_line.split()[2:]
        self.assertEqual(set(paths), {"evidence/spdr_sector_holdings", "data/latest_spdr_sector_holdings.json"})
        for path in paths:
            self.assertNotIn(".xlsx", path)

    def test_first_dispatch_verification_note_present(self):
        self.assertIn("First dispatch is also verification", self.text)

    def test_daily_schedule_recommendation_documented(self):
        self.assertIn("time-gated", self.text)
        self.assertIn("This PR recommends", self.text)
        self.assertIn("proposed daily schedule", self.text)

    def test_uses_pinned_action_shas(self):
        for step in steps(self.document):
            uses = step.get("uses")
            if uses:
                self.assertRegex(uses, r"@[0-9a-f]{40}\s*(#.*)?$")

    def test_dispatch_input_is_never_substituted_directly_into_the_shell(self):
        # PR #761 review item 2: `${{ inputs.tickers }}` must never appear
        # inside a `run:` script body (that substitutes the untrusted
        # value into the script text before the shell parses it -- a
        # script-injection risk). It may only appear on the right-hand
        # side of an `env:` mapping.
        capture_step = next(step for step in steps(self.document) if step.get("name") and "Capture SPDR sector holdings" in step["name"])
        self.assertNotIn("${{ inputs.tickers }}", capture_step.get("run", ""))
        self.assertEqual(capture_step.get("env"), {"TICKERS": "${{ inputs.tickers }}"})
        for step in steps(self.document):
            if step is capture_step:
                continue
            self.assertNotIn("inputs.tickers", step.get("run", ""))

    def test_ticker_variable_is_referenced_quoted(self):
        capture_step = next(step for step in steps(self.document) if step.get("name") and "Capture SPDR sector holdings" in step["name"])
        run = capture_step["run"]
        self.assertIn('"$TICKERS"', run)
        self.assertIn('-n "$TICKERS"', run)
        # Every occurrence of the variable is inside double quotes -- an
        # unquoted use anywhere would defeat the fix (word-splitting/glob).
        total_occurrences = run.count("$TICKERS")
        quoted_occurrences = run.count('"$TICKERS"')
        self.assertEqual(total_occurrences, quoted_occurrences)
        self.assertGreater(total_occurrences, 0)


if __name__ == "__main__":
    unittest.main()
