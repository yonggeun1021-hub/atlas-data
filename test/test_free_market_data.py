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
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("free_market_data", ROOT / "collectors" / "free_market_data.py")
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


class FreeMarketDataTests(unittest.TestCase):
    def test_http_error_is_redacted_and_normalized(self):
        error = urllib.error.HTTPError(
            "https://example.invalid/data?api_key=do-not-leak", 401,
            "Unauthorized", hdrs=None, fp=None,
        )
        with mock.patch.object(M.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(M.FreeMarketDataError, "^HTTP_ERROR:401$") as raised:
                M._get("https://example.invalid/data?api_key=do-not-leak", {"X-Key": "secret"})
        self.assertNotIn("do-not-leak", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_url_error_is_redacted_and_normalized(self):
        error = urllib.error.URLError("host contained do-not-leak")
        with mock.patch.object(M.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(M.FreeMarketDataError, "^NETWORK_ERROR:URL_ERROR$") as raised:
                M._get("https://example.invalid/do-not-leak")
        self.assertNotIn("do-not-leak", str(raised.exception))

    def test_contract_is_iex_shadow_only(self):
        c = M.load_contract()
        self.assertEqual(c["alpaca"]["feed"], "iex")
        self.assertEqual(c["fred"]["raw_retention"], "TRANSIENT_NOT_PERSISTED")
        self.assertTrue(c["fred"]["partial_publish_authorized"])
        self.assertEqual(c["alpaca"]["credential_scope"], "DEDICATED_MARKET_DATA_ONLY")
        self.assertTrue(c["authority"]["evidence_capture_only"])
        self.assertFalse(c["authority"]["us_breadth_authorized"])
        self.assertFalse(c["authority"]["order_authorized"])
        self.assertFalse(c["authority"]["trading_authorized"])

    def test_fetch_build_and_publish_preserve_alpaca_raw_but_not_fred_raw(self):
        now = dt.datetime(2026, 8, 22, 1, 2, 3, tzinfo=dt.timezone.utc)
        fred_raw = json.dumps({"observations":[{"date":"2026-08-21","value":"15.5","realtime_start":"2026-08-22","realtime_end":"2026-08-22"}]}).encode()
        alpaca_raw = json.dumps({"bars":{"MSFT":{"c":500.1,"v":1200,"t":"2026-08-21T19:59:00Z"}}}).encode()
        daily_raw = json.dumps({"bars":[{"o":498.0,"h":502.0,"l":497.0,"c":500.1,"v":1200,"t":"2026-08-21T00:00:00Z"}]}).encode()
        fred_got, fred = M.fetch_fred("x", now, getter=lambda *_: fred_raw)
        alpaca_got, bars = M.fetch_alpaca("k", "s", ["MSFT"], getter=lambda *_: alpaca_raw)
        daily_got, daily_bars = M.fetch_alpaca_daily_bars("k", "s", ["MSFT"], now, getter=lambda *_: daily_raw)
        packet = M.build_capture(
            now, fred_got, fred, M.load_contract(), alpaca_status="READY",
            alpaca_raw=alpaca_got, bars=bars, daily_raw=daily_got,
            daily_bars=daily_bars,
        )
        self.assertEqual(packet["schema_version"], "free_market_data_capture/3")
        self.assertEqual(packet["fred"]["response_sha256"], M.sha256_bytes(fred_raw))
        self.assertEqual(packet["fred"]["raw_retention"], "TRANSIENT_NOT_PERSISTED")
        self.assertEqual(packet["alpaca"]["source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")
        self.assertEqual(packet["alpaca"]["status"], "READY")
        self.assertFalse(packet["authority"]["entry_authorized"])
        self.assertEqual(packet["alpaca"]["daily_timeframe"], "1Day")
        self.assertEqual(packet["alpaca"]["daily_bars"][0]["symbol"], "MSFT")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); M.publish(root, now, packet, alpaca_raw=alpaca_raw, daily_raw=daily_got)
            self.assertTrue((root/"data/latest_free_market_data.json").exists())
            self.assertTrue((root/"evidence/free_market_data/derived/2026-08-22/manifest.json").exists())
            self.assertFalse((root/"evidence/free_market_data/raw/2026-08-22/fred_vixcls.json.gz").exists())
            self.assertTrue((root/"evidence/free_market_data/raw/2026-08-22/alpaca_iex_daily_bars.json.gz").exists())

    def test_fred_derived_capture_survives_explicit_alpaca_block(self):
        now = dt.datetime(2026, 8, 22, 1, 2, 3, tzinfo=dt.timezone.utc)
        fred_raw = b'{"observations":[]}'
        fred = {"series_id":"VIXCLS", "observation_date":"2026-08-21", "value":"15.5"}
        packet = M.build_capture(
            now, fred_raw, fred, M.load_contract(),
            alpaca_status="BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL",
        )
        self.assertEqual(packet["fred"]["status"], "READY")
        self.assertEqual(packet["alpaca"]["bars"], [])
        self.assertIsNone(packet["alpaca"]["raw_sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish(root, now, packet)
            self.assertTrue((root/"evidence/free_market_data/derived/2026-08-22/manifest.json").exists())
            self.assertFalse((root/"evidence/free_market_data/raw/2026-08-22").exists())

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

    def test_missing_dedicated_credential_publishes_fred_and_blocks_only_alpaca(self):
        fred_raw = b'{"observations":[{"date":"2026-08-21","value":"15.5"}]}'
        fred = {"series_id":"VIXCLS", "observation_date":"2026-08-21", "value":"15.5"}
        contract = M.load_contract()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, {"FRED_API_KEY": "x"}, clear=True), \
             mock.patch("sys.argv", ["free_market_data.py", "--root", tmp]), \
             mock.patch.object(M, "load_contract", return_value=contract), \
             mock.patch.object(M, "fetch_fred", return_value=(fred_raw, fred)):
            self.assertEqual(M.main(), 0)
            packet = json.loads((Path(tmp)/"data/latest_free_market_data.json").read_text())
            self.assertEqual(packet["fred"]["status"], "READY")
            self.assertEqual(packet["alpaca"]["status"], "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL")
            self.assertFalse((Path(tmp)/"evidence/free_market_data/raw").exists())

    def test_old_shared_alpaca_credential_name_is_never_accepted_as_a_fallback(self):
        env = {"FRED_API_KEY": "x", "ALPACA_API_KEY": "old-shared-key", "ALPACA_API_SECRET": "old-shared-secret"}
        source = inspect.getsource(M)
        self.assertNotIn('os.getenv("ALPACA_API_KEY"', source)
        self.assertNotIn('os.getenv("ALPACA_API_SECRET"', source)

    def test_source_never_reads_the_old_shared_env_var_names(self):
        source = inspect.getsource(M)
        self.assertNotIn('os.getenv("ALPACA_API_KEY"', source)
        self.assertNotIn('os.getenv("ALPACA_API_SECRET"', source)
        self.assertIn('os.getenv("ALPACA_MARKET_DATA_API_KEY"', source)
        self.assertIn('os.getenv("ALPACA_MARKET_DATA_API_SECRET"', source)

    def test_alpaca_http_failure_preserves_fred_partial_publication(self):
        fred_raw = b'{"observations":[{"date":"2026-08-25","value":"15.5"}]}'
        fred = {"series_id":"VIXCLS", "observation_date":"2026-08-25", "value":"15.5"}
        contract = M.load_contract()
        env = {
            "FRED_API_KEY": "fred", "ALPACA_MARKET_DATA_API_KEY": "market",
            "ALPACA_MARKET_DATA_API_SECRET": "secret",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("sys.argv", ["free_market_data.py", "--root", tmp]), \
             mock.patch.object(M, "load_contract", return_value=contract), \
             mock.patch.object(M, "fetch_fred", return_value=(fred_raw, fred)), \
             mock.patch.object(M, "fetch_alpaca", side_effect=M.FreeMarketDataError("HTTP_ERROR:401")):
            self.assertEqual(M.main(), 0)
            packet = json.loads((Path(tmp)/"data/latest_free_market_data.json").read_text())
        self.assertEqual(packet["fred"]["status"], "READY")
        self.assertEqual(packet["alpaca"]["status"], "ALPACA_CAPTURE_FAILED:HTTP_ERROR:401")
        self.assertEqual(packet["alpaca"]["bars"], [])


if __name__ == "__main__": unittest.main()
