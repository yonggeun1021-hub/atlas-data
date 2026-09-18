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

    def test_fx_false_incident_is_no_longer_an_alarm(self):
        # evidence/fred_dexkous_fx ends at 2026-09-11 and the collector's own
        # 2026-09-17 raw manifest says FRED served nothing newer. Age-only, this
        # was reported as a three-day FX observation loss. It must now be
        # informational, and must NOT open an issue on its own.
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        item = next(i for i in report["items"] if i["id"] == "fred_dexkous_fx")
        self.assertEqual(item["status"], "SOURCE_NOT_YET_PUBLISHED")
        self.assertEqual(item["last_date"], "2026-09-11")
        self.assertEqual(item["source_latest"], "2026-09-11")
        self.assertNotIn("fred_dexkous_fx", {i["id"] for i in report["alarm_items"]})
        self.assertIn("fred_dexkous_fx", {i["id"] for i in report["informational_items"]})

    def test_population_observations_are_flagged_as_behind_their_source(self):
        # Both population observations declare their input in
        # population.source.path; those upstream universes have reached
        # 2026-09-16 while the observations hold 2026-09-10 / 2026-09-11. That
        # is committed data the source already offered and we never took.
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        by_id = {item["id"]: item for item in report["items"]}
        for producer in ("korea_population_symbol_observation", "us_population_symbol_observation"):
            with self.subTest(producer=producer):
                item = by_id[producer]
                self.assertEqual(item["status"], "COLLECTION_BEHIND_SOURCE")
                self.assertEqual(item["source_latest"], "2026-09-16")
                self.assertGreater(item["source_latest"], item["last_date"])
        # The loudest state sorts first in the issue body.
        self.assertEqual(report["alarm_items"][0]["status"], "COLLECTION_BEHIND_SOURCE")

    def test_free_market_data_outage_is_not_silenced_by_its_own_claim(self):
        # free-market-data.yml failed 2026-09-16/09-17, so the provider-reported
        # newest bar inside data/latest_free_market_data.json is frozen at
        # 2026-09-15 alongside our own date. Equal dates must NOT be read as
        # "the source is quiet" here.
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        item = next(i for i in report["items"] if i["id"] == "free_market_data")
        self.assertEqual(item["source_latest_status"], "CLAIM_NOT_CURRENT")
        self.assertEqual(item["status"], "SOURCE_LATEST_UNKNOWN")
        self.assertNotEqual(item["status"], "SOURCE_NOT_YET_PUBLISHED")
        self.assertIn("free_market_data", {i["id"] for i in report["stale_items"]})

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

    def test_real_repo_blind_spots_are_exactly_the_two_documented_ones(self):
        report = MODULE.build_report(root=ROOT, today=dt.date(2026, 9, 18))
        self.assertEqual(
            [spot["id"] for spot in report["source_latest_blind_spots"]],
            ["kr_paper_runtime_decision", "us_sip_daily_liquidity"],
        )


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
