#!/usr/bin/env python3
"""P7-13 Crypto PAPER exit and position-management regression."""
from __future__ import annotations

import copy
import importlib.util
import inspect
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "crypto_paper_exit_manager.py"
SPEC = importlib.util.spec_from_file_location("crypto_paper_exit_manager", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
SIM = MODULE.SIMULATOR
CONTRACT = MODULE.load_contract()


def sim_intent(
    *, order_id="PAPER.ENTRY.1", key="PAPER.ENTRY.SUBMIT.1", side="BUY",
    quantity="2", submitted_at="2026-08-29T01:30:00Z",
    expires_at="2026-08-29T02:30:00Z", regime="UNKNOWN",
):
    return SIM.build_intent(
        order_id=order_id,
        idempotency_key=key,
        market="KRW-BTC",
        side=side,
        order_type="MARKET",
        quantity=quantity,
        limit_price=None,
        fee_rate="0",
        queue_fraction="1",
        submitted_at=submitted_at,
        expires_at=expires_at,
        market_regime_status=regime,
        source_plan_ref=f"test://entry-plan/{order_id}",
        source_plan_sha256="a" * 64,
        source_evidence_ref="test://entry-evidence/KRW-BTC",
        source_evidence_sha256="b" * 64,
    )


def book(
    *, snapshot_id="SNAPSHOT.ENTRY.1", captured_at="2026-08-29T01:31:00Z",
    asks=None, bids=None, source_sha="c" * 64,
):
    return SIM.build_snapshot(
        snapshot_id=snapshot_id,
        market="KRW-BTC",
        captured_at=captured_at,
        freshness_status="FRESH",
        ask_levels=asks or [
            {"price": "100", "quantity": "1"},
            {"price": "101", "quantity": "2"},
        ],
        bid_levels=bids or [
            {"price": "99", "quantity": "1"},
            {"price": "98", "quantity": "2"},
        ],
        source_ref=f"test://book/{snapshot_id}",
        source_sha256=source_sha,
    )


def bought_ledger():
    ledger = SIM.create_ledger(
        ledger_id="PAPER.EXIT.TEST",
        initial_cash="1000",
        opened_at="2026-08-29T01:00:00Z",
        idempotency_key="PAPER.ACCOUNT.OPEN",
    )
    ledger = SIM.submit_order(ledger, sim_intent())
    return SIM.match_order(
        ledger,
        order_id="PAPER.ENTRY.1",
        snapshot=book(),
        event_at="2026-08-29T01:31:01Z",
        idempotency_key="PAPER.ENTRY.MATCH.1",
    )


def account(
    ledger=None, *, observed_at="2026-08-29T01:40:00Z", price="110",
    source_sha="d" * 64,
):
    return SIM.build_account_state(
        ledger or bought_ledger(),
        observed_at=observed_at,
        mark_prices={"KRW-BTC": price},
        mark_freshness_status="FRESH",
        mark_source_ref=f"test://mark/{observed_at}",
        mark_source_sha256=source_sha,
    )


def trigger(
    trigger_id, category, condition, action, *, threshold=None,
    fraction=None, order_id=None, key=None,
):
    return {
        "trigger_id": trigger_id,
        "category": category,
        "condition": condition,
        "threshold": threshold,
        "action": action,
        "quantity_fraction": fraction,
        "paper_order_id": order_id,
        "paper_order_idempotency_key": key,
    }


def default_triggers():
    return [
        trigger(
            "TRIGGER.STOP", "HARD_EXIT", "PRICE_AT_OR_BELOW", "EXIT_REVIEW",
            threshold="90", fraction="1", order_id="PAPER.EXIT.STOP",
            key="PAPER.EXIT.STOP.SUBMIT",
        ),
        trigger(
            "TRIGGER.HARVEST", "PROFIT_TRAIL", "PRICE_AT_OR_ABOVE", "HARVEST_PARTIAL",
            threshold="120", fraction="0.5", order_id="PAPER.EXIT.HARVEST",
            key="PAPER.EXIT.HARVEST.SUBMIT",
        ),
        trigger(
            "TRIGGER.TIME", "TIME_REVIEW", "TIME_AT_OR_AFTER", "EXIT_REVIEW",
            threshold="2026-08-29T03:00:00Z", fraction="1", order_id="PAPER.EXIT.TIME",
            key="PAPER.EXIT.TIME.SUBMIT",
        ),
    ]


def plan(*, triggers=None, entry_account=None):
    entry_account = entry_account or account(
        observed_at="2026-08-29T01:32:00Z", price="101", source_sha="e" * 64,
    )
    return MODULE.build_exit_plan(
        plan_id="PAPER.EXIT.PLAN.1",
        market="KRW-BTC",
        source_entry_order_id="PAPER.ENTRY.1",
        created_at=entry_account["observed_at"],
        triggers=default_triggers() if triggers is None else triggers,
        source_entry_account=entry_account,
        source_entry_plan_ref="test://entry-plan/PAPER.ENTRY.1",
        source_entry_plan_sha256="a" * 64,
    )


def signals(**changes):
    value = {
        "kill_switch": "CLEAR",
        "security": "CLEAR",
        "liquidity": "CLEAR",
        "risk_budget": "CLEAR",
        "regime": "UNKNOWN",
        "trend": "INTACT",
    }
    value.update(changes)
    return value


def observation(
    *, observed_at="2026-08-29T01:40:00Z", price="110", prior_high="115",
    freshness="FRESH", signal_values=None, source_sha="d" * 64,
):
    return MODULE.build_observation(
        observation_id=f"PAPER.EXIT.OBS.{observed_at.replace(':', '')}",
        market="KRW-BTC",
        observed_at=observed_at,
        current_price=price,
        prior_high_watermark=prior_high,
        freshness_status=freshness,
        signals=signals() if signal_values is None else signal_values,
        source_ref=f"test://mark/{observed_at}",
        source_sha256=source_sha,
    )


def evaluate(*, exit_plan=None, current_account=None, observed=None):
    observed = observed or observation()
    current_account = current_account or account(
        observed_at=observed["observed_at"],
        price=observed["current_price"],
        source_sha=observed["source_sha256"],
    )
    return MODULE.evaluate_exit(exit_plan or plan(), current_account, observed)


class ContractAndPlanTests(unittest.TestCase):
    def test_all_live_and_market_judgment_authority_is_false(self):
        self.assertTrue(CONTRACT["authority"]["paper_exit_review_only"])
        for field, value in CONTRACT["authority"].items():
            if field != "paper_exit_review_only":
                self.assertIs(value, False, field)

    def test_module_has_no_network_or_private_endpoint(self):
        text = SOURCE.read_text(encoding="utf-8")
        config = (ROOT / "config" / "crypto_paper_exit_manager_contract.json").read_text(encoding="utf-8")
        for forbidden in (
            "/v1/orders", "/v1/withdraws", "/v1/deposits", "Authorization",
            "api_key", "secret_key", "JWT", "requests.", "urllib.request",
            "websocket", "socket.",
        ):
            self.assertNotIn(forbidden, text)
            self.assertNotIn(forbidden, config)

    def test_plan_has_no_threshold_or_fraction_defaults(self):
        signature = inspect.signature(MODULE.build_exit_plan)
        self.assertIs(signature.parameters["triggers"].default, inspect.Parameter.empty)
        self.assertNotIn("stop_price", signature.parameters)
        self.assertNotIn("harvest_fraction", signature.parameters)

    def test_plan_binds_exact_entry_account_quantity_and_vwap(self):
        value = plan()
        self.assertEqual(value["initial_quantity"], "2")
        self.assertEqual(value["entry_price"], "100.5")
        self.assertEqual(
            value["source_entry_account"]["packet_sha256"],
            MODULE.SIMULATOR.validate_account_state(value["source_entry_account"])["packet_sha256"],
        )

    def test_plan_rejects_priority_inversion_duplicate_trigger_and_order_identity(self):
        inverted = list(reversed(default_triggers()))
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "PRIORITY_ORDER"):
            plan(triggers=inverted)
        duplicated = default_triggers()
        duplicated[1]["trigger_id"] = duplicated[0]["trigger_id"]
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "TRIGGER_ID_DUPLICATE"):
            plan(triggers=duplicated)
        duplicated_order = default_triggers()
        duplicated_order[1]["paper_order_id"] = duplicated_order[0]["paper_order_id"]
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "PAPER_ORDER_IDENTITY_DUPLICATE"):
            plan(triggers=duplicated_order)

    def test_plan_rejects_rehashed_entry_account_tamper(self):
        entry = account(observed_at="2026-08-29T01:32:00Z", price="101", source_sha="e" * 64)
        entry["cash"] = "999"
        entry["packet_sha256"] = SIM.payload_sha256({k: v for k, v in entry.items() if k != "packet_sha256"})
        with self.assertRaises(SIM.CryptoPaperSimulatorError):
            plan(entry_account=entry)


class EvaluationTests(unittest.TestCase):
    def test_no_trigger_is_explicit_hold_with_zero_quantity(self):
        result = evaluate()
        self.assertEqual(result["status"], "NO_TRIGGER_HOLD")
        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(result["target_quantity"], "0")
        self.assertIsNone(result["paper_order_identity_candidate"])

    def test_stop_selects_full_exit_review(self):
        observed = observation(price="90", prior_high="115")
        result = evaluate(observed=observed)
        self.assertEqual(result["status"], "TRIGGER_SELECTED_REVIEW_ONLY")
        self.assertEqual(result["action"], "EXIT_REVIEW")
        self.assertEqual(result["selected_trigger_id"], "TRIGGER.STOP")
        self.assertEqual(result["target_quantity"], "2")
        self.assertEqual(result["paper_order_identity_candidate"]["side"], "SELL")
        self.assertTrue(result["human_review_required"])

    def test_hard_exit_beats_profit_when_both_true(self):
        triggers = [
            trigger(
                "TRIGGER.KILL", "HARD_EXIT", "KILL_SWITCH_TRIGGERED", "EXIT_REVIEW",
                fraction="1", order_id="PAPER.EXIT.KILL", key="PAPER.EXIT.KILL.SUBMIT",
            ),
            trigger(
                "TRIGGER.PROFIT", "PROFIT_TRAIL", "PRICE_AT_OR_ABOVE", "HARVEST_PARTIAL",
                threshold="100", fraction="0.5", order_id="PAPER.EXIT.PROFIT",
                key="PAPER.EXIT.PROFIT.SUBMIT",
            ),
        ]
        result = evaluate(
            exit_plan=plan(triggers=triggers),
            observed=observation(price="120", prior_high="120", signal_values=signals(kill_switch="TRIGGERED")),
        )
        self.assertEqual(result["selected_trigger_id"], "TRIGGER.KILL")
        self.assertEqual(result["target_quantity"], "2")

    def test_unknown_planned_signal_waits_before_lower_profit(self):
        triggers = [
            trigger(
                "TRIGGER.REGIME", "RISK_REGIME", "REGIME_FAIL", "REDUCE",
                fraction="0.5", order_id="PAPER.EXIT.REGIME", key="PAPER.EXIT.REGIME.SUBMIT",
            ),
            trigger(
                "TRIGGER.PROFIT", "PROFIT_TRAIL", "PRICE_AT_OR_ABOVE", "HARVEST_PARTIAL",
                threshold="100", fraction="0.5", order_id="PAPER.EXIT.PROFIT",
                key="PAPER.EXIT.PROFIT.SUBMIT",
            ),
        ]
        result = evaluate(
            exit_plan=plan(triggers=triggers),
            observed=observation(price="120", prior_high="120", signal_values=signals(regime="UNKNOWN")),
        )
        self.assertEqual(result["status"], "WAIT_UNKNOWN_EVIDENCE")
        self.assertIsNone(result["action"])
        self.assertIsNone(result["target_quantity"])

    def test_unplanned_unknown_regime_is_preserved_but_not_interpreted(self):
        observed = observation(price="120", prior_high="120", signal_values=signals(regime="UNKNOWN"))
        result = evaluate(observed=observed)
        self.assertEqual(result["selected_trigger_id"], "TRIGGER.HARVEST")
        self.assertEqual(result["source_packets"]["observation"]["signals"]["regime"], "UNKNOWN")
        self.assertFalse(result["authority"]["market_judgment_authorized"])

    def test_stale_evidence_waits_and_does_not_call_it_hold(self):
        result = evaluate(observed=observation(price="90", freshness="STALE"))
        self.assertEqual(result["status"], "WAIT_STALE_EVIDENCE")
        self.assertIsNone(result["action"])
        self.assertIsNone(result["target_quantity"])

    def test_partial_harvest_quantity_is_from_entry_plan_and_capped_by_current_position(self):
        observed = observation(price="120", prior_high="120")
        result = evaluate(observed=observed)
        self.assertEqual(result["action"], "HARVEST_PARTIAL")
        self.assertEqual(result["target_quantity"], "1")
        self.assertEqual(result["paper_order_identity_candidate"]["order_id"], "PAPER.EXIT.HARVEST")

    def test_trailing_uses_prior_high_then_advances_high_watermark(self):
        triggers = [
            trigger(
                "TRIGGER.TRAIL", "PROFIT_TRAIL", "DRAWDOWN_FROM_PRIOR_HIGH_AT_OR_ABOVE",
                "EXIT_REVIEW", threshold="0.1", fraction="1",
                order_id="PAPER.EXIT.TRAIL", key="PAPER.EXIT.TRAIL.SUBMIT",
            )
        ]
        result = evaluate(
            exit_plan=plan(triggers=triggers),
            observed=observation(price="108", prior_high="120"),
        )
        self.assertEqual(result["selected_trigger_id"], "TRIGGER.TRAIL")
        self.assertEqual(result["prior_high_watermark"], "120")
        self.assertEqual(result["next_high_watermark"], "120")
        new_high = evaluate(
            exit_plan=plan(triggers=triggers),
            observed=observation(price="130", prior_high="120"),
        )
        self.assertEqual(new_high["status"], "NO_TRIGGER_HOLD")
        self.assertEqual(new_high["next_high_watermark"], "130")

    def test_time_review_uses_exact_caller_timestamp(self):
        observed = observation(observed_at="2026-08-29T03:00:00Z", price="110", source_sha="f" * 64)
        current = account(observed_at=observed["observed_at"], price="110", source_sha="f" * 64)
        result = evaluate(observed=observed, current_account=current)
        self.assertEqual(result["selected_trigger_id"], "TRIGGER.TIME")
        self.assertEqual(result["action"], "EXIT_REVIEW")

    def test_account_and_observation_must_be_same_exact_market_evidence(self):
        observed = observation()
        wrong_source = account(observed_at=observed["observed_at"], price="110", source_sha="f" * 64)
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "SOURCE_MISMATCH"):
            evaluate(observed=observed, current_account=wrong_source)
        wrong_price = account(observed_at=observed["observed_at"], price="111", source_sha=observed["source_sha256"])
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "PRICE_MISMATCH"):
            evaluate(observed=observed, current_account=wrong_price)

    def test_current_account_must_descend_from_exact_entry_ledger(self):
        observed = observation()
        other_ledger = copy.deepcopy(bought_ledger())
        other_ledger["ledger_id"] = "PAPER.EXIT.OTHER"
        other_ledger["packet_sha256"] = SIM.payload_sha256(
            {k: v for k, v in other_ledger.items() if k != "packet_sha256"}
        )
        other_account = account(
            other_ledger,
            observed_at=observed["observed_at"],
            price=observed["current_price"],
            source_sha=observed["source_sha256"],
        )
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "LEDGER_ID_MISMATCH"):
            evaluate(observed=observed, current_account=other_account)

    def test_output_rederivation_rejects_rehashed_action_or_quantity_tamper(self):
        value = evaluate(observed=observation(price="120", prior_high="120"))
        for field, changed in (("action", "HOLD"), ("target_quantity", "2")):
            tampered = copy.deepcopy(value)
            tampered[field] = changed
            tampered["packet_sha256"] = MODULE.payload_sha256(
                {k: v for k, v in tampered.items() if k != "packet_sha256"}
            )
            with self.subTest(field=field), self.assertRaisesRegex(
                MODULE.CryptoPaperExitManagerError, "OUTPUT_DERIVATION_MISMATCH"
            ):
                MODULE.validate_output(tampered)


class EndToEndPaperLifecycleTests(unittest.TestCase):
    def test_briefing_plan_equivalent_buy_to_exit_review_to_virtual_sell(self):
        buy_ledger = bought_ledger()
        observed = observation(price="120", prior_high="120")
        current = account(
            buy_ledger,
            observed_at=observed["observed_at"],
            price=observed["current_price"],
            source_sha=observed["source_sha256"],
        )
        decision = evaluate(current_account=current, observed=observed)
        identity = decision["paper_order_identity_candidate"]
        sell_intent = SIM.build_intent(
            order_id=identity["order_id"],
            idempotency_key=identity["idempotency_key"],
            market=identity["market"],
            side="SELL",
            order_type="MARKET",
            quantity=decision["target_quantity"],
            limit_price=None,
            fee_rate="0",
            queue_fraction="1",
            submitted_at=observed["observed_at"],
            expires_at="2026-08-29T02:40:00Z",
            market_regime_status=observed["signals"]["regime"],
            source_plan_ref="test://p7-exit-decision",
            source_plan_sha256=decision["packet_sha256"],
            source_evidence_ref=observed["source_ref"],
            source_evidence_sha256=observed["source_sha256"],
        )
        sell_ledger = SIM.submit_order(buy_ledger, sell_intent)
        sell_book = book(
            snapshot_id="SNAPSHOT.EXIT.HARVEST",
            captured_at="2026-08-29T01:41:00Z",
            bids=[{"price": "120", "quantity": "2"}],
            source_sha=observed["source_sha256"],
        )
        sell_ledger = SIM.match_order(
            sell_ledger,
            order_id=identity["order_id"],
            snapshot=sell_book,
            event_at="2026-08-29T01:41:01Z",
            idempotency_key="PAPER.EXIT.HARVEST.MATCH",
        )
        final = SIM.build_account_state(
            sell_ledger,
            observed_at="2026-08-29T01:42:00Z",
            mark_prices={"KRW-BTC": "120"},
            mark_freshness_status="FRESH",
            mark_source_ref="test://mark/final",
            mark_source_sha256="9" * 64,
        )
        self.assertEqual(final["positions"][0]["quantity"], "1")
        self.assertEqual(final["cash"], "919")
        exit_order = next(row for row in final["orders"] if row["order_id"] == "PAPER.EXIT.HARVEST")
        self.assertEqual(exit_order["status"], "FILLED")
        self.assertEqual(exit_order["filled_quantity"], "1")
        self.assertFalse(final["authority"]["exchange_order_authorized"])

        repeated_observation = observation(
            observed_at="2026-08-29T01:42:00Z", price="120", prior_high="120", source_sha="9" * 64,
        )
        repeated = evaluate(exit_plan=plan(), current_account=final, observed=repeated_observation)
        self.assertEqual(repeated["status"], "TRIGGER_ALREADY_APPLIED")
        self.assertIsNone(repeated["target_quantity"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
