#!/usr/bin/env python3
"""P5-08 Crypto Candidate Promotion Rule regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "crypto_candidate_promotion.py"
SPEC = importlib.util.spec_from_file_location("crypto_candidate_promotion", MODULE_PATH)
PROMO = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PROMO)

UNI = PROMO.UPBIT_UNIVERSE
REGIME_OC = PROMO.REGIME_OUTPUT_CONTRACT

GENERATED_AT = "2026-08-28T23:59:59Z"
EVAL_AS_OF = "2026-08-28"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def universe_row(
    *,
    market="KRW-BTC",
    state=None,
    canonical_asset_id="BTC",
    caution_any=False,
    kraken_cross_exchange_reference=False,
):
    state = state or UNI.STATE_PAPER_ELIGIBLE
    return {
        "market": market,
        "state": state,
        "reason": "PAPER_ELIGIBLE_ALL_GATES_PASSED",
        "candidate_canonical_asset_id": canonical_asset_id,
        "market_event_warning": False,
        "market_event_caution_any": caution_any,
        "kraken_cross_exchange_reference": kraken_cross_exchange_reference,
        "authority": dict(UNI._ROW_AUTHORITY),
    }


def universe_packet(rows: list) -> dict:
    return {
        "schema_version": UNI.OUTPUT_SCHEMA_VERSION,
        "snapshot_date": EVAL_AS_OF,
        "evaluation_as_of": EVAL_AS_OF,
        "available_at": "2026-08-28T00:40:00Z",
        "manifest_sha256": "a" * 64,
        "markets": rows,
    }


def unknown_regime_payload(market="CRYPTO") -> dict:
    return REGIME_OC.build_unknown_output(market, GENERATED_AT)


def candle_row(close_time: str, trade_price: str, open_time: str | None = None) -> dict:
    return {
        "open_time": open_time or close_time,
        "close_time": close_time,
        "opening_price": trade_price,
        "high_price": trade_price,
        "low_price": trade_price,
        "trade_price": trade_price,
        "candle_acc_trade_price": "1000000",
        "candle_acc_trade_volume": "10",
    }


def market_evidence_packet(
    *,
    market="KRW-BTC",
    daily_prices=("100", "110"),
    four_hour_prices=("100", "110"),
    orderbook_present=True,
    trades_present=True,
):
    candles = {}
    times = ["2026-08-26T00:00:00Z", "2026-08-27T00:00:00Z", "2026-08-28T00:00:00Z"]
    daily_candles = [
        candle_row(times[i], price) for i, price in enumerate(daily_prices)
    ] if daily_prices is not None else []
    candles["1d"] = {"finalized_candle_count": len(daily_candles), "finalized_candles": daily_candles}
    four_hour_times = ["2026-08-28T12:00:00Z", "2026-08-28T16:00:00Z", "2026-08-28T20:00:00Z"]
    four_hour_candles = [
        candle_row(four_hour_times[i], price) for i, price in enumerate(four_hour_prices)
    ] if four_hour_prices is not None else []
    candles["4h"] = {"finalized_candle_count": len(four_hour_candles), "finalized_candles": four_hour_candles}
    candles["15m"] = {"finalized_candle_count": 0, "finalized_candles": []}
    candles["1h"] = {"finalized_candle_count": 0, "finalized_candles": []}

    orderbook = {"best_bid": "99000", "best_ask": "100000"} if orderbook_present else {}
    trades = {"trade_count": 5} if trades_present else {"trade_count": 0}

    return {
        "market": market,
        "candles": candles,
        "orderbook": orderbook,
        "trades": trades,
    }


def leadership_observed_window(*, asset_relative_strength=None, partial_window_assets=None) -> dict:
    return {
        "window_id": "primary_30d",
        "role": "PRIMARY",
        "status": "OBSERVED_UNCLASSIFIED",
        "unknown_reason": None,
        "asset_relative_strength": asset_relative_strength or [],
        "partial_window_assets": partial_window_assets or [],
    }


def leadership_unknown_window(reason="INSUFFICIENT_CONTIGUOUS_HISTORY") -> dict:
    return {
        "window_id": "primary_30d",
        "role": "PRIMARY",
        "status": "UNKNOWN",
        "unknown_reason": reason,
        "asset_relative_strength": [],
        "partial_window_assets": [],
    }


def leadership_output(window: dict, *, market="CRYPTO", as_of_date=EVAL_AS_OF) -> dict:
    return {
        "market": market,
        "as_of_date": as_of_date,
        "windows": [
            {"window_id": "pilot_7d", "role": "PILOT", "status": "UNKNOWN", "unknown_reason": "STUB",
             "asset_relative_strength": [], "partial_window_assets": []},
            window,
        ],
    }


# ---------------------------------------------------------------------------
# Per-criterion tests
# ---------------------------------------------------------------------------

class IdentityCriterionTests(unittest.TestCase):
    def test_pass_when_canonical_asset_id_present(self):
        result = PROMO.evaluate_identity(universe_row(canonical_asset_id="BTC"))
        self.assertEqual(result["status"], "PASS")

    def test_unknown_when_canonical_asset_id_missing(self):
        result = PROMO.evaluate_identity(universe_row(canonical_asset_id=None))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "IDENTITY_UNRATIFIED")


class TradabilityCriterionTests(unittest.TestCase):
    def test_pass_for_tradeable_universe_state(self):
        result = PROMO.evaluate_tradability(universe_row(state=UNI.STATE_TRADEABLE_UNIVERSE))
        self.assertEqual(result["status"], "PASS")

    def test_pass_for_paper_eligible_state(self):
        result = PROMO.evaluate_tradability(universe_row(state=UNI.STATE_PAPER_ELIGIBLE))
        self.assertEqual(result["status"], "PASS")

    def test_raises_for_out_of_scope_state(self):
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.evaluate_tradability(universe_row(state=UNI.STATE_OBSERVATION_POOL))


class RegimeCriterionTests(unittest.TestCase):
    def test_always_unknown_for_valid_crypto_payload(self):
        result = PROMO.evaluate_regime(unknown_regime_payload())
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "REGIME_AGGREGATE_UNAUTHORIZED_PENDING_P1_COM_05")

    def test_never_resolves_to_anything_but_unknown_across_axis_variety(self):
        """P1-CR-08 invariant: regardless of which axes are DEFINED vs
        UNDEFINED, the top-level regime value stays "UNKNOWN" -- so P5-08's
        REGIME criterion can never be anything but UNKNOWN today.
        """
        factor_variants = [
            None,
            {"TREND": {"status": "UNDEFINED", "warnings": ["DATA_CONTRACT_INCOMPLETE"]}},
        ]
        for factors in factor_variants:
            payload = REGIME_OC.build_unknown_output("CRYPTO", GENERATED_AT, factors=factors)
            self.assertEqual(payload["regime"], "UNKNOWN")
            result = PROMO.evaluate_regime(payload)
            self.assertEqual(result["status"], "UNKNOWN")

    def test_market_mismatch_raises(self):
        payload = unknown_regime_payload(market="US")
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.evaluate_regime(payload)

    def test_unauthorized_regime_value_raises_not_guessed(self):
        payload = copy.deepcopy(unknown_regime_payload())
        payload["regime"] = "RISK_OFF"
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.evaluate_regime(payload)


class TrendCriterionTests(unittest.TestCase):
    def test_pass_when_directions_agree(self):
        packet = market_evidence_packet(daily_prices=("100", "110"), four_hour_prices=("100", "105"))
        result = PROMO.evaluate_trend("KRW-BTC", packet)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["daily_direction"], "UP")
        self.assertEqual(result["four_hour_direction"], "UP")

    def test_pass_when_one_side_flat(self):
        packet = market_evidence_packet(daily_prices=("100", "100"), four_hour_prices=("100", "105"))
        result = PROMO.evaluate_trend("KRW-BTC", packet)
        self.assertEqual(result["status"], "PASS")

    def test_fail_when_directions_conflict(self):
        packet = market_evidence_packet(daily_prices=("100", "110"), four_hour_prices=("100", "90"))
        result = PROMO.evaluate_trend("KRW-BTC", packet)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("TREND_DIRECTION_CONFLICT", result["reason"])

    def test_unknown_when_evidence_packet_missing(self):
        result = PROMO.evaluate_trend("KRW-BTC", None)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "MARKET_EVIDENCE_PACKET_MISSING")

    def test_unknown_when_insufficient_finalized_candles(self):
        packet = market_evidence_packet(daily_prices=("100",), four_hour_prices=("100", "105"))
        result = PROMO.evaluate_trend("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "INSUFFICIENT_FINALIZED_CANDLES")

    def test_market_mismatch_raises(self):
        packet = market_evidence_packet(market="KRW-ETH")
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.evaluate_trend("KRW-BTC", packet)


class RelativeStrengthCriterionTests(unittest.TestCase):
    def test_pass_when_positive(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "0.050000000000"}]
        )
        result = PROMO.evaluate_relative_strength("BTC", leadership_output(window))
        self.assertEqual(result["status"], "PASS")

    def test_fail_when_not_positive(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "-0.010000000000"}]
        )
        result = PROMO.evaluate_relative_strength("BTC", leadership_output(window))
        self.assertEqual(result["status"], "FAIL")

    def test_fail_when_exactly_zero(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "0.000000000000"}]
        )
        result = PROMO.evaluate_relative_strength("BTC", leadership_output(window))
        self.assertEqual(result["status"], "FAIL")

    def test_unknown_when_identity_missing(self):
        result = PROMO.evaluate_relative_strength(None, leadership_output(leadership_observed_window()))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "IDENTITY_UNRATIFIED")

    def test_unknown_when_leadership_output_missing(self):
        result = PROMO.evaluate_relative_strength("BTC", None)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "LEADERSHIP_OUTPUT_MISSING")

    def test_unknown_when_window_unknown(self):
        result = PROMO.evaluate_relative_strength("BTC", leadership_output(leadership_unknown_window()))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("LEADERSHIP_WINDOW_UNKNOWN", result["reason"])

    def test_unknown_when_asset_not_covered(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "ETH", "relative_strength_vs_btc": "0.05"}]
        )
        result = PROMO.evaluate_relative_strength("BTC", leadership_output(window))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "LEADERSHIP_ASSET_NOT_COVERED")

    def test_unknown_when_asset_window_partial(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "0.05"}],
            partial_window_assets=[{"canonical_asset_id": "BTC"}],
        )
        result = PROMO.evaluate_relative_strength("BTC", leadership_output(window))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "LEADERSHIP_ASSET_WINDOW_INCOMPLETE")


class VolumeLiquidityCriterionTests(unittest.TestCase):
    def test_pass_when_all_families_present(self):
        packet = market_evidence_packet()
        result = PROMO.evaluate_volume_liquidity("KRW-BTC", packet)
        self.assertEqual(result["status"], "PASS")

    def test_unknown_when_packet_missing(self):
        result = PROMO.evaluate_volume_liquidity("KRW-BTC", None)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_unknown_when_orderbook_missing(self):
        packet = market_evidence_packet(orderbook_present=False)
        result = PROMO.evaluate_volume_liquidity("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["liquidity_family_present"])

    def test_unknown_when_trades_missing(self):
        packet = market_evidence_packet(trades_present=False)
        result = PROMO.evaluate_volume_liquidity("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["volume_family_present"])

    def test_unknown_when_price_family_missing(self):
        packet = market_evidence_packet(daily_prices=None)
        result = PROMO.evaluate_volume_liquidity("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["price_family_present"])


class OverextensionCriterionTests(unittest.TestCase):
    def test_always_unknown(self):
        result = PROMO.evaluate_overextension()
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "NO_RATIFIED_OVEREXTENSION_THRESHOLD")


class MaterialBlockerCriterionTests(unittest.TestCase):
    def test_pass_when_no_caution(self):
        result = PROMO.evaluate_material_blocker(universe_row(caution_any=False))
        self.assertEqual(result["status"], "PASS")

    def test_fail_when_caution_active(self):
        result = PROMO.evaluate_material_blocker(universe_row(caution_any=True))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "UPBIT_MARKET_EVENT_CAUTION_ACTIVE")

    def test_unknown_when_caution_status_missing(self):
        row = universe_row()
        row["market_event_caution_any"] = None
        result = PROMO.evaluate_material_blocker(row)
        self.assertEqual(result["status"], "UNKNOWN")


# ---------------------------------------------------------------------------
# Aggregate state-machine rule
# ---------------------------------------------------------------------------

ALL_PASS_CRITERIA = {name: {"status": "PASS", "reason": "TEST"} for name in PROMO.CRITERIA}


class AggregateStateTests(unittest.TestCase):
    def test_all_pass_yields_focused_review(self):
        state, reason = PROMO.aggregate_state(ALL_PASS_CRITERIA)
        self.assertEqual(state, PROMO.STATE_FOCUSED_REVIEW)
        self.assertEqual(reason, "ALL_CRITERIA_PASSED")

    def test_any_fail_yields_blocked_even_with_unknowns(self):
        criteria = dict(ALL_PASS_CRITERIA)
        criteria["MATERIAL_BLOCKER"] = {"status": "FAIL", "reason": "TEST"}
        criteria["REGIME"] = {"status": "UNKNOWN", "reason": "TEST"}
        state, reason = PROMO.aggregate_state(criteria)
        self.assertEqual(state, PROMO.STATE_BLOCKED)
        self.assertIn("MATERIAL_BLOCKER", reason)

    def test_any_unknown_no_fail_yields_watch(self):
        criteria = dict(ALL_PASS_CRITERIA)
        criteria["REGIME"] = {"status": "UNKNOWN", "reason": "TEST"}
        state, reason = PROMO.aggregate_state(criteria)
        self.assertEqual(state, PROMO.STATE_WATCH)
        self.assertIn("REGIME", reason)

    def test_incomplete_criteria_set_raises(self):
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.aggregate_state({"IDENTITY": {"status": "PASS", "reason": "x"}})


# ---------------------------------------------------------------------------
# Full packet build
# ---------------------------------------------------------------------------

class BuildPromotionPacketTests(unittest.TestCase):
    def _full_watch_inputs(self):
        packet = universe_packet([universe_row()])
        regime = unknown_regime_payload()
        evidence = {"KRW-BTC": market_evidence_packet()}
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "0.05"}]
        )
        leadership = leadership_output(window)
        return packet, regime, evidence, leadership

    def test_real_evaluation_always_lands_in_watch_today(self):
        """Because REGIME is UNKNOWN-by-construction, a real
        build_promotion_packet() call can never reach FOCUSED_REVIEW today
        -- WATCH is the best achievable outcome even with every other
        criterion passing.
        """
        packet, regime, evidence, leadership = self._full_watch_inputs()
        result = PROMO.build_promotion_packet(
            packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        self.assertEqual(len(result["candidates"]), 1)
        row = result["candidates"][0]
        self.assertEqual(row["promotion_state"], PROMO.STATE_WATCH)
        self.assertEqual(row["criteria"]["REGIME"]["status"], "UNKNOWN")
        self.assertEqual(result["summary"]["watch_count"], 1)
        self.assertEqual(result["summary"]["focused_review_count"], 0)

    def test_material_blocker_fail_yields_blocked_over_watch(self):
        packet = universe_packet([universe_row(caution_any=True)])
        regime = unknown_regime_payload()
        evidence = {"KRW-BTC": market_evidence_packet()}
        leadership = leadership_output(
            leadership_observed_window(
                asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "0.05"}]
            )
        )
        result = PROMO.build_promotion_packet(
            packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        row = result["candidates"][0]
        self.assertEqual(row["promotion_state"], PROMO.STATE_BLOCKED)
        self.assertEqual(result["summary"]["blocked_count"], 1)

    def test_observation_pool_market_excluded_from_output(self):
        packet = universe_packet([
            universe_row(market="KRW-BTC", state=UNI.STATE_PAPER_ELIGIBLE),
            universe_row(market="KRW-XRP", state=UNI.STATE_OBSERVATION_POOL, canonical_asset_id=None),
        ])
        regime = unknown_regime_payload()
        evidence = {"KRW-BTC": market_evidence_packet()}
        leadership = leadership_output(
            leadership_observed_window(
                asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "0.05"}]
            )
        )
        result = PROMO.build_promotion_packet(
            packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        markets = {row["market"] for row in result["candidates"]}
        self.assertEqual(markets, {"KRW-BTC"})

    def test_kraken_presence_never_promotes(self):
        """P3-12's own invariant, carried forward: Kraken cross-exchange
        labeling must never affect P5-08's promotion outcome either.
        """
        packet, regime, evidence, leadership = self._full_watch_inputs()
        packet_kraken_true = copy.deepcopy(packet)
        packet_kraken_true["markets"][0]["kraken_cross_exchange_reference"] = True
        packet_kraken_false = copy.deepcopy(packet)
        packet_kraken_false["markets"][0]["kraken_cross_exchange_reference"] = False

        result_true = PROMO.build_promotion_packet(
            packet_kraken_true, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        result_false = PROMO.build_promotion_packet(
            packet_kraken_false, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        row_true = result_true["candidates"][0]
        row_false = result_false["candidates"][0]
        self.assertEqual(row_true["promotion_state"], row_false["promotion_state"])
        self.assertEqual(row_true["criteria"], row_false["criteria"])

    def test_authority_false_everywhere(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        result = PROMO.build_promotion_packet(
            packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        self.assertEqual(result["authority"], PROMO._ROW_AUTHORITY)
        for row in result["candidates"]:
            self.assertEqual(row["authority"], PROMO._ROW_AUTHORITY)
            self.assertTrue(all(v is False for v in row["authority"].values()))

    def test_determinism_same_input_twice_identical_output(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        first = PROMO.build_promotion_packet(
            copy.deepcopy(packet), copy.deepcopy(regime), copy.deepcopy(evidence), copy.deepcopy(leadership),
            evaluation_as_of=EVAL_AS_OF,
        )
        second = PROMO.build_promotion_packet(
            copy.deepcopy(packet), copy.deepcopy(regime), copy.deepcopy(evidence), copy.deepcopy(leadership),
            evaluation_as_of=EVAL_AS_OF,
        )
        self.assertEqual(PROMO.canonical_json(first), PROMO.canonical_json(second))
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])

    def test_invalid_evaluation_as_of_rejected(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.build_promotion_packet(
                packet, regime, evidence, leadership, evaluation_as_of="not-a-date"
            )

    def test_universe_packet_schema_mismatch_rejected(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        bad_packet = copy.deepcopy(packet)
        bad_packet["schema_version"] = "wrong/1"
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.build_promotion_packet(
                bad_packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
            )

    def test_invalid_regime_payload_rejected(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        bad_regime = copy.deepcopy(regime)
        del bad_regime["market"]
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.build_promotion_packet(
                packet, bad_regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
            )


class ContractTests(unittest.TestCase):
    def test_load_contract_pinned(self):
        contract = PROMO.load_contract()
        self.assertEqual(contract["contract_version"], "crypto_candidate_promotion_contract/1")
        self.assertTrue(all(v is False for v in contract["authority"].values()))


if __name__ == "__main__":
    unittest.main()
