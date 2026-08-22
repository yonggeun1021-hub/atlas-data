#!/usr/bin/env python3
"""P10-02/P10-03 PIT Replay -- Opportunity Episode deduplication
regression.

CIO review round 2 (flaw 5): a single multi-day rally/drawdown must be
counted ONCE as an episode, not once per calendar day it spans.
CIO review round 3 (flaw 3): grouping must be keyed on
(subject, trigger_family, forward-window overlap), not a flat calendar-day
gap -- and episodes built from SIGNAL_MISS/DATA_FAILURE rows (nothing
actually detected) must never carry a `first_detected_date`-shaped field;
the renamed field set is episode_start_date/first_signal_date/
first_action_eligible_date/episode_end_date/outcome_window_start/
outcome_window_end/representative_forward_return_pct.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.opportunity_episode import (  # noqa: E402
    FALLBACK_GAP_DAYS, NO_SIGNAL_FAMILY, group_into_episodes, trigger_family,
)


def daily_row(subject, date, root_cause, outcome, triggers=None, entry_date=None, outcome_window_end=None):
    return {
        "subject": subject, "decision_date": date, "root_cause": root_cause,
        "forward_return_pct": outcome, "materiality_horizon_used": "5",
        "evidence_sha256": "a" * 64, "source": "src",
        "triggers_detected": triggers or [],
        "entry_date": entry_date or date,
        "outcome_window_end": outcome_window_end,
    }


class RenamedFieldSetTests(unittest.TestCase):
    def test_no_first_detected_date_field_exists_anywhere(self):
        rows = [daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0)]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertNotIn("first_detected_date", episodes[0])

    def test_signal_miss_episode_has_no_first_signal_date(self):
        rows = [daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0)]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertIsNone(episodes[0]["first_signal_date"])

    def test_data_failure_episode_has_no_first_signal_date_or_action_eligible_date(self):
        rows = [daily_row("X", "2026-08-13", "DATA_FAILURE", 5.0)]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertIsNone(episodes[0]["first_signal_date"])
        self.assertIsNone(episodes[0]["first_action_eligible_date"])

    def test_real_signal_episode_has_a_first_signal_date(self):
        rows = [daily_row("X", "2026-08-13", "GATE_BLOCK", 5.0, triggers=["PRICE_CONFIRMATION"])]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(episodes[0]["first_signal_date"], "2026-08-13")

    def test_representative_forward_return_pct_replaces_final_outcome_pct(self):
        rows = [daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.5)]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertIn("representative_forward_return_pct", episodes[0])
        self.assertNotIn("final_outcome_pct", episodes[0])
        self.assertEqual(episodes[0]["representative_forward_return_pct"], 5.5)

    def test_episode_start_and_end_date_fields_present(self):
        rows = [daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0, outcome_window_end="2026-08-14")]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(episodes[0]["episode_start_date"], "2026-08-13")
        self.assertEqual(episodes[0]["episode_end_date"], "2026-08-13")


class TriggerFamilyGroupingTests(unittest.TestCase):
    def test_no_signal_family_for_empty_triggers(self):
        self.assertEqual(trigger_family([]), NO_SIGNAL_FAMILY)

    def test_family_is_deterministic_and_order_independent(self):
        self.assertEqual(
            trigger_family(["FLOW_REVERSAL", "PRICE_CONFIRMATION"]),
            trigger_family(["PRICE_CONFIRMATION", "FLOW_REVERSAL"]),
        )

    def test_different_trigger_family_never_merges_even_same_subject_and_root_cause(self):
        rows = [
            daily_row("X", "2026-08-13", "ACTION_CONVERSION_FAILURE", 5.0,
                      triggers=["PRICE_CONFIRMATION"], outcome_window_end="2026-08-14"),
            daily_row("X", "2026-08-14", "ACTION_CONVERSION_FAILURE", 6.0,
                      triggers=["FLOW_REVERSAL"], outcome_window_end="2026-08-15"),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 2)


class WindowOverlapGroupingTests(unittest.TestCase):
    """CIO round-3 flaw 3: grouping must use real forward-window overlap,
    not a flat calendar-day gap."""

    def test_rows_whose_windows_overlap_merge_even_with_a_large_calendar_gap(self):
        # Row 1's own measurement window extends to 08-20; row 2's entry
        # falls inside that window despite a 6-calendar-day gap (> the old
        # flat 4-day rule) -- must still merge, because it's the SAME
        # underlying price action window, not a coincidence.
        rows = [
            daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0, outcome_window_end="2026-08-20"),
            daily_row("X", "2026-08-19", "SIGNAL_MISS", 6.0, outcome_window_end="2026-08-24"),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["daily_rows_deduped"], 2)

    def test_rows_whose_windows_do_not_overlap_split_even_within_the_old_4_day_gap(self):
        # Row 1's window ends 08-14; row 2 starts 08-16 -- only a 2-day
        # calendar gap (would have merged under the old flat rule) but the
        # windows genuinely do not overlap -- must split into two episodes.
        rows = [
            daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0, entry_date="2026-08-13",
                      outcome_window_end="2026-08-14"),
            daily_row("X", "2026-08-16", "SIGNAL_MISS", 6.0, entry_date="2026-08-16",
                      outcome_window_end="2026-08-17"),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 2)

    def test_missing_window_info_falls_back_to_the_conservative_flat_gap_rule(self):
        rows = [
            daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0, entry_date=None, outcome_window_end=None),
            daily_row("X", "2026-08-14", "SIGNAL_MISS", 6.0, entry_date=None, outcome_window_end=None),
        ]
        for r in rows:
            r["entry_date"] = None
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)  # 1-day gap <= FALLBACK_GAP_DAYS

    def test_different_subject_never_merges(self):
        rows = [
            daily_row("A", "2026-08-13", "SIGNAL_MISS", 5.0, outcome_window_end="2026-08-20"),
            daily_row("B", "2026-08-14", "SIGNAL_MISS", 6.0, outcome_window_end="2026-08-20"),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 2)

    def test_different_root_cause_never_merges_into_one_episode(self):
        rows = [
            daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0, outcome_window_end="2026-08-20"),
            daily_row("X", "2026-08-14", "GATE_BLOCK", 6.0, triggers=["PRICE_CONFIRMATION"],
                      outcome_window_end="2026-08-20"),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 2)

    def test_episode_id_is_deterministic(self):
        rows = [daily_row("X", "2026-08-13", "SIGNAL_MISS", 5.0)]
        e1 = group_into_episodes(rows, outcome_field="forward_return_pct")
        e2 = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(e1[0]["episode_id"], e2[0]["episode_id"])

    def test_grouping_key_is_never_the_size_or_sign_of_the_outcome(self):
        rows = [
            daily_row("X", "2026-08-13", "SIGNAL_MISS", 999.0, outcome_window_end="2026-08-20"),
            daily_row("X", "2026-08-14", "SIGNAL_MISS", -999.0, outcome_window_end="2026-08-21"),
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)

    def test_episode_count_is_far_smaller_than_raw_row_count_for_a_long_chained_rally(self):
        rows = [
            daily_row("X", f"2026-07-{d:02d}", "DATA_FAILURE", 1.0, outcome_window_end=f"2026-07-{d + 2:02d}")
            for d in range(22, 32)
        ]
        episodes = group_into_episodes(rows, outcome_field="forward_return_pct")
        self.assertEqual(len(episodes), 1)
        self.assertLess(len(episodes), len(rows))


if __name__ == "__main__":
    unittest.main()
