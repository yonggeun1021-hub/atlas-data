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
    def test_real_retained_cycle_matches_current_reference_and_expected_dates(self):
        packet = subject.build()
        rows = {row["market"]: row for row in packet["markets"]}
        reference = subject.paper_regime_reference.build_reference(ROOT)
        reference_rows = {row["market"]: row for row in reference["markets"]}

        for market in ("US", "KR", "CRYPTO"):
            row = rows[market]
            expected_eligibility = (
                "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
                if row["decision_date"] != row["expected_observation_date"]
                else "CURRENT_AS_FETCHED_NOT_PIT" if market == "US" else "CURRENT"
            )
            self.assertEqual(row["market_eligibility"], expected_eligibility)
            self.assertEqual(
                row["candidate_regime"],
                reference_rows[market]["paper_reference"]["candidate_regime"],
            )
            self.assertEqual(
                row["score"],
                reference_rows[market]["paper_reference"]["score"],
            )
            self.assertEqual(
                row["classification_may_be_displayed"],
                row["candidate_regime"] != "UNKNOWN",
            )

        same_dates = len({row["decision_date"] for row in rows.values()}) == 1
        all_current = all(
            row["market_eligibility"] in {"CURRENT", "CURRENT_AS_FETCHED_NOT_PIT"}
            for row in rows.values()
        )
        self.assertEqual(
            packet["status"],
            "STAGE2_READY_COMPLETE"
            if all_current and same_dates
            else "STAGE2_READY_PARTIAL_KR_SOURCE_NOT_ADVANCED",
        )
        self.assertEqual(packet["comparison"]["same_decision_date"], same_dates)
        self.assertEqual(
            packet["comparison"]["three_market_comparison_status"],
            "COMPLETE" if all_current and same_dates else "PARTIAL_OR_NON_COMPARABLE",
        )
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
