#!/usr/bin/env python3
"""P1-KR-06 next-session observed-availability regression."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_risk_availability.py"
SPEC = importlib.util.spec_from_file_location("korea_risk_availability", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def source_observation():
    return {
        "response_sha256": "a" * 64,
        "row_count": 50,
        "usable_row_count": 49,
        "exact_index_row_count": 1,
    }


def build_receipt(**updates):
    kwargs = {
        "observation_date": "2026-08-25",
        "latest_collection_date": "2026-08-26",
        "latest_krx_sha256": "b" * 64,
        "source_observation": source_observation(),
        "observed_at_utc": "2026-08-26T00:05:00Z",
    }
    kwargs.update(updates)
    return MODULE.build_receipt(**kwargs)


def latest_krx():
    return {
        "collected_for_kst_date": "2026-08-26",
        "source_tier": "Official",
        "decision_readiness": {
            "confirmed_through": "2026-08-25",
            "same_day_confirmation": "next_day",
        },
    }


class KoreaRiskAvailabilityTest(unittest.TestCase):
    def test_policy_is_ratified_but_only_temporal_input_is_authorized(self):
        policy = MODULE.load_policy()
        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(policy["decision_capability"], "TEMPORAL_INPUT_ONLY")
        self.assertFalse(policy["same_day_decision_eligible"])
        self.assertEqual(
            policy["source_publication_time_status"], "UNKNOWN_NOT_INFERRED"
        )

    def test_receipt_uses_observed_time_not_source_publication_time(self):
        receipt = build_receipt()
        MODULE.verify_receipt(receipt)
        self.assertEqual(
            receipt["status"], "TEMPORAL_INPUT_QUALIFIED_NEXT_SESSION"
        )
        self.assertEqual(
            receipt["atlas_observed_available_at_kst"],
            "2026-08-26T09:05:00+09:00",
        )
        self.assertIsNone(receipt["source_publication_time"])
        self.assertTrue(receipt["authority"]["temporal_input_qualified"])
        for key, value in receipt["authority"].items():
            if key != "temporal_input_qualified":
                self.assertFalse(value, key)

    def test_same_day_and_future_like_inputs_fail_closed(self):
        with self.assertRaisesRegex(
            MODULE.AvailabilityError, "SAME_DAY_OBSERVATION_NOT_ELIGIBLE"
        ):
            build_receipt(observed_at_utc="2026-08-25T05:00:00Z")
        with self.assertRaisesRegex(
            MODULE.AvailabilityError, "NOT_NEXT_SESSION_INPUT"
        ):
            build_receipt(latest_collection_date="2026-08-25")

    def test_exact_kospi_row_is_required_without_retaining_level(self):
        body = json.dumps(
            {
                "OutBlock_1": [
                    {"BAS_DD": "20260825", "IDX_NM": "코스피", "CLSPRC_IDX": "9999.99"},
                    {"BAS_DD": "20260825", "IDX_NM": "제조", "CLSPRC_IDX": "123.45"},
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
        observed = MODULE.qualify_response_body(
            body, "20260825", MODULE.load_policy()
        )
        self.assertEqual(observed["exact_index_row_count"], 1)
        self.assertNotIn("close", observed)
        self.assertNotIn("9999.99", json.dumps(observed))
        missing = json.dumps(
            {
                "OutBlock_1": [
                    {"BAS_DD": "20260825", "IDX_NM": "제조", "CLSPRC_IDX": "123.45"}
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
        with self.assertRaisesRegex(
            MODULE.AvailabilityError, "EXACT_KOSPI_ROW_REQUIRED"
        ):
            MODULE.qualify_response_body(missing, "20260825", MODULE.load_policy())

    def test_self_rehashed_semantic_tamper_is_rejected(self):
        receipt = build_receipt()
        receipt["source_publication_time"] = "2026-08-25T16:00:00+09:00"
        unsigned = dict(receipt)
        unsigned.pop("receipt_sha256")
        receipt["receipt_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.AvailabilityError, "RECEIPT_INVALID"):
            MODULE.verify_receipt(receipt)

    def test_timestamp_alias_tamper_is_rejected_after_rehash(self):
        receipt = build_receipt()
        receipt["atlas_observed_available_at_kst"] = "2026-08-26T09:06:00+09:00"
        unsigned = dict(receipt)
        unsigned.pop("receipt_sha256")
        receipt["receipt_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.AvailabilityError, "TIMESTAMP_MISMATCH"):
            MODULE.verify_receipt(receipt)

        utc_alias = build_receipt()
        utc_alias["atlas_observed_available_at_utc"] = "2026-08-26T00:05:00+00:00"
        unsigned = dict(utc_alias)
        unsigned.pop("receipt_sha256")
        utc_alias["receipt_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.AvailabilityError, "TIMESTAMP_NOT_CANONICAL"
        ):
            MODULE.verify_receipt(utc_alias)

    def test_append_only_publish_and_existing_verification(self):
        receipt = build_receipt()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = MODULE.publish_receipt(receipt, root)
            self.assertEqual(path.read_bytes(), MODULE.canonical_bytes(receipt))
            verified = MODULE.verify_existing("2026-08-25", root)
            self.assertEqual(verified, receipt)
            with self.assertRaisesRegex(
                MODULE.AvailabilityError, "APPEND_ONLY_VIOLATION"
            ):
                MODULE.publish_receipt(receipt, root)

    def test_latest_krx_must_confirm_only_a_prior_session(self):
        self.assertEqual(
            MODULE.resolve_source_session(latest_krx()),
            ("2026-08-25", "2026-08-26"),
        )
        same_day = latest_krx()
        same_day["decision_readiness"]["confirmed_through"] = "2026-08-26"
        with self.assertRaisesRegex(
            MODULE.AvailabilityError, "LATEST_KRX_NOT_NEXT_SESSION"
        ):
            MODULE.resolve_source_session(same_day)
        wrong_rule = latest_krx()
        wrong_rule["decision_readiness"]["same_day_confirmation"] = "clock"
        with self.assertRaisesRegex(
            MODULE.AvailabilityError, "LATEST_KRX_FINALITY_MISMATCH"
        ):
            MODULE.resolve_source_session(wrong_rule)

    def test_receipt_has_no_reconstructive_source_values(self):
        encoded = MODULE.canonical_bytes(build_receipt()).decode("utf-8")
        for forbidden in ('"CLSPRC_IDX"', '"close"', '"OutBlock_1"', '"rows"'):
            self.assertNotIn(forbidden, encoded)

    def test_workflow_records_success_time_inside_the_fetching_process(self):
        workflow = (ROOT / ".github" / "workflows" / "collect.yml").read_text()
        step = workflow.split(
            "- name: Capture next-session KOSPI availability (P1-KR-06)", 1
        )[1].split("- name: Collect DART", 1)[0]
        self.assertIn(
            "python3 .github/scripts/korea_risk_availability.py",
            step,
        )
        self.assertNotIn("--observed-at-utc", step)


if __name__ == "__main__":
    unittest.main()
