#!/usr/bin/env python3
"""Alpaca SIP daily-bars workflow structure: manual dispatch only (no
cron), secrets scoped to exactly one step's env, never echoed in any
``run`` script, offline regression before the network call, a schema
re-check before commit, and a guarded commit/push (only when something is
actually staged). Offline YAML parse only -- the workflow is never
dispatched from tests.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

WORKFLOW = ROOT / ".github" / "workflows" / "alpaca-sip-daily-bars.yml"
SECRET_NAMES = {"ALPACA_MARKET_DATA_API_KEY", "ALPACA_MARKET_DATA_API_SECRET"}


def load(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def triggers(document: dict) -> set:
    return set(document.get("on", document.get(True)))


def steps(document: dict) -> list[dict]:
    return [step for job in document["jobs"].values() for step in job["steps"]]


class AlpacaSipDailyBarsWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.document, self.text = load(WORKFLOW)

    def test_workflow_dispatch_only_no_schedule(self):
        # Checks the parsed trigger set, not a text substring -- the
        # workflow's own explanatory comment legitimately uses the English
        # word "schedule" to say a cron needs separate approval.
        self.assertEqual(triggers(self.document), {"workflow_dispatch"})
        on_block = self.document.get("on", {})
        for forbidden in ("schedule", "cron", "pull_request", "push"):
            self.assertNotIn(forbidden, on_block)

    def test_permissions_contents_write_and_concurrency_guarded(self):
        self.assertEqual(self.document["permissions"], {"contents": "write"})
        for job in self.document["jobs"].values():
            self.assertNotIn("permissions", job)
            self.assertNotIn("concurrency", job)
        self.assertIs(self.document["concurrency"]["cancel-in-progress"], False)
        self.assertTrue(self.document["concurrency"]["group"])

    def test_secrets_only_in_one_step_env_never_echoed_in_scripts(self):
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

    def test_step_order_offline_regression_before_capture_before_schema_check_before_commit(self):
        names = [step.get("name") for step in steps(self.document)]
        offline = names.index(next(n for n in names if n and "Offline regression" in n))
        collect = names.index(next(n for n in names if n and "Collect SIP daily bars" in n))
        schema = names.index(next(n for n in names if n and "Schema re-check" in n))
        commit = names.index(next(n for n in names if n and "Commit derived" in n))
        self.assertLess(offline, collect)
        self.assertLess(collect, schema)
        self.assertLess(schema, commit)

    def test_offline_regression_runs_all_three_new_test_modules(self):
        step = next(step for step in steps(self.document) if step.get("name") and "Offline regression" in step["name"])
        self.assertIn("test/test_us_liquidity_sip_source.py", step["run"])
        self.assertIn("test/test_us_listing_lookup.py", step["run"])
        self.assertIn("test/test_alpaca_sip_daily_bars.py", step["run"])

    def test_only_derived_data_paths_are_committed(self):
        commit = next(step for step in steps(self.document) if step.get("name") == "Commit derived liquidity evidence and latest pointer")
        self.assertIn("git add data/us_sip_daily_liquidity data/latest_us_sip_daily_liquidity.json", commit["run"])
        # never stages anything else (no "git add ." / "git add -A")
        self.assertNotIn("git add .", commit["run"])
        self.assertNotIn("git add -A", commit["run"])

    def test_commit_is_guarded_on_an_actual_staged_diff(self):
        commit = next(step for step in steps(self.document) if step.get("name") == "Commit derived liquidity evidence and latest pointer")
        self.assertIn("git diff --staged --quiet", commit["run"])
        self.assertIn("exit 0", commit["run"])

    def test_pinned_action_shas(self):
        for step in steps(self.document):
            uses = step.get("uses")
            if uses:
                self.assertRegex(uses, r"@[0-9a-f]{40}\s*(#.*)?$")

    def test_symbol_universe_is_never_expanded_in_this_workflow(self):
        # The collector reads config/free_market_data_contract.json itself;
        # this workflow must not pass a different/expanded symbol list.
        self.assertNotIn("--symbols", self.text)
        self.assertNotIn("SYMBOLS=", self.text)


if __name__ == "__main__":
    unittest.main()
