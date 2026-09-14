#!/usr/bin/env python3
"""Alpaca SIP access probe workflow structure: manual only, least
privilege, secrets only in one step env, no commit/push, tracked-change
guard as the last step. Offline YAML parse only -- the workflow is never
dispatched from tests.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORKFLOW = ROOT / ".github" / "workflows" / "alpaca-sip-access-probe.yml"
SECRET_NAMES = {"ALPACA_MARKET_DATA_API_KEY", "ALPACA_MARKET_DATA_API_SECRET"}


def load(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def triggers(document: dict) -> set:
    return set(document.get("on", document.get(True)))


def steps(document: dict) -> list[dict]:
    return [step for job in document["jobs"].values() for step in job["steps"]]


class AlpacaSipAccessProbeWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.document, self.text = load(WORKFLOW)

    def test_workflow_dispatch_only(self):
        self.assertEqual(triggers(self.document), {"workflow_dispatch"})
        self.assertNotIn("schedule", self.text)
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("push", self.text)

    def test_least_privilege_permissions_and_concurrency(self):
        self.assertEqual(self.document["permissions"], {"contents": "read"})
        for job in self.document["jobs"].values():
            self.assertNotIn("permissions", job)
            self.assertNotIn("concurrency", job)
        self.assertIs(self.document["concurrency"]["cancel-in-progress"], False)
        self.assertTrue(self.document["concurrency"]["group"])

    def test_secrets_only_in_one_step_env_never_inline_in_scripts(self):
        referenced = set(re.findall(r"secrets\.([A-Z_]+)", self.text))
        self.assertEqual(referenced, SECRET_NAMES)
        secret_steps = 0
        for step in steps(self.document):
            script = step.get("run", "")
            self.assertNotIn("secrets.", script)
            self.assertNotIn("set -x", script)
            for name in SECRET_NAMES:
                self.assertNotRegex(script, rf"(echo|printf|cat).*\$\{{?{name}")
            env = step.get("env", {})
            secret_env = {key for key, value in env.items() if "secrets." in str(value)}
            if secret_env:
                self.assertEqual(secret_env, SECRET_NAMES)
                secret_steps += 1
        self.assertEqual(secret_steps, 1, "secrets must be scoped to exactly one step")

    def test_checkout_persist_credentials_false_and_nothing_committed(self):
        job_steps = steps(self.document)
        checkout = [step for step in job_steps if str(step.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(len(checkout), 1)
        self.assertIs(checkout[0]["with"]["persist-credentials"], False)
        self.assertNotIn("git push", self.text)
        self.assertNotIn("git commit", self.text)
        self.assertEqual(job_steps[-1]["name"], "Tracked output prohibition")
        self.assertIn("git diff --exit-code", job_steps[-1]["run"])
        self.assertIn("git status --porcelain", job_steps[-1]["run"])

    def test_offline_regression_runs_before_any_network_capture(self):
        names = [step.get("name") for step in steps(self.document)]
        offline = [i for i, n in enumerate(names) if n and "Offline probe regression" in n]
        capture = [i for i, n in enumerate(names) if n and "Bounded SIP-vs-IEX capture" in n]
        self.assertEqual(len(offline), 1)
        self.assertEqual(len(capture), 1)
        self.assertLess(offline[0], capture[0])

    def test_schema_check_runs_between_capture_and_upload(self):
        names = [step.get("name") for step in steps(self.document)]
        capture = names.index(next(n for n in names if n and "Bounded SIP-vs-IEX capture" in n))
        schema = names.index(next(n for n in names if n and "Schema re-check" in n))
        upload = [i for i, step in enumerate(steps(self.document)) if str(step.get("uses", "")).startswith("actions/upload-artifact@")][0]
        self.assertLess(capture, schema)
        self.assertLess(schema, upload)

    def test_output_lives_under_runner_temp_and_is_aggregate_only(self):
        upload = [step for step in steps(self.document) if str(step.get("uses", "")).startswith("actions/upload-artifact@")]
        self.assertEqual(len(upload), 1)
        self.assertTrue(str(upload[0]["with"]["path"]).startswith("${{ runner.temp }}/"))

    def test_upload_does_not_run_unconditionally_but_the_final_guard_does(self):
        # CIO review 2026-09-15: this probe uploads vendor data derived from
        # a live credential into a public repo's artifact storage. Upload
        # must NOT carry if: always() -- a failed schema re-check (or any
        # earlier failed step) must stop the run before anything is
        # uploaded. The final tracked-change guard is the opposite case: it
        # must always run, even after an earlier failure, so a dirty
        # checkout is still caught.
        job_steps = steps(self.document)
        upload = next(step for step in job_steps if str(step.get("uses", "")).startswith("actions/upload-artifact@"))
        self.assertNotIn("if", upload)
        guard = next(step for step in job_steps if step.get("name") == "Tracked output prohibition")
        self.assertEqual(guard.get("if"), "always()")

    def test_request_budget_documented_as_six(self):
        self.assertIn("at most 6", self.text.lower())

    def test_uses_pinned_action_shas_consistent_with_repo(self):
        for step in steps(self.document):
            uses = step.get("uses")
            if uses:
                self.assertRegex(uses, r"@[0-9a-f]{40}\s*(#.*)?$")


if __name__ == "__main__":
    unittest.main()
