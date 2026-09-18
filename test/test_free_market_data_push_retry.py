"""US free-market-data publication must survive the push race.

Runs 34911129881 (2026-09-14T23:57Z), 35163739007 (2026-09-16T23:46Z) and
35287712594 (2026-09-17T23:38Z) all captured US evidence successfully
(alpaca_status READY, status PASS) and then lost it at the
"Commit immutable evidence and latest pointer" step with

    ! [rejected]          main -> main (fetch first)

because the step ended in a bare `git push`. Three US sessions were dropped,
which is why regime/us_paper_runtime_publication.py was still reading a
2026-09-15 packet on 2026-09-18.

These tests fix, offline and with no network call:

  * the commit step publishes through the shared bounded-retry helper and no
    longer ends in a bare `git push`;
  * the helper really does recover from a rejected push -- exercised against
    two real local clones racing on one bare origin, not a mock;
  * a push that keeps failing still fails the run, and a rebase conflict
    publishes nothing (fail-closed);
  * the ratified fingerprint record matches the workflow's new bytes, and the
    sha256-pinned registry file was NOT edited to achieve that.

Collection targets, sources, the committed paths and the cron are asserted
unchanged.
"""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/free-market-data.yml"
SCRIPT = ROOT / ".github/scripts/push_to_default_branch.sh"
AMENDMENT = ROOT / "config/free_market_data_source_owner_amendment_v1.json"
REGISTRY = ROOT / "config/regime_source_owner_registry_v2.json"

COMMIT_STEP = "Commit immutable evidence and latest pointer"

# Exactly the paths the step committed before this change.
EXPECTED_ADD_PATHS = [
    "evidence/free_market_data/derived",
    "evidence/free_market_data/raw",
    "evidence/free_market_data/fred/raw",
    "data/latest_free_market_data.json",
    "evidence/us_symbol_market_review",
    "data/latest_us_symbol_market_review.json",
]


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def workflow_steps() -> list:
    spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return spec["jobs"]["capture"]["steps"]


def commit_step() -> dict:
    for step in workflow_steps():
        if step.get("name") == COMMIT_STEP:
            return step
    raise AssertionError(f"{COMMIT_STEP!r} step is gone")


# The runner has no global git identity and we deliberately ignore whatever
# the developer's machine has, so every git invocation -- including the rebase
# the helper runs internally -- must carry an identity in the environment.
# Without this the helper's rebase dies with "empty ident name" on CI and the
# conflict branch is taken for the wrong reason.
def git_env(extra=None) -> dict:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "atlas-test",
        "GIT_AUTHOR_EMAIL": "atlas-test@example.invalid",
        "GIT_COMMITTER_NAME": "atlas-test",
        "GIT_COMMITTER_EMAIL": "atlas-test@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    })
    if extra:
        env.update(extra)
    return env


def git(*args, cwd, check=True, env=None):
    return subprocess.run(
        ("git",) + args, cwd=str(cwd), env=git_env(env),
        capture_output=True, text=True, check=check,
    )


def run_helper(cwd, branch="main", attempts="5"):
    return subprocess.run(
        ["bash", str(SCRIPT), branch, attempts],
        cwd=str(cwd), env=git_env(), capture_output=True, text=True,
    )


class PushRetryWiringTests(unittest.TestCase):
    """The workflow must call the bounded helper, not a bare push."""

    def test_commit_step_no_longer_ends_in_a_bare_push(self):
        run = commit_step()["run"]
        bare = [line.strip() for line in run.splitlines()
                if line.strip() in ("git push", "git push origin main",
                                    "git push origin HEAD:main")]
        self.assertEqual(bare, [], f"bare push survives in {COMMIT_STEP!r}: {bare}")

    def test_commit_step_publishes_through_the_shared_helper(self):
        run = commit_step()["run"]
        self.assertIn("push_to_default_branch.sh", run)
        self.assertTrue(SCRIPT.is_file(), "shared helper is missing")
        # Resolved from the event payload, exactly as daily-briefing.yml does,
        # so no branch name is hardcoded here.
        self.assertEqual(
            commit_step()["env"]["DEFAULT_BRANCH"],
            "${{ github.event.repository.default_branch }}",
        )
        self.assertIn('push_to_default_branch.sh "$DEFAULT_BRANCH"', run)

    def test_helper_is_bounded_and_not_a_fifth_inlined_variant(self):
        body = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("MAX_ATTEMPTS", body)
        self.assertIn("set -euo pipefail", body)
        # The retry loop must not be re-implemented inside this workflow.
        run = commit_step()["run"]
        for token in ("for attempt in", "max_attempts", "until git push"):
            self.assertNotIn(token, run, f"inlined retry token {token!r} in the workflow")

    def test_commit_step_still_commits_exactly_the_same_paths(self):
        run = commit_step()["run"]
        add = [line.strip() for line in run.splitlines()
               if line.strip().startswith("git add ")]
        self.assertEqual(len(add), 1)
        self.assertEqual(add[0].split()[2:], EXPECTED_ADD_PATHS)
        self.assertIn("if git diff --staged --quiet; then exit 0; fi", run)

    def test_collection_targets_sources_and_cron_are_unchanged(self):
        spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        triggers = spec.get("on", spec.get(True))
        self.assertEqual(triggers["schedule"], [{"cron": "35 21 * * 0-5"}])
        runs = "\n".join(step.get("run", "") for step in workflow_steps())
        self.assertIn("python3 collectors/free_market_data.py", runs)
        self.assertIn("python3 decision/us_symbol_market_review.py", runs)
        # No new network reach, and no new secret.
        self.assertNotIn("curl", runs)
        self.assertNotIn("wget", runs)
        capture = [s for s in workflow_steps() if "secrets." in json.dumps(s.get("env", {}))]
        self.assertEqual(
            [s["name"] for s in capture],
            ["Capture FRED risk/liquidity and Alpaca IEX evidence"],
        )


class HelperBehaviourTests(unittest.TestCase):
    """Exercise the real helper against real repositories -- no network."""

    def _origin_and_clone(self, tmp: Path):
        origin = tmp / "origin.git"
        git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp)
        work = tmp / "work"
        git("clone", str(origin), str(work), cwd=tmp)
        (work / "seed.txt").write_text("seed\n", encoding="utf-8")
        git("add", "seed.txt", cwd=work)
        git("commit", "-m", "seed", cwd=work)
        git("push", "origin", "HEAD:main", cwd=work)
        return origin, work

    def test_a_rejected_push_is_retried_and_the_commit_survives(self):
        """This is the 2026-09-14/16/17 failure, reproduced and then fixed."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            origin, collector = self._origin_and_clone(tmp)

            # Another collector lands on main first -- the race.
            other = tmp / "other"
            git("clone", str(origin), str(other), cwd=tmp)
            (other / "other.txt").write_text("other collector\n", encoding="utf-8")
            git("add", "other.txt", cwd=other)
            git("commit", "-m", "other collector evidence", cwd=other)
            git("push", "origin", "HEAD:main", cwd=other)

            # Our run builds its evidence commit against the now-stale main.
            (collector / "latest_free_market_data.json").write_text("{}\n", encoding="utf-8")
            git("add", "latest_free_market_data.json", cwd=collector)
            git("commit", "-m", "data: free market evidence", cwd=collector)

            # A bare push is what used to happen: rejected, evidence lost.
            bare = git("push", "origin", "HEAD:main", cwd=collector, check=False)
            self.assertNotEqual(bare.returncode, 0)
            self.assertIn("rejected", bare.stderr)

            # The helper recovers it.
            done = run_helper(collector, attempts="5")
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("rebasing", done.stderr)

            # Both commits are on main: ours was replayed, theirs was kept.
            log = git("log", "--format=%s", "main", cwd=collector).stdout
            self.assertIn("data: free market evidence", log)
            self.assertIn("other collector evidence", log)
            listed = git("ls-tree", "--name-only", "main", cwd=origin).stdout.split()
            self.assertIn("latest_free_market_data.json", listed)
            self.assertIn("other.txt", listed)

    def test_a_persistent_push_failure_still_fails_the_run(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            origin, collector = self._origin_and_clone(tmp)

            # Reject every push, forever.
            hook = origin / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\necho 'rejected by test' >&2\nexit 1\n",
                            encoding="utf-8")
            hook.chmod(0o755)

            (collector / "latest_free_market_data.json").write_text("{}\n", encoding="utf-8")
            git("add", "latest_free_market_data.json", cwd=collector)
            git("commit", "-m", "data: free market evidence", cwd=collector)

            done = run_helper(collector, attempts="2")
            self.assertNotEqual(done.returncode, 0,
                                "a dropped commit was reported as success")
            self.assertIn("STOP", done.stderr)
            # Nothing was published.
            listed = git("ls-tree", "--name-only", "main", cwd=origin).stdout.split()
            self.assertNotIn("latest_free_market_data.json", listed)

    def test_the_retry_is_bounded(self):
        """The loop must stop, not spin: attempts are capped by the argument."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            origin, collector = self._origin_and_clone(tmp)
            hook = origin / "hooks" / "pre-receive"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            (collector / "x.txt").write_text("x\n", encoding="utf-8")
            git("add", "x.txt", cwd=collector)
            git("commit", "-m", "bounded", cwd=collector)

            done = run_helper(collector, attempts="3")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("after 3 attempts", done.stderr)

    def test_a_rebase_conflict_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            origin, collector = self._origin_and_clone(tmp)

            other = tmp / "other"
            git("clone", str(origin), str(other), cwd=tmp)
            (other / "shared.txt").write_text("theirs\n", encoding="utf-8")
            git("add", "shared.txt", cwd=other)
            git("commit", "-m", "theirs", cwd=other)
            git("push", "origin", "HEAD:main", cwd=other)

            (collector / "shared.txt").write_text("ours\n", encoding="utf-8")
            git("add", "shared.txt", cwd=collector)
            git("commit", "-m", "ours", cwd=collector)

            done = run_helper(collector, attempts="5")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("nothing was published", done.stderr)
            # The rebase was aborted, not left half-applied.
            self.assertEqual(
                git("ls-tree", "-r", "--name-only", "main", cwd=origin).stdout.split(),
                ["seed.txt", "shared.txt"],
            )
            self.assertEqual(
                git("show", "main:shared.txt", cwd=origin).stdout, "theirs\n")


class RatifiedFingerprintTests(unittest.TestCase):
    """The ratified fingerprint record must track the workflow's real bytes."""

    def setUp(self):
        self.amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))

    def test_fingerprint_matches_the_new_workflow_bytes(self):
        self.assertEqual(
            self.amendment["workflow_sha256"],
            sha256(WORKFLOW.read_bytes()),
        )
        self.assertEqual(
            self.amendment["workflow_path"],
            ".github/workflows/free-market-data.yml",
        )

    def test_superseded_fingerprint_is_the_value_the_registry_still_pins(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        owner = registry["markets"]["US"]["source_owner"]
        self.assertEqual(owner["workflow_path"], self.amendment["workflow_path"])
        self.assertEqual(owner["workflow_sha256"],
                         self.amendment["superseded_workflow_sha256"])
        self.assertNotEqual(self.amendment["workflow_sha256"],
                            self.amendment["superseded_workflow_sha256"])

    def test_the_sha_pinned_registry_file_was_not_edited(self):
        """The whole reason this is an overlay: the registry's bytes are pinned
        by two live runtimes that fail closed on POLICY_BINDING_DRIFT."""
        binding = self.amendment["registry_binding"]
        self.assertEqual(binding["mode"], "ADDITIVE_OVERLAY_REGISTRY_BYTES_UNCHANGED")
        self.assertEqual(binding["sha256"], sha256(REGISTRY.read_bytes()))
        for pinner in ("config/crypto_paper_runtime_v1.json",
                       "config/kr_paper_runtime_ratification_candidate_v1.json"):
            self.assertIn(binding["sha256"],
                          (ROOT / pinner).read_text(encoding="utf-8"),
                          f"{pinner} no longer pins the registry bytes we recorded")

    def test_ratification_record_is_bound_by_hash(self):
        rat = self.amendment["ratification"]
        record_path = ROOT / rat["path"]
        self.assertTrue(record_path.is_file())
        self.assertEqual(rat["sha256"], sha256(record_path.read_bytes()))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["record_type"], "USER_RATIFICATION")
        self.assertEqual(record["verbatim"], rat["verbatim"])
        # Nothing here grants any trading authority.
        for flag, value in record["authority"].items():
            self.assertFalse(value, flag)
        for flag, value in self.amendment["authority"].items():
            if flag != "publication_reliability_only":
                self.assertFalse(value, flag)

    def test_helper_bytes_are_pinned_by_the_amendment(self):
        self.assertEqual(
            self.amendment["change"]["shared_script_sha256"],
            sha256(SCRIPT.read_bytes()),
        )


if __name__ == "__main__":
    unittest.main()
