"""P4-07 candle-finalization lookahead regression (capture v3 fetch times).

Defect (PR #729 review, Note A): population judged every candle's
FINALIZED/IN_PROGRESS state against ``downloaded_at_utc`` -- the capture's
COMPLETION instant, ~60 s after the 15m candles are fetched. Retained
2026-09-05 capture ran 01:29:48-01:30:50; the KRW-BTC 15m 01:15-01:30 candle
(last trade 01:29:45.585Z, no 01:30 candle in the response) was recorded
FINALIZED with ``latest_finalized_close_time`` 01:30:00Z although it had not
closed when fetched.

Fix: capture v3 records each candle fetch's own request/response instant in
the manifest; population judges finalization against the request instant.
v1/v2 packets are never rewritten and re-derive byte-identically.

Every test here uses real retained provider bytes or fixed instants; nothing
depends on the current date or wall clock.
"""
from __future__ import annotations

import base64
import hashlib
import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CAP = _load("upbit_microstructure_capture_fetch_time", ".github/scripts/upbit_microstructure_capture.py")
POP = _load("upbit_microstructure_populate_fetch_time", ".github/scripts/upbit_microstructure_populate.py")
EV = POP.EV

RAW_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "microstructure"
DATA_ROOT = ROOT / "data" / "observations" / "upbit_market_evidence"
RETAINED_KEY = "2026-09-05-p3-e705235488fda94f"
BTC_OPEN = "2026-09-05T01:15:00Z"
BTC_CLOSE = dt.datetime(2026, 9, 5, 1, 30, tzinfo=UTC)
BTC_LAST_TRADE_MS = 1788571785585  # 2026-09-05T01:29:45.585Z

# Every retained legacy (v1/v2) exact-P3 capture as of this fix, with the
# committed FINALIZED candles whose close time is later than the capture's
# own start -- i.e. not provably closed when fetched (no per-fetch time was
# recorded, so the capture start is the only honest lower bound).
LEGACY_EXPOSURE = {
    "2026-08-30-p3-a9be9c63f9a39d1a": set(),
    "2026-08-30-p3-ffb4e69d3f31c53b": set(),
    "2026-08-31-p3-d96e517d53969a79": set(),
    "2026-09-03-p3-7be807075981c0d2": set(),
    "2026-09-04-p3-a0b1acd3fc6949a6": set(),
    "2026-09-05-p3-e705235488fda94f": {
        (market, "15m", "2026-09-05T01:30:00Z")
        for market in ("KRW-BTC", "KRW-ETH", "KRW-LINK", "KRW-SHIB", "KRW-SOL", "KRW-SUI", "KRW-WLD", "KRW-XRP")
    },
    "2026-09-06-p3-07d8d7b7071c50da": set(),
    "2026-09-07-p3-c51ee08caaedbc57": set(),
    "2026-09-08-p3-cb4dcb9a78babd1b": set(),
    "2026-09-09-p3-5ee8ef32891b2dda": set(),
    "2026-09-10-p3-ce225c64b7f4b0a6": set(),
    "2026-09-11-p3-2d346dfcbde96a8e": set(),
    "2026-09-12-p3-5d4c04196dab426d": set(),
    "2026-09-13-p3-ec41eba3d21ba39d": set(),
    "2026-09-14-p3-c8e277226b41cfb2": {
        (market, "15m", "2026-09-14T05:45:00Z")
        for market in ("KRW-BTC", "KRW-ETH", "KRW-LINK", "KRW-SHIB", "KRW-SOL", "KRW-SUI", "KRW-WLD", "KRW-XRP")
    },
}


def _parse_second(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _ms(value: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(value / 1000, tz=UTC)


def _bundle(snapshot: Path, relative_gz: str) -> dict:
    out = {}
    for line in gzip.open(snapshot / relative_gz, "rb").read().splitlines():
        record = json.loads(line)
        out[record["market"]] = base64.b64decode(record["body_b64"])
    return out


def _newest_provider_ms(raw: bytes) -> int | None:
    rows = json.loads(raw)
    stamps = [row["timestamp"] for row in rows if isinstance(row, dict) and isinstance(row.get("timestamp"), int)]
    return max(stamps) if stamps else None


class _PhysicalReplay:
    """Serves retained provider bytes at the exact URLs capture requests, on
    a simulated clock that is physically consistent with those bytes: sleeps
    advance it, each request takes ``latency``, and a response is never in
    hand before the newest provider timestamp it contains."""

    def __init__(self, snapshot: Path, contract: dict, markets: list, start: dt.datetime, latency_ms: int = 250):
        self.now = start
        self.latency = dt.timedelta(milliseconds=latency_ms)
        self.responses = {}
        for timeframe in contract["timeframes"]:
            count = contract["candle_lookback_count_by_timeframe"][timeframe]
            unit = contract["candle_upbit_unit_by_timeframe"].get(timeframe)
            file_name = contract["candles_raw_file_template"].format(TIMEFRAME=timeframe)
            for market, body in _bundle(snapshot, file_name).items():
                if unit is not None:
                    url = contract["candles_minutes_endpoint_template"].format(UNIT=unit, MARKET=market, COUNT=count)
                else:
                    url = contract["candles_days_endpoint_template"].format(MARKET=market, COUNT=count)
                self.responses[url] = body
        for market, body in _bundle(snapshot, contract["trades_raw_file"]).items():
            url = contract["trades_endpoint_template"].format(MARKET=market, COUNT=contract["trades_lookback_count"])
            self.responses[url] = body
        encoded = urllib.parse.quote(",".join(markets), safe=",")
        self.responses[contract["orderbook_endpoint_template"].format(MARKETS=encoded)] = gzip.open(
            snapshot / contract["orderbook_raw_file"], "rb"
        ).read()

    def clock(self) -> dt.datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += dt.timedelta(seconds=seconds)

    def fetch(self, url: str, timeout_seconds: int) -> bytes:
        if url not in self.responses:
            raise AssertionError(f"unexpected provider URL in replay: {url}")
        raw = self.responses[url]
        self.now += self.latency
        newest = _newest_provider_ms(raw)
        if newest is not None:
            self.now = max(self.now, _ms(newest + 1))
        return raw


def _replay_capture(root: Path, key: str = RETAINED_KEY) -> tuple[Path, dict]:
    retained = RAW_ROOT / key
    manifest = CAP.validate_snapshot(retained)
    contract = CAP.load_contract()
    start = _parse_second(manifest["capture_started_at_utc"]) + dt.timedelta(milliseconds=200)
    replay = _PhysicalReplay(retained, contract, manifest["markets"], start)
    target = CAP.capture_snapshot(
        root, markets=manifest["markets"], snapshot_date=dt.date.fromisoformat(manifest["vintage_date"]),
        contract=contract, fetcher=replay.fetch, sleeper=replay.sleep, clock=replay.clock,
        snapshot_key=key, universe_lineage=manifest["universe_lineage"],
    )
    return target, manifest


def _rewrite_manifest(snapshot: Path, mutate) -> None:
    path = snapshot / "_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class FetchWindowClassificationTests(unittest.TestCase):
    """Builder-level fetch-window semantics on synthetic 15m rows (the
    SHA-pinned primitive ``upbit_candle_finalization.py`` is unchanged)."""

    def setUp(self):
        self.row = {
            "candle_date_time_utc": "2026-09-05T01:15:00",
            "opening_price": 1, "high_price": 1, "low_price": 1, "trade_price": 1,
            "candle_acc_trade_price": 1, "candle_acc_trade_volume": 1,
        }
        self.previous_row = dict(self.row, candle_date_time_utc="2026-09-05T01:00:00")
        self.next_row = dict(self.row, candle_date_time_utc="2026-09-05T01:30:00")
        self.captured_at = dt.datetime(2026, 9, 5, 1, 30, 51, tzinfo=UTC)

    def _block(self, rows, requested, received):
        return EV.build_candle_evidence(
            "KRW-BTC", "15m", rows, as_of=self.captured_at, captured_at=self.captured_at,
            max_staleness_seconds=1800,
            fetch_window={"request_started_at": requested, "response_received_at": received},
        )

    def test_primitive_is_unchanged_and_pinned(self):
        contract = json.loads((ROOT / "config" / "intraday_risk_observation_preparation_contract.json").read_text(encoding="utf-8"))
        pins = [
            source for source in contract["source_profiles"].values()
            if source.get("provider_contract_ref") == "microstructure/upbit_candle_finalization.py"
        ]
        self.assertTrue(pins)
        actual = hashlib.sha256((ROOT / "microstructure" / "upbit_candle_finalization.py").read_bytes()).hexdigest()
        for pin in pins:
            self.assertEqual(pin["provider_contract_sha256"], actual)

    def test_candle_opened_while_request_in_flight_is_in_progress_not_malformed(self):
        requested = BTC_CLOSE - dt.timedelta(milliseconds=50)
        received = BTC_CLOSE + dt.timedelta(milliseconds=200)
        block = self._block([self.next_row, self.row, self.previous_row], requested, received)
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:15:00Z")
        self.assertEqual(block["in_progress_candle_count"], 2)
        self.assertEqual(block["finalized_candle_count"], 1)

    def test_candle_opened_after_response_is_future_dated(self):
        requested = BTC_CLOSE - dt.timedelta(milliseconds=50)
        with self.assertRaisesRegex(EV.MarketEvidenceError, "CANDLE_MALFORMED:KRW-BTC:15m:FUTURE_DATED_CANDLE"):
            self._block([self.next_row, self.row], requested, requested)

    def test_response_before_request_rejected(self):
        with self.assertRaisesRegex(EV.MarketEvidenceError, "CANDLE_FETCH_WINDOW_INVALID"):
            self._block([self.row], BTC_CLOSE, BTC_CLOSE - dt.timedelta(milliseconds=1))

    def test_naive_fetch_time_rejected(self):
        with self.assertRaisesRegex(EV.MarketEvidenceError, "FETCH_REQUEST_TIME_NAIVE"):
            self._block([self.row], dt.datetime(2026, 9, 5, 1, 30), BTC_CLOSE)

    def test_duplicate_rows_still_counted_under_fetch_window(self):
        block = self._block([self.row, dict(self.row), self.previous_row], BTC_CLOSE, BTC_CLOSE)
        self.assertEqual(block["duplicate_row_count"], 1)
        self.assertIn("DUPLICATE_CANDLE", block["fail_closed_reasons"])


class RetainedLegacyExposureTests(unittest.TestCase):
    """Issued v1/v2 evidence stays as issued; its exposure is pinned."""

    def test_retained_btc_case_is_the_defect(self):
        snapshot = RAW_ROOT / RETAINED_KEY
        manifest = CAP.validate_snapshot(snapshot)
        self.assertIn(manifest["capture_version"], CAP.LEGACY_CAPTURE_VERSIONS)
        self.assertNotIn(CAP.CANDLE_FETCH_TIMES_FIELD, manifest)
        self.assertEqual(manifest["capture_started_at_utc"], "2026-09-05T01:29:48Z")
        self.assertEqual(manifest["downloaded_at_utc"], "2026-09-05T01:30:50Z")
        rows = json.loads(_bundle(snapshot, "upbit_candles_15m.ndjson.gz")["KRW-BTC"])
        newest = max(rows, key=lambda row: row["candle_date_time_utc"])
        self.assertEqual(newest["candle_date_time_utc"] + "Z", BTC_OPEN)
        self.assertEqual(newest["timestamp"], BTC_LAST_TRADE_MS)
        committed = json.loads((DATA_ROOT / RETAINED_KEY / "packet.json").read_text(encoding="utf-8"))
        block = committed["packets"]["KRW-BTC"]["candles"]["15m"]
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:30:00Z")
        self.assertEqual(block["in_progress_candle_count"], 0)
        self.assertNotIn("finalization_as_of", block)

    def test_legacy_packets_rebuild_byte_identically(self):
        for key in LEGACY_EXPOSURE:
            with self.subTest(key=key):
                committed = json.loads((DATA_ROOT / key / "packet.json").read_text(encoding="utf-8"))
                self.assertEqual(POP.rebuild(key), committed)

    def test_legacy_exposure_is_exactly_pinned(self):
        for key, expected in LEGACY_EXPOSURE.items():
            with self.subTest(key=key):
                snapshot = RAW_ROOT / key
                manifest = CAP.validate_snapshot(snapshot)
                self.assertIn(manifest["capture_version"], CAP.LEGACY_CAPTURE_VERSIONS)
                started = _parse_second(manifest["capture_started_at_utc"])
                committed = json.loads((DATA_ROOT / key / "packet.json").read_text(encoding="utf-8"))
                exposed = set()
                for market, packet in committed["packets"].items():
                    for timeframe, block in packet["candles"].items():
                        for row in block["finalized_candles"]:
                            if _parse_second(row["close_time"]) > started:
                                exposed.add((market, timeframe, row["close_time"]))
                self.assertEqual(exposed, expected)
                for market, timeframe, close_time in exposed:
                    # No response holds the following candle, so nothing in
                    # the retained bytes shows the fetch happened after close.
                    file_name = f"upbit_candles_{timeframe}.ndjson.gz"
                    opens = {row["candle_date_time_utc"] + "Z" for row in json.loads(_bundle(snapshot, file_name)[market])}
                    self.assertNotIn(close_time, opens, (key, market))


class CaptureV3FetchTimeReplayTests(unittest.TestCase):
    """Real retained 2026-09-05 provider bytes through capture v3 + populate."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.snapshot, cls.retained_manifest = _replay_capture(cls.root)
        cls.manifest = CAP.validate_snapshot(cls.snapshot)
        cls.built = POP.build_packets(RETAINED_KEY, raw_root=cls.root)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_manifest_records_every_candle_fetch_inside_capture_window(self):
        self.assertEqual(self.manifest["capture_version"], "upbit-microstructure-capture/v3")
        self.assertEqual(self.manifest["checksums"], self.retained_manifest["checksums"])
        fetch_times = self.manifest[CAP.CANDLE_FETCH_TIMES_FIELD]
        self.assertEqual(sorted(fetch_times), sorted(self.manifest["timeframes"]))
        for timeframe, by_market in fetch_times.items():
            self.assertEqual(sorted(by_market), self.manifest["markets"])
        btc = fetch_times["15m"]["KRW-BTC"]
        self.assertLess(CAP.parse_fetch_time(btc["request_started_at_utc"]), BTC_CLOSE)
        # The response is not in hand before the newest trade it contains.
        self.assertGreaterEqual(CAP.parse_fetch_time(btc["response_received_at_utc"]), _ms(BTC_LAST_TRADE_MS))

    def test_btc_0115_candle_is_not_finalized_under_fetch_time(self):
        self.assertEqual(self.built["errors"], {})
        block = self.built["packets"]["KRW-BTC"]["candles"]["15m"]
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:15:00Z")
        self.assertNotIn("2026-09-05T01:30:00Z", [row["close_time"] for row in block["finalized_candles"]])
        self.assertEqual(block["in_progress_candle_count"], 1)
        self.assertEqual(
            block["finalization_as_of"],
            self.manifest[CAP.CANDLE_FETCH_TIMES_FIELD]["15m"]["KRW-BTC"]["request_started_at_utc"],
        )
        # Freshness semantics unchanged: still aged against captured_at.
        self.assertEqual(block["freshness"]["status"], "FRESH")
        self.assertEqual(
            block["freshness"]["age_seconds"],
            int((_parse_second(self.manifest["downloaded_at_utc"]) - dt.datetime(2026, 9, 5, 1, 15, tzinfo=UTC)).total_seconds()),
        )

    def test_every_finalized_candle_closed_by_its_own_fetch_request(self):
        fetch_times = self.manifest[CAP.CANDLE_FETCH_TIMES_FIELD]
        for market, packet in self.built["packets"].items():
            for timeframe, block in packet["candles"].items():
                requested = CAP.parse_fetch_time(fetch_times[timeframe][market]["request_started_at_utc"])
                for row in block["finalized_candles"]:
                    self.assertLessEqual(_parse_second(row["close_time"]), requested, (market, timeframe))
                # Output schema and packet-level instants are unchanged.
                self.assertEqual(packet["schema_version"], EV.OUTPUT_SCHEMA_VERSION)
                self.assertEqual(packet["as_of"], self.manifest["downloaded_at_utc"])

    def test_populate_is_idempotent_for_v3(self):
        data_root = self.root / "data"
        first = POP.populate(RETAINED_KEY, raw_root=self.root, data_root=data_root)
        second = POP.populate(RETAINED_KEY, raw_root=self.root, data_root=data_root)
        self.assertEqual((first["outcome"], second["outcome"]), ("populated", "verified_existing"))

    def test_mutation_revert_to_packet_as_of_reintroduces_lookahead(self):
        """Mutation proof 1: ignoring the recorded fetch time (judging against
        the packet ``as_of``) makes the test above fail."""
        with mock.patch.object(POP, "candle_fetch_windows", return_value=None):
            built = POP.build_packets(RETAINED_KEY, raw_root=self.root)
        block = built["packets"]["KRW-BTC"]["candles"]["15m"]
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:30:00Z")
        self.assertEqual(block["in_progress_candle_count"], 0)

    def test_mutation_completion_time_as_fetch_time_reintroduces_lookahead(self):
        """Mutation proof 2: a (structurally valid) manifest whose candle
        fetch instants are the capture completion time reproduces the defect
        -- the fix depends on the per-fetch instant, nothing else."""
        with tempfile.TemporaryDirectory() as tmp:
            mutated_root = Path(tmp)
            shutil.copytree(self.snapshot, mutated_root / RETAINED_KEY)
            completion = self.manifest["downloaded_at_utc"].replace("Z", ".000Z")

            def to_completion(manifest):
                for by_market in manifest[CAP.CANDLE_FETCH_TIMES_FIELD].values():
                    for window in by_market.values():
                        window["request_started_at_utc"] = completion
                        window["response_received_at_utc"] = completion

            _rewrite_manifest(mutated_root / RETAINED_KEY, to_completion)
            built = POP.build_packets(RETAINED_KEY, raw_root=mutated_root)
        block = built["packets"]["KRW-BTC"]["candles"]["15m"]
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:30:00Z")


class BuilderFetchWindowBoundaryTests(unittest.TestCase):
    """Retained KRW-BTC 15m rows at the exact 01:30:00 close boundary."""

    def setUp(self):
        self.rows = json.loads(_bundle(RAW_ROOT / RETAINED_KEY, "upbit_candles_15m.ndjson.gz")["KRW-BTC"])
        self.captured_at = dt.datetime(2026, 9, 5, 1, 30, 51, tzinfo=UTC)

    def _block(self, requested: dt.datetime, received: dt.datetime | None = None, **kwargs) -> dict:
        window = None if requested is None else {
            "request_started_at": requested,
            "response_received_at": received or requested + dt.timedelta(milliseconds=300),
        }
        return EV.build_candle_evidence(
            "KRW-BTC", "15m", self.rows, as_of=self.captured_at, captured_at=self.captured_at,
            max_staleness_seconds=1800, fetch_window=window, **kwargs,
        )

    def test_request_at_exact_close_is_finalized(self):
        block = self._block(BTC_CLOSE)
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:30:00Z")
        self.assertEqual(block["finalization_as_of"], "2026-09-05T01:30:00.000Z")

    def test_request_one_ms_before_close_is_not_finalized(self):
        block = self._block(BTC_CLOSE - dt.timedelta(milliseconds=1))
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:15:00Z")
        self.assertEqual(block["in_progress_candle_count"], 1)
        self.assertEqual(block["finalization_as_of"], "2026-09-05T01:29:59.999Z")

    def test_legacy_call_without_window_is_unchanged(self):
        block = self._block(None)
        self.assertEqual(block["latest_finalized_close_time"], "2026-09-05T01:30:00Z")
        self.assertNotIn("finalization_as_of", block)

    def test_fetch_window_after_captured_at_fails_closed(self):
        with self.assertRaisesRegex(EV.MarketEvidenceError, "CANDLE_FETCH_WINDOW_INVALID"):
            self._block(self.captured_at, self.captured_at + dt.timedelta(milliseconds=1))

    def test_fetch_windows_must_cover_every_timeframe(self):
        policy = EV.load_ratified_policy()
        with self.assertRaisesRegex(EV.MarketEvidenceError, "CANDLE_FETCH_WINDOWS_TIMEFRAMES_MISMATCH"):
            EV.build_market_evidence_packet(
                "KRW-BTC", candles_by_timeframe={}, trades=[], orderbook_row={},
                as_of=self.captured_at, captured_at=self.captured_at, policy=policy,
                candle_fetch_windows={"15m": {"request_started_at": BTC_CLOSE, "response_received_at": BTC_CLOSE}},
            )


class ManifestFetchTimeValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.snapshot, _ = _replay_capture(Path(cls._tmp.name) / "raw")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _assert_rejected(self, mutate, code: str):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / RETAINED_KEY
            shutil.copytree(self.snapshot, copy)
            _rewrite_manifest(copy, mutate)
            with self.assertRaisesRegex(CAP.CaptureError, code):
                CAP.validate_snapshot(copy)

    def test_v3_without_fetch_times_rejected(self):
        self._assert_rejected(lambda m: m.pop(CAP.CANDLE_FETCH_TIMES_FIELD), "MANIFEST_CANDLE_FETCH_TIMES_MISSING")

    def test_legacy_version_with_fetch_times_rejected(self):
        self._assert_rejected(
            lambda m: m.update(capture_version="upbit-microstructure-capture/v2"),
            "MANIFEST_CANDLE_FETCH_TIMES_UNEXPECTED",
        )

    def test_missing_market_rejected(self):
        self._assert_rejected(
            lambda m: m[CAP.CANDLE_FETCH_TIMES_FIELD]["1h"].pop("KRW-BTC"),
            "MANIFEST_CANDLE_FETCH_TIMES_MARKETS_MISMATCH",
        )

    def test_missing_timeframe_rejected(self):
        self._assert_rejected(
            lambda m: m[CAP.CANDLE_FETCH_TIMES_FIELD].pop("1d"),
            "MANIFEST_CANDLE_FETCH_TIMES_TIMEFRAMES_MISMATCH",
        )

    def test_fetch_time_after_completion_rejected(self):
        def mutate(manifest):
            manifest[CAP.CANDLE_FETCH_TIMES_FIELD]["15m"]["KRW-BTC"]["response_received_at_utc"] = (
                manifest["downloaded_at_utc"].replace("Z", ".001Z")
            )
        self._assert_rejected(mutate, "MANIFEST_CANDLE_FETCH_TIME_OUTSIDE_CAPTURE")

    def test_request_after_response_rejected(self):
        def mutate(manifest):
            window = manifest[CAP.CANDLE_FETCH_TIMES_FIELD]["15m"]["KRW-BTC"]
            window["request_started_at_utc"], window["response_received_at_utc"] = (
                window["response_received_at_utc"], window["request_started_at_utc"],
            )
        self._assert_rejected(mutate, "MANIFEST_CANDLE_FETCH_TIME_OUTSIDE_CAPTURE")

    def test_second_precision_fetch_time_rejected(self):
        def mutate(manifest):
            manifest[CAP.CANDLE_FETCH_TIMES_FIELD]["15m"]["KRW-BTC"]["request_started_at_utc"] = (
                manifest["capture_started_at_utc"]
            )
        self._assert_rejected(mutate, "MANIFEST_CANDLE_FETCH_TIME_INVALID")


class FetchTimeRoundingTests(unittest.TestCase):
    def test_request_floors_and_response_ceils_to_millisecond(self):
        value = dt.datetime(2026, 9, 5, 1, 29, 59, 999500, tzinfo=UTC)
        self.assertEqual(CAP.iso_utc_ms_floor(value), "2026-09-05T01:29:59.999Z")
        self.assertEqual(CAP.iso_utc_ms_ceil(value), "2026-09-05T01:30:00.000Z")
        exact = dt.datetime(2026, 9, 5, 1, 30, tzinfo=UTC)
        self.assertEqual(CAP.iso_utc_ms_floor(exact), CAP.iso_utc_ms_ceil(exact))

    def test_retry_records_the_successful_attempt_request(self):
        state = {"now": dt.datetime(2026, 9, 5, 1, 29, 48, tzinfo=UTC), "calls": 0}
        contract = CAP.load_contract()

        def clock():
            return state["now"]

        def sleeper(seconds):
            state["now"] += dt.timedelta(seconds=seconds)

        def fetcher(url, timeout_seconds):
            state["now"] += dt.timedelta(milliseconds=100)
            if "minutes/15" in url and state["calls"] == 0:
                state["calls"] += 1
                raise TimeoutError("transient")
            if "orderbook" in url:
                return json.dumps([{"market": "KRW-BTC", "timestamp": 0, "orderbook_units": []}]).encode()
            return b"[]"

        with tempfile.TemporaryDirectory() as tmp:
            target = CAP.capture_snapshot(
                Path(tmp), markets=["KRW-BTC"], snapshot_date=dt.date(2026, 9, 5), contract=contract,
                fetcher=fetcher, sleeper=sleeper, clock=clock,
            )
            manifest = CAP.validate_snapshot(target)
        window = manifest[CAP.CANDLE_FETCH_TIMES_FIELD]["15m"]["KRW-BTC"]
        # attempt 1 at 01:29:48.000 fails (+0.1 s), backoff 2 s, attempt 2 at 01:29:50.100.
        self.assertEqual(window["request_started_at_utc"], "2026-09-05T01:29:50.100Z")
        self.assertEqual(window["response_received_at_utc"], "2026-09-05T01:29:50.200Z")


if __name__ == "__main__":
    unittest.main(verbosity=2)
