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

import contextlib
import datetime as dt
import importlib.util
import io
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


class SourceSideAxisTest(unittest.TestCase):
    """The distinction age alone cannot make: "the upstream source published
    nothing newer" (nothing is wrong) vs "our run looked fine but dropped what
    the source did publish" (a real incident). Every test here fails against
    the schedule-only classifier that preceded it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.today = dt.date(2026, 9, 18)  # a Friday

    # -- the exact FX case that produced the false incident report -----------

    def _fx_like_spec(self):
        """A shape-for-shape copy of the real fred_dexkous_fx entry: dated
        append-only observations, plus raw captures in a separate path whose
        manifest declares the span the API actually served."""
        return {
            "id": "fx_like", "label_ko": "환율 관측", "kind": "GLOB",
            "glob": "evidence/fx/observations/*/*.captured.json",
            "calendar": MODULE.US_MON_SAT_KST,
            "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_DATED_PATH_FIELD,
                "glob": "evidence/fx/raw/*/*/manifest.json",
                "field": "observation_date_range.1",
                "field_note": "observation_date_range end in the newest raw manifest",
            },
        }

    def _write_fx(self, our_latest: dt.date, capture_day: dt.date, source_latest: dt.date):
        _write_json(self.root / "evidence/fx/observations" / our_latest.isoformat() / "abc123.captured.json", {})
        _write_json(
            self.root / "evidence/fx/raw" / capture_day.isoformat() / "revision0" / "manifest.json",
            {"observation_date_range": ["2026-08-18", source_latest.isoformat()], "series_id": "DEXKOUS"},
        )

    def test_fx_our_latest_equals_source_latest_is_not_an_alarm(self):
        # The real 2026-09-18 case: our newest observation is 2026-09-11 and the
        # collector's own 2026-09-17 raw capture says FRED served nothing after
        # 2026-09-11. Six expected production days have passed, so the age-only
        # classifier called this STALE and a three-day FX observation loss was
        # reported that had never happened.
        self._write_fx(
            our_latest=dt.date(2026, 9, 11),
            capture_day=dt.date(2026, 9, 17),
            source_latest=dt.date(2026, 9, 11),
        )
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[self._fx_like_spec()])
        item = report["items"][0]

        self.assertEqual(item["status"], "SOURCE_NOT_YET_PUBLISHED")
        # The schedule axis still honestly reports the age gap underneath.
        self.assertEqual(item["schedule_status"], "STALE")
        # Both dates are recorded and shown, as required.
        self.assertEqual(item["last_date"], "2026-09-11")
        self.assertEqual(item["source_latest"], "2026-09-11")
        self.assertEqual(item["source_claim_date"], "2026-09-17")
        # Informational, not an alarm: no issue would be opened for this.
        self.assertEqual([i["id"] for i in report["informational_items"]], ["fx_like"])
        self.assertEqual(report["alarm_items"], [])
        self.assertTrue(report["all_fresh"])
        body = MODULE.render_issue_body(report)
        self.assertIn("2026-09-11", body)
        self.assertIn("경보 대상 없음", body)

    def test_fx_source_ahead_of_us_is_the_loudest_alarm(self):
        # The inverse of the same case: FRED has served through 2026-09-17 but
        # our newest observation is still 2026-09-11. Identical age to the test
        # above -- only the source-side fact differs -- and this one IS an
        # incident, because a run that looked successful dropped data.
        self._write_fx(
            our_latest=dt.date(2026, 9, 11),
            capture_day=dt.date(2026, 9, 17),
            source_latest=dt.date(2026, 9, 17),
        )
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[self._fx_like_spec()])
        item = report["items"][0]

        self.assertEqual(item["status"], "COLLECTION_BEHIND_SOURCE")
        self.assertEqual(item["last_date"], "2026-09-11")
        self.assertEqual(item["source_latest"], "2026-09-17")
        self.assertGreater(item["source_lag_cycles"], item["source_lag_allowance"])
        self.assertEqual([i["id"] for i in report["alarm_items"]], ["fx_like"])
        self.assertFalse(report["all_fresh"])
        body = MODULE.render_issue_body(report)
        self.assertIn("경보", body)
        self.assertIn("2026-09-17", body)

    def test_two_identical_ages_split_on_the_source_fact_alone(self):
        # Same producer shape, same last_date, same today -- the only
        # difference is what the source says. The whole point of the change.
        quiet_root = self.root / "quiet"
        behind_root = self.root / "behind"
        for target, source_latest in ((quiet_root, dt.date(2026, 9, 11)), (behind_root, dt.date(2026, 9, 17))):
            _write_json(target / "evidence/fx/observations/2026-09-11/abc.captured.json", {})
            _write_json(
                target / "evidence/fx/raw/2026-09-17/revision0/manifest.json",
                {"observation_date_range": ["2026-08-18", source_latest.isoformat()]},
            )
        spec = self._fx_like_spec()
        quiet = MODULE.build_report(root=quiet_root, today=self.today, watchlist=[spec])["items"][0]
        behind = MODULE.build_report(root=behind_root, today=self.today, watchlist=[spec])["items"][0]

        self.assertEqual(quiet["last_date"], behind["last_date"])
        self.assertEqual(quiet["schedule_status"], behind["schedule_status"])
        self.assertNotEqual(quiet["status"], behind["status"])
        self.assertEqual(quiet["status"], "SOURCE_NOT_YET_PUBLISHED")
        self.assertEqual(behind["status"], "COLLECTION_BEHIND_SOURCE")

    # -- COLLECTION_BEHIND_SOURCE must fire even when the calendar says FRESH -

    def test_collection_behind_source_fires_even_when_schedule_is_fresh(self):
        # A producer that ran today (so no age gap at all) but committed a much
        # older observation than the upstream it reads. This is the failure the
        # age axis structurally cannot see.
        spec = {
            "id": "downstream", "label_ko": "하류 산출물", "kind": "FILE",
            "path": "data/latest_downstream.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_DATED_PATH,
                "glob": "data/observations/upstream/*/packet.json",
                "field_note": "newest committed upstream observation directory",
            },
        }
        _write_json(self.root / spec["path"], {"as_of_date": self.today.isoformat()})
        for day in ("2026-09-16", "2026-09-17", "2026-09-18"):
            _write_json(self.root / "data/observations/upstream" / day / "packet.json", {})
        # Our artifact's own date is today, so the schedule axis is FRESH...
        fresh = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])["items"][0]
        self.assertEqual(fresh["schedule_status"], "FRESH")
        self.assertEqual(fresh["status"], "FRESH")

        # ...but if what we actually hold lags the upstream, it is an alarm.
        spec = {**spec, "source_compare_fields": ["consumed_observation_date"]}
        _write_json(
            self.root / spec["path"],
            {"as_of_date": self.today.isoformat(), "consumed_observation_date": "2026-09-11"},
        )
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        item = report["items"][0]
        self.assertEqual(item["schedule_status"], "FRESH")
        self.assertEqual(item["status"], "COLLECTION_BEHIND_SOURCE")
        self.assertEqual(item["our_compared_date"], "2026-09-11")
        self.assertEqual(item["source_latest"], "2026-09-18")
        self.assertFalse(report["all_fresh"])

    def test_source_one_cycle_ahead_is_normal_pipelining_not_an_alarm(self):
        # Upstream routinely lands before the producer that reads it within the
        # same cycle; that must not alarm, or the loud state becomes noise.
        spec = {
            "id": "downstream", "label_ko": "하류 산출물", "kind": "FILE",
            "path": "data/latest_downstream.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_DATED_PATH,
                "glob": "data/observations/upstream/*/packet.json",
                "field_note": "newest committed upstream observation directory",
            },
        }
        _write_json(self.root / spec["path"], {"as_of_date": "2026-09-17"})
        _write_json(self.root / "data/observations/upstream/2026-09-18/packet.json", {})
        item = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])["items"][0]
        self.assertEqual(item["source_lag_cycles"], 1)
        self.assertEqual(item["status"], "FRESH")

    # -- SOURCE_LATEST_UNKNOWN: its own state, neither "fine" nor "stale" ----

    def test_no_committed_source_latest_is_its_own_explicit_state(self):
        spec = {
            "id": "blind", "label_ko": "원천 미확보 산출물", "kind": "FILE",
            "path": "data/latest_blind.json", "date_fields": ["evaluation_at"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_UNAVAILABLE,
                "field_note": None,
                "resolve_by": "commit the source manifest's newest served session",
            },
        }
        _write_json(self.root / spec["path"], {"evaluation_at": "2026-09-13T00:00:00Z"})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        item = report["items"][0]

        self.assertEqual(item["status"], "SOURCE_LATEST_UNKNOWN")
        self.assertEqual(item["source_latest_status"], "UNAVAILABLE")
        self.assertIsNone(item["source_latest"])
        # Not folded into "fine"...
        self.assertNotEqual(item["status"], "FRESH")
        self.assertFalse(report["all_fresh"])
        # ...and not folded into "stale" either.
        self.assertNotEqual(item["status"], "STALE")
        self.assertEqual(report["alarm_items"], [])
        self.assertEqual([i["id"] for i in report["unknown_items"]], ["blind"])
        # No value is invented to fill the gap; what would resolve it is stated.
        self.assertEqual(
            [spot["id"] for spot in report["source_latest_blind_spots"]], ["blind"]
        )
        self.assertIn("commit the source manifest", MODULE.render_issue_body(report))

    def test_blind_spot_is_listed_even_while_the_producer_is_fresh(self):
        # The structural inability to tell "quiet" from "broken" is a standing
        # property of the producer, so it is reported whether or not today
        # happens to be fine -- never silently absorbed into "all fresh".
        spec = {
            "id": "blind_but_fresh", "label_ko": "원천 미확보 산출물", "kind": "FILE",
            "path": "data/latest_blind.json", "date_fields": ["evaluation_at"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_UNAVAILABLE, "field_note": None,
                "resolve_by": "record the provider's newest available session",
            },
        }
        _write_json(self.root / spec["path"], {"evaluation_at": self.today.isoformat() + "T00:00:00Z"})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertEqual(report["items"][0]["status"], "FRESH")
        self.assertTrue(report["all_fresh"])
        self.assertEqual(
            [spot["id"] for spot in report["source_latest_blind_spots"]], ["blind_but_fresh"]
        )
        self.assertIn("record the provider's newest available session", MODULE.render_issue_body(report))

    def test_co_frozen_source_claim_cannot_self_certify_as_quiet(self):
        # The free_market_data trap. Its source-side date (the newest daily bar
        # the provider returned) lives inside the very artifact that stopped
        # updating, so "our latest == source latest" is true by construction for
        # exactly as long as the producer stays broken. A claim whose own
        # evidence is stalled must never clear the alarm.
        spec = {
            "id": "self_claim", "label_ko": "자체 주장 산출물", "kind": "FILE",
            "path": "data/latest_self.json",
            "date_fields": ["observed_at_utc"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_SELF_FIELD,
                "field": "provider.bars[].session_date",
                "claim_date_field": "observed_at_utc",
                "field_note": "max provider.bars[].session_date inside the artifact itself",
            },
        }
        _write_json(
            self.root / spec["path"],
            {
                "observed_at_utc": "2026-09-15T23:39:00Z",
                "provider": {"bars": [{"session_date": "2026-09-14"}, {"session_date": "2026-09-15"}]},
            },
        )
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        item = report["items"][0]

        self.assertEqual(item["source_latest"], "2026-09-15")  # equal to ours...
        self.assertEqual(item["last_date"], "2026-09-15")
        # ...but NOT reported as "source is quiet", because the claim is stale.
        self.assertEqual(item["source_latest_status"], "CLAIM_NOT_CURRENT")
        self.assertEqual(item["status"], "SOURCE_LATEST_UNKNOWN")
        self.assertNotEqual(item["status"], "SOURCE_NOT_YET_PUBLISHED")
        self.assertFalse(report["all_fresh"])

    def test_self_claim_clears_the_gap_once_the_producer_runs_again(self):
        # Same locator, same "our latest == source latest", but the artifact was
        # written today -- so the claim genuinely speaks for now and the quiet
        # source is correctly reported as informational rather than unknown.
        spec = {
            "id": "self_claim", "label_ko": "자체 주장 산출물", "kind": "FILE",
            "path": "data/latest_self.json",
            "date_fields": ["source_session_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_SELF_FIELD,
                "field": "provider.bars[].session_date",
                "claim_date_field": "observed_at_utc",
                "field_note": "max provider.bars[].session_date inside the artifact itself",
            },
        }
        _write_json(
            self.root / spec["path"],
            {
                "source_session_date": "2026-09-11",
                "observed_at_utc": self.today.isoformat() + "T23:39:00Z",
                "provider": {"bars": [{"session_date": "2026-09-11"}]},
            },
        )
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        item = report["items"][0]
        self.assertEqual(item["source_latest_status"], "RESOLVED")
        self.assertEqual(item["schedule_status"], "STALE")
        self.assertEqual(item["status"], "SOURCE_NOT_YET_PUBLISHED")
        self.assertTrue(report["all_fresh"])

    def test_stalled_claim_can_still_raise_but_never_clear(self):
        # Asymmetry check: a stalled claim is still proof the source once
        # offered that date, so it may RAISE the loud alarm even though it is
        # not allowed to clear one.
        spec = {
            "id": "self_claim", "label_ko": "자체 주장 산출물", "kind": "FILE",
            "path": "data/latest_self.json", "date_fields": ["observed_at_utc"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_SELF_FIELD,
                "field": "provider.bars[].session_date",
                "claim_date_field": "observed_at_utc",
                "field_note": "max provider.bars[].session_date inside the artifact itself",
            },
        }
        _write_json(
            self.root / spec["path"],
            {
                "observed_at_utc": "2026-09-11T23:39:00Z",
                "provider": {"bars": [{"session_date": "2026-09-16"}]},
            },
        )
        item = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])["items"][0]
        self.assertEqual(item["source_latest_status"], "CLAIM_NOT_CURRENT")
        self.assertEqual(item["status"], "COLLECTION_BEHIND_SOURCE")

    def test_declared_source_evidence_that_never_existed_is_unknown_not_fine(self):
        spec = {
            "id": "missing_source", "label_ko": "원천 증거 없음", "kind": "FILE",
            "path": "data/latest_widget.json", "date_fields": ["as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "source_latest": {
                "kind": MODULE.SOURCE_DATED_PATH,
                "glob": "data/observations/upstream_never_ran/*/packet.json",
                "field_note": "newest committed upstream observation directory",
            },
        }
        _write_json(self.root / spec["path"], {"as_of_date": "2026-09-08"})
        item = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])["items"][0]
        self.assertEqual(item["source_latest_status"], "NO_SOURCE_EVIDENCE")
        self.assertEqual(item["status"], "SOURCE_LATEST_UNKNOWN")

    # -- the kept states are still reachable and still distinct -------------

    def test_never_produced_survives_the_source_axis(self):
        spec = {
            "id": "never", "label_ko": "미생성", "kind": "GLOB",
            "glob": "data/observations/never/*/summary.json",
            "calendar": MODULE.NO_SCHEDULE, "default_max_gap_days": 4,
            "source_latest": {
                "kind": MODULE.SOURCE_DATED_PATH,
                "glob": "data/observations/upstream/*/packet.json",
                "field_note": "newest committed upstream observation directory",
            },
        }
        _write_json(self.root / "data/observations/upstream/2026-09-18/packet.json", {})
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        self.assertEqual(report["items"][0]["status"], "NEVER_PRODUCED")
        self.assertIn("never", {item["id"] for item in report["alarm_items"]})

    def test_no_schedule_stale_survives_when_the_source_is_also_behind(self):
        # A dispatch-only producer whose upstream has not advanced either: the
        # gap is real and unexplained, so NO_SCHEDULE_STALE remains visible on
        # the schedule axis rather than being overwritten and lost.
        spec = {
            "id": "dispatch_only", "label_ko": "수동 실행 산출물", "kind": "GLOB",
            "glob": "data/observations/dispatch/*/summary.json",
            "calendar": MODULE.NO_SCHEDULE, "default_max_gap_days": 4,
            "source_latest": {
                "kind": MODULE.SOURCE_DATED_PATH,
                "glob": "data/observations/upstream/*/packet.json",
                "field_note": "newest committed upstream observation directory",
            },
        }
        _write_json(self.root / "data/observations/dispatch/2026-09-08/summary.json", {})
        _write_json(self.root / "data/observations/upstream/2026-09-08/packet.json", {})
        item = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])["items"][0]
        self.assertEqual(item["schedule_status"], "NO_SCHEDULE_STALE")
        # The upstream claim is itself 10 days stale, so it cannot clear this.
        self.assertEqual(item["source_latest_status"], "CLAIM_NOT_CURRENT")
        self.assertEqual(item["status"], "SOURCE_LATEST_UNKNOWN")


class SourceLatestFieldResolutionTest(unittest.TestCase):
    """The path/field readers, isolated -- these are what bind each producer to
    a real committed field rather than to a guessed one."""

    def test_date_is_read_from_whichever_path_depth_carries_it(self):
        # data/observations/<producer>/<date>/packet.json and the
        # content-addressed evidence/<producer>/raw/<date>/<sha>/manifest.json
        # layouts must both resolve to the same date.
        self.assertEqual(
            MODULE._path_date(("data", "observations", "x", "2026-09-16", "packet.json")),
            dt.date(2026, 9, 16),
        )
        self.assertEqual(
            MODULE._path_date(("evidence", "x", "raw", "2026-09-17", "deadbeef", "manifest.json")),
            dt.date(2026, 9, 17),
        )
        self.assertIsNone(MODULE._path_date(("evidence", "x", "raw", "manifest.json")))

    def test_list_index_and_list_fan_out_paths(self):
        payload = {
            "observation_date_range": ["2026-08-18", "2026-09-11"],
            "raw": {"ohlc": [
                {"latest_finalized_day": "2026-09-16"},
                {"latest_finalized_day": "2026-09-17"},
                {"other": 1},
            ]},
        }
        self.assertEqual(MODULE._max_date_at(payload, "observation_date_range.1"), dt.date(2026, 9, 11))
        # Fan-out takes the max across every element, not just the first.
        self.assertEqual(MODULE._max_date_at(payload, "raw.ohlc[].latest_finalized_day"), dt.date(2026, 9, 17))
        self.assertIsNone(MODULE._max_date_at(payload, "raw.ohlc[].absent_field"))
        self.assertIsNone(MODULE._max_date_at(payload, "nope.at.all"))


class RealRepoSourceAxisTest(unittest.TestCase):
    """The change's whole reason to exist, checked against this repo's own
    already-committed evidence at a fixed KST date."""

    # NOTE on why these are fixtures and not live reads. These four incidents
    # were originally asserted against whatever data/ and evidence/ happened to
    # hold, with only the *decision date* pinned. That is not deterministic:
    # the moment main advanced (free-market-data.yml recovered, the crypto
    # classification moved on) the assertions flipped, even though the
    # classifier was unchanged. A regression test whose premise the repo can
    # retire is a test that will be deleted rather than believed. So each
    # incident is now frozen as a fixture reproducing the exact committed
    # shape and dates it had on 2026-09-18 -- the field names and values are
    # the real ones, and they stay true forever. Date-independent properties
    # (spec declarations, real-repo smoke) are still checked live, below.

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.today = dt.date(2026, 9, 18)

    def test_fx_false_incident_frozen_is_not_an_alarm(self):
        # evidence/fred_dexkous_fx ended at 2026-09-11 while the collector's
        # 2026-09-17 raw manifest declared observation_date_range ending
        # 2026-09-11 -- FRED's H.10 series had published nothing newer. Age
        # alone called this STALE and a three-day FX observation loss was
        # reported that had never happened.
        _write_json(self.root / "evidence/fred_dexkous_fx/observations/2026-09-11/dd79e0c3f725a336.captured.json", {})
        _write_json(
            self.root / "evidence/fred_dexkous_fx/raw/2026-09-17/37995b4354ff8796/manifest.json",
            {"series_id": "DEXKOUS", "source_kind": "FRED_API", "observation_count": 18,
             "observation_date_range": ["2026-08-18", "2026-09-11"]},
        )
        spec = next(s for s in MODULE.default_watchlist() if s["id"] == "fred_dexkous_fx")
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        item = report["items"][0]
        self.assertEqual(item["schedule_status"], "STALE")
        self.assertEqual(item["status"], "SOURCE_NOT_YET_PUBLISHED")
        self.assertEqual(item["last_date"], "2026-09-11")
        self.assertEqual(item["source_latest"], "2026-09-11")
        self.assertEqual(report["alarm_items"], [])
        self.assertIn("fred_dexkous_fx", {i["id"] for i in report["informational_items"]})

    def test_population_observations_frozen_are_behind_their_source(self):
        # Both summaries name their input in population.source.path; those
        # upstream universes had reached 2026-09-16 while the observations held
        # 2026-09-10 / 2026-09-11 -- data the source already offered that we
        # never took.
        for producer, ours, universe in (
            ("korea_population_symbol_observation", "2026-09-10", "krx_global_universe"),
            ("us_population_symbol_observation", "2026-09-11", "us_global_universe"),
        ):
            with self.subTest(producer=producer):
                root = self.root / producer
                _write_json(root / f"data/observations/{producer}/{ours}/summary.json", {})
                for day in ("2026-09-10", "2026-09-15", "2026-09-16"):
                    _write_json(root / f"data/observations/{universe}/{day}/packet.json", {})
                spec = next(s for s in MODULE.default_watchlist() if s["id"] == producer)
                report = MODULE.build_report(root=root, today=self.today, watchlist=[spec])
                item = report["items"][0]
                self.assertEqual(item["status"], "COLLECTION_BEHIND_SOURCE")
                self.assertEqual(item["last_date"], ours)
                self.assertEqual(item["source_latest"], "2026-09-16")
                self.assertEqual([i["id"] for i in report["alarm_items"]], [producer])

    def test_free_market_data_outage_frozen_is_not_silenced_by_its_own_claim(self):
        # free-market-data.yml failed 2026-09-16/09-17, so the provider-reported
        # newest bar inside the artifact was frozen at 2026-09-15 alongside our
        # own date. Equal dates must NOT read as "the source is quiet" here.
        _write_json(
            self.root / "data/latest_free_market_data.json",
            {"observed_at_utc": "2026-09-15T23:39:00Z",
             "us_market_reference": {"as_of_session_date": "2026-09-15"},
             "alpaca": {"daily_bars": [
                 {"symbol": "SPY", "opened_at": "2026-09-14T04:00:00Z"},
                 {"symbol": "SPY", "opened_at": "2026-09-15T04:00:00Z"}]}},
        )
        spec = next(s for s in MODULE.default_watchlist() if s["id"] == "free_market_data")
        report = MODULE.build_report(root=self.root, today=self.today, watchlist=[spec])
        item = report["items"][0]
        self.assertEqual(item["source_latest"], "2026-09-15")
        self.assertEqual(item["last_date"], "2026-09-15")
        self.assertEqual(item["source_latest_status"], "CLAIM_NOT_CURRENT")
        self.assertEqual(item["status"], "SOURCE_LATEST_UNKNOWN")
        self.assertNotEqual(item["status"], "SOURCE_NOT_YET_PUBLISHED")
        self.assertFalse(report["all_fresh"])

    def test_every_watched_producer_declares_its_source_latest_position(self):
        # No producer may quietly have no opinion: each entry either names the
        # committed field its source-side latest comes from, or declares the
        # blind spot explicitly with what would resolve it.
        for spec in MODULE.default_watchlist():
            with self.subTest(producer=spec["id"]):
                locator = spec.get("source_latest")
                self.assertIsNotNone(locator, f"{spec['id']} declares no source_latest")
                if locator["kind"] == MODULE.SOURCE_UNAVAILABLE:
                    self.assertTrue(locator["resolve_by"].strip())
                else:
                    self.assertTrue(locator["field_note"].strip())

    def test_real_repo_blind_spots_are_exactly_the_documented_ones(self):
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        self.assertEqual(
            sorted(spot["id"] for spot in report["source_latest_blind_spots"]),
            ["kr_paper_runtime_decision", "paper_regime_reference", "us_sip_daily_liquidity"],
        )


def _history(**workflows) -> dict:
    return {"schema_version": MODULE.RUN_HISTORY_SCHEMA, "fetched_at_utc": "2026-09-18T03:00:00Z",
            "workflows": {name.replace("__", "-") + ".yml": runs for name, runs in workflows.items()}}


def _run(run_id: int, created_at: str, conclusion: str, event: str = "workflow_run") -> dict:
    return {"id": run_id, "created_at": created_at, "status": "completed",
            "conclusion": conclusion, "event": event}


# The real 2026-09-18 paper-regime-reference.yml sequence, verified against the
# Actions API. Two failures, then two skips that became the newest terminal
# state -- so every "latest run conclusion" surface read non-red while the
# producer was down. The skips came from that workflow's job-level
# `if: ... || github.event.workflow_run.conclusion == 'success'`: the triggering
# upstream (P9-06 Upbit Realtime WebSocket Bounded Capture) concluded
# `cancelled`, so its single `build` job never ran.
REAL_SKIP_OVER_FAILURE_RUNS = [
    _run(35298736708, "2026-09-18T02:17:06Z", "skipped"),
    _run(35296682372, "2026-09-18T01:46:34Z", "skipped"),
    _run(35296380899, "2026-09-18T01:42:00Z", "failure"),
    _run(35295228731, "2026-09-18T01:25:06Z", "failure"),
    _run(35294462144, "2026-09-18T01:13:46Z", "success"),
]
# The same workflow's actual recovery run, 02:46Z.
REAL_RECOVERY_RUN = _run(35300643915, "2026-09-18T02:46:04Z", "success")


class RunConclusionAxisTest(unittest.TestCase):
    """A workflow's latest run conclusion can be `skipped` while real failures
    sit behind it. Anchored to the real run-id sequence above, not an invented
    one. Every test here fails against the classifier that had no run axis."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.today = dt.date(2026, 9, 18)
        self.now = dt.datetime(2026, 9, 18, 3, 0, tzinfo=dt.timezone.utc)
        self.spec = {
            "id": "paper_regime_reference_like", "label_ko": "PAPER 시장 참고 판정", "kind": "FILE",
            "path": "data/latest_ref.json", "date_fields": ["generated_at"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "workflow_file": "paper-regime-reference.yml",
            "terminal_run_window_hours": 24,
        }
        # Output itself is fresh -- the incident is invisible to the age axis.
        _write_json(self.root / self.spec["path"], {"generated_at": "2026-09-17T23:42:46Z"})

    def _report(self, runs):
        return MODULE.build_report(
            root=self.root, today=self.today, watchlist=[self.spec],
            run_history=_history(paper__regime__reference=runs), now_utc=self.now,
        )

    def test_latest_skipped_over_failure_is_an_alarm(self):
        report = self._report(REAL_SKIP_OVER_FAILURE_RUNS)
        item = report["items"][0]

        # The age axis sees nothing wrong at all.
        self.assertEqual(item["schedule_status"], "FRESH")
        self.assertEqual(item["status"], "LATEST_RUN_SKIPPED_OVER_FAILURE")
        self.assertEqual(item["latest_run_conclusion"], "skipped")
        self.assertEqual(item["latest_run_id"], 35298736708)
        self.assertFalse(report["all_fresh"])
        self.assertIn("paper_regime_reference_like", {i["id"] for i in report["alarm_items"]})

    def test_it_reports_the_last_actual_failure_not_the_skip(self):
        item = self._report(REAL_SKIP_OVER_FAILURE_RUNS)["items"][0]
        failure = item["last_actual_failure"]
        self.assertEqual(failure["id"], 35296380899)
        self.assertEqual(failure["created_at"], "2026-09-18T01:42:00Z")
        self.assertEqual(failure["conclusion"], "failure")
        # Both failures since the last success are counted, and the last
        # success (01:13:46Z) is named.
        self.assertEqual(item["last_success_run_id"], 35294462144)
        self.assertIn("35296380899", item["detail"])
        # The rendered line leads with the real failure, not the skip id.
        body = MODULE.render_issue_body(self._report(REAL_SKIP_OVER_FAILURE_RUNS))
        self.assertIn("35296380899", body)
        self.assertIn("skipped", body)

    def test_recovery_run_closes_the_finding(self):
        # The same workflow's real 02:46Z success ends the window.
        item = self._report([REAL_RECOVERY_RUN] + REAL_SKIP_OVER_FAILURE_RUNS)["items"][0]
        self.assertEqual(item["run_status"], MODULE.RUN_STATUS_OK)
        self.assertEqual(item["status"], "FRESH")

    def test_skipped_with_no_failure_behind_it_is_not_this_alarm(self):
        # A skip over a success is ordinary upstream fan-in noise.
        runs = [_run(2, "2026-09-18T02:17:06Z", "skipped"), _run(1, "2026-09-18T01:13:46Z", "success")]
        item = self._report(runs)["items"][0]
        self.assertNotEqual(item["status"], "LATEST_RUN_SKIPPED_OVER_FAILURE")

    def test_producer_silent_when_only_non_terminal_runs_in_the_window(self):
        # Nothing but skips/cancels for 24h: never red anywhere, and the forced
        # cron path is only twice a day, so this can persist.
        runs = [
            _run(5, "2026-09-18T02:17:06Z", "skipped"),
            _run(4, "2026-09-18T01:46:34Z", "skipped"),
            _run(3, "2026-09-17T20:00:00Z", "skipped"),
            _run(2, "2026-09-17T10:00:00Z", "skipped"),
            _run(1, "2026-09-16T01:13:46Z", "success"),  # outside the 24h window
        ]
        report = self._report(runs)
        item = report["items"][0]
        self.assertEqual(item["status"], "PRODUCER_SILENT_NO_TERMINAL_RUN")
        self.assertEqual(item["terminal_runs_in_window"], 0)
        self.assertEqual(item["terminal_run_window_hours"], 24)
        self.assertFalse(report["all_fresh"])
        self.assertIn("skipped", item["detail"])

    def test_a_terminal_run_in_the_window_is_not_silent(self):
        runs = [_run(2, "2026-09-18T02:17:06Z", "skipped"), _run(1, "2026-09-18T01:13:46Z", "success")]
        item = self._report(runs)["items"][0]
        self.assertNotEqual(item["status"], "PRODUCER_SILENT_NO_TERMINAL_RUN")
        self.assertEqual(item["terminal_runs_in_window"], 1)

    def test_silence_is_not_claimed_for_a_producer_with_no_schedule(self):
        # A dispatch-only producer has no expected day, so "no terminal run
        # today" is not a finding about it.
        spec = {
            "id": "dispatch_only", "label_ko": "수동 실행", "kind": "FILE",
            "path": "data/latest_dispatch.json", "date_fields": ["generated_at_utc"],
            "calendar": MODULE.NO_SCHEDULE, "default_max_gap_days": 10,
            "workflow_file": "alpaca-sip-daily-bars.yml",
        }
        _write_json(self.root / spec["path"], {"generated_at_utc": "2026-09-16T00:00:00Z"})
        report = MODULE.build_report(
            root=self.root, today=self.today, watchlist=[spec],
            run_history=_history(alpaca__sip__daily__bars=[_run(1, "2026-09-10T00:00:00Z", "skipped")]),
            now_utc=self.now,
        )
        self.assertNotEqual(report["items"][0]["status"], "PRODUCER_SILENT_NO_TERMINAL_RUN")

    def test_latest_failed_run_is_recorded_but_not_escalated(self):
        # A plainly failing latest run is already visibly red on every badge, so
        # it is reported rather than alarmed -- but never labelled OK.
        runs = [
            _run(3, "2026-09-17T23:53:08Z", "failure", event="schedule"),
            _run(2, "2026-09-17T22:10:10Z", "failure", event="workflow_dispatch"),
            _run(1, "2026-09-17T00:02:47Z", "success", event="schedule"),
        ]
        report = self._report(runs)
        item = report["items"][0]
        self.assertEqual(item["run_status"], "LATEST_RUN_FAILED")
        self.assertNotEqual(item["run_status"], MODULE.RUN_STATUS_OK)
        self.assertEqual(item["failed_runs_since_last_success"], 2)
        self.assertEqual(item["status"], "FRESH")  # output itself is fresh
        self.assertEqual(
            [entry["id"] for entry in report["producers_with_failing_runs"]],
            ["paper_regime_reference_like"],
        )
        self.assertIn("최신 run", MODULE.render_issue_body(report))

    # -- the run axis may only escalate, never explain a stale output away ---

    def test_healthy_runs_cannot_clear_an_output_staleness_alarm(self):
        # The general principle: a producer whose output is stale alarms even
        # when no run failed.
        _write_json(self.root / self.spec["path"], {"generated_at": "2026-09-05T00:00:00Z"})
        report = self._report([_run(1, "2026-09-18T02:46:04Z", "success")])
        item = report["items"][0]
        self.assertEqual(item["run_status"], MODULE.RUN_STATUS_OK)
        self.assertEqual(item["schedule_status"], "STALE")
        # No source locator on this synthetic spec, so the schedule verdict
        # stands untouched -- and healthy run metadata does not soften it.
        self.assertEqual(item["status"], "STALE")
        self.assertIn("paper_regime_reference_like", {i["id"] for i in report["alarm_items"]})
        self.assertFalse(report["all_fresh"])

    def test_missing_run_history_fails_closed_and_does_not_silence_staleness(self):
        _write_json(self.root / self.spec["path"], {"generated_at": "2026-09-05T00:00:00Z"})
        report = MODULE.build_report(
            root=self.root, today=self.today, watchlist=[self.spec],
            run_history=None, now_utc=self.now,
        )
        item = report["items"][0]
        self.assertEqual(item["run_status"], MODULE.RUN_STATUS_HISTORY_UNAVAILABLE)
        self.assertFalse(report["all_fresh"])
        self.assertIn("run 이력 미확보", MODULE.render_issue_body(report))

    def test_run_history_loader_fails_closed_on_bad_input(self):
        good = self.root / "good.json"
        _write_json(good, _history(paper__regime__reference=[]))
        self.assertIsNotNone(MODULE.load_run_history(good))
        self.assertIsNone(MODULE.load_run_history(None))
        self.assertIsNone(MODULE.load_run_history(self.root / "absent.json"))
        wrong_schema = self.root / "wrong.json"
        _write_json(wrong_schema, {"schema_version": "something_else/9", "workflows": {}})
        self.assertIsNone(MODULE.load_run_history(wrong_schema))
        no_workflows = self.root / "noworkflows.json"
        _write_json(no_workflows, {"schema_version": MODULE.RUN_HISTORY_SCHEMA})
        self.assertIsNone(MODULE.load_run_history(no_workflows))

    def test_a_second_independent_finding_is_not_hidden_by_a_louder_one(self):
        # Output behind its source AND the latest run skipped over a failure:
        # the loud one is the headline, the other stays visible.
        spec = {**self.spec, "source_latest": {
            "kind": MODULE.SOURCE_DATED_PATH,
            "glob": "data/observations/upstream/*/packet.json",
            "field_note": "newest committed upstream observation directory",
        }}
        _write_json(self.root / spec["path"], {"generated_at": "2026-09-11T00:00:00Z"})
        _write_json(self.root / "data/observations/upstream/2026-09-18/packet.json", {})
        report = MODULE.build_report(
            root=self.root, today=self.today, watchlist=[spec],
            run_history=_history(paper__regime__reference=REAL_SKIP_OVER_FAILURE_RUNS),
            now_utc=self.now,
        )
        item = report["items"][0]
        self.assertEqual(item["status"], "COLLECTION_BEHIND_SOURCE")
        self.assertIn(
            "LATEST_RUN_SKIPPED_OVER_FAILURE",
            [entry["status"] for entry in item["also_detected"]],
        )
        self.assertIn("추가 감지", MODULE.render_issue_body(report))


class ClassificationAxisTest(unittest.TestCase):
    """A pointer being present and dated today does not mean the state the
    sizing rules depend on is available. Keyed on the producer's own
    classification status, never on pointer presence (cf. issue #511)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.today = dt.date(2026, 9, 18)
        self.now = dt.datetime(2026, 9, 18, 3, 0, tzinfo=dt.timezone.utc)

    def _spec(self, **overrides):
        spec = {
            "id": "refresh_status_like", "label_ko": "시장판정 갱신 상태", "kind": "FILE",
            "path": "data/latest_refresh.json", "date_fields": ["current_reference.as_of_date"],
            "calendar": MODULE.EVERY_DAY, "allowed_missed_cycles": 1,
            "classification": {
                "status_field": "official_decision.classification_status",
                "missing_inputs_field": "official_decision.coverage.missing_axes",
                "available_statuses": ["CLASSIFIED"],
                "failure_markers": ["_FAILED", "REDERIVATION_FAILED"],
            },
        }
        spec.update(overrides)
        return spec

    def _report(self, payload, spec=None):
        spec = spec or self._spec()
        _write_json(self.root / spec["path"], payload)
        return MODULE.build_report(root=self.root, today=self.today, watchlist=[spec], now_utc=self.now)

    def test_todays_5_of_5_pointer_does_not_clear_an_unavailable_classification(self):
        # Exactly the issue #511 shape: the current-reference pointer is today's
        # and complete, which is all crypto-regime-refresh-watchdog.yml checks,
        # while the official decision is a WAIT_* state missing a required axis.
        report = self._report({
            "current_reference": {"as_of_date": "2026-09-18", "coverage": {"ratio": "5/5", "missing_axes": []}},
            "official_decision": {
                "classification_status": "WAIT_MARKET_NORMALIZATION_INPUT",
                "runtime_regime": "UNKNOWN",
                "coverage": {"ratio": "4/5", "missing_axes": ["LEADERSHIP"]},
            },
        })
        item = report["items"][0]
        self.assertEqual(item["schedule_status"], "FRESH")      # pointer is today's
        self.assertEqual(item["status"], "CLASSIFICATION_UNAVAILABLE")
        self.assertEqual(item["classification_status"], MODULE.CLASS_STATUS_UNAVAILABLE)
        self.assertIn("LEADERSHIP", item["detail"])
        self.assertFalse(report["all_fresh"])

    def test_declared_failure_state_is_an_alarm_even_with_no_missing_inputs(self):
        report = self._report({
            "current_reference": {"as_of_date": "2026-09-18"},
            "official_decision": {
                "classification_status": "CRYPTO_RISK_REDERIVATION_FAILED",
                "coverage": {"missing_axes": []},
            },
        })
        self.assertEqual(report["items"][0]["status"], "CLASSIFICATION_UNAVAILABLE")

    def test_available_classification_is_fine(self):
        report = self._report({
            "current_reference": {"as_of_date": "2026-09-18"},
            "official_decision": {"classification_status": "CLASSIFIED", "coverage": {"missing_axes": []}},
        })
        self.assertEqual(report["items"][0]["classification_status"], MODULE.CLASS_STATUS_AVAILABLE)
        self.assertTrue(report["all_fresh"])

    def test_undeclared_status_value_fails_closed_to_an_alarm(self):
        # A state nobody has declared must surface once, not pass silently.
        report = self._report({
            "current_reference": {"as_of_date": "2026-09-18"},
            "official_decision": {"classification_status": "SOME_BRAND_NEW_STATE", "coverage": {"missing_axes": []}},
        })
        self.assertEqual(report["items"][0]["status"], "CLASSIFICATION_UNAVAILABLE")
        self.assertIn("SOME_BRAND_NEW_STATE", report["items"][0]["detail"])

    def test_designed_pending_state_is_reported_without_alarming(self):
        # US/CRYPTO paper runtime sit at BLOCKED pending ratification. Alarming
        # daily on a designed long-running state is what trains a reader to
        # dismiss the watchdog, so it is parked and reported instead.
        spec = self._spec(classification={
            "status_field": "decision_status",
            "available_statuses": ["PAPER_RUNTIME_CLASSIFIED"],
            "expected_unavailable_statuses": ["BLOCKED"],
            "failure_markers": ["_FAILED"],
        }, date_fields=["evaluation_at"])
        report = self._report({"evaluation_at": "2026-09-18T01:00:00Z", "decision_status": "BLOCKED"}, spec)
        item = report["items"][0]
        self.assertEqual(item["classification_status"], MODULE.CLASS_STATUS_EXPECTED_UNAVAILABLE)
        self.assertNotEqual(item["status"], "CLASSIFICATION_UNAVAILABLE")
        self.assertTrue(report["all_fresh"])

    def test_each_market_is_judged_on_its_own_status(self):
        spec = self._spec(
            date_fields=["generated_at"],
            classification={
                "per_market_field": "markets",
                "market_label_field": "market",
                "status_field": "classification_status",
                "available_statuses": ["PAPER_REFERENCE_CLASSIFIED"],
                "failure_markers": ["_FAILED"],
            },
        )
        report = self._report({
            "generated_at": "2026-09-18T01:00:00Z",
            "markets": [
                {"market": "US", "classification_status": "PAPER_REFERENCE_CLASSIFIED"},
                {"market": "KR", "classification_status": "PAPER_REFERENCE_CLASSIFIED"},
                {"market": "CRYPTO", "classification_status": "WAIT_MARKET_NORMALIZATION_INPUT"},
            ],
        }, spec)
        item = report["items"][0]
        self.assertEqual(item["status"], "CLASSIFICATION_UNAVAILABLE")
        # The failing market is named; the healthy ones are not accused.
        self.assertIn("CRYPTO", item["detail"])
        self.assertNotIn("US:", item["detail"])
        self.assertEqual(item["classification_observed"]["US"], "PAPER_REFERENCE_CLASSIFIED")

    def test_absent_status_field_is_an_alarm_not_a_pass(self):
        report = self._report({
            "current_reference": {"as_of_date": "2026-09-18"},
            "official_decision": {"coverage": {"missing_axes": []}},
        })
        self.assertEqual(report["items"][0]["status"], "CLASSIFICATION_UNAVAILABLE")


class RealRepoRunAndClassificationTest(unittest.TestCase):
    def test_crypto_refresh_status_classification_frozen_is_flagged(self):
        # The issue #511 shape as committed on 2026-09-18: current_reference is a
        # complete 5/5 reference -- all crypto-regime-refresh-watchdog.yml looks
        # at -- while official_decision is WAIT_PIT_LEADERSHIP_HISTORY with
        # LEADERSHIP missing. Frozen, because the live values move.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "data/latest_crypto_regime_refresh_status.json", {
                "current_reference": {"as_of_date": "2026-09-18",
                                      "coverage": {"ratio": "5/5", "missing_axes": []}},
                "official_decision": {"classification_status": "WAIT_PIT_LEADERSHIP_HISTORY",
                                      "runtime_regime": "UNKNOWN",
                                      "coverage": {"ratio": "4/5", "missing_axes": ["LEADERSHIP"]}},
            })
            _write_json(root / "data/observations/crypto_recent_reference/2026-09-18/packet.json", {})
            spec = next(s for s in MODULE.default_watchlist()
                        if s["id"] == "crypto_regime_refresh_status")
            report = MODULE.build_report(root=root, today=dt.date(2026, 9, 18), watchlist=[spec])
            item = report["items"][0]
            self.assertEqual(item["schedule_status"], "FRESH")
            self.assertEqual(item["status"], "CLASSIFICATION_UNAVAILABLE")
            self.assertEqual(item["classification_status"], MODULE.CLASS_STATUS_UNAVAILABLE)
            self.assertIn("LEADERSHIP", item["detail"])

    def test_the_two_new_producers_are_watched(self):
        specs = {spec["id"]: spec for spec in MODULE.default_watchlist()}
        self.assertIn("paper_regime_reference", specs)
        self.assertIn("crypto_regime_refresh_status", specs)
        # Both are produced by the workflow the skip-over-failure incident hit.
        for producer in ("paper_regime_reference", "crypto_regime_refresh_status"):
            self.assertEqual(specs[producer]["workflow_file"], "paper-regime-reference.yml")

    def test_paper_runtime_blocked_states_are_parked_not_alarmed(self):
        # Guard against the watchdog going permanently red on designed states.
        # Asserted on the classification axis only: whether these producers are
        # in alarm_items for some *other* reason (a stale input, say) is a
        # different question and must stay answerable.
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        by_id = {item["id"]: item for item in report["items"]}
        for producer in ("us_paper_runtime_decision", "crypto_paper_runtime_decision"):
            with self.subTest(producer=producer):
                item = by_id[producer]
                self.assertEqual(item["classification_status"],
                                 MODULE.CLASS_STATUS_EXPECTED_UNAVAILABLE)
                self.assertNotEqual(item["status"], "CLASSIFICATION_UNAVAILABLE")

    def test_emit_workflow_files_is_single_sourced_from_the_watchlist(self):
        # The workflow's fetch step reads this list rather than duplicating it.
        expected = sorted({spec["workflow_file"] for spec in MODULE.default_watchlist()
                           if spec.get("workflow_file")})
        self.assertIn("paper-regime-reference.yml", expected)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            exit_code = MODULE.main(["--emit-workflow-files"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(captured.getvalue().split(), expected)


class ScheduleClaimIsDerivedTest(unittest.TestCase):
    """No spec may claim "no trigger exists" -- or sit at NO_SCHEDULE -- while a
    committed workflow actually drives that producer's output root.

    This is the coupling PR #799 added a test for on its own branch. Guarding it
    here too means the claim is correct whichever of the two lands first: the
    population specs derive their schedule from what is committed, so neither
    ordering needs a hand edit. Held for EVERY spec, not just those two.
    """

    NO_TRIGGER_CLAIM = "no .github/workflows trigger exists"

    @staticmethod
    def _bodies(root: Path) -> dict:
        """Executable YAML per workflow, comments stripped (same rule #799 uses,
        so a commented-out proposal is never read as a live trigger)."""
        directory = root / ".github" / "workflows"
        return {
            path.name: "\n".join(
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#"))
            for path in sorted(directory.glob("*.yml"))
        } if directory.is_dir() else {}

    def _assert_claims_hold(self, root: Path):
        bodies = self._bodies(root)
        for spec in MODULE.default_watchlist(root):
            root_path = str(spec.get("glob") or spec.get("path") or "").split("/*", 1)[0]
            self.assertTrue(root_path, f"spec {spec['id']} has no glob/path")
            writers = sorted(name for name, body in bodies.items() if root_path in body)
            with self.subTest(spec=spec["id"]):
                if self.NO_TRIGGER_CLAIM in str(spec.get("workflow") or ""):
                    self.assertEqual(
                        writers, [],
                        f"{spec['id']} claims no trigger exists but {writers} write {root_path}")
                if (spec.get("calendar") or {}).get("type") == "NO_SCHEDULE":
                    for name in writers:
                        self.assertNotIn(
                            "schedule:", bodies[name],
                            f"{spec['id']} is NO_SCHEDULE but {name} both schedules and writes {root_path}")

    def test_claims_hold_against_this_repo_as_committed(self):
        self._assert_claims_hold(ROOT)

    def test_claims_hold_once_a_daily_schedule_for_the_population_lands(self):
        # Simulates PR #799 landing: its real workflow, verbatim in shape --
        # daily cron plus the git add of both population output roots.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/population-symbol-observation-daily.yml").write_text(
                "name: Population Symbol Observation Daily\n"
                "on:\n"
                "  schedule:\n"
                '    - cron: "20 15 * * *"\n'
                '    - cron: "20 20 * * *"\n'
                "  workflow_dispatch:\n"
                "jobs:\n"
                "  observe:\n"
                "    steps:\n"
                "      - run: |\n"
                "          git add data/observations/korea_population_symbol_observation \\\n"
                "                  data/observations/us_population_symbol_observation\n",
                encoding="utf-8")
            self._assert_claims_hold(root)

            specs = {s["id"]: s for s in MODULE.default_watchlist(root)}
            for producer in ("korea_population_symbol_observation", "us_population_symbol_observation"):
                spec = specs[producer]
                # Flipped automatically: real trigger named, real cron quoted.
                self.assertEqual(spec["calendar"]["type"], "EVERY_DAY")
                self.assertEqual(spec["schedule_derivation"], "COMMITTED_WORKFLOW_CRON")
                self.assertEqual(spec["workflow_file"], "population-symbol-observation-daily.yml")
                self.assertIn("20 15 * * *", spec["workflow"])
                self.assertNotIn(self.NO_TRIGGER_CLAIM, spec["workflow"])

    def test_a_dispatch_only_writer_does_not_become_a_schedule_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/population-dispatch.yml").write_text(
                "on:\n  workflow_dispatch:\n"
                "jobs:\n  o:\n    steps:\n"
                "      - run: git add data/observations/korea_population_symbol_observation\n",
                encoding="utf-8")
            spec = next(s for s in MODULE.default_watchlist(root)
                        if s["id"] == "korea_population_symbol_observation")
            self.assertEqual(spec["calendar"]["type"], "NO_SCHEDULE")
            self.assertEqual(spec["schedule_derivation"], "COMMITTED_WORKFLOW_DISPATCH_ONLY")
            # It is driven by something, so the "no trigger" claim is dropped...
            self.assertNotIn(self.NO_TRIGGER_CLAIM, spec["workflow"])
            # ...and the dispatch-only reality is stated instead.
            self.assertIn("workflow_dispatch only", spec["workflow"])
            self._assert_claims_hold(root)

    def test_a_commented_out_cron_is_not_read_as_a_live_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/proposal.yml").write_text(
                "on:\n  workflow_dispatch:\n"
                "#  schedule:\n"
                '#    - cron: "0 13 * * *"\n'
                "jobs:\n  o:\n    steps:\n"
                "      - run: git add data/observations/us_population_symbol_observation\n",
                encoding="utf-8")
            spec = next(s for s in MODULE.default_watchlist(root)
                        if s["id"] == "us_population_symbol_observation")
            self.assertEqual(spec["schedule_derivation"], "COMMITTED_WORKFLOW_DISPATCH_ONLY")
            self.assertEqual(spec["calendar"]["type"], "NO_SCHEDULE")


class DeclaredNonCoverageTest(unittest.TestCase):
    """The watchdog must state what it cannot see. A spec that claimed coverage
    it cannot deliver would be worse than an admitted gap."""

    def test_kis_market_poll_is_declared_not_silently_missing(self):
        entry = next(e for e in MODULE.DECLARED_NON_COVERAGE if e["id"] == "atlas_kis_market_poll")
        # The incident, the reason, and the remedies are all stated.
        self.assertIn("2026-09-10", entry["incident"])
        self.assertIn("timer fired normally", entry["incident"])
        self.assertTrue(entry["why_not_observable"].strip())
        self.assertGreaterEqual(len(entry["would_be_caught_by"]), 2)

    def test_it_is_not_smuggled_into_the_watchlist_as_a_fake_spec(self):
        # Adding a spec for a path nobody writes would sit permanently at
        # NEVER_PRODUCED -- standing red noise that trains dismissal, and a
        # claim of coverage that does not exist.
        declared = {e["id"] for e in MODULE.DECLARED_NON_COVERAGE}
        watched = {spec["id"] for spec in MODULE.default_watchlist()}
        self.assertEqual(declared & watched, set())
        for spec in MODULE.default_watchlist():
            target = str(spec.get("glob") or spec.get("path") or "")
            self.assertNotIn("kis_market_poll", target)

    def test_non_coverage_is_carried_into_the_report_and_rendered(self):
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18), watchlist=[])
        self.assertEqual(
            [e["id"] for e in report["declared_non_coverage"]],
            [e["id"] for e in MODULE.DECLARED_NON_COVERAGE])
        body = MODULE.render_issue_body(report)
        # Rendered even on an all-clear report, so silence is never read as coverage.
        self.assertTrue(report["all_fresh"])
        self.assertIn("atlas_kis_market_poll", body)
        self.assertIn("볼 수 없는", body)


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

    def test_frozen_2026_09_18_incident_set_is_flagged(self):
        # The four incidents as they stood on 2026-09-18, frozen together so the
        # whole report -- not just one item -- is exercised. Deterministic: no
        # live data/ or evidence/ read, so main advancing cannot retire it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(root / "data/latest_free_market_data.json", {
                "observed_at_utc": "2026-09-15T23:39:00Z",
                "alpaca": {"daily_bars": [{"opened_at": "2026-09-15T04:00:00Z"}]}})
            _write_json(root / "data/latest_kr_paper_runtime_decision.json", {
                "evaluation_at": "2026-09-13T00:58:44Z", "decision_status": "PAPER_RUNTIME_CLASSIFIED",
                "current_observation": {"as_of_date": "2026-09-11"}})
            # The KR calendar replays the officially captured KRX holiday list,
            # so the fixture needs it too. It is content-pinned by sha256 inside
            # the module, so copying it keeps this deterministic.
            capture = Path(MODULE.KR_CALENDAR_PACKETS.CAPTURE_REF)
            (root / capture.parent).mkdir(parents=True, exist_ok=True)
            (root / capture).write_bytes((ROOT / capture).read_bytes())
            _write_json(root / "data/observations/korea_population_symbol_observation/2026-09-10/summary.json", {})
            _write_json(root / "data/observations/us_population_symbol_observation/2026-09-11/summary.json", {})
            for universe in ("krx_global_universe", "us_global_universe"):
                _write_json(root / f"data/observations/{universe}/2026-09-16/packet.json", {})
            wanted = {"free_market_data", "kr_paper_runtime_decision",
                      "korea_population_symbol_observation", "us_population_symbol_observation"}
            watchlist = [s for s in MODULE.default_watchlist() if s["id"] in wanted]
            self.assertEqual(len(watchlist), len(wanted))
            report = MODULE.build_report(root=root, today=dt.date(2026, 9, 18), watchlist=watchlist)
            by_id = {item["id"]: item for item in report["items"]}

            # Both population observations: the source offered newer than we held.
            for producer in ("korea_population_symbol_observation", "us_population_symbol_observation"):
                self.assertEqual(by_id[producer]["status"], "COLLECTION_BEHIND_SOURCE")
            # free_market_data: co-frozen claim, so honestly "cannot tell".
            self.assertEqual(by_id["free_market_data"]["status"], "SOURCE_LATEST_UNKNOWN")
            # KR paper runtime: no source-side latest is committed at all.
            self.assertEqual(by_id["kr_paper_runtime_decision"]["status"], "SOURCE_LATEST_UNKNOWN")
            self.assertEqual(by_id["kr_paper_runtime_decision"]["source_latest_status"], "UNAVAILABLE")
            # Every one of them needs attention, and the loudest sorts first.
            self.assertEqual(wanted, {item["id"] for item in report["stale_items"]})
            self.assertEqual(report["alarm_items"][0]["status"], "COLLECTION_BEHIND_SOURCE")
            self.assertFalse(report["all_fresh"])

    def test_real_repo_report_builds_and_renders_for_any_date(self):
        # Date-independent real-repo check: whatever the committed evidence has
        # become, the watchdog must classify every producer without raising and
        # render a body. This is what the live read is actually good for.
        for today in (dt.date(2026, 9, 18), dt.date(2026, 10, 5), dt.date(2027, 1, 4)):
            with self.subTest(today=today):
                report = MODULE.build_report(root=ROOT, today=today)
                self.assertEqual(len(report["items"]), len(MODULE.default_watchlist(ROOT)))
                for item in report["items"]:
                    self.assertIn(item["status"], MODULE.STATUS_SEVERITY)
                json.dumps(report)
                self.assertTrue(MODULE.render_issue_body(report).strip())

    def test_report_is_json_serializable(self):
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        json.dumps(report)  # must not raise


if __name__ == "__main__":
    unittest.main()
