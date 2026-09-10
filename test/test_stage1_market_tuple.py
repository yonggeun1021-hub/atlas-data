from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from regime import stage1_market_tuple as subject


class Stage1MarketTupleTests(unittest.TestCase):
    def test_real_retained_cycle_is_stage2_ready_with_explicit_kr_staleness(self):
        packet = subject.build()
        rows = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(packet["status"], "STAGE2_READY_PARTIAL_KR_SOURCE_NOT_ADVANCED")
        self.assertEqual(rows["US"]["candidate_regime"], "NEUTRAL")
        self.assertEqual(rows["KR"]["market_eligibility"], "SOURCE_NOT_ADVANCED_EXPECTED_SESSION")
        self.assertEqual(rows["CRYPTO"]["candidate_regime"], "UNKNOWN")
        self.assertIsNone(rows["CRYPTO"]["score"])
        self.assertFalse(rows["CRYPTO"]["classification_may_be_displayed"])
        self.assertFalse(packet["comparison"]["same_decision_date"])
        self.assertTrue(all(slot["status"] == "NOT_DUE" for slot in packet["future_scheduled_slots"]))
        self.assertFalse(packet["authority"]["real_trading"])
        self.assertFalse(packet["authority"]["order_authorized"])

    def test_event_time_tamper_fails_closed(self):
        spec = json.loads(subject.INPUT.read_text(encoding="utf-8"))
        spec["events"][0]["completed_at"] = "2026-09-08T21:00:00Z"
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "input.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            with self.assertRaisesRegex(subject.Stage1MarketTupleError, "EVENT_TIME_ORDER_INVALID"):
                subject.build(ROOT, path)

    def test_workflow_hash_tamper_fails_closed(self):
        spec = json.loads(subject.INPUT.read_text(encoding="utf-8"))
        spec["events"][0]["workflow_file_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "input.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            with self.assertRaisesRegex(subject.Stage1MarketTupleError, "WORKFLOW_HASH_MISMATCH"):
                subject.build(ROOT, path)


if __name__ == "__main__":
    unittest.main()
