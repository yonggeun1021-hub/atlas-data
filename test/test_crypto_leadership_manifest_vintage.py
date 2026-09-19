"""Hotfix regression: leadership lineage manifests live in the vintage folder.

``.github/scripts/crypto_leadership.py`` reads ``evidence/crypto/breadth/raw/
<vintage>/`` as ``as_of = vintage - 1 day`` and records that as-of date in
``lineage.manifest_sha256_by_date``.  The decision snapshot must therefore
verify each claimed sha against ``raw/<as_of + 1 day>/_manifest.json``.  Before
the fix it looked in ``raw/<as_of>/`` (the previous day's capture), which broke
main once the 2026-09-14 packet became the first to carry non-empty lineage.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "decision" / "crypto_paper_decision_snapshot.py"
SPEC = importlib.util.spec_from_file_location(
    "crypto_paper_decision_snapshot_manifest_vintage", MODULE_PATH
)
CPDS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CPDS)

LEADERSHIP = CPDS.PROMOTION.CRYPTO_LEADERSHIP
REAL_RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
REAL_PACKET_PATH = (
    ROOT / "data" / "observations" / "crypto_leadership" / "2026-09-14" / "packet.json"
)
Error = CPDS.CryptoPaperDecisionSnapshotError


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lineage_entry(as_of_date: str, claims: list[tuple[str, str]]) -> dict:
    return {
        "date": as_of_date,
        "record": {
            "schema_version": 2,
            "market": "CRYPTO",
            "as_of_date": as_of_date,
            "status": "PARTIAL",
            "lineage": {
                "manifest_sha256_by_date": [
                    {"as_of_date": day, "manifest_sha256": digest} for day, digest in claims
                ]
            },
        },
    }


class ConventionAgreementTests(unittest.TestCase):
    def test_helper_matches_leadership_discover_snapshot_map_on_committed_evidence(self):
        snapshots = LEADERSHIP.discover_snapshot_map(REAL_RAW_ROOT)
        self.assertGreater(len(snapshots), 0)
        for as_of, folder in snapshots.items():
            self.assertEqual(
                CPDS.leadership_manifest_vintage_folder(as_of.isoformat()), folder.name
            )

    def test_helper_matches_discover_snapshot_map_across_month_and_year_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            for name in ("2024-03-01", "2026-01-01", "2026-09-09"):
                (raw / name).mkdir()
            snapshots = LEADERSHIP.discover_snapshot_map(raw)
            self.assertEqual(
                {day.isoformat(): folder.name for day, folder in snapshots.items()},
                {"2024-02-29": "2024-03-01", "2025-12-31": "2026-01-01",
                 "2026-09-08": "2026-09-09"},
            )
            for as_of, folder in snapshots.items():
                self.assertEqual(
                    CPDS.leadership_manifest_vintage_folder(as_of.isoformat()), folder.name
                )


class CommittedEvidenceTests(unittest.TestCase):
    def test_real_2026_09_14_packet_lineage_verifies(self):
        record = json.loads(REAL_PACKET_PATH.read_text(encoding="utf-8"))
        claims = record["lineage"]["manifest_sha256_by_date"]
        self.assertIn(
            "2026-09-08", [item["as_of_date"] for item in claims]
        )
        # Previously raised LEADERSHIP_LINEAGE_MANIFEST_NOT_NATURAL:2026-09-08.
        CPDS._validate_leadership_entry({"date": "2026-09-14", "record": record})

    def test_real_as_of_2026_09_08_maps_to_vintage_2026_09_09(self):
        record = json.loads(REAL_PACKET_PATH.read_text(encoding="utf-8"))
        claimed = next(
            item["manifest_sha256"]
            for item in record["lineage"]["manifest_sha256_by_date"]
            if item["as_of_date"] == "2026-09-08"
        )
        vintage = sha256(REAL_RAW_ROOT / "2026-09-09" / "_manifest.json")
        same_name = sha256(REAL_RAW_ROOT / "2026-09-08" / "_manifest.json")
        self.assertEqual(claimed, vintage)
        self.assertNotEqual(claimed, same_name)

    def test_real_packet_with_claim_swapped_to_same_name_folder_is_not_natural(self):
        record = json.loads(REAL_PACKET_PATH.read_text(encoding="utf-8"))
        forged = copy.deepcopy(record)
        for item in forged["lineage"]["manifest_sha256_by_date"]:
            if item["as_of_date"] == "2026-09-08":
                item["manifest_sha256"] = sha256(REAL_RAW_ROOT / "2026-09-08" / "_manifest.json")
        with self.assertRaisesRegex(Error, "LEADERSHIP_LINEAGE_MANIFEST_NOT_NATURAL:2026-09-08"):
            CPDS._validate_leadership_entry({"date": "2026-09-14", "record": forged})


class SyntheticVintageFolderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.raw = self.root / "evidence" / "crypto" / "breadth" / "raw"
        self.same_name = self.raw / "2026-09-08" / "_manifest.json"
        self.vintage = self.raw / "2026-09-09" / "_manifest.json"
        for path, body in ((self.same_name, "capture-2026-09-08"), (self.vintage, "capture-2026-09-09")):
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"fixture": body}), encoding="utf-8")
        self.assertNotEqual(sha256(self.same_name), sha256(self.vintage))
        patches = (
            mock.patch.object(CPDS, "ROOT", self.root),
            mock.patch.object(CPDS, "CRYPTO_BREADTH_RAW_ROOT", self.raw),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def validate(self, claims):
        CPDS._validate_leadership_entry(lineage_entry("2026-09-14", claims))

    def test_vintage_folder_manifest_is_accepted(self):
        self.validate([("2026-09-08", sha256(self.vintage))])

    def test_same_name_folder_manifest_is_not_natural(self):
        with self.assertRaisesRegex(Error, "LEADERSHIP_LINEAGE_MANIFEST_NOT_NATURAL:2026-09-08"):
            self.validate([("2026-09-08", sha256(self.same_name))])

    def test_missing_vintage_folder_is_missing_even_if_same_name_exists(self):
        self.vintage.unlink()
        with self.assertRaisesRegex(Error, "LEADERSHIP_LINEAGE_MANIFEST_MISSING:2026-09-08"):
            self.validate([("2026-09-08", sha256(self.same_name))])

    def test_impossible_calendar_date_is_entry_invalid(self):
        with self.assertRaisesRegex(Error, "LEADERSHIP_LINEAGE_ENTRY_INVALID"):
            self.validate([("2026-02-30", sha256(self.vintage))])

    def test_symlinked_vintage_manifest_is_rejected(self):
        outside = self.root / "outside.json"
        outside.write_bytes(self.vintage.read_bytes())
        self.vintage.unlink()
        self.vintage.symlink_to(outside)
        with self.assertRaisesRegex(Error, "LEADERSHIP_LINEAGE_PATH_SYMLINK"):
            self.validate([("2026-09-08", sha256(outside))])

    def test_raw_root_outside_root_is_path_escape(self):
        with tempfile.TemporaryDirectory() as other:
            with mock.patch.object(CPDS, "CRYPTO_BREADTH_RAW_ROOT", Path(other)):
                with self.assertRaisesRegex(Error, "LEADERSHIP_LINEAGE_PATH_ESCAPE"):
                    self.validate([("2026-09-08", sha256(self.vintage))])


if __name__ == "__main__":
    unittest.main()
