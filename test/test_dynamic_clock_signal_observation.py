#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock import run_dynamic_clock
from decision import dynamic_clock_signal_observation as adapter
from decision import ready_signal_order_boundary as boundary

DAILY_SPEC = importlib.util.spec_from_file_location(
    "atlas_p8_03_daily_orchestrator_test",
    ROOT / "briefing/daily_orchestrator.py",
)
DAILY = importlib.util.module_from_spec(DAILY_SPEC)
assert DAILY_SPEC.loader is not None
DAILY_SPEC.loader.exec_module(DAILY)


class DynamicClockSignalObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_dynamic_clock.run(
            "2026-08-20", run_dynamic_clock.MODE_HISTORICAL_REPLAY
        )
        cls.as_of_utc = f"{cls.report['decision_date']}T23:59:59Z"

    def test_real_candidates_reach_boundary_as_signal_observations(self):
        observation, value = adapter.build_boundary_input(
            self.report, self.as_of_utc
        )
        packet = boundary.build_packet(value)
        self.assertGreater(observation["subject_count"], 0)
        self.assertEqual(packet["summary"]["subject_count"], observation["subject_count"])
        self.assertEqual(packet["summary"]["signal_present_count"], observation["subject_count"])
        self.assertEqual(packet["summary"]["ready_count"], 0)
        self.assertEqual(packet["summary"]["entry_trigger_count"], 0)
        self.assertEqual(packet["summary"]["order_intent_count"], 0)

    def test_ready_is_never_claimed_and_has_no_lineage(self):
        _, value = adapter.build_boundary_input(self.report, self.as_of_utc)
        for row in value["subjects"]:
            self.assertEqual(row["ready_status"], "NOT_EVALUATED")
            self.assertIsNone(row["ready_source_ref"])
            self.assertIsNone(row["ready_source_sha256"])

    def test_source_candidate_hash_is_exact_signal_lineage(self):
        observation, value = adapter.build_boundary_input(self.report, self.as_of_utc)
        observed = {
            row["boundary_subject_id"]: row for row in observation["subjects"]
        }
        for row in value["subjects"]:
            source = observed[row["subject_id"]]
            self.assertEqual(row["signal_source_sha256"], source["source_candidate_record_hash"])
            self.assertIn(observation["source_report_sha256"], row["signal_source_ref"])

    def test_authority_is_observation_only(self):
        observation, value = adapter.build_boundary_input(self.report, self.as_of_utc)
        self.assertTrue(observation["authority"]["dynamic_review_trigger_observation_only"])
        self.assertTrue(value["authority"]["signal_observation_only"])
        for authority in (observation["authority"], value["authority"]):
            for key, enabled in authority.items():
                if key not in {
                    "dynamic_review_trigger_observation_only",
                    "ready_observation_only",
                    "signal_observation_only",
                }:
                    self.assertFalse(enabled, key)

    def test_btc_projection_is_explicitly_not_security_identity(self):
        observation = adapter.build_packet(self.report, self.as_of_utc)
        btc = [row for row in observation["subjects"] if row["source_market"] == "BTC"]
        self.assertTrue(btc)
        self.assertTrue(all(row["boundary_market"] == "CRYPTO" for row in btc))
        self.assertTrue(all(
            row["market_projection_status"]
            == "PRESENTATION_VOCABULARY_ONLY_NOT_SECURITY_IDENTITY"
            for row in btc
        ))

    def test_source_market_mismatch_is_rejected(self):
        tampered = copy.deepcopy(self.report)
        candidate = tampered["by_market"]["BTC"]["review_queue"][0]
        candidate["market"] = "CRYPTO"
        candidate["record_hash"] = adapter.REVIEW_CANDIDATE.payload_sha256({
            key: value for key, value in candidate.items() if key != "record_hash"
        })
        with self.assertRaisesRegex(
            adapter.DynamicClockSignalObservationError,
            "CANDIDATE_MARKET_MISMATCH",
        ):
            adapter.build_packet(tampered, self.as_of_utc)

    def test_report_after_boundary_asof_is_rejected(self):
        decision = dt.date.fromisoformat(self.report["decision_date"])
        prior_kst = dt.datetime.combine(
            decision - dt.timedelta(days=1),
            dt.time.max,
            tzinfo=adapter.KST,
        )
        prior_utc = prior_kst.astimezone(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with self.assertRaisesRegex(
            adapter.DynamicClockSignalObservationError,
            "REPORT_DECISION_AFTER_BOUNDARY_AS_OF",
        ):
            adapter.build_packet(self.report, prior_utc)

    def test_previous_utc_date_on_same_kst_decision_day_is_accepted(self):
        decision = dt.date.fromisoformat(self.report["decision_date"])
        start_kst = dt.datetime.combine(
            decision,
            dt.time.min,
            tzinfo=adapter.KST,
        )
        boundary_utc = start_kst.astimezone(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self.assertLess(boundary_utc[:10], self.report["decision_date"])
        packet = adapter.build_packet(self.report, boundary_utc)
        self.assertEqual(packet["as_of_utc"], boundary_utc)

    def test_candidate_decision_date_must_match_report_decision_date(self):
        tampered = copy.deepcopy(self.report)
        tampered["decision_date"] = (
            dt.date.fromisoformat(self.report["decision_date"])
            + dt.timedelta(days=1)
        ).isoformat()
        with self.assertRaisesRegex(
            adapter.DynamicClockSignalObservationError,
            "CANDIDATE_REPORT_DECISION_DATE_MISMATCH",
        ):
            adapter.build_packet(
                tampered, f"{tampered['decision_date']}T23:59:59Z"
            )

    def test_tier_does_not_change_signal_identity_or_authority(self):
        baseline = adapter.build_packet(self.report, self.as_of_utc)
        tampered = copy.deepcopy(self.report)
        candidate = tampered["by_market"]["BTC"]["review_queue"][0]
        candidate["tier"] = "OBSERVATION_ONLY"
        candidate["human_review_required"] = False
        candidate["record_hash"] = adapter.REVIEW_CANDIDATE.payload_sha256({
            key: value for key, value in candidate.items() if key != "record_hash"
        })
        changed = adapter.build_packet(tampered, self.as_of_utc)
        before = next(row for row in baseline["subjects"] if row["source_market"] == "BTC")
        after = next(row for row in changed["subjects"] if row["source_market"] == "BTC")
        self.assertEqual(before["boundary_subject_id"], after["boundary_subject_id"])
        self.assertEqual(before["signal_id"], after["signal_id"])
        self.assertEqual(baseline["authority"], changed["authority"])

    def test_adapter_output_tamper_and_resign_is_rejected_by_rederivation(self):
        packet = adapter.build_packet(self.report, self.as_of_utc)
        packet["subjects"][0]["ready_status"] = "READY"
        packet["packet_sha256"] = adapter.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            adapter.DynamicClockSignalObservationError,
            "OUTPUT_DERIVATION_MISMATCH",
        ):
            adapter.validate_packet(packet, self.report)

    def test_build_is_deterministic(self):
        first = adapter.build_boundary_input(self.report, self.as_of_utc)
        second = adapter.build_boundary_input(copy.deepcopy(self.report), self.as_of_utc)
        self.assertEqual(first, second)

    def test_daily_action_boundary_consumes_real_candidates_without_ready_or_order(self):
        row = DAILY.build_action_boundary(self.as_of_utc, self.report)
        self.assertEqual(row["status"], "READY")
        self.assertEqual(
            row["reason"],
            "DYNAMIC_CLOCK_SIGNAL_OBSERVATIONS_BOUND_READY_NOT_EVALUATED_NO_ACTION_AUTHORITY",
        )
        summary = row["packet"]["summary"]
        self.assertGreater(summary["signal_present_count"], 0)
        self.assertEqual(summary["ready_count"], 0)
        self.assertEqual(summary["entry_trigger_count"], 0)
        self.assertEqual(summary["order_intent_count"], 0)

    def test_daily_action_boundary_without_dynamic_report_stays_empty(self):
        row = DAILY.build_action_boundary(self.as_of_utc, None)
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["packet"]["summary"]["subject_count"], 0)

    def test_real_daily_packet_wires_the_same_live_signal_population(self):
        daily_decision_date = (
            dt.date.fromisoformat(self.report["decision_date"]) + dt.timedelta(days=1)
        ).isoformat()
        with mock.patch.object(
            DAILY.DYNAMIC_CLOCK, "run", return_value=copy.deepcopy(self.report)
        ):
            packet = DAILY.build_packet(
                "evening",
                daily_decision_date,
                f"{daily_decision_date}T23:59:59Z",
            )
        row = next(
            item for item in packet["components"]
            if item["component_id"] == "ACTION_BOUNDARY"
        )
        rotation = next(
            item for item in packet["components"]
            if item["component_id"] == "ROTATION_DISCOVERY"
        )
        expected = sum(
            len(market["review_queue"])
            for market in self.report["by_market"].values()
        )
        self.assertEqual(row["status"], "READY")
        self.assertEqual(row["packet"]["summary"]["subject_count"], expected)
        self.assertEqual(row["packet"]["summary"]["signal_present_count"], expected)
        self.assertEqual(row["packet"]["summary"]["ready_count"], 0)
        self.assertEqual(row["packet"]["summary"]["entry_trigger_count"], 0)
        self.assertEqual(row["packet"]["summary"]["order_intent_count"], 0)
        self.assertEqual(
            rotation["packet"]["summary"]["signal_observation_count"], expected
        )
        self.assertEqual(rotation["packet"]["summary"]["ready_count"], 0)
        self.assertEqual(rotation["packet"]["summary"]["entry_trigger_count"], 0)
        self.assertEqual(rotation["packet"]["discovery"]["new_candidates"], [])


if __name__ == "__main__":
    unittest.main()
