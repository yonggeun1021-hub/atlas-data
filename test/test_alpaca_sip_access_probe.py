#!/usr/bin/env python3
"""Offline regression for collectors/alpaca_sip_access_probe.py.

Fake HTTP layer only -- no network call. Covers: the request-budget cap,
retry-once-on-transient-failure-only, no secret leakage anywhere in the
returned summary, the aggregate-only schema check (rejects any per-day
price/close/volume/vwap field), the SIP/IEX volume ratio and notional-ratio
arithmetic, and contract-driven symbol resolution (including the current
real contract, where AAPL is not an approved alpaca.symbol and the fallback
to SPY/XLK/MSFT must engage).
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "alpaca_sip_access_probe", ROOT / "collectors" / "alpaca_sip_access_probe.py"
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

SECRET_KEY = "AKFAKESECRETKEYID000"
SECRET_SECRET = "fake-super-secret-value-should-never-leak"
CREDENTIALS = {"key": SECRET_KEY, "secret": SECRET_SECRET}
NOW = dt.datetime(2026, 9, 15, 12, 0, 0, tzinfo=dt.timezone.utc)


def _bar(date: str, close: float, volume: float, vwap: float) -> dict:
    return {"t": f"{date}T00:00:00Z", "o": close, "h": close, "l": close, "c": close, "v": volume, "vw": vwap, "n": 1}


def _bars_body(symbols_rows: dict) -> bytes:
    return json.dumps({"bars": symbols_rows, "next_page_token": None}).encode()


class FakeOpener:
    """Scripted responses keyed by feed (read from the request URL)."""

    def __init__(self, by_feed: dict, *, always_error: dict | None = None):
        self.by_feed = by_feed
        self.always_error = always_error or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict):
        self.calls.append((url, dict(headers)))
        query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))
        feed = query["feed"]
        if feed in self.always_error:
            raise self.always_error[feed]
        responses = list(self.by_feed[feed])
        index = min(len([c for c in self.calls if f"feed={feed}" in c[0]]) - 1, len(responses) - 1)
        return responses[index]


class SymbolResolutionTests(unittest.TestCase):
    def test_preferred_used_when_all_three_approved(self):
        contract = {"alpaca": {"symbols": ["SPY", "XLK", "AAPL", "MSFT"]}}
        symbols, source = M.resolve_symbols(contract)
        self.assertEqual(symbols, ["SPY", "XLK", "AAPL"])
        self.assertIn("SPY,XLK,AAPL", source)

    def test_fallback_to_msft_when_aapl_not_approved(self):
        contract = {"alpaca": {"symbols": ["SPY", "XLK", "MSFT", "QQQ"]}}
        symbols, source = M.resolve_symbols(contract)
        self.assertEqual(symbols, ["SPY", "XLK", "MSFT"])
        self.assertIn("fallback", source)

    def test_first_three_approved_when_neither_set_fully_present(self):
        contract = {"alpaca": {"symbols": ["QQQ", "IWM", "XLF", "XLE"]}}
        symbols, source = M.resolve_symbols(contract)
        self.assertEqual(symbols, ["QQQ", "IWM", "XLF"])

    def test_insufficient_approved_symbols_fails_closed(self):
        contract = {"alpaca": {"symbols": ["QQQ"]}}
        with self.assertRaisesRegex(M.ProbeError, "CONTRACT_ALPACA_SYMBOLS_INSUFFICIENT"):
            M.resolve_symbols(contract)

    def test_real_contract_currently_falls_back_because_aapl_is_absent(self):
        contract = M.load_contract()
        approved = set(contract["alpaca"]["symbols"])
        self.assertIn("SPY", approved)
        self.assertIn("XLK", approved)
        self.assertNotIn("AAPL", approved, "AAPL was approved in the contract -- update the fallback expectation")
        symbols, source = M.resolve_symbols(contract)
        self.assertEqual(symbols, ["SPY", "XLK", "MSFT"])
        self.assertIn("fallback", source)


class SessionWindowTests(unittest.TestCase):
    def test_end_is_at_least_two_days_before_now_and_start_precedes_it(self):
        start, end = M.session_window(NOW)
        self.assertLessEqual(dt.date.fromisoformat(end), (NOW - dt.timedelta(days=M.MIN_DAYS_AGO)).date())
        self.assertLess(dt.date.fromisoformat(start), dt.date.fromisoformat(end))

    def test_naive_clock_fails_closed(self):
        with self.assertRaisesRegex(M.ProbeError, "CLOCK_NOT_TIMEZONE_AWARE"):
            M.session_window(dt.datetime(2026, 9, 15))


class RequestBudgetTests(unittest.TestCase):
    def test_request_budget_constant_is_exactly_six(self):
        # CIO review 2026-09-15: pin the code constant directly, not only
        # the YAML comment text that documents it (test_request_budget_
        # documented_as_six in the workflow test checks the comment; this
        # checks the actual value the budget is constructed with).
        self.assertEqual(M.REQUEST_BUDGET, 6)
        self.assertEqual(M.RequestBudget().limit, 6)

    def test_budget_exhausted_fails_closed(self):
        budget = M.RequestBudget(limit=2)
        budget.spend()
        budget.spend()
        with self.assertRaisesRegex(M.ProbeError, "REQUEST_BUDGET_EXHAUSTED"):
            budget.spend()

    def test_full_probe_never_exceeds_six_requests(self):
        symbols = ["SPY", "XLK", "MSFT"]
        rows = {s: [_bar("2026-09-10", 100.0, 1000.0, 100.0)] for s in symbols}
        opener = FakeOpener({
            "sip": [(200, _bars_body(rows))],
            "iex": [(200, _bars_body(rows))],
        })
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        self.assertLessEqual(summary["requests_used"], M.REQUEST_BUDGET)
        self.assertEqual(summary["requests_used"], 2)  # one per feed, no retries needed


class RetryPolicyTests(unittest.TestCase):
    def test_transient_failure_is_retried_once_then_succeeds(self):
        symbols = ["SPY", "XLK", "MSFT"]
        rows = {s: [_bar("2026-09-10", 100.0, 1000.0, 100.0)] for s in symbols}
        opener = FakeOpener({
            "sip": [(503, b'{"message": "temporary"}'), (200, _bars_body(rows))],
            "iex": [(200, _bars_body(rows))],
        })
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        self.assertEqual(summary["feeds"]["sip"]["attempts_used"], 2)
        self.assertTrue(summary["feeds"]["sip"]["returned_bars"])
        self.assertEqual(summary["requests_used"], 3)

    def test_definitive_403_is_not_retried(self):
        symbols = ["SPY", "XLK", "MSFT"]
        rows = {s: [_bar("2026-09-10", 100.0, 1000.0, 100.0)] for s in symbols}
        body = b'{"message": "subscription does not permit querying recent SIP data"}'
        opener = FakeOpener({
            "sip": [(403, body)],
            "iex": [(200, _bars_body(rows))],
        })
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        self.assertEqual(summary["feeds"]["sip"]["attempts_used"], 1)
        self.assertFalse(summary["feeds"]["sip"]["returned_bars"])
        self.assertEqual(summary["feeds"]["sip"]["attempts"][0]["status"], 403)
        self.assertEqual(summary["feeds"]["sip"]["attempts"][0]["error_message_class"], "SIP_SUBSCRIPTION_DENIED")
        self.assertIn("subscription does not permit", summary["feeds"]["sip"]["attempts"][0]["error_message"])
        self.assertEqual(summary["requests_used"], 2)  # 1 sip + 1 iex, no retry spent on the 403

    def test_network_error_is_retried_once(self):
        symbols = ["SPY", "XLK", "MSFT"]
        rows = {s: [_bar("2026-09-10", 100.0, 1000.0, 100.0)] for s in symbols}

        class Flaky:
            def __init__(self):
                self.calls = 0

            def __call__(self, url, headers):
                if "feed=sip" in url:
                    self.calls += 1
                    if self.calls == 1:
                        import urllib.error
                        raise urllib.error.URLError("boom")
                    return 200, _bars_body(rows)
                return 200, _bars_body(rows)

        summary = M.run_probe(CREDENTIALS, opener=Flaky(), clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        self.assertEqual(summary["feeds"]["sip"]["attempts_used"], 2)
        self.assertIsNone(summary["feeds"]["sip"]["attempts"][0]["error_message_class"])
        self.assertEqual(summary["feeds"]["sip"]["attempts"][0]["transport_error"], "NETWORK_ERROR:URL_ERROR")
        self.assertTrue(summary["feeds"]["sip"]["returned_bars"])


class NoSecretLeakageTests(unittest.TestCase):
    def test_credentials_never_appear_in_the_summary(self):
        symbols = ["SPY", "XLK", "MSFT"]
        rows = {s: [_bar("2026-09-10", 100.0, 1000.0, 100.0)] for s in symbols}
        body = b'{"message": "subscription does not permit querying recent SIP data"}'
        opener = FakeOpener({"sip": [(403, body)], "iex": [(200, _bars_body(rows))]})
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        blob = json.dumps(summary)
        self.assertNotIn(SECRET_KEY, blob)
        self.assertNotIn(SECRET_SECRET, blob)
        # And the headers actually sent did carry them (so the test would
        # catch a leak, not just a probe that forgot to send credentials).
        self.assertTrue(any(h.get("APCA-API-SECRET-KEY") == SECRET_SECRET for _, h in opener.calls))

    def test_credentials_from_env_are_stripped_and_checked(self):
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


class SchemaRejectionTests(unittest.TestCase):
    def test_forbidden_top_level_key_is_rejected(self):
        with self.assertRaisesRegex(M.ProbeError, "FORBIDDEN_FIELD_IN_ARTIFACT"):
            M.assert_no_forbidden_fields({"symbol": "SPY", "close": 123.4})

    def test_forbidden_nested_key_is_rejected(self):
        with self.assertRaisesRegex(M.ProbeError, "FORBIDDEN_FIELD_IN_ARTIFACT"):
            M.assert_no_forbidden_fields({"per_symbol": {"SPY": {"bars": []}}})

    def test_forbidden_key_inside_a_list_is_rejected(self):
        with self.assertRaisesRegex(M.ProbeError, "FORBIDDEN_FIELD_IN_ARTIFACT"):
            M.assert_no_forbidden_fields({"rows": [{"volume": 1.0}]})

    def test_clean_aggregate_summary_passes(self):
        M.assert_no_forbidden_fields({
            "per_symbol": {"SPY": {"sip_volume_over_iex_volume_ratio_median": 1.02, "sip_bar_count": 10}},
        })

    def test_real_run_probe_output_passes_its_own_schema_check(self):
        symbols = ["SPY", "XLK", "MSFT"]
        rows = {s: [_bar("2026-09-10", 100.0, 1000.0, 101.0)] for s in symbols}
        opener = FakeOpener({"sip": [(200, _bars_body(rows))], "iex": [(200, _bars_body(rows))]})
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        M.assert_no_forbidden_fields(summary)  # run_probe already asserts this; re-check is idempotent
        self.assertNotIn("bars_by_symbol", json.dumps(summary))

    def test_renamed_per_day_volume_series_is_still_rejected_by_shape(self):
        # CIO review 2026-09-15: a key-name blocklist alone lets a renamed
        # per-day series through. This field name is deliberately NOT in
        # FORBIDDEN_KEYS -- it must still be rejected because it is a bare
        # list of more than a handful of plain numbers.
        with self.assertRaisesRegex(M.ProbeError, "FORBIDDEN_NUMERIC_SERIES"):
            M.assert_no_forbidden_fields({
                "per_symbol": {"SPY": {"sip_daily_volume_series": [100.0, 200.0, 150.0, 175.0, 300.0]}},
            })

    def test_short_numeric_list_at_or_below_the_threshold_is_allowed(self):
        M.assert_no_forbidden_fields({"window": {"session_count_options": [5, 10, 20]}})

    def test_date_keyed_map_is_rejected_regardless_of_its_key_name(self):
        with self.assertRaisesRegex(M.ProbeError, "FORBIDDEN_DATE_KEYED_MAP"):
            M.assert_no_forbidden_fields({"by_session": {"2026-09-08": 123456.0, "2026-09-09": 234567.0}})

    def test_non_date_string_keys_are_not_mistaken_for_a_date_keyed_map(self):
        M.assert_no_forbidden_fields({"per_symbol": {"SPY": {}, "XLK": {}, "MSFT": {}}})


class AggregateArithmeticTests(unittest.TestCase):
    def test_volume_ratio_and_notional_ratio_medians(self):
        symbols = ["SPY", "XLK", "MSFT"]
        sip_rows = {
            "SPY": [
                _bar("2026-09-08", 100.0, 2000.0, 101.0),
                _bar("2026-09-09", 100.0, 3000.0, 99.0),
                _bar("2026-09-10", 100.0, 4000.0, 100.0),
            ]
        }
        iex_rows = {
            "SPY": [
                _bar("2026-09-08", 100.0, 1000.0, 101.0),
                _bar("2026-09-09", 100.0, 1000.0, 99.0),
                _bar("2026-09-10", 100.0, 1000.0, 100.0),
            ]
        }
        opener = FakeOpener({"sip": [(200, _bars_body(sip_rows))], "iex": [(200, _bars_body(iex_rows))]})
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["shared_session_count"], 3)
        # volume ratios: 2.0, 3.0, 4.0 -> median 3.0
        self.assertAlmostEqual(row["sip_volume_over_iex_volume_ratio_median"], 3.0)
        # sip vwap/close ratios: 1.01, 0.99, 1.00 -> median 1.00
        self.assertAlmostEqual(row["vwap_notional_over_close_notional_ratio_median_sip"], 1.00, places=6)

    def test_missing_iex_volume_day_is_excluded_from_ratio(self):
        symbols = ["SPY", "XLK", "MSFT"]
        sip_rows = {"SPY": [_bar("2026-09-10", 100.0, 500.0, 100.0)]}
        iex_rows = {"SPY": [_bar("2026-09-10", 100.0, 0.0, 100.0)]}
        opener = FakeOpener({"sip": [(200, _bars_body(sip_rows))], "iex": [(200, _bars_body(iex_rows))]})
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        self.assertIsNone(summary["per_symbol"]["SPY"]["sip_volume_over_iex_volume_ratio_median"])

    def test_sip_absent_leaves_ratios_none_but_iex_still_reported(self):
        symbols = ["SPY", "XLK", "MSFT"]
        iex_rows = {"SPY": [_bar("2026-09-10", 100.0, 500.0, 100.0)]}
        body = b'{"message": "subscription does not permit querying recent SIP data"}'
        opener = FakeOpener({"sip": [(403, body)], "iex": [(200, _bars_body(iex_rows))]})
        summary = M.run_probe(CREDENTIALS, opener=opener, clock=lambda: NOW, contract={"alpaca": {"symbols": symbols}})
        row = summary["per_symbol"]["SPY"]
        self.assertEqual(row["sip_bar_count"], 0)
        self.assertEqual(row["iex_bar_count"], 1)
        self.assertIsNone(row["sip_volume_over_iex_volume_ratio_median"])
        self.assertIsNone(row["vwap_notional_over_close_notional_ratio_median_sip"])
        self.assertIsNotNone(row["vwap_notional_over_close_notional_ratio_median_iex"])


class OutputPathGuardTests(unittest.TestCase):
    def test_output_inside_checkout_is_forbidden(self):
        with self.assertRaisesRegex(M.ProbeError, "OUTPUT_INSIDE_CHECKOUT_FORBIDDEN"):
            M._forbid_inside_checkout(M.ROOT / "evidence" / "x.json")

    def test_output_outside_checkout_is_allowed(self):
        M._forbid_inside_checkout(Path("/tmp/somewhere/x.json"))


if __name__ == "__main__":
    unittest.main()
