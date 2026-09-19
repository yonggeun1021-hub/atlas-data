#!/usr/bin/env python3
"""Scheduled crypto PAPER runtime producer and stablecoin cutoff contract."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import crypto_paper_runtime as RUNTIME  # noqa: E402
from regime import crypto_paper_runtime_publication as PUBLICATION  # noqa: E402

PRODUCER = ROOT / ".github" / "workflows" / "crypto-paper-runtime.yml"
STABLECOIN = ROOT / ".github" / "workflows" / "stablecoin-capture.yml"
PINNED_CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
PINNED_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"

_spec = importlib.util.spec_from_file_location("crypto_runtime_fixtures", ROOT / "test" / "test_crypto_paper_runtime.py")
FIXTURES = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FIXTURES)


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True)) or {}


def cron_minutes(workflow: dict) -> list[int]:
    result = []
    for item in triggers(workflow)["schedule"]:
        minute, hour, day, month, weekday = item["cron"].split()
        assert (day, month, weekday) == ("*", "*", "*")
        result.append(int(hour) * 60 + int(minute))
    return sorted(result)


class CryptoPaperRuntimeScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.producer = load(PRODUCER)
        cls.steps = cls.producer["jobs"]["publish"]["steps"]
        cls.script = "\n".join(step.get("run", "") for step in cls.steps)

    def test_producer_runs_after_07z_decision_time_off_the_hour(self):
        minutes = cron_minutes(self.producer)
        self.assertTrue(minutes)
        for value in minutes:
            self.assertGreater(value, 7 * 60)
            self.assertNotIn(value % 60, {0, 30})
        self.assertIn("workflow_dispatch", triggers(self.producer))
        self.assertEqual(RUNTIME.DECISION_TIME.isoformat(), "07:00:00")

    def test_least_privilege_concurrency_and_no_secrets(self):
        self.assertEqual(self.producer["permissions"], {"contents": "write"})
        self.assertEqual(self.producer["concurrency"],
                         {"group": "atlas-crypto-paper-runtime", "cancel-in-progress": False})
        text = PRODUCER.read_text(encoding="utf-8")
        self.assertNotIn("secrets.", text)
        self.assertNotRegex(text, r"curl|wget|pip install")
        uses = [step["uses"] for step in self.steps if "uses" in step]
        self.assertEqual(uses, [PINNED_CHECKOUT, PINNED_PYTHON])

    def test_verifies_contract_then_builds_checks_and_commits_only_runtime_outputs(self):
        self.assertIn("python3 test/test_crypto_paper_runtime.py", self.script)
        build = self.script.index("--output \"$LATEST\"\n")
        check = self.script.index("--output \"$LATEST\" --check")
        commit = self.script.index("git commit")
        self.assertLess(build, check)
        self.assertLess(check, commit)
        self.assertIn('CODE_REVISION="$(git rev-parse HEAD)"', self.script)
        self.assertIn('git reset --hard "origin/$DEFAULT_BRANCH"', self.script)
        added = re.findall(r"git add ([^\n]+)", self.script)
        self.assertEqual(added, ['"$LATEST" "$EVIDENCE"'])
        self.assertIn('LATEST="data/latest_crypto_paper_runtime_decision.json"', self.script)
        self.assertIn('EVIDENCE="evidence/regime/crypto_paper_runtime/$DECISION_DATE/$DIGEST.json"', self.script)
        self.assertIn("append-only evidence conflict", self.script)

    def all_run_text(self):
        return "\n".join(
            step.get("run", "")
            for job in self.producer["jobs"].values() for step in job["steps"]
        )

    def test_no_force_push_of_any_form(self):
        pushes = re.findall(r"git\s+push\b[^\n]*", self.all_run_text())
        self.assertEqual(pushes, ['git push origin "HEAD:$DEFAULT_BRANCH"; then'])
        forced = re.compile(r"\s(-[A-Za-z]*f[A-Za-z]*|--force\S*|--mirror|--delete|-d)(\s|$)|[\s\"':]\+")
        for push in pushes:
            self.assertIsNone(forced.search(push), push)
        for variant in ("git push -f origin x", "git push --force origin x", "git push --force-with-lease origin x",
                        'git push origin "+HEAD:main"', "git push origin +HEAD:main", "git push -uf origin x"):
            self.assertIsNotNone(forced.search(variant), variant)

    def test_commit_never_stages_implicitly(self):
        commits = re.findall(r"git\s+commit\b[^\n]*", self.all_run_text())
        self.assertEqual(len(commits), 1)
        implicit = re.compile(r"\s(-[A-Za-z]*a[A-Za-z]*|--all|--amend)(\s|$)")
        for commit in commits:
            self.assertIsNone(implicit.search(commit), commit)
        for variant in ("git commit -a -m x", "git commit -am x", "git commit --all -m x"):
            self.assertIsNotNone(implicit.search(variant), variant)

    def test_every_candidate_head_reruns_contract_tests_and_retry_skips_published_day(self):
        loop = self.script.index("for attempt in")
        reset = self.script.index('git reset --hard "origin/$DEFAULT_BRANCH"')
        tests = self.script.index("python3 test/test_crypto_paper_runtime.py")
        kraken = self.script.index("python3 test/test_crypto_kraken_btc_replay_diagnostic.py")
        skip = self.script.index("--published-for-current-date")
        build = self.script.index('--output "$LATEST"\n')
        push = self.script.index("git push")
        self.assertLess(loop, reset)
        self.assertLess(reset, tests)
        self.assertLess(tests, kraken)
        self.assertLess(kraken, skip)
        self.assertLess(skip, build)
        self.assertLess(build, push)
        self.assertEqual(self.script.count("python3 test/test_crypto_paper_runtime.py"), 1)
        skip_block = self.script[self.script.rindex("if ", 0, skip):build]
        self.assertIn("exit 0", skip_block)


    def test_stablecoin_has_pre_cutoff_primary_and_backup(self):
        minutes = cron_minutes(load(STABLECOIN))
        self.assertEqual(minutes[0], 5 * 60 + 50)
        self.assertEqual(len([m for m in minutes if m < 7 * 60]), 2)
        for value in minutes:
            self.assertNotIn(value % 60, {0, 30})
        self.assertLess(minutes[0], min(cron_minutes(self.producer)))

    def test_retry_captures_after_07z_remain_lookahead_rejected(self):
        start, current = FIXTURES.D(2026, 9, 14), FIXTURES.D(2026, 9, 20)
        baseline = FIXTURES.evaluate(FIXTURES.chain(start, current))
        self.assertEqual(baseline["runtime_regime"], "RISK_ON")
        for value in cron_minutes(load(STABLECOIN)):
            hour, minute = divmod(value, 60)
            available_at = f"2026-09-20T{hour:02d}:{minute:02d}:00Z"
            records = FIXTURES.chain(start, current)
            records["2026-09-20"]["stablecoin"]["available_at"] = available_at
            packet = FIXTURES.evaluate(copy.deepcopy(records), evaluation_at="2026-09-20T08:45:00Z")
            with self.subTest(available_at=available_at):
                if value < 7 * 60:
                    self.assertEqual(packet["runtime_regime"], "RISK_ON", packet["reasons"])
                else:
                    self.assertEqual(packet["runtime_regime"], "UNKNOWN")
                    self.assertIn("LIQUIDITY_LOOKAHEAD", packet["reasons"])


class PublishedForCurrentDateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    def publish(self, target: Path, evaluation_at: str) -> None:
        with mock.patch("builtins.print"):
            self.assertEqual(PUBLICATION.main([
                "--evaluation-at", evaluation_at, "--code-revision", self.code_revision, "--output", str(target)]), 0)

    def cli(self, target: Path, evaluation_at: str) -> int:
        with mock.patch("builtins.print"):
            return PUBLICATION.main([
                "--evaluation-at", evaluation_at, "--code-revision", self.code_revision, "--output", str(target),
                "--published-for-current-date"])

    def test_retry_slot_does_not_republish_same_utc_day(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.json"
            self.assertFalse(PUBLICATION.published_for_current_date(target, "2026-09-14T07:15:00Z"))
            self.assertEqual(self.cli(target, "2026-09-14T07:15:00Z"), 3)
            self.publish(target, "2026-09-14T07:15:00Z")
            first = target.read_bytes()
            self.assertTrue(PUBLICATION.published_for_current_date(target, "2026-09-14T08:45:00Z"))
            self.assertTrue(PUBLICATION.published_for_current_date(target, "2026-09-15T06:59:59Z"))
            self.assertEqual(self.cli(target, "2026-09-14T08:45:00Z"), 0)
            self.assertEqual(target.read_bytes(), first)
            self.assertFalse(PUBLICATION.published_for_current_date(target, "2026-09-15T07:00:00Z"))
            self.assertEqual(self.cli(target, "2026-09-15T07:15:00Z"), 3)

    def test_unverifiable_published_packet_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.json"
            self.publish(target, "2026-09-14T07:15:00Z")
            original = target.read_bytes()
            target.write_bytes(original.replace(b'"BLOCKED"', b'"PAPER_RUNTIME_CLASSIFIED"', 1))
            self.assertFalse(PUBLICATION.published_for_current_date(target, "2026-09-14T08:45:00Z"))
            target.write_bytes(b"not json")
            self.assertFalse(PUBLICATION.published_for_current_date(target, "2026-09-14T08:45:00Z"))


if __name__ == "__main__":
    unittest.main()
