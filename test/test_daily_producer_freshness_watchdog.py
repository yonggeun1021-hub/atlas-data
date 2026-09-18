#!/usr/bin/env python3
"""Offline fixture regression for watchdog/daily_producer_freshness.py.

No network, no secrets, no committed writes. Covers the four required
behaviours from the build brief:
  1. a fresh set produces no stale items (no issue would be opened);
  2. a stale one is detected;
  3. a holiday/weekend does not false-alarm;
  4. an output that has NEVER existed is reported differently from one
     that existed and went stale.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "watchdog" / "daily_producer_freshness.py"
SPEC = importlib.util.spec_from_file_location("daily_producer_freshness_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class CalendarPolicyTest(unittest.TestCase):
    """Weekend / KR-holiday awareness, replayed against the repo's own
    already-committed sources -- never date.weekday() guessing alone."""

    def test_weekday_set_skips_weekend_without_alarm(self):
        # Friday 2026-09-18 -> Monday 2026-09-21: a Mon-Fri-only producer
        # must not count Saturday (09-19) or Sunday (09-20) as missed
        # cycles -- only Monday itself (the current expected day) shows up,
        # and one cycle of grace (allowed_missed_cycles=1) keeps that FRESH.
        calendar = MODULE._weekday_set("MON", "TUE", "WED", "THU", "FRI")
        friday = dt.date(2026, 9, 18)
        saturday = dt.date(2026, 9, 19)
        sunday = dt.date(2026, 9, 20)
        monday = dt.date(2026, 9, 21)
        self.assertEqual(friday.weekday(), 4)
        self.assertEqual(monday.weekday(), 0)
        missed = MODULE.missed_expected_days(calendar, friday, monday)
        self.assertEqual(missed, [monday])
        self.assertNotIn(saturday, missed)
        self.assertNotIn(sunday, missed)

    def test_weekday_set_still_flags_a_missed_weekday(self):
        calendar = MODULE._weekday_set("MON", "TUE", "WED", "THU", "FRI")
        monday = dt.date(2026, 9, 21)
        wednesday = dt.date(2026, 9, 23)
        missed = MODULE.missed_expected_days(calendar, monday, wednesday)
        self.assertEqual(missed, [dt.date(2026, 9, 22), dt.date(2026, 9, 23)])

    def test_kr_official_holiday_capture_marks_chuseok_closed(self):
        # 2026-09-12/13 are officially listed KRX closures in the committed
        # capture -- not inferred from Saturday/Sunday arithmetic (they are
        # in fact a Sat/Sun here, but the point of this module is that the
        # *official* capture, not the weekday, is what is consulted).
        self.assertEqual(MODULE.kr_trading_day_status(dt.date(2026, 9, 11)), "OPEN_REGULAR")
        self.assertEqual(MODULE.kr_trading_day_status(dt.date(2026, 9, 12)), "CLOSED")
        self.assertEqual(MODULE.kr_trading_day_status(dt.date(2026, 9, 13)), "CLOSED")
        self.assertEqual(MODULE.kr_trading_day_status(dt.date(2026, 9, 14)), "OPEN_REGULAR")

    def test_kr_holiday_gap_does_not_false_alarm(self):
        # Last observation right before the holiday cluster; checked on the
        # very next trading day. One-cycle grace absorbs same-day timing.
        missed = MODULE.missed_expected_days(
            MODULE.KR_TRADING_DAY, dt.date(2026, 9, 11), dt.date(2026, 9, 14)
        )
        self.assertEqual(missed, [dt.date(2026, 9, 14)])
        self.assertLessEqual(len(missed), 1)

    def test_kr_holiday_gap_still_flags_genuine_multi_day_stall(self):
        missed = MODULE.missed_expected_days(
            MODULE.KR_TRADING_DAY, dt.date(2026, 9, 11), dt.date(2026, 9, 16)
        )
        self.assertGreater(len(missed), 1)

    def test_out_of_capture_year_is_unknown_not_guessed(self):
        # The committed capture is bound to year 2026 (see build_calendar_packet's
        # SESSION_YEAR_MISMATCH). A date outside it must fail closed to
        # UNKNOWN, never fall back to weekday arithmetic.
        self.assertEqual(MODULE.kr_trading_day_status(dt.date(2027, 1, 4)), "UNKNOWN")


class FreshStaleFixtureTest(unittest.TestCase):
    """Synthetic FILE/GLOB items in an isolated temp root -- no real repo
    data is read for these, only the calendar helper (which needs no
    per-test fixture: it replays the repo's own committed capture)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.today = dt.date(2026, 9, 18)  # a Friday

    def test_fresh_output_produces_no_stale_item(self):
        spec = {
            "id": "widget_fresh", "label_ko": "테스트", "kind": "FILE",
            "path": "data/latest_widget.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
        }
        _write_json(self.root / spec["path"], {"as_of_date": self.today.isoformat()})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertTrue(report["all_fresh"])
        self.assertEqual(report["stale_items"], [])
        self.assertEqual(report["items"][0]["status"], "FRESH")

    def test_stale_output_is_detected(self):
        spec = {
            "id": "widget_stale", "label_ko": "테스트", "kind": "FILE",
            "path": "data/latest_widget.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
        }
        old = (self.today - dt.timedelta(days=10)).isoformat()
        _write_json(self.root / spec["path"], {"as_of_date": old})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertFalse(report["all_fresh"])
        self.assertEqual(report["items"][0]["status"], "STALE")
        self.assertEqual(report["items"][0]["missed_expected_cycles"], 10)

    def test_never_existed_is_reported_differently_from_went_stale(self):
        missing_spec = {
            "id": "widget_missing", "label_ko": "없음", "kind": "FILE",
            "path": "data/latest_never_written.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
        }
        stale_spec = {
            "id": "widget_went_stale", "label_ko": "정체", "kind": "FILE",
            "path": "data/latest_went_stale.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
        }
        old = (self.today - dt.timedelta(days=10)).isoformat()
        _write_json(self.root / stale_spec["path"], {"as_of_date": old})

        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[missing_spec, stale_spec])
        by_id = {item["id"]: item for item in report["items"]}

        self.assertEqual(by_id["widget_missing"]["status"], "NEVER_PRODUCED")
        self.assertIsNone(by_id["widget_missing"]["last_date"])
        self.assertNotIn("age_days", by_id["widget_missing"])

        self.assertEqual(by_id["widget_went_stale"]["status"], "STALE")
        self.assertEqual(by_id["widget_went_stale"]["last_date"], old)
        self.assertIn("age_days", by_id["widget_went_stale"])

        # Both are non-fresh, but under visibly different statuses.
        self.assertNotEqual(by_id["widget_missing"]["status"], by_id["widget_went_stale"]["status"])
        self.assertEqual({item["id"] for item in report["stale_items"]}, {"widget_missing", "widget_went_stale"})

    def test_weekend_only_producer_not_alarmed_on_monday_morning(self):
        # The literal example from the brief: a Mon-Fri producer whose last
        # output is Friday must not alarm on Monday for "not running Sunday".
        spec = {
            "id": "us_weekday_widget", "label_ko": "테스트", "kind": "FILE",
            "path": "data/latest_widget.json", "date_fields": ["as_of_date"],
            "calendar": MODULE._weekday_set("MON", "TUE", "WED", "THU", "FRI"),
            "allowed_missed_cycles": 1,
        }
        friday = dt.date(2026, 9, 18)
        monday = dt.date(2026, 9, 21)
        _write_json(self.root / spec["path"], {"as_of_date": friday.isoformat()})
        report = MODULE.build_report(root=self.root, today=monday, watchlist=[spec])
        self.assertTrue(report["all_fresh"])
        self.assertEqual(report["items"][0]["status"], "FRESH")

    def test_glob_never_matched_is_never_produced(self):
        spec = {
            "id": "obs_never", "label_ko": "관측", "kind": "GLOB",
            "glob": "data/observations/never_ran/*/summary.json",
            "calendar": MODULE.NO_SCHEDULE, "default_max_gap_days": 4,
        }
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertEqual(report["items"][0]["status"], "NEVER_PRODUCED")

    def test_glob_matched_but_old_is_stale_not_never_produced(self):
        spec = {
            "id": "obs_stale", "label_ko": "관측", "kind": "GLOB",
            "glob": "data/observations/ran_once/*/summary.json",
            "calendar": MODULE.NO_SCHEDULE, "default_max_gap_days": 4,
        }
        old_dir = self.today - dt.timedelta(days=8)
        _write_json(self.root / "data/observations/ran_once" / old_dir.isoformat() / "summary.json", {})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertEqual(report["items"][0]["status"], "NO_SCHEDULE_STALE")
        self.assertEqual(report["items"][0]["last_date"], old_dir.isoformat())

    def test_no_schedule_wording_differs_from_calendar_stale(self):
        fresh_spec = {
            "id": "dispatch_only_fresh", "label_ko": "테스트", "kind": "FILE",
            "path": "data/latest_dispatch.json", "date_fields": ["generated_at_utc"],
            "calendar": MODULE.NO_SCHEDULE, "default_max_gap_days": 10,
        }
        _write_json(self.root / fresh_spec["path"], {"generated_at_utc": (self.today - dt.timedelta(days=3)).isoformat() + "T00:00:00Z"})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[fresh_spec])
        self.assertEqual(report["items"][0]["status"], "NO_SCHEDULE_FRESH")
        self.assertNotEqual(report["items"][0]["status"], "FRESH")

    def test_self_declared_gap_field_overrides_default(self):
        spec = {
            "id": "rotation_like", "label_ko": "테스트", "kind": "FILE",
            "path": "data/latest_rotation_like.json", "date_fields": ["as_of_date"],
            "gap_field": "maximum_observation_gap_days", "default_max_gap_days": 1,
            "calendar": MODULE.EVERY_DAY,
        }
        # 5 days old, but the artifact itself declares a 7-day tolerance --
        # must win over the module's own default_max_gap_days=1.
        old = (self.today - dt.timedelta(days=5)).isoformat()
        _write_json(self.root / spec["path"], {"as_of_date": old, "maximum_observation_gap_days": 7})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertEqual(report["items"][0]["status"], "FRESH")
        self.assertEqual(report["items"][0]["threshold_days"], 7)

    def test_date_field_missing_is_reported_not_crashed(self):
        spec = {
            "id": "widget_no_date", "label_ko": "테스트", "kind": "FILE",
            "path": "data/latest_widget.json", "date_fields": ["nonexistent_field"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
        }
        _write_json(self.root / spec["path"], {"unrelated": True})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertEqual(report["items"][0]["status"], "DATE_FIELD_MISSING")
        self.assertFalse(report["all_fresh"])

    def test_render_issue_body_says_all_fresh_when_nothing_stale(self):
        spec = {
            "id": "widget_fresh", "label_ko": "테스트", "kind": "FILE",
            "path": "data/latest_widget.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
        }
        _write_json(self.root / spec["path"], {"as_of_date": self.today.isoformat()})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        body = MODULE.render_issue_body(report)
        self.assertIn("모든 감시 대상이 최신입니다", body)

    def test_render_issue_body_lists_each_stale_item_once(self):
        spec = {
            "id": "widget_stale", "label_ko": "테스트-라벨", "kind": "FILE",
            "path": "data/latest_widget.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
        }
        old = (self.today - dt.timedelta(days=10)).isoformat()
        _write_json(self.root / spec["path"], {"as_of_date": old})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        body = MODULE.render_issue_body(report)
        self.assertEqual(body.count("widget_stale"), 1)
        self.assertIn("테스트-라벨", body)


class AuthorityAndSchemaTest(unittest.TestCase):
    def test_authority_block_is_all_false_except_read_only(self):
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18), watchlist=[])
        authority = report["authority"]
        self.assertTrue(authority["read_only_watch"])
        for key, value in authority.items():
            if key == "read_only_watch":
                continue
            self.assertIs(value, False, key)

    def test_default_watchlist_ids_are_unique(self):
        ids = [spec["id"] for spec in MODULE.default_watchlist()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_default_watchlist_covers_the_required_outputs(self):
        specs = {spec["id"]: spec for spec in MODULE.default_watchlist()}
        required_paths = {
            "data/latest_free_market_data.json",
            "data/latest_rotation_confirmation_kr.json",
            "data/latest_rotation_confirmation_us.json",
            "data/latest_rotation_confirmation_crypto.json",
            "data/latest_kr_paper_runtime_decision.json",
            "data/latest_us_paper_runtime_decision.json",
            "data/latest_spdr_sector_holdings.json",
            "data/latest_us_sip_daily_liquidity.json",
        }
        observed_paths = {spec["path"] for spec in specs.values() if spec["kind"] == "FILE"}
        self.assertTrue(required_paths.issubset(observed_paths), required_paths - observed_paths)
        self.assertIn("crypto_leadership", specs)
        self.assertIn("korea_population_symbol_observation", specs)
        self.assertIn("us_population_symbol_observation", specs)


class RealRepoRegressionTest(unittest.TestCase):
    """Replays the watchdog against this repo's own already-committed
    evidence as of a fixed historical decision date -- the exact silent
    failures the build brief describes are still on disk and must still be
    caught. Fixed date keeps this deterministic forever (no real network,
    no clock dependency)."""

    def test_known_2026_09_18_incidents_are_flagged(self):
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        stale_ids = {item["id"] for item in report["stale_items"]}
        self.assertIn("free_market_data", stale_ids)
        self.assertIn("kr_paper_runtime_decision", stale_ids)
        self.assertIn("korea_population_symbol_observation", stale_ids)
        self.assertIn("us_population_symbol_observation", stale_ids)
        self.assertFalse(report["all_fresh"])

    def test_report_is_json_serializable(self):
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        json.dumps(report)  # must not raise


if __name__ == "__main__":
    unittest.main()
