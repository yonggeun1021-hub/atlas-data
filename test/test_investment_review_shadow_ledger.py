#!/usr/bin/env python3
"""P10-06 zero-capital Investment Review ledger regression."""
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load("investment_review_shadow_ledger", ROOT / "shadow" / "investment_review_shadow_ledger.py")
P8_FIXTURE = load("investment_review_p8_fixture", ROOT / "test" / "test_investment_decision_review.py")


def review():
    rules = P8_FIXTURE.rule_packet()
    return P8_FIXTURE.MODULE.build_packet(
        P8_FIXTURE.thesis(rules), rules, "2026-08-24T00:01:00Z"
    )


class InvestmentReviewShadowLedgerTests(unittest.TestCase):
    def test_blocked_review_is_recorded_without_capital_action_order_or_stage(self):
        record = MODULE.build_record(review(), "2026-08-24T00:02:00Z", 1)
        self.assertEqual(record["review_outcome"], "BLOCKED")
        self.assertFalse(record["proposal_observed"])
        self.assertEqual(record["capital"], {"authorized": False, "amount": 0})
        self.assertIsNone(record["action"])
        self.assertIsNone(record["order"])
        self.assertIsNone(record["stage_change"])

    def test_chain_requires_previous_hash_after_genesis(self):
        first = MODULE.build_record(review(), "2026-08-24T00:02:00Z", 1)
        second = MODULE.build_record(
            review(), "2026-08-25T00:02:00Z", 2, first["record_sha256"]
        )
        self.assertEqual(second["lineage"]["previous_record_sha256"], first["record_sha256"])
        with self.assertRaisesRegex(MODULE.InvestmentReviewShadowLedgerError, "PREVIOUS_RECORD_SHA_INVALID"):
            MODULE.build_record(review(), "2026-08-25T00:02:00Z", 2)

    def test_self_rehashed_capital_or_stage_drift_is_rejected(self):
        for key, value in (("capital", {"authorized": True, "amount": 1}), ("stage_change", "READY")):
            record = MODULE.build_record(review(), "2026-08-24T00:02:00Z", 1)
            record[key] = value
            record["record_sha256"] = MODULE.payload_sha256({
                name: item for name, item in record.items() if name != "record_sha256"
            })
            with self.assertRaisesRegex(MODULE.InvestmentReviewShadowLedgerError, "RECORD_BOUNDARY_INVALID"):
                MODULE.validate_record(record)


if __name__ == "__main__":
    unittest.main()
