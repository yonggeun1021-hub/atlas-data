#!/usr/bin/env python3
"""No raw KRX bytes in the public korea-market-signals.yml Actions artifact.

docs/krx_investable_registry_contract.md's "Distribution boundary" section:
KRX Open API terms restrict third-party provision/redistribution, so "KIS
archives and rows, KRX raw responses, symbol names and per-symbol status
fields... are private-only evidence." The uploaded actions/upload-artifact
bundle from this workflow is downloadable by any repository reader for its
retention window, so it must never carry the original per-symbol KOSPI/KOSDAQ
response bytes the pykrx candidate step captures under artifact/source-capture
for local hash-binding. This is a regression guard for that upload, and for
its K2 workflow_sha256 pin in config/regime_source_owner_registry_v2.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "korea-market-signals.yml"
REGISTRY = ROOT / "config" / "regime_source_owner_registry_v2.json"


class NoRawArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        with WORKFLOW.open(encoding="utf-8") as stream:
            cls.workflow = yaml.safe_load(stream)
        cls.steps = cls.workflow["jobs"]["collect-artifact"]["steps"]

    def step_names(self):
        return [step.get("name") for step in self.steps]

    def test_raw_responses_are_removed_before_the_upload_step(self):
        names = self.step_names()
        strip_index = names.index("Strip raw KRX response bytes before public artifact upload")
        upload_index = next(
            i for i, step in enumerate(self.steps) if step.get("uses", "").startswith("actions/upload-artifact")
        )
        build_index = names.index("Build KRX information-system PAPER reference candidate")
        self.assertLess(build_index, strip_index)
        self.assertLess(strip_index, upload_index)
        strip_run = self.steps[strip_index]["run"]
        self.assertIn("rm -rf artifact/source-capture/responses", strip_run)
        # manifest.json (hashes, byte lengths, capture times, status) is kept --
        # only the raw per-symbol response bytes are removed.
        self.assertIn("test -f artifact/source-capture/manifest.json", strip_run)
        self.assertNotIn("rm -rf artifact/source-capture\n", strip_run)
        self.assertNotIn("rm -rf artifact/source-capture ", strip_run)

    def test_upload_step_still_publishes_the_whole_pruned_artifact_directory(self):
        upload_step = next(
            step for step in self.steps if step.get("uses", "").startswith("actions/upload-artifact")
        )
        self.assertEqual(upload_step["with"]["path"], "artifact")
        self.assertEqual(upload_step["with"]["if-no-files-found"], "error")

    def test_workflow_never_uploads_a_source_capture_responses_path_directly(self):
        # Defends against a future edit that narrows the strip step instead of
        # widening it (e.g. only deleting some response files).
        self.assertNotIn("path: artifact/source-capture/responses", self.text)

    def test_k2_pin_matches_this_workflow_after_the_fix(self):
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        source_owner = registry["markets"]["KRX"]["source_owner"]
        self.assertEqual(source_owner["workflow_path"], ".github/workflows/korea-market-signals.yml")
        self.assertEqual(
            source_owner["workflow_sha256"],
            hashlib.sha256(WORKFLOW.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
