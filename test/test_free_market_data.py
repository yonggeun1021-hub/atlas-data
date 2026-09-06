#!/usr/bin/env python3
import copy
import datetime as dt
import gzip
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

# Two captures on the same UTC day: the exact case that used to destroy the
# earlier same-day raw response and derived observation via os.replace.
FIRST_CAPTURE = dt.datetime(2026, 7, 1, 6, 0, 0, tzinfo=dt.timezone.utc)
SECOND_CAPTURE = dt.datetime(2026, 7, 1, 12, 30, 0, tzinfo=dt.timezone.utc)
CAPTURE_DAY = "2026-07-01"


def _liquidity(observed_at):
    return {
        "status": "READY", "derivation_version": "fred_liquidity_current/v1",
        "source_scope": "FRED_OFFICIAL_SERIES_API",
        "raw_retention": "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED",
        "captured_at_utc": observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "series": [], "response_hashes": {},
        "derived_payload_sha256": M.sha256_bytes(M.canonical_bytes([])),
        "warnings": ["CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY"],
    }


def _contract_root(tmp):
    """A temp root carrying the real contract, so replay re-derives identically."""
    root = Path(tmp)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "free_market_data_contract.json").write_bytes(
        (ROOT / "config" / "free_market_data_contract.json").read_bytes()
    )
    return root


def _daily_getter(symbols, shift):
    start = dt.date(2026, 5, 1)
    bodies = {
        symbol: json.dumps({"bars": [{
            "o": 100 + step, "h": 103 + step, "l": 97 + step,
            "c": 100 + step + index * 0.01 + shift, "v": 1000 + step,
            "t": f"{(start + dt.timedelta(days=step)).isoformat()}T00:00:00Z",
        } for step in range(61)]}).encode()
        for index, symbol in enumerate(symbols)
    }
    return lambda url, *_: bodies[url.split("/stocks/", 1)[1].split("/", 1)[0]]


def _ready_capture(root, observed_at, *, shift=0.0):
    """Build a full READY packet for `root`'s contract without any network."""
    contract = M.load_contract(root / "config" / "free_market_data_contract.json")
    symbols = contract["alpaca"]["symbols"]
    fred_body = json.dumps({"observations": [{
        "date": "2026-06-30", "value": "15.5",
        "realtime_start": "2026-07-01", "realtime_end": "2026-07-01",
    }]}).encode()
    latest_body = json.dumps({"bars": {symbol: {
        "c": 100 + index + shift, "v": 1000, "t": "2026-06-30T19:59:00Z",
    } for index, symbol in enumerate(symbols)}}).encode()
    fred_raw, fred = M.fetch_fred("x", observed_at, getter=lambda *_: fred_body)
    alpaca_raw, bars = M.fetch_alpaca("k", "s", symbols, getter=lambda *_: latest_body)
    daily_raw, daily_bars = M.fetch_alpaca_daily_bars(
        "k", "s", symbols, observed_at, getter=_daily_getter(symbols, shift)
    )
    fred_bundle = M.FRED_PROVENANCE.build_evidence_bundle(observed_at, fred_raw)
    packet = M.build_capture(
        observed_at, fred_raw, fred, contract, alpaca_status="READY",
        fred_evidence=fred_bundle["pointer"], fred_liquidity=_liquidity(observed_at),
        alpaca_raw=alpaca_raw, bars=bars, daily_raw=daily_raw, daily_bars=daily_bars,
    )
    return {
        "packet": packet, "fred_bundle": fred_bundle,
        "alpaca_raw": alpaca_raw, "daily_raw": daily_raw,
    }


def _publish(root, observed_at, capture):
    return M.publish(
        root, observed_at, capture["packet"], fred_bundle=capture["fred_bundle"],
        alpaca_raw=capture["alpaca_raw"], daily_raw=capture["daily_raw"],
    )


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
        self.assertEqual(c["contract_version"], "free_market_data/3")
        self.assertEqual(c["alpaca"]["feed"], "iex")
        self.assertEqual(c["fred"]["raw_retention"], "APPEND_ONLY_CONTENT_ADDRESSED")
        self.assertTrue(c["fred"]["partial_publish_authorized"])
        self.assertEqual(c["alpaca"]["credential_scope"], "DEDICATED_MARKET_DATA_ONLY")
        self.assertEqual(c["alpaca"]["trend_symbols"], ["SPY", "QQQ", "IWM"])
        self.assertEqual(c["fred"]["liquidity_series"], ["WRESBAL", "TOTBKCR"])
        self.assertTrue(c["authority"]["evidence_capture_only"])
        self.assertFalse(c["authority"]["us_breadth_authorized"])
        self.assertFalse(c["authority"]["order_authorized"])
        self.assertFalse(c["authority"]["trading_authorized"])

    def test_fetch_build_and_publish_preserve_alpaca_and_append_only_fred_raw(self):
        now = dt.datetime(2026, 8, 22, 1, 2, 3, tzinfo=dt.timezone.utc)
        fred_raw = json.dumps({"observations":[{"date":"2026-08-21","value":"15.5","realtime_start":"2026-08-22","realtime_end":"2026-08-22"}]}).encode()
        alpaca_raw = json.dumps({"bars":{"MSFT":{"c":500.1,"v":1200,"t":"2026-08-21T19:59:00Z"}}}).encode()
        daily_raw = json.dumps({"bars":[{"o":498.0,"h":502.0,"l":497.0,"c":500.1,"v":1200,"t":"2026-08-21T00:00:00Z"}]}).encode()
        fred_got, fred = M.fetch_fred("x", now, getter=lambda *_: fred_raw)
        alpaca_got, bars = M.fetch_alpaca("k", "s", ["MSFT"], getter=lambda *_: alpaca_raw)
        daily_got, daily_bars = M.fetch_alpaca_daily_bars("k", "s", ["MSFT"], now, getter=lambda *_: daily_raw)
        fred_bundle = M.FRED_PROVENANCE.build_evidence_bundle(now, fred_raw)
        contract = M.load_contract()
        contract["alpaca"]["trend_symbols"] = ["MSFT"]
        contract["alpaca"]["sector_reference_symbols"] = []
        contract["alpaca"]["return_windows_sessions"] = [0]
        liquidity = {
            "status": "READY", "derivation_version": "fred_liquidity_current/v1",
            "source_scope": "FRED_OFFICIAL_SERIES_API",
            "raw_retention": "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED",
            "captured_at_utc": "2026-08-22T01:02:03Z", "series": [],
            "response_hashes": {}, "derived_payload_sha256": M.sha256_bytes(M.canonical_bytes([])),
            "warnings": ["CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY"],
        }
        packet = M.build_capture(
            now, fred_got, fred, contract, alpaca_status="READY",
            fred_evidence=fred_bundle["pointer"],
            fred_liquidity=liquidity,
            alpaca_raw=alpaca_got, bars=bars, daily_raw=daily_got,
            daily_bars=daily_bars,
        )
        self.assertEqual(packet["schema_version"], "free_market_data_capture/5")
        self.assertEqual(packet["fred"]["response_sha256"], M.sha256_bytes(fred_raw))
        self.assertEqual(packet["fred"]["raw_retention"], "APPEND_ONLY_CONTENT_ADDRESSED")
        self.assertEqual(packet["alpaca"]["source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")
        self.assertEqual(packet["alpaca"]["status"], "READY")
        self.assertFalse(packet["authority"]["entry_authorized"])
        self.assertEqual(packet["alpaca"]["daily_timeframe"], "1Day")
        self.assertEqual(packet["alpaca"]["daily_bars"][0]["symbol"], "MSFT")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); M.publish(root, now, packet, fred_bundle=fred_bundle, alpaca_raw=alpaca_raw, daily_raw=daily_got)
            self.assertTrue((root/"data/latest_free_market_data.json").exists())
            self.assertTrue((root/"evidence/free_market_data/derived/2026-08-22/manifest.json").exists())
            self.assertTrue((root/fred_bundle["pointer"]["raw_path"]).exists())
            self.assertTrue((root/fred_bundle["pointer"]["manifest_path"]).exists())
            replay = M.FRED_PROVENANCE.validate_evidence(root, packet["fred"]["evidence"])
            self.assertEqual(replay["observation"]["value"], "15.5")
            self.assertTrue((root/"evidence/free_market_data/raw/2026-08-22/alpaca_iex_daily_bars.json.gz").exists())

    def test_fred_derived_capture_survives_explicit_alpaca_block(self):
        now = dt.datetime(2026, 8, 22, 1, 2, 3, tzinfo=dt.timezone.utc)
        fred_raw = b'{"observations":[{"date":"2026-08-21","value":"15.5"}]}'
        fred = {"series_id":"VIXCLS", "observation_date":"2026-08-21", "value":"15.5"}
        fred_bundle = M.FRED_PROVENANCE.build_evidence_bundle(now, fred_raw)
        packet = M.build_capture(
            now, fred_raw, fred, M.load_contract(),
            fred_evidence=fred_bundle["pointer"],
            fred_liquidity={
                "status": "FRED_LIQUIDITY_CAPTURE_FAILED:TEST",
                "derivation_version": "fred_liquidity_current/v1",
                "source_scope": "FRED_OFFICIAL_SERIES_API",
                "raw_retention": "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED",
                "captured_at_utc": "2026-08-22T01:02:03Z", "series": [],
                "response_hashes": {}, "derived_payload_sha256": M.sha256_bytes(M.canonical_bytes([])),
                "warnings": ["CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY"],
            },
            alpaca_status="BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL",
        )
        self.assertEqual(packet["fred"]["status"], "READY")
        self.assertEqual(packet["alpaca"]["bars"], [])
        self.assertIsNone(packet["alpaca"]["raw_sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            M.publish(root, now, packet, fred_bundle=fred_bundle)
            self.assertTrue((root/"evidence/free_market_data/derived/2026-08-22/manifest.json").exists())
            self.assertTrue((root/fred_bundle["pointer"]["raw_path"]).exists())

    def test_missing_or_malformed_provider_data_fails_closed(self):
        now = dt.datetime(2026, 8, 22, tzinfo=dt.timezone.utc)
        with self.assertRaisesRegex(M.FreeMarketDataError, "FRED_OBSERVATIONS_MISSING"):
            M.fetch_fred("x", now, getter=lambda *_: b'{"observations":[]}')
        with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_NO_SYMBOLS_RETURNED"):
            M.fetch_alpaca("k", "s", ["MSFT"], getter=lambda *_: b'{"bars":{}}')

    def test_liquidity_normalizes_units_and_discards_raw_bodies(self):
        now = dt.datetime(2026, 8, 28, 1, 2, 3, tzinfo=dt.timezone.utc)
        responses = {
            "series": json.dumps({"seriess": [{
                "title": "Reserve Balances", "frequency": "Weekly",
                "units": "Billions of U.S. Dollars",
            }]}).encode(),
            "observations": json.dumps({"observations": [
                {"date": "2026-08-19", "value": "3.1"},
                {"date": "2026-08-26", "value": "3.2"},
            ]}).encode(),
        }
        def getter(url, *_):
            return responses["observations" if "observations" in url else "series"]
        result = M.fetch_fred_liquidity("x", now, ["WRESBAL"], getter=getter)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["series"][0]["value"], "3200")
        self.assertEqual(result["series"][0]["change"], "100")
        self.assertNotIn("raw", json.dumps(result).lower().replace("raw_retention", ""))

    def test_us_market_reference_reports_returns_without_interpreting_regime(self):
        contract = M.load_contract()
        symbols = contract["alpaca"]["trend_symbols"] + contract["alpaca"]["sector_reference_symbols"]
        bars = []
        start = dt.date(2026, 5, 1)
        for symbol in symbols:
            for index in range(61):
                bars.append({
                    "symbol": symbol,
                    "opened_at": f"{(start + dt.timedelta(days=index)).isoformat()}T00:00:00Z",
                    "open": str(100 + index), "high": str(101 + index),
                    "low": str(99 + index), "close": str(100 + index),
                    "volume": "1000",
                })
        result = M.derive_us_market_reference(bars, contract)
        self.assertEqual(result["status"], "READY")
        self.assertEqual([row["symbol"] for row in result["trend_etfs"]], ["SPY", "QQQ", "IWM"])
        self.assertEqual(len(result["sector_etfs"]), 12)
        self.assertEqual(result["schema_version"], "us_market_reference/v2")
        self.assertEqual(result["proxy_axes"]["BREADTH"]["status"], "OBSERVED")
        self.assertEqual(result["proxy_axes"]["LEADERSHIP"]["status"], "OBSERVED")
        self.assertEqual(
            result["proxy_axes"]["BREADTH"]["measurement"]["observed_count"],
            14,
        )
        self.assertEqual(
            result["proxy_axes"]["LEADERSHIP"]["measurement"]["observed_count"],
            12,
        )
        self.assertEqual(result["interpretation"], "OBSERVED_UNCLASSIFIED")
        self.assertNotIn("RISK_ON", json.dumps(result))


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
             mock.patch.object(M, "fetch_fred", return_value=(fred_raw, fred)), \
             mock.patch.object(M, "fetch_fred_liquidity", return_value={
                 "status": "READY", "derivation_version": "fred_liquidity_current/v1",
                 "source_scope": "FRED_OFFICIAL_SERIES_API",
                 "raw_retention": "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED",
                 "captured_at_utc": "2026-08-22T01:02:03Z", "series": [],
                 "response_hashes": {}, "derived_payload_sha256": M.sha256_bytes(M.canonical_bytes([])),
                 "warnings": ["CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY"],
             }):
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
             mock.patch.object(M, "fetch_fred_liquidity", return_value={
                 "status": "READY", "derivation_version": "fred_liquidity_current/v1",
                 "source_scope": "FRED_OFFICIAL_SERIES_API",
                 "raw_retention": "TRANSIENT_NOT_PERSISTED_HASH_ATTESTED",
                 "captured_at_utc": "2026-08-25T01:02:03Z", "series": [],
                 "response_hashes": {}, "derived_payload_sha256": M.sha256_bytes(M.canonical_bytes([])),
                 "warnings": ["CURRENT_SNAPSHOT_ONLY_NOT_HISTORICAL_PIT_REPLAY"],
             }), \
             mock.patch.object(M, "fetch_alpaca", side_effect=M.FreeMarketDataError("HTTP_ERROR:401")):
            self.assertEqual(M.main(), 0)
            packet = json.loads((Path(tmp)/"data/latest_free_market_data.json").read_text())
        self.assertEqual(packet["fred"]["status"], "READY")
        self.assertEqual(packet["alpaca"]["status"], "ALPACA_CAPTURE_FAILED:HTTP_ERROR:401")
        self.assertEqual(packet["alpaca"]["bars"], [])


class ImmutableRevisionRetentionTests(unittest.TestCase):
    """A second capture on the same UTC day must not destroy the first one.

    Raw provider bytes are content-addressed, so identical content
    deduplicates across observation times; the derived observation revision is
    capture-time addressed, so each genuine observation keeps its own identity.
    """

    def _daily_revisions(self, root):
        store = root / "evidence/free_market_data/raw/alpaca/daily_bars"
        return sorted(path.name for path in store.iterdir()) if store.is_dir() else []

    def _derived_revisions(self, root, day=CAPTURE_DAY):
        base = root / "evidence/free_market_data/derived" / day
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def test_two_same_day_publications_both_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            first = _ready_capture(root, FIRST_CAPTURE, shift=0.0)
            second = _ready_capture(root, SECOND_CAPTURE, shift=0.5)
            self.assertNotEqual(
                first["packet"]["alpaca"]["daily_raw_sha256"],
                second["packet"]["alpaca"]["daily_raw_sha256"],
            )
            first_receipt = _publish(root, FIRST_CAPTURE, first)
            second_receipt = _publish(root, SECOND_CAPTURE, second)

            self.assertNotEqual(
                first_receipt["observation_revision_id"],
                second_receipt["observation_revision_id"],
            )
            for receipt, capture in (
                (first_receipt, first), (second_receipt, second)
            ):
                retained = json.loads(
                    (root / receipt["derived_revision_path"]).read_text()
                )
                self.assertEqual(retained, capture["packet"])
            self.assertEqual(len(self._derived_revisions(root)), 2)
            self.assertEqual(len(self._daily_revisions(root)), 2)
            for capture in (first, second):
                pointer = capture["packet"]["alpaca"]["daily_raw_evidence"]
                self.assertEqual(
                    M.read_alpaca_raw_revision(root, pointer), capture["daily_raw"]
                )
            # The latest-wins compatibility paths still resolve to the newest.
            self.assertEqual(
                json.loads((root / "data/latest_free_market_data.json").read_text()),
                second["packet"],
            )
            self.assertEqual(
                json.loads(
                    (root / "evidence/free_market_data/derived" / CAPTURE_DAY
                     / "manifest.json").read_text()
                ),
                second["packet"],
            )

    def test_first_packet_still_replays_after_the_second_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            first = _ready_capture(root, FIRST_CAPTURE, shift=0.0)
            second = _ready_capture(root, SECOND_CAPTURE, shift=0.5)
            _publish(root, FIRST_CAPTURE, first)
            _publish(root, SECOND_CAPTURE, second)
            for capture in (first, second):
                packet = capture["packet"]
                replay = M.validate_alpaca_daily_evidence(root, packet)
                self.assertEqual(
                    replay["raw_response_sha256"],
                    packet["alpaca"]["daily_raw_sha256"],
                )
                self.assertEqual(
                    replay["raw_path"],
                    packet["alpaca"]["daily_raw_evidence"]["raw_path"],
                )
                self.assertEqual(replay["reference"], packet["us_market_reference"])
            self.assertNotEqual(
                M.validate_alpaca_daily_evidence(root, first["packet"])["raw_response_sha256"],
                second["packet"]["alpaca"]["daily_raw_sha256"],
            )

    def test_identical_response_bytes_deduplicate_across_observation_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            first = _ready_capture(root, FIRST_CAPTURE, shift=0.0)
            second = _ready_capture(root, SECOND_CAPTURE, shift=0.0)
            self.assertEqual(first["daily_raw"], second["daily_raw"])
            _publish(root, FIRST_CAPTURE, first)
            _publish(root, SECOND_CAPTURE, second)
            self.assertEqual(
                first["packet"]["alpaca"]["daily_raw_evidence"],
                second["packet"]["alpaca"]["daily_raw_evidence"],
            )
            self.assertEqual(len(self._daily_revisions(root)), 1)
            # Same bytes, but two genuine observations at two capture times.
            self.assertNotEqual(
                first["packet"]["observed_at_utc"],
                second["packet"]["observed_at_utc"],
            )
            self.assertEqual(len(self._derived_revisions(root)), 2)

    def test_identical_publication_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            capture = _ready_capture(root, FIRST_CAPTURE)
            first_receipt = _publish(root, FIRST_CAPTURE, capture)
            revision = root / first_receipt["derived_revision_path"]
            before = revision.read_bytes()
            second_receipt = _publish(root, FIRST_CAPTURE, capture)
            self.assertEqual(first_receipt, second_receipt)
            self.assertEqual(revision.read_bytes(), before)
            self.assertEqual(len(self._derived_revisions(root)), 1)
            self.assertEqual(len(self._daily_revisions(root)), 1)

    def test_partial_capture_publishes_a_derived_revision_without_alpaca_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            fred_raw = b'{"observations":[{"date":"2026-06-30","value":"15.5"}]}'
            fred = {"series_id": "VIXCLS", "observation_date": "2026-06-30", "value": "15.5"}
            bundle = M.FRED_PROVENANCE.build_evidence_bundle(FIRST_CAPTURE, fred_raw)
            packet = M.build_capture(
                FIRST_CAPTURE, fred_raw, fred,
                M.load_contract(root / "config" / "free_market_data_contract.json"),
                fred_evidence=bundle["pointer"],
                fred_liquidity=_liquidity(FIRST_CAPTURE),
                alpaca_status="ALPACA_CAPTURE_FAILED:HTTP_ERROR:401",
            )
            self.assertIsNone(packet["alpaca"]["raw_evidence"])
            self.assertIsNone(packet["alpaca"]["daily_raw_evidence"])
            receipt = M.publish(root, FIRST_CAPTURE, packet, fred_bundle=bundle)
            self.assertEqual(receipt["alpaca_raw_revision_paths"], {})
            self.assertEqual(
                json.loads((root / receipt["derived_revision_path"]).read_text()),
                packet,
            )
            self.assertTrue((root / bundle["pointer"]["raw_path"]).exists())
            self.assertFalse(
                (root / "evidence/free_market_data/raw/alpaca").exists()
            )
            self.assertFalse(
                (root / "evidence/free_market_data/raw" / CAPTURE_DAY).exists()
            )

    def test_conflicting_bytes_at_an_immutable_address_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            capture = _ready_capture(root, FIRST_CAPTURE)
            receipt = _publish(root, FIRST_CAPTURE, capture)
            pointer = capture["packet"]["alpaca"]["daily_raw_evidence"]

            (root / receipt["derived_revision_path"]).write_bytes(b"{}\n")
            with self.assertRaisesRegex(M.FreeMarketDataError, "DERIVED_REVISION_CONFLICT"):
                _publish(root, FIRST_CAPTURE, capture)
            (root / receipt["derived_revision_path"]).write_bytes(
                json.dumps(capture["packet"], indent=2, sort_keys=True).encode() + b"\n"
            )

            (root / pointer["raw_path"]).write_bytes(
                M.FRED_PROVENANCE.deterministic_gzip(b'{"responses":{}}')
            )
            with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_RAW_REVISION_CONFLICT"):
                _publish(root, FIRST_CAPTURE, capture)
            with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_RAW_FILE_BYTES_MISMATCH"):
                M.read_alpaca_raw_revision(root, pointer)
            with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_RAW_FILE_BYTES_MISMATCH"):
                M.validate_alpaca_daily_evidence(root, capture["packet"])

    def test_a_packet_that_cannot_prove_its_own_hash_is_never_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            capture = _ready_capture(root, FIRST_CAPTURE)
            capture["packet"]["alpaca"]["daily_bars"][0]["close"] = "999999"
            with self.assertRaisesRegex(M.FreeMarketDataError, "PACKET_SHA256_MISMATCH"):
                _publish(root, FIRST_CAPTURE, capture)
            self.assertFalse((root / "evidence/free_market_data/derived").exists())

    def test_a_pointer_outside_the_evidence_store_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            capture = _ready_capture(root, FIRST_CAPTURE)
            _publish(root, FIRST_CAPTURE, capture)
            escaped = dict(capture["packet"]["alpaca"]["daily_raw_evidence"])
            escaped["raw_path"] = "../alpaca_iex_daily_bars.json.gz"
            with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_RAW_PATH_INVALID"):
                M.read_alpaca_raw_revision(root, escaped)


class LegacyPacketCompatibilityTests(unittest.TestCase):
    """Packets published before pinned revisions must keep replaying, and must
    never be rebound to bytes captured later on the same day."""

    @staticmethod
    def _as_legacy(packet):
        legacy = copy.deepcopy(packet)
        legacy["alpaca"].pop("raw_evidence")
        legacy["alpaca"].pop("daily_raw_evidence")
        unsigned = {k: v for k, v in legacy.items() if k != "packet_sha256"}
        legacy["packet_sha256"] = M.sha256_bytes(M.canonical_bytes(unsigned))
        return legacy

    def test_legacy_packet_resolves_the_unchanged_per_day_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            capture = _ready_capture(root, FIRST_CAPTURE)
            _publish(root, FIRST_CAPTURE, capture)
            legacy = self._as_legacy(capture["packet"])
            replay = M.validate_alpaca_daily_evidence(root, legacy)
            self.assertEqual(
                replay["raw_path"],
                f"evidence/free_market_data/raw/{CAPTURE_DAY}/alpaca_iex_daily_bars.json.gz",
            )
            self.assertEqual(
                replay["raw_response_sha256"], legacy["alpaca"]["daily_raw_sha256"]
            )
            self.assertEqual(replay["reference"], legacy["us_market_reference"])

    def test_legacy_packet_is_not_rebound_after_a_later_same_day_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            first = _ready_capture(root, FIRST_CAPTURE, shift=0.0)
            second = _ready_capture(root, SECOND_CAPTURE, shift=0.5)
            _publish(root, FIRST_CAPTURE, first)
            legacy = self._as_legacy(first["packet"])
            _publish(root, SECOND_CAPTURE, second)

            compat = root / "evidence/free_market_data/raw" / CAPTURE_DAY / "alpaca_iex_daily_bars.json.gz"
            self.assertEqual(
                M.sha256_bytes(gzip.decompress(compat.read_bytes())),
                second["packet"]["alpaca"]["daily_raw_sha256"],
            )
            replay = M.validate_alpaca_daily_evidence(root, legacy)
            self.assertEqual(
                replay["raw_response_sha256"], legacy["alpaca"]["daily_raw_sha256"]
            )
            self.assertNotEqual(
                replay["raw_response_sha256"],
                second["packet"]["alpaca"]["daily_raw_sha256"],
            )
            self.assertTrue(replay["raw_path"].startswith(M.ALPACA_RAW_STORE))
            self.assertEqual(replay["reference"], legacy["us_market_reference"])

    def test_pre_existing_compatibility_bytes_are_preserved_before_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            first = _ready_capture(root, FIRST_CAPTURE, shift=0.0)
            second = _ready_capture(root, SECOND_CAPTURE, shift=0.5)
            _publish(root, FIRST_CAPTURE, first)
            # Simulate a day whose only surviving artifact is the legacy
            # compatibility file written by the previous os.replace publisher.
            for kind in ("latest_bars", "daily_bars"):
                revision = (
                    root / M.ALPACA_RAW_STORE / kind
                    / first["packet"]["alpaca"][
                        "raw_sha256" if kind == "latest_bars" else "daily_raw_sha256"
                    ]
                )
                for path in sorted(revision.iterdir()):
                    path.unlink()
                revision.rmdir()
            legacy = self._as_legacy(first["packet"])

            _publish(root, SECOND_CAPTURE, second)
            replay = M.validate_alpaca_daily_evidence(root, legacy)
            self.assertEqual(
                replay["raw_response_sha256"], legacy["alpaca"]["daily_raw_sha256"]
            )
            self.assertEqual(
                M.read_alpaca_raw_revision(
                    root, first["packet"]["alpaca"]["daily_raw_evidence"]
                ),
                first["daily_raw"],
            )

    def test_a_missing_daily_response_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            capture = _ready_capture(root, FIRST_CAPTURE)
            legacy = self._as_legacy(capture["packet"])
            with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_DAILY_RAW_INVALID"):
                M.validate_alpaca_daily_evidence(root, legacy)
            with self.assertRaisesRegex(M.FreeMarketDataError, "ALPACA_RAW_REVISION_MISSING"):
                M.validate_alpaca_daily_evidence(root, capture["packet"])


if __name__ == "__main__": unittest.main()
