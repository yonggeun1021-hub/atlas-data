#!/usr/bin/env python3
"""P8-12 Dynamic Clock -- episode state machine regression: duplicate-event
suppression, cooldown (renewal), expiry, and re-activation, per the task's
explicit hard requirement that all four be implemented and tested."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.dynamic_clock import (  # noqa: E402
    ClockEvent, DynamicClockError, build_episode_history, close_stale_episodes, policy_for,
)


def _ev(detected_at: str, evidence_hash: str, evidence_available_at: str | None = None) -> ClockEvent:
    return ClockEvent(
        detected_at=detected_at,
        evidence_available_at=evidence_available_at or detected_at,
        evidence_hash=evidence_hash,
        source="test/fixture",
        strength=0.7,
    )


class PolicyTableTests(unittest.TestCase):
    def test_every_trigger_type_has_a_policy(self):
        from replay.opportunity_trigger import TRIGGER_TYPES
        for t in TRIGGER_TYPES:
            policy = policy_for(t)
            self.assertIn("cooldown_days", policy)
            self.assertIn("expiry_days", policy)
            self.assertGreaterEqual(policy["expiry_days"], policy["cooldown_days"])

    def test_unknown_trigger_type_fails_closed(self):
        with self.assertRaisesRegex(DynamicClockError, "NO_CLOCK_POLICY_FOR_TRIGGER_TYPE"):
            policy_for("NOT_A_REAL_TYPE")


class DuplicateSuppressionTests(unittest.TestCase):
    def test_identical_evidence_hash_does_not_open_a_second_episode(self):
        events = [_ev("2026-08-13", "a" * 64), _ev("2026-08-14", "a" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["renewal_count"], 0)
        self.assertEqual(len(episodes[0]["evidence_trail"]), 1)

    def test_duplicate_does_not_extend_expiry(self):
        events = [_ev("2026-08-13", "a" * 64), _ev("2026-08-13", "a" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual(episodes[0]["expiry"], "2026-08-15")  # 2 days after first detection only


class CooldownRenewalTests(unittest.TestCase):
    def test_distinct_evidence_within_expiry_renews_same_episode(self):
        # Real repo shape: BTC's PRICE_CONFIRMATION fired on 08-20/21/22 with
        # a DIFFERENT evidence_sha256 each day (new snapshot each day).
        events = [_ev("2026-08-20", "a" * 64), _ev("2026-08-21", "b" * 64), _ev("2026-08-22", "c" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual(len(episodes), 1, "must be ONE continuing episode, not three separate ones")
        ep = episodes[0]
        self.assertEqual(ep["status"], "ACTIVE")
        self.assertEqual(ep["opened_at"], "2026-08-20")
        self.assertEqual(ep["renewal_count"], 2)
        self.assertEqual(len(ep["evidence_trail"]), 3)
        self.assertEqual(ep["expiry"], "2026-08-24")  # renewed from the LAST event (08-22 + 2 days)
        self.assertEqual(ep["next_review_at"], "2026-08-23")

    def test_renewal_preserves_episode_id_across_renewals(self):
        events = [_ev("2026-08-13", "a" * 64), _ev("2026-08-14", "b" * 64)]
        episodes = build_episode_history("005930", "KOREA", "FLOW_REVERSAL", events)
        self.assertEqual(len(episodes), 1)


class ExpiryTests(unittest.TestCase):
    def test_gap_past_expiry_closes_the_episode_and_opens_a_new_one(self):
        # PRICE_CONFIRMATION expiry_days=2: a gap of 10 days is well past it.
        events = [_ev("2026-08-13", "a" * 64), _ev("2026-08-25", "b" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["status"], "EXPIRED")
        self.assertEqual(episodes[1]["status"], "ACTIVE")

    def test_close_stale_episodes_expires_an_active_episode_with_no_further_evidence(self):
        events = [_ev("2026-08-13", "a" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual(episodes[0]["status"], "ACTIVE")
        aged = close_stale_episodes(episodes, as_of_date="2026-08-20")  # far past 2026-08-15 expiry
        self.assertEqual(aged[0]["status"], "EXPIRED")

    def test_close_stale_episodes_leaves_a_fresh_active_episode_alone(self):
        events = [_ev("2026-08-13", "a" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        aged = close_stale_episodes(episodes, as_of_date="2026-08-14")  # still within expiry=08-15
        self.assertEqual(aged[0]["status"], "ACTIVE")

    def test_close_stale_episodes_does_not_mutate_input(self):
        events = [_ev("2026-08-13", "a" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        close_stale_episodes(episodes, as_of_date="2026-08-20")
        self.assertEqual(episodes[0]["status"], "ACTIVE", "close_stale_episodes must not mutate its input")


class ReactivationTests(unittest.TestCase):
    def test_reactivated_episode_links_back_to_the_expired_one(self):
        events = [_ev("2026-08-13", "a" * 64), _ev("2026-08-25", "b" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        expired, reactivated = episodes
        self.assertIsNone(expired["reactivated_from_episode_id"])
        self.assertEqual(reactivated["reactivated_from_episode_id"], expired["episode_id"])

    def test_three_episodes_across_two_expiry_gaps(self):
        events = [_ev("2026-08-13", "a" * 64), _ev("2026-08-25", "b" * 64), _ev("2026-09-10", "c" * 64)]
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual([e["status"] for e in episodes], ["EXPIRED", "EXPIRED", "ACTIVE"])
        self.assertEqual(episodes[2]["reactivated_from_episode_id"], episodes[1]["episode_id"])


class FailClosedTests(unittest.TestCase):
    def test_out_of_order_events_raise(self):
        events = [_ev("2026-08-14", "a" * 64), _ev("2026-08-13", "b" * 64)]
        with self.assertRaisesRegex(DynamicClockError, "EVENTS_NOT_CHRONOLOGICAL"):
            build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)

    def test_empty_event_stream_yields_no_episodes(self):
        self.assertEqual(build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", []), [])


class DeterminismTests(unittest.TestCase):
    def test_same_input_yields_identical_episode_history(self):
        events = [_ev("2026-08-13", "a" * 64), _ev("2026-08-14", "b" * 64), _ev("2026-08-25", "c" * 64)]
        r1 = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        r2 = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
