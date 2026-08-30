"""P9-06 deterministic gap -> public REST -> receipt -> ledger recovery.

All source bytes in this file are fixtures.  They prove mechanism only and
must never be counted as NATURAL_AUTOMATED evidence.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


G = load_module("upbit_realtime_gate_recovery_test", ROOT / "realtime" / "upbit_realtime_gate.py")
C = load_module(
    "upbit_realtime_capture_recovery_test",
    ROOT / ".github" / "scripts" / "upbit_realtime_capture.py",
)


def z(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def trade(ts: int, sid: int) -> dict:
    return {
        "type": "trade",
        "code": "KRW-BTC",
        "trade_price": 100_000_000.0,
        "trade_volume": 0.001,
        "timestamp": ts,
        "trade_timestamp": ts,
        "sequential_id": sid,
        "ask_bid": "BID",
        "stream_type": "REALTIME",
    }


def ws_candle(open_time: str, *, trade_price: int = 1005, timestamp: int = 1_800_000_000_000) -> dict:
    return {
        "type": "candle.15m",
        "code": "KRW-BTC",
        "candle_date_time_utc": open_time,
        "opening_price": 1000,
        "high_price": 1010,
        "low_price": 990,
        "trade_price": trade_price,
        "candle_acc_trade_price": 123456,
        "candle_acc_trade_volume": 12.3,
        "timestamp": timestamp,
        "stream_type": "REALTIME",
    }


def rest_candle(open_time: str, *, trade_price: int = 1005) -> dict:
    return {
        "market": "KRW-BTC",
        "candle_date_time_utc": open_time,
        "candle_date_time_kst": open_time,
        "opening_price": 1000,
        "high_price": 1010,
        "low_price": 990,
        "trade_price": trade_price,
        "timestamp": int(z(open_time + "Z").timestamp() * 1000),
        "candle_acc_trade_price": 123456,
        "candle_acc_trade_volume": 12.3,
        "unit": 15,
    }


def gate() -> "G.RealtimeGate":
    return G.RealtimeGate(
        markets=["KRW-BTC"],
        max_reconnect_attempts=3,
        base_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
        max_staleness_seconds_by_kind={"ticker": 20, "trade": 20, "orderbook": 20},
        connection_gap_min_seconds=5,
        provider_gap_threshold_seconds_by_kind={"ticker": 5, "trade": 5, "orderbook": 5, "candle": 20},
        max_backfill_window_seconds=300,
        max_backfill_rows=20,
    )


class GapReceiptRecoveryTests(unittest.TestCase):
    def test_time_gap_stays_pending_until_hashed_public_rest_receipt_applies(self):
        realtime = gate()
        t0 = z("2026-08-28T00:00:00Z")
        realtime.handle_message(trade(int(t0.timestamp() * 1000), 100), received_at=t0)
        second = t0 + dt.timedelta(seconds=30)
        realtime.handle_message(trade(int(second.timestamp() * 1000), 200), received_at=second)

        pending = realtime.pending_gap_windows()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source"], "WS_PROVIDER_TIME_GAP")
        self.assertTrue(pending[0]["bounded"])
        self.assertEqual(realtime.status_snapshot(second)["overall_status"], G.UNKNOWN)

        contract = G.load_contract()
        requests = C.plan_public_rest_backfill(pending, realtime.markets, contract)
        self.assertTrue(requests)
        self.assertTrue(all(request["method"] == "GET" for request in requests))
        self.assertTrue(all(request["auth_required"] is False for request in requests))
        self.assertTrue(all(request["count"] <= contract["rest_backfill_max_rows"] for request in requests))
        self.assertTrue(all("api.upbit.com/v1/" in request["url"] for request in requests))
        trade_request = next(request for request in requests if request["kind"] == "trade")
        trade_query = urllib.parse.parse_qs(urllib.parse.urlparse(trade_request["url"]).query)
        self.assertEqual(trade_query["to"], ["00:00:30"])
        self.assertEqual(
            trade_request["request_range"],
            {"from": "2026-08-28T00:00:00Z", "to": "2026-08-28T00:00:30Z"},
        )

        payloads = {}
        for request in requests:
            if request["kind"] == "candle" and request["timeframe"] == "15m":
                payloads[request["url"]] = [rest_candle("2026-08-28T00:00:00")]
            else:
                payloads[request["url"]] = []

        clock_values = iter(
            z(value) + dt.timedelta(milliseconds=100 if index % 2 == 0 else 900)
            for index, value in enumerate((
                "2026-08-28T00:00:31Z", "2026-08-28T00:00:32Z",
                "2026-08-28T00:00:33Z", "2026-08-28T00:00:34Z",
                "2026-08-28T00:00:35Z", "2026-08-28T00:00:36Z",
                "2026-08-28T00:00:37Z", "2026-08-28T00:00:38Z",
            ))
        )

        def fetcher(url: str, _timeout: int) -> bytes:
            return json.dumps(payloads[url], sort_keys=True).encode("utf-8")

        sleeps = []
        receipt = C.execute_public_rest_backfill(
            requests,
            gap_ids=[pending[0]["gap_id"]],
            fetcher=fetcher,
            clock=lambda: next(clock_values),
            evidence_class=G.SYNTHETIC_FIXTURE,
            sleeper=sleeps.append,
        )
        self.assertEqual(
            sleeps,
            [C.REST_BACKFILL_MIN_REQUEST_INTERVAL_SECONDS] * (len(requests) - 1),
        )
        self.assertEqual(receipt["schema_version"], "upbit_public_rest_backfill_receipt/1")
        self.assertEqual(receipt["evidence_class"], G.SYNTHETIC_FIXTURE)
        self.assertEqual(G.payload_sha256({k: v for k, v in receipt.items() if k != "receipt_sha256"}), receipt["receipt_sha256"])
        for response in receipt["responses"]:
            self.assertIn("request_range", response)
            self.assertIn("returned_range", response)
            self.assertIn("provider_time", response)
            self.assertIn("transport_time", response)
            self.assertEqual(G.payload_sha256(response["payload"]), response["payload_sha256"])
            self.assertEqual(response["transport_time"]["duration_milliseconds"], 1000)

        accepted_before = realtime.counts["accepted"]
        applied = realtime.apply_backfill_receipt(receipt, revalidated_at=z("2026-08-28T00:15:01Z"))
        self.assertEqual(applied["action"], "RECEIPT_APPLIED")
        self.assertEqual(realtime.counts["accepted"], accepted_before)
        self.assertEqual(realtime.candles.finalized_count("KRW-BTC", "15m"), 1)
        self.assertEqual(realtime.pending_gap_windows(), [])

        ledger_count = realtime.candles.finalized_count("KRW-BTC", "15m")
        reapplied = realtime.apply_backfill_receipt(receipt, revalidated_at=z("2026-08-28T00:15:02Z"))
        self.assertEqual(reapplied["action"], "IDEMPOTENT_REVALIDATED")
        self.assertEqual(realtime.counts["accepted"], accepted_before)
        self.assertEqual(realtime.candles.finalized_count("KRW-BTC", "15m"), ledger_count)

    def test_gap_larger_than_bound_is_visible_and_never_planned(self):
        realtime = gate()
        t0 = z("2026-08-28T00:00:00Z")
        realtime.handle_message(trade(int(t0.timestamp() * 1000), 100), received_at=t0)
        second = t0 + dt.timedelta(seconds=301)
        realtime.handle_message(trade(int(second.timestamp() * 1000), 200), received_at=second)
        pending = realtime.pending_gap_windows()
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0]["bounded"])
        self.assertEqual(C.plan_public_rest_backfill(pending, realtime.markets, G.load_contract()), [])
        self.assertEqual(realtime.status_snapshot(second)["overall_status"], G.UNKNOWN)

    def test_tampered_receipt_fails_before_gap_or_ledger_mutation(self):
        realtime = gate()
        receipt = G.build_backfill_receipt(
            gap_ids=["a" * 64],
            responses=[],
            evidence_class=G.SYNTHETIC_FIXTURE,
            generated_at=z("2026-08-28T00:00:00Z"),
        )
        receipt["gap_ids"] = ["b" * 64]
        with self.assertRaises(G.RealtimeGateError):
            realtime.apply_backfill_receipt(receipt, revalidated_at=z("2026-08-28T00:00:01Z"))
        self.assertEqual(realtime.candles.total_finalized_count(), 0)

    def test_partial_receipt_cannot_hide_or_resolve_pending_gap(self):
        realtime = gate()
        t0 = z("2026-08-28T00:00:00Z")
        realtime.handle_message(trade(int(t0.timestamp() * 1000), 100), received_at=t0)
        second = t0 + dt.timedelta(seconds=30)
        realtime.handle_message(trade(int(second.timestamp() * 1000), 200), received_at=second)
        gap_id = realtime.pending_gap_windows()[0]["gap_id"]
        incomplete = G.build_backfill_receipt(
            gap_ids=[gap_id], responses=[], evidence_class=G.SYNTHETIC_FIXTURE,
            generated_at=second,
        )
        with self.assertRaisesRegex(G.RealtimeGateError, "BACKFILL_RECEIPT_COVERAGE_INCOMPLETE"):
            realtime.apply_backfill_receipt(incomplete, revalidated_at=second)
        self.assertEqual([row["gap_id"] for row in realtime.pending_gap_windows()], [gap_id])


class FinalizedCandleLedgerTests(unittest.TestCase):
    def test_in_progress_is_preserved_then_promoted_once_at_close(self):
        realtime = gate()
        before_close = z("2026-08-28T00:05:00Z")
        result = realtime.handle_message(
            ws_candle("2026-08-28T00:00:00"),
            received_at=before_close,
            as_of=before_close,
        )
        self.assertEqual(result["candle_ingest"]["in_progress_count"], 1)
        self.assertEqual(realtime.candles.in_progress_count("KRW-BTC", "15m"), 1)
        self.assertEqual(realtime.candles.finalized_count("KRW-BTC", "15m"), 0)

        promoted = realtime.candles.promote_closed(as_of=z("2026-08-28T00:15:00Z"))
        self.assertEqual(promoted["added_count"], 1)
        self.assertEqual(realtime.candles.in_progress_count("KRW-BTC", "15m"), 0)
        self.assertEqual(realtime.candles.finalized_count("KRW-BTC", "15m"), 1)
        self.assertEqual(realtime.candles.promote_closed(as_of=z("2026-08-28T00:15:01Z"))["added_count"], 0)

    def test_rest_recovery_promotes_missing_closed_candle_without_ws_accepted_increment(self):
        realtime = gate()
        request = {
                "kind": "candle", "timeframe": "15m", "market": "KRW-BTC",
                "method": "GET", "url": "https://api.upbit.com/v1/candles/minutes/15?market=KRW-BTC&count=1",
                "count": 1, "auth_required": False,
                "request_range": {"from": "2026-08-28T00:00:00Z", "to": "2026-08-28T00:15:00Z"},
        }
        request["request_id"] = G.payload_sha256(request)
        response = G.backfill_response_record(
            request=request,
            payload=[rest_candle("2026-08-28T00:00:00")],
            requested_at=z("2026-08-28T00:15:01Z"),
            received_at=z("2026-08-28T00:15:02Z"),
        )
        receipt = G.build_backfill_receipt(
            gap_ids=[], responses=[response], evidence_class=G.PIT_REPLAY,
            generated_at=z("2026-08-28T00:15:02Z"),
        )
        realtime.apply_backfill_receipt(receipt, revalidated_at=z("2026-08-28T00:15:03Z"))
        self.assertEqual(realtime.counts["accepted"], 0)
        self.assertEqual(realtime.candles.finalized_count("KRW-BTC", "15m"), 1)


class RatifiedFreshnessPolicyTests(unittest.TestCase):
    def test_exact_hash_ratified_packet_loads_and_crypto_thresholds_match_notion(self):
        policy = G.load_ratified_freshness_policy(observed_at=z("2026-08-30T00:35:58Z"))
        self.assertEqual(policy["schema_version"], "intraday_freshness_policy/1")
        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(policy["max_provider_age_seconds_by_market"]["CRYPTO"], 20)
        self.assertEqual(policy["max_transport_delay_seconds_by_market"]["CRYPTO"], 3)

    def test_proposal_and_tampered_or_stale_packets_are_rejected(self):
        with self.assertRaises(G.RealtimeGateError):
            G.load_ratified_freshness_policy(
                ROOT / "config" / "upbit_realtime_freshness_policy_proposal.json",
                observed_at=z("2026-08-30T00:35:58Z"),
            )

        policy = json.loads(G.RATIFIED_FRESHNESS_POLICY_PATH.read_text(encoding="utf-8"))
        policy["max_provider_age_seconds_by_market"]["CRYPTO"] = 21
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tampered.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaises(G.RealtimeGateError):
                G.load_ratified_freshness_policy(path, observed_at=z("2026-08-30T00:35:58Z"))

        with self.assertRaises(G.RealtimeGateError):
            G.load_ratified_freshness_policy(observed_at=z("9999-12-31T23:59:59Z"))

    def test_consumer_only_accepts_crypto_and_fails_closed_on_transport_staleness(self):
        observed = z("2026-08-30T00:36:00Z")
        quote = {
            "asset_id": "CRYPTO.UPBIT.KRW-BTC", "market": "CRYPTO", "price": "1", "volume": "1",
            "quote_currency": "KRW", "provider_id": "UPBIT.WS.PUBLIC",
            "provider_timestamp": "2026-08-30T00:35:59Z", "received_at": "2026-08-30T00:36:00Z",
            "source_ref": "wss://api.upbit.com/websocket/v1#ticker", "source_sha256": "a" * 64,
        }
        fresh = G.evaluate_with_ratified_freshness_policy([quote], observed_at=observed, batch_id="P9_06_TEST")
        self.assertEqual(fresh["status"], "EVALUATED")
        self.assertEqual(fresh["result"]["results"][0]["freshness_status"], "FRESH")

        stale = copy.deepcopy(quote)
        stale["provider_timestamp"] = "2026-08-30T00:35:50Z"
        stale["received_at"] = "2026-08-30T00:35:55Z"
        stale_result = G.evaluate_with_ratified_freshness_policy(
            [stale], observed_at=observed, batch_id="P9_06_STALE_TEST",
        )
        self.assertEqual(stale_result["result"]["results"][0]["freshness_status"], "STALE")

        non_crypto = copy.deepcopy(quote)
        non_crypto["market"] = "US"
        with self.assertRaises(G.RealtimeGateError):
            G.evaluate_with_ratified_freshness_policy([non_crypto], observed_at=observed, batch_id="P9_06_SCOPE_TEST")


class EvidenceClassTests(unittest.TestCase):
    def test_replay_and_synthetic_never_count_as_natural_or_p10_day(self):
        summary = G.summarize_evidence_classes([
            {"evidence_class": G.NATURAL_AUTOMATED, "started_at": "2026-08-30T00:00:00Z"},
            {"evidence_class": G.PIT_REPLAY, "started_at": "2026-08-30T01:00:00Z"},
            {"evidence_class": G.SYNTHETIC_FIXTURE, "started_at": "2026-08-30T02:00:00Z"},
        ])
        self.assertEqual(summary["natural_sample_count"], 1)
        self.assertEqual(summary["p10_12_natural_day_count"], 1)
        self.assertEqual(summary["pit_replay_count"], 1)
        self.assertEqual(summary["synthetic_fixture_count"], 1)


if __name__ == "__main__":
    unittest.main()
