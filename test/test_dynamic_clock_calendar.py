#!/usr/bin/env python3
"""P8-12 clock policy config + market-calendar regression (item 5): policy
values live in config/dynamic_clock_policy.json (not hardcoded), are
labeled PROVISIONAL_CIO_MVP, and KOREA uses a business-day approximation
with an explicit calendar_confidence flag rather than a silently-confident
date."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.dynamic_clock import (  # noqa: E402
    CalendarConfidence, ClockEvent, add_business_days, add_review_days, build_episode_history,
    calendar_confidence_for, load_policy,
)


class PolicyFileShapeTests(unittest.TestCase):
    def test_policy_file_is_labeled_provisional_not_ratified(self):
        doc = load_policy()
        self.assertEqual(doc["approval_status"], "PROVISIONAL_CIO_MVP")
        self.assertNotEqual(doc["approval_status"], "RATIFIED")

    def test_policy_file_has_an_evidence_basis(self):
        doc = load_policy()
        self.assertTrue(doc["evidence_basis"])

    def test_policy_file_declares_all_three_markets(self):
        doc = load_policy()
        for market in ("BTC", "KOREA", "CRYPTO"):
            self.assertIn(market, doc["market_calendars"])

    def test_clock_module_does_not_hardcode_a_policy_dict(self):
        source = (ROOT / "clock" / "dynamic_clock.py").read_text(encoding="utf-8")
        self.assertNotIn('"cooldown_days": 1, "expiry_days": 2', source)


class CalendarConfidenceTests(unittest.TestCase):
    def test_btc_and_crypto_are_verified_24_7(self):
        self.assertEqual(calendar_confidence_for("BTC"), CalendarConfidence.VERIFIED_24_7)
        self.assertEqual(calendar_confidence_for("CRYPTO"), CalendarConfidence.VERIFIED_24_7)

    def test_korea_is_unverified_no_holiday_calendar(self):
        self.assertEqual(calendar_confidence_for("KOREA"), CalendarConfidence.UNVERIFIED_NO_HOLIDAY_CALENDAR)

    def test_unknown_market_fails_closed(self):
        from clock.dynamic_clock import DynamicClockError
        with self.assertRaisesRegex(DynamicClockError, "NO_MARKET_CALENDAR_FOR_MARKET"):
            calendar_confidence_for("MARS")


class BusinessDayArithmeticTests(unittest.TestCase):
    def test_adding_one_business_day_over_a_weekday_is_the_next_day(self):
        # 2026-08-20 is a Thursday.
        self.assertEqual(add_business_days("2026-08-20", 1), "2026-08-21")

    def test_adding_business_days_skips_the_weekend(self):
        # 2026-08-21 is a Friday -- 1 business day later must skip Sat/Sun.
        self.assertEqual(add_business_days("2026-08-21", 1), "2026-08-24")

    def test_starting_on_a_weekend_still_skips_forward_to_a_weekday(self):
        # 2026-08-22 is a Saturday.
        self.assertEqual(add_business_days("2026-08-22", 1), "2026-08-24")


class MarketAwareReviewDatesTests(unittest.TestCase):
    def test_korea_review_dates_carry_the_unverified_flag(self):
        date, confidence = add_review_days("2026-08-21", 1, "KOREA")
        self.assertEqual(date, "2026-08-24")  # Friday + 1 business day -> Monday
        self.assertEqual(confidence, CalendarConfidence.UNVERIFIED_NO_HOLIDAY_CALENDAR)

    def test_btc_review_dates_carry_the_verified_flag(self):
        date, confidence = add_review_days("2026-08-21", 1, "BTC")
        self.assertEqual(date, "2026-08-22")  # plain calendar day, weekends included
        self.assertEqual(confidence, CalendarConfidence.VERIFIED_24_7)

    def test_episode_history_stamps_korea_episodes_with_the_uncertainty_flag(self):
        ev = ClockEvent(detected_at="2026-08-21", evidence_available_at="2026-08-21",
                         evidence_hash="a" * 64, source="src", strength=0.5)
        episodes = build_episode_history("005930", "KOREA", "PRICE_CONFIRMATION", [ev])
        ep = episodes[0]
        self.assertEqual(ep["next_review_at_calendar_confidence"], CalendarConfidence.UNVERIFIED_NO_HOLIDAY_CALENDAR)
        self.assertEqual(ep["expiry_calendar_confidence"], CalendarConfidence.UNVERIFIED_NO_HOLIDAY_CALENDAR)
        # Friday 08-21 + 1 business day -> Monday 08-24
        self.assertEqual(ep["next_review_at"], "2026-08-24")

    def test_episode_history_stamps_btc_episodes_with_the_verified_flag(self):
        ev = ClockEvent(detected_at="2026-08-21", evidence_available_at="2026-08-21",
                         evidence_hash="a" * 64, source="src", strength=0.5)
        episodes = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [ev])
        ep = episodes[0]
        self.assertEqual(ep["next_review_at_calendar_confidence"], CalendarConfidence.VERIFIED_24_7)
        self.assertEqual(ep["next_review_at"], "2026-08-22")


if __name__ == "__main__":
    unittest.main()
