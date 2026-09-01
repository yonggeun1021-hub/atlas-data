"""P4-07 Upbit microstructure REST capture regression."""
from __future__ import annotations

import datetime as dt
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "upbit_microstructure_capture.py"
SPEC = importlib.util.spec_from_file_location("upbit_microstructure_capture", MODULE_PATH)
CAP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CAP)


def fixed_clock():
    return dt.datetime(2026, 8, 29, 1, 20, 0, tzinfo=dt.timezone.utc)


def build_fetcher(contract, markets, *, candle_count_by_timeframe=None):
    candle_count_by_timeframe = candle_count_by_timeframe or contract["candle_lookback_count_by_timeframe"]
    responses = {}
    for timeframe in contract["timeframes"]:
        unit = contract["candle_upbit_unit_by_timeframe"].get(timeframe)
        count = candle_count_by_timeframe[timeframe]
        for market in markets:
            if unit is not None:
                url = contract["candles_minutes_endpoint_template"].format(UNIT=unit, MARKET=market, COUNT=count)
            else:
                url = contract["candles_days_endpoint_template"].format(MARKET=market, COUNT=count)
            rows = [
                {
                    "market": market, "candle_date_time_utc": f"2026-08-{28 - i:02d}T00:00:00",
                    "opening_price": 1000, "high_price": 1010, "low_price": 990, "trade_price": 1005,
                    "candle_acc_trade_price": 123456, "candle_acc_trade_volume": 12.3,
                }
                for i in range(min(count, 3))
            ]
            responses[url] = json.dumps(rows).encode()
    for market in markets:
        url = contract["trades_endpoint_template"].format(MARKET=market, COUNT=contract["trades_lookback_count"])
        responses[url] = json.dumps([
            {"market": market, "trade_price": 1000, "trade_volume": 1.0, "timestamp": 1756339200000, "ask_bid": "BID"}
        ]).encode()
    if markets:
        encoded = ",".join(markets)
        url = contract["orderbook_endpoint_template"].format(MARKETS=encoded)
        responses[url] = json.dumps([
            {
                "market": m, "timestamp": 1756339200000,
                "orderbook_units": [{"bid_price": 999, "bid_size": 10, "ask_price": 1001, "ask_size": 10}],
            }
            for m in markets
        ]).encode()

    def fetcher(url, timeout):
        if url not in responses:
            raise AssertionError(f"capture requested an unexpected URL: {url}")
        return responses[url]

    return fetcher


class FetchWithRetryTests(unittest.TestCase):
    def test_succeeds_after_transient_failures_within_budget(self):
        calls = {"count": 0}
        sleeps = []

        def flaky(url, timeout):
            calls["count"] += 1
            if calls["count"] < 3:
                raise OSError("transient")
            return b"ok"

        result = CAP.fetch_with_retry(
            "http://x", fetcher=flaky, max_attempts=4, backoff_base_seconds=1.0, sleeper=sleeps.append,
        )
        self.assertEqual(result, b"ok")
        self.assertEqual(calls["count"], 3)
        self.assertEqual(sleeps, [1.0, 2.0])  # exponential backoff, 2 sleeps before the 3rd (successful) attempt

    def test_fails_closed_after_max_attempts_never_drops_silently(self):
        def always_fails(url, timeout):
            raise OSError("permanently down")

        with self.assertRaisesRegex(CAP.CaptureError, "FETCH_FAILED_MAX_RETRIES"):
            CAP.fetch_with_retry(
                "http://x", fetcher=always_fails, max_attempts=3, backoff_base_seconds=0.01, sleeper=lambda s: None,
            )

    def test_invalid_retry_policy_rejected(self):
        with self.assertRaisesRegex(CAP.CaptureError, "RETRY_POLICY_INVALID"):
            CAP.fetch_with_retry("http://x", fetcher=lambda u, t: b"x", max_attempts=0)


class LoadTargetMarketsTests(unittest.TestCase):
    def test_transition_lineage_is_revalidated_and_carried_into_p4_manifest_basis(self):
        lineage = {
            "record_payload_sha256": "b" * 64,
            "markets": ["KRW-BTC"],
            "market_count": 1,
        }
        manifest = {
            "source_record": {
                "path": "canonical.json", "file_sha256": "c" * 64,
                "payload_sha256": "d" * 64,
            },
            "successor_record": {
                "path": "successor.json", "file_sha256": "e" * 64,
                "payload_sha256": "b" * 64,
            },
        }
        selected = {
            "path": Path("successor.json").resolve(),
            "record": {"payload_sha256": "b" * 64},
            "transition_manifest": manifest,
            "transition_manifest_file_sha256": "f" * 64,
            "transition_manifest_payload_sha256": "1" * 64,
        }
        with (
            mock.patch.object(CAP.P3_P4, "consume_universe_record", return_value=dict(lineage)),
            mock.patch.object(CAP.UNIVERSE, "validate_same_vintage_transition", return_value=selected),
        ):
            result = CAP.load_universe_lineage(
                Path("successor.json"),
                expected_record_sha256="b" * 64,
                transition_manifest_path=Path("transition.json"),
            )
        self.assertEqual(result["transition"]["canonical_source"], manifest["source_record"])
        self.assertEqual(result["transition"]["successor"], manifest["successor_record"])
        self.assertEqual(result["transition"]["manifest_file_sha256"], "f" * 64)

    def test_no_packet_path_returns_empty(self):
        self.assertEqual(CAP.load_target_markets(None), [])

    def test_missing_file_returns_empty(self):
        self.assertEqual(CAP.load_target_markets(Path("/nonexistent/packet.json")), [])

    def test_selects_only_tradeable_universe_and_paper_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "packet.json"
            path.write_text(json.dumps({
                "packet": {
                    "markets": [
                        {"market": "KRW-BTC", "state": "PAPER_ELIGIBLE"},
                        {"market": "KRW-ETH", "state": "TRADEABLE_UNIVERSE"},
                        {"market": "KRW-XRP", "state": "OBSERVATION_POOL"},
                    ]
                }
            }))
            self.assertEqual(CAP.load_target_markets(path), ["KRW-BTC", "KRW-ETH"])


class CaptureSnapshotTests(unittest.TestCase):
    def test_transition_lineage_manifest_and_source_pins_are_durable_and_required(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC"]
        fetcher = build_fetcher(contract, markets)
        base_lineage = {
            "snapshot_date": "2026-08-29",
            "record_path": (
                "data/observations/upbit_tradeable_universe/2026-08-29/transitions/"
                + "a" * 64 + "-to-" + "b" * 64 + "/packet.json"
            ),
            "record_payload_sha256": "b" * 64,
            "markets": markets,
            "market_count": 1,
            "authority": {
                "evidence_derivation_only": True,
                "order_authorized": False,
            },
        }
        snapshot_key = CAP.P3_P4.snapshot_key(base_lineage)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                CAP.CaptureError,
                "MANIFEST_UNIVERSE_TRANSITION_PROVENANCE_MISSING",
            ):
                CAP.capture_snapshot(
                    Path(tmp), markets=markets, snapshot_date=dt.date(2026, 8, 29),
                    contract=contract, fetcher=fetcher, sleeper=lambda s: None,
                    clock=fixed_clock, snapshot_key=snapshot_key,
                    universe_lineage=base_lineage,
                )

        valid_lineage = dict(base_lineage)
        valid_lineage["transition"] = {
            "manifest_path": "transition.json",
            "manifest_file_sha256": "c" * 64,
            "manifest_payload_sha256": "d" * 64,
            "canonical_source": {
                "path": "canonical.json", "file_sha256": "e" * 64,
                "payload_sha256": "f" * 64,
            },
            "successor": {
                "path": "successor.json", "file_sha256": "1" * 64,
                "payload_sha256": "b" * 64,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = CAP.capture_snapshot(
                Path(tmp), markets=markets, snapshot_date=dt.date(2026, 8, 29),
                contract=contract, fetcher=fetcher, sleeper=lambda s: None,
                clock=fixed_clock, snapshot_key=snapshot_key,
                universe_lineage=valid_lineage,
            )
            manifest = CAP.validate_snapshot(target)
            self.assertEqual(manifest["universe_lineage"]["transition"], valid_lineage["transition"])
            tampered = json.loads((target / "_manifest.json").read_text(encoding="utf-8"))
            del tampered["universe_lineage"]["transition"]["canonical_source"]
            (target / "_manifest.json").write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(
                CAP.CaptureError,
                "MANIFEST_UNIVERSE_TRANSITION_PROVENANCE_INVALID",
            ):
                CAP.validate_snapshot(target)

    def test_capture_writes_hash_bound_manifest_and_validates(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC", "KRW-ETH"]
        fetcher = build_fetcher(contract, markets)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, markets=markets, snapshot_date=dt.date(2026, 8, 29), contract=contract, fetcher=fetcher,
                sleeper=lambda s: None, clock=fixed_clock,
            )
            manifest = CAP.validate_snapshot(target)
            self.assertEqual(manifest["market_count"], 2)
            self.assertEqual(manifest["markets"], markets)
            self.assertFalse(manifest["auth_required"])
            self.assertFalse(manifest["order_or_withdrawal_endpoints_called"])
            for timeframe in contract["timeframes"]:
                file_name = contract["candles_raw_file_template"].format(TIMEFRAME=timeframe)
                self.assertIn(file_name, manifest["checksums"])

    def test_empty_market_list_is_a_successful_append_only_empty_snapshot(self):
        contract = CAP.load_contract()
        fetcher = build_fetcher(contract, [])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, markets=[], snapshot_date=dt.date(2026, 8, 29), contract=contract, fetcher=fetcher,
                sleeper=lambda s: None, clock=fixed_clock,
            )
            manifest = CAP.validate_snapshot(target)
            self.assertEqual(manifest["market_count"], 0)
            self.assertEqual(manifest["markets"], [])

    def test_append_only_violation_preserves_existing_snapshot(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC"]
        fetcher = build_fetcher(contract, markets)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, markets=markets, snapshot_date=dt.date(2026, 8, 29), contract=contract, fetcher=fetcher,
                sleeper=lambda s: None, clock=fixed_clock,
            )
            before = (target / contract["orderbook_raw_file"]).read_bytes()
            with self.assertRaisesRegex(CAP.CaptureError, "APPEND_ONLY_VIOLATION"):
                CAP.capture_snapshot(
                    root, markets=markets, snapshot_date=dt.date(2026, 8, 29), contract=contract, fetcher=fetcher,
                    sleeper=lambda s: None, clock=fixed_clock,
                )
            self.assertEqual((target / contract["orderbook_raw_file"]).read_bytes(), before)

    def test_hash_tamper_is_rejected_by_validate_snapshot(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC"]
        fetcher = build_fetcher(contract, markets)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, markets=markets, snapshot_date=dt.date(2026, 8, 29), contract=contract, fetcher=fetcher,
                sleeper=lambda s: None, clock=fixed_clock,
            )
            tampered = gzip.compress(b'[{"market":"KRW-BTC"}]')
            (target / contract["orderbook_raw_file"]).write_bytes(tampered)
            with self.assertRaisesRegex(CAP.CaptureError, "RAW_FILE_HASH_MISMATCH"):
                CAP.validate_snapshot(target)

    def test_transient_failure_recovers_via_retry_and_capture_still_succeeds(self):
        contract = CAP.load_contract()
        markets = ["KRW-BTC"]
        base_fetcher = build_fetcher(contract, markets)
        state = {"count": 0}

        def flaky_then_ok(url, timeout):
            state["count"] += 1
            if state["count"] == 1:
                raise OSError("transient network blip")
            return base_fetcher(url, timeout)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, markets=markets, snapshot_date=dt.date(2026, 8, 29), contract=contract,
                fetcher=flaky_then_ok, sleeper=lambda s: None, clock=fixed_clock,
            )
            manifest = CAP.validate_snapshot(target)
            self.assertEqual(manifest["market_count"], 1)

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
        contract_text = (ROOT / "config" / "upbit_market_evidence_contract.json").read_text(encoding="utf-8")
        for forbidden in ("/v1/orders", "/v1/withdraws", "/v1/deposits", "Authorization", "api_key", "secret_key", "JWT"):
            self.assertNotIn(forbidden, text)
            self.assertNotIn(forbidden, contract_text)
        contract = CAP.load_contract()
        for endpoint_key in (
            "candles_minutes_endpoint_template", "candles_days_endpoint_template",
            "trades_endpoint_template", "orderbook_endpoint_template",
        ):
            self.assertIn("/v1/", contract[endpoint_key])
            self.assertNotIn("orders", contract[endpoint_key].lower())
            self.assertNotIn("withdraw", contract[endpoint_key].lower())
            self.assertNotIn("deposit", contract[endpoint_key].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
