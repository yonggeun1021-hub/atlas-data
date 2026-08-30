#!/usr/bin/env python3
"""P3-12-GOV-05: identity/upbit_exact_release_binding_release.py.

Synthetic-fixture-only: this module never runs against the real
committed registry/taxonomy/freeze (no genuine code approval exists yet),
so every test here builds its own tempdir fixtures.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_release_test",
    ROOT / "identity" / "upbit_exact_release_binding_release.py",
)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


def _code_approval(**overrides):
    doc = {
        "schema_version": "upbit_exact_release_binding_code_approval/1",
        "approval_status": "RATIFIED",
        "ratified_by": "CIO_USER",
        "ratified_at_utc": "2026-08-30T13:00:00Z",
        "successor_candidate": {
            "path": "successor_candidate.json",
            "file_sha256": "a" * 64,
            "payload_sha256": "b" * 64,
        },
        "authority": {"order_authorized": False},
    }
    doc.update(overrides)
    return doc


class BuildReleaseProjectionTests(unittest.TestCase):
    def test_projection_preserves_existing_fields_and_adds_only_the_two_pointers(self):
        current_registry = {"mappings": {"KRW-BTC": "BTC"}, "approval_status": "RATIFIED"}
        current_taxonomy = {"records": [{"canonical_asset_id": "BTC"}], "approval_status": "RATIFIED"}
        current_freeze = {"released_paper_markets": ["KRW-BTC"]}
        code_approval = _code_approval()

        result = RELEASE.build_release_projection(
            code_approval=code_approval,
            code_approval_path="code_approval.json",
            code_approval_file_sha256="c" * 64,
            current_registry=current_registry,
            current_taxonomy=current_taxonomy,
            current_freeze=current_freeze,
        )

        self.assertEqual(result["registry"]["mappings"], {"KRW-BTC": "BTC"})
        self.assertEqual(result["registry"]["code_approval_evidence_ref"], "code_approval.json")
        self.assertEqual(result["registry"]["code_approval_evidence_sha256"], "c" * 64)
        self.assertEqual(result["taxonomy"]["records"], [{"canonical_asset_id": "BTC"}])
        self.assertEqual(result["taxonomy"]["code_approval_evidence_ref"], "code_approval.json")
        self.assertEqual(
            result["freeze"]["code_approval_resolution"],
            {
                "code_approval_evidence_ref": "code_approval.json",
                "code_approval_evidence_sha256": "c" * 64,
                "ratified_at_utc": "2026-08-30T13:00:00Z",
                "successor_candidate_path": "successor_candidate.json",
                "successor_candidate_file_sha256": "a" * 64,
                "successor_candidate_payload_sha256": "b" * 64,
            },
        )
        # original inputs never mutated in place
        self.assertNotIn("code_approval_evidence_ref", current_registry)

    def test_deterministic_same_input_twice_identical_output(self):
        args = dict(
            code_approval=_code_approval(),
            code_approval_path="code_approval.json",
            code_approval_file_sha256="c" * 64,
            current_registry={"mappings": {}}, current_taxonomy={"records": []}, current_freeze={},
        )
        first = RELEASE.build_release_projection(**args)
        second = RELEASE.build_release_projection(**args)
        self.assertEqual(first, second)

    def test_not_ratified_approval_raises(self):
        with self.assertRaises(RELEASE.ReleaseProjectionError):
            RELEASE.build_release_projection(
                code_approval=_code_approval(approval_status="PENDING"),
                code_approval_path="x.json", code_approval_file_sha256="c" * 64,
                current_registry={}, current_taxonomy={}, current_freeze={},
            )

    def test_missing_ratified_at_raises(self):
        approval = _code_approval()
        del approval["ratified_at_utc"]
        with self.assertRaises(RELEASE.ReleaseProjectionError):
            RELEASE.build_release_projection(
                code_approval=approval,
                code_approval_path="x.json", code_approval_file_sha256="c" * 64,
                current_registry={}, current_taxonomy={}, current_freeze={},
            )

    def test_missing_successor_pin_raises(self):
        approval = _code_approval()
        del approval["successor_candidate"]
        with self.assertRaises(RELEASE.ReleaseProjectionError):
            RELEASE.build_release_projection(
                code_approval=approval,
                code_approval_path="x.json", code_approval_file_sha256="c" * 64,
                current_registry={}, current_taxonomy={}, current_freeze={},
            )


class ValidateCommittedReleaseTests(unittest.TestCase):
    def _write(self, path: Path, obj) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, sort_keys=True), encoding="utf-8")
        return path

    def test_committed_files_matching_the_projection_validate_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code_approval_path = self._write(tmp / "code_approval.json", _code_approval())
            current_registry_path = self._write(tmp / "current_registry.json", {"mappings": {"KRW-BTC": "BTC"}})
            current_taxonomy_path = self._write(tmp / "current_taxonomy.json", {"records": []})
            current_freeze_path = self._write(tmp / "current_freeze.json", {})

            expected = RELEASE.build_release_projection(
                code_approval=json.loads(code_approval_path.read_text()),
                code_approval_path=str(code_approval_path),
                code_approval_file_sha256=RELEASE.file_sha256(code_approval_path),
                current_registry=json.loads(current_registry_path.read_text()),
                current_taxonomy=json.loads(current_taxonomy_path.read_text()),
                current_freeze=json.loads(current_freeze_path.read_text()),
            )
            committed_registry_path = self._write(tmp / "committed_registry.json", expected["registry"])
            committed_taxonomy_path = self._write(tmp / "committed_taxonomy.json", expected["taxonomy"])
            committed_freeze_path = self._write(tmp / "committed_freeze.json", expected["freeze"])

            RELEASE.validate_committed_release(
                code_approval_path=code_approval_path,
                current_registry_path=current_registry_path,
                current_taxonomy_path=current_taxonomy_path,
                current_freeze_path=current_freeze_path,
                committed_registry_path=committed_registry_path,
                committed_taxonomy_path=committed_taxonomy_path,
                committed_freeze_path=committed_freeze_path,
            )  # must not raise

    def test_committed_registry_diverging_from_projection_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code_approval_path = self._write(tmp / "code_approval.json", _code_approval())
            current_registry_path = self._write(tmp / "current_registry.json", {"mappings": {"KRW-BTC": "BTC"}})
            current_taxonomy_path = self._write(tmp / "current_taxonomy.json", {"records": []})
            current_freeze_path = self._write(tmp / "current_freeze.json", {})

            expected = RELEASE.build_release_projection(
                code_approval=json.loads(code_approval_path.read_text()),
                code_approval_path=str(code_approval_path),
                code_approval_file_sha256=RELEASE.file_sha256(code_approval_path),
                current_registry=json.loads(current_registry_path.read_text()),
                current_taxonomy=json.loads(current_taxonomy_path.read_text()),
                current_freeze=json.loads(current_freeze_path.read_text()),
            )
            tampered_registry = dict(expected["registry"])
            tampered_registry["mappings"] = {"KRW-BTC": "TAMPERED"}
            committed_registry_path = self._write(tmp / "committed_registry.json", tampered_registry)
            committed_taxonomy_path = self._write(tmp / "committed_taxonomy.json", expected["taxonomy"])
            committed_freeze_path = self._write(tmp / "committed_freeze.json", expected["freeze"])

            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.validate_committed_release(
                    code_approval_path=code_approval_path,
                    current_registry_path=current_registry_path,
                    current_taxonomy_path=current_taxonomy_path,
                    current_freeze_path=current_freeze_path,
                    committed_registry_path=committed_registry_path,
                    committed_taxonomy_path=committed_taxonomy_path,
                    committed_freeze_path=committed_freeze_path,
                )


if __name__ == "__main__":
    unittest.main()
