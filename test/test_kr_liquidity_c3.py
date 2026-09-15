#!/usr/bin/env python3
"""Offline regression for universe/kr_liquidity_c3.py (KR T2 C3).

No network, no clock. Thresholds are proved to come from the committed,
sha-verified ratification record; a tampered, missing or re-shaped record
fails closed to UNKNOWN. Status vocabulary: NOT_EVALUATED (fewer than 20
sessions) vs UNKNOWN (missing/stale input) vs FAIL (confirmed negative).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors import krx_price_history as COLLECTOR  # noqa: E402
from universe import kr_liquidity_c3 as M  # noqa: E402
from universe import price_history_store as STORE  # noqa: E402

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
        out = M.evaluate_symbol(CODE, calendar_sessions=WINDOW, available_sessions=WINDOW,
                                rows=rows("9" * 12, "99999"), required_session=REQUIRED,
                                status_exclusion=flags(), rule_root=self._temp_root(remove=True))
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertEqual(out["reasons"], ["RATIFIED_RULE_UNAVAILABLE"])

    def test_caller_cannot_pass_thresholds(self):
        import inspect
        params = inspect.signature(M.evaluate_symbol).parameters
        self.assertNotIn("rule", params)
        self.assertNotIn("rule", inspect.signature(M.evaluate_from_store).parameters)


class EvaluationTests(unittest.TestCase):
    def run_case(self, **kw):
        args = dict(calendar_sessions=WINDOW, available_sessions=WINDOW, rows=rows(),
                    required_session=REQUIRED, status_exclusion=flags())
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

    def test_missing_store_session_is_unknown_not_not_evaluated(self):
        # empty store, short store, one EMPTY session inside: all missing history
        for available in ([], WINDOW[5:], WINDOW[:10] + WINDOW[11:]):
            out = self.run_case(available_sessions=available,
                                rows=rows(days=available))
            self.assertEqual(out["status"], "UNKNOWN", available)
            self.assertTrue(out["reasons"][0].startswith("PRICE_HISTORY_SESSION_MISSING:"))
            self.assertIsNone(out["avg_traded_value_krw"])

    def test_symbol_listed_inside_window_is_not_evaluated(self):
        out = self.run_case(rows=rows(days=WINDOW[5:]))
        self.assertEqual(out["status"], "NOT_EVALUATED")
        self.assertEqual(out["reasons"], ["SYMBOL_LISTING_HISTORY_SHORTER_THAN_WINDOW"])
        self.assertIsNone(out["avg_traded_value_krw"])

    def test_interior_or_trailing_symbol_gap_is_unknown_never_averaged(self):
        for days in (WINDOW[:7] + WINDOW[8:], WINDOW[:-1]):
            out = self.run_case(rows=rows(days=days))
            self.assertEqual(out["status"], "UNKNOWN")
            self.assertIsNone(out["avg_traded_value_krw"])
        self.assertEqual(self.run_case(rows=[])["reasons"], ["SYMBOL_NOT_IN_PRICE_HISTORY"])

    def test_window_must_be_last_twenty_ending_at_required_session(self):
        with self.assertRaisesRegex(M.KrLiquidityC3Error, "CALENDAR_WINDOW_NOT_LAST_20"):
            self.run_case(required_session="20260915")
        with self.assertRaisesRegex(M.KrLiquidityC3Error, "CALENDAR_WINDOW_NOT_LAST_20"):
            self.run_case(calendar_sessions=WINDOW[1:], available_sessions=WINDOW[1:], rows=rows(days=WINDOW[1:]))

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
            self.run_case(calendar_sessions=list(reversed(WINDOW)))
        with self.assertRaises(M.KrLiquidityC3Error):
            M.evaluate_symbol("bad code", calendar_sessions=WINDOW, available_sessions=WINDOW, rows=[],
                              required_session=REQUIRED, status_exclusion=None)

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


def provider_payload(codes, bas_dd, *, empty=False, value="1000000000"):
    rows_ = [] if empty else [{
        "BAS_DD": bas_dd, "ISU_CD": code, "ISU_NM": f"FIXTURE{code}", "MKT_NM": "KOSPI",
        "SECT_TP_NM": "", "TDD_CLSPRC": "1000", "CMPPREVDD_PRC": "5", "FLUC_RT": "0.50",
        "TDD_OPNPRC": "995", "TDD_HGPRC": "1010", "TDD_LWPRC": "990", "ACC_TRDVOL": "1000000",
        "ACC_TRDVAL": value, "MKTCAP": "8941100000", "LIST_SHRS": "8941100",
    } for code in codes]
    return json.dumps({"OutBlock_1": rows_}, separators=(",", ":")).encode("utf-8")


class RealStoreCalendarWindowTests(unittest.TestCase):
    """20260901-EMPTY reproduction on a real store with the real 2026 calendar."""

    AS_OF = "2026-09-15T01:00:00Z"

    @classmethod
    def setUpClass(cls):
        cls.contract = COLLECTOR.load_contract()

    def write(self, store, bas_dd, *, empty=False, value="1000000000", retrieved="2026-09-15T00:30:00Z"):
        raw = {"kospi": provider_payload([CODE], bas_dd, empty=empty, value=value)}
        status = COLLECTOR.classify(raw, bas_dd)
        rows_ = COLLECTOR.derive_compact_rows(raw, bas_dd, self.contract) if status == "OK" else []
        compact = COLLECTOR.compact_bytes(rows_)
        parts = [{
            "part_id": "kospi", "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
            "http_status": 200, "row_count": len(rows_), "raw_relpath": "raw/kospi.json.gz",
            "raw_sha256": COLLECTOR.digest(raw["kospi"]), "raw_byte_count": len(raw["kospi"]),
        }]
        manifest = COLLECTOR.build_manifest(
            market="KR", bas_dd=bas_dd, status=status, parts=parts, compact=compact,
            compact_row_count=len(rows_), pit_class="HISTORICAL_BACKFILL",
            attempts=[{"attempt_no": 1, "kind": "BACKFILL", "requested_at_utc": retrieved,
                       "retrieved_at_utc": retrieved, "http_status": 200,
                       "row_count": len(rows_), "outcome": status}],
            public_code_commit="0" * 40, contract=self.contract,
            first_available_observed_at_utc=retrieved if status == "OK" else None,
        )
        store.write_session("KR", manifest, raw_by_part=raw, compact=compact)

    def build(self, tmp):
        store = STORE.PriceHistoryStore(tmp, contract=self.contract)
        # 21 calendar sessions ending 20260914, with 20260901 EMPTY and a very
        # large 20260817-side session that would enter a stretched window.
        sessions = COLLECTOR.open_sessions_ending(REQUIRED, 21, self.contract)
        self.assertEqual(sessions[1:], WINDOW)
        for day in sessions:
            if day == "20260901":
                self.write(store, day, empty=True, retrieved="2026-09-01T09:13:00Z")
            elif day == sessions[0]:
                self.write(store, day, value="999999999999")
            else:
                self.write(store, day)
        return store

    def test_empty_session_inside_window_is_unknown_not_stretched(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.build(tmp)
            stretched = store.session_window("KR", 20, self.AS_OF)
            self.assertNotIn("20260901", stretched)
            self.assertEqual(stretched[0], "20260814")  # what the old window averaged over
            out = M.evaluate_from_store(store, CODE, as_of_utc=self.AS_OF, required_session=REQUIRED,
                                        status_exclusion=flags())
            self.assertEqual(out["status"], "UNKNOWN", out)
            self.assertEqual(out["missing_sessions"], ["20260901"])
            self.assertIsNone(out["avg_traded_value_krw"])

    def test_empty_session_upgraded_to_ok_then_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.build(tmp)
            self.write(store, "20260901", retrieved="2026-09-02T01:47:00Z")
            manifest = store.manifest("KR", "20260901")
            self.assertEqual(manifest["status"], "OK")
            self.assertEqual([a["outcome"] for a in manifest["attempts"]], ["EMPTY", "OK"])
            self.assertEqual([a["attempt_no"] for a in manifest["attempts"]], [1, 2])
            self.assertEqual(manifest["first_available_observed_at_utc"], "2026-09-02T01:47:00Z")
            out = M.evaluate_from_store(store, CODE, as_of_utc=self.AS_OF, required_session=REQUIRED,
                                        status_exclusion=flags())
            self.assertEqual(out["status"], "PASS", out)
            self.assertEqual(out["avg_traded_value_krw"], "1000000000")
            # OK is never overwritten
            with self.assertRaisesRegex(STORE.PriceHistoryStoreError, "SESSION_ALREADY_STORED"):
                self.write(store, "20260901", value="1")

    def test_empty_reattempt_appends_attempt_and_stays_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.build(tmp)
            self.write(store, "20260901", empty=True, retrieved="2026-09-01T09:40:00Z")
            manifest = store.manifest("KR", "20260901")
            self.assertEqual(manifest["status"], "EMPTY")
            self.assertEqual(len(manifest["attempts"]), 2)
            self.assertIsNone(manifest["first_available_observed_at_utc"])

    def test_session_not_yet_available_at_instant_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self.build(tmp)
            self.write(store, "20260901", retrieved="2026-09-15T02:00:00Z")
            out = M.evaluate_from_store(store, CODE, as_of_utc=self.AS_OF, required_session=REQUIRED,
                                        status_exclusion=flags())
            self.assertEqual(out["status"], "UNKNOWN")
            self.assertEqual(out["missing_sessions"], ["20260901"])

    def test_calendar_gap_is_unknown_not_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = STORE.PriceHistoryStore(tmp, contract=self.contract)
            out = M.evaluate_from_store(store, CODE, as_of_utc="2025-06-10T01:00:00Z",
                                        required_session="20250609", status_exclusion=flags())
            self.assertEqual(out["status"], "UNKNOWN")
            self.assertTrue(out["reasons"][0].startswith("CALENDAR_WINDOW_UNAVAILABLE:"), out)


class RequiredSessionStalenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = COLLECTOR.load_contract()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = STORE.PriceHistoryStore(cls.tmp.name, contract=cls.contract)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_latest_collectable_session_follows_publication_and_calendar(self):
        cases = {
            "2026-09-15T00:09:00Z": "20260911",  # Tue 09:09 KST: Monday not yet collectable
            "2026-09-15T00:10:00Z": "20260914",  # Tue 09:10 KST: Monday collectable
            "2026-09-14T01:00:00Z": "20260911",  # Mon 10:00 KST -> Friday
            "2026-09-13T12:00:00Z": "20260911",  # Sunday -> Friday
            "2026-08-18T00:30:00Z": "20260814",  # Tue after 08-17 holiday, before 09:10 -> Fri 08-14
            "2026-08-18T01:00:00Z": "20260814",  # 08-17 is closed, so still Fri 08-14
        }
        for as_of, expected in cases.items():
            self.assertEqual(M.latest_collectable_session(self.store, as_of), expected, as_of)

    def test_stale_required_session_is_unknown(self):
        """Reviewer case: required 20260910 evaluated at 2026-09-30 -> UNKNOWN."""
        out = M.evaluate_from_store(self.store, CODE, as_of_utc="2026-09-30T03:00:00Z",
                                    required_session="20260910", status_exclusion=flags(session="2026-09-10"))
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertTrue(out["reasons"][0].startswith(
            "REQUIRED_SESSION_NOT_LATEST_COLLECTABLE:required=20260910:latest="), out)

    def test_not_yet_published_required_session_is_unknown(self):
        out = M.evaluate_from_store(self.store, CODE, as_of_utc="2026-09-15T00:09:00Z",
                                    required_session="20260914", status_exclusion=flags())
        self.assertEqual(out["status"], "UNKNOWN")
        self.assertIn("latest=20260911", out["reasons"][0])

    def test_stale_even_with_a_complete_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = RealStoreCalendarWindowTests()
            helper.contract = self.contract
            store = STORE.PriceHistoryStore(tmp, contract=self.contract)
            for day in COLLECTOR.open_sessions_ending("20260910", 20, self.contract):
                helper.write(store, day, retrieved="2026-09-11T01:00:00Z")
            fresh = M.evaluate_from_store(store, CODE, as_of_utc="2026-09-11T01:00:00Z",
                                          required_session="20260910",
                                          status_exclusion=flags(session="2026-09-10"))
            self.assertEqual(fresh["status"], "PASS", fresh)
            stale = M.evaluate_from_store(store, CODE, as_of_utc="2026-09-30T03:00:00Z",
                                          required_session="20260910",
                                          status_exclusion=flags(session="2026-09-10"))
            self.assertEqual(stale["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
