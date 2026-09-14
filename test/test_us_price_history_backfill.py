#!/usr/bin/env python3
"""US price-history backfill planner (US-DATA-1 item 2) regression.

Pure-logic coverage only: anchor chaining, batching, pacing estimate, the
dry-run plan shape, and the `run_live_backfill` path exercised through a
synthetic in-memory getter (no real network call anywhere in this file).
`run_all.py` executes this file directly (`python3 <script>`), not via
pytest -- it must self-run through `unittest.main()`.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "us_price_history_backfill", ROOT / "collectors" / "us_price_history_backfill.py"
)
M = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(M)


def _contract():
    return M.load_contract(M.CONTRACT_PATH)


def _fixture_getter(bars_by_symbol):
    """Synthetic Alpaca daily-bars getter: no urllib, no network."""
    def getter(url, *_args, **_kwargs):
        symbol = url.split("/stocks/", 1)[1].split("/", 1)[0]
        return json.dumps({"bars": bars_by_symbol[symbol]}).encode()
    return getter


def _bar(date_str, value=100.0):
    return {
        "o": value, "h": value + 1, "l": value - 1, "c": value + 0.5,
        "v": 1000, "t": f"{date_str}T00:00:00Z",
    }


class BackfillSymbolsTests(unittest.TestCase):
    def test_matches_contract_union_count(self):
        symbols = M.backfill_symbols(_contract())
        self.assertEqual(len(symbols), 22)
        self.assertEqual(symbols, sorted(symbols))
        self.assertEqual(len(symbols), len(set(symbols)))

    def test_missing_symbols_fails_closed(self):
        with self.assertRaises(M.BackfillPlanError):
            M.backfill_symbols({"alpaca": {}})

    def test_duplicate_symbols_fail_closed(self):
        with self.assertRaises(M.BackfillPlanError):
            M.backfill_symbols({"alpaca": {"symbols": ["SPY", "SPY", "QQQ"]}})


class AnchorChainingTests(unittest.TestCase):
    def test_single_window_needs_one_anchor(self):
        end = dt.date(2026, 9, 14)
        start = end - dt.timedelta(days=30)
        anchors = M.compute_backfill_anchors(start, end, window_days=180)
        self.assertEqual(anchors, [end])

    def test_trailing_year_needs_three_anchors_with_180_day_window(self):
        end = dt.date(2026, 9, 14)
        start = end - dt.timedelta(days=364)
        anchors = M.compute_backfill_anchors(start, end, window_days=180)
        self.assertEqual(len(anchors), 3)
        self.assertEqual(anchors[0], end)

    def test_anchor_chain_has_no_coverage_gap(self):
        end = dt.date(2026, 9, 14)
        start = end - dt.timedelta(days=364)
        window = 180
        anchors = M.compute_backfill_anchors(start, end, window_days=window)
        oldest = min(anchors)
        self.assertLessEqual(oldest - dt.timedelta(days=window), start)

    def test_reversed_range_fails_closed(self):
        with self.assertRaises(M.BackfillPlanError):
            M.compute_backfill_anchors(dt.date(2026, 9, 14), dt.date(2026, 9, 1))

    def test_non_positive_window_fails_closed(self):
        with self.assertRaises(M.BackfillPlanError):
            M.compute_backfill_anchors(dt.date(2026, 9, 1), dt.date(2026, 9, 14), window_days=0)

    def test_equal_start_and_end_is_a_single_anchor(self):
        day = dt.date(2026, 9, 14)
        self.assertEqual(M.compute_backfill_anchors(day, day), [day])


class RequestUnitsAndBatchesTests(unittest.TestCase):
    def test_symbol_major_ordering_keeps_each_symbols_anchors_together(self):
        units = M.build_request_units(["AAA", "BBB"], [dt.date(2026, 1, 1), dt.date(2026, 6, 1)])
        self.assertEqual(
            units,
            [
                ("AAA", dt.date(2026, 6, 1)), ("AAA", dt.date(2026, 1, 1)),
                ("BBB", dt.date(2026, 6, 1)), ("BBB", dt.date(2026, 1, 1)),
            ],
        )

    def test_batch_count_matches_ceil_division(self):
        units = [("S", dt.date(2026, 1, 1))] * 10
        batches = M.build_batches(units, batch_size=4)
        self.assertEqual([len(b) for b in batches], [4, 4, 2])

    def test_non_positive_batch_size_fails_closed(self):
        with self.assertRaises(M.BackfillPlanError):
            M.build_batches([("S", dt.date(2026, 1, 1))], batch_size=0)


class WallclockEstimateTests(unittest.TestCase):
    def test_exact_formula(self):
        # 10 requests, batch_size=4 -> 3 batches -> 2 batch boundaries.
        seconds = M.estimate_wallclock_seconds(
            total_requests=10, batch_size=4, request_pause_seconds=3.0, batch_pause_seconds=15.0,
        )
        self.assertEqual(seconds, 10 * 3.0 + 2 * 15.0)

    def test_zero_requests_is_zero_seconds(self):
        self.assertEqual(M.estimate_wallclock_seconds(0, 4, 3.0, 15.0), 0.0)

    def test_non_positive_batch_size_fails_closed(self):
        with self.assertRaises(M.BackfillPlanError):
            M.estimate_wallclock_seconds(10, 0, 3.0, 15.0)


class BuildPlanTests(unittest.TestCase):
    def test_full_trailing_year_plan_shape_and_authority(self):
        end = dt.date(2026, 9, 14)
        start = end - dt.timedelta(days=364)
        plan = M.build_plan(_contract(), start, end)
        self.assertEqual(plan["symbol_count"], 22)
        self.assertEqual(plan["anchor_count"], 3)
        self.assertEqual(plan["total_requests"], 66)
        self.assertEqual(plan["pit_class"], "HISTORICAL_BACKFILL")
        self.assertEqual(plan["network_calls_made"], 0)
        self.assertTrue(all(v is False for v in plan["authority"].values()))
        self.assertEqual(plan["batch_count"], -(-66 // plan["batch_size"]))

    def test_plan_is_json_serializable(self):
        end = dt.date(2026, 9, 14)
        plan = M.build_plan(_contract(), end - dt.timedelta(days=10), end)
        json.dumps(plan)  # must not raise


class DryRunCliTests(unittest.TestCase):
    def test_default_invocation_makes_no_network_call_and_prints_plan(self):
        argv = ["us_price_history_backfill.py", "--end-date", "2026-09-14"]
        with mock.patch("sys.argv", argv), mock.patch("builtins.print") as printed:
            code = M.main()
        self.assertEqual(code, 0)
        printed_text = printed.call_args[0][0]
        plan = json.loads(printed_text)
        self.assertEqual(plan["mode"], "DRY_RUN")
        self.assertEqual(plan["total_requests"], 66)

    def test_live_without_credentials_falls_back_to_dry_run_with_blocked_reason(self):
        argv = ["us_price_history_backfill.py", "--end-date", "2026-09-14", "--live"]
        with mock.patch("sys.argv", argv), mock.patch("builtins.print") as printed, \
                mock.patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("ALPACA_MARKET_DATA_API_KEY", None)
            _os.environ.pop("ALPACA_MARKET_DATA_API_SECRET", None)
            code = M.main()
        self.assertEqual(code, 0)
        plan = json.loads(printed.call_args[0][0])
        self.assertEqual(plan["mode"], "DRY_RUN")
        self.assertEqual(plan["blocked_reason"], "US_PRICE_HISTORY_BACKFILL_BLOCKED_BY_MISSING_CREDENTIAL")


class LiveCredentialGuardTests(unittest.TestCase):
    def test_missing_both_fails_closed(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("ALPACA_MARKET_DATA_API_KEY", None)
            _os.environ.pop("ALPACA_MARKET_DATA_API_SECRET", None)
            with self.assertRaises(M.BackfillPlanError):
                M._require_live_credentials()

    def test_incomplete_pair_fails_closed(self):
        with mock.patch.dict("os.environ", {"ALPACA_MARKET_DATA_API_KEY": "k"}, clear=False):
            import os as _os
            _os.environ.pop("ALPACA_MARKET_DATA_API_SECRET", None)
            with self.assertRaises(M.BackfillPlanError):
                M._require_live_credentials()

    def test_complete_pair_returns_tuple(self):
        with mock.patch.dict(
            "os.environ",
            {"ALPACA_MARKET_DATA_API_KEY": "k", "ALPACA_MARKET_DATA_API_SECRET": "s"},
            clear=False,
        ):
            self.assertEqual(M._require_live_credentials(), ("k", "s"))


class OutDirBoundaryTests(unittest.TestCase):
    def test_path_inside_repo_is_refused(self):
        with self.assertRaises(M.BackfillPlanError):
            M._require_out_dir_outside_repo(M.ROOT / "data")

    def test_path_outside_repo_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "not_in_repo"
            resolved = M._require_out_dir_outside_repo(outside)
            self.assertEqual(resolved, outside.resolve())


class RunLiveBackfillOfflineFixtureTests(unittest.TestCase):
    """Exercises the live path end-to-end with a synthetic getter -- still
    zero real network calls, since the injected `getter` never touches
    urllib."""

    def test_writes_count_only_manifests_outside_repo(self):
        anchor = dt.date(2026, 9, 14)
        start = anchor - dt.timedelta(days=5)
        contract = {"alpaca": {"symbols": ["SPY", "QQQ"]}}
        bars_by_symbol = {
            "SPY": [_bar("2026-09-10"), _bar("2026-09-11"), _bar("2026-09-14")],
            "QQQ": [_bar("2026-09-12"), _bar("2026-09-13")],
        }
        with mock.patch.dict(
            "os.environ",
            {"ALPACA_MARKET_DATA_API_KEY": "k", "ALPACA_MARKET_DATA_API_SECRET": "s"},
            clear=False,
        ), tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "us_price_history_backfill_out"
            receipt = M.run_live_backfill(
                contract, start, anchor, out_dir,
                getter=_fixture_getter(bars_by_symbol), sleep_fn=lambda _seconds: None,
            )
            # Assertions that touch the filesystem must stay inside the
            # TemporaryDirectory's `with` block -- it is deleted on exit.
            manifests = sorted(out_dir.glob("*.manifest.json"))
            self.assertEqual(len(manifests), 2)
            for manifest_path in manifests:
                manifest = json.loads(manifest_path.read_text())
                self.assertEqual(manifest["pit_class"], "HISTORICAL_BACKFILL")
                self.assertTrue(all(v is False for v in manifest["authority"].values()))
        self.assertEqual(receipt["total_requests"], 2)  # single-window range -> one anchor/symbol
        self.assertEqual(receipt["row_counts_by_symbol"]["SPY"], 3)
        self.assertEqual(receipt["row_counts_by_symbol"]["QQQ"], 2)
        self.assertTrue(all(v is False for v in receipt["authority"].values()))

    def test_lookahead_bar_fails_closed_instead_of_silent_trim(self):
        anchor = dt.date(2026, 9, 14)
        start = anchor - dt.timedelta(days=5)
        contract = {"alpaca": {"symbols": ["SPY"]}}
        bars_by_symbol = {"SPY": [_bar("2026-09-10"), _bar("2026-09-16")]}  # after anchor
        with mock.patch.dict(
            "os.environ",
            {"ALPACA_MARKET_DATA_API_KEY": "k", "ALPACA_MARKET_DATA_API_SECRET": "s"},
            clear=False,
        ), tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            with self.assertRaises(M.BackfillPlanError) as ctx:
                M.run_live_backfill(
                    contract, start, anchor, out_dir,
                    getter=_fixture_getter(bars_by_symbol), sleep_fn=lambda _seconds: None,
                )
        self.assertIn("US_PRICE_HISTORY_BACKFILL_LOOKAHEAD_VIOLATION", str(ctx.exception))

    def test_out_dir_inside_repo_is_refused_before_any_request(self):
        contract = {"alpaca": {"symbols": ["SPY"]}}
        with mock.patch.dict(
            "os.environ",
            {"ALPACA_MARKET_DATA_API_KEY": "k", "ALPACA_MARKET_DATA_API_SECRET": "s"},
            clear=False,
        ):
            with self.assertRaises(M.BackfillPlanError):
                M.run_live_backfill(
                    contract, dt.date(2026, 9, 1), dt.date(2026, 9, 14), M.ROOT / "data",
                    getter=_fixture_getter({}), sleep_fn=lambda _seconds: None,
                )

    def test_missing_credentials_refused_before_any_request(self):
        contract = {"alpaca": {"symbols": ["SPY"]}}
        with mock.patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("ALPACA_MARKET_DATA_API_KEY", None)
            _os.environ.pop("ALPACA_MARKET_DATA_API_SECRET", None)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(M.BackfillPlanError):
                    M.run_live_backfill(
                        contract, dt.date(2026, 9, 1), dt.date(2026, 9, 14), Path(tmp) / "out",
                        getter=_fixture_getter({}), sleep_fn=lambda _seconds: None,
                    )


if __name__ == "__main__":
    unittest.main()
