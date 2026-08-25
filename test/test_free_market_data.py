#!/usr/bin/env python3
import datetime as dt
import importlib.util
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("free_market_data", ROOT / "collectors" / "free_market_data.py")
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


class FreeMarketDataTests(unittest.TestCase):
    def test_contract_is_iex_shadow_only(self):
        c = M.load_contract()
        self.assertEqual(c["alpaca"]["feed"], "iex")
        self.assertTrue(c["authority"]["evidence_capture_only"])
        self.assertFalse(c["authority"]["us_breadth_authorized"])
        self.assertFalse(c["authority"]["order_authorized"])
        self.assertFalse(c["authority"]["trading_authorized"])

    def test_fetch_build_and_publish_preserve_raw_hashes(self):
        now = dt.datetime(2026, 8, 22, 1, 2, 3, tzinfo=dt.timezone.utc)
        fred_raw = json.dumps({"observations":[{"date":"2026-08-21","value":"15.5","realtime_start":"2026-08-22","realtime_end":"2026-08-22"}]}).encode()
        alpaca_raw = json.dumps({"bars":{"MSFT":{"c":500.1,"v":1200,"t":"2026-08-21T19:59:00Z"}}}).encode()
        daily_raw = json.dumps({"bars":[{"o":498.0,"h":502.0,"l":497.0,"c":500.1,"v":1200,"t":"2026-08-21T00:00:00Z"}]}).encode()
        fred_got, fred = M.fetch_fred("x", now, getter=lambda *_: fred_raw)
        alpaca_got, bars = M.fetch_alpaca("k", "s", ["MSFT"], getter=lambda *_: alpaca_raw)
        daily_got, daily_bars = M.fetch_alpaca_daily_bars("k", "s", ["MSFT"], now, getter=lambda *_: daily_raw)
        packet = M.build_capture(now, fred_got, fred, alpaca_got, bars, daily_got, daily_bars, M.load_contract())
        self.assertEqual(packet["fred"]["raw_sha256"], M.sha256_bytes(fred_raw))
        self.assertEqual(packet["alpaca"]["source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")
        self.assertFalse(packet["authority"]["entry_authorized"])
        self.assertEqual(packet["alpaca"]["daily_timeframe"], "1Day")
        self.assertEqual(packet["alpaca"]["daily_bars"][0]["symbol"], "MSFT")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); M.publish(root, now, fred_raw, alpaca_raw, daily_got, packet)
            self.assertTrue((root/"data/latest_free_market_data.json").exists())
            self.assertTrue((root/"evidence/free_market_data/raw/2026-08-22/manifest.json").exists())
            self.assertTrue((root/"evidence/free_market_data/raw/2026-08-22/alpaca_iex_daily_bars.json.gz").exists())

    def test_missing_or_malformed_provider_data_fails_closed(self):
        now = dt.datetime(2026, 8, 22, tzinfo=dt.timezone.utc)
        with self.assertRaisesRegex(M.FreeMarketDataError, "FRED_OBSERVATIONS_MISSING"):
            M.fetch_fred("x", now, getter=lambda *_: b'{"observations":[]}')
        with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_NO_SYMBOLS_RETURNED"):
            M.fetch_alpaca("k", "s", ["MSFT"], getter=lambda *_: b'{"bars":{}}')


class DedicatedMarketDataCredentialTests(unittest.TestCase):
    """★ 2026-08-23 cutover: ALPACA_API_KEY/ALPACA_API_SECRET (the account/
    trading credential) now live ONLY in the private atlas-private-evidence
    repo. This collector is a separate, market-data-only consumer and must
    require its OWN dedicated credential (ALPACA_MARKET_DATA_API_KEY/
    ALPACA_MARKET_DATA_API_SECRET) -- never fall back to the old shared
    name, never silently skip, never fabricate a placeholder price."""

    def test_missing_dedicated_credential_blocks_with_explicit_status(self):
        with mock.patch.dict(os.environ, {"FRED_API_KEY": "x"}, clear=True), \
             mock.patch("sys.argv", ["free_market_data.py"]):
            with self.assertRaisesRegex(SystemExit, "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL"):
                M.main()

    def test_old_shared_alpaca_credential_name_is_never_accepted_as_a_fallback(self):
        env = {"FRED_API_KEY": "x", "ALPACA_API_KEY": "old-shared-key", "ALPACA_API_SECRET": "old-shared-secret"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("sys.argv", ["free_market_data.py"]):
            with self.assertRaisesRegex(SystemExit, "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL"):
                M.main()

    def test_source_never_reads_the_old_shared_env_var_names(self):
        source = inspect.getsource(M)
        self.assertNotIn('os.getenv("ALPACA_API_KEY"', source)
        self.assertNotIn('os.getenv("ALPACA_API_SECRET"', source)
        self.assertIn('os.getenv("ALPACA_MARKET_DATA_API_KEY"', source)
        self.assertIn('os.getenv("ALPACA_MARKET_DATA_API_SECRET"', source)


if __name__ == "__main__": unittest.main()
