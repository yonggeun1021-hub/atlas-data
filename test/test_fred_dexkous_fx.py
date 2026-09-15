#!/usr/bin/env python3
"""FRED DEXKOUS capture/publish/reader regressions -- offline, mocked HTTP
only. No network call is ever made by this file."""
from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fred_dexkous_fx", ROOT / "collectors" / "fred_dexkous_fx.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

NOW = dt.datetime(2026, 9, 15, 1, 0, 0, tzinfo=dt.timezone.utc)


def api_raw(rows: list[tuple[str, str]]) -> bytes:
    return json.dumps({
        "observations": [{"date": d, "value": v} for d, v in rows],
    }).encode()


def csv_raw(rows: list[tuple[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["DATE", "DEXKOUS"])
    for d, v in rows:
        writer.writerow([d, v])
    return buf.getvalue().encode()


def authorities_false(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_authorized") and item is not False:
                return False
            if not authorities_false(item):
                return False
    elif isinstance(value, list):
        return all(authorities_false(item) for item in value)
    return True


class ParsingTests(unittest.TestCase):
    def test_api_parses_ascending_and_skips_missing(self):
        raw = api_raw([("2026-09-10", "1385.00"), ("2026-09-11", "."), ("2026-09-12", "1390.50")])
        out = M.parse_api_observations(raw)
        self.assertEqual([o["observation_date"] for o in out], ["2026-09-10", "2026-09-12"])

    def test_api_rejects_descending(self):
        raw = api_raw([("2026-09-12", "1390.50"), ("2026-09-10", "1385.00")])
        with self.assertRaisesRegex(M.FredDexkousFxError, "DEXKOUS_OBSERVATIONS_NOT_ASCENDING"):
            M.parse_api_observations(raw)

    def test_api_rejects_non_positive_value(self):
        raw = api_raw([("2026-09-10", "-1.0")])
        with self.assertRaisesRegex(M.FredDexkousFxError, "DEXKOUS_OBSERVATION_VALUE_INVALID"):
            M.parse_api_observations(raw)

    def test_csv_parses_and_skips_missing(self):
        raw = csv_raw([("2026-09-10", "1385.00"), ("2026-09-11", "."), ("2026-09-12", "1390.50")])
        out = M.parse_csv_observations(raw)
        self.assertEqual([o["value"] for o in out], ["1385.00", "1390.50"])

    def test_csv_rejects_bad_header(self):
        raw = b"NOT_DATE,DEXKOUS\n2026-09-10,1385.00\n"
        with self.assertRaisesRegex(M.FredDexkousFxError, "DEXKOUS_CSV_HEADER_INVALID"):
            M.parse_csv_observations(raw)

    def test_parse_observations_dispatches_on_source_kind(self):
        raw = api_raw([("2026-09-10", "1385.00")])
        self.assertEqual(M.parse_observations(raw, "FRED_API")[0]["value"], "1385.00")
        with self.assertRaisesRegex(M.FredDexkousFxError, "DEXKOUS_SOURCE_KIND_INVALID"):
            M.parse_observations(raw, "SOMETHING_ELSE")


class FetchTests(unittest.TestCase):
    def test_fetch_dexkous_uses_api_when_key_present(self):
        calls = []
        def fake_getter(url, headers=None):
            calls.append(url)
            return api_raw([("2026-09-10", "1385.00")])
        raw, kind = M.fetch_dexkous("secret-key", getter=fake_getter)
        self.assertEqual(kind, "FRED_API")
        self.assertIn("api.stlouisfed.org", calls[0])
        self.assertIn("api_key=secret-key", calls[0])

    def test_fetch_dexkous_falls_back_to_csv_without_key(self):
        calls = []
        def fake_getter(url, headers=None):
            calls.append(url)
            return csv_raw([("2026-09-10", "1385.00")])
        raw, kind = M.fetch_dexkous(None, getter=fake_getter)
        self.assertEqual(kind, "FRED_CSV")
        self.assertIn("fred.stlouisfed.org", calls[0])

    def test_csv_fallback_also_bounds_the_request_via_cosd(self):
        # PR #765 follow-up: the CSV fallback's raw archive was previously
        # unbounded (FRED's entire 1981-present response) every normal run,
        # unlike the API path (which already passed observation_start).
        calls = []
        def fake_getter(url, headers=None):
            calls.append(url)
            return csv_raw([("2026-09-10", "1385.00")])
        M.fetch_dexkous(None, observation_start="2026-08-16", getter=fake_getter)
        self.assertIn("cosd=2026-08-16", calls[0])

    def test_csv_fallback_omits_cosd_when_no_observation_start_given(self):
        # --backfill never passes observation_start -- confirms the full
        # series is still requested in that deliberate case.
        calls = []
        def fake_getter(url, headers=None):
            calls.append(url)
            return csv_raw([("2026-09-10", "1385.00")])
        M.fetch_dexkous(None, getter=fake_getter)
        self.assertNotIn("cosd", calls[0])

    def test_http_error_is_caught_and_reduced_to_a_status_code(self):
        import urllib.error
        from unittest import mock

        def raise_http_error(request, timeout=30):
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

        with mock.patch("urllib.request.urlopen", side_effect=raise_http_error):
            with self.assertRaisesRegex(M.FredDexkousFxError, "^DEXKOUS_HTTP_ERROR_429$"):
                M.fetch_via_csv()


class CaptureAndPublishTests(unittest.TestCase):
    def test_authority_is_all_false(self):
        raw = api_raw([("2026-09-10", "1385.00")])
        cap = M.build_capture(NOW, raw, "FRED_API")
        self.assertTrue(authorities_false(cap["manifest"]))
        for entry in cap["observation_records"]:
            self.assertTrue(authorities_false(entry["record"]))

    def test_capture_is_deterministic(self):
        raw = api_raw([("2026-09-10", "1385.00")])
        first = M.build_capture(NOW, raw, "FRED_API")
        second = M.build_capture(NOW, raw, "FRED_API")
        self.assertEqual(first["manifest"], second["manifest"])
        self.assertEqual(
            [e["record_path"] for e in first["observation_records"]],
            [e["record_path"] for e in second["observation_records"]],
        )

    def test_publish_writes_one_record_per_observation_and_is_idempotent(self):
        raw = api_raw([("2026-09-10", "1385.00"), ("2026-09-11", "1390.50")])
        cap = M.build_capture(NOW, raw, "FRED_API")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = M.publish_capture(root, cap)
            self.assertEqual(len(summary["new_observation_paths"]), 2)
            for path in summary["new_observation_paths"]:
                self.assertTrue((root / path).exists())
            # Re-publishing the identical capture creates nothing new.
            summary2 = M.publish_capture(root, cap)
            self.assertEqual(summary2["new_observation_paths"], [])

    def test_backfill_rows_have_null_availability_and_unknown_kind(self):
        raw = api_raw([("2020-01-02", "1160.00")])
        cap = M.build_capture(NOW, raw, "FRED_API", is_backfill=True)
        record = cap["observation_records"][0]["record"]
        self.assertEqual(record["availability_kind"], "UNKNOWN_BACKFILL")
        self.assertIsNone(record["availability_captured_at_utc"])
        self.assertTrue(cap["observation_records"][0]["record_path"].endswith(".backfill.json"))

    def test_captured_and_backfill_records_for_same_pair_do_not_collide(self):
        raw = api_raw([("2020-01-02", "1160.00")])
        backfill = M.build_capture(NOW, raw, "FRED_API", is_backfill=True)
        captured = M.build_capture(NOW, raw, "FRED_API", is_backfill=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_capture(root, backfill)
            M.publish_capture(root, captured)
            paths = sorted(p.name for p in (root / M.EVIDENCE_ROOT / "observations" / "2020-01-02").glob("*.json"))
            self.assertEqual(len(paths), 2)
            self.assertTrue(any(p.endswith(".backfill.json") for p in paths))
            self.assertTrue(any(p.endswith(".captured.json") for p in paths))

    def test_append_only_collision_fails_closed(self):
        raw = api_raw([("2026-09-10", "1385.00")])
        cap = M.build_capture(NOW, raw, "FRED_API")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_capture(root, cap)
            path = root / cap["raw_pointer"]["manifest_path"]
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(M.FredDexkousFxError, "APPEND_ONLY_COLLISION"):
                M.publish_capture(root, cap)

    def test_re_observing_the_same_pair_later_keeps_the_earlier_availability(self):
        # This is the exact shape of every normal day inside the bounded
        # write window: the same (date, value) is legitimately re-parsed
        # on a later run with a different captured_at_utc/batch_revision_id.
        # It must be silently kept as a no-op -- not APPEND_ONLY_COLLISION,
        # and the ORIGINAL (earlier, tighter) availability must survive.
        raw = api_raw([("2026-09-10", "1385.00")])
        first = M.build_capture(NOW, raw, "FRED_API")
        later = NOW + dt.timedelta(days=1)
        second = M.build_capture(later, raw, "FRED_API")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary1 = M.publish_capture(root, first)
            self.assertEqual(len(summary1["new_observation_paths"]), 1)
            summary2 = M.publish_capture(root, second)
            self.assertEqual(summary2["new_observation_paths"], [])  # no-op, not a collision
            path = root / first["observation_records"][0]["record_path"]
            on_disk = json.loads(path.read_text())
            self.assertEqual(
                on_disk["availability_captured_at_utc"],
                first["observation_records"][0]["record"]["availability_captured_at_utc"],
            )

    def test_observation_record_identity_mismatch_at_the_same_path_fails_closed(self):
        # Same content-address path, but the observation_date/value baked
        # into the existing file's content disagrees with the incoming
        # write -- unreachable in practice (the path is derived from
        # exactly those fields) except genuine corruption, so this must
        # still fail closed rather than silently keep the wrong record.
        raw = api_raw([("2026-09-10", "1385.00")])
        cap = M.build_capture(NOW, raw, "FRED_API")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_capture(root, cap)
            record_path = root / cap["observation_records"][0]["record_path"]
            corrupted = json.loads(record_path.read_text())
            corrupted["value"] = "9999.99"
            record_path.write_text(json.dumps(corrupted))
            with self.assertRaisesRegex(M.FredDexkousFxError, "APPEND_ONLY_COLLISION"):
                M.publish_capture(root, cap)

    def test_path_traversal_is_rejected(self):
        raw = api_raw([("2026-09-10", "1385.00")])
        cap = M.build_capture(NOW, raw, "FRED_API")
        cap["raw_pointer"]["raw_path"] = f"{M.EVIDENCE_ROOT}/raw/../../secret"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(M.FredDexkousFxError, "EVIDENCE_PATH_INVALID"):
                M.publish_capture(Path(tmp), cap)


class BusinessDaysTests(unittest.TestCase):
    def test_same_day_is_zero(self):
        d = dt.date(2026, 9, 15)
        self.assertEqual(M.business_days_between(d, d), 0)

    def test_friday_to_monday_is_one_business_day(self):
        # 2026-09-18 is a Friday; 2026-09-21 is the following Monday.
        self.assertEqual(
            M.business_days_between(dt.date(2026, 9, 18), dt.date(2026, 9, 21)), 1
        )

    def test_ten_weekdays_forward(self):
        start = dt.date(2026, 9, 15)  # Tuesday
        end = start
        added = 0
        while added < 10:
            end += dt.timedelta(days=1)
            if end.weekday() < 5:
                added += 1
        self.assertEqual(M.business_days_between(start, end), 10)

    def test_decision_before_availability_fails_closed(self):
        with self.assertRaisesRegex(M.FredDexkousFxError, "DECISION_BEFORE_AVAILABILITY"):
            M.business_days_between(dt.date(2026, 9, 15), dt.date(2026, 9, 14))


class LatestAvailableTests(unittest.TestCase):
    def _publish(self, root, rows, captured_at=NOW, is_backfill=False):
        raw = api_raw(rows)
        cap = M.build_capture(captured_at, raw, "FRED_API", is_backfill=is_backfill)
        M.publish_capture(root, cap)

    def test_returns_latest_eligible_observation_not_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._publish(root, [("2026-09-14", "1385.00"), ("2026-09-15", "1387.50")])
            result = M.latest_available(root, "2026-09-15T02:00:00Z")
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["observation_date"], "2026-09-15")
            self.assertEqual(result["value"], "1387.50")
            self.assertFalse(result["stale"])
            self.assertIsNone(result["display"])

    def test_flags_nav_display_after_ten_business_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._publish(root, [("2026-09-15", "1387.50")], captured_at=NOW)
            # NOW is 2026-09-15 (Tue). Add 11 weekdays.
            far = dt.date(2026, 9, 15)
            added = 0
            while added < 11:
                far += dt.timedelta(days=1)
                if far.weekday() < 5:
                    added += 1
            result = M.latest_available(root, far.isoformat() + "T00:00:00Z")
            self.assertTrue(result["stale"])
            self.assertEqual(result["display"], "NAV 일부 미검증")
            self.assertEqual(result["staleness_clock_kind"], "CIO_INTERPRETATION_NOT_USER_RATIFIED")

    def test_future_availability_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._publish(root, [("2026-09-15", "1387.50")], captured_at=NOW)
            result = M.latest_available(root, "2026-09-14T00:00:00Z")
            self.assertEqual(result["status"], "NO_POINT_IN_TIME_OBSERVATION_AVAILABLE")
            self.assertFalse(result["backfill_only_rows_present"])

    def test_backfill_only_is_never_usable_but_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._publish(root, [("2020-01-02", "1160.00")], is_backfill=True)
            result = M.latest_available(root, "2026-09-15T00:00:00Z")
            self.assertEqual(result["status"], "NO_POINT_IN_TIME_OBSERVATION_AVAILABLE")
            self.assertTrue(result["backfill_only_rows_present"])

    def test_no_capture_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = M.latest_available(Path(tmp), "2026-09-15T00:00:00Z")
            self.assertEqual(result["status"], "NO_POINT_IN_TIME_OBSERVATION_AVAILABLE")
            self.assertFalse(result["backfill_only_rows_present"])

    def test_re_observing_a_backfilled_value_makes_it_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._publish(root, [("2020-01-02", "1160.00")], is_backfill=True)
            self._publish(root, [("2020-01-02", "1160.00")], captured_at=NOW, is_backfill=False)
            result = M.latest_available(root, "2026-09-15T02:00:00Z")
            self.assertEqual(result["status"], "OK")
            self.assertEqual(result["observation_date"], "2020-01-02")


class BoundedWriteWindowTests(unittest.TestCase):
    """CIO incident 2026-09-15: the first live FRED run wrote 11,352
    CAPTURED files (the entire 1981-present history) in one commit. A
    normal capture must never do that again -- see module docstring,
    "Normal captures are bounded, not full-history"."""

    def test_compute_write_from_date_with_nothing_committed_is_just_the_window(self):
        today = dt.date(2026, 9, 15)
        self.assertEqual(
            M.compute_write_from_date(today, None, window_days=30),
            (today - dt.timedelta(days=30)).isoformat(),
        )

    def test_compute_write_from_date_reaches_back_to_a_real_gap(self):
        # Last commit was 60 days ago -- further back than the 30-day
        # window -- so the gap itself, not the window, is authoritative.
        today = dt.date(2026, 9, 15)
        latest_committed = (today - dt.timedelta(days=60)).isoformat()
        self.assertEqual(
            M.compute_write_from_date(today, latest_committed, window_days=30),
            latest_committed,
        )

    def test_compute_write_from_date_uses_the_window_when_committed_is_recent(self):
        today = dt.date(2026, 9, 15)
        latest_committed = (today - dt.timedelta(days=2)).isoformat()
        self.assertEqual(
            M.compute_write_from_date(today, latest_committed, window_days=30),
            (today - dt.timedelta(days=30)).isoformat(),
        )

    def test_latest_committed_captured_date_ignores_backfill_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish_capture(root, M.build_capture(NOW, api_raw([("2020-01-02", "1160.00")]), "FRED_API", is_backfill=True))
            self.assertIsNone(M.latest_committed_captured_date(root))
            M.publish_capture(root, M.build_capture(NOW, api_raw([("2026-09-10", "1385.00")]), "FRED_API"))
            self.assertEqual(M.latest_committed_captured_date(root), "2026-09-10")

    def test_build_capture_bounds_written_records_but_manifest_keeps_full_count(self):
        # A response spanning 1981-present-like range; only the tail should
        # be written, but the manifest must still describe everything parsed.
        rows = [("1981-04-13", "1.50"), ("2020-01-02", "1160.00"), ("2026-09-14", "1385.00"), ("2026-09-15", "1387.50")]
        capture = M.build_capture(
            NOW, api_raw(rows), "FRED_API", write_observations_from="2026-09-14",
        )
        self.assertEqual(capture["manifest"]["observation_count"], 4)
        self.assertEqual(capture["manifest"]["observation_date_range"], ["1981-04-13", "2026-09-15"])
        self.assertEqual(capture["manifest"]["written_observation_count"], 2)
        self.assertEqual(
            sorted(e["record"]["observation_date"] for e in capture["observation_records"]),
            ["2026-09-14", "2026-09-15"],
        )

    def test_backfill_with_write_from_is_rejected(self):
        with self.assertRaisesRegex(M.FredDexkousFxError, "BACKFILL_WITH_WRITE_FROM_NOT_ALLOWED"):
            M.build_capture(
                NOW, api_raw([("2020-01-02", "1160.00")]), "FRED_API",
                is_backfill=True, write_observations_from="2026-01-01",
            )

    def test_end_to_end_normal_run_never_rewrites_full_history_again(self):
        # Simulate: day 1 is a normal run against a response that (like the
        # real incident) spans the entire history; day 2 is the next daily
        # run against the same full-history response plus one new day.
        # Neither day should write anywhere near the full row count.
        history = [(f"2020-01-{d:02d}", "1160.00") for d in range(1, 29)]  # 28 old rows
        recent = [("2026-09-14", "1385.00")]
        full_response_day1 = history + recent
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest_committed = M.latest_committed_captured_date(root)
            write_from = M.compute_write_from_date(dt.date(2026, 9, 15), latest_committed, window_days=5)
            capture1 = M.build_capture(
                NOW, api_raw(full_response_day1), "FRED_API", write_observations_from=write_from,
            )
            summary1 = M.publish_capture(root, capture1)
            self.assertEqual(len(summary1["new_observation_paths"]), 1)  # only 2026-09-14

            day2 = NOW + dt.timedelta(days=1)
            full_response_day2 = full_response_day1 + [("2026-09-15", "1387.50")]
            latest_committed = M.latest_committed_captured_date(root)
            write_from2 = M.compute_write_from_date(day2.date(), latest_committed, window_days=5)
            capture2 = M.build_capture(
                day2, api_raw(full_response_day2), "FRED_API", write_observations_from=write_from2,
            )
            summary2 = M.publish_capture(root, capture2)
            # 2026-09-14 already exists (no-op, content-addressed) and
            # 2026-09-15 is genuinely new -- never the 29-row full history.
            self.assertEqual(len(summary2["new_observation_paths"]), 1)
            self.assertEqual(summary2["new_observation_paths"][0].split("/")[-2], "2026-09-15")


class MainBoundedCaptureTests(unittest.TestCase):
    """main() end-to-end: a normal run must bound itself using whatever is
    already on disk, without the caller doing anything special."""

    def test_normal_run_bounds_writes_using_latest_committed_date(self):
        history_rows = [(f"2019-{m:02d}-01", "1150.00") for m in range(1, 13)]  # 12 old rows
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_root = M.ROOT
            M.ROOT = root
            try:
                # Seed one already-committed CAPTURED observation so the
                # window has something to bound against.
                M.publish_capture(
                    root,
                    M.build_capture(NOW, api_raw([("2026-09-14", "1385.00")]), "FRED_API"),
                )

                # Patch fetch_dexkous itself, not the low-level _get: _get
                # is only a *default parameter value* captured once when
                # fetch_via_api/fetch_via_csv were defined, so reassigning
                # the module attribute M._get after the fact would not
                # reach main()'s call.
                def fake_fetch_dexkous(api_key, *, observation_start=None, getter=None):
                    raw = api_raw(history_rows + [("2026-09-14", "1385.00"), ("2026-09-15", "1387.50")])
                    return raw, "FRED_API"

                original_fetch_dexkous = M.fetch_dexkous
                M.fetch_dexkous = fake_fetch_dexkous
                try:
                    rc = M.main([])
                finally:
                    M.fetch_dexkous = original_fetch_dexkous
            finally:
                M.ROOT = original_root

            self.assertEqual(rc, 0)
            written_dates = sorted(p.name for p in (root / M.EVIDENCE_ROOT / "observations").iterdir())
            # Only the recent tail was ever written -- never the 12 old rows.
            self.assertNotIn("2019-01-01", written_dates)
            self.assertIn("2026-09-15", written_dates)


if __name__ == "__main__":
    unittest.main()
