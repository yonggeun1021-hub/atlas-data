#!/usr/bin/env python3
"""P11 PIT Replay -- Opportunity Episode deduplication regression.

CIO review (PR #210, flaw 5): a single multi-day rally/drawdown must be
counted ONCE as an episode, not once per calendar day it spans.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.opportunity_episode import MAX_GAP_DAYS, group_into_episodes  # noqa: E402


def daily_row(subject, date, root_cause, outcome):
    return {
        "subject": subject, "decision_date": date, "root_cause": root_cause,
        "forward_return_pct": outcome, "materiality_horizon_used": "5",
        "evidence_sha256": "a" * 64, "source": "src",
    }


class EpisodeGroupingTests(unittest.TestCase):
    def test_five_consecutive_daily_rows_become_one_episode(self):
        rows = [daily_row("000660", f"2026-07-{d:02d}", "DATA_FAILURE", 10.0 + d) for d in range(22, 27)]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["daily_rows_deduped"], 5)
        self.assertEqual(episodes[0]["first_detected_date"], "2026-07-22")
        self.assertEqual(episodes[0]["last_detected_date"], "2026-07-26")
        self.assertEqual(episodes[0]["max_delay_days"], 4)

    def test_final_outcome_is_taken_from_the_first_row_not_the_last(self):
        rows = [
            daily_row("X", "2026-07-22", "SIGNAL_MISS", 5.5),
            daily_row("X", "2026-07-23", "SIGNAL_MISS", 99.9),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(episodes[0]["final_outcome_pct"], 5.5)

    def test_gap_larger_than_max_gap_days_starts_a_new_episode(self):
        rows = [
            daily_row("X", "2026-07-22", "SIGNAL_MISS", 5.0),
            daily_row("X", f"2026-07-{22 + MAX_GAP_DAYS + 2:02d}", "SIGNAL_MISS", 6.0),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 2)

    def test_different_root_cause_never_merges_into_one_episode(self):
        rows = [
            daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0),
            daily_row("X", "2026-08-14", "GATE_BLOCK", 6.0),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 2)

    def test_different_subject_never_merges(self):
        rows = [
            daily_row("A", "2026-08-13", "SIGNAL_MISS", 5.0),
            daily_row("B", "2026-08-14", "SIGNAL_MISS", 6.0),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 2)

    def test_episode_id_is_deterministic(self):
        rows = [daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0)]
        e1 = group_into_episodes(rows, outcome_field="forward_return_pct")
        e2 = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(e1[0]["episode_id"], e2[0]["episode_id"])

    def test_first_action_eligible_date_skips_data_failure_rows(self):
        rows = [
            daily_row("X", "2026-08-10", "DATA_FAILURE", 5.0),
            daily_row("X", "2026-08-11", "DATA_FAILURE", 5.0),
        ]
        # DATA_FAILURE rows never merge with a real-root-cause row (same
        # subject, different root_cause) -- confirm the pure-DATA_FAILURE
        # episode reports no action-eligible date at all.
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)
        self.assertIsNone(episodes[0]["first_action_eligible_date"])

    def test_grouping_key_is_never_the_size_or_sign_of_the_outcome(self):
        # A wildly different outcome on consecutive days with the same
        # subject/root_cause must still merge -- proving the grouping rule
        # is (subject, date-adjacency, root_cause) only, never outcome-based.
        rows = [
            daily_row("X", "2026-08-13", "SIGNAL_MISS", 999.0),
            daily_row("X", "2026-08-14", "SIGNAL_MISS", -999.0),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)

    def test_episode_count_is_far_smaller_than_raw_row_count_for_a_long_rally(self):
        rows = [daily_row("X", f"2026-07-{d:02d}", "DATA_FAILURE", 1.0) for d in range(22, 32)]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)
        self.assertLess(len(episodes), len(rows))


if __name__ == "__main__":
    unittest.main()
