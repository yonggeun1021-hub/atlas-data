#!/usr/bin/env python3
"""Offline regression for collectors/alpaca_sip_daily_bars.py.

Fake HTTP layer only -- no network call. Covers: contract-driven symbol
resolution (all 22 approved symbols, never a hardcoded/expanded list),
per-symbol requests, ``page_token`` pagination, a bounded request budget
with retry-once-on-transient-failure, the SIP->IEX fallback when SIP is
denied, a 0-bar symbol (the exact SPY/MSFT multi-symbol-probe failure mode,
now avoided by per-symbol requests) -- reported as ``NOT_EVALUATED``, the
15-minute-past-close freshness embargo, the derived-only output (never a
raw open/high/low/close/volume/vwap field or per-day bar -- ``last_close_usd``
is the one deliberate scalar exception, required by the ratified price-floor
condition itself), and that no secret ever appears in the output or in any
place other than the two auth headers.

★ 2026-09-15 correction: the ratified threshold is now bound via the real
committed ``config/us_liquidity_sip_source_policy.json`` (sha256-verified
against ``evidence/authority/``) -- most tests here call
``run_collection(..., policy=None)``, which loads that REAL policy (not a
mock), so ``volume_status``/``price_status`` below reflect genuine
PASS/FAIL arithmetic against the real $10,000,000 / $5 numbers.

★ 2026-09-15 wiring: ``otc_exclusion_status`` now comes from
``universe/us_listing_lookup.py`` (a point-in-time Nasdaq Trader Symbol
Directory lookup). Most tests here pass an explicit ``listing=no_listing(...)``
fixture (unresolved for every symbol) so they stay independent of whatever
the real committed capture happens to contain on any given day; a
dedicated ``ListingWiringTests`` class below exercises the real lookup
(against a temp fixture packet, still never the actual committed
multi-megabyte packets) end to end, proving the collector really can reach
``status: "PASS"`` now.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "alpaca_sip_daily_bars", ROOT / "collectors" / "alpaca_sip_daily_bars.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
LIQ = M.LIQ

SECRET_KEY = "AKFAKESECRETKEYID000"
SECRET_SECRET = "fake-super-secret-value-should-never-leak"
CREDENTIALS = {"key": SECRET_KEY, "secret": SECRET_SECRET}
# 23:00Z is safely >=15 minutes past both the EDT (20:00Z) and EST (21:00Z)
# regular close of any date used below.
NOW = dt.datetime(2026, 9, 15, 23, 0, 0, tzinfo=dt.timezone.utc)
CONTRACT = {"alpaca": {"symbols": ["SPY", "MSFT"]}}


def _bar(date: str, close: float, volume: float) -> dict:
    return {"t": f"{date}T00:00:00Z", "o": close, "h": close, "l": close, "c": close, "v": volume, "n": 1}


def _dates(end: dt.date, count: int) -> list[str]:
    return [(end - dt.timedelta(days=i)).isoformat() for i in range(count)][::-1]


FULL_WINDOW_DATES = _dates(dt.date(2026, 9, 14), LIQ.REQUIRED_SESSION_WINDOW)


def _body(bars: list[dict], next_page_token=None) -> bytes:
    return json.dumps({"bars": bars, "next_page_token": next_page_token}).encode()


def no_listing(symbols: list[str]) -> dict:
    """A ``resolve_listing``-shaped result where nothing resolved (no
    packet available) -- decouples these tests from whatever the real
    committed data/observations/us_global_universe packet contains today.
    """
    return {
        "packet_date": None,
        "packet_path": None,
        "packet_sha256": None,
        "listing_packet_age_days": None,
        "per_symbol": {
            symbol: {"status": None, "reasons": ["NO_LISTING_PACKET_AVAILABLE"]}
            for symbol in symbols
        },
    }


class ScriptedOpener:
    """Scripted (status, body) responses per feed, consumed in order."""

    def __init__(self, by_feed: dict, *, always_error: dict | None = None):
        self.by_feed = {feed: list(responses) for feed, responses in by_feed.items()}
        self.always_error = always_error or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict):
        self.calls.append((url, dict(headers)))
        query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))
        feed = query["feed"]
        if feed in self.always_error:
            raise self.always_error[feed]
        responses = self.by_feed[feed]
        return responses.pop(0) if len(responses) > 1 else responses[0]


class SymbolResolutionTests(unittest.TestCase):
    def test_all_approved_symbols_used_sorted_and_deduplicated(self):
        contract = {"alpaca": {"symbols": ["MSFT", "SPY", "MSFT", "AAPL"]}}
        symbols, source = M.resolve_symbols(contract)
        self.assertEqual(symbols, ["AAPL", "MSFT", "SPY"])
        self.assertIn("free_market_data_contract.json", source)

    def test_empty_symbol_list_fails_closed(self):
        with self.assertRaisesRegex(M.AlpacaSipDailyBarsError, "CONTRACT_ALPACA_SYMBOLS_EMPTY"):
            M.resolve_symbols({"alpaca": {"symbols": []}})

    def test_real_contract_has_exactly_the_22_approved_symbols(self):
        symbols, _ = M.resolve_symbols()
        self.assertEqual(len(symbols), 22)
        for expected in ("SPY", "QQQ", "IWM", "XLK", "MSFT", "NVDA", "TSM", "SMH"):
            self.assertIn(expected, symbols)


class FreshnessEmbargoTests(unittest.TestCase):
    def test_bar_well_past_close_is_fresh(self):
        self.assertTrue(M.is_bar_fresh_enough(dt.date(2026, 9, 10), NOW))

    def test_bar_from_todays_still_open_session_is_not_fresh(self):
        today_market_open_now = dt.datetime(2026, 9, 15, 18, 0, tzinfo=dt.timezone.utc)  # 14:00 EDT, market open
        self.assertFalse(M.is_bar_fresh_enough(dt.date(2026, 9, 15), today_market_open_now))

    def test_bar_exactly_at_the_15_minute_boundary_is_fresh(self):
        close_edt = M.regular_close_utc(dt.date(2026, 9, 15))  # EDT date -> 20:00Z
        boundary = close_edt + dt.timedelta(minutes=15)
        self.assertTrue(M.is_bar_fresh_enough(dt.date(2026, 9, 15), boundary))

    def test_one_second_before_the_boundary_is_not_fresh(self):
        close_edt = M.regular_close_utc(dt.date(2026, 9, 15))
        just_before = close_edt + dt.timedelta(minutes=15) - dt.timedelta(seconds=1)
        self.assertFalse(M.is_bar_fresh_enough(dt.date(2026, 9, 15), just_before))

    def test_dst_transition_close_times_differ_by_one_hour(self):
        winter_close = M.regular_close_utc(dt.date(2026, 1, 15))  # EST -> 21:00Z
        summer_close = M.regular_close_utc(dt.date(2026, 7, 15))  # EDT -> 20:00Z
        self.assertEqual(winter_close.hour, 21)
        self.assertEqual(summer_close.hour, 20)

    def test_naive_clock_fails_closed(self):
        with self.assertRaisesRegex(M.AlpacaSipDailyBarsError, "CLOCK_NOT_TIMEZONE_AWARE"):
            M.session_window(dt.datetime(2026, 9, 15))


class FullPipelineTests(unittest.TestCase):
    def test_sip_full_window_used_no_iex_request_needed(self):
        # close=$100, volume=1,000,000/day -> avg $100,000,000/day, well
        # above the real committed $10,000,000 threshold; close $100 is
        # also above the real $5 price floor. The threshold IS bound now
        # (2026-09-15 correction) -- volume_status/price_status really are
        # PASS. Only otc_exclusion_status is honestly UNKNOWN (no listing
        # source wired into this collector), so the overall status stays
        # UNKNOWN, not because the threshold is missing but because one
        # specific, clearly-named input is.
        rows = [_bar(d, 100.0, 1_000_000.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["source_feed"], "sip")
        self.assertEqual(row["session_count"], LIQ.REQUIRED_SESSION_WINDOW)
        self.assertEqual(row["avg_traded_value_usd"], "100000000.00")
        self.assertEqual(row["last_close_usd"], "100.00")
        self.assertEqual(row["volume_status"], "PASS")
        self.assertEqual(row["price_status"], "PASS")
        self.assertEqual(row["otc_exclusion_status"], "UNKNOWN")
        self.assertEqual(row["status"], "UNKNOWN")
        self.assertIn("EXCHANGE_LISTING_STATUS_UNAVAILABLE", row["reasons"])
        self.assertEqual(summary["policy_status"], "RATIFIED")
        # SIP alone was enough: no request for this symbol carried feed=iex
        spy_calls = [url for url, _ in opener.calls if "SPY" in url]
        self.assertTrue(all("feed=sip" in url for url in spy_calls))

    def test_close_confirmed_below_price_floor_fails(self):
        # close=$2 is a confirmed, real bar-derived close below the real
        # $5 price floor -- a definite FAIL, computed with no mocked policy.
        rows = [_bar(d, 2.0, 10_000_000.0) for d in FULL_WINDOW_DATES]  # volume high enough that only price fails
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["last_close_usd"], "2.00")
        self.assertEqual(row["price_status"], "FAIL")
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("LAST_CLOSE_BELOW_PRICE_FLOOR", row["reasons"])

    def test_sip_denied_falls_back_to_iex(self):
        rows = [_bar(d, 5.0, 400_000.0) for d in FULL_WINDOW_DATES]  # avg 2,000,000/day
        body = b'{"message": "subscription does not permit querying recent SIP data"}'
        opener = ScriptedOpener({"sip": [(403, body)], "iex": [(200, _body(rows))]})
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["source_feed"], "iex")
        self.assertEqual(row["avg_traded_value_usd"], "2000000.00")
        self.assertEqual(row["session_count"], LIQ.REQUIRED_SESSION_WINDOW)
        self.assertTrue(row["sip_feed_ok"] is False or row["sip_bar_count"] == 0)

    def test_zero_bar_symbol_both_feeds_is_not_evaluated_not_a_crash(self):
        # This is exactly the alpaca-sip-access-probe finding (run 34907066300):
        # a symbol can come back with zero bars without an HTTP error. The
        # per-symbol endpoint here must handle that as NOT_EVALUATED (the
        # base record's own vocabulary for "fewer than 20 sessions -- no
        # T2 yet"), not crash and not UNKNOWN (UNKNOWN is reserved for a
        # real-but-inconclusive/missing input, which this isn't).
        opener = ScriptedOpener({"sip": [(200, _body([]))], "iex": [(200, _body([]))]})
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        row = summary["per_symbol"]["SPY"]
        self.assertIsNone(row["source_feed"])
        self.assertEqual(row["status"], "NOT_EVALUATED")
        self.assertEqual(row["sip_bar_count"], 0)
        self.assertEqual(row["iex_bar_count"], 0)

    def test_pagination_is_followed_and_bounded(self):
        page1 = [_bar(d, 5.0, 400_000.0) for d in FULL_WINDOW_DATES[:15]]
        page2 = [_bar(d, 5.0, 400_000.0) for d in FULL_WINDOW_DATES[15:]]

        class PagedOpener:
            def __init__(self):
                self.calls = []

            def __call__(self, url, headers):
                self.calls.append((url, headers))
                if "page_token=" in url:
                    return 200, _body(page2, next_page_token=None)
                return 200, _body(page1, next_page_token="tok-abc")

        opener = PagedOpener()
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["session_count"], LIQ.REQUIRED_SESSION_WINDOW)
        # two SIP page requests were made for SPY specifically
        spy_sip_calls = [u for u, _ in opener.calls if "SPY" in u and "feed=sip" in u]
        self.assertEqual(len(spy_sip_calls), 2)

    def test_pagination_that_never_terminates_fails_closed(self):
        class InfinitePagesOpener:
            def __call__(self, url, headers):
                return 200, _body([_bar(FULL_WINDOW_DATES[0], 5.0, 1.0)], next_page_token="always-more")

        opener = InfinitePagesOpener()
        with self.assertRaisesRegex(M.AlpacaSipDailyBarsError, "MAX_PAGES_EXCEEDED"):
            M.request_symbol_feed("SPY", "sip", M.session_window(NOW), CREDENTIALS, opener, M.RequestBudget(limit=1000))

    def test_transient_failure_is_retried_once_then_succeeds(self):
        rows = [_bar(d, 100.0, 1_000_000.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({
            "sip": [(503, b'{"message": "temporary"}'), (200, _body(rows))],
        })
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["source_feed"], "sip")
        self.assertEqual(row["session_count"], LIQ.REQUIRED_SESSION_WINDOW)

    def test_stale_bars_outside_the_freshness_embargo_are_excluded(self):
        # A bar dated "today" whose close was only 5 minutes ago is still
        # inside the 15-minute embargo and must be dropped.
        today = dt.date(2026, 9, 15)
        too_fresh_now = M.regular_close_utc(today) + dt.timedelta(minutes=5)
        rows = [_bar(d, 100.0, 1_000_000.0) for d in FULL_WINDOW_DATES]
        rows.append(_bar(today.isoformat(), 100.0, 1_000_000.0))
        opener = ScriptedOpener({"sip": [(200, _body(rows))], "iex": [(200, _body(rows))]})
        summary = M.run_collection(
            CREDENTIALS, opener=opener, clock=lambda: too_fresh_now, contract=CONTRACT, policy=None,
            listing=no_listing(CONTRACT["alpaca"]["symbols"]),
        )
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["session_count"], LIQ.REQUIRED_SESSION_WINDOW)
        self.assertNotEqual(row["window_end"], today.isoformat())
        self.assertNotIn(today.isoformat(), json.dumps(row))


class DerivedOnlyOutputTests(unittest.TestCase):
    def test_no_raw_bar_series_or_secret_reaches_the_summary(self):
        # ``last_close_usd`` (the ratified price-floor check's own input)
        # is a deliberate, single-scalar exception to "no price ever
        # appears" -- it is required by RULE.LIQUIDITY.US_SIP_SOURCE.V1's
        # own last_close_usd_min condition, not a leaked raw vendor row.
        # What must never appear is the raw per-day VOLUME figure (never
        # reported in any form, aggregate or otherwise) or a secret.
        rows = [_bar(d, 123.45, 987_654.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        M.assert_no_raw_bar_fields(summary)  # already asserted inside run_collection; idempotent re-check
        blob = json.dumps(summary)
        self.assertNotIn(SECRET_KEY, blob)
        self.assertNotIn(SECRET_SECRET, blob)
        self.assertNotIn("987654", blob)  # raw per-day volume never appears in any form
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["last_close_usd"], "123.45")  # the one deliberate exception
        self.assertTrue(any(h.get("APCA-API-SECRET-KEY") == SECRET_SECRET for _, h in opener.calls))

    def test_forbidden_raw_field_is_rejected_by_the_guard(self):
        with self.assertRaisesRegex(M.AlpacaSipDailyBarsError, "FORBIDDEN_RAW_FIELD_IN_OUTPUT"):
            M.assert_no_raw_bar_fields({"per_symbol": {"SPY": {"close": 100.0}}})

    def test_credentials_from_env_are_stripped(self):
        import os

        old = dict(os.environ)
        try:
            os.environ["ALPACA_MARKET_DATA_API_KEY"] = "  keyvalue  "
            os.environ["ALPACA_MARKET_DATA_API_SECRET"] = " secretvalue "
            creds = M._credentials_from_env()
            self.assertEqual(creds, {"key": "keyvalue", "secret": "secretvalue"})
        finally:
            os.environ.clear()
            os.environ.update(old)


class RequestBudgetTests(unittest.TestCase):
    def test_budget_exhausted_fails_closed(self):
        budget = M.RequestBudget(limit=1)
        budget.spend()
        with self.assertRaisesRegex(M.AlpacaSipDailyBarsError, "REQUEST_BUDGET_EXHAUSTED"):
            budget.spend()

    def test_budget_scales_with_actual_symbol_count(self):
        rows = [_bar(d, 1.0, 1.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        contract = {"alpaca": {"symbols": ["SPY", "QQQ", "IWM"]}}
        summary = M.run_collection(
            CREDENTIALS, opener=opener, clock=lambda: NOW, contract=contract, policy=None,
            listing=no_listing(contract["alpaca"]["symbols"]),
        )
        expected = 3 * len(M.FEEDS) * M.MAX_PAGES_PER_REQUEST * M.MAX_ATTEMPTS_PER_PAGE
        self.assertEqual(summary["request_budget"], expected)
        self.assertLessEqual(summary["requests_used"], expected)


class WriteOutputsTests(unittest.TestCase):
    def test_write_outputs_creates_dated_file_and_latest_pointer(self):
        import tempfile

        rows = [_bar(d, 1.0, 1.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        summary = M.run_collection(CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing=no_listing(CONTRACT["alpaca"]["symbols"]))
        with tempfile.TemporaryDirectory() as tmp:
            dated_dir = Path(tmp) / "data" / "us_sip_daily_liquidity"
            latest_path = Path(tmp) / "data" / "latest_us_sip_daily_liquidity.json"
            dated_path, written_latest = M.write_outputs(summary, dated_dir=dated_dir, latest_path=latest_path)
            self.assertTrue(dated_path.exists())
            self.assertTrue(written_latest.exists())
            on_disk = json.loads(dated_path.read_text(encoding="utf-8"))
            M.assert_no_raw_bar_fields(on_disk)
            latest = json.loads(written_latest.read_text(encoding="utf-8"))
            self.assertEqual(latest["symbol_count"], summary["symbol_count"])


class ListingWiringTests(unittest.TestCase):
    """The 2026-09-15 wiring: otc_exclusion_status now comes from a real
    point-in-time listing lookup, not an always-None input. Uses a small
    temp fixture packet (never the real committed multi-megabyte ones) so
    these tests stay fast and independent of what today's real capture
    happens to contain.
    """

    def _fixture_root(self, tmp: str, date: str, rows: list[dict], as_of_utc: str | None = None) -> Path:
        root = Path(tmp)
        packet_dir = root / "data" / "observations" / "us_global_universe" / date
        packet_dir.mkdir(parents=True)
        (packet_dir / "packet.json").write_text(
            json.dumps({"packet": {
                "as_of_date": date,
                "as_of_utc": as_of_utc or f"{date}T20:00:00Z",  # safely before NOW (23:00Z that day)
                "source_attribute_rows": rows,
            }}), encoding="utf-8",
        )
        return root

    def test_exchange_listed_symbol_reaches_a_real_pass_end_to_end(self):
        rows = [_bar(d, 10.0, 2_000_000.0) for d in FULL_WINDOW_DATES]  # avg $20M, close $10 -- both above real floors
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        with tempfile.TemporaryDirectory() as tmp:
            listing_root = self._fixture_root(tmp, "2026-09-15", [
                {"primary_symbol": "SPY", "source_name": "other_listed", "fields": {"Test Issue": "N"}},
            ])
            summary = M.run_collection(
                CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing_root=listing_root,
            )
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["listing_status"], "EXCHANGE_LISTED")
        self.assertEqual(row["otc_exclusion_status"], "PASS")
        self.assertEqual(row["status"], "PASS")  # was structurally unreachable before this wiring
        self.assertEqual(summary["listing_packet_date"], "2026-09-15")
        self.assertEqual(summary["listing_packet_age_days"], 0)
        self.assertIsNotNone(summary["listing_packet_sha256"])
        self.assertEqual(summary["listing_packet_path"], "data/observations/us_global_universe/2026-09-15/packet.json")

    def test_confirmed_test_issue_symbol_fails_through_the_collector(self):
        rows = [_bar(d, 10.0, 2_000_000.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        with tempfile.TemporaryDirectory() as tmp:
            listing_root = self._fixture_root(tmp, "2026-09-15", [
                {"primary_symbol": "SPY", "source_name": "nasdaq_listed", "fields": {"Test Issue": "Y"}},
            ])
            summary = M.run_collection(
                CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing_root=listing_root,
            )
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["listing_status"], "TEST_ISSUE")
        self.assertEqual(row["otc_exclusion_status"], "FAIL")
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("TEST_ISSUE_EXCLUDED", row["reasons"])

    def test_symbol_absent_from_the_listing_packet_stays_unknown(self):
        rows = [_bar(d, 10.0, 2_000_000.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        with tempfile.TemporaryDirectory() as tmp:
            listing_root = self._fixture_root(tmp, "2026-09-15", [])  # nothing in the packet at all
            summary = M.run_collection(
                CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing_root=listing_root,
            )
        row = summary["per_symbol"]["SPY"]
        self.assertIsNone(row["listing_status"])
        self.assertEqual(row["otc_exclusion_status"], "UNKNOWN")
        self.assertEqual(row["status"], "UNKNOWN")
        self.assertIn("SYMBOL_ABSENT_FROM_LISTING_PACKET", row["listing_reasons"])

    def test_no_listing_packet_available_at_all_is_unknown_not_a_crash(self):
        rows = [_bar(d, 10.0, 2_000_000.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        with tempfile.TemporaryDirectory() as tmp:
            listing_root = Path(tmp)  # no data/observations/us_global_universe at all
            summary = M.run_collection(
                CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing_root=listing_root,
            )
        self.assertIsNone(summary["listing_packet_date"])
        row = summary["per_symbol"]["SPY"]
        self.assertIsNone(row["listing_status"])
        self.assertEqual(row["otc_exclusion_status"], "UNKNOWN")

    def test_pit_selection_through_the_collector_never_uses_a_future_packet(self):
        # NOW is 2026-09-15; a packet dated 2026-09-20 must never be used.
        rows = [_bar(d, 10.0, 2_000_000.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        with tempfile.TemporaryDirectory() as tmp:
            listing_root = Path(tmp)
            older = listing_root / "data" / "observations" / "us_global_universe" / "2026-09-10"
            older.mkdir(parents=True)
            (older / "packet.json").write_text(
                json.dumps({"packet": {
                    "as_of_utc": "2026-09-10T20:00:00Z",
                    "source_attribute_rows": [
                        {"primary_symbol": "SPY", "source_name": "other_listed", "fields": {"Test Issue": "N"}},
                    ],
                }}), encoding="utf-8",
            )
            future = listing_root / "data" / "observations" / "us_global_universe" / "2026-09-20"
            future.mkdir(parents=True)
            (future / "packet.json").write_text(
                json.dumps({"packet": {
                    "as_of_utc": "2026-09-20T20:00:00Z",
                    "source_attribute_rows": [
                        {"primary_symbol": "SPY", "source_name": "nasdaq_listed", "fields": {"Test Issue": "Y"}},
                    ],
                }}), encoding="utf-8",
            )
            summary = M.run_collection(
                CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing_root=listing_root,
            )
        self.assertEqual(summary["listing_packet_date"], "2026-09-10")  # NOT 2026-09-20
        self.assertEqual(summary["per_symbol"]["SPY"]["listing_status"], "EXCHANGE_LISTED")

    def test_instant_guard_through_the_collector_rejects_a_same_day_packet_captured_after_this_run(self):
        # NOW is 2026-09-15T23:00:00Z. A same-day packet whose own as_of_utc
        # is AFTER that (e.g. captured by a later run the same day) must
        # never be used, even though its directory date qualifies.
        rows = [_bar(d, 10.0, 2_000_000.0) for d in FULL_WINDOW_DATES]
        opener = ScriptedOpener({"sip": [(200, _body(rows))]})
        with tempfile.TemporaryDirectory() as tmp:
            listing_root = self._fixture_root(
                tmp, "2026-09-15",
                [{"primary_symbol": "SPY", "source_name": "other_listed", "fields": {"Test Issue": "N"}}],
                as_of_utc="2026-09-15T23:30:00Z",  # after NOW (23:00:00Z)
            )
            summary = M.run_collection(
                CREDENTIALS, opener=opener, clock=lambda: NOW, contract=CONTRACT, policy=None, listing_root=listing_root,
            )
        self.assertIsNone(summary["listing_packet_date"])  # the 09-15 packet was rejected, nothing earlier exists
        row = summary["per_symbol"]["SPY"]
        self.assertIsNone(row["listing_status"])
        self.assertEqual(row["otc_exclusion_status"], "UNKNOWN")


class ListingProducerUntouchedTests(unittest.TestCase):
    def test_collector_never_writes_to_the_listing_producers_own_paths(self):
        text = (ROOT / "collectors" / "alpaca_sip_daily_bars.py").read_text(encoding="utf-8")
        self.assertNotIn("us_global_universe.py", text)
        self.assertNotIn("us_breadth_forward.py", text)


if __name__ == "__main__":
    unittest.main()
