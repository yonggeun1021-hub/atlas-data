#!/usr/bin/env python3
"""Focused unit tests for the Crypto PAPER descriptive normalization helper.

The two ``btc_risk/v1`` payloads under ``REAL_PRIOR``/``REAL_CURRENT`` are the
actual retained 2026-09-07 and 2026-09-08 finalized transform results supplied
in the task context.  Every other payload here is a small synthetic fixture
built to exercise one rule or one rejection.  Neither kind proves provider or
source qualification: raw-file hash validation and transform rederivation stay
mandatory at the producer integration boundary.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from regime import crypto_paper_descriptive_normalization as module
from regime import paper_regime_reference as common


ERROR = module.CryptoPaperDescriptiveNormalizationError

REAL_PRIOR = {
    "schema_version": 1,
    "transform_version": "btc_risk/v1",
    "market": "CRYPTO",
    "asset": "BTC",
    "quote_currency": "USD",
    "market_timezone": "UTC",
    "measurement": "btc_risk_features",
    "status": "AVAILABLE_UNCALIBRATED",
    "latest_finalized_day": "2026-09-07",
    "risk_point": {
        "as_of_date": "2026-09-07",
        "realized_volatility": {
            "lookback_returns": 30,
            "window_start": "2026-08-08",
            "window_end": "2026-09-07",
            "return_semantics": "simple_close_to_close",
            "estimator": "sqrt_mean_squared_simple_returns",
            "annualization_days": 365,
            "annualized_fraction": "0.491256556551",
        },
        "drawdown": {
            "lookback_closes": 90,
            "window_start": "2026-06-10",
            "window_end": "2026-09-07",
            "semantics": "close_peak_to_trough",
            "current_fraction": "-0.026893514822",
            "current_peak_day": "2026-09-03",
            "maximum_fraction": "-0.116956805759",
            "maximum_peak_day": "2026-06-15",
            "maximum_trough_day": "2026-06-30",
        },
        "stress_features": {
            "version": "btc_stress_features/v1",
            "calibration_status": "UNDEFINED_UNCALIBRATED",
            "thresholds_applied": False,
            "classification": "UNDEFINED",
            "feature_vector": {
                "realized_vol_30d_annualized_fraction": "0.491256556551",
                "current_drawdown_90d_fraction": "-0.026893514822",
                "maximum_drawdown_90d_fraction": "-0.116956805759",
            },
        },
    },
    "current_candle": {
        "date": "2026-09-08",
        "excluded": True,
        "reason": "source_documents_not_yet_committed_timeframe",
    },
    "lineage": {
        "pit_status": "qualified_direct_capture",
        "vintage_date": "2026-09-08",
        "available_at": "2026-09-08T00:42:02Z",
        "source_name": "kraken_spot_ohlc",
        "source_sha256": "b4887593873b7399e58c4aaf537a279a380bc7642ed2c0e54b8a281c03583c3c",
        "capture_version": "btc-price-capture/v1",
        "source_transform_version": "btc_trend/v1",
        "missing_data_policy": "unknown_fail_closed_no_fill",
    },
    "stress_threshold_authorized": False,
    "stress_classification_authorized": False,
    "regime_score_authorized": False,
    "production_wiring_authorized": False,
    "trading_action_authorized": False,
}

REAL_CURRENT = {
    "schema_version": 1,
    "transform_version": "btc_risk/v1",
    "market": "CRYPTO",
    "asset": "BTC",
    "quote_currency": "USD",
    "market_timezone": "UTC",
    "measurement": "btc_risk_features",
    "status": "AVAILABLE_UNCALIBRATED",
    "latest_finalized_day": "2026-09-08",
    "risk_point": {
        "as_of_date": "2026-09-08",
        "realized_volatility": {
            "lookback_returns": 30,
            "window_start": "2026-08-09",
            "window_end": "2026-09-08",
            "return_semantics": "simple_close_to_close",
            "estimator": "sqrt_mean_squared_simple_returns",
            "annualization_days": 365,
            "annualized_fraction": "0.492063564002",
        },
        "drawdown": {
            "lookback_closes": 90,
            "window_start": "2026-06-11",
            "window_end": "2026-09-08",
            "semantics": "close_peak_to_trough",
            "current_fraction": "-0.034776521019",
            "current_peak_day": "2026-09-03",
            "maximum_fraction": "-0.116956805759",
            "maximum_peak_day": "2026-06-15",
            "maximum_trough_day": "2026-06-30",
        },
        "stress_features": {
            "version": "btc_stress_features/v1",
            "calibration_status": "UNDEFINED_UNCALIBRATED",
            "thresholds_applied": False,
            "classification": "UNDEFINED",
            "feature_vector": {
                "realized_vol_30d_annualized_fraction": "0.492063564002",
                "current_drawdown_90d_fraction": "-0.034776521019",
                "maximum_drawdown_90d_fraction": "-0.116956805759",
            },
        },
    },
    "current_candle": {
        "date": "2026-09-09",
        "excluded": True,
        "reason": "source_documents_not_yet_committed_timeframe",
    },
    "lineage": {
        "pit_status": "qualified_direct_capture",
        "vintage_date": "2026-09-09",
        "available_at": "2026-09-09T00:41:37Z",
        "source_name": "kraken_spot_ohlc",
        "source_sha256": "99c6531f2795b49845ce18025497e010ab6854b8f7450d72041191ff1c745eec",
        "capture_version": "btc-price-capture/v1",
        "source_transform_version": "btc_trend/v1",
        "missing_data_policy": "unknown_fail_closed_no_fill",
    },
    "stress_threshold_authorized": False,
    "stress_classification_authorized": False,
    "regime_score_authorized": False,
    "production_wiring_authorized": False,
    "trading_action_authorized": False,
}

BASE = {
    "trend_category": "ABOVE_200DMA",
    "breadth_advance_fraction": "0.50",
    "stablecoin_daily_net_issuance": "0",
    "stablecoin_weekly_net_issuance": "0",
    "leadership_code": "BTC_LEADERSHIP",
    "decision_at": "2026-09-09T02:00:00Z",
    "decision_date": "2026-09-09",
    "price_as_of_date": "2026-09-08",
    "current_reference_mode": module.CURRENT_REFERENCE_MODE,
}

MISSING = object()


def sha(payload: object) -> str:
    """The expected canonical payload hash under the existing convention."""
    return common.payload_sha256(payload)


def synthetic_risk(
    *,
    as_of_date: str,
    volatility: str,
    current_drawdown: str,
    maximum_drawdown: str = "-0.30",
    vintage_date: str | None = None,
    available_at: str | None = None,
    candle_date: str | None = None,
    lookback_returns: int = 30,
    source_sha256: str = "a" * 64,
) -> dict:
    """A minimal well-formed ``btc_risk/v1`` payload for one finalized day."""
    payload = copy.deepcopy(REAL_CURRENT)
    payload["latest_finalized_day"] = as_of_date
    point = payload["risk_point"]
    point["as_of_date"] = as_of_date
    point["realized_volatility"].update({
        "window_start": "2026-01-01",
        "window_end": as_of_date,
        "annualized_fraction": volatility,
        "lookback_returns": lookback_returns,
    })
    point["drawdown"].update({
        "window_start": "2026-01-01",
        "window_end": as_of_date,
        "current_fraction": current_drawdown,
        "maximum_fraction": maximum_drawdown,
    })
    # The in-progress candle is the day after the finalized day, matching the
    # real capture shape; a fixture must not fabricate a far-future candle.
    payload["current_candle"]["date"] = candle_date or (
        dt.date.fromisoformat(as_of_date) + dt.timedelta(days=1)
    ).isoformat()
    payload["lineage"].update({
        "vintage_date": vintage_date or as_of_date,
        "available_at": available_at or f"{vintage_date or as_of_date}T00:30:00Z",
        "source_sha256": source_sha256,
    })
    return payload


def call(**overrides):
    """Invoke the helper, hashing whichever payloads are actually passed in.

    The expected hashes default to the real hash of the supplied dictionary so
    that type/date/comparability rules can be tested in isolation; a test that
    targets the hash gate overrides them explicitly.
    """
    current = overrides.pop("current_risk_transform", MISSING)
    prior = overrides.pop("prior_risk_transform", MISSING)
    current = copy.deepcopy(REAL_CURRENT) if current is MISSING else current
    prior = copy.deepcopy(REAL_PRIOR) if prior is MISSING else prior
    kwargs = dict(
        BASE,
        current_risk_transform=current,
        prior_risk_transform=prior,
        current_risk_payload_sha256=sha(current) if isinstance(current, dict) else "",
        prior_risk_payload_sha256=sha(prior) if isinstance(prior, dict) else "",
    )
    kwargs.update(overrides)
    return module.normalize_crypto_measurements(**kwargs)


def directions(result: dict) -> dict:
    return {row["axis"]: row["direction"] for row in result["axes"]}


def synthetic_pair(current_volatility: str, current_drawdown: str, **overrides):
    """A comparable prior/current pair around a fixed prior day."""
    prior = synthetic_risk(
        as_of_date="2026-09-07", volatility="0.40", current_drawdown="-0.05",
        vintage_date="2026-09-08",
    )
    current = synthetic_risk(
        as_of_date="2026-09-08", volatility=current_volatility,
        current_drawdown=current_drawdown, vintage_date="2026-09-09",
    )
    return call(current_risk_transform=current, prior_risk_transform=prior, **overrides)


class TrendMapping(unittest.TestCase):
    def test_source_native_categories_map_directly(self):
        for category, expected in [
            ("ABOVE_200DMA", "POSITIVE"),
            ("AT_200DMA", "NEUTRAL"),
            ("BELOW_200DMA", "NEGATIVE"),
        ]:
            with self.subTest(category=category):
                self.assertEqual(directions(call(trend_category=category))["TREND"], expected)

    def test_unknown_category_is_unavailable_not_neutral(self):
        for category in ("NEAR_200DMA", "", "above_200dma", None):
            with self.subTest(category=category):
                with self.assertRaises(ERROR) as raised:
                    call(trend_category=category)
                self.assertIn("TREND_CATEGORY_UNAVAILABLE", str(raised.exception))


class BreadthBoundaries(unittest.TestCase):
    def test_provisional_thresholds_at_and_around_the_edges(self):
        for value, expected in [
            ("0.5500", "POSITIVE"),
            ("0.5499", "NEUTRAL"),
            ("0.50", "NEUTRAL"),
            ("0.4501", "NEUTRAL"),
            ("0.4500", "NEGATIVE"),
            ("0", "NEGATIVE"),
            ("1", "POSITIVE"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(
                    directions(call(breadth_advance_fraction=value))["BREADTH"], expected
                )

    def test_out_of_range_and_non_numeric_fractions_are_rejected(self):
        for value in ("1.01", "-0.01", "abc", None, True, [0.5]):
            with self.subTest(value=value):
                with self.assertRaises(ERROR):
                    call(breadth_advance_fraction=value)


class LiquiditySignPair(unittest.TestCase):
    def test_daily_weekly_issuance_sign_pair(self):
        for daily, weekly, expected in [
            ("1200", "9000", "POSITIVE"),
            ("-1200", "-9000", "NEGATIVE"),
            ("1200", "-9000", "NEUTRAL"),
            ("-1200", "9000", "NEUTRAL"),
            ("0", "9000", "NEUTRAL"),
            ("0", "0", "NEUTRAL"),
        ]:
            with self.subTest(daily=daily, weekly=weekly):
                result = call(
                    stablecoin_daily_net_issuance=daily,
                    stablecoin_weekly_net_issuance=weekly,
                )
                self.assertEqual(directions(result)["LIQUIDITY"], expected)

    def test_summary_names_the_issuance_proxy_not_buying_power(self):
        row = next(r for r in call()["axes"] if r["axis"] == "LIQUIDITY")
        self.assertIn("발행량 대리지표", row["summary_ko"])

    def test_non_numeric_issuance_is_rejected(self):
        for value in (None, True, "", {}):
            with self.subTest(value=value):
                with self.assertRaises(ERROR):
                    call(stablecoin_daily_net_issuance=value)


class LeadershipMapping(unittest.TestCase):
    def test_only_broad_alt_is_positive(self):
        for code, expected in [
            ("BROAD_ALT_LEADERSHIP", "POSITIVE"),
            ("BTC_LEADERSHIP", "NEUTRAL"),
            ("ETH_LEADERSHIP", "NEUTRAL"),
            ("MIXED_WINDOW_LEADERSHIP", "NEUTRAL"),
            ("NARROW_ALT_LEADERSHIP", "NEUTRAL"),
        ]:
            with self.subTest(code=code):
                self.assertEqual(directions(call(leadership_code=code))["LEADERSHIP"], expected)

    def test_unknown_code_is_unavailable_not_neutral(self):
        for code in ("SOL_LEADERSHIP", "", None):
            with self.subTest(code=code):
                with self.assertRaises(ERROR) as raised:
                    call(leadership_code=code)
                self.assertIn("LEADERSHIP_CATEGORY_UNAVAILABLE", str(raised.exception))


class MalformedCategoryInputs(unittest.TestCase):
    """Non-string categories must fail closed with the typed module exception.

    ``assertRaises(ERROR)`` is the whole point here: an unhashable list or dict
    reaching the mapping lookup would surface as ``TypeError``, which is not a
    subclass of the module error and would leave the producer without a value
    it can map to UNKNOWN.
    """

    MALFORMED = (["ABOVE_200DMA"], {"category": "ABOVE_200DMA"}, ("BTC_LEADERSHIP",), {"a"}, 1, 0, 1.5)

    def test_non_string_trend_category_raises_the_typed_error(self):
        for value in self.MALFORMED:
            with self.subTest(value=value):
                with self.assertRaises(ERROR) as raised:
                    call(trend_category=value)
                self.assertIn("TREND_CATEGORY_UNAVAILABLE", str(raised.exception))

    def test_non_string_leadership_code_raises_the_typed_error(self):
        for value in self.MALFORMED:
            with self.subTest(value=value):
                with self.assertRaises(ERROR) as raised:
                    call(leadership_code=value)
                self.assertIn("LEADERSHIP_CATEGORY_UNAVAILABLE", str(raised.exception))

    def test_bool_is_not_a_category(self):
        for key, code in [
            ("trend_category", "TREND_CATEGORY_UNAVAILABLE"),
            ("leadership_code", "LEADERSHIP_CATEGORY_UNAVAILABLE"),
        ]:
            with self.subTest(key=key):
                with self.assertRaises(ERROR) as raised:
                    call(**{key: True})
                self.assertIn(code, str(raised.exception))


class RiskDirectionQuadrants(unittest.TestCase):
    """Prior is volatility 0.40 and drawdown -0.05 in every case below."""

    def test_all_nine_sign_and_equality_quadrants(self):
        for volatility, drawdown, expected in [
            ("0.30", "-0.02", "POSITIVE"),   # both improved
            ("0.30", "-0.05", "POSITIVE"),   # volatility improved, drawdown equal
            ("0.40", "-0.02", "POSITIVE"),   # volatility equal, drawdown improved
            ("0.40", "-0.05", "NEUTRAL"),    # both unchanged
            ("0.30", "-0.09", "NEUTRAL"),    # mixed
            ("0.50", "-0.02", "NEUTRAL"),    # mixed
            ("0.50", "-0.09", "NEGATIVE"),   # both deteriorated
            ("0.50", "-0.05", "NEGATIVE"),   # volatility deteriorated, drawdown equal
            ("0.40", "-0.09", "NEGATIVE"),   # volatility equal, drawdown deteriorated
        ]:
            with self.subTest(volatility=volatility, drawdown=drawdown):
                result = synthetic_pair(volatility, drawdown)
                self.assertEqual(directions(result)["RISK_VOL"], expected)

    def test_retained_actual_transform_pair_is_negative(self):
        """Both realized volatility and drawdown worsened from 09-07 to 09-08."""
        result = call()
        self.assertEqual(directions(result)["RISK_VOL"], "NEGATIVE")
        binding = result["risk_binding"]
        self.assertEqual(binding["volatility_change"], "DETERIORATED")
        self.assertEqual(binding["drawdown_change"], "DETERIORATED")
        self.assertEqual(binding["current"]["as_of_date"], "2026-09-08")
        self.assertEqual(binding["prior"]["as_of_date"], "2026-09-07")

    def test_binding_preserves_both_raw_source_hashes_and_payload_hashes(self):
        binding = call()["risk_binding"]
        self.assertEqual(
            binding["current"]["source_sha256"], REAL_CURRENT["lineage"]["source_sha256"]
        )
        self.assertEqual(
            binding["prior"]["source_sha256"], REAL_PRIOR["lineage"]["source_sha256"]
        )
        self.assertEqual(binding["current"]["payload_sha256"], sha(REAL_CURRENT))
        self.assertEqual(binding["prior"]["payload_sha256"], sha(REAL_PRIOR))
        self.assertEqual(binding["raw_source_qualification"], "REQUIRED_AT_PRODUCER_INTEGRATION")


class RiskPayloadIntegrity(unittest.TestCase):
    def test_declared_hash_must_match_the_payload(self):
        with self.assertRaises(ERROR) as raised:
            call(current_risk_payload_sha256="0" * 64)
        self.assertIn("RISK_PAYLOAD_SHA_MISMATCH", str(raised.exception))

    def test_tampered_payload_under_the_original_hash_is_rejected(self):
        tampered = copy.deepcopy(REAL_CURRENT)
        tampered["risk_point"]["realized_volatility"]["annualized_fraction"] = "0.10"
        with self.assertRaises(ERROR) as raised:
            call(
                current_risk_transform=tampered,
                current_risk_payload_sha256=sha(REAL_CURRENT),
            )
        self.assertIn("RISK_PAYLOAD_SHA_MISMATCH", str(raised.exception))

    def test_malformed_hash_and_missing_transform_are_rejected(self):
        with self.assertRaises(ERROR):
            call(prior_risk_payload_sha256="not-a-hash")
        with self.assertRaises(ERROR):
            call(prior_risk_transform=None)

    def test_identity_and_refusal_flags_are_enforced(self):
        for key, value in [
            ("transform_version", "btc_risk/v2"),
            ("asset", "ETH"),
            ("quote_currency", "KRW"),
            ("market_timezone", "Asia/Seoul"),
            ("status", "AVAILABLE_CALIBRATED"),
            ("schema_version", True),
            ("trading_action_authorized", True),
            ("regime_score_authorized", True),
        ]:
            with self.subTest(key=key):
                payload = copy.deepcopy(REAL_CURRENT)
                payload[key] = value
                with self.assertRaises(ERROR):
                    call(current_risk_transform=payload)

    def test_stress_block_must_stay_uncalibrated(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["risk_point"]["stress_features"]["classification"] = "HIGH"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_STRESS_NOT_UNCALIBRATED", str(raised.exception))

    def test_incomplete_payload_blocks(self):
        for path in (("risk_point",), ("lineage",), ("current_candle",)):
            with self.subTest(path=path):
                payload = copy.deepcopy(REAL_CURRENT)
                del payload[path[0]]
                with self.assertRaises(ERROR):
                    call(current_risk_transform=payload)

    def test_missing_lineage_field_blocks(self):
        payload = copy.deepcopy(REAL_CURRENT)
        del payload["lineage"]["capture_version"]
        with self.assertRaises(ERROR):
            call(current_risk_transform=payload)


class RiskValueGates(unittest.TestCase):
    def test_negative_volatility_is_rejected(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["risk_point"]["realized_volatility"]["annualized_fraction"] = "-0.01"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_VOLATILITY_NEGATIVE", str(raised.exception))

    def test_out_of_range_drawdown_is_rejected(self):
        for value in ("0.01", "-1.5"):
            with self.subTest(value=value):
                payload = copy.deepcopy(REAL_CURRENT)
                payload["risk_point"]["drawdown"]["current_fraction"] = value
                with self.assertRaises(ERROR):
                    call(current_risk_transform=payload)

    def test_maximum_drawdown_must_not_be_shallower_than_current(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["risk_point"]["drawdown"]["maximum_fraction"] = "-0.01"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_DRAWDOWN_INCOHERENT", str(raised.exception))

    def test_non_finite_and_bool_as_number_are_rejected(self):
        for value in ("NaN", "Infinity", True):
            with self.subTest(value=value):
                payload = copy.deepcopy(REAL_CURRENT)
                payload["risk_point"]["realized_volatility"]["annualized_fraction"] = value
                with self.assertRaises(ERROR):
                    call(current_risk_transform=payload)

    def test_included_current_candle_is_rejected(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["current_candle"]["excluded"] = False
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_CANDLE_NOT_EXCLUDED", str(raised.exception))

    def test_candle_must_follow_the_finalized_day(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["current_candle"]["date"] = "2026-09-08"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_CANDLE_DATE_INVALID", str(raised.exception))

    def test_window_end_must_equal_the_finalized_day(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["risk_point"]["realized_volatility"]["window_end"] = "2026-09-07"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_VOLATILITY_WINDOW_INVALID", str(raised.exception))

    def test_finalized_day_and_risk_point_date_must_agree(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["latest_finalized_day"] = "2026-09-07"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_FINALIZED_DAY_MISMATCH", str(raised.exception))

    def test_malformed_dates_are_rejected(self):
        for value in ("2026-9-8", "2026-13-01", "20260908", ""):
            with self.subTest(value=value):
                payload = copy.deepcopy(REAL_CURRENT)
                payload["risk_point"]["as_of_date"] = value
                with self.assertRaises(ERROR):
                    call(current_risk_transform=payload)


class RiskComparability(unittest.TestCase):
    def test_non_consecutive_finalized_days_are_rejected(self):
        prior = synthetic_risk(
            as_of_date="2026-09-05", volatility="0.40", current_drawdown="-0.05",
            vintage_date="2026-09-06",
        )
        current = synthetic_risk(
            as_of_date="2026-09-08", volatility="0.41", current_drawdown="-0.06",
            vintage_date="2026-09-09",
        )
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=current, prior_risk_transform=prior)
        self.assertIn("RISK_DATES_NOT_CONSECUTIVE", str(raised.exception))

    def test_reversed_pair_is_rejected(self):
        # Decided late enough that both payloads are available, so the failure
        # is the reversed ordering itself and not the availability bound.
        with self.assertRaises(ERROR) as raised:
            call(
                current_risk_transform=copy.deepcopy(REAL_PRIOR),
                prior_risk_transform=copy.deepcopy(REAL_CURRENT),
                price_as_of_date="2026-09-07",
                decision_date="2026-09-10",
                decision_at="2026-09-10T02:00:00Z",
            )
        self.assertIn("RISK_DATES_NOT_CONSECUTIVE", str(raised.exception))

    def test_changed_lookback_makes_the_pair_incomparable(self):
        prior = synthetic_risk(
            as_of_date="2026-09-07", volatility="0.40", current_drawdown="-0.05",
            vintage_date="2026-09-08", lookback_returns=14,
        )
        current = synthetic_risk(
            as_of_date="2026-09-08", volatility="0.30", current_drawdown="-0.02",
            vintage_date="2026-09-09",
        )
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=current, prior_risk_transform=prior)
        self.assertIn("RISK_VOLATILITY_NOT_COMPARABLE", str(raised.exception))

    def test_changed_drawdown_semantics_makes_the_pair_incomparable(self):
        prior = copy.deepcopy(REAL_PRIOR)
        prior["risk_point"]["drawdown"]["semantics"] = "intraday_peak_to_trough"
        with self.assertRaises(ERROR) as raised:
            call(prior_risk_transform=prior)
        self.assertIn("RISK_DRAWDOWN_NOT_COMPARABLE", str(raised.exception))

    def test_changed_source_identity_makes_the_pair_incomparable(self):
        for key, value in [
            ("source_name", "other_exchange_ohlc"),
            ("capture_version", "btc-price-capture/v2"),
            ("missing_data_policy", "forward_fill"),
            ("pit_status", "reconstructed"),
        ]:
            with self.subTest(key=key):
                prior = copy.deepcopy(REAL_PRIOR)
                prior["lineage"][key] = value
                with self.assertRaises(ERROR) as raised:
                    call(prior_risk_transform=prior)
                self.assertIn("RISK_SOURCE_IDENTITY_NOT_COMPARABLE", str(raised.exception))

    def test_differing_raw_source_hashes_are_expected_and_accepted(self):
        """Two different capture days legitimately have different raw hashes."""
        self.assertNotEqual(
            REAL_CURRENT["lineage"]["source_sha256"],
            REAL_PRIOR["lineage"]["source_sha256"],
        )
        self.assertEqual(directions(call())["RISK_VOL"], "NEGATIVE")


class AvailabilityAndDecisionContext(unittest.TestCase):
    def test_input_available_after_decision_time_is_rejected(self):
        with self.assertRaises(ERROR) as raised:
            call(decision_at="2026-09-09T00:41:36Z")
        self.assertIn("RISK_INPUT_NOT_YET_AVAILABLE", str(raised.exception))

    def test_availability_exactly_at_decision_time_is_accepted(self):
        self.assertEqual(
            directions(call(decision_at="2026-09-09T00:41:37Z"))["RISK_VOL"], "NEGATIVE"
        )

    def test_prior_may_not_become_available_after_current(self):
        prior = copy.deepcopy(REAL_PRIOR)
        prior["lineage"]["available_at"] = "2026-09-09T01:00:00Z"
        with self.assertRaises(ERROR) as raised:
            call(prior_risk_transform=prior)
        self.assertIn("RISK_AVAILABILITY_ORDER_INVALID", str(raised.exception))

    def test_vintage_before_finalized_day_is_rejected(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["lineage"]["vintage_date"] = "2026-09-07"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_VINTAGE_BEFORE_FINALIZED_DAY", str(raised.exception))

    def test_future_capture_vintage_is_rejected(self):
        """A vintage dated after the decision day could not have existed yet."""
        for side in ("current_risk_transform", "prior_risk_transform"):
            with self.subTest(side=side):
                payload = copy.deepcopy(
                    REAL_CURRENT if side == "current_risk_transform" else REAL_PRIOR
                )
                payload["lineage"]["vintage_date"] = "2026-09-10"
                with self.assertRaises(ERROR) as raised:
                    call(**{side: payload})
                self.assertIn("RISK_VINTAGE_AFTER_DECISION_DATE", str(raised.exception))

    def test_vintage_on_the_decision_date_is_accepted(self):
        self.assertEqual(REAL_CURRENT["lineage"]["vintage_date"], BASE["decision_date"])
        self.assertEqual(directions(call())["RISK_VOL"], "NEGATIVE")

    def test_future_excluded_candle_date_is_rejected(self):
        for side in ("current_risk_transform", "prior_risk_transform"):
            with self.subTest(side=side):
                payload = copy.deepcopy(
                    REAL_CURRENT if side == "current_risk_transform" else REAL_PRIOR
                )
                payload["current_candle"]["date"] = "2026-12-31"
                with self.assertRaises(ERROR) as raised:
                    call(**{side: payload})
                self.assertIn("RISK_CANDLE_AFTER_DECISION_DATE", str(raised.exception))

    def test_candle_date_on_the_decision_date_is_accepted(self):
        self.assertEqual(REAL_CURRENT["current_candle"]["date"], BASE["decision_date"])
        self.assertEqual(directions(call())["RISK_VOL"], "NEGATIVE")

    def test_availability_before_the_declared_vintage_is_rejected(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["lineage"]["available_at"] = "2026-09-08T23:59:59Z"
        with self.assertRaises(ERROR) as raised:
            call(current_risk_transform=payload)
        self.assertIn("RISK_AVAILABLE_BEFORE_VINTAGE", str(raised.exception))

    def test_availability_at_vintage_midnight_is_accepted(self):
        payload = copy.deepcopy(REAL_CURRENT)
        payload["lineage"]["available_at"] = "2026-09-09T00:00:00Z"
        self.assertEqual(
            directions(call(current_risk_transform=payload))["RISK_VOL"], "NEGATIVE"
        )

    def test_current_reference_mode_must_be_declared(self):
        for mode in ("PIT_REPLAY", "", None):
            with self.subTest(mode=mode):
                with self.assertRaises(ERROR) as raised:
                    call(current_reference_mode=mode)
                self.assertIn("CURRENT_REFERENCE_MODE_INVALID", str(raised.exception))

    def test_decision_timestamp_must_be_utc_and_match_the_decision_date(self):
        for decision_at in (
            "2026-09-09T02:00:00+00:00",
            "2026-09-09 02:00:00Z",
            "2026-09-09T25:00:00Z",
            "2026-09-10T02:00:00Z",
        ):
            with self.subTest(decision_at=decision_at):
                with self.assertRaises(ERROR):
                    call(decision_at=decision_at)

    def test_price_date_must_match_the_current_finalized_day(self):
        with self.assertRaises(ERROR) as raised:
            call(price_as_of_date="2026-09-07")
        self.assertIn("RISK_PRICE_DATE_MISMATCH", str(raised.exception))

    def test_price_date_must_precede_the_decision_date(self):
        with self.assertRaises(ERROR) as raised:
            call(price_as_of_date="2026-09-09", decision_date="2026-09-09")
        self.assertIn("PRICE_DATE_NOT_BEFORE_DECISION_DATE", str(raised.exception))

    def test_missing_measurement_argument_is_never_defaulted(self):
        kwargs = dict(
            BASE,
            current_risk_transform=copy.deepcopy(REAL_CURRENT),
            prior_risk_transform=copy.deepcopy(REAL_PRIOR),
            current_risk_payload_sha256=sha(REAL_CURRENT),
            prior_risk_payload_sha256=sha(REAL_PRIOR),
        )
        for key in (
            "trend_category",
            "breadth_advance_fraction",
            "stablecoin_daily_net_issuance",
            "stablecoin_weekly_net_issuance",
            "leadership_code",
        ):
            with self.subTest(key=key):
                incomplete = {k: v for k, v in kwargs.items() if k != key}
                with self.assertRaises(TypeError):
                    module.normalize_crypto_measurements(**incomplete)


class ResultShapeAndCaveats(unittest.TestCase):
    def test_axes_are_complete_and_canonically_ordered(self):
        result = call()
        self.assertEqual([row["axis"] for row in result["axes"]], module.AXES)
        self.assertEqual(module.AXES, common.AXES)

    def test_scores_come_from_the_shared_axis_helper(self):
        result = call(
            trend_category="ABOVE_200DMA",
            breadth_advance_fraction="0.60",
            leadership_code="BROAD_ALT_LEADERSHIP",
            stablecoin_daily_net_issuance="10",
            stablecoin_weekly_net_issuance="20",
        )
        scores = {row["axis"]: row["score"] for row in result["axes"]}
        self.assertEqual(
            scores,
            {"TREND": 1, "BREADTH": 1, "RISK_VOL": -1, "LIQUIDITY": 1, "LEADERSHIP": 1},
        )

    def test_helper_never_classifies_or_promotes_runtime(self):
        result = call()
        self.assertEqual(result["classification_status"], "NOT_CLASSIFIED_BY_THIS_HELPER")
        self.assertEqual(result["runtime_regime"], "UNKNOWN")
        for key in ("paper_reference", "candidate_regime", "confidence", "score"):
            self.assertNotIn(key, result)
        self.assertTrue(result["authority"]["read_only_reference"])
        for key, value in result["authority"].items():
            if key != "read_only_reference":
                self.assertIs(value, False, key)

    def test_mandatory_caveats_are_always_returned(self):
        for kwargs in (
            {},
            {"trend_category": "BELOW_200DMA", "leadership_code": "BROAD_ALT_LEADERSHIP"},
            {"breadth_advance_fraction": "0.99"},
        ):
            with self.subTest(kwargs=kwargs):
                caveats = call(**kwargs)["caveats"]
                self.assertEqual(caveats["absolute_stress_status"], "NOT_ASSESSED")
                self.assertEqual(
                    caveats["stress_calibration_status"], "UNDEFINED_UNCALIBRATED"
                )
                self.assertEqual(
                    caveats["breadth_threshold_status"], "PROVISIONAL_CRYPTO_PAPER_POLICY"
                )
                self.assertEqual(
                    caveats["liquidity_input_semantics"],
                    "NAMED_STABLECOIN_ISSUANCE_PROXY_NOT_EXCHANGE_BUYING_POWER",
                )
                self.assertEqual(
                    caveats["point_in_time_status"],
                    "CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY",
                )
                self.assertEqual(
                    caveats["confidence_semantics"],
                    "CONFIDENCE_MATCHING_AXIS_FRACTION_NOT_PROBABILITY",
                )
                self.assertEqual(
                    caveats["source_qualification_status"],
                    "PAYLOAD_INTEGRITY_ONLY_RAW_SOURCE_QUALIFICATION_REQUIRED_AT_PRODUCER_INTEGRATION",
                )

    def test_caveats_are_a_copy_the_caller_cannot_mutate_globally(self):
        result = call()
        result["caveats"]["absolute_stress_status"] = "TAMPERED"
        result["authority"]["trading_authorized"] = True
        self.assertEqual(module.MANDATORY_CAVEATS["absolute_stress_status"], "NOT_ASSESSED")
        self.assertIs(module.AUTHORITY["trading_authorized"], False)
        self.assertEqual(call()["caveats"]["absolute_stress_status"], "NOT_ASSESSED")

    def test_result_is_deterministic(self):
        self.assertEqual(common.payload_sha256(call()), common.payload_sha256(call()))


if __name__ == "__main__":
    unittest.main()
