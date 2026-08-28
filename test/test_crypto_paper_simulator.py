#!/usr/bin/env python3
"""P10-11 deterministic Crypto PAPER simulator and ledger regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "crypto_paper_simulator.py"
SPEC = importlib.util.spec_from_file_location("crypto_paper_simulator", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def intent(
    *, order_id="PAPER.ORDER.1", key="PAPER.SUBMIT.1", market="KRW-BTC",
    side="BUY", order_type="MARKET", quantity="2", limit_price=None,
    fee_rate="0.001", queue_fraction="1",
    submitted_at="2026-08-29T01:30:00Z", expires_at="2026-08-29T02:30:00Z",
    regime="UNKNOWN",
):
    return MODULE.build_intent(
        order_id=order_id,
        idempotency_key=key,
        market=market,
        side=side,
        order_type=order_type,
        quantity=quantity,
        limit_price=limit_price,
        fee_rate=fee_rate,
        queue_fraction=queue_fraction,
        submitted_at=submitted_at,
        expires_at=expires_at,
        market_regime_status=regime,
        source_plan_ref=f"test://plan/{order_id}",
        source_plan_sha256="a" * 64,
        source_evidence_ref=f"test://evidence/{market}",
        source_evidence_sha256="b" * 64,
    )


def snapshot(
    *, snapshot_id="SNAPSHOT.BTC.1", market="KRW-BTC",
    captured_at="2026-08-29T01:31:00Z", freshness="FRESH",
    asks=None, bids=None,
):
    return MODULE.build_snapshot(
        snapshot_id=snapshot_id,
        market=market,
        captured_at=captured_at,
        freshness_status=freshness,
        ask_levels=asks or [
            {"price": "100", "quantity": "1"},
            {"price": "101", "quantity": "2"},
        ],
        bid_levels=bids or [
            {"price": "99", "quantity": "1"},
            {"price": "98", "quantity": "2"},
        ],
        source_ref=f"test://orderbook/{snapshot_id}",
        source_sha256="c" * 64,
    )


def ledger(initial_cash="1000"):
    return MODULE.create_ledger(
        ledger_id="PAPER.LEDGER.TEST",
        initial_cash=initial_cash,
        opened_at="2026-08-29T01:00:00Z",
        idempotency_key="PAPER.ACCOUNT.OPEN",
    )


def submit(base=None, value=None):
    return MODULE.submit_order(base or ledger(), value or intent())


def match(base, *, order_id="PAPER.ORDER.1", snap=None, key="PAPER.MATCH.1", at="2026-08-29T01:31:01Z"):
    return MODULE.match_order(
        base,
        order_id=order_id,
        snapshot=snap or snapshot(),
        event_at=at,
        idempotency_key=key,
    )


def account(base, marks=None, observed_at="2026-08-29T01:40:00Z"):
    return MODULE.build_account_state(
        base,
        observed_at=observed_at,
        mark_prices=marks or {"KRW-BTC": "105"},
        mark_freshness_status="FRESH",
        mark_source_ref="test://marks/1",
        mark_source_sha256="d" * 64,
    )


class ContractAndAuthorityTests(unittest.TestCase):
    def test_contract_is_exact_and_all_real_authority_is_false(self):
        self.assertEqual(CONTRACT["mode"], "PAPER_LAB_ONLY")
        self.assertEqual(CONTRACT["market_judgment_policy"], "PRESERVE_INPUT_STATUS_NO_PROMOTION")
        authority = CONTRACT["authority"]
        self.assertTrue(authority["paper_simulation_only"])
        for field in (
            "network_access_authorized", "credential_access_authorized",
            "investment_eligibility_authorized", "action_authorized",
            "exchange_order_authorized", "broker_submission_authorized",
            "withdrawal_authorized", "production_authorized", "trading_authorized",
            "real_capital_authorized",
        ):
            self.assertIs(authority[field], False, field)

    def test_source_and_contract_have_no_network_private_or_order_endpoint(self):
        text = SOURCE.read_text(encoding="utf-8")
        config = (ROOT / "config" / "crypto_paper_simulator_contract.json").read_text(encoding="utf-8")
        for forbidden in (
            "/v1/orders", "/v1/withdraws", "/v1/deposits", "Authorization",
            "api_key", "secret_key", "JWT", "requests.", "urllib.request",
            "websocket", "socket.",
        ):
            self.assertNotIn(forbidden, text)
            self.assertNotIn(forbidden, config)

    def test_no_economic_default_is_hidden_in_build_intent_signature(self):
        import inspect
        signature = inspect.signature(MODULE.build_intent)
        for field in ("fee_rate", "queue_fraction", "quantity", "limit_price"):
            self.assertIs(signature.parameters[field].default, inspect.Parameter.empty)

    def test_unknown_market_regime_is_preserved_not_promoted(self):
        value = intent(regime="UNKNOWN")
        self.assertEqual(value["market_regime_status"], "UNKNOWN")
        state = account(match(submit(value=value)))
        self.assertEqual(state["orders"][0]["market_regime_status"], "UNKNOWN")
        self.assertFalse(state["authority"]["investment_eligibility_authorized"])


class PacketValidationTests(unittest.TestCase):
    def test_builders_are_deterministic_and_levels_are_price_sorted(self):
        first = intent()
        second = intent()
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        book = snapshot(
            asks=[{"price": "101", "quantity": "2"}, {"price": "100", "quantity": "1"}],
            bids=[{"price": "98", "quantity": "2"}, {"price": "99", "quantity": "1"}],
        )
        self.assertEqual([row["price"] for row in book["ask_levels"]], ["100", "101"])
        self.assertEqual([row["price"] for row in book["bid_levels"]], ["99", "98"])

    def test_noncanonical_or_invalid_cost_inputs_fail_closed(self):
        cases = [
            {"fee_rate": "0.0010"},
            {"fee_rate": "1"},
            {"queue_fraction": "0"},
            {"queue_fraction": "1.1"},
            {"quantity": "NaN"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(MODULE.CryptoPaperSimulatorError):
                intent(**kwargs)

    def test_market_intent_cannot_smuggle_limit_and_limit_requires_one(self):
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "MARKET_INTENT_LIMIT_PRICE_MUST_BE_NULL"):
            intent(order_type="MARKET", limit_price="100")
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "INTENT_LIMIT_PRICE_INVALID"):
            intent(order_type="LIMIT", limit_price=None)

    def test_rehashed_semantic_tamper_is_still_rejected(self):
        value = intent()
        value["authority"]["exchange_order_authorized"] = True
        value["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in value.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "INTENT_IDENTITY_INVALID"):
            MODULE.validate_intent(value)

    def test_snapshot_hash_tamper_and_duplicate_prices_fail(self):
        value = snapshot()
        value["ask_levels"][0]["quantity"] = "9"
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "SNAPSHOT_PACKET_SHA_MISMATCH"):
            MODULE.validate_snapshot(value)
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "PRICE_DUPLICATE"):
            snapshot(asks=[{"price": "100", "quantity": "1"}, {"price": "100", "quantity": "2"}])


class LifecycleTests(unittest.TestCase):
    def test_market_buy_fills_across_levels_and_account_reconciles(self):
        filled = match(submit())
        state = account(filled)
        order = state["orders"][0]
        position = state["positions"][0]
        self.assertEqual(order["status"], "FILLED")
        self.assertEqual(order["filled_quantity"], "2")
        self.assertEqual(order["gross_value"], "201")
        self.assertEqual(order["fees"], "0.201")
        self.assertEqual(state["cash"], "798.799")
        self.assertEqual(position["quantity"], "2")
        self.assertEqual(position["cost_basis"], "201.201")
        self.assertEqual(state["position_market_value"], "210")
        self.assertEqual(state["total_nav"], "1008.799")

    def test_queue_fraction_produces_partial_fill_then_second_snapshot_completes(self):
        base = submit(value=intent(queue_fraction="0.5"))
        first = match(base, snap=snapshot(snapshot_id="SNAPSHOT.BTC.1"))
        first_state = account(first)
        self.assertEqual(first_state["orders"][0]["status"], "PARTIALLY_FILLED")
        self.assertEqual(first_state["orders"][0]["filled_quantity"], "1.5")
        second = match(
            first,
            snap=snapshot(snapshot_id="SNAPSHOT.BTC.2", captured_at="2026-08-29T01:32:00Z"),
            key="PAPER.MATCH.2",
            at="2026-08-29T01:32:01Z",
        )
        self.assertEqual(account(second)["orders"][0]["status"], "FILLED")

    def test_limit_order_records_explicit_no_fill(self):
        base = submit(value=intent(order_type="LIMIT", limit_price="99"))
        result = match(base)
        self.assertEqual(result["events"][-1]["event_type"], "MATCH_EVALUATED_NO_FILL")
        self.assertEqual(account(result)["orders"][0]["status"], "OPEN")

    def test_buy_is_capped_by_virtual_cash_and_never_goes_negative(self):
        base = submit(ledger("100"), intent(quantity="10", fee_rate="0.01"))
        result = match(base)
        state = account(result)
        self.assertGreaterEqual(MODULE.Decimal(state["cash"]), MODULE.Decimal("0"))
        self.assertEqual(state["orders"][0]["status"], "PARTIALLY_FILLED")
        self.assertLess(MODULE.Decimal(state["orders"][0]["filled_quantity"]), MODULE.Decimal("1"))

    def test_buy_then_sell_updates_cash_position_and_realized_pnl(self):
        bought = match(submit(value=intent(quantity="2", fee_rate="0")))
        sell_intent = intent(
            order_id="PAPER.ORDER.2", key="PAPER.SUBMIT.2", side="SELL",
            quantity="2", fee_rate="0", submitted_at="2026-08-29T01:35:00Z",
            expires_at="2026-08-29T02:35:00Z",
        )
        submitted_sell = MODULE.submit_order(bought, sell_intent)
        sold = match(
            submitted_sell,
            order_id="PAPER.ORDER.2",
            snap=snapshot(
                snapshot_id="SNAPSHOT.BTC.SELL.1", captured_at="2026-08-29T01:36:00Z",
                bids=[{"price": "110", "quantity": "1"}, {"price": "109", "quantity": "2"}],
            ),
            key="PAPER.MATCH.SELL.1", at="2026-08-29T01:36:01Z",
        )
        state = account(sold, marks={})
        self.assertEqual(state["positions"], [])
        self.assertEqual(state["cash"], "1018")
        self.assertEqual(state["realized_pnl"], "18")

    def test_sell_quantity_is_capped_by_position(self):
        bought = match(submit(value=intent(quantity="1", fee_rate="0")))
        sell_intent = intent(
            order_id="PAPER.ORDER.2", key="PAPER.SUBMIT.2", side="SELL",
            quantity="2", fee_rate="0", submitted_at="2026-08-29T01:35:00Z",
            expires_at="2026-08-29T02:35:00Z",
        )
        base = MODULE.submit_order(bought, sell_intent)
        result = match(
            base, order_id="PAPER.ORDER.2",
            snap=snapshot(snapshot_id="SNAPSHOT.SELL.CAP", captured_at="2026-08-29T01:36:00Z"),
            key="PAPER.MATCH.SELL.CAP", at="2026-08-29T01:36:01Z",
        )
        state = account(result, marks={})
        sell_order = next(row for row in state["orders"] if row["order_id"] == "PAPER.ORDER.2")
        self.assertEqual(sell_order["filled_quantity"], "1")
        self.assertEqual(sell_order["status"], "PARTIALLY_FILLED")

    def test_exact_retry_is_noop_but_new_key_for_same_snapshot_is_blocked(self):
        base = submit()
        snap = snapshot()
        first = match(base, snap=snap)
        retry = match(first, snap=snap)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(retry))
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "SNAPSHOT_ALREADY_MATCHED"):
            match(first, snap=snap, key="PAPER.MATCH.DIFFERENT")

    def test_cancel_and_expiry_exact_retries_are_noops(self):
        open_order = submit()
        cancelled = MODULE.cancel_order(
            open_order, order_id="PAPER.ORDER.1", event_at="2026-08-29T01:31:00Z",
            idempotency_key="PAPER.CANCEL.RETRY", reason="USER_PAPER_LAB_CANCEL",
        )
        cancel_retry = MODULE.cancel_order(
            cancelled, order_id="PAPER.ORDER.1", event_at="2026-08-29T01:31:00Z",
            idempotency_key="PAPER.CANCEL.RETRY", reason="USER_PAPER_LAB_CANCEL",
        )
        self.assertEqual(MODULE.canonical_json(cancelled), MODULE.canonical_json(cancel_retry))

        expiring = submit(
            value=intent(expires_at="2026-08-29T01:31:00Z"),
        )
        expired = MODULE.expire_order(
            expiring, order_id="PAPER.ORDER.1", event_at="2026-08-29T01:31:00Z",
            idempotency_key="PAPER.EXPIRE.RETRY",
        )
        expiry_retry = MODULE.expire_order(
            expired, order_id="PAPER.ORDER.1", event_at="2026-08-29T01:31:00Z",
            idempotency_key="PAPER.EXPIRE.RETRY",
        )
        self.assertEqual(MODULE.canonical_json(expired), MODULE.canonical_json(expiry_retry))

    def test_same_idempotency_key_with_different_intent_is_collision(self):
        first = submit()
        changed = intent(quantity="1")
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "IDEMPOTENCY_KEY_COLLISION"):
            MODULE.submit_order(first, changed)

    def test_stale_unknown_cross_market_and_preorder_snapshots_fail_closed(self):
        base = submit()
        cases = [
            (snapshot(freshness="STALE"), "SNAPSHOT_NOT_FRESH"),
            (snapshot(freshness="UNKNOWN"), "SNAPSHOT_NOT_FRESH"),
            (snapshot(market="KRW-ETH"), "SNAPSHOT_MARKET_MISMATCH"),
            (snapshot(captured_at="2026-08-29T01:29:00Z"), "SNAPSHOT_PRECEDES_ORDER"),
        ]
        for snap, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, code):
                match(base, snap=snap)

    def test_cancel_and_expiry_are_terminal(self):
        open_order = submit()
        cancelled = MODULE.cancel_order(
            open_order,
            order_id="PAPER.ORDER.1",
            event_at="2026-08-29T01:31:00Z",
            idempotency_key="PAPER.CANCEL.1",
            reason="USER_PAPER_LAB_CANCEL",
        )
        self.assertEqual(account(cancelled)["orders"][0]["status"], "CANCELLED")
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "ORDER_TERMINAL"):
            match(cancelled)

        expiry_intent = intent(
            order_id="PAPER.ORDER.2", key="PAPER.SUBMIT.2",
            submitted_at="2026-08-29T01:35:00Z", expires_at="2026-08-29T01:36:00Z",
        )
        second = MODULE.submit_order(cancelled, expiry_intent)
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "ORDER_EXPIRED_TOO_EARLY"):
            MODULE.expire_order(
                second, order_id="PAPER.ORDER.2", event_at="2026-08-29T01:35:59Z",
                idempotency_key="PAPER.EXPIRE.EARLY",
            )
        expired = MODULE.expire_order(
            second, order_id="PAPER.ORDER.2", event_at="2026-08-29T01:36:00Z",
            idempotency_key="PAPER.EXPIRE.2",
        )
        state = account(expired, observed_at="2026-08-29T01:40:00Z")
        second_order = next(row for row in state["orders"] if row["order_id"] == "PAPER.ORDER.2")
        self.assertEqual(second_order["status"], "EXPIRED")


class LedgerIntegrityAndRecoveryTests(unittest.TestCase):
    def test_account_state_revalidates_exact_embedded_ledger_and_derivation(self):
        value = account(match(submit()))
        self.assertEqual(MODULE.validate_account_state(value), value)
        tampered = copy.deepcopy(value)
        tampered["cash"] = "999"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "ACCOUNT_STATE_DERIVATION_MISMATCH"):
            MODULE.validate_account_state(tampered)

    def test_account_state_requires_fresh_marks(self):
        value = match(submit())
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "ACCOUNT_MARK_NOT_FRESH"):
            MODULE.build_account_state(
                value,
                observed_at="2026-08-29T01:40:00Z",
                mark_prices={"KRW-BTC": "105"},
                mark_freshness_status="UNKNOWN",
                mark_source_ref="test://marks/unknown",
                mark_source_sha256="d" * 64,
            )

    def test_outer_rehash_cannot_hide_historical_fill_mutation(self):
        value = match(submit())
        value["events"][-1]["payload"]["filled_quantity"] = "1"
        event = value["events"][-1]
        event["event_sha256"] = MODULE.payload_sha256({k: v for k, v in event.items() if k != "event_sha256"})
        value["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in value.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "MATCH_DERIVATION_MISMATCH"):
            MODULE.validate_ledger(value)

    def test_content_addressed_publish_is_idempotent_and_restart_recovers_exact_state(self):
        first = submit()
        latest = match(first)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, created = MODULE.publish_ledger_snapshot(root, first)
            _, created_again = MODULE.publish_ledger_snapshot(root, first)
            latest_path, latest_created = MODULE.publish_ledger_snapshot(root, latest)
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertTrue(latest_created)
            self.assertTrue(latest_path.exists())
            recovered = MODULE.recover_ledger(root, "PAPER.LEDGER.TEST")
            self.assertEqual(MODULE.canonical_json(recovered), MODULE.canonical_json(latest))
            self.assertEqual(MODULE.canonical_json(account(recovered)), MODULE.canonical_json(account(latest)))

    def test_publisher_forbids_repository_internal_account_state(self):
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "TRACKED_LEDGER_OUTPUT_FORBIDDEN"):
            MODULE.publish_ledger_snapshot(ROOT / "data" / "paper-ledger", submit())
        self.assertFalse((ROOT / "data" / "paper-ledger").exists())

    def test_recovery_rejects_modified_snapshot_and_divergent_history(self):
        first = submit()
        latest = match(first)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path, _ = MODULE.publish_ledger_snapshot(root, latest)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["events"][0]["payload"]["initial_cash"] = "999"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(MODULE.CryptoPaperSimulatorError):
                MODULE.recover_ledger(root, "PAPER.LEDGER.TEST")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            MODULE.publish_ledger_snapshot(root, first)
            alternative = MODULE.cancel_order(
                first,
                order_id="PAPER.ORDER.1",
                event_at="2026-08-29T01:31:00Z",
                idempotency_key="PAPER.CANCEL.ALT",
                reason="ALTERNATIVE_HISTORY",
            )
            MODULE.publish_ledger_snapshot(root, latest)
            MODULE.publish_ledger_snapshot(root, alternative)
            with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "HISTORY_DIVERGED_AT_LENGTH"):
                MODULE.recover_ledger(root, "PAPER.LEDGER.TEST")

    def test_ledger_event_and_packet_hash_chain_detects_each_layer(self):
        value = match(submit())
        broken = copy.deepcopy(value)
        broken["events"][-1]["previous_event_sha256"] = "f" * 64
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "EVENT_SHA_MISMATCH"):
            MODULE.validate_ledger(broken)
        outer = copy.deepcopy(value)
        outer["packet_sha256"] = "e" * 64
        with self.assertRaisesRegex(MODULE.CryptoPaperSimulatorError, "LEDGER_PACKET_SHA_MISMATCH"):
            MODULE.validate_ledger(outer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
