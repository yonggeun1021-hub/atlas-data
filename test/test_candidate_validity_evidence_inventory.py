#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.candidate_validity_evidence_inventory import (
    DEFAULT_DYNAMIC_ROOT,
    DEFAULT_OBSERVATION_ROOT,
    CandidateValidityEvidenceInventoryError,
    build_inventory,
    validate_inventory,
)
from clock.review_candidate import AUTHORITY_ALL_FALSE
from replay.opportunity_trigger import canonical_json, payload_sha256


class CandidateValidityEvidenceInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = build_inventory()

    def test_real_inventory_separates_revalidatable_legacy_and_rejected(self):
        counts = self.inventory["evidence_status_counts"]
        self.assertEqual(
            self.inventory["artifact_count"],
            sum(counts.values()),
        )
        self.assertGreaterEqual(counts["REVALIDATABLE"], 5)
        self.assertGreaterEqual(counts["LEGACY_NON_REVALIDATABLE"], 1)
        self.assertGreaterEqual(counts["REJECTED_NOT_A_SAMPLE"], 1)

    def test_natural_and_manual_samples_are_not_mixed(self):
        counts = self.inventory["revalidatable_artifact_qualification_counts"]
        natural = self.inventory["natural_operational_sample"]
        manual = self.inventory["manual_operational_sample"]
        self.assertGreaterEqual(counts["NATURAL_OPERATIONAL_SAMPLE"], 2)
        self.assertGreaterEqual(counts["MANUAL_OPERATIONAL_SAMPLE"], 3)
        self.assertEqual(counts["LOCAL_REPRODUCTION_NOT_OPERATIONAL_SAMPLE"], 0)
        self.assertEqual(natural["artifact_count"], counts["NATURAL_OPERATIONAL_SAMPLE"])
        self.assertEqual(manual["artifact_count"], counts["MANUAL_OPERATIONAL_SAMPLE"])
        self.assertGreaterEqual(natural["distinct_evidence_sample_count"], 2)
        self.assertGreaterEqual(natural["distinct_observation_date_count"], 1)
        self.assertLessEqual(
            natural["distinct_observation_date_count"],
            natural["artifact_count"],
        )
        self.assertLessEqual(
            natural["distinct_evidence_sample_count"],
            natural["artifact_count"],
        )

    def test_no_minimum_sample_or_validity_policy_is_invented(self):
        boundary = self.inventory["policy_boundary"]
        self.assertIsNone(boundary["minimum_required_natural_samples"])
        self.assertEqual(boundary["minimum_sample_authority_status"], "UNRATIFIED_NOT_DEFINED")
        self.assertFalse(boundary["validity_window_selected"])
        self.assertFalse(boundary["candidate_freshness_evaluated"])
        self.assertFalse(boundary["risk_capacity_opened"])
        self.assertFalse(boundary["p8_13_entry_proposal_opened"])
        self.assertEqual(boundary["money_action"], "NONE")
        self.assertEqual(self.inventory["authority"], AUTHORITY_ALL_FALSE)

    def test_trigger_coverage_is_sample_presence_not_a_window_decision(self):
        coverage = {row["trigger_type"]: row for row in self.inventory["trigger_family_coverage"]}
        self.assertGreater(coverage["PRICE_CONFIRMATION"]["natural_samples_with_candidate"], 0)
        self.assertEqual(coverage["FUNDAMENTAL_REVISION"]["natural_samples_with_candidate"], 0)

    def test_validator_rebuilds_the_whole_inventory(self):
        self.assertEqual(validate_inventory(self.inventory), self.inventory)
        bad = copy.deepcopy(self.inventory)
        bad["natural_operational_sample"]["distinct_evidence_sample_count"] = 99
        bad["inventory_sha256"] = payload_sha256({key: value for key, value in bad.items() if key != "inventory_sha256"})
        with self.assertRaisesRegex(CandidateValidityEvidenceInventoryError, "SEMANTIC_TAMPER"):
            validate_inventory(bad)

    def test_revalidatable_source_tamper_is_fatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "dynamic_clock"
            observations = root / "candidate_validity_observations"
            for source in DEFAULT_DYNAMIC_ROOT.rglob("*"):
                if source.is_file() and ("candidate_validity_observations" in source.parts or "candidate_validity_source_reports" in source.parts):
                    target = root / source.relative_to(DEFAULT_DYNAMIC_ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            first = next((root / "candidate_validity_source_reports").glob("*.json"))
            first.write_bytes(first.read_bytes() + b" ")
            with self.assertRaises(CandidateValidityEvidenceInventoryError):
                build_inventory(observations, root)

    def test_observation_deletion_changes_population_and_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "dynamic_clock"
            observations = root / "candidate_validity_observations"
            for source in DEFAULT_DYNAMIC_ROOT.rglob("*"):
                if source.is_file() and ("candidate_validity_observations" in source.parts or "candidate_validity_source_reports" in source.parts):
                    target = root / source.relative_to(DEFAULT_DYNAMIC_ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
            before = build_inventory(observations, root)
            next(observations.glob("*/*.json")).unlink()
            after = build_inventory(observations, root)
            self.assertEqual(after["artifact_count"], before["artifact_count"] - 1)
            self.assertNotEqual(after["inventory_sha256"], before["inventory_sha256"])


if __name__ == "__main__":
    unittest.main()
