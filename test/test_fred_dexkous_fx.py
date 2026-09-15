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


if __name__ == "__main__":
    unittest.main()
