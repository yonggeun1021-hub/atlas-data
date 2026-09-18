#!/usr/bin/env python3
"""Offline regression for collectors/verify_evidence_staged.py -- the guard
added after the 2026-09-17/18 FRED DEXKOUS FX evidence-loss investigation
(see .github/workflows/fred-dexkous-fx.yml, "Commit append-only evidence
(bounded push retry, evidence-loss guard)").

No network. Uses a real throwaway `git init` repo so the guard's own
`git diff --staged --name-only` call is exercised for real, exactly as it
runs in CI -- only the repository is temporary and local.

The core claim under test: a run that writes an observation on disk but
whose commit does not actually include it must be made to FAIL (exit 1),
not silently succeed. Before this guard existed, nothing checked this --
a git-add path gap, a losing race in a concurrent rebase, or any other
reason a written file failed to reach `git add`'s target would push (or
no-op) and the workflow would go green regardless.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_evidence_staged", ROOT / "collectors" / "verify_evidence_staged.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def _run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, text=True)


class _TempGitRepoCase(unittest.TestCase):
    """Sets up a real, empty, local git repo per test and chdir's into it
    for the duration (restored in tearDown) -- the guard shells out to
    `git diff --staged --name-only` in the current directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self._original_cwd = os.getcwd()
        _run("git", "init", "--quiet", str(self.repo))
        os.chdir(self.repo)
        _run("git", "config", "user.email", "test@example.invalid")
        _run("git", "config", "user.name", "test")
        # Establish an initial commit so `git diff --staged` behaves
        # normally (diffing against a real HEAD, matching the real
        # workflow, which always runs after `actions/checkout`).
        (self.repo / "README").write_text("seed\n")
        _run("git", "add", "README")
        _run("git", "commit", "--quiet", "-m", "seed")

    def tearDown(self):
        os.chdir(self._original_cwd)
        self._tmp.cleanup()

    def _write_summary(self, new_paths: list[str], field: str = "new_observation_paths") -> Path:
        summary_path = self.repo / "summary.json"
        summary_path.write_text(json.dumps({field: new_paths}))
        return summary_path


class EvidenceLossIsCaught(_TempGitRepoCase):
    def test_reported_path_not_staged_fails_the_run(self):
        """The exact silent-loss shape: the collector claims it wrote a new
        observation file, but that file was never `git add`-ed (e.g. it was
        written outside the tree the commit step stages, or a concurrent
        rebase dropped it). Before this guard, the workflow would proceed
        to `git commit`/`git push` and report success. It must now fail."""
        summary_path = self._write_summary(
            ["evidence/fred_dexkous_fx/observations/2099-01-01/deadbeef.captured.json"]
        )
        # Deliberately never create or `git add` the reported file.
        rc = M.main([str(summary_path)])
        self.assertEqual(rc, 1, "a run that lost reported evidence must go red")

    def test_reported_path_actually_staged_passes(self):
        """The healthy case: the file the collector says is new really is
        staged for this commit -- the guard must not block a good run."""
        rel_path = "evidence/fred_dexkous_fx/observations/2099-01-01/deadbeef.captured.json"
        full_path = self.repo / rel_path
        full_path.parent.mkdir(parents=True)
        full_path.write_text("{}")
        _run("git", "add", rel_path)

        summary_path = self._write_summary([rel_path])
        rc = M.main([str(summary_path)])
        self.assertEqual(rc, 0)

    def test_no_new_paths_is_a_no_op(self):
        """Matches every real fred-dexkous-fx.yml run inspected on
        2026-09-15/16/17: new_observation_paths is empty because FRED had
        nothing new to publish. The guard must not fail a run that
        genuinely wrote nothing new."""
        summary_path = self._write_summary([])
        rc = M.main([str(summary_path)])
        self.assertEqual(rc, 0)

    def test_missing_summary_file_fails_closed(self):
        rc = M.main([str(self.repo / "does-not-exist.json")])
        self.assertEqual(rc, 1)

    def test_custom_field_name_is_honored(self):
        """Not used by fred-dexkous-fx.yml today, but keeps the guard
        reusable for a collector whose summary shape differs (e.g. a future
        differently-shaped sibling), without hardcoding one field name."""
        rel_path = "evidence/fred_dexkous_fx/observations/2099-01-02/cafebabe.captured.json"
        summary_path = self._write_summary([rel_path], field="freshly_written_paths")
        # Not staged -- and not staged under the custom field must still be
        # caught when that field name is passed explicitly.
        rc = M.main([str(summary_path), "--field", "freshly_written_paths"])
        self.assertEqual(rc, 1)
        # Same summary, default field name only -- nothing to check, passes.
        rc = M.main([str(summary_path)])
        self.assertEqual(rc, 0)


class DirRecordShapeIsCaught(_TempGitRepoCase):
    """population-symbol-observation-daily.yml's shape: a list of {output_dir,
    wrote_anything} records rather than a flat list of exact paths (see
    module docstring, "A second, optional shape")."""

    def _write_dir_summary(self, observations: list[dict]) -> Path:
        summary_path = self.repo / "summary.json"
        summary_path.write_text(json.dumps({"observations": observations}))
        return summary_path

    def test_written_directory_missing_expected_files_fails(self):
        summary_path = self._write_dir_summary(
            [{"market": "KR", "output_dir": "data/observations/korea_population_symbol_observation/2099-01-01",
              "wrote_anything": True}]
        )
        # Neither summary.json nor packet.json.gz was ever created/staged.
        rc = M.main([
            str(summary_path), "--written-dirs-field", "observations",
            "--dir-key", "output_dir", "--wrote-key", "wrote_anything",
            "--expect-file", "summary.json", "--expect-file", "packet.json.gz",
        ])
        self.assertEqual(rc, 1)

    def test_written_directory_with_both_files_staged_passes(self):
        rel_dir = "data/observations/korea_population_symbol_observation/2099-01-01"
        full_dir = self.repo / rel_dir
        full_dir.mkdir(parents=True)
        (full_dir / "packet.json.gz").write_bytes(b"\x1f\x8b")
        (full_dir / "summary.json").write_text("{}")
        _run("git", "add", rel_dir)

        summary_path = self._write_dir_summary(
            [{"market": "KR", "output_dir": rel_dir, "wrote_anything": True}]
        )
        rc = M.main([
            str(summary_path), "--written-dirs-field", "observations",
            "--dir-key", "output_dir", "--wrote-key", "wrote_anything",
            "--expect-file", "summary.json", "--expect-file", "packet.json.gz",
        ])
        self.assertEqual(rc, 0)

    def test_verified_existing_record_is_not_checked(self):
        """A market that skipped a rebuild (verified_existing,
        wrote_anything=False) reports no new files -- the guard must not
        demand any, even though its output_dir is committed from a prior
        day's run."""
        summary_path = self._write_dir_summary(
            [{"market": "KR",
              "output_dir": "data/observations/korea_population_symbol_observation/2099-01-01",
              "wrote_anything": False}]
        )
        rc = M.main([
            str(summary_path), "--written-dirs-field", "observations",
            "--expect-file", "summary.json", "--expect-file", "packet.json.gz",
        ])
        self.assertEqual(rc, 0)

    def test_blocked_record_with_no_output_dir_is_skipped_not_crashed(self):
        summary_path = self._write_dir_summary(
            [{"market": "US", "session_date": None, "outcome": "blocked", "wrote_anything": False}]
        )
        rc = M.main([str(summary_path), "--written-dirs-field", "observations"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
