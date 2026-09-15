#!/usr/bin/env python3
"""Per-market realtime freshness in the P9 -> P10-11 Crypto PAPER runtime bridge.

User ratification CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914 (record
sha256 043932a4...) and CIO addenda (floor metric bc009c59..., subscription
scope 25e69d51...):

* each market's realtime freshness is judged on its own; the aggregate
  realtime status and the realtime run's gate ``overall_status`` no longer
  block every market in the bridge;
* a market with a ``market_action_cap_reason`` (STALE/MISSING/UNKNOWN
  realtime, liquidity floor excluded, non-realtime cap) receives no new PAPER
  intent, while other markets are evaluated normally;
* one market's missing or non-FRESH ticker/orderbook is that market's blocker
  (or UNKNOWN mark), never an abort of the whole request;
* ``crypto_paper_runtime_request/3`` carries the decision schema and per-market
  status; issued ``/2`` requests and ``/1`` decisions keep validating.

Every replay uses the committed natural 2026-09-13 21:12 inputs (the 2112
decision packet's retained sources, realtime run_008): KRW-BTC/ETH/LINK/SOL/
SUI/XRP FRESH and KRW-SHIB/WLD STALE at capture end + 1s, aggregate STALE.
Only the ratification's effective-instant check and, where stated, upstream
promotion/eligibility states are lifted (test-only) so the bridge path
itself is observable on natural bytes.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BRIDGE = load("test_crypto_paper_runtime_bridge_per_market", ROOT / "shadow" / "crypto_paper_runtime_bridge.py")
DECISION = BRIDGE.DECISION
POLICY = DECISION.PER_MARKET
SIMULATOR = BRIDGE.SIMULATOR
ELIGIBILITY = BRIDGE.ELIGIBILITY

VPM = DECISION.PER_MARKET_OUTPUT_SCHEMA_VERSION
V2_ISSUED = DECISION.PER_MARKET_V2_OUTPUT_SCHEMA_VERSION
V1 = DECISION.LEGACY_OUTPUT_SCHEMA_VERSION
NATURAL_2112_GLOB = "evidence/crypto_paper_decision/2026-09-13/2112/*/packet.json"
REPLAY_AT = "2026-09-13T21:12:24Z"
FRESH_AT_REPLAY = ("KRW-BTC", "KRW-ETH", "KRW-LINK", "KRW-SOL", "KRW-SUI", "KRW-XRP")
STALE_AT_REPLAY = ("KRW-SHIB", "KRW-WLD")
ALL_EIGHT = tuple(sorted(FRESH_AT_REPLAY + STALE_AT_REPLAY))
CODE_COMMIT = "b" * 40
OBSERVATION_COMMIT = "c" * 40

# Golden identities of ``crypto_paper_runtime_request/2`` packets produced by
# the issued bridge at public main 829ccaa10f9560e60ae469b5b4ef022e20457dc2
# (before this change) for the scenarios below, hashed with
# ``source_inputs.observation_root`` blanked because it is a host path.
LEGACY_V2_GOLDEN = {
    "v1_decision_open_btc_order": "078f32271dc292b008d0cb1e476aa4073c84eb88f841abd2cbe20b25765aaa07",
}
# The pre-change bridge could also emit a /2 request over a /3 decision
# (normalized sha256 d835f993...); no such request was issued by the private
# runtime pin, and accepting one is a per-market -> aggregate downgrade.


def natural_packet() -> dict:
    paths = sorted(ROOT.glob(NATURAL_2112_GLOB))
    assert len(paths) == 1, paths
    return json.loads(paths[0].read_text(encoding="utf-8"))


def natural_entries(packet: dict | None = None) -> dict:
    packet = packet or natural_packet()
    refs = {row["role"]: row for row in packet["source_refs"]}

    def entry(role):
        path = ROOT / refs[role]["path"]
        return {"date": path.parent.name, "path": path, "record": json.loads(path.read_text(encoding="utf-8"))}

    universe = entry("upbit_tradeable_universe_packet")
    universe["packet"] = universe["record"]["packet"]
    market = entry("upbit_market_evidence_packet")
    market["date"] = market["record"]["snapshot_date"]
    return {
        "universe_entry": universe,
        "market_evidence_entry": market,
        "realtime_entry": entry("upbit_realtime_capture_run"),
        "leadership_entry": entry("crypto_leadership_packet"),
    }


def replay(schema_version: str = VPM, *, realtime_entry: dict | None = None) -> dict:
    packet = natural_packet()
    entries = natural_entries(packet)
    if realtime_entry is not None:
        entries["realtime_entry"] = realtime_entry
    return DECISION.build_snapshot(
        generated_at=REPLAY_AT, source_commit=packet["source_commit"],
        previous_entry=None, component_rows=None, schema_version=schema_version,
        **entries,
    )


def order_draft(market: str) -> dict:
    return {
        "expires_at": "2026-09-13T21:42:24Z",
        "entry_zone": {"low": "1", "high": "2"},
        "duplicate_guard_key": f"PAPER.BUY.GUARD.{market}.PER.MARKET.TEST",
        "quantity": "1",
        "planned_loss_krw": "10",
    }


@contextlib.contextmanager
def per_market_effective():
    """The natural inputs predate the ratified effective instant (test-only)."""
    with mock.patch.object(POLICY, "is_effective", return_value=True):
        yield


@contextlib.contextmanager
def actionable_upstream(eligible_markets=()):
    """Candidates reach FOCUSED_REVIEW, non-realtime evidence FRESH, and the
    bridge's P5-09 rebuild returns PAPER_BUY_ELIGIBLE for ``eligible_markets``.

    Today's Regime UNKNOWN keeps every natural candidate at WATCH and P5-09
    at no eligibility, so the bridge's per-market entry gate is never reached
    on natural data without lifting these upstream states.
    """
    real_build = DECISION.PROMOTION.build_promotion_packet

    def promoted(*args, **kwargs):
        packet = real_build(*args, **kwargs)
        for row in packet["candidates"]:
            row["promotion_state"] = "FOCUSED_REVIEW"
            row["promotion_reason"] = "TEST_ONLY_ALL_CRITERIA_PASSED"
        return packet

    def eligibility(promotion, **kwargs):
        if kwargs.get("paper_account_state") is None:  # the decision's own funnel
            raise ELIGIBILITY.CryptoPaperBuyEligibilityError("TEST_ONLY_PROMOTION_STATE_LIFTED")
        return {"candidates": [
            {"market": market, "eligibility_state": "PAPER_BUY_ELIGIBLE", "order_draft": order_draft(market)}
            for market in eligible_markets
        ]}

    with (
        mock.patch.object(DECISION.PROMOTION, "build_promotion_packet", side_effect=promoted),
        mock.patch.object(ELIGIBILITY, "build_eligibility_packet", side_effect=eligibility),
        mock.patch.object(ELIGIBILITY, "validate_output", side_effect=lambda value: value),
        mock.patch.object(DECISION, "_market_evidence_freshness", return_value=(DECISION.FRESH, None)),
    ):
        yield


@contextlib.contextmanager
def btc_below_liquidity_floor():
    real_evaluate = POLICY.evaluate_liquidity_floor

    def evaluate(*args, **kwargs):
        block = real_evaluate(*args, **kwargs)
        block["markets"]["KRW-BTC"].update(status=POLICY.EXCLUDED, reason=POLICY.BELOW_FLOOR)
        return block

    with mock.patch.object(POLICY, "evaluate_liquidity_floor", side_effect=evaluate):
        yield


def ledger():
    return SIMULATOR.create_ledger(
        ledger_id="PAPER.LEDGER.PER.MARKET.TEST", initial_cash="100000000",
        opened_at="2026-09-13T20:59:00Z",
        idempotency_key="PAPER.ACCOUNT.OPEN.PER.MARKET.TEST",
    )


def account(open_order_markets=()):
    value = ledger()
    for market in open_order_markets:
        intent = SIMULATOR.build_intent(
            order_id=f"PAPER.BUY.{market}.CARRIED.TEST",
            idempotency_key=f"PAPER.SUBMIT.{market}.CARRIED.TEST",
            market=market, side="BUY", order_type="LIMIT", quantity="1",
            limit_price="1", fee_rate="0", queue_fraction="1",
            submitted_at="2026-09-13T21:00:00Z", expires_at="2026-09-13T22:00:00Z",
            market_regime_status="UNKNOWN", source_plan_ref="test://plan/per-market",
            source_plan_sha256="b" * 64, source_evidence_ref="test://book/per-market",
            source_evidence_sha256="c" * 64,
        )
        value = SIMULATOR.submit_order(value, intent)
    return SIMULATOR.build_account_state(
        value, observed_at=REPLAY_AT, mark_prices={},
        mark_freshness_status="FRESH", mark_source_ref="test://marks/per-market",
        mark_source_sha256="d" * 64,
    )


def config():
    return BRIDGE.build_runtime_config(
        approval_status=BRIDGE.RUNTIME_CONFIG_APPROVAL,
        approved_by="CIO_TEST", approved_at="2026-09-13T21:00:00Z",
        ledger_id="PAPER.LEDGER.PER.MARKET.TEST", initial_cash_krw="100000000",
        fee_rate="0", queue_fraction="1", order_type="LIMIT",
        limit_price_source="ENTRY_ZONE_LOW",
    )


def request_for(decision, *, account_state=None, with_config=True, **kwargs):
    return BRIDGE.build_runtime_request(
        decision, expected_source_commit=decision["source_commit"],
        public_code_commit_sha=CODE_COMMIT, observation_commit_sha=OBSERVATION_COMMIT,
        account_state=account_state if account_state is not None else account(),
        open_position_risk=[], runtime_config=config() if with_config else None,
        **kwargs,
    )


def normalized_request_sha256(request: dict) -> str:
    value = copy.deepcopy(request)
    value.pop("packet_sha256")
    value["source_inputs"]["observation_root"] = "<observation_root>"
    return BRIDGE.payload_sha256(value)


def intents_by_market(request: dict) -> dict:
    return {row["market"]: row for row in request["requests"]}


class ModifiedRunFixture(unittest.TestCase):
    """A copy of natural run_008 with one retained message removed (test-only)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix=".crypto_runtime_per_market_test_", dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def realtime_entry(self, mutate) -> dict:
        entry = natural_entries()["realtime_entry"]
        record = copy.deepcopy(entry["record"])
        run = record["run"]
        mutate(run)
        status = run["status"]
        status.pop("payload_sha256")
        status["payload_sha256"] = BRIDGE.payload_sha256(status)
        # Re-evaluate the exact ratified P9-01 consumer result over the tickers
        # actually retained, as the capture does, so rederivation stays exact.
        gate = DECISION.REALTIME_GATE
        contract = gate.load_contract()
        observed = dt.datetime.strptime(status["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        quotes = []
        for item in run["latest_public_messages"].values():
            if item.get("kind") != "ticker":
                continue
            received = dt.datetime.fromisoformat(item["received_at"][:-1] + "+00:00")
            quotes.append(gate.quote_row_from_ticker(gate.parse_message(item["raw"]), received_at=received))
        run["ratified_freshness_policy"]["consumer_result"] = gate.evaluate_with_ratified_freshness_policy(
            quotes, observed_at=observed, batch_id=f"P9_06_{observed.strftime('%Y%m%dT%H%M%SZ')}", contract=contract,
        )
        record["source_sha256"] = BRIDGE.payload_sha256(run)
        path = self.tmp / "realtime" / entry["date"] / "run_modified.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
        return {"date": entry["date"], "path": path, "record": record}


class NaturalPerMarketViewTests(unittest.TestCase):
    def test_view_records_every_subscribed_market_and_marks_are_per_market(self):
        with per_market_effective():
            decision = replay()
            self.assertEqual(BRIDGE.validate_decision_snapshot(decision), decision)
            self.assertEqual(decision["freshness_status"]["realtime"], DECISION.STALE)
            view = BRIDGE.market_freshness_view(decision)
            self.assertEqual(sorted(view), list(ALL_EIGHT))
            for market in FRESH_AT_REPLAY:
                self.assertEqual(view[market]["realtime_status"], DECISION.FRESH, market)
            for market in STALE_AT_REPLAY:
                self.assertEqual(view[market]["realtime_status"], DECISION.STALE, market)
            self.assertEqual(BRIDGE.market_realtime_status(decision, "KRW-DOGE"), (
                DECISION.MISSING, [BRIDGE.MARKET_NOT_SUBSCRIBED_REASON],
            ))

            marks = BRIDGE.latest_mark_prices_by_market(decision, ["KRW-WLD", "KRW-BTC", "KRW-SHIB", "KRW-DOGE"])
            self.assertEqual(sorted(marks["marks"]), ["KRW-BTC"])
            self.assertEqual(marks["mark_status"], {
                "KRW-BTC": "FRESH", "KRW-DOGE": "UNKNOWN", "KRW-SHIB": "UNKNOWN", "KRW-WLD": "UNKNOWN",
            })
            self.assertEqual(
                marks["unavailable_reasons"]["KRW-WLD"],
                "MARKET_REALTIME_NOT_FRESH:KRW-WLD:STALE:PROVIDER_AGE_EXCEEDED,TRANSPORT_DELAY_EXCEEDED",
            )
            self.assertTrue(marks["unavailable_reasons"]["KRW-DOGE"].startswith("MARKET_REALTIME_NOT_FRESH:KRW-DOGE:MISSING"))
            only_btc = BRIDGE.latest_mark_prices(decision, ["KRW-BTC"])
            self.assertEqual(only_btc[0], marks["marks"])
            self.assertEqual(only_btc[2], marks["source_sha256"])
            with self.assertRaisesRegex(BRIDGE.MarketEvidenceUnavailableError, "MARKET_REALTIME_NOT_FRESH:KRW-SHIB:STALE"):
                BRIDGE.latest_mark_prices(decision, ["KRW-BTC", "KRW-SHIB"])
            # The aggregate STALE and the run's overall_status STALE no longer
            # block a FRESH market's own book.
            self.assertEqual(
                BRIDGE.orderbook_snapshot(decision, market="KRW-ETH")["captured_at"], "2026-09-13T21:12:23Z",
            )
            with self.assertRaisesRegex(BRIDGE.MarketEvidenceUnavailableError, "DECISION_REALTIME_FRESHNESS_NOT_RATIFIED_FRESH"):
                BRIDGE.orderbook_snapshot(decision, market="KRW-ETH", per_market=False)

    def test_issued_v2_decision_uses_candidate_rows(self):
        with per_market_effective():
            decision = replay(V2_ISSUED)
            view = BRIDGE.market_freshness_view(decision)
        self.assertEqual(sorted(view), list(ALL_EIGHT))
        self.assertTrue(all(row["candidate"] for row in view.values()))
        self.assertEqual(view["KRW-WLD"]["realtime_status"], DECISION.STALE)
        self.assertEqual(BRIDGE.market_freshness_view(natural_packet()), {})

    def test_forged_per_market_block_fails_closed(self):
        with per_market_effective():
            decision = replay()
        forged = copy.deepcopy(decision)
        forged["realtime_per_market_freshness"]["subscribed_market_realtime"]["KRW-WLD"]["status"] = "FRESH"
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "DECISION_MARKET_REALTIME_INCONSISTENT:KRW-WLD"):
            BRIDGE.market_freshness_view(forged)
        del forged["realtime_per_market_freshness"]["subscribed_market_realtime"]
        with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "DECISION_SUBSCRIBED_MARKET_REALTIME_MISSING"):
            BRIDGE.market_freshness_view(forged)


class PerMarketRequestTests(ModifiedRunFixture):
    def assert_fresh_market_intent_despite_stale_aggregate(self, request):
        """Invariant: aggregate STALE never blocks a FRESH, uncapped market."""
        self.assertEqual(request["status"], "PAPER_INTENTS_READY")
        self.assertEqual(sorted(intents_by_market(request)), ["KRW-ETH"])

    def assert_capped_market_has_no_intent(self, request):
        """Invariant: a market with market_action_cap_reason gets no intent."""
        self.assertNotIn("KRW-BTC", intents_by_market(request))
        self.assertIn(
            "MARKET_ACTION_CAPPED:KRW-BTC:REALTIME_LIQUIDITY_FLOOR_EXCLUDED:KRW-BTC:TURNOVER_30D_AVG_BELOW_FLOOR",
            request["blockers"],
        )

    def test_fresh_market_intent_while_aggregate_and_run_status_are_stale(self):
        with per_market_effective(), actionable_upstream(["KRW-ETH"]):
            decision = replay()
            run_status = natural_entries()["realtime_entry"]["record"]["run"]["status"]["overall_status"]
            self.assertEqual(run_status, "STALE")
            self.assertEqual(decision["realtime_per_market_freshness"]["action_capped_markets"], list(STALE_AT_REPLAY))
            request = request_for(decision)
            self.assertEqual(BRIDGE.validate_runtime_request(request), request)
        self.assert_fresh_market_intent_despite_stale_aggregate(request)
        self.assertEqual(request["schema_version"], "crypto_paper_runtime_request/3")
        self.assertEqual(request["decision_schema_version"], VPM)
        self.assertEqual(request["freshness_mode"], BRIDGE.PER_MARKET_FRESHNESS_MODE)
        status = request["market_status"]
        self.assertEqual(sorted(status), list(ALL_EIGHT))
        self.assertEqual(sorted(m for m, row in status.items() if row["entry_state"] == BRIDGE.ENTRY_OPEN), list(FRESH_AT_REPLAY))
        self.assertEqual(status["KRW-WLD"]["market_action_cap_reason"], "MARKET_REALTIME_FRESHNESS_NOT_FRESH:KRW-WLD:STALE")
        intent = intents_by_market(request)["KRW-ETH"]["intent"]
        self.assertEqual(intent["side"], "BUY")
        self.assertTrue(intent["source_evidence_ref"].endswith("#latest_public_messages/KRW-ETH/orderbook"))
        self.assertFalse(request["authority"]["exchange_order_authorized"])

    def test_stale_market_eligible_is_blocked_alone(self):
        with per_market_effective(), actionable_upstream(["KRW-WLD"]):
            request = request_for(replay())
        self.assertEqual(request["requests"], [])
        self.assertEqual(request["status"], "WAIT_MARKET_EVIDENCE_OR_CAP")
        self.assertEqual(request["blockers"], [
            "MARKET_ACTION_CAPPED:KRW-WLD:MARKET_REALTIME_FRESHNESS_NOT_FRESH:KRW-WLD:STALE",
        ])

    def test_capped_fresh_market_gets_no_intent_while_other_market_opens(self):
        with per_market_effective(), btc_below_liquidity_floor(), actionable_upstream(["KRW-BTC", "KRW-ETH"]):
            decision = replay()
            rows = {row["market"]: row for row in decision["candidates"]}
            self.assertEqual(rows["KRW-BTC"]["realtime_freshness"]["status"], DECISION.FRESH)
            self.assertIsNotNone(rows["KRW-BTC"]["market_action_cap_reason"])
            request = request_for(decision)
        self.assert_capped_market_has_no_intent(request)
        # BTC is removed before allocation, so ETH alone is not an allocation wait.
        self.assert_fresh_market_intent_despite_stale_aggregate(request)
        self.assertEqual(request["market_status"]["KRW-BTC"]["entry_state"], BRIDGE.ENTRY_CAPPED)

    def test_missing_orderbook_is_that_markets_blocker_not_an_abort(self):
        def drop_eth_orderbook(run):
            del run["latest_public_messages"]["orderbook|-|KRW-ETH"]

        entry = self.realtime_entry(drop_eth_orderbook)
        with per_market_effective(), actionable_upstream(["KRW-ETH"]):
            decision = replay(realtime_entry=entry)
            self.assertEqual(BRIDGE.market_realtime_status(decision, "KRW-ETH")[0], DECISION.FRESH)
            request = request_for(decision)
            self.assertEqual(request["requests"], [])
            self.assertEqual(request["status"], "WAIT_MARKET_EVIDENCE_OR_CAP")
            self.assertEqual(request["blockers"], [
                "ENTRY_SNAPSHOT_UNAVAILABLE:KRW-ETH:REALTIME_ORDERBOOK_MISSING:KRW-ETH",
            ])
            # A carried order in another FRESH market still gets its book.
            carried = request_for(decision, account_state=account(["KRW-BTC", "KRW-ETH"]))
            self.assertEqual(carried["status"], "PAPER_MATCHES_READY")
            self.assertEqual([row["market"] for row in carried["match_snapshots"]], ["KRW-BTC"])
            self.assertIn(
                "MATCH_SNAPSHOT_UNAVAILABLE:KRW-ETH:REALTIME_ORDERBOOK_MISSING:KRW-ETH", carried["blockers"],
            )
            # A /2 request can never be derived from a per-market decision.
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_DECISION"):
                BRIDGE._derive_runtime_request(
                    decision, expected_source_commit=decision["source_commit"],
                    account_state=account(), open_position_risk=[], runtime_config=config(),
                    request_schema_version=BRIDGE.LEGACY_REQUEST_SCHEMA_VERSION,
                )

    def test_missing_ticker_is_market_missing_and_unknown_mark(self):
        def drop_eth_ticker(run):
            del run["latest_public_messages"]["ticker|-|KRW-ETH"]

        entry = self.realtime_entry(drop_eth_ticker)
        with per_market_effective(), actionable_upstream(["KRW-ETH", "KRW-XRP"]):
            decision = replay(realtime_entry=entry)
            self.assertEqual(BRIDGE.market_realtime_status(decision, "KRW-ETH")[0], DECISION.MISSING)
            marks = BRIDGE.latest_mark_prices_by_market(decision, ["KRW-ETH", "KRW-XRP"])
            self.assertEqual(marks["mark_status"], {"KRW-ETH": "UNKNOWN", "KRW-XRP": "FRESH"})
            request = request_for(decision)
        self.assertEqual(sorted(intents_by_market(request)), ["KRW-XRP"])
        self.assertIn(
            "MARKET_ACTION_CAPPED:KRW-ETH:MARKET_REALTIME_FRESHNESS_NOT_FRESH:KRW-ETH:MISSING", request["blockers"],
        )

    def test_market_orderbook_channel_not_fresh_blocks_that_market_only(self):
        def eth_orderbook_stale(run):
            for row in run["status"]["markets"]:
                if row["market"] == "KRW-ETH":
                    row["freshness_by_kind"]["orderbook"]["status"] = "STALE"

        entry = self.realtime_entry(eth_orderbook_stale)
        with per_market_effective(), actionable_upstream(["KRW-ETH"]):
            decision = replay(realtime_entry=entry)
            request = request_for(decision)
            self.assertEqual(request["blockers"], [
                "ENTRY_SNAPSHOT_UNAVAILABLE:KRW-ETH:REALTIME_ORDERBOOK_NOT_FRESH:KRW-ETH",
            ])
            carried = request_for(decision, account_state=account(["KRW-SOL"]))
        self.assertEqual([row["market"] for row in carried["match_snapshots"]], ["KRW-SOL"])

    def test_carried_match_requires_the_markets_ticker_channel(self):
        """A fill needs a FRESH mark next; a lagging ticker channel blocks the match."""
        def sol_ticker_channel_stale(run):
            for row in run["status"]["markets"]:
                if row["market"] == "KRW-SOL":
                    row["freshness_by_kind"]["ticker"]["status"] = "STALE"

        entry = self.realtime_entry(sol_ticker_channel_stale)
        with per_market_effective():
            decision = replay(realtime_entry=entry)
            self.assertEqual(BRIDGE.market_realtime_status(decision, "KRW-SOL")[0], DECISION.FRESH)
            BRIDGE.orderbook_snapshot(decision, market="KRW-SOL")  # the book itself is usable
            request = request_for(decision, account_state=account(["KRW-SOL", "KRW-XRP"]), with_config=False)
        self.assertEqual([row["market"] for row in request["match_snapshots"]], ["KRW-XRP"])
        self.assertIn("MATCH_SNAPSHOT_UNAVAILABLE:KRW-SOL:REALTIME_TICKER_NOT_FRESH:KRW-SOL", request["blockers"])

    def test_carried_orders_match_per_market(self):
        with per_market_effective():
            decision = replay()
            request = request_for(decision, account_state=account(["KRW-BTC", "KRW-WLD"]), with_config=False)
        self.assertEqual(request["status"], "PAPER_MATCHES_READY")
        self.assertEqual([row["market"] for row in request["match_snapshots"]], ["KRW-BTC"])
        self.assertIn(
            "MATCH_SNAPSHOT_UNAVAILABLE:KRW-WLD:MARKET_REALTIME_NOT_FRESH:KRW-WLD:STALE:"
            "PROVIDER_AGE_EXCEEDED,TRANSPORT_DELAY_EXCEEDED",
            request["blockers"],
        )

    def test_tampered_per_market_request_fields_fail_rederivation(self):
        with per_market_effective():
            request = request_for(replay(), with_config=False)
            for mutate, code in (
                (lambda value: value["market_status"]["KRW-WLD"].update(entry_state=BRIDGE.ENTRY_OPEN), "DERIVATION_MISMATCH"),
                (lambda value: value.update(freshness_mode=BRIDGE.AGGREGATE_FRESHNESS_MODE), "DERIVATION_MISMATCH"),
                (lambda value: value.update(schema_version=BRIDGE.LEGACY_REQUEST_SCHEMA_VERSION), "FIELDS_INVALID"),
                (lambda value: value.pop("market_status"), "FIELDS_INVALID"),
            ):
                forged = copy.deepcopy(request)
                mutate(forged)
                forged["packet_sha256"] = BRIDGE.payload_sha256({k: v for k, v in forged.items() if k != "packet_sha256"})
                with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, code):
                    BRIDGE.validate_runtime_request(forged)


class LegacyCompatibilityTests(unittest.TestCase):
    def test_v1_decision_under_request_v3_keeps_aggregate_gate(self):
        decision = natural_packet()
        self.assertEqual(decision["schema_version"], V1)
        request = request_for(decision, account_state=account(["KRW-BTC"]), with_config=False)
        self.assertEqual(request["freshness_mode"], BRIDGE.AGGREGATE_FRESHNESS_MODE)
        self.assertEqual(request["market_status"], {})
        self.assertEqual(request["match_snapshots"], [])
        self.assertIn(
            "MATCH_SNAPSHOT_UNAVAILABLE:KRW-BTC:DECISION_REALTIME_FRESHNESS_NOT_RATIFIED_FRESH", request["blockers"],
        )

    def test_issued_v2_requests_rebuild_byte_identically_and_revalidate(self):
        scenarios = {
            "v1_decision_open_btc_order": (natural_packet, contextlib.nullcontext),
        }
        for name, (decision_factory, context) in scenarios.items():
            with self.subTest(name), context():
                decision = decision_factory()
                legacy = BRIDGE._derive_runtime_request(
                    decision, expected_source_commit=decision["source_commit"],
                    public_code_commit_sha=CODE_COMMIT, observation_commit_sha=OBSERVATION_COMMIT,
                    account_state=account(["KRW-BTC"]), open_position_risk=[], runtime_config=None,
                    request_schema_version=BRIDGE.LEGACY_REQUEST_SCHEMA_VERSION,
                )
                self.assertEqual(normalized_request_sha256(legacy), LEGACY_V2_GOLDEN[name])
                self.assertEqual(set(legacy), set(BRIDGE.LEGACY_REQUEST_FIELDS))
                self.assertEqual(legacy["match_snapshots"], [])
                self.assertEqual(BRIDGE.validate_runtime_request(legacy), legacy)
                forged = copy.deepcopy(legacy)
                forged["blockers"] = []
                forged["packet_sha256"] = BRIDGE.payload_sha256({k: v for k, v in forged.items() if k != "packet_sha256"})
                with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "DERIVATION_MISMATCH"):
                    BRIDGE.validate_runtime_request(forged)


    def test_legacy_request_over_a_per_market_decision_is_rejected(self):
        with per_market_effective():
            decision = replay()
            with self.assertRaisesRegex(
                BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_DECISION",
            ):
                BRIDGE._derive_runtime_request(
                    decision, expected_source_commit=decision["source_commit"],
                    public_code_commit_sha=CODE_COMMIT, observation_commit_sha=OBSERVATION_COMMIT,
                    account_state=account(["KRW-BTC"]), open_position_risk=[], runtime_config=None,
                    request_schema_version=BRIDGE.LEGACY_REQUEST_SCHEMA_VERSION,
                )
            # A /3 request relabelled /2 (legacy field set, rehashed) is rejected too.
            request = request_for(decision, account_state=account(["KRW-BTC"]), with_config=False)
            forged = {k: v for k, v in request.items() if k in BRIDGE.LEGACY_REQUEST_FIELDS}
            forged["schema_version"] = BRIDGE.LEGACY_REQUEST_SCHEMA_VERSION
            forged["packet_sha256"] = BRIDGE.payload_sha256({k: v for k, v in forged.items() if k != "packet_sha256"})
            with self.assertRaisesRegex(
                BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_DECISION",
            ):
                BRIDGE.validate_runtime_request(forged)


class PerMarketTamperAbortTests(ModifiedRunFixture):
    """Tampered per-market evidence aborts; only unavailable evidence is a blocker."""

    def tampered(self, key):
        def mutate(run):
            run["latest_public_messages"][key]["source_sha256"] = "0" * 64
        return self.realtime_entry(mutate)

    def test_tampered_ticker_aborts_marks(self):
        entry = self.tampered("ticker|-|KRW-ETH")
        with per_market_effective():
            decision = replay(realtime_entry=entry)
            self.assertEqual(BRIDGE.market_realtime_status(decision, "KRW-ETH")[0], DECISION.FRESH)
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "REALTIME_TICKER_SHA_MISMATCH:KRW-ETH"):
                BRIDGE.latest_mark_prices_by_market(decision, ["KRW-BTC", "KRW-ETH"])

    def test_tampered_orderbook_aborts_entry_request(self):
        entry = self.tampered("orderbook|-|KRW-ETH")
        with per_market_effective(), actionable_upstream(["KRW-ETH"]):
            decision = replay(realtime_entry=entry)
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "REALTIME_ORDERBOOK_SHA_MISMATCH:KRW-ETH"):
                request_for(decision)

    def test_tampered_orderbook_aborts_carried_match_request(self):
        entry = self.tampered("orderbook|-|KRW-ETH")
        with per_market_effective():
            decision = replay(realtime_entry=entry)
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "REALTIME_ORDERBOOK_SHA_MISMATCH:KRW-ETH"):
                request_for(decision, account_state=account(["KRW-BTC", "KRW-ETH"]), with_config=False)


class MutationProofTests(ModifiedRunFixture):
    """Each mutation must break an invariant asserted above."""

    def test_restoring_the_global_aggregate_gate_is_detected(self):
        checker = PerMarketRequestTests("test_fresh_market_intent_while_aggregate_and_run_status_are_stale")
        with (
            per_market_effective(), actionable_upstream(["KRW-ETH"]),
            mock.patch.object(BRIDGE, "is_per_market_decision", return_value=False),
        ):
            request = request_for(replay())
        self.assertIn("ENTRY_SNAPSHOT_UNAVAILABLE:KRW-ETH:DECISION_REALTIME_FRESHNESS_NOT_RATIFIED_FRESH", request["blockers"])
        with self.assertRaises(AssertionError):
            checker.assert_fresh_market_intent_despite_stale_aggregate(request)

    def test_emitting_an_intent_for_a_capped_market_is_detected(self):
        checker = PerMarketRequestTests("test_capped_fresh_market_gets_no_intent_while_other_market_opens")
        with (
            per_market_effective(), btc_below_liquidity_floor(), actionable_upstream(["KRW-BTC"]),
            mock.patch.object(BRIDGE, "_per_market_entry_blocker", return_value=None),
        ):
            request = request_for(replay())
        with self.assertRaises(AssertionError):
            checker.assert_capped_market_has_no_intent(request)

    def test_unmutated_invariants_hold(self):
        checker = PerMarketRequestTests("test_capped_fresh_market_gets_no_intent_while_other_market_opens")
        with per_market_effective(), btc_below_liquidity_floor(), actionable_upstream(["KRW-BTC"]):
            request = request_for(replay())
        checker.assert_capped_market_has_no_intent(request)

    def test_bridge_stays_offline(self):
        source = (ROOT / "shadow" / "crypto_paper_runtime_bridge.py").read_text(encoding="utf-8")
        for forbidden in (
            "import requests", "urllib.request", "websockets", "import socket",
            "/v1/orders", "/v1/withdraws", "API_KEY", "SECRET_KEY",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
