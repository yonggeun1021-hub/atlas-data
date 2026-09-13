#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery" / "candidate_evidence_lifecycle_receipt.py"
SPEC = importlib.util.spec_from_file_location("candidate_evidence_lifecycle_receipt", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


GENERATED_AT = "2026-09-13T05:30:00Z"


def _row(name: str, stage: str | None, coverage: bool = True) -> dict:
    return {"name": name, "stage": stage, "coverage": coverage, "collected": False}


class CurrentEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = MODULE.build_receipt(generated_at_utc=GENERATED_AT)

    def test_current_receipt_validates_and_keeps_all_authority_closed(self):
        self.assertEqual(MODULE.validate_receipt(self.receipt), self.receipt)
        self.assertEqual(self.receipt["contract_version"], MODULE.CONTRACT_VERSION)
        self.assertEqual(self.receipt["as_of_date"], "2026-09-11")
        self.assertEqual(self.receipt["previous_evaluation_date"], "2026-09-10")
        authority = self.receipt["authority"]
        self.assertTrue(authority["read_only"])
        self.assertTrue(all(value is False for key, value in authority.items() if key != "read_only"))

    def test_retained_reason_is_queryable_with_exact_source_and_normalized_symbol(self):
        row = MODULE.lookup_symbol(self.receipt, "298040.KS")
        reason = row["inclusion_reason"]
        self.assertEqual(reason["status"], "RETAINED_REVIEW_REQUIRED_EVIDENCE")
        self.assertIn("Power 슬롯 신규 Discovery", reason["reason"])
        self.assertEqual(reason["source"]["source_ref"], "collection://0d145a42-f565-43bc-97ec-cfb474d0f8ea")
        self.assertRegex(reason["source"]["file_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(reason["source"]["notion_row_url"].startswith("https://app.notion.com/"))
        self.assertEqual(
            reason["authority_semantics"],
            "HISTORICAL_REVIEW_EVIDENCE_ONLY_NOT_CANDIDATE_OR_STAGE_AUTHORITY",
        )

    def test_current_stage_delta_is_maintained_and_reason_rule_time_are_queryable(self):
        for row in self.receipt["lifecycle_records"]:
            with self.subTest(symbol=row["symbol"]):
                self.assertEqual(row["lifecycle_status"], MODULE.MAINTAINED)
                self.assertEqual(row["transition_reason"]["status"], "DERIVED_FROM_STAGE_HISTORY")
                self.assertEqual(row["classification_rule"]["rule_version"], "1")
                self.assertEqual(row["evidence_as_of"], {"value": "2026-09-11", "precision": "DATE_ONLY"})

    def test_ratified_temporal_rule_is_connected_without_becoming_stage_policy(self):
        row = MODULE.lookup_symbol(self.receipt, "012450")
        validity = row["candidate_validity"]
        self.assertEqual(validity["status"], "ASSESSED")
        self.assertEqual(validity["temporal_status"], "FRESH_TEMPORAL")
        self.assertEqual(validity["source"]["rule_id"], "P8-12-CANDIDATE-VALIDITY-WINDOW")
        self.assertEqual(validity["source"]["rule_version"], "1")
        self.assertEqual(
            validity["scope_boundary"],
            "TEMPORAL_FRESHNESS_ONLY_NEVER_STAGE_PROMOTION_OR_EXCLUSION",
        )

    def test_policy_gaps_stay_distinct_from_connected_implementation_gaps(self):
        gaps = {row["field"]: row for row in self.receipt["gap_classification"]}
        self.assertEqual(gaps["inclusion_reason"]["prior_root_cause"], "IMPLEMENTATION_NOT_CONNECTED")
        self.assertEqual(gaps["inclusion_reason"]["current_status"], MODULE.CONNECTED)
        self.assertEqual(
            gaps["candidate_validity_and_expiry"]["prior_root_cause"],
            "CONSUMER_MISSING_EXISTING_RATIFIED_RULE",
        )
        for field in (
            "symbol_to_sector_binding",
            "rotation_ledger_link",
            "stage_promotion_hold_exclusion_rule",
        ):
            self.assertEqual(gaps[field]["root_cause"], MODULE.POLICY_UNDEFINED)

        korea = MODULE.lookup_symbol(self.receipt, "298040")["sector_rotation"]
        self.assertEqual(korea["symbol_to_sector_binding"]["status"], MODULE.POLICY_UNDEFINED)
        self.assertEqual(
            korea["symbol_to_sector_binding"]["evidence"]["market_boundary"]["scope"],
            "SECTOR_SERIES_IDENTITY_TO_THEME_ID_ONLY",
        )
        self.assertEqual(korea["rotation_ledger_link"]["evidence"]["repository_default_policy"], "ABSENT")

    def test_policy_decision_packet_and_stage4_handoff_add_no_threshold_or_eligibility(self):
        request = self.receipt["policy_decision_request"]
        self.assertEqual(request["status"], "AWAITING_CIO_RATIFICATION")
        self.assertEqual(request["recommended_option"], "A_SEPARATE_SYSTEM_EVALUATED_STAGE")
        self.assertEqual(request["missing_data_policy"], "HOLD_OR_NOT_COMPUTABLE_NEVER_PASS")
        self.assertEqual(
            request["manual_system_boundary"],
            "MANUAL_NOTION_TAG_RETAINED_AS_SOURCE_FACT_NEVER_REQUIRED_AS_SYSTEM_PROMOTION_INPUT",
        )
        self.assertTrue(all(option["numeric_thresholds_added"] is False for option in request["options"]))
        handoff = self.receipt["downstream_handoff"]
        self.assertEqual(handoff["status"], "NOT_ADMISSIBLE_STAGE_POLICY_UNRATIFIED")
        self.assertEqual(handoff["stage4_eligible_record_count"], 0)
        self.assertEqual(handoff["evidence_query_record_count"], len(self.receipt["lifecycle_records"]))


class MechanicalDeltaTests(unittest.TestCase):
    def test_next_evaluation_classifies_new_maintained_promoted_demoted_and_dropped(self):
        history = {
            "2026-09-10": {
                "AAA": _row("A", "Discovery"),
                "BBB": _row("B", "Candidate"),
                "CCC": _row("C", "Ready"),
                "DDD": _row("D", "Discovery"),
            },
            "2026-09-11": {
                "AAA": _row("A", "Discovery"),
                "BBB": _row("B", "Ready"),
                "CCC": _row("C", "Candidate"),
                "EEE": _row("E", None),
            },
        }
        delta = MODULE.classify_stage_history(history)
        by_symbol = {row["symbol"]: row for row in delta["rows"]}
        self.assertEqual(by_symbol["AAA"]["lifecycle_status"], MODULE.MAINTAINED)
        self.assertEqual(by_symbol["BBB"]["lifecycle_status"], MODULE.PROMOTED)
        self.assertEqual(by_symbol["CCC"]["lifecycle_status"], MODULE.DEMOTED)
        self.assertEqual(by_symbol["DDD"]["lifecycle_status"], MODULE.DROPPED)
        self.assertEqual(by_symbol["EEE"]["lifecycle_status"], MODULE.NEW)

    def test_first_snapshot_is_new_and_never_claims_a_decision_reason(self):
        delta = MODULE.classify_stage_history(
            {"2026-09-10": {"AAA": _row("A", "Discovery")}}
        )
        row = delta["rows"][0]
        self.assertEqual(row["lifecycle_status"], MODULE.NEW)
        self.assertEqual(MODULE._transition_reason(row)["status"], MODULE.NO_EVIDENCE)


class FailClosedTests(unittest.TestCase):
    def test_watchlist_reason_is_not_backdated_before_capture(self):
        watchlist = MODULE.load_watchlist()
        evidence = MODULE._inclusion_evidence("298040", watchlist, "2026-08-13", ROOT)
        self.assertEqual(evidence["status"], MODULE.NOT_YET_AVAILABLE)
        self.assertIsNone(evidence["reason"])

    def test_validity_tamper_and_self_rehashed_authority_opening_are_rejected(self):
        original = json.loads(MODULE.VALIDITY_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "validity.json"
            tampered = copy.deepcopy(original)
            tampered["candidate_assessments"][0]["temporal_status"] = "FRESH_TEMPORAL"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.CandidateEvidenceLifecycleError, "HASH_MISMATCH"):
                MODULE.load_validity_assessment(path)

            authority_opened = copy.deepcopy(original)
            authority_opened["downstream_locks"]["stage"] = True
            unsigned = copy.deepcopy(authority_opened)
            unsigned.pop("assessment_sha256")
            authority_opened["assessment_sha256"] = MODULE.payload_sha256(unsigned)
            path.write_text(json.dumps(authority_opened), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.CandidateEvidenceLifecycleError, "SEMANTICS_INVALID"):
                MODULE.load_validity_assessment(path)

    def test_receipt_self_rehash_cannot_invent_symbol_sector_policy(self):
        receipt = MODULE.build_receipt(generated_at_utc=GENERATED_AT)
        tampered = copy.deepcopy(receipt)
        tampered["lifecycle_records"][0]["sector_rotation"]["symbol_to_sector_binding"]["status"] = "BOUND"
        tampered.pop("receipt_sha256")
        tampered["receipt_sha256"] = MODULE.payload_sha256(tampered)
        with self.assertRaisesRegex(MODULE.CandidateEvidenceLifecycleError, "POLICY_INVENTED"):
            MODULE.validate_receipt(tampered)

    def test_receipt_self_rehash_cannot_rewrite_retained_reason(self):
        receipt = MODULE.build_receipt(generated_at_utc=GENERATED_AT)
        tampered = copy.deepcopy(receipt)
        row = next(item for item in tampered["lifecycle_records"] if item["symbol"] == "298040")
        row["inclusion_reason"]["reason"] = "rewritten"
        tampered.pop("receipt_sha256")
        tampered["receipt_sha256"] = MODULE.payload_sha256(tampered)
        with self.assertRaisesRegex(MODULE.CandidateEvidenceLifecycleError, "REDERIVATION_MISMATCH"):
            MODULE.validate_receipt(tampered)


if __name__ == "__main__":
    unittest.main()
