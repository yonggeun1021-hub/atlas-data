#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance import capital_rotation_e2e as acceptance


class CapitalRotationAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.morning_path = "evidence/scheduled_briefing_retrieval/2026-08-26/morning/rev-001.json"
        cls.evening_path = "evidence/scheduled_briefing_retrieval/2026-08-26/evening/rev-001.json"
        cls.morning_envelope = json.loads((ROOT / cls.morning_path).read_text())
        cls.evening_envelope = json.loads((ROOT / cls.evening_path).read_text())

    def receipt(self, slot: str, run_id: int, event_name="schedule", schedule=None):
        envelope = self.morning_envelope if slot == "morning" else self.evening_envelope
        path = self.morning_path if slot == "morning" else self.evening_path
        schedule = acceptance.SCHEDULES[slot] if schedule is None else schedule
        return acceptance.build_run_receipt(
            ROOT,
            event_name=event_name,
            event_schedule=schedule,
            run_id=run_id,
            run_attempt=1,
            workflow_head_sha="1" * 40,
            decision_date="2026-08-26",
            slot=slot,
            source_commit=envelope["source_commit"],
            authority_path=path,
        )

    @staticmethod
    def write_run(root: Path, receipt: dict):
        path = acceptance._run_path(root, receipt)
        acceptance._write_append_only(path, receipt)
        return path

    @staticmethod
    def write_json(path: Path, value: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    def test_real_retrieval_envelope_builds_natural_receipt(self):
        receipt = self.receipt("evening", 101)
        validated = acceptance.validate_run_receipt(ROOT, receipt)
        self.assertEqual(validated["sample_qualification"], "NATURAL_SCHEDULED_RUN")
        self.assertEqual(validated["source_commit"], self.evening_envelope["source_commit"])
        self.assertEqual(validated["generation_id"], self.evening_envelope["generation_id"])

    def test_manual_dispatch_is_visible_but_excluded(self):
        receipt = self.receipt("evening", 102, "workflow_dispatch", "")
        self.assertEqual(receipt["sample_qualification"], "MANUAL_DIAGNOSTIC_EXCLUDED")
        acceptance.validate_run_receipt(ROOT, receipt)

    def test_unknown_schedule_never_becomes_natural(self):
        receipt = self.receipt("evening", 103, "schedule", "0 0 * * *")
        self.assertEqual(receipt["sample_qualification"], "NATURAL_PROVENANCE_NOT_COMPUTABLE")

    def test_qualification_tamper_rehashed_is_rejected(self):
        receipt = self.receipt("evening", 104, "workflow_dispatch", "")
        receipt["sample_qualification"] = "NATURAL_SCHEDULED_RUN"
        receipt["receipt_sha256"] = acceptance.payload_sha256(receipt, "receipt_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "QUALIFICATION_TAMPERED"):
            acceptance.validate_run_receipt(ROOT, receipt)

    def test_source_commit_tamper_rehashed_is_rejected(self):
        receipt = self.receipt("evening", 105)
        receipt["source_commit"] = "2" * 40
        receipt["receipt_sha256"] = acceptance.payload_sha256(receipt, "receipt_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "SOURCE_PINNED_VALIDATOR_UNAVAILABLE"):
            acceptance.validate_run_receipt(ROOT, receipt)

    def test_authority_bytes_tamper_is_rejected(self):
        receipt = self.receipt("evening", 106)
        receipt["retrieval_authority"]["sha256"] = "3" * 64
        receipt["receipt_sha256"] = acceptance.payload_sha256(receipt, "receipt_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "AUTHORITY_BYTES_MISMATCH"):
            acceptance.validate_run_receipt(ROOT, receipt)

    def test_append_only_same_bytes_noop_different_bytes_conflict(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / "receipt.json"
            value = self.receipt("evening", 107)
            self.assertTrue(acceptance._write_append_only(target, value))
            self.assertFalse(acceptance._write_append_only(target, value))
            changed = copy.deepcopy(value)
            changed["github"]["run_attempt"] = 2
            changed["receipt_sha256"] = acceptance.payload_sha256(changed, "receipt_sha256")
            with self.assertRaisesRegex(acceptance.AcceptanceError, "APPEND_ONLY_CONFLICT"):
                acceptance._write_append_only(target, changed)

    def test_one_real_natural_pair_counts_once_but_gate_stays_not_ready(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root, portal_root, fail_root = base / "runs", base / "portal", base / "fail"
            self.write_run(run_root, self.receipt("morning", 201))
            self.write_run(run_root, self.receipt("evening", 202))
            result = acceptance.build_inventory(ROOT, run_root=run_root, portal_root=portal_root, fail_root=fail_root)
            self.assertEqual(result["observed"]["natural_pair_dates"], ["2026-08-26"])
            self.assertEqual(result["status"], "NOT_READY")

    def test_manual_receipt_never_completes_pair(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root = base / "runs"
            self.write_run(run_root, self.receipt("morning", 203))
            self.write_run(run_root, self.receipt("evening", 204, "workflow_dispatch", ""))
            result = acceptance.build_inventory(ROOT, run_root=run_root, portal_root=base / "portal", fail_root=base / "fail")
            self.assertEqual(result["observed"]["natural_pair_dates"], [])
            self.assertEqual(result["observed"]["manual_or_non_schedule_receipt_count"], 1)

    def test_duplicate_natural_slot_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root = base / "runs"
            self.write_run(run_root, self.receipt("morning", 205))
            self.write_run(run_root, self.receipt("morning", 206))
            with self.assertRaisesRegex(acceptance.AcceptanceError, "DUPLICATE_NATURAL_SLOT_DISTINCT_RUNS"):
                acceptance.build_inventory(ROOT, run_root=run_root, portal_root=base / "portal", fail_root=base / "fail")

    def test_same_run_rerun_attempt_is_counted_once(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root = base / "runs"
            first = self.receipt("morning", 210)
            second = copy.deepcopy(first)
            second["github"]["run_attempt"] = 2
            second["receipt_sha256"] = acceptance.payload_sha256(second, "receipt_sha256")
            self.write_run(run_root, first)
            self.write_run(run_root, second)
            result = acceptance.build_inventory(ROOT, run_root=run_root, portal_root=base / "portal", fail_root=base / "fail")
            self.assertEqual(result["observed"]["natural_run_receipt_count"], 2)
            self.assertEqual(result["observed"]["superseded_natural_rerun_attempt_count"], 1)
            self.assertEqual(result["observed"]["natural_pair_dates"], [])

    def test_self_hashed_portal_receipt_is_rejected_until_trusted_producer_exists(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            self.write_json(base / "portal" / "forged.json", {"receipt_sha256": "a" * 64})
            with self.assertRaisesRegex(acceptance.AcceptanceError, "UNTRUSTED_PORTAL_RECEIPT_PRESENT"):
                acceptance.build_inventory(
                    ROOT,
                    run_root=base / "runs",
                    portal_root=base / "portal",
                    fail_root=base / "fail",
                )

    def test_self_hashed_fail_closed_receipt_is_rejected_until_observer_exists(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            self.write_json(base / "fail" / "forged.json", {"receipt_sha256": "b" * 64})
            with self.assertRaisesRegex(acceptance.AcceptanceError, "UNTRUSTED_FAIL_CLOSED_RECEIPT_PRESENT"):
                acceptance.build_inventory(
                    ROOT,
                    run_root=base / "runs",
                    portal_root=base / "portal",
                    fail_root=base / "fail",
                )

    def test_inventory_recomputed_validation_rejects_rehash_tamper(self):
        current = acceptance.build_inventory(ROOT)
        changed = copy.deepcopy(current)
        changed["status"] = "PASS"
        changed["inventory_sha256"] = acceptance.payload_sha256(changed, "inventory_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "DRIFT_OR_TAMPER"):
            acceptance.validate_inventory(ROOT, changed)

    def test_all_money_and_trading_authority_remains_false(self):
        inventory = acceptance.build_inventory(ROOT)
        self.assertEqual(inventory["authority"], acceptance.AUTHORITY)
        self.assertTrue(inventory["authority"]["evidence_inventory_only"])
        self.assertFalse(any(value for key, value in inventory["authority"].items() if key != "evidence_inventory_only"))

    def test_workflow_binds_provenance_to_github_context_and_commits_receipt(self):
        text = (ROOT / ".github/workflows/daily-briefing.yml").read_text()
        self.assertIn("EVENT_NAME: ${{ github.event_name }}", text)
        self.assertIn("RUN_ID: ${{ github.run_id }}", text)
        self.assertIn("RUN_ATTEMPT: ${{ github.run_attempt }}", text)
        self.assertIn("WORKFLOW_HEAD_SHA: ${{ github.sha }}", text)
        self.assertNotIn("inputs.event_name", text)
        self.assertIn("publish-run-receipt", text)
        self.assertIn('git add "$RUN_RECEIPT_PATH"', text)
        self.assertIn('validate-inventory "$ACCEPTANCE_PATH"', text)

    def test_committed_inventory_is_exact_rebuild_and_not_ready(self):
        value = json.loads((ROOT / "evidence/operational/capital_rotation_e2e_acceptance.json").read_text())
        self.assertEqual(acceptance.validate_inventory(ROOT, value), value)
        self.assertEqual(value["status"], "NOT_READY")
        self.assertEqual(value["observed"]["natural_pair_dates"], [])


if __name__ == "__main__":
    unittest.main()
