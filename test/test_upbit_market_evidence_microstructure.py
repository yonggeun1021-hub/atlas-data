"""P4-07 Upbit market evidence & microstructure derivation regression."""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


EV = _load("upbit_market_evidence", "microstructure/upbit_market_evidence.py")
FIN = _load("upbit_candle_finalization_for_evidence_test", "microstructure/upbit_candle_finalization.py")
POP = _load("upbit_microstructure_populate_for_evidence_test", ".github/scripts/upbit_microstructure_populate.py")

UTC = dt.timezone.utc


def candle(open_time: str, **overrides) -> dict:
    row = {
        "candle_date_time_utc": open_time,
        "opening_price": 1000, "high_price": 1010, "low_price": 990,
        "trade_price": 1005, "candle_acc_trade_price": 123456, "candle_acc_trade_volume": 12.3,
    }
    row.update(overrides)
    return row


def default_policy(**overrides) -> dict:
    policy = {
        "approval_status": "PROPOSED_UNRATIFIED",
        "policy_version": "test/v1",
        "orderbook_depth_levels": 3,
        "paper_slippage_estimate_notional_krw": "1000000",
        "max_spread_bps_normal": "100",
        "max_slippage_bps_normal": "150",
        "max_staleness_seconds_by_timeframe": {"15m": 1800, "1h": 7200, "4h": 28800, "1d": 172800},
        "max_trades_staleness_seconds": 600,
        "max_orderbook_staleness_seconds": 300,
    }
    policy.update(overrides)
    return policy


def orderbook_row(market="KRW-BTC", *, best_bid=999, best_ask=1001, timestamp_ms=1756339200000, levels=None):
    levels = levels or [{"bid_price": best_bid, "bid_size": 10, "ask_price": best_ask, "ask_size": 10}]
    return {"market": market, "timestamp": timestamp_ms, "orderbook_units": levels}


def trade_rows(market="KRW-BTC", *, timestamp_ms=1756339200000, price=1000):
    return [{"market": market, "trade_price": price, "trade_volume": 1.0, "timestamp": timestamp_ms, "ask_bid": "BID"}]


def full_candles_by_timeframe(open_time="2026-08-28T00:00:00"):
    return {tf: [candle(open_time)] for tf in FIN.TIMEFRAMES}


class FreshnessTests(unittest.TestCase):
    def test_subsecond_provider_timestamp_before_capture_is_fresh(self):
        ref = dt.datetime(2026, 8, 30, 4, 52, 13, 49000, tzinfo=UTC)
        captured = dt.datetime(2026, 8, 30, 4, 52, 13, 100000, tzinfo=UTC)
        result = EV.freshness_status(ref, captured, 300)
        self.assertEqual(result["status"], EV.FRESH)
        self.assertEqual(result["age_seconds"], 0)

    def test_fresh_within_threshold(self):
        ref = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        captured = dt.datetime(2026, 8, 28, 0, 10, 0, tzinfo=UTC)
        result = EV.freshness_status(ref, captured, 1800)
        self.assertEqual(result["status"], EV.FRESH)
        self.assertEqual(result["age_seconds"], 600)

    def test_stale_past_threshold(self):
        ref = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        captured = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        result = EV.freshness_status(ref, captured, 1800)
        self.assertEqual(result["status"], EV.STALE)

    def test_unknown_when_reference_missing(self):
        self.assertEqual(EV.freshness_status(None, dt.datetime.now(tz=UTC), 100)["status"], EV.UNKNOWN)

    def test_unknown_when_captured_before_reference_impossible_ordering(self):
        ref = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        captured = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        self.assertEqual(EV.freshness_status(ref, captured, 100)["status"], EV.UNKNOWN)


class TimestampParsingTests(unittest.TestCase):
    def test_population_parser_preserves_subsecond_utc(self):
        parsed = POP._parse_utc("2026-08-30T04:52:13.100000Z")
        self.assertEqual(parsed, dt.datetime(2026, 8, 30, 4, 52, 13, 100000, tzinfo=UTC))

    def test_population_parser_rejects_naive_timestamp(self):
        with self.assertRaisesRegex(POP.PopulationError, "TIMESTAMP_NAIVE"):
            POP._parse_utc("2026-08-30T04:52:13.100000")


class CandleEvidenceTests(unittest.TestCase):
    def test_normal_complete_input_produces_finalized_candles(self):
        as_of = dt.datetime(2026, 8, 28, 0, 15, 0, tzinfo=UTC)
        captured_at = dt.datetime(2026, 8, 28, 0, 20, 0, tzinfo=UTC)
        rows = [candle("2026-08-28T00:00:00")]
        result = EV.build_candle_evidence("KRW-BTC", "15m", rows, as_of=as_of, captured_at=captured_at, max_staleness_seconds=1800)
        self.assertEqual(result["finalized_candle_count"], 1)
        self.assertEqual(result["in_progress_candle_count"], 0)
        self.assertEqual(result["freshness"]["status"], EV.FRESH)
        self.assertEqual(result["authority"]["decision_eligible"], False)
        self.assertEqual(result["authority"]["order_authorized"], False)

    def test_in_progress_candle_excluded_from_finalized_output(self):
        as_of = dt.datetime(2026, 8, 28, 0, 10, 0, tzinfo=UTC)
        rows = [candle("2026-08-28T00:00:00")]  # 15m candle closes at 00:15, still open at as_of
        result = EV.build_candle_evidence("KRW-BTC", "15m", rows, as_of=as_of, captured_at=as_of, max_staleness_seconds=1800)
        self.assertEqual(result["finalized_candle_count"], 0)
        self.assertEqual(result["in_progress_candle_count"], 1)
        self.assertEqual(result["finalized_candles"], [])

    def test_stale_evidence_flagged_and_excluded_from_action_eligible_freshness(self):
        as_of = dt.datetime(2026, 8, 28, 0, 15, 0, tzinfo=UTC)
        captured_at = dt.datetime(2026, 8, 28, 5, 0, 0, tzinfo=UTC)  # long after close
        rows = [candle("2026-08-28T00:00:00")]
        result = EV.build_candle_evidence("KRW-BTC", "15m", rows, as_of=as_of, captured_at=captured_at, max_staleness_seconds=1800)
        self.assertEqual(result["freshness"]["status"], EV.STALE)


class OrderbookEvidenceTests(unittest.TestCase):
    def test_whole_second_capture_keeps_legacy_timestamp_serialization(self):
        row = orderbook_row(timestamp_ms=1788065532999)
        captured = dt.datetime(2026, 8, 30, 4, 52, 13, tzinfo=UTC)
        result = EV.build_orderbook_evidence(
            "KRW-BTC", row, captured_at=captured, max_staleness_seconds=300,
            depth_levels=1, slippage_notional_krw="5000",
            max_spread_bps_normal="1000", max_slippage_bps_normal="1000",
        )
        self.assertEqual(result["freshness"]["status"], EV.FRESH)
        self.assertEqual(result["observed_at"], "2026-08-30T04:52:12Z")
        self.assertEqual(result["available_at"], "2026-08-30T04:52:13Z")

    def test_subsecond_observed_and_available_times_are_not_truncated(self):
        row = orderbook_row(timestamp_ms=1788065533049)
        captured = dt.datetime(2026, 8, 30, 4, 52, 13, 100000, tzinfo=UTC)
        result = EV.build_orderbook_evidence(
            "KRW-BTC", row, captured_at=captured, max_staleness_seconds=300,
            depth_levels=1, slippage_notional_krw="5000",
            max_spread_bps_normal="1000", max_slippage_bps_normal="1000",
        )
        self.assertEqual(result["freshness"]["status"], EV.FRESH)
        self.assertEqual(result["observed_at"], "2026-08-30T04:52:13.049000Z")
        self.assertEqual(result["available_at"], "2026-08-30T04:52:13.100000Z")

    def test_normal_spread_depth_slippage_computed(self):
        row = orderbook_row(levels=[
            {"bid_price": 999, "bid_size": 10, "ask_price": 1001, "ask_size": 10},
            {"bid_price": 998, "bid_size": 10, "ask_price": 1002, "ask_size": 10},
        ])
        result = EV.build_orderbook_evidence(
            "KRW-BTC", row, captured_at=dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC),
            max_staleness_seconds=300, depth_levels=2, slippage_notional_krw="5000",
            max_spread_bps_normal="1000", max_slippage_bps_normal="1000",
        )
        self.assertEqual(result["spread_status"], "NORMAL")
        self.assertEqual(result["slippage_status"], "NORMAL")
        self.assertEqual(result["depth"]["levels_available"], 2)
        self.assertIsNotNone(result["spread_bps"])
        self.assertIsNotNone(result["slippage_bps"])

    def test_missing_orderbook_for_one_market_fails_closed_others_unaffected(self):
        with self.assertRaisesRegex(EV.MarketEvidenceError, "ORDERBOOK_UNITS_MISSING"):
            EV.build_orderbook_evidence(
                "KRW-BTC", {"market": "KRW-BTC", "orderbook_units": []},
                captured_at=dt.datetime.now(tz=UTC), max_staleness_seconds=300, depth_levels=2,
                slippage_notional_krw="5000", max_spread_bps_normal="100", max_slippage_bps_normal="100",
            )
        # a different, well-formed market is unaffected
        ok_row = orderbook_row("KRW-ETH")
        result = EV.build_orderbook_evidence(
            "KRW-ETH", ok_row, captured_at=dt.datetime.fromtimestamp(1756339200, tz=UTC),
            max_staleness_seconds=300, depth_levels=1, slippage_notional_krw="5000",
            max_spread_bps_normal="1000", max_slippage_bps_normal="1000",
        )
        self.assertEqual(result["market"], "KRW-ETH")

    def test_abnormal_spread_flagged_and_excluded_not_silently_accepted(self):
        row = orderbook_row(best_bid=500, best_ask=1500)  # huge spread
        result = EV.build_orderbook_evidence(
            "KRW-BTC", row, captured_at=dt.datetime.fromtimestamp(1756339200, tz=UTC),
            max_staleness_seconds=300, depth_levels=1, slippage_notional_krw="5000",
            max_spread_bps_normal="100", max_slippage_bps_normal="100000",
        )
        self.assertEqual(result["spread_status"], "ABNORMAL_EXCLUDED")

    def test_abnormal_slippage_flagged_when_depth_insufficient_or_extreme(self):
        # depth cannot fill the requested notional -> NOT_COMPUTABLE, never silently normal
        row = orderbook_row(levels=[{"bid_price": 999, "bid_size": 1, "ask_price": 1001, "ask_size": 1}])
        result = EV.build_orderbook_evidence(
            "KRW-BTC", row, captured_at=dt.datetime.fromtimestamp(1756339200, tz=UTC),
            max_staleness_seconds=300, depth_levels=1, slippage_notional_krw="100000000",
            max_spread_bps_normal="1000", max_slippage_bps_normal="1000",
        )
        self.assertEqual(result["slippage_status"], "NOT_COMPUTABLE")
        self.assertIsNone(result["slippage_bps"])


class TradesEvidenceTests(unittest.TestCase):
    def test_normal_trades_evidence(self):
        rows = trade_rows()
        result = EV.build_trades_evidence("KRW-BTC", rows, captured_at=dt.datetime.fromtimestamp(1756339200, tz=UTC), max_staleness_seconds=600)
        self.assertEqual(result["trade_count"], 1)
        self.assertEqual(result["freshness"]["status"], EV.FRESH)

    def test_missing_required_field_fails_closed(self):
        rows = [{"market": "KRW-BTC", "trade_price": 1000}]  # missing trade_volume/timestamp/ask_bid
        with self.assertRaisesRegex(EV.MarketEvidenceError, "TRADE_FIELD_MISSING"):
            EV.build_trades_evidence("KRW-BTC", rows, captured_at=dt.datetime.now(tz=UTC), max_staleness_seconds=600)

    def test_empty_trades_fails_closed(self):
        with self.assertRaisesRegex(EV.MarketEvidenceError, "TRADES_EMPTY_OR_INVALID"):
            EV.build_trades_evidence("KRW-BTC", [], captured_at=dt.datetime.now(tz=UTC), max_staleness_seconds=600)


class FullPacketTests(unittest.TestCase):
    def test_normal_complete_packet(self):
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        captured_at = dt.datetime(2026, 8, 28, 1, 5, 0, tzinfo=UTC)
        packet = EV.build_market_evidence_packet(
            "KRW-BTC", candles_by_timeframe=full_candles_by_timeframe(), trades=trade_rows(timestamp_ms=int(as_of.timestamp() * 1000)),
            orderbook_row=orderbook_row(timestamp_ms=int(as_of.timestamp() * 1000)),
            as_of=as_of, captured_at=captured_at, policy=default_policy(),
        )
        self.assertEqual(packet["market"], "KRW-BTC")
        self.assertEqual(set(packet["candles"]), set(FIN.TIMEFRAMES))
        self.assertFalse(packet["policy_ratified"])
        self.assertEqual(packet["authority"]["order_authorized"], False)
        for key in EV._EVIDENCE_AUTHORITY:
            self.assertFalse(packet["authority"][key])

    def test_missing_timeframe_fails_closed(self):
        incomplete = full_candles_by_timeframe()
        del incomplete["1d"]
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        with self.assertRaisesRegex(EV.MarketEvidenceError, "CANDLES_MISSING"):
            EV.build_market_evidence_packet(
                "KRW-BTC", candles_by_timeframe=incomplete, trades=trade_rows(), orderbook_row=orderbook_row(),
                as_of=as_of, captured_at=as_of, policy=default_policy(),
            )

    def test_future_dated_evidence_rejected(self):
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        captured_at = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)  # captured BEFORE as_of: impossible
        with self.assertRaisesRegex(EV.MarketEvidenceError, "CAPTURED_AT_BEFORE_AS_OF"):
            EV.build_market_evidence_packet(
                "KRW-BTC", candles_by_timeframe=full_candles_by_timeframe(), trades=trade_rows(), orderbook_row=orderbook_row(),
                as_of=as_of, captured_at=captured_at, policy=default_policy(),
            )

    def test_determinism_same_input_twice_identical_output(self):
        as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=UTC)
        captured_at = dt.datetime(2026, 8, 28, 1, 5, 0, tzinfo=UTC)
        kwargs = dict(
            candles_by_timeframe=full_candles_by_timeframe(), trades=trade_rows(), orderbook_row=orderbook_row(),
            as_of=as_of, captured_at=captured_at, policy=default_policy(),
        )
        first = EV.build_market_evidence_packet("KRW-BTC", **kwargs)
        second = EV.build_market_evidence_packet("KRW-BTC", **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])


class AuthorityAndEndpointSafetyTests(unittest.TestCase):
    def test_every_authority_field_hardcoded_false(self):
        for key, value in EV._EVIDENCE_AUTHORITY.items():
            self.assertFalse(value, key)

    def test_no_order_withdrawal_or_private_endpoint_reference_in_module(self):
        text = (ROOT / "microstructure" / "upbit_market_evidence.py").read_text(encoding="utf-8")
        for forbidden in ("/v1/orders", "/v1/withdraws", "/v1/deposits", "Authorization", "api_key", "secret_key", "JWT"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
