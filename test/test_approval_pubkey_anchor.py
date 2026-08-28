#!/usr/bin/env python3
"""Regression for the out-of-band CIO approval-key anchor check."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "check_approval_pubkey_anchor.py"
SPEC = importlib.util.spec_from_file_location("check_approval_pubkey_anchor", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ApprovalPublicKeyAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / "config").mkdir()
        self.key = bytes(range(32))
        self.key_path = self.repo / MODULE.bf.APPROVAL_PUBKEY_PATH
        self.key_path.write_text(self.key.hex() + "\n", encoding="utf-8")
        self.fingerprint = hashlib.sha256(self.key).hexdigest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_exact_anchor_is_accepted(self) -> None:
        with mock.patch.dict(
            os.environ,
            {MODULE.bf.APPROVAL_FINGERPRINT_ENV: self.fingerprint},
            clear=False,
        ):
            self.assertIsNone(MODULE.verify(self.repo))

    def test_missing_anchor_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(MODULE.bf.APPROVAL_FINGERPRINT_ENV, None)
            with self.assertRaises(MODULE.bf.FinalizationError) as ctx:
                MODULE.verify(self.repo)
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_ANCHOR_MISSING")

    def test_mismatched_anchor_fails_closed(self) -> None:
        with mock.patch.dict(
            os.environ,
            {MODULE.bf.APPROVAL_FINGERPRINT_ENV: "0" * 64},
            clear=False,
        ):
            with self.assertRaises(MODULE.bf.FinalizationError) as ctx:
                MODULE.verify(self.repo)
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_PUBKEY_UNTRUSTED")

    def test_malformed_public_key_fails_closed(self) -> None:
        self.key_path.write_text("not-hex\n", encoding="utf-8")
        with mock.patch.dict(
            os.environ,
            {MODULE.bf.APPROVAL_FINGERPRINT_ENV: self.fingerprint},
            clear=False,
        ):
            with self.assertRaises(MODULE.bf.FinalizationError) as ctx:
                MODULE.verify(self.repo)
        self.assertEqual(ctx.exception.code, "FINALIZATION_APPROVAL_PUBKEY_MALFORMED")

    def test_cli_failure_redacts_anchor_and_computed_fingerprint(self) -> None:
        anchor = "1" * 64
        env = {**os.environ, MODULE.bf.APPROVAL_FINGERPRINT_ENV: anchor}
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo-root", str(self.repo)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        output = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("FINALIZATION_APPROVAL_PUBKEY_UNTRUSTED", output)
        self.assertNotIn(anchor, output)
        self.assertNotIn(self.fingerprint, output)


if __name__ == "__main__":
    unittest.main()
