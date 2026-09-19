#!/usr/bin/env python3
"""US-DATA-1 U3 workflow structure: manual only, least privilege, no secret echo.

Offline YAML checks only. The workflows are never dispatched from tests.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from regime import us_replay_range_declaration as DECL  # noqa: E402
from regime import us_replay_range_driver as DRIVER  # noqa: E402
PROBE = ROOT / ".github" / "workflows" / "us-regime-replay-range-probe.yml"
REPLAY = ROOT / ".github" / "workflows" / "us-regime-historical-replay.yml"
SECRET_NAMES = {"FRED_API_KEY", "ALPACA_MARKET_DATA_API_KEY", "ALPACA_MARKET_DATA_API_SECRET"}


def load(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def triggers(document: dict) -> dict:
    return document.get("on", document.get(True))


def steps(document: dict) -> list[dict]:
    return [step for job in document["jobs"].values() for step in job["steps"]]


class CommonWorkflowPolicy:
    path: Path
    permissions: dict

    def setUp(self):
        self.document, self.text = load(self.path)

    def test_workflow_dispatch_only(self):
        self.assertEqual(set(triggers(self.document)), {"workflow_dispatch"})
        self.assertNotIn("schedule", self.text)

    def test_least_privilege_permissions_and_concurrency(self):
        self.assertEqual(self.document["permissions"], self.permissions)
        for job in self.document["jobs"].values():
            self.assertNotIn("permissions", job)
            self.assertLessEqual(job["timeout-minutes"], 350)
        self.assertIs(self.document["concurrency"]["cancel-in-progress"], False)
        self.assertTrue(self.document["concurrency"]["group"])

    def test_secrets_only_in_step_env_never_in_scripts(self):
        referenced = set(re.findall(r"secrets\.([A-Z_]+)", self.text))
        self.assertEqual(referenced - {"GITHUB_TOKEN"}, SECRET_NAMES)
        for step in steps(self.document):
            script = step.get("run", "")
            self.assertNotIn("secrets.", script)
            self.assertNotIn("${{", script, "expressions must reach scripts through env, never inline")
            self.assertNotIn("set -x", script)
            for name in SECRET_NAMES:
                self.assertNotRegex(script, rf"(echo|printf|cat).*\$\{{?{name}")
            env = step.get("env", {})
            secret_env = {key for key, value in env.items() if "secrets." in str(value)}
            if secret_env:
                self.assertEqual(secret_env, SECRET_NAMES)

    def test_checkout_does_not_persist_credentials_and_nothing_is_committed(self):
        checkout = [step for step in steps(self.document) if str(step.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(checkout), 1)
        self.assertIs(checkout[0]["with"]["persist-credentials"], False)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("git commit", self.text)
        self.assertEqual(steps(self.document)[-1]["name"], "Tracked output prohibition")

    def test_outputs_live_under_runner_temp(self):
        for step in steps(self.document):
            if str(step.get("uses", "")).startswith("actions/upload-artifact@"):
                for line in str(step["with"]["path"]).splitlines():
                    self.assertTrue(line.strip().startswith("${{ runner.temp }}/"), line)


class ProbeWorkflowTest(CommonWorkflowPolicy, unittest.TestCase):
    path = PROBE
    permissions = {"contents": "read"}

    def test_only_public_outputs_are_uploaded(self):
        uploads = [step for step in steps(self.document) if str(step.get("uses", "")).startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["with"]["path"], "${{ runner.temp }}/us-probe/upload")
        self.assertNotIn("private", self.text.split("Upload public-safe probe output")[0].split("Offline analysis")[1])

    def test_uploaded_names_match_the_committed_path_convention(self):
        self.assertIn(f"upload/{DECL.SUMMARY_FILE_NAME}", self.text)
        self.assertIn(f"upload/{DECL.DECLARATION_FILE_NAME}", self.text)

    def test_calendar_endpoint_is_a_closed_choice(self):
        inputs = triggers(self.document)["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"alpaca_calendar_url"})
        self.assertEqual(inputs["alpaca_calendar_url"]["type"], "choice")
        self.assertEqual(
            inputs["alpaca_calendar_url"]["options"],
            ["https://paper-api.alpaca.markets/v2/calendar", "https://api.alpaca.markets/v2/calendar"],
        )


class ReplayWorkflowTest(CommonWorkflowPolicy, unittest.TestCase):
    path = REPLAY
    permissions = {"contents": "read", "actions": "read"}

    def test_no_date_inputs_and_committed_declaration(self):
        inputs = triggers(self.document)["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"resume_from_run_id", "time_budget_minutes"})
        self.assertEqual(self.document["env"]["DECLARATION"], "evidence/us_regime_replay/range_declaration_v1.json")
        self.assertNotRegex(self.text, r"--date\b")

    def test_resume_download_and_overwriting_chunk_upload(self):
        names = [step.get("name") for step in steps(self.document)]
        download = steps(self.document)[names.index("Download chunks from the resumed run")]
        self.assertEqual(download["with"]["name"], "us-regime-replay-chunks")
        self.assertEqual(download["with"]["github-token"], "${{ github.token }}")
        upload = steps(self.document)[names.index("Upload chunks (resume source)")]
        self.assertEqual(upload["with"]["name"], "us-regime-replay-chunks")
        self.assertIs(upload["with"]["overwrite"], True)
        self.assertEqual(upload["if"], "always()")
        self.assertLess(names.index("Run pending chunks (paced, time-bounded)"), names.index("Finalize full range and evaluate US PIT acceptance"))

    def test_declaration_rebuild_is_verified_before_plan_and_run(self):
        env = self.document["env"]
        self.assertEqual(env["PROBE_SUMMARY"], "evidence/us_regime_replay/probe_summary_v1.json")
        self.assertEqual(Path(env["PROBE_SUMMARY"]).name, DECL.SUMMARY_FILE_NAME)
        self.assertEqual(Path(env["DECLARATION"]).name, DECL.DECLARATION_FILE_NAME)
        self.assertEqual(Path(env["PROBE_SUMMARY"]).parent, Path(env["DECLARATION"]).parent)
        scripts = [step.get("run", "") for step in steps(self.document)]
        verify = [i for i, script in enumerate(scripts) if "us_replay_range_declaration.py verify" in script]
        driver = [i for i, script in enumerate(scripts) if "us_replay_range_driver.py" in script]
        self.assertEqual(len(verify), 1)
        self.assertTrue(driver)
        self.assertLess(verify[0], min(driver))
        self.assertIn('test -f "$PROBE_SUMMARY"', "\n".join(scripts[:verify[0]]))

    def test_time_budget_and_chunk_size_match_the_driver(self):
        inputs = triggers(self.document)["workflow_dispatch"]["inputs"]
        budget = inputs["time_budget_minutes"]
        self.assertEqual(budget["default"], str(DRIVER.MAX_TIME_BUDGET_MINUTES))
        self.assertIn(f"(1-{DRIVER.MAX_TIME_BUDGET_MINUTES})", budget["description"])
        self.assertEqual(self.document["env"]["CHUNK_SIZE"], str(DRIVER.DEFAULT_CHUNK_SIZE))
        for job in self.document["jobs"].values():
            # Budget, one last paced chunk start and finalize fit in the job timeout.
            self.assertGreaterEqual(job["timeout-minutes"], DRIVER.MAX_TIME_BUDGET_MINUTES + 20)

    def test_finalize_step_holds_no_secret(self):
        for step in steps(self.document):
            if step.get("name", "").startswith("Finalize"):
                self.assertNotIn("env", step)


if __name__ == "__main__":
    unittest.main()
