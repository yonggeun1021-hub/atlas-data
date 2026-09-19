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
        # 180B over the finalized 30-day window = a 6B/day mean, i.e. a market
        # that actually clears P3-12's ratified `min_30d_avg_krw_turnover`
        # (5B/day). The previous 10B fixture was a 333M/day market -- below the
        # ratified liquidity floor, so it could never have been a genuine
        # PAPER_ELIGIBLE row. Same value the other in-scope fixtures use
        # (test_upbit_bounded_identity_registry, test_upbit_taxonomy_schema_
        # eligible_candidate). With 30 finalized days this makes the ratified
        # 1%-of-ADV30 liquidity term 60,000,000 KRW, so the NAV 5% term binds
        # first for the default 100,000,000 KRW PAPER NAV -- which is what the
        # ratified `min(NAV x 5%, 1% x ADV30)` is supposed to do for a liquid
        # name. `adv30_bound_universe_row()` below exercises the other side.
        "trailing_30d_krw_turnover": "180000000000",
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


def adv30_bound_universe_row(*, turnover="6000000000", **kwargs) -> dict:
    """A thin but fully-historied market: 30 finalized days, so ADV30 is
    defined, but small enough that the ratified 1%-of-ADV30 term is the
    binding per-name cap instead of NAV 5%."""
    row = universe_row(**kwargs)
    row["trailing_30d_krw_turnover"] = turnover
    return row


def short_history_universe_row(*, observed=20, **kwargs) -> dict:
    """Fewer committed finalized days than the ratified window -- must fail
    closed rather than average a shorter window."""
    row = universe_row(**kwargs)
    row["observed_daily_candle_count"] = observed
    return row


def crypto_runtime_gate(regime: str, *, decision_date="2026-08-28", expected_date=None):
    """Stubs only the upstream P5-08 runtime-decision gate (which has its own
    regression in test_crypto_candidate_promotion_v3.py), exactly as
    test_crypto_paper_wiring_v2.lifted_regime_and_rotation does. Everything
    under test here -- the registry parameter resolution, the state ->
    multiplier mapping, the aggregate cap and the no-new-buys flag -- still
    runs for real."""
    expected = decision_date if expected_date is None else expected_date

    def gate(runtime_decision, *, reference_at):
        new_buys = PROMO.REGIME_GATE_V3[regime][1]
        return PROMO._regime_gate_criterion(
            regime, f"CRYPTO_RUNTIME_REGIME:{regime}:NEW_BUYS_{new_buys}",
            source_runtime_regime=regime, runtime_decision_id="TEST_ONLY",
            runtime_decision_date=decision_date, expected_decision_date=expected,
            runtime_reasons=[],
        )

    return mock.patch.object(PROMO, "evaluate_crypto_runtime_regime", gate)


def crypto_state(regime="RISK_ON", **kwargs) -> dict:
    """Genuine `resolve_crypto_allocation_state` output for `regime`."""
    with crypto_runtime_gate(regime, **kwargs):
        return P59.resolve_crypto_allocation_state({"stub": True}, reference_at=GENERATED_AT)


def risk_inputs(regime="RISK_ON", *, row=None, **kwargs) -> dict:
    """The three ratified sizing inputs `_paper_risk` now requires."""
    return {
        "crypto_state": crypto_state(regime, **kwargs),
        "universe_row": universe_row() if row is None else row,
        "universe_policy": UNI.load_policy(),
    }


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

    def test_nonpositive_computed_quantity_is_incomplete(self):
        packet = market_evidence_packet(breakout=True)
        draft = P59.build_order_draft(
            "KRW-ETH", packet, self.policy, self.universe_policy,
            evaluation_as_of=EVAL_AS_OF,
            paper_account_state=paper_account_state(total_nav_krw="0.000000000000000000000001"),
            fee_rate="0.0005",
        )
        self.assertIsNone(draft["quantity"])
        self.assertIsNone(draft["fee_amount_krw"])
        self.assertEqual(draft["fee_rate"], "0.0005")
        result = P59.evaluate_order_draft_complete(draft)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["missing_fields"], ["quantity", "fee_amount_krw"])

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
            **risk_inputs("RISK_ON"),
        )
        self.assertEqual(result["status"], "PASS")
        # RISK_ON: aggregate cap = base(0.15) x 1.00; per-name bound by NAV 5%.
        self.assertEqual(result["crypto_market_state"], "RISK_ON")
        self.assertEqual(result["crypto_state_multiplier_of_base"], "1")
        self.assertEqual(result["crypto_aggregate_cap_nav_fraction"], "0.15")
        self.assertEqual(result["per_name_cap_bound_by"], "NAV_FRACTION")
        self.assertEqual(result["per_name_cap_nav_fraction_term_krw"], "5000000")
        self.assertEqual(result["per_name_cap_liquidity_term_krw"], "60000000")
        self.assertEqual(result["per_name_effective_cap_krw"], "5000000")
        self.assertEqual(result["adv30_status"], "OBSERVED")
        self.assertEqual(result["adv30_krw"], "6000000000")
        self.assertEqual(result["adv30_finalized_day_count"], 30)

    def test_fail_when_single_asset_cap_breached(self):
        from decimal import Decimal
        tight_entry_invalidation = {"entry_price": Decimal("105"), "planned_stop_price": Decimal("104")}
        result = P59.evaluate_paper_risk_budget(
            tight_entry_invalidation, self.policy, paper_account_state(), 18,
            **risk_inputs("RISK_ON"),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("SINGLE_ASSET_PAPER_EXPOSURE_CAP", result["reason"])

    # -- GAP A: crypto market state -> ratified aggregate cap ----------------

    def test_stress_state_zeroes_the_aggregate_cap_and_denies_every_buy(self):
        """RULE.ALLOCATION.V2 STRESS multiplier is 0.00: the crypto aggregate
        cap is zero, so no buy of any size can pass."""
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("STRESS"),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["crypto_aggregate_cap_nav_fraction"], "0")
        self.assertIn("CRYPTO_STATE_AGGREGATE_CAP_ZERO:STRESS", result["reason"])
        self.assertIn("CRYPTO_STATE_NO_NEW_BUYS:STRESS:DENY", result["reason"])

    def test_unknown_state_caps_at_half_of_base_and_hard_blocks_new_buys(self):
        """UNKNOWN is "hold_current_up_to_0.50_no_new_buys": the cap falls to
        half of base AND new buys are denied outright -- not merely sized
        smaller."""
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("UNKNOWN"),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["crypto_state_multiplier_of_base"], "0.5")
        self.assertEqual(result["crypto_aggregate_cap_nav_fraction"], "0.075")
        self.assertEqual(result["crypto_new_buys"], "DENY")
        self.assertIn("CRYPTO_STATE_NO_NEW_BUYS:UNKNOWN:DENY", result["reason"])
        # The cap alone would have allowed this position (weight 0.0175 <
        # 0.075); only the hard no-new-buys flag stops it.
        self.assertNotIn("TOTAL_CRYPTO_PAPER_EXPOSURE_CAP", result["reason"])

    def test_missing_state_fails_closed_and_never_becomes_risk_on(self):
        """No state supplied at all -- must land on UNKNOWN + no new buys, and
        must never fall back to RISK_ON or to the old flat 0.05 aggregate."""
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            universe_row=universe_row(), universe_policy=UNI.load_policy(),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["crypto_market_state"], "UNKNOWN")
        self.assertEqual(result["crypto_market_state_reason"], "CRYPTO_RUNTIME_DECISION_NOT_SUPPLIED")
        self.assertEqual(result["crypto_new_buys"], "DENY")
        self.assertNotEqual(result["crypto_aggregate_cap_nav_fraction"], "0.05")

    def test_state_staler_than_ratified_gap_degrades_to_unknown_no_new_buys(self):
        """A RISK_ON decision older than CRYPTO's ratified
        maximum_observation_gap_days (2) must not keep sizing at RISK_ON."""
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("RISK_ON", decision_date="2026-08-20", expected_date="2026-08-28"),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["crypto_market_state"], "UNKNOWN")
        self.assertEqual(result["crypto_observation_gap_days"], 8)
        self.assertEqual(result["crypto_maximum_observation_gap_days"], 2)
        self.assertEqual(
            result["crypto_market_state_reason"],
            "CRYPTO_RUNTIME_DECISION_STALE_BEYOND_RATIFIED_GAP:8>2",
        )
        self.assertIn("CRYPTO_STATE_NO_NEW_BUYS:UNKNOWN:DENY", result["reason"])

    def test_state_within_ratified_gap_keeps_its_own_multiplier(self):
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("NEUTRAL", decision_date="2026-08-26", expected_date="2026-08-28"),
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["crypto_market_state"], "NEUTRAL")
        self.assertEqual(result["crypto_observation_gap_days"], 2)
        self.assertEqual(result["crypto_aggregate_cap_nav_fraction"], "0.105")

    def test_multiplier_table_is_bound_to_the_ratification_record(self):
        """The table is resolved from RULE.ALLOCATION.V2 in the rule registry
        and cross-checked against contract/3's gate -- drift raises."""
        state = crypto_state("RISK_ON")
        self.assertEqual(state["gate_source_ratification_id"], "PAPER-MARKET-ALLOCATION-V2-20260913")
        self.assertEqual(
            state["gate_source_record_sha256"],
            "345801ab907f75c4761097670430fb097e5e8d3b1e595217850fe20fd240a4c8",
        )
        drifted = dict(PROMO.REGIME_GATE_V3, NEUTRAL=("PASS", "PERMIT_SELECTIVE", "0.99", None))
        P59._ALLOCATION_PARAMS.clear()
        try:
            with mock.patch.object(PROMO, "REGIME_GATE_V3", drifted):
                with self.assertRaisesRegex(
                    P59.CryptoPaperBuyEligibilityError, "ALLOCATION_MULTIPLIER_MISMATCH:NEUTRAL",
                ):
                    P59._allocation_params()
        finally:
            P59._ALLOCATION_PARAMS.clear()

    # -- GAP B: 1%-of-ADV30 per-name liquidity cap --------------------------

    def test_adv30_bound_name_is_capped_below_the_nav_fraction(self):
        """A thin market's ratified 1%-of-ADV30 room binds before NAV 5%, and
        the record says which term bound."""
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("RISK_ON", row=adv30_bound_universe_row()),
        )
        # 6,000,000,000 / 30 = 200,000,000 ADV30; 1% = 2,000,000 < NAV 5% of
        # 5,000,000, so the liquidity term is the effective cap.
        self.assertEqual(result["adv30_krw"], "200000000")
        self.assertEqual(result["per_name_cap_nav_fraction_term_krw"], "5000000")
        self.assertEqual(result["per_name_cap_liquidity_term_krw"], "2000000")
        self.assertEqual(result["per_name_effective_cap_krw"], "2000000")
        self.assertEqual(result["per_name_cap_bound_by"], "LIQUIDITY_ADV30_FRACTION")
        # The 1,750,000 KRW position still fits under 2,000,000.
        self.assertEqual(result["status"], "PASS")

    def test_adv30_bound_name_breaches_on_a_position_the_nav_cap_would_allow(self):
        from decimal import Decimal
        result = P59.evaluate_paper_risk_budget(
            {"entry_price": Decimal("105"), "planned_stop_price": Decimal("98")},
            self.policy, paper_account_state(), 18,
            **risk_inputs("RISK_ON", row=adv30_bound_universe_row()),
        )
        # quantity 35,714.285714...; notional ~3,750,000 -- under NAV 5%
        # (5,000,000) but over the 1%-of-ADV30 room (2,000,000).
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("SINGLE_ASSET_PAPER_EXPOSURE_CAP:LIQUIDITY_ADV30_FRACTION", result["reason"])
        self.assertLess(
            Decimal(result["per_name_effective_cap_krw"]),
            Decimal(result["per_name_cap_nav_fraction_term_krw"]),
        )

    def test_short_adv_history_fails_closed_instead_of_shortening_the_window(self):
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("RISK_ON", row=short_history_universe_row(observed=20)),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["adv30_status"], "UNAVAILABLE")
        self.assertIsNone(result["adv30_krw"])
        self.assertIsNone(result["per_name_effective_cap_krw"])
        self.assertEqual(result["adv30_finalized_day_count"], 19)
        self.assertEqual(result["adv30_required_finalized_day_count"], 30)
        self.assertIn("ADV30_FINALIZED_HISTORY_INCOMPLETE:19/30", result["reason"])
        self.assertEqual(result["per_name_cap_bound_by"], "FAIL_CLOSED_LIQUIDITY_ADV30_UNAVAILABLE")

    def test_missing_turnover_fails_closed(self):
        row = universe_row()
        row["trailing_30d_krw_turnover"] = None
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("RISK_ON", row=row),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("ADV30_TRAILING_TURNOVER_MISSING", result["reason"])

    def test_exactly_thirty_finalized_days_is_sufficient(self):
        """31 observed candles -> 30 finalized (index 0, today, excluded),
        which is exactly the ratified window."""
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(), 18,
            **risk_inputs("RISK_ON", row=short_history_universe_row(observed=31)),
        )
        self.assertEqual(result["adv30_status"], "OBSERVED")
        self.assertEqual(result["adv30_finalized_day_count"], 30)
        self.assertEqual(result["status"], "PASS")

    # -- the regression #789-as-was would have shipped ----------------------

    def test_per_name_cap_can_never_equal_the_aggregate_cap(self):
        """#789 as it stood set the per-name cap (0.05) equal to the aggregate
        cap (0.05), so ONE coin could consume the entire crypto budget and
        nothing forced diversification. Under every ratified state either new
        buys are denied outright, or the per-name cap is strictly below the
        aggregate cap."""
        from decimal import Decimal
        nav = Decimal(paper_account_state()["total_nav_krw"])
        single = Decimal(self.policy["risk"]["single_asset_paper_exposure_nav_fraction"])
        checked = 0
        for regime in PROMO.REGIME_GATE_V3:
            state = crypto_state(regime)
            aggregate = state["aggregate_cap_nav_fraction"]
            result = P59.evaluate_paper_risk_budget(
                self._entry_invalidation(), self.policy, paper_account_state(), 18,
                **risk_inputs(regime),
            )
            per_name_fraction = Decimal(result["per_name_effective_cap_krw"]) / nav
            with self.subTest(regime=regime):
                self.assertNotEqual(per_name_fraction, aggregate)
                if not state["no_new_buys"]:
                    self.assertLess(per_name_fraction, aggregate)
                    self.assertEqual(result["status"], "PASS")
                else:
                    self.assertEqual(result["status"], "FAIL")
            checked += 1
        self.assertEqual(checked, 5)
        # And the flat aggregate field that made the collision possible is gone.
        self.assertNotIn("total_crypto_paper_exposure_nav_fraction", self.policy["risk"])
        self.assertEqual(single, Decimal("0.05"))

    def test_old_max_concurrent_positions_cap_is_superseded_and_not_enforced(self):
        """RATIFIED (build plan row C4, 2026-09-18): the old fixed
        max_concurrent_paper_positions=3 cap is superseded ("옛 코인 3종목
        한도 대체") -- NameRoom/aggregate-room bind instead. A 4th position
        that would have tripped the old count-based cap must now PASS as
        long as it stays within the (still-enforced) single-asset and
        aggregate NAV caps.
        """
        existing = [
            {"asset_id": f"X{i}", "planned_loss_nav_fraction": "0.0001", "portfolio_weight_nav_fraction": "0.01"}
            for i in range(3)
        ]
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy, paper_account_state(open_positions=existing), 18,
            **risk_inputs("RISK_ON"),
        )
        self.assertEqual(result["status"], "PASS")
        self.assertNotIn("MAX_CONCURRENT_PAPER_POSITIONS", str(result))
        self.assertEqual(result["projected_open_position_count"], 4)

    def test_max_concurrent_paper_positions_field_removed_from_policy(self):
        """Pins the ratified removal: the field must not silently reappear
        without a matching ratification amending build plan row C4."""
        self.assertNotIn("max_concurrent_paper_positions", self.policy["risk"])

    def test_total_crypto_cap_uses_exposure_not_planned_loss(self):
        # Seven 2%-weight positions = 0.14 existing exposure, against a tiny
        # 0.0007 aggregate planned loss. Only the exposure sum can breach the
        # ratified RISK_ON aggregate cap of 0.15 (0.14 + 0.0175 = 0.1575).
        existing = [
            {
                "asset_id": f"X{i}",
                "planned_loss_nav_fraction": "0.0001",
                "portfolio_weight_nav_fraction": "0.02",
            }
            for i in range(7)
        ]
        result = P59.evaluate_paper_risk_budget(
            self._entry_invalidation(), self.policy,
            paper_account_state(open_positions=existing), 18,
            **risk_inputs("RISK_ON"),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("TOTAL_CRYPTO_PAPER_EXPOSURE_CAP", result["reason"])
        from decimal import Decimal
        self.assertEqual(result["projected_total_planned_loss_nav_fraction"], "0.0032")
        # Exposure (~0.1575) is what breaches the ratified 0.15 RISK_ON cap;
        # the aggregate planned loss (0.0032) is nowhere near it.
        self.assertGreater(
            Decimal(result["projected_total_crypto_exposure_nav_fraction"]),
            Decimal(result["crypto_aggregate_cap_nav_fraction"]),
        )
        self.assertLess(
            Decimal(result["projected_total_planned_loss_nav_fraction"]),
            Decimal(result["crypto_aggregate_cap_nav_fraction"]),
        )


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
            crypto_runtime_gate("RISK_ON"),
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
                crypto_runtime_decision={"stub": True},
                regime_reference_at=GENERATED_AT,
            )
        self.assertEqual(result["eligibility_state"], P59.STATE_PAPER_BUY_ELIGIBLE)
        for name in P59.CRITERIA:
            self.assertIn(result["criteria"][name]["status"], ("PASS",))
        self.assertIsNotNone(result["order_draft"]["quantity"])
        self.assertIsNotNone(result["order_draft"]["duplicate_guard_key"])
        self.assertTrue(all(v is False for v in result["authority"].values()))

    def test_zero_quantity_candidate_waits_with_null_draft(self):
        packet = market_evidence_packet(breakout=True, four_hour_direction="UP", daily_direction="UP")
        row = universe_row(caution_any=False)
        with (
            mock.patch.object(PROMO, "evaluate_regime", return_value={
                "status": "PASS", "reason": "TEST_ONLY_QUANTITY_BOUNDARY_ISOLATION",
            }),
            mock.patch.object(PROMO, "evaluate_overextension", return_value={
                "status": "PASS", "reason": "TEST_ONLY_QUANTITY_BOUNDARY_ISOLATION",
            }),
            mock.patch.object(PROMO, "evaluate_material_blocker", return_value={
                "status": "PASS", "reason": "TEST_ONLY_QUANTITY_BOUNDARY_ISOLATION",
            }),
            crypto_runtime_gate("RISK_ON"),
        ):
            result = P59.evaluate_candidate(
                self._candidate_row(),
                regime_payload=unknown_regime_payload(),
                market_evidence_packet=packet,
                universe_row=row,
                policy=P59.load_policy(),
                universe_policy=UNI.load_policy(),
                evaluation_as_of=EVAL_AS_OF,
                paper_account_state=paper_account_state(total_nav_krw="0.000000000000000000000001"),
                fee_rate="0.0005",
                known_idempotency_keys=set(),
                crypto_runtime_decision={"stub": True},
                regime_reference_at=GENERATED_AT,
            )
        self.assertEqual(result["eligibility_state"], P59.STATE_WAIT)
        completeness = result["criteria"]["ORDER_DRAFT_COMPLETE"]
        self.assertEqual(completeness["status"], "UNKNOWN")
        self.assertEqual(completeness["missing_fields"], ["quantity", "fee_amount_krw"])
        self.assertTrue(all(value is None for value in result["order_draft"].values()))

    def test_without_mocks_real_evaluation_is_blocked_by_the_ratified_state_gate(self):
        """The unmocked, real end-to-end path. With no crypto runtime decision
        supplied the ratified state resolves to UNKNOWN, which DENIES new buys
        -- so today's genuine outcome is BLOCKED, not PAPER_BUY_ELIGIBLE.

        Before this change the same path returned WATCH: the aggregate cap was
        a flat 0.05 read from config and the crypto market state was not
        consulted at all, so an absent state silently permitted sizing. That is
        the fail-open this commit closes.
        """
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
        self.assertEqual(result["eligibility_state"], P59.STATE_BLOCKED)
        self.assertEqual(result["criteria"]["REGIME_PERMITS_ENTRY"]["status"], "UNKNOWN")
        self.assertEqual(result["criteria"]["BREAKOUT_OR_PULLBACK"]["status"], "PASS")
        self.assertEqual(result["criteria"]["TRIGGER_TIMEFRAME_ALIGNMENT"]["status"], "PASS")
        risk = result["criteria"]["PAPER_RISK_BUDGET"]
        self.assertEqual(risk["status"], "FAIL")
        self.assertEqual(risk["crypto_market_state"], "UNKNOWN")
        self.assertIn("CRYPTO_STATE_NO_NEW_BUYS:UNKNOWN:DENY", risk["reason"])


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
        registry = UNI.load_identity_registry()
        policy = copy.deepcopy(policy)
        taxonomy = copy.deepcopy(taxonomy)
        registry = copy.deepcopy(registry)
        policy["approval_status"] = "RATIFIED"
        policy["effective_date"] = EVAL_AS_OF
        taxonomy["approval_status"] = "RATIFIED"
        taxonomy["effective_from"] = EVAL_AS_OF
        registry["approval_status"] = "RATIFIED"
        registry["effective_from"] = EVAL_AS_OF
        self.policy_patch = mock.patch.object(UNI, "load_policy", return_value=policy)
        self.taxonomy_patch = mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy)
        self.registry_patch = mock.patch.object(UNI, "load_identity_registry", return_value=registry)
        # P3-12-GOV-05: standard test-only mock exempting this
        # hypothetical-future-ratification fixture from the exact-release
        # allowlist binding (dedicated coverage lives in
        # test_upbit_exact_release_binding.py) -- never a production bypass.
        self.exact_release_binding_patch = mock.patch.object(
            UNI.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True,
        )
        self.policy_patch.start()
        self.taxonomy_patch.start()
        self.registry_patch.start()
        self.exact_release_binding_patch.start()
        self.addCleanup(self.exact_release_binding_patch.stop)
        self.addCleanup(self.registry_patch.stop)
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
        registry = copy.deepcopy(UNI.load_identity_registry())
        policy["approval_status"] = "RATIFIED"
        policy["effective_date"] = EVAL_AS_OF
        taxonomy["approval_status"] = "RATIFIED"
        taxonomy["effective_from"] = EVAL_AS_OF
        registry["approval_status"] = "RATIFIED"
        registry["effective_from"] = EVAL_AS_OF
        self.policy_patch = mock.patch.object(UNI, "load_policy", return_value=policy)
        self.taxonomy_patch = mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy)
        self.registry_patch = mock.patch.object(UNI, "load_identity_registry", return_value=registry)
        # P3-12-GOV-05: standard test-only mock exempting this
        # hypothetical-future-ratification fixture from the exact-release
        # allowlist binding (dedicated coverage lives in
        # test_upbit_exact_release_binding.py) -- never a production bypass.
        self.exact_release_binding_patch = mock.patch.object(
            UNI.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True,
        )
        self.policy_patch.start()
        self.taxonomy_patch.start()
        self.registry_patch.start()
        self.exact_release_binding_patch.start()
        self.addCleanup(self.exact_release_binding_patch.stop)
        self.addCleanup(self.registry_patch.stop)
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
        registry = copy.deepcopy(UNI.load_identity_registry())
        policy["approval_status"] = "RATIFIED"
        policy["effective_date"] = EVAL_AS_OF
        taxonomy["approval_status"] = "RATIFIED"
        taxonomy["effective_from"] = EVAL_AS_OF
        registry["approval_status"] = "RATIFIED"
        registry["effective_from"] = EVAL_AS_OF
        self.policy_patch = mock.patch.object(UNI, "load_policy", return_value=policy)
        self.taxonomy_patch = mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy)
        self.registry_patch = mock.patch.object(UNI, "load_identity_registry", return_value=registry)
        # P3-12-GOV-05: standard test-only mock exempting this
        # hypothetical-future-ratification fixture from the exact-release
        # allowlist binding (dedicated coverage lives in
        # test_upbit_exact_release_binding.py) -- never a production bypass.
        self.exact_release_binding_patch = mock.patch.object(
            UNI.EXACT_RELEASE_BINDING, "validate_exact_release", return_value=True,
        )
        self.policy_patch.start()
        self.taxonomy_patch.start()
        self.registry_patch.start()
        self.exact_release_binding_patch.start()
        self.addCleanup(self.exact_release_binding_patch.stop)
        self.addCleanup(self.registry_patch.stop)
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
        self.assertEqual(policy["approval_status"], "PAPER_BASELINE_RATIFIED_MARKET_ALLOCATION_V2")

    def test_ratified_risk_values_pinned(self):
        """RATIFIED 2026-09-18 (build plan row C4 /
        USER_RATIFICATION_PAPER_MARKET_ALLOCATION_V2_20260913.json): the
        per-name NAV cap is 5%; the old fixed 3-position cap is gone; and the
        flat aggregate fraction is gone too, because the ratified aggregate cap
        is NAV0 x base(CRYPTO) x the crypto state multiplier, resolved from
        RULE.ALLOCATION.V2 rather than restated as a literal here.
        """
        policy = P59.load_policy()
        self.assertEqual(policy["risk"]["single_asset_paper_exposure_nav_fraction"], "0.05")
        self.assertNotIn("max_concurrent_paper_positions", policy["risk"])
        self.assertNotIn("total_crypto_paper_exposure_nav_fraction", policy["risk"])
        self.assertEqual(
            set(policy["risk"]),
            {"per_trade_planned_loss_nav_fraction", "single_asset_paper_exposure_nav_fraction"},
        )

    def test_policy_risk_schema_rejects_reintroduced_flat_aggregate_field(self):
        """A flat aggregate fraction is exactly what let the per-name cap equal
        the aggregate cap. Re-adding it without a matching ratification must
        fail closed rather than silently re-enable single-name concentration."""
        path = P59.POLICY_PATH
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["risk"]["total_crypto_paper_exposure_nav_fraction"] = "0.05"
        tmp_path = path.parent / "_tmp_test_crypto_paper_flat_aggregate_policy.json"
        tmp_path.write_text(json.dumps(raw), encoding="utf-8")
        try:
            with self.assertRaisesRegex(
                P59.CryptoPaperBuyEligibilityError, "POLICY_RISK_FIELDS_INVALID",
            ):
                P59.load_policy(tmp_path)
        finally:
            tmp_path.unlink()

    def test_paper_risk_reads_single_asset_cap_from_config_not_hardcoded(self):
        """Proves `_paper_risk` enforces whatever the policy file says, not
        a duplicated literal -- a config edit alone must move the gate.
        Guards against the ratified 0.05 and the config drifting apart
        again the way the old 0.02 baseline drifted from build plan C4.
        """
        from decimal import Decimal

        policy = copy.deepcopy(P59.load_policy())
        entry_price, stop_price = Decimal("105"), Decimal("100")
        account = paper_account_state()

        inputs = risk_inputs("RISK_ON")

        def breaches(risk_policy):
            return P59._paper_risk(entry_price, stop_price, risk_policy, account, 18, **inputs)["breaches"]

        # At the shipped 5% cap, a ~5.25% position breaches the NAV term.
        self.assertIn("SINGLE_ASSET_PAPER_EXPOSURE_CAP:NAV_FRACTION", breaches(policy))

        # Widening the config's single-asset cap alone (no code change)
        # must make the same position pass -- proving the value is read
        # from the policy dict, not hardcoded inside `_paper_risk`.
        widened = copy.deepcopy(policy)
        widened["risk"]["single_asset_paper_exposure_nav_fraction"] = "0.50"
        self.assertNotIn(
            "SINGLE_ASSET_PAPER_EXPOSURE_CAP:NAV_FRACTION", breaches(widened),
        )

        # Narrowing it below the same position's weight must (re)breach it.
        narrowed = copy.deepcopy(policy)
        narrowed["risk"]["single_asset_paper_exposure_nav_fraction"] = "0.001"
        self.assertIn("SINGLE_ASSET_PAPER_EXPOSURE_CAP:NAV_FRACTION", breaches(narrowed))

    def test_policy_risk_schema_rejects_reintroduced_max_concurrent_field(self):
        """If a future edit re-adds `max_concurrent_paper_positions` to the
        config without also updating `load_policy`'s schema (and thereby
        without a matching ratification), loading must fail closed rather
        than silently enforcing an unratified cap again."""
        path = P59.POLICY_PATH
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["risk"]["max_concurrent_paper_positions"] = 3
        tmp_path = path.parent / "_tmp_test_crypto_paper_buy_eligibility_policy.json"
        tmp_path.write_text(json.dumps(raw), encoding="utf-8")
        try:
            with self.assertRaisesRegex(
                P59.CryptoPaperBuyEligibilityError, "POLICY_RISK_FIELDS_INVALID",
            ):
                P59.load_policy(tmp_path)
        finally:
            tmp_path.unlink()


if __name__ == "__main__":
    unittest.main()
