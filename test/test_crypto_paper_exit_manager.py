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


def submitted_ledger():
    ledger = SIM.create_ledger(
        ledger_id="PAPER.EXIT.TEST",
        initial_cash="1000",
        opened_at="2026-08-29T01:00:00Z",
        idempotency_key="PAPER.ACCOUNT.OPEN",
    )
    return SIM.submit_order(ledger, sim_intent())


def partially_filled_ledger():
    return SIM.match_order(
        submitted_ledger(),
        order_id="PAPER.ENTRY.1",
        snapshot=book(asks=[{"price": "100", "quantity": "1"}]),
        event_at="2026-08-29T01:31:01Z",
        idempotency_key="PAPER.ENTRY.MATCH.1",
    )


def draft_entry_account():
    return account(observed_at="2026-08-29T01:32:00Z", price="101", source_sha="e" * 64)


def order_draft(**changes):
    """A P5-09 order draft as an upstream producer would hand it to a caller."""
    value = {
        "draft_id": "P509.ORDER.DRAFT.1",
        "market": "KRW-BTC",
        "planned_stop_price": "90",
        "expires_at": "2026-08-29T02:30:00Z",
        "next_review_at": "2026-08-29T03:00:00Z",
    }
    value.update(changes)
    return value


def binding(
    source_field, trigger_id, category, action, *, fraction=None,
    order_id=None, key=None,
):
    return {
        "source_field": source_field,
        "trigger_id": trigger_id,
        "category": category,
        "action": action,
        "quantity_fraction": fraction,
        "paper_order_id": order_id,
        "paper_order_idempotency_key": key,
    }


def default_bindings():
    return [
        binding(
            "planned_stop_price", "TRIGGER.STOP", "HARD_EXIT", "EXIT_REVIEW",
            fraction="1", order_id="PAPER.EXIT.STOP", key="PAPER.EXIT.STOP.SUBMIT",
        ),
        binding(
            "next_review_at", "TRIGGER.TIME", "TIME_REVIEW", "EXIT_REVIEW",
            fraction="1", order_id="PAPER.EXIT.TIME", key="PAPER.EXIT.TIME.SUBMIT",
        ),
    ]


def expected_draft_triggers():
    return [
        trigger(
            "TRIGGER.STOP", "HARD_EXIT", "PRICE_AT_OR_BELOW", "EXIT_REVIEW",
            threshold="90", fraction="1", order_id="PAPER.EXIT.STOP",
            key="PAPER.EXIT.STOP.SUBMIT",
        ),
        trigger(
            "TRIGGER.TIME", "TIME_REVIEW", "TIME_AT_OR_AFTER", "EXIT_REVIEW",
            threshold="2026-08-29T03:00:00Z", fraction="1",
            order_id="PAPER.EXIT.TIME", key="PAPER.EXIT.TIME.SUBMIT",
        ),
    ]


def draft_plan(*, draft=None, bindings=None, source_account=None, **changes):
    source_account = draft_entry_account() if source_account is None else source_account
    arguments = {
        "plan_id": "PAPER.EXIT.PLAN.1",
        "market": "KRW-BTC",
        "source_entry_order_id": "PAPER.ENTRY.1",
        "created_at": source_account["observed_at"],
        "order_draft": order_draft() if draft is None else draft,
        "trigger_bindings": default_bindings() if bindings is None else bindings,
        "source_entry_account": source_account,
        "source_entry_plan_ref": "test://entry-plan/PAPER.ENTRY.1",
        "source_entry_plan_sha256": "a" * 64,
    }
    arguments.update(changes)
    return MODULE.build_exit_plan_from_order_draft(**arguments)


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


class OrderDraftAdapterTests(unittest.TestCase):
    def test_adapter_mirrors_build_exit_plan_arguments_without_policy_defaults(self):
        signature = inspect.signature(MODULE.build_exit_plan_from_order_draft)
        direct = inspect.signature(MODULE.build_exit_plan)
        for parameter in signature.parameters.values():
            self.assertIs(parameter.kind, inspect.Parameter.KEYWORD_ONLY, parameter.name)
        self.assertNotIn("triggers", signature.parameters)
        self.assertEqual(
            set(signature.parameters) - {"order_draft", "trigger_bindings"},
            set(direct.parameters) - {"triggers"},
        )
        for name in ("order_draft", "trigger_bindings"):
            self.assertIs(signature.parameters[name].default, inspect.Parameter.empty)
        for name in ("stop_price", "harvest_fraction", "category", "action", "quantity_fraction"):
            self.assertNotIn(name, signature.parameters)

    def test_adapter_maps_declared_source_fields_to_exact_conditions_and_values(self):
        value = draft_plan()
        self.assertEqual(value["triggers"], expected_draft_triggers())
        self.assertEqual(value["initial_quantity"], "2")
        self.assertEqual(value["entry_price"], "100.5")
        self.assertEqual(MODULE.validate_exit_plan(value), value)
        self.assertEqual(value["authority"], CONTRACT["plan_authority"])

    def test_adapter_output_equals_direct_build_exit_plan(self):
        source = draft_entry_account()
        adapted = draft_plan(source_account=source)
        direct = MODULE.build_exit_plan(
            plan_id="PAPER.EXIT.PLAN.1",
            market="KRW-BTC",
            source_entry_order_id="PAPER.ENTRY.1",
            created_at=source["observed_at"],
            triggers=expected_draft_triggers(),
            source_entry_account=source,
            source_entry_plan_ref="test://entry-plan/PAPER.ENTRY.1",
            source_entry_plan_sha256="a" * 64,
        )
        self.assertEqual(MODULE.canonical_json(adapted), MODULE.canonical_json(direct))
        self.assertEqual(adapted["packet_sha256"], direct["packet_sha256"])
        self.assertEqual(draft_plan(source_account=source, contract=CONTRACT), direct)

    def test_adapter_plan_drives_the_existing_evaluate_path(self):
        result = evaluate(exit_plan=draft_plan(), observed=observation(price="90", prior_high="115"))
        self.assertEqual(result["status"], "TRIGGER_SELECTED_REVIEW_ONLY")
        self.assertEqual(result["selected_trigger_id"], "TRIGGER.STOP")
        self.assertEqual(result["action"], "EXIT_REVIEW")
        self.assertEqual(result["target_quantity"], "2")
        self.assertEqual(result["paper_order_identity_candidate"]["order_id"], "PAPER.EXIT.STOP")
        self.assertTrue(result["human_review_required"])
        self.assertFalse(result["authority"]["exchange_order_authorized"])

    def test_adapter_uses_only_caller_supplied_category_action_and_fraction(self):
        bindings = [
            binding(
                "planned_stop_price", "TRIGGER.RISK", "RISK_REGIME", "REDUCE",
                fraction="0.25", order_id="PAPER.EXIT.RISK", key="PAPER.EXIT.RISK.SUBMIT",
            ),
            binding(
                "expires_at", "TRIGGER.EXPIRY", "TIME_REVIEW", "HARVEST_PARTIAL",
                fraction="0.5", order_id="PAPER.EXIT.EXPIRY", key="PAPER.EXIT.EXPIRY.SUBMIT",
            ),
        ]
        value = draft_plan(bindings=bindings)
        self.assertEqual(
            [
                (row["condition"], row["threshold"], row["category"], row["action"], row["quantity_fraction"])
                for row in value["triggers"]
            ],
            [
                ("PRICE_AT_OR_BELOW", "90", "RISK_REGIME", "REDUCE", "0.25"),
                ("TIME_AT_OR_AFTER", "2026-08-29T02:30:00Z", "TIME_REVIEW", "HARVEST_PARTIAL", "0.5"),
            ],
        )
        observed = observation(observed_at="2026-08-29T02:30:00Z", price="110", source_sha="7" * 64)
        result = evaluate(
            exit_plan=value,
            current_account=account(observed_at=observed["observed_at"], price="110", source_sha="7" * 64),
            observed=observed,
        )
        self.assertEqual(result["selected_trigger_id"], "TRIGGER.EXPIRY")
        self.assertEqual(result["target_quantity"], "1")

    def test_adapter_rejects_missing_null_and_malformed_source_values(self):
        cases = (
            ({"planned_stop_price": None}, "ORDER_DRAFT_PRICE_INVALID:0"),
            ({"planned_stop_price": "0"}, "ORDER_DRAFT_PRICE_INVALID:0"),
            ({"planned_stop_price": "-5"}, "ORDER_DRAFT_PRICE_INVALID:0"),
            ({"planned_stop_price": 90}, "ORDER_DRAFT_PRICE_INVALID:0"),
            ({"planned_stop_price": "90.50"}, "ORDER_DRAFT_PRICE_INVALID:0:NON_CANONICAL"),
            ({"next_review_at": None}, "ORDER_DRAFT_TIME_INVALID:1"),
            ({"next_review_at": "2026-08-29 03:00:00"}, "ORDER_DRAFT_TIME_INVALID:1"),
            ({"next_review_at": "2026-02-30T03:00:00Z"}, "ORDER_DRAFT_TIME_INVALID:1"),
        )
        for changes, code in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(
                MODULE.CryptoPaperExitManagerError, code
            ):
                draft_plan(draft=order_draft(**changes))
        missing = order_draft()
        del missing["planned_stop_price"]
        with self.assertRaisesRegex(
            MODULE.CryptoPaperExitManagerError, "ORDER_DRAFT_SOURCE_FIELD_MISSING:0:planned_stop_price"
        ):
            draft_plan(draft=missing)

    def test_adapter_rejects_unsupported_source_fields_and_binding_shapes(self):
        unsupported = [
            binding(
                "planned_take_profit_price", "TRIGGER.TP", "PROFIT_TRAIL", "HARVEST_PARTIAL",
                fraction="0.5", order_id="PAPER.EXIT.TP", key="PAPER.EXIT.TP.SUBMIT",
            )
        ]
        with self.assertRaisesRegex(
            MODULE.CryptoPaperExitManagerError, "ORDER_DRAFT_SOURCE_FIELD_UNSUPPORTED:0"
        ):
            draft_plan(bindings=unsupported)
        caller_condition = default_bindings()
        caller_condition[1]["condition"] = "PRICE_AT_OR_ABOVE"
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "TRIGGER_BINDING_FIELDS_MISMATCH:1"):
            draft_plan(bindings=caller_condition)
        incomplete = default_bindings()
        del incomplete[0]["quantity_fraction"]
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "TRIGGER_BINDING_FIELDS_MISMATCH:0"):
            draft_plan(bindings=incomplete)
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "TRIGGER_BINDINGS_EMPTY"):
            draft_plan(bindings=[])
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "TRIGGER_BINDINGS_INVALID"):
            draft_plan(bindings=tuple(default_bindings()))
        for bad_draft in ([("planned_stop_price", "90")], {1: "90"}):
            with self.subTest(draft=bad_draft), self.assertRaisesRegex(
                MODULE.CryptoPaperExitManagerError, "ORDER_DRAFT_INVALID"
            ):
                draft_plan(draft=bad_draft)

    def test_adapter_rejects_invalid_categories_actions_and_quantity_metadata(self):
        cases = (
            ("MOMENTUM", "EXIT_REVIEW", "1", "PAPER.EXIT.STOP", "TRIGGER.STOP", "TRIGGER_CATEGORY_INVALID:0"),
            ("HARD_EXIT", "SELL", "1", "PAPER.EXIT.STOP", "TRIGGER.STOP", "TRIGGER_ACTION_INVALID:0"),
            ("HARD_EXIT", "EXIT_REVIEW", "0", "PAPER.EXIT.STOP", "TRIGGER.STOP", "TRIGGER_QUANTITY_FRACTION_INVALID:0"),
            ("HARD_EXIT", "EXIT_REVIEW", "1.5", "PAPER.EXIT.STOP", "TRIGGER.STOP", "TRIGGER_QUANTITY_FRACTION_INVALID:0"),
            ("HARD_EXIT", "EXIT_REVIEW", "1", None, "TRIGGER.STOP", "TRIGGER_PAPER_ORDER_ID_INVALID:0"),
            (
                "PROFIT_TRAIL", "TRAIL", "1", "PAPER.EXIT.STOP", "TRIGGER.STOP",
                "NON_QUANTITY_TRIGGER_ORDER_IDENTITY_FORBIDDEN:0",
            ),
            ("HARD_EXIT", "EXIT_REVIEW", "1", "PAPER.EXIT.STOP", "trigger.stop", "TRIGGER_ID_INVALID:0"),
        )
        for category, action, fraction, order_id, trigger_id, code in cases:
            row = binding(
                "planned_stop_price", trigger_id, category, action,
                fraction=fraction, order_id=order_id, key="PAPER.EXIT.STOP.SUBMIT",
            )
            with self.subTest(code=code), self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, code):
                draft_plan(bindings=[row])

    def test_adapter_rejects_duplicate_identities_and_never_sorts_bindings(self):
        duplicated_id = default_bindings()
        duplicated_id[1]["trigger_id"] = duplicated_id[0]["trigger_id"]
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "TRIGGER_ID_DUPLICATE"):
            draft_plan(bindings=duplicated_id)
        duplicated_order = default_bindings()
        duplicated_order[1]["paper_order_id"] = duplicated_order[0]["paper_order_id"]
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "PAPER_ORDER_IDENTITY_DUPLICATE"):
            draft_plan(bindings=duplicated_order)
        duplicated_key = default_bindings()
        duplicated_key[1]["paper_order_idempotency_key"] = duplicated_key[0]["paper_order_idempotency_key"]
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "PAPER_ORDER_IDENTITY_DUPLICATE"):
            draft_plan(bindings=duplicated_key)
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "PRIORITY_ORDER"):
            draft_plan(bindings=list(reversed(default_bindings())))

    def test_adapter_preserves_existing_entry_account_boundaries(self):
        unfilled = account(
            submitted_ledger(), observed_at="2026-08-29T01:32:00Z", price="101", source_sha="e" * 64,
        )
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "SOURCE_ENTRY_ORDER_NOT_FILLED_BUY"):
            draft_plan(source_account=unfilled)
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "SOURCE_ENTRY_ORDER_NOT_FOUND"):
            draft_plan(source_entry_order_id="PAPER.ENTRY.404")
        with self.assertRaisesRegex(MODULE.CryptoPaperExitManagerError, "PLAN_MARKET_ENTRY_ORDER_MISMATCH"):
            draft_plan(market="KRW-ETH")
        with self.assertRaisesRegex(
            MODULE.CryptoPaperExitManagerError, "PLAN_CREATED_AT_MUST_EQUAL_ENTRY_ACCOUNT_OBSERVED_AT"
        ):
            draft_plan(created_at="2026-08-29T01:40:00Z")
        partial = draft_plan(source_account=account(
            partially_filled_ledger(), observed_at="2026-08-29T01:32:00Z", price="101", source_sha="e" * 64,
        ))
        self.assertEqual(partial["initial_quantity"], "1")
        self.assertEqual(partial["entry_price"], "100")
        self.assertEqual(partial["triggers"], expected_draft_triggers())

    def test_adapter_leaves_caller_inputs_unchanged_and_repeats_deterministically(self):
        draft = order_draft()
        bindings = default_bindings()
        source = draft_entry_account()
        draft_before = copy.deepcopy(draft)
        bindings_before = copy.deepcopy(bindings)
        source_before = copy.deepcopy(source)
        first = draft_plan(draft=draft, bindings=bindings, source_account=source)
        second = draft_plan(draft=draft, bindings=bindings, source_account=source)
        self.assertEqual(draft, draft_before)
        self.assertEqual(bindings, bindings_before)
        self.assertEqual(source, source_before)
        self.assertEqual(first, second)
        first["triggers"][0]["threshold"] = "1"
        first["triggers"].append("TAMPER")
        self.assertEqual(draft_plan(draft=draft, bindings=bindings, source_account=source), second)
        draft["planned_stop_price"] = "80"
        bindings[0]["trigger_id"] = "TRIGGER.MUTATED"
        self.assertEqual(second["triggers"][0]["threshold"], "90")
        self.assertEqual(second["triggers"][0]["trigger_id"], "TRIGGER.STOP")


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
