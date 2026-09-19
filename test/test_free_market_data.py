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
import urllib.parse

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
        self.assertTrue({"ANET", "CRDO", "MU", "SNDK"} <= set(c["alpaca"]["symbols"]))
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


class AlpacaRawPreservationByteIdentityTests(unittest.TestCase):
    """Guards a property nothing else in this file checks directly.

    Every other test here compares the *decompressed* payload's sha256
    (``read_alpaca_raw_revision`` decompresses before returning). A consumer
    that instead pins the raw gzip container bytes read straight off disk at
    the mutable ``raw/<day>/...`` compatibility path -- e.g. a packet that
    records ``sha256(file_bytes_on_disk)`` rather than
    ``sha256(decompressed_payload)`` -- silently depends on
    ``_preserve_prior_alpaca_raw`` reproducing that exact container, not just
    a container that decompresses to the same thing.

    That reproduction is not incidental: both the original write (the
    producer's own capture-time call in ``publish``) and the later
    preservation call (``_preserve_prior_alpaca_raw``, invoked right before a
    second same-day capture would overwrite the compatibility file) route the
    same payload through ``FRED_PROVENANCE.deterministic_gzip``. If that
    encoder ever stopped being byte-stable across two calls -- a different
    library, a real mtime, a different compresslevel or filename in the gzip
    header -- preservation would keep "working" (the decompressed content
    would still match, so every *other* test here would keep passing) while
    silently no longer reproducing the exact bytes a consumer pinned by file
    hash. This class asserts the byte-exact property at its source instead of
    against a hardcoded digest, so it tracks the code rather than today's
    data.
    """

    def test_deterministic_gzip_produces_identical_bytes_for_the_same_payload_every_call(self):
        payload = b'{"responses":{"SPY":{"bars":[{"t":"2026-07-04T00:00:00Z","c":1}]}}}'
        first = M.FRED_PROVENANCE.deterministic_gzip(payload)
        second = M.FRED_PROVENANCE.deterministic_gzip(payload)
        self.assertEqual(
            first, second,
            "deterministic_gzip must be byte-stable across calls, or a later "
            "preservation call can no longer reproduce an earlier write's "
            "exact container",
        )
        # A container that merely decompresses to the same payload would not
        # be enough to make the point above -- require byte equality above,
        # this just confirms the payload itself round-trips.
        self.assertEqual(gzip.decompress(first), payload)

    def test_preserving_a_compatibility_capture_reproduces_its_gzip_container_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            payload = b'{"responses":{"QQQ":{"bars":[{"t":"2026-07-05T00:00:00Z","c":2}]}}}'

            # Mirrors the producer's own capture-time write of the mutable
            # per-day compatibility pointer (free_market_data.py's `publish`,
            # which runs the raw response through the same encoder).
            original_gzip_bytes = M.FRED_PROVENANCE.deterministic_gzip(payload)
            compat_path = (
                root / "evidence/free_market_data/raw/2026-07-05"
                / M.ALPACA_RAW_KINDS["daily_bars"]
            )
            compat_path.parent.mkdir(parents=True, exist_ok=True)
            compat_path.write_bytes(original_gzip_bytes)

            # Mirrors what `publish` does immediately before a second
            # same-day capture would overwrite that same mutable path.
            M._preserve_prior_alpaca_raw(root, compat_path, "daily_bars")

            preserved_path = (
                root / M.ALPACA_RAW_STORE / "daily_bars" / M.sha256_bytes(payload)
                / M.ALPACA_RAW_KINDS["daily_bars"]
            )
            self.assertTrue(preserved_path.exists())
            # The property a pinned-by-file-hash consumer depends on: not
            # merely that both sides decompress to `payload` (any correct
            # gzip encoder would do that), but that preservation reproduces
            # the exact bytes the original producer wrote to the
            # compatibility path.
            self.assertEqual(preserved_path.read_bytes(), original_gzip_bytes)


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




# ---------------------------------------------------------------------------
# Historical range receipt (collectors/free_market_data_history.py).
#
# Registered here rather than in a new test file on purpose: a new test/ file
# must also be added to run_all.py's APPROVED_TESTS by filename or all four
# regression shards fail instantly, and this module is the daily collector's
# own regression. The two are checked against each other below, so a change to
# one that silently diverges from the other fails here.
# ---------------------------------------------------------------------------

HISTORY_SPEC = importlib.util.spec_from_file_location(
    "free_market_data_history", ROOT / "collectors" / "free_market_data_history.py"
)
H = importlib.util.module_from_spec(HISTORY_SPEC)
HISTORY_SPEC.loader.exec_module(H)

FAKE_FRED_KEY = "fake-fred-key-never-real"
FAKE_ALPACA_KEY = "fake-alpaca-key"
FAKE_ALPACA_SECRET = "fake-alpaca-secret"


def _history_bar(day, step):
    return {
        "o": 100 + step, "h": 103 + step, "l": 97 + step, "c": 101 + step,
        "v": 1000 + step, "t": f"{day.isoformat()}T05:00:00Z",
    }


def _history_getter(*, bars_per_symbol=3, split_pages=False, calls=None):
    """Fake HTTP layer for the history collector. No network, no real key."""
    start = dt.date(2026, 5, 4)

    def getter(url, headers=None):
        if calls is not None:
            calls.append((url, dict(headers or {})))
        base, _, query = url.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        if "/stocks/" in base:
            symbol = base.split("/stocks/", 1)[1].split("/", 1)[0]
            if params.get("feed") == "sip":
                # The history module imports its own copy of the daily
                # collector, so its exception class is a distinct object; the
                # fake transport must raise the class that module catches.
                raise H.FreeMarketDataError("HTTP_ERROR:403")
            rows = [
                _history_bar(start + dt.timedelta(days=step), step)
                for step in range(bars_per_symbol)
            ]
            if split_pages and not params.get("page_token"):
                return json.dumps(
                    {"bars": rows[:1], "next_page_token": "page-2"}
                ).encode()
            if split_pages:
                return json.dumps({"bars": rows[1:], "next_page_token": None}).encode()
            return json.dumps({"bars": rows, "next_page_token": None}).encode()
        if base.endswith("/fred/series/vintagedates"):
            return json.dumps({
                "count": 2, "vintage_dates": ["2026-05-05", "2026-05-12"],
            }).encode()
        if base.endswith("/fred/series/observations"):
            series_id = params["series_id"]
            return json.dumps({"count": 2, "observations": [
                {"realtime_start": "2026-05-05", "realtime_end": "2026-05-11",
                 "date": "2026-05-04", "value": "1.5"},
                {"realtime_start": "2026-05-12", "realtime_end": "9999-12-31",
                 "date": "2026-05-04", "value": f"2.5{len(series_id)}"},
            ]}).encode()
        if base.endswith("/fred/series"):
            return json.dumps({"seriess": [{
                "title": params["series_id"], "frequency": "Weekly",
                "units": "Millions of Dollars",
            }]}).encode()
        raise AssertionError(f"unexpected url {base}")

    return getter


class HistoricalRangeReceiptTests(unittest.TestCase):
    """The added range/vintage entry point, and the daily path it must not move."""

    def _contract(self):
        return M.load_contract(ROOT / "config" / "free_market_data_contract.json")

    def test_score_symbols_come_from_the_contract_and_stay_inside_it(self):
        contract = self._contract()
        symbols = H.score_symbols(contract)
        alpaca = contract["alpaca"]
        proxy = alpaca["current_proxy_axes"]
        self.assertEqual(symbols, sorted(set(
            alpaca["trend_symbols"] + proxy["breadth_symbols"]
            + proxy["leadership_symbols"] + [proxy["leadership_benchmark"]]
        )))
        self.assertTrue(set(symbols) <= set(alpaca["symbols"]))
        # The axes are read, never redefined: the thresholds and the symbol
        # lists this module consumes are exactly the contract's own.
        self.assertEqual(alpaca["trend_symbols"], ["SPY", "QQQ", "IWM"])
        self.assertEqual(len(proxy["breadth_symbols"]), 14)
        self.assertEqual(len(proxy["leadership_symbols"]), 12)
        self.assertEqual(proxy["leadership_window_sessions"], 20)

    def test_normalized_bar_is_identical_to_the_daily_collector(self):
        """Same bytes, same rows -- the two must not drift apart silently."""
        symbols = ["SPY", "QQQ"]
        getter = _history_getter(bars_per_symbol=4)
        observed_at = dt.datetime(2026, 5, 8, 12, 0, 0, tzinfo=dt.timezone.utc)
        _, daily_rows = M.fetch_alpaca_daily_bars(
            FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET, symbols, observed_at, getter
        )
        history_rows = []
        for symbol in symbols:
            _, bars, _ = H.fetch_alpaca_daily_bars_range(
                FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET, symbol,
                "2026-05-04", "2026-05-08", feed="iex", getter=getter,
            )
            history_rows.extend(bars)
        key = lambda row: (row["symbol"], row["opened_at"])
        self.assertEqual(sorted(history_rows, key=key), sorted(daily_rows, key=key))

    def test_explicit_window_is_sent_and_every_page_is_followed(self):
        calls = []
        getter = _history_getter(bars_per_symbol=3, split_pages=True, calls=calls)
        pages, bars, summary = H.fetch_alpaca_daily_bars_range(
            FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET, "SPY",
            "2026-05-04", "2026-05-06", feed="iex", getter=getter,
        )
        self.assertEqual(len(pages), 2)
        self.assertEqual(summary["bar_count"], 3)
        self.assertEqual(summary["first_session_date"], "2026-05-04")
        self.assertEqual(summary["last_session_date"], "2026-05-06")
        first = dict(urllib.parse.parse_qsl(calls[0][0].partition("?")[2]))
        self.assertEqual(first["start"], "2026-05-04")
        self.assertEqual(first["end"], "2026-05-06")
        self.assertNotIn("page_token", first)
        second = dict(urllib.parse.parse_qsl(calls[1][0].partition("?")[2]))
        self.assertEqual(second["page_token"], "page-2")
        # Credentials travel in Alpaca's own headers, never in the URL.
        self.assertNotIn(FAKE_ALPACA_SECRET, calls[0][0])
        self.assertEqual(calls[0][1]["APCA-API-SECRET-KEY"], FAKE_ALPACA_SECRET)

    def test_window_feasibility_reports_ten_windows_only_at_221_bars(self):
        self.assertFalse(H.window_feasibility(220)["meets_window_target"])
        self.assertTrue(H.window_feasibility(221)["meets_window_target"])
        self.assertEqual(H.window_feasibility(221)["scored_sessions_possible"], 200)
        self.assertEqual(
            H.window_feasibility(0)["bars_required_for_window_target"], 221
        )

    def test_vintage_request_asks_alfred_for_the_full_realtime_window(self):
        calls = []
        getter = _history_getter(calls=calls)
        contract = self._contract()
        _, rows = H.fetch_fred_vintage_observations(
            FAKE_FRED_KEY, "TOTBKCR", "2026-05-01", "2026-05-31",
            contract["fred"]["output_type"], getter=getter,
        )
        params = dict(urllib.parse.parse_qsl(calls[0][0].partition("?")[2]))
        self.assertEqual(params["realtime_start"], H.FRED_REALTIME_MIN)
        self.assertEqual(params["realtime_end"], H.FRED_REALTIME_MAX)
        self.assertEqual(params["output_type"], str(contract["fred"]["output_type"]))
        # Both revisions of the same observation date survive, each with its
        # own publication window -- that is what the current-revision request
        # in the daily path cannot give.
        self.assertEqual([row["available_from"] for row in rows],
                         ["2026-05-05", "2026-05-12"])
        self.assertEqual({row["observation_date"] for row in rows}, {"2026-05-04"})

    def test_availability_never_returns_a_value_published_later(self):
        rows = [
            {"observation_date": "2026-05-04", "value": "1.5",
             "available_from": "2026-05-05", "available_to": "2026-05-11"},
            {"observation_date": "2026-05-04", "value": "2.5",
             "available_from": "2026-05-12", "available_to": "9999-12-31"},
            {"observation_date": "2026-05-11", "value": ".",
             "available_from": "2026-05-12", "available_to": "9999-12-31"},
        ]
        self.assertEqual(H.observations_available_at(rows, "2026-05-04"), [])
        early = H.observations_available_at(rows, "2026-05-06")
        self.assertEqual([row["value"] for row in early], ["1.5"])
        late = H.observations_available_at(rows, "2026-05-20")
        self.assertEqual([row["value"] for row in late], ["2.5"])
        # The revised value must not leak backwards into the earlier as-of day:
        # that is the TOTBKCR failure mode the vintage path exists to prevent.
        self.assertNotIn("2.5", [row["value"] for row in early])

    def test_fetch_writes_only_under_the_history_store(self):
        contract = self._contract()
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            report = H.fetch(
                root, FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET,
                contract, "2026-05-04", "2026-05-06", feed="iex",
                symbols=["SPY", "QQQ"], getter=_history_getter(bars_per_symbol=3),
            )
            self.assertEqual(report["status"], "PASS")
            written = sorted(
                path.relative_to(root).as_posix()
                for path in root.rglob("*") if path.is_file()
            )
            self.assertTrue(written)
            for relative in written:
                if relative == "config/free_market_data_contract.json":
                    continue
                self.assertTrue(
                    relative.startswith(f"{H.HISTORY_STORE}/"),
                    f"{relative} is outside the history store",
                )
            # The daily capture's own outputs must not exist after a range run.
            for untouched in (
                "data/latest_free_market_data.json",
                "evidence/free_market_data/derived",
                "evidence/free_market_data/raw",
                "evidence/free_market_data/fred",
            ):
                self.assertFalse((root / untouched).exists(), untouched)
            self.assertTrue((root / report["receipt_path"]).is_file())
            receipt = json.loads((root / report["receipt_path"]).read_text("utf-8"))
            # The weekly liquidity change needs the observation before the
            # window start, so the FRED window is extended backwards and both
            # windows are recorded rather than the extension being implicit.
            self.assertEqual(receipt["requested_window"],
                             {"start": "2026-05-04", "end": "2026-05-06"})
            for series in receipt["fred"]["series"]:
                self.assertEqual(series["observation_lead_days"],
                                 H.FRED_OBSERVATION_LEAD_DAYS)
                self.assertLess(series["observation_start_applied"], "2026-05-04")
            self.assertEqual(receipt["daily_paths_written"], [])
            self.assertFalse(receipt["authority"]["trading_authorized"])
            self.assertFalse(receipt["authority"]["us_breadth_authorized"])

    def test_receipts_and_manifests_never_carry_a_credential(self):
        contract = self._contract()
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            H.fetch(
                root, FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET,
                contract, "2026-05-04", "2026-05-06", feed="iex",
                symbols=["SPY"], getter=_history_getter(bars_per_symbol=3),
            )
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix == ".gz":
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for secret in (FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET):
                    self.assertNotIn(secret, text, path.name)
                self.assertNotIn("api_key", text, path.name)

    def test_retained_objects_are_deterministic_gzip_and_append_only(self):
        contract = self._contract()
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            first = H.fetch(
                root, FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET,
                contract, "2026-05-04", "2026-05-06", feed="iex",
                symbols=["SPY"], getter=_history_getter(bars_per_symbol=3),
            )
            snapshot = {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.rglob("*")) if path.is_file()
            }
            # An identical re-run is a byte-identical no-op, not an overwrite.
            second = H.fetch(
                root, FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET,
                contract, "2026-05-04", "2026-05-06", feed="iex",
                symbols=["SPY"], getter=_history_getter(bars_per_symbol=3),
            )
            self.assertEqual(second["receipt_sha256"], first["receipt_sha256"])
            self.assertEqual(snapshot, {
                path.relative_to(root).as_posix(): path.read_bytes()
                for path in sorted(root.rglob("*")) if path.is_file()
            })
            gz = [name for name in snapshot if name.endswith(".json.gz")]
            self.assertTrue(gz)
            for name in gz:
                raw = gzip.decompress(snapshot[name])
                self.assertEqual(
                    snapshot[name], M.FRED_PROVENANCE.deterministic_gzip(raw), name
                )
            manifests = [
                json.loads(snapshot[name]) for name in snapshot
                if name.endswith("/manifest.json")
            ]
            self.assertTrue(manifests)
            for manifest in manifests:
                self.assertIn(
                    manifest.get("raw_retention"),
                    {H.HISTORY_RAW_RETENTION, None},
                )
            # Conflicting bytes at one immutable address fail closed.
            target = root / sorted(gz)[0]
            with self.assertRaises(H.FreeMarketDataError):
                H._write_once(target, b"different", "HISTORY_RAW_OBJECT_CONFLICT")

    def test_verify_replays_every_retained_object_and_fails_on_tampering(self):
        contract = self._contract()
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            H.fetch(
                root, FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET,
                contract, "2026-05-04", "2026-05-06", feed="iex",
                symbols=["SPY", "QQQ"], getter=_history_getter(bars_per_symbol=3),
            )
            report = H.verify(root)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["alpaca_series_replayed"], 2)
            self.assertEqual(report["alfred_availability_series_replayed"], 3)
            self.assertGreater(report["raw_objects_replayed"], 0)
            # A derived packet edited in place no longer replays.
            series = sorted(
                (root / H.HISTORY_STORE / "alpaca" / "series").rglob(
                    "alpaca_daily_bars_series.json"
                )
            )[0]
            body = json.loads(series.read_text(encoding="utf-8"))
            body["bars"][0]["close"] = "999"
            series.write_text(
                json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(H.FreeMarketDataError):
                H.verify(root)

    def test_verify_needs_no_credential_and_makes_no_request(self):
        source = (ROOT / "collectors" / "free_market_data_history.py").read_text(
            encoding="utf-8"
        )
        verify_source = inspect.getsource(H.verify)
        self.assertNotIn("_request(", verify_source)
        self.assertNotIn("getter", verify_source)
        self.assertIn("if args.mode == \"verify\":", source)

    def test_history_paths_outside_the_store_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for candidate in (
                "data/latest_free_market_data.json",
                "evidence/free_market_data/raw/alpaca/daily_bars/x/manifest.json",
                "../escape/manifest.json",
                f"{H.HISTORY_STORE}/../../manifest.json",
            ):
                with self.assertRaises(H.FreeMarketDataError):
                    H._safe_history_path(root, candidate, "/manifest.json")

    def test_measure_mode_stays_within_five_requests_and_writes_nothing(self):
        contract = self._contract()
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            before = sorted(path for path in root.rglob("*") if path.is_file())
            report = H.measure(
                FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET, contract,
                "2026-05-04", "2026-05-06",
                getter=_history_getter(bars_per_symbol=3, calls=calls),
            )
            self.assertEqual(
                sorted(path for path in root.rglob("*") if path.is_file()), before
            )
        self.assertTrue(report["writes_nothing"])
        self.assertLessEqual(report["requests_made"], H.MEASURE_REQUEST_BUDGET)
        self.assertEqual(report["requests_made"], 5)
        self.assertEqual(sorted(report["fred_vintages"]), ["TOTBKCR", "VIXCLS", "WRESBAL"])
        for series in report["fred_vintages"].values():
            self.assertEqual(series["earliest_vintage_date"], "2026-05-05")
        self.assertEqual(report["alpaca"]["iex"]["status"], "OBSERVED")
        # A denied SIP feed is reported, never raised, and never substituted.
        self.assertEqual(report["alpaca"]["sip"]["status"], "UNAVAILABLE")
        self.assertFalse(report["conclusion"]["sip_available"])
        self.assertFalse(report["conclusion"]["iex_meets_window_target"])
        self.assertEqual(report["conclusion"]["score_symbol_count"], 15)

    def test_measure_mode_refuses_to_exceed_its_request_budget(self):
        budget = H.RequestBudget(1)
        getter = _history_getter()
        H.fetch_fred_vintage_dates(FAKE_FRED_KEY, "VIXCLS", getter=getter, budget=budget)
        with self.assertRaises(H.FreeMarketDataError):
            H.fetch_fred_vintage_dates(
                FAKE_FRED_KEY, "WRESBAL", getter=getter, budget=budget
            )

    @staticmethod
    def _executable_source(path):
        """Source with comments and docstrings removed.

        The module's prose legitimately names the daily paths it must not write
        (that is the point of the docstring). Only executable code is checked,
        so documentation can stay explicit without weakening the assertion.
        """
        import ast
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
        return ast.unparse(ast.fix_missing_locations(tree))

    def test_history_module_names_no_daily_output_path(self):
        """The daily path's outputs are not writable from this module at all."""
        source = self._executable_source(
            ROOT / "collectors" / "free_market_data_history.py"
        )
        self.assertNotIn("latest_free_market_data", source)
        self.assertNotIn("_atomic_write", source)
        self.assertNotIn("publish_alpaca_raw_revision", source)
        self.assertNotIn("publish_evidence_bundle", source)
        self.assertNotIn("ALPACA_API_SECRET", source.replace(
            "ALPACA_MARKET_DATA_API_SECRET", ""
        ))
        self.assertNotIn("ALPACA_API_KEY", source.replace(
            "ALPACA_MARKET_DATA_API_KEY", ""
        ))
        self.assertNotIn("RISK_ON_MIN_SCORE", source)
        self.assertNotIn("RISK_OFF_MAX_SCORE", source)
        # The daily collector itself is imported as a library and its own
        # publish path is never called from here.
        self.assertNotIn("DAILY.publish", source)
        self.assertNotIn("DAILY.main", source)

    def test_fetch_refuses_a_feed_the_contract_does_not_pin(self):
        contract = self._contract()
        with tempfile.TemporaryDirectory() as tmp:
            root = _contract_root(tmp)
            with self.assertRaises(H.FreeMarketDataError):
                H.fetch(
                    root, FAKE_FRED_KEY, FAKE_ALPACA_KEY, FAKE_ALPACA_SECRET,
                    contract, "2026-05-04", "2026-05-06", feed="sip",
                    symbols=["SPY"], getter=_history_getter(),
                )
            self.assertFalse((root / H.HISTORY_STORE).exists())


class HistoricalRangeWorkflowTests(unittest.TestCase):
    """The new manual workflow, and the pinned files it must not disturb."""

    WORKFLOW = ROOT / ".github/workflows/us-regime-score-history-range.yml"
    # The thirteen files the Ubuntu schedule dispatcher pins by blob sha and
    # event-ref fingerprint (docs/do_not_touch_and_why.md). A byte change to
    # any of them can permanently and silently end a catch-up slot, so the new
    # capability is a NEW file and these stay untouched.
    PINNED = (
        "upbit-realtime-capture.yml", "crypto-breadth-capture.yml",
        "upbit-universe-capture.yml", "upbit-microstructure-capture.yml",
        "stablecoin-capture.yml", "crypto-paper-runtime.yml",
        "briefing-handoff-watchdog.yml", "daily-briefing.yml",
        "daily-briefing-recovery.yml", "spdr-sector-holdings.yml",
        "fred-dexkous-fx.yml", "kr-paper-runtime-daily-publish.yml",
        "us-paper-runtime.yml",
    )

    def test_new_workflow_is_manual_only_and_takes_an_explicit_window(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("cron", text)
        for required in ("start_date", "end_date", "mode"):
            self.assertIn(f"{required}:", text)
        self.assertIn("collectors/free_market_data_history.py", text)
        # Account/trading credential names must never appear in any workflow.
        self.assertNotIn("secrets.ALPACA_API_KEY", text)
        self.assertNotIn("secrets.ALPACA_API_SECRET", text)

    def test_new_workflow_never_dispatches_the_forbidden_korea_workflow(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("korea-market-signals", text)
        self.assertNotIn("workflow_run", text)

    def test_measure_mode_commits_nothing(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("git diff --exit-code", text)
        # Only the history store may ever be staged by this workflow.
        staged = [
            line.strip() for line in text.splitlines()
            if line.strip().startswith("git add ")
        ]
        self.assertTrue(staged)
        for line in staged:
            self.assertEqual(
                line, f"git add {H.HISTORY_STORE}", "only the history store may be staged"
            )

    def test_the_thirteen_dispatcher_pinned_workflows_are_not_this_file(self):
        self.assertNotIn(self.WORKFLOW.name, self.PINNED)
        for name in self.PINNED:
            self.assertTrue(
                (ROOT / ".github/workflows" / name).is_file(),
                f"{name} is pinned by the schedule dispatcher and must still exist",
            )


if __name__ == "__main__": unittest.main()
