#!/usr/bin/env python3
"""P9 public-message retention and P5/P9 -> P10 PAPER runtime bridge."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BRIDGE = load("test_crypto_paper_runtime_bridge", ROOT / "shadow" / "crypto_paper_runtime_bridge.py")
CAPTURE = load("test_crypto_paper_runtime_capture", ROOT / ".github" / "scripts" / "upbit_realtime_capture.py")
DECISION = BRIDGE.DECISION
SIMULATOR = BRIDGE.SIMULATOR
SOURCE_COMMIT = "a" * 40


def raw_ticker(*, timestamp=1_788_000_000_000, price=100):
    return {
        "type": "ticker", "code": "KRW-BTC", "opening_price": price,
        "trade_price": price, "timestamp": timestamp, "trade_timestamp": timestamp,
        "trade_volume": 1, "stream_type": "REALTIME",
    }


def raw_orderbook(*, timestamp=1_788_000_000_000, ask=101, bid=99):
    return {
        "type": "orderbook", "code": "KRW-BTC", "timestamp": timestamp,
        "orderbook_units": [{
            "ask_price": ask, "ask_size": 2, "bid_price": bid, "bid_size": 3,
        }],
        "stream_type": "REALTIME",
    }


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix=".crypto_runtime_test_", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def decision(self, *, received_at="2026-08-29T01:30:30.000000Z"):
        latest = {}
        received = dt.datetime.strptime(received_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        for raw in (raw_ticker(), raw_orderbook()):
            parsed = BRIDGE.REALTIME.parse_message(raw)
            CAPTURE.retain_latest_public_message(
                latest,
                raw=raw,
                result={"action": "ACCEPTED", "market": parsed["market"], "kind": parsed["kind"]},
                received_at=received,
            )
        run = {
            "status": {
                "overall_status": "FRESH",
                "markets": [{
                    "market": "KRW-BTC",
                    "freshness_by_kind": {
                        "ticker": {"status": "FRESH"},
                        "orderbook": {"status": "FRESH"},
                    },
                }],
            },
            "latest_public_messages_schema_version": CAPTURE.LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION,
            "latest_public_messages": latest,
        }
        record = {
            "schema_version": "upbit_realtime_capture_run/1",
            "source_sha256": BRIDGE.payload_sha256(run),
            "run": run,
        }
        directory = self.tmp / "realtime" / "2026-08-29"
        directory.mkdir(parents=True)
        path = directory / "run_001.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        entry = {"date": "2026-08-29", "path": path, "record": record}
        return DECISION.build_snapshot(
            generated_at="2026-08-29T01:31:00Z",
            source_commit=SOURCE_COMMIT,
            universe_entry=None,
            market_evidence_entry=None,
            realtime_entry=entry,
        )


class LatestPublicMessageTests(unittest.TestCase):
    def test_only_an_accepted_message_replaces_the_latest_exact_public_payload(self):
        latest = {}
        now = dt.datetime(2026, 8, 29, 1, 30, tzinfo=UTC)
        first = raw_orderbook(ask=101)
        parsed = BRIDGE.REALTIME.parse_message(first)
        CAPTURE.retain_latest_public_message(
            latest, raw=first,
            result={"action": "ACCEPTED", "market": "KRW-BTC", "kind": "orderbook"},
            received_at=now,
        )
        self.assertEqual(latest["orderbook|-|KRW-BTC"]["raw"], first)
        self.assertEqual(latest["orderbook|-|KRW-BTC"]["source_sha256"], parsed["payload_sha256"])

        rejected = raw_orderbook(timestamp=1_788_000_000_001, ask=999)
        CAPTURE.retain_latest_public_message(
            latest, raw=rejected,
            result={"action": "OUT_OF_ORDER_FLAGGED", "market": "KRW-BTC", "kind": "orderbook"},
            received_at=now + dt.timedelta(seconds=1),
        )
        self.assertEqual(latest["orderbook|-|KRW-BTC"]["raw"], first)


class BridgeContractTests(RuntimeFixture):
    def test_decision_is_rederived_and_exact_public_orderbook_becomes_p10_snapshot(self):
        decision = self.decision()
        self.assertEqual(
            BRIDGE.validate_decision_snapshot(decision, expected_source_commit=SOURCE_COMMIT),
            decision,
        )
        snapshot = BRIDGE.orderbook_snapshot(decision, market="KRW-BTC")
        self.assertEqual(snapshot["captured_at"], "2026-08-29T01:30:30Z")
        self.assertEqual(snapshot["ask_levels"], [{"price": "101", "quantity": "2"}])
        self.assertEqual(snapshot["bid_levels"], [{"price": "99", "quantity": "3"}])
        self.assertFalse(snapshot["authority"]["exchange_order_authorized"])

    def test_no_promotion_produces_honest_wait_not_an_order(self):
        request = BRIDGE.build_runtime_request(
            self.decision(), expected_source_commit=SOURCE_COMMIT,
            account_state=None, open_position_risk=None, runtime_config=None,
        )
        self.assertEqual(request["status"], "WAIT_PROMOTION_UNAVAILABLE")
        self.assertEqual(request["requests"], [])
        self.assertEqual(request["match_snapshots"], [])
        self.assertFalse(request["authority"]["exchange_order_authorized"])

    def test_prior_open_order_gets_current_snapshot_while_new_same_run_order_never_can(self):
        ledger = SIMULATOR.create_ledger(
            ledger_id="PAPER.LEDGER.RUNTIME.TEST", initial_cash="1000",
            opened_at="2026-08-29T00:59:00Z", idempotency_key="PAPER.ACCOUNT.OPEN.RUNTIME.TEST",
        )
        intent = SIMULATOR.build_intent(
            order_id="PAPER.BUY.KRW-BTC.RUNTIME.TEST",
            idempotency_key="PAPER.SUBMIT.KRW-BTC.RUNTIME.TEST",
            market="KRW-BTC", side="BUY", order_type="LIMIT", quantity="1",
            limit_price="100", fee_rate="0", queue_fraction="1",
            submitted_at="2026-08-29T01:00:00Z", expires_at="2026-08-29T02:00:00Z",
            market_regime_status="UNKNOWN", source_plan_ref="test://plan/runtime",
            source_plan_sha256="b" * 64, source_evidence_ref="test://book/runtime",
            source_evidence_sha256="c" * 64,
        )
        ledger = SIMULATOR.submit_order(ledger, intent)
        account = SIMULATOR.build_account_state(
            ledger, observed_at="2026-08-29T01:31:00Z", mark_prices={},
            mark_freshness_status="FRESH", mark_source_ref="test://marks/runtime",
            mark_source_sha256="d" * 64,
        )
        request = BRIDGE.build_runtime_request(
            self.decision(), expected_source_commit=SOURCE_COMMIT,
            account_state=account, open_position_risk=None, runtime_config=None,
        )
        self.assertEqual(request["status"], "PAPER_MATCHES_READY")
        self.assertEqual(
            request["match_snapshots"][0]["order_ids"],
            ["PAPER.BUY.KRW-BTC.RUNTIME.TEST"],
        )
        self.assertEqual(request["requests"], [])

    def test_runtime_config_requires_explicit_ratification_hash_and_keeps_real_authority_false(self):
        config = BRIDGE.build_runtime_config(
            approval_status=BRIDGE.RUNTIME_CONFIG_APPROVAL,
            approved_by="CIO_TEST", approved_at="2026-08-29T01:00:00Z",
            ledger_id="PAPER.LEDGER.RUNTIME.TEST", initial_cash_krw="1000",
            fee_rate="0", queue_fraction="1", order_type="LIMIT",
            limit_price_source="ENTRY_ZONE_LOW",
        )
        self.assertEqual(config["fee_rate"], "0")
        self.assertFalse(config["authority"]["exchange_order_authorized"])
        tampered = copy.deepcopy(config)
        tampered["initial_cash_krw"] = "2000"
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_CONFIG_SHA_MISMATCH"):
            BRIDGE.validate_runtime_config(tampered)

    def test_source_file_tamper_is_rejected_before_any_runtime_request(self):
        decision = self.decision()
        source = ROOT / decision["source_refs"][0]["path"]
        record = json.loads(source.read_text(encoding="utf-8"))
        record["run"]["latest_public_messages"]["orderbook|-|KRW-BTC"]["raw"]["orderbook_units"][0]["ask_price"] = 999
        source.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "DECISION_SOURCE_SHA_MISMATCH"):
            BRIDGE.validate_decision_snapshot(decision, expected_source_commit=SOURCE_COMMIT)


if __name__ == "__main__":
    unittest.main()
