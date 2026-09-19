#!/usr/bin/env python3
"""Candidate evidence lifecycle receipt regression.

GENERATED_AT is a fixed past instant, so the receipt's rolling-pointer
sources (data/stage_history.json and the Dynamic Clock candidate validity /
identity observations, all rewritten by the daily collect chain) are pinned
for the whole module to the frozen snapshot in test/rolling_pointer_snapshot.py.
Against the live tree every newer collect date would otherwise make
GENERATED_AT precede the selected as_of date and fail closed.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery" / "candidate_evidence_lifecycle_receipt.py"
SPEC = importlib.util.spec_from_file_location("candidate_evidence_lifecycle_receipt", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
if str(ROOT / "test") not in sys.path:
    sys.path.insert(0, str(ROOT / "test"))
import rolling_pointer_snapshot as SNAPSHOT  # noqa: E402


GENERATED_AT = "2026-09-13T05:30:00Z"
_PINNED = contextlib.ExitStack()


def setUpModule():
    snapshot = SNAPSHOT.materialize(Path(_PINNED.enter_context(tempfile.TemporaryDirectory())))
    _PINNED.enter_context(SNAPSHOT.pinned_candidate_receipt_sources(MODULE, snapshot))


def tearDownModule():
    _PINNED.close()


def _row(name: str, stage: str | None, coverage: bool = True) -> dict:
    return {"name": name, "stage": stage, "coverage": coverage, "collected": False}


def _gate_input(symbol: str, market: str, status_overrides: dict[str, str] | None = None) -> dict:
    policy = MODULE.load_stage_evaluation_policy()["policy"]
    overrides = status_overrides or {}
    evidence = {
        "ref": "evidence/test/market_native.json",
        "sha256": "a" * 64,
        "available_at_utc": "2026-09-13T05:27:28Z",
    }
    return {
        "contract_version": "candidate_stage_gate_input/1",
        "symbol": symbol,
        "market": market,
        "evaluation_at_utc": "2026-09-13T05:29:00Z",
        "review_or_expiry_time_utc": "2026-09-14T05:29:00Z",
        "market_native_evidence_contract": f"{market.lower()}_candidate_evaluator/1",
        "reviewer_identity": "SYSTEM_EVALUATOR_TEST_FIXTURE",
        "gates": {
            name: {
                "status": overrides.get(name, "PASS"),
                "evidence_refs": [copy.deepcopy(evidence)],
            }
            for name in policy["required_gate_order"]
        },
    }


class CurrentEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.receipt = MODULE.build_receipt(generated_at_utc=GENERATED_AT)

    def test_receipt_reads_the_pinned_rolling_pointer_snapshot(self):
        pinned = {row["repo_path"]: row["sha256"] for row in SNAPSHOT.MANIFEST["files"]}
        lineage = self.receipt["source_lineage"]
        self.assertEqual(lineage["stage_history"]["file_sha256"], pinned[SNAPSHOT.STAGE_HISTORY_PATH])
        self.assertEqual(lineage["candidate_validity"]["file_sha256"], pinned[SNAPSHOT.VALIDITY_PATH])

    def test_generated_before_selected_as_of_date_fails_closed(self):
        with self.assertRaisesRegex(MODULE.CandidateEvidenceLifecycleError, "GENERATED_AT_PRECEDES_AS_OF_DATE"):
            MODULE.build_receipt(generated_at_utc="2026-09-10T05:30:00Z", as_of_date="2026-09-11")

    def test_current_receipt_validates_and_keeps_trading_authority_closed(self):
        self.assertEqual(MODULE.validate_receipt(self.receipt), self.receipt)
        self.assertEqual(self.receipt["contract_version"], MODULE.CONTRACT_VERSION)
        self.assertEqual(self.receipt["as_of_date"], "2026-09-11")
        self.assertEqual(self.receipt["previous_evaluation_date"], "2026-09-10")
        authority = self.receipt["authority"]
        self.assertTrue(authority["read_only"])
        self.assertTrue(authority["system_candidate_stage_evaluation"])
        self.assertTrue(authority["stage4_internal_paper_candidate_handoff"])
        self.assertTrue(authority["stage_promotion"])
        for key in ("candidate_generation", "candidate_ranking", "manual_stage_mutation", "stage_exclusion", "rotation", "buy", "action", "order", "production", "trading", "real_capital"):
            self.assertFalse(authority[key])

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
        for field in ("symbol_to_sector_binding", "rotation_ledger_link"):
            self.assertEqual(gaps[field]["root_cause"], MODULE.POLICY_UNDEFINED)
        self.assertEqual(gaps["stage_promotion_hold_exclusion_rule"]["prior_root_cause"], MODULE.POLICY_UNDEFINED)
        self.assertEqual(gaps["stage_promotion_hold_exclusion_rule"]["current_status"], MODULE.CONNECTED)

        korea = MODULE.lookup_symbol(self.receipt, "298040")["sector_rotation"]
        self.assertEqual(korea["symbol_to_sector_binding"]["status"], MODULE.POLICY_UNDEFINED)
        self.assertEqual(
            korea["symbol_to_sector_binding"]["evidence"]["market_boundary"]["scope"],
            "SECTOR_SERIES_IDENTITY_TO_THEME_ID_ONLY",
        )
        self.assertEqual(korea["rotation_ledger_link"]["evidence"]["repository_default_policy"], "ABSENT")

    def test_ratified_policy_holds_current_rows_without_connected_gate_inputs(self):
        decision = self.receipt["policy_decision"]
        self.assertEqual(decision["status"], "RATIFIED_ACTIVE")
        self.assertEqual(decision["selected_option"], "A_SEPARATE_SYSTEM_EVALUATED_STAGE")
        self.assertEqual(decision["missing_data_policy"], "HOLD_OR_NOT_COMPUTABLE_NEVER_PASS")
        self.assertEqual(
            decision["manual_system_boundary"],
            "MANUAL_NOTION_TAG_RETAINED_AS_SOURCE_FACT_NEVER_REQUIRED_AS_SYSTEM_PROMOTION_INPUT",
        )
        self.assertFalse(decision["numeric_thresholds_added"])
        for row in self.receipt["lifecycle_records"]:
            evaluation = row["system_stage_evaluation"]
            self.assertEqual(evaluation["derived_transition"], "HOLD")
            self.assertEqual(
                evaluation["first_blocker"],
                "canonical_population_membership:GATE_INPUT_NOT_CONNECTED",
            )
            self.assertFalse(evaluation["manual_stage_used_as_promotion_input"])
            audit = row["gate_connection_audit"]
            self.assertEqual(audit["gate_count"], 9)
            self.assertEqual(
                [gate["gate"] for gate in audit["gates"]],
                list(MODULE.REQUIRED_STAGE_GATES),
            )
        handoff = self.receipt["downstream_handoff"]
        self.assertEqual(handoff["status"], "RATIFIED_STAGE_POLICY_ACTIVE_NO_SYSTEM_CANDIDATE")
        self.assertEqual(handoff["system_candidate_record_count"], 0)
        self.assertEqual(handoff["stage4_eligible_record_count"], 0)
        self.assertEqual(handoff["evidence_query_record_count"], len(self.receipt["lifecycle_records"]))


class RatifiedSystemStageTests(unittest.TestCase):
    def test_all_required_pass_promotes_but_does_not_bypass_stage4_contract(self):
        receipt = MODULE.build_receipt(
            generated_at_utc=GENERATED_AT,
            system_gate_inputs={"298040": _gate_input("298040.KS", "KOREA")},
        )
        MODULE.validate_receipt(
            receipt,
            system_gate_inputs={"298040": _gate_input("298040.KS", "KOREA")},
        )
        evaluation = next(row for row in receipt["lifecycle_records"] if row["symbol"] == "298040")["system_stage_evaluation"]
        self.assertEqual(evaluation["system_evaluated_stage"], "Candidate")
        self.assertEqual(evaluation["derived_transition"], "PROMOTE")
        self.assertTrue(evaluation["system_candidate_eligible"])
        self.assertFalse(evaluation["stage4_internal_paper_eligible"])
        self.assertEqual(receipt["downstream_handoff"]["system_candidate_symbols"], ["298040"])
        self.assertEqual(receipt["downstream_handoff"]["stage4_eligible_symbols"], [])
        self.assertEqual(
            receipt["downstream_handoff"]["status"],
            "SYSTEM_CANDIDATE_AVAILABLE_STAGE4_COMMON_FUNNEL_INPUT_REQUIRED",
        )
        self.assertFalse(receipt["authority"]["buy"])
        self.assertFalse(receipt["authority"]["trading"])

    def test_unknown_stale_fail_and_active_veto_each_hold_at_exact_first_blocker(self):
        for status in ("MISSING", "UNKNOWN", "STALE", "FAIL", "ACTIVE_VETO"):
            with self.subTest(status=status):
                gate_input = _gate_input("298040", "KOREA", {"translation_status": status})
                receipt = MODULE.build_receipt(
                    generated_at_utc=GENERATED_AT,
                    system_gate_inputs={"298040": gate_input},
                )
                evaluation = next(row for row in receipt["lifecycle_records"] if row["symbol"] == "298040")["system_stage_evaluation"]
                self.assertEqual(evaluation["derived_transition"], "HOLD")
                self.assertEqual(evaluation["first_blocker"], f"translation_status:{status}")
                self.assertFalse(evaluation["system_candidate_eligible"])
                self.assertFalse(evaluation["stage4_internal_paper_eligible"])

    def test_manual_stage_observation_does_not_change_system_decision(self):
        policy = MODULE.load_stage_evaluation_policy()
        gate_input = _gate_input("298040", "KOREA")
        first = MODULE._validate_gate_input(
            {"symbol": "298040", "market": "KOREA", "current_stage": None},
            gate_input,
            policy,
            dt.datetime.fromisoformat(GENERATED_AT.replace("Z", "+00:00")),
        )
        second = MODULE._validate_gate_input(
            {"symbol": "298040", "market": "KOREA", "current_stage": "Buy"},
            gate_input,
            policy,
            dt.datetime.fromisoformat(GENERATED_AT.replace("Z", "+00:00")),
        )
        for field in (
            "system_evaluated_stage",
            "derived_transition",
            "system_candidate_eligible",
            "stage4_internal_paper_eligible",
        ):
            self.assertEqual(first[field], second[field])
        self.assertNotEqual(first["manual_watchlist_stage_observation"], second["manual_watchlist_stage_observation"])

    def test_future_evidence_reference_is_rejected(self):
        gate_input = _gate_input("298040", "KOREA")
        gate_input["gates"]["freshness_status"]["evidence_refs"][0]["available_at_utc"] = "2026-09-13T05:29:01Z"
        with self.assertRaisesRegex(MODULE.CandidateEvidenceLifecycleError, "NOT_POINT_IN_TIME"):
            MODULE.build_receipt(
                generated_at_utc=GENERATED_AT,
                system_gate_inputs={"298040": gate_input},
            )

    def test_policy_is_not_applied_before_its_effective_time(self):
        receipt = MODULE.build_receipt(
            generated_at_utc="2026-09-12T05:30:00Z",
            as_of_date="2026-09-11",
        )
        self.assertEqual(receipt["policy_decision"]["status"], "RATIFIED_NOT_YET_EFFECTIVE")
        self.assertEqual(receipt["downstream_handoff"]["status"], "RATIFIED_POLICY_NOT_YET_EFFECTIVE")
        self.assertTrue(all(
            row["system_stage_evaluation"]["first_blocker"] == "POLICY_NOT_YET_EFFECTIVE"
            for row in receipt["lifecycle_records"]
        ))

    def test_rebound_policy_tamper_cannot_open_trading_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            policy_path = tmp_path / "policy.json"
            registry_path = tmp_path / "registry.json"
            approval_path = tmp_path / "approval.json"
            policy = json.loads(MODULE.STAGE_POLICY_PATH.read_text(encoding="utf-8"))
            policy["authority"]["trading"] = True
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            approval_path.write_bytes(MODULE.STAGE_POLICY_APPROVAL_PATH.read_bytes())
            registry = json.loads(MODULE.STAGE_POLICY_REGISTRY_PATH.read_text(encoding="utf-8"))
            registry["records"][0]["content_sha256"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
            registry["records"][0]["authority_evidence_sha256"] = hashlib.sha256(approval_path.read_bytes()).hexdigest()
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.CandidateEvidenceLifecycleError, "AUTHORITY_INVALID"):
                MODULE.load_stage_evaluation_policy(
                    policy_path=policy_path,
                    registry_path=registry_path,
                    approval_path=approval_path,
                )


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
