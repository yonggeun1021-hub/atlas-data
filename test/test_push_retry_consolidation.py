#!/usr/bin/env python3
"""One push-retry code path for append-only evidence producers.

Two halves:

1. Behavioural proof that .github/scripts/push_to_default_branch.sh retries in a
   bounded way and fails closed. These tests build real git repositories in a
   temp dir and run the real script against them -- they are not regex checks
   over the shell source, because the 2026-09-15 push-race incident and the
   spdr `set -e` gap were both behavioural, not textual.

2. A consolidation guard: every workflow step that commits and pushes must call
   that one script. Divergent copies are how population-symbol-observation-daily
   .yml went daily on 2026-09-18 with no retry at all while fred-dexkous-fx.yml
   and spdr-sector-holdings.yml had carried one since 2026-09-15. Three explicit
   exception tables below are the only permitted non-shared pushes, and all
   three are self-invalidating: REGISTRY_PINNED entries are checked against the
   live pin (unpinning a workflow turns its exception into a failure telling
   you to convert it), and FROZEN_UNTIL entries carry an expiry date that fails
   the moment it passes.

No workflow is ever dispatched from these tests.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / ".github" / "scripts" / "push_to_default_branch.sh"
WORKFLOWS = ROOT / ".github" / "workflows"
REGISTRY = ROOT / "config" / "regime_source_owner_registry_v2.json"

# ---------------------------------------------------------------------------
# Exception table 1: workflow files whose bytes are sha256-pinned in
# config/regime_source_owner_registry_v2.json. That registry is itself pinned by
# nine configs and by ratified append-only evidence, so converting these would
# reach committed evidence. They keep their own inline push until the pin is
# deliberately retired. Value = number of raw `git push` sites in the file.
REGISTRY_PINNED: dict[str, int] = {
    "crypto-breadth-capture.yml": 4,
    "paper-regime-reference.yml": 1,
}

# Exception table 2: steps that must NOT rebase-and-retry, because what they
# publish is a function of the branch tip. Replaying such a commit on a newer
# base republishes a stale computation as though it were fresh, so these fail
# closed or rebuild from the new head instead. Key = (workflow file, step name),
# value = (number of raw `git push` sites, why).
NO_REBASE_BY_DESIGN: dict[tuple[str, str], tuple[int, str]] = {
    ("daily-briefing.yml", "Publish provider-free daily briefing packet"): (
        3, "the briefing locator is built from main's tip; on a race it rebuilds once rather than replaying a stale locator",
    ),
    ("us-paper-runtime.yml", "Build, verify and commit US PAPER runtime decision"): (
        1, "the runtime decision is derived from the head it was built on; on a race it rebuilds from the new head",
    ),
    ("crypto-paper-runtime.yml", "Build, verify and commit crypto PAPER runtime decision"): (
        1, "same as us-paper-runtime: the decision is a function of the head it was built on",
    ),
    ("p3-11-wildcard-intake.yml", "Push without rebase or force"): (
        1, "intentionally fails closed if main advanced rather than publishing a stale intake",
    ),
    ("p8-12-dynamic-clock.yml", "Push (fail closed on a concurrent-push race, never force)"): (
        1, "the clock is recomputed by whatever lands next; rebasing would publish a stale clock",
    ),
    ("cross-market-flow-transition-ledger.yml", "Commit append-only evidence and latest pointer"): (
        1, "after a rebase this step re-verifies capital_flow_posture_reference and rebuilds the ledger "
           "against the new tip, then amends the commit -- replaying the original commit unchanged "
           "would republish a ledger built against a stale reference. Pre-existing gap, not touched by "
           "this consolidation: the rebuild is a single attempt with no bound, so a second race in the "
           "same run still fails the step outright rather than retrying; giving it a bounded retry needs "
           "a rebuild loop, not the shared replay script, and is tracked as separate follow-up work.",
    ),
}

# Exception table 3: workflows whose bytes are frozen on the operational server
# side, independent of anything in this repo's own config/ or evidence chain.
# Key = workflow file, value = (number of raw `git push` sites, expiry date as
# an ISO date string, why). test_frozen_exceptions_have_not_expired fails once
# `expires` has passed, so an entry cannot be silently forgotten past its date.
FROZEN_UNTIL: dict[str, tuple[int, str, str]] = {
    "stablecoin-capture.yml": (
        1, "2026-09-24",
        "the Ubuntu schedule dispatcher (/opt/atlas-schedule-dispatcher) fetches this "
        "workflow's live bytes from the GitHub contents API at ref: main and compares "
        "them to a blob_sha/event_ref_fingerprint pin in "
        "/etc/atlas-schedule-dispatcher/config.json; on mismatch it returns "
        "drift_blocked and permanently resolves that day's slot with no dispatch, no "
        "retry, and no alarm. The crypto PAPER runtime's 5-consecutive-day clock "
        "(day 1 secured 2026-09-18) depends on that dispatcher firing through "
        "2026-09-23, so this file must not change before the dispatcher is re-pinned "
        "on 2026-09-24 (see PR #815 review comment). Converting it is a one-line "
        "follow-up once the freeze lifts.",
    ),
}

COMMENT = re.compile(r"^\s*#")
PUSH = re.compile(r"\bgit\s+push\b")


def code_lines(run: str) -> str:
    """The step's shell source with comment-only lines removed."""
    return "\n".join(l for l in run.split("\n") if not COMMENT.match(l))


def push_steps() -> list[tuple[str, str, str]]:
    """(workflow filename, step name, step shell source) for every step that pushes."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            continue
        for job in (document.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                body = code_lines(run)
                if PUSH.search(body):
                    found.append((path.name, step.get("name", "<unnamed>"), body))
    return found


def git(*args: str, cwd: Path, check: bool = True, env: dict | None = None):
    full = dict(os.environ)
    full.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    })
    if env:
        full.update(env)
    return subprocess.run(["git", *args], cwd=cwd, check=check, env=full,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


class PushToDefaultBranchBehaviourTest(unittest.TestCase):
    """Runs the real script against real repositories."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pushretry-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        git("init", "--bare", "--initial-branch=main", str(self.origin), cwd=self.tmp)
        self.work = self.clone("work")
        (self.work / "seed.txt").write_text("seed\n")
        git("add", "-A", cwd=self.work)
        git("commit", "-m", "seed", cwd=self.work)
        git("push", "origin", "HEAD:main", cwd=self.work)

    def clone(self, name: str) -> Path:
        target = self.tmp / name
        git("clone", str(self.origin), str(target), cwd=self.tmp)
        return target

    def run_script(self, cwd: Path, *args: str, backoff: str = "0"):
        return subprocess.run(
            ["bash", str(SCRIPT), *(args or ("main",))],
            cwd=cwd, env={**os.environ,
                          "PUSH_RETRY_BACKOFF_SECONDS": backoff,
                          "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                          "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def commit(self, repo: Path, name: str, body: str, message: str):
        (repo / name).write_text(body)
        git("add", "-A", cwd=repo)
        git("commit", "-m", message, cwd=repo)

    def origin_log(self) -> str:
        return git("log", "--format=%s", "main", cwd=self.origin).stdout

    def test_clean_push_succeeds(self):
        self.commit(self.work, "a.txt", "a\n", "evidence a")
        result = self.run_script(self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("evidence a", self.origin_log())

    def test_rebases_and_publishes_when_branch_advanced(self):
        other = self.clone("other")
        self.commit(other, "b.txt", "b\n", "evidence b")
        git("push", "origin", "HEAD:main", cwd=other)

        self.commit(self.work, "a.txt", "a\n", "evidence a")
        result = self.run_script(self.work)

        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.origin_log()
        self.assertIn("evidence a", log)
        self.assertIn("evidence b", log, "the other producer's commit must survive")
        # Replayed on top, not force-pushed over.
        self.assertLess(log.index("evidence a"), log.index("evidence b"))

    def test_conflict_fails_closed_and_publishes_nothing(self):
        other = self.clone("other")
        self.commit(other, "shared.txt", "theirs\n", "evidence theirs")
        git("push", "origin", "HEAD:main", cwd=other)

        self.commit(self.work, "shared.txt", "ours\n", "evidence ours")
        result = self.run_script(self.work)

        self.assertNotEqual(result.returncode, 0, "a conflict must fail the step")
        self.assertIn("nothing was published", result.stderr)
        self.assertNotIn("evidence ours", self.origin_log())
        self.assertIn("evidence theirs", self.origin_log())

    def test_conflict_does_not_leave_the_repo_mid_rebase(self):
        """The spdr-sector-holdings.yml gap: without `set -e` a conflicted
        rebase left the tree mid-rebase and the loop pushed from that HEAD."""
        other = self.clone("other")
        self.commit(other, "shared.txt", "theirs\n", "evidence theirs")
        git("push", "origin", "HEAD:main", cwd=other)
        self.commit(self.work, "shared.txt", "ours\n", "evidence ours")

        self.run_script(self.work)

        for leftover in ("rebase-merge", "rebase-apply"):
            self.assertFalse((self.work / ".git" / leftover).exists(),
                             f"rebase was not aborted: .git/{leftover} remains")

    def reject_hook(self) -> Path:
        """A bare-repo hook that rejects every push and records each attempt."""
        counter = self.tmp / "attempts.txt"
        hook = self.origin / "hooks" / "pre-receive"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho x >> %s\necho 'rejected by test' >&2\nexit 1\n" % counter)
        hook.chmod(0o755)
        return counter

    def test_retry_is_bounded_and_fails_closed_when_push_keeps_failing(self):
        counter = self.reject_hook()
        self.commit(self.work, "a.txt", "a\n", "evidence a")

        result = self.run_script(self.work, "main", "3")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed after 3 attempts", result.stderr)
        self.assertEqual(counter.read_text().count("x"), 3,
                         "must push exactly MAX_ATTEMPTS times, no more and no fewer")
        self.assertNotIn("evidence a", self.origin_log())

    def test_default_attempt_bound_is_five(self):
        counter = self.reject_hook()
        self.commit(self.work, "a.txt", "a\n", "evidence a")

        result = self.run_script(self.work)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(counter.read_text().count("x"), 5)

    def test_commit_dropped_as_empty_is_reported_not_silent(self):
        """If the identical content already landed, the rebase drops this run's
        commit. Exiting 0 without a word would be indistinguishable from having
        published it."""
        other = self.clone("other")
        self.commit(other, "same.txt", "identical\n", "evidence theirs")
        git("push", "origin", "HEAD:main", cwd=other)

        self.commit(self.work, "same.txt", "identical\n", "evidence ours")
        result = self.run_script(self.work)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dropped this run's", result.stderr)
        self.assertIn("already published", result.stderr)

    def test_never_force_pushes(self):
        self.assertNotRegex(SCRIPT.read_text(encoding="utf-8"),
                            r"push[^\n]*(--force|\+HEAD|-f\b)")

    def test_rejects_a_nonsense_attempt_bound(self):
        # "" is deliberately not included here: bash's ${2:-5} treats an empty
        # positional the same as an unset one, so it falls back to the default
        # of 5 -- that is correct `:-` semantics, not a validation gap.
        self.commit(self.work, "a.txt", "a\n", "evidence a")
        for bad in ("0", "-1", "abc"):
            with self.subTest(bad=bad):
                result = self.run_script(self.work, "main", bad)
                self.assertNotEqual(result.returncode, 0, f"accepted {bad!r}")
                self.assertNotIn("evidence a", self.origin_log())

    def test_requires_a_branch_argument(self):
        result = subprocess.run(["bash", str(SCRIPT)], cwd=self.work,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_script_is_executable_and_sets_strict_mode(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), "script must be executable")
        self.assertIn("set -euo pipefail", SCRIPT.read_text(encoding="utf-8"))


class PushRetryConsolidationTest(unittest.TestCase):
    """Every commit-and-push step uses the one shared path, or is a declared exception."""

    def setUp(self):
        self.steps = push_steps()

    def test_there_is_at_least_one_push_step_to_check(self):
        self.assertGreater(len(self.steps), 10, "workflow discovery is broken")

    def test_every_push_step_is_shared_or_a_declared_exception(self):
        offenders = []
        for workflow, step, body in self.steps:
            if "push_to_default_branch.sh" in body:
                continue
            if workflow in REGISTRY_PINNED:
                continue
            if workflow in FROZEN_UNTIL:
                continue
            if (workflow, step) in NO_REBASE_BY_DESIGN:
                continue
            offenders.append(f"{workflow} :: {step}")
        self.assertEqual(offenders, [], (
            "These steps push without .github/scripts/push_to_default_branch.sh.\n"
            "Append-only evidence producers must call it: "
            'bash .github/scripts/push_to_default_branch.sh "$DEFAULT_BRANCH"\n'
            "If the step must not rebase (its output depends on the branch tip), "
            "add it to NO_REBASE_BY_DESIGN with a reason.\nOffenders: "
            + ", ".join(offenders)))

    def test_no_inline_retry_loop_survives_outside_the_exceptions(self):
        """A second retry implementation is the thing being eliminated."""
        offenders = []
        for workflow, step, body in self.steps:
            if workflow in REGISTRY_PINNED or workflow in FROZEN_UNTIL or (workflow, step) in NO_REBASE_BY_DESIGN:
                continue
            if re.search(r"max_attempts|until git push|pull --rebase", body):
                offenders.append(f"{workflow} :: {step}")
        self.assertEqual(offenders, [], "inline push-retry loops remain: " + ", ".join(offenders))

    def test_shared_callers_pass_a_branch_argument(self):
        for workflow, step, body in self.steps:
            if "push_to_default_branch.sh" not in body:
                continue
            with self.subTest(workflow=workflow, step=step):
                for line in body.split("\n"):
                    if "push_to_default_branch.sh" in line:
                        self.assertRegex(
                            line.strip(),
                            r"push_to_default_branch\.sh\s+\"?\$",
                            f"{workflow} :: {step} calls the script with no branch argument")

    def test_registry_pinned_exceptions_are_still_actually_pinned(self):
        """Self-invalidating: if a workflow is unpinned, its exception must go."""
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        pinned: dict[str, str] = {}

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key.endswith("_path") and isinstance(value, str) and value.endswith(".yml"):
                        sha_key = key.replace("_path", "_sha256")
                        if sha_key in node:
                            pinned[Path(value).name] = node[sha_key]
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(registry)
        for workflow in REGISTRY_PINNED:
            with self.subTest(workflow=workflow):
                self.assertIn(workflow, pinned,
                              f"{workflow} is no longer pinned in the registry -- "
                              "convert it to the shared script and drop this exception")
                actual = hashlib.sha256((WORKFLOWS / workflow).read_bytes()).hexdigest()
                self.assertEqual(actual, pinned[workflow],
                                 f"{workflow} no longer matches its registry pin")

    def test_exception_tables_have_no_stale_entries(self):
        seen_files = {w for w, _, _ in self.steps}
        for workflow in REGISTRY_PINNED:
            self.assertIn(workflow, seen_files, f"stale REGISTRY_PINNED entry: {workflow}")
        for workflow in FROZEN_UNTIL:
            self.assertIn(workflow, seen_files, f"stale FROZEN_UNTIL entry: {workflow}")
        keys = {(w, s) for w, s, _ in self.steps}
        for key in NO_REBASE_BY_DESIGN:
            self.assertIn(key, keys, f"stale NO_REBASE_BY_DESIGN entry: {key}")

    def test_declared_raw_push_counts_match_reality(self):
        """Pins the exceptions to an exact size so a new raw push cannot be
        quietly added inside an already-excepted file or step."""
        by_file: dict[str, int] = {}
        for workflow, step, body in self.steps:
            if "push_to_default_branch.sh" in body:
                continue
            by_file[workflow] = by_file.get(workflow, 0) + len(PUSH.findall(body))
        for workflow, expected in REGISTRY_PINNED.items():
            with self.subTest(workflow=workflow):
                self.assertEqual(by_file.get(workflow), expected)
        for workflow, (expected, _expires, _why) in FROZEN_UNTIL.items():
            with self.subTest(workflow=workflow):
                self.assertEqual(by_file.get(workflow), expected)
        for (workflow, step), (expected, _why) in NO_REBASE_BY_DESIGN.items():
            with self.subTest(workflow=workflow, step=step):
                bodies = [b for w, s, b in self.steps if (w, s) == (workflow, step)]
                self.assertEqual(sum(len(PUSH.findall(b)) for b in bodies), expected)

    def test_every_no_rebase_exception_states_a_reason(self):
        for key, (_count, why) in NO_REBASE_BY_DESIGN.items():
            with self.subTest(key=key):
                self.assertGreater(len(why), 30, f"{key} needs a real reason")

    def test_frozen_exceptions_have_not_expired(self):
        """A frozen file that is still frozen past its own expiry date is a
        gap, not a feature -- either the freeze was lifted and this table
        entry is stale, or nobody circled back and the file is still frozen
        with no one tracking it. Either way the fix is to look at it, not to
        push the date."""
        today = dt.date.today()
        for workflow, (_count, expires, why) in FROZEN_UNTIL.items():
            with self.subTest(workflow=workflow):
                self.assertGreater(len(why), 30, f"{workflow} needs a real reason")
                expiry = dt.date.fromisoformat(expires)
                self.assertLessEqual(
                    today, expiry,
                    f"{workflow}'s freeze expired on {expires} -- convert it to the shared "
                    "script now, or confirm the freeze with whoever owns it and move the date")


if __name__ == "__main__":
    unittest.main()
