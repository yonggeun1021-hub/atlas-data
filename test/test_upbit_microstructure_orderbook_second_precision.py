"""P4-07 orderbook ``ORDERBOOK_UNKNOWN`` second-precision regression.

Root cause pinned here, against real retained captures only (no wall clock):
``upbit_microstructure_capture.py`` v1 serialized the capture completion
instant as ``downloaded_at_utc`` truncated to the whole second, while Upbit
orderbook rows carry millisecond ``timestamp`` values taken moments before
completion. Any market whose book updated inside the final wall-clock second
of the capture (in practice the liquid ones: KRW-BTC/ETH/XRP) therefore had
``reference_time > captured_at`` by < 1 s, which
``upbit_market_evidence.freshness_status`` correctly treats as an impossible
ordering -> ``ORDERBOOK_UNKNOWN`` -> decision-level
``UPBIT_MARKET_EVIDENCE_COMPONENT_UNKNOWN`` for every market.

The fix rounds the completion instant UP (capture v2). The builder, the
ratified P4-07 policy numbers, and every issued v1 packet stay untouched.
"""
from __future__ import annotations

import base64
import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CAP = _load("upbit_microstructure_capture_second_precision", ".github/scripts/upbit_microstructure_capture.py")
POP = _load("upbit_microstructure_populate_second_precision", ".github/scripts/upbit_microstructure_populate.py")
EV = POP.EV

RAW_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "microstructure"
DATA_ROOT = ROOT / "data" / "observations" / "upbit_market_evidence"
# Real retained capture whose committed packet shows the defect for exactly
# the three markets named in the ticket.
RETAINED_KEY = "2026-09-13-p3-ec41eba3d21ba39d"
AFFECTED = ("KRW-BTC", "KRW-ETH", "KRW-XRP")


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _orderbook_rows(snapshot: Path, contract: dict) -> list:
    return json.loads(gzip.open(snapshot / contract["orderbook_raw_file"], "rb").read())


def _replay_fetcher(snapshot: Path, contract: dict, markets: list):
    """Serve the retained provider bytes at the exact URLs capture requests."""
    responses = {}
    for timeframe in contract["timeframes"]:
        count = contract["candle_lookback_count_by_timeframe"][timeframe]
        unit = contract["candle_upbit_unit_by_timeframe"].get(timeframe)
        bundle = gzip.open(snapshot / contract["candles_raw_file_template"].format(TIMEFRAME=timeframe), "rb").read()
        for line in bundle.splitlines():
            record = json.loads(line)
            market = record["market"]
            if unit is not None:
                url = contract["candles_minutes_endpoint_template"].format(UNIT=unit, MARKET=market, COUNT=count)
            else:
                url = contract["candles_days_endpoint_template"].format(MARKET=market, COUNT=count)
            responses[url] = base64.b64decode(record["body_b64"])
    for line in gzip.open(snapshot / contract["trades_raw_file"], "rb").read().splitlines():
        record = json.loads(line)
        url = contract["trades_endpoint_template"].format(
            MARKET=record["market"], COUNT=contract["trades_lookback_count"],
        )
        responses[url] = base64.b64decode(record["body_b64"])
    encoded = urllib.parse.quote(",".join(markets), safe=",")
    responses[contract["orderbook_endpoint_template"].format(MARKETS=encoded)] = gzip.open(
        snapshot / contract["orderbook_raw_file"], "rb"
    ).read()

    def fetch(url: str, timeout_seconds: int) -> bytes:
        if url not in responses:
            raise AssertionError(f"unexpected provider URL in replay: {url}")
        return responses[url]

    return fetch


class CeilToUtcSecondTest(unittest.TestCase):
    def test_whole_second_is_unchanged(self):
        value = dt.datetime(2026, 9, 13, 1, 33, 18, tzinfo=UTC)
        self.assertEqual(CAP.ceil_to_utc_second(value), value)

    def test_any_fraction_rounds_up(self):
        for micro in (1, 126000, 999999):
            value = dt.datetime(2026, 9, 13, 1, 33, 18, micro, tzinfo=UTC)
            self.assertEqual(
                CAP.ceil_to_utc_second(value), dt.datetime(2026, 9, 13, 1, 33, 19, tzinfo=UTC),
            )

    def test_non_utc_input_is_normalized(self):
        kst = dt.timezone(dt.timedelta(hours=9))
        value = dt.datetime(2026, 9, 13, 10, 33, 18, 500000, tzinfo=kst)
        self.assertEqual(
            CAP.ceil_to_utc_second(value), dt.datetime(2026, 9, 13, 1, 33, 19, tzinfo=UTC),
        )


class RetainedCaptureRootCauseTest(unittest.TestCase):
    """Pins the defect in already-issued v1 evidence, which stays as issued."""

    def setUp(self):
        self.contract = CAP.load_contract()

    def test_committed_unknowns_are_exactly_the_sub_second_truncation(self):
        """Across every retained v1 exact-P3 capture, a market is
        ORDERBOOK_UNKNOWN iff its orderbook timestamp falls inside the
        truncated final second -- no residual clock skew, no threshold."""
        checked = 0
        unknown_seen = 0
        for snapshot in sorted(RAW_ROOT.iterdir()):
            if not CAP.SNAPSHOT_KEY_RE.fullmatch(snapshot.name):
                continue
            manifest = CAP.validate_snapshot(snapshot)
            if manifest.get("capture_version") != "upbit-microstructure-capture/v1":
                continue
            packet_path = DATA_ROOT / snapshot.name / "packet.json"
            if not packet_path.is_file():
                continue
            results = json.loads(packet_path.read_text(encoding="utf-8"))["market_results"]
            downloaded_ms = int(_parse_utc(manifest["downloaded_at_utc"]).timestamp() * 1000)
            for row in _orderbook_rows(snapshot, self.contract):
                delta_ms = row["timestamp"] - downloaded_ms
                inside_truncated_second = 0 < delta_ms < 1000
                is_unknown = "ORDERBOOK_UNKNOWN" in results[row["market"]]["reasons"]
                self.assertEqual(
                    is_unknown, inside_truncated_second,
                    f"{snapshot.name}:{row['market']}:delta_ms={delta_ms}",
                )
                self.assertLess(delta_ms, 1000, f"{snapshot.name}:{row['market']}")
                checked += 1
                unknown_seen += is_unknown
        self.assertGreater(checked, 0)
        self.assertGreater(unknown_seen, 0)

    def test_retained_2026_09_13_btc_eth_xrp_unknown_from_truncation(self):
        snapshot = RAW_ROOT / RETAINED_KEY
        manifest = CAP.validate_snapshot(snapshot)
        self.assertEqual(manifest["capture_version"], "upbit-microstructure-capture/v1")
        committed = json.loads((DATA_ROOT / RETAINED_KEY / "packet.json").read_text(encoding="utf-8"))
        truncated = _parse_utc(manifest["downloaded_at_utc"])
        rows = {row["market"]: row for row in _orderbook_rows(snapshot, self.contract)}
        policy = EV.load_ratified_policy()
        for market in AFFECTED:
            self.assertEqual(committed["market_results"][market]["reasons"], ["ORDERBOOK_UNKNOWN"])
            evidence = committed["packets"][market]["orderbook"]
            self.assertEqual(evidence["freshness"]["status"], "UNKNOWN")
            # Every other orderbook quality check already passed.
            self.assertEqual(evidence["spread_status"], "NORMAL")
            self.assertEqual(evidence["slippage_status"], "NORMAL")
            self.assertEqual(evidence["depth"]["levels_available"], policy["orderbook_depth_levels"])
            self.assertEqual(evidence["fail_closed_reasons"], ["ORDERBOOK_UNKNOWN"])
            reference = dt.datetime.fromtimestamp(rows[market]["timestamp"] / 1000, tz=UTC)
            self.assertTrue(truncated < reference < truncated + dt.timedelta(seconds=1))

    def test_issued_v1_packet_still_rebuilds_byte_identically(self):
        committed = json.loads((DATA_ROOT / RETAINED_KEY / "packet.json").read_text(encoding="utf-8"))
        self.assertEqual(POP.rebuild(RETAINED_KEY), committed)


class CaptureV2ReplayTest(unittest.TestCase):
    """Replays the real retained provider bytes through the fixed capture."""

    def setUp(self):
        self.contract = CAP.load_contract()
        self.retained = RAW_ROOT / RETAINED_KEY
        self.manifest = CAP.validate_snapshot(self.retained)
        self.rows = _orderbook_rows(self.retained, self.contract)

    def _capture(self, root: Path, completion: dt.datetime) -> Path:
        started = _parse_utc(self.manifest["capture_started_at_utc"]) + dt.timedelta(milliseconds=500)
        # Capture v3 also reads the clock around every candle fetch: every
        # instant up to the orderbook response is ``started``; the completion
        # read after it is ``completion``.
        state = {"now": started}
        markets = self.manifest["markets"]
        replay = _replay_fetcher(self.retained, self.contract, markets)
        orderbook_prefix = self.contract["orderbook_endpoint_template"].split("{", 1)[0]

        def fetcher(url: str, timeout_seconds: int) -> bytes:
            raw = replay(url, timeout_seconds)
            if url.startswith(orderbook_prefix):
                state["now"] = completion
            return raw

        return CAP.capture_snapshot(
            root, markets=markets, snapshot_date=dt.date.fromisoformat(self.manifest["vintage_date"]),
            contract=self.contract, fetcher=fetcher,
            sleeper=lambda seconds: None, clock=lambda: state["now"],
            snapshot_key=RETAINED_KEY, universe_lineage=self.manifest["universe_lineage"],
        )

    def test_earliest_honest_completion_yields_fresh_orderbook_and_pass(self):
        # The earliest instant the capture can possibly have completed is the
        # latest orderbook row it holds (here KRW-ETH at 01:33:18.134Z).
        latest_ms = max(row["timestamp"] for row in self.rows)
        completion = dt.datetime.fromtimestamp(latest_ms / 1000, tz=UTC)
        self.assertNotEqual(completion.microsecond, 0)
        with tempfile.TemporaryDirectory() as tmp:
            target = self._capture(Path(tmp), completion)
            manifest = CAP.validate_snapshot(target)
            # v3 (candle fetch times) keeps the v2 whole-second ceil.
            self.assertEqual(manifest["capture_version"], CAP.CAPTURE_VERSION)
            # Provider bytes are byte-identical to the retained capture.
            self.assertEqual(manifest["checksums"], self.manifest["checksums"])
            self.assertEqual(manifest["capture_started_at_utc"], self.manifest["capture_started_at_utc"])
            self.assertEqual(manifest["downloaded_at_utc"], "2026-09-13T01:33:19Z")
            self.assertEqual(
                (target / "_downloaded_at.txt").read_text(encoding="utf-8"), "2026-09-13T01:33:19Z\n",
            )
            built = POP.build_packets(RETAINED_KEY, raw_root=Path(tmp))
        self.assertEqual(built["errors"], {})
        for market in AFFECTED:
            orderbook = built["packets"][market]["orderbook"]
            self.assertEqual(orderbook["freshness"]["status"], "FRESH", market)
            self.assertIn(orderbook["freshness"]["age_seconds"], (0, 1), market)
            self.assertEqual(orderbook["fail_closed_reasons"], [], market)
            self.assertEqual(built["market_results"][market]["status"], "PASS", market)
        for market, result in built["market_results"].items():
            self.assertNotIn("ORDERBOOK_UNKNOWN", result["reasons"], market)
        # Every orderbook freshness in the record is FRESH, so the decision
        # snapshot's all-market P4-07 aggregation is no longer forced UNKNOWN
        # by this defect (candle-gap reasons are not freshness statuses).
        self.assertEqual(
            {packet["orderbook"]["freshness"]["status"] for packet in built["packets"].values()},
            {"FRESH"},
        )

    def test_whole_second_completion_is_not_shifted(self):
        completion = _parse_utc(self.manifest["downloaded_at_utc"]) + dt.timedelta(seconds=1)
        with tempfile.TemporaryDirectory() as tmp:
            manifest = CAP.validate_snapshot(self._capture(Path(tmp), completion))
        self.assertEqual(manifest["downloaded_at_utc"], "2026-09-13T01:33:19Z")

    def test_reversed_clock_still_fails_closed(self):
        completion = _parse_utc(self.manifest["capture_started_at_utc"]) - dt.timedelta(seconds=5)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CAP.CaptureError, "CAPTURE_CLOCK_REVERSED"):
                self._capture(Path(tmp), completion)


class BuilderFailClosedUnchangedTest(unittest.TestCase):
    """The fix lives in capture; the builder's impossible-ordering guard is
    untouched and still rejects an orderbook stamped after capture."""

    def test_orderbook_after_captured_at_is_still_unknown(self):
        contract = CAP.load_contract()
        policy = EV.load_ratified_policy()
        rows = {row["market"]: row for row in _orderbook_rows(RAW_ROOT / RETAINED_KEY, contract)}
        row = rows["KRW-BTC"]
        reference = dt.datetime.fromtimestamp(row["timestamp"] / 1000, tz=UTC)
        kwargs = dict(
            max_staleness_seconds=policy["max_orderbook_staleness_seconds"],
            depth_levels=policy["orderbook_depth_levels"],
            slippage_notional_krw=policy["paper_slippage_estimate_notional_krw"],
            max_spread_bps_normal=policy["max_spread_bps_normal"],
            max_slippage_bps_normal=policy["max_slippage_bps_normal"],
        )
        ceiled = CAP.ceil_to_utc_second(reference)
        fresh = EV.build_orderbook_evidence("KRW-BTC", row, captured_at=ceiled, **kwargs)
        self.assertEqual(fresh["freshness"]["status"], "FRESH")
        # An orderbook stamped after the recorded upper bound (e.g. clock skew)
        # is not absorbed.
        skewed = dict(row, timestamp=int(ceiled.timestamp() * 1000) + 1)
        unknown = EV.build_orderbook_evidence("KRW-BTC", skewed, captured_at=ceiled, **kwargs)
        self.assertEqual(unknown["fail_closed_reasons"], ["ORDERBOOK_UNKNOWN"])
        # Truncated captured_at (v1 behaviour) reproduces the defect.
        truncated = reference.replace(microsecond=0)
        legacy = EV.build_orderbook_evidence("KRW-BTC", row, captured_at=truncated, **kwargs)
        self.assertEqual(legacy["fail_closed_reasons"], ["ORDERBOOK_UNKNOWN"])


if __name__ == "__main__":
    unittest.main()
