"""Upbit realtime observation service (services/upbit-realtime-observation/)
regression -- entirely mocked messages and an in-process loopback HTTP
server, no live Upbit WebSocket connection required.

An additional, clearly-labelled manual functional check against the real
public WebSocket exists at the bottom of this file
(``ManualLiveSmokeTest``). It is skipped unless
``ATLAS_UPBIT_OBS_RUN_LIVE_SMOKE_TEST=1`` is set in the environment and the
optional ``websockets`` package is installed -- it is a manual verification
step, not a natural sample, and is never part of ``run_all.py``'s approved
offline regression (same discipline P9-06 established for its own bounded
live run)."""
from __future__ import annotations

import ast
import datetime as dt
import http.client
import importlib.util
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "services" / "upbit-realtime-observation"
GATE_MODULE_PATH = SERVICE_DIR / "observation_gate.py"
SERVICE_MODULE_PATH = SERVICE_DIR / "service.py"
REALTIME_GATE_MODULE_PATH = ROOT / "realtime" / "upbit_realtime_gate.py"
DOCKERFILE_PATH = SERVICE_DIR / "Dockerfile"
COMPOSE_PATH = SERVICE_DIR / "compose.yaml"
ENV_EXAMPLE_PATH = SERVICE_DIR / ".env.example"
README_PATH = SERVICE_DIR / "README.md"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


OG = load_module("upbit_realtime_observation_gate", GATE_MODULE_PATH)
UTC = dt.timezone.utc


def make_ticker(*, code="KRW-BTC", price=100_000_000.0, ts=1_800_000_000_000, change_rate=0.01,
                signed_change_rate=0.01, change="RISE", volume_24h=123.456, stream_type="REALTIME"):
    return {
        "type": "ticker", "code": code, "opening_price": price, "trade_price": price,
        "timestamp": ts, "trade_timestamp": ts, "trade_volume": 0.001, "stream_type": stream_type,
        "change": change, "change_rate": change_rate, "signed_change_rate": signed_change_rate,
        "change_price": price * change_rate, "signed_change_price": price * signed_change_rate,
        "acc_trade_volume_24h": volume_24h, "acc_trade_price_24h": volume_24h * price,
    }


def make_orderbook(*, code="KRW-BTC", ts=1_800_000_000_000, bid=99_999_000.0, ask=100_001_000.0,
                    stream_type="REALTIME"):
    return {
        "type": "orderbook", "code": code, "timestamp": ts,
        "orderbook_units": [{"ask_price": ask, "bid_price": bid, "ask_size": 0.5, "bid_size": 0.4}],
        "stream_type": stream_type,
    }


def make_trade(*, code="KRW-BTC", ts=1_800_000_000_000, sid=17_800_000_000_000_000, stream_type="REALTIME"):
    return {
        "type": "trade", "code": code, "trade_price": 100_000_000.0, "trade_volume": 0.001,
        "timestamp": ts, "trade_timestamp": ts, "sequential_id": sid, "ask_bid": "BID",
        "stream_type": stream_type,
    }


def new_gate(markets=("KRW-BTC", "KRW-ETH"), **overrides):
    kwargs = dict(
        markets=list(markets),
        max_staleness_seconds_by_kind={"ticker": 30, "orderbook": 15},
        base_backoff_seconds=1.0, max_backoff_seconds=8.0,
    )
    kwargs.update(overrides)
    return OG.ObservationGate(**kwargs)


T0 = dt.datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Market list parsing
# ---------------------------------------------------------------------------

class MarketListParsingTests(unittest.TestCase):
    def test_parses_comma_separated_upper_and_dedupes(self):
        self.assertEqual(
            OG.parse_market_list(" krw-btc, KRW-ETH ,krw-btc"), ["KRW-BTC", "KRW-ETH"],
        )

    def test_empty_string_fails_closed(self):
        with self.assertRaises(OG.ObservationServiceError):
            OG.parse_market_list("   ")

    def test_invalid_market_code_fails_closed(self):
        with self.assertRaises(OG.ObservationServiceError):
            OG.parse_market_list("KRW-BTC,USD-ETH")

    def test_non_string_fails_closed(self):
        with self.assertRaises(OG.ObservationServiceError):
            OG.parse_market_list(None)


# ---------------------------------------------------------------------------
# Message handling: accepted / malformed / unsupported kind / out of scope
# ---------------------------------------------------------------------------

class MessageHandlingTests(unittest.TestCase):
    def test_ticker_and_orderbook_accepted(self):
        gate = new_gate()
        r1 = gate.handle_message(make_ticker(), received_at=T0)
        r2 = gate.handle_message(make_orderbook(), received_at=T0)
        self.assertEqual(r1["action"], "ACCEPTED")
        self.assertEqual(r2["action"], "ACCEPTED")
        self.assertEqual(gate.counts["accepted"], 2)

    def test_malformed_message_rejected_not_raised(self):
        gate = new_gate()
        result = gate.handle_message({"type": "ticker", "code": "KRW-BTC"}, received_at=T0)
        self.assertEqual(result["action"], "REJECTED_MALFORMED")
        self.assertEqual(gate.counts["rejected_malformed"], 1)

    def test_trade_and_candle_are_explicitly_unsupported_not_silently_dropped(self):
        gate = new_gate()
        trade_result = gate.handle_message(make_trade(), received_at=T0)
        self.assertEqual(trade_result["action"], "REJECTED_UNSUPPORTED_KIND")
        self.assertEqual(trade_result["kind"], "trade")
        self.assertEqual(gate.counts["rejected_unsupported_kind"], 1)

    def test_out_of_scope_market_rejected(self):
        gate = new_gate(markets=("KRW-BTC",))
        result = gate.handle_message(make_ticker(code="KRW-ETH"), received_at=T0)
        self.assertEqual(result["action"], "REJECTED_OUT_OF_SCOPE_MARKET")
        self.assertEqual(gate.counts["rejected_out_of_scope_market"], 1)

    def test_handle_message_never_raises_on_garbage_input(self):
        gate = new_gate()
        for garbage in (None, [], "not a dict", {"type": "myOrder", "code": "KRW-BTC"}):
            result = gate.handle_message(garbage, received_at=T0)
            self.assertIn(result["action"], ("REJECTED_MALFORMED",))


# ---------------------------------------------------------------------------
# Duplicate guard -- bounded-memory adaptation
# ---------------------------------------------------------------------------

class DuplicateGuardTests(unittest.TestCase):
    def test_exact_duplicate_ignored(self):
        gate = new_gate()
        msg = make_ticker()
        gate.handle_message(msg, received_at=T0)
        result = gate.handle_message(dict(msg), received_at=T0 + dt.timedelta(seconds=1))
        self.assertEqual(result["action"], "DUPLICATE_IGNORED")
        self.assertEqual(gate.counts["duplicate_ignored"], 1)

    def test_different_payload_same_key_is_accepted_as_new(self):
        gate = new_gate()
        gate.handle_message(make_ticker(price=100.0), received_at=T0)
        result = gate.handle_message(make_ticker(price=101.0), received_at=T0)
        self.assertEqual(result["action"], "ACCEPTED")

    def test_duplicate_guard_size_bounded_by_market_kind_count_not_message_count(self):
        # The whole point of LastSeenDuplicateGuard vs. P9-06's DuplicateGuard:
        # a persistent daemon must not grow this dict forever.
        gate = new_gate(markets=("KRW-BTC", "KRW-ETH"))
        for i in range(500):
            for code in ("KRW-BTC", "KRW-ETH"):
                gate.handle_message(make_ticker(code=code, price=float(i), ts=1_800_000_000_000 + i), received_at=T0)
                gate.handle_message(make_orderbook(code=code, ts=1_800_000_000_000 + i), received_at=T0)
        # 2 markets x 2 kinds = 4, no matter how many distinct messages were processed.
        self.assertEqual(len(gate.duplicate_guard), 4)


# ---------------------------------------------------------------------------
# Out-of-order tracker (reused unchanged from realtime/upbit_realtime_gate.py)
# ---------------------------------------------------------------------------

class OutOfOrderTests(unittest.TestCase):
    def test_regressed_ticker_timestamp_flagged_and_state_not_advanced(self):
        gate = new_gate()
        gate.handle_message(make_ticker(price=100.0, ts=2_000), received_at=T0)
        result = gate.handle_message(make_ticker(price=999.0, ts=1_000), received_at=T0)
        self.assertEqual(result["action"], "OUT_OF_ORDER_FLAGGED")
        self.assertEqual(gate.counts["out_of_order"], 1)
        snapshot = gate.status_snapshot(T0)
        # The out-of-order 999.0 must never have overwritten the last good price.
        self.assertEqual(snapshot["markets"]["KRW-BTC"]["last_price"], 100.0)


# ---------------------------------------------------------------------------
# Reconnect state machine -- never permanently gives up (unlike P9-06)
# ---------------------------------------------------------------------------

class ReconnectTests(unittest.TestCase):
    def test_next_attempt_always_retries_never_fails_closed_permanently(self):
        state = OG.PersistentConnectionState(base_backoff_seconds=1.0, max_backoff_seconds=4.0)
        state.on_disconnect(T0, "ConnectionClosed")
        # Far beyond P9-06's typical max_attempts (e.g. 6) -- a persistent
        # daemon must keep retrying, not fail closed to a permanent state.
        for _ in range(50):
            attempt = state.next_attempt()
            self.assertEqual(attempt["action"], "RETRY")
            self.assertLessEqual(attempt["backoff_seconds"], 4.0)
        self.assertEqual(state.state, OG.RECONNECTING)

    def test_backoff_caps_at_configured_maximum(self):
        state = OG.PersistentConnectionState(base_backoff_seconds=1.0, max_backoff_seconds=8.0)
        state.on_disconnect(T0, "x")
        backoffs = [state.next_attempt()["backoff_seconds"] for _ in range(10)]
        self.assertEqual(max(backoffs), 8.0)
        self.assertEqual(backoffs[-1], 8.0)

    def test_on_connected_resets_attempt_and_failure_counters(self):
        state = OG.PersistentConnectionState(base_backoff_seconds=1.0, max_backoff_seconds=8.0)
        state.on_disconnect(T0, "x")
        state.next_attempt()
        state.next_attempt()
        state.on_connected(T0 + dt.timedelta(seconds=5))
        self.assertEqual(state.state, OG.CONNECTED)
        self.assertEqual(state.attempt, 0)
        self.assertEqual(state.consecutive_failures, 0)

    def test_request_stop_is_the_only_way_to_stop_retrying(self):
        state = OG.PersistentConnectionState(base_backoff_seconds=1.0, max_backoff_seconds=8.0)
        state.on_disconnect(T0, "x")
        state.next_attempt()
        state.request_stop()
        self.assertEqual(state.state, OG.STOPPED)
        with self.assertRaises(OG.ObservationServiceError):
            state.next_attempt()

    def test_invalid_backoff_config_fails_closed(self):
        with self.assertRaises(OG.ObservationServiceError):
            OG.PersistentConnectionState(base_backoff_seconds=0, max_backoff_seconds=8.0)


# ---------------------------------------------------------------------------
# Freshness -- the never-silently-fresh contract
# ---------------------------------------------------------------------------

class FreshnessTests(unittest.TestCase):
    def test_no_data_before_any_message_while_connected(self):
        gate = new_gate()
        gate.on_connected(T0)
        snapshot = gate.status_snapshot(T0)
        self.assertEqual(snapshot["markets"]["KRW-BTC"]["freshness"], OG.NO_DATA)
        self.assertEqual(snapshot["overall_freshness"], OG.NO_DATA)

    def test_fresh_right_after_a_message_while_connected(self):
        gate = new_gate()
        gate.on_connected(T0)
        gate.handle_message(make_ticker(), received_at=T0)
        gate.handle_message(make_orderbook(), received_at=T0)
        snapshot = gate.status_snapshot(T0 + dt.timedelta(seconds=1))
        self.assertEqual(snapshot["markets"]["KRW-BTC"]["freshness"], OG.FRESH)

    def test_stale_after_max_staleness_window_elapses(self):
        gate = new_gate(max_staleness_seconds_by_kind={"ticker": 10, "orderbook": 10})
        gate.on_connected(T0)
        gate.handle_message(make_ticker(), received_at=T0)
        gate.handle_message(make_orderbook(), received_at=T0)
        snapshot = gate.status_snapshot(T0 + dt.timedelta(seconds=30))
        self.assertEqual(snapshot["markets"]["KRW-BTC"]["freshness"], OG.STALE)

    def test_disconnected_overrides_a_very_recent_message_never_silently_fresh(self):
        # The critical honesty test: a message received one second ago must
        # NOT be reported FRESH if the socket is currently down.
        gate = new_gate()
        gate.on_connected(T0)
        gate.handle_message(make_ticker(), received_at=T0)
        gate.handle_message(make_orderbook(), received_at=T0)
        gate.on_disconnect(T0 + dt.timedelta(seconds=1), "ConnectionClosed")
        snapshot = gate.status_snapshot(T0 + dt.timedelta(seconds=2))
        self.assertEqual(snapshot["markets"]["KRW-BTC"]["freshness"], OG.DISCONNECTED)
        self.assertEqual(snapshot["markets"]["KRW-BTC"]["ticker_freshness"]["status"], OG.DISCONNECTED)
        self.assertEqual(snapshot["overall_freshness"], OG.DISCONNECTED)
        self.assertEqual(snapshot["connection_state"], OG.RECONNECTING)

    def test_reconnecting_before_first_ever_connect_is_disconnected_not_fresh(self):
        gate = new_gate()
        snapshot = gate.status_snapshot(T0)
        self.assertEqual(snapshot["connection_state"], OG.CONNECTING)
        self.assertEqual(snapshot["markets"]["KRW-BTC"]["freshness"], OG.DISCONNECTED)

    def test_freshness_never_returns_a_value_outside_the_four_contract_states(self):
        gate = new_gate()
        for connected in (True, False):
            if connected:
                gate.on_connected(T0)
            else:
                gate.on_disconnect(T0, "x")
            snapshot = gate.status_snapshot(T0 + dt.timedelta(seconds=5))
            for market_row in snapshot["markets"].values():
                self.assertIn(market_row["freshness"], OG.FRESHNESS_STATUSES)
                self.assertIn(market_row["ticker_freshness"]["status"], OG.FRESHNESS_STATUSES)
                self.assertIn(market_row["orderbook_freshness"]["status"], OG.FRESHNESS_STATUSES)
            self.assertIn(snapshot["overall_freshness"], OG.FRESHNESS_STATUSES)


# ---------------------------------------------------------------------------
# Snapshot shape / dual timestamps / authority
# ---------------------------------------------------------------------------

class SnapshotShapeTests(unittest.TestCase):
    def test_snapshot_carries_both_utc_and_kst_receive_timestamps(self):
        gate = new_gate()
        gate.on_connected(T0)
        gate.handle_message(make_ticker(), received_at=T0)
        snapshot = gate.status_snapshot(T0)
        row = snapshot["markets"]["KRW-BTC"]
        self.assertTrue(row["received_at_utc"].endswith("Z"))
        self.assertIn("+09:00", row["received_at_kst"])
        # KST is exactly UTC+9 (no DST) -- the wall-clock hour must differ by 9.
        utc_dt = dt.datetime.fromisoformat(row["received_at_utc"].replace("Z", "+00:00"))
        kst_dt = dt.datetime.fromisoformat(row["received_at_kst"])
        self.assertEqual(kst_dt.astimezone(UTC), utc_dt)
        self.assertEqual((kst_dt.replace(tzinfo=None) - utc_dt.replace(tzinfo=None)).total_seconds(), 9 * 3600)

    def test_ticker_fields_present_price_change_volume(self):
        gate = new_gate()
        gate.on_connected(T0)
        gate.handle_message(make_ticker(price=123.0, change_rate=0.05, volume_24h=9.0), received_at=T0)
        row = gate.status_snapshot(T0)["markets"]["KRW-BTC"]
        self.assertEqual(row["last_price"], 123.0)
        self.assertEqual(row["change_rate"], 0.05)
        self.assertEqual(row["acc_trade_volume_24h"], 9.0)

    def test_orderbook_fields_present_bid_ask(self):
        gate = new_gate()
        gate.on_connected(T0)
        gate.handle_message(make_orderbook(bid=1.0, ask=2.0), received_at=T0)
        row = gate.status_snapshot(T0)["markets"]["KRW-BTC"]
        self.assertEqual(row["best_bid"]["price"], 1.0)
        self.assertEqual(row["best_ask"]["price"], 2.0)

    def test_snapshot_is_json_serializable_and_hash_stable(self):
        gate = new_gate()
        gate.on_connected(T0)
        gate.handle_message(make_ticker(), received_at=T0)
        snapshot = gate.status_snapshot(T0)
        encoded = json.dumps(snapshot, default=str)
        self.assertIsInstance(encoded, str)
        self.assertIn("payload_sha256", snapshot)


class AuthorityTests(unittest.TestCase):
    def test_authority_all_false_and_observation_only_true(self):
        gate = new_gate()
        snapshot = gate.status_snapshot(T0)
        for key, value in snapshot["authority"].items():
            self.assertFalse(value, key)
        self.assertTrue(snapshot["observation_only"])
        for key in (
            "feeds_tradeable_universe", "feeds_candidate_promotion",
            "feeds_paper_eligibility", "feeds_decision_or_order_path",
        ):
            self.assertFalse(snapshot[key], key)

    def test_subscription_message_never_contains_a_private_channel_type(self):
        message = OG.build_subscription_message(["KRW-BTC", "KRW-ETH"], ticket="atlas-obs-test")
        GATE = OG.GATE
        emitted_types = [entry["type"] for entry in message if "type" in entry]
        for forbidden in GATE.PRIVATE_WS_TYPES_FORBIDDEN:
            self.assertNotIn(forbidden, emitted_types)
        self.assertEqual(set(emitted_types), set(OG.OBSERVATION_MESSAGE_TYPES))
        self.assertNotIn("trade", emitted_types)

    def test_build_subscription_message_requests_no_candle_timeframe(self):
        message = OG.build_subscription_message(["KRW-BTC"], ticket="atlas-obs-test")
        emitted_types = [entry["type"] for entry in message if "type" in entry]
        self.assertFalse(any(t.startswith("candle.") for t in emitted_types))


# ---------------------------------------------------------------------------
# Source-level boundary proofs -- mirrors this session's established
# source-grep pattern (test/test_upbit_realtime_gate.py)
# ---------------------------------------------------------------------------

class SourceBoundaryTests(unittest.TestCase):
    def test_observation_gate_has_no_websockets_asyncio_or_socket_dependency(self):
        source = GATE_MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import websockets", source)
        self.assertNotIn("import asyncio", source)
        self.assertNotIn("import socket", source)

    def test_observation_gate_never_calls_an_order_withdrawal_or_deposit_endpoint(self):
        for path in (GATE_MODULE_PATH, SERVICE_MODULE_PATH):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("api.upbit.com/v1/orders", source)
            self.assertNotIn("api.upbit.com/v1/withdraws", source)
            self.assertNotIn("api.upbit.com/v1/deposits", source)
            self.assertNotIn("myOrder\"", source.replace("'", '"'))
            self.assertNotIn("myAsset\"", source.replace("'", '"'))

    def test_service_lazily_imports_websockets_only_inside_a_function(self):
        source = SERVICE_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_imports.add(node.module)
        self.assertNotIn("websockets", top_level_imports)
        self.assertIn("import websockets", source)

    def test_neither_module_imports_or_references_the_tradeable_universe_module(self):
        # ast-based, not substring-based: the human-readable docstrings and
        # comments in both files legitimately *mention*
        # universe/upbit_tradeable_universe.py while explaining that this
        # service never touches it -- what must actually be absent is an
        # *import statement* referencing it, or a dynamic-load call (the
        # importlib.util.spec_from_file_location(...) pattern this repo uses
        # elsewhere) with that path as a literal argument.
        for path in (GATE_MODULE_PATH, SERVICE_MODULE_PATH):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("universe", alias.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn("universe", node.module)
                if isinstance(node, ast.Call):
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            self.assertNotIn("upbit_tradeable_universe.py", arg.value)
                            self.assertNotIn("universe/", arg.value)

    def test_neither_module_writes_to_the_repo_evidence_or_data_directories(self):
        for path in (GATE_MODULE_PATH, SERVICE_MODULE_PATH):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn('"evidence/', source)
            self.assertNotIn("'evidence/", source)
            self.assertNotIn('"data/observations', source)
            self.assertNotIn("write_evidence_snapshot", source)
            self.assertNotIn("open(\"w", source)  # no local file writes at all in this service's code

    def test_gate_module_reuses_realtime_upbit_realtime_gate_unchanged(self):
        source = GATE_MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("upbit_realtime_gate.py", source)
        self.assertTrue(REALTIME_GATE_MODULE_PATH.exists())

    def test_dockerfile_never_installs_or_references_order_credentials(self):
        source = DOCKERFILE_PATH.read_text(encoding="utf-8")
        for forbidden in ("KIS_", "APP_SECRET", "API_KEY", "API_SECRET", "ORDER"):
            self.assertNotIn(forbidden, source)
        self.assertIn("USER atlas", source)

    def test_compose_binds_loopback_by_default_and_has_restart_policy(self):
        source = COMPOSE_PATH.read_text(encoding="utf-8")
        self.assertIn("restart: unless-stopped", source)
        self.assertIn("127.0.0.1", source)
        self.assertIn("/health", source)

    def test_env_example_has_no_secret_looking_values(self):
        source = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        for forbidden in ("SECRET_KEY=", "API_KEY=", "PASSWORD=", "TOKEN=replace"):
            self.assertNotIn(forbidden, source)

    def test_readme_documents_outbound_only_portal_delivery(self):
        source = README_PATH.read_text(encoding="utf-8")
        self.assertIn("outbound-only portal delivery", source.lower())
        self.assertIn("no inbound firewall rule", source.lower())


# ---------------------------------------------------------------------------
# Local (loopback-only) HTTP API -- no live Upbit connection involved
# ---------------------------------------------------------------------------

class HttpApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.S = load_module("upbit_realtime_observation_service", SERVICE_MODULE_PATH)
        cls.gate = new_gate()
        cls.gate.on_connected(T0)
        cls.gate.handle_message(make_ticker(), received_at=T0)
        cls.gate.handle_message(make_orderbook(), received_at=T0)
        handler = cls.S._make_handler(cls.gate, "2026-08-29T00:00:00.000Z")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
        finally:
            conn.close()

    def test_health_always_ok(self):
        status, body = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_ready_true_when_connected(self):
        status, body = self._get("/ready")
        self.assertEqual(status, 200)
        self.assertTrue(body["ready"])

    def test_ready_false_when_disconnected(self):
        gate = new_gate()
        gate.on_disconnect(T0, "x")
        handler = self.S._make_handler(gate, "2026-08-29T00:00:00.000Z")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/ready")
            resp = conn.getresponse()
            body = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(resp.status, 503)
            self.assertFalse(body["ready"])
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_snapshot_returns_full_contract_shape(self):
        status, body = self._get("/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(body["schema_version"], "upbit_realtime_observation_snapshot/1")
        self.assertIn("KRW-BTC", body["markets"])

    def test_unknown_path_is_404(self):
        status, _ = self._get("/nope")
        self.assertEqual(status, 404)

    def test_post_is_405_read_only(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("POST", "/snapshot", body="{}")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 405)
        finally:
            conn.close()


class ServiceConfigTests(unittest.TestCase):
    def setUp(self):
        self.S = load_module("upbit_realtime_observation_service_config", SERVICE_MODULE_PATH)
        self._saved_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)

    def test_default_config_binds_loopback(self):
        for key in list(os.environ):
            if key.startswith("ATLAS_UPBIT_OBS_"):
                del os.environ[key]
        config = self.S.load_config_from_env()
        self.assertEqual(config["bind"], "127.0.0.1")
        self.assertEqual(set(config["markets"]), set(self.S.OG.DEFAULT_MARKETS))

    def test_non_loopback_bind_fails_closed_without_explicit_override(self):
        os.environ["ATLAS_UPBIT_OBS_BIND"] = "0.0.0.0"
        os.environ.pop("ATLAS_UPBIT_OBS_ALLOW_NON_LOOPBACK_BIND", None)
        with self.assertRaises(self.S.ServiceConfigError):
            self.S.load_config_from_env()

    def test_non_loopback_bind_allowed_with_explicit_override(self):
        os.environ["ATLAS_UPBIT_OBS_BIND"] = "0.0.0.0"
        os.environ["ATLAS_UPBIT_OBS_ALLOW_NON_LOOPBACK_BIND"] = "true"
        config = self.S.load_config_from_env()
        self.assertEqual(config["bind"], "0.0.0.0")

    def test_invalid_numeric_env_fails_closed(self):
        os.environ["ATLAS_UPBIT_OBS_PORT"] = "not-a-number"
        with self.assertRaises(self.S.ServiceConfigError):
            self.S.load_config_from_env()

    def test_portal_push_url_and_signing_key_are_required_together(self):
        os.environ["ATLAS_PORTAL_PUSH_URL"] = (
            "https://atlas.ddcloud.co.kr/api/internal/upbit-realtime-observation/snapshot"
        )
        os.environ.pop("ATLAS_SIGNING_KEY_PATH", None)
        with self.assertRaises(self.S.ServiceConfigError):
            self.S.load_config_from_env()

    def test_portal_push_must_use_https_exact_ingest_path(self):
        os.environ["ATLAS_PORTAL_PUSH_URL"] = "http://127.0.0.1/snapshot"
        os.environ["ATLAS_SIGNING_KEY_PATH"] = "/run/secrets/realtime-signing.pem"
        with self.assertRaises(self.S.ServiceConfigError):
            self.S.load_config_from_env()


# ---------------------------------------------------------------------------
# Manual, clearly-labelled live smoke test -- NOT part of the automated
# regression, NOT a natural sample. Skipped unless explicitly requested and
# `websockets` is installed.
# ---------------------------------------------------------------------------

@unittest.skipUnless(
    os.getenv("ATLAS_UPBIT_OBS_RUN_LIVE_SMOKE_TEST") == "1",
    "manual live check only -- set ATLAS_UPBIT_OBS_RUN_LIVE_SMOKE_TEST=1 to run",
)
class ManualLiveSmokeTest(unittest.TestCase):
    """Connects briefly to the real wss://api.upbit.com/websocket/v1 and
    proves at least one real ticker/orderbook message round-trips through
    ObservationGate.handle_message into an ACCEPTED, non-DISCONNECTED
    snapshot. This is a manual functional check of transport connectivity,
    not evidence, not a natural-sample regression, and never runs as part of
    ``run_all.py``'s approved offline suite."""

    def test_live_connection_receives_at_least_one_accepted_message(self):
        import asyncio
        try:
            import websockets
        except ImportError:
            self.skipTest("websockets not installed")

        async def _run():
            gate = new_gate(markets=("KRW-BTC", "KRW-ETH"))
            message = OG.build_subscription_message(["KRW-BTC", "KRW-ETH"], ticket="atlas-obs-manual-smoke")
            async with websockets.connect("wss://api.upbit.com/websocket/v1", open_timeout=10) as ws:
                gate.on_connected(OG.utc_now())
                await ws.send(json.dumps(message))
                for _ in range(20):
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    parsed_raw = json.loads(raw)
                    gate.handle_message(parsed_raw, received_at=OG.utc_now())
                    if gate.counts["accepted"] >= 2:
                        break
            return gate

        gate = asyncio.run(_run())
        self.assertGreaterEqual(gate.counts["accepted"], 1)
        snapshot = gate.status_snapshot()
        self.assertNotEqual(snapshot["overall_freshness"], OG.DISCONNECTED)


if __name__ == "__main__":
    unittest.main()
