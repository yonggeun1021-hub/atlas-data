#!/usr/bin/env python3
"""P8-12 fail-closed regression (item 7): missing/ambiguous/invalid inputs
must raise, never silently produce a fabricated or best-guess result."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.dynamic_clock import ClockEvent, DynamicClockError, build_episode_history, load_policy  # noqa: E402
from clock.review_candidate import compute_tier  # noqa: E402
from clock.operational_scan import MARKET_TRIGGER_COMPUTABILITY, not_computable_report  # noqa: E402
from clock.review_candidate import (  # noqa: E402
    ReviewCandidateError, build_expired_record, build_raw_trigger_record, build_subject_review_candidate,
)


class InvalidDateTests(unittest.TestCase):
    def test_malformed_detected_at_raises(self):
        ev = ClockEvent(detected_at="2026/08/20", evidence_available_at="2026-08-19",
                         evidence_hash="a" * 64, source="src", strength=0.5)
        with self.assertRaisesRegex(DynamicClockError, "DATE_INVALID"):
            build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])

    def test_missing_detected_at_raises(self):
        ev = ClockEvent(detected_at=None, evidence_available_at="2026-08-19",
                         evidence_hash="a" * 64, source="src", strength=0.5)
        with self.assertRaisesRegex(DynamicClockError, "DATE_INVALID"):
            build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])


class FutureEvidenceLeakageTests(unittest.TestCase):
    def test_evidence_available_at_after_detected_at_raises(self):
        # Would mean "the evidence became available AFTER Atlas detected
        # it" -- a future-data leak this module must refuse outright, as
        # defense-in-depth even though replay.opportunity_trigger already
        # enforces the same direction upstream.
        ev = ClockEvent(detected_at="2026-08-13", evidence_available_at="2026-08-14",
                         evidence_hash="a" * 64, source="src", strength=0.5)
        with self.assertRaisesRegex(DynamicClockError, "EVIDENCE_AVAILABLE_AT_AFTER_DETECTED_AT"):
            build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])


class UnknownTriggerTypeTests(unittest.TestCase):
    def test_unrecognized_trigger_type_raises_before_touching_events(self):
        ev = ClockEvent(detected_at="2026-08-13", evidence_available_at="2026-08-13",
                         evidence_hash="a" * 64, source="src", strength=0.5)
        with self.assertRaisesRegex(DynamicClockError, "NO_CLOCK_POLICY_FOR_TRIGGER_TYPE"):
            build_episode_history("BTC", "BTC", "TOTALLY_MADE_UP", [ev])


class MissingMarketTests(unittest.TestCase):
    def test_unknown_market_in_computability_matrix_raises_not_silently_empty(self):
        with self.assertRaises(KeyError):
            MARKET_TRIGGER_COMPUTABILITY["MARS"]  # noqa: B018

    def test_not_computable_report_for_unknown_market_raises(self):
        with self.assertRaises(KeyError):
            not_computable_report("MARS")


class MalformedEpisodeTests(unittest.TestCase):
    def test_build_raw_trigger_record_on_a_dict_missing_evidence_trail_raises(self):
        with self.assertRaises(KeyError):
            build_raw_trigger_record({"status": "ACTIVE"})

    def test_build_raw_trigger_record_on_wrong_status_raises_review_candidate_error(self):
        with self.assertRaisesRegex(ReviewCandidateError, "EPISODE_NOT_ACTIVE"):
            build_raw_trigger_record({"status": "EXPIRED"})

    def test_build_expired_record_on_wrong_status_raises(self):
        with self.assertRaisesRegex(ReviewCandidateError, "EPISODE_NOT_EXPIRED"):
            build_expired_record({"status": "ACTIVE"})

    def test_build_raw_trigger_record_on_missing_status_key_raises(self):
        with self.assertRaisesRegex(ReviewCandidateError, "EPISODE_NOT_ACTIVE"):
            build_raw_trigger_record({})

    def test_build_subject_review_candidate_on_empty_list_raises(self):
        with self.assertRaisesRegex(ReviewCandidateError, "NO_ACTIVE_EPISODES_FOR_SUBJECT"):
            build_subject_review_candidate("BTC", "BTC", [], pit_eligibility_status="PASS", decision_at="2026-08-20")


class OutOfOrderAmbiguityTests(unittest.TestCase):
    def test_two_events_on_the_same_date_with_different_hashes_is_accepted_as_same_day_renewal(self):
        # Not an error: same-day re-detection with a corrected/different
        # evidence citation is a legitimate renewal, not an ambiguity.
        events = [
            ClockEvent(detected_at="2026-08-13", evidence_available_at="2026-08-13",
                       evidence_hash="a" * 64, source="src", strength=0.5),
            ClockEvent(detected_at="2026-08-13", evidence_available_at="2026-08-13",
                       evidence_hash="b" * 64, source="src", strength=0.6),
        ]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["renewal_count"], 1)


class PolicyFileFailClosedTests(unittest.TestCase):
    def test_missing_policy_file_raises(self):
        with self.assertRaisesRegex(DynamicClockError, "POLICY_FILE_NOT_FOUND"):
            load_policy(Path("/nonexistent/dynamic_clock_policy.json"))

    def test_malformed_policy_file_missing_required_field_raises(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"policy_version": "x"}')
            path = Path(fh.name)
        try:
            with self.assertRaisesRegex(DynamicClockError, "POLICY_FILE_MISSING_FIELD"):
                load_policy(path)
        finally:
            path.unlink()


class PitIneligibilityDoesNotCrashTieringTests(unittest.TestCase):
    def test_not_computable_pit_status_maps_to_observation_only_not_a_crash(self):
        result = compute_tier(2, "NOT_COMPUTABLE", {"status": "x"}, {"status": "x"})
        self.assertEqual(result["tier"], "OBSERVATION_ONLY")

    def test_fail_pit_status_maps_to_observation_only(self):
        result = compute_tier(3, "FAIL", {"status": "x"}, {"status": "x"})
        self.assertEqual(result["tier"], "OBSERVATION_ONLY")


class DecisionDateValidationTests(unittest.TestCase):
    """`decision_date` fails closed only on a malformed date string."""

    def test_malformed_decision_date_raises(self):
        from clock.run_dynamic_clock import DynamicClockOrchestratorError, _validate_decision_date
        with self.assertRaisesRegex(DynamicClockOrchestratorError, "DECISION_DATE_INVALID"):
            _validate_decision_date("not-a-date")

    def test_none_decision_date_is_always_valid(self):
        from clock.run_dynamic_clock import _validate_decision_date
        _validate_decision_date(None)  # must not raise

    def test_any_well_formed_decision_date_is_valid(self):
        from clock.run_dynamic_clock import _validate_decision_date
        _validate_decision_date("2020-01-01")  # must not raise, even far in the past
        _validate_decision_date("2026-08-25")  # must not raise


class DecisionDatePrecedesEvidenceFailClosedTests(unittest.TestCase):
    """CIO integration review round 1, defect 1: the old `_effective_as_of`
    silently used `max(decision_date, evidence_as_of)`, overwriting an
    explicit, earlier `decision_date` with LATER evidence -- a real PIT
    lookahead violation the CIO directly reproduced. That function and its
    test class are DELETED, not patched. OPERATIONAL mode now fails closed
    instead."""

    def test_decision_date_behind_real_evidence_raises_in_operational_mode(self):
        from clock.run_dynamic_clock import DynamicClockOrchestratorError, run

        with self.assertRaisesRegex(DynamicClockOrchestratorError, "DECISION_DATE_PRECEDES_EVIDENCE_AS_OF"):
            run(decision_date="2020-01-01")  # far behind BTC/KOREA/CRYPTO's real committed evidence

    def test_same_decision_date_does_not_raise_in_historical_replay_mode(self):
        from clock.run_dynamic_clock import run

        report = run(decision_date="2020-01-01", mode="HISTORICAL_REPLAY")  # must not raise
        self.assertEqual(report["mode"], "HISTORICAL_REPLAY")

    def test_invalid_mode_raises(self):
        from clock.run_dynamic_clock import DynamicClockOrchestratorError, run

        with self.assertRaisesRegex(DynamicClockOrchestratorError, "INVALID_MODE"):
            run(decision_date="2026-08-22", mode="NOT_A_REAL_MODE")


if __name__ == "__main__":
    unittest.main()
