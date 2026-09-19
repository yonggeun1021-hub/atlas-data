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
            "2026-08-21", run_dynamic_clock.MODE_HISTORICAL_REPLAY
        )
        cls.decision_date = cls.report["decision_date"]
        cls.day_before = (
            dt.date.fromisoformat(cls.decision_date) - dt.timedelta(days=1)
        ).isoformat()
        # Legacy/evening geometry: the same UTC calendar day as decision_date.
        # `30 9 * * 1-5` is 18:30 KST of decision_date, and every archived
        # packet was derived on this basis.
        cls.as_of_utc = f"{cls.decision_date}T23:59:59Z"
        # Production morning geometry: cron `5 22 * * *` is 07:05 KST of the
        # NEXT KST day, so the instant's UTC calendar day is one day behind
        # the report's KST decision_date.
        cls.morning_as_of_utc = f"{cls.day_before}T22:05:00Z"
        # 15:00:00Z is exactly 00:00:00 KST of decision_date; one second
        # earlier is still 23:59:59 KST of the previous KST business day.
        cls.kst_midnight_as_of_utc = f"{cls.day_before}T15:00:00Z"
        cls.last_kst_instant_before_decision_date = f"{cls.day_before}T14:59:59Z"
        # 23:59:59 KST of decision_date itself.
        cls.last_kst_instant_of_decision_date = f"{cls.decision_date}T14:59:59Z"

    def semantic_body(self, packet):
        """The adapter packet minus its two instant-derived keys."""
        return {
            key: value for key, value in packet.items()
            if key not in {"as_of_utc", "packet_sha256"}
        }

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
        # Re-based onto the corrected basis: `{decision_date - 1}T23:59:59Z` is
        # 08:59:59 KST of decision_date, i.e. the SAME KST business day, so it
        # is no longer an "after boundary" input.  `T00:00:00Z` is 09:00 KST of
        # the previous KST business day and is genuinely earlier.
        for earlier in (1, 2, 3, 30):
            day = (
                dt.date.fromisoformat(self.decision_date) - dt.timedelta(days=earlier)
            ).isoformat()
            with self.assertRaisesRegex(
                adapter.DynamicClockSignalObservationError,
                "REPORT_DECISION_AFTER_BOUNDARY_AS_OF",
            ):
                adapter.build_packet(self.report, f"{day}T00:00:00Z")

    def test_truly_future_report_decision_date_is_still_rejected(self):
        # The guard is corrected, not removed: a decision_date that is genuinely
        # after the instant's KST business day still fails closed, before any
        # candidate is even read.
        for ahead in (1, 2, 30):
            tampered = copy.deepcopy(self.report)
            tampered["decision_date"] = (
                dt.date.fromisoformat(self.decision_date) + dt.timedelta(days=ahead)
            ).isoformat()
            with self.assertRaisesRegex(
                adapter.DynamicClockSignalObservationError,
                "REPORT_DECISION_AFTER_BOUNDARY_AS_OF",
            ):
                adapter.build_packet(
                    tampered, self.last_kst_instant_of_decision_date
                )

    def test_kst_morning_generation_window_builds_instead_of_failing_closed(self):
        # The exact production defect: 22:05Z is the next KST business day, so
        # the run instant is a UTC calendar day behind the report's KST
        # decision_date.
        self.assertLess(self.morning_as_of_utc[:10], self.decision_date)
        self.assertEqual(
            adapter._kst_business_date(self.morning_as_of_utc), self.decision_date
        )

        observation, value = adapter.build_boundary_input(
            self.report, self.morning_as_of_utc
        )
        packet = boundary.build_packet(value)
        self.assertGreater(observation["subject_count"], 0)
        self.assertEqual(observation["as_of_utc"], self.morning_as_of_utc)
        # Building on the morning geometry must not add any authority.
        self.assertEqual(
            packet["summary"]["signal_present_count"], observation["subject_count"]
        )
        self.assertEqual(packet["summary"]["ready_count"], 0)
        self.assertEqual(packet["summary"]["entry_trigger_count"], 0)
        self.assertEqual(packet["summary"]["order_intent_count"], 0)
        for row in value["subjects"]:
            self.assertEqual(row["ready_status"], "NOT_EVALUATED")
            self.assertIsNone(row["ready_source_ref"])
            self.assertIsNone(row["ready_source_sha256"])

    def test_morning_and_evening_geometry_emit_the_same_adapter_body(self):
        morning = adapter.build_packet(self.report, self.morning_as_of_utc)
        evening = adapter.build_packet(self.report, self.as_of_utc)
        # Restoring the morning run changes only its own instant keys: every
        # subject row, lineage ref, projection status and authority flag is
        # identical to the evening packet the run already emitted.
        self.assertEqual(self.semantic_body(morning), self.semantic_body(evening))
        self.assertNotEqual(morning["as_of_utc"], evening["as_of_utc"])
        self.assertNotEqual(morning["packet_sha256"], evening["packet_sha256"])

    def test_kst_midnight_is_the_exact_accept_reject_boundary(self):
        # 15:00:00Z is 00:00:00 KST of decision_date: accepted.
        self.assertEqual(
            adapter._kst_business_date(self.kst_midnight_as_of_utc),
            self.decision_date,
        )
        accepted = adapter.build_packet(self.report, self.kst_midnight_as_of_utc)
        self.assertGreater(accepted["subject_count"], 0)

        # One second earlier is still the previous KST business day: rejected.
        self.assertEqual(
            adapter._kst_business_date(self.last_kst_instant_before_decision_date),
            self.day_before,
        )
        with self.assertRaisesRegex(
            adapter.DynamicClockSignalObservationError,
            "REPORT_DECISION_AFTER_BOUNDARY_AS_OF",
        ):
            adapter.build_packet(
                self.report, self.last_kst_instant_before_decision_date
            )

        # ... and the same wall-clock second of decision_date's own KST day
        # (23:59:59 KST) is accepted.
        self.assertEqual(
            adapter._kst_business_date(self.last_kst_instant_of_decision_date),
            self.decision_date,
        )
        own_day = adapter.build_packet(
            self.report, self.last_kst_instant_of_decision_date
        )
        self.assertEqual(
            self.semantic_body(own_day), self.semantic_body(accepted)
        )

    def test_business_date_is_offset_aware_not_string_truncation(self):
        for instant in (
            self.as_of_utc,
            self.morning_as_of_utc,
            self.kst_midnight_as_of_utc,
            self.last_kst_instant_before_decision_date,
            self.last_kst_instant_of_decision_date,
        ):
            aware = dt.datetime.strptime(instant, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=dt.timezone.utc
            )
            expected = aware.astimezone(
                dt.timezone(dt.timedelta(hours=9))
            ).date().isoformat()
            derived = adapter._kst_business_date(instant)
            self.assertEqual(derived, expected)
            # Monotone relaxation: the KST business date is never earlier than
            # the UTC calendar date, so no input that used to pass can now fail.
            self.assertGreaterEqual(derived, instant[:10])

    def test_legacy_same_utc_day_packet_bytes_and_revalidation_are_unchanged(self):
        # Archived packets were derived on the same-UTC-day geometry.  They must
        # still re-derive byte-for-byte and still round-trip validate_packet,
        # which independently rebuilds the packet from the report.
        packet = adapter.build_packet(self.report, self.as_of_utc)
        self.assertEqual(
            adapter.canonical_json(packet),
            adapter.canonical_json(
                adapter.build_packet(copy.deepcopy(self.report), self.as_of_utc)
            ),
        )
        self.assertEqual(packet["as_of_utc"], self.as_of_utc)
        self.assertNotIn("as_of_date", packet)
        self.assertFalse(any("as_of_date" in row for row in packet["subjects"]))
        unsigned = {
            key: value for key, value in packet.items() if key != "packet_sha256"
        }
        self.assertEqual(packet["packet_sha256"], adapter.payload_sha256(unsigned))
        self.assertEqual(adapter.validate_packet(packet, self.report), packet)

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

    def test_daily_action_boundary_morning_geometry_is_ready_not_degraded(self):
        # Orchestrator-level parity: the scheduled morning run passed its UTC
        # generated_at straight through to the adapter and came back DEGRADED
        # with REPORT_DECISION_AFTER_BOUNDARY_AS_OF.
        row = DAILY.build_action_boundary(self.morning_as_of_utc, self.report)
        self.assertEqual(row["status"], "READY")
        self.assertEqual(
            row["reason"],
            "DYNAMIC_CLOCK_SIGNAL_OBSERVATIONS_BOUND_READY_NOT_EVALUATED_NO_ACTION_AUTHORITY",
        )
        evening = DAILY.build_action_boundary(self.as_of_utc, self.report)
        self.assertEqual(row["packet"]["summary"], evening["packet"]["summary"])
        summary = row["packet"]["summary"]
        self.assertGreater(summary["signal_present_count"], 0)
        self.assertEqual(summary["ready_count"], 0)
        self.assertEqual(summary["entry_trigger_count"], 0)
        self.assertEqual(summary["order_intent_count"], 0)
        self.assertFalse(row["decision_eligible"])
        self.assertFalse(row["action_eligible"])
        self.assertFalse(row["order_eligible"])

    def test_daily_action_boundary_without_dynamic_report_stays_empty(self):
        row = DAILY.build_action_boundary(self.as_of_utc, None)
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["packet"]["summary"]["subject_count"], 0)

    def test_real_daily_packet_wires_the_same_live_signal_population(self):
        # The daily packet now freezes and verifies the Dynamic Clock report's
        # exact source decision date.  Keep the mocked historical report and
        # packet on that same date; a next-day packet would be a deliberately
        # invalid cross-date source binding, not an isolated observation.
        daily_decision_date = self.report["decision_date"]
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
