"""P4-07 candle-finalization boundary primitive regression."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "microstructure" / "upbit_candle_finalization.py"
SPEC = importlib.util.spec_from_file_location("upbit_candle_finalization", MODULE_PATH)
FIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(FIN)

UTC = dt.timezone.utc


def candle(open_time: str, **overrides) -> dict:
    row = {
        "candle_date_time_utc": open_time,
        "opening_price": 1000, "high_price": 1010, "low_price": 990,
        "trade_price": 1005, "candle_acc_trade_price": 123456, "candle_acc_trade_volume": 12.3,
    }
    row.update(overrides)
    return row


class IsCandleFinalizedTests(unittest.TestCase):
    def test_close_time_before_as_of_is_finalized(self):
        open_time = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        as_of = dt.datetime(2026, 8, 28, 0, 16, 0, tzinfo=UTC)
        self.assertTrue(FIN.is_candle_finalized(open_time, "15m", as_of))

    def test_close_time_exactly_at_as_of_is_finalized_inclusive(self):
        open_time = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        as_of = dt.datetime(2026, 8, 28, 0, 15, 0, tzinfo=UTC)
        self.assertTrue(FIN.is_candle_finalized(open_time, "15m", as_of))

    def test_close_time_after_as_of_is_not_finalized(self):
        open_time = dt.datetime(2026, 8, 28, 0, 10, 0, tzinfo=UTC)
        as_of = dt.datetime(2026, 8, 28, 0, 20, 0, tzinfo=UTC)
        self.assertFalse(FIN.is_candle_finalized(open_time, "15m", as_of))

    def test_naive_datetime_rejected(self):
        with self.assertRaises(FIN.CandleFinalizationError):
            FIN.is_candle_finalized(dt.datetime(2026, 8, 28), "15m", dt.datetime(2026, 8, 28, 1, tzinfo=UTC))

    def test_unknown_timeframe_rejected(self):
        with self.assertRaises(FIN.CandleFinalizationError):
            FIN.is_candle_finalized(dt.datetime(2026, 8, 28, tzinfo=UTC), "2h", dt.datetime(2026, 8, 28, 1, tzinfo=UTC))


class ClassifyCandlesTests(unittest.TestCase):
    def test_partitions_finalized_vs_in_progress_per_timeframe(self):
        as_of = dt.datetime(2026, 8, 28, 1, 5, 0, tzinfo=UTC)
        rows = [candle("2026-08-28T00:00:00"), candle("2026-08-28T00:15:00"), candle("2026-08-28T01:00:00")]
        result = FIN.classify_candles(rows, "15m", as_of)
        self.assertEqual(len(result["finalized"]), 2)
        self.assertEqual(len(result["in_progress"]), 1)
        self.assertEqual(result["in_progress"][0]["open_time"], dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC))

    def test_missing_required_field_fails_closed(self):
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        row = candle("2026-08-28T00:00:00")
        del row["trade_price"]
        with self.assertRaisesRegex(FIN.CandleFinalizationError, "CANDLE_FIELD_MISSING"):
            FIN.classify_candles([row], "15m", as_of)

    def test_future_dated_open_time_rejected(self):
        as_of = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        rows = [candle("2026-08-28T01:00:00")]
        with self.assertRaisesRegex(FIN.CandleFinalizationError, "FUTURE_DATED_CANDLE"):
            FIN.classify_candles(rows, "15m", as_of)

    def test_duplicate_open_time_deduped_deterministically(self):
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        first = candle("2026-08-28T00:00:00", trade_price=1000)
        duplicate = candle("2026-08-28T00:00:00", trade_price=9999)
        result = FIN.classify_candles([first, duplicate], "15m", as_of)
        self.assertEqual(result["duplicate_row_count"], 1)
        self.assertEqual(len(result["finalized"]), 1)
        self.assertEqual(result["finalized"][0]["raw"]["trade_price"], 1000)

    def test_all_four_timeframes_supported(self):
        as_of = dt.datetime(2026, 8, 29, 0, 0, 0, tzinfo=UTC)
        for timeframe in ("15m", "1h", "4h", "1d"):
            result = FIN.classify_candles([candle("2026-08-28T00:00:00")], timeframe, as_of)
            self.assertEqual(len(result["finalized"]), 1)


class GapDetectionAndBackfillTests(unittest.TestCase):
    def test_detect_gaps_finds_missing_window(self):
        present = [
            dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC),
            dt.datetime(2026, 8, 28, 0, 15, 0, tzinfo=UTC),
            # 00:30 missing
            dt.datetime(2026, 8, 28, 0, 45, 0, tzinfo=UTC),
        ]
        gaps = FIN.detect_gaps(
            present, "15m",
            dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC),
            dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(gaps, [dt.datetime(2026, 8, 28, 0, 30, 0, tzinfo=UTC)])

    def test_no_gaps_when_fully_covered(self):
        window_start = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        window_end = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        present = FIN.expected_open_times("15m", window_start, window_end)
        self.assertEqual(FIN.detect_gaps(present, "15m", window_start, window_end), [])

    def test_group_contiguous_gaps_merges_adjacent(self):
        missing = [
            dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC),
            dt.datetime(2026, 8, 28, 0, 15, 0, tzinfo=UTC),
            dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC),
        ]
        windows = FIN.group_contiguous_gaps(missing, "15m")
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0]["from_open_time"], missing[0])
        self.assertEqual(windows[0]["to_open_time"], missing[1])
        self.assertEqual(windows[1]["from_open_time"], missing[2])

    def test_backfill_fills_gap_without_duplicating_or_corrupting_adjacent(self):
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        committed_rows = [candle("2026-08-28T00:00:00"), candle("2026-08-28T00:30:00")]
        committed = {
            entry["open_time"]: entry
            for entry in FIN.classify_candles(committed_rows, "15m", as_of)["finalized"]
        }
        # simulate a re-query of the whole window that includes the
        # previously-missing 00:15 candle plus the two already-committed ones
        refetched_rows = [
            candle("2026-08-28T00:00:00"), candle("2026-08-28T00:15:00"), candle("2026-08-28T00:30:00"),
        ]
        refetched = FIN.classify_candles(refetched_rows, "15m", as_of)["finalized"]
        result = FIN.merge_finalized_no_overwrite(committed, refetched)
        self.assertEqual(len(result["merged"]), 3)
        self.assertEqual(result["added_open_times"], [dt.datetime(2026, 8, 28, 0, 15, 0, tzinfo=UTC)])

    def test_backfill_rejects_conflicting_rewrite_of_committed_history(self):
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        committed_rows = [candle("2026-08-28T00:00:00", trade_price=1000)]
        committed = {
            entry["open_time"]: entry
            for entry in FIN.classify_candles(committed_rows, "15m", as_of)["finalized"]
        }
        conflicting_rows = [candle("2026-08-28T00:00:00", trade_price=9999)]
        conflicting = FIN.classify_candles(conflicting_rows, "15m", as_of)["finalized"]
        with self.assertRaisesRegex(FIN.CandleFinalizationError, "COMMITTED_CANDLE_MISMATCH"):
            FIN.merge_finalized_no_overwrite(committed, conflicting)


class DeterminismTests(unittest.TestCase):
    def test_classify_candles_deterministic_across_runs(self):
        as_of = dt.datetime(2026, 8, 28, 1, 5, 0, tzinfo=UTC)
        rows = [candle("2026-08-28T00:00:00"), candle("2026-08-28T00:15:00"), candle("2026-08-28T01:00:00")]
        first = FIN.classify_candles(list(rows), "15m", as_of)
        second = FIN.classify_candles(list(rows), "15m", as_of)
        self.assertEqual(
            [e["open_time"] for e in first["finalized"]], [e["open_time"] for e in second["finalized"]]
        )
        self.assertEqual(first["finalized"][0]["raw"], second["finalized"][0]["raw"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
