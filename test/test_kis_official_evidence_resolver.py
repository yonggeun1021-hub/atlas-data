#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.kis_official_evidence_resolver import (
    KisOfficialEvidenceResolutionError,
    _resolve_git_evidence,
    main,
)


class ExactGitEvidenceResolverTests(unittest.TestCase):
    def _repo(self) -> tuple[tempfile.TemporaryDirectory, Path, str, dict]:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@atlas.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Atlas Test"], check=True)
        (repo / "docs").mkdir()
        contents = {"source.txt": b"official source bytes\n", "docs/fields.txt": b"pdno,hldg_qty\n"}
        for relative, data in contents.items():
            path = repo / relative
            path.write_bytes(data)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "fixture"], check=True)
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach", commit], check=True)
        manifest = {
            relative: hashlib.sha256(data).hexdigest()
            for relative, data in contents.items()
        }
        return temp, repo, commit, manifest

    def test_valid_exact_commit_bytes_are_reproduced(self) -> None:
        _, repo, commit, manifest = self._repo()
        result = _resolve_git_evidence(
            repo, repo="example/official", commit_sha=commit, manifest=manifest
        )
        self.assertEqual(result["resolutionStatus"], "EXACT_GIT_BYTES_REPRODUCED")
        self.assertEqual(result["commitSha"], commit)
        self.assertEqual({row["filePath"] for row in result["files"]}, set(manifest))
        self.assertTrue(all(
            value is False
            for key, value in result["authority"].items()
            if key != "review_only"
        ))

    def test_wrong_content_hash_is_rejected(self) -> None:
        _, repo, commit, manifest = self._repo()
        manifest["source.txt"] = "0" * 64
        with self.assertRaisesRegex(
            KisOfficialEvidenceResolutionError,
            "EVIDENCE_CONTENT_HASH_MISMATCH:source.txt",
        ):
            _resolve_git_evidence(
                repo, repo="example/official", commit_sha=commit, manifest=manifest
            )

    def test_missing_git_object_is_a_stable_retrieval_failure(self) -> None:
        _, repo, commit, manifest = self._repo()
        manifest["missing.txt"] = hashlib.sha256(b"missing").hexdigest()
        with self.assertRaisesRegex(
            KisOfficialEvidenceResolutionError,
            "EVIDENCE_GIT_OBJECT_READ_FAILED:missing.txt",
        ):
            _resolve_git_evidence(
                repo, repo="example/official", commit_sha=commit, manifest=manifest
            )

    def test_wrong_checkout_head_is_rejected_before_file_reads(self) -> None:
        _, repo, commit, manifest = self._repo()
        subprocess.run(["git", "-C", str(repo), "switch", "-q", "-c", "next"], check=True)
        (repo / "next.txt").write_text("next\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "next"], check=True)
        with self.assertRaisesRegex(
            KisOfficialEvidenceResolutionError,
            "EVIDENCE_CHECKOUT_HEAD_MISMATCH",
        ):
            _resolve_git_evidence(
                repo, repo="example/official", commit_sha=commit, manifest=manifest
            )

    def test_branch_at_exact_commit_is_not_an_exact_detached_checkout(self) -> None:
        _, repo, commit, manifest = self._repo()
        subprocess.run(["git", "-C", str(repo), "switch", "-q", "-c", "mutable-ref"], check=True)
        with self.assertRaisesRegex(
            KisOfficialEvidenceResolutionError,
            "EVIDENCE_CHECKOUT_HEAD_NOT_DETACHED",
        ):
            _resolve_git_evidence(
                repo, repo="example/official", commit_sha=commit, manifest=manifest
            )

    def test_cli_failure_is_sanitized_and_authority_false(self) -> None:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            exit_code = main(["relative"])
        output = json.loads(buffer.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(output["resolutionStatus"], "FAILED")
        self.assertFalse(output["authority"]["order_authorized"])
        self.assertFalse(output["authority"]["trading_authorized"])


class CliRealSubprocessInvocationTests(unittest.TestCase):
    """Regression for a real execution-path defect: calling main() in-
    process (as every other test in this file does) never exercises
    Python's own module-resolution for a script invoked directly --
    `python3 identity/kis_official_evidence_resolver.py` sets sys.path[0]
    to the script's own directory, not the repo root, so the module's
    `from identity.kis_provenance_proposal import ...` absolute import
    previously raised ModuleNotFoundError only under this exact real
    invocation, never under `python3 -m ...` or an in-process call. Both
    real subprocess invocation styles must behave identically."""

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True, timeout=30,
        )

    def test_module_invocation_and_direct_script_invocation_agree(self) -> None:
        module_result = self._run(
            [sys.executable, "-m", "identity.kis_official_evidence_resolver", "relative"]
        )
        script_result = self._run(
            [sys.executable, "identity/kis_official_evidence_resolver.py", "relative"]
        )
        self.assertNotIn("ModuleNotFoundError", script_result.stderr)
        self.assertEqual(module_result.returncode, script_result.returncode)
        self.assertEqual(
            json.loads(module_result.stdout)["resolutionStatus"],
            json.loads(script_result.stdout)["resolutionStatus"],
        )

    def test_direct_script_invocation_resolves_real_evidence_end_to_end(self) -> None:
        _, repo, commit, manifest = ExactGitEvidenceResolverTests._repo(self)
        # Reuse the resolver's own real manifest module shape by pointing
        # a genuinely detached checkout at the fixture repo -- this only
        # proves the CLI's import path works end to end under a real
        # subprocess; the manifest/commit pinned inside the module itself
        # (koreainvestment/open-trading-api) is exercised separately.
        result = self._run(
            [sys.executable, "identity/kis_official_evidence_resolver.py", str(repo)]
        )
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        payload = json.loads(result.stdout)
        # This fixture repo's commit will not match the module's own
        # pinned koreainvestment/open-trading-api commit, so resolution
        # itself fails closed -- the point here is solely that the CLI
        # reached real resolver code (a JSON resolutionStatus was
        # produced at all) rather than crashing on import.
        self.assertIn("resolutionStatus", payload)
        self.assertFalse(payload["authority"]["order_authorized"])


if __name__ == "__main__":
    unittest.main()
