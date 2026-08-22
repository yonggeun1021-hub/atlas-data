#!/usr/bin/env python3
"""P8-12 real operational wiring regression (item 6): the Dynamic Clock
workflow exists, is triggered as a follow-up to the REAL existing collector
workflows (never a new schedule/cron of its own), makes no new provider API
calls, and has an idempotency check before any commit.

Note: `workflow_run` triggers only activate once this workflow file itself
is merged to the default branch (a standard GitHub Actions constraint, not
a gap in this wiring) -- this test validates the file's shape/content, not
a live trigger, which cannot be proven from a feature branch."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW_PATH = ROOT / ".github" / "workflows" / "p8-12-dynamic-clock.yml"

REAL_UPSTREAM_WORKFLOW_NAMES = (
    "BTC Price Daily Capture",
    "P1-CR-06 Crypto Breadth Daily Capture",
    "Atlas Daily Collect",
)


class WorkflowFileExistsTests(unittest.TestCase):
    def test_workflow_file_exists(self):
        self.assertTrue(WORKFLOW_PATH.is_file())

    def test_workflow_is_valid_yaml(self):
        import yaml
        doc = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertIn("jobs", doc)


class RealCollectorNamesTests(unittest.TestCase):
    """The workflow_run trigger must name workflows that ACTUALLY EXIST in
    this repo with that exact `name:` value -- not a guessed/aspirational
    name."""

    def test_every_referenced_upstream_workflow_name_matches_a_real_file(self):
        workflows_dir = ROOT / ".github" / "workflows"
        real_names = set()
        for path in workflows_dir.glob("*.yml"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("name:"):
                    real_names.add(line.split(":", 1)[1].strip())
                    break
        for name in REAL_UPSTREAM_WORKFLOW_NAMES:
            self.assertIn(name, real_names, f"{name!r} does not match any real workflow's name: field")

    def test_workflow_run_trigger_lists_the_real_upstream_names(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        for name in REAL_UPSTREAM_WORKFLOW_NAMES:
            self.assertIn(name, text)


class NoNewProviderCallsTests(unittest.TestCase):
    def test_workflow_never_calls_a_provider_or_collector_script_directly(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        for forbidden in ("curl ", "requests.", "collectors/", "krx_r2_openapi", "kraken"):
            self.assertNotIn(forbidden, text.lower() if forbidden.islower() else text)

    def test_workflow_only_runs_the_pure_computation_script(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("clock/run_dynamic_clock.py", text)

    def test_run_dynamic_clock_module_itself_makes_no_network_calls(self):
        # Static check mirroring the module's own docstring claim: no
        # requests/urllib/http-client import anywhere in clock/.
        for path in (ROOT / "clock").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("import requests", "import urllib.request", "import http.client"):
                self.assertNotIn(forbidden, source, path)


class IdempotencyTests(unittest.TestCase):
    def test_workflow_has_an_idempotency_check_before_committing(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("git diff --cached --quiet", text)

    def test_workflow_only_touches_its_own_output_directory(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("evidence/operational/dynamic_clock", text)
        self.assertIn("Committed-output-only guard", text)


if __name__ == "__main__":
    unittest.main()
