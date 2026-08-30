#!/usr/bin/env python3
"""Korea five-axis to staged-symbol review bridge regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("korea_symbol_market_review", ROOT / "decision" / "korea_symbol_market_review.py")
REVIEW = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REVIEW)

SHADOW_SPEC = importlib.util.spec_from_file_location(
    "krx_shadow_strategy", ROOT / "decision" / "krx_shadow_strategy.py"
)
SHADOW = importlib.util.module_from_spec(SHADOW_SPEC)
assert SHADOW_SPEC.loader is not None
SHADOW_SPEC.loader.exec_module(SHADOW)


def current_inputs():
    market = json.loads((ROOT / "data" / "latest_korea_market_signals.json").read_text(encoding="utf-8"))
    stages = json.loads((ROOT / "data" / "stage_history.json").read_text(encoding="utf-8"))
    return market, stages


class CurrentEvidenceTests(unittest.TestCase):
    def test_current_packet_connects_five_axes_price_and_flow_without_orders(self):
        market, stages = current_inputs()
        result = REVIEW.build_review(market, stages)
        self.assertEqual(REVIEW.validate_output(result), result)
        self.assertEqual(result["five_axis"]["ratio"], "5/5")
        self.assertEqual(result["five_axis"]["aggregate_regime"], "UNKNOWN")
        self.assertEqual(result["five_axis"]["final_policy"], "PENDING_POLICY_RATIFICATION")
        by_symbol = {row["symbol"]: row for row in result["symbols"]}
        self.assertEqual(set(by_symbol), {"012450", "298040", "329180"})
        candidate = by_symbol["298040"]
        source_row = result["source"]["stage_snapshot"]["subjects"]["298040"][
            "latest_confirmed_row"
        ]
        self.assertEqual(candidate["pipeline_stage"], "Candidate")
        self.assertEqual(candidate["price_context"]["close_krw"], source_row["close"])
        self.assertEqual(
            candidate["flow_context"]["foreign_net_value_krw"],
            source_row["net_value"]["외국인합계"],
        )
        self.assertEqual(
            candidate["flow_context"]["institution_net_value_krw"],
            source_row["net_value"]["기관합계"],
        )
        foreign_positive = source_row["net_value"]["외국인합계"] > 0
        institution_positive = source_row["net_value"]["기관합계"] > 0
        expected_flow_fact = {
            (True, True): "FOREIGN_AND_INSTITUTION_NET_BUY",
            (True, False): "FOREIGN_NET_BUY_INSTITUTION_NET_SELL",
            (False, True): "INSTITUTION_NET_BUY_FOREIGN_NET_SELL",
            (False, False): "FOREIGN_AND_INSTITUTION_NET_SELL",
        }[(foreign_positive, institution_positive)]
        self.assertEqual(candidate["entry_review"]["state"], "WAIT")
        self.assertIn(expected_flow_fact, candidate["observed_facts"])
        self.assertEqual(result["summary"]["automatic_entry_count"], 0)
        self.assertEqual(result["summary"]["automatic_exit_count"], 0)
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_rehashing_tampered_source_cannot_change_output(self):
        market, stages = current_inputs()
        packet = REVIEW.build_review(market, stages)
        tampered = copy.deepcopy(packet)
        tampered["source"]["stage_snapshot"]["subjects"]["298040"]["latest_confirmed_row"]["close"] = 1
        unsigned = {key: value for key, value in tampered.items() if key != "packet_sha256"}
        tampered["packet_sha256"] = REVIEW.payload_sha256(unsigned)
        with self.assertRaisesRegex(REVIEW.KoreaSymbolMarketReviewError, "OUTPUT_DERIVATION_MISMATCH"):
            REVIEW.validate_output(tampered)

    def test_open_authority_fails_closed(self):
        market, stages = current_inputs()
        contract = REVIEW.load_contract()
        contract["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(REVIEW.KoreaSymbolMarketReviewError, "AUTHORITY_INVALID"):
            REVIEW.build_review(market, stages, contract=contract)

    def test_populate_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="korea_symbol_review_") as tmp:
            first = REVIEW.populate(output_root=Path(tmp) / "evidence", latest_path=Path(tmp) / "latest.json")
            second = REVIEW.populate(output_root=Path(tmp) / "evidence", latest_path=Path(tmp) / "latest.json")
        self.assertEqual(first["outcome"], "populated")
        self.assertEqual(second["outcome"], "verified_existing")
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])


def _shadow_window(available_at="2026-08-27T00:15:00Z", valid_until="2026-08-27T01:00:00Z"):
    return {"available_at": available_at, "valid_until": valid_until}


def _shadow_bar(interval, opened_at, closed_at, *, high=100_000):
    return {
        "interval": interval,
        "completed": True,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "available_at": closed_at,
        "valid_until": "2026-08-27T01:00:00Z",
        "open": 99_000,
        "high": high,
        "low": 98_000,
        "close": 99_500,
        "source_sha256": "a" * 64,
    }


def _shadow_candidate(
    symbol="005930",
    candidate_id="KRX-SHADOW-005930",
    rank=1,
    authority_status="RATIFIED_SHADOW_ONLY",
):
    window = _shadow_window()
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "briefing_rank": rank,
        "identity": {
            "status": "RESOLVED",
            "symbol": symbol,
            "canonical_instrument_id": f"KRX:{symbol}:COMMON",
            **window,
            "source_sha256": "b" * 64,
        },
        "eligibility": {
            "status": "ELIGIBLE",
            "authority_status": authority_status,
            **window,
            "source_sha256": "c" * 64,
        },
        "market_context": {
            "status": "AVAILABLE",
            "authority_status": authority_status,
            "entry_allowed": True,
            "hold_allowed": True,
            **window,
            "source_sha256": "d" * 64,
        },
        "relative_strength": {
            "status": "AVAILABLE",
            "authority_status": authority_status,
            "entry_confirmed": True,
            "hold_confirmed": True,
            **window,
            "source_sha256": "e" * 64,
        },
        "liquidity": {
            "status": "AVAILABLE",
            "authority_status": authority_status,
            "eligible": True,
            "max_shadow_quantity": 50,
            **window,
            "source_sha256": "f" * 64,
        },
        "bars": {
            "15m": _shadow_bar("15m", "2026-08-27T00:00:00Z", "2026-08-27T00:15:00Z"),
            "1h": _shadow_bar("1h", "2026-08-26T05:00:00Z", "2026-08-26T06:00:00Z"),
            "1d": _shadow_bar("1d", "2026-08-26T00:00:00Z", "2026-08-26T06:30:00Z"),
        },
        "quote": {
            "status": "AVAILABLE",
            "observed_at": "2026-08-27T00:29:59Z",
            "available_at": "2026-08-27T00:29:59Z",
            "valid_until": "2026-08-27T00:30:30Z",
            "last": 101_000,
            "bid": 100_900,
            "ask": 101_000,
            "source_sha256": "1" * 64,
        },
        "position": {
            "status": "FLAT",
            "entry_price": None,
            "quantity": None,
            "opened_at": None,
            "take_profit_1_done": None,
            **window,
            "source_sha256": "5" * 64,
        },
        "trade_plan": {
            "policy_id": "KRX-SHADOW-TEST-V1",
            "status": authority_status,
            "entry_reference_price": 100_000,
            "max_entry_price": 102_000,
            "stop_price": 98_000,
            "take_profit_1_price": 106_000,
            "final_take_profit_price": 110_000,
            "take_profit_1_fraction_bps": 5_000,
            "expires_at": "2026-09-03T06:20:00Z",
            "invalidation_triggered": False,
            "exit_on_regime_block": True,
            "exit_on_relative_strength_break": True,
            "tick_size": 100,
            "entry_fee_bps": 10,
            "exit_fee_bps": 10,
            "stop_slippage_bps": 20,
            "entry_after_kst": "09:15:00",
            "session_ends_kst": "15:20:00",
            "quote_max_age_seconds": 30,
            "max_spread_bps": 25,
            **window,
            "source_sha256": "2" * 64,
        },
        "risk_budget": {
            "status": authority_status,
            "allocation_id": f"ALLOC-{symbol}",
            "allocation_scope": "PER_CANDIDATE_PREALLOCATED_FROM_ACCOUNT_RISK_BUDGET",
            "account_risk_budget_id": "ACCOUNT-RISK-BUDGET-TEST",
            "account_risk_budget_total_krw": 100_000,
            "account_committed_risk_krw": 0,
            "risk_budget_krw": 100_000,
            "account_capacity_quantity": 100,
            "current_open_positions": 0,
            **window,
            "source_sha256": "3" * 64,
        },
        "source_sha256": "4" * 64,
    }


def _shadow_input(candidates=None, *, mode="PAPER_CANARY", prior=None):
    value = {
        "schema_version": "krx_shadow_strategy_input/1",
        "contract_version": "krx_shadow_strategy/1",
        "decision_batch_id": "KRX-SHADOW-20260827-0030",
        "evaluated_at": "2026-08-27T00:29:59Z",
        "business_date": "2026-08-27",
        "mode": mode,
        "prior_decision_keys": [] if prior is None else prior,
        "candidates": [_shadow_candidate()] if candidates is None else candidates,
        "authority": copy.deepcopy(SHADOW.AUTHORITY_BOUNDARY),
    }
    value["packet_sha256"] = SHADOW.payload_sha256(value)
    return value


class KrxShadowStrategyTests(unittest.TestCase):
    def test_policy_candidate_registry_cannot_imply_ratification(self):
        policies = json.loads(
            (ROOT / "config" / "krx_shadow_strategy_policy_candidates.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("PROPOSED_UNRATIFIED_REPLAY_ONLY", policies["status"])
        self.assertFalse(policies["ratifies_strategy"])
        self.assertIsNone(policies["validation"]["minimum_sample_size"])
        self.assertFalse(policies["authority"]["strategy_policy_ratified"])
        self.assertTrue(all(
            item["status"] != "RATIFIED_SHADOW_ONLY" for item in policies["candidates"]
        ))

    def test_committed_005930_interface_fixture_is_explicit_and_non_authoritative(self):
        fixture = json.loads(
            (ROOT / "test" / "fixtures" / "krx_shadow_strategy_input.json").read_text(
                encoding="utf-8"
            )
        )
        packet = SHADOW.build_packet(fixture)
        row = packet["decisions"][0]
        self.assertEqual("005930", row["symbol"])
        self.assertEqual("ENTER", row["diagnostic_action"])
        self.assertEqual("NO_TRADE", row["action"])
        self.assertEqual(0, packet["summary"]["order_draft_count"])

    def test_ratified_shadow_entry_calculates_fee_slippage_loss_but_never_order(self):
        packet = SHADOW.build_packet(_shadow_input())
        row = packet["decisions"][0]
        self.assertEqual("NO_TRADE", row["action"])
        self.assertEqual("ENTER", row["diagnostic_action"])
        self.assertEqual(3_395, row["diagnostic"]["planned_loss_per_share_krw"])
        self.assertEqual(29, row["diagnostic"]["quantity"])
        self.assertEqual(98_455, row["diagnostic"]["planned_loss_krw"])
        self.assertIsNone(row["risk_plan"]["quantity"])
        self.assertIsNone(row["action_stage"])
        self.assertIsNone(row["order"]["order_draft"])
        self.assertIsNone(row["order"]["submission_authority"])
        self.assertFalse(row["authority"]["paper_order_write"])
        self.assertFalse(row["authority"]["real_trading"])
        self.assertEqual(packet, SHADOW.validate_packet(packet))

    def test_draft_policy_preserves_diagnostic_enter_but_final_action_is_no_trade(self):
        candidate = _shadow_candidate(authority_status="DIAGNOSTIC_DRAFT_NOT_AUTHORITY")
        packet = SHADOW.build_packet(_shadow_input([candidate]))
        row = packet["decisions"][0]
        self.assertEqual("ENTER", row["diagnostic_action"])
        self.assertEqual("NO_TRADE", row["action"])
        self.assertIn("SHADOW_POLICY_OR_ELIGIBILITY_AUTHORITY_UNRATIFIED", row["reason_codes"])
        self.assertIn("계산상 SHADOW ENTER", row["briefing_sentence"])

    def test_dynamic_candidates_support_enter_hold_and_exit_deterministically(self):
        entering = _shadow_candidate("005930", "KRX-SHADOW-005930", 2)
        holding = _shadow_candidate("000660", "KRX-SHADOW-000660", 1)
        holding["position"] = {
            "status": "OPEN", "entry_price": 100_000, "quantity": 10,
            "opened_at": "2026-08-26T00:30:00Z", "take_profit_1_done": False,
            **_shadow_window(), "source_sha256": "5" * 64,
        }
        exiting = _shadow_candidate("071050", "KRX-SHADOW-071050", 3)
        exiting["position"] = {
            "status": "OPEN", "entry_price": 100_000, "quantity": 8,
            "opened_at": "2026-08-26T00:30:00Z", "take_profit_1_done": False,
            **_shadow_window(), "source_sha256": "5" * 64,
        }
        exiting["quote"].update({"last": 97_000, "bid": 97_000, "ask": 97_100})
        for candidate in (entering, holding, exiting):
            candidate["risk_budget"]["account_risk_budget_total_krw"] = 300_000
            candidate["risk_budget"]["account_committed_risk_krw"] = 100_000
            candidate["risk_budget"]["current_open_positions"] = 2
        first = SHADOW.build_packet(_shadow_input([entering, holding, exiting], mode="DYNAMIC_BRIEFING"))
        second = SHADOW.build_packet(_shadow_input([copy.deepcopy(entering), copy.deepcopy(holding), copy.deepcopy(exiting)], mode="DYNAMIC_BRIEFING"))
        self.assertEqual(first, second)
        self.assertEqual(["000660", "005930", "071050"], [row["symbol"] for row in first["decisions"]])
        self.assertEqual(["NO_TRADE", "NO_TRADE", "NO_TRADE"], [row["action"] for row in first["decisions"]])
        self.assertEqual(["HOLD", "ENTER", "EXIT"], [row["diagnostic_action"] for row in first["decisions"]])
        self.assertEqual("STOP", first["decisions"][2]["diagnostic"]["action_stage"])

    def test_completed_bar_stale_identity_and_four_hour_boundaries_fail_closed(self):
        candidate = _shadow_candidate()
        candidate["bars"]["15m"]["completed"] = False
        candidate["quote"]["observed_at"] = "2026-08-27T00:29:00Z"
        candidate["quote"]["available_at"] = "2026-08-27T00:29:00Z"
        candidate["quote"]["valid_until"] = "2026-08-27T00:29:58Z"
        candidate["identity"]["status"] = "UNRESOLVED"
        candidate["identity"]["canonical_instrument_id"] = None
        packet = SHADOW.build_packet(_shadow_input([candidate]))
        row = packet["decisions"][0]
        self.assertEqual("NO_TRADE", row["action"])
        self.assertIn("BAR_15M_INCOMPLETE", row["reason_codes"])
        self.assertIn("QUOTE_STALE", row["reason_codes"])
        self.assertIn("IDENTITY_NOT_RESOLVED", row["reason_codes"])

        with_four_hour = _shadow_candidate()
        with_four_hour["bars"]["4h"] = copy.deepcopy(with_four_hour["bars"]["1h"])
        with_four_hour["bars"]["4h"]["interval"] = "4h"
        with self.assertRaisesRegex(SHADOW.KrxShadowStrategyError, "BAR_INTERVAL_SET_INVALID"):
            SHADOW.build_packet(_shadow_input([with_four_hour]))

    def test_duplicate_gap_tick_size_and_canary_limits_are_no_trade(self):
        baseline = SHADOW.build_packet(_shadow_input())
        duplicate_input = _shadow_input(prior=[baseline["decisions"][0]["decision_key"]])
        duplicate_input["decision_batch_id"] = "KRX-SHADOW-RETRY-20260827-0030"
        duplicate_input["packet_sha256"] = SHADOW.payload_sha256(
            {key: value for key, value in duplicate_input.items() if key != "packet_sha256"}
        )
        duplicate = SHADOW.build_packet(duplicate_input)
        self.assertIn("DUPLICATE_DECISION", duplicate["decisions"][0]["reason_codes"])
        self.assertEqual("NO_TRADE", duplicate["decisions"][0]["action"])

        gap = _shadow_candidate()
        gap["quote"].update({"last": 103_000, "bid": 102_900, "ask": 103_000})
        gap_packet = SHADOW.build_packet(_shadow_input([gap]))
        self.assertIn("ENTRY_GAP_ABOVE_MAX_PRICE", gap_packet["decisions"][0]["reason_codes"])

        tick = _shadow_candidate()
        tick["trade_plan"]["stop_price"] = 98_050
        tick_packet = SHADOW.build_packet(_shadow_input([tick]))
        self.assertIn("TICK_SIZE_MISMATCH:stop_price", tick_packet["decisions"][0]["reason_codes"])

        canary = _shadow_candidate("000660", "KRX-SHADOW-000660", 1)
        canary["risk_budget"]["current_open_positions"] = 1
        canary["risk_budget"]["account_committed_risk_krw"] = 50_000
        canary["risk_budget"]["account_risk_budget_total_krw"] = 200_000
        canary_packet = SHADOW.build_packet(_shadow_input([canary]))
        reasons = canary_packet["decisions"][0]["reason_codes"]
        self.assertIn("PAPER_CANARY_SYMBOL_NOT_ALLOWED", reasons)
        self.assertIn("PAPER_CANARY_POSITION_LIMIT_REACHED", reasons)

    def test_expired_invalidated_or_p10_market_guarded_flat_plan_never_enters(self):
        expired = _shadow_candidate()
        expired["trade_plan"]["expires_at"] = "2026-08-27T00:20:00Z"
        expired["trade_plan"]["invalidation_triggered"] = True
        row = SHADOW.build_packet(_shadow_input([expired]))["decisions"][0]
        self.assertEqual("NO_TRADE", row["diagnostic_action"])
        self.assertIn("ENTRY_PLAN_EXPIRED", row["reason_codes"])
        self.assertIn("PLAN_INVALIDATION_TRIGGERED", row["reason_codes"])

        old_quote = _shadow_candidate()
        old_quote["quote"]["observed_at"] = "2026-08-27T00:29:00Z"
        old_quote["quote"]["available_at"] = "2026-08-27T00:29:00Z"
        row = SHADOW.build_packet(_shadow_input([old_quote]))["decisions"][0]
        self.assertIn("QUOTE_EXCEEDS_POLICY_MAX_AGE", row["reason_codes"])

        wide = _shadow_candidate()
        wide["quote"].update({"bid": 100_000, "last": 101_000, "ask": 101_000})
        row = SHADOW.build_packet(_shadow_input([wide]))["decisions"][0]
        self.assertIn("SPREAD_EXCEEDS_POLICY_MAX", row["reason_codes"])

        outside = _shadow_candidate()
        outside["trade_plan"]["session_ends_kst"] = "09:20:00"
        row = SHADOW.build_packet(_shadow_input([outside]))["decisions"][0]
        self.assertIn("OUTSIDE_KRX_POLICY_SESSION", row["reason_codes"])

    def test_future_position_bad_bar_and_canonical_identity_mismatch_fail_closed(self):
        future = _shadow_candidate()
        future["position"] = {
            "status": "OPEN", "entry_price": 100_000, "quantity": 10,
            "opened_at": "2026-08-28T00:30:00Z", "take_profit_1_done": False,
            **_shadow_window(), "source_sha256": "5" * 64,
        }
        future["risk_budget"]["current_open_positions"] = 1
        future["risk_budget"]["account_committed_risk_krw"] = 50_000
        row = SHADOW.build_packet(_shadow_input([future]))["decisions"][0]
        self.assertIn("POSITION_OPENED_AT_FUTURE", row["reason_codes"])
        self.assertEqual("NO_TRADE", row["diagnostic_action"])

        wrong_identity = _shadow_candidate()
        wrong_identity["identity"]["canonical_instrument_id"] = "KRX:000660:COMMON"
        row = SHADOW.build_packet(_shadow_input([wrong_identity]))["decisions"][0]
        self.assertIn("IDENTITY_NOT_RESOLVED", row["reason_codes"])

        bad_bar = _shadow_candidate()
        bad_bar["bars"]["15m"]["closed_at"] = "2026-08-27T00:00:01Z"
        bad_bar["bars"]["15m"]["available_at"] = "2026-08-27T00:00:01Z"
        with self.assertRaisesRegex(SHADOW.KrxShadowStrategyError, "BAR_DURATION_INVALID:15m"):
            SHADOW.build_packet(_shadow_input([bad_bar]))

    def test_aggregate_risk_and_partial_exit_policy_are_bounded(self):
        first = _shadow_candidate("005930", "KRX-SHADOW-005930", 1)
        second = _shadow_candidate("000660", "KRX-SHADOW-000660", 2)
        with self.assertRaisesRegex(
            SHADOW.KrxShadowStrategyError,
            "AGGREGATE_RISK_ALLOCATION_EXCEEDS_ACCOUNT_BUDGET",
        ):
            SHADOW.build_packet(_shadow_input([first, second], mode="DYNAMIC_BRIEFING"))

        zero_fraction = _shadow_candidate()
        zero_fraction["trade_plan"]["take_profit_1_fraction_bps"] = 0
        with self.assertRaisesRegex(
            SHADOW.KrxShadowStrategyError,
            "TAKE_PROFIT_1_FRACTION_MUST_BE_PARTIAL",
        ):
            SHADOW.build_packet(_shadow_input([zero_fraction]))

        partial = _shadow_candidate()
        partial["position"] = {
            "status": "OPEN", "entry_price": 100_000, "quantity": 10,
            "opened_at": "2026-08-26T00:30:00Z", "take_profit_1_done": False,
            **_shadow_window(), "source_sha256": "5" * 64,
        }
        partial["risk_budget"]["current_open_positions"] = 1
        partial["risk_budget"]["account_committed_risk_krw"] = 50_000
        partial["quote"].update({"bid": 106_000, "last": 106_000, "ask": 106_100})
        row = SHADOW.build_packet(_shadow_input([partial]))["decisions"][0]
        self.assertEqual("EXIT", row["diagnostic_action"])
        self.assertEqual("TAKE_PROFIT_1", row["diagnostic"]["action_stage"])
        self.assertEqual(5, row["diagnostic"]["quantity"])
        self.assertIsNone(row["risk_plan"]["quantity"])

    def test_entry_only_guards_do_not_block_open_position_exit_and_exit_priority_is_conservative(self):
        invalidated = _shadow_candidate()
        invalidated["position"] = {
            "status": "OPEN", "entry_price": 100_000, "quantity": 10,
            "opened_at": "2026-08-26T00:30:00Z", "take_profit_1_done": False,
            **_shadow_window(), "source_sha256": "5" * 64,
        }
        invalidated["risk_budget"]["current_open_positions"] = 1
        invalidated["risk_budget"]["account_committed_risk_krw"] = 50_000
        invalidated["trade_plan"]["invalidation_triggered"] = True
        invalidated["trade_plan"]["session_ends_kst"] = "09:20:00"
        invalidated["quote"].update({"bid": 100_000, "last": 101_000, "ask": 101_000})
        row = SHADOW.build_packet(_shadow_input([invalidated]))["decisions"][0]
        self.assertEqual("EXIT", row["diagnostic_action"])
        self.assertEqual("INVALIDATION", row["diagnostic"]["action_stage"])
        self.assertNotIn("OUTSIDE_KRX_POLICY_SESSION", row["reason_codes"])
        self.assertNotIn("SPREAD_EXCEEDS_POLICY_MAX", row["reason_codes"])

        regime_break = _shadow_candidate()
        regime_break["position"] = copy.deepcopy(invalidated["position"])
        regime_break["risk_budget"]["current_open_positions"] = 1
        regime_break["risk_budget"]["account_committed_risk_krw"] = 50_000
        regime_break["market_context"]["hold_allowed"] = False
        regime_break["quote"].update({"bid": 106_000, "last": 106_000, "ask": 106_100})
        row = SHADOW.build_packet(_shadow_input([regime_break]))["decisions"][0]
        self.assertEqual("REGIME_INVALIDATION", row["diagnostic"]["action_stage"])
        self.assertEqual(10, row["diagnostic"]["quantity"])

    def test_latest_completed_15m_exact_spread_and_account_risk_consistency_fail_closed(self):
        previous_day = _shadow_candidate()
        previous_day["bars"]["15m"].update({
            "opened_at": "2026-08-26T00:00:00Z",
            "closed_at": "2026-08-26T00:15:00Z",
            "available_at": "2026-08-26T00:15:00Z",
        })
        row = SHADOW.build_packet(_shadow_input([previous_day]))["decisions"][0]
        self.assertIn("BAR_15M_NOT_LATEST_COMPLETED_SESSION_SLOT", row["reason_codes"])

        fractional_overage = _shadow_candidate()
        fractional_overage["trade_plan"]["tick_size"] = 5
        fractional_overage["quote"].update({"bid": 100_000, "last": 100_255, "ask": 100_255})
        row = SHADOW.build_packet(_shadow_input([fractional_overage]))["decisions"][0]
        self.assertIn("SPREAD_EXCEEDS_POLICY_MAX", row["reason_codes"])

        unreported_open = _shadow_candidate()
        unreported_open["position"] = {
            "status": "OPEN", "entry_price": 100_000, "quantity": 10,
            "opened_at": "2026-08-26T00:30:00Z", "take_profit_1_done": False,
            **_shadow_window(), "source_sha256": "5" * 64,
        }
        with self.assertRaisesRegex(
            SHADOW.KrxShadowStrategyError,
            "OPEN_POSITION_COUNT_BELOW_BATCH_OPEN_CANDIDATES",
        ):
            SHADOW.build_packet(_shadow_input([unreported_open]))

        existing = _shadow_candidate("005930", "KRX-SHADOW-005930", 1)
        existing["position"] = copy.deepcopy(unreported_open["position"])
        flat = _shadow_candidate("000660", "KRX-SHADOW-000660", 2)
        for candidate in (existing, flat):
            candidate["risk_budget"]["current_open_positions"] = 1
            candidate["risk_budget"]["account_committed_risk_krw"] = 100_000
            candidate["risk_budget"]["account_risk_budget_total_krw"] = 150_000
        with self.assertRaisesRegex(
            SHADOW.KrxShadowStrategyError,
            "AGGREGATE_RISK_ALLOCATION_EXCEEDS_ACCOUNT_BUDGET",
        ):
            SHADOW.build_packet(_shadow_input([existing, flat], mode="DYNAMIC_BRIEFING"))

    def test_malformed_embedded_source_is_a_domain_error(self):
        packet = SHADOW.build_packet(_shadow_input())
        packet["source"] = []
        packet["packet_sha256"] = SHADOW.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(SHADOW.KrxShadowStrategyError, "SOURCE_FIELDS_INVALID"):
            SHADOW.validate_packet(packet)

    def test_merged_krx_gate_lock_remains_compatible_with_shadow_output(self):
        assessment = json.loads(
            (
                ROOT
                / "evidence"
                / "krx_paper_gate"
                / "2026-08-30"
                / "assessment.json"
            ).read_text(encoding="utf-8")
        )
        gates = {row["gate_id"]: row for row in assessment["gate_results"]}
        self.assertEqual("LOCKED", assessment["current_state"])
        self.assertEqual("PASS", gates["KRX_SHADOW"]["own_status"])
        self.assertEqual("UNKNOWN", gates["KRX_SHADOW"]["status"])
        self.assertEqual("FAIL", gates["KRX_PAPER_CANARY_START"]["status"])
        self.assertTrue(all(value is False for value in assessment["authority"].values()))

        packet = SHADOW.build_packet(_shadow_input())
        self.assertEqual({"NO_TRADE"}, {row["action"] for row in packet["decisions"]})
        self.assertFalse(packet["order"]["executable"])
        self.assertIsNone(packet["order"]["order_draft"])
        self.assertIsNone(packet["order"]["submission_authority"])
        self.assertFalse(packet["authority"]["paper_order_write"])
        self.assertFalse(packet["authority"]["real_trading"])

    def test_merged_market_data_contract_is_completed_bars_not_shadow_authority(self):
        market_data = json.loads(
            (ROOT / "config" / "krx_market_data_contract.json").read_text(
                encoding="utf-8"
            )
        )
        consumer = json.loads(
            (ROOT / "config" / "krx_market_data_consumer_contract.json").read_text(
                encoding="utf-8"
            )
        )
        timeframes = market_data["supported_timeframes"]
        self.assertTrue(all(timeframes[key]["required_for_consumer"] for key in ("15m", "1h", "1d")))
        self.assertFalse(timeframes["4h"]["required_for_consumer"])
        self.assertEqual("UNRATIFIED_SESSION_BOUNDARY", timeframes["4h"]["status"])
        self.assertEqual(
            "EXACT_HASH_REQUIRED_NOT_PINNED_BY_THIS_LANE",
            consumer["external_consumers"]["shadow"]["status"],
        )
        self.assertEqual("ABSENT", consumer["freshness_dependency"]["repository_default_policy"])
        self.assertTrue(all(value is False for value in consumer["authority"].values()))

        packet = SHADOW.build_packet(_shadow_input())
        self.assertEqual("NO_TRADE", packet["decisions"][0]["action"])
        self.assertFalse(packet["order"]["executable"])

    def test_merged_registry_is_explicit_evidence_interface_not_symbol_selection_authority(self):
        registry = json.loads(
            (ROOT / "config" / "krx_investable_registry_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            "NON_AUTHORITY_EVIDENCE_CANDIDATE",
            registry["krx_paper_gate_compatibility"]["evidence_role"],
        )
        self.assertFalse(
            registry["krx_paper_gate_compatibility"]["gate_result_authorized"]
        )
        self.assertFalse(
            registry["krx_paper_gate_compatibility"]["state_transition_authorized"]
        )
        self.assertTrue(all(
            row["status"] == "UNRATIFIED" and row["proposed_threshold"] is None
            for row in registry["measurement_policy"].values()
        ))
        self.assertTrue(registry["authority"]["registry_evidence_only"])
        self.assertTrue(all(
            value is False
            for key, value in registry["authority"].items()
            if key != "registry_evidence_only"
        ))

        packet = SHADOW.build_packet(_shadow_input())
        self.assertFalse(packet["authority"]["symbol_selection_authority"])
        self.assertEqual("NO_TRADE", packet["decisions"][0]["action"])

    def test_rehashed_output_tamper_is_rejected_by_semantic_rebuild(self):
        packet = SHADOW.build_packet(_shadow_input())
        tampered = copy.deepcopy(packet)
        tampered["decisions"][0]["action"] = "EXIT"
        tampered["packet_sha256"] = SHADOW.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(SHADOW.KrxShadowStrategyError, "OUTPUT_DERIVATION_MISMATCH"):
            SHADOW.validate_packet(tampered)


if __name__ == "__main__":
    unittest.main()
