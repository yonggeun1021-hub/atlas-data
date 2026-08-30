#!/usr/bin/env python3
"""P5-08 Crypto Candidate Promotion Rule regression."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "crypto_candidate_promotion.py"
SPEC = importlib.util.spec_from_file_location("crypto_candidate_promotion", MODULE_PATH)
PROMO = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(PROMO)

UNI = PROMO.UPBIT_UNIVERSE
REGIME_OC = PROMO.REGIME_OUTPUT_CONTRACT
MARKET_EV = PROMO.MARKET_EVIDENCE

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
        "observed_daily_candle_count": 120,
        "trailing_30d_krw_turnover": "10000000000",
        "kraken_cross_exchange_reference": kraken_cross_exchange_reference,
        "authority": dict(UNI._ROW_AUTHORITY),
    }


def universe_packet(rows: list) -> dict:
    policy = UNI.load_policy()
    taxonomy = UNI.load_taxonomy()
    packet = {
        "schema_version": UNI.OUTPUT_SCHEMA_VERSION,
        "snapshot_date": EVAL_AS_OF,
        "evaluation_as_of": EVAL_AS_OF,
        "available_at": "2026-08-28T00:40:00Z",
        "manifest_sha256": "a" * 64,
        "policy_version": policy["policy_version"],
        "policy_ratified": True,
        "taxonomy_version": taxonomy["policy_version"],
        "taxonomy_ratified": True,
        "duplicate_market_codes": {},
        "summary": {
            "market_count": len(rows),
            "observation_pool_count": sum(row["state"] == UNI.STATE_OBSERVATION_POOL for row in rows),
            "tradeable_universe_count": sum(row["state"] == UNI.STATE_TRADEABLE_UNIVERSE for row in rows),
            "paper_eligible_count": sum(row["state"] == UNI.STATE_PAPER_ELIGIBLE for row in rows),
            "blocked_count": sum(row["state"] == UNI.STATE_BLOCKED for row in rows),
        },
        "markets": rows,
        "authority": dict(UNI._ROW_AUTHORITY),
    }
    packet["payload_sha256"] = UNI.payload_sha256(packet)
    return packet


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


def valid_market_evidence_packet(market="KRW-BTC") -> dict:
    as_of = dt.datetime(2026, 8, 28, 1, 0, 0, tzinfo=dt.timezone.utc)
    captured_at = dt.datetime(2026, 8, 28, 1, 5, 0, tzinfo=dt.timezone.utc)
    raw_candle = {
        "candle_date_time_utc": "2026-08-27T00:00:00",
        "opening_price": 1000,
        "high_price": 1010,
        "low_price": 990,
        "trade_price": 1005,
        "candle_acc_trade_price": 123456,
        "candle_acc_trade_volume": 12.3,
    }
    candles = {timeframe: [copy.deepcopy(raw_candle)] for timeframe in MARKET_EV.finalization.TIMEFRAMES}
    timestamp_ms = int(as_of.timestamp() * 1000)
    trades = [{
        "market": market,
        "trade_price": 1000,
        "trade_volume": 1,
        "timestamp": timestamp_ms,
        "ask_bid": "BID",
    }]
    orderbook = {
        "market": market,
        "timestamp": timestamp_ms,
        "orderbook_units": [{"bid_price": 999, "bid_size": 10000, "ask_price": 1001, "ask_size": 10000}],
    }
    return MARKET_EV.build_market_evidence_packet(
        market,
        candles_by_timeframe=candles,
        trades=trades,
        orderbook_row=orderbook,
        as_of=as_of,
        captured_at=captured_at,
        policy=MARKET_EV.load_policy(),
    )


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
    def test_unknown_when_directions_agree_without_ratified_rule(self):
        packet = market_evidence_packet(daily_prices=("100", "110"), four_hour_prices=("100", "105"))
        result = PROMO.evaluate_trend("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["daily_direction"], "UP")
        self.assertEqual(result["four_hour_direction"], "UP")

    def test_unknown_when_one_side_flat_without_ratified_rule(self):
        packet = market_evidence_packet(daily_prices=("100", "100"), four_hour_prices=("100", "105"))
        result = PROMO.evaluate_trend("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")

    def test_unknown_when_directions_conflict_without_ratified_rule(self):
        packet = market_evidence_packet(daily_prices=("100", "110"), four_hour_prices=("100", "90"))
        result = PROMO.evaluate_trend("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "NO_RATIFIED_CANDIDATE_TREND_RULE")

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
    def test_positive_btc_leg_stays_unknown_without_peer_leg(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "ETH", "relative_strength_vs_btc": "0.050000000000"}]
        )
        result = PROMO.evaluate_relative_strength("ETH", leadership_output(window))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "PEER_RELATIVE_STRENGTH_UNRATIFIED")

    def test_fail_when_not_positive(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "ETH", "relative_strength_vs_btc": "-0.010000000000"}]
        )
        result = PROMO.evaluate_relative_strength("ETH", leadership_output(window))
        self.assertEqual(result["status"], "FAIL")

    def test_fail_when_exactly_zero(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "ETH", "relative_strength_vs_btc": "0.000000000000"}]
        )
        result = PROMO.evaluate_relative_strength("ETH", leadership_output(window))
        self.assertEqual(result["status"], "FAIL")

    def test_btc_reference_asset_stays_unknown_pending_dedicated_rule(self):
        result = PROMO.evaluate_relative_strength("BTC", leadership_output(leadership_observed_window()))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "BTC_SELF_REFERENCE_RULE_UNRATIFIED")

    def test_unknown_when_identity_missing(self):
        result = PROMO.evaluate_relative_strength(None, leadership_output(leadership_observed_window()))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "IDENTITY_UNRATIFIED")

    def test_unknown_when_leadership_output_missing(self):
        result = PROMO.evaluate_relative_strength("BTC", None)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "LEADERSHIP_OUTPUT_MISSING")

    def test_unknown_when_window_unknown(self):
        result = PROMO.evaluate_relative_strength("ETH", leadership_output(leadership_unknown_window()))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("LEADERSHIP_WINDOW_UNKNOWN", result["reason"])

    def test_unknown_when_asset_not_covered(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "BTC", "relative_strength_vs_btc": "0.00"}]
        )
        result = PROMO.evaluate_relative_strength("ETH", leadership_output(window))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "LEADERSHIP_ASSET_NOT_COVERED")

    def test_unknown_when_asset_window_partial(self):
        window = leadership_observed_window(
            asset_relative_strength=[{"canonical_asset_id": "ETH", "relative_strength_vs_btc": "0.05"}],
            partial_window_assets=[{"canonical_asset_id": "ETH"}],
        )
        result = PROMO.evaluate_relative_strength("ETH", leadership_output(window))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "LEADERSHIP_ASSET_WINDOW_INCOMPLETE")


class VolumeLiquidityCriterionTests(unittest.TestCase):
    def test_all_families_present_stays_unknown_without_ratified_thresholds(self):
        packet = market_evidence_packet()
        result = PROMO.evaluate_volume_liquidity("KRW-BTC", packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "VOLUME_LIQUIDITY_THRESHOLDS_UNRATIFIED")

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
    def test_no_caution_stays_unknown_without_security_outage_coverage(self):
        result = PROMO.evaluate_material_blocker(universe_row(caution_any=False))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "SECURITY_AND_NETWORK_OUTAGE_COVERAGE_MISSING")

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
    def setUp(self):
        policy = UNI.load_policy()
        taxonomy = UNI.load_taxonomy()
        registry = UNI.load_identity_registry()
        self.actual_policy = copy.deepcopy(policy)
        self.actual_taxonomy = copy.deepcopy(taxonomy)
        policy["approval_status"] = "RATIFIED"
        policy["effective_date"] = EVAL_AS_OF
        taxonomy["approval_status"] = "RATIFIED"
        taxonomy["effective_from"] = EVAL_AS_OF
        registry["approval_status"] = "RATIFIED"
        registry["effective_from"] = EVAL_AS_OF
        self.policy_patch = mock.patch.object(UNI, "load_policy", return_value=policy)
        self.taxonomy_patch = mock.patch.object(UNI, "load_taxonomy", return_value=taxonomy)
        self.registry_patch = mock.patch.object(UNI, "load_identity_registry", return_value=registry)
        self.policy_patch.start()
        self.taxonomy_patch.start()
        self.registry_patch.start()

    def tearDown(self):
        self.registry_patch.stop()
        self.taxonomy_patch.stop()
        self.policy_patch.stop()

    def _full_watch_inputs(self):
        packet = universe_packet([universe_row()])
        regime = unknown_regime_payload()
        evidence = {"KRW-BTC": valid_market_evidence_packet()}
        leadership = None
        return packet, regime, evidence, leadership

    def test_real_evaluation_always_lands_in_watch_today(self):
        """Even under a simulated future P3-12 ratification, unresolved
        canonical criteria cap a genuine derivation at WATCH.
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
        evidence = {}
        leadership = None
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
        evidence = {}
        leadership = None
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
        packet_kraken_true["payload_sha256"] = UNI.payload_sha256({
            key: value for key, value in packet_kraken_true.items() if key != "payload_sha256"
        })
        packet_kraken_false = copy.deepcopy(packet)
        packet_kraken_false["markets"][0]["kraken_cross_exchange_reference"] = False
        packet_kraken_false["payload_sha256"] = UNI.payload_sha256({
            key: value for key, value in packet_kraken_false.items() if key != "payload_sha256"
        })

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

    def test_previous_day_regime_payload_rejected_as_stale_for_evaluation_date(self):
        packet, _regime, evidence, leadership = self._full_watch_inputs()
        stale_regime = REGIME_OC.build_unknown_output("CRYPTO", "2026-08-27T23:59:59Z")
        with self.assertRaisesRegex(PROMO.CryptoCandidatePromotionError, "REGIME_PAYLOAD_DATE_MISMATCH"):
            PROMO.build_promotion_packet(
                packet, stale_regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
            )

    def test_unratified_universe_policy_cannot_be_bypassed_by_fabricated_in_scope_row(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        packet["policy_ratified"] = False
        packet["taxonomy_ratified"] = UNI._approval_effective(
            self.actual_taxonomy, EVAL_AS_OF, date_field="effective_from",
        )
        packet["payload_sha256"] = UNI.payload_sha256({
            key: value for key, value in packet.items() if key != "payload_sha256"
        })
        with (
            mock.patch.object(UNI, "load_policy", return_value=self.actual_policy),
            mock.patch.object(UNI, "load_taxonomy", return_value=self.actual_taxonomy),
            self.assertRaises(PROMO.CryptoCandidatePromotionError),
        ):
            PROMO.build_promotion_packet(
                packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
            )

    def test_universe_source_tamper_rejected_even_with_rehashed_outer_output(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        result = PROMO.build_promotion_packet(
            packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        tampered = copy.deepcopy(result)
        tampered["source_packets"]["universe"]["markets"][0]["state"] = UNI.STATE_OBSERVATION_POOL
        tampered["payload_sha256"] = PROMO.payload_sha256({
            key: value for key, value in tampered.items() if key != "payload_sha256"
        })
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.validate_output(tampered)

    def test_derived_candidate_tamper_rejected_even_with_rehashed_output(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        result = PROMO.build_promotion_packet(
            packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        tampered = copy.deepcopy(result)
        tampered["candidates"][0]["promotion_state"] = PROMO.STATE_FOCUSED_REVIEW
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = PROMO.payload_sha256(unsigned)
        with self.assertRaises(PROMO.CryptoCandidatePromotionError):
            PROMO.validate_output(tampered)

    def test_valid_output_revalidates_from_embedded_sources(self):
        packet, regime, evidence, leadership = self._full_watch_inputs()
        result = PROMO.build_promotion_packet(
            packet, regime, evidence, leadership, evaluation_as_of=EVAL_AS_OF
        )
        self.assertEqual(PROMO.validate_output(result), result)


class ContractTests(unittest.TestCase):
    def test_load_contract_pinned(self):
        contract = PROMO.load_contract()
        self.assertEqual(contract["contract_version"], "crypto_candidate_promotion_contract/2")
        self.assertTrue(all(v is False for v in contract["authority"].values()))


if __name__ == "__main__":
    unittest.main()
