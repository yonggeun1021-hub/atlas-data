#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision.price_state_operational_evidence_inventory import (
    PriceStateOperationalEvidenceInventoryError,
    build_inventory,
    validate_inventory,
)
from clock.review_candidate import AUTHORITY_ALL_FALSE
from replay.opportunity_trigger import payload_sha256


class PriceStateOperationalEvidenceInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inventory = build_inventory()

    def test_real_retained_population_tracks_growing_natural_samples(self):
        natural = self.inventory["natural_distinct_sample_count"]
        manual = self.inventory["manual_distinct_sample_count"]
        self.assertGreaterEqual(natural, 2)
        self.assertGreaterEqual(manual, 3)
        self.assertEqual(self.inventory["distinct_sample_count"], natural + manual)

    def test_natural_sample_proves_real_price_state_linkage(self):
        self.assertTrue(self.inventory["natural_price_state_linked_candidate_observed"])
        natural_samples = [
            row for row in self.inventory["samples"]
            if row["sample_qualification"] == "NATURAL_OPERATIONAL_SAMPLE"
        ]
        self.assertTrue(natural_samples)
        self.assertTrue(any(
            sample["by_market"]["BTC"]["linkage_status_counts"].get("LINKED", 0) > 0
            for sample in natural_samples
        ))
        self.assertTrue(any(
            sample["by_market"]["KOREA"]["linkage_status_counts"].get("LINKED", 0) > 0
            for sample in natural_samples
        ))
        self.assertTrue(any(
            sample["by_market"]["CRYPTO"]["linkage_status_counts"].get(
                "NOT_LINKED_THIS_SLICE", 0
            ) > 0
            for sample in natural_samples
        ))
        for natural in natural_samples:
            linked = sum(
                market["linkage_status_counts"].get("LINKED", 0)
                for market in natural["by_market"].values()
            )
            self.assertEqual(natural["price_state_linked_candidate_count"], linked)
            for market in natural["by_market"].values():
                self.assertEqual(
                    sum(market["linkage_status_counts"].values()),
                    market["candidate_count"],
                )

    def test_reflection_remains_unknown_in_every_candidate(self):
        for sample in self.inventory["samples"]:
            for market in sample["by_market"].values():
                counts = market["reflection_status_counts"]
                self.assertTrue(set(counts).issubset({"UNKNOWN", "NOT_AVAILABLE"}))
                self.assertEqual(sum(counts.values()), market["candidate_count"])
                if market["candidate_count"] == 0:
                    self.assertEqual(counts, {})
                else:
                    self.assertTrue(counts)

    def test_provisional_threshold_never_opens_downstream(self):
        boundary = self.inventory["operational_boundary"]
        self.assertEqual(boundary["classification_thresholds_approval_status"], "PROVISIONAL")
        self.assertEqual(boundary["reflection_status_authority"], "ABSENT_STRUCTURALLY_UNKNOWN_ONLY")
        self.assertFalse(boundary["candidate_validity_evaluated"])
        self.assertFalse(boundary["risk_capacity_opened"])
        self.assertFalse(boundary["p8_13_entry_proposal_opened"])
        self.assertEqual(boundary["money_action"], "NONE")
        self.assertEqual(self.inventory["authority"], AUTHORITY_ALL_FALSE)

    def test_full_inventory_is_independently_rebuilt(self):
        self.assertEqual(validate_inventory(self.inventory), self.inventory)
        bad = copy.deepcopy(self.inventory)
        bad["natural_price_state_linked_candidate_observed"] = False
        bad["inventory_sha256"] = payload_sha256({key: value for key, value in bad.items() if key != "inventory_sha256"})
        with self.assertRaisesRegex(PriceStateOperationalEvidenceInventoryError, "SEMANTIC_TAMPER"):
            validate_inventory(bad)

    def test_no_outcome_or_executable_stop_fields(self):
        text = str(self.inventory).lower()
        for forbidden in ("forward_return", "mfe", "mae", "position_size", "planned_stop", "quantity", "order_intent"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
