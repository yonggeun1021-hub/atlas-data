#!/usr/bin/env python3
"""KR sector index history rotation-study workflow security regression.

Offline YAML structure checks only -- no KRX call. Pins: manual dispatch only,
read-only token, no persisted checkout credentials, the KRX secret present
only in the single backfill step env (never in run text, job env or other
steps), inputs passed through env instead of being interpolated into shell,
offline tests and the pre-registration hash before any network step, private
records confined to runner temp, upload of the validated aggregate-only public
directory only, and a final tracked-change prohibition. `run_all.py` executes
this file directly.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "kr-sector-history-rotation-study.yml"
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


class KrSectorHistoryStudyWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)
        cls.triggers = cls.workflow.get("on", cls.workflow.get(True))
        cls.job = cls.workflow["jobs"]["backfill-and-study"]
        cls.steps = cls.job["steps"]
        cls.by_name = {step["name"]: step for step in cls.steps if "name" in step}

    def test_workflow_dispatch_only(self):
        self.assertEqual(set(self.triggers), {"workflow_dispatch"})
        self.assertEqual(set(self.triggers["workflow_dispatch"]["inputs"]), {"start_date", "end_date"})
        self.assertEqual(list(self.workflow["jobs"]), ["backfill-and-study"])

    def test_read_only_permissions_and_no_persisted_credentials(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotIn("permissions", self.job)
        checkout = [s for s in self.steps if str(s.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(checkout), 1)
        self.assertIs(checkout[0]["with"]["persist-credentials"], False)
        for step in self.steps:
            if "uses" in step:
                self.assertRegex(step["uses"], PINNED_ACTION)

    def test_secret_only_in_backfill_step_env(self):
        self.assertEqual(self.text.count("secrets."), 1)
        self.assertNotIn("env", self.workflow)
        self.assertNotIn("env", self.job)
        backfill = self.by_name["Backfill KRX sector index history into runner temp"]
        self.assertEqual(backfill["env"]["KRX_API_KEY"], "${{ secrets.KRX_API_KEY }}")
        for step in self.steps:
            self.assertNotIn("secrets.", str(step.get("run", "")))
            self.assertNotIn("secrets.", str(step.get("with", "")))
            if step is not backfill:
                self.assertNotIn("secrets.", str(step.get("env", "")))
        self.assertNotIn("echo \"$KRX_API_KEY", self.text)
        self.assertNotIn("set -x", self.text)
        self.assertNotIn("KRX_ID", self.text)
        self.assertNotIn("KRX_PW", self.text)

    def test_inputs_never_interpolated_into_shell(self):
        for step in self.steps:
            self.assertNotIn("${{", str(step.get("run", "")), step.get("name"))

    def test_offline_tests_and_preregistration_precede_network(self):
        names = [step.get("name") for step in self.steps]
        order = [
            "Offline regression",
            "Verify pre-registration hash",
            "Plan request budget without network",
            "Capture official KRX holiday calendars",
            "Backfill KRX sector index history into runner temp",
            "Run pre-registered KR rotation event study",
            "Validate aggregate-only artifact",
            "Upload aggregate statistics and input hashes only",
            "Tracked output prohibition",
        ]
        self.assertEqual([n for n in names if n in order], order)
        offline = self.by_name["Offline regression"]["run"]
        self.assertIn("test/test_kr_sector_index_history_backfill.py", offline)
        self.assertIn("test/test_kr_rotation_event_study.py", offline)
        self.assertIn("--plan-only", self.by_name["Plan request budget without network"]["run"])

    def test_private_records_stay_in_runner_temp_and_only_public_dir_uploads(self):
        backfill = self.by_name["Backfill KRX sector index history into runner temp"]["run"]
        self.assertIn('--records-dir "$RUNNER_TEMP/kr-sector-history/private/records"', backfill)
        self.assertIn('--receipt-out "$RUNNER_TEMP/kr-sector-history/public/BACKFILL_RECEIPT.json"', backfill)
        upload = self.by_name["Upload aggregate statistics and input hashes only"]
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/kr-sector-history/public")
        self.assertEqual(upload["if"], "always() && steps.validate.outcome == 'success'")
        self.assertNotIn("private", str(upload["with"]))
        uploads = [s for s in self.steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        validate = self.by_name["Validate aggregate-only artifact"]
        self.assertEqual(validate["id"], "validate")
        self.assertIn("--validate-public", validate["run"])

    def test_no_repository_write_path(self):
        for forbidden in ("git push", "git commit", "git add", "gh pr", "contents: write", "create-pull-request"):
            self.assertNotIn(forbidden, self.text)
        final = self.steps[-1]
        self.assertEqual(final["name"], "Tracked output prohibition")
        self.assertEqual(final["if"], "always()")
        self.assertIn("git diff --exit-code", final["run"])
        self.assertIn('test -z "$(git status --porcelain)"', final["run"])

    def test_bounded_runtime(self):
        self.assertLessEqual(self.job["timeout-minutes"], 120)
        self.assertIs(self.workflow["concurrency"]["cancel-in-progress"], False)


if __name__ == "__main__":
    unittest.main()
