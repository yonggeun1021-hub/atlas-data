#!/usr/bin/env python3
"""Append-only FRED VIX evidence and independent replay regressions."""

from __future__ import annotations

import copy
import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fred_vix_provenance", ROOT / "collectors" / "fred_vix_provenance.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
NOW = dt.datetime(2026, 8, 27, 1, 2, 3, tzinfo=dt.timezone.utc)


def raw(value: str = "15.50", date: str = "2026-08-26") -> bytes:
    return json.dumps({
        "output_type": 1,
        "observations": [
            {
                "date": "2026-08-25", "value": "16.00",
                "realtime_start": "2026-08-27", "realtime_end": "2026-08-27",
            },
            {
                "date": date, "value": value,
                "realtime_start": "2026-08-27", "realtime_end": "2026-08-27",
            },
        ],
    }, sort_keys=True).encode()


def authorities_false(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_authorized") and item is not False:
                return False
            if not authorities_false(item):
                return False
    elif isinstance(value, list):
        return all(authorities_false(item) for item in value)
    return True


class FredVixProvenanceTests(unittest.TestCase):
    def test_workflow_commits_the_advertised_fred_raw_evidence_tree(self):
        workflow = (
            ROOT / ".github" / "workflows" / "free-market-data.yml"
        ).read_text(encoding="utf-8")
        commit_line = next(
            line.strip()
            for line in workflow.splitlines()
            if line.strip().startswith("git add ")
        )
        self.assertIn("evidence/free_market_data/fred/raw", commit_line.split())

    def test_bundle_is_deterministic_and_authority_false(self):
        first = M.build_evidence_bundle(NOW, raw())
        second = M.build_evidence_bundle(NOW, raw())
        self.assertEqual(first, second)
        self.assertTrue(authorities_false(first["manifest"]))
        self.assertEqual(
            first["manifest"]["raw_retention"],
            "APPEND_ONLY_CONTENT_ADDRESSED",
        )

    def test_publish_and_independent_replay(self):
        bundle = M.build_evidence_bundle(NOW, raw())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_evidence_bundle(root, bundle)
            replay = M.validate_evidence(
                root, bundle["pointer"], decision_at="2026-08-27T01:02:04Z"
            )
        self.assertEqual(replay["observation"]["series_id"], "VIXCLS")
        self.assertEqual(replay["observation"]["observation_date"], "2026-08-26")
        self.assertEqual(replay["observation"]["value"], "15.50")

    def test_same_revision_is_noop_but_same_day_different_content_is_preserved(self):
        first = M.build_evidence_bundle(NOW, raw("15.50"))
        second = M.build_evidence_bundle(NOW, raw("17.25"))
        self.assertNotEqual(
            first["pointer"]["evidence_revision_id"],
            second["pointer"]["evidence_revision_id"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_evidence_bundle(root, first)
            M.publish_evidence_bundle(root, first)
            M.publish_evidence_bundle(root, second)
            self.assertTrue((root / first["pointer"]["raw_path"]).exists())
            self.assertTrue((root / second["pointer"]["raw_path"]).exists())
            self.assertEqual(len(list((root / "evidence/free_market_data/fred/raw/2026-08-27").glob("*/manifest.json"))), 2)

    def test_raw_tamper_is_rejected(self):
        bundle = M.build_evidence_bundle(NOW, raw())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_evidence_bundle(root, bundle)
            (root / bundle["pointer"]["raw_path"]).write_bytes(
                gzip.compress(raw("99.00"), mtime=0)
            )
            with self.assertRaisesRegex(M.FredVixEvidenceError, "RAW_FILE_BYTES_MISMATCH"):
                M.validate_evidence(root, bundle["pointer"])

    def test_manifest_tamper_even_with_new_file_hash_is_rejected(self):
        bundle = M.build_evidence_bundle(NOW, raw())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_evidence_bundle(root, bundle)
            manifest_path = root / bundle["pointer"]["manifest_path"]
            manifest = json.loads(manifest_path.read_text())
            manifest["observation"]["value"] = "1.00"
            manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
            manifest_path.write_bytes(manifest_bytes)
            pointer = copy.deepcopy(bundle["pointer"])
            pointer["manifest_file_sha256"] = M.sha256_bytes(manifest_bytes)
            with self.assertRaisesRegex(M.FredVixEvidenceError, "EVIDENCE_REDERIVATION_MISMATCH"):
                M.validate_evidence(root, pointer)

    def test_future_capture_is_rejected(self):
        bundle = M.build_evidence_bundle(NOW, raw())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_evidence_bundle(root, bundle)
            with self.assertRaisesRegex(M.FredVixEvidenceError, "EVIDENCE_FROM_FUTURE"):
                M.validate_evidence(
                    root, bundle["pointer"], decision_at="2026-08-27T01:02:02Z"
                )

    def test_observation_from_future_is_rejected(self):
        with self.assertRaisesRegex(M.FredVixEvidenceError, "FRED_OBSERVATION_FROM_FUTURE"):
            M.build_evidence_bundle(NOW, raw(date="2026-08-28"))

    def test_path_traversal_is_rejected(self):
        pointer = M.build_evidence_bundle(NOW, raw())["pointer"]
        pointer["raw_path"] = "evidence/free_market_data/fred/raw/../../secret"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(M.FredVixEvidenceError, "EVIDENCE_PATH_INVALID"):
                M.validate_evidence(Path(tmp), pointer)

    def test_response_rows_must_be_ascending(self):
        backwards = json.dumps({"observations": [
            {"date": "2026-08-26", "value": "15"},
            {"date": "2026-08-25", "value": "16"},
        ]}).encode()
        with self.assertRaisesRegex(M.FredVixEvidenceError, "FRED_OBSERVATIONS_NOT_ASCENDING"):
            M.build_evidence_bundle(NOW, backwards)

    def test_append_only_collision_fails_closed(self):
        bundle = M.build_evidence_bundle(NOW, raw())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_evidence_bundle(root, bundle)
            (root / bundle["pointer"]["manifest_path"]).write_bytes(b"tampered")
            with self.assertRaisesRegex(M.FredVixEvidenceError, "APPEND_ONLY_COLLISION"):
                M.publish_evidence_bundle(root, bundle)


if __name__ == "__main__":
    unittest.main()
