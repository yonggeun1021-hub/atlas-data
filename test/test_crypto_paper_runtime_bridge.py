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


if __name__ == "__main__":
    unittest.main()
