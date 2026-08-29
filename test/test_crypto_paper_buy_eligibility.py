#!/usr/bin/env python3
"""P5-09 Crypto PAPER Buy Eligibility regression."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "crypto_paper_buy_eligibility.py"
SPEC = importlib.util.spec_from_file_location("crypto_paper_buy_eligibility", MODULE_PATH)
P59 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(P59)

PROMO = P59.PROMOTION
UNI = PROMO.UPBIT_UNIVERSE
REGIME_OC = PROMO.REGIME_OUTPUT_CONTRACT

GENERATED_AT = "2026-08-28T23:59:59Z"
EVAL_AS_OF = "2026-08-28"
UTC = dt.timezone.utc


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def universe_row(
    *, market="KRW-ETH", state=None, canonical_asset_id="ETH", caution_any=False,
) -> dict:
    state = state or UNI.STATE_PAPER_ELIGIBLE
    return {
        "market": market,
        "state": state,
        "reason": "PAPER_ELIGIBLE_ALL_GATES_PASSED",
        "candidate_canonical_asset_id": canonical_asset_id,
        "market_event_warning": False,
        "market_event_caution_any": caution_any,
        "observed_daily_candle_count": 120,
        "trailing_30d_krw_turnover": "10000000000",
        "kraken_cross_exchange_reference": False,
        "authority": dict(UNI._ROW_AUTHORITY),
    }


def universe_packet(rows: list) -> dict:
    policy = UNI.load_policy()
    taxonomy = UNI.load_taxonomy()
    packet = {
        "schema_version": UNI.OUTPUT_SCHEMA_VERSION,
        "snapshot_date": EVAL_AS_OF,
        "evaluation_as_of": EVAL_AS_OF,
        "available_at": GENERATED_AT,
        "manifest_sha256": "a" * 64,
        "policy_version": policy.get("policy_version"),
        "policy_ratified": policy.get("approval_status") == "RATIFIED",
        "taxonomy_version": taxonomy.get("policy_version"),
        "taxonomy_ratified": taxonomy.get("approval_status") == "RATIFIED",
        "duplicate_market_codes": [],
        "summary": {
            "market_count": len(rows),
            "observation_pool_count": sum(r["state"] == UNI.STATE_OBSERVATION_POOL for r in rows),
            "tradeable_universe_count": sum(r["state"] == UNI.STATE_TRADEABLE_UNIVERSE for r in rows),
            "paper_eligible_count": sum(r["state"] == UNI.STATE_PAPER_ELIGIBLE for r in rows),
            "blocked_count": sum(r["state"] == UNI.STATE_BLOCKED for r in rows),
        },
        "markets": rows,
        "authority": dict(UNI._ROW_AUTHORITY),
    }
    packet["payload_sha256"] = UNI.payload_sha256(
        {k: v for k, v in packet.items() if k != "payload_sha256"}
    )
    return packet


def unknown_regime_payload(market="CRYPTO") -> dict:
    return REGIME_OC.build_unknown_output(market, GENERATED_AT)


def hourly_candle(open_time: dt.datetime, *, high: str, low: str, close: str, volume: str) -> dict:
    close_time = open_time + dt.timedelta(hours=1)
    return {
        "open_time": open_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "close_time": close_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "opening_price": close,
        "high_price": high,
        "low_price": low,
        "trade_price": close,
        "candle_acc_trade_price": "1000000",
        "candle_acc_trade_volume": volume,
    }


def breakout_1h_candles(*, lookback_bars=20, breakout=True):
    base = dt.datetime(2026, 8, 27, 0, 0, 0, tzinfo=UTC)
    rows = [
        hourly_candle(base + dt.timedelta(hours=i), high="100", low="90", close="100", volume="10")
        for i in range(lookback_bars)
    ]
    trigger_close = "105" if breakout else "99"
    trigger_volume = "20" if breakout else "5"
    rows.append(hourly_candle(
        base + dt.timedelta(hours=lookback_bars), high="106", low="100",
        close=trigger_close, volume=trigger_volume,
    ))
    return rows


def four_hour_candles(direction="UP"):
    a, b = ("100", "110") if direction == "UP" else ("110", "100")
    return [
        {
            "open_time": "2026-08-28T12:00:00Z", "close_time": "2026-08-28T16:00:00Z",
            "opening_price": a, "high_price": a, "low_price": a, "trade_price": a,
            "candle_acc_trade_price": "1", "candle_acc_trade_volume": "1",
        },
        {
            "open_time": "2026-08-28T16:00:00Z", "close_time": "2026-08-28T20:00:00Z",
            "opening_price": b, "high_price": b, "low_price": b, "trade_price": b,
            "candle_acc_trade_price": "1", "candle_acc_trade_volume": "1",
        },
    ]


def daily_candles(direction="UP"):
    a, b = ("100", "110") if direction == "UP" else ("110", "100")
    return [
        {
            "open_time": "2026-08-26T00:00:00Z", "close_time": "2026-08-27T00:00:00Z",
            "opening_price": a, "high_price": a, "low_price": a, "trade_price": a,
            "candle_acc_trade_price": "1", "candle_acc_trade_volume": "1",
        },
        {
            "open_time": "2026-08-27T00:00:00Z", "close_time": "2026-08-28T00:00:00Z",
            "opening_price": b, "high_price": b, "low_price": b, "trade_price": b,
            "candle_acc_trade_price": "1", "candle_acc_trade_volume": "1",
        },
    ]


def fifteen_minute_candles(direction="UP"):
    a, b = ("100", "101") if direction == "UP" else ("101", "100")
    return [
        {
            "open_time": "2026-08-28T23:30:00Z", "close_time": "2026-08-28T23:45:00Z",
            "opening_price": a, "high_price": "102", "low_price": "99", "trade_price": a,
            "candle_acc_trade_price": "1", "candle_acc_trade_volume": "1",
        },
        {
            "open_time": "2026-08-28T23:45:00Z", "close_time": "2026-08-29T00:00:00Z",
            "opening_price": b, "high_price": "102", "low_price": "99", "trade_price": b,
            "candle_acc_trade_price": "1", "candle_acc_trade_volume": "1",
        },
    ]


def market_evidence_packet(
    *, market="KRW-ETH", breakout=True, four_hour_direction="UP", daily_direction="UP",
    fifteen_minute_direction="UP", include_15m=True, include_orderbook=True,
    include_trades=True, freshness_status="FRESH", policy_ratified=True,
):
    candles = {
        "1h": {
            "finalized_candle_count": len(breakout_1h_candles(breakout=breakout)),
            "finalized_candles": breakout_1h_candles(breakout=breakout),
        },
        "4h": {
            "finalized_candle_count": len(four_hour_candles(four_hour_direction)),
            "finalized_candles": four_hour_candles(four_hour_direction),
        },
        "1d": {
            "finalized_candle_count": len(daily_candles(daily_direction)),
            "finalized_candles": daily_candles(daily_direction),
        },
        "15m": {
            "finalized_candle_count": len(fifteen_minute_candles(fifteen_minute_direction)) if include_15m else 0,
            "finalized_candles": fifteen_minute_candles(fifteen_minute_direction) if include_15m else [],
        },
    }
    for value in candles.values():
        value["freshness"] = {"status": freshness_status}
    orderbook = {
        "best_bid": "104900", "best_ask": "105100", "freshness": {"status": freshness_status},
    } if include_orderbook else {}
    trades = {
        "trade_count": 12 if include_trades else 0, "freshness": {"status": freshness_status},
    }
    return {
        "market": market,
        "policy_ratified": policy_ratified,
        "candles": candles,
        "orderbook": orderbook,
        "trades": trades,
    }


def paper_account_state(*, total_nav_krw="100000000", open_positions=None) -> dict:
    return {"total_nav_krw": total_nav_krw, "open_positions": open_positions or []}


ALL_PASS_CRITERIA = {name: {"status": "PASS", "reason": "TEST"} for name in P59.CRITERIA}


# ---------------------------------------------------------------------------
# aggregate_state: proves the state machine itself, exactly mirroring
# P5-08's own ``test_all_pass_yields_focused_review`` discipline.
# ---------------------------------------------------------------------------

class AggregateStateTests(unittest.TestCase):
    def test_all_pass_with_order_draft_complete_yields_paper_buy_eligible(self):
        state, reason = P59.aggregate_state(ALL_PASS_CRITERIA)
        self.assertEqual(state, P59.STATE_PAPER_BUY_ELIGIBLE)
        self.assertIn("ORDER_DRAFT_COMPLETE", reason)

    def test_all_pass_with_order_draft_unknown_yields_wait(self):
        criteria = dict(ALL_PASS_CRITERIA)
        criteria["ORDER_DRAFT_COMPLETE"] = {"status": "UNKNOWN", "reason": "TEST"}
        state, reason = P59.aggregate_state(criteria)
        self.assertEqual(state, P59.STATE_WAIT)

    def test_any_gating_fail_yields_blocked_even_with_complete_order_draft(self):
        criteria = dict(ALL_PASS_CRITERIA)
        criteria["NO_BLOCKER_STALE_OVERHEAT_DUPLICATE"] = {"status": "FAIL", "reason": "TEST"}
        state, reason = P59.aggregate_state(criteria)
        self.assertEqual(state, P59.STATE_BLOCKED)

    def test_any_gating_unknown_yields_watch(self):
        criteria = dict(ALL_PASS_CRITERIA)
        criteria["REGIME_PERMITS_ENTRY"] = {"status": "UNKNOWN", "reason": "TEST"}
        state, reason = P59.aggregate_state(criteria)
        self.assertEqual(state, P59.STATE_WATCH)

    def test_fail_dominates_unknown(self):
        criteria = dict(ALL_PASS_CRITERIA)
        criteria["REGIME_PERMITS_ENTRY"] = {"status": "UNKNOWN", "reason": "TEST"}
        criteria["TRIGGER_TIMEFRAME_ALIGNMENT"] = {"status": "FAIL", "reason": "TEST"}
        state, reason = P59.aggregate_state(criteria)
        self.assertEqual(state, P59.STATE_BLOCKED)

    def test_order_draft_complete_never_gates_watch_or_blocked(self):
        """ORDER_DRAFT_COMPLETE only selects WAIT vs PAPER_BUY_ELIGIBLE; it
        never itself produces WATCH or BLOCKED (it never returns FAIL)."""
        criteria = dict(ALL_PASS_CRITERIA)
        criteria["ORDER_DRAFT_COMPLETE"] = {"status": "UNKNOWN", "reason": "TEST"}
        state, _ = P59.aggregate_state(criteria)
        self.assertNotIn(state, (P59.STATE_WATCH, P59.STATE_BLOCKED))

    def test_invalid_criteria_set_raises(self):
        with self.assertRaises(P59.CryptoPaperBuyEligibilityError):
            P59.aggregate_state({"ONLY_ONE": {"status": "PASS", "reason": "x"}})


# ---------------------------------------------------------------------------
# Per-criterion mechanical tests
# ---------------------------------------------------------------------------

class FocusedReviewUpstreamTests(unittest.TestCase):
    def test_pass_for_focused_review_row(self):
        result = P59.evaluate_focused_review_upstream({"promotion_state": "FOCUSED_REVIEW"})
        self.assertEqual(result["status"], "PASS")

    def test_raises_for_out_of_scope_row(self):
        with self.assertRaises(P59.CryptoPaperBuyEligibilityError):
            P59.evaluate_focused_review_upstream({"promotion_state": "WATCH"})


class RegimePermitsEntryTests(unittest.TestCase):
    def test_unknown_by_construction(self):
        result = P59.evaluate_regime_permits_entry(unknown_regime_payload())
        self.assertEqual(result["status"], "UNKNOWN")


class TriggerTimeframeAlignmentTests(unittest.TestCase):
    def test_pass_when_aligned_up(self):
        packet = market_evidence_packet(four_hour_direction="UP", daily_direction="UP")
        result = P59.evaluate_trigger_timeframe_alignment(packet)
        self.assertEqual(result["status"], "PASS")

    def test_fail_when_four_hour_conflicts(self):
        packet = market_evidence_packet(four_hour_direction="DOWN", daily_direction="UP")
        result = P59.evaluate_trigger_timeframe_alignment(packet)
        self.assertEqual(result["status"], "FAIL")

    def test_fail_when_daily_conflicts(self):
        packet = market_evidence_packet(four_hour_direction="UP", daily_direction="DOWN")
        result = P59.evaluate_trigger_timeframe_alignment(packet)
        self.assertEqual(result["status"], "FAIL")

    def test_fail_when_fifteen_minute_trigger_conflicts(self):
        packet = market_evidence_packet(fifteen_minute_direction="DOWN")
        result = P59.evaluate_trigger_timeframe_alignment(packet)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["fifteen_minute_direction"], "DOWN")

    def test_unknown_when_evidence_missing(self):
        result = P59.evaluate_trigger_timeframe_alignment(None)
        self.assertEqual(result["status"], "UNKNOWN")


class BreakoutOrPullbackTests(unittest.TestCase):
    def setUp(self):
        self.policy = P59.load_policy()

    def test_pass_when_breakout_confirmed(self):
        packet = market_evidence_packet(breakout=True)
        result = P59.evaluate_breakout_or_pullback(packet, self.policy)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["reason"], "BREAKOUT_CONFIRMED")

    def test_unknown_when_breakout_not_triggered(self):
        packet = market_evidence_packet(breakout=False)
        result = P59.evaluate_breakout_or_pullback(packet, self.policy)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_unknown_when_insufficient_1h_candles(self):
        result = P59.evaluate_breakout_or_pullback(None, self.policy)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "INSUFFICIENT_1H_CANDLES_FOR_BREAKOUT")

    def test_never_fails(self):
        """Disjunctive criterion with an undecidable Pullback leg: an
        unsatisfied Breakout leg must resolve UNKNOWN, never FAIL."""
        packet = market_evidence_packet(breakout=False)
        result = P59.evaluate_breakout_or_pullback(packet, self.policy)
        self.assertNotEqual(result["status"], "FAIL")


class IndependentPriceVolumeEvidenceTests(unittest.TestCase):
    def test_pass_when_both_families_present(self):
        packet = market_evidence_packet()
        result = P59.evaluate_independent_price_volume_evidence(packet)
        self.assertEqual(result["status"], "PASS")

    def test_unknown_when_orderbook_missing(self):
        packet = market_evidence_packet(include_orderbook=False)
        result = P59.evaluate_independent_price_volume_evidence(packet)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_unknown_when_15m_missing(self):
        packet = market_evidence_packet(include_15m=False)
        result = P59.evaluate_independent_price_volume_evidence(packet)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_unknown_when_packet_missing(self):
        result = P59.evaluate_independent_price_volume_evidence(None)
        self.assertEqual(result["status"], "UNKNOWN")


class NoBlockerStaleOverheatDuplicateTests(unittest.TestCase):
    def test_fail_when_caution_active(self):
        row = universe_row(caution_any=True)
        result = P59.evaluate_no_blocker_stale_overheat_duplicate(
            row, market_evidence_packet(), "KEY-1", set(),
        )
        self.assertEqual(result["status"], "FAIL")

    def test_unknown_when_no_ledger_supplied(self):
        row = universe_row(caution_any=False)
        result = P59.evaluate_no_blocker_stale_overheat_duplicate(
            row, market_evidence_packet(), "KEY-1", None,
        )
        self.assertEqual(result["status"], "UNKNOWN")

    def test_fail_when_duplicate_key_present(self):
        row = universe_row(caution_any=False)
        result = P59.evaluate_no_blocker_stale_overheat_duplicate(
            row, market_evidence_packet(), "KEY-1", {"KEY-1"},
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["duplicate"]["status"], "FAIL")

    def test_never_passes_without_forcing_overextension(self):
        """Overheat has no ratified definition anywhere (not even in the
        PAPER baseline), so this composite can never reach real PASS -- it
        stays capped at UNKNOWN even with a novel duplicate key and no
        active caution, exactly like P5-08's own OVEREXTENSION criterion."""
        row = universe_row(caution_any=False)
        result = P59.evaluate_no_blocker_stale_overheat_duplicate(
            row, market_evidence_packet(), "KEY-1", set(),
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["overextension"]["status"], "UNKNOWN")

    def test_stale_current_evidence_is_a_hard_gate(self):
        row = universe_row(caution_any=False)
        result = P59.evaluate_no_blocker_stale_overheat_duplicate(
            row, market_evidence_packet(freshness_status="STALE"), "KEY-1", set(),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["freshness"]["status"], "FAIL")

    def test_unratified_freshness_policy_never_counts_as_fresh(self):
        result = P59.evaluate_current_evidence_freshness(
            market_evidence_packet(policy_ratified=False),
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "MARKET_EVIDENCE_FRESHNESS_POLICY_UNRATIFIED")


class DuplicateGuardKeyTests(unittest.TestCase):
    def test_deterministic_same_input_same_key(self):
        key1 = P59.compute_duplicate_guard_key("KRW-ETH", EVAL_AS_OF, "2026-08-28T00:00:00Z", "105", "90")
        key2 = P59.compute_duplicate_guard_key("KRW-ETH", EVAL_AS_OF, "2026-08-28T00:00:00Z", "105", "90")
        self.assertEqual(key1, key2)

    def test_different_market_different_key(self):
        key1 = P59.compute_duplicate_guard_key("KRW-ETH", EVAL_AS_OF, "2026-08-28T00:00:00Z", "105", "90")
        key2 = P59.compute_duplicate_guard_key("KRW-BTC", EVAL_AS_OF, "2026-08-28T00:00:00Z", "105", "90")
        self.assertNotEqual(key1, key2)

    def test_different_entry_price_different_key(self):
        key1 = P59.compute_duplicate_guard_key("KRW-ETH", EVAL_AS_OF, "2026-08-28T00:00:00Z", "105", "90")
        key2 = P59.compute_duplicate_guard_key("KRW-ETH", EVAL_AS_OF, "2026-08-28T00:00:00Z", "106", "90")
        self.assertNotEqual(key1, key2)

    def test_key_matches_p9_04_token_pattern(self):
        key = P59.compute_duplicate_guard_key("KRW-ETH", EVAL_AS_OF, "2026-08-28T00:00:00Z", "105", "90")
        self.assertRegex(key, r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class OrderDraftTests(unittest.TestCase):
    def setUp(self):
        self.policy = P59.load_policy()
        self.universe_policy = UNI.load_policy()

    def test_complete_draft_when_all_inputs_supplied(self):
        packet = market_evidence_packet(breakout=True)
        draft = P59.build_order_draft(
            "KRW-ETH", packet, self.policy, self.universe_policy,
            evaluation_as_of=EVAL_AS_OF, paper_account_state=paper_account_state(), fee_rate="0.0005",
        )
        result = P59.evaluate_order_draft_complete(draft)
        self.assertEqual(result["status"], "PASS")
        self.assertIsNotNone(draft["quantity"])
        self.assertIsNotNone(draft["duplicate_guard_key"])

    def test_incomplete_draft_without_paper_account_state(self):
        packet = market_evidence_packet(breakout=True)
        draft = P59.build_order_draft(
            "KRW-ETH", packet, self.policy, self.universe_policy,
            evaluation_as_of=EVAL_AS_OF, paper_account_state=None, fee_rate="0.0005",
        )
        self.assertIsNone(draft["quantity"])
        result = P59.evaluate_order_draft_complete(draft)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_incomplete_draft_without_fee_rate(self):
        packet = market_evidence_packet(breakout=True)
        draft = P59.build_order_draft(
            "KRW-ETH", packet, self.policy, self.universe_policy,
            evaluation_as_of=EVAL_AS_OF, paper_account_state=paper_account_state(), fee_rate=None,
        )
        self.assertIsNone(draft["fee_amount_krw"])
        result = P59.evaluate_order_draft_complete(draft)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_no_fields_when_breakout_not_computable(self):
        draft = P59.build_order_draft(
            "KRW-ETH", None, self.policy, self.universe_policy,
            evaluation_as_of=EVAL_AS_OF, paper_account_state=paper_account_state(), fee_rate="0.0005",
        )
        for field in P59._ORDER_DRAFT_REQUIRED_FIELDS:
            self.assertIsNone(draft[field])

    def test_quantity_matches_planned_loss_formula(self):
        from decimal import Decimal
        packet = market_evidence_packet(breakout=True)
        draft = P59.build_order_draft(
            "KRW-ETH", packet, self.policy, self.universe_policy,
            evaluation_as_of=EVAL_AS_OF, paper_account_state=paper_account_state(), fee_rate="0.0005",
        )
        entry_price = Decimal("105")
        stop_price = Decimal("90")
        planned_loss_krw = Decimal("100000000") * Decimal(self.policy["risk"]["per_trade_planned_loss_nav_fraction"])
        expected_quantity = P59._floor(planned_loss_krw / (entry_price - stop_price), self.policy["decimal_scale"])
        self.assertEqual(Decimal(draft["quantity"]), expected_quantity)
        self.assertEqual(Decimal(draft["planned_loss_krw"]), planned_loss_krw)

    def test_expiry_is_next_hourly_boundary_after_trigger_close(self):
        packet = market_evidence_packet(breakout=True)
        draft = P59.build_order_draft(
            "KRW-ETH", packet, self.policy, self.universe_policy,
            evaluation_as_of=EVAL_AS_OF, paper_account_state=paper_account_state(), fee_rate="0.0005",
        )
        trigger_close = breakout_1h_candles(breakout=True)[-1]["close_time"]
        expected = (
            dt.datetime.strptime(trigger_close, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            + dt.timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(draft["expires_at"], expected)
        self.assertEqual(draft["next_review_at"], expected)


class PaperRiskBudgetTests(unittest.TestCase):
    def setUp(self):
        self.policy = P59.load_policy()

    def _entry_invalidation(self):
        from decimal import Decimal
        return {
            "entry_price": Decimal("105"), "planned_stop_price": Decimal("90"),
        }

    def test_unknown_without_account_state(self):
        result = P59.evaluate_paper_risk_budget(self._entry_invalidation(), self.policy, None, 18)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_pass_within_budget(self):
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
        )
        self.assertEqual(result["status"], "PASS")

    def test_fail_when_single_asset_cap_breached(self):
        from decimal import Decimal
        tight_entry_invalidation = {"entry_price": Decimal("105"), "planned_stop_price": Decimal("104")}
        result = P59.evaluate_paper_risk_budget(
            tight_entry_invalidation, self.policy, paper_account_state(), 18,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("SINGLE_ASSET_PAPER_EXPOSURE_CAP", result["reason"])

    def test_fail_when_max_concurrent_positions_breached(self):
        existing = [
            {"asset_id": f"X{i}", "planned_loss_nav_fraction": "0.0001", "portfolio_weight_nav_fraction": "0.01"}
            for i in range(3)
        ]
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(open_positions=existing), 18,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("MAX_CONCURRENT_PAPER_POSITIONS", result["reason"])

    def test_total_crypto_cap_uses_exposure_not_planned_loss(self):
        existing = [
            {
                "asset_id": f"X{i}",
                "planned_loss_nav_fraction": "0.0001",
                "portfolio_weight_nav_fraction": "0.02",
            }
            for i in range(2)
        ]
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy,
            paper_account_state(open_positions=existing), 18,
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("TOTAL_CRYPTO_PAPER_EXPOSURE_CAP", result["reason"])


class ZeroOrderEndpointCallsTests(unittest.TestCase):
    def test_always_pass(self):
        self.assertEqual(P59.evaluate_zero_order_endpoint_calls()["status"], "PASS")

    def test_module_source_has_no_network_import(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for token in ("urllib", "requests", "socket", "http.client"):
            self.assertNotIn(token, source)


# ---------------------------------------------------------------------------
# Full end-to-end reachability proof.
#
# Exactly like P5-08's own docstring admission ("the production builder
# cannot manufacture that input"), two of P5-09's nine criteria are
# hard-capped short of a real PASS today, purely because they echo
# upstream boundaries this module does not own:
#   - REGIME_PERMITS_ENTRY echoes P1-CR-08/regime/output_contract.py, whose
#     own validate_output() authorizes only "UNKNOWN" until P1-COM-05.
#   - the OVEREXTENSION leg inside NO_BLOCKER_STALE_OVERHEAT_DUPLICATE
#     echoes P5-08's own OVEREXTENSION criterion, which has no ratified
#     definition anywhere, including the PAPER baseline text.
# Both are mocked ONLY here, at the exact leaf functions P5-09 delegates
# to (``crypto_candidate_promotion.evaluate_regime`` /
# ``evaluate_overextension``), to prove every criterion P5-09 itself
# actually owns -- trigger alignment, breakout, evidence independence,
# order-draft completeness, PAPER risk budget, duplicate-guard, and the
# zero-order-call invariant -- genuinely combines into PAPER_BUY_ELIGIBLE
# given sufficient synthetic evidence. MATERIAL_BLOCKER is not mocked: with
# caution_any=False it is UNKNOWN, and since NO_BLOCKER_STALE_OVERHEAT_
# DUPLICATE takes the worst-of its three sub-checks, only mocking
# OVEREXTENSION is required (MATERIAL_BLOCKER's own UNKNOWN would still
# have forced the composite to UNKNOWN, so it is mocked too, for the same
# stated reason).
# ---------------------------------------------------------------------------

class EndToEndReachabilityTests(unittest.TestCase):
    def _candidate_row(self):
        return {"market": "KRW-ETH", "canonical_asset_id": "ETH", "promotion_state": "FOCUSED_REVIEW"}

    def test_synthetic_all_pass_input_reaches_paper_buy_eligible(self):
        packet = market_evidence_packet(breakout=True, four_hour_direction="UP", daily_direction="UP")
        row = universe_row(caution_any=False)
        with (
            mock.patch.object(PROMO, "evaluate_regime", return_value={
                "status": "PASS", "reason": "TEST_ONLY_FORCED_PAST_P1_CR_08_BOUNDARY",
            }),
            mock.patch.object(PROMO, "evaluate_overextension", return_value={
                "status": "PASS", "reason": "TEST_ONLY_FORCED_NO_RATIFIED_DEFINITION_EXISTS",
            }),
            mock.patch.object(PROMO, "evaluate_material_blocker", return_value={
                "status": "PASS", "reason": "TEST_ONLY_FORCED_NO_COVERAGE_EXISTS",
            }),
        ):
            result = P59.evaluate_candidate(
                self._candidate_row(),
                regime_payload=unknown_regime_payload(),
                market_evidence_packet=packet,
                universe_row=row,
                policy=P59.load_policy(),
                universe_policy=UNI.load_policy(),
                evaluation_as_of=EVAL_AS_OF,
                paper_account_state=paper_account_state(),
                fee_rate="0.0005",
                known_idempotency_keys=set(),
            )
        self.assertEqual(result["eligibility_state"], P59.STATE_PAPER_BUY_ELIGIBLE)
        for name in P59.CRITERIA:
            self.assertIn(result["criteria"][name]["status"], ("PASS",))
        self.assertIsNotNone(result["order_draft"]["quantity"])
        self.assertIsNotNone(result["order_draft"]["duplicate_guard_key"])
        self.assertTrue(all(v is False for v in result["authority"].values()))

    def test_without_mocks_real_evaluation_never_exceeds_watch(self):
        """The unmocked, real end-to-end path -- proving today's genuine
        ceiling is WATCH, never PAPER_BUY_ELIGIBLE, even with every
        mechanically-computable criterion satisfied."""
        packet = market_evidence_packet(breakout=True, four_hour_direction="UP", daily_direction="UP")
        row = universe_row(caution_any=False)
        result = P59.evaluate_candidate(
            self._candidate_row(),
            regime_payload=unknown_regime_payload(),
            market_evidence_packet=packet,
            universe_row=row,
            policy=P59.load_policy(),
            universe_policy=UNI.load_policy(),
            evaluation_as_of=EVAL_AS_OF,
            paper_account_state=paper_account_state(),
            fee_rate="0.0005",
            known_idempotency_keys=set(),
        )
        self.assertEqual(result["eligibility_state"], P59.STATE_WATCH)
        self.assertEqual(result["criteria"]["REGIME_PERMITS_ENTRY"]["status"], "UNKNOWN")
        self.assertEqual(result["criteria"]["BREAKOUT_OR_PULLBACK"]["status"], "PASS")
        self.assertEqual(result["criteria"]["TRIGGER_TIMEFRAME_ALIGNMENT"]["status"], "PASS")


# ---------------------------------------------------------------------------
# Order-draft null/UNKNOWN blocks PAPER_BUY_ELIGIBLE invariant
# ---------------------------------------------------------------------------

class NullFieldNeverEligibleTests(unittest.TestCase):
    def test_non_paper_buy_eligible_rows_have_fully_null_order_draft(self):
        packet = market_evidence_packet(breakout=True, four_hour_direction="UP", daily_direction="UP")
        row = universe_row(caution_any=False)
        result = P59.evaluate_candidate(
            {"market": "KRW-ETH", "canonical_asset_id": "ETH", "promotion_state": "FOCUSED_REVIEW"},
            regime_payload=unknown_regime_payload(),
            market_evidence_packet=packet,
            universe_row=row,
            policy=P59.load_policy(),
            universe_policy=UNI.load_policy(),
            evaluation_as_of=EVAL_AS_OF,
            paper_account_state=paper_account_state(),
            fee_rate="0.0005",
            known_idempotency_keys=set(),
        )
        self.assertNotEqual(result["eligibility_state"], P59.STATE_PAPER_BUY_ELIGIBLE)
        self.assertTrue(all(v is None for v in result["order_draft"].values()))


# ---------------------------------------------------------------------------
# Production-empty confirmation: real, unmocked P5-08 output over today's
# repository state yields zero FOCUSED_REVIEW rows, hence zero P5-09
# candidates -- the correct, expected state, not a bug.
# ---------------------------------------------------------------------------

class ProductionEmptyTests(unittest.TestCase):
    """P3-12's universe policy is genuinely `PROPOSED_PAPER_BASELINE_
    UNRATIFIED` today, so no market can even reach TRADEABLE_UNIVERSE/
    PAPER_ELIGIBLE in real production output -- P5-08's own test file
    establishes this exact mocking pattern to get ANY in-scope row at all.
    Mocking the universe policy/taxonomy to RATIFIED here isolates the
    REGIME cause specifically: even in a hypothetical near-term world where
    P3-12's policy is ratified, P1-CR-08's REGIME boundary alone is enough
    to cap every candidate at WATCH, so P5-09 still sees zero FOCUSED_REVIEW
    rows and produces zero PAPER_BUY_ELIGIBLE candidates -- the correct,
    expected state today, not a bug.
    """

    def setUp(self):
        policy = UNI.load_policy()
        taxonomy = UNI.load_taxonomy()
        policy = copy.deepcopy(policy)
        taxonomy = copy.deepcopy(taxonomy)
        policy["approval_status"] = "RATIFIED"
        taxonomy["approval_status"] = "RATIFIED"
        self.policy_patch = mock.patch.object(UNI, "load_policy", return_value=policy)
        self.taxonomy_patch = mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy)
        self.policy_patch.start()
        self.taxonomy_patch.start()
        self.addCleanup(self.taxonomy_patch.stop)
        self.addCleanup(self.policy_patch.stop)

    def _promotion_packet(self):
        packet = universe_packet([universe_row(market="KRW-ETH", state=UNI.STATE_PAPER_ELIGIBLE)])
        regime = unknown_regime_payload()
        return PROMO.build_promotion_packet(packet, regime, {}, None, evaluation_as_of=EVAL_AS_OF)

    def test_real_p5_08_output_yields_zero_candidates(self):
        promotion_packet = self._promotion_packet()
        self.assertEqual(promotion_packet["summary"]["focused_review_count"], 0)
        self.assertEqual(promotion_packet["candidates"][0]["criteria"]["REGIME"]["status"], "UNKNOWN")
        result = P59.build_eligibility_packet(promotion_packet, evaluation_as_of=EVAL_AS_OF)
        self.assertEqual(result["focused_review_input_count"], 0)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["summary"]["paper_buy_eligible_count"], 0)

    def test_validate_output_roundtrips_the_empty_packet(self):
        promotion_packet = self._promotion_packet()
        result = P59.build_eligibility_packet(promotion_packet, evaluation_as_of=EVAL_AS_OF)
        self.assertEqual(P59.validate_output(result), result)

    def test_rehashed_embedded_policy_substitution_is_rejected(self):
        promotion_packet = self._promotion_packet()
        result = P59.build_eligibility_packet(promotion_packet, evaluation_as_of=EVAL_AS_OF)
        forged = copy.deepcopy(result)
        forged["source"]["policy"]["breakout"]["volume_ratio_min"] = "0.1"
        forged["payload_sha256"] = P59.payload_sha256(
            {key: value for key, value in forged.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(
            P59.CryptoPaperBuyEligibilityError, "POLICY_REPOSITORY_PIN_MISMATCH",
        ):
            P59.validate_output(forged)


class DeterminismTests(unittest.TestCase):
    def setUp(self):
        policy = copy.deepcopy(UNI.load_policy())
        taxonomy = copy.deepcopy(UNI.load_taxonomy())
        policy["approval_status"] = "RATIFIED"
        taxonomy["approval_status"] = "RATIFIED"
        self.policy_patch = mock.patch.object(UNI, "load_policy", return_value=policy)
        self.taxonomy_patch = mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy)
        self.policy_patch.start()
        self.taxonomy_patch.start()
        self.addCleanup(self.taxonomy_patch.stop)
        self.addCleanup(self.policy_patch.stop)

    def test_build_eligibility_packet_deterministic(self):
        packet = universe_packet([universe_row(market="KRW-ETH", state=UNI.STATE_PAPER_ELIGIBLE)])
        regime = unknown_regime_payload()
        promotion_packet = PROMO.build_promotion_packet(
            packet, regime, {}, None, evaluation_as_of=EVAL_AS_OF,
        )
        first = P59.build_eligibility_packet(copy.deepcopy(promotion_packet), evaluation_as_of=EVAL_AS_OF)
        second = P59.build_eligibility_packet(copy.deepcopy(promotion_packet), evaluation_as_of=EVAL_AS_OF)
        self.assertEqual(P59.canonical_json(first), P59.canonical_json(second))
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])

    def test_evaluate_candidate_deterministic(self):
        packet = market_evidence_packet(breakout=True)
        row = universe_row(caution_any=False)
        args = dict(
            regime_payload=unknown_regime_payload(),
            market_evidence_packet=packet,
            universe_row=row,
            policy=P59.load_policy(),
            universe_policy=UNI.load_policy(),
            evaluation_as_of=EVAL_AS_OF,
            paper_account_state=paper_account_state(),
            fee_rate="0.0005",
            known_idempotency_keys=set(),
        )
        candidate_row = {"market": "KRW-ETH", "canonical_asset_id": "ETH", "promotion_state": "FOCUSED_REVIEW"}
        first = P59.evaluate_candidate(copy.deepcopy(candidate_row), **copy.deepcopy(args))
        second = P59.evaluate_candidate(copy.deepcopy(candidate_row), **copy.deepcopy(args))
        self.assertEqual(P59.canonical_json(first), P59.canonical_json(second))


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        policy = copy.deepcopy(UNI.load_policy())
        taxonomy = copy.deepcopy(UNI.load_taxonomy())
        policy["approval_status"] = "RATIFIED"
        taxonomy["approval_status"] = "RATIFIED"
        self.policy_patch = mock.patch.object(UNI, "load_policy", return_value=policy)
        self.taxonomy_patch = mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy)
        self.policy_patch.start()
        self.taxonomy_patch.start()
        self.addCleanup(self.taxonomy_patch.stop)
        self.addCleanup(self.policy_patch.stop)

    def test_authority_false_everywhere(self):
        self.assertTrue(all(v is False for v in P59._ROW_AUTHORITY.values()))
        packet = universe_packet([universe_row(market="KRW-ETH", state=UNI.STATE_PAPER_ELIGIBLE)])
        regime = unknown_regime_payload()
        promotion_packet = PROMO.build_promotion_packet(
            packet, regime, {}, None, evaluation_as_of=EVAL_AS_OF,
        )
        result = P59.build_eligibility_packet(promotion_packet, evaluation_as_of=EVAL_AS_OF)
        self.assertEqual(result["authority"], P59._ROW_AUTHORITY)


class ContractAndPolicyTests(unittest.TestCase):
    def test_load_contract_pinned(self):
        contract = P59.load_contract()
        self.assertEqual(contract["contract_version"], "crypto_paper_buy_eligibility_contract/2")
        self.assertEqual(P59.OUTPUT_SCHEMA_VERSION, "crypto_paper_buy_eligibility_packet/2")
        self.assertTrue(all(v is False for v in contract["authority"].values()))

    def test_load_policy_pinned(self):
        policy = P59.load_policy()
        self.assertEqual(policy["baseline_label"], "PROPOSED_PAPER_BASELINE")
        self.assertTrue(policy["not_a_live_capital_limit"])
        self.assertNotEqual(policy["approval_status"], "RATIFIED")


if __name__ == "__main__":
    unittest.main()
