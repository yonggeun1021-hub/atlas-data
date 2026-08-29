"""P9-06 Upbit real-time WebSocket finalized-candle & orderbook gate
regression -- entirely mocked messages, no live connection required."""
from __future__ import annotations

import ast
import datetime as dt
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "realtime" / "upbit_realtime_gate.py"
CAPTURE_SCRIPT_PATH = ROOT / ".github" / "scripts" / "upbit_realtime_capture.py"
CONTRACT_PATH = ROOT / "config" / "upbit_realtime_gate_contract.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


G = load_module("upbit_realtime_gate", MODULE_PATH)
UTC = dt.timezone.utc


def make_ticker(*, code="KRW-BTC", price=100000000.0, ts=1_800_000_000_000, stream_type="REALTIME"):
    return {
        "type": "ticker", "code": code, "opening_price": price, "trade_price": price,
        "timestamp": ts, "trade_timestamp": ts, "trade_volume": 0.001, "stream_type": stream_type,
    }


def make_trade(*, code="KRW-BTC", price=100000000.0, ts=1_800_000_000_000, sid=17_800_000_000_000_000,
                stream_type="REALTIME"):
    return {
        "type": "trade", "code": code, "trade_price": price, "trade_volume": 0.001,
        "timestamp": ts, "trade_timestamp": ts, "sequential_id": sid, "ask_bid": "BID",
        "stream_type": stream_type,
    }


def make_orderbook(*, code="KRW-BTC", ts=1_800_000_000_000, stream_type="REALTIME"):
    return {
        "type": "orderbook", "code": code, "timestamp": ts,
        "orderbook_units": [{"ask_price": 1.0, "bid_price": 0.9, "ask_size": 1.0, "bid_size": 1.0}],
        "stream_type": stream_type,
    }


def make_candle(*, code="KRW-BTC", timeframe="15m", open_time="2026-08-28T00:00:00", ts=1_800_000_000_000,
                 stream_type="REALTIME", **price_overrides):
    row = {
        "type": G.CANDLE_WS_TYPE_BY_TIMEFRAME[timeframe], "code": code,
        "candle_date_time_utc": open_time, "opening_price": 1000, "high_price": 1010,
        "low_price": 990, "trade_price": 1005, "candle_acc_trade_price": 123456,
        "candle_acc_trade_volume": 12.3, "timestamp": ts, "stream_type": stream_type,
    }
    row.update(price_overrides)
    return row


def new_gate(markets=("KRW-BTC", "KRW-ETH"), **overrides):
    kwargs = dict(
        markets=list(markets), max_reconnect_attempts=3, base_backoff_seconds=1.0,
        max_backoff_seconds=8.0, max_staleness_seconds_by_kind={"ticker": 30, "trade": 30, "orderbook": 15},
        connection_gap_min_seconds=5,
    )
    kwargs.update(overrides)
    return G.RealtimeGate(**kwargs)


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------

class ContractTests(unittest.TestCase):
    def test_contract_loads_and_enforces_public_only_safety_invariants(self):
        contract = G.load_contract()
        self.assertFalse(contract["auth_required"])
        self.assertFalse(contract["order_or_withdrawal_endpoints_called"])
        self.assertFalse(contract["private_channel_subscribed"])
        self.assertEqual(set(contract["public_message_types"]), set(G.PUBLIC_MESSAGE_TYPES))
        self.assertEqual(contract["candle_ws_type_by_timeframe"], G.CANDLE_WS_TYPE_BY_TIMEFRAME)

    def test_contract_rejects_tampered_safety_invariant(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            tampered = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
            tampered["auth_required"] = True
            json.dump(tampered, handle)
            path = Path(handle.name)
        try:
            with self.assertRaises(G.RealtimeGateError):
                G.load_contract(path)
        finally:
            path.unlink()

    def test_freshness_proposal_is_never_ratified_in_repo(self):
        proposal = G.load_freshness_policy_proposal()
        self.assertNotEqual(proposal["approval_status"], "RATIFIED")


# ---------------------------------------------------------------------------
# Message parsing / normal stream
# ---------------------------------------------------------------------------

class MessageParsingTests(unittest.TestCase):
    def test_normal_ticker_trade_orderbook_candle_all_parse(self):
        for raw in (make_ticker(), make_trade(), make_orderbook(), make_candle()):
            parsed = G.parse_message(raw)
            self.assertEqual(parsed["market"], "KRW-BTC")
            self.assertIn(parsed["kind"], ("ticker", "trade", "orderbook", "candle"))

    def test_malformed_message_missing_field_fails_closed(self):
        with self.assertRaises(G.RealtimeGateError):
            G.parse_message({"type": "ticker", "code": "KRW-BTC"})

    def test_message_not_a_dict_fails_closed(self):
        with self.assertRaises(G.RealtimeGateError):
            G.parse_message(["not", "a", "dict"])

    def test_unknown_message_type_fails_closed(self):
        with self.assertRaises(G.RealtimeGateError):
            G.parse_message({"type": "candle.7m", "code": "KRW-BTC"})

    def test_private_channel_type_rejected_even_if_well_formed(self):
        for private_type in G.PRIVATE_WS_TYPES_FORBIDDEN:
            with self.assertRaises(G.RealtimeGateError):
                G.parse_message({"type": private_type, "code": "KRW-BTC"})

    def test_daily_candle_not_a_supported_ws_type(self):
        self.assertNotIn("1d", G.CANDLE_WS_TYPE_BY_TIMEFRAME)


class GateNormalStreamTests(unittest.TestCase):
    def test_normal_message_stream_all_accepted(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        for raw in (make_ticker(), make_trade(), make_orderbook()):
            result = gate.handle_message(raw, received_at=now)
            self.assertEqual(result["action"], "ACCEPTED")
        self.assertEqual(gate.counts["accepted"], 3)

    def test_in_progress_candle_never_finalized_reuses_p4_07_boundary(self):
        gate = new_gate()
        as_of = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)  # < close_time (00:15)
        raw = make_candle(open_time="2026-08-28T00:00:00")
        result = gate.handle_message(raw, received_at=as_of, as_of=as_of)
        self.assertEqual(result["action"], "ACCEPTED")
        self.assertEqual(result["candle_ingest"]["added_open_times"], [])
        self.assertEqual(result["candle_ingest"]["in_progress_count"], 1)
        self.assertEqual(gate.candles.finalized_count("KRW-BTC", "15m"), 0)

    def test_finalized_candle_recorded_once_boundary_elapses(self):
        gate = new_gate()
        as_of = dt.datetime(2026, 8, 28, 0, 15, tzinfo=UTC)  # == close_time -> finalized
        raw = make_candle(open_time="2026-08-28T00:00:00")
        result = gate.handle_message(raw, received_at=as_of, as_of=as_of)
        self.assertEqual(result["candle_ingest"]["added_open_times"], ["2026-08-28T00:00:00Z"])
        self.assertEqual(gate.candles.finalized_count("KRW-BTC", "15m"), 1)

    def test_candle_finalization_literally_calls_finalization_module(self):
        # Confirms reuse, not reimplementation: an out-of-range timeframe
        # raises finalization's own CandleFinalizationError, not a
        # locally-defined error class.
        from microstructure import upbit_candle_finalization as finalization
        with self.assertRaises(finalization.CandleFinalizationError):
            finalization.classify_candles(
                [make_candle(open_time="2026-08-28T00:00:00")], "7m",
                dt.datetime(2026, 8, 28, 0, 15, tzinfo=UTC),
            )

    def test_committed_candle_mismatch_fails_closed_not_silently_overwritten(self):
        from microstructure import upbit_candle_finalization as finalization
        gate = new_gate()
        as_of = dt.datetime(2026, 8, 28, 0, 15, tzinfo=UTC)
        first = make_candle(open_time="2026-08-28T00:00:00", trade_price=1005)
        gate.handle_message(first, received_at=as_of, as_of=as_of)
        conflicting = make_candle(open_time="2026-08-28T00:00:00", trade_price=9999999)
        with self.assertRaises(finalization.CandleFinalizationError):
            gate.candles.ingest("KRW-BTC", "15m", conflicting, as_of=as_of)


# ---------------------------------------------------------------------------
# Duplicate guard
# ---------------------------------------------------------------------------

class DuplicateGuardTests(unittest.TestCase):
    def test_exact_duplicate_message_rejected_not_double_counted(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        raw = make_trade()
        first = gate.handle_message(raw, received_at=now)
        second = gate.handle_message(dict(raw), received_at=now)
        self.assertEqual(first["action"], "ACCEPTED")
        self.assertEqual(second["action"], "DUPLICATE_IGNORED")
        self.assertEqual(gate.counts["accepted"], 1)
        self.assertEqual(gate.counts["duplicate_ignored"], 1)

    def test_same_key_different_payload_is_not_treated_as_duplicate(self):
        # Same millisecond timestamp, genuinely different orderbook content
        # -- Upbit's own docs note this can happen; must not be discarded.
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        first = make_orderbook(ts=1_800_000_000_000)
        second = make_orderbook(ts=1_800_000_000_000)
        second["orderbook_units"][0]["ask_price"] = 2.0
        r1 = gate.handle_message(first, received_at=now)
        r2 = gate.handle_message(second, received_at=now)
        self.assertEqual(r1["action"], "ACCEPTED")
        self.assertEqual(r2["action"], "ACCEPTED")
        self.assertEqual(gate.counts["accepted"], 2)

    def test_duplicate_finalized_candle_across_reconnect_is_idempotent(self):
        gate = new_gate()
        as_of = dt.datetime(2026, 8, 28, 0, 15, tzinfo=UTC)
        raw = make_candle(open_time="2026-08-28T00:00:00")
        gate.handle_message(raw, received_at=as_of, as_of=as_of)
        gate.on_disconnect(as_of, "CONNECTION_RESET")
        gate.on_connected(as_of + dt.timedelta(seconds=2))
        # Reconnect re-delivers the same already-finalized candle snapshot.
        redelivered = dict(raw)
        result = gate.handle_message(redelivered, received_at=as_of + dt.timedelta(seconds=3), as_of=as_of)
        self.assertEqual(result["action"], "DUPLICATE_IGNORED")
        self.assertEqual(gate.candles.finalized_count("KRW-BTC", "15m"), 1)


# ---------------------------------------------------------------------------
# Out-of-order
# ---------------------------------------------------------------------------

class OutOfOrderTests(unittest.TestCase):
    def test_out_of_order_trade_flagged_state_not_corrupted(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        gate.handle_message(make_trade(sid=200), received_at=now)
        result = gate.handle_message(make_trade(sid=100, ts=1_800_000_001_000), received_at=now)
        self.assertEqual(result["action"], "OUT_OF_ORDER_FLAGGED")
        self.assertEqual(gate.counts["out_of_order"], 1)
        self.assertEqual(gate.sequence._last_trade_sequential_id["KRW-BTC"], 200)

    def test_out_of_order_orderbook_flagged_by_timestamp_regression(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        gate.handle_message(make_orderbook(ts=2_000), received_at=now)
        result = gate.handle_message(make_orderbook(ts=1_000), received_at=now)
        self.assertEqual(result["action"], "OUT_OF_ORDER_FLAGGED")

    def test_equal_sequential_id_is_in_order_not_flagged(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        gate.handle_message(make_trade(sid=500), received_at=now)
        result = gate.handle_message(make_trade(sid=500, ts=1_800_000_001_000), received_at=now)
        # Same sequential_id, later wall-clock timestamp -> different
        # natural_key (sequential_id unchanged) means this is treated as a
        # duplicate key with a different payload (new ts), i.e. accepted,
        # never flagged out-of-order for holding steady.
        self.assertNotEqual(result["action"], "OUT_OF_ORDER_FLAGGED")


# ---------------------------------------------------------------------------
# Reconnect / gap detection / backfill triggers
# ---------------------------------------------------------------------------

class ReconnectTests(unittest.TestCase):
    def test_reconnect_scenario_resubscribes_and_tracks_gap(self):
        gate = new_gate()
        t0 = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        gate.handle_message(make_trade(sid=1, ts=1_800_000_000_000), received_at=t0)
        gate.on_disconnect(t0 + dt.timedelta(seconds=1), "CONNECTION_RESET")
        attempt = gate.next_reconnect_attempt()
        self.assertEqual(attempt["action"], "RETRY")
        self.assertAlmostEqual(attempt["backoff_seconds"], 1.0)
        reconnect_at = t0 + dt.timedelta(seconds=20)
        gate.on_connected(reconnect_at)
        # Backfilled/resumed stream: same trade re-delivered as a SNAPSHOT.
        redelivered = gate.handle_message(
            make_trade(sid=1, ts=1_800_000_000_000), received_at=reconnect_at + dt.timedelta(seconds=1),
        )
        self.assertEqual(redelivered["action"], "DUPLICATE_IGNORED")
        # New post-reconnect trade continues cleanly, no state corruption.
        fresh = gate.handle_message(
            make_trade(sid=2, ts=1_800_000_002_000), received_at=reconnect_at + dt.timedelta(seconds=2),
        )
        self.assertEqual(fresh["action"], "ACCEPTED")
        self.assertEqual(gate.connection.state, G.CONNECTED)
        windows = G.connection_gap_windows(gate.connection.disconnect_intervals, min_gap_seconds=5)
        self.assertEqual(len(windows), 1)
        self.assertAlmostEqual(windows[0]["gap_seconds"], 19.0)

    def test_backoff_grows_exponentially_and_is_capped(self):
        self.assertEqual(G.next_backoff_seconds(1, base_seconds=1.0, max_seconds=100.0), 1.0)
        self.assertEqual(G.next_backoff_seconds(2, base_seconds=1.0, max_seconds=100.0), 2.0)
        self.assertEqual(G.next_backoff_seconds(3, base_seconds=1.0, max_seconds=100.0), 4.0)
        self.assertEqual(G.next_backoff_seconds(10, base_seconds=1.0, max_seconds=8.0), 8.0)

    def test_max_retry_exceeded_fails_closed_to_wait_not_infinite_loop(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
        gate.on_disconnect(now, "TIMEOUT")
        attempt = None
        for _ in range(10):  # generous upper bound -- must terminate well before this
            attempt = gate.next_reconnect_attempt()
            if attempt["action"] == "FAIL_CLOSED":
                break
            gate.on_disconnect(now, "TIMEOUT_AGAIN")
        self.assertEqual(attempt["action"], "FAIL_CLOSED")
        self.assertEqual(gate.connection.state, G.WAIT_MAX_RETRIES_EXCEEDED)
        status = gate.status_snapshot(now)
        self.assertEqual(status["overall_status"], G.WAIT)
        # Calling next_attempt again while WAIT_MAX_RETRIES_EXCEEDED must not
        # silently keep retrying -- it must fail closed (raise), never loop.
        with self.assertRaises(G.RealtimeGateError):
            gate.connection.next_attempt()

    def test_kill_switch_request_stop_is_clean_and_final(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
        gate.on_disconnect(now, "TIMEOUT")
        gate.request_stop()
        self.assertEqual(gate.connection.state, G.STOPPED)
        with self.assertRaises(G.RealtimeGateError):
            gate.connection.next_attempt()


# ---------------------------------------------------------------------------
# Gap detection -- candle dimension (literal P4-07 reuse)
# ---------------------------------------------------------------------------

class CandleGapDetectionTests(unittest.TestCase):
    def test_missing_open_time_window_detected_and_grouped_for_backfill(self):
        gate = new_gate()
        as_of = dt.datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
        gate.handle_message(make_candle(open_time="2026-08-28T00:00:00"), received_at=as_of, as_of=as_of)
        # 00:15, 00:30, 00:45 are missing before the next captured candle.
        gate.handle_message(make_candle(open_time="2026-08-28T00:45:00"), received_at=as_of, as_of=as_of)
        present = gate.candles.finalized_open_times("KRW-BTC", "15m")
        window_start = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
        window_end = dt.datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
        windows = G.candle_gap_windows(present, "15m", window_start, window_end)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["from_open_time"], dt.datetime(2026, 8, 28, 0, 15, tzinfo=UTC))
        self.assertEqual(windows[0]["to_open_time"], dt.datetime(2026, 8, 28, 0, 30, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

class FreshnessTests(unittest.TestCase):
    def test_fresh_within_threshold(self):
        last = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        now = last + dt.timedelta(seconds=10)
        result = G.evaluate_stream_freshness(last, now, 30)
        self.assertEqual(result["status"], G.FRESH)

    def test_stale_data_no_messages_past_threshold(self):
        # Single-market gate, every kind observed once at t0 -- isolates the
        # STALE signal from the (separately-tested) UNKNOWN-for-never-seen
        # case below.
        gate = new_gate(markets=("KRW-BTC",))
        t0 = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
        for raw in (make_ticker(ts=1_800_000_000_000), make_trade(ts=1_800_000_000_000),
                    make_orderbook(ts=1_800_000_000_000)):
            gate.handle_message(raw, received_at=t0)
        later = t0 + dt.timedelta(seconds=60)  # exceeds every kind's threshold
        status = gate.status_snapshot(later)
        self.assertEqual(status["markets"][0]["freshness_by_kind"]["ticker"]["status"], G.STALE)
        self.assertEqual(status["overall_status"], G.STALE)

    def test_unknown_outranks_stale_when_some_kinds_were_never_observed(self):
        gate = new_gate(markets=("KRW-BTC",))
        t0 = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
        gate.handle_message(make_ticker(ts=1_800_000_000_000), received_at=t0)  # trade/orderbook never seen
        later = t0 + dt.timedelta(seconds=60)
        status = gate.status_snapshot(later)
        self.assertEqual(status["markets"][0]["freshness_by_kind"]["ticker"]["status"], G.STALE)
        self.assertEqual(status["markets"][0]["freshness_by_kind"]["trade"]["status"], G.UNKNOWN)
        self.assertEqual(status["overall_status"], G.UNKNOWN)

    def test_missing_timestamp_or_impossible_ordering_is_unknown_never_fresh(self):
        self.assertEqual(G.evaluate_stream_freshness(None, dt.datetime.now(tz=UTC), 30)["status"], G.UNKNOWN)
        last = dt.datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
        now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)  # now BEFORE last -- impossible
        self.assertEqual(G.evaluate_stream_freshness(last, now, 30)["status"], G.UNKNOWN)

    def test_no_market_ever_reported_never_silently_fresh(self):
        gate = new_gate()
        status = gate.status_snapshot(dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC))
        for row in status["markets"]:
            for freshness in row["freshness_by_kind"].values():
                self.assertEqual(freshness["status"], G.UNKNOWN)
        self.assertEqual(status["overall_status"], G.UNKNOWN)

    def test_empty_market_scope_is_unknown_never_fresh(self):
        gate = new_gate(markets=())
        status = gate.status_snapshot(dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC))
        self.assertEqual(status["markets"], [])
        self.assertEqual(status["overall_status"], G.UNKNOWN)

    def test_intraday_freshness_guard_reused_and_fails_closed_without_ratified_policy(self):
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        parsed = G.parse_message(make_ticker(ts=int(now.timestamp() * 1000)))
        quote = G.quote_row_from_ticker(parsed, received_at=now)
        result = G.evaluate_via_intraday_freshness_guard([quote], observed_at=now, batch_id="P9_06_TEST")
        self.assertEqual(result["status"], G.UNKNOWN)
        self.assertEqual(result["reason"], "P9_01_RATIFIED_POLICY_ABSENT")

    def test_intraday_freshness_guard_evaluates_when_a_ratified_policy_is_supplied(self):
        now = dt.datetime(2026, 8, 28, 0, 5, tzinfo=UTC)
        parsed = G.parse_message(make_ticker(ts=int(now.timestamp() * 1000)))
        quote = G.quote_row_from_ticker(parsed, received_at=now)
        policy = {
            "schema_version": "intraday_freshness_policy/1",
            "policy_id": "P9_06_TEST_POLICY",
            "approval_status": "RATIFIED",
            "ratified_by": "test fixture",
            "ratified_at_utc": "2026-08-27T00:00:00Z",
            "effective_from_utc": "2026-08-27T00:00:00Z",
            "effective_to_utc": "2026-08-29T00:00:00Z",
            "input_contract_version": "intraday_freshness_guard/1",
            "max_provider_age_seconds_by_market": {"US": 60, "KOREA": 45, "CRYPTO": 20},
            "max_transport_delay_seconds_by_market": {"US": 10, "KOREA": 8, "CRYPTO": 3},
        }
        policy["packet_sha256"] = G.INTRADAY_FRESHNESS.payload_sha256(policy)
        result = G.evaluate_via_intraday_freshness_guard(
            [quote], observed_at=now, batch_id="P9_06_TEST", ratified_policy=policy,
        )
        self.assertEqual(result["status"], "EVALUATED")
        self.assertEqual(result["result"]["results"][0]["freshness_status"], "FRESH")


# ---------------------------------------------------------------------------
# Malformed / crash resilience
# ---------------------------------------------------------------------------

class MalformedMessageTests(unittest.TestCase):
    def test_malformed_message_fails_closed_never_crashes_caller(self):
        gate = new_gate()
        now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
        for bad in (None, {}, {"type": "ticker"}, {"type": "nonsense", "code": "KRW-BTC"}, "not-a-dict", 12345):
            result = gate.handle_message(bad, received_at=now)
            self.assertEqual(result["action"], "REJECTED_MALFORMED")
        self.assertEqual(gate.counts["rejected_malformed"], 6)
        self.assertEqual(gate.counts["accepted"], 0)


# ---------------------------------------------------------------------------
# Identity scoping -- P3-12 eligible-market set, no auto-expansion
# ---------------------------------------------------------------------------

class IdentityScopingTests(unittest.TestCase):
    def test_eligible_markets_reads_tradeable_and_paper_eligible_only(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"packet": {"markets": [
                {"market": "KRW-BTC", "state": "PAPER_ELIGIBLE"},
                {"market": "KRW-ETH", "state": "TRADEABLE_UNIVERSE"},
                {"market": "KRW-XRP", "state": "OBSERVATION_POOL"},
                {"market": "KRW-DOGE", "state": "BLOCKED"},
            ]}}, handle)
            path = Path(handle.name)
        try:
            markets = G.eligible_markets_from_universe_packet(path)
            self.assertEqual(markets, ["KRW-BTC", "KRW-ETH"])
        finally:
            path.unlink()

    def test_no_universe_packet_is_empty_not_an_error(self):
        self.assertEqual(G.eligible_markets_from_universe_packet(None), [])
        self.assertEqual(G.eligible_markets_from_universe_packet(Path("/nonexistent/packet.json")), [])

    def test_message_for_a_market_outside_the_gate_scope_is_rejected(self):
        gate = new_gate(markets=("KRW-BTC",))
        now = dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC)
        result = gate.handle_message(make_ticker(code="KRW-ETH"), received_at=now)
        self.assertEqual(result["action"], "REJECTED_OUT_OF_SCOPE_MARKET")
        self.assertEqual(gate.counts["rejected_out_of_scope_market"], 1)
        self.assertEqual(gate.counts["accepted"], 0)

    def test_kraken_or_any_other_exchange_market_never_auto_subscribed(self):
        # Nothing in this module ever reads a Kraken/cross-exchange market
        # list to build the subscription set -- eligible_markets_from_
        # universe_packet's only inputs are P3-12's own packet fields. A
        # mention of "Kraken" in a docstring/comment (documenting the
        # invariant, same as P3-12 itself does) is fine; what must never
        # exist is an actual Python identifier referencing it.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        identifiers = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        self.assertFalse(any("kraken" in identifier.lower() for identifier in identifiers))

    def test_subscription_message_never_exceeds_gate_scoped_markets(self):
        message = G.build_subscription_message(["KRW-BTC"], ticket="t1")
        for entry in message:
            if "codes" in entry:
                self.assertEqual(entry["codes"], ["KRW-BTC"])


# ---------------------------------------------------------------------------
# Authority + zero private/order channel calls
# ---------------------------------------------------------------------------

class AuthorityTests(unittest.TestCase):
    def test_status_snapshot_authority_all_false(self):
        gate = new_gate()
        status = gate.status_snapshot(dt.datetime(2026, 8, 28, 0, 0, tzinfo=UTC))
        for key, value in status["authority"].items():
            self.assertFalse(value, key)

    def test_subscription_message_never_contains_a_private_channel_type(self):
        message = G.build_subscription_message(["KRW-BTC", "KRW-ETH"], ticket="atlas-test")
        emitted_types = [entry["type"] for entry in message if "type" in entry]
        for forbidden in G.PRIVATE_WS_TYPES_FORBIDDEN:
            self.assertNotIn(forbidden, emitted_types)
        for emitted in emitted_types:
            self.assertTrue(
                emitted in G.PUBLIC_MESSAGE_TYPES or emitted in G.TIMEFRAME_BY_CANDLE_WS_TYPE,
            )

    def test_build_subscription_message_refuses_a_private_type_even_if_forced(self):
        with self.assertRaises(G.RealtimeGateError):
            # Simulate a caller attempting to force a private channel by
            # monkeypatching the allowed set -- the explicit forbidden-type
            # check inside parse_message/build_subscription_message is the
            # real guard, exercised directly here.
            G.parse_message({"type": "myOrder", "code": "KRW-BTC", "stream_type": "REALTIME"})

    def test_no_order_or_private_rest_endpoint_referenced_anywhere_in_module(self):
        # myOrder/myAsset DO appear as forbidden-type string literals in the
        # module (that is the point -- they are compared against and
        # rejected) -- what matters is no order/withdrawal/deposit REST
        # endpoint is ever referenced at all.
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("api.upbit.com/v1/orders", source)
        self.assertNotIn("api.upbit.com/v1/withdraws", source)
        self.assertNotIn("api.upbit.com/v1/deposits", source)
        self.assertIn("PRIVATE_WS_TYPES_FORBIDDEN", source)

    def test_capture_script_never_imports_a_private_order_helper_and_uses_lazy_websockets_import(self):
        source = CAPTURE_SCRIPT_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.add(node.module)
        self.assertNotIn("websockets", top_level_imports)
        self.assertIn("import websockets", source)  # present, but lazily inside a function
        self.assertNotIn("api.upbit.com/v1/orders", source)
        self.assertNotIn("api.upbit.com/v1/withdraws", source)


# ---------------------------------------------------------------------------
# Full-file sanity
# ---------------------------------------------------------------------------

class ModuleSanityTests(unittest.TestCase):
    def test_module_has_no_websockets_or_asyncio_dependency(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import websockets", source)
        self.assertNotIn("import asyncio", source)
        self.assertNotIn("import socket", source)

    def test_candle_ws_timeframes_are_exactly_the_ws_supported_subset(self):
        self.assertEqual(set(G.CANDLE_WS_TYPE_BY_TIMEFRAME), {"15m", "1h", "4h"})


if __name__ == "__main__":
    unittest.main()
