#!/usr/bin/env python3
"""US sector rotation backfill-study workflow structure (offline YAML checks).

Manual only, least privilege, secrets only in the backfill step env, vendor rows
only under RUNNER_TEMP private/, one aggregated JSON upload gated by the schema
check, nothing committed. The workflow is never dispatched from tests.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "us-sector-rotation-backfill-study.yml"
SECRET_NAMES = {"ALPACA_MARKET_DATA_API_KEY", "ALPACA_MARKET_DATA_API_SECRET"}
ARTIFACT = "us_sector_rotation_event_study_v1.json"

_spec = importlib.util.spec_from_file_location("us_price_history_backfill_for_workflow_test", ROOT / "collectors" / "us_price_history_backfill.py")
BF = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(BF)


def steps(document):
    return [step for job in document["jobs"].values() for step in job["steps"]]


class SectorRotationBackfillStudyWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.document = yaml.safe_load(self.text)
        self.steps = steps(self.document)
        self.names = [step.get("name") for step in self.steps]

    def step(self, prefix):
        matches = [s for s in self.steps if str(s.get("name", "")).startswith(prefix)]
        self.assertEqual(len(matches), 1, prefix)
        return matches[0]

    def test_workflow_dispatch_only_without_inputs(self):
        triggers = self.document.get("on", self.document.get(True))
        self.assertEqual(set(triggers), {"workflow_dispatch"})
        self.assertIsNone(triggers["workflow_dispatch"])
        self.assertNotIn("schedule", self.text)
        self.assertNotIn("workflow_run", self.text)
        self.assertNotIn("pull_request", self.text)

    def test_least_privilege_concurrency_and_timeout(self):
        self.assertEqual(self.document["permissions"], {"contents": "read"})
        self.assertEqual(len(self.document["jobs"]), 1)
        for job in self.document["jobs"].values():
            self.assertNotIn("permissions", job)
            self.assertLessEqual(job["timeout-minutes"], 60)
        self.assertEqual(self.document["concurrency"]["group"], "us-sector-rotation-backfill-study")
        self.assertIs(self.document["concurrency"]["cancel-in-progress"], False)

    def test_actions_are_pinned_to_full_commit_shas(self):
        uses = [str(s["uses"]) for s in self.steps if "uses" in s]
        self.assertEqual(len(uses), 3)
        for use in uses:
            self.assertRegex(use, r"^actions/(checkout|setup-python|upload-artifact)@[0-9a-f]{40}$")

    def test_checkout_does_not_persist_credentials_and_nothing_is_committed(self):
        checkout = [s for s in self.steps if str(s.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(checkout), 1)
        self.assertIs(checkout[0]["with"]["persist-credentials"], False)
        for forbidden in ("git push", "git commit", "git add", "gh ", "GITHUB_TOKEN", "github.token"):
            self.assertNotIn(forbidden, self.text)

    def test_secrets_only_in_backfill_step_env_never_in_scripts(self):
        self.assertEqual(set(re.findall(r"secrets\.([A-Z_]+)", self.text)), SECRET_NAMES)
        backfill = self.step("Bounded Alpaca backfill")
        for step in self.steps:
            script = step.get("run", "")
            self.assertNotIn("secrets.", script)
            self.assertNotIn("${{", script, "expressions must reach scripts through env, never inline")
            self.assertNotIn("set -x", script)
            self.assertNotRegex(script, r"\benv\b\s*($|\|)|printenv")
            for name in SECRET_NAMES:
                self.assertNotRegex(script, rf"(echo|printf|cat|tee).*\$\{{?{name}")
            env = step.get("env") or {}
            secret_env = {key for key, value in env.items() if "secrets." in str(value)}
            if step is backfill:
                self.assertEqual(secret_env, SECRET_NAMES)
                self.assertEqual({key: str(value) for key, value in env.items()},
                                 {name: f"${{{{ secrets.{name} }}}}" for name in SECRET_NAMES})
            else:
                self.assertEqual(secret_env, set(), step.get("name"))
        self.assertNotIn("env", self.document["jobs"]["study"])
        self.assertEqual(set(self.document.get("env", {})), {"PYTHONDONTWRITEBYTECODE"})

    def test_backfill_is_live_required_bounded_and_writes_outside_checkout(self):
        script = self.step("Bounded Alpaca backfill")["run"]
        self.assertIn("set -euo pipefail", script)
        invocations = re.findall(r"python3 collectors/us_price_history_backfill.py([^>]*)>", script)
        self.assertEqual(len(invocations), 2, "one attempt plus at most one resume attempt")
        for args in invocations:
            self.assertIn("--live", args)
            self.assertIn("--require-live", args)
            self.assertIn('--out-dir "$private/backfill"', args)
            for widening in ("--start-date", "--window-days", "--max-requests", "--request-pause-seconds",
                             "--batch-pause-seconds", "--batch-size", "--contract"):
                self.assertNotIn(widening, args)
        self.assertIn('private="$RUNNER_TEMP/us-rotation-study/private"', script)
        self.assertIn("date -u -d yesterday +%F", script)
        self.assertIn('> "$private/receipt.json"', script)
        # Stated bound matches the code bound (+1 for the single failed attempt before resume).
        self.assertIn(f"at most {BF.MAX_LIVE_REQUESTS + 1} requests", self.step("Bounded Alpaca backfill")["name"])
        self.assertIn(f"at most {BF.MAX_LIVE_REQUESTS} HTTP requests", self.text)

    def test_preregistration_and_offline_regression_run_before_any_request(self):
        order = [self.names.index(n) for n in (
            "Offline backfill and study regression", "Verify pre-registration hash",
        )]
        backfill = self.names.index(self.step("Bounded Alpaca backfill")["name"])
        study = self.names.index("Run pre-registered US rotation event study (offline, no secrets)")
        check = self.names.index("Artifact schema check before upload")
        upload = self.names.index("Upload aggregated statistics only")
        self.assertLess(max(order), backfill)
        self.assertLess(backfill, study)
        self.assertLess(study, check)
        self.assertLess(check, upload)
        regression = self.steps[order[0]]["run"]
        self.assertIn("test/test_us_price_history_backfill.py", regression)
        self.assertIn("test/test_us_sector_rotation_event_study.py", regression)
        self.assertIn("verify-preregistration", self.steps[order[1]]["run"])

    def test_study_reads_private_dir_and_writes_only_the_upload_file(self):
        script = self.step("Run pre-registered US rotation event study")["run"]
        self.assertIn('--backfill-dir "$work/private/backfill"', script)
        self.assertIn(f'--out "$work/upload/{ARTIFACT}"', script)
        self.assertIn('work="$RUNNER_TEMP/us-rotation-study"', script)
        check = self.step("Artifact schema check before upload")["run"]
        self.assertIn("check-artifact", check)
        self.assertIn('-type f | wc -l)" -eq 1', check)

    def test_only_the_aggregated_json_is_uploaded(self):
        uploads = [s for s in self.steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        upload = uploads[0]
        self.assertNotIn("if", upload, "upload must only run after the schema check succeeded")
        self.assertEqual(upload["with"]["path"], f"${{{{ runner.temp }}}}/us-rotation-study/upload/{ARTIFACT}")
        self.assertNotIn("private", str(upload["with"]))
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertLessEqual(upload["with"]["retention-days"], 30)

    def test_final_step_asserts_clean_checkout_and_artifact_schema(self):
        final = self.steps[-1]
        self.assertEqual(final["name"], "Tracked output and artifact schema prohibition")
        self.assertEqual(final["if"], "always()")
        self.assertIn("git diff --exit-code", final["run"])
        self.assertIn('test -z "$(git status --porcelain)"', final["run"])
        self.assertIn("check-artifact", final["run"])
        self.assertNotIn("env", final)


if __name__ == "__main__":
    unittest.main()
