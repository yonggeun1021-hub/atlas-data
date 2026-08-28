"""P3-12 Upbit public-market capture regression."""
from __future__ import annotations

import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "upbit_market_capture.py"
SPEC = importlib.util.spec_from_file_location("upbit_market_capture", MODULE_PATH)
CAP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CAP)


def fixed_clock():
    return dt.datetime(2026, 8, 28, 0, 40, 0, tzinfo=dt.timezone.utc)


def build_fetcher(contract, markets, *, warning_for=None, candle_count=100):
    warning_for = warning_for or set()
    market_all = [
        {
            "market": market,
            "korean_name": f"코인{i}",
            "english_name": f"Coin{i}",
            "market_event": {"warning": market in warning_for, "caution": {"PRICE_FLUCTUATIONS": False}},
        }
        for i, market in enumerate(markets)
    ]
    ticker = [{"market": m, "trade_price": 1000} for m in markets]
    orderbook = [
        {"market": m, "orderbook_units": [{"bid_price": 999, "bid_size": 10, "ask_price": 1001, "ask_size": 10}]}
        for m in markets
    ]
    candles_by_market = {
        m: [
            {
                "market": m,
                "candle_date_time_utc": f"2026-08-{max(1, 28 - i):02d}T00:00:00",
                "candle_acc_trade_price": 6000000000,
                "trade_price": 1000,
            }
            for i in range(candle_count)
        ]
        for m in markets
    }
    responses = {
        contract["market_all_endpoint"]: json.dumps(market_all).encode(),
        contract["ticker_endpoint_template"].format(MARKETS=",".join(markets)): json.dumps(ticker).encode(),
        contract["orderbook_endpoint_template"].format(MARKETS=",".join(markets)): json.dumps(orderbook).encode(),
    }
    for m in markets:
        url = contract["candles_days_endpoint_template"].format(MARKET=m, COUNT=contract["candle_lookback_count"])
        responses[url] = json.dumps(candles_by_market[m]).encode()

    def fetcher(url, timeout):
        if url not in responses:
            raise AssertionError(f"capture requested an unexpected URL: {url}")
        return responses[url]

    return fetcher


class UpbitMarketCaptureTests(unittest.TestCase):
    def test_capture_writes_hash_bound_manifest_and_validates(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC", "KRW-ETH"]
        fetcher = build_fetcher(contract, markets)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, snapshot_date=dt.date(2026, 8, 28), contract=contract, fetcher=fetcher,
                sleeper=lambda s: None, clock=fixed_clock,
            )
            manifest = CAP.validate_snapshot(target)
            self.assertEqual(manifest["market_count"], 2)
            self.assertEqual(manifest["markets"], markets)
            self.assertFalse(manifest["auth_required"])
            self.assertFalse(manifest["order_or_withdrawal_endpoints_called"])
            self.assertEqual(manifest["downloaded_at_utc"], "2026-08-28T00:40:00Z")

    def test_append_only_violation_preserves_existing_snapshot(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC"]
        fetcher = build_fetcher(contract, markets)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, snapshot_date=dt.date(2026, 8, 28), contract=contract, fetcher=fetcher,
                sleeper=lambda s: None, clock=fixed_clock,
            )
            before = (target / contract["market_all_raw_file"]).read_bytes()
            with self.assertRaisesRegex(CAP.CaptureError, "APPEND_ONLY_VIOLATION"):
                CAP.capture_snapshot(
                    root, snapshot_date=dt.date(2026, 8, 28), contract=contract, fetcher=fetcher,
                    sleeper=lambda s: None, clock=fixed_clock,
                )
            self.assertEqual((target / contract["market_all_raw_file"]).read_bytes(), before)

    def test_hash_tamper_is_rejected_by_validate_snapshot(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC"]
        fetcher = build_fetcher(contract, markets)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, snapshot_date=dt.date(2026, 8, 28), contract=contract, fetcher=fetcher,
                sleeper=lambda s: None, clock=fixed_clock,
            )
            tampered = gzip.compress(b'[{"market":"KRW-BTC","korean_name":"x","english_name":"y"}]')
            (target / contract["market_all_raw_file"]).write_bytes(tampered)
            with self.assertRaisesRegex(CAP.CaptureError, "RAW_FILE_HASH_MISMATCH"):
                CAP.validate_snapshot(target)

    def test_manifest_market_list_is_deduplicated_even_if_upstream_repeats(self):
        contract = CAP.load_contract()
        market_all = [
            {"market": "KRW-BTC", "korean_name": "a", "english_name": "a", "market_event": {"warning": False, "caution": {}}},
            {"market": "KRW-BTC", "korean_name": "dup", "english_name": "dup", "market_event": {"warning": False, "caution": {}}},
        ]
        self.assertEqual(CAP.krw_markets(market_all, "KRW-"), ["KRW-BTC"])

    def test_contract_safety_invariant_enforced(self):
        contract = CAP.load_contract()
        contract["auth_required"] = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(CAP.CaptureError, "CONTRACT_SAFETY_INVARIANT_VIOLATED"):
                CAP.load_contract(path)

    def test_no_order_withdrawal_or_private_endpoint_paths_referenced(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        contract_text = (ROOT / "config" / "upbit_market_capture_contract.json").read_text(encoding="utf-8")
        # "/v1/order" alone is deliberately excluded here -- it is a substring
        # of the legitimate public "/v1/orderbook" endpoint. Real private
        # order-placement/cancellation paths ("/v1/orders", exact) and
        # auth-material tokens are checked precisely instead.
        for forbidden in ("/v1/orders", "/v1/withdraws", "/v1/deposits", "Authorization", "api_key", "secret_key", "JWT"):
            self.assertNotIn(forbidden, text)
            self.assertNotIn(forbidden, contract_text)
        contract = CAP.load_contract()
        for endpoint_key in ("market_all_endpoint", "ticker_endpoint_template", "orderbook_endpoint_template", "candles_days_endpoint_template"):
            self.assertIn("/v1/", contract[endpoint_key])
            self.assertNotIn("orders", contract[endpoint_key].lower())
            self.assertNotIn("withdraw", contract[endpoint_key].lower())
            self.assertNotIn("deposit", contract[endpoint_key].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
