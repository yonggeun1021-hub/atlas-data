from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from paper_12_31_us_upstream_gate_lineage import receipt as module


class USUpstreamGateLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = module.build_receipt()

    def test_natural_sources_are_hash_verified_and_not_fixtures(self):
        observations = self.receipt["natural_observations"]
        market = observations["finished_session_candidate"]
        universe = observations["source_coverage_universe"]
        self.assertEqual(market["session_date"], "2026-08-31")
        self.assertEqual(market["reference_coverage"], "15/15")
        self.assertEqual(universe["source_coverage_count"], 13177)
        self.assertEqual(self.receipt["evidence_class"], "NATURAL_READ_ONLY_COMMITTED_SOURCE_OBSERVATIONS")
        self.assertNotIn("fixture", market["ref"].lower())
        self.assertNotIn("fixture", universe["ref"].lower())
        self.assertTrue(all(row["verified"] for row in self.receipt["binding_checks"]))

    def test_gate_one_does_not_promote_daily_reference_to_finished_session(self):
        gate = self.receipt["gates"][0]
        self.assertEqual((gate["gate"], gate["status"]), (1, "UNKNOWN"))
        self.assertIn("OFFICIAL_DATE_SPECIFIC_EXCHANGE_CALENDAR_EVIDENCE_ABSENT", gate["blockers"])
        self.assertIn("COMPLETED_15M_SERIES_ABSENT", gate["blockers"])
        self.assertIn("COMPLETED_1H_SERIES_ABSENT", gate["blockers"])

    def test_freshness_policy_is_not_invented(self):
        gate = self.receipt["gates"][1]
        self.assertEqual(gate["status"], "HOLD")
        self.assertIn("US_TTL_NOT_RATIFIED", gate["blockers"])
        for source in self.receipt["paper_12_6_input"]["sources"]:
            self.assertEqual(source["status"], "UNKNOWN")
            self.assertIsNone(source["ttlSeconds"])
            self.assertEqual(source["policy"]["approvalStatus"], "ABSENT")

    def test_regime_stays_unknown_hold_zero_of_five(self):
        nested = self.receipt["paper_12_6_receipt"]
        self.assertEqual(self.receipt["regime_coverage"], "0/5")
        self.assertEqual(nested["regimeOutput"]["coverage"]["ratio"], "0/5")
        self.assertEqual(nested["status"], "HOLD")
        self.assertEqual(nested["judgement"], "UNKNOWN")
        self.assertIsNone(nested["action"])
        self.assertTrue(all(axis["status"] == "UNDEFINED" for axis in nested["axisChecks"]))

    def test_rotation_and_source_coverage_do_not_create_candidate_entry_exit(self):
        gate = self.receipt["gates"][3]
        self.assertTrue(gate["connected_observation"]["rotation_reference_present"])
        self.assertEqual(gate["connected_observation"]["source_coverage_universe_count"], 13177)
        self.assertIn("INVESTABLE_UNIVERSE_POLICY_UNRATIFIED", gate["blockers"])
        for key in ("candidate_receipt", "entry_receipt", "exit_receipt"):
            self.assertEqual(self.receipt["downstream"][key], {"status": "NOT_ELIGIBLE", "action": None})

    def test_paper_12_4_and_12_1_exact_subtree_pins_are_preserved(self):
        nested = self.receipt["paper_12_6_receipt"]
        self.assertEqual(self.receipt["downstream"]["paper_12_4"], nested["consumerPins"]["paper_12_4"])
        self.assertEqual(self.receipt["downstream"]["paper_12_1"], nested["consumerPins"]["paper_12_1"])
        self.assertEqual(self.receipt["downstream"]["paper_12_4"]["json_pointer"], "/regimeOutput")
        self.assertEqual(
            self.receipt["downstream"]["paper_12_1"]["json_pointer"],
            "/paperDecisionBridgeProjection",
        )

    def test_local_policy_draft_is_explicitly_rejected_as_ratification_source(self):
        audited = {row["commit"]: row for row in self.receipt["audited_local_commits"]}
        self.assertEqual(
            audited["afbe457e09aa622f77bd8292c4552339feb79cbd"]["disposition"],
            "REJECTED_AS_RATIFICATION_SOURCE",
        )
        self.assertEqual(
            audited["cefdec832b66e441e680a6db40c38e674d4546b6"]["disposition"],
            "NOT_CONSUMED_NO_NATURAL_PAYLOAD",
        )

    def test_receipt_tamper_and_derivation_are_detected(self):
        changed = copy.deepcopy(self.receipt)
        changed["status"] = "PASS"
        with self.assertRaisesRegex(module.GateLineageError, "RECEIPT_SHA_MISMATCH"):
            module.validate_receipt(changed)
        report = json.loads(module.REPORT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(module.canonical_bytes(report), module.canonical_bytes(self.receipt))
        self.assertEqual(module.validate_receipt(report)["receipt_sha256"], self.receipt["receipt_sha256"])

    def test_all_authority_and_side_effect_boundaries_remain_closed(self):
        self.assertTrue(self.receipt["authority"]["observation_only"])
        for key, value in self.receipt["authority"].items():
            if key != "observation_only":
                self.assertFalse(value, key)
        self.assertTrue(all(value == 0 for value in self.receipt["side_effects"].values()))


if __name__ == "__main__":
    unittest.main()
