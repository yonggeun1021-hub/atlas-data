#!/usr/bin/env python3
"""D1: per-market marks in the P10-11 account view (``crypto_paper_account_state/2``).

Design note: docs/crypto_paper_per_market_account_marks.md.  A held position
whose market has no FRESH mark is valued UNKNOWN (null value, null account
NAV) instead of making the whole account view unbuildable, so exits and
stop-losses of FRESH markets proceed while the stale market is a HOLD.  New
entries still need a NAV and wait.  The /1 view and contract are unchanged.
"""
from __future__ import annotations

import contextlib
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PM = load("d1_bridge_per_market_fixtures", ROOT / "test" / "test_crypto_paper_runtime_bridge_per_market.py")
BRIDGE = PM.BRIDGE
SIM = BRIDGE.SIMULATOR
MANAGER = load("d1_exit_manager", ROOT / "portfolio" / "crypto_paper_exit_manager.py")
V2 = SIM.PER_MARKET_ACCOUNT_STATE_SCHEMA_VERSION


def held_ledger(markets=("KRW-BTC", "KRW-WLD")):
    ledger = SIM.create_ledger(
        ledger_id="PAPER.LEDGER.PER.MARKET.TEST", initial_cash="100000000",
        opened_at="2026-09-13T20:00:00Z", idempotency_key="PAPER.ACCOUNT.OPEN.PER.MARKET.TEST",
    )
    for index, market in enumerate(markets):
        intent = SIM.build_intent(
            order_id=f"PAPER.BUY.{market}.D1", idempotency_key=f"PAPER.BUY.SUBMIT.{market}.D1",
            market=market, side="BUY", order_type="MARKET", quantity="2", limit_price=None,
            fee_rate="0", queue_fraction="1", submitted_at=f"2026-09-13T20:1{index}:00Z",
            expires_at="2026-09-13T21:00:00Z", market_regime_status="UNKNOWN",
            source_plan_ref="test://plan/d1", source_plan_sha256="b" * 64,
            source_evidence_ref="test://book/d1", source_evidence_sha256="c" * 64,
        )
        ledger = SIM.submit_order(ledger, intent)
        book = SIM.build_snapshot(
            snapshot_id=f"PAPER.D1.BOOK.{market}", market=market, captured_at=f"2026-09-13T20:1{index}:30Z",
            freshness_status="FRESH", ask_levels=[{"price": "100", "quantity": "2"}],
            bid_levels=[{"price": "99", "quantity": "2"}], source_ref="test://book/d1", source_sha256="c" * 64,
        )
        ledger = SIM.match_order(
            ledger, order_id=intent["order_id"], snapshot=book,
            event_at=f"2026-09-13T20:1{index}:31Z", idempotency_key=f"PAPER.D1.MATCH.{market}",
        )
    return ledger


def per_market_account(ledger, *, observed_at="2026-09-13T21:12:24Z", btc="90", wld=None, sha="d" * 64):
    marks = {"KRW-BTC": btc}
    status = {"KRW-BTC": "FRESH", "KRW-WLD": "UNKNOWN"}
    if wld is not None:
        marks["KRW-WLD"] = wld
        status["KRW-WLD"] = "FRESH"
    return SIM.build_account_state_per_market(
        ledger, observed_at=observed_at, mark_prices=marks, mark_status=status,
        mark_source_ref="test://marks/d1", mark_source_sha256=sha,
    )


def rehashed(value):
    value = copy.deepcopy(value)
    value.pop("packet_sha256")
    value["packet_sha256"] = SIM.payload_sha256(value)
    return value


class PerMarketAccountViewTests(unittest.TestCase):
    def test_unknown_market_is_valued_unknown_and_fresh_market_is_marked(self):
        account = per_market_account(held_ledger())
        self.assertEqual(account["schema_version"], V2)
        self.assertEqual(account["source"]["mark_freshness_status"], "PER_MARKET")
        rows = {row["market"]: row for row in account["positions"]}
        self.assertEqual(rows["KRW-BTC"]["mark_status"], "FRESH")
        self.assertEqual(rows["KRW-BTC"]["market_value"], "180")
        self.assertEqual(rows["KRW-WLD"], {
            "market": "KRW-WLD", "quantity": "2", "cost_basis": "200", "average_cost": "100",
            "mark_status": "UNKNOWN", "mark_price": None, "market_value": None,
            "unrealized_pnl": None, "realized_pnl": "0",
        })
        self.assertEqual(account["mark_prices"], {"KRW-BTC": "90"})
        self.assertIsNone(account["total_nav"])
        self.assertIsNone(account["position_market_value"])
        self.assertIsNone(account["unrealized_pnl"])
        self.assertEqual(account["cash"], "99999600")
        self.assertEqual(SIM.validate_account_state(account), account)

    def test_all_fresh_per_market_view_matches_the_v1_totals(self):
        ledger = held_ledger()
        v2 = per_market_account(ledger, wld="110")
        v1 = SIM.build_account_state(
            ledger, observed_at="2026-09-13T21:12:24Z", mark_prices={"KRW-BTC": "90", "KRW-WLD": "110"},
            mark_freshness_status="FRESH", mark_source_ref="test://marks/d1", mark_source_sha256="d" * 64,
        )
        for field in ("cash", "position_market_value", "total_nav", "unrealized_pnl", "realized_pnl", "mark_prices"):
            self.assertEqual(v2[field], v1[field], field)
        self.assertEqual(v1["schema_version"], "crypto_paper_account_state/1")
        self.assertNotIn("mark_status", v1["positions"][0])

    def test_inconsistent_mark_status_or_price_fails_closed(self):
        ledger = held_ledger()
        for kwargs, code in (
            ({"mark_prices": {"KRW-BTC": "90", "KRW-WLD": "1"}, "mark_status": {"KRW-BTC": "FRESH", "KRW-WLD": "UNKNOWN"}}, "ACCOUNT_MARK_PRICES_STATUS_MISMATCH"),
            ({"mark_prices": {"KRW-BTC": "90"}, "mark_status": {"KRW-BTC": "FRESH"}}, "ACCOUNT_MARK_STATUS_INVALID"),
            ({"mark_prices": {"KRW-BTC": "90"}, "mark_status": {"KRW-BTC": "FRESH", "KRW-WLD": "STALE"}}, "ACCOUNT_MARK_STATUS_INVALID"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(SIM.CryptoPaperSimulatorError, code):
                SIM.build_account_state_per_market(
                    ledger, observed_at="2026-09-13T21:12:24Z", mark_source_ref="test://marks/d1",
                    mark_source_sha256="d" * 64, **kwargs,
                )
        account = per_market_account(ledger)
        forged = copy.deepcopy(account)
        forged["total_nav"] = "100000000"
        with self.assertRaisesRegex(SIM.CryptoPaperSimulatorError, "ACCOUNT_STATE_DERIVATION_MISMATCH"):
            SIM.validate_account_state(rehashed(forged))
        forged = copy.deepcopy(account)
        del forged["positions"][1]["mark_status"]
        with self.assertRaisesRegex(SIM.CryptoPaperSimulatorError, "POSITION_MARK_STATUS_MISSING"):
            SIM.validate_account_state(rehashed(forged))


class ExitManagerPerMarketTests(unittest.TestCase):
    def plan(self, ledger, market):
        entry = per_market_account(ledger, observed_at="2026-09-13T21:00:00Z", btc="100")
        identity = market.replace("-", "")
        return MANAGER.build_exit_plan(
            plan_id=f"PAPER.EXIT.PLAN.{identity}", market=market, source_entry_order_id=f"PAPER.BUY.{market}.D1",
            created_at=entry["observed_at"], source_entry_account=entry,
            source_entry_plan_ref="test://plan/d1", source_entry_plan_sha256="b" * 64,
            triggers=[{
                "trigger_id": f"TRIGGER.STOP.{identity}", "category": "HARD_EXIT", "condition": "PRICE_AT_OR_BELOW",
                "threshold": "95", "action": "EXIT_REVIEW", "quantity_fraction": "1",
                "paper_order_id": f"PAPER.EXIT.STOP.{identity}", "paper_order_idempotency_key": f"PAPER.EXIT.STOP.SUBMIT.{identity}",
            }],
        )

    def observation(self, market, price, freshness, sha="d" * 64):
        return MANAGER.build_observation(
            observation_id=f"PAPER.EXIT.OBS.{market.replace('-', '')}", market=market,
            observed_at="2026-09-13T21:12:24Z", current_price=price, prior_high_watermark="100",
            freshness_status=freshness,
            signals={"kill_switch": "UNKNOWN", "security": "UNKNOWN", "liquidity": "UNKNOWN",
                     "risk_budget": "UNKNOWN", "regime": "NOT_EVALUATED", "trend": "UNKNOWN"},
            source_ref="test://marks/d1", source_sha256=sha,
        )

    def test_fresh_market_stop_fires_while_another_held_market_is_unknown(self):
        ledger = held_ledger()
        account = per_market_account(ledger)  # BTC 90 (below the 95 stop), WLD UNKNOWN
        decision = MANAGER.evaluate_exit(self.plan(ledger, "KRW-BTC"), account, self.observation("KRW-BTC", "90", "FRESH"))
        self.assertEqual(decision["status"], "TRIGGER_SELECTED_REVIEW_ONLY")
        self.assertEqual(decision["target_quantity"], "2")
        self.assertEqual(decision["paper_order_identity_candidate"]["side"], "SELL")
        self.assertEqual(MANAGER.validate_output(decision), decision)

    def test_unknown_market_can_only_wait(self):
        ledger = held_ledger()
        account = per_market_account(ledger)
        plan = self.plan(ledger, "KRW-WLD")
        with self.assertRaisesRegex(MANAGER.CryptoPaperExitManagerError, "ACCOUNT_POSITION_MARK_UNKNOWN_FOR_FRESH_OBSERVATION"):
            MANAGER.evaluate_exit(plan, account, self.observation("KRW-WLD", "90", "FRESH"))
        waited = MANAGER.evaluate_exit(plan, account, self.observation("KRW-WLD", "90", "STALE"))
        self.assertEqual(waited["status"], "WAIT_STALE_EVIDENCE")
        self.assertIsNone(waited["paper_order_identity_candidate"])

    def test_unknown_market_stale_price_never_advances_the_high_watermark(self):
        ledger = held_ledger()
        account = per_market_account(ledger)
        plan = self.plan(ledger, "KRW-WLD")
        for freshness in ("STALE", "UNKNOWN"):
            with self.subTest(freshness=freshness):
                waited = MANAGER.evaluate_exit(plan, account, self.observation("KRW-WLD", "1000", freshness))
                self.assertEqual(waited["status"], "WAIT_STALE_EVIDENCE")
                self.assertEqual(waited["prior_high_watermark"], "100")
                self.assertEqual(waited["next_high_watermark"], "100")
                self.assertEqual(MANAGER.validate_output(waited), waited)
                forged = copy.deepcopy(waited)
                forged["next_high_watermark"] = "1000"
                forged.pop("packet_sha256")
                forged["packet_sha256"] = MANAGER.payload_sha256(forged)
                with self.assertRaisesRegex(MANAGER.CryptoPaperExitManagerError, "OUTPUT_DERIVATION_MISMATCH"):
                    MANAGER.validate_output(forged)
        # A FRESH-marked market in the same /2 view still advances with its mark.
        fresh = per_market_account(ledger, btc="120")
        advanced = MANAGER.evaluate_exit(self.plan(ledger, "KRW-BTC"), fresh, self.observation("KRW-BTC", "120", "FRESH"))
        self.assertEqual(advanced["next_high_watermark"], "120")

    def test_contract_names_both_account_views_and_the_unknown_mark_policy(self):
        contract = MANAGER.load_contract()
        self.assertEqual(contract["source_account_schema_version"], "crypto_paper_account_state/1")
        self.assertEqual(contract["per_market_source_account_schema_version"], V2)
        self.assertEqual(
            contract["unknown_mark_position_policy"],
            "UNKNOWN_MARK_POSITION_ACCEPTS_ONLY_NON_FRESH_OBSERVATION_WAIT_STALE_EVIDENCE_HIGH_WATERMARK_NOT_ADVANCED",
        )
        drifted = copy.deepcopy(contract)
        drifted["per_market_source_account_schema_version"] = "crypto_paper_account_state/3"
        with self.assertRaisesRegex(MANAGER.CryptoPaperExitManagerError, "CONTRACT_FIELD_MISMATCH"):
            MANAGER._validate_contract(drifted)


class BridgeLegacyRequestAccountTests(unittest.TestCase):
    def test_legacy_v2_request_rejects_a_per_market_v2_account(self):
        decision = PM.natural_packet()
        ledger = held_ledger()
        for name, account in (("unknown", per_market_account(ledger)), ("all_fresh", per_market_account(ledger, wld="110"))):
            with self.subTest(account=name):
                self.assertEqual(account["schema_version"], V2)
                with self.assertRaisesRegex(
                    BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_ACCOUNT",
                ):
                    BRIDGE._derive_runtime_request(
                        decision, expected_source_commit=decision["source_commit"],
                        public_code_commit_sha=PM.CODE_COMMIT, observation_commit_sha=PM.OBSERVATION_COMMIT,
                        account_state=account, open_position_risk=[], runtime_config=None,
                        request_schema_version=BRIDGE.LEGACY_REQUEST_SCHEMA_VERSION,
                    )
        # A /3 request over the same /2 account is still derivable.
        request = BRIDGE._derive_runtime_request(
            decision, expected_source_commit=decision["source_commit"],
            public_code_commit_sha=PM.CODE_COMMIT, observation_commit_sha=PM.OBSERVATION_COMMIT,
            account_state=per_market_account(ledger), open_position_risk=[], runtime_config=None,
        )
        self.assertEqual(request["schema_version"], BRIDGE.REQUEST_SCHEMA_VERSION)

    def test_issued_legacy_request_with_a_swapped_in_v2_account_fails_closed(self):
        decision = PM.natural_packet()
        legacy = BRIDGE._derive_runtime_request(
            decision, expected_source_commit=decision["source_commit"],
            public_code_commit_sha=PM.CODE_COMMIT, observation_commit_sha=PM.OBSERVATION_COMMIT,
            account_state=PM.account(["KRW-BTC"]), open_position_risk=[], runtime_config=None,
            request_schema_version=BRIDGE.LEGACY_REQUEST_SCHEMA_VERSION,
        )
        self.assertEqual(BRIDGE.validate_runtime_request(legacy), legacy)
        forged = copy.deepcopy(legacy)
        forged["source_inputs"]["account_state"] = per_market_account(held_ledger(), wld="110")
        forged["packet_sha256"] = BRIDGE.payload_sha256({k: v for k, v in forged.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(
            BRIDGE.CryptoPaperRuntimeBridgeError, "RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_ACCOUNT",
        ):
            BRIDGE.validate_runtime_request(forged)


class BridgePerMarketAccountTests(PM.ModifiedRunFixture):
    def test_unknown_valued_position_blocks_entries_but_not_carried_matches(self):
        ledger = held_ledger()
        intent = SIM.build_intent(
            order_id="PAPER.BUY.KRW-XRP.D1.CARRIED", idempotency_key="PAPER.BUY.SUBMIT.KRW-XRP.D1.CARRIED",
            market="KRW-XRP", side="BUY", order_type="LIMIT", quantity="1", limit_price="1",
            fee_rate="0", queue_fraction="1", submitted_at="2026-09-13T21:00:00Z", expires_at="2026-09-13T22:00:00Z",
            market_regime_status="UNKNOWN", source_plan_ref="test://plan/d1", source_plan_sha256="b" * 64,
            source_evidence_ref="test://book/d1", source_evidence_sha256="c" * 64,
        )
        carried = SIM.submit_order(ledger, intent)
        risk = [{"market": "KRW-BTC", "planned_loss_krw": "10"}, {"market": "KRW-WLD", "planned_loss_krw": "10"}]
        with PM.per_market_effective(), PM.actionable_upstream(["KRW-ETH"]):
            decision = PM.replay()
            for ledger_value, expected_status in ((ledger, "WAIT_ACCOUNT_NAV_UNKNOWN"), (carried, "PAPER_MATCHES_READY")):
                account = per_market_account(ledger_value)
                request = BRIDGE.build_runtime_request(
                    decision, expected_source_commit=decision["source_commit"],
                    public_code_commit_sha=PM.CODE_COMMIT, observation_commit_sha=PM.OBSERVATION_COMMIT,
                    account_state=account, open_position_risk=risk, runtime_config=PM.config(),
                )
                self.assertEqual(request["status"], expected_status)
                self.assertEqual(request["requests"], [])
                self.assertIn("PAPER_ACCOUNT_NAV_UNKNOWN:KRW-WLD", request["blockers"])
                self.assertIsNone(request["eligibility"])
                self.assertEqual(BRIDGE.validate_runtime_request(request), request)
            self.assertEqual([row["market"] for row in request["match_snapshots"]], ["KRW-XRP"])
            with self.assertRaisesRegex(BRIDGE.CryptoPaperRuntimeBridgeError, "PAPER_ACCOUNT_NAV_UNKNOWN"):
                BRIDGE.paper_account_state_from_ledger(per_market_account(ledger), open_position_risk=risk)


if __name__ == "__main__":
    unittest.main()
