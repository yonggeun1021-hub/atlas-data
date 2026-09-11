from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from regime import stage1_market_tuple as subject


def _next_regular_session(observed_date: str) -> str:
    """The first regular (Mon-Fri) KR session date strictly after ``observed_date``.

    Derived from the observed date, never a literal, so the controlled test
    below cannot go stale when the live KR source advances. Exchange holidays
    are deliberately not modelled: the producer decides eligibility by exact
    date equality, so any later date exercises the same
    ``SOURCE_NOT_ADVANCED_EXPECTED_SESSION`` branch.
    """
    day = dt.date.fromisoformat(observed_date) + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day.isoformat()


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

    def test_kr_eligibility_is_decided_by_expected_versus_observed_date(self):
        """Both KR eligibility branches, each forced by its own cause.

        The real-retained smoke above can only observe whichever branch the
        live KR source happens to land on today. Here the cause is controlled:
        same producer, same retained inputs and receipts, and a temporary copy
        of the spec whose KR expected date is set relative to the KR
        ``as_of_date`` the current PAPER reference actually re-derives -- no
        literal date, so the next cron cannot make this stale. The temporary
        spec lives under the repository root because the producer records
        ``input.path`` relative to it.
        """
        reference = subject.paper_regime_reference.build_reference(ROOT)
        observed_kr = {row["market"]: row for row in reference["markets"]}["KR"]["as_of_date"]
        baseline = {row["market"]: row for row in subject.build()["markets"]}
        spec = json.loads(subject.INPUT.read_text(encoding="utf-8"))
        cases = (
            ("CURRENT", observed_kr),
            ("SOURCE_NOT_ADVANCED_EXPECTED_SESSION", _next_regular_session(observed_kr)),
        )
        for expected_eligibility, expected_date in cases:
            with self.subTest(expected_eligibility=expected_eligibility):
                controlled = copy.deepcopy(spec)
                controlled["expected_market_dates"]["KR"] = expected_date
                with tempfile.TemporaryDirectory(dir=ROOT) as raw:
                    path = Path(raw) / "input.json"
                    path.write_text(json.dumps(controlled), encoding="utf-8")
                    packet = subject.build(ROOT, path)
                rows = {row["market"]: row for row in packet["markets"]}
                self.assertEqual(rows["KR"]["decision_date"], observed_kr)
                self.assertEqual(rows["KR"]["expected_observation_date"], expected_date)
                self.assertEqual(rows["KR"]["market_eligibility"], expected_eligibility)
                # Controlling the KR expected date must not move the other
                # markets, the retained source receipts, the future slots, or
                # the closed authority boundary.
                for market in ("US", "CRYPTO"):
                    self.assertEqual(
                        rows[market]["market_eligibility"],
                        baseline[market]["market_eligibility"],
                        market,
                    )
                self.assertEqual(
                    [row["payload_sha256"] for row in packet["source_event_receipts"]],
                    [row["payload_sha256"] for row in subject.build()["source_event_receipts"]],
                )
                self.assertTrue(all(slot["status"] == "NOT_DUE" for slot in packet["future_scheduled_slots"]))
                self.assertFalse(packet["authority"]["real_trading"])
                self.assertFalse(packet["authority"]["order_authorized"])
                self.assertFalse(packet["authority"]["trading_authorized"])
                self.assertFalse(packet["authority"]["capital_authorized"])

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
