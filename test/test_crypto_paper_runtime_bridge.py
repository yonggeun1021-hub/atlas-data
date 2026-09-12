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
from unittest import mock


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
        policy_patch = mock.patch.object(
            DECISION.REALTIME_GATE,
            "load_freshness_policy_proposal",
            return_value={"approval_status": "RATIFIED"},
        )
        policy_patch.start()
        self.addCleanup(policy_patch.stop)

    def test_ratified_market_evidence_uses_exact_ratified_policy_pin(self):
        market_evidence = BRIDGE.PROMOTION.MARKET_EVIDENCE
        policy = market_evidence.load_ratified_policy()
        as_of = dt.datetime(2026, 8, 30, 1, 0, 0, tzinfo=UTC)
        captured_at = dt.datetime(2026, 8, 30, 1, 5, 0, tzinfo=UTC)
        raw_candle = {
            "candle_date_time_utc": "2026-08-29T00:00:00",
            "opening_price": 1000,
            "high_price": 1010,
            "low_price": 990,
            "trade_price": 1005,
            "candle_acc_trade_price": 123456,
            "candle_acc_trade_volume": 12.3,
        }
        candles = {
            timeframe: [copy.deepcopy(raw_candle)]
            for timeframe in market_evidence.finalization.TIMEFRAMES
        }
        timestamp_ms = int(as_of.timestamp() * 1000)
        packet = market_evidence.build_market_evidence_packet(
            "KRW-BTC",
            candles_by_timeframe=candles,
            trades=[{
                "market": "KRW-BTC",
                "trade_price": 1000,
                "trade_volume": 1,
                "timestamp": timestamp_ms,
                "ask_bid": "BID",
            }],
            orderbook_row={
                "market": "KRW-BTC",
                "timestamp": timestamp_ms,
                "orderbook_units": [{
                    "bid_price": 999,
                    "bid_size": 10000,
                    "ask_price": 1001,
                    "ask_size": 10000,
                }],
            },
            as_of=as_of,
            captured_at=captured_at,
            policy=policy,
        )

        checked = BRIDGE.PROMOTION._validate_market_evidence_packet(
            packet, "KRW-BTC", "2026-08-30"
        )

        self.assertTrue(checked["policy_ratified"])
        self.assertEqual(checked["policy_version"], policy["policy_version"])

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
        status = {
            "schema_version": "upbit_realtime_gate_status/1",
            "generated_at": "2026-08-29T01:30:31Z",
            "connection_state": "CONNECTED",
            "reconnect_count": 0,
            "last_disconnect_reason": None,
            "overall_status": "FRESH",
            "counts": {"accepted": 2},
            "markets": [{
                "market": "KRW-BTC",
                "freshness_by_kind": {
                    "ticker": {"status": "FRESH"},
                    "orderbook": {"status": "FRESH"},
                },
            }],
            "pending_connection_gap_windows": [],
            "duplicate_guard_size": 2,
            "authority": dict(DECISION.REALTIME_GATE._GATE_AUTHORITY),
        }
        status["payload_sha256"] = BRIDGE.payload_sha256(status)
        run = {
            "started_at": "2026-08-29T01:30:00Z",
            "ended_at": "2026-08-29T01:30:31Z",
            "requested_duration_seconds": 31,
            "markets": ["KRW-BTC"],
            "message_log": [{
                "received_at": received_at,
                "result": {"action": "ACCEPTED", "market": "KRW-BTC"},
            }],
            "status": status,
            "candle_ledger": {},
            "latest_public_messages_schema_version": CAPTURE.LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION,
            "latest_public_messages": latest,
        }
        record = {
            "schema_version": "upbit_realtime_capture_run/1",
            "transform_version": "upbit_realtime_gate/1",
            "auth_required": False,
            "order_or_withdrawal_endpoints_called": False,
            "private_channel_subscribed": False,
            "run": run,
        }
        record["source_sha256"] = BRIDGE.payload_sha256(run)
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

    def account_with_open_order(
        self, *, submitted_at="2026-08-29T01:00:00Z",
        expires_at="2026-08-29T02:00:00Z",
    ):
        ledger = SIMULATOR.create_ledger(
            ledger_id="PAPER.LEDGER.RUNTIME.TEST", initial_cash="1000",
            opened_at="2026-08-29T00:59:00Z",
            idempotency_key="PAPER.ACCOUNT.OPEN.RUNTIME.TEST",
        )
        intent = SIMULATOR.build_intent(
            order_id="PAPER.BUY.KRW-BTC.RUNTIME.TEST",
            idempotency_key="PAPER.SUBMIT.KRW-BTC.RUNTIME.TEST",
            market="KRW-BTC", side="BUY", order_type="LIMIT", quantity="1",
            limit_price="100", fee_rate="0", queue_fraction="1",
            submitted_at=submitted_at, expires_at=expires_at,
            market_regime_status="UNKNOWN", source_plan_ref="test://plan/runtime",
            source_plan_sha256="b" * 64, source_evidence_ref="test://book/runtime",
            source_evidence_sha256="c" * 64,
        )
        ledger = SIMULATOR.submit_order(ledger, intent)
        return SIMULATOR.build_account_state(
            ledger, observed_at="2026-08-29T01:31:00Z", mark_prices={},
            mark_freshness_status="FRESH", mark_source_ref="test://marks/runtime",
            mark_source_sha256="d" * 64,
        )

    def empty_account(self):
        ledger = SIMULATOR.create_ledger(
            ledger_id="PAPER.LEDGER.RUNTIME.TEST", initial_cash="1000",
            opened_at="2026-08-29T00:59:00Z",
            idempotency_key="PAPER.ACCOUNT.OPEN.RUNTIME.TEST",
        )
        return SIMULATOR.build_account_state(
            ledger, observed_at="2026-08-29T01:31:00Z", mark_prices={},
            mark_freshness_status="FRESH", mark_source_ref="test://marks/runtime",
            mark_source_sha256="d" * 64,
        )

    def config(self):
        return BRIDGE.build_runtime_config(
            approval_status=BRIDGE.RUNTIME_CONFIG_APPROVAL,
            approved_by="CIO_TEST", approved_at="2026-08-29T01:00:00Z",
            ledger_id="PAPER.LEDGER.RUNTIME.TEST", initial_cash_krw="1000",
            fee_rate="0", queue_fraction="1", order_type="LIMIT",
            limit_price_source="ENTRY_ZONE_LOW",
        )

    def separate_observation(self, decision):
        root = Path(tempfile.mkdtemp(prefix="crypto_runtime_observation_"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for ref in decision["source_refs"]:
            source = ROOT / ref["path"]
            target = root / ref["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        packet_path = root / "evidence" / "crypto_paper_decision" / "packet.json"
        packet_path.parent.mkdir(parents=True, exist_ok=True)
        packet_path.write_text(json.dumps(decision), encoding="utf-8")
        return root, packet_path

    @staticmethod
    def eligible_packet(markets):
        return {
            "candidates": [{
                "market": market,
                "eligibility_state": "PAPER_BUY_ELIGIBLE",
                "order_draft": {},
            } for market in markets],
        }

    @staticmethod
    def promotion_packet():
        return {
            "evaluation_as_of": "2026-08-29T01:31:00Z",
            "source_packets": {"regime": {"regime": "UNKNOWN"}},
        }

    def stage5_fixture_envelope(self, *, source_path=None):
        source_path = source_path or (
            ROOT / "test" / "fixtures" / "stage5_paper_stage4_lineage_fixture.json"
        )
        source_ref = str(source_path.relative_to(ROOT))
        source_sha = BRIDGE._file_sha256(source_path)
        contract = BRIDGE.STAGE5.load_contract()
        decision = {
            "schema_version": contract["input_schema_version"],
            "decision_id": "STAGE4.FIXTURE.CRYPTO.20260912",
            "status": "NOT_EVALUATED",
            "candle_open_at": "2026-09-12T12:54:00Z",
            "candle_closed_at": "2026-09-12T12:55:00Z",
            "available_at": "2026-09-12T13:26:25Z",
            "decided_at": "2026-09-12T13:26:25Z",
            "source_ref": source_ref,
            "source_sha256": source_sha,
            "authority": copy.deepcopy(contract["authority"]),
        }
        decision["packet_sha256"] = BRIDGE.STAGE5.payload_sha256(decision)
        snapshot = BRIDGE.STAGE5.SIMULATOR.build_snapshot(
            snapshot_id="STAGE5.FIXTURE.SNAPSHOT.CRYPTO.1",
            market="KRW-BTC",
            captured_at="2026-09-12T13:27:00Z",
            freshness_status="FRESH",
            ask_levels=[{"price": "100", "quantity": "2"}],
            bid_levels=[{"price": "99", "quantity": "2"}],
            source_ref="fixture://stage5/orderbook/crypto/1",
            source_sha256="b" * 64,
        )
        envelope = {
            "schema_version": contract["input_schema_version"],
            "contract_version": contract["contract_version"],
            "mode": contract["mode"],
            "envelope_id": "STAGE5.FIXTURE.ENVELOPE.CRYPTO.1",
            "decision": decision,
            "plan": {
                "plan_id": "STAGE5.FIXTURE.PLAN.CRYPTO.1",
                "ledger_id": "STAGE5.FIXTURE.LEDGER.CRYPTO.1",
                "initial_cash": "1000",
                "opened_at": "2026-09-12T13:26:25Z",
                "opening_idempotency_key": "STAGE5.FIXTURE.OPEN.CRYPTO.1",
                "order_id": "STAGE5.FIXTURE.ORDER.CRYPTO.1",
                "submit_idempotency_key": "STAGE5.FIXTURE.SUBMIT.CRYPTO.1",
                "match_idempotency_key": "STAGE5.FIXTURE.MATCH.CRYPTO.1",
                "side": "BUY",
                "order_type": "MARKET",
                "quantity": "1",
                "limit_price": None,
                "fee_rate": "0",
                "queue_fraction": "1",
                "submitted_at": "2026-09-12T13:26:26Z",
                "expires_at": "2026-09-12T13:31:00Z",
                "match_at": "2026-09-12T13:27:01Z",
                "mark_price": "101",
                "account_observed_at": "2026-09-12T13:27:02Z",
            },
            "snapshot": snapshot,
            "authority": copy.deepcopy(contract["authority"]),
        }
        envelope["packet_sha256"] = BRIDGE.STAGE5.payload_sha256(envelope)
        pins = {
            "expected_envelope_sha256": envelope["packet_sha256"],
            "expected_decision_packet_sha256": decision["packet_sha256"],
            "expected_decision_source_sha256": source_sha,
        }
        return envelope, pins


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

    def test_public_bridge_has_no_network_or_execution_endpoint(self):
        source = (ROOT / "shadow" / "crypto_paper_runtime_bridge.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "import requests", "from requests", "urllib.request", "websockets",
            "import socket", "socket.socket(",
            "requests.get(", "requests.post(", "requests.request(",
            "/v1/orders", "/v1/withdraws", "API_KEY", "SECRET_KEY",
        ):
            self.assertNotIn(forbidden, source)


class RollingObservationRootTests(unittest.TestCase):
    def test_stablecoin_evidence_only_in_observation_root_rederives(self):
        seed_packet_path = (
            ROOT
            / "evidence"
            / "crypto_paper_decision"
            / "2026-08-30"
            / "0719"
            / "0c42f5337058dcbfde6f21b6742c3cfefefe87c46891e1b90895505ffe643f51"
            / "packet.json"
        )
        seed = json.loads(seed_packet_path.read_text(encoding="utf-8"))

        observation_root = Path(tempfile.mkdtemp(prefix="crypto_runtime_stablecoin_"))
        self.addCleanup(shutil.rmtree, observation_root, ignore_errors=True)
        for source_row in seed["source_components"]["source_directories"]:
            source = ROOT / source_row["path"]
            target = observation_root / source_row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)

        generated_at = seed["generated_at"]
        component_registry = DECISION.LIVE_COMPONENT_REGISTRY.build_registry(
            generated_at, root=observation_root
        )
        with (
            mock.patch.object(DECISION, "ROOT", observation_root),
            mock.patch.object(DECISION.LIVE_AXIS, "ROOT", observation_root),
        ):
            decision = DECISION.build_snapshot(
                generated_at=generated_at,
                source_commit=seed["source_commit"],
                universe_entry=None,
                market_evidence_entry=None,
                realtime_entry=None,
                component_rows=component_registry,
            )
        self.assertEqual(
            decision["crypto_regime_five_axis"]["LIQUIDITY"]["status"],
            "DEFINED",
        )
        stablecoin_row = decision["source_components"]["rows"][
            "STABLECOIN_NET_ISSUANCE"
        ]
        self.assertEqual(stablecoin_row["status"], "READY")

        copied_packet = (
            observation_root / "evidence" / "crypto_paper_decision" / "packet.json"
        )
        copied_packet.parent.mkdir(parents=True, exist_ok=True)
        copied_packet.write_text(json.dumps(decision), encoding="utf-8")

        code_only_root = Path(tempfile.mkdtemp(prefix="crypto_runtime_code_only_"))
        self.addCleanup(shutil.rmtree, code_only_root, ignore_errors=True)
        self.assertFalse(
            (code_only_root / "evidence" / "stablecoin" / "raw").exists()
        )
        with mock.patch.object(DECISION.LIVE_AXIS, "ROOT", code_only_root):
            validator = BRIDGE._decision_validator(observation_root)
            self.assertEqual(
                Path(validator.LIVE_AXIS.__file__).resolve(),
                (ROOT / "regime" / "live_axis_adapter.py").resolve(),
            )
            self.assertEqual(
                validator.LIVE_AXIS.ROOT.resolve(), observation_root.resolve()
            )
            checked = BRIDGE.load_and_validate_decision_snapshot(
                copied_packet,
                expected_source_commit=decision["source_commit"],
                observation_root=observation_root,
            )

        self.assertEqual(checked, decision)
        self.assertTrue(checked["authority"])
        self.assertTrue(
            all(value is False for value in checked["authority"].values())
        )
        self.assertEqual(
            checked["crypto_regime_five_axis"]["LIQUIDITY"]["status"],
            "DEFINED",
        )


class BridgeContractTests(RuntimeFixture):
    def test_exact_key_market_source_uses_embedded_operational_date(self):
        universe_path = self.tmp / "universe" / "2026-08-29" / "packet.json"
        universe_path.parent.mkdir(parents=True)
        universe_path.write_text(
            json.dumps({"packet": {"evaluation_as_of": "2026-08-29T01:00:00Z"}}),
            encoding="utf-8",
        )
        market_path = (
            self.tmp / "market" / "2026-08-29-p3-ffffffffffffffff" / "packet.json"
        )
        market_path.parent.mkdir(parents=True)
        market_path.write_text(
            json.dumps({"snapshot_date": "2026-08-29", "packets": {}}),
            encoding="utf-8",
        )
        packet = {
            "source_refs": [
                {
                    "role": "upbit_tradeable_universe_packet",
                    "path": str(universe_path.relative_to(ROOT)),
                    "sha256": BRIDGE._file_sha256(universe_path),
                },
                {
                    "role": "upbit_market_evidence_packet",
                    "path": str(market_path.relative_to(ROOT)),
                    "sha256": BRIDGE._file_sha256(market_path),
                },
            ]
        }

        entries = BRIDGE._entries(packet)

        self.assertEqual(entries["universe"]["date"], "2026-08-29")
        self.assertEqual(entries["market_evidence"]["date"], "2026-08-29")

    def test_approved_code_rederives_a_later_separate_observation_checkout(self):
        decision = self.decision()
        observation_root, packet_path = self.separate_observation(decision)
        shutil.rmtree(self.tmp / "realtime")

        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "DECISION_REDERIVATION_FAILED:SOURCE_REF_FILE_INVALID",
        ):
            BRIDGE.validate_decision_snapshot(decision)

        checked = BRIDGE.load_and_validate_decision_snapshot(
            packet_path,
            expected_source_commit=SOURCE_COMMIT,
            observation_root=observation_root,
        )
        self.assertEqual(checked, decision)
        request = BRIDGE.build_runtime_request(
            checked,
            expected_source_commit=SOURCE_COMMIT,
            public_code_commit_sha="b" * 40,
            observation_root=observation_root,
            observation_commit_sha="c" * 40,
            account_state=None,
            open_position_risk=None,
            runtime_config=None,
        )
        self.assertEqual(request["observation_commit_sha"], "c" * 40)
        self.assertEqual(
            request["source_inputs"]["observation_root"],
            str(observation_root.resolve()),
        )
        self.assertEqual(
            BRIDGE.validate_runtime_request(
                request,
                expected_public_code_commit_sha="b" * 40,
                expected_observation_root=observation_root,
                expected_observation_commit_sha="c" * 40,
            ),
            request,
        )

    def test_observation_root_or_commit_rewrite_is_rejected(self):
        decision = self.decision()
        observation_root, _packet_path = self.separate_observation(decision)
        request = BRIDGE.build_runtime_request(
            decision,
            expected_source_commit=SOURCE_COMMIT,
            public_code_commit_sha="b" * 40,
            observation_root=observation_root,
            observation_commit_sha="c" * 40,
            account_state=None,
            open_position_risk=None,
            runtime_config=None,
        )
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "RUNTIME_REQUEST_OBSERVATION_COMMIT_MISMATCH",
        ):
            BRIDGE.validate_runtime_request(
                request, expected_observation_commit_sha="d" * 40,
            )
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "RUNTIME_REQUEST_OBSERVATION_ROOT_MISMATCH",
        ):
            BRIDGE.validate_runtime_request(
                request, expected_observation_root=ROOT,
            )

    def test_symlinked_observation_root_is_rejected(self):
        decision = self.decision()
        observation_root, _packet_path = self.separate_observation(decision)
        link = observation_root.parent / (observation_root.name + "_link")
        link.symlink_to(observation_root, target_is_directory=True)
        self.addCleanup(link.unlink, missing_ok=True)
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "OBSERVATION_ROOT_INVALID",
        ):
            BRIDGE.validate_decision_snapshot(decision, observation_root=link)

    def test_symlinked_observation_source_or_decision_packet_is_rejected(self):
        decision = self.decision()
        observation_root, packet_path = self.separate_observation(decision)
        source_path = observation_root / decision["source_refs"][0]["path"]
        source_copy = source_path.with_suffix(".real.json")
        source_path.rename(source_copy)
        source_path.symlink_to(source_copy)
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "DECISION_REDERIVATION_FAILED:SOURCE_REF_FILE_INVALID",
        ):
            BRIDGE.validate_decision_snapshot(
                decision, observation_root=observation_root,
            )

        source_path.unlink()
        source_copy.rename(source_path)
        packet_copy = packet_path.with_suffix(".real.json")
        packet_path.rename(packet_copy)
        packet_path.symlink_to(packet_copy)
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError, "DECISION_PATH_INVALID",
        ):
            BRIDGE.load_and_validate_decision_snapshot(
                packet_path, observation_root=observation_root,
            )

    def test_observation_source_path_escape_is_rejected(self):
        decision = self.decision()
        observation_root, _packet_path = self.separate_observation(decision)
        forged = copy.deepcopy(decision)
        forged["source_refs"][0]["path"] = "../outside.json"
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "DECISION_REDERIVATION_FAILED:SOURCE_REF_PATH_ESCAPE",
        ):
            BRIDGE.validate_decision_snapshot(
                forged, observation_root=observation_root,
            )

    def test_rehashed_observation_lineage_tamper_needs_exact_external_identity(self):
        decision = self.decision()
        observation_root, _packet_path = self.separate_observation(decision)
        request = BRIDGE.build_runtime_request(
            decision,
            expected_source_commit=SOURCE_COMMIT,
            public_code_commit_sha="b" * 40,
            observation_root=observation_root,
            observation_commit_sha="c" * 40,
            account_state=None,
            open_position_risk=None,
            runtime_config=None,
        )
        forged = copy.deepcopy(request)
        forged["observation_commit_sha"] = "d" * 40
        forged["source_inputs"]["observation_commit_sha"] = "d" * 40
        forged["packet_sha256"] = BRIDGE.payload_sha256(
            {key: value for key, value in forged.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "RUNTIME_REQUEST_OBSERVATION_COMMIT_MISMATCH",
        ):
            BRIDGE.validate_runtime_request(
                forged, expected_observation_commit_sha="c" * 40,
            )

    def test_first_natural_post_merge_decision_is_frozen_as_old_identity_lineage(self):
        paths = sorted((ROOT / "evidence" / "crypto_paper_decision" / "2026-08-29" / "0504").glob(
            "*/packet.json"
        ))
        self.assertEqual(len(paths), 1)
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "DECISION_REDERIVATION_FAILED:OUTPUT_DERIVATION_MISMATCH",
        ):
            BRIDGE.load_and_validate_decision_snapshot(
                paths[0],
                expected_source_commit="ba11308e96fa926395e87d37ded8b726d46a3872",
            )

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

    def test_stage4_fixture_calls_stage5_and_preserves_exact_upstream_lineage(self):
        envelope, pins = self.stage5_fixture_envelope()
        with mock.patch.object(
            BRIDGE.DECISION,
            "build_regime_snapshot",
            side_effect=AssertionError("Stage5 fixture path must not read raw regime"),
        ):
            receipt = BRIDGE.build_stage5_fixture_connection(envelope, **pins)

        self.assertEqual(
            receipt["mode"], "MOCK_PATH_VERIFIED_NOT_PAPER_EXECUTION"
        )
        self.assertEqual(
            receipt["execution_state"], "NOT_EXECUTED_FIXTURE_RESULT_ONLY"
        )
        lineage = receipt["stage4_lineage"]
        source = lineage["source_record"]
        self.assertEqual(lineage["decision_status"], "NOT_EVALUATED")
        self.assertEqual(lineage["evaluated_at"], "2026-09-12T13:26:25Z")
        self.assertEqual(source["market_regime"]["candidate_regime"], "NEUTRAL")
        self.assertEqual(source["market_regime"]["runtime_regime"], "UNKNOWN")
        self.assertEqual(
            source["market_regime"]["source_identity"]["generation_id"],
            "70176ad3877836620cf7403ddc30af7f2275a657df111c950a2338c1c87961af",
        )
        self.assertEqual(
            source["market_regime"]["source_identity"]["payload_sha256"],
            "735fab39899e0963fed4d91bb56e1fa2adcc796e78034499e54371d20b7b0bed",
        )
        self.assertEqual(
            source["candidate"]["rejection_reasons"],
            [
                "STAGE3_PACKET_MISSING_STAGE1_REGIME_LINEAGE",
                "P7_15_THREE_MARKET_REGIME_NOT_PROVEN_TO_STAGE1_LINEAGE",
                "NUMERIC_POLICY_UNRATIFIED",
                "POSITION_AND_EXIT_POLICY_NOT_SUPPLIED",
            ],
        )
        result = receipt["stage5_result"]
        self.assertEqual(result["virtual_plan"]["market_regime_status"], "NOT_EVALUATED")
        self.assertEqual(result["ledger"]["ledger_id"], "STAGE5.FIXTURE.LEDGER.CRYPTO.1")
        self.assertEqual(result["source"]["decision_source_sha256"], lineage["source_sha256"])
        self.assertTrue(all(value is False for value in receipt["authority"].values()))

    def test_stage5_fixture_source_reason_or_receipt_rehash_cannot_change_lineage(self):
        original = ROOT / "test" / "fixtures" / "stage5_paper_stage4_lineage_fixture.json"
        copied = self.tmp / "stage4" / "decision.json"
        copied.parent.mkdir(parents=True)
        shutil.copy2(original, copied)
        envelope, pins = self.stage5_fixture_envelope(source_path=copied)
        receipt = BRIDGE.build_stage5_fixture_connection(envelope, **pins)

        changed = json.loads(copied.read_text(encoding="utf-8"))
        changed["candidate"]["rejection_reasons"] = []
        copied.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "STAGE5_DECISION_SOURCE_SHA_MISMATCH",
        ):
            BRIDGE.build_stage5_fixture_connection(envelope, **pins)

        shutil.copy2(original, copied)
        forged = copy.deepcopy(receipt)
        forged["stage4_lineage"]["source_record"]["candidate"][
            "rejection_reasons"
        ] = []
        forged["packet_sha256"] = BRIDGE.payload_sha256(
            {key: value for key, value in forged.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "STAGE5_CONNECTION_DERIVATION_MISMATCH",
        ):
            BRIDGE.validate_stage5_fixture_connection(forged)

    def test_stage5_fixture_cannot_use_existing_runtime_account_namespace(self):
        envelope, pins = self.stage5_fixture_envelope()
        envelope["plan"]["ledger_id"] = "PAPER.LEDGER.RUNTIME.TEST"
        envelope["packet_sha256"] = BRIDGE.STAGE5.payload_sha256(
            {key: value for key, value in envelope.items() if key != "packet_sha256"}
        )
        pins["expected_envelope_sha256"] = envelope["packet_sha256"]
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "STAGE5_FIXTURE_LEDGER_NAMESPACE_INVALID",
        ):
            BRIDGE.build_stage5_fixture_connection(envelope, **pins)

    def test_prior_open_order_gets_current_snapshot_while_new_same_run_order_never_can(self):
        request = BRIDGE.build_runtime_request(
            self.decision(), expected_source_commit=SOURCE_COMMIT,
            account_state=self.account_with_open_order(),
            open_position_risk=None, runtime_config=None,
        )
        self.assertEqual(request["status"], "PAPER_MATCHES_READY")
        self.assertEqual(
            request["match_snapshots"][0]["order_ids"],
            ["PAPER.BUY.KRW-BTC.RUNTIME.TEST"],
        )
        self.assertEqual(request["requests"], [])

    def test_equal_timestamp_is_not_a_later_match_snapshot(self):
        request = BRIDGE.build_runtime_request(
            self.decision(received_at="2026-08-29T01:30:30.000000Z"),
            expected_source_commit=SOURCE_COMMIT,
            account_state=self.account_with_open_order(
                submitted_at="2026-08-29T01:30:30Z",
            ),
            open_position_risk=None,
            runtime_config=None,
        )
        self.assertEqual(request["match_snapshots"], [])
        self.assertIn(
            "MATCH_SNAPSHOT_NOT_AFTER_OPEN_ORDER:PAPER.BUY.KRW-BTC.RUNTIME.TEST",
            request["blockers"],
        )

    def test_unratified_realtime_freshness_cannot_match_virtual_order(self):
        with mock.patch.object(
            DECISION.REALTIME_GATE,
            "load_freshness_policy_proposal",
            return_value={"approval_status": "PROPOSED_UNRATIFIED"},
        ):
            decision = self.decision()
            self.assertEqual(decision["freshness_status"]["realtime"], "UNKNOWN")
            request = BRIDGE.build_runtime_request(
                decision,
                expected_source_commit=SOURCE_COMMIT,
                account_state=self.account_with_open_order(),
                open_position_risk=None,
                runtime_config=None,
            )
        self.assertEqual(request["match_snapshots"], [])
        self.assertTrue(any(
            "DECISION_REALTIME_FRESHNESS_NOT_RATIFIED_FRESH" in blocker
            for blocker in request["blockers"]
        ))

    def test_rehashed_runtime_request_tamper_fails_full_rederivation(self):
        request = BRIDGE.build_runtime_request(
            self.decision(), expected_source_commit=SOURCE_COMMIT,
            account_state=None, open_position_risk=None, runtime_config=None,
        )
        forged = copy.deepcopy(request)
        forged["status"] = "PAPER_MATCHES_READY"
        forged["match_snapshots"] = [{
            "market": "KRW-BTC",
            "order_ids": ["FORGED.ORDER"],
            "snapshot": BRIDGE.orderbook_snapshot(
                forged["source_inputs"]["decision"], market="KRW-BTC",
            ),
        }]
        forged["packet_sha256"] = BRIDGE.payload_sha256(
            {key: value for key, value in forged.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "RUNTIME_REQUEST_DERIVATION_MISMATCH",
        ):
            BRIDGE.validate_runtime_request(forged)

    def test_future_runtime_ratification_cannot_apply_to_past_decision(self):
        config = BRIDGE.build_runtime_config(
            approval_status=BRIDGE.RUNTIME_CONFIG_APPROVAL,
            approved_by="CIO_TEST", approved_at="2026-08-29T01:32:00Z",
            ledger_id="PAPER.LEDGER.RUNTIME.TEST", initial_cash_krw="1000",
            fee_rate="0", queue_fraction="1", order_type="LIMIT",
            limit_price_source="ENTRY_ZONE_LOW",
        )
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "RUNTIME_CONFIG_APPROVED_AFTER_DECISION",
        ):
            BRIDGE.build_runtime_request(
                self.decision(), expected_source_commit=SOURCE_COMMIT,
                account_state=None, open_position_risk=None,
                runtime_config=config,
            )

    def test_future_retained_public_message_cannot_be_used_as_decision_evidence(self):
        decision = self.decision(received_at="2026-08-29T01:31:01.000000Z")
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "REALTIME_ORDERBOOK_FUTURE_DATED:KRW-BTC",
        ):
            BRIDGE.orderbook_snapshot(decision, market="KRW-BTC")

    def test_open_position_planned_loss_must_be_strictly_positive(self):
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "OPEN_POSITION_PLANNED_LOSS_INVALID",
        ):
            BRIDGE._normalize_open_position_risk([
                {"market": "KRW-BTC", "planned_loss_krw": "0"},
            ])

    def test_multiple_eligible_candidates_wait_for_allocation_policy(self):
        with (
            mock.patch.object(
                BRIDGE, "_promotion_packet", return_value=self.promotion_packet(),
            ),
            mock.patch.object(
                BRIDGE.ELIGIBILITY, "build_eligibility_packet",
                return_value=self.eligible_packet(["KRW-BTC", "KRW-ETH"]),
            ),
            mock.patch.object(
                BRIDGE.ELIGIBILITY, "validate_output", side_effect=lambda value: value,
            ),
        ):
            request = BRIDGE.build_runtime_request(
                self.decision(), expected_source_commit=SOURCE_COMMIT,
                account_state=self.empty_account(), open_position_risk=[],
                runtime_config=self.config(),
            )
        self.assertEqual(request["status"], "WAIT_ALLOCATION_POLICY")
        self.assertEqual(request["requests"], [])
        self.assertIn(
            "MULTIPLE_ELIGIBLE_CANDIDATES_REQUIRE_ALLOCATION_POLICY:KRW-BTC,KRW-ETH",
            request["blockers"],
        )

    def test_pending_open_order_reserves_capacity_against_new_intent(self):
        with (
            mock.patch.object(
                BRIDGE, "_promotion_packet", return_value=self.promotion_packet(),
            ),
            mock.patch.object(
                BRIDGE.ELIGIBILITY, "build_eligibility_packet",
                return_value=self.eligible_packet(["KRW-ETH"]),
            ),
            mock.patch.object(
                BRIDGE.ELIGIBILITY, "validate_output", side_effect=lambda value: value,
            ),
        ):
            request = BRIDGE.build_runtime_request(
                self.decision(), expected_source_commit=SOURCE_COMMIT,
                account_state=self.account_with_open_order(), open_position_risk=[],
                runtime_config=self.config(),
            )
        self.assertEqual(request["status"], "PAPER_MATCHES_READY")
        self.assertEqual(request["requests"], [])
        self.assertIn(
            "NEW_INTENT_BLOCKED_PENDING_OPEN_ORDERS:"
            "PAPER.BUY.KRW-BTC.RUNTIME.TEST",
            request["blockers"],
        )

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
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError,
            "DECISION_REDERIVATION_FAILED:SOURCE_REF_HASH_MISMATCH",
        ):
            BRIDGE.validate_decision_snapshot(decision, expected_source_commit=SOURCE_COMMIT)



class LeadershipObservationRootTests(unittest.TestCase):
    def test_observation_manifest_root_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'evidence/crypto/breadth/raw/2000-01-01/_manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"fixture":"observation-only"}')
            validator = BRIDGE._decision_validator(root)
            record = {'schema_version': 2, 'market': 'CRYPTO',
                      'as_of_date': '2000-01-01', 'status': 'PARTIAL',
                      'lineage': {'manifest_sha256_by_date': [{
                          'as_of_date': '2000-01-01',
                          'manifest_sha256': BRIDGE._file_sha256(manifest)}]}}
            entry = {'date': '2000-01-01', 'record': record}
            validator._validate_leadership_entry(entry)
            manifest.write_text('{"fixture":"tampered"}')
            with self.assertRaisesRegex(validator.CryptoPaperDecisionSnapshotError,
                                        'LEADERSHIP_LINEAGE_MANIFEST_NOT_NATURAL'):
                validator._validate_leadership_entry(entry)

    def test_full_rederivation_preserves_hash_and_role_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_root = root / 'evidence/crypto/breadth/raw'
            manifest = raw_root / '2026-08-28/_manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"fixture":"no-order"}')
            record = {'schema_version':2, 'market':'CRYPTO',
                      'as_of_date':'2026-08-28', 'status':'UNKNOWN',
                      'lineage':{'manifest_sha256_by_date':[{
                          'as_of_date':'2026-08-28',
                          'manifest_sha256':BRIDGE._file_sha256(manifest)}]}}
            path = root / 'data/observations/crypto_leadership/2026-08-28/packet.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(record))
            with mock.patch.object(DECISION,'ROOT',root), mock.patch.object(DECISION,'CRYPTO_BREADTH_RAW_ROOT',raw_root):
                packet = DECISION.build_snapshot(
                    generated_at='2026-08-29T01:31:00Z', source_commit=SOURCE_COMMIT,
                    universe_entry=None,market_evidence_entry=None,realtime_entry=None,
                    leadership_entry={'date':'2026-08-28','path':path,'record':record})
            self.assertEqual(BRIDGE.validate_decision_snapshot(packet,observation_root=root),packet)
            self.assertTrue(all(x is False for x in packet['authority'].values()))
            for role in ['crypto_leadership_packet','invented_role']:
                forged=copy.deepcopy(packet)
                extra=copy.deepcopy(forged['source_refs'][0]);extra['role']=role
                forged['source_refs'].append(extra)
                forged['payload_sha256']=BRIDGE.payload_sha256({k:v for k,v in forged.items() if k!='payload_sha256'})
                with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError,'SOURCE_REF_ROLE_INVALID'):
                    BRIDGE.validate_decision_snapshot(forged,observation_root=root)
            path.write_text(json.dumps({**record,'status':'PARTIAL'}))
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError,'SOURCE_REF_HASH_MISMATCH'):
                BRIDGE.validate_decision_snapshot(packet,observation_root=root)
            path.write_text(json.dumps(record))
            outside=root/'outside.json';outside.write_bytes(manifest.read_bytes())
            manifest.unlink();manifest.symlink_to(outside)
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError,'LEADERSHIP_LINEAGE_PATH_SYMLINK'):
                BRIDGE.validate_decision_snapshot(packet,observation_root=root)


if __name__ == "__main__":
    unittest.main()
