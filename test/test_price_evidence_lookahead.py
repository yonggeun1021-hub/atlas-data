#!/usr/bin/env python3
"""P8-10 real evidence assembly -- hard anti-lookahead regression.

Reuses `replay.lookahead_gate` (PR #210) unchanged -- this file does not
reimplement PIT-timing logic, it proves `decision/price_evidence.py`
actually calls it and that the real committed evidence a genuine `--out`
run would see never smuggles a future-dated snapshot into an assembled
price_reflection input.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import price_evidence as pe  # noqa: E402
from replay.lookahead_gate import LookaheadViolation  # noqa: E402


def _write_korea_leadership_packet(base_dir: Path, observation_date: str, generated_at: str,
                                    kospi_gross: str = "1.01", kosdaq_gross: str = "1.02") -> None:
    day_dir = base_dir / observation_date
    day_dir.mkdir(parents=True)
    (day_dir / "packet.json").write_text(json.dumps({
        "generated_at": generated_at,
        "leadership_packet": {
            "observation_date": observation_date,
            "relative_strength_observations": [
                {"role": "KOSPI_BENCHMARK", "series_identity": "KOSPI::코스피",
                 "cumulative_gross_return": kospi_gross},
                {"role": "KOSDAQ_BENCHMARK", "series_identity": "KOSDAQ::코스닥",
                 "cumulative_gross_return": kosdaq_gross},
            ],
        },
    }), encoding="utf-8")


class KoreaBenchmarkSeriesLookaheadTests(unittest.TestCase):
    def test_a_session_captured_after_decision_date_is_excluded_from_live_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_korea_leadership_packet(base, "2026-08-10", "2026-08-10T09:00:00Z")
            _write_korea_leadership_packet(base, "2026-08-11", "2026-08-12T09:00:00Z")  # captured AFTER 08-11
            series = pe.KoreaBenchmarkSeries.load("KOSPI", base_dir=base)
            self.assertEqual(series.live_dates_at_or_before("2026-08-11"), ["2026-08-10"])
            self.assertEqual(series.live_dates_at_or_before("2026-08-12"), ["2026-08-10", "2026-08-11"])

    def test_index_levels_never_include_a_future_captured_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_korea_leadership_packet(base, "2026-08-10", "2026-08-10T09:00:00Z", kospi_gross="1.10")
            _write_korea_leadership_packet(base, "2026-08-11", "2026-08-30T09:00:00Z", kospi_gross="9.99")
            series = pe.KoreaBenchmarkSeries.load("KOSPI", base_dir=base)
            levels = series.index_levels("2026-08-11")
            self.assertEqual(set(levels), {"2026-08-10"})
            self.assertNotIn("2026-08-11", levels)  # the 9.99x factor must never leak in

    def test_capture_dates_at_or_before_never_exceeds_decision_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_korea_leadership_packet(base, "2026-08-10", "2026-08-10T09:00:00Z")
            _write_korea_leadership_packet(base, "2026-08-11", "2026-08-15T09:00:00Z")
            series = pe.KoreaBenchmarkSeries.load("KOSPI", base_dir=base)
            for cd in series.capture_dates_at_or_before("2026-08-11"):
                self.assertLessEqual(cd, "2026-08-11")

    def test_real_committed_korea_leadership_context_is_entirely_same_day_backfilled(self):
        """Real-data version of the above: every packet currently committed
        in this repo has generated_at dated 2026-08-22 regardless of its own
        observation_date (a documented, real backfill quirk -- see
        price_evidence.py's module docstring), so an earlier decision_date
        must see ZERO live-known benchmark sessions."""
        series = pe.KoreaBenchmarkSeries.load("KOSPI")
        self.assertGreater(len(series.dates()), 0)  # sanity: real data is actually loaded
        self.assertEqual(series.live_dates_at_or_before("2026-08-14"), [])
        self.assertEqual(series.index_levels("2026-08-14"), {})


class LookaheadGateReuseTests(unittest.TestCase):
    """Confirms decision/price_evidence.py actually calls the shared,
    unmodified replay.lookahead_gate primitive (not a local reimplementation)."""

    def test_module_imports_the_shared_lookahead_gate(self):
        import ast
        source = (ROOT / "decision" / "price_evidence.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
        self.assertIn("replay", imported_names)
        self.assertIn("lg.assert_no_signal_lookahead", source)

    def test_gate_raises_on_a_hand_fed_future_evidence_date(self):
        from replay import lookahead_gate as lg
        with self.assertRaises(LookaheadViolation):
            lg.assert_no_signal_lookahead("2026-08-11", ["2026-08-12"], label="synthetic")


class RealEvidenceEndToEndLookaheadSweepTests(unittest.TestCase):
    """Sweeps assemble_price_evidence() across every real, distinct KRX
    snapshot capture_date in this repo and asserts the resulting price_as_of
    (and therefore every return-window figure derived from it) is never
    dated after its own decision_date."""

    @classmethod
    def setUpClass(cls):
        from replay import evidence_index as ei
        cls.snapshot_capture_dates = sorted({s.capture_date for s in ei.find_krx_snapshots()})

    def test_price_as_of_never_exceeds_decision_date_across_every_real_capture_date(self):
        checked = 0
        for decision_date in self.snapshot_capture_dates:
            for subject in ("298040.KS", "267260.KS"):
                ev = pe.assemble_krx_stock_evidence(subject[:-3], decision_date)
                if ev["price_as_of"] is None:
                    continue
                checked += 1
                self.assertLessEqual(ev["price_as_of"][:10], decision_date)
        self.assertGreater(checked, 0, "sanity: at least one real (subject, decision_date) pair should resolve")

    def test_tsm_price_as_of_never_exceeds_decision_date(self):
        for decision_date in ("2026-08-13", "2026-08-20", "2026-08-22"):
            ev = pe.assemble_us_equity_evidence("TSM", decision_date)
            if ev["price_as_of"] is None:
                continue
            self.assertLessEqual(ev["price_as_of"][:10], decision_date)

    def test_btc_price_as_of_never_exceeds_decision_date_and_window_grows_monotonically(self):
        from replay import evidence_index as ei
        from replay import price_series as ps
        btc_capture_dates = sorted({s.capture_date for s in ei.find_btc_snapshots()})
        series = ps.build_btc_series(ei.find_btc_snapshots())
        counts = []
        for decision_date in btc_capture_dates:
            ev = pe.assemble_crypto_evidence("BTC", decision_date)
            if ev["price_as_of"] is not None:
                self.assertLessEqual(ev["price_as_of"][:10], decision_date)
            counts.append(len(series.live_trading_dates_at_or_before(decision_date)))
        self.assertEqual(counts, sorted(counts))

    def test_earlier_decision_dates_see_a_strictly_non_decreasing_evidence_window(self):
        """As decision_date advances through the real committed history, the
        live-known window can only grow or stay flat, never shrink or reach
        into the future -- checked via the count of live trading dates."""
        from replay import evidence_index as ei
        from replay import price_series as ps
        snapshots = ei.find_krx_snapshots()
        series = ps.build_krx_series("298040", snapshots)
        counts = [
            len(series.live_trading_dates_at_or_before(d))
            for d in self.snapshot_capture_dates
        ]
        self.assertEqual(counts, sorted(counts))


if __name__ == "__main__":
    unittest.main()
