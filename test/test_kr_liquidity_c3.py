#!/usr/bin/env python3
"""Offline regression for universe/kr_liquidity_c3.py (KR T2 C3).

No network, no clock. Thresholds are proved to come from the committed,
sha-verified ratification record; a tampered, missing or re-shaped record
fails closed to UNKNOWN. Status vocabulary: NOT_EVALUATED (fewer than 20
sessions) vs UNKNOWN (missing/stale input) vs FAIL (confirmed negative).
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universe import kr_liquidity_c3 as M  # noqa: E402

# 20 open KRX sessions ending 2026-09-14 (2026 official capture: no holiday in
# this span; weekends skipped).
WINDOW = [
    "20260818", "20260819", "20260820", "20260821", "20260824", "20260825",
    "20260826", "20260827", "20260828", "20260831", "20260901", "20260902",
    "20260903", "20260904", "20260907", "20260908", "20260909", "20260910",
    "20260911", "20260914",
]
REQUIRED = "20260914"
CODE = "005930"


def rows(value="1000000000", close="1000", days=WINDOW, code=CODE):
    return [{"bas_dd": d, "code": code, "value": value, "close": close} for d in days]


def flags(result="CLEAR", code=CODE, session="2026-09-14", reasons=()):
    return {
        "schema_version": "kr_c3_status_exclusion_result/1",
        "short_code": code,
        "required_session": session,
        "result": result,
        "reasons": list(reasons),
        "ratification_id": "PAPER-LIQUIDITY-KR-US-V1-20260914",
    }


class RatifiedRuleBindingTests(unittest.TestCase):
    def test_committed_record_binds_the_ratified_numbers(self):
        described = M.describe_ratified_kr_rule()
        self.assertEqual(described["status"], "RATIFIED", described)
        rule = described["rule"]
        record = json.loads((ROOT / M.RATIFICATION_RELPATH).read_text(encoding="utf-8"))
        self.assertEqual(rule["avg_traded_value_krw_min"], record["KR"]["avg_traded_value_20_sessions_krw_min"])
        self.assertEqual(rule["last_close_krw_min"], record["KR"]["last_close_krw_min"])
        self.assertEqual(rule["exclude"], record["KR"]["exclude"])
        self.assertEqual(rule["window_sessions"], 20)

    def test_module_source_holds_no_threshold_number(self):
        source = (ROOT / "universe" / "kr_liquidity_c3.py").read_text(encoding="utf-8")
        self.assertNotIn("1000000000", source)
        self.assertNotIn('"1000"', source)

    def _temp_root(self, mutate=None, remove=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        target = Path(tmp.name) / M.RATIFICATION_RELPATH
        target.parent.mkdir(parents=True)
        if not remove:
            shutil.copyfile(ROOT / M.RATIFICATION_RELPATH, target)
            if mutate:
                mutate(target)
        return Path(tmp.name)

    def test_tampered_record_fails_closed(self):
        def lower_threshold(path):
            path.write_text(path.read_text(encoding="utf-8").replace('"1000000000"', '"100000000"'), encoding="utf-8")
        root = self._temp_root(lower_threshold)
        self.assertEqual(M.describe_ratified_kr_rule(root)["status"], "RATIFICATION_HASH_MISMATCH")
        self.assertIsNone(M.load_ratified_kr_rule(root))

    def test_missing_record_fails_closed(self):
        self.assertIsNone(M.load_ratified_kr_rule(self._temp_root(remove=True)))

    def test_no_rule_means_unknown_even_with_perfect_data(self):
        out = M.evaluate_symbol(CODE, window_sessions=WINDOW, rows=rows("9" * 12, "99999"),
                                required_session=REQUIRED, status_exclusion=flags(), rule=None)
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(out["reasons"], ["RATIFIED_RULE_UNAVAILABLE"])


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rule = M.load_ratified_kr_rule()

    def run_case(self, **kw):
        args = dict(window_sessions=WINDOW, rows=rows(), required_session=REQUIRED,
                    status_exclusion=flags(), rule=self.rule)
        args.update(kw)
        return M.evaluate_symbol(CODE, **args)

    def test_exact_thresholds_pass(self):
        out = self.run_case()
        self.assertEqual(out["status"], "PASS", out)
        self.assertEqual(out["avg_traded_value_krw"], "1000000000")
        self.assertEqual(out["last_close_krw"], "1000")

    def test_average_is_over_exactly_twenty_sessions(self):
        data = rows("0")
        data[-1]["value"] = "20000000000"  # one big day: mean = 1,000,000,000
        self.assertEqual(self.run_case(rows=data)["status"], "PASS")
        data[-1]["value"] = "19999999999"
        out = self.run_case(rows=data)
        self.assertEqual(out["status"], "FAIL")
        self.assertIn("AVG_TRADED_VALUE_BELOW_MIN", out["reasons"])

    def test_last_close_below_floor_fails(self):
        out = self.run_case(rows=rows(close="999"))
        self.assertEqual(out["status"], "FAIL")
        self.assertIn("LAST_CLOSE_BELOW_MIN", out["reasons"])

    def test_halt_or_managed_issue_fails(self):
        out = self.run_case(status_exclusion=flags("EXCLUDED", reasons=["KIS_TRADING_HALT"]))
        self.assertEqual(out["status"], "FAIL")
        self.assertEqual(out["status_exclusion_status"], "FAIL")

    def test_missing_or_mismatched_flags_are_unknown(self):
        for status_exclusion, reason in (
            (None, "STATUS_FLAGS_MISSING"),
            (flags("UNKNOWN", reasons=["MASTER_SESSION_STALE"]), "STATUS_FLAGS_UNKNOWN:MASTER_SESSION_STALE"),
            (flags(session="2026-09-11"), "STATUS_FLAGS_SESSION_MISMATCH"),
            (flags(code="000660"), "STATUS_FLAGS_SYMBOL_MISMATCH"),
            ({**flags(), "ratification_id": "OTHER"}, "STATUS_FLAGS_RATIFICATION_MISMATCH"),
        ):
            out = self.run_case(status_exclusion=status_exclusion)
            self.assertEqual(out["status"], "UNKNOWN", reason)
            self.assertIn(reason, out["reasons"])

    def test_fail_beats_unknown(self):
        out = self.run_case(rows=rows(close="999"), status_exclusion=None)
        self.assertEqual(out["status"], "FAIL")

    def test_fewer_than_twenty_store_sessions_is_not_evaluated(self):
        out = self.run_case(window_sessions=WINDOW[1:], rows=rows(days=WINDOW[1:]))
        self.assertEqual(out["status"], "NOT_EVALUATED")
        self.assertEqual(out["reasons"], ["STORE_HISTORY_SHORTER_THAN_WINDOW"])
        self.assertEqual(self.run_case(window_sessions=[], rows=[])["status"], "NOT_EVALUATED")

    def test_symbol_listed_inside_window_is_not_evaluated(self):
        out = self.run_case(rows=rows(days=WINDOW[5:]))
        self.assertEqual(out["status"], "NOT_EVALUATED")
        self.assertIsNone(out["avg_traded_value_krw"])

    def test_interior_or_trailing_gap_is_unknown_never_averaged(self):
        for days in (WINDOW[:7] + WINDOW[8:], WINDOW[:-1]):
            out = self.run_case(rows=rows(days=days))
            self.assertEqual(out["status"], "UNKNOWN")
            self.assertIsNone(out["avg_traded_value_krw"])
        self.assertEqual(self.run_case(rows=[])["reasons"], ["SYMBOL_NOT_IN_PRICE_HISTORY"])

    def test_stale_store_is_unknown(self):
        out = self.run_case(required_session="20260915")
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(out["reasons"], ["PRICE_HISTORY_STALE"])
        # stale beats short: a short stale store is still UNKNOWN, not NOT_EVALUATED
        out = self.run_case(window_sessions=WINDOW[:5], rows=rows(days=WINDOW[:5]))
        self.assertEqual(out["status"], "UNKNOWN")

    def test_unparseable_provider_value_is_unknown(self):
        data = rows()
        data[3]["value"] = "-"
        out = self.run_case(rows=data)
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertIn("TRADED_VALUE_MISSING", out["reasons"])

    def test_malformed_arguments_raise(self):
        with self.assertRaises(M.KrLiquidityC3Error):
            self.run_case(rows=rows(code="000660"))
        with self.assertRaises(M.KrLiquidityC3Error):
            self.run_case(window_sessions=list(reversed(WINDOW)))
        with self.assertRaises(M.KrLiquidityC3Error):
            M.evaluate_symbol("bad code", window_sessions=WINDOW, rows=[], required_session=REQUIRED,
                              status_exclusion=None, rule=self.rule)

    def test_evaluate_from_store_uses_window_and_series(self):
        class FakeStore:
            calls = []

            def session_window(self, market, n, t):
                self.calls.append(("window", market, n, t))
                return list(WINDOW)

            def series(self, market, code, n, t):
                self.calls.append(("series", market, code, n, t))
                return rows()

        store = FakeStore()
        out = M.evaluate_from_store(store, CODE, as_of_utc="2026-09-14T08:00:00Z",
                                    required_session=REQUIRED, status_exclusion=flags(), rule=self.rule)
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(store.calls[0], ("window", "KR", 20, "2026-09-14T08:00:00Z"))

    def test_public_summary_is_counts_only(self):
        results = [self.run_case(), self.run_case(rows=rows(close="999")), self.run_case(status_exclusion=None)]
        summary = M.public_summary(results)
        self.assertEqual(summary["status_counts"], {"PASS": 1, "FAIL": 1, "UNKNOWN": 1, "NOT_EVALUATED": 0})
        text = json.dumps(summary)
        self.assertNotIn(CODE, text)
        self.assertNotIn("avg_traded_value", text)
        self.assertNotIn("last_close", text)

    def test_authority_all_false(self):
        self.assertTrue(all(v is False for v in self.run_case()["authority"].values()))


if __name__ == "__main__":
    unittest.main()
