#!/usr/bin/env python3
"""US sector rotation event study (1-year Alpaca backfill) regression.

Offline only: synthetic bars, synthetic in-memory getter, no network. Covers
pre-registration hash + parameter lock, metric computation on hand-checkable
panels, gates, the backfill-dir loader (hash/lookahead/conflict/boundary), an
end-to-end run over a synthetic 66-unit backfill, and the aggregate-only
artifact schema (price/close/volume/returns series are rejected).
`run_all.py` executes this file directly.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


S = _load("us_sector_rotation_event_study", "study/us_sector_rotation_event_study.py")
BF = _load("us_price_history_backfill_for_study_test", "collectors/us_price_history_backfill.py")
LIVE_ENV = {"ALPACA_MARKET_DATA_API_KEY": "k", "ALPACA_MARKET_DATA_API_SECRET": "s"}
END = dt.date(2026, 9, 11)
START = END - dt.timedelta(days=364)


def _weekdays(start, end):
    day, out = start, []
    while day <= end:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _synthetic_paths(seed=7):
    """Seeded random-walk closes for all 22 approved symbols over ~1.5 years of weekdays."""
    rng = random.Random(seed)
    days = _weekdays(END - dt.timedelta(days=560), END)
    paths = {}
    for index, symbol in enumerate(BF.APPROVED_SYMBOLS):
        level = 60.0 + 7 * index
        drift = rng.uniform(-0.0006, 0.0006)
        series = {}
        for day in days:
            level *= math.exp(drift + rng.gauss(0, 0.012))
            series[day.isoformat()] = round(level, 4)
        paths[symbol] = series
    return paths


def _synthetic_getter(paths, calls=None):
    def getter(url, headers=None):
        symbol = url.split("/stocks/", 1)[1].split("/", 1)[0]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        start = query["start"][0]
        end = query["end"][0][:10]
        if calls is not None:
            calls.append(symbol)
        bars = []
        for day, close in sorted(paths[symbol].items()):
            if start <= day <= end:
                bars.append({"o": close, "h": close * 1.01, "l": close * 0.99, "c": close,
                             "v": 1000 + len(bars), "vw": close, "t": f"{day}T04:00:00Z"})
        return json.dumps({"bars": bars, "next_page_token": None}).encode()
    return getter


def _flat_panel(T=60, move=None):
    dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(T)]
    close = {s: [100.0] * T for s in S.STUDY_SYMBOLS}
    if move:
        move(close)
    return {"dates": dates, "close": close, "dollar_vol": {s: [1.0] * T for s in S.STUDY_SYMBOLS}}


class PreregistrationTests(unittest.TestCase):
    def test_committed_document_hash_matches(self):
        self.assertEqual(S.verify_preregistration(), S.PREREGISTRATION_SHA256)

    def test_tampered_document_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "prereg.md"
            copy.write_bytes(S.PREREGISTRATION_PATH.read_bytes().replace(b"n >= 80", b"n >= 40"))
            with self.assertRaises(S.StudyError) as ctx:
                S.verify_preregistration(copy)
        self.assertEqual(str(ctx.exception), "US_ROTATION_STUDY_PREREGISTRATION_HASH_MISMATCH")

    def test_parameters_are_the_preregistered_us_values(self):
        text = S.PREREGISTRATION_PATH.read_text()
        for fragment in (
            "US: B=SPY, L=20", "US TOP = ranks 1-3, BOTTOM = ranks 9-11", "US 5/10/20 sessions (MAIN = 10)",
            "US sector ETF 0.10%", "Also reported at 2x cost", "US: d=3, w=5", "US N=20, M=5",
            "US N=3, L=20", "every 20 sessions (US)", "5-session avg dollar volume / 20-session avg",
            ">= 1.0", "last 40 sessions", "within 5 observations", "within 10 observations",
            "n >= 80; n_nonoverlap >= 30; mean - 2c > 0; hit rate >= baseline + 5pp",
            "US >= 6 of eligible sectors positive", "n >= 30, mean FX < 0",
            "No parameter will be changed after results are seen",
        ):
            self.assertIn(fragment, text)
        p = S.PARAMETERS
        self.assertEqual((p["rs_lookback_sessions"], p["top_bucket_size"], p["bottom_bucket_size"]), (20, 3, 3))
        self.assertEqual((p["horizons_sessions"], p["main_horizon_sessions"]), ([5, 10, 20], 10))
        self.assertEqual(p["round_trip_cost"], 0.0010)
        self.assertEqual((p["watch_rank_improvement_min"], p["watch_window_sessions"]), (3, 5))
        self.assertEqual((p["rrg_n_m"], p["momentum_rebalance_sessions_top_n"]), ([20, 5], [20, 3]))
        self.assertEqual((p["participation_short_long_sessions"], p["participation_min_ratio"]), ([5, 20], 1.0))
        self.assertEqual((p["gate_enter_min_n"], p["gate_enter_min_n_nonoverlap"], p["gate_enter_hit_margin"]), (80, 30, 0.05))
        self.assertEqual((p["gate_repeatability_min_positive_sectors"], p["gate_exit_min_n"]), (6, 30))
        self.assertEqual(p["gate_repeatability_min_events_per_sector"], 3)
        self.assertEqual(len(S.ENTITIES), 11)
        self.assertEqual(S.BENCHMARK, "SPY")
        self.assertEqual(set(S.STUDY_SYMBOLS) - set(BF.APPROVED_SYMBOLS), set())

    def test_module_has_no_network_client(self):
        source = (ROOT / "study" / "us_sector_rotation_event_study.py").read_text()
        for token in ("urllib", "requests", "socket", "http.client", "subprocess"):
            self.assertNotIn(f"import {token}", source)


class EngineMetricTests(unittest.TestCase):
    def test_breakout_entry_bucket_and_forward_excess(self):
        # Every symbol flat at 100; XLK rises 1%/session (log) from session 31.
        def move(close):
            for t in range(31, 60):
                close["XLK"][t] = 100.0 * math.exp(0.01 * (t - 30))
        res = S.run_engine(_flat_panel(move=move))
        enters = [ev for ev in res["events"]["R1-k1_ENTER"] if ev["e"] == "XLK"]
        self.assertEqual([ev["t"] for ev in enters], [31])
        # Flat ties sort by name, so XLB/XLC/XLE start TOP; XLE drops to MIDDLE at t=31.
        self.assertEqual([ev["t"] for ev in res["events"]["R1-k1_ENTER"] if ev["e"] != "XLK"], [])
        self.assertEqual([(ev["e"], ev["t"]) for ev in res["events"].get("R1-k1_EXIT", [])], [])
        k2 = [ev["t"] for ev in res["events"]["R1-k2_ENTER"] if ev["e"] == "XLK"]
        self.assertEqual(k2, [32])
        # FX_5 at t=31: XLK log return over t+1..t+6 minus the 11-sector mean.
        self.assertAlmostEqual(res["fx"]("XLK", 31, 5), 0.05 - 0.05 / 11, places=12)
        self.assertAlmostEqual(res["fx"]("XLK", 31, 5, vs="bench"), 0.05, places=12)
        self.assertIsNone(res["fx"]("XLK", 55, 5))
        self.assertIn("XLK", {ev["e"] for ev in res["events"]["R4_ENTER_LEADING"]})
        self.assertEqual(res["recent_start"], res["dates"][60 - 10 - 1 - 40])

    def test_participation_filter_and_whipsaw_exit(self):
        def move(close):
            for t in range(31, 34):
                close["XLK"][t] = 100.0 * math.exp(0.02 * (t - 30))
            for t in range(34, 60):
                close["XLK"][t] = 100.0 * math.exp(-0.05)
        panel = _flat_panel(move=move)
        panel["dollar_vol"]["XLK"] = [1.0] * 30 + [5.0] * 30
        res = S.run_engine(panel)
        self.assertEqual([ev["t"] for ev in res["events"]["R2_ENTER"] if ev["e"] == "XLK"], [32])
        exits = [ev for ev in res["events"]["R1-k1_EXIT"] if ev["e"] == "XLK"]
        self.assertTrue(exits)
        self.assertLessEqual(exits[0]["t"] - 31, 5)
        self.assertGreaterEqual(res["whip"][1][0], 1)

    def test_summarize_matches_hand_computation(self):
        dates = [f"d{i:03d}" for i in range(100)]
        values = {("XLK", 10): 0.03, ("XLK", 12): -0.01, ("XLE", 11): 0.02, ("XLE", 70): 0.04, ("XLF", 80): None}
        res = {
            "events": {"X": [{"e": e, "t": t, "date": dates[t]} for (e, t) in values]},
            "fx": lambda e, t, h, vs="avg": values[(e, t)] if vs == "avg" else (None if values[(e, t)] is None else values[(e, t)] - 0.01),
            "base": {5: 0.5}, "recent_start": dates[60],
        }
        s = S.summarize(res, "X", 5, "enter")
        self.assertEqual(s["n"], 4)
        self.assertEqual(s["n_nonoverlap"], 3)  # XLK t=12 overlaps t=10 within h=5
        self.assertAlmostEqual(s["mean"], 0.02)
        self.assertAlmostEqual(s["median"], 0.025)
        self.assertAlmostEqual(s["hit"], 0.75)
        self.assertAlmostEqual(s["false_rate"], 0.25)
        self.assertAlmostEqual(s["mean_trim"], (0.03 - 0.01 + 0.02) / 3)
        self.assertEqual((s["recent_n"], s["early_n"]), (1, 3))
        self.assertAlmostEqual(s["recent_mean"], 0.04)
        self.assertAlmostEqual(s["mean_cost"], 0.019)
        self.assertAlmostEqual(s["mean_2cost"], 0.018)
        self.assertAlmostEqual(s["mean_vs_benchmark"], 0.01)
        self.assertEqual(s["per_entity"]["XLK"][0], 2)
        exit_summary = S.summarize(res, "X", 5, "exit")
        self.assertAlmostEqual(exit_summary["false_rate"], 0.75)
        self.assertAlmostEqual(exit_summary["mean_trim"], (0.03 + 0.02 + 0.04) / 3)

    def _enter_summary(self, **overrides):
        s = {"n": 100, "n_nonoverlap": 40, "mean": 0.01, "hit": 0.6, "base_hit": 0.5, "mean_trim": 0.008,
             "recent_mean": 0.002, "mean_2cost": 0.008,
             "per_entity": {e: (5, 0.01 if i < 6 else -0.01) for i, e in enumerate(S.ENTITIES)}}
        s.update(overrides)
        return s

    def test_enter_gate(self):
        self.assertEqual(S.gate(self._enter_summary(), "enter")[0], "PASS")
        self.assertEqual(S.gate(self._enter_summary(n=79), "enter")[0], "INSUFFICIENT")
        self.assertEqual(S.gate(self._enter_summary(n_nonoverlap=29), "enter")[0], "INSUFFICIENT")
        self.assertEqual(S.gate(self._enter_summary(hit=0.549), "enter")[0], "FAIL:hit")
        self.assertEqual(S.gate(self._enter_summary(mean_2cost=-0.0001, recent_mean=-0.001), "enter")[0], "FAIL:cost2x,recent")
        five_positive = {e: (5, 0.01 if i < 5 else -0.01) for i, e in enumerate(S.ENTITIES)}
        self.assertEqual(S.gate(self._enter_summary(per_entity=five_positive), "enter")[0], "FAIL:repeat")
        self.assertEqual(S.gate({"n": 0}, "enter")[0], "INSUFFICIENT(n=0)")

    def test_exit_gate(self):
        s = {"n": 30, "mean": -0.01, "mean_trim": -0.005, "recent_mean": -0.002}
        self.assertEqual(S.gate(s, "exit")[0], "JUSTIFIED")
        self.assertEqual(S.gate(dict(s, n=29), "exit")[0], "INSUFFICIENT")
        self.assertEqual(S.gate(dict(s, recent_mean=0.001), "exit")[0], "NOT_JUSTIFIED:recent")


class BackfillIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.backfill_dir = Path(cls.tmp.name) / "backfill"
        cls.paths = _synthetic_paths()
        cls.calls = []
        with mock.patch.dict("os.environ", LIVE_ENV, clear=False):
            cls.receipt = BF.run_live_backfill(
                BF.load_contract(), START, END, cls.backfill_dir,
                getter=_synthetic_getter(cls.paths, cls.calls), sleep_fn=lambda _s: None,
            )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_backfill_made_exactly_the_bounded_request_count(self):
        self.assertEqual(len(self.calls), 66)
        self.assertEqual(self.receipt["requests_made"], 66)

    def test_loader_builds_one_year_panel_with_hashes_only_in_meta(self):
        panel, meta = S.load_backfill_panel(self.backfill_dir)
        expected = [d.isoformat() for d in _weekdays(START, END)]
        self.assertEqual(panel["dates"], expected)
        self.assertEqual(meta["session_count"], len(expected))
        self.assertEqual(meta["unit_count"], 66)
        self.assertEqual(meta["revision_conflicts"], 0)
        self.assertEqual(panel["close"]["XLK"][-1], self.paths["XLK"][END.isoformat()])
        self.assertNotIn("close", json.dumps(meta))

    def test_end_to_end_artifact_is_aggregate_only(self):
        panel, meta = S.load_backfill_panel(self.backfill_dir)
        artifact = S.build_artifact(panel, meta, self.receipt)
        S.validate_artifact(artifact)
        self.assertEqual(set(artifact["events"]), set(S.KINDS))
        self.assertEqual(artifact["backfill"]["requests_made"], 66)
        main = artifact["events"]["R1-k1_ENTER"]["horizons"]["h10"]
        for key in ("n", "mean_forward_excess", "hit_rate", "false_signal_rate", "recent_mean", "early_mean",
                    "mean_after_cost", "mean_after_2x_cost", "per_sector"):
            self.assertIn(key, main)
        self.assertTrue(all(isinstance(v, str) for v in artifact["candidate_verdicts"].values()))
        all_closes = {round(c, 4) for series in self.paths.values() for c in series.values()}

        def floats(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from floats(item)
            elif isinstance(value, list):
                for item in value:
                    yield from floats(item)
            elif isinstance(value, float):
                yield value
        for number in floats(artifact):
            self.assertLessEqual(abs(number), 1.5)
            self.assertNotIn(round(number, 4), all_closes)
        text = json.dumps(artifact)
        self.assertNotIn('"c"', text)
        self.assertNotIn('"bars"', text)

    def test_cli_run_writes_once_outside_repo_and_check_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "upload" / "study.json"
            receipt = Path(tmp) / "receipt.json"
            receipt.write_text(json.dumps(self.receipt))
            argv = ["run", "--backfill-dir", str(self.backfill_dir), "--out", str(out), "--receipt", str(receipt)]
            with mock.patch("builtins.print"):
                self.assertEqual(S.main(argv), 0)
                self.assertEqual(S.main(["check-artifact", "--artifact", str(out)]), 0)
                self.assertEqual(S.main(argv), 1)  # never overwrites
                inside = ["run", "--backfill-dir", str(self.backfill_dir), "--out", str(ROOT / "study" / "x.json")]
                self.assertEqual(S.main(inside), 1)
        self.assertFalse((ROOT / "study" / "x.json").exists())

    def test_tampered_raw_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "bf"
            copy.mkdir()
            for path in self.backfill_dir.iterdir():
                (copy / path.name).write_bytes(path.read_bytes())
            target = copy / f"{BF.unit_stem('SPY', END)}{BF.RAW_SUFFIX}"
            target.write_bytes(target.read_bytes().replace(b'"c":', b'"c": ', 1))
            with self.assertRaises(S.StudyError) as ctx:
                S.load_backfill_panel(copy)
        self.assertIn("US_ROTATION_STUDY_RAW_HASH_MISMATCH", str(ctx.exception))

    def test_missing_symbol_and_inside_repo_dir_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "bf"
            copy.mkdir()
            for path in self.backfill_dir.iterdir():
                if not path.name.startswith("XLU_"):
                    (copy / path.name).write_bytes(path.read_bytes())
            with self.assertRaises(S.StudyError) as ctx:
                S.load_backfill_panel(copy)
        self.assertEqual(str(ctx.exception), "US_ROTATION_STUDY_SYMBOL_MISSING:XLU")
        with self.assertRaises(S.StudyError):
            S.load_backfill_panel(ROOT / "study")


class LoaderEdgeTests(unittest.TestCase):
    def _unit(self, out, symbol, anchor, bars, start="2026-01-01", end="2026-12-31"):
        raw = json.dumps({"responses": {symbol: {"bars": bars}}}, sort_keys=True).encode()
        stem = f"{symbol}_{anchor}"
        (out / f"{stem}.raw.json").write_bytes(raw)
        (out / f"{stem}.manifest.json").write_text(json.dumps({
            "symbol": symbol, "anchor_date": anchor, "raw_file": f"{stem}.raw.json",
            "raw_sha256": S.sha256_bytes(raw), "range_start_date": start, "range_end_date": end,
            "projection_version": S.BACKFILL_PROJECTION_VERSION,
        }))

    def _bars(self, n, base=100.0, offset=0):
        days = _weekdays(dt.date(2026, 1, 5), dt.date(2026, 12, 31))[:n]
        return [{"t": f"{d.isoformat()}T04:00:00Z", "c": base + offset, "v": 10, "vw": base} for d in days]

    def test_later_anchor_wins_and_conflicts_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for symbol in S.STUDY_SYMBOLS:
                self._unit(out, symbol, "2026-12-31", self._bars(210))
                self._unit(out, symbol, "2026-06-30", self._bars(120, offset=1.0 if symbol == "XLK" else 0.0))
            panel, meta = S.load_backfill_panel(out)
        self.assertEqual(meta["revision_conflicts"], 120)
        self.assertEqual(set(panel["close"]["XLK"]), {100.0})
        self.assertEqual(panel["dollar_vol"]["XLK"][0], 1000.0)

    def test_bar_after_anchor_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for symbol in S.STUDY_SYMBOLS:
                self._unit(out, symbol, "2026-03-01", self._bars(210))
            with self.assertRaises(S.StudyError) as ctx:
                S.load_backfill_panel(out)
        self.assertIn("US_ROTATION_STUDY_LOOKAHEAD_VIOLATION", str(ctx.exception))

    def test_short_history_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for symbol in S.STUDY_SYMBOLS:
                self._unit(out, symbol, "2026-12-31", self._bars(129))
            with self.assertRaises(S.StudyError) as ctx:
                S.load_backfill_panel(out)
        self.assertEqual(str(ctx.exception), "US_ROTATION_STUDY_SESSIONS_INSUFFICIENT:129")

    def test_mixed_ranges_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for symbol in S.STUDY_SYMBOLS:
                self._unit(out, symbol, "2026-12-31", self._bars(210), start="2026-01-02" if symbol == "SPY" else "2026-01-01")
            with self.assertRaises(S.StudyError) as ctx:
                S.load_backfill_panel(out)
        self.assertEqual(str(ctx.exception), "US_ROTATION_STUDY_RANGE_INCONSISTENT")


class ArtifactSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(3)
        T = 230
        dates = [d.isoformat() for d in _weekdays(dt.date(2025, 9, 1), dt.date(2026, 9, 30))][:T]
        close = {}
        for s in S.STUDY_SYMBOLS:
            level, series = 100.0, []
            for _ in range(T):
                level *= math.exp(rng.gauss(0, 0.01))
                series.append(level)
            close[s] = series
        panel = {"dates": dates, "close": close, "dollar_vol": {s: [1.0 + rng.random() for _ in range(T)] for s in S.STUDY_SYMBOLS}}
        meta = {"source": "synthetic", "unit_count": 36, "study_symbols": list(S.STUDY_SYMBOLS),
                "range_start_date": dates[0], "range_end_date": dates[-1], "first_session": dates[0],
                "last_session": dates[-1], "session_count": T, "revision_conflicts": 0,
                "large_daily_move_counts": {}, "unit_raw_sha256": {"SPY_2026-09-30": "0" * 64},
                "input_set_sha256": "0" * 64}
        cls.artifact = S.build_artifact(panel, meta, None)

    def _mutated(self, mutate):
        doc = json.loads(json.dumps(self.artifact))
        mutate(doc)
        return doc

    def _rejects(self, mutate, code_prefix):
        with self.assertRaises(S.StudyError) as ctx:
            S.validate_artifact(self._mutated(mutate))
        self.assertTrue(str(ctx.exception).startswith(code_prefix), str(ctx.exception))

    def test_valid_artifact_passes(self):
        S.validate_artifact(self.artifact)
        self.assertEqual(set(self.artifact), S.TOP_LEVEL_KEYS)

    def test_price_like_fields_are_rejected(self):
        for key in ("closes", "close", "prices", "bars", "volume", "vwap", "returns_series", "c", "ohlc"):
            self._rejects(lambda d, k=key: d["inputs"].__setitem__(k, 1), "US_ROTATION_STUDY_ARTIFACT_FORBIDDEN_FIELD")

    def test_numeric_arrays_are_rejected(self):
        self._rejects(lambda d: d["r5_rebalance"].__setitem__("excess_by_rebalance", [0.1, -0.2, 0.3, 0.05]),
                      "US_ROTATION_STUDY_ARTIFACT_NUMERIC_ARRAY")
        self._rejects(lambda d: d["events"]["R3_WATCH"].__setitem__("fx", [[0.1, 0.2]]),
                      "US_ROTATION_STUDY_ARTIFACT_NESTED_LIST")
        self._rejects(lambda d: d["inputs"].__setitem__("labels", ["x"] * 25),
                      "US_ROTATION_STUDY_ARTIFACT_LIST_TOO_LONG")

    def test_date_keyed_or_date_heavy_content_is_rejected(self):
        self._rejects(lambda d: d["inputs"].__setitem__("by_day", {"2026-09-01": 0.01}),
                      "US_ROTATION_STUDY_ARTIFACT_DATE_KEYED_MAPPING")
        self._rejects(lambda d: d["inputs"].__setitem__("event_dates", [f"2026-09-{i:02d}" for i in range(1, 10)]),
                      "US_ROTATION_STUDY_ARTIFACT_TOO_MANY_DATES")

    def test_structure_parameters_authority_and_preregistration_are_locked(self):
        self._rejects(lambda d: d.__setitem__("per_day", {}), "US_ROTATION_STUDY_ARTIFACT_TOP_LEVEL_KEYS_INVALID")
        self._rejects(lambda d: d["parameters"].__setitem__("round_trip_cost", 0.0005),
                      "US_ROTATION_STUDY_ARTIFACT_PARAMETERS_CHANGED")
        self._rejects(lambda d: d["authority"].__setitem__("capital_authority", True),
                      "US_ROTATION_STUDY_ARTIFACT_AUTHORITY_INVALID")
        self._rejects(lambda d: d["preregistration"].__setitem__("sha256", "0" * 64),
                      "US_ROTATION_STUDY_ARTIFACT_PREREGISTRATION_INVALID")
        self._rejects(lambda d: d["events"].pop("R2_ENTER"), "US_ROTATION_STUDY_ARTIFACT_CANDIDATES_INVALID")
        self._rejects(lambda d: d["inputs"].__setitem__("nan", float("nan")), "US_ROTATION_STUDY_ARTIFACT_NON_FINITE")

    def test_oversized_artifact_is_rejected(self):
        self._rejects(lambda d: d["inputs"].__setitem__("unit_raw_sha256", {f"U{i}": "0" * 64 for i in range(4000)}),
                      "US_ROTATION_STUDY_ARTIFACT_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
