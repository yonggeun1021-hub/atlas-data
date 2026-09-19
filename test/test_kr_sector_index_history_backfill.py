#!/usr/bin/env python3
"""KR sector index history backfill regression (offline, synthetic responses).

No network: every provider call goes through an in-memory opener. Covers the
request budget and range guards, per-market request shape, write-once resume,
fail-closed stops on HTTP 401/403/429 and KRX error codes, transient retry and
blocking, session classification from responses (never weekday inference),
official-calendar cross-checks, the repository output boundary, and that the
receipt passes the aggregate-only public validator without carrying the key
or any index value. `run_all.py` executes this file directly.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


B = _load("kr_sector_index_history_backfill_under_test", "collectors/kr_sector_index_history_backfill.py")
S = _load("kr_rotation_event_study_for_backfill_test", "rotation/kr_rotation_event_study.py")
CAL = B.CALENDAR

KEY = "synthetic-krx-key-never-real-0123456789"
TODAY = dt.date(2026, 9, 15)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def synthetic_rows(market: str, bas_dd: str, identities: dict, *, skip=()):
    rng = random.Random(f"{market}:{bas_dd}")
    rows = [{
        "BAS_DD": bas_dd, "IDX_CLSS": market, "IDX_NM": f"{'코스피' if market == 'KOSPI' else '코스닥'} (외국주포함)",
        "CLSPRC_IDX": "-", "ACC_TRDVAL": "1,000",
    }]
    for identity in [identities[market]["benchmark"], *identities[market]["members"]]:
        if identity in skip:
            continue
        name = identity.split("::", 1)[1]
        rows.append({
            "BAS_DD": bas_dd, "IDX_CLSS": market, "IDX_NM": name,
            "CLSPRC_IDX": f"{rng.uniform(500, 5000):,.2f}",
            "ACC_TRDVAL": f"{rng.randint(10**9, 10**12):,}",
        })
    rows.append({"BAS_DD": bas_dd, "IDX_CLSS": market, "IDX_NM": "비섹터 참고지수", "CLSPRC_IDX": "1,234.56", "ACC_TRDVAL": "5"})
    return rows


class Provider:
    """Synthetic KRX: holidays -> empty OutBlock_1; scripted failures per call index."""

    def __init__(self, identities, holidays=(), failures=None, override=None):
        self.identities = identities
        self.holidays = set(holidays)
        self.failures = dict(failures or {})
        self.override = override or {}
        self.calls = []

    def __call__(self, request, timeout=None):
        url = urlparse(request.full_url)
        bas_dd = parse_qs(url.query)["basDd"][0]
        market = "KOSPI" if url.path.endswith("/kospi_dd_trd") else "KOSDAQ"
        assert request.get_header("Auth_key") == KEY
        assert KEY not in request.full_url
        index = len(self.calls)
        self.calls.append((market, bas_dd))
        failure = self.failures.get(index)
        if failure is not None:
            return failure(request)
        if (market, bas_dd) in self.override:
            return FakeResponse(self.override[(market, bas_dd)])
        rows = [] if bas_dd in self.holidays else synthetic_rows(market, bas_dd, self.identities)
        return FakeResponse(json.dumps({"OutBlock_1": rows}, ensure_ascii=False).encode("utf-8"))


def http_error(code):
    def raiser(request):
        raise HTTPError(request.full_url, code, "synthetic", {}, io.BytesIO(b"{}"))
    return raiser


def url_error(request):
    raise URLError("synthetic outage")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def calendar_capture(year: int, holidays: list[str]) -> bytes:
    rows = []
    for iso in sorted(holidays):
        day = dt.date.fromisoformat(iso)
        rows.append({
            "calnd_dd": iso, "dy_tp_cd": CAL.DAY_CODES[day.weekday()], "calnd_dd_dy": iso,
            "kr_dy_tp": "휴장", "holdy_eng_nm": "Synthetic Holiday",
        })
    raw = json.dumps({"block1": rows}).encode("utf-8")
    bundle = {
        "schema_version": CAL.CAPTURE_SCHEMA, "provider_id": CAL.PROVIDER_ID, "market": "KOREA",
        "venue_scope": "KRX_ONLY", "year": year, "page_url": CAL.PAGE_URL, "otp_url": CAL.OTP_URL,
        "data_url": CAL.DATA_URL, "bld": CAL.BLD, "market_rule_url": CAL.MARKET_RULE_URL,
        "request": {"search_bas_yy": str(year), "gridTp": "KRX", "pagePath": "",
                    "network_operations": ["PAGE_GET", "OTP_GET", "HOLIDAY_POST"], "redirects_allowed": False},
        "capture_started_at": "2026-09-14T00:00:00Z", "response_received_at": "2026-09-14T00:00:01Z",
        "page_raw_sha256": "0" * 64, "otp_retained": False,
        "response": {"http_status": 200, "content_type": "application/json", "final_url": CAL.DATA_URL,
                     "redirect_count": 0, "raw_base64": base64.b64encode(raw).decode("ascii"),
                     "raw_sha256": hashlib.sha256(raw).hexdigest()},
        "authority": {"market_calendar_observation_only": True, "candidate_authorized": False,
                      "entry_authorized": False, "order_authorized": False, "trading_authorized": False,
                      "real_capital_authorized": False},
    }
    return CAL.canonical_bytes(bundle)


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.contract = B.load_contract()
        self.identities = B.ratified_identities()
        self.tmp = tempfile.TemporaryDirectory()
        self.records = Path(self.tmp.name) / "records"

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, start, end, calendars=None):
        return B.build_plan(start, end, contract=self.contract, calendars=calendars, today_kst=TODAY)

    def run_backfill(self, plan, provider, clock=None, **kwargs):
        clock = clock or Clock()
        return B.run_backfill(
            KEY, plan, self.records, contract=self.contract, identities=self.identities,
            opener=provider, sleep=clock.sleep, monotonic=clock.monotonic, **kwargs,
        )

    # ------------------------------------------------------------ contract and plan

    def test_endpoints_match_existing_market_signals_contract(self):
        signals = json.loads((ROOT / "config/korea_market_signals_contract.json").read_text())
        self.assertEqual(self.contract["endpoints"]["KOSPI"], signals["index_endpoints"]["kospi"])
        self.assertEqual(self.contract["endpoints"]["KOSDAQ"], signals["index_endpoints"]["kosdaq"])
        self.assertTrue(self.contract["endpoints"]["KOSPI"].endswith("/svc/apis/idx/kospi_dd_trd"))
        self.assertEqual(self.contract["documented_rate_or_daily_limit"], "NOT_DOCUMENTED_IN_REPOSITORY")
        self.assertIs(self.contract["data_use_boundary"]["derived_closes_committable"], False)

    def test_ratified_scope_is_46_sectors_with_own_benchmarks(self):
        self.assertEqual(len(self.identities["KOSPI"]["members"]), 24)
        self.assertEqual(len(self.identities["KOSDAQ"]["members"]), 22)
        self.assertEqual(self.identities["KOSPI"]["benchmark"], "KOSPI::코스피")
        self.assertEqual(self.identities["KOSDAQ"]["benchmark"], "KOSDAQ::코스닥")

    def test_three_year_plan_is_two_requests_per_weekday_within_budget(self):
        plan = self.plan("2023-09-15", "2026-09-14")
        self.assertEqual(plan["requested_weekday_count"], 782)
        self.assertEqual(plan["weekend_days_not_requested"], 314)
        self.assertEqual(plan["planned_requests"], 1564)
        self.assertLessEqual(plan["planned_requests"], self.contract["hard_max_planned_requests"])
        self.assertTrue(all(dt.datetime.strptime(r["bas_dd"], "%Y%m%d").weekday() < 5 for r in plan["dates"]))
        self.assertEqual(B.estimate_wallclock_seconds(plan, 1.0), 1564.0)
        self.assertEqual(set(plan["calendar_years"]), {"2023", "2024", "2025", "2026"})
        self.assertTrue(all(v["source"] == "NONE_RESPONSE_ONLY" for v in plan["calendar_years"].values()))

    def test_plan_guards_fail_closed(self):
        with self.assertRaisesRegex(B.BackfillError, "RANGE_END_NOT_BEFORE_TODAY_KST"):
            self.plan("2026-09-01", "2026-09-15")
        with self.assertRaisesRegex(B.BackfillError, "RANGE_EXCEEDS_MAXIMUM_CALENDAR_DAYS"):
            self.plan("2023-01-01", "2026-09-14")
        with self.assertRaisesRegex(B.BackfillError, "RANGE_PRECEDES_MINIMUM_OFFICIAL_DATE"):
            self.plan("2009-12-01", "2010-02-01")
        with self.assertRaisesRegex(B.BackfillError, "RANGE_ORDER_INVALID"):
            self.plan("2026-09-10", "2026-09-01")
        with self.assertRaisesRegex(B.BackfillError, "RANGE_START_INVALID"):
            self.plan("2026-9-1", "2026-09-10")
        tight = dict(self.contract, hard_max_planned_requests=10)
        with self.assertRaisesRegex(B.BackfillError, "PLANNED_REQUESTS_EXCEED_HARD_MAXIMUM"):
            B.build_plan("2026-08-03", "2026-08-14", contract=tight, today_kst=TODAY)

    def test_plan_only_cli_makes_no_network_call(self):
        import contextlib
        original = B.urlopen
        B.urlopen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network"))
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(B.main(["--start", "2026-08-03", "--end", "2026-08-07", "--plan-only"]), 0)
        finally:
            B.urlopen = original
        self.assertIn('"planned_requests": 10', out.getvalue())

    # ------------------------------------------------------------ happy path, resume, write-once

    def test_complete_run_classifies_sessions_from_responses_and_resumes(self):
        plan = self.plan("2026-08-10", "2026-08-21")
        provider = Provider(self.identities, holidays={"20260814"})
        clock = Clock()
        receipt = self.run_backfill(plan, provider, clock)
        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertEqual(len(provider.calls), 20)
        self.assertEqual(receipt["request_accounting"]["http_attempts"], 20)
        self.assertEqual(receipt["request_accounting"]["records_written"], 20)
        self.assertEqual(receipt["sessions"]["session_confirmed_count"], 9)
        self.assertEqual(receipt["sessions"]["non_session_empty_response_dates"], ["2026-08-14"])
        self.assertIs(receipt["sessions"]["weekday_inference_used_for_sessions"], False)
        self.assertEqual(receipt["identity_coverage"]["KOSPI::건설"]["present_session_count"], 9)
        self.assertEqual(receipt["identity_coverage"]["KOSDAQ::코스닥"]["missing_session_count"], 0)
        # pacing: every request after the first waited the minimum interval
        self.assertEqual(clock.sleeps.count(1.0), 19)
        record = json.loads((self.records / "KOSPI" / "20260810.json").read_text())
        self.assertEqual(record["matched_identity_count"], 25)
        self.assertNotIn("KOSPI::비섹터 참고지수", record["series"])
        # public receipt: aggregate-only validator passes, no key, no index value
        S.validate_public_artifact(receipt)
        text = json.dumps(receipt, ensure_ascii=False)
        self.assertNotIn(KEY, text)
        self.assertNotIn(record["series"]["KOSPI::건설"]["close"], text)
        # resume: nothing is re-requested, hashes are stable
        again = Provider(self.identities, holidays={"20260814"})
        second = self.run_backfill(plan, again)
        self.assertEqual(again.calls, [])
        self.assertEqual(second["request_accounting"]["records_reused"], 20)
        self.assertEqual(second["records_payload_sha256"], receipt["records_payload_sha256"])
        self.assertEqual(second["manifest_sha256"], receipt["manifest_sha256"])

    def test_records_are_write_once(self):
        plan = self.plan("2026-08-10", "2026-08-10")
        self.run_backfill(plan, Provider(self.identities))
        path = self.records / "KOSPI" / "20260810.json"
        before = path.read_bytes()
        with self.assertRaisesRegex(B.BackfillError, "RECORD_ALREADY_EXISTS"):
            B.write_once(path, {"schema_version": "tampered"})
        self.assertEqual(path.read_bytes(), before)

    def test_output_inside_repository_is_refused(self):
        plan = self.plan("2026-08-10", "2026-08-10")
        with self.assertRaisesRegex(B.BackfillError, "OUTPUT_INSIDE_REPOSITORY_FORBIDDEN"):
            B.run_backfill(KEY, plan, ROOT / "tmp-kr-records", contract=self.contract,
                           identities=self.identities, opener=Provider(self.identities),
                           sleep=lambda s: None, monotonic=lambda: 0.0)
        self.assertFalse((ROOT / "tmp-kr-records").exists())

    def test_missing_key_and_low_interval_refused_before_any_call(self):
        plan = self.plan("2026-08-10", "2026-08-10")
        provider = Provider(self.identities)
        with self.assertRaisesRegex(B.BackfillError, "KRX_API_KEY_MISSING"):
            B.run_backfill("", plan, self.records, contract=self.contract, identities=self.identities,
                           opener=provider, sleep=lambda s: None, monotonic=lambda: 0.0)
        with self.assertRaisesRegex(B.BackfillError, "MIN_INTERVAL_BELOW_FLOOR"):
            self.run_backfill(plan, provider, min_interval=0.1)
        self.assertEqual(provider.calls, [])

    # ------------------------------------------------------------ fail-closed stops

    def test_http_401_403_429_stop_immediately(self):
        for code in (401, 403, 429):
            with self.subTest(code=code):
                records = Path(self.tmp.name) / f"records-{code}"
                plan = self.plan("2026-08-10", "2026-08-21")
                provider = Provider(self.identities, failures={3: http_error(code)})
                receipt = B.run_backfill(KEY, plan, records, contract=self.contract, identities=self.identities,
                                         opener=provider, sleep=lambda s: None, monotonic=lambda: 0.0)
                self.assertEqual(receipt["status"], "STOPPED")
                self.assertEqual(receipt["stop"], {"reason": "HTTP_STATUS_STOP", "http_status": code,
                                                   "bas_dd": "20260811", "market": "KOSDAQ"})
                self.assertEqual(len(provider.calls), 4)
                S.validate_public_artifact(receipt)

    def test_krx_error_code_body_stops(self):
        plan = self.plan("2026-08-10", "2026-08-12")
        body = json.dumps({"respMsg": "Unauthorized Key", "respCode": "401"}).encode()
        provider = Provider(self.identities, override={("KOSPI", "20260811"): body})
        receipt = self.run_backfill(plan, provider)
        self.assertEqual(receipt["status"], "STOPPED")
        self.assertEqual(receipt["stop"]["reason"], "RESPONSE_CODE_STOP")
        self.assertEqual(len(provider.calls), 3)

    def test_other_4xx_and_schema_and_date_mismatch_stop(self):
        cases = {
            "HTTP_STATUS_STOP": {"failures": {0: http_error(400)}},
            "RESPONSE_SCHEMA_INVALID": {"override": {("KOSPI", "20260810"): b'{"unexpected": []}'}},
            "RESPONSE_NOT_JSON": {"override": {("KOSPI", "20260810"): b"<html>"}},
            "RESPONSE_DATE_MISMATCH": {"override": {("KOSPI", "20260810"): json.dumps(
                {"OutBlock_1": [{"BAS_DD": "20260807", "IDX_NM": "코스피", "CLSPRC_IDX": "1"}]}).encode()}},
        }
        for reason, kwargs in cases.items():
            with self.subTest(reason=reason):
                records = Path(self.tmp.name) / f"records-{reason}"
                plan = self.plan("2026-08-10", "2026-08-11")
                provider = Provider(self.identities, **kwargs)
                receipt = B.run_backfill(KEY, plan, records, contract=self.contract, identities=self.identities,
                                         opener=provider, sleep=lambda s: None, monotonic=lambda: 0.0)
                self.assertEqual(receipt["status"], "STOPPED")
                self.assertEqual(receipt["stop"]["reason"], reason)
                self.assertEqual(len(provider.calls), 1)
                self.assertFalse((records / "KOSPI" / "20260810.json").exists())

    def test_budget_exhaustion_stops(self):
        plan = self.plan("2026-08-10", "2026-08-14")
        contract = dict(self.contract, hard_max_http_attempts=3)
        provider = Provider(self.identities)
        receipt = B.run_backfill(KEY, plan, self.records, contract=contract, identities=self.identities,
                                 opener=provider, sleep=lambda s: None, monotonic=lambda: 0.0)
        self.assertEqual(receipt["stop"]["reason"], "HTTP_ATTEMPT_BUDGET_EXHAUSTED")
        self.assertEqual(len(provider.calls), 3)

    # ------------------------------------------------------------ transient handling

    def test_transient_failure_is_retried_with_backoff(self):
        plan = self.plan("2026-08-10", "2026-08-10")
        provider = Provider(self.identities, failures={0: http_error(502), 1: url_error})
        clock = Clock()
        receipt = self.run_backfill(plan, provider, clock)
        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertEqual(receipt["request_accounting"]["transient_retries"], 2)
        self.assertEqual(receipt["request_accounting"]["http_attempts"], 4)
        self.assertIn(10, clock.sleeps)
        self.assertIn(30, clock.sleeps)

    def test_exhausted_retries_block_date_and_repeated_outage_stops(self):
        plan = self.plan("2026-08-10", "2026-08-11")
        provider = Provider(self.identities, failures={0: http_error(503), 1: http_error(503), 2: http_error(503)})
        receipt = self.run_backfill(plan, provider)
        self.assertEqual(receipt["status"], "INCOMPLETE_BLOCKED")
        self.assertEqual(receipt["request_accounting"]["blocked_requests"], [{"bas_dd": "20260810", "market": "KOSPI"}])
        self.assertEqual(receipt["sessions"]["session_confirmed_count"], 1)
        S.validate_public_artifact(receipt)

        records = Path(self.tmp.name) / "records-outage"
        outage = Provider(self.identities, failures={i: url_error for i in range(100)})
        plan = self.plan("2026-08-03", "2026-08-14")
        stopped = B.run_backfill(KEY, plan, records, contract=self.contract, identities=self.identities,
                                 opener=outage, sleep=lambda s: None, monotonic=lambda: 0.0)
        self.assertEqual(stopped["stop"]["reason"], "CONSECUTIVE_TRANSIENT_FAILURES")
        self.assertEqual(len(outage.calls), 15)

    # ------------------------------------------------------------ session disagreements

    def test_market_disagreement_is_recorded(self):
        plan = self.plan("2026-08-10", "2026-08-11")
        provider = Provider(self.identities, override={("KOSDAQ", "20260811"): b'{"OutBlock_1": []}'})
        receipt = self.run_backfill(plan, provider)
        self.assertEqual(receipt["status"], "DISAGREEMENT")
        self.assertEqual(receipt["sessions"]["disagreements"], [{"date": "2026-08-11", "kind": "MARKETS_DISAGREE"}])

    def test_official_calendar_cross_check_both_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture-2026.json"
            path.write_bytes(calendar_capture(2026, ["2026-08-14", "2026-08-17"]))
            calendars = B.load_calendar_captures([path])
        plan = self.plan("2026-08-10", "2026-08-18", calendars)
        self.assertEqual({r["bas_dd"]: r["calendar_status"] for r in plan["dates"]}["20260814"], "CLOSED")
        self.assertEqual(plan["calendar_years"]["2026"]["source"], "KRX_GLOBAL_MARKET_CLOSING_HOLIDAY_01023")
        # KRX returns rows on calendar-closed 08-17 and nothing on calendar-open 08-18
        provider = Provider(self.identities, holidays={"20260814", "20260818"})
        receipt = self.run_backfill(plan, provider)
        self.assertEqual(receipt["status"], "DISAGREEMENT")
        self.assertEqual(receipt["sessions"]["disagreements"], [
            {"date": "2026-08-17", "kind": "CALENDAR_CLOSED_BUT_ROWS"},
            {"date": "2026-08-18", "kind": "CALENDAR_OPEN_BUT_EMPTY"},
        ])
        self.assertEqual(receipt["sessions"]["non_session_empty_response_dates"], ["2026-08-14"])
        # weekday holiday was still requested (empty response recorded), never inferred
        self.assertIn(("KOSPI", "20260814"), provider.calls)

    def test_missing_ratified_identity_is_counted_not_inferred(self):
        plan = self.plan("2026-08-10", "2026-08-11")
        rows = synthetic_rows("KOSPI", "20260811", self.identities, skip={"KOSPI::건설"})
        rows.append({"BAS_DD": "20260811", "IDX_NM": "건설업", "CLSPRC_IDX": "100.00", "ACC_TRDVAL": "1"})
        provider = Provider(self.identities, override={
            ("KOSPI", "20260811"): json.dumps({"OutBlock_1": rows}, ensure_ascii=False).encode("utf-8")})
        receipt = self.run_backfill(plan, provider)
        self.assertEqual(receipt["status"], "COMPLETE")
        cell = receipt["identity_coverage"]["KOSPI::건설"]
        self.assertEqual((cell["present_session_count"], cell["missing_session_count"]), (1, 1))
        self.assertEqual(cell["last_present"], "2026-08-10")
        self.assertEqual(len(receipt["index_name_catalogs"]["KOSPI"]), 2)

    def test_duplicate_ratified_identity_stops(self):
        plan = self.plan("2026-08-10", "2026-08-10")
        rows = synthetic_rows("KOSPI", "20260810", self.identities)
        rows.append(dict(rows[1]))
        provider = Provider(self.identities, override={
            ("KOSPI", "20260810"): json.dumps({"OutBlock_1": rows}, ensure_ascii=False).encode("utf-8")})
        receipt = self.run_backfill(plan, provider)
        self.assertEqual(receipt["stop"]["reason"], "RESPONSE_RATIFIED_IDENTITY_DUPLICATE")

    def test_module_has_no_commit_or_publish_path(self):
        source = (ROOT / "collectors/kr_sector_index_history_backfill.py").read_text()
        for forbidden in ("subprocess", "git ", "print(auth_key", "print(key", "raw_base64"):
            self.assertNotIn(forbidden, source)
        for key, value in B.AUTHORITY.items():
            if key.endswith("_authorized"):
                self.assertIs(value, False)


if __name__ == "__main__":
    unittest.main()
