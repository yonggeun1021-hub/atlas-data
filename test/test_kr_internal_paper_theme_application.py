#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rotation import kr_internal_paper_theme_application as APP


class ContractAndAdmissionTests(unittest.TestCase):
    def test_contract_is_exact_bounded_profile(self):
        contract = APP.load_contract()
        self.assertEqual(
            contract["allowed_asset_ids"],
            ["KR:XKRX:000660", "KR:XKRX:005930"],
        )
        self.assertFalse(contract["authority"]["global_taxonomy_authority_changed"])
        self.assertFalse(contract["authority"]["real_authority"])
        self.assertFalse(contract["authority"]["trading_authorized"])

    def test_approval_evidence_binds_complete_determining_payload(self):
        registry = json.loads(APP.REGISTRY_PATH.read_text(encoding="utf-8"))
        record = APP._validate_registry_document(registry, APP.load_contract())
        evidence_path = ROOT / record["approval_evidence_ref"]
        evidence_raw = evidence_path.read_bytes()
        evidence = json.loads(evidence_raw.decode("utf-8"))
        self.assertEqual(hashlib.sha256(evidence_raw).hexdigest(), record["approval_evidence_sha256"])
        self.assertEqual(evidence["determining_payload"], APP.determining_payload(record))
        self.assertEqual(
            evidence["approved_full_payload_sha256"],
            APP.payload_sha256(APP.determining_payload(record)),
        )

    def test_scope_or_authority_drift_fails_closed(self):
        registry = json.loads(APP.REGISTRY_PATH.read_text(encoding="utf-8"))
        expanded = copy.deepcopy(registry)
        expanded["records"][0]["allowed_asset_ids"].append("KR:XKRX:999999")
        with self.assertRaisesRegex(APP.ThemeApplicationError, "REGISTRY_SCOPE_OR_DECISION_MISMATCH"):
            APP._validate_registry_document(expanded, APP.load_contract())
        promoted = copy.deepcopy(registry)
        promoted["records"][0]["real_authority"] = True
        with self.assertRaisesRegex(APP.ThemeApplicationError, "REGISTRY_AUTHORITY_EXPANDED"):
            APP._validate_registry_document(promoted, APP.load_contract())

    def test_committed_source_admission_and_first_seen_are_verified(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        result = APP.resolve_source_admission(head)
        self.assertEqual(result["status"], "RATIFIED_EXACT_SCOPE")
        self.assertEqual(result["source_manifest_first_seen_at"], "2026-09-07T15:32:29Z")
        self.assertEqual(
            set(result["source_snapshot_first_seen_at"].values()),
            {"2026-09-07T15:32:29Z"},
        )
        self.assertGreaterEqual(
            result["admission_real_usable_from"],
            result["registry_record_first_seen_at"],
        )


class ActualTimeMembershipTests(unittest.TestCase):
    usable = "2026-09-07T16:05:50Z"  # 2026-09-08 01:05:50 KST

    def test_prior_observation_date_has_empty_interval(self):
        result = APP.evaluate_membership_times(
            "2026-09-07", self.usable, "2026-09-08T02:00:00Z"
        )
        self.assertFalse(result["nonempty"])
        self.assertFalse(result["active"])
        self.assertEqual(result["status"], "UNKNOWN_EMPTY_MEMBERSHIP_INTERVAL")

    def test_same_kst_day_evaluation_and_execution_can_be_active(self):
        result = APP.evaluate_membership_times(
            "2026-09-08",
            self.usable,
            "2026-09-08T10:00:00+09:00",
            "2026-09-08T15:00:00+09:00",
        )
        self.assertTrue(result["nonempty"])
        self.assertTrue(result["evaluation_active"])
        self.assertTrue(result["forward_execution_active"])
        self.assertTrue(result["active"])

    def test_next_day_1000_kst_carryover_fails(self):
        result = APP.evaluate_membership_times(
            "2026-09-08",
            self.usable,
            "2026-09-09T10:00:00+09:00",
            "2026-09-09T10:01:00+09:00",
        )
        self.assertFalse(result["evaluation_active"])
        self.assertFalse(result["forward_execution_active"])
        self.assertFalse(result["active"])
        self.assertEqual(result["status"], "UNKNOWN_EVALUATION_OUTSIDE_MEMBERSHIP_INTERVAL")

    def test_next_midnight_is_exclusive(self):
        result = APP.evaluate_membership_times(
            "2026-09-08",
            self.usable,
            "2026-09-08T23:59:59+09:00",
            "2026-09-09T00:00:00+09:00",
        )
        self.assertTrue(result["evaluation_active"])
        self.assertFalse(result["forward_execution_active"])
        self.assertFalse(result["active"])
        self.assertEqual(result["status"], "UNKNOWN_FORWARD_EXECUTION_OUTSIDE_MEMBERSHIP_INTERVAL")

    def test_naive_actual_time_is_rejected(self):
        with self.assertRaisesRegex(APP.ThemeApplicationError, "EVALUATION_AT_INVALID"):
            APP.evaluate_membership_times("2026-09-08", self.usable, "2026-09-08T10:00:00")


class ExistingValidatorReuseTests(unittest.TestCase):
    def test_real_master_and_leadership_are_independently_validated_before_date_mismatch(self):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        master = ROOT / "data/observations/krx_global_universe/2026-08-28/packet.json"
        leadership = ROOT / "data/observations/korea_leadership_context/2026-09-04/packet.json"
        with self.assertRaisesRegex(APP.ThemeApplicationError, "OBSERVATION_DATE_MISMATCH"):
            APP.evaluate_application(
                master,
                leadership,
                "2026-09-08T10:00:00+09:00",
                head,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
